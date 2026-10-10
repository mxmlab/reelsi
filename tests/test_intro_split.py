# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Длина строки интро — ручка СТИЛЯ; разрез — только по смысловым границам.

Сверка ИИ-интро с итогом владельца по 48 роликам: код рвал строку хука длиннее
порога ПО БУКВАМ, и владелец склеивал обратно — «ни в коем | случае» -> «ни в коем
случае», «не надо | брать,» -> «не надо брать,», «от гормона | роста», «Вечная |
проблема», «давайте уже | расскажем». Его строки доходят до ~20 символов, когда фраза
— одно смысловое целое.

Правила:
  * строка режется, ТОЛЬКО если она длиннее ручки; короче — не трогается вовсе;
  * разрез — не после «не»/предлога (INTRO_PREFIX_WORDS) и не оставляет одиноким такое
    слово; после прочего служебного слова — только если без него нельзя; среди
    допустимых берётся ближайший к середине (см. _split_words);
  * если допустимого разреза нет вовсе, строка остаётся целой (смысл важнее длины);
  * ручка — ключ СТИЛЯ `intro_row_max` (дефолт 20, core/styles.py). Стиль приезжает от
    КЛИПА: `/api/ai_intro` несёт его `style`, cmd_intro(style=…) читает ручку через
    styles.resolve. Нет стиля — BASE.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402
from core.aicut import commands  # noqa: E402


