# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка, что имя из ответа чужой службы остаётся безопасным для файловой системы.

Имена и пути приходят не от пользователя, а из ОТВЕТА внешней службы (id файла
стока, имя записи генерации). Враждебный или взломанный провайдер подсунул бы
`id="../../../../tmp/OWNED"` — и файл записался бы за пределами базовой папки.
Поэтому имя чистится ДО склейки пути (без разделителей, `..` и управляющих
символов), а готовый путь дополнительно проверяется `realpath` внутри цели:
одна чистка без проверки не ловит символическую ссылку в самой папке.
"""
from __future__ import annotations

import os
import re

# Имя файла: только буквы, цифры, дефис, подчёркивание и точка. Разделители пути
# (`/`, `\`), голая `..` и всё остальное заменяются разделителем — так «../x»
# превращается в «-x», а не в выход из папки.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Символы, которые делает опасными сам Windows, и хвостовые точки с пробелами:
# «x.» и «x » разбираются не так, как выглядят, и легко обходят сравнение имён.
_WIN_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6",
     "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
     "LPT7", "LPT8", "LPT9"})


def safe_name(raw: object, fallback: str = "file") -> str:
    """Безопасное имя файла целиком: без разделителей пути, `..` и управляющих символов.

    Пустая строка или одно лишь «../..» — не имя, а `fallback`: файлу нужно имя,
    по которому его потом найдёт индекс базы.
    """
    text = _SAFE_NAME_RE.sub("-", str(raw or "").strip())
    text = text.strip("-. ")
    if not text or text in (".", ".."):
        return fallback
    stem = text.split(".", 1)[0].upper()
    if stem in _WIN_RESERVED_NAMES:
        return fallback
    return text[:128]


def inside_dir(base: str, name: str) -> str:
    """Путь `base/name` — и он действительно внутри `base` (иначе `ValueError`).

    `realpath` берётся и у папки, и у результата: символическая ссылка внутри базы
    увела бы файл наружу при внешне безобидном имени.
    """
    root = os.path.realpath(base)
    path = os.path.realpath(os.path.join(root, name))
    if path == root or not path.startswith(root + os.sep):
        raise ValueError(f"путь вышел за пределы папки: {name!r}")
    return path
