# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие функции трёх таймлайнов (00-core.js) и их подключение в редакторе, подкасте и вставках.

Три полосы времени — редактор нарезки (70-editor.js), «Вся запись» (46-podcast-editor.js) и
превью вставок (85-inserts-view.js) — ведут одну механику через 00-core.js. Здесь гоняем её
под node: сами функции — настоящие, из файла; DOM — заглушка, которая записывает присваивания.

Запуск: py -3.10 -m pytest tests/test_tl_common_js.py -q
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "static" / "app"
CORE_JS = APP / "00-core.js"
EDITOR_JS = APP / "70-editor.js"
PODCAST_JS = APP / "46-podcast-editor.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: Path) -> str:
    with io.open(str(path), encoding="utf-8") as f:
        return f.read()


# Заглушка DOM: любой id — элемент с присваиваемыми полями и пустым контекстом canvas;
# document.addEventListener запоминает обработчики (клавиши) — их вызывает тест.
_DOM_STUB = r"""
const __HANDLERS = {};
const __ELS = {};
const __J = [];   // журнал вызовов canvas: [имя, аргументы] и ['set:имя', значение]
function __el(){
  const ctx = new Proxy({}, {
    get(t,p){ return typeof p === 'string' ? ((...a)=>{ __J.push([p, a.map(String)]); }) : undefined; },
    set(t,p,v){ __J.push(['set:' + String(p), String(v)]); return true; }});
  return {style:{}, dataset:{}, disabled:false, textContent:'', innerHTML:'', value:'', width:400, height:120,
          clientWidth:400, clientHeight:120, getContext(){ return ctx; },
          getBoundingClientRect(){ return {left:0,top:0,width:400,height:120}; },
          classList:{contains(){ return true; }, add(){}, remove(){}, toggle(){}},
          addEventListener(t,fn){ (this.__h = this.__h || {})[t] = fn; }, removeEventListener(){},
          appendChild(c){ return c; }, append(){}, setAttribute(){}, getAttribute(){ return null; },
          closest(){ return null; }, querySelector(){ return null; }, querySelectorAll(){ return []; }};
}
const document = {
  documentElement: {setAttribute(){}}, body: {},
  addEventListener(type, fn){ (__HANDLERS[type] = __HANDLERS[type] || []).push(fn); },
  removeEventListener(){},
  getElementById(id){ if(!__ELS[id]) __ELS[id] = __el(); return __ELS[id]; },
  createElement(){ return __el(); }, activeElement: null
};
const window = {addEventListener(type, fn){ (__HANDLERS['win:' + type] = __HANDLERS['win:' + type] || []).push(fn); },
                removeEventListener(){}};
globalThis.window = window;
globalThis.document = document;
globalThis.devicePixelRatio = 1;
globalThis.localStorage = {getItem(){ return null; }, setItem(){}};
globalThis.getComputedStyle = () => ({getPropertyValue(){ return ''; }});
const toast = () => {};
const uiLog = () => {};
const pvWordAt = () => '';
function __key(type, ev){
  const e = Object.assign({key:'', ctrlKey:false, metaKey:false, altKey:false, shiftKey:false,
                           target:{tagName:'DIV', type:''}, preventDefault(){}}, ev);
  (__HANDLERS[type] || []).forEach(fn => fn(e));
}
"""


def _run(tmp_path: Path, files: list[Path], body: str, name: str = "tl_check.js") -> Any:
    """Стенд: заглушка DOM + перечисленные файлы по порядку + сценарий; вернуть JSON последней строки."""
    script = _DOM_STUB + "\n" + "\n".join(_read(p) for p in files) + "\n" + body + "\n"
    path = tmp_path / name
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


# --- histStack / histKey -------------------------------------------------------------------

@node
def test_hist_stack_touch_undo_redo(tmp_path):
    """touch пишет шаг только при изменении; undo/redo переносят состояние между стеками;
    новая правка после отмены убивает повтор; reset очищает оба стека и ставит базу."""
    res = _run(tmp_path, [CORE_JS], """
let S={v:0};
const h=histStack({snapshot:()=>JSON.stringify(S),apply:s=>{S=JSON.parse(s);}});
const base=h.touch();                 // базы ещё нет — только запоминаем, шага нет
const noChange=h.touch();             // без изменений шага нет
S.v=1; const t1=h.touch();            // первый шаг
S.v=2; h.touch();
const u1=h.undo(), afterU1=S.v;       // 2 -> 1
const r1=h.redo(), afterR1=S.v;       // 1 -> 2
h.undo();                             // 2 -> 1
S.v=7; const t3=h.touch();            // новая правка убивает повтор
const canRedoAfterNew=h.canRedo();
const u2=h.undo(); const afterU2=S.v;  // 7 -> 1
h.reset();
console.log(JSON.stringify({base,noChange,t1,u1,afterU1,r1,afterR1,t3,canRedoAfterNew,u2,afterU2,
  canUndoAfterReset:h.canUndo(),canRedoAfterReset:h.canRedo()}));
""")
    assert res["base"] is False and res["noChange"] is False, "касание без изменения пишет шаг"
    assert res["t1"] is True and res["t3"] is True, "правка не записалась шагом"
    assert res["u1"] is True and res["afterU1"] == 1, "отмена не вернула прежнее состояние"
    assert res["r1"] is True and res["afterR1"] == 2, "повтор не вернул отменённое"
    assert res["canRedoAfterNew"] is False, "новая правка не убила стек повтора"
    assert res["afterU2"] == 1, "после новой правки отмена ушла не к предыдущему состоянию"
    assert res["canUndoAfterReset"] is False and res["canRedoAfterReset"] is False, (
        "reset не очистил стеки")


@node
def test_hist_stack_depth_cap_and_rebase(tmp_path):
    """Глубина ограничена max; rebase меняет базу без шага истории (нормализация сервера)."""
    res = _run(tmp_path, [CORE_JS], """
let S={v:0};
const h=histStack({max:5,snapshot:()=>JSON.stringify(S),apply:s=>{S=JSON.parse(s);}});
h.reset();
for(let i=1;i<=12;i++){S.v=i;h.touch();}
let depth=0;while(h.canUndo()){h.undo();depth++;}
S={v:40}; h.reset();
S.v=41; h.touch();                    // шаг есть
S.v=42; h.rebase();                   // 42 — новая база, без шага
const stepsAfterRebase=h.touch();     // снимок равен базе: шага нет
console.log(JSON.stringify({depth,stepsAfterRebase}));
""")
    assert res["depth"] == 5, f"глубина истории не ограничена пятью шагами: {res}"
    assert res["stepsAfterRebase"] is False, "rebase не сменил базу — нормализация стала шагом"


