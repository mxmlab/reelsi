# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тень прекомпа интро: непрозрачность — ПРОЦЕНТЫ, а не сырые 0..255 AE.

Ручки `intro_comp_shadow_op`/`intro_comp_shadow2_op` были подписаны «Прозрачность,
камера N, %», а в них лежало сырое значение AE Drop Shadow Opacity (0..255): человек
ставил «50 %» и получал 20 %, дефолт 68 выглядел как «68 %», будучи 27 %. Теперь ключи
`intro_comp_shadow_opacity`/`intro_comp_shadow2_opacity` — это проценты 0..100,
а в 0..255 их переводит ОДНО место (`core/xml2ae/plan_style.read_style`,
`opacity*255/100`), откуда число берут и .jsx, и превью (plan.intro[].shadow).

Что проверяется:

  * миграция старых значений: 68 -> 26.7, 31 -> 12.2, 255 -> 100 (и ключей `_op`
    больше нет);
  * дефолт .jsx прежний: пустой стиль даёт ровно `dropShadow(iL, 68)` — 26.7 % даёт
    68.085 из 255, и шаблон считает это дефолтом по допуску;
  * 50 % -> 127.5 в .jsx (одно место пересчёта, второй формулы нет);
  * схема: диапазон 0..100 и подпись «Непрозрачность тени, …»;
  * .jsx из СТАРОГО стиля (ключи `_op`) собирается как из нового с процентами.

Фикстура: timeline_subs.xml.gz — первый кат на кадре 443 @60fps (7.3833 с).
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _build as _golden_build  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _intro(times):
    """times — список групп, каждая — список моментов слов (сек)."""
    return [dict(words=["СЛОВО"], color="white", times=list(t)) for t in times]


def _build(xml, tmp_path, style, times=((1.0,),), splits=(1,), name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=_intro(times), intro_splits=splits,
                                   style=style, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style, times=((1.0,),), splits=(1,)):
    return xml2ae.scene_plan(xml, intro=_intro(times), intro_splits=splits,
                             style=style, disclaimer="", emit=lambda *a: None)


def _shadow_field(key):
    fields = []

    def walk(items):
        for it in items:
            if it.get("type") == "field" and (
                    it.get("key") == key or it.get("key2") == key):
                fields.append(it)
            if it.get("items"):
                walk(it["items"])

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    assert fields, f"в схеме нет ручки {key}"
    return fields[0]


# ---- 1. Миграция старых 0..255 -> проценты -------------------------------------

@pytest.mark.parametrize("old,new", [(68.0, 26.7), (31.0, 12.2), (255.0, 100.0), (0.0, 0.0)])
def test_миграция_старых_значений_в_проценты(old, new):
    """op/255*100 с округлением до десятых: 68 -> 26.7, 31 -> 12.2, 255 -> 100."""
    out = styles.migrate_intro_comp_shadow_pct(
        {"intro_comp_shadow_op": old, "intro_comp_shadow2_op": old})

    assert out["intro_comp_shadow_opacity"] == new
    assert out["intro_comp_shadow2_opacity"] == new
    assert "intro_comp_shadow_op" not in out
    assert "intro_comp_shadow2_op" not in out


def test_миграция_не_пересчитывает_дважды():
    """Метка версии держит второй проход: стиль, прочитанный дважды, не поедет."""
    once = styles.migrate_intro_comp_shadow_pct({"intro_comp_shadow_op": 68.0})
    fresh = {"intro_comp_shadow_opacity": 26.7, "intro_comp_shadow_v": once["intro_comp_shadow_v"]}

    twice = styles.migrate_intro_comp_shadow_pct(dict(fresh))

    assert twice["intro_comp_shadow_opacity"] == 26.7
    assert fresh == twice


def test_resolve_переводит_старый_стиль():
    """Дверь сборки (resolve) тоже переводит старый ключ — правка одна, а не в шаблоне."""
    out = styles.resolve({"intro_comp_shadow_op": 31.0})

    assert out["intro_comp_shadow_opacity"] == 12.2
    assert "intro_comp_shadow_op" not in out


def test_новый_ключ_без_метки_не_пересчитывается():
    """Стиль уже в процентах и без метки (собран руками) — не делим на 255.

    Такой стиль приходит из панели и из кода, который передаёт настройки аргументом:
    `resolve()` там не вызывается, и признаком нового смысла служит сам ключ `_opacity`.
    """
    out = styles.migrate_intro_comp_shadow_pct({"intro_comp_shadow_opacity": 50.0})

    assert out["intro_comp_shadow_opacity"] == 50.0


# ---- 2. Одно место пересчёта: проценты -> 0..255 -------------------------------

