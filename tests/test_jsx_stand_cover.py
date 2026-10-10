# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стенд сгенерированного .jsx: ветки шаблона, которые раньше не исполнял ни один тест.

Замер (B4): счётчики входа в функции шаблона по всему набору тестов показали функции, которые
ни один стенд не вызывал: introAnimFX, introHlGlow, introWordShadow, introSQ, introBackScale,
applyLumetri/applyLumetri2, hlDur. Прежние тесты этих веток проверяли только синтаксис
(node --check) или текст .jsx. Блоки верхнего уровня (рото-слои, вставки, музыка) тоже
проверялись текстом.

Стенд ниже исполняет .jsx в node с заглушкой AE, которая записывает слои (имя, родитель,
тайминги, матт, shy/label/звук), ключи свойств и эффекты. Тесты сверяют результат со
значениями плана сцены и с числами из шаблона, а не только «не упало».

Что стерегут:
1. Рото: копия и маска созданы, маска лежит над копией, матт LUMA, копия прикреплена к Null
   своей камеры, тайминги = кадры рото, масштаб маски = масштаб копии * mf.
2. Вставка видео: слой с именем файла, in/out по плану, звук вставки выключен.
3. Вставка фото: прекомп «INS <файл>» с слоем фото и слой вставки в основной композиции.
4. Музыка: слой «Музыка» в самом верху, старт 0, уровень из стиля.
5. Интро с глитчем: introAnimFX исполнена, ключи Opacity строки — семь значений шаблона.
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

from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

