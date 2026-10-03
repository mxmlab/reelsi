# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шаг 1: ОДИН плеер — редактор. Обработанный голос идёт за его плейхедом.

Решение владельца 02.10.2026: «сверху есть блок "монтаж", который по сути повторяет все
остальные функции и кнопки — убрать, звук только переместить» и «даже лучше, если будет
один плеер, два — это костыль». Баг, который это и показал: «если нажимаю далеко на
таймлайне — ползунок не переносится сразу, скачет по каждому срезу».

Что проверяется (в браузере это место не увидеть без живого сервера, клипа и плагинов,
а ломается молча):

1. клик по таймлайну во время игры ставит плейхед ТУДА ЖЕ и он там и остаётся — нет
   второго плеера, который возвращал бы его на начало следующего куска монтажа;
2. игра редактора ведёт дорожку обработанного голоса временем ED.cs (исходное время
   камеры 1 = время запечённого трека), а на вырезанном месте звук прыгает вместе с
   картинкой (edJump), а не доигрывает удалённый кусок;
3. в разметке нет блока «Монтаж» (#pvplay, #pvseek, #pvtime, #pvcam), а громкость и
   строка «камера — склеек — длина» живут в строке управления редактора.

Стенд гоняет БОЕВЫЕ функции под node: тела берутся из `static/app/70-editor.js` и
`static/app/60-preview.js` — копий в тесте нет (иначе стенд сторожил бы сам себя).

Запуск:  py -3.10 -m pytest tests/test_editor_single_player.py -q
"""
from __future__ import annotations

import json
import os
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

EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
HTML = ROOT / "templates" / "index.html"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Тела боевых функций: плеер шага 1 живёт в двух файлах — дверь показа и общий объект
# кадра (60-preview.js), часы и хоткеи (70-editor.js).
EDITOR_FUNCS = ("edOpen", "edResize", "edTotal", "edBlockAt", "edCutTime", "edSrcOf", "edCutOf",
                "edS2X", "edX2S", "edUI",
                "edRaw", "edInCut", "edWords", "edSeek", "edToggle", "edPlay", "edPause",
                "edTake", "edJump", "edArm", "edTick", "edBind")
PREVIEW_FUNCS = ("openPreview", "pvVideoTo", "pvWordAt", "pvSegAt", "bufMake", "bufIdle",
                 "bufArm", "bufRoll", "bufTake",
                 "bufSwap", "spareLead", "spareIdle", "spareStop", "spareSwap", "spareRollAt",
                 "camVisual", "camIdle", "camApply", "camTrack", "camDeltas", "camBufs",
                 "vtPlaying", "vtSrcAt", "vtNow", "vtAudioCam", "vtGate", "vtTick",
                 "vtIsPv", "vtIsEd", "vtMuteHost",
                 "pvSrc", "MEDIA_VOL")


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


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    out = []
    for name in PREVIEW_FUNCS:
        if name == "MEDIA_VOL":
            continue                      # это переменная, а не функция — объявим в стенде
        src = _func_src(preview, name)
        if name == "vtTick":              # боевую дорожку зовём из счётчика стенда
            src = src.replace("function vtTick(", "function realVtTick(", 1)
        out.append(src)
    for name in EDITOR_FUNCS:
        out.append(_func_src(editor, name))
    return "\n".join(out)


STAND = r"""
// ---- мини-DOM: ровно то, что нужно плееру шага 1 --------------------------------
const MADE=[];
class El{
  constructor(tag){this.tag=String(tag).toUpperCase();this.attrs={};this.dataset={};this.style={};
    this.children=[];this.parentElement=null;this.className='';this.id='';this._text='';this._html='';
    this.paused=true;this.played=0;this.pauseLog=0;this.seekLog=[];this.readyState=4;this.seeking=false;
    this.muted=false;this.volume=1;this.playbackRate=1;this._ct=0;this.classList={add(){},remove(){},contains:()=>false};}
  set currentTime(v){this._ct=+v||0;this.seekLog.push(this._ct);}
  get currentTime(){return this._ct;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k];}
  removeAttribute(k){delete this.attrs[k];}
  appendChild(c){c.parentElement=this;this.children.push(c);return c;}
  insertBefore(c,ref){c.parentElement=this;const i=ref?this.children.indexOf(ref):-1;
    if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c;}
  remove(){}
  closest(){return null;}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  addEventListener(){}
  getContext(){return CTX;}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  load(){}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  getBoundingClientRect(){return {left:0,top:0,width:1000,height:200};}
  get innerHTML(){return this._html;}
  set innerHTML(h){this._html=String(h);}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);}
}
const CTX={clearRect(){},fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fill(){},
  fillText(){},strokeRect(){},closePath(){},setTransform(){},save(){},restore(){}
  ,set fillStyle(v){},set strokeStyle(v){},set lineWidth(v){},set font(v){},set textBaseline(v){}};
