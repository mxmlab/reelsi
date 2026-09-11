# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Таблица оформления строк интро (_intro_look) и инварианты.

Проверяем полную таблицу (color × back × nwords) → (anim, fx),
инварианты «белая не светится», «glitch только на accent», переживание
back при переносе длинных строк и сохранение color/back в _place_mids.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402

# Помощник из test_intro_hook.py — задание просит переиспользовать,
# дублируем ту же сигнатуру.
from test_intro_hook import _words  # noqa: E402


# ---- 2.1: таблица _intro_look целиком ----

@pytest.mark.parametrize("color, back, nwords, expected", [
    ("accent", False, 1, ("glitch", "glow")),
    ("accent", False, 2, ("glitch", "glow")),
    ("accent", False, 3, ("reveal", "glow")),
    ("white",  True,  1, ("reveal", "")),
    ("white",  False, 1, ("", "")),
    ("yellow", False, 1, ("", "")),
    ("yellow", True,  1, ("reveal", "")),
])
def test_таблица_intro_look(color, back, nwords, expected):
    """Полная таблица (color, back, nwords) → (anim, fx)."""
    assert aicut._intro_look(color, back, nwords) == expected


# ---- 2.2: белая строка не светится никогда ----

def test_белая_строка_не_светится_никогда():
    """Непустой fx обязан быть ТОЛЬКО у color == 'accent'."""
    for color in ("white", "yellow", "accent"):
        for back in (False, True):
            for nwords in (1, 3):
                anim, fx = aicut._intro_look(color, back, nwords)
                if color != "accent":
                    assert fx == "", (
                        f"fx != '' при color={color}, back={back}, nwords={nwords}")
                else:
                    assert fx != "", (
                        f"fx == '' при color=accent, back={back}, nwords={nwords}")


# ---- 2.3: glitch только на цветной (accent) ----

def test_glitch_только_на_accent():
    """anim == 'glitch' встречается только при color == 'accent'."""
    for color in ("white", "yellow", "accent"):
        for back in (False, True):
            for nwords in (1, 3):
                anim, _ = aicut._intro_look(color, back, nwords)
                if anim == "glitch":
                    assert color == "accent", (
                        f"glitch при color={color}, back={back}, nwords={nwords}")


# ---- 2.4: back переживает перенос длинных строк ----

def test_back_переживает_перенос_длинных_строк():
    """Строка из 6 слов с back=True, порезанная _wrap_intro_rows с узким лимитом,
    даёт больше одной строки и у КАЖДОЙ back=True."""
    words = _words(6)
    rows = [{"count": 6, "color": "accent", "break": True, "back": True}]
    result = aicut._wrap_intro_rows(rows, words, limit=9)
    assert len(result) > 1, "строка должна быть разбита на несколько"
    for r in result:
        assert r["back"] is True, f"back потерялся: {r}"


# ---- 2.5: _place_mids сохраняет и цвет, и back ----

def test_place_mids_сохраняет_цвет_и_back():
    """Одна группа (from, count, color, back) при пустом busy и intro_len=0:
    в результате у строк color == 'accent' и back is True."""
    words = _words(40)
    groups = [(10, 2, "accent", True)]
    mids = aicut._place_mids(groups, words, intro_len=0, busy=(),
                             emit=lambda *a, **k: None)
    assert len(mids) > 0, "ожидается хотя бы одна строка"
    for m in mids:
        assert m["color"] == "accent", f"color потерялся: {m}"
        assert m["back"] is True, f"back потерялся: {m}"
