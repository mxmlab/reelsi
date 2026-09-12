# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Автофит интро считает план (задание BP).

Раньше порог автофита жил в AE-шаблоне и мерился от ширины прекомпа (INTRO_WIDE×3),
а зум Камеры 1 в расчёт не входил вовсе — длинная строка вылезала из кадра задолго
до срабатывания. Теперь ds считает scene_plan: ширина строки тем же шрифтом (поля
file/var из fonts.py), зум — МАКСИМУМ на окне группы, а шаблон и превью берут
готовое ds и ничего не досчитывают.

Здесь:
  * широкая строка при зуме 160%% ужимается так, что lineW·0.968·(ds/100)·G·Z
    не больше W·0.92, а при зуме 100%% ds больше (зум входит в расчёт);
  * короткие строки план не трогает (ds остаётся 100);
  * ручной gs — верхняя граница: автофит только уменьшает;
  * шрифта нет в системе — группу не трогаем (ужать по неизвестной ширине хуже).
Шрифт собран программно (все «A» шириной 1 em), машина теста от системы не зависит.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import fonts  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_FIT_W, _zoom_max  # noqa: E402

W = 1080               # ширина кадра фикстуры timeline_subs.xml.gz
FSIZE = 140            # _fsize = max(60, int(1080*0.13))
LIMIT = W * INTRO_FIT_W