def _words(*names):
    """Лента слов: по слову каждые 0.5 с, длительность 0.4 с."""
    return [(i, w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(names)]


def _lines(rows, words):
    """Строки (по count) -> [[слова строки], ...] по ленте слов."""
    out, k = [], 0
    for r in rows:
        n = max(1, r.get("count") or 1)
        out.append([w[1] for w in words[k:k + n]])
        k += n
    return out


def _rows(*specs):
    """Спеки (count, color, break) -> строки того же вида, что у cmd_intro."""
    return [{"count": s[0], "color": s[1], "break": s[2]} for s in specs]


def _chunks_end_with_prefix(chunks):
    """Куски, кончающиеся словом из INTRO_PREFIX_WORDS («не», предлог): такой отрыв
    правило запрещает во всех кусках."""
    return [c for c in chunks if c and c[-1].upper() in commands.INTRO_PREFIX_WORDS]


def _intro(tmp_path, monkeypatch, names, model_rows, style=None):
    """cmd_intro на подменённых словах и ответе модели: XML и модель не нужны."""
    words = _words(*names)
    monkeypatch.setattr(commands, "_words_from_xml", lambda _p: words)
    monkeypatch.setattr(commands, "_ask_json",
                        lambda *a, **k: {"intro_rows": model_rows, "mid_groups": []})
    res = commands.cmd_intro(str(tmp_path / "clip.xml"), emit=lambda *a, **k: None,
                             style=style)
    return res, words


# ---- разрез по словам: ручка 20 против прежних 14 ----

def test_ни_в_коем_случае_при_ручке_20_одна_строка():
    """«НИ В КОЕМ СЛУЧАЕ» (16) — одно смысловое целое: при ручке 20 строка цела."""
    words = _words("НИ", "В", "КОЕМ", "СЛУЧАЕ")
    rows = _rows((4, "white", True))
    assert commands._wrap_intro_rows(rows, words, 20) == \
        [{"count": 4, "color": "white", "break": True}]


def test_ни_в_коем_случае_при_ручке_9_не_режется_после_в():
    """Ручка 9 — строка режется, но НЕ после «В»: предлог остаётся со своим словом.
    Кусок «НИ В КОЕМ» (9) — ровно ручка; разрез после «КОЕМ» единственный допустимый."""
    out = commands._split_words(["НИ", "В", "КОЕМ", "СЛУЧАЕ"], limit=9)
    assert out == [["НИ", "В", "КОЕМ"], ["СЛУЧАЕ"]]
    assert not _chunks_end_with_prefix(out)


def test_если_вы_на_курсе_не_рвется_после_на():
    """«ЕСЛИ ВЫ НА КУРСЕ» при ручке 14: разрез после «НА» запрещён (предлог остался бы
    без своего слова), куски не кончаются предлогом."""
    out = commands._split_words(["ЕСЛИ", "ВЫ", "НА", "КУРСЕ"], limit=14)
    assert [w for chunk in out for w in chunk] == ["ЕСЛИ", "ВЫ", "НА", "КУРСЕ"]
    assert not _chunks_end_with_prefix(out)
    assert not any(chunk[-1] == "НА" for chunk in out)


def test_кусок_не_остаётся_из_одного_служебного_слова():
    """«МЫ НА» при ручке 3: разрез после «МЫ» оставил бы хвост из одного предлога —
    строка не режется."""
    assert commands._split_words(["МЫ", "НА"], limit=3) == [["МЫ", "НА"]]


def test_длинная_строка_режется_по_смысловой_границе():
    """Длинная строка режется, и оба куска влезают в ручку, когда чистый разрез есть."""
    assert commands._split_words(["БОЛЬШИНСТВО", "НА", "КУРСЕ"], limit=14) == \
        [["БОЛЬШИНСТВО"], ["НА", "КУРСЕ"]]
    assert commands._split_words(["НАЧИНАЕМ", "С", "ПОЛОВИНЫ"], limit=14) == \
        [["НАЧИНАЕМ"], ["С", "ПОЛОВИНЫ"]]


def test_разрез_ближайший_к_середине_среди_чистых():
    """Из допустимых разрезов берётся ближайший к середине: «РАЗ И ДВА | ТРИ ЧЕТЫРЕ»,
    а не «РАЗ И | ДВА ТРИ ЧЕТЫРЕ» (тот же служебный конец, но дальше от середины)."""
    assert commands._split_words(["РАЗ", "И", "ДВА", "ТРИ", "ЧЕТЫРЕ"], limit=14) == \
        [["РАЗ", "И", "ДВА"], ["ТРИ", "ЧЕТЫРЕ"]]


@pytest.mark.parametrize("names,limit", [
    (("НИ", "В", "КОЕМ", "СЛУЧАЕ"), 20),
    (("НИ", "В", "КОЕМ", "СЛУЧАЕ"), 9),
    (("ЕСЛИ", "ВЫ", "НА", "КУРСЕ"), 14),
    (("БОЛЬШИНСТВО", "НА", "КУРСЕ"), 14),
    (("МЫ", "НА", "ТРЕНИРОВКЕ", "КАЖДЫЙ", "ДЕНЬ"), 9),
    (("ДАВАЙТЕ", "УЖЕ", "РАССКАЖЕМ", "ПРО", "ЭТО", "СЕЙЧАС"), 12),
])
def test_разбивка_сохраняет_слова_и_порядок(names, limit):
    """Перенос — только перекладывание слов между строками, не перестановка речи."""
    out = commands._split_words(list(names), limit=limit)
    assert [w for chunk in out for w in chunk] == list(names)


# ---- склейка служебного слова: до ручки и без грабежа цветной строки ----

def test_склейка_служебного_слова_тянет_до_порога_14():
    """«ДЛЯ» + «МОНТАЖА» (10 символов) — строка кончается предлогом, первое слово следующей
    переезжает назад: порог склейки INTRO_DEFUNC_MAX_CHARS = 14."""
    words = _words("ДЛЯ", "МОНТАЖА", "ЭТО")
    rows = _rows((1, "white", True), (1, "white", False), (1, "white", False))
    out = commands._intro_defunc(rows, words)
    assert [r["count"] for r in out] == [2, 1]
    assert _lines(out, words) == [["ДЛЯ", "МОНТАЖА"], ["ЭТО"]]


def test_склейка_не_выходит_за_порог_14():
    """«ДЛЯ ПРОФЕССИОНАЛА» — 18 символов: длиннее порога склейки, слово не тянется."""
    words = _words("ДЛЯ", "ПРОФЕССИОНАЛА", "МОНТАЖА")
    rows = _rows((1, "white", True), (1, "white", False), (1, "white", False))
    out = commands._intro_defunc(rows, words)
    assert [r["count"] for r in out] == [1, 1, 1]


def test_склейка_не_зависит_от_ручки_стиля():
    """_intro_defunc не принимает ручку стиля: порог — INTRO_DEFUNC_MAX_CHARS (14).
    «ЕСЛИ ВЫ» + «НА» — 10 символов, в порог 14 влезает, слово «НА» тянется наверх.
    Ручка intro_row_max (20) сюда не доходит: она режет только _wrap_intro_rows."""
    import inspect
    assert list(inspect.signature(commands._intro_defunc).parameters) == ["rows", "words"]
    assert commands.INTRO_DEFUNC_MAX_CHARS == 14
    words = _words("ЕСЛИ", "ВЫ", "НА", "КУРСЕ")
    rows = _rows((2, "white", True), (2, "white", False))
    out = commands._intro_defunc(rows, words)
    assert [r["count"] for r in out] == [3, 1]


def test_из_цветной_строки_слово_не_тянется():
    """Жёлтая/accent строка — смысловой пик: первое её слово наверх не уезжает, иначе
    строка остаётся пустой и цвет теряется целиком. Отрыв перед ней закрывает
    _intro_fix_prefix — служебное слово уезжает В цветную строку, цвет сохраняется."""
    words = _words("ВЫ", "НЕ", "ЧУВСТВУЕТЕ")
    rows = _rows((2, "white", True), (1, "accent", False))
    out = commands._intro_defunc(rows, words)
    assert [r["count"] for r in out] == [2, 1]
    assert [r["color"] for r in out] == ["white", "accent"]


# ---- ручка стиля доезжает до разбивки ----

def test_ручка_стиля_задаёт_длину_строки(tmp_path, monkeypatch):
    """Стиль клипа (dict) с ручкой 9 режет то, что BASE (20) оставляет целым."""
    names = ("НИ", "В", "КОЕМ", "СЛУЧАЕ")
    model = [{"count": 4, "color": "white", "break": True}]
    wide, words = _intro(tmp_path, monkeypatch, names, model)
    narrow, _ = _intro(tmp_path, monkeypatch, names, model, style={"intro_row_max": 9})
    assert [r["count"] for r in wide["intro_rows"]] == [4]
    assert _lines(wide["intro_rows"], words) == [["НИ", "В", "КОЕМ", "СЛУЧАЕ"]]
    assert [r["count"] for r in narrow["intro_rows"]] == [3, 1]
    assert _lines(narrow["intro_rows"], words) == [["НИ", "В", "КОЕМ"], ["СЛУЧАЕ"]]


def test_два_клипа_два_стиля_режут_по_своему(tmp_path, monkeypatch):
    """Два клипа с разными стилями (ручки 20 и 9) на одних и тех же словах: каждый
    режет по СВОЕЙ ручке, второй стиль не перебивает первый."""
    names = ("НИ", "В", "КОЕМ", "СЛУЧАЕ")
    model = [{"count": 4, "color": "white", "break": True}]
    a, _ = _intro(tmp_path, monkeypatch, names, model, style={"intro_row_max": 20})
    b, _ = _intro(tmp_path, monkeypatch, names, model, style={"intro_row_max": 9})
    assert [r["count"] for r in a["intro_rows"]] == [4]
    assert [r["count"] for r in b["intro_rows"]] == [3, 1]


def test_стиль_по_имени_читается_из_стилей(tmp_path, monkeypatch):
    """Клип приносит стиль ИМЕНЕМ: ручка берётся из файла стиля, а не из дефолта."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path))
    styles.save("СтильУзкий", {"intro_row_max": 9})
    names = ("НИ", "В", "КОЕМ", "СЛУЧАЕ")
    model = [{"count": 4, "color": "white", "break": True}]
    res, _ = _intro(tmp_path, monkeypatch, names, model, style="СтильУзкий")
    assert [r["count"] for r in res["intro_rows"]] == [3, 1]
    assert commands._intro_row_max("СтильУзкий") == 9


def test_без_стиля_ручка_из_base():
    """Нет стиля — BASE: ручка 20. Битое число в стиле — тоже BASE."""
    assert commands._intro_row_max(None) == 20
    assert commands._intro_row_max({"intro_row_max": "много"}) == 20
    assert commands._intro_row_max({"intro_row_max": 11}) == 11


def test_дефолт_ручки_один_в_базе_и_в_панели():
    """20 живёт в BASE стиля; панель строит поле из схемы — дефолт обязан попадать в
    пределы поля, иначе ползунок не покажет значение BASE."""
    from core import style_schema
    fields = []

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                walk(it.get("items", []))
            elif it.get("type") == "field":
                fields.append(it)

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    row = next(f for f in fields if f.get("key") == "intro_row_max")
    assert row["min"] <= styles.BASE["intro_row_max"] <= row["max"]
    assert commands.INTRO_HOOK_ROW_MAX_CHARS == styles.BASE["intro_row_max"] == 20
