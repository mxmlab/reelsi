# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панель «Голос»: один выключатель, голос всего клипа в превью, плеер 9:16.

Стенды гоняют БОЕВЫЕ функции из `static/app/95-styles.js` и `static/app/60-preview.js`
под node на мини-DOM: в браузере эти места не увидеть без живого сервера, клипа и
плагинов, а ломаются они молча.

1. Разметка блока и чтение значений — ОДНА пара функций (`voiceFxRender` /
   `voiceFxRead`): панель превью (`mode: 'panel'`) и сводка в редакторе профиля
   (`mode: 'summary'`) строятся одним кодом, второй разметки блока нет ни в одном
   файле; сводка — без единой ручки, читать из неё нечего.
2. Выключатель ОДИН: галок «Для нарезки», «В итоговый трек» и «Обработанный голос»
   в разметке нет, значений `cut`/`final` панель не пишет, а «обработка включена» —
   это шумодав или включённый плагин цепочки (зеркало серверного `voice_fx_on`).
3. «Сохранить у спикера» пишет ТОЛЬКО `voice_fx`: остальные поля профиля (пороги,
   LUT, рамка, папки) уезжают на сервер теми же, что и пришли.
4. Голос клипа: превью открывается и само просит запечь ВЕСЬ клип
   (`/api/voicefx_bake`), ход виден СТРОКОЙ на кадре превью (`.pvpxv` рядом с
   прогрессом прокси, общей формы прогресса у голоса нет), а играет готовый трек
   из кеша обработки через `/api/media`.
5. ВИДЕО — ХОЗЯИН: с включённым обработанным голосом время видео растёт монотонно,
   звук подводится к нему, а сам `<video>` не получает ни pause, ни seek.

Запуск:  py -3.10 -m pytest tests/test_voice_panel_js.py -q
"""
from __future__ import annotations

import json
import math
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

STYLES_JS = ROOT / "static" / "app" / "95-styles.js"
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
STAGE_CSS = ROOT / "static" / "app.css"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Функции блока голоса: ровно то, что проверяем, — тела берутся из файла, копий в
# тесте нет (стенд сторожил бы сам себя).
VOICE_FUNCS = (
    "voiceFxHost", "vfxEl", "vstTitle", "voiceFxLive", "voiceFxOn", "voiceFxLiveOn", "voiceFxWindowOn",
    # одна панель на два места: спикер и «есть звук клипа» — от панели, не от открытого клипа
    "voiceFxIsClip", "voiceFxPanelHosts", "voiceFxSyncPanels", "noAudioWrap",
    "voiceFxSummary", "voiceFxRender", "voiceFxRead", "voiceFxStrength", "voiceFxOther",
    "voiceFxStatus", "dnAttenSync", "dnEngineSync", "vfxEngine", "vfxEngineSel",
    "vfxEngName", "vfxStrengthLabel", "vfxStrengthHint", "vfxAltDefault",
    "vstRow", "vstFxNote", "vstFxDn", "vstFxOn", "vstFxDel", "vstFxMove", "vstFxAdd",
    "vfxSepStep", "vfxSepText", "vfxSepSize", "vfxSepApply", "pvVoiceAuto", "pvVoiceSave",
    "vfxDnSummaryText", "vfxDnToggle", "vfxDnUpdateSummary",
    "syncDbSliders", "setStyleDb", "voiceFxLiveGain", "voiceFxLiveOutDb", "voiceFxHostOn",
    # громкость голоса: одна дверь чтения и одна записи (спикер клипа или стиль)
    "voiceDbSpeakerKey", "voiceDbBySpeaker", "voiceDbOf", "setVoiceDb", "voiceDbSaveSoon",
    "voiceDbSaveNow",
    # свежий профиль с диска — база всех записей профиля (95-styles.js)
    "speakerFresh",
)
PREVIEW_FUNCS = ("vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying", "vtEl", "vtGate",
                 "vtNote", "vtName", "vtProfileFx", "vtFx", "vtDetach", "vtStop", "vtPause",
                 "vtLiveOn", "vtSetMute", "vtLiveUpdate", "vtLiveRate", "vtLiveExpect", "vtLiveCmd",
                 "vtLiveSid", "vtLivePlay", "vtLivePause",
                 "vtIsPv", "vtIsEd", "vtEa", "vtMuteHost", "vtStage", "vtHostLive", "vtLivePrep",
                 "vtStatesWait", "vtStatesTake", "vtHostDown",
                 "vtPrep", "vtVoiceShow", "vtVoiceWatch", "vtVoicePoll",
                 "pvProgRow", "pvProgDrop", "vtVoiceLine", "vtVoiceStop", "vtVoiceTake",
                 "vtVoiceUse", "vtUse", "vtTick", "vtSeek", "vtRate",
                 "voiceGraphNeeded", "applyDbGains", "dbToGain")


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


# Хелперы «одна панель на два места» (VO2). На коде ДО них их нет: стенд тогда не
# должен падать на сборке, а обязан провалиться по ПОВЕДЕНИЮ (правка профиля уходит
# не тому спикеру) — поэтому отсутствующий хелпер пропускаем, а не требуем.
OPTIONAL_HELPERS = ("voiceFxIsClip", "voiceFxPanelHosts", "voiceFxSyncPanels", "noAudioWrap")


def _src(*names: str) -> str:
    """Тела боевых функций: голос — из 95-styles.js, плеер — из 60-preview.js."""
    styles = STYLES_JS.read_text(encoding="utf-8")
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    out = []
    for name in names:
        where = styles if name in VOICE_FUNCS else preview
        assert name in VOICE_FUNCS or name in PREVIEW_FUNCS, f"нет такого стенда: {name}"
        if name in OPTIONAL_HELPERS and "function %s(" % name not in where:
            continue
        out.append(_func_src(where, name))
    return "\n".join(out)


DOM = r"""
// ---- мини-DOM: только то, что нужно панели (innerHTML + поиск по атрибутам) ----
function _attrsOf(s){
  const out={};const rx=/([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;let m;
  while((m=rx.exec(s||'')))out[m[1]]=(m[2]===undefined?'':m[2]);
  return out;}
function _dataKey(k){return k.slice(5).replace(/-(\w)/g,(s,c)=>c.toUpperCase());}
class El{
  constructor(tag){this.tag=tag;this.attrs={};this.children=[];this.parentElement=null;
    this.dataset={};this.style={};this.value='';this.checked=false;this.options=[];
    this.className='';this.src='';this.volume=0;this.readyState=0;this._text='';this._html='';
    this.paused=true;this.played=0;this.currentTime=0;this.seeking=false;
    this.seekLog=[];this.pauseLog=0;}
  get nextSibling(){if(!this.parentElement)return null;
    const s=this.parentElement.children;return s[s.indexOf(this)+1]||null;}
  setAttribute(k,v){this.attrs[k]=String(v);
    if(k.indexOf('data-')===0)this.dataset[_dataKey(k)]=String(v);}
  getAttribute(k){return this.attrs[k];}
  appendChild(c){c.parentElement=this;this.children.push(c);return c;}
  insertBefore(c,ref){c.parentElement=this;const i=ref?this.children.indexOf(ref):-1;
    if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c;}
  remove(){if(!this.parentElement)return;const s=this.parentElement.children;
    const i=s.indexOf(this);if(i>=0)s.splice(i,1);this.parentElement=null;}
  get innerHTML(){return this._html;}
  set innerHTML(h){this._html=String(h);this.children=[];parseInto(this,String(h));}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);}
  closest(sel){const key=sel.replace(/^\[|\]$/g,'');let el=this;
    while(el){if(matchSel(el,key))return el;el=el.parentElement;}return null;}
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  querySelectorAll(sel){const key=sel.replace(/^\[|\]$/g,'');const out=[];
    const walk=e=>{for(const c of e.children){if(matchSel(c,key))out.push(c);walk(c);}};
    walk(this);return out;}
  addEventListener(){}play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  load(){}focus(){}blur(){}removeAttribute(k){delete this.attrs[k];if(k==='src')this.src='';}
}
const VOID_TAGS={'input':1,'br':1,'img':1,'hr':1,'meta':1,'link':1};
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
    // `checked` в разметке — это и есть состояние галки в браузере: без переноса в
    // свойство панель читалась бы как «всё выключено», и стенд врал бы вместе с ней
    el.checked=(a['checked']!==undefined);
    if(a['value']!==undefined)el.value=a['value'];
    stack[stack.length-1].appendChild(el);
    if(!VOID_TAGS[m[1].toLowerCase()]&&!/\/>$/.test(m[0]))stack.push(el);
  }
  return root;}
