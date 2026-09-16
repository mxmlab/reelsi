# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IB (круг 8), п. 1: права, ссылки и переводы строк в `core/fileio.py`.

`mkstemp` создаёт tmp с правами 0600, а `os.replace` переносил их на файл
пользователя (0644/0664 -> 0600), поэтому обычный файл после первой атомарной
записи становился «только для владельца». Ссылка при этом подменялась обычным
файлом, а ЦЕЛЬ оставалась со старыми данными: на `styles/` и `speakers/`,
подключённых junction'ом к рабочей копии сессии, это выглядело как «сохранил
пресет, а его нигде нет». Плюс `atomic_text_write` не умел `newline` — профили
спикеров пишутся с CRLF (`core/speakers.py`).

Запуск: py -3.10 -m pytest tests/test_r8_ib_fileio.py -q -p no:cacheprovider
"""
import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import fileio  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="права POSIX на Windows не проверить")


def test_json_пишется_побайтово_как_прямой_записью(tmp_path):
    """Содержимое вывода не изменилось: тот же json.dump(ensure_ascii=False), те же
    переводы строк. Атомарность не имеет права поменять формат файлов."""
    obj = {"б": 1, "a": [1, 2, {"c": None}], "ё": "текст"}
    atomic = tmp_path / "atomic.json"
    direct = tmp_path / "direct.json"
    fileio.atomic_json_dump(str(atomic), obj, indent=1)
    with open(direct, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    assert atomic.read_bytes() == direct.read_bytes()
    assert [p.name for p in tmp_path.iterdir() if ".tmp." in p.name] == [], \
        "временный файл остался лежать"


@posix_only
def test_права_существующего_файла_сохраняются(tmp_path):
    """0644 и 0664 переживают атомарную запись: mkstemp даёт 0600, и без переноса
    прав файл пользователя молча становился закрытым."""
    for mode in (0o644, 0o664):
        p = tmp_path / f"m{mode:o}.json"
        p.write_text("{}", encoding="utf-8")
        os.chmod(p, mode)
        fileio.atomic_json_dump(str(p), {"a": 1})
        assert stat.S_IMODE(p.stat().st_mode) == mode, \
            f"права {mode:o} не сохранились: {stat.S_IMODE(p.stat().st_mode):o}"


@posix_only
def test_права_нового_файла_по_umask(tmp_path):
    """У нового файла права как у `open(..., "w")` — 0666 & ~umask, снятый при импорте
    модуля, а не 0600 от mkstemp."""
    old_umask = os.umask(0o027)
    try:
        importlib.reload(fileio)                       # umask снимается при импорте
        assert fileio._NEW_FILE_MODE == 0o640, "umask снят не при импорте модуля"
        p = tmp_path / "new.json"
        fileio.atomic_json_dump(str(p), {"a": 1})
        assert stat.S_IMODE(p.stat().st_mode) == 0o640, \
            f"новый файл не по umask: {stat.S_IMODE(p.stat().st_mode):o}"
        t = tmp_path / "new.txt"
        fileio.atomic_text_write(str(t), "текст")
        assert stat.S_IMODE(t.stat().st_mode) == 0o640
    finally:
        os.umask(old_umask)
        importlib.reload(fileio)                       # вернуть модуль в обычное состояние


def test_запись_через_ссылку_меняет_цель_а_не_ссылку(tmp_path):
    """Ссылку не подменяем: `styles/` и `speakers/` в рабочей копии сессии — junction
    на общие папки, и os.replace по пути ссылки оставлял цель со старыми данными."""
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("симлинки в этом окружении не создаются")
    fileio.atomic_json_dump(str(link), {"a": 1})
    assert os.path.islink(link), "ссылка заменена обычным файлом"
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}, \
        "данные не доехали до цели ссылки"


def test_text_write_newline(tmp_path):
    """newline=None — как у `open()` по умолчанию (на Windows «\\n» и есть CRLF),
    newline="\\r\\n" — как в прежней прямой записи профилей спикеров."""
    plain = tmp_path / "plain.txt"
    fileio.atomic_text_write(str(plain), "a\nb")
    assert plain.read_bytes() == "a\nb".replace("\n", os.linesep).encode("utf-8")

    crlf = tmp_path / "crlf.txt"
    fileio.atomic_text_write(str(crlf), "a\nb", newline="\r\n")
    assert crlf.read_bytes() == b"a\r\nb"


def test_сбой_записи_не_трогает_старый_файл(tmp_path, monkeypatch):
    """Сбой на подмене файла (или «Стоп» до неё) оставляет прежние данные: смысл
    всего модуля. Временный файл при этом убирается."""
    p = tmp_path / "state.json"
    p.write_text('{"old": 1}', encoding="utf-8")

    def boom(*a, **k):
        raise OSError("сбой на подмене файла")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        fileio.atomic_json_dump(str(p), {"new": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"old": 1}, "старый файл затёрт"
    assert [x.name for x in tmp_path.iterdir()] == ["state.json"], "временный файл остался"