@node
def test_hist_key_maps_ctrl_z_y_and_russian_layout(tmp_path):
    """Ctrl/⌘+Z и «я» — отмена; Ctrl/⌘+Y, «н» и Ctrl/⌘+Shift+Z — повтор; без Ctrl — ничего."""
    res = _run(tmp_path, [CORE_JS], """
const k=(key,o)=>histKey(Object.assign({key,ctrlKey:false,metaKey:false,altKey:false,shiftKey:false},o||{}));
console.log(JSON.stringify({
  z:k('z',{ctrlKey:true}), zCap:k('Z',{ctrlKey:true}), ya:k('я',{ctrlKey:true}),
  y:k('y',{ctrlKey:true}), yRu:k('н',{ctrlKey:true}), meta:k('z',{metaKey:true}),
  metaY:k('y',{metaKey:true}), redoShift:k('Z',{ctrlKey:true,shiftKey:true}),
  redoShiftRu:k('Я',{ctrlKey:true,shiftKey:true}),
  plainZ:k('z'), plainY:k('y'), altZ:k('z',{ctrlKey:true,altKey:true}), cKey:k('c',{ctrlKey:true})
}));
""")
    assert res["z"] == "undo" and res["zCap"] == "undo" and res["ya"] == "undo"
    assert res["y"] == "redo" and res["yRu"] == "redo" and res["meta"] == "undo" and res["metaY"] == "redo"
    assert res["redoShift"] == "redo" and res["redoShiftRu"] == "redo"
    assert res["plainZ"] is None and res["plainY"] is None
    assert res["altZ"] is None and res["cKey"] is None


# --- редактор нарезки: отмена и повтор через клавиатуру ----------------------------------

_EDITOR_SETUP = """
ED.dur=100; ED.v0=0; ED.v1=100; ED.cs=40; ED.peaks=[]; ED.pps=80; ED.br=[]; ED.brBand=0; ED.cuts=[];
ED.blocks=[{s0:0,s1:100}]; ED.sel=-1; ED.fps=60; ED.raw=false; ED.play=false;
ED_HIST.reset();
"""


@node
def test_editor_undo_then_redo_restores_the_cut(tmp_path):
    """Резать → Ctrl+Z → блок целый; Ctrl+Y → разрез вернулся. Кнопка «Повтор» отражает стек."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
const key=(key,o)=>__key('keydown',Object.assign({key},o));
edSplit();
const afterSplit=ED.blocks.length;
key('z',{ctrlKey:true});
const afterUndo=ED.blocks.length, redoEnabled=!$('edRedoBtn').disabled;
key('y',{ctrlKey:true});
const afterRedo=ED.blocks.length;
key('я',{ctrlKey:true});                 // отмена русской раскладкой
const afterRuUndo=ED.blocks.length;
key('н',{ctrlKey:true});                 // повтор русской раскладкой
const afterRuRedo=ED.blocks.length;
console.log(JSON.stringify({afterSplit,afterUndo,redoEnabled,afterRedo,afterRuUndo,afterRuRedo}));
""")
    assert res["afterSplit"] == 2, res
    assert res["afterUndo"] == 1, "Ctrl+Z не вернул блок"
    assert res["redoEnabled"] is True, "после отмены кнопка «Повтор» не включилась"
    assert res["afterRedo"] == 2, "Ctrl+Y не вернул разрез"
    assert res["afterRuUndo"] == 1 and res["afterRuRedo"] == 2, "русская раскладка «я»/«н» не работает"


@node
def test_editor_drag_of_a_block_edge_is_one_history_step(tmp_path):
    """Тяга края блока — один шаг истории по отпусканию, не по каждому движению мыши.

    Отпускание — настоящий обработчик окна из edBind (mouseup): шага до него нет, после — есть,
    и отмена возвращает край к началу тяги.
    """
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
ED.blocks=[{s0:0,s1:50},{s0:60,s1:100}];
ED_HIST.reset();
edBind();
ED.drag={i:0,edge:1};
for(let k=1;k<=30;k++){ED.blocks[0].s1=40+k*0.5;}   // 30 движений тяги, кончается на 55 (не на 50)
const before=ED_HIST.canUndo();
__HANDLERS['win:mouseup'].forEach(fn=>fn());     // отпускание
const after=ED_HIST.canUndo();
__HANDLERS['win:mouseup'].forEach(fn=>fn());     // второе отпускание без тяги шага не даёт
edUndo();
const back=ED.blocks[0].s1;
const stepsLeft=ED_HIST.canUndo();
console.log(JSON.stringify({before,after,back,stepsLeft}));
""")
    assert res["before"] is False, "шаг записан до отпускания — по каждому движению"
    assert res["after"] is True, "отпускание не записало шаг"
    assert res["back"] == 50, "отмена не вернула край к началу тяги (50)"
    assert res["stepsLeft"] is False, "тяга записалась больше чем одним шагом"


# --- волна: максимум на пиксель ----------------------------------------------------------

def _spans(journal):
    """Высоты столбиков волны: пары moveTo/lineTo по x (y0 — moveTo, y1 — lineTo)."""
    out = {}
    pend = None
    for name, args in journal:
        if name == "moveTo":
            pend = (float(args[0]), float(args[1]))
        elif name == "lineTo" and pend is not None:
            out.setdefault(pend[0], abs(float(args[1]) - pend[1]))
            pend = None
    return out


@node
def test_wave_paints_the_peak_maximum_of_each_pixel(tmp_path):
    """Столбик пикселя — МАКСИМУМ пиков на его отрезке времени, не точка в начале пикселя.

    Четыре пикселя на 10 с: пиксель 0 покрывает 0..2.5 с, пик 1.0 стоит на 0.7 с (индекс 7 при pps 10).
    Точечная выборка брала бы индекс 0 (значение 0) и нарисовала бы почти плоскость.
    """
    res = _run(tmp_path, [CORE_JS], """
const peaks=new Array(100).fill(0); peaks[7]=1;
const colors=[];
const g=__el().getContext('2d');
__J.length=0;
tlPaintWave(g,{W:4,H:120,top:18,dpr:1,v0:0,v1:10,dur:10,peaks:peaks,pps:10,colorAt:s=>{colors.push(s);return '#fff';}});
console.log(JSON.stringify({journal:__J.map(x=>[x[0],x[1]]),colorsN:colors.length}));
""")
    spans = _spans([tuple(x) for x in res["journal"]])
    h0 = spans[0.5]
    assert abs(h0 - (120 - 18) * 0.44 * 2) < 1e-6, f"пиксель 0 не взял пик 1.0: высота {h0}"
    assert res["colorsN"] == 4, "цвет спрашивается раз на пиксель"


@node
def test_wave_without_peaks_draws_nothing(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
__J.length=0;
tlPaintWave(__el().getContext('2d'),{W:4,H:120,top:18,dpr:1,v0:0,v1:10,dur:10,peaks:[],pps:10,colorAt:()=>'#fff'});
console.log(JSON.stringify({n:__J.length}));
""")
    assert res["n"] == 0, "без пиков волна рисуется (раньше у редактора — плоская линия)"


@node
def test_editor_wave_takes_the_shared_maximum(tmp_path):
    """Редактор рисует волну через tlPaintWave: пик внутри пикселя виден, не теряется."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
const c=document.getElementById('edtl'); c.width=4; c.height=120;
ED.v0=0; ED.v1=10; ED.dur=10; ED.pps=10; ED.peaks=new Array(100).fill(0); ED.peaks[7]=1;
ED.blocks=[{s0:0,s1:10}]; ED.sel=0;
__J.length=0;
edDraw();
console.log(JSON.stringify({journal:__J.map(x=>[x[0],x[1]])}));
""")
    spans = _spans([tuple(x) for x in res["journal"]])
    assert abs(spans[0.5] - (120 - 18) * 0.44 * 2) < 1e-6, (
        f"редактор не взял максимум пикселя: высота {spans.get(0.5)}")


