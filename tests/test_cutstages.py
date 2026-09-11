# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты модуля cutstages: единый список ступеней и нормализация (задание GE).

ПОЧЕМУ этот тест существует:
Список ступеней нарезки должен быть единственным источником правды.
Тест проверяет:
- normalize({}) возвращает дефолты и ветку gigaam;
- pauses="loud" переключает ветку на vad и гасит ступени sense, refine, breath;
- включённые breath, dedupe или sense принудительно зажигают asr;
- при pauses="off" и всех снятых ступенях asr=False;
- ступени, недоступные в выбранной ветке, отсутствуют в нормализованном словаре;
- нормализация идемпотентна: normalize(normalize(x)[0]) == normalize(x).
"""
import pytest
from core import cutstages


def test_normalize_empty_returns_defaults_and_gigaam():
    """normalize({}) возвращает дефолты и ветку gigaam."""
    stages, branch = cutstages.normalize({})
    assert branch == "gigaam"
    assert stages == {
        "pauses": "speech",
        "asr": True,
        "sense": True,
        "dedupe": False,
        "refine": True,
        "breath": True,
        "draft": False,
    }


def test_normalize_loud_switches_to_vad_and_drops_gigaam_stages():
    """pauses='loud' даёт ветку vad и исключает sense, refine, breath."""
    stages, branch = cutstages.normalize({"pauses": "loud"})
    assert branch == "vad"
    assert "sense" not in stages
    assert "refine" not in stages
    assert "breath" not in stages
    assert "pauses" in stages
    assert "asr" in stages
    assert "dedupe" in stages
    assert "draft" in stages
    assert stages["pauses"] == "loud"


def test_asr_computed_from_needs_and_pauses():
    """Проверка вычисления флага asr:
    - pauses='speech' зажигает asr даже при всех снятых булевых флагах;
    - включённые sense, dedupe или breath зажигают asr;
    - при pauses='off' и снятых зависимых ступенях asr=False."""
    # pauses='speech' -> asr=True
    stages, _ = cutstages.normalize({
        "pauses": "speech",
        "sense": False,
        "dedupe": False,
        "refine": False,
        "breath": False,
    })
    assert stages["asr"] is True

    # pauses='off', но sense=True -> asr=True
    stages, _ = cutstages.normalize({
        "pauses": "off",
        "sense": True,
        "dedupe": False,
        "refine": False,
        "breath": False,
    })
    assert stages["asr"] is True

    # pauses='off', но breath=True -> asr=True
    stages, _ = cutstages.normalize({
        "pauses": "off",
        "sense": False,
        "dedupe": False,
        "refine": False,
        "breath": True,
    })
    assert stages["asr"] is True

    # pauses='off', но dedupe=True -> asr=True
    stages, _ = cutstages.normalize({
        "pauses": "off",
        "sense": False,
        "dedupe": True,
        "refine": False,
        "breath": False,
    })
    assert stages["asr"] is True

    # pauses='off', refine=True (не требует asr), остальные False -> asr=False
    stages, _ = cutstages.normalize({
        "pauses": "off",
        "sense": False,
        "dedupe": False,
        "refine": True,
        "breath": False,
    })
    assert stages["asr"] is False

    # pauses='off' и всё снято -> asr=False
    stages, _ = cutstages.normalize({
        "pauses": "off",
        "sense": False,
        "dedupe": False,
        "refine": False,
        "breath": False,
        "draft": False,
    })
    assert stages["asr"] is False


def test_unavailable_stages_removed_from_result():
    """Ступени, недоступные в ветке (например, sense в vad), отсутствуют в результате."""
    stages, branch = cutstages.normalize({
        "pauses": "loud",
        "sense": True,
        "refine": True,
        "breath": True,
        "dedupe": False,
    })
    assert branch == "vad"
    assert "sense" not in stages
    assert "refine" not in stages
    assert "breath" not in stages


@pytest.mark.parametrize("raw_input", [
    {},
    {"pauses": "loud"},
    {"pauses": "off", "sense": False, "dedupe": False, "refine": False, "breath": False},
    {"pauses": "speech", "draft": True, "refine": False},
    {"pauses": "loud", "dedupe": True, "draft": True},
    {"sense": True, "refine": False},
])
def test_normalize_is_idempotent(raw_input):
    """Нормализация идемпотентна: normalize(normalize(x)[0]) == normalize(x)."""
    res1, br1 = cutstages.normalize(raw_input)
    res2, br2 = cutstages.normalize(res1)
    assert res1 == res2
    assert br1 == br2


def test_stages_panel_visibility_flag():
    """Все ступени имеют флаг panel (bool). У draft panel=False, у остальных panel=True."""
    for s in cutstages.STAGES:
        assert "panel" in s, f"У ступени {s['key']} нет флага panel"
        assert isinstance(s["panel"], bool)
        if s["key"] == "draft":
            assert s["panel"] is False
        else:
            assert s["panel"] is True

