# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Переключение типа вставки фото↔видео переискивает сток и базу вставок.

Симптом (владелец, 02.10.2026): «при переключении типа вставки с фото на видео не
перебираются сток вставки и библиотеки вставок, они так же стоят на фото, надо чтобы
они перенаходили результаты».

`insToggleType` менял только `x.type`: `stockOpts`/`libOpts` (и флаги `stockShown`/
`libShown`) оставались от прежнего типа, а `insStockFor`/`insLibFor` ищут лишь при пустых
вариантах — открытые панели так и висели с фото. Автоподобранный файл (`libAuto`) прежнего
типа тоже оставался в карточке.

Теперь `insToggleType` сбрасывает варианты, убирает автоподобранный файл чужого типа
(выбранный руками и сгенерённый не трогает), переискивает открытые панели и заново
запускает автоподбор из базы для пустой вставки.

Доправка: карточка перерисовывается сразу после смены типа (до сетевых запросов), а от
двойного клика на время переискивания защищает живой флаг `x._typeBusy` — в состояние
он не пишется (`stateObj` чистит его, как `genBusy`).

Стенд — node: тела боевых функций вырезаются из `static/app/80-inserts.js` (копий формул
нет), `fetch` — заглушка, которая записывает запросы. Сеть, стоки и сервер не поднимаются.

Запуск: py -3.10 -m pytest tests/test_ins_toggle_type.py -q
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

INSERTS_JS = os.path.join(ROOT, "static", "app", "80-inserts.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")

# боевые функции карточки, которых касается смена типа
FUNCS = ("insWant", "insKind", "insSetMedia", "insApplyCrop", "insClipFormat",
         "insToggleType", "insStockFor", "insLibFor", "insLibFill")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name целиком, вместе с `async`, если он есть (иначе `await` отвалится)."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в исходнике не нашлась функция %s" % name
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


STAND = r"""
// ── заглушки окружения: проверяем поведение карточки, а не соседей ────────────
function t(s,v){let o=s;if(v)for(const k in v)o=o.split('{'+k+'}').join(String(v[k]));return o;}
function esc(s){return String(s==null?'':s);}
function toast(){}
function errText(d){return String((d&&d.err)||'');}
function val(){return '';}
function isPhotoPath(p){return /\.(png|jpe?g|webp|gif|bmp|avif|tiff?)$/i.test(String(p||''));}
let RENDER=0;   // счётчик перерисовок карточки: им проверяем мгновенный рендер до сети
function renderInsHost(){RENDER++;}
function syncClipLists(){}
function saveState(){}
function ipvMarks(){}
function ipvRefresh(){}
function uiLog(){}
let ILLCFG={auto:true};
function illCfg(){return ILLCFG;}
let curIns=0;
let CLIPS=[{xml:'a.xml',inserts:[]}];
let SPEAKERS={};
// fetch-заглушка: пишем вызовы, ответ отдаём по маршруту (тела запросов — как в бою)
let CALLS=[];
let STOCK=[];
let LIB=[];
function fetch(url,opts){
  const body=JSON.parse(opts.body||'{}');
  CALLS.push({url:url,body:body,render:RENDER});   // render — сколько перерисовок было ДО запроса
  if(url==='/api/stock_search')return Promise.resolve({json:()=>Promise.resolve({results:STOCK})});
  if(url==='/api/insertlib_match')
    return Promise.resolve({json:()=>Promise.resolve({results:body.queries.map(()=>LIB)})});
  return Promise.resolve({json:()=>Promise.resolve({})});
}
const calls=(url)=>CALLS.filter(c=>c.url===url);
"""


def _run(scenario, source=None):
    """Стенд + тела боевых функций + сценарий; сценарий печатает JSON последней строкой."""
    src = source if source is not None else _read(INSERTS_JS)
    code = STAND + "\n".join(_func(src, n) for n in FUNCS) + "\n" + scenario
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:600]
    return json.loads(p.stdout)