def _build_font(tmp_path):
    """Крошечный статичный шрифт: все «A» шириной 1000/1000 em, пробел 300/1000 em."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    fb = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "space", "A"]
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({0x20: "space", 0x41: "A"})
    glyf = {}
    for name in glyphs:
        pen = TTGlyphPen(None)
        glyf[name] = pen.glyph()
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({"space": (300, 0), "A": (1000, 0), ".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestSans", "styleName": "Regular",
                       "uniqueFontIdentifier": "TestSans", "fullName": "TestSans",
                       "psName": "TestSans-Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupMaxp()
    fb.setupHead()
    path = str(tmp_path / "TestSans.ttf")
    fb.save(path)
    return path


@pytest.fixture()
def font(tmp_path, monkeypatch):
    """Шрифт с известными ширинами зарегистрирован в fonts.py (file/var уже там)."""
    _build_font(tmp_path)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fonts.list_fonts(refresh=True)
    yield "TestSans-Regular"
    fonts._CACHE = None


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml_subs, words, zoom, gs=None, style=None, accent=False):
    return xml2ae.scene_plan(xml_subs, disclaimer="", intro_riser=False,
                             intro=[dict(words=words, color="white",
                                         times=[1.0], gs=gs,
                                         accent=accent)],
                             style=dict(style or {}, font="TestSans-Regular"),
                             cam1_scale=[(0, zoom), (300, zoom)])


def test_long_word_fits_at_160_and_looser_at_100(font, xml_subs):
    """Строка заведомо шире кадра (10 «A» × 140 px = 1400 px): на зуме 160%% ds ужат
    ровно до равенства lineW·0.968·(ds/100)·G·Z = W·0.92, на зуме 100%% ds больше."""
    words = ["A" * 10]
    ds160 = _plan(xml_subs, words, 160)["intro"][0]["ds"]
    ds100 = _plan(xml_subs, words, 100)["intro"][0]["ds"]

    assert ds160 < 100, "широкая строка обязана ужаться"
    assert ds100 > ds160, "зум входит в расчёт: на 100%% ужим слабее, чем на 160%%"

    # равенство выполнено точно (в пределах округления ds)
    linew = fonts.text_width("TestSans-Regular", " ".join(words), FSIZE)
    for ds, z in ((ds160, 160), (ds100, 100)):
        prod = linew * 0.968 * (ds / 100) * (z / 100)
        assert prod <= LIMIT + 1e-6, "строка с зумом обязана поместиться в 0.92·W"

    # ds головной строки = ds группы: шаблон читает GRP[0].ds, превью — plan.intro[].ds
    g = _plan(xml_subs, words, 160)["intro"][0]
    assert g["lines"][0]["ds"] == round(g["ds"], 4)


def test_short_word_unchanged(font, xml_subs):
    """Короткое слово («A» = 140 px даже с зумом 160%% влезает): план не трогает
    ds — ни у группы, ни на головной строке (дефолт 100 не пишется)."""
    g = _plan(xml_subs, ["A"], 160)["intro"][0]
    assert g["ds"] == 100
    assert "ds" not in g["lines"][0]


def test_manual_gs_overrides_autofit(font, xml_subs):
    """Рука сильнее автофита (задание CF): если gs != 100, группу масштабировали
    вручную — автофит не применяется. При gs=200 масштаб остаётся 200 (не урезается
    до 45.8), при gs=30 — 30; при gs=100 или None — автофит сжимает широкую строку."""
    # gs = 200: увеличение рукой сохраняется
    g200 = _plan(xml_subs, ["A" * 10], 160, gs=200)["intro"][0]
    assert g200["ds"] == 200
    assert g200["lines"][0]["ds"] == 200

    # gs = 30: уменьшение рукой сохраняется
    g30 = _plan(xml_subs, ["A" * 10], 160, gs=30)["intro"][0]
    assert g30["ds"] == 30
    assert g30["lines"][0]["ds"] == 30

    # gs = 100: автофит работает и ужимает широкую строку
    g100 = _plan(xml_subs, ["A" * 10], 160, gs=100)["intro"][0]
    assert g100["ds"] < 100
    assert g100["lines"][0]["ds"] == round(g100["ds"], 4)


def test_missing_font_disables_fit(font, xml_subs, monkeypatch):
    """Шрифта нет в системе — ширины нет: группу не трогаем, ds как был."""
    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [])
    fonts.list_fonts(refresh=True)
    g = _plan(xml_subs, ["A" * 10], 160)["intro"][0]
    assert g["ds"] == 100


def test_text_width_none_for_unknown_font(font):
    assert fonts.text_width("NoSuchFont-Regular", "A", 140) is None
    assert fonts.text_width("TestSans-Regular", "A", 140) == 140


def test_zoom_max_is_max_on_window():
    """Зум берётся МАКСИМУМОМ по окну группы, а не в момент старта: пик внутри окна."""
    assert _zoom_max([(0, 160), (300, 160)], 60, 0.0, 3.05) == 160
    assert _zoom_max([(0, 100), (100, 200), (200, 100)], 60, 0.5, 2.0) == 200
    # граница окна внутри сегмента — интерполяция, пик за окном не учитывается
    assert _zoom_max([(0, 100), (100, 200)], 60, 0.0, 1.5) == 190
    assert _zoom_max([(0, 100), (100, 200)], 60, 0.0, 0.25) == 115
    # ключей нет — зум не мешает
    assert _zoom_max([], 60, 0.0, 1.0) == 100


def test_zoom_max_holds_on_jump_cut():
    """Джамп-кат (hold): значение держится от своего ключа до следующего — линейная
    интерполяция занизила бы пик, и длинная строка вылезла бы в AE."""
    # ключ 182 активен весь кадровый участок [0,62): на окне [0.5, 0.9] зум всё ещё 182
    assert _zoom_max([(0, 182), (62, 100)], 60, 0.5, 0.9, hold=True) == 182
    # после вступления ключа 100 зум уже 100 (максимум по активным не позднее конца окна)
    assert _zoom_max([(0, 182), (62, 100)], 60, 0.5, 2.0, hold=True) == 182
    assert _zoom_max([(0, 182), (62, 140), (120, 100)], 60, 1.1, 2.0, hold=True) == 140
    # без hold — то же окно считается интерполяцией (проверка, что флаг реально влияет)
    interp = _zoom_max([(0, 182), (62, 100)], 60, 0.5, 0.9, hold=False)
    assert 100 < interp < 182, "интерполяция между 182 и 100 не должна дать пик 182"
