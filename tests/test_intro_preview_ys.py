# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Превью ставит строки интро по готовым y из плана, а не CSS-потоком (задание A2).

Задание A1 положило в группу плана `ys` — y базовой линии каждой строки в координатах
прекомпа высотой `plan.h`. Шаг строк зависит от шрифта и якоря, CSS-поток (flex-колонка
+ line-height + ручные marginTop у back) его не повторяет: превью врало о высоте.

Проверяет:
  * `introGroupWindows` протаскивает `ys` группы из плана;
  * `ipvIntro` при `ys` той же длины, что строки, кладёт каждую `.iline` абсолютно:
    по горизонтали по центру блока (класс `.abs`), по вертикали — базовой линией на
    `(ys[li] - plan.h/2) * k` от центра контейнера `#ipvintro`, `k = clientWidth / plan.w`;
    смещение базовой линии от верха строки — замером (пустой inline-block, как в
    ipvCaption), причём из ВЁРСТКИ (`offsetTop`/`offsetHeight`), а не из
    `getBoundingClientRect`: у блока есть `transform: scale(...)`, и прямоугольник
    приходит в экранных пикселях — тогда строка уезжает на «масштаб блока» раз
    (дефект приёмки A2, задание A2b);
  * без `ys` (старый бэкенд) — прежний CSS-поток с ручными `marginTop` у back.

Числа для сверки (эмуляция в node, контейнер 540×960, plan 1080×1920, замер 20 px):
k = 540/1080 = 0.5, центр = 960/2 = 480; строка 0: 480 + (900-960)*0.5 - 20 = 430;
строка 1: 480 + (972-960)*0.5 - 20 = 466. Прямоугольники в эмуляции отдаются с
масштабом 0.5 (как при `transform: scale(0.5)` блока) — замер по ним дал бы 440 и 476,
так что эти числа держат посадку строк независимо от масштаба блока.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

JS_REL = ("static", "app", "85-inserts-view.js")


def _run_node(tmp_path, name, script):
    node_file = str(tmp_path / name)
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)
    return subprocess.run(["node", node_file], capture_output=True, text=True, encoding="utf-8")


def _js_src():
    return open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()


def _ipvintro_region(js_src):
    """Кусок исходника от aeEase до ipvIntroPos — в нём живут и ipvIntro, и introGroupWindows."""
    idx_ae = js_src.find("function aeEase")
    idx_pos = js_src.find("function ipvIntroPos(")
    assert idx_ae >= 0 and idx_pos > idx_ae, "не нашлись aeEase/ipvIntroPos в 85-inserts-view.js"
    return js_src[idx_ae:idx_pos]


