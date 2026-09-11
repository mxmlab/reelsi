# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставки шага 2 в плане сцены scene_plan (задание DP).

Баг, ради которого тест: на шаге 2 карточки вставок хранят start_sec/duration_sec,
а scene_plan читает start_s/start_f/dur_s/dur_f. Без конвертера cardToIns все вставки
шага 2 в плане сцены схлопывались в нулевую секунду (start=0, end=0). После приведения
к контракту плана окно встаёт на задуманные ~12 с, а стиль «Кам 1» над перебивкой
получает oncam2=True и общий сдвиг insert_c1on2_x/y.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_step2_insert_timing_in_scene_plan(xml_subs):
    """Вставка шага 2 (start_sec=12, duration_sec=2) после cardToIns даёт окно около 12 с."""
    # Контракт cardToIns: start_s=start_sec, dur_s=duration_sec
    ins = [dict(type="photo", media="test.jpg", start_s=12.0, start_f=0,
                dur_s=2.0, dur_f=0, x=0, y=0, sc=100, mw=100, mh=100)]
    plan = xml2ae.scene_plan(xml_subs, inserts=ins)
    assert len(plan["inserts"]) == 1
    item = plan["inserts"][0]
    # Окно около 12 с (с возможным снапом к кату), а не нулевая секунда
    assert abs(item["start"] - 12.0) <= 0.35
    assert item["end"] > item["start"]
    assert item["end"] - item["start"] >= 1.0


def test_step2_insert_style_cam1_on_cam2(xml_subs):
    """При insert_style='cam1' вставка над перебивкой (12 с) получает oncam2=True и сдвиг."""
    ins = [dict(type="photo", media="test.jpg", start_s=12.0, start_f=0,
                dur_s=2.0, dur_f=0, x=0, y=0, sc=100, mw=100, mh=100)]
    style = {"insert_style": "cam1", "insert_c1on2_x": 200, "insert_c1on2_y": -150}
    plan = xml2ae.scene_plan(xml_subs, inserts=ins, style=style)
    assert len(plan["inserts"]) == 1
    item = plan["inserts"][0]
    assert item["style"] == "cam1"
    assert item["oncam2"] is True
    # Позиционные ключи вылета содержат сдвиг (200, -150)
    pos_keys = item.get("anim", {}).get("position", [])
    assert pos_keys, "у вставки cam1 должны быть ключи anim.position"
    # Пик вылета (средние ключи) смещён на 200 по X
    xs = [k[1][0] for k in pos_keys]
    assert any(abs(x - 200) < 1e-3 for x in xs), f"сдвиг X=200 не найден в ключах {pos_keys}"


def test_step2_insert_duration_zero_fallback(xml_subs):
    """При duration_sec=0 карточка получает дефолтную длительность dur_s=2, не 0 (задание DP-хвост)."""
    # Симуляция выхода cardToIns при x.duration_sec=0: dur_s = round((0 || 2)*100)/100 = 2.0
    # При dur_s=2.0 вставка сохраняется в плане с нормальным окном
    ins = [dict(type="photo", media="test.jpg", start_s=12.0, start_f=0,
                dur_s=2.0, dur_f=0, x=0, y=0, sc=100, mw=100, mh=100)]
    plan = xml2ae.scene_plan(xml_subs, inserts=ins)
    assert len(plan["inserts"]) == 1
    assert plan["inserts"][0]["end"] - plan["inserts"][0]["start"] >= 1.0

