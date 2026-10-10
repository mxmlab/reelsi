# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выбор «Движок нарезки (текст)» в настройках — статическая проверка интерфейса.

Без браузера: селект стоит в разметке ПОСЛЕ «Движок нарезки (тайминги)», его функции
определены, обе «вторые двери» (заполнение профилей и загрузка списка движков) его
обновляют, а сводка нарезки показывает выбранный текстовый движок.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX_HTML = os.path.join(ROOT, "templates", "index.html")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _func_body(src, name):
    """Тело функции верхнего уровня `name`: от объявления до следующего объявления."""
    m = re.search(r"^(?:async )?function " + name + r"\(", src, re.M)
    assert m, "функция %s не найдена" % name
    nxt = re.search(r"^(?:async )?function \w+\(", src[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start():end]


def test_text_select_in_markup_after_cut_asr():
    html = _read(INDEX_HTML)
    m = re.search(r'<select id="cut_text_asr"[^>]*onchange="setCutTextAsr\(this\.value\)"', html)
    assert m, 'в index.html нет <select id="cut_text_asr" с onchange="setCutTextAsr(this.value)"'
    asr = html.find('id="cut_asr"')
    assert asr != -1 and asr < m.start(), "cut_text_asr должен стоять ПОСЛЕ cut_asr"


def test_set_and_fill_functions_defined():
    src = _read(SETTINGS_JS)
    assert re.search(r"^(?:async )?function setCutTextAsr\(", src, re.M), "нет setCutTextAsr"
    assert re.search(r"^(?:async )?function fillCutTextAsr\(", src, re.M), "нет fillCutTextAsr"
    assert "set_active_cut_text_asr" in _func_body(src, "setCutTextAsr")


def test_second_doors_refresh_text_select():
    settings = _read(SETTINGS_JS)
    styles = _read(STYLES_JS)
    assert re.search(r"\bfillCutTextAsr\(\)", _func_body(settings, "fillAIProfileSelects")), (
        "fillAIProfileSelects не обновляет cut_text_asr")
    assert re.search(r"\bfillCutTextAsr\(\)", _func_body(styles, "loadASREngines")), (
        "loadASREngines не обновляет cut_text_asr")


def test_cut_summary_mentions_text_engine():
    src = _read(SETTINGS_JS)
    assert "active_cut_text_asr" in _func_body(src, "cutSummary"), (
        "cutSummary не показывает текстовый движок нарезки")