@node
def test_podcast_wave_takes_the_shared_maximum(tmp_path):
    """Окно «Вся запись» — тот же максимум: общая функция, одна высота при тех же данных."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
POD.dur=10; POD.v0=0; POD.v1=10; POD.peaks=new Array(100).fill(0); POD.peaks[7]=1; POD.peaksPps=10;
POD.segs=[{id:'a',start:0,end:10,title:'',why:'',reel:''}]; POD.picked=['a']; POD.sel='a'; POD.words=[];
__ELS.pod_tl_cv = __el(); __ELS.pod_tl_cv.width=4; __ELS.pod_tl_cv.height=120;
__ELS.pod_tl_cv.clientWidth=4; __ELS.pod_tl_cv.clientHeight=120;
__J.length=0;
podRenderTl();
console.log(JSON.stringify({journal:__J.map(x=>[x[0],x[1]])}));
""")
    spans = _spans([tuple(x) for x in res["journal"]])
    h = spans[0.5]
    # шкала подкаста: лента над волной — волна ниже неё; берём высоту и сверяем с общей формулой
    assert h > 0 and abs(h - (120 - (18 + 4 + 22 + 4)) * 0.44 * 2) < 1e-6, (
        f"окно подкаста не взяло максимум пикселя: высота {h}")


# --- тяга плейхеда: игра не останавливается ----------------------------------------------

@node
def test_podcast_seek_drag_does_not_pause_the_player(tmp_path):
    """Тяга плейхеда по «Вся запись» на пустом месте: видео идёт, pause/play не зовутся,
    позиция едет за курсором."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
const v={currentTime:40,paused:false,pauses:0,plays:0,
  pause(){this.pauses++;this.paused=true;},play(){this.plays++;this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=100; POD.segs=[]; POD.words=[]; POD.picked=[];
const ev=(x,y)=>({clientX:x,clientY:y,button:0,shiftKey:false,ctrlKey:false,metaKey:false,preventDefault(){}});
podDownTl(ev(200,100));                 // пустое место под линейкой — тяга-перемотка
podMove(ev(260,100));                   // 260 px из 400 при окне 0..100 — 65 с
const playing=!v.paused;
podUp();
console.log(JSON.stringify({pauses:v.pauses,plays:v.plays,playing,pos:v.currentTime}));
""")
    assert res["pauses"] == 0 and res["plays"] == 0, f"тяга остановила игру: {res}"
    assert res["playing"] is True, "после тяги видео не играет"
    assert abs(res["pos"] - 65) < 1e-6, f"позиция не поехала за курсором: {res}"


@node
def test_inserts_strip_drag_does_not_pause_the_player(tmp_path):
    """Тяга по полосе вставок: ipvPause/ipvPlay не зовутся, каждое движение — ipvSeekTo,
    отпускание не запускает игру заново (она и не останавливалась)."""
    src = _read(ROOT / "static" / "app" / "85-inserts-view.js")
    start = src.index("$('itlin').addEventListener('pointerdown'")
    end = src.index("// Средняя кнопка: без этого Chrome", start)
    block = src[start:end]
    res = _run(tmp_path, [CORE_JS], """
__ELS.itlin = __el();
let IPV={vids:[{}],playing:true}, ITL={pps:10,scrub:false};
const PAUSES=[], PLAYS=[], SEEKS=[];
function ipvPause(){PAUSES.push(1);IPV.playing=false;}
function ipvPlay(){PLAYS.push(1);IPV.playing=true;}
function ipvSeekTo(tm){SEEKS.push(+tm.toFixed(3));}
function itlMidPan(){}
""" + block + """
const down=__ELS.itlin.__h.pointerdown;
down({button:0,clientX:50,target:{closest(){return null;}},preventDefault(){}});
const scrubOn=ITL.scrub;
__HANDLERS['win:pointermove'].forEach(fn=>fn({clientX:120}));
__HANDLERS['win:pointerup'].forEach(fn=>fn({clientX:120}));
console.log(JSON.stringify({pauses:PAUSES.length,plays:PLAYS.length,seeks:SEEKS,scrubOn,
  scrubOff:ITL.scrub,playing:IPV.playing}));
""")
    assert res["pauses"] == 0 and res["plays"] == 0, f"протяжка полосы остановила игру: {res}"
    assert res["seeks"] == [5, 12], f"протяжка не переставляла позицию: {res}"
    assert res["scrubOn"] is True and res["scrubOff"] is False and res["playing"] is True, res


# --- окно за плейхедом: tlReveal, tlClampView (решение владельца 2026-10-12) ---------------
# Окно НЕ едет за плейхедом при игре и перемотке ни на одном таймлайне. Окно встаёт на цель
# только при явном переходе (podRevealAt / itlRevealAt). Тесты «не двигается» должны краснеть
# на старом коде (HEAD), где окно ехало за плейхедом.

def _js_fn(src: str, name: str) -> str:
    """Текст верхнеуровневой функции `name` от объявления до парной закрывающей скобки."""
    m = re.search(r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src, re.M)
    assert m is not None, f"не нашлась функция {name}"
    i = src.index("{", m.end())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"у функции {name} не нашлась закрывающая скобка")


def _js_fns(src: str, names: tuple) -> str:
    """Тела тех функций из `names`, что ЕСТЬ в файле: старый код звал itlEnsure, новый её уже не знает."""
    return "\n".join(_js_fn(src, n) for n in names
                     if re.search(r"^(?:async\s+)?function\s+%s\s*\(" % n, src, re.M))


@node
def test_reveal_keeps_the_window_while_the_target_is_inside(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
console.log(JSON.stringify({
  inside: tlReveal(0,20,15,100), edgeL: tlReveal(0,20,0,100), edgeR: tlReveal(0,20,20,100)}));
""")
    assert res["inside"] == [0, 20], "цель в окне — окно не должно двигаться"
    assert res["edgeL"] == [0, 20] and res["edgeR"] == [0, 20], "край окна — не «вне окна»"


@node
def test_reveal_puts_the_target_at_ten_percent_past_the_right_edge(tmp_path):
    """Цель за правым краем (50 с при окне 0..20): окно 48..68, цель на 10 % ширины."""
    res = _run(tmp_path, [CORE_JS], """
const r=tlReveal(0,20,50,100);
console.log(JSON.stringify({v0:r[0],v1:r[1],frac:(50-r[0])/(r[1]-r[0])}));
""")
    assert res["v0"] == pytest.approx(48) and res["v1"] == pytest.approx(68), res
    assert res["frac"] == pytest.approx(0.1), res


@node
def test_reveal_never_leaves_the_record(tmp_path):
    """У конца записи окно не уходит за длительность; запись без длины и окно шире записи — без сдвига."""
    res = _run(tmp_path, [CORE_JS], """
console.log(JSON.stringify({
  end: tlReveal(0,20,99,100), noDur: tlReveal(0,20,50,0), wide: tlReveal(0,20,50,10)}));
""")
    assert res["end"] == [80, 100], f"окно ушло за конец записи: {res}"
    assert res["noDur"] == [0, 20], "запись без длины: окно сдвинулось"
    assert res["wide"] == [0, 20], "окно шире записи: переход сломал его"


@node
def test_clamp_view_respects_the_minimum_span(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
console.log(JSON.stringify({
  lo: tlClampView(-5,20,100,5), hi: tlClampView(95,105,100,5),
  short: tlClampView(0,2,3,5), tiny: tlClampView(50,51,100,5), noMin: tlClampView(50,51,100,0)}));
""")
    assert res["lo"] == {"v0": 0, "v1": 25}
    assert res["hi"] == {"v0": 90, "v1": 100}, "окно у конца записи ушло за неё"
    assert res["short"] == {"v0": 0, "v1": 3}, "запись короче минимума — окно = вся запись"
    assert res["tiny"] == {"v0": 50, "v1": 55}, "окно короче минимума не доведено до минимума от своего начала"
    assert res["noMin"] == {"v0": 50, "v1": 51}


