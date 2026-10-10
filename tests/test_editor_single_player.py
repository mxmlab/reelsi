# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шаг 1: ОДИН плеер — редактор. Звук — буфер Web Audio, он же часы плеера.

Решение владельца 02.10.2026: «сверху есть блок "монтаж", который по сути повторяет все
остальные функции и кнопки — убрать, звук только переместить» и «даже лучше, если будет
один плеер, два — это костыль». Баг, который это и показал: «если нажимаю далеко на
таймлайне — ползунок не переносится сразу, скачет по каждому срезу».

Что проверяется (в браузере это место не увидеть без живого сервера, клипа и плагинов,
а ломается молча):

1. клик по таймлайну во время игры ставит плейхед ТУДА ЖЕ и он там и остаётся — нет
   второго плеера, который возвращал бы его на начало следующего куска монтажа;
2. ЧАСЫ — ЗВУК. Звук клипа лежит в AudioBuffer, и на «Играть» каждый оставленный блок
   встаёт в очередь узлом `start(когда, откуда, сколько)`; ED.cs считается из того, что
   звучит, а немое видео догоняет звук. Так ушли все три жалобы 2026-10-09 разом: голос
   «плыл» (его подгоняли к картинке скоростью ±6 %, 63 смены за 40 с), терял куски на
   промахе дублёра и молчал, пока картинка доезжала перемоткой;
3. очередь совпадает с блоками правки, вырез пропускается, «слушать вырезанное» играет
   подряд; правка блоков на ходу и смена буфера (готов обработанный голос) пересобирают
   очередь от того места, что звучит;
4. ползунок громкости меняет звук редактора (жалоба «работает не всегда»: с обработанным
   голосом громкость не менялась вовсе — плеер шага 1 не входил в applyMediaVol);
5. <video> шага 1 немые всегда: ни гейт голоса, ни пуск, ни подмена дублёром не
   возвращают звук камеры поверх звука редактора;
6. в разметке нет блока «Монтаж» (#pvplay, #pvseek, #pvtime, #pvcam), а громкость и
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
                "edWords", "edSeek", "edToggle", "edPlay", "edPause",
                "edTake", "edJump", "edVideoSeek", "edFollow", "edArm", "edTick", "edBind",
                "edSplit", "edDelSel", "edPush", "edUndo")
PREVIEW_FUNCS = ("openPreview", "pvVideoTo", "pvWordAt", "pvSegAt", "bufMake", "bufIdle",
                 "bufSilent", "bufArm", "bufRoll", "bufTake",
                 "bufSwap", "spareLead", "spareIdle", "spareStop", "spareSwap", "spareRollAt",
                 # Гашение камерного разбега (spareStop) зовёт и гашение дублёра голоса.
                 # `vtOf` здесь НЕ вырезаем: у стенда своя дверь дорожки (ниже).
                 "vtSpareOf", "vtSpareLive", "vtSpareIdle", "vtSpareStop", "vtSpareAt", "vtSpareArm",
                 "vtSpareRoll", "vtSpareTake", "vtSpareSwap", "vtSpareCtl",
                 "voicePrime",
                 "camVisual", "camIdle", "camApply", "camTrack", "camDeltas", "camBufs",
                 "vtPlaying", "vtSrcAt", "vtNow", "vtAudioCam", "vtGate", "vtTick",
                 "vtSeek", "vtRate", "vtUse", "vtDetach",
                 "vtIsPv", "vtIsEd", "vtMuteHost",
                 # Звук редактора: буфер, очередь, часы, гейн — боевые тела.
                 "eaOpen", "eaDecode", "eaVoice", "eaBuf", "eaSig", "eaPlan", "eaNow", "eaStop",
                 "eaStart", "eaClock", "eaSync", "eaGain", "dbToGain",
                 "setMediaVol", "applyMediaVol", "syncVolUI",
                 "pvSrc", "MEDIA_VOL")