@node
def test_open_stock_panel_researches_by_the_new_type():
    """1. Фото с открытыми вариантами стока → видео: запрос со `type:'video'`, старые кадры сброшены.

    Панель осталась открытой (это переискивание, а не закрытие) — иначе владелец жал бы
    «Сток» второй раз руками.
    """
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'photo',start_sec:1,duration_sec:2,query:'sunset',
      media:'',stockOpts:[{thumb:'old'}],stockShown:true}]}];
    ILLCFG={auto:false};                                // автоподбор не подмешиваем
    STOCK=[{thumb:'new1'},{thumb:'new2'}];
    (async()=>{
      const x=CLIPS[0].inserts[0], before=x.stockOpts;
      await insToggleType(0);
      const sc=calls('/api/stock_search');
      console.log(JSON.stringify({type:x.type, n:sc.length,
        bodies:sc.map(c=>c.body), keptOld:x.stockOpts===before,
        opts:(x.stockOpts||[]).map(o=>o.thumb), shown:x.stockShown,
        libOpts:x.libOpts, libCalls:calls('/api/insertlib_match').length}));
    })();
    """)
    assert out["type"] == "video", out
    assert out["n"] == 1, "сток не переискали при смене типа: %r" % out["bodies"]
    assert out["bodies"][0]["type"] == "video", \
        "сток ищется по прежнему типу: %r" % out["bodies"][0]
    assert out["bodies"][0]["query"] == "sunset", "запрос карточки в сток не уехал"
    assert out["keptOld"] is False and out["opts"] == ["new1", "new2"], \
        "старые варианты стока остались на карточке: %r" % out
    assert out["shown"] is True, "панель стока закрылась вместо переискивания"
    assert out["libOpts"] is None and out["libCalls"] == 0, \
        "база дёрнута там, где автоподбор выключен: %r" % out


@node
def test_open_library_panel_researches_by_the_new_type():
    """2. Открытая база → видео: `/api/insertlib_match` со `type:'video'`, варианты новые.

    `k=8` — ручная выдача показывает больше, чем автоподбор (у того пять).
    """
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'photo',start_sec:1,duration_sec:2,query:'sunset',
      media:'',libOpts:[{path:'C:/lib/old.png',name:'old',score:0.5}],libShown:true}]}];
    ILLCFG={auto:false};
    LIB=[{path:'C:/lib/new.mp4',name:'new',score:0.9,type:'video'}];
    (async()=>{
      const x=CLIPS[0].inserts[0], before=x.libOpts;
      await insToggleType(0);
      const m=calls('/api/insertlib_match');
      console.log(JSON.stringify({type:x.type, n:m.length, q:m.length?m[0].body.queries[0]:null,
        k:m.length?m[0].body.k:0, keptOld:x.libOpts===before,
        paths:(x.libOpts||[]).map(o=>o.path), shown:x.libShown,
        stockOpts:x.stockOpts, stockCalls:calls('/api/stock_search').length}));
    })();
    """)
    assert out["type"] == "video", out
    assert out["n"] == 1, "базу не переискали при смене типа"
    assert out["q"] == {"q": "sunset", "type": "video"}, \
        "запрос в базу ушёл без нового типа: %r" % out["q"]
    assert out["k"] == 8, "ручная выдача потеряла свой k: %r" % out["k"]
    assert out["keptOld"] is False and out["paths"] == ["C:/lib/new.mp4"], \
        "старые варианты базы остались на карточке: %r" % out
    assert out["shown"] is True, "панель базы закрылась вместо переискивания"
    assert out.get("stockOpts") is None and out["stockCalls"] == 0, out


