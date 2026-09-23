# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож кодировки файлов требований.

Старый pip на Windows читает requirements*.txt в системной кодировке (cp1252), и
UTF-8-кириллица в комментарии роняет установку на первом же байте, которого в cp1252
нет:

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position 265

Именно это и случилось с requirements-dev.txt после выпуска 0.2.0-beta, но проверка
жила только в CI на Windows: ни полный локальный прогон, ни прогон на срезе её не
видели, поэтому поломка всплыла уже после публикации. Здесь та же проверка, но
локальная и независимая от платформы: каждый requirements*.txt в корне обязан
читаться и в cp1252 (старый pip на Windows), и в utf-8-sig (современный pip).

Кодировки указаны явно, а не через locale.getpreferredencoding(): на Linux это utf-8,
и тест был бы зелёным впустую. Список файлов берётся glob'ом, а не константой: новый
файл требований попадает под проверку сам.

Запуск: python -m pytest tests/test_requirements_encoding.py -q
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# cp1252 — системная кодировка, которой старый pip на Windows читает файлы
# требований; utf-8-sig — кодировка современного pip (BOM он тоже съедает).
ENCODINGS = ("cp1252", "utf-8-sig")


def _requirement_files():
    """requirements*.txt в корне репозитория; пустой список — ошибка, а не молчание."""
    files = sorted(ROOT.glob("requirements*.txt"))
    assert files, (
        "в корне репозитория нет ни одного requirements*.txt: сторож проверял бы "
        "пустой список и был бы зелёным всегда"
    )
    return files


def test_requirements_files_found():
    """Glob видит файлы требований — иначе проверка кодировок вырождена в пустую."""
    names = [path.name for path in _requirement_files()]
    assert "requirements.txt" in names, f"среди найденных файлов нет requirements.txt: {names}"


def test_requirements_read_in_cp1252_and_utf8_sig():
    """Каждый requirements*.txt читается и старым pip (cp1252), и современным (utf-8-sig)."""
    problems = []
    for path in _requirement_files():
        for enc in ENCODINGS:
            try:
                path.read_text(encoding=enc)
            except UnicodeDecodeError as e:
                problems.append(f"{path.name} не читается в {enc}: {e}")
    assert not problems, (
        "файлы требований обязаны читаться и старым pip на Windows (cp1252), и\n"
        "современным (utf-8-sig). Не-ASCII комментарий роняет установку с\n"
        "UnicodeDecodeError, которого в cp1252 просто нет как байта, — комментарии в\n"
        "requirements*.txt держим по-английски (ASCII):\n  " + "\n  ".join(problems)
    )
