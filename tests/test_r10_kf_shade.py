# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание KF: затемнение интро следует за размером кадра и intro_scale.

Затемнение занимает постоянную долю кадра при любом разрешении композиции
и масштабируется вместе с текстом интро при intro_scale != 100.
При 1080x1920 и intro_scale = 100 план и .jsx идентичны эталону задания IL.
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

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.jsutil import _jd  # noqa: E402
from core.xml2ae.layout import (  # noqa: E402
    SHADE_BLUR, SHADE_DY, SHADE_H, SHADE_OX, SHADE_OY, SHADE_SCALE, SHADE_W, SHADE_X,
)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def xml_subs_2160(tmp_path):
    dst = str(tmp_path / "timeline_2160.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g:
        text = g.read().decode("utf-8")
    text_2160 = text.replace("<width>1080</width>", "<width>2160</width>").replace(
        "<height>1920</height>", "<height>3840</height>"
    )
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text_2160)
    return dst


INTRO = [
    dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
    dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
]

SHADE_768 = {
    "x": -4, "y": 553, "scale": 94, "w": 1416, "h": 1052,
    "ox": -20, "oy": 610, "blur": 653, "op": 100.0,
}


def _scene_plan(xml, intro=None, splits=None, style=None):
    return xml2ae.scene_plan(
        xml,
        intro=intro if intro is not None else INTRO,
        intro_splits=splits if splits is not None else [1],
        style=dict(style or {}),
        disclaimer="",
        intro_riser=False,
        emit=lambda *a, **kw: None,
    )


def _build(xml, tmp_path, intro=None, style=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(
        xml,
        jsx_path=str(tmp_path / name),
        intro=intro if intro is not None else INTRO,
        intro_splits=[1],
        style=style or {},
        intro_mode="word",
        disclaimer="",
        intro_riser=False,
        emit=lambda *a, **kw: None,
    )
    return open(path, encoding="utf-8-sig").read(), path


def test_frame_1080_intro_scale_100_matches_default(xml_subs):
    """Кадр 1080, intro_scale 100 -> plan['shade'] равен исходному (все поля, точно)."""
    plan = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": 768, "intro_scale": 100})
    sh = plan["shade"]
    assert sh == SHADE_768
    # Проверяем целочисленные типы геометрии (без float-хвостов)
    for k in ("x", "y", "scale", "w", "h", "ox", "oy", "blur"):
        assert isinstance(sh[k], int), f"поле {k} должно быть int, получено {type(sh[k])}"
    # Проверяем сериализацию _jd: в JS уходят точные целые числа
    jd_str = _jd(sh)
    for piece in ('"w":1416', '"h":1052', '"ox":-20', '"oy":610', '"blur":653', '"x":-4', '"scale":94'):
        assert piece in jd_str, f"{piece} отсутствует в {jd_str}"


def test_frame_2160_doubles_dimensions(xml_subs, xml_subs_2160):
    """Кадр 2160 -> w, h, ox, oy, blur, x вдвое больше, scale прежний, y == intro_y + SHADE_DY*2."""
    intro_y = 768
    p1080 = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": intro_y, "intro_scale": 100})
    p2160 = _scene_plan(xml_subs_2160, style={"intro_shade": True, "intro_y": intro_y, "intro_scale": 100})
    sh1080 = p1080["shade"]
    sh2160 = p2160["shade"]

    assert sh2160["w"] == sh1080["w"] * 2
    assert sh2160["h"] == sh1080["h"] * 2
    assert sh2160["ox"] == sh1080["ox"] * 2
    assert sh2160["oy"] == sh1080["oy"] * 2
    assert sh2160["blur"] == sh1080["blur"] * 2
    assert sh2160["x"] == sh1080["x"] * 2
    assert sh2160["scale"] == sh1080["scale"]
    assert sh2160["y"] == intro_y + SHADE_DY * 2
    # Отношение ширины затемнения к ширине кадра одинаково
    assert sh1080["w"] / p1080["w"] == sh2160["w"] / p2160["w"]


def test_intro_scale_150_scales_layer_and_dy(xml_subs):
    """intro_scale 150 при кадре 1080 -> scale == SHADE_SCALE*1.5, y == intro_y + SHADE_DY*1.5."""
    intro_y = 768
    sh = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": intro_y, "intro_scale": 150})["shade"]
    assert sh["scale"] == SHADE_SCALE * 1.5
    assert sh["y"] == intro_y + SHADE_DY * 1.5
    assert sh["w"] == SHADE_W
    assert sh["h"] == SHADE_H
    assert sh["blur"] == SHADE_BLUR
    assert sh["ox"] == SHADE_OX
    assert sh["oy"] == SHADE_OY
    assert sh["x"] == SHADE_X


def test_jsx_contains_doubled_shade_at_2160(xml_subs_2160, tmp_path):
    """.jsx при включённом затемнении и кадре 2160 содержит INTRO_SHADE с удвоенными w/h."""
    jsx, path = _build(xml_subs_2160, tmp_path, style={"intro_shade": True, "intro_y": 768})
    assert "var INTRO_SHADE=" in jsx
    m = re.search(r"var INTRO_SHADE=(\{.*?\});", jsx)
    assert m is not None, "INTRO_SHADE не найден в jsx"
    data = json.loads(m.group(1))
    assert data["w"] == SHADE_W * 2
    assert data["h"] == SHADE_H * 2
    assert data["blur"] == SHADE_BLUR * 2
    assert data["x"] == SHADE_X * 2
    assert data["ox"] == SHADE_OX * 2
    assert data["oy"] == SHADE_OY * 2
    assert data["scale"] == SHADE_SCALE

    rep = verify_jsx.Report(path)
    verify_jsx.check_syntax(path, jsx, rep)
    verify_jsx.check_undeclared(jsx, rep)
    verify_jsx.check_line_separators(jsx, rep)
    verify_jsx.check_bom(path, rep)
    assert rep.ok, f"verify_jsx нашёл проблемы: {rep.errors}"
