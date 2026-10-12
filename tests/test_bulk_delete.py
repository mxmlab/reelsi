# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Удаление клипов и подкастов: одна механика на всех шагах — «удалил = в корзину».

Раньше на шаге 1 были метла «убрать из списка» и окно с выбором из двух; теперь нет ни
метл, ни списочного удаления. Удаление — одно окно: сухой прогон сразу, галка «И исходники»
(по умолчанию снята), общий исходник серым и без галки. Корзина — клипы и подкасты одним
списком; очистка корзины — в ⚙ → «Нарезка», с подтверждением.

Тесты гоняют БОЕВЫЕ функции из `static/app/40-queue.js` под node с заглушками (как другие
тесты интерфейса): функция вырезается по балансу скобок, состояние и внешние двери — заглушки
(сервер через fetch, DOM, тосты). Кнопки шапок берутся из настоящей разметки
templates/index.html и разбираются как в DOM. Проверяется:

1. Shift-диапазон: клик по 2-му, затем Shift по 5-му — отмечены 2..5; Shift-снятие снимает
   диапазон, точка отсчёта — последняя кликнутая галка;
2. окно открывается сразу и сухим прогоном смотрит каждый клип (batch — все XML пачки);
3. «Удалить» шлёт dry:false по каждому клипу; галка «И исходники» — sources:true только когда
   её поставили; ошибка одного клипа не останавливает остальные; итог — в тост и журнал;
4. общий исходник (нужен другому клипу) серый, галка недоступна, sources:false;
5. кнопка «Удалить выбранные» погашена при пустом выборе и не открывает окно без галочек;
6. корзина: клипы и подкасты одним списком, метка «подкаст», podcast_root, возврат подкаста
   перечитывает подкасты, а папки подкастов ещё нет — сервер её пропускает, клипы показаны;
7. очистка корзины: сухой прогон, вопрос с числами, ошибки в тост и журнал;
8. статика: шапки трёх шагов, отсутствие метл и окна «убрать из списка», кнопка очистки в ⚙.

Запуск: python -m pytest tests/test_bulk_delete.py -q
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
WIDGETS_JS = os.path.join(ROOT, "static", "app", "20-widgets.js")
CSS = os.path.join(ROOT, "static", "app.css")
HTML = os.path.join(ROOT, "templates", "index.html")

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _src():
    return io.open(QUEUE_JS, encoding="utf-8").read()


