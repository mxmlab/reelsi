# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Голос в превью: живые плагины — только при открытом окне, иначе запечённая дорожка.

Решено владельцем: пока открыто окно хотя бы одного плагина, звук идёт ВЖИВУЮ через
плагины (крутить и слушать); окна закрыты — настройки всех плагинов сохранены, голос
перепекается, и ВСЕ шаги играют запечённую дорожку. Живой хост — отдельный процесс, и
подогнать его к картинке точнее 0,4 с нельзя: с включёнными VST превью лагало
(«концы не доигрывал, иногда больше играл и не там»), с выключенными шло ровно.

Стенды гоняют БОЕВЫЕ функции из `static/app/60-preview.js` и `static/app/95-styles.js`
под node с заглушками (образец — `tests/test_bulk_delete.py`): в браузере эти места не
увидеть без живого сервера, клипа и плагинов, а ломаются они молча. Проверяется:

1. плагины включены, окна нет → заказ итогового голоса (`final: true`), хост не
   синхронизируется вовсе;
2. окно открыто → хост синхронизируется, а плеер просит дорожку шумодава;
3. закрытие окна → `dump_states` (состояния всех плагинов) и гашение хоста ДО заказа
   итогового голоса: иначе он вернётся из кеша посчитанным под прежние настройки;
4. пока печётся — играет ПРЕДЫДУЩАЯ запечённая дорожка (`st.path` не пуст), а не звук
   камеры;
5. синхрон дорожки превью шага 3: расхождение 0,1 с гасится СКОРОСТЬЮ (`playbackRate`,
   `currentTime` не трогается), 0,5 с — перемоткой; на паузе позиция ставится сразу.
   У шага 1 дорожки-элемента нет вовсе: голос там играет буфер Web Audio (блок `ea*`,
   tests/test_editor_single_player.py), и готовый трек уходит в него (`eaVoice`).

Питоновская часть — в конце файла: хост отдаёт состояние ВСЕХ плагинов на
`dump_states`, `live_stop` забирает его ПЕРЕД гашением, вход хоста — дорожка шумодава.

Запуск:  py -3.10 -m pytest tests/test_voice_preview_mode.py -q
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

