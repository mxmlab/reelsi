# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание ZM: интро можно открепить от движения камеры.

Нулы «интро» и «интро на кам2» (и затемнение под интро) привязаны к нулу «Камера 1» —
текст живёт её зумом, сдвигом и слежением за головой. Галка стиля `intro_cam`
(дефолт True, «интро едет с камерой») это открепляет: нулы уходят на ветку else —
позиция в координатах кадра, — автофит перестаёт умножать зум камеры, а предпросмотр
рисует блок и затемнение без ipvCamChild (одна функция-выбор на оба места).

Проверяет:
  1. дефолт (ключа нет / True) — .jsx побайтово прежний (эталон golden держится),
     объявления INTRO_CAM в скрипте нет вовсе;
  2. `intro_cam=False` — у нулов интро нет `parent=cam1null`, у затемнения тоже;
     исполнение .jsx в node с заглушками AE подтверждает: родителя нет, позиция —
     координаты кадра (центр + сдвиги), а не локальная система нула камеры;
  3. автофит: при зуме камеры 300% и `intro_cam=False` ds группы равен расчёту для
     зума 100% (привязанное интро той же строкой ужимается сильнее);
  4. предпросмотр (node, боевые функции из static/app/85-inserts-view.js): при
     `intro_cam=false` позиция интро и затемнения не зависит от зума/сдвига/слежения;
  5. сторож «каждая ручка»: галка есть в схеме и в BASE, и меняет собранный .jsx.
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

from core import fonts, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_FIT_W  # noqa: E402
from test_geometry_python import _build as _geo_build, _mask_assets  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402

