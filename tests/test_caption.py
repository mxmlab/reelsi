# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Tests for caption feature (task DG)."""
import gzip
import json
import os
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles
from core import xml2ae
from api.editor import _sidecar_caption
from core.xml2ae.build import scene_plan


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_caption_style_defaults():
    assert styles.BASE["caption"] is False
    assert styles.BASE["caption_font"] == "SFPro-Bold"
    assert styles.BASE["caption_size"] == 26.0
    assert styles.BASE["caption_case"] == "upper"
    assert styles.BASE["caption_fill"] == [1.0, 1.0, 1.0]
    assert styles.BASE["caption_x"] == 55.5
    assert styles.BASE["caption_y"] == 228.0
    assert styles.BASE["caption_bg"] is True
    assert styles.BASE["caption_bg_fill"] == [0.345, 0.345, 0.345]
    assert styles.BASE["caption_bg_op"] == 45.0
    assert styles.BASE["caption_bg_round"] == 68.0
    assert styles.BASE["caption_kx"] == 1.718
    assert styles.BASE["caption_ky"] == 2.484


def test_sidecar_caption_read(tmp_path):
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").write("<xml/>")

    assert _sidecar_caption(xml) == ""

    sidecar = str(tmp_path / "clip.caption.json")
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump({"text": "  peptides  "}, f)
    assert _sidecar_caption(xml) == "peptides"

    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump("plain text", f)
    assert _sidecar_caption(xml) == "plain text"


def test_scene_plan_caption_off_or_empty(xml_subs):
    plan_off = scene_plan(xml_subs, caption="peptides", style={"caption": False})
    assert "caption" not in plan_off
    assert plan_off["_ae"]["caption_js"] == ""

    plan_empty = scene_plan(xml_subs, caption="", style={"caption": True})
    assert "caption" in plan_empty
    assert plan_empty["caption"]["text"] == ""
    assert plan_empty["_ae"]["caption_js"] == ""


def test_scene_plan_caption_on(xml_subs):
    plan_on = scene_plan(xml_subs, caption="peptides", style={
        "caption": True,
        "caption_font": "SFPro-Bold",
        "caption_size": 26.0,
        "caption_case": "upper",
        "caption_fill": [0.9, 0.9, 0.9],
        "caption_x": 200.0,
        "caption_y": 250.0,
        "caption_bg": True,
        "caption_bg_fill": [0.2, 0.2, 0.2],
        "caption_bg_op": 50.0,
        "caption_bg_round": 60.0,
        "caption_kx": 1.718,
        "caption_ky": 2.484,
    })
    assert "caption" in plan_on
    cap = plan_on["caption"]
    assert cap["text"] == "PEPTIDES"
    assert cap["font"] == "SFPro-Bold"
    assert cap["size"] == 26.0
    assert "scale" not in cap          # масштаб слоя убран: кегль подписи экранный
    assert cap["case"] == "upper"
    assert cap["fill"] == [0.9, 0.9, 0.9]
    assert cap["x"] == 200.0
    assert cap["y"] == 250.0
    assert cap["bg"] is True
    assert cap["bg_fill"] == [0.2, 0.2, 0.2]
    assert cap["bg_op"] == 50.0
    assert cap["bg_round"] == 60.0
    assert cap["kx"] == 1.718
    assert cap["ky"] == 2.484

    js = plan_on["_ae"]["caption_js"]
    assert 'capBg.name = "Подпись (фон)";' in js
    assert 'capLayer.name = "Подпись";' in js
    assert 'capVal.text = "PEPTIDES";' in js
    assert 'capVal.fontSize = 26;' in js
    assert 'capLayer.property("ADBE Transform Group").property("ADBE Scale")' not in js
    assert 'capLayer.property("ADBE Transform Group").property("ADBE Position").setValue([200, 250]);' in js
    assert 'capBgRect.property("ADBE Vector Rect Roundness").setValue(60);' in js
    assert 'capBg.property("ADBE Transform Group").property("ADBE Opacity").setValue(50);' in js


def test_scene_plan_caption_without_bg(xml_subs):
    plan = scene_plan(xml_subs, caption="text only", style={
        "caption": True,
        "caption_bg": False,
        "caption_case": "as-is",
    })
    assert "caption" in plan
    assert plan["caption"]["bg"] is False
    js = plan["_ae"]["caption_js"]
    assert "Подпись (фон)" not in js
    assert 'capLayer.name = "Подпись";' in js
    assert 'capVal.text = "text only";' in js


