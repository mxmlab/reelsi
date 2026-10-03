# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: независимый зум на Камере 2 (свой набор настроек, раздел в панели, свои ключи).

Что стерегут:
1. Дефолт (cam2_zoom="none"): .jsx побайтово равен golden; в плане нет zoom.cam2.
2. cam2_zoom="pulse": ключи кам2 стоят на срезах входа кам1→кам2, а НЕ на возвратах на кам1;
   смена cam1_zoom_* не меняет ключи кам2 и наоборот.
3. jump + наезды в тейках и drift для кам2 дают ключи в пределах своих lo/hi.
4. Ключи кам1 при любых настройках кам2 — те же, что до правки.
5. Миграция при чтении: старый стиль cam2_zoom_on=True -> cam2_* = cam1_*; False/нет -> none.
6. Схема: раздел cam2 идёт сразу после cam1, в cam2.zoom тот же список полей и show_if;
   в cam1.tr нет ключей cam2.
7. Превью (JS-стенд): масштаб кадра кам2 считается по ключам кам2 вокруг своей точки.
8. Клик выбора точки: при видимой Камере 2 (и включённом зуме кам2) пишутся cam2_zoom_*,
   иначе cam1_zoom_*.
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
sys.path.insert(0, HERE)

from core import styles, xml2ae  # noqa: E402
from core.style_schema import schema  # noqa: E402
from core.xml2ae.layout import _cam1_return_frames, _show_segments  # noqa: E402
from test_preview_cam import _PLAN_JS, _func, _run_node, node  # noqa: E402

VIEW = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
PICK = os.path.join(ROOT, "static", "app", "95-styles.js")
PANEL = os.path.join(ROOT, "static", "app", "94-stylepanel.js")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _call(style):
    return dict(style=dict(style), disclaimer="", intro_riser=False,
                emit=lambda *a, **k: None)


def _jsx(xml, style):
    src, _n, _s = xml2ae.to_ae_full(xml, return_source=True, **_call(style))
    return src


ZOOM = {"cam1_zoom": "jump"}


def test_default_none_matches_golden_and_no_cam2_in_plan(xml_subs, tmp_path):
    """1. Дефолт (cam2_zoom='none'): .jsx побайтово равен golden, в плане нет cam2."""
    from tests.test_geometry_python import _build as _golden_build, _mask_assets
    golden_path = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
    if os.path.isfile(golden_path):
        golden = _mask_assets(open(golden_path, "r", encoding="utf-8-sig").read())
        base_jsx = _mask_assets(_golden_build(xml_subs, tmp_path, {}))
        assert base_jsx == golden, "дефолтная сборка без зума кам2 разошлась с golden"

    base = _jsx(xml_subs, dict(ZOOM))
    off = _jsx(xml_subs, dict(ZOOM, cam2_zoom="none"))
    assert off == base, "выключенный зум кам2 изменил .jsx"
    assert "cam2null" not in base
    assert "CAM2_SCALE" not in base

    plan = xml2ae.scene_plan(xml_subs, **_call(dict(ZOOM, cam2_zoom="none")))
    assert "cam2" not in plan["zoom"]
    base_st = styles.resolve({})
    assert base_st["cam2_zoom"] == "none"
    assert (base_st["cam2_zoom_cx"], base_st["cam2_zoom_cy"]) == (0.5, 0.5)
    assert base_st["cam2_fit"] == 100.0
    assert base_st["cam2_pan_x"] == 0
    assert base_st["cam2_pan_y"] == 0
    assert base_st["cam2_rot"] == 0.0

    # Любой параметр не равен дефолту -> Камера 2 активна
    for st_mod in ({"cam2_zoom_cx": 0.3}, {"cam2_fit": 120.0}, {"cam2_pan_x": 10.0},
                   {"cam2_pan_y": -15.0}, {"cam2_rot": 5.0}):
        on_st = dict(ZOOM, cam2_zoom="none", **st_mod)
        mod_jsx = _jsx(xml_subs, on_st)
        assert "var cam2null" in mod_jsx, f"при {st_mod} Камера 2 должна быть активной"
        mod_plan = xml2ae.scene_plan(xml_subs, **_call(on_st))
        assert "cam2" in mod_plan["zoom"]


def test_cam2_fit_without_zoom(xml_subs):
    """cam2_zoom=none, cam2_fit=130: в .jsx у нула кам2 масштаб 130, блок кам1 неизменен."""
    st = {"cam1_zoom": "pulse", "cam2_zoom": "none", "cam2_fit": 130.0}
    jsx = _jsx(xml_subs, st)
    assert "var cam2null" in jsx
    assert "var CAM2_SCALE=[[0,130]];" in jsx
    base = _jsx(xml_subs, {"cam1_zoom": "pulse", "cam2_zoom": "none"})
    # кам1 ключи и масштаб идентичны
    m_jsx = re.search(r"var CAM1_SCALE=\[.*?\];", jsx)
    m_base = re.search(r"var CAM1_SCALE=\[.*?\];", base)
    assert m_jsx and m_base and m_jsx.group(0) == m_base.group(0)


