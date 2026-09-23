# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка сброса стиля абзаца resetParagraphStyle().

Что проверяем:
1. В xml2ae/template.py и xml2ae/build.py число resetCharStyle() равно числу
   resetParagraphStyle() (8 и 8).
2. Каждый resetParagraphStyle() стоит в той же строке сразу после resetCharStyle()
   той же переменной.
3. В собранном .jsx (фикстура timeline_subs.xml.gz, с дисклеймером и интро)
   числа вызовов resetCharStyle() и resetParagraphStyle() равны.
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_paragraph_reset_in_source_files():
    template_path = os.path.join(ROOT, "core", "xml2ae", "template.py")
    build_path = os.path.join(ROOT, "core", "xml2ae", "build.py")

    template_txt = open(template_path, encoding="utf-8").read()
    build_txt = open(build_path, encoding="utf-8").read()

    # Число вызовов в каждом файле и суммарно 10 и 10
    t_char = len(re.findall(r"\bresetCharStyle\(\)", template_txt))
    t_para = len(re.findall(r"\bresetParagraphStyle\(\)", template_txt))
    # +2 — циклы стопки жёлтых в режиме строк (SUBS_LOOP_STACK*)
    assert t_char == 8, f"в template.py ожидалось 8 resetCharStyle, получено {t_char}"
    assert t_para == 8, f"в template.py ожидалось 8 resetParagraphStyle, получено {t_para}"

    b_char = len(re.findall(r"\bresetCharStyle\(\)", build_txt))
    b_para = len(re.findall(r"\bresetParagraphStyle\(\)", build_txt))
    assert b_char == 2, f"в build.py ожидалось 2 resetCharStyle, получено {b_char}"
    assert b_para == 2, f"в build.py ожидалось 2 resetParagraphStyle, получено {b_para}"

    assert (t_char + b_char) == 10
    assert (t_para + b_para) == 10

    # Каждый resetParagraphStyle() стоит сразу после resetCharStyle() той же переменной в той же строке
    pattern = re.compile(r"(\w+)\.resetCharStyle\(\);\s*(\w+)\.resetParagraphStyle\(\);")
    for name, content in [("template.py", template_txt), ("build.py", build_txt)]:
        matches = pattern.findall(content)
        expected_count = 8 if name == "template.py" else 2   # +2 — циклы стопки жёлтых
        assert len(matches) == expected_count, (
            f"в {name} найдено {len(matches)} связок char+para вместо {expected_count}"
        )
        for var1, var2 in matches:
            assert var1 == var2, f"в {name} переменные не совпали: {var1} != {var2}"


def test_paragraph_reset_in_assembled_jsx(xml_subs, tmp_path):
    out_jsx = str(tmp_path / "out.jsx")
    intro = [
        dict(words=["ТЕСТ"], color="yellow", times=[1.0]),
    ]
    style = {
        "caption": "Тестовая подпись",
        "disc_end": True,
    }
    path, _, _ = xml2ae.to_ae_full(
        xml_subs,
        jsx_path=out_jsx,
        intro=intro,
        style=style,
        disclaimer="ТЕКСТ ДИСКЛЕЙМЕРА",
        emit=lambda *a: None,
    )

    jsx_txt = open(path, encoding="utf-8-sig").read()

    n_char = len(re.findall(r"\bresetCharStyle\(\)", jsx_txt))
    n_para = len(re.findall(r"\bresetParagraphStyle\(\)", jsx_txt))

    assert n_char > 0, "в собранном .jsx нет вызовов resetCharStyle"
    assert n_char == n_para, (
        f"в собранном .jsx число resetCharStyle ({n_char}) != resetParagraphStyle ({n_para})"
    )

    # Каждая пара в .jsx вызывает resetParagraphStyle на той же переменной
    pattern = re.compile(r"(\w+)\.resetCharStyle\(\);\s*(\w+)\.resetParagraphStyle\(\);")
    matches = pattern.findall(jsx_txt)
    assert len(matches) == n_char
    for var1, var2 in matches:
        assert var1 == var2