# Объявления из файлов (не копии): константы звука и видео-подгонки и состояние EA.
PREVIEW_DECLS = ("EA_LEAD", "EA_FADE", "EA")
EDITOR_DECLS = ("ED_V_SOFT", "ED_V_HARD", "ED_V_RATE", "ED_ARM")


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
    """Объявление `const|let NAME=…;` из файла — и однострочное, и литерал на несколько
    строк (`let EA={…};`): берём до `;` в конце строки на нулевой глубине скобок."""
    m = re.search(r"^(?:const|let) %s=" % re.escape(name), src, re.M)
    assert m is not None, f"нет объявления {name}"
    depth = 0
    for i in range(m.end(), len(src)):
        c = src[i]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == ";" and depth == 0:
            return src[m.start():i + 1]
    raise AssertionError(f"не нашёлся конец объявления {name}")


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    out = [_decl(preview, n) for n in PREVIEW_DECLS] + [_decl(editor, n) for n in EDITOR_DECLS]
    # Пороги синхрона дорожки — ИЗ ФАЙЛА: свои копии в стенде разъезжались бы с
    # боевыми молча (перемотка становится скоростью — на этом и попались).
    for name in ("VT_SOFT", "VT_DRIFT", "VT_RATE", "VT_QUIET", "VT_POLL"):
        m = re.search(r"^const %s=.*$" % name, preview, re.M)
        assert m is not None, f"в 60-preview.js нет const {name}"
        out.append(m.group(0))
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
let IPV=null,CPV=null;               // другие плееры applyMediaVol: в стенде их нет
let CURSTYLE={voice_db:0};           // громкость голоса стиля: 0 дБ — множитель 1
globalThis.localStorage={setItem(){},getItem(){return null;}};
// ---- поддельный Web Audio: часы двигает стенд, узлы помнят, куда и как их поставили ----
class FParam{constructor(v){this.value=v;this.ev=[];}
  setValueAtTime(v,t){this.ev.push(['set',v,t]);}
  linearRampToValueAtTime(v,t){this.ev.push(['ramp',v,t]);}}
const SRCS=[];                       // все узлы-источники, по порядку создания
class FNode{constructor(kind){this.kind=kind;this.out=[];this.gain=new FParam(1);
    this.buffer=null;this.started=null;this.stopped=false;}
  connect(n){this.out.push(n);return n;}
  disconnect(){this.out=[];}
  start(when,off,dur){this.started={when:when,off:off,dur:dur};}
  stop(){this.stopped=true;}}
const RAWBUF={duration:120,name:'raw'},PROCBUF={duration:120,name:'proc'};
const ACTX={state:'running',currentTime:100,outputLatency:0,destination:new FNode('dest'),
  createGain(){return new FNode('gain');},
  createBufferSource(){const n=new FNode('src');SRCS.push(n);return n;},
  decodeAudioData(ab){return Promise.resolve(ab&&ab.proc?PROCBUF:RAWBUF);},
  resume(){return Promise.resolve();}};
let AUDIO=null;
function audioGraph(){AUDIO=ACTX;}
function audioWake(){audioGraph();}
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
// Живые (не остановленные) узлы — то, что сейчас стоит в очереди звука.
function live(){return SRCS.filter(n=>!n.stopped&&n.started);}
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
function vtOf(P){if(!P.vt)P.vt={on:true,el:new El('audio'),path:'C:/v.wav',
  vsp:null,vspAt:null};return P.vt;}
function vtTick(P,tm){VTTICK.push([P,+tm]);realVtTick(P,tm);}   // кто позван — проверяет тест
function vtPause(P){CALLS.push(['pause',P]);}
function vtNote(){}
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
  curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice',silent:true};
// Редактор: единственный плеер шага 1 (его поля ровно как в 70-editor.js)
let ED={xml:'',blocks:[],fps:60,cam:'C:/cam1.mp4',dur:0,peaks:[],pps:80,sel:-1,play:false,raw:false,
  raf:0,cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],brBand:0,seekLead:0.12};
// Что вернуть на двери открытия превью и загрузки редактора
let CUT={audio:[{ts:0,te:70,src:0},{ts:70,te:120,src:80}],   // вырез 70..80 исходника
  cams:[{path:'C:/cam1.mp4',name:'A'}],segs:[{ts:0,te:70,src:0,ci:0},{ts:70,te:120,src:80,ci:0}],
  words:[{s:0,e:120,w:'привет'}],dur:120};
