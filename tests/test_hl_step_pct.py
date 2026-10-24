# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Ручка стиля «Отступ стопки жёлтых, %» (ключ hl_step_pct).

Шаг стопки жёлтых — один на оба режима: и на «по слову», и на «жёлтые подряд — стопкой»
в режиме строк. Ключ задаёт его в процентах от обычного шага; 100 — прежнее число.
Проверяется: дефолт даёт прежний шаг в плане и в .jsx; 150 растягивает шаг в полтора
раза, и план, и .jsx (HL_STEP) несут одно и то же число; в режиме строк число то же.

Слова фикстуры (tests/fixtures/timeline_subs.xml.gz, 60 fps): 7 «Д», 8 «ОДЛО»,
9 «ИПСУМДОЛО» — серия из трёх жёлтых подряд, 11 «МЕТКОНСЕКТ» — одиночное жёлтое.

Запуск: py -3.10 -m pytest tests/test_hl_step_pct.py -q
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402

SERIES = [7, 8, 9]
SINGLE = 11
ROW_MODE = {"sub_words_per_row": 2}
# Прежняя константа шага стопки (до ручки): шаг = высота кадра × 0.06224
OLD_STEP_K = 0.06224


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=()):
    """План сцены с заданным стилем и жёлтыми."""
    return xml2ae.scene_plan(xml, highlights=list(highlights), style=dict(style or {}),
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=()):
    """Собранный .jsx с заданным стилем и жёлтыми."""
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   highlights=list(highlights), style=dict(style or {}),
                                   disclaimer="", emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _jsx_step(jsx):
    """Число из строки `var HL_STEP = …;` собранного .jsx."""
    m = re.search(r"var HL_STEP = ([0-9.]+);", jsx)
    assert m, "в .jsx нет var HL_STEP"
    return float(m.group(1))


def _stack_rows(plan):
    """Слово фикстуры -> ряд стопки по плану (поле repl — индекс слова в субтитрах)."""
    return {it["repl"]: it["row"] for it in plan["subs"]
            if it.get("repl") in SERIES and it.get("color") == "yellow"}


def test_default_step_is_the_old_number(xml_subs, tmp_path):
    """Дефолт (100): шаг плана — прежнее число, и .jsx несёт то же самое."""
    plan = _plan(xml_subs, highlights=SERIES)
    assert plan["hl_step"] == round(plan["h"] * OLD_STEP_K, 2)
    assert _plan(xml_subs, style={"hl_step_pct": 100}, highlights=SERIES)["hl_step"] \
        == plan["hl_step"]
    jsx = _jsx(xml_subs, tmp_path, "step_default.jsx", highlights=SERIES)
    assert _jsx_step(jsx) == plan["hl_step"]


def test_step_150_stretches_the_stack_by_half(xml_subs, tmp_path):
    """150: шаг в плане и в .jsx — round(h × 0.06224 × 1.5, 2); отступ второго ряда
    стопки от первого вырос в 1.5 раза."""
    base = _plan(xml_subs, highlights=SERIES)
    wide = _plan(xml_subs, style={"hl_step_pct": 150}, highlights=SERIES)
    h = wide["h"]
    assert wide["hl_step"] == round(h * OLD_STEP_K * 1.5, 2)

    rows = _stack_rows(wide)
    assert rows == {7: 0, 8: 1, 9: 2}, rows
    # превью кладёт слово ряда r на posy + r·hl_step: отступ между рядами 0 и 1
    gap_base = (base["posy"] + 1 * base["hl_step"]) - (base["posy"] + 0 * base["hl_step"])
    gap_wide = (wide["posy"] + 1 * wide["hl_step"]) - (wide["posy"] + 0 * wide["hl_step"])
    assert gap_wide == pytest.approx(gap_base * 1.5, abs=0.01)

    jsx = _jsx(xml_subs, tmp_path, "step_150.jsx", style={"hl_step_pct": 150},
               highlights=SERIES)
    assert _jsx_step(jsx) == wide["hl_step"]


@pytest.mark.parametrize("pct", [100, 150])
def test_row_stack_mode_uses_one_number(xml_subs, tmp_path, pct):
    """Режим строк с «жёлтые подряд — стопкой»: план и .jsx несут одно число на обе
    ветки стопки (рядом с строками и отдельно), при любом значении ключа."""
    style = dict(ROW_MODE, hl_row_stack=True, hl_step_pct=pct)
    highlights = SERIES + [SINGLE]
    plan = _plan(xml_subs, style=style, highlights=highlights)
    assert plan["hl_step"] == round(plan["h"] * OLD_STEP_K * pct / 100.0, 2)

    stack = sorted(it["row"] for it in plan["subs"] if it.get("stack"))
    assert stack == [0, 1, 2], stack

    jsx = _jsx(xml_subs, tmp_path, "step_rows_%d.jsx" % pct, style=style,
               highlights=highlights)
    assert _jsx_step(jsx) == plan["hl_step"]
