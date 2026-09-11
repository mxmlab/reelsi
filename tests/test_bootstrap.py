# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты первого запуска и инициализации файлов из примеров (bootstrap.py).

Запуск:  python -m pytest reelsi/tests/test_bootstrap.py -q
"""
import copy
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import bootstrap  # noqa: E402
from core import insertlib  # noqa: E402
from core import speakers  # noqa: E402
from core import styles  # noqa: E402

EXAMPLES = {
    "insertlib": os.path.join(ROOT, "examples", "insertlib.example.json"),
    "styles": os.path.join(ROOT, "examples", "styles.example.json"),
    "speakers": os.path.join(ROOT, "examples", "speakers.example.json"),
}


def _setup_clean_env(tmp_path, monkeypatch):
    """Изолированная среда без пользовательских файлов, но с example.json."""
    root_dir = tmp_path / "repo"
    root_dir.mkdir()
    for name, src in EXAMPLES.items():
        shutil.copy2(src, root_dir / os.path.basename(src))

    style_dir = root_dir / "styles"
    speaker_dir = root_dir / "speakers"

    # bootstrap берёт личные файлы и примеры из core.paths (корень репозитория):
    # подменяем корень и папку примеров, а не отдельную константу модуля.
    monkeypatch.setattr(bootstrap.paths, "ROOT", str(root_dir))
    monkeypatch.setattr(bootstrap.paths, "EXAMPLES", str(root_dir))
    monkeypatch.setattr(styles, "STYLE_DIR", str(style_dir))
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(speaker_dir))
    return root_dir, style_dir, speaker_dir


from core import app_meta  # noqa: E402


def test_bootstrap_creates_missing_files_russian(tmp_path, monkeypatch):
    """Чистая установка на русском: bootstrap заводит insertlib.json, styles/Мой стиль.json и speakers/Пример профиля.json."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    root_dir, style_dir, speaker_dir = _setup_clean_env(tmp_path, monkeypatch)

    created = bootstrap.ensure_user_files()

    assert len(created) == 3, f"ожидалось 3 сообщения, получено: {created}"
    assert any("insertlib.json" in m for m in created)
    assert any("styles/Мой стиль.json" in m for m in created)
    assert any("speakers/Пример профиля.json" in m for m in created)

    ins_path = root_dir / "insertlib.json"
    st_path = style_dir / "Мой стиль.json"
    sp_path = speaker_dir / "Пример профиля.json"

    assert ins_path.is_file(), "insertlib.json не создан"
    assert st_path.is_file(), "styles/Мой стиль.json не создан"
    assert sp_path.is_file(), "speakers/Пример профиля.json не создан"

    # Созданные файлы валидны для загрузчиков
    st_data = json.load(open(st_path, encoding="utf-8"))
    resolved = styles.resolve(copy.deepcopy(st_data))
    assert resolved["label"] == "Мой стиль"

    sp_loaded = speakers.all_speakers()
    assert any(v.get("label") == "Пример профиля" for v in sp_loaded.values())

    ins_data = json.load(open(ins_path, encoding="utf-8"))
    assert ins_data.get("emb_tag") == insertlib.EMB_TAG


def test_bootstrap_creates_missing_files_english(tmp_path, monkeypatch):
    """Чистая установка на английском: bootstrap заводит insertlib.json, styles/My style.json и speakers/Example speaker.json."""
    monkeypatch.setenv("REELSI_LANG", "en")
    app_meta.ui_lang(force_reload=True)
    root_dir, style_dir, speaker_dir = _setup_clean_env(tmp_path, monkeypatch)

    created = bootstrap.ensure_user_files()

    assert len(created) == 3, f"ожидалось 3 сообщения, получено: {created}"
    assert any("insertlib.json" in m for m in created)
    assert any("styles/My style.json" in m for m in created)
    assert any("speakers/Example speaker.json" in m for m in created)

    ins_path = root_dir / "insertlib.json"
    st_path = style_dir / "My style.json"
    sp_path = speaker_dir / "Example speaker.json"

    assert ins_path.is_file(), "insertlib.json не создан"
    assert st_path.is_file(), "styles/My style.json не создан"
    assert sp_path.is_file(), "speakers/Example speaker.json не создан"

    # Созданные файлы валидны для загрузчиков
    st_data = json.load(open(st_path, encoding="utf-8"))
    assert st_data.get("label") == "My style"
    resolved = styles.resolve("My style")
    assert resolved["label"] == "My style"

    sp_loaded = speakers.all_speakers()
    assert any(v.get("label") == "Example speaker" for v in sp_loaded.values())

    ins_data = json.load(open(ins_path, encoding="utf-8"))
    assert ins_data.get("emb_tag") == insertlib.EMB_TAG


