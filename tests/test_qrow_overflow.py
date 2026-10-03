# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: строки списка роликов в оверлее прогресса не шире самого списка.

Дефект (скриншот владельца, разметка «вставки (ИИ)», 4 ролика параллельно): строки
списка были шире окна — у `#qlist` появлялась горизонтальная прокрутка, начало имён
срезалось («6.xml» вместо полного имени), вторая строка («думает: 560 симв.
размышлений, ответа пока нет · не хочешь ж…») тянулась за край.

Причина: `.qname` — flex-элемент без `min-width:0`, а его автоматический минимум равен
ширине содержимого; вместе с `white-space:nowrap` у `.qdetail` это распирало строку, и
многоточие не срабатывало. Прокрутка `scrollIntoView({block:'nearest'})` ехала вбок
вместе со списком и сдвигала начало имён за левый край.

Сторожим три вещи:

1. `static/app.css` — у `.qrow .qname` есть `min-width:0` и обрезка имени многоточием
   одной строкой, у `#qlist` — `overflow-x:hidden`, у `.qreason` (второй `flex:1` в той
   же строке) — тоже `min-width:0`: без него ошибка распирала строку так же;
2. `static/app/55-progress.js` — форма прогресса кладёт ПОЛНОЕ имя в `title` строки
   (в списке имя обрезано многоточием, целиком видно только в подсказке), а СЫРУЮ строку
   лога в строку ролика не пускает вовсе: у неё есть словарный статус;
3. она же не даёт списку уехать вбок: прокрутка с `inline:'nearest'` и сброс `scrollLeft`.

Мутация: убрать `min-width:0` из правила `.qrow .qname` — падает
`test_qname_and_list_clip_horizontally`.

