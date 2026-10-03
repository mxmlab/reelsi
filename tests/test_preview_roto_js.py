# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пункты 1-3 задания: кнопка расчёта и слой рото в превью AE (JS-стенд).

Гоняется НЕ копия формул, а тела боевых функций из `static/app/85-inserts-view.js`
(слой рото) и `static/app/87-roto-preview.js` (кнопка) — тот же приём, что в
`tests/test_ins_video_preview.py`.

Стережётся:

1. кнопка «Рассчитать рото и трекинг» видна только при `roto`/`camN_head_follow` в стиле
   клипа и не видна без них (иначе предлагала бы работу, которой в сборке не будет);
2. после «готово» вызывается `ipvRefresh` — план перезапрашивается, слежение приезжает
   из него само;
3. слой рото встаёт по `layer_order` (z-index = 10 - место, как у прочих слоёв): по
   дефолтному порядку он ВЫШЕ интро, а порядком `intro` первым — ниже; холст получает
   `display:block` (в CSS у него `display:none`);
4. кадр маски берётся по времени ВНУТРИ куска: `t − ts`, с квантом кадра — файл маски
   начинается с начала куска, и в AE у слоя маски startTime=ts (иначе показывался бы
   только последний кадр маски);
5. слой дорисовывается САМ: по метаданным маски (`loadeddata`), по кадру камеры и по
   `seeked` на паузе — после расчёта и перемотки холст не остаётся пустым;
6. тело запроса кнопки — то же, что у `/api/scene` (`ipvPlanBody`), плюс `build`;
7. маска отдаётся существующей раздачей медиа (`/api/media`).

Запуск:  python -m pytest tests/test_preview_roto_js.py -q -p no:cacheprovider
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

VIEW_JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
CALC_JS = os.path.join(ROOT, "static", "app", "87-roto-preview.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

# Функции слоя рото (85-inserts-view.js) — тела настоящие, не копии.
VIEW_FUNCS = ("ipvRotoAt", "ipvRotoMaskTime", "ipvRotoFrameStands", "ipvRotoShow",
              "ipvRotoVideo", "ipvRotoWaitCam")
# Функции кнопки (87-roto-preview.js).
CALC_FUNCS = ("ipvCalcWanted", "ipvCalcUI", "ipvCalcDone")
# Кнопка целиком: тела обеих дверей — то, что шлёт тело запроса (пункт 3).
CALC_RUN_FUNCS = CALC_FUNCS + ("ipvCalcApply", "ipvCalcRun")
# Функции слоя рото для стенда со сведением кадра (пункт 2): сведение гоняем настоящее —
# заглушены только камера и отрисовка её кадра (чужая машинерия, не логика слоя).
PAINT_FUNCS = ("ipvRotoAt", "ipvRotoMaskTime", "ipvRotoFrameStands", "ipvRotoShow",
               "ipvRotoVideo", "ipvRotoWaitCam", "ipvRotoCanvas", "ipvRotoClear",
               "ipvLumaAlphaFilter", "ipvRotoPaint", "ipvRoto")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_preview_cam.py).

    `async` входит в вырезку: без него тело с `await` не соберётся.
    """
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def _const(src, name):
    """Объявление константы name из исходника — тоже настоящее, не копия."""
    m = re.search(r"^const\s+%s\s*=\s*(.+?);\s*$" % re.escape(name), src, re.M)
    assert m, f"в исходнике не нашлась константа {name}"
    return "const %s=%s;" % (name, m.group(1))


def _stand(view_funcs=VIEW_FUNCS, calc_funcs=CALC_FUNCS, dom=None, view_consts=()):
    """Боевые функции обоих файлов + мини-окружение, которого им хватает."""
    view = _read(VIEW_JS)
    calc = _read(CALC_JS)
    funcs = "\n".join(_const(view, n) for n in view_consts) + "\n" \
        + "\n".join(_func(view, n) for n in view_funcs) + "\n" \
        + "\n".join(_func(calc, n) for n in calc_funcs)
    return funcs + "\n" + (dom if dom is not None else _DOM_JS)



# Мини-DOM: ровно то, что нужно кнопке и слою рото. Своих копий логики тут нет.
_DOM_JS = r"""
let IPVCALC_UP=false;
let IPVCALC_TO=0;
const IPV_ROTOSEQ=[];
var IPV={fps:60,plan:null,roto:[],xml:'',vids:[]};
function El(tag){this.tag=tag;this.style={};this.className='';this.dataset={};
  this._cls={};this._ev={};this.muted=false;this.playsInline=false;this.preload='';}
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.removeEventListener=function(n,f){var a=this._ev[n]||[];var i=a.indexOf(f);
  if(i>=0)a.splice(i,1);};
