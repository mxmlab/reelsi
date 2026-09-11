# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт кнопки «Обновить список» — POST /api/ai_models.

Пойманный баг (2026-08-11, аудит чистки): из ответа убрали сборку списка
reasoning-моделей, а `reasoning_models=reasoning` в `jsonify` оставили. Роут падал
в `NameError`, его глотал общий `except Exception` — и кнопка отвечала не списком
моделей, а «NameError: name 'reasoning' is not defined». Тестов на роут не было,
482 зелёных теста этого не заметили; починено следующим коммитом.

Здесь стережётся сам КОНТРАКТ ответа: фронт (`static/app/10-settings.js`) читает
`models`, `reasoning_models`, `efforts`, `default_enabled` и `caps`, и молчаливая
потеря любого из полей ломает пометку «· reasoning» и фильтрацию селекта «Ум».
Источник правды по возможностям — каталог models.dev (aicut.catalog, задание BY):
в тесте он подменяется фикстурой, сеть не трогается.

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
from core import aicut  # noqa: E402


# Модели провайдера (список id приходит из {base}/models).
PROVIDER_MODELS = {"data": [
    {"id": "deepseek/deepseek-v4-flash"},
    {"id": "openai/o3"},
    {"id": "meta/llama-4"},
]}

# Возможности моделей из каталога models.dev (фикстура caps()).
EMPTY_CAPS = {"reasoning": None, "reasoning_kind": None, "efforts": None,
              "structured_output": None, "temperature": None,
              "out_limit": None, "ctx_limit": None, "cache": None,
              "default": None, "cost": None}


def _caps(provider, model, emit=None):
    m = (model or "").lower()
    if m == "deepseek/deepseek-v4-flash":
        return {"reasoning": True, "reasoning_kind": "effort",
                "efforts": ["high", "xhigh"], "structured_output": True,
                "temperature": True, "out_limit": 65536, "ctx_limit": 131072,
                "cache": True, "default": True,
                "cost": {"input": 0.1, "output": 0.2}}
    if m == "openai/o3":
        return {"reasoning": True, "reasoning_kind": "effort",
                "efforts": ["low", "medium", "high"], "structured_output": True,
                "temperature": False, "out_limit": 65536, "ctx_limit": 200000,
                "cache": False, "default": False,
                "cost": {"input": 2.0, "output": 8.0}}
    return dict(EMPTY_CAPS)


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    """Каталог моделей вместо сети. Список id провайдера — из {base}/models,
    возможности — из каталога models.dev (подменён). Каталоги картинок и видео
    (/images/models, /videos/models) отвечают 404 — роут обязан это пережить."""
    import urllib.error
    import urllib.request

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/models") and "/images/" not in url and "/videos/" not in url:
            return Resp(json.dumps(PROVIDER_MODELS).encode("utf-8"))
        raise urllib.error.HTTPError(url, 404, "no catalog", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(aicut.catalog, "caps", _caps)
    # кнопка «Обновить список» тянет каталог силой — в тесте не ходим в сеть
    monkeypatch.setattr(aicut.catalog, "ensure_catalog",
                        lambda emit=None, force=False: {"openrouter": {}})


def _ask(client):
    return client.post("/api/ai_models", json={
        "name": "test",
        "profile": {"provider": "openrouter", "base_url": "https://example.invalid/api/v1",
                    "api_key": "sk-test"},
    }).get_json()


def test_models_route_returns_reasoning_models(client, catalog):
    """Роут вообще отвечает, и в ответе есть все поля, которые читает фронт."""
    d = _ask(client)
    assert not d.get("error"), d                     # NameError сюда и приходил
    assert d["ok"] is True
    assert "meta/llama-4" in d["models"]
    # ключевое: список reasoning-моделей собран из каталога и отдан
    assert set(d["reasoning_models"]) == {"deepseek/deepseek-v4-flash", "openai/o3"}
    assert "meta/llama-4" not in d["reasoning_models"]


def test_models_route_reports_supported_efforts(client, catalog):
    """`efforts` — по нему фронт прячет уровни, которых у модели нет.

    На deepseek-v4-flash low/medium в каталоге отсутствуют, и отправленный low
    молча мапился в максимум, сжигая сотни тысяч токенов, — поэтому список
    уровней обязан доезжать до фронта дословно. Модель без efforts в каталог
    не попадает — не выдумываем.
    """
    d = _ask(client)
    assert d["efforts"]["deepseek/deepseek-v4-flash"] == ["high", "xhigh"]
    assert "meta/llama-4" not in d["efforts"]


def test_models_route_survives_missing_image_and_video_catalogs(client, catalog):
    """/images/models и /videos/models у провайдера может не быть — не 500 и не error."""
    d = _ask(client)
    assert d["ok"] is True and d["image_models"] == [] and d["video_models"] == []


def test_default_enabled_reaches_frontend(client, catalog):
    """`default_enabled` из каталога доезжает до фронта обоими путями (задания BW/BY).

    Модель, думающая по умолчанию (deepseek-v4-flash), обязана быть видна в селекте
    «Ум» как «Off — думает сама, выключаем явно»: иначе off выглядит как «ничего
    не делать». Второй путь — /api/ai_config (reasoning_defaults и model_caps):
    фронт берёт подпись оттуда при обычной загрузке страницы, до «Обновить список».
    """
    d = _ask(client)
    assert d["default_enabled"]["deepseek/deepseek-v4-flash"] is True
    # o3 в каталоге НЕ думает по умолчанию — в словаре честный False, а не отсутствие
    assert d["default_enabled"]["openai/o3"] is False
    assert "meta/llama-4" not in d["default_enabled"]  # нет в каталоге — не выдумываем
    # caps доезжают до фронта (контекст/вывод/цена рядом с моделью, задание BY)
    assert d["caps"]["deepseek/deepseek-v4-flash"]["ctx_limit"] == 131072
    assert d["caps"]["deepseek/deepseek-v4-flash"]["out_limit"] == 65536
