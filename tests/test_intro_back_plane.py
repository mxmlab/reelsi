# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задний план строк интро (галочка back).

Строка с флагом back=True уходит «на задний план»: получает регистр back_case (дефолт lower),
шрифт back_font (если задан) или обычный шрифт интро (если back_font пуст),
и тень back_shadow_op / back_shadow_soft.

Здесь:
  * golden: ни у кого back — .jsx побайтово прежний;
  * back=true при ПУСТОМ back_font — строка уезжает в .jsx строчными, обычным шрифтом
    интро, с тенью back_shadow_op / back_shadow_soft;
  * back=true при заданном back_font — шрифт подменяется;
  * back=false — строка не меняется, даже если в прекомпе есть глитч;
  * строка с галочкой accent сохраняет accent_font, задний план её не перебивает;
  * back_case: lower / as-is / upper дают разный регистр;
  * поле back проходит через все двери из пункта 5 (текстовая проверка исходников);
  * план сцены несёт тот же шрифт и тот же готовый регистр, что и .jsx.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3


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


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word", intro_splits=None):
    os.makedirs(str(tmp_path), exist_ok=True)
    splits = [1] if intro_splits is None else intro_splits
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"), intro=intro,
                                   intro_splits=splits, style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def test_golden_ни_у_кого_back_jsx_побайтово_прежний(xml_subs, tmp_path):
    """golden: ни у кого back не выставлен — .jsx побайтово прежний, плейсхолдеры пусты."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 0.5]),
    ]
    jsx_with_font = _build(xml_subs, tmp_path / "1", intro,
                           style={"back_font": "Geologica-ExtraLight", "back_case": "lower"},
                           intro_splits=[1])
    jsx_empty = _build(xml_subs, tmp_path / "2", intro,
                       style={"back_font": "", "back_case": "lower"}, intro_splits=[1])
    jsx_default = _build(xml_subs, tmp_path / "3", intro, style={}, intro_splits=[1])
    assert jsx_with_font == jsx_default
    assert jsx_empty == jsx_default
    for g in _groups(jsx_default):
        for ln in g:
            assert "accent_font" not in ln
            assert "back" not in ln


def test_back_true_пустой_back_font_строчными_обычный_шрифт_тень(xml_subs, tmp_path):
    """back=true при ПУСТОМ back_font — строка строчными, обычным шрифтом, с тенью."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 0.5], back=True),
    ]
    jsx = _build(xml_subs, tmp_path, intro,
                 style={"back_font": "", "back_case": "lower", "intro_shadow": True,
                        "back_shadow_op": 150.0, "back_shadow_soft": 25.0},
                 intro_splits=[1])
    g1, g2 = _groups(jsx)
    assert g2[1].get("back") is True
    assert g2[1]["words"] == ["вот", "что"]
    # Обычный шрифт интро (accent_font не подставляется)
    assert "accent_font" not in g2[1]
    # Тень со значениями back_shadow
    assert "BACK_SHADOW_OP=150, BACK_SHADOW_SOFT=25" in jsx
    assert "introWordShadow(L2, ln.back);" in jsx


def test_back_true_заданный_back_font_шрифт_подменяется(xml_subs, tmp_path):
    """back=true при заданном back_font — шрифт подменяется на back_font."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 0.5], back=True),
    ]
    jsx = _build(xml_subs, tmp_path, intro,
                 style={"back_font": "Geologica-ExtraLight", "back_case": "lower"},
                 intro_splits=[1])
    g1, g2 = _groups(jsx)
    # Группа 1 — без back: шрифт и регистр не меняются
    assert "accent_font" not in g1[0]
    assert "back" not in g1[0]
    assert g1[0]["words"] == ["ПЕРВОЕ"]

    # Группа 2 — строка с глитчем: без back, шрифт не меняется
    assert "accent_font" not in g2[0]
    assert "back" not in g2[0]
    assert g2[0]["words"] == ["ТЕСТОСТЕРОН"]
    assert g2[0]["anim"] == "glitch"

    # Группа 2 — строка с back=True: шрифт back_font, регистр lower
    assert g2[1]["back"] is True
    assert g2[1]["accent_font"] == "Geologica-ExtraLight"
    assert g2[1]["words"] == ["вот", "что"]

    # Шаблон передаёт af
    assert "function introDoc(tl, txt, col,af)" in jsx
    assert ",ln.accent_font)" in jsx


def test_back_false_строка_не_меняется_даже_если_в_прекомпе_есть_глитч(xml_subs, tmp_path):
    """back=false — строка не меняется, даже если в прекомпе есть глитч."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 0.5], back=False),
    ]
    jsx = _build(xml_subs, tmp_path, intro,
                 style={"back_font": "Geologica-ExtraLight", "back_case": "lower"},
                 intro_splits=[1])
    g1, g2 = _groups(jsx)
    assert "back" not in g2[1]
    assert "accent_font" not in g2[1]
    assert g2[1]["words"] == ["ВОТ", "ЧТО"]


