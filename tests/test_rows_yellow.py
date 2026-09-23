# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание ZU: режим строк — анимация жёлтых в превью и стопка подряд идущих жёлтых.

1. План режима строк несёт всё, что нужно превью для анимации жёлтых: время появления у
   самого слова (``words[].t0`` — то же правило, что в цикле строк .jsx),
   подъём ``hl_rise``, длительность ``hl_dur``, режим ``hl_row_anim`` и блюр
   ``hl_blur``/``hl_blur_amt``. Боевая ``ipvSubs`` из ``static/app/85-inserts-view.js``
   исполняется в node с заглушками DOM: до своего момента слово невидимо, в середине
   подъёма — промежуточный сдвиг и прозрачность, после — снова на месте.
2. ``hl_row_stack=True`` с серией из трёх подряд жёлтых: этих слов нет ни в ``SUB_ROWS``,
   ни в строках плана; элементы стопки стоят с ``row`` 0,1,2 и общим ``gend`` (его считает
   то же правило, что раскладывает стопку в режиме «по слову», — ``_stack_layout``);
   одиночное жёлтое слово остаётся в строке. Превью рисует элементы стопки отдельными
   строками по шагу ``HL_STEP``, не склеивая их со строкой текста.
3. ``hl_row_stack=False`` — .jsx побайтово как на main: ни данных стопки, ни её цикла;
   сборка совпадает и с той же сборкой без ключа, и с эталоном ``golden_geometry.jsx``.
4. Сторож «каждая ручка»: ``hl_row_stack`` влияет на сборку (``sub_words_per_row=2`` и
   серия жёлтых), а без серии не добавляет в .jsx ничего.

Слова фикстуры (tests/fixtures/timeline_subs.xml.gz, 60 fps): 7 «Д», 8 «ОДЛО»,
9 «ИПСУМДОЛО» — серия из трёх жёлтых подряд (уезжает в стопку; тексты в фикстуре
уникальны, поэтому строки сверяются по словам), 11 «МЕТКОНСЕКТ» — одиночное жёлтое
(остаётся в строке и въезжает внутри своей строки: слово на 233 кадре, строка 194..290).
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
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# индексы слов фикстуры: серия из трёх жёлтых подряд и одиночное жёлтое
SERIES = [7, 8, 9]
SINGLE = 11
HIGHLIGHTS = SERIES + [SINGLE]
JS_REL = ("static", "app", "85-inserts-view.js")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=None):
    """План сцены в режиме строк (два слова в строке)."""
    st = {"sub_words_per_row": 2}
    st.update(style or {})
    return xml2ae.scene_plan(xml, highlights=list(highlights or []), style=st,
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=None):
    """Собранный .jsx в режиме строк."""
    st = {"sub_words_per_row": 2}
    st.update(style or {})
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   highlights=list(highlights or []), style=st,
                                   disclaimer="", emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _json_var(jsx, name):
    """Данные цикла из .jsx: var <name> = [...]."""
    m = re.search(r"var %s = (\[.*?\]);" % name, jsx)
    assert m, "в .jsx нет var %s" % name
    return json.loads(m.group(1))


def _row_words(jsx):
    """Слова строк цикла SUB_ROWS: [[t, слово, hl], ...] -> плоский список слов."""
    return [wd[1] for row in _json_var(jsx, "SUB_ROWS") for wd in row[4]]


def _plan_row_words(plan):
    """Слова строк плана (элементы без пометки stack)."""
    return [wd["w"] for it in plan["subs"] if not it.get("stack")
            for wd in it.get("words") or []]


def _preview_plan(plan):
    """Из плана — только то, что читает ipvSubs (числа превью берёт из плана)."""
    keys = ("w", "h", "posy", "fsize", "sub_step", "hl_step", "hl_rise", "hl_dur",
            "hl_row_anim", "hl_blur", "hl_blur_amt", "subs")
    return {k: plan[k] for k in keys if k in plan}


