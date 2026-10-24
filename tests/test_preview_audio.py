# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук превью: камера играет свой звук, граф будится одной дверью, Firefox — строка.

Три вещи, каждая из которых ломается МОЛЧА (все — с живого прогона):

1. **Тишина на шаге 1.** Приостановленный `AudioContext` (браузер держит его в
   `suspended`, пока не было живого жеста) глушит звук целиком — без ошибки в консоли.
   Плеер шага 3 звучал потому, что `ipvPlay` в конце звал `musicSync`, то есть граф
   будился ПОБОЧНО через музыку, а шаг 1 не будил никто. Теперь `audioWake()` — ОДНА
   дверь пробуждения контекста, и её зовут все запуски воспроизведения.
2. **Звук элементов в граф не заводится вовсе.** Все плееры со звуком немые (`silent`):
   звук шага 1 и голос (громкость, цензура) играет очередь Web Audio `ea*`. `<video>`
   через `createMediaElementSource` в граф не подключается; единственный вход в граф —
   музыка (`musicEnsure`). Громкость голоса стиля (`eaGain`) — гейн очереди, а не
   `VG.gain`: дорожка `<audio>` голоса снята 2026-10-23, и граф для голоса не нужен.
3. **Звука нет, и никто не говорит почему.** Материал камер пишет `pcm_s16be` в MP4:
   Chromium эту дорожку читает, Firefox — нет и молча. Звуковой прокси под это был
   костылём и в Chromium играл ПОВЕРХ звука камеры (двойной голос у владельца) —
   он убран целиком, а причина молчания называется строкой по ФАКТУ элемента:
   `<video>.mozHasAudio` есть только в Firefox, поэтому в Chromium строки не бывает
   никогда. Строка живёт в общем контейнере прогресса плеера (`pvProgRow`) и уходит,
   когда источник сменился на прокси (там дорожка уже aac).

Плюс pedalboard: он не был объявлен НИГДЕ, и человек узнавал о нехватке только в
момент включения плагинов; а список устройств вывода умирал вместе с плагинами,
хотя устройства — это звуковая система, а не VST3.

