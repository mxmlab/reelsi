# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: ключ из переменной окружения (env:VAR) и нормализация Base URL.

1. resolve_key: значение переменной из окружения / пустая строка / обычный ключ как есть.
2. save_profile: в ai_config.json сохраняется сырая строка env:VAR, а не секрет.
3. resolve_profile: отдаёт разрешённый ключ из окружения.
4. GET /api/ai_config: env-ключ не маскируется + key_env_ok; обычный ключ маскируется без key_env_ok.
5. normalize_base_url: срезание хвостов /chat/completions, /models и др., без дописывания /v1.
6. api_ai_models: автопроба /v1 при 404 на {base}/models.

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

from core import aicut  # noqa: E402
from core.aicut import config as config  # noqa: E402
import api  # noqa: E402


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    p = tmp_path / "ai_config.json"
    init_cfg = {
        "active": "test_normal",
        "profiles": {
            "test_normal": {
                "provider": "openrouter",
                "base_url": "https://example.com/v1",
                "api_key": "sk-secret123456",
                "model": "qwen/qwen3",
            }
        },
    }
    p.write_text(json.dumps(init_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(config, "AI_CONFIG_PATH", str(p))
    return p


@pytest.fixture
def client(cfg_file):
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# 1. resolve_key
# --------------------------------------------------------------------------- #
def test_resolve_key(monkeypatch):
    """resolve_key: при заданной переменной отдаёт значение, при незаданной — '',
    обычный ключ возвращается как есть."""
    monkeypatch.setenv("REELSI_TEST_KEY", "secret-from-env-999")

    # Заданная переменная
    assert aicut.resolve_key("env:REELSI_TEST_KEY") == "secret-from-env-999"
    assert aicut.resolve_key("  env:REELSI_TEST_KEY  ") == "secret-from-env-999"

    # Незаданная переменная
    monkeypatch.delenv("REELSI_UNSET_VAR_XYZ", raising=False)
    assert aicut.resolve_key("env:REELSI_UNSET_VAR_XYZ") == ""

    # Обычный ключ
    assert aicut.resolve_key("sk-xxx-12345") == "sk-xxx-12345"
    assert aicut.resolve_key("") == ""
    assert aicut.resolve_key(None) is None


# --------------------------------------------------------------------------- #
# 2. Сохранение профиля через роут: в конфиге остаётся сырая строка env:...
# --------------------------------------------------------------------------- #
def test_save_profile_keeps_raw_env_key(client, cfg_file, monkeypatch):
    """При сохранении профиля с env:VAR в ai_config.json попадает строка env:VAR."""
    monkeypatch.setenv("REELSI_TEST_KEY", "super-secret-real-token")

    res = client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "EnvProfile",
            "profile": {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "env:REELSI_TEST_KEY",
                "model": "deepseek/deepseek-r1",
            },
        },
    ).get_json()

    assert res["ok"] is True

    # Читаем ai_config.json с диска напрямую
    disk_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    saved_key = disk_cfg["profiles"]["EnvProfile"]["api_key"]
    assert saved_key == "env:REELSI_TEST_KEY"
    assert "super-secret-real-token" not in cfg_file.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 3. resolve_profile отдаёт разрешённое значение
# --------------------------------------------------------------------------- #
def test_resolve_profile_resolves_env_key(client, cfg_file, monkeypatch):
    """resolve_profile(name=...) отдаёт РАЗРЕШЁННОЕ значение ключа."""
    monkeypatch.setenv("REELSI_TEST_KEY", "real-active-token")

    client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "EnvProfile",
            "profile": {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "env:REELSI_TEST_KEY",
                "model": "deepseek/deepseek-r1",
            },
        },
    )

    prof = aicut.resolve_profile(name="EnvProfile")
    assert prof["api_key"] == "real-active-token"