def test_cam2_pan_moves_cam2null(xml_subs):
    """cam2_pan_x/y: Position нула кам2 сдвинут ровно на pan."""
    st = {"cam2_pan_x": 40.0, "cam2_pan_y": -25.0}
    jsx = _jsx(xml_subs, st)
    assert 'cam2null.property("ADBE Transform Group").property("ADBE Position").setValue([580,935])' in jsx
    assert "CAM1_ROT" not in jsx


def test_cam2_rot_rotates_only_cam2_layers(xml_subs):
    """cam2_rot: поворот стоит на слоях камеры 2 и НЕ стоит на слоях камеры 1; cam1_rot — наоборот."""
    st = {"cam1_rot": -5.0, "cam2_rot": 12.0}
    jsx = _jsx(xml_subs, st)
    assert "var CAM1_ROT=-5;" in jsx
    assert "var CAM2_ROT=12;" in jsx
    assert 'if(!isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' in jsx
    assert 'if(isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM2_ROT); }catch(e){} }' in jsx
    assert 'if(ci==0){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' in jsx
    assert 'if(ci==1){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM2_ROT); }catch(e){} }' in jsx


def test_cam2_fit_times_zoom(xml_subs):
    """fit × зум: при cam2_zoom=pulse, cam2_fit=120 каждый ключ кам2 = ключ без fit × 1.2."""
    p100 = xml2ae.scene_plan(xml_subs, **_call({"cam2_zoom": "pulse", "cam2_fit": 100.0}))
    p120 = xml2ae.scene_plan(xml_subs, **_call({"cam2_zoom": "pulse", "cam2_fit": 120.0}))
    k100 = p100["zoom"]["cam2"]["keys"]
    k120 = p120["zoom"]["cam2"]["keys"]
    assert len(k100) == len(k120) and len(k100) > 1
    for a, b in zip(k100, k120):
        assert a[0] == b[0]
        assert b[1] == pytest.approx(round(a[1] * 1.2, 2))


def test_pulse_keys_on_cam1_to_cam2_cuts_not_returns(xml_subs):
    """2. pulse: ключи кам2 стоят на срезах кам1→кам2, а НЕ на возвратах на кам1."""
    meta, cams, subs, _ = xml2ae.parse_full(xml_subs)
    segs = _show_segments(cams)
    entries = [s[0] for s in segs if s[2] == 1 and s[1] > s[0]]
    returns = _cam1_return_frames(cams)
    assert entries, "фикстура обязана иметь входы на камеру 2"
    assert returns, "фикстура обязана иметь возвраты на камеру 1"

    st = {"cam1_zoom": "none", "cam2_zoom": "pulse"}
    plan = xml2ae.scene_plan(xml_subs, **_call(st))
    c2_data = plan["zoom"].get("cam2")
    assert c2_data is not None, "при cam2_zoom='pulse' план обязан иметь zoom.cam2"

    c2_keys = c2_data["keys"]
    c2_key_frames = [k[0] for k in c2_keys]

    # Каждый срез входа на кам2 должен быть в ключах кам2
    for e in entries:
        assert e in c2_key_frames, f"срез кам1->кам2 {e} отсутствует в ключах кам2"
    # Ни один срез возврата на кам1 не должен быть в ключах кам2
    for r in returns:
        assert r not in c2_key_frames, f"возврат на кам1 {r} ошибочно попал в ключи кам2"

    # Независимость: изменение настроек зума кам1 не влияет на ключи кам2
    plan_c1_mod = xml2ae.scene_plan(xml_subs, **_call(dict(st, cam1_zoom="jump", cam1_zoom_big=250)))
    assert plan_c1_mod["zoom"]["cam2"]["keys"] == c2_keys, "изменение кам1 изменило ключи кам2"

    # И наоборот: изменение настроек зума кам2 не влияет на ключи кам1
    base_plan = xml2ae.scene_plan(xml_subs, **_call({"cam1_zoom": "pulse"}))
    c2_mod_plan = xml2ae.scene_plan(xml_subs, **_call({"cam1_zoom": "pulse", "cam2_zoom": "pulse", "cam2_zoom_big": 250}))
    assert base_plan["zoom"]["keys"] == c2_mod_plan["zoom"]["keys"], "изменение кам2 изменило ключи кам1"


