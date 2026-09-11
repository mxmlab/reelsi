# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свечение жёлтого хайлайта интро с тритоном (задание GP).

Что проверяем (10 случаев из раздела «Как проверить» задания GP):
  1. группа [белая back «благодаря»] + [жёлтая «ЭТОМУ ПЕП*ИДУ»] без anim/fx:
     у каждого жёлтого слова ровно [Glo2 149/77/0.62, Tritone(Midtones=HL_FILL)],
     у белого нет Glo2 и Tritone, у прекомпа нет Glo2;
  2. группа из трёх строк back с anim="reveal", средняя жёлтая:
     у жёлтого слова порядок Drop Shadow, Gaussian Blur 2, Glo2, Tritone;
  3. группа [жёлтая back «слово»] + [жёлтая glitch+glow «КОНСУЛЬТАЦИЯ»]:
     у «слово» нет Glo2 и Tritone, у «КОНСУЛЬТАЦИЯ» как сейчас, прекомп Glo2 211;
  4. группа из двух белых строк без anim/fx:
     прекомп Glo2 радиус 42, у слов эффектов нет;
  5. группа [жёлтая fx="glow"]:
     у слова Glo2 и Tritone ровно по одному, у прекомпа нет Glo2;
  6. сборка, где НИ у одной строки нет anim/fx: группа с жёлтой строкой ->
     Glo2+Tritone на слове, прекомп без Glo2; соседняя группа из белых строк ->
     прекомп Glo2 радиус 42;
  7. случай 1 в режиме intro_mode="line" -> то же на слое строки;
  8. группа [accent без anim/fx] -> у слова эффектов нет, прекомп Glo2 радиус 42;
  9. стиль с intro_hl_fill -> Midtones тритона = INTRO_HL_FILL;
  10. сборка без жёлтых строк интро -> в .jsx нет introHlGlow и нет grpYellow.
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
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word", splits=None, name="out.jsx"):
    splits = splits if splits is not None else [len(intro)]
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=splits, style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _hl_fill_literal(jsx):
    m = re.search(r"var HL_FILL = (\[[^\]]*\])(?:,|;)", jsx)
    assert m, "в .jsx нет объявления HL_FILL"
    return m.group(1)


