# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Размытие на старте и дисклеймер в конце.

В разобранном проекте оба делаются руками в каждом ролике: Adjustment Layer с
Gaussian Blur 25→0 за 0.52 с и копия головного дисклеймера на конец контента.
Ключи стиля: start_blur (0 = выкл), start_blur_dur (0.52), disclaimer_end (галка).

Здесь:
  * golden: выключено (дефолт) — .jsx побайтово прежний (плейсхолдеры шаблона пусты,
    композиция не удлиняется);
  * размытие: Adjustment Layer поверх всего, ключи start_blur->0 за start_blur_dur;
  * хвостовой дисклеймер: копия головного на конец контента (inPoint=DUR), держится
    1 с, гаснет за 0.35, композиция удлинена на его длительность.
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


def _build(xml, tmp_path, style=None, disclaimer="ТЕКСТ ДИСКЛЕЙМЕРА"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   style=style or {}, disclaimer=disclaimer,
                                   emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def test_выключено_дефолт_jsx_прежний(xml_subs, tmp_path):
    """Стиль без новых ключей и с ними-но-выключенными — .jsx побайтово прежний."""
    plain = _build(xml_subs, tmp_path, style={})
    off = _build(xml_subs, tmp_path, style={
        "start_blur": 0, "start_blur_dur": 0.52, "disclaimer_end": False})
    assert off == plain
    # шаблон не несёт ни размытия, ни хвостового дисклеймера, композиция не удлинена
    assert "adjustmentLayer=true" not in plain
    assert "dle.inPoint=DUR" not in plain
    assert "Math.max(DUR,1), FPS)" in plain


def test_размытие_на_старте(xml_subs, tmp_path):
    """Корректирующий слой — СОЛИД с флагом, а эффект — «ADBE Gaussian Blur 2».

    Два промаха, которые не ловит ни один дешёвый чек и которые роняют сборку прямо
    в AE (первая же строка после ошибки не выполняется, проект остаётся недособранным):
      * `addAdjustmentLayer` в API After Effects НЕТ вовсе. LayerCollection умеет
        add/addNull/addSolid/addText/addBoxText/addCamera/addLight/addShape;
        корректирующий слой — это солид с `adjustmentLayer=true`.
      * matchName эффекта — с пробелом перед двойкой. Снято с живого проекта
        (`sample1.inspect.json`: mn='ADBE Gaussian Blur 2', свойство '…-0001').
    `node --check` оба пропускает: синтаксис безупречный, врут имена.
    """
    jsx = _build(xml_subs, tmp_path, style={"start_blur": 25, "start_blur_dur": 0.52})
    assert "addAdjustmentLayer" not in jsx, "такого метода в AE нет — сборка упадёт"
    assert "main.layers.addSolid(" in jsx and "sbl.adjustmentLayer=true" in jsx
    assert '"Размытие на старте"' in jsx
    assert '"ADBE Gaussian Blur 2"' in jsx
    assert '"ADBE Gaussian Blur 2-0001"' in jsx
    assert "ADBE Gaussian Blur2" not in jsx, "matchName без пробела — эффект не добавится"
    assert "setValueAtTime(0, 25)" in jsx
    assert "setValueAtTime(0.52, 0)" in jsx
    assert "sbl.moveToBeginning" in jsx       # поверх всех слоёв
    assert "dle.inPoint=DUR" not in jsx       # хвостового дисклеймера нет


def test_хвостовой_дисклеймер(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, style={"disclaimer_end": True})
    assert "dle.inPoint=DUR" in jsx
    assert "dop.setValueAtTime(DUR+DISC_END-0.35, 100)" in jsx
    assert "dop.setValueAtTime(DUR+DISC_END, 0)" in jsx
    assert "dle.outPoint=DUR+DISC_END" in jsx
    assert "Math.max(DUR,1)+1.35, FPS)" in jsx   # композиция удлинена на 1.35 с
    assert "adjustmentLayer=true" not in jsx


def test_хвостовой_дисклеймер_без_текста_не_создаётся(xml_subs, tmp_path):
    """Галка стоит, но головной дисклеймер скрыт (пусто) — копию создавать не из чего."""
    jsx = _build(xml_subs, tmp_path, style={"disclaimer_end": True}, disclaimer="")
    assert "dle.inPoint=DUR" not in jsx
    assert "Math.max(DUR,1), FPS)" in jsx
