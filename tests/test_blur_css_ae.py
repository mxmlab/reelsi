# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Смена жёлтого слова и блюр появления: превью теми же ключами, что .jsx.

Разбор эталонного рендера (C1476, собран в AE):

1. **Смена жёлтого слова** (13.6 с). У AE прежнее «НАТУРАЛЬНЫЙ» уже ушло, новое
   «ПРЕДЕЛ» проявляется НА СТРОКЕ субтитров; у нас прежнее висело целиком, а новое
   выезжало ниже. Обе разницы задаёт ОДНА строка данных .jsx — ряд и общий конец связки
   (``row``/``gend``, их считает ``layout._stack_layout``): у слова с ручным разделителем
   (``hl_breaks``) ``gend`` равен его собственному концу, и слой гаснет ровно на появлении
   следующего, а ряд остаётся нулевым (``POSY + row*HL_STEP``, не шаг стопки). Превью
   обязано брать оба числа из плана: своей копии правила «когда прежнее уходит» у него нет.

2. **Блюр появления.** В .jsx уезжает значение стиля как есть — «Blurriness» Gaussian
   Blur (70.4 у жёлтого слова, 26.8 у раскрытия интро), а превью рисует размытие
   фильтром браузера, где число — сигма гауссианы. Подставлять одно вместо другого
   нельзя: замер по тому же рендеру даёт sigma = Blurriness / 4.2
   (``layout.AE_BLUR_PER_SIGMA``), и «КУБИК» на 29.3 с выходил размытым вчетверо
   сильнее AE — буквы не читались вовсе. Перевод считается ОДИН раз в Python и едет в
   план полями ``hl_blur_css`` / ``intro_anims.reveal.blur_css``; в .jsx по-прежнему
   уходит число стиля (его читает AE).

Стенд гоняет боевую ``ipvSubs`` в node на плане настоящей сборки: проверяются ключи
слова (место в ряду, подъём, прозрачность, блюр), а не текст .jsx.
"""
import gzip
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
sys.path.insert(0, HERE)

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import AE_BLUR_PER_SIGMA, HL_DUR, css_blur_px  # noqa: E402
from tests.test_rows_yellow import _ipv_subs_code, _preview_plan  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

BLUR = {"hl_blur": True, "hl_blur_amt": 70.4}
# Фикстура: «Д» (7) и «ОДЛО» (8) — два жёлтых подряд. С разделителем после «Д» это ровно
# случай эталона: прежнее слово уходит своим концом, новое встаёт в нулевой ряд.
PREV, NEXT = 7, 8
PREV_W, NEXT_W = "Д", "ОДЛО"
LONG = 11              # «МЕТКОНСЕКТ»: жёлтое без своей длительности появления
LONG_W = "МЕТКОНСЕКТ"


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=None, breaks=None):
    st = dict(BLUR)
    st.update(style or {})
    return xml2ae.scene_plan(xml, highlights=list(highlights or []),
                             hl_breaks=list(breaks or []), style=st,
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=None, breaks=None, intro=None):
    st = dict(BLUR)
    st.update(style or {})
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   highlights=list(highlights or []),
                                   hl_breaks=list(breaks or []), intro=intro or [],
                                   style=st, disclaimer="", emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


# --------------------------------------------------------------------------- 1. перевод
def test_css_blur_conversion():
    """«Blurriness» AE -> сигма CSS: число делится на замеренный множитель."""
    assert AE_BLUR_PER_SIGMA == 4.2, "множитель перевода уехал — перемерять по рендеру"
    assert css_blur_px(70.4) == 16.76, "число стиля жёлтых переведено неверно"
    assert css_blur_px(26.8) == 6.38, "число раскрытия интро переведено неверно"
    assert css_blur_px(4.2) == 1.0
    assert css_blur_px(0) == 0.0 and css_blur_px(None) == 0.0
    assert css_blur_px("мусор") == 0.0, "мусор в стиле обязан значить «блюра нет»"


def test_plan_carries_css_blur_and_jsx_keeps_ae_number(xml_subs, tmp_path):
    """План несёт пиксели CSS, .jsx — «Blurriness» AE: перевод не утёк в сборку.

    Одно число стиля кормит две двери, и путать их нельзя: AE читает «Blurriness»,
    браузер — сигму. План отдаёт превью готовые пиксели, .jsx остаётся прежним.
    """
    plan = _plan(xml_subs, highlights=[PREV, NEXT], breaks=[PREV])
    assert plan["hl_blur"] is True and plan["hl_blur_amt"] == 70.4
    assert plan["hl_blur_css"] == css_blur_px(plan["hl_blur_amt"])
    assert plan["hl_blur_css"] != plan["hl_blur_amt"], "в план уехало число AE, а не сигма"
    reveal = plan["intro_anims"]["reveal"]
    assert reveal["blur"] == 26.8, "план потерял число шаблона"
    assert reveal["blur_css"] == css_blur_px(reveal["blur"])

    intro = [dict(words=["РАСКРЫТИЕ"], color="white", times=[1.0], anim="reveal")]
    jsx = _jsx(xml_subs, tmp_path, "blur.jsx", highlights=[PREV, NEXT], breaks=[PREV],
               intro=intro)
    assert "var HL_BLUR = 70.4;" in jsx, "в .jsx уехала сигма вместо «Blurriness»"
    assert "pBl.setValueAtTime(t0,26.8); pBl.setValueAtTime(t0+F_DUR,0);" in jsx, \
        "ключи блюра раскрытия интро уехали из .jsx"


# --------------------------------------------------- 2. смена слова и блюр (node-стенд)
# Эмуляция DOM под ipvSubs: #ipvsub 540x960 (k = 540/1080 = 0.5). Строки разбираются
# вместе с атрибутами слов (data-hl0 — момент появления, data-hld — своя длительность):
# без разбора атрибутов слово второй строки в стенд бы не попало.
_DOM = r"""
const assert = require('assert');

