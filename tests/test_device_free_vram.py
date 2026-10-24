# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свободная видеопамять: nvidia-smi первым, torch — запасным (`core.device.free_vram_mib`).

На Windows (WDDM) `torch.cuda.mem_get_info` отдаёт бюджет СВОЕГО процесса. Замер 2026-10-24:
чужой процесс держит на карте 8.4 ГБ, nvidia-smi — 7465 МиБ свободно, торч — 15111 МиБ.
Видеокарты и torch в тесте нет: torch подменяется модулем, nvidia-smi — функцией.
"""
import sys
import types

import pytest

from core import device, model_service

MIB = 1024 * 1024


class _FakeCuda:
    """Подмена `torch.cuda`: сколько торч «видит свободно» и сколько раз его спросили."""

    def __init__(self, free_mib: int, total_mib: int = 16376, available: bool = True) -> None:
        self.free_mib = free_mib
        self.total_mib = total_mib
        self.available = available
        self.asked = 0

    def is_available(self) -> bool:
        return self.available

    def mem_get_info(self) -> tuple[int, int]:
        self.asked += 1
        return (self.free_mib * MIB, self.total_mib * MIB)


def _install_torch(monkeypatch: pytest.MonkeyPatch, cuda: _FakeCuda) -> None:
    torch = types.ModuleType("torch")
    torch.cuda = cuda  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> _FakeCuda:
    cuda = _FakeCuda(free_mib=15111)     # «свободно» по бюджету торча — для чужих процессов враньё
    _install_torch(monkeypatch, cuda)
    return cuda


def test_nvidia_smi_answer_wins_and_torch_is_not_asked(monkeypatch, fake_torch) -> None:
    """nvidia-smi отдал число — берём его; торч не спрашиваем: его бюджет чужую занятость не видит.

    Мутация: вернуть torch первым — вернётся 15111 вместо 7465, тест покраснеет.
    """
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: 7465)
    assert device.free_vram_mib() == 7465
    assert fake_torch.asked == 0


def test_zero_from_nvidia_smi_is_a_real_answer(monkeypatch, fake_torch) -> None:
    """0 МиБ свободно — ответ nvidia-smi (карта полна), а не повод спросить торч."""
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: 0)
    assert device.free_vram_mib() == 0
    assert fake_torch.asked == 0


def test_torch_is_the_fallback_without_nvidia_smi(monkeypatch, fake_torch) -> None:
    """Нет nvidia-smi (не NVIDIA, урезанная сборка) — запасной путь: торч."""
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: None)
    assert device.free_vram_mib() == 15111
    assert fake_torch.asked == 1


def test_none_when_neither_answers(monkeypatch) -> None:
    """Ни nvidia-smi, ни torch с картой — мера неизвестна: None, а не 0 (потолка нет)."""
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: None)
    cuda = _FakeCuda(free_mib=0, available=False)
    _install_torch(monkeypatch, cuda)
    assert device.free_vram_mib() is None
    assert cuda.asked == 0


def test_none_when_torch_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: None)
    monkeypatch.setitem(sys.modules, "torch", None)   # import даёт ImportError
    assert device.free_vram_mib() is None


def test_vram_fits_sees_the_busy_card(monkeypatch, fake_torch) -> None:
    """Потребитель получает правду: на занятой карте слот не влезает, хотя торч говорит «свободно».

    Слот распознавания — ASR_VRAM_MIB + VRAM_RESERVE_MIB; nvidia-smi отдаёт 1000 МиБ — не влезает.
    """
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: 1000)
    monkeypatch.setattr(model_service, "_service_device", lambda: "cuda")
    assert model_service.vram_fits() is False
    assert fake_torch.asked == 0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Часы замера: тест двигает их сам — окно TTL без реального ожидания."""
    now = [1000.0]
    monkeypatch.setattr(device, "_monotonic", lambda: now[0])
    return now


def test_two_calls_within_ttl_ask_nvidia_smi_once(monkeypatch, clock) -> None:
    """Два вызова в пределах TTL: nvidia-smi спрошен один раз, оба ответа одинаковы.

    Мутация: убрать кэш из `free_vram_mib` — вызовов станет два, тест покраснеет.
    """
    calls = []
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: calls.append(1) or 7465)
    assert device.free_vram_mib() == 7465
    clock[0] += device.FREE_VRAM_TTL_S / 2
    assert device.free_vram_mib() == 7465
    assert len(calls) == 1, "в пределах TTL nvidia-smi спрашивается один раз"


def test_after_ttl_the_measurement_is_repeated(monkeypatch, clock) -> None:
    """По истечении TTL замер повторяется и отдаёт новое значение."""
    values = iter([7465, 1000])
    calls = []
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: calls.append(1) or next(values))
    assert device.free_vram_mib() == 7465
    clock[0] += device.FREE_VRAM_TTL_S
    assert device.free_vram_mib() == 1000
    assert len(calls) == 2


def test_none_result_is_cached_too(monkeypatch, clock) -> None:
    """«Меры нет» (None) тоже кэшируется: карты нет — не перепроверяем на каждом окне."""
    calls = []
    monkeypatch.setattr(device, "_nvidia_smi_free_mib", lambda: calls.append(1))
    monkeypatch.setitem(sys.modules, "torch", None)   # torch не даёт меры
    assert device.free_vram_mib() is None
    clock[0] += device.FREE_VRAM_TTL_S / 2
    assert device.free_vram_mib() is None
    assert len(calls) == 1


def test_cache_starts_empty_in_every_test() -> None:
    """Замер прошлого теста сюда не протекает: фикстура сбрасывает кэш до и после каждого теста.

    Стоит в конце файла нарочно: предыдущие тесты кэш наполняют.
    """
    assert device._FREE_VRAM_CACHE is None