def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок.

    `async` — часть объявления: без него у вырезанной функции остаётся `await` в теле,
    и node роняет скрипт ещё до проверок (в других тестах UI ровно та же вырезка).
    """
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
    """Объявление состояния верхнего уровня — им пользуются вырезанные функции."""
    m = re.search(r"^let %s=.*$" % re.escape(name), src, re.M)
    assert m, "в 40-queue.js нет объявления let %s" % name
    return m.group(0)


_ATTR_RE = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?')


def _attrs(open_tag):
    """Атрибуты открывающего тега: {имя: значение} (флаг без значения -> "").

    Разбор, а не поиск подстроки: `disabled` — это АТРИБУТ кнопки, а не слово в куске
    разметки (на этом старый сторож пропускал любую подмену).
    """
    body = open_tag[open_tag.index(" ") + 1:open_tag.rindex(">")]
    return {m.group(1): (m.group(2) if m.group(2) is not None else "")
            for m in _ATTR_RE.finditer(body)}


def _delsel_buttons(html=None):
    """Разметка кнопок «Удалить выбранные» из шапок трёх шагов — куском HTML как есть."""
    html = io.open(HTML, encoding="utf-8").read() if html is None else html
    out = []
    for m in re.finditer(r"<button\b", html):
        end = html.find("</button>", m.start())
        assert end > 0, "у кнопки шапки не нашлось закрывающего тега"
        chunk = html[m.start():end + len("</button>")]
        if "data-delsel" in _attrs(chunk[:chunk.index(">") + 1]):
            out.append(chunk)
    return out


def _header_of(html, element_id):
    """Заголовок секции (`<h2>…</h2>`), в котором стоит элемент с данным id."""
    idx = html.index('id="%s"' % element_id)
    start = html.rfind("<h2>", 0, idx)
    end = html.index("</h2>", idx)
    assert start >= 0 and end > idx, "элемент %s не в заголовке <h2>" % element_id
    return html[start:end]


# Заглушки — только внешние двери (сервер, DOM, тосты). Всё, что проверяется, — боевые
# функции из 40-queue.js: тест не должен сторожить свою копию логики.
# Кнопки-корзины берутся из боевой разметки (templates/index.html) и разбираются как в
# DOM: флаг `disabled` и `data-*` — атрибуты открывающего тега, доступные свойством.
STUBS = r"""
let TOASTS=[],LOGS=[],OPENED=[],CLOSED=[],SAVES=0,RENDERS={1:0,2:0,3:0};
// Элемент с минимумом DOM: текст, дети (для строк корзины), свойство innerHTML сбрасывает детей
class El{
  constructor(id){this.id=id||'';this.style={};this.textContent='';this.disabled=false;
    this.className='';this.kids=[];this._h='';this.value='';this.onclick=null;this.attrs={};}
  get innerHTML(){return this._h;}
  set innerHTML(v){this._h=String(v);this.kids=[];}
  appendChild(c){this.kids.push(c);return c;}
  setAttribute(k,v){this.attrs[k]=String(v);}
}
const ELS={};
function $(id){if(!ELS[id])ELS[id]=new El(id);return ELS[id];}
function val(id){return $(id).value||'';}
function txt(e){return e.textContent+(e.kids||[]).map(txt).join('');}
function parseAttrs(body){
  const out={};
  const re=/([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;
  let m;
  while((m=re.exec(body))!==null)out[m[1]]=(m[2]===undefined?'':m[2]);
  return out;
}
function Button(html){
  const open=html.slice(0,html.indexOf('>')+1);
  const attrs=parseAttrs(open.slice(open.indexOf(' ')+1,open.length-1));
  this.id=attrs.id||'';
  this.attrs=attrs;
  this.style={};
  this.disabled=Object.prototype.hasOwnProperty.call(attrs,'disabled');
  this.className=attrs.class||'';
  this.dataset={};
  for(const k in attrs)if(k.indexOf('data-')===0)this.dataset[k.slice(5)]=attrs[k];
  this.innerHTML=html.slice(open.length,html.lastIndexOf('</'));
  this.getAttribute=n=>Object.prototype.hasOwnProperty.call(attrs,n)?attrs[n]:null;
  this.hasAttribute=n=>Object.prototype.hasOwnProperty.call(attrs,n);
}
const DELBTNS=@DELBTNS@.map(h=>new Button(h));
const document={querySelectorAll:sel=>(sel==='[data-delsel]'?DELBTNS:[]),createElement:tag=>new El(tag)};
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function plur(n,one,few,many){n=Math.abs(n)%100;const d=n%10;return (n>10&&n<20)?many:(d>1&&d<5)?few:(d===1)?one:many;}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}
function errText(d){return String((d&&(d.error||d))||'');}
function effOutdir(c){return null;}
function toast(m){TOASTS.push(String(m));}
function uiLog(m){LOGS.push(String(m));}
function openModal(id){OPENED.push(id);}
function closeModal(id){CLOSED.push(id);}
function saveState(){SAVES++;}
function syncNav(){}
function renderClips1(){RENDERS[1]++;}
function renderClips2(){RENDERS[2]++;}
function renderClips3(){RENDERS[3]++;}
let curAE=-1,curEdit=-1,curIns=-1,AEGLOBAL='';
"""


def _run(tmp_path, name, funcs, body, decls=("CLIPS", "SEL_ANCHOR", "DEL_CLIP_IDXS", "TRASHROOTS")):
    """Заглушки + боевые объявления и функции + сценарий; вернуть разобранный JSON."""
    src = _src()
    script = (STUBS.replace("@DELBTNS@", json.dumps(_delsel_buttons(), ensure_ascii=False)) + "\n"
              + "\n".join(_let(src, d) for d in decls) + "\n"
              + "\n".join(_func(src, f) for f in funcs) + "\n"
              + body)
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


CLIP_FUNCS = ["clipLabel", "delClips", "delClipCall", "delClipDiskPrepare",
              "delClipDiskExecute", "_spliceClip", "trashRemember", "trashDirOf"]


@node
def test_shift_click_selects_the_whole_range(tmp_path):
    """1. Клик по 2-му, Shift по 5-му — отмечены 2..5; Shift-снятие снимает диапазон.

    Точка отсчёта — общая переменная SEL_ANCHOR: без неё Shift отмечал бы «от начала
    списка», а не от прошлой кликнутой галки. Списки трёх шагов рисуют одну c.sel,
    поэтому каждая правка обязана перерисовать все три — иначе галки разъедутся.
    """
    out = _run(tmp_path, "zs_shift.js", ["pickClip"], r"""
CLIPS=[{name:'A'},{name:'B'},{name:'C'},{name:'D'},{name:'E'}];
pickClip(1,true,false);
const afterClick=CLIPS.map(c=>!!c.sel);
pickClip(4,true,true);
const afterShift=CLIPS.map(c=>!!c.sel);
const anchor=SEL_ANCHOR;
pickClip(1,false,true);
const afterUnshift=CLIPS.map(c=>!!c.sel);
console.log(JSON.stringify({afterClick,afterShift,anchor,afterUnshift,renders:RENDERS}));
""")
    assert out["afterClick"] == [False, True, False, False, False], (
        "обычный клик по галке отмечает не один клип: %r" % out["afterClick"])
    assert out["afterShift"] == [False, True, True, True, True], (
        "Shift не отметил диапазон от прошлой кликнутой галки: %r" % out["afterShift"])
    assert out["anchor"] == 4, "точка отсчёта не запомнила последнюю кликнутую галку"
    assert out["afterUnshift"] == [False, False, False, False, False], (
        "Shift-снятие не сняло диапазон: %r" % out["afterUnshift"])
    assert out["renders"] == {"1": 3, "2": 3, "3": 3}, (
        "после правки галок перерисованы не все три списка: %r" % out["renders"])


@node
def test_window_opens_at_once_and_dry_runs_every_clip(tmp_path):
    """2. Окно открывается сразу, без экрана выбора; сухой прогон — по каждому клипу.

    Каждый запрос несёт batch — все XML пачки (их исходники сервер не считает общими
    с пачкой). Галка «И исходники» по умолчанию снята; без общих исходников — недоступна.
    """
    out = _run(tmp_path, "zs_window.js", CLIP_FUNCS, r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'},{name:'C',xml:'C:/out/C.xml'},
       {name:'D',xml:'C:/out/D.xml'}];
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push(b);
  return {json:async()=>({ok:true,cams:[],files:[{path:b.xml.replace('.xml','.cuts.json'),size:1048576}],
    bytes:1048576,sources:[]})};};
(async()=>{
  $('delClipSrc').checked=true;          // после прошлого открытия галка могла остаться
  await delClips([0,2,3]);
  console.log(JSON.stringify({opened:OPENED.slice(),title:$('delClipTitle').textContent,
    names:$('delClipName').textContent,
    calls:CALLS.map(c=>({url:c.xml,dry:c.dry,batch:c.batch})),
    info:$('delClipInfo').textContent,pending:DEL_CLIP_IDXS.length,
    srcChecked:$('delClipSrc').checked,srcDisabled:$('delClipSrc').disabled,
    confirmDisabled:$('delClipDiskConfirmBtn').disabled}));
})();
""")
    assert out["opened"] == ["mbDelClip"], "окно удаления открылось не то"
    assert out["title"] == "Удалить 3 клипа?", "в заголовке окна нет «N клипов»: %r" % out["title"]
    assert out["names"] == "A, C, D", "в окне не видно, какие клипы удаляются: %r" % out["names"]
    batch = ["C:/out/A.xml", "C:/out/C.xml", "C:/out/D.xml"]
    assert [c["url"] for c in out["calls"]] == batch, "сухой прогон не прошёл по каждому клипу"
    assert all(c["dry"] is True for c in out["calls"]), "сухой прогон ушёл как настоящее удаление"
    assert all(c["batch"] == batch for c in out["calls"]), "в сухом прогоне нет пачки удаления"
    assert out["info"].startswith("Файлы нарезки: 3 (3.00 МБ) — уйдут в корзину, можно вернуть"), (
        "строка про файлы нарезки не та: %r" % out["info"])
    assert out["srcChecked"] is False, "галка «И исходники» не снята по умолчанию"
    assert out["srcDisabled"] is True, "галка доступна, хотя исходников на диске нет"
    assert out["confirmDisabled"] is False, "кнопка «Удалить» не ожила после сухого прогона"
    assert out["pending"] == 3, "список удаляемых не запомнен на время окна"