@node
def test_editor_view_does_not_follow_the_playhead(tmp_path):
    """Редактор: игра (edTick) и стрелки не двигают окно, даже когда плейхед ушёл за правый край."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
function pvVideoTo(){}                          // видео — заглушка: окно тест не трогает
function edFollow(){}                           // картинка за звуком — не предмет этого теста
function edArm(){}
let EA_CS=50;
function eaSync(){return {cs:EA_CS,end:false};} // часы звука: плейхед на 50 с
ED.play=true; ED.v0=0; ED.v1=20; ED.cs=40;
edTick();
const afterPlay=[ED.v0,ED.v1], csPlay=ED.cs;
ED.play=false; ED.v0=0; ED.v1=20; ED.cs=50;
__key('keydown',{key:'ArrowRight',target:{tagName:'DIV',type:''}});
console.log(JSON.stringify({afterPlay,csPlay,afterArrow:[ED.v0,ED.v1],csArrow:ED.cs}));
""")
    assert res["csPlay"] == 50, f"игра не двинула плейхед: {res}"
    assert res["afterPlay"] == [0, 20], "игра редактора поехала окном за плейхедом"
    assert res["csArrow"] > 50, f"стрелка не двинула плейхед: {res}"
    assert res["afterArrow"] == [0, 20], "перемотка стрелкой поехала окном за плейхедом"


@node
def test_podcast_view_does_not_follow_the_playhead(tmp_path):
    """«Вся запись»: timeupdate (игра) и перемотка не двигают окно, плейхед за его правым краем."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
const v={currentTime:50,paused:false,pauses:0,
  pause(){this.pauses++;this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=20; POD.segs=[]; POD.words=[]; POD.picked=[]; POD.stopAt=null;
podOnTime();                                    // кадр игры: плейхед на 50 с, окно 0..20
const afterTime=[POD.v0,POD.v1];
podSeek(60);                                    // перемотка за правый край
const afterSeek=[POD.v0,POD.v1];
console.log(JSON.stringify({afterTime,afterSeek,pos:v.currentTime}));
""")
    assert res["afterTime"] == [0, 20], "игра подкаста поехала окном за плейхедом"
    assert res["afterSeek"] == [0, 20], "перемотка подкаста поехала окном за плейхедом"
    assert res["pos"] == 60, f"перемотка не двинула плейхед: {res}"


@node
def test_podcast_play_segment_reveals_it(tmp_path):
    """«Играть кусок» из списка — явный переход: кусок вне окна → окно встаёт, кусок на 10 %."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
function iconBtn(){ return __el(); }
const v={currentTime:0,paused:true,pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=20; POD.words=[]; POD.picked=[]; POD.stopAt=null;
POD.segs=[{id:'a',start:50,end:60,title:'',why:'',reel:''}];
podPlaySeg('a');
console.log(JSON.stringify({v0:POD.v0,v1:POD.v1,pos:v.currentTime,stopAt:POD.stopAt,playing:!v.paused}));
""")
    assert res["v0"] == pytest.approx(48) and res["v1"] == pytest.approx(68), (
        f"«Играть кусок» не показал кусок: {res}")
    assert res["pos"] == 50 and res["stopAt"] == 60 and res["playing"] is True, res


@node
def test_inserts_strip_does_not_follow_the_playhead(tmp_path):
    """Лента вставок: перемотка (ipvSeekTo) и шаг игры (ipvStep) не двигают ленту за плейхедом."""
    src = _read(APP / "85-inserts-view.js")
    body = _js_fns(src, ("ipvSeekTo", "ipvStep", "itlEnsure"))
    res = _run(tmp_path, [CORE_JS], """
__ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],audio:[{ts:0,te:100,src:0,ci:0}],dur:100,contentDur:100,playing:true,aidx:0,vidx:-1,primed:-1};
let ITL={pps:10,scrub:false};
function pvSegAt(){return 0;} function spareIdle(){} function camIdle(){} function ipvApplyVisual(){}
function ipvUI(){} function musicElSync(){} function ipvRenderMode(){return true;} function ipvVideoTo(){}
function ipvLeadTime(){return 0;} function spareRollAt(){}
function pvStep(){return {tm:90,adv:false,swap:false,seeked:false,end:false};}
""" + body + """
ipvSeekTo(90);                                  // перемотка на 90 с при ленте 0..20 с
const afterSeek=__ELS.itl.scrollLeft;
__ELS.itl.scrollLeft=0;
ipvStep();                                      // кадр игры: тот же плейхед на 90 с
const afterStep=__ELS.itl.scrollLeft;
console.log(JSON.stringify({afterSeek,afterStep}));
""")
    assert res["afterSeek"] == 0, f"перемотка сдвинула ленту за плейхедом: {res}"
    assert res["afterStep"] == 0, f"игра сдвинула ленту за плейхедом: {res}"


@node
def test_inserts_double_click_reveals_the_insert(tmp_path):
    """Двойной клик по вставке (ipvJump) — явный переход: лента показывает начало вставки (90 с)."""
    src = _read(APP / "85-inserts-view.js")
    body = _js_fns(src, ("ipvSeekTo", "itlRevealAt", "ipvJump", "itlEnsure"))
    res = _run(tmp_path, [CORE_JS], """
__ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],audio:[{ts:0,te:100,src:0,ci:0}],dur:100,contentDur:100,playing:false,aidx:0,vidx:-1,primed:-1};
let ITL={pps:10,scrub:false};
function ipvIns(){return [{}];} function insSD(){return {s:90,d:2};}
function pvSegAt(){return 0;} function spareIdle(){} function camIdle(){} function ipvApplyVisual(){}
function ipvUI(){} function musicElSync(){} function ipvRenderMode(){return true;} function ipvVideoTo(){}
function ipvLeadTime(){return 0;} function ipvPlay(){IPV.playing=true;}
""" + body + """
ipvJump(0);
const el=__ELS.itl;
console.log(JSON.stringify({scroll:el.scrollLeft,v0:el.scrollLeft/10,v1:(el.scrollLeft+el.clientWidth)/10,
  playing:IPV.playing}));
""")
    assert res["v0"] <= 90 <= res["v1"], f"двойной клик: начало вставки (90 с) вне видимого окна ленты: {res}"
    assert res["scroll"] == pytest.approx(800), f"лента не встала на вставку: {res}"
    assert res["playing"] is True, res


@node
def test_inserts_reveal_does_not_fight_a_strip_drag(tmp_path):
    """Тянут ленту или плейхед (ITL.scrub) — явный переход не перебивает протяжку."""
    src = _read(APP / "85-inserts-view.js")
    body = _js_fn(src, "itlRevealAt")
    res = _run(tmp_path, [CORE_JS], """
__ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={contentDur:100,dur:100}; let ITL={pps:10,scrub:true};
""" + body + """
itlRevealAt(90);
console.log(JSON.stringify({scroll:__ELS.itl.scrollLeft}));
""")
    assert res["scroll"] == 0, "явный переход перебил протяжку ленты"


