# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистой логики извлечения правил интро: tools/intro_rules.py."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.intro_rules import (
    _text_width,
    camera_at,
    intro_groups_from_jsx,
    med,
    pct,
    split_mid_groups_precomps,
)


def test_split_mid_groups_precomps_empty() -> None:
    """Пустые mid_groups: [] не должны вызывать IndexError (вызов без исключения)."""
    all_pc, n_null = split_mid_groups_precomps([])
    assert isinstance(all_pc, list)
    assert all_pc == []


def test_split_mid_groups_precomps_exact_null_count() -> None:
    """Проверка точного подсчёта n_null (на пустом входе n_null должен быть 0)."""
    _, n_null = split_mid_groups_precomps([])
    assert n_null == 0


def test_split_mid_groups_precomps_logic() -> None:
    """split_mid_groups_precomps группирует по break и расширяет границы строк."""
    subs = [("w0", 0.0, 0.5), ("w1", 0.5, 1.0), ("w2", 1.0, 1.5), ("w3", 1.5, 2.0)]
    mids: list[dict[str, Any]] = [
        {"from": 0, "count": 2, "break": False},
        {"from": 2, "count": 1, "break": True},
        {"from": None, "count": 1, "break": False},
    ]
    all_pc, _ = split_mid_groups_precomps(mids, subs)
    # Первый прекомп закрылся на break: fi=0, li=1 (0 + 2 - 1), 1 строка
    # Второй прекомп закрылся фиктивным break в конце: fi=2, li=2, 2 строки
    assert all_pc == [(0, 1, 1), (2, 2, 2)]


def test_camera_at() -> None:
    """camera_at определяет активную камеру по номеру кадра."""
    cam_tracks = [
        {"clips": [(0, 100), (200, 300)]},
        {"clips": [(100, 200)]},
    ]
    assert camera_at(cam_tracks, 0) == 0
    assert camera_at(cam_tracks, 50) == 0
    assert camera_at(cam_tracks, 100) == 1
    assert camera_at(cam_tracks, 199) == 1
    assert camera_at(cam_tracks, 250) == 0
    assert camera_at(cam_tracks, 350) == -1  # Дырка / за пределами


def test_intro_groups_from_jsx(tmp_path: Path) -> None:
    """intro_groups_from_jsx парсит var INTRO_GROUPS из файла .jsx."""
    f_ok = tmp_path / "valid.jsx"
    f_ok.write_text('var INTRO_GROUPS=[{"k": "v"}];\n', encoding="utf-8")
    assert intro_groups_from_jsx(str(f_ok)) == [{"k": "v"}]

    f_none = tmp_path / "none.jsx"
    f_none.write_text("var NO = 1;\n", encoding="utf-8")
    assert intro_groups_from_jsx(str(f_none)) is None

    f_bad = tmp_path / "bad.jsx"
    f_bad.write_text("var INTRO_GROUPS=[bad];\n", encoding="utf-8")
    assert intro_groups_from_jsx(str(f_bad)) is None


def test_med_and_pct() -> None:
    """med и pct корректно рассчитывают медиану и квантили."""
    assert med([]) is None
    assert med([5, 1, 3]) == 3
    assert med([10, None, 20]) == 20

    assert pct([], 0.5) is None
    assert pct([1, 2, 3, 4, 5], 0.4) == 3


def test_text_width_mock() -> None:
    """_text_width рассчитывает ширину текста по глифам шрифта."""
    class MockHead:
        unitsPerEm = 1000

    class MockFont:
        def __getitem__(self, key: str) -> Any:
            if key == "head":
                return MockHead()
            if key == "hmtx":
                return {1: [500], 3: [250]}
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, int]:
            return {ord("a"): 1, 0x20: 3}

    mock_tt = MockFont()
    # "a" (500) + " " (250) = 750 units. 750 / 1000 * 140 = 105.0
    w = _text_width(mock_tt, "a ")
    assert abs(w - 105.0) < 1e-4

    # Неизвестный глиф берет ширину пробела (sp=3 -> 250)
    w_unk = _text_width(mock_tt, "x")
    assert abs(w_unk - (250 / 1000 * 140)) < 1e-4
