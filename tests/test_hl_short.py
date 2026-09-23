# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание MA: короткое жёлтое слово успевает доиграть появление.

Владелец (2026-09-19): «На некоторых маленьких жёлтых словах в субтитрах не успевает
доиграться анимация — они просто исчезают». Подъём HL_RISE, проявление и блюр играли
общие HL_DUR = 0.35 с, а слой гаснет на outPoint = gend: у 49 слов из 140 в ролике
владельца видимое время меньше 0.35 с (минимум 0.05 с).

1. Длительность появления — ОДНА функция в layout: d = min(HL_DUR, HL_FIT · видимое
   время), HL_FIT = 0.6 — слово стоит неподвижно хотя бы 40 % своей жизни.
2. Режим «по слову»: у жёлтого с видимостью 0.2 с в плане hd ≈ 0.12, и он играет ключи
   за него — последний ключ позиции/прозрачности/блюра не позже outPoint; длинное жёлтое
   в том же ролике остаётся на t0 + 0.35.
3. Стопка подряд жёлтых (hl_row_stack) в режиме строк идёт тем же циклом, что режим «по
   слову»: у короткого последнего слова стопки ключи тоже ужимаются. Цикл СТРОК не
   тронут — там момент появления зажат так, что анимация успевает (ключи на w_t0 + 0.35).
4. Ни одного короткого жёлтого — .jsx побайтово как на main: ни функции hlDur, ни поля
   длительности, цикл субтитров без слова hlDur вовсе (укорочение включает само короткое
   слово в данных, своего «выключено» у фичи нет), а сборка с дефолтами — с
   эталоном fixtures/golden_geometry.jsx (эталон только читается).
5. Превью: длительность появления берётся из плана (data-hld у слова), своего числа в JS
   нет — в середине короткой анимации прозрачность промежуточная, на t0+hd слово стоит
   полностью проявленным, а длинное в тот же момент ещё едет на общей hl_dur плана.

Собранные циклы субтитров ИСПОЛНЯЮТСЯ в node с заглушками AE (приём tests/test_hl_anim),
а боевая ipvSubs — с заглушками DOM (приём tests/test_rows_yellow): проверяются реальные
ключи, inPoint/outPoint и прозрачность, а не текст .jsx.

Слова фикстуры (tests/fixtures/timeline_subs.xml.gz, 60 fps): 11 «МЕТКОНСЕКТ» — длинное
жёлтое (233..290 кадров, 0.95 с), 95 «КТ» — короткое (2539..2551, 0.2 с), 7 «Д» и
8 «ОДЛО» — серия из двух жёлтых подряд (уезжает в стопку при hl_row_stack).
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

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import HL_DUR, HL_FIT, hl_appear_dur  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402
from tests.test_rows_yellow import _ipv_subs_code, _preview_plan  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