@node
def test_subtitle_row_reveals_its_start(tmp_path):
    """Строка списка субтитров — явный переход: плейхед на начало строки, лента показывает её."""
    inserts = _read(APP / "85-inserts-view.js")
    subs = _read(APP / "80-inserts.js")
    body = _js_fn(subs, "subrowGo") + "\n" + _js_fns(inserts, ("ipvSeekTo", "itlRevealAt", "itlEnsure"))
    res = _run(tmp_path, [CORE_JS], """
__ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],audio:[{ts:0,te:100,src:0,ci:0}],dur:100,contentDur:100,playing:false,aidx:0,vidx:-1,primed:-1};
let ITL={pps:10,scrub:false};
function pvSegAt(){return 0;} function spareIdle(){} function camIdle(){} function ipvApplyVisual(){}
function ipvUI(){} function musicElSync(){} function ipvRenderMode(){return true;} function ipvVideoTo(){}
function ipvLeadTime(){return 0;}
""" + body + """
subrowGo(90);
console.log(JSON.stringify({scroll:__ELS.itl.scrollLeft,aidx:IPV.aidx}));
""")
    assert res["scroll"] == pytest.approx(800), f"строка субтитров не показала своё место в ленте: {res}"


# Двери явного перехода: кто вправе звать tlReveal (через podRevealAt / itlRevealAt).
# Сторож по тексту: новый вызов из другой функции — красная строка, пока её не внесли сюда.
_REVEAL_DOORS = {
    "tlReveal": {"podRevealAt", "itlRevealAt"},
    "podRevealAt": {"podPlaySeg"},
    "itlRevealAt": {"ipvJump", "subrowGo"},
}
_OLD_FOLLOW_NAMES = ("tlFollowHead", "edFollowView", "podKeepHead", "itlEnsure")


def _callers(src: str, name: str) -> set[str]:
    """Имена верхнеуровневых функций, в теле которых звучит вызов `name(` (не объявление)."""
    out: set[str] = set()
    decls = [(m.start(), m.group(1)) for m in re.finditer(r"^(?:async\s+)?function\s+([\w$]+)\s*\(", src, re.M)]
    for m in re.finditer(r"(?<![\w$.])%s\s*\(" % re.escape(name), src):
        before = src[max(0, m.start() - 9):m.start()]
        if before.endswith("function "):
            continue
        owner = [n for pos, n in decls if pos <= m.start()]
        out.add(owner[-1] if owner else "<верхний уровень>")
    return out


def test_reveal_is_called_only_from_the_explicit_doors():
    files = sorted(APP.glob("*.js"))
    texts = {f.name: _read(f) for f in files}
    everything = "\n".join(texts.values())
    for old in _OLD_FOLLOW_NAMES:
        assert old not in everything, f"«{old}» ещё есть в static/app — слежение за плейхедом должно уйти"
    for name, allowed in _REVEAL_DOORS.items():
        found: set[str] = set()
        for text in texts.values():
            found |= _callers(text, name)
        assert found == allowed, (
            f"{name} зовётся из {sorted(found)}, а двери явного перехода — {sorted(allowed)}; "
            "новая дверь — внести сюда и в отчёт, иначе окно поедет за плейхедом")


# --- линейка: левая кнопка — плейхед за курсором, средняя — протяжка окна --------------
# Решение владельца 2026-10-17: линейка работает как пустое поле — плейхед в точку нажатия и за
# курсором, пока кнопка зажата; окно при этом стоит. Прокрутка окна — средняя кнопка и колесо.
# Стенд: mousedown редактора регистрируем сами — tlMidGuard вешает свой mousedown на тот же
# элемент и в заглушке затёр бы его (слот __h один на тип).

_EDITOR_RULER_SETUP = _EDITOR_SETUP + """
function pvVideoTo(){}
ED.v0=0; ED.v1=20; ED.cs=40;
const SEEKS=[];
const _edSeek=edSeek; edSeek=function(s){SEEKS.push(s);_edSeek(s);};
__ELS.edtl=__el(); const REC={};
__ELS.edtl.addEventListener=(t,fn)=>{(REC[t]=REC[t]||[]).push(fn);};
edBind();
const ev=(x,y,b)=>({clientX:x,clientY:y,button:b===undefined?0:b,pointerId:1,shiftKey:false,ctrlKey:false,metaKey:false,preventDefault(){}});
"""


@node
def test_editor_ruler_click_sets_the_head_and_keeps_the_window(tmp_path):
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_RULER_SETUP + """
REC.mousedown[0](ev(200,5));                  // линейка, левая кнопка: 200 px из 400 = 10 с окна 0..20
__HANDLERS['win:mouseup'].forEach(fn=>fn(ev(200,5)));   // отпускание без движения
console.log(JSON.stringify({seeks:SEEKS,cs:ED.cs,v0:ED.v0,v1:ED.v1}));
""")
    assert res["seeks"] == [pytest.approx(10)], f"клик по линейке не поставил плейхед в точку клика: {res}"
    assert res["cs"] == pytest.approx(10), res
    assert res["v0"] == 0 and res["v1"] == 20, f"клик по линейке сдвинул окно: {res}"


@node
def test_editor_ruler_drag_follows_the_cursor_and_keeps_the_window(tmp_path):
    """Левая кнопка на линейке: плейхед идёт за курсором по КАЖДОМУ движению, окно стоит,
    отпускание снимает тягу (дальше движение плейхед не двигает)."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_RULER_SETUP + """
REC.mousedown[0](ev(200,5));                                  // 10 с
__HANDLERS['win:mousemove'].forEach(fn=>fn(ev(240,5)));       // 12 с
__HANDLERS['win:mousemove'].forEach(fn=>fn(ev(100,5)));       // 5 с
__HANDLERS['win:mouseup'].forEach(fn=>fn(ev(100,5)));
__HANDLERS['win:mousemove'].forEach(fn=>fn(ev(300,5)));       // тяги нет — плейхед стоит
console.log(JSON.stringify({seeks:SEEKS,cs:ED.cs,v0:ED.v0,v1:ED.v1,drag:!!ED.drag}));
""")
    assert res["seeks"] == [pytest.approx(10), pytest.approx(12), pytest.approx(5)], (
        f"плейхед не шёл за курсором по каждому движению: {res}")
    assert res["cs"] == pytest.approx(5), res
    assert res["v0"] == 0 and res["v1"] == 20, f"протяжка линейки сдвинула окно: {res}"
    assert res["drag"] is False, "отпускание не сняло тягу плейхеда"


@node
def test_editor_ruler_middle_button_pans_the_view_not_the_head(tmp_path):
    """Средняя кнопка на линейке: окно едет за курсором, плейхед не ставится ни в pointerdown,
    ни в mousedown, который браузер шлёт следом."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_RULER_SETUP + """
REC.pointerdown[0](ev(200,5,1));
REC.mousedown[0](ev(200,5,1));
REC.pointermove[REC.pointermove.length-1](ev(100,5,1));       // 100 px влево — окно на 5 с вправо
REC.pointerup[REC.pointerup.length-1](ev(100,5,1));
console.log(JSON.stringify({seeks:SEEKS,cs:ED.cs,v0:ED.v0,v1:ED.v1}));
""")
    assert res["seeks"] == [], f"средняя кнопка поставила плейхед: {res}"
    assert res["cs"] == 40, f"средняя кнопка сдвинула плейхед: {res}"
    assert res["v0"] == pytest.approx(5) and res["v1"] == pytest.approx(25), (
        f"средняя кнопка на линейке не протянула окно: {res}")


@node
def test_podcast_ruler_click_sets_the_head_and_keeps_the_window(tmp_path):
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
const v={currentTime:40,paused:true,pauses:0,pause(){this.pauses++;this.paused=true;},
  play(){this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=50; POD.segs=[]; POD.words=[]; POD.picked=[];
const ev=(x,y)=>({clientX:x,clientY:y,button:0,pointerId:1,shiftKey:false,ctrlKey:false,metaKey:false,preventDefault(){}});
podDownTl(ev(200,5));                          // линейка: клик, не протяжка
__HANDLERS['pointerup'].forEach(fn=>fn(ev(200,5)));   // отпускание без движения (document)
console.log(JSON.stringify({pos:v.currentTime,v0:POD.v0,v1:POD.v1,pauses:v.pauses}));
""")
    assert res["pos"] == pytest.approx(25), f"клик по линейке не поставил плейхед в точку клика: {res}"
    assert res["v0"] == 0 and res["v1"] == 50, f"клик по линейке сдвинул окно: {res}"