globalThis.document={createElement:t=>new El(t),addEventListener:()=>{},body:new El('body')};
"""

STATE = r"""
// Состояние модуля, которое в браузере живёт в общем скоупе страницы
const VFX_DB_DEFAULT=40;
const VFX_ENGINE_DEFAULT='roformer';
const VFX_MIX_DEFAULT=100;
let VFXSEP=null;
let VFXSEP_TIMER=0;
const VFXSEP_POLL=1500;
let VSTLIST=[];
let VOICEFXSPK='';
let SPEAKERS={};
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function errText(d){return (d&&(d.error||d.err))||'';}
function esc(s){return String(s);}
function ico(){return '';}
function tipArm(){}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function fmtT(s){return String(Math.round((+s||0)*10)/10)+'s';}
function $(id){return globalThis.__byId&&globalThis.__byId[id]||null;}
// Общая форма прогресса (55-progress.js) — как её зовут боевые функции превью.
const PROGCALLS=[];
function progOpen(o){PROGCALLS.push(['open',(o&&o.title)||'',((o&&o.items)||[]).map(i=>i.name)]);}
function progItem(name,ev,extra){PROGCALLS.push(['item',name,ev,(extra&&extra.pct)!=null?extra.pct:null]);}
function progDone(msg,bad){PROGCALLS.push(['done',msg||'',!!bad]);}
function progUpdate(frac,stage){PROGCALLS.push(['update',frac==null?null:frac,stage||'']);}
function progStep(stage){PROGCALLS.push(['step',stage||'']);}
function progMini(){PROGCALLS.push(['mini']);}
function hideProg(){PROGCALLS.push(['hide']);}
function clipByXml(){return {name:'01_clip.xml',label:'01_clip'};}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
function pvProxyLoad(){return Promise.resolve(null);}
function pvProxyMerge(){return null;}
function pvProxyWatch(){}
function fxDeviceFill(){return Promise.resolve();}
function vstFxList(){return Promise.resolve();}
function vstFxEdit(){return Promise.resolve();}
function voiceFxSepFill(){return Promise.resolve();}
function voiceFxStatus(host,text){const el=host?host.querySelector('[data-vfx="status"]'):null;
  if(el)el.textContent=text||'';}
let TUNED=0,BAKED=0;
function pvVoiceTune(){TUNED++;}
function pvVoiceBake(){BAKED++;}
// Открытое окно плагина: живой звук идёт через него, и превью это знает
// (VOICEFXLIVE ставит панель — 95-styles.js:vstFxLiveWatch).
let VOICEFXLIVE=null;
// Плеер: время монтажа и дорожка обработанного голоса (vt*, 60-preview.js)
let MEDIA_VOL=1;
let PV={audio:[],aidx:0,vids:[],playing:false,scrubbing:false,xml:'',voicePanel:'pvvoice'};
function voiceWiring(){}
// Сервер живого хоста: состояния плагинов он отдаёт на dump_states. Штатная ветка
// «окно закрыли» зовёт и его, и гашение (60-preview.js:vtHostDown).
function voiceFxHostPost(url,body){
  return globalThis.fetch(url,{method:'POST',body:JSON.stringify(body)}).then(r=>r.json());}