@node
def test_delete_sends_real_calls_and_sources_only_when_ticked(tmp_path):
    """3. «Удалить» шлёт dry:false по каждому клипу; sources:true — только при отметке галки.

    Ошибка клипа B не останавливает остальных; из списка убираем всех, кого пытались удалить;
    итог — тост, ошибка поимённо — в журнал.
    """
    out = _run(tmp_path, "zs_delete.js", CLIP_FUNCS, r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'},{name:'C',xml:'C:/out/C.xml'}];
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);
  CALLS.push({dry:b.dry,xml:b.xml,sources:b.sources,batch:b.batch});
  if(b.xml.indexOf('B.xml')>=0)return {json:async()=>({error:'файл занят'})};
  return {json:async()=>({ok:true,cams:[],files:[{path:b.xml,size:1048576}],bytes:1048576,
    sources:[{path:'C:/cam/X.mp4',size:2097152,shared_with:[]}]})};};
(async()=>{
  // 1) галка не отмечена: исходники не уносим
  await delClips([0,1,2]);
  const srcListed=$('delClipSrcList').innerHTML;
  CALLS.length=0;TOASTS.length=0;LOGS.length=0;
  await delClipDiskExecute();
  const unticked=CALLS.filter(c=>!c.dry);
  const toastUnticked=TOASTS.slice(), logUnticked=LOGS.slice();
  const leftAfterFirst=CLIPS.length;
  // 2) галка отмечена: исходник X свободен — уносим
  CLIPS=[{name:'D',xml:'C:/out/D.xml'}];
  await delClips([0]);
  $('delClipSrc').checked=true;
  CALLS.length=0;
  await delClipDiskExecute();
  const ticked=CALLS.filter(c=>!c.dry);
  console.log(JSON.stringify({srcListed,unticked,toast:toastUnticked,log:logUnticked,leftAfterFirst,
    ticked,tickedSources:ticked.map(c=>c.sources),renders:RENDERS}));
})();
""")
    assert "X.mp4" in out["srcListed"] and "нужен ещё" not in out["srcListed"], (
        "свободный исходник не показан в окне: %r" % out["srcListed"])
    assert [c["xml"] for c in out["unticked"]] == ["C:/out/A.xml", "C:/out/B.xml", "C:/out/C.xml"], (
        "удаление не прошло по каждому выбранному клипу: %r" % out["unticked"])
    assert all(c["sources"] is False for c in out["unticked"]), (
        "без галки исходники уходят в запросе: %r" % out["unticked"])
    assert all(c["batch"] == ["C:/out/A.xml", "C:/out/B.xml", "C:/out/C.xml"] for c in out["unticked"]), (
        "в удалении нет пачки — общие исходники не отличить")
    assert out["toast"] and "В корзине: клипов 2, файлов 2 (2.00 МБ)" in out["toast"][0], (
        "итог удаления не показан тостом: %r" % out["toast"])
    assert "ошибок: 1" in out["toast"][0], "в тосте нет числа ошибок: %r" % out["toast"]
    assert any("B: файл занят" in m for m in out["log"]), (
        "ошибка не записана поимённо в журнал: %r" % out["log"])
    assert out["leftAfterFirst"] == 0, "из списка убраны не все, кого пытались удалить"
    assert [c["xml"] for c in out["ticked"]] == ["C:/out/D.xml"]
    assert out["tickedSources"] == [True], (
        "при отмеченной галке исходник не отмечен к уносу: %r" % out["tickedSources"])


@node
def test_удалили_открытый_клип_панель_шага_3_пересаживается(tmp_path):
    """Регрессия: раньше removeSelClips после удаления садил панель шага 3 на живой клип
    (selectAE(0)), а при пустом списке прятал #aecfg и снимал AEXML. Логика пропала вместе с
    функцией; теперь она в общем пути удаления delClipDiskExecute, после _spliceClip.
    Панель трогаем только если открытый клип сам ушёл из списка."""
    out = _run(tmp_path, "zs_panel.js", CLIP_FUNCS, r"""
