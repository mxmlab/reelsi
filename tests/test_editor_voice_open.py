# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Открытие превью шага 1: панель «Голос» И клип, а не «У клипа нет спикера».

Плеер шага 1 — редактор нарезки (ED), и дорожка обработанного голоса живёт на нём:
спикера панель берёт по `ED.xml`, файл камеры 1 — по `ED.cams`, а сам заказ уходит
дверью `/api/voicefx_bake` (итоговый голос с цепочкой; живой хост плагинов поднимает
отдельная дверь `/api/voicefx_host` — и только при открытом окне плагина).

Порядок при открытии один и он же — суть бага: `openPreview` (клип и камеры) → `edOpen`
(`ED.xml`) → `pvVoicePanel` (панель и заказ). Пока панель звалась в конце `openPreview`,
она читала ЕЩЁ ПРЕЖНИЙ `ED.xml`: у клипа со спикером рисовалось «У клипа нет спикера»,
а заказа трека не было вовсе — ни `/api/voicefx_bake`, ни `/api/voicefx_host`
(живая проверка архитектора).

Стенд гоняет БОЕВЫЕ функции под node: `openEditClip`/`openPreview`/`vt*` из
`static/app/60-preview.js`, `ed*` из `static/app/70-editor.js`, панель и хост из
`static/app/95-styles.js`. Тела берутся из файлов, копий в тесте нет.

Запуск:  py -3.10 -m pytest tests/test_editor_voice_open.py -q
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
STYLES_JS = ROOT / "static" / "app" / "95-styles.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Панель «Голос» и подъём живого хоста (95-styles.js)
VOICE_FUNCS = (
    "voiceFxHost", "vfxEl", "vstTitle", "voiceFxLive", "voiceFxOn", "voiceFxLiveOn",
    "voiceFxWindowOn", "voiceFxHostOn", "voiceFxHasVst", "voiceFxRender", "voiceFxRead", "voiceFxStrength",
    "voiceFxOther", "voiceFxStatus", "vfxEngine", "vfxEngineSel", "vfxEngName",
    "vfxStrengthLabel", "vfxStrengthHint", "vfxAltDefault", "vstRow", "vstFxNote",
    "vfxDnSummaryText", "vfxDnToggle", "vfxDnUpdateSummary",
    "voiceFxHostKey", "voiceFxHostPost", "voiceFxHostSync", "voiceFxHostStart",
    "voiceFxHostPoll", "voiceFxHostNotes", "voiceFxHostGone", "voiceFxHostStop",
    "pvVoicePanel",
)
# Дорожка голоса и объект кадра (60-preview.js)
PREVIEW_FUNCS = (
    "openEditClip", "openPreview", "pvVideoTo", "pvWordAt", "pvSegAt", "pvSrc",
    "bufMake", "bufIdle", "bufArm", "bufRoll", "bufTake", "bufSwap", "spareLead",
    "spareIdle", "spareStop", "spareSwap", "spareRollAt",
    # Гашение камерного разбега (spareStop) зовёт и гашение дублёра голоса.
    "vtSpareOf", "vtSpareLive", "vtSpareIdle", "vtSpareStop", "vtSpareAt", "vtSpareArm",
    "vtSpareRoll", "vtSpareTake", "vtSpareSwap", "vtSpareCtl", "voicePrime",
    "camVisual", "camIdle", "camApply", "camTrack", "camDeltas", "camBufs",
    "vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying", "vtIsPv", "vtIsEd",
    "vtMuteHost", "vtStage", "vtEl", "vtNote", "vtName", "vtProfileFx", "vtFx", "vtDetach",
    "vtStop", "vtPause", "vtGate", "vtLiveOn", "vtSetMute", "vtLiveUpdate", "vtLiveRate", "vtLiveExpect",
    "vtLiveCmd", "vtLiveSid", "vtLivePlay", "vtLivePause", "vtPrep", "vtVoiceShow",
    "vtVoiceWatch", "vtVoicePoll", "vtVoiceLine", "vtVoiceStop", "vtVoiceTake", "vtVoiceUse",
    "vtUse", "vtTick", "vtSeek", "vtRate",
    # Звук редактора шага 1: буфер Web Audio, очередь блоков, часы, гейн.
    "eaOpen", "eaDecode", "eaVoice", "eaBuf", "eaSig", "eaPlan", "eaNow", "eaStop",
    "eaStart", "eaClock", "eaSync", "eaGain", "dbToGain",
    "vtHostLive", "vtLivePrep", "vtStatesWait", "vtHostDown", "pvProgRow", "pvProgDrop",
)
# Единственный плеер шага 1 — редактор (70-editor.js)
EDITOR_FUNCS = (
    "edOpen", "edResize", "edTotal", "edBlockAt", "edCutTime", "edCutOf", "edS2X", "edX2S",
    "edUI", "edWords", "edSeek", "edToggle", "edPlay", "edPause",
    "edTake", "edJump", "edVideoSeek", "edFollow", "edArm", "edTick",
)


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


