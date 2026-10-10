# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: личные названия препаратов не попадают в публичное дерево.

Словарь вставок по названиям — личные данные владельца, как стили и спикеры: в публичном
коде, доках и тестах не должно быть ни одного слова из него. Сторож проходит по всем
файлам дерева (`tests/gitfiles.py`: git, а в распакованном срезе без `.git` — обход).

Исключения — ЯВНЫЕ, каждое с причиной. Все они старше переноса на общий механизм
(коммит d22a3c8b, 2026-10-09) и в них слово встречается не как название препарата:
история релизов, журнал заданий, примеры цензуры. Исключается целый файл, поэтому
правку в таком файле ревьюят вручную.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests import gitfiles  # noqa: E402

# Читает дерево вместе с неотслеживаемыми файлами: под `-n auto` идёт в одном воркере,
# как и остальные сторожи дерева (см. test_public_clean).
pytestmark = pytest.mark.xdist_group("repo_tree")

# Слова личного словаря (в нижнем регистре). Сам файл словаря в дереве не лежит — только пример.
FORBIDDEN = (
    "тренболон", "trenbolone", "мастерон", "нандролон",
    "тестостерон", "testosterone", "drug_aliases",
)

EXEMPT = {
    "tests/personal_words.txt": "личный список слов для обезличивания, не словарь вставок",
    "tests/test_named_guard.py": "сам сторож: список слов обязан где-то стоять",
    "CHANGELOG.md": "история релизов (28.09): стем цензуры «трен» в примере «тренболон»",
    "TASKS.md": "журнал заданий (21.08): имя картинки из старой проверки подбора",
    "core/censor.py": "документация цензуры (28.09): примеры стемов слов, не словарь препаратов",
    "tests/test_censor.py": "тесты цензуры (28.09): те же примеры стемов",
    "core/subtitle_blobs.py": "тестовая строка для раскладки текста (ТЕСТОСТЕРОН — длинное слово, 12.09), не препарат",
    "tests/test_verify.py": "тестовая строка-слово для переноса в титрах (ТЕСТОСТЕРОН, 12.09), не препарат",
    "tests/test_intro_back_plane.py": "тестовая строка-слово для вставки в интро (ТЕСТОСТЕРОН, 27.07), не препарат",
}


def _text(rel: str) -> str | None:
    """Текст файла в нижнем регистре; бинарные (с NUL-байтами) — None, там слов не ищем."""
    with open(os.path.join(ROOT, rel), "rb") as fh:
        data = fh.read()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace").lower()


def test_no_personal_drug_names_in_the_public_tree():
    """Ни одно слово из личного словаря не встречается в файлах дерева вне явных исключений."""
    hits: list[str] = []
    for rel in gitfiles.staged(ROOT):
        if rel in EXEMPT or not os.path.isfile(os.path.join(ROOT, rel)):
            continue
        text = _text(rel)
        if text is None:
            continue
        for word in FORBIDDEN:
            if word in text:
                hits.append(f"{rel}: «{word}»")
    assert not hits, "личные названия в публичном дереве:\n" + "\n".join(hits)