const CALLS=[];const SELS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push(b);
  return {json:async()=>({ok:true,cams:[],files:[{path:b.xml,size:1}],bytes:1,sources:[]})};};
function selectAE(i){SELS.push(i);AEXML=CLIPS[i].xml;}
let AEXML='';
(async()=>{
  const res={};
  // 1) открыт B, удалили B, живые A и C — панель садится на первый живой (selectAE(0))
  CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'},{name:'C',xml:'C:/out/C.xml'}];
  curAE=1;AEXML='C:/out/B.xml';
  await delClips([1]);await delClipDiskExecute();
  res.one={sels:SELS.slice(),curAE,names:CLIPS.map(c=>c.name)};
  // 2) открыт D, удалили все — панель прячется, AEXML пусто
  CLIPS=[{name:'D',xml:'C:/out/D.xml'}];SELS.length=0;
  curAE=0;AEXML='C:/out/D.xml';$('aecfg').style.display='block';
  await delClips([0]);await delClipDiskExecute();
  res.all={sels:SELS.slice(),aecfg:$('aecfg').style.display,AEXML,curAE};
  // 3) открыт A, удалили только B — панель не трогаем
  CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'}];SELS.length=0;
  curAE=0;AEXML='C:/out/A.xml';
  await delClips([1]);await delClipDiskExecute();
  res.other={sels:SELS.slice(),AEXML,curAE};
  console.log(JSON.stringify(res));
})();
""")
    assert out["one"] == {"sels": [0], "curAE": -1, "names": ["A", "C"]}, (
        "после удаления открытого клипа панель не села на живой клип: %r" % out["one"])
    assert out["all"]["sels"] == [], out["all"]
    assert out["all"]["aecfg"] == "none", "панель не спрятана при пустом списке: %r" % out["all"]
    assert out["all"]["AEXML"] == "", "принадлежность панели не снята: %r" % out["all"]
    assert out["other"]["sels"] == [] and out["other"]["AEXML"] == "C:/out/A.xml", (
        "панель тронута, хотя открытый клип остался: %r" % out["other"])


@node
def test_клип_из_чужой_папки_виден_в_корзине_и_стирается(tmp_path):
    """Клип из папки, которой нет в папке результата («Добавить XML…»), держит корзину у себя.
    После удаления его папка запоминается (TRASHROOTS) и уходит в запрос корзины и очистки —
    даже когда клипа в списке уже нет. Список запомненных — не больше 50, дубль — один раз."""
    out = _run(tmp_path, "zs_trashroots.js", CLIP_FUNCS + ["trashCall"], r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/cut/B.xml'}];
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push({url:url,b:b});
  return {json:async()=>({ok:true,cams:[],files:[{path:b.xml,size:1}],bytes:1,sources:[],items:[]})};};
(async()=>{
  await delClips([1]);
  await delClipDiskExecute();
  const afterDelete={trash:TRASHROOTS.slice(),names:CLIPS.map(c=>c.name)};
  CALLS.length=0;
  await trashCall('/api/trash_list',{dir:'C:/out',podcast_root:'C:/pod'});
  const listed=CALLS[CALLS.length-1];
  CLIPS=[];                                  // клипа B в списке уже нет — его папка всё равно помнится
  await trashCall('/api/trash_empty',{dir:'C:/out',podcast_root:'C:/pod',dry:true});
  const emptied=CALLS[CALLS.length-1];
  // дубль по регистру и «/» в конце — одна папка; больше 50 — вытесняются старые
  trashRemember('c:/CUT/');
  const dup=TRASHROOTS.filter(x=>x.toLowerCase()==='c:/cut').length;
  for(let i=0;i<60;i++)trashRemember('C:/many/'+i);
  console.log(JSON.stringify({afterDelete,listedDirs:listed.b.dirs,emptiedDirs:emptied.b.dirs,
    dup:dup,len:TRASHROOTS.length,
    first:TRASHROOTS[0],last:TRASHROOTS[TRASHROOTS.length-1]}));
})();
""")
    assert out["afterDelete"] == {"trash": ["C:/cut"], "names": ["A"]}, (
        "папка клипа из чужой папки не запомнилась: %r" % out["afterDelete"])
    assert out["listedDirs"] == ["C:/out", "C:/cut"], (
        "корзина не получает папку удалённого клипа: %r" % out["listedDirs"])
    assert out["emptiedDirs"] == ["C:/out", "C:/cut"], (
        "очистка не видит папку, где лежит корзина клипа: %r" % out["emptiedDirs"])
    assert out["dup"] == 1, "та же папка в другом написании попала в список дважды"
    assert out["len"] == 50 and out["last"] == "C:/many/59" and out["first"] == "C:/many/10", (
        "ограничение 50 записей не работает: %r" % out)