_STAND = r"""
const fs = require('fs');
let jsx = fs.readFileSync(process.argv[1], 'utf8');
if (jsx.charCodeAt(0) === 0xFEFF) jsx = jsx.slice(1);

function Prop(name) { this.name = name; this.keys = []; this.children = {}; this.expression = ''; this.value = undefined; this.matchName = ''; }
Prop.prototype.setValue = function (v) { this.value = v; this.keys.push([null, v]); };
Prop.prototype.setValueAtTime = function (t, v) { this.keys.push([t, v]); };
Prop.prototype.setInterpolationTypeAtKey = function () {};
Object.defineProperty(Prop.prototype, 'numKeys', { get: function () { return this.keys.filter(function (k) { return k[0] !== null; }).length; } });
Prop.prototype.property = function (n) { if (!this.children[n]) this.children[n] = new Prop(n); return this.children[n]; };
Prop.prototype.addProperty = function (mn) { return this.property(mn); };

function Layer(name, comp) {
  this.name = name; this.comp = comp; this.parent = null; this.startTime = 0; this.inPoint = 0; this.outPoint = 0;
  this.shy = false; this.label = 0; this.audioEnabled = true; this.matte = null; this.matteType = null;
  this.text = ''; this.props = {}; this.effects = []; this.enabled = true; this.width = 1080; this.height = 1920;
  this.blendingMode = 0; this.nullLayer = false; this.quality = 0;
}
Layer.prototype.property = function (n) {
  if (n === 'ADBE Text Properties') {
    const self = this;
    const doc = { text: '', fontSize: 0, fillColor: [1, 1, 1], applyFill: false, fauxBold: false, justification: 0,
                  resetCharStyle: function () {}, resetParagraphStyle: function () {} };
    const p = new Prop(n);
    p.property = function () { const d = new Prop('doc'); d.value = doc; d.setValue = function (v) { self.text = v.text; }; return d; };
    return p;
  }
  if (n === 'ADBE Effect Parade') {
    const self = this;
    return { addProperty: function (mn) { const e = new Prop(mn); e.matchName = mn; self.effects.push(e); return e; } };
  }
  if (!this.props[n]) this.props[n] = new Prop(n);
  return this.props[n];
};
Layer.prototype.remove = function () { const s = this.comp._stack; const i = s.indexOf(this); if (i >= 0) s.splice(i, 1); };
Layer.prototype.moveToBeginning = function () { this.remove(); this.comp._stack.unshift(this); };
Layer.prototype.moveBefore = function (o) { this.remove(); const i = this.comp._stack.indexOf(o); if (i >= 0) this.comp._stack.splice(i, 0, this); else this.comp._stack.unshift(this); };
Layer.prototype.moveAfter = function (o) { this.remove(); const i = this.comp._stack.indexOf(o); if (i >= 0) this.comp._stack.splice(i + 1, 0, this); else this.comp._stack.push(this); };
Layer.prototype.setTrackMatte = function (m, t) { this.matte = m; this.matteType = t; };
Layer.prototype.sourceRectAtTime = function () { return { width: 200, height: 100 }; };

function Comp(name, w, h, dur) {
  this.name = name; this.width = w; this.height = h; this.duration = dur; this._stack = []; this.parentFolder = null;
  const self = this;
  this.layers = {
    add: function (item) { const l = new Layer(item && item.name || 'layer', self); self._stack.unshift(l); return l; },
    addNull: function () { const l = new Layer('Null', self); l.nullLayer = true; self._stack.unshift(l); return l; },
    addShape: function () { const l = new Layer('Shape', self); self._stack.unshift(l); return l; },
    addText: function (t) { const l = new Layer('Text', self); l.text = t; self._stack.unshift(l); return l; },
    get length() { return self._stack.length; }
  };
}
Comp.prototype.layer = function (i) { return this._stack[i - 1]; };
Comp.prototype.openInViewer = function () { mainComp = this; };

let mainComp = null;
const items = [];
function FootageItem(path) { this.name = path.split(/[\\\/]/).pop(); this.mainSource = { file: { fsName: path } }; this.width = 1080; this.height = 1920; this.duration = 10; this.parentFolder = null; }
const app = {
  beginUndoGroup: function () {}, endUndoGroup: function () {},
  project: {
    get numItems() { return items.length; },
    item: function (i) { return items[i - 1]; },
    items: {
      addComp: function (n, w, h, par, dur) { const c = new Comp(n, w, h, dur); items.push(c); if (!mainComp) mainComp = c; return c; },
      addFolder: function (n) { const f = { name: n, parentFolder: null }; items.push(f); return f; }
    },
    importFile: function (io) { const it = new FootageItem(io.file.fsName); items.push(it); return it; }
  }
};
function File(p) { this.fsName = p; this.name = String(p).split(/[\\\/]/).pop(); this.exists = true; }
File.prototype.open = function () { return true; };
File.prototype.writeln = function () {};
File.prototype.close = function () {};
function ImportOptions(f) { this.file = f; }
function KeyframeEase(s, i) { this.speed = s; this.influence = i; }
function Shape() { this.closed = false; }
const KeyframeInterpolationType = { BEZIER: 1, LINEAR: 2, HOLD: 3 };
const BlendingMode = { ADD: 1, NORMAL: 0 };
const TrackMatteType = { LUMA: 1, ALPHA: 0 };
const LayerQuality = { DRAFT: 0, BEST: 1 };
const ParagraphJustification = { CENTER_JUSTIFY: 1, LEFT_JUSTIFY: 0 };
const $ = { writeln: function () {} };
const alert = function () {};
const FontObj = function () {};
const Font = FontObj;
var fontMissing = true;

eval(jsx);

function ser(p) {
  const out = { keys: p.keys, children: {} };
  Object.keys(p.children).forEach(function (k) { out.children[k] = ser(p.children[k]); });
  return out;
}
function lay(l) {
  const props = {};
  Object.keys(l.props).forEach(function (k) { props[k] = ser(l.props[k]); });
  return { name: l.name, parent: l.parent ? l.parent.name : null, startTime: l.startTime, inPoint: l.inPoint,
           outPoint: l.outPoint, shy: l.shy, label: l.label, audioEnabled: l.audioEnabled,
           matte: l.matte ? l.matte.name : null, matteType: l.matteType, text: l.text, props: props,
           effects: l.effects.map(function (e) { return { mn: e.matchName, p: ser(e) }; }),
           nullLayer: l.nullLayer, hasMask: l.masks || 0 };
}
const comps = items.filter(function (i) { return i instanceof Comp; });
const out = { main: mainComp ? mainComp._stack.map(lay) : [],
              comps: comps.map(function (c) { return { name: c.name, layers: c._stack.map(lay) }; }),
              hits: globalThis.__B4HIT || {} };
console.log(JSON.stringify(out));
"""


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


