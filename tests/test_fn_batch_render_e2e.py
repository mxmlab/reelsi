# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сквозной тест рендера набора роликов (_run_render_batch в api/render.py).

ПОЧЕМУ этот тест существует:
В задании FJ функция _run_proc_master была объявлена с keyword-only параметрами:
    def _run_proc_master(afx, *args, good, render_dir, aelog_path):
но вызов на строке 624 передавал good, render_dir и aelog позиционно.
Из-за отсутствия сквозного теста на _run_render_batch (проверялись только отдельные
функции парсинга и одиночный рендер) TypeError: _run_proc_master() missing 3 required
keyword-only arguments доехал до пользователя.

Этот тест прогоняет _run_render_batch целиком на наборе из двух роликов с подменой
subprocess.Popen (AfterFX и aerender не запускаются) и проверяет:
1) правильность вызова _run_proc_master и _run_proc_batch;
2) сборку .jsx и мастер-скрипта;
3) прохождение стадий очереди до 'done' с корректными путями .mov по имени композиций.
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

    # Ролик 1: имя файла 01_C0233.xml, секвенция C0233
    xml1_path = tmp_path / "01_C0233.xml"
    content1 = xml_content.replace("<name>CLIP-006</name>", "<name>C0233</name>")
    xml1_path.write_text(content1, encoding="utf-8")

    # Ролик 2: имя файла 02_C0234.xml, секвенция C0234
    xml2_path = tmp_path / "02_C0234.xml"
    content2 = xml_content.replace("<name>CLIP-006</name>", "<name>C0234</name>")
    xml2_path.write_text(content2, encoding="utf-8")

    return {
        "xml1": str(xml1_path),
        "xml2": str(xml2_path),
        "dir": str(tmp_path),
    }


def test_batch_render_e2e_two_clips(batch_fixture, tmp_path, monkeypatch):
    """Сквозной прогон _run_render_batch на наборе из двух роликов:
    сборка .jsx -> предполёт -> _run_proc_master -> _run_proc_batch -> done для каждого .mov."""
    outdir = str(tmp_path / "jsx_out")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    # Заранее создаем ожидаемые .mov файлы (как если бы aerender их записал)
    mov1 = os.path.join(render_dir, "C0233.mov")
    mov2 = os.path.join(render_dir, "C0234.mov")
    with open(mov1, "wb") as f:
        f.write(b"quicktime_data")
    with open(mov2, "wb") as f:
        f.write(b"quicktime_data")

    # Подделка _find_ae
    monkeypatch.setattr(
        render, "_find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    # Открытая копия After Effects останавливает прогон ДО запуска AfterFX (задание
    # AE-Hygiene) — в тесте AE «закрыт», иначе результат зависел бы от машины.
    monkeypatch.setattr(render, "_ae_running", lambda: False)

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            self.pid = 99999
            self.returncode = 0
            exe = str(cmd[0]).lower()

            if "afterfx" in exe:
                # AfterFX выполняет master_call. Создаем .aelog.txt и .aep
                # Путь aep_path и aelog создаются в batch_dir (outdir)
                aep_path = os.path.join(outdir, "reelsi_batch.aep")
                aelog_path = os.path.join(outdir, "reelsi_batch.aelog.txt")
                with open(aep_path, "wb") as f:
                    f.write(b"fake_aep")
                jsx1 = os.path.abspath(os.path.join(outdir, "01_C0233.jsx"))
                jsx2 = os.path.abspath(os.path.join(outdir, "02_C0234.jsx"))
                with open(aelog_path, "w", encoding="utf-8") as f:
                    f.write("REELSI-MASTER: начат\n")
                    f.write(f"evalFile ok: {jsx1}\n")
                    f.write(f"evalFile ok: {jsx2}\n")
                    f.write("comp ok: C0233\n")
                    f.write("comp ok: C0234\n")
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

                def _aerender_stream():
                    for line in aerender_lines:
                        # В любой момент рендера стадию render имеет НЕ БОЛЬШЕ ОДНОГО элемента
                        items = render.RJOB.get("items", [])
                        rendering = [it for it in items if it.get("stage") == "render"]
                        assert len(rendering) <= 1, (
                            f"В стадии render больше одного элемента ({len(rendering)}): {rendering}"
                        )
                        for it in items:
                            assert it.get("stage") in ("wait", "render", "done", "error"), (
                                f"Недопустимая стадия элемента {it}: {it.get('stage')}"
                            )
                        yield line

                self.stdout = _aerender_stream()
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
        items=[
            {"name": "01_C0233", "stage": "wait", "pct": None, "path": "", "reason": ""},
            {"name": "02_C0234", "stage": "wait", "pct": None, "path": "", "reason": ""},
        ]
    )

    render._run_render_batch(batch, outdir, render_dir)

    assert not render.RJOB["failed"], f"Рендер упал с ошибками: {render.RJOB['failed']}"
    assert len(render.RJOB["result"]) == 2
    assert render.RJOB["result"] == [mov1, mov2]

    items = render.RJOB["items"]
    assert len(items) == 2
    assert items[0]["name"] == "01_C0233"
    assert items[0]["stage"] == "done"
    assert items[0]["pct"] == 1.0
    assert items[0]["path"] == mov1

    assert items[1]["name"] == "02_C0234"
    assert items[1]["stage"] == "done"
    assert items[1]["pct"] == 1.0
    assert items[1]["path"] == mov2


