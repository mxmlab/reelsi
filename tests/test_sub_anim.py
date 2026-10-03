# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пресет появления слова субтитров (sub_anim): план, .jsx и предпросмотр.

Один путь данных на три выхода: пресет стиля -> ключи на КАЖДОЕ слово (Python,
core/xml2ae/layout.py) -> план сцены -> .jsx (те же ключи на слой слова) и превью
(те же ключи на DOM-спан слова). Ни .jsx, ни превью кривых не считают — только
интерполируют ключи плана (превью — `keysAt`, та же кривая, что `easePair` в AE).

Проверяется:

1. `rise` — у каждого БЕЛОГО слова ключи от `(t0, 0, amt, 100)` до
   `(t0+dur, 100, 0, 100)`: прозрачность, подъём, блюр;
2. `pop` — три ключа масштаба (старт, пик 108, 100) и прозрачность; положения нет;
3. жёлтые слова пресет НЕ трогает: у них своя анимация появления, и две на одном
   слое складывались бы дважды;
4. .jsx при `rise`: у слоя слова ключи Position и Opacity по времени начала слова —
   те же числа, что в плане;
5. превью (node, боевые функции из static/app/85-inserts-view.js): на `t0+dur/2` у
   спана промежуточные прозрачность и сдвиг — ровно интерполяция ключей плана, на
   `t0+dur` — финальные значения без следов анимации, до `t0` слово скрыто;
6. `none` (дефолт) — ни ключей в плане, ни подстановок в .jsx, ни изменений эталона.
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
from core.xml2ae import layout  # noqa: E402
from core.xml2ae.template import (SUBS_LOOP_ROWS, SUBS_LOOP_WORDS,  # noqa: E402
                                  SUBS_LOOP_WORDS_JOINED)
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

JS_REL = ("static", "app", "85-inserts-view.js")
AMT = 90.0          # своя сила появления в тестах: дефолт пресета в проверках не участвует
DUR = 0.3           # …и своя длительность
YELLOW = 3          # индекс жёлтого слова фикстуры (см. план ниже)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, preset="rise", style=None, highlights=(YELLOW,)):
    """План сцены с пресетом появления; по умолчанию — «въезжает снизу»."""
    st = {"sub_anim": preset, "sub_anim_dur": DUR, "sub_anim_amt": AMT}
    st.update(style or {})
    return xml2ae.scene_plan(xml, highlights=list(highlights), style=st,
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, preset="rise", style=None, highlights=(YELLOW,),
         hl_joins=None):
    st = {"sub_anim": preset, "sub_anim_dur": DUR, "sub_anim_amt": AMT}
    st.update(style or {})
    path, _n, _s = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                     highlights=list(highlights), style=st,
                                     hl_joins=(list(hl_joins) if hl_joins else None),
                                     inserts=[], disclaimer="", intro=None, roto=False,
                                     intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _white(plan):
    """Белые слова плана: (элемент, слово) — у элемента режима «по слову» нет words."""
    return [(it, None) for it in plan["subs"] if it["color"] != "yellow"]


def _words(jsx):
    """Данные SUBS из .jsx: [[начало, конец, слово, hl, row, gend, …], …]."""
    m = re.search(r"var SUBS=(\[.*?\]);", jsx)
    assert m, "в .jsx нет var SUBS"
    return json.loads(m.group(1))


def _preview_plan(plan):
    """Только то, что читает ipvSubs: числа превью берёт из плана, своих не держит."""
    keys = ("w", "h", "posy", "fsize", "sub_step", "hl_step", "hl_rise", "hl_dur",
            "hl_blur", "hl_blur_amt", "subs")
    return {k: plan[k] for k in keys if k in plan}


