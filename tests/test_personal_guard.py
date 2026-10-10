# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож личных файлов (tests/_personal_guard.py): сравнение снимков.

Проверяется только сама функция сравнения на временных каталогах. Настоящие личные
файлы корня репозитория этот тест не читает и не меняет.
"""
import os

from tests._personal_guard import (
    PERSONAL_FILES,
    file_digest,
    personal_changes,
    personal_snapshot,
)


def _snap(d):
    return personal_snapshot(lambda name: os.path.join(str(d), name))


def test_personal_list_has_three_files_and_no_ui_state():
    """Состав сторожа: три файла; ui_state.json нарочно не сторожится (пишет сервер)."""
    assert set(PERSONAL_FILES) == {"ai_config.json", "named_inserts.json", "insertlib.json"}
    assert "ui_state.json" not in PERSONAL_FILES


def test_unchanged_files_report_nothing(tmp_path):
    (tmp_path / "ai_config.json").write_text('{"a": 1}', encoding="utf-8")
    before = _snap(tmp_path)
    assert personal_changes(before, _snap(tmp_path)) == []


def test_absent_files_stay_absent_without_report(tmp_path):
    before = _snap(tmp_path)
    assert all(v is None for v in before.values())
    assert personal_changes(before, _snap(tmp_path)) == []


def test_same_size_and_same_mtime_rewrite_is_still_caught(tmp_path):
    """Главный случай: переписали тем же размером и вернули mtime — sha256 видит."""
    f = tmp_path / "ai_config.json"
    f.write_text('{"key": "AAAA"}', encoding="utf-8")
    st = os.stat(f)
    before = _snap(tmp_path)
    f.write_text('{"key": "BBBB"}', encoding="utf-8")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(f).st_size == st.st_size
    assert personal_changes(before, _snap(tmp_path)) == ["изменён: ai_config.json"]


def test_appeared_and_disappeared_files_are_named(tmp_path):
    before = _snap(tmp_path)
    (tmp_path / "insertlib.json").write_text("{}", encoding="utf-8")
    assert personal_changes(before, _snap(tmp_path)) == ["появился: insertlib.json"]

    before = _snap(tmp_path)
    os.remove(tmp_path / "insertlib.json")
    assert personal_changes(before, _snap(tmp_path)) == ["пропал: insertlib.json"]


def test_report_contains_names_only_not_digests(tmp_path):
    f = tmp_path / "named_inserts.json"
    f.write_text("one", encoding="utf-8")
    before = _snap(tmp_path)
    f.write_text("two", encoding="utf-8")
    report = " ".join(personal_changes(before, _snap(tmp_path)))
    digest = file_digest(str(f))
    assert "named_inserts.json" in report
    assert digest not in report