const BY_ID={};
globalThis.document={createElement:t=>{const el=new El(t);MADE.push(el);return el;},
  addEventListener(){},body:new El('body'),activeElement:null,querySelectorAll:()=>[]};
function $(id){return BY_ID[id]||null;}
globalThis.devicePixelRatio=1;
// Состояние страницы, к которому обращаются боевые функции
let MEDIA_VOL=1;
const CALLS=[];                     // что плеер сказал дорожке голоса и живому окну
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+m);}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function fmtT(s){return String(Math.round((+s||0)*10)/10)+'s';}
function fmtIns(s){const x=Math.max(0,+s||0);return Math.floor(x/60)+':'+String(Math.floor(x%60)).padStart(2,'0');}
function ico(){return '';}
function errText(d){return (d&&(d.error||d.err))||'';}
function seqGap(list,i){const a=list[i],b=list[i+1];
  return (a&&b)?Math.abs(b.src-(a.src+(a.te-a.ts))):0;}
let PVPX={map:{}};
function clipByXml(){return {name:'01_clip.xml',label:'01_clip'};}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
// Дорожка обработанного голоса и живой хост плагинов: их двери подменяем счётчиками —
// проверяем, ЧЕМ плеер их зовёт, а не то, как они внутри играют звук.
let VTTICK=[],LIVE=[];
function vtOf(P){if(!P.vt)P.vt={on:true,el:new El('audio'),path:'C:/v.wav'};return P.vt;}
function vtTick(P,tm){VTTICK.push([P,+tm]);realVtTick(P,tm);}   // кто позван — проверяет тест
function vtPause(P){CALLS.push(['pause',P]);}
function vtLiveOn(){return false;}                    // окно плагина в стенде закрыто
function vtLiveUpdate(){return false;}
function vtLivePlay(P){LIVE.push(['play',P,+P.cs]);}
function vtLivePause(P){LIVE.push(['pause',P,0]);}
function voiceWiring(){}
function voiceFxStatus(){}
function mediaFree(){}
function camFramePlan(){return null;}
function pvProxyLoad(){return Promise.resolve(null);}
function pvProxyMerge(){return null;}
function pvProxyWatch(){}
function vtStop(){}
function vtPrep(){}
function voiceFxRender(){}
function vstFxList(){}
function voiceFxSepFill(){}
function pvVoicePanel(){}
// Рисование таймлайна в этом стенде не проверяется (это сторож другой — test_ui_static):
// здесь важны часы, показ ракурса и звук, поэтому холст заглушён.
function edDraw(){}
function renderClips(){}
function saveState(){}
function selClips(){return [];}
function progOpen(){}
function progStep(){}
function progDone(){}
function localQStart(){}
function localQSet(){}
function localQEnd(){}
function askConfirm(){return Promise.resolve(false);}
function clearHl(){return 0;}
function syncClipLists(){}
function renderClips1(){return 0;}
function renderClips2(){return 0;}
function qClipSum(){return '';}
function engLabel(e){return e;}
function subSkipped(){return '';}
function aiPost(){return Promise.resolve({});}
function insAfterAI(){return Promise.resolve('');}
function insLog(){}
function val(){return '';}
function sleep(){return Promise.resolve();}
function aiStepConc(){return 1;}
function runPool(){return Promise.resolve();}
function uiBusyGuard(){return false;}
function uiBusySet(){}
function markClipPlay(){}
function openSpeaker(){}
function selectAE(){}
function stopAll(){}
function updateStatus(){}
function loadWordsFor(){}
// Плеер — объект кадра PV (как в бою: камеры, дублёр, слова, EDL)
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,
  curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice'};
