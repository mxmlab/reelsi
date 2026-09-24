# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты поведения роутов api/, у которых ранее было 0 вызовов через тестовый клиент.

Проверяются 13 роутов:
1.  /api/ai_stop (POST)
2.  /api/ai_yellow (POST)
3.  /api/fonts (GET)
4.  /api/gen_subs (POST)
5.  /api/insertlib_desc (POST)
6.  /api/pickaudio (GET)
7.  /api/pickfiles (GET)
8.  /api/pickmedia (GET)
9.  /api/pickone (GET)
10. /api/savespeaker (POST)
11. /api/speakers (GET)
12. /api/styles (GET)
13. /api/words (POST)

Для каждого роута проверяется:
- Успешный ответ на валидном входе (формат ответа)
- Отказ на некорректном входе (код / err ровно как в коде роута)
- Побочные эффекты (запись файла, обновление индекса, смена состояния)
- Полная изоляция: tmp_path, заглушки вместо тяжелых библиотек (librosa, whisper, AE, внешняя сеть)
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core.fileio import atomic_json_dump  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client() -> Any:
    """Тестовый клиент Flask с зарегистрированным blueprint api."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Изоляция глобальных состояний и личных файлов пользователя."""
    from core import insertlib, speakers, styles

    speaker_dir = tmp_path / "speakers"
    speaker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(speaker_dir))

    styles_dir = tmp_path / "styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(styles, "STYLE_DIR", str(styles_dir))

    index_path = tmp_path / "insertlib.json"
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(index_path))

    # Сброс флага активности ИИ
    api._core.AI_ACTIVE = 0
    yield
    api._core.AI_ACTIVE = 0