def test_caption_jsx_generation(xml_subs, tmp_path):
    jsx_path = str(tmp_path / "caption.jsx")
    xml2ae.to_ae_full(xml_subs, jsx_path=jsx_path, caption="пептиды", style={
        "caption": True,
        "caption_font": "SFPro-Bold",
        "caption_size": 26.0,
        "caption_case": "upper",
        "caption_fill": [1.0, 1.0, 1.0],
        "caption_x": 179.0,
        "caption_y": 228.0,
        "caption_bg": True,
        "caption_bg_fill": [0.345, 0.345, 0.345],
        "caption_bg_op": 45.0,
        "caption_bg_round": 68.0,
        "caption_kx": 1.718,
        "caption_ky": 2.484,
    }, emit=lambda *a: None)

    code = open(jsx_path, "r", encoding="utf-8-sig").read()
    assert 'capBg.name = "Подпись (фон)";' in code
    assert 'capLayer.name = "Подпись";' in code
    assert '[r.width * 1.718, r.height * 2.484];' in code
    assert 'capBgRect.property("ADBE Vector Rect Roundness").setValue(68);' in code
    assert 'capBg.property("ADBE Transform Group").property("ADBE Opacity").setValue(45);' in code
    assert 'capVal.fontSize = 26;' in code
    assert 'capLayer.property("ADBE Transform Group").property("ADBE Scale")' not in code
    assert 'capVal.fillColor = [1,1,1];' in code
    assert 'capLayer.property("ADBE Transform Group").property("ADBE Position").setValue([179, 228]);' in code
    assert 'capBg.moveToBeginning();' in code
    assert 'capLayer.moveToBeginning();' in code


def test_caption_bg_size_expr():
    from core.xml2ae.build import _caption_bg_size_expr
    expr = _caption_bg_size_expr(0.9, 1.3)
    assert '[r.width * 0.9, r.height * 1.3];' in expr
    assert 'targetLayerName = "Подпись";' in expr



def test_caption_api_routes(tmp_path):
    from flask import Flask
    from api.editor import bp as editor_bp

    app = Flask(__name__)
    app.register_blueprint(editor_bp)
    client = app.test_client()

    xml = str(tmp_path / "seq.xml")
    open(xml, "w", encoding="utf-8").write("<xml/>")

    resp = client.post("/api/caption", json={"xml": xml})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["text"] == ""

    resp = client.post("/api/caption", json={"xml": xml, "text": "peptides"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["text"] == "peptides"

    sidecar_path = str(tmp_path / "seq.caption.json")
    assert os.path.isfile(sidecar_path)
    with open(sidecar_path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved == {"text": "peptides"}

    resp = client.post("/api/caption", json={"xml": xml})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["text"] == "peptides"

    resp_err = client.post("/api/caption", json={"xml": "nonexistent.xml"})
    data_err = resp_err.get_json()
    assert "error" in data_err


def test_caption_ui_elements():
    index_html = open(os.path.join(ROOT, "templates", "index.html"), "r", encoding="utf-8").read()
    assert 'id="st_caption"' in index_html
    assert 'id="st_caption_wrap"' in index_html
    assert 'id="st_captionfont"' in index_html
    assert 'id="st_captionsize"' in index_html
    assert 'id="st_captioncase"' in index_html
    assert 'id="st_captionfillcolor"' in index_html
    assert 'id="st_captionx"' in index_html
    assert 'id="st_captiony"' in index_html
    assert 'id="st_captionbg"' in index_html
    assert 'id="st_captionbg_wrap"' in index_html
    assert 'id="st_captionkx"' in index_html
    assert 'id="st_captionky"' in index_html
    assert 'id="aewcaption"' in index_html
    assert 'id="aewcaption_text"' in index_html
    assert 'id="aewcaptionsave"' in index_html
    assert 'id="aewcaptionres"' in index_html
    assert 'id="pvwcaption"' not in index_html
    assert 'id="pvwcaption_text"' not in index_html
    assert 'id="pvwcaptionsave"' not in index_html
    assert 'id="ipvcaption"' in index_html

    # Поле подписи лежит в #aewpanel, а не в #pvwordsbox
    aew_pos = index_html.find('id="aewpanel"')
    aew_words_pos = index_html.find('id="aewwords"')
    aew_cap_pos = index_html.find('id="aewcaption"')
    assert aew_pos != -1 and aew_words_pos != -1 and aew_cap_pos != -1
    assert aew_pos < aew_cap_pos < aew_words_pos

    pvw_pos = index_html.find('id="pvwordsbox"')
    assert 'id="aewcaption"' not in index_html[pvw_pos:aew_pos]

    app_css = open(os.path.join(ROOT, "static", "app.css"), "r", encoding="utf-8").read()
    assert ".ipvcaption" in app_css

    styles_js = open(os.path.join(ROOT, "static", "app", "95-styles.js"), "r", encoding="utf-8").read()
    assert "captionUI" in styles_js
    assert "captionBgUI" in styles_js
    assert "syncCaptionFillHex" in styles_js
    assert "syncCaptionBgHex" in styles_js
    assert "st_caption" in styles_js

    view_js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), "r", encoding="utf-8").read()
    assert "ipvCaption" in view_js
    assert "rgba(" in view_js
    assert "aewUpdateCaptionUI" in view_js

    inserts_js = open(os.path.join(ROOT, "static", "app", "80-inserts.js"), "r", encoding="utf-8").read()
    assert "aewLoadCaption" in inserts_js
    assert "aewSaveCaption" in inserts_js
    assert "aewUpdateCaptionUI" in inserts_js

    preview_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), "r", encoding="utf-8").read()
    assert "pvwLoadCaption" not in preview_js
    assert "pvwSaveCaption" not in preview_js
    assert "pvwUpdateCaptionUI" not in preview_js