function voiceFxHostDump(P){return Promise.resolve(null);}
function voiceFxHostOn(){return false;}
"""


def _sync_consts() -> str:
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


def _src_any(name: str) -> str:
    """Тело функции из любого файла (95-styles.js или 60-preview.js) — для `extra`."""
    for where in (STYLES_JS, PREVIEW_JS):
        text = where.read_text(encoding="utf-8")
        if re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), text):
            return _func_src(text, name)
    raise AssertionError(f"нет функции {name}")


def _run_node(tmp_path: Path, body: str, name: str = "voice_stand.js", extra: tuple = ()) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки.

    `extra` — НАСТОЯЩИЕ функции поверх заглушек STATE (pvVoiceTune, vstFxEdit): обычные
    стенды их глушат, а здесь проверяем именно то, как правка доходит до сохранения.
    """
    path = tmp_path / name
    path.write_text(DOM + _sync_consts() + "\n" + STATE
                    + _src(*(VOICE_FUNCS + PREVIEW_FUNCS)) + "\n"
                    + "\n".join(_src_any(n) for n in extra) + "\n" + body,
                    encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


def _panel(fx: Any, mode: str = "panel", spk: str = "", audio: bool = True) -> str:
    """Отрисовка панели боевой функцией и проверка, что разметка вообще появилась.

    `spk` — спикер панели (ключ профиля, куда пишется voice_fx); `audio` — панель со
    звуком клипа (превью). По умолчанию — превью клипа этого спикера.
    """
    opts = {"mode": mode, "spk": spk, "audio": audio}
    return ("const host=new El('div');\nvoiceFxRender(host,%s,%s);\n"
            % (json.dumps(fx, ensure_ascii=False), json.dumps(opts, ensure_ascii=False)))


# --------------------------------------------------------------------------- #
# 1. Одна разметка на два места: панель превью и сводка профиля
# --------------------------------------------------------------------------- #
@node
def test_one_renderer_for_clip_and_profile_panel(tmp_path: Path) -> None:
    """Панель превью и панель профиля строятся ОДНОЙ функцией; профиль — без звука клипа.

    Владелец: в профиле спикера править можно так же, как в любом клипе. Поэтому панель
    профиля — та же разметка (режима-сводки больше нет), но слушать в ней нечего: «Куда
    играть» и «Настроить» неактивны, справка — на самих контролах. Значения читаются из
    той же разметки и в профиле, и в превью.
    """
    body = _panel({"denoise": {"on": True, "atten_db": 55},
                   "vst": [{"path": "C:/a.vst3", "name": "", "state": "AQID", "on": True},
                           {"path": "C:/b.vst3", "name": "Shell", "state": "", "on": False}]},
                  spk="Мясников", audio=True) + """
const html=host.innerHTML;
const read=voiceFxRead(host);
const prof=new El('div');
voiceFxRender(prof,{denoise:{on:true,atten_db:55},
  vst:[{path:'C:/a.vst3',on:true},{path:'C:/b.vst3',on:false}]},
  {mode:'panel',spk:'Спикер_Подкаст',audio:false});
const phtml=prof.innerHTML;
const readProf=voiceFxRead(prof);
const off=new El('div');
voiceFxRender(off,null,{mode:'off'});
console.log(JSON.stringify({
  mode:host.dataset.vfxmode, profMode:prof.dataset.vfxmode, offMode:off.dataset.vfxmode,
  spk:host.dataset.spk, profSpk:prof.dataset.spk,
  audio:host.dataset.audio, profAudio:prof.dataset.audio,
  has:{dn:html.indexOf('data-vfx="dn_on"')>=0,atten:html.indexOf('data-vfx="dn_atten_num"')>=0,
       vst:html.indexOf('data-vfx="vst"')>=0,pick:html.indexOf('data-vfx="pick"')>=0,
       status:html.indexOf('data-vfx="status"')>=0,
       cut:html.indexOf('data-vfx="cut"')>=0,final:html.indexOf('data-vfx="final"')>=0,
       listen:html.indexOf('data-vfx="listen"')>=0},
  rows:host.querySelectorAll('[data-vst]').length,
  read:read, readProf:readProf,
  profRows:prof.querySelectorAll('[data-vst]').length,
  profDisabled:prof.querySelectorAll('[disabled]').length,
  clipDisabled:host.querySelectorAll('[disabled]').length,
  profTips:prof.querySelectorAll('[data-t="Слушать — в превью клипа этого спикера"]').length,
  profHtmlHasTip:phtml.indexOf('Слушать — в превью клипа этого спикера')>=0,
  offInputs:off.querySelectorAll('[data-vfx]').length
}));
"""
    res = _run_node(tmp_path, body)

    assert res["mode"] == "panel" and res["profMode"] == "panel" and res["offMode"] == "off"
    # Спикер панели — свой: превью пишет в спикера клипа, профиль — в своего
    assert res["spk"] == "Мясников" and res["profSpk"] == "Спикер_Подкаст", res
    assert res["audio"] == "1" and res["profAudio"] == "0", "панель профиля получила звук клипа"
    # Панель: ОДИН выключатель, шумодав с силой, цепочка, состояние
    assert res["has"]["dn"] and res["has"]["atten"] and res["has"]["vst"] and res["has"]["pick"]
    assert res["has"]["status"], f"в панели нет части разметки: {res['has']}"
    # Галок назначения и галки «Обработанный голос» в разметке больше нет
    assert not res["has"]["cut"], "вернулась галка «Для нарезки»"
    assert not res["has"]["final"], "вернулась галка «В итоговый трек»"
    assert not res["has"]["listen"], "вернулась галка «Обработанный голос»"
    assert res["rows"] == 2, "строки цепочки плагинов не построены"
    # Значения читаются из ТОЙ ЖЕ разметки — порядок строк и есть порядок обработки.
    # cut/final панель не пишет вовсе: решение одно и выводится из обработки.
    expect = {"denoise": {"on": True, "engine": "roformer", "atten_db": 55, "mix": 100},
              "vst": [{"path": "C:/a.vst3", "name": "", "state": "AQID", "on": True},
                      {"path": "C:/b.vst3", "name": "Shell", "state": "", "on": False}]}
    assert res["read"] == expect, res["read"]
    # Панель профиля читается так же: её значения и уйдут в voice_fx спикера
    assert res["readProf"]["denoise"] == {"on": True, "engine": "roformer",
                                          "atten_db": 55, "mix": 100}, res["readProf"]
    assert [v["on"] for v in res["readProf"]["vst"]] == [True, False], res["readProf"]
    # Профиль: строки есть, а без звука неактивны «Куда играть» и обе «Настроить»;
    # справка про это — на самих контролах (три места), клип этим не затронут
    assert res["profRows"] == 2 and res["profDisabled"] == 3, res
    assert res["profTips"] == 3, res["profTips"]
    assert res["clipDisabled"] == 0, "в панели клипа что-то неактивно без причины"
    assert res["offInputs"] == 0, "режим off рисует ручки"


def test_list_end_ends_are_narrow_and_engine_button_is_compact() -> None:
    """Списки — по ширине содержимого (max ~320px), а не на всю панель.

    Растянутый на полэкрана выпадающий список читается хуже собственной строки, а
    кнопка скачивания RoFormer стоит рядом со списком движка и не растягивается
    (это действие, а не украшение панели). Поле числа — узкое.
    """
    js = STYLES_JS.read_text(encoding="utf-8")
    for key in ("dn_engine", "pick", "device"):
        m = re.search(r'data-vfx="%s"[^>]*style="([^"]*)"' % key, js)
        assert m, f"у списка {key} нет своей ширины"
        assert "width:auto" in m.group(1) and "max-width:320px" in m.group(1), (key, m.group(1))
    num = re.search(r'data-vfx="dn_atten_num"[^>]*style="([^"]*)"', js)
    assert num and "width:58px" in num.group(1), "поле числа растянулось"
    # Кнопка скачивания — в строке движка, а не отдельной строкой на всю панель
    row = js[js.index('data-vfx="dn_engine"'):js.index('data-vfx="dn_label"')]
    assert 'data-vfx="sep_install"' in row, "кнопка скачивания RoFormer не рядом со списком"


def test_voice_block_markup_is_declared_once() -> None:
    """Второй разметки блока и второго чтения нет ни в одном файле интерфейса.

    Разметка блока жила в `templates/index.html` (профиль спикера) и в JS (список
    плагинов) — две копии одного и того же расходились. Теперь она одна: функция
    `voiceFxRender`, из которой в html остались только места под неё.
    """
    from core import app_meta
    src = app_meta.app_js_text()
    for name in ("voiceFxRender", "voiceFxRead", "voiceFxOn", "voiceFxSummary"):
        assert src.count("function %s(" % name) == 1, f"{name} объявлена не один раз"
    # Галок назначения и второго решения «работать ли обработке» в интерфейсе нет
    for gone in ("voiceFxAutoFinal", "data-vfx=\"cut\"", "data-vfx=\"final\"",
                 "data-vfx=\"listen\"", "Галки относятся ко всей обработке"):
        assert gone not in src, f"в интерфейсе осталось: {gone}"
    # Оба места зовут одну функцию — каждое своим спикером; режима-сводки больше нет
    assert "voiceFxRender(vh,p.voice_fx,{mode:'panel',spk:SPKEDIT,audio:false})" in src, \
        "профиль спикера рисует блок не общей функцией"
    assert "voiceFxRender(host,prof.voice_fx,{mode:'panel',spk:key,audio:true})" in src, \
        "панель превью рисует блок не общей функцией"
    # (голый 'summary' — тег <summary> в других панелях; ищем именно режим разметки)
    assert "mode:'summary'" not in src and "mode==='summary'" not in src, "режим-сводка остался в коде"
    # И ни одного литерального id старого блока: ручек в профиле спикера больше нет
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    for el_id in ("spk_dn_on", "spk_vst_list", "spk_fx_final", "spk_fx_start",
                  "spk_fx_orig", "spk_fx_proc"):
        assert 'id="%s"' % el_id not in html, f"в разметке осталась ручка #{el_id}"


# --------------------------------------------------------------------------- #
# 2. Один выключатель: обработка включена ⇔ шумодав или плагин цепочки
# --------------------------------------------------------------------------- #
@node
def test_single_switch_is_the_only_decision(tmp_path: Path) -> None:
    """Ничего не включено — читать нечего; включили шумодав или плагин — обработка есть.

    Значения панели не несут `cut`/`final` вовсе: сервер выводит их из одного
    правила (`core/voicefx.voice_fx_on`), и вторая копия решения в профиле не
    хранится — иначе они разъедутся, и обработка молча выключится.
    """
    body = _panel({}) + """
const steps={};
steps.empty=voiceFxRead(host);                 // ничего не включено — писать нечего
const dn=vfxEl(host,'dn_on');
dn.checked=true;vstFxDn(dn);                   // включили шумодав
steps.byDenoise=voiceFxRead(host);
steps.liveAfterDenoise=voiceFxLive(steps.byDenoise);
dn.checked=false;vstFxDn(dn);                  // выключили — и обработки нет
steps.afterDenoiseOff=voiceFxRead(host);
VSTLIST=[{path:'C:/p.vst3',name:'',title:'Plugin'}];
const pick=vfxEl(host,'pick');pick.value='0';
vstFxAdd(vfxEl(host,'pick'));                  // включили плагин — обработка есть
steps.byPlugin=voiceFxRead(host);
steps.onByPlugin=voiceFxOn(host);
const row=vfxEl(host,'vst').querySelectorAll('[data-vst]')[0];
const on=row.querySelector('[data-vfx="vst_on"]');on.checked=false;vstFxOn(on);
steps.pluginOff=voiceFxRead(host);
steps.pluginOffLive=voiceFxLive(steps.pluginOff);
console.log(JSON.stringify({steps:steps,tuned:TUNED,
  hasCut:'cut' in (steps.byPlugin||{}),hasFinal:'final' in (steps.byPlugin||{})}));
"""
    res = _run_node(tmp_path, body)
    steps = res["steps"]

    assert steps["empty"] is None, "панель пишет профиль, когда ничего не включено"
    assert steps["byDenoise"]["denoise"]["on"] is True, steps["byDenoise"]
    assert steps["liveAfterDenoise"] is True, "включённый шумодав не считается обработкой"
    assert steps["afterDenoiseOff"] is None, "выключенный шумодав оставил обработку"
    assert steps["byPlugin"] and steps["byPlugin"]["vst"][0]["on"] is True, steps["byPlugin"]
    assert steps["onByPlugin"] is True, "включённый плагин не считается обработкой"
    assert steps["pluginOffLive"] is False, "снятая галка плагина оставила обработку"
    assert res["hasCut"] is False and res["hasFinal"] is False, \
        "панель снова пишет cut/final отдельными решениями"
    assert res["tuned"] > 0, "правка выключателя не пересчитывает голос клипа"


# --------------------------------------------------------------------------- #
# 3. Сохранение: пишется ТОЛЬКО voice_fx
# --------------------------------------------------------------------------- #
@node
def test_save_writes_only_voice_fx(tmp_path: Path) -> None:
    """«Сохранить у спикера»: остальные поля профиля уезжают теми же, что пришли."""
    profile = {"label": "Мясников", "cut": {"onset_db": 24, "onset_fall": 25.0},
               "lut": {"1": "C:/lut/cam1.cube"}, "camdirs": ["C:/cam"],
               "breath_model": "model.json", "ref": "ref.json",
               "voice_fx": {"denoise": {"on": True, "atten_db": 20}, "vst": []}}
    body = _panel({"denoise": {"on": True, "atten_db": 40}, "vst": []}, spk="Мясников") + """
const ORIG=%s;
SPEAKERS={'Мясников':JSON.parse(JSON.stringify(ORIG))};
VOICEFXSPK='Мясников';
const sent=[];
globalThis.fetch=async(url,opt)=>{
  if(String(url)==='/api/speakers')return {json:async()=>({ok:true,speakers:SPEAKERS})};
  if(String(url)==='/api/savespeaker'){sent.push(JSON.parse(opt.body));
    return {json:async()=>({ok:true,key:'Мясников'})};}
  if(String(url)==='/api/voicefx_devices')return {json:async()=>({devices:[]})};
  return {json:async()=>({ok:true})};};
(async()=>{
  await pvVoiceSave(vfxEl(host,'dn_on'));
  const row=sent[0]||{data:{}};
  console.log(JSON.stringify({sent:sent.length,name:row.name,
    voice_fx:row.data.voice_fx,
    others:Object.keys(row.data).filter(k=>k!=='voice_fx').sort(),
    same:JSON.stringify(Object.assign({},row.data,{voice_fx:null}))
         ===JSON.stringify(Object.assign({},ORIG,{voice_fx:null})),
    status:(vfxEl(host,'status')||{}).textContent,baked:BAKED,logs:LOGS.length}));
})();
""" % json.dumps(profile, ensure_ascii=False)
    res = _run_node(tmp_path, body)

    assert res["sent"] == 1, "профиль не сохранялся"
    assert res["name"] == "Мясников", res
    assert res["voice_fx"] == {"denoise": {"on": True, "engine": "roformer",
                                           "atten_db": 40, "mix": 100}, "vst": []}, res["voice_fx"]
    assert res["same"] is True, "чужие поля профиля уехали не теми, что пришли"
    assert res["others"] == ["breath_model", "camdirs", "cut", "label", "lut", "ref"], res["others"]
    assert "сохранено" in res["status"], res["status"]
    assert res["baked"] == 1, "после сохранения голос не поставлен на запекание"


# --------------------------------------------------------------------------- #
# 4. Голос клипа: время монтажа -> исходное время камеры 1, играет весь клип
# --------------------------------------------------------------------------- #
@node
def test_timeline_time_maps_to_source_and_whole_clip_plays(tmp_path: Path) -> None:
    """На стыке берётся НОВЫЙ кусок, а играется ОДИН файл голоса ВСЕГО клипа.

    Окна по 20 секунд больше нет: она считала шумодав заново на каждом краю окна, и
    видео вставало. Готовый трек клипа играется через /api/media, а позиция берётся по
    исходному времени камеры 1 (vtSrcAt) — файл посчитан по её звуку от нуля.
    """
    body = """
PV.audio=[{ts:0,te:10,src:100},{ts:10,te:20,src:300}];   // стык: 10с монтажа = 300с исходника
PV.aidx=0;
const onFirst=[vtSrcAt(PV,0),vtSrcAt(PV,5),vtSrcAt(PV,9.5)];
PV.aidx=1;
const onSecond=[vtSrcAt(PV,10),vtSrcAt(PV,10.5),vtSrcAt(PV,19)];
// Окно: тот же расчёт, что в кадре плеера, и запрос уходит на запекание всего клипа
const host=new El('div');
voiceFxRender(host,{denoise:{on:true,engine:'roformer',mix:100},vst:[]},{mode:'panel'});
document.body.appendChild(host);
globalThis.__byId={pvvoice:host};
PV.cams=[{path:'C:/cam1.mp4'}];
PV.xml='C:/out/01_clip.xml';
const asked=[];
globalThis.fetch=async(url,opt)=>{
  if(String(url)==='/api/voicefx_bake'){asked.push(JSON.parse(opt.body));
    return {json:async()=>({ok:true,ready:true,running:false,path:'C:/out/01_clip.voice.wav'})};}
  return {json:async()=>({ok:true})};};
(async()=>{
  await vtPrep(PV);
  const body=asked[0]||{};
  const tr=vtOf(PV);
  console.log(JSON.stringify({onFirst:onFirst,onSecond:onSecond,asked:asked.length,
    xml:body.xml,src:body.src,fx:body.fx,mode:host.dataset.vfxmode,
    on:tr.on,path:tr.path,
    el:(tr.el?{src:tr.el.src,volume:tr.el.volume}:null)}));
})();
"""
    res = _run_node(tmp_path, body)

    assert res["onFirst"] == [100, 105, 109.5], res["onFirst"]
    assert res["onSecond"] == [300, 300.5, 309], res["onSecond"]
    assert res["asked"] == 1, "голос клипа не запрошен"
    assert res["xml"] == "C:/out/01_clip.xml", "запекание просят не для XML клипа"
    assert res["src"] == "C:/cam1.mp4", "голос считается не по файлу камеры 1"
    assert res["fx"]["denoise"] == {"on": True, "engine": "roformer",
                                    "atten_db": 40, "mix": 100}, "настройки панели не уехали"
    assert res["on"] is True and res["path"] == "C:/out/01_clip.voice.wav", res
    assert res["el"] and res["el"]["src"] == "/api/media?path=C%3A%2Fout%2F01_clip.voice.wav", \
        "готовый трек играется не через /api/media"
    assert res["el"]["volume"] == 1, "громкость превью не применилась к треку"


@node
def test_settings_changed_while_baking_recompute(tmp_path: Path) -> None:
    """Ручки поменяли, пока трек считался — играем НЕ посчитанный под прежние.

    Смена движка, доли или цепочки обязана пересчитать голос: иначе на экране одни
    настройки, а в ушах другие — ровно то, из-за чего «разницы не слышно».
    """
    body = """
const host=new El('div');
voiceFxRender(host,{denoise:{on:true,engine:'roformer',mix:100},vst:[]},{mode:'panel'});
document.body.appendChild(host);
globalThis.__byId={pvvoice:host};
PV.cams=[{path:'C:/cam1.mp4'}];
PV.xml='C:/out/01_clip.xml';
let bakes=0,polls=0;
globalThis.fetch=async(url,opt)=>{
  if(String(url)==='/api/voicefx_bake'){bakes++;
    return {json:async()=>({ok:true,ready:false,running:true,xml:'C:/out/01_clip.xml'})};}
  // Ход спрашивают про СВОЙ клип: `?xml=...` в адресе (одно задание на клип).
  if(String(url).indexOf('/api/voicefx_bake_status')===0){polls++;
    if(polls===1)vfxEl(host,'dn_atten_num').value='70';   // ручку крутнули во время счёта
    // `done` сервер отдаёт вместе с путём: по нему фронт и понимает, что трек готов
    return {json:async()=>({ok:true,done:true,running:false,pct:100,i:5,n:5,
      xml:'C:/out/01_clip.xml',path:'C:/out/01_clip.voice.wav'})};}
  return {json:async()=>({ok:true})};};
const realTimeout=globalThis.setTimeout;
globalThis.setTimeout=(fn)=>{fn();return 0;};
function clearTimeout(){}
(async()=>{
  await vtPrep(PV);
  for(let i=0;i<40;i++)await Promise.resolve();
  globalThis.setTimeout=realTimeout;
  const tr=vtOf(PV);
  console.log(JSON.stringify({bakes:bakes,polls:polls,
    want:tr.want.indexOf('"mix":70')>=0,on:tr.on,
    el:(tr.el?tr.el.src:'')}));
})();
"""
    res = _run_node(tmp_path, body)

    assert res["bakes"] >= 2, "смена настроек во время счёта не пересчитала голос: %s" % res
    assert res["want"] is True, "пересчёт пошёл под прежние настройки: %s" % res
    assert res["on"] is True and res["el"].startswith("/api/media?path="), res


@node
def test_bake_progress_is_a_line_on_the_frame(tmp_path: Path) -> None:
    """Пока голос считается — играет звук камеры, а ход растёт СТРОКОЙ на кадре превью.

    Проценты берутся у самого шумодава (сервер отдаёт их в /api/voicefx_bake_status
    про ЭТОТ клип), а не у сборки прокси. Общей формы прогресса у голоса больше нет:
    оверлей поверх превью закрывал бы ровно то, что настраивают.
    """
    body = """
const host=new El('div');
voiceFxRender(host,{denoise:{on:true,engine:'roformer',mix:100},vst:[]},{mode:'panel'});
document.body.appendChild(host);
const STAGE=new El('div');STAGE.id='pvstage';
globalThis.__byId={pvstage:STAGE,pvvoice:host};
PV.cams=[{path:'C:/cam1.mp4'}];
PV.xml='C:/out/01_clip.xml';
let polls=0,mid='',midIn=false;
globalThis.fetch=async(url,opt)=>{
  if(String(url)==='/api/voicefx_bake')
    return {json:async()=>({ok:true,ready:false,queued:false,running:true,pct:0,i:0,n:0})};
  if(String(url).indexOf('/api/voicefx_bake_status')===0){polls++;
    if(polls===1&&PV.vt&&PV.vt.vtbox){const b=PV.vt.vtbox;mid=String(b.innerHTML);
      midIn=!!(b.parentElement&&b.parentElement.parentElement===STAGE);}
    return {json:async()=>(polls<3
      ?{ok:true,xml:PV.xml,queued:false,running:true,pct:polls*40,i:polls,n:5}
      :{ok:true,xml:PV.xml,queued:false,running:false,done:true,pct:100,i:5,n:5,
        path:'C:/_voicefx/deadbeef.wav'})};}
  return {json:async()=>({ok:true})};};
// Опрос идёт по setTimeout — в стенде он мгновенный и синхронный
const realTimeout=globalThis.setTimeout;
globalThis.setTimeout=(fn)=>{fn();return 0;};
function clearTimeout(){}
(async()=>{
  await vtPrep(PV);
  // Опрос идёт цепочкой промисов (fetch -> json -> следующий опрос): даём ей доиграть
  for(let i=0;i<60;i++)await Promise.resolve();
  const note=(host.querySelector('[data-vfx="status"]')||{}).textContent;
  globalThis.setTimeout=realTimeout;
  const tr=vtOf(PV);
  console.log(JSON.stringify({opened:PROGCALLS.filter(c=>c[0]==='open').length,
    polls:polls,mid:mid,after:(tr.vtbox?String(tr.vtbox.innerHTML):''),
    id:(tr.vtbox?tr.vtbox.id:''),inStage:midIn,
    on:tr.on,el:(tr.el?tr.el.src:''),note:note}));
})();
"""
    res = _run_node(tmp_path, body)

    assert res["opened"] == 0, "ход голоса снова открывает общую форму прогресса"
    assert res["polls"] >= 3, "ход запекания не опрашивался"
    assert res["id"] == "pvvoiceline" and res["inStage"] is True, res
    assert "голос обрабатывается" in res["mid"], \
        "хода голоса не видно строкой на кадре: %s" % res["mid"]
    assert "data-vtstop" in res["mid"], "у счёта нет кнопки «Стоп»: %s" % res["mid"]
    assert res["on"] is True and res["el"].startswith("/api/media?path="), res
    # После счёта трек подключён — надпись «готов», а строку хода снял законный конец
    # (живой прогон 27.09: висело «голос обрабатывается…», хотя голос уже играл).
    assert res["note"] == "обработанный голос клипа готов", res["note"]
    assert res["after"] == "", "строка хода осталась висеть после конца счёта"


# --------------------------------------------------------------------------- #
# 5. Движок шумодава: выбор, подписи ползунка и сила каждого движка
# --------------------------------------------------------------------------- #
@node
def test_engine_choice_and_slider_labels(tmp_path: Path) -> None:
    """Поля движка нет — RoFormer (мягкий): движок по умолчанию.

    Профили владельца записаны до RoFormer, движка в них нет; движок по умолчанию —
    RoFormer (мягкий), DeepFilterNet остаётся в списке. Подписи ползунка при этом
    разные: дБ у deep-filter против % у RoFormer.
    """
    body = """
const old=new El('div');            // профиль без поля движка
voiceFxRender(old,{denoise:{on:true,atten_db:55},vst:[]},{mode:'panel'});
const fresh=new El('div');          // новая настройка: шумодав ещё не включали
voiceFxRender(fresh,{}, {mode:'panel'});
const ro=new El('div');             // движок выбран явно
voiceFxRender(ro,{denoise:{on:true,engine:'roformer_aggr',atten_db:55,mix:70},vst:[]},
  {mode:'panel'});
const deep=new El('div');           // прежний движок по-прежнему выбирается
voiceFxRender(deep,{denoise:{on:true,engine:'deepfilter',atten_db:55},vst:[]},{mode:'panel'});
const sum=new El('div');
voiceFxRender(sum,{denoise:{on:true,engine:'roformer',mix:60},vst:[]},
  {mode:'panel',spk:'Спикер_Подкаст',audio:false});
console.log(JSON.stringify({
  oldEngine:vfxEl(old,'dn_engine').dataset.engine, oldLabel:vfxEl(old,'dn_label').textContent,
  oldSlider:String(vfxEl(old,'dn_atten_num').value), oldAlt:String(vfxEl(old,'dn_alt').value),
  oldRead:voiceFxRead(old),
  freshEngine:vfxEl(fresh,'dn_engine').dataset.engine,
  freshLabel:vfxEl(fresh,'dn_label').textContent,
  freshSlider:String(vfxEl(fresh,'dn_atten_num').value),
  freshAlt:String(vfxEl(fresh,'dn_alt').value),
  deepEngine:vfxEl(deep,'dn_engine').dataset.engine,
  deepLabel:vfxEl(deep,'dn_label').textContent,
  deepSlider:String(vfxEl(deep,'dn_atten_num').value),
  roEngine:vfxEl(ro,'dn_engine').dataset.engine, roSlider:String(vfxEl(ro,'dn_atten_num').value),
  roAlt:String(vfxEl(ro,'dn_alt').value), roRead:voiceFxRead(ro),
  sumText:sum.innerHTML
}));
"""
    res = _run_node(tmp_path, body)

    assert res["oldEngine"] == "roformer", "профиль без поля движка не поехал на RoFormer"
    assert res["oldLabel"] == "Доля обработанного, %", res["oldLabel"]
    # Ползунок показывает силу ТЕКУЩЕГО движка (% у RoFormer), а прежняя сила
    # deep-filter лежит в скрытом поле: переключение туда-обратно её не теряет.
    assert res["oldSlider"] == "100" and res["oldAlt"] == "55", res
    assert res["oldRead"]["denoise"] == {"on": True, "engine": "roformer",
                                         "atten_db": 55, "mix": 100}, res["oldRead"]
    assert res["freshEngine"] == "roformer", "новая настройка начинается не с RoFormer"
    assert res["freshLabel"] == "Доля обработанного, %", res["freshLabel"]
    assert res["freshSlider"] == "100" and res["freshAlt"] == "40", res
    assert res["deepEngine"] == "deepfilter", "DeepFilterNet пропал из движков"
    assert res["deepLabel"] == "Подавление, дБ" and res["deepSlider"] == "55", res
    assert res["roEngine"] == "roformer_aggr" and res["roSlider"] == "70" and res["roAlt"] == "55"
    assert res["roRead"]["denoise"] == {"on": True, "engine": "roformer_aggr",
                                        "atten_db": 55, "mix": 70}, res["roRead"]
    # Панель профиля показывает ту же сводку шумодава, что и превью (одна разметка)
    assert "RoFormer (мягкий) · доля 60 %" in res["sumText"], res["sumText"]


@node
def test_engine_switch_keeps_both_strengths(tmp_path: Path) -> None:
    """Переключение движка туда-обратно не теряет силу ни одного из них.

    Ползунок один, а значений два (дБ и %): сила прошлого движка уезжает в скрытое
    поле, из него же берётся сила нового. Без этого выбранные 40 дБ молча
    превратились бы в 100 % (или наоборот) при первом же переключении.
    """
    body = _panel({"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
                   "vst": []}) + """
const sel=vfxEl(host,'dn_engine');
const steps={};
const val=k=>String(vfxEl(host,k).value);      // в браузере value всегда строка
steps.startLabel=vfxEl(host,'dn_label').textContent;
sel.value='roformer';dnEngineSync(sel);
steps.toRoformer={slider:val('dn_atten_num'),alt:val('dn_alt'),
  label:vfxEl(host,'dn_label').textContent,engine:sel.dataset.engine};
vfxEl(host,'dn_atten_num').value='70';dnAttenSync(vfxEl(host,'dn_atten_num'));
sel.value='deepfilter';dnEngineSync(sel);
steps.backToDeep={slider:val('dn_atten_num'),alt:val('dn_alt'),
  label:vfxEl(host,'dn_label').textContent,engine:sel.dataset.engine};
const readBack=voiceFxRead(host);
sel.value='roformer';dnEngineSync(sel);
const readRo=voiceFxRead(host);
console.log(JSON.stringify({steps:steps,readBack:readBack,readRo:readRo,tuned:TUNED}));
"""
    res = _run_node(tmp_path, body)
    steps = res["steps"]

    assert steps["startLabel"] == "Подавление, дБ", steps
    assert steps["toRoformer"]["slider"] == "100", "у RoFormer не своя сила"
    assert steps["toRoformer"]["alt"] == "40", "сила deep-filter не сохранилась в запасе"
    assert steps["toRoformer"]["label"] == "Доля обработанного, %", steps
    assert steps["toRoformer"]["engine"] == "roformer", "движок в разметке не переключился"
    assert steps["backToDeep"]["slider"] == "40", "сила deep-filter потерялась"
    assert steps["backToDeep"]["alt"] == "70", "доля RoFormer потерялась"
    assert res["readBack"]["denoise"] == {"on": True, "engine": "deepfilter",
                                          "atten_db": 40, "mix": 70}, res["readBack"]
    assert res["readRo"]["denoise"] == {"on": True, "engine": "roformer",
                                        "atten_db": 40, "mix": 70}, res["readRo"]
    assert res["tuned"] > 0, "смена движка не пересчитывает окно прослушивания"


# --------------------------------------------------------------------------- #
# 6. Кнопка скачивания RoFormer: только когда движка нет
# --------------------------------------------------------------------------- #
@node
def test_install_button_appears_only_without_environment(tmp_path: Path) -> None:
    """Кнопка скачивания — только у RoFormer и только пока окружения нет.

    Стоит она РЯДОМ со списком движка (не отдельной строкой на всю панель), а размер
    загрузки приходит с сервера: в разметке числа нет — моделей две, и врать в кнопке
    нельзя, по ней человек решает, качать ли два гигабайта.
    """
    missing = ("{size:'~2.1 ГБ',size_bytes:2203685202,engines:{roformer:{installed:false},"
               "roformer_aggr:{installed:false}},job:{running:false,step:'',log:[]}}")
    ready = ("{size:'~2.1 ГБ',size_bytes:2203685202,engines:{roformer:{installed:true},"
             "roformer_aggr:{installed:true}},job:{running:false,step:'',log:[]}}")
    running = ("{size:'~2.1 ГБ',size_bytes:2203685202,engines:{roformer:{installed:false},"
               "roformer_aggr:{installed:false}},job:{running:true,step:'pip',i:2,n:4,"
               "error:'',log:['Collecting audio-separator']}}")
    roformer = {"denoise": {"on": True, "engine": "roformer", "mix": 100}, "vst": []}
    deep = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}, "vst": []}

    def body(fx: Any, sep: str) -> str:
        return (_panel(fx)
                + "\nVFXSEP=" + sep + ";\nvfxSepApply(host);\n"
                + "console.log(JSON.stringify({display:vfxEl(host,'sep_install').style.display,"
                + " btn:vfxEl(host,'sep_install').textContent,"
                + " disabled:vfxEl(host,'sep_install').disabled,"
                + " state:vfxEl(host,'sep_state').textContent}));\n")

    res = _run_node(tmp_path, body(roformer, missing), name="sep_missing.js")
    assert res["display"] == "", "кнопки скачивания нет, а окружения тоже нет"
    assert "Скачать RoFormer" in res["btn"] and "~2.1 ГБ" in res["btn"], res["btn"]
    assert res["disabled"] is False

    res = _run_node(tmp_path, body(roformer, ready), name="sep_ready.js")
    assert res["display"] == "none", "кнопка скачивания висит на готовом окружении"

    res = _run_node(tmp_path, body(roformer, running), name="sep_running.js")
    assert res["display"] == "", "во время установки кнопку прятать нечем"
    assert res["disabled"] is True, "по кнопке можно нажать второй установщик"
    assert "2/4" in res["state"] and "ставлю пакеты audio-separator" in res["state"], res["state"]
    assert "Collecting audio-separator" in res["state"], "хвост лога установки не виден"

    res = _run_node(tmp_path, body(deep, missing), name="sep_deep.js")
    assert res["display"] == "none", "кнопка RoFormer висит на deep-filter"