def test_акцент_сохраняет_приоритет_над_задним_планом(xml_subs, tmp_path):
    """Строка с галочкой accent сохраняет accent_font, задний план её не перебивает."""
    intro = [
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ПУЗО"], color="white", times=[T_CAM2 + 0.5], accent=True, back=True),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 1.0], back=True),
    ]
    jsx = _build(xml_subs, tmp_path, intro,
                 style={"accent_font": "TeddyBear-Regular", "accent_case": "title",
                        "back_font": "Geologica-ExtraLight", "back_case": "lower"},
                 intro_splits=[])
    g = _groups(jsx)[0]
    # 0: glitch — без accent_font, регистр прежний
    assert "accent_font" not in g[0]
    assert "back" not in g[0]
    assert g[0]["words"] == ["ТЕСТОСТЕРОН"]
    # 1: accent — сохранил свой accent_font и свой регистр title (не стал lower, back не выставился)
    assert g[1]["accent_font"] == "TeddyBear-Regular"
    assert g[1]["words"] == ["Пузо"]
    assert "back" not in g[1]
    # 2: обычная строка с back=True — ушла на задний план (back_font + lower)
    assert g[2]["back"] is True
    assert g[2]["accent_font"] == "Geologica-ExtraLight"
    assert g[2]["words"] == ["вот", "что"]


def test_back_case_lower_as_is_upper(xml_subs, tmp_path):
    """back_case: lower / as-is / upper дают разный регистр."""
    cases = [
        ("lower", ["вот", "что"]),
        ("as-is", ["ВОТ", "чТо"]),
        ("upper", ["ВОТ", "ЧТО"]),
    ]
    for case, expected in cases:
        intro = [
            dict(words=["ПУЗО"], color="white", times=[T_CAM1], anim="glitch"),
            dict(words=["ВОТ", "чТо"], color="white", times=[T_CAM1 + 0.5], back=True),
        ]
        jsx = _build(xml_subs, tmp_path / case, intro,
                     style={"back_font": "Geologica-ExtraLight", "back_case": case},
                     intro_splits=[])
        g = _groups(jsx)[0]
        assert g[1]["back"] is True
        assert g[1]["accent_font"] == "Geologica-ExtraLight"
        assert g[1]["words"] == expected


def test_поле_back_проходит_через_все_двери():
    """Поле back проходит через все 6 дверей интерфейса (текстовая проверка исходников)."""
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()
    ae_js = open(os.path.join(ROOT, "static", "app", "90-ae.js"), encoding="utf-8").read()

    pvw_open = _func(pvw_js, "pvwOpen")
    pvw_commit = _func(pvw_js, "pvwCommitIntro")
    select_ae = _func(ae_js, "selectAE")
    capture_ae = _func(ae_js, "captureAE")
    ai_intro = _func(ae_js, "aiIntroAllRun")
    resolve_intro = _func(ae_js, "resolveIntroFor")

    doors = [
        ("60-preview.js: pvwOpen", pvw_open),
        ("60-preview.js: pvwCommitIntro", pvw_commit),
        ("90-ae.js: selectAE", select_ae),
        ("90-ae.js: captureAE", capture_ae),
        ("90-ae.js: aiIntroAllRun", ai_intro),
        ("90-ae.js: resolveIntroFor", resolve_intro),
    ]

    for door_name, code in doors:
        m = re.search(r"\{[^{}]*accent[^{}]*\}", code)
        assert m, f"В двери {door_name} не найден объект с полем 'accent'"
        block = m.group(0)
        assert "back" in block, f"В двери {door_name} отсутствует поле 'back' рядом с 'accent': {block}"


