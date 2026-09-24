# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты защиты сайдкаров рядом с присланным путём.

Производные пути (<stem>.project.json, .words.json, .cuts.json, .yellow.json,
.caption.json, .breaths.json, .omni.json) проверяются единой функцией sidecar_path
на принадлежность к денилисту секретов (_never_serve) и на ссылки (симлинк /
жёсткая ссылка), уводящие из каталога базового файла.

При обнаружении подмены роуты отказывают (ReelsiError), а файл-секрет не читается
и не перезаписывается. Обычные сайдкары работают как раньше.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api  # noqa: E402
from api._core import sidecar_path  # noqa: E402
import core.aicut.config  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

H = {"Host": "127.0.0.1:5001"}
FAKE_SECRET_KEY = "sk-fake-secret-key-sidecar-guard-12345"


@pytest.fixture
def client() -> FlaskClient:
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _make_hardlink(src: str | Path, dst: str | Path) -> None:
    """Создаёт жёсткую ссылку или пропускает тест, если ФС/ОС не поддерживает."""
    try:
        os.link(src, dst)
    except OSError as e:
        pytest.skip(f"Создание жёстких ссылок не поддерживается: {e}")


def _can_create_symlinks(tmp_path: Path) -> bool:
    """Проверить, разрешено ли создание симлинков в текущей ОС/окружении."""
    src = tmp_path / "_test_symlink_probe_src"
    dst = tmp_path / "_test_symlink_probe_dst"
    src.write_text("probe", encoding="utf-8")
    try:
        os.symlink(src, dst)
        return True
    except OSError:
        return False
    finally:
        try:
            dst.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        except Exception:
            pass


@pytest.fixture
def symlink_maker(tmp_path: Path) -> Any:
    """Создаёт симлинк или пропускает тест, если нет прав."""
    if not _can_create_symlinks(tmp_path):
        pytest.skip("Создание симлинков не поддерживается или нет прав в окружении")

    def _make(target: str | Path, link_path: str | Path) -> str | Path:
        try:
            os.symlink(target, link_path)
        except OSError as e:
            pytest.skip(f"Не удалось создать симлинк: {e}")
        return link_path

    return _make


@pytest.fixture
def sample_xml(tmp_path: Path) -> str:
    """Рабочий минимальный XML клипа в отдельной папке."""
    clip_dir = tmp_path / "project_clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    xml_path = clip_dir / "timeline.xml"
    src_fixture = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")
    if os.path.isfile(src_fixture):
        shutil.copy(src_fixture, str(xml_path))
    else:
        xml_path.write_text("<xmeml version='4'><project><name>test</name></project></xmeml>", encoding="utf-8")
    return str(xml_path)


def test_sidecar_path_direct_allowed(tmp_path: Path) -> None:
    """Обычный сайдкар строится корректно и возвращается без ошибок."""
    clip_xml = tmp_path / "clip.xml"
    clip_xml.write_text("<xml/>", encoding="utf-8")

    # Несуществующий сайдкар — путь возвращается
    p = sidecar_path(str(clip_xml), ".project.json")
    assert p == str(tmp_path / "clip.project.json")

    # Существующий обычный сайдкар в том же каталоге — возвращается
    Path(p).write_text("{}", encoding="utf-8")
    p2 = sidecar_path(str(clip_xml), ".project.json")
    assert p2 == p

    # Пустой base — пустая строка
    assert sidecar_path("", ".project.json") == ""


def test_sidecar_path_direct_rejects_secret_name(tmp_path: Path) -> None:
    """Имя сайдкара из списка секретов отклоняется."""
    base = tmp_path / "ai_config"
    with pytest.raises(ReelsiError) as exc_info:
        sidecar_path(str(base), ".json")
    assert exc_info.value.code == "forbidden_sidecar"


def test_sidecar_path_direct_rejects_hardlink_to_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Жёсткая ссылка сайдкара на ai_config.json отклоняется sidecar_path."""
    secret = tmp_path / "ai_config.json"
    secret.write_text(f'{{"api_key": "{FAKE_SECRET_KEY}"}}', encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))
    monkeypatch.setattr("core.paths.root", lambda name: str(secret) if name == "ai_config.json" else str(tmp_path / name))

    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    clip_xml = clip_dir / "clip.xml"
    clip_xml.write_text("<xml/>", encoding="utf-8")
    sidecar = clip_dir / "clip.project.json"
    _make_hardlink(secret, sidecar)

    with pytest.raises(ReelsiError) as exc_info:
        sidecar_path(str(clip_xml), ".project.json")
    assert exc_info.value.code == "forbidden_sidecar"


def test_sidecar_path_direct_rejects_symlink_outside(tmp_path: Path, symlink_maker: Any) -> None:
    """Симлинк сайдкара на файл вне каталога базового файла отклоняется."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "notes.txt"
    outside_file.write_text("секретные заметки", encoding="utf-8")

    clip_dir = tmp_path / "clips"
    clip_dir.mkdir()
    clip_xml = clip_dir / "clip.xml"
    clip_xml.write_text("<xml/>", encoding="utf-8")

    sidecar = clip_dir / "clip.project.json"
    symlink_maker(outside_file, sidecar)

    with pytest.raises(ReelsiError) as exc_info:
        sidecar_path(str(clip_xml), ".project.json")
    assert exc_info.value.code == "forbidden_sidecar"


