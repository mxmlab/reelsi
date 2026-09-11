# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставка в стиле «Кам 1», попавшая на перебивку: свой нул + общий сдвиг точки покоя.

Баг, ради которого тест: при принудительном стиле «Кам 1» ВСЕ фото-вставки висели на
нуле «вставки кам1» (он привязан к Null Камеры 1). На перебивке Камеры 2 такая вставка
всё равно ездила и меняла размер вместе с зум-дрейфом Камеры 1, которой в кадре нет,
и подвинуть её было нечем. Теперь у неё отдельный СВОБОДНЫЙ нул и пара INS_C1_ON2_X/Y.

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


def _build(xml, tmp_path, style):
    ins = [dict(type="photo", media=str(tmp_path / "a.jpg"), start_s=T_CAM1, start_f=0,
                dur_s=2.0, dur_f=0),
           dict(type="photo", media=str(tmp_path / "b.jpg"), start_s=T_CAM2, start_f=0,
                dur_s=2.0, dur_f=0)]
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), inserts=ins,
                                   style=style, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _inserts(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]); //", jsx).group(1))


def test_флаг_oncam2_только_у_вставки_на_перебивке(xml_subs, tmp_path):
    a, b = _inserts(_build(xml_subs, tmp_path, {"insert_style": "cam1"}))
    assert (a["style"], a["oncam2"]) == ("cam1", False)
    assert (b["style"], b["oncam2"]) == ("cam1", True)


def test_на_авто_стиле_флага_нет_никогда(xml_subs, tmp_path):
    """auto над перебивкой даёт стиль cam2 — вылета из-за спины там и не бывает."""
    a, b = _inserts(_build(xml_subs, tmp_path, {"insert_style": "auto"}))
    assert [a["style"], b["style"]] == ["cam1", "cam2"]
    assert not a["oncam2"] and not b["oncam2"]


def test_нул_на_перебивке_не_привязан_к_камере_1(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, {"insert_style": "cam1"})
    assert 'insNull1b.name="вставки кам1 на кам2"' in jsx
    assert "insNull1b.parent" not in jsx          # именно это и отвязывает от зума кам1
    assert "insNull1.parent=cam1null" in jsx      # обычные cam1-вставки как были


def test_сдвиг_из_стиля_доезжает_до_jsx(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 {"insert_style": "cam1", "insert_c1on2_x": 120, "insert_c1on2_y": -80})
    assert "var INS_C1_ON2_X = 120, INS_C1_ON2_Y = -80;" in jsx


def test_без_настройки_сдвиг_нулевой(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, {"insert_style": "cam1"})
    assert "var INS_C1_ON2_X = 0, INS_C1_ON2_Y = 0;" in jsx
