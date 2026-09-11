# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты контракта выгрузки моделей LM Studio (задание EC).

Reelsi выгружает ТОЛЬКО то, что загрузил сам (_OUR_MODELS), и не трогает сторонние
процессы/модели пользователя в LM Studio.
"""
import os
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut  # noqa: E402
from api._core import _ai_end  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_our_models(monkeypatch):
    """Каждый тест стартует с чистым реестром _OUR_MODELS."""
    with aicut.llm._OUR_MODELS_LOCK:
        aicut.llm._OUR_MODELS.clear()
    # Подменяем бинарник lms на фиктивный путь, чтобы _lms_bin() не возвращал None
    monkeypatch.setattr(aicut.llm, "_lms_bin", lambda: "fake_lms")
    yield
    with aicut.llm._OUR_MODELS_LOCK:
        aicut.llm._OUR_MODELS.clear()


def test_foreign_model_not_touched_when_registry_empty(monkeypatch):
    """1. Чужое не трогаем: реестр пуст, loaded_info() возвращает чужую модель —
    unload_ours() не должен вызвать subprocess.run НИ РАЗУ. Главный тест задания."""
    calls = []
    monkeypatch.setattr(aicut.llm.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(aicut.llm, "loaded_info", lambda url=None: [
        {"id": "qwen3.8-27b@iq2_xxs", "type": "llm", "state": "loaded"}
    ])

    assert len(aicut.our_loaded_models()) == 0
    aicut.unload_ours()
    assert len(calls) == 0, f"subprocess.run вызван {len(calls)} раз(а), ожидалось 0: {calls}"


def test_unload_ours_unloads_only_our_model(monkeypatch):
    """2. Своё выгружаем: после ensure_loaded("X") реестр содержит X;
    unload_ours() зовёт lms unload X и не зовёт --all."""
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class Dummy:
            returncode = 0
        return Dummy()

    monkeypatch.setattr(aicut.llm.subprocess, "run", fake_run)
    monkeypatch.setattr(aicut.llm, "loaded_models", lambda url=None: [])

    # Загружаем нашу модель X
    aicut.ensure_loaded("my_model_X")
    assert "my_model_X" in aicut.our_loaded_models()
    assert any(cmd[:2] == ["fake_lms", "load"] and "my_model_X" in cmd for cmd in calls)

    # Очищаем историю вызовов и делаем unload_ours
    calls.clear()
    aicut.unload_ours()

    # Должен быть вызов unload my_model_X, и НИКАКИХ --all
    assert len(calls) == 1
    assert calls[0] == ["fake_lms", "unload", "my_model_X"]
    assert "--all" not in calls[0]
    assert len(aicut.our_loaded_models()) == 0


def test_mixed_models_unloads_only_ours(monkeypatch):
    """3. Смешанный случай: наша X и чужая Y в LM Studio — выгружается только X."""
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class Dummy:
            returncode = 0
        return Dummy()

    monkeypatch.setattr(aicut.llm.subprocess, "run", fake_run)
    monkeypatch.setattr(aicut.llm, "loaded_models", lambda url=None: [])
    monkeypatch.setattr(aicut.llm, "loaded_info", lambda url=None: [
        {"id": "our_model_X", "type": "llm", "state": "loaded"},
        {"id": "foreign_model_Y", "type": "llm", "state": "loaded"},
    ])

    # Регистрируем нашу модель X через ensure_loaded
    aicut.ensure_loaded("our_model_X")
    assert aicut.our_loaded_models() == {"our_model_X"}

    calls.clear()
    aicut.unload_ours()

    # Выгрузилась только our_model_X
    assert calls == [["fake_lms", "unload", "our_model_X"]]
    assert len(aicut.our_loaded_models()) == 0


def test_cloud_operation_does_not_touch_lm_studio(monkeypatch):
    """4. Облачная работа: ничего не грузили, _ai_end(unload=True) —
    в LM Studio не ушло ни одной команды."""
    calls = []
    monkeypatch.setattr(aicut.llm.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(aicut.llm, "loaded_info", lambda url=None: [
        {"id": "user_qwen_model", "type": "llm", "state": "loaded"}
    ])

    # Имитируем завершение ИИ-шага в облаке
    ep = aicut.begin_call()
    _ai_end(ep, unload=True)

    assert len(calls) == 0, f"В LM Studio ушла команда при работе в облаке: {calls}"


def test_ensure_loaded_does_not_unload_all(monkeypatch):
    """ensure_loaded не зовёт unload --all, а сносит только наши прошлые модели."""
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class Dummy:
            returncode = 0
        return Dummy()

    monkeypatch.setattr(aicut.llm.subprocess, "run", fake_run)
    monkeypatch.setattr(aicut.llm, "loaded_models", lambda url=None: ["foreign_model", "old_our_model"])

    # Симулируем, что у нас была загружена old_our_model
    with aicut.llm._OUR_MODELS_LOCK:
        aicut.llm._OUR_MODELS.add("old_our_model")

    aicut.ensure_loaded("new_our_model")

    # Проверяем, что не было вызова unload --all
    for cmd in calls:
        assert "--all" not in cmd
    # Должен быть выгружен old_our_model, но НЕ foreign_model
    assert ["fake_lms", "unload", "old_our_model"] in calls
    assert ["fake_lms", "unload", "foreign_model"] not in calls
    assert "new_our_model" in aicut.our_loaded_models()


def test_warn_foreign_models(monkeypatch):
    """warn_foreign_models предупреждает о чужих моделях и игнорирует эмбеддеры и наши модели."""
    log = []

    def fake_emit(msg, **vars):
        log.append({"msg": msg, "vars": vars})

    monkeypatch.setattr(aicut.llm, "loaded_info", lambda url=None: [
        {"id": "our_model", "type": "llm", "state": "loaded"},
        {"id": "foreign_qwen", "type": "llm", "state": "loaded"},
        {"id": "text-embedding-nomic", "type": "embeddings", "state": "loaded"},
    ])

    with aicut.llm._OUR_MODELS_LOCK:
        aicut.llm._OUR_MODELS.add("our_model")

    aicut.warn_foreign_models(emit=fake_emit)

    # Должно быть ровно одно предупреждение — о foreign_qwen
    assert len(log) == 1
    assert log[0]["vars"].get("model") == "foreign_qwen"
    assert "LM Studio" in log[0]["msg"]