@node
def test_auto_picked_photo_is_dropped_and_a_video_is_picked():
    """3а. Автоподобранное фото → видео: файл убран и сразу подобран видеофайл из базы.

    Файл был поставлен автоподбором под прежний тип — под новый он не годится (в AE тип
    вставки решает расширение файла, `insSetMedia`). Автоподбор пустой карточки идёт тем
    же путём, что при правке запроса (`insQuery`): с типом и с учётом `illCfg().auto`.
    """
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'photo',start_sec:1,duration_sec:2,query:'sunset',
      media:'C:/lib/auto.png',libAuto:true}]}];
    ILLCFG={auto:true};
    LIB=[{path:'C:/lib/auto.mp4',name:'auto',score:0.9,auto:true,type:'video'}];
    (async()=>{
      const x=CLIPS[0].inserts[0];
      await insToggleType(0);
      const m=calls('/api/insertlib_match');
      console.log(JSON.stringify({type:x.type, n:m.length, q:m.length?m[0].body.queries[0]:null,
        media:x.media, libAuto:x.libAuto, libOpts:(x.libOpts||[]).map(o=>o.path)}));
    })();
    """)
    assert out["type"] == "video", out
    assert out["n"] == 1 and out["q"] == {"q": "sunset", "type": "video"}, \
        "автоподбор не переискал базу под новый тип: %r" % out
    assert out["media"] == "C:/lib/auto.mp4", \
        "автофайл прежнего типа остался/не заменён видео: %r" % out["media"]
    assert out["libAuto"] is True, "заменённый файл потерял признак автоподбора"
    assert out["libOpts"] == ["C:/lib/auto.mp4"], out


@node
def test_hand_picked_and_generated_files_survive_the_toggle():
    """3б. Выбранный руками и сгенерённый файл — не трогаем: за них юзер заплатил/выбрал сам."""
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[
      {type:'photo',start_sec:1,duration_sec:2,query:'sunset',media:'C:/hand/pic.png',libAuto:false},
      {type:'photo',start_sec:5,duration_sec:2,query:'city',media:'C:/gen/img.png',genAuto:true,libAuto:false}]}];
    ILLCFG={auto:true};
    LIB=[{path:'C:/lib/other.mp4',name:'other',score:0.9,auto:true,type:'video'}];
    (async()=>{
      const [a,b]=CLIPS[0].inserts;
      await insToggleType(0);
      await insToggleType(1);
      console.log(JSON.stringify({a:{type:a.type,media:a.media,libAuto:a.libAuto},
        b:{type:b.type,media:b.media,libAuto:b.libAuto,genAuto:b.genAuto},
        matchCalls:calls('/api/insertlib_match').length}));
    })();
    """)
    assert out["a"] == {"type": "video", "media": "C:/hand/pic.png", "libAuto": False}, \
        "ручной файл тронули при смене типа: %r" % out["a"]
    assert out["b"] == {"type": "video", "media": "C:/gen/img.png", "libAuto": False,
                        "genAuto": True}, "сгенерённый файл тронули при смене типа: %r" % out["b"]
    assert out["matchCalls"] == 0, \
        "база дёрнута для карточек с файлом — автоподбор обязан молчать: %r" % out