JS_REL = ("static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

T_CAM1, T_CAM2 = 1.0, 8.3
# Группа на камере 1 и группа на перебивке: у каждой свой нул («интро» / «интро на кам2»).
INTRO = [
    dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
    dict(words=["ВТОРАЯ", "КАМЕРА"], color="white", times=[T_CAM2]),
]
OFF = {"intro_cam": False, "intro_shade": True, "intro_x": 60, "intro_y": 100, "intro_y2": 40}
ON = dict(OFF, intro_cam=True)
# Одна строка заведомо шире кадра (10 букв × кегль 140 = 1400 px при W=1080) — для автофита.
WIDE = [dict(words=["A" * 10], color="white", times=[T_CAM1])]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминированная цензура: поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture()
def wide_font(monkeypatch):
    """Ширина текста известна и от системных шрифтов не зависит: буква = ровно кегль."""
    monkeypatch.setattr(fonts, "text_width", lambda ps, text, size: float(size) * len(text))


def _build(xml, tmp_path, style=None, intro=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=INTRO if intro is None else intro,
                                   intro_splits=[1], style=dict(style or {}),
                                   intro_mode="word", disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _scene(xml, style, intro=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=INTRO if intro is None else intro,
                             intro_splits=[1], style=dict(style), emit=lambda *a: None)


# ============================================================ 1. дефолт = эталон

def test_default_style_builds_the_same_jsx(xml_subs, tmp_path):
    """1. Ключа нет или он True — .jsx прежний: подстановки пусты, INTRO_CAM не объявлен.

    Сверка с эталоном побайтовая (маскируются только пути ассетов машины) — тем же
    способом, что в test_geometry_python: golden обновлять нельзя, он и есть страховка.
    """
    golden = open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                  encoding="utf-8-sig").read()
    raw = _geo_build(xml_subs, tmp_path, style={})
    assert "INTRO_CAM" not in raw, "при дефолте в .jsx появилось упоминание INTRO_CAM"
    assert _mask_assets(raw) == _mask_assets(golden), \
        "сборка со стилем по умолчанию разошлась с эталоном golden_geometry.jsx"

    default_jsx = _build(xml_subs, tmp_path, name="default.jsx")[0]
    true_jsx = _build(xml_subs, tmp_path, style={"intro_cam": True}, name="true.jsx")[0]
    assert default_jsx == true_jsx, "явное intro_cam=True меняет .jsx"
    assert "INTRO_CAM" not in default_jsx
    assert "if(cam1null){ introNull.parent=cam1null;" in default_jsx, \
        "по умолчанию нул «интро» обязан висеть на нуле Камеры 1"
    assert "if(cam1null){ introNull2.parent=cam1null;" in default_jsx


# ==================================================== 2. открепление в .jsx

def test_jsx_with_intro_cam_off_unparents_the_intro_nulls(xml_subs, tmp_path):
    """2. `intro_cam=False` — условие привязки несёт INTRO_CAM, и он объявлен false.

    Нулов «интро» два (кам1 и кам2) — обоих касается одно условие; затемнение под
    интро открепляется тем же условием (одна галка — одно поведение).
    """
    off = _build(xml_subs, tmp_path, style=OFF, name="off.jsx")[0]
    assert "var INTRO_CAM=false;" in off, "в .jsx нет объявления INTRO_CAM=false"
    assert off.count("if(cam1null && INTRO_CAM){") == 2, \
        "условие INTRO_CAM стоит не у обоих нулов интро"
    assert "if(cam1null && INTRO_CAM){ introNull.parent=cam1null;" in off
    assert "if(cam1null && INTRO_CAM){ introNull2.parent=cam1null;" in off
    # условие привязки без галки в сборке остаться не должно ни у одного нула
    assert "if(cam1null){ introNull.parent=cam1null;" not in off
    assert "if(cam1null){ introNull2.parent=cam1null;" not in off
    assert "if (cam1null && INTRO_CAM){ shadeLayer.parent=cam1null;" in off, \
        "затемнение под интро осталось на нуле Камеры 1"
    assert "if (cam1null){ shadeLayer.parent=cam1null;" not in off

    on = _build(xml_subs, tmp_path, style=ON, name="on.jsx")[0]
    assert "INTRO_CAM" not in on, "при intro_cam=True в .jsx попал INTRO_CAM"
    assert "if(cam1null){ introNull.parent=cam1null;" in on
    assert "if(cam1null){ introNull2.parent=cam1null;" in on
    assert "if (cam1null){ shadeLayer.parent=cam1null;" in on


# Исполнение .jsx в node: заглушка AE ведёт стек слоёв и запоминает родителя и
# Position каждого слоя. Взято из стенда test_fm_layer_order (simulate_jsx_stack),
# добавлены ровно две вещи: parent у слоя и запись setValue по пути свойства.
JSX_LAYERS_JS = r'''
const fs = require('fs');
let mainComp = null;
function mockProp(owner, path) {
  return {
    value: { resetCharStyle: ()=>{}, resetParagraphStyle: ()=>{} },
    numKeys: 0,
    setValue: (v)=>{ if (owner && path) owner._set[path] = v; },
    setValueAtTime: ()=>{},
    addProperty: (n)=>mockProp(owner, path+'/'+n),
    property: (n)=>mockProp(owner, path ? path+'/'+n : n),
    setInterpolationTypeAtKey: ()=>{}
  };
}
class MockLayer {
  constructor(name, type, comp) {
    this.name = name || 'Layer'; this.type = type || 'layer'; this._comp = comp;
    this.parent = null; this._set = {};
  }
  property(n) { return mockProp(this, n); }
  remove() { const s = this._comp._stack; const i = s.indexOf(this); if (i >= 0) s.splice(i, 1); }
  moveToBeginning() { const s = this._comp._stack; const i = s.indexOf(this);
    if (i >= 0) s.splice(i, 1); s.unshift(this); }
  moveBefore(o) { const s = this._comp._stack; const i = s.indexOf(this);
    if (i >= 0) s.splice(i, 1); const j = s.indexOf(o);
    if (j >= 0) s.splice(j, 0, this); else s.unshift(this); }
  moveAfter(o) { const s = this._comp._stack; const i = s.indexOf(this);
    if (i >= 0) s.splice(i, 1); const j = s.indexOf(o);
    if (j >= 0) s.splice(j + 1, 0, this); else s.push(this); }
  setTrackMatte() {}
}
class MockComp {
  constructor(name) {
    this.name = name; this._stack = [];
    const self = this;
    this.layers = {
      add: (item) => { const l = new MockLayer((item&&item.name)||'layer', 'layer', self);
        self._stack.unshift(l); return l; },
      addNull: () => { const l = new MockLayer('Null', 'null', self);
        l.nullLayer = true; self._stack.unshift(l); return l; },
      addShape: () => { const l = new MockLayer('Shape', 'shape', self);
        self._stack.unshift(l); return l; },
      addText: (txt) => { const l = new MockLayer('Text: ' + txt, 'text', self);
        self._stack.unshift(l); return l; },
      get length() { return self._stack.length; }
    };
  }
  layer(idx) { return this._stack[idx - 1]; }
  openInViewer() { mainComp = this; }
}
const app = {
  project: {
    items: { addComp: (n) => new MockComp(n), addFolder: () => ({}) },
    importFile: (io) => ({ name: io.file ? io.file.name : 'imported',
                           width: 1080, height: 1920, duration: 10 })
  },
  beginUndoGroup: () => {},
  endUndoGroup: () => {}
};
const File = function(p) { this.fsName = p; this.name = (p||'').split(/[\\\/]/).pop();
  this.exists = true; };
const ImportOptions = function(f) { this.file = f; };
const TrackMatteType = { LUMA: 1 };
const BlendingMode = { ADD: 1 };
const KeyframeInterpolationType = { BEZIER: 1, HOLD: 2, LINEAR: 3 };
const Shape = function() {};
const alert = () => {};
let jsx = fs.readFileSync(process.argv[1], 'utf8');
if (jsx.charCodeAt(0) === 0xFEFF) jsx = jsx.slice(1);
eval(jsx);
const out = (mainComp ? mainComp._stack : []).map(l => ({
  name: l.name, parent: l.parent ? l.parent.name : null,
  pos: l._set['ADBE Transform Group/ADBE Position'] || null
}));
console.log(JSON.stringify(out));
'''


def _jsx_layers(path):
    """Слои главной композиции .jsx после исполнения в node: имя, родитель, Position."""
    res = subprocess.run(["node", "-e", JSX_LAYERS_JS, str(path)], capture_output=True,
                         text=True, encoding="utf-8-sig", timeout=60)
    assert res.returncode == 0, f"ошибка симуляции .jsx: {res.stderr}"
    return {lay["name"]: lay for lay in json.loads(res.stdout.strip().splitlines()[-1])}


def test_executed_jsx_keeps_the_detached_nulls_in_frame_coordinates(xml_subs, tmp_path):
    """2б. По исполнению: у откреплённых нулов родителя нет, позиция — координаты кадра.

    Привязанный нул живёт в ЛОКАЛЬНОЙ системе нула Камеры 1 ([intro_x, INTRO_Y]), а
    откреплённый встаёт в координаты кадра ({W/2+intro_x, H/2+INTRO_Y}) — эта ветка
    else в шаблоне уже была, галка лишь выбирает её.
    """
    _, off_path = _build(xml_subs, tmp_path, style=OFF, name="exec_off.jsx")
    off = _jsx_layers(off_path)
    for name in ("интро", "интро на кам2", "Затемнение интро"):
        assert name in off, f"в сборке нет слоя {name!r}"
        assert off[name]["parent"] is None, f"{name}: остался ребёнком {off[name]['parent']}"
    assert off["интро"]["pos"] == [1080 / 2 + 60, 1920 / 2 + 100]
    assert off["интро на кам2"]["pos"] == [1080 / 2 + 60, 1920 / 2 + 100 + 40]
    shade = _scene(xml_subs, OFF)["shade"]
    assert off["Затемнение интро"]["pos"] == [1080 / 2 + shade["x"], 1920 / 2 + shade["y"]]

    _, on_path = _build(xml_subs, tmp_path, style=ON, name="exec_on.jsx")
    on = _jsx_layers(on_path)
    for name in ("интро", "интро на кам2", "Затемнение интро"):
        assert on[name]["parent"] == "Камера 1", f"{name}: родитель не нул Камеры 1"
    assert on["интро"]["pos"] == [60, 100]
    assert on["интро на кам2"]["pos"] == [60, 140]
    assert on["Затемнение интро"]["pos"] == [shade["x"], shade["y"]]


# ============================================================ 3. автофит

def test_autofit_ignores_camera_zoom_when_detached(wide_font, xml_subs):
    """3. Откреплённый текст зумом камеры не растёт: ds считается как при зуме 100%.

    Строка заведомо шире кадра (10 букв × кегль 140 = 1400 px). Привязанное интро
    ужимается зумом 300% втрое сильнее — автофит обязан увидеть зум; откреплённое
    считает по 100%: lineW·0.968·(ds/100) = W·0.92.
    """
    big = _scene(xml_subs, {"cam1_zoom_big": 300}, intro=WIDE)
    big_off = _scene(xml_subs, {"cam1_zoom_big": 300, "intro_cam": False}, intro=WIDE)
    flat = _scene(xml_subs, {"cam1_zoom": "none"}, intro=WIDE)

    assert max(k[1] for k in big["zoom"]["keys"]) > 100, \
        "предпосылка теста: на окне группы обязан быть зум камеры больше 100%"
    ds_big = big["intro"][0]["ds"]
    ds_off = big_off["intro"][0]["ds"]
    ds_flat = flat["intro"][0]["ds"]

    assert ds_big < ds_off, "зум привязанного интро не ужал строку — автофит не работает"
    assert ds_off == ds_flat, "откреплённый автофит всё ещё считает зум камеры"

    w = big_off["w"]
    expected = 100.0 * w * INTRO_FIT_W / (1400 * 0.968)
    assert ds_off == pytest.approx(expected, rel=1e-6), \
        "ds откреплённой группы посчитан не по формуле с зумом 100%"
    assert ds_off < 100, "строка шире кадра — автофит обязан был её ужать"


# ============================================================ 4. предпросмотр

DOM_SIM = r"""
const assert = require('assert');
const REG = {};
class El {
  constructor(tag){
    this.tagName = tag.toUpperCase(); this.style = {}; this.dataset = {};
    this._children = []; this.parent = null; this._id = null;
  }
  set id(v){ this._id = v; REG[v] = this; }
  get id(){ return this._id; }
  set className(v){ this._cn = String(v || ''); }
  get className(){ return this._cn || ''; }
  get children(){ return this._children; }
  appendChild(c){ this._children.push(c); c.parent = this; return c; }
  insertBefore(c, ref){ const i = this._children.indexOf(ref);
    if (i < 0) this._children.push(c); else this._children.splice(i, 0, c);
    c.parent = this; return c; }
  remove(){ if (this.parent){ const i = this.parent._children.indexOf(this);
      if (i >= 0) this.parent._children.splice(i, 1); }
    if (REG[this._id] === this) delete REG[this._id]; this.parent = null; }
}
const stage = new El('div'); stage.id = 'ipvstage'; stage.clientWidth = 540;   // k = 540/1080 = 0.5
const intro = new El('div'); intro.id = 'ipvintro'; intro.clientWidth = 540;
stage.appendChild(intro);
global.$ = (id) => REG[id] || null;
global.document = { createElement: (t) => new El(t) };
global.ipvNow = () => 2.0;
// Машина кадра Камеры 1 (зум, pan, слежение) подменена заглушкой: у неё свои тесты
// (test_zoom_z*). Здесь проверяется ровно одно — берут ли её числа интро и затемнение.
const CAM = { s: 2.0, shift: [300, -200] };
global.ipvZoomAt = () => CAM.s;
global.ipvCamShift = () => CAM.shift;
global.IPV = { fps: 60, plan: @PLAN@ };
"""

SHADE = {"x": -4, "y": 553, "scale": 94, "w": 1416, "h": 1052,
         "ox": -20, "oy": 610, "blur": 653, "op": 40}


def _func(src, name):
    """Тело функции из исходника: от `function name(` до парной закрывающей скобки."""
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


def _run_node(tmp_path, name, script):
    node_file = str(tmp_path / name)
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)
    return subprocess.run(["node", node_file], capture_output=True, text=True,
                          encoding="utf-8-sig", timeout=60)


