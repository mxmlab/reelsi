# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дисклеймер подстраивается под шрифт: кегль по ширине, шаг строк по буквам.

Кегль дисклеймера был `int(H·0.0245)` (47 при 1920) и от шрифта не зависел. После смены
шрифта на Oswald-Bold самая длинная строка стала 1194 px при кадре 1080 — за краем, и
пользователь ужимал слой в AE руками до 90% (кегль фактически 42.3). Теперь кегль
считается под долю ширины кадра `layout.DISC_FIT_W = 0.992` (замер: у SF Pro Condensed
при 47 самая длинная строка занимала 1071 px при кадре 1080) и только УМЕНЬШАЕТСЯ —
узкий шрифт дисклеймер не раздувает.

Второе — шаг строк. Пользователь выставил его в AE вручную (45 px вместо авто 56.4), и
это ровно «хвост вниз верхней строки + высота букв нижней + 0.8 px». Зазор живёт ключом
стиля `disc_gap` (None = интервал авто, как сегодня), а шаг считает Python по контурам
глифов (`fonts.ink_extent`, как у строк интро в) и отдаёт в .jsx готовым
`DISC_LEAD`. Применяют его ОБА блока дисклеймера — головной (template.py) и концевой
(disclaimer_end, build.py).

Шрифт собран программно (фикстура из test_intro_line_ys.py): «A» 0..700, «g» −200..500
при upm 1000 — на системные шрифты тест не опирается. Текст дисклеймера задан стилем:
все глифы обязаны быть в этом шрифте.
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import fonts  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import DISC_FIT_W  # noqa: E402

PS = "TestInk-Regular"
BASE_SIZE = 47       # база при 1920: int(1920 * 0.0245)
W_FRAME = 1080       # ширина кадра фикстуры (H = 1920, DISC_Y = 1466)


def _build_font(tmp_path):
    """Крошечный статичный шрифт с контурами известной высоты (upm 1000):
    «A» — прямоугольник 0..700, «g» — −200..500, пробел без контура."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def _rect(x0, y0, x1, y1):
        pen = TTGlyphPen(None)
        pen.moveTo((x0, y0))
        pen.lineTo((x1, y0))
        pen.lineTo((x1, y1))
        pen.lineTo((x0, y1))
        pen.closePath()
        return pen.glyph()

    def _empty():
        return TTGlyphPen(None).glyph()

    fb = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "space", "A", "g"]
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({0x20: "space", 0x41: "A", 0x67: "g"})
    fb.setupGlyf({".notdef": _empty(), "space": _empty(),
                  "A": _rect(0, 0, 500, 700), "g": _rect(0, -200, 500, 500)})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "space": (300, 0),
                               "A": (500, 0), "g": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestInk", "styleName": "Regular",
                       "uniqueFontIdentifier": "TestInk", "fullName": "TestInk",
                       "psName": PS})
    fb.setupOS2()
    fb.setupPost()
    fb.setupMaxp()
    fb.setupHead()
    path = str(tmp_path / "TestInk.ttf")
    fb.save(path)
    return path


@pytest.fixture()
def font(tmp_path, monkeypatch):
    """Шрифт с известными контурами зарегистрирован в fonts.py."""
    _build_font(tmp_path)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fonts.list_fonts(refresh=True)
    yield PS
    fonts._CACHE = None


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml_subs, tmp_path, disclaimer, style=None):
    """Сборка .jsx; текст дисклеймера и шрифт заданы стилем — глифы только тестовые."""
    st = dict(style or {})
    st["disclaimer"] = disclaimer
    st["font"] = PS
    path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out.jsx"),
                                   style=st, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _decl(jsx, name):
    """Число объявления DISC_SIZE=/DISC_LEAD= из .jsx (None, если объявления нет)."""
    m = re.search(name + r"=(-?[\d.]+)", jsx)
    return float(m.group(1)) if m else None


# ---- 1. дефолты: база 47, DISC_LEAD не объявлен ----------------------------------------------

def test_default_style_keeps_base_size_and_no_lead(font, xml_subs, tmp_path):
    """Дефолтный стиль (disc_gap = None): кегль — база 47, DISC_LEAD нет вовсе."""
    jsx = _build(xml_subs, tmp_path, "Ag\nAg")
    assert _decl(jsx, "DISC_SIZE") == BASE_SIZE
    assert _decl(jsx, "DISC_LEAD") is None
    assert "DISC_LEAD" not in jsx, "при интервале авто DISC_LEAD объявлять нельзя"


# ---- 2. кегль по ширине самой длинной строки -------------------------------------------------

def test_wide_line_shrinks_size_short_line_keeps_it(font, xml_subs, tmp_path):
    """Строка шире 0.992·W при кегле 47 — кегль ровно под долю кадра; строка уже — 47."""
    wide = "A" * 50                       # 50 · 500/1000 · 47 = 1175 px > 0.992 · 1080
    w_max = fonts.text_width(PS, wide, BASE_SIZE)
    assert w_max > DISC_FIT_W * W_FRAME, "фикстура перестала быть шире кадра"
    expected = round(BASE_SIZE * DISC_FIT_W * W_FRAME / w_max, 2)
    assert expected == 42.85, "сдвинулась арифметика кегля (ожидали 42.85)"

    jsx = _build(xml_subs, tmp_path, wide + "\nA")
    assert _decl(jsx, "DISC_SIZE") == expected, f"DISC_SIZE={_decl(jsx, 'DISC_SIZE')} != {expected}"
    # узкий шрифт кегль не раздувает: короткая строка остаётся базовой
    assert _decl(_build(xml_subs, tmp_path, "A\nA"), "DISC_SIZE") == BASE_SIZE


# ---- 3. зазор строк: DISC_LEAD по буквам, в обоих блоках --------------------------------------

def test_disc_gap_sets_lead_in_both_disclaimer_blocks(font, xml_subs, tmp_path):
    """disc_gap = 0.8: DISC_LEAD = максимум (desc верхней + asc нижней) по соседним парам
    + зазор при подобранном кегле, и dd.leading=DISC_LEAD стоит в обоих блоках дисклеймера."""
    style = {"disc_gap": 0.8, "disclaimer_end": True}
    jsx = _build(xml_subs, tmp_path, "Ag\nAg", style=style)

    asc, desc = fonts.ink_extent(PS, "Ag", BASE_SIZE)
    expected = round(desc + asc + 0.8, 2)          # единственная соседняя пара
    assert expected == 43.1, "сдвинулась арифметика интервала (ожидали 43.1)"
    assert _decl(jsx, "DISC_SIZE") == BASE_SIZE, "кегль ужался там, где не должен"
    assert _decl(jsx, "DISC_LEAD") == expected
    assert jsx.count("DISC_LEAD=") == 1, "DISC_LEAD объявляется не один раз"
    # головной блок (template.py) и концевой (disclaimer_end, build.py) — оба
    assert jsx.count("dd.leading=DISC_LEAD") == 2, \
        "dd.leading=DISC_LEAD нет в обоих блоках дисклеймера"


# ---- 4. дефолтный .jsx не меняется ------------------------------------------------------------

def test_golden_fixture_disclaimer_untouched():
    """Эталон golden_geometry.jsx не тронут: DISC_SIZE=47, ни DISC_LEAD, ни dd.leading.
    Полную побайтовость эталона стережёт tests/test_geometry_python.test_golden_jsx."""
    golden = open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                  encoding="utf-8-sig").read()
    assert "DISC_SIZE=47" in golden
    assert "DISC_LEAD" not in golden
    assert "dd.leading" not in golden
