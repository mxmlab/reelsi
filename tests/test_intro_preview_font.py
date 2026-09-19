# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шрифт строк интро и межсловный отступ в превью (задание «превью рисует жёлтые строки
не тем шрифтом и не тем межсловным отступом»).

Что ловим:
  * лесенка шрифта строки интро живёт ОДНИМ местом в Python (_intro_line_font) и её
    используют автофит и plan.intro[].fonts; превью читает готовое. На реальном ролике
    жёлтая строка МАКСИМАЛЬНО (кегль 140) выходила в превью 1207.6 px против 851.6 в AE
    (+42 %) — у жёлтой строки не было запасного s.font, и она рисовалась системным
    шрифтом с fontWeight 800;
  * межсловный отступ: AE ставит между словами пробел ШРИФТА (template.py:614-621),
    превью добавляло margin-left:.32em — на строке «и сделать это» это +9 % (537.4
    против 491.0 в AE).
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

from core import xml2ae  # noqa: E402
from core.xml2ae.build import _intro_line_font  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml_subs, style, lines):
    return xml2ae.scene_plan(xml_subs, style=style,
                             intro=[{"words": ln["words"], "color": ln["color"],
                                     "times": [1.0]} for ln in lines])


def test_intro_line_font_ladder():
    """Единственная лесенка шрифта строки интро: непустой accent_font перекрывает всё;
    жёлтая строка — intro_hl_font_ps; white/accent/custom — intro_font_ps.
    Баг был в том, что автофит пропускал hl_font и мерил ширину не тем шрифтом."""
    f_ps, hl_ps = "IntroFont-Regular", "IntroHlFont-Bold"
    # accent_font перекрывает и цвет, и оба шрифта интро (в т.ч. у жёлтой строки)
    assert _intro_line_font({"accent_font": "Accent-Bold", "color": "yellow"},
                            f_ps, hl_ps) == "Accent-Bold"
    # accent_font из одних пробелов — как пустой (strip), лесенка идёт дальше
    assert _intro_line_font({"accent_font": "  ", "color": "yellow"},
                            f_ps, hl_ps) == hl_ps
    # жёлтая — intro_hl_font_ps
    assert _intro_line_font({"color": "yellow"}, f_ps, hl_ps) == hl_ps
    # остальные цвета — intro_font_ps
    for color in ("white", "accent", "custom"):
        assert _intro_line_font({"color": color}, f_ps, hl_ps) == f_ps
    # строки без цвета тоже intro_font_ps (жёлтой считается только color=="yellow")
    assert _intro_line_font({}, f_ps, hl_ps) == f_ps


def test_plan_fonts_use_hl_font_for_yellow(xml_subs):
    """Стиль {"font": "FontA", "hl_font": "FontB"} без intro_font/intro_hl_font:
    у жёлтой строки шрифт FontB, у белой FontA. Это ровно пропущенный hl_font:
    в AE intro_hl_font_ps = hl_font = FontB (build.py:473-476)."""
    plan = _plan(xml_subs, {"font": "FontA", "hl_font": "FontB"},
                 [{"words": ["МАКСИМАЛЬНО"], "color": "yellow"},
                  {"words": ["БЕЗОПАСНО"], "color": "white"}])
    fonts0 = plan["intro"][0]["fonts"]
    assert fonts0[0] == "FontB", "жёлтая строка обязана взять hl_font"
    assert fonts0[1] == "FontA", "белая строка — font"


def test_plan_fonts_fall_back_to_font(xml_subs):
    """Стиль {"font": "FontA"} без hl_font: у жёлтой строки шрифт FontA, а не пусто.
    Сегодняшний баг превью: жёлтая строка уходила в системный шрифт с fontWeight 800."""
    plan = _plan(xml_subs, {"font": "FontA"},
                 [{"words": ["МАКСИМАЛЬНО"], "color": "yellow"}])
    assert plan["intro"][0]["fonts"][0] == "FontA"


def test_plan_fonts_parallel_lines_and_not_in_jsx_lines(xml_subs):
    """fonts идёт параллельно lines (по элементу на строку), а в сами строки поле не
    кладётся: lines уезжают в .jsx как INTRO_GROUPS и обязаны остаться прежними (golden)."""
    plan = _plan(xml_subs, {"font": "FontA", "hl_font": "FontB"},
                 [{"words": ["МАКСИМАЛЬНО"], "color": "yellow"},
                  {"words": ["и", "сделать", "это"], "color": "white"}])
    for g in plan["intro"]:
        assert len(g["fonts"]) == len(g["lines"]), "шрифт на каждую строку группы"
        assert all(isinstance(ps, str) and ps for ps in g["fonts"])
        for ln in g["lines"]:
            assert "fonts" not in ln, "в .jsx-строки поле fonts не попадает"


def test_front_reads_fonts_from_plan_and_space_text_node():
    """Сторож фронта: превью берёт шрифт из plan.intro[].fonts (не досчитывает),
    introGroupWindows передаёт fonts, ipvIntro ставит между словами текстовый узел-пробел."""
    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
              "r", encoding="utf-8").read()
    # ZY положило в тот же объект окна ещё ключи lx/lk (большое слева) — они идут ПОСЛЕ
    # fonts, поэтому требуем не «fonts последним ключом», а наличие ключа fonts и закрытие
    # объекта окна: протаскивание fonts проверяется ровно так же строго.
    assert re.search(r"\.map\(g=>\(\{lines:g\.lines,[^}]*fonts:g\.fonts[^}]*\}\)\)", js), \
        "introGroupWindows обязан протащить fonts из плана"
    assert "IPV.intro[gi].fonts" in js, "ipvIntro обязан читать шрифт из плана"
    assert "document.createTextNode(' ')" in js, \
        "между словами — настоящий пробел шрифта, как в AE"


def test_app_css_no_em_word_gap():
    """В .ipvintro нет margin-left у span+span: отступ между словами — текстовый узел-пробел
    из ipvIntro (как пробел шрифта в AE), а .32em давал +9 % на строке из трёх слов.
    Правило .ipvintro span{display:inline-block;opacity:0} при этом остаётся."""
    css = open(os.path.join(ROOT, "static", "app.css"), "r", encoding="utf-8").read()
    for rule in re.findall(r"\.ipvintro[^{]*\{[^}]*\}", css):
        assert "span+span" not in rule, "межсловный отступ .32em убран"
    assert re.search(r"\.ipvintro\s+span\{[^}]*display:inline-block", css), \
        "правило .ipvintro span{display:inline-block;opacity:0} не трогали"
