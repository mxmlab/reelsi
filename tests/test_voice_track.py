# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обработанный голос в превью: одна дорожка на все превью, прокси — всегда со звуком камеры.

Голос клипа играет ОТДЕЛЬНОЙ дорожкой `<audio>` в плеере (`vt*`, static/app/60-preview.js):
видео — хозяин, звук подводится к его позиции. В видео он больше не вшивается, поэтому
превью-прокси пересобирается только от исходника камеры.

Три проверки, каждая ломается молча:

1. превью шага 3 подключает ТУ ЖЕ дорожку и той же дверью (`/api/voicefx_bake`): копии
   блока в `85-inserts-view.js` нет, а настройки шаг 3 берёт из профиля спикера — панели
   «Голос» у него нет. Там же — гейт по активной камере: слушают камеру 2, значит звучит
   она сама, а дорожка камеры 1 молчит;
2. заказ превью-прокси не зависит от настроек голоса: путь прокси камеры один и тот же
   при включённой обработке с запечённым голосом, при выключенной и без спикера;
3. сборка прокси показывает НАСТОЯЩИЙ процент (`pct > 0`) до своего конца — по ходу
   ffmpeg (`-progress pipe:1`), а не «0 %» на всю сборку.

Стенд с node гоняет БОЕВЫЕ функции плеера (тела берутся из файла, копий в тесте нет):
в браузере это место не увидеть без живого сервера, клипа и плагинов.

Запуск:  py -3.10 -m pytest tests/test_voice_track.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

QUEUE_JS = ROOT / "static" / "app" / "40-queue.js"
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
VIEW_JS = ROOT / "static" / "app" / "85-inserts-view.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


# --------------------------------------------------------------------------- #
# Стенд: боевые функции дорожки под node на мини-DOM
# --------------------------------------------------------------------------- #
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


def _track_src(*extra: str) -> str:
    """Тела функций дорожки (`vt*`), дверь спикера и названные функции шага 3.

    `vtProfileFx` берёт профиль спикера клипа через `clipSpeaker(c)`, а живёт дверь
    в `40-queue.js`: стенд собирает только `60-preview.js`, и без неё он падал бы на
    `ReferenceError`. Вырезаем дверь из её файла (приём `tests/test_two_speakers.py`),
    а не копируем текстом — копия разъехалась бы с боевой молча.
    """
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    view = VIEW_JS.read_text(encoding="utf-8")
    queue = QUEUE_JS.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"^(?:async\s+)?function\s+(vt[A-Za-z0-9_$]*)\s*\(",
                                  preview, re.M)))
    assert names, "в 60-preview.js нет ни одной функции дорожки (vt*)"
    out = [_func_src(preview, n) for n in names]
    out.append(_func_src(queue, "clipSpeaker"))
    out += [_func_src(view, n) for n in extra]
    return "\n".join(out)


DOM = r"""
// ---- мини-DOM: только то, что нужно дорожке (элемент звука и плеер) ----
class El{
  constructor(tag){this.tag=tag;this.attrs={};this.dataset={};this.style={};this.src='';
    this.volume=1;this.readyState=0;this.paused=true;this.seeking=false;this.currentTime=0;
    this.preload='';this.muted=false;this.played=0;this.pauseLog=0;this.seekLog=[];this._ev={};}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k];}
  removeAttribute(k){delete this.attrs[k];if(k==='src')this.src='';}
  addEventListener(k,f){(this._ev[k]=this._ev[k]||[]).push(f);}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  load(){}
}
const MADE=[];
globalThis.document={createElement:t=>{const el=new El(t);MADE.push(el);return el;}};
"""