def _probe_intro(jsx, tmp_path):
    i_intro = jsx.index("if (INTRO_GROUPS.length){")
    i_roto = jsx.index("var rotoLayers = [];")
    intro_code = jsx[i_intro:i_roto]

    def get_var(name, default="null"):
        m = re.search(r"^\s*var\s+" + name + r"\s*=\s*(.*?);", jsx, re.M)
        return f"var {name} = " + (m.group(1) if m else default) + ";"

    declarations = "\n".join([
        get_var("W", "1080"),
        get_var("H", "1920"),
        get_var("FPS", "30"),
        get_var("INTRO_WIDE", "1.33"),
        get_var("INTRO_MODE", '"word"'),
        get_var("INTRO_GROUPS"),
        get_var("INTRO_ON2", "[]"),
        get_var("INTRO_IDY", "[]"),
        get_var("INTRO_LY", "[]"),
        get_var("INTRO_GLOW", "1"),
        get_var("HL_FILL", "[1,0.8,0.1]"),
        get_var("INTRO_HL_FILL", "null"),
        get_var("HL_BOLD", "false"),
        get_var("HL_DUR", "0.35"),
        get_var("F_DUR", "0.25"),
        get_var("HL_RISE", "60"),
        get_var("BACK_SCALE", "1"),
        get_var("BACK_STEP", "0.45"),
        get_var("INTRO_FONT", '"Arial"'),
        get_var("ACCENT_FONT", '"Arial"'),
        get_var("CUSTOM_FONT", '"Arial"'),
        get_var("FONT_SIZE", "60"),
        get_var("INTRO_SHADOW_OP", "40"),
        get_var("BACK_SHADOW_OP", "70"),
        get_var("INTRO_SHADOW_DIR", "135"),
        get_var("INTRO_SHADOW_DIST", "0"),
        get_var("BACK_SHADOW_SOFT", "40"),
        get_var("INTRO_SHADOW_SOFT", "40"),
        get_var("INTRO_FX", "[]"),
    ])

    js_runner = r"""
var recordedLayers = [];
var precomps = [];
var introLayers = [];

function makeLayer(isPrecomp, groupIndex) {
    var layer = {
        isPrecomp: isPrecomp,
        groupIndex: groupIndex,
        text: '',
        removed: false,
        effects: [],
        property: function(name) {
            if (name === 'ADBE Effect Parade') {
                return {
                    addProperty: function(mn) {
                        var eff = { matchName: mn, props: {}, property: function(pn) {
                            return { setValue: function(v) { eff.props[pn] = v; } };
                        } };
                        layer.effects.push(eff);
                        return eff;
                    }
                };
            }
            var p = {
                value: {
                    resetCharStyle: function() {},
                    resetParagraphStyle: function() {},
                    text: '',
                    font: '',
                    fontSize: 60,
                    fillColor: [1, 1, 1],
                    applyFill: true,
                    justification: null
                },
                setValue: function(v) {
                    p.value = v;
                    if (v && v.text) layer.text = v.text;
                },
                setValueAtTime: function(t, v) {},
                property: function() { return p; }
            };
            return p;
        },
        sourceRectAtTime: function() { return { width: 100, height: 50 }; },
        remove: function() { layer.removed = true; }
    };
    return layer;
}

function addFX(L, mn) {
    var eff = { matchName: mn, props: {}, property: function(pn) {
        return { setValue: function(v) { eff.props[pn] = v; } };
    } };
    L.effects.push(eff);
    return eff;
}
function setP(fx, mn, v) {
    if (fx) fx.props[mn] = v;
}
function easePair() {}
function toBin() {}
function dropShadow() {}
var ParagraphJustification = { CENTER_JUSTIFY: 1 };
var introNull = null, introNull2 = null;

var app = {
    project: {
        items: {
            addComp: function(name, w, h, pa, dur, fps) {
                var compObj = {
                    name: name,
                    layers: {
                        addText: function(txt) {
                            var L = makeLayer(false, precomps.length);
                            L.text = txt;
                            recordedLayers.push(L);
                            return L;
                        }
                    }
                };
                return compObj;
            }
        }
    }
};

var main = {
    layers: {
        add: function(compObj) {
            var iL = makeLayer(true, precomps.length);
            precomps.push(iL);
            return iL;
        }
    }
};

""" + declarations + "\n" + intro_code + r"""

var out = {
    layers: recordedLayers.filter(function(w){ return !w.removed && w.text !== ''; }).map(function(w) {
        return {
            text: w.text,
            groupIndex: w.groupIndex,
            effects: w.effects.map(function(e) {
                return { matchName: e.matchName, props: e.props };
            })
        };
    }),
    precomps: precomps.map(function(p, idx) {
        return {
            groupIndex: idx,
            effects: p.effects.map(function(e) {
                return { matchName: e.matchName, props: e.props };
            })
        };
    })
};

console.log(JSON.stringify(out));
"""
    script_path = tmp_path / "probe_intro_hl_glow.js"
    script_path.write_text(js_runner, encoding="utf-8")
    res = subprocess.run(["node", str(script_path)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node упал: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


@node
def test_case_1_white_back_yellow_no_anim_word_mode(xml_subs, tmp_path):
    """Случай 1: группа [белая back 'благодаря'] + [жёлтая 'ЭТОМУ ПЕП*ИДУ'] без anim/fx.

    У каждого жёлтого слова ровно [Glo2 149/77/0.62, Tritone(Midtones=HL_FILL)],
    у белого нет Glo2 и Tritone, у прекомпа нет Glo2.
    """
    intro = [
        dict(words=["благодаря"], color="white", times=[1.0], back=True),
        dict(words=["ЭТОМУ", "ПЕП*ИДУ"], color="yellow", times=[1.5]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[2], name="case1.jsx")
    data = _probe_intro(jsx, tmp_path)

    # Белое слово "благодаря" — нет Glo2 и нет Tritone
    white_words = [w for w in data["layers"] if w["text"] == "благодаря"]
    assert len(white_words) == 1
    white_mns = [e["matchName"] for e in white_words[0]["effects"]]
    assert "ADBE Glo2" not in white_mns
    assert "ADBE Tritone" not in white_mns

    # Жёлтые слова "ЭТОМУ" и "ПЕП*ИДУ"
    for wtext in ["ЭТОМУ", "ПЕП*ИДУ"]:
        yw = next(w for w in data["layers"] if w["text"] == wtext)
        effs = yw["effects"]
        assert [e["matchName"] for e in effs] == ["ADBE Glo2", "ADBE Tritone"]
        assert effs[0]["props"] == {
            "ADBE Glo2-0002": 149,
            "ADBE Glo2-0003": 77,
            "ADBE Glo2-0004": 0.62,
        }
        hl = json.loads(_hl_fill_literal(jsx))
        assert effs[1]["props"]["ADBE Tritone-0002"] == hl

    # У прекомпа нет Glo2
    precomp_mns = [e["matchName"] for e in data["precomps"][0]["effects"]]
    assert "ADBE Glo2" not in precomp_mns


@node
def test_case_2_reveal_back_middle_yellow_effects_order(xml_subs, tmp_path):
    """Случай 2: группа из трёх строк back с anim='reveal', средняя жёлтая.

    У жёлтого слова порядок эффектов: Drop Shadow, Gaussian Blur 2, Glo2, Tritone.
    """
    intro = [
        dict(words=["ПЕРВАЯ"], color="white", back=True, anim="reveal", times=[1.0]),
        dict(words=["ЖЁЛТАЯ"], color="yellow", back=True, anim="reveal", times=[1.3]),
        dict(words=["ТРЕТЬЯ"], color="white", back=True, anim="reveal", times=[1.6]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[3], name="case2.jsx")
    data = _probe_intro(jsx, tmp_path)

    yw = next(w for w in data["layers"] if w["text"].lower() == "жёлтая")
    eff_names = [e["matchName"] for e in yw["effects"]]
    assert eff_names == [
        "ADBE Drop Shadow",
        "ADBE Gaussian Blur 2",
        "ADBE Glo2",
        "ADBE Tritone",
    ]


@node
def test_case_3_yellow_back_and_yellow_glitch_glow(xml_subs, tmp_path):
    """Случай 3: группа [жёлтая back 'слово'] + [жёлтая glitch+glow 'КОНСУЛЬТАЦИЯ'].

    У 'слово' нет Glo2 и Tritone (grpGlitch=true в группе),
    у 'КОНСУЛЬТАЦИЯ' как сейчас, прекомп Glo2 211.
    """
    intro = [
        dict(words=["слово"], color="yellow", back=True, times=[1.0]),
        dict(words=["КОНСУЛЬТАЦИЯ"], color="yellow", anim="glitch", fx="glow", times=[1.5]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[2], name="case3.jsx")
    data = _probe_intro(jsx, tmp_path)

    w_slovo = next(w for w in data["layers"] if w["text"] == "слово")
    slovo_mns = [e["matchName"] for e in w_slovo["effects"]]
    assert "ADBE Glo2" not in slovo_mns
    assert "ADBE Tritone" not in slovo_mns

    w_kons = next(w for w in data["layers"] if w["text"] == "КОНСУЛЬТАЦИЯ")
    kons_mns = [e["matchName"] for e in w_kons["effects"]]
    assert "ADBE Glo2" in kons_mns
    assert "ADBE Tritone" in kons_mns

    # Прекомп группы с глитчем — усиленный Glo2 211/93/0.42
    precomp_effs = data["precomps"][0]["effects"]
    glo = next(e for e in precomp_effs if e["matchName"] == "ADBE Glo2")
    assert glo["props"] == {
        "ADBE Glo2-0002": 211,
        "ADBE Glo2-0003": 93,
        "ADBE Glo2-0004": 0.42,
    }


@node
def test_case_4_two_white_lines_plain_precomp_glow_42(xml_subs, tmp_path):
    """Случай 4: группа из двух белых строк без anim/fx.

    Прекомп Glo2 радиус 42, у слов эффектов нет.
    """
    intro = [
        dict(words=["БЕЛАЯ", "ОДИН"], color="white", times=[1.0]),
        dict(words=["БЕЛАЯ", "ДВА"], color="white", times=[1.3]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[2], name="case4.jsx")
    data = _probe_intro(jsx, tmp_path)

    for w in data["layers"]:
        assert w["effects"] == []

    precomp_effs = data["precomps"][0]["effects"]
    glo = next(e for e in precomp_effs if e["matchName"] == "ADBE Glo2")
    assert glo["props"]["Glow Radius"] == 42


@node
def test_case_5_yellow_fx_glow_single_effects_no_comp_glow(xml_subs, tmp_path):
    """Случай 5: группа [жёлтая fx='glow'].

    У слова Glo2 и Tritone ровно по одному, у прекомпа нет Glo2.
    """
    intro = [
        dict(words=["СВЕЧЕНИЕ"], color="yellow", fx="glow", times=[1.0]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[1], name="case5.jsx")
    data = _probe_intro(jsx, tmp_path)

    w = next(w for w in data["layers"] if w["text"] == "СВЕЧЕНИЕ")
    glo_count = sum(1 for e in w["effects"] if e["matchName"] == "ADBE Glo2")
    tt_count = sum(1 for e in w["effects"] if e["matchName"] == "ADBE Tritone")
    assert glo_count == 1
    assert tt_count == 1

    precomp_mns = [e["matchName"] for e in data["precomps"][0]["effects"]]
    assert "ADBE Glo2" not in precomp_mns


@node
def test_case_6_no_anim_fx_two_groups_yellow_and_white(xml_subs, tmp_path):
    """Случай 6: сборка, где НИ у одной строки нет anim/fx.

    Группа с жёлтой строкой -> Glo2+Tritone на слове, прекомп без Glo2;
    соседняя группа из белых строк -> прекомп Glo2 радиус 42.
    """
    intro = [
        dict(words=["ЖЁЛТАЯ"], color="yellow", times=[1.0]),
        dict(words=["БЕЛАЯ"], color="white", times=[5.0]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[1, 1], name="case6.jsx")
    data = _probe_intro(jsx, tmp_path)

    # Группа 0: слово ЖЁЛТАЯ
    yw = next(w for w in data["layers"] if w["groupIndex"] == 0 and w["text"] == "ЖЁЛТАЯ")
    yw_mns = [e["matchName"] for e in yw["effects"]]
    assert "ADBE Glo2" in yw_mns
    assert "ADBE Tritone" in yw_mns

    # Прекомп группы 0 — без Glo2
    p0_mns = [e["matchName"] for e in data["precomps"][0]["effects"]]
    assert "ADBE Glo2" not in p0_mns

    # Группа 1: слово БЕЛАЯ — без эффектов
    ww = next(w for w in data["layers"] if w["groupIndex"] == 1 and w["text"] == "БЕЛАЯ")
    assert ww["effects"] == []

    # Прекомп группы 1 — Glo2 радиус 42
    p1_glo = next(e for e in data["precomps"][1]["effects"] if e["matchName"] == "ADBE Glo2")
    assert p1_glo["props"]["Glow Radius"] == 42


@node
def test_case_7_line_mode_case_1(xml_subs, tmp_path):
    """Случай 7: случай 1 в режиме intro_mode='line' -> то же на слое строки."""
    intro = [
        dict(words=["благодаря"], color="white", times=[1.0], back=True),
        dict(words=["ЭТОМУ", "ПЕП*ИДУ"], color="yellow", times=[1.5]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, mode="line", splits=[2], name="case7.jsx")
    data = _probe_intro(jsx, tmp_path)

    # Слой белой строки
    w_line = next(l for l in data["layers"] if "благодаря" in l["text"])
    w_mns = [e["matchName"] for e in w_line["effects"]]
    assert "ADBE Glo2" not in w_mns
    assert "ADBE Tritone" not in w_mns

    # Слой жёлтой строки
    y_line = next(l for l in data["layers"] if "ЭТОМУ" in l["text"])
    effs = y_line["effects"]
    assert [e["matchName"] for e in effs] == ["ADBE Glo2", "ADBE Tritone"]
    assert effs[0]["props"] == {
        "ADBE Glo2-0002": 149,
        "ADBE Glo2-0003": 77,
        "ADBE Glo2-0004": 0.62,
    }
    hl = json.loads(_hl_fill_literal(jsx))
    assert effs[1]["props"]["ADBE Tritone-0002"] == hl

    # Прекомп без Glo2
    precomp_mns = [e["matchName"] for e in data["precomps"][0]["effects"]]
    assert "ADBE Glo2" not in precomp_mns


@node
def test_case_8_accent_without_anim_fx(xml_subs, tmp_path):
    """Случай 8: группа [accent без anim/fx] -> у слова эффектов нет, прекомп Glo2 радиус 42."""
    intro = [
        dict(words=["АКЦЕНТ"], color="accent", times=[1.0]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[1], name="case8.jsx")
    data = _probe_intro(jsx, tmp_path)

    w = next(w for w in data["layers"] if w["text"] == "АКЦЕНТ")
    assert w["effects"] == []

    precomp_effs = data["precomps"][0]["effects"]
    glo = next(e for e in precomp_effs if e["matchName"] == "ADBE Glo2")
    assert glo["props"]["Glow Radius"] == 42


@node
def test_case_9_style_with_intro_hl_fill(xml_subs, tmp_path):
    """Случай 9: стиль с intro_hl_fill -> Midtones тритона = INTRO_HL_FILL."""
    custom_fill = [0.2, 0.4, 0.8]
    intro = [
        dict(words=["ЖЁЛТАЯ"], color="yellow", times=[1.0]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[1],
                    style={"intro_hl_fill": custom_fill}, name="case9.jsx")
    data = _probe_intro(jsx, tmp_path)

    yw = next(w for w in data["layers"] if w["text"] == "ЖЁЛТАЯ")
    tt = next(e for e in yw["effects"] if e["matchName"] == "ADBE Tritone")
    assert tt["props"]["ADBE Tritone-0002"] == custom_fill


def test_case_10_no_yellow_intro_lines_no_fn_and_no_grp_yellow(xml_subs, tmp_path):
    """Случай 10: сборка без жёлтых строк интро -> в .jsx нет introHlGlow и нет grpYellow."""
    intro = [
        dict(words=["БЕЛАЯ"], color="white", times=[1.0]),
        dict(words=["АКЦЕНТ"], color="accent", times=[1.5]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, splits=[2], name="case10.jsx")

    assert "introHlGlow" not in jsx
    assert "grpYellow" not in jsx