Object.defineProperty(El.prototype,'classList',{get:function(){var self=this;return {
  add:function(c){self._cls[c]=1;},remove:function(c){delete self._cls[c];},
  contains:function(c){return !!self._cls[c];}};}});
var _els={};
function $(id){return _els[id]||null;}
var _modal=new El('div');_modal.classList.add('on');_els['mbInserts']=_modal;
_els['ipvstage']=new El('div');
var IPVMODE='ae';
function aewOn(){return $('mbInserts').classList.contains('on')&&IPVMODE==='ae';}
var STSCHEMA={base:{roto:false,cam1_head_follow:false,cam2_head_follow:false}};
var CURSTYLE=null;
var document={createElement:function(t){return new El(t);}};
function vidSeekTol(){return 0.4;}
function t(s){return s;}
"""


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_button_visible_only_with_roto_or_head_follow():
    """1. Кнопка видна при `roto`/`cam1_head_follow`/`cam2_head_follow` и скрыта без них."""
    code = _stand() + """
    _els['rotoCalcBtn']=new El('button');_els['rotoCalcRes']=new El('span');
    function look(style,ready,up){
      CURSTYLE=style;IPVCALC_UP=up;ipvCalcUI(ready);
      return {want:ipvCalcWanted(),btn:$('rotoCalcBtn').style.display,
              res:$('rotoCalcRes').style.display};}
    console.log(JSON.stringify({
      roto:look({roto:true},false,false),
      head1:look({cam1_head_follow:true},false,false),
      head2:look({cam2_head_follow:true},false,false),
      none:look({roto:false,cam1_head_follow:false,cam2_head_follow:false},false,false),
      done:look({roto:true},true,true)}));
    """
    out = _run_node(code)

    for key in ("roto", "head1", "head2"):
        assert out[key]["want"] is True, f"{key}: стиль не распознан как «есть что считать»"
        assert out[key]["btn"] == "", f"{key}: кнопка спрятана при включённом рото/слежении"
        assert out[key]["res"] == "none", f"{key}: пометка «посчитано» показана без расчёта"
    assert out["none"]["want"] is False, "без рото и слежения стиль признан рабочим"
    assert out["none"]["btn"] == "none", "кнопка видна, хотя считать нечего"
    # готово: кнопки нет, пометка «посчитано» на месте (Pulse Green — только статус)
    assert out["done"]["btn"] == "none" and out["done"]["res"] == "", out["done"]


@node
def test_done_refreshes_the_plan():
    """2. После «готово» план перезапрашивается (ipvRefresh) — слежение приезжает из него."""
    code = _stand() + """
    var calls={apply:0,refresh:0,drop:0,ui:0,log:0};
    async function ipvCalcApply(){calls.apply++;return null;}
    function ipvRefresh(){calls.refresh++;}
    function pvProgDrop(){calls.drop++;}
    function uiLog(){calls.log++;}
    function ipvCalcUI(){calls.ui++;}
    (async()=>{await ipvCalcDone();console.log(JSON.stringify(calls));})();
    """
    out = _run_node(code)

    assert out["refresh"] == 1, "после расчёта план не перезапрошен (ipvRefresh)"
    assert out["apply"] == 1, "после расчёта не подтянут кэш масок (ipvCalcApply)"
    assert out["drop"] == 1, "строка прогресса осталась висеть на кадре"


@node
def test_roto_layer_follows_layer_order():
    """3. Слой рото — обычный слой layer_order: дефолтным порядком выше интро, другим — ниже."""
    code = _stand() + """
    function zIntro(order){const i=order.indexOf('intro');return i>=0?(10-i):0;}
    function z(order){IPV.plan={layer_order:order};var cv=new El('canvas');
      ipvRotoShow(cv);return {z:+cv.style.zIndex,disp:cv.style.display,over:null};}
    var def=z(['subs','video','roto','photo','intro']);
    var custom=z(['intro','subs','video','photo','roto']);
    var noRoto=z(['subs','video','photo']);
    console.log(JSON.stringify({
      def:def,custom:custom,noRoto:noRoto,
      introDef:zIntro(['subs','video','roto','photo','intro']),
      introCustom:zIntro(['intro','subs','video','photo','roto'])}));
    """
    out = _run_node(code)

    # дефолт: рото (место 2 -> z 8) ВЫШЕ интро (место 4 -> z 6)
    assert out["def"]["z"] == 8, out["def"]
    assert out["def"]["z"] > out["introDef"], \
        "по дефолтному layer_order рото оказалось ПОД интро"
    # порядком intro первым интро выше рото — слой ушёл под него
    assert out["custom"]["z"] < out["introCustom"], \
        "layer_order с интро сверху не опустил рото под интро"
    # холст показывается именно block'ом: в CSS у canvas.ipvroto стоит display:none
    assert out["def"]["disp"] == "block" and out["custom"]["disp"] == "block", out
    # рото нет в порядке — слой всё равно рисуется, но ниже всех (запасной z)
    assert out["noRoto"]["disp"] == "block" and out["noRoto"]["z"] == 8, out["noRoto"]


@node
def test_mask_frame_time_is_time_inside_chunk():
    """4. Кадр маски = t − ts (время ВНУТРИ куска), с квантом кадра (пауза не дрожит).

    Файл маски начинается с начала куска (ffprobe: кусок ts=0..te=4.567 → маска 4.571 с),
    и в AE у слоя маски startTime=ts (`template.py`: `mk.startTime=rr.ts`). Значит кадр
    маски — `t − ts`; прибавка `src_start` упирала видео в конец, и всё время показывался
    последний кадр маски (ошибка живой проверки).
    """
    code = _stand() + """
    var p={ci:0,src_start:2.0,ts:10.0,te:14.0};
    IPV.roto=[p];
    var v={seeking:false,currentTime:2.0};
    console.log(JSON.stringify({
      atStart:ipvRotoMaskTime(p,10.0,60),
      mid:ipvRotoMaskTime(p,12.0,60),
      quant:ipvRotoMaskTime(p,10.0+1/240,60),
      before:ipvRotoMaskTime(p,0.0,60),
      hit:!!ipvRotoAt(12.0),miss:ipvRotoAt(1.0),
      stands:ipvRotoFrameStands(v,p,12.0,60),
      notStands:ipvRotoFrameStands(v,p,10.0,60)}));
    """
    out = _run_node(code)

    assert abs(out["atStart"] - 0.0) < 1e-9, out
    assert abs(out["mid"] - 2.0) < 1e-9, \
        "кадр маски не равен времени внутри куска (t − ts): %r" % (out["mid"],)
    assert abs(out["quant"] - 0.0) < 1e-9, "подкадр (1/240 с) не округлился к кадру 60p"
    assert abs(out["before"] - 0.0) < 1e-9, "до начала куска маска ушла в отрицательное время"
    assert out["hit"] is True and out["miss"] is None, out
    assert out["stands"] is True and out["notStands"] is False, out


@node
def test_mask_comes_through_media_route():
    """7. Кадр маски берётся существующей раздачей медиа (/api/media), не своей дверью."""
    code = _stand() + """
    IPV.roto=[{ci:0,src_start:1.0,ts:0.0,te:2.0,mask:'C:/base/roto/_cache/cam1/roto_ab.mp4'}];
    var el=ipvRotoVideo(0,IPV.roto[0]);
    var again=ipvRotoVideo(0,IPV.roto[0]);
    console.log(JSON.stringify({src:el.src,same:el===again,
      muted:el.muted,inline:el.playsInline}));
    """
    out = _run_node(code)

    assert out["src"].startswith("/api/media?path="), out["src"]
    assert "roto_ab.mp4" in out["src"], out["src"]
    assert out["same"] is True, "элемент маски пересоздаётся на каждом кадре"
    assert out["muted"] is True and out["inline"] is True, out


# --------------------------------------------------------------------------- #
# Мини-окружение стенда со СВЕДЕНИЕМ кадра (пункт 2). Камера и отрисовка её кадра —
# заглушки (чужая машинерия: в узле нет ни видео, ни canvas 2D), сам слой рото —
# боевые функции. Счётчик `d` — сколько раз фигура легла на холст слоя.
# --------------------------------------------------------------------------- #
_DOM_PAINT_JS = r"""
let IPV_ROTO='';
const IPV_ROTOSEQ=[];
let IPV_ROTOACC=-1;
let IPV_ROTODRAWN=-1;
var IPV={fps:60,plan:{layer_order:['subs','video','roto','photo','intro']},
  roto:[],xml:'C:/clips/a.xml',vids:[]};
