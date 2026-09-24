# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистой логики хуков интро: tools/intro_hook_check.py и tools/intro_hook_rules.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import tools.intro_hook_check as hook_check
import tools.intro_hook_rules as hook_rules


def test_hook_check_invalid_extension() -> None:
    """check() требует файл с расширением .intro.json."""
    with pytest.raises(ValueError, match="жду путь к .intro.json"):
        hook_check.check("test.json")


def test_hook_check_logic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """check() подсчитывает долю строк в одно слово и со служебным словом на конце."""
    intro_file = tmp_path / "clip01.intro.json"

    # 4 строки:
    # 1: count=1, "ПО" (одно слово, служебное слово)
    # 2: count=2, "ПРИВЕТ", "МИР" (2 слова, не служебное)
    # 3: count=1, "НА" (одно слово, служебное слово)
    # 4: count=3, но доступно только 1 слово (неполная строка — должна быть отброшена)
    intro_data = {
        "intro_rows": [
            {"count": 1},
            {"count": 2},
            {"count": 1},
            {"count": 3},
        ]
    }
    intro_file.write_text(json.dumps(intro_data, ensure_ascii=False), encoding="utf-8")

    fake_words: list[tuple[int, str, float, float]] = [
        (0, "ПО", 0.0, 0.3),
        (1, "ПРИВЕТ", 0.4, 0.7),
        (2, "МИР", 0.8, 1.1),
        (3, "НА", 1.2, 1.4),
        (4, "ХВОСТ", 1.5, 1.8),
    ]

    monkeypatch.setattr(hook_check.aicut, "_words_from_xml", lambda _p: fake_words)

    one, func, total = hook_check.check(str(intro_file))
    assert total == 3
    assert one == 2
    assert func == 2

    out = capsys.readouterr().out
    assert "строк 3" in out
    assert "в одно слово 67%" in out
    assert "со служебным словом на конце 67%" in out


def test_hook_check_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """main() в intro_hook_check обрабатывает аргументы и выводит итоговую статистику."""
    # Без аргументов -> код 1
    monkeypatch.setattr(sys, "argv", ["intro_hook_check.py"])
    assert hook_check.main() == 1

    # С двумя файлами -> код 0 и сводка
    f1 = tmp_path / "a.intro.json"
    f2 = tmp_path / "b.intro.json"
    f1.write_text(json.dumps({"intro_rows": [{"count": 1}]}), encoding="utf-8")
    f2.write_text(json.dumps({"intro_rows": [{"count": 2}]}), encoding="utf-8")

    monkeypatch.setattr(
        hook_check.aicut,
        "_words_from_xml",
        lambda _p: [(0, "ТЕСТ", 0.0, 0.5), (1, "ДВА", 0.6, 1.0)],
    )

    monkeypatch.setattr(sys, "argv", ["intro_hook_check.py", str(f1), str(f2)])
    code = hook_check.main()
    assert code == 0
    out = capsys.readouterr().out
    assert "итого:" in out


def test_hook_rules_intro_groups_from_jsx(tmp_path: Path) -> None:
    """intro_groups_from_jsx извлекает JSON из выражения var INTRO_GROUPS=[...];."""
    # Корректный JSX
    f_ok = tmp_path / "valid.jsx"
    f_ok.write_text(
        'var other = 1;\nvar INTRO_GROUPS=[[{"words": ["тест"], "times": [1.5]}]];\n',
        encoding="utf-8",
    )
    res = hook_rules.intro_groups_from_jsx(str(f_ok))
    assert res == [[{"words": ["тест"], "times": [1.5]}]]

    # Без переменной
    f_none = tmp_path / "none.jsx"
    f_none.write_text("var OTHER = [];\n", encoding="utf-8")
    assert hook_rules.intro_groups_from_jsx(str(f_none)) is None

    # С битым JSON
    f_bad = tmp_path / "bad.jsx"
    f_bad.write_text("var INTRO_GROUPS=[{broken];\n", encoding="utf-8")
    assert hook_rules.intro_groups_from_jsx(str(f_bad)) is None


def test_hook_rules_math_helpers() -> None:
    """med и pct корректно считают медиану и квантили с фильтрацией None."""
    assert hook_rules.med([]) is None
    assert hook_rules.med([5, 1, 3]) == 3
    assert hook_rules.med([10, None, 20, 30, 40]) == 30

    assert hook_rules.pct([], 0.9) is None
    assert hook_rules.pct([10, 20, 30, 40, 50], 0.5) == 30
    assert hook_rules.pct([10, None, 50], 0.0) == 10


def test_hook_rules_split_hook() -> None:
    """_split_hook разделяет прекомпы на хук и акценты по порогам времени и пауз."""
    # Прекомп 1: старт 0.0, конец 2.0 -> хук
    # Прекомп 2: старт 2.5 (разрыв 0.5 < 1.5), конец 4.0 -> хук
    # Прекомп 3: старт 6.0 (разрыв 2.0 >= 1.5) -> акцент
    # Пустой прекомп: должен отсеиваться
    # Прекомп 4: старт 26.0 (старт >= 25) -> акцент
    groups: list[Any] = [
        [],  # пустой
        [{"times": [0.0, 1.0]}, {"times": [1.5, 2.0]}],
        [{"times": [2.5, 3.0]}, {"times": [3.5, 4.0]}],
        [{"times": [6.0, 7.0]}],
        [{"times": [26.0, 27.0]}],
    ]
    hook, accents = hook_rules._split_hook(groups)
    assert len(hook) == 2
    assert len(accents) == 2
    assert hook[0][0]["times"][0] == 0.0
    assert hook[1][0]["times"][0] == 2.5
    assert accents[0][0]["times"][0] == 6.0
    assert accents[1][0]["times"][0] == 26.0
