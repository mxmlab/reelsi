# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты защиты от отдачи секретов через жёсткие ссылки.

Жёсткая ссылка (os.link) оставляет безобидное имя файла и указывает напрямую на
тот же inode/file index ФС, поэтому os.path.realpath не раскрывает исходный путь.
Проверяется инвариант: _never_serve и /api/media отклоняют файлы, связанные по
samefile с известными секретами (AI_CONFIG_PATH, ai_config.json, rclone.conf).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api  # noqa: E402
from api._core import _never_serve  # noqa: E402
import core.aicut.config  # noqa: E402

H = {"Host": "127.0.0.1:5001"}
FAKE_KEY = "sk-fake-secret-key-hardlink-9876543210"
FAKE_VIDEO_PAYLOAD = b"\x00\x00\x00\x18ftypmp42fake-video-payload"


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _make_hardlink(src, dst):
    """Создаёт жёсткую ссылку или пропускает тест, если ФС/ОС не поддерживает."""
    try:
        os.link(src, dst)
    except OSError as e:
        pytest.skip(f"Создание жёстких ссылок не поддерживается или нет прав: {e}")


def test_media_hardlink_to_ai_config_rejected(client, tmp_path, monkeypatch):
    """Секрет во tmp_path, AI_CONFIG_PATH подменён; os.link(secret, clip.mp4) -> 403, _never_serve -> True."""
    secret = tmp_path / "secret_ai_config.json"
    secret.write_text(f'{{"api_key": "{FAKE_KEY}"}}', encoding="utf-8")
    monkeypatch.setattr(core.aicut.config, "AI_CONFIG_PATH", str(secret))

    clip_mp4 = tmp_path / "clip.mp4"
    _make_hardlink(secret, clip_mp4)

    assert _never_serve(str(clip_mp4)) is True

    r = client.get(f"/api/media?path={clip_mp4}", headers=H)
    assert r.status_code == 403
    assert FAKE_KEY not in r.get_data(as_text=True)


def test_media_normal_video_file_allowed(client, tmp_path):
    """Обычный видеофайл отдаётся со статусом 200, _never_serve -> False."""
    clip_mp4 = tmp_path / "normal_video.mp4"
    clip_mp4.write_bytes(FAKE_VIDEO_PAYLOAD)

    assert _never_serve(str(clip_mp4)) is False

    r = client.get(f"/api/media?path={clip_mp4}", headers=H)
    assert r.status_code == 200
    assert r.data == FAKE_VIDEO_PAYLOAD


def test_media_hardlink_to_root_ai_config_rejected(client, tmp_path, monkeypatch):
    """Жёсткая ссылка на ai_config.json из paths.root блокируется."""
    secret = tmp_path / "root_ai_config.json"
    secret.write_text(f'{{"api_key": "{FAKE_KEY}"}}', encoding="utf-8")
    monkeypatch.setattr("core.paths.root", lambda name: str(secret) if name == "ai_config.json" else str(tmp_path / name))

    clip_mp4 = tmp_path / "clip_root.mp4"
    _make_hardlink(secret, clip_mp4)

    assert _never_serve(str(clip_mp4)) is True

    r = client.get(f"/api/media?path={clip_mp4}", headers=H)
    assert r.status_code == 403
    assert FAKE_KEY not in r.get_data(as_text=True)


def test_media_hardlink_to_rclone_conf_rejected(client, tmp_path, monkeypatch):
    """Жёсткая ссылка на rclone.conf блокируется."""
    secret = tmp_path / "custom_rclone.conf"
    secret.write_text("token = fake_token", encoding="utf-8")
    monkeypatch.setattr("core.rclone.rclone_conf", lambda: str(secret))

    clip_mp4 = tmp_path / "clip_rc.mp4"
    _make_hardlink(secret, clip_mp4)

    assert _never_serve(str(clip_mp4)) is True

    r = client.get(f"/api/media?path={clip_mp4}", headers=H)
    assert r.status_code == 403


def test_never_serve_oserror_handled(tmp_path):
    """Несуществующий или недоступный путь не вызывает падения _never_serve."""
    non_existent = tmp_path / "does_not_exist.mp4"
    assert _never_serve(str(non_existent)) is False
