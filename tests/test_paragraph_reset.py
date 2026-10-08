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
    # Подстановки шаблона (подпись и хвостовой дисклеймер) уехали из build.py распилом
    # scene_plan: подпись — в plan_decor.py, хвостовой дисклеймер — в plan_ae.py. Двери
    # сборки те же, но текст подстановок живёт там — считаем связки по ОБОИМ файлам,
    # иначе сторож проверял бы пустоту.
    parts = [("plan_decor.py", os.path.join(ROOT, "core", "xml2ae", "plan_decor.py")),
             ("plan_ae.py", os.path.join(ROOT, "core", "xml2ae", "plan_ae.py"))]

    template_txt = open(template_path, encoding="utf-8").read()
    b_char = b_para = 0
    pattern = re.compile(r"(\w+)\.resetCharStyle\(\);\s*(\w+)\.resetParagraphStyle\(\);")

    # Число вызовов в каждом файле и суммарно 10 и 10
    t_char = len(re.findall(r"\bresetCharStyle\(\)", template_txt))
    t_para = len(re.findall(r"\bresetParagraphStyle\(\)", template_txt))
    # +2 — циклы стопки жёлтых в режиме строк (SUBS_LOOP_STACK*)
    assert t_char == 8, f"в template.py ожидалось 8 resetCharStyle, получено {t_char}"
    assert t_para == 8, f"в template.py ожидалось 8 resetParagraphStyle, получено {t_para}"

    total_matches = 0
    for name, path in parts:
        content = open(path, encoding="utf-8").read()
        b_char += len(re.findall(r"\bresetCharStyle\(\)", content))
        b_para += len(re.findall(r"\bresetParagraphStyle\(\)", content))
        matches = pattern.findall(content)
        total_matches += len(matches)
        for var1, var2 in matches:
            assert var1 == var2, f"в {name} переменные не совпали: {var1} != {var2}"
    assert b_char == 2, f"в подстановках ожидалось 2 resetCharStyle, получено {b_char}"
    assert b_para == 2, f"в подстановках ожидалось 2 resetParagraphStyle, получено {b_para}"
    assert total_matches == 2, f"связок char+para в подстановках {total_matches}, ожидалось 2"

    assert (t_char + b_char) == 10
    assert (t_para + b_para) == 10

    # Каждый resetParagraphStyle() стоит сразу после resetCharStyle() той же переменной в той же строке
    for name, content in [("template.py", template_txt)]:
        matches = pattern.findall(content)
        expected_count = 8
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
