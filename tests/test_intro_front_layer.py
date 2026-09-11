# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Поднятие прекомпа интро над рото и видеовставками при временном пересечении с видео.

Если группа интро по времени пересекается с видеовставкой:
  * В JS уходит INTRO_FRONT[gI] = 1 (вместо 0);
  * Прекомп такой группы отправляется в introFrontLayers;
  * После прохода LAYER_ORDER слои из introFrontLayers поднимаются наверх
    (moveToBeginning), оказываясь над рото и видеовставками;
  * Группы без пересечения с видео остаются в introLayers и лежат под рото;
  * Фотовставки НЕ триггерят поднятие;
  * Если НИ ОДНА группа не пересекается с видео — все четыре подстановки пустые,
    INTRO_FRONT отсутствует, .jsx остаётся прежним (golden-инвариант).
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
    """Мокаем GPU-рото для быстрой и предсказуемой генерации рото-слоёв в .jsx."""
    mock_data = json.dumps([
        {"ci": 0, "ts": 0.0, "te": 5.0, "cs": 0.0, "scale": 100, "mf": 1, "mask": "C:/x/mask.mp4"}
    ])
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: mock_data)


def _build(xml, tmp_path, intro, inserts=None, splits=None, style=None):
    out_path = str(tmp_path / "out.jsx")
    path, _, _ = xml2ae.to_ae_full(
        xml,
        jsx_path=out_path,
        intro=intro,
        inserts=inserts or [],
        intro_splits=[] if splits is None else splits,
        style=style or {},
        disclaimer="",
        emit=lambda *a: None,
    )
    return open(path, encoding="utf-8-sig").read()


def _get_intro_front(jsx):
    """Возвращает массив INTRO_FRONT из .jsx или None, если его нет (все 0)."""
    m = re.search(r"var INTRO_FRONT\s*=\s*(\[.*?\]);", jsx)
    if not m:
        return None
    return json.loads(m.group(1))


from test_fm_layer_order import simulate_jsx_stack


def test_пересечение_с_видео_поднимает_интро(xml_subs, tmp_path):
    """Группа интро, пересекающаяся по времени с видеовставкой, получает INTRO_FRONT=[1]
    и поднимается выше рото и видео."""
    intro = [dict(words=["ПЕРВОЕ", "СЛОВО"], times=[1.0, 1.5])]
    video = [dict(type="video", media="C:/x/v.mp4", start_s=1.2, dur_s=2.0)]
    out_file = tmp_path / "out.jsx"
    jsx = _build(xml_subs, tmp_path, intro, inserts=video)

    assert _get_intro_front(jsx) == [1]
    assert "introFrontLayers.push(iL)" in jsx
    assert "introFrontLayers[fi].moveToBeginning()" in jsx

    # Проверяем стек через симуляцию в Node
    stack = simulate_jsx_stack(out_file)
    cats = [x["category"] for x in stack if x["category"] in ("intro", "video", "roto")]
    assert "intro" in cats and "video" in cats and "roto" in cats
    # intro поднято наверх (индекс в стеке меньше, чем у video и roto)
    assert cats.index("intro") < cats.index("video")
    assert cats.index("intro") < cats.index("roto")


def test_без_пересечения_с_видео_интро_остаётся_под_рото(xml_subs, tmp_path):
    """Группа интро без пересечения с видеовставкой: INTRO_FRONT отсутствует,
    прекомп остаётся на своём дефолтном слое (под рото)."""
    intro = [dict(words=["ПЕРВОЕ", "СЛОВО"], times=[0.1, 0.5])]
    video = [dict(type="video", media="C:/x/v.mp4", start_s=4.0, dur_s=2.0)]
    out_file = tmp_path / "out.jsx"
    jsx = _build(xml_subs, tmp_path, intro, inserts=video)

    # при front=0 INTRO_FRONT не попадает в .jsx
    assert _get_intro_front(jsx) is None
    assert "introFrontLayers" not in jsx

    stack = simulate_jsx_stack(out_file)
    cats = [x["category"] for x in stack if x["category"] in ("intro", "video", "roto")]
    # intro лежит ниже рото и видео (дефолтный порядок: video -> roto -> ... -> intro)
    assert cats.index("roto") < cats.index("intro")


def test_фотовставка_не_триггерит_поднятие(xml_subs, tmp_path):
    """Фотовставка (type='photo') пересекается по времени с интро, но НЕ поднимает его:
    INTRO_FRONT отсутствует."""
    intro = [dict(words=["ПЕРВОЕ", "СЛОВО"], times=[1.0, 1.5])]
    photo = [dict(type="photo", style="cam2", media="C:/x/p.png", start_s=1.2, dur_s=2.0)]
    out_file = tmp_path / "out.jsx"
    jsx = _build(xml_subs, tmp_path, intro, inserts=photo)

    assert _get_intro_front(jsx) is None
    assert "introFrontLayers" not in jsx

    stack = simulate_jsx_stack(out_file)
    cats = [x["category"] for x in stack if x["category"] in ("intro", "roto")]
    # intro остаётся ниже рото
    assert cats.index("roto") < cats.index("intro")


def test_несколько_групп_раздельное_поднятие(xml_subs, tmp_path):
    """Две группы: первая пересекается с видео, вторая нет -> INTRO_FRONT=[1, 0]."""
    intro = [
        dict(words=["ПЕРВАЯ", "ГРУППА"], times=[1.0, 1.5]),
        dict(words=["ВТОРАЯ", "ГРУППА"], times=[4.0, 4.5]),
    ]
    video = [dict(type="video", media="C:/x/v.mp4", start_s=1.2, dur_s=1.0)]
    jsx = _build(xml_subs, tmp_path, intro, inserts=video, splits=[1])

    assert _get_intro_front(jsx) == [1, 0]


def test_без_видеовставок_инвариант_сохранён(xml_subs, tmp_path):
    """Без видеовставок INTRO_FRONT отсутствует в .jsx (все 0 → подстановки пустые)."""
    intro = [
        dict(words=["ПЕРВАЯ", "ГРУППА"], times=[1.0, 1.5]),
        dict(words=["ВТОРАЯ", "ГРУППА"], times=[2.0, 2.5]),
    ]
    jsx = _build(xml_subs, tmp_path, intro, inserts=[], splits=[1])
    assert _get_intro_front(jsx) is None
    assert "introFrontLayers" not in jsx

