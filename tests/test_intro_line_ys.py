# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вертикальная раскладка строк интро считается в Python (задание A1).

Шаг строк жил жёсткими пикселями в шаблоне (LINE_STEP=160, до строки заднего плана
160·back_step) и о шрифте не знал: после смены шрифта малые строки наезжали на строку
над ними, и пользователь раздвигал их руками в AE. Его шаг совпал с «хвост вниз верхней
строки + высота букв нижней + ~4 px» — теперь это и есть формула: высоту букв даёт
`fonts.ink_extent` (контуры глифов через BoundsPen), зазор — ключ стиля `back_gap`.
Второе: блок центрировался по числу строк, и добавленная строка поднимала первую —
теперь есть якорь (`intro_anchor` / `intro_anchor2`): «по центру» или «на первой строке».

Здесь:
  * `ink_extent` по контурам известной высоты: «A» 0..700, «g» −200..500 при upm 1000;
  * без шрифта `intro_line_ys` совпадает с прежней формулой шаблона (три раскладки);
  * с шрифтом шаг до строки заднего плана = max(базовый, хвост + высота букв + зазор);
  * якорь «first»: первая строка в h/2 при 1, 2 и 3 строках, iDy — как для одной строки;
  * план: ys у группы, в строках его нет, группа камеры 2 берёт `intro_anchor2`;
  * .jsx: со строками заднего плана есть INTRO_LY и lineY берётся оттуда, без них — нет.

