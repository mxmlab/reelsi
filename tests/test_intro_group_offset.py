# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Смещение ГРУППЫ интро (задание E, шаг 1): gx/gy с головной строки доезжают до .jsx.

Поле новое — живёт в данных (job.introRows), поэтому задание начинается с Python и
шаблона, а не с мыши. Здесь:
  * golden: без смещений .jsx прежний, дефолт 0/0 не меняет ни байта (dx/dy в данные
    не попадают вовсе);
  * со смещением — dx/dy приезжают в головную строку СВОЕЙ группы, позиция прекомпа
    сдвигается, соседняя группа не меняется;
  * plan.intro[] несёт dx/dy каждой группе — их возьмёт предпросмотр в шаге 2 задания.

Перегруппировка: смещение живёт на головной строке. Если группу разрезали или слили,
оно остаётся у той строки, которая осталась головной, — новая группа, начатая строкой
без gx/gy, получает 0/0. Поэтому в данные (intro) gx/gy пишутся ТОЛЬКО на голове.
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
sys.path.insert(0, os.path.dirname(HERE))

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3          # секунды: в кадре Камера 1 / перебивка (см. test_intro_on_cam2)

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
    # node на Windows пишет в пайп UTF-8 (с BOM для кириллицы), а locale-декодирование
    # (cp1251) превращает слова в «?» и роняет json.loads — декодируем явно utf-8-sig
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


def _build(xml, tmp_path, intro):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1], disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    # первый же `];` за INTRO_GROUPS — конец данных (внутренние `]` идут за `,`/`}`)
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def test_default_zero_offset_changes_no_byte(xml_subs, tmp_path):
    """gx/gy = 0/0 (или вовсе без них) — .jsx побайтово прежний: dx/dy не эмитятся."""
    plain = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])])
    zero = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], gx=0, gy=0),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], gx=0, gy=0)])
    assert zero == plain
    for g in _groups(plain):
        for ln in g:
            assert "dx" not in ln and "dy" not in ln
            assert "ds" not in ln


def test_default_scale_is_byte_identical_to_missing_scale(xml_subs, tmp_path):
    plain = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])])
    explicit = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], gs=100),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], gs=100)])
    assert explicit == plain


def test_offset_moves_only_its_group(xml_subs, tmp_path):
    """Смещение уезжает в головную строку только сдвигаемой группы; соседняя не меняется."""
    jsx = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], gx=100, gy=-40)])
    groups = _groups(jsx)
    assert len(groups) == 2
    assert "dx" not in groups[0][0] and "dy" not in groups[0][0]
    assert groups[1][0]["dx"] == 100 and groups[1][0]["dy"] == -40
    # позиция прекомпа считается от gDx/gDy (данные группы); общий сдвиг нула на месте
    assert "var gDx=(GRP[0].dx||0), gDy=(GRP[0].dy||0);" in jsx
    assert 'setValue([gDx,-520.7894+iDy+gDy])' in jsx
    assert 'setValue([W/2+gDx, H/2-520.7894+iDy+gDy])' in jsx


def test_scene_plan_carries_dx_dy(xml_subs):
    """plan.intro[] отдаёт dx/dy каждой группы — их возьмёт предпросмотр (шаг 2 задания E)."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], gx=100, gy=-40, gs=125)],
        intro_splits=[1])
    assert len(plan["intro"]) == 2
    assert [g["dx"] for g in plan["intro"]] == [0, 100]
    assert [g["dy"] for g in plan["intro"]] == [0, -40]
    assert [g["ds"] for g in plan["intro"]] == [100, 125]


def test_scale_moves_only_its_group(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], gs=125)])
    groups = _groups(jsx)
    assert "ds" not in groups[0][0]
    assert groups[1][0]["ds"] == 125
    assert "gDs=(GRP[0].ds||100)" in jsx
    # Масштаб группы применяется К БАЗОВОМУ iSc, до подгонки по ширине и до расчёта
    # опускания под INTRO_SAFE_TOP. Применённый последним (iSc*=gDs/100 перед setValue),
    # он оставлял обе защиты считать неотмасштабированный блок, и группа на 125%
    # уезжала за верх кадра — в AE, при ровном превью. Обратно не возвращать.
    assert "var iSc=96.8*gDs/100;" in jsx
    assert "iSc*=gDs/100" not in jsx, "масштаб группы снова применяется после защит"


@node
def test_frontend_resolve_carries_gx_gy_on_head_rows():
    """resolveIntroFor проносит gx/gy ТОЛЬКО на головной строке группы (то, что уедет
    в /api/scene и в сборку). Строка-продолжение с gx/gy (такого не должно быть, но
    если появится) группу не двигает: поле на середине отбрасывается."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n) for n in ("introSortRows", "resolveIntroFor"))
    out = _run_node(
        "%s\nvar WORDS=[{w:'А',start:1,i:0},{w:'Б',start:2,i:1},{w:'В',start:3,i:2},"
        "{w:'Г',start:4,i:3}];"
        "console.log(JSON.stringify(resolveIntroFor("
        "[{count:1,color:'white',gx:40,gy:-20,gs:125},"
        "{count:1,color:'yellow',break:true,from:1,gx:100,gy:-40,gs:80},"
        "{count:1,color:'white'},{count:1,color:'white',gx:7,gy:9}],WORDS)));" % body)
    assert out["splits"] == [1]
    heads = [ln for ln in out["lines"]]
    assert heads[0]["gx"] == 40 and heads[0]["gy"] == -20
    assert heads[0]["gs"] == 125
    assert heads[1]["gx"] == 100 and heads[1]["gy"] == -40
    assert heads[1]["gs"] == 80
    assert heads[2]["gx"] == 0 and heads[2]["gy"] == 0   # продолжение группы — не голова
    assert heads[2]["gs"] == 100
    assert heads[3]["gx"] == 0 and heads[3]["gy"] == 0   # gx/gy на середине отброшены