Запуск:  py -3.10 -m pytest tests/test_preview_audio.py -q
"""
from __future__ import annotations

import io
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

APP = ROOT / "static" / "app"
PREVIEW_JS = APP / "60-preview.js"
STYLES_JS = APP / "95-styles.js"
EDITOR_JS = APP / "70-editor.js"
VIEW_JS = APP / "85-inserts-view.js"
CAMJS = APP / "88-cams.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def _read(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


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


def _fx_consts(src: str) -> str:
    """Порог строки Firefox — из файла: своя копия в стенде разъехалась бы с боевой."""
    m = re.search(r"^const FIREFOX_FACT_MIN=[^\r\n]*$", src, re.M)
    assert m is not None, "пропал порог FIREFOX_FACT_MIN"
    return m.group(0)


def _run_node(tmp_path: Path, name: str, script: str) -> subprocess.CompletedProcess[str]:
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    return subprocess.run(["node", str(f)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def _assert_node(res: subprocess.CompletedProcess[str], *marks: str) -> None:
    out = (res.stdout or "") + (res.stderr or "")
    assert res.returncode == 0, out
    for m in marks:
        assert m in (res.stdout or ""), out


# --------------------------------------------------------------------------- #
# 1. pedalboard объявлен: requirements и doctor
# --------------------------------------------------------------------------- #
def test_pedalboard_is_declared_in_optional_requirements():
    """pedalboard не был объявлен ни в одном файле зависимостей — ни в
    requirements*, ни в pyproject, ни в установщиках. `pip install -r
    requirements-optional.txt` обязан его ставить: иначе живое прослушивание
    недостижимо в принципе, и человек об этом не узнает."""
    txt = _read(ROOT / "requirements-optional.txt")
    lines = [ln.strip() for ln in txt.splitlines()]
    pkgs = [ln for ln in lines if ln and not ln.startswith("#")]
    assert any(re.split(r"[<>=!]", ln, 1)[0].strip() == "pedalboard" for ln in pkgs), (
        f"pedalboard не объявлен: {pkgs}")
    assert any(ln.lower().startswith("pedalboard") for ln in lines), "нет строки pedalboard"


def test_doctor_names_pedalboard_and_what_turns_off(monkeypatch, capsys):
    """Доктор обязан знать про pedalboard и говорить, ЧТО отключится.

    Печатать «Ready to work», проверяя только половину голосового пайплайна
    (RoFormer), — и есть та самая жалоба: человек узнаёт о нехватке в момент
    включения, из ответа роута.
    """
    import doctor

    names = [n for n, _f in doctor.OPTIONAL]
    assert "pedalboard" in names, f"pedalboard нет в OPTIONAL: {names}"
    feature = dict(doctor.OPTIONAL)["pedalboard"]
    assert len(feature) > 5, "не сказано, что именно отключится"

    monkeypatch.setattr(doctor, "_rows", [])
    monkeypatch.setattr(doctor, "_bad", 0)
    monkeypatch.setattr(doctor, "_mod", lambda n: (False, "ModuleNotFoundError"))
    doctor.check_optional()
    out = "\n".join(" ".join(r) for r in doctor._rows)
    assert "pedalboard" in out, f"доктор молчит про pedalboard:\n{out}"
    assert doctor.t(feature) in out, "не сказано, какая функция отключится"
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# 2. Устройства вывода не зависят от плагинов
# --------------------------------------------------------------------------- #
def test_devices_live_without_pedalboard(monkeypatch):
    """Без pedalboard список устройств отдаётся ЗАПАСНЫМ путём, а не ошибкой про
    плагины: перечисление устройств — вопрос звуковой системы.

    Сбор списка поднимает аудиосистему JUCE, поэтому идёт дочерним процессом; без
    пакета процесс отвечает кодом `vst_unavailable`, и раньше этот код уезжал
    фронту как ошибка всего роута.
    """
    from core import voicefx
    from core.umsg import ReelsiError, umsg

    def _no_pedalboard(*a: Any, **k: Any) -> None:
        raise ReelsiError(umsg("vst_unavailable",
                               "Нет пакета pedalboard — VST-плагины недоступны: "
                               "pip install pedalboard"))

    monkeypatch.setattr(voicefx, "_DEVICES_CACHE", None)
    monkeypatch.setattr(voicefx, "_run_child", _no_pedalboard)
    monkeypatch.setattr(voicefx, "system_output_devices",
                        lambda: {"devices": ["Динамики (Realtek)", "Наушники"],
                                 "default": ""})

    d = voicefx.output_devices()
    assert d == {"devices": ["Динамики (Realtek)", "Наушники"], "default": ""}, d
    # Причина — отдельной дверью: контракт `{devices, default}` у списка не менялся.
    reason = voicefx.devices_reason()
    assert "pedalboard" in reason, reason
    monkeypatch.setattr(voicefx, "_DEVICES_CACHE", None)
    monkeypatch.setattr(voicefx, "_DEVICES_REASON", "")


def test_devices_route_answers_with_a_reason_not_an_error(client, monkeypatch):
    """Роут отдаёт причину полем `reason` и НЕ отдаёт `err`-код плагинов: иначе
    фронт показал бы ошибку вместо списка устройств."""
    from core import voicefx

    monkeypatch.setattr(voicefx, "_DEVICES_CACHE", None)
    monkeypatch.setattr(voicefx, "_DEVICES_REASON", "")
    monkeypatch.setattr(voicefx, "output_devices",
                        lambda refresh=False: {"devices": [], "default": ""})
    monkeypatch.setattr(voicefx, "devices_reason",
                        lambda refresh=False: "нет пакета pedalboard")
    d = client.get("/api/voicefx_devices").get_json()
    assert d.get("ok") is True and not d.get("error"), d
    assert d.get("devices") == [] and d.get("reason") == "нет пакета pedalboard", d
    monkeypatch.setattr(voicefx, "_DEVICES_CACHE", None)
    monkeypatch.setattr(voicefx, "_DEVICES_REASON", "")


# --------------------------------------------------------------------------- #
# 3. Звукового прокси нет ни во фронте, ни в бэке
# --------------------------------------------------------------------------- #
# Имена дверей и файлов звукового прокси: их не должно остаться НИГДЕ. `pa_` ловит
# и префикс файлов прокси, и `data-pa_txt`; `PVPX_AUDIO` — карту звука с сервера.
_PROXY_REMNANTS = re.compile(
    r"paSync|PVPX_AUDIO|paActive|paMuteCam|paPlay|paPause|paPush|paStop|paElement"
    r"|paNeeded|paReady|paCodec|paSrc|pvAudioNote|audio_proxy|pa_|audioReady|audioWhat")


def test_audio_proxy_is_gone_from_the_frontend():
    """Звуковой прокси убран целиком, а не выключен: ни функции, ни карты с сервера.
    Оставленный мёртвый код — вторая копия ответа на «кто звучит»."""
    for js in (PREVIEW_JS, EDITOR_JS, VIEW_JS, CAMJS):
        found = _PROXY_REMNANTS.findall(_read(js))
        assert not found, f"{js.name}: остатки звукового прокси {sorted(set(found))}"


def test_audio_proxy_is_gone_from_the_backend():
    """Сборки звукового прокси нет ни в `api/previewproxy.py`, ни в `core/media.py`:
    в браузере звучит дорожка камеры, а не отдельный `<audio>` поверх неё."""
    for py in (ROOT / "api" / "previewproxy.py", ROOT / "core" / "media.py"):
        txt = _read(py)
        found = sorted(set(re.findall(
            r"AUDIO_PROXY\w*|audio_proxy\w*|build_audio_proxy|probe_audio_codec"
            r"|audio_needs_proxy|BROWSER_AUDIO_CODECS", txt)))
        assert not found, f"{py.name}: остатки звукового прокси {found}"


def test_preview_proxy_route_has_no_audio_field(client, monkeypatch, tmp_path):
    """Ответ `/api/preview_proxy` больше не несёт поле `audio`: путь, готовность и
    кодек исходника ехали только ради звукового прокси."""
    from api import previewproxy

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h, allintra=False: ([], str(tmp_path)))
    monkeypatch.setattr(previewproxy, "_extra_proxy_plan",
                        lambda paths, h, tdir, allintra: [])
    monkeypatch.setattr(previewproxy, "_cross_lock_release", lambda: None)

    d = client.post("/api/preview_proxy", json={"xml": str(xml)}).get_json()
    assert d.get("ok") is True, d
    assert "audio" not in d, d


def test_run_preview_proxy_takes_only_the_video_plan(monkeypatch):
    """Сборщик прокси собирает РОВНО видео-прокси: третьего аргумента со звуком у
    него нет вовсе — иначе звук снова поехал бы отдельным файлом."""
    from api import previewproxy
    from core import draftrender

    built: list[str] = []
    monkeypatch.setattr(previewproxy, "_cross_lock_release", lambda: None)
    monkeypatch.setattr(draftrender, "build_preview_proxy",
                        lambda s, d, height=0, emit=None, progress=None: built.append(d))
    monkeypatch.setattr(draftrender, "RENDER_PROXY_RE", re.compile("^$"))

    previewproxy._run_preview_proxy([("/c1.mp4", "/pv_1.mp4", False)], 720)
    assert built == ["/pv_1.mp4"], built


# --------------------------------------------------------------------------- #
# 4. node-стенды: audioWake и граф (браузер подменён)
# --------------------------------------------------------------------------- #
# Заглушки ровно под то, что зовут проверяемые двери: DOM, звук, лог.
_AUDIO_STAND = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function t(s){return String(s);}
function $(id){return null;}
function applyDbGains(){}
"""