function El(tag){
  this.tagName=String(tag).toUpperCase();
  this._cls=new Set();
  this.style={setProperty(){},removeProperty(){}};
  this.styleStr='';this.dataset={};this.children=[];this.childNodes=[];
  this.parent=null;this._html='';this._text='';
  const self=this;
  this.classList={
    add:c=>self._cls.add(c),remove:c=>self._cls.delete(c),
    toggle:(c,v)=>{const on=(v===undefined)?!self._cls.has(c):!!v;
      if(on)self._cls.add(c);else self._cls.delete(c);return on;},
    contains:c=>self._cls.has(c)};
}
Object.defineProperty(El.prototype,'className',{
  get(){return [...this._cls].join(' ');},
  set(v){this._cls=new Set(String(v||'').split(/\s+/).filter(Boolean));}});
Object.defineProperty(El.prototype,'textContent',{
  get(){return this._text||this.children.map(c=>c.textContent).join('');},
  set(v){this._text=''+(v==null?'':v);this.children=[];}});
Object.defineProperty(El.prototype,'innerHTML',{
  get(){return this._html;},
  set(h){
    this._html=h;this.children=[];
    const rowRe=/<span class="(pvsubw(?: yel)?)" style="([^"]*)">([\s\S]*?)(?=<span class="pvsubw(?: yel)?"|$)/g;
    let m;
    while((m=rowRe.exec(h))!==null){
      const row=new El('span');row.className=m[1];row.styleStr=m[2];row._html=m[3];
      const wdRe=/<span class="(pvsubw_wd[^"]*)"([^>]*) style="([^"]*)">([\s\S]*?)<\/span>/g;
      let w;
      while((w=wdRe.exec(m[3]))!==null){
        const el=new El('span');el.className=w[1];
        const at=/data-([a-z0-9]+)="([^"]*)"/g;let a;
        while((a=at.exec(w[2]))!==null)el.dataset[a[1]]=a[2];
        el.styleStr=w[3];el.textContent=w[4];
        row.appendChild(el);
      }
      this.appendChild(row);
    }
  }});
El.prototype.appendChild=function(ch){this.children.push(ch);ch.parent=this;return ch;};
El.prototype.remove=function(){if(this.parent){const i=this.parent.children.indexOf(this);
  if(i>=0)this.parent.children.splice(i,1);this.parent=null;}};
El.prototype.querySelector=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  return this.children.find(c=>c._cls.has(want))||null;};
El.prototype.querySelectorAll=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  const res=[];
  (function scan(el){for(const ch of el.children){if(ch._cls.has(want))res.push(ch);scan(ch);}})(this);
  return res;};

