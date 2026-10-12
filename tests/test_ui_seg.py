# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Переключатели .seg: подсветка выбора переезжает за выбором (давний баг «Камер»).

Баг: шаг 1, «Камер 1 2 3 4» — выбрал «3», число камер применилось, а белая подсветка
осталась на «2». setMode() в успешной ветке не звал segUI(). Теперь подсветку ставит один
делегированный обработчик change в 00-core.js, а программные смены .checked зовут segUI() сами.

Сценарии исполняются боевыми 00-core.js и 40-queue.js в node (vm) на заглушке DOM с .seg из
label > input[type=radio]. Заглушка узнаёт только то, что нужно этим обработчикам.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "static", "app")
CORE = os.path.join(APP, "00-core.js")
QUEUE = os.path.join(APP, "40-queue.js")
BOOT = os.path.join(APP, "99-boot.js")

needs_node = pytest.mark.skipif(
    not shutil.which("node"), reason="сценарии исполняют боевой код под node")

HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const [CORE, QUEUE] = process.argv.slice(2);

// ---- заглушка DOM: селекторы только такие, как в боевом коде .seg ----
function compound(s) {
  const c = { tag: null, cls: [], attrs: [], checked: false };
  const re = /([a-zA-Z][\w-]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|([^\]]*)))?\]|(:checked)/y;
  let i = 0;
  while (i < s.length) {
    re.lastIndex = i;
    const m = re.exec(s);
    if (!m) throw new Error('селектор не разобран: ' + s);
    i = re.lastIndex;
    if (m[1]) c.tag = m[1].toUpperCase();
    else if (m[2]) c.cls.push(m[2]);
    else if (m[3]) c.attrs.push(['id', m[3]]);
    else if (m[4]) c.attrs.push([m[4], m[5] !== undefined ? m[5] : m[6]]);
    else c.checked = true;
  }
  return c;
}
function matches(el, c) {
  if (c.tag && el.tagName !== c.tag) return false;
  for (const k of c.cls) if (!el.classList.contains(k)) return false;
  for (const [k, v] of c.attrs) {
    const have = el.getAttribute(k);
    if (have === null) return false;
    if (v !== undefined && have !== v) return false;
  }
  if (c.checked && !el.checked) return false;
  return true;
}
function selectAll(scope, sel) {
  const parts = sel.trim().split(/\s+/).map(compound);
  const last = parts[parts.length - 1];
  const out = [];
  for (const el of scope.walk()) {
    if (!matches(el, last)) continue;
    let idx = parts.length - 2;
    for (let a = el.parentNode; a && idx >= 0; a = a.parentNode) if (matches(a, parts[idx])) idx--;
    if (idx < 0) out.push(el);
  }
  return out;
}
class El {
  constructor(tag, doc, attrs) {
    this.tagName = tag.toUpperCase();
    this.nodeType = 1;
    this.doc = doc;
    this.attrs = Object.assign({}, attrs);
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this._cls = new Set((this.attrs.class || '').split(/\s+/).filter(Boolean));
    this._checked = this.attrs.checked !== undefined;
  }
  get classList() {
    const s = this._cls;
    return {
      contains: c => s.has(c),
      add: c => { s.add(c); },
      remove: c => { s.delete(c); },
      toggle: (c, force) => {
        const on = force === undefined ? !s.has(c) : !!force;
        if (on) s.add(c); else s.delete(c);
        return on;
      },
    };
  }
  get checked() { return this._checked; }
  set checked(v) {
    this._checked = !!v;
    // радио одной группы: новый выбор снимает прежний, как в браузере
    if (v && this.attrs.type === 'radio') {
      for (const o of this.doc.allEls()) {
        if (o !== this && o.attrs.type === 'radio' && o.attrs.name === this.attrs.name) o._checked = false;
      }
    }
  }
  get value() { return this.attrs.value !== undefined ? this.attrs.value : ''; }
  getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; }
  add(tag, attrs = {}) {
    const e = new El(tag, this.doc, attrs);
    e.parentNode = this;
    this.children.push(e);
    return e;
  }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
  querySelectorAll(sel) { return selectAll(this, sel); }
  querySelector(sel) { return selectAll(this, sel)[0] || null; }
  closest(sel) {
    const c = compound(sel);
    for (let n = this; n; n = n.parentNode) if (matches(n, c)) return n;
    return null;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
}
class Doc {
  constructor() {
    this.listeners = {};
    this.root = new El('html', this, {});
    this.body = this.root.add('body');
    this.documentElement = this.root;
  }
  allEls() { return [...this.root.walk()]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  querySelectorAll(sel) { return selectAll(this.root, sel); }
  querySelector(sel) { return selectAll(this.root, sel)[0] || null; }
  getElementById(id) { return selectAll(this.root, '#' + id)[0] || null; }
}

// ---- события: inline on<type> отрабатывает у цели, потом всплытие по listeners ----
const pending = [];
function fire(ctx, target, type) {
  const evt = { type, target, preventDefault() {}, stopPropagation() {} };
  for (let n = target; n; n = n.parentNode) {
    const code = n.attrs['on' + type];
    if (code) {
      const r = vm.runInContext(code, ctx);
      if (r && typeof r.then === 'function') pending.push(r);
    }
    for (const fn of n.listeners[type] || []) fn.call(n, evt);
  }
  for (const fn of ctx.document.listeners[type] || []) fn.call(ctx.document, evt);
}

const doc = new Doc();
let ASK = false, ASKED = 0, SAVED = 0;
const ctx = vm.createContext({
  document: doc,
  window: { addEventListener() {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  console, setTimeout, clearTimeout, Promise,
  // из файлов, которых тут нет (50-chrome, 99-boot), — заглушки
  askConfirm: async () => { ASKED++; return ASK; },
  saveState: () => { SAVED++; },
  BUILT: 0,
});
vm.runInContext(fs.readFileSync(CORE, 'utf8'), ctx, { filename: 'core' });
vm.runInContext(fs.readFileSync(QUEUE, 'utf8'), ctx, { filename: 'queue' });
// buildCamRows из 40-queue трогает DOM строк камер — в сценарии нужна только сама отметка
vm.runInContext('buildCamRows = () => { BUILT++; };', ctx);

const labelsOn = seg => seg.children.map(l => l.classList.contains('on'));
const checkedVals = seg => seg.children.map(l => l.children[0]).filter(i => i.checked).map(i => i.value);
const inputOf = (seg, k) => seg.children[k].children[0];
function reset() { doc.body.children.length = 0; SAVED = 0; ASKED = 0; ctx.BUILT = 0; }
function setState(js) { vm.runInContext(js, ctx); }
// QUEUE и LASTCAMS в 40-queue.js — let-привязки скрипта, на ctx их нет: читаем через vm
const readG = name => vm.runInContext(name, ctx);

// .seg с тремя пунктами, без inline-обработчиков: подсветку может поставить только общий путь
function buildPlain(cur) {
  const seg = doc.body.add('div', { class: 'seg' });
  ['a', 'b', 'c'].forEach(v => {
    const lb = seg.add('label', { class: v === cur ? 'on' : '' });
    lb.add('input', { type: 'radio', name: 'g', value: v, ...(v === cur ? { checked: '' } : {}) });
  });
  return seg;
}
// «Камер 1 2 3 4» как в index.html: onchange="setMode()"
function buildCams(cur) {
  const seg = doc.body.add('div', { class: 'seg' });
  ['1', '2', '3', '4'].forEach(v => {
    const lb = seg.add('label', { class: v === cur ? 'on' : '' });
    lb.add('input', { type: 'radio', name: 'cams', value: v, onchange: 'setMode()',
      ...(v === cur ? { checked: '' } : {}) });
  });
  return seg;
}

(async () => {
  const out = {};

  reset();
  {
    const seg = buildPlain('b');
    inputOf(seg, 2).checked = true;
    fire(ctx, inputOf(seg, 2), 'change');
    out.plain_change = labelsOn(seg);
  }

  reset();
  {
    // программная смена .checked change не шлёт: подсветку должен ставить вызывающий
    const seg = buildPlain('b');
    inputOf(seg, 0).checked = true;
    out.plain_programmatic = labelsOn(seg);
  }

  reset();
  {
    setState('QUEUE=[]; LASTCAMS=2;');
    const seg = buildCams('2');
    const i3 = inputOf(seg, 2);
    i3.checked = true;
    fire(ctx, i3, 'change');
    await Promise.all(pending.splice(0));
    out.cams_success = {
      on: labelsOn(seg), checked: checkedVals(seg),
      LASTCAMS: readG('LASTCAMS'), built: ctx.BUILT, saved: SAVED, asked: ASKED,
    };
  }

  reset();
  {
    // очередь с парой не-рилсов: смена числа камер спрашивает и при отказе откатывает выбор
    setState("QUEUE=[['clip1','clip2']]; LASTCAMS=2;");
    ASK = false;
    const seg = buildCams('2');
    const i3 = inputOf(seg, 2);
    i3.checked = true;
    fire(ctx, i3, 'change');
    const mid = labelsOn(seg);
    await Promise.all(pending.splice(0));
    out.cams_cancel = {
      mid, on: labelsOn(seg), checked: checkedVals(seg),
      LASTCAMS: readG('LASTCAMS'), queue: readG('QUEUE.length'), built: ctx.BUILT, saved: SAVED, asked: ASKED,
    };
  }

  reset();
  {
    setState("QUEUE=[['clip1','clip2']]; LASTCAMS=2;");
    ASK = true;
    const seg = buildCams('2');
    const i3 = inputOf(seg, 2);
    i3.checked = true;
    fire(ctx, i3, 'change');
    await Promise.all(pending.splice(0));
    out.cams_confirm = {
      on: labelsOn(seg), checked: checkedVals(seg),
      LASTCAMS: readG('LASTCAMS'), queue: readG('QUEUE.length'), built: ctx.BUILT, saved: SAVED, asked: ASKED,
    };
  }

  console.log(JSON.stringify(out));
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
"""


@pytest.fixture(scope="module")
def seg_result(tmp_path_factory):
    d = tmp_path_factory.mktemp("seg")
    harness = d / "seg_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    p = subprocess.run(["node", str(harness), CORE, QUEUE], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:800]
    return json.loads(p.stdout)


@needs_node
def test_plain_seg_highlight_moves_with_change(seg_result):
    """change на втором пункте переставляет on только на него — у любого .seg, без inline-onchange."""
    assert seg_result["plain_change"] == [False, False, True]


@needs_node
def test_programmatic_check_does_not_move_highlight(seg_result):
    """Программная смена .checked change не шлёт: подсветку такие места ставят сами (segUI)."""
    assert seg_result["plain_programmatic"] == [False, True, False]


@needs_node
def test_cams_success_moves_highlight_to_new_count(seg_result):
    """Выбор «3» в «Камер»: число применилось и подсветка на 3 (давний баг: оставалась на 2)."""
    r = seg_result["cams_success"]
    assert r["on"] == [False, False, True, False], "подсветка не переехала на выбранное число"
    assert r["checked"] == ["3"]
    assert r["LASTCAMS"] == 3 and r["built"] == 1 and r["saved"] == 1


@needs_node
def test_cams_cancel_restores_old_highlight(seg_result):
    """Отказ от смены с очисткой очереди: радио и подсветка возвращаются на прежнее «2»."""
    r = seg_result["cams_cancel"]
    assert r["mid"] == [False, False, True, False], "во время вопроса выбор и подсветка расходятся"
    assert r["on"] == [False, True, False, False]
    assert r["checked"] == ["2"]
    assert r["LASTCAMS"] == 2 and r["queue"] == 1 and r["built"] == 0 and r["saved"] == 0
    assert r["asked"] == 1


@needs_node
def test_cams_confirm_moves_highlight_and_clears_pairs(seg_result):
    """Подтверждение очистки: очередь без не-рилсов, подсветка на новом числе."""
    r = seg_result["cams_confirm"]
    assert r["on"] == [False, False, True, False]
    assert r["checked"] == ["3"]
    assert r["LASTCAMS"] == 3 and r["queue"] == 0 and r["built"] == 1 and r["saved"] == 1


def test_delegated_change_handler_sits_next_to_segui():
    """Общий обработчик — в 00-core.js рядом с segUI, а не разрозненно в разметке."""
    src = open(CORE, encoding="utf-8").read()
    assert re.search(r"^function segUI\(\)", src, re.M)
    assert re.search(
        r"document\.addEventListener\('change',e=>\{if\(e\.target&&e\.target\.closest&&"
        r"e\.target\.closest\('\.seg'\)\)segUI\(\);\}\);", src), \
        "делегированного change-обработчика для .seg нет в 00-core.js"


def test_restored_cams_refreshes_highlight_on_boot():
    """Восстановление числа камер из состояния — программная смена; подсветку ставит applyState."""
    src = open(BOOT, encoding="utf-8").read()
    line = next(l for l in src.splitlines() if "s.cams" in l and "rb" in l)
    assert "segUI()" in line, "восстановление cams не обновляет подсветку .seg"
