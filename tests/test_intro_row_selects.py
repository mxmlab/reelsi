# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты новых полей и селекторов строки интро.

Покрывает:
  * resolveIntroFor переносит color/fill/anim/fx/dec в line;
  * во всех ПЯТИ дверях новые поля перечислены рядом с 'accent';
  * golden: строки без новых полей дают тот же .jsx, что и раньше;
  * план сцены (scene_plan) принимает новые поля anim/fx/dec.
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


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word"):
    os.makedirs(str(tmp_path), exist_ok=True)
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _plain_intro():
    return [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
            dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]


@node
def test_resolve_intro_for_carries_color_fill_anim_fx_dec():
    """resolveIntroFor переносит color/fill/anim/fx/dec в line."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n) for n in ("introSortRows", "resolveIntroFor"))
    out = _run_node(
        "%s\nvar WORDS=[{w:'Слово1',start:1,i:0},{w:'Слово2',start:2,i:1},"
        "{w:'Слово3',start:3,i:2},{w:'Слово4',start:4,i:3}];"
        "console.log(JSON.stringify(resolveIntroFor("
        "[{count:1,color:'custom',fill:[0.2,0.4,0.6],anim:'count',fx:'glow',dec:2,accent:true},"
        "{count:1,color:'accent',anim:'up',break:true,from:1},"
        "{count:1,color:'yellow',fx:'glow',break:true,from:2},"
        "{count:1,color:'white',break:true,from:3}],"
        "WORDS)));" % body)

    lines = out["lines"]
    assert len(lines) == 4

    # 1. Custom with all fields
    assert lines[0]["color"] == "custom"
    assert lines[0]["fill"] == [0.2, 0.4, 0.6]
    assert lines[0]["anim"] == "count"
    assert lines[0]["fx"] == "glow"
    assert lines[0]["dec"] == 2
    assert lines[0]["accent"] is True
    assert lines[0]["is_count"] is True

    # 2. Accent color with anim
    assert lines[1]["color"] == "accent"
    assert lines[1]["fill"] is None
    assert lines[1]["anim"] == "up"
    assert lines[1]["fx"] == ""
    assert lines[1]["dec"] == 0
    assert lines[1]["accent"] is False
    assert lines[1]["is_count"] is False

    # 3. Yellow with fx
    assert lines[2]["color"] == "yellow"
    assert lines[2]["anim"] == ""
    assert lines[2]["fx"] == "glow"
    assert lines[2]["dec"] == 0
    assert lines[2]["is_count"] is False

    # 4. Defaults
    assert lines[3]["color"] == "white"
    assert lines[3]["fill"] is None
    assert lines[3]["anim"] == ""
    assert lines[3]["fx"] == ""
    assert lines[3]["dec"] == 0
    assert lines[3]["is_count"] is False


def test_all_doors_list_new_fields_next_to_accent():
    """Во всех дверях к строкам интро новые поля перечислены рядом с accent.

    Дверей было пять: панель слов предпросмотра нарезки (pvwOpen/pvwCommitIntro)
    удалена вместе со своими контейнерами — полей строк интро в ней больше нет.
    Остались три двери AE-панели, и новая строка обязана пройти каждую.
    """
    ae_js = open(os.path.join(ROOT, "static", "app", "90-ae.js"), encoding="utf-8").read()

    # 1. selectAE (90-ae.js:65)
    select_ae = _func(ae_js, "selectAE")
    # 2. aiIntroAllRun (90-ae.js:419)
    ai_intro = _func(ae_js, "aiIntroAllRun")
    # 3. resolveIntroFor (90-ae.js:280)
    resolve_intro = _func(ae_js, "resolveIntroFor")

    doors = [
        ("90-ae.js: selectAE", select_ae),
        ("90-ae.js: aiIntroAllRun", ai_intro),
        ("90-ae.js: resolveIntroFor", resolve_intro),
    ]

    required_fields = ["color", "fill", "anim", "fx", "dec", "is_count", "cnt_words"]
    for door_name, code in doors:
        m = re.search(r"\{[^{}]*accent[^{}]*\}", code)
        assert m, f"В двери {door_name} не найден объект с полем 'accent'"
        block = m.group(0)
        for field in required_fields:
            assert field in block, f"В двери {door_name} отсутствует поле '{field}' рядом с 'accent' в объекте: {block}"


def test_anim_select_does_not_contain_count_option():
    """В выборе анимации строки интро (animSelect) нет опции count (вынесена на чип числа icnt), и нет ручного счётчика знаков."""
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()
    row_html = _func(pvw_js, "introRowHtml")
    assert 'value="count"' not in row_html
    assert "icnt" in row_html
    assert "introToggleCount" in pvw_js
    # Ручной ввод знаков после запятой не отображается в строке
    assert "Знаков после запятой" not in row_html


@node
def test_resolve_intro_for_auto_dec_numbers():
    """resolveIntroFor автоматически считает dec: для целых 0, для 2.5 или 2,5 -> 1, для 3.14 -> 2."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n) for n in ("introSortRows", "resolveIntroFor"))
    out = _run_node(
        "%s\nvar WORDS=[{w:'100',start:1,i:0},{w:'2.5',start:2,i:1},"
        "{w:'2,5',start:3,i:2},{w:'3.14',start:4,i:3}];"
        "console.log(JSON.stringify(resolveIntroFor("
        "[{count:1,anim:'count',dec:99},"
        "{count:1,anim:'count',break:true,from:1},"
        "{count:1,anim:'count',break:true,from:2},"
        "{count:1,anim:'count',break:true,from:3}],"
        "WORDS)));" % body)

    lines = out["lines"]
    assert lines[0]["dec"] == 0
    assert lines[1]["dec"] == 1
    assert lines[2]["dec"] == 1
    assert lines[3]["dec"] == 2


def test_golden_intro_without_new_fields_gives_same_jsx(xml_subs, tmp_path):
    """golden: строки без новых полей дают тот же .jsx, что и раньше."""
    jsx = _build(xml_subs, tmp_path / "1", _plain_intro())
    # В полученных группах нет следов anim, fx, dec
    for g in _groups(jsx):
        for ln in g:
            assert "anim" not in ln
            assert "fx" not in ln
            assert "dec" not in ln
            assert ln["color"] == "white"


def test_scene_plan_carries_anim_fx_dec(xml_subs):
    """Новые поля anim, fx, dec доезжают до плана сцены."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="custom", fill=[0.1, 0.2, 0.3], anim="count", fx="glow", dec=2, times=[T_CAM1]),
        dict(words=["СДО*НУТЬ"], color="accent", anim="up", times=[T_CAM2]),
    ]
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro, intro_splits=[1])
    lines1 = plan["intro"][0]["lines"]
    assert lines1[0]["color"] == "custom"
    assert lines1[0]["fill"] == [0.1, 0.2, 0.3]
    assert lines1[0]["anim"] == "count"
    assert lines1[0]["fx"] == "glow"
    assert lines1[0]["dec"] == 2

    lines2 = plan["intro"][1]["lines"]
    assert lines2[0]["color"] == "accent"
    assert lines2[0]["anim"] == "up"
    assert "fx" not in lines2[0]