STYLES_JS = ROOT / "static" / "app" / "95-styles.js"
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Функции блока голоса: тела берутся из файлов, копий в тесте нет (стенд сторожил бы
# сам себя). Порядок вырезки — как в файлах, чтобы вставки не мешали друг другу.
VOICE_FUNCS = ("voiceFxHostOn", "voiceFxLiveOn", "voiceFxWindowOn", "voiceFxHasVst",
               "voiceFxHostKey", "voiceFxHostSync", "voiceFxHostStart", "voiceFxHostStop",
               "voiceFxHostDump", "voiceFxStatesApply", "vfxEl")
PREVIEW_FUNCS = ("vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying", "vtEl",
                 "vtGate", "vtNote", "vtProfileFx", "vtFx", "vtDetach", "vtStop",
                 "vtLiveOn", "vtSetMute", "vtLiveUpdate", "vtLiveRate", "vtLiveExpect",
                 "vtLiveCmd", "vtLiveSid", "vtLivePlay", "vtLivePause",
                 "vtIsPv", "vtIsEd", "vtMuteHost", "vtStage", "vtHostLive", "vtLivePrep",
                 "vtStatesWait", "vtStatesTake", "vtHostDown", "vtPrep", "vtVoiceShow",
                 "vtVoiceWatch", "vtVoiceTake", "vtVoiceUse", "vtUse", "vtTick",
                 "vtVoiceLine", "vtVoiceStop", "vtSeek", "vtRate")


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки.

    `async` забираем вместе с функцией: без него `await` внутри тела — синтаксическая
    ошибка, и стенд падал бы «missing ) after argument list» вместо проверки.
    """
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
    """Объявление `const NAME=...;` — константа нужна вырезанным функциям."""
    m = re.search(r"^const %s=.*$" % re.escape(name), src, re.M)
    assert m is not None, f"в 60-preview.js нет объявления const {name}"
    return m.group(0)


def _src(*names: str) -> str:
    """Тела боевых функций: голос — из 95-styles.js, плеер — из 60-preview.js."""
    styles = STYLES_JS.read_text(encoding="utf-8")
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    out = []
    for name in names:
        where = styles if name in VOICE_FUNCS else preview
        assert name in VOICE_FUNCS or name in PREVIEW_FUNCS, f"нет такого стенда: {name}"
        out.append(_func_src(where, name))
    return "\n".join(out)


# Заглушки — только внешние двери (сервер, DOM, тосты). Всё, что проверяется, — боевые
# функции. Панель «Голос» не рисуется: vtFx читает профиль спикера (vtProfileFx), а
# `voicePanel` у плеера пуст — та же ветка, что у превью шага 1 без панели.
STUBS = r"""
let VOICEFXLIVE=null;
let VFXHOST={timer:0,key:'',seq:0,starting:null,row:null,state:'',skip:''};
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function errText(d){return (d&&(d.error||d.err))||'';}
function esc(s){return String(s);}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function $(id){return globalThis.__byId&&globalThis.__byId[id]||null;}
function voiceWiring(){}
function voiceFxStatus(){}
// Клип по XML: настройки голоса берутся из профиля спикера клипа (vtProfileFx) —
// панели у стенда нет, и это та же ветка, что у превью шага 3.
function clipByXml(){return {label:'01_clip',job:{speaker:'Мясников'}};}
// Дверь спикера клипа (40-queue.js): профиль голоса берётся у ЕГО спикера.
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
let MEDIA_VOL=1;
let SPEAKERS={};
let VOICEFXSPK='Мясников';
function fxDeviceGet(){return '';}
function voiceFxHostNotes(){}
function voiceFxHostPoll(){}
// Звук редактора шага 1 (блок `ea*`): готовый трек плеера шага 1 уходит в его буфер.
const EAV=[];
function eaVoice(p){EAV.push(p);}
function eaGain(){}
let ED,IPV,CPV;
// Что стенд увидел со стороны сервера: заказы запекания, команды живому хосту и
// подъём хоста. «Хост синхронизировали» здесь — это запрос `/api/voicefx_host`
// (поднять) или `/api/voicefx_live` с командой `chain` (перестроить на лету).
const VFX_DUMPED={bakes:[],dumps:0,stopped:0,synced:0};
// Сервер: заказы запекания и команды живому хосту. Состояния плагинов отдаём
// ровно так, как их отдаёт /api/voicefx_live на команду dump_states.
globalThis.fetch=async(url,opt)=>{
  const u=String(url);
  if(u==='/api/voicefx_bake'){
    VFX_DUMPED.bakes.push(JSON.parse(opt.body));
    return {json:async()=>({ok:true,ready:true,running:false,
      path:(JSON.parse(opt.body).final?'C:/cache/final.wav':'C:/cache/dn.wav')})};}
  if(u==='/api/voicefx_live'){
    const body=JSON.parse(opt.body||'{}');
    // «Хост синхронизировали» — это цепочка (перестроить на лету) или его подъём,
    // а не команды звука: их превью шлёт и с открытым окном, и без него.
    if(body.cmd==='chain')VFX_DUMPED.synced++;
    if(body.cmd==='dump_states'){VFX_DUMPED.dumps++;
      return {json:async()=>({ok:true,sent:true,states:[
        {path:'C:/a.vst3',state_b64:'AQID'},{path:'C:/b.vst3',state_b64:'BAUG'}]})};}
    return {json:async()=>({ok:true,sent:true})};}
  if(u==='/api/voicefx_host'){VFX_DUMPED.synced++;
    return {json:async()=>({ok:true,sid:'1',running:true,track_ready:true,
      window:false,fresh:true})};}
  if(u==='/api/voicefx_host_stop'){VFX_DUMPED.stopped++;return {json:async()=>({ok:true})};}
  return {json:async()=>({ok:true})};};
// Накрученное в окне уезжает в профиль письмом: `voiceFxHostStop`/`voiceFxHostDump`
// собирают его через `voiceFxHostPost` (та же дверь, что в бою).
async function voiceFxHostPost(url,body){
  return (await globalThis.fetch(url,{method:'POST',body:JSON.stringify(body)})).json();}
