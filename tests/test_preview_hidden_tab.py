# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Фон и перескок через вырезанное: шаг ведёт сторож-таймер, а не один rAF.

Баг владельца: «запустил превью, переключился в другое окно (alt-tab) и слушаю фоном —
играет ВСЁ подряд, вместе с вырезанными кусками. Вернулся в окно — снова перескакивает».

Причина: кадр игры редактора (edTick) и кадр предпросмотра вставок (ipvTick) планировались
только через requestAnimationFrame, а в скрытой вкладке браузер rAF не вызывает ВООБЩЕ.
Видео при этом играет само — значит перескока через вырезанное не было ни разу. Таймеры в
скрытой вкладке, которая ИГРАЕТ ЗВУК, Chrome не душит: поэтому шаг ведёт таймер.

Вторая, более частая причина (подтверждена в живом браузере): вкладка ушла в фон, а
`document.hidden` остался `false` — у перекрытого окна и у встроенных панелей rAF стоит, а
«скрыта» браузер не говорит. Одной ветки по флагу мало: шаг обязан приходить по сторожу
независимо от видимости. Инвариант — пока плеер играет, шаг идёт не реже ~PV_WATCHDOG_MS.

Что проверяется (в браузере это место слышно, а не видно, и ломается молча):

1. rAF НЕ приходит, а `document.hidden=false` (случай из живого браузера), время идёт:
   игра с 1.9 через блоки [0-2] и [5-7] перескакивает на 5, а не «играет» 2…5;
2. rAF идёт исправно — сторож не дублирует: за кадр ровно один шаг;
3. rAF шёл, потом встал посреди игры (события visibilitychange НЕ было) — перескок всё равно
   происходит;
4. пауза в скрытой вкладке: после неё не срабатывает НИ один вид шага;
5. скрытая вкладка (rAF не приходит никогда) — перескок по таймеру, и на паузе в фоне
   предпросмотр вставок замолкает;
6. смена видимости во время игры: уход в hidden — перескок есть, возврат в visible — один цикл;
7. плеер БЕЗ полей `step`/`tim`/`raf` (как пересозданный IPV): токен шага заводит сам
   планировщик — шаг идёт и по сторожу (перескок через вырез), и по rAF (ровно один на кадр).

Мутация: убрать сторож из pvFramePlan (оставить только ветку по `document.hidden`) — случаи 1
и 3 краснеют. Вторая мутация: вернуть `const step=++P.step` — токен на пересозданном плеере
(случай 7) становится NaN, `NaN!==NaN` — всегда «шаг чужой», и цикл встаёт намертво.

Стенд гоняет БОЕВЫЕ функции под node: тела берутся из `static/app/60-preview.js`,
`static/app/70-editor.js` и `static/app/85-inserts-view.js` — копий в тесте нет (иначе стенд
сторожил бы сам себя).

Запуск:  py -3.10 -m pytest tests/test_preview_hidden_tab.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"
VIEW_JS = ROOT / "static" / "app" / "85-inserts-view.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Планировщик кадра — из файла плеера: и объявления состояния, и функции. Копии в стенде
# разъехались бы с боевыми молча (период шага, оба поля rAF/таймер).
FRAME_CONSTS = ("PV_FRAME_MS", "PV_WATCHDOG_MS", "PV_FRAMES", "PV_FRAME_VIS")
FRAME_FUNCS = ("pvFrameHidden", "pvFrameStop", "pvFramePlan", "pvFrameStart",
               "pvFrameOff", "pvFrameReplan")
# Двери цикла редактора: пуск/пауза, часы (edTick) и прыжок через вырез.
EDITOR_FUNCS = ("edPlay", "edPause", "edTick", "edJump", "edBlockAt")
# Двери цикла предпросмотра вставок: свой шаг идёт через общий pvStep.
VIEW_FUNCS = ("ipvStep", "ipvTick", "ipvPlay", "ipvPause")
PREVIEW_FUNCS = ("pvStep",)


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


def _decl(src: str, name: str) -> str:
    """Объявление `const NAME=...;` / `let NAME=...;` — тоже из файла, а не копией."""
    m = re.search(r"^(?:const|let) %s=.*$" % re.escape(name), src, re.M)
    assert m is not None, f"в 60-preview.js нет объявления {name}"
    return m.group(0)