const subEl=new El('div');subEl.id='ipvsub';subEl.clientWidth=540;subEl.clientHeight=960;
global.$=id=>(id==='ipvsub'?subEl:null);
global.document={createElement:tag=>new El(tag)};
global.t=s=>s;
global.esc=s=>String(s==null?'':s);
global.rgb2hex=()=>'#fff';
global.ipvFontFor=()=>null;
global.FONTS=[];
global.CURSTYLE={};
global.IPVMODE='ae';
global.IPV={plan:@PLAN@};

function host(){return subEl.querySelector('.pvsubs_host');}
function paint(plan,tm){IPV.plan=plan;ipvSubs(tm);return host();}
function words(){return host().querySelectorAll('.pvsubw_wd');}
function word(txt){return words().filter(w=>w.textContent===txt)[0]||null;}
function rowOf(txt){
  return host().querySelectorAll('.pvsubw').filter(r=>(r._html||'').indexOf('>'+txt+'</span>')>=0)[0]||null;}
"""


def _run_preview(tmp_path, name, plan, checks, plan2=None):
    """Прогнать боевую ipvSubs в node: эмуляция DOM, план и проверки.

    Второй план (`plan2`) — тот же клип с другими числами: превью обязано рисовать то,
    что лежит в плане, а не помнить прошлый кадр.
    """
    script = (_DOM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + _ipv_subs_code() + "\nconst plan=IPV.plan;\n"
              + ("const plan2=%s;\n" % json.dumps(_preview_plan(plan2 or plan),
                                                  ensure_ascii=False))
              + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, "проверки превью не прошли: %s" % res.stdout


@node
def test_word_change_hides_previous_and_keeps_row(xml_subs, tmp_path):
    """На моменте смены слова прежнее скрыто, новое — в ряду строки и с ключами .jsx.

    Разделитель после прежнего слова (hl_breaks) — это ровно случай эталона: у прежнего
    gend = собственный конец, у нового ряд 0. Стенд сверяет с планом и место нового
    (bottom строки = posy + row*hl_step), и все три ключа появления — подъём,
    прозрачность и блюр (последний обязан быть в пикселях CSS, а не числом AE).
    """
    plan = _plan(xml_subs, highlights=[PREV, NEXT], breaks=[PREV])
    prev, nxt = plan["subs"][PREV], plan["subs"][NEXT]
    assert (prev["w"], nxt["w"]) == (PREV_W, NEXT_W), "фикстура уехала"
    # С разделителем связка рвётся: прежнее уходит своим концом, новое в нулевом ряду.
    assert prev["gend"] == prev["e"], "разделитель не разорвал связку жёлтых"
    assert nxt["s"] == prev["gend"], "фикстура не даёт смены слова встык"
    assert (prev["row"], nxt["row"]) == (0, 0), "новое слово уехало из ряда строки"
    assert nxt["hd"] and nxt["hd"] < HL_DUR, "у нового слова своя длительность появления"

    checks = r"""
const prev=plan.subs[@PREV@], nxt=plan.subs[@NEXT@];
const t0=nxt.s, dur=nxt.hd, k=540/plan.w;

// Кадр ДО смены: прежнее слово ещё стоит (его gend = момент смены).
paint(plan, t0-1/60);
assert(word(@PREV_W@)!==null, 'прежнее слово пропало раньше своего конца');

// Момент смены: прежнее скрыто (его gend прошёл), новое нарисовано.
paint(plan, t0);
assert(word(@PREV_W@)===null, 'на моменте смены прежнее слово ещё висит');
const w=word(@NEXT_W@);
assert(w!==null, 'новое слово не нарисовано на своём моменте');

// Место нового — ряд строки, а не шаг стопки: bottom = posy + row*hl_step.
const row=rowOf(@NEXT_W@);
assert(row!==null, 'новое слово не попало в строку');
const bot=(plan.h-(plan.posy+nxt.row*plan.hl_step))/plan.h*100;
assert(row.styleStr==='bottom:'+bot.toFixed(2)+'%;',
  'новое слово не в ряду строки: '+row.styleStr+' против bottom:'+bot.toFixed(2)+'%;');
assert(nxt.row===0 && Math.abs(bot-((plan.h-plan.posy)/plan.h*100))<1e-9,
  'ряд нового слова не нулевой');

