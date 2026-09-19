# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Удаление выбранных клипов и выбор диапазона через Shift (задание ZS).

Раньше удалить клип можно было только на шаге 1 и только по одному (крестик), а галок
на шаге 1 не было вовсе. Теперь галочка выбора одна на все три шага, клик по ней с Shift
отмечает весь диапазон от прошлой кликнутой галки, а «Удалить выбранные» в шапке каждого
списка открывает то же окно, что крестик, — но на список.

Тесты гоняют БОЕВЫЕ функции из `static/app/40-queue.js` под node с заглушками (как
другие тесты интерфейса): вырезаем функцию по балансу скобок и подставляем ей состояние
и внешние двери. Проверяется:

1. Shift-диапазон: клик по 2-му, затем Shift по 5-му — отмечены 2..5; Shift-снятие
   снимает диапазон, точка отсчёта — последняя кликнутая галка;
2. «Убрать из списка» при отмеченных 1, 3, 4 — в CLIPS остались остальные, порядок
   сохранён, curAE/curEdit/curIns поправлены общим _spliceClip;
3. «Стереть с диска»: dry:true и dry:false зовутся по каждому выбранному, ошибка одного
   клипа не останавливает остальные, итог уходит в toast и uiLog;
4. кнопка «Удалить выбранные» неактивна при пустом выборе (пусто ≠ все, в отличие от
   сборки) и без галочек ничего не открывает.

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


# Заглушки — только внешние двери (сервер, DOM, тосты). Всё, что проверяется, — боевые
# функции из 40-queue.js: тест не должен сторожить свою копию логики.
STUBS = r"""
let TOASTS=[],LOGS=[],OPENED=[],CLOSED=[],SAVES=0,RENDERS={1:0,2:0,3:0};
const ELS={};
function $(id){if(!ELS[id])ELS[id]={id,style:{},textContent:'',innerHTML:'',disabled:false};return ELS[id];}
const DELBTNS=[{disabled:false},{disabled:false},{disabled:false}];
const document={querySelectorAll:sel=>(sel==='[data-delsel]'?DELBTNS:[])};
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


def _run(tmp_path, name, funcs, body, decls=("CLIPS", "SEL_ANCHOR", "DEL_CLIP_IDXS")):
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
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


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
def test_list_only_delete_keeps_order_and_fixes_open_indexes(tmp_path):
    """2. Отмечены 1, 3, 4 (0-based 0, 2, 3): в CLIPS остались остальные в том же порядке,
    а индексы открытых клипов поправлены общим _spliceClip (удалённый → −1, после него — сдвиг).

    Одиночный крестик идёт тем же путём: delClip(1) — это delClips([1]).
    """
    out = _run(tmp_path, "zs_list_only.js",
               ["pickClip", "clipLabel", "delClip", "delClips", "delClipListOnly", "_spliceClip"], r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'},{name:'C',xml:'C:/out/C.xml'},
       {name:'D',xml:'C:/out/D.xml'},{name:'E',xml:'C:/out/E.xml'},{name:'F',xml:'C:/out/F.xml'}];
delClip(1);
const singleTitle=$('delClipTitle').textContent;
const singlePending=DEL_CLIP_IDXS.slice();
const singleOpened=OPENED.slice();
OPENED.length=0;
curAE=2;curEdit=5;curIns=1;SEL_ANCHOR=4;
delClips([0,2,3]);
const title=$('delClipTitle').textContent;
const names=$('delClipName').textContent;
const opened=OPENED.slice();
delClipListOnly();
console.log(JSON.stringify({singleTitle,singlePending,singleOpened,title,names,opened,
  left:CLIPS.map(c=>c.name),curAE,curEdit,curIns,anchor:SEL_ANCHOR,
  closed:CLOSED,renders:RENDERS,saves:SAVES,pending:DEL_CLIP_IDXS.length}));
""")
    assert out["singleTitle"] == "Удалить клип?", (
        "крестик одного клипа больше не показывает прежний заголовок: %r" % out["singleTitle"])
    assert out["singlePending"] == [1], "крестик идёт не через общий список из одного индекса"
    assert out["singleOpened"] == ["mbDelClip"], (
        "крестик одного клипа открывает не то окно: %r" % out["singleOpened"])
    assert out["title"] == "Удалить 3 клипа?", (
        "в заголовке окна нет «N клипов»: %r" % out["title"])
    assert out["names"] == "A, C, D", "в окне не видно, какие клипы удаляются: %r" % out["names"]
    assert out["opened"] == ["mbDelClip"], "«убрать из списка» открывает не то окно"
    assert out["left"] == ["B", "E", "F"], (
        "убрались не отмеченные клипы или поехал порядок: %r" % out["left"])
    assert out["curAE"] == -1, "curAE остался на удалённом клипе"
    assert out["curEdit"] == 2, "curEdit не сдвинулся на число удалённых перед ним: %r" % out["curEdit"]
    assert out["curIns"] == 0, "curIns не сдвинулся на число удалённых перед ним: %r" % out["curIns"]
    assert out["anchor"] == -1, (
        "после удаления точка отсчёта диапазона не сброшена — Shift считал бы от чужого клипа")
    assert out["closed"] == ["mbDelClip"], "окно удаления не закрылось"
    assert out["renders"] == {"1": 1, "2": 1, "3": 1}, (
        "после удаления перерисованы не все три списка: %r" % out["renders"])
    assert out["saves"] == 1, "после удаления состояние не сохранено"
    assert out["pending"] == 0, "список удаляемых не очищен — следующий вызов удалит их снова"