# =========================================================================== #
# 1. POST /api/ai_stop (api/ai.py)
# =========================================================================== #
def test_ai_stop_success_calls_cancel(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный вызов /api/ai_stop вызывает aicut.cancel_call() и отдаёт ok: True."""
    from core import aicut

    cancelled = []
    monkeypatch.setattr(aicut, "cancel_call", lambda: cancelled.append(True) or 1)
    monkeypatch.setattr(aicut, "unload_ours", lambda: None)

    r = client.post("/api/ai_stop", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d == {"ok": True}
    assert len(cancelled) == 1


def test_ai_stop_failure_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """При исключении в cancel_call() роут возвращает ошибку ai_stop_failed."""
    from core import aicut

    def boom() -> None:
        raise RuntimeError("cancel_crash")

    monkeypatch.setattr(aicut, "cancel_call", boom)

    r = client.post("/api/ai_stop", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "ai_stop_failed"


# =========================================================================== #
# 2. POST /api/ai_yellow (api/ai.py)
# =========================================================================== #
def test_ai_yellow_rejects_missing_xml(client: Any, tmp_path: Path) -> None:
    """Несуществующий XML отбивается ошибкой file_not_found."""
    r = client.post("/api/ai_yellow", json={"xml": str(tmp_path / "missing.xml")}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "file_not_found"


def test_ai_yellow_success_returns_indices(client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Валидный XML вызывает aicut.cmd_yellow и возвращает yellow, colored, total."""
    from core import aicut

    xml_path = tmp_path / "seq.xml"
    xml_path.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")

    def fake_yellow(path: str, model: str | None = None, url: str | None = None, emit: Any = None) -> dict[str, Any]:
        return {"yellow": [1, 4], "colored": [1], "total": 10}

    monkeypatch.setattr(aicut, "cmd_yellow", fake_yellow)
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: None)

    r = client.post("/api/ai_yellow", json={"xml": str(xml_path), "model": "test-m"}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d == {"ok": True, "yellow": [1, 4], "colored": [1], "total": 10}
    assert api._core.AI_ACTIVE == 0


def test_ai_yellow_model_error_returns_err(client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ модели в aicut.cmd_yellow возвращает yellow_failed."""
    from core import aicut

    xml_path = tmp_path / "seq.xml"
    xml_path.write_text("<xmeml></xmeml>", encoding="utf-8")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("lm_studio_down")

    monkeypatch.setattr(aicut, "cmd_yellow", boom)
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: None)

    r = client.post("/api/ai_yellow", json={"xml": str(xml_path)}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "yellow_failed"
    assert api._core.AI_ACTIVE == 0


# =========================================================================== #
# 3. GET /api/fonts (api/files.py)
# =========================================================================== #
def test_fonts_success_returns_list(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный вызов отдаёт ok: True и список шрифтов из core.fonts.list_fonts()."""
    from core import fonts

    monkeypatch.setattr(fonts, "list_fonts", lambda: [{"ps": "ArialMT", "family": "Arial"}])
    r = client.get("/api/fonts", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "fonts": [{"ps": "ArialMT", "family": "Arial"}]}


def test_fonts_error_graceful_fallback(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """При сбое в list_fonts() отдаётся пустой список и note (мягкая деградация)."""
    from core import fonts

    def boom() -> None:
        raise RuntimeError("gdi_enum_failed")

    monkeypatch.setattr(fonts, "list_fonts", boom)
    r = client.get("/api/fonts", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["fonts"] == []
    assert "gdi_enum_failed" in d["note"]


# =========================================================================== #
# 4. POST /api/gen_subs (api/editor.py)
# =========================================================================== #
def test_gen_subs_rejects_missing_xml(client: Any, tmp_path: Path) -> None:
    """Несуществующий XML отбивается file_not_found."""
    r = client.post("/api/gen_subs", json={"xml": str(tmp_path / "none.xml")}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "file_not_found"


def test_gen_subs_success_creates_words_sidecar(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При успешной генерации субтитров создаётся <stem>.words.json и возвращаются subs/words."""
    import numpy as np
    import soundfile as sf
    from core import asr_backends, xml2ae, xmlbuild

    xml_file = tmp_path / "test_cut.xml"
    xml_file.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")
    proj_file = tmp_path / "test_cut.project.json"
    cam_file = tmp_path / "cam1.mp4"
    cam_file.write_bytes(b"\x00" * 100)

    proj_data = {
        "version": 1,
        "cams": [str(cam_file)],
        "keep": [[0.0, 1.0]],
        "offsets": [0.0],
    }
    atomic_json_dump(str(proj_file), proj_data)

    # librosa подменяется целиком модулем-пустышкой: на CI (Linux) пакет не установлен,
    # а monkeypatch.setattr("librosa.load", ...) требует существующий модуль.
    fake_librosa = types.ModuleType("librosa")
    fake_librosa.load = lambda path, sr=16000, mono=True: (np.zeros(16000), 16000)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setattr(sf, "write", lambda *a, **k: None)
    fake_words = [{"w": "Привет", "start": 0.1, "end": 0.5}]
    monkeypatch.setattr(asr_backends, "transcribe_words", lambda *a, **k: fake_words)
    monkeypatch.setattr(xmlbuild, "build", lambda *a, **k: {"subtitles": 1, "long_words": []})
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **k: None)

    r = client.post("/api/gen_subs", json={"xml": str(xml_file), "subengine": "whisper"}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d == {"ok": True, "subs": 1, "words": 1, "engine": "whisper", "skipped": []}

    # Побочный эффект: записан файл <stem>.words.json
    words_file = tmp_path / "test_cut.words.json"
    assert words_file.exists()
    assert json.loads(words_file.read_text(encoding="utf-8")) == fake_words


# =========================================================================== #
# 5. POST /api/insertlib_desc (api/inserts.py)
# =========================================================================== #
def test_insertlib_desc_rejects_missing_file_in_index(client: Any, tmp_path: Path) -> None:
    """Если файла нет в индексе, возвращается ошибка 'файла нет в индексе'."""
    idx_file = tmp_path / "insertlib.json"
    atomic_json_dump(str(idx_file), {"version": 1, "items": []})

    r = client.post("/api/insertlib_desc", json={"path": str(tmp_path / "img.png"), "desc": "новый"}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert "файла нет в индексе" in d.get("error", "")


def test_insertlib_desc_success_updates_index(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Успешное обновление описания изменяет desc и desc_src в индексе на диске."""
    from core import insertlib

    img_path = str(tmp_path / "pic.png")
    idx_file = tmp_path / "insertlib.json"
    initial_item = {
        "path": img_path,
        "name": "pic.png",
        "type": "photo",
        "used": 0,
        "desc": "старое описание",
        "desc_src": "name",
        "emb": None,
    }
    atomic_json_dump(str(idx_file), {"version": 1, "items": [initial_item], "emb_model": ""})
    monkeypatch.setattr(insertlib, "_emb_model", lambda: "")

    r = client.post("/api/insertlib_desc", json={"path": img_path, "desc": "ручное описание"}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d == {"ok": True, "desc": "ручное описание"}

    # Побочный эффект: в индексе сохранилось новое описание и desc_src == user
    reloaded = json.loads(idx_file.read_text(encoding="utf-8"))
    assert reloaded["items"][0]["desc"] == "ручное описание"
    assert reloaded["items"][0]["desc_src"] == "user"


# =========================================================================== #
# 6. GET /api/pickaudio (api/files.py)
# =========================================================================== #
def test_pickaudio_success_returns_path(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный выбор аудио через диалог возвращает path."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: "C:/music/sound.mp3")
    r = client.get("/api/pickaudio", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"path": "C:/music/sound.mp3"}


def test_pickaudio_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой диалога возвращает pickaudio_failed."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: (_ for _ in ()).throw(RuntimeError("dialog_boom")))
    r = client.get("/api/pickaudio", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "pickaudio_failed"


# =========================================================================== #
# 7. GET /api/pickfiles (api/files.py)
# =========================================================================== #
def test_pickfiles_success_returns_paths_list(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Мультивыбор файлов возвращает paths списком путей."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: "C:/1.xml|C:/2.xml")
    r = client.get("/api/pickfiles", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"paths": ["C:/1.xml", "C:/2.xml"]}


def test_pickfiles_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой диалога возвращает pickfiles_failed."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: (_ for _ in ()).throw(RuntimeError("dialog_boom")))
    r = client.get("/api/pickfiles", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "pickfiles_failed"


# =========================================================================== #
# 8. GET /api/pickmedia (api/files.py)
# =========================================================================== #
def test_pickmedia_success_returns_path(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Выбор медиафайла возвращает path."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: "C:/vids/clip.mov")
    r = client.get("/api/pickmedia", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"path": "C:/vids/clip.mov"}


def test_pickmedia_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой диалога возвращает pickmedia_failed."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: (_ for _ in ()).throw(RuntimeError("dialog_boom")))
    r = client.get("/api/pickmedia", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "pickmedia_failed"


# =========================================================================== #
# 9. GET /api/pickone (api/files.py)
# =========================================================================== #
def test_pickone_success_returns_path(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Одиночный выбор файла любого типа возвращает path."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: "C:/data/overlay.png")
    r = client.get("/api/pickone", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"path": "C:/data/overlay.png"}


def test_pickone_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой диалога возвращает pickone_failed."""
    monkeypatch.setattr("api.files._native_pick", lambda cmd: (_ for _ in ()).throw(RuntimeError("dialog_boom")))
    r = client.get("/api/pickone", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "pickone_failed"


# =========================================================================== #
# 10. POST /api/savespeaker (api/presets.py)
# =========================================================================== #
def test_savespeaker_rejects_empty_name(client: Any) -> None:
    """Пустое имя спикера отбивается need_speaker_name."""
    r = client.post("/api/savespeaker", json={"name": "", "data": {}}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "need_speaker_name"


def test_savespeaker_success_writes_profile(client: Any, tmp_path: Path) -> None:
    """Успешное сохранение записывает reelsi/speakers/<name>.json."""
    speaker_data = {"cut": {"onset_db": 24, "onset_fall": 25.0}}
    r = client.post("/api/savespeaker", json={"name": "Максим", "data": speaker_data}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is True
    assert d.get("key") == "Максим"

    # Побочный эффект: файл записан на диск
    saved_file = tmp_path / "speakers" / "Максим.json"
    assert saved_file.exists()
    content = json.loads(saved_file.read_text(encoding="utf-8"))
    assert content["cut"]["onset_db"] == 24


# =========================================================================== #
# 11. GET /api/speakers (api/presets.py)
# =========================================================================== #
def test_speakers_success_returns_profiles_and_defaults(client: Any, tmp_path: Path) -> None:
    """Успешный вызов возвращает профили спикеров, defaults и labels."""
    from core import speakers

    profile = tmp_path / "speakers" / "Спикер1.json"
    atomic_json_dump(str(profile), {"label": "Спикер 1", "cut": {"onset_db": 20}})

    r = client.get("/api/speakers", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert "Спикер1" in d["speakers"]
    assert d["defaults"] == speakers.CUT_DEFAULTS
    assert d["labels"] == [list(x) for x in speakers.CUT_LABELS]


def test_speakers_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка загрузки спикеров возвращает speakers_load_failed."""
    from core import speakers

    monkeypatch.setattr(speakers, "all_speakers", lambda: (_ for _ in ()).throw(RuntimeError("disk_err")))
    r = client.get("/api/speakers", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "speakers_load_failed"


# =========================================================================== #
# 12. GET /api/styles (api/presets.py)
# =========================================================================== #
def test_styles_success_returns_presets(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный вызов возвращает список стилей из core.styles.all_styles()."""
    from core import styles

    fake_styles = [{"name": "Default", "title": "По умолчанию"}]
    monkeypatch.setattr(styles, "all_styles", lambda: fake_styles)

    r = client.get("/api/styles", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "styles": fake_styles}


def test_styles_error_returns_err(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка загрузки стилей возвращает styles_load_failed."""
    from core import styles

    monkeypatch.setattr(styles, "all_styles", lambda: (_ for _ in ()).throw(RuntimeError("disk_err")))
    r = client.get("/api/styles", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "styles_load_failed"


# =========================================================================== #
# 13. POST /api/words (api/editor.py)
# =========================================================================== #
def test_words_rejects_missing_xml(client: Any, tmp_path: Path) -> None:
    """Несуществующий XML отбивается file_not_found."""
    r = client.post("/api/words", json={"xml": str(tmp_path / "missing.xml")}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "file_not_found"


def test_words_success_returns_ordered_words(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Успешный вызов возвращает fps, yellow, breaks и слова с индексами и секундами."""
    from core import xml2ae

    xml_file = tmp_path / "clip.xml"
    xml_file.write_text("<xmeml><sequence></sequence></xmeml>", encoding="utf-8")

    meta = {"fps": 25.0}
    subs = [(0, 25, "Первое"), (25, 50, "Второе")]
    monkeypatch.setattr(xml2ae, "parse_full", lambda path: (meta, ["cam1"], subs, []))
    monkeypatch.setattr(xml2ae, "auto_highlights", lambda path: {"yellow": [0], "breaks": [1]})

    r = client.post("/api/words", json={"xml": str(xml_file)}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["fps"] == 25.0
    assert d["yellow"] == [0]
    assert d["breaks"] == [1]
    assert d["words"] == [
        {"i": 0, "w": "Первое", "start": 0.0},
        {"i": 1, "w": "Второе", "start": 1.0},
    ]


def test_words_failure_returns_err(client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой парсинга XML возвращает words_failed."""
    from core import xml2ae

    xml_file = tmp_path / "clip.xml"
    xml_file.write_text("<xmeml></xmeml>", encoding="utf-8")

    monkeypatch.setattr(xml2ae, "parse_full", lambda path: (_ for _ in ()).throw(RuntimeError("xml_broken")))

    r = client.post("/api/words", json={"xml": str(xml_file)}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == "words_failed"