SERIES = [7, 8]        # серия подряд жёлтых: последнее слово серии короткое
SHORT = 95             # «КТ»: 0.2 с — анимация 0.35 с в неё не влезает
LONG = 11              # «МЕТКОНСЕКТ»: 0.95 с — длительность остаётся общей
# Ролик без коротких жёлтых: отмечено только длинное слово — укорочать нечего, и .jsx
# обязан быть прежним. Это и есть «выключенное» состояние фичи: своего флага у неё нет,
# укорочение включает само короткое слово в разметке.
NO_SHORT = [LONG]
SHORT_W, LONG_W = "КТ", "МЕТКОНСЕКТ"
BLUR = {"hl_blur": True, "hl_blur_amt": 70.4}
GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
ROWS = {"sub_words_per_row": 2, "hl_row_stack": True}


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=None, hl_joins=None):
    return xml2ae.scene_plan(xml, highlights=list(highlights or []), style=dict(style or {}),
                             hl_joins=hl_joins, emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=None, hl_joins=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   highlights=list(highlights or []), hl_joins=hl_joins,
                                   style=dict(style or {}),
                                   disclaimer="", emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _json_var(jsx, name):
    """Данные цикла из .jsx: var <name> = [...];"""
    m = re.search(r"var %s\s*=\s*(\[.*?\]);" % name, jsx)
    assert m, "в .jsx нет var %s" % name
    return json.loads(m.group(1))


def _yellow_rows(jsx, name="SUBS"):
    return [r for r in _json_var(jsx, name) if len(r) > 3 and r[3] == 1]


def _yellow_items(plan):
    return [it for it in plan["subs"] if it.get("color") == "yellow"]


# --------------------------------------------------------------------------- стенд .jsx
_STAND = r"""
let _logs = [];
function _LOG(m){ _logs.push(String(m)); }
var ParagraphJustification = { CENTER_JUSTIFY: 1 };
function easePair(p){ p.eased = true; }

let layers = [];

function makeProp(lay, name){
  const p = {
    lay: lay, name: name, keys: [], eased: false,
    numKeys: 0, isSpatial: false, value: [0, 0],
    setValue: function(v){ p.keys.push([null, v]); },
    setValueAtTime: function(t, v){ p.keys.push([t, v]); },
    setInterpolationTypeAtKey: function(){},
    setTemporalEaseAtKey: function(){}
  };
  return p;
}

function makeLayer(txt){
  const lay = { text: txt, name: txt, inPoint: null, outPoint: null, props: {}, fx: {} };
  lay.getProp = function(name){
    if (!lay.props[name]) lay.props[name] = makeProp(lay, name);
    return lay.props[name];
  };
  lay.property = function(prop){
    if (prop === 'ADBE Text Properties'){
      return { property: function(){
        return { value: { resetCharStyle: function(){}, resetParagraphStyle: function(){},
                          text: '', fontSize: 0, fillColor: [1, 1, 1], applyFill: false,
                          fauxBold: false, font: '', justification: 0 },
                 setValue: function(){} };
      } };
    }
    if (prop === 'ADBE Transform Group'){
      return { property: function(p){
        if (p === 'ADBE Position') return lay.getProp('position');
        if (p === 'ADBE Opacity') return lay.getProp('opacity');
        return lay.getProp(p);
      } };
    }
    if (prop === 'ADBE Effect Parade'){
      return { addProperty: function(mn){
        const fx = { name: mn, params: {} };
        fx.property = function(pn){
          if (!fx.params[pn]) fx.params[pn] = makeProp(lay, pn);
          return fx.params[pn];
        };
        if (!lay.fx[mn]) lay.fx[mn] = [];
        lay.fx[mn].push(fx);
        return fx;
      } };
    }
    return { property: function(){ return lay.getProp(prop); } };
  };
  lay.sourceRectAtTime = function(){ return { width: 150, height: 50 }; };
  layers.push(lay);
  return lay;
}

const subc = { layers: { addText: makeLayer } };

__CODE__

function dumpProp(p){ return { keys: p.keys, eased: !!p.eased }; }
function dumpFx(fx){
  const out = {};
  Object.keys(fx).forEach(function(mn){
    out[mn] = fx[mn].map(function(f){
      const params = {};
      Object.keys(f.params).forEach(function(pn){ params[pn] = dumpProp(f.params[pn]); });
      return { name: f.name, params: params };
    });
  });
  return out;
}
console.log(JSON.stringify({
  layers: layers.map(function(l){
    return { text: l.text, inPoint: l.inPoint, outPoint: l.outPoint,
             position: dumpProp(l.getProp('position')),
             opacity: dumpProp(l.getProp('opacity')), fx: dumpFx(l.fx) };
  }),
  logs: _logs
}));
"""

# Строки собранного .jsx, нужные циклам субтитров: константы стиля и данные.
_LINE_PREFIXES = (
    "    var W=", "    var SUB_WIDE=", "    var SW=", "    var FITW",
    "    var FONT =", "    var HL_FONT =", "    var HL_BOLD =", "    var FONT_SIZE =",
    "    var HL_FILL =", "    var HL_RISE =", "    var HL_STEP =",
    "    var HL_ROW_WORD =", "    var HL_BLUR =", "    var SUBS=",
)
_LOOP_MARKERS = ("    var SUB_ROWS = ",
                 "    for (var i=0;i<SUBS.length;i++){",
                 "    var i=0;\n    while (i<SUBS.length){")
# Функции появления: блюр (многострочный) и длительности короткого жёлтого
# (слова/стопка, — слово строки): уезжают в .jsx только когда
# реально нужны, стенду нужны все объявленные.
_FN_PATTERNS = (
    r"\n    function hlBlur.*?\n    \}\n",
    r"\n    function hlDur\(sw\)\{[^\n]*\}",
    r"\n    function hlRowDur\(wd\)\{[^\n]*\}",
)


def _loop_code(jsx):
    """Циклы субтитров из собранного .jsx (от первого цикла до поп-SFX)."""
    starts = [jsx.index(m) for m in _LOOP_MARKERS if m in jsx]
    assert starts, "в .jsx не найден ни один цикл субтитров"
    i = min(starts)
    return jsx[i:jsx.index("    // ---- поп-SFX", i)]


def _stand_code(jsx):
    """Выдержка из .jsx для стенда: константы стиля, функции появления и циклы субтитров."""
    constants = "\n".join(ln for ln in jsx.splitlines()
                          if any(ln.startswith(p) for p in _LINE_PREFIXES))
    fns = [m.group(0) for p in _FN_PATTERNS for m in [re.search(p, jsx, re.S)] if m]
    return "\n".join(x for x in (constants, "".join(fns), _loop_code(jsx)) if x)


def _run(jsx, tmp_path, name):
    """Исполнить собранные циклы в node с заглушками AE: слои с ключами и эффектами."""
    script = _STAND.replace("__CODE__", _stand_code(jsx))
    js_file = tmp_path / ("stand_%s.js" % name)
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % (res.stderr or "")[-2000:]
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert not data["logs"], "стенд записал ошибки в _LOG: %s" % data["logs"]
    return data


def _layer(run, text):
    hits = [l for l in run["layers"] if l["text"] == text]
    assert len(hits) == 1, "слоёв с текстом %r: %d" % (text, len(hits))
    return hits[0]


def _layer_at(run, t0):
    """Слой по моменту появления: тексты в фикстуре повторяются («КТ» есть и в 16-й
    секунде), а время начала у каждого слова своё."""
    hits = [l for l in run["layers"]
            if l["inPoint"] is not None and abs(l["inPoint"] - t0) < 1e-6]
    assert len(hits) == 1, "слоёв с началом %r: %d" % (t0, len(hits))
    return hits[0]


def _keys(lay, prop):
    return [k[0] for k in lay[prop]["keys"]]


def _blur_keys(lay, param="ADBE Gaussian Blur 2-0001"):
    fx = lay["fx"]["ADBE Gaussian Blur 2"][0]
    return [k[0] for k in fx["params"][param]["keys"]]


# ------------------------------------------------------------ 1. функция длительности
def test_appear_duration_formula():
    """d = min(HL_DUR, HL_FIT·vis): общая длительность, пока она влезает в видимое время."""
    assert HL_DUR == 0.35 and HL_FIT == 0.6
    assert hl_appear_dur(1.0) == 0.35, "длинному слову укоротили появление"
    assert hl_appear_dur(0.3) == 0.18, "доля видимого времени не 0.6"
    assert hl_appear_dur(0.05) == 0.03, "самое короткое слово не ужалось"
    # Ровно на границе 0.5833 с анимация ещё влезает целиком (0.6·vis >= HL_DUR).
    assert hl_appear_dur(HL_DUR / HL_FIT) == 0.35
    assert hl_appear_dur(0.0) == 0.0, "нулевое слово не должно получать длительность"


# ------------------------------------------------------- 2. режим «по слову»: короткое
@node
@pytest.mark.parametrize("joined", [False, True])
@node
def test_word_mode_short_yellow_plays_its_own_time(xml_subs, tmp_path, joined):
    """Короткое жёлтое (0.2 с) играет появление за 0.12 с и успевает до outPoint;
    длинное в том же ролике остаётся на общей 0.35 с. joined=True — тот же случай в цикле
    со склейками (SUBS_LOOP_WORDS_JOINED: второй проход идёт по r_words, а не по SUBS)."""
    style = dict(BLUR)
    joins = [SHORT] if joined else None
    plan = _plan(xml_subs, style=style, highlights=[SHORT, LONG], hl_joins=joins)
    items = {it["w"]: it for it in _yellow_items(plan)}
    assert plan["hl_dur"] == HL_DUR, "план потерял общую длительность появления"

    short_it = items[SHORT_W]
    assert short_it["hd"] == pytest.approx(0.12), "короткому слову не посчитали длительность"
    assert short_it["hd"] == hl_appear_dur(short_it["gend"] - short_it["s"])
    assert "hd" not in items[LONG_W], "длинному жёлтому проставили своё появление"

    jsx = _jsx(xml_subs, tmp_path, "word.jsx", style=style, highlights=[SHORT, LONG],
               hl_joins=joins)
    assert "function hlDur(sw){ return sw[7]; }" in jsx, "нет функции длительности"
    assert ("var r_layers" in jsx) == joined, "собрался не тот цикл субтитров"
    yellows = {r[2]: r for r in _yellow_rows(jsx)}
    assert yellows[SHORT_W][7] == pytest.approx(0.12), "в данных цикла нет длительности"
    assert yellows[LONG_W][7] == pytest.approx(HL_DUR), "длинному уехала чужая длительность"
    assert len(yellows[SHORT_W]) == 8, "поле длительности не после счётчика (индекс 7)"

    run = _run(jsx, tmp_path, "word")
    meta, _cams, subs, _xi = xml2ae.parse_full(xml_subs)
    fps = float(meta["fps"])
    short = _layer_at(run, subs[SHORT][0] / fps)
    t0, out = short["inPoint"], short["outPoint"]
    assert out == pytest.approx(short_it["gend"]), "конец слоя не по gend"
    assert t0 + HL_DUR > out, "фикстура не воспроизводит обрыв: анимация и так влезала"
    for prop in ("position", "opacity"):
        assert _keys(short, prop) == pytest.approx([t0, t0 + 0.12]), \
            "ключи %s короткого слова не за его длительность" % prop
        assert _keys(short, prop)[-1] <= out + 1e-9, "ключ %s остался за outPoint" % prop
    assert _blur_keys(short) == pytest.approx([t0, t0 + 0.12]), "блюр не за ту же длительность"
    assert _blur_keys(short)[-1] <= out + 1e-9, "ключ блюра остался за outPoint"

    long_lay = _layer_at(run, subs[LONG][0] / fps)
    lt0 = long_lay["inPoint"]
    assert _keys(long_lay, "opacity") == pytest.approx([lt0, lt0 + HL_DUR]), \
        "длинное жёлтое поехало с общей длительности"


# ------------------------------------------- 3. стопка подряд жёлтых в режиме строк
@pytest.mark.parametrize("joined", [False, True])
@node
def test_rows_stack_short_last_word_plays_its_own_time(xml_subs, tmp_path, joined):
    """Стопка идёт циклом режима «по слову»: у коротких слов стопки ключи свои (последнее
    слово серии — короткое), а жёлтое в строке (цикл строк) остаётся на общей длительности.
    joined=True — та же стопка в цикле со склейками (SUBS_LOOP_STACK_JOINED)."""
    style = dict(ROWS, **BLUR)
    joins = [SERIES[0]] if joined else None
    plan = _plan(xml_subs, style=style, highlights=SERIES + [LONG], hl_joins=joins)
    stack = [it for it in plan["subs"] if it.get("stack")]
    assert [it["w"] for it in stack] == ["Д", "ОДЛО"], "серия уехала в стопку не целиком"
    assert [it["hd"] for it in stack] == pytest.approx([0.16, 0.15]), \
        "каждому слову стопки не посчитали свою длительность"

    jsx = _jsx(xml_subs, tmp_path, "rows.jsx", style=style, highlights=SERIES + [LONG],
               hl_joins=joins)
    data = _json_var(jsx, "SUB_STACK")
    assert [r[7] for r in data] == pytest.approx([0.16, 0.15]), "в данных стопки нет длительностей"
    assert all(r[6] is None for r in data), "поле счётчика в стопке не пустое"
    assert "r_t1 - HL_DUR" in jsx, "цикл строк потерял своё окно появления"

    run = _run(jsx, tmp_path, "rows")
    # Слои стопки цикл создаёт ПОСЛЕ строк — они и есть последние слои сборки.
    stack_layers = run["layers"][-len(data):]
    assert [l["text"] for l in stack_layers] == [r[2] for r in data]
    first, last = stack_layers
    for lay, hd in ((first, 0.16), (last, 0.15)):
        t0, out = lay["inPoint"], lay["outPoint"]
        assert t0 + HL_DUR > out, "фикстура не воспроизводит обрыв в стопке"
        assert _keys(lay, "opacity") == pytest.approx([t0, t0 + hd])
        assert _keys(lay, "opacity")[-1] <= out + 1e-9
        assert _keys(lay, "position") == pytest.approx([t0, t0 + hd])
        assert _blur_keys(lay) == pytest.approx([t0, t0 + hd])

    # Жёлтое в СТРОКЕ цикл строк не трогает: момент там зажат под общую HL_DUR.
    row_layers = run["layers"][:-len(data)]
    in_row = [l for l in row_layers if l["text"] == LONG_W]
    assert len(in_row) == 1, "в строках не нашлось длинное жёлтое"
    wt0 = in_row[0]["inPoint"]
    assert _keys(in_row[0], "opacity") == pytest.approx([wt0, wt0 + HL_DUR]), \
        "трогать цикл строк было нельзя: там анимация и так успевает"


# --------------------------------------- 4. ни одного короткого жёлтого — как на main
def test_no_short_yellow_jsx_is_main(xml_subs, tmp_path):
    """Ролик без коротких жёлтых (укорочать нечего) — .jsx как на main: ни функции, ни
    поля длительности, а цикл субтитров не знает слова hlDur вовсе.

    «Выключенного» состояния у фичи нет: раньше оно изображалось подменой
    константы layout.HL_FIT = 1e6, и тест сторожил собственную подмену, а не данные.
    Теперь выключено = в ролике нет коротких жёлтых (отмечено только длинное слово,
    0.95 с): укорочение включает само слово в разметке, и это видно на той же фикстуре —
    стоит отметить и короткое, в .jsx появляется hlDur и цикл субтитров меняется.
    """
    long_only = _jsx(xml_subs, tmp_path, "long.jsx", style=dict(BLUR), highlights=NO_SHORT)
    for token in ("hlDur", "data-hld"):
        assert token not in long_only, "в .jsx без коротких жёлтых остался %s" % token
    assert "hlDur" not in _loop_code(long_only), "цикл субтитров зовёт чужую длительность"
    assert len(_yellow_rows(long_only)[0]) == 6, "у длинного жёлтого появилось лишнее поле"
    assert "function hlBlur(L, t0){" in long_only and "hlBlur(L, t0);" in long_only, \
        "вызов блюра уехал с прежней сигнатуры"

    # Та же фикстура, но отмечено и короткое жёлтое: укорочение включается ДАННЫМИ —
    # функция длительности, поле у жёлтых и свой момент в цикле субтитров.
    with_short = _jsx(xml_subs, tmp_path, "short.jsx", style=dict(BLUR),
                      highlights=[SHORT, LONG])
    assert "function hlDur(sw){ return sw[7]; }" in with_short, "нет функции длительности"
    assert len(_yellow_rows(with_short)) == 2 and \
        all(len(r) == 8 for r in _yellow_rows(with_short)), "нет поля длительности"
    assert "hlDur(sw)" in _loop_code(with_short), "укорочение не доехало до цикла субтитров"
    assert _loop_code(with_short) != _loop_code(long_only), "короткое слово не изменило цикл"

    # Полная сборка с дефолтами — эталон main (не перегенерируется, только читается).
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path)) == golden, \
        "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"


