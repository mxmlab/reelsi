# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Видеовставки в превью шага 2: без моргания чёрным и с рабочим «Масштаб, %».

Два дефекта, пойманных владельцем (2026-09-19), и что тут стережётся:

1. **Моргание.** Перестройка оверлея (`ipvOverlayPlan`/`ipvOverlay`) на каждый новый план
   (а его сбрасывает каждая правка: `ipvRefresh` → `IPV.cur=-2` → `ipvPlanFetch`) делала
   `ov.innerHTML=''` и создавала НОВЫЙ `<video>` с тем же `src` — свежий элемент показывает
   чёрное, пока не загрузит первый кадр. Теперь элементы живут в кэше `IPV.insVids` по пути
   файла и переезжают из обёртки в обёртку, `src` у живого элемента не переприсваивается.
   Элементы, чьих вставок больше нет в клипе/плане, освобождаются (`pause` + снять `src` +
   `load`) — иначе каждый держит соединение.

2. **Масштаб.** Ветка `ipvInsPlace` для `sc>=100` жёстко ставила `width/height:100%` с
   `object-fit:cover` и `sc` не читала вовсе: «Масштаб, %» больше 100 не менял на экране
   ничего. Хуже — на `sc≠100` обёртка теряет класс `.full`, и элемент попадает под правило
   `.ipvins video{max-width:72%;max-height:46%}`: ролик рисовался маленькой обрезанной
   карточкой вместо кадра. Теперь коробка ОДНА на все `sc`: заполнение кадра при `sc=100`
   (`fitw/fith` из плана) × `sc/100`; нет полей — по закэшированным размерам того же файла;
   нет и их — после `loadedmetadata` кадр перерисовывается (молча без масштаба не остаёмся).

Гоняется НЕ копия формул, а сам отгружаемый код: функции вырезаются из
`static/app/85-inserts-view.js` и исполняются node'ом на мини-DOM. Панорама
(`_fill_slack`/`insVideoPan`) — свободная при любом масштабе: позиция =
x/y пользователя × k, клампа запасом вылета нет; уехав за край ролика, вставка
открывает кадр камеры. `slackx`/`slacky` в плане остались справкой.

Запуск: python -m pytest tests/test_ins_video_preview.py -q
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

JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

W, H = 1080, 1920                      # кадр превью = кадр композиции (стойка 9:16)
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}

# функции боевого файла, которые нужны стенду (тела настоящие, не копии)
FUNCS = ("normInsPath", "insVideoFill", "insVidCache", "insVidDimsMap", "insVidKey",
         "insVideoEl", "insVidFree", "insVidDimsGet", "insVidDimsPut", "insVidPlanDims",
         "ipvInsDims", "insVidKeep", "insVidSweep", "insVidFreeAll",
         "ipvIns", "insSD", "insImgURL", "insCardKey", "ipvInsPlace", "ipvOverlayPlan")


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_preview_cam.py)."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
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


def _js():
    """Настоящие тела функций из static/app/85-inserts-view.js — для прогона в node."""
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in FUNCS)


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _fill_box(iw, ih):
    """Коробка заполнения кадра при sc=100 — то, что план несёт как fitw/fith.

    Тот же расчёт, что `_fit_scale`/`_ins_js` в core/xml2ae (Python округляет до 2 знаков).
    """
    f = max(W / iw, H / ih)
    return round(iw * f, 2), round(ih * f, 2)


