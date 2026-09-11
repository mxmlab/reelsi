# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт общего разрешения видео в /api/ai_config (задание EZB).

Разрешение — ОБЩАЯ настройка ai_config.video_resolution (в ⚙ «Разметка и AE»), как
модель. /api/ai_config GET и все успешные POST отдают video_resolution (резолвнутую
против caps). set_video_resolution: пусто можно, известная модель с непустыми caps и
неподдерживаемым значением — structured error БЕЗ записи. set_video_model атомарно
сбрасывает старое разрешение, если оно несовместимо с новыми известными caps.

Тесты идут на ВРЕМЕННОМ ai_config.json (боевой с ключами не читаем и не меняем).

Запуск:  python -m pytest reelsi/tests -q
"""
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core.aicut import config as config  # noqa: E402


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({"active": "t", "profiles": {
        "t": {"provider": "openrouter", "base_url": "https://example.test",
              "api_key": "", "model": "x"}}}), encoding="utf-8")
    monkeypatch.setattr(config, "AI_CONFIG_PATH", str(p))
    return p


@pytest.fixture
def client(cfg_file):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _read(p):
    return json.load(io.open(p, encoding="utf-8"))


def test_ai_config_get_returns_video_resolution(client, cfg_file):
    cfg = _read(cfg_file)
    cfg["video_resolution"] = "720p"
    cfg_file.write_text(json.dumps(cfg))
    d = client.get("/api/ai_config").get_json()
    assert d.get("video_resolution") == "720p"


def test_set_video_resolution_unsupported_is_error_no_save(client, cfg_file):
    """Известная модель (Kling — только 720p), неподдерживаемое 4K — отказ БЕЗ записи."""
    client.post("/api/ai_config", json={"action": "set_video_model",
                                        "model": "kwaivgi/kling-v3.0-pro"})
    d = client.post("/api/ai_config", json={"action": "set_video_resolution",
                                            "resolution": "4K"}).get_json()
    assert d.get("err") == "video_resolution_unsupported"
    assert _read(cfg_file).get("video_resolution", "") != "4K"


def test_set_video_resolution_supported_saves_canonical(client, cfg_file):
    client.post("/api/ai_config", json={"action": "set_video_model",
                                        "model": "kwaivgi/kling-v3.0-pro"})
    d = client.post("/api/ai_config", json={"action": "set_video_resolution",
                                            "resolution": "720p"}).get_json()
    assert d.get("ok") and d.get("video_resolution") == "720p"
    assert _read(cfg_file)["video_resolution"] == "720p"


def test_set_video_resolution_empty_is_allowed(client, cfg_file):
    client.post("/api/ai_config", json={"action": "set_video_model",
                                        "model": "kwaivgi/kling-v3.0-pro"})
    client.post("/api/ai_config", json={"action": "set_video_resolution",
                                        "resolution": "720p"})
    d = client.post("/api/ai_config", json={"action": "set_video_resolution",
                                            "resolution": ""}).get_json()
    assert d.get("ok") and d.get("video_resolution") == ""
    assert _read(cfg_file).get("video_resolution", "") == ""


def test_set_video_model_keeps_compatible_resolution(client, cfg_file):
    """Kling и Veo обе поддерживают 720p — смена модели его сохраняет."""
    client.post("/api/ai_config", json={"action": "set_video_model",
                                        "model": "kwaivgi/kling-v3.0-pro"})
    client.post("/api/ai_config", json={"action": "set_video_resolution",
                                        "resolution": "720p"})
    d = client.post("/api/ai_config", json={"action": "set_video_model",
                                            "model": "google/veo-3.1"}).get_json()
    assert d.get("ok") and d.get("video_resolution") == "720p"


def test_set_video_model_resets_incompatible_resolution(client, cfg_file):
    """Смена на Hailuo (только 2K) сбрасывает 720p на «по умолчанию»."""
    client.post("/api/ai_config", json={"action": "set_video_model",
                                        "model": "kwaivgi/kling-v3.0-pro"})
    client.post("/api/ai_config", json={"action": "set_video_resolution",
                                        "resolution": "720p"})
    d = client.post("/api/ai_config", json={"action": "set_video_model",
                                            "model": "minimax/hailuo-3"}).get_json()
    assert d.get("ok") and d.get("video_resolution") == ""
    assert _read(cfg_file).get("video_resolution", "") == ""
