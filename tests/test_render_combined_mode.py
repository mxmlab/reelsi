# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание C: «Один на всё» работает и для кнопки «Собрать и отрендерить».

Кнопка «Собрать и отрендерить» (startRender) раньше жёстко слала mode='separate',
а бэкенд (api_render_run) mode принимал, но в _run_render_job не передавал вовсе —
человек выбирал «Один на всё», а рендер всё равно собирал по .jsx на ролик.
Теперь:
- build_combined умеет (comps_global=True, comp_names_out) собирать таймлайны так,
  чтобы мастер-скрипт выполнил ОДИН файл (REELSI_COMPS + префиксы бинов), и отдавать
  имена главных композиций по порядку;
- рендер при mode="combined" собирает один Reelsi_all.jsx, предполёт гоняет по нему
  ОДИН раз (непрошедший ролик снимает весь набор — в лог пишется прямо), а мастер
  получает ровно один путь;
- startRender читает радио multimode тем же способом, что и buildMulti.

AE не запускается: процессы AfterFX/aerender подменяются, как в test_fn_batch_render_e2e.

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

from core import xml2ae  # noqa: E402
import api.render as render  # noqa: E402


def _fileurl(p):
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


@pytest.fixture()
def xmls(tmp_path):
    """Два XML-файла набора (01_C0233.xml -> композиция C0233, 02_C0234.xml -> C0234)
    и фиктивные медиа камер (должны существовать на диске: предполёт verify_jsx)."""
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
    xml1.write_text(xml_content.replace("<name>CLIP-006</name>", "<name>C0233</name>"),
                    encoding="utf-8")
    xml2 = tmp_path / "02_C0234.xml"
    xml2.write_text(xml_content.replace("<name>CLIP-006</name>", "<name>C0234</name>"),
                    encoding="utf-8")
    return {"xml1": str(xml1), "xml2": str(xml2), "dir": str(tmp_path),
            "cam1": str(cam1), "cam2": str(cam2)}


def _jobs(xmls, outdir):
    """Сырые jobs фронта (как их шлёт startRender): по ролику набора."""
    return [{"xml": xmls["xml1"], "outdir": outdir, "roto": False, "style": {"roto": False}},
            {"xml": xmls["xml2"], "outdir": outdir, "roto": False, "style": {"roto": False}}]


def _norm(jobs):
    """api_render_run нормализует набор ДО ответа, а в поток отдаёт уже готовый,
    поэтому и прямой вызов диспетчера в тесте получает тот же вид,
    что в бою, — нормализованный."""
    from api.build import _norm_build_jobs
    return _norm_build_jobs(jobs)


