# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: блок субтитров (этап 1).

Блок субтитров уехал из `scene_plan` в `core/xml2ae/plan_subs.py`. Сторож держит СТЫК
двух дверей одной арифметики: `plan_subs`, вызванный НАПРЯМУЮ на фикстуре
(`tests/fixtures/timeline_subs.xml.gz`), обязан отдать ровно то, что `scene_plan` кладёт
в план, — элементы субтитров (`plan["subs"]`), данные SUBS (`plan["_ae"]["subs"]`) и
данные строк (`plan["_ae"]["sub_loop"]`, он же цикл SUB_ROWS/стопки), — а также
подстановки шаблона и геометрию полосы. Разъедутся — .jsx соберётся не по тому, что
рисует предпросмотр, и увидеть это можно только в AE.

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока (стиль,
разметка жёлтых, режим строк): на разных входах сравнение шло бы вхолостую.

Запуск: python -m pytest tests/test_plan_subs_split.py -q
"""
import copy
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import styles  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.build import _accent_word, _parse_intro_count, read_style  # noqa: E402
from core.xml2ae.plan_subs import SubsInputs, plan_subs  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    """Ролик фикстуры: 1-я секунда — кам1, 8-я — перебивка (та же, что у golden-теста)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _inputs(xml, style=None, highlights=None, hl_breaks=None, hl_count=None, hl_joins=None):
    """SubsInputs ровно такими, какими их собрал бы scene_plan к моменту вызова блока.

    Повторяет только ЧТЕНИЯ scene_plan (разбор XML, резолв стиля, чтение структуры
    стиля `read_style`, отсев индексов разметки) — своей арифметики субтитров здесь нет
    намеренно: иначе сторож проверял бы копию правила, а не стык.
    """
    meta, cams, subs, _xml_inserts = xml2ae.parse_full(xml)
    st = styles.resolve(style)
    hl = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    brk = set(int(x) for x in (hl_breaks or []) if 0 <= int(x) < len(subs))
    cnt = set(int(x) for x in (hl_count or []) if 0 <= int(x) < len(subs))
    joins = set(int(x) for x in (hl_joins or []) if 0 <= int(x) < len(subs)) - brk
    style_values = read_style(st)
    font_ps = style_values.font
    return SubsInputs(
        subs=subs, hl=hl, brk=brk, cnt=cnt, joins=joins,
        font_ps=font_ps, hl_font_ps=style_values.hl_font or font_ps,
        width=meta["w"], height=meta["h"], fps=meta["fps"] or 60, cams=cams,
        word_timings=None, style=style_values,
        accent_word=_accent_word, parse_count=_parse_intro_count)


def _check(xml, **kw):
    """Сверить plan_subs с планом scene_plan; -> (plan, SubsPlan)."""
    plan = xml2ae.scene_plan(xml, emit=lambda *a: None, **kw)
    sp = plan_subs(_inputs(xml, **kw))
    ae = plan["_ae"]
    # данные субтитров: элементы плана и строки циклов SUBS/SUB_ROWS/стопки
    assert sp.subs == plan["subs"]
    assert sp.subs_js == ae["subs"]
    assert sp.sub_loop == ae["sub_loop"]
    # подстановки шаблона
    assert sp.hl_row_decl == ae["hl_row_decl"]
    assert sp.hl_blur_decl == ae["hl_blur_decl"]
    assert sp.hl_blur_fn == ae["hl_blur_fn"]
    assert sp.hl_short_fn == ae["hl_short_fn"]
    # геометрия полосы: её читают план (предпросмотр), шаблон и окна интро
    assert sp.hl_blur_on == plan["hl_blur"]
    assert sp.sub_scale == plan["sub_scale"]
    assert sp.posy == plan["posy"]
    assert sp.hl_step == plan["hl_step"]
    assert sp.hl_rise == plan["hl_rise"]
    assert sp.hl_dur == plan["hl_dur"]
    assert sp.fsize == plan["fsize"]
    assert sp.fsize_base == plan["intro_fsize"]
    if "sub_step" in plan:                     # шаг строк план несёт только в режиме строк
        assert sp.sub_step == plan["sub_step"]
    assert sp.sub_step == round(sp.fsize * 1.18, 2)
    return plan, sp


def test_plan_subs_matches_scene_plan_words(xml_subs):
    """Режим «по слову»: дефолт и полная разметка жёлтых (склейки, разделители, счётчик)."""
    plan, sp = _check(xml_subs)
    assert plan["subs"] and not any(it.get("stack") for it in sp.subs)
    # коротких жёлтых нет — нет ни hlDur/hlRowDur, ни блюра (подстановки пусты)
    assert sp.hl_short_fn == "" and sp.hl_blur_fn == "" and sp.hl_blur_decl == ""

    plan, sp = _check(xml_subs,
                      style={"sub_case": "sentence", "hl_blur": True, "hl_row_anim": "word",
                             "sub_scale": 120, "sub_y": 0.8},
                      highlights=[0, 1, 2, 3, 4, 5], hl_joins=[0, 2], hl_breaks=[4],
                      hl_count=[1])
    # ветки блока задействованы: регистр субтитров и своя длительность короткого жёлтого
    assert sp.hl_blur_fn and sp.hl_blur_decl
    assert "hlDur(" in sp.hl_short_fn
    assert sp.subs[0]["w"] == "Лорем"            # sub_case=sentence применён в блоке


def test_plan_subs_matches_scene_plan_rows(xml_subs):
    """Режим строк: раскладка по строкам, стопка подряд жёлтых, короткие жёлтые."""
    plan, sp = _check(xml_subs,
                      style={"sub_words_per_row": 3, "sub_rows_max": 2,
                             "hl_row_stack": True, "hl_blur": True},
                      highlights=[0, 1, 2, 252], hl_joins=[0], hl_breaks=[2])
    assert "sub_step" in plan                      # шаг строк уезжает в план
    assert any(it.get("stack") for it in sp.subs)  # стопка собрана
    assert "HL_ROW_WORD" in sp.hl_row_decl
    assert "hlDur(" in sp.hl_short_fn and "hlRowDur(" in sp.hl_short_fn


def test_plan_subs_keeps_inputs_intact(xml_subs):
    """Входы не правятся «по месту»: слова и разметка после вызова те же."""
    inp = _inputs(xml_subs, style={"hl_row_stack": True}, highlights=[0, 1, 2, 3],
                  hl_joins=[0], hl_breaks=[2], hl_count=[1])
    snapshot = copy.deepcopy(inp)
    plan_subs(inp)
    assert inp == snapshot