const document={querySelectorAll:()=>[],createElement:()=>new El('audio')};
"""

DOM = r"""
// Мини-<video>/<audio>: ровно те свойства, которыми живёт синхрон и глушение дорожки.
// `currentTime` — свойство, а не поле: перемотка видна счётчику (`seeks`), и по нему
// проверяется главное — что маленькое расхождение гасится СКОРОСТЬЮ, а не seek'ом.
class El{
  constructor(tag){this.tag=tag;this.src='';this.volume=0;this.readyState=1;
    this.paused=true;this.seeking=false;this._ct=0;this.playbackRate=1;
    this.muted=false;this.seeks=[];this.plays=0;this.pauses=0;this.style={};}
  get currentTime(){return this._ct;}
  set currentTime(v){this._ct=v;this.seeks.push(v);}
  play(){this.plays++;this.paused=false;return Promise.resolve();}
  pause(){this.pauses++;this.paused=true;}
  load(){}removeAttribute(k){if(k==='src')this.src='';}
  addEventListener(){}}
"""

STATE = r"""
// Плеер шага 1: время монтажа, дорожка голоса и камера. `voicePanel` пуст — ручки
// берутся из профиля спикера (vtProfileFx), как у плеера без панели.
function P_(){
  return {xml:'C:/out/01_clip.xml',cams:[{path:'C:/cam1.mp4'}],audioCi:0,playing:false,
    scrubbing:false,audio:[{ts:0,te:10,src:100}],aidx:0,vids:null,
    voicePanel:null,cs:0,play:false,vt:null};}
"""


def _run_node(tmp_path: Path, body: str, name: str = "vpmode.js") -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    script = (STUBS + "\n" + DOM + "\n" + STATE + "\n"
              + "\n".join(_const(preview, c) for c in ("VT_DRIFT", "VT_SOFT", "VT_RATE",
                                                       "VT_QUIET", "VT_POLL"))
              + "\n" + _src(*(VOICE_FUNCS + PREVIEW_FUNCS)) + "\n" + body)
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# Настройки профиля спикера: шумодав и один ВКЛЮЧЁННЫЙ плагин. Именно включённый
# плагин и делает живой хост нужным — с одним шумодавом звука вживую не бывает.
FX = {"denoise": {"on": True, "engine": "roformer", "atten_db": 40, "mix": 100},
      "vst": [{"path": "C:/a.vst3", "name": "", "state": "AQID", "on": True}]}


def _prep_body(window: bool, host_on: bool, ticks: int = 0) -> str:
    """Заказ голоса в заданном состоянии: окно плагина и живой хост.

    `window` — окно плагина открыто (голос играет хост); `host_on` — процесс хоста
    вообще поднят (`VOICEFXLIVE`). Это разные вещи: окно могли закрыть, а хост ещё
    живёт до гашения — и именно на этом переходе заказывается итоговый голос.

    `ticks` — сколько раз прокрутить очередь микрозадач перед снятием результата.
    `vtPrep` нарочно не ждёт внутренний перезаказ (превью не должно стоять, пока
    гасится хост), поэтому без этих прокруток виден промежуточный шаг, а не итог.
    """
    live = ('{sid:"1",running:true,window:' + ('true' if window else 'false')
            + ',track_ready:true}') if host_on else 'null'
    return f"""
