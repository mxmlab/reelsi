# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты функционала плашки под субтитрами (задание DE).

Проверяет:
- ключи стиля по умолчанию в styles.py (BASE["sub_bg"] == False);
- константы тени плашки в layout.py;
- генерацию выражения размера плашки из refs/sub_bg_size.js;
- сборку scene_plan и .jsx (при выключенной и включённой плашке);
- привязку SUB_BG_HIDE к прозрачности плашки при анимации rise;
- наличие элементов управления в index.html и static JS.
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
from core.xml2ae.layout import (SUB_BG_SH_OP, SUB_BG_SH_DIR, SUB_BG_SH_DIST, SUB_BG_SH_SOFT)
from core.xml2ae.build import _sub_bg_expr, scene_plan


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_sub_bg_style_defaults():
    """Ключи плашки субтитров в BASE по умолчанию."""
    assert styles.BASE["sub_bg"] is False
    assert styles.BASE["sub_bg_fill"] == [1.0, 1.0, 1.0]
    assert styles.BASE["sub_bg_op"] == 72.0
    assert styles.BASE["sub_bg_h"] == 160.0
    assert styles.BASE["sub_bg_round"] == 78.0
    assert styles.BASE["sub_bg_pad"] == 18.0
    assert styles.BASE["sub_bg_padmin"] == 70.0
    assert styles.BASE["sub_bg_dy"] == 0.0
    assert styles.BASE["sub_bg_anim"] == 0.22


def test_sub_bg_layout_constants():
    """Константы тени плашки в layout.py соответствуют adcut.aep."""
    assert SUB_BG_SH_OP == 173.4
    assert SUB_BG_SH_DIR == 181.0
    assert SUB_BG_SH_DIST == 5.0
    assert SUB_BG_SH_SOFT == 44.0


def test_sub_bg_expr_substitution():
    """Подстановка констант стиля и имени слоя субтитров в выражение размера плашки."""
    st = {
        "sub_bg_h": 180.0,
        "sub_bg_pad": 25.0,
        "sub_bg_padmin": 80.0,
        "sub_bg_anim": 0.35,
    }
    code = _sub_bg_expr(st, sub_layer_name="Субтитры (Ролик 1)")
    assert code.startswith("// --- НАСТРОЙКИ ---")
    assert "SPDX" not in code
    assert "Эталон из adcut.aep" not in code
    assert "const fixedHeight  = 180;" in code
    assert "const padPercent   = 0.25;" in code
    assert "const minPadX      = 80;" in code
    assert "const animDuration = 0.35;" in code
    assert 'const precompLayer = thisComp.layer("Субтитры (Ролик 1)");' in code
    # Проверяем, что базовый алгоритм сохранён
    assert "Ease Out Quart" in code
    assert "calcFullWidth" in code


def test_unique_sub_comp_names_for_multiple_jsx(xml_subs, tmp_path):
    """Задание DL (пункт 2): несколько .jsx в одном проекте AE не коллидят по имени субтитров."""
    clip1_xml = str(tmp_path / "Clip_A.xml")
    clip2_xml = str(tmp_path / "Clip_B.xml")
    shutil.copyfile(xml_subs, clip1_xml)
    shutil.copyfile(xml_subs, clip2_xml)

    with open(clip1_xml, "r", encoding="utf-8") as f:
        c1 = f.read().replace("<name>CLIP-006</name>", "<name>Clip_A</name>")
    with open(clip1_xml, "w", encoding="utf-8") as f:
        f.write(c1)

    with open(clip2_xml, "r", encoding="utf-8") as f:
        c2 = f.read().replace("<name>CLIP-006</name>", "<name>Clip_B</name>")
    with open(clip2_xml, "w", encoding="utf-8") as f:
        f.write(c2)

    jsx1_path = str(tmp_path / "clip1.jsx")
    jsx2_path = str(tmp_path / "clip2.jsx")

    xml2ae.to_ae_full(clip1_xml, jsx1_path, style={"sub_bg": True})
    xml2ae.to_ae_full(clip2_xml, jsx2_path, style={"sub_bg": True})

    code1 = open(jsx1_path, encoding="utf-8-sig").read()
    code2 = open(jsx2_path, encoding="utf-8-sig").read()

    # В первом jsx создаётся комп "Субтитры (Clip_A)" (литералом в addComp)
    assert '"Субтитры (Clip_A)"' in code1

    # Выражение плашки JSON-encoded в .jsx → проверяем через scene_plan и _sub_bg_expr
    plan1 = scene_plan(clip1_xml, style={"sub_bg": True})
    assert plan1["sub_bg"]["layer"] == "Субтитры (Clip_A)"
    expr1 = _sub_bg_expr({}, sub_layer_name="Субтитры (Clip_A)")
    assert 'const precompLayer = thisComp.layer("Субтитры (Clip_A)");' in expr1

    # Во втором jsx создаётся комп "Субтитры (Clip_B)"
    assert '"Субтитры (Clip_B)"' in code2

    plan2 = scene_plan(clip2_xml, style={"sub_bg": True})
    assert plan2["sub_bg"]["layer"] == "Субтитры (Clip_B)"
    expr2 = _sub_bg_expr({}, sub_layer_name="Субтитры (Clip_B)")
    assert 'const precompLayer = thisComp.layer("Субтитры (Clip_B)");' in expr2

    # Имена уникальны между двумя роликами
    assert plan1["sub_bg"]["layer"] != plan2["sub_bg"]["layer"]