# --------------------------------------------------------------------------- #
# 1-3. План: ключи на каждое белое слово, жёлтые не тронуты
# --------------------------------------------------------------------------- #
def test_rise_keys_on_every_white_word(xml_subs):
    """`rise`: ключи от (t0, 0, amt, 100) до (t0+dur, 100, 0, 100) на КАЖДОМ белом слове."""
    plan = _plan(xml_subs, "rise")
    checked = 0
    for it in plan["subs"]:
        if it["color"] == "yellow":
            assert "anim" not in it, "жёлтому слову выдали анимацию пресета: %r" % (it,)
            continue
        a = it["anim"]
        t0, t1 = it["s"], round(it["s"] + DUR, 4)
        assert a["name"] == "rise"
        assert a["dur"] == DUR
        assert a["op"] == [[t0, 0.0], [t1, 100.0]], "прозрачность не 0 -> 100: %r" % (a,)
        assert a["dy"] == [[t0, AMT], [t1, 0.0]], "подъём не amt -> 0: %r" % (a,)
        # Блюр — доля амплитуды (пресет подъёма проявляется из размытия).
        assert a["bl"] == [[t0, AMT * layout.SUB_ANIM_BLUR_K], [t1, 0.0]], \
            "блюр появления не на тех же ключах: %r" % (a,)
        assert "sc" not in a, "у въезда снизу не должно быть масштаба"
        checked += 1
    assert checked > 5, "в фикстуре не нашлось белых слов: %d" % checked


def test_pop_has_three_scale_keys(xml_subs):
    """`pop`: три ключа масштаба (старт, пик, 100), прозрачность 0 -> 100, положения нет."""
    plan = _plan(xml_subs, "pop", style={"sub_anim_amt": 60})
    checked = 0
    for it in plan["subs"]:
        if it["color"] == "yellow":
            continue
        a = it["anim"]
        t0, t1 = it["s"], round(it["s"] + DUR, 4)
        assert a["name"] == "pop"
        assert len(a["sc"]) == 3, "у роста не три ключа масштаба: %r" % (a["sc"],)
        assert a["sc"][0] == [t0, 60]
        assert a["sc"][1][0] == round(t0 + DUR * layout.SUB_ANIM_POP_PEAK_AT, 4)
        assert a["sc"][1][1] == layout.SUB_ANIM_POP_PEAK
        assert a["sc"][2] == [t1, 100.0]
        assert a["op"] == [[t0, 0.0], [t1, 100.0]]
        assert "dy" not in a and "bl" not in a, \
            "рост уехал бы снизу и из размытия — это другой пресет: %r" % (a,)
        checked += 1
    assert checked > 5


def test_preset_defaults_and_none(xml_subs):
    """Числа по умолчанию — у пресета (`dur`/`amt` = 0/None), а `none` не даёт ключей."""
    rise = layout.sub_anim_preset("rise", 0, None)
    pop = layout.sub_anim_preset("pop", 0, None)
    assert (rise.dur, rise.rise, rise.blur, rise.scale) == (0.18, 120.0, 60.0, 100.0)
    assert (pop.dur, pop.rise, pop.blur, pop.scale) == (0.2, 0.0, 0.0, 50.0)
    # Ноль вместо амплитуды — «как задумано пресетом»: у роста старт не ноль.
    assert layout.sub_anim_preset("pop", 0, 0).scale == 50.0
    assert layout.sub_anim_preset("none") is None
    assert layout.sub_anim_preset("") is None
    assert layout.sub_anim_preset("нет-такого") is None, "незнакомый пресет не должен оживать"

    off = _plan(xml_subs, "none")
    assert all("anim" not in it for it in off["subs"]), \
        "при sub_anim=none в плане появились ключи появления"
    absent = _plan(xml_subs, "none", style={"sub_anim": None})
    assert off["subs"] == absent["subs"], "выключенный пресет зависит от способа выключения"


def test_none_build_is_main(xml_subs, tmp_path):
    """`none` — дефолт: эталон .jsx не меняется ни на байт, подстановок пресета нет."""
    off = _jsx(xml_subs, tmp_path, "anim_off.jsx", preset="none", highlights=[])
    assert "subAnimKeys" not in off and "HL_W_" not in off, \
        "выключенный пресет оставил в .jsx свои подстановки"
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path)) == golden, \
        "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"


