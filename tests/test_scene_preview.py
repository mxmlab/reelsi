# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Предпросмотр шага 3 рисует ПЛАН сцены (задание D).

Главное правило серии заданий — у значения один источник: предпросмотр берёт всё из
/api/scene и ничего не досчитывает. Здесь:
  * перевод AE-кривой в CSS cubic-bezier (единый интерполятор keysAt) — на той же паре
    35/90, что записана в комментарии шаблона;
  * окна групп интро: ts/te считает scene_plan по РОВНО той же формуле, что inAt/outEnd
    в AE_FULL (раньше это были две копии, и они уже разошлись);
  * контракт плана: groups с ts/te и субтитры с gend в секундах, как s/e.
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
from core.xml2ae.layout import (INTRO_F_DUR, INTRO_F_OUT, INTRO_HOLD,  # noqa: E402
                           _intro_group_window)

node = pytest.mark.skipif(not shutil.which("node"), reason="перевод кривой требует node в PATH")


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
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


@node
def test_ae_ease_pair_to_css_bezier():
    """Пара влияний 35/90 даёт cubic-bezier(0.35, 0, 0.10, 1) — формула из шага 5 задания D."""
    src = app_meta.app_js_text()
    out = _run_node("%s\nconsole.log(JSON.stringify(aeEase(35,90)));" % _func(src, "aeEase"))
    assert out == pytest.approx([0.35, 0, 0.10, 1], abs=1e-9)


@node
def test_keys_at_holds_on_jump_cut_and_settles():
    """keysAt: джамп-кат (hold) держит значение до следующего ключа; на осевших — ключи."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n) for n in ("bezierY", "bezierT", "aeEase", "keysAt"))
    out = _run_node("%s\nconsole.log(JSON.stringify(["
                    "keysAt([[0,100],[2,140]],null,0.5,true),"
                    "keysAt([[0,100],[2,140]],null,2.5,true),"
                    "keysAt([[0,182],[1,100]],[[33.3333,35],[90,33.3333]],0.999),"
                    "keysAt([[0,[1,2]],[1,[3,4]]],null,0.5)]));" % body)
    # hold: 0.5с — до ключа 2с -> 100; после 2с -> 140
    assert out[:2] == [100, 140]
    # наезд 182->100 по фирменной кривой: у конца почти 100
    assert out[2] < 101
    # векторные значения (Position cam1) интерполируются покомпонентно
    assert out[3][0] > 1 and out[3][1] > 2 and out[3][0] < 3 and out[3][1] < 4


def test_intro_group_window_matches_ae_formula():
    """ts/te — РОВНО inAt/outEnd из AE_FULL: inAt=(gI==0&&gMin<3)?0:gMin;
    outStart=last?(gMax+F_DUR+HOLD):max(gMax, inAt+F_DUR); outEnd=outStart+F_OUT."""
    # серединная группа: выход не раньше конца фейд-ина (это и разошлось у старого JS)
    ts, te = _intro_group_window([5.0, 5.5], 1, 3)
    assert ts == 5.0
    assert te == round(max(5.5, 5.0 + INTRO_F_DUR) + INTRO_F_OUT, 4)
    # первая группа с реальным началом видна с 0
    assert _intro_group_window([1.0, 2.0], 0, 2)[0] == 0.0
    # а начинающаяся позже — со своего первого слова
    assert _intro_group_window([4.0, 5.0], 0, 2)[0] == 4.0
    # последняя держит HOLD после последнего слова
    ts3, te3 = _intro_group_window([10.0], 1, 2)
    assert ts3 == 10.0
    assert te3 == round(10.0 + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT, 4)
    # группа без слов — нулевое окно (gMin/gMax = 0)
    assert _intro_group_window([], 0, 1) == (0.0, round(INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT, 4))


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_scene_plan_carries_ts_te_and_seconds_gend(xml_subs, tmp_path):
    """План сцены: окна групп интро (ts/te) приезжают в plan.intro, а gend субтитров —
    в СЕКУНДАХ, как s/e (иначе предпросмотр сравнивал бы кадры с секундами)."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[1.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3])], intro_splits=[1])
    assert len(plan["intro"]) == 2
    for g in plan["intro"]:
        assert "ts" in g and "te" in g and g["te"] > g["ts"]
    # субтитры: gend >= e (общий конец стопки не раньше конца слова), в секундах
    for w in plan["subs"]:
        assert w["gend"] >= w["e"] - 1e-6, "gend стопки раньше конца слова"
        assert w["gend"] < 1e9
