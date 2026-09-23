# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Цвета и тень текста интро — новые ключи стиля.

Строка интро несла только color=="white"|"yellow" и общую тень прекомпа (dropShadow на
СЛОЕ прекомпа, задаётся build.py константами 68/181/5/44 — не трогаем). Здесь добавлены:
  * hl_fill3            третий цвет строки (color=="accent")
  * color=="custom"     свой цвет строки (поле fill [r,g,b]; без fill — white)
  * intro_fill/intro_hl_fill  свой цвет ОБЫЧНОГО/ВЫДЕЛЕННОГО текста интро (None = как было)
  * intro_shadow(+op/dir/dist/soft)  тень (Drop Shadow) на КАЖДОМ слове/строке интро

Модель теста — reelsi/tests/test_intro_accent.py: golden-проверка, разбор .jsx (INTRO_GROUPS),
проверка плана сцены (scene_plan).

  * golden: без новых ключей .jsx побайтово прежний (плейсхолдеры шаблона пусты);
  * color=="accent" — уезжает hl_fill3;
  * color=="custom" с fill — уезжает именно этот цвет;
  * color=="custom" без fill — рисуется как white;
  * intro_fill/intro_hl_fill заданы — цвета в .jsx именно эти;
  * intro_shadow=True — у слоёв слов интро ADBE Drop Shadow 255/16/6.8/19, при False его нет.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3          # секунды: в кадре Камера 1 / перебивка (см. test_intro_accent.py)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word", splits=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=[1] if splits is None else splits, style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _plain_intro():
    return [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
            dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]