@node
def test_disk_delete_calls_every_clip_and_survives_one_error(tmp_path):
    """3. «Стереть с диска»: dry:true и dry:false зовутся по каждому выбранному клипу,
    ошибка одного (B) не останавливает остальные, итог — в toast и uiLog поимённо.

    Бэкенд удаляет по одному XML (задание ZS: цикл во фронте), поэтому клипов в цикле
    ровно столько, сколько отмечено, а порядок ошибок не рвёт.
    """
    out = _run(tmp_path, "zs_disk.js",
               ["clipLabel", "delClips", "delClipDiskPrepare", "delClipDiskExecute", "_spliceClip"], r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'},{name:'C',xml:'C:/out/C.xml'}];
const CALLS=[];
globalThis.fetch=async (url,opt)=>{const body=JSON.parse(opt.body);CALLS.push({url,dry:body.dry,xml:body.xml});
  if(body.dry){
    if(body.xml.indexOf('B.xml')>=0)return {json:async()=>({error:'нет доступа'})};
    return {json:async()=>({ok:true,cams:['cam1'],files:[{path:body.xml.replace('.xml','.cuts.json'),size:1048576}],bytes:1048576})};
  }
  if(body.xml.indexOf('B.xml')>=0)return {json:async()=>({error:'файл занят'})};
  return {json:async()=>({ok:true,files:[{path:body.xml,size:1048576}],bytes:1048576})};
};
(async()=>{
  delClips([0,1,2]);
  await delClipDiskPrepare();
  const dry=CALLS.filter(c=>c.dry).map(c=>c.xml);
  const info=$('delClipInfo').textContent,cams=$('delClipCams').textContent;
  const list=$('delClipList').innerHTML,confirmDisabled=$('delClipDiskConfirmBtn').disabled;
  const prepLog=LOGS.slice();
  CALLS.length=0;TOASTS.length=0;LOGS.length=0;
  await delClipDiskExecute();
  console.log(JSON.stringify({dry,info,cams,list,confirmDisabled,prepLog,
    del:CALLS.map(c=>({dry:c.dry,xml:c.xml})),left:CLIPS.map(c=>c.name),
    toast:TOASTS,log:LOGS,renders:RENDERS,saves:SAVES}));
})();
""")
    # сухой прогон: по одному запросу на клип, ошибка B не оборвала цикл
    assert out["dry"] == ["C:/out/A.xml", "C:/out/B.xml", "C:/out/C.xml"], (
        "сухой прогон не прошёл по каждому выбранному клипу: %r" % out["dry"])
    assert "Будет удалено файлов: 2 (2.00 МБ)" in out["info"], (
        "сводка не сложила файлы всех клипов: %r" % out["info"])
    assert "не прочитано клипов: 1" in out["info"], (
        "не сказано, что один клип не прочитался: %r" % out["info"])
    assert out["cams"] == "Исходник (НЕ трогаем): cam1", "камеры не показаны: %r" % out["cams"]
    assert "A.cuts.json" in out["list"] and "C.cuts.json" in out["list"], (
        "сводный список файлов не собран: %r" % out["list"])
    assert out["confirmDisabled"] is False, "кнопка удаления не ожила после сухого прогона"
    assert any("B: нет доступа" in m for m in out["prepLog"]), (
        "ошибка сухого прогона ушла в никуда: %r" % out["prepLog"])
    # удаление: по одному запросу на клип, ошибка B не остановила C
    assert out["del"] == [{"dry": False, "xml": "C:/out/A.xml"},
                          {"dry": False, "xml": "C:/out/B.xml"},
                          {"dry": False, "xml": "C:/out/C.xml"}], (
        "удаление не прошло по каждому выбранному клипу: %r" % out["del"])
    assert out["left"] == [], "из списка убраны не все, кого пытались удалить: %r" % out["left"]
    assert out["toast"] and "Удалено файлов: 2 (2.00 МБ)" in out["toast"][0], (
        "итог удаления не показан тостом: %r" % out["toast"])
    assert "ошибок: 1" in out["toast"][0], "в тосте нет числа ошибок: %r" % out["toast"]
    log = " ".join(out["log"])
    assert "убрано файлов 2 (2.00 МБ)" in log, "в журнале нет итога удаления: %r" % out["log"]
    assert "B: файл занят" in log, "в журнале нет ошибки поимённо: %r" % out["log"]
    assert out["renders"] == {"1": 1, "2": 1, "3": 1}, (
        "после удаления с диска перерисованы не все три списка: %r" % out["renders"])
    assert out["saves"] == 1, "после удаления с диска состояние не сохранено"


@node
def test_delete_selected_button_is_dead_with_empty_selection(tmp_path):
    """4. Кнопка неактивна при пустом выборе, а без галочек удаление не открывается вовсе.

    Пусто у всех НЕ значит «все»: это семантика selClips() для сборки, у удаления она
    опасна — снесла бы весь список. С галочкой кнопка живая и открывает окно на список.
    """
    out = _run(tmp_path, "zs_button.js", ["syncDelSel", "delSelClips", "clipLabel", "delClips"], r"""
CLIPS=[{name:'A',xml:'C:/out/A.xml'},{name:'B',xml:'C:/out/B.xml'}];
syncDelSel();
const emptyDisabled=DELBTNS.map(b=>b.disabled);
delSelClips();
const openedOnEmpty=OPENED.length,toastEmpty=TOASTS[0]||'';
CLIPS[1].sel=true;
syncDelSel();
const oneDisabled=DELBTNS.map(b=>b.disabled);
delSelClips();
console.log(JSON.stringify({emptyDisabled,oneDisabled,openedOnEmpty,toastEmpty,
  opened:OPENED,title:$('delClipTitle').textContent,name:$('delClipName').textContent}));
""")
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


def test_step1_has_the_shared_checkbox_and_three_delete_buttons():
    """Галки на шаге 1 и корзины в шапках трёх списков — по разметке и по коду.

    Шаг 1 раньше галок не имел вовсе, а удаление было только крестиком. Теперь выбор общий
    (c.sel), Shift идёт через общий pickClip со сбросом точки отсчёта при удалении, а
    кнопки-корзины стоят в шапке каждого из трёх шагов.
    """
    js = _src()
    html = io.open(HTML, encoding="utf-8").read()

    # шаг 1: та же галка pickbox с общим c.sel и общим тултипом
    r1 = js[js.index("function renderClips1()"):js.index("function renderClips2()")]
    assert 'class="pickbox"' in r1, "на шаге 1 нет галки выбора"
    assert "CLIPS[" in r1 and ".sel=this.checked" in r1, "галка шага 1 пишет не в c.sel"
    assert "pickClip(" in r1 and "SHIFT_HELD" in r1, "галка шага 1 не зовёт общий выбор диапазона"
    assert "Выбор клипов — общий для всех шагов" in r1, "у галки шага 1 нет общего тултипа"

    # шаги 2 и 3 ходят тем же путём
    r2 = js[js.index("function renderClips2()"):js.index("function markupSelCount(")]
    r3 = js[js.index("function renderClips3()"):js.index("function spkSelHTML(")]
    for name, body in (("шаг 2", r2), ("шаг 3", r3)):
        assert "pickClip(" in body and "SHIFT_HELD" in body, (
            "%s: галка не зовёт общий pickClip" % name)

    # точка отсчёта одна и сбрасывается там, где список перерисован из-за удаления
    for fn in ("delClipListOnly", "delClipDiskExecute", "removeSelClips", "clearClipsList"):
        assert "SEL_ANCHOR=-1" in _func(js, fn), (
            "%s: после удаления не сброшена точка отсчёта диапазона" % fn)

    # одиночный крестик — тот же путь, второй копии логики удаления нет
    assert "delClips([i])" in _func(js, "delClip"), (
        "крестик одного клипа идёт своей копией логики, а не общим списком")
    assert js.count("/api/clip_delete") == 2, (
        "удаление с диска обзавелось второй копией (ожидались сухой прогон + удаление)")

    # кнопки-корзины: шапка каждого шага, погашены без выбора, справка и имя на месте
    assert html.count("data-delsel") == 3, "кнопок «Удалить выбранные» не три"
    for i in (1, 2, 3):
        start = html.index('id="delSel%d"' % i)
        seg = html[start:html.index("</button>", start)]
        assert "delSelClips()" in seg, "кнопка шага %d не зовёт delSelClips()" % i
        assert "disabled" in seg, "кнопка шага %d не погашена без выбора" % i
        assert 'data-ic="trash"' in seg, "у кнопки шага %d не иконка из набора ico" % i
        assert "aria-label=" in seg and "data-t=" in seg, (
            "у кнопки шага %d нет имени/справки" % i)