# --------------------------------------------------------------------------- #
# 7. Стенд плеера: видео — хозяин, обработанный звук ему подчинён
# --------------------------------------------------------------------------- #
@node
def test_video_time_is_monotonic_with_processed_voice(tmp_path: Path) -> None:
    """С включённым обработанным голосом время ВИДЕО растёт монотонно.

    Здесь и была жалоба владельца: «видео встаёт на паузу и прыгает, звук идёт
    дальше» — окно звука перезапрашивалось на каждом краю, сервер считал шумодав
    заново, и плеер захлёбывался. Стенд ведёт кадры плеера и проверяет две вещи:
    время видео не убывает и не стоит, а сам `<video>` не получает от звуковой ветки
    ни pause, ни seek — звук подводится к позиции видео, а не наоборот.
    """
    body = """
PV.audio=[{ts:0,te:5,src:100},{ts:5,te:10,src:200}];   // стык на 5-й секунде
PV.aidx=0;PV.playing=true;PV.scrubbing=false;
const v=new El('video');v.readyState=4;v.currentTime=100;v.paused=false;
PV.vids=[v];
// Голос клипа уже готов и играет: <audio> живёт своей жизнью
const host=new El('div');
voiceFxRender(host,{denoise:{on:true,engine:'roformer',mix:100},vst:[]},{mode:'panel'});
document.body.appendChild(host);
globalThis.__byId={pvvoice:host};
vtOf(PV).on=true;vtOf(PV).path='C:/out/01_clip.voice.wav';
const el=vtEl(PV);el.src='/api/media?path=C%3A%2Fout%2F01_clip.voice.wav';el.readyState=4;
// 24 кадра по 1/24 с: видео идёт вперёд, звук тянется за ним
const times=[];const seeks=[];
function nowTm(){const a=PV.audio[PV.aidx];return a.ts+(PV.vids[0].currentTime-a.src);}
for(let i=0;i<24;i++){
  vtTick(PV,nowTm());
  times.push(PV.vids[0].currentTime);
  seeks.push(el.currentTime);
  PV.vids[0].currentTime+=1/24;      // видео играет: так двигает его браузер
}
// ...и на стыке: кусок другой, источник прыгает, а видео продолжает играть
PV.aidx=1;PV.vids[0].currentTime=200;
const afterSplice=[];
for(let i=0;i<12;i++){
  vtTick(PV,nowTm());
  afterSplice.push(PV.vids[0].currentTime);
  PV.vids[0].currentTime+=1/24;
}
let mono=true;
for(let i=1;i<times.length;i++)if(times[i]<=times[i-1])mono=false;
for(let i=1;i<afterSplice.length;i++)if(afterSplice[i]<=afterSplice[i-1])mono=false;
console.log(JSON.stringify({mono:mono,times:times.length,
  videoPaused:PV.vids[0].paused,pauseLog:PV.vids[0].pauseLog,
  voicePaused:el.paused,voicePlays:el.played>0,
  voiceSeeks:seeks.filter((x,i)=>i&&x!==seeks[i-1]).length,
  lastSeek:seeks[0],lastVideo:afterSplice[afterSplice.length-1],
  muted:!!PV.voiceMute}));
"""
    res = _run_node(tmp_path, body)

    assert res["mono"] is True, "время видео шло не монотонно"
    assert res["videoPaused"] is False, "видео встало на паузу из-за обработанного звука"
    assert res["pauseLog"] == 0, "звуковая ветка поставила видео на паузу"
    assert res["voicePlays"] is True, "обработанный голос так и не заиграл"
    assert res["voicePaused"] is False, "звук стоит, хотя видео идёт"
    assert res["voiceSeeks"] >= 1, "звук не подводился к позиции видео: %s" % res
    assert res["lastSeek"] == 100, "первая подводка звука не на позицию видео: %s" % res
    assert res["muted"] is True, "звук камеры не заглушён — слышно два голоса"
    assert res["lastVideo"] > 200, "видео не продвинулось после стыка"

