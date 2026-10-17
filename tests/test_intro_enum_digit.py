# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Перечисление «цифра или слово» решает модель на весь список (enum_lists), а не на акцент.

Правило владельца: список правил, шагов, советов («три правила», «держите», «подключите») — номер
цифрой; перечень признаков, симптомов, рассказ «первое — …» — словом. Модель на КАЖДОЕ перечисление
в ролике возвращает {from, digit}, даже если его акценты не поместились под вставкой: цифра в субтитрах
нужна и там. Код переписывает головные слова цепочки на цифру тем же путём, что правка руками
(xml2ae.edit_word по индексу слова). Настоящую модель здесь не вызываем: ответ подменяем.

Запуск: py -3.10 -m pytest tests/test_intro_enum_digit.py -q
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402
from core.aicut import commands  # noqa: E402
from core.aicut.prompts import INTRO_SCHEMA, INTRO_SYSTEM  # noqa: E402


def _timed(*phrases):
    """Лента: слова фразы через 0.2 с, между фразами пауза 0.8 с (как в test_intro_enum)."""
    out, t = [], 0.0
    for ph in phrases:
        for w in ph.split():
            out.append((len(out), w, t, t + 0.15))
            t += 0.2
        t += 0.8
    return out


# Головы перечисления — индексы 1, 5, 8 (слова-номера ПЕРВОЕ, ВТОРОЕ, ТРЕТЬЕ).
ORDINAL_PHRASES = ("ПРИВЕТ", "ПЕРВОЕ ДЕРЖИТЕ БАЛАНС СОЛЕЙ", "ВТОРОЕ ПОДКЛЮЧИТЕ ПЕПТИДЫ",
                   "ТРЕТЬЕ ИСПОЛЬЗУЙТЕ ДОЗЫ")
DIGIT_PHRASES = ("ПРИВЕТ", "1 ДЕРЖИТЕ БАЛАНС СОЛЕЙ", "2 ПОДКЛЮЧИТЕ ПЕПТИДЫ", "3 ИСПОЛЬЗУЙТЕ ДОЗЫ")
HEADS = [1, 5, 8]


def _grp(head, count, **extra):
    """Группа-акцент, как её отдаёт модель: enum=true."""
    g = {"from": head, "count": count, "color": "white", "back": False, "enum": True}
    g.update(extra)
    return g


@pytest.fixture
def edits(monkeypatch):
    """Подмена xml2ae.edit_word: запоминает вызовы (индекс, текст), по умолчанию успех."""
    calls = []

    def fake(xml_path, index, text, out_path=None):
        calls.append((index, text))
        return {"ok": True, "word": text}

    monkeypatch.setattr(xml2ae, "edit_word", fake)
    return calls


def _run_intro(tmp_path, monkeypatch, enum_lists=None, mid_groups=(), words=None, inserts=(),
               dry=False, log=None):
    """cmd_intro с подменённым ответом модели.

    enum_lists=None — ответ БЕЗ поля enum_lists (старая модель). words — синтетическая лента
    (иначе — реальный XML в tmp_path). inserts — занятые вставками окна (start_sec, duration_sec)."""
    if words is not None:
        monkeypatch.setattr(commands, "_words_from_xml", lambda _p: list(words))
    answer = {"intro_rows": [], "mid_groups": list(mid_groups)}
    if enum_lists is not None:
        answer["enum_lists"] = list(enum_lists)
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: answer)
    lines = [] if log is None else log

    def emit(msg="", /, **kw):
        try:
            lines.append(str(msg).format(**kw))
        except (KeyError, IndexError, ValueError):
            lines.append(str(msg))        # промпт в dry-режиме содержит фигурные скобки JSON

    xml = str(tmp_path / "clip.xml")
    return commands.cmd_intro(xml, emit=emit, inserts=list(inserts), dry=dry)


def _xml_copy(tmp_path):
    """Копия реального тестового таймлайна: правки идут только в tmp_path, файлы владельца не трогаем."""
    dst = str(tmp_path / "clip.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _xml_with_ordinals(tmp_path, heads):
    """Копия таймлайна, где на позициях heads стоят ПЕРВОЕ/ВТОРОЕ/ТРЕТЬЕ — как у ASR до правки."""
    xml = _xml_copy(tmp_path)
    for h, w in zip(heads, ["ПЕРВОЕ", "ВТОРОЕ", "ТРЕТЬЕ"]):
        assert xml2ae.edit_word(xml, h, w).get("ok")
    return xml


# ---- 1. цепочки: одна цепочка на перечисление ----

def test_enum_chains_два_перечисления_две_цепочки():
    """«1 2 3» и потом «1 2» — две цепочки; _enum_heads — их плоский список по порядку."""
    words = _timed("1 ТЕСТ", "2 А", "3 Б", "1 ДВА", "2 ТРИ")
    assert commands._enum_chains(words, 0) == [[0, 2, 4], [6, 8]]
    assert commands._enum_heads(words, 0) == [0, 2, 4, 6, 8]


# ---- 2. под вставкой: пункта в mids нет, цифра всё равно пишется в субтитры ----

def test_enum_lists_digit_true_под_вставкой_все_три_головы_цифрами(tmp_path, monkeypatch):
    """Перечисление ПОД ВСТАВКОЙ: в mid_groups ни одной группы, акцента нет. enum_lists говорит «цифра» —
    все три головы в копии XML становятся 1/2/3 на тех же позициях, остальные слова не меняются."""
    xml = _xml_with_ordinals(tmp_path, [10, 20, 30])
    before = commands._words_from_xml(xml)
    inserts = [{"start_sec": before[9][2], "duration_sec": before[31][3] - before[9][2]}]

    log = []
    res = _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 10, "digit": True}],
                     inserts=inserts, log=log)

    assert not any(r.get("big") for r in res["mid_groups"]), "пункт должен быть под вставкой"
    after = commands._words_from_xml(xml)
    assert [after[h][1] for h in (10, 20, 30)] == ["1", "2", "3"]
    assert [w[1] for i, w in enumerate(after) if i not in (10, 20, 30)] == \
        [w[1] for i, w in enumerate(before) if i not in (10, 20, 30)]
    for was, n in (("ПЕРВОЕ", "1"), ("ВТОРОЕ", "2"), ("ТРЕТЬЕ", "3")):
        assert any(f"номер пункта: «{was}» → «{n}»" in ln for ln in log), log
    saved = json.loads((tmp_path / "clip.intro.json").read_text(encoding="utf-8"))
    assert all("enum" not in r and "digit" not in r for r in saved["mid_groups"])


