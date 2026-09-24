# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Экранирование текста и атрибутов для XML 1.0.

Удаляет символы, запрещённые спецификацией XML 1.0 (C0 кроме \\t\\n\\r,
U+FFFE, U+FFFF, одиночные суррогаты), и экранирует спецсимволы (& < > и \" для атрибутов).
"""
import re

# Запрещённые в XML 1.0 символы: всё кроме #x9, #xA, #xD, #x20-#xD7FF, #xE000-#xFFFD, #x10000-#x10FFFF
_ILLEGAL_XML_CHARS = re.compile(
    r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


def xml_text(s: object, attr: bool = False) -> str:
    """Очистить строку от недопустимых символов XML 1.0 и экранировать & < > (и \" при attr=True)."""
    cleaned = _ILLEGAL_XML_CHARS.sub("", str(s))
    escaped = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if attr:
        escaped = escaped.replace('"', "&quot;")
    return escaped
