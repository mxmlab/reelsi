# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общая запись и срез лога джобов (api/_core.py: job_log_add / job_log_since).

Потолок LOG_CAP и сдвиг log_base одинаковы у всех джобов — тест стережёт, что
?since= после обрезки лога отдаёт те же строки, что и до общей функции."""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from api._core import LOG_CAP, job_log_add, job_log_since  # noqa: E402


def test_потолок_и_сдвиг_log_base():
    state = {"log": [], "log_base": 0}
    lock = threading.Lock()
    for i in range(LOG_CAP + 5):
        job_log_add(state, lock, f"строка {i}")
    assert len(state["log"]) == LOG_CAP
    assert state["log_base"] == 5
    rows, total = job_log_since(state, 0)
    assert total == LOG_CAP + 5
    assert rows[0] == "строка 5"          # старые срезаны, глобальный номер сохранён
    rows, total = job_log_since(state, LOG_CAP + 4)
    assert rows == [f"строка {LOG_CAP + 4}"] and total == LOG_CAP + 5


def test_переменные_дают_запись_log_entry_без_переменных_строка():
    state = {"log": [], "log_base": 0}
    lock = threading.Lock()
    job_log_add(state, lock, "шаг {n}", {"n": 2})
    job_log_add(state, lock, "простая строка")
    assert state["log"] == [{"t": "шаг {n}", "v": {"n": 2}}, "простая строка"]
