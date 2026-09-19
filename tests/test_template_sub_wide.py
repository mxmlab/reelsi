# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Широкий прекомп субтитров (задание GP).

Что проверяем:
1. В шаблоне объявлено var SUB_WIDE=3; и var SW=Math.round(W*SUB_WIDE);
2. Прекомп субтитров создаётся с SW (широкая ширина);
3. В трёх циклах субтитров нет W/2, W / 2 и (W - totW);
4. При sub_scale=70 якорь [SW/2, POSY], позиция [W/2, POSY];
5. Инвариант геометрии в node: каждый из трёх циклов исполняется с заглушками
   (sourceRectAtTime отдаёт заданные ширины, W=1080) дважды — с SUB_WIDE=1 и SUB_WIDE=3;
   для каждого слоя X(3) - (SW - W)/2 == X(1) с точностью 1e-6. Стенд цикла строк
   объявляет HL_ROW_WORD (задание ZH): жёлтое слово въезжает в момент слова, а не
   со строкой, — на X-раскладку это не влияет.
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
from core.xml2ae.template import SUBS_LOOP_WORDS, SUBS_LOOP_WORDS_JOINED, SUBS_LOOP_ROWS  # noqa: E402

SRC = xml2ae.AE_FULL
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_sub_wide_constants_in_template():
    assert "var SUB_WIDE=3;" in SRC
    assert "var SW=Math.round(W*SUB_WIDE);" in SRC


def test_subc_uses_wide_width():
    i = SRC.index("app.project.items.addComp(%(sub_comp_name)s")
    seg = SRC[i:i + 120]
    assert "addComp(%(sub_comp_name)s, SW, H," in seg


def test_no_old_w_half_in_subtitle_loops():
    loops = [
        ("SUBS_LOOP_WORDS", SUBS_LOOP_WORDS),
        ("SUBS_LOOP_WORDS_JOINED", SUBS_LOOP_WORDS_JOINED),
        ("SUBS_LOOP_ROWS", SUBS_LOOP_ROWS),
    ]
    for name, code in loops:
        assert not re.search(r'(?<![A-Za-z0-9_])W/2', code), f"в {name} остался 'W/2'"
        assert not re.search(r'(?<![A-Za-z0-9_])W\s*/\s*2', code), f"в {name} остался 'W / 2'"
        assert "(W - totW)" not in code, f"в {name} остался '(W - totW)'"


def test_sub_scale_70_anchor_and_position(xml_subs, tmp_path):
    out = str(tmp_path / "scale70.jsx")
    xml2ae.to_ae_full(xml_subs, jsx_path=out, style={"sub_scale": 70}, emit=lambda *a: None)
    txt = open(out, encoding="utf-8-sig").read()

    # Якорь [SW/2, POSY], позиция [W/2, POSY]
    assert re.search(r'subLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Anchor Point"\)\.setValue\(\[SW/2, \d+\]\);', txt)
    assert re.search(r'subLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, \d+\]\);', txt)


