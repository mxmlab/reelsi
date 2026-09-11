# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания CL: автосохранение правок сабов в шаблон спикера (styles.patch, /api/style_patch)."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402
from core import styles  # noqa: E402


@pytest.fixture()
def custom_style_file(tmp_path, monkeypatch):
    """Временная папка стилей с тестовым шаблоном."""
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(styles_dir))

    sample = {
        "label": "TestStyle",
        "font": "SFPro-CondensedSemibold",
        "sub_words_per_row": 1,
        "sub_rows_max": 1,
        "music_db": -20.0,
    }
    file_path = styles_dir / "TestStyle.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=1)

    return "TestStyle", str(file_path)


def test_styles_patch_updates_only_given_keys(custom_style_file):
    """styles.patch обновляет только переданные ключи, оставляя остальные нетронутыми."""
    name, path = custom_style_file

    key, returned_path = styles.patch(name, {"sub_words_per_row": 3, "sub_rows_max": 2})
    assert key.lower() == name.lower()
    assert os.path.exists(returned_path)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["sub_words_per_row"] == 3
    assert data["sub_rows_max"] == 2
    # Остальные поля остались без изменений
    assert data["label"] == "TestStyle"
    assert data["font"] == "SFPro-CondensedSemibold"
    assert data["music_db"] == -20.0


def test_styles_patch_not_found(tmp_path, monkeypatch):
    """styles.patch возбуждает FileNotFoundError, если файл шаблона не найден."""
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(styles_dir))

    with pytest.raises(FileNotFoundError):
        styles.patch("nonexistent", {"sub_words_per_row": 2})


def test_styles_patch_validation():
    """styles.patch проверяет валидность аргументов."""
    with pytest.raises(ValueError):
        styles.patch("", {"sub_words_per_row": 2})
    with pytest.raises(ValueError):
        styles.patch("test", "not_a_dict")


def test_api_style_patch_route(custom_style_file):
    """Роут /api/style_patch обновляет файл шаблона через API."""
    name, path = custom_style_file

    import webui
    client = webui.app.test_client()

    resp = client.post("/api/style_patch", json={
        "name": name,
        "patch": {"sub_words_per_row": 4, "sub_rows_max": 2}
    })
    assert resp.status_code == 200
    d = resp.get_json()
    assert d.get("ok") is True

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["sub_words_per_row"] == 4
    assert data["sub_rows_max"] == 2
    assert data["font"] == "SFPro-CondensedSemibold"


def test_api_style_patch_errors(custom_style_file):
    """Роут /api/style_patch корректно возвращает ошибки через umsg."""
    import webui
    client = webui.app.test_client()

    # Без имени
    r1 = client.post("/api/style_patch", json={"patch": {"sub_words_per_row": 2}})
    assert r1.get_json().get("err") == "style_name_missing"

    # Пустой patch
    r2 = client.post("/api/style_patch", json={"name": "TestStyle", "patch": {}})
    assert r2.get_json().get("err") == "empty_patch"

    # Несуществующий стиль
    r3 = client.post("/api/style_patch", json={"name": "NoSuchStyle", "patch": {"sub_words_per_row": 2}})
    assert r3.get_json().get("err") == "styles_patch_failed"


def test_js_insp_sub_edit_no_style_touched():
    """inspSubEdit не ставит STYLE_TOUCHED=true и вызывает patchSubStyleSoon."""
    js = app_meta.app_js_text()

    assert "function inspSubEdit(" in js
    assert "function patchSubStyleSoon(" in js

    # Проверяем, что в теле inspSubEdit нет присваивания STYLE_TOUCHED=true
    idx = js.find("function inspSubEdit()")
    assert idx != -1
    fn_body = js[idx:idx + 600]
    assert "STYLE_TOUCHED" not in fn_body
    assert "patchSubStyleSoon(" in fn_body
