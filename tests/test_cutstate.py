# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты контракта cutstate — сохранение и чтение состояния клипа между ступенями."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import cutstate  # noqa: E402
from core import fileio  # noqa: E402


def test_new_fields_and_defaults():
    """Проверяем, что new() возвращает полную схему со всеми 25 полями,
    постоянные инициализированы переданными значениями, результаты ступеней None,
    а служебные поля в начальном состоянии.
    """
    st = cutstate.new(
        "01_intro",
        "out/01_intro.xml",
        ["cam1.mp4", "cam2.mp4"],
        [0.0, 0.25],
        "cam1.wav",
        scale=1.2,
        cam_return=2,
        speaker="host",
        model="gemini",
        engine="gigaam",
        stages={"asr": True, "sense": True},
        branch="gigaam",
    )

    expected_fields = {
        "version",
        "stem",
        "out",
        "cams",
        "offsets",
        "wav",
        "scale",
        "cam_return",
        "speaker",
        "model",
        "engine",
        "stages",
        "branch",
        "full_text",
        "words",
        "silence_bounds",
        "kept",
        "drop",
        "rule",
        "cutlog",
        "keep",
        "assign",
        "breath_marks",
        "done",
        "error",
    }
    assert set(st.keys()) == expected_fields
    assert st["version"] == 1
    assert st["stem"] == "01_intro"
    assert st["out"] == "out/01_intro.xml"
    assert st["cams"] == ["cam1.mp4", "cam2.mp4"]
    assert st["offsets"] == [0.0, 0.25]
    assert st["wav"] == "cam1.wav"
    assert st["scale"] == 1.2
    assert st["cam_return"] == 2
    assert st["speaker"] == "host"
    assert st["model"] == "gemini"
    assert st["engine"] == "gigaam"
    assert st["stages"] == {"asr": True, "sense": True}
    assert st["branch"] == "gigaam"

    # Результаты ступеней на старте строго None
    stage_results = [
        "full_text",
        "words",
        "silence_bounds",
        "kept",
        "drop",
        "rule",
        "cutlog",
        "keep",
        "assign",
        "breath_marks",
    ]
    for key in stage_results:
        assert st[key] is None

    # Служебные поля
    assert st["done"] == []
    assert st["error"] is None


def test_roundtrip_types(tmp_path):
    """Проверяем круговой рейс типов: на диске хранятся примитивы JSON (сортированные списки,
    строковые ключи словарей), а при чтении load() восстанавливает set, dict[int, str]
    и списки кортежей интервалов.
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    st["kept"] = {3, 1, 2}
    st["drop"] = {7}
    st["rule"] = {5: "decide_markup"}
    st["keep"] = [(0.5, 2.0)]
    st["silence_bounds"] = [(2.0, 2.9)]

    p = tmp_path / "01_clip.state.json"
    cutstate.save(p, st)

    # Проверяем сырое представление в JSON на диске
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)

    assert raw["kept"] == [1, 2, 3]
    assert raw["drop"] == [7]
    assert raw["rule"] == {"5": "decide_markup"}
    assert raw["keep"] == [[0.5, 2.0]]
    assert raw["silence_bounds"] == [[2.0, 2.9]]

    # Проверяем восстановление типов через load
    loaded = cutstate.load(p)
    assert loaded["kept"] == {1, 2, 3}
    assert isinstance(loaded["kept"], set)

    assert loaded["drop"] == {7}
    assert isinstance(loaded["drop"], set)

    assert loaded["rule"] == {5: "decide_markup"}
    assert all(isinstance(k, int) for k in loaded["rule"].keys())

    assert loaded["keep"] == [(0.5, 2.0)]
    assert isinstance(loaded["keep"][0], tuple)

    assert loaded["silence_bounds"] == [(2.0, 2.9)]
    assert isinstance(loaded["silence_bounds"][0], tuple)


def test_unknown_field_survives_update(tmp_path):
    """Проверяем, что неизвестные поля из будущих версий схемы не затираются
    при вызове update().
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    p = tmp_path / "01_clip.state.json"
    cutstate.save(p, st)

    # Имитируем добавление поля из будущей версии схемы напрямую в файл
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    raw["поле_из_будущего"] = 42
    with open(p, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)

    words_data = [{"w": "тест", "start": 0.1, "end": 0.4}]
    res = cutstate.update(p, words=words_data)

    assert res.get("поле_из_будущего") == 42
    assert res["words"] == words_data

    # Проверяем также чтение заново с диска
    reloaded = cutstate.load(p)
    assert reloaded.get("поле_из_будущего") == 42
    assert reloaded["words"] == words_data