// Редактор: единственный плеер шага 1 (его поля ровно как в 70-editor.js)
let ED={xml:'',blocks:[],fps:60,cam:'C:/cam1.mp4',dur:0,peaks:[],pps:80,sel:-1,play:false,raw:false,
  raf:0,cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],brBand:0};
// Что вернуть на двери открытия превью и загрузки редактора
let CUT={audio:[{ts:0,te:70,src:0},{ts:70,te:120,src:80}],   // вырез 70..80 исходника
  cams:[{path:'C:/cam1.mp4',name:'A'}],segs:[{ts:0,te:70,src:0,ci:0},{ts:70,te:120,src:80,ci:0}],
  words:[{s:0,e:120,w:'привет'}],dur:120};
globalThis.fetch=async(url,opt)=>{
  const u=String(url);
  if(u==='/api/aicut_preview')return {json:async()=>JSON.parse(JSON.stringify(CUT))};
  if(u==='/api/editor_load')return {json:async()=>({keep:[[0,70],[80,120]],fps:60,cam:'C:/cam1.mp4'})};
  if(u.indexOf('/api/waveform')===0)return {json:async()=>({peaks:[0.1,0.2],pps:80,dur:120})};
  return {json:async()=>({ok:true})};
};
function requestAnimationFrame(){return 1;}
function cancelAnimationFrame(){}
const EDRULER=18;   // высота линейки, css px (как в 70-editor.js)
const PV_PREROLL=0.35,PV_SWAP_LO=-0.12,PV_SWAP_HI=0.5;   // окна дублёра (как в 60-preview.js)
const VT_DRIFT=0.15;                                     // допуск подводки звука (60-preview.js)
""" + _bodies() + r"""

