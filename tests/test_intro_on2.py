# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Камера группы интро — по БОЛЬШИНСТВУ окна группы, а не по моменту первого слова (BR).

Зачем: группа живёт gMax+0.3+1.0+0.75 секунд. Если кат случился через кадр после первого
слова, почти всё окно группа висит над кадром камеры 2, а нул ей доставался от камеры 1
(INTRO_ON2=0). Камеру считаем по сумме длительностей по окну [ts, te] группы; ничья
(ровно 50/50) — поздней камере.

Фикстура: timeline_subs.xml.gz — первый кат на кадре 443 @60fps (7.3833 с): до него идёт
Камера 1, после — Камера 2.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402

CUT_F = 443                              # первый кат фикстуры, кадр @60fps
CUT = CUT_F / 60.0                       # 7.3833 с


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


def _build(xml, tmp_path, times, splits):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   intro=_intro(times), intro_splits=splits,
                                   style={}, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, times, splits):
    return xml2ae.scene_plan(xml, intro=_intro(times), intro_splits=splits,
                             style={}, disclaimer="", emit=lambda *a: None)


def _on2(jsx):
    return json.loads(re.search(r"var INTRO_ON2=(\[.*?\]);", jsx).group(1))


def test_группа_за_кадр_до_ката_на_кам2(xml_subs, tmp_path):
    """Старт группы за один кадр до ката на Камеру 2 — окно группы почти всё на перебивке:
    и план, и .jsx вешают её на нул камеры 2 (раньше по первому слову доставался нул кам1)."""
    jsx = _build(xml_subs, tmp_path, [[(CUT_F - 1) / 60.0]], splits=[])
    assert _on2(jsx) == [1]
    assert [g["on2"] for g in _plan(xml_subs, [[(CUT_F - 1) / 60.0]], []).get("intro", [])] == [True]


def test_кат_в_самом_конце_окна_остаётся_кам1(xml_subs, tmp_path):
    """Кат в последних ~0.17 с окна группы: большинство окна всё ещё Камера 1 — on2 ложно,
    старое поведение (по первому слову) не поехало. Третья группа целиком на кам2 — истинно."""
    assert _on2(_build(xml_subs, tmp_path, [[1.0], [6.5], [8.3]], splits=[1, 2])) == [0, 0, 1]
