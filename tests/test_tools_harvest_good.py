# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистой логики сбора эталонных субтитров: tools/harvest_good.py."""
from __future__ import annotations

from pathlib import Path

from tools.harvest_good import _center_xy, resolve_files


def test_center_xy_centers_position_and_anchor() -> None:
    """_center_xy центрирует координаты X по 0.5 для Position и Anchor Point, сохраняя Y."""
    sample = (
        "<clip>"
        "<name>Position</name><value>prefix,0.123:0.456,postfix</value>"
        "<other>stuff</other>"
        "<name>Anchor Point</name><value>prefix,0.987:0.654,postfix</value>"
        "</clip>"
    )
    res = _center_xy(sample)
    assert "<name>Position</name><value>prefix,0.5:0.456,postfix</value>" in res
    assert "<name>Anchor Point</name><value>prefix,0.5:0.654,postfix</value>" in res


def test_center_xy_supports_scientific_notation() -> None:
    """_center_xy корректно обрабатывает числа с плавающей точкой в научной нотации."""
    sample = (
        "<name>Position</name><value>data,1.2e-3:4.5e+1,end</value>"
        "<name>Anchor Point</name><value>data,-2.0e+0:3.0e-1,end</value>"
    )
    res = _center_xy(sample)
    assert "<name>Position</name><value>data,0.5:4.5e+1,end</value>" in res
    assert "<name>Anchor Point</name><value>data,0.5:3.0e-1,end</value>" in res


def test_center_xy_unmatched_template_untouched() -> None:
    """Если Position/Anchor Point нет в шаблоне, текст не искажается."""
    sample = "<clip><name>Opacity</name><value>100</value></clip>"
    assert _center_xy(sample) == sample


def test_resolve_files(tmp_path: Path) -> None:
    """resolve_files отбирает *good*.xml из папок, добавляет файлы и убирает дубли."""
    d = tmp_path / "subfolder"
    d.mkdir()

    f_good1 = d / "01_good.xml"
    f_good1.write_text("<xml/>", encoding="utf-8")
    f_good2 = d / "ref_good_v2.xml"
    f_good2.write_text("<xml/>", encoding="utf-8")
    f_bad = d / "02_bad.xml"
    f_bad.write_text("<xml/>", encoding="utf-8")

    f_direct = tmp_path / "direct.xml"
    f_direct.write_text("<xml/>", encoding="utf-8")

    non_existent = tmp_path / "missing.xml"

    # Передаём папку, явный файл, несуществующий путь и дубль явного файла
    args = [str(d), str(f_direct), str(non_existent), str(f_direct)]
    resolved = resolve_files(args)

    assert str(non_existent) not in resolved
    assert str(f_bad) not in resolved
    assert str(f_good1) in resolved
    assert str(f_good2) in resolved
    assert str(f_direct) in resolved
    # Дедупликация и сортировка
    assert resolved == sorted(set(resolved))
    assert len(resolved) == 3
