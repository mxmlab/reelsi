# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Журнал вызовов canvas у двух полос времени: редактор нарезки (edDraw) и «Вся запись» (podRenderTl).

Стенд: поддельный контекст canvas записывает каждый вызов и каждое присваивание свойства
(fillRect, strokeRect, fillText, fillStyle, strokeStyle, font, lineWidth…). Данные фиксированные.

Фикстура tests/fixtures/tl_journal_head_ce2e688a.json — журнал ДО общих функций таймлайнов
(коммит ce2e688a, перед tlPaintRuler/tlDrawSpan/tlPaintWave). Критерий «без смены вида»:
- «Вся запись» (podRenderTl) — журнал побайтно тот же;
- редактор (edDraw) — тот же журнал, кроме волны: её меняет намеренно tlPaintWave (максимум пиков);
  волна сверяется с журналом tlPaintWave на тех же параметрах.

Запуск: py -3.10 -m pytest tests/test_tl_canvas_journal.py -q
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "static" / "app"
FIXTURE = HERE / "fixtures" / "tl_journal_head_ce2e688a.json"

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Журнал canvas: [имя, аргументы] — вызовы; ['set:имя', значение] — присваивания.
STUB = r"""
var __log = [];
function __fmt(v){ return String(v); }
function __mkCtx(){
  return new Proxy({}, {
    get(t, p){
      if(typeof p !== 'string') return undefined;
      if(p === 'measureText') return (s) => ({width: String(s).length * 6});
      return (...args) => { __log.push([p, args.map(__fmt)]); return undefined; };
    },
    set(t, p, v){ __log.push(['set:' + String(p), __fmt(v)]); return true; }
  });
}
function __mkCanvas(cw, ch){
  const ctx = __mkCtx();
  return {width: cw, height: ch, clientWidth: cw, clientHeight: ch, style: {},
    getContext(){ return ctx; }, getBoundingClientRect(){ return {left: 0, top: 0, width: cw, height: ch}; }};
}
const __ids = {};
const document = {
  documentElement: {setAttribute(){}}, body: {},
  addEventListener(){}, removeEventListener(){},
  getElementById(id){ return __ids[id] || null; }
};
const window = {addEventListener(){}, removeEventListener(){}};
globalThis.window = window;
globalThis.document = document;
globalThis.devicePixelRatio = 1;
globalThis.localStorage = {getItem(){return null;}, setItem(){}};
globalThis.getComputedStyle = () => ({getPropertyValue(){ return ''; }});
"""

# Фиксированные данные: пики (LCG), блоки/куски те же, окно 5..70 с, курсор 40 с.
BODY = r"""
let __seed = 12345;
function __rnd(){ __seed = (__seed * 1103515245 + 12345) & 0x7fffffff; return __seed / 0x7fffffff; }
const __PK = [];
for(let i = 0; i < 5200; i++){ __PK.push(Math.round(__rnd() * 1000) / 1000); }
const __SEGS = [{s: 0, e: 20}, {s: 30, e: 55}, {s: 60, e: 90}];
ED.dur = 100; ED.v0 = 5; ED.v1 = 70; ED.cs = 40; ED.peaks = __PK; ED.pps = 50; ED.br = []; ED.brBand = 0; ED.cuts = [];
ED.blocks = __SEGS.map(x => ({s0: x.s, s1: x.e})); ED.sel = 1; ED.fps = 60;
__ids.edtl = __mkCanvas(400, 120);
__log = [];
edDraw();
const __edJournal = __log;
// опорный журнал волны: tlPaintWave на тех же параметрах и тех же цветах, что у редактора
let __bi = 0;
__ids.ref = __mkCanvas(400, 120);
__log = [];
tlPaintWave(__ids.ref.getContext('2d'), {W: 400, H: 120, top: 18, dpr: 1, v0: 5, v1: 70, dur: 100, peaks: __PK, pps: 50,
  colorAt: s => {
    while(__bi < ED.blocks.length && s >= ED.blocks[__bi].s1) __bi++;
    const inb = __bi < ED.blocks.length && s >= ED.blocks[__bi].s0;
    return inb ? ((__bi === ED.sel) ? TL_COL.waveSel : TL_COL.wave) : TL_COL.waveOff;}});
const __waveRef = __log;
POD.dur = 100; POD.v0 = 5; POD.v1 = 70; POD.peaks = __PK; POD.peaksPps = 50;
POD.segs = __SEGS.map((x, i) => ({id: 'seg' + i, start: x.s, end: x.e, title: '', why: '', reel: ''}));
POD.picked = ['seg1']; POD.sel = 'seg1';
POD.words = [{w: 'alpha', start: 31, end: 33}, {w: 'beta', start: 33.2, end: 40}, {w: 'gamma', start: 40.1, end: 40.4}, {w: 'delta', start: 61, end: 70}];
__ids.pod_video = {currentTime: 40, paused: true};
__ids.pod_tl_cv = __mkCanvas(400, 120);
__log = [];
podRenderTl();
const __podJournal = __log;
console.log(JSON.stringify({edDraw: __edJournal, podRenderTl: __podJournal, waveRef: __waveRef}));
"""


