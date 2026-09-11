# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты параметров анимаций интро, огибающей и таймингов звука глитча.

Проверяет обязательные требования:
  * план сцены несёт блок параметров анимаций, и значения в нём совпадают с теми, что
    уезжают в .jsx (глитч и раскрытие);
  * в static/app/85-inserts-view.js больше НЕТ литералов 0.44, 0.1417, 0.93, 26.8, 0.2667;
  * превью без блока в плане не падает и берёт значения по умолчанию;
  * у слоя звука глитча четыре ключа Audio Levels (−48 → db → db → −48) — ПРАВКА 2;
  * смещения звука заданы в секундах (GLITCH_SFX_PRE_S), а не в кадрах.
"""
import gzip
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402
from core.xml2ae.build import (GLITCH_SFX_ATTACK_S, GLITCH_SFX_HOLD_S, GLITCH_SFX_PRE_S,  # noqa: E402
                          GLITCH_SFX_QUIET_DB, GLITCH_SFX_RELEASE_S, INTRO_ANIMS)

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_scene_plan_intro_anims_matches_jsx(xml_subs, tmp_path):
    """План сцены несёт intro_anims, и значения совпадают с уезжающими в .jsx."""
    plan = xml2ae.scene_plan(xml_subs)
    assert "intro_anims" in plan
    assert plan["intro_anims"] == INTRO_ANIMS
    anims = plan["intro_anims"]

    # Глитч
    assert anims["glitch"]["blur"] == 3.4
    assert anims["glitch"]["dur"] == 0.44
    assert anims["glitch"]["end_keys"] == [[0.017, 100], [0.205, 5], [0.392, 100]]
    assert anims["glitch"]["op_keys"] == [
        [0.0, 0], [0.05, 100], [0.1, 100], [0.1417, 0], [0.1833, 93], [0.225, 0], [0.2667, 100]
    ]

    # Раскрытие
    assert anims["reveal"]["blur"] == 26.8
    assert anims["reveal"]["scale"] == 0.7
    assert anims["reveal"]["scale_3d"] == [11, 11, 91.66667]
    assert anims["reveal"]["shape"] == 2
    assert anims["reveal"]["smoothness"] == 100
    assert anims["reveal"]["ease"] == [10, 95]

    # Проверяем сборку .jsx
    intro = [
        dict(words=["ТЕСТ"], color="white", times=[1.0], anim="glitch"),
        dict(words=["РЕВИЛ"], color="white", times=[2.0], anim="reveal"),
    ]
    out_jsx = str(tmp_path / "out_anims.jsx")
    xml2ae.to_ae_full(xml_subs, out_jsx, intro=intro, roto=False)
    with open(out_jsx, "r", encoding="utf-8") as f:
        jsx = f.read()

    # Сверка чисел в .jsx со значениями из plan["intro_anims"]
    g_dur = anims["glitch"]["dur"]
    assert f"pStart.setValueAtTime(t0+{g_dur:g},100);" in jsx

    for kt, kv in anims["glitch"]["op_keys"]:
        t_str = "t0" if kt == 0 else f"t0+{kt:g}"
        assert f"op.setValueAtTime({t_str},{kv:g});" in jsx

    r_blur = anims["reveal"]["blur"]
    assert f"pBl.setValueAtTime(t0,{r_blur:g});" in jsx
    r_sc = anims["reveal"]["scale"]
    assert f"sc.setValueAtTime(t0,[{int(round(r_sc*100))},{int(round(r_sc*100))}]);" in jsx
    assert f'adv.property("ADBE Text Range Shape").setValue({anims["reveal"]["shape"]});' in jsx
    assert f'adv.property("ADBE Text Selector Smoothness").setValue({anims["reveal"]["smoothness"]});' in jsx
    assert f'adv.property("ADBE Text Levels Max Ease").setValue({anims["reveal"]["ease"][0]});' in jsx
    assert f'adv.property("ADBE Text Levels Min Ease").setValue({anims["reveal"]["ease"][1]});' in jsx
    sc3d = anims["reveal"]["scale_3d"]
    sc3d_str = ",".join(f"{x:g}" if x == int(x) else str(x) for x in sc3d)
    assert f"aSc.setValue([{sc3d_str}]);" in jsx


def test_no_animation_literals_in_inserts_view_js():
    """В reelsi/static/app/85-inserts-view.js больше НЕТ литералов 0.44, 0.1417, 0.93, 26.8, 0.2667."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    forbidden = ["0.44", "0.1417", "0.93", "26.8", "0.2667"]
    for lit in forbidden:
        assert lit not in content, f"Литерал {lit} всё ещё присутствует в static/app/85-inserts-view.js!"