def _ipv_subs_code():
    """Боевые функции превью: aeEase/keysAt (кривая, что easePair в AE) и ipvSubs."""
    js = open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()
    a, b = js.find("function aeEase"), js.find("function ipvIns(")
    assert a >= 0 and b > a, "не нашлись aeEase/keysAt в 85-inserts-view.js"
    c, d = js.find("function ipvSubs(tm){"), js.find("function ipvUI(tm){")
    assert c >= 0 and d > c, "не нашлась ipvSubs в 85-inserts-view.js"
    return js[a:b] + "\n" + js[c:d]


# Эмуляция DOM ровно под ipvSubs: #ipvsub 540x960 (k = 540/1080 = 0.5), host разбирает
# innerHTML на строки .pvsubw и слова .pvsubw_wd — по ним и работает покадровая анимация.
_DOM_SIM = r"""
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
    // строка: класс ровно pvsubw / pvsubw yel (у слов класс pvsubw_wd — он сюда не попадает)
    const rowRe=/<span class="(pvsubw(?: yel)?)" style="([^"]*)">([\s\S]*?)(?=<span class="pvsubw(?: yel)?"|$)/g;
    let m;
    while((m=rowRe.exec(h))!==null){
      const row=new El('span');row.className=m[1];row.styleStr=m[2];row._html=m[3];
      const wdRe=/<span class="(pvsubw_wd[^"]*)"(?: data-hl0="([^"]*)")? style="([^"]*)">([\s\S]*?)<\/span>/g;
      let w;
      while((w=wdRe.exec(m[3]))!==null){
        const el=new El('span');el.className=w[1];
        if(w[2]!==undefined)el.dataset.hl0=w[2];
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
function spans(){return host().querySelectorAll('.pvsubw');}
function words(){return host().querySelectorAll('.pvsubw_wd');}
function owner(txt){return spans().filter(sp=>(sp._html||'').indexOf('>'+txt+'</span>')>=0);}
"""