// ---- один прогон «открыли клип и играем» ----------------------------------------
async function playFrom(at){
  const stage=new El('div');stage.id='pvstage';BY_ID.pvstage=stage;
  BY_ID.pvsub=new El('div');
  BY_ID.edtime=new El('span');BY_ID.edcam=new El('div');BY_ID.edplay=new El('button');
  BY_ID.pvvoice=new El('div');BY_ID.pvvoicespk=new El('span');
  BY_ID.edtl=new El('canvas');BY_ID.edtl.clientWidth=1000;BY_ID.edtl.clientHeight=200;
  await openPreview('C:/out/01_clip.xml');
  await edOpen();
  ED.raw=true;                                   // клик по таймлайну — куда угодно, в т.ч. далеко
  edSeek(at);                                    // клик по таймлайну: плейхед на месте клика
  edPlay();                                      // ...и только потом «играть»
  const livePlay=LIVE.slice();
  LIVE=[];
  VTTICK=[];
  return {v:PV.vids[0],el:vtOf(ED).el,livePlay:livePlay};
}
function ticks(n){for(let i=0;i<n;i++){ED.raf=0;edTick();}}
function report(name,extra){console.log(JSON.stringify(Object.assign({name:name},extra)));}
"""


def _run_node(body: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="single_player_") as d:
        path = Path(d) / "stand.js"
        path.write_text(STAND + "\n" + body, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 1. Клик далеко во время игры: плейхед и звук сразу на месте клика
# --------------------------------------------------------------------------- #
@node
def test_far_click_during_playback_keeps_the_playhead_there() -> None:
    """Баг владельца: «нажимаю далеко на таймлайне — ползунок скачет по каждому срезу».

    Играл монтажный плеер (PV), клик по #edtl звал edSeek, а pvTick в следующем кадре
    возвращал время на начало СЛЕДУЮЩЕГО куска монтажа — кусок за куском. Теперь плеер
    один: клик ставит ED.cs, и следующий кадр игры считает время от него же.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(55.9);
  r.el.seekLog.length=0;                         // интересен seek ПОСЛЕ клика, а не на открытии
  edSeek(55.9);                                  // клик по таймлайну ВО ВРЕМЯ игры
  const atClick=r.v.currentTime;                 // seek ушёл на место клика уже в edSeek
  const seekAtClick=VTTICK.slice(-1)[0];         // и звук позван туда же сразу
  const audioAtClick=r.el.currentTime;           // ...и <audio> реально стоит на этом месте
  const csAtClick=ED.cs;
  ticks(30);                                     // ~0.5 с игры (30 кадров по 1/60)
  report('far_click',{atClick:atClick,csAtClick:csAtClick,
    seekLogAtClick:(seekAtClick?seekAtClick[1]:null),audioAtClick:audioAtClick,
    cs:ED.cs,playing:!!ED.play,pvTick:(typeof pvTick==='function'),
    tickIsEd:(VTTICK.length>0&&VTTICK[VTTICK.length-1][0]===ED)});
})();
""")
    assert out["csAtClick"] == 55.9, out
    assert out["tickIsEd"] is True and out["atClick"] == 55.9, (
        f"живой <video> не переехал на место клика: {out}")
    assert out["seekLogAtClick"] == 55.9, (
        f"звук не позван на место клика сразу: {out}")
    assert out["audioAtClick"] == 55.9, (
        f"обработанный голос не переехал на место клика: {out}")
    assert out["cs"] >= 55.9, (
        f"плейхед откатился назад после клика (это и был баг «скачет»): {out}")
    assert out["playing"] is True, "игра прервалась на клике по таймлайну"
    assert out["pvTick"] is False, "вернулся второй плеер шага 1 (pvTick)"


# --------------------------------------------------------------------------- #
# 2. Игра редактора ведёт дорожку голоса временем ED.cs
# --------------------------------------------------------------------------- #
@node
def test_editor_play_drives_the_voice_track_with_the_editor_clock() -> None:
    """Голос клипа звучит в редакторе, а время звука — ED.cs (исходное время камеры 1).

    Тот же файл `<стем>.voice.wav` уезжает в AE, DRP и черновой рендер: он посчитан по
    звуку камеры 1 от её нуля, поэтому время запечённого трека и есть ED.cs. Второй двери
    для «где сейчас звук» нет — vtSrcAt/vtNow знают про редактор.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(40);
  const el=r.el;
  // Пять кадров игры: время видео едет, дорожка подводится к тем же местам
  for(let i=0;i<5;i++){r.v.currentTime=40+(i+1)/60;ED.raf=0;edTick();}
  const tms=VTTICK.map(x=>x[1]);
  report('clock',{tms:tms,allEd:VTTICK.every(x=>x[0]===ED),
    pausedVoice:CALLS.filter(c=>c[0]==='pause'&&c[1]===ED).length,
    srcAt:vtSrcAt(ED,ED.cs),cs:ED.cs,now:vtNow(ED),
    monotone:tms.every((x,i)=>i===0||x>=tms[i-1]),
    plays:el.played,elSrc:el.src});
})();
""")
    assert out["tms"], "игра редактора не зовёт дорожку голоса вовсе"
    assert out["allEd"] is True, "дорожка подводится не к плееру шага 1"
    assert out["pausedVoice"] == 0, (
        "игра редактора глушит дорожку обработанного голоса (вернулся vtPause)")
    assert abs(out["srcAt"] - out["cs"]) < 1e-9, (
        f"время звука не равно времени камеры 1 под плейхедом: {out}")
    assert abs(out["now"] - out["cs"]) < 1e-9, "часы плеера шага 1 — не ED.cs"
    assert out["monotone"] is True, f"время дорожки поехало назад на игре: {out}"


# --------------------------------------------------------------------------- #
# 3. Вырезанное место: звук прыгает вместе с картинкой
# --------------------------------------------------------------------------- #
@node
def test_voice_jumps_over_the_cut_with_the_picture() -> None:
    """На вырезанном обработанный голос молчит и прыгает вместе с картинкой (edJump).

    Раньше дорожку вёл монтажный плеер по своему таймлайну и на вырезанных местах
    «доезжала» своим ходом. Теперь единственный плеер пропускает вырез картинкой
    (edTick -> edJump) и обязан позвать звук туда же — в тот же кадр, не позже.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(69.5);
  ED.raw=false;                                     // режим «слушать монтаж»: вырезанное пропускаем
  // Кадр игры уже на последнем кадре блока: edTick обязан прыгнуть через вырез на 80
  r.v.currentTime=ED.blocks[0].s1-0.01;r.el.currentTime=ED.cs;ED.raf=0;
  VTTICK.length=0;
  edTick();
  const afterJump={cs:ED.cs,v:r.v.currentTime,vtt:VTTICK.map(x=>x[1]),
    lastIsEd:VTTICK.length>0&&VTTICK[VTTICK.length-1][0]===ED,
    audio:r.el.seekLog.slice(-1)[0]};
  // Плейхед в вырезанном и стоим (пауза): режим «слушать монтаж» — это вырез
  ED.play=false;edSeek(75);
  const inCut={inCut:edInCut(ED),raw:edRaw(ED),v:r.v.currentTime,cs:ED.cs};
  report('cut',{afterJump:afterJump,inCut:inCut});
})();
""")
    assert out["afterJump"]["cs"] == 80, (
        f"картинка не прыгнула через вырез: {out['afterJump']}")
    assert out["afterJump"]["vtt"] and 80 in out["afterJump"]["vtt"], (
        f"звук не прыгнул на начало следующего блока: {out['afterJump']}")
    assert out["afterJump"]["lastIsEd"] is True, (
        f"звук позвали не от плеера шага 1: {out['afterJump']}")
    assert out["afterJump"]["audio"] == 80, (
        f"обработанный голос не подведён к новому месту: {out['afterJump']}")
    assert out["inCut"] == {"inCut": True, "raw": False, "v": 75, "cs": 75}, out["inCut"]
    assert out["inCut"]["inCut"] is True and out["inCut"]["v"] == 75, out["inCut"]

# --------------------------------------------------------------------------- #
# 4. Живой хост плагинов: play и seek в редакторе
# --------------------------------------------------------------------------- #
@node
def test_editor_play_tells_the_live_host_to_play_and_seek() -> None:
    """Окно плагина открыто — «играть» и перемотка уходят ему от редактора.

    Позицию хоста берёт vtSrcAt/vtNow: пока это была дверь монтажного плеера (pvNow),
    редактор с окном плагина звучал бы из чужого места.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(20);
  const liveAfterPlay=r.livePlay;
  const atPlay=vtSrcAt(ED,vtNow(ED));
  edSeek(35);
  report('live',{afterPlay:liveAfterPlay.map(x=>[x[0],x[1]===ED,x[2]]),atPlay:atPlay,
    playSrcAt:vtSrcAt(ED,vtNow(ED)),calls:VTTICK.slice(-1)[0][0]===ED});
})();
""")
    assert out["afterPlay"] and out["afterPlay"][0][1] is True, (
        f"«играть» не ушло в живое окно от плеера шага 1: {out['afterPlay']}")
    assert out["afterPlay"][0][2] == 20, (
        f"«играть» ушло в окно не с места редактора: {out['afterPlay']}")
    assert out["atPlay"] == 20, "место для хоста не равно времени камеры 1 под плейхедом"
    assert out["playSrcAt"] == 35, out
    assert out["calls"] is True, "перемотка не позвала дорожку плеера шага 1"


# --------------------------------------------------------------------------- #
# 5. Разметка: блока «Монтаж» нет, громкость у редактора
# --------------------------------------------------------------------------- #
def test_markup_has_one_player_and_the_volume_moved_to_the_editor() -> None:
    """Блок «Монтаж» убран целиком, а звук и строка камер переехали к редактору.

    Решение владельца: «звук только переместить». Второго ползунка перемотки и второй
    кнопки «играть» в разметке быть не должно — состояние воспроизведения одно.
    """
    html = HTML.read_text(encoding="utf-8")
    for gone in ('id="pvplay"', 'id="pvseek"', 'id="pvtime"', 'id="pvcam"',
                 'class="edlbl">Монтаж</div>'):
        assert gone not in html, f"в разметке шага 1 остался блок «Монтаж»: {gone}"
    block = html[html.index('id="edplay"'):html.index('id="edsave"')]
    assert 'data-vol' in block, "громкость превью не переехала в строку управления редактора"
    assert 'id="edcam"' in block, "строка «камера — склеек — длина» пропала вместе с блоком"
    assert 'id="edtime"' in block, "в строке управления редактора нет времени"
    # Второго плеера нет и в коде: все монтажные pv*-двери удалены
    js = "".join((ROOT / "static" / "app" / f).read_text(encoding="utf-8")
                 for f in sorted(os.listdir(ROOT / "static" / "app")) if f.endswith(".js"))
    for dead in ("function pvPlay(", "function pvPause(", "function pvTick(",
                 "function pvScrub(", "function pvSeekTo(", "function pvUI(",
                 "function pvNow(", "function pvApplyVisual(", "function pvToggle("):
        assert dead not in js, f"мёртвая ссылка на второй плеер шага 1: {dead}"
