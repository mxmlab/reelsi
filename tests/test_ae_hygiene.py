# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание AE-Hygiene: признаки успеха безголового рендера больше не врут на втором прогоне.

ПОЧЕМУ этот тест существует:
Артефакты прошлого прогона остаются на диске, и все три признака «AE отработал»
(наличие .aelog.txt, наличие .aep, код возврата AfterFX) врют на втором и последующих
прогонах:
- старый .aelog.txt читается как «скрипт запустился» и печатает «собран:» за ролики,
  которые в этом прогоне ещё даже не строились;
- старый .aep считается «мастер сохранил проект», и aerender рендерит ПРОШЛЫЙ проект;
- открытая копия AfterFX «съедает» наш AfterFX -noui: код 0 за 0 секунд, скрипт уходит
  в неё, переписывает её проект и закрывает её.

Проверяется (без живого AE, всё на подменах): удаление старого журнала ДО запуска,
проверка «.aep обновлён этим прогоном» вместо «файл существует», стоп прогона при
открытом After Effects, сторож простоя, снимающий молчащий процесс по лимиту.

Запуск: python -m pytest reelsi/tests -q
"""
import gzip
import os
import sys
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402


def _fileurl(p):
    """file://localhost/… как в Premiere XML — для подмены путей камер фикстуры."""
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


def _log_text(rjob):
    """Весь лог джоба одним текстом: строки и структурированные {t}/{v}-записи remit."""
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


@pytest.fixture()
def clips(tmp_path):
    """Два XML-файла набора (01_C0233, 02_C0234) и фиктивные медиа камер."""
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"dummy")
    cam2.write_bytes(b"dummy")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g:
        xml_content = g.read().decode("utf-8")
    xml_content = xml_content.replace(
        "file://localhost/C%3a/footage/cam1/CLIP-006.MP4", _fileurl(cam1)
    ).replace(
        "file://localhost/C%3a/footage/cam2/CLIP-006.MP4", _fileurl(cam2)
    )
    xml1 = tmp_path / "01_C0233.xml"
    xml2 = tmp_path / "02_C0234.xml"
    xml1.write_text(xml_content.replace("<name>CLIP-006</name>", "<name>C0233</name>"),
                    encoding="utf-8")
    xml2.write_text(xml_content.replace("<name>CLIP-006</name>", "<name>C0234</name>"),
                    encoding="utf-8")
    return {"xml1": str(xml1), "xml2": str(xml2), "dir": str(tmp_path)}


def _install_popen(monkeypatch, launched, on_afterfx=None):
    """Подменить subprocess.Popen. Перехватываются только запуски AfterFX/aerender
    (имя exe содержит afterfx/aerender): on_afterfx(cmd) вызывается при запуске
    AfterFX (именно там «появляются» файлы реального AE), aerender фиксируется.
    Служебные процессы сборки (node, ffprobe и т.п.) уходят в настоящий Popen —
    иначе они засоряют список запусков. Подменённый процесс «уже вышел»: poll()=0,
    stdout пустой."""
    real_popen = render.subprocess.Popen

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            exe = os.path.basename(str(cmd[0])).lower()
            if "afterfx" not in exe and "aerender" not in exe:
                self._real = real_popen(cmd, *args, **kwargs)
                return
            launched.append(exe)
            self._real = None
            self.pid = 424242
            self.returncode = 0
            if "afterfx" in exe and on_afterfx is not None:
                on_afterfx(cmd)
            self.stdout = iter([])

        def poll(self):
            if self._real is not None:
                return self._real.poll()
            return 0

        def wait(self):
            if self._real is not None:
                return self._real.wait()
            return 0

        def __getattr__(self, name):
            if self._real is not None:
                return getattr(self._real, name)
            raise AttributeError(name)

    monkeypatch.setattr(render.subprocess, "Popen", FakePopen)


