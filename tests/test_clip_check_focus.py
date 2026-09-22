# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Фокус остаётся на галке клипа после перерисовки списков (задание MP).

Галка выбора одна на три шага, а клик по ней перерисовывает ВСЕ три списка
(`pickClip` → `renderClips1/2/3`, а те начинают с `innerHTML=''`). Вместе со старыми
строками из документа уходил и элемент с фокусом: он падал на `body`, и дальше с
клавиатуры было не пройти — Tab/Space начинали с начала страницы, Shift-выбор диапазона
рвался. Теперь `pickClip` запоминает галку и её список ДО перерисовки и возвращает фокус
на ту же галку (уже новый элемент) в том же списке.

Тесты гоняют БОЕВЫЕ функции из `static/app/40-queue.js` под node (приём
`tests/test_bulk_delete.py`): функции вырезаются по балансу скобок, вместо DOM —
заглушка с настоящим `activeElement` и `querySelector` (фокус снимается с элементов,
выброшенных из документа, — как в браузере). Заглушки `renderClips1/2/3` строят галки
ровно так же, как боевая разметка: `div.clip` → `label.pickbox` → `input[type=checkbox]`,
потому что признак `data-ci` и контейнер списка ищет именно `focusClipCheck`.

Проверяется:

1. фокус на галке клипа 2 в списке шага 2 → `pickClip(2,true,false)` → фокус на галке
   клипа 2 того же списка и именно на НОВОМ элементе (старый выброшен из документа);
2. Shift-диапазон: фокус остаётся на последней кликнутой галке, а не уезжает на начало;
3. фокус был не на галке — после `pickClip` он не перескакивает на галку.

Запуск: python -m pytest tests/test_clip_check_focus.py -q
"""
import io
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

QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
HTML = os.path.join(ROOT, "templates", "index.html")

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _src():
    return io.open(QUEUE_JS, encoding="utf-8").read()


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в 40-queue.js не нашлась функция %s" % name
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError("не сошлись скобки у %s" % name)


def _let(src, name):
    """Объявление состояния верхнего уровня — им пользуется вырезанная функция."""
    m = re.search(r"^let %s=.*$" % re.escape(name), src, re.M)
    assert m, "в 40-queue.js нет объявления let %s" % name
    return m.group(0)


# Заглушки: DOM и внешние двери. Всё, что проверяется, — боевой pickClip из 40-queue.js.
STUBS = r"""
// ---- минимальный DOM: узлы, дерево, document.querySelector ----
let FOCUS_CALLS=[],RENDERS={1:0,2:0,3:0},NEXT_NODE=1;
class El{
  constructor(tag){this.tag=tag;this.children=[];this.parentNode=null;this._html='';this.checked=false;
    this.dataset={};this.attrs={};this.isConnected=false;this.className='';this.node=NEXT_NODE++;EL_BY_ID[this.node]=this;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?this.attrs[k]:null;}
  set innerHTML(v){this._html=String(v);this.children.forEach(k=>k._disconnect());
    this.children=[];                       // innerHTML='' выбрасывает старые узлы из дерева
    const m=/<input type="checkbox"([^>]*)>/.exec(this._html);
    if(m){const ci=/data-ci="([^"]*)"/.exec(m[1]);
      if(ci){const inp=new El('input');inp.dataset.ci=ci[1];inp.checked=/checked/.test(m[1]);this.appendChild(inp);}}}
  get innerHTML(){return this._html;}
  appendChild(k){k.parentNode=this;k._connected();this.children.push(k);return k;}
  _connected(){this.isConnected=true;this.children.forEach(x=>x._connected());}
  _disconnect(){if(this.isConnected&&document.activeElement===this)document.activeElement=document.body;
    this.isConnected=false;this.children.forEach(x=>x._disconnect());}
  contains(n){return n===this||this.children.some(k=>k.contains(n));}
  closest(sel){const want=sel.replace(/^\[|\]$/g,'');let n=this;
    while(n){if(n.getAttribute&&n.getAttribute(want)!==null)return n;n=n.parentNode;}return null;}
  focus(opts){FOCUS_CALLS.push({ci:this.dataset.ci===undefined?null:this.dataset.ci,opts:opts});
    document.activeElement=this;}}