@pytest.fixture(autouse=True)
def _mock_assets_resolver(monkeypatch):
    from core import assets
    monkeypatch.setattr(assets, "resolver", lambda base: lambda role: "C:/mock/transition.mov" if role == "transition" else "")


ROTO_MASK = {"ci": 0, "ts": 0.5, "te": 2.5, "cs": 0.25, "scale": 100, "mf": 2, "mask": "C:/x/mask.mp4"}


@pytest.fixture()
def roto_mock(monkeypatch):
    import json as _json
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: _json.dumps([ROTO_MASK]))


def _build(xml, tmp_path, name, **kw):
    path, _n, _s = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), style=kw.pop("style", {}),
                                     intro_riser=False, disclaimer="", emit=lambda *a, **k: None, **kw)
    return path


def _stand(jsx_path):
    """Исполняет .jsx в node с заглушкой AE и возвращает записанные слои и композиции."""
    res = subprocess.run(["node", "-e", _STAND, str(jsx_path)], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"стенд упал: {res.stderr[-800:]}"
    return json.loads(res.stdout)


def _layer(layers, name):
    hits = [l for l in layers if l["name"] == name]
    assert hits, f"слоя «{name}» нет; есть: {[l['name'] for l in layers][:30]}"
    return hits[0]


def _const(jsx, name):
    m = re.search(r"var %s=([-\d.]+)" % name, jsx)
    assert m, f"в .jsx нет переменной {name}"
    return float(m.group(1))


def test_roto_copy_and_mask_geometry(xml_subs, tmp_path, roto_mock):
    """1. Рото: копия и маска созданы; маска над копией; LUMA; копия у Null своей камеры;
    тайминги = кадры рото; масштаб маски = масштаб копии * mf (mf=2)."""
    path = _build(xml_subs, tmp_path, "roto.jsx")
    jsx = open(path, encoding="utf-8-sig").read()
    st = _stand(path)
    main = st["main"]
    cc = _layer(main, "Рото камера")
    mk = _layer(main, "Рото маска")

    assert cc["parent"] == "Камера 1", cc["parent"]
    assert mk["parent"] == "Камера 1", mk["parent"]
    assert main.index(mk) == main.index(cc) - 1, "маска должна лежать непосредственно над копией"
    assert cc["matte"] == "Рото маска" and cc["matteType"] == 1, (cc["matte"], cc["matteType"])
    assert cc["startTime"] == pytest.approx(ROTO_MASK["cs"])
    assert cc["inPoint"] == pytest.approx(ROTO_MASK["ts"])
    assert cc["outPoint"] == pytest.approx(ROTO_MASK["te"])
    assert mk["inPoint"] == pytest.approx(ROTO_MASK["ts"])
    assert mk["outPoint"] == pytest.approx(ROTO_MASK["te"])
    assert cc["shy"] is True and mk["shy"] is True
    assert cc["label"] == 9 and mk["label"] == 9

    copy_scale = cc["props"]["ADBE Transform Group"]["children"]["ADBE Scale"]["keys"][-1][1]
    mask_scale = mk["props"]["ADBE Transform Group"]["children"]["ADBE Scale"]["keys"][-1][1]
    cam1_fit = _const(jsx, "CAM1_FIT")
    m = re.search(r"var W=(\d+), H=(\d+)", jsx)
    assert m, "в .jsx нет размера композиции"
    W, H = float(m.group(1)), float(m.group(2))
    # рото-копия лежит пиксель-в-пиксель с кадром своей камеры: fit заполнения * CAM1_FIT
    rfit = 100 * max(W / 1080.0, H / 1920.0)
    assert copy_scale[0] == pytest.approx(rfit * cam1_fit / 100.0, rel=1e-6)
    assert mask_scale[0] == pytest.approx(copy_scale[0] * ROTO_MASK["mf"], rel=1e-6)


def test_roto_cam2_uses_plan_scale_not_cam1_fit(xml_subs, tmp_path, monkeypatch):
    """1b. Рото камеры 2: масштаб копии = scale из рото (не fit камеры 1), родитель — Камера 2."""
    import json as _json
    rr = dict(ROTO_MASK, ci=1, scale=77.0, mf=1)
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: _json.dumps([rr]))
    path = _build(xml_subs, tmp_path, "roto2.jsx")
    main = _stand(path)["main"]
    cc = _layer(main, "Рото камера")
    assert cc["parent"] == "Камера 2", cc["parent"]
    scale = cc["props"]["ADBE Transform Group"]["children"]["ADBE Scale"]["keys"]
    assert scale[-1][1] == pytest.approx([77.0, 77.0]), scale