def test_routes_reject_hardlink_sidecar_to_secret(
    client: FlaskClient,
    tmp_path: Path,
    sample_xml: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Роуты /api/export_drp, редактора и сборки отклоняют сайдкар-хардлинк на ai_config.json.

    Секрет не прочитан и не перезаписан (сверка содержимого и mtime).
    """
    secret = tmp_path / "ai_config.json"
    secret.write_text(f'{{"api_key": "{FAKE_SECRET_KEY}"}}', encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))
    monkeypatch.setattr("core.paths.root", lambda name: str(secret) if name == "ai_config.json" else str(tmp_path / name))

    sidecar = Path(sample_xml).with_suffix(".project.json")
    _make_hardlink(secret, sidecar)

    initial_content = secret.read_text(encoding="utf-8")
    initial_mtime = os.path.getmtime(secret)

    # 1. Роут экспорта: /api/export_drp
    r1 = client.post("/api/export_drp", json={"xml": sample_xml}, headers=H)
    d1 = r1.get_json() or {}
    assert r1.status_code == 403 or d1.get("ok") is not True or d1.get("err") == "forbidden_sidecar"
    assert FAKE_SECRET_KEY not in r1.get_data(as_text=True)

    # Секрет не пострадал
    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime

    # 2. Роут редактора: /api/editor_load
    r2 = client.post("/api/editor_load", json={"xml": sample_xml}, headers=H)
    d2 = r2.get_json() or {}
    assert d2.get("ok") is not True
    assert d2.get("err") == "forbidden_sidecar"
    assert FAKE_SECRET_KEY not in r2.get_data(as_text=True)

    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime

    # 3. Роут сборки: /api/cams_save
    r3 = client.post("/api/cams_save", json={"xml": sample_xml, "keep": [[0.0, 5.0]]}, headers=H)
    d3 = r3.get_json() or {}
    assert d3.get("ok") is not True
    assert d3.get("err") == "forbidden_sidecar"
    assert FAKE_SECRET_KEY not in r3.get_data(as_text=True)

    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime


def test_routes_reject_symlink_sidecar_to_secret(
    client: FlaskClient,
    tmp_path: Path,
    sample_xml: str,
    symlink_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Роуты отклоняют сайдкар-симлинк на ai_config.json; секрет не перезаписан."""
    secret = tmp_path / "ai_config.json"
    secret.write_text(f'{{"api_key": "{FAKE_SECRET_KEY}"}}', encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))
    monkeypatch.setattr("core.paths.root", lambda name: str(secret) if name == "ai_config.json" else str(tmp_path / name))

    sidecar = Path(sample_xml).with_suffix(".project.json")
    symlink_maker(secret, sidecar)

    initial_content = secret.read_text(encoding="utf-8")
    initial_mtime = os.path.getmtime(secret)

    # 1. /api/export_drp
    r1 = client.post("/api/export_drp", json={"xml": sample_xml}, headers=H)
    d1 = r1.get_json() or {}
    assert r1.status_code == 403 or d1.get("ok") is not True or d1.get("err") == "forbidden_sidecar"

    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime

    # 2. /api/editor_load
    r2 = client.post("/api/editor_load", json={"xml": sample_xml}, headers=H)
    d2 = r2.get_json() or {}
    assert d2.get("ok") is not True
    assert d2.get("err") == "forbidden_sidecar"

    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime

    # 3. /api/cams_save
    r3 = client.post("/api/cams_save", json={"xml": sample_xml, "keep": [[0.0, 5.0]]}, headers=H)
    d3 = r3.get_json() or {}
    assert d3.get("ok") is not True
    assert d3.get("err") == "forbidden_sidecar"

    assert secret.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(secret) == initial_mtime


def test_routes_reject_symlink_sidecar_outside_dir(
    client: FlaskClient,
    tmp_path: Path,
    sample_xml: str,
    symlink_maker: Any,
) -> None:
    """Сайдкар-симлинк на внешний файл notes.txt блокируется роутами."""
    notes = tmp_path / "notes.txt"
    notes.write_text("безобидный внешний файл", encoding="utf-8")
    initial_content = notes.read_text(encoding="utf-8")
    initial_mtime = os.path.getmtime(notes)

    sidecar = Path(sample_xml).with_suffix(".project.json")
    symlink_maker(notes, sidecar)

    r = client.post("/api/editor_load", json={"xml": sample_xml}, headers=H)
    d = r.get_json() or {}
    assert d.get("ok") is not True
    assert d.get("err") == "forbidden_sidecar"

    # Файл снаружи не затронут
    assert notes.read_text(encoding="utf-8") == initial_content
    assert os.path.getmtime(notes) == initial_mtime


def test_normal_sidecar_works_as_before(client: FlaskClient, sample_xml: str) -> None:
    """Легальный сайдкар рядом с XML штатно читается и обновляется роутами."""
    sidecar = Path(sample_xml).with_suffix(".project.json")
    proj_data = {
        "version": 1,
        "cams": ["cam1.mp4"],
        "offsets": [0.0],
        "fps": 60.0,
        "keep": [[0.0, 3.5]],
    }
    sidecar.write_text(json.dumps(proj_data), encoding="utf-8")

    r = client.post("/api/editor_load", json={"xml": sample_xml}, headers=H)
    d = r.get_json() or {}
    assert d.get("ok") is True
    assert d.get("cam") == "cam1.mp4"
    assert d.get("fps") == 60.0

    # Проверяем также чтение и запись подписи через /api/caption
    r_cap = client.post("/api/caption", json={"xml": sample_xml, "text": "Моя подпись"}, headers=H)
    assert r_cap.get_json() == {"ok": True, "text": "Моя подпись"}

    r_cap_read = client.post("/api/caption", json={"xml": sample_xml}, headers=H)
    assert r_cap_read.get_json() == {"ok": True, "text": "Моя подпись"}
