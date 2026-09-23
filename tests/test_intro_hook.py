# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Хук ИИ-интро: строки по 1–2 слова и разбиение на прекомпы (2026-08-14).

Баги, ради которых тест: у intro_rows в схеме было только count и color — модель про
разбиение не спрашивали вообще, и хук уезжал в сборку ОДНИМ прекомпом; а строки
резались порогом 9 символов (INTRO_ROW_MAX_CHARS), хотя ручной эталон (38 .jsx)
даёт медиану строки 8 символов, p90 13, max 16.
"""
import gzip
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402


def _words(n=40, step=0.5):
    """Лента слов: слово каждые step секунд, длительность слова 0.4с."""
    return [(k, f"СЛОВО{k}", k * step, k * step + 0.4) for k in range(n)]


def _precomps(rows):
    """Лента строк -> список прекомпов (списки строк)."""
    out, cur = [], []
    for r in rows:
        if r["break"] and cur:
            out.append(cur)
            cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def test_split_words_не_оставляет_предлог_в_конце_куска():
    """Механический перенос не должен отрывать предлог от слова: «БОЛЬШИНСТВО НА КУРСЕ»
    с лимитом 14 режется «БОЛЬШИНСТВО» + «НА КУРСЕ», а не «БОЛЬШИНСТВО НА» + «КУРСЕ».
    Контрольный случай: «НАЧИНАЕМ С ПОЛОВИНЫ» старый алгоритм резал по середине и
    оставлял «С» последним в куске (2026-08-14)."""
    out = aicut._split_words(["БОЛЬШИНСТВО", "НА", "КУРСЕ"], limit=14)
    assert out == [["БОЛЬШИНСТВО"], ["НА", "КУРСЕ"]]
    out = aicut._split_words(["НАЧИНАЕМ", "С", "ПОЛОВИНЫ"], limit=14)
    assert out == [["НАЧИНАЕМ"], ["С", "ПОЛОВИНЫ"]]


def test_intro_defunc_не_оставляет_предлог_в_конце_строки():
    """Модель рвёт строку по 14 символам и оставляет предлог в конце («СТАВЯТ / ПО»).
    _split_words такие короткие строки не трогает — нужен _intro_defunc, который тянет
    слово из следующей строки назад (2026-08-14)."""
    rows = [{"count": 1, "color": "white", "break": True},
            {"count": 1, "color": "white", "break": False},
            {"count": 1, "color": "white", "break": False},
            {"count": 1, "color": "white", "break": False}]
    words = [(0, "СТАВЯТ", 0, .3), (1, "ПО", .4, .6), (2, "1500", .7, .9),
             (3, "2000", 1.0, 1.2)]
    out = aicut._intro_defunc(rows, words)
    assert [r["count"] for r in out] == [1, 2, 1]
    k, merged = 0, []
    for r in out:
        merged.append([w[1] for w in words[k:k + r["count"]]])
        k += r["count"]
    assert merged == [["СТАВЯТ"], ["ПО", "1500"], ["2000"]]


def test_хук_не_режет_короткую_строку_и_длинное_слово():
    """Порог 9 резал «привет мир» (10 символов) пополам; одно длинное слово юзер
    кладёт целиком — лимит хука 14 это обещает."""
    words = [(0, "привет", 0, 0.3), (1, "мир", 0.4, 0.7)]
    rows = aicut._wrap_intro_rows([{"count": 2, "color": "white", "break": True}], words)
    assert rows == [{"count": 2, "color": "white", "break": True}]
    long = [(0, "супердлинноеслово16", 0, 0.5)]
    assert aicut._wrap_intro_rows(
        [{"count": 1, "color": "white", "break": True}], long) == \
        [{"count": 1, "color": "white", "break": True}]


def test_break_переживает_перенос():
    """Продолжение перенесённой строки получает break=False (контракт _place_mids)."""
    words = [(0, "супердлинноеслово", 0, 0.3), (1, "тоже", 0.4, 0.6),
             (2, "очень", 0.7, 0.9), (3, "длинное", 1.0, 1.2)]
    rows = aicut._wrap_intro_rows(
        [{"count": 4, "color": "white", "break": True}], words, limit=9)
    assert rows[0]["break"] is True
    assert all(not r["break"] for r in rows[1:])
    assert sum(r["count"] for r in rows) == 4


def test_хук_без_break_разбивается_на_прекомпы():
    """Модель разбиение не прислала (только голова хука) — страховка ставит 3–5
    прекомпов, ни одного больше 3 строк."""
    rows = [{"count": 1, "color": "white", "break": (i == 0)} for i in range(14)]
    out = aicut._hook_breaks(rows, _words(14))
    precomps = _precomps(out)
    assert 3 <= len(precomps) <= 5
    assert all(len(p) <= 3 for p in precomps)


def test_хук_не_переигрывает_модель():
    """Пришли 4 головы — осталось 4; прекомп из 6 строк дорезан, мелкие не тронуты."""
    rows = [{"count": 2, "color": "white", "break": True},
            {"count": 1, "color": "white", "break": False},
            {"count": 1, "color": "white", "break": True},
            {"count": 1, "color": "white", "break": False},
            {"count": 2, "color": "white", "break": True},
            {"count": 1, "color": "white", "break": False},
            {"count": 1, "color": "white", "break": True},
            {"count": 1, "color": "white", "break": False}]
    out = aicut._hook_breaks(rows, _words(10))
    assert sum(1 for r in out if r["break"]) == 4
    big = [{"count": 1, "color": "white", "break": True}] \
        + [{"count": 1, "color": "white", "break": False}] * 5 \
        + [{"count": 1, "color": "white", "break": True},
           {"count": 1, "color": "white", "break": False}]
    out = aicut._hook_breaks(big, _words(8))
    assert [len(p) for p in _precomps(out)] == [3, 3, 2]


@pytest.fixture
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
         open(dst, "wb") as f:
        f.write(g.read())
    return dst


def test_cmd_intro_целиком(xml_subs, tmp_path, monkeypatch):
    """cmd_intro с подменённым _ask_json: в .intro.json у строк хука есть break,
    прекомпов хука ≥ 2, сумма count не изменилась."""
    model_rows = [{"count": 1, "color": "white"} for _ in range(6)]
    monkeypatch.setattr(
        aicut.commands, "_ask_json",
        lambda *a, **k: {"intro_rows": model_rows,
                         "mid_groups": [{"from": 100, "count": 2, "color": "white"},
                                        {"from": 120, "count": 1, "color": "yellow"}]})
    res = aicut.cmd_intro(xml_subs, emit=lambda *a, **k: None)
    rows = res["intro_rows"]
    assert all("break" in r for r in rows)
    assert sum(1 for r in rows if r["break"]) >= 2
    assert sum(r["count"] for r in rows) == 6
    saved = json.load(open(str(tmp_path / "timeline.intro.json"), encoding="utf-8"))
    assert saved["intro_rows"] == rows