Запуск: py -3.10 -m pytest tests/test_qrow_overflow.py -q
"""
from __future__ import annotations

import html as htmllib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CSS = os.path.join(ROOT, "static", "app.css")
PROG_JS = os.path.join(ROOT, "static", "app", "55-progress.js")
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _rule(css: str, selector: str) -> str:
    """Тело правила по селектору без пробелов (как в соседних CSS-тестах)."""
    m = re.search(re.escape(selector) + r"\{([^}]+)\}", css)
    assert m, "в app.css нет правила %s" % selector
    return m.group(1).replace(" ", "")


def _extract(src: str, marker: str) -> str:
    """Кусок исходника от marker до парной закрывающей скобки."""
    assert marker in src, "не нашёл в исходнике: %s" % marker
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("не закрылась скобка у %s" % marker)


def _run_node(tmp_path: Any, name: str, script: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON (последняя строка stdout)."""
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, "node: %s" % (proc.stderr or proc.stdout)[:600]
    lines = [x.strip() for x in proc.stdout.strip().splitlines() if x.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


# ---- 1. CSS: строка не шире списка ----------------------------------------

def test_qname_and_list_clip_horizontally() -> None:
    """`.qname` может сжиматься (min-width:0) и режет имя многоточием, `#qlist` не
    прокручивается вбок, `.qreason` сжимается так же.

    Без `min-width:0` flex-элемент не сжимается меньше своего содержимого: строка
    вылезала за `#qlist`, у списка появлялась горизонтальная прокрутка, а имя
    обрезалось слева — по уехавшему краю. Мутация «убрать min-width:0» валит тест.
    """
    css = _read(CSS)

    name = _rule(css, ".qrow .qname")
    for frag in ("flex:1", "min-width:0", "overflow:hidden",
                 "text-overflow:ellipsis", "white-space:nowrap"):
        assert frag in name, "в .qrow .qname нет %s: %s" % (frag, name)
    assert "word-break:break-word" not in name, \
        "имя снова переносится на вторую строку вместо многоточия в конце: %s" % name

    qlist = _rule(css, "#qlist")
    assert "overflow-x:hidden" in qlist, "список снова может уехать вбок: %s" % qlist
    assert "overflow-y:auto" in qlist, "вертикальная прокрутка списка пропала: %s" % qlist

    reason = _rule(css, ".qreason")
    assert "flex:1" in reason and "min-width:0" in reason, \
        "текст ошибки распирает строку так же, как распирал detail: %s" % reason


# ---- 2. queueRender: полное имя в title, сырой лог — не в строку -----------

@node
def test_rows_carry_full_name_and_human_status(tmp_path: Any) -> None:
    """Реальная форма прогресса: у каждой строки `title` = имя и человеческий статус,
    а сырая строка лога в строку ролика не попадает вовсе."""
    raw_detail = ("думает: 560 симв. размышлений, ответа пока нет · "
                  "не хочешь ждать — отмени")
    core = _read(CORE_JS)
    script = """
const fs=require('fs');
let LANG='ru',I18N={};
__T__
__FMTLOG__
__ESC__
let LOGCACHE=[],LOGSINCE=0,CLIPS=[],curAE=-1,curIns=-1,AICFG=null;
let VIDPOLL=false,UIBUSY=false;
function uiLog(){}function toast(){}function goStep(){}function vidCancel(){}function saveState(){}
function refreshLog(){}function uiBusySet(){}function insTarget(){}function defJob(){return {};}
function plur(n,a,b,c){return c;}function errText(e){return String(e);}function insLog(){}
const EL={};
function mkEl(){return {innerHTML:'',textContent:'',className:'',value:'',disabled:false,
  style:{},scrollHeight:120,clientHeight:40,scrollTop:0,scrollLeft:7,scrolled:null,
  classList:{add(){},remove(){},contains(){return false;}},scrollIntoView(o){this.scrolled=o;},focus(){}};}
function $(id){return EL[id]||(EL[id]=mkEl());}
__PROG__
const out={};
queueRender({items:[
  {name:'gravacao_1790083060187.xml',stage:'inserts',detail:DETAIL},
  {name:'06.xml',stage:'done'},
  {name:'Ролик "А" <b>',stage:'error',reason:'нет ключа API'}
]});
out.html=$('qlist').innerHTML;
out.scrollLeft=$('qlist').scrollLeft;
out.scrolled=$('qrow_0').scrolled;
fs.writeSync(1, JSON.stringify(out)+'\\n');
process.exit(0);
"""
    script = (script
              .replace("__T__", _extract(core, "function t(s, vars)"))
              .replace("__FMTLOG__", _extract(core, "function fmtLog(l)"))
              .replace("__ESC__", _extract(core, "function esc(s)"))
              .replace("__PROG__", _read(PROG_JS))
              .replace("DETAIL", json.dumps(raw_detail, ensure_ascii=False)))
    res = _run_node(tmp_path, "qrow_title.js", script)

    titles: dict[int, str] = {}
    for idx, raw in re.findall(r'<div class="qrow[^"]*" id="qrow_(\d+)"(?: title="([^"]*)")?',
                               res["html"]):
        titles[int(idx)] = htmllib.unescape(raw)
    assert sorted(titles) == [0, 1, 2], "строк в списке не три: %r" % res["html"]

    # 1. Полное имя ролика и человеческий статус — в подсказке строки
    assert titles[0] == "gravacao_1790083060187.xml — вставки (ИИ)", \
        "в title не полное имя со статусом: %r" % titles[0]
    assert titles[1] == "06.xml — готово", "title строки без статуса: %r" % titles[1]
    # 2. Сырой хвост лога в строку не попал: у него нет словарного статуса
    assert "560 симв" not in htmllib.unescape(res["html"]), \
        "сырая строка лога вернулась в строку ролика: %r" % res["html"]
    assert "думает" not in htmllib.unescape(res["html"]), res["html"]
    # 3. Кавычки и угловые скобки в имени не рвут атрибут (esc их экранирует)
    assert titles[2] == 'Ролик "А" <b> — ошибка: нет ключа API', \
        "title строки с ошибкой: %r" % titles[2]
    assert "&quot;" in res["html"] and 'title="Ролик &quot;' in res["html"], \
        "кавычки имени ушли в атрибут как есть: %r" % res["html"]
    # 4. Ошибка — коротко во второй строке, а не простыней
    assert "ошибка: нет ключа API" in htmllib.unescape(res["html"]), res["html"]

    # 5. Прокрутка только по вертикали: список не уезжает вбок
    assert res["scrollLeft"] == 0, \
        "список остался прокрученным вбок (начало имён срезано): %r" % res["scrollLeft"]
    assert res["scrolled"] == {"block": "nearest", "inline": "nearest"}, \
        "прокрутка к текущей строке снова может уехать вбок: %r" % res["scrolled"]


def test_queue_render_pins_horizontal_scroll() -> None:
    """Тот же сторож без node: в исходнике формы видны `inline:'nearest'`,
    сброс `scrollLeft` и `title` строки — проверка не пропадает там, где нет node."""
    render = _extract(_read(PROG_JS), "function queueRender(")
    assert "scrollIntoView({block:'nearest',inline:'nearest'})" in render, \
        "прокрутка к строке не ограничена вертикалью"
    assert "ql.scrollLeft=0" in render, "список не возвращается из горизонтального сдвига"
    row = _extract(_read(PROG_JS), "function progRowHTML(")
    assert "title=\"" in row and "rowTitle" not in row and "const title=" in row, \
        "полное имя и статус не попадают в title строки"