def _ys_block(js_src):
    """Блок посадки строк по ys внутри ipvIntro — от `if(ys){` до парной закрывающей скобки."""
    region = _ipvintro_region(js_src)
    idx = region.find("if(ys){")
    assert idx >= 0, "не нашёлся блок посадки строк по ys (`if(ys){`) в ipvIntro"
    depth = 0
    for i in range(idx, len(region)):
        ch = region[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return region[idx:i + 1]
    raise AssertionError("блок посадки строк по ys не закрылся")


# Эмуляция DOM: ровно те методы, что трогает ipvIntro. Контейнер #ipvintro — 540×960
# (k = 540/1080 = 0.5, центр по высоте 480), замер базовой линии — 20 px от верха строки.
DOM_SIM = r"""
const assert = require('assert');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.style = { setProperty(){}, removeProperty(){} };
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
    this.parent = null;
    this._text = '';
    // Вёрстка (offsetTop/offsetHeight) — в НЕтрансформированных пикселях: опора базовой
    // линии (i нулевой высоты) стоит на 20 px ниже верха строки, как inline-block с
    // height:0 и vertical-align:baseline. Именно эти 20 px и обязан мерить ipvIntro.
    this.offsetTop = (this.tagName === 'I') ? 20 : 0;
    this.offsetHeight = 0;
    // Экранные прямоугольники приходят уже с transform: scale(0.5) блока #ipvintro
    // (масштаб группы × зум камеры): 20 px вёрстки — это 10 px экрана, поэтому замер
    // через getBoundingClientRect дал бы baseOff = 10 вместо 20 и сдвинул строку на 10.
    // Ставим опоре внутри строки (top строки 0 по экрану) именно такой масштаб.
    this._rect = (this.tagName === 'I') ? { top: 10, bottom: 10 } : { top: 0, bottom: 16.5 };
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
  get textContent() {
    if (this._text !== '' && this._text !== undefined) return this._text;
    return this.children.map(c => c.textContent).join('');
  }
  set textContent(v) { this._text = '' + (v != null ? v : ''); this.children = []; }
  get innerHTML() { return this._html || ''; }
  set innerHTML(html) { this._html = html; this.children = []; this._text = ''; }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  remove() {
    if (this.parent) {
      const i = this.parent.children.indexOf(this);
      if (i >= 0) this.parent.children.splice(i, 1);
      this.parent = null;
    }
  }
  getBoundingClientRect() { return this._rect; }
  querySelectorAll(sel) {
    const res = [];
    if (sel === '.iline') {
      for (const ch of this.children) { if (ch._classes && ch._classes.has('iline')) res.push(ch); }
      return res;
    }
    if (sel === '.iline > span') {
      for (const line of this.children) {
        for (const sp of line.children) { if (sp.tagName === 'SPAN') res.push(sp); }
      }
      return res;
    }
    return res;
  }
}

const ioEl = new Element('div');
ioEl.clientWidth = 540;
ioEl.clientHeight = 960;
global.$ = (id) => (id === 'ipvintro' ? ioEl : null);
global.document = {
  createElement: (tag) => new Element(tag),
  // Текстовый узел-пробел между словами (ipvIntro): tagName '#text', в селекторы
  // .iline>span не попадает — как настоящий текстовый узел в браузере.
  createTextNode: (txt) => ({ tagName: '#text', textContent: '' + txt, children: [] })
};
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
  plan: { w: 1080, h: 1920, fsize: 48 },
  intro: @INTRO@
};
"""


def _dom_script(js_src, intro_groups, checks):
    """Собирает скрипт для node: эмуляция DOM + кусок 85-inserts-view.js + проверки."""
    sim = DOM_SIM.replace("@INTRO@", json.dumps(intro_groups, ensure_ascii=False))
    return sim + "\n" + _ipvintro_region(js_src) + "\n" + checks


@node
def test_node_intro_lines_sit_on_plan_ys(tmp_path):
    """ys из плана: строки абсолютные, top = центр контейнера + (ys - plan.h/2)*k - базовая
    линия из вёрстки (offsetTop), независимо от масштаба блока (дефект A2b)."""
    intro = [{
        "inAt": 1.0, "outEnd": 5.0, "fade": 0.75, "front": False,
        "ys": [900, 972],
        "lines": [
            {"color": "yellow", "anim": "reveal", "words": ["1500", "человек"], "times": [1.0, 1.2]},
            {"color": "white", "back": True, "words": ["подробно"], "times": [1.4]},
        ],
    }]
    checks = r"""
    ipvIntro(1.5);
    const lines = ioEl.querySelectorAll('.iline');
    assert.strictEqual(lines.length, 2, 'строк должно быть две: ' + lines.length);

    // 1. Каждая строка — абсолютная (.abs): вертикаль задаёт y из плана, а не CSS-поток.
    for (const ln of lines) {
      assert(ln._classes.has('abs'), 'строка без класса абсолютной строки: ' + ln.className);
    }
    assert.strictEqual(lines[0].className.indexOf('back'), -1);
    assert(lines[1]._classes.has('back'), 'вторая строка обязана остаться back: ' + lines[1].className);

    // 2. Базовая линия — на y из плана: центр контейнера 480, k = 0.5, замер 20.
    // Самопроверка эмуляции (дефект A2b): вёрстка даёт 20, а экранный прямоугольник —
    // 10 (масштаб блока 0.5). Мерить его нельзя: top уехал бы на 440/476.
    const probe = document.createElement('i');
    probe.className = 'pvcap_strut';
    assert.strictEqual(probe.offsetTop + probe.offsetHeight, 20,
      'эмуляция: опора базовой линии обязана стоять на 20 px вёрстки');
    assert.strictEqual(probe.getBoundingClientRect().bottom, 10,
      'эмуляция: экранный прямоугольник обязан быть в масштабе 0.5, как при transform:scale(.5)');
    const t0 = parseFloat(lines[0].style.top);
    assert.strictEqual(lines[0].style.top, '430.00px',
      'первая строка: 480 + (900-960)*0.5 - 20 = 430, получено ' + lines[0].style.top);
    const t1 = parseFloat(lines[1].style.top);
    assert.strictEqual(lines[1].style.top, '466.00px',
      'вторая строка: 480 + (972-960)*0.5 - 20 = 466, получено ' + lines[1].style.top);
    assert(t1 > t0, 'строка 972 стоит НИЖЕ строки 900');
    assert.strictEqual(t1 - t0, 36, 'шаг строк = (972-900)*0.5 при k = 0.5');

    // 3. В ветке ys ручные marginTop у back не ставим — вертикаль целиком из плана.
    assert(!lines[1].style.marginTop, 'ручной marginTop в ветке ys: ' + lines[1].style.marginTop);

    // 4. Опора базовой линии — это замер, из строки она убрана.
    for (const ln of lines) {
      assert(!ln.children.some(c => c._classes && c._classes.has('pvcap_strut')),
        'pvcap_strut остался в строке (замер утёк в разметку)');
    }
    // 5. Ручка масштаба по-прежнему в последней строке — под ней.
    assert(lines[1].children.some(c => c._classes && c._classes.has('intro-scale-handle')),
      'ручка масштаба уехала из последней строки');

    console.log("OK: intro lines sit on plan ys");
    """
    res = _run_node(tmp_path, "test_intro_ys.js", _dom_script(_js_src(), intro, checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: intro lines sit on plan ys" in res.stdout


@node
def test_node_intro_without_ys_keeps_css_flow(tmp_path):
    """Старый бэкенд без ys: классов .abs нет, ручные marginTop у back — как раньше."""
    intro = [{
        "inAt": 1.0, "outEnd": 5.0, "fade": 0.75, "front": False,
        "lines": [
            {"color": "yellow", "words": ["СТАРТ"], "times": [1.0]},
            {"color": "white", "back": True, "words": ["подробно"], "times": [1.2]},
        ],
    }]
    checks = r"""
    ipvIntro(1.5);
    const lines = ioEl.querySelectorAll('.iline');
    assert.strictEqual(lines.length, 2, 'строк должно быть две: ' + lines.length);

    for (const ln of lines) {
      assert(!ln._classes.has('abs'),
        'без ys строка обязана остаться в CSS-потоке: ' + ln.className);
      assert.strictEqual(ln.style.top, undefined, 'без ys top не ставится: ' + ln.style.top);
    }
    assert.strictEqual(lines[1].style.marginTop, '-0.3em',
      'marginTop у back после обычной строки: ' + lines[1].style.marginTop);

    console.log("OK: intro without ys keeps css flow");
    """
    res = _run_node(tmp_path, "test_intro_no_ys.js", _dom_script(_js_src(), intro, checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: intro without ys keeps css flow" in res.stdout


@node
def test_node_intro_group_windows_pass_ys(tmp_path):
    """Сторож: introGroupWindows протаскивает ys группы из плана (без него ветка .abs мертва)."""
    js_src = _js_src()
    idx = js_src.find("function introGroupWindows(")
    assert idx >= 0, "introGroupWindows не найдена в 85-inserts-view.js"
    end = js_src.find("\nfunction ", idx + 10)
    win_fn = js_src[idx:end if end > 0 else len(js_src)]

    checks = r"""
    const plan = [{
      ts: 1.0, te: 5.0, fade: 0.75, front: true,
      shadow: { fill: [1, 1, 1], op: 68 }, fonts: ['A', 'B'],
      ys: [900, 972],
      lines: [{ words: ['A'] }, { words: ['B'] }]
    }];
    const out = introGroupWindows(plan);
    assert.strictEqual(out.length, 1, 'окно группы потерялось');
    assert.deepStrictEqual(out[0].ys, [900, 972], 'ys не протащен из плана: ' + JSON.stringify(out[0].ys));
    assert.strictEqual(out[0].inAt, 1.0);
    assert.strictEqual(out[0].outEnd, 5.0);

    // Группа без ts/te (панель шага 2) окна не даёт — как и раньше, ys её не оживляет.
    assert.strictEqual(introGroupWindows([{ lines: [{ words: ['A'] }], ys: [900] }]).length, 0,
      'группа без ts/te не должна давать окно');

    console.log("OK: introGroupWindows passes ys");
    """
    script = "const assert = require('assert');\n" + win_fn + "\n" + checks
    res = _run_node(tmp_path, "test_intro_win_ys.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: introGroupWindows passes ys" in res.stdout


def test_ys_block_measures_baseline_from_layout():
    """Сторож дефекта A2: посадка строк по ys меряет вёрстку, а не экранный прямоугольник.

    Эмуляция отдаёт getBoundingClientRect с масштабом 0.5, так что замер по прямоугольнику
    дал бы top 440/476 вместо 430/466 (см. test_node_intro_lines_sit_on_plan_ys).
    """
    block = _ys_block(_js_src())
    assert "offsetTop" in block, "в посадке строк по ys нет offsetTop (замер не из вёрстки)"
    assert "offsetHeight" in block, "в посадке строк по ys нет offsetHeight"
    assert "getBoundingClientRect" not in block, (
        "в посадке строк по ys остался getBoundingClientRect: transform блока отдаёт "
        "экранные пиксели и baseOff ошибается в «масштаб блока» раз")


def test_css_has_abs_intro_line():
    """В static/app.css есть класс абсолютной строки интро; прежние правила .iline не тронуты."""
    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()
    m = re.search(r"\.ipvintro \.iline\.abs\{([^}]*)\}", css)
    assert m, "нет правила .ipvintro .iline.abs в app.css"
    rule = m.group(1)
    assert "position:absolute" in rule, "строка .abs осталась в потоке: " + rule
    assert "left:50%" in rule and "translateX(-50%)" in rule, "строка .abs не по центру блока: " + rule
    assert "top:0" in rule, "верх строки .abs ставит ipvIntro, в CSS должен быть top:0: " + rule
    # ручка масштаба остаётся под последней строкой: .iline по-прежнему позиционирован
    iline = re.search(r"\.ipvintro \.iline\{([^}]*)\}", css)
    assert iline and "position:relative" in iline.group(1), (
        "у .iline нет position:relative — ручка позиционируется не от строки")
