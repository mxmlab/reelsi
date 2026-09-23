# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание HC: прогресс сборки проекта в режиме «Один на всё» (whole_file=True).

ExtendScript пишет строки «таймлайн ok: <имя>» в журнал мастера после каждого таймлайна,
Python отслеживает их по порядку строк в файле, переводя собранные ролики в stage=built,
текущий в stage=aep, оставшиеся держит в wait. stage_total равен числу роликов, stage_done
шагает от 0 до N.

Запуск: python -m pytest tests/test_hc_combined_build_progress.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402
from core import jobstate, render_job  # noqa: E402


def _reset_job(names):
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    jobstate.items_init(render.RJOB, render.RLOCK, names)


def test_tail_master_log_with_timelines(tmp_path):
    """(a) _tail_master_log с timelines на 3 стема:
    1) журнал REELSI-MASTER: начат + таймлайн ok: A -> вернул 1, built/aep/wait, cur = 2-й стем;
    2) дописали ещё таймлайн ok: A (то же имя) -> вернул 1, built/built/aep;
    3) повторный вызов без новых строк -> 0, стадии те же;
    4) дописали evalFile ok: <общий путь> -> вернул 0, у 3-го стема по-прежнему aep,
       строки «собран:» в логе нет, нормализованный путь в seen.
    """
    stems = ["01_A", "02_B", "03_C"]
    _reset_job(stems)
    for i, s in enumerate(stems):
        jobstate.item_set(render.RJOB, render.RLOCK, s, stage="aep" if i == 0 else "wait")
    render.RJOB["cur"] = stems[0]

    aelog = str(tmp_path / "reelsi_batch.aelog.txt")
    seen = set()
    timelines = {"stems": list(stems), "built": 0}
    combined_path = str(tmp_path / "Reelsi_all.jsx")
    norm_combined = os.path.abspath(combined_path).replace("\\", "/")
    by_jsx = {norm_combined: stems[-1]}

    # 1) REELSI-MASTER: начат + таймлайн ok: A
    with open(aelog, "w", encoding="utf-8") as f:
        f.write("REELSI-MASTER: начат\n")
        f.write("таймлайн ok: A\n")

    res1 = render_job.tail_master_log(render.RJOB, aelog, seen, by_jsx, timelines=timelines)
    assert res1 == 1
    stages1 = [it["stage"] for it in render.RJOB["items"]]
    assert stages1 == ["built", "aep", "wait"]
    assert render.RJOB["cur"] == stems[1]

    # 2) Дописали ещё таймлайн ok: A (то же самое имя композиции)
    with open(aelog, "a", encoding="utf-8") as f:
        f.write("таймлайн ok: A\n")

    res2 = render_job.tail_master_log(render.RJOB, aelog, seen, by_jsx, timelines=timelines)
    assert res2 == 1
    stages2 = [it["stage"] for it in render.RJOB["items"]]
    assert stages2 == ["built", "built", "aep"]
    assert render.RJOB["cur"] == stems[2]

    # 3) Повторный вызов без новых строк -> 0, стадии те же
    res3 = render_job.tail_master_log(render.RJOB, aelog, seen, by_jsx, timelines=timelines)
    assert res3 == 0
    stages3 = [it["stage"] for it in render.RJOB["items"]]
    assert stages3 == ["built", "built", "aep"]
    assert render.RJOB["cur"] == stems[2]

    # 4) Дописали evalFile ok: <общий путь>
    with open(aelog, "a", encoding="utf-8") as f:
        f.write(f"evalFile ok: {combined_path}\n")

    res4 = render_job.tail_master_log(render.RJOB, aelog, seen, by_jsx, timelines=timelines)
    assert res4 == 0
    stages4 = [it["stage"] for it in render.RJOB["items"]]
    assert stages4 == ["built", "built", "aep"]
    assert render.RJOB["cur"] == stems[2]

    # Строки «собран:» в логе быть не должно
    for e in render.RJOB["log"]:
        txt = e if isinstance(e, str) else e.get("t", "")
        assert "собран:" not in txt, f"Найдена нежелательная запись 'собран:': {e}"

    # Нормализованный путь обязан быть в seen
    assert norm_combined in seen