def test_jump_takes_and_drift_within_limits(xml_subs):
    """3. jump + наезды в тейках и drift для кам2 дают ключи в пределах своих lo/hi."""
    st_jump = {
        "cam1_zoom": "none",
        "cam2_zoom": "jump",
        "cam2_zoom_lo": 110.0,
        "cam2_zoom_hi": 130.0,
        "cam2_take_zoom": True,
        "cam2_take_min": 4.0,
        "cam2_take_lo": 10.0,
        "cam2_take_hi": 20.0,
        "cam2_take_hold": 1.5,
    }
    plan_jump = xml2ae.scene_plan(xml_subs, **_call(st_jump))
    jkeys = plan_jump["zoom"]["cam2"]["keys"]
    assert len(jkeys) > 1
    # Значения ключей не должны быть меньше 100
    for k in jkeys:
        assert k[1] >= 100.0, f"ключ jump {k} меньше 100"

    st_drift = {
        "cam1_zoom": "none",
        "cam2_zoom": "drift",
        "cam2_drift_lo": 105.0,
        "cam2_drift_hi": 125.0,
    }
    plan_drift = xml2ae.scene_plan(xml_subs, **_call(st_drift))
    dkeys = plan_drift["zoom"]["cam2"]["keys"]
    assert len(dkeys) > 1
    for k in dkeys:
        assert k[1] >= 100.0, f"ключ drift {k} меньше 100"


def test_cam1_keys_invariant_to_cam2_settings(xml_subs):
    """4. Ключи кам1 при любых настройках кам2 — те же, что до правки."""
    base_plan = xml2ae.scene_plan(xml_subs, **_call({"cam1_zoom": "jump"}))
    ref_keys = base_plan["zoom"]["keys"]
    ref_holds = base_plan["zoom"]["holds"]

    for c2_mode in ("none", "pulse", "jump", "drift"):
        p = xml2ae.scene_plan(xml_subs, **_call({"cam1_zoom": "jump", "cam2_zoom": c2_mode,
                                                 "cam2_zoom_big": 220, "cam2_drift_hi": 170}))
        assert p["zoom"]["keys"] == ref_keys, f"cam2_zoom={c2_mode} изменил ключи кам1"
        assert p["zoom"]["holds"] == ref_holds


def test_migration_cam2_zoom_on():
    """5. Миграция: cam2_zoom_on=True -> cam2_* = cam1_*; False/нет -> none."""
    old_on = {"cam2_zoom_on": True, "cam1_zoom": "drift", "cam1_zoom_big": 175.0, "cam1_drift_hi": 155.0}
    res_on = styles.resolve(old_on)
    assert "cam2_zoom_on" not in res_on
    assert res_on["cam2_zoom"] == "drift"
    assert res_on["cam2_zoom_big"] == 175.0
    assert res_on["cam2_drift_hi"] == 155.0

    old_off = {"cam2_zoom_on": False, "cam1_zoom": "jump"}
    res_off = styles.resolve(old_off)
    assert "cam2_zoom_on" not in res_off
    assert res_off["cam2_zoom"] == "none"

    empty = styles.resolve({})
    assert "cam2_zoom_on" not in empty
    assert empty["cam2_zoom"] == "none"

    # JS стенд миграции stMigrateCam2Zoom
    with open(PICK, "r", encoding="utf-8") as f:
        pick = f.read()
    mig_js = _func(pick, "stMigrateCam2Zoom")
    code = mig_js + """
    const r1 = stMigrateCam2Zoom({cam2_zoom_on: true, cam1_zoom: 'jump', cam1_zoom_big: 190});
    const r2 = stMigrateCam2Zoom({cam2_zoom_on: false, cam1_zoom: 'jump'});
    const r3 = stMigrateCam2Zoom({cam1_zoom: 'jump'});
    console.log(JSON.stringify({r1, r2, r3}));
    """
    out = _run_node(code)
    assert out["r1"]["cam2_zoom"] == "jump" and out["r1"]["cam2_zoom_big"] == 190
    assert "cam2_zoom_on" not in out["r1"]
    assert out["r2"]["cam2_zoom"] == "none"
    assert out["r3"]["cam2_zoom"] == "none"


