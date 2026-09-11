# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Откат dims в temporalEase (шаблон сборки) — задание CE.

AE напечатал одиннадцать раз «Unable to call "setTemporalEaseAtKey" because of
parameter 2. Value array does not have 1 elements.»: зум камеры 1 считал dims из
`sc.value.length` (2 для 2D-нула), а setTemporalEaseAtKey ждёт РОВНО один элемент.
Отказ прятал пустой catch — кривая наезда молча не применялась ни на одном ключе.

Два соседних места шаблона (easePair, bez) откат уже имели, здесь его не было.
Теперь все три пользуются общим `temporalEase`: пробует value.length, при отказе —
один элемент, а если не вышло и так — пишет в лог, а не в пустоту. Тест гоняет
САМ отгружаемый код: функция вырезается из template.py и исполняется node'ом
с мок-свойством, у которого setTemporalEaseAtKey падает при неверной размерности.

Запуск: python -m pytest reelsi/tests -q
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

from core.xml2ae import template as template  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="проверка отката требует node в PATH")


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name} — её переименовали или удалили"
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


def _run_node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace").strip()[:400]
    # node пишет stdout в UTF-8, а text=True декодировал бы локалью (cp1252) — кириллица
    # в логе превращалась бы в мусор, и проверка текста сообщения не работала
    return json.loads(p.stdout.decode("utf-8", "replace"))


def _extract():
    """temporalEase и _LOG из боевого шаблона + мок-обвязка для node."""
    ease = _func(template.AE_FULL, "temporalEase")
    log = _func(template.AE_FULL, "_LOG")
    return ease, log


def test_temporal_ease_falls_back_from_dims_to_one_element():
    """Scale 2D-нула: value.length=2, а свойство принимает только 1 элемент (задание CE).
    Первый заход с dim=2 падает, откат с одним элементом проходит — кривая доезжает."""
    ease, log = _extract()
    script = (
        "var log=[], _log=null, _pending=[];\n"
        "%s\n"                       # _LOG
        "var $={writeln:function(s){log.push('W:'+s);}};\n"
        "function KeyframeEase(spd,inf){ this.spd=spd; this.inf=inf; }\n"
        "var attempts=[], ok=[];\n"
        "var p={numKeys:3, name:'Scale', value:{length:2},\n"
        "  setTemporalEaseAtKey:function(k,ai,ao){\n"
        "    attempts.push([k,ai.length]);\n"
        "    if(ai.length!==1) throw new Error('Value array does not have 1 elements.');\n"
        "    ok.push([k, ai[0].inf, ao[0].inf]); } };\n"
        "%s\n"                       # temporalEase
        "temporalEase(p, [90,35,33.3333], [35,90,33.3333]);\n"
        "console.log(JSON.stringify({ok:ok, attempts:attempts, log:log}));"
    ) % (log, ease)
    r = _run_node(script)
    # каждый ключ получил ровно один KeyframeEase с верным влиянием (вход/выход)
    assert r["ok"] == [[1, 90, 35], [2, 35, 90], [3, 33.3333, 33.3333]], r
    # откат реально сработал: первый заход с dim=2 упал на первом же ключе,
    # второй заход с dim=1 прошёл все ключи
    assert r["attempts"] == [[1, 2], [1, 1], [2, 1], [3, 1]], r
    assert r["log"] == [], r


def test_temporal_ease_spatial_position_uses_one_element_directly():
    """Position пространственна (isSpatial) — AE ждёт РОВНО один элемент, а на 2D-нуле
    value.length=2. Раньше размерность бралась по value.length, первая попытка падала на
    каждом таком свойстве и AE печатал ошибку в лог на КАЖДОМ ключе (35 строк шума в
    одном прогоне, задание EV). Теперь с isSpatial первый заход идёт с одним элементом —
    ошибок нет вовсе, лог пуст."""
    ease, log = _extract()
    script = (
        "var log=[], _log=null, _pending=[];\n"
        "%s\n"
        "var $={writeln:function(s){log.push('W:'+s);}};\n"
        "function KeyframeEase(spd,inf){ this.spd=spd; this.inf=inf; }\n"
        "var attempts=[], ok=[];\n"
        "var p={numKeys:3, name:'Position', isSpatial:true, value:{length:2},\n"
        "  setTemporalEaseAtKey:function(k,ai,ao){\n"
        "    attempts.push([k,ai.length]);\n"
        "    if(ai.length!==1) throw new Error('Value array does not have 1 elements.');\n"
        "    ok.push([k, ai[0].inf, ao[0].inf]); } };\n"
        "%s\n"
        "temporalEase(p, [90,35,33.3333], [35,90,33.3333]);\n"
        "console.log(JSON.stringify({ok:ok, attempts:attempts, log:log}));"
    ) % (log, ease)
    r = _run_node(script)
    # все попытки — ровно один элемент, ни одной с двумя: шум в логе AE исчез
    assert r["attempts"] == [[1, 1], [2, 1], [3, 1]], r
    assert r["ok"] == [[1, 90, 35], [2, 35, 90], [3, 33.3333, 33.3333]], r
    assert r["log"] == [], r


def test_temporal_ease_scalar_applies_same_influence_to_every_key():
    """easePair/bez передают скаляр — он идёт на ВСЕ ключи (1-мерное свойство, откат не нужен)."""
    ease, log = _extract()
    script = (
        "var log=[], _log=null, _pending=[];\n"
        "%s\n"
        "var $={writeln:function(s){log.push('W:'+s);}};\n"
        "function KeyframeEase(spd,inf){ this.spd=spd; this.inf=inf; }\n"
        "var ok=[];\n"
        "var p={numKeys:2, name:'Opacity', value:{length:1},\n"
        "  setTemporalEaseAtKey:function(k,ai,ao){ ok.push([k, ai[0].inf, ao[0].inf]); } };\n"
        "%s\n"
        "temporalEase(p, 90, 35);\n"
        "console.log(JSON.stringify(ok));"
    ) % (log, ease)
    assert _run_node(script) == [[1, 90, 35], [2, 90, 35]]


def test_temporal_ease_logs_when_even_one_element_fails():
    """Пустой catch убран (задание CE): если не вышло и с одним элементом — сообщение в
    лог хвоста (.aelog.txt), а не в пустоту. Раньше так ошибку и не увидели."""
    ease, log = _extract()
    script = (
        "var log=[], _log=null, _pending=[];\n"
        "%s\n"
        "var $={writeln:function(s){log.push('W:'+s);}};\n"
        "function KeyframeEase(spd,inf){ this.spd=spd; this.inf=inf; }\n"
        "var p={numKeys:1, name:'Scale', value:{length:2},\n"
        "  setTemporalEaseAtKey:function(){ throw new Error('boom'); } };\n"
        "%s\n"
        "temporalEase(p, [90], [35]);\n"
        "console.log(JSON.stringify(log));"
    ) % (log, ease)
    r = _run_node(script)
    # строка ушла в лог (в node не хватает File-объекта AE, поэтому с префиксом $.writeln,
    # в реальном прогоне то же самое пишется в .aelog.txt) — главное, что не в пустоту
    assert any("temporalEase на «Scale»" in s and "boom" in s for s in r), r