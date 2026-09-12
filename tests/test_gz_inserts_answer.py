# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт G: ответ модели проверяется до разбора.

При фолбэке «схема в промпте» (провайдер не умеет structured outputs и ответил 400)
модель кладёт в `inserts` что попало, включая строки. Дальше сразу шёл `it.get(...)` —
`AttributeError` после уже оплаченного вызова, шаг падал целиком.

Запуск:  python -m pytest tests -q
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# Пословная лента: 20 слов по секунде — тяжёлый разбор XML в тесте не нужен.
WORDS = [(k, f"слово{k}", float(k), k + 0.5) for k in range(20)]
ANSWER = {"analysis": "ок",
          "inserts": [
              "просто строка",                      # так отвечает модель без схемы
              None,                                  # и так тоже
              {"phrase": "слово8 слово9", "type": "photo", "start_sec": 8.0,
               "duration_sec": 2.5, "prompt": "упаковка таблеток", "query": "pills",
               "mosaic": False},
          ]}


@pytest.fixture
def stub(monkeypatch):
    from core.aicut import commands

    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(WORDS))
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: json.loads(json.dumps(ANSWER)))


def test_мусор_в_ответе_не_роняет_шаг(tmp_path, stub):
    """Строка и None в `inserts` отбрасываются, валидный элемент обрабатывается."""
    from core.aicut import commands

    out = tmp_path / "clip.xml"
    res = commands.cmd_inserts(str(out), emit=lambda *a, **k: None)

    assert [it["query"] for it in res["inserts"]] == ["pills"], res["inserts"]
    assert res["inserts"][0]["start_sec"] == 8.0, "тайминг не прижат к фразе"
    saved = json.loads((tmp_path / "clip.inserts.json").read_text(encoding="utf-8"))
    assert [it["query"] for it in saved["inserts"]] == ["pills"], "сайдкар не записан"


def test_пустой_ответ_даёт_пустой_набор(tmp_path, monkeypatch):
    """Ни одной валидной вставки — пустой набор, а не падение."""
    from core.aicut import commands

    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(WORDS))
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: {"inserts": ["мусор", None]})

    res = commands.cmd_inserts(str(tmp_path / "clip.xml"), emit=lambda *a, **k: None)

    assert res["inserts"] == []