def test_schema_cam2_section_and_fields():
    """6. Схема: раздел cam2 идёт сразу после cam1, зеркальные группы tr и zoom."""
    sch = schema()
    layers = sch["layers"]
    sec_ids = [s["id"] for s in layers]
    assert "cam1" in sec_ids and "cam2" in sec_ids
    idx1 = sec_ids.index("cam1")
    idx2 = sec_ids.index("cam2")
    assert idx2 == idx1 + 1, "раздел cam2 обязан идти сразу после cam1"

    c1_sec = layers[idx1]
    c2_sec = layers[idx2]

    # В cam1.tr нет ключей cam2
    c1_tr = next(g for g in c1_sec["items"] if g["id"] == "cam1.tr")
    c1_tr_keys = [it["key"] for it in c1_tr["items"]]
    for k in c1_tr_keys:
        assert not k.startswith("cam2_"), f"в cam1.tr остался ключ {k}"

    # В cam2 есть группы cam2.tr, cam2.head и обёртка cam2.zoom (внутри которой 5 подгрупп зума)
    c2_group_ids = [g["id"] for g in c2_sec["items"]]
    assert "cam2.tr" in c2_group_ids
    assert "cam2.head" in c2_group_ids
    assert "cam2.zoom" in c2_group_ids
    for sub in ("zoom_start", "zoom_cut", "zoom_take", "zoom_yellow", "zoom_cycle"):
        assert f"cam2.{sub}" not in c2_group_ids

    c2_zoom = next(g for g in c2_sec["items"] if g["id"] == "cam2.zoom")
    c1_zoom = next(g for g in c1_sec["items"] if g["id"] == "cam1.zoom")
    c2_sub_ids = [g["id"] for g in c2_zoom["items"]]
    for sub in ("zoom_start", "zoom_cut", "zoom_take", "zoom_yellow", "zoom_cycle"):
        assert f"cam2.{sub}" in c2_sub_ids

    c2_tr = next(g for g in c2_sec["items"] if g["id"] == "cam2.tr")
    c2_tr_keys = [it["key"] for it in c2_tr["items"]]
    assert c2_tr_keys == [
        "cam2_fit", "cam2_zoom_cx", "cam2_pan_x", "cam2_pan_y", "cam2_rot",
    ]
    c1_labels = [it["label"] for it in c1_tr["items"]]
    c2_labels = [it["label"] for it in c2_tr["items"]]
    assert c1_labels == c2_labels

    c1_head = next(g for g in c1_sec["items"] if g["id"] == "cam1.head")
    c2_head = next(g for g in c2_sec["items"] if g["id"] == "cam2.head")
    assert c1_head.get("fx") is True and c1_head.get("toggle") == "cam1_head_follow"
    assert c2_head.get("fx") is True and c2_head.get("toggle") == "cam2_head_follow"
    assert [it["key"] for it in c1_head["items"]] == ["cam1_head_x", "cam1_head_smooth", "cam1_head_min"]
    assert [it["key"] for it in c2_head["items"]] == ["cam2_head_x", "cam2_head_smooth", "cam2_head_min"]

    # Поля подгрупп cam2.zoom_* зеркальны cam1.zoom_*
    for sub in ("zoom_start", "zoom_cut", "zoom_take", "zoom_yellow", "zoom_cycle"):
        c1_sub = next(g for g in c1_zoom["items"] if g["id"] == f"cam1.{sub}")
        c2_sub = next(g for g in c2_zoom["items"] if g["id"] == f"cam2.{sub}")
        c1_keys = [it["key"] for it in c1_sub["items"]]
        c2_keys = [it["key"] for it in c2_sub["items"]]
        assert len(c1_keys) == len(c2_keys)
        for k1, k2 in zip(c1_keys, c2_keys):
            assert k2 == k1.replace("cam1_", "cam2_")


_SIM = r"""
const fs = require('fs');
const layers = [];
function mk(rec) {
  const kids = {};
  return {
    value: { resetCharStyle: ()=>{}, resetParagraphStyle: ()=>{} },
    get numKeys() { return rec.times.length; },
    setValue: (v) => { rec.vals.push(v); },
    setValueAtTime: (t, v) => { rec.times.push(t); rec.keyvals.push(v); },
    addProperty: () => mk({vals:[],times:[],keyvals:[],kids:{}}),
    property: (n) => { if (!kids[n]) { kids[n] = mk({vals:[],times:[],keyvals:[],kids:{}}); rec.kids[n] = kids[n]._rec; }
                       return kids[n]; },
    setInterpolationTypeAtKey: ()=>{}, setTemporalEaseAtKey: ()=>{}, setTemporalContinuousAtKey: ()=>{},
    setSpatialTangentsAtKey: ()=>{}, _rec: rec
  };
}
class L {
  constructor(name) { this.name = name; this._rec = {vals:[],times:[],keyvals:[],kids:{}}; this._root = mk(this._rec); }
  property(n) { return this._root.property(n); }
  remove() {} moveToBeginning() {} moveBefore() {} moveAfter() {} setTrackMatte() {}
}
class C {
  constructor(name) { this.name = name;
    this.layers = {
      add: (it) => { const l = new L((it&&it.name)||'layer'); layers.push(l); return l; },
      addNull: () => { const l = new L('Null'); layers.push(l); return l; },
      addShape: () => { const l = new L('Shape'); layers.push(l); return l; },
      addText: (t) => { const l = new L('Text: '+t); layers.push(l); return l; },
      get length() { return layers.length; }
    }; }
  layer(i) { return layers[i-1]; } openInViewer() {}
}
const app = { project: { items: { addComp: (n)=>new C(n), addFolder: ()=>({}) },
  importFile: (io)=>({ name: io.file ? io.file.name : 'imported', width: 1080, height: 1920, duration: 10 }) },
  beginUndoGroup: ()=>{}, endUndoGroup: ()=>{} };
const File = function(p){ this.fsName=p; this.name=(p||'').split(/[\\\/]/).pop(); this.exists=true; };
const ImportOptions = function(f){ this.file=f; };
const TrackMatteType = { LUMA: 1 }; const BlendingMode = { ADD: 1 };
const KeyframeInterpolationType = { BEZIER: 1, HOLD: 2 };
const Shape = function(){}; const alert = ()=>{};
const $ = {writeln: ()=>{}, fileName: 'x.jsx'};
let jsx = fs.readFileSync(process.argv[1], 'utf8');
if (jsx.charCodeAt(0) === 0xFEFF) jsx = jsx.slice(1);
eval(jsx);
const EMPTY = {vals:[],times:[],keyvals:[]};
function tr(l, name) { const g = l._rec.kids['ADBE Transform Group'];
  return g && g.kids[name] ? g.kids[name] : EMPTY; }
const out = {};
for (const l of layers) if (/^Камера [12]$/.test(l.name))
  out[l.name] = {scaleTimes: tr(l,'ADBE Scale').times, scaleVals: tr(l,'ADBE Scale').keyvals,
                 anchor: tr(l,'ADBE Anchor Point').vals, pos: tr(l,'ADBE Position').vals};
console.log(JSON.stringify(out));
"""


