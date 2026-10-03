# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Отрисовка мозаики (цензора) на видеовставках при скрабе на паузе.

Стенд проверяет:
1. Видеоэлемент вставки подписывается на события `seeked` и `loadeddata`.
2. При скрабе на паузе (плеер на паузе, срабатывает `seeked`) мозаика
   принудительно перерисовывается поверх текущего кадра без задержки и
   не требует воспроизведения (`timeupdate`).
3. На `seeked` холст получает вызов `drawImage` от декодированного кадра видео.

Запуск: py -3.10 -m pytest tests/test_ins_mosaic_pause.py -q
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
node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

FUNCS = (
    "normInsPath", "insVideoFill", "insVidCache", "insVidDimsMap", "insVidKey",
    "insVideoEl", "insVidFree", "insVidDimsGet", "insVidDimsPut", "insVidPlanDims",
    "ipvInsDims", "insVidKeep", "insVidSweep", "insVidFreeAll", "ipvPlanWH",
    "ipvIns", "insSD", "insImgURL", "insCardKey", "ipvInsPlace", "ipvOverlayPlan",
    "ipvInsDraw", "ipvInsSrc", "ipvInsMosaics", "ipvPixelate", "drawMosaic",
)


def _func(src, name):
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
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^const MOSAIC_BLOCK=\d+;", src, re.M)
    assert m, "в исходнике не нашлось объявление MOSAIC_BLOCK"
    return m.group(0) + "\n" + "\n".join(_func(src, n) for n in FUNCS)


_DOM_JS = r"""
function ClassList(el){this.el=el;}
ClassList.prototype.add=function(c){var s=new Set((this.el.className||'').split(/\s+/).filter(Boolean));s.add(c);this.el.className=Array.from(s).join(' ');};
ClassList.prototype.remove=function(c){var s=new Set((this.el.className||'').split(/\s+/).filter(Boolean));s.delete(c);this.el.className=Array.from(s).join(' ');};
ClassList.prototype.contains=function(c){return (this.el.className||'').split(/\s+/).indexOf(c)>=0;};

function El(tag){
  this.tag=tag;this.tagName=tag.toUpperCase();this.children=[];this.style={};this.dataset={};this._attrs={};this._ev={};
  this.className='';this.parentNode=null;this.textContent='';this.classList=new ClassList(this);
  this.muted=false;this.playsInline=false;this.preload='';this.paused=true;
  this.currentTime=0;this.videoWidth=1920;this.videoHeight=1080;this.calls=[];
  this.width=0;this.height=0;
}
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.insertBefore=function(c,ref){c.parentNode=this;var i=ref?this.children.indexOf(ref):-1;
  if(i>=0)this.children.splice(i,0,c);else this.children.push(c);return c;};
El.prototype.removeChild=function(c){var i=this.children.indexOf(c);
  if(i>=0)this.children.splice(i,1);c.parentNode=null;return c;};
Object.defineProperty(El.prototype,'firstChild',{get:function(){return this.children[0]||null;}});
Object.defineProperty(El.prototype,'nextSibling',{get:function(){
  if(!this.parentNode) return null;
  var idx=this.parentNode.children.indexOf(this);
  return idx>=0&&idx+1<this.parentNode.children.length?this.parentNode.children[idx+1]:null;
}});
Object.defineProperty(El.prototype,'clientWidth',{get:function(){return 1080;}});
Object.defineProperty(El.prototype,'clientHeight',{get:function(){return 1920;}});
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
El.prototype.closest=function(sel){return this.parentNode;};
El.prototype.querySelector=function(sel){
  if(sel.indexOf('canvas')>=0){
    for(var i=0;i<this.children.length;i++){
      if(this.children[i].tag==='canvas') return this.children[i];
    }
    return null;
  }
  return null;
};
El.prototype.getContext=function(kind){
  var self=this;
  return {
    imageSmoothingEnabled: true,
    clearRect: function(x,y,w,h){self.calls.push('clearRect');},
    drawImage: function(img,sx,sy,sw,sh,dx,dy,dw,dh){
      self.calls.push('drawImage');
      self.lastDrawn = {
        imgTag: img ? img.tag : null,
        paused: img ? img.paused : null,
        cw: self.width, ch: self.height
      };
    }
  };
};

var document = {
  createElement: function(tag){return new El(tag);},
  querySelectorAll: function(){return [];}
};
var window = {requestAnimationFrame: function(cb){setTimeout(cb, 0);}};
var IPV = {insVids: new Map(), dims: new Map()};
"""


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_ins_video_registers_seeked_and_loadeddata_listeners():
    """Видеовставка регистрирует обработчики loadeddata и seeked для мозаики."""
    code = _js() + _DOM_JS + """
    var vid = insVideoEl('C:/v/clip.mp4');
    var evs = Object.keys(vid._ev);
    console.log(JSON.stringify({
      hasSeeked: evs.indexOf('seeked') >= 0,
      hasLoadedData: evs.indexOf('loadeddata') >= 0,
      events: evs
    }));
    """
    res = _run_node(code)
    assert res["hasSeeked"], f"seeked не зарегистрирован: {res['events']}"
    assert res["hasLoadedData"], f"loadeddata не зарегистрирован: {res['events']}"


@node
def test_mosaic_redraws_on_seeked_while_paused():
    """При перемещении (seeked) на паузе мозаика перерисовывается поверх кадра."""
    code = _js() + _DOM_JS + """
    var vid = insVideoEl('C:/v/clip.mp4');
    vid.paused = true;
    var wr = new El('div');
    wr.appendChild(vid);
    ipvPixelate(wr, vid);

    var cv = wr.querySelector('canvas');
    var callsBefore = cv ? cv.calls.slice() : [];

    // Имитируем скраббинг на паузе
    vid.currentTime = 5.0;
    vid.fire('seeked');

    console.log(JSON.stringify({
      hasCanvas: !!cv,
      callsBefore: callsBefore,
      callsAfter: cv ? cv.calls : [],
      lastDrawn: cv ? cv.lastDrawn : null
    }));
    """
    res = _run_node(code)
    assert "drawImage" in res["callsAfter"], f"Холст не перерисован при seeked: {res['callsAfter']}"
    assert res["lastDrawn"]["cw"] == 1080 and res["lastDrawn"]["ch"] == 1920
    assert len(res["callsAfter"]) > len(res["callsBefore"])