var _els={};
function $(id){return _els[id]||null;}
function Ctx(){this.d=0;this.c=0;this.filter='none';this.globalCompositeOperation='source-over';}
Ctx.prototype.setTransform=function(){};
Ctx.prototype.clearRect=function(){this.c++;};
Ctx.prototype.drawImage=function(){this.d++;};
function El(tag){this.tag=tag;this.style={};this.className='';this.dataset={};this._ev={};
  this.width=0;this.height=0;this.videoWidth=0;this.videoHeight=0;this.readyState=0;
  this.currentTime=0;this.seeking=false;this.seekable={length:1};this._ctx=null;}
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.removeEventListener=function(n,f){var a=this._ev[n]||[];var i=a.indexOf(f);
  if(i>=0)a.splice(i,1);};
El.prototype.fire=function(n){var a=(this._ev[n]||[]).slice();for(var i=0;i<a.length;i++)a[i]({type:n});};
El.prototype.getContext=function(){if(!this._ctx)this._ctx=new Ctx();return this._ctx;};
El.prototype.getBoundingClientRect=function(){return {width:1080,height:1920};};
El.prototype.appendChild=function(c){return c;};
El.prototype.insertBefore=function(c,x){return c;};
El.prototype.setAttribute=function(){};
El.prototype.querySelectorAll=function(){return [];};
var document={createElement:function(t){return new El(t);},
  createElementNS:function(){return new El('svg');}};