def test_note_says_ready_when_processed_voice_attached() -> None:
    """Готовый трек подключён (после счёта или мгновенно из кеша) — надпись панели «готов».

    Живой прогон 27.09: после включения шумодава голос из кеша подхватился за 1.5 с, а в
    панели висело «голос обрабатывается…» — надпись меняла только ветка опроса хода.
    """
    js = PREVIEW_JS.read_text(encoding="utf-8")
    i = js.index("function vtUse(")
    body = js[i:js.index("function ", i + 10)]
    assert "vtNote(P,t('обработанный голос клипа готов'))" in body, "надпись не обновляется при подключении трека"


@node
def test_voice_panel_volume_slider_and_denoise_first_row(tmp_path: Path) -> None:
    """Ползунок громкости шага 1 пишет CURSTYLE.voice_db и зовёт applyDbGains.

    Шумодав — первая строка цепочки, его чекбокс переключает denoise.on.
    """
    body = """
let liveGains = [];
// `var` — нарочно: стенд собран из функций РАЗНЫХ файлов, и глобальная привязка
// нужна настоящая (globalThis.VG не виден неквалифицированному `VG` внутри функции).
var voiceFxHostPost = async function(url, body) {
    liveGains.push({cmd: body.cmd, db: body.db}); return {ok: true};
};
var voiceFxHostOn = function() { return true; };
VOICEFXLIVE = { sid: 's1', running: true, track_ready: true };   // let — из STATE стенда
var CURSTYLE = { voice_db: 0, music_db: -20 };
var STYLES = { base: { voice_db: 0, music_db: -20 } };
var VG = { gain: { value: 1.0 } };
var MG = { gain: { value: 0.1 } };
MEDIA_VOL = 1;                  // ползунок прослушивания — на полную (let — из STATE)
var captureAE = function() {};
var updateStyleDiffDots = function() {};

const host = new El('div');
voiceFxRender(host, {
    denoise: { on: true, engine: 'roformer', mix: 100 },
    vst: [{ path: 'C:/eq.vst3', name: 'EQ', on: true, state: '' }]
}, { mode: 'panel' });
document.body.appendChild(host);
globalThis.__byId = {
  pvvoice: host
};

// Проверяем наличие ползунка громкости
const volSlider = host.querySelector('[data-vfx="spk_db"]');
const volTxt = host.querySelector('[data-vfx="spk_dbv"]');
const sldFound = !!(volSlider && volTxt);

// Меняем значение через setStyleDb
setStyleDb('voice', -6.0);
const dbAfter = CURSTYLE.voice_db;
const sldValAfter = volSlider ? volSlider.value : null;
const txtValAfter = volTxt ? volTxt.textContent : '';

// Проверяем, что Шумодав — первая строка цепочки
const sethdr = host.querySelector('[class="sethdr"]');
const dnChk = host.querySelector('[data-vfx="dn_on"]');
const dnSummary = host.querySelector('[data-vfx="dn_summary"]');

// Чекбокс шумодава связан с denoise.on
const initialOn = dnChk ? dnChk.checked : false;
if (dnChk) dnChk.checked = false;
const readAfterOff = voiceFxRead(host);

console.log(JSON.stringify({
    sldFound: sldFound,
    dbAfter: dbAfter,
    sldValAfter: sldValAfter,
    txtValAfter: txtValAfter,
    liveGains: liveGains,
    vgGain: VG.gain.value,
    hasSetHdr: !!sethdr,
    dnSummary: dnSummary ? dnSummary.textContent : '',
    initialOn: initialOn,
    readDnOn: readAfterOff ? readAfterOff.denoise.on : null
}));
"""
    res = _run_node(tmp_path, body)
    assert res["sldFound"] is True, "ползунок громкости (spk_db) не найден в панели"
    assert res["dbAfter"] == -6.0, "setStyleDb не выставил voice_db в CURSTYLE"
    assert res["sldValAfter"] == -6.0, "syncDbSliders не обновил значение ползунка"
    assert "dB" in res["txtValAfter"], "подпись громкости не содержит dB"
    assert [g for g in res["liveGains"] if g["cmd"] == "gain" and g["db"] == -6.0], \
        "правка громкости голоса не уехала хосту командой gain: %s" % (res["liveGains"],)
    assert abs(res["vgGain"] - 10.0 ** (-6.0 / 20.0)) < 0.01, "applyDbGains не выставил VG.gain"
    assert res["initialOn"] is True, "шумодав изначально не был отмечен"
    assert res["readDnOn"] is False, "снятие галки шумодава не отключило denoise.on"
    assert "RoFormer" in res["dnSummary"], f"сводка шумодава не содержит имя движка: {res['dnSummary']}"