@node
def test_shared_source_is_grey_unavailable_and_never_sent(tmp_path):
    """4. Исходник, который нужен другому клипу, — серым с причиной; галка недоступна.

    Даже если галку каким-то путём отметили, при недоступной галке запрос уходит с
    sources:false: общий исходник не должен уехать в корзину вместе с клипом.
    """
    out = _run(tmp_path, "zs_shared.js", CLIP_FUNCS, r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'}];
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push({dry:b.dry,sources:b.sources});
  return {json:async()=>({ok:true,cams:[],files:[{path:b.xml,size:1048576}],bytes:1048576,
    sources:[{path:'C:/cam/X.mp4',size:2097152,shared_with:['Other']}]})};};
(async()=>{
  await delClips([0]);
  const disabled=$('delClipSrc').disabled, list=$('delClipSrcList').innerHTML;
  $('delClipSrc').checked=true;          // «отметили» мимо недоступной галки
  CALLS.length=0;
  await delClipDiskExecute();
  console.log(JSON.stringify({disabled,list,sent:CALLS.filter(c=>!c.dry).map(c=>c.sources)}));
})();
""")
    assert out["disabled"] is True, "галка доступна, хотя исходник нужен другому клипу"
    assert "нужен ещё клипам: Other, не удаляется" in out["list"], (
        "серый исходник без причины: %r" % out["list"])
    assert "color:var(--mut)" in out["list"], "общий исходник не выделен серым"
    assert out["sent"] == [False], "общий исходник ушёл в запрос с sources:true: %r" % out["sent"]


@node
def test_delete_selected_button_is_dead_with_empty_selection(tmp_path):
    """5. Кнопки «Удалить выбранные» — настоящая разметка шапок, и боевой syncDelSel
    гасит и зажигает их по выбору: разметка приходит погашенной, пустой выбор оставляет
    их погашенными, одна галка включает все три.

    Пусто у всех НЕ значит «все»: это семантика selClips() для сборки, у удаления она
    опасна — снесла бы весь список. С галочкой кнопка живая и открывает окно на список.
    """
    out = _run(tmp_path, "zs_button.js", ["syncDelSel", "delSelClips"] + CLIP_FUNCS, r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'}];
globalThis.fetch=async()=>({json:async()=>({ok:true,files:[],bytes:0,sources:[]})});
const ids=DELBTNS.map(b=>b.id);
const handlers=DELBTNS.map(b=>b.getAttribute('onclick'));
const labels=DELBTNS.map(b=>!!b.getAttribute('aria-label'));
const tips=DELBTNS.map(b=>!!b.getAttribute('data-t'));
const icons=DELBTNS.map(b=>/<span[^>]*data-ic="trash"/.test(b.innerHTML));
const classes=DELBTNS.map(b=>b.className);
const markupDisabled=DELBTNS.map(b=>b.disabled);
syncDelSel();
const emptyDisabled=DELBTNS.map(b=>b.disabled);
delSelClips();
const openedOnEmpty=OPENED.length,toastEmpty=TOASTS[0]||'';
CLIPS[1].sel=true;
syncDelSel();
const oneDisabled=DELBTNS.map(b=>b.disabled);
(async()=>{
  await delSelClips();
  console.log(JSON.stringify({ids,handlers,labels,tips,icons,classes,markupDisabled,emptyDisabled,
    oneDisabled,openedOnEmpty,toastEmpty,
    opened:OPENED,title:$('delClipTitle').textContent,name:$('delClipName').textContent}));
})();
""")
    # разметка: три кнопки, у каждой свой обработчик, справка и иконка из набора ico
    assert out["ids"] == ["delSel1", "delSel2", "delSel3"], (
        "кнопок «Удалить выбранные» не три: %r" % out["ids"])
    assert out["handlers"] == ["delSelClips()"] * 3, (
        "кнопки шапок зовут не delSelClips(): %r" % out["handlers"])
    assert out["labels"] == [True, True, True], "у кнопки нет aria-label: %r" % out["labels"]
    assert out["tips"] == [True, True, True], "у кнопки нет справки data-t: %r" % out["tips"]
    assert out["icons"] == [True, True, True], "у кнопки не иконка из набора ico: %r" % out["icons"]
    assert all(c.split() == ["sm", "sq"] for c in out["classes"]), (
        "кнопка не квадратная в рамке (класс sm sq): %r" % out["classes"])

    # поведение: разметка погашена, пустой выбор её не зажигает, галка — зажигает все три
    assert out["markupDisabled"] == [True, True, True], (
        "кнопки приходят из разметки живыми (нет атрибута disabled): %r" % out["markupDisabled"])
    assert out["emptyDisabled"] == [True, True, True], (
        "кнопки «Удалить выбранные» живые при пустом выборе: %r" % out["emptyDisabled"])
    assert out["openedOnEmpty"] == 0, "без галочек открылось окно удаления"
    assert "Ничего не отмечено" in out["toastEmpty"], (
        "без галочек нет внятного сообщения: %r" % out["toastEmpty"])
    assert out["oneDisabled"] == [False, False, False], (
        "с отмеченным клипом кнопки остались погашенными: %r" % out["oneDisabled"])
    assert out["opened"] == ["mbDelClip"], "кнопка открывает не то окно"
    assert out["title"] == "Удалить клип?" and out["name"] == "B", (
        "окно показывает не тот клип: %r / %r" % (out["title"], out["name"]))


