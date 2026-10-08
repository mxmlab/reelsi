# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Прокси приехал во время игры: живой <video> шага 1 из эфира не забирают.

Баг владельца: «когда заканчивается сборка превью-прокси, видео встаёт на кадре, а
плеер играет — пауза и снова играть, дальше норм». Причина: плеер шага 1 один, это
редактор (`ED.play`), а его окно с кадром — объект `PV`. Состояние «PV играет» не
ставит никто (`PV.playing` с 02.10 не пишется вовсе), поэтому `pvProxyRefresh` считал
играющий кадр стоящим, звал `spareHandover(PV)` и обменивал живой элемент на дублёра
прямо посреди игры: прежний живой уезжал в паузу, а в `PV.vids[0]` вставал дублёр,
которого никто не пускал. `edTick` дальше читал его `currentTime` — стоп-кадр при
живой кнопке «пауза».

Инвариант: «кадр этого плеера играет» — отдельный вопрос, и отвечает на него одна
дверь `pvVidsPlaying(P)` (у `PV` — через редактор). Ею и решается судьба живого
`<video>` в `pvProxyRefresh` и в самом `spareHandover`.

Стенд гоняет БОЕВЫЕ функции под node: тела берутся из `static/app/60-preview.js` —
копий в тесте нет (иначе стенд сторожил бы сам себя).

Запуск:  py -3.10 -m pytest tests/test_proxy_refresh_playing.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Машина дублёра (60-preview.js) — тела из файла. `edPlay`/`edPause` берём у редактора:
# игру шага 1 начинает ровно он, и в стенде обязано быть так же, как в браузере.
PREVIEW_FUNCS = ("pvSrc", "bufMake", "bufSilent", "bufIdle", "liveOf", "bufSwap",
                 "spareLead", "segGap", "vtPlaying", "pvVidsPlaying", "spareHandover",
                 "pvProxyRefresh", "camVisual")
EDITOR_FUNCS = ("edPlay",)
# Константы порогов — из файла: свои копии в стенде разъезжались бы с боевыми молча.
CONSTS = ("PV_PREROLL", "PV_SWAP_LO", "PV_SWAP_HI", "MEDIA_VOL")


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


def _const(src: str, name: str) -> str:
    """Объявление `const NAME=...`/`let NAME=...` — из файла (MEDIA_VOL объявлен через let)."""
    m = re.search(r"^(?:const|let) %s=.*$" % re.escape(name), src, re.M)
    assert m is not None, f"в 60-preview.js нет объявления {name}"
    return m.group(0)


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    return "\n".join([_const(preview, c) for c in CONSTS]
                     + [_func_src(preview, n) for n in PREVIEW_FUNCS]
                     + [_func_src(editor, n) for n in EDITOR_FUNCS])


# Заглушки — внешние двери (DOM, кадр клипа, карта прокси) и счётчики ожиданий.
# Своих копий боевых правил здесь нет: `pvProxyLoad`/`pvProxyMerge` подменены ровно
# затем, чтобы стенд не ходил на сервер, — карту прокси тест задаёт сам.
STUBS = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function $(id){return BY_ID[id]||null;}
function ico(){return '';}
function requestAnimationFrame(){return 1;}
function cancelAnimationFrame(){}
const location={origin:''};
// Мини-<video>: ровно те свойства, которыми живёт переезд дублёром. `pauseLog` —
// счётчик пауз на элементе: по нему и видно, выкинули ли живой кадр из эфира.
class El{
  constructor(tag){this.tag=String(tag).toUpperCase();this.src='';this.preload='';this.muted=false;
    this.playsInline=false;this.volume=1;this.playbackRate=1;this.readyState=2;this.seeking=false;
    this.paused=true;this.played=0;this.pauseLog=0;this.seekLog=[];this.style={};this.attrs={};
    this.children=[];}
  set currentTime(v){this._ct=+v||0;this.seekLog.push(this._ct);}
  get currentTime(){return this._ct||0;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  removeAttribute(k){delete this.attrs[k];}
  addEventListener(){}
  removeEventListener(){}
  appendChild(c){this.children.push(c);return c;}
  insertBefore(c,ref){this.children.push(c);return c;}
  remove(){}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;this.pauses++;return undefined;}
  querySelector(){return null;}
}
const BY_ID={};
const document={createElement:t=>new El(t),addEventListener(){},body:new El('body'),
  activeElement:null,querySelectorAll:()=>[]};
// Объект кадра шага 1: камеры, дублёры, EDL. Своего состояния игры у него нет — играет
// редактор (ED.play), и это ровно тот контракт, что в бою.
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,
  curCi:0,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice'};
let ED={xml:'',blocks:[],fps:60,cam:'',dur:0,peaks:[],pps:80,sel:-1,play:false,raw:false,raf:0,
  cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],brBand:0,cams:null,voicePanel:'pvvoice'};