@node
def test_live_host_volume_is_both_sliders(tmp_path: Path) -> None:
    """Громкость живого хоста = громкость голоса спикера + громкость прослушивания.

    Хост играет своим процессом (`core/voicefx_editor --live`), и страница своего звука
    не отдаёт: без этой суммы ползунок громкости прослушивания не влиял бы на голос из
    плагинов вовсе, а громкость голоса спикера — влияла бы только на свой `<video>`.
    Формула одна (`voiceFxLiveOutDb`), её зовут оба ползунка и подъём хоста.
    """
    body = """
let POSTS = [];
// `var` — нарочно: стенд собран из функций РАЗНЫХ файлов, и подмене нужна настоящая
// глобальная привязка (`globalThis.f` не виден неквалифицированному `f()` в функции).
var voiceFxHostPost = async function(url, body) { POSTS.push([url, body]); return {ok: true}; };
var voiceFxHostOn = function() { return true; };
VOICEFXLIVE = { sid: 's1', running: true, track_ready: true };   // let — из STATE стенда
var CURSTYLE = { voice_db: -6, music_db: -20 };
var STYLES = { base: { voice_db: 0, music_db: -20 } };
var VG = { gain: { value: 1.0 } };
var MG = { gain: { value: 0.1 } };
var setMediaVol = function(pct) { MEDIA_VOL = Math.max(0, Math.min(1, (+pct || 0) / 100)); };
var applyMediaVol = function() {};
var syncVolUI = function() {};
var stRefresh = function() {};
var updateStyleDiffDots = function() {};
var captureAE = function() {};

const last = () => POSTS[POSTS.length - 1][1];

setStyleDb('voice', -6);        // ползунок «Громкость» панели «Голос»
applyDbGains();                 // его правка доходит до хоста через VG/MG-граф
const voiceOn = last().db;
const voiceCmd = last().cmd;
setMediaVol(50);                // ползунок громкости прослушивания в строке плеера
voiceFxLiveGain();              // то же, что делает боевой setMediaVol (проверка — ниже)
const half = last().db;
setMediaVol(0);                 // полный ноль — тишина, а не «очень тихо»
voiceFxLiveGain();
const silent = last().db;
setMediaVol(25);
voiceFxLiveGain();
const quarter = last().db;

console.log(JSON.stringify({
    voiceCmd: voiceCmd, voiceOn: voiceOn, half: half, silent: silent, quarter: quarter,
    urls: POSTS.map(p => p[0]), sids: POSTS.map(p => p[1].sid)
}));
"""
    res = _run_node(tmp_path, body)

    # Громкость голоса спикера уехала хосту командой gain
    assert res["voiceCmd"] == "gain", res
    assert res["voiceOn"] == -6.0, res
    # Прослушивание вдвое — минус ≈6.02 дБ
    assert abs(res["half"] - (-6.0 - 20 * math.log10(2))) < 0.01, res
    assert abs((res["voiceOn"] - res["half"]) - 6.02) < 0.01, res
    # Ноль — полная тишина, а не тихий звук
    assert res["silent"] <= -100, res
    # Ползунок на 25 % — ещё −12 дБ
    assert abs(res["quarter"] - (-6.0 - 20 * math.log10(4))) < 0.01, res
    assert all(u == "/api/voicefx_live" for u in res["urls"]), res


