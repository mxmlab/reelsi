# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно группы интро с глитчем не кончается раньше окна той же группы без глитча.

Баг: группа с глитч-словом на раннем слове гасла на 0.4 с раньше обычной. Окно выхода
считалось от глитча (_out_s = te − 0.75, фейд 0.35), и подрезка под старт следующей группы
уже резать было нечего, так что между концом группы и стартом следующей оставался
пустой отрезок — интро на экране нет.

Инвариант: te группы с глитчем >= te той же группы без глитча. Продление окна под конец
анимации глитча при этом должно работать: глитч на позднем слове даёт te строго больше.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles, xml2ae  # noqa: E402


@pytest.fixture()
def xml_path(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan_groups(xml, intro, splits):
    """Окна групп интро из плана сцены: список (ts, te) по порядку групп.

    Гашение к субтитру выключено: оно режет окно по субтитрам фикстуры и заслонило бы
    правило глитча, которое здесь проверяем."""
    plan = xml2ae.scene_plan(xml, intro=intro, intro_splits=splits, intro_riser=False,
                             disclaimer="", roto=False, emit=lambda *a: None,
                             style={"intro_sub_cut": False})
    return [(g["ts"], g["te"]) for g in plan["intro"]]


def _early_glitch(anim_early):
    """Группа 0: раннее слово на 1.0 с (глитч или нет) и обычное на 2.0 с — базовое окно
    до 2.75 с. Группа 1 стартует на 2.5 с, то есть ВНУТРИ базового окна группы 0."""
    return [
        dict(words=["РАНЬШЕ"], color="white", times=[1.0], anim=anim_early),
        dict(words=["ПОЗЖЕ"], color="white", times=[2.0], anim=""),
        dict(words=["СЛЕДУЮЩЕЕ"], color="white", times=[2.5], anim=""),
    ]


def _late_glitch(anim_late):
    """Группа 0: глитч на позднем слове 2.0 с, следующая группа далеко (6.0 с)."""
    return [
        dict(words=["НАЧАЛО"], color="white", times=[0.5], anim=""),
        dict(words=["КОНЕЦ"], color="white", times=[2.0], anim=anim_late),
        dict(words=["ДАЛЕКО"], color="white", times=[6.0], anim=""),
    ]


SPLITS = [2]    # граница после второй строки: группы [РАНЬШЕ, ПОЗЖЕ] и [СЛЕДУЮЩЕЕ]


def test_группа_с_глитчем_стыкуется_со_следующей_без_дыры(xml_path):
    with_glitch = _plan_groups(xml_path, _early_glitch("glitch"), SPLITS)
    without = _plan_groups(xml_path, _early_glitch(""), SPLITS)
    assert len(with_glitch) == 2 and len(without) == 2

    te_glitch = with_glitch[0][1]
    te_plain = without[0][1]
    ts_next = with_glitch[1][0]
    assert te_glitch == te_plain == ts_next, (
        f"дыра перед следующим интро: te с глитчем {te_glitch}, "
        f"te без глитча {te_plain}, ts следующей группы {ts_next}")


def test_окно_с_глитчем_не_короче_окна_без_глитча_для_каждой_группы(xml_path):
    with_glitch = _plan_groups(xml_path, _early_glitch("glitch"), SPLITS)
    without = _plan_groups(xml_path, _early_glitch(""), SPLITS)
    assert len(with_glitch) == len(without) == 2
    for gi, ((_, te_glitch), (_, te_plain)) in enumerate(zip(with_glitch, without)):
        assert te_glitch >= te_plain, (
            f"группа {gi}: te с глитчем {te_glitch} раньше te без глитча {te_plain}")


def test_глитч_на_позднем_слове_продлевает_окно(xml_path):
    with_glitch = _plan_groups(xml_path, _late_glitch("glitch"), [2])
    without = _plan_groups(xml_path, _late_glitch(""), [2])
    assert len(with_glitch) == 2 and len(without) == 2
    te_glitch = with_glitch[0][1]
    te_plain = without[0][1]
    assert te_glitch > te_plain, (
        f"продление под конец анимации глитча не сработало: "
        f"te с глитчем {te_glitch}, без глитча {te_plain}")


def test_далёкая_последняя_группа_с_ранним_глитчем_получает_надбавку(xml_path):
    """Последняя группа (intro_last_hold по умолчанию) с ранним глитчем: полка берётся от
    начала спада обычной группы (te − fade), надбавка intro_fx_hold_add — поверх.

    Группа одна слово на 12.0 с: базовое окно до 14.05 с, полка глитча 12.44 с — раньше
    начала спада (13.7 с), поэтому полка = 13.7 с, надбавка 0.3 даёт 14.0 с, te = 14.35 с.
    Без глитча te = 14.05 с. Итог: te с глитчем = te без глитча + надбавка.
    """
    hold = styles.BASE["intro_fx_hold_add"]
    intro_glitch = [
        dict(words=["ПЕРВОЕ"], color="white", times=[1.0]),
        dict(words=["ПОСЛЕДНЕЕ"], color="white", times=[12.0], anim="glitch"),
    ]
    intro_plain = [
        dict(words=["ПЕРВОЕ"], color="white", times=[1.0]),
        dict(words=["ПОСЛЕДНЕЕ"], color="white", times=[12.0], anim=""),
    ]
    with_glitch = _plan_groups(xml_path, intro_glitch, [1])
    without = _plan_groups(xml_path, intro_plain, [1])
    assert len(with_glitch) == 2 and len(without) == 2

    te_glitch = with_glitch[1][1]
    te_plain = without[1][1]
    assert te_glitch == pytest.approx(te_plain + hold, abs=1e-6), (
        f"надбавка далёкой последней группы не поверх окна: "
        f"te с глитчем {te_glitch}, без глитча {te_plain}, надбавка {hold}")
