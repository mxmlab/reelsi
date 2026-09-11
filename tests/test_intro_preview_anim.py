# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты предпросмотра анимаций, цветов, размеров и счетчиков интро в браузере.

Проверяет:
  * В static/app/85-inserts-view.js реализована покадровая анимация:
    - кривая cubic-bezier easePair(35, 90) (ipvEase);
    - эффекты anim: 'glitch', 'reveal', 'up', 'left', 'right', fade;
    - числовой счетчик is_count (0 -> target за 1.5с по ipvEase с сохранением точности, запятой и пробелов);
    - цвета: yellow, accent, custom (l.fill), white из плана/стиля;
    - свечение fx: 'glow';
    - размеры и интервалы для l.back (back_scale, marginTop);
    - групповой фейд-аут за 0.75с до конца группы;
  * В xml2ae/build.py: plan содержит hl_fill3, intro_fill, intro_hl_fill, fonts,
    back_scale, back_step;
  * В static/app.css: .ipvintro span имеет display:inline-block и не блокируется задержкой
    transition; межсловный отступ задаёт сам ipvIntro текстовым узлом-пробелом (пробел
    шрифта, как в AE), CSS-правила margin-left .32em для него больше нет;
  * Через node: прямое выполнение функций анимации и DOM-моделирования подтверждает точные значения на кадрах.
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

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_plan_contains_intro_style_fields(xml_subs):
    """scene_plan пробрасывает цвета интро и параметры back_scale в plan для превью."""
    style = {
        "hl_fill3": [0.8, 0.2, 0.3],
        "intro_fill": [0.9, 0.9, 0.9],
        "intro_hl_fill": [1.0, 0.8, 0.1],
        "back_scale": 0.65,
        "back_step": 0.40,
    }
    intro = [
        {"words": ["ТЕСТ"], "color": "white", "times": [1.0]},
    ]
    plan = xml2ae.scene_plan(xml_subs, style=style, intro=intro)
    assert plan["hl_fill3"] == [0.8, 0.2, 0.3]
    assert plan["intro_fill"] == [0.9, 0.9, 0.9]
    assert plan["intro_hl_fill"] == [1.0, 0.8, 0.1]
    assert plan["back_scale"] == 0.65
    assert plan["back_step"] == 0.40


def test_app_css_ipvintro_span():
    """В static/app.css span интро имеет display:inline-block для работы transform/filter."""
    css_path = os.path.join(ROOT, "static", "app.css")
    css = open(css_path, "r", encoding="utf-8").read()
    m = re.search(r"\.ipvintro\s+span\{([^}]+)\}", css)
    assert m, "селектор .ipvintro span не найден в app.css"
    rule = m.group(1)
    assert "display:inline-block" in rule or "display: inline-block" in rule
    assert "transition" not in rule, "transition:opacity не должен задерживать покадровый рендеринг"