def test_both_volume_sliders_ring_the_live_host() -> None:
    """Оба ползунка зовут один и тот же пересчёт громкости живого хоста.

    Стенд проверяет саму формулу, а связку — здесь: `applyDbGains` (правка ползунка
    «Громкость» панели) и `setMediaVol` (ползунок прослушивания в строке плеера) обязаны
    звать `voiceFxLiveGain`. Без второго вызова ползунок прослушивания менял бы только
    звук страницы, а голос из процесса хоста остался бы на прежнем уровне — ровно то,
    на что жаловался владелец.
    """
    preview = PREVIEW_JS.read_text(encoding="utf-8")

    gains = _func_src(preview, "applyDbGains")
    assert "voiceFxLiveGain()" in gains, gains

    vol = _func_src(preview, "setMediaVol")
    assert "voiceFxLiveGain()" in vol, vol
    assert "applyMediaVol()" in vol, vol

    styles = STYLES_JS.read_text(encoding="utf-8")
    out = _func_src(styles, "voiceFxLiveOutDb").replace(" ", "")
    # Итог считает ОДНА формула: и правка ручки, и подъём хоста зовут её, а не свою копию
    assert "20*Math.log10(vol)" in out, out
    start = _func_src(styles, "voiceFxHostStart").replace(" ", "")
    assert "gain_db:gain" in start and "voiceFxLiveOutDb()" in start, start


# --------------------------------------------------------------------------- #
# 7. Профиль спикера — та же панель, но СВОЙ спикер и без звука клипа (VO2)
# --------------------------------------------------------------------------- #
# Глобалы модуля, которые стенд не берёт из функций (`let`/`const`/`var` верхнего уровня
# файлов). Таймеры — сразу: тесту важен итог записи, а не порядок затишья.
VO2_GLOBALS = r"""
let VFXSAVET=0;
const VFX_SAVE_QUIET=700;
let VOICEDB_SAVE_T=null;const VOICEDB_PENDING={};const VOICEDB_SAVE_MS=400;
var VG=null,MG=null;
var CURSTYLE={voice_db:0,music_db:-20};
var ED={xml:'',vt:null};
function captureAE(){}
function updateStyleDiffDots(){}
function applyDbGains(){}
let PREPS=0;
function vtPrep(){PREPS++;}
const REQS=[];
globalThis.setTimeout=(fn)=>{fn();return 0;};
globalThis.clearTimeout=()=>{};
async function settle(){for(let i=0;i<80;i++)await Promise.resolve();}
const SAVES=()=>REQS.filter(r=>r.url==='/api/savespeaker');
const FORBIDDEN=()=>REQS.filter(r=>r.url.indexOf('/api/voicefx_host')===0||r.url.indexOf('/api/voicefx_bake')===0).length;
globalThis.fetch=async(url,opt)=>{
  const u=String(url);
  REQS.push({url:u,body:(opt&&opt.body)?JSON.parse(opt.body):null});
  if(u==='/api/speakers')return {json:async()=>({ok:true,speakers:JSON.parse(JSON.stringify(SPEAKERS))})};
  if(u==='/api/savespeaker')return {json:async()=>({ok:true,key:JSON.parse(opt.body).name})};
  if(u==='/api/voicefx_devices')return {json:async()=>({devices:[]})};
  return {json:async()=>({ok:true})};};
"""

_VO2_A = {"label": "СпикерА", "voice_db": -3,
          "voice_fx": {"denoise": {"on": True, "engine": "roformer", "atten_db": 40, "mix": 100},
                       "vst": []}}
_VO2_B = {"label": "СпикерБ", "voice_db": 5,
          "voice_fx": {"denoise": {"on": True, "engine": "roformer", "atten_db": 40, "mix": 100},
                       "vst": [{"path": "C:/eq.vst3", "name": "EQ", "on": True, "state": ""}]}}


@node
def test_profile_edit_writes_its_own_speaker_only(tmp_path: Path) -> None:
    """Открыт клип спикера Б, а профиль А правим: пишется ТОЛЬКО А, Б не трогаем.

    Шумодав, громкость и плагин — каждая правка уходит одним /api/savespeaker с ключом А.
    Звуковые кнопки в профиле неактивны, а хост и запекание голоса не запрашиваются ни разу:
    звука клипа в профиле нет.
    """
    body = VO2_GLOBALS + """
SPEAKERS={'СпикерА':JSON.parse(JSON.stringify(__A__)),'СпикерБ':JSON.parse(JSON.stringify(__B__))};
const B0=JSON.stringify(SPEAKERS['СпикерБ']);
VSTLIST=[{path:'C:/new.vst3',name:'New',title:'New'}];
// Клип спикера Б — панель превью (звук клипа); профиль А — в модалке (без звука)
VOICEFXSPK='СпикерБ';
ED={xml:'C:/out/B.xml',vt:null};
const clip=new El('div');
voiceFxRender(clip,SPEAKERS['СпикерБ'].voice_fx,{mode:'panel',spk:'СпикерБ',audio:true});
document.body.appendChild(clip);
const prof=new El('div');
voiceFxRender(prof,SPEAKERS['СпикерА'].voice_fx,{mode:'panel',spk:'СпикерА',audio:false});
document.body.appendChild(prof);
globalThis.__byId={pvvoice:clip,spkVoice:prof};
(async()=>{
  const out={steps:[]};
  const step=async(label,fn)=>{const n0=SAVES().length;await fn();await settle();
    const got=SAVES().slice(n0).map(r=>r.body.name);
    out.steps.push({label:label,saves:got.length,names:got});};
  await step('denoiseOff',async()=>{const dn=vfxEl(prof,'dn_on');dn.checked=false;vstFxDn(dn);});
  await step('denoiseOn',async()=>{const dn=vfxEl(prof,'dn_on');dn.checked=true;vstFxDn(dn);});
  await step('volume',async()=>{const sl=vfxEl(prof,'spk_db');sl.value='-9';
    setVoiceDb(sl.value,undefined,voiceFxHost(sl).dataset.spk);});
  await step('plugin',async()=>{vfxEl(prof,'pick').value='0';vstFxAdd(vfxEl(prof,'pick'));});
  // Страховка: «Настроить» в профиле не открывает окно плагина (звука клипа там нет)
  const n0=REQS.length;
  const btn=prof.querySelector('[onclick="vstFxEdit(this)"]');
  if(btn)await vstFxEdit(btn);
  await settle();
  const bA=SPEAKERS['СпикерА'];
  console.log(JSON.stringify({steps:out.steps,
    bUntouched:JSON.stringify(SPEAKERS['СпикерБ'])===B0,
    aVoiceDb:bA.voice_db, aDenoise:!!(bA.voice_fx&&bA.voice_fx.denoise&&bA.voice_fx.denoise.on),
    aPlugins:((bA.voice_fx&&bA.voice_fx.vst)||[]).length,
    clipDenoise:vfxEl(clip,'dn_on').checked, clipRows:clip.querySelectorAll('[data-vst]').length,
    forbidden:FORBIDDEN(), baked:BAKED, preps:PREPS,
    profDisabled:prof.querySelectorAll('[disabled]').length,
    clipDisabled:clip.querySelectorAll('[disabled]').length,
    guardReqs:REQS.length-n0, guardBtn:!!btn}));
})();
""".replace("__A__", json.dumps(_VO2_A, ensure_ascii=False)).replace("__B__", json.dumps(_VO2_B, ensure_ascii=False))
    res = _run_node(tmp_path, body, extra=("pvVoiceTune", "vstFxEdit"))
    assert res["guardBtn"] is True, "в профиле нет кнопки «Настроить» — нечего проверять"
    for st in res["steps"]:
        assert st["saves"] == 1 and st["names"] == ["СпикерА"], \
            f"правка «{st['label']}» в профиле А записала не один профиль А: {st}"
    assert res["bUntouched"] is True, "профиль спикера Б изменился от правок профиля А"
    assert res["aVoiceDb"] == -9, res
    assert res["aDenoise"] is True and res["aPlugins"] == 1, res
    assert res["clipRows"] == 1 and res["clipDenoise"] is True, \
        "панель открытого клипа Б подменилась правкой профиля А (у Б одна строка EQ)"
    assert res["forbidden"] == 0, "профиль запросил хост или запекание голоса клипа"
    assert res["baked"] == 0 and res["preps"] == 0, "голос клипа Б пересчитан из профиля А"
    # Неактивны: «Куда играть» и «Настроить» у строки плагина (шумодав-галка — не звук)
    assert res["profDisabled"] >= 2, "в профиле нет неактивных кнопок звука"
    assert res["clipDisabled"] == 0, "в панели клипа кнопки звука неактивны без причины"
    assert res["guardReqs"] == 0, "«Настроить» в профиле дёрнула сервер"


