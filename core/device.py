# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выбор вычислительного устройства: cuda -> mps -> cpu.

До этого каждый модуль решал сам, и решал одинаково: `"cuda" if
torch.cuda.is_available() else "cpu"`. На маке это означало «всё на CPU» даже там,
где Metal справился бы, — не потому что нельзя, а потому что вариант mps никто не
написал (разбор по платформам — `docs/PLATFORMS.md`).

ВАЖНО: ни одна ветка кроме cuda/cpu на живом железе НЕ ПРОВЕРЕНА — Apple Silicon и
Radeon у нас нет. На Windows с NVIDIA поведение ровно прежнее: cuda находится первой,
до mps дело не доходит.

ROCm (AMD) отдельной ветки не требует: сборка PyTorch под ROCm выдаёт себя за
`torch.cuda` — код остаётся тем же, меняется только колесо при установке.
"""
import os
import platform
from typing import Any
from core.umsg import ReelsiError

# MPS покрывает не все операции, и без этой переменной инференс падает на первой же
# неподдержанной вместо того, чтобы досчитать её на CPU. Ставим ДО импорта torch —
# позже он её уже не перечитает. setdefault: если юзер выставил осознанно, не спорим.
if platform.system() == "Darwin":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

VALID = ("cuda", "mps", "cpu")


def pick_device(force: str | None = None) -> str:
    """Явный выбор (аргумент или env) приоритетнее авто. Всегда возвращает строку."""
    d = (force or "").strip().lower()
    if d in VALID:
        return d
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except ReelsiError: raise
    except Exception:
        pass  # torch недоступен/без GPU — работаем на CPU
    return "cpu"


def autocast_dtype(dev: str) -> Any:
    """fp16 — только там, где он проверенно быстрее и стабильнее.

    На CUDA половинная точность даёт 2x скорость и вдвое меньше памяти. На MPS fp16
    поддержан, но у нас нет железа, чтобы убедиться, что RVM на нём не сыплет NaN, —
    а тихо испорченная альфа-маска видна только рендером в AE. Пока честный fp32:
    медленнее, зато предсказуемо.
    """
    import torch
    return torch.float16 if dev == "cuda" else torch.float32


def ct2_device(device: str | None = None, compute_type: str | None = None) -> tuple[str, str]:
    """(device, compute_type) для faster-whisper. Отдельно от pick_device — НЕ описка.

    faster-whisper работает на CTranslate2, а он не поддерживает ни Metal, ни ROCm:
    на маке это не «медленнее», этого просто нет. Отдать ему `mps` — гарантированное
    падение, поэтому здесь ровно два исхода: CUDA или CPU. На CPU float16 тоже не
    вариант (CTranslate2 его там не считает) — берём int8, он для CPU и сделан.

    Быстрая транскрипция на Mac и AMD решается только сменой движка на whisper.cpp —
    см. `docs/PLATFORMS.md`.
    """
    if device:
        return device, (compute_type or ("float16" if device == "cuda" else "int8"))
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", (compute_type or "float16")
    except ReelsiError: raise
    except Exception:
        pass  # torch недоступен/без GPU — работаем на CPU
    return "cpu", (compute_type or "int8")


def empty_cache(dev: str | None = None) -> None:
    """Отдать память обратно. На Windows переполнение VRAM не даёт честный OOM —
    оно вешает машину целиком, поэтому кэш чистим явно, а не надеемся на GC."""
    try:
        import torch
        if dev in (None, "cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()
        if dev in (None, "mps"):
            mps = getattr(torch, "mps", None)
            if mps is not None and getattr(torch.backends, "mps", None) and \
                    torch.backends.mps.is_available():
                mps.empty_cache()
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU недоступны — чистить нечего