@node
def test_trash_lists_clips_and_podcasts_and_restores_by_kind(tmp_path):
    """6. Корзина: клипы и подкасты одним списком, метка «подкаст», podcast_root; возврат
    подкаста перечитывает подкасты, клипа — папку результата. Папки подкастов ещё нет —
    сервер её пропускает: запрос один, с обеими папками, клипы показаны без повтора.
    """
    out = _run(tmp_path, "zs_trash.js",
               ["trashRoots", "trashCall", "trashDirOf", "trashNote", "trashWhen", "trashRow", "openTrash", "restoreTrashItem"], r"""
$('ai_outdir').value='C:/out';
function podcastRootVal(){return 'C:/pod';}
const PODS=[],SCANS=[];
function podcastList(){PODS.push(1);}
function scanOutdir(){SCANS.push(1);}
const CALLS=[];
const ITEMS=[
  {id:'20261012-100000_Выпуск',kind:'podcast',stem:'',title:'Выпуск',deleted_at:'2026-10-12T10:00:00',
   files:2,bytes:1048576,sources:1,root:'C:/pod'},
  {id:'20261012-090000_A',kind:'clip',stem:'A',title:'A',deleted_at:'2026-10-12T09:00:00',
   files:3,bytes:2097152,sources:0,root:'C:/out'}];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push({url:url,b:b});
  if(url==='/api/trash_list')return {json:async()=>({ok:true,items:ITEMS})};   // сервер пропускает пустую папку подкастов
  return {json:async()=>({ok:true,restored:['x'],skipped:[]})};};
(async()=>{
  await openTrash();
  const rows=$('trashList').kids.map(txt);
  const listReq=CALLS[0].b;
  await $('trashList').kids[0].kids[1].onclick();      // «Вернуть» подкаста
  const restore=CALLS[CALLS.length-1];
  const afterPodcast={pods:PODS.length,scans:SCANS.length};
  CALLS.length=0;
  await openTrash();
  console.log(JSON.stringify({rows,listReq,restoreUrl:restore.url,restoreReq:restore.b,afterPodcast,
    again:CALLS.map(c=>c.b),shown:$('trashList').kids.length}));
})();
""")
    assert out["listReq"] == {"dir": "C:/out", "podcast_root": "C:/pod", "dirs": ["C:/out"]}, (
        "корзина не получает обе папки: %r" % out["listReq"])
    rows = out["rows"]
    assert len(rows) == 2 and "подкаст" in rows[0] and "Выпуск" in rows[0], rows
    assert "подкаст" not in rows[1], "метка «подкаст» у клипа"
    assert "+ исходники: 1" in rows[0] and "+ исходники" not in rows[1], rows
    assert out["restoreUrl"] == "/api/trash_restore"
    assert out["restoreReq"] == {"root": "C:/pod", "id": "20261012-100000_Выпуск"}, out["restoreReq"]
    assert out["afterPodcast"] == {"pods": 1, "scans": 0}, (
        "после возврата подкаста перечитан не список подкастов: %r" % out["afterPodcast"])
    # папки подкастов нет: сервер её пропускает — один запрос с обеими папками, без повтора
    assert out["again"] == [{"dir": "C:/out", "podcast_root": "C:/pod", "dirs": ["C:/out"]}], out["again"]
    assert out["shown"] == 2, "без папки подкастов клипы не показаны: %r" % out["shown"]


