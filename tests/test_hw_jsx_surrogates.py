# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты экранирования суррогатов и noncharacters в JS-литералах (задание HW, круг 7).

1. _js("a\\udcffb") и _js("\\ufffe\\uffff") экранируются в ASCII \\uXXXX;
2. Запись полученных литералов в файл utf-8-sig не падает с UnicodeEncodeError;
3. node --check на сформированном JS-файле проходит успешно;
4. Кириллица и валидный юникод остаются сырыми (ensure_ascii=False);
5. _js_multiline корректно обрабатывает переносы строк (\\r) и экранирует суррогаты.
"""
import shutil
import subprocess

import pytest

from core.xml2ae import jsutil


def test_js_lone_surrogates_ascii_and_utf8_sig(tmp_path):
    """Одиночные суррогаты (\\ud800..\\udfff) экранируются в \\uXXXX и пишутся в utf-8-sig."""
    raw_s = "a\udcffb"
    lit = jsutil._js(raw_s)

    # Символ суррогата заменён на ASCII escape-последовательность
    assert r"\udcff" in lit
    assert lit == '"a\\udcffb"'
    assert lit.isascii(), f"Литерал содержит не-ASCII символы: {lit!r}"

    # Запись в utf-8-sig не вызывает UnicodeEncodeError
    jsx_file = tmp_path / "test_surrogate.jsx"
    jsx_file.write_text(f"var test_surrogate = {lit};\n", encoding="utf-8-sig")

    # Проверяем граничные суррогаты: \ud800 (верхний старт) и \udfff (нижний конец)
    lit_bounds = jsutil._js("start_\ud800_mid_\udfff_end")
    assert r"\ud800" in lit_bounds
    assert r"\udfff" in lit_bounds
    assert lit_bounds.isascii()
    jsx_bounds = tmp_path / "test_bounds.jsx"
    jsx_bounds.write_text(f"var test_bounds = {lit_bounds};\n", encoding="utf-8-sig")


def test_js_noncharacters_ufffe_uffff_ascii_and_utf8_sig(tmp_path):
    """Символы U+FFFE и U+FFFF экранируются в \\ufffe и \\uffff, сохраняясь в utf-8-sig."""
    raw_s = "\ufffe\uffff"
    lit = jsutil._js(raw_s)

    assert r"\ufffe" in lit
    assert r"\uffff" in lit
    assert lit == '"\\ufffe\\uffff"'
    assert lit.isascii(), f"Литерал содержит не-ASCII символы: {lit!r}"

    jsx_file = tmp_path / "test_nonchar.jsx"
    jsx_file.write_text(f"var test_nonchar = {lit};\n", encoding="utf-8-sig")


def test_js_node_check_syntax(tmp_path):
    """node --check валидирует синтаксис JS-кода с литералами суррогатов и U+FFFE/U+FFFF."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node не установлен в системе")

    lit1 = jsutil._js("a\udcffb")
    lit2 = jsutil._js("\ufffe\uffff")
    lit3 = jsutil._js("Привет, мир! \u2028 \u2029")

    js_code = (
        f"var x = {lit1};\n"
        f"var y = {lit2};\n"
        f"var z = {lit3};\n"
    )
    js_file = tmp_path / "check_syntax.js"
    js_file.write_text(js_code, encoding="utf-8-sig")

    res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"node --check вернул ошибку: {res.stderr}"


def test_js_cyrillic_and_unicode_remain_raw():
    """Кириллица и стандартный юникод остаются сырыми для стабильности эталонов."""
    cyrillic_text = "Привет, мир! Тестовая строка субтитров"
    lit = jsutil._js(cyrillic_text)
    assert cyrillic_text in lit
    assert r"\u041f" not in lit

    # Смешанный текст: кириллица остаётся сырой, спецсимволы экранируются
    mixed = "Кадр \udcff с переносом\u2028строки и меткой\ufffe"
    lit_mixed = jsutil._js(mixed)
    assert "Кадр " in lit_mixed
    assert " с переносом" in lit_mixed
    assert "строки и меткой" in lit_mixed
    assert r"\udcff" in lit_mixed
    assert r"\u2028" in lit_mixed
    assert r"\ufffe" in lit_mixed


def test_js_multiline_handles_surrogates_and_noncharacters(tmp_path):
    """_js_multiline сохраняет переносы строк AE (\\r) и экранирует суррогаты."""
    multi = "Первая строка\udcff\nВторая строка\ufffe\uffff\r\nТретья"
    lit = jsutil._js_multiline(multi)

    assert r"\udcff" in lit
    assert r"\ufffe" in lit
    assert r"\uffff" in lit
    assert r"\r" in lit
    assert "\n" not in lit

    jsx_file = tmp_path / "test_multiline.jsx"
    jsx_file.write_text(f"var test_multi = {lit};\n", encoding="utf-8-sig")