def test_scene_plan_sub_bg(xml_subs):
    """scene_plan: при sub_bg=False плашки нет в плане, при True — все параметры на месте."""
    plan_off = scene_plan(xml_subs, style={"sub_bg": False})
    assert "sub_bg" not in plan_off
    assert plan_off["_ae"]["sub_bg_js"] == ""
    assert plan_off["sub_shadow"] is True
    assert 'var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' in plan_off["_ae"]["sub_shadow_js"]

    plan_on = scene_plan(xml_subs, style={
        "sub_bg": True,
        "sub_bg_fill": [0.2, 0.4, 0.6],
        "sub_bg_op": 80.0,
        "sub_bg_h": 150.0,
        "sub_bg_round": 60.0,
        "sub_bg_pad": 20.0,
        "sub_bg_padmin": 65.0,
        "sub_bg_dy": -35.0,
        "sub_bg_anim": 0.25,
    })
    assert "sub_bg" in plan_on
    assert plan_on["sub_shadow"] is False
    assert plan_on["_ae"]["sub_shadow_js"] == ""
    sbg = plan_on["sub_bg"]
    assert sbg["fill"] == [0.2, 0.4, 0.6]
    assert sbg["op"] == 80.0
    assert sbg["h"] == 150.0
    assert sbg["round"] == 60.0
    assert sbg["pad"] == 20.0
    assert sbg["padmin"] == 65.0
    assert sbg["dy"] == -35.0
    assert sbg["anim"] == 0.25
    assert sbg["y"] == round(plan_on["posy"] - 0.27 * plan_on["fsize"] - 35.0, 2)
    assert "Фон субтитров" in plan_on["_ae"]["sub_bg_js"]


def test_to_ae_full_jsx_generation(xml_subs, tmp_path):
    """to_ae_full: генерация шейп-слоя плашки в .jsx."""
    out_off = str(tmp_path / "out_off.jsx")
    xml2ae.to_ae_full(xml_subs, out_off, style={"sub_bg": False})
    jsx_off = open(out_off, encoding="utf-8-sig").read()
    assert "Фон субтитров" not in jsx_off
    # При sub_bg=False тень на прекомпе субтитров создаётся
    assert 'var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' in jsx_off

    out_on = str(tmp_path / "out_on.jsx")
    xml2ae.to_ae_full(xml_subs, out_on, style={
        "sub_bg": True,
        "sub_bg_fill": [1.0, 1.0, 1.0],
        "sub_bg_op": 72.0,
        "sub_bg_h": 160.0,
        "sub_bg_round": 78.0,
        "sub_bg_pad": 18.0,
        "sub_bg_padmin": 70.0,
        "sub_bg_dy": -41.0,
    })
    jsx_on = open(out_on, encoding="utf-8-sig").read()
    assert 'bgLayer.name = "Фон субтитров";' in jsx_on
    assert 'bgContents.addProperty("ADBE Vector Shape - Rect");' in jsx_on
    assert 'bgRect.property("ADBE Vector Rect Roundness").setValue(78);' in jsx_on
    assert 'bgFill.property("ADBE Vector Fill Color").setValue([1,1,1]);' in jsx_on
    assert 'bgLayer.property("ADBE Transform Group").property("ADBE Opacity").setValue(72);' in jsx_on
    assert 'bgDs.property("ADBE Drop Shadow-0002").setValue(173.4);' in jsx_on
    assert 'bgDs.property("ADBE Drop Shadow-0003").setValue(181);' in jsx_on
    assert 'bgDs.property("ADBE Drop Shadow-0004").setValue(5);' in jsx_on
    assert 'subLayers = [bgLayer, subLayer];' in jsx_on
    # При sub_bg=True тень на прекомпе субтитров НЕ создаётся вовсе
    assert 'var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' not in jsx_on