const EL_BY_ID={},TOP={},CONTAINERS={};
const body=new El('body');
const document={body,activeElement:body,
  contains:n=>!!(n&&n.isConnected),
  createElement:tag=>new El(tag),
  querySelectorAll:()=>[],
  querySelector:sel=>{
    const m=/^\[data-clips="([^"]*)"\] input\[type=checkbox\]\[data-ci="([^"]*)"\]$/.exec(sel);
    if(!m)return TOP[sel]||null;
    const h=CONTAINERS[m[1]];
    if(!h)return null;
    const inp=h.children.filter(r=>r.children.some(c=>c.dataset.ci===m[2]))
      .map(r=>r.children.find(c=>c.dataset.ci===m[2]));
    return inp[0]||null;}};
// списки шагов: контейнеры с data-clips лежат в теле документа (как в index.html).
// LIST объявлен ДО renderClips*: const в TDZ, и обращение к нему изнутри функции,
// вызванной раньше объявления, роняет сценарий (ReferenceError).
const LIST={};
['1','2','3'].forEach(k=>{const h=new El('div');h.setAttribute('data-clips',k);CONTAINERS[k]=h;LIST[k]=h;body.appendChild(h);});
function $(id){if(!TOP[id]){const d=new El('div');TOP[id]=d;body.appendChild(d);}
  return CONTAINERS[String(id).replace('clips','')]||TOP[id];}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}
// двери, которых у вырезанных функций нет в тесте: разметка строки нас не интересует
function clipActs(){return '';}
function statusTags(){return '';}
function syncDelSel(){}
function syncNav(){}
function syncBuildBtn(){}
function markupSelCount(){}
function renderClips1(){RENDERS[1]++;LIST[1].innerHTML='';
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip';
    el.innerHTML='<label class="pickbox"><input type="checkbox" data-ci="'+i+'" '
      +(c.sel?'checked':'')+'></label>';LIST[1].appendChild(el);});}
function renderClips2(){RENDERS[2]++;LIST[2].innerHTML='';
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip';
    el.innerHTML='<label class="pickbox"><input type="checkbox" data-ci="'+i+'" '
      +(c.sel?'checked':'')+'></label>';LIST[2].appendChild(el);});}
function renderClips3(){RENDERS[3]++;LIST[3].innerHTML='';
  CLIPS.forEach((c,i)=>{const el=document.createElement('div');el.className='clip';
    el.innerHTML='<label class="pickbox"><input type="checkbox" data-ci="'+i+'" '
      +(c.sel?'checked':'')+'></label>';LIST[3].appendChild(el);});}
// dataset в браузере всегда строка — сравниваем текстом, чтобы сценарий мог звать
// checkIn(2, 2) и checkIn(2, '2') одинаково
function checkIn(list,ci){const h=LIST[list];ci=String(ci);
  const row=h.children.filter(r=>r.children.some(c=>String(c.dataset.ci)===ci))[0];
  return row?row.children.find(c=>String(c.dataset.ci)===ci):null;}
"""


def _run(tmp_path, name, funcs, body, decls=("CLIPS", "SEL_ANCHOR")):
    """Заглушки + боевые объявления и функции + сценарий; вернуть разобранный JSON."""
    src = _src()
    script = (STUBS + "\n"
              + "\n".join(_let(src, d) for d in decls) + "\n"
              + "\n".join(_func(src, f) for f in funcs) + "\n"
              + body)
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, ("node упал:\n" + (p.stderr or p.stdout)).strip()[:1200]
    # сценарий печатает ровно одну строку JSON — по ней и разбираем
    line = [x for x in p.stdout.strip().splitlines() if x.startswith("{")][-1]
    assert line, "сценарий ничего не напечатал:\n%s" % (p.stdout or p.stderr)[:1200]
    return json.loads(line)


@node
def test_focus_returns_to_the_same_clip_checkbox_in_the_same_list(tmp_path):
    """1. Фокус на галке клипа 2 списка шага 2 → `pickClip(2,true,false)` → фокус снова
    на галке клипа 2 того же списка, и это НОВЫЙ элемент: старый список уже выброшен.

    Списки перерисовываются целиком, поэтому без возврата фокуса он оставался на `body` —
    с клавиатуры дальше было не пройти. Возврат обязан попасть в свой список: галки трёх
    шагов рисуют одну `c.sel`, но живут в разных контейнерах.
    """
    out = _run(tmp_path, "mp_focus.js", ["pickClip"], r"""
