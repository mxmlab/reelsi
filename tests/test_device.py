# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выбор устройства: cuda -> mps -> cpu.

Ветки mps и cpu на этой машине не выполняются никогда — NVIDIA находится первой.
Поэтому они и проверяются подделкой torch: иначе ошибка в них всплывёт только на
чужом маке, у человека, который не сможет её диагностировать.

Отдельно стережём faster-whisper: CTranslate2 не поддерживает Metal и ROCm вовсе,
и отдать ему "mps" — гарантированное падение. Разбор — docs/PLATFORMS.md.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import device


class _FakeBackendsMPS:
    def __init__(self, available):
        self._a = available

    def is_available(self):
        return self._a


def _fake_torch(cuda, mps):
    """torch с нужным набором железа. Ровно те атрибуты, которые читает device.py."""
    t = types.SimpleNamespace()
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    t.backends = types.SimpleNamespace(mps=_FakeBackendsMPS(mps))
    return t


@pytest.fixture
def fake_torch(monkeypatch):
    def _install(cuda=False, mps=False):
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda, mps))
    return _install


def test_cuda_wins_when_present(fake_torch):
    """NVIDIA есть — берём её, до mps дело не доходит. Это поведение «как было»."""
    fake_torch(cuda=True, mps=True)
    assert device.pick_device() == "cuda"


def test_mps_used_when_no_cuda(fake_torch):
    """Мак: раньше здесь молча получался cpu, хотя Metal справился бы."""
    fake_torch(cuda=False, mps=True)
    assert device.pick_device() == "mps"


def test_cpu_when_nothing_available(fake_torch):
    fake_torch(cuda=False, mps=False)
    assert device.pick_device() == "cpu"


def test_no_torch_at_all_is_not_a_crash(monkeypatch):
    """torch может быть не установлен (или сломан) — выбор устройства не место,
    где это должно ронять процесс."""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert device.pick_device() == "cpu"


def test_explicit_choice_beats_autodetect(fake_torch):
    """Явный выбор юзера (флаг или env) важнее того, что нашлось само."""
    fake_torch(cuda=True, mps=True)
    assert device.pick_device("cpu") == "cpu"
    assert device.pick_device("mps") == "mps"


def test_garbage_falls_back_to_autodetect(fake_torch):
    """Мусор в env не должен уехать в torch как имя устройства."""
    fake_torch(cuda=True, mps=False)
    assert device.pick_device("нет такого") == "cuda"
    assert device.pick_device("") == "cuda"
    assert device.pick_device(None) == "cuda"


# --------------------------------------------------------------------------- #
# faster-whisper: только cuda или cpu, mps не существует
# --------------------------------------------------------------------------- #
def test_ct2_never_returns_mps(fake_torch):
    """Главный тест файла. CTranslate2 не умеет Metal: на маке обязан быть cpu+int8,
    иначе faster-whisper падает при загрузке модели."""
    fake_torch(cuda=False, mps=True)
    dev, ct = device.ct2_device()
    assert dev == "cpu", "CTranslate2 отдали mps — он такого устройства не знает"
    assert ct == "int8", "float16 на CPU CTranslate2 не считает"


def test_ct2_keeps_cuda_fp16(fake_torch):
    """На NVIDIA — ровно как раньше."""
    fake_torch(cuda=True, mps=False)
    assert device.ct2_device() == ("cuda", "float16")


def test_ct2_respects_explicit_compute_type(fake_torch):
    """Явно заданный compute_type не перебиваем."""
    fake_torch(cuda=True, mps=False)
    assert device.ct2_device("cuda", "int8_float16") == ("cuda", "int8_float16")


def test_fp16_only_on_cuda():
    """fp16 на MPS не включаем: проверить на живом железе нечем, а тихо испорченная
    альфа-маска рото видна только рендером в AE.
    torch может быть не установлен (установка без GPU, CI) — тогда пропускаем:
    голый import ронял весь набор, и в отчёте это выглядело как красный тест."""
    torch = pytest.importorskip("torch")
    assert device.autocast_dtype("cuda") is torch.float16
    assert device.autocast_dtype("mps") is torch.float32
    assert device.autocast_dtype("cpu") is torch.float32


def test_mps_fallback_env_set_on_mac(monkeypatch):
    """На маке PYTORCH_ENABLE_MPS_FALLBACK обязан быть выставлен ДО импорта torch:
    без него инференс падает на первой операции, которой нет в MPS."""
    import importlib
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    importlib.reload(device)
    assert os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    importlib.reload(device)