# --------------------------------------------------------------------------- #
# 4. .jsx: те же ключи на слой слова
# --------------------------------------------------------------------------- #
def test_jsx_puts_plan_keys_on_word_layer(xml_subs, tmp_path):
    """При `rise` у слоя слова — Position и Opacity по времени начала слова (сверка с планом)."""
    jsx = _jsx(xml_subs, tmp_path, "anim_rise.jsx")
    assert "var HL_W_DUR = %s" % DUR in jsx, "длительность пресета не уехала в .jsx"
    assert "try{ subAnimKeys(L, t0, POSY, HL_W_SC); }catch(e){}" in jsx, \
        "в ветке белого слова нет вызова ключей появления"
    # Подъём в .jsx — от УЖЕ поставленной позиции слоя: своего Y у него нет.
    assert "p.setValueAtTime(t0, [X, by+HL_W_RISE]);" in jsx
    assert "p.setValueAtTime(t0+HL_W_DUR, [X, by]);" in jsx
    assert 'op.setValueAtTime(t0, 0); op.setValueAtTime(t0+HL_W_DUR, 100);' in jsx
    assert "easePair(p);" in jsx and "easePair(op);" in jsx, \
        "ключи появления поставили без кривой (easePair)"

    plan = _plan(xml_subs, "rise")
    words = _words(jsx)
    assert len(words) == len(plan["subs"])
    for it, row in zip(plan["subs"], words):
        # Слово и его время в данных цикла — те же, что в плане (ключи ставятся по t0).
        assert row[2] == it["w"] and row[0] / 60.0 == it["s"]
        if it["color"] == "yellow":
            continue
        assert it["anim"]["op"][0][0] == row[0] / 60.0, \
            "ключи появления стоят не на времени начала слова"


def test_jsx_pop_declares_scale(xml_subs, tmp_path):
    """`pop` в .jsx: стартовый масштаб объявлен, подъём и блюр нейтральны."""
    jsx = _jsx(xml_subs, tmp_path, "anim_pop.jsx", preset="pop",
               style={"sub_anim_amt": 60})
    assert "HL_W_SC = 60" in jsx, "стартовый размер роста не уехал в .jsx"
    assert "HL_W_RISE = 0" in jsx and "HL_W_BLUR = 0" in jsx, \
        "рост получил и подъём, и блюр — два движения на одном слове"
    assert "sc.setValueAtTime(t0+HL_W_DUR*0.6, [108, 108]);" in jsx, "нет пика роста"
    assert "sc.setValueAtTime(t0+HL_W_DUR, [100, 100]);" in jsx, "рост не садится в 100"


def test_jsx_joined_loop_uses_data_row(xml_subs, tmp_path):
    """Цикл со склейками: вызов идёт вторым проходом, старт роста — из данных слова."""
    jsx = _jsx(xml_subs, tmp_path, "anim_join.jsx", preset="pop",
               style={"sub_anim_amt": 60}, highlights=[], hl_joins=[3, 4])
    assert "try{ subAnimKeys(L, t0, finalY, (rsw[7]==null ? HL_W_SC : rsw[7])); }catch(e){}" in jsx, \
        "в цикле со склейками нет вызова со своим местом и данными"
    subs = _words(jsx)
    assert any(len(r) >= 8 and r[7] == 60 for r in subs), \
        "стартовый размер роста не уехал полем строки данных SUBS"

