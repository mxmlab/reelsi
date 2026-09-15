# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание HM, пункт 1: мусорный `drop` из ответа модели не роняет нарезку.

`core/omni_cut.py` разбирал ответ голым `int()`:
`{int(i) for i in data.get("drop", []) if int(i) in idxset}`. Свободный ответ
модели (фолбэк «схема в промпте» после 400) вида `{"drop": ["a"]}` или `[{}]`
бросал ValueError/TypeError уже ПОСЛЕ оплаченного вызова: джоб падал трейсбеком
целиком, а интервалы, которые модель просила выкинуть, оставались в ролике.
Для этого класса разбора в `core/aicut/commands.py:29` есть `as_ints()`.

Запуск:  python -m pytest tests -q
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# Тексты намеренно не похожи друг на друга: у decide() есть пост-проход keep-last
# (почти-дубли меняются местами) и гард хвостов, и на похожих фразах llm_drop
# поехал бы не из-за разбора ответа.
TEXTS = [{"start": 0.0, "end": 3.0, "text": "первый кусок про камеру"},
         {"start": 3.0, "end": 6.0, "text": "второй кусок про свет"},
         {"start": 6.0, "end": 9.0, "text": "третий кусок про звук"}]


def _drop(monkeypatch, answer):
    """decide() с подменённым ответом модели: что попало в llm_drop."""
    from core import aicut, omni_cut

    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: answer)
    # allow_long_drop=True — гард длинных интервалов выключен, и в llm_drop
    # остаётся ровно то, что вернула модель (иначе тест проверял бы гард).
    _, llm_drop, _notes, _tail = omni_cut.decide(TEXTS, emit=lambda *a, **k: None,
                                                 allow_long_drop=True)
    return llm_drop


@pytest.mark.parametrize("answer", [
    {"drop": ["a"]},                     # строка вместо индекса — ValueError на int()
    {"drop": [{}]},                      # объект вместо индекса — TypeError на int()
    {"drop": ["третье", None]},
    {"drop": None},                      # поля нет вовсе
])
def test_мусорный_drop_не_роняет_нарезку(monkeypatch, answer):
    """Раньше здесь был трейсбек ValueError/TypeError (int('a'), int({}))."""
    assert _drop(monkeypatch, answer) == set()


def test_валидные_индексы_разбираются_а_мусор_отбрасывается(monkeypatch):
    """Смешанный ответ: числа и строки-числа берём, чужое — мимо.
    99 вне idxset и −1 (отрицательный) в drop не попадают."""
    assert _drop(monkeypatch, {"drop": ["1", 2, None, {}, 99, -1]}) == {1, 2}