# ---- мини-DOM: ровно то, что нужно оверлею вставок, и ничего лишнего ----
# Стенд исполняет боевые функции как есть, поэтому документ должен вести себя как настоящий
# там, где это важно для контракта: `innerHTML=''` выбрасывает детей (иначе перестройка
# оверлея находила бы старую обёртку), `src` живёт атрибутом (снятый src — это
# `removeAttribute`, а не пустая строка), `removeChild` отвязывает элемент.
_DOM_JS = r"""
var STAGE_W = 1080;                      // ширина сцены превью: k = 1, px превью = px кадра
function El(tag){
  this.tag=tag;this.children=[];this.style={};this.dataset={};this._attrs={};this._ev={};
  this.className='';this.parentNode=null;this.textContent='';
  this.muted=false;this.playsInline=false;this.preload='';this.paused=true;
  this.currentTime=0;this.videoWidth=0;this.videoHeight=0;this.calls=[];
}
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.removeChild=function(c){var i=this.children.indexOf(c);
  if(i>=0)this.children.splice(i,1);c.parentNode=null;return c;};
Object.defineProperty(El.prototype,'firstChild',{get:function(){return this.children[0]||null;}});
Object.defineProperty(El.prototype,'clientWidth',{get:function(){return STAGE_W;}});
Object.defineProperty(El.prototype,'innerHTML',{
  get:function(){return this._html||'';},
  set:function(v){this._html=String(v);if(String(v)==='')this.children=[];}});
Object.defineProperty(El.prototype,'src',{
  get:function(){return this._attrs.src||'';},
  set:function(v){this._attrs.src=String(v);this.srcSets=(this.srcSets||0)+1;}});
El.prototype.setAttribute=function(n,v){this._attrs[n]=String(v);};
El.prototype.getAttribute=function(n){return (n in this._attrs)?this._attrs[n]:null;};
El.prototype.removeAttribute=function(n){delete this._attrs[n];};
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.fire=function(n){var a=this._ev[n]||[];for(var i=0;i<a.length;i++)a[i]({type:n});};
El.prototype.pause=function(){this.paused=true;this.calls.push('pause');};
El.prototype.play=function(){this.paused=false;this.calls.push('play');return {catch:function(){}};};
El.prototype.load=function(){this.calls.push('load');};
El.prototype.querySelector=function(sel){
  var m=/^\[data-ins="(.+)"\]$/.exec(sel);
  if(m){for(var i=0;i<this.children.length;i++)
    if(String(this.children[i].dataset.ins)===m[1])return this.children[i];return null;}
  if(sel==='img'||sel==='video'){for(var j=0;j<this.children.length;j++)
    if(this.children[j].tag===sel)return this.children[j];return null;}
  return null;};
var OV=new El('div');
var document={createElement:function(t){return new El(t);},
              querySelectorAll:function(){return [];}};
function $(id){return id==='ipvins'?OV:null;}
function t(s){return s;}
function esc(s){return String(s==null?'':s);}
function isPhotoPath(p){return /\.(png|jpe?g|webp|avif|gif|bmp)$/i.test(p||'');}
function ipvNow(){return 1;}                       // плейхед внутри окна тестовой вставки
function ipvUI(tm){uiCalls++;ipvOverlayPlan(tm);}  // как в бою: ipvUI -> ipvOverlay -> план
var uiCalls=0;
var IPVMODE='clips', curIns=0, curAE=-1, INS=[], CLIPS=[{inserts:[]}];
var IPV={fps:60,plan:null,insShift:null,cur:-1,vids:[],insVids:new Map(),dims:new Map()};
// вставка плана: поля ровно те, что шлёт /api/scene (start/end в секундах, x/y в px кадра)
function mkItem(sc,extra){
  var x={t:'video',media:'C:/v/clip.mp4',start:0,end:2,sc:sc,x:0,y:0,sin:0,noexit:false,front:true};
  for(var k in (extra||{}))x[k]=extra[k];
  return x;}
function mkPlan(items){return {w:1080,h:1920,layer_order:['subs','video','roto','photo','intro'],
                               inserts:items};}
function build(items,hard,tm){
  if(hard){IPV.insVids=new Map();IPV.dims=new Map();OV.innerHTML='';}
  IPV.plan=mkPlan(items);IPV.cur=-2;IPV.insShift=null;
  ipvOverlayPlan(tm==null?1:tm);
  var wr=OV.querySelector('[data-ins="0"]');
  return wr?wr.firstChild:null;}
"""