def _sim(jsx_text, tmp_path):
    p = tmp_path / "out.jsx"
    p.write_text(jsx_text, encoding="utf-8")
    res = subprocess.run(["node", "-e", _SIM, str(p)], capture_output=True, text=True,
                         encoding="utf-8-sig", timeout=60)
    assert res.returncode == 0, res.stderr[-800:]
    return json.loads(res.stdout)


@node
def test_on_jsx_cam2_null_gets_own_keys_and_own_anchor(xml_subs, tmp_path):
    """7. Вкл: нул Камеры 2 получает свои ключи масштаба и якорь/позицию от cam2_zoom_*."""
    st = dict(ZOOM, cam2_zoom="pulse", cam2_zoom_cx=0.3, cam2_zoom_cy=0.7)
    jsx = _jsx(xml_subs, st)
    assert "var CAM2_SCALE=" in jsx
    assert "var CAM2_HOLDS=" in jsx
    assert "var CAM2_EASE=" in jsx
    got = _sim(jsx, tmp_path)
    c1, c2 = got["Камера 1"], got["Камера 2"]
    assert c1["scaleTimes"], "у Камеры 1 должны быть ключи"
    assert c2["scaleTimes"], "у Камеры 2 должны быть ключи"
    # Моменты ключей кам1 и кам2 разные, так как стоят на разных срезах
    assert c2["scaleTimes"] != c1["scaleTimes"]
    w, h = 1080, 1920
    assert c2["anchor"][-1] == pytest.approx([0.3 * w - w / 2, 0.7 * h - h / 2])
    assert c2["pos"][-1] == pytest.approx([0.3 * w, 0.7 * h])


@node
def test_on_default_point_no_anchor_but_keys(xml_subs, tmp_path):
    """Точка 0.5/0.5: якорь не пишется, ключи есть; none — камера 2 без ключей."""
    got = _sim(_jsx(xml_subs, dict(ZOOM, cam2_zoom="pulse")), tmp_path)
    assert got["Камера 2"]["anchor"] == [] and got["Камера 2"]["scaleTimes"]
    off = _sim(_jsx(xml_subs, dict(ZOOM, cam2_zoom="none")), tmp_path)
    assert off["Камера 2"]["scaleTimes"] == [], "cam2_zoom=none: камера 2 обязана быть без ключей"


def _view(*names):
    with open(VIEW, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in names)