@node
def test_profile_without_voice_fx_gets_it_on_first_switch(tmp_path: Path) -> None:
    """У спикера без voice_fx (Спикер_Подкаст) панель рисуется, шумодав создаёт его.

    Раньше настроить такого спикера можно было только открыв клип. Теперь включение
    шумодава в профиле записывает voice_fx в сохраняемый профиль.
    """
    body = VO2_GLOBALS + """
SPEAKERS={'Спикер_Подкаст':{label:'Спикер Подкаст',voice_db:0}};
VOICEFXSPK='';
const prof=new El('div');
voiceFxRender(prof,undefined,{mode:'panel',spk:'Спикер_Подкаст',audio:false});
document.body.appendChild(prof);
globalThis.__byId={spkVoice:prof};
(async()=>{
  const drawn=prof.dataset.vfxmode==='panel'&&!!vfxEl(prof,'dn_on');
  const dn=vfxEl(prof,'dn_on');dn.checked=true;vstFxDn(dn);
  await settle();
  const saves=SAVES();
  console.log(JSON.stringify({drawn:drawn,saves:saves.length,
    name:saves[0]?saves[0].body.name:null,
    fx:saves[0]?saves[0].body.data.voice_fx:null,
    stored:SPEAKERS['Спикер_Подкаст'].voice_fx||null,
    label:saves[0]?saves[0].body.data.label:null,
    baked:BAKED}));
})();
"""
    res = _run_node(tmp_path, body, extra=("pvVoiceTune",))
    assert res["drawn"] is True, "панель профиля без voice_fx не нарисовалась"
    assert res["saves"] == 1 and res["name"] == "Спикер_Подкаст", res
    assert res["fx"] and res["fx"]["denoise"]["on"] is True, res["fx"]
    assert res["fx"]["denoise"]["engine"] == "roformer", res["fx"]
    assert res["stored"] and res["stored"]["denoise"]["on"] is True, "в памяти профиля voice_fx не появился"
    assert res["label"] == "Спикер Подкаст", "подпись профиля потерялась при записи voice_fx"
    assert res["baked"] == 0, "профиль без открытого клипа запустил запекание"


@node
def test_open_clip_and_profile_of_same_speaker_stay_in_sync(tmp_path: Path) -> None:
    """Клип спикера А и профиль А открыты: правка в профиле видна в панели клипа, и наоборот.

    Профиль пишет voice_fx и громкость; панель клипа перерисовывается из сохранённого, а
    голос клипа пересчитывается (pvVoiceBake). Правка клипа — обратно в профиль.
    """
    body = VO2_GLOBALS + """
SPEAKERS={'СпикерА':JSON.parse(JSON.stringify(__A__))};
VOICEFXSPK='СпикерА';
ED={xml:'C:/out/A.xml',vt:null};
const clip=new El('div');
voiceFxRender(clip,SPEAKERS['СпикерА'].voice_fx,{mode:'panel',spk:'СпикерА',audio:true});
document.body.appendChild(clip);
const prof=new El('div');
voiceFxRender(prof,SPEAKERS['СпикерА'].voice_fx,{mode:'panel',spk:'СпикерА',audio:false});
document.body.appendChild(prof);
globalThis.__byId={pvvoice:clip,spkVoice:prof};
(async()=>{
  const pdn=vfxEl(prof,'dn_on');pdn.checked=false;vstFxDn(pdn);
  await settle();
  const cl={dn:vfxEl(clip,'dn_on').checked};
  const psl=vfxEl(prof,'spk_db');psl.value='-9';setVoiceDb(psl.value,undefined,voiceFxHost(psl).dataset.spk);
  await settle();
  cl.db=vfxEl(clip,'spk_db').value;
  cl.dbv=vfxEl(clip,'spk_dbv').textContent;
  const bakedAfterProfile=BAKED;
  const cdn=vfxEl(clip,'dn_on');cdn.checked=true;vstFxDn(cdn);
  await settle();
  console.log(JSON.stringify({clipAfterProfile:cl,
    profDnAfterClip:vfxEl(prof,'dn_on').checked,
    stored:!!(SPEAKERS['СпикерА'].voice_fx&&SPEAKERS['СпикерА'].voice_fx.denoise.on),
    bakedAfterProfile:bakedAfterProfile, bakedAfterAll:BAKED,
    names:SAVES().map(r=>r.body.name)}));
})();
""".replace("__A__", json.dumps(_VO2_A, ensure_ascii=False))
    res = _run_node(tmp_path, body, extra=("pvVoiceTune",))
    ca = res["clipAfterProfile"]
    assert ca["dn"] is False, "панель клипа не перерисовалась по сохранённому профилю (шумодав)"
    # Ползунок в стенде хранит число, в браузере — строку: сравниваем по значению
    assert float(ca["db"]) == -9.0 and ca["dbv"] == "-9.0 dB", f"громкость в панели клипа не обновилась: {ca}"
    assert res["bakedAfterProfile"] >= 1, "голос клипа не пересчитан после правки профиля того же спикера"
    assert res["profDnAfterClip"] is True, "правка клипа не дошла до профиля того же спикера"
    assert res["stored"] is True, "правка клипа не записалась в профиль"
    assert all(n == "СпикерА" for n in res["names"]), res["names"]


# --------------------------------------------------------------------------- #
# 8. Подсказка неактивного контрола в профиле — на ОБЁРТКЕ (VO2, правка 2)
# --------------------------------------------------------------------------- #
# Chrome не шлёт события мыши disabled-контролам: tooltip, висящий на самом disabled
# «Куда играть» или «Настроить», не всплывает ни на hover, ни на фокус. Поэтому справка
# висит на фокусируемой обёртке (span.vfxlock, tabindex=0), а сам контрол не принимает
# указатель (app.css: .vfxlock>:disabled{pointer-events:none}).
@node
def test_locked_controls_in_profile_carry_the_tooltip_on_a_wrapper(tmp_path: Path) -> None:
    body = """
const fx={denoise:{on:true,atten_db:40},vst:[{path:'C:/a.vst3',name:'',state:'',on:true}]};
const prof=new El('div');
voiceFxRender(prof,fx,{mode:'panel',spk:'Спикер_Подкаст',audio:false});
const dev=vfxEl(prof,'device');
const btn=prof.querySelector('[onclick="vstFxEdit(this)"]');
const clip=new El('div');
voiceFxRender(clip,fx,{mode:'panel',spk:'Спикер_Подкаст',audio:true});
const cbtn=clip.querySelector('[onclick="vstFxEdit(this)"]');
const devW=dev?dev.parentElement:null, btnW=btn?btn.parentElement:null;
console.log(JSON.stringify({
  devFound:!!dev, btnFound:!!btn,
  devDisabled:!!dev&&dev.attrs.disabled!==undefined,
  devTag:devW?devW.tag:'', devClass:devW?(devW.attrs.class||''):'',
  devTip:devW?(devW.attrs['data-t']||''):'', devTab:devW?(devW.attrs.tabindex||''):'',
  devOwnTip:dev?(dev.attrs['data-t']||''):'',
  btnDisabled:!!btn&&btn.attrs.disabled!==undefined,
  btnTag:btnW?btnW.tag:'', btnClass:btnW?(btnW.attrs.class||''):'',
  btnTip:btnW?(btnW.attrs['data-t']||''):'', btnTab:btnW?(btnW.attrs.tabindex||''):'',
  btnOwnTip:btn?(btn.attrs['data-t']||''):'',
  clipBtnDisabled:!!cbtn&&cbtn.attrs.disabled!==undefined,
  clipDevWrapped:!!vfxEl(clip,'device')&&!!vfxEl(clip,'device').parentElement&&vfxEl(clip,'device').parentElement.attrs.class==='vfxlock'
}));
"""
    res = _run_node(tmp_path, body)
    tip = "Слушать — в превью клипа этого спикера"
    assert res["devFound"] and res["btnFound"], res
    # Неактивный контрол — disabled, а справка — на фокусируемой обёртке
    assert res["devDisabled"] is True and res["devOwnTip"] == "", "справка осталась на disabled-контроле"
    assert res["devTag"] == "span" and "vfxlock" in res["devClass"], res
    assert res["devTip"] == tip and res["devTab"] == "0", res
    assert res["btnDisabled"] is True and res["btnOwnTip"] == "", "справка осталась на кнопке «Настроить»"
    assert res["btnTag"] == "span" and "vfxlock" in res["btnClass"], res
    assert res["btnTip"] == tip and res["btnTab"] == "0", res
    # В панели клипа ничего не заперто: ни обёртки, ни disabled
    assert res["clipBtnDisabled"] is False and res["clipDevWrapped"] is False, res


def test_css_stops_disabled_controls_from_taking_the_pointer() -> None:
    """Сторож CSS: у заперто-обёрнутого контрола pointer-events:none, и причина записана.

    Без этого правила hover уходит к disabled-контролу, и обёртка с подсказкой не
    получает mouseover (в Chrome события мыши disabled-контролам не доставляются).
    """
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    m = re.search(r"\.vfxlock\s*>\s*:disabled\s*\{\s*pointer-events\s*:\s*none\s*;?\s*\}", css)
    assert m is not None, "в app.css нет правила .vfxlock>:disabled{pointer-events:none}"
    before = css[:m.start()][-400:]
    assert "Chrome" in before and "disabled" in before, "правило без комментария — почему"
    js = STYLES_JS.read_text(encoding="utf-8")
    assert 'class="vfxlock"' in js and 'tabindex="0"' in js, "обёртка с подсказкой не отрисована"