var window={devicePixelRatio:1};
_els['ipvstage']=new El('div');
_els['ipvroto']=new El('canvas');
var CAM=[new El('video')];
function ipvCamSrc(ci){return CAM[ci];}
function lutKS(){return [1,1];}
function ipvDrawFrame(){return {m:[1,0,0,1,0,0],uv:[0,0,1,1],dst:[0,0,1080,1920]};}
var NOW=0;
function ipvNow(){return NOW;}
function draws(){return $('ipvroto')._ctx?$('ipvroto')._ctx.d:0;}
function clears(){return $('ipvroto')._ctx?$('ipvroto')._ctx.c:0;}
function vidSeekTol(){return 0.4;}
function t(s){return s;}
"""


@node
def test_layer_draws_itself_on_its_own_events():
    """5. Слой рото сводится САМ: метаданные маски, кадр камеры, `seeked` на паузе.

    Живая проверка: после `ipvCalcDone`/`ipvSeekTo(2.0)` холст был пуст (0 непрозрачных
    пикселей) и рисовался только ручным вызовом со сбросом `IPV_ROTOACC`. Две причины:
    сведение по неготовому кадру камеры помечалось сделанным (и повтор уже не приходил),
    а перемотка маски/приход её метаданных не подписывались повторным сведением.
    """
    code = _stand(PAINT_FUNCS, (), _DOM_PAINT_JS, view_consts=("IPV_LUMA_A",)) + """
    var p={ci:0,ts:10.0,te:14.0,src_start:2.0,mask:'C:/base/roto/_cache/cam1/roto_ab.mp4'};
    IPV.roto=[p];
    CAM[0].videoWidth=1920;CAM[0].videoHeight=1080;CAM[0].readyState=2;
    // 1) метаданных маски ещё нет: слой пуст, кадр не помечен сведённым
    NOW=12.0;ipvRoto(12.0);
    var a1=draws(),c1=clears(),el=IPV_ROTOSEQ[0];
    el.videoWidth=1920;el.videoHeight=1080;el.readyState=2;
    el.fire('loadeddata');                    // метаданные приехали — слой свёлся сам
    var a2=draws(),cap=el.currentTime;
    // 2) кадра камеры нет: сведение не удалось — и это НЕ «сведено»
    CAM[0].readyState=1;NOW=13.0;ipvRoto(13.0);
    var b1=draws();
    CAM[0].readyState=2;CAM[0].fire('seeked');   // кадр камеры пришёл — дорисовались сами
    var b2=draws();
    // 3) перемотка маски на паузе: `seeked` перерисовывает холст без внешнего вызова
    NOW=13.5;ipvRoto(13.5);
    var d1=draws();
    el.fire('seeked');
    var d2=draws();
    console.log(JSON.stringify({a1:a1,clears1:c1,a2:a2,cap:cap,b1:b1,b2:b2,d1:d1,d2:d2,
      disp:$('ipvroto').style.display}));
    """
    out = _run_node(code)

    assert out["a1"] == 0 and out["clears1"] >= 1, \
        "без метаданных маски слой нарисовался (рисовать нечем): %r" % (out,)
    assert out["a2"] == 1 and abs(out["cap"] - 2.0) < 1e-9, \
        "по метаданным маски слой не свёлся кадром t − ts: %r" % (out,)
    assert out["b1"] == out["a2"], \
        "сведение по неготовому кадру камеры зачлось сделанным (пустой холст не пересведётся)"
    assert out["b2"] == out["a2"] + 1, \
        "кадр камеры пришёл, а слой сам не дорисовался: %r" % (out,)
    assert out["d2"] == out["d1"] + 1, \
        "`seeked` маски на паузе не перерисовал холст без внешнего вызова"
    assert out["disp"] == "block", "холст слоя не показан (в CSS у него display:none)"


@node
def test_calc_body_is_the_scene_plan_body():
    """6. Тело /api/preview_calc — то же, что у /api/scene (`ipvPlanBody`), плюс `build`.

    Разметка рото зависит от интро и вставок задания: от «{xml, style}» (как было в
    `87-roto-preview.js`) план вышел бы другим, и маски искались бы не по тем кускам.
    """
    code = _stand(VIEW_FUNCS, CALC_RUN_FUNCS) + """
    var PLAN={xml:'C:/clips/a.xml',style:{roto:true},inserts:[{media:'x.png'}],
      intro:[],intro_remove:[],intro_splits:[],music:'m.mp3',cams:2,exposure:0,
      roto:true,roto_bottom:0};
    var seen=null,asked=0;
    function ipvPlanBody(){asked++;return Object.assign({},PLAN);}
    function fetch(url,opt){seen={url:url,body:JSON.parse(opt.body)};
      return Promise.resolve({json:function(){return Promise.resolve(
        {ok:true,roto:[],head:{ready:false}});}});}
    async function ipvCalcPoll(){}
    async function ipvCalcDone(){}
    function ipvRoto(){}
    function ipvNow(){return 0;}
    function toast(){}
    function errText(e){return String(e);}
    IPV.xml='C:/clips/a.xml';
    (async()=>{
      await ipvCalcApply();
      var apply={asked:asked,url:seen.url,body:seen.body};
      IPVCALC_UP=false;
      await ipvCalcRun();
      console.log(JSON.stringify({apply:apply,run:{asked:asked,url:seen.url,body:seen.body},
        plan:PLAN}));
    })();
    """
    out = _run_node(code)

    assert out["apply"]["url"] == "/api/preview_calc", out
    assert out["apply"]["body"] == out["plan"], \
        "тело ipvCalcApply — не ipvPlanBody(): %r" % (out["apply"]["body"],)
    assert "build" not in out["apply"]["body"], \
        "быстрая дверь просит расчёт (`build`): %r" % (out["apply"]["body"],)
    assert out["apply"]["asked"] == 1, "ipvCalcApply собирает тело мимо ipvPlanBody"
    assert out["run"]["url"] == "/api/preview_calc", out
    assert out["run"]["body"] == dict(out["plan"], build=True), \
        "тело ipvCalcRun — не ipvPlanBody() с признаком build: %r" % (out["run"]["body"],)
    assert out["run"]["asked"] == 2, "ipvCalcRun собирает тело мимо ipvPlanBody"
