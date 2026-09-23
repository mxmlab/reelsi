# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт сводки ИИ-вызовов — GET /api/ai_stats.

Сводка «кто сколько думает» в модалке настроек ИИ. До неё догадка о том, кто жжёт
бюджет на размышления, была перевёрнутой: дипсик на off думал 0 секунд (он просто
медленно генерирует), а luna на low — от 21% до 90% вывода. Медианы, а не средние:
один сорвавшийся вызов на 200k токенов перекашивает среднее.

Запуск:  python -m pytest reelsi/tests -q
"""
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


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _log_tmp(tmp_path, monkeypatch):
    """Сводка читает ai_calls.jsonl — пишем в тестовую папку, не в боевой.

    И роут (api/ai.py), и записи (llm.py) берут путь из config.AI_LOG_PATH —
    патчим его, а не отдельный модуль, иначе сводка прочитает боевой лог."""
    p = str(tmp_path / "ai_calls.jsonl")
    # Роут читает aicut.AI_LOG_PATH (переэкспорт из config, связан при импорте) —
    # патчим его же, иначе сводка прочитает боевой лог.
    monkeypatch.setattr(aicut, "AI_LOG_PATH", p)
    return p


def _write_log(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_stats_empty_when_no_log(client, _log_tmp):
    """Файла лога нет — пустая сводка, а не ошибка."""
    d = client.get("/api/ai_stats").get_json()
    assert d["ok"] is True and d["groups"] == []


def test_stats_groups_and_medians(client, _log_tmp):
    """Группировка по (модель, шаг, ум); токены и время — МЕДИАНЫ."""
    _write_log(_log_tmp, [
        {"model": "m/1", "step": "inserts", "reasoning": "low", "ok": True,
         "in": 100, "out": 200, "rt": 50, "ms": 1000},
        {"model": "m/1", "step": "inserts", "reasoning": "low", "ok": True,
         "in": 900, "out": 1000, "rt": 300, "ms": 3000},
        # другой уровень — отдельная строка
        {"model": "m/1", "step": "inserts", "reasoning": "off", "ok": True,
         "in": 10, "out": 20, "rt": 0, "ms": 500},
        # стартовая запись (ok=None) и ошибка — не портят медианы
        {"model": "m/1", "step": "inserts", "reasoning": "low", "ok": None,
         "in": 0, "out": 0, "rt": 0, "ms": 0},
        {"model": "m/1", "step": "inserts", "reasoning": "low", "ok": False,
         "err": "обрыв"},
    ])
    d = client.get("/api/ai_stats").get_json()
    by = {(g["model"], g["step"], g["lvl"]): g for g in d["groups"]}
    g = by[("m/1", "inserts", "low")]
    assert g["n"] == 2                        # ok=None и ошибка в медианы не вошли
    assert g["in"] == 500 and g["out"] == 600 and g["rt"] == 175
    assert g["rtpct"] == 29                    # 175 / 600 * 100 округлено
    assert g["sec"] == 2.0                     # медиана 1000/3000
    assert g["err"] == 1                       # ошибки считаются отдельно
    assert by[("m/1", "inserts", "off")]["n"] == 1