@node
def test_preview_cam2_scale_and_fixed_point():
    """8. Превью: кадр камеры 2 учитывает fit, pan и rot из zoom.cam2."""
    code = _view("keysAt", "ipvZoomAt", "ipvCam2Point", "ipvCamShift", "ipvCamMatrix",
                 "ipvLmSmooth", "ipvLumetriTone", "ipvLumetriTable",
                 "ipvLumetriFilter", "ipvFrameGeom", "ipvDrawFrame",
                 "ipvCamPaint") + _PLAN_JS + """
    function run(withCam2, rotVal){
      const calls = [];
      const ctx = {setTransform: function(){calls.push(['t'].concat([].slice.call(arguments)));},
        clearRect: ()=>{}, drawImage: function(){calls.push(['d'].concat([].slice.call(arguments).slice(1)));}};
      const cv = {width: 0, height: 0, style: {}, _c: ctx, getContext: () => ctx};
      const st = {getBoundingClientRect: () => ({width: 1080, height: 1920})};
      globalThis.$ = function(id){return id === 'ipvcam' ? cv : (id === 'ipvstage' ? st : null);};
      globalThis.window = {devicePixelRatio: 1};
      globalThis.lutApply = (ci,v)=>v; globalThis.lutKS = ()=>[1,1];
      const pl = zoomPlan(120, 0, [0, 0], 0.5, 0.5);
      if (withCam2) {
        pl.zoom.cam2 = {cx: 0.3, cy: 0.7, fit: 100, pan: [40, -30], rot: rotVal || 0,
                        keys: [[0, 160]], holds: [0], ease: [[16.6667, 16.6667]]};
      }
      globalThis.IPV = {curCi: 1, fps: 60, vids: [{}, {readyState: 4, videoWidth: 1080, videoHeight: 1920}], plan: pl};
      ipvCamPaint(ipvZoomAt(0));
      return {calls: calls, s2: ipvZoomAt(0, 'cam2')};
    }
    const onRes = run(true, 0), offRes = run(false, 0), rotRes = run(true, 90);
    const on = onRes.calls, off = offRes.calls, rotCalls = rotRes.calls;
    const tOn = on.filter(c => c[0] === 't').pop().slice(1);
    const tOff = off.filter(c => c[0] === 't').pop().slice(1);
    const tRot = rotCalls.filter(c => c[0] === 't').pop().slice(1);
    const drawOn = on.filter(c => c[0] === 'd').pop().slice(1);
    const drawOff = off.filter(c => c[0] === 'd').pop().slice(1);
    console.log(JSON.stringify({tOn, tOff, tRot, drawOn, drawOff, s2: onRes.s2}));
    """
    out = _run_node(code)
    s2 = out["s2"]
    assert s2 == pytest.approx(1.6)
    assert out["drawOn"] == out["drawOff"]
    a, b, c, d, e, f = out["tOn"]
    assert (a, b, c, d) == (pytest.approx(s2), 0, 0, pytest.approx(s2))
    # Сдвиг pan [40, -30] учтён в матрице вокруг точки наезда:
    assert a * (0.3 - 0.5) * 1080 + e == pytest.approx(0.3 * 1080 + 40)
    assert d * (0.7 - 0.5) * 1920 + f == pytest.approx(0.7 * 1920 - 30)
    # Выключено -> единичная матрица, центрированная в композиции
    assert out["tOff"] == [1, 0, 0, 1, 540, 960]
    # rot=90 учтён в матрице
    ra, rb, rc, rd, re_, rf_ = out["tRot"]
    assert (ra, rb, rc, rd) == (pytest.approx(0, abs=1e-6), pytest.approx(s2),
                                pytest.approx(-s2), pytest.approx(0, abs=1e-6))


@node
def test_pick_click_writes_visible_camera():
    """9. Клик выбора точки: видна камера 2 и включён её зум -> cam2_zoom_*, иначе cam1_zoom_*."""
    with open(PICK, "r", encoding="utf-8") as f:
        pick = f.read()
    with open(PANEL, "r", encoding="utf-8") as f:
        panel = f.read()
    fn = "\n".join([_func(pick, "isCam2Active"), _func(pick, "zoomPickTarget"),
                    _func(pick, "zoomPickKeys"), _func(panel, "applyZoomPoint")])
    code = fn + """
    function go(curCi, zoomMode, which){
      globalThis.IPV = {curCi: curCi};
      globalThis.CURSTYLE = {cam2_zoom: zoomMode};
      globalThis.STYLES = {base: {}};
      applyZoomPoint(0.25, 0.75, zoomPickTarget(which));
      return CURSTYLE;
    }
    console.log(JSON.stringify({c2: go(1, 'pulse'), c1_off: go(1, 'none'), c1_vis: go(0, 'pulse'),
                                forced: go(0, 'pulse', 'cam2')}));
    """
    out = _run_node(code)
    assert (out["c2"]["cam2_zoom_cx"], out["c2"]["cam2_zoom_cy"]) == (0.25, 0.75)
    assert "cam1_zoom_cx" not in out["c2"]
    for k in ("c1_off", "c1_vis"):
        assert (out[k]["cam1_zoom_cx"], out[k]["cam1_zoom_cy"]) == (0.25, 0.75)
        assert "cam2_zoom_cx" not in out[k]
    assert out["forced"]["cam2_zoom_cx"] == 0.25


def test_cam1_keys_pinned_to_values_before_cam2():
    """Ключи Камеры 1 во всех трёх режимах — ровно те числа, что были до появления зума Камеры 2.

    Сравнение «план с кам2 == план без кам2» этого не ловит: случайный разброс берётся от
    зерна по кадрам срезов, и сдвиг зерна меняет ключи в ОБОИХ планах одинаково.
    """
    from core.xml2ae.layout import _cam1_drift_keys, _cam1_jump_keys, _cam1_zoom_keys
    cams = [{"clips": [(0, 100, 0, 100, True), (220, 400, 220, 400, True), (520, 700, 520, 700, True)]},
            {"clips": [(100, 220, 100, 220, True), (400, 520, 400, 520, True)]}]
    assert _cam1_zoom_keys(cams, big=182, lo=112, hi=140, fps=60) == PIN_PULSE
    assert _cam1_jump_keys(cams, lo=100, hi=140, fps=60, big=182) == PIN_JUMP
    assert _cam1_drift_keys(cams, lo=100, hi=160, fps=60, big=182) == PIN_DRIFT