# ===== Задание BG — масштаб интро: gs доживает до плана, общий масштаб превью знает =====

def test_scene_plan_carries_intro_scale(xml_subs):
    """Общий масштаб интро доезжает до плана (задание BG): в AE он висит на нуле «интро»
    (родителе прекомпа) и множит смещение ребёнка и его размер, а сдвиг самого нула
    (intro_y/intro_y2) не трогает. plan.intro_scale — в процентах, как в стиле; базовая
    позиция y уже учитывает G: дефолт 100% — ровно прежняя, G=0.6 — intro_y + 0.6*(-INTRO_BASE_Y + idy)."""
    intro = [dict(words=["А"], color="white", times=[1.0]),
             dict(words=["Б"], color="white", times=[1.0]),
             dict(words=["В"], color="white", times=[1.0]),
             dict(words=["Г"], color="white", times=[8.3])]
    p100 = xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro, intro_splits=[3],
                             style=dict(intro_y=500))
    assert p100["intro_scale"] == 100
    p60 = xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro, intro_splits=[3],
                            style=dict(intro_y=500, intro_scale=60))
    assert p60["intro_scale"] == 60
    # idy группы из 3 строк gs=100 = 78.1094 (см. test_q2_idy_совпадает_со_старой_формулой)
    assert p100["intro"][0]["y"] == round(500 - 520.7894 + 78.1094, 2)
    assert p60["intro"][0]["y"] == round(500 + 0.6 * (-520.7894 + 78.1094), 2)


@node
def test_frontend_intro_resolve_carries_gs_on_head_rows():
    """introResolve проносит gs на головных строках (задание BG, 2026-08-14): раньше
    терял — превью после рефетча плана возвращало блок к 100%, а в сборку текущего
    клипа (tojsx/startRender) масштаб группы не уезжал. Теперь это обёртка над
    resolveIntroFor, и числа те же, что у близнеца выше; поля середины отброшены."""
    src = app_meta.app_js_text()
    body = "\n".join(_func(src, n)
                     for n in ("introSortRows", "introReorder", "resolveIntroFor", "introResolve"))
    out = _run_node(
        "%s\nvar INTRO=[{count:1,color:'white',gx:40,gy:-20,gs:125},"
        "{count:1,color:'yellow',break:true,from:1,gx:100,gy:-40,gs:80},"
        "{count:1,color:'white'},{count:1,color:'white',gx:7,gy:9}];"
        "var WORDS=[{w:'А',start:1,i:0},{w:'Б',start:2,i:1},{w:'В',start:3,i:2},"
        "{w:'Г',start:4,i:3}];var INTRO_PICK=-1;"
        "console.log(JSON.stringify(introResolve()));" % body)
    assert out["splits"] == [1]
    assert [ln["gs"] for ln in out["lines"]] == [125, 80, 100, 100]
    # продолжения групп не двигают и не масштабируют — gx/gs середины отброшены
    assert out["lines"][2]["gx"] == 0 and out["lines"][2]["gs"] == 100
    assert out["lines"][3]["gx"] == 0 and out["lines"][3]["gs"] == 100
