# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задания IX: сторож rclone на реальном выводе (api/gdrive.py).

Проверяется:
1. Разбор `Checks: i / n` как прогресса (отдельные поля `ci`/`cn`), сохранение
   контракта `_stats_fields` и `_is_noise`.
2. Активность по словарю `last[k]`, а не кортежу строки:
   (а) значения заморожены → процесс снят по таймауту простоя (~1 с), пока вывод идёт;
   (б) растут байты → процесс жив;
   (в) байты на 100%, растёт `Checks` → процесс жив;
   (г) растёт только номер файла → процесс жив.
"""
import os
import sys
import time


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.gdrive as gdrive  # noqa: E402
from api.gdrive import (  # noqa: E402
    _is_noise, _run_rclone, _stats_fields,
)

try:
    from api.gdrive import _checks_fields
except ImportError:
    _checks_fields = None


class _MockStreamingProc:
    """Подставной процесс rclone, генерирующий блоки вывода во времени."""

    def __init__(self, block_fn, max_duration=2.5, step=0.04):
        self.pid = 99995
        self._killed = False
        self._done = False
        self._block_fn = block_fn
        self._max_duration = max_duration
        self._step = step
        self.stdout = self._stream()

    def _stream(self):
        t_end = time.time() + self._max_duration
        idx = 0
        while time.time() < t_end and not self._killed:
            block = self._block_fn(idx)
            idx += 1
            for line in block:
                if self._killed:
                    return
                yield line
                time.sleep(self._step)
        self._done = True

    def poll(self):
        if self._killed:
            return gdrive._RCLONE_STALLED
        if self._done:
            return 0
        return None

    def wait(self):
        return gdrive._RCLONE_STALLED if self._killed else 0

    def kill(self):
        self._killed = True


def test_checks_fields_parsing():
    """Checks: i / n разбирается как прогресс (ci, cn), в _stats_fields не попадает, _is_noise True."""
    assert _checks_fields is not None, "функция _checks_fields должна быть определена в api/gdrive.py"
    assert _checks_fields("Checks:                 2 / 2, 100%") == {"ci": 2, "cn": 2}
    assert _checks_fields("Checks: 0 / 1, 0%") == {"ci": 0, "cn": 1}
    assert _checks_fields("Checks:        15 / 30") == {"ci": 15, "cn": 30}
    assert _checks_fields("Transferred:   0.5 GiB / 1.0 GiB, 50%") is None
    assert _checks_fields("Elapsed time:  45.0s") is None
    # Контракты прежних функций не нарушены
    assert _stats_fields("Checks:                 2 / 2, 100%") is None
    assert _is_noise("Checks:                 2 / 2, 100%") is True


def test_watchdog_kills_frozen_progress_while_output_continues(monkeypatch):
    """(а) Значения заморожены → снят за порог (~1 с), ПОКА вывод идёт.

    Блок rclone -v --stats 2s печатается непрерывно. На старом коде кортежи строк
    разной формы (байты, файлы, текущий файл) чередовались и сбрасывали сторож.
    """
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 1.0)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: p.kill())

    def frozen_block(idx):
        return [
            "Transferred:   0.512 GiB / 1.234 GiB, 41%, 12.5 MiB/s, ETA 1m2s\n",
            "Transferred:            2 / 5, 40%\n",
            "Checks:                 2 / 2, 100%\n",
            "Elapsed time:        45.0s\n",
            "Transferring:\n",
            " *  IMG_6753.MOV: 41% /1.234Gi, 12.345Mi/s, 1m2s\n",
        ]

    proc = _MockStreamingProc(frozen_block, max_duration=2.5, step=0.04)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: proc)

    t0 = time.time()
    rc = _run_rclone(["rclone"], lambda l: None)
    elapsed = time.time() - t0

    assert rc == gdrive._RCLONE_STALLED
    assert proc._killed is True
    assert 0.9 <= elapsed < 2.0


def test_watchdog_keeps_alive_when_bytes_grow(monkeypatch):
    """(б) Растут байты → процесс жив и успешно завершается."""
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 1.0)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: p.kill())

    def growing_bytes_block(idx):
        mb = min(10 + idx * 20, 100)
        pct = min(10 + idx * 20, 100)
        return [
            f"Transferred:   {mb} MiB / 100 MiB, {pct}%, 10 MiB/s, ETA 1m\n",
            "Transferred:            1 / 5, 20%\n",
            "Checks:                 0 / 2, 0%\n",
            "Elapsed time:        10.0s\n",
            "Transferring:\n",
            f" *  IMG_6753.MOV: {pct}% /100Mi, 10Mi/s, 1m\n",
        ]

    proc = _MockStreamingProc(growing_bytes_block, max_duration=1.4, step=0.05)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: proc)

    rc = _run_rclone(["rclone"], lambda l: None)

    assert rc == 0
    assert proc._killed is False


def test_watchdog_keeps_alive_when_bytes_100_pct_and_checks_grow(monkeypatch):
    """(в) Байты на 100%, растёт Checks → процесс жив.

    На старом коде Checks отбрасывался как шум, и при байтах 100% процесс снимался
    через порог простоя как зависший.
    """
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 1.0)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: p.kill())

    def growing_checks_block(idx):
        ci = min(1 + idx * 2, 10)
        pct = ci * 10
        return [
            "Transferred:   100 MiB / 100 MiB, 100%, 0 B/s, ETA -\n",
            f"Checks:                 {ci} / 10, {pct}%\n",
            "Elapsed time:        45.0s\n",
        ]

    proc = _MockStreamingProc(growing_checks_block, max_duration=1.4, step=0.05)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: proc)

    rc = _run_rclone(["rclone"], lambda l: None)

    assert rc == 0
    assert proc._killed is False


def test_watchdog_keeps_alive_when_only_file_number_grows(monkeypatch):
    """(г) Растёт только номер файла → процесс жив."""
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 1.0)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: p.kill())

    def growing_files_block(idx):
        fi = min(1 + idx, 5)
        return [
            "Transferred:   10 MiB / 10 MiB, 100%, 0 B/s, ETA -\n",
            f"Transferred:            {fi} / 5, {fi * 20}%\n",
            "Checks:                 2 / 2, 100%\n",
            "Elapsed time:        20.0s\n",
        ]

    proc = _MockStreamingProc(growing_files_block, max_duration=1.4, step=0.05)
    monkeypatch.setattr(gdrive.subprocess, "Popen", lambda *a, **k: proc)

    rc = _run_rclone(["rclone"], lambda l: None)

    assert rc == 0
    assert proc._killed is False
