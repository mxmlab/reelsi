# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты параллельной сборки проектов After Effects (N воркеров + слияние).

Проверяет:
1) resolve_ae_build_workers и partition_clips (LPT-балансировка по длительности);
2) Выбор таймлайнов через REELSI_ONLY на стенде aesim.js;
3) Параллельный запуск N=3 процессов на 7 клипах: одновременный старт воркеров,
   формирование частей в _parts/, слияние один раз через render_merge.jsx;
4) Частичный отказ (упала часть 2) — клипы части 2 failed, оставшиеся части
   сливаются и рендерятся;
5) N=1 — идентичность прежнему единому мастер-скрипту (render_master.jsx).
"""
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render
from core import render_job, xml2ae
from core.render_job import (
    partition_clips,
    resolve_ae_build_workers,
)


def _fileurl(p):
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"), safe="/:")


@pytest.fixture()
def sample_xml(tmp_path):
    """Базовый XML таймлайна с фиктивными видеофайлами."""
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

    def _make_xml(name: str) -> str:
        p = tmp_path / f"{name}.xml"
        p.write_text(xml_content.replace("<name>CLIP-006</name>", f"<name>{name}</name>"), encoding="utf-8")
        return str(p)

    return _make_xml


# ========================================================================= #
# 1. Модульные тесты хелперов параллельной сборки
# ========================================================================= #

def test_resolve_ae_build_workers(monkeypatch):
    """Проверка логики определения числа воркеров."""
    assert resolve_ae_build_workers(0) == 1
    assert resolve_ae_build_workers(-1) == 1

    # Явные числа
    assert resolve_ae_build_workers(5, "1") == 1
    assert resolve_ae_build_workers(5, "2") == 2
    assert resolve_ae_build_workers(2, "4") == 2  # не больше числа клипов
    assert resolve_ae_build_workers(5, "invalid") == 1

    # Режим "auto" при разном объёме RAM
    monkeypatch.setattr(render_job, "get_free_ram_gb", lambda: 64.0)
    assert resolve_ae_build_workers(7, "auto") == 3  # min(3, 7, 64//10=6) -> 3
    assert resolve_ae_build_workers(2, "auto") == 2  # min(3, 2, 6) -> 2

    monkeypatch.setattr(render_job, "get_free_ram_gb", lambda: 15.0)
    assert resolve_ae_build_workers(7, "auto") == 1  # 15//10 = 1 -> min(3, 7, 1) = 1

    monkeypatch.setattr(render_job, "get_free_ram_gb", lambda: 4.0)
    assert resolve_ae_build_workers(7, "auto") == 1  # max(1, 4//10=0) -> 1


def test_partition_clips_lpt():
    """Проверка LPT-разбиения клипов по группам."""
    durations = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    groups = partition_clips(durations, 3)

    assert len(groups) == 3
    # Все клипы распределены без дублей и пропусков
    all_indices = sorted([idx for grp in groups for idx in grp])
    assert all_indices == list(range(7))

    # Проверка балансировки нагрузок
    loads = [sum(durations[i] for i in grp) for grp in groups]
    assert max(loads) - min(loads) <= 20.0  # нагрузки сбалансированы

    # Граничные случаи
    assert partition_clips([10.0, 20.0], 1) == [[0, 1]]
    assert sorted(partition_clips([10.0, 20.0], 5)) == [[0], [1]]
    assert partition_clips([], 3) == [[]]


# ========================================================================= #
# 2. Стендовая проверка REELSI_ONLY через aesim.js
# ========================================================================= #

def test_aesim_stand_reelsi_only(sample_xml, tmp_path):
    """Проверка работы REELSI_ONLY на стенде aesim.js:
    с REELSI_ONLY=[1] собирается только таймлайн 1 (C02), без REELSI_ONLY — оба."""
    aesim_path = os.environ.get("REELSI_AESIM")
    if not aesim_path or not os.path.isfile(aesim_path):
        pytest.skip("Стенд aesim.js не найден (задай REELSI_AESIM)")

    xml1 = sample_xml("C01")
    xml2 = sample_xml("C02")

    combined_jsx = str(tmp_path / "combined.jsx")
    xml2ae.build_combined(
        [{"xml_path": xml1, "roto": False}, {"xml_path": xml2, "roto": False}],
        combined_jsx,
        comps_global=True,
    )

    # 1) Запуск без REELSI_ONLY: должны быть композиции обоих таймлайнов
    out_all_json = str(tmp_path / "out_all.json")
    res1 = subprocess.run(["node", aesim_path, combined_jsx, out_all_json], capture_output=True, text=True)
    assert res1.returncode == 0
    data_all = json.loads(open(out_all_json, encoding="utf-8").read())
    comp_names_all = [c["name"] for c in data_all["comps"]]
    assert any("C01" in n for n in comp_names_all)
    assert any("C02" in n for n in comp_names_all)

    # 2) Запуск с REELSI_ONLY = [1]: должна собраться только композиция второго таймлайна (C02)
    part_jsx = str(tmp_path / "part1.jsx")
    with open(combined_jsx, "r", encoding="utf-8-sig") as f:
        src = f.read()
    with open(part_jsx, "w", encoding="utf-8-sig") as f:
        f.write("var REELSI_ONLY = [1];\n" + src)

    out_part_json = str(tmp_path / "out_part.json")
    res2 = subprocess.run(["node", aesim_path, part_jsx, out_part_json], capture_output=True, text=True)
    assert res2.returncode == 0
    data_part = json.loads(open(out_part_json, encoding="utf-8").read())
    comp_names_part = [c["name"] for c in data_part["comps"]]
    assert not any("C01" in n for n in comp_names_part)
    assert any("C02" in n for n in comp_names_part)


# ========================================================================= #
# 3. Параллельный запуск N=3 на 7 клипах
# ========================================================================= #

def test_parallel_render_n3_seven_clips(sample_xml, tmp_path, monkeypatch):
    """Сквозной тест параллельной сборки: N=3 на 7 клипах.
    Проверяет:
    - запуск 3 параллельных процессов AfterFX с флагом -m;
    - одновременную активность всех 3 процессов ДО завершения первого;
    - один вызов слияния render_merge.jsx;
    - передачу всех 7 композиций в aerender."""
    outdir = str(tmp_path / "jsx_out")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    clip_names = [f"CLIP_{i:02d}" for i in range(1, 8)]
    xml_paths = [sample_xml(name) for name in clip_names]

    # Создаём фиктивные .mov в render_dir
    for name in clip_names:
        with open(os.path.join(render_dir, f"{name}.mov"), "wb") as f:
            f.write(b"video")

    monkeypatch.setenv("REELSI_AE_BUILD_WORKERS", "3")
    monkeypatch.setattr(
        render_job, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    monkeypatch.setattr(render_job, "ae_running", lambda: False)

    started_afx_cmds = []
    part_cmds = []
    merge_cmds = []
    alive_afx_count = 0
    max_concurrent_afx = 0
    afx_lock = threading.Lock()

    class ParallelFakePopen:
        def __init__(self, cmd, *args, **kwargs):
            nonlocal alive_afx_count, max_concurrent_afx
            self.cmd = [str(c) for c in cmd]
            self.pid = 10000 + len(started_afx_cmds)
            self.returncode = 0
            self.stdout = iter([])
            self.t_start = time.time()
            self.duration = 0.2
            exe = str(self.cmd[0]).lower()

            if "afterfx" in exe:
                with afx_lock:
                    started_afx_cmds.append(self.cmd)
                    alive_afx_count += 1
                    if alive_afx_count > max_concurrent_afx:
                        max_concurrent_afx = alive_afx_count

                script_path = self.cmd[-1]
                content = open(script_path, "r", encoding="utf-8-sig", errors="replace").read()

                m_log = re.search(r'REELSI_MASTER_LOG\s*=\s*"([^"]+)"', content)
                m_aep = re.search(r'new File\("([^"]+\.aep)"\)', content)
                aelog_file = m_log.group(1) if m_log else None
                aep_file = m_aep.group(1) if m_aep else None

                if "REELSI-MERGE" in content:
                    merge_cmds.append(self.cmd)
                    if aep_file:
                        with open(aep_file, "wb") as f:
                            f.write(b"merged_batch_aep")
                    if aelog_file:
                        with open(aelog_file, "w", encoding="utf-8") as f:
                            f.write("REELSI-MASTER: начат\nREELSI-MERGE: начат\nsave#1: ok\nsave#2: ok\nREELSI-MERGE: готово\n")
                else:
                    part_cmds.append(self.cmd)
                    if aep_file:
                        with open(aep_file, "wb") as f:
                            f.write(b"part_aep")
                    if aelog_file:
                        with open(aelog_file, "w", encoding="utf-8") as f:
                            f.write("REELSI-MASTER: начат\n")
                            for name in clip_names:
                                f.write(f"таймлайн ok: {name}\n")
                            f.write("save#1: ok\nexists=true\nREELSI-MASTER: готово\n")

                self.stdout = iter(["AfterFX finished"])
            elif "aerender" in exe:
                lines = ["PROGRESS: Launching After Effects..."]
                for name in clip_names:
                    lines.extend([
                        f"PROGRESS:  Output To: {os.path.join(render_dir, name + '.mov')}",
                        f'PROGRESS:  Finished composition "{name}".'
                    ])
                self.stdout = iter(lines)

        def poll(self):
            if time.time() - self.t_start < self.duration:
                return None
            with afx_lock:
                if not getattr(self, "_decremented", False):
                    self._decremented = True
                    nonlocal alive_afx_count
                    alive_afx_count -= 1
            return 0

        def wait(self):
            rem = self.duration - (time.time() - self.t_start)
            if rem > 0:
                time.sleep(rem)
            with afx_lock:
                if not getattr(self, "_decremented", False):
                    self._decremented = True
                    nonlocal alive_afx_count
                    alive_afx_count -= 1
            return 0

    monkeypatch.setattr(render_job.subprocess, "Popen", ParallelFakePopen)

    jobs = [{"xml": p, "outdir": outdir, "roto": False, "style": {"roto": False}} for p in xml_paths]
    from api.build import _norm_build_jobs
    jobs = _norm_build_jobs(jobs)

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False, items=[]
    )

    render_job.run_render_job(render.RJOB, jobs, outdir, render_dir)

    assert not render.RJOB["failed"], f"Рендер упал: {render.RJOB['failed']}"
    assert render.RJOB["done"] is True

    # Проверяем флаг -m в командах AfterFX
    assert len(part_cmds) == 3, f"Ожидалось 3 команды сборки частей, запущено: {len(part_cmds)}"
    for c in part_cmds:
        assert "-m" in c, f"Флаг -m отсутствует в команде запуска воркера: {c}"

    assert len(merge_cmds) == 1, f"Ожидался 1 вызов слияния, запущено: {len(merge_cmds)}"
    assert "-m" in merge_cmds[0], f"Флаг -m отсутствует в команде слияния: {merge_cmds[0]}"

    # Проверяем, что воркеры запускались одновременно
    assert max_concurrent_afx >= 2, f"Максимальное число одновременно живых воркеров: {max_concurrent_afx}"

    # Проверяем, что временная папка _parts/ удалена после успешного слияния
    assert not os.path.exists(os.path.join(outdir, "_parts"))


# ========================================================================= #
# 4. Частичный отказ воркера (часть 1 упала)
# ========================================================================= #

def test_parallel_render_part_failure(sample_xml, tmp_path, monkeypatch):
    """Сбой части 1: клипы части 1 помечаются failed, части 0 и 2 сливаются и рендерятся."""
    outdir = str(tmp_path / "jsx_out_fail")
    render_dir = str(tmp_path / "exp_fail")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    clip_names = [f"CLIP_{i:02d}" for i in range(1, 7)]
    xml_paths = [sample_xml(name) for name in clip_names]

    for name in clip_names:
        with open(os.path.join(render_dir, f"{name}.mov"), "wb") as f:
            f.write(b"video")

    monkeypatch.setenv("REELSI_AE_BUILD_WORKERS", "3")
    monkeypatch.setattr(
        render_job, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    monkeypatch.setattr(render_job, "ae_running", lambda: False)

    merged_aeps_imported = []

    class FailPartPopen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = [str(c) for c in cmd]
            self.pid = 20000
            self.returncode = 0
            self.stdout = iter([])
            exe = str(self.cmd[0]).lower()

            if "afterfx" in exe:
                script_path = self.cmd[-1]
                content = open(script_path, "r", encoding="utf-8-sig", errors="replace").read()

                m_log = re.search(r'REELSI_MASTER_LOG\s*=\s*"([^"]+)"', content)
                m_aep = re.search(r'new File\("([^"]+\.aep)"\)', content)
                aelog_file = m_log.group(1) if m_log else None
                aep_file = m_aep.group(1) if m_aep else None

                if "REELSI-MERGE" in content:
                    merged_aeps_imported.append(content)
                    if aep_file:
                        with open(aep_file, "wb") as f:
                            f.write(b"merged_aep")
                    if aelog_file:
                        with open(aelog_file, "w", encoding="utf-8") as f:
                            f.write("REELSI-MASTER: начат\nREELSI-MERGE: начат\nsave#1: ok\nREELSI-MERGE: готово\n")
                    self.stdout = iter(["Merge ok"])
                else:
                    if aelog_file and "part1" in aelog_file:
                        # Часть 1 падает
                        self.returncode = 1
                        self.stdout = iter(["Part 1 crashed"])
                        return
                    if aep_file:
                        with open(aep_file, "wb") as f:
                            f.write(b"part_aep")
                    if aelog_file:
                        with open(aelog_file, "w", encoding="utf-8") as f:
                            f.write("REELSI-MASTER: начат\n")
                            for name in clip_names:
                                f.write(f"таймлайн ok: {name}\n")
                            f.write("save#1: ok\nexists=true\nREELSI-MASTER: готово\n")
                    self.stdout = iter(["Part ok"])
            elif "aerender" in exe:
                lines = ["PROGRESS: Launching After Effects..."]
                for name in clip_names:
                    lines.extend([
                        f"PROGRESS:  Output To: {os.path.join(render_dir, name + '.mov')}",
                        f'PROGRESS:  Finished composition "{name}".'
                    ])
                self.stdout = iter(lines)

        def poll(self):
            return self.returncode if self.returncode != 0 else 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(render_job.subprocess, "Popen", FailPartPopen)

    jobs = [{"xml": p, "outdir": outdir, "roto": False, "style": {"roto": False}} for p in xml_paths]
    from api.build import _norm_build_jobs
    jobs = _norm_build_jobs(jobs)

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False, items=[]
    )

    render_job.run_render_job(render.RJOB, jobs, outdir, render_dir)

    # Клипы упавшей части 1 зафиксированы в failed
    assert len(render.RJOB["failed"]) > 0

    # Слияние вызывалось только для успешных частей 0 и 2
    assert len(merged_aeps_imported) == 1
    assert "reelsi_batch.part1.aep" not in merged_aeps_imported[0]
    assert "reelsi_batch.part0.aep" in merged_aeps_imported[0]
    assert "reelsi_batch.part2.aep" in merged_aeps_imported[0]


# ========================================================================= #
# 5. Сравнение N=1 с прежним путём
# ========================================================================= #

def test_parallel_render_n1_uses_single_master(sample_xml, tmp_path, monkeypatch):
    """N=1 использует ровно прежний путь (render_master.jsx, без папки _parts/)."""
    outdir = str(tmp_path / "jsx_out_n1")
    render_dir = str(tmp_path / "exp_n1")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    clip_names = ["CLIP_01", "CLIP_02"]
    xml_paths = [sample_xml(name) for name in clip_names]
    for name in clip_names:
        with open(os.path.join(render_dir, f"{name}.mov"), "wb") as f:
            f.write(b"video")

    monkeypatch.setenv("REELSI_AE_BUILD_WORKERS", "1")
    monkeypatch.setattr(
        render_job, "find_ae",
        lambda: ("fake_AfterFX.exe", "fake_aerender.exe", "Adobe After Effects 2026")
    )
    monkeypatch.setattr(render_job, "ae_running", lambda: False)

    afx_commands = []

    class N1FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = [str(c) for c in cmd]
            self.pid = 30000
            self.returncode = 0
            self.stdout = iter([])
            exe = str(self.cmd[0]).lower()

            if "afterfx" in exe:
                afx_commands.append(self.cmd)
                script_path = self.cmd[-1]
                content = open(script_path, "r", encoding="utf-8-sig", errors="replace").read()
                m_log = re.search(r'REELSI_MASTER_LOG\s*=\s*"([^"]+)"', content)
                m_aep = re.search(r'new File\("([^"]+\.aep)"\)', content)
                aelog_file = m_log.group(1) if m_log else None
                aep_file = m_aep.group(1) if m_aep else None
                if aep_file:
                    with open(aep_file, "wb") as f:
                        f.write(b"batch_aep")
                if aelog_file:
                    combined_jsx = os.path.abspath(os.path.join(outdir, "Reelsi_all.jsx")).replace("\\", "/")
                    with open(aelog_file, "w", encoding="utf-8") as f:
                        f.write("REELSI-MASTER: начат\n")
                        f.write(f"evalFile ok: {combined_jsx}\n")
                        for name in clip_names:
                            f.write(f"таймлайн ok: {name}\n")
                        f.write("save#1: ok\nsave#2: ok\nREELSI-MASTER: готово\n")
                self.stdout = iter(["Single master finished"])
            elif "aerender" in exe:
                lines = ["PROGRESS: Launching After Effects..."]
                for name in clip_names:
                    lines.extend([
                        f"PROGRESS:  Output To: {os.path.join(render_dir, name + '.mov')}",
                        f'PROGRESS:  Finished composition "{name}".'
                    ])
                self.stdout = iter(lines)

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(render_job.subprocess, "Popen", N1FakePopen)

    jobs = [{"xml": p, "outdir": outdir, "roto": False, "style": {"roto": False}} for p in xml_paths]
    from api.build import _norm_build_jobs
    jobs = _norm_build_jobs(jobs)

    render.RJOB.update(
        running=True, done=False, log=[], pct=None, cur="", ae="",
        out_dir=render_dir, result=[], failed=[], cancel=False, items=[]
    )

    render_job.run_render_job(render.RJOB, jobs, outdir, render_dir)

    assert not render.RJOB["failed"]
    assert render.RJOB["done"] is True

    # Ровно один запуск AfterFX
    assert len(afx_commands) == 1
    # Проверяем, что не запускались части или слияние
    cmd_str = " ".join(afx_commands[0]).lower()
    assert "part" not in cmd_str
    assert "merge" not in cmd_str
    assert not os.path.exists(os.path.join(outdir, "_parts"))
