# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Оформление строк интро (_intro_look): таблица, правила ГРУППЫ и инварианты.

Правила сняты с ручных правок владельца (02.10.2026, 400 строк; совпадение 327 = 82 %):
accent -> glitch ЛЮБОЙ длины (108 из 109); back -> up при 2+ словах (10 из 16), при
одном слове без анимации; группа 4+ строк (white/yellow) -> первая reveal, остальные
каскадом right (15 из 17); белая 2–3 слова -> up, белая 1 слово -> без анимации (57 из
69); жёлтая 1 слово -> без анимации (105 из 124). Правило пары «1+1 -> left/right»
удалено: у владельца так 3 пары из 43, остальные 40 без анимации.

Инварианты: fx отсюда не ставится никому (свечение — дверь стиля), glitch бывает
только на accent, back переживает перенос длинных строк, _place_mids хранит
color/back.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402

# Помощник из test_intro_hook.py — задание просит переиспользовать,
# дублируем ту же сигнатуру.
from test_intro_hook import _words  # noqa: E402


# ---- 2.1: таблица _intro_look целиком (группа из одной строки) ----

@pytest.mark.parametrize("color, back, nwords, expected", [
    ("accent", False, 1, ("glitch", "")),
    ("accent", False, 2, ("glitch", "")),
    ("accent", False, 3, ("glitch", "")),
    ("accent", True,  2, ("glitch", "")),      # accent сильнее back
    ("white",  True,  1, ("", "")),
    ("white",  True,  2, ("up", "")),
    ("white",  True,  3, ("up", "")),
    ("white",  False, 1, ("", "")),
    ("white",  False, 2, ("up", "")),
    ("white",  False, 3, ("up", "")),
    ("yellow", False, 1, ("", "")),
    ("yellow", True,  1, ("", "")),
    ("yellow", True,  2, ("up", "")),
])
def test_таблица_intro_look(color, back, nwords, expected):
    """Полная таблица (color, back, nwords) → (anim, fx)."""
    assert aicut._intro_look(color, back, nwords) == expected


# ---- 2.2: правила группы — каждый отдельный случай ----

def test_белая_строка_два_слова_up():
    """Белая строка 2–3 слова (вне каскада) → up."""
    assert aicut._intro_look("white", False, 2, group_pos=0, group_size=1) == ("up", "")
    assert aicut._intro_look("white", False, 3, group_pos=0, group_size=1) == ("up", "")


def test_белая_одно_слово_без_анимации():
    """Белая строка 1 слово → без анимации (57 из 69)."""
    assert aicut._intro_look("white", False, 1, group_pos=0, group_size=1) == ("", "")


def test_жёлтая_одно_слово_без_анимации():
    """Жёлтая строка 1 слово → без анимации (105 из 124)."""
    assert aicut._intro_look("yellow", False, 1, group_pos=0, group_size=1) == ("", "")


def test_accent_glitch_без_свечения():
    """accent → glitch ЛЮБОЙ длины, fx пустой: свечение accent-строк ставит стиль."""
    assert aicut._intro_look("accent", False, 1, group_pos=0, group_size=1) == ("glitch", "")
    assert aicut._intro_look("accent", False, 2, group_pos=0, group_size=1) == ("glitch", "")


def test_accent_три_слова_glitch():
    """accent 3 слова → glitch, а не reveal (у владельца 108 из 109 accent-строк)."""
    assert aicut._intro_look("accent", False, 3, group_pos=0, group_size=1) == ("glitch", "")


def test_back_одно_слово_без_анимации():
    """back 1 слово → без анимации: reveal владелец back-строкам не ставит."""
    assert aicut._intro_look("white", True, 1, group_pos=0, group_size=1) == ("", "")
    assert aicut._intro_look("yellow", True, 1, group_pos=0, group_size=1) == ("", "")


def test_back_два_слова_up():
    """back 2+ слова → up (10 из 16 back-строк)."""
    assert aicut._intro_look("white", True, 2, group_pos=0, group_size=1) == ("up", "")
    assert aicut._intro_look("white", True, 3, group_pos=0, group_size=1) == ("up", "")


def test_пара_однословных_без_анимации():
    """Правило пары 1+1 удалено: у владельца 40 пар из 43 без анимации."""
    assert aicut._intro_look("white", False, 1, group_pos=0, group_size=2) == ("", "")
    assert aicut._intro_look("white", False, 1, group_pos=1, group_size=2) == ("", "")