def _reset_job(names):
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    render.items_init(render.RJOB, render.RLOCK, names)


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("REELSI_RENDER_STATS", str(tmp_path / "stats.json"))
    monkeypatch.setattr(
        render, "_find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    monkeypatch.setattr(render, "_ae_running", lambda: False)   # в тестах AE «закрыт»


# --- (a) гигиена журнала: старый .aelog.txt удаляется ДО запуска AfterFX -----------

def test_batch_deletes_stale_aelog_before_master(clips, tmp_path, monkeypatch):
    """Набор: .aelog.txt прошлого прогона (с «evalFile ok:») удаляется до запуска
    мастера, и ни одной строки «собран:» из старого лога в журнал джоба не попадает."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    aelog = os.path.join(outdir, "reelsi_batch.aelog.txt")
    jsx1 = os.path.abspath(os.path.join(outdir, "01_C0233.jsx"))
    jsx2 = os.path.abspath(os.path.join(outdir, "02_C0234.jsx"))
    with open(aelog, "w", encoding="utf-8") as f:       # «прошлый прогон»
        f.write("REELSI-MASTER: начат\n")
        f.write("evalFile ok: %s\n" % jsx1)
        f.write("evalFile ok: %s\n" % jsx2)
        f.write("comp ok: C0233\ncomp ok: C0234\nREELSI-MASTER: готово\n")
    seen_at_launch = []

    def on_afterfx(cmd):
        # мастер НИЧЕГО не создаёт — если старый лог пережил гигиену, он будет прочитан
        seen_at_launch.append(os.path.isfile(aelog))

    launched = []
    _install_popen(monkeypatch, launched, on_afterfx=on_afterfx)
    batch = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False},
             {"xml_path": clips["xml2"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233", "02_C0234"])
    render._run_render_batch(batch, outdir, render_dir)

    assert launched == ["fake_afterfx.exe"], launched
    assert seen_at_launch == [False], "старый .aelog.txt не удалён до запуска AfterFX"
    text = _log_text(render.RJOB)
    assert "собран:" not in text, "строки старого лога попали в журнал джоба:\n" + text
    assert any(f["name"] in ("01_C0233", "02_C0234") for f in render.RJOB["failed"])


def test_single_deletes_stale_aelog_before_afterfx(clips, tmp_path, monkeypatch):
    """Одиночный путь: старый .aelog.txt удаляется до запуска AfterFX, его строки в
    журнал джоба не попадают (после «пустого» прогона файла нет — «скрипт не запустился»)."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    aelog = os.path.join(outdir, "01_C0233.aelog.txt")
    with open(aelog, "w", encoding="utf-8") as f:
        f.write("REELSI-OLD: прошлый прогон\n")
    seen_at_launch = []

    def on_afterfx(cmd):
        seen_at_launch.append(os.path.isfile(aelog))     # гигиена должна была удалить

    launched = []
    _install_popen(monkeypatch, launched, on_afterfx=on_afterfx)
    job = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233"])
    render._run_render_single(job, outdir, render_dir)

    assert launched == ["fake_afterfx.exe"], launched
    assert seen_at_launch == [False], "старый .aelog.txt не удалён до запуска AfterFX"
    text = _log_text(render.RJOB)
    assert "REELSI-OLD" not in text, "строки старого лога попали в журнал джоба:\n" + text
    assert any(f["name"] == "01_C0233" for f in render.RJOB["failed"])


# --- (b) .aep от прошлого прогона, не обновлённый этим, успехом НЕ считается ---------

def test_batch_stale_aep_not_updated_fails(clips, tmp_path, monkeypatch):
    """Набор: .aep остался от прошлого прогона и не пересохранён мастером — ролики в
    failed, aerender не запускается, в логе прямо сказано про прошлый прогон."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    aep = os.path.join(outdir, "reelsi_batch.aep")
    with open(aep, "wb") as f:
        f.write(b"old_project")
    past = 1000000000.0                      # время «прошлого прогона»
    os.utime(aep, (past, past))
    jsx1 = os.path.abspath(os.path.join(outdir, "01_C0233.jsx"))
    jsx2 = os.path.abspath(os.path.join(outdir, "02_C0234.jsx"))

    def on_afterfx(cmd):
        # мастер идёт и пишет журнал, но проект НЕ пересохраняет — .aep остаётся старым
        with open(os.path.join(outdir, "reelsi_batch.aelog.txt"), "w",
                  encoding="utf-8") as f:
            f.write("REELSI-MASTER: начат\n")
            f.write("evalFile ok: %s\n" % jsx1)
            f.write("evalFile ok: %s\n" % jsx2)
            f.write("REELSI-MASTER: готово\n")

    launched = []
    _install_popen(monkeypatch, launched, on_afterfx=on_afterfx)
    batch = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False},
             {"xml_path": clips["xml2"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233", "02_C0234"])
    render._run_render_batch(batch, outdir, render_dir)

    assert launched == ["fake_afterfx.exe"], "aerender запустился по старому .aep: %r" % launched
    reasons = [f["reason"] for f in render.RJOB["failed"]]
    assert len(reasons) == 2, reasons
    assert all("не сохранил .aep" in r for r in reasons), reasons
    text = _log_text(render.RJOB)
    assert "остался от прошлого прогона" in text, "нет строки про .aep прошлого прогона:\n" + text
    assert not render.RJOB["result"]


def test_single_stale_aep_not_updated_fails(clips, tmp_path, monkeypatch):
    """Одиночный путь: старый .aep не пересохранён — ролик в failed, aerender не идёт."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    aep = os.path.join(outdir, "01_C0233.aep")
    with open(aep, "wb") as f:
        f.write(b"old_project")
    past = 1000000000.0
    os.utime(aep, (past, past))

    def on_afterfx(cmd):
        with open(os.path.join(outdir, "01_C0233.aelog.txt"), "w",
                  encoding="utf-8") as f:
            f.write("REELSI: ок\n")     # журнал пишется, проект — нет

    launched = []
    _install_popen(monkeypatch, launched, on_afterfx=on_afterfx)
    job = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233"])
    render._run_render_single(job, outdir, render_dir)

    assert launched == ["fake_afterfx.exe"], "aerender запустился по старому .aep: %r" % launched
    reasons = [f["reason"] for f in render.RJOB["failed"]]
    assert len(reasons) == 1, reasons
    assert "не сохранил .aep" in reasons[0], reasons
    text = _log_text(render.RJOB)
    assert "остался от прошлого прогона" in text, "нет строки про .aep прошлого прогона:\n" + text
    assert not render.RJOB["result"]


# --- (c) открытый After Effects — стоп ДО запуска ----------------------------------

def test_batch_open_afterfx_stops_before_launch(clips, tmp_path, monkeypatch):
    """Набор: AfterFX.exe уже запущен — прогон не начинается, AfterFX не вызывается,
    в журнале требование закрыть After Effects."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_ae_running", lambda: True)
    launched = []
    _install_popen(monkeypatch, launched)
    batch = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False},
             {"xml_path": clips["xml2"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233", "02_C0234"])
    render._run_render_batch(batch, outdir, render_dir)

    assert launched == [], "AfterFX запущен при открытой копии AE: %r" % launched
    reasons = [f["reason"] for f in render.RJOB["failed"]]
    assert len(reasons) == 2, reasons
    assert all("After Effects открыт" in r for r in reasons), reasons
    text = _log_text(render.RJOB)
    assert "After Effects ОТКРЫТ" in text, "в журнале нет требования закрыть AE:\n" + text
    assert not render.RJOB["result"]


def test_single_open_afterfx_stops_before_launch(clips, tmp_path, monkeypatch):
    """Одиночный путь: открытый After Effects останавливает прогон до запуска AfterFX."""
    outdir = str(tmp_path / "jsx")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir)
    os.makedirs(render_dir)
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(render, "_ae_running", lambda: True)
    launched = []
    _install_popen(monkeypatch, launched)
    job = [{"xml_path": clips["xml1"], "outdir": outdir, "roto": False}]
    _reset_job(["01_C0233"])
    render._run_render_single(job, outdir, render_dir)

    assert launched == [], "AfterFX запущен при открытой копии AE: %r" % launched
    reasons = [f["reason"] for f in render.RJOB["failed"]]
    assert len(reasons) == 1, reasons
    assert "After Effects открыт" in reasons[0], reasons
    text = _log_text(render.RJOB)
    assert "After Effects ОТКРЫТ" in text, "в журнале нет требования закрыть AE:\n" + text
    assert not render.RJOB["result"]


# --- (d) сторож простоя: молчащий процесс снимается по лимиту, а не висит ------------

class _AliveSilent:
    """Процесс, который ЖИВЁТ (poll()=None) и ничего не выводит — имитация зависшего AE."""
    def __init__(self, *args, **kwargs):
        self.pid = 31337
        self.returncode = None
        self.stdout = iter([])

    def poll(self):
        return None

    def wait(self):
        return 0


def _clock(step=300.0):
    """Подмена часов: каждый вызов уводит время на step секунд вперёд."""
    state = {"t": 1000.0}

    def _fake_time():
        state["t"] += step
        return state["t"]
    return _fake_time


def test_master_stall_guard_kills_silent_process(tmp_path, monkeypatch):
    """_run_proc_master: мастер жив и молчит — предупреждение, затем снятие по лимиту
    (не вечное ожидание). Время двигается подменой таймера, sleep'а нет."""
    outdir = str(tmp_path)
    jsx1 = os.path.abspath(os.path.join(outdir, "01_C0233.jsx"))
    jsx2 = os.path.abspath(os.path.join(outdir, "02_C0234.jsx"))
    good = [("01_C0233", "C0233", jsx1, 100),
            ("02_C0234", "C0234", jsx2, 100)]
    _reset_job(["01_C0233", "02_C0234"])
    monkeypatch.setattr(render.subprocess, "Popen", _AliveSilent)
    killed = []
    monkeypatch.setattr(render, "_kill_proc", lambda p: killed.append(p))
    monkeypatch.setattr(render._time, "time", _clock(step=300.0))
    monkeypatch.setattr(render._time, "sleep", lambda *a, **k: None)

    render._run_proc_master(
        "fake_AfterFX.exe", "-noui", "-r", "master.jsx",
        good=good, render_dir=str(tmp_path / "exp"),
        aelog_path=os.path.join(outdir, "reelsi_batch.aelog.txt"))

    assert killed, "сторож не снял молчащий мастер"
    text = _log_text(render.RJOB)
    assert "не отвечает" in text and "прогон снят" in text, "нет сообщения о снятии:\n" + text
    reasons = [f["reason"] for f in render.RJOB["failed"]]
    assert any("не отвечает" in r for r in reasons), reasons


def test_single_afx_stall_guard_kills_silent_process(tmp_path, monkeypatch):
    """Одиночный AfterFX: процесс жив и молчит — снятие по лимиту (rc=_AE_STALLED),
    а не вечное ожидание. Время двигается подменой таймера, sleep'а нет."""
    _reset_job(["01_C0233"])
    monkeypatch.setattr(render.subprocess, "Popen", _AliveSilent)
    killed = []
    monkeypatch.setattr(render, "_kill_proc", lambda p: killed.append(p))
    monkeypatch.setattr(render._time, "time", _clock(step=300.0))
    monkeypatch.setattr(render._time, "sleep", lambda *a, **k: None)

    rc = render._run_proc_afx(["fake_AfterFX.exe", "-noui", "-r", "01_C0233.jsx"])

    assert rc == render._AE_STALLED, rc
    assert killed, "сторож не снял молчащий AfterFX"
    text = _log_text(render.RJOB)
    assert "не отвечает" in text and "прогон снят" in text, "нет сообщения о снятии:\n" + text
