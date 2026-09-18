# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания ZB: Камера 1 — точка наезда числами со скрабом, сдвиг кадра, горизонт.

Проверяет:
1. Дефолты (pan_x=0, pan_y=0, rot=0) -> .jsx побайтово совпадает с дефолтной сборкой.
2. cam1_pan_x=40, cam1_pan_y=-30 -> Position нула [W/2+40, H/2-30] и Position.setValue([0,0]) у cc и mk.
3. cam1_rot=-1.2 -> CAM1_ROT=-1.2, ADBE Rotate Z ставится в addCam только для Камеры 1
   и у рото только для ci==0; нулы поворота не получают.
4. План сцены несёт zoom.pan и zoom.rot.
5. Превью через node: ipvCamChild со сдвигом даёт +pan; при pan=[0,0] — прежние числа.
6. Новые ключи заведены в styles.BASE и style_schema.py.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles, xml2ae  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build_jsx(xml, out_name, style=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=out_name,
                                   style=style or {}, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _func(src, name):
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def test_cam1_zoom_zb_defaults_match_baseline(xml_subs, tmp_path):
    """1. Дефолты (pan_x=0, pan_y=0, rot=0) дают .jsx, побайтово идентичный дефолтной сборке."""
    jsx_def = _build_jsx(xml_subs, str(tmp_path / "def.jsx"), style={})
    jsx_expl = _build_jsx(xml_subs, str(tmp_path / "expl.jsx"),
                          style={"cam1_pan_x": 0, "cam1_pan_y": 0, "cam1_rot": 0.0})
    assert jsx_def == jsx_expl
    assert "CAM1_ROT" not in jsx_def


def test_cam1_pan_shifts_null_and_resets_roto(xml_subs, tmp_path):
    """2. cam1_pan_x=40, cam1_pan_y=-30 при 0.5/0.5 -> Position нула [W/2+40, H/2-30] и setValue([0,0]) у cc и mk."""
    jsx = _build_jsx(xml_subs, str(tmp_path / "pan.jsx"),
                     style={"cam1_pan_x": 40, "cam1_pan_y": -30, "cam1_zoom_cx": 0.5, "cam1_zoom_cy": 0.5})
    # W=1080 -> W/2+40 = 580, H=1920 -> H/2-30 = 930
    assert 'cam1null.property("ADBE Transform Group").property("ADBE Position").setValue([580,930]);' in jsx
    assert 'cc.property("ADBE Transform Group").property("ADBE Position").setValue([0,0]);' in jsx
    assert 'mk.property("ADBE Transform Group").property("ADBE Position").setValue([0,0]);' in jsx


def test_cam1_rot_sets_rotation_on_cam1_and_roto(xml_subs, tmp_path):
    """3. cam1_rot=-1.2 -> CAM1_ROT=-1.2, Rotate Z только для cam1 и roto ci==0, нулы не поворачиваются."""
    jsx = _build_jsx(xml_subs, str(tmp_path / "rot.jsx"),
                     style={"cam1_rot": -1.2})
    assert "CAM1_ROT=-1.2" in jsx
    assert 'if(!isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' in jsx
    assert 'if(ci==0){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' in jsx
    assert 'if(ci==0){ try{ mk.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' in jsx

    # Нулы не должны получать поворот
    assert 'cam1null.property("ADBE Transform Group").property("ADBE Rotate Z")' not in jsx
    assert 'nul.property("ADBE Transform Group").property("ADBE Rotate Z")' not in jsx
    assert 'insNull' not in jsx or 'insNull.property("ADBE Transform Group").property("ADBE Rotate Z")' not in jsx


def test_scene_plan_carries_pan_and_rot(xml_subs):
    """4. План сцены несёт zoom.pan и zoom.rot."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="",
                             style={"cam1_pan_x": 40, "cam1_pan_y": -30, "cam1_rot": -1.2})
    zoom = plan["zoom"]
    assert zoom["pan"] == [40.0, -30.0] or zoom["pan"] == [40, -30]
    assert zoom["rot"] == -1.2

    plan_def = xml2ae.scene_plan(xml_subs, disclaimer="", style={})
    zoom_def = plan_def["zoom"]
    assert zoom_def["pan"] == [0, 0] or zoom_def["pan"] == [0.0, 0.0]
    assert zoom_def["rot"] == 0 or zoom_def["rot"] == 0.0


@node
def test_node_ipv_cam_child_pan():
    """5. Превью через node: ipvCamChild со сдвигом даёт +pan; при pan=[0,0] — прежние числа."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    fn_child = _func(src, "ipvCamChild")
    code = f"""
    const IPV = {{ plan: {{ w: 1080, h: 1920, zoom: {{ cx: 0.5, cy: 0.5, pan: [40, -30], rot: -1.2 }} }} }};
    {fn_child}
    const withPan = ipvCamChild(100, 200, 1.5);
    IPV.plan.zoom.pan = [0, 0];
    IPV.plan.zoom.rot = 0;
    const noPan = ipvCamChild(100, 200, 1.5);
    console.log(JSON.stringify({{ withPan, noPan }}));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["noPan"] == [150, 300]
    assert out["withPan"] == [190, 270]


def test_style_guards_zb():
    """6. Новые ключи cam1_pan_x, cam1_pan_y, cam1_rot заведены в styles.BASE и style_schema.py."""
    for k in ("cam1_pan_x", "cam1_pan_y", "cam1_rot"):
        assert k in styles.BASE, f"{k} нет в styles.BASE"
        f = watcher.schema_field(k)
        assert f, f"{k} нет в схеме"
        assert f["ctl"] == "num"


@node
def test_node_st_reset_key():
    """7. stResetKey для num-поля (cam1_fit) ставит значение родителя и не бросает ReferenceError;
    для point (cam1_zoom_cx) ставит обе координаты."""
    js_path = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    fn_reset = _func(src, "stResetKey")
    fn_find = _func(src, "findFieldByKey")
    code = f"""
    const STSCHEMA = {{
      base: {{ cam1_fit: 120, cam1_zoom_cx: 0.5, cam1_zoom_cy: 0.5 }},
      layers: [
        {{
          id: 'cam1',
          items: [
            {{ type: 'field', key: 'cam1_fit', ctl: 'num' }},
            {{ type: 'field', key: 'cam1_zoom_cx', key2: 'cam1_zoom_cy', ctl: 'point' }}
          ]
        }}
      ]
    }};
    let CURSTYLE = {{ cam1_fit: 150, cam1_zoom_cx: 0.1, cam1_zoom_cy: 0.2 }};
    const parentStyle = {{ cam1_fit: 100, cam1_zoom_cx: 0.55, cam1_zoom_cy: 0.65 }};
    function getStyleParent() {{ return parentStyle; }}
    function fillStyleFields() {{}}
    function stEdit() {{}}
    {fn_find}
    {fn_reset}

    // 1. Сброс num-поля cam1_fit
    stResetKey('cam1_fit');
    const fitVal = CURSTYLE.cam1_fit;

    // 2. Сброс point-поля cam1_zoom_cx
    stResetKey('cam1_zoom_cx');
    const ptX = CURSTYLE.cam1_zoom_cx;
    const ptY = CURSTYLE.cam1_zoom_cy;

    console.log(JSON.stringify({{ fitVal, ptX, ptY }}));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["fitVal"] == 100
    assert out["ptX"] == 0.55
    assert out["ptY"] == 0.65


@node
def test_node_roto_mask_zoom_center_invariant():
    """8. Подсказка «низ маски» при rot=90, pan=0 оставляет точку центра исходника на экране на месте."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    fn_child = _func(src, "ipvCamChild")
    fn_mask = _func(src, "ipvRotoMaskZoom")
    code = f"""
    const m = {{
      style: {{
        transform: '',
        transformOrigin: '',
        willChange: '',
        backfaceVisibility: ''
      }}
    }};
    const st = {{
      querySelector: (sel) => sel === '.rotomask' ? m : null,
      getBoundingClientRect: () => ({{ width: 1080, height: 1920 }})
    }};
    function $(id) {{ return id === 'ipvstage' ? st : null; }}

    const s = 1.5;
    const cx = 0.6;
    const cy = 0.4;
    const rot = 90;
    const pan = [0, 0];

    const IPV = {{
      plan: {{
        w: 1080,
        h: 1920,
        zoom: {{ cx, cy, pan, rot }}
      }},
      vids: []
    }};

    {fn_child}
    {fn_mask}

    ipvRotoMaskZoom(s, cx, cy);

    // Точка центра исходника в координатах исходного элемента до трансформаций (W/2, H/2):
    const W = 1080, H = 1920;
    const pt0 = [W / 2, H / 2];

    // transformOrigin из m.style.transformOrigin, например '60% 40%'
    const ox = cx * W;
    const oy = cy * H;

    // Точка относительно origin:
    let p = [pt0[0] - ox, pt0[1] - oy];

    // Парсим функции из строки transform
    const tfStr = m.style.transform;
    const fnRe = /([a-zA-Z0-9]+)\\(([^)]+)\\)/g;
    const ops = [];
    let match;
    while ((match = fnRe.exec(tfStr)) !== null) {{
      ops.push({{ name: match[1], args: match[2].split(',').map(v => parseFloat(v.trim())) }});
    }}

    // Применяем операции справа налево:
    for (let i = ops.length - 1; i >= 0; i--) {{
      const op = ops[i];
      if (op.name === 'scale3d') {{
        p[0] *= op.args[0];
        p[1] *= op.args[1];
      }} else if (op.name === 'translate') {{
        p[0] += op.args[0];
        p[1] += op.args[1];
      }} else if (op.name === 'rotate') {{
        const rad = op.args[0] * Math.PI / 180;
        const cos = Math.cos(rad);
        const sin = Math.sin(rad);
        const x = p[0] * cos - p[1] * sin;
        const y = p[0] * sin + p[1] * cos;
        p[0] = x;
        p[1] = y;
      }}
    }}

    // Прибавляем transformOrigin обратно
    const finalPt = [p[0] + ox, p[1] + oy];

    // Ожидаемое положение центра исходника на экране:
    const k = W / IPV.plan.w;
    const cc = ipvCamChild(0, 0, s);
    const expected = [W / 2 + cc[0] * k, H / 2 + cc[1] * k];

    console.log(JSON.stringify({{ finalPt, expected, transform: tfStr }}));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert abs(out["finalPt"][0] - out["expected"][0]) < 1e-3
    assert abs(out["finalPt"][1] - out["expected"][1]) < 1e-3