def test_render_job_batch_dispatcher(batch_fixture, tmp_path, monkeypatch):
    """Сквозной прогон диспетчера _run_render_job с набором из 2 клипов:
    нормализация jobs -> items_init -> _run_render_combined -> RJOB['done']=True, RJOB['failed']=[]
    (задание GQ: решение пользователя 2026-09-11 — набор всегда собирается в один Reelsi_all.jsx)."""
    outdir = str(tmp_path / "jsx_out_job")
    render_dir = str(tmp_path / "exp_job")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    mov1 = os.path.join(render_dir, "C0233.mov")
    mov2 = os.path.join(render_dir, "C0234.mov")
    with open(mov1, "wb") as f:
        f.write(b"data")
    with open(mov2, "wb") as f:
        f.write(b"data")

    monkeypatch.setattr(
        render, "_find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    # Открытая копия After Effects останавливает прогон ДО запуска AfterFX (задание
    # AE-Hygiene) — в тесте AE «закрыт», иначе результат зависел бы от машины.
    monkeypatch.setattr(render, "_ae_running", lambda: False)

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
                    f.write("comp ok: C0233\n")
                    f.write("comp ok: C0234\n")
                    f.write("REELSI-MASTER: готово\n")
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

                def _aerender_stream():
                    for line in aerender_lines:
                        items = render.RJOB.get("items", [])
                        rendering = [it for it in items if it.get("stage") == "render"]
                        assert len(rendering) <= 1, (
                            f"В стадии render больше одного элемента ({len(rendering)}): {rendering}"
                        )
                        for it in items:
                            assert it.get("stage") in ("wait", "render", "done", "error"), (
                                f"Недопустимая стадия элемента {it}: {it.get('stage')}"
                            )
                        yield line

                self.stdout = _aerender_stream()
            else:
                self.stdout = iter([])

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(render.subprocess, "Popen", FakePopen)

    jobs = [
        {"xml": batch_fixture["xml1"], "outdir": outdir, "roto": False},
        {"xml": batch_fixture["xml2"], "outdir": outdir, "roto": False},
    ]

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False,
        items=[]
    )

    render._run_render_job(jobs, outdir, render_dir)

    assert not render.RJOB["failed"], f"Рендер упал с ошибками: {render.RJOB['failed']}"
    assert render.RJOB["done"] is True
    assert render.RJOB["running"] is False
    assert render.RJOB["pct"] == 1.0
    assert len(render.RJOB["result"]) == 2
    assert render.RJOB["result"] == [mov1, mov2]