@node
def test_lib_for_sends_the_insert_type():
    """4. Кнопка 📚 сама по себе шлёт тип вставки — как автоподбор (`insWant(x)`)."""
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'video',start_sec:1,duration_sec:3,query:'sea',media:''}]}];
    LIB=[{path:'C:/lib/sea.mp4',name:'sea',score:0.9,type:'video'}];
    (async()=>{
      await insLibFor(0);
      const m=calls('/api/insertlib_match');
      console.log(JSON.stringify({n:m.length, q:m.length?m[0].body.queries[0]:null,
        paths:(CLIPS[0].inserts[0].libOpts||[]).map(o=>o.path), shown:CLIPS[0].inserts[0].libShown}));
    })();
    """)
    assert out["n"] == 1 and out["q"] == {"q": "sea", "type": "video"}, \
        "📚 не передала тип вставки в запрос: %r" % out["q"]
    assert out["paths"] == ["C:/lib/sea.mp4"] and out["shown"] is True, out


@node
def test_type_and_card_change_before_the_first_network_request():
    """5. Тип и карточка меняются сразу, а не после ответа сети: рендер вызван до fetch.

    Клик по бейджу не должен «висеть», пока идут переискивание стока и базы. Проверяем
    синхронный срез сразу после вызова (fetch ещё не разрешился) и счётчик перерисовок,
    записанный заглушкой fetch в момент запроса.
    """
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'photo',start_sec:1,duration_sec:2,query:'sunset',
      media:'',stockOpts:[{thumb:'old'}],stockShown:true}]}];
    ILLCFG={auto:false};
    STOCK=[{thumb:'new1'}];
    (async()=>{
      const x=CLIPS[0].inserts[0];
      const p=insToggleType(0);                      // НЕ ждём: снимок до разрешения fetch
      const snap={type:x.type, renders:RENDER, busy:!!x._typeBusy,
        fetchRenders:calls('/api/stock_search').map(c=>c.render)};
      await p;
      console.log(JSON.stringify({snap:snap, finalRenders:RENDER,
        finalBusy:!!x._typeBusy, oldGone:x.stockOpts!==null&&x.stockOpts[0].thumb==='new1'}));
    })();
    """)
    assert out["snap"]["type"] == "video", \
        "тип не сменился синхронно, до сети: %r" % out["snap"]
    assert out["snap"]["renders"] >= 1, \
        "карточка перерисована только после ответа сети (раннего renderInsHost нет): %r" % out
    assert out["snap"]["fetchRenders"] == [out["snap"]["renders"]], \
        "рендер карточки не предшествовал сетевому запросу: %r" % out
    assert out["finalRenders"] >= 2, \
        "карточку не перерисовали ещё раз после переискивания: %r" % out["finalRenders"]
    assert out["finalBusy"] is False, \
        "флаг занятости не снят — бейдж залип бы навсегда: %r" % out


@node
def test_double_click_toggles_the_type_once():
    """6. Двойной клик, пока идёт переискивание, — тип меняется ОДИН раз.

    Без гвардии второй клик переключал бы тип назад и слал второй запрос в сток.
    """
    out = _run(r"""
    CLIPS=[{xml:'a.xml',inserts:[{type:'photo',start_sec:1,duration_sec:2,query:'sunset',
      media:'',stockOpts:[{thumb:'old'}],stockShown:true}]}];
    ILLCFG={auto:false};
    STOCK=[{thumb:'new1'}];
    (async()=>{
      const x=CLIPS[0].inserts[0];
      const p1=insToggleType(0);
      const p2=insToggleType(0);                     // второй клик по бейджу, пока идёт первый
      const busy=x._typeBusy;
      await Promise.all([p1,p2]);
      console.log(JSON.stringify({type:x.type, busy:busy, stockCalls:calls('/api/stock_search').length,
        finalBusy:!!x._typeBusy}));
    })();
    """)
    assert out["busy"] is True, "флаг занятости не выставлен синхронно — гвардия не работает"
    assert out["type"] == "video", \
        "двойной клик вернул прежний тип: переключилось %r раз" % ("два" if out["type"] == "photo" else "?")
    assert out["finalBusy"] is False, "флаг занятости залип после переискивания"
    assert out["stockCalls"] == 1, \
        "двойной клик ушёл в сток дважды: %r" % out["stockCalls"]


def test_type_busy_flag_never_reaches_the_saved_state():
    """Флаг занятости — живой: `stateObj` вычищает его из вставок, как `genBusy`.

    Иначе залипший `_typeBusy` (F5 посреди переискивания) пережил бы перезагрузку и
    гвардия двойного клика навсегда глотала бы нажатия по бейджу.
    """
    boot = _read(os.path.join(ROOT, "static", "app", "99-boot.js"))
    assert "function stateObj()" in boot and "function saveState()" in boot, \
        "в 99-boot.js не нашлись stateObj/saveState"
    st = boot[boot.index("function stateObj()"):boot.index("function saveState()")]
    assert "delete xx._typeBusy" in st, \
        "_typeBusy уезжает в сохранённое состояние и переживает F5"