@node
def test_trash_empty_asks_with_counts_and_reports_failures(tmp_path):
    """7. Очистка корзины: сухой прогон для счётчика, вопрос с числами, настоящий прогон,
    ошибки — в тост и журнал, счётчик после очистки обновлён."""
    out = _run(tmp_path, "zs_trash_empty.js",
               ["trashRoots", "trashCall", "trashDirOf", "trashCountLoad", "trashEmpty"], r"""
$('ai_outdir').value='C:/out';
function podcastRootVal(){return 'C:/pod';}
const ASKS=[];
function askConfirm(text){ASKS.push(text);return Promise.resolve(true);}
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const b=JSON.parse(opt.body);CALLS.push(b);
  if(b.dry)return {json:async()=>({ok:true,entries:2,bytes:2147483648})};
  return {json:async()=>({ok:true,removed:2,bytes:2147483648,failed:[{path:'C:/pod/_reelsi_trash/x',why:'занят'}]})};};
(async()=>{
  await trashCountLoad();
  const sum=$('trashSum').textContent, enabled=!$('trashEmptyBtn').disabled;
  CALLS.length=0;
  await trashEmpty();
  console.log(JSON.stringify({sum,enabled,asks:ASKS,
    calls:CALLS.map(b=>({dry:b.dry,dir:b.dir,pod:b.podcast_root})),
    toast:TOASTS.slice(),log:LOGS.slice(),afterSum:$('trashSum').textContent}));
})();
""")
    assert out["sum"] == "В корзине: 2 записей, 2.00 ГБ", out["sum"]
    assert out["enabled"] is True, "кнопка очистки погашена при непустой корзине"
    assert out["asks"] == ["Удалить насовсем 2 записей (2.00 ГБ)? Вернуть будет нельзя."], out["asks"]
    dry = out["calls"][0]
    assert dry == {"dry": True, "dir": "C:/out", "pod": "C:/pod"}, dry
    assert {"dry": False, "dir": "C:/out", "pod": "C:/pod"} in out["calls"], (
        "настоящая очистка не ушла: %r" % out["calls"])
    assert any("Освобождено 2.00 ГБ" in m and "ошибок: 1" in m for m in out["toast"]), out["toast"]
    assert any("занят" in m for m in out["log"]), "ошибка очистки не в журнале: %r" % out["log"]
    assert out["afterSum"] == "В корзине: 2 записей, 2.00 ГБ", "счётчик не обновлён после очистки"


# --- статика: шапки трёх шагов, удалённые пути, кнопка очистки в ⚙ ---

def test_each_step_header_has_trash_and_square_delete_button():
    """На шагах 1, 2 и 3 в шапке рядом: «Корзина» (openTrash, иконка bin) и квадратная
    «Удалить выбранные» (sm sq) — в таком порядке. Больше в шапке ничего не меняем."""
    html = io.open(HTML, encoding="utf-8").read()
    widgets = io.open(WIDGETS_JS, encoding="utf-8").read()
    css = io.open(CSS, encoding="utf-8").read()
    assert "bin:'<path" in widgets, "иконки bin нет в 20-widgets.js"
    assert "button.sm.sq{" in css, "класса квадратной кнопки sq нет в app.css"
    for n in (1, 2, 3):
        assert '<button class="sm sq" id="delSel%d"' % n in html, (
            "шаг %d: кнопка «Удалить выбранные» не квадратная в рамке" % n)
        head = _header_of(html, "delSel%d" % n)
        assert 'onclick="openTrash()"' in head, "шаг %d: в шапке нет «Корзины»" % n
        assert 'data-ic="bin"' in head, "шаг %d: у «Корзины» не иконка bin" % n
        assert head.index('onclick="openTrash()"') < head.index('id="delSel%d"' % n), (
            "шаг %d: «Корзина» должна стоять перед «Удалить выбранные»" % n)


def test_removed_list_only_pieces_are_gone():
    """Метлы и окно «убрать из списка» удалены: ни функций, ни кнопок, ни первого экрана."""
    js = _src()
    html = io.open(HTML, encoding="utf-8").read()
    for name in ("delClipListOnly", "clearClipsList", "removeSelClips", "delClipDiskBack"):
        assert not re.search(r"\bfunction\s+%s\b" % name, js), "%s осталась в 40-queue.js" % name
    for ident in ("delClipInitial", "delClipConfirm", "delClipCams", "clipsClear", "rmSelClips"):
        assert ident not in js and ident not in html, "%s осталась" % ident
    modal = html[html.index('id="mbDelClip"'):html.index('id="mbDelPodcast"')]
    assert "Убрать из списка" not in modal, "в окне удаления осталось «убрать из списка»"
    assert "клип" in modal.lower(), "окно удаления пустое"