def _run_preview(tmp_path, name, plan, checks, plan2=None):
    """Прогнать боевую ipvSubs в node: эмуляция DOM + проверки.

    В скрипте план — `plan` (он же IPV.plan), второй план — `plan2`: тем же subs, но с
    другими числами (например, с выключенным блюром) — превью обязано брать их из плана.
    """
    script = (_DOM_SIM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + _ipv_subs_code() + "\n"
              + "const plan=IPV.plan;\n"
              + ("const plan2=%s;\n" % json.dumps(_preview_plan(plan2 or plan),
                                                  ensure_ascii=False))
              + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, "проверки превью не прошли: %s" % res.stdout


# ------------------------------------------------- 1. анимация жёлтых в строках превью
@node
def test_preview_animates_yellow_in_rows(xml_subs, tmp_path):
    """План строк несёт время появления, подъём, длительность и блюр; превью (node) до
    момента прячет жёлтое слово, в середине подъёма сдвигает и проявляет, после — отпускает."""
    st = {"hl_row_anim": "word", "hl_blur": True, "hl_blur_amt": 70.4}
    plan = _plan(xml_subs, style=st, highlights=[SINGLE])

    # 1a. План несёт параметры анимации (превью своих чисел не заводит). Высота подъёма
    # на фикстуре (кадр 1920) — 123.0 px; число выписано явно, а не пересчитано тем же
    # множителем, что стоит в реализации.
    assert plan["hl_row_anim"] == "word"
    assert plan["h"] == 1920, "кадр фикстуры уехал — число подъёма ниже посчитано для него"
    assert plan["hl_rise"] == 123.0, "высота подъёма жёлтого уехала из плана: %r" % plan["hl_rise"]
    assert plan["hl_dur"] == 0.35, "длительность подъёма уехала из плана"
    assert plan["hl_blur"] is True and plan["hl_blur_amt"] == 70.4

    # 1b. Время появления жёлтого слова в строке — контракт ZH, посчитанный Python.
    target = None
    for it in plan["subs"]:
        for wd in it.get("words") or []:
            if wd["color"] != "yellow":
                continue
            exp = round(min(max(wd["s"], it["s"]), max(it["s"], it["e"] - plan["hl_dur"])), 4)
            assert wd.get("t0") == exp, "t0 жёлтого не по правилу ZH: %r" % (wd,)
            if wd["t0"] > it["s"] + 0.1 and wd["t0"] + plan["hl_dur"] < it["e"]:
                target = (it, wd)
    assert target, "в фикстуре нет жёлтого, въезжающего внутри своей строки"
    assert any(wd.get("t0") is None for it in plan["subs"] for wd in it.get("words") or []), \
        "белым словам тоже проставили время появления"

    # 1c. hl_row_anim="row": появление — начало строки (как было до ZH).
    row_mode = _plan(xml_subs, style={"hl_row_anim": "row"}, highlights=[SINGLE])
    assert row_mode["hl_row_anim"] == "row"
    yellow = [wd for it in row_mode["subs"] for wd in it.get("words") or []
              if wd["color"] == "yellow"]
    assert yellow, "жёлтых в плане нет"
    for it in row_mode["subs"]:
        for wd in it.get("words") or []:
            if wd["color"] == "yellow":
                assert wd["t0"] == round(it["s"], 4), "при hl_row_anim=row появление не со строкой"

    # Тот же план с выключенным блюром: превью обязано взять блюр из плана, а не помнить своё.
    no_blur = dict(_preview_plan(plan))
    no_blur["hl_blur"] = False

    checks = r"""
// Один жёлтый в строках, у него своё время появления (t0) из плана.
paint(plan,@ROW_S@);
let ys=words().filter(w=>w.dataset.hl0!==undefined);
assert.strictEqual(ys.length,1,'в строках ожидался один жёлтый с t0: '+ys.length);
const t0=parseFloat(ys[0].dataset.hl0);
assert.strictEqual(t0,@T0@,'превью взяло не плановое t0: '+t0+' vs '+@T0@);
const rise=plan.hl_rise, dur=plan.hl_dur, k=540/plan.w, amt=plan.hl_blur_amt;
assert(rise>0&&dur>0,'в плане нет подъёма/длительности');

// До момента появления — невидимо и поднято на весь подъём.
paint(plan,t0-0.1);
ys=words().filter(w=>w.dataset.hl0!==undefined);
assert.strictEqual(parseFloat(ys[0].style.opacity),0,'до t0 слово видно: '+ys[0].style.opacity);
assert.strictEqual(ys[0].style.position,'relative','подъём не через relative: '+ys[0].style.position);
assert(Math.abs(parseFloat(ys[0].style.top)-rise*k)<0.011,'до t0 подъём не полный: '+ys[0].style.top);

// Середина подъёма — сдвиг и прозрачность промежуточные, ровно по кривой easePair.
const tm=t0+dur/2;
paint(plan,tm);
ys=words().filter(w=>w.dataset.hl0!==undefined);
const rem=keysAt([[t0,1],[t0+dur,0]],null,tm);
const op=parseFloat(ys[0].style.opacity), top=parseFloat(ys[0].style.top);
assert(rem>0&&rem<1,'кривая easePair дала не промежуточное значение: '+rem);
assert(op>0&&op<1,'середина обязана быть промежуточной: '+op);
assert(top>0&&top<rise*k,'середина обязана быть между нулём и подъёмом: '+top);
assert(Math.abs(op-(1-rem))<1e-6,'прозрачность не по кривой keysAt: '+op+' vs '+(1-rem));
assert(Math.abs(top-rise*rem*k)<0.011,'сдвиг не по кривой keysAt: '+top+' vs '+(rise*rem*k));
assert.strictEqual(ys[0].style.filter,'blur('+(amt*rem*k).toFixed(2)+'px)',
  'блюр не от hl_blur_amt: '+ys[0].style.filter);

// После подъёма — на месте, следов анимации нет.
paint(plan,t0+dur+0.05);
ys=words().filter(w=>w.dataset.hl0!==undefined);
assert.strictEqual(ys[0].style.top,'','после подъёма остался сдвиг: '+ys[0].style.top);
assert.strictEqual(ys[0].style.opacity,'','после подъёма осталась прозрачность: '+ys[0].style.opacity);
assert.strictEqual(ys[0].style.filter,'','после подъёма остался блюр: '+ys[0].style.filter);
assert(!ys[0].style.position,'после подъёма остался position: '+ys[0].style.position);

// Тот же кадр, но план с выключенным блюром: фильтра нет, а подъём тот же.
paint(plan2,tm);
ys=words().filter(w=>w.dataset.hl0!==undefined);
assert.strictEqual(ys[0].style.filter,'','при hl_blur=False повесили фильтр: '+ys[0].style.filter);
assert(Math.abs(parseFloat(ys[0].style.opacity)-(1-rem))<1e-6,'подъём пропал без блюра');
assert(Math.abs(parseFloat(ys[0].style.top)-rise*rem*k)<0.011,'подъём пропал без блюра');
console.log("OK: preview animates yellow in rows");
"""
    checks = (checks.replace("@ROW_S@", json.dumps(target[0]["s"]))
                    .replace("@T0@", json.dumps(target[1]["t0"])))
    _run_preview(tmp_path, "yp_anim.js", plan, checks, plan2=no_blur)


# ------------------------------------------------------------ 2. стопка подряд жёлтых
@node
def test_row_stack_takes_series_out_of_rows(xml_subs, tmp_path):
    """hl_row_stack=True: серия из 3 подряд жёлтых уходит из строк в стопку (row 0,1,2,
    общий gend), одиночное жёлтое остаётся в строке; превью рисует стопку своим шагом."""
    st = {"hl_row_stack": True}
    plan = _plan(xml_subs, style=st, highlights=HIGHLIGHTS)
    jsx = _jsx(xml_subs, tmp_path, "stack_on.jsx", style=st, highlights=HIGHLIGHTS)
    meta, cams, subs, _xi = xml2ae.parse_full(xml_subs)
    series_w = [subs[k][2] for k in SERIES]
    single_w = subs[SINGLE][2]

    # 2a. В строках .jsx (SUB_ROWS) слов серии нет, одиночное жёлтое — на месте.
    row_words = _row_words(jsx)
    for w in series_w:
        assert w not in row_words, "слово серии %r осталось в строке" % w
    assert single_w in row_words, "одиночное жёлтое уехало из строк"

    # 2b. Цикл стопки: ровно слова серии, row 0,1,2 и общий конец (как в режиме «по слову»).
    stack = _json_var(jsx, "SUB_STACK")
    assert [x[2] for x in stack] == series_w
    assert [x[4] for x in stack] == [0, 1, 2]
    assert len({x[5] for x in stack}) == 1, "у стопки не общий конец"
    assert stack[0][5] == max(subs[k][1] for k in SERIES), "конец стопки не по последнему слову"

    # 2c. То же в плане: элементы стопки помечены, строки собраны из остальных слов.
    stack_items = [it for it in plan["subs"] if it.get("stack")]
    assert [it["w"] for it in stack_items] == series_w
    assert [it["row"] for it in stack_items] == [0, 1, 2]
    assert len({it["gend"] for it in stack_items}) == 1
    assert stack_items[0]["gend"] == pytest.approx(stack[0][5] / float(meta["fps"]))
    for it in stack_items:
        assert it["color"] == "yellow" and "words" not in it, \
            "элемент стопки не как элемент режима «по слову»: %r" % (it,)
    plan_words = _plan_row_words(plan)
    for w in series_w:
        assert w not in plan_words, "слово серии %r осталось в строках плана" % w
    single = [wd for it in plan["subs"] if not it.get("stack")
              for wd in it.get("words") or [] if wd["color"] == "yellow"]
    assert [wd["w"] for wd in single] == [single_w], "одиночное жёлтое потеряло свою строку"
    assert single[0].get("t0") is not None, "у одиночного жёлтого в строке пропало время появления"

    # 2d. Превью: элементы стопки — своими строками по HL_STEP, не в ряду текста.
    # Момент — середина последнего слова серии: стопка видна вся, а строки текста рядом живы.
    tm = round((stack_items[-1]["s"] + stack_items[0]["gend"]) / 2.0, 4)
    checks = r"""
const stackItems=plan.subs.filter(s=>s.stack);
assert.strictEqual(stackItems.length,3,'в плане не три элемента стопки: '+stackItems.length);
const tm=%s;
paint(plan,tm);
for(const it of stackItems){
  const bot=((plan.h-(plan.posy+it.row*plan.hl_step))/plan.h*100).toFixed(2);
  const mine=owner(it.w);
  assert.strictEqual(mine.length,1,'элемент стопки '+it.w+' не своей строкой: '+mine.length);
  assert(mine[0].styleStr.indexOf('bottom:'+bot+'%%')===0,
    'стопка '+it.w+' стоит не по HL_STEP: '+mine[0].styleStr+' (ждали bottom:'+bot+'%%)');
}
// Строка текста в тот же момент — по шагу строк SUB_STEP и без слов стопки.
const rowIt=plan.subs.filter(s=>!s.stack&&s.s<=tm&&tm<s.gend)[0];
assert(rowIt,'в этот момент нет видимой строки текста');
const rowBot=((plan.h-(plan.posy+rowIt.row*plan.sub_step))/plan.h*100).toFixed(2);
const rowSpans=owner(rowIt.words[0].w);
assert.strictEqual(rowSpans.length,1,'строка текста не нашлась: '+rowIt.words[0].w);
assert(rowSpans[0].styleStr.indexOf('bottom:'+rowBot+'%%')===0,
  'строка текста стоит не по SUB_STEP: '+rowSpans[0].styleStr+' (ждали bottom:'+rowBot+'%%)');
for(const it of stackItems)
  assert(!rowSpans[0]._html.includes(it.w),'слово стопки '+it.w+' осталось в строке текста');
console.log("OK: preview draws stack rows by HL_STEP");
""" % json.dumps(round(tm, 4))
    _run_preview(tmp_path, "yp_stack.js", plan, checks)


# ------------------------------------------- 3. выключенная галка — .jsx прежний (golden)
def test_row_stack_off_jsx_is_main(xml_subs, tmp_path):
    """hl_row_stack=False — .jsx побайтово как на main: ни SUB_STACK, ни его данных."""
    off = _jsx(xml_subs, tmp_path, "stack_off.jsx", style={"hl_row_stack": False},
               highlights=HIGHLIGHTS)
    absent = _jsx(xml_subs, tmp_path, "stack_absent.jsx", style={}, highlights=HIGHLIGHTS)
    assert off == absent, "выключенная галка всё равно меняет .jsx"
    for token in ("SUB_STACK", "hl_row_stack", "stack"):
        assert token not in off, "в .jsx с выключенной галкой остался %s" % token

    # Дефолтная сборка (режим «по слову») — эталон main, не перегенерируется.
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path)) == golden, \
        "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"