def _watchdog_ms() -> float:
    """Порог сторожа из боевого файла: тесты двигают время по нему, а не по своей копии."""
    m = re.search(r"^const PV_WATCHDOG_MS=([0-9.]+);", PREVIEW_JS.read_text(encoding="utf-8"), re.M)
    assert m is not None, "в 60-preview.js нет объявления PV_WATCHDOG_MS"
    return float(m.group(1))


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    view = VIEW_JS.read_text(encoding="utf-8")
    return "\n".join([_decl(preview, c) for c in FRAME_CONSTS]
                     + [_func_src(preview, n) for n in FRAME_FUNCS]
                     + [_func_src(preview, n) for n in PREVIEW_FUNCS]
                     + [_func_src(editor, n) for n in EDITOR_FUNCS]
                     + [_func_src(view, n) for n in VIEW_FUNCS])


STAND = r"""
// ---- мини-DOM: ровно то, что нужно двум циклам кадра --------------------------------
class El{
  constructor(tag){this.tag=String(tag).toUpperCase();this.attrs={};this.dataset={};this.style={};
    this.children=[];this.parentElement=null;this.className='';this.id='';this._text='';this._html='';
    this.paused=true;this.played=0;this.readyState=4;this.seeking=false;this.muted=false;
    this.volume=1;this.playbackRate=1;this._ct=0;this.seekLog=[];
    this.classList={add(){},remove(){},contains:()=>false};}
  set currentTime(v){this._ct=+v||0;this.seekLog.push(this._ct);}
  get currentTime(){return this._ct;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k];}
  appendChild(c){c.parentElement=this;this.children.push(c);return c;}
  insertBefore(c,ref){c.parentElement=this;this.children.push(c);return c;}
  remove(){}
  closest(){return null;}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  addEventListener(){}
  removeEventListener(){}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;}
  get innerHTML(){return this._html;}
  set innerHTML(h){this._html=String(h);}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);}
}
const BY_ID={};
function $(id){if(!BY_ID[id])BY_ID[id]=new El('div');return BY_ID[id];}
// ---- видимость вкладки -------------------------------------------------------------
// Слушателей запоминаем: видимость в тесте переключает ИМЕННО тест, как alt-tab в жизни.
const LISTENERS={};
globalThis.document={hidden:false,createElement:t=>new El(t),
  addEventListener:(k,fn)=>{(LISTENERS[k]=LISTENERS[k]||[]).push(fn);},
  body:new El('body'),activeElement:null,querySelectorAll:()=>[]};
function visibility(hidden){document.hidden=!!hidden;
  (LISTENERS.visibilitychange||[]).forEach(f=>f());}
globalThis.devicePixelRatio=1;
// ---- часы стенда под контролем теста ------------------------------------------------
// rAF ведёт себя как в СКРЫТОЙ вкладке: колбэки копятся и не вызываются сами — их пускает
// только flushRaf() («кадр»). Если бы стенд вызывал их сам, случай 1 ничего бы не показал.
// Кадр созревает через PV_STAND_FRAME_MS от взвода: браузерный rAF идёт по своим часам, а
// не от того, сколько тест отмерил на шаг.
let RAF_ID=0;let NOW=0;const RAFS=new Map();
const PV_STAND_FRAME_MS=16;
function requestAnimationFrame(cb){const id=++RAF_ID;RAFS.set(id,{cb:cb,due:NOW+PV_STAND_FRAME_MS});return id;}
function cancelAnimationFrame(id){RAFS.delete(id);}
function flushRaf(){const due=[...RAFS.entries()];RAFS.clear();
  for(const x of due)x[1].cb(0);return due.length;}
// Таймеры фальшивые, но с ВИРТУАЛЬНЫМ временем: настоящие 16 мс в тесте были бы гонкой,
// а «любой pending-таймер сработал» — враньём (сторож на 60 мс срабатывал бы раньше кадра
// и давал лишний шаг). Часы двигает advance(ms), как играющее <video>.
let TIM_ID=0;const TIMERS=new Map();
globalThis.setTimeout=(fn,delay)=>{const id=++TIM_ID;TIMERS.set(id,{fn:fn,due:NOW+(+delay||0)});return id;};
globalThis.clearTimeout=(id)=>{TIMERS.delete(id);};
// Ближайший к `to` готовый таймер: снимаем его из очереди и зовём.
function dueTimer(to){let bestId=0,bestDue=Infinity;
  for(const [id,t] of TIMERS){if(t.due<=to&&(t.due<bestDue||(t.due===bestDue&&id<bestId))){bestId=id;bestDue=t.due;}}
  if(!bestId)return null;const t=TIMERS.get(bestId);TIMERS.delete(bestId);return t;}
function flushTimers(){let n=0;for(;;){const t=dueTimer(NOW);if(!t)break;t.fn();n++;}return n;}
// Виртуальное время вперёд: rAF и сторожа срабатывают в порядке due — как в браузере.
// Важно: шаг, взведённый ВНУТРИ сработавшего колбэка, относится к следующему моменту, а
// время идёт только вперёд — иначе шаг вызывал бы сам себя без конца (от зацикливания
// стенда сторожит счётчик событий: он должен падать, а не съедать память).
function advance(ms){let left=+ms||0,guard=0;
  while(left>0){
    const target=NOW+left;
    let tId=0,tDue=Infinity;
    for(const [id,t] of TIMERS)if(t.due<tDue||(t.due===tDue&&id<tId)){tId=id;tDue=t.due;}
    let rId=0,rDue=Infinity;
    for(const [id,r] of RAFS)if(r.due<rDue||(r.due===rDue&&id<rId)){rId=id;rDue=r.due;}
    const next=Math.min(tDue,rDue);
    if(next>target){NOW=target;break;}
    NOW=next>NOW?next:NOW+1;
    if(++guard>100000)throw new Error('стенд: события не кончаются (NOW='+NOW+')');
    left=target-NOW;
    if(rDue<=tDue){const r=RAFS.get(rId);RAFS.delete(rId);if(r)r.cb(0);}
    else{const t=TIMERS.get(tId);TIMERS.delete(tId);if(t)t.fn();}
  }}
// ---- состояние плееров (поля ровно как в бою) ---------------------------------------
let MEDIA_VOL=1;
let EDMUTVOICE=0;
let VTTICK=[];        // дорожка обработанного голоса: кто позвал и куда — считает и ШАГИ
let ARMS=0;           // разбег дублёра: edArm зовётся каждым кадром игры
let DRAWS=0;          // рисование таймлайна: в скрытой вкладке его можно пропустить
function vtTick(P,tm){VTTICK.push([P,+tm]);}
function edArm(){ARMS++;}
function edDraw(){DRAWS++;}
function edUI(){}
function vtOf(){return {failed:false};}
function vtPrep(){}
function audioWake(){}
function pvAudioLimit(){}
function ico(){return '';}
function t(s){return String(s);}
function toast(){}
function errText(){return '';}
function pvVideoTo(){}
function vtLivePlay(){}
function spareIdle(){}
function spareLead(){return null;}   // дублёров камер нет: прыжок идёт запасным seek'ом
function spareStop(){}
function sparePrime(){}
function spareSwap(){return false;}
function spareRollAt(){}
function camIdle(){}
function camVisual(){}
function camTrack(){}
function vtPlaying(){return false;}
function vtPause(){}
function ipvApplyVisual(){}
function ipvUI(){}
function ipvNow(){return 0;}
function musicElSync(){}
function sfxPause(){}
function edVoiceSeekWait(){}   // ожидание промаха дублёра: не предмет этого стенда
// Плеер шага 1: редактор (ED) и общий объект кадра (PV) — часы и прыжок живут на них.
const CAM=new El('video');CAM.src='C:/cam1.mp4';
let ED={xml:'C:/out/01_clip.xml',blocks:[{s0:0,s1:2},{s0:5,s1:7}],fps:60,cam:'C:/cam1.mp4',dur:7,
  peaks:[],pps:80,sel:-1,play:false,raw:false,raf:0,step:0,tim:0,cs:0,drag:null,v0:0,v1:7,hist:[],cuts:[],
  br:[],brBand:0,cams:null,voicePanel:'pvvoice',
  vtOpen:false,vtTimer:0,vtEl:null,vtOn:null};
let PV={vids:[CAM],bufs:[],cams:[{path:'C:/cam1.mp4',name:'A'}],segs:[],audio:[],words:[],dur:7,
  aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,tim:0,xml:'',
  voicePanel:'pvvoice'};
// Предпросмотр вставок (IPV): два куска EDL с вырезом 2..5 исходника — прыжок есть.
const IV=new El('video');IV.src='C:/cam1.mp4';
let IPV={vids:[IV],bufs:[],scrubbing:false,scrubT:0,
  segs:[{ts:0,te:2,src:0,ci:0},{ts:2,te:4,src:5,ci:0}],
  audio:[{ts:0,te:2,src:0,ci:0},{ts:2,te:4,src:5,ci:0}],words:[],dur:4,contentDur:4,fps:60,aidx:0,
  vidx:-1,primed:-1,curCi:-1,rollCi:-1,playing:false,raf:0,step:0,tim:0,xml:'',cur:-1,intro:[],
  introCur:-1,plan:null,insShift:null,insVids:new Map(),dims:new Map(),roto:[],
  stats:{styk:0,swap:0,seek:0}};
// ---- прогон -------------------------------------------------------------------------
function resetCounters(){VTTICK=[];ARMS=0;DRAWS=0;}
// Кадр жизни: время идёт вперёд шагом PV_STAND_FRAME_MS, rAF и сторожа срабатывают в
// порядке due — как в браузере. Видео «играет само»: его currentTime ведёт стенд, и ровно
// на столько же двигается плейхед (в бою его берут из <video> в edTick). В скрытой вкладке
// (rAF не приходит вовсе, шаг ведёт сторож) работает он же: время идёт так же, иначе таймер
// на PV_WATCHDOG_MS не сработал бы от того, что его просто «покрутили».
function tickFrames(v,n){const d=PV_STAND_FRAME_MS/1000;
  for(let i=0;i<n;i++){v.currentTime=v.currentTime+d;advance(PV_STAND_FRAME_MS);}}
// Время, которое НЕ должно было прозвучать: вырезанный зазор 2..5 исходника.
function inCut(log){return log.filter(x=>x>2.05&&x<4.95);}
function report(name,extra){console.log(JSON.stringify(Object.assign({name:name},extra)));}
""" + _bodies() + r"""


function edFrames(n){tickFrames(CAM,n);}
"""