Шрифт собран программно и с контурами известной высоты: машина теста от системных
шрифтов не зависит.
"""
import gzip
import json
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
from core.xml2ae.layout import INTRO_BASE_Y, INTRO_LINE_STEP, _intro_i_dy, intro_line_ys  # noqa: E402

PS = "TestInk-Regular"
T_CAM1, T_CAM2 = 1.0, 8.3        # окна камер в фикстуре: 1-я секунда кам1, 8-я — перебивка


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
    """Шрифт с известными контурами зарегистрирован в fonts.py (file/var уже там)."""
    _build_font(tmp_path)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fonts.list_fonts(refresh=True)
    yield PS
    fonts._CACHE = None


@pytest.fixture()
def no_fonts(monkeypatch):
    """Шрифтов нет вовсе: ink_extent возвращает None, шаг остаётся базовым."""
    monkeypatch.setattr(fonts, "_FONT_DIRS", [])
    fonts.list_fonts(refresh=True)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _ln(word, back=False):
    return {"words": [word], "times": [T_CAM1], **({"back": True} if back else {})}


# ---- 1. ink_extent: высоты букв из контуров -------------------------------------------------

def test_ink_extent_known_heights(font):
    """«A» 0..700 при size 100 → (70, 0); «g» −200..500 → (50, 20): asc — подъём чернил над
    базовой линией, desc — глубина под ней (положительная)."""
    assert fonts.ink_extent(PS, "A", 100) == (70.0, 0.0)
    assert fonts.ink_extent(PS, "g", 100) == (50.0, 20.0)
    assert fonts.ink_extent(PS, "Ag", 100) == (70.0, 20.0)
    # кегль множит линейно
    assert fonts.ink_extent(PS, "g", 200) == (100.0, 40.0)


def test_ink_extent_none_without_font_or_glyph(font):
    """Шрифта нет или глифа нет — None: по неизвестной высоте шаг не считаем."""
    assert fonts.ink_extent("NoSuchFont-Regular", "A", 100) is None
    assert fonts.ink_extent(PS, "Ж", 100) is None


# ---- 2. без шрифта — прежняя формула шаблона ------------------------------------------------

def _today_back(n, backs, back_step, h):
    """Y по сегодняшней формуле шаблона (ветка со строками заднего плана)."""
    steps = [0.0]
    tot = 0.0
    for i in range(1, n):
        tot += INTRO_LINE_STEP * (back_step if backs[i] else (0.75 if backs[i - 1] else 1.0))
        steps.append(tot)
    cY = (h / 2 - (n - 1) * 60) if (not backs[0] and n > 1) else (h / 2 - tot / 2)
    return [round(cY + s, 2) for s in steps]


def _today_noback(n, h):
    """Y по сегодняшней формуле шаблона (ветка без строк заднего плана)."""
    cY = h / 2 - (n - 1) / 2 * INTRO_LINE_STEP
    return [round(cY + i * INTRO_LINE_STEP, 2) for i in range(n)]


def test_without_font_matches_template_formula(no_fonts):
    """Шрифта нет (ink_extent → None) — intro_line_ys повторяет формулу шаблона в трёх
    раскладках: без back; с back и головой big; с back и головой back."""
    h, fs, bs, bstep, gap = 1920.0, 140.0, 0.69, 0.45, 4.0
    # без строк back в ролике — все шаги LINE_STEP
    lines = [_ln("A"), _ln("A"), _ln("A")]
    ys = intro_line_ys(lines, [None] * 3, fs, bs, bstep, gap, False, "center", h)
    assert ys == _today_noback(3, h)

    # с back и головой big
    lines = [_ln("A"), _ln("A", back=True), _ln("A")]
    ys = intro_line_ys(lines, [None] * 3, fs, bs, bstep, gap, True, "center", h)
    assert ys == _today_back(3, [False, True, False], bstep, h)

    # с back и головой back
    lines = [_ln("A", back=True), _ln("A")]
    ys = intro_line_ys(lines, [None] * 2, fs, bs, bstep, gap, True, "center", h)
    assert ys == _today_back(2, [True, False], bstep, h)


# ---- 3. с шрифтом — шаг по зазору между буквами ----------------------------------------------

def test_back_gap_step_uses_ink(font):
    """Шаг ДО строки заднего плана — max(базовый, хвост верхней строки + высота букв нижней
    + back_gap); шаги строк над big и big под big остаются прежними (160 / 120)."""
    h, fs, bs = 1920.0, 100.0, 0.69
    # верх «g» (хвост 20), низ — back «A» (0.69·70 = 48.3): 20 + 48.3 + 4 = 72.3 > 72
    ys = intro_line_ys([_ln("g"), _ln("A", back=True)], [PS, PS], fs, bs, 0.45, 4.0,
                       True, "center", h)
    assert round(ys[1] - ys[0], 2) == 72.3, "зазор между буквами не попал в шаг"

    # хвоста нет (верх «A»: 0 + 48.3 + 4 = 52.3 < 72) — базовый шаг строки заднего плана
    ys = intro_line_ys([_ln("A"), _ln("A", back=True)], [PS, PS], fs, bs, 0.45, 4.0,
                       True, "center", h)
    assert round(ys[1] - ys[0], 2) == round(INTRO_LINE_STEP * 0.45, 2)

    # зазор не влияет на шаги без строки заднего плана: big под big — 160, после back — 120
    ys = intro_line_ys([_ln("A"), _ln("A")], [PS, PS], fs, bs, 0.45, 4.0, True, "center", h)
    assert round(ys[1] - ys[0], 2) == INTRO_LINE_STEP
    ys = intro_line_ys([_ln("A", back=True), _ln("A")], [PS, PS], fs, bs, 0.45, 4.0,
                       True, "center", h)
    assert round(ys[1] - ys[0], 2) == round(INTRO_LINE_STEP * 0.75, 2)

    # зазор выключен (None) — базовый шаг, как в шаблоне
    ys = intro_line_ys([_ln("g"), _ln("A", back=True)], [PS, PS], fs, bs, 0.45, None,
                       True, "center", h)
    assert round(ys[1] - ys[0], 2) == round(INTRO_LINE_STEP * 0.45, 2)


# ---- 4. якорь «first» -----------------------------------------------------------------------

def test_anchor_first_keeps_first_line(font):
    """Якорь «first»: первая строка стоит в h/2 при 1, 2 и 3 строках, остальные — ниже."""
    h, fs, bs = 1920.0, 100.0, 0.69
    for n in (1, 2, 3):
        lines = [_ln("A") for _ in range(n)]
        ys = intro_line_ys(lines, [PS] * n, fs, bs, 0.45, 4.0, False, "first", h)
        assert ys[0] == h / 2, f"{n} строк: первая строка уехала из h/2"
        assert ys == [round(h / 2 + i * INTRO_LINE_STEP, 2) for i in range(n)]


def _plan(xml_subs, intro, splits=None, style=None):
    return xml2ae.scene_plan(xml_subs, disclaimer="", intro_riser=False, intro=intro,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS))


def test_plan_anchor_first_counts_one_line_for_idy(font, xml_subs):
    """При якоре «first» iDy в плане считается как для ОДНОЙ строки (верх блока не
    поднимается) — y группы из трёх строк совпадает с y группы из одной."""
    three = [dict(words=["A"], color="white", times=[T_CAM1 + i * 0.5]) for i in range(3)]
    one = [dict(words=["A"], color="white", times=[T_CAM1])]
    p3_first = _plan(xml_subs, three, style={"intro_anchor": "first"})
    p1_center = _plan(xml_subs, one)
    p3_center = _plan(xml_subs, three)
    h = p3_first["h"]
    assert p3_first["intro"][0]["y"] == p1_center["intro"][0]["y"]
    assert p3_first["intro"][0]["y"] == round(-INTRO_BASE_Y + _intro_i_dy(h, 1, 100), 2)
    assert p3_center["intro"][0]["y"] == round(-INTRO_BASE_Y + _intro_i_dy(h, 3, 100), 2)
    assert p3_center["intro"][0]["y"] != p3_first["intro"][0]["y"], \
        "без якоря «first» третья строка обязана поднять блок"


# ---- 5. план: ys у группы, якорь камеры 2 ---------------------------------------------------

def test_plan_ys_per_group_and_anchor2(font, xml_subs):
    """У каждой группы ys по числу строк, в строках ключа ys нет; группа, попавшая на
    перебивку, берёт intro_anchor2 (камера 1 при этом остаётся по центру)."""
    intro = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["g"], color="white", times=[T_CAM1 + 0.5]),
             dict(words=["A"], color="white", times=[T_CAM2]),
             dict(words=["g"], color="white", times=[T_CAM2 + 0.5])]
    plan = _plan(xml_subs, intro, splits=[2],
                 style={"intro_anchor": "center", "intro_anchor2": "first"})
    h = plan["h"]
    assert len(plan["intro"]) == 2
    for g in plan["intro"]:
        assert len(g["ys"]) == len(g["lines"])
        for ln in g["lines"]:
            assert "ys" not in ln, "ys уехал в строка: INTRO_GROUPS обязан остаться прежним"

    cam1 = [g for g in plan["intro"] if not g["on2"]][0]
    cam2 = [g for g in plan["intro"] if g["on2"]][0]
    assert cam2["ys"][0] == h / 2, "группа камеры 2 не взяла intro_anchor2"
    assert cam1["ys"][0] != h / 2, "группа камеры 1 взяла не свой якорь"


# ---- 6. .jsx --------------------------------------------------------------------------------

def _build(xml_subs, tmp_path, intro, style=None):
    os.makedirs(str(tmp_path), exist_ok=True)
    path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out.jsx"),
                                   intro=intro, intro_splits=[],
                                   style=dict(style or {}, font=PS),
                                   disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def test_jsx_uses_intro_ly_with_back_lines(font, xml_subs, tmp_path):
    """Есть строки заднего плана — в .jsx уезжает готовая раскладка INTRO_LY, и lineY
    берётся из неё: шаг знает высоту букв шрифта, шаблону это не сосчитать."""
    intro = [dict(words=["g"], color="white", times=[T_CAM1]),
             dict(words=["A"], color="white", times=[T_CAM1 + 0.5], back=True)]
    # back_case=as-is: буква строки заднего плана остаётся «A» — её высоту и меряем
    jsx = _build(xml_subs, tmp_path, intro, style={"back_case": "as-is"})
    assert re.search(r"var INTRO_LY=\[\[[-\d.]", jsx), "INTRO_LY не объявлен"
    assert "lineY=INTRO_LY[gI][qi]" in jsx, "шаблон считает lineY сам, а не берёт готовое"
    m = re.search(r"var INTRO_LY=(\[.*?\]);\s*//", jsx)
    assert m, "INTRO_LY не разобрался"
    ys = json.loads(m.group(1))
    assert len(ys) == 1 and len(ys[0]) == 2
    # кегль фикстуры 140 (1080·0.13), задний план 140·0.69: шаг = хвост «g» (28) + высота
    # букв «A» (67.62) + зазор 4 = 99.62 — больше базового 160·0.45 = 72, значит он и взят
    assert round(ys[0][1] - ys[0][0], 2) == 99.62


def test_jsx_has_no_intro_ly_without_back(font, xml_subs, tmp_path):
    """Без строк заднего плана и с якорем «center» .jsx прежний: INTRO_LY нет вовсе."""
    intro = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["g"], color="white", times=[T_CAM1 + 0.5])]
    jsx = _build(xml_subs, tmp_path, intro, style={"intro_anchor": "center"})
    assert "INTRO_LY" not in jsx


def test_jsx_has_intro_ly_with_first_anchor(font, xml_subs, tmp_path):
    """Якорь «first» без строк заднего плана: INTRO_LY тоже нужен — раскладка не по центру."""
    intro = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["g"], color="white", times=[T_CAM1 + 0.5])]
    jsx = _build(xml_subs, tmp_path, intro, style={"intro_anchor": "first"})
    assert "var INTRO_LY=" in jsx
    assert "lineY=INTRO_LY[gI][qi]" in jsx