def test_bootstrap_idempotent(tmp_path, monkeypatch):
    """Идемпотентность: второй запуск подряд ничего не меняет и молчит."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    root_dir, style_dir, speaker_dir = _setup_clean_env(tmp_path, monkeypatch)

    first_created = bootstrap.ensure_user_files()
    assert len(first_created) == 3

    ins_path = root_dir / "insertlib.json"
    st_path = style_dir / "Мой стиль.json"
    sp_path = speaker_dir / "Пример профиля.json"

    mtimes_before = {p: os.path.getmtime(p) for p in (ins_path, st_path, sp_path)}
    contents_before = {p: open(p, "rb").read() for p in (ins_path, st_path, sp_path)}

    time.sleep(0.01)
    second_created = bootstrap.ensure_user_files()

    assert second_created == [], f"второй запуск создал лишнее: {second_created}"
    for p in (ins_path, st_path, sp_path):
        assert os.path.getmtime(p) == mtimes_before[p], f"mtime изменился для {p}"
        assert open(p, "rb").read() == contents_before[p], f"содержимое изменилось для {p}"


def test_bootstrap_does_not_overwrite_existing_files(tmp_path, monkeypatch):
    """Не затирает своё: если в папках уже есть файлы, bootstrap молчит."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    root_dir, style_dir, speaker_dir = _setup_clean_env(tmp_path, monkeypatch)

    style_dir.mkdir(parents=True)
    speaker_dir.mkdir(parents=True)

    custom_st = style_dir / "МойКастомныйСтиль.json"
    custom_st.write_text('{"label": "Кастом"}', encoding="utf-8")

    custom_sp = speaker_dir / "МойСпикер.json"
    custom_sp.write_text('{"label": "Спикер"}', encoding="utf-8")

    custom_ins = root_dir / "insertlib.json"
    custom_ins.write_text('{"items": ["my_item"]}', encoding="utf-8")

    created = bootstrap.ensure_user_files()

    assert created == [], f"bootstrap создал файлы при наличии своих: {created}"
    assert not (style_dir / "Мой стиль.json").exists()
    assert not (speaker_dir / "Пример профиля.json").exists()
    assert json.load(open(custom_ins, encoding="utf-8")) == {"items": ["my_item"]}


def test_bootstrap_isolated_profile(tmp_path, monkeypatch):
    """Изолированный профиль: bootstrap заводит базовую установку,
    а профиль через REELSI_INSERTLIB копирует рабочий индекс в свой тестовый путь."""
    root_dir, _, _ = _setup_clean_env(tmp_path, monkeypatch)

    bootstrap.ensure_user_files()

    base_ins = root_dir / "insertlib.json"
    profile_ins = tmp_path / "insertlib.test.json"

    assert base_ins.is_file()
    assert not profile_ins.exists()

    monkeypatch.setattr(insertlib, "INDEX_PATH", str(profile_ins))
    monkeypatch.setattr(insertlib.paths, "ROOT", str(root_dir))
    insertlib._CACHE["data"] = None

    loaded = insertlib._load()
    assert loaded is not None
    assert profile_ins.is_file(), "изолированный профиль не получил копию индекса"
    assert profile_ins.read_bytes() == base_ins.read_bytes()