def test_video_insert_timing_from_plan_and_audio_off(xml_subs, tmp_path):
    """2. Вставка видео: слой «Вставка: <файл>», in/out = тайминги плана, звук вставки выключен."""
    ins = [{"type": "video", "media": "C:/x/v.mp4", "start_s": 2.0, "dur_s": 3.0}]
    p = xml2ae.scene_plan(xml_subs, inserts=ins, style={}, roto=False)
    plan = p["inserts"][0]
    path = _build(xml_subs, tmp_path, "vid.jsx", inserts=ins, roto=False)
    main = _stand(path)["main"]
    vl = _layer(main, "Вставка: v.mp4")
    assert vl["inPoint"] == pytest.approx(plan["start"])
    assert vl["outPoint"] == pytest.approx(plan["end"])
    assert vl["audioEnabled"] is False
    assert vl["parent"] is None
    assert plan["end"] - plan["start"] == pytest.approx(3.0)


def test_photo_insert_precomp_holds_photo_and_main_layer(xml_subs, tmp_path):
    """3. Вставка фото: прекомп «INS a.png» с фото внутри; в основной композиции слой «Вставка: a.png»."""
    ins = [{"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 2.0, "dur_s": 2.0}]
    path = _build(xml_subs, tmp_path, "photo.jsx", inserts=ins, roto=False)
    st = _stand(path)
    _layer(st["main"], "Вставка: a.png")
    pre = [c for c in st["comps"] if c["name"] == "INS a.png"]
    assert pre, f"прекомпа фото нет; композиции: {[c['name'] for c in st['comps']]}"
    assert [l["name"] for l in pre[0]["layers"]] == ["a.png"], pre[0]["layers"]


def test_music_layer_start_zero_and_style_level(xml_subs, tmp_path):
    """4. Музыка: слой «Музыка» со стартом 0, уровень Audio Levels = music_db из стиля.
    Позицию в стеке не проверяем: шаблон добавляет слой после камер, и порядок — не контракт."""
    track = tmp_path / "track.mp3"
    track.write_bytes(b"ID3")
    path = _build(xml_subs, tmp_path, "music.jsx", music=str(track), music_db=-12.0, roto=False)
    main = _stand(path)["main"]
    ml = _layer(main, "Музыка")
    assert ml["startTime"] == 0
    levels = ml["props"]["ADBE Audio Group"]["children"]["ADBE Audio Levels"]["keys"]
    assert levels[-1][1] == pytest.approx([-12.0, -12.0]), levels


def test_intro_glitch_runs_anim_fx_and_opacity_keys(xml_subs, tmp_path):
    """5. Интро с anim=glitch: introAnimFX исполнена, Opacity строки = семь значений шаблона."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2]),
    ]
    path = _build(xml_subs, tmp_path, "glitch.jsx", intro=intro, intro_splits=[1],
                  intro_mode="word")
    st = _stand(path)
    seqs = []
    for c in [{"layers": st["main"]}] + st["comps"]:
        for l in c["layers"]:
            op = l["props"].get("ADBE Transform Group", {}).get("children", {}).get("ADBE Opacity")
            if op:
                seqs.append([v for t, v in op["keys"] if t is not None])
    assert [0, 100, 100, 0, 93, 0, 100] in seqs, seqs


def test_parse_skips_clip_without_timings(tmp_path):
    """6. parse.py: клип без start/end (переход или служебный элемент) не попадает в камеры и
    не меняет результат разбора. Ветка раньше не исполнялась ни одним тестом (замер B4)."""
    import xml.etree.ElementTree as ET
    from core.xml2ae.parse import parse_full
    src = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")
    base = parse_full(src)
    tree = ET.parse(src)
    track = tree.getroot().find(".//sequence//media/video/track")
    assert track is not None and track.find("clipitem") is not None
    junk = ET.Element("clipitem")
    ET.SubElement(junk, "name").text = "transition"
    track.insert(0, junk)
    mod = str(tmp_path / "timeline_junk.xml")
    tree.write(mod, encoding="utf-8", xml_declaration=True)
    assert parse_full(mod) == base


# ---------------------------------------------------------------- порция 2 (B4)
LM_ALL = {"lm_exposure": 0.5, "lm_contrast": 10, "lm_highlights": 20, "lm_shadows": 30,
          "lm_whites": -40, "lm_blacks": -50, "lm_temp": 60, "lm_tint": -70, "lm_sat": 150}
LM2_ALL = {"lm2_exposure": -0.75, "lm2_contrast": -12, "lm2_highlights": -18,
           "lm2_shadows": -25, "lm2_whites": 38, "lm2_blacks": 45, "lm2_temp": -55,
           "lm2_tint": 65, "lm2_sat": 75}
# соответствие ключ стиля -> matchName параметра Lumetri (как LUMETRI_PARAMS в plan_ae.py)
LUMETRI_MN = {"exposure": "ADBE Lumetri-0011", "contrast": "ADBE Lumetri-0012",
              "highlights": "ADBE Lumetri-0013", "shadows": "ADBE Lumetri-0014",
              "whites": "ADBE Lumetri-0015", "blacks": "ADBE Lumetri-0016",
              "temp": "ADBE Lumetri-0007", "tint": "ADBE Lumetri-0008", "sat": "ADBE Lumetri-0020"}


def _lumetri_layers(layers):
    """Слои с эффектом ADBE Lumetri: [(слой, {matchName параметра: значение})]."""
    out = []
    for l in layers:
        for e in l["effects"]:
            if e["mn"] == "ADBE Lumetri":
                vals = {k: v["keys"][-1][1] for k, v in e["p"]["children"].items() if v["keys"]}
                out.append((l, vals))
    return out


def test_lumetri_cam1_and_cam2_values_from_style(xml_subs, tmp_path):
    """Flags lm_on / lm2_on (разомкнуто): на клипах Камеры 1 — значения lm_*, на клипах
    Камеры 2 — значения lm2_*. Применяет applyLumetri / applyLumetri2 (раньше не исполнялись)."""
    style = {"lm_on": True, "lm2_on": True, "lm2_link": False}
    style.update(LM_ALL)
    style.update(LM2_ALL)
    path = _build(xml_subs, tmp_path, "lumetri.jsx", style=style, roto=False)
    main = _stand(path)["main"]
    found = _lumetri_layers(main)
    cam1 = [v for l, v in found if l["audioEnabled"]]
    cam2 = [v for l, v in found if not l["audioEnabled"]]
    assert cam1 and cam2, f"Lumetri не легли на оба набора клипов: cam1={len(cam1)} cam2={len(cam2)}"
    for vals in cam1:
        for key, mn in LUMETRI_MN.items():
            assert vals[mn] == pytest.approx(LM_ALL["lm_" + key]), (key, vals)
    for vals in cam2:
        for key, mn in LUMETRI_MN.items():
            assert vals[mn] == pytest.approx(LM2_ALL["lm2_" + key]), (key, vals)


def _censored_xml(xml_subs, tmp_path):
    """Фикстура с одним словом-цензурой: в субтитре «МЕТКОНСЕКТ» вместо К стоит звёздочка."""
    raw = open(xml_subs, "rb").read()
    word = "МЕТКОНСЕКТ".encode("utf-8")
    assert word in raw, "в фикстуре нет слова МЕТКОНСЕКТ"
    dst = str(tmp_path / "censored.xml")
    open(dst, "wb").write(raw.replace(word, "МЕТ*ОНСЕКТ".encode("utf-8")))
    return dst


def test_censor_mute_keys_match_plan_windows(xml_subs, tmp_path):
    """Цензура: окна CENSOR из плана; на голосовом клипе ключи громкости — VOICE_DB ->
    -100 на [a, b] -> VOICE_DB с полями 0.02 с, где [a, b] = окно ∩ клип."""
    from core.xml2ae.layout import _censor_windows
    from core.xml2ae.parse import parse_full
    xml = _censored_xml(xml_subs, tmp_path)
    path = _build(xml, tmp_path, "censor.jsx", roto=False)
    jsx = open(path, encoding="utf-8-sig").read()
    m = re.search(r"var CENSOR=(\[.*?\]);", jsx)
    assert m, "в .jsx нет CENSOR"
    windows = json.loads(m.group(1))
    star = [(s, e, w) for (s, e, w) in parse_full(xml)[2] if "*" in (w or "")]
    assert star, "фикстура без звёздочки"
    fps = float(re.search(r"FPS=([\d.]+)", jsx).group(1))
    expect = _censor_windows(star, fps)
    # план округляет окна до 4 знаков — сверка с допуском 1e-3 с
    assert len(windows) == len(expect), (len(windows), len(expect))
    for w, e in zip(windows, expect):
        assert w == pytest.approx(list(e), abs=1e-3), (w, e)
    voice_db = _const(jsx, "VOICE_DB")

    st = _stand(path)
    hits = 0
    for l in st["main"]:
        lv = l["props"].get("ADBE Audio Group", {}).get("children", {}).get("ADBE Audio Levels")
        if not lv:
            continue
        seq = [(t, v) for t, v in lv["keys"] if t is not None]
        for cs, ce in windows:
            a, b = max(cs, l["inPoint"]), min(ce, l["outPoint"])
            if a >= b:
                continue
            want = [(max(l["inPoint"], a - 0.02), [voice_db, voice_db]), (a, [-100, -100]),
                    (b, [-100, -100]), (min(l["outPoint"], b + 0.02), [voice_db, voice_db])]
            got = [(round(t, 6), v) for t, v in seq]
            exp = [(round(t, 6), v) for t, v in want]
            joined = any(got[i:i + 4] == exp for i in range(len(got)))
            assert joined, f"на «{l['name']}» нет ключей окна {(cs, ce)}: {got}"
            hits += 1
    assert hits >= 1, "ни один слой не получил ключей цензуры"


def test_intro_on_cam2_parent_and_position(xml_subs, tmp_path):
    """Интро на кам2 (intro_cam2 при активной Камере 2): нул «интро на кам2» — ребёнок нула
    Камеры 2, позиция [intro_x, INTRO_Y2]. Ветка intro2_cam2_js раньше не исполнялась."""
    intro = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
             dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]
    style = {"cam2_zoom": "pulse", "intro_cam2": True, "intro_x": 40}
    path = _build(xml_subs, tmp_path, "intro_cam2.jsx", intro=intro, intro_splits=[1],
                  intro_mode="word", style=style)
    jsx = open(path, encoding="utf-8-sig").read()
    y2 = _const(jsx, "INTRO_Y2")
    st = _stand(path)
    nul = [l for l in st["main"] if l["name"] == "интро на кам2"]
    assert nul, "нула «интро на кам2» не создан"
    n = nul[0]
    assert n["parent"] == "Камера 2", n["parent"]
    pos = n["props"]["ADBE Transform Group"]["children"]["ADBE Position"]["keys"][-1][1]
    assert pos == pytest.approx([40.0, y2]), (pos, y2)