globalThis.fetch=async(url,opt)=>{
  const u=String(url);
  if(u==='/api/aicut_preview')return {json:async()=>JSON.parse(JSON.stringify(CUT))};
  if(u==='/api/editor_load')return {json:async()=>({keep:[[0,70],[80,120]],fps:60,cam:'C:/cam1.mp4'})};
  if(u.indexOf('/api/waveform')===0)return {json:async()=>({peaks:[0.1,0.2],pps:80,dur:120})};
  if(u==='/api/preview_audio')return {json:async()=>({ok:true,path:'C:/out/_tmp/pa_cam1.wav'})};
  if(u.indexOf('/api/media?')===0)return {arrayBuffer:async()=>({proc:u.indexOf('voice')>=0})};
  return {json:async()=>({ok:true})};
};
function requestAnimationFrame(){return 1;}
function cancelAnimationFrame(){}
const EDRULER=18;   // высота линейки, css px (как в 70-editor.js)
const PV_PREROLL=0.35,PV_SWAP_LO=-0.12,PV_SWAP_HI=0.5;   // окна дублёра (как в 60-preview.js)
const VT_SWAP_LO=-0.06,VT_SWAP_HI=0.25;   // допуск подмены дорожки голоса (как в 60-preview.js)
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
  await tick();                                  // звук камеры 1 вынут и декодирован (eaOpen)
  ED.raw=true;                                   // клик по таймлайну — куда угодно, в т.ч. далеко
  edSeek(at);                                    // клик по таймлайну: плейхед на месте клика
  edPlay();                                      // ...и только потом «играть»
  const livePlay=LIVE.slice();
  LIVE=[];
  VTTICK=[];
  return {v:PV.vids[0],el:vtOf(ED).el,livePlay:livePlay};
}
// Кадр игры: часы звука уходят на 1/60 с вперёд, редактор делает шаг.
function ticks(n){for(let i=0;i<n;i++){ACTX.currentTime+=1/60;ED.raf=0;edTick();}}
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
# 1. Клик далеко во время игры: плейхед, картинка и очередь звука — на месте клика
# --------------------------------------------------------------------------- #
@node
def test_far_click_during_playback_keeps_the_playhead_there() -> None:
    """Баг владельца: «нажимаю далеко на таймлайне — ползунок скачет по каждому срезу».

    Играл монтажный плеер (PV), клик по #edtl звал edSeek, а pvTick в следующем кадре
    возвращал время на начало СЛЕДУЮЩЕГО куска монтажа — кусок за куском. Теперь плеер
    один: клик ставит ED.cs, картинку и очередь звука, и следующий кадр считает время от
    того, что звучит с места клика.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(55.9);
  edSeek(55.9);                                  // клик по таймлайну ВО ВРЕМЯ игры
  const atClick=r.v.currentTime;                 // картинка ушла на место клика уже в edSeek
  const q=live();                                // очередь звука пересобрана с места клика
  const csAtClick=ED.cs;
  ticks(30);                                     // ~0.5 с игры (30 кадров по 1/60)
  report('far_click',{atClick:atClick,csAtClick:csAtClick,
    firstOff:q.length?q[0].started.off:null,queued:q.length,
    cs:ED.cs,playing:!!ED.play,pvTick:(typeof pvTick==='function')});
})();
""")
    assert out["csAtClick"] == 55.9 and out["atClick"] == 55.9, (
        f"живой <video> не переехал на место клика: {out}")
    assert out["firstOff"] == 55.9, f"звук после клика играет не с места клика: {out}"
    assert out["cs"] >= 55.9, (
        f"плейхед откатился назад после клика (это и был баг «скачет»): {out}")
    assert out["playing"] is True, "игра прервалась на клике по таймлайну"
    assert out["pvTick"] is False, "вернулся второй плеер шага 1 (pvTick)"