@node
def test_same_video_element_survives_overlay_rebuild():
    """1. Две перестройки оверлея подряд (правка → новый план) — тот же `<video>`.

    Моргание чёрным было ровно от пересоздания: новый элемент с тем же `src` показывает
    чёрное, пока не загрузит кадр. Сторож держит и то, что `src` живому элементу не
    переприсваивают (srcSets — сколько раз элемент получал src за свою жизнь).
    """
    code = _js() + _DOM_JS + """
    var fw=%s, fh=%s;
    var v1=build([mkItem(100,{fitw:fw,fith:fh})]);
    var src1=v1.src;
    // правка масштаба: план пересчитан, IPV.cur сброшен (ipvRefresh) — оверлей строится заново
    var v2=build([mkItem(130,{fitw:fw,fith:fh})]);
    console.log(JSON.stringify({same:v1===v2, src1:src1, src2:v2.src,
      sets1:v1.srcSets||0, sets2:v2.srcSets||0, cache:IPV.insVids.size,
      w2:v2.style.width||'', inDOM:OV.querySelector('[data-ins="0"]').firstChild===v2}));
    """ % _fill_box(1920, 1080)
    out = _run_node(code)

    assert out["same"], "перестройка оверлея пересоздала <video> — кадр моргнёт чёрным"
    assert out["src1"] and out["src1"] == out["src2"], f"src вставки потерялся: {out}"
    assert out["sets2"] == 1, f"живому элементу переприсвоили src: {out['sets2']} раз"
    assert out["cache"] == 1, f"в кэше не один элемент на файл: {out['cache']}"
    assert out["inDOM"], "переиспользованный элемент не встал в новую обёртку"
    # масштаб новой правки доехал до того же самого элемента
    assert abs(float((out.get("w2") or "0").rstrip("px")) - _fill_box(1920, 1080)[0] * 1.3) < 1e-6, \
        out.get("w2")


@node
def test_vanished_insert_releases_its_video():
    """2. Вставки больше нет в плане — её `<video>` освобождён (`pause`, снятый `src`, `load`).

    Иначе каждый просмотренный файл висит соединением (браузер держит 6
    соединений на сервер, и превью шага 2 начинает ждать по несколько секунд).
    """
    code = _js() + _DOM_JS + """
    var v=build([mkItem(100)]);
    var src0=v.src;
    build([]);                                    // вставку убрали из клипа — план без неё
    console.log(JSON.stringify({src0:src0, src:v.src, attr:v.getAttribute('src'),
      calls:v.calls, cache:IPV.insVids.size, wrappers:OV.children.length}));
    """
    out = _run_node(code)

    assert out["src0"], "вставка не получила src — тест ничего не проверяет"
    assert out["src"] == "", f"src остался на элементе: {out['src']}"
    assert out["attr"] is None, "src снят не через removeAttribute — файл всё ещё привязан"
    assert "pause" in out["calls"] and "load" in out["calls"], \
        f"элемент не освобождён (pause+load): {out['calls']}"
    assert out["cache"] == 0, f"освобождённый элемент остался в кэше: {out['cache']}"
    assert out["wrappers"] == 0, "обёртка исчезнувшей вставки осталась в оверлее"