# --------------------------------------------------------------------------- #
# 5. Превью (node): те же ключи, интерполяция по времени
# --------------------------------------------------------------------------- #
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
    const rowRe=/<span class="(pvsubw(?: yel)?)" style="([^"]*)">([\s\S]*?)(?=<span class="pvsubw(?: yel)?"|$)/g;
    let m;
    while((m=rowRe.exec(h))!==null){
      const row=new El('span');row.className=m[1];row.styleStr=m[2];row._html=m[3];
      const wdRe=/<span class="(pvsubw_wd[^"]*)"((?: data-[a-z0-9]+="[^"]*")*) style="([^"]*)">([\s\S]*?)<\/span>/g;
      let w;
      while((w=wdRe.exec(m[3]))!==null){
        const el=new El('span');el.className=w[1];
        const attrRe=/data-([a-z0-9]+)="([^"]*)"/g;
        let at;
        while((at=attrRe.exec(w[2]||''))!==null)
          el.dataset[at[1]]=at[2].replace(/&quot;/g,'"').replace(/&amp;/g,'&');
        el.styleStr=w[3];
        parseIch(el, w[4]);
        row.appendChild(el);
      }
      this.appendChild(row);
    }
    // Слово, перерисованное ipvRenderChars: строк-слов в разметке уже нет, а буквы есть.
    if(!this.children.length && /class="ich"/.test(h)) parseIch(this, h);
  }});
// Буквы слова: глитч раскладывает слово на спаны букв (ipvRenderChars), и стенд обязан
// их видеть — иначе проверить мерцание букв нечем.
function parseIch(el, html){
  const chRe=/<span class="ich"([^>]*)>([\s\S]*?)<\/span>/g;
  let c, chs=[];
  while((c=chRe.exec(html))!==null){
    const chEl=new El('span');chEl.className='ich';
    const cs=/style="([^"]*)"/.exec(c[1]);
    chEl.styleStr=cs?cs[1]:'';
    chEl.textContent=c[2];
    chs.push(chEl);
  }
  if(chs.length){ chs.forEach(x=>el.appendChild(x)); el._text=''; }
  else el.textContent=html;
}
El.prototype.appendChild=function(ch){this.children.push(ch);ch.parent=this;return ch;};
El.prototype.remove=function(){if(this.parent){const i=this.parent.children.indexOf(this);
  if(i>=0)this.parent.children.splice(i,1);this.parent=null;}};
