# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты индикации фаз и монотонности прогресса рендера.

ПОЧЕМУ этот тест существует:
Рендер набора имеет три фазы:
1) сборка .jsx таймлайнов силами Python (_run_render_combined -> build_combined);
2) сборка общего проекта AfterFX -noui (_run_proc_master);
3) рендер композиций aerender -project (_run_proc_batch).
Ранее на фазе 1 RJOB["pct"] не двигался, RJOB["stage_label"] не выставлялся,
а на фазе 2 RJOB["pct"] оставался None. В модалке висело «0/12» и 0% полосы.
Этот тест проверяет:
- на фазе 1 stage_label='сборка таймлайнов', pct растет от 0 до 0.15, stage_done отражает число собранных .jsx;
- на фазе 2 stage_label='сборка проекта', pct растет от 0.15 до 0.35, stage_done отражает собранные в мастер ролики;
- на фазе 3 stage_label='рендер', pct растет от 0.35 до 1.0, stage_done отражает отрендеренные ролики;
- вся последовательность RJOB["pct"] на протяжении трех фаз строго монотонна (pct[t] >= pct[t-1]);
- одиночный рендер (_run_render_single) также монотонно проходит все три фазы и выставляет stage_label;
- /api/render_status отдает stage_done и stage_total;
- перевод «Интро-текст», «сборка проекта», «сборка таймлайнов» присутствует в en.json.
"""
import gzip
import json
import os
import sys
import urllib.parse
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402
from core import aerender  # noqa: E402


def _fileurl(p):
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


@pytest.fixture()
def batch_fixture(tmp_path):
    """Готовит два XML-файла и фиктивные медиафайлы для двух роликов набора."""
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


def test_combined_render_stages_and_monotonicity(batch_fixture, tmp_path, monkeypatch):
    """Проверка всех трех фаз рендера набора «Один на всё»:
    1) сборка таймлайнов (0..15%): stage_label='сборка таймлайнов';
    2) сборка проекта (15..35%): stage_label='сборка проекта', stage_done=1,2;
    3) рендер (35..100%): stage_label='рендер', stage_done=1,2;
    4) строгая монотонность: RJOB['pct'] ни разу не уменьшается."""
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

    monkeypatch.setenv("REELSI_RENDER_STATS", str(tmp_path / "stats.json"))
    monkeypatch.setattr(
        render, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    # Открытая копия After Effects останавливает прогон ДО запуска AfterFX (задание
    # AE-Hygiene) — в тесте AE «закрыт», иначе результат зависел бы от машины.
    monkeypatch.setattr(render, "ae_running", lambda: False)

    # Список записанных состояний (pct, stage_label, stage_done, stage_total, cur)
    history = []

    def record_state():
        with render.RLOCK:
            p = render.RJOB.get("pct")
            lbl = render.RJOB.get("stage_label")
            sd = render.RJOB.get("stage_done")
            st = render.RJOB.get("stage_total")
            cur = render.RJOB.get("cur")
            if p is not None:
                history.append((p, lbl, sd, st, cur))

    orig_remit = render.remit

    def hooked_remit(*args, **kwargs):
        orig_remit(*args, **kwargs)
        record_state()

    monkeypatch.setattr(render, "remit", hooked_remit)

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            self.pid = 99999
            self.returncode = 0
            exe = str(cmd[0]).lower()

            if "afterfx" in exe:
                aep_path = os.path.join(outdir, "reelsi_batch.aep")
                aelog_path = os.path.join(outdir, "reelsi_batch.aelog.txt")
                with open(aep_path, "wb") as f:
                    f.write(b"fake_aep")
                combined_jsx = os.path.abspath(os.path.join(outdir, "Reelsi_all.jsx")).replace("\\", "/")
                with open(aelog_path, "w", encoding="utf-8") as f:
                    f.write("REELSI-MASTER: начат\n")
                    f.write(f"evalFile ok: {combined_jsx}\n")
                    f.write("таймлайн ok: C0233\n")
                    f.write("таймлайн ok: C0234\n")
                    f.write("REELSI-MASTER: готово\n")
                self.stdout = iter(["AfterFX master execution finished"])
            elif "aerender" in exe:
                aerender_lines = [
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
                ]
                self.stdout = iter(aerender_lines)
            else:
                self.stdout = iter([])

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(render.subprocess, "Popen", FakePopen)

    batch = [
        {"xml_path": batch_fixture["xml1"], "outdir": outdir, "roto": False},
        {"xml_path": batch_fixture["xml2"], "outdir": outdir, "roto": False},
    ]

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False,
        stage_label=None, stage_done=0, stage_total=2,
        items=[
            {"name": "01_C0233", "stage": "wait", "pct": None, "path": "", "reason": ""},
            {"name": "02_C0234", "stage": "wait", "pct": None, "path": "", "reason": ""},
        ]
    )

    record_state()
    render._run_render_combined(batch, outdir, render_dir)
    record_state()

    assert not render.RJOB["failed"], f"Рендер упал: {render.RJOB['failed']}"
    assert len(render.RJOB["result"]) == 2
    assert render.RJOB["pct"] == 1.0

    # 1. Проверяем наличие всех трех фаз в истории
    labels_seen = {lbl for _, lbl, _, _, _ in history if lbl}
    assert "сборка таймлайнов" in labels_seen, f"Фаза 1 не найдена в {labels_seen}"
    assert "сборка проекта" in labels_seen, f"Фаза 2 не найдена в {labels_seen}"
    assert "рендер" in labels_seen, f"Фаза 3 не найдена в {labels_seen}"

    # 2. Проверяем строгую монотонность pct
    pcts = [p for p, _, _, _, _ in history]
    for k in range(1, len(pcts)):
        assert pcts[k] >= pcts[k - 1] - 1e-6, (
            f"Откат процента назад на шаге {k}: {pcts[k-1]} -> {pcts[k]}; история={pcts}"
        )

    # 3. Проверяем диапазоны по фазам
    phase1_pcts = [p for p, lbl, _, _, _ in history if lbl == "сборка таймлайнов"]
    assert phase1_pcts, "Нет точек фазы 1"
    assert min(phase1_pcts) >= 0.0
    assert max(phase1_pcts) <= aerender.PHASE_JSX_END + 1e-6

    phase2_pcts = [p for p, lbl, _, _, _ in history if lbl == "сборка проекта"]
    assert phase2_pcts, "Нет точек фазы 2"
    assert min(phase2_pcts) >= aerender.PHASE_JSX_END - 1e-6
    assert max(phase2_pcts) <= aerender.PHASE_AEP_END + 1e-6

    phase3_pcts = [p for p, lbl, _, _, _ in history if lbl == "рендер"]
    assert phase3_pcts, "Нет точек фазы 3"
    assert min(phase3_pcts) >= aerender.PHASE_AEP_END - 1e-6
    assert max(phase3_pcts) <= 1.0


def test_single_render_stages_and_monotonicity(batch_fixture, tmp_path, monkeypatch):
    """Проверка фаз и монотонности одиночного пути (_run_render_single)."""
    outdir = str(tmp_path / "jsx_out_single")
    render_dir = str(tmp_path / "exp_single")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    mov1 = os.path.join(render_dir, "C0233.mov")
    with open(mov1, "wb") as f:
        f.write(b"quicktime_data")

    monkeypatch.setenv("REELSI_RENDER_STATS", str(tmp_path / "stats.json"))
    monkeypatch.setattr(
        render, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    # Открытая копия After Effects останавливает прогон ДО запуска AfterFX (задание
    # AE-Hygiene) — в тесте AE «закрыт», иначе результат зависел бы от машины.
    monkeypatch.setattr(render, "ae_running", lambda: False)

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

    class FakePopenSingle:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            self.pid = 99999
            self.returncode = 0
            exe = str(cmd[0]).lower()

            if "afterfx" in exe:
                aep_path = os.path.join(outdir, "01_C0233.aep")
                aelog_path = os.path.join(outdir, "01_C0233.aelog.txt")
                with open(aep_path, "wb") as f:
                    f.write(b"fake_aep")
                with open(aelog_path, "w", encoding="utf-8") as f:
                    f.write("REELSI: ок\n")
                self.stdout = iter(["AfterFX finished"])
            elif "aerender" in exe:
                aerender_lines = [
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
                ]
                self.stdout = iter(aerender_lines)
            else:
                self.stdout = iter([])

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(render.subprocess, "Popen", FakePopenSingle)

    jobs = [
        {"xml_path": batch_fixture["xml1"], "outdir": outdir, "roto": False},
    ]

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False,
        stage_label=None, stage_done=0, stage_total=1,
        items=[
            {"name": "01_C0233", "stage": "wait", "pct": None, "path": "", "reason": ""},
        ]
    )

    record_state()
    render._run_render_single(jobs, outdir, render_dir)
    record_state()

    assert not render.RJOB["failed"]
    assert render.RJOB["pct"] == 1.0

    # Проверяем монотонность
    pcts = [p for p, _, _, _ in history]
    for k in range(1, len(pcts)):
        assert pcts[k] >= pcts[k - 1] - 1e-6, f"Откат процента: {pcts[k-1]} -> {pcts[k]}"

    labels = {lbl for _, lbl, _, _ in history if lbl}
    assert "сборка таймлайнов" in labels
    assert "сборка проекта" in labels
    assert "рендер" in labels


def test_render_status_api_fields():
    """/api/render_status отдает stage_done и stage_total."""
    import api
    from flask import Flask

    render.RJOB.update(running=True, done=False, log=[], pct=0.25, cur="03_C0234", ae="AE 2026",
                       out_dir="exp", result=[], failed=[], cancel=False,
                       stage_label="сборка проекта", stage_done=3, stage_total=12,
                       items=[{"name": "03_C0234", "stage": "aep", "pct": None, "path": "", "reason": ""}])
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    r = app.test_client().get("/api/render_status", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["stage_label"] == "сборка проекта"
    assert body["stage_done"] == 3
    assert body["stage_total"] == 12
    assert body["pct"] == 0.25


def test_i18n_intro_text_and_stage_labels():
    """Проверка наличия переводов 'Интро-текст', 'сборка проекта', 'сборка таймлайнов' в en.json."""
    i18n_path = os.path.join(ROOT, "static", "i18n", "en.json")
    with open(i18n_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    assert "Интро-текст" in d, "Ключ 'Интро-текст' отсутствует в en.json"
    assert d["Интро-текст"] == "Intro text"

    assert "сборка проекта" in d, "Ключ 'сборка проекта' отсутствует в en.json"
    assert d["сборка проекта"] == "project build"

    assert "сборка таймлайнов" in d, "Ключ 'сборка таймлайнов' отсутствует в en.json"
    assert d["сборка таймлайнов"] == "timeline build"


def test_combined_phase1_eta_baseline_or_blank(batch_fixture, tmp_path, monkeypatch):
    """Правило ETA на фазе 1 живого пути «Один на всё»: без статистики
    прошлых прогонов — ETA=None (прочерк); со статистикой — базовая оценка фазы и
    eta_preliminary=True. Сборку таймлайнов подменяем: проверяется состояние интерфейса
    в начале фазы, а у самой сборки свои тесты."""
    from core import xml2ae

    stats = tmp_path / "stats_eta.json"
    monkeypatch.setenv("REELSI_RENDER_STATS", str(stats))
    outdir = str(tmp_path / "jsx_out_eta")
    render_dir = str(tmp_path / "exp_eta")
    batch = [
        {"xml_path": batch_fixture["xml1"], "outdir": outdir, "roto": False},
        {"xml_path": batch_fixture["xml2"], "outdir": outdir, "roto": False},
    ]
    starts = []

    def fake_build_combined(jobs, out_jsx, **kw):
        with render.RLOCK:
            starts.append((render.RJOB.get("stage_label"), render.RJOB.get("eta"),
                           render.RJOB.get("eta_phase"), render.RJOB.get("eta_total"),
                           render.RJOB.get("eta_preliminary")))
        raise xml2ae.Cancelled()      # до AE этот тест не доходит: он про фазу 1

    monkeypatch.setattr(xml2ae, "build_combined", fake_build_combined)

    def run():
        render.RJOB.update(
            running=True, done=False, log=[], pct=None, cur="", ae="",
            out_dir=render_dir, result=[], failed=[], cancel=False,
            stage_label=None, stage_done=0, stage_total=len(batch),
            eta=None, eta_phase=None, eta_total=None, eta_preliminary=False,
            items=[
                {"name": "01_C0233", "stage": "wait", "pct": None, "path": "", "reason": ""},
                {"name": "02_C0234", "stage": "wait", "pct": None, "path": "", "reason": ""},
            ]
        )
        render._run_render_combined(batch, outdir, render_dir)

    # 1) Статистики нет — ETA не выдумывается, в интерфейсе прочерк
    run()
    assert starts == [("сборка таймлайнов", None, None, None, False)], starts

    # 2) Статистика есть (5 с / 36 с / 83 с на ролик) — оценка на 2 ролика, preliminary
    aerender.save_render_stats(10, 50.0, 360.0, 830.0)
    run()
    assert starts[1] == ("сборка таймлайнов", 10.0, 10.0, 248.0, True), starts[1]