SPEAKERS={{'Мясников':{{'voice_fx':{json.dumps(FX, ensure_ascii=False)}}}}};
const P=P_();
ED=P;   // плеер шага 1: у него панель «Голос» и живой хост плагинов
VOICEFXLIVE={live};
(async()=>{{
  await vtPrep(P);
  for(let i=0;i<{ticks};i++)await Promise.resolve();
  console.log(JSON.stringify({{
    bakes:VFX_DUMPED.bakes||[], synced:VFX_DUMPED.synced,
    dumps:VFX_DUMPED.dumps||0, stopped:VFX_DUMPED.stopped||0,
    path:P.vt.path, fin:P.vt.fin, on:P.vt.on,
    el:(P.vt.el?P.vt.el.src:''), eav:EAV}}));
}})();
"""


# --------------------------------------------------------------------------- #
# 1. Живой хост — только при открытом окне плагина
# --------------------------------------------------------------------------- #
@node
def test_plugins_on_without_a_window_bake_the_final_voice(tmp_path: Path) -> None:
    """Плагины включены, окна нет → заказ ИТОГОВОГО голоса, хост не поднимается.

    Это и есть суть решения: без открытого окна крутить нечего, а живой хост — это
    отдельный процесс, который не подогнать точнее 0,4 с. Все шаги играют запечённую
    дорожку с плагинами (`final: true`) — ту, что уедет в AE, DRP и черновик.
    """
    res = _run_node(tmp_path, _prep_body(window=False, host_on=False))

    assert len(res["bakes"]) == 1, "голос клипа не запрошен"
    assert res["bakes"][0].get("final") is True, \
        "без открытого окна заказан не итоговый голос: %s" % res["bakes"][0]
    assert res["synced"] == 0, "живой хост синхронизировали без открытого окна"
    assert res["dumps"] == 0 and res["stopped"] == 0, res
    assert res["on"] is True and res["fin"] is True, res
    # Шаг 1: итоговый голос уходит в буфер редактора, а не в <audio>.
    # Первый заход: прежний голос снят (None), потом готовый итоговый — в буфер.
    assert res["eav"] == [None, "C:/cache/final.wav"], res
    assert res["el"] == "", "у плеера шага 1 завёлся <audio> голоса: %s" % res


@node
def test_an_open_plugin_window_plays_live(tmp_path: Path) -> None:
    """Окно плагина открыто → хост синхронизируется, плеер просит дорожку шумодава.

    Вживую играет хост (цепочка в реальном времени), а своя дорожка — БЕЗ плагинов:
    запеки их сюда — цепочка слышалась бы дважды.
    """
    res = _run_node(tmp_path, _prep_body(window=True, host_on=True))

    assert res["synced"] >= 1, "открытое окно не подняло живой хост"
    assert len(res["bakes"]) == 1, "дорожка шумодава не запрошена"
    assert not res["bakes"][0].get("final"), \
        "при открытом окне заказан итоговый голос, а не дорожка шумодава"
    assert res["dumps"] == 0 and res["stopped"] == 0, \
        "открытое окно не должно гасить хост: крутить и слушать"
    assert res["on"] is True and res["fin"] is False, res


@node
def test_closing_the_window_dumps_states_then_bakes_the_final_voice(tmp_path: Path) -> None:
    """Закрыли окно → состояния ВСЕХ плагинов, гашение хоста, и только потом заказ.

    Порядок обязателен: состояние знает только процесс хоста, поэтому сперва
    `dump_states` (сервер запишет профиль), потом гашение по PID, и лишь затем заказ
    итогового голоса. Закажи его раньше — он вернулся бы из кеша посчитанным под
    ПРЕЖНИЕ настройки плагинов, и в ушах был бы старый звук.
    """
    # `await vtPrep` не ждёт внутренний перезаказ (он нарочно без await — окно уже
    # закрыто, и превью не должно стоять): прокручиваем очередь микрозадач, чтобы
    # снять ИТОГ, а не промежуточный шаг.
    res = _run_node(tmp_path, _prep_body(window=False, host_on=True, ticks=40))

    assert res["dumps"] >= 1, "перед гашением не забрали состояния всех плагинов"
    assert res["stopped"] >= 1, "хост не погасили после закрытия окна"
    assert len(res["bakes"]) == 1, "итоговый голос не заказан после перепекания"
    assert res["bakes"][0].get("final") is True, res["bakes"][0]
    assert res["synced"] == 0, "хост синхронизировали, хотя окна уже нет"
    assert res["on"] is True and res["fin"] is True, res
    assert res["eav"] and res["eav"][-1] == "C:/cache/final.wav", res


@node
def test_while_rebaking_the_previous_track_keeps_playing(tmp_path: Path) -> None:
    """Пока печётся новый голос — играет ПРЕДЫДУЩАЯ запечённая дорожка.

    `st.path` и `st.on` остаются прежними: подмена источника звука на пустоту рвала
    бы воспроизведение и возвращала СЫРОЙ звук камеры — ровно то, от чего уходили.
    """
    body = """
SPEAKERS={'Мясников':{'voice_fx':%s}};
const P=P_();
// Прошлый заход: дорожка шумодава уже подключена и играет.
P.vt={on:true,el:null,path:'C:/cache/old.wav',fin:true,seq:0,timer:0,poll:0,want:'',
  note:'',vtq:false,vtend:false,live:null,dn:JSON.stringify(%s.denoise),wantDn:'',
  wantFinal:true,warn:'',statesWait:null};
const oldEl=new El('audio');oldEl.src='/api/media?path=old';P.vt.el=oldEl;
VOICEFXLIVE=null;
let slow=null;
globalThis.fetch=async(url,opt)=>{const u=String(url);
  if(u==='/api/voicefx_bake'){await new Promise(r=>{slow=r;});
    return {json:async()=>({ok:true,ready:false,running:true,pct:0,i:0,n:0})};}
  return {json:async()=>({ok:true})};};