PIN_PULSE = [(0, 182), (62, 100.0), (220, 119.7), (282, 100.0), (520, 136.2), (582, 100.0)]
PIN_JUMP = [(0, 182.0, 1, 0), (62, 100.0, 2, 1), (100, 138.3, 0, 1), (220, 111.3, 0, 1),
            (400, 125.6, 0, 1), (520, 112.8, 0, 1)]
PIN_DRIFT = [(0, 182.0, 1), (62, 100.0, 2), (99, 130.8, 0), (100, 151.3, 0), (219, 124.5, 0),
             (220, 148.1, 0), (399, 137.6, 0), (400, 114.4, 0), (519, 134.1, 0), (520, 113.6, 0),
             (699, 140.0, 0)]


def test_cam2_head_follow_default_plan_and_golden(xml_subs, tmp_path):
    """10. Дефолт: golden побайтово, в плане нет zoom.cam2.follow."""
    from tests.test_geometry_python import _build as _golden_build, _mask_assets
    golden_path = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
    if os.path.isfile(golden_path):
        golden = _mask_assets(open(golden_path, "r", encoding="utf-8-sig").read())
        base_jsx = _mask_assets(_golden_build(xml_subs, tmp_path, {}))
        assert base_jsx == golden, "Дефолт разошёлся с golden"

    plan = xml2ae.scene_plan(xml_subs, style={})
    assert "cam2" not in plan.get("zoom", {}) or "follow" not in plan["zoom"]["cam2"]


def test_cam2_head_ranges_and_separate_sidecar(tmp_path):
    """11. Диапазоны камеры 2 = куски её показа; кэш камеры 1 читается прежним файлом (.head.json)."""
    from core import headtrack
    # cams: камера 1 показывает [0..100] и [200..300], камера 2 показывает [100..200]
    cams = [
        {"clips": [(0, 100, 0, 100, True), (200, 300, 200, 300, True)]},
        {"clips": [(100, 200, 50, 150, True)]},
    ]
    r1 = headtrack.cam1_ranges(cams, fps=50, cam=1)
    r2 = headtrack.cam1_ranges(cams, fps=50, cam=2)
    assert r1 == [(0.0, 2.0), (4.0, 6.0)]
    assert r2 == [(1.0, 3.0)]

    xml_fake = str(tmp_path / "proj.xml")
    p1 = headtrack.head_cache_path(xml_fake, cam=1)
    p2 = headtrack.head_cache_path(xml_fake, cam=2)
    assert p1.endswith(".head.json")
    assert p2.endswith(".head2.json")
    assert p1 != p2

    v1 = str(tmp_path / "c1.mp4")
    v2 = str(tmp_path / "c2.mp4")
    with open(v1, "wb") as f:
        f.write(b"video1")
    with open(v2, "wb") as f:
        f.write(b"video2")

    track1 = {"v": 1, "video": os.path.abspath(v1), "size": os.path.getsize(v1),
              "mtime": os.path.getmtime(v1), "ranges": [[0.0, 10.0]], "pts": [[0.0, 0.4]]}
    track2 = {"v": 1, "video": os.path.abspath(v2), "size": os.path.getsize(v2),
              "mtime": os.path.getmtime(v2), "ranges": [[0.0, 10.0]], "pts": [[0.0, 0.7]]}

    with open(p1, "w", encoding="utf-8") as f:
        json.dump(track1, f)
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(track2, f)

    loaded1 = headtrack.load_cached(xml_fake, v1, cam=1)
    loaded2 = headtrack.load_cached(xml_fake, v2, cam=2)
    assert loaded1 is not None and loaded1["pts"] == [[0.0, 0.4]]
    assert loaded2 is not None and loaded2["pts"] == [[0.0, 0.7]]