def test_sub_bg_with_rise_hide(xml_subs, tmp_path):
    """Плашка гаснет вместе с субтитрами при rise-вставке с целевой непрозрачностью sub_bg_op."""
    out_jsx = str(tmp_path / "out_rise.jsx")
    xml2ae.to_ae_full(xml_subs, out_jsx, inserts=[
        {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5}
    ], style={"insert_anim": "rise", "sub_bg": True, "sub_bg_op": 72.0})
    jsx = open(out_jsx, encoding="utf-8-sig").read()
    assert "SUB_BG_HIDE" in jsx
    assert "applyKeyframes(bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Opacity\"), SUB_BG_HIDE);" in jsx


def test_static_sub_bg_ui():
    """Элементы интерфейса плашки субтитров в index.html и 95-styles.js."""
    html_path = os.path.join(ROOT, "templates", "index.html")
    html = open(html_path, encoding="utf-8").read()
    assert 'id="st_subbg"' in html
    assert 'id="st_subbg_wrap"' in html
    assert 'id="st_subbgcolor"' in html
    assert 'id="st_subbghex"' in html
    assert 'id="st_subbgop"' in html
    assert 'id="st_subbgh"' in html
    assert 'id="st_subbground"' in html
    assert 'id="st_subbgpad"' in html
    assert 'id="st_subbgdy"' in html

    js_path = os.path.join(ROOT, "static", "app", "95-styles.js")
    js = open(js_path, encoding="utf-8").read()
    assert "syncSubBgHex" in js
    assert "subBgUI" in js
    assert "st_subbg" in js
    assert "CURSTYLE.sub_bg=" in js


def test_sub_shadow_and_badge_dk(xml_subs, tmp_path):
    """Задание DK: при sub_bg=true тень субтитров снимается, тень плашки остаётся.

    При sub_bg=false тень субтитров создаётся как обычно.
    """
    plan_off = scene_plan(xml_subs, style={"sub_bg": False})
    assert plan_off["sub_shadow"] is True

    plan_on = scene_plan(xml_subs, style={"sub_bg": True})
    assert plan_on["sub_shadow"] is False

    out_off = str(tmp_path / "dk_off.jsx")
    xml2ae.to_ae_full(xml_subs, out_off, style={"sub_bg": False})
    jsx_off = open(out_off, encoding="utf-8-sig").read()
    assert 'var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' in jsx_off
    assert 'bgLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' not in jsx_off

    out_on = str(tmp_path / "dk_on.jsx")
    xml2ae.to_ae_full(xml_subs, out_on, style={"sub_bg": True})
    jsx_on = open(out_on, encoding="utf-8-sig").read()
    # Тень на subLayer отсутствует
    assert 'var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' not in jsx_on
    # Тень на bgLayer присутствует
    assert 'var bgDs = bgLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");' in jsx_on


def test_preview_sub_cleanup_and_shadow_dk():
    """Задание DK: ipvSubs очищает текстовые узлы и управляет переменной --subsh."""
    js_ins = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), encoding="utf-8").read()
    # Очистка чужих узлов из #ipvsub
    assert "el.childNodes" in js_ins
    assert "node.nodeType===3" in js_ins or "node.nodeType === 3" in js_ins
    assert "pvsub_bg" in js_ins and "pvsubs_host" in js_ins
    # Управление переменной --subsh
    assert "--subsh" in js_ins
    assert "pl.sub_shadow===false" in js_ins or "pl.sub_shadow === false" in js_ins

    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()
    assert "--subsh" in css