(async()=>{
  const p=vtPrep(P);
  await Promise.resolve();await Promise.resolve();
  const mid={path:P.vt.path,on:P.vt.on,src:oldEl.src};
  if(slow)slow();
  await p;
  console.log(JSON.stringify({mid:mid,after:{path:P.vt.path,on:P.vt.on,
    src:oldEl.src,paused:oldEl.paused}}));
})();
""" % (json.dumps(FX, ensure_ascii=False), json.dumps(FX, ensure_ascii=False))
    res = _run_node(tmp_path, body)

    assert res["mid"]["path"] == "C:/cache/old.wav", \
        "во время перепекания дорожка отцеплена: %s" % res["mid"]
    assert res["mid"]["on"] is True and res["mid"]["src"] == "/api/media?path=old", \
        "старая дорожка снята с воспроизведения: %s" % res["mid"]
    assert res["after"]["path"] == "C:/cache/old.wav", \
        "трек пропал после ответа «считается»: %s" % res["after"]


# --------------------------------------------------------------------------- #
# 2. Синхрон дорожки: скорость вместо перемотки
# --------------------------------------------------------------------------- #
def _tick_body(drift: float, playing: bool = True) -> str:
    """Кадр плеера с заданным расхождением звука и картинки.

    `drift` — насколько дорожка ОТСТАЁТ от картинки (положительное — надо догнать).
    Плеер по EDL (превью шага 3): монтаж 5 с = исходник 105 с (кусок `src:100`), поэтому
    расхождение задаётся относительно 105-й секунды исходника.
    """
    return """
const P=P_();
P.playing=%s;P.play=%s;        // играем (у шага 3 это P.playing)
P.vids=[new El('video')];P.vids[0]._ct=5+100;   // камера на 5-й секунде монтажа
P.vt={on:true,el:null,path:'C:/cache/dn.wav',fin:false,seq:0,timer:0,poll:0,want:'',
  note:'',vtq:false,vtend:false,live:null,dn:JSON.stringify(%s.denoise),wantDn:'',
  wantFinal:false,warn:'',statesWait:null};
const el=new El('audio');P.vt.el=el;
el.currentTime=%s-%s;          // расхождение заданной величины
el.seeks.length=0;             // считаем только перемотки САМОГО кадра
el.paused=!%s;el.readyState=4;
vtTick(P,5);
console.log(JSON.stringify({rate:el.playbackRate,ct:el.currentTime,
  seeks:el.seeks.length,seeksDone:el.seeks,pauses:el.pauses,
  muted:P.vids[0].muted}));
""" % ("true" if playing else "false", "true" if playing else "false",
       json.dumps(FX, ensure_ascii=False),
       "105", json.dumps(drift), "true" if playing else "false")


@node
def test_small_drift_is_fixed_by_rate(tmp_path: Path) -> None:
    """Расхождение 0,1 с → меняется СКОРОСТЬ, `currentTime` не трогается.

    Перемотка — это провал в звуке и сброс хвоста плагинов; на расхождении в десятые
    доли секунды её делать нельзя. ±6 % на слух незаметны (у камер ровно тот же приём).
    """
    res = _run_node(tmp_path, _tick_body(0.1))

    assert res["rate"] > 1.0, "отстающую дорожку не разогнали: %s" % res
    assert abs(res["rate"] - 1.06) < 1e-9, res["rate"]
    assert res["seeks"] == 0, "перемотали там, где хватало скорости: %s" % res
    assert res["ct"] == 104.9, "кадр тронул currentTime: %s" % res
    # Звук камеры заглушен, пока играет дорожка: иначе слышно два голоса разом.
    assert res["muted"] is True, "звук камеры не заглушен под дорожкой: %s" % res


@node
def test_big_drift_is_fixed_by_seek(tmp_path: Path) -> None:
    """Расхождение 0,5 с → перемотка (скоростью такое не догнать)."""
    res = _run_node(tmp_path, _tick_body(0.5))

    assert res["seeksDone"] == [105], "дорожку не перемотали на место: %s" % res
    assert res["rate"] == 1, "перемотка оставила скорость задранной: %s" % res


@node
def test_pause_places_the_track_exactly(tmp_path: Path) -> None:
    """Пауза ставит дорожку РОВНО, без ожидания порога: подъезжать на паузе некуда.

    (Прыжок через вырез у шага 1 больше не дело дорожки: звук там — очередь блоков в
    буфере Web Audio, tests/test_editor_single_player.py.)
    """
    body = """
