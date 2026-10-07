# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рендер без After Effects: покадровая отрисовка и её доставка в файл.

Стенд гоняет НЕ копию формул, а сам отгружаемый код: функции вырезаются из
`static/app/85-inserts-view.js` и исполняются node'ом на мини-DOM. Проверяется то,
ради чего рендер и делался:

1. **Кадр по номеру воспроизводим.** `ipvRenderAt(t)` дважды подряд на одной `t`
   даёт одинаковое состояние накладок (текст, позиции, прозрачности, ширина плашки),
   а на разных `t` в окне анимации — разное. Возврат на прежнюю `t` после чужой
   даёт прежнюю картинку: между кадрами не остаётся состояния.
2. **В режиме рендера нет CSS-переходов.** Ширина плашки субтитров в живом превью
   едет переходом по реальному времени, в рендере её считает `ipvSubsBgAt` на момент
   кадра. Стенд смотрит инлайновый `transition` плашки и класс `render-mode` на корне;
   живое превью (без рендера) обязано остаться с переходом — оно работает как раньше.
3. **Ширина плашки в рендере — величина на момент кадра,** а не «как сложилось»:
   переезд на новую ширину считается на `t`, и на кадре перехода она промежуточная.
4. **Мутация ловится.** Если вернуть переход плашки на место, тот же стенд краснеет —
   это отдельный тест, он собирает стенд из испорченного исходника и требует провала.
5. **Кадр не ждёт «показанного» кадра <video>.** Живой замер владельца: seek 1.5–2.2 с
   на кадр при `VFRAME_MS=1500` — значит `requestVideoFrameCallback` у видео на паузе в
   Chrome без окна не приходит вовсе, и каждый кадр упирался в полный таймаут. Ждать
   нечего: после `seeked` кадр уже отдан декодером, а камеру рисует холст (drawImage).
   Стенд даёт `<video>`, у которого колбэк не зовётся никогда, а `seeked` приходит сразу:
   кадр обязан собраться быстрее 50 мс — и напрямую через `vFrameAt`, и боевой дверью.
6. **Мутация ожидания — тоже.** Вернуть безусловный потолок ожидания показанного кадра
   (`VFRAME_MS`) — стенд из испорченного исходника обязан покраснеть.
7. **Все <video> кадра ждутся разом.** Активная камера и видеовставки — разные декодеры:
   по очереди это их сумма, а не максимум. Стенд держит два `<video>` на потолке ожидания
   (`seeked` не приходит вовсе — файл не открылся) и требует и одного потолка по времени,
   и пересечения самих ожиданий: по времени одному «по очереди» ещё может повезти — вторая
   перемотка успевает отыграть, пока ждут первую.
8. **Повтор кадра не перематывает, соседний — перематывает.** Кадр, уже стоящий на
   просимом времени (разница меньше половины кадра), не трогается и не ждётся; соседний
   кадр (шаг 1/fps) — ДРУГОЙ кадр, и `<video>` на паузе само на него не перейдёт.

Плюс контракты Python-обвязки: тело сборки доезжает до страницы (роуты), кадры из
съёмщика читаются по префиксу длины, а отмена гасит СВОЙ Chrome по PID — не по имени.

Запуск: python -m pytest tests/test_webrender.py -q
"""
import json
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

from core.app_meta import console_emit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

JS85 = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
JS60 = os.path.join(ROOT, "static", "app", "60-preview.js")
JS00 = os.path.join(ROOT, "static", "app", "00-core.js")
JS95 = os.path.join(ROOT, "static", "app", "95-styles.js")
CAPTURE = os.path.join(ROOT, "core", "webrender", "capture.mjs")
TIMING = os.path.join(ROOT, "core", "webrender", "timing.mjs")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

W, H = 1080, 1920
FPS = 60
DASH = "\u2014"          # тире: в cp1252 его нет, и `print` на нём падает (см. тесты CLI ниже)


def _capture_json(cmd):
    """Разобрать командную строку съёмщика ЕГО ЖЕ кодом разбора и вернуть значения.

    `--parse-only` в capture.mjs печатает разобранное и выходит, не трогая ни Chrome,
    ни сеть. Поэтому тест сверяет не копию условий в Python, а реальный разбор:
    расхождение единиц или имени ключа между сторонами видно здесь, а не на живом
    прогоне («страница рендера не готова за 0 с»).
    """
    p = subprocess.run(["node", CAPTURE, *cmd[2:], "--parse-only"], capture_output=True,
                       text=True, encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, f"разбор аргументов съёмщика упал: {p.stderr}"
    return json.loads(p.stdout.strip().splitlines()[-1])


def _python_capture_cmd(tmp_path, chrome=None, node_exe="node"):
    """Командная строка, которую Python строит для съёмщика (без Chrome и ffmpeg)."""
    import core.webrender as wr

    return wr._capture_cmd(node_exe, "http://127.0.0.1:5001/render?xml=x.xml&body=abc&pxh=1080",
                           W, H, FPS, 300, 180, str(tmp_path / "профиль"), chrome)


def _free_port():
    """Свободный порт на 127.0.0.1: его отдаём подделывателю Chrome в тесте.

    Съёмщик сам выбирает порт отладочного протокола и ждёт на нём DevTools, поэтому
    стенд-браузер обязан слушать ЗАРАНЕЕ известный порт, а не произвольный: иначе
    ожидание упиралось бы в таймаут (30 с на каждом прогоне теста).
    """
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# Функции боевого файла, которые нужны стенду (тела настоящие, не копии). Из
# 60-preview.js — только выбор куска EDL (pvSegAt), он же зовётся из ipvSeekTo.
FUNCS85 = (
    "ipvNow", "ipvNowFrame", "ipvSeekTo", "ipvLeadTime", "ipvVideoTo",
    "ipvPlanWH", "ipvSubRawWidth", "ipvSubFullW",
    "ipvSubRawWAt", "ipvSubsBgAt", "ipvSubsInvalidate", "ipvSubs", "ipvTopLine",
    # Тень AE (Drop Shadow) в CSS: одна дверь на всё превью, зовут её и ipvSubs, и
    # ipvInsPlace, и ipvIntro — без неё в снятых кадрах тени бы не было вовсе.
    "aeShadowCss",
    "ipvCaption", "ipvDisc", "ipvShade", "ipvIntroChild", "ipvCamChild", "ipvCamShift",
    "ipvCamMatrix", "ipvLmSmooth", "ipvLumetriTone", "ipvLumetriTable", "ipvLumetriFilter",
    # Кадр камеры вынесен в ipvFrameGeom/ipvDrawFrame (одна отрисовка на превью и слой рото):
    # ipvCamPaint только готовит холст и зовёт их, поэтому тела нужны стенду тоже.
    "ipvFrameGeom", "ipvDrawFrame", "ipvCamPaint",
    # Слой рото зовёт ipvUI на каждом кадре (ipvRoto -> ipvRotoClear): масок у стенда нет
    # (IPV.roto пуст), слой снимается, но вызов по пути теста есть.
    "ipvRotoMaskZoom", "ipvRoto", "ipvRotoClear", "ipvStartBlur", "ipvZoomAt", "ipvZoom", "ipvUI",
    "ipvSeekTo", "ipvRenderMode", "ipvRenderModeOn", "ipvCamTimeAt", "vSeekDone",
    "vFrameShown", "vFrameAt", "ipvRenderAssets", "ipvRenderPaint", "ipvRenderPaintFrame",
    "ipvRenderAt",
    # Кадры камер картинками: правило «время → кадр», источник пикселей и окно картинок
    "ipvSrcFrameAt", "ipvFrameURL", "ipvFrameImg", "ipvFrameFail", "ipvFrameMiss",
    "ipvFrameWhy", "ipvFrameTrim", "ipvFrameReady", "ipvFrameAt", "ipvCamSrc",
    "ipvFramePrime", "ipvFrameInChunk",
    "keysAt", "aeEase", "bezierY", "bezierT", "insSD", "vidSeekTol", "ipvIns",
    "ipvOverlay", "ipvOverlayPlan", "ipvInsPlace", "insImgURL", "insCardKey",
    "ipvInsDraw", "ipvInsSrc", "ipvInsMosaics", "ipvPixelate", "drawMosaic",
    "insVidCache", "insVidKey", "insVidKeep", "insVidFree", "insVidSweep", "insVidDimsMap",
    "insVidDimsGet", "insVidDimsPut", "insVidPlanDims", "insVideoEl", "insVideoFill",
    "ipvInsDims", "normInsPath", "ipvIntro", "ipvFontFor",
    "sfxSync", "sfxPause", "itlPh", "ipvApplyVisual",
)
# Переменные состояния отрисовки: они объявлены на верхнем уровне файла, а не внутри
# функций, — стенду их надо взять оттуда же, а не заводить свои копии со своими числами.
DECLS85 = ("VFRAME_MS", "IPV_RT", "SUBW_CACHE", "SUBW_MEASURE", "SUBW_TOUCHED",
           "MOSAIC_BLOCK",
           "IPV_LM_HL", "IPV_LM_SH", "IPV_LM_WH", "IPV_LM_BL", "IPV_LM_BAL", "IPV_LM_N",
           "IPV_LM_HL_LO", "IPV_LM_HL_HI", "IPV_LM_HL_TOP",
           "IPV_LM_SH_LO", "IPV_LM_SH_HI", "IPV_LM_SH_TOP",
           "IPV_LM_GAIN_R", "IPV_LM_GAIN_G", "IPV_LM_GAIN_B",
           "IPV_LM_GAIN_LO", "IPV_LM_GAIN_HI", "IPV_LM_GAIN_FADE", "IPV_LM_GAIN_END",
           "IPV_LM_SIG", "IPV_LM2_SIG",
           "IPV_FRAMES", "IPV_FEXT", "IPV_FRANGE", "IPV_FRAME", "IPV_FIMGS", "IPV_FDEAD",
           "IPV_FMISSN", "IPV_FLOG", "IPV_FMAX", "IPV_FMISS_MAX",
           # Слой рото: состояние на верхнем уровне того же файла, его же сбрасывает
           # ipvOpen на каждом открытии клипа (и ищет ipvRoto).
           "IPV_ROTO", "IPV_ROTOSEQ", "IPV_ROTOACC", "IPV_ROTODRAWN")
FUNCS_OTHER = {
    JS60: ("pvSegAt",),
    JS00: ("esc", "fmtT", "isPhotoPath"),
    JS95: ("rgb2hex",),
}


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_ins_video_preview.py).

    `async` входит в вырезанный кусок: без него рендер кадра (`ipvRenderAt` и его
    помощники — асинхронные) не собрался бы вовсе.
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


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _decls(src, names):
    """Строки объявлений верхнего уровня (const/let/var) для перечисленных имён.

    Строка берётся целиком: на одной строке бывает несколько имён сразу
    (`const IPV_LM_HL=0.25, IPV_LM_SH=0.25, …`), и повтор её в стенде был бы
    повторным объявлением.
    """
    out, seen = [], set()
    for name in names:
        m = re.search(r"^(?:const|let|var)\s+[^\n]*\b%s\b[^\n]*" % re.escape(name), src, re.M)
        assert m, f"в исходнике не нашлось объявление {name}"
        line = m.group(0).strip()
        if line not in seen:
            seen.add(line)
            out.append(line)
    return "\n".join(out)


def _js(js85=None):
    """Настоящие тела функций: 85 — отрисовка кадра, остальные — общие хелперы."""
    src85 = js85 if js85 is not None else _read(JS85)
    out = [_decls(src85, DECLS85)]
    out += [_func(src85, n) for n in FUNCS85]
    for path, names in FUNCS_OTHER.items():
        src = _read(path)
        out += [_func(src, n) for n in names]
    return "\n".join(out)


# ---- мини-DOM: ровно то, что нужно покадровой отрисовке, и ничего лишнего ----
# Стенд исполняет боевые функции как есть, поэтому документ должен вести себя как
# настоящий там, где это важно для контракта: innerHTML разбирается в элементы
# (строки субтитров собираются строкой), classList живёт поверх className (его
# пишут и напрямую), у элементов есть размеры, а <video> умеет seek с событием.
_DOM_JS = r"""
var STAGE_W = %(w)d, STAGE_H = %(h)d;
var BY_ID = {};
function ClassList(el){this.el=el;}
ClassList.prototype._l=function(){return String(this.el.className||'').split(/\s+/).filter(Boolean);};
ClassList.prototype.contains=function(c){return this._l().indexOf(c)>=0;};
ClassList.prototype.add=function(c){if(!this.contains(c)){var l=this._l();l.push(c);this.el.className=l.join(' ');}};
ClassList.prototype.remove=function(c){this.el.className=this._l().filter(function(x){return x!==c;}).join(' ');};
ClassList.prototype.toggle=function(c,on){if(on===undefined)on=!this.contains(c);if(on)this.add(c);else this.remove(c);return !!on;};
function Style(){}
Style.prototype.setProperty=function(k,v){this[k]=String(v);};
Style.prototype.removeProperty=function(k){delete this[k];};
function El(tag){
  this.tag=tag;this.children=[];this.parentNode=null;this.id='';this._cls='';this.dataset={};
  this.style=new Style();this.classList=new ClassList(this);this._ev={};this._attrs={};this._text='';
  this._html='';this.scrollLeft=0;
  // Картинки в стенде «уже загружены»: ждать нечего, а ipvRenderAssets иначе ждал бы
  // события load, которого у мини-DOM не бывает (в браузере его шлёт загрузчик).
  this.complete=(tag==='img');
}
Object.defineProperty(El.prototype,'className',{get:function(){return this._cls;},
  set:function(v){this._cls=String(v);}});
Object.defineProperty(El.prototype,'firstChild',{get:function(){return this.children[0]||null;}});
Object.defineProperty(El.prototype,'childNodes',{get:function(){return this.children;}});
Object.defineProperty(El.prototype,'textContent',{
  get:function(){if(this.children.length){var s='';for(var i=0;i<this.children.length;i++)s+=this.children[i].textContent;return s;}
    return this._text;},
  set:function(v){this.children=[];this._text=String(v==null?'':v);}});
Object.defineProperty(El.prototype,'innerHTML',{
  get:function(){return this._html;},
  set:function(v){this._html=String(v);this._text='';this.children=[];
    var kids=parseHTML(String(v));for(var i=0;i<kids.length;i++)this.appendChild(kids[i]);}});
Object.defineProperty(El.prototype,'clientWidth',{get:function(){
  if(this.id==='ipvstage'||this.id==='ipvsub'||this.id==='ipvintro'||this.id==='ipvins')return STAGE_W;
  var m=/^([\d.]+)px$/.exec(String(this.style.width||''));return m?parseFloat(m[1]):0;}});
Object.defineProperty(El.prototype,'clientHeight',{get:function(){
  if(this.id==='ipvstage'||this.id==='ipvsub'||this.id==='ipvintro'||this.id==='ipvins')return STAGE_H;
  var m=/^([\d.]+)px$/.exec(String(this.style.height||''));return m?parseFloat(m[1]):0;}});
El.prototype.appendChild=function(c){if(!c)return c;c.parentNode=this;this.children.push(c);
  if(c.id)BY_ID[c.id]=c;return c;};
El.prototype.insertBefore=function(c,ref){c.parentNode=this;var i=this.children.indexOf(ref);
  if(i<0)this.children.push(c);else this.children.splice(i,0,c);if(c.id)BY_ID[c.id]=c;return c;};
El.prototype.removeChild=function(c){var i=this.children.indexOf(c);if(i>=0)this.children.splice(i,1);
  c.parentNode=null;return c;};
El.prototype.remove=function(){if(this.parentNode)this.parentNode.removeChild(this);};
El.prototype.setAttribute=function(n,v){if(n==='class')this.className=v;else if(n==='id'){this.id=String(v);BY_ID[this.id]=this;}
  else this._attrs[n]=String(v);};
El.prototype.getAttribute=function(n){if(n==='class')return this.className;if(n in this._attrs)return this._attrs[n];return null;};
El.prototype.removeAttribute=function(n){delete this._attrs[n];};
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.removeEventListener=function(n,f){var a=this._ev[n]||[];var i=a.indexOf(f);if(i>=0)a.splice(i,1);};
El.prototype.fire=function(n,ev){var a=(this._ev[n]||[]).slice();for(var i=0;i<a.length;i++)a[i](ev||{type:n});};
El.prototype.scrollIntoView=function(){};
El.prototype.focus=function(){};El.prototype.blur=function(){};
El.prototype.play=function(){this.paused=false;return {catch:function(){},then:function(){}};};
El.prototype.pause=function(){this.paused=true;};
El.prototype.closest=function(sel){var el=this;while(el){if(matchSel(el,sel))return el;el=el.parentNode;}return null;};
El.prototype.contains=function(el){while(el){if(el===this)return true;el=el.parentNode;}return false;};
El.prototype.getContext=function(){if(!this._c)this._c={ops:0,setTransform:function(){this.ops++;},
  clearRect:function(){this.ops++;},drawImage:function(src){this.ops++;this.last=src;},save:function(){},restore:function(){},
  filter:'none'};return this._c;};
El.prototype.decode=function(){return Promise.resolve();};
El.prototype.descendants=function(){var out=[];(function walk(el){for(var i=0;i<el.children.length;i++){
  out.push(el.children[i]);walk(el.children[i]);}})(this);return out;};
El.prototype.querySelectorAll=function(sel){
  return this.descendants().filter(function(el){return matchSel(el,sel);});};
El.prototype.querySelector=function(sel){var a=this.querySelectorAll(sel);return a.length?a[0]:null;};
// Размеры: у сцены — кадр ролика, у текста — по длине текста (стабильно и зависит от слов,
// как настоящая метрика шрифта; ширина плашки обязана меняться вместе с текстом).
// Слова строки стоят подряд, поэтому у каждого свой left — иначе «какая строка длиннее»
// в стенде не отличить: у всех слов был бы нулевой левый край.
El.prototype.getBoundingClientRect=function(){
  if(this.id==='ipvstage')return {left:0,top:0,right:STAGE_W,bottom:STAGE_H,
    width:STAGE_W,height:STAGE_H,x:0,y:0};
  var w=String(this.textContent||'').length*6;
  var left=0;
  if(this.parentNode){var sib=this.parentNode.children;
    for(var i=0;i<sib.length;i++){if(sib[i]===this)break;
      left+=String(sib[i].textContent||'').length*6+6;}}
  return {left:left,top:0,right:left+w,bottom:20,width:w,height:20,x:left,y:0};};
function matchSel(el,sel){
  // Поддерживаются ровно те формы, которые спрашивает отрисовка: `тег`, `.класс`,
  // `тег.класс`, `#id`, `[data-…="…"]` и потомок из двух таких частей через пробел
  // (`[data-ins="0"] video` — так видеовставка ищет своё <video> внутри обёртки).
  // Ведущая точка — это КЛАСС, а не имя тега (на этом стенд уже спотыкался:
  // `.pvcap_text` искался как тег).
  var s=String(sel).trim();
  if(s==='*')return true;
  var parts=s.split(/\s+/).filter(Boolean);
  if(parts.length>1){
    // Последняя часть — на самом элементе, предыдущие — на его предках (порядок как в CSS).
    if(!matchSel(el,parts[parts.length-1]))return false;
    var up=el.parentNode;
    for(var k=parts.length-2;k>=0;k--){
      var hit=null;
      for(;up;up=up.parentNode)if(matchSel(up,parts[k])){hit=up;break;}
      if(!hit)return false;
      up=hit.parentNode;}
    return true;}
  var am=/\[([\w-]+)="([^"]*)"\]/.exec(s);
  if(am){
    // `data-ins` в разметке лежит в dataset как `ins` — та же замена, что у парсера.
    var key=am[1].replace(/^data-/,'').replace(/-(\w)/g,function(_,c){return c.toUpperCase();});
    if(String(el.dataset[key]===undefined?'':el.dataset[key])!==am[2])return false;
    s=s.slice(0,am.index)+s.slice(am.index+am[0].length);}
  var im=/#([\w-]+)/.exec(s);
  if(im){if(el.id!==im[1])return false;
    s=s.slice(0,im.index)+s.slice(im.index+im[0].length);}
  var m=/^([\w-]*)((?:\.[\w-]+)*)$/.exec(s.trim());
  if(!m)return false;
  if(m[1]&&el.tag!==m[1])return false;
  var cls=m[2].split('.').filter(Boolean);
  for(var i=0;i<cls.length;i++)if(!el.classList.contains(cls[i]))return false;
  return true;}
