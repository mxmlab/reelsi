# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GN: мастер набора не читает stdout AE + «Один на всё» по умолчанию.

1. _run_proc_master читает stdout дочернего процесса фоновым потоком через общую
   функцию _pump_stdout(p), предотвращая переполнение буфера OS-трубы и зависание
   AfterFX -noui в WriteFile при обильном выводе.
2. В templates/index.html режим по умолчанию — combined («Один на всё»).
3. Мастер-скрипт определяет $.global.REELSI_MASTER_LOG, закрывает лог перед каждым
   $.evalFile и сбрасывает REELSI_MASTER_LOG в null в finally.
4. При comps_global=True каждый таймлайн дописывает в REELSI_MASTER_LOG строку
   «таймлайн ok: <имя>», которую _tail_master_log выводит в журнал, освежая сторож.
   При comps_global=False вывод .jsx остаётся побайтово прежним.
"""
import gzip
import os
import sys
import threading
import urllib.parse
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.build import _write_master  # noqa: E402


def _fileurl(p):
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


def _log_text(rjob):
    parts = []
    for e in rjob["log"]:
        if isinstance(e, str):
            parts.append(e)
        else:
            t = e.get("t", "")
            v = e.get("v") or {}
            try:
                parts.append(t.format(**v))
            except Exception:
                parts.append(t)
    return "\n".join(parts)


def _reset_job(names):
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    render.items_init(render.RJOB, render.RLOCK, names)


@pytest.fixture()
def xml_fixture(tmp_path):
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"dummy")
    cam2.write_bytes(b"dummy")

    raw_xml_gz = os.path.join(HERE, "fixtures", "timeline_subs.xml.gz")
    with gzip.open(raw_xml_gz, "rb") as g:
        xml_content = g.read().decode("utf-8")

    xml_content = xml_content.replace(
        "file://localhost/C%3a/footage/cam1/CLIP-006.MP4", _fileurl(cam1)
    ).replace(
        "file://localhost/C%3a/footage/cam2/CLIP-006.MP4", _fileurl(cam2)
    )

    xml1_path = tmp_path / "01_C0233.xml"
    content1 = xml_content.replace("<name>CLIP-006</name>", "<name>C0233</name>")
    xml1_path.write_text(content1, encoding="utf-8")

    xml2_path = tmp_path / "02_C0234.xml"
    content2 = xml_content.replace("<name>CLIP-006</name>", "<name>C0234</name>")
    xml2_path.write_text(content2, encoding="utf-8")

    return {
        "xml1": str(xml1_path),
        "xml2": str(xml2_path),
        "dir": str(tmp_path),
    }


# ============================================================================
# Часть 1. Мастер набора читает stdout AE (тест на переполнение OS-буфера)
# ============================================================================

def test_master_stdout_pipe_does_not_deadlock(tmp_path):
    """Подставной AfterFX пишет >1 МБ в stdout, затем пишет evalFile ok в aelog.
    _run_proc_master обязан непрерывно выкачивать stdout фоновым потоком, не допуская
    дедлока по переполнению буфера OS-трубы, и успешно завершиться."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    jsx1 = os.path.abspath(os.path.join(outdir, "01_C0233.jsx"))
    with open(jsx1, "w", encoding="utf-8") as f:
        f.write("var dummy=1;\n")
    aelog = os.path.join(outdir, "reelsi_batch.aelog.txt")
    norm_jsx1 = jsx1.replace("\\", "/")

    # Подставной скрипт: пишет >1 МБ текста в stdout (буфер трубы Windows обычно 4–64 КБ),
    # после чего создает aelog с отметкой успеха и выходит с кодом 0.
    fake_py = tmp_path / "fake_afterfx.py"
    fake_py.write_text(f"""# -*- coding: utf-8 -*-
import sys

# Вывод > 1 МБ текста в stdout
line = "X" * 1023 + "\\n"
for _ in range(1100):
    sys.stdout.write(line)
sys.stdout.flush()

# Запись в aelog после обильного stdout
with open({repr(aelog)}, "w", encoding="utf-8") as f:
    f.write("REELSI-MASTER: начат\\n")
    f.write("evalFile ok: {norm_jsx1}\\n")
    f.write("comp ok: C0233\\n")
    f.write("REELSI-MASTER: готово\\n")

sys.exit(0)
""", encoding="utf-8")

    good = [("01_C0233", "C0233", jsx1, 100)]
    _reset_job(["01_C0233"])

    worker_result = {}

    def _run():
        try:
            rc = render._run_proc_master(
                sys.executable, str(fake_py),
                good=good, render_dir=render_dir, aelog_path=aelog,
            )
            worker_result["rc"] = rc
        except Exception as e:
            worker_result["err"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=30)

    # Если дедлок — поток останется жив через 30 секунд
    alive = t.is_alive()
    if alive:
        if render.RPROC:
            try:
                render._kill_proc(render.RPROC)
            except Exception:
                pass
        pytest.fail("Дедлок: _run_proc_master не вернулся за 30 секунд (буфер stdout переполнен)")

    assert worker_result.get("rc") == 0, f"Ошибка выполнения: {worker_result}"
    text = _log_text(render.RJOB)
    assert any("XXXXX" in ln for ln in text.splitlines()), "Строки из stdout не попали в RJOB['log']"