# --------------------------------------------------------------------------- #
# 4. GET /api/ai_config: env-ключ не замаскирован + key_env_ok
# --------------------------------------------------------------------------- #
def test_ai_config_get_env_key_and_key_env_ok(client, cfg_file, monkeypatch):
    """GET /api/ai_config: env-ключ отдаётся как есть и содержит key_env_ok (True/False);
    обычный ключ маскируется и key_env_ok у него отсутствует."""
    monkeypatch.setenv("REELSI_SET_VAR", "some_token")
    monkeypatch.delenv("REELSI_UNSET_VAR", raising=False)

    disk_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    disk_cfg["profiles"]["EnvSet"] = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "env:REELSI_SET_VAR",
        "model": "m1",
    }
    disk_cfg["profiles"]["EnvUnset"] = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "env:REELSI_UNSET_VAR",
        "model": "m2",
    }
    cfg_file.write_text(json.dumps(disk_cfg, ensure_ascii=False), encoding="utf-8")

    res = client.get("/api/ai_config").get_json()
    profs = res["profiles"]

    # Обычный ключ: замаскирован, key_env_ok нет
    assert profs["test_normal"]["api_key"].startswith("•••")
    assert "key_env_ok" not in profs["test_normal"]

    # env:REELSI_SET_VAR: не замаскирован, key_env_ok == True
    assert profs["EnvSet"]["api_key"] == "env:REELSI_SET_VAR"
    assert profs["EnvSet"]["key_env_ok"] is True

    # env:REELSI_UNSET_VAR: не замаскирован, key_env_ok == False
    assert profs["EnvUnset"]["api_key"] == "env:REELSI_UNSET_VAR"
    assert profs["EnvUnset"]["key_env_ok"] is False


# --------------------------------------------------------------------------- #
# 5. normalize_base_url
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "src, expected",
    [
        ("https://h/v1/chat/completions", "https://h/v1"),
        ("https://h/v1/", "https://h/v1"),
        ("https://h", "https://h"),
        ("https://h/v1/models", "https://h/v1"),
        ("https://h/v1/completions", "https://h/v1"),
        ("https://h/v1/responses", "https://h/v1"),
        ("https://h/v1/images", "https://h/v1"),
        ("https://h/v1/videos", "https://h/v1"),
        ("  http://localhost:1234/v1/chat/completions/  ", "http://localhost:1234/v1"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_base_url(src, expected):
    assert aicut.normalize_base_url(src) == expected


def test_save_profile_normalizes_base_url(client, cfg_file):
    """save_profile сохраняет нормализованный base_url."""
    res = client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "UrlTest",
            "profile": {
                "provider": "openai",
                "base_url": "https://api.myllm.com/v1/chat/completions",
                "api_key": "sk-xxx",
                "model": "custom-model",
            },
        },
    ).get_json()
    assert res["ok"] is True

    disk_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert disk_cfg["profiles"]["UrlTest"]["base_url"] == "https://api.myllm.com/v1"


# --------------------------------------------------------------------------- #
# 6. api_ai_models: автопроба /v1 при 404
# --------------------------------------------------------------------------- #
def test_api_ai_models_auto_probe_v1(client, monkeypatch):
    """api_ai_models: если {base}/models отвечает 404, а {base}/v1/models отдаёт список,
    роут успешно отвечает с ok=True, моделями и base_url с /v1."""
    import urllib.error
    import urllib.request

    requested_urls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        requested_urls.append(url)
        if url == "https://my-host.org/models":
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if url == "https://my-host.org/v1/models":
            return Resp(
                json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode("utf-8")
            )
        if "/images/" in url or "/videos/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        aicut.catalog, "ensure_catalog", lambda emit=None, force=False: {}
    )
    monkeypatch.setattr(aicut.catalog, "caps", lambda prov, mid: {})

    res = client.post(
        "/api/ai_models",
        json={
            "name": "ProbeTest",
            "profile": {
                "provider": "openai",
                "base_url": "https://my-host.org",
                "api_key": "sk-test",
            },
        },
    ).get_json()

    assert not res.get("error"), res
    assert res["ok"] is True
    assert res["models"] == ["model-a", "model-b"]
    assert res["base_url"] == "https://my-host.org/v1"
    assert "https://my-host.org/models" in requested_urls
    assert "https://my-host.org/v1/models" in requested_urls