def _read(path: Path) -> str:
    with io.open(str(path), encoding="utf-8") as f:
        return f.read()


def _journals(tmp_path: Path) -> dict:
    """Журналы двух полос на текущем коде: 00-core + редактор + подкаст + стенд."""
    code = "\n".join([STUB, _read(APP / "00-core.js"), _read(APP / "70-editor.js"),
                      _read(APP / "46-podcast-editor.js"), BODY])
    path = tmp_path / "tl_journal_check.js"
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(code)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def _split_wave(journal: list) -> tuple:
    """Журнал полосы → (голова, волна, хвост). Голова — до заливки фона включительно; волна —
    вызовы до первой заливки блока (присваивание fillStyle перед ней уходит в хвост)."""
    bg = next(i for i, x in enumerate(journal) if x[0] == "fillRect")
    nxt = next(i for i in range(bg + 1, len(journal)) if journal[i][0] == "fillRect")
    end = nxt - 1 if journal[nxt - 1][0] == "set:fillStyle" else nxt
    return journal[:bg + 1], journal[bg + 1:end], journal[end:]


@node
def test_podcast_journal_is_byte_identical_to_the_head_before_the_shared_helpers(tmp_path):
    """«Вся запись» рисуется как до общих функций: ruler, рамки кусков, волна — тот же журнал."""
    now = _journals(tmp_path)["podRenderTl"]
    before = json.loads(_read(FIXTURE))["podRenderTl"]
    assert len(now) == len(before), f"число вызовов canvas изменилось: {len(before)} → {len(now)}"
    assert now == before, "журнал «Вся запись» отличается от фикстуры ce2e688a"


@node
def test_editor_journal_matches_the_head_except_the_wave(tmp_path):
    """Редактор: всё вокруг волны (фон, блоки, вздохи, линейка, плейхед) — как до общих функций.

    Волну меняет намеренно tlPaintWave (максимум пиков), её сверяем с журналом tlPaintWave.
    """
    journals = _journals(tmp_path)
    before = json.loads(_read(FIXTURE))["edDraw"]
    head_b, wave_b, tail_b = _split_wave(before)
    head_n, wave_n, tail_n = _split_wave(journals["edDraw"])
    assert head_n == head_b, "голова журнала редактора (очистка и фон) изменилась"
    assert tail_n == tail_b, "хвост журнала редактора (блоки, линейка, плейхед) изменился"
    assert wave_n == journals["waveRef"], (
        "волна редактора не совпала с журналом tlPaintWave на тех же пиках и цветах")
    assert len(wave_n) > 0 and wave_n != wave_b, "волна не перерисована через tlPaintWave"


@node
def test_ruler_and_span_frames_are_the_one_drawing_of_three_timelines(tmp_path):
    """Линейка и рамки — через общие tlPaintRuler/tlDrawSpan (сторож на вызов, не на журнал)."""
    src = _read(APP / "70-editor.js") + _read(APP / "46-podcast-editor.js")
    core = _read(APP / "00-core.js")
    assert src.count("tlPaintRuler(g,") == 2, "линейку редактора и подкаста рисует не общая функция"
    assert src.count("tlDrawSpan(g,") == 2, "рамки редактора и подкаста рисует не общая функция"
    assert "function tlPaintRuler(" in core and "function tlDrawSpan(" in core