STATE = r"""
// Состояние страницы, к которому обращаются боевые функции дорожки. Пороги синхрона
// подставляются ИЗ ФАЙЛА (см. `_run_node`): свои копии разъезжались бы с боевыми
// молча — перемотка становится скоростью, и стенд проверял бы не то.
let MEDIA_VOL=1;
function voiceWiring(){}
function $(id){return (globalThis.__byId&&globalThis.__byId[id])||null;}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function uiLog(m){LOGS.push(String(m));}
const LOGS=[];
function errText(d){return (d&&(d.error||d.err))||'';}
function clipByXml(xml){return CLIPS.find(c=>c.xml===xml);}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
let CLIPS=[];
let SPEAKERS={};
const PROGCALLS=[];
function progOpen(o){PROGCALLS.push(['open',(o&&o.title)||'']);}
function progItem(n,ev,x){PROGCALLS.push(['item',n,ev]);}
function progDone(m,bad){PROGCALLS.push(['done',m||'',!!bad]);}
function progUpdate(f,s){PROGCALLS.push(['update',f==null?null:f,s||'']);}
function progMini(){PROGCALLS.push(['mini']);}
function hideProg(){PROGCALLS.push(['hide']);}
// Плеер шага 3 — тот же объект, что собирает ipvOpen (85-inserts-view.js)
let IPV={vids:[],bufs:[],segs:[],audio:[],words:[],dur:0,fps:60,aidx:0,curCi:-1,
  playing:false,scrubbing:false,raf:0,xml:'',plan:null};
"""


def _consts() -> str:
    """Пороги синхрона дорожки — из 60-preview.js, а не копией в стенде.

    Копия разъезжается с боевыми молча: пока в стенде стоял `VT_DRIFT=0.15`, а в
    файле стало 0.25 (перемотка превратилась в подводку скоростью), стенд проверял
    бы прежнее поведение и был бы зелёным на сломанном.
    """
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    out = []
    for name in ("VT_SOFT", "VT_DRIFT", "VT_RATE", "VT_QUIET", "VT_POLL"):
        m = re.search(r"^const %s=.*$" % name, preview, re.M)
        assert m is not None, f"в 60-preview.js нет const {name}"
        out.append(m.group(0))
    return "\n".join(out)


