# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт каталога возможностей моделей (aicut/catalog.py).

Каталог models.dev — единственный источник правды о том, что умеет модель:
reasoning (и чем управляется), structured_output, temperature, лимиты, цена.
Здесь стережётся caps() — разбор записи каталога в плоский словарь — и правила
«каталога нет -> кэш -> пусто» (слой не имеет права быть единственной точкой
отказа ИИ-шагов). Сеть из тестов не трогается: каталог подменяется фикстурой.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.aicut import catalog as catalog  # noqa: E402


# Записи каталога ровно в формате models.dev api.json — живая пара:
# у luna temperature=False и шесть уровней усилий, у deepseek — True и low/high/max.
FIXTURE = {
    "openrouter": {
        "id": "openrouter", "name": "OpenRouter",
        "models": {
            "openai/gpt-5.6-luna": {
                "id": "openai/gpt-5.6-luna", "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": [
                    "none", "low", "medium", "high", "xhigh", "max"]}],
                "structured_output": True, "temperature": False,
                "limit": {"context": 1050000, "output": 128000},
                "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02}},
            "deepseek/deepseek-v4-flash-0731": {
                "id": "deepseek/deepseek-v4-flash-0731", "reasoning": True,
                # toggle + effort: думает по умолчанию, уровней только три
                "reasoning_options": [{"type": "toggle"},
                                      {"type": "effort", "values": ["low", "high", "max"]}],
                "structured_output": True, "temperature": True,
                "limit": {"context": 1310720, "output": 393216},
                "cost": {"input": 0.14, "output": 0.28, "cache_read": 0.028}},
            # обычная модель — reasoning нет, options нет
            "meta/llama-4": {
                "id": "meta/llama-4", "reasoning": False,
                "temperature": True, "cost": {"input": 0.1, "output": 0.2}},
        },
    },
}


@pytest.fixture(autouse=True)
def _catalog_fixture(monkeypatch, tmp_path):
    """Каталог из фикстуры вместо сети и кэша: caps() читает его, не ходя в сеть."""
    monkeypatch.setattr(catalog, "CATALOG_URL", "file://no-network")
    monkeypatch.setattr(catalog, "catalog_cache_path",
                        lambda: str(tmp_path / "models_dev.json"))
    monkeypatch.setattr(catalog, "_CATALOG", None)
    monkeypatch.setattr(catalog, "_CATALOG_TS", 0.0)
    import urllib.request
    def fake(req, timeout=None):
        raise urllib.error.URLError("сеть в тестах запрещена")
    import urllib.error
    monkeypatch.setattr(urllib.request, "urlopen", fake)


@pytest.fixture
def loaded(monkeypatch, _catalog_fixture):
    """Каталог загружен в память (как после успешной подгрузки)."""
    monkeypatch.setattr(catalog, "_CATALOG", FIXTURE)
    monkeypatch.setattr(catalog, "_CATALOG_TS", 9999999999.0)


def test_luna_caps(loaded):
    """openai/gpt-5.6-luna: temperature=False, шесть усилий, out 128k."""
    c = catalog.caps("openrouter", "openai/gpt-5.6-luna")
    assert c["temperature"] is False
    assert c["efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert c["out_limit"] == 128000
    assert c["ctx_limit"] == 1050000
    assert c["reasoning_kind"] == "effort"
    assert c["default"] is False            # toggle нет — думать не по умолчанию
    assert c["cache"] is True               # cache_read в цене есть
    assert c["cost"] == {"input": 0.2, "output": 1.2}


def test_deepseek_caps(loaded):
    """deepseek-v4-flash-0731: temperature=True, усилия low/high/max, out 393k."""
    c = catalog.caps("openrouter", "deepseek/deepseek-v4-flash-0731")
    assert c["temperature"] is True
    assert c["efforts"] == ["low", "high", "max"]
    assert c["out_limit"] == 393216
    assert c["reasoning_kind"] == "effort"
    assert c["default"] is True             # toggle есть — думает по умолчанию


def test_plain_model_caps(loaded):
    """Модель без reasoning: поля честные (False/None), не выдумываем усилия."""
    c = catalog.caps("openrouter", "meta/llama-4")
    assert c["reasoning"] is False
    assert c["reasoning_kind"] is None
    assert c["efforts"] is None
    assert c["temperature"] is True


def test_unknown_model_returns_all_none(loaded):
    """Свой сервер / локальная сборка (нет в каталоге) — всё None, вызывающий
    ведёт себя как раньше."""
    c = catalog.caps("lmstudio", "qwen3.6-27b-4bpw-16gb-vram")
    assert all(v is None for v in c.values())


def test_unknown_provider_returns_all_none(loaded):
    c = catalog.caps("no-such-provider", "x")
    assert all(v is None for v in c.values())


def test_catalog_unavailable_no_cache(monkeypatch, _catalog_fixture):
    """Каталога нет и кэша нет: caps() возвращает всё None, а не падает.

    Слой не имеет права быть единственной точкой отказа ИИ-шагов: сеть недоступна
    и кэша нет — модели считаем «unknown» и работаем как раньше."""
    log = []
    c = catalog.caps("openrouter", "openai/gpt-5.6-luna",
                     emit=lambda *a, **k: log.append(" ".join(map(str, a))))
    assert all(v is None for v in c.values())
    assert any("каталог" in m and "кэша нет" in m for m in log), log


def test_catalog_unavailable_uses_disk_cache(monkeypatch, tmp_path, _catalog_fixture):
    """Сеть недоступна, но кэш с диска есть — работаем на нём и пишем строку в лог."""
    path = str(tmp_path / "models_dev.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(FIXTURE, f)
    # СТАРЫЙ кэш (старше TTL): первое обращение тянет сеть, сеть битая —
    # остаёмся на прошлом кэше и говорим об этом в лог.
    os.utime(path, (1000000.0, 1000000.0))
    log = []
    c = catalog.caps("openrouter", "deepseek/deepseek-v4-flash-0731",
                     emit=lambda *a, **k: log.append(" ".join(map(str, a))))
    assert c["efforts"] == ["low", "high", "max"]
    assert any("каталог" in m for m in log), log


def test_fresh_disk_cache_needs_no_network(monkeypatch, tmp_path, _catalog_fixture):
    """Свежий кэш (< TTL) не ходит в сеть и не пишет в лог: это обычная загрузка."""
    path = str(tmp_path / "models_dev.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(FIXTURE, f)
    os.utime(path, (9999999999.0, 9999999999.0))
    log = []
    c = catalog.caps("openrouter", "deepseek/deepseek-v4-flash-0731",
                     emit=lambda *a, **k: log.append(" ".join(map(str, a))))
    assert c["efforts"] == ["low", "high", "max"]
    assert not log, log


def test_valid_level_from_catalog(loaded):
    """Уровни «ума» принимаются из каталога (max/xhigh), а не только наши 4."""
    assert catalog.valid_level("openrouter", "deepseek/deepseek-v4-flash-0731", "max")
    assert catalog.valid_level("openrouter", "deepseek/deepseek-v4-flash-0731", "off")
    assert not catalog.valid_level("openrouter", "deepseek/deepseek-v4-flash-0731", "medium")


def test_valid_level_unknown_model(loaded):
    """Модели нет в каталоге — её уровни не валидируем (возврат False, кроме off)."""
    assert catalog.valid_level("lmstudio", "qwen3.6-27b-4bpw-16gb-vram", "off")
    assert not catalog.valid_level("lmstudio", "qwen3.6-27b-4bpw-16gb-vram", "high")