const P=P_();                  // плеер по EDL (шаг 3), стоим на 5-й секунде монтажа
P.vids=[new El('video')];P.vids[0]._ct=105;
P.vt={on:true,el:null,path:'C:/cache/dn.wav',fin:false,seq:0,timer:0,poll:0,want:'',
  note:'',vtq:false,vtend:false,live:null,dn:JSON.stringify(%s.denoise),wantDn:'',
  wantFinal:false,warn:'',statesWait:null};
const el=new El('audio');P.vt.el=el;el.readyState=4;
// Пауза: расхождение 0,2 с скоростью гасить нельзя — стоим ровно на месте
el.currentTime=104.8;el.seeks.length=0;el.paused=true;el.playbackRate=1.06;
vtTick(P,5);
const onPause={rate:el.playbackRate,ct:el.currentTime,seeks:el.seeks.slice()};
console.log(JSON.stringify({onPause:onPause}));
""" % json.dumps(FX, ensure_ascii=False)
    res = _run_node(tmp_path, body)

    assert res["onPause"]["ct"] == 105, "на паузе дорожка стоит не на месте: %s" % res
    assert res["onPause"]["rate"] == 1, "на паузе скорость осталась задранной: %s" % res


# --------------------------------------------------------------------------- #
# 3. Питон: хост отдаёт состояния, гашение их забирает, вход — дорожка шумодава
# --------------------------------------------------------------------------- #
from core import voicefx, voicefx_editor as ed  # noqa: E402


class _FakePlugin:
    """Плагин-пустышка: помнит состояние, «показывает окно», пропускает звук."""

    def __init__(self, path: str, name: str, state: bytes = b"") -> None:
        self.path = path
        self.name = name
        self.raw_state = state

    def __call__(self, chunk: Any, sr: float, reset: bool = False) -> Any:
        return chunk

    def show_editor(self) -> None:
        return None


def _event_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Файл событий хоста: его подменяет `_EVENTS_FILE` в процессе редактора."""
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(ed, "_EVENTS_FILE", str(path))
    return str(path)