def test_run_proc_master_combined_progress(tmp_path, monkeypatch):
    """(b) _run_proc_master(..., whole_file=True) на 3 роликах с фейковым процессом,
    который дописывает строки «таймлайн ok:» по одной между опросами:
    записанные stage_done не убывают и доходят до 3, stage_total всё время 3,
    pct в пределах [p_jsx_end, p_aep_end].
    """
    outdir = str(tmp_path / "jsx_out")
    render_dir = str(tmp_path / "exp")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(render_dir, exist_ok=True)

    combined_jp = os.path.abspath(os.path.join(outdir, "Reelsi_all.jsx")).replace("\\", "/")
    stems = ["01_C0233", "02_C0234", "03_C0235"]
    good = [
        (stems[0], "C0233", combined_jp, 100),
        (stems[1], "C0234", combined_jp, 100),
        (stems[2], "C0235", combined_jp, 100),
    ]
    aelog = os.path.join(outdir, "reelsi_batch.aelog.txt")
    _reset_job(stems)

    class FakeMasterProcess:
        """Имитирует AfterFX, пишущий строки журнала последовательно между опросами poll()."""
        def __init__(self, *args, **kwargs):
            self.pid = 42424
            self.returncode = 0
            self.stdout = iter([])
            self.step = 0

        def poll(self):
            if self.step == 0:
                with open(aelog, "w", encoding="utf-8") as f:
                    f.write("REELSI-MASTER: начат\n")
                self.step += 1
                return None
            elif self.step == 1:
                with open(aelog, "a", encoding="utf-8") as f:
                    f.write("таймлайн ok: C0233\n")
                self.step += 1
                return None
            elif self.step == 2:
                with open(aelog, "a", encoding="utf-8") as f:
                    f.write("таймлайн ok: C0234\n")
                self.step += 1
                return None
            elif self.step == 3:
                with open(aelog, "a", encoding="utf-8") as f:
                    f.write("таймлайн ok: C0235\n")
                self.step += 1
                return None
            elif self.step == 4:
                with open(aelog, "a", encoding="utf-8") as f:
                    f.write(f"evalFile ok: {combined_jp}\n")
                    f.write("comp ok: C0233\n")
                    f.write("comp ok: C0234\n")
                    f.write("comp ok: C0235\n")
                    f.write("REELSI-MASTER: готово\n")
                self.step += 1
                return None
            else:
                return 0

        def wait(self):
            return 0

    monkeypatch.setattr(render_job.subprocess, "Popen", FakeMasterProcess)

    history = []

    def record_state():
        with render.RLOCK:
            sd = render.RJOB.get("stage_done")
            st = render.RJOB.get("stage_total")
            pct = render.RJOB.get("pct")
            lbl = render.RJOB.get("stage_label")
            if lbl == "сборка проекта":
                history.append((sd, st, pct))

    orig_remit = render.remit

    def hooked_remit(*args, **kwargs):
        orig_remit(*args, **kwargs)
        record_state()

    monkeypatch.setattr(render, "remit", hooked_remit)
    monkeypatch.setattr(render.RJOB, "emit", hooked_remit)
    monkeypatch.setattr(render_job._time, "sleep", lambda *a, **k: record_state())

    p_jsx = 0.20
    p_aep = 0.60
    rc = render_job.run_proc_master(
        render.RJOB,
        "fake_AfterFX.exe", "-noui", "-r", "master.jsx",
        good=good, render_dir=render_dir, aelog_path=aelog,
        p_jsx_end=p_jsx, p_aep_end=p_aep,
        t_aep_base=60.0, t_render_base=60.0,
        has_stats=True, whole_file=True,
    )
    record_state()

    assert rc == 0
    assert history, "Состояния сборки проекта не зафиксированы"

    # 1) stage_total всё время 3
    stage_totals = [st for _sd, st, _pct in history]
    assert all(st == 3 for st in stage_totals), f"stage_total должен быть 3, получено: {stage_totals}"
    assert 1 not in stage_totals, f"значение 1 не должно появляться в stage_total: {stage_totals}"

    # 2) stage_done не убывают и доходят до 3
    stage_dones = [sd for sd, _st, _pct in history if sd is not None]
    assert stage_dones == sorted(stage_dones), f"stage_done убывает: {stage_dones}"
    assert stage_dones[-1] == 3, f"stage_done не дошёл до 3: {stage_dones}"
    # Промежуточные значения 1 и 2 должны присутствовать
    assert 1 in stage_dones and 2 in stage_dones, f"stage_done не зафиксировал шаги 1 и 2: {stage_dones}"

    # 3) pct в пределах [p_jsx_end, p_aep_end]
    pcts = [pct for _sd, _st, pct in history if pct is not None]
    assert all(p_jsx - 1e-6 <= p <= p_aep + 1e-6 for p in pcts), (
        f"pct вышел за границы [{p_jsx}, {p_aep}]: {pcts}"
    )