def test_без_новых_ключей_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Стиль без новых ключей — .jsx как был: плейсхолдеры шаблона пусты."""
    jsx_default = _build(xml_subs, tmp_path, _plain_intro(), style={})
    jsx_explicit_defaults = _build(xml_subs, tmp_path, _plain_intro(), style={
        "hl_fill3": [0.6863, 0.1216, 0.1216],
        "intro_fill": None, "intro_hl_fill": None,
        "intro_shadow": False,
        "intro_shadow_op": 116.0, "intro_shadow_dir": 16.0,
        "intro_shadow_dist": 6.8, "intro_shadow_soft": 34.0,
        "back_shadow_op": 131.0, "back_shadow_soft": 38.0,
    })
    assert jsx_explicit_defaults == jsx_default
    for g in _groups(jsx_default):
        for ln in g:
            assert "fill" not in ln
    assert 'dd.fillColor=(col=="yellow"?HL_FILL:[1,1,1]); dd.applyFill=true;' in jsx_default
    assert "function introDoc(tl, txt, col){" in jsx_default
    assert ", HL_FILL3=" not in jsx_default        # объявление константы, не упоминание в комментарии
    assert ", INTRO_FILL=" not in jsx_default
    assert ", INTRO_HL_FILL=" not in jsx_default
    assert "introWordShadow" not in jsx_default
    assert "INTRO_SHADOW_OP" not in jsx_default


def test_цвет_accent_уезжает_hl_fill3(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["ЛИБИДО"], color="accent", times=[T_CAM2])],
                 style={"hl_fill3": [0.5, 0.1, 0.2]})
    g1, g2 = _groups(jsx)
    assert g1[0]["color"] == "white"
    assert g2[0]["color"] == "accent"
    assert "HL_FILL3=[0.5,0.1,0.2]" in jsx
    assert 'col=="accent"?HL_FILL3:' in jsx


def test_цвет_custom_с_fill_уезжает_именно_этот_цвет(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["ЛИБИДО"], color="custom", fill=[0.2, 0.4, 0.9], times=[T_CAM2])],
                 style={})
    g1, g2 = _groups(jsx)
    assert g2[0]["color"] == "custom"
    assert g2[0]["fill"] == [0.2, 0.4, 0.9]
    assert 'col=="custom"?(cf||[1,1,1]):' in jsx
    assert ",ln.fill" in jsx


def test_цвет_custom_без_fill_рисуется_как_white(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["ЛИБИДО"], color="custom", times=[T_CAM2])],
                 style={})
    g1, g2 = _groups(jsx)
    assert g2[0]["color"] == "custom"
    assert "fill" not in g2[0]                          # данные не несут цвета
    # JS-выражение при отсутствии cf у конкретной строки падает на [1,1,1] (белый) через ||
    assert 'col=="custom"?(cf||[1,1,1]):' in jsx


def test_intro_fill_и_intro_hl_fill_меняют_цвета(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path,
                 [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
                  dict(words=["ЛИБИДО"], color="yellow", times=[T_CAM2])],
                 style={"intro_fill": [0.9, 0.9, 0.9], "intro_hl_fill": [1.0, 0.0, 0.0]})
    assert "INTRO_FILL=[0.9,0.9,0.9]" in jsx
    assert "INTRO_HL_FILL=[1,0,0]" in jsx
    assert 'dd.fillColor=(col=="yellow"?INTRO_HL_FILL:INTRO_FILL); dd.applyFill=true;' in jsx


def test_intro_fill_без_yellow_строк_всё_равно_объявляется(xml_subs, tmp_path):
    """intro_fill/intro_hl_fill — свойство СТИЛЯ, не строки: объявляется по значению
    стиля, а не по факту использования конкретного цвета в этой сборке."""
    jsx = _build(xml_subs, tmp_path, _plain_intro(), style={"intro_fill": [0.1, 0.2, 0.3]})
    assert "INTRO_FILL=[0.1,0.2,0.3]" in jsx
    assert ", INTRO_HL_FILL=" not in jsx            # объявление константы, не упоминание в комментарии


def test_intro_shadow_включает_тень_на_каждом_слове(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, _plain_intro(), style={"intro_shadow": True}, mode="word")
    assert "function introWordShadow(L, isBack){" in jsx
    assert "INTRO_SHADOW_OP=116, INTRO_SHADOW_DIR=16, INTRO_SHADOW_DIST=6.8, INTRO_SHADOW_SOFT=34" in jsx
    assert "BACK_SHADOW_OP=131, BACK_SHADOW_SOFT=38" in jsx
    assert "ADBE Drop Shadow" in jsx
    assert 'setP(ds,"ADBE Drop Shadow-0002",isBack?BACK_SHADOW_OP:INTRO_SHADOW_OP)' in jsx
    assert 'setP(ds,"ADBE Drop Shadow-0003",INTRO_SHADOW_DIR)' in jsx
    assert 'setP(ds,"ADBE Drop Shadow-0004",INTRO_SHADOW_DIST)' in jsx
    assert 'setP(ds,"ADBE Drop Shadow-0005",isBack?BACK_SHADOW_SOFT:INTRO_SHADOW_SOFT)' in jsx
    # вызов на КАЖДОМ слове (пословный режим): один introWordShadow(L2, ln.back) на цикл по словам
    assert "introWordShadow(L2, ln.back);" in jsx
    # dropShadow(iL, 68) — тень СЛОЯ прекомпа — остаётся нетронутой, это отдельный механизм
    assert "dropShadow(iL, 68);" in jsx
    # у тени слов цвет не задаётся (дефолт AE — чёрный), у прекомпа — белый [1,1,1]
    m_word = re.search(r"function introWordShadow\([^)]*\)\s*\{([^}]+)\}", jsx)
    assert m_word and "ADBE Drop Shadow-0001" not in m_word.group(1)
    m_precomp = re.search(r"function dropShadow\([^)]*\)\s*\{([^}]+)\}", jsx)
    assert m_precomp and 'setP(ds,"ADBE Drop Shadow-0001",[1,1,1]);' in m_precomp.group(1)


def test_intro_shadow_свои_параметры(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, _plain_intro(),
                 style={"intro_shadow": True, "intro_shadow_op": 200.0, "intro_shadow_dir": 45.0,
                        "intro_shadow_dist": 3.0, "intro_shadow_soft": 10.0,
                        "back_shadow_op": 150.0, "back_shadow_soft": 25.0}, mode="line")
    assert "INTRO_SHADOW_OP=200, INTRO_SHADOW_DIR=45, INTRO_SHADOW_DIST=3, INTRO_SHADOW_SOFT=10" in jsx
    assert "BACK_SHADOW_OP=150, BACK_SHADOW_SOFT=25" in jsx
    # построчный режим: тень вешается на слой строки (Ll), а не на слово
    assert "introWordShadow(Ll, ln.back);" in jsx


def test_intro_shadow_задний_план_получает_back_shadow(xml_subs, tmp_path):
    """Строка заднего плана (back=True) получает флаг back=true в INTRO_GROUPS."""
    intro = [
        dict(words=["СОСЕДКА"], color="white", times=[T_CAM1], back=True),
        dict(words=["ФОН ГЛИТЧ"], color="white", anim="glitch", times=[T_CAM1 + 0.1]),
    ]
    jsx = _build(xml_subs, tmp_path, intro, style={
        "intro_shadow": True,
        "back_font": "Arial",
    }, mode="word", splits=[])
    groups = _groups(jsx)
    assert len(groups) == 1
    # Первая строка — задний план (back=True)
    assert groups[0][0].get("back") is True
    # Вторая строка — сам глитч (back не выставлен)
    assert not groups[0][1].get("back")


def test_intro_shadow_false_тени_нет(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, _plain_intro(), style={"intro_shadow": False})
    assert "introWordShadow" not in jsx
    assert "INTRO_SHADOW_OP" not in jsx


def test_цвет_и_fill_в_плане_сцены(xml_subs):
    """Превью читает те же данные, что уезжают в .jsx: план несёт color/fill строки."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ЛИБИДО"], color="custom", fill=[0.3, 0.6, 0.1], times=[T_CAM2])],
        intro_splits=[1], style={})
    lines = plan["intro"][1]["lines"]
    assert lines[0]["color"] == "custom"
    assert lines[0]["fill"] == [0.3, 0.6, 0.1]


