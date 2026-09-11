# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Цензура субтитров (censor.py) и правка её списков из настроек.

Списки правятся в ⚙ → «Слова», и правка НЕ должна затрагивать файлы поставки: у
каждого канала свои стоп-слова, а badwords.txt/okwords.txt лежат в репозитории.
Проверяем ровно этот контракт — подмену списка своим, возврат к поставочному и то,
что звёздочка в субтитрах и мьют звука считаются по одному и тому же правилу.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import censor as _censor  # noqa: E402


@pytest.fixture()
def censor(tmp_path, monkeypatch):
    """censor.py со своими файлами: и поставочными, и «пользовательскими». Боевые
    списки тесты не трогают — иначе прогон переписал бы юзеру его цензуру."""
    base = {"bad": str(tmp_path / "badwords.txt"), "ok": str(tmp_path / "okwords.txt")}
    user = {"bad": str(tmp_path / "badwords.user.txt"),
            "ok": str(tmp_path / "okwords.user.txt")}
    open(base["bad"], "w", encoding="utf-8").write("# коммент\nубива\nбля\n")
    open(base["ok"], "w", encoding="utf-8").write("бляшк\n")
    monkeypatch.setattr(_censor, "BASE_PATHS", base)
    monkeypatch.setattr(_censor, "USER_PATHS", user)
    monkeypatch.setattr(_censor, "_cache", {"bad": (None, None, []), "ok": (None, None, [])})
    return _censor


def test_stars_middle_letter_and_keeps_length(censor):
    """Слово должно остаться узнаваемым: звёздочка вместо ОДНОЙ буквы, длина та же."""
    out = censor.censor("убивать")
    assert out != "убивать" and len(out) == len("убивать") and out.count("*") == 1


def test_allow_list_wins(censor):
    """Обычное слово со стоп-подстрокой внутри не цензурится (иначе «бляшка»)."""
    assert censor.is_bad("бля") and not censor.is_bad("бляшка")


def test_ordinary_word_untouched(censor):
    assert censor.censor("монтаж") == "монтаж"


def test_user_list_replaces_shipped_one(censor):
    """Правка из UI ЗАМЕНЯЕТ поставочный список, а не дополняет его: иначе стем
    нельзя было бы убрать, а половина правок — именно про это."""
    censor.write_text("bad", "монтаж\n")
    assert censor.is_bad("монтаж") and not censor.is_bad("убивать")


def test_user_edit_does_not_touch_shipped_file(censor):
    """Файл из репозитория остаётся как был — его правка утекла бы в гит."""
    censor.write_text("bad", "монтаж\n")
    assert "убива" in open(censor.BASE_PATHS["bad"], encoding="utf-8").read()
    assert censor.is_custom("bad")


def test_empty_list_censors_nothing(censor):
    """Пустой список — осмысленный выбор «не цензурить», а не «вернуть как было»."""
    censor.write_text("bad", "")
    assert censor.censor("убивать") == "убивать"


def test_reset_returns_shipped_list(censor):
    censor.write_text("bad", "монтаж\n")
    censor.reset("bad")
    assert censor.is_bad("убивать") and not censor.is_bad("монтаж")
    assert not censor.is_custom("bad")


def test_info_reports_source_and_count(censor):
    """То, что видно в настройках: текст, счётчик и чей это список."""
    d = censor.info()
    assert d["bad"]["custom"] is False and d["bad"]["count"] == 2   # коммент не в счёт
    censor.write_text("ok", "бляшк\nстрах\n")
    assert censor.info()["ok"] == {"text": "бляшк\nстрах\n", "custom": True, "count": 2}


def test_edit_applies_without_restart(censor):
    """Списки перечитываются по mtime — иначе правка «не работала» до перезапуска."""
    assert not censor.is_bad("монтаж")
    censor.write_text("bad", "монтаж\n")
    assert censor.is_bad("монтаж")


def test_censor_windows_only_by_asterisk(censor):
    """Задание DC: глушим строго по звёздочке в тексте субтитра.

    Если пользователь убрал звёздочку руками, слово не должно глушиться, даже
    если оно осталось в списке плохих. И наоборот: ручная звёздочка глушит звук."""
    from core.xml2ae.layout import _censor_windows

    # 1. Слово со звёздочкой (автоцензура или ручная) -> окно заглушки есть
    # Длина 7 букв ("уб*вать"), звёздочка на позиции 2, кадры 0..70 (fps=10 -> 7 сек)
    # Середина буквы: s + dur * 2 / 7 = 0 + 70 * 2 / 7 = 20 кадров -> 2.0 сек; конец = 30 кадров -> 3.0 сек
    wins_star = _censor_windows([(0, 70, "уб*вать")], fps=10.0)
    assert wins_star == [(2.0, 3.0)]

    # 2. Ручное снятие цензуры (звёздочка убрана): слово из списка badwords без звёздочки
    assert censor.is_bad("убивать")
    wins_uncensored = _censor_windows([(0, 70, "убивать")], fps=10.0)
    assert wins_uncensored == []

    # 3. Ручная звёздочка в обычном слове -> окно заглушки есть
    # Длина 7 букв ("мон*таж"), звёздочка на позиции 3, кадры 0..70 (fps=10 -> 7 сек)
    # Середина буквы: s + dur * 3 / 7 = 30 кадров -> 3.0 сек; конец = 40 кадров -> 4.0 сек
    wins_manual = _censor_windows([(0, 70, "мон*таж")], fps=10.0)
    assert wins_manual == [(3.0, 4.0)]

