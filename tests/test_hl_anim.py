# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание ZH: жёлтые в режиме строк (анимация появления) и блюр появления жёлтых.

1. Режим строк (`sub_words_per_row=2`): раньше жёлтое слово ложилось статично на
   время строки. Теперь у него своё время появления — `hl_row_anim="word"` берёт
   время слова (`wd[0]`, но не раньше строки и не позже, чем остаётся место на
   подъём), `"row"` — время строки (`r_t0`); подъём и проявление стоят на том же
   времени, что у жёлтых в режиме «по слову».
2. Блюр появления (`hl_blur`): `ADBE Gaussian Blur 2` на жёлтом слое, Repeat Edge
   Pixels (`-0003`) = 0, Blurriness (`-0001`) `HL_BLUR` -> 0 на тех же ключах, что
   подъём; вызов `hlBlur` есть во всех трёх циклах (по слову, склейка, строки).
3. `hl_blur=False` (дефолт) — .jsx побайтово прежний: сверяется с эталоном
   `fixtures/golden_geometry.jsx` (эталон не перегенерируется, только читается).

Собранные циклы субтитров ИСПОЛНЯЮТСЯ в node с заглушками AE (как в
test_template_sub_wide): проверяются реальные inPoint, ключи позиции/прозрачности и
параметры эффекта, а не текст .jsx.
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
from core.xml2ae.template import (SUBS_LOOP_ROWS,  # noqa: E402
                                  SUBS_LOOP_WORDS, SUBS_LOOP_WORDS_JOINED)
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Слова фикстуры: 1 — «СУ», второй в КОРОТКОЙ строке (0..35 кадров: окно подъёма
# 0.35 с в неё не влезает, работает кламп к концу строки); 11 — «МЕТКОНСЕКТ»,
# второй в длинной строке (194..290 кадров: появление ровно в момент слова).
HIGHLIGHTS = [1, 11]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


# --------------------------------------------------------------------------- стенд
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

# Строки собранного .jsx, которые нужны циклу субтитров: константы стиля и данные.
_LINE_PREFIXES = (
    "    var W=", "    var SUB_WIDE=", "    var SW=", "    var FITW",
    "    var FONT =", "    var HL_FONT =", "    var HL_BOLD =", "    var FONT_SIZE =",
    "    var HL_FILL =", "    var HL_RISE =", "    var HL_STEP =",
    "    var HL_ROW_WORD =", "    var HL_BLUR =", "    var SUBS=",
)
_LOOP_MARKERS = ("    var SUB_ROWS = ",
                 "    for (var i=0;i<SUBS.length;i++){",
                 "    var i=0;\n    while (i<SUBS.length){")


def _jsx(xml, tmp_path, name="out.jsx", highlights=None, hl_joins=None, style=None):
    """Собрать .jsx и вернуть его текст."""
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   highlights=highlights or [], hl_joins=hl_joins,
                                   style=dict(style or {}), disclaimer="",
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _rows_jsx(xml, tmp_path, name="rows.jsx", row_anim="word", **extra):
    """Сборка в режиме строк: два слова в строке, жёлтое — вторым."""
    st = {"sub_words_per_row": 2, "hl_row_anim": row_anim}
    st.update(extra)
    return _jsx(xml, tmp_path, name=name, highlights=HIGHLIGHTS, style=st)


def _stand_code(jsx):
    """Выдержка из собранного .jsx: константы, функции появления и цикл субтитров."""
    constants = "\n".join(ln for ln in jsx.splitlines()
                          if any(ln.startswith(p) for p in _LINE_PREFIXES))
    starts = [jsx.index(m) for m in _LOOP_MARKERS if m in jsx]
    assert starts, "в .jsx не найден ни один цикл субтитров"
    i = min(starts)
    loop = jsx[i:jsx.index("    // ---- поп-SFX", i)]
    fns = []
    if "    function hlBlur(L, t0){" in jsx:
        a = jsx.index("    function hlBlur(L, t0){")
        b = jsx.index("\n    }\n", a) + len("\n    }\n")
        fns.append(jsx[a:b])
    # Длительность появления короткого жёлтого (задание MA): цикл зовёт hlDur(sw) — стенду
    # она нужна так же, как hlBlur, иначе node падает на ReferenceError.
    m = re.search(r"\n    function hlDur\(sw\)\{[^\n]*\}", jsx)
    if m:
        fns.append(m.group(0))
    return "\n".join(x for x in (constants, "\n".join(fns), loop) if x)


def _run(jsx, tmp_path, name):
    """Исполнить собранный цикл в node с заглушками AE, вернуть слои и лог."""
    script = _STAND.replace("__CODE__", _stand_code(jsx))
    js_file = tmp_path / ("stand_%s.js" % name)
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % (res.stderr or "")[-2000:]
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert not data["logs"], "стенд записал ошибки в _LOG: %s" % data["logs"]
    return data


def _rows_data(jsx):
    return json.loads(re.search(r"var SUB_ROWS = (\[.*?\]);", jsx).group(1))


def _num(jsx, pattern, what):
    m = re.search(pattern, jsx)
    assert m, "в .jsx не найдено %s" % what
    return float(m.group(1))


def _fps(jsx):
    return _num(jsx, r"var W=\d+, H=\d+, FPS=([\d.]+),", "FPS")


def _hl_dur(jsx):
    return _num(jsx, r"HL_DUR = ([\d.]+);", "HL_DUR")


def _rows_params(jsx):
    """FPS, HL_RISE, HL_DUR, POSY и SUB_STEP — из самого .jsx (не из головы теста)."""
    return (_fps(jsx),
            _num(jsx, r"var HL_RISE = ([\d.]+),", "HL_RISE"), _hl_dur(jsx),
            _num(jsx, r"POSY = (\d+);", "POSY"),
            _num(jsx, r"var SUB_STEP = ([\d.]+);", "SUB_STEP"))


def _pairs(data):
    """[(строка, слово)] в том же порядке, в каком цикл создаёт слои."""
    return [(row, wd) for row in data for wd in row[4]]


def _expect_t0(row, wd, fps, dur, row_word):
    """Контракт ZH: время появления жёлтого = время слова (или строки), зажатое в окно строки."""
    r_t0, r_t1 = row[0] / fps, row[1] / fps
    if not wd[2] or not row_word:
        return r_t0
    return min(max(wd[0] / fps, r_t0), max(r_t0, r_t1 - dur))


# ------------------------------------------------------------------- режим строк
@node
def test_rows_word_anim_yellow_appears_when_spoken(xml_subs, tmp_path):
    """hl_row_anim="word": жёлтое въезжает в момент слова, подъём/проявление — на нём же."""
    jsx = _rows_jsx(xml_subs, tmp_path, row_anim="word")
    assert "var HL_ROW_WORD = true;" in jsx
    data, params = _rows_data(jsx), _rows_params(jsx)
    fps, rise, dur, posy, step = params
    assert all(row[3] == 0 for row in data), "фикстура размечена построчным кеглем — ожидания по lineY неверны"
    run = _run(jsx, tmp_path, "rows_word")

    pairs = _pairs(data)
    assert [l["text"] for l in run["layers"]] == [wd[1] for _, wd in pairs]

    seen_word_time = seen_clamp = 0
    for lay, (row, wd) in zip(run["layers"], pairs):
        lineY = posy + row[2] * step
        r_t0 = row[0] / fps
        if not wd[2]:
            assert lay["inPoint"] == pytest.approx(r_t0), "белое слово уехало со времени строки"
            assert lay["opacity"]["keys"] == [], "белому слову поставили проявление"
            assert len(lay["position"]["keys"]) == 1
            assert lay["position"]["keys"][0][0] is None, "белое слово статично (setValue без ключей)"
            assert lay["position"]["keys"][0][1][1] == pytest.approx(lineY)
            continue

        t0 = _expect_t0(row, wd, fps, dur, row_word=True)
        assert lay["inPoint"] == pytest.approx(t0)
        assert lay["position"]["keys"] == [[t0, [lay["position"]["keys"][0][1][0], lineY + rise]],
                                           [t0 + dur, [lay["position"]["keys"][1][1][0], lineY]]]
        assert lay["position"]["keys"][0][1][0] == pytest.approx(lay["position"]["keys"][1][1][0])
        assert lay["opacity"]["keys"] == [[t0, 0], [t0 + dur, 100]]
        assert lay["position"]["eased"] and lay["opacity"]["eased"], "кривая easePair не применена"
        if wd[0] / fps > r_t0 and t0 == pytest.approx(wd[0] / fps):
            seen_word_time += 1
        if t0 < wd[0] / fps:
            seen_clamp += 1
    assert seen_word_time >= 1, "не проверен случай «появление ровно в момент слова»"
    assert seen_clamp >= 1, "не проверен случай «окно строки короче подъёма — время сдвинуто раньше»"


@node
def test_rows_row_anim_yellow_appears_with_row(xml_subs, tmp_path):
    """hl_row_anim="row": время появления жёлтого = время строки (как было до ZH)."""
    jsx = _rows_jsx(xml_subs, tmp_path, name="rows_row.jsx", row_anim="row")
    assert "var HL_ROW_WORD = false;" in jsx
    data, params = _rows_data(jsx), _rows_params(jsx)
    fps, rise, dur, posy, step = params
    run = _run(jsx, tmp_path, "rows_row")

    pairs = _pairs(data)
    yellow_late = 0
    for lay, (row, wd) in zip(run["layers"], pairs):
        r_t0 = row[0] / fps
        if not wd[2]:
            assert lay["inPoint"] == pytest.approx(r_t0)
            continue
        if wd[0] / fps > r_t0:
            yellow_late += 1
        # время появления = время строки, даже когда слово произнесено позже
        assert lay["inPoint"] == pytest.approx(r_t0)
        assert lay["opacity"]["keys"] == [[r_t0, 0], [r_t0 + dur, 100]]
        lineY = posy + row[2] * step
        assert lay["position"]["keys"] == [[r_t0, [lay["position"]["keys"][0][1][0], lineY + rise]],
                                           [r_t0 + dur, [lay["position"]["keys"][1][1][0], lineY]]]
    assert yellow_late >= 1, "жёлтых, произнесённых не первыми в строке, в фикстуре нет"


# ---------------------------------------------------------------- блюр появления
@node
@pytest.mark.parametrize("mode", ["words", "joined", "rows"])
def test_blur_keys_in_all_three_loops(xml_subs, tmp_path, mode):
    """hl_blur: Gaussian Blur 70.4 -> 0 на ключах подъёма во всех трёх циклах."""
    blur_st = {"hl_blur": True, "hl_blur_amt": 70.4}
    if mode == "words":
        jsx = _jsx(xml_subs, tmp_path, name="blur_words.jsx", highlights=[1],
                   style=dict(blur_st, sub_words_per_row=1))
    elif mode == "joined":
        jsx = _jsx(xml_subs, tmp_path, name="blur_joined.jsx", highlights=[0, 1], hl_joins=[0],
                   style=dict(blur_st, sub_words_per_row=1))
    else:
        jsx = _rows_jsx(xml_subs, tmp_path, name="blur_rows.jsx", **blur_st)

    assert "var HL_BLUR = 70.4;" in jsx
    assert "function hlBlur(L, t0){" in jsx
    assert '"ADBE Gaussian Blur 2"' in jsx
    assert 'bl.property("ADBE Gaussian Blur 2-0003").setValue(0);' in jsx
    assert jsx.count("hlBlur(L, t0);") == 1, "вызов hlBlur не ровно в одном месте цикла"

    dur = _hl_dur(jsx)
    run = _run(jsx, tmp_path, "blur_" + mode)
    yellow = [l for l in run["layers"] if l["fx"].get("ADBE Gaussian Blur 2")]
    assert yellow, "ни одному жёлтому слою не повесили блюр"
    for lay in yellow:
        fx = lay["fx"]["ADBE Gaussian Blur 2"][0]
        t0 = lay["inPoint"]
        # Подъём, проявление и блюр стоят на ОДНИХ ключах (контракт ZH). С задания MA
        # длительность у каждого жёлтого своя (короткое слово играет появление за hlDur(sw)),
        # поэтому конец берём у ключей проявления, а общая HL_DUR — только потолок.
        op_end = lay["opacity"]["keys"][-1][0]
        assert op_end <= t0 + dur, "появление жёлтого длиннее общей HL_DUR"
        assert fx["params"]["ADBE Gaussian Blur 2-0003"]["keys"] == [[None, 0]], "повтор краёв включён"
        assert fx["params"]["ADBE Gaussian Blur 2-0001"]["keys"] == [[t0, 70.4], [op_end, 0]]
        assert fx["params"]["ADBE Gaussian Blur 2-0001"]["eased"], "кривая блюра не easePair"


def test_blur_off_leaves_no_trace(xml_subs, tmp_path):
    """hl_blur=False — .jsx побайтово прежний эталон, ни функции, ни вызова."""
    jsx = _mask_assets(_build(xml_subs, tmp_path))
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert jsx == golden, "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"
    for token in ("hlBlur", "HL_BLUR", "ADBE Gaussian Blur 2", "HL_ROW_WORD"):
        assert token not in jsx, "при выключенном блюре/режиме слов в .jsx остался %s" % token


def test_blur_on_other_amount_changes_jsx(xml_subs, tmp_path):
    """Сила блюра — ручка: другое число меняет .jsx (и не трогает подъём)."""
    a = _jsx(xml_subs, tmp_path, name="amt_a.jsx", highlights=[1],
             style={"hl_blur": True, "hl_blur_amt": 70.4})
    b = _jsx(xml_subs, tmp_path, name="amt_b.jsx", highlights=[1],
             style={"hl_blur": True, "hl_blur_amt": 120.5})
    assert "var HL_BLUR = 120.5;" in b and "var HL_BLUR = 70.4;" in a


def test_plan_decls_empty_at_defaults(xml_subs):
    """Подстановки шаблона: при дефолтах пусты, в режиме строк и с блюром — заполнены."""
    kw = dict(emit=lambda *a, **k: None)
    rows = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 2}, **kw)["_ae"]
    assert "var HL_ROW_WORD = true;" in rows["hl_row_decl"]
    assert rows["hl_blur_decl"] == "" and rows["hl_blur_fn"] == ""

    blur = xml2ae.scene_plan(xml_subs, style={"hl_blur": True, "hl_blur_amt": 70.4}, **kw)["_ae"]
    assert "var HL_BLUR = 70.4;" in blur["hl_blur_decl"]
    assert "ADBE Gaussian Blur 2" in blur["hl_blur_fn"]
    assert blur["hl_row_decl"] == "", "объявление HL_ROW_WORD уехало в режим «по слову»"

    base = xml2ae.scene_plan(xml_subs, style={}, **kw)["_ae"]
    assert base["hl_row_decl"] == "" and base["hl_blur_decl"] == "" and base["hl_blur_fn"] == ""


def test_templates_carry_blur_call_placeholder():
    """Вызов hlBlur стоит в каждом из трёх циклов (подстановка пуста при выключенном блюре)."""
    for name, tpl in (("SUBS_LOOP_WORDS", SUBS_LOOP_WORDS),
                      ("SUBS_LOOP_WORDS_JOINED", SUBS_LOOP_WORDS_JOINED),
                      ("SUBS_LOOP_ROWS", SUBS_LOOP_ROWS)):
        assert "%(hl_blur_call)s" in tpl, "в %s нет вызова блюра" % name
    assert "HL_ROW_WORD" in SUBS_LOOP_ROWS
    assert "posP.setValueAtTime(t0," in SUBS_LOOP_ROWS
    assert "easePair(posP); easePair(op);%(hl_blur_call)s" in SUBS_LOOP_ROWS