_JS_GEOMETRY_RUNNER = r"""
const loopName = process.argv[2];
const wideVal = parseFloat(process.argv[3]);

let layers = [];
const W = 1080;
const H = 1920;
const SUB_WIDE = wideVal;
const SW = Math.round(W * SUB_WIDE);
const FITW = W * 0.92;
const FPS = 30;
const DUR = 10;
const POSY = 864;
const HL_STEP = 100;
const HL_RISE = 60;
const HL_DUR = 0.35;
const HL_ROW_WORD = true;   // цикл строк (задание ZH): жёлтое въезжает в момент слова
const HL_BOLD = true;
const FONT_SIZE = 72;
const HL_FILL = [1, 0.9, 0];
const FILL = [1, 1, 1];
const HL_FONT = 'HLFont';
const FONT = 'BaseFont';
const ParagraphJustification = { CENTER_JUSTIFY: 1 };

function easePair() {}

const subc = {
  layers: {
    addText: function(txt) {
      const lay = {
        text: txt,
        inPoint: 0,
        outPoint: 0,
        posX: null,
        property: function(prop) {
          if (prop === 'ADBE Text Properties') {
            return {
              property: function() {
                return {
                  value: {
                    resetCharStyle: ()=>{},
                    resetParagraphStyle: ()=>{}
                  },
                  setValue: ()=>{}
                };
              }
            };
          }
          if (prop === 'ADBE Transform Group') {
            return {
              property: function(p) {
                if (p === 'ADBE Position') {
                  return {
                    setValue: function(v) { lay.posX = v[0]; },
                    setValueAtTime: function(t, v) { lay.posX = v[0]; }
                  };
                }
                return {
                  setValue: ()=>{},
                  setValueAtTime: ()=>{}
                };
              }
            };
          }
          return { property: ()=>({}) };
        },
        sourceRectAtTime: function() {
          // Заданная ширина слова: 150px
          return { width: 150, height: 50 };
        }
      };
      layers.push(lay);
      return lay;
    }
  }
};

if (loopName === 'words') {
  const SUBS = [
    [0, 15, 'Первое', false, 0, 15],
    [15, 30, 'Второе', true, 0, 45],
    [30, 45, 'Третье', true, 1, 45],
    [45, 60, 'Четвёртое', false, 0, 60]
  ];
  __LOOP_WORDS__
} else if (loopName === 'words_joined') {
  const SUBS = [
    [0, 15, 'Белое', false, 0, 15],
    [15, 30, 'Слово1', true, 0, 60],
    [30, 45, 'Слово2', true, 0, 60],
    [45, 60, 'Слово3', true, 0, 60],
    [60, 75, 'Хвост', false, 0, 75]
  ];
  __LOOP_WORDS_JOINED__
} else if (loopName === 'rows') {
  const _SUB_ROWS_DATA = [
    [0, 30, 0, 72, [[0, 'Раз', false], [10, 'Два', true], [20, 'Три', false]]],
    [30, 60, 1, 80, [[30, 'Четыре', true], [40, 'Пять', true]]]
  ];
  const _SUB_STEP_DATA = 120;
  __LOOP_ROWS__
}

const res = layers.map(l => ({ text: l.text, x: l.posX }));
console.log(JSON.stringify(res));
"""


@node
@pytest.mark.parametrize("loop_key, loop_code", [
    ("words", SUBS_LOOP_WORDS.replace("%(sub_count_code)s", "").replace("%(hl_blur_call)s", "").replace("%(hl_dur_js)s", "HL_DUR")),
    ("words_joined", SUBS_LOOP_WORDS_JOINED.replace("%(sub_count_code)s", "").replace("%(hl_blur_call)s", "").replace("%(hl_dur_js)s", "HL_DUR")),
    ("rows", SUBS_LOOP_ROWS.replace("%(sub_rows)s", "_SUB_ROWS_DATA").replace("%(sub_step)g", "_SUB_STEP_DATA").replace("%(hl_blur_call)s", "")),
])
def test_sub_wide_geometry_invariant_in_node(loop_key, loop_code, tmp_path):
    """Инвариант геометрии: для каждого слоя X(3) - (SW - W)/2 == X(1) с точностью 1e-6."""
    script_base = _JS_GEOMETRY_RUNNER
    if loop_key == "words":
        script_base = script_base.replace("__LOOP_WORDS__", loop_code)
    elif loop_key == "words_joined":
        script_base = script_base.replace("__LOOP_WORDS_JOINED__", loop_code)
    elif loop_key == "rows":
        script_base = script_base.replace("__LOOP_ROWS__", loop_code)

    def run_node(wide):
        js_file = tmp_path / f"runner_{loop_key}_{wide}.js"
        js_file.write_text(script_base, encoding="utf-8")
        res = subprocess.run(["node", str(js_file), loop_key, str(wide)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert res.returncode == 0, f"node упал: {res.stderr}"
        return json.loads(res.stdout.strip().splitlines()[-1])

    out1 = run_node(1.0)
    out3 = run_node(3.0)

    assert len(out1) == len(out3)
    assert len(out1) > 0

    W = 1080.0
    SW3 = round(W * 3.0)
    offset = (SW3 - W) / 2.0  # 1080.0

    for l1, l3 in zip(out1, out3):
        assert l1["text"] == l3["text"]
        x1 = l1["x"]
        x3 = l3["x"]
        assert abs((x3 - offset) - x1) < 1e-6, (
            f"Инвариант геометрии нарушен для '{l1['text']}': X(3)={x3}, X(1)={x1}, diff={abs((x3 - offset) - x1)}"
        )
