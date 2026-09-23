# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Независимые счётчики чисел в одной строке интро (`cnt_words` -> `cnts`).

Раньше счётчик был ОДНИМ флагом на строку (`is_count`): две кнопки `123` в одной
строке загорались вместе, а клик снимал счётчик со ВСЕХ строк интро. Теперь:

  * `cnt_words` — позиции слов со счётчиком внутри строки (`0..count-1`), без него
    (старые задания) счётчик читается как легаси — на первом числе строки;
  * план: `line["cnts"] = [[позиция, цель, выражение, dec], ...]` по возрастанию
    позиции, а скаляры `cnt`/`expr`/`dec`/`cnt_idx` равны ПЕРВОМУ элементу
    (на них стоит строчный режим: один текстовый слой — один счётчик);
  * `.jsx` пословного режима ищет счётчик по `ln.cnts`, поэтому каждое слово-число
    получает свой Slider Control и своё выражение;
  * `introToggleCount(rows, i, p, p0)` переключает позицию `p` в своей строке,
    не трогая остальные строки, и материализует легаси-разметку в список.
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

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

ROUND_EXPR = 'Math.round(effect("Slider Control")("Slider").value)'
COMMA_EXPR = 'effect("Slider Control")("Slider").value.toFixed(1).replace(".", ",")'


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
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
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


def _lines(xml, intro):
    """Первая (и единственная) группа плана сцены — её строки."""
    plan = xml2ae.scene_plan(xml, disclaimer="", intro=intro, intro_splits=None,
                             intro_riser=False, emit=lambda *a: None)
    return plan["intro"][0]["lines"]


def _build(xml, tmp_path, intro, mode="word"):
    os.makedirs(str(tmp_path), exist_ok=True)
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1], style={},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def test_plan_два_счётчика_в_одной_строке(xml_subs):
    """cnt_words=[0,2] на «100 И 2,5»: cnts — два элемента, скаляры — первый."""
    intro = [dict(words=["100", "И", "2,5"], color="white",
                  times=[T_CAM1, T_CAM1 + 0.3, T_CAM1 + 0.6], cnt_words=[0, 2])]
    ln = _lines(xml_subs, intro)[0]

    assert ln["cnts"] == [[0, 100, ROUND_EXPR, 0], [2, 2.5, COMMA_EXPR, 1]]
    assert ln["cnt_idx"] == 0
    assert ln["cnt"] == 100
    assert ln["dec"] == 0
    assert ln["is_count"] is True


def test_plan_счётчик_только_на_втором_числе(xml_subs):
    """cnt_words=[2]: единственный элемент cnts — на позиции 2, cnt_idx тоже 2."""
    intro = [dict(words=["100", "И", "2,5"], color="white",
                  times=[T_CAM1, T_CAM1 + 0.3, T_CAM1 + 0.6], cnt_words=[2])]
    ln = _lines(xml_subs, intro)[0]

    assert ln["cnts"] == [[2, 2.5, COMMA_EXPR, 1]]
    assert ln["cnt_idx"] == 2
    assert ln["cnt"] == 2.5
    assert ln["dec"] == 1


def test_plan_легаси_is_count_без_cnt_words(xml_subs):
    """Старое задание (is_count без cnt_words): cnts из одного первого числа."""
    intro = [dict(words=["100", "СПОСОБОВ"], color="white",
                  times=[T_CAM1, T_CAM1 + 0.3], is_count=True)]
    ln = _lines(xml_subs, intro)[0]

    assert ln["cnts"] == [[0, 100, ROUND_EXPR, 0]]
    assert ln["cnt_idx"] == 0
    assert ln["cnt"] == 100


def test_plan_cnt_words_на_нечисловом_слове_без_счётчика(xml_subs):
    """cnt_words=[1] на нечисловом слове: ни cnts, ни cnt/cnt_idx, is_count не ставится."""
    intro = [dict(words=["100", "СПОСОБОВ"], color="white",
                  times=[T_CAM1, T_CAM1 + 0.3], cnt_words=[1])]
    ln = _lines(xml_subs, intro)[0]

    assert "cnts" not in ln
    assert "cnt" not in ln
    assert "cnt_idx" not in ln
    assert "is_count" not in ln


def test_jsx_пословный_режим_ищет_счётчик_по_cnts(xml_subs, tmp_path):
    """Строка из п.1 уезжает в INTRO_GROUPS с cnts, шаблон ищет счётчик по ln.cnts."""
    intro = [dict(words=["100", "И", "2,5"], color="white",
                  times=[T_CAM1, T_CAM1 + 0.3, T_CAM1 + 0.6], cnt_words=[0, 2]),
             dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]
    jsx = _build(xml_subs, tmp_path / "multi", intro, mode="word")
    g1, _ = _groups(jsx)

    assert g1[0]["cnts"] == [[0, 100, ROUND_EXPR, 0], [2, 2.5, COMMA_EXPR, 1]]
    assert g1[0]["cnt_idx"] == 0
    assert g1[0]["cnt"] == 100

    # Пословный режим: счётчик каждого слова ищется В СПИСКЕ ln.cnts, а не по cnt_idx.
    assert "if(ln.cnts)" in jsx
    assert "if(ln.cnts[ci][0]===wj2)" in jsx
    assert "cw?cw[1]:null, cw?cw[2]:null" in jsx
    assert "ln.cnt_idx===wj2" not in jsx


@node
def test_intro_toggle_count_независимые_позиции():
    """introToggleCount: позиции независимы, чужая строка не сбрасывается, легаси -> [p0,p]."""
    body = _func(app_meta.app_js_text(), "introToggleCount")
    out = _run_node(
        "%s\n"
        "var rows=[{count:3,color:'white',is_count:false,cnt_words:null},"
        "{count:2,color:'white',is_count:true,cnt_words:null},"
        "{count:3,color:'white',is_count:true,cnt_words:null}];\n"
        "introToggleCount(rows,0,0,0);\n"          # первое число строки
        "introToggleCount(rows,0,2,0);\n"          # второе — независимо от первого
        "var both=JSON.parse(JSON.stringify(rows));\n"
        "introToggleCount(rows,0,0,0);\n"          # снять первое — второе остаётся
        "var one=JSON.parse(JSON.stringify(rows));\n"
        "introToggleCount(rows,0,2,0);\n"          # снять последнее — счётчиков нет
        "var none=JSON.parse(JSON.stringify(rows));\n"
        "introToggleCount(rows,2,2,0);\n"          # легаси: клик по второму числу
        "var legacy=JSON.parse(JSON.stringify(rows));\n"
        "console.log(JSON.stringify({both:both,one:one,none:none,legacy:legacy}));" % body)

    # Две позиции включены независимо.
    assert out["both"][0]["cnt_words"] == [0, 2]
    assert out["both"][0]["is_count"] is True
    # Другая строка со своим is_count не сброшена и не получила чужих позиций.
    assert out["both"][1]["is_count"] is True
    assert out["both"][1]["cnt_words"] is None
    # Снятие одной позиции не трогает вторую.
    assert out["one"][0]["cnt_words"] == [2]
    assert out["one"][0]["is_count"] is True
    # Сняли последнюю — cnt_words пуст, is_count снят.
    assert out["none"][0]["cnt_words"] == []
    assert out["none"][0]["is_count"] is False
    # Легаси-строка (is_count без cnt_words): клик по второму числу даёт [p0, p].
    assert out["legacy"][2]["cnt_words"] == [0, 2]
    assert out["legacy"][2]["is_count"] is True
