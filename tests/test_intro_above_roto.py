# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Галка «Интро над рото в нижней половине (камера 1)» (задание C).

Стиль получил ключ intro_roto_by_pos. Если он включён, группа интро Камеры 1, чей блок
ушёл ОТ ЦЕНТРА КАДРА ВНИЗ (зона субтитров, _y + G*dy > 0), в собранном .jsx поднимается
сразу над рото: каждый её слой переносится ПЕРЕД самым верхним рото-слоем (moveBefore).
Блок в верхней половине кадра остаётся под рото, как раньше.

Проверяется:
1. scene_plan: галка включена, gy большой положительный -> above_roto True; gy
   отрицательный -> False (камера 1, зум не учитываем);
2. галка выключена -> above_roto у всех False, в .jsx нет INTRO_ABOVE_ROTO
   (golden-инвариант: все четыре подстановки пустые);
3. группа на видеовставке (front) и группа на перебивке (Камера 2) -> всегда False;
4. в .jsx с такой группой есть INTRO_ABOVE_ROTO, introAboveRoto.push(iL) и moveBefore,
   и блок подъёма стоит РАНЬШЕ блока introFrontLayers;
5. сторож tests/test_style_keys_in_ui.py остаётся зелёным (ключ есть в BASE и в ручке).
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

from core import styles  # noqa: E402
from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _mock_roto(monkeypatch):
    """Мокаем GPU-рото: в .jsx появляются настоящие рото-слои, над которыми и поднимаем."""
    mock_data = json.dumps([
        {"ci": 0, "ts": 0.0, "te": 5.0, "cs": 0.0, "scale": 100, "mf": 1, "mask": "C:/x/mask.mp4"}
    ])
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: mock_data)


def _plan(xml, intro, inserts=None, splits=None, style=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False, intro=intro,
                             intro_splits=[] if splits is None else splits,
                             inserts=inserts or [], roto=False,
                             include_xml_inserts=False,
                             style=dict(style or {}))


def _build(xml, tmp_path, intro, inserts=None, splits=None, style=None):
    out_path = str(tmp_path / "out.jsx")
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=out_path, intro=intro, inserts=inserts or [],
        intro_splits=[] if splits is None else splits,
        include_xml_inserts=False,
        style=style or {}, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _above_arr(jsx):
    """Массив INTRO_ABOVE_ROTO из .jsx или None, если подстановка пустая."""
    m = re.search(r"var INTRO_ABOVE_ROTO\s*=\s*(\[.*?\]);", jsx)
    return json.loads(m.group(1)) if m else None


def test_нижний_блок_поднимается_верхний_нет(xml_subs):
    """Галка включена, камера 1: gy огромный положительный (блок ниже центра) -> True,
    gy отрицательный (блок выше центра) -> False."""
    low = _plan(xml_subs, [dict(words=["НИЖНЕЕ"], times=[1.0], gy=1000)],
                style={"intro_roto_by_pos": True})
    assert low["intro"][0]["on2"] is False
    assert low["intro"][0]["above_roto"] is True

    high = _plan(xml_subs, [dict(words=["ВЕРХНЕЕ"], times=[1.0], gy=-100)],
                 style={"intro_roto_by_pos": True})
    assert high["intro"][0]["above_roto"] is False


def test_без_галки_jsx_прежний(xml_subs, tmp_path):
    """Галка выключена (дефолт стиля): у всех групп False, в .jsx нет INTRO_ABOVE_ROTO."""
    plan = _plan(xml_subs, [
        dict(words=["НИЖНЕЕ"], times=[1.0], gy=1000),
        dict(words=["ЕЩЁ"], times=[2.0], gy=1000),
    ], splits=[1])
    assert plan["intro"] and all(g["above_roto"] is False for g in plan["intro"])

    jsx = _build(xml_subs, tmp_path, [
        dict(words=["НИЖНЕЕ"], times=[1.0], gy=1000),
        dict(words=["ЕЩЁ"], times=[2.0], gy=1000),
    ], splits=[1])
    assert "INTRO_ABOVE_ROTO" not in jsx
    assert "introAboveRoto" not in jsx


def test_видео_и_камера2_не_трогаются(xml_subs):
    """Группа на видеовставке (front) и группа на перебивке (Камера 2) не поднимаются,
    даже когда блок заведомо в нижней половине кадра (gy большой)."""
    front = _plan(xml_subs, [dict(words=["НА", "ВИДЕО"], times=[4.0, 4.4], gy=1000)],
                  inserts=[dict(type="video", media="C:/x/v.mp4", start_s=4.0, dur_s=2.0)],
                  style={"intro_roto_by_pos": True})
    assert front["intro"][0]["front"] is True
    assert front["intro"][0]["above_roto"] is False

    on2 = _plan(xml_subs, [dict(words=["ПЕРЕБИВКА"], times=[8.0], gy=1000)],
                style={"intro_roto_by_pos": True})
    assert on2["intro"][0]["on2"] is True
    assert on2["intro"][0]["above_roto"] is False


def test_jsx_несёт_подъём_над_рото(xml_subs, tmp_path):
    """Группа камеры 1 в нижней половине и группа на видео (для непустой подстановки
    front): в .jsx есть INTRO_ABOVE_ROTO=[1,0], push в introAboveRoto, moveBefore, и
    блок подъёма над рото стоит РАНЬШЕ блока introFrontLayers."""
    intro = [dict(words=["НИЖНЕЕ", "ИНТРО"], times=[1.0, 1.4], gy=1000),
             dict(words=["НА", "ВИДЕО"], times=[4.0, 4.4])]
    jsx = _build(xml_subs, tmp_path, intro,
                 inserts=[dict(type="video", media="C:/x/v.mp4", start_s=4.0, dur_s=2.0)],
                 splits=[1], style={"intro_roto_by_pos": True})

    assert _above_arr(jsx) == [1, 0]
    assert "introAboveRoto.push(iL)" in jsx
    assert jsx.index("introAboveRoto[ai].moveBefore(topRoto)") \
        < jsx.index("introFrontLayers[fi].moveToBeginning()")


def test_ключ_стиля_есть_в_ручке_ui():
    """Пятый пункт задания: сторож ключей зелёный — ключ есть в styles.BASE и заведён
    в схеме панели (значит, у него есть галка в интерфейсе; задание JB)."""
    import test_style_keys_in_ui as watcher
    assert "intro_roto_by_pos" in styles.BASE
    field = watcher.schema_field("intro_roto_by_pos")
    assert field is not None, "ключ есть в BASE, но ручки в схеме нет"
    assert field["ctl"] == "bool", "intro_roto_by_pos перестал быть галкой"