function decodeEnt(s){return String(s).replace(/&amp;/g,'&').replace(/&quot;/g,'"')
  .replace(/&#39;/g,"'").replace(/&lt;/g,'<');}
function parseHTML(html){
  var root=new El('#frag');var stack=[root];var i=0;
  while(i<html.length){
    var lt=html.indexOf('<',i);
    if(lt<0){if(i<html.length)stack[stack.length-1].appendChild(textNode(html.slice(i)));break;}
    if(lt>i)stack[stack.length-1].appendChild(textNode(html.slice(i,lt)));
    var gt=html.indexOf('>',lt);
    if(gt<0)break;
    var tag=html.slice(lt+1,gt);
    if(tag.charAt(0)==='/'){if(stack.length>1)stack.pop();}
    else{
      var name=/^([a-zA-Z0-9]+)/.exec(tag)[1].toLowerCase();
      var el=new El(name);
      var re=/\s([\w-]+)="([^"]*)"/g,m;
      while((m=re.exec(tag))){
        var val=decodeEnt(m[2]);
        if(m[1]==='class')el.className=val;
        else if(m[1]==='style'){val.split(';').forEach(function(pair){
          var kv=pair.split(':');
          if(kv.length!==2)return;
          var name=kv[0].trim();
          // Имена свойств в браузере живут в camelCase (`font-size` -> fontSize), а
          // свои свойства (`--subfs`) остаются как есть — как в CSSStyleDeclaration.
          if(name.indexOf('--')!==0)name=name.replace(/-(\w)/g,function(_,c){return c.toUpperCase();});
          el.style[name]=kv[1].trim();});}
        else if(m[1].indexOf('data-')===0)el.dataset[m[1].slice(5).replace(/-(\w)/g,function(_,c){return c.toUpperCase();})]=val;
        else el.setAttribute(m[1],val);}
      stack[stack.length-1].appendChild(el);
      if(!/\/$/.test(tag))stack.push(el);
    }
    i=gt+1;}
  return root.children;}
function textNode(t){var n=new El('#text');n._text=t;return n;}
// Перемотка, «показанный» кадр и одновременность ожиданий — ручки стенда, а не поведение
// мини-DOM по умолчанию:
//   SEEK_MS    — сколько <video> «едет» до события `seeked` (0 — сразу, как в жизни на
//                прокси из одних ключевых кадров; отрицательное — `seeked` не придёт
//                вовсе: файл не открылся, и ждать остаётся только до потолка VFRAME_MS);
//   VFC_SILENT — requestVideoFrameCallback не зовёт колбэк НИКОГДА. Ровно так ведёт себя
//                <video> на паузе в Chrome без окна — из-за этого каждый кадр рендера
//                ждал полный VFRAME_MS (живой замер: seek 1.5–2.2 с на кадр);
//   VWAIT_MAX  — сколько ожиданий `seeked` висело ОДНОВРЕМЕННО (потолок за прогон).
//                Этим числом «ждут разом» и отличается от «ждут по очереди»: второй может
//                ждать уже отыгравшую перемотку и уложиться в ноль, и по времени разницы
//                не увидеть.
var SEEK_MS=0,VFC_SILENT=false,VWAIT_N=0,VWAIT_MAX=0;
function FakeVid(){
  El.call(this,'video');this.readyState=2;this.videoWidth=3840;this.videoHeight=2160;
  this._ct=0;this.seeking=false;this.paused=true;this.seeks=0;this.src='';this._w=0;
  this.requestVideoFrameCallback=function(cb){
    if(VFC_SILENT)return 1;                     // колбэк не придёт вовсе
    setTimeout(function(){cb(0,{});},0);return 1;};
  // Ожидание `seeked` (vSeekDone) считаем по постановке и снятию слушателя: это и есть
  // «сколько <video> ждут кадр прямо сейчас».
  var add=El.prototype.addEventListener,rem=El.prototype.removeEventListener;
  this.addEventListener=function(n,f){add.call(this,n,f);
    if(n==='seeked'){this._w++;VWAIT_N++;if(VWAIT_N>VWAIT_MAX)VWAIT_MAX=VWAIT_N;}};
  this.removeEventListener=function(n,f){rem.call(this,n,f);
    if(n==='seeked'&&this._w){this._w--;VWAIT_N--;}};}
FakeVid.prototype=Object.create(El.prototype);
Object.defineProperty(FakeVid.prototype,'currentTime',{
  get:function(){return this._ct;},
  set:function(v){this._ct=+v;this.seeks++;var self=this;
    if(this._seekT)clearTimeout(this._seekT);
    this.seeking=true;
    if(SEEK_MS<0)return;                        // `seeked` не придёт: ждём до потолка
    this._seekT=setTimeout(function(){self.seeking=false;self.fire('seeked');},SEEK_MS);}});
var STAGE=new El('div');STAGE.id='ipvstage';
var SUB=new El('div');SUB.id='ipvsub';SUB.className='pvsub';
var INTRO=new El('div');INTRO.id='ipvintro';INTRO.className='ipvintro';
// OVINS, а не INS: имя INS занято списком вставок шага 3 (80-inserts.js), и общая
// переменная сломала бы и оверлей, и список.
var OVINS=new El('div');OVINS.id='ipvins';OVINS.className='ipvins';
var TOPLINE=new El('div');TOPLINE.id='ipvtopline';TOPLINE.className='ipvtopline';
TOPLINE.appendChild(new El('div')).className='pvtl_track';
TOPLINE.appendChild(new El('div')).className='pvtl_prog';
var CAPTION=new El('div');CAPTION.id='ipvcaption';CAPTION.className='ipvcaption';
CAPTION.appendChild(new El('div')).className='pvcap_inner';
CAPTION.appendChild(new El('span')).className='pvcap_text';
var CAMCV=new El('canvas');CAMCV.id='ipvcam';CAMCV.width=0;CAMCV.height=0;
STAGE.appendChild(TOPLINE);STAGE.appendChild(CAPTION);STAGE.appendChild(INTRO);
STAGE.appendChild(OVINS);STAGE.appendChild(SUB);STAGE.appendChild(CAMCV);
BY_ID['ipvstage']=STAGE;BY_ID['ipvsub']=SUB;BY_ID['ipvintro']=INTRO;BY_ID['ipvins']=OVINS;
BY_ID['ipvtopline']=TOPLINE;BY_ID['ipvcaption']=CAPTION;BY_ID['ipvcam']=CAMCV;
// Ползунок и время превью: их трогает ipvUI на каждом кадре (в бою они есть и на
// странице рендера, спрятанные — без них падала бы отрисовка накладок).
var TIMEEL=new El('span');TIMEEL.id='ipvtime';BY_ID['ipvtime']=TIMEEL;
var SEEKEL=new El('input');SEEKEL.id='ipvseek';SEEKEL.value=0;BY_ID['ipvseek']=SEEKEL;
function $(id){return BY_ID[id]||null;}
var DOCROOT=new El('html');
var document={
  documentElement:DOCROOT,
  body:STAGE,
  head:new El('head'),
  createElement:function(tag){if(tag==='video')return new FakeVid();var el=new El(tag);return el;},
  getElementById:function(id){return BY_ID[id]||null;},
  querySelectorAll:function(sel){if(/\s/.test(sel))return [];return STAGE.querySelectorAll(sel);},
  addEventListener:function(){},removeEventListener:function(){},
  fonts:{load:function(){return Promise.resolve();},ready:Promise.resolve()},
  createRange:function(){return {_el:null,
    selectNodeContents:function(el){this._el=el;},
    getBoundingClientRect:function(){var w=this._el?String(this._el.textContent||'').length*6:0;
      return {left:0,top:0,right:w,bottom:20,width:w,height:20};}};}};
var window={devicePixelRatio:1,document:document,addEventListener:function(){}};
function requestAnimationFrame(cb){return setTimeout(function(){cb(0);},0);}
// --- заглушки того, что к отрисовке кадра не относится (музыка, дублёры, таймлайн, панели) ---
// Заглушка музыки названа по БОЕВОЙ двери предпросмотра (`musicElSync` в
// 85-inserts-view.js): имя musicSync занято в 95-styles.js (сброс закреплённого трека
// клипа), и стенд, оставшийся на старом имени, ронял отрисовку кадра ReferenceError'ом.
function musicElSync(){}function spareIdle(){}function camIdle(){}function itlEnsure(){}
function aewHighlight(){}function vgDuck(){}function introMarkPlaying(){}
function uiLog(){}function toast(){}function lutApply(ci,v){return v;}function lutKS(){return [1,1];}
// Переводчик: строки интерфейса стенду не нужны, ключ равен тексту (как в русском UI).
function t(s,vars){if(!vars)return s;var out=s;for(var k in vars)out=out.split('{'+k+'}').join(String(vars[k]));return out;}
// Активный ракурс выбирает camApply в 60-preview.js; стенду хватает того, что он делает
// для кадра: выставить IPV.curCi (холст рисует именно её).
function camApply(P,tm,play){P.curCi=0;P.vidx=0;}
var FONTS=[];var SFXFONTLOG={};var SFX_ELS={};var IPV_BODY=null;var IPVMODE='clips';
var MEDIA_VOL=1;var CLIPS=[];var INS=[];var curIns=-1;var curAE=-1;
var IPV={vids:[],bufs:[],segs:[],audio:[],words:[],dur:0,fps:60,aidx:0,vidx:-1,primed:-1,
  curCi:-1,rollCi:-1,playing:false,raf:0,xml:'',cur:-1,intro:[],introCur:-1,plan:null,
  insShift:null,insVids:new Map(),dims:new Map(),
  stats:{styk:0,swap:0,seek:0,cam:0,stale:0,back:0}};
"""

# План сцены: те же поля и в тех же единицах, что шлёт /api/scene. Окно появления
# жёлтого слова (t0=2.6, hd=0.35) — то самое «окно анимации», на котором проверяется,
# что разные моменты дают разные кадры.
_PLAN_JS = r"""
var PLAN={w:%(w)d,h:%(h)d,fps:%(fps)d,dur:20,
  layer_order:['subs','video','roto','photo','intro'],
  posy:1500,hl_step:120,sub_step:120,fsize:140,hl_rise:40,hl_dur:0.35,
  hl_blur:true,hl_blur_amt:8,sub_fill:[1,1,1],hl_fill:[0.96,0.77,0.09],
  sub_shadow:false,sub_scale:100,start_blur:0,start_blur_dur:0.52,
  ins_c2x:540,ins_c2y:330,
  sub_bg:{fill:[1,1,1],op:72,h:160,round:78,pad:18,padmin:70,dy:0,y:1500,anim:0.22},
  top_line:{w:600,h:12,th:12,y:80,dur:20,from:[1,1,0.5],to:[1,0.7,1],
            track_fill:[1,1,1],track_op:16},
  caption:{text:'подпись',x:100,y:200,size:26,fill:[1,1,1],bg:true,
           bg_fill:[0.3,0.3,0.3],bg_op:45,bg_round:68,kx:1.7,ky:2.4},
  shade:{x:0,y:-600,w:1080,h:800,scale:100,blur:60,op:70},
  zoom:{keys:[[0,100],[120,160],[300,100]],ease:null,cx:0.5,cy:0.5,pan:[0,0],rot:0,fit:100},
  subs:[
    {s:0.5,gend:2.5,row:0,w:'привет',fsize:140,sub_step:120,words:[{w:'привет',color:'white'}]},
    {s:2.5,gend:4.5,row:0,w:'жёлтое слово',fsize:140,sub_step:120,
     words:[{w:'жёлтое',color:'yellow',t0:2.6,hd:0.35},{w:'слово',color:'white'}]}
  ],
  inserts:[{t:'photo',media:'C:/ins/a.png',start:1,end:4,x:0,y:0,style:'cam2',scale:44,sc:100,
    card:{w:400,h:300,pw:1080,ph:810,plate:null,photo:null},
    anim:{opacity:[[1,0],[1.5,100],[3.5,100],[4,0]],
          scale:[[1,44],[1.5,100],[3.5,100],[4,44]],
          position:[[1,[540,960]],[4,[540,960]]]}}]
};
IPV.plan=PLAN;IPV.dur=20;IPV.fps=%(fps)d;
IPV.audio=[{ts:0,te:20,src:0}];
IPV.segs=[{ts:0,te:20,src:0,ci:0}];
IPV.intro=[];
IPV.vids=[new FakeVid()];
"""

# Снимок состояния накладок: то, что снимает браузер — текст, позиции, прозрачности.
# Счётчиков стенда (сколько раз <video> перематывали) здесь НЕТ нарочно: сравнение
# кадров идёт по КАРТИНКЕ, а число перемоток — свойство дороги, а не кадра, и в снимке
# оно ломало бы «одна t дважды — одинаковый кадр». Перемотки читает SEEKS().
_SNAP_JS = r"""
function SEEKS(){return IPV.vids.map(function(v){return v.seeks;});}
// Чем нарисован кадр камеры: холст запоминает источник последнего drawImage — по нему и
// видно, взяты пиксели из картинки или из <video> (ipvCamSrc).
function CAMSRC(){var c=CAMCV._c;if(!c||!c.last)return 'none';return c.last.tag||'?';}
function snapWr(wr){
  var el=wr.firstChild;if(!el)return null;
  return {cls:el.className,op:el.style.opacity||'',tf:el.style.transform||'',
          w:el.style.width||'',h:el.style.height||'',
          filter:el.style.filter||'',img:(function(){var i=el.querySelector('img');
            return i?(i.style.width+'x'+i.style.height):'';})()};}