@node
def test_video_out_of_frame_pauses_but_stays_cached():
    """2б. Вставка ещё в плане, но плейхед ушёл на другую: элемент на паузе и остаётся в кэше.

    Кэш держит элемент ради повторного показа (вернулись — кадр уже декодирован, моргания
    нет), но детачнутый `<video>` продолжает играть сам по себе: без паузы втихую крутились
    бы все просмотренные вставки клипа. Освобождать его нельзя — вставка из клипа не пропала.
    """
    code = _js() + _DOM_JS + """
    var A=mkItem(100,{media:'C:/v/a.mp4',start:0,end:2});
    var B=mkItem(100,{media:'C:/v/b.mp4',start:5,end:7});
    build([A,B],true,1);                                  // в кадре A
    var va=IPV.insVids.get(normInsPath('C:/v/a.mp4'));
    va.paused=false;                                      // в бою её запускает цикл показа
    build([A,B],false,6);                                 // плейхед ушёл на B
    var vb=IPV.insVids.get(normInsPath('C:/v/b.mp4'));
    console.log(JSON.stringify({cache:IPV.insVids.size, aPaused:va.paused,
      aCalls:va.calls, aSrc:va.src, b:!!vb, bSrc:vb?vb.src:''}));
    """
    out = _run_node(code)

    assert out["aPaused"], "ушедшая из кадра вставка играет втихую (не поставлена на паузу)"
    assert "pause" in out["aCalls"], f"паузы не было вовсе: {out['aCalls']}"
    assert out["aSrc"], "элемент освободили, хотя вставка из клипа не пропала — кадр моргнёт заново"
    assert out["cache"] == 2, f"в кэше не оба файла клипа: {out['cache']}"
    assert out["b"] and out["bSrc"], "новая вставка не получила свой элемент"


@node
def test_video_box_is_fill_times_scale_from_the_plan():
    """3. Коробка видео = `fitw*sc/100` × `fith*sc/100` при sc 80/100/130 — и без метаданных.

    Размер берётся из ПЛАНА (его посчитал Python из размеров файла), а не из `videoWidth`
    свежего элемента: у только что созданного `<video>` размеров ещё нет (0 до
    `loadedmetadata`), и вставка показывалась без масштаба. Кадр 1:1 (k=1), поэтому px
    превью равны px композиции.
    """
    cases = []
    for name, (iw, ih) in SIZES.items():
        fw, fh = _fill_box(iw, ih)
        for sc in (80, 100, 130):
            cases.append({"name": f"{name} sc={sc}", "sc": sc, "fitw": fw, "fith": fh,
                          "want_w": fw * sc / 100, "want_h": fh * sc / 100})
    code = _js() + _DOM_JS + """
    var CASES=%s;
    var out=CASES.map(function(c){
      var v=build([mkItem(c.sc,{fitw:c.fitw,fith:c.fith,x:0,y:0})]);
      var w=parseFloat(v.style.width);
      return {name:c.name, w:(isNaN(w)?0:w), h:parseFloat(v.style.height),
              raw_w:v.style.width||'', vw:v.videoWidth, vh:v.videoHeight};});
    console.log(JSON.stringify(out));
    """ % json.dumps(cases)
    out = _run_node(code)

    for c, got in zip(cases, out):
        assert got["vw"] == 0 and got["vh"] == 0, \
            f"{c['name']}: стенд выдал метаданные — «сразу после создания» не проверить"
        assert got["raw_w"] not in ("", None), \
            f"{c['name']}: у видео нет размера вовсе — масштаб нечего применять"
        assert abs(got["w"] - c["want_w"]) < 1e-6, \
            f"{c['name']}: ширина {got['raw_w']}, ждали {c['want_w']}px (fitw*sc/100)"
        assert abs(got["h"] - c["want_h"]) < 1e-6, \
            f"{c['name']}: высота {got['h']}, ждали {c['want_h']}px (fith*sc/100)"


