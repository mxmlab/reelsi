# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистой логики инструментов локализации: tools/i18n_js_keys.py и tools/i18n_merge.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.i18n_js_keys as js_keys
import tools.i18n_merge as merge


def test_i18n_js_unescape() -> None:
    """_js_unescape преобразует экранированные последовательности в реальные символы."""
    raw = r"Line 1\nLine 2\tTab\\Backslash\'Single\"Double\0Null\unknown"
    res = js_keys._js_unescape(raw)
    assert res == "Line 1\nLine 2\tTab\\Backslash'Single\"Double\0Nullunknown"


def test_i18n_js_keys_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() в i18n_js_keys находит русские ключи t('...') и сопоставляет со словарем."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    # JS файл с русскими и английскими t('...')
    js_file = app_dir / "01-test.js"
    js_file.write_text(
        "const a = t('Ключ один\\nс переносом');\n"
        "const b = t('Only english');\n"
        "const c = t('Переведённый ключ');\n",
        encoding="utf-8",
    )

    # Не-JS файл (должен игнорироваться)
    ignore_file = app_dir / "readme.txt"
    ignore_file.write_text("t('Игнор');", encoding="utf-8")

    # Словарь en.json
    en_file = tmp_path / "en.json"
    en_file.write_text(
        json.dumps({"Переведённый ключ": "Translated key"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(js_keys, "APP", str(app_dir))
    monkeypatch.setattr(js_keys, "EN", str(en_file))

    code = js_keys.main()
    assert code == 0
    out = capsys.readouterr().out
    assert "t('...') ключей всего: 2, без перевода: 1" in out
    assert "Ключ один\nс переносом" in out


def test_i18n_js_keys_main_without_en_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() в i18n_js_keys работает, если файл en.json ещё не существует."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "test.js").write_text("t('Новый ключ');", encoding="utf-8")

    monkeypatch.setattr(js_keys, "APP", str(app_dir))
    monkeypatch.setattr(js_keys, "EN", str(tmp_path / "absent_en.json"))

    code = js_keys.main()
    assert code == 0
    out = capsys.readouterr().out
    assert "без перевода: 1" in out


def test_i18n_merge_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() в i18n_merge дополняет en.json новыми ключами, не затирая существующие."""
    en_file = tmp_path / "en.json"
    initial_data = {
        "Кастом": "Existing custom translation",  # Не должно перезаписаться
        "Ступени нарезки": "",  # Пустой перевод — должен перезаписаться
    }
    en_file.write_text(json.dumps(initial_data, ensure_ascii=False), encoding="utf-8")

    test_additions = {
        "Кастом": "Custom",
        "Ступени нарезки": "Cutting stages",
        "Новый ключ": "New key",
    }
    monkeypatch.setattr(merge, "EN", str(en_file))
    monkeypatch.setattr(merge, "ADDITIONS", test_additions)

    code = merge.main()
    assert code == 0

    merged = json.loads(en_file.read_text(encoding="utf-8"))
    assert merged["Кастом"] == "Existing custom translation"
    assert merged["Ступени нарезки"] == "Cutting stages"
    assert merged["Новый ключ"] == "New key"


def test_i18n_merge_empty_translation_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой перевод в ADDITIONS вызывает отказ SystemExit."""
    en_file = tmp_path / "en.json"
    en_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(merge, "EN", str(en_file))
    monkeypatch.setattr(merge, "ADDITIONS", {"Ошибка": "   "})

    with pytest.raises(SystemExit, match="пустой перевод"):
        merge.main()