@node
def test_preview_without_plan_intro_anims_defaults(tmp_path):
    """Превью без блока параметров в плане не падает и берёт значения по умолчанию."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js_src = open(js_path, "r", encoding="utf-8").read()

    idx_ae = js_src.find("function aeEase")
    idx_pos = js_src.find("function ipvIntroPos(")
    fn_code = js_src[idx_ae:idx_pos]

    script = r"""
    const assert = require('assert');

    class Element {
      constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.style = {};
        this._classes = new Set();
        const self = this;
        this.classList = {
          add: (c) => self._classes.add(c),
          remove: (c) => self._classes.delete(c),
          toggle: (c, v) => v ? self._classes.add(c) : self._classes.delete(c),
          contains: (c) => self._classes.has(c)
        };
        this.dataset = {};
        this.children = [];
        this._text = '';
      }
      get textContent() {
        if(this._text !== undefined && this._text !== '') return this._text;
        if(this.children.length > 0) return this.children.map(c => c.textContent).join('');
        return '';
      }
      set textContent(v) {
        this._text = '' + (v != null ? v : '');
        this.children = [];
      }
      get innerHTML() { return this._html || ''; }
      set innerHTML(html) {
        this._html = html;
        this.children = [];
        if(!html){this._text = ''; return;}
        this._text = html.replace(/<[^>]+>/g, '');
        const matches = [...html.matchAll(/<span[^>]*>(.*?)<\/span>/g)];
        for(const m of matches) {
          const el = new Element('span');
          el.textContent = m[1];
          this.children.push(el);
        }
      }
      appendChild(child) { this.children.push(child); return child; }
      querySelectorAll(sel) {
        const res = [];
        if(sel === '.iline > span') {
          for(const line of this.children) {
            for(const sp of line.children) {
              if(sp.tagName === 'SPAN') res.push(sp);
            }
          }
          return res;
        }
        const scan = (el) => {
          for(const ch of el.children) {
            if(sel.includes('span') && ch.tagName === 'SPAN') res.push(ch);
            scan(ch);
          }
        };
        scan(this);
        return res;
      }
    }

    const ioEl = new Element('div');
    global.$ = (id) => (id === 'ipvintro' ? ioEl : null);
    global.document = { createElement: (tag) => new Element(tag),
      // Текстовый узел-пробел между словами (ipvIntro): tagName '#text', в селекторы
      // .iline>span не попадает — как настоящий текстовый узел в браузере.
      createTextNode: (txt) => ({ tagName: '#text', textContent: '' + txt, children: [] }) };
    global.t = (s) => s;
    global.esc = (s) => s;
    global.introMarkPlaying = () => {};
    global.ipvIntroPos = () => {};
    global.IPVMODE = 'ae';
    global.FONTS = [];
    global.CURSTYLE = {};
    // IPV без plan или с plan без intro_anims (старое сохранённое состояние)
    global.IPV = {
      introCur: -2,
      plan: {}, // БЛОКА intro_anims НЕТ
      intro: [{
        inAt: 1.0,
        outEnd: 5.0,
        lines: [
          { color: 'white', anim: 'glitch', words: ['ГЛИТЧ'], times: [1.1] },
          { color: 'yellow', anim: 'reveal', words: ['РЕВИЛ'], times: [1.3] }
        ]
      }]
    };
    """ + "\n" + fn_code + r"""
    // Тест 1: ipvGlitchOp без плана берёт значения по умолчанию и не падает
    assert.strictEqual(ipvGlitchOp(0), 0);
    assert.strictEqual(ipvGlitchOp(0.05), 1);
    assert.strictEqual(ipvGlitchOp(0.1417), 0);
    assert.strictEqual(ipvGlitchOp(0.2667), 1);
    assert.strictEqual(ipvGlitchOp(0.5), 1);

    // Тест 2: ipvIntro без intro_anims не падает и отображает анимации по умолчанию
    ipvIntro(1.15); // момент глитча
    assert.strictEqual(ioEl.style.display, 'flex');
    const spans = ioEl.querySelectorAll('.iline > span');
    assert.strictEqual(spans.length, 2);

    ipvIntro(1.35); // момент ревила
    const spRev = spans[1];
    assert.strictEqual(spRev.dataset.origWord, 'РЕВИЛ');
    assert(spRev.style.filter.includes('blur'), 'Ревил по умолчанию должен иметь blur');
    assert(spRev.style.transform.includes('scale'), 'Ревил по умолчанию должен иметь scale');

    console.log("OK: Preview without intro_anims runs successfully with defaults");
    """

    node_file = str(tmp_path / "test_dom_defaults.js")
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)

    res = subprocess.run(["node", node_file], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: Preview without intro_anims runs successfully with defaults" in res.stdout


def test_glitch_sound_audio_levels_fades(xml_subs, tmp_path):
    """У слоя звука глитча четыре ключа Audio Levels: −48 → db → db → −48 (ПРАВКА 2).

    Нарастание 0.08 с (GLITCH_SFX_ATTACK_S), спад 0.12 с (GLITCH_SFX_RELEASE_S) до
    GLITCH_SFX_QUIET_DB, конец спада — за кадр до конца слоя. Полка идёт от glitch_db.
    """
    intro = [
        dict(words=["СЛОВО"], color="white", times=[3.0], anim="glitch"),
    ]
    fake_wav = str(tmp_path / "glitch_sfx.wav")
    with open(fake_wav, "wb") as f:
        f.write(b"RIFFdummy")

    custom_db = -7.5
    out_jsx = str(tmp_path / "glitch_fades.jsx")
    xml2ae.to_ae_full(xml_subs, out_jsx, intro=intro, style={"glitch": fake_wav, "glitch_db": custom_db}, roto=False)
    with open(out_jsx, "r", encoding="utf-8") as f:
        jsx = f.read()

    # Проверяем базовый уровень и 4 ключа Audio Levels
    assert f"glAlv.setValue([{custom_db:g}, {custom_db:g}]);" in jsx
    assert f"glAlv.setValueAtTime(3, [{GLITCH_SFX_QUIET_DB:g}, {GLITCH_SFX_QUIET_DB:g}]);" in jsx
    assert f"glAlv.setValueAtTime(3.08, [{custom_db:g}, {custom_db:g}]);" in jsx
    assert f"glAlv.setValueAtTime(3.45, [{custom_db:g}, {custom_db:g}]);" in jsx
    assert f"glAlv.setValueAtTime(3.57, [{GLITCH_SFX_QUIET_DB:g}, {GLITCH_SFX_QUIET_DB:g}]);" in jsx
    # константы огибающей — те же, что в коде (вторая копии не заводится)
    assert GLITCH_SFX_ATTACK_S == 0.08 and GLITCH_SFX_HOLD_S == 0.45
    assert GLITCH_SFX_RELEASE_S == 0.12 and GLITCH_SFX_QUIET_DB == -48.0


def test_glitch_sound_offsets_fps_independent(xml_subs, tmp_path):
    """Смещения звука глитча одинаковы при fps 25 и 30 (в секундах), а не разъезжаются."""
    # Константы смещений в секундах
    assert GLITCH_SFX_PRE_S == 0.567

    intro = [
        dict(words=["ТЕСТ"], color="white", times=[2.0], anim="glitch"),
    ]
    fake_wav = str(tmp_path / "glitch_sfx.wav")
    with open(fake_wav, "wb") as f:
        f.write(b"RIFFdummy")

    # Проверяем сборку .jsx: startTime = 2.0 − 0.567 = 1.433, слой живёт до
    # 2.0 + полка 0.45 + спад 0.12 + кадр (в фикстуре 60 fps) = 2.5867
    out_jsx = str(tmp_path / "glitch_fps.jsx")
    xml2ae.to_ae_full(xml_subs, out_jsx, intro=intro, style={"glitch": fake_wav}, roto=False)
    with open(out_jsx, "r", encoding="utf-8") as f:
        jsx = f.read()

    assert "gl.startTime=1.433; gl.inPoint=2; gl.outPoint=2.5867;" in jsx