def test_host_dump_states_reports_every_loaded_plugin(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`dump_states` отдаёт состояние ВСЕХ загруженных плагинов, включая выключенный.

    Состояние знает только процесс хоста, и по этой команде оно уезжает серверу перед
    гашением. Выключенный плагин тоже загружен (его держат ради галки) — и его ручки
    человек крутит, поэтому он в списке наравне с включённым.
    """
    events = _event_names(tmp_path, monkeypatch)
    loaded = [
        ({"path": "C:/a.vst3", "name": "", "on": True}, _FakePlugin("C:/a.vst3", "", b"\x01\x02")),
        ({"path": "C:/b.vst3", "name": "Shell", "on": False},
         _FakePlugin("C:/b.vst3", "Shell", b"\x03\x04\x05")),
    ]
    ed._dump_states(loaded)

    lines = [json.loads(x) for x in Path(events).read_text(encoding="utf-8").splitlines()]
    ev = next(e for e in lines if e["event"] == "states")
    assert ev["plugins"] == [
        {"path": "C:/a.vst3", "name": "", "state_b64": base64.b64encode(b"\x01\x02").decode()},
        {"path": "C:/b.vst3", "name": "Shell",
         "state_b64": base64.b64encode(b"\x03\x04\x05").decode()},
    ], ev["plugins"]


def test_live_stop_dumps_states_before_killing(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """`live_stop` при открытом окне СНАЧАЛА шлёт `dump_states`, потом гасит по PID.

    Гашение по PID — единственный способ снять зависший хост, но ответа он уже не даёт:
    без состояния перед ним накрученное в окнах пропадало. Хук получает состояния,
    пока сессия жива.
    """
    sent: list[dict[str, Any]] = []
    killed: list[Any] = []
    states = [{"path": "C:/a.vst3", "state_b64": base64.b64encode(b"\x07").decode()}]

    class _Proc:
        pid = 4242

        def poll(self) -> None:
            return None

        stdin = SimpleNamespace(write=lambda s: sent.append(json.loads(s)),
                                flush=lambda: None, close=lambda: None)
        returncode = 0

    monkeypatch.setattr(voicefx, "_kill", lambda proc, what: killed.append(proc))
    # Рабочая папка сессии — своя: `live_finish` убирает её вместе с содержимым.
    work = tmp_path / "work"
    work.mkdir()
    events = _event_names(tmp_path, monkeypatch)
    ses = voicefx._session_new(str(work), _Proc(), state_out="", track_file="",
                               chain=[{"path": "C:/a.vst3"}], index=0,
                               speaker="Мясников", fx={}, device="", start=0.0, src="",
                               events_file=events)

    def fake_dump(session: Any, timeout: float = 0.0) -> list[dict[str, str]]:
        """Вместо ожидания события — сразу пишем то, что отдал бы хост.

        Команду отправляет БОЕВОЙ `live_dump_request`: проверяем, что она вообще
        ушла в трубу хоста, а потом отдаём событие так, как его пишет хост.
        """
        before = voicefx.live_dump_request(session)
        assert sent and sent[0].get("cmd") == "dump_states", sent
        with open(events, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "states", "plugins": states}) + "\n")
        return voicefx.live_dump_wait(session, before, timeout=0.5)

    monkeypatch.setattr(voicefx, "live_dump_states", fake_dump)
    got: list[Any] = []
    assert voicefx.live_stop(ses, lambda s, st: got.append((s, st))) is True

    assert got and got[0][0] is ses and got[0][1] == states, got
    assert killed, "хост не погашен по PID"
    assert sent, "перед гашением не спросили состояния (команда хосту не ушла)"
    assert sent[0].get("cmd") == "dump_states", \
        "перед гашением не спросили состояния: %s" % sent


def test_live_events_parses_the_states_event(tmp_path: Path) -> None:
    """Событие `states` разбирается в сессию: путь + base64, битый base64 — мимо."""
    good = base64.b64encode(b"\x09\x08").decode()
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        json.dumps({"event": "states", "plugins": [
            {"path": "C:/a.vst3", "name": "", "state_b64": good},
            {"path": "", "state_b64": good},
            {"path": "C:/c.vst3", "state_b64": "не-base64!!"},
        ]}, ensure_ascii=False),
    ]) + "\n", encoding="utf-8")
    ses = voicefx._session_new(str(tmp_path), SimpleNamespace(pid=1, poll=lambda: 0),
                               state_out="", track_file="", chain=[], index=-1,
                               speaker="", fx={}, device="", start=0.0, src="",
                               events_file=str(events))
    voicefx.live_events(ses)

    assert ses.states == [{"path": "C:/a.vst3", "state_b64": good}], ses.states
    assert ses.states_seq == 1


def test_host_input_is_the_denoise_track_when_it_is_cached(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Вход хоста — дорожка шумодава, если шумодав включён и она уже посчитана.

    Хост обязан играть тот же голос, что уйдёт в итог: сырой звук камеры — это не он.
    Сырой остаётся ровно тем, кому играть больше нечего (кеша нет — пока RoFormer
    считает, человеку нужно слышать голос, а не тишину).
    """
    src = tmp_path / "cam1.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.zeros((48000, 1), dtype="<i2").tobytes())
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    fx = voicefx.normalize_fx(FX)
    dn = fx["denoise"]
    cache = voicefx.denoise_cache_path(str(src), dn)

    tracks: list[tuple[str, str]] = []

    def fake_denoise(s: str, d: dict[str, Any], **kw: Any) -> str:
        tracks.append((s, "on" if d.get("on") else "off"))
        return cache
    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise)
    monkeypatch.setattr(voicefx, "live_track",
                        lambda session, path, at=None: True)

    ses = voicefx._session_new(str(tmp_path), SimpleNamespace(pid=1, poll=lambda: 0),
                               state_out="", track_file="", chain=[], index=-1,
                               speaker="", fx=fx, device="", start=0.0, src=str(src))
    # Кеша нет: хост получает сырой звук сразу, а дорожку досчитывает следом.
    voicefx._prepare_track(ses)
    assert [t[1] for t in tracks] == ["off", "on"], \
        "без кеша хост сначала не получил сырой звук: %s" % tracks
    assert ses.track_input == "denoised", ses.track_input

    # Кеш появился: хост играет дорожку шумодава СРАЗУ, без захода через сырой звук.
    tracks.clear()
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    Path(cache).write_bytes(b"RIFF")
    voicefx._prepare_track(ses)
    assert [t[1] for t in tracks] == ["on"], \
        "с посчитанной дорожкой хост пошёл играть сырой звук: %s" % tracks
    assert ses.track_input == "denoised" and ses.track_ready is True
