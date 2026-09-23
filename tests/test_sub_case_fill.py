# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: регистр и цвет субтитров (sub_case / sub_fill).

Регистр применяется в scene_plan к ГОТОВОМУ тексту — .jsx и превью читают одно и то же.
Дефолты (upper + белый) не меняют .jsx ни на байт; жёлтые (hl_fill) не трогаются.
"""
import gzip
import os
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402
from core.subs import build_sub_rows  # noqa: E402
from core.xml2ae.build import _accent_word  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_accent_word_lower_and_sentence():
    """Общая машинка регистра: lower и title/sentence-ветка."""
    assert _accent_word("ПРИВЕТ", "lower") == "привет"
    assert _accent_word("Привет", "upper") == "ПРИВЕТ"
    assert _accent_word("ПРИВЕТ", "title") == "Привет"
    assert _accent_word("ПРИВЕТ", "sentence") == "Привет"
    assert _accent_word("качественную", "upper") == "КАЧЕСТВЕННУЮ"
    assert _accent_word("", "lower") == ""


def test_default_upper_white_jsx_identical(xml_subs, tmp_path):
    """sub_case=upper и белый цвет — .jsx совпадает с прежним побайтово."""
    jsx_def, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "def.jsx"),
                                      style={}, emit=lambda *a: None)
    c_def = open(jsx_def, encoding="utf-8-sig").read()
    # FILL остаётся белым, как в старой строке шаблона
    assert "FILL = [1,1,1]" in c_def
    # план несёт белый sub_fill
    p_def = xml2ae.scene_plan(xml_subs, style={})
    assert p_def["sub_fill"] == [1.0, 1.0, 1.0]
    # слова уже капсом (из XML), upper() их не меняет
    assert p_def["subs"]
    for s in p_def["subs"]:
        assert s["w"] == s["w"].upper()


def test_lower_case_plan_and_jsx(xml_subs, tmp_path):
    """sub_case=lower: слова строчными и в плане, и в .jsx."""
    p = xml2ae.scene_plan(xml_subs, style={"sub_case": "lower"})
    assert p["subs"]
    for s in p["subs"]:
        assert s["w"] == s["w"].lower()
        assert s["w"] != s["w"].upper() or not any(ch.isalpha() for ch in s["w"])
    jsx, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "lower.jsx"),
                                  style={"sub_case": "lower"}, emit=lambda *a: None)
    c = open(jsx, encoding="utf-8-sig").read()
    assert "FILL = [1,1,1]" in c          # цвет не тронут


def test_sentence_case_first_word_of_replica(xml_subs):
    """sub_case=sentence: первое слово реплики с заглавной, остальные строчные (rows-режим)."""
    per_row = 3
    plan = xml2ae.scene_plan(xml_subs, style={"sub_case": "sentence",
                                              "sub_words_per_row": per_row})
    meta, cams, subs_list, xml_inserts = xml2ae.parse_full(xml_subs)
    cut_bounds = set()
    for cam in cams:
        for cl in cam.get("clips", []):
            cut_bounds.add(int(cl[0]))
            cut_bounds.add(int(cl[1]))
    raw_lines = build_sub_rows(subs_list, per_row=per_row, max_rows=1,
                               cut_bounds=cut_bounds)
    repl_first = set()
    _seen = set()
    for ln in raw_lines:
        r = ln["repl"]
        if r not in _seen and ln["row"] == 0 and ln["words"]:
            _seen.add(r)
            repl_first.add(ln["words"][0]["idx"])
    fps = meta["fps"]

    for ln in raw_lines:
        for x in ln["words"]:
            expected = (_accent_word(x["w"], "title") if x["idx"] in repl_first
                        else _accent_word(x["w"], "lower"))
            # найти слово в плане по его старту (внутри строки words)
            w_start = x["start"] / fps
            match = [w for s in plan["subs"] for w in s["words"]
                     if abs(w["s"] - w_start) < 1e-6]
            assert match, f"слово {x['w']!r} не нашлось в плане"
            assert match[0]["w"] == expected, \
                f"{x['w']!r} -> {match[0]['w']!r}, ожидали {expected!r}"


def test_fill_color_in_jsx_and_plan(xml_subs, tmp_path):
    """sub_fill не белый: у базовых слов fillColor новый, у жёлтых прежний (hl_fill)."""
    blue = [0.0, 0.5, 1.0]
    jsx, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "color.jsx"),
                                  style={"sub_fill": blue}, emit=lambda *a: None)
    c = open(jsx, encoding="utf-8-sig").read()
    assert "FILL = [0,0.5,1]" in c
    p = xml2ae.scene_plan(xml_subs, style={"sub_fill": blue})
    assert p["sub_fill"] == blue
    # жёлтые по-прежнему берут hl_fill (дефолтный жёлтый) — его значение не менялось
    assert "HL_FILL = [1,0.9176,0]" in c