def test_дефолт_jsx_прежний(xml_subs, tmp_path):
    """Пустой стиль: .jsx несёт ровно прежнюю строку dropShadow(iL, 68) и собирается
    в эталон байт в байт (26.7 % = 68.1 из 255 — дефолт по допуску)."""
    jsx = _build(xml_subs, tmp_path, {}, name="def.jsx")

    assert "dropShadow(iL, 68);" in jsx
    assert "introCompShadow" not in jsx
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert _mask_assets(_golden_build(xml_subs, tmp_path, style={})) == golden


def test_около_дефолта_шаблон_видит_прежний_дефолт(xml_subs, tmp_path):
    """26.7 % — дефолт ручки: 68.1 из 255 вместо ровно 68.0, и допуск обязан признать это
    прежней подстановкой (иначе дефолт уехал бы в introCompShadow и golden покраснел)."""
    jsx = _build(xml_subs, tmp_path, {"intro_comp_shadow_opacity": 26.7}, name="near.jsx")

    assert "dropShadow(iL, 68);" in jsx
    assert "introCompShadow" not in jsx


def test_шаг_ручки_уже_не_дефолт(xml_subs, tmp_path):
    """26.8 % — уже не дефолт (68.3 из 255, разница 0.3 больше допуска): ручка не мёртвая."""
    jsx = _build(xml_subs, tmp_path, {"intro_comp_shadow_opacity": 26.8}, name="near2.jsx")

    assert "dropShadow(iL, 68);" not in jsx
    assert "introCompShadow(iL, INTRO_ON2[gI]);" in jsx


def test_своё_значение_уходит_от_дефолта(xml_subs, tmp_path):
    """28 % — уже не дефолт: ручка не мёртвая, шаблон собирает introCompShadow (golden
    это не задевает — он про стиль по умолчанию)."""
    jsx = _build(xml_subs, tmp_path, {"intro_comp_shadow_opacity": 28.0}, name="own.jsx")

    assert "dropShadow(iL, 68);" not in jsx
    assert "introCompShadow(iL, INTRO_ON2[gI]);" in jsx


def test_проценты_едут_в_jsx_сырыми_255(xml_subs, tmp_path):
    """50 % -> 127.5 в .jsx: пересчёт ровно один (plan_style), в шаблоне процентов нет."""
    jsx = _build(xml_subs, tmp_path, {"intro_comp_shadow_opacity": 50.0}, name="pct50.jsx")

    assert "introCompShadow(iL, INTRO_ON2[gI]);" in jsx
    assert "on2?68.1:127.5" in jsx


def test_план_отдаёт_сырое_число_превью(xml_subs):
    """План несёт то же сырое 0..255: превью делит его на 255 само, своей формулы нет."""
    plan = _plan(xml_subs, {"intro_comp_shadow2_opacity": 100.0}, times=((1.0,), (9.0,)),
                 splits=(1,))

    assert plan["intro"][0]["shadow"] == {"fill": [1, 1, 1], "op": 68.1}
    assert plan["intro"][1]["shadow"] == {"fill": [1, 1, 1], "op": 255.0}


def test_старый_стиль_в_сборке_как_новый(xml_subs, tmp_path):
    """Стиль со старыми ключами `_op` (0..255) собирается как стиль с процентами:
    68 из 255 -> 26.7 %, а 26.7 % дают в .jsx тот же дефолт."""
    old = _build(xml_subs, tmp_path, {"intro_comp_shadow_op": 68.0,
                                      "intro_comp_shadow2_op": 68.0}, name="old.jsx")
    new = _build(xml_subs, tmp_path, {"intro_comp_shadow_opacity": 26.7,
                                      "intro_comp_shadow2_opacity": 26.7}, name="new.jsx")

    assert old == new
    assert "dropShadow(iL, 68);" in new


# ---- 3. Схема: диапазон 0..100 и подпись «Непрозрачность» -----------------------

@pytest.mark.parametrize("key", ["intro_comp_shadow_opacity", "intro_comp_shadow2_opacity"])
def test_схема_проценты_и_подпись(key):
    """Ручка подписана «Непрозрачность», диапазон 0..100, дефолт — проценты из BASE."""
    field = _shadow_field(key)

    assert field["ctl"] == "num"
    assert (field["min"], field["max"]) == (0, 100)
    assert field["step"] == 1
    assert "Непрозрачность" in field["label"]
    assert "%" in field["label"]
    assert 0.0 <= float(styles.BASE[key]) <= 100.0
    assert "100 %" in field["tip"], "тултип обязан сказать, что 100 % — тень полной силы"


def test_схема_старых_ручек_нет():
    """Старые ключи `_op` из схемы убраны: их читает только миграция."""
    keys = set(styles.BASE)
    doc = str(style_schema.LAYERS)

    for old in ("intro_comp_shadow_op", "intro_comp_shadow2_op"):
        assert old not in keys, f"{old} остался в styles.BASE"
        assert f'"{old}"' not in doc, f"{old} остался ручкой схемы"
