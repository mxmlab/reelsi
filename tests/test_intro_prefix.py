# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""«Не»/предлог/указательное не отрываются от слова (02.10.2026).

Замер ручных правок владельца (12 клипов, 392 строки): в 25 строках он дописывал
слова СПЕРЕДИ, из них 10 — «не» («ЧУВСТВУЕТЕ» -> «НЕ ЧУВСТВУЕТЕ», «СПАСАЮТ» ->
«НЕ СПАСАЮТ» — смысл переворачивался), остальные — предлог или зависимое слово
(«ТРЕНИРОВКЕ» -> «НА ТРЕНИРОВКЕ», «ПРЕП*РАТЫ» -> «ЭТИ ПРЕП*РАТЫ»).

Здесь шаг _intro_fix_prefix (строки хука по count), раскладка акцентов _place_mids
(«не»/предлог внутри группы и в своей строке, 2026-10-09), плюс инвариант «порядок
слов не меняется».
"""
import os
import random
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


# ---- «НЕ»/«НИ» — всегда; предлоги и указательные — только перед цветной/back (правка 2026-10-09) ----

def test_не_делать_перед_белой_строкой_вместе():
    """«НЕ» в конце строки уходит к своему слову при ЛЮБОМ цвете следующей строки:
    «ТАК НЕ | ДЕЛАТЬ» (обе белые) -> «ТАК | НЕ ДЕЛАТЬ» (решение владельца 2026-10-09)."""
    words = _words("ТАК", "НЕ", "ДЕЛАТЬ")
    rows = _rows((2, "white", True), (1, "white", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert _spell(out, words) == [["ТАК"], ["НЕ", "ДЕЛАТЬ"]]


def test_ни_переезжает_перед_белой_строкой():
    """«НИ» — тот же особый случай, что «НЕ»: перед белой строкой уходит к своему слову."""
    words = _words("ВИДЕЛИ", "НИ", "РАЗУ")
    rows = _rows((2, "white", True), (1, "white", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert _spell(out, words) == [["ВИДЕЛИ"], ["НИ", "РАЗУ"]]


def test_если_вы_на_курсе_перед_белой_не_трогается():
    """«ЕСЛИ ВЫ НА | КУРСЕ» перед белой строкой — без изменений: предлог перед белой
    владелец оставляет сам, как и делал в хуке."""
    words = _words("ЕСЛИ", "ВЫ", "НА", "КУРСЕ")
    rows = _rows((3, "white", True), (1, "white", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [3, 1]
    assert _spell(out, words) == [["ЕСЛИ", "ВЫ", "НА"], ["КУРСЕ"]]


def test_на_курсе_перед_жёлтой_переносится():
    """Предлог перед ЦВЕТНОЙ строкой переезжает к своему слову: «ДЕЛО НА | КУРСЕ»."""
    words = _words("ДЕЛО", "НА", "КУРСЕ")
    rows = _rows((2, "white", True), (1, "yellow", False))
    out = aicut._intro_fix_prefix(rows, words)
    assert [r["count"] for r in out] == [1, 2]
    assert _spell(out, words) == [["ДЕЛО"], ["НА", "КУРСЕ"]]


def test_разрывы_прекомпов_и_back_сохраняются():
    """break и back строк не теряются при переносе."""
    words = _words("ВОТ", "НЕ", "СПАСАЮТ", "ФОН")
    rows = _rows((2, "white", True), (1, "accent", False, True), (1, "white", True))
    out = aicut._intro_fix_prefix(rows, words)
    assert out[0] == {"count": 1, "color": "white", "break": True}
    assert out[1] == {"count": 2, "color": "accent", "break": False, "back": True}
    assert out[2] == {"count": 1, "color": "white", "break": True}


# ---- mid_groups: раскладка акцентов (_place_mids) ----
# Старый шаг _intro_fix_prefix_mids (после раскладки) удалён 2026-10-09: его сдвиг
# начала группы и добор конца теперь делает _place_mids ДО разреза строк.

def _spaced(*names):
    """Лента слов с разрывом 2 с: соседние группы не наезжают друг на друга."""
    return [(i, w, i * 2.0, i * 2.0 + 0.4) for i, w in enumerate(names)]


def _place(groups, words):
    return aicut._place_mids(groups, words, 0, (), emit=lambda *a, **k: None)


def test_mids_не_отрывается_от_акцента():
    """Акцент от «СПАСАЮТ», а перед ним «НЕ»: группа начинается с «НЕ» (from−1, count+1)."""
    words = _spaced("МЫ", "НЕ", "СПАСАЮТ")
    out = _place([(2, 1, "accent", False)], words)
    assert (out[0]["from"], out[0]["count"]) == (1, 2)


def test_mids_предлог_на_тренировке():
    """Жёлтый акцент от «ТРЕНИРОВКЕ»: «НА» уезжает в группу (from−1, count+1)."""
    words = _spaced("МЫ", "НА", "ТРЕНИРОВКЕ")
    out = _place([(2, 1, "yellow", False)], words)
    assert (out[0]["from"], out[0]["count"]) == (1, 2)


def test_mids_белая_группа_после_предлога_сдвигается():
    """Решение владельца 2026-10-09: правило «не/предлог со своим словом» одно для всех
    цветов. Белая группа «ТРЕНИРОВКОЙ» после «С» тянет «С» в себя (from−1, count+1).
    Раньше сдвиг был только для жёлтого/accent — этот тест его меняет намеренно."""
    words = _spaced("МЫ", "С", "ТРЕНИРОВКОЙ")
    out = _place([(2, 1, "white")], words)
    assert (out[0]["from"], out[0]["count"], out[0]["color"]) == (1, 2, "white")


@pytest.mark.parametrize("color", ["white", "yellow", "accent"])
def test_mids_цепочка_ни_в_тянется_целиком(color):
    """«НИ В КОЕМ СЛУЧАЕ»: группа «КОЕМ СЛУЧАЕ» начинается с «НИ» — сдвиг цепочкой, а не
    на одно слово назад (решение владельца 2026-10-09: правило для всех цветов)."""
    words = _spaced("НИ", "В", "КОЕМ", "СЛУЧАЕ")
    out = _place([(2, 2, color)], words)
    assert out[0]["from"] == 0
    assert _spell(out, words) == [["НИ", "В", "КОЕМ"], ["СЛУЧАЕ"]]


def test_mids_без_предлога_не_двигаются():
    """Белый акцент и акцент без предлога перед ним остаются на месте."""
    words = _spaced("МЫ", "СПАСАЮТ")
    out = _place([(1, 1, "accent"), (0, 1, "white")], words)
    assert out == [{"from": 0, "count": 1, "color": "white", "break": True},
                   {"from": 1, "count": 1, "color": "accent", "break": True}]


def test_mids_добирает_слово_после_не_в_конце_группы():
    """«ВАРИАНТ НЕ» + «ПОДОЙДЕТ»: группа добирает следующее слово и делится как
    [ВАРИАНТ] [НЕ ПОДОЙДЕТ] — «НЕ» не остаётся концом строки (ИИ оборвал группу на «НЕ»)."""
    words = _spaced("ВАРИАНТ", "НЕ", "ПОДОЙДЕТ")
    out = _place([(0, 2, "accent", False)], words)
    assert [(m["from"], m["count"], m["break"]) for m in out] == [(0, 1, True), (None, 2, False)]
    assert _spell(out, words) == [["ВАРИАНТ"], ["НЕ", "ПОДОЙДЕТ"]]


def test_mids_не_плюс_слово_одна_строка():
    """«НЕ ПОДОЙДЕТ» — одна группа и одна строка: разрезать после «НЕ» нельзя."""
    words = _spaced("НЕ", "ПОДОЙДЕТ")
    out = _place([(0, 1, "accent", False)], words)
    assert [(m["from"], m["count"], m["break"]) for m in out] == [(0, 2, True)]


def test_mids_предлог_в_конце_ролика_снимается():
    """Группа «ЭТО НЕ» в самом конце ролика: добирать нечего — «НЕ» снимается, и
    «ЭТО» тоже (оно служебное) — группа пропускается, а не висит концом строки."""
    words = _spaced("МЫ", "ЭТО", "НЕ")
    assert _place([(1, 2, "white", False)], words) == []
    assert _place([(2, 1, "accent", False)], words) == []


# ---- _split_words: «не»/предлог не остаётся концом куска (2026-10-09) ----

@pytest.mark.parametrize("ws,expected", [
    (["ВАРИАНТ", "НЕ", "ПОДОЙДЕТ"], [["ВАРИАНТ"], ["НЕ", "ПОДОЙДЕТ"]]),
    (["НАМ", "НЕ", "ПОЗВОЛЯЕТ"], [["НАМ"], ["НЕ", "ПОЗВОЛЯЕТ"]]),
    (["НЕ", "ПОДОЙДЕТ"], [["НЕ", "ПОДОЙДЕТ"]]),
    (["ДОЛЖНЫ", "ПОД", "НЕЕ"], [["ДОЛЖНЫ"], ["ПОД", "НЕЕ"]]),
    (["НУЖНО", "НЕ", "ПОТЕРЯТЬ"], [["НУЖНО"], ["НЕ", "ПОТЕРЯТЬ"]]),
    (["ДОЛЖНЫ", "ПОД"], [["ДОЛЖНЫ", "ПОД"]]),
    (["БЕЗОПАСНО,", "С"], [["БЕЗОПАСНО,", "С"]]),
])
def test_split_words_не_и_предлог_не_одиноки_и_не_концы(ws, expected):
    """Разрез сразу после «не»/предлога запрещён; кусок из одного такого слова — тоже.
    Если допустимого разреза нет — строка целиком (длиннее лимита лучше отрыва)."""
    assert aicut._split_words(ws) == expected


def test_split_words_служебное_штрафуется_а_не_запрещено():
    """После прочего служебного слова («НАМ») разрез берётся только если без него
    нельзя: здесь свободный разрез после «АБВГ» дальше от середины, но без штрафа."""
    out = aicut._split_words(["АБВГ", "НАМ", "ДЕЖЗИКЛМ"], limit=9)
    assert out == [["АБВГ"], ["НАМ"], ["ДЕЖЗИКЛМ"]]


# ---- свойство: ни одна строка mid_groups не кончается «не»/предлогом и не одинока ----

_VOCAB = (
    # «не»/предлоги/указательные (INTRO_PREFIX_WORDS)
    "НЕ НИ В НА С О ЭТО ЭТИ ПОД ДЛЯ ПО ОТ ЗА БЕЗ ИЗ К ПРИ ПРО У ЧЕРЕЗ КАЖДЫЙ ЭТОТ "
    # прочие служебные (INTRO_FUNC_WORDS)
    "И ЧТО ЖЕ ТАК БЫ ДА ЛИ ИЛИ НО ВСЕ МЫ ВЫ ОН ОНИ ТАМ ВОТ ДАЖЕ ЕЩЕ НАМ ТЕБЕ "
    # обычные слова
    "ТРЕНИРОВКЕ ПОДОЙДЕТ ВАРИАНТ ПОЗВОЛЯЕТ ПОТЕРЯТЬ ДОЛЖНЫ НУЖНО БЕЗОПАСНО, РЕЗУЛЬТАТ "
    "ПРОГРАММА ЧУВСТВУЕТЕ СПАСАЮТ ПРОФЕССИОНАЛЬНЫХ ОРГАНИЗМ МЫШЦЫ ВОДЫ ЛИШНЕЙ ПРОСТО "
    "ДЕНЬ СНОВА ПОМОГАЕТ ВЕСЬ СЛОЖНО ПРОЦЕСС ЗАДАЧА ЛЮДИ ВРЕМЯ ГОРАЗДО ОЧЕНЬ ДОМА ФОРМА"
).split()


def test_словарь_свойства_достаточен():
    assert len(_VOCAB) >= 60


def test_свойство_mids_строки_не_кончаются_предлогом_и_не_одиноки():
    """200 случайных групп по тексту из слов словаря: в раскладке mid_groups ни одна
    строка не кончается словом из INTRO_PREFIX_WORDS и не состоит из одного такого слова."""
    rng = random.Random(20261009)
    prefix = aicut.INTRO_PREFIX_WORDS
    for _ in range(200):
        names = [rng.choice(_VOCAB) for _ in range(rng.randint(4, 14))]
        f = rng.randrange(len(names))
        c = rng.randint(1, min(4, len(names) - f))
        color = rng.choice(["white", "yellow", "accent"])
        out = _place([(f, c, color, False)], _spaced(*names))
        k = 0
        for row in out:
            if row["from"] is not None:
                k = row["from"]
            ws = names[k:k + row["count"]]
            k += row["count"]
            assert ws[-1].upper() not in prefix, (names, f, c, out)
            assert not (len(ws) == 1 and ws[0].upper() in prefix), (names, f, c, out)


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
