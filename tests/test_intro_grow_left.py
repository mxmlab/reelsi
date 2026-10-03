# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кнопка «добавить слово слева» (introGrowLeft): строка забирает предыдущее слово.

ИИ отрывает начало смысловой части: ставит «ПОМОГАЕТ ВАМ» вместо «НЕ ПОМОГАЕТ ВАМ»,
а дописать слово в начало строки было нельзя — приходилось удалять блок целиком.
`introGrowLeft(rows, i)` — одна общая функция для панели строк интро, работает по данным:

  * акцент (`from`): `from` − 1, `count` + 1; слово, уже занятое другой строкой, у неё
    отнимается — иначе одно слово показали бы две строки;
  * обычная строка: предыдущая `count` − 1, эта `count` + 1; опустевшая предыдущая
    удаляется, а её break/from переезжает на нашу (группа не склеивается с соседней);
  * строка с первого слова интро (слово 0) — брать слева нечего, функция возвращает false.

Разметка (иконка `arrow_left_plus` слева от слов) и обвязка панели (`aewGrowLeft` ->
`introReorder()` + `aewSync()`) проверяются отдельно — там же и перевод подсказки.
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

from core import app_meta  # noqa: E402

APP = os.path.join(ROOT, "static", "app")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


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


def _grow(words, rows, i):
    """introGrowLeft на данных: строки после правки + раскладка слов, как в introWalk."""
    body = _func(app_meta.app_js_text(), "introGrowLeft")
    return _run_node(
        "%s\n"
        "var W=%s;\n"
        "var rows=%s;\n"
        "var ok=introGrowLeft(rows,%d);\n"
        # раскладка строк — та же, что в introWalk: from задаёт начало, дальше слова подряд
        "function walk(rs){var off=0;return rs.map(function(r){"
        "if(r.from!=null&&r.from>=0)off=r.from;var a=[];"
        "for(var k=0;k<(r.count|0);k++)a.push(W[off++]);return a;});}\n"
        "console.log(JSON.stringify({ok:ok,rows:rows,words:walk(rows)}));"
        % (body, json.dumps(words, ensure_ascii=False),
           json.dumps(rows, ensure_ascii=False), i))


@node
def test_две_строки_слово_забирается_у_предыдущей():
    """«ВЫ НЕ | ПОМОГАЕТ ВАМ» -> «ВЫ | НЕ ПОМОГАЕТ ВАМ»: строка 2 — 3 слова, строка 1 — 1."""
    out = _grow(["вы", "не", "помогает", "вам"],
                [{"count": 2, "color": "white"}, {"count": 2, "color": "white"}], 1)
    assert out["ok"] is True
    assert [r["count"] for r in out["rows"]] == [1, 3]
    assert out["words"] == [["вы"], ["не", "помогает", "вам"]]


@node
def test_акцент_from_сдвигается_влево():
    """Акцент from=10,count=1 -> from=9,count=2; занятое слово отнимается у своей строки."""
    out = _grow(["с%d" % k for k in range(20)],
                [{"count": 1, "from": 10, "color": "white"}], 0)
    assert out["ok"] is True
    assert out["rows"][0]["from"] == 9
    assert out["rows"][0]["count"] == 2

    # слово from-1 занято предыдущей строкой: оно уходит из неё, а не показывается дважды
    out2 = _grow(["с%d" % k for k in range(20)],
                 [{"count": 10, "color": "white"},
                  {"count": 1, "from": 10, "color": "white"}], 1)
    assert out2["rows"][0]["count"] == 9
    assert out2["rows"][1]["from"] == 9
    assert out2["rows"][1]["count"] == 2
    assert out2["words"] == [["с%d" % k for k in range(9)], ["с9", "с10"]]


@node
def test_предыдущая_строка_в_слово_удаляется_и_отдаёт_break():
    """Предыдущая строка опустела — удаляется, а её break переезжает (группа не склеится)."""
    out = _grow(["a", "b", "c", "d", "e"],
                [{"count": 2}, {"count": 1, "break": True}, {"count": 2}], 2)
    assert out["ok"] is True
    assert len(out["rows"]) == 2
    assert out["rows"][1]["count"] == 3
    assert out["rows"][1]["break"] is True, "заголовок группы обязан переехать на нашу строку"
    assert out["words"] == [["a", "b"], ["c", "d", "e"]]


@node
def test_первая_строка_интро_без_изменений():
    """Слово 0 — слева брать нечего: ни строки, ни слова не меняются."""
    out = _grow(["a", "b", "c", "d"], [{"count": 2}, {"count": 2}], 0)
    assert out["ok"] is False
    assert [r["count"] for r in out["rows"]] == [2, 2]
    assert out["words"] == [["a", "b"], ["c", "d"]]


@node
def test_акцент_забирает_занятое_слово_у_предыдущей_строки():
    """Акцент from=2 на «ВЫ НЕ | ПОМОГАЕТ ВАМ»: слово «не» уходит из первой строки."""
    out = _grow(["вы", "не", "помогает", "вам"],
                [{"count": 2}, {"count": 2, "from": 2}], 1)
    assert out["ok"] is True
    assert out["rows"][1]["from"] == 1
    assert out["rows"][1]["count"] == 3
    assert out["rows"][0]["count"] == 1
    assert out["words"] == [["вы"], ["не", "помогает", "вам"]]


def test_кнопка_в_разметке_строки_и_обвязка_панели():
    """Иконка «стрелка влево +» слева от слов, подсказка в data-t, disabled у слова 0."""
    row = _func(open(os.path.join(APP, "60-preview.js"), encoding="utf-8").read(),
                "introRowHtml")
    assert "arrow_left_plus" in row
    assert "canLeft" in row
    assert "cfg.grow" in row
    assert "Добавить слово слева: забрать предыдущее слово в эту строку" in row
    assert "disabled" in row

    assert "arrow_left_plus:" in open(os.path.join(APP, "20-widgets.js"),
                                      encoding="utf-8").read()
    # панель передаёт свою обвязку; правка идёт через пересортировку и aewSync, как прочие
    assert "grow:'aewGrowLeft'" in open(os.path.join(APP, "80-inserts.js"),
                                        encoding="utf-8").read()
    grow = _func(open(os.path.join(APP, "90-ae.js"), encoding="utf-8").read(), "aewGrowLeft")
    assert "introGrowLeft(INTRO,i)" in grow
    assert "introReorder()" in grow
    assert "aewSync()" in grow


def test_подсказка_переведена():
    """Обе строки кнопки лежат в словаре (иначе на английском остался бы русский текст)."""
    en = json.load(open(EN, encoding="utf-8"))
    assert en["Добавить слово слева"].strip()
    assert en["Добавить слово слева: забрать предыдущее слово в эту строку"].strip()