El.prototype.querySelector=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  return this.children.find(c=>c._cls.has(want))||null;};
El.prototype.querySelectorAll=function(sel){
  const want=String(sel||'').replace(/^\./,'').replace(/\[.*$/,'');
  const res=[];
  (function scan(el){for(const ch of el.children){if(ch._cls.has(want))res.push(ch);scan(ch);}})(this);
  return res;};
// Раскладка стенда: у браузера геометрию не спросить, а подложке слова (sub_wbg) она
// нужна — фигура встаёт по прямоугольнику ТЕКУЩЕГО слова. Модель нарочно грубая и
// детерминированная: буква — 0.6 кегля, пробел между словами — 0.28 кегля, строка
// центрируется по стойке, её низ — из bottom:X%. Числа модели не «истина», а общий
// язык: по ним же стенд считает, где фигура обязана оказаться.
const SIM_W=540, SIM_H=960;
function simFs(el){
  const cqw=(s)=>{const m=/(?:^|;)font-size:([0-9.]+)cqw/.exec(s||'');return m?parseFloat(m[1])*SIM_W/100:null;};
  // Кегль слова: свой (множитель жёлтого) -> кегль строки -> фолбэк CSS (.pvsubw: 7.7cqw).
  return cqw(el.styleStr) || (el.parent?cqw(el.parent.styleStr):null) || 7.7*SIM_W/100;
}
El.prototype.getBoundingClientRect=function(){
  if(this._cls.has('pvsubs_host'))
    return {left:0,top:0,right:SIM_W,bottom:SIM_H,width:SIM_W,height:SIM_H};
  if(this._cls.has('pvsubw_wd') && this.parent){
    const row=this.parent, fs=simFs(this), spc=fs*0.28;
    const wds=row.children.filter(c=>c._cls.has('pvsubw_wd'));
    let tot=0; wds.forEach(w=>tot+=0.6*fs*w.textContent.length);
    tot+=Math.max(0,wds.length-1)*spc;
    let x=(SIM_W-tot)/2;
    for(const w of wds){
      const ww=0.6*fs*w.textContent.length;
      if(w===this){
        const m=/(?:^|;)bottom:([0-9.]+)%/.exec(row.styleStr||'');
        const bot=m?(SIM_H-parseFloat(m[1])/100*SIM_H):SIM_H;
        return {left:x,top:bot-fs,right:x+ww,bottom:bot,width:ww,height:fs};
      }
      x+=ww+spc;
    }
  }
  return {left:0,top:0,right:0,bottom:0,width:0,height:0};};

const subEl=new El('div');subEl.id='ipvsub';subEl.clientWidth=540;subEl.clientHeight=960;
global.$=id=>(id==='ipvsub'?subEl:null);
global.document={createElement:tag=>new El(tag)};
global.t=s=>s;
global.esc=s=>String(s==null?'':s);
// Цвет [r,g,b] 0..1 -> hex: заглушка «#fff» делала проверки цвета слепыми (в разметке
// не было ни одного цвета плана), поэтому считаем по-настоящему.
global.rgb2hex=c=>{const h=v=>{const n=Math.max(0,Math.min(255,Math.round((+v||0)*255)));
  return ('0'+n.toString(16)).slice(-2);};
  return '#'+h(c&&c[0])+h(c&&c[1])+h(c&&c[2]);};
global.ipvFontFor=()=>null;
global.FONTS=[];
global.CURSTYLE={};
global.IPVMODE='ae';
global.IPV={plan:@PLAN@,fps:60,renderT:null};

function host(){return subEl.querySelector('.pvsubs_host');}
function paint(plan,tm){IPV.plan=plan;ipvSubs(tm);return host();}
function words(){return host().querySelectorAll('.pvsubw_wd');}
function animated(){return words().filter(w=>w.dataset.wa!==undefined);}
"""


def _ipv_code():
    """Боевые функции превью: keysAt (кривая easePair) и отрисовка субтитров.

    Второй вырезки — глитча и построчного рендера букв (``ipvGlitchOp``/
    ``ipvRenderChars``) — требует пресет появления «глитч»: им играет и интро, и
    субтитры, и своей копии в стенде не заводится.
    """
    js = open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()
    a, b = js.find("function aeEase"), js.find("function ipvIns(")
    assert a >= 0 and b > a, "не нашлись aeEase/keysAt в 85-inserts-view.js"
    c, d = js.find("function ipvSubWordAnim("), js.find("function ipvUI(tm){")
    assert c >= 0 and d > c, "не нашлась отрисовка субтитров в 85-inserts-view.js"
    e, f = js.find("function ipvGlitchOp("), js.find("function ipvIntro(")
    assert e >= 0 and f > e, "не нашлись ipvGlitchOp/ipvRenderChars в 85-inserts-view.js"
    return js[a:b] + "\n" + js[c:d] + "\n" + js[e:f]


def _run_preview(tmp_path, name, plan, checks, mutate=False):
    """Прогнать боевую отрисовку субтитров в node: эмуляция DOM + проверки.

    `mutate=True` — мутация: функция появления игнорирует ключи плана и ставит
    финальное состояние (проверка, что стенд краснеет именно на этом).
    """
    script = (_DOM_SIM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + _ipv_code() + "\n"
              + ("ipvSubWordAnim=function(wsp,tm){wsp.style.opacity='';wsp.style.top='';"
                 "wsp.style.visibility='';};\n" if mutate else "")
              + "const plan=IPV.plan;\n" + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    return res


@node
def test_preview_interpolates_plan_keys(xml_subs, tmp_path):
    """На t0+dur/2 спан слова — ровно интерполяция ключей плана, на t0+dur — финал."""
    # Режим строк: в строке два слова, и второе появляется ПОЗЖЕ начала строки —
    # значит его спан уже на экране, когда ключей ещё нет (в AE это слой до inPoint).
    # Длительность в этом прогоне короче: слово, у которого анимация целиком
    # укладывается в его видимое окно, — иначе «после появления» проверять не на чем.
    plan = _plan(xml_subs, "rise", highlights=[],
                 style={"sub_words_per_row": 2, "sub_anim_dur": 0.05})
    row = next(it for it in plan["subs"] if len(it.get("words") or []) > 1)
    word = next(wd for wd in row["words"]
                if wd.get("anim") and wd["anim"]["op"][0][0] > row["s"] + 0.02
                and wd["anim"]["op"][-1][0] + 0.01 < wd["e"])
    a = word["anim"]
    t0, t1 = a["op"][0][0], a["op"][-1][0]
    tm = t0 + (t1 - t0) / 2.0

    checks = r"""
const t0=%(t0)s, t1=%(t1)s, tm=%(tm)s, w=%(word)s, rowS=%(row_s)s, rowE=%(row_e)s, end=%(gend)s;
// Строка уже видна (её окно открылось), а слово ещё не появилось: ключей нет —
// спан есть, но скрыт, как слой AE до inPoint.
assert(t0-0.05>rowS && t0-0.05<rowE,'строка не видна — проверка «до t0» стерегла бы пустоту');
paint(plan,t0-0.05);
let of=animated().filter(s=>s.textContent===w);
assert(animated().length>0,'в разметке нет слов с ключами появления');
assert.strictEqual(of.length,1,'слово исчезло из строки до t0');
assert.strictEqual(of[0].style.visibility,'hidden','до t0 слово не скрыто: '+of[0].style.visibility);

// Середина: значения РОВНО те, что даёт интерполяция ключей плана.
paint(plan,tm);
let sp=animated().filter(s=>s.textContent===w);
assert.strictEqual(sp.length,1,'слово пропало в середине появления');
sp=sp[0];
const a=JSON.parse(decodeURIComponent(sp.dataset.wa));
assert.strictEqual(a.name,'rise','превью взяло не плановые ключи: '+sp.dataset.wa);
assert.notStrictEqual(sp.style.visibility,'hidden','в середине слово всё ещё скрыто');
const op=parseFloat(sp.style.opacity), top=parseFloat(sp.style.top), blur=parseFloat(sp.style.filter.replace(/[^0-9.]+/g,''));
const expOp=keysAt(a.op,null,tm)/100, expDy=keysAt(a.dy,null,tm), expBl=keysAt(a.bl,null,tm);
assert(op>0&&op<1,'середина обязана быть промежуточной: '+op);
// 1e-4, а не 1e-6: стиль ставится с четырьмя знаками (toFixed(4)) — та же
// точность, с какой числа уезжают в .jsx.
assert(Math.abs(op-expOp)<1e-4,'прозрачность не по ключам плана: '+op+' vs '+expOp);
assert(Math.abs(top-expDy)<0.011,'сдвиг не по ключам плана: '+top+' vs '+expDy);
assert(Math.abs(blur-expBl)<0.011,'блюр не по ключам плана: '+blur+' vs '+expBl);
assert.strictEqual(sp.style.position,'relative','подъём сделан не через relative');

// После появления: финальное состояние, следов анимации нет.
assert(t1+0.01<end,'слово ушло раньше конца анимации — проверка «после» стерегла бы пустоту');
paint(plan,Math.min(t1+0.01,end-0.01));
sp=animated().filter(s=>s.textContent===w);
assert.strictEqual(sp.length,1,'слово пропало сразу после появления');
sp=sp[0];
assert.strictEqual(sp.style.opacity,'','после появления осталась прозрачность');
assert.strictEqual(sp.style.top,'','после появления остался сдвиг');
assert.strictEqual(sp.style.position,'','после появления остался relative');
assert.strictEqual(sp.style.filter,'','после появления остался блюр');
assert.strictEqual(sp.style.transform,'','после появления остался масштаб');
assert.notStrictEqual(sp.style.visibility,'hidden','слово осталось скрытым после появления');
console.log('OK: preview interpolates plan keys');
""" % {"t0": json.dumps(t0), "t1": json.dumps(t1), "tm": json.dumps(round(tm, 6)),
       "word": json.dumps(word["w"], ensure_ascii=False), "row_s": json.dumps(row["s"]),
       "row_e": json.dumps(row["e"]), "gend": json.dumps(word["e"])}
    res = _run_preview(tmp_path, "sa_rise.js", plan, checks)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, "проверки превью не прошли: %s" % res.stdout


@node
def test_preview_pop_scales_and_render_has_no_transition(xml_subs, tmp_path):
    """`pop`: масштаб на промежуточном кадре; в режиме рендера — только inline-стиль."""
    plan = _plan(xml_subs, "pop", highlights=[], style={"sub_anim_amt": 50})
    # Слово, чьё появление целиком укладывается в его видимое окно: иначе спан
    # исчезнет раньше, чем анимация доиграет, и проверять будет нечего.
    word = next(it for it in plan["subs"]
                if it["color"] != "yellow" and it["gend"] - it["s"] > DUR + 0.05)
    a = word["anim"]
    t0, t1 = a["op"][0][0], a["op"][-1][0]
    tm = t0 + (t1 - t0) * layout.SUB_ANIM_POP_PEAK_AT

    checks = r"""
const t0=%(t0)s, t1=%(t1)s, tm=%(tm)s, w=%(word)s;
paint(plan,tm);
const sp=animated().filter(s=>s.textContent===w)[0];
assert(sp,'не нашёлся спан слова '+w);
const a=JSON.parse(decodeURIComponent(sp.dataset.wa));
assert.strictEqual(a.name,'pop');
const sc=parseFloat(sp.style.transform.replace(/[^0-9.]+/g,''));
const expSc=keysAt(a.sc,null,tm)/100;
assert(Math.abs(sc-expSc)<1e-4,'масштаб не по ключам плана: '+sc+' vs '+expSc);
assert(sc>1,'в пике роста слово не крупнее нормы: '+sc);
assert.strictEqual(sp.style.transformOrigin,'50%% 100%%','рост идёт не от базовой линии');
assert.strictEqual(sp.style.top,'','рост получил ещё и подъём');
assert.strictEqual(sp.style.filter,'','рост получил ещё и блюр');
const op=parseFloat(sp.style.opacity);
assert(Math.abs(op-keysAt(a.op,null,tm)/100)<1e-4,'прозрачность не по ключам плана: '+op);

// Режим рендера: кадр снимается по номеру и повторяется — значение обязано быть
// функцией времени, а не следствием перехода или порядка вызовов.
IPV.renderT=tm;
paint(plan,tm);
let sp2=animated().filter(s=>s.textContent===w)[0];
const a1=sp2.style.transform, o1=sp2.style.opacity;
IPV.renderT=null;
paint(plan,tm);
sp2=animated().filter(s=>s.textContent===w)[0];
assert.strictEqual(sp2.style.transform,a1,'в рендере значение кадра зависит от режима');
assert.strictEqual(sp2.style.opacity,o1,'в рендере прозрачность зависит от режима');
const tr=[]; for(const el of words()) tr.push(el.style.transition||'');
assert(tr.every(v=>v===''),'у слов завёлся CSS-переход — снимок поедет по реальному времени: '+JSON.stringify(tr));
console.log('OK: preview pops and render stays per-frame');
""" % {"t0": json.dumps(t0), "t1": json.dumps(t1), "tm": json.dumps(round(tm, 6)),
       "word": json.dumps(word["w"], ensure_ascii=False)}
    res = _run_preview(tmp_path, "sa_pop.js", plan, checks)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, "проверки превью не прошли: %s" % res.stdout


@node
def test_preview_ignores_keys_mutation_turns_red(xml_subs, tmp_path):
    """Мутация: появление игнорирует ключи плана и ставит финал — стенд краснеет."""
    plan = _plan(xml_subs, "rise", highlights=[])
    word = next(it for it in plan["subs"]
                if it["color"] != "yellow" and it["gend"] - it["s"] > DUR + 0.05)
    a = word["anim"]
    t0, t1 = a["op"][0][0], a["op"][-1][0]
    checks = r"""
const t0=%(t0)s, t1=%(t1)s, tm=%(tm)s, w=%(word)s;
paint(plan,tm);
const sp=animated().filter(s=>s.textContent===w)[0];
assert(sp,'не нашёлся спан слова '+w+' среди '+words().map(s=>s.textContent).join(','));
const expOp=keysAt(JSON.parse(decodeURIComponent(sp.dataset.wa)).op,null,tm)/100;
const op=parseFloat(sp.style.opacity);
assert(Math.abs(op-expOp)<1e-4,'мутация не поймана: прозрачность '+op+' vs '+expOp);
assert(parseFloat(sp.style.top)>0,'мутация не поймана: подъём уже финальный');
console.log('OK: mutation caught');
""" % {"t0": json.dumps(t0), "t1": json.dumps(t1),
       "tm": json.dumps(round(t0 + (t1 - t0) / 2.0, 6)),
       "word": json.dumps(word["w"], ensure_ascii=False)}
    ok = _run_preview(tmp_path, "sa_mut_ok.js", plan, checks)
    assert ok.returncode == 0 and "OK" in ok.stdout, \
        "стенд не проходит немодифицированное превью: %s" % ((ok.stderr or "") + ok.stdout)[-800:]
    bad = _run_preview(tmp_path, "sa_mut_bad.js", plan, checks, mutate=True)
    assert bad.returncode != 0, "мутация НЕ покраснела — стенд стережёт пустоту"


# --------------------------------------------------------------------------- #
# 6. Сторож «каждая ручка»: пресет реально меняет сборку
# --------------------------------------------------------------------------- #
def test_preset_changes_the_build(xml_subs, tmp_path):
    """Ручка sub_anim влияет на сборку, а её выключение не добавляет ничего."""
    on = _jsx(xml_subs, tmp_path, "knob_on.jsx", preset="rise")
    off = _jsx(xml_subs, tmp_path, "knob_off.jsx", preset="none")
    assert on != off, "ручка sub_anim не влияет на сборку"
    assert "subAnimKeys" in on and "subAnimKeys" not in off
    # Сила и длительность — тоже ручки: их числа уезжают в .jsx.
    other = _jsx(xml_subs, tmp_path, "knob_amt.jsx", preset="rise",
                 style={"sub_anim_amt": 200, "sub_anim_dur": 0.5})
    assert "HL_W_RISE = 200" in other and "HL_W_DUR = 0.5" in other
    assert other != on


def test_word_loops_carry_the_call_placeholder():
    """Место вызова есть в КАЖДОМ цикле базовых слов: без него пресет молчит в этом режиме.

    Шаблоны подставляются по-разному (по слову и со склейками), а подстановка одна:
    пропусти её в одном из циклов — и пресет работал бы только в соседнем режиме.
    """
    for name, tpl in (("SUBS_LOOP_WORDS", SUBS_LOOP_WORDS),
                      ("SUBS_LOOP_WORDS_JOINED", SUBS_LOOP_WORDS_JOINED),
                      ("SUBS_LOOP_ROWS", SUBS_LOOP_ROWS)):
        assert "%(base_anim)s" in tpl, "в %s нет места вызова появления слова" % name
    # У жёлтых своя анимация: пресет вызывается в ветке БЕЛОГО слова, не вместо неё.
    assert "posP.setValue([SW/2, POSY]);%(base_anim)s" in SUBS_LOOP_WORDS
    assert "} else {\n                posP.setValue([wCenter, lineY]);%(base_anim)s" in SUBS_LOOP_ROWS
