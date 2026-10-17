# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выдача стоков у вставки не переживает F5: старые списки «только Pexels» не держатся вечно.

Симптом (владелец, октябрь 2026): в подборе стоков вставки показывается только Pexels,
хотя сервер уже отдаёт по кругу все стоки. В сохранённом состоянии у 23 вставок лежала
старая выдача `stockOpts` (183 кадра Pexels, 1 Unsplash), а кнопка стоков ходит на сервер
только при пустом `stockOpts` — значит старый список жил вечно.

Причина: `stateObj` писал вставку целиком, включая `stockOpts` и `stockShown`. Выдача —
временные данные (сервер кеширует её сам на 24 ч), в состояние она не попадает, как
`genBusy` и `_typeBusy`.

Что стерегут тесты:

1. **Сохранённое состояние выдачи не содержит.** У вставки нет `stockOpts` и `stockShown`,
   `libOpts`/`libShown` на месте, мгновенные флаги по-прежнему вычищаются.
2. **В памяти выдача остаётся.** Иначе каждые 2,5 с (`flushSave` → `saveState` → `stateObj`)
   открытый ряд стоков закрывался бы сам, как только что-то сохранилось.
3. **Загрузка чистит уже сохранённое.** `applyState` — общий путь и для серверной, и для
   локальной копии состояния — убирает `stockOpts`/`stockShown`: старые списки у владельца
   уходят при первом F5.

Стенд — node: настоящие `stateObj` и `applyState` из static/app/99-boot.js вырезаются по
имени (копий логики в тесте нет), DOM, сеть и соседние функции — заглушки.

Запуск: py -3.10 -m pytest tests/test_ins_stock_state.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOOT_JS = os.path.join(ROOT, "static", "app", "99-boot.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _fn(src, name):
    """Тело функции name целиком (с async, если есть). Приём как в test_ui_lastclip.py."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике нет функции {name}"
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


# Заглушки — только внешние двери: DOM, тосты, сохранение, стили/камеры/рендер.
# Всё, что проверяется, — боевые функции из static/app/99-boot.js.
STUBS = r"""
let ELS={},LOGS=[],SAVES=0;
function $(id){if(!ELS[id])ELS[id]={checked:false,value:''};return ELS[id];}
function t(s){return String(s);}
function toast(){}
function uiLog(m){LOGS.push(String(m));}
function saveState(){SAVES++;}
function insHistTouch(){}
function val(){return '';}
function rendEngine(){return 'ae';}
function rendEngineSet(){}
function nCams(){return 2;}
function vidStateObj(){return {};}
function clipPathFix(p){return p;}
function buildCamRows(){}
function renderQueue(){}
function renderRenderDirField(){}
function renderCutStagesUI(){}
function cutSummary(){}
let CAMDIRS=[],CAMFILES=[],CAMFROM=[],QUEUE=[],CUT_STAGES={},CUT_THRESHOLDS={},CLIPS=[];
let curAE=-1,LASTCLIP={},CUTRUN=null,CURSTYLE=null,STEP=1,AERENDER='',AEMUSICDIR='',TRASHROOTS=[];
"""

# Одна вставка со всем набором полей: выдача стоков, раскрытый ряд, библиотека, мгновенные флаги.
INSERT = """{type:'photo',start_sec:1,duration_sec:2,query:'sunset',media:'',
  genBusy:true,_typeBusy:true,stockOpts:[{provider:'pexels'}],stockShown:true,
  libOpts:[{id:7}],libShown:true}"""


def _stand():
    boot = _read(BOOT_JS)
    return "\n".join([STUBS, _fn(boot, "stateObj"), _fn(boot, "applyState")])


def _run(tmp_path, name, body):
    script = (_stand() + "\n(async()=>{\n" + body
              + "\n})().catch(e=>{console.error(e&&e.stack||e);process.exitCode=1;});\n")
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:1200]
    return json.loads(p.stdout.strip().splitlines()[-1])


@node
def test_saved_state_has_no_stock_results(tmp_path):
    """stateObj не пишет stockOpts/stockShown; libOpts/libShown и прочие флаги — как раньше."""
    body = "CLIPS=[{name:'A',xml:'A.xml',inserts:[" + INSERT + "]}];\n" + """
const saved=stateObj().CLIPS[0].inserts[0];
console.log(JSON.stringify({
  savedHasStock:'stockOpts' in saved, savedHasShown:'stockShown' in saved,
  savedLib:saved.libOpts, savedLibShown:saved.libShown,
  savedHasBusy:('genBusy' in saved)||('_typeBusy' in saved), logs:LOGS}));
"""
    r = _run(tmp_path, "saved.js", body)
    assert r["savedHasStock"] is False, \
        "stockOpts уехал в сохранённое состояние — старая выдача снова переживёт F5"
    assert r["savedHasShown"] is False, "stockShown уехал в сохранённое состояние"
    assert r["savedLib"] == [{"id": 7}] and r["savedLibShown"] is True, \
        "libOpts/libShown задели — это не относится к багу стоков"
    assert r["savedHasBusy"] is False, "мгновенные флаги genBusy/_typeBusy снова уходят в состояние"
    assert r["logs"] == []


@node
def test_stock_results_stay_in_memory_after_saving(tmp_path):
    """stateObj не трогает живую вставку: открытый ряд стоков не закрывается на каждом flushSave."""
    body = "CLIPS=[{name:'A',xml:'A.xml',inserts:[" + INSERT + "]}];\n" + """
const live=CLIPS[0].inserts[0];
stateObj();
console.log(JSON.stringify({liveStock:live.stockOpts, liveShown:live.stockShown, logs:LOGS}));
"""
    r = _run(tmp_path, "live.js", body)
    assert r["liveStock"] == [{"provider": "pexels"}], \
        "stateObj стёр выдачу у живой вставки — ряд стоков сворачивался бы сам"
    assert r["liveShown"] is True, "stateObj сбросил раскрытый ряд у живой вставки"
    assert r["logs"] == []


@node
def test_saved_stock_results_are_dropped_on_load(tmp_path):
    """applyState чистит старую выдачу из сохранённого состояния; библиотека остаётся."""
    body = """
const s={CLIPS:[{name:'A',xml:'A.xml',inserts:[""" + INSERT + """]}]};
applyState(s);
const x=CLIPS[0].inserts[0];
console.log(JSON.stringify({hasStock:'stockOpts' in x, hasShown:'stockShown' in x,
  libOpts:x.libOpts, libShown:x.libShown, logs:LOGS}));
"""
    r = _run(tmp_path, "load.js", body)
    assert r["hasStock"] is False, \
        "старая выдача стоков пережила загрузку — список «только Pexels» держится вечно"
    assert r["hasShown"] is False, "раскрытый ряд стоков пережил загрузку"
    assert r["libOpts"] == [{"id": 7}] and r["libShown"] is True, \
        "загрузка задела libOpts/libShown — это не относится к багу стоков"
    assert r["logs"] == [], f"состояние загружено с ошибкой: {r['logs']}"