def _ed_literal() -> str:
    """Объявление плеера шага 1 — ИЗ ФАЙЛА, а не копия в стенде.

    Своей копии у стенда нет нарочно: `cams` (файл камеры клипа для дорожки голоса) и
    `voicePanel` (панель, чьи РУЧКИ читает vtFx) — это и есть связь плеера с клипом, и
    потерять их молча он не должен.
    """
    js = EDITOR_JS.read_text(encoding="utf-8")
    start = js.index("let ED={")
    depth = 0
    for i in range(start + len("let ED="), len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                lit = js[start:i + 1] + ";"
                assert "cams:null" in lit and "voicePanel:'pvvoice'" in lit, \
                    "у плеера шага 1 пропали cams/voicePanel — дорожка голоса снова без клипа: " + lit
                return lit
    raise AssertionError("не нашлось объявление let ED={…} в 70-editor.js")


def _vfxhost_literal() -> str:
    """Состояние живого хоста (95-styles.js) — из файла: у стенда своей копии нет."""
    js = STYLES_JS.read_text(encoding="utf-8")
    m = re.search(r"^let VFXHOST=\{[^\n]*\};", js, re.M)
    assert m, "не нашлось состояние хоста VFXHOST"
    return m.group(0)


def _bodies() -> str:
    styles = STYLES_JS.read_text(encoding="utf-8")
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    out = [_func_src(styles, n) for n in VOICE_FUNCS]
    # Пороги синхрона дорожки — ИЗ ФАЙЛА: свои копии в стенде разъезжались бы с
    # боевыми молча (перемотка становится скоростью — на этом и попались). Допуск
    # подмены дублёра голоса (VT_SWAP_*) и пороги звука редактора (EA_*, ED_V_*) — оттуда же.
    for name in ("VT_SOFT", "VT_DRIFT", "VT_RATE", "VT_QUIET", "VT_POLL",
                 "VT_SWAP_LO", "VT_SWAP_HI", "EA_LEAD", "EA_FADE"):
        m = re.search(r"^const %s=.*$" % name, preview, re.M)
        assert m is not None, f"в 60-preview.js нет const {name}"
        out.append(m.group(0))
    for name in ("ED_V_SOFT", "ED_V_HARD", "ED_V_RATE", "ED_ARM"):
        m = re.search(r"^const %s=.*$" % name, editor, re.M)
        assert m is not None, f"в 70-editor.js нет const {name}"
        out.append(m.group(0))
    # Состояние звука редактора `let EA={…};` — литерал на две строки, берём до `};`.
    a = preview.index("let EA={")
    out.append(preview[a:preview.index("};", a) + 2])
    for name in PREVIEW_FUNCS:
        src = _func_src(preview, name)
        if name == "vtTick":              # боевую дорожку зовём из счётчика стенда
            src = src.replace("function vtTick(", "function realVtTick(", 1)
        out.append(src)
    out += [_func_src(editor, n) for n in EDITOR_FUNCS]
    return "\n".join(out)


DOM = r"""
// ---- мини-DOM: панель строит разметку строкой, плееру нужен элемент звука ----
function _attrsOf(s){
  const out={};const rx=/([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;let m;
  while((m=rx.exec(s||'')))out[m[1]]=(m[2]===undefined?'':m[2]);
  return out;}
function _dataKey(k){return k.slice(5).replace(/-(\w)/g,(s,c)=>c.toUpperCase());}
class El{
  constructor(tag){this.tag=String(tag).toUpperCase();this.attrs={};this.children=[];this.parentElement=null;
    this.dataset={};this.style={};this.value='';this.checked=false;this.options=[];
    this.className='';this.src='';this.volume=1;this.readyState=0;this._text='';this._html='';
    this.paused=true;this.played=0;this.currentTime=0;this.seeking=false;this.muted=false;
    this.playbackRate=1;this.seekLog=[];this.pauseLog=0;this.width=0;this.height=0;
    this.clientWidth=0;this.clientHeight=0;
    this.classList={add(){},remove(){},contains:()=>false};}
  get nextSibling(){if(!this.parentElement)return null;
    const s=this.parentElement.children;return s[s.indexOf(this)+1]||null;}
  setAttribute(k,v){this.attrs[k]=String(v);
    if(k.indexOf('data-')===0)this.dataset[_dataKey(k)]=String(v);}
  getAttribute(k){return this.attrs[k];}
  removeAttribute(k){delete this.attrs[k];if(k==='src')this.src='';}
  appendChild(c){c.parentElement=this;this.children.push(c);return c;}
  insertBefore(c,ref){c.parentElement=this;const i=ref?this.children.indexOf(ref):-1;
    if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c;}
  remove(){if(!this.parentElement)return;const s=this.parentElement.children;
    const i=s.indexOf(this);if(i>=0)s.splice(i,1);this.parentElement=null;}
  closest(sel){const key=sel.replace(/^\[|\]$/g,'');let el=this;
    while(el){if(matchSel(el,key))return el;el=el.parentElement;}return null;}
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  querySelectorAll(sel){const key=sel.replace(/^\[|\]$/g,'');const out=[];
    const walk=e=>{for(const c of e.children){if(matchSel(c,key))out.push(c);walk(c);}};
    walk(this);return out;}
  addEventListener(){}play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  load(){}focus(){}blur(){}
  getBoundingClientRect(){return {left:0,top:0,width:1000,height:200};}
  get innerHTML(){return this._html;}
  set innerHTML(h){this._html=String(h);this.children=[];parseInto(this,String(h));}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);}
}
const VOID_TAGS={'input':1,'br':1,'img':1,'hr':1,'meta':1,'link':1,'option':1};
function matchSel(el,key){
  const m=/^([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?$/.exec(key);
  if(!m)return false;
  const val=(m[2]!==undefined?m[2]:(m[3]!==undefined?m[3]:null));
  return val===null?(el.attrs[m[1]]!==undefined):(el.attrs[m[1]]===val);}
function parseInto(root,html){
  const rx=/<\/?([a-zA-Z][\w-]*)((?:\s+[^<>]*?)?)\/?>|([^<]+)/g;let m;
  const stack=[root];
  while((m=rx.exec(html))){
    if(m[3]!==undefined){const txt=m[3].trim();
      if(txt)stack[stack.length-1]._text+=txt;continue;}
    if(m[0][1]==='/'){if(stack.length>1)stack.pop();continue;}
    const el=new El(m[1]);const a=_attrsOf(m[2]);
    for(const k in a){el.attrs[k]=a[k];
      if(k.indexOf('data-')===0)el.dataset[_dataKey(k)]=a[k];}
    el.className=a['class']||'';
    el.checked=(a['checked']!==undefined);
    if(a['value']!==undefined)el.value=a['value'];
    stack[stack.length-1].appendChild(el);
    if(!VOID_TAGS[m[1].toLowerCase()]&&!/\/>$/.test(m[0]))stack.push(el);
  }
  return root;}
const BY_ID={};
globalThis.document={createElement:t=>new El(t),addEventListener(){},body:new El('body'),
  activeElement:null,querySelectorAll:()=>[]};
globalThis.devicePixelRatio=1;
function $(id){return BY_ID[id]||null;}
function requestAnimationFrame(){return 1;}
function cancelAnimationFrame(){}
"""

STATE = r"""
// Состояние модуля, которое в браузере живёт в общем скоупе страницы
let MEDIA_VOL=1;
// Выключатель мутационного теста (test_voice_spare.py): без него вырезанный `edJump`
// спотыкался бы о необъявленное имя. В бою он всегда 0.
let EDMUTVOICE=0;
let CURSTYLE={voice_db:0,music_db:-20};
let VOICEFXSPK='';
let VOICEFXLIVE=null;
let VSTLIST=[];
let VFXSEP=null;let VFXSEP_TIMER=0;const VFXSEP_POLL=1500;
const VFX_DB_DEFAULT=40;const VFX_ENGINE_DEFAULT='roformer';const VFX_MIX_DEFAULT=100;
const CALLS=[];const LOGS=[];
const VTTICK=[];
let PVPX={map:{},xml:'',poll:0,watch:[],height:0};
const PV_PREROLL=0.35,PV_SWAP_LO=-0.12,PV_SWAP_HI=0.5;
const VT_LIVE_DRIFT=0.4;
const EDRULER=18;
// Опросы (хост, ход голоса) в стенде не идут: таймеры складываются и не запускаются —
// иначе процесс висел бы на бесконечном опросе живого хоста.
const TIMERS=[];
globalThis.setTimeout=(fn)=>{TIMERS.push(fn);return TIMERS.length;};
globalThis.clearTimeout=()=>{};
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+m);}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function esc(s){return String(s==null?'':s);}
function ico(){return '';}
function tipArm(){}
function errText(d){return (d&&(d.error||d.err))||'';}
function fmtT(s){return String(Math.round((+s||0)*10)/10)+'s';}
function fmtIns(s){const x=Math.max(0,+s||0);return Math.floor(x/60)+':'+String(Math.floor(x%60)).padStart(2,'0');}
// Клип со СПИКЕРОМ и профиль, у которого в цепочке включённый плагин: без него живой
// хост не поднимают (voiceFxHasVst) — а именно его подъём и потерялся.
const CLIP={xml:'C:/out/01_clip.xml',name:'01_clip.xml',label:'01_clip',job:{speaker:'Голос'}};
const CLIPS=[CLIP];
let SPEAKERS={'Голос':{label:'Голос',voice_fx:{denoise:{on:true,engine:'roformer',mix:100},
  vst:[{path:'C:/p/eq.vst3',name:'EQ',on:true,state:''}]}}};
function clipByXml(xml){return (xml&&xml===CLIP.xml)?CLIP:null;}
// Дверь спикера клипа (40-queue.js): панель голоса берёт профиль у ЕГО спикера.
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
function pvTitle(){}
function selectAE(){}
function openModal(){}
let curEdit=-1;let curAE=0;let AEXML='';
function mediaFree(){}
function voiceWiring(){}
function pvProxyLoad(){return Promise.resolve(null);}
function pvProxyMerge(){return null;}
function pvProxyWatch(){}
function vstFxList(){return Promise.resolve();}
function vstFxEdit(){return Promise.resolve();}
function voiceFxSepFill(){return Promise.resolve();}
function fxDeviceFill(){return Promise.resolve();}
function fxDeviceGet(){return '';}
function fxDeviceSet(){}
function pvVoiceAuto(){}
// Рисование таймлайна в стенде не проверяется (у него свой сторож): важен порядок
// «клип -> редактор -> панель и заказ голоса», а не пиксели волны.
function edDraw(){}
function voiceFxStatus(host,text){const el=host?host.querySelector('[data-vfx="status"]'):null;
  if(el)el.textContent=text||'';}
// Общий объект кадра клипа (как в 60-preview.js) и плеер шага 1 — из файла (см. _ed_literal)
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,
  curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice',silent:true};
// Поддельный Web Audio: узлы помнят буфер и место в очереди, часы двигает стенд.
const SRCS=[];
class FNode{constructor(){this.out=[];this.gain={value:1,setValueAtTime(){},linearRampToValueAtTime(){}};
    this.buffer=null;this.started=null;this.stopped=false;}
  connect(n){this.out.push(n);return n;}disconnect(){this.out=[];}
  start(w,o,d){this.started={when:w,off:o,dur:d};}stop(){this.stopped=true;}}
const ACTX={state:'running',currentTime:100,outputLatency:0,destination:new FNode(),
  createGain(){return new FNode();},createBufferSource(){const n=new FNode();SRCS.push(n);return n;},
  decodeAudioData(ab){return Promise.resolve({duration:120,name:ab&&ab.name});},
  resume(){return Promise.resolve();}};
let AUDIO=null;
function audioGraph(){AUDIO=ACTX;}
function audioWake(){audioGraph();}
const CUT={audio:[{ts:0,te:70,src:0},{ts:70,te:120,src:80}],
  cams:[{path:'C:/cam1.mp4',name:'A'}],
  segs:[{ts:0,te:70,src:0,ci:0},{ts:70,te:120,src:80,ci:0}],
  words:[{s:0,e:120,w:'привет'}],dur:120};
globalThis.fetch=async(url,opt)=>{
  const u=String(url);let body=null;
  try{body=(opt&&opt.body)?JSON.parse(opt.body):null;}catch(e){}
  CALLS.push([u,body]);
  if(u==='/api/aicut_preview')return {json:async()=>JSON.parse(JSON.stringify(CUT))};
  if(u==='/api/preview_proxy')return {json:async()=>({cams:[{path:'C:/cam1.mp4',ready:true,
    proxy:'C:/px/cam1.mp4'}],extra:[]})};
  if(u==='/api/editor_load')return {json:async()=>({keep:[[0,70],[80,120]],fps:60,cam:'C:/cam1.mp4'})};
  if(u.indexOf('/api/waveform')===0)return {json:async()=>({peaks:[0.1,0.2],pps:80,dur:120})};
  if(u==='/api/omnicut_cuts')return {json:async()=>({cuts:[]})};
  if(u==='/api/breaths')return {json:async()=>({marks:[]})};
  if(u==='/api/voicefx_bake')return {json:async()=>({ok:true,ready:true,running:false,pct:100,
    path:'C:/out/01_clip.voice.wav'})};
  if(u==='/api/voicefx_host')return {json:async()=>({ok:true,sid:'s1',fresh:true,track_ready:false,
    window:false,skipped:[]})};
  if(u==='/api/preview_audio')return {json:async()=>({ok:true,path:'C:/out/_tmp/pa_cam1.wav'})};
  if(u.indexOf('/api/media?')===0)return {arrayBuffer:async()=>({
    name:u.indexOf('voice')>=0?'voice':'camera'})};
  return {json:async()=>({ok:true})};};
// Дорожка голоса зовётся счётчиком: проверяем, ЧЕМ плеер её ведёт (шаг 1 — редактор)
function vtTick(P,tm){VTTICK.push([P,+tm]);return realVtTick(P,tm);}
// Живой хост в стенде закрыт: окна нет
function vtLiveOn(){return false;}
function voiceWiringStub(){}
function openFlow(){
  BY_ID.pvstage=new El('div');
  BY_ID.pvsub=new El('div');
  BY_ID.mbPvTitle=new El('span');
  BY_ID.pvvoice=new El('div');
  BY_ID.pvvoicespk=new El('span');
  BY_ID.edtime=new El('span');
  BY_ID.edcam=new El('div');
  BY_ID.edplay=new El('button');
  BY_ID.edtl=new El('canvas');BY_ID.edtl.clientWidth=1000;BY_ID.edtl.clientHeight=200;
  return openEditClip(0);
}
"""


def _run(body: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    import tempfile
    src = (DOM + STATE + _vfxhost_literal() + "\n" + _ed_literal() + "\n" + _bodies()
           + "\n" + body)
    with tempfile.TemporaryDirectory(prefix="editor_voice_") as d:
        path = Path(d) / "stand.js"
        path.write_text(src, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 1. Панель «Голос»: клип со спикером, а не «У клипа нет спикера»
# --------------------------------------------------------------------------- #
@node
def test_open_preview_renders_the_voice_panel_of_the_clip() -> None:
    """После открытия клипа панель в режиме panel, и в ней виден ЕГО спикер.

    Баг (живая проверка): `openPreview` звал `pvVoicePanel` до `edOpen`, панель читала
    прежний `ED.xml` и рисовалась «У клипа нет спикера», хотя `job.speaker` у клипа есть.
    Ручной повторный вызов после открытия рисовал её правильно — то есть дело в порядке.
    """
    out = _run("""
(async()=>{
  await openFlow();
  for(let i=0;i<20;i++)await Promise.resolve();   // хосту дать договорить (он чистит надпись)
  const host=BY_ID.pvvoice;
  const labels=host.querySelectorAll('[data-vfx]').length;
  vtNote(ED,'проверка надписи');   // надпись панели пишет ИМЕННО плеер шага 1
  const st=host.querySelector('[data-vfx="status"]');
  console.log(JSON.stringify({mode:host.dataset.vfxmode,xml:ED.xml,
    cams:(ED.cams||[]).map(c=>c.path),
    spk:(BY_ID.pvvoicespk||{}).textContent,status:(st||{}).textContent,labels:labels,
    off:(host.innerHTML||'').indexOf('нет спикера')>=0}));
})();
""")
    assert out["mode"] == "panel", (
        f"панель «Голос» не отрисована для клипа со спикером (порядок сломан): {out}")
    assert out["off"] is False, f"в панели по-прежнему «У клипа нет спикера»: {out}"
    assert out["xml"] == "C:/out/01_clip.xml", out
    assert out["cams"] == ["C:/cam1.mp4"], f"редактор не знает камеру клипа: {out}"
    assert out["spk"] == "Голос", f"в панели не спикер клипа: {out}"
    assert out["labels"] > 5, "панель пустая — разметка блока не построена"
    # Надпись панели адресуется полем voicePanel плеера: без него vtNote(ED,…) молча
    # ничего не пишет, и в превью не видно ни «прошу голос клипа…», ни «готов».
    assert out["status"] == "проверка надписи", (
        f"надпись панели пишет не плеер шага 1 (нет voicePanel у ED): {out}")


# --------------------------------------------------------------------------- #
# 2. Заказ голоса при открытии: дорожка и живой хост плагинов
# --------------------------------------------------------------------------- #
@node
def test_open_preview_asks_for_the_final_track_and_does_not_raise_the_host() -> None:
    """При открытии уходит ИТОГОВЫЙ заказ (`/api/voicefx_bake`, `final: true`).

    Живой хост — отдельный процесс, и подогнать его к картинке точнее 0,4 с нельзя:
    с включёнными VST превью лагало. Поэтому хост поднимается ТОЛЬКО при открытом
    окне плагина («Настроить»), а в остальное время все шаги играют запечённую
    дорожку с цепочкой — ту самую, что уедет в AE, DRP и черновик.

    Живая проверка (раньше): в логе сервера не было ни одного из двух заказов —
    заказ уходил от монтажного плеера (PV), а панель с дорожкой уже переехали на
    редактор (ED), у которого не было ни клипа, ни камеры.
    """
    out = _run("""
(async()=>{
  await openFlow();
  const pick=u=>CALLS.filter(c=>c[0]===u).map(c=>c[1]);
  const bake=pick('/api/voicefx_bake'),host=pick('/api/voicefx_host');
  console.log(JSON.stringify({bakes:bake.length,hosts:host.length,
    bake:bake[0]||null,host:host[0]||null,
    urls:CALLS.map(c=>c[0])}));
})();
""")
    assert out["bakes"] == 1, f"голос клипа не заказан при открытии: {out['urls']}"
    assert out["hosts"] == 0, (
        f"живой хост поднят без открытого окна плагина: {out['urls']}")
    bake = out["bake"]
    assert bake["xml"] == "C:/out/01_clip.xml" and bake["src"] == "C:/cam1.mp4", bake
    assert bake.get("final") is True, (
        f"без открытого окна заказан не итоговый голос: {bake}")
    assert bake["fx"]["vst"][0]["on"] is True, f"настройки цепочки уехали не из панели: {bake}"


# --------------------------------------------------------------------------- #
# 3. Кадр редактора ведёт дорожку голоса и глушит звук камеры
# --------------------------------------------------------------------------- #
@node
def test_editor_frame_drives_the_track_and_mutes_the_camera() -> None:
    """«Играть» — кадр редактора: звучит готовый голос клипа из буфера, камера немая.

    Открытие клипа заказало итоговый голос; он готов — и уходит в буфер звука редактора
    (`eaVoice`), а не в <audio>. «Играть» ставит очередь блоков на этот буфер с места
    плейхеда (ED.cs — исходное время камеры 1 = время запечённого трека). <video> кадра
    немые всегда (PV.silent): иначе звук камеры звучал бы поверх голоса — два голоса разом.
    """
    out = _run("""
(async()=>{
  await openFlow();
  for(let i=0;i<10;i++)await Promise.resolve();   // звук камеры и голос декодированы
  VTTICK.length=0;
  edPlay();
  ACTX.currentTime+=1/60;ED.raf=0;edTick();
  const q=SRCS.filter(n=>!n.stopped&&n.started);
  console.log(JSON.stringify({ticks:VTTICK.map(x=>[x[0]===ED,x[1]]),
    play:!!ED.play,video:PV.vids[0].played,
    mute:PV.vids.every(v=>v.muted),trackPath:vtOf(ED).path||'',trackOn:!!vtOf(ED).on,
    trackEl:!!vtOf(ED).el,bufs:q.map(n=>n.buffer&&n.buffer.name),firstOff:q.length?q[0].started.off:null}));
})();
""")
    assert out["ticks"], "кадр редактора не позвал дорожку голоса (живой хост и гейн)"
    assert all(t[0] for t in out["ticks"]), f"дорожку ведёт не плеер шага 1: {out['ticks']}"
    assert out["play"] is True and out["video"] >= 1, f"редактор не заиграл: {out}"
    assert out["trackOn"] is True and out["trackPath"] == "C:/out/01_clip.voice.wav", out
    assert out["trackEl"] is False, f"у плеера шага 1 завёлся <audio> голоса: {out}"
    assert out["bufs"] and set(out["bufs"]) == {"voice"}, (
        f"очередь звука играет не готовый голос клипа: {out}")
    assert out["firstOff"] == 0, f"звук начался не с места плейхеда: {out}"
    assert out["mute"] is True, f"звук камеры шага 1 не немой — слышно два голоса: {out}"
