# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Чтение кадров из трубы ffmpeg: одна читалка `roto.iter_frames` и сторож у поиска лиц.

Баг, из-за которого это написано: `face_crops` (рамка подкаста, кадры для рта) запускал
ffmpeg без таймаута и без отмены. Зависший ffmpeg вешал перевод в рилсы, и кнопка «Стоп»
его не останавливала. Теперь у `face_crops` есть простой (`stall_sec`) и отмена (`cancel`).

Подменяем только ffmpeg: команда `ffmpeg …` уходит в поддельный процесс `sys.executable -c`,
ffprobe (`roto._probe`) отдаёт фиксированные размеры. Остальное — настоящий код.
"""
import io
import subprocess
import sys
import threading
import time

import pytest

from core import podcast_reframe as pr
from core import roto
from core.xml2ae.parse import Cancelled

# Поддельный ffmpeg: молчит (ни кадра, ни выхода) дольше любого порога в тесте.
SILENT = "import time\ntime.sleep(20)\n"
HEADS = [[0.5, 0.3, 0.05]]


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr(roto, "_probe", lambda src: (1920, 1080, 10.0, "10"))
    real = subprocess.Popen
    procs = []

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "ffmpeg":
            cmd = [sys.executable, "-c", SILENT]
        p = real(cmd, *a, **k)
        procs.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", fake)
    return procs


def test_молчащий_ffmpeg_снимается_по_простою(fake_ffmpeg):
    """Порог простоя 1 с: face_crops кончается за порог + 3 с, а не ждёт 20 с сна."""
    t0 = time.time()
    with pytest.raises(RuntimeError, match="молчит"):
        pr.face_crops("не-важно.mp4", 0.0, 1.0, HEADS, stall_sec=1.0)
    dt = time.time() - t0
    assert dt <= 1.0 + 3, dt
    assert all(p.poll() is not None for p in fake_ffmpeg), "ffmpeg не снят"


def test_отмена_прерывает_чтение_кадров_за_три_секунды(fake_ffmpeg):
    """Кнопка «Стоп» (cancel) во время молчащего ffmpeg: Cancelled за ≤ 3 с после нажатия."""
    flag = {"on": False}
    t_cancel = []

    def press_stop():
        t_cancel.append(time.time())
        flag["on"] = True

    timer = threading.Timer(1.0, press_stop)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            pr.face_crops("не-важно.mp4", 0.0, 1.0, HEADS,
                          cancel=lambda: flag["on"], stall_sec=60)
    finally:
        timer.cancel()
    assert t_cancel, "отмена не нажата"
    assert time.time() - t_cancel[0] <= 3, "Cancelled позже 3 с после отмены"
    assert all(p.poll() is not None for p in fake_ffmpeg), "ffmpeg не снят"


def test_iter_frames_пачки_и_короткий_хвост():
    """Контракт читалки: полные кадры пачками по chunk, неполный хвост отбрасывается, конец
    потока (b'' от _read_exact) заканчивает генератор."""
    data = b"".join(bytes([i]) * 4 for i in range(5)) + b"xy"
    batches = list(roto.iter_frames(io.BytesIO(data), 4, 2))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert b"".join(b"".join(b) for b in batches) == data[:20]
    assert list(roto.iter_frames(io.BytesIO(b""), 4, 2)) == []