def _run_node(body: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hidden_tab_") as d:
        path = Path(d) / "stand.js"
        path.write_text(STAND + "\n" + body, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 1. Скрытая вкладка: перескок через вырезанное идёт по таймеру
# --------------------------------------------------------------------------- #
@node
def test_hidden_tab_jumps_over_the_cut_by_timer() -> None:
    """В скрытой вкладке rAF не приходит вовсе — шаг ведёт таймер, и перескок есть.

    Блоки правки [0-2] и [5-7]: игра с 1.9 обязана перескочить на 5, а не «проиграть»
    вырезанное 2…5. Проверяем и то, что перескок ведёт тот же кадр, что и голос
    (`vtTick(ED,…)`) с разбегом дублёра (`edArm`), — иначе в фоне пропал бы звук.
    """
    out = _run_node("""
(()=>{
  visibility(true);                       // вкладку скрыли: rAF не придёт ни разу
  ED.cs=1.9;CAM.currentTime=1.9;
  edPlay();
  const rafAfterPlay=RAFS.size;           // скрытая вкладка: rAF не взведён вовсе
  CAM.seekLog.length=0;
  edFrames(13);                           // видео играет само, шаг приходит таймером
  report('jump',{cs:ED.cs,at:CAM.currentTime,seekLog:CAM.seekLog,
    inCut:inCut(CAM.seekLog),rafAfterPlay:rafAfterPlay,rafEnd:RAFS.size,
    arms:ARMS,ticks:VTTICK.length,tickIsEd:VTTICK.every(x=>x[0]===ED),
    draws:DRAWS,playing:!!ED.play});
})();
""")
    assert out["rafAfterPlay"] == 0 and out["rafEnd"] == 0, (
        f"в скрытой вкладке шаг взведён через rAF — он не сработает никогда: {out}")
    assert out["playing"] is True, f"игра прервалась сама: {out}"
    assert out["arms"] >= 3, f"кадр игры не пришёл по таймеру: {out['arms']} шагов"
    # Шаг зовёт голос сам, и прыжок через вырез зовёт его ещё раз — отсюда «не меньше».
    assert out["ticks"] >= out["arms"] and out["tickIsEd"] is True, (
        f"голос не ведётся кадром игры в скрытой вкладке: {out}")
    assert 5 in out["seekLog"], f"перескока на 5 через вырез не было: {out['seekLog']}"
    assert out["inCut"] == [], (
        f"в фоне проиграно вырезанное (время {out['inCut']} из зазора 2…5): {out['seekLog']}")
    assert abs(out["cs"] - out["at"]) < 2e-2, (
        f"плейхед разошёлся с живым <video>: {out}")
    assert out["draws"] == 0, (
        "в скрытой вкладке таймлайн рисуется на каждом шаге — это лишняя работа фона")


# --------------------------------------------------------------------------- #
# 2. Смена видимости во время игры: один живой цикл, а не два
# --------------------------------------------------------------------------- #
@node
def test_visibility_change_mid_play_keeps_one_live_cycle() -> None:
    """Ушли в другое окно посреди игры — шаг переезжает с rAF на таймер; вернулись — обратно.

    Уже взведённый rAF в скрытой вкладке не сработает НИКОГДА, поэтому без перевзвода игра
    в фоне встала бы намертво. И наоборот: перевзвод обязан СНЯТЬ прежний шаг — два живых
    шага дают двойной перескок (шаг ровно один на кадр).
    """
    out = _run_node("""
(()=>{
  ED.cs=1.9;CAM.currentTime=1.9;
  edPlay();                               // играем в видимой вкладке: шаг взведён через rAF
  const rafVisible=RAFS.size;
  visibility(true);                       // alt-tab посреди игры
  const rafAfterHide=RAFS.size,timAfterHide=TIMERS.size;
  CAM.seekLog.length=0;
  tickFrames(CAM,13);                     // rAF молчит: шаг в фоне ведёт сторож
  const jumped=ED.cs,jumpedTo=CAM.currentTime,seekLog=[...CAM.seekLog];
  visibility(false);                      // вернулись в окно
  resetCounters();
  flushRaf();                             // один кадр видимой вкладки
  report('vis',{rafVisible:rafVisible,rafAfterHide:rafAfterHide,timAfterHide:timAfterHide,
    jumped:jumped,jumpedTo:jumpedTo,steps:ARMS,ticks:VTTICK.length,rafNow:RAFS.size,
    timNow:TIMERS.size,inCut:inCut(seekLog)});
})();
""")
    assert out["rafVisible"] == 1, f"видимая вкладка не взвела rAF: {out}"
    assert out["rafAfterHide"] == 0 and out["timAfterHide"] == 1, (
        f"уход в фон не перевёл шаг с rAF на таймер: {out}")
    # Перескок на 5: сторож сработал в фоне и довёл плейхед до живого 5 (прыжок внутри
    # шага, а не «оставить на паузе») — и шаг при этом РОВНО один.
    assert out["jumped"] >= 5 and out["jumpedTo"] >= 5 and not out["inCut"], (
        f"в скрытой вкладке перескок через вырез не сработал: {out}")
    assert abs(out["jumped"] - out["jumpedTo"]) < 2e-2, (
        f"плейхед разошёлся с живым <video>: {out}")
    assert out["steps"] == 1, (
        f"после ухода в фон живой не один шаг за кадр, а {out['steps']}: {out}")
    assert out["ticks"] == 1, f"шаг позвал голос не по разу на шаг: {out}"
    assert out["rafNow"] == 1 and out["timNow"] == 1, (
        f"возврат в окно не вернул цикл на rAF: {out}")


# --------------------------------------------------------------------------- #
# 3. Пауза в скрытой вкладке: не срабатывает ни один вид шага
# --------------------------------------------------------------------------- #
@node
def test_pause_in_hidden_tab_stops_both_kinds_of_step() -> None:
    """Пауза снимает И rAF, И таймер: после неё цикл не воскресает даже при возврате в окно.

    В фоне это особенно опасно: игра «остановлена», а таймер продолжает звать кадр — и после
    закрытия клипа плеер дёргал бы уже снятые элементы.
    """
    out = _run_node("""
(()=>{
  visibility(true);
  ED.cs=1.0;CAM.currentTime=1.0;
  edPlay();
  const before=ARMS;                      // шага ещё не было: сторож ждёт свои 60 мс
  edPause();
  resetCounters();
  const timAfter=TIMERS.size,rafAfter=RAFS.size;
  CAM.currentTime=1.99;flushTimers();flushRaf();   // ни один вид шага не должен сработать
  visibility(false);flushRaf();                    // и возврат в окно игру не воскрешает
  report('pause',{before:before,timAfter:timAfter,rafAfter:rafAfter,steps:ARMS,
    ticks:VTTICK.length,playing:!!ED.play});
})();
""")
    assert out["before"] == 0, f"шаг пришёл раньше сторожа: {out}"
    assert out["playing"] is False, f"пауза не остановила игру: {out}"
    assert out["timAfter"] == 0 and out["rafAfter"] == 0, (
        f"после паузы остался живой шаг (rAF/таймер): {out}")
    assert out["steps"] == 0 and out["ticks"] == 0, (
        f"после паузы кадр всё ещё зовётся: {out}")


# --------------------------------------------------------------------------- #
# 4. Предпросмотр вставок: тот же перескок по таймеру
# --------------------------------------------------------------------------- #
@node
def test_inserts_preview_jumps_over_its_cut_by_timer() -> None:
    """ipvTick в скрытой вкладке тоже ведёт таймер: куски EDL [0-2] и (2-4 от исходника 5)
    дают перескок на 5, а не проигрывание вырезанного.

    До правки здесь был только rAF, и фон играл предпросмотр вставок с вырезанным; а
    страховочный setInterval складывался с rAF в видимой вкладке (плейхед быстрее кадра).
    """
    out = _run_node("""
(()=>{
  visibility(true);
  IV.currentTime=1.9;IV.seekLog.length=0;
  ipvPlay();
  VTTICK.length=0;                         // пуск сам зовёт голос: считаем только шаги игры
  const rafAfterPlay=RAFS.size;
  tickFrames(IV,13);
  const afterPlay={aidx:IPV.aidx,at:IV.currentTime,seekLog:IV.seekLog,
    inCut:inCut(IV.seekLog),ticks:VTTICK.length,playing:!!IPV.playing,rafEnd:RAFS.size};
  ipvPause();                              // пауза в фоне: шаг снимается вместе с таймером
  resetCounters();
  flushTimers();flushRaf();
  report('ipv',{afterPlay:afterPlay,rafAfterPlay:rafAfterPlay,
    ticksAfterPause:VTTICK.length,playing:!!IPV.playing,timNow:TIMERS.size});
})();
""")
    assert out["rafAfterPlay"] == 0, f"в скрытой вкладке шаг взведён через rAF: {out}"
    play = out["afterPlay"]
    assert play["playing"] is True and play["aidx"] == 1, (
        f"предпросмотр вставок не прошёл стык кусков в фоне: {play}")
    assert 5 in play["seekLog"], f"перескока через вырез не было: {play['seekLog']}"
    assert play["inCut"] == [], (
        f"в фоне проигран вырез предпросмотра вставок: {play['seekLog']}")
    assert play["ticks"] >= 3 and play["rafEnd"] == 0, (
        f"шаг предпросмотра вставок не шёл по таймеру: {play}")
    assert out["playing"] is False and out["ticksAfterPause"] == 0, (
        f"после паузы в фоне шаг предпросмотра вставок продолжается: {out}")
    assert out["timNow"] == 0, f"после паузы остался живой таймер: {out}"


# --------------------------------------------------------------------------- #
# 5. rAF стоит, а document.hidden=false — случай из живого браузера
# --------------------------------------------------------------------------- #
@node
def test_dead_raf_with_visible_flag_still_jumps() -> None:
    """rAF не приходит НИ РАЗУ, но `document.hidden` врёт «видно» — шаг ведёт сторож.

    Ровно случай из живого браузера: вкладка ушла в фон (перекрытое окно, встроенная
    панель), rAF встал, а `visibilitychange` не пришёл и флаг остался `false`. В логе
    владельца `ED.cs` стоял на 7.5, а видео играло 7.5 → 12.6 внутрь вырезанного.
    Правильность не должна зависеть от флага: игра с 1.9 через блоки [0-2] и [5-7] обязана
    перескочить на 5, и ни одного кадра из зазора 2…5 в логе перемоток быть не должно.
    """
    out = _run_node("""
(()=>{
  visibility(false);                      // флаг ВРЁТ: браузер говорит «видно», rAF молчит
  ED.cs=1.9;CAM.currentTime=1.9;
  edPlay();
  const hiddenAtPlay=!!document.hidden,rafAfterPlay=RAFS.size,timAfterPlay=TIMERS.size;
  CAM.seekLog.length=0;
  tickFrames(CAM,10);                     // кадров rAF нет: шаг приходит только сторожем
  report('deadraf',{hiddenAtPlay:hiddenAtPlay,cs:ED.cs,at:CAM.currentTime,
    seekLog:CAM.seekLog,inCut:inCut(CAM.seekLog),rafAfterPlay:rafAfterPlay,
    timAfterPlay:timAfterPlay,arms:ARMS,ticks:VTTICK.length,
    tickIsEd:VTTICK.every(x=>x[0]===ED),playing:!!ED.play});
})();
""")
    assert out["hiddenAtPlay"] is False, f"стенд соврал про видимость: {out}"
    assert out["rafAfterPlay"] == 1, f"rAF не был взведён вовсе — проверять нечего: {out}"
    assert out["timAfterPlay"] == 1, f"сторож не взведён рядом с rAF: {out}"
    assert out["playing"] is True, f"игра прервалась сама: {out}"
    assert out["arms"] >= 2, (
        f"сторож не повёл шаг при молчащем rAF: {out['arms']} шагов за 160 мс")
    assert out["ticks"] >= out["arms"] and out["tickIsEd"] is True, (
        f"голос не ведётся кадром игры при молчащем rAF: {out}")
    assert 5 in out["seekLog"], f"перескока на 5 через вырез не было: {out['seekLog']}"
    assert out["inCut"] == [], (
        f"проиграно вырезанное (время {out['inCut']} из зазора 2…5): {out['seekLog']}")
    assert abs(out["cs"] - out["at"]) < 2e-2, f"плейхед разошёлся с живым <video>: {out}"


# --------------------------------------------------------------------------- #
# 6. rAF идёт исправно: сторож не дублирует шаг
# --------------------------------------------------------------------------- #
@node
def test_live_raf_gives_exactly_one_step_per_frame() -> None:
    """Пока rAF приходит, сторож молчит: на кадр ровно один шаг, а не два.

    Два шага за кадр — это двойной перескок через вырезанное: голос и плейхед уезжают
    вперёд картинки. Здесь оба вида шага доступны (rAF и сторож), и они обязаны дать
    РОВНО 10 шагов на 10 кадров — то есть ни один кадр не шагнул дважды.
    """
    out = _run_node("""
(()=>{
  visibility(false);                      // окно на виду: rAF приходит
  ED.cs=1.0;CAM.currentTime=1.0;
  edPlay();
  resetCounters();
  const rafAfterPlay=RAFS.size,timAfterPlay=TIMERS.size;
  tickFrames(CAM,10);                     // rAF приходит каждый кадр: сторож не нужен
  report('live',{rafAfterPlay:rafAfterPlay,timAfterPlay:timAfterPlay,arms:ARMS,
    ticks:VTTICK.length,playing:!!ED.play});
})();
""")
    assert out["rafAfterPlay"] == 1, f"живой rAF не взведён: {out}"
    assert out["timAfterPlay"] == 1, f"сторож не взведён рядом с rAF: {out}"
    assert out["playing"] is True, f"игра прервалась сама: {out}"
    assert out["arms"] == 10, (
        f"на 10 кадров пришло {out['arms']} шагов — сторож дублирует живой rAF")
    assert out["ticks"] == 10, f"голос позван не по разу на кадр: {out}"


# --------------------------------------------------------------------------- #
# 7. rAF шёл, потом встал посреди игры — без события visibilitychange
# --------------------------------------------------------------------------- #
@node
def test_raf_dies_mid_play_without_visibility_event() -> None:
    """rAF приходит, потом перестаёт — и БЕЗ visibilitychange шаг ведёт сторож.

    Событие приходит не всегда (перекрытое окно, встроенная панель): если бы перескок
    держался на нём, игра в фоне поехала бы по вырезанному молча.
    """
    out = _run_node("""
(()=>{
  visibility(false);
  ED.cs=1.0;CAM.currentTime=1.0;
  edPlay();
  tickFrames(CAM,3);                      // rAF шёл: кадры ведёт он
  const beforeArms=ARMS;
  ED.cs=1.0;CAM.currentTime=1.9;          // плейхед у самого выреза 2…5, rAF встал
  CAM.seekLog.length=0;
  tickFrames(CAM,10);                     // rAF мёртв, события видимости НЕ было
  report('died',{beforeArms:beforeArms,hidden:!!document.hidden,cs:ED.cs,at:CAM.currentTime,
    seekLog:CAM.seekLog,inCut:inCut(CAM.seekLog),arms:ARMS,rafNow:RAFS.size,
    playing:!!ED.play});
})();
""")
    assert out["beforeArms"] == 3, f"rAF не вёл шаг до поломки: {out}"
    assert out["hidden"] is False, f"стенд соврал про видимость: {out}"
    assert out["playing"] is True, f"игра прервалась сама: {out}"
    assert out["arms"] > out["beforeArms"], (
        f"после смерти rAF сторож не подхватил шаг: {out['arms']} шагов")
    assert 5 in out["seekLog"], f"перескока на 5 через вырез не было: {out['seekLog']}"
    assert out["inCut"] == [], (
        f"проиграно вырезанное (время {out['inCut']} из зазора 2…5): {out['seekLog']}")


# --------------------------------------------------------------------------- #
# 8. Плеер без полей шага (пересозданный IPV): токен заводит сам планировщик
# --------------------------------------------------------------------------- #
@node
def test_recreated_player_without_step_fields_still_steps() -> None:
    """Плеер БЕЗ полей `step`/`tim`/`raf` (как пересозданный IPV) всё равно шагает.

    `IPV` пересобирается литералом при каждом открытии предпросмотра вставок
    (85-inserts-view.js, ipvOpen): если планировщик берёт поле вызывающего (`++P.step`),
    на пересозданном плеере выходит `NaN`, а `NaN!==NaN` — всегда «шаг чужой», и цикл
    встаёт намертво с первого же открытия. Поэтому токен ведёт САМ планировщик, а поля
    `raf`/`tim` он только читает (undefined — это «шага нет») и заводит присваиванием.

    Оба вида шага проверяются на плеере без этих полей: rAF стоит — шаг ведёт сторож и
    перескок через вырез есть; rAF идёт — на кадр ровно один шаг, а не ноль.
    """
    off = _run_node("""
(()=>{
  visibility(true);                      // rAF не придёт: шаг ведёт только сторож
  delete IPV.step;delete IPV.tim;delete IPV.raf;   // пересозданный IPV полей не несёт
  IV.currentTime=1.9;IV.seekLog.length=0;
  ipvPlay();
  VTTICK.length=0;                       // пуск сам зовёт голос: считаем только шаги игры
  const rafAfterPlay=RAFS.size,timAfterPlay=TIMERS.size;
  tickFrames(IV,13);
  report('bare_off',{rafAfterPlay:rafAfterPlay,timAfterPlay:timAfterPlay,ticks:VTTICK.length,
    at:IV.currentTime,seekLog:IV.seekLog,inCut:inCut(IV.seekLog),playing:!!IPV.playing});
})();
""")
    assert off["rafAfterPlay"] == 0, f"скрытая вкладка взвела rAF — он не придёт: {off}"
    assert off["timAfterPlay"] == 1, (
        f"без прежнего поля `tim` сторож не взведён — шага не будет вовсе: {off}")
    assert off["playing"] is True, f"игра прервалась сама: {off}"
    assert off["ticks"] >= 3, (
        f"шаг не пришёл ни разу на плеере без поля `step`: {off['ticks']} шагов")
    assert 5 in off["seekLog"], f"перескока через вырез не было: {off['seekLog']}"
    assert off["inCut"] == [], (
        f"проигран вырез предпросмотра вставок: {off['seekLog']}")

    on = _run_node("""
(()=>{
  visibility(false);                     // окно на виду: rAF приходит каждый кадр
  delete IPV.step;delete IPV.tim;delete IPV.raf;   // пересозданный IPV полей не несёт
  IV.currentTime=1.0;IV.seekLog.length=0;
  ipvPlay();
  VTTICK.length=0;
  const rafAfterPlay=RAFS.size,timAfterPlay=TIMERS.size;
  tickFrames(IV,10);
  report('bare_on',{rafAfterPlay:rafAfterPlay,timAfterPlay:timAfterPlay,ticks:VTTICK.length,
    timNow:TIMERS.size,playing:!!IPV.playing});
})();
""")
    assert on["rafAfterPlay"] == 1 and on["timAfterPlay"] == 1, (
        f"живой rAF и сторож не взведены рядом: {on}")
    assert on["ticks"] == 10, (
        f"на 10 кадров пришло {on['ticks']} шагов — токен шага не завёлся сам: {on}")
    assert on["playing"] is True, f"игра прервалась сама: {on}"
