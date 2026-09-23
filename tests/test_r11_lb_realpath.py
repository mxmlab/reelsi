# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты проверки настоящих путей (realpath) для денилиста секретов и allowlist расширений.

Проверяется инвариант:
Решение «отдать / не отдать / удалить» принимается и по присланному пути,
и по его os.path.realpath: секрет, если секрет хоть один из двух;
расширение допустимо, только если допустимо у обоих.
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

H = {"Host": "127.0.0.1:5001"}
FAKE_KEY = "sk-fake-secret-key-1234567890"


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _can_create_symlinks(tmp_path):
    """Проверить, разрешено ли создание симлинков в текущей ОС/окружении."""
    src = tmp_path / "_test_symlink_src"
    dst = tmp_path / "_test_symlink_dst"
    src.write_text("test", encoding="utf-8")
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
def symlink_maker(tmp_path):
    """Создаёт симлинк или пропускает тест, если нет прав."""
    if not _can_create_symlinks(tmp_path):
        pytest.skip("Создание симлинков не поддерживается или нет прав в окружении")

    def _make(target, link_path):
        try:
            os.symlink(target, link_path)
        except OSError as e:
            pytest.skip(f"Не удалось создать симлинк: {e}")
        return link_path

    return _make


@pytest.fixture
def fake_ai_config(tmp_path):
    """Фиктивный ai_config.json во tmp_path с тестовым ключом."""
    p = tmp_path / "ai_config.json"
    p.write_text(f'{{"api_key": "{FAKE_KEY}"}}', encoding="utf-8")
    return p


def test_export_xml_symlink_to_secret_rejected(client, tmp_path, symlink_maker, fake_ai_config):
    """export_xml по ссылке x.xml -> ai_config.json: 403, ключ не отдаётся."""
    x_xml = tmp_path / "x.xml"
    symlink_maker(fake_ai_config, x_xml)
    r = client.get(f"/api/export_xml?path={x_xml}", headers=H)
    assert r.status_code == 403
    assert FAKE_KEY not in r.get_data(as_text=True)


def test_export_xml_symlink_to_non_xml_target_rejected(client, tmp_path, symlink_maker):
    """export_xml по ссылке y.xml -> secret.txt: 403, цель без .xml."""
    secret = tmp_path / "secret.txt"
    secret.write_text("СЕКРЕТНЫЙ_ТЕКСТ", encoding="utf-8")
    y_xml = tmp_path / "y.xml"
    symlink_maker(secret, y_xml)
    r = client.get(f"/api/export_xml?path={y_xml}", headers=H)
    assert r.status_code == 403
    assert "СЕКРЕТНЫЙ_ТЕКСТ" not in r.get_data(as_text=True)


def test_media_symlink_to_secret_rejected(client, tmp_path, symlink_maker, fake_ai_config):
    """api/media по ссылке video.mp4 -> ai_config.json: 403, ключ не отдаётся."""
    video_mp4 = tmp_path / "video.mp4"
    symlink_maker(fake_ai_config, video_mp4)
    r = client.get(f"/api/media?path={video_mp4}", headers=H)
    assert r.status_code == 403
    assert FAKE_KEY not in r.get_data(as_text=True)


def test_media_symlink_to_non_media_target_rejected(client, tmp_path, symlink_maker):
    """api/media по ссылке clip.mp4 -> notes.txt: 403, цель без разрешённого расширения."""
    notes = tmp_path / "notes.txt"
    notes.write_text("СЕКРЕТНЫЕ_ЗАМЕТКИ", encoding="utf-8")
    clip_mp4 = tmp_path / "clip.mp4"
    symlink_maker(notes, clip_mp4)
    r = client.get(f"/api/media?path={clip_mp4}", headers=H)
    assert r.status_code == 403
    assert "СЕКРЕТНЫЕ_ЗАМЕТКИ" not in r.get_data(as_text=True)


def test_media_symlink_to_real_media_allowed(client, tmp_path, symlink_maker):
    """Легальная ссылка alias.mp4 -> real.mp4 отдаётся со статусом 200."""
    real_mp4 = tmp_path / "real.mp4"
    content = b"\x00\x00\x00\x18ftypmp42fake-video-payload"
    real_mp4.write_bytes(content)
    alias_mp4 = tmp_path / "alias.mp4"
    symlink_maker(real_mp4, alias_mp4)
    r = client.get(f"/api/media?path={alias_mp4}", headers=H)
    assert r.status_code == 200
    assert r.data == content


def test_never_serve_direct_call_on_symlink(tmp_path, symlink_maker, fake_ai_config):
    """Прямой вызов _never_serve на harmless.png -> ai_config.json возвращает True."""
    harmless_png = tmp_path / "harmless.png"
    symlink_maker(fake_ai_config, harmless_png)
    assert _never_serve(str(harmless_png)) is True