def test_пара_yellow_1_плюс_1_без_анимации():
    """Пара жёлтых по одному слову → обе строки без анимации (105 из 124)."""
    assert aicut._intro_look("yellow", False, 1, group_pos=0, group_size=2) == ("", "")
    assert aicut._intro_look("yellow", False, 1, group_pos=1, group_size=2) == ("", "")


def test_группа_из_четырёх_каскад():
    """Группа 4+ строк: первая reveal, остальные каскадом right."""
    got = [aicut._intro_look("white", False, 1, group_pos=i, group_size=4)
           for i in range(4)]
    assert got == [("reveal", ""), ("right", ""), ("right", ""), ("right", "")]


def test_группа_из_четырёх_каскад_на_двухсловных():
    """Каскад важнее правила строки: 2 слова в группе из 4 → reveal/right, не up."""
    assert aicut._intro_look("white", False, 2, group_pos=0, group_size=4) == ("reveal", "")
    assert aicut._intro_look("white", False, 2, group_pos=1, group_size=4) == ("right", "")


# ---- 2.3: fx отсюда не ставится никому (свечение — из стиля) ----

def test_fx_не_ставится_ни_одной_строке():
    """fx обязан быть пустым у ВСЕХ: свечение — ключ стиля intro_accent_glow, а не разметка."""
    for color in ("white", "yellow", "accent"):
        for back in (False, True):
            for nwords in (1, 2, 3):
                for gpos, gsize in ((0, 1), (1, 2), (0, 4), (3, 4)):
                    _, fx = aicut._intro_look(color, back, nwords, gpos, gsize)
                    assert fx == "", (
                        f"fx != '' при color={color}, back={back}, nwords={nwords}, "
                        f"group={gpos}/{gsize}")


# ---- 2.4: glitch только на цветной (accent) ----

def test_glitch_только_на_accent():
    """anim == 'glitch' встречается только при color == 'accent'."""
    for color in ("white", "yellow", "accent"):
        for back in (False, True):
            for nwords in (1, 3):
                for gpos, gsize in ((0, 1), (1, 2), (0, 4)):
                    anim, _ = aicut._intro_look(color, back, nwords, gpos, gsize)
                    if anim == "glitch":
                        assert color == "accent", (
                            f"glitch при color={color}, back={back}, nwords={nwords}")


# ---- 2.5: back переживает перенос длинных строк ----

def test_back_переживает_перенос_длинных_строк():
    """Строка из 6 слов с back=True, порезанная _wrap_intro_rows с узким лимитом,
    даёт больше одной строки и у КАЖДОЙ back=True."""
    words = _words(6)
    rows = [{"count": 6, "color": "accent", "break": True, "back": True}]
    result = aicut._wrap_intro_rows(rows, words, limit=9)
    assert len(result) > 1, "строка должна быть разбита на несколько"
    for r in result:
        assert r["back"] is True, f"back потерялся: {r}"


# ---- 2.6: _place_mids сохраняет и цвет, и back ----

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


# ---- 2.7: cmd_intro раздаёт group_pos/group_size по строкам между break ----

def test_cmd_intro_раскладывает_анимации_по_группам(monkeypatch, tmp_path):
    """cmd_intro зовёт _intro_look с контекстом группы: группа из 4 строк получает
    каскад reveal/right/right/right, а одинокая строка — без анимации."""
    import gzip
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        f.write(g.read())
    model_rows = [{"count": 1, "color": "white", "break": True},
                  {"count": 1, "color": "white", "break": False},
                  {"count": 1, "color": "white", "break": False},
                  {"count": 1, "color": "white", "break": False},
                  {"count": 1, "color": "white", "break": True}]
    monkeypatch.setattr(aicut.commands, "_ask_json",
                        lambda *a, **k: {"intro_rows": model_rows, "mid_groups": []})
    res = aicut.cmd_intro(dst, emit=lambda *a, **k: None)
    rows = res["intro_rows"]
    assert [r["anim"] for r in rows] == ["reveal", "right", "right", "right", ""], rows
    assert all(r["fx"] == "" for r in rows)
    saved = json.load(open(str(tmp_path / "timeline.intro.json"), encoding="utf-8"))
    assert saved["intro_rows"] == rows