def _run_node(body: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    import tempfile
    src = (DOM + _consts() + "\n" + STATE + _track_src("ipvNow", "ipvNowFrame")
           + "\n" + body)
    with tempfile.TemporaryDirectory(prefix="voice_track_") as d:
        path = Path(d) / "stand.js"
        path.write_text(src, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60,
                              cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


@node
def test_step_three_plays_the_same_track() -> None:
    """Превью шага 3 подключает дорожку той же дверью и берёт настройки из профиля.

    Панели «Голос» у шага 3 нет, поэтому настройки едут из профиля спикера клипа — те
    самые, по которым соберётся проект. Трека при этом ДВА не заводится: играет тот же
    `<стем>.voice.wav` через `/api/media`, а видео — хозяин: время `<video>` дорожка не
    трогает вообще, только подводит к нему звук.
    """
    body = r"""
IPV.cams=[{path:'C:/cam1.mp4'},{path:'C:/cam2.mp4'}];
IPV.audio=[{ts:0,te:10,src:100},{ts:10,te:20,src:300}];
IPV.segs=[{ts:0,te:10,src:100,ci:0},{ts:10,te:20,src:300,ci:0}];
IPV.dur=20;IPV.aidx=0;IPV.xml='C:/out/01_clip.xml';
CLIPS=[{xml:'C:/out/01_clip.xml',name:'01_clip',job:{speaker:'Голос'}}];
SPEAKERS={'Голос':{label:'Голос',voice_fx:{denoise:{on:true,engine:'roformer',mix:70},vst:[]}}};
const v=document.createElement('video'),v2=document.createElement('video');
v.readyState=4;v.currentTime=100;v.paused=false;v2.currentTime=100;
IPV.vids=[v,v2];IPV.playing=true;
const asked=[];
globalThis.fetch=async(url,opt)=>{asked.push([String(url),opt?JSON.parse(opt.body):null]);
  if(String(url)==='/api/voicefx_bake')
    return {json:async()=>({ok:true,ready:true,running:false,path:'C:/out/01_clip.voice.wav'})};
  return {json:async()=>({ok:true})};};
(async()=>{
  await vtPrep(IPV);
  const el=IPV.vt.el;
  el.readyState=4;
  // Кадр плеера: видео стоит на 103-й секунде исходника — первый кусок, 3-я секунда
  vtTick(IPV,3);
  const first={tm:el.currentTime,mute:!!IPV.voiceMute,cam1:v.muted,cam2:v2.muted,
    paused:el.paused,seeks:v.seekLog.length,pauses:v.pauseLog};
  // Стык: второй кусок, время считается от ЕГО начала
  IPV.aidx=1;v.currentTime=300;
  vtTick(IPV,10.5);
  const afterSplice=el.currentTime;
  // Слушают камеру 2 — звучит она сама, дорожка камеры 1 молчит
  IPV.audioCi=1;vtTick(IPV,10.5);
  const cam2={mute:!!IPV.voiceMute,voicePaused:el.paused,cam2:v2.muted,cam1:v.muted};
  IPV.audioCi=0;vtTick(IPV,10.5);
  const back={mute:!!IPV.voiceMute,voicePaused:el.paused,cam2:v2.muted};
  console.log(JSON.stringify({asked:asked,on:IPV.vt.on,path:IPV.vt.path,src:el.src,
    first:first,afterSplice:afterSplice,cam2:cam2,back:back,
    audios:MADE.filter(e=>e.tag==='audio').length,
    vtime:v.currentTime,vpaused:v.paused}));
})();
"""
    res = _run_node(body)

    # Дверь — та же, что у панели «Голос» (шаг 1): запекание клипа по файлу камеры 1
    bake = [a for a in res["asked"] if a[0] == "/api/voicefx_bake"]
    assert len(bake) == 1, f"шаг 3 не просит голос клипа: {res['asked']}"
    assert bake[0][1]["xml"] == "C:/out/01_clip.xml", bake[0][1]
    assert bake[0][1]["src"] == "C:/cam1.mp4", bake[0][1]
    assert bake[0][1]["fx"] == {"denoise": {"on": True, "engine": "roformer", "mix": 70},
                                "vst": []}, \
        "настройки шага 3 взяты не из профиля спикера: %s" % bake[0][1]["fx"]
    # Дорожка подключена: тот же файл голоса, что уезжает в AE, и через /api/media
    assert res["on"] is True and res["path"] == "C:/out/01_clip.voice.wav", res
    assert res["src"] == "/api/media?path=C%3A%2Fout%2F01_clip.voice.wav", res["src"]
    assert res["audios"] == 1, "дорожка завела лишний элемент звука"
    # Звук подводится к позиции ВИДЕО, а не наоборот
    assert res["first"]["tm"] == 103, res["first"]
    assert res["afterSplice"] == 300.5, "время звука на стыке считается не от куска"
    # ...и видео дорожка не трогает вовсе: ни seek, ни паузы
    assert res["first"]["seeks"] == 0 and res["first"]["pauses"] == 0, res["first"]
    assert res["vpaused"] is False and res["vtime"] == 300, res
    # Звук камеры заглушён, пока играет обработанный голос
    assert res["first"]["mute"] is True and res["first"]["cam1"] is True, res["first"]
    # Камера 2 звучит сама: её никто не глушит, а дорожка камеры 1 молчит
    assert res["cam2"]["mute"] is False and res["cam2"]["voicePaused"] is True, res["cam2"]
    assert res["cam2"]["cam2"] is False and res["cam2"]["cam1"] is True, res["cam2"]
    assert res["back"]["mute"] is True and res["back"]["voicePaused"] is False, res["back"]


def test_track_code_lives_in_one_place() -> None:
    """Дорожка объявлена ОДИН раз, а шаг 3 только зовёт её.

    Копия блока в `85-inserts-view.js` — это второе место, где решается «как звучит
    голос»: разъехавшиеся копии и дали разный звук у превью шага 1 и шага 3.
    """
    view = VIEW_JS.read_text(encoding="utf-8")
    for name in ("vtPrep", "vtTick", "vtPause", "vtStop"):
        assert f"function {name}(" not in view, f"в 85-inserts-view.js завелась копия {name}"
    # Своей двери «запечь голос» и своего решения «глушить ли звук камеры» у шага 3 нет
    assert "voicefx_bake" not in view, "шаг 3 ходит в запекание голоса своей дверью"
    assert "voiceMute=" not in view, "шаг 3 сам глушит звук камеры (копия vtGate)"
    # ...а зовёт общие: открытие, кадр, протяжка и пауза
    assert "vtPrep(IPV)" in view, "открытие превью шага 3 не подключает дорожку"
    assert "vtTick(IPV," in view, "кадр плеера шага 3 не ведёт дорожку"
    assert "vtPause(IPV)" in view, "пауза превью шага 3 не останавливает дорожку"
    assert "vtStop(IPV)" in view, "переоткрытие клипа не отцепляет прежнюю дорожку"


@node
def test_steps23_live_host_fallback_and_final_audio_switch() -> None:
    """Шаги 2-3: готов -> <audio> с voice.wav, живой хост не зовётся НИКОГДА.

    У шага 3 нет ни панели «Голос», ни окон плагинов: крутить нечего, а живой хост —
    отдельный процесс, который не подогнать к картинке точнее 0,4 с (с включёнными VST
    превью лагало). Поэтому оба случая — и готовый голос, и ещё считающийся — идут
    ИТОГОВЫМ заказом (`final: true`), а пока трек считается, звучит звук камеры.
    """
    body = r"""
let hostSyncCalls = [];
let hostStopCalls = 0;
globalThis.voiceFxHostSync = async function(fx, opts) {
  hostSyncCalls.push({ fx: fx, player: (opts && opts.player) || null });
  return true;
};
globalThis.voiceFxHostStop = async function() {
  hostStopCalls++;
  return true;
};
globalThis.voiceFxHostDump = async function() { return null; };
globalThis.voiceFxHostOn = function() { return false; };

IPV.cams = [{ path: 'C:/cam1.mp4' }];
IPV.audio = [{ ts: 0, te: 10, src: 100 }];
IPV.segs = [{ ts: 0, te: 10, src: 100, ci: 0 }];
IPV.dur = 10;
IPV.aidx = 0;
IPV.xml = 'C:/out/01_clip.xml';
CLIPS = [{ xml: 'C:/out/01_clip.xml', name: '01_clip', job: { speaker: 'Голос' } }];
SPEAKERS = { 'Голос': { label: 'Голос', voice_fx: { denoise: { on: true, engine: 'roformer', mix: 70 }, vst: [] } } };

// Случай 1: итоговый голос уже готов (ready: true)
let bakeResponses = [
  { ok: true, ready: true, running: false, path: 'C:/out/01_clip.voice.wav' }
];
let bakeCalls = [];
globalThis.fetch = async(url, opt) => {
  const u = String(url);
  if (u === '/api/voicefx_bake') {
    const body = opt ? JSON.parse(opt.body) : null;
    bakeCalls.push({ url: u, body: body });
    return { json: async() => bakeResponses.shift() };
  }
  if (u.indexOf('/api/voicefx_bake_status') === 0) {
    return { json: async() => ({ ok: true, ready: true, done: true, path: 'C:/out/01_clip.voice.wav' }) };
  }
  return { json: async() => ({ ok: true }) };
};

(async() => {
  // Сценарий 1: готов сразу
  await vtPrep(IPV);
  const case1 = {
    bakeCalls: bakeCalls.length,
    finalFlag: bakeCalls[0].body.final,
    hostSyncs: hostSyncCalls.length,
    hostStops: hostStopCalls,
    audioOn: IPV.vt.on,
    audioPath: IPV.vt.path
  };

  // Сценарий 2: голос НЕ готов (running: true) -> играет звук камеры, идёт опрос хода
  vtStop(IPV);
  hostSyncCalls = [];
  hostStopCalls = 0;
  bakeCalls = [];
  bakeResponses = [
    { ok: true, ready: false, running: true, queued: false }
  ];
  await vtPrep(IPV);
  const case2Before = {
    bakeCalls: bakeCalls.length,
    finalFlag: bakeCalls[0].body.final,
    hostSyncs: hostSyncCalls.length,
    hostPlayerIsIpv: hostSyncCalls[0] ? hostSyncCalls[0].player === IPV : false,
    audioOn: IPV.vt.on
  };

  // Имитируем завершение запекания: вызываем vtVoicePoll
  await vtVoicePoll(IPV, IPV.vt.seq);
  const case2After = {
    audioOn: IPV.vt.on,
    audioPath: IPV.vt.path,
    hostStops: hostStopCalls
  };

  console.log(JSON.stringify({
    case1: case1,
    case2Before: case2Before,
    case2After: case2After
  }));
})();
"""
    res = _run_node(body)
    # Случай 1
    assert res["case1"]["bakeCalls"] == 1
    assert res["case1"]["finalFlag"] is True, "запрос запекания для шагов 2-3 не несёт final: true"
    assert res["case1"]["hostSyncs"] == 0, "для готового голоса поднялся живой хост"
    assert res["case1"]["audioOn"] is True and res["case1"]["audioPath"] == "C:/out/01_clip.voice.wav"

    # Случай 2: шаг 3 живым хостом не пользуется никогда — ни с готовым, ни со считающимся
    assert res["case2Before"]["bakeCalls"] == 1, "итоговый голос не заказан"
    assert res["case2Before"]["finalFlag"] is True, \
        "для неготового голоса заказан не итоговый трек"
    assert res["case2Before"]["hostSyncs"] == 0, \
        "у шага 3 нет окон плагинов — живому хосту там взяться неоткуда"
    assert res["case2Before"]["audioOn"] is False, \
        "пока трек считается, играет звук камеры, а не пустая дорожка"
    assert res["case2After"]["audioOn"] is True and res["case2After"]["audioPath"] == "C:/out/01_clip.voice.wav"


# --------------------------------------------------------------------------- #
# Заказ прокси: настроек голоса он не знает
# --------------------------------------------------------------------------- #
FX_FINAL: dict[str, Any] = {"denoise": {"on": True, "engine": "roformer", "mix": 70},
                            "vst": []}
FX_OFF: dict[str, Any] = {"denoise": {"on": False}, "vst": []}


@pytest.fixture
def clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Клип с камерой 1 и обработкой голоса: план и роут смотрят на один и тот же XML."""
    from api import previewproxy
    from core import draftrender, speakers, voicefx, xml2ae

    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    spk = tmp_path / "speakers"
    spk.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(spk))
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    cam1 = tmp_path / "cam1.mp4"
    cam1.write_bytes(b"cam1")
    (tmp_path / "_tmp").mkdir()
    monkeypatch.setattr(xml2ae, "virtual_edl",
                        lambda p, ncams=None: {"cams": [{"path": str(cam1)}]})
    return previewproxy, draftrender, speakers, voicefx, str(xml), str(cam1)


def _speaker_with_fx(speakers: Any, xml: str, cam1: str, fx: dict[str, Any],
                     baked: bool) -> None:
    """Спикер клипа (сайдкар нарезки + профиль) и, если нужно, запечённый голос."""
    from core import voicefx
    from core.project_file import write_project

    speakers.save("Голос", {"label": "Голос", "voice_fx": fx})
    write_project(os.path.splitext(xml)[0] + ".project.json",
                  {"cams": [cam1], "speaker": "Голос"})
    if baked:
        dst = voicefx.final_voice_path(xml)
        Path(dst).write_bytes(b"RIFF voice")
        with open(os.path.splitext(xml)[0] + ".voice.json", "w", encoding="utf-8") as f:
            json.dump({"key": voicefx.final_voice_key(cam1, fx), "src": cam1}, f)


def test_proxy_path_does_not_depend_on_voice_settings(clip: Any) -> None:
    """Путь превью-прокси камеры один и тот же при любых настройках голоса.

    Прокси — от ИСХОДНИКА и всегда со звуком камеры: обработанный голос играет отдельной
    дорожкой и в видео не вшивается. Иначе смена любой ручки шумодава давала бы новый
    хеш, новый файл и полную пересборку прокси камеры на десятки секунд.
    """
    previewproxy, draftrender, speakers, voicefx, xml, cam1 = clip
    tdir = os.path.join(os.path.dirname(xml), "_tmp")

    plain = previewproxy._preview_proxy_plan(xml, 720)[0]
    assert plain[0][1] == draftrender.preview_path(cam1, 720, tdir)

    _speaker_with_fx(speakers, xml, cam1, FX_FINAL, baked=True)
    with_voice = previewproxy._preview_proxy_plan(xml, 720)[0]

    _speaker_with_fx(speakers, xml, cam1, FX_OFF, baked=False)
    without = previewproxy._preview_proxy_plan(xml, 720)[0]

    assert with_voice[0][1] == plain[0][1], \
        f"включённая обработка переписала путь прокси: {with_voice[0][1]}"
    assert without[0][1] == plain[0][1], \
        f"выключенная обработка переписала путь прокси: {without[0][1]}"
    assert "voice" not in os.path.basename(plain[0][1]), \
        f"в имени прокси снова голос: {plain[0][1]}"
    assert voicefx.final_voice_path(xml) == os.path.splitext(xml)[0] + ".voice.wav"


def test_route_does_not_order_voice_work(clip: Any) -> None:
    """Роут прокси не заказывает голос: все прокси готовы — сборка не поднимается.

    Раньше этот же роут пёк `<стем>.voice.wav` первым шагом сборки, и «голос ещё не
    запечён» запускало работу даже тогда, когда собирать было нечего: на экране висело
    «сборка прокси 1/1 · 0 %» — у запекания процента сборки прокси не бывает.
    """
    from flask import Flask
    import api as api_pkg
    from api import previewproxy

    previewproxy_, draftrender, speakers, voicefx, xml, cam1 = clip
    app = Flask(__name__)
    app.register_blueprint(api_pkg.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    _speaker_with_fx(speakers, xml, cam1, FX_FINAL, baked=False)
    voice_wav = voicefx.final_voice_path(xml)
    assert not os.path.exists(voice_wav), "голос уже запечён — проверяем случай «ещё нет»"
    plain = draftrender.preview_path(cam1, 720, os.path.join(os.path.dirname(xml), "_tmp"))
    Path(plain).write_bytes(b"proxy")           # прокси камеры уже собран

    d = client.post("/api/preview_proxy", json={"xml": xml, "build": True}).get_json()
    assert d["ok"] is True, d
    assert d["cams"][0]["proxy"] == plain, d["cams"]
    assert d["cams"][0]["ready"] is True
    assert d["building"] is False, "роут прокси поднял сборку из-за настроек голоса"
    assert not os.path.exists(voice_wav), \
        "роут прокси запекает голос — это дело двери /api/voicefx_bake"
    with previewproxy.PXLOCK:
        assert previewproxy.PXJOB["running"] is False


# --------------------------------------------------------------------------- #
# Процент сборки: настоящий и до её конца
# --------------------------------------------------------------------------- #
def test_proxy_build_reports_real_percent_before_it_ends(clip: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока прокси собирается, `pct` уже больше нуля — процент идёт от самого ffmpeg.

    «0 %» на всю сборку владелец видел живьём: сборка показывала процент ЗАПЕКАНИЯ
    голоса (у него процента прокси нет), а не сборки файла. Здесь ffmpeg подменён и
    печатает ровно то, что печатает боевой с `-progress pipe:1`: 4 секунды из 10.
    """
    previewproxy, draftrender, _speakers, _voicefx, xml, cam1 = clip
    tdir = os.path.join(os.path.dirname(xml), "_tmp")
    plan = previewproxy._preview_proxy_plan(xml, 720)[0]
    assert plan and plan[0][2] is False, "прокси камеры уже готов — собирать нечего"

    monkeypatch.setattr(draftrender, "_display_dims", lambda s: (1080, 1920, False))
    monkeypatch.setattr(draftrender, "_src_fps", lambda s: 25.0)
    monkeypatch.setattr(draftrender, "_src_dur", lambda s: 10.0)
    monkeypatch.setattr(draftrender, "hw_encoder", lambda refresh=False: None)
    monkeypatch.setattr(previewproxy, "_cross_lock_release", lambda: None)

    seen: list[tuple[int, bool]] = []           # (pct, файл уже на месте?) в момент приёма

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        on_progress = kw.get("on_progress")
        assert on_progress is not None, "проценты сборки не запрошены у ffmpeg"
        on_progress("frame=1\nout_time_us=4000000\nprogress=continue\n")
        with previewproxy.PXLOCK:
            seen.append((previewproxy.PXJOB["pct"], os.path.isfile(cmd[-1])))
        Path(cmd[-1]).write_bytes(b"ff")        # «ffmpeg дописал файл»
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(draftrender, "_run_ff", fake_run)
    previewproxy._run_preview_proxy(plan, 720)

    assert seen == [(40, False)], f"процента сборки не было до её конца: {seen}"
    assert os.path.isfile(plan[0][1]), "прокси не встал на место из плана"
    with previewproxy.PXLOCK:
        assert previewproxy.PXJOB["pct"] == 40, previewproxy.PXJOB
        assert previewproxy.PXJOB["running"] is False
    assert tdir and os.path.dirname(plan[0][1]) == tdir
