# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задания GD: свои заголовки в профиле провайдера + кнопка «Дублировать»
и переключение типа поля для env-ключей.
"""
import json
import os
import sys
import urllib.request
import pytest
from flask import Flask

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut  # noqa: E402
import api  # noqa: E402
from core import app_meta  # noqa: E402


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    """Изолированный ai_config.json для тестов."""
    cfg_path = str(tmp_path / "ai_config.json")
    init_cfg = {
        "active": "test_prof",
        "profiles": {
            "test_prof": {
                "provider": "openai",
                "base_url": "http://localhost:1234/v1",
                "api_key": "secret123",
                "model": "gpt-4o",
            }
        },
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(init_cfg, f)
    monkeypatch.setattr(aicut.config, "AI_CONFIG_PATH", cfg_path)
    return cfg_path


class FakeStreamResp:
    """Фейковый ответ для стрим-вызова chat/completions."""

    def __init__(self, content='{"ok": true}'):
        delta = {"content": content}
        self._lines = [
            ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n").encode(),
            b"data: [DONE]\n",
        ]

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_apply_profile_headers_contract():
    """1. apply_profile_headers контракт:
    - без headers возвращает исходный набор;
    - с headers={"api-key": "x"} добавляет его;
    - с headers={"Authorization": "своё"} перебивает наш Bearer;
    - исходный словарь не мутирует.
    """
    base = {"Authorization": "Bearer k"}
    base_copy = dict(base)

    # Без headers
    res1 = aicut.apply_profile_headers(base, {})
    assert res1 == {"Authorization": "Bearer k"}
    assert base == base_copy
    assert res1 is not base

    # С headers={"api-key": "x"}
    res2 = aicut.apply_profile_headers(base, {"headers": {"api-key": "x"}})
    assert res2 == {"Authorization": "Bearer k", "api-key": "x"}
    assert base == base_copy

    # С headers={"Authorization": "своё"} перебивает наш Bearer
    res3 = aicut.apply_profile_headers(base, {"headers": {"Authorization": "своё"}})
    assert res3 == {"Authorization": "своё"}
    assert base == base_copy


def test_llm_chat_request_sends_custom_headers(monkeypatch):
    """2. Заголовок реально уходит в запрос: на моке urlopen в chat-вызове llm
    проверить, что api-key из профиля есть в заголовках запроса.
    """
    captured_req = []

    def mock_urlopen(req, timeout=600):
        captured_req.append(req)
        return FakeStreamResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    prof = {
        "provider": "openai",
        "base_url": "http://example.com/v1",
        "api_key": "orig_key",
        "model": "m1",
        "headers": {"api-key": "azure_secret", "X-Custom": "val"},
        "name": "azure",
    }
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    aicut._ask_openai(prof, "system prompt", "user prompt", schema, emit=lambda *a, **k: None)

    chat_reqs = [r for r in captured_req if "/chat/completions" in r.full_url]
    assert len(chat_reqs) == 1
    req = chat_reqs[0]

    # Проверяем наличие заголовков
    headers_dict = {k.lower(): v for k, v in req.headers.items()}
    assert headers_dict.get("api-key") == "azure_secret"
    assert headers_dict.get("x-custom") == "val"
    assert headers_dict.get("authorization") == "Bearer orig_key"


def test_env_var_in_headers_resolved_at_use_time(clean_config, client, monkeypatch):
    """3. Значение env:REELSI_TEST_HDR в заголовке разрешается из окружения
    (monkeypatch.setenv), а в ai_config.json после сохранения профиля лежит
    СЫРАЯ строка env:REELSI_TEST_HDR.
    """
    monkeypatch.setenv("REELSI_TEST_HDR", "super_env_secret_42")

    # Сохраняем профиль с заголовком env:REELSI_TEST_HDR через API
    r = client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "env_hdr_prof",
            "profile": {
                "provider": "openai",
                "base_url": "http://localhost:1234/v1",
                "api_key": "k",
                "headers_text": "api-key: env:REELSI_TEST_HDR\nX-Other: normal_val",
                "model": "gpt-4o",
            },
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("ok") is True

    # Проверяем, что в сыром файле лежит именно env:REELSI_TEST_HDR
    with open(clean_config, encoding="utf-8") as f:
        raw_cfg = json.load(f)
    assert raw_cfg["profiles"]["env_hdr_prof"]["headers"]["api-key"] == "env:REELSI_TEST_HDR"
    assert raw_cfg["profiles"]["env_hdr_prof"]["headers"]["X-Other"] == "normal_val"

    # При резолве через resolve_profile значение разрешается
    prof = aicut.resolve_profile(name="env_hdr_prof")
    assert prof["headers"]["api-key"] == "super_env_secret_42"
    assert prof["headers"]["X-Other"] == "normal_val"


def test_parse_headers_text_and_empty_handling(clean_config, client):
    """4. Разбор строки формы:
    "X-Foo: bar\\nмусор без двоеточия\\n\\n X-Baz :  qux " -> {"X-Foo": "bar", "X-Baz": "qux"}.
    Пустая строка -> у профиля нет ключа headers.
    """
    sample = "X-Foo: bar\nмусор без двоеточия\n\n X-Baz :  qux "
    parsed = aicut.parse_headers_text(sample)
    assert parsed == {"X-Foo": "bar", "X-Baz": "qux"}

    # Сохраняем с валидными строками
    client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "test_prof",
            "profile": {
                "provider": "openai",
                "base_url": "http://localhost:1234/v1",
                "api_key": "k",
                "headers_text": sample,
                "model": "gpt-4o",
            },
        },
    )
    with open(clean_config, encoding="utf-8") as f:
        raw_cfg = json.load(f)
    assert raw_cfg["profiles"]["test_prof"]["headers"] == {"X-Foo": "bar", "X-Baz": "qux"}

    # Теперь сохраняем с пустой строкой -> ключ headers удаляется
    client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "test_prof",
            "profile": {
                "provider": "openai",
                "base_url": "http://localhost:1234/v1",
                "api_key": "k",
                "headers_text": "   \n  \n",
                "model": "gpt-4o",
            },
        },
    )
    with open(clean_config, encoding="utf-8") as f:
        raw_cfg = json.load(f)
    assert "headers" not in raw_cfg["profiles"]["test_prof"]


def test_clone_profile_action(clean_config, client):
    """5. clone_profile: копия появилась со всеми полями оригинала, оригинал на месте,
    active не изменился; попытка склонировать в занятое имя даёт ошибку и конфиг не меняет.
    """
    # Подготовим профиль с заголовками
    client.post(
        "/api/ai_config",
        json={
            "action": "save_profile",
            "name": "original",
            "set_active": True,
            "profile": {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "sk-12345",
                "headers_text": "HTTP-Referer: custom\napi-key: secret",
                "model": "anthropic/claude-3.5-sonnet",
            },
        },
    )

    with open(clean_config, encoding="utf-8") as f:
        cfg_before = json.load(f)
    assert cfg_before["active"] == "original"

    # Успешное клонирование
    r = client.post(
        "/api/ai_config",
        json={"action": "clone_profile", "name": "original", "new_name": "original копия"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("ok") is True
    assert data.get("active") == "original"  # active не изменился

    with open(clean_config, encoding="utf-8") as f:
        cfg_after = json.load(f)

    assert "original" in cfg_after["profiles"]
    assert "original копия" in cfg_after["profiles"]
    assert cfg_after["profiles"]["original копия"]["api_key"] == "sk-12345"
    assert cfg_after["profiles"]["original копия"]["headers"] == {
        "HTTP-Referer": "custom",
        "api-key": "secret",
    }
    assert cfg_after["active"] == "original"

    # Попытка клонирования в уже занятое имя -> ошибка, конфиг не меняется
    r_err = client.post(
        "/api/ai_config",
        json={"action": "clone_profile", "name": "original", "new_name": "original копия"},
    )
    err_data = r_err.get_json()
    assert err_data.get("error") is True or err_data.get("ok") is None or "error" in err_data

    with open(clean_config, encoding="utf-8") as f:
        cfg_final = json.load(f)
    assert cfg_final == cfg_after


def test_front_env_key_type_switch_in_js_text():
    """Часть C: проверка по ТЕКСТУ сборки фронта (app_meta.app_js_text()),
    что переключение типа поля есть и что оно завязано именно на префикс env:.
    """
    js_text = app_meta.app_js_text()
    assert "startsWith('env:')" in js_text or 'startsWith("env:")' in js_text
    assert "type" in js_text
    assert "password" in js_text
    assert "text" in js_text
    assert "aiSyncKeyType" in js_text