@pytest.mark.parametrize("case_name,intro_item", [
    ("glitch", dict(words=["ГЛИТЧ"], color="white", times=[T_CAM1], anim="glitch")),
    ("back", dict(words=["ЗАДНИЙ ПЛАН"], color="white", times=[T_CAM1], back=True)),
])
def test_intro_word_shadow_цвет_черный_дефолт_для_всех_авто_теней(xml_subs, tmp_path, case_name, intro_item):
    """В сгенерированном .jsx у тени СЛОВ интро НЕТ установки ADBE Drop Shadow-0001
    (дефолт AE — чёрный), а у тени ПРЕКОМПА она есть и равна [1,1,1] (белый).
    Проверяется для обоих случаев автоматической тени: глитч и задний план
    (свечение fx=="glow" автотени больше не даёт).
    """
    intro = [intro_item, dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2])]
    jsx = _build(xml_subs, tmp_path, intro, style={"intro_shadow": False})

    # Функция тени слов добавлена в jsx
    m_word = re.search(r"function introWordShadow\([^)]*\)\s*\{([^}]+)\}", jsx)
    assert m_word is not None, f"introWordShadow не найдена в .jsx для случая {case_name}"
    # У тени СЛОВ интро НЕТ установки ADBE Drop Shadow-0001
    assert "ADBE Drop Shadow-0001" not in m_word.group(1)

    # У тени ПРЕКОМПА (dropShadow) установка ADBE Drop Shadow-0001 есть и равна [1,1,1]
    m_precomp = re.search(r"function dropShadow\([^)]*\)\s*\{([^}]+)\}", jsx)
    assert m_precomp is not None, f"dropShadow не найдена в .jsx для случая {case_name}"
    assert 'setP(ds,"ADBE Drop Shadow-0001",[1,1,1]);' in m_precomp.group(1)
    assert "dropShadow(iL, 68);" in jsx