def _preview_script(checks):
    """Боевые функции предпросмотра + заглушка DOM/камеры + проверки."""
    src = open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()
    body = "\n".join(_func(src, n) for n in
                     ("ipvCamChild", "ipvShade", "ipvIntroChild", "ipvIntroPos"))
    plan = {"w": 1080, "h": 1920, "intro_cam": False, "zoom": {"cx": 0.5, "cy": 0.5},
            "intro_scale": 100, "shade": dict(SHADE),
            "intro": [{"dx": 0, "dy": 0, "y": 100, "ds": 100}]}
    return DOM_SIM.replace("@PLAN@", json.dumps(plan, ensure_ascii=False)) + "\n" + body \
        + "\n" + checks


@node
def test_node_preview_intro_ignores_camera_when_detached(tmp_path):
    """4. `plan.intro_cam === false` — блок интро стоит на месте при любом зуме и сдвиге.

    С одной и той же базой (y=100, k=0.5) transform обязан быть translate(0px,50px)
    scale(0.968): ни зума камеры, ни pan, ни слежения. При включённой галке те же
    числа камеры блок двигают — значит проверка не проходит на пустом месте.
    """
    checks = r"""
    const io = $('ipvintro');
    const tf = () => io.style.transform;
    IPV.plan.intro_cam = false;
    ipvIntroPos(0, 2.0);
    const off1 = tf();
    CAM.s = 3.5; CAM.shift = [700, -500];
    ipvIntroPos(0, 2.0);
    const off2 = tf();
    assert.strictEqual(off1, off2,
      'при intro_cam=false позиция интро поехала за камерой: ' + off1 + ' -> ' + off2);
    assert(off1.indexOf('scale(0.968)') >= 0, 'зум камеры остался в масштабе блока: ' + off1);
    assert(off1.indexOf('translate(0px,50px)') >= 0, 'база блока не в координатах кадра: ' + off1);

    IPV.plan.intro_cam = true;
    CAM.s = 2.0; CAM.shift = [300, -200];
    ipvIntroPos(0, 2.0);
    const on1 = tf();
    CAM.s = 3.5; CAM.shift = [700, -500];
    ipvIntroPos(0, 2.0);
    const on2 = tf();
    assert(on1 !== on2, 'с включённой галкой блок обязан ехать за камерой');
    assert(on1.indexOf('scale(1.936)') >= 0, 'зум камеры не умножен в масштаб: ' + on1);
    console.log("OK: intro follows the camera flag");
    """
    res = _run_node(tmp_path, "test_zm_intro.js", _preview_script(checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: intro follows the camera flag" in res.stdout


@node
def test_node_preview_shade_ignores_camera_when_detached(tmp_path):
    """4б. Затемнение идёт той же функцией-выбором: при intro_cam=false масштаб без зума.

    Числа при k=0.5, s=1: центр (−22.8, 1126.4) кадра -> (−11.4, 563.2) превью,
    масштаб слоя 0.94 (без зума камеры). При включённой галке — 0.94×зум.
    """
    checks = r"""
    const tf = () => $('ipvshade').style.transform;
    IPV.plan.intro_cam = false;
    ipvShade();
    const off1 = tf();
    CAM.s = 4.0; CAM.shift = [900, 900];
    ipvShade();
    const off2 = tf();
    assert.strictEqual(off1, off2,
      'при intro_cam=false затемнение поехало за камерой: ' + off1 + ' -> ' + off2);
    assert(off1.indexOf('scale(0.94)') >= 0, 'зум камеры остался в масштабе плашки: ' + off1);
    // центр в координатах кадра: (−22.8, 1126.4)·k, k = 0.5 — сверяем числом, не строкой
    const m = off1.match(/translate\(([-\d.]+)px,([-\d.]+)px\)/);
    assert(m, 'не разобрать сдвиг затемнения: ' + off1);
    assert(Math.abs(parseFloat(m[1]) + 22.8 * 0.5) < 1e-9, 'центр затемнения по X: ' + m[1]);
    assert(Math.abs(parseFloat(m[2]) - 1126.4 * 0.5) < 1e-9, 'центр затемнения по Y: ' + m[2]);

    IPV.plan.intro_cam = true;
    CAM.s = 2.0; CAM.shift = [0, 0];
    ipvShade();
    assert(tf().indexOf('scale(1.88)') >= 0, 'с галкой затемнение обязано жить зумом: ' + tf());
    console.log("OK: shade follows the camera flag");
    """
    res = _run_node(tmp_path, "test_zm_shade.js", _preview_script(checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: shade follows the camera flag" in res.stdout


# ============================================================ 5. сторож ручки

def test_intro_cam_knob_changes_the_assembly(xml_subs, tmp_path):
    """5. Ручка схемы влияет на сборку (сторож «каждая ручка», test_r11_li_every_knob).

    Поле обязано быть в схеме (bool) и в BASE (дефолт True — «как сегодня»), а снятая
    галка — менять .jsx: иначе ручка мертва и молчит.
    """
    field = watcher.schema_field("intro_cam")
    assert field, "в схеме нет галки intro_cam"
    assert field.get("ctl") == "bool", "intro_cam перестала быть галкой"
    assert styles.BASE.get("intro_cam") is True, "дефолт intro_cam в styles.BASE не True"
    assert styles.resolve(None).get("intro_cam") is True

    assert _scene(xml_subs, {})["intro_cam"] is True
    assert _scene(xml_subs, {"intro_cam": False})["intro_cam"] is False

    on = _build(xml_subs, tmp_path, style={}, name="knob_on.jsx")[0]
    off = _build(xml_subs, tmp_path, style={"intro_cam": False}, name="knob_off.jsx")[0]
    assert on != off, "снятая галка «интро едет с камерой» не изменила собранный .jsx"
    assert "if(cam1null){ introNull.parent=cam1null;" in on
    assert "if(cam1null){ introNull.parent=cam1null;" not in off