def test_js_helpers_presence():
    """В static/app/85-inserts-view.js присутствуют все хелперы анимации интро."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js = open(js_path, "r", encoding="utf-8").read()
    assert "function ipvEase(" in js
    assert "function ipvToHex(" in js
    assert "function ipvParseCount(" in js
    assert "function ipvGlitchOp(" in js
    assert "function ipvIntro(" in js
    assert "else{dv.style.fontWeight='800';}" in js or 'else{dv.style.fontWeight="800";}' in js
    assert "fontVariationSettings" in js


@node
def test_node_intro_animation_logic(tmp_path):
    """Исполнение логики анимации интро через Node.js:
    проверяет точность счета, скрамблинга, интерполяции ease, цветов и трансформаций.
    """
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js_src = open(js_path, "r", encoding="utf-8").read()

    parts = []
    for fn in ["function aeEase", "function bezierY", "function bezierT",
                "function ipvEase", "function ipvToHex", "function ipvParseCount",
                "function ipvIntroDefAnims", "function ipvGlitchOp"]:
        idx = js_src.find(fn)
        assert idx >= 0, f"не найдена {fn}"
        end = js_src.find("\nfunction ", idx + 10)
        parts.append(js_src[idx:end if end > 0 else len(js_src)])

    script = "\n".join(parts) + """
    const assert = require('assert');

    // 1. Тест ipvEase (кривая 35, 90)
    assert.strictEqual(ipvEase(0), 0);
    assert.strictEqual(ipvEase(1), 1);
    const midEase = ipvEase(0.5);
    assert(midEase > 0.6 && midEase < 0.95, 'Кривая 35/90 на u=0.5 должна быть крутой (~0.85): ' + midEase);

    // 2. Тест ipvToHex
    assert.strictEqual(ipvToHex([1, 1, 1]), '#ffffff');
    assert.strictEqual(ipvToHex([0, 0, 0]), '#000000');
    assert.strictEqual(ipvToHex([0.6863, 0.1216, 0.1216]), '#af1f1f');
    assert.strictEqual(ipvToHex('var(--yel)'), 'var(--yel)');

    // 3. Тест ipvParseCount
    const c1 = ipvParseCount("1500");
    assert.deepStrictEqual(c1, {val: 1500, dec: 0, hasComma: false, hasSpaces: false, pfx: '', sfx: ''});

    const c2 = ipvParseCount("12,5");
    assert.deepStrictEqual(c2, {val: 12.5, dec: 1, hasComma: true, hasSpaces: false, pfx: '', sfx: ''});

    const c3 = ipvParseCount("+500%");
    assert.deepStrictEqual(c3, {val: 500, dec: 0, hasComma: false, hasSpaces: false, pfx: '+', sfx: '%'});

    const c4 = ipvParseCount("1 000 000");
    assert.deepStrictEqual(c4, {val: 1000000, dec: 0, hasComma: false, hasSpaces: true, pfx: '', sfx: ''});

    const c5 = ipvParseCount("привет");
    assert.strictEqual(c5, null);

    // 4. Тест ipvGlitchOp
    assert.strictEqual(ipvGlitchOp(0), 0);
    assert.strictEqual(ipvGlitchOp(0.05), 1);
    assert.strictEqual(ipvGlitchOp(0.1417), 0);
    assert.strictEqual(ipvGlitchOp(0.2667), 1);
    assert.strictEqual(ipvGlitchOp(0.5), 1);

    console.log("OK: All intro animation logic tests passed in Node.js");
    """
    node_file = str(tmp_path / "test_anim.js")
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)

    res = subprocess.run(["node", node_file], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: All intro animation logic tests passed in Node.js" in res.stdout


@node
def test_node_full_dom_intro_preview(tmp_path):
    """Исполнение полного цикла ipvIntro(tm) на симуляторе DOM в Node.js."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js_src = open(js_path, "r", encoding="utf-8").read()

    idx_ae = js_src.find("function aeEase")
    idx_pos = js_src.find("function ipvIntroPos(")
    fn_code = js_src[idx_ae:idx_pos]

    sim_dom = r"""
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
      get innerHTML() {
        return this._html || '';
      }
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
    global.CURSTYLE = {
      intro_hl_fill: [1, 0.9, 0],
      hl_fill3: [0.6863, 0.1216, 0.1216],
      intro_fill: [1, 1, 1],
      back_scale: 0.69
    };
    global.IPV = {
      introCur: -2,
      intro: [{
        inAt: 1.0,
        outEnd: 5.0,
        lines: [
          {
            color: 'yellow',
            anim: 'reveal',
            words: ['1500', 'человек'],
            times: [1.0, 1.2],
            is_count: true,
            cnt: 1500,
            dec: 0,
            cnt_idx: 0
          },
          {
            color: 'accent',
            anim: 'up',
            words: ['СТАРТ'],
            times: [1.4]
          },
          {
            color: 'white',
            back: true,
            anim: 'glitch',
            words: ['подробно'],
            times: [1.6]
          },
          {
            color: 'custom',
            fill: [0.2, 0.8, 0.4],
            anim: 'left',
            words: ['здесь'],
            times: [1.8]
          }
        ]
      }]
    };
    """

    test_calls = """
    // 1. Кадр tm = 0.5 (до начала интро)
    ipvIntro(0.5);
    assert.strictEqual(ioEl.style.display, 'none');

    // 2. Кадр tm = 1.15 (в процессе анимации reveal первого слова и счетчика)
    ipvIntro(1.15);
    assert.strictEqual(ioEl.style.display, 'flex');
    const spans = ioEl.querySelectorAll('.iline > span');
    assert.strictEqual(spans.length, 5);

    // Первое слово ('1500', счетчик, reveal, tw=1.0)
    const sp0 = spans[0];
    assert.strictEqual(sp0.dataset.origWord, '1500');
    assert.strictEqual(sp0.dataset.isCount, '1');
    const curCnt = parseInt(sp0.textContent, 10);
    assert(curCnt > 0 && curCnt < 1500, 'Счетчик на dt=0.15 должен быть в процессе: ' + curCnt);
    assert(sp0.style.filter.includes('blur'), 'Reveal должен иметь blur: ' + sp0.style.filter);
    assert(sp0.style.transform.includes('scale'), 'Reveal должен иметь scale: ' + sp0.style.transform);

    // Второе слово (tw=1.2, dt = -0.05)
    const sp1 = spans[1];
    assert.strictEqual(sp1.style.opacity, '0');

    // 3. Кадр tm = 1.5 (слово 'СТАРТ', tw=1.4, anim='up', dt=0.1)
    ipvIntro(1.5);
    const sp2 = spans[2];
    assert.strictEqual(sp2.dataset.origWord, 'СТАРТ');
    assert(sp2.style.transform.includes('translateY'), 'Up должен иметь translateY: ' + sp2.style.transform);

    // 4. Кадр tm = 1.7 (слово 'подробно', tw=1.6, anim='glitch', dt=0.1)
    ipvIntro(1.7);
    const sp3 = spans[3];
    assert.strictEqual(sp3.dataset.origWord, 'подробно');
    assert(sp3.style.transform.includes('translate('), 'Glitch должен иметь translate jitter: ' + sp3.style.transform);

    // 5. Кадр tm = 1.9 (слово 'здесь', tw=1.8, anim='left', dt=0.1)
    ipvIntro(1.9);
    const sp4 = spans[4];
    assert.strictEqual(sp4.dataset.origWord, 'здесь');
    assert(sp4.style.transform.includes('translateX'), 'Left должен иметь translateX: ' + sp4.style.transform);

    // 6. Кадр tm = 2.6 (счетчик завершил счет HL_DUR=1.5с)
    ipvIntro(2.6);
    assert.strictEqual(spans[0].textContent, '1500');

    // 7. Кадр tm = 4.8 (фейд-аут за 0.75с до outEnd=5.0)
    ipvIntro(4.8);
    const opOut = parseFloat(ioEl.style.opacity);
    assert(opOut < 1.0 && opOut >= 0.0, 'Фейд-аут должен снижать opacity: ' + opOut);

    console.log("OK: Full DOM intro preview test passed in Node.js");
    """

    full_script = sim_dom + "\n" + fn_code + "\n" + test_calls
    node_file = str(tmp_path / "test_dom.js")
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(full_script)

    res = subprocess.run(["node", node_file], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: Full DOM intro preview test passed in Node.js" in res.stdout


def test_sfx_ev_pop_def_out(xml_subs):
    """События звука pop в плане сцены: при дефолтном стиле out равен None (JS берёт 0.1с)."""
    plan = xml2ae.scene_plan(xml_subs, highlights=[0, 1])
    sfx = plan.get("audio", {}).get("sfx", [])
    pop = next((s for s in sfx if s["kind"] == "pop"), None)
    if pop and pop["events"]:
        ev = pop["events"][0]
        assert ev["out"] is None, "по умолчанию out в плане сцены None (JS берёт 0.1с)"


def test_intro_scale_formula_0968():
    """ipvIntroPos содержит точный коэффициент масштаба прекомпа 0.968 (96.8% из AE)."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js = open(js_path, "r", encoding="utf-8").read()
    assert "0.968*G*" in js or "0.968 * G *" in js or "0.968*G" in js


def test_chip_css_subhl_vars():
    """В static/app.css чипы и разделители (.brk.on) хайлайтов используют CSS-переменную var(--subhl)."""
    css_path = os.path.join(ROOT, "static", "app.css")
    css = open(css_path, "r", encoding="utf-8").read()
    assert ".chip.on{background:var(--subhl,var(--yel))" in css
    assert "color:var(--subhl-tx,#111)" in css
    assert ".brk.on{color:var(--subhl,var(--yel))}" in css
    assert ".ipvintro .iline.yel{color:var(--introhl,var(--subhl,var(--yel)))}" in css


def test_intro_row_color_select_and_text_hl():
    """В introRowHtml в селекторе цвета отображается 'хайлайт', а цвет текста берет CSS-переменную хайлайта."""
    pvw_path = os.path.join(ROOT, "static", "app", "60-preview.js")
    js = open(pvw_path, "r", encoding="utf-8").read()
    assert "+t('хайлайт')+'</option>'" in js
    assert "colVal==='yellow'?'var(--introhl,var(--subhl,var(--yel)))'" in js



def test_ytmusic_deterministic_seed(tmp_path):
    """ytmusic.random_track выбирает трек детерминированно по seed."""
    from core import ytmusic
    m_dir = str(tmp_path / "music")
    os.makedirs(m_dir, exist_ok=True)
    for name in ["track1.m4a", "track2.m4a", "track3.m4a"]:
        with open(os.path.join(m_dir, name), "wb") as f:
            f.write(b"RIFFdummy")
    t_a1 = ytmusic.random_track(m_dir, seed="clip_alpha")
    t_a2 = ytmusic.random_track(m_dir, seed="clip_alpha")
    assert t_a1 == t_a2, "Один и тот же seed обязан возвращать один и тот же трек"
    assert t_a1 is not None and os.path.isfile(t_a1)


@node
def test_node_joined_words_single_container(tmp_path):
    """При склейке слов (одинаковый row) ipvSubs рендерит один .pvsubw со словами через пробел, без наложения."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js_src = open(js_path, "r", encoding="utf-8").read()

    idx_start = js_src.find("function ipvSubs(tm){")
    idx_end = js_src.find("function ipvUI(tm){", idx_start)
    assert idx_start != -1 and idx_end != -1
    subs_fn = js_src[idx_start:idx_end]

    script = r"""
    const assert = require('assert');

    class Element {
      constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.style = { setProperty: () => {}, removeProperty: () => {} };
        this.children = [];
        this.childNodes = [];
        this.dataset = {};
        this._html = '';
        this.classList = {
          _c: new Set(),
          add(c) { this._c.add(c); },
          remove(c) { this._c.delete(c); },
          contains(c) { return this._c.has(c); },
          toggle(c, force) {
            if (force === undefined) force = !this._c.has(c);
            if (force) this._c.add(c); else this._c.delete(c);
            return force;
          }
        };
      }
      appendChild(child) { this.children.push(child); return child; }
      querySelector(sel) {
        if (sel === '.pvsubs_host') return this.children.find(c => c.className === 'pvsubs_host') || null;
        if (sel === '.pvsub_bg') return null;
        return null;
      }
      querySelectorAll(sel) {
        const res = [];
        const scan = (el) => {
          for(const ch of el.children) {
            if(sel.includes('pvsubw') && (ch.className || '').includes('pvsubw')) res.push(ch);
            scan(ch);
          }
        };
        scan(this);
        return res;
      }
      get innerHTML() { return this._html; }
      set innerHTML(h) {
        this._html = h;
        this.children = [];
        const re = /<span class="(pvsubw[^"]*)" style="([^"]*)">([\s\S]*?)<\/span>(?=(?:<span class="pvsubw|$))/g;
        let match;
        while ((match = re.exec(h)) !== null) {
          const el = new Element('span');
          el.className = match[1];
          el.styleStr = match[2];
          el._html = match[3];
          this.children.push(el);
        }
      }
    }

    const hostEl = new Element('div');
    global.$ = (id) => (id === 'ipvsub' ? hostEl : null);
    global.document = { createElement: (tag) => new Element(tag) };
    global.t = (s) => s;
    global.esc = (s) => s;
    global.rgb2hex = () => '#fff';
    global.ipvFontFor = () => null;
    global.FONTS = [];
    global.CURSTYLE = {};
    global.IPVMODE = 'ae';
    global.IPV = {
      plan: {
        w: 1080, h: 1920,
        subs: [
          { s: 1.0, gend: 2.5, w: 'СЛОВО1', color: 'yellow', row: 0 },
          { s: 1.2, gend: 2.5, w: 'СЛОВО2', color: 'yellow', row: 0 }
        ]
      }
    };
    """ + "\n" + subs_fn + r"""
    // Кадр tm = 1.3: оба слова видны и склеены в row=0
    ipvSubs(1.3);
    const subHost = hostEl.querySelector('.pvsubs_host');
    assert(subHost, 'pvsubs_host должен существовать');
    // Должен быть ровно ОДИН контейнер строки .pvsubw для row=0 (нет наложения двух абсолютных span)
    assert.strictEqual(subHost.children.length, 1, 'Должен быть 1 контейнер строки для row=0, а не 2 наложенных: ' + subHost.children.length);
    const rowEl = subHost.children[0];
    assert(rowEl._html.includes('СЛОВО1'), 'Контейнер строки должен содержать СЛОВО1');
    assert(rowEl._html.includes('СЛОВО2'), 'Контейнер строки должен содержать СЛОВО2');

    console.log("OK: Joined words rendered in single container without overlap");
    """
    node_file = str(tmp_path / "test_joined_subs.js")
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)

    res = subprocess.run(["node", node_file], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: Joined words rendered in single container without overlap" in res.stdout


def test_intro_css_interword_gap_scoped_to_line_words():
    """Межсловный отступ не задаётся CSS'ом ВООБЩЕ.

    Прежнее правило .ipvintro .iline>span+span{margin-left:.32em} было задумано как пробел
    МЕЖДУ СЛОВАМИ, но .32em — не метрика шрифта: на строке «и сделать это» оно давало
    +9 % к ширине (537.4 px против 491.0 в AE). Теперь отступ ставит ipvIntro настоящим
    текстовым узлом-пробелом (как пробел шрифта в AE, template.py:614-621), поэтому
    никакого margin-left в правилах .ipvintro быть не должно.
    """
    css_path = os.path.join(ROOT, "static", "app.css")
    css = open(css_path, "r", encoding="utf-8").read()
    for rule in re.findall(r"\.ipvintro[^{}]*\{[^}]*\}", css):
        assert "margin-left" not in rule, \
            f"межсловный отступ обязан жить в ipvIntro (пробел шрифта), а не в CSS: {rule!r}"
        assert "span+span" not in rule, \
            f"селектор span+span давал отступ и между буквами .ich: {rule!r}"


@node
def test_node_glitch_letters_get_no_interword_margin(tmp_path):
    """Межсловный отступ — текстовый узел-пробел МЕЖДУ словами, внутри слова его нет.

    Проверка на эмуляции DOM в node: строка собирается как в ipvIntro (пробел-узел прямым
    ребёнком .iline), буквы .ich из ipvRenderChars пробелов не получают — раньше CSS-правило
    span+span вешало margin-left .32em и на КАЖДУЮ пару букв, и слово в превью растягивалось
    на 143 %, а в AE метрика строки не менялась. Заодно сторожим режим отображения буквы:
    glitch просит обычный inline (там только opacity, а inline-block ломает кернинг), reveal
    по умолчанию — inline-block (буквы масштабируются), пробел внутри слова по-прежнему
    держит white-space:pre. Источник отступа — сам ipvIntro: ровно один createTextNode(' ')
    на каждое слово после первого (CSS-правила .32em в app.css больше нет).
    """
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    js_src = open(js_path, "r", encoding="utf-8").read()
    idx = js_src.find("function ipvRenderChars")
    assert idx >= 0, "ipvRenderChars не найдена в 85-inserts-view.js"
    end = js_src.find("\nfunction ", idx + 10)
    render_fn = js_src[idx:end if end > 0 else len(js_src)]

    # Межсловного отступа в CSS больше нет вовсе (задание «жёлтые строки не тем шрифтом и
    # не тем межсловным отступом»): пробел между словами ставит ipvIntro текстовым узлом.
    idx_intro = js_src.find("function ipvIntro(")
    assert idx_intro >= 0, "ipvIntro не найдена в 85-inserts-view.js"
    end_intro = js_src.find("\nfunction ", idx_intro + 10)
    ipvintro_fn = js_src[idx_intro:end_intro if end_intro > 0 else len(js_src)]

    sim = r"""
    const assert = require('assert');

    // Эмуляция DOM: достаточно классов, родителей, соседей и style-атрибута букв.
    class Element {
      constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.style = {};
        this.dataset = {};
        this.children = [];
        this.parent = null;
        this._classes = new Set();
        this._html = '';
        this._text = '';
        this._display = null;
        this._wsPre = false;
      }
      get className() { return [...this._classes].join(' '); }
      set className(v) { this._classes = new Set((v || '').split(/\s+/).filter(Boolean)); }
      appendChild(ch) { this.children.push(ch); ch.parent = this; return ch; }
      set textContent(v) { this._text = '' + (v != null ? v : ''); this.children = []; }
      set innerHTML(html) {
        this._html = html;
        this.children = [];
        this._text = '';
        if (!html) return;
        const re = /<span\s+class="([^"]+)"\s+style="([^"]*)">([\s\S]*?)<\/span>/g;
        let mm;
        while ((mm = re.exec(html)) !== null) {
          const el = new Element('span');
          el.className = mm[1];
          for (const dec of mm[2].split(';')) {
            const kv = dec.split(':');
            if (kv.length === 2) {
              const k = kv[0].trim();
              const v = kv[1].trim();
              if (k === 'display') el._display = v;
              if (k === 'white-space') el._wsPre = (v === 'pre');
            }
          }
          el.textContent = mm[3];
          this.appendChild(el);
        }
      }
    }

    // Межсловный отступ приезжает текстовым узлом-пробелом, а не CSS'ом: правило
    // .ipvintro .iline>span+span{margin-left:.32em} удалено, и в CSS его быть не должно.
    const IPVINTRO = @FN@;
    """

    checks = r"""
    // 1. Строка из двух слов, собранная как в ipvIntro: пробел-узел МЕЖДУ словами,
    //    буквы слов — без единого пробела внутри.
    const io = new Element('div'); io.className = 'ipvintro';
    const line = new Element('div'); line.className = 'iline';
    io.appendChild(line);
    const w1 = new Element('span'); w1.className = 'iword'; w1.textContent = 'СТАНОВИТЕСЬ';
    const w2 = new Element('span'); w2.className = 'iword'; w2.textContent = 'СЛОВО';
    line.appendChild(w1);
    line.appendChild({ tagName: '#text', textContent: ' ', children: [], parent: null });
    line.appendChild(w2);

    const noop = () => {};
    ipvRenderChars(w1, 'СТАНОВИТЕСЬ', noop, 'inline');  // glitch: только opacity -> inline
    ipvRenderChars(w2, 'СЛОВО', noop);                   // reveal: масштаб -> inline-block по умолчанию

    // Буквы glitch-слова: display inline и никакого межсловного отступа внутри слова.
    assert.strictEqual(w1.children.length, 'СТАНОВИТЕСЬ'.length);
    for (const ch of w1.children) {
      assert(ch._classes.has('ich'), 'буква должна иметь класс ich');
      assert.strictEqual(ch._display, 'inline', 'в глитче букве нужен inline (только opacity), а не inline-block');
    }
    // Буквы reveal-слова: inline-block как было — и тоже без межсловного отступа.
    for (const ch of w2.children) {
      assert.strictEqual(ch._display, 'inline-block', 'reveal по умолчанию остаётся inline-block');
    }

    // 2. Пробел внутри слова по-прежнему держит white-space:pre.
    const line2 = new Element('div'); line2.className = 'iline';
    io.appendChild(line2);
    const w3 = new Element('span'); w3.className = 'iword'; w3.textContent = 'A B';
    line2.appendChild(w3);
    ipvRenderChars(w3, 'A B', noop, 'inline');
    assert.strictEqual(w3.children.length, 3);
    assert.strictEqual(w3.children[1]._wsPre, true, 'пробел внутри слова должен держать white-space:pre');
    assert.strictEqual(w3.children[0]._wsPre, false);
    assert.strictEqual(w3.children[2]._wsPre, false);

    // 3. Пробел-узел приезжает ровно один раз, и только прямым ребёнком строки —
    //    между словами (было: margin-left .32em на span+span, в т.ч. между буквами).
    const isSpace = (n) => n && n.tagName === '#text' && n.textContent === ' ';
    let gapCount = 0;
    for (let i = 1; i < line.children.length; i++) {
      if (isSpace(line.children[i])) gapCount++;
    }
    assert.strictEqual(gapCount, 1, 'пробел шрифта обязан приехать ровно один раз — между словами');
    const inner = [];
    const collect = (el) => {
      for (const c of el.children) { if (isSpace(c)) inner.push(c); collect(c); }
    };
    collect(w1); collect(w2); collect(w3);
    assert.strictEqual(inner.length, 0, 'внутри слова пробелов-узлов быть не должно');

    // 4. Источник отступа — сам ipvIntro: один createTextNode на каждое слово после первого.
    const src = IPVINTRO;
    assert.strictEqual((src.match(/createTextNode\(' '\)/g) || []).length, 1,
      'ipvIntro обязан ставить пробел между словами ровно одним createTextNode');
    assert(/if\(wi>0\)/.test(src), 'пробел ставится перед каждым словом, кроме первого');

    console.log("OK: glitch/reveal letters get no inter-letter gaps");
    """

    full_script = (sim.replace("@FN@", json.dumps(ipvintro_fn)) + "\n" + render_fn
                   + "\n" + checks)
    node_file = str(tmp_path / "test_chars_gap.js")
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(full_script)

    res = subprocess.run(["node", node_file], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: glitch/reveal letters get no inter-letter gaps" in res.stdout

