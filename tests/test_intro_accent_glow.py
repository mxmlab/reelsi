# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свечение строк интро — ЕДИНАЯ галка стиля intro_accent_glow, решает её план.

Решение владельца (02.10.2026): выбор свечения у отдельной строки убран — ни
выпадашки в строке, ни поля строки. Включённая галка: светятся ВСЕ accent-строки и
accent-акценты, снятая — ни одна. Белые и жёлтые строки свечения не получают никогда
(замер владельца: свечение было только на accent, 98 из 109).

Поле `fx` строки (из старых сохранённых клипов) НЕ читается вовсе: `_intro_line_js`
(core/xml2ae/plan_intro.py) ставит fx="glow" только по галке стиля и цвету accent.
Интерфейс (`introRowHtml`) выпадашку эффекта больше не рисует, а разметка ИИ
(`introRowsFromAI`) не ставит fx ни одной строке.
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

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402
from test_intro_row_selects import _func, _run_node  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3
PVW_JS = os.path.join(ROOT, "static", "app", "60-preview.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, intro, style=None):
    return xml2ae.scene_plan(xml, intro=intro, intro_splits=[1], disclaimer="",
                             style=style or {})


def _build(xml, tmp_path, intro, style=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode="word", disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _row(color="accent", fx="__нет__"):
    """Строка разметки: fx="__нет__" — поля fx в строке нет вовсе."""
    x = dict(words=["ПАДАЕТ"], color=color, times=[T_CAM1])
    if fx != "__нет__":
        x["fx"] = fx
    return x


# Свечение accent-строки — из галки стиля: включена, светится при ЛЮБОМ fx строки.

@pytest.mark.parametrize("fx", [None, "", "glow"])
def test_accent_с_галкой_светится_при_любом_fx(xml_subs, fx):
    """Галка вкл: accent-строка получает fx="glow" — и без поля fx, и с пустым, и со старым "glow"."""
    line = _plan(xml_subs, [_row("accent", fx)],
                 {"intro_accent_glow": True})["intro"][0]["lines"][0]
    assert line.get("fx") == "glow", f"accent без свечения при fx={fx!r}"


@pytest.mark.parametrize("fx", [None, "", "glow"])
def test_accent_без_галки_не_светится_при_любом_fx(xml_subs, fx):
    """Галка выкл: ни одна accent-строка не светится — поле fx строки НЕ читается.

    Это и есть сторож мутации: вернуть в плане чтение fx строки — краснеет здесь.
    """
    line = _plan(xml_subs, [_row("accent", fx)],
                 {"intro_accent_glow": False})["intro"][0]["lines"][0]
    assert "fx" not in line, f"свечение вернулось при снятой галке, fx строки={fx!r}"


def test_белая_и_жёлтая_с_fx_glow_не_светятся(xml_subs):
    """Белые и жёлтые строки свечения не получают никогда — даже с fx="glow" и включённой галкой."""
    intro = [_row("white", "glow"), _row("yellow", "glow")]
    plan = xml2ae.scene_plan(xml_subs, intro=intro, intro_splits=[1, 1], disclaimer="",
                             style={"intro_accent_glow": True})
    for grp in plan["intro"]:
        for line in grp["lines"]:
            assert "fx" not in line, line


def test_старый_стиль_без_ключа_идёт_по_дефолту_галки(xml_subs):
    """Стиль без ключа: работает дефолт BASE (True) — accent светится, как при явной галке."""
    from core import styles
    assert styles.BASE["intro_accent_glow"] is True, "дефолт ключа обязан быть True"
    line = _plan(xml_subs, [_row("accent", "")], {})["intro"][0]["lines"][0]
    assert line.get("fx") == "glow"


# ---- .jsx: несёт ровно то, что посчитал план ----

def test_jsx_accent_с_галкой_собирает_свечение(xml_subs, tmp_path):
    """Галка вкл: INTRO_GROUPS несёт fx="glow" и по нему собирается introAnimFX."""
    jsx = _build(xml_subs, tmp_path, [_row("accent", "glow")], {}, name="glow.jsx")
    assert _groups(jsx)[0][0].get("fx") == "glow"
    assert "function introAnimFX" in jsx


def test_jsx_accent_без_галки_свечения_не_собирает(xml_subs, tmp_path):
    """Галка выкл: fx вообще нет — INTRO_GROUPS без поля, ветки свечения в .jsx нет."""
    jsx = _build(xml_subs, tmp_path, [_row("accent", "glow")],
                 {"intro_accent_glow": False}, name="plain.jsx")
    assert "fx" not in _groups(jsx)[0][0]
    assert 'else if(fx=="glow")' not in jsx


# ---- интерфейс: выпадашки эффекта нет, разметка ИИ fx не ставит ----

@node
def test_introRowHtml_без_выпадашки_эффекта():
    """В строке интро нет выпадашки «нет/свечение» и её обработчика записи fx."""
    row_html = _func(open(PVW_JS, encoding="utf-8").read(), "introRowHtml")
    assert "fxSelect" not in row_html, "выпадашка эффекта вернулась в строку интро"
    assert "fxVal" not in row_html
    assert ".fx=this.value" not in row_html, "строка снова пишет своё поле fx"
    assert "t('Эффект строки')" not in row_html
    assert "t('свечение')" not in row_html


@node
def test_introRowsFromAI_не_ставит_fx():
    """Разметка ИИ (WJ): fx не ставится ни одной строке, второго аргумента-стиля нет."""
    js = app_meta.app_js_text()
    body = _func(js, "introRowsFromAI")
    assert "fx" not in body, "introRowsFromAI снова ставит fx строкам"
    assert "intro_accent_glow" not in body, "галка стиля вернулась в разметку ИИ"
    d = ("{intro_rows:[{count:1,color:'accent'},{count:1,color:'white'},{count:1,color:'yellow'}],"
         "mid_groups:[{count:1,color:'accent',from:3}]}")
    out = _run_node("%s\nconsole.log(JSON.stringify(introRowsFromAI(%s)));" % (body, d))
    assert [r["color"] for r in out] == ["accent", "white", "yellow", "accent"]
    assert all("fx" not in r for r in out), out
    assert not re.search(r"introRowsFromAI\(d\s*,", js), (
        "в вызовы разметки интро вернулся второй аргумент со стилем")


def test_правило_живёт_в_плане_и_ключ_есть_в_схеме_панели():
    """Ручка есть в панели стиля с переводом, дефолт True; поле fx строки план не читает."""
    from core import style_schema
    keys = json.dumps(style_schema.LAYERS, ensure_ascii=False)
    assert '"intro_accent_glow"' in keys, "ручки intro_accent_glow нет в схеме панели"

    plan_src = open(os.path.join(ROOT, "core", "xml2ae", "plan_intro.py"),
                    encoding="utf-8").read()
    # Комментарии не считаем: ключ в них называется по делу (объяснение правила).
    plan_code = re.sub(r"#[^\n]*", "", plan_src)
    assert "accent_glow" in plan_code, "план больше не читает галку intro_accent_glow"
    assert 'x.get("fx")' not in plan_code, (
        "план снова читает поле fx строки — выбор свечения вернулся к отдельной строке")