def _reset_job(names):
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    render.items_init(render.RJOB, render.RLOCK, names)


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


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("REELSI_RENDER_STATS", str(tmp_path / "stats.json"))
    monkeypatch.setattr(
        render, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    # Открытая копия After Effects останавливает прогон ДО запуска AfterFX (задание
    # AE-Hygiene) — в тесте AE «закрыт», иначе результат зависел бы от машины.
    monkeypatch.setattr(render, "ae_running", lambda: False)


# ---------------- (a,b) build_combined: comps_global, бины, имена композиций -------

def test_combined_build_comps_global_push_per_timeline_and_bins(xmls, tmp_path):
    """build_combined с comps_global=True: строка $.global.REELSI_COMPS.push встречается
    по разу на каждый таймлайн, бины получают префиксы стемов; с comp_names_out —
    имена главных композиций в порядке набора."""
    jobs = [{"xml_path": xmls["xml1"], "roto": False},
            {"xml_path": xmls["xml2"], "roto": False}]
    names = []
    path, n = xml2ae.build_combined(jobs, str(tmp_path / "all.jsx"),
                                    emit=lambda *a, **k: None,
                                    comps_global=True, comp_names_out=names)
    assert n == 2
    txt = open(path, encoding="utf-8-sig").read()
    # главные композиции обоих таймлайнов уходят в $.global.REELSI_COMPS — из чего
    # мастер-скрипт строит очередь рендера
    assert txt.count("$.global.REELSI_COMPS.push(main);") == 2, (
        "REELSI_COMPS.push не по разу на таймлайн")
    # бины с префиксами стемов: «Вставки»/«Субтитры» роликов в одном проекте не каша
    assert 'var BIN_PFX = "01_C0233 — ";' in txt, "нет префикса бинов первого ролика"
    assert 'var BIN_PFX = "02_C0234 — ";' in txt, "нет префикса бинов второго ролика"
    # имена главных композиций — по порядку набора (имя ≠ стем: 01_C0233 -> C0233)
    assert names == ["C0233", "C0234"], names


def test_combined_build_default_byte_identical(xmls, tmp_path):
    """build_combined без comps_global/comp_names_out даёт БАЙТ В БАЙТ прежний .jsx:
    новые параметры не должны сдвинуть golden кнопки «Собрать набор»."""
    jobs = [{"xml_path": xmls["xml1"], "roto": False},
            {"xml_path": xmls["xml2"], "roto": False}]
    emit = lambda *a, **k: None  # noqa: E731
    p_new, n = xml2ae.build_combined(jobs, str(tmp_path / "all.jsx"), emit=emit)
    assert n == 2
    # «старое» поведение, собранное вручную, как build_combined собирал раньше
    parts = []
    for j in jobs:
        kw = {k: v for k, v in j.items() if k != "xml_path"}
        kw.setdefault("emit", emit)
        kw["cancel"] = (lambda: False)
        src, _, _ = xml2ae.to_ae_full(j["xml_path"], return_source=True, **kw)
        parts.append(src)
    expected = "\n\n// ===== следующий таймлайн =====\n\n".join(parts)
    assert open(p_new, encoding="utf-8-sig").read() == expected, (
        "build_combined без параметров изменил .jsx — golden нарушен")
    # явные значения по умолчанию — тот же результат, что и вызов без них
    p_def, _ = xml2ae.build_combined(jobs, str(tmp_path / "all2.jsx"), emit=emit,
                                     comps_global=False, comp_names_out=None)
    a = open(p_new, encoding="utf-8-sig").read()
    b = open(p_def, encoding="utf-8-sig").read()
    assert a == b, "вызов с параметрами по умолчанию изменил .jsx"
    assert "REELSI_COMPS" not in a and "BIN_PFX" not in a, (
        "по умолчанию в .jsx попали строки набора")


# ---------------- (c) рендер mode="combined": один файл, мастер — один путь --------

def _fake_popen_combined(monkeypatch, outdir, render_dir, mov1, mov2, combined_jp):
    """FakePopen для _run_render_job(mode='combined'): AfterFX «выполняет» мастер
    (пишет .aelog.txt и .aep), aerender рендерит обе композиции по очереди.
    Служебные процессы (node --check из предполёта и т.п.) уходят в настоящий
    Popen — как в test_ae_hygiene."""
    real_popen = render.subprocess.Popen

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            exe = os.path.basename(str(cmd[0])).lower()
            if "afterfx" not in exe and "aerender" not in exe:
                self._real = real_popen(cmd, *args, **kwargs)
                return
            self._real = None
            self.cmd = cmd
            self.pid = 99999
            self.returncode = 0
            if "afterfx" in exe:
                aep_path = os.path.join(outdir, "reelsi_batch.aep")
                aelog_path = os.path.join(outdir, "reelsi_batch.aelog.txt")
                with open(aep_path, "wb") as f:
                    f.write(b"fake_aep")
                ok_path = combined_jp.replace("\\", "/")
                with open(aelog_path, "w", encoding="utf-8") as f:
                    f.write("REELSI-MASTER: начат\n")
                    f.write("таймлайн ok: C0233\n")
                    f.write("таймлайн ok: C0234\n")
                    f.write(f"evalFile ok: {ok_path}\n")
                    f.write("comp ok: C0233\n")
                    f.write("comp ok: C0234\n")
                    f.write("REELSI-MASTER: готово\n")
                self.stdout = iter(["AfterFX master execution finished"])
            else:  # aerender
                self.stdout = iter([
                    "PROGRESS: Launching After Effects...",
                    "PROGRESS:  Start: 0:00:00:00",
                    "PROGRESS:  End: 0:00:00:02",
                    "PROGRESS:  Duration: 0:00:00:03",
                    "PROGRESS:  Frame Rate: 60.00 (comp)",
                    "PROGRESS:  Output Module: Untitled 1",
                    rf"PROGRESS:  Output To: {mov1}",
                    "PROGRESS:  Format: QuickTime",
                    "PROGRESS:  0:00:00:00 (1): 0 Seconds",
                    "PROGRESS:  0:00:00:01 (2): 0 Seconds",
                    "PROGRESS:  0:00:00:02 (3): 0 Seconds",
                    'PROGRESS:  8/26/2026 1:09:51 AM: Finished composition "C0233".',
                    "PROGRESS:  Start: 0:00:00:00",
                    "PROGRESS:  End: 0:00:00:02",
                    "PROGRESS:  Duration: 0:00:00:03",
                    "PROGRESS:  Frame Rate: 60.00 (comp)",
                    "PROGRESS:  Output Module: Untitled 1",
                    rf"PROGRESS:  Output To: {mov2}",
                    "PROGRESS:  Format: QuickTime",
                    "PROGRESS:  0:00:00:00 (1): 0 Seconds",
                    "PROGRESS:  0:00:00:01 (2): 0 Seconds",
                    "PROGRESS:  0:00:00:02 (3): 0 Seconds",
                    'PROGRESS:  8/26/2026 1:09:55 AM: Finished composition "C0234".',
                ])

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


def test_render_combined_collects_one_file_and_master_single_path(xmls, tmp_path,
                                                                  monkeypatch):
    """mode="combined": рендер собирает ОДИН Reelsi_all.jsx (клиповых .jsx нет),
    мастер-скрипт получает ровно один путь, обе композиции рендерятся по именам."""
    outdir = str(tmp_path / "jsx_out")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    mov1 = os.path.join(render_dir, "C0233.mov")
    mov2 = os.path.join(render_dir, "C0234.mov")
    with open(mov1, "wb") as f:
        f.write(b"quicktime_data")
    with open(mov2, "wb") as f:
        f.write(b"quicktime_data")

    _env(monkeypatch, tmp_path)
    combined_jp = os.path.abspath(os.path.join(outdir, "Reelsi_all.jsx"))
    _fake_popen_combined(monkeypatch, outdir, render_dir, mov1, mov2, combined_jp)

    # подпись этапов: на «сборке проекта» этап — ОДИН шаг (мастер выполняет один файл)
    history = []

    def record_state():
        with render.RLOCK:
            p = render.RJOB.get("pct")
            lbl = render.RJOB.get("stage_label")
            sd = render.RJOB.get("stage_done")
            st = render.RJOB.get("stage_total")
            if p is not None:
                history.append((p, lbl, sd, st))

    orig_remit = render.remit

    def hooked_remit(*args, **kwargs):
        orig_remit(*args, **kwargs)
        record_state()

    monkeypatch.setattr(render, "remit", hooked_remit)

    _reset_job(["01_C0233", "02_C0234"])
    render._run_render_job(_norm(_jobs(xmls, outdir)), outdir, render_dir)

    assert not render.RJOB["failed"], f"Рендер упал: {render.RJOB['failed']}"
    assert render.RJOB["result"] == [mov1, mov2], render.RJOB["result"]
    assert render.RJOB["done"] is True
    # один общий .jsx — и НИ ОДНОГО клипового
    assert os.path.isfile(combined_jp), "Reelsi_all.jsx не собран"
    assert not os.path.isfile(os.path.join(outdir, "01_C0233.jsx")), (
        "клиповый .jsx собран в режиме «Один на всё»")
    assert not os.path.isfile(os.path.join(outdir, "02_C0234.jsx")), (
        "клиповый .jsx собран в режиме «Один на всё»")
    # мастер-скрипт получил ровно ОДИН путь — Reelsi_all.jsx
    master = open(os.path.join(outdir, "render_master.jsx"), encoding="utf-8-sig").read()
    assert master.count(combined_jp.replace("\\", "/")) == 1, (
        "мастер должен получить ровно один путь — Reelsi_all.jsx")
    assert "01_C0233.jsx" not in master and "02_C0234.jsx" not in master
    # «сборка проекта» — stage_total == 2 (по роликам набора), значения 1 нет
    project_steps = [st for _p, lbl, _sd, st in history if lbl == "сборка проекта"]
    assert project_steps and all(st == 2 for st in project_steps), (
        f"на этапе «сборка проекта» stage_total обязан быть 2: {project_steps}")
    assert 1 not in project_steps, f"значение 1 не должно появляться в stage_total: {project_steps}"


def test_render_combined_preflight_fail_stops_whole_set(xmls, tmp_path, monkeypatch):
    """Набор роликов: непрошедший предполёт ролик останавливает прогон ЦЕЛИКОМ
    (в общем Reelsi_all.jsx отсеять один нельзя), в логе — объяснение и совет
    исправить файл или убрать ролик. AE не запускается вовсе."""
    outdir = str(tmp_path / "jsx_out")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)
    _env(monkeypatch, tmp_path)

    launched = []
    real_popen = render.subprocess.Popen

    class FakePopen:
        """AE-процессы фиксируются в launched, служебные (node --check из предполёта
        и т.п.) уходят в настоящий Popen — как в test_ae_hygiene."""

        def __init__(self, cmd, *args, **kwargs):
            exe = os.path.basename(str(cmd[0])).lower()
            if "afterfx" not in exe and "aerender" not in exe:
                self._real = real_popen(cmd, *args, **kwargs)
                return
            launched.append(cmd)
            raise AssertionError("AE не должен запускаться при непройденном предполёте")

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

    os.remove(xmls["cam1"])      # файл камеры пропал — предполёт обязан это поймать

    _reset_job(["01_C0233", "02_C0234"])
    render._run_render_job(_norm(_jobs(xmls, outdir)), outdir, render_dir)

    assert launched == [], "AfterFX/aerender запущены при непройденном предполёте"
    assert not render.RJOB["result"], "непрошедший предполёт набор отрендерился"
    names = [f["name"] for f in render.RJOB["failed"]]
    assert sorted(names) == ["01_C0233", "02_C0234"], (
        f"остановиться должны ВСЕ ролики набора: {names}")
    text = _log_text(render.RJOB)
    assert "снимает ВЕСЬ набор" in text, f"в логе нет объяснения остановки:\n{text}"
    assert "убери ролик из набора" in text, f"в логе нет совета исправить файл или убрать ролик:\n{text}"


