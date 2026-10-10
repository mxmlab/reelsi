# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож личных файлов: сверка sha256 до и после прогона тестов.

Инцидент 2026-10-09: функцию сценария настроек вызвали мимо pytest с боевым путём
к личному `ai_config.json` в корне репозитория, и сценарий перезаписал его — ключи
API пропали. Общий сторож дерева (`_snapshot_root_files` в conftest) по mtime и
размеру этого не ловит, если файл переписан тем же размером в тот же момент, и
работает только внутри pytest. Этот сторож берёт sha256 трёх личных файлов на старте
сессии и сверяет их на финише: содержимое и хэши в отчёт не попадают — только имена.

Файл `ui_state.json` сюда НЕ входит нарочно: его пишет живой сервер владельца, и
сверка показывала бы его работу как порчу.

Функции здесь чистые: путь к файлу приходит снаружи (`path_of`), поэтому тесты
сторожа проверяют сравнение на временных каталогах и не трогают настоящие файлы.
"""
import hashlib
import os

# Личные файлы корня репозитория, которые тесты не имеют права менять.
PERSONAL_FILES = ("ai_config.json", "named_inserts.json", "insertlib.json")

_ABSENT = None  # отсутствие файла — отдельное значение, sha256 таким строкой не бывает


def file_digest(path):
    """sha256 файла в hex; None — файла нет; «недоступен» — файл не читается."""
    if not os.path.isfile(path):
        return _ABSENT
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return "недоступен"
    return h.hexdigest()


def personal_snapshot(path_of):
    """Снимок личных файлов: имя -> sha256 (или None). path_of(имя) -> путь."""
    return {name: file_digest(path_of(name)) for name in PERSONAL_FILES}


def personal_changes(before, after):
    """Список изменений между двумя снимками — только имена файлов, без хэшей."""
    out = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name, _ABSENT)
        a = after.get(name, _ABSENT)
        if b == a:
            continue
        if b is _ABSENT:
            out.append("появился: %s" % name)
        elif a is _ABSENT:
            out.append("пропал: %s" % name)
        else:
            out.append("изменён: %s" % name)
    return out