function SNAP(){
  var words=SUB.querySelectorAll('.pvsubw_wd').map(function(w){
    return {t:w.textContent,op:w.style.opacity||'',top:w.style.top||'',
            pos:w.style.position||'',filter:w.style.filter||''};});
  var lines=SUB.querySelectorAll('.pvsubw').map(function(l){
    return {bottom:l.style.bottom||'',fs:l.style.fontSize||'',cls:l.className};});
  var bg=SUB.querySelector('.pvsub_bg');
  var tl=TOPLINE.querySelector('.pvtl_prog');
  var ins=OVINS.children.map(snapWr);
  return {t:IPV.renderT,words:words,lines:lines,subOp:SUB.style.opacity||'',
    bg:bg?{w:bg.style.width||'',trans:bg.style.transition||'',op:bg.style.opacity||'',
           h:bg.style.height||''}:null,
    tl:tl?(tl.style.width||''):null,tlDisp:TOPLINE.style.display||'',
    cap:CAPTION.style.display||'',capText:CAPTION.querySelector('.pvcap_text').textContent,
    shade:(function(){var s=$('ipvshade');return s?{tf:s.style.transform||'',op:s.style.opacity||''}:null;})(),
    ins:ins,rootCls:DOCROOT.className,
    rtFrames:(typeof IPV_RT!=='undefined')?IPV_RT.frames:0,
    camSrc:CAMSRC(),
    camPx:(function(){var c=CAMCV._c;if(!c)return 0;var n=c.ops;c.ops=0;return n;})()};
}
"""


def _stand(js85=None, extra=""):
    """Собрать программу стенда: боевые функции + мини-DOM + план + сценарий."""
    return (_js(js85) + "\n" + (_DOM_JS % {"w": W, "h": H})
            + "\n" + (_PLAN_JS % {"w": W, "h": H, "fps": FPS})
            + "\n" + _SNAP_JS + "\n" + extra)


def _run_node(code):
    """Прогнать стенд node'ом. Код — во временный файл, а не в `-e`: он весит больше
    ста килобайт, и командная строка Windows такого не принимает (WinError 206)."""
    with tempfile.TemporaryDirectory(prefix="webrender_stand_") as d:
        path = os.path.join(d, "stand.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        p = subprocess.run(["node", path], capture_output=True, text=True,
                           encoding="utf-8-sig", errors="replace", timeout=180, cwd=d)
    assert p.returncode == 0, (p.stderr or p.stdout)[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


_TWICE_JS = r"""
(async function(){
  // Сначала — ЖИВОЕ превью: ни одного кадра рендера ещё не рисовали, часы идут от
  // <video>. Так и работает интерфейс: класс render-mode на этой странице не появляется.
  ipvUI(3.0);
  var live=SNAP();
  await ipvRenderAt(3.0);
  var a=SNAP();
  await ipvRenderAt(3.0);
  var b=SNAP();
  await ipvRenderAt(2.62);          // окно появления жёлтого слова (t0=2.6, hd=0.35)
  var c=SNAP();
  await ipvRenderAt(3.0);
  var d=SNAP();
  console.log(JSON.stringify({a:a,b:b,c:c,d:d,live:live}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_frame_at_one_time_is_reproducible_and_differs_at_another():
    """1. Одна `t` дважды — одинаковый кадр; другая `t` в окне анимации — другой.

    Кадр рендера обязан быть функцией номера, а не «что успело доехать»: иначе
    собранный ролик дрожал бы на одном и том же месте от прогона к прогону.
    """
    out = _run_node(_stand(extra=_TWICE_JS))

    assert out["a"] == out["b"], \
        "одна и та же t дала разные кадры — рендер не воспроизводим"
    assert out["a"] == out["d"], \
        "возврат на прежнюю t после чужой дал другую картинку — между кадрами остаётся состояние"
    assert out["a"] != out["c"], \
        "разные моменты в окне анимации дали одинаковый кадр — анимация не считается вовсе"
    # Разница обязана быть именно в анимации слова, а не в чём-то постороннем.
    aw = {(w["t"], w["op"], w["top"]) for w in out["a"]["words"]}
    cw = {(w["t"], w["op"], w["top"]) for w in out["c"]["words"]}
    assert aw != cw, f"видимые слова не изменились: {out['a']['words']} / {out['c']['words']}"
    assert any(w["op"] not in ("", "1") for w in out["c"]["words"]), \
        f"в окне появления нет промежуточной прозрачности: {out['c']['words']}"
    assert out["a"]["camPx"] > 0, "холст камеры не перерисован ни разу"
    assert out["a"]["words"], "субтитры не нарисовались — проверять нечего"


@node
def test_render_mode_has_no_css_transitions_and_live_preview_keeps_them():
    """2. В режиме рендера переходов нет, в живом превью они на месте.

    Ширину плашки субтитров в живом превью везёт CSS-переход по реальному времени:
    снимок кадра поймал бы его середину и зависел бы от скорости съёмки. В рендере
    величину считает ipvSubsBgAt на момент кадра, а переход обязан быть снят.
    """
    out = _run_node(_stand(extra=_TWICE_JS))

    assert "render-mode" in out["a"]["rootCls"].split(), \
        f"на корне нет класса render-mode: {out['a']['rootCls']!r}"
    assert out["a"]["bg"] is not None, "плашки субтитров нет — проверять нечего"
    assert out["a"]["bg"]["trans"] in ("", "none"), \
        f"в режиме рендера у плашки остался переход: {out['a']['bg']['trans']!r}"
    # Живое превью — как раньше: тот же переход, что стоял до правки, и класс режима
    # рендера на его странице не появляется вовсе.
    assert out["live"]["bg"]["trans"].startswith("width "), \
        f"живое превью потеряло переход плашки: {out['live']['bg']['trans']!r}"
    assert "render-mode" not in out["live"]["rootCls"].split(), \
        f"класс render-mode появился в живом превью: {out['live']['rootCls']!r}"


@node
def test_plate_width_moves_with_the_plan_not_with_real_time():
    """3. Ширина плашки в рендере — величина на момент кадра (а не «как сложилось»).

    Плашка переезжает на новую ширину за sub_bg_anim (ease out quart — та же кривая,
    что в выражении .jsx). На кадре сразу после смены строки она обязана быть между
    прежней и новой шириной, а на дальнем кадре — равной новой: это и значит «считается
    на t», а не «доехала когда-то».
    """
    out = _run_node(_stand(extra=r"""
    (async function(){
      function w(t){return ipvSubFullW(ipvSubRawWAt(t),PLAN.sub_bg);}
      await ipvRenderAt(2.30);            // строка «привет» (короткая)
      var first=SNAP();
      await ipvRenderAt(2.55);            // строка сменилась: плашка поехала
      var mid=SNAP();
      await ipvRenderAt(4.00);            // далеко за концом перехода
      var late=SNAP();
      console.log(JSON.stringify({first:first,mid:mid,late:late,
        rawPrev:w(2.30),rawCur:w(2.55),rawLate:w(4.0)}));
    })().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
    """))

    def px(s):
        return float(s["bg"]["w"].replace("cqw", ""))

    assert out["rawLate"] > out["rawPrev"], \
        "новая строка не длиннее прежней — тест ничего не проверяет"
    assert px(out["first"]) < px(out["late"]), \
        f"плашка не выросла вместе со строкой: {out['first']['bg']['w']} / {out['late']['bg']['w']}"
    # В середине перехода ширина строго между прежней и новой — это и есть «на момент t».
    mid = px(out["mid"])
    assert px(out["first"]) < mid < px(out["late"]), \
        f"на кадре перехода ширина не промежуточная: {mid} (было {px(out['first'])}, стало {px(out['late'])})"


@node
def test_mutation_broken_transition_is_caught():
    """4. Мутация: вернуть переход плашки в режиме рендера — стенд обязан покраснеть.

    Проверка не «на словах»: тот же стенд собирается из ИСПОРЧЕННОГО исходника
    (переход плашки ставится безусловно), и тест требует, чтобы проверка из него
    вышла красной. Так сторож не сможет молча перестать ловить регресс.
    """
    src = _read(JS85)
    marker = "bgEl.style.transition=ipvRenderMode()?'none':('width '+bgAnim+'s cubic-bezier(.165,.84,.44,1)');"
    assert marker in src, "строка перехода плашки изменилась — мутация устарела"
    broken = src.replace(
        marker, "bgEl.style.transition='width '+bgAnim+'s cubic-bezier(.165,.84,.44,1)';")
    out = _run_node(_stand(js85=broken, extra=_TWICE_JS))
    trans = out["a"]["bg"]["trans"]
    assert trans.startswith("width "), (
        "мутация не поймана: с возвращённым переходом плашки стенд остался зелёным "
        f"(transition={trans!r})")


# --------------------------------------------------------------------------- #
# Кадр за кадром: когда <video> перематывается, когда его ждут и как ждут
# --------------------------------------------------------------------------- #
# Кадры рендера идут шагом 1/fps, то есть ровно по кадрам исходника. Ждать «показанный»
# кадр (requestVideoFrameCallback) нечего: после `seeked` кадр уже отдан декодером, а
# камеру рисует холст. Ждать несколько <video> по очереди нельзя — это их сумма, а не
# максимум. И повторять перемотку там, где кадр уже стоит на месте, незачем; соседний же
# кадр — ДРУГОЙ кадр, и <video> на паузе само на него не перейдёт. Сцена при этом
# выставляется на tm на КАЖДОМ кадре: субтитры считает ipvUI, и «кадр уже на месте» не
# должно значить «кадр не считается».

# Потолок, за которым ожидание уже не «быстро»: у владельца кадр стоил 1.5–2.2 с.
FRAME_WAIT_MS = 50
# Стенд «молчащего» кадра: requestVideoFrameCallback не зовёт колбэк НИКОГДА — ровно так
# ведёт себя <video> на паузе в Chrome без окна, а `seeked` приходит сразу (прокси из одних
# ключевых кадров). Со старым ожиданием показанного кадра каждый кадр упирался в полный
# VFRAME_MS: здесь это видно числом, а не на глаз.
_SILENT_VFC_JS = r"""
(async function(){
  VFC_SILENT=true;
  var fresh=new FakeVid();
  IPV.renderT=3.0;                       // режим рендера — его же ставит и ipvRenderAt
  var t0=performance.now();
  await vFrameAt(fresh,3.0);             // время ставится (0 -> 3), дальше ждём `seeked`
  var direct=performance.now()-t0;
  var seeks=fresh.seeks;
  var t1=performance.now();
  await ipvRenderAt(3.0+1/PLAN.fps);     // боевая дверь кадра: соседний кадр, перемотка нужна
  var frame=performance.now()-t1;
  console.log(JSON.stringify({direct:direct,frame:frame,seeks:seeks,
    camSeeks:IPV.vids[0].seeks,rtFrames:IPV_RT.frames}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_render_frame_does_not_wait_for_the_presented_frame():
    """5. Кадр не ждёт `requestVideoFrameCallback` — и напрямую, и боевой дверью.

    Живой замер владельца: `seek` 1.5–2.2 с на кадр при `VFRAME_MS=1500`, то есть каждый
    кадр ждал полный таймаут — колбэк у <video> на паузе в Chrome без окна не приходит
    вовсе. Ждать нечего: после `seeked` кадр уже отдан декодером и годен для drawImage, а
    камеру рисует холст (ipvCamPaint), а не показ самого <video>.
    """
    out = _run_node(_stand(extra=_SILENT_VFC_JS))

    assert out["seeks"] >= 1, \
        "стенд не перемотал <video> — ждать было нечего, и тест ничего не проверяет"
    assert out["camSeeks"] >= 1 and out["rtFrames"] == 1, out
    assert out["direct"] < FRAME_WAIT_MS, (
        f"vFrameAt на молчащем requestVideoFrameCallback занял {out['direct']:.0f} мс — "
        f"кадр ждёт «показанного» кадра (потолок VFRAME_MS)")
    assert out["frame"] < FRAME_WAIT_MS, (
        f"кадр рендера собрался за {out['frame']:.0f} мс при молчащем "
        "requestVideoFrameCallback — ожидание показанного кадра вернулось")


@node
def test_mutation_waiting_for_the_presented_frame_again_is_caught():
    """6. Мутация: вернуть ожидание показанного кадра с потолком VFRAME_MS — стенд краснеет.

    Проверка не «на словах»: стенд собирается из ИСПОРЧЕННОГО исходника (потолок ожидания
    показанного кадра берётся безусловно), и тест требует, чтобы проверка выше вышла
    красной. Так сторож не сможет молча перестать ловить регресс.
    """
    src = _read(JS85)
    marker = "const ms=ipvRenderMode()?0:VFRAME_MS;"
    assert marker in src, "строка ожидания показанного кадра изменилась — мутация устарела"
    broken = src.replace(marker, "const ms=VFRAME_MS;")
    out = _run_node(_stand(js85=broken, extra=_SILENT_VFC_JS))
    assert out["direct"] > FRAME_WAIT_MS, (
        "мутация не поймана: с ожиданием показанного кадра vFrameAt всё равно собрался за "
        f"{out['direct']:.0f} мс")


# --------------------------------------------------------------------------- #
# Кадры камер картинками: выемка ДО съёмки и отрисовка из картинки
# --------------------------------------------------------------------------- #
# Замер владельца: перемотка <video> — 90 мс из 225 на кадр. Поэтому перед съёмкой куска
# кадры вынимаются ffmpeg'ом (core/webrender.capture_frames), а страница рисует камеру из
# картинки по номеру ИСХОДНОГО кадра — той же отрисовкой (ipvCamPaint), другим источником
# пикселей (ipvCamSrc). Проверок тут четыре, и все они про одно: кадр не должен уехать.
#
# EDL для сверки правила: обе камеры, куски подряд — есть и склейки камер, и стыки кусков.
# У первого куска src=0 и ts=0 нарочно: только на нём время источника считается без
# смещений, и в наборе времён появляется РОВНАЯ половина кадра — место, где округление
# «floor(x+0.5)» и питоновский round(0.5)=0 разошлись бы на кадр.
RULE_SEGS = [
    {"ci": 0, "ts": 0.0, "te": 1.5, "src": 0.0},
    {"ci": 1, "ts": 1.5, "te": 2.25, "src": 4.0},
    {"ci": 0, "ts": 2.25, "te": 3.0, "src": 13.75},
    {"ci": 1, "ts": 3.0, "te": 5.0, "src": 8.0},
    {"ci": 0, "ts": 5.0, "te": 6.4, "src": 0.0},
]
RULE_FPS = 60
RULE_CHUNKS = [0, 96, 192, 288, 384]      # кадры начала кусков рендера (4 экземпляра)


def _rule_times():
    """Времена сверки: границы кусков EDL, склейки камер и стыки кусков рендера.

    Круглые времена тут не годятся: расходятся как раз границы — выбор куска (`_seg_at`)
    и исходное время камеры (`_cam_time_at`) считаются по ним. Времена берутся как есть,
    без округления: полукадр нужен РОВНЫЙ, иначе округление не проверить.
    """
    step = 1.0 / RULE_FPS
    end = float(RULE_SEGS[-1]["te"])
    times = set()
    for s in RULE_SEGS:
        for edge in (float(s["ts"]), float(s["te"])):
            for d in (-1.0, -0.5, 0.0, 0.5, 1.0):
                t = edge + d * step
                if 0.0 <= t <= end:
                    times.add(t)
    for c in RULE_CHUNKS:
        t = c / RULE_FPS
        if t <= end:
            times.add(t)
            times.add(t + 0.5 * step)
    return sorted(times)


def _rule_src_time(t):
    """Время источника первого куска EDL на момент t — то, что округляет правило."""
    s = RULE_SEGS[0]
    return float(s["src"]) + max(0.0, t - float(s["ts"]))


_RULE_JS = r"""
IPV.segs=%(segs)s;IPV.fps=%(fps)d;IPV.dur=%(dur)s;
var TIMES=%(times)s;
console.log(JSON.stringify(TIMES.map(function(tm){
  var r=ipvSrcFrameAt(tm);return r?[r.ci,r.f]:null;})));
"""


def _rule_page(times, js85=None):
    """Что отвечает СТРАНИЦА на эти времена: боевые функции файла в node."""
    return _run_node(_stand(js85=js85, extra=_RULE_JS % {
        "segs": json.dumps(RULE_SEGS, ensure_ascii=False), "fps": RULE_FPS,
        "dur": RULE_SEGS[-1]["te"], "times": json.dumps(times)}))


def _rule_extraction(times):
    """Что отвечает ВЫЕМКА на те же времена: то же правило, но своё (Python)."""
    import core.webrender as wr

    return [[r[0], r[1]] if r else None
            for r in (wr.src_frame_at(RULE_SEGS, RULE_FPS, t) for t in times)]


@node
def test_frame_rule_is_one_for_extraction_and_for_the_page():
    """Правило «время → кадр»: выемка и страница дают ОДИН номер на всех временах.

    Разойдись они на кадр — на страницу приедет ДРУГАЯ картинка, и увидеть это можно
    только глазами по готовому ролику. Проверка идёт на границах: стыки кусков EDL,
    склейки камер (тут меняется и камера, и источник времени) и стыки кусков рендера.
    """
    import math

    times = _rule_times()
    page, extract = _rule_page(times), _rule_extraction(times)

    assert page == extract, "\n".join(
        f"{t}: страница {a}, выемка {b}" for t, a, b in zip(times, page, extract)
        if a != b)
    # Проверка не пустая: камер две, номера кадров разные.
    assert {None if not r else r[0] for r in page} == {0, 1}, page[:5]
    assert len({r[1] for r in page if r}) > 20, page[:5]
    # И на РОВНОЙ половине кадра стороны тоже сходятся — а это то самое место, где
    # питоновский round(0.5)=0 разошёлся бы с JS Math.round на целый кадр.
    half = [t for t in times if abs(abs(_rule_src_time(t) * RULE_FPS) % 1 - 0.5) < 1e-12]
    assert half, "в наборе времён нет ровной половины кадра — округление не проверить"
    t = half[0]
    x = _rule_src_time(t) * RULE_FPS
    assert int(math.floor(x + 0.5)) != round(x), (
        f"половина кадра на {t} не различает floor(x+0.5) и round — проверять нечего")
    assert page[times.index(t)] == extract[times.index(t)] == [0, int(math.floor(x + 0.5))]


@node
def test_mutation_shifted_frame_number_is_caught():
    """Мутация: сдвинуть номер кадра на 1 — сверка выемки со страницей обязана покраснеть.

    Проверка не «на словах»: стенд собирается из ИСПОРЧЕННОГО исходника (номер кадра
    страницы на единицу больше), и тест требует, чтобы сверка выше вышла красной. Так
    сторож не сможет молча перестать ловить расхождение на кадр.
    """
    src = _read(JS85)
    marker = "return {ci:(+s.ci||0),f:Math.floor(tsrc*(IPV.fps||60)+0.5)};}"
    assert marker in src, "правило «время → кадр» изменилось — мутация устарела"
    broken = src.replace(
        marker, "return {ci:(+s.ci||0),f:Math.floor(tsrc*(IPV.fps||60)+0.5)+1};}")

    times = _rule_times()
    page, extract = _rule_page(times, js85=broken), _rule_extraction(times)
    wrong = [t for t, a, b in zip(times, page, extract) if a != b]
    assert wrong, "мутация не поймана: сдвиг номера кадра на 1 сверка не заметила"
    assert len(wrong) > len(times) * 0.8, (len(wrong), len(times))


# Стенд кадра с картинками: <img> загружается (или нет — IMG_FAIL), холст запоминает,
# чем нарисован кадр (CAMSRC). Здесь видно сразу четыре вещи: с картинкой <video> НЕ
# перематывается, пропажа ОДНОГО кадра откатывает только его (камера продолжает брать
# картинки), а без картинок вовсе — кадры идут перемоткой и в лог уходит одна строка.
# `fetch` подменён: причину пропажи страница спрашивает у сервера HEAD-запросом, и в
# стенде без подмены это был бы настоящий запрос из node (ответ зависел бы от сети).
_IMAGES_JS = r"""
(async function(){
  var loaded=[],errs=[];
  var realCreate=document.createElement,realErr=console.error;
  IMG_FAIL=false;
  fetch=function(u,o){return Promise.resolve({status:404});};
  document.createElement=function(tag){var el=realCreate(tag);
    if(tag==='img'){el.naturalWidth=1080;el.naturalHeight=1920;
      el.decode=function(){if(IMG_FAIL)return Promise.reject(new Error('нет картинки'));
        loaded.push(el.src);return Promise.resolve();};}
    return el;};
  console.error=function(m){errs.push(String(m));};
  IPV_FRAMES='C:/clip/_tmp/webrender_1_2/frames_0';
  function shot(){return {seeks:SEEKS(),src:CAMSRC(),
    fr:IPV_FRAME?[IPV_FRAME.ci,IPV_FRAME.f]:null,frames:IPV_RT.frames,loaded:loaded.length,
    imgs:IPV_FIMGS.size,errs:errs.length};}
  await ipvRenderAt(3.0);
  var a=shot();
  await ipvRenderAt(3.0+1/PLAN.fps);         // соседний кадр — другой файл картинки
  var b=shot();
  IMG_FAIL=true;                             // НЕ пришла одна картинка (кадр 360)
  await ipvRenderAt(6.0);
  var c=shot();
  IMG_FAIL=false;
  await ipvRenderAt(6.0+1/PLAN.fps);         // следующая картинка снова доехала
  var d=shot();
  // Картинок нет ВООБЩЕ: папка не приехала. Камера уходит на перемотку после
  // IPV_FMISS_MAX пропаж ПОДРЯД, и дальше картинки уже не запрашиваются вовсе.
  IPV_FDEAD.clear();IPV_FLOG.clear();IPV_FMISSN.clear();IPV_FRAME=null;IPV_FIMGS.clear();
  IPV_FRAMES='C:/clip/_tmp/webrender_1_2/нет-такой-папки';IMG_FAIL=true;
  for(var i=0;i<IPV_FMISS_MAX;i++)await ipvRenderAt(9.0+i/PLAN.fps);
  var e=shot();
  await ipvRenderAt(9.0+IPV_FMISS_MAX/PLAN.fps);
  var f=shot();
  console.error=realErr;
  console.log(JSON.stringify({a:a,b:b,c:c,d:d,e:e,f:f,errs:errs.length,msgs:errs}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_render_takes_the_camera_from_the_image_and_falls_back_to_video():
    """Кадр камеры — из картинки; картинки нет — перемотка `<video>` и строка в лог.

    Главное здесь — что с картинкой `<video>` не перематывается ВООБЩЕ (`currentTime`
    не трогается ни на одном кадре): именно перемотка и была самой дорогой долей кадра.
    Дальше — откаты: пропал ОДИН кадр, его и перематываем, а камера продолжает брать
    картинки; картинок нет вовсе (папка не приехала) — все кадры идут перемоткой, и об
    этом одна строка, а не по строке на кадр.
    """
    out = _run_node(_stand(extra=_IMAGES_JS))

    # 1) Картинка есть: холст рисует ЕЁ, а <video> не перематывается ни разу.
    assert out["a"]["src"] == "img", out["a"]
    assert out["a"]["fr"] == [0, 180], out["a"]
    assert out["a"]["seeks"] == [0], f"<video> перемотали при живой картинке: {out['a']}"
    assert out["a"]["frames"] == 1, out["a"]
    # 2) Соседний кадр — другой номер и другой файл: правило считает кадр, а не «как сложилось».
    assert out["b"]["src"] == "img" and out["b"]["fr"] == [0, 181], out["b"]
    assert out["b"]["seeks"] == [0], f"<video> перемотали на соседнем кадре: {out['b']}"
    assert out["b"]["loaded"] > out["a"]["loaded"], (out["a"], out["b"])
    # 3) Не пришла ОДНА картинка — перемоткой идёт только этот кадр.
    assert out["c"]["src"] == "video" and out["c"]["fr"] is None, out["c"]
    assert out["c"]["seeks"][0] >= 1, f"отката на перемотку не было: {out['c']}"
    assert out["c"]["errs"] == out["b"]["errs"] + 1, out["c"]
    assert "перемоткой" in out["msgs"][0], out["msgs"]
    # 4) Следующий кадр снова из картинки: одна пропажа камеру не выключает.
    assert out["d"]["src"] == "img" and out["d"]["seeks"] == out["c"]["seeks"], out["d"]
    # 5) Картинок нет вовсе: камера уходит на перемотку после IPV_FMISS_MAX пропаж подряд,
    #    и новых попыток загрузить после этого уже нет (иначе лог и сеть забиты мусором).
    assert out["e"]["src"] == "video" and out["e"]["seeks"][0] > out["d"]["seeks"][0], out["e"]
    assert out["f"]["src"] == "video" and out["f"]["seeks"][0] > out["e"]["seeks"][0], out["f"]
    assert out["f"]["imgs"] == out["e"]["imgs"], (
        f"картинки запрашиваются снова, хотя их нет вовсе: {out['e']} -> {out['f']}")
    assert out["f"]["errs"] == out["e"]["errs"], out["msgs"]   # вторая попытка не логируется


# Стенд причины: ПЕРВАЯ картинка камеры не пришла — ровно то, что случилось на живом
# прогоне (камера сменилась на 150-м кадре пятисекундного куска, картинки её первого кадра
# не оказалось, и камера ушла на перемотку `<video>` до конца куска). Проверяется, что
# такой сбой стоит ОДНОГО кадра, и что в лог уходит ПРИЧИНА пропажи, а не только факт.
_FIRST_MISS_JS = r"""
(async function(){
  var errs=[],urls=[],probe=[],STATUS=404,BADQ=[];
  var realCreate=document.createElement,realErr=console.error;
  function isBad(v){for(var i=0;i<BADQ.length;i++)if(v.indexOf(BADQ[i])>=0)return true;
    return false;}
  fetch=function(u,o){probe.push([String(u),(o&&o.method)||'GET']);
    return Promise.resolve({status:STATUS});};
  document.createElement=function(tag){var el=realCreate(tag);
    if(tag!=='img')return el;
    el.naturalWidth=1080;el.naturalHeight=1920;el._ok=true;
    Object.defineProperty(el,'src',{get:function(){return el._src||'';},set:function(v){
      el._src=v;urls.push(v);
      if(isBad(v)){el._ok=false;setTimeout(function(){el.fire('error');},0);}}});
    el.decode=function(){return el._ok?Promise.resolve()
      :Promise.reject(new Error('нет картинки'));};
    return el;};
  console.error=function(m){errs.push(String(m));};
  IPV_FRAMES='C:/clip/_tmp/webrender_1_2/frames_0';
  IPV_FRANGE=null;
  function shot(){return {src:CAMSRC(),fr:IPV_FRAME?[IPV_FRAME.ci,IPV_FRAME.f]:null,
    urls:urls.length,imgs:IPV_FIMGS.size,miss:(IPV_FMISSN.get(0)||0),dead:IPV_FDEAD.size,
    errs:errs.length,probe:probe.length,msgs:errs.slice()};}
  function tick(){return new Promise(function(r){setTimeout(r,2);});}
  // Картинку кадра страница грузит ЗАРАНЕЕ — на кадр вперёд, поэтому «не пришла картинка
  // кадра N» задаётся до отрисовки кадра N-1. Ровно так это и работает в бою.
  BADQ.push('c0_180.jpg');                   // первая же картинка камеры не пришла
  await ipvRenderAt(3.0);
  await tick();
  var a=shot();
  BADQ.push('c0_182.jpg');
  await ipvRenderAt(3.0+1/PLAN.fps);         // 181 доехал, 182 — нет
  await tick();
  var b=shot();
  BADQ.push('c0_183.jpg');STATUS=200;IPV_FLOG.clear();
  await ipvRenderAt(3.0+2/PLAN.fps);         // 182 не пришёл, 183 — тоже (причина другая)
  await tick();
  var c=shot();
  BADQ.push('c0_184.jpg');
  await ipvRenderAt(3.0+3/PLAN.fps);         // третья пропажа ПОДРЯД — камера уходит
  await tick();
  var d=shot();
  await ipvRenderAt(3.0+4/PLAN.fps);         // и больше картинок не просит
  await tick();
  var e=shot();
  console.error=realErr;
  console.log(JSON.stringify({a:a,b:b,c:c,d:d,e:e,msgs:errs,probe:probe.length}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_first_missing_image_costs_one_frame_not_the_whole_chunk():
    """Пропала ПЕРВАЯ картинка камеры — перемоткой идёт один кадр, а не весь кусок.

    Живой прогон (20 с, куски по 300 кадров): у четвёртого экземпляра не пришла картинка
    первого кадра камеры, сменившейся на 150-м кадре куска, — и камера была помечена «без
    картинок» до конца куска. 149 кадров пошли перемоткой `<video>` (seek 94 мс вместо 22),
    и выигрыша от картинок на длинном отрезке не осталось вовсе; на куске в 75 кадров
    (прогон на 5 секундах) то же самое стоило одного кадра и видно не было.

    Здесь проверяется и обратное: сбой не «накапливается» — следующая картинка снова
    берётся из файла, — и что строка в логе называет ПРИЧИНУ (её даёт HEAD по адресу
    картинки), а не только факт пропажи.
    """
    out = _run_node(_stand(extra=_FIRST_MISS_JS))

    # 1) Кадр с пропавшей картинкой — перемоткой, но камера НЕ выключена.
    assert out["a"]["src"] == "video" and out["a"]["fr"] is None, out["a"]
    assert out["a"]["dead"] == 0, f"одна пропажа выключила камеру до конца куска: {out['a']}"
    assert out["a"]["miss"] == 1, out["a"]
    # 2) Следующий кадр снова из картинки — вот этого и не было в живом прогоне:
    #    там первая же пропажа помечала камеру «без картинок» до конца куска.
    assert out["b"]["src"] == "img", f"камера осталась без картинок: {out['b']}"
    assert out["b"]["fr"] == [0, 181], out["b"]
    # 3) Вторая пропажа подряд — по-прежнему не приговор, но и НЕ вторая строка в лог.
    assert out["c"]["src"] == "video" and out["c"]["dead"] == 0, out["c"]
    assert out["c"]["errs"] == 2, f"на камеру больше строк, чем пропаж: {out['msgs']}"
    # 4) Третья пропажа подряд — камера уходит на перемотку, и картинки больше не просит:
    #    пропавшая папка не должна стоить трёхсот запросов и трёхсот строк в консоли.
    assert out["d"]["dead"] == 1, f"камера так и не ушла на перемотку: {out['d']}"
    assert out["e"]["imgs"] == out["d"]["imgs"], (out["d"], out["e"])
    assert out["e"]["errs"] == out["d"]["errs"], out["msgs"]
    # 5) Причина названа: 404 — файла нет; 200 — файл есть, а до браузера не доехал.
    assert "404" in out["msgs"][0] and "перемоткой" in out["msgs"][0], out["msgs"]
    assert "200" in out["msgs"][1], out["msgs"]
    # Причина спрашивается ОДИН раз на строку и тем же запросом, что и картинка: HEAD.
    assert out["probe"] == 2, f"причину спрашивают лишний раз: {out['probe']}"


@node
def test_frame_prime_does_not_ask_beyond_the_chunk():
    """Примерка кадра не просит картинку за последним кадром куска — её там нет.

    Картинку следующего кадра страница грузит заранее, и на последнем кадре куска эта
    просьба уходила в СОСЕДНИЙ кусок: файла для такого кадра в папке нет (его вынимает
    другой экземпляр), сервер отвечает 404. На длинном куске (300 кадров) каждая такая
    пропажа уводила камеру на перемотку до конца куска — границы куска (fstart/fcount,
    core/webrender._chunk_url) и есть то, что её останавливает.
    """
    out = _run_node(_stand(extra=r"""
    (async function(){
      var urls=[];
      var realCreate=document.createElement;
      document.createElement=function(tag){var el=realCreate(tag);
        if(tag!=='img')return el;
        el.naturalWidth=1080;el.naturalHeight=1920;
        Object.defineProperty(el,'src',{get:function(){return el._src||'';},
          set:function(v){el._src=v;urls.push(v);}});
        el.decode=function(){return Promise.resolve();};
        return el;};
      IPV_FRAMES='C:/clip/_tmp/webrender_1_2/frames_0';
      IPV_FRANGE=null;                       // границ нет: примерка идёт за конец куска
      await ipvRenderAt(3.0);
      var without=urls.slice();
      urls.length=0;IPV_FIMGS.clear();IPV_FRAME=null;
      IPV_FRANGE=[180,1];                    // кусок — ровно один кадр 180
      await ipvRenderAt(3.0);
      var inside=urls.slice();
      console.log(JSON.stringify({without:without,inside:inside}));
    })().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
    """))
    import urllib.parse

    def names(urls):
        # В кадре стенда есть ещё фото-вставка (PLAN.inserts): её картинка к папке кадров
        # куска отношения не имеет — считаем только адреса из неё.
        return [os.path.basename(urllib.parse.unquote(u.split("path=", 1)[1]))
                for u in urls if "frames_0" in u]

    # Без границ: свой кадр и примерка следующего — тот самый кадр соседнего куска.
    assert names(out["without"]) == ["c0_180.jpg", "c0_181.jpg"], out["without"]
    # С границами куска примерка за его конец не идёт вовсе.
    assert names(out["inside"]) == ["c0_180.jpg"], (
        f"примерка ушла за последний кадр куска: {out['inside']}")


@node
def test_frame_url_points_at_the_source_frame_file():
    """Адрес картинки: папка куска и имя `c<камера>_<кадр>.<расширение>` — как их кладёт выемка.

    Расширение приезжает странице в адресе куска (`fext`), и здесь оно берётся у САМОЙ
    выемки (`webrender.FRAME_EXT`): разойдись они — страница просила бы несуществующие
    файлы, а 404 уводил бы камеру на перемотку `<video>` до конца куска.
    """
    import urllib.parse

    import core.webrender as wr

    ext = wr.FRAME_EXT.lstrip(".")
    code = ("let IPV_FRAMES='C:/clip/_tmp/frames_0';let IPV_FEXT=%s;\n" % json.dumps(ext)
            + _func(_read(JS85), "ipvFrameURL")
            + "\nconsole.log(JSON.stringify([ipvFrameURL(0,180),ipvFrameURL(1,7)]));")
    urls = _run_node(code)
    got = [os.path.basename(urllib.parse.unquote(u.split("path=", 1)[1])) for u in urls]
    assert got == ["c0_180.%s" % ext, "c1_7.%s" % ext], got
    assert [os.path.basename(wr._frame_path("C:/clip/_tmp/frames_0", ci, f))
            for ci, f in ((0, 180), (1, 7))] == got, got


def test_chunk_url_carries_the_frame_range_of_the_chunk(tmp_path):
    """В адресе куска, кроме папки картинок, — ГРАНИЦЫ его кадров: fstart и fcount.

    По ним страница не грузит картинку за последним кадром куска: там кадр соседнего
    куска, и в этой папке его нет — просьба о нём получала бы 404, а пропажа картинки
    уводила камеру на перемотку `<video>` до конца куска (см. `_chunk_url`).

    Папку собирает `os.path.join` сборки, поэтому путь берём от `tmp_path`: в бою он
    АБСОЛЮТНЫЙ на своей ОС, а жёстко прописанный «C:/…» на Linux уезжал бы в `/src/C:/…`
    (`_url_path` зовёт `abspath`).
    """
    import urllib.parse

    import core.webrender as wr

    base = "http://127.0.0.1:5001/render?xml=x.xml&body=abc&pxh=1080"
    frame_dir = str(tmp_path / "_tmp" / "frames_3")
    url = wr._chunk_url(base, frame_dir, 900, 300)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    want = wr._url_path(frame_dir)
    assert q["frames"] == [want], q
    # Слэши — прямые (страница клеит имя файла через «/»), обратных в адресе нет.
    assert "\\" not in q["frames"][0] and "/" in q["frames"][0], q
    assert q["fstart"] == ["900"] and q["fcount"] == ["300"], q
    # Папки нет — картинок нет: адрес не трогаем и пустых значений в него не подставляем.
    assert wr._chunk_url(base, "", 900, 300) == base, url


def test_render_page_reads_the_chunk_range_from_the_url(client):
    """Страница рендера читает из адреса те же имена, что шлёт Python: frames/fstart/fcount.

    Имена параметров — контракт двух сторон, как у кадра с картинкой: разойтись они могут
    молча (страница просто ничего не найдёт и будет просить картинку за концом куска),
    поэтому сверяются с константами сборщика, а не с копией строк в тесте.
    """
    import core.webrender as wr

    html = _served_render_page(client)
    for name in (wr.FRAMES_PARAM, wr.FRAME_START_PARAM, wr.FRAME_COUNT_PARAM):
        assert "q.get('%s')" % name in html, f"страница не читает «{name}» из адреса"
    assert "IPV_FRANGE=" in html, "страница не выставляет границы куска"


FFMPEG = shutil.which("ffmpeg")
live_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="выемка картинок требует ffmpeg в PATH")


def _pattern_video(path, frames=120, fps=30):
    """Источник, у которого КАЖДЫЙ кадр узнаётся по яркости: номер кадра = `N*17 % 200`.

    Шаг 17 взаимно прост с 200, поэтому яркости разных кадров не повторяются (это и
    проверяет тест) — по ним видно, ТОТ ли кадр вынулся, а не «какой-то». Значения
    держатся в 16..235: полный диапазон mp4 их не срежет.
    """
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
           "color=c=black:s=64x64:r=%d:d=%g" % (fps, frames / fps),
           "-vf", "geq=lum='16+mod(N*17,200)':cb=128:cr=128",
           "-c:v", "libx264", "-g", "1", "-bf", "0", "-keyint_min", "1",
           "-crf", "18", "-pix_fmt", "yuv420p", str(path)]
    p = subprocess.run(cmd, capture_output=True, timeout=180)
    assert p.returncode == 0, p.stderr[-800:]
    return str(path)


def _luma_profile(src):
    """Яркость КАЖДОГО кадра файла — по одному байту на кадр (серый 1x1)."""
    p = subprocess.run([FFMPEG, "-v", "error", "-i", src, "-vf", "scale=1:1",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, timeout=180)
    assert p.returncode == 0, p.stderr[-800:]
    return list(p.stdout)


def _luma_one(path):
    """Яркость одной картинки — тем же путём, что у профиля кадров."""
    return _luma_profile(path)[0]


@live_ffmpeg
def test_extraction_lands_on_the_source_frame_the_rule_asks_for(tmp_path):
    """Выемка кладёт на диск ИМЕННО тот кадр источника, который просит правило.

    Проверка не по числу файлов, а по содержимому: у источника свой узнаваемый уровень
    яркости на каждый кадр, и вынутая картинка обязана совпасть с кадром, который
    посчитало правило. Промах на кадр (самое дорогое, что тут может случиться: его
    видно только глазами по ролику) ловится здесь.

    Источник берётся ИСХОДНЫЙ (`capture_frames(sources=…)`), а не прокси рендера: кадры
    камер рендер вынимает из исходника, и подмена пути на прокси — это ровно та поломка,
    ради которой тест и переписан.
    """
    import core.webrender as wr

    src = _pattern_video(tmp_path / "source.mp4")
    prof = _luma_profile(src)
    src_fps = wr._file_fps(src)
    assert src_fps == 30.0, f"частота источника не прочлась: {src_fps}"

    fps = 60.0                                # ролик вдвое чаще источника — как в бою
    segs = [{"ci": 0, "ts": 0.5, "te": 1.5, "src": 1.0}]
    need = wr.frames_for_chunk(segs, fps, 61, 12)
    size = wr.frame_size(64, 64, 1.4, (64, 64))       # источник 64x64: крупнее не вынуть
    out = str(tmp_path / "кадры")
    got = wr.capture_frames(need, {0: src}, fps, size, out, emit=lambda *a, **k: None)
    assert got == out, "картинки не вынулись"

    f_list = need[0]
    idx = [wr._src_index(f, fps, src_fps) for f in f_list]
    assert len(set(idx)) > 1 and max(idx) < len(prof), (idx, len(prof))
    # Ролик вдвое чаще источника: соседние кадры ролика показывают ОДИН кадр источника
    # — различать надо разные кадры источника.
    uniq = sorted(set(idx))
    vals = [prof[n] for n in uniq]
    assert len(set(vals)) == len(vals), f"кадры источника неразличимы по яркости: {vals}"
    for f, n in zip(f_list, idx):
        path = wr._frame_path(out, 0, f)
        assert os.path.isfile(path), f"нет картинки кадра ролика {f}"
        assert abs(_luma_one(path) - prof[n]) <= 3, (
            f"кадр ролика {f}: вынулся не тот кадр источника (яркость "
            f"{_luma_one(path)} вместо {prof[n]} у кадра {n})")
    # Имена на диске — ровно те, что просит страница (её же функцией и сверяем).
    # Расширение странице называет сборщик (`fext`), поэтому в стенд оно приходит
    # оттуда же, откуда его берёт рендер (`webrender.FRAME_EXT`).
    urls = _run_node("let IPV_FRAMES=%s;let IPV_FEXT=%s;\n%s\nconsole.log(JSON.stringify(%s.map("
                     "function(f){return ipvFrameURL(0,f);})));"
                     % (json.dumps(out.replace("\\", "/")),
                        json.dumps(wr.FRAME_EXT.lstrip(".")),
                        _func(_read(JS85), "ipvFrameURL"), json.dumps(f_list)))
    import urllib.parse

    asked = [os.path.basename(urllib.parse.unquote(u.split("path=", 1)[1])) for u in urls]
    assert asked == [os.path.basename(wr._frame_path(out, 0, f)) for f in f_list], asked


@live_ffmpeg
def test_extraction_command_reads_the_source_not_the_proxy(tmp_path):
    """Команда выемки читает ИСХОДНИК камеры (`-i <исходник>`), а не прокси рендера.

    Прокси (`pv_r…`) собирался короткой стороной кадра ролика, и зум клипа растягивал
    его: стена на 20-й секунде выходила 356 против 2004 у AE. Здесь проверяется сама
    команда и то, чем она записана на диск: путь входа — исходник, размер картинки —
    из правила зума, цвет и сжатие заданы явно.
    """
    import core.webrender as wr

    src = _pattern_video(tmp_path / "cam1.mp4")
    proxy = str(tmp_path / "pv_r0123456789ab.mp4")      # прокси рядом — соблазн подставить
    shutil.copyfile(src, proxy)
    out = str(tmp_path / "frames")
    os.makedirs(out)
    size = (44, 64)                                     # «по зуму»: короткая сторона 44
    cmd = wr._extract_cmd(0, src, 30.0, [30, 31], out, size)
    assert cmd[cmd.index("-i") + 1] == src, cmd
    assert proxy not in cmd, "выемка пошла по прокси рендера"
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=44:64" in vf, vf
    assert wr.FRAME_SCALE_FLAGS in vf, vf
    assert "in_range=tv" in vf and "in_color_matrix=bt709" in vf, vf
    assert "out_range=full" in vf, vf                   # JPEG не умеет студийный диапазон
    # `-t` у ВХОДА: без него `select` разбирает файл до конца (замер: 17.8 с против 1.0 с).
    assert cmd.index("-t") < cmd.index("-i"), cmd
    # И на самом деле: вынутая картинка — того размера, что просили.
    got = wr.capture_frames({0: [30, 31]}, {0: src}, 30.0, size, out,
                            emit=lambda *a, **k: None)
    assert got == out, "кадры не вынулись"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            wr._frame_path(out, 0, 30)], capture_output=True, text=True)
    assert probe.stdout.strip() == "44,64", probe.stdout


# --------------------------------------------------------------------------- #
# Размер вынутого кадра: по зуму клипа, а не «короткая сторона ролика»
# --------------------------------------------------------------------------- #
# Замер владельца (C1476, дисперсия лапласиана серого 1080x1920): стена 5 с — 2212 у AE
# против 433 у нас, стена 20 с — 2004 против 356. Причина не в кодировщике (битрейт
# одинаковый) и не в тексте субтитров (он у нас резкий — 1103): мылится ТОЛЬКО видео
# камеры. Прокси рендера собирался короткой стороной кадра ролика, а зум клипа
# (`cam1_zoom_big`/`lo`/`hi`, до 182 %) растягивал его при отрисовке — AE сжимает 4K
# сразу в итоговый масштаб. Отсюда правило: короткая сторона вынутого кадра не меньше
# min(W,H)×зум, но не крупнее исходника.
def _zoom_plan(keys, holds=None, ease=None, fps=60):
    """План сцены с одним полем зума — ровно то, что читает `_frame_max_zoom`."""
    z = {"keys": keys, "fit": 100.0, "cx": 0.5, "cy": 0.5, "pan": [0, 0], "rot": 0,
         "eases": ease}
    if holds is not None:
        z["holds"] = holds
    if ease is not None:
        z["ease"] = ease
    return {"fps": fps, "w": 1080, "h": 1920, "zoom": z}


def test_frame_size_follows_the_clip_zoom_and_the_source():
    """Размер картинки: min(W,H)×зум, но не крупнее исходника; стороны чётные."""
    import core.webrender as wr

    # 4K-источник, повёрнутый в портрет (как C1476): показывается 2160x3840.
    assert wr.frame_size(1080, 1920, 1.0, (2160, 3840)) == (1080, 1920)
    assert wr.frame_size(1080, 1920, 1.4, (2160, 3840)) == (1512, 2688)
    assert wr.frame_size(1080, 1920, 1.82, (2160, 3840)) == (1966, 3494)
    # Исходник мельче нужного — берём его целиком: растягивать нечем.
    assert wr.frame_size(1080, 1920, 1.82, (1080, 1920)) == (1080, 1920)
    assert wr.frame_size(1080, 1920, 1.4, (720, 1280)) == (720, 1280)
    # Стороны чётные: нечётную не переносит ни yuv420p у mjpeg, ни сам масштаб 4:2:0.
    for src in ((2161, 3841), (2000, 3001), (1080, 1921)):
        w, h = wr.frame_size(1080, 1920, 1.37, src)
        assert w % 2 == 0 and h % 2 == 0, (src, w, h)


def test_frame_size_ignores_the_zoom_that_is_not_in_the_clip():
    """Правило отзывается на ЗУМ КЛИПА, а не на число в вызове: мутация ловится тестом.

    Мутация — вернуть прежнее поведение («короткая сторона = min(W,H)», прокси рендера):
    тогда на исходнике 2160x3840 и зуме 182 % вынулась бы картинка 1080x1920, то есть
    ровно то мыло, ради которого правило и заведено.
    """
    import core.webrender as wr

    src, size = (2160, 3840), (1080, 1920)
    zoom = 1.82
    good = wr.frame_size(size[0], size[1], zoom, src)
    assert good == (1966, 3494), good
    mutant = wr.frame_size(size[0], size[1], 1.0, src)      # прежнее поведение
    assert mutant == (1080, 1920), mutant
    assert min(good) >= min(size) * zoom - 2, (good, mutant)
    assert min(mutant) < min(size) * zoom * 0.6, (good, mutant)


def test_frame_max_zoom_reads_the_curve_not_only_the_keys():
    """Максимум зума куска — по КАДРАМ кривой, а не по значениям ключей.

    Ключи плана — это точки кривой, а не её значения: между 100 % и 182 % середина
    разгона идёт по ЭТАЛОННОЙ кривой (out 35 у источника, in 90 у цели) и от прямой
    отличается заметно — подставить вместо кривой прямую значит посчитать не тот
    масштаб, а промах тут виден только по готовому ролику (мельче нужного = мыло,
    крупнее = лишние гигабайты на диске). Разгон по ней идёт ВЫШЕ прямой: крутая
    середина и мягкий приход в пик. Ключей без ease тоже касается: в `zoom_at`
    подставляется та же пара из layout (было своё зеркальное (0.9, 0.65) — рендер без
    AE считал кривую, обратную превью).
    """
    import core.webrender as wr

    plan = _zoom_plan([[0, 100.0], [62, 182.0], [124, 100.0]])
    # Максимум на отрезке разгона — в его конце; на всём куске — 182 %.
    assert abs(wr._frame_max_zoom(plan, 0, 124) - 1.82) < 1e-9
    assert abs(wr._frame_max_zoom(plan, 62, 1) - 1.82) < 1e-9
    mid = wr._frame_max_zoom(plan, 30, 1)
    straight = 1.0 + (1.82 - 1.0) * 30 / 62
    assert 1.0 < mid < 1.82, mid
    assert mid > straight + 0.1, (
        f"середина разгона ({mid}) посчитана по прямой ({straight}) — кривая не разобрана")
    # И это не «случайно другое число»: кривая — та же `keysAt` страницы (тест ниже).
    assert abs(wr.zoom_at(plan["zoom"], 30) - mid) < 1e-12
    # План без зума (движок выключен, ключ один) — ровно 1.
    assert wr._frame_max_zoom(_zoom_plan([[0, 100.0]]), 0, 100) == 1.0
    # Рамка кадра камеры растит слой ещё сильнее — множитель входит в результат.
    assert abs(wr._frame_max_zoom(plan, 62, 1, frame_zoom=1.1) - 1.82 * 1.1) < 1e-9


@node
def test_zoom_curve_is_one_for_python_and_for_the_page():
    """Зум считает ОДНО правило: `keysAt` страницы и `zoom_at` рендера дают одно число.

    Разойдись они — картинка вынулась бы не того размера (мельче нужного = мыло,
    крупнее = лишние гигабайты на диске), и увидеть это можно только глазами по ролику.
    """
    import core.webrender as wr

    keys = [[0, 100.0], [37, 182.0], [61, 140.0], [124, 100.0], [200, 160.0]]
    ease = [[35, 90], [35, 90], [35, 90], [35, 90], [35, 90]]
    frames = list(range(0, 201))
    got = _run_node("""
%s
var KEYS=%s,EASE=%s,FR=%s;
var out=FR.map(function(f){return +keysAt(KEYS,EASE,f,false).toFixed(4);});
console.log(JSON.stringify(out));
""" % (_func(_read(JS85), "keysAt") + "\n" + _func(_read(JS85), "aeEase") + "\n"
       + _func(_read(JS85), "bezierT") + "\n" + _func(_read(JS85), "bezierY"),
       json.dumps(keys), json.dumps(ease), json.dumps(frames)))
    assert len(got) == len(frames)
    for f, want in zip(frames, got):
        mine = wr.zoom_at({"keys": keys, "ease": ease}, f) * 100.0
        assert abs(mine - want) <= 0.01, f"кадр {f}: рендер {mine}, страница {want}"
    assert max(got) > 150.0, "набор ключей не разгоняет зум — проверять нечего"


def test_zoom_curve_honours_the_hold_keys():
    """Джамп-кат держит значение до следующего ключа — как `keysAt` с массивом hold."""
    import core.webrender as wr

    keys = [[0, 182.0], [62, 100.0]]
    hold = wr.zoom_at({"keys": keys, "holds": [1, 0]}, 30) * 100.0
    smooth = wr.zoom_at({"keys": keys, "holds": [0, 0]}, 30) * 100.0
    assert hold == 182.0, hold
    assert 100.0 < smooth < 182.0, smooth


def test_no_frame_is_an_error_not_a_substitute(tmp_path, monkeypatch):
    """Кадр не вынулся — ошибка с ИМЕНЕМ файла и номером кадра, а не подстановка.

    Подставить прокси или чёрное значило бы молча отдать ролик с чужим кадром: увидеть
    это можно только глазами по готовому файлу. Поэтому упавший ffmpeg (как и «ffmpeg
    отработал, а кадра в папке нет») останавливает рендер понятным сообщением.
    """
    import types

    import core.webrender as wr

    src = str(tmp_path / "cam1.mp4")
    with open(src, "wb") as f:
        f.write(b"x")
    out = str(tmp_path / "frames")
    monkeypatch.setattr(wr, "_file_fps", lambda p: 25.0)
    # ffmpeg «упал»: файлов не создал.
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stderr=b"boom"))
    with pytest.raises(wr.ReelsiError) as e:
        wr.capture_frames({0: [100, 101]}, {0: src}, 60.0, (1512, 2688), out,
                          emit=lambda *a, **k: None)
    text = str(e.value)
    assert src in text, text                              # КАКОЙ файл
    assert re.search(r"кадр\w* источника \d+", text), text  # КАКОЙ кадр
    assert getattr(e.value, "code", "") == "webrender_frames", e.value.code

    # ffmpeg «отработал», а картинки нет: причина та же, и она тоже ошибка.
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stderr=b""))
    with pytest.raises(wr.ReelsiError) as e2:
        wr.capture_frames({0: [100]}, {0: src}, 60.0, (1512, 2688), out,
                          emit=lambda *a, **k: None)
    assert re.search(r"кадр\w* источника \d+", str(e2.value)), str(e2.value)
    assert src in str(e2.value), str(e2.value)


def test_camera_has_no_source_means_seek_fallback(tmp_path, monkeypatch):
    """Камеры нет в исходниках — её кадры идут перемоткой, рендер не падает.

    «Исходник уехал» и «монтаж не пришёл» — не повод не рендерить: страница честно
    перематывает `<video>`, как до этой правки. Ошибкой это становится только тогда,
    когда кадр ДОЛЖЕН был вынуться (см. тест выше).
    """
    import core.webrender as wr

    assert wr.capture_frames({0: [1, 2]}, {}, 60.0, (100, 100),
                             str(tmp_path / "нет"), emit=lambda *a, **k: None) is None
    assert wr.capture_frames({}, {0: "x.mp4"}, 60.0, (100, 100),
                             str(tmp_path / "нет"), emit=lambda *a, **k: None) is None
    assert wr.capture_frames({0: [1]}, {0: "x.mp4"}, 0.0, (100, 100),
                             str(tmp_path / "нет"), emit=lambda *a, **k: None) is None


@node
def test_canvas_smoothing_is_high_and_the_frame_is_not_shrunk_by_it():
    """Холст камеры: сглаживание высокое, а сжатие картинки — не больше двукратного.

    Мыло в рендере бралось не только из размера вынутого кадра: холст мог испортить
    его ещё раз, при отрисовке `drawImage`. Здесь проверяются обе половины: качество
    сглаживания, которое боевой `ipvCamPaint` ставит контексту, и масштаб, с которым
    туда попадает картинка. Масштаб ≈1 — прямое следствие правила зума
    (core.webrender.frame_size): вынутый кадр уже того размера, в каком его показывают.
    """
    import core.webrender as wr

    # Боевая настройка контекста — из самого кода отрисовки, а не из копии в тесте.
    paint = _func(_read(JS85), "ipvCamPaint")
    assert "imageSmoothingEnabled=true" in paint, "у холста камеры выключено сглаживание"
    assert "imageSmoothingQuality='high'" in paint, (
        "у холста камеры не выставлено качество сглаживания: однократное сжатие "
        "крупного кадра мылит")
    # Размеры «как в бою»: источник 2160x3840 (повёрнутый 4K), кадр ролика 1080x1920.
    zoom = 1.4
    size = wr.frame_size(1080, 1920, zoom, (2160, 3840))
    got = _run_node("""
%s
var ZMAX=%s,SZ=%s,w=1080,h=1920,out=[];
for(var i=0;i<=100;i++){
  var s=1+(ZMAX-1)*i/100;                       // зум от 100 %% до максимума клипа
  out.push(+((h/SZ[1])*s).toFixed(4));}         // высота картинки -> высота кадра
console.log(JSON.stringify(out));
""" % (_func(_read(JS85), "ipvCamDrawScale"), json.dumps(zoom), json.dumps(list(size))))
    assert len(got) == 101, got
    # Пиксель КАРТИНКИ ложится в холст без растяжения: на максимуме зума ровно 1:1
    # (затем размер и считался), на 100 % — во столько раз мельче, во сколько
    # максимальный зум больше единицы.
    assert max(got) <= 1.0 + 1e-6, got
    assert min(got) >= 1.0 / zoom - 1e-6, got
    assert abs(got[-1] - 1.0) <= 1e-3, got           # toFixed(4) в стенде
    assert abs(got[0] - 1.0 / zoom) <= 1e-3, got
    # Значит, и сжатие картинки на холсте никогда не мельче 1/зум — это и есть предел,
    # за которым однократное усреднение начинает стоить детали.
    assert 1.0 / zoom >= wr.FRAME_MIN_DRAW_SCALE, zoom
    # А доля исходника в картинке — ровно 1/зум: на самом зуме пиксель ИСХОДНИКА тоже
    # ложится в холст 1:1, без растягивания. Допуск — на округление сторон до чётных.
    assert abs(size[1] / 3840 - 1.0 / zoom) <= 0.02, (size, zoom)
    assert abs(size[1] / 3840 * zoom - 1.0) <= 0.03, (size, zoom)
    # У прокси рендера (короткая сторона = короткой стороне кадра ролика) тот же расчёт
    # давал на максимуме зума 1.4 — растяжение, из-за которого стена и была мылом.
    proxy = _run_node("""
var srcShort=1080,outShort=1080,s=1.4;      // прокси 1080x1920 против кадра 1080x1920
console.log(JSON.stringify({proxy:s*(srcShort/outShort)}));
""")
    assert proxy["proxy"] > 1.39, proxy


def test_render_gives_each_chunk_its_frame_folder_and_clears_it(tmp_path, monkeypatch):
    """Папка картинок куска уезжает в адрес страницы и убирается вместе с куском.

    Папка своя у КАЖДОГО куска (куски снимаются разом и кадры у них разные), путь в
    адресе — с прямыми слэшами (страница клеит к нему имя файла через «/»), а после
    куска папки на диске нет: 1080x1920 на кадр — это сотни килобайт, и держать их до
    конца ролика незачем.
    """
    import struct
    import types
    import urllib.parse

    import core.webrender as wr

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(wr, "_scene_plan", lambda host, body: {
        "fps": 50, "w": 1080, "h": 1920, "dur": 10,
        "cams": [{"ci": 0, "path": "C:/исходник.mp4", "clips": []}],
        "zoom": {"keys": [[0, 140.0]], "fit": 100.0, "cx": 0.5, "cy": 0.5,
                 "pan": [0, 0], "rot": 0}})
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: None)
    monkeypatch.setattr(wr, "kill_tree", lambda p: None)
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(wr, "_camera_sources", lambda plan, edl, emit: {0: "C:/исходник.mp4"})
    monkeypatch.setattr(wr, "source_dims", lambda path: (2160, 3840))
    monkeypatch.setattr(wr, "_render_edl", lambda host, xml, fps, emit: {
        "fps": 50, "segs": [{"ci": 0, "ts": 0.0, "te": 10.0, "src": 0.0}]})
    made = []

    def fake_capture(frames, sources, fps, size, out_dir, emit=None, cancel=None):
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "c0_1.jpg"), "wb") as f:
            f.write(b"jpg")
        made.append((out_dir, frames, sources, size))
        return out_dir

    monkeypatch.setattr(wr, "capture_frames", fake_capture)
    urls = []

    class Sink:
        def write(self, b):
            return len(b)
        def flush(self):
            pass
        def close(self):
            pass

    class FakeFF:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.stdin = Sink()
            self.stderr = io.BytesIO(b"")
        def poll(self):
            return 0
        def wait(self):
            open(self.cmd[-1], "wb").write(b"mp4")
            return 0

    class FakeCap:
        def __init__(self, cmd, **k):
            urls.append(cmd[cmd.index("--url") + 1])
            n = int(cmd[cmd.index("--frames") + 1])
            self.pid = 111
            self.stdout = io.BytesIO(b"".join(struct.pack(">I", 4) + b"JPEG"
                                              for _ in range(n)))
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n")
        def poll(self):
            return 0
        def wait(self):
            return 0

    monkeypatch.setattr(wr.subprocess, "Popen",
                        lambda cmd, **k: FakeFF(cmd, **k) if cmd[0] == "ffmpeg"
                        else FakeCap(cmd, **k))
    res = wr.render(str(xml), str(out), dur=0.2, audio=False, instances=2,
                    emit=lambda *a, **k: None)
    assert res["ok"], res
    assert len(urls) == 2, urls
    query = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in urls]
    dirs = [q["frames"][0] for q in query]
    assert len(set(dirs)) == 2, f"у кусков одна папка картинок на всех: {dirs}"
    # Границы куска — те же числа, что у съёмщика в --start/--frames: по ним страница не
    # просит картинку за последним кадром куска (её там нет, там уже соседний кусок).
    starts = [int(q["fstart"][0]) for q in query]
    counts = [int(q["fcount"][0]) for q in query]
    assert starts == [0, 5] and counts == [5, 5], (starts, counts)
    for d in dirs:
        assert "\\" not in d, f"в адресе путь с обратными слэшами: {d}"
        assert not os.path.isdir(d), f"картинки куска остались на диске: {d}"
    # Кадры куска считает то же правило: у нулевого куска — первые пять кадров ролика.
    assert [os.path.abspath(d).replace("\\", "/") for d, _, _, _ in made] == dirs, (made, dirs)
    assert [f for _, f, _, _ in made] == [{0: [0, 1, 2, 3, 4]}, {0: [5, 6, 7, 8, 9]}], made
    # Вынимаются ИСХОДНИКИ камер, и размер картинки — тот, что посчитан по зуму куска:
    # источник 2160x3840 («повёрнутый» 4K, как C1476), кадр ролика 1080x1920, зум 140 %
    # — короткая сторона 1080×1.4 = 1512.
    assert [s for _, _, s, _ in made] == [{0: "C:/исходник.mp4"}] * 2, made
    assert [sz for _, _, _, sz in made] == [(1512, 2688)] * 2, made


def test_render_without_frames_leaves_the_url_alone(tmp_path, monkeypatch):
    """Картинок нет — адрес страницы прежний, и в него не подставляется пустая папка.

    Так рендер и остаётся рабочим, когда выемка не удалась: страница просто перематывает
    `<video>`, как до этой правки, а в адресе нет параметра с пустым значением.
    """
    import struct
    import types
    import urllib.parse

    import core.webrender as wr

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(wr, "_scene_plan", lambda host, body: {"fps": 50, "w": 1080,
                                                              "h": 1920, "dur": 10})
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: None)
    monkeypatch.setattr(wr, "kill_tree", lambda p: None)
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(wr, "_camera_sources", lambda plan, edl, emit: {})   # исходников нет
    urls = []

    class Sink:
        def write(self, b):
            return len(b)
        def flush(self):
            pass
        def close(self):
            pass

    class FakeFF:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.stdin = Sink()
            self.stderr = io.BytesIO(b"")
        def poll(self):
            return 0
        def wait(self):
            open(self.cmd[-1], "wb").write(b"mp4")
            return 0

    class FakeCap:
        def __init__(self, cmd, **k):
            urls.append(cmd[cmd.index("--url") + 1])
            n = int(cmd[cmd.index("--frames") + 1])
            self.pid = 111
            self.stdout = io.BytesIO(b"".join(struct.pack(">I", 4) + b"JPEG"
                                              for _ in range(n)))
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n")
        def poll(self):
            return 0
        def wait(self):
            return 0

    monkeypatch.setattr(wr.subprocess, "Popen",
                        lambda cmd, **k: FakeFF(cmd, **k) if cmd[0] == "ffmpeg"
                        else FakeCap(cmd, **k))
    res = wr.render(str(xml), str(out), dur=0.2, audio=False, instances=2,
                    emit=lambda *a, **k: None)
    assert res["ok"], res
    assert urls and len(set(urls)) == 1, urls
    assert "frames=" not in urls[0], urls[0]
    assert urllib.parse.urlsplit(urls[0]).path == "/render", urls[0]


# Два <video> кадра ждутся РАЗОМ: `seeked` в этом стенде не приходит вовсе (файл не
# открылся — ровно тот случай, ради которого потолок VFRAME_MS и стоит). По очереди это
# ДВА потолка, разом — один: разница ровно вдвое, и её видно числом, а не на глаз.
VFRAME_MS_STAND = 1500        # тот же потолок, что в static/app/85-inserts-view.js
_PARALLEL_JS = r"""
(async function(){
  SEEK_MS=-1;VFC_SILENT=true;            // ни `seeked`, ни показанного кадра: файл не открылся
  // Вставка — ВИДЕО: её <video> и камера кадра — два разных декодера в одном кадре.
  PLAN.inserts=[{t:'video',media:'C:/ins/v.mp4',start:1,end:4,x:0,y:0,sin:0,sc:100,
    fitw:1080,fith:1920,
    anim:{opacity:[[1,0],[1.5,100],[3.5,100],[4,0]],
          scale:[[1,44],[1.5,100],[3.5,100],[4,44]],
          position:[[1,[540,960]],[4,[540,960]]]}}];
  var t0=performance.now();
  await ipvRenderAt(3.0);                 // камера 0 -> 3.0, видеовставка 0 -> 2.0
  var ms=performance.now()-t0;
  var ivs=OVINS.querySelectorAll('video');
  console.log(JSON.stringify({ms:ms,nVid:ivs.length,waits:VWAIT_MAX,
    camSeeks:IPV.vids[0].seeks,insSeeks:ivs.map(function(v){return v.seeks;})}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_render_waits_all_videos_not_one_after_another():
    """7. Камера и видеовставка ждутся РАЗОМ: время ≈ максимум, а не сумма.

    Вторая половина той же беды, что и в тесте выше: кадр ждал камеру, а потом отдельно
    каждую видеовставку. Здесь это видно сразу двумя числами — потолком одновременных
    ожиданий (`waits`: ждут разом или по очереди) и временем кадра (один потолок против
    двух). По времени одному «по очереди» ещё может повезти — вторая перемотка успевает
    отыграть, пока ждут первую, — поэтому одновременность проверяется отдельно.
    """
    out = _run_node(_stand(extra=_PARALLEL_JS))

    assert out["nVid"] == 1, "видеовставки в кадре нет — ждать было бы нечего, тест пустой"
    assert out["camSeeks"] >= 1 and out["insSeeks"] and out["insSeeks"][0] >= 1, out
    assert out["waits"] >= 2, (
        "ожидания двух <video> не пересеклись: пока один ждал, второй ещё не ждал — это и "
        f"есть «по очереди» (одновременных ожиданий было {out['waits']})")
    assert out["ms"] >= VFRAME_MS_STAND * 0.9, (
        f"кадр собрался за {out['ms']:.0f} мс — потолок ожидания не отработал, мерить нечего")
    assert out["ms"] < VFRAME_MS_STAND * 1.5, (
        f"кадр собрался за {out['ms']:.0f} мс при потолке {VFRAME_MS_STAND} мс на каждое "
        f"<video>: похоже, ждут по очереди (сумма ≈ {2 * VFRAME_MS_STAND} мс)")


@node
def test_same_frame_is_not_seeked_twice_and_the_next_one_is():
    """8. Повтор кадра не перематывает <video>, соседний кадр — перематывает.

    Стенд — единственное место, где видно и то, и другое сразу: `seeks` считает сам
    <video> (FakeVid), а `capText` — то, что выставила отрисовка накладок. Если
    «пропустить перемотку» реализовать пропуском всей сцены, субтитры замрут на первом
    кадре — и это ровно тот молчаливый брак, ради которого тест и написан.
    """
    out = _run_node(_stand(extra=r"""
    (async function(){
      await ipvRenderAt(3.0);
      var a=SNAP();var sa=SEEKS();
      // Тот же момент ещё раз: кадр уже стоит на просимом времени — ни перемотки, ни
      // ожидания (разница ноль, это тот же кадр исходника).
      await ipvRenderAt(3.0);
      var b=SNAP();var sb=SEEKS();
      // Соседний кадр того же куска EDL — ДРУГОЙ кадр: <video> на паузе сам на него не
      // перейдёт, перемотка нужна (иначе картинка замерла бы на кадре 3.0).
      await ipvRenderAt(3.0+1/PLAN.fps);
      var c=SNAP();var sc=SEEKS();
      // Другой кусок EDL (камера 2): время источника ушло вперёд скачком — перемотка нужна.
      await ipvRenderAt(6.0);
      var d=SNAP();var sd=SEEKS();
      console.log(JSON.stringify({a:a,b:b,c:c,d:d,
        seeks:{a:sa,b:sb,c:sc,d:sd}}));
    })().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
    """))
    sk = out["seeks"]
    # В _PLAN_JS кусок камеры 2 начинается на 5 с: дальше — другой источник времени.
    assert out["a"]["capText"], "подпись в кадре не выставилась — проверять нечего"
    assert out["a"]["rtFrames"] == 1, out["a"]["rtFrames"]
    assert sk["a"][0] >= 1, "первый кадр не перемотал <video> вовсе"
    # 1) Кадр уже стоит на месте: повтор не перематывает — просить тот же кадр дважды
    #    значит сбрасывать готовый кадр декодера.
    assert sk["b"] == sk["a"], \
        f"повтор того же кадра перемотал <video>: {sk['a']} -> {sk['b']}"
    # 2) Соседний кадр — перемотка ровно одна: без неё снимок остался бы прежним кадром.
    assert sk["c"][0] == sk["a"][0] + 1, (
        "соседний кадр (шаг 1/fps) не перемотал <video> — <video> на паузе вперёд не идёт, "
        f"снимок остался бы прежним кадром: {sk['a']} -> {sk['c']}")
    # 3) Но сцена на каждом кадре ВЫСТАВЛЕНА: отрисовка накладок не пропущена.
    for name in ("b", "c"):
        assert out[name]["rtFrames"] == 1, \
            f"кадр {name}: отрисовка не отметилась (frames={out[name]['rtFrames']})"
        assert out[name]["capText"] == out["a"]["capText"], \
            f"кадр {name}: накладки не пересчитаны — подпись «{out[name]['capText']}»"
        assert out[name]["words"], f"кадр {name}: слова субтитров пропали"
    # 4) Смена источника (другой кусок EDL) — перемотка обязана быть: без неё кадр
    #    остался бы от прежней камеры.
    assert sk["d"][0] == sk["c"][0] + 1, \
        f"переход на другой кусок EDL не перемотал <video>: {sk['d']}"


@node
def test_capture_args_units_and_names_match_on_both_sides(tmp_path):
    """Единицы и имена ключей съёмщика: Python собрал — capture.mjs разобрал.

    Единица времени расходилась молча: Python слал секунды (180) под ключом без
    единицы, съёмщик читал те же числа как миллисекунды — страница объявлялась
    неготовой через 180 мс («не готова за 0 с»). Поэтому единица стоит в самом имени
    ключа, а сверяет стороны реальный разбор съёмщика: подмена единицы или переименование
    ключа с любой стороны валит этот тест, а не живой рендер.
    """
    import core.webrender as wr

    got = _capture_json(_python_capture_cmd(tmp_path))
    assert got["readyTimeoutMs"] == wr.READY_TIMEOUT_MS == 180000, (
        "таймаут готовности разошёлся по единицам: Python шлёт "
        f"{wr.READY_TIMEOUT_MS}, съёмщик разобрал {got['readyTimeoutMs']}")
    for key, want in (("w", W), ("h", H), ("start", 300), ("frames", 180)):
        assert got[key] == want, f"{key}: съёмщик разобрал {got[key]}, а задумано {want}"
    assert got["fps"] == FPS, got["fps"]                    # кадры в секунду, не в минуту
    assert got["readyTimeoutMs"] / 1000 == 180, "миллисекунды съёмщика — не 180 секунд"
    # Профиль Chrome едет строкой как есть: в пути кириллица и обратные слэши.
    assert got["profile"] == str(tmp_path / "профиль"), got["profile"]
    assert got["profile"].endswith("профиль"), got["profile"]
    # Обратные слэши — свойство пути Windows, а не условия задачи: на POSIX путь тот же
    # самый, но с «/» (`str(tmp_path / "профиль")`), и требовать «\» значило бы проверять
    # разбор на несуществующем там пути.
    if sys.platform == "win32":
        assert "\\" in got["profile"], got["profile"]
    assert got["url"].startswith("http://127.0.0.1:5001/render?"), got["url"]
    assert got["chrome"] is None, got["chrome"]             # не передан — ищем сами


@node
def test_capture_args_field_units_survive_the_wire(tmp_path):
    """Поля кроме таймаута — те же числа и типы, и ни один ключ не переименован молча.

    Съёмщик разбирает `--w 1080` как 1080 пикселей, `--start 300` как НОМЕР КАДРА и
    `--fps 59.94` как кадры в секунду. Проверка идёт через разбор съёмщика, а не через
    наличие строки в списке: ключ, переименованный на одной стороне, оставил бы
    значение по умолчанию — и рендер поехал бы не с того места.
    """
    import core.webrender as wr

    got = _capture_json(_python_capture_cmd(tmp_path, chrome="C:/chrome.exe", node_exe="node"))
    assert got["chrome"] == "C:/chrome.exe", got["chrome"]
    assert isinstance(got["start"], int) and isinstance(got["frames"], int), got
    assert got["fps"] == FPS, got["fps"]                   # число, а не строка «60»

    cmd = _python_capture_cmd(tmp_path)
    cmd[cmd.index("--fps") + 1] = "59.94"                   # NTSC: дробные кадры в секунду
    cmd[cmd.index("--start") + 1] = "0"                     # нулевой кадр — законное число
    got = _capture_json(cmd)
    assert got["fps"] == 59.94, f"дробный fps не дожил: {got['fps']}"
    assert got["start"] == 0, f"нулевой кадр подменён значением по умолчанию: {got['start']}"
    assert got["frames"] == 180, got["frames"]

    # Имена ключей у съёмщика: разбор ловит переименование, а не только значение.
    fresh = set(wr._capture_cmd("node", "", 1, 1, 1.0, 0, 1, "", None))
    assert "--ready-timeout" not in fresh, \
        "ключ без единицы вернулся: единицу времени опять не отличить от секунд"
    assert "--ready-timeout-ms" in fresh, fresh


@node
def test_mutation_seconds_instead_of_ms_is_caught(tmp_path, monkeypatch):
    """Мутация: вернуть секунды в имя ключа или в константу — тест обязан покраснеть.

    Проверка не «на словах». Первая половина — ключ без единицы с прежним числом секунд
    (ровно та строка, что была до правки): значение до съёмщика НЕ доезжает и он берёт
    своё, то есть таймаут молча становится не тем, что задумал Python. Вторая — константа
    снова в секундах: расхождение видно на разборе. Так сторож не сможет молча перестать
    ловить единицы.
    """
    cmd = _python_capture_cmd(tmp_path)
    i = cmd.index("--ready-timeout-ms")
    cmd[i:i + 2] = ["--ready-timeout", "180"]             # ровно то, что было до правки
    broken = _capture_json(cmd)
    assert broken["readyTimeoutMs"] == 180000, (
        "ключ без единицы доехал до съёмщика — значит, он всё ещё разбирается, и "
        f"единицу времени опять не отличить от секунд: {broken}")
    assert broken["readyTimeoutMs"] != 180, broken        # секунды до таймаута не доехали

    monkeypatch.setattr("core.webrender.READY_TIMEOUT_MS", 180)   # константа снова в секундах
    got = _capture_json(_python_capture_cmd(tmp_path))
    assert got["readyTimeoutMs"] == 180, got
    assert got["readyTimeoutMs"] != 180000, "мутация константы не доехала до съёмщика"


@node
def test_capture_frame_format_and_timing_interval_travel_to_the_catcher(tmp_path):
    """Формат кадра, качество и период разбивки — теми же ключами и числами.

    Снимок в PNG стоит мегабайты на кадр: почти всё время уходит на кодирование в
    браузере и base64 через протокол DevTools, а ffmpeg всё равно читает поток. Формат
    и качество называет Python (`CAPTURE_FORMAT`/`CAPTURE_QUALITY`), читает съёмщик —
    и оба конца сверяются его РЕАЛЬНЫМ разбором, а не наличием строки в списке.
    """
    import core.webrender as wr

    got = _capture_json(_python_capture_cmd(tmp_path))
    assert got["format"] == wr.CAPTURE_FORMAT == "jpeg", got
    assert got["jpegQuality"] == wr.CAPTURE_QUALITY == 95, got
    assert got["timingEvery"] > 0, got
    # Дробное и нецелое качество съёмщик принимает числом (а не «да/нет»).
    cmd = _python_capture_cmd(tmp_path)
    cmd[cmd.index("--jpeg-quality") + 1] = "97.5"
    assert _capture_json(cmd)["jpegQuality"] == 97.5, "качество не дожило до съёмщика"
    # Формат вне списка — отказ разбора, а не «снимем как получится».
    cmd[cmd.index("--format") + 1] = "webp"
    bad = subprocess.run(["node", CAPTURE, *cmd[2:], "--parse-only"], capture_output=True,
                         text=True, encoding="utf-8-sig", errors="replace", timeout=60)
    assert bad.returncode == 2 and "format" in bad.stderr, (bad.returncode, bad.stderr)


@node
def test_timing_line_is_printed_by_the_catcher_and_parsed_by_python(tmp_path):
    """Строка `#timing …` — одна на печать и на разбор: съёмщик печатает, Python читает.

    Владелец увидел 1.3 с на кадр, и без разбивки не видно, на что именно: ждать кадр
    <video>, рисовать, снимать или отдавать байты. Печать живёт в webrender/timing.mjs,
    разбор — в core.webrender.TIMING_RE; тест гоняет НАСТОЯЩУЮ функцию печати и кормит
    её вывод НАСТОЯЩЕМУ разбору: переименованное поле или сменённая единица ловится
    здесь, а не на живом рендере.
    """
    import core.webrender as wr

    js = ("import { timingLine } from './timing.mjs';"
          "process.stdout.write(timingLine({frames:30,seek:300,paint:90,shot:900,write:60}));")
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, cwd=os.path.dirname(CAPTURE),
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr
    line = p.stdout.strip()
    got = wr._timing_line(line)
    assert got is not None, f"Python не разобрал строку съёмщика: {line!r}"
    # Средние, а не суммы: 300 мс на 30 кадров — это 10 мс на кадр.
    assert got == {"seek": 10.0, "paint": 3.0, "shot": 30.0, "write": 2.0}, (line, got)
    # Единица времени в строке одна — миллисекунды; в логе видно, что это среднее.
    assert "мс" in line and "среднее" in line, line
    # Строка без разбивки (прогресс, ошибка) разбором не считается — иначе `#timing`
    # перехватывал бы в логе чужие строки.
    assert wr._timing_line("кадр 30/180") is None
    assert wr._timing_line("#ошибка: страница упала") is None


@node
def test_timing_lines_go_to_log_but_not_into_the_error_tail():
    """Разбивка идёт в лог, но не вытесняет из хвоста причину падения.

    Хвост (`state['tail']`) — то, что попадает в сообщение «съёмщик упал». Строка
    `#timing` приходит каждые 30 кадров: на 1800 кадрах это 60 строк, и причина падения
    в первых из них была бы выдавлена наружу молча.
    """
    import core.webrender as wr

    class FakeProc:
        stderr = io.BytesIO((
            "#chrome-pid 4242\n#ready 1080x1920@60\n"
            "#timing seek=1.0 paint=2.0 shot=3.0 write=4.0 (мс, среднее)\n"
            "#ошибка: страница упала\n").encode("utf-8"))

    state = {"chrome_pids": [], "tail": []}
    lines = []
    wr._pump_stderr(FakeProc(), state, lambda line, **v: lines.append(line))
    assert state["chrome_pids"] == [4242], state      # PID своего Chrome — для гашения
    tail = "\n".join(state["tail"])
    assert "ошибка" in tail, tail
    assert "#timing" not in tail, f"разбивка вытесняет причину падения: {tail}"
    assert any("#timing" in line for line in lines), lines      # в лог — попадает


@pytest.fixture()
def client(tmp_path):
    """Клиент Flask с подменёнными путями состояния (как в соседних тестах api/)."""
    os.environ.setdefault("REELSI_UI_STATE", str(tmp_path / "ui_state.test.json"))
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_render_body_round_trip(client, tmp_path):
    """Тело сборки доезжает до страницы: POST кладёт в _tmp клипа, GET отдаёт обратно."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    body = {"xml": str(xml), "style": {"font": "SFPro"}, "inserts": [{"media": "a.png"}]}

    r = client.post("/api/render_body", json=body)
    d = r.get_json()
    assert d.get("ok") and d.get("id"), d
    import re as _re
    assert _re.fullmatch(r"[0-9a-f]{8,32}", d["id"]), d["id"]

    g = client.get("/api/render_body", query_string={"xml": str(xml), "id": d["id"]})
    got = g.get_json()
    assert got.get("ok"), got
    assert got["body"]["style"] == {"font": "SFPro"}, got["body"]
    assert got["body"]["inserts"] == [{"media": "a.png"}], got["body"]
    # Тело лежит в _tmp клипа — рядом с прокси и черновиками, а не в чужой папке.
    from api.webrender import body_path
    assert os.path.dirname(body_path(str(xml), d["id"])) == str(tmp_path / "_tmp")


def test_render_body_rejects_junk(client, tmp_path):
    """Мусор вместо id и несуществующий xml — ошибка, а не падение и не пустой план."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    bad_id = client.get("/api/render_body",
                        query_string={"xml": str(xml), "id": "../../secret"}).get_json()
    assert bad_id.get("error"), bad_id
    no_xml = client.post("/api/render_body", json={"xml": str(tmp_path / "нет.xml")}).get_json()
    assert no_xml.get("error"), no_xml
    # Числовое поле вместо пути (сторож «ни один POST не отвечает 500 на числа»).
    assert client.post("/api/render_body", json={"xml": 123}).status_code == 200


def test_pump_frames_follows_length_prefix(monkeypatch):
    """Кадры из съёмщика читаются по префиксу длины: 4 байта (старший вперёд) + кадр."""
    import io
    import struct
    import threading
    import core.webrender as wr

    frames = [b"JPEG-1", b"JPEG-22"]
    payload = b"".join(struct.pack(">I", len(f)) + f for f in frames)
    proc = type("P", (), {"stdout": io.BytesIO(payload)})()

    class Sink:
        """Труба кодировщика куска: собрать байты и «закрыться» (EOF ему ставит поток)."""
        def __init__(self):
            self.buf = b""
            self.closed = False
        def write(self, b):
            assert not self.closed, "кадр уехал в уже закрытую трубу"
            self.buf += b
        def close(self):
            self.closed = True

    ff = type("F", (), {"stdin": Sink()})()
    lines = []
    # Кадры куска идут в СВОЙ кодировщик, а прогресс считает общий счётчик кусков:
    # кусок называет себя (номер k), чтобы в строку шла СУММА готового по всем кускам.
    # Шаблон строки подставляет emit (как console_emit в бою), поэтому и здесь он же.
    n = wr._pump_frames(
        proc, ff, len(frames),
        wr._Progress(len(frames), lambda line, **v: lines.append(line.format(**v))),
        0, None, threading.Event())
    assert n == len(frames), n
    assert ff.stdin.buf == b"JPEG-1JPEG-22", ff.stdin.buf
    assert ff.stdin.closed, "труба кодировщика не закрыта — сегмент остался бы недописан"
    assert lines == ["кадр 1/2", "кадр 2/2"], lines   # прогресс — по всему ролику, а не по куску


def test_cancel_kills_chrome_by_pid(monkeypatch):
    """Отмена гасит Chrome ПО PID — по имени убивать нельзя: на машине чужой браузер.

    Дверь у ОС своя (`core/webrender._kill_pid`): на Windows `taskkill /PID`, на POSIX
    `os.kill`. Проверяем ТУ, что есть на этой системе: подмена Windows-двери на Linux
    ничего бы не поймала, и тест краснел бы на исправном коде.
    """
    import core.webrender as wr

    calls = []
    if os.name == "nt":
        monkeypatch.setattr(wr.subprocess, "run",
                            lambda cmd, **kw: calls.append(list(cmd)) or type("R", (), {})())
        wr._kill_pid(0)                      # нулевой PID — не трогаем никого
        assert not calls, calls
        wr._kill_pid(4242)
        assert calls, "Chrome не погашен вовсе"
        flat = [str(x) for cmd in calls for x in cmd]
        assert "4242" in flat, flat
        assert any(x.lower() in ("taskkill", "/pid") for x in flat), flat
        assert not any(x.lower() in ("/im", "chrome.exe") for x in flat), \
            f"гасим по имени, а не по PID: {flat}"
    else:
        monkeypatch.setattr(wr.os, "kill",
                            lambda pid, sig: calls.append((pid, sig)))
        wr._kill_pid(0)                      # нулевой PID — не трогаем никого
        assert not calls, calls
        wr._kill_pid(4242)
        assert calls, "Chrome не погашен вовсе"
        import signal
        assert calls == [(4242, signal.SIGKILL)], \
            f"гасим не тот процесс или не тем сигналом: {calls}"


def test_render_frame_range_from_plan_and_cancel(tmp_path, monkeypatch):
    """Кадров ровно `fps × длительность` (fps — ИЗ ПЛАНА), а отмена не пишет файл.

    Съёмщик и ffmpeg подменены: проверяется не Chrome, а арифметика куска и то, что
    «Стоп» возвращает отмену, а не падение.
    """
    import io
    import struct
    import types
    import core.webrender as wr

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    plan = {"fps": 50, "w": 1080, "h": 1920, "dur": 10}
    monkeypatch.setattr(wr, "_scene_plan", lambda host, body: plan)
    monkeypatch.setattr(wr, "_camera_sources", lambda plan, edl, emit: {})
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))

    state = {"ff_cmd": {}, "cap_cmd": None, "in": None}

    class Rec:
        """Приёмник кадров вместо stdin ffmpeg: копит байты и «закрывается» как труба."""
        def __init__(self):
            self.buf = b""
            self.closed = False
        def write(self, b):
            self.buf += b
        def flush(self):
            pass
        def close(self):
            self.closed = True

    class FakeFF:
        """ffmpeg: кодировщик куска (`image2pipe`) и склейка сегментов (`concat`)."""
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.mode = cmd[cmd.index("-f") + 1]        # image2pipe или concat
            state["ff_cmd"][self.mode] = list(cmd)
            self.stdin = Rec()
            if self.mode == "image2pipe":
                state["in"] = self.stdin                # кадры куска — сюда
            self.stderr = io.BytesIO(b"")
        def poll(self):
            return 0
        def wait(self):
            # ffmpeg пишет файл: без него render считает, что ролик не собрался.
            # Путь — ПОСЛЕДНИЙ аргумент команды: у куска это его сегмент, у склейки —
            # итоговая картинка (при звуке она временная, см. webrender_audio).
            open(self.cmd[-1], "wb").write(b"mp4")
            return 0

    class FakeCap:
        def __init__(self, cmd, frames, **k):
            state["cap_cmd"] = list(cmd)
            self.pid = 111
            self.stdout = io.BytesIO(
                b"".join(struct.pack(">I", len(f)) + f for f in frames))
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n")
        def poll(self):
            return 0
        def wait(self):
            return 0

    def fake_popen(cmd, **k):
        if cmd and cmd[0] == "ffmpeg":
            return FakeFF(cmd, **k)
        return FakeCap(cmd, [b"JPEG"] * 5, **k)

    monkeypatch.setattr(wr.subprocess, "Popen", fake_popen)
    # taskkill (гашение Chrome по PID) — заглушкой: тест не должен трогать процессы
    # машины, а `subprocess.run` внутри себя ходит в тот же подменённый Popen.
    runs = []
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: runs.append(list(cmd)) or types.SimpleNamespace(returncode=0))
    # instances=1: арифметика куска проверяется на ОДНОМ экземпляре съёмщика, а деление
    # кадров между экземплярами — своим тестом (tests/test_webrender_instances.py).
    res = wr.render(str(xml), str(out), start=1.0, dur=0.1, audio=False, instances=1,
                    emit=lambda line, **v: None)
    assert res["ok"] and res["frames"] == 5, res          # 0.1 с при 50 к/с — пять кадров
    assert state["in"].buf == b"JPEG" * 5, state["in"].buf
    assert state["in"].closed, "труба ffmpeg не закрыта — ролик остался бы недописанным"
    assert state["cap_cmd"] is not None, state
    assert state["cap_cmd"][state["cap_cmd"].index("--frames") + 1] == "5", state["cap_cmd"]
    assert state["cap_cmd"][state["cap_cmd"].index("--start") + 1] == "50", state["cap_cmd"]
    enc = state["ff_cmd"]["image2pipe"]                  # кодировщик куска
    assert enc[enc.index("-framerate") + 1] == "50", enc
    # Вход кодировщика — ТОТ ЖЕ формат, что шлёт съёмщик: mjpeg читает поток JPEG-кадров.
    # Разойдись они — ffmpeg молча прочитал бы мусор, а файл вышел бы пустым.
    i = enc.index("-c:v")
    assert enc[i + 1] == "mjpeg", enc
    assert enc[:i].count("-c:v") == 0, enc                   # и это именно вход
    # Склейка сегментов — без перекодирования: второй раз кодировать нечего.
    cat = state["ff_cmd"]["concat"]
    assert cat[cat.index("-f") + 1] == "concat", cat
    assert cat[cat.index("-safe") + 1] == "0", cat
    assert cat[cat.index("-c") + 1] == "copy", cat

    # Отмена до первого кадра: файла нет, но и исключения нет — это «стоп», а не сбой.
    out.unlink()
    killed = []
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: killed.append(pid))
    res = wr.render(str(xml), str(out), dur=0.1, cancel=lambda: True,
                    emit=lambda line, **v: None)
    assert res["cancelled"] is True and res["frames"] == 0, res
    assert not out.exists(), "отменённый рендер оставил файл"


@node
def test_ffmpeg_error_is_reported_before_the_catcher(tmp_path, monkeypatch):
    """ffmpeg упал — пользователь видит ЕГО ошибку, а не «съёмщик упал».

    Живой дефект: папки выхода нет, ffmpeg выходит сразу, съёмщик падает на закрытой
    трубе — и в интерфейсе было «съёмщик кадров упал (код 1): кадр 1/180» без причины.
    Поэтому разбор идёт СНАЧАЛА по ffmpeg, и его stderr читается отдельным потоком:
    через `communicate` он пришёл бы только после конца процесса, а труба, набитая
    ошибками ffmpeg, остановила бы его самого.

    Обе стороны падают ОДНОВРЕМЕННО — ровно как в живом прогоне; порядок разбора и
    проверяется.
    """
    import io
    import struct
    import types
    import core.webrender as wr

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "нет-такой-папки" / "out.mp4"
    monkeypatch.setattr(wr, "_scene_plan", lambda host, body: {"fps": 50, "w": 1080,
                                                              "h": 1920, "dur": 10})
    monkeypatch.setattr(wr, "_camera_sources", lambda plan, edl, emit: {})
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: None)
    monkeypatch.setattr(wr, "kill_tree", lambda p: None)
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0))

    ff_msg = "[h264_nvenc @ 0000] Cannot open output file"

    class Rec:
        buf = b""
        def write(self, b):
            self.buf += b
        def flush(self):
            pass
        def close(self):
            pass                       # труба закрыта — ffmpeg «упал» на первой же записи

    class FakeFF:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.stdin = Rec()
            self.stderr = io.BytesIO((ff_msg + "\n").encode("utf-8"))
        def poll(self):
            return 1                   # кодировщик уже упал
        def wait(self):
            return 1                   # код возврата ffmpeg

    class FakeCap:
        def __init__(self, cmd, **k):
            self.pid = 111
            self.stdout = io.BytesIO(b"".join(struct.pack(">I", len(b"JPEG")) + b"JPEG"
                                              for _ in range(5)))
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n")
        def poll(self):
            return 1
        def wait(self):
            return 1                   # и съёмщик тоже «упал» — на закрытой трубе

    monkeypatch.setattr(wr.subprocess, "Popen",
                        lambda cmd, **k: FakeFF(cmd, **k) if cmd[0] == "ffmpeg"
                        else FakeCap(cmd, **k))

    with pytest.raises(Exception) as exc:
        wr.render(str(xml), str(out), dur=0.1, emit=lambda line, **v: None)
    text = str(exc.value)
    assert "ffmpeg" in text, f"в сообщении нет причины от ffmpeg: {text!r}"
    assert ff_msg in text, f"ошибка ffmpeg не доехала до пользователя: {text!r}"
    assert "Съёмщик" not in text and "съёмщик" not in text, (
        "показана ошибка съёмщика, хотя упал ffmpeg (порядок разбора перепутан): "
        f"{text!r}")


@node
def test_render_creates_the_output_directory(tmp_path, monkeypatch):
    """Папку выходного файла рендер создаёт сам.

    Без этого ffmpeg выходил сразу («No such file or directory»), съёмщик падал на
    закрытой трубе, и пользователь видел ошибку съёмщика вместо «папки нет». Здесь путь
    ЗАРАНЕЕ не существует: проверяется и создание, и то, что рендер дошёл до конца.
    """
    import io
    import struct
    import types
    import core.webrender as wr

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "новая" / "вложенная" / "out.mp4"
    assert not out.parent.exists()
    monkeypatch.setattr(wr, "_scene_plan", lambda host, body: {"fps": 50, "w": 1080,
                                                              "h": 1920, "dur": 10})
    monkeypatch.setattr(wr, "_camera_sources", lambda plan, edl, emit: {})
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: None)
    monkeypatch.setattr(wr, "kill_tree", lambda p: None)
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0))

    class Rec:
        def write(self, b):
            pass
        def flush(self):
            pass
        def close(self):
            pass

    class FakeFF:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.stdin = Rec()
            self.stderr = io.BytesIO(b"")
        def poll(self):
            return 0
        def wait(self):
            # ffmpeg пишет файл: папка обязана существовать ДО его запуска. У куска это
            # его сегмент в рабочей папке рендера, у склейки — итоговый файл в папке вывода.
            assert os.path.isdir(os.path.dirname(self.cmd[-1])), \
                f"ffmpeg запущен с несуществующей папкой: {self.cmd[-1]}"
            open(self.cmd[-1], "wb").write(b"mp4")
            return 0

    class FakeCap:
        def __init__(self, cmd, **k):
            self.pid = 111
            self.stdout = io.BytesIO(b"".join(struct.pack(">I", len(b"J")) + b"J"
                                              for _ in range(5)))
            self.stderr = io.BytesIO(b"")
        def poll(self):
            return 0
        def wait(self):
            return 0

    monkeypatch.setattr(wr.subprocess, "Popen",
                        lambda cmd, **k: FakeFF(cmd, **k) if cmd[0] == "ffmpeg"
                        else FakeCap(cmd, **k))
    res = wr.render(str(xml), str(out), dur=0.1, audio=False, instances=1,
                    emit=lambda line, **v: None)
    assert res["ok"], res
    assert out.is_file() and out.stat().st_size > 0, "файл не собрался"


@node
def test_capture_runs_a_whole_render_and_exits_zero(tmp_path):
    """Съёмщик проходит кадры до конца и выходит КОДОМ 0 — без Chrome.

    Живой дефект: после последних кадров съёмщик выходил кодом 1 («кадр 173/180»), хотя
    ffmpeg получил все кадры и файл был цел. Причина — код выхода выставлялся ДО того,
    как станет известен результат: `killChrome()` внутри себя звал `process.exit`, и
    уборка (гашение браузера и слив stdout) шла уже после выхода. Поэтому здесь код
    выхода проверяется у НАСТОЯЩЕГО процесса на настоящем (поддельном) протоколе
    DevTools: Chrome не запускается, браузер изображает node-стенд, который отвечает
    по HTTP (`/json/version`, `/json/list`) и по WebSocket — как Chrome.

    Кадры читаются тем же способом, что в бою: 4 байта длины (старший вперёд) и данные.
    """
    import struct

    stand = tmp_path / "fake_chrome.mjs"
    stand.write_text(_FAKE_CHROME_JS.replace("__FRAME_B64__", _TINY_JPEG_B64), encoding="utf-8")
    page = tmp_path / "render.html"
    page.write_text("<html><body>страница рендера</body></html>", encoding="utf-8")
    profile = tmp_path / "профиль"
    frames = 8

    # Вместо chrome.exe запускается `node <стенд>` (REELSI_CAPTURE_CHROME_APP): .mjs
    # Windows напрямую не запускает, а Chrome там — .exe, и путь к нему съёмщик зовёт
    # как есть. Снаружи разницы нет: те же аргументы (порт стенд берёт из них, как
    # Chrome), тот же PID, то же гашение по PID.
    # Потоки съёмщика — В ФАЙЛЫ, а не в трубы: в ограниченном окружении запуск процесса
    # с трубами на stdio отказывает (EPERM), а рендеру это безразлично — он пишет кадры
    # в stdout, и какая под ним труба, ему знать не нужно.
    env = dict(os.environ)
    env["REELSI_CAPTURE_CHROME_APP"] = "node"
    env["FAKE_CHROME_FLAGS_FILE"] = str(tmp_path / "fake_flags.json")
    cmd = _python_capture_cmd(tmp_path, chrome=str(stand))
    cmd[cmd.index("--url") + 1] = page.as_uri()
    cmd[cmd.index("--frames") + 1] = str(frames)
    cmd[cmd.index("--profile") + 1] = str(profile)
    frames_file = tmp_path / "кадры.bin"
    err_file = tmp_path / "лог.txt"
    with open(frames_file, "wb") as fo, open(err_file, "wb") as fe:
        rc = subprocess.call(cmd, stdout=fo, stderr=fe, env=env, timeout=120)

    data = frames_file.read_bytes()
    err = err_file.read_bytes().decode("utf-8", "replace")
    assert rc == 0, f"съёмщик вышел кодом {rc} на успешном прогоне:\n{err[-2000:]}"
    got, i = [], 0
    while i + 4 <= len(data):
        size = struct.unpack(">I", data[i:i + 4])[0]
        got.append(data[i + 4:i + 4 + size])
        i += 4 + size
    assert len(got) == frames, f"кадров пришло {len(got)} из {frames}"
    assert all(f == got[0] for f in got), "кадры разъехались — формат потока сломан"
    assert f"#done {frames}" in err, err
    assert "#timing" in err, f"съёмщик не напечатал разбивку времени: {err[-800:]}"

    # ФЛАГИ БРАУЗЕРА — контракт командной строки. Фоновые сервисы Chrome (мастер
    # оптимизации с моделью, подсказки, перевод, MediaRouter, регистрация в GCM) в логе
    # владельца были видны как `Created TensorFlow Lite XNNPACK delegate` и
    # `registration_request … PHONE_REGISTRATION_ERROR`: это время старта страницы и
    # лишний шум. Проверяются ровно те флаги, что уезжают в командной строке: загрузку
    # модели выключает `OptimizationGuideModelDownloading`, а регистрацию в GCM — сама
    # служба push-сообщений (`PushMessaging`) вместе с расширениями и приложениями по
    # умолчанию, которые её и заводят (`--disable-extensions`, `--disable-default-apps`,
    # `--disable-component-extensions-with-background-pages`).
    flags = json.loads(
        (tmp_path / "fake_flags.json").read_text(encoding="utf-8"))["argv"]
    flat = " ".join(flags)
    for flag in ("--headless=new", "--no-first-run",
                 "--no-default-browser-check", "--disable-background-networking",
                 "--disable-component-update", "--disable-sync",
                 "--disable-domain-reliability", "--metrics-recording-only",
                 "--mute-audio", "--disable-extensions", "--disable-default-apps",
                 "--disable-component-extensions-with-background-pages"):
        assert flag in flags, f"в командной строке браузера нет {flag}: {flat}"
    # АППАРАТНАЯ ОТРИСОВКА: кадр — самая дорогая часть съёмки, и собирать его
    # процессором незачем, когда кодирует всё равно видеокарта. `--disable-gpu` тут
    # стоял раньше; вместе с ним уходит и программный путь Chrome, если карты нет, —
    # `--enable-unsafe-swiftshader` оставляет его запасным. Бэкенд ANGLE — НЕ константа:
    # его называет Python по платформе (ключ `--angle`), и жёсткий `d3d11` уводил Linux
    # в программный SwiftShader (tests/test_webrender_fixes.py).
    import core.webrender as wr

    for flag in ("--enable-gpu", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"):
        assert flag in flags, f"нет флага аппаратной отрисовки {flag}: {flat}"
    assert "--use-angle=%s" % wr.angle_backend() in flags, \
        f"нет флага ANGLE для этой платформы: {flat}"
    assert "--disable-gpu" not in flags, \
        f"аппаратная отрисовка снова выключена: {flat}"
    feats = next((f.split("=", 1)[1] for f in flags if f.startswith("--disable-features=")), "")
    assert feats, f"не выключены фоновые службы браузера: {flat}"
    for feature in ("OptimizationGuideModelDownloading", "OptimizationHints",
                    "Translate", "MediaRouter", "PushMessaging"):
        assert feature in feats.split(","), f"не выключена фоновая служба {feature}: {feats}"
    assert any(f.startswith("--user-data-dir=") for f in flags), flags
    assert any(f.startswith("--remote-debugging-port=") for f in flags), flags


def test_gpu_fallback_switch_is_honoured(tmp_path, monkeypatch):
    """`REELSI_CAPTURE_NO_GPU=1` возвращает программный путь (дверь на случай брака карты).

    Флаги аппаратной отрисовки — не догма: на чужой машине аппаратный кадр может врать
    (пустой снимок, чужой цвет). Тогда рендер обязан уметь вернуться на прежний
    программный путь переменной окружения, не правя код.

    Chrome здесь НЕ запускается: проверяется одна функция флагов — та же, что зовёт
    `launchChrome`.
    """
    cap = os.path.join(ROOT, "core", "webrender", "capture.mjs")
    src = _read(cap)
    assert "REELSI_CAPTURE_NO_GPU" in src, "нет двери возврата на программную отрисовку"
    # Флаги собираются ОДНИМ выражением: без карты — прежний `--disable-gpu`, с картой —
    # разрешение аппаратной отрисовки. Проверяем на самом файле, не поднимая браузер.
    # Хвост ветки берётся по её ПОСЛЕДНЕМУ флагу: бэкенд ANGLE теперь подставляется
    # ключом (`...(ANGLE ? ['--use-angle=' + ANGLE] : [])`), и прежняя регулярка
    # обрывалась на первой `]` — внутри подстановки.
    m = re.search(r"REELSI_CAPTURE_NO_GPU \? \[('--disable-gpu')\] : \[(.*?"
                  r"--enable-unsafe-swiftshader')", src, re.S)
    assert m, "сборка флагов графики изменилась — проверять нечего"
    assert "--disable-gpu" in m.group(1)
    branch = m.group(2)
    for flag in ("'--enable-gpu'", "'--ignore-gpu-blocklist'",
                 "'--enable-unsafe-swiftshader'"):
        assert flag in branch, f"в аппаратной ветке нет {flag}: {branch}"
    # Бэкенд ANGLE — из ключа `--angle`, а не жёсткой константой: платформу называет
    # Python (см. `core.webrender.angle_backend`), и `d3d11` вне Windows не существует.
    assert "'--use-angle=' + ANGLE" in branch, branch


def test_gpu_report_goes_to_the_log():
    """Съёмщик печатает, ЧЕМ рисует, — строкой `#gpu`, и она не засоряет хвост ошибки.

    Локальная настройка ничего не значит: аппаратная отрисовка может не достаться
    (карта в списке блокировки, драйвер отказал), и тогда Chrome уходит на программный
    путь. По логу это должно быть видно — иначе «почему долго» ищут заново.
    """
    cap = os.path.join(ROOT, "core", "webrender", "capture.mjs")
    src = _read(cap)
    assert "SystemInfo.getInfo" in src, "съёмщик не спрашивает браузер про отрисовку"
    assert "#gpu " in src, "нет строки лога про отрисовку"
    # Эта же строка описана и в шапке съёмщика — формат у обеих сторон один.
    assert "#gpu" in src.split("// ------", 1)[0], "формат строки не описан в шапке"
    assert "#gpu" in _read(os.path.join(ROOT, "core", "webrender.py")), \
        "Python не знает про строку #gpu и утащит её в хвост диагностики"


# Поддельный Chrome: отвечает по протоколу DevTools ровно тем, что нужно съёмщику.
# Ни одного внешнего модуля — как у самого съёмщика. Кадр отдаётся крошечным JPEG.
_FAKE_CHROME_JS = r"""
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const FRAME = Buffer.from('__FRAME_B64__', 'base64');
// Порт отладочного протокола стенд берёт ИЗ СВОЕЙ КОМАНДНОЙ СТРОКИ — ровно так, как это
// делает Chrome (`--remote-debugging-port=N`). Съёмщик выбирает свободный порт сам и
// ждёт DevTools именно на нём; стенд, слушающий «свой» порт, не нашёлся бы никогда.
const PORT = Number((process.argv.find((a) => a.startsWith('--remote-debugging-port=')) || '')
  .split('=')[1] || 0);

// «Браузер»: живой процесс, которого съёмщик потом гасит по PID.
const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000);'],
  { stdio: 'ignore' });
// Аргументы, с которыми стенд позвали, — в файл: по ним тест сверяет контракт
// командной строки браузера (флаги), не поднимая настоящий Chrome.
if (process.env.FAKE_CHROME_FLAGS_FILE) {
  writeFileSync(process.env.FAKE_CHROME_FLAGS_FILE,
    JSON.stringify({ argv: process.argv.slice(2) }), 'utf8');
}
process.stderr.write('fake chrome up\n');
function frameWs(text) {
  const payload = Buffer.from(text, 'utf8');
  const head = [0x81];
  if (payload.length < 126) head.push(payload.length);
  else if (payload.length < 65536) head.push(126, payload.length >> 8, payload.length & 255);
  else head.push(127, 0, 0, 0, 0, (payload.length >>> 24) & 255,
    (payload.length >>> 16) & 255, (payload.length >>> 8) & 255, payload.length & 255);
  return Buffer.concat([Buffer.from(head), payload]);
}

function readWs(buf) {
  if (buf.length < 2) return null;
  const opcode = buf[0] & 0x0f;
  let len = buf[1] & 0x7f, off = 2;
  if (len === 126) { if (buf.length < 4) return null; len = buf.readUInt16BE(2); off = 4; }
  else if (len === 127) { if (buf.length < 10) return null; len = Number(buf.readBigUInt64BE(2)); off = 10; }
  const masked = (buf[1] & 0x80) !== 0;
  let mask = null;
  if (masked) { if (buf.length < off + 4) return null; mask = buf.subarray(off, off + 4); off += 4; }
  if (buf.length < off + len) return null;
  const payload = Buffer.from(buf.subarray(off, off + len));
  if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
  return { opcode, payload, rest: buf.subarray(off + len) };
}

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  if (req.url.startsWith('/json/version')) {
    res.end(JSON.stringify({ Browser: 'fake',
      webSocketDebuggerUrl: 'ws://127.0.0.1:' + PORT + '/devtools/browser/f' }));
    return;
  }
  // Адрес страницы — С ПОРТОМ: съёмщик подключается по нему как есть, и адрес без
  // порта увёл бы его на 80-й.
  res.end(JSON.stringify([
    { type: 'page', url: 'about:blank',
      webSocketDebuggerUrl: 'ws://127.0.0.1:' + PORT + '/devtools/page/f' },
  ]));
});

server.on('upgrade', (req, socket) => {
  const accept = createHash('sha1')
    .update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64');
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
    + 'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
  // PID и порт поддельного «браузера» уезжают в stderr: тест проверяет, что съёмщик
  // гасит по PID именно его (и что порт вообще был запрошен).
  process.stderr.write('#fake-port ' + server.address().port + '\n');
  let buf = Buffer.alloc(0);
  const send = (obj) => socket.write(frameWs(JSON.stringify(obj)));
  socket.on('data', (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    for (;;) {
      const parsed = readWs(buf);
      if (!parsed) break;
      buf = parsed.rest;
      if (parsed.opcode === 8) { socket.end(); return; }
      if (parsed.opcode !== 1) continue;
      const msg = JSON.parse(parsed.payload.toString('utf8'));
      const expr = String((msg.params && msg.params.expression) || '');
      if (msg.method === 'Page.captureScreenshot') {
        send({ id: msg.id, result: { data: FRAME.toString('base64') } });
      } else if (msg.method === 'Runtime.evaluate') {
        // Значение выбирается по САМОМУ запросу, как ответил бы настоящий Chrome:
        // готовность — строкой, размер сцены — JSON-ом, отрисовка кадра — временем.
        let value = null;
        if (expr.includes('reelsiRenderReady')) value = 'ready';
        else if (expr.includes('getBoundingClientRect')) {
          value = JSON.stringify({ x: 0, y: 0, w: 1080, h: 1920 });
        } else if (expr.includes('ipvRenderAt')) value = 0;
        send({ id: msg.id, result: { result: { type: 'string', value } } });
      } else {
        send({ id: msg.id, result: {} });
      }
    }
  });
});

server.listen(PORT, '127.0.0.1', () => {
  process.stderr.write('#fake-port ' + server.address().port + '\n');
});
server.on('error', (e) => process.stderr.write('#fake-error ' + String(e) + '\n'));
process.on('SIGTERM', () => { child.kill('SIGKILL'); process.exit(0); });
"""

# Крошечный настоящий JPEG 1x1 (ffmpeg его читает как mjpeg-поток): поток проверяется
# на уровне «кадр == 4 байта длины + данные», а не на содержимом картинки.
_TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


# --------------------------------------------------------------------------- #
# Прокси кадра: отдельный файл, отдельный ключ, ключевой кадр каждый
# --------------------------------------------------------------------------- #
def test_render_proxy_has_its_own_key_and_keyframes():
    """Прокси рендера — ОТДЕЛЬНЫЙ файл от превью, собранный с `-g 1` (все кадры ключевые).

    Превью играет длинным GOP ради размера файла, рендер просит у <video> то один, то
    другой кадр исходника: на длинном GOP каждая такая перемотка гоняет декодер от
    ближайшего ключевого кадра (живой прогон — 1.3 с на кадр). Поэтому у рендера свой
    путь (`render_proxy_path`) и свой ключ кэша, а собирается он ПАРАМЕТРОМ того же
    сборщика (`allintra`), а не второй копией кода.
    """
    import core.draftrender as dr

    src = os.path.join(HERE, "кам1.mp4")          # файла нет: берётся только его stat
    with open(src, "wb") as f:
        f.write(b"x")
    try:
        tdir = tempfile.mkdtemp(prefix="webrender_proxy_")
        prev = dr.preview_path(src, 1080, tdir)
        rend = dr.render_proxy_path(src, 1080, tdir)
        assert prev != rend, "прокси рендера и превью — один и тот же файл"
        assert os.path.basename(rend).startswith("pv_r"), os.path.basename(rend)
        assert dr.RENDER_PROXY_RE.match(os.path.basename(rend)), os.path.basename(rend)
        # Ключ кэша свой: чужой размер кадра или смена исходника его меняют.
        assert rend != dr.render_proxy_path(src, 720, tdir), "ключ не зависит от размера"
        # Параметр ключевых кадров — у ТОГО ЖЕ сборщика: аргументы кодека в одном месте.
        assert "-g" not in dr._x264_args(q=23), "у превью появился ключевой кадр каждый"
        allintra = dr._x264_args(q=23, allintra=True)
        assert allintra[allintra.index("-g") + 1] == "1", allintra
    finally:
        os.remove(src)


def test_preview_proxy_api_asks_for_the_render_catcher():
    """`/api/preview_proxy` с `allintra` строит план из all-intra прокси (и наоборот).

    Кадры камер рендер без AE берёт из ИСХОДНИКОВ (core/webrender), поэтому all-intra
    разновидность ему больше не нужна. Дверь осталась общей на всех, и параметр обязан
    доезжать до плана: иначе попросивший all-intra молча получил бы прокси превью и
    снова декодировал от далёкого ключевого кадра.
    """
    import api.previewproxy as pp
    import core.draftrender as dr

    tmp = tempfile.mkdtemp(prefix="webrender_api_")
    xml = os.path.join(tmp, "clip.xml")
    with open(xml, "w", encoding="utf-8") as f:
        f.write("<x/>")
    cam = os.path.join(tmp, "кам1.mp4")
    with open(cam, "wb") as f:
        f.write(b"x")
    import core.xml2ae as x2a
    real_edl = x2a.virtual_edl
    x2a.virtual_edl = lambda p, **kw: {"cams": [{"path": cam}]}      # type: ignore[assignment]
    try:
        plain, tdir = pp._preview_proxy_plan(xml, 1080, False)
        allin, _ = pp._preview_proxy_plan(xml, 1080, True)
        assert plain[0][1] != allin[0][1], "план не различает прокси превью и рендера"
        assert allin[0][1] == dr.render_proxy_path(cam, 1080, tdir), allin
        assert plain[0][1].endswith(".mp4") and os.path.basename(plain[0][1]).startswith("pv_")
    finally:
        x2a.virtual_edl = real_edl                                # type: ignore[assignment]


def test_render_proxy_builder_forces_all_intra_x264(monkeypatch):
    """Сборка прокси рендера: ffmpeg получает `-g 1` и НЕ получает аппаратный кодек.

    Ключевой кадр каждый — режим x264; у аппаратных кодировщиков он зовётся иначе
    (intra-refresh) и на части драйверов не заводится вовсе, а тут важна гарантия
    формата, а не скорость сборки прокси. Проверяется на РЕАЛЬНОЙ командной строке,
    которую сборщик отдаёт ffmpeg.
    """
    import core.draftrender as dr

    tmp = tempfile.mkdtemp(prefix="webrender_build_")
    src = os.path.join(tmp, "кам1.mp4")
    with open(src, "wb") as f:
        f.write(b"x")
    dst = os.path.join(tmp, "pv_r000000000000.mp4")
    seen = []
    monkeypatch.setattr(dr, "_display_dims", lambda s: (1080, 1920, False))
    monkeypatch.setattr(dr, "hw_encoder", lambda refresh=False: "h264_nvenc")

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        with open(cmd[-1], "wb") as f:                 # «ffmpeg собрал прокси»
            f.write(b"proxy")
        return __import__("subprocess").CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dr, "_run_ff", fake_run)
    got = dr.build_render_proxy(src, dst, height=1080, emit=lambda *a, **k: None)
    assert got == dst and os.path.isfile(dst), got
    cmd = seen[0]
    assert "-g" in cmd and cmd[cmd.index("-g") + 1] == "1", cmd
    assert "libx264" in cmd, f"прокси рендера ушёл на аппаратный кодек: {cmd}"
    assert "-an" in cmd, f"в прокси рендера попал звук: {cmd}"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264", cmd


class _Cp1252Out:
    """sys.stdout консоли Windows до переключения кодировки: cp1252, кириллицы нет.

    Как настоящий TextIOWrapper, умеет `reconfigure` — именно им CLI переключает
    кодировку потока. Без этого вызова любая кириллица тут падает `charmap`, и тест
    воспроизводит дефект один в один.
    """

    def __init__(self, encoding="cp1252"):
        self.encoding = encoding
        self.errors = "strict"
        self.buf = io.BytesIO()
        self.reconfigured = 0

    def reconfigure(self, encoding=None, errors=None):
        self.reconfigured += 1
        if encoding is not None:
            self.encoding = encoding
        if errors is not None:
            self.errors = errors

    def write(self, s):
        self.buf.write(s.encode(self.encoding, self.errors))   # cp1252 тут и падает
        return len(s)

    def flush(self):
        pass

    def decoded(self):
        return self.buf.getvalue().decode("utf-8", "replace")


def test_cli_prints_russian_into_cp1252_console(tmp_path, monkeypatch, capsys):
    """CLI печатает русский лог и на cp1252-консоли — иначе рендер падает на первой строке.

    Воспроизведение дефекта: `py -3.10 -m core.webrender` в консоли, где stdout — cp1252,
    падал `UnicodeEncodeError: 'charmap' codec can't encode characters` ещё до съёмки.
    Печатать CLI обязан тем же путём, что остальные точки входа проекта, а кодировку
    потока переключать до первой строки лога. Chrome и ffmpeg подменены: проверяется
    вывод, а не рендер.

    Язык задан ЯВНО (`REELSI_LANG=ru`): `console_emit` — общий путь логов, и строку он
    переводит по словарю, а язык берёт из системы. На машине с английским языком
    интерфейса вывод был бы английским, и тест падал бы не по делу: проверяется здесь
    кодировка потока, а не перевод.
    """
    import core.webrender as cw
    from core import app_meta

    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta._UI_LANG_CACHED = None

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    seen = {}

    def fake_render(*a, **kw):
        seen.update(kw)
        return {"ok": True, "cancelled": False, "out": str(out), "frames": 3,
                "fps": 60.0, "w": 1080, "h": 1920}

    monkeypatch.setattr(cw, "render", fake_render)
    fake = _Cp1252Out()
    monkeypatch.setattr(sys, "stdout", fake)
    rc = cw.main([str(xml), "--out", str(out), "--dur", "1"])
    captured = capsys.readouterr()

    assert rc == 0, rc
    assert fake.reconfigured == 1, "CLI не переключил кодировку потока — cp1252 остался"
    assert fake.encoding == "utf-8", fake.encoding
    text = fake.decoded() + captured.out
    assert "готово" in text, f"русской строки в выводе нет: {text!r}"
    # Хвост — размер кадра и частота: «60x1080» читалось как «60 на 1080» и не говорило
    # ни разрешения, ни кадров в секунду.
    assert f"готово: {out} {DASH} кадров 3, 1080x1920, 60 к/с" in text, text
    # emit CLI — общий путь логов проекта, а не свой print мимо перевода и кодировки.
    assert seen.get("emit") is console_emit, seen.get("emit")
    # Вторая половина контракта: без переключения кодировки тот же вывод падает —
    # значит, тест ловит именно дефект, а не «повезло с кодировкой хоста».
    with pytest.raises(UnicodeEncodeError):
        _Cp1252Out().write(f"готово: x {DASH} кадров 3")


# --------------------------------------------------------------------------- #
# Контракт «страница рендера ↔ съёмщик»: имена и их тип
# --------------------------------------------------------------------------- #
# Живой прогон: под `window.reelsiRenderPaint` на странице лежал ОБЪЕКТ статистики, а
# съёмщик звал имя как функцию — «TypeError: window.reelsiRenderPaint is not a function»
# на первом же кадре, и рендер не начинался. Проверка ниже берёт имена НЕ списком
# руками, а из самого съёмщика (capture.mjs и timing.mjs) вместе с тем, зовут имя или
# читают, а стенд грузит страницу КАК ОНА ЕСТЬ: реальные static/app/*.js (кроме
# 99-boot.js — его на странице рендера нет) и встроенный запуск из templates/render.html.
#
# Мини-браузер — ровно то, чего касается загрузка файлов интерфейса (document,
# localStorage, location, слушатели), и ни одного запроса наружу: fetch отвечает
# отказом, как на странице без сервера. Имена и их тип выставляет загрузка скриптов, а
# не сеть, — этого хватает.
_PAGE_STAND_JS = r"""
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';

const dir = process.argv[2];                          // папка с файлами static/app
const html = readFileSync(process.argv[3], 'utf8');   // страница рендера, как её отдал сервер
const want = JSON.parse(process.argv[4] || '[]');     // имена, которые проверяет тест

// Элемент-пустышка: панелей у страницы рендера нет, но общий код вешает обработчики на
// их элементы прямо при загрузке (см. служебный каркас в templates/render.html).
function makeEl(id) {
  return {
    id: id || '', value: '', textContent: '', innerHTML: '', outerHTML: '', title: '',
    checked: false, disabled: false, hidden: false, files: [], children: [], childNodes: [],
    style: { setProperty() {}, removeProperty() {}, getPropertyValue() { return ''; } },
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    appendChild(c) { return c; }, insertBefore(c) { return c; }, removeChild(c) { return c; },
    remove() {}, setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    hasAttribute() { return false; }, querySelector() { return null; },
    querySelectorAll() { return []; }, closest() { return null; }, contains() { return false; },
    focus() {}, blur() {}, click() {}, scrollIntoView() {}, insertAdjacentHTML() {},
    play() { return Promise.resolve(); }, pause() {}, load() {}, cloneNode() { return makeEl(id); },
    animate() { return {}; },
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
    },
  };
}

const doc = {
  readyState: 'complete', title: '', hidden: false, cookie: '',
  body: makeEl('body'), head: makeEl('head'), documentElement: makeEl('html'),
  fonts: { ready: Promise.resolve(), load() { return Promise.resolve([]); },
           add() {}, check() { return true; } },
  createElement: (tag) => makeEl(tag),
  createElementNS: (ns, tag) => makeEl(tag),
  createDocumentFragment: () => makeEl('#fragment'),
  getElementById: (id) => makeEl(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  createTreeWalker: () => ({ nextNode: () => null }),
};

globalThis.window = globalThis;
globalThis.self = globalThis;
globalThis.document = doc;
// navigator в node объявлен только геттером — переопределяем своим свойством.
Object.defineProperty(globalThis, 'navigator', {
  configurable: true, writable: true,
  value: { userAgent: 'node-стенд', language: 'ru', languages: ['ru'],
           clipboard: { writeText() { return Promise.resolve(); } } },
});
globalThis.location = { search: '', href: 'http://127.0.0.1:5001/render', protocol: 'http:',
                        host: '127.0.0.1:5001', reload() {}, assign() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {}, clear() {},
                            key: () => null, length: 0 };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {},
                                 removeEventListener() {}, removeListener() {} });
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.fetch = () => Promise.reject(new Error('стенд без сети'));
globalThis.alert = () => {};
globalThis.confirm = () => false;
globalThis.prompt = () => null;
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.NodeFilter = { SHOW_TEXT: 4 };
globalThis.devicePixelRatio = 1;
globalThis.innerWidth = 1080;
globalThis.innerHeight = 1920;
globalThis.screen = { width: 1080, height: 1920 };
globalThis.Image = class { constructor() { this.style = {}; } addEventListener() {} };

const errors = [];
const rejections = [];
const files = [];
process.on('unhandledRejection', (e) => rejections.push(String((e && e.message) || e)));

// Скрипты страницы — В ТОМ ЖЕ ПОРЯДКЕ, в каком их видит браузер: сначала встроенные
// константы, потом файлы интерфейса, потом встроенный запуск страницы.
const tag = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
let m;
while ((m = tag.exec(html))) {
  const src = (m[1].match(/\bsrc="([^"]+)"/) || [])[1];
  if (!src) {
    if (!m[2].trim()) continue;
    try { vm.runInThisContext(m[2], { filename: 'render.html:inline' }); }
    catch (e) { errors.push('встроенный скрипт страницы упал: ' + ((e && e.message) || e)); }
    continue;
  }
  const name = path.basename(src.split('?')[0]);
  files.push(name);
  let code;
  try { code = readFileSync(path.join(dir, name), 'utf8'); }
  catch (e) { errors.push('файл интерфейса не читается: ' + name); continue; }
  try { vm.runInThisContext(code, { filename: name }); }
  catch (e) { errors.push('файл ' + name + ' упал при загрузке: ' + ((e && e.message) || e)); }
}

// Хвост запуска страницы асинхронный: даём ему доехать, чтобы отказ был виден здесь же.
await new Promise((r) => setTimeout(r, 60));

const types = {};
for (const name of want) types[name] = typeof globalThis[name];
process.stdout.write(JSON.stringify({ files, errors, rejections, types }) + '\n');
"""

# Имя в комментарии — не обращение: `window.reelsiRenderPaint` в пояснении не значит,
# что съёмщик его зовёт.
_JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _catcher_globals(paths=(CAPTURE, TIMING)) -> dict[str, str]:
    """Все `window.reelsi*` съёмщика: имя → `call` (зовут) или `read` (читают).

    Разбор идёт по настоящему коду съёмщика, а не по списку в тесте: переименование или
    новая дверь на его стороне попадают в контракт сами.
    """
    out: dict[str, str] = {}
    for path in paths:
        src = _JS_COMMENT.sub("", _read(path))
        for m in re.finditer(r"window\.(reelsi\w*)(\s*\()?", src):
            call = bool(m.group(2))
            if call or m.group(1) not in out:
                out[m.group(1)] = "call" if call else "read"
    return out


def _page_stand(html, app_dir, names):
    """Что оказалось в `window` после загрузки страницы рендера (node-стенд).

    `app_dir` — папка с файлами static/app: в мутации это испорченная копия, поэтому
    подменять исходники не нужно.
    """
    with tempfile.TemporaryDirectory(prefix="webrender_page_") as d:
        page = os.path.join(d, "render.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(html)
        stand = os.path.join(d, "stand.mjs")
        with open(stand, "w", encoding="utf-8") as f:
            f.write(_PAGE_STAND_JS)
        p = subprocess.run(["node", stand, app_dir, page, json.dumps(sorted(names))],
                           capture_output=True, text=True, encoding="utf-8-sig",
                           errors="replace", timeout=180, cwd=d)
    assert p.returncode == 0, (p.stderr or p.stdout)[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def _page_contract_problems(html, app_dir, wanted):
    """Расхождения контракта «страница ↔ съёмщик» списком строк: пусто — всё сошлось."""
    got = _page_stand(html, app_dir, wanted)
    problems = list(got["errors"]) + ["страница упала: " + r for r in got["rejections"]]
    for name in sorted(wanted):
        seen = got["types"].get(name, "undefined")
        if wanted[name] == "call":
            if seen != "function":
                problems.append(
                    f"{name}: съёмщик зовёт его как функцию, а на странице рендера это {seen}")
        elif seen == "undefined" and not re.search(r"window\.%s\s*=" % name, html):
            # Чтение: имя либо живёт в интерфейсе, либо его ставит сама страница. Флаги
            # готовности появляются только после плана — их место проверяется по тексту.
            problems.append(f"{name}: съёмщик читает его, а на странице рендера такого имени нет")
    return problems, got


def _served_render_page(client):
    """Страница рендера, как её отдаёт сервер: те же скрипты и тот же встроенный запуск."""
    return client.get("/render",
                      query_string={"xml": "x.xml", "body": "abc"}).get_data(as_text=True)


@node
def test_render_page_gives_the_catcher_every_door_it_calls(client):
    """Съёмщик и страница рендера сходятся по каждому имени — и по типу.

    Живой прогон: `window.reelsiRenderPaint` был на странице ОБЪЕКТОМ статистики, а
    съёмщик звал его как функцию — «TypeError: window.reelsiRenderPaint is not a
    function» на первом кадре. Имена берутся из самого съёмщика (capture.mjs и
    timing.mjs) вместе с тем, зовут их или читают, а стенд грузит страницу как она есть:
    реальные файлы static/app/ (без запуска основного интерфейса — 99-boot.js на
    странице рендера нет) и её встроенный запуск. Флаги готовности страница ставит после
    плана, поэтому их проверяем по её тексту.
    """
    doors = _catcher_globals()
    # Сторож на сам разбор: переименование в съёмщике не должно сделать контракт пустым.
    assert doors, "в съёмщике не нашлось ни одного window.reelsi* — разбор сломался"
    assert doors.get("reelsiRenderPaint") == "call", doors
    for flag in ("reelsiRenderReady", "reelsiRenderError", "reelsiRenderInfo"):
        assert doors.get(flag) == "read", doors

    html = _served_render_page(client)
    problems, got = _page_contract_problems(html, os.path.dirname(JS85), doors)
    assert not problems, "\n".join(problems)
    # Стенд действительно грузил интерфейс: иначе «всё сошлось» не значит ничего.
    assert "85-inserts-view.js" in got["files"], got["files"]
    assert "99-boot.js" not in got["files"], got["files"]
    # И обратная сторона: имя, которого на странице нет, проверка обязана заметить.
    absent = dict(doors, reelsiRenderNothing="read")
    missing, _ = _page_contract_problems(html, os.path.dirname(JS85), absent)
    assert any("reelsiRenderNothing" in p for p in missing), missing
    # Флаги готовности — в тексте страницы: съёмщик ждёт именно их появления.
    for flag in ("reelsiRenderReady", "reelsiRenderInfo"):
        assert re.search(r"window\.%s\s*=" % flag, html), f"страница не выставляет {flag}"


@node
def test_mutation_stats_object_on_the_paint_door_is_caught(client, tmp_path):
    """Мутация: вернуть на дверь объект статистики — контракт обязан покраснеть.

    Ровно этот дефект и был в живом прогоне. Стенд собирается из ИСПОРЧЕННОЙ КОПИИ
    static/app/, а страница берётся та же, что отдаёт сервер, — так сторож не сможет
    молча перестать ловить расхождение имён.
    """
    src = _read(JS85)
    marker = "window.reelsiRenderPaint=ipvRenderPaint;"
    assert marker in src, "дверь отрисовки переименована — мутация устарела"
    app = tmp_path / "app"
    shutil.copytree(os.path.dirname(JS85), app)
    (app / "85-inserts-view.js").write_text(
        src.replace(marker, "window.reelsiRenderPaint=IPV_RT;"), encoding="utf-8")

    problems, got = _page_contract_problems(
        _served_render_page(client), str(app), _catcher_globals())
    assert got["types"].get("reelsiRenderPaint") == "object", (
        f"стенд не увидел объект статистики на двери: {got['types'].get('reelsiRenderPaint')}")
    assert any("reelsiRenderPaint" in p for p in problems), (
        "мутация не поймана: с объектом статистики на двери контракт остался зелёным: "
        f"{problems}")


def test_render_page_boot_names_exist_in_the_interface():
    """Запуск страницы рендера зовёт только то, что в интерфейсе есть.

    У страницы нет своей копии отрисовки — она подаёт готовые значения в общие
    переменные и зовёт общие функции. Опечатка в имени (`PVPx`, `ipvPlanWh`) в браузере
    выглядела бы как «рендер не начался», и увидеть её было бы негде: страница рендера
    открывается один раз и без панелей. Проверяем имена по исходникам интерфейса.
    """
    from core.app_meta import app_js_text

    src = app_js_text()
    page = _read(os.path.join(ROOT, "templates", "render.html"))
    # Переменные, которые страница выставляет перед запуском предпросмотра.
    for name in ("CURSTYLE", "CLIPS", "PVPX", "IPVMODE"):
        assert re.search(r"\b%s\s*(?:\.\s*\w+\s*)?=" % name, page), \
            f"страница не выставляет {name}"
        assert re.search(r"\b(?:let|const|var)\s+[^;\n]*\b%s\b" % name, src), (
            f"переменной {name} нет в static/app/ — страница пишет в никуда")
    assert "typeof IPV_BODY" in src, "в превью нет двери IPV_BODY — тело сборки не подставить"
    for fn in ("loadSpeakers", "loadFonts", "ipvPlanWH", "ipvOpen", "errText"):
        assert f"{fn}(" in page, f"страница не зовёт {fn}"
        assert re.search(r"(?:async\s+)?function\s+%s\s*\(" % fn, src), (
            f"функции {fn} нет в static/app/ — страница упадёт на её вызове")
    # Кадр просит не страница, а съёмщик — через Runtime.evaluate.
    cap = _read(CAPTURE)
    assert "ipvRenderAt(" in cap, "съёмщик не зовёт отрисовку кадра"
    assert re.search(r"(?:async\s+)?function\s+ipvRenderAt\s*\(", src), \
        "функции ipvRenderAt нет в static/app/ — съёмщик позовёт пустоту"
    # Готовность страницы читает съёмщик — имена обязаны совпадать.
    for flag in ("reelsiRenderReady", "reelsiRenderError", "reelsiRenderInfo"):
        assert flag in page, f"страница не выставляет {flag}"
        assert flag in cap, f"съёмщик не знает про {flag}"


def test_capture_script_is_dependency_free():
    """Съёмщик не тянет пакетов: встроенные модули node и свои файлы рядом.

    Свои относительные импорты разрешены и обязаны существовать на диске: разбивка
    времени (`#timing`) печатается съёмщиком, а разбирается Python, и формат её живёт
    в ОДНОМ файле — `webrender/timing.mjs`. Импорт в никуда (`./timing.mjs` без файла)
    выглядел бы как «съёмщик не запускается вовсе».
    """
    src = _read(CAPTURE)
    imports = re.findall(r"^import\s+.*?from\s+'([^']+)'", src, re.M)
    assert imports, "в съёмщике нет ни одного импорта — проверь разбор"
    for mod in imports:
        if mod.startswith("."):
            assert os.path.isfile(os.path.join(os.path.dirname(CAPTURE), os.path.basename(mod))), \
                f"съёмщик импортирует свой файл, которого нет: {mod}"
            continue
        assert mod.startswith("node:"), f"съёмщик тянет внешний модуль: {mod}"
    assert "timing.mjs" in src and "timingLine" in src, \
        "съёмщик не печатает разбивку времени общей функцией (webrender/timing.mjs)"
    assert "remote-debugging-port" in src, "съёмщик не поднимает Chrome с отладочным портом"
    assert "Page.captureScreenshot" in src, "съёмщик не снимает кадр"
    assert "#chrome-pid" in src, "съёмщик не сообщает PID своего Chrome — отмену нечем гасить"
    # Код выхода съёмщика выставляется ТОЛЬКО на завершении main: process.exit внутри
    # гашения Chrome отдавал код, выставленный до того, как станет известен результат.
    assert re.search(r"\.then\(\(\)\s*=>\s*\{[^}]*process\.exit\(0\)", src, re.S), \
        "успешное завершение съёмщика не выставляет код 0 явно"


def test_render_page_is_served_with_scene_and_without_main_boot(client):
    """Страница рендера отдаётся сервером: сцена есть, запуска основного UI нет.

    Съёмщик открывает `/render` в Chrome и ждёт `window.reelsiRenderReady`. Браузера
    в проверке нет, но страницу и её скрипты видно и так — и это ровно то, что от неё
    требуется: те же файлы интерфейса (кроме 99-boot.js — он восстанавливает состояние
    чужого интерфейса и пишет его зеркало) и сцена в кадре.
    """
    r = client.get("/render", query_string={"xml": "x.xml", "body": "abc"})
    html = r.get_data(as_text=True)
    srcs = re.findall(r'/static/app/([\w.-]+)', html)
    assert "85-inserts-view.js" in srcs, "страница рендера не грузит отрисовку предпросмотра"
    assert "99-boot.js" not in srcs, \
        "страница рендера грузит запуск основного интерфейса — он пишет чужое состояние"
    for elem in ('id="ipvstage"', 'id="ipvins"', 'id="ipvsub"', 'id="ipvintro"'):
        assert elem in html, f"на странице рендера нет {elem}"
    assert "reelsiRenderReady" in html, "страница не сообщает о готовности — съёмщик не начнёт"
    # Встроенный запуск страницы обязан быть синтаксически целым: браузера в проверке нет,
    # а опечатка в нём выглядела бы как «рендер не стартует» без единой строки в логе.
    inline = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert inline.strip(), "у страницы рендера нет встроенного запуска"
    with tempfile.TemporaryDirectory(prefix="webrender_page_") as d:
        path = os.path.join(d, "page.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(inline)
        p = subprocess.run(["node", "--check", path], capture_output=True, text=True,
                           encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, f"запуск страницы рендера не разбирается: {p.stderr}"
