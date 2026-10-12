# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""core._pathguard.is_within — «путь внутри папки», одна проверка для inside_dir и подкаста.

Префикс-ловушка: `…\\ab\\x` не внутри `…\\a` (сравнение идёт по «папка + разделитель»).
Сама папка внутри себя не считается; `..` и ссылки уходят по realpath."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core._pathguard import inside_dir, is_within  # noqa: E402


def test_внутри_и_глубоко_внутри(tmp_path):
    base = tmp_path / "a"
    assert is_within(str(base / "x.mp3"), str(base))
    assert is_within(str(base / "sub" / "deep" / "y.wav"), str(base))


def test_снаружи_и_сама_папка(tmp_path):
    base = tmp_path / "a"
    assert not is_within(str(tmp_path / "other.mp3"), str(base))
    assert not is_within(str(base), str(base))             # папка сама в себе не «внутри»


def test_префикс_ловушка_a_и_ab(tmp_path):
    """`ab` начинается с `a`, но это другая папка: проверка по строке без разделителя ошибалась бы."""
    base = tmp_path / "a"
    sibling = tmp_path / "ab"
    assert not is_within(str(sibling / "x.mp3"), str(base))
    assert is_within(str(base / "x.mp3"), str(base))


def test_выход_через_dotdot_не_внутри(tmp_path):
    base = tmp_path / "a"
    base.mkdir()
    assert not is_within(str(base / ".." / "x.mp3"), str(base))
    assert is_within(str(base / "sub" / ".." / "x.mp3"), str(base))   # нормализуется внутрь


def test_inside_dir_строится_на_том_же_правиле(tmp_path):
    base = tmp_path / "a"
    base.mkdir()
    assert inside_dir(str(base), "x.mp3") == os.path.realpath(str(base / "x.mp3"))
    with pytest.raises(ValueError):
        inside_dir(str(base), "../ab/x.mp3")
    with pytest.raises(ValueError):
        inside_dir(str(base), "..")