def test_delete_window_has_no_remove_path_and_one_disk_call():
    """Удаление клипов с диском: одна дверь (delClipCall), она зовётся и для сухого прогона,
    и для настоящего; точка отсчёта Shift сбрасывается после удаления."""
    js = _src()
    assert js.count("/api/clip_delete") == 1, (
        "удаление с диска обзавелось второй копией — ожидалась одна дверь delClipCall")
    assert "SEL_ANCHOR=-1" in _func(js, "delClipDiskExecute"), (
        "после удаления не сброшена точка отсчёта диапазона")
    assert not re.search(r"\bfunction delClip\(", js), "delClip осталась, хотя её никто не зовёт"
    acts = _func(js, "clipActs")
    assert "delClip(" not in acts and "ico('x')" not in acts, (
        "у строки клипа шага 1 вернулся крестик — удаление только через мусорку по галкам")


def test_disk_prepare_disables_the_checkbox_without_free_sources():
    """Галка «И исходники» включается только при свободном исходнике; общий — серым."""
    js = _src()
    prep = _func(js, "delClipDiskPrepare")
    assert "$('delClipSrc').disabled=!free.length" in prep, (
        "галка доступна при одних общих исходниках")
    assert "shared_with" in prep and "color:'+(s.shared.length?'var(--mut)'" in prep.replace(" ", ""), (
        "общий исходник не выделен серым с причиной")
    execute = _func(js, "delClipDiskExecute")
    assert "!$('delClipSrc').disabled" in execute, (
        "исходники уходят в запросе, даже когда галка недоступна")


def test_trash_settings_block_is_in_cut_tab_with_counter():
    """⚙ → «Нарезка»: блок «Корзина» с «!»-справкой, счётчиком и кнопкой очистки; сводка
    обновляется при заходе на вкладку."""
    html = io.open(HTML, encoding="utf-8").read()
    js = io.open(os.path.join(ROOT, "static", "app", "10-settings.js"), encoding="utf-8").read()
    cut = html[html.index('id="aistab_cut"'):html.index('id="aistab_podcast"')]
    assert 'id="trashEmptyBtn"' in cut and 'onclick="trashEmpty()"' in cut, (
        "в ⚙ → «Нарезка» нет кнопки очистки корзины")
    assert 'id="trashSum"' in cut, "в ⚙ → «Нарезка» нет счётчика корзины"
    assert "Корзина" in cut and 'data-t="Удалённые клипы и подкасты' in cut, (
        "блок «Корзина» без справки о дообучении")
    assert "if(tab==='cut')trashCountLoad()" in js, "сводка корзины не обновляется при заходе"


def test_trash_request_sends_podcast_root_and_guards_empty_folders():
    """Корзина шлёт обе папки; тост «не задана папка» — только когда нет ни той, ни другой."""
    js = _src()
    call = _func(js, "trashCall")
    assert "dirs:dirs" in call and "JSON.stringify(" in call, (
        "запрос корзины не шлёт список папок dirs")
    assert "podcast_root" not in call and "no_folder" not in call, (
        "в корзине остался клиентский обход отсутствующей папки подкастов — сервер её пропускает")
    roots = _func(js, "trashRoots")
    assert "typeof podcastRootVal==='function'" in roots, "podcastRootVal не проверяется по типу"
    body = _func(js, "openTrash")
    assert "if(!r.dir&&!r.pod)" in body, "тост «не задана папка» срабатывает одной папкой"


def test_step1_has_the_shared_checkbox_and_shift_on_all_three_steps():
    """Галки на шаге 1 — те же, что на шагах 2 и 3: одна c.sel, Shift через общий pickClip.

    Шаг 1 раньше галок не имел вовсе. Теперь выбор общий; точка отсчёта Shift сбрасывается
    там, где список перерисован из-за удаления. Поведение Shift — тест выше, здесь разметка.
    """
    js = _src()
    r1 = js[js.index("function renderClips1()"):js.index("function renderClips2()")]
    assert 'class="pickbox"' in r1, "на шаге 1 нет галки выбора"
    assert "CLIPS[" in r1 and ".sel=this.checked" in r1, "галка шага 1 пишет не в c.sel"
    assert "pickClip(" in r1 and "SHIFT_HELD" in r1, "галка шага 1 не зовёт общий выбор диапазона"
    assert "Выбор клипов — общий для всех шагов" in r1, "у галки шага 1 нет общего тултипа"
    r2 = js[js.index("function renderClips2()"):js.index("function markupSelCount(")]
    r3 = js[js.index("function renderClips3()"):js.index("function spkSelHTML(")]
    for name, body in (("шаг 2", r2), ("шаг 3", r3)):
        assert "pickClip(" in body and "SHIFT_HELD" in body, (
            "%s: галка не зовёт общий pickClip" % name)
    assert "SEL_ANCHOR=-1" in _func(js, "delClipDiskExecute"), (
        "после удаления не сброшена точка отсчёта диапазона")
    html = io.open(HTML, encoding="utf-8").read()
    assert len(_delsel_buttons(html)) == 3, "кнопок «Удалить выбранные» не три"
