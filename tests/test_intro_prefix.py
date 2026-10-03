# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""«Не»/предлог/указательное не отрываются от слова (02.10.2026).

Замер ручных правок владельца (12 клипов, 392 строки): в 25 строках он дописывал
слова СПЕРЕДИ, из них 10 — «не» («ЧУВСТВУЕТЕ» -> «НЕ ЧУВСТВУЕТЕ», «СПАСАЮТ» ->
«НЕ СПАСАЮТ» — смысл переворачивался), остальные — предлог или зависимое слово
(«ТРЕНИРОВКЕ» -> «НА ТРЕНИРОВКЕ», «ПРЕП*РАТЫ» -> «ЭТИ ПРЕП*РАТЫ»).

Здесь шаг _intro_fix_prefix (строки хука по count) и _intro_fix_prefix_mids
(акценты по полю from), плюс инвариант «порядок слов не меняется».
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402


def _words(*names):
    """Лента слов: по слову каждые 0.5 с, длительность 0.4 с."""
    return [(i, w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(names)]


def _rows(*specs):
    """Спеки (count, color, break[, back]) -> строки того же вида, что у cmd_intro."""
    out = []
    for spec in specs:
        row = {"count": spec[0], "color": spec[1], "break": spec[2]}
        if len(spec) > 3:
            row["back"] = spec[3]
        out.append(row)
    return out


def _spell(rows, words):
    """Разложить строки обратно по ленте слов: [[слова строки], ...]."""
    out, k = [], 0
    for r in rows:
        out.append([w[1] for w in words[k:k + r["count"]]])
        k += r["count"]
    return out


def _flat(words):
    return [w[1] for w in words]


# ---- «не» перед цветной строкой переезжает в неё ----

def test_не_переезжает_в_акцентную_строку():
    """«вы не | ЧУВСТВУЕТЕ» -> «вы | НЕ ЧУВСТВУЕТЕ»: «не» не остаётся в белой строке."""
    words = _words("ВЫ", "НЕ", "ЧУВСТВУЕТЕ")
    rows = _rows((2, "white", True), (1, "accent", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert [r["color"] for r in out] == ["white", "accent"]
    assert _spell(out, words) == [["ВЫ"], ["НЕ", "ЧУВСТВУЕТЕ"]]


def test_опустевшая_белая_строка_удаляется():
    """«не» было единственным словом строки — строка исчезает, а не остаётся пустой."""
    words = _words("НЕ", "СПАСАЮТ")
    rows = _rows((1, "white", True), (1, "accent", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert out == [{"count": 2, "color": "accent", "break": True}]
    assert _spell(out, words) == [["НЕ", "СПАСАЮТ"]]


def test_предлог_на_тренировке():
    """Предлог из закрытого списка: «мы | НА ТРЕНИРОВКЕ», а не «мы на | ТРЕНИРОВКЕ»."""
    words = _words("МЫ", "НА", "ТРЕНИРОВКЕ")
    rows = _rows((2, "white", True), (1, "yellow", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert _spell(out, words) == [["МЫ"], ["НА", "ТРЕНИРОВКЕ"]]


def test_указательное_эти_препараты():
    """Указательное/определительное — тот же перенос: «ЭТИ ПРЕП*РАТЫ»."""
    words = _words("БЕРУТ", "ЭТИ", "ПРЕП*РАТЫ")
    rows = _rows((2, "white", True), (1, "accent", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert _spell(out, words) == [["БЕРУТ"], ["ЭТИ", "ПРЕП*РАТЫ"]]


def test_back_строка_тоже_не_отрывает_слово():
    """Строка заднего плана (back) — та же цель: «ВОТ | БЕЗ ЛИШНЕЙ ВОДЫ»."""
    words = _words("ПЬЮ", "БЕЗ", "ЛИШНЕЙ", "ВОДЫ")
    rows = _rows((2, "white", True), (2, "white", False, True))
    out = aicut._intro_fix_prefix(rows, words)
    assert _spell(out, words) == [["ПЬЮ"], ["БЕЗ", "ЛИШНЕЙ", "ВОДЫ"]]


# ---- строка из одного предлога склеивается со следующим словом ----

def test_строка_из_одного_предлога_склеивается():
    """Строка «О» (один предлог) — склеивается со следующим словом, пустой не висит."""
    words = _words("РАССКАЖУ", "О", "СЕБЕ")
    rows = _rows((1, "white", True), (1, "white", False), (1, "white", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert _spell(out, words) == [["РАССКАЖУ"], ["О", "СЕБЕ"]]


def test_одиночный_предлог_уезжает_в_цветную_строку():
    """«ПОГОВОРИМ | О | ТРЕНИРОВКЕ»: «О» уходит в жёлтую строку к своему слову."""
    words = _words("ПОГОВОРИМ", "О", "ТРЕНИРОВКЕ")
    rows = _rows((1, "white", True), (1, "white", False), (1, "yellow", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert _spell(out, words) == [["ПОГОВОРИМ"], ["О", "ТРЕНИРОВКЕ"]]


# ---- белая строка перед белой — не трогаем ----

def test_белые_строки_не_перекладываются():
    """Переносится только в цветную/back-строку: у двух белых строк с предлогом ничего не меняется."""
    words = _words("МЫ", "НА", "ТРЕНИРОВКЕ")
    rows = _rows((2, "white", True), (1, "white", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [2, 1]
    assert _spell(out, words) == [["МЫ", "НА"], ["ТРЕНИРОВКЕ"]]


def test_разрывы_прекомпов_и_back_сохраняются():
    """break и back строк не теряются при переносе."""
    words = _words("ВОТ", "НЕ", "СПАСАЮТ", "ФОН")
    rows = _rows((2, "white", True), (1, "accent", False, True), (1, "white", True))
    out = aicut._intro_fix_prefix(rows, words)
    assert out[0] == {"count": 1, "color": "white", "break": True}
    assert out[1] == {"count": 2, "color": "accent", "break": False, "back": True}
    assert out[2] == {"count": 1, "color": "white", "break": True}


# ---- mid_groups: то же по полю from ----

def test_mids_не_отрывается_от_акцента():
    """Акцент от «СПАСАЮТ», а перед ним «НЕ»: from−1, count+1."""
    words = _words("МЫ", "НЕ", "СПАСАЮТ")
    mids = [{"from": 2, "count": 1, "color": "accent", "break": True}]
    out = aicut._intro_fix_prefix_mids(mids, words)
    assert out[0]["from"] == 1
    assert out[0]["count"] == 2


def test_mids_предлог_на_тренировке():
    """Жёлтый акцент от «ТРЕНИРОВКЕ»: «НА» уезжает в группу (from−1, count+1)."""
    words = _words("МЫ", "НА", "ТРЕНИРОВКЕ")
    mids = [{"from": 2, "count": 1, "color": "yellow", "break": True}]
    out = aicut._intro_fix_prefix_mids(mids, words)
    assert (out[0]["from"], out[0]["count"]) == (1, 2)


def test_mids_без_предлога_не_двигаются():
    """Белый акцент и акцент без предлога перед ним остаются на месте."""
    words = _words("МЫ", "СПАСАЮТ")
    mids = [{"from": 1, "count": 1, "color": "accent", "break": True},
            {"from": 0, "count": 1, "color": "white", "break": True}]
    out = aicut._intro_fix_prefix_mids([dict(m) for m in mids], words)
    assert out == mids


def test_mids_продолжения_строки_не_двигаются():
    """У строк-продолжений акцента from=None — их шаг не трогает."""
    words = _words("НЕ", "СПАСАЮТ", "ВСЕХ")
    mids = [{"from": 1, "count": 1, "color": "accent", "break": True},
            {"from": None, "count": 1, "color": "accent", "break": False}]
    out = aicut._intro_fix_prefix_mids([dict(m) for m in mids], words)
    assert out[0]["from"] == 0 and out[0]["count"] == 2
    assert out[1]["from"] is None and out[1]["count"] == 1


# ---- инвариант: порядок слов не меняется ----

@pytest.mark.parametrize("names,rows", [
    (("ВЫ", "НЕ", "ЧУВСТВУЕТЕ"), ((2, "white", True), (1, "accent", False))),
    (("МЫ", "НА", "ТРЕНИРОВКЕ"), ((2, "white", True), (1, "yellow", False))),
    (("РАССКАЖУ", "О", "СЕБЕ"), ((1, "white", True), (1, "white", False), (1, "white", False))),
    (("ПОГОВОРИМ", "О", "ТРЕНИРОВКЕ"), ((1, "white", True), (1, "white", False), (1, "yellow", False))),
])
def test_порядок_слов_не_меняется(names, rows):
    """Перенос — перекладывание слов между строками, а не перестановка речи."""
    words = _words(*names)
    out = aicut._intro_fix_prefix(_rows(*rows), words)
    flat = [w for line in _spell(out, words) for w in line]
    assert flat == _flat(words)
    assert sum(r["count"] for r in out) == len(words)
