# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт: возможности провайдера определяются фактами, а не именем openrouter.

1. caps("openai", "deepseek/deepseek-v4-flash") -> reasoning is True, efforts непустой,
   ctx_limit == 1048576, cost is None, catalog_provider == "deepseek".
2. Регрессия: caps("openrouter", model) -> cost is not None, catalog_provider == "openrouter".
3. Неизвестная модель ("no-such-vendor/no-such-model") -> все поля None, catalog_provider is None.
4. gen_image: 3 ветки (в каталоге -> /images; пустой каталог + 404 -> chat fallback; в каталоге + 404 -> ReelsiError).
5. Роут /api/ai_models: provider="openai" отдаёт image_models, video_models, caps, reasoning_models.
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core.aicut import catalog as catalog
from core.aicut import images as images
import api
from core.umsg import ReelsiError


CATALOG_FIXTURE = {
    "anthropic": {
        "id": "anthropic", "name": "Anthropic",
        "models": {
            "claude-3-7-sonnet": {
                "id": "claude-3-7-sonnet",
                "reasoning": True,
                "reasoning_options": [{"type": "budget_tokens"}],
                "structured_output": True,
                "temperature": True,
                "limit": {"context": 200000, "output": 64000},
                "cost": {"input": 3.0, "output": 15.0}
            }
        }
    },
    "deepseek": {
        "id": "deepseek", "name": "DeepSeek",
        "models": {
            "deepseek-v4-flash": {
                "id": "deepseek-v4-flash",
                "reasoning": True,
                "reasoning_options": [
                    {"type": "toggle"},
                    {"type": "effort", "values": ["low", "high", "max"]}
                ],
                "structured_output": True,
                "temperature": True,
                "limit": {"context": 1048576, "output": 131072},
                "cost": {"input": 0.14, "output": 0.28, "cache_read": 0.028}
            }
        }
    },
    "openai": {
        "id": "openai", "name": "OpenAI",
        "models": {
            "gpt-5.6-luna": {
                "id": "gpt-5.6-luna",
                "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": ["none", "low", "medium", "high", "xhigh", "max"]}],
                "structured_output": True,
                "temperature": False,
                "limit": {"context": 1050000, "output": 128000},
                "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02}
            }
        }
    },
    "openrouter": {
        "id": "openrouter", "name": "OpenRouter",
        "models": {
            "gpt-5.6-luna": {
                "id": "gpt-5.6-luna",
                "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": ["none", "low", "medium", "high", "xhigh", "max"]}],
                "structured_output": True,
                "temperature": False,
                "limit": {"context": 1050000, "output": 128000},
                "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02}
            },
            "openai/gpt-5.6-luna": {
                "id": "openai/gpt-5.6-luna",
                "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": ["none", "low", "medium", "high", "xhigh", "max"]}],
                "structured_output": True,
                "temperature": False,
                "limit": {"context": 1050000, "output": 128000},
                "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02}
            },
            "meta/llama-4": {
                "id": "meta/llama-4",
                "reasoning": False,
                "temperature": True,
                "limit": {"context": 131072, "output": 8192},
                "cost": {"input": 0.1, "output": 0.2}
            }
        }
    },
    "thirdparty": {
        "id": "thirdparty", "name": "ThirdParty",
        "models": {
            "custom-thirdparty-model": {
                "id": "custom-thirdparty-model",
                "reasoning": True,
                "reasoning_options": [{"type": "toggle"}],
                "limit": {"context": 65536, "output": 4096},
                "cost": {"input": 0.5, "output": 1.0}
            }
        }
    }
}


