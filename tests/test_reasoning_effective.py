# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Эффективный уровень «ума»: память на модель и понижение под каталог.

Диагноз (задание по UI-состояниям): у deepseek-v4-flash в каталоге efforts =
low/high/max, хранимый для шага «нарезка» medium. Было три разных ответа на один
вопрос: селект показывал off, сводка medium, а в API уходил low. Здесь стережётся
новый контракт: reasoning_by_model (память уровня НА МОДЕЛЬ) читается раньше
reasoning_steps, а интерфейсу отдаётся ТОЛЬКО effective_step_reasoning — результат
понижения по каталогу. Каталог и ai_config подменяются фикстурами: сеть и боевой
файл не трогаются.

Запуск:  python -m pytest tests -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.aicut import catalog as catalog  # noqa: E402
from core.aicut import config as config  # noqa: E402


# Записи каталога в формате models.dev api.json (пара из задания BY): у luna
# шесть усилий, у deepseek-v4-flash — low/high/max (medium в них нет).
FIXTURE = {
    "openrouter": {
        "id": "openrouter", "name": "OpenRouter",
        "models": {
            "deepseek/deepseek-v4-flash-0731": {
                "id": "deepseek/deepseek-v4-flash-0731", "reasoning": True,
                "reasoning_options": [{"type": "toggle"},
                                      {"type": "effort", "values": ["low", "high", "max"]}],
                "structured_output": True, "temperature": True,
                "limit": {"context": 1310720, "output": 393216},
                "cost": {"input": 0.14, "output": 0.28, "cache_read": 0.028}},
            "openai/gpt-5.6-luna": {
                "id": "openai/gpt-5.6-luna", "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": [
                    "none", "low", "medium", "high", "xhigh", "max"]}],
                "structured_output": True, "temperature": False,
                "limit": {"context": 1050000, "output": 128000},
                "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02}},
        },
    },
}

BASE_CFG = {
    "active": "ОР",
    "profiles": {
        "ОР": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
               "api_key": "k", "model": "deepseek/deepseek-v4-flash-0731"},
    },
}


@pytest.fixture(autouse=True)
def _catalog_fixture(monkeypatch, tmp_path):
    """Каталог из фикстуры вместо сети и кэша (как в test_catalog.py)."""
    monkeypatch.setattr(catalog, "CATALOG_URL", "file://no-network")
    monkeypatch.setattr(catalog, "catalog_cache_path",
                        lambda: str(tmp_path / "models_dev.json"))
    monkeypatch.setattr(catalog, "_CATALOG", FIXTURE)
    monkeypatch.setattr(catalog, "_CATALOG_TS", 9999999999.0)
    import urllib.request, urllib.error
    def fake(req, timeout=None):
        raise urllib.error.URLError("сеть в тестах запрещена")
    monkeypatch.setattr(urllib.request, "urlopen", fake)


@pytest.fixture(autouse=True)
def cfg_path(tmp_path, monkeypatch):
    """ai_config подменяется файлом во временной папке (боевой не трогаем)."""
    path = str(tmp_path / "ai_config.json")
    monkeypatch.setattr(config, "AI_CONFIG_PATH", path)
    monkeypatch.setattr(config, "_seed_ai_config", lambda: None)
    monkeypatch.setattr(config, "HERE", str(tmp_path))
    config.save_ai_config(json.loads(json.dumps(BASE_CFG)))
    return path


def _set_cut(cfg_path, lvl, model="deepseek/deepseek-v4-flash-0731", by_model=True):
    """reasoning_steps.cut + память на модель (как set_reasoning_step в api/ai.py)."""
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("reasoning_steps", {})["cut"] = lvl
    if by_model and model:
        cfg.setdefault("reasoning_by_model", {}).setdefault(model, {})["cut"] = lvl
    config.save_ai_config(cfg)


# ---- nearest_supported_level (перенос из llm.py в каталог) ----
def test_nearest_medium_downgraded_to_low():
    """medium при efforts low/high/max -> low (ближайший СНИЗУ, не выше)."""
    assert catalog.nearest_supported_level(
        "openrouter", "deepseek/deepseek-v4-flash-0731", "medium") == "low"