# ------------------------------------------------------- 5. превью: длительность из плана
# Эмуляция DOM под ipvSubs: #ipvsub 540x960 (k = 540/1080 = 0.5). Разбор innerHTML общий
# для строк и слов; у слова анимации кладут data-hl0 (момент) и data-hld (длительность).
_DOM_SIM = r"""
const assert = require('assert');

function El(tag){
  this.tagName=String(tag).toUpperCase();
  this._cls=new Set();
  this.style={setProperty(){},removeProperty(){}};
  this.styleStr='';this.dataset={};this.children=[];this.childNodes=[];
  this.parent=null;this._html='';this._text='';
  const self=this;
  this.classList={
    add:c=>self._cls.add(c),remove:c=>self._cls.delete(c),
    toggle:(c,v)=>{const on=(v===undefined)?!self._cls.has(c):!!v;
      if(on)self._cls.add(c);else self._cls.delete(c);return on;},
    contains:c=>self._cls.has(c)};
}
Object.defineProperty(El.prototype,'className',{
  get(){return [...this._cls].join(' ');},
  set(v){this._cls=new Set(String(v||'').split(/\s+/).filter(Boolean));}});
Object.defineProperty(El.prototype,'textContent',{
  get(){return this._text||this.children.map(c=>c.textContent).join('');},
  set(v){this._text=''+(v==null?'':v);this.children=[];}});
Object.defineProperty(El.prototype,'innerHTML',{
  get(){return this._html;},
  set(h){
    this._html=h;this.children=[];
    const rowRe=/<span class="(pvsubw(?: yel)?)" style="([^"]*)">([\s\S]*?)(?=<span class="pvsubw(?: yel)?"|$)/g;
    let m;
    while((m=rowRe.exec(h))!==null){
      const row=new El('span');row.className=m[1];row.styleStr=m[2];row._html=m[3];
      const wdRe=/<span class="(pvsubw_wd[^"]*)"([^>]*) style="([^"]*)">([\s\S]*?)<\/span>/g;
      let w;
      while((w=wdRe.exec(m[3]))!==null){
        const el=new El('span');el.className=w[1];
        const a0=/data-hl0="([^"]*)"/.exec(w[2]);if(a0)el.dataset.hl0=a0[1];
        const a1=/data-hld="([^"]*)"/.exec(w[2]);if(a1)el.dataset.hld=a1[1];
        el.styleStr=w[3];el.textContent=w[4];
        row.appendChild(el);
      }
      this.appendChild(row);
    }
  }});
El.prototype.appendChild=function(ch){this.children.push(ch);ch.parent=this;return ch;};
El.prototype.remove=function(){if(this.parent){const i=this.parent.children.indexOf(this);
  if(i>=0)this.parent.children.splice(i,1);this.parent=null;}};
El.prototype.querySelector=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  return this.children.find(c=>c._cls.has(want))||null;};
El.prototype.querySelectorAll=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  const res=[];
  (function scan(el){for(const ch of el.children){if(ch._cls.has(want))res.push(ch);scan(ch);}})(this);
  return res;};

const subEl=new El('div');subEl.id='ipvsub';subEl.clientWidth=540;subEl.clientHeight=960;
global.$=id=>(id==='ipvsub'?subEl:null);
global.document={createElement:tag=>new El(tag)};
global.t=s=>s;
global.esc=s=>String(s==null?'':s);
global.rgb2hex=()=>'#fff';
global.ipvFontFor=()=>null;
global.FONTS=[];
global.CURSTYLE={};
global.IPVMODE='ae';
global.IPV={plan:@PLAN@};

function host(){return subEl.querySelector('.pvsubs_host');}
function paint(plan,tm){IPV.plan=plan;ipvSubs(tm);return host();}
function words(){return host().querySelectorAll('.pvsubw_wd');}
function word(txt){return words().filter(w=>w.textContent===txt)[0]||null;}
function opacity(txt){const el=word(txt);return el?el.style.opacity:null;}
"""