@node
def test_podcast_ruler_drag_moves_the_head_and_keeps_the_window(tmp_path):
    """Левая кнопка на линейке «Вся запись»: плейхед за курсором по каждому движению, окно стоит."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
const v={currentTime:40,paused:true,pauses:0,pause(){this.pauses++;this.paused=true;},
  play(){this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=50; POD.segs=[]; POD.words=[]; POD.picked=[];
const SEEKS=[]; const _podSeek=podSeek; podSeek=function(t){SEEKS.push(t);return _podSeek(t);};
const ev=(x,y)=>({clientX:x,clientY:y,button:0,pointerId:1,shiftKey:false,ctrlKey:false,metaKey:false,preventDefault(){}});
podDownTl(ev(200,5));                          // 25 с
podMove(ev(100,5));                            // 12.5 с
podMove(ev(300,5));                            // 37.5 с
podUp();
console.log(JSON.stringify({seeks:SEEKS,pos:v.currentTime,v0:POD.v0,v1:POD.v1,pauses:v.pauses,drag:!!POD.drag}));
""")
    assert res["seeks"] == [pytest.approx(25), pytest.approx(12.5), pytest.approx(37.5)], (
        f"плейхед подкаста не шёл за курсором по каждому движению: {res}")
    assert res["pos"] == pytest.approx(37.5), res
    assert res["v0"] == 0 and res["v1"] == 50, f"протяжка линейки подкаста сдвинула окно: {res}"
    assert res["pauses"] == 0 and res["drag"] is False, res


@node
def test_inserts_ruler_click_sets_the_head_and_keeps_the_strip(tmp_path):
    src = _read(APP / "85-inserts-view.js")
    start = src.index("$('itlin').addEventListener('pointerdown'")
    end = src.index("// Средняя кнопка: без этого Chrome", start)
    block = src[start:end]
    res = _run(tmp_path, [CORE_JS], """
__ELS.itlin = __el(); __ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],playing:false}, ITL={pps:10,scrub:false};
const SEEKS=[];
function ipvPause(){}
function ipvPlay(){}
function ipvSeekTo(tm){SEEKS.push(tm);}
""" + _js_fn(src, "itlMidPan") + "\n" + block + """
const ruler={closest(sel){return sel==='#itlruler'?{}:null;}};
const ev=(x,b)=>({button:b===undefined?0:b,clientX:x,pointerId:1,target:ruler,preventDefault(){}});
__ELS.itlin.__h.pointerdown(ev(50));           // линейка, левая кнопка: 50 px при 10 px/с = 5 с
__HANDLERS['win:pointerup'].forEach(fn=>fn({pointerId:1,clientX:50}));   // отпускание без движения
console.log(JSON.stringify({seeks:SEEKS,scroll:__ELS.itl.scrollLeft}));
""")
    assert res["seeks"] == [5], f"клик по линейке вставок не поставил плейхед в точку клика: {res}"
    assert res["scroll"] == 0, f"клик по линейке вставок сдвинул ленту: {res}"


@node
def test_inserts_ruler_drag_moves_the_head_and_keeps_the_strip(tmp_path):
    """Левая кнопка на линейке вставок: плейхед за курсором по каждому движению, лента стоит."""
    src = _read(APP / "85-inserts-view.js")
    start = src.index("$('itlin').addEventListener('pointerdown'")
    end = src.index("// Средняя кнопка: без этого Chrome", start)
    block = src[start:end]
    res = _run(tmp_path, [CORE_JS], """
__ELS.itlin = __el(); __ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],playing:false}, ITL={pps:10,scrub:false};
const SEEKS=[];
function ipvPause(){}
function ipvPlay(){}
function ipvSeekTo(tm){SEEKS.push(tm);}
function itlMidPan(){}
""" + block + """
const ruler={closest(sel){return sel==='#itlruler'?{}:null;}};
const ev=(x)=>({button:0,clientX:x,pointerId:1,target:ruler,preventDefault(){}});
__ELS.itlin.__h.pointerdown(ev(200));                                    // 20 с
__HANDLERS['win:pointermove'].forEach(fn=>fn({pointerId:1,clientX:100}));   // 10 с
__HANDLERS['win:pointerup'].forEach(fn=>fn({pointerId:1,clientX:100}));
console.log(JSON.stringify({seeks:SEEKS,scroll:__ELS.itl.scrollLeft,scrub:ITL.scrub}));
""")
    assert res["seeks"] == [20, 10], f"плейхед вставок не шёл за курсором по каждому движению: {res}"
    assert res["scroll"] == 0, f"протяжка линейки вставок прокрутила ленту: {res}"
    assert res["scrub"] is False, "отпускание не сняло перемотку полосы"


# --- клавиши правки: резать и удалить --------------------------------------------------

@node
def test_edit_key_table(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
const k=(key,o)=>tlEditKey(Object.assign({key,ctrlKey:false,metaKey:false,altKey:false},o||{}));
console.log(JSON.stringify({
  c:k('c'), cRu:k('с'), s:k('S'), sRu:k('ы'),
  d:k('d'), dRu:k('в'), del:k('Delete'), bs:k('Backspace'),
  ctrlC:k('c',{ctrlKey:true}), altS:k('s',{altKey:true}), x:k('x'), enter:k('Enter')}));
""")
    assert res["c"] == "split" and res["cRu"] == "split" and res["s"] == "split" and res["sRu"] == "split"
    assert res["d"] == "delete" and res["dRu"] == "delete" and res["del"] == "delete" and res["bs"] == "delete"
    assert res["ctrlC"] is None and res["altS"] is None, "Ctrl+C / Alt+S — не клавиши правки"
    assert res["x"] is None and res["enter"] is None


@node
def test_editor_keys_split_and_delete(tmp_path):
    """Редактор: S режет под курсором, Backspace удаляет выбранный блок, Ctrl+C не режет."""
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
const key=(key,o)=>__key('keydown',Object.assign({key},o));
key('s');
const afterS=ED.blocks.length;
key('c',{ctrlKey:true});
const afterCtrlC=ED.blocks.length;
key('Backspace');
const afterBs=ED.blocks.length;
console.log(JSON.stringify({afterS,afterCtrlC,afterBs}));
""")
    assert res["afterS"] == 2, "S не разрезал блок под курсором"
    assert res["afterCtrlC"] == 2, "Ctrl+C разрезал блок"
    assert res["afterBs"] == 1, "Backspace не удалил выбранный блок"