_AUDIO_FUNCS = ("audioGraph", "audioWake")

# Подмена AudioContext: считает пробуждения (`resumed`). Одна на стенды про граф —
# свои копии разъехались бы с проверками.
_FAKE_AUDIO = r"""
let resumed=0;
function AudioContext(){
  this.state='suspended';
  this.destination={};
  this.createGain=()=>({connect(){},gain:{value:1}});
  this.resume=()=>{resumed++;this.state='running';return Promise.resolve();};
}
global.window=global;
global.AudioContext=AudioContext;
window.AudioContext=AudioContext;
"""


# Дверь громкости голоса (95-styles.js): читает громкость спикера клипа, а без открытого
# клипа — спикер шага 1. Стенду нужен только её контекст; сама дверь — боевая.
_VOICE_DOOR_FUNCS = ("voiceDbSpeakerKey", "voiceDbBySpeaker", "voiceDbOf")
_VOICE_DOOR_STAND = (
    "let curAE=-1,CLIPS=[],SPEAKERS={};\n"
    "const val=()=>'';\n"
    "function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}\n")


def _audio_stand(tmp_path: Path, name: str, body: str,
                 extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    """Стенд по боевым функциям графа: тела берутся из 60-preview.js, копий в тесте нет.

    `extra` — функции СВЕРХ очереди графа (applyDbGains и её dbToGain): нужны стендам про
    громкость голоса стиля. Одноимённая заглушка из `_AUDIO_STAND` при этом перекрывается
    настоящим телом — так и задумано: стенд повторяет боевой граф, а не правится по частям.
    """
    prev = _read(PREVIEW_JS)
    styles = _read(STYLES_JS)
    funcs = tuple(_AUDIO_FUNCS) + tuple(extra)
    script = ("const assert=require('assert');\n" + _AUDIO_STAND
              + "let AUDIO=null,MG=null;\n"
              + "let MEDIA_VOL=1;\n"
              + _VOICE_DOOR_STAND
              + "\n".join(_func_src(styles, n) for n in _VOICE_DOOR_FUNCS) + "\n"
              + "\n".join(_func_src(prev, n) for n in funcs) + "\n"
              + body)
    return _run_node(tmp_path, name, script)


@node
def test_graph_wakes_once(tmp_path):
    """Контекст будится ОДНОЙ дверью (audioWake) и только один раз: повторный пуск не зовёт
    `resume()` снова. Приостановленный AudioContext без этой двери молчит целиком."""
    res = _audio_stand(tmp_path, "audio_wake.js", _FAKE_AUDIO + r"""
audioWake();                     // первый пуск: граф поднят, контекст будится
assert.strictEqual(AUDIO!==null&&AUDIO.state==='running',true,'audioWake не разбудил контекст');
assert.strictEqual(resumed,1,'контекст не разбужен: '+resumed);
// audioWake идемпотентна: повторный пуск не будит контекст второй раз.
audioWake();
assert.strictEqual(resumed,1,'audioWake будит контекст повторно');
console.log('OK: graph wakes once');
""")
    _assert_node(res, "OK: graph wakes once")


@node
def test_edPlay_and_ipvPlay_wake_the_graph(tmp_path):
    """Каждый запуск воспроизведения будит граф САМ (audioWake), а не побочно
    через музыку: раньше во всём фронтенде был ровно один `AUDIO.resume()` — в
    `musicSync`, и плеер шага 3 звучал случайно, а шаг 1 не будил никто."""
    prev, ed, view, cams = (_read(PREVIEW_JS), _read(EDITOR_JS),
                            _read(VIEW_JS), _read(CAMJS))
    script = ("const assert=require('assert');\n" + _AUDIO_STAND
              + "let MEDIA_VOL=1;let woke=0;function audioWake(){woke++;}\n"
              + "function pvAudioLimit(){}function vtOf(){return {failed:false};}function vtPrep(){}\n"
              + "function edBlockAt(){return 0;}function spareIdle(){}\n"
              + "function pvVideoTo(){}function vtLivePlay(){}function edTick(){}\n"
              + "function spareStop(){}function camIdle(){}function vtPause(){}\n"
              + "function requestAnimationFrame(){return 1;}function cancelAnimationFrame(){}\n"
              + "function ico(n){return '<i>'+n+'</i>';}\n"
              + "function $(id){return {innerHTML:''};}\n"
              + "let ED={play:false,raw:true,cs:0,blocks:[{s0:0,s1:5}],dur:5,raf:0};\n"
              + "let PV={vids:[{muted:true,volume:1,style:{},play(){return Promise.resolve();},"
                "pause(){}}],audio:[],aidx:0};\n"
              + "function play(){return {catch(){}};}\n"
              + "const VV=PV.vids[0];VV.play=play;\n"
              + f"const ED_PLAY={json.dumps(_func_src(ed, 'edPlay'))};\n"
              + f"const ED_PAUSE={json.dumps(_func_src(ed, 'edPause'))};\n"
              + _func_src(prev, "vtPlaying") + "\n"
              + _func_src(prev, "vtAudioCam") + "\n"
              # eval, а не new Function: боевым функциям нужен скоуп стенда (ED, PV.vids[0]),
              # и он же отдаёт им заглушку audioWake со счётчиком.
              + "eval(ED_PLAY);edPlay();\n"
              + "assert.strictEqual(woke,1,'edPlay не разбудил граф: '+woke);\n"
              + "eval(ED_PAUSE);edPause();\n"
              + "assert.strictEqual(woke,1,'пауза будит граф');\n"
              + "console.log('OK: edPlay wakes the graph');\n"
              + f"assert({json.dumps(_func_src(view, 'ipvPlay'))}.includes('audioWake('),"
                "'ipvPlay не разбудил граф');\n"
              + f"assert({json.dumps(_func_src(cams, 'cpvPlay'))}.includes('audioWake('),"
                "'cpvPlay не разбудил граф');\n"
              + "console.log('OK: every play start wakes the graph');\n")
    res = _run_node(tmp_path, "audio_wake_players.js", script)
    _assert_node(res, "OK: edPlay wakes the graph", "OK: every play start wakes the graph")


@node
def test_play_wakes_the_context(tmp_path):
    """Пуск воспроизведения будит контекст из обработчика нажатия: браузер отпускает
    автозапуск только на живом жесте, и без пробуждения очередь звука молчит."""
    res = _audio_stand(tmp_path, "wake_on_play.js", _FAKE_AUDIO + r"""
audioWake();                     // нажатие «Play»: живой жест будит контекст
assert.strictEqual(resumed,1,'контекст не разбужен живым жестом: '+resumed);
console.log('OK: play wakes the context');
""")
    _assert_node(res, "OK: play wakes the context")


@node
def test_player_start_wakes_the_context(tmp_path):
    """Нажатие «Play» (edPlay — та самая дверь из браузера) будит контекст через audioWake:
    без этого звук шага 1 молчал бы при приостановленном AudioContext."""
    prev, ed = _read(PREVIEW_JS), _read(EDITOR_JS)
    script = ("const assert=require('assert');\n" + _AUDIO_STAND
              + "let AUDIO=null,MG=null;\n"
              + "let MEDIA_VOL=1;\n"
              + "function pvAudioLimit(){}function vtOf(){return {failed:false};}function vtPrep(){}\n"
              + "function edBlockAt(){return 0;}function spareIdle(){}\n"
              + "function pvVideoTo(){}function vtLivePlay(){}function edTick(){}\n"
              + "function spareStop(){}function camIdle(){}function vtPause(){}\n"
              + "function requestAnimationFrame(){return 1;}function cancelAnimationFrame(){}\n"
              + "function ico(n){return '<i>'+n+'</i>';}\n"
              + "function $(id){return {innerHTML:''};}\n"
              + "let CURSTYLE={voice_db:0};\n"
              + "let ED={play:false,raw:true,cs:0,blocks:[{s0:0,s1:5}],dur:5,raf:0};\n"
              + "let PV={vids:[{muted:true,volume:1,style:{},play(){return Promise.resolve();},"
                "pause(){}}],audio:[],aidx:0};\n"
              + "\n".join(_func_src(prev, n) for n in _AUDIO_FUNCS) + "\n"
              + _FAKE_AUDIO
              + f"const ED_PLAY={json.dumps(_func_src(ed, 'edPlay'))};\n"
              # eval, а не new Function: боевой edPlay нужен скоуп стенда (ED, PV.vids[0]),
              # и он же отдаёт ему настоящую audioWake из 60-preview.js.
              + "eval(ED_PLAY);edPlay();\n"
              + "assert.strictEqual(resumed,1,'пуск плеера не разбудил контекст: '+resumed);\n"
              + "console.log('OK: player start wakes the context');\n")
    _assert_node(_run_node(tmp_path, "player_start_wakes.js", script),
                 "OK: player start wakes the context")


# --------------------------------------------------------------------------- #
# 5. Строка Firefox: по факту элемента, а не по догадке
# --------------------------------------------------------------------------- #
# Мини-DOM ровно под `pvProgRow`/`pvProgDrop` и строку: контейнер на стойке, строки
# в нём, у строки `.pvpx_txt`. Тот же приём, что у стендов прогресса в
# tests/test_voice_preview_job.py, — только здесь он свой, чтобы стенд не потянул
# за собой весь боевой плеер.
_FX_DOM = r"""
let SEQ=0;
function El(tag){this.tagName=tag;this.children=[];this.parentNode=null;
  this.attrs={};this.className='';this.style={};this.id='';this._html='';this._text='';
  this._spanTxt=null;this.dataset={};this.__n=++SEQ;}
Object.defineProperty(El.prototype,'innerHTML',{
  // Атрибуты САМОГО элемента тут НЕ трогаются: строка заводится `setAttribute`, и
  // обнуление их означало бы «строка без имени» — ровно то, по чему её и ищут.
  get(){return this._html;},
  set(v){this._html=String(v);this.children=[];this._spanTxt=null;
    if(String(v).includes('pvpx_txt')){
      // Разметка здесь всегда одна: строка со `span.pvpx_txt` и её `data-`-атрибутами.
      // Атрибуты — на САМОМ span (по ним же ищет querySelector), а не на контейнере.
      const s=new El('span');s._cls='pvpx_txt';
      for(const mm of String(v).matchAll(/data-([a-z0-9_]+)="([^"]*)"/g))
        s.attrs['data-'+mm[1]]=mm[2];
      this.children.push(s);this._spanTxt=s;}}});
Object.defineProperty(El.prototype,'textContent',{
  get(){return this._spanTxt?this._spanTxt._text:this._text;},set(v){this._text=String(v);}});
El.prototype.setAttribute=function(k,v){this.attrs[k]=String(v);};
El.prototype.getAttribute=function(k){return this.attrs[k];};
El.prototype.removeAttribute=function(k){delete this.attrs[k];};
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.remove=function(){if(!this.parentNode)return;
  const i=this.parentNode.children.indexOf(this);if(i>=0)this.parentNode.children.splice(i,1);
  this.parentNode=null;};
function _match(el,sel){
  const a=sel.match(/^\[data-([a-z0-9_]+)(?:="([^"]*)")?\]$/);
  if(a){const key='data-'+a[1];
    return Object.prototype.hasOwnProperty.call(el.attrs,key)
      &&(a[2]==null||String(el.attrs[key])===a[2]);}
  const c=sel.match(/^\.([\w-]+)$/);
  if(c)return el._cls===c[1]||String(el.className).split(' ').indexOf(c[1])>=0;
  return false;}
El.prototype.querySelector=function(sel){
  for(const c of this.children){if(_match(c,sel))return c;const d=c.querySelector(sel);if(d)return d;}
  return null;};
El.prototype.querySelectorAll=function(sel){
  const out=[];for(const c of this.children){if(_match(c,sel))out.push(c);
    out.push(...c.querySelectorAll(sel));}return out;};
const STAGE=new El('div');
const MADE=[];
global.document={createElement:t=>{const e=new El(t);MADE.push(e);return e;},
  addEventListener(){},removeEventListener(){}};
function t(s){return String(s);}
function $(id){return STAGE;}
// Боевые camVisual/vtIsEd/vtMuteHost читают PV и ED — они и есть «кадр».
let PV=null,ED=null;
function vtLiveUpdate(){}
const PROTOS=['mo','z','HasAudio'];
function video(src,hasAudio){const v=new El('video');v.src=src;v.currentTime=0;
  v.paused=true;v.seeking=false;v.readyState=4;v.playbackRate=1;
  v.play=()=>Promise.resolve();v.pause=()=>{};v.muted=false;
  if(hasAudio!==undefined)v[PROTOS.join('')]=hasAudio;   // свойство есть ТОЛЬКО у Firefox
  return v;}
function player(vids){return {vids,audioCi:0,liveMute:false,curCi:0};}
const boxes=()=>STAGE.children.filter(c=>c.attrs&&c.attrs['data-pvprog']!==undefined);
const rows=()=>{const b=boxes()[0];return b?b.children.map(c=>c.attrs['data-pvrow']):[];};
const rowTxt=()=>{const b=boxes()[0];if(!b)return '';
  const r=b.querySelector('[data-pvrow="fxaudio"]');return r?String(r.textContent):'';};
"""


def _fx_stand(tmp_path: Path, name: str, body: str) -> subprocess.CompletedProcess[str]:
    prev = _read(PREVIEW_JS)
    funcs = ("fxHasFact", "fxAudioFact", "pvAudioLimit", "_fxTimerReset",
             "pvProgRow", "pvProgDrop", "vtIsEd", "vtMuteHost")
    script = ("const assert=require('assert');\n"
              + _fx_consts(prev) + "\n"
              + "\n".join(_func_src(prev, n) for n in funcs) + "\n"
              + _FX_DOM + "\n" + body)
    return _run_node(tmp_path, name, script)


_FX_TEXT = "Firefox не читает звук этих камер — звук появится с прокси"
# Общая обвязка стендов: живой setTimeout складывается в blobs, а не срабатывает сам —
# порог игры (1,5 с) проверяется вызовом того же отложенного захода вручную.
_FX_TIMER = (
    "let blobs=[];global.setTimeout=(fn,ms)=>{blobs.push([fn,ms]);return blobs.length;};\n"
    "function fire(){const cbs=blobs.splice(0).map(b=>b[0]);cbs.forEach(f=>f());}\n"
    "const EXPECTED_TEXT=" + json.dumps(_FX_TEXT, ensure_ascii=False) + ";\n")


@node
def test_firefox_row_appears_only_when_the_element_says_there_is_no_audio(tmp_path):
    """Firefox не читает звук исходника камер — плеер обязан СКАЗАТЬ причину, иначе
    молчание читается как поломка. Решает не кодек с сервера, а сам элемент:
    `<video>.mozHasAudio === false` после 1,5 с игры. Не наиграла порог — судить не о
    чем, строки нет."""
    res = _fx_stand(tmp_path, "fx_audio_row.js", _FX_TIMER + """
PV=player([video('cam1.mp4',false)]);
ED=PV;
pvAudioLimit(STAGE,PV);
assert.deepStrictEqual(rows(),[],'строка появилась до порога игры');
PV.vids[0].currentTime=2.0;            // камера наиграла порог
fire();                                // ...и отложенный заход сработал
assert.deepStrictEqual(rows(),['fxaudio'],
  'строка про звук Firefox не появилась: '+JSON.stringify(rows()));
assert.strictEqual(rowTxt(),EXPECTED_TEXT,'текст строки не тот: '+rowTxt());
pvAudioLimit(STAGE,PV);
assert.deepStrictEqual(rows(),['fxaudio'],'повторный кадр плодит строки');
console.log('OK: Firefox row appears by the element fact');
""")
    _assert_node(res, "OK: Firefox row appears by the element fact")


@node
def test_no_firefox_row_without_that_property(tmp_path):
    """В Chromium свойства `mozHasAudio` нет вовсе — и строки нет НИКОГДА: гадать по
    серверному кодеку тут не о чем, звук камеры играет сам. Тот же стенд ловит и
    мутацию: показ строки без проверки свойства красит ровно этот тест."""
    res = _fx_stand(tmp_path, "fx_audio_row_chromium.js", _FX_TIMER + """
PV=player([video('cam1.mp4')]);         // свойства нет — это Chromium
ED=PV;
PV.vids[0].currentTime=2.0;
pvAudioLimit(STAGE,PV);
fire();
assert.deepStrictEqual(rows(),[],
  'строка про Firefox появилась в Chromium: '+JSON.stringify(rows()));
console.log('OK: no Firefox row without the property');
""")
    _assert_node(res, "OK: no Firefox row without the property")


@node
def test_no_firefox_row_while_the_element_has_audio(tmp_path):
    """Firefox читает этот файл (`mozHasAudio === true`) — говорить нечего: строка
    появляется только на молчащем исходнике."""
    res = _fx_stand(tmp_path, "fx_audio_row_has_audio.js", _FX_TIMER + """
PV=player([video('cam1.mp4',true)]);
ED=PV;
PV.vids[0].currentTime=2.0;
pvAudioLimit(STAGE,PV);
fire();
assert.deepStrictEqual(rows(),[],'строка появилась там, где звук читается');
console.log('OK: no row while the element has audio');
""")
    _assert_node(res, "OK: no row while the element has audio")


@node
def test_firefox_row_goes_away_when_the_source_changes(tmp_path):
    """Превью переехало на прокси — у элемента ДРУГОЙ src, и о молчании исходника
    говорить уже нечего: ответ помнится на элементе ВМЕСТЕ с src, поэтому строка
    уходит сама, без отдельной уборки."""
    res = _fx_stand(tmp_path, "fx_audio_row_switch.js", _FX_TIMER + """
PV=player([video('cam1.mp4',false)]);
ED=PV;
PV.vids[0].currentTime=2.0;
pvAudioLimit(STAGE,PV);
fire();
assert.deepStrictEqual(rows(),['fxaudio'],'строка не появилась вовсе');
PV.vids[0].src='/api/media?path=pv_cam1.mp4';   // переезд на прокси: дорожка aac
pvAudioLimit(STAGE,PV);
assert.deepStrictEqual(rows(),[],'строка осталась после смены источника');
console.log('OK: Firefox row goes away with the source');
""")
    _assert_node(res, "OK: Firefox row goes away with the source")


@node
def test_firefox_row_is_shared_by_all_players(tmp_path):
    """Строку зовут плееры, у которых звучит <video> (страница рендера вставок), и общий
    опрос прокси: одна функция на все — иначе у одного плеера причина называлась бы, у
    другого нет. Шаг 1 и раскладка камер её НЕ зовут: их <video> немые, звук там — буфер
    из WAV (`/api/preview_audio`), и молчание исходника в Firefox к нему не относится.
    Живёт строка в ОБЩЕМ контейнере прогресса, рядом со строкой голоса."""
    prev, ed, view, cams = (_read(PREVIEW_JS), _read(EDITOR_JS),
                            _read(VIEW_JS), _read(CAMJS))
    for src, marker in ((view, "ipvStep"), (view, "ipvOpen"), (prev, "pvProxyRefresh")):
        assert "pvAudioLimit(" in _func_src(src, marker), f"{marker} не зовёт строку про Firefox"
    for src, marker in ((ed, "edTick"), (ed, "edPlay"), (cams, "cpvStep")):
        assert "pvAudioLimit(" not in _func_src(src, marker), (
            f"{marker} зовёт строку про звук Firefox, а <video> этого плеера немые")
    res = _fx_stand(tmp_path, "fx_audio_row_players.js", _FX_TIMER + """
PV=player([video('cam1.mp4',false)]);
ED=PV;
PV.vids[0].currentTime=2.0;
const r=pvProgRow(STAGE,'voice','pvpxv');
r.innerHTML='<span class="pvpx_txt">голос</span>';
pvAudioLimit(STAGE,PV);
fire();
assert.deepStrictEqual(rows(),['voice','fxaudio'],
  'строка не встала рядом со строкой голоса: '+JSON.stringify(rows()));
console.log('OK: Firefox row shares the progress container');
""")
    _assert_node(res, "OK: Firefox row shares the progress container")


# --------------------------------------------------------------------------- #
# 6. Словарь, сторож строк и соседние сторожа
# --------------------------------------------------------------------------- #
def test_new_strings_are_translated():
    """Новые строки интерфейса обязаны быть в словаре: иначе на английском они
    останутся русскими. Общий сторож — tests/test_i18n.py, здесь проверка, что
    строки именно эти (правка текста без словаря — тихая поломка)."""
    en = json.loads(_read(ROOT / "static" / "i18n" / "en.json"))
    for key in ("живое прослушивание через плагины VST3 и список устройств вывода",
                "Firefox не читает звук этих камер — звук появится с прокси",
                "нет пакета pedalboard — плагины VST3 недоступны, устройства вывода "
                "показаны звуковой системой: pip install pedalboard"):
        assert key in en, f"нет перевода: {key}"
        assert en[key].strip() and not re.search(r"[А-Яа-яЁё]", en[key]), en[key]


def test_dead_audio_proxy_strings_are_gone_from_the_dictionary():
    """Ключи убранного звукового прокси из словаря тоже уходят: лишний ключ молча
    стареет, а `test_i18n.test_backend_error_code_is_never_orphan` держит такое
    правило для кодов ошибок."""
    en = json.loads(_read(ROOT / "static" / "i18n" / "en.json"))
    dead = [k for k in en if re.search(r"звуковой прокси|звук — из звукового|"
                                       r"ещё собирается: браузер не читает", k)]
    assert not dead, f"в словаре висят строки убранного прокси: {dead}"


def test_no_task_codes_or_missing_guard_files():
    """Кодов заданий в новых текстах нет (общий сторож —
    tests/test_no_task_codes.py), и соседние сторожа смотрят на те же двери."""
    hits = set()
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        if re.search(r"voiceWiring|audioWake|previewproxy|voicefx_devices|doctor", _read(p)):
            hits.add(p.name)
    for want in ("test_doctor.py", "test_ui_static.py", "test_voice_live.py",
                 "test_hp_concurrency.py"):
        assert want in hits, f"{want} перестал смотреть за своими дверями"