def _run_preview(tmp_path, name, plan, checks):
    script = (_DOM_SIM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + _ipv_subs_code() + "\nconst plan=IPV.plan;\n" + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, "проверки превью не прошли: %s" % res.stdout


@node
def test_preview_takes_duration_from_plan(xml_subs, tmp_path):
    """Превью анимирует короткое жёлтое за hd из плана, а длинное — за общую hl_dur."""
    plan = _plan(xml_subs, style=dict(BLUR), highlights=[SHORT, LONG])
    items = {it["w"]: it for it in _yellow_items(plan)}
    short_it, long_it = items[SHORT_W], items[LONG_W]
    assert short_it["hd"] < plan["hl_dur"], "в плане нет укороченной длительности"

    checks = r"""
// Слово «КТ» в фикстуре встречается дважды (в 16-й секунде — белое): берём жёлтое.
const shortS=plan.subs.filter(s=>s.w===@SHORT_W@&&s.color==='yellow')[0];
const longS=plan.subs.filter(s=>s.w===@LONG_W@&&s.color==='yellow')[0];
const hd=shortS.hd, t0=shortS.s, hDur=plan.hl_dur;
assert(hd>0&&hd<hDur,'в плане нет укороченной длительности: '+hd);

// Середина короткой анимации — прозрачность промежуточная (строго между 0 и 1), а само
// слово несёт длительность из плана (data-hld), а не общую hl_dur.
paint(plan,t0+hd/2);
assert.strictEqual(word(@SHORT_W@).dataset.hld,String(hd),'превью не взяло длительность из плана');
let op=opacity(@SHORT_W@);
assert(op!==''&&parseFloat(op)>0&&parseFloat(op)<1,'середина короткой анимации: '+op);

// На t0+hd короткая анимация уже доиграла: слово стоит полностью проявленным.
paint(plan,t0+hd);
assert.strictEqual(opacity(@SHORT_W@),'','на t0+hd короткое слово ещё не доиграло');

// Длинное слово в СВОЙ t0+hd ещё едет: его длительность — общая hl_dur плана, поля hd у
// него в плане нет.
const lt0=longS.s;
paint(plan,lt0+hd);
assert.strictEqual(word(@LONG_W@).dataset.hld,undefined,'длинному жёлтому дали своё поле');
op=opacity(@LONG_W@);
assert(op!==''&&parseFloat(op)>0&&parseFloat(op)<1,'длинное слово поехало на чужую длительность: '+op);
paint(plan,lt0+hDur);
assert.strictEqual(opacity(@LONG_W@),'','на t0+hl_dur длинное слово не доиграло');
console.log("OK: preview takes appear duration from the plan");
"""
    checks = (checks.replace("@SHORT_W@", json.dumps(SHORT_W, ensure_ascii=False))
                    .replace("@LONG_W@", json.dumps(LONG_W, ensure_ascii=False)))
    # Момент, с которого превью считает появление, — начало слова в исходнике.
    meta, _cams, subs, _xi = xml2ae.parse_full(xml_subs)
    fps = float(meta["fps"])
    assert short_it["s"] == pytest.approx(subs[SHORT][0] / fps)
    assert long_it["s"] == pytest.approx(subs[LONG][0] / fps)
    _run_preview(tmp_path, "hl_short_preview.js", plan, checks)