@node
def test_podcast_keys_split_and_delete(tmp_path):
    """Подкаст: S режет кусок под плейхедом, Backspace удаляет выбранный, Ctrl+C не режет."""
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
globalThis.setTimeout=()=>0; globalThis.clearTimeout=()=>{};
function ico(){ return ''; }
function iconBtn(){ return __el(); }   // кнопки строк куска (20-widgets.js не грузим)
__ELS.mbConfirm={classList:{contains:()=>false}};
__ELS.pod_video={currentTime:20,paused:true};
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=100; POD.words=[]; POD.picked=['a']; POD.sel='a';
POD.segs=[{id:'a',start:10,end:30,title:'',why:'',reel:''}];
const key=(key,o)=>__key('keydown',Object.assign({key},o));
key('Ctrl');                                          // служебная нажатая клавиша — ничего
key('c',{ctrlKey:true});
const afterCtrlC=POD.segs.length;
key('S');
const afterS=POD.segs.length;
key('Backspace');
const afterBs=POD.segs.length;
console.log(JSON.stringify({afterCtrlC,afterS,afterBs}));
""")
    assert res["afterCtrlC"] == 1, "Ctrl+C разрезал кусок"
    assert res["afterS"] == 2, "S не разрезал кусок под плейхедом"
    assert res["afterBs"] == 1, "Backspace не удалил выбранный кусок"


# --- линейка: средняя кнопка двигает окно, плейхед не трогает -----------------------------

@node
def test_podcast_ruler_middle_button_pans_the_view_not_the_head(tmp_path):
    res = _run(tmp_path, [CORE_JS, PODCAST_JS], """
function ico(){ return ''; }
const v={currentTime:40,paused:false,pauses:0,pause(){this.pauses++;this.paused=true;},
  play(){this.paused=false;return Promise.resolve();}};
__ELS.pod_video=v;
POD.open=true; POD.dur=100; POD.v0=0; POD.v1=50; POD.segs=[]; POD.words=[]; POD.picked=[];
const SEEKS=[]; const _podSeek=podSeek; podSeek=function(t){SEEKS.push(t);return _podSeek(t);};
const ev=(x,y,b)=>({clientX:x,clientY:y,button:b,pointerId:1,shiftKey:false,ctrlKey:false,metaKey:false,preventDefault(){}});
podMid(ev(200,5,1));                           // средняя кнопка на линейке — протяжка окна
__ELS.pod_tl.__h.pointermove(ev(100,5,1));     // тянем влево на 100 px из 400 при окне 50 с — окно уехало на 12.5 с
__ELS.pod_tl.__h.pointerup(ev(100,5,1));
console.log(JSON.stringify({v0:POD.v0,v1:POD.v1,seeks:SEEKS,pos:v.currentTime,pauses:v.pauses,
  panning:tlPanning(__ELS.pod_tl),drag:!!POD.drag}));
""")
    assert res["v0"] == pytest.approx(12.5) and res["v1"] == pytest.approx(62.5), (
        f"средняя кнопка на линейке не сдвинула окно: {res}")
    assert res["seeks"] == [] and res["pos"] == 40, f"средняя кнопка перемотала плейхед: {res}"
    assert res["pauses"] == 0 and res["panning"] is False and res["drag"] is False, res


@node
def test_inserts_ruler_pointerdown_seeks_and_middle_button_pans(tmp_path):
    """Линейка вставок: левая кнопка ставит плейхед (а не тянет ленту), средняя — тянет ленту."""
    src = _read(APP / "85-inserts-view.js")
    start = src.index("$('itlin').addEventListener('pointerdown'")
    end = src.index("// Средняя кнопка: без этого Chrome", start)
    block = src[start:end]
    res = _run(tmp_path, [CORE_JS], """
__ELS.itlin = __el(); __ELS.itl = __el(); __ELS.itl.clientWidth = 200; __ELS.itl.scrollLeft = 0;
let IPV={vids:[{}],playing:false}, ITL={pps:10,scrub:false};
const SEEKS=[];
function ipvPause(){}
function ipvPlay(){}
function ipvSeekTo(tm){SEEKS.push(tm);}
""" + _js_fn(src, "itlMidPan") + "\n" + block + """
const ruler={closest(sel){return sel==='#itlruler'?{}:null;}};
const ev=(x,b)=>({button:b,clientX:x,pointerId:1,target:ruler,preventDefault(){}});
__ELS.itlin.__h.pointerdown(ev(50,0));         // левая на линейке: плейхед на 5 с
__HANDLERS['win:pointerup'].forEach(fn=>fn({pointerId:1,clientX:50}));
const afterLeft={seeks:SEEKS.slice(),scroll:__ELS.itl.scrollLeft};
SEEKS.length=0;
__ELS.itlin.__h.pointerdown(ev(200,1));        // средняя на линейке: лента едет за курсором
__ELS.itl.__h.pointermove({pointerId:1,clientX:100});
__ELS.itl.__h.pointerup({pointerId:1});
console.log(JSON.stringify({afterLeft,midSeeks:SEEKS,midScroll:__ELS.itl.scrollLeft}));
""")
    assert res["afterLeft"]["seeks"] == [5], f"левая кнопка на линейке не поставила плейхед: {res}"
    assert res["afterLeft"]["scroll"] == 0, f"левая кнопка на линейке сдвинула ленту: {res}"
    assert res["midSeeks"] == [], f"средняя кнопка на линейке поставила плейхед: {res}"
    assert res["midScroll"] == 100, f"средняя кнопка на линейке не протянула ленту: {res}"


def test_three_timeline_hints_share_the_gesture_sentence():
    """Жесты трёх полос времени описаны одной фразой (правило части А): редактор, «Вся запись», вставки."""
    html = _read(ROOT / "templates" / "index.html")
    gesture = ("Клик или протяжка по полю и по плейхеду — перемотка · клик или протяжка по линейке — плейхед за курсором · "
               "средняя кнопка — прокрутка окна · колесо — зум · "
               "Shift+колесо или горизонтальное колесо — прокрутка")
    assert html.count(gesture) == 3, (
        "фраза жестов должна стоять в «!» всех трёх полос ровно по разу: нашлось %d" % html.count(gesture))


# --- протяжка вида: tlMidPan ---------------------------------------------------------------

@node
def test_tl_mid_pan_follows_its_own_pointer_and_releases(tmp_path):
    """tlMidPan: смещение — от нажатия; чужой указатель не двигает; отпускание снимает тягу и курсор."""
    res = _run(tmp_path, [CORE_JS], """
const el=__el(); const log=[];
el.style.cursor='text';
tlMidPan(el,{clientX:200,pointerId:7},{snap:()=>({s:10}),apply:(dx,s)=>log.push([dx,s.s])});
const during=tlPanning(el), curDuring=el.style.cursor;
el.__h.pointermove({clientX:230,pointerId:7});
el.__h.pointermove({clientX:150,pointerId:9});       // другой указатель — не наш
el.__h.pointerup({pointerId:7});
console.log(JSON.stringify({during,curDuring,after:tlPanning(el),curAfter:el.style.cursor,log}));
""")
    assert res["during"] is True and res["curDuring"] == "grabbing", "во время тяги нет grabbing/метки"
    assert res["log"] == [[30, 10]], f"смещение не от нажатия или чужой указатель двинул вид: {res}"
    assert res["after"] is False and res["curAfter"] == "text", "отпускание не сняло тягу и курсор"


@node
def test_tl_mid_guard_stops_autoscroll_once(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
const el=__el();
tlMidGuard(el); tlMidGuard(el);
const n=(el.__h && el.__h.mousedown) ? 1 : 0;
let prevented=0;
el.__h.mousedown({button:1,preventDefault(){prevented++;}});
el.__h.mousedown({button:0,preventDefault(){prevented++;}});
console.log(JSON.stringify({n,prevented}));
""")
    assert res["n"] == 1 and res["prevented"] == 1, "средняя кнопка не гасится или гасится не только средняя"