# ---------------- (e) startRender НЕ шлёт mode, buildMulti читает радио multimode ---

def test_start_render_does_not_send_mode_and_build_multi_reads_radio():
    """startRender НЕ читает радио multimode и не передаёт mode (рендер набора
    всегда собирает один Reelsi_all.jsx, решение пользователя 2026-09-11), а
    buildMulti радио читает."""
    js = open(os.path.join(ROOT, "static", "app", "90-ae.js"), encoding="utf-8").read()
    i_start = js.index("async function startRender")
    i_poll = js.index("async function pollRender")
    start_render = js[i_start:i_poll]
    i_build = js.index("async function buildMulti")
    build_multi = js[i_build:js.index("function resolveIntroFor", i_build)]
    assert "input[name=multimode]" not in start_render, (
        "startRender не должен читать радио multimode")
    assert "mode" not in start_render.split("const body={")[1].split("};")[0], (
        "startRender не должен передавать mode в body")
    assert "input[name=multimode]:checked" in build_multi, (
        "buildMulti должен читать радио multimode")


# ---------------- (f) Задание GQ: рендер всегда combined ---------------------

def test_run_render_job_multiple_clips_always_calls_combined(tmp_path, monkeypatch):
    """_run_render_job с двумя роликами уходит в _run_render_combined и ни в какой
    другой путь (решение пользователя 2026-09-11; клипового пути
    _run_render_batch в коде нет)."""
    f1 = tmp_path / "01.xml"
    f2 = tmp_path / "02.xml"
    f1.write_text("<xml/>", encoding="utf-8")
    f2.write_text("<xml/>", encoding="utf-8")
    jobs = [{"xml": str(f1)}, {"xml": str(f2)}]

    called = []
    monkeypatch.setattr(render, "_run_render_combined", lambda norm, outdir, rdir: called.append("combined"))
    monkeypatch.setattr(render, "_run_render_single", lambda norm, outdir, rdir: called.append("single"))

    _reset_job(["01", "02"])
    render._run_render_job(_norm(jobs), str(tmp_path), str(tmp_path / "render"))
    assert called == ["combined"], f"Ожидался только вызов combined, получено: {called}"


def test_api_render_run_mode_separate_leads_to_combined(tmp_path, monkeypatch):
    """api_render_run с "mode": "separate" в теле всё равно приводит к combined."""
    from webui import app
    client = app.test_client()

    f1 = tmp_path / "01.xml"
    f2 = tmp_path / "02.xml"
    f1.write_text("<xml/>", encoding="utf-8")
    f2.write_text("<xml/>", encoding="utf-8")
    jobs = [{"xml": str(f1)}, {"xml": str(f2)}]

    called = []
    monkeypatch.setattr(render, "_run_render_combined", lambda norm, outdir, rdir: called.append("combined"))

    # Синхронно запускаем таргет треда, чтобы избежать гонок в тесте
    def fake_thread(target, args=(), daemon=True):
        class T:
            def start(self):
                target(*args)
        return T()
    monkeypatch.setattr(render.threading, "Thread", fake_thread)

    render.RJOB.update(running=False, done=False)
    resp = client.post("/api/render_run", json={
        "jobs": jobs,
        "mode": "separate",
        "batch_limit": 5,
        "outdir": str(tmp_path),
        "render_dir": str(tmp_path / "render")
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert called == ["combined"], f"Ожидался вызов combined, получено: {called}"