# ============================================================================
# Часть 2. Радио multimode по умолчанию — «Отдельные .jsx» + сторож таймлайнов
# ============================================================================

def test_multimode_radio_default_is_separate():
    """В templates/index.html радио multimode по умолчанию должно иметь value="separate"
    с атрибутом checked и классом on на его label (ручная сборка снова separate)."""
    index_html = os.path.join(ROOT, "templates", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        html = f.read()

    assert '<input type="radio" name="multimode" value="separate" checked' in html, (
        "Радио separate должно иметь checked"
    )
    assert '<label class="on"><input type="radio" name="multimode" value="separate"' in html, (
        "Label у separate должен иметь class=\"on\""
    )
    assert '<input type="radio" name="multimode" value="combined" checked' not in html, (
        "Радио combined не должно быть checked по умолчанию"
    )


def test_write_master_has_reelsi_master_log_and_closes_before_evalfile(tmp_path):
    """_write_master объявляет $.global.REELSI_MASTER_LOG, закрывает лог перед $.evalFile
    и сбрасывает переменную в null в finally."""
    jsx1 = str(tmp_path / "clip1.jsx")
    jsx2 = str(tmp_path / "clip2.jsx")
    master_path = str(tmp_path / "render_master.jsx")
    aep_path = str(tmp_path / "reelsi_batch.aep")
    render_dir = str(tmp_path / "exp")

    _write_master([jsx1, jsx2], master_path, aep_path, render_dir)

    with open(master_path, "r", encoding="utf-8-sig") as f:
        master_src = f.read()

    assert "$.global.REELSI_MASTER_LOG =" in master_src, "В мастере нет $.global.REELSI_MASTER_LOG"
    assert "$.global.REELSI_MASTER_LOG = null;" in master_src, (
        "В finally нет сброса $.global.REELSI_MASTER_LOG = null"
    )

    # Проверяем, что _log.close() вызывается ДО $.evalFile
    i_loop = master_src.index("for (var _fi=0; _fi<_files.length; _fi++)")
    i_close = master_src.index("_log.close();", i_loop)
    i_eval = master_src.index("$.evalFile(", i_loop)
    assert i_close < i_eval, "Лог мастера должен закрываться ДО $.evalFile"


def test_build_combined_comps_global_writes_timeline_ok(xml_fixture, tmp_path):
    """build_combined с comps_global=True добавляет запись «таймлайн ok:» в REELSI_MASTER_LOG
    для каждого таймлайна."""
    jobs = [
        {"xml_path": xml_fixture["xml1"], "roto": False},
        {"xml_path": xml_fixture["xml2"], "roto": False},
    ]
    out_jsx = str(tmp_path / "combined.jsx")
    xml2ae.build_combined(jobs, out_jsx, comps_global=True, emit=lambda *a, **k: None)

    with open(out_jsx, "r", encoding="utf-8-sig") as f:
        src = f.read()

    assert src.count("таймлайн ok: ") == 2, "Должно быть по одной записи «таймлайн ok:» на таймлайн"
    assert src.count("if($.global.REELSI_MASTER_LOG)") == 2
    assert "_LOG(\"запись в REELSI_MASTER_LOG: \"" in src


def test_build_combined_comps_global_false_no_timeline_ok(xml_fixture, tmp_path):
    """build_combined с comps_global=False НЕ содержит «таймлайн ok:» и REELSI_MASTER_LOG."""
    jobs = [
        {"xml_path": xml_fixture["xml1"], "roto": False},
        {"xml_path": xml_fixture["xml2"], "roto": False},
    ]
    out_jsx = str(tmp_path / "combined.jsx")
    xml2ae.build_combined(jobs, out_jsx, comps_global=False, emit=lambda *a, **k: None)

    with open(out_jsx, "r", encoding="utf-8-sig") as f:
        src = f.read()

    assert "таймлайн ok:" not in src
    assert "REELSI_MASTER_LOG" not in src


def test_tail_master_log_prints_timeline_ok():
    """_tail_master_log выводит строки «таймлайн ok:» с префиксом «  [aelog] »."""
    seen = set()
    remit_lines = []

    def fake_remit(msg, **kwargs):
        remit_lines.append(msg)

    orig_remit = render.remit
    render.remit = fake_remit
    try:
        import tempfile
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", delete=False) as tf:
            tf.write("REELSI-MASTER: начат\n")
            tf.write("таймлайн ok: C0233\n")
            tf.write("таймлайн ok: C0234\n")
            tf_path = tf.name

        try:
            render._tail_master_log(tf_path, seen, {})
            assert "  [aelog] таймлайн ok: C0233" in remit_lines
            assert "  [aelog] таймлайн ok: C0234" in remit_lines
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)
    finally:
        render.remit = orig_remit