# --- колесо на полосе времени: tlWheelBind -------------------------------------------------

@node
def test_wheel_bind_zooms_on_vertical_and_pans_on_shift_or_horizontal(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
const el=__el(); el.getBoundingClientRect=()=>({left:10,top:0,width:400,height:120});
const calls=[]; let prevented=0;
tlWheelBind(el,{pan:d=>calls.push(['pan',d]),zoom:(f,x)=>calls.push(['zoom',+f.toFixed(4),x])});
const h=el.__h.wheel;
const ev=o=>Object.assign({deltaX:0,deltaY:0,shiftKey:false,clientX:110,preventDefault(){prevented++;}},o);
h(ev({deltaY:-100}));                 // вверх — ближе, курсор в 100 css px от полосы
h(ev({deltaY:100}));                  // вниз — дальше
h(ev({deltaY:50,shiftKey:true}));     // Shift — прокрутка
h(ev({deltaX:30,deltaY:2}));          // горизонтальный жест — прокрутка
h(ev({deltaY:0}));                    // пустой жест — ничего, кроме отмены прокрутки страницы
console.log(JSON.stringify({calls,prevented}));
""")
    assert res["calls"] == [["zoom", 1.35, 100], ["zoom", 0.7407, 100], ["pan", 50], ["pan", 30]], res
    assert res["prevented"] == 5, "колесо не гасит прокрутку страницы у каждого жеста"


# --- зум окна: tlZoomSpan, редактор ------------------------------------------------------

@node
def test_zoom_span_keeps_the_point_under_the_cursor_and_respects_the_minimum(tmp_path):
    res = _run(tmp_path, [CORE_JS], """
console.log(JSON.stringify({
  deep: tlZoomSpan(0,100,1000,0.5,100,5),        // до минимума 5 с, по центру
  out: tlZoomSpan(0,50,0.1,0.5,100,5),           // дальше всей записи — вся запись
  anchorLeft: tlZoomSpan(0,100,2,0,100,1),       // курсор у левого края — он остаётся там
  anchorMid: tlZoomSpan(0,50,2,0.5,100,1)        // курсор в центре — тоже
}));
""")
    assert res["deep"] == {"v0": 47.5, "v1": 52.5}, res
    assert res["out"] == {"v0": 0, "v1": 100}, res
    assert res["anchorLeft"] == {"v0": 0, "v1": 50}, res
    assert res["anchorMid"] == {"v0": 12.5, "v1": 37.5}, res


@node
def test_editor_zoom_anchors_at_the_cursor_and_stops_at_one_and_a_half_seconds(tmp_path):
    res = _run(tmp_path, [CORE_JS, EDITOR_JS], _EDITOR_SETUP + """
const c=document.getElementById('edtl'); c.width=400; c.height=120;
ED.v0=0; ED.v1=100;
edZoom(2,100);                                   // курсор — 25 % ширины, при зуме 2 остаётся на месте
const anchored={v0:ED.v0,v1:ED.v1};
ED.v0=0; ED.v1=100; edZoom(1000,null);           // предел приближения — 1.5 с по центру
const capped={v0:ED.v0,v1:ED.v1};
console.log(JSON.stringify({anchored,capped}));
""")
    assert res["anchored"]["v0"] == pytest.approx(12.5) and res["anchored"]["v1"] == pytest.approx(62.5), res
    span = res["capped"]["v1"] - res["capped"]["v0"]
    assert span == pytest.approx(1.5) and res["capped"]["v0"] == pytest.approx(49.25), (
        f"редактор приблизил дальше минимума 1.5 с: {res}")


# --- кнопка-иконка строки: iconBtn (20-widgets.js) -------------------------------------

def test_icon_button_options_match_the_two_old_helpers():
    """iconBtn заменил podRowBtn (строка подкаста: data-t, без title и без остановки всплытия) и
    podIconBtn (куски: title, aria-label, клик не всплывает к строке). Различия — опциями."""
    widgets = _read(APP / "20-widgets.js")
    ic_start = widgets.index("const IC={")
    ic_src = widgets[ic_start:widgets.index("\n};", ic_start) + 3]
    ico_start = widgets.index("function ico(")
    ico_src = widgets[ico_start:widgets.index("\n", ico_start) + 1]
    start = widgets.index("function iconBtn(")
    btn_src = widgets[start:widgets.index("return b;}", start) + len("return b;}")]
    res = _run(tmp_path=Path(__import__("tempfile").mkdtemp()), files=[], body="""
""" + ic_src + "\n" + ico_src + "\n" + btn_src + """
// документ стенда — const: меняем его метод, а не сам объект
document.createElement=()=>{const e={attrs:{},setAttribute(k,v){this.attrs[k]=String(v);},
  title:'',innerHTML:'',className:'',type:'',onclick:null};return e;};
const fn=()=>{}; let stopped=0;
const row=iconBtn('pencil','Править куски',fn,{tip:true});
const cut=iconBtn('trash','Удалить кусок',()=>{},{stop:true,title:true});
cut.onclick({stopPropagation(){stopped++;}});
console.log(JSON.stringify({rowAttrs:row.attrs,rowTitle:row.title,rowSame:row.onclick===fn,
  cutAttrs:cut.attrs,cutTitle:cut.title,stopped,cls:cut.className,type:cut.type}));
""")
    assert res["rowAttrs"] == {"aria-label": "Править куски", "data-t": "Править куски"}, res
    assert res["rowTitle"] == "" and res["rowSame"] is True, "строка подкаста: без title, onclick — как передан"
    assert res["cutAttrs"] == {"aria-label": "Удалить кусок"} and res["cutTitle"] == "Удалить кусок", res
    assert res["stopped"] == 1, "кнопка куска не останавливает всплытие"
    assert res["cls"] == "icon" and res["type"] == "button", res


def test_dropdown_panel_and_timeline_colours_come_from_one_place():
    """Выпадающие списки (спикер клипа, дорожка подкаста) — одна панель .dd-panel; цвета полос —
    переменные --tl-bg / --tl-ribbon / --tl-bubble в :root, а TL_COL и canvas читают их."""
    css = _read(ROOT / "static" / "app.css")
    root = css[css.index(":root{"):css.index("}", css.index(":root{"))]
    for var in ("--tl-bg:#0d0d0d", "--tl-ribbon:#101010", "--tl-bubble:#141414"):
        assert var in root, f"в :root нет {var}"
    core = _read(APP / "00-core.js")
    for var in ("'--tl-bg'", "'--tl-ribbon'", "'--tl-bubble'"):
        assert var in core, f"TL_COL не читает {var}"
    assert "'#0d0d0d'" not in core.replace("g('--tl-bg', '#0d0d0d')", ""), "цвет полосы захардкожен мимо TL_COL"
    assert 'class="opts dd-panel"' in _read(APP / "40-queue.js"), "список спикера не на dd-panel"
    assert "'podmenu dd-panel'" in _read(APP / "45-podcast.js"), "меню дорожки не на dd-panel"
    assert 'class="podmenu dd-panel"' in _read(ROOT / "templates" / "index.html"), "меню дорожки в разметке не на dd-panel"
