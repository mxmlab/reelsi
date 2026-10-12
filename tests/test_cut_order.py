# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Порядок клипов параллельной нарезки на сервере (/api/status отдаёт порядок очереди).

Ролики идут параллельно (cut_parallel_width), и results копит их в порядке ГОТОВНОСТИ.
Клиент вставляет клипы по номеру очереди, поэтому сервер обязан отдать его параллельным
массивом result_order: для каждого пути из results — его индекс в очереди прогона
(items_init — в порядке набора). Здесь настоящий прогон run_omnicut_job с пулом из трёх
роликов, подменён только движок (fake omni_cut): ролики готовы в порядке 3, 1, 2.

1. В статусе results — в порядке готовности, result_order — [2, 0, 1] (qi = 2,0,1).
2. Путь, которого нет в очереди (например, черновик), получает null, а не чужой номер.

Запуск: python -m pytest tests/test_cut_order.py -q -p no:cacheprovider
"""
import os
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

HDR = {"Host": "127.0.0.1:5001"}

# Ролики 01 (индекс 0) и 02 (индекс 1) стартуют раньше, но работают дольше, чем 03 (индекс 2):
# готовность идёт 03, 01, 02 — то есть индексы очереди 2, 0, 1.
_CHILD = textwrap.dedent("""\
import os, re, sys, time
args = sys.argv[1:]
out_path = args[args.index("--out") + 1]
num = int(re.match(r"(\\d+)_", os.path.basename(out_path)).group(1))
time.sleep({1: 0.6, 2: 1.2, 3: 0.1}[num])
with open(out_path, "w", encoding="utf-8") as f:
    f.write("<xml/>")
print("done", flush=True)
""")


@pytest.fixture
def client():
    from webui import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_parallel_results_carry_queue_order(monkeypatch: pytest.MonkeyPatch, tmp_path, client) -> None:
    import api.jobs as jobs
    from api._core import JOB, LOCK
    from core import aicut

    script = str(tmp_path / "fake_omni.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_CHILD)
    monkeypatch.setattr(jobs, "module_cmd",
                        lambda mod, *args: [sys.executable, script] + list(args))
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 3)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)

    with LOCK:
        JOB.update(cancel=False, running=True, done=False, results=[], failed=[],
                   items=[], log=[], log_base=0)

    outdir = str(tmp_path / "out")
    pairs = [[f"cam_{i}.mp4"] for i in range(3)]
    try:
        jobs.run_omnicut_job(outdir, pairs, stages={"draft": False})
        d = client.get("/api/status", headers=HDR).get_json()
    finally:
        with LOCK:
            JOB.update(running=False, done=True)

    names = [os.path.basename(p) for p in d["results"]]
    assert names == ["03_cam_2.xml", "01_cam_0.xml", "02_cam_1.xml"], (
        "results должен идти в порядке готовности, а не очереди: %r" % names)
    assert d["result_order"] == [2, 0, 1], (
        "result_order не восстановил порядок очереди: %r" % d["result_order"])
    assert len(d["result_order"]) == len(d["results"])


def test_result_not_in_queue_gets_null(client) -> None:
    """Черновик (и любой путь вне очереди) не получает чужой номер: null."""
    from api._core import JOB, LOCK

    with LOCK:
        JOB.update(running=False, done=True, cancel=False, failed=[],
                   items=[{"name": "01_a", "stage": "done", "pct": None,
                           "path": os.path.join("out", "01_a.xml"), "reason": ""}],
                   results=[os.path.join("out", "01_a.xml"), os.path.join("out", "draft.mp4")],
                   log=[], log_base=0)
    try:
        d = client.get("/api/status", headers=HDR).get_json()
    finally:
        with LOCK:
            JOB.update(results=[], items=[])
    assert d["result_order"] == [0, None]