CLIPS=[{name:'A'},{name:'B'},{name:'C'},{name:'D'}];
renderClips1();renderClips2();renderClips3();
const old=checkIn(2,2);document.activeElement=old;
const wasInDoc=old.isConnected,wasFocused=document.activeElement===old;
pickClip(2,true,false);
const now=document.activeElement;
console.log(JSON.stringify({wasInDoc,wasFocused,oldDead:!old.isConnected,
  oldIsNotActive:document.activeElement!==old,
  ci:now.dataset?now.dataset.ci:null,list:(now.closest&&now.closest('[data-clips]')||{}).getAttribute
    ?now.closest('[data-clips]').getAttribute('data-clips'):null,
  inDoc:!!(now.isConnected),checked:!!now.checked,sel:CLIPS.map(c=>!!c.sel),anchor:SEL_ANCHOR,
  calls:FOCUS_CALLS.map(c=>({ci:c.ci,preventScroll:!!(c.opts&&c.opts.preventScroll)})),
  renders:RENDERS}));
""")
    assert out["wasInDoc"] and out["wasFocused"], "подготовка теста: фокус не встал на галку"
    assert out["oldDead"] and out["oldIsNotActive"], (
        "старая галка осталась в документе — тест не проверяет перерисовку")
    assert out["ci"] == "2", "фокус вернулся не на ту галку клипа: %r" % out["ci"]
    assert out["list"] == "2", (
        "фокус вернулся в чужой список (ждали шаг 2): %r" % out["list"])
    assert out["inDoc"] is True, "фокус остался на выброшенном из документа элементе"
    assert out["sel"] == [False, False, True, False], "галка отметила не тот клип: %r" % out["sel"]
    assert out["calls"] == [{"ci": "2", "preventScroll": True}], (
        "фокус вернули не один раз или без preventScroll: %r" % out["calls"])
    assert out["renders"] == {"1": 2, "2": 2, "3": 2}, (
        "списки перерисованы не все (или лишний раз): %r" % out["renders"])


@node
def test_shift_range_keeps_focus_on_the_last_clicked_clip(tmp_path):
    """2. Shift-диапазон: фокус остаётся на последней кликнутой галке, а не уезжает
    на начало списка — иначе клавиатурный Shift-выбор рвётся на первом же шаге.

    Клик по галке 3, затем Shift по галке 1: отмечены 1..3, фокус — на галке 1.
    """
    out = _run(tmp_path, "mp_shift.js", ["pickClip"], r"""
CLIPS=[{name:'A'},{name:'B'},{name:'C'},{name:'D'}];
renderClips1();renderClips2();renderClips3();
document.activeElement=checkIn(2,2);
pickClip(2,true,false);
document.activeElement=checkIn(2,0);
pickClip(0,true,true);
const now=document.activeElement;
console.log(JSON.stringify({sel:CLIPS.map(c=>!!c.sel),anchor:SEL_ANCHOR,
  ci:now.dataset?now.dataset.ci:null,
  list:now.closest('[data-clips]').getAttribute('data-clips'),inDoc:!!now.isConnected,
  calls:FOCUS_CALLS.map(c=>c.ci),renders:RENDERS}));
