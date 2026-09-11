# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общий сдвиг вставок кам1 (задание CB): парный к «Вставки кам2 по вертикали».

Жалоба: «Почему нет общего ползунка с вставками кам1 общего в стилях. На кам2 есть,
а на кам1 нету рядом». Правка: ключи стиля insert_c1_x/insert_c1_y (px) складываются
с ключами _cam1_pos_keys в scene_plan, превью рисует готовое из плана — второй
копии формулы нет. Дефолт 0/0 обязан давать старый .jsx байт в байт (golden).

Здесь:
  * сдвиг 0/0 — собранный .jsx не отличается от сборки без этих ключей вовсе;
  * insert_c1_y=120 — ключи позиции вставки кам1 в плане уехали ровно на 120 вниз;
  * сдвиг не трогает вставки кам2 (у них своя точка покоя).
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _ins(style, iy):
    """Одна вставка: стиль, личный сдвиг y — всё остальное фиксировано."""
    return [dict(type="photo", media="a.jpg",
                 start_s=1.0, start_f=0, dur_s=2.0, dur_f=0,
                 style=style, x=0, y=iy)]


def _build(xml, tmp_path, style_kw):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   inserts=_ins("cam1", 0), style=style_kw,
                                   disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style_kw):
    return xml2ae.scene_plan(xml, inserts=_ins("cam1", 0), style=style_kw,
                             disclaimer="")


def test_сдвиг_ноль_не_меняет_jsx_побайтово(xml_subs, tmp_path):
    """insert_c1_x/y = 0 == ключей в стиле нет вовсе: .jsx байт в байт (golden)."""
    a = _build(xml_subs, tmp_path, {"insert_style": "cam1"})
    b = _build(xml_subs, tmp_path,
               {"insert_style": "cam1", "insert_c1_x": 0, "insert_c1_y": 0})
    assert a == b


def test_сдвиг_y_120_в_плане_уезжает_на_120(xml_subs):
    """Ключи позиции вставки кам1 в плане сдвинуты ровно на 120 вниз."""
    base = _plan(xml_subs, {"insert_style": "cam1"})["inserts"][0]["anim"]["position"]
    shifted = _plan(xml_subs, {"insert_style": "cam1", "insert_c1_y": 120}
                    )["inserts"][0]["anim"]["position"]
    # ключи идут парами [время, [x, y]] — время то же, y сдвинут на 120
    assert [t for t, _ in shifted] == [t for t, _ in base]
    assert [y for _, (x, y) in shifted] == [y + 120 for _, (x, y) in base]
    # сам сдвиг ненулевой — тест проверяет реальную разницу, а не тождество
    assert [y for _, (_, y) in base] != [y + 120 for _, (_, y) in base]
    assert shifted[0] == [base[0][0], [0, base[0][1][1] + 120]]


def test_сдвиг_x_120_в_плане_уезжает_на_120(xml_subs):
    base = _plan(xml_subs, {"insert_style": "cam1"})["inserts"][0]["anim"]["position"]
    shifted = _plan(xml_subs, {"insert_style": "cam1", "insert_c1_x": 120}
                    )["inserts"][0]["anim"]["position"]
    assert [x for _, (x, _) in shifted] == [x + 120 for _, (x, _) in base]


def test_сдвиг_не_трогает_вставки_кам2(xml_subs):
    """У кам2 своя точка покоя (insert_c2_y): сдвиг кам1 на неё не влияет."""
    base = _plan(xml_subs, {"insert_style": "cam2"})["inserts"][0]
    shifted = _plan(xml_subs, {"insert_style": "cam2", "insert_c1_y": 120}
                    )["inserts"][0]
    assert base == shifted