def test_enum_lists_from_на_два_слова_раньше_головы_цепочка_найдена(tmp_path, monkeypatch):
    """from = 8, а первая голова цепочки — 10: модель сдвинула индекс на два слова назад — цепочка всё равно
    находится (в пределах трёх слов), и все её головы становятся цифрами."""
    xml = _xml_with_ordinals(tmp_path, [10, 20, 30])
    log = []
    _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 8, "digit": True}], log=log)

    after = commands._words_from_xml(xml)
    assert [after[h][1] for h in (10, 20, 30)] == ["1", "2", "3"]
    assert not any("не найдено" in ln for ln in log), log


# ---- 3. digit=false (или поля нет): слова не трогаем ----

@pytest.mark.parametrize("entry", [{"from": 1, "digit": False}, {"from": 1}])
def test_digit_false_или_нет_поля_слово_не_трогаем(tmp_path, monkeypatch, edits, entry):
    """digit=false (или поле не пришло): edit_word не вызван, пункты всё равно поставлены кодом."""
    res = _run_intro(tmp_path, monkeypatch, enum_lists=[entry], words=_timed(*ORDINAL_PHRASES))
    assert edits == []
    assert [r["from"] for r in res["mid_groups"] if r.get("big")] == HEADS


# ---- 4. головное слово уже цифра — ничего не делаем ----

def test_головное_слово_уже_цифра_не_трогаем(tmp_path, monkeypatch, edits):
    log = []
    _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 1, "digit": True}],
               words=_timed(*DIGIT_PHRASES), log=log)
    assert edits == []
    assert not any("номер пункта" in ln for ln in log), log


# ---- 5. dry-режим: запись не идёт ----

def test_dry_не_переписывает_слова(tmp_path, monkeypatch, edits):
    res = _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 1, "digit": True}],
                     words=_timed(*ORDINAL_PHRASES), dry=True)
    assert res is None
    assert edits == []


# ---- 6. нет поля enum_lists (старая модель): без падения и без правок ----

def test_нет_enum_lists_ничего_не_делаем(tmp_path, monkeypatch, edits):
    res = _run_intro(tmp_path, monkeypatch, enum_lists=None, words=_timed(*ORDINAL_PHRASES))
    assert res is not None
    assert edits == []


# ---- 7. одна цепочка из двух from пишется один раз, каждая голова — один вызов ----

def test_два_from_в_одну_цепочку_пишутся_один_раз(tmp_path, monkeypatch, edits):
    _run_intro(tmp_path, monkeypatch,
               enum_lists=[{"from": 1, "digit": True}, {"from": 5, "digit": True}],
               words=_timed(*ORDINAL_PHRASES))
    assert edits == [(1, "1"), (5, "2"), (8, "3")]


# ---- 8. from далеко от цепочки — строка в журнал, без правок ----

def test_from_вне_досягаемости_не_найдено(tmp_path, monkeypatch, edits):
    log = []
    _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 11, "digit": True}],
               words=_timed(*ORDINAL_PHRASES), log=log)
    assert edits == []
    assert any("перечисление с 11 не найдено" in ln for ln in log), log


# ---- 9. ошибка правки — строка в журнал, разметка не падает ----

def test_ошибка_edit_word_пишется_в_журнал_и_не_роняет(tmp_path, monkeypatch):
    def failing(xml_path, index, text, out_path=None):
        return {"error": "индекс вне диапазона"}

    monkeypatch.setattr(xml2ae, "edit_word", failing)
    log = []
    res = _run_intro(tmp_path, monkeypatch, enum_lists=[{"from": 1, "digit": True}],
                     words=_timed(*ORDINAL_PHRASES), log=log)
    assert res is not None
    assert [r["from"] for r in res["mid_groups"] if r.get("big")] == HEADS
    assert any("не переписан" in ln and "ПЕРВОЕ" in ln for ln in log), log


# ---- 10. схема и промпт ----

def test_схема_enum_lists_в_required_а_digit_в_пункте_нет():
    top = INTRO_SCHEMA["properties"]
    assert "enum_lists" in INTRO_SCHEMA["required"]
    assert top["enum_lists"]["items"]["properties"]["digit"] == {"type": "boolean"}
    assert top["enum_lists"]["items"]["properties"]["from"] == {"type": "integer"}
    item = top["mid_groups"]["items"]
    assert "digit" not in item["properties"]
    assert "digit" not in item["required"]


def test_промпт_описывает_enum_lists():
    assert "enum_lists (массив {from,digit})" in INTRO_SYSTEM
    assert "под вставкой" in INTRO_SYSTEM
    assert "digit=true" in INTRO_SYSTEM and "digit=false" in INTRO_SYSTEM
