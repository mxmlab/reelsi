# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ИИ-вставки видят слова без цензурных звёздочек.

Субтитры несут цензуру («BPC-*57», «Т*500», «ПЕП*ИДЫ»), а исходная расшифровка лежит рядом с XML
в `<stem>.words.json` (тот же таймлайн, секунды). `_words_for_ai` снимает «*» по сайдкару: модель
и проход по словарю названий получают настоящее слово. Субтитры при этом не меняются.

Тесты подменяют `_words_from_xml` и кладут сайдкар во временную папку: ни настоящих XML, ни
вызовов ИИ, ни сети.

Запуск: py -3.10 -m pytest tests/test_inserts_words_for_ai.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.aicut import commands  # noqa: E402


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: list[tuple[str, float]],
           side: list[tuple[str, float]] | None = None, raw: str | None = None) -> str:
    """Лента субтитров с цензурой (spec) и сайдкар clip.words.json рядом с clip.xml.

    side — записи сайдкара (слово, старт); raw — сырой текст сайдкара (для битого файла).
    Без обоих аргументов сайдкара нет вовсе.
    """
    words = [(k, w, s, s + 0.4) for k, (w, s) in enumerate(spec)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))
    if raw is not None:
        (tmp_path / "clip.words.json").write_text(raw, encoding="utf-8")
    elif side is not None:
        data = [{"w": w, "start": s, "end": s + 0.4} for w, s in side]
        (tmp_path / "clip.words.json").write_text(json.dumps(data, ensure_ascii=False),
                                                  encoding="utf-8")
    return str(tmp_path / "clip.xml")


def _texts(xml: str) -> list[str]:
    return [w for _k, w, _s, _e in commands._words_for_ai(xml)]


# ================== 1. ЗВЁЗДОЧКА СНИМАЕТСЯ ПО САЙДКАРУ ==================
def test_bpc_star_is_restored_to_the_real_name(tmp_path, monkeypatch):
    """«BPC-*57» в субтитрах + «BPC-157» в сайдкаре на том же времени -> «BPC-157»."""
    xml = _setup(tmp_path, monkeypatch, [("Сегодня", 1.0), ("BPC-*57", 30.33)],
                 side=[("Сегодня", 1.0), ("BPC-157", 30.34)])
    assert _texts(xml) == ["Сегодня", "BPC-157"]


@pytest.mark.parametrize("censored, real", [
    ("Т*500", "ТБ500"),
    ("ПЕП*ИДЫ", "ПЕПТИДЫ"),
    ("ЭПИТ*ЛОН", "ЭПИТАЛОН"),
])
def test_star_is_restored_for_each_censored_form(tmp_path, monkeypatch, censored, real):
    """Звёздочка на месте одной буквы: любое слово той же длины с тем же написанием вне «*»."""
    xml = _setup(tmp_path, monkeypatch, [(censored, 10.48)], side=[(real, 10.48)])
    assert _texts(xml) == [real]


def test_restored_word_is_upper_case(tmp_path, monkeypatch):
    """Текст из сайдкара приводится к верхнему регистру — как слова субтитров."""
    xml = _setup(tmp_path, monkeypatch, [("BPC-*57", 30.33)], side=[("bpc-157", 30.33)])
    assert _texts(xml) == ["BPC-157"]


def test_closest_in_time_candidate_wins(tmp_path, monkeypatch):
    """Два подходящих кандидата в окне 0.08 с — берётся ближайший по времени."""
    xml = _setup(tmp_path, monkeypatch, [("Т*500", 10.48)],
                 side=[("ТБ500", 10.50), ("ТА500", 10.47)])
    assert _texts(xml) == ["ТА500"]


# ================== 2. ЧТО НЕ ТРОГАЕМ ==================
def test_different_length_is_not_a_match(tmp_path, monkeypatch):
    """«Т*500» и «ТБ-500» — разные слова (длины не равны): слово остаётся как было."""
    xml = _setup(tmp_path, monkeypatch, [("Т*500", 10.48)], side=[("ТБ-500", 10.48)])
    assert _texts(xml) == ["Т*500"]


def test_time_shift_over_eighty_ms_is_not_a_match(tmp_path, monkeypatch):
    """Сдвиг 0.2 с — уже другое слово: кандидат не подходит, звёздочка остаётся."""
    xml = _setup(tmp_path, monkeypatch, [("BPC-*57", 30.33)], side=[("BPC-157", 30.53)])
    assert _texts(xml) == ["BPC-*57"]


def test_word_without_star_is_never_touched(tmp_path, monkeypatch):
    """Слово без «*» не меняется, даже если в сайдкаре на том же времени другой текст:
    правки текста владельцем живут в XML, сайдкар их не знает."""
    xml = _setup(tmp_path, monkeypatch, [("ТБ500", 10.48)], side=[("ТБ-500", 10.48)])
    assert _texts(xml) == ["ТБ500"]


def test_no_sidecar_leaves_words_as_they_are(tmp_path, monkeypatch):
    """Сайдкара нет — слова ровно такие, как вернул _words_from_xml (с цензурой)."""
    xml = _setup(tmp_path, monkeypatch, [("Т*500", 10.48), ("сегодня", 11.0)])
    assert _texts(xml) == ["Т*500", "сегодня"]


def test_broken_sidecar_leaves_words_as_they_are(tmp_path, monkeypatch):
    """Битый сайдкар не роняет разбор: слова остаются с цензурой."""
    xml = _setup(tmp_path, monkeypatch, [("Т*500", 10.48)], raw="{не json")
    assert _texts(xml) == ["Т*500"]


# ================== 3. ПРОМПТ ИИ ВИДИТ НАСТОЯЩЕЕ НАЗВАНИЕ ==================
def test_dry_inserts_prompt_has_the_real_name_not_the_star(tmp_path, monkeypatch):
    """cmd_inserts в режиме dry печатает промпт: в нём «BPC-157», а не «BPC-*57»."""
    xml = _setup(tmp_path, monkeypatch,
                 [("Сегодня", 1.0), ("BPC-*57", 30.33), ("вдвое", 31.0), ("закончили", 120.0)],
                 side=[("Сегодня", 1.0), ("BPC-157", 30.34), ("вдвое", 31.0), ("закончили", 120.0)])
    out: list[str] = []

    def _emit(msg: str = "", /, **_vars: Any) -> None:
        out.append(str(msg))

    commands.cmd_inserts(xml, dry=True, emit=_emit)
    text = "\n".join(out)
    assert "BPC-157" in text, text
    assert "BPC-*57" not in text, text
