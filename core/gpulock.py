# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Межпроцессный замок видеокарты: GPU-участки нарезки идут строго по одному.

Параллельная ИИ-нарезка запускает K подпроцессов одновременно, но переполнение
VRAM на Windows не даёт честной ошибки, а вешает ПК. GPU нужен в двух местах
(распознавание и вздохи), между ними — облачный вызов решения. Замок держим
ТОЛЬКО на GPU-участке: облачные вызовы идут одновременно.

Карту занимает не «всё, что связано с видео»: NVENC и CUDA-модели — да, а Quick
Sync (Intel), AMF (AMD) и процессорные x264/x265 — нет. Поэтому у участка,
который кодирует выбранным кодеком, своя дверь — `codec_gpu_lock`: она берёт
замок только под NVIDIA, а остальные кодеки не ждут чужую модель зря.

Путь лок-файла — ``JOB_LOCK_PATH + ".gpu"``: он рядом с общим локом джоба,
подпроцессы получают тот же ``REELSI_JOB_LOCK`` через ``child_env()``, изолированные
профили изолированы автоматически.
"""
from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Callable, Generator

from core import encoders
from core.app_meta import console_emit
from core.jobstate import JOB_LOCK_PATH


def _gpu_lock_path() -> str:
    """Путь к файлу замка GPU: рядом с локом джоба."""
    return JOB_LOCK_PATH + ".gpu"


@contextlib.contextmanager
def gpu_lock(
    label: str = "",
    emit: Callable[..., Any] = console_emit,
) -> Generator[None, None, None]:
    """Эксклюзивный межпроцессный замок GPU — для GPU-участков нарезки.

    Блокирующее взятие файлового замка: не взялся → ``time.sleep(0.5)`` и снова.
    Если ждём дольше 1 с — один раз печатаем предупреждение в лог. Не реентерабелен.
    """
    path = _gpu_lock_path()
    fh = None
    waited = 0.0
    warned = False
    try:
        while True:
            try:
                fh = open(path, "a+b")
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    getattr(msvcrt, "locking")(fh.fileno(),
                                               getattr(msvcrt, "LK_NBLCK"), 1)
                else:
                    import fcntl
                    getattr(fcntl, "flock")(
                        fh.fileno(),
                        getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
                    )
                break  # замок взят
            except Exception:
                # Не удалось — файл занят другим процессом
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass  # закрыть не удалось — замок не взят, дескриптор освободит GC
                    fh = None
                time.sleep(0.5)
                waited += 0.5
                if waited > 1.0 and not warned:
                    warned = True
                    emit("  ⏳ жду видеокарту ({label}) — её занял другой ролик…",
                         label=label)
        yield
    finally:
        if fh is not None:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    getattr(msvcrt, "locking")(fh.fileno(),
                                               getattr(msvcrt, "LK_UNLCK"), 1)
                else:
                    import fcntl
                    getattr(fcntl, "flock")(fh.fileno(),
                                           getattr(fcntl, "LOCK_UN"))
            except Exception:
                pass  # замок уже снят (файл закрыт/процесс упал) — снимать нечего
            try:
                fh.close()
            except Exception:
                pass  # закрыть не удалось — замок уже снят, дескриптор освободит GC


@contextlib.contextmanager
def codec_gpu_lock(
    codec: str,
    label: str = "",
    emit: Callable[..., Any] = console_emit,
) -> Generator[None, None, None]:
    """Замок видеокарты — только для участка, который кодирует на NVIDIA.

    `codec` — имя кодека (`h264_nvenc`) или семейство (`nvidia`). Кодирование Quick
    Sync, AMF, VideoToolbox и процессором карту NVIDIA не занимает вовсе: держать его
    за замком значило ждать чужой RoFormer или распознавание без всякой причины
    (жалоба владельца: «выбран другой кодек, а он ждёт и не делает»). NVENC карту
    занимает — замер на этой машине: сессия hevc_nvenc держит ~348 МиБ, — поэтому
    NVENC-участки замок берут по-прежнему: он бережёт VRAM, а на Windows переполнение
    VRAM не даёт честной ошибки, а вешает машину.
    """
    if not encoders.uses_nvidia(codec):
        # Intel/AMD/Apple и процессор: своё железо, замок ни при чём — не берём вовсе,
        # чтобы не ждать чужую CUDA-модель (и не тратить на это полсекунды опроса).
        yield
        return
    with gpu_lock(label, emit):
        yield