# ------------------------------------------------------- 4. сторож «каждая ручка»
def test_row_stack_knob_changes_build(xml_subs, tmp_path):
    """hl_row_stack влияет на сборку (per_row=2 + серия жёлтых), без серии — не влияет."""
    on = _jsx(xml_subs, tmp_path, "knob_on.jsx", style={"hl_row_stack": True},
              highlights=HIGHLIGHTS)
    off = _jsx(xml_subs, tmp_path, "knob_off.jsx", style={"hl_row_stack": False},
               highlights=HIGHLIGHTS)
    assert on != off, "ручка hl_row_stack не влияет на сборку"
    assert "var SUB_STACK = " in on, "галке нечего собирать: данные стопки не появились"
    assert "var SUB_STACK = " not in off
    assert _row_words(on) != _row_words(off), "строки собраны из тех же слов, что без галки"

    # Серии жёлтых нет — галка не добавляет в .jsx ничего.
    lone = _jsx(xml_subs, tmp_path, "knob_lone.jsx", style={"hl_row_stack": True},
                highlights=[SINGLE])
    assert "var SUB_STACK = " not in lone, "одиночное жёлтое уехало в стопку"
    assert lone == _jsx(xml_subs, tmp_path, "knob_lone_off.jsx", style={}, highlights=[SINGLE])

    # Режим «по слову» галка не трогает вовсе: там стопка и так работает, .jsx тот же.
    words_on = _jsx(xml_subs, tmp_path, "knob_words_on.jsx",
                    style={"hl_row_stack": True, "sub_words_per_row": 1}, highlights=HIGHLIGHTS)
    words_off = _jsx(xml_subs, tmp_path, "knob_words_off.jsx",
                     style={"sub_words_per_row": 1}, highlights=HIGHLIGHTS)
    assert words_on == words_off, "галке нечего делать в режиме «по слову»"
    assert "var SUB_STACK" not in words_on