// Соседние плееры превью: в этом стенде открыт только шаг 1, остальные закрыты.
let IPV=null,CPV=null;
function pvVideoTo(){}
function spareIdle(){}
function spareStop(){}
function sparePrime(){}
function camIdle(){}
function camApply(){}
function pvAudioLimit(){}
function voiceWiring(){}
function vtOf(P){return (P.vt=P.vt||{on:false,el:null,vsp:null});}
function vtSpareStop(){}
function vtSpareIdle(){}
function vtSpareArm(){}
function vtSpareRoll(){}
function vtSpareSwap(){}
function vtSpareTake(){}
function vtSpareAt(){}
function voicePrime(){}
function ipvTransRefresh(){}
function vtPause(){}
function vtStop(){}
// Ожидания готовности дублёра. В бою это события элемента (`loadedmetadata`,
// `seeked`), в стенде — обещание, которое САМО отпускается через HOLD микрозадач.
// Так регрессия не подвешивает прогон: старая проверка `vtPlaying` забирает живой кадр
// из эфира, переезд доходит до конца, сценарий возвращается и печатает отчёт — тест
// падает на своих утверждениях, а не на «стенд ничего не напечатал».
let HOLD=0;
function hold(n){HOLD=n;}
const wait=()=>{
  if(HOLD<=0)return Promise.resolve();
  HOLD--;
  return Promise.resolve().then(wait);};
function vLoaded(){return wait();}
function vSeeked(){return wait();}
const flush=()=>Promise.resolve();   // один ход микрозадач: ровно столько ждёт await
let NEXT_MAP={};
function pvProxyLoad(){return Promise.resolve({map:Object.assign({},NEXT_MAP),building:false,
  total:Object.keys(NEXT_MAP).length,ready:Object.keys(NEXT_MAP).length});}
function pvProxyMerge(px){if(px)Object.assign(PVPX.map,px.map);return px;}
let PVPX={map:{},xml:'',poll:0,watch:[],height:0};
// Один прогон «открыли клип»: живой <video> камеры 1 и его дублёр — ровно как их
// заводит openPreview/bufMake.
function setupXml(path){
  const mk=tag=>{const el=new El(tag);el.pauses=0;return el;};
  const stage=mk('div');
  const live=mk('video');live.src=pvSrc(path);live.readyState=4;live._ct=12.5;
  PV.vids=[live];PV.cams=[{path:path,name:'A'}];PV.audio=[];PV.aidx=0;PV.scrubbing=false;
  PV.bufs=[];bufMake(PV,stage,null,0,0);
  ED.play=false;ED.raf=1;   // в бою кадр игры уже заказан (requestAnimationFrame)
  return {live:live,buf:PV.bufs[0].el};
}
function proxyFor(path,name){NEXT_MAP[path]=name;}
function report(name,extra){console.log(JSON.stringify(Object.assign({name:name},extra)));}
"""
STAND = STUBS + "\n" + _bodies() + "\n"


def _run_node(body: str, name: str = "pxfreeze.js") -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    with tempfile.TemporaryDirectory(prefix="proxy_refresh_") as d:
        path = Path(d) / name
        path.write_text(STAND + "\n" + body, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60,
                              cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 0. Дверь: у плеера шага 1 «кадр играет» решает редактор
# --------------------------------------------------------------------------- #
@node
def test_step_one_videos_play_through_the_editor() -> None:
    """`pvVidsPlaying(PV)` = играет редактор (или сам кадр), и это НЕ `vtPlaying(PV)`.

    Состояние игры шага 1 живёт на редакторе, а окно кадра о нём не знает. Разница
    и есть корень бага: `vtPlaying(PV)` про играющий кадр отвечает «стоит».
    """
    out = _run_node("""
const r=setupXml('C:/cam1.mp4');
const before={door:pvVidsPlaying(PV),field:vtPlaying(PV)};
ED.play=true;
const playing={door:pvVidsPlaying(PV),field:vtPlaying(PV)};
ED.play=false;PV.playing=true;
const own={door:pvVidsPlaying(PV),field:vtPlaying(PV)};
report('door',{before:before,playing:playing,own:own,ed:vtPlaying(ED)});
""")
    assert out["before"] == {"door": False, "field": False}, (
        f"стоящий кадр шага 1 считается играющим: {out['before']}")
    assert out["playing"] == {"door": True, "field": False}, (
        "игра редактора не видна двери кадра (или, наоборот, видна vtPlaying): "
        f"{out['playing']}")
    assert out["own"] == {"door": True, "field": True}, (
        f"кадр, играющий сам (PV.playing), потерялся: {out['own']}")


# --------------------------------------------------------------------------- #
# 1. Редактор играет, прокси приехал: живой элемент остаётся в эфире
# --------------------------------------------------------------------------- #
@node
def test_proxy_arrives_while_editor_plays_keeps_the_live_video() -> None:
    """Прокси дособрались во время игры — в `PV.vids[0]` тот же элемент, пауз на нём нет.

    Ровно жалоба владельца: живой <video> уезжал в паузу, а на его место вставал
    неиграющий дублёр — картинка замирала при живой кнопке «пауза».
    """
    out = _run_node("""
