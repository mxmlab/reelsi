# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты сторожа сигналов: проверка запрета сигналов в системные цели, себя и свои группы."""
import os
import signal
import subprocess
import sys
from typing import Any, Callable

import pytest

from tests import conftest


def _os_killpg(pgid: int, sig: int) -> None:
    """Вызов os.killpg через getattr для совместимости mypy на Windows и POSIX."""
    killpg_fn: Callable[[int, int], None] = getattr(os, "killpg")
    killpg_fn(pgid, sig)


@pytest.fixture
def record_kill(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Подменяет реальную функцию os.kill записывающей заглушкой под обёрткой сторожа."""
    calls: list[tuple[int, int]] = []
    fake: Callable[[int, int], Any] = lambda pid, sig: calls.append((pid, sig))
    monkeypatch.setattr(conftest, "_real_os_kill", fake)
    monkeypatch.setattr(os.kill, "_real", fake, raising=False)
    return calls


@pytest.fixture
def record_killpg(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Подменяет реальную функцию os.killpg записывающей заглушкой под обёрткой сторожа."""
    calls: list[tuple[int, int]] = []
    fake: Callable[[int, int], Any] = lambda pgid, sig: calls.append((pgid, sig))
    monkeypatch.setattr(conftest, "_real_os_killpg", fake)
    killpg_target = getattr(os, "killpg", None)
    if killpg_target is not None:
        monkeypatch.setattr(killpg_target, "_real", fake, raising=False)
    return calls


def test_kill_pid_1_rejected(record_kill: list[tuple[int, int]]) -> None:
    """kill(1, ...) отклоняется сторожем и не доходит до системного вызова."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в pid=1"):
        os.kill(1, signal.SIGTERM)

    assert record_kill == [], "Сигнал в pid=1 не должен доходить до реальной функции"


def test_kill_pid_0_rejected(record_kill: list[tuple[int, int]]) -> None:
    """kill(0, ...) отклоняется сторожем (текущая группа процессов)."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в pid=0"):
        os.kill(0, signal.SIGTERM)

    assert record_kill == [], "Сигнал в pid=0 не должен доходить до реальной функции"


def test_kill_negative_pid_rejected(record_kill: list[tuple[int, int]]) -> None:
    """kill(-pid, ...) с отрицательным pid отклоняется сторожем."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в отрицательный pid=-5"):
        os.kill(-5, signal.SIGTERM)

    assert record_kill == [], "Сигнал в отрицательный pid не должен доходить до реальной функции"


def test_kill_self_pid_rejected_without_marker(record_kill: list[tuple[int, int]]) -> None:
    """kill(свой pid, ...) без маркера allow_self_signal отклоняется сторожем."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в собственный процесс"):
        os.kill(os.getpid(), signal.SIGTERM)

    assert record_kill == [], "Сигнал в свой pid без маркера не должен доходить до реальной функции"


@pytest.mark.allow_self_signal
def test_kill_self_pid_allowed_with_marker(record_kill: list[tuple[int, int]]) -> None:
    """kill(свой pid, ...) с маркером allow_self_signal разрешён фикстурой."""
    os.kill(os.getpid(), signal.SIGTERM)

    assert record_kill == [(os.getpid(), signal.SIGTERM)], "Сигнал в свой pid с маркером должен вызываться"


@pytest.mark.allow_self_signal
def test_kill_pid_1_rejected_even_with_allow_self_signal(record_kill: list[tuple[int, int]]) -> None:
    """Маркер allow_self_signal не разрешает слать сигналы в pid=1."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в pid=1"):
        os.kill(1, signal.SIGTERM)

    assert record_kill == []


def test_kill_signal_zero_allowed_for_self_and_pid_1(record_kill: list[tuple[int, int]]) -> None:
    """kill(..., 0) пропускается сторожем (проверка существования процесса, сигнал не доставляется)."""
    os.kill(os.getpid(), 0)
    os.kill(1, 0)

    assert record_kill == [(os.getpid(), 0), (1, 0)], (
        "Сигнал 0 должен беспрепятственно доходить до реальной функции для своего pid и pid=1"
    )


def test_killpg_1_rejected(record_killpg: list[tuple[int, int]]) -> None:
    """killpg(1, ...) отклоняется сторожем (системная/init группа)."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в группу pgid=1"):
        _os_killpg(1, signal.SIGTERM)

    assert record_killpg == [], "Сигнал в pgid=1 не должен доходить до реальной функции"


def test_killpg_0_rejected(record_killpg: list[tuple[int, int]]) -> None:
    """killpg(0, ...) отклоняется сторожем (текущая группа процессов)."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в группу pgid=0"):
        _os_killpg(0, signal.SIGTERM)

    assert record_killpg == [], "Сигнал в pgid=0 не должен доходить до реальной функции"


def test_killpg_negative_pgid_rejected(record_killpg: list[tuple[int, int]]) -> None:
    """killpg(-pgid, ...) с отрицательной группой процессов отклоняется сторожем."""
    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в отрицательную группу pgid=-2"):
        _os_killpg(-2, signal.SIGTERM)

    assert record_killpg == [], "Сигнал в отрицательный pgid не должен доходить до реальной функции"


def test_killpg_own_group_rejected(monkeypatch: pytest.MonkeyPatch, record_killpg: list[tuple[int, int]]) -> None:
    """killpg(своя группа, ...) отклоняется сторожем."""
    monkeypatch.setattr(os, "getpgrp", lambda: 4242, raising=False)

    with pytest.raises(RuntimeError, match="запрещена отправка сигнала .* в свою группу процессов pgid=4242"):
        _os_killpg(4242, signal.SIGTERM)

    assert record_killpg == [], "Сигнал в свою группу процессов не должен доходить до реальной функции"


def test_kill_child_process_allowed() -> None:
    """kill(pid дочернего процесса, запущенного самим тестом) — проходит успешно."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert proc.poll() is None, "Дочерний процесс должен быть запущен"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.poll() is not None, "Дочерний процесс должен завершиться после os.kill"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
