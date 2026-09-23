# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тень ПРЕКОМПА интро — своя у камеры 1 и камеры 2.

Раньше шаблон вешал на каждый прекомп интро жёсткую белую тень `dropShadow(iL, 68)`
(цвет [1,1,1], направление 135, дистанция 0, мягкость 287) — одну на обе камеры.
Ключи стиля `intro_comp_shadow*` (камера 1) и `intro_comp_shadow2*` (камера 2) задают
свой цвет и непрозрачность; группа берёт значения ТОЙ камеры, на которой висит
(INTRO_ON2[gI] — тот же признак, что у нула «интро на кам2»). Превью рисует ту же тень
фильтром `drop-shadow` по числу из плана (plan.intro[].shadow).

Дефолты (белая, 68) НЕ меняют .jsx: подстановка — ровно прежняя строка dropShadow(iL, 68),
объявление introCompShadow пустое (побайтовость стережёт golden_geometry.jsx).

Фикстура: timeline_subs.xml.gz — первый кат на кадре 443 @60fps (7.3833 с): до него
Камера 1, после — Камера 2.
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


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _intro(times):
    """times — список групп, каждая — список моментов слов (сек)."""
    return [dict(words=["СЛОВО"], color="white", times=list(t)) for t in times]


def _build(xml, tmp_path, times, splits, style):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   intro=_intro(times), intro_splits=splits,
                                   style=style, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, times, splits, style):
    return xml2ae.scene_plan(xml, intro=_intro(times), intro_splits=splits,
                             style=style, disclaimer="", emit=lambda *a: None)


# Первая группа — Камера 1 (1.0 с), третья — Камера 2 (8.3 с, после ката 7.3833 с).
TIMES = [[1.0], [6.5], [8.3]]
SPLITS = [1, 2]


def test_дефолт_прежний_dropshadow(xml_subs, tmp_path):
    """Все четыре ключа на дефолтах: .jsx несёт ровно прежнюю строку dropShadow(iL, 68)
    и никакого introCompShadow."""
    jsx = _build(xml_subs, tmp_path, TIMES, SPLITS, {})
    assert "dropShadow(iL, 68);" in jsx
    assert "introCompShadow" not in jsx


def test_камера_2_чёрная_тень_240(xml_subs, tmp_path):
    """Стиль с чёрной тенью 240 у камеры 2: в .jsx объявлена introCompShadow с выбором
    по INTRO_ON2[gI] и числами камеры 2; в плане у группы камеры 2 — свои значения,
    у группы камеры 1 — дефолтные."""
    style = {"intro_comp_shadow2_fill": [0, 0, 0], "intro_comp_shadow2_op": 240}
    groups = _plan(xml_subs, TIMES, SPLITS, style)["intro"]
    assert [g["on2"] for g in groups] == [False, False, True]
    assert groups[0]["shadow"] == {"fill": [1, 1, 1], "op": 68}
    assert groups[2]["shadow"] == {"fill": [0, 0, 0], "op": 240}

    jsx = _build(xml_subs, tmp_path, TIMES, SPLITS, style)
    assert "introCompShadow(iL, INTRO_ON2[gI]);" in jsx
    assert re.search(r"function introCompShadow\(L, on2\)", jsx)
    assert "[0,0,0]" in jsx and "240" in jsx


def test_фронт_превью_рисует_тень():
    """Сторож фронта: introGroupWindows протаскивает shadow из плана, ipvIntro ставит
    на #ipvintro фильтр drop-shadow (приближение AE Drop Shadow, дистанция 0)."""
    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
              encoding="utf-8").read()
    assert re.search(r"shadow:g\.shadow", js), (
        "introGroupWindows обязан протащить shadow из плана")
    assert re.search(r"filter='drop-shadow\(", js), (
        "ipvIntro обязан ставить filter: drop-shadow для тени прекомпа")