def test_план_сцены_несёт_шрифт_и_готовый_регистр(xml_subs):
    """План сцены несёт тот же шрифт и тот же готовый регистр, что и .jsx."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM2], anim="glitch"),
        dict(words=["ВОТ", "ЧТО"], color="white", times=[T_CAM2 + 0.5], back=True),
    ]
    # 1. При заданном back_font
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro, intro_splits=[1],
                             style={"back_font": "Geologica-ExtraLight", "back_case": "lower"})
    g1_lines = plan["intro"][0]["lines"]
    g2_lines = plan["intro"][1]["lines"]

    assert "accent_font" not in g1_lines[0]
    assert "back" not in g1_lines[0]
    assert g1_lines[0]["words"] == ["ПЕРВОЕ"]

    assert "accent_font" not in g2_lines[0]
    assert "back" not in g2_lines[0]
    assert g2_lines[0]["words"] == ["ТЕСТОСТЕРОН"]

    assert g2_lines[1]["back"] is True
    assert g2_lines[1]["accent_font"] == "Geologica-ExtraLight"
    assert g2_lines[1]["words"] == ["вот", "что"]

    # 2. При пустом back_font — обычный шрифт интро (accent_font не задан), но регистр lower и back=True
    plan_empty = xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro, intro_splits=[1],
                                   style={"back_font": "", "back_case": "lower"})
    g2_lines_empty = plan_empty["intro"][1]["lines"]
    assert g2_lines_empty[1]["back"] is True
    assert "accent_font" not in g2_lines_empty[1]
    assert g2_lines_empty[1]["words"] == ["вот", "что"]


def test_back_true_авто_тень_без_галочки_intro_shadow(xml_subs, tmp_path):
    """Строка с back==True при выключенной галочке intro_shadow получает Drop Shadow 131/16/6.8/38."""
    intro = [
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM1]),
        dict(words=["вот", "что"], color="white", times=[T_CAM1 + 0.5], back=True),
    ]
    jsx = _build(xml_subs, tmp_path, intro, style={"intro_shadow": False}, intro_splits=[])
    assert "INTRO_SHADOW_OP=116, INTRO_SHADOW_DIR=16, INTRO_SHADOW_DIST=6.8, INTRO_SHADOW_SOFT=34" in jsx
    assert "BACK_SHADOW_OP=131, BACK_SHADOW_SOFT=38" in jsx
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' in jsx


def test_back_step_прижатие_к_главному_слову(xml_subs, tmp_path):
    """back_step: строка заднего плана стоит к соседней главной на 0.65 обычного шага (дефолт), при back_step=1.0 — как раньше."""
    intro = [
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["вот", "что"], color="white", times=[T_CAM1 + 0.5], back=True),
    ]
    # Дефолт back_step = 0.65 (160 * 0.65 = 104 px между строками)
    jsx_def = _build(xml_subs, tmp_path / "def", intro, style={}, intro_splits=[])
    assert "BACK_STEP=0.65" in jsx_def
    assert "LINE_STEP * ((GRP[si].back || GRP[si-1].back) ? BACK_STEP : 1.0)" in jsx_def
    assert "(!GRP[0].back && nL>1) ? (H/2 - (nL-1)*60) : (H/2 - totH/2)" in jsx_def

    # back_step = 1.0 — шаг как раньше
    jsx_10 = _build(xml_subs, tmp_path / "10", intro, style={"back_step": 1.0}, intro_splits=[])
    assert "BACK_STEP=1" in jsx_10


def test_back_scale_кегль_строчного_текста(xml_subs, tmp_path):
    """back_scale: кегль строчного текста уменьшается до BACK_SCALE (дефолт 0.69)."""
    intro = [
        dict(words=["ТЕСТОСТЕРОН"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["вот", "что"], color="white", times=[T_CAM1 + 0.5], back=True),
    ]
    # Дефолт back_scale = 0.69
    jsx_def = _build(xml_subs, tmp_path / "def", intro, style={}, intro_splits=[])
    assert "BACK_SCALE=0.69" in jsx_def
    assert 'function introBackScale(L){ try{ L.property("ADBE Transform Group").property("ADBE Scale").setValue([Math.round(BACK_SCALE*1000)/10, Math.round(BACK_SCALE*1000)/10, 100]); }catch(e){} }' in jsx_def
    assert "if(ln.back) introBackScale(L2);" in jsx_def
    assert "if(ln.back) lineW*=BACK_SCALE;" in jsx_def
    assert "if(ln.back) wpx*=BACK_SCALE;" in jsx_def

    # Кастомный back_scale = 0.5
    jsx_custom = _build(xml_subs, tmp_path / "cust", intro, style={"back_scale": 0.5}, intro_splits=[])
    assert "BACK_SCALE=0.5" in jsx_custom

    # В line mode тоже вызывается introBackScale(Ll)
    jsx_line = _build(xml_subs, tmp_path / "line", intro, style={}, mode="line", intro_splits=[])
    assert "if(ln.back) introBackScale(Ll);" in jsx_line



