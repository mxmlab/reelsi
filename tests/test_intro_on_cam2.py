# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Интро, попавшее на перебивку, висит на своём нуле «интро на кам2».

Зачем: все интро-прекомпы висели на ОДНОМ нуле «интро». На перебивке кадр другой
(человек стоит иначе), и текст за спиной просится ниже — но опустить его можно было
только вместе со всем интро на Камере 1. Теперь у групп, появляющихся на кам2, свой
нул с добавкой intro_y2, и правится это разом.

Фикстура: timeline_subs.xml.gz — Камера 2 выключена на первом клипе (кадры 0..443) и
включена на втором (443..651 @60fps), т.е. 1-я секунда идёт по кам1, 8.3-я по кам2.
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

T_CAM1, T_CAM2 = 1.0, 8.3          # секунды: в кадре Камера 1 / перебивка (см. докстринг)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, style=None):
    # две группы интро: первая на Камере 1, вторая (акцент по середине) — на перебивке
    intro = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
             dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])]
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _on2(jsx):
    return json.loads(re.search(r"var INTRO_ON2=(\[.*?\]);", jsx).group(1))


def test_флаг_только_у_группы_на_перебивке(xml_subs, tmp_path):
    assert _on2(_build(xml_subs, tmp_path)) == [0, 1]


def test_нул_создаётся_и_привязан_как_обычный(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path)
    assert 'introNull2.name="интро на кам2"' in jsx
    assert "introNull2.parent=cam1null" in jsx      # такой же, как «интро»: едет за зумом кам1
    assert "var iPar = (INTRO_ON2[gI] ? introNull2 : introNull);" in jsx


def test_сдвиг_из_стиля_доезжает_до_jsx(xml_subs, tmp_path):
    assert "var INTRO_Y2=90;" in _build(xml_subs, tmp_path, {"intro_y2": 90})


def test_без_настройки_сдвиг_нулевой(xml_subs, tmp_path):
    assert "var INTRO_Y2=0;" in _build(xml_subs, tmp_path)