def test_mark_done_dedup_and_order(tmp_path):
    """Проверяем, что повторный вызов mark_done с той же ступенью не дублирует ключ
    и не ломает исходный порядок выполнения.
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    p = tmp_path / "01_clip.state.json"
    cutstate.save(p, st)

    cutstate.mark_done(p, "asr")
    cutstate.mark_done(p, "sense")
    cutstate.mark_done(p, "asr")
    cutstate.mark_done(p, "refine")
    cutstate.mark_done(p, "sense")

    loaded = cutstate.load(p)
    assert loaded["done"] == ["asr", "sense", "refine"]


def test_mark_error(tmp_path):
    """Проверяем, что mark_error извлекает тип и текст из исключения,
    формируя корректную запись ошибки, которую затем возвращает load.
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    p = tmp_path / "01_clip.state.json"
    cutstate.save(p, st)

    exc = ValueError("Ошибка декодирования аудиодорожки")
    cutstate.mark_error(p, "asr", exc)

    loaded = cutstate.load(p)
    assert loaded["error"] == {
        "stage": "asr",
        "type": "ValueError",
        "msg": "Ошибка декодирования аудиодорожки",
    }


def test_load_nonexistent_file(tmp_path):
    """Проверяем, что load() отсутствующего файла поднимает FileNotFoundError,
    а не возвращает пустой словарь (отсутствие файла — авария, а не старт с нуля).
    """
    missing_path = tmp_path / "nonexistent.state.json"
    with pytest.raises(FileNotFoundError):
        cutstate.load(missing_path)


def test_path_for():
    """Проверяем, что path_for формирует имя <idx:02d>_<stem>.state.json с ведущим нулем."""
    res = cutstate.path_for("/some/dir", 1, "clip")
    assert os.path.basename(res) == "01_clip.state.json"

    res_zero = cutstate.path_for("/some/dir", 0, "clip")
    assert os.path.basename(res_zero) == "00_clip.state.json"

    res_two_digits = cutstate.path_for("/some/dir", 42, "clip")
    assert os.path.basename(res_two_digits) == "42_clip.state.json"


def test_atomic_write(tmp_path, monkeypatch):
    """Проверяем, что все операции записи (save, update, mark_done, mark_error)
    выполняются исключительно через атомарный atomic_json_dump, а не через прямой open(..., 'w').
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    p = tmp_path / "01_clip.state.json"

    # Инициализируем файл на диске
    cutstate.save(p, st)

    calls = []
    orig_dump = fileio.atomic_json_dump

    def spy_atomic_dump(path, obj, **kw):
        calls.append(str(path))
        return orig_dump(path, obj, **kw)

    monkeypatch.setattr(fileio, "atomic_json_dump", spy_atomic_dump)

    # 1. save
    cutstate.save(p, st)
    assert len(calls) == 1

    # 2. update
    cutstate.update(p, full_text="тестовый текст")
    assert len(calls) == 2

    # 3. mark_done
    cutstate.mark_done(p, "asr")
    assert len(calls) == 3

    # 4. mark_error
    cutstate.mark_error(p, "sense", RuntimeError("сбой модели"))
    assert len(calls) == 4

    assert all(c == str(p) for c in calls)


def test_version_immutable(tmp_path):
    """Проверяем инвариант неизменности версии схемы: ни update, ни mark_done,
    ни mark_error не могут изменить поле version.
    """
    st = cutstate.new("clip", "out.xml", ["c1.mp4"], [0.0], "c1.wav", scale=1.0, cam_return=1)
    p = tmp_path / "01_clip.state.json"
    cutstate.save(p, st)

    cutstate.update(p, version=999, full_text="тест")
    assert cutstate.load(p)["version"] == 1

    cutstate.mark_done(p, "asr")
    assert cutstate.load(p)["version"] == 1

    cutstate.mark_error(p, "asr", ValueError("ошибка"))
    assert cutstate.load(p)["version"] == 1