@pytest.fixture(autouse=True)
def _catalog_fixture(monkeypatch, tmp_path):
    path = str(tmp_path / "models_dev.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(CATALOG_FIXTURE, f)
    monkeypatch.setattr(catalog, "CATALOG_URL", "https://models.dev/api.json")
    monkeypatch.setattr(catalog, "catalog_cache_path", lambda: path)
    monkeypatch.setattr(catalog, "_CATALOG", CATALOG_FIXTURE)
    monkeypatch.setattr(catalog, "_CATALOG_TS", 9999999999.0)


# ---- 1. Кросс-поиск через вендора --------------------------------------------

def test_cross_vendor_search_openai_provider():
    """caps('openai', 'deepseek/deepseek-v4-flash') находит модель у вендора deepseek,
    но cost возвращает None, а catalog_provider == 'deepseek'."""
    c = catalog.caps("openai", "deepseek/deepseek-v4-flash")
    assert c["reasoning"] is True
    assert c["efforts"] == ["low", "high", "max"]
    assert c["ctx_limit"] == 1048576
    assert c["out_limit"] == 131072
    assert c["cost"] is None               # чужая цена не показывается
    assert c["catalog_provider"] == "deepseek"
    assert c["cache"] is True              # cache_read из модели сохранился
    assert c["default"] is True            # toggle сохранился


def test_openai_provider_step_1b_matches_literal_provider():
    """caps('openai', 'gpt-5.6-luna') находит запись в openai (шаг 1b): efforts непустой,
    ctx_limit не None, cost is None (не вендор), catalog_provider == 'openai'."""
    c = catalog.caps("openai", "gpt-5.6-luna")
    assert c["efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert c["ctx_limit"] == 1050000
    assert c["cost"] is None
    assert c["catalog_provider"] == "openai"
    assert c["reasoning"] is True


def test_cross_vendor_search_openrouter_fallback():
    """caps('openai', 'meta/llama-4') находит модель под openrouter (шаг 3), cost is None."""
    c = catalog.caps("openai", "meta/llama-4")
    assert c["reasoning"] is False
    assert c["ctx_limit"] == 131072
    assert c["cost"] is None
    assert c["catalog_provider"] == "openrouter"


def test_cross_vendor_search_sorted_fallback():
    """caps('openai', 'custom-thirdparty-model') находит модель по перебору провайдеров (шаг 4)."""
    c = catalog.caps("openai", "custom-thirdparty-model")
    assert c["reasoning"] is True
    assert c["ctx_limit"] == 65536
    assert c["cost"] is None
    assert c["catalog_provider"] == "thirdparty"


def test_step_4_prefers_rich_entry_with_reasoning_options(monkeypatch):
    """Шаг 4 перебора по алфавиту предпочитает запись с reasoning_options перед бедной записью первого хостера."""
    custom_catalog = {
        "alpha_host": {
            "id": "alpha_host", "name": "Alpha Host",
            "models": {
                "rich-model-test": {
                    "id": "rich-model-test",
                    "reasoning": True,
                    "limit": {"context": 64000, "output": 4096},
                    "cost": {"input": 0.5, "output": 1.0}
                }
            }
        },
        "beta_host": {
            "id": "beta_host", "name": "Beta Host",
            "models": {
                "rich-model-test": {
                    "id": "rich-model-test",
                    "reasoning": True,
                    "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}],
                    "limit": {"context": 128000, "output": 8192},
                    "cost": {"input": 0.6, "output": 1.2}
                }
            }
        }
    }
    monkeypatch.setattr(catalog, "_CATALOG", custom_catalog)
    c = catalog.caps("unknown_provider", "rich-model-test")
    assert c["catalog_provider"] == "beta_host"
    assert c["efforts"] == ["low", "medium", "high"]
    assert c["ctx_limit"] == 128000
    assert c["cost"] is None


def test_step_4_fallback_to_first_alphabetical_when_no_reasoning_options(monkeypatch):
    """Шаг 4: если ни у кого нет reasoning_options, берётся первый по алфавиту."""
    custom_catalog = {
        "alpha_host": {
            "id": "alpha_host", "name": "Alpha Host",
            "models": {
                "plain-model-test": {
                    "id": "plain-model-test",
                    "limit": {"context": 64000, "output": 4096},
                }
            }
        },
        "beta_host": {
            "id": "beta_host", "name": "Beta Host",
            "models": {
                "plain-model-test": {
                    "id": "plain-model-test",
                    "limit": {"context": 128000, "output": 8192},
                }
            }
        }
    }
    monkeypatch.setattr(catalog, "_CATALOG", custom_catalog)
    c = catalog.caps("unknown_provider", "plain-model-test")
    assert c["catalog_provider"] == "alpha_host"
    assert c["ctx_limit"] == 64000
    assert c["cost"] is None


# ---- 2. Регрессия для прямого совпадения (шаг 1) ------------------------------

def test_openrouter_exact_match_returns_cost():
    """Для родного провайдера (шаг 1) cost возвращается как есть."""
    c = catalog.caps("openrouter", "openai/gpt-5.6-luna")
    assert c["reasoning"] is True
    assert c["efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert c["ctx_limit"] == 1050000
    assert c["cost"] == {"input": 0.2, "output": 1.2}
    assert c["catalog_provider"] == "openrouter"


def test_anthropic_exact_match_returns_cost():
    """Для провайдера anthropic (шаг 1 через CATALOG_KEY) cost возвращается и catalog_provider == 'anthropic'."""
    c = catalog.caps("anthropic", "claude-3-7-sonnet")
    assert c["catalog_provider"] == "anthropic"
    assert c["cost"] == {"input": 3.0, "output": 15.0}
    assert c["ctx_limit"] == 200000
    assert c["reasoning"] is True


# ---- 3. Совсем неизвестная модель --------------------------------------------

def test_completely_unknown_model_returns_all_none():
    """Неизвестная модель -> все поля None, catalog_provider is None."""
    c = catalog.caps("openai", "no-such-vendor/no-such-model")
    assert c["catalog_provider"] is None
    assert all(v is None for v in c.values())


# ---- 4. gen_image: три ветки -------------------------------------------------

class _DummyResp(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_gen_image_in_catalog_uses_images_api(monkeypatch):
    """Ветка 1: Модель в IMAGE_MODELS -> идёт в /images."""
    called_urls = []
    fake_png = b"\x89PNG\r\n\x1a\n"
    b64_png = base64.b64encode(fake_png).decode("utf-8")

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        called_urls.append(url)
        assert url == "https://api.test/v1/images"
        body = json.dumps({"data": [{"b64_json": b64_png}]}).encode("utf-8")
        return _DummyResp(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(images, "IMAGE_MODELS", {"flux-schnell": {}})

    prof = {"name": "img_prof", "provider": "openai", "base_url": "https://api.test/v1",
            "api_key": "k", "model": "flux-schnell"}
    res = images.gen_image("a red cat", prof=prof)
    assert res == fake_png
    assert called_urls == ["https://api.test/v1/images"]


def test_gen_image_empty_catalog_fallback_to_chat_on_404(monkeypatch):
    """Ветка 2: Пустой каталог + 404 от /images -> откат в /chat/completions."""
    called_urls = []
    fake_png = b"\x89PNG\r\n\x1a\n"
    data_url = "data:image/png;base64," + base64.b64encode(fake_png).decode("utf-8")

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        called_urls.append(url)
        if url.endswith("/images"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if url.endswith("/chat/completions"):
            body = json.dumps({
                "choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}]
            }).encode("utf-8")
            return _DummyResp(body)
        raise urllib.error.HTTPError(url, 500, "Unexpected", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(images, "IMAGE_MODELS", {})

    prof = {"name": "img_prof", "provider": "openai", "base_url": "https://api.test/v1",
            "api_key": "k", "model": "nano-banana"}
    res = images.gen_image("a red cat", prof=prof)
    assert res == fake_png
    assert called_urls == ["https://api.test/v1/images", "https://api.test/v1/chat/completions"]


def test_gen_image_in_catalog_raises_reelsierror_on_404(monkeypatch):
    """Ветка 3: Модель в IMAGE_MODELS + 404 от /images -> ReelsiError без chat fallback."""
    called_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        called_urls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(images, "IMAGE_MODELS", {"flux-schnell": {}})

    prof = {"name": "img_prof", "provider": "openai", "base_url": "https://api.test/v1",
            "api_key": "k", "model": "flux-schnell"}
    with pytest.raises(ReelsiError) as exc_info:
        images.gen_image("a red cat", prof=prof)

    assert "не найдена в Image API" in str(exc_info.value)
    assert "OpenRouter" not in str(exc_info.value)
    assert called_urls == ["https://api.test/v1/images"]


# ---- 5. Роут /api/ai_models для provider="openai" ----------------------------

@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_ai_models_openai_provider(client, monkeypatch):
    """Для provider='openai' роут возвращает image_models, video_models, reasoning_models и caps."""
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/images/models"):
            body = json.dumps({"data": [{"id": "flux-pro", "supported_parameters": {"output_format": ["png"]}}]})
        elif url.endswith("/videos/models"):
            body = json.dumps({"data": [{"id": "bytedance/seedance-2.0", "slug": "seedance-2.0"}]})
        elif url.endswith("/models"):
            body = json.dumps({"data": [{"id": "deepseek/deepseek-v4-flash"}]})
        else:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return _DummyResp(body.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    resp = client.post("/api/ai_models", json={
        "name": "custom_openai",
        "profile": {
            "provider": "openai",
            "base_url": "https://custom-ai.test/v1",
            "api_key": "sk-test"
        }
    })
    d = resp.get_json()
    assert d["ok"] is True
    assert "flux-pro" in d["image_models"]
    assert "bytedance/seedance-2.0" in d["video_models"]
    assert "deepseek/deepseek-v4-flash" in d["models"]
    assert "deepseek/deepseek-v4-flash" in d["reasoning_models"]
    assert d["efforts"].get("deepseek/deepseek-v4-flash") == ["low", "high", "max"]
    assert d["caps"]["deepseek/deepseek-v4-flash"]["ctx_limit"] == 1048576
    assert d["caps"]["deepseek/deepseek-v4-flash"]["cost"] is None
    assert d["caps"]["deepseek/deepseek-v4-flash"]["default"] is True
