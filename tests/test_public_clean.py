# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож обезличивания всего публикуемого дерева (задание BH, TASKS.md).

Одна разовая чистка (Фаза 2, 2026-08-08) личное не удержала — новая волна работы
заносит имена обратно, и узнаём мы об этом ревизией перед релизом. Поэтому личное
держится ТЕСТОМ: обходим всё, что ПОПАДЁТ в коммит: отслеживаемые файлы и новые,
ещё не попавшие в индекс, — `git ls-files --cached --others --exclude-standard`
за вычетом `.publicignore` — и падаем на фамилиях, логине и рабочем пути.
`.publicignore` — единственный источник правды
о том, что публикуется; второй список ни в тесте, ни в доках не заводится.

Списков в публикуемом коде нет: всё личное — в непубликуемом
`tests/personal_words.txt` (под `.publicignore`), поэтому обходятся ВСЕ
публикуемые файлы.

Ловушка: `C:\Users` целиком запрещать НЕЛЬЗЯ — в тултипах интерфейса и в `en.json`
это законные образцы `C:\Users\Me\Downloads` и `C:\Users\...\Reelsi_out`. Запрещён
конкретный логин, а не буква диска.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402

SPDX = "SPDX-License-Identifier: AGPL-3.0-or-later"
SPDX_EXT = (".py", ".js", ".jsx", ".ps1", ".sh")


def _git_files():
    """Отслеживаемые плюс новые файлы, которые попали бы в коммит.

    Простой `git ls-files` видит только то, что уже в индексе: новый файл в
    рабочей копии сторож не замечает — набор зелёный до коммита и красный сразу
    после него (tests/test_py_exec.py, 2026-09-09). Сторож обязан срабатывать
    ДО `git add`, отсюда `--cached --others`; `--exclude-standard` уважает
    .gitignore и оставляет за бортом рабочий мусор. Повторы из вывода убираем,
    чтобы файл не обошёлся дважды; порядок сохраняем.

    Файлы, которых нет на диске, пропускаем: пока переезд (задание GU) не закоммичен,
    индекс ещё держит старые пути — `ae_inspect.jsx` числится отслеживаемым, а лежит
    уже в `tools/`. Читать нечего — значит и проверять нечего.
    """
    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True, encoding="utf-8")
    seen = set()
    files = []
    for f in out.splitlines():
        if f and f not in seen and os.path.exists(os.path.join(ROOT, f)):
            seen.add(f)
            files.append(f)
    return files


def _ignored_patterns():
    path = os.path.join(ROOT, public_slice.IGNORE_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return public_slice.parse_ignore(f.read())


def _public_files():
    patterns = _ignored_patterns()
    return [f for f in _git_files() if not public_slice.is_ignored(f, patterns)]


def _load_words():
    words_path = os.path.join(ROOT, public_slice.WORDS_FILE)
    if not os.path.exists(words_path):
        return None
    try:
        with open(words_path, encoding="utf-8") as f:
            return public_slice.parse_words(f.read())
    except OSError:
        return None


def test_public_tree_has_no_personal_data(capsys):
    """Публикуемые файлы не несут фамилий, логина и рабочего пути."""
    words = _load_words()
    if words is None:
        pytest.skip("список личных слов не публикуется, проверка идёт в приватном репозитории")
    bad = []
    files = []
    for rel in _public_files():
        files.append(rel)
        path = os.path.join(ROOT, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        bad.extend(public_slice.find_personal(rel, text, words))
    print(f"просканировано файлов: {len(files)}")
    assert not bad, "в публикуемых файлах личное:\n" + "\n".join(bad)


def test_public_code_has_spdx():
    """Публикуемые исходники несут SPDX-заголовок (AGPL-3.0-or-later)."""
    missing = []
    for rel in _public_files():
        if not rel.endswith(SPDX_EXT):
            continue
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        if SPDX not in text:
            missing.append(rel)
    assert not missing, f"нет заголовка {SPDX}:\n" + "\n".join(missing)


def test_сторож_видит_новый_файл_до_индекса():
    """Новый .py-файл без SPDX-шапки сторож видит ещё до `git add`.

    `git ls-files` без `--others` про неотслеживаемый файл молчит: набор зелёный
    до коммита и красный сразу после (tests/test_py_exec.py, 2026-09-09). Поэтому
    кладём новый файл во временный подкаталог репозитория, которого нет ни в
    .gitignore, ни в .publicignore (иначе проверка была бы зелёной впустую),
    и ждём, что `_public_files()` его вернёт. В finally всё убираем.
    """
    subdir = ".guard_untracked_tmp"
    rel = subdir + "/new_file_no_spdx.py"
    abs_path = os.path.join(ROOT, subdir, "new_file_no_spdx.py")
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("def probe():\n    pass\n")
        assert rel in _public_files(), (
            "новый файл без SPDX-шапки не виден сторожам до `git add`: " + rel
        )
    finally:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        subdir_path = os.path.join(ROOT, subdir)
        if os.path.isdir(subdir_path):
            os.rmdir(subdir_path)
