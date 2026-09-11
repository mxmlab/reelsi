# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Страж решения EP: промпт vision не зовёт стиль и цвета и требует только фразу предмета.

Прежний промпт просил «style (…, notable colors» — эти слова засоряли описания
(photo 486 раз на 1373, цвета 964) и склеивали документы в семантическом поиске.
Без этого теста решение легко откатить случайно.

Запуск:  python -m pytest reelsi/tests/test_describe_prompt.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib


def test_describe_prompt_has_no_style_or_colors():
    """В промпте нет 'notable colors' и 'style (', есть 'ONLY the object phrase'."""
    p = insertlib._DESCRIBE_PROMPT
    assert "notable colors" not in p
    assert "style (" not in p
    assert "ONLY the object phrase" in p


def test_describe_prompt_forbids_refusal_and_brands():
    """ES: в промпте нет строки про бренды (она давала 11 % отказов) и есть явный запрет
    отвечать, что предмет не определяется."""
    p = insertlib._DESCRIBE_PROMPT
    assert "brand" not in p
    assert "Never answer that objects cannot be identified" in p