# --------------------------------------------------------------------------- #
# 2. Часы — звук: ED.cs идёт за тем, что звучит, а не за <video>
# --------------------------------------------------------------------------- #
@node
def test_the_playhead_follows_the_audio_clock_not_the_video() -> None:
    """ED.cs = исходное время того, что сейчас звучит; видео догоняет, а не ведёт.

    Раньше часами был currentTime <video>, а звук подгоняли к нему скоростью ±6 % — голос
    «плыл». Теперь наоборот: видео немое, его скорость не слышна, и подгоняется оно.
    Проверка: видео нарочно уводим далеко — плейхед его не слушает, а видео прыгает к звуку.
    """
    out = _run_node("""
(async()=>{
  const r=await playFrom(40);
  const t0=EA.t0;
  ticks(6);                                      // 0.1 с часов звука
  const cs1=ED.cs,want1=40+(ACTX.currentTime-t0);
  r.v.currentTime=10;                            // видео «убежало» — часы не должны поехать за ним
  ticks(1);
  const want2=40+(ACTX.currentTime-t0);
  report('clock',{cs1:cs1,want1:want1,cs2:ED.cs,want2:want2,v:PV.vids[0].currentTime,
    lead:ED.seekLead,muted:PV.vids.every(v=>v.muted)});
})();
""")
    assert abs(out["cs1"] - out["want1"]) < 1e-6, f"плейхед не по часам звука: {out}"
    assert abs(out["cs2"] - out["want2"]) < 1e-6, (
        f"плейхед поехал за убежавшим видео, а не за звуком: {out}")
    # Видео прыгнуло к звуку с упреждением на перемотку (звук за это время уйдёт вперёд).
    assert abs(out["v"] - (out["want2"] + out["lead"])) < 1e-6, (
        f"видео не догнало звук перемоткой с упреждением: {out}")
    assert out["muted"] is True, f"видео шага 1 зазвучало: {out}"


# --------------------------------------------------------------------------- #
# 3. Очередь = блоки правки; вырез пропускается; «слушать вырезанное» — подряд
# --------------------------------------------------------------------------- #
@node
def test_the_queue_matches_the_blocks_and_skips_the_cut() -> None:
    """Каждый оставленный блок — свой узел: старт встык, смещение и длина — из блока.

    Блоки клипа стенда: [0,70] и [80,120], вырез 70..80. С места 60 очередь — два узла:
    60..70 сейчас и 80..120 ровно через 10 с. Ни перемотки, ни подгонки скоростью: стык
    звучит с точностью до сэмпла. Часы за стыком дают 80+, и картинка прыгает туда же.
    В режиме «слушать вырезанное» звук идёт одним куском от места до конца.
    """
    out = _run_node("""
(async()=>{
  await playFrom(60);
  edPause();ED.raw=false;edSeek(60);edPlay();
  const t0=EA.t0,q=live().map(n=>({w:+(n.started.when-t0).toFixed(6),o:n.started.off,
    d:+n.started.dur.toFixed(6),buf:n.buffer&&n.buffer.name}));
  ACTX.currentTime=t0+10.05;ED.raf=0;edTick();  // часы звука уже за стыком
  const after={cs:+ED.cs.toFixed(6),v:PV.vids[0].currentTime};
  edPause();ED.raw=true;edSeek(60);edPlay();
  const raw=live().map(n=>({o:n.started.off,d:+n.started.dur.toFixed(6)}));
  report('queue',{q:q,after:after,raw:raw});
})();
""")
    assert out["q"] == [{"w": 0, "o": 60, "d": 10, "buf": "raw"},
                        {"w": 10, "o": 80, "d": 40, "buf": "raw"}], (
        f"очередь звука не совпадает с блоками правки: {out['q']}")
    assert out["after"]["cs"] == 80.05, f"плейхед не перешёл стык по часам звука: {out}"
    assert out["after"]["v"] >= 80.05, f"картинка не прыгнула через вырез: {out}"
    assert out["raw"] == [{"o": 60, "d": 60}], (
        f"«слушать вырезанное» играет не подряд до конца: {out['raw']}")