@node
def test_scale_above_100_zooms_the_frame():
    """4. Ветка «масштаб не виден»: на `main` sc>100 не менял на экране НИЧЕГО.

    Было: `if(sc>=100)` ставила width/height 100% с object-fit:cover, а sc не читала.
    Стало: коробка заполнения × sc/100, то есть увеличение — это настоящий наезд кадра,
    и ролик по-прежнему заполняет кадр (коробка шире кадра, панорама x/y внутри).

    Здесь же стережётся панорама: клампа запасом вылета больше нет — x/y
    едут как заданы, при любом масштабе, и ужатое видео (sc<100) тоже.
    """
    fw, fh = _fill_box(1920, 1080)
    code = _js() + _DOM_JS + """
    var fw=%s, fh=%s, slackx=(fw-1080)/2;
    function look(sc,x,y){
      var v=build([mkItem(sc,{fitw:fw,fith:fh,slackx:slackx,slacky:0,x:x,y:y})]);
      var w=parseFloat(v.style.width);
      return {w:(isNaN(w)?0:w), raw:v.style.width||'', fit:v.style.objectFit,
              mw:v.style.maxWidth, mh:v.style.maxHeight, tf:v.style.transform};}
    console.log(JSON.stringify({s100:look(100,0,0), s130:look(130,0,0),
      pan100:look(100,99999,-99999), pan130:look(130,99999,-99999), free80:look(80,99999,-99999)}));
    """ % (fw, fh)
    out = _run_node(code)

    # масштаб 130% обязан УВЕЛИЧИТЬ коробку (на main тут было width:100% с cover)
    assert abs(out["s130"]["w"] - fw * 1.3) < 1e-6, \
        f"sc=130 не даёт наезда: {out['s130']['raw']} (ждали {fw * 1.3}px)"
    assert out["s130"]["w"] > out["s100"]["w"], \
        "увеличение масштаба не увеличило коробку — «Масштаб, %» не виден"
    # ролик по-прежнему ЗАПОЛНЯЕТ кадр: коробка шире кадра, и пропорции у неё те же, что у
    # файла (object-fit:fill ничего не растягивает — растягивать нечего)
    assert out["s130"]["w"] > W, f"коробка уже кадра — ролик перестал его заполнять: {out['s130']}"
    assert out["s130"]["fit"] == "fill", \
        f"на sc=130 остался object-fit:{out['s130']['fit']} в коробке кадра"
    # .full у обёртки при sc!=100 нет — правило .ipvins video{max-width:72%;max-height:46%}
    # снимается только инлайновыми none, иначе ролик рисуется маленькой карточкой
    assert out["s130"]["mw"] == "none" and out["s130"]["mh"] == "none", \
        f"коробка видео не защищена от CSS max-width/max-height: {out['s130']}"
    assert out["s100"]["mw"] == "none" and out["s100"]["mh"] == "none", out["s100"]

    # панорама: ни запас вылета (16:9 по X ~1166 px, по Y — ноль), ни масштаб позицию не
    # режут — было `translate(slackx px,0px)`, стало ровно заданное
    for key in ("pan100", "pan130", "free80"):
        assert out[key]["tf"] == "translate(99999px,-99999px)", \
            f"{key}: позицию вставки зажали запасом вылета: {out[key]['tf']}"


@node
def test_dims_arrive_with_metadata_and_the_frame_is_redrawn():
    """5. Размеров нет ни в плане, ни в кэше — после `loadedmetadata` кадр перерисовывается.

    Поля fitw/fith в плане нет, когда `_media_dims` не прочитал размеры файла. Раньше
    preview молча оставался без масштаба (для sc<100 коробкой служил сам кадр 1080×1920 —
    ролик растягивался в 9:16). Теперь размеры запоминаются с метаданных, и кадр
    перерисовывается сам — «Масштаб, %» снова работает.
    """
    code = _js() + _DOM_JS + """
    var v=build([mkItem(100,{fitw:null,fith:null,slackx:null,slacky:null})]);
    var before=v.style.width||'';
    var calls0=uiCalls;
    v.videoWidth=1920;v.videoHeight=1080;v.fire('loadedmetadata');
    console.log(JSON.stringify({before:before, after:v.style.width, uiCalls:uiCalls-calls0,
      dims:IPV.dims.size}));
    """
    out = _run_node(code)

    f = max(W / 1920, H / 1080)
    assert out["before"] == "", \
        f"без размеров коробка поставлена «на глаз»: {out['before']}"
    assert out["uiCalls"] == 1, "метаданные не перерисовали кадр — вставка осталась без масштаба"
    assert out["dims"] == 1, "размеры файла не закэшированы — следующий показ снова без размера"
    assert abs(float(out["after"].rstrip("px")) - 1920 * f) < 1e-6, \
        f"после метаданных коробка {out['after']}, ждали {1920 * f}px"