def test_nearest_off_stays_off():
    assert catalog.nearest_supported_level(
        "openrouter", "deepseek/deepseek-v4-flash-0731", "off") == "off"


def test_nearest_below_all_available_is_off():
    """Уровень ниже всех доступных (minimal при low/high/max) -> off."""
    assert catalog.nearest_supported_level(
        "openrouter", "deepseek/deepseek-v4-flash-0731", "minimal") == "off"


def test_nearest_unknown_model_unchanged():
    """Каталог молчит про модель (efforts пустые) — вход без изменений."""
    assert catalog.nearest_supported_level("lmstudio", "qwen3.6-27b", "medium") == "medium"


# ---- effective_step_reasoning на подставленном ai_config ----
def test_effective_medium_becomes_low(cfg_path):
    """Хранимое medium у deepseek-v4-flash -> эффективное low (как в API)."""
    _set_cut(cfg_path, "medium")
    assert config.effective_step_reasoning("cut") == "low"


def test_effective_valid_level_stays(cfg_path):
    """Валидный для модели уровень не трогаем: low остаётся low."""
    _set_cut(cfg_path, "low")
    assert config.effective_step_reasoning("cut") == "low"


def test_effective_unknown_model_returns_stored(cfg_path):
    """Каталог молчит про модель — эффективное == хранимое (как раньше)."""
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["profiles"]["ОР"]["model"] = "qwen3.6-27b-4bpw-16gb-vram"
    cfg.setdefault("reasoning_steps", {})["cut"] = "medium"
    config.save_ai_config(cfg)
    assert config.effective_step_reasoning("cut") == "medium"


# ---- step_reasoning: память уровня НА МОДЕЛЬ раньше reasoning_steps ----
def test_step_reasoning_prefers_reasoning_by_model(cfg_path):
    """reasoning_by_model[модель][шаг] читается раньше reasoning_steps.

    Сменил модель у шага — видишь уровень, который выбирал ДЛЯ НЕЁ, а не чужой
    из общего reasoning_steps. Здесь по модели лежит low, по шагу — high."""
    cfg = {
        "active": "ОР",
        "profiles": BASE_CFG["profiles"],
        "reasoning_steps": {"cut": "high"},
        "reasoning_by_model": {"deepseek/deepseek-v4-flash-0731": {"cut": "low"}},
    }
    config.save_ai_config(cfg)
    assert config.step_reasoning("cut") == "low"


def test_step_reasoning_by_model_invalid_for_model_skipped(cfg_path):
    """Значение из reasoning_by_model, невалидное для этой модели, пропускается
    (иначе снова «показываем одно, шлём другое»): medium для deepseek-v4-flash
    не принимаем, дальше по цепочке — reasoning_steps."""
    cfg = {
        "active": "ОР",
        "profiles": BASE_CFG["profiles"],
        "reasoning_steps": {"cut": "high"},
        "reasoning_by_model": {"deepseek/deepseek-v4-flash-0731": {"cut": "medium"}},
    }
    config.save_ai_config(cfg)
    assert config.step_reasoning("cut") == "high"


def test_step_reasoning_falls_back_to_steps(cfg_path):
    """Нет памяти на модель — читаем reasoning_steps, как раньше."""
    _set_cut(cfg_path, "high", by_model=False)
    assert config.step_reasoning("cut") == "high"


# ---- GET /api/ai_config: критерий готовности блока A ----
def test_ai_config_returns_reasoning_effective(cfg_path):
    """Критерий A: GET /api/ai_config отдаёт reasoning_effective.cut == "low"
    при модели deepseek-v4-flash и хранимом reasoning_steps.cut = medium."""
    import os
    os.environ.setdefault("REELSI_NO_BROWSER", "1")
    from flask import Flask
    import api
    _set_cut(cfg_path, "medium")
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    d = app.test_client().get("/api/ai_config").get_json()
    assert not d.get("error"), d
    assert d["reasoning_steps"]["cut"] == "medium"
    assert d["reasoning_effective"]["cut"] == "low"