@node
def test_an_edit_during_playback_rebuilds_the_queue_from_what_sounds() -> None:
    """Правка блоков на ходу (тут — ✂ и удаление) пересобирает очередь со звучащего места.

    Правок у редактора много (тяга края, ✂, удаление, Ctrl+Z, возврат щели, вырез вздоха),
    и у каждой своя дверь. Очередь пересобирает не каждая из них, а сверка подписи блоков
    на кадре — так ни одна дверь не забудется. Старые узлы обязаны остановиться: иначе
    удалённый кусок прозвучал бы поверх нового.
    """
    out = _run_node("""
(async()=>{
  await playFrom(20);
  edPause();ED.raw=false;edSeek(20);edPlay();
  const old=live();
  ticks(6);
  ED.cs=30;edSplit();ED.cs=20.1;                 // ✂ на 30: блок [0,70] -> [0,30],[30,70]
  ED.sel=1;edDelSel();                           // ...и удаление [30,70]
  ticks(1);
  const q=live().map(n=>({o:+n.started.off.toFixed(3),d:+n.started.dur.toFixed(3)}));
  report('edit',{oldStopped:old.every(n=>n.stopped),q:q,blocks:ED.blocks.length});
})();
""")
    assert out["oldStopped"] is True, f"прежняя очередь звучит поверх новой: {out}"
    assert len(out["q"]) == 2 and out["q"][1] == {"o": 80, "d": 40}, (
        f"после удаления блока очередь не пересобрана: {out}")
    assert abs(out["q"][0]["o"] + out["q"][0]["d"] - 30) < 1e-3, (
        f"первый кусок не кончается на новом крае блока (30): {out}")


@node
def test_the_ready_voice_replaces_the_camera_sound_in_the_queue() -> None:
    """Обработанный голос готов (vtUse) — очередь встаёт на него с того же места.

    Пока голос печётся, звучит звук камеры 1 (буфер `raw`). Готов — подмена буфера, и
    пересобирает её та же сверка на кадре. Сняли обработку (vtDetach) — снова звук камеры.
    """
    out = _run_node("""
(async()=>{
  await playFrom(10);
  const before=live().map(n=>n.buffer.name);
  vtOf(ED).path='';vtUse(ED,'C:/out/01_clip.voice.ab12.wav',null,true);
  await tick();ticks(1);
  const withVoice=live().map(n=>n.buffer.name);
  vtDetach(ED);ticks(1);
  const back=live().map(n=>n.buffer.name);
  report('voice',{before:before,withVoice:withVoice,back:back,cs:ED.cs});
})();
""")
    assert set(out["before"]) == {"raw"}, out
    assert out["withVoice"] and set(out["withVoice"]) == {"proc"}, (
        f"готовый обработанный голос не встал в очередь: {out}")
    assert out["back"] and set(out["back"]) == {"raw"}, (
        f"после снятия обработки звук камеры не вернулся: {out}")


@node
def test_the_end_of_the_queue_stops_the_clip() -> None:
    """Очередь кончилась — пауза, плейхед в начало, узлов в очереди нет."""
    out = _run_node("""
(async()=>{
  await playFrom(10);
  edPause();ED.raw=false;edSeek(115);edPlay();
  ACTX.currentTime=EA.t0+5.5;ED.raf=0;edTick();
  report('end',{play:ED.play,cs:ED.cs,queued:live().length});
})();
""")
    assert out == {"name": "end", "play": False, "cs": 0, "queued": 0}, out


# --------------------------------------------------------------------------- #
# 4. Громкость: ползунок двигает звук редактора
# --------------------------------------------------------------------------- #
VOLUME_BODY = """
(async()=>{
  await playFrom(10);
  const g0=EA.gain.gain.value;
  setMediaVol(30);
  const g30=EA.gain.gain.value;
  CURSTYLE.voice_db=6;applyMediaVol();           // громкость голоса стиля — тем же гейном
  const g30db=EA.gain.gain.value;
  report('vol',{g0:g0,g30:g30,g30db:g30db,edVt:vtOf(ED).el.volume});
})();
"""


