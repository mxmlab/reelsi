# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты привязки каталога видео-моделей к провайдеру.

Инварианты:
- Каталог в памяти помечен ключом (provider, base_url без хвостового «/» в нижнем регистре).
- Объект словаря VIDEO_MODEL_CAPS один на процесс: очищается и наполняется, но не
  переприсваивается (идентичность с фасадом aicut.VIDEO_MODEL_CAPS не теряется).
- Смена профиля на другого провайдера приводит к перезапросу каталога; при сбое
  каталог очищается, а не остаётся старым.
- Предполёт и выпадашка UI смотрят живой каталог только при совпадении ключа с
  текущим профилем.
"""
import io
import json
import urllib.error
import urllib.request

import pytest

from core import aicut


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _reset_catalog():
    """Сбрасываем каталог и ключ до и после каждого теста."""
    aicut.set_video_catalog(None, {})
    yield
    aicut.set_video_catalog(None, {})


@pytest.fixture
def client():
    import webui
    return webui.app.test_client()


def test_catalog_key_normalization():
    """Ключ нормализуется: нижний регистр, без хвостовых слэшей, кортеж или профиль."""
    assert aicut.video._catalog_key(None) is None
    assert aicut.video._catalog_key({}) is None
    assert aicut.video._catalog_key({"provider": "", "base_url": ""}) is None

    key1 = aicut.video._catalog_key({
        "provider": "OpenRouter",
        "base_url": "https://OpenRouter.AI/api/v1///",
    })
    assert key1 == ("openrouter", "https://openrouter.ai/api/v1")

    key2 = aicut.video._catalog_key(("OpenRouter", "https://openrouter.ai/api/v1/"))
    assert key2 == ("openrouter", "https://openrouter.ai/api/v1")


def test_catalog_switch_between_providers(monkeypatch):
    """Каталог провайдера A (модель a/model), затем ensure_video_catalog с профилем B
    (другой base_url) и ответом каталога B (b/model): в каталоге только b/model, ключ B."""
    prof_a = {"provider": "openrouter", "base_url": "https://api.a.com/v1", "api_key": "k-a"}
    prof_b = {"provider": "openrouter", "base_url": "https://api.b.com/v1", "api_key": "k-b"}

    # Заполняем каталог провайдера A
    aicut.set_video_catalog(prof_a, [{"id": "a/model"}])
    assert aicut.video.VIDEO_CATALOG_KEY == ("openrouter", "https://api.a.com/v1")
    assert "a/model" in aicut.video.VIDEO_MODEL_CAPS

    # Мокаем ответ каталога для B
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        assert "api.b.com" in url
        data = {"data": [{"id": "b/model", "supported_durations": [5, 10]}]}
        return _Resp(json.dumps(data).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Запрашиваем каталог для профиля B
    aicut.ensure_video_catalog(prof_b)

    # В каталоге осталась только b/model и ключ B
    assert aicut.video.VIDEO_CATALOG_KEY == ("openrouter", "https://api.b.com/v1")
    assert list(aicut.video.VIDEO_MODEL_CAPS.keys()) == ["b/model"]
    assert "a/model" not in aicut.video.VIDEO_MODEL_CAPS


def test_preflight_does_not_reject_model_on_foreign_catalog(monkeypatch):
    """Предполёт модели b/model при старом каталоге A и профиле B не отказывает
    «модели нет у провайдера»."""
    prof_a = {"provider": "openrouter", "base_url": "https://api.a.com/v1", "api_key": "k-a"}
    prof_b = {"provider": "openrouter", "base_url": "https://api.b.com/v1", "api_key": "k-b"}

    # В памяти лежит каталог провайдера A
    aicut.set_video_catalog(prof_a, [{"id": "a/model"}])

    # Вызываем предполёт для b/model с профилем B (явно через аргумент prof)
    bad = aicut.video_check("b/model", {}, [], prompt="test", prof=prof_b)
    assert not any("не видео-модель" in b for b in bad)

    # Проверяем также через active profile (resolve_video_profile)
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: prof_b)
    bad_active = aicut.video_check("b/model", {}, [], prompt="test")
    assert not any("не видео-модель" in b for b in bad_active)

    # Напротив, если каталог совпадает с профилем B, незнакомая модель отклоняется
    aicut.set_video_catalog(prof_b, [{"id": "b/model"}])
    bad_unknown = aicut.video_check("c/unknown-model", {}, [], prompt="test", prof=prof_b)
    assert any("не видео-модель этого провайдера" in b for b in bad_unknown)


def test_dict_identity_preserved_across_set_and_routes(client, monkeypatch):
    """Идентичность объекта словаря aicut.VIDEO_MODEL_CAPS is aicut.video.VIDEO_MODEL_CAPS
    сохраняется до и после set_video_catalog, а также после вызова роутов из api/ai.py
    и api/videogen.py, заполняющих каталог."""
    assert aicut.VIDEO_MODEL_CAPS is aicut.video.VIDEO_MODEL_CAPS

    # 1. После прямого вызова set_video_catalog
    aicut.set_video_catalog(("openrouter", "https://api.test/v1"), [{"id": "m1"}])
    assert aicut.VIDEO_MODEL_CAPS is aicut.video.VIDEO_MODEL_CAPS
    assert "m1" in aicut.VIDEO_MODEL_CAPS

    # 2. После вызова роута /api/ai_models (api/ai.py)
    calls = []

    def fake_urlopen_ai(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if "/videos/models" in url:
            data = {"data": [{"id": "ai/video-mod"}]}
            return _Resp(json.dumps(data).encode("utf-8"))
        if "/images/models" in url:
            data = {"data": []}
            return _Resp(json.dumps(data).encode("utf-8"))
        # /models
        data = {"data": [{"id": "ai/chat-mod"}]}
        return _Resp(json.dumps(data).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_ai)
    monkeypatch.setattr(aicut.catalog, "ensure_catalog", lambda *a, **k: None)

    res_ai = client.post("/api/ai_models", json={
        "name": "test_ai",
        "profile": {
            "provider": "openrouter",
            "base_url": "https://api.ai-route.com/v1",
            "api_key": "k-ai",
        },
    }).get_json()
    assert res_ai.get("ok") is True
    assert aicut.VIDEO_MODEL_CAPS is aicut.video.VIDEO_MODEL_CAPS
    assert "ai/video-mod" in aicut.VIDEO_MODEL_CAPS
    assert aicut.video.VIDEO_CATALOG_KEY == ("openrouter", "https://api.ai-route.com/v1")

    # 3. После вызова роута /api/video_models (api/videogen.py)
    prof_vg = {
        "name": "test_vg",
        "provider": "openrouter",
        "base_url": "https://api.vg-route.com/v1",
        "api_key": "k-vg",
        "model": "vg/video-mod",
    }
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: prof_vg)

    def fake_urlopen_vg(req, timeout=None):
        data = {"data": [{"id": "vg/video-mod", "supported_durations": [4, 8]}]}
        return _Resp(json.dumps(data).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_vg)

    res_vg = client.post("/api/video_models", json={"model": "vg/video-mod"}).get_json()
    assert res_vg.get("ok") is True
    assert aicut.VIDEO_MODEL_CAPS is aicut.video.VIDEO_MODEL_CAPS
    assert "vg/video-mod" in aicut.VIDEO_MODEL_CAPS
    assert "ai/video-mod" not in aicut.VIDEO_MODEL_CAPS
    assert aicut.video.VIDEO_CATALOG_KEY == ("openrouter", "https://api.vg-route.com/v1")


def test_failed_catalog_request_clears_old_catalog(monkeypatch):
    """Сбой запроса каталога после смены профиля → каталог пуст, а не старый."""
    prof_a = {"provider": "openrouter", "base_url": "https://api.a.com/v1", "api_key": "k-a"}
    prof_b = {"provider": "openrouter", "base_url": "https://api.b.com/v1", "api_key": "k-b"}

    # Был загружен каталог A
    aicut.set_video_catalog(prof_a, [{"id": "a/model"}])
    assert "a/model" in aicut.video.VIDEO_MODEL_CAPS

    # При обращении к B происходит сбой сети (например, 500 или таймаут)
    def fake_failing_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_failing_urlopen)

    # ensure_video_catalog не падает, а очищает каталог
    aicut.ensure_video_catalog(prof_b)

    assert aicut.video.VIDEO_MODEL_CAPS == {}
    assert aicut.video.VIDEO_CATALOG_KEY is None


def test_catalog_early_exit_when_key_matches(monkeypatch):
    """ensure_video_catalog не идёт в сеть, если каталог уже загружен для этого же ключа."""
    prof = {"provider": "openrouter", "base_url": "https://api.cached.com/v1", "api_key": "k"}
    aicut.set_video_catalog(prof, [{"id": "cached/model"}])

    network_called = []

    def fake_urlopen(req, timeout=None):
        network_called.append(True)
        return _Resp(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    aicut.ensure_video_catalog(prof)
    assert len(network_called) == 0
    assert "cached/model" in aicut.video.VIDEO_MODEL_CAPS


def test_video_model_list_and_caps_ignore_foreign_catalog():
    """video_model_list и video_caps не подмешивают чужой каталог при несовпадении профиля."""
    prof_a = {"provider": "openrouter", "base_url": "https://api.a.com/v1", "api_key": "k-a"}
    prof_b = {"provider": "openrouter", "base_url": "https://api.b.com/v1", "api_key": "k-b"}

    # В каталоге модель провайдера A с кастомными ограничениями
    aicut.set_video_catalog(prof_a, [{
        "id": "custom/a-only",
        "supported_durations": [99],
    }])

    # Запрашиваем список и caps с профилем B
    lst = aicut.video_model_list(prof=prof_b)
    ids = [m["id"] for m in lst]
    assert "custom/a-only" not in ids

    # caps модели custom/a-only с профилем B вернёт None (чужой каталог скрыт)
    caps_b = aicut.video_caps("custom/a-only", prof=prof_b)
    assert caps_b is None

    # А с профилем A — вернёт данные из каталога
    caps_a = aicut.video_caps("custom/a-only", prof=prof_a)
    assert caps_a is not None and caps_a["durations"] == ["99"]


def test_video_catalog_key_not_reexported_on_facade():
    """VIDEO_CATALOG_KEY — переприсваиваемый глобал, на фасаде aicut его быть не должно."""
    assert not hasattr(aicut, "VIDEO_CATALOG_KEY")
    assert hasattr(aicut.video, "VIDEO_CATALOG_KEY")


def test_video_model_list_resolves_profile_once(monkeypatch):
    """video_model_list(prof=None) разрешает _active_video_profile один раз перед циклом,
    а не на каждой модели списка; при отсутствии профиля семантика сохраняется."""
    # 1. Профиль активен — ровно 1 вызов resolver'а
    prof = {"provider": "openrouter", "base_url": "https://api.active.com/v1"}
    calls_active = 0

    def counting_active():
        nonlocal calls_active
        calls_active += 1
        return prof

    monkeypatch.setattr(aicut.video, "_active_video_profile", counting_active)
    aicut.set_video_catalog(prof, [{"id": "active/model"}])
    lst_active = aicut.video_model_list()
    assert calls_active == 1
    assert "active/model" in [m["id"] for m in lst_active]

    # 2. Профиль отсутствует (None) — ровно 1 вызов resolver'а
    calls_none = 0

    def counting_none():
        nonlocal calls_none
        calls_none += 1
        return None

    monkeypatch.setattr(aicut.video, "_active_video_profile", counting_none)

    # 2a. Каталог с ключом (чужой) при отсутствии профиля игнорируется
    aicut.set_video_catalog(prof, [{"id": "foreign/model"}])
    calls_none = 0
    lst_none = aicut.video_model_list()
    assert calls_none == 1
    assert "foreign/model" not in [m["id"] for m in lst_none]

    # 2b. Каталог без ключа (ручной/тестовый) при отсутствии профиля доступен
    aicut.set_video_catalog(None, [{"id": "unkeyed/model"}])
    calls_none = 0
    lst_unkeyed = aicut.video_model_list()
    assert calls_none == 1
    assert "unkeyed/model" in [m["id"] for m in lst_unkeyed]