// Ключи появления — те же, что в .jsx у слоя слова: подъём, прозрачность, блюр
// (t0 и t0+hd, кривая easePair = дефолт keysAt). Блюр — в пикселях CSS из плана.
const tm=t0+dur/2;
paint(plan, tm);
const w2=word(@NEXT_W@);
const rem=keysAt([[t0,1],[t0+dur,0]],null,tm);
assert(rem>0&&rem<1,'кривая появления дала крайнее значение: '+rem);
assert(Math.abs(parseFloat(w2.style.top)-plan.hl_rise*rem*k)<0.011,
  'подъём не по ключам .jsx: '+w2.style.top);
assert(Math.abs(parseFloat(w2.style.opacity)-(1-rem))<1e-6,
  'прозрачность не по ключам .jsx: '+w2.style.opacity);
assert(plan.hl_blur_css!=null && plan.hl_blur_css!==plan.hl_blur_amt,
  'в плане нет блюра в пикселях CSS');
assert(w2.style.filter==='blur('+(plan.hl_blur_css*rem*k).toFixed(2)+'px)',
  'блюр появления не в пикселях CSS: '+w2.style.filter);

// После появления слово отпущено: ни подъёма, ни размытия.
paint(plan, t0+dur);
const w3=word(@NEXT_W@);
assert(w3.style.top===''&&w3.style.opacity===''&&w3.style.filter==='',
  'слово не отпущено после появления: '+w3.style.top+' '+w3.style.filter);
console.log("OK: word change keeps the row and the .jsx keys");
"""
    checks = (checks.replace("@PREV@", json.dumps(PREV)).replace("@NEXT@", json.dumps(NEXT))
                    .replace("@PREV_W@", json.dumps(PREV_W, ensure_ascii=False))
                    .replace("@NEXT_W@", json.dumps(NEXT_W, ensure_ascii=False)))
    _run_preview(tmp_path, "sub_word_change.js", plan, checks)


@node
def test_preview_blur_is_the_css_number_not_the_ae_one(xml_subs, tmp_path):
    """Число AE в blur() не подставляется: подмена поля плана видна в стенде.

    Стенд гоняет ДВА плана: боевой (с hl_blur_css) и подменённый (то же самое, но
    hl_blur_css = hl_blur_amt — как было до перевода). Превью обязано нарисовать разные
    фильтры: иначе перевод чисел не доехал до него вовсе.
    """
    plan = _plan(xml_subs, highlights=[LONG])
    sub = plan["subs"][LONG]
    assert sub["w"] == LONG_W and "hd" not in sub, "у слова фикстуры своя длительность"
    t0 = sub["s"]
    checks = r"""
const k=540/plan.w, tm=@TM@;
paint(plan,tm);
const a=word(@W@).style.filter;
const rem=keysAt([[@T0@,1],[@T0@+plan.hl_dur,0]],null,tm);
assert(rem>0&&rem<1,'кривая появления дала крайнее значение: '+rem);
assert(a==='blur('+(plan.hl_blur_css*rem*k).toFixed(2)+'px)','боевой план: '+a);
paint(plan2,tm);
const b=word(@W@).style.filter;
assert(b==='blur('+(plan2.hl_blur_amt*rem*k).toFixed(2)+'px)',
  'подменённый план не доехал до превью: '+b);
assert(a!==b,'превью не отличает пиксели CSS от числа AE');
console.log("OK: preview takes the css blur from the plan");
"""
    checks = (checks.replace("@TM@", json.dumps(round(t0 + 0.08, 4)))
                    .replace("@T0@", json.dumps(t0))
                    .replace("@W@", json.dumps(sub["w"], ensure_ascii=False)))
    # Подменённый план: тот же клип, но перевод «не сделан» — как было до правки.
    bad = json.loads(json.dumps(plan))
    bad["hl_blur_css"] = bad["hl_blur_amt"]
    _run_preview(tmp_path, "sub_blur_css.js", plan, checks, plan2=bad)


def test_preview_reads_the_plan_field():
    """Сторож полей: превью берёт блюр из плана (hl_blur_css), а не из числа стиля."""
    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
              encoding="utf-8").read()
    assert "pl.hl_blur_css" in js, "превью не читает hl_blur_css плана"
    assert re.search(r"r\.blur_css!=null", js), "превью не читает blur_css раскрытия интро"
