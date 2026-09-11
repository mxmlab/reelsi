# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты функционала верхней строки-прогресса (задание DF).

Проверяет:
- ключи стиля по умолчанию в styles.py (BASE["top_line"] == False);
- сборку scene_plan и .jsx (при выключенной и включённой строке);
- генерацию слоёв «Строка (дорожка)» и «Строка (прогресс)», эффект Ramp и маску прогресса;
- наличие элементов управления в index.html, static JS и CSS.
"""
import gzip
import os
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles
from core import xml2ae
from core.xml2ae.build import scene_plan


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_top_line_style_defaults():
    """Ключи верхней строки-прогресса в BASE по умолчанию."""
    assert styles.BASE["top_line"] is False
    assert styles.BASE["top_line_y"] == 162.0
    assert styles.BASE["top_line_w"] == 969.0
    assert styles.BASE["top_line_th"] == 12.5
    assert styles.BASE["top_line_from"] == [0.984, 1.0, 0.541]
    assert styles.BASE["top_line_to"] == [1.0, 0.698, 0.988]
    assert styles.BASE["top_line_track_fill"] == [1.0, 1.0, 1.0]
    assert styles.BASE["top_line_track_op"] == 16.0


def test_scene_plan_top_line(xml_subs):
    """scene_plan: при top_line=False строки нет в плане, при True — все параметры на месте."""
    plan_off = scene_plan(xml_subs, style={"top_line": False})
    assert "top_line" not in plan_off
    assert plan_off["_ae"]["top_line_js"] == ""

    plan_on = scene_plan(xml_subs, style={
        "top_line": True,
        "top_line_y": 150.0,
        "top_line_w": 900.0,
        "top_line_th": 14.0,
        "top_line_from": [0.5, 0.6, 1.0],
        "top_line_to": [0.7, 0.9, 0.8],
        "top_line_track_fill": [0.9, 0.9, 0.9],
        "top_line_track_op": 20.0,
    })
    assert "top_line" in plan_on
    tl = plan_on["top_line"]
    assert tl["y"] == 150.0
    assert tl["w"] == 900.0
    assert tl["th"] == 14.0
    assert tl["from"] == [0.5, 0.6, 1.0]
    assert tl["to"] == [0.7, 0.9, 0.8]
    assert tl["track_fill"] == [0.9, 0.9, 0.9]
    assert tl["track_op"] == 20.0
    assert tl["dur"] == plan_on["dur"]

    js = plan_on["_ae"]["top_line_js"]
    assert 'tlTrack.name = "Строка (дорожка)";' in js
    assert 'tlTrackFill.property("ADBE Vector Fill Color").setValue([0.9,0.9,0.9]);' in js
    assert 'tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", 900, 14, 1);' in js
    assert 'tlRamp.property("ADBE Ramp-0001").setValue([0, 7]);' in js
    assert 'tlRamp.property("ADBE Ramp-0002").setValue([0.5,0.6,1]);' in js
    assert 'tlRamp.property("ADBE Ramp-0003").setValue([900, 7]);' in js
    assert 'tlRamp.property("ADBE Ramp-0004").setValue([0.7,0.9,0.8]);' in js
    assert 'tlMaskProp.setValueAtTime(0, _tlShape(0, 0, 14, 14, 7));' in js
    assert 'tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, 900, 14, 7));' in js


def test_top_line_jsx_generation(xml_subs, tmp_path):
    """Сборка .jsx: при включённой строке появляются два слоя и маска с ключами."""
    jsx_path = str(tmp_path / "top_line.jsx")
    xml2ae.to_ae_full(xml_subs, jsx_path=jsx_path, style={
        "top_line": True,
        "top_line_y": 162.0,
        "top_line_w": 969.0,
        "top_line_th": 12.5,
        "top_line_from": [0.984, 1.0, 0.541],
        "top_line_to": [1.0, 0.698, 0.988],
        "top_line_track_fill": [1.0, 1.0, 1.0],
        "top_line_track_op": 16.0,
    }, emit=lambda *a: None)

    code = open(jsx_path, "r", encoding="utf-8-sig").read()
    assert "Строка (дорожка)" in code
    assert 'tlTrackFill.property("ADBE Vector Fill Color").setValue([1,1,1]);' in code
    assert 'tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", 969, 12, 1);' in code
    assert 'tlTrack.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, 162]);' in code
    assert 'tlProg.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, 162]);' in code
    assert 'tlTrack.property("ADBE Transform Group").property("ADBE Opacity").setValue(16);' in code
    assert "ADBE Ramp" in code
    assert 'tlRamp.property("ADBE Ramp-0002").setValue([0.984,1,0.541]);' in code
    assert 'tlRamp.property("ADBE Ramp-0004").setValue([1,0.698,0.988]);' in code
    assert "tlMaskProp.setValueAtTime(0, _tlShape(0, 0, 12, 12, 6));" in code
    assert "tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, 969, 12, 6));" in code
    assert "tlTrack.moveToBeginning()" in code
    assert "tlProg.moveToBeginning()" in code

    # Проверка для толщины 20
    jsx_path_20 = str(tmp_path / "top_line_20.jsx")
    xml2ae.to_ae_full(xml_subs, jsx_path=jsx_path_20, style={
        "top_line": True,
        "top_line_y": 162.0,
        "top_line_w": 969.0,
        "top_line_th": 20.0,
        "top_line_from": [0.984, 1.0, 0.541],
        "top_line_to": [1.0, 0.698, 0.988],
        "top_line_track_fill": [1.0, 1.0, 1.0],
        "top_line_track_op": 16.0,
    }, emit=lambda *a: None)
    code_20 = open(jsx_path_20, "r", encoding="utf-8-sig").read()
    assert 'tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", 969, 20, 1);' in code_20
    assert "tlMaskProp.setValueAtTime(0, _tlShape(0, 0, 20, 20, 10));" in code_20
    assert "tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, 969, 20, 10));" in code_20


def test_top_line_ui_elements():
    """Элементы интерфейса, JS-функции и CSS-классы для верхней строки."""
    html = open(os.path.join(ROOT, "templates", "index.html"), "r", encoding="utf-8").read()
    css = open(os.path.join(ROOT, "static", "app.css"), "r", encoding="utf-8").read()
    js_styles = open(os.path.join(ROOT, "static", "app", "95-styles.js"), "r", encoding="utf-8").read()
    js_preview = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), "r", encoding="utf-8").read()

    # HTML
    assert 'id="st_topline"' in html
    assert 'id="st_topline_wrap"' in html
    assert 'id="st_topliney"' in html
    assert 'id="st_toplinew"' in html
    assert 'id="st_toplineth"' in html
    assert 'id="st_toplinetrackop"' in html
    assert 'id="st_toplinetrackfillcolor"' in html
    assert 'id="st_toplinetrackfillhex"' in html
    assert 'id="st_toplinefromcolor"' in html
    assert 'id="st_toplinefromhex"' in html
    assert 'id="st_toplinetocolor"' in html
    assert 'id="st_toplinetohex"' in html
    assert 'id="ipvtopline"' in html

    # CSS
    assert ".ipvtopline" in css
    assert ".pvtl_track" in css
    assert ".pvtl_prog" in css

    # JS
    assert "function syncTopLineTrackHex()" in js_styles
    assert "function syncTopLineFromHex()" in js_styles
    assert "function syncTopLineToHex()" in js_styles
    assert "function topLineUI()" in js_styles
    assert "function ipvTopLine(" in js_preview