(async()=>{
  const r=setupXml('C:/cam1.mp4');
  const live=PV.vids[0];
  proxyFor('C:/cam1.mp4','C:/cache/pv_cam1.mp4');   // прокси дособрался
  ED.play=true;                                     // ...и в этот момент играет редактор
  await pvProxyRefresh();
  await flush();
  report('playing',{same:PV.vids[0]===live,pauses:live.pauseLog,
    srcPath:decodeURIComponent(live.src.split('path=')[1]),
    plays:live.played,bufInPlace:PV.bufs[0].el.src.split('path=')[1]});
})();
""")
    assert out["same"] is True, "живой <video> играющего редактора забрали из эфира"
    assert out["pauses"] == 0, (
        f"на играющем кадре вызвали pause() — это и есть стоп-кадр: {out}")
    assert out["srcPath"] == "C:/cam1.mp4", (
        f"играющему кадру подменили источник: {out}")
    assert out["bufInPlace"] == "C%3A%2Fcam1.mp4", (
        f"дублёр не остался на исходнике (переезд на прокси идёт на стыке): {out}")


# --------------------------------------------------------------------------- #
# 2. Редактор стоит: переезд дублёром состоялся
# --------------------------------------------------------------------------- #
@node
def test_proxy_arrives_while_editor_stopped_hands_over_by_the_spare() -> None:
    """Стоящий плеер шага 1 переезжает на прокси сразу — дублёром, без чёрного кадра.

    Обратная сторона того же правила: «стоит» по-прежнему значит «переезжай сейчас».
    """
    out = _run_node("""
(async()=>{
  const r=setupXml('C:/cam1.mp4');
  const live=PV.vids[0],buf=PV.bufs[0].el;
  proxyFor('C:/cam1.mp4','C:/cache/pv_cam1.mp4');
  ED.play=false;
  await pvProxyRefresh();                           // стоящий: переезд идёт до конца
  await flush();
  report('stopped',{swapped:PV.vids[0]===buf,
    livePath:decodeURIComponent(PV.vids[0].src.split('path=')[1]),
    oldInSpare:PV.bufs[0].el===live,oldPaused:live.pauseLog,
    ct:PV.vids[0].currentTime});
})();
""")
    assert out["swapped"] is True, f"стоящий плеер не переехал дублёром: {out}"
    assert out["livePath"] == "C:/cache/pv_cam1.mp4", (
        f"в эфире не прокси — переезд не состоялся: {out}")
    assert out["oldInSpare"] is True, "прежний живой элемент не ушёл в дублёры"
    assert out["ct"] == 12.5, f"дублёр встал не на кадр, что был на экране: {out}"


# --------------------------------------------------------------------------- #
# 3. Редактор начал играть, пока шёл переезд: обмена нет
# --------------------------------------------------------------------------- #
@node
def test_editor_started_playing_while_handover_waits_cancels_the_swap() -> None:
    """Игру начали между «подвели дублёра» и подменой — эфир не отдаём.

    Ждать готовности дублёра приходится (метаданные, seek), и за это время владелец
    успевает нажать «играть». Тогда решение меняется: играющий кадр остаётся живым,
    переезд уйдёт на ближайший стык (sparePrime подтянет свежий src сам).
    """
    out = _run_node("""
(async()=>{
  const r=setupXml('C:/cam1.mp4');
  const live=PV.vids[0],buf=PV.bufs[0].el;
  proxyFor('C:/cam1.mp4','C:/cache/pv_cam1.mp4');
  ED.play=false;
  hold(2);                                          // переезд на ожидании (метаданные, seek)
  const run=pvProxyRefresh();
  await flush();                                    // ...и вот здесь владелец нажал «играть»
  ED.play=true;
  await run;await flush();
  report('raced',{same:PV.vids[0]===live,spareStillSpare:PV.bufs[0].el===buf,
    livePath:decodeURIComponent(live.src.split('path=')[1]),pauses:live.pauseLog});
})();
""")
    assert out["same"] is True, f"переезд подменил кадр уже играющего редактора: {out}"
    assert out["spareStillSpare"] is True, "дублёр вышел в эфир мимо решения двери"
    assert out["livePath"] == "C:/cam1.mp4", f"живому подменили источник: {out}"
    assert out["pauses"] == 0, f"живой кадр остановили посреди игры: {out}"
