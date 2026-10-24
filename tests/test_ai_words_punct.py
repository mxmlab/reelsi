# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ИИ видит слова с пунктуацией и без цензуры: жёлтые, вставки и интро.

Субтитры XML — ЗАГЛАВНЫЕ, без знаков, с цензурой («КУ*СЕ»). Расшифровка рядом (`<stem>.words.json`)
несёт исходные слова: с пунктуацией и обычным регистром, без цензуры. `_shown_words` отдаёт модели
слово расшифровки, если буквы совпали, а `_word_lines` ставит его во вторую колонку задания.

Списки слов, по которым считает код, не меняются: тест на пост-обработку жёлтых фиксирует, что
индексы в `.yellow.json` такие же, как до правки.

Тесты подменяют `_words_from_xml` (настоящий XML не разбираем) и кладут сайдкар во временную
папку: ни вызовов ИИ, ни сети.

Запуск: py -3.10 -m pytest tests/test_ai_words_punct.py -q -p no:cacheprovider
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

from core import xml2ae  # noqa: E402
from core.aicut import commands  # noqa: E402

# Слова субтитров, как их отдаёт _words_from_xml: ЗАГЛАВНЫЕ, без знаков, с цензурой.
XML_WORDS = [("ВАШ", 0.00), ("КУ*СЕ", 0.82), ("ОСТАНОВИТЬСЯ", 2.57),
             ("ДОЗЫ", 4.13), ("ОГРОМНЫЕ", 5.00), ("ТЕСТ", 6.00)]
# Расшифровка рядом: та же лента по времени, с пунктуацией и обычным регистром.
# «ДОЗЫ» владелец поправил в XML (в расшифровке было «дозировки»), поэтому буквы не совпадают.
SIDE = [("Ваш", 0.00), ("курсе", 0.82), ("остановиться.", 2.57),
        ("дозировки", 4.13), ("Огромные", 5.00), ("тест,", 6.00)]


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, xml_words, side=None):
    """Лента субтитров (xml_words) и, если задан side, сайдкар clip.words.json рядом с clip.xml.

    Возвращает (путь к XML, ленту (индекс, слово, старт, конец)). Настоящего XML нет:
    _words_from_xml подменён, путь нужен только для поиска сайдкара.
    """
    words = [(k, w, s, s + 0.4) for k, (w, s) in enumerate(xml_words)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))
    if side is not None:
        data = [{"w": w, "start": s, "end": s + 0.4} for w, s in side]
        (tmp_path / "clip.words.json").write_text(json.dumps(data, ensure_ascii=False),
                                                  encoding="utf-8")
    return str(tmp_path / "clip.xml"), words


def _collector():
    out: list[str] = []

    def _emit(msg: str = "", /, **_vars: Any) -> None:
        out.append(str(msg))

    return out, _emit


def _rows(user: str) -> dict[int, str]:
    """Строки задания `индекс<TAB>слово<TAB>тайминг` -> {индекс: слово, которое видит модель}."""
    rows: dict[int, str] = {}
    for line in user.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit():
            rows[int(parts[0])] = parts[1]
    return rows


# ================== 1. ЧТО ВИДИТ МОДЕЛЬ ==================
def test_shown_words_take_sidecar_text_where_letters_match(tmp_path, monkeypatch):
    """Буквы совпали — показываем слово расшифровки как есть (регистр и знаки);
    правленое владельцем слово — из XML."""
    xml, words = _setup(tmp_path, monkeypatch, XML_WORDS, side=SIDE)
    assert commands._shown_words(xml, words) == [
        "Ваш", "курсе", "остановиться.", "ДОЗЫ", "Огромные", "тест,"]


def test_word_edited_in_xml_is_not_replaced_by_sidecar(tmp_path, monkeypatch):
    """Правка владельца в XML живёт там: сайдкар с другими буквами её не перебивает."""
    xml, words = _setup(tmp_path, monkeypatch, [("ДОЗЫ", 4.13)], side=[("дозировки", 4.13)])
    assert commands._shown_words(xml, words) == ["ДОЗЫ"]


def test_without_sidecar_every_word_is_shown_as_in_xml(tmp_path, monkeypatch):
    """Сайдкара нет — все слова идут как в XML, с цензурой и без знаков."""
    xml, words = _setup(tmp_path, monkeypatch, XML_WORDS)
    assert commands._shown_words(xml, words) == [w for w, _s in XML_WORDS]


def test_sidecar_word_outside_the_time_window_is_not_used(tmp_path, monkeypatch):
    """Сдвиг старта больше 0.08 с — это другое слово: кандидат не подходит, «КУ*СЕ» остаётся."""
    xml, words = _setup(tmp_path, monkeypatch, [("КУ*СЕ", 0.82)], side=[("курсе", 1.10)])
    assert commands._shown_words(xml, words) == ["КУ*СЕ"]


@pytest.mark.parametrize("xml_word, side_word, same", [
    ("КУ*СЕ", "курсе", True),       # звёздочка — любая одна буква
    ("КУ*СЕ", "курсе!", True),      # краевой знак в расшифровке не в счёт
    ("ТЕСТ", "тест,", True),        # регистр не в счёт
    ("ЁЛКА", "елка.", True),        # ё = е
    ("Т*500", "ТБ-500", False),     # разная длина букв
])
def test_same_letters_rule(xml_word, side_word, same):
    assert commands._same_letters(xml_word, side_word) is same


# ================== 2. ЗАДАНИЕ МОДЕЛИ В РЕЖИМЕ DRY ==================
@pytest.mark.parametrize("step", ["yellow", "inserts", "intro"])
def test_dry_prompt_shows_sidecar_text_in_every_step(tmp_path, monkeypatch, step):
    """В задании каждого из трёх шагов «курсе» и «остановиться.», а «КУ*СЕ» нет."""
    xml, _words = _setup(tmp_path, monkeypatch, XML_WORDS, side=SIDE)
    out, emit = _collector()
    run = {"yellow": commands.cmd_yellow,
           "inserts": commands.cmd_inserts,
           "intro": commands.cmd_intro}[step]
    run(xml, dry=True, emit=emit)
    assert len(out) == 1, out
    user = out[0].split("\n---\n", 1)[1]
    rows = _rows(user)
    assert rows[1] == "курсе", user
    assert rows[2] == "остановиться.", user
    assert "КУ*СЕ" not in user, user


# ================== 3. ПОСТ-ОБРАБОТКА ЖЁЛТЫХ НЕ МЕНЯЕТСЯ ==================
def test_yellow_indices_written_are_the_same_as_before(tmp_path, monkeypatch):
    """Модель вернула индексы с мусором и выходом за границы: код берёт валидные и пишет
    .yellow.json так же, как до правки. Списки слов для кода не менялись, поэтому и результат тот же."""
    xml, _words = _setup(tmp_path, monkeypatch, XML_WORDS, side=SIDE)
    monkeypatch.setattr(commands, "_ask_json",
                        lambda *_a, **_k: {"yellow": [2, 0, 2, 99, "мусор"]})
    monkeypatch.setattr(xml2ae, "write_highlights",
                        lambda xml_path, idx: {"colored": list(idx), "skipped": []})
    monkeypatch.setattr(xml2ae.precompute, "emphasis_precompute", lambda *a, **k: None)
    _out, emit = _collector()
    res = commands.cmd_yellow(xml, emit=emit)
    assert res["yellow"] == [0, 2]
    saved = json.loads((tmp_path / "clip.yellow.json").read_text(encoding="utf-8"))
    assert saved == {"yellow": [0, 2]}