def test_cam2_head_follow_jsx_keys_and_cam1_untouched(xml_subs, tmp_path, monkeypatch):
    """12. С подложенным кэшем трека: CAM2_FOLLOW на нуле cam2; CAM1_FOLLOW прежние; мутация ловит подмену."""
    from core import headtrack
    track_c1 = {
        "v": 1, "fps": 10, "w": 1080, "h": 1920,
        "pts": [[0.0, 0.35], [5.0, 0.35], [10.0, 0.35], [15.0, 0.35]],
    }
    track_c2 = {
        "v": 1, "fps": 10, "w": 1080, "h": 1920,
        "pts": [[0.0, 0.75], [5.0, 0.75], [10.0, 0.75], [15.0, 0.75]],
    }

    def fake_load_cached(xml_path, video, ranges=None, cam=1):
        return track_c1 if cam == 1 else track_c2

    monkeypatch.setattr(headtrack, "load_cached", fake_load_cached)

    # 1. Сборка только с cam1_head_follow
    st_c1 = {"cam1_head_follow": True, "cam2_fit": 130.0}
    plan_c1 = xml2ae.scene_plan(xml_subs, style=st_c1)
    c1_follow_keys = plan_c1["zoom"]["follow"]["keys"]

    # 2. Сборка с cam1_head_follow и cam2_head_follow
    st_both = {"cam1_head_follow": True, "cam2_head_follow": True, "cam2_fit": 130.0}
    out_jsx = str(tmp_path / "both.jsx")
    res_path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=out_jsx, style=st_both,
                                       disclaimer="", emit=lambda *a: None)
    jsx = open(res_path, encoding="utf-8-sig").read()

    plan_both = xml2ae.scene_plan(xml_subs, style=st_both)
    # Ключи CAM1_FOLLOW не изменились
    assert plan_both["zoom"]["follow"]["keys"] == c1_follow_keys
    assert "var CAM1_FOLLOW=" in jsx

    # Ключи CAM2_FOLLOW присутствуют в плане и в .jsx
    assert "cam2" in plan_both["zoom"] and "follow" in plan_both["zoom"]["cam2"]
    c2_follow_keys = plan_both["zoom"]["cam2"]["follow"]["keys"]
    assert len(c2_follow_keys) > 0

    assert "var CAM2_FOLLOW=" in jsx
    assert "var cam2null = nulls.length>1 ? nulls[1] : null;" in jsx
    assert "if (cam2null && typeof CAM2_FOLLOW !== \"undefined\" && CAM2_FOLLOW.length)" in jsx

    # Мутация: если для камеры 2 ошибочно подсунуть кэш камеры 1 — ключи не сходятся
    def fake_load_cached_c1_only(xml_path, video, ranges=None, cam=1):
        return track_c1
    monkeypatch.setattr(headtrack, "load_cached", fake_load_cached_c1_only)
    plan_mutated = xml2ae.scene_plan(xml_subs, style=st_both)
    c2_mutated_keys = plan_mutated["zoom"]["cam2"]["follow"]["keys"]
    assert c2_follow_keys != c2_mutated_keys, "Мутация (кэш камеры 1 для камеры 2) не меняет ключи"


@node
def test_cam2_head_follow_preview_shift():
    """13. Превью: ipvCamShift(tm, 'cam2') = pan + значение слежения из zoom.cam2.follow."""
    code = _view("keysAt", "ipvCamShift") + """
    const IPV = {
      fps: 60,
      plan: {
        zoom: {
          pan: [10, 20],
          follow: {
            keys: [[0, 30], [60, 30]],
            ease: [[16.6667, 16.6667], [16.6667, 16.6667]]
          },
          cam2: {
            pan: [-15, 25],
            follow: {
              keys: [[0, 50], [60, 70]],
              ease: [[16.6667, 16.6667], [16.6667, 16.6667]]
            }
          }
        }
      }
    };
    const c1_shift0 = ipvCamShift(0, 'cam1');
    const c2_shift0 = ipvCamShift(0, 'cam2');
    const c2_shift1 = ipvCamShift(1.0, 'cam2');
    const c2_shift_num = ipvCamShift(0, 1);
    console.log(JSON.stringify({c1_shift0, c2_shift0, c2_shift1, c2_shift_num}));
    """
    out = _run_node(code)
    assert out["c1_shift0"] == [40, 20]
    assert out["c2_shift0"] == [35, 25]
    assert out["c2_shift1"] == [55, 25]
    assert out["c2_shift_num"] == [35, 25]



@node
def test_cam2_intro_rides_head_follow_in_preview():
    """Интро на камере 2 (едет с камерой) в превью сдвигается и слежением за головой.

    В AE нул интро на кам2 — ребёнок нула камеры 2, а слежение ставит ключи на X этого нула.
    Превью обязано брать сдвиг той же дверью ipvCamShift, иначе текст стоит, а кадр едет.
    """
    code = _view("keysAt", "ipvZoomAt", "ipvCam2Point", "ipvCamShift", "ipvIntroChild") + """
    function ipvNow(){ return 0; }
    const IPV = {fps: 60, plan: {w: 1080, h: 1920, intro_cam2: true, zoom: {
      cx: 0.5, cy: 0.5, keys: [[0, 100]], ease: [[16.6667, 16.6667]],
      cam2: {cx: 0.5, cy: 0.5, pan: [0, 0], keys: [[0, 100]], holds: [0], ease: [[16.6667, 16.6667]],
             follow: {keys: [[0, 80], [60, 80]], ease: [[16.6667, 16.6667], [16.6667, 16.6667]]}}}}};
    console.log(JSON.stringify({p: ipvIntroChild(100, 50, 0, true)}));
    """
    out = _run_node(code)
    assert out["p"][0] == 180, "интро на камере 2 не поехало за головой"
    assert out["p"][1] == 50
