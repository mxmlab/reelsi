# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания CI: вкладка «Сабы» на странице разметки."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_sub_tab_html_structure():
    """Проверка разметки вкладки Сабы в index.html."""
    html = _read("templates/index.html")

    # Кнопки-вкладки в панели вставок
    assert 'id="insparts"' in html
    assert 'id="inspbtn_inserts"' in html
    assert 'id="inspbtn_subs"' in html
    assert "openInsPart('inserts')" in html
    assert "openInsPart('subs')" in html

    # Контейнеры вкладок
    assert 'id="inspart_inserts"' in html
    assert 'id="inspart_subs"' in html

    # Контролы вкладки Сабы
    assert 'id="insp_subwords"' in html
    assert 'id="insp_subrows"' in html
    assert 'id="sub_introwarn"' in html
    assert 'id="subrowslist"' in html

    # Диапазон значений слов в строке (1..6)
    m = re.search(r'<input[^>]*id="insp_subwords"[^>]*>', html)
    assert m, "нет input insp_subwords"
    tag = m.group(0)
    assert 'min="1"' in tag and 'max="6"' in tag


def test_sub_tab_js_functions():
    """Проверка наличия функций управления вкладкой Сабы в JS."""
    js = app_meta.app_js_text()

    assert "function openInsPart(" in js
    assert "function syncSubTabUI(" in js
    assert "function inspSubEdit(" in js
    assert "function renderSubTab(" in js
    assert "function renderSubRowsList(" in js
    assert "function subrowHighlight(" in js


def test_sub_tab_css_styles():
    """Проверка CSS-стилей для списка строк субтитров."""
    css = _read("static/app.css")

    assert ".subrowslist" in css
    assert ".subrow-item" in css
    assert ".subrow-time" in css
    assert ".subrow-text" in css


def test_sub_tab_plan_generation(tmp_path):
    """Проверка структуры строк субтитров в плане сцены при разных sub_words_per_row."""
    import gzip
    import shutil

    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)

    # При 1 слове в строке
    plan1 = xml2ae.scene_plan(dst, style={"sub_words_per_row": 1})
    assert "subs" in plan1
    assert len(plan1["subs"]) > 0

    # При 3 словах в строке
    plan3 = xml2ae.scene_plan(dst, style={"sub_words_per_row": 3})
    assert "subs" in plan3
    assert len(plan3["subs"]) < len(plan1["subs"])
    # Проверяем структуру строк в плане
    for sub in plan3["subs"]:
        assert "s" in sub and "e" in sub and "w" in sub
        assert isinstance(sub["s"], (int, float))
        assert isinstance(sub["e"], (int, float))
        assert isinstance(sub["w"], str)