""")
    assert out["sel"] == [True, True, True, False], (
        "Shift не отметил диапазон: %r" % out["sel"])
    assert out["anchor"] == 0, "точка отсчёта диапазона не сдвинулась на последнюю галку"
    assert out["ci"] == "0", (
        "после Shift-диапазона фокус уехал не на последнюю кликнутую галку: %r" % out["ci"])
    assert out["list"] == "2", "фокус после Shift вернулся в чужой список: %r" % out["list"]
    assert out["inDoc"] is True, "фокус остался на выброшенном элементе"
    assert out["calls"] == ["2", "0"], (
        "фокус возвращали не после каждой правки галки: %r" % out["calls"])
    assert out["renders"] == {"1": 3, "2": 3, "3": 3}, (
        "на две правки галок списки перерисованы не по три раза: %r" % out["renders"])


@node
def test_focus_outside_a_checkbox_is_not_stolen(tmp_path):
    """3. Фокус был не на галке (клик мышью по пустому месту, фокус на кнопке) — после
    `pickClip` он остаётся там же: перетаскивать фокус на галку нельзя, человек её не трогал.

    Признак галки — `data-ci` на самом checkbox: у всего остального в строке его нет.
    """
    out = _run(tmp_path, "mp_outside.js", ["pickClip"], r"""
CLIPS=[{name:'A'},{name:'B'}];
renderClips1();renderClips2();renderClips3();
const btn=document.createElement('button');btn.id='toStep2';body.appendChild(btn);
document.activeElement=btn;
pickClip(1,true,false);
const now=document.activeElement;
console.log(JSON.stringify({tag:now.tag,id:now.id||'',focusedCheck:!!(now.dataset&&now.dataset.ci),
  sel:CLIPS.map(c=>!!c.sel),calls:FOCUS_CALLS.length,renders:RENDERS}));
""")
    assert out["sel"] == [False, True], "галка отметила не тот клип: %r" % out["sel"]
    assert out["focusedCheck"] is False, "фокус перескочил на галку, хотя его там не было"
    assert out["tag"] == "button" and out["id"] == "toStep2", (
        "фокус ушёл с кнопки: %r/%r" % (out["tag"], out["id"]))
    assert out["calls"] == 0, "focus() звали, хотя фокуса на галке не было: %d" % out["calls"]
    assert out["renders"] == {"1": 2, "2": 2, "3": 2}, (
        "списки перерисованы не все: %r" % out["renders"])


def test_checkboxes_carry_data_ci_and_lists_carry_data_clips():
    """Признаки, по которым `pickClip` находит галку после перерисовки, — на месте везде.

    Галка клипа — `data-ci="<индекс>"` на самом checkbox, список — `data-clips` на
    контейнере строк (`#clips1/2/3`): без любого из них возврат фокуса молча ничего не найдёт
    (а `preventScroll` в возврате — чтобы страница не дёргалась к галке).
    """
    js = _src()
    html = io.open(HTML, encoding="utf-8").read()

    for name in ("renderClips1", "renderClips2", "renderClips3"):
        body = _func(js, name)
        assert 'data-ci="' in body, "%s: у галки клипа нет признака data-ci" % name
        assert "data-ci=\"'+i+'\"" in body, (
            "%s: в data-ci галки не индекс клипа" % name)

    pick = _func(js, "pickClip")
    assert "document.activeElement" in pick, (
        "pickClip не запоминает галку с фокусом до перерисовки")
    assert "closest('[data-clips]')" in pick, (
        "список галки ищется не по контейнеру data-clips")
    assert "getAttribute('data-clips')" in pick and "document.contains(" in pick, (
        "pickClip не отличает живую галку от выброшенной из документа")
    assert "preventScroll:true" in pick, (
        "фокус возвращается без preventScroll — страница поедет к галке")
    assert "renderClips3();" in pick.split("preventScroll")[0], (
        "фокус возвращается до перерисовки всех трёх списков")

    for i in (1, 2, 3):
        tag = '<div id="clips%d" class="clips" data-clips="%d">' % (i, i)
        assert tag in html, "у контейнера списка шага %d нет data-clips" % i