@node
def test_the_volume_slider_moves_the_editor_sound() -> None:
    """Жалоба: «ползунок громкости в редакторе работает не всегда».

    Причина: applyMediaVol проходил по [PV, IPV, CPV], а дорожка обработанного голоса шага 1
    жила на ED — её громкость ставилась один раз при создании. Без обработки звучал <video>
    из PV, и ползунок работал; с обработкой — нет. Теперь звук редактора — свой гейн, и
    ползунок, и громкость голоса стиля ставят его одной формулой.
    """
    out = _run_node(VOLUME_BODY)
    assert abs(out["g0"] - 1) < 1e-9, out
    assert abs(out["g30"] - 0.3) < 1e-9, f"ползунок 30 % не дошёл до звука редактора: {out}"
    assert abs(out["g30db"] - 0.3 * 10 ** (6 / 20)) < 1e-9, (
        f"громкость голоса стиля не дошла до звука редактора: {out}")
    assert abs(out["edVt"] - 0.3) < 1e-9, f"плеер шага 1 выпал из applyMediaVol: {out}"


@node
def test_the_volume_mutation_turns_the_slider_test_red() -> None:
    """Мутация: убрать звук редактора из applyMediaVol — тест ползунка обязан покраснеть."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    body = _func_src(preview, "applyMediaVol")
    mutant = body.replace("if(typeof eaGain==='function')eaGain();", "")
    assert mutant != body, "в applyMediaVol не нашёлся вызов eaGain — мутация пуста"
    script = STAND.replace(body, mutant) + "\n" + VOLUME_BODY
    import tempfile
    with tempfile.TemporaryDirectory(prefix="single_player_mut_") as d:
        path = Path(d) / "stand.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    out = json.loads([x for x in proc.stdout.splitlines() if x.strip()][-1])
    assert abs(out["g30"] - 0.3) > 0.1, f"мутация не поймана — тест ползунка слепой: {out}"


# --------------------------------------------------------------------------- #
# 5. <video> шага 1 немые: звучит только буфер редактора
# --------------------------------------------------------------------------- #
SILENT_BODY = """
(async()=>{
  await playFrom(10);
  const seen=[];
  const all=()=>PV.vids.concat((PV.bufs||[]).map(b=>b.el));
  for(let k=0;k<5;k++){ticks(12);seen.push(all().every(v=>v.muted));}
  vtGate(ED,false);seen.push(all().every(v=>v.muted));   // гейт голоса «звук камере»
  vtGate(ED,true);seen.push(all().every(v=>v.muted));
  camVisual(PV,0,true);seen.push(all().every(v=>v.muted));
  edPause();edPlay();seen.push(all().every(v=>v.muted));
  report('silent',{seen:seen});
})();
"""


@node
def test_step1_videos_stay_muted_whatever_opens_the_gate() -> None:
    """Ровно один источник голоса: у шага 1 это буфер, а все <video> немые.

    Писателей `muted` у <video> много (ракурс camVisual, гейт голоса vtGate, живой хост,
    пуск редактора, подмена дублёром) — каждый обязан учесть немой кадр (`PV.silent`).
    Иначе звук камеры зазвучал бы поверх звука редактора: два голоса со сдвигом.
    """
    out = _run_node(SILENT_BODY)
    assert all(out["seen"]), f"какая-то дверь вернула звук <video> шага 1: {out}"


@node
def test_the_gate_mutation_turns_the_silent_test_red() -> None:
    """Мутация: гейт голоса без учёта немого кадра — тест немоты обязан покраснеть."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    body = _func_src(preview, "vtGate")
    mutant = body.replace("v.muted=live||!!M.silent;", "v.muted=live;")
    assert mutant != body, "в vtGate не нашлась проверка немого кадра — мутация пуста"
    script = STAND.replace(body, mutant) + "\n" + SILENT_BODY
    import tempfile
    with tempfile.TemporaryDirectory(prefix="single_player_mut_") as d:
        path = Path(d) / "stand.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    out = json.loads([x for x in proc.stdout.splitlines() if x.strip()][-1])
    assert not all(out["seen"]), f"мутация не поймана — тест немоты слепой: {out}"


# --------------------------------------------------------------------------- #
# 6. Живой хост плагинов: play и seek в редакторе
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
# 7. Разметка: блока «Монтаж» нет, громкость у редактора
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
