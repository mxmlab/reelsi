# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Перечисление ≠ дубль (жалоба юзера 2026-08-04).

«где он сделал вот это, а где он сделал другое» — начало повторяется дословно,
и все три детектора повторов видели тут брошенный заход: срезали всё до второго
вхождения, оставляя «а где он сделал другое». Гейт общий — align.is_enumeration:
союз-связка перед вторым вхождением + СВОЯ содержательная мысль у первого.

Здесь проверяются текстовые пути (align/ssm); пословный пост-проход GigaAM —
в test_gigaam_postprocess.py.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import align  # noqa: E402
from core import ssm  # noqa: E402

ENUM = "где он сделал вот это а где он сделал другое"
RESTART = "где он сделал ээ где он сделал другое"


def mk(text, dur=0.3, gap=0.1):
    out, t = [], 0.0
    for w in text.split():
        out.append({"w": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + gap
    return out


def test_enumeration_is_not_a_restart():
    assert align.find_restarts(mk(ENUM))[0] == []


def test_real_restart_still_found():
    """Тот же повтор, но без своей мысли у первого захода — режем как раньше."""
    rng, log = align.find_restarts(mk(RESTART))
    assert rng and log[0][2] == "где он сделал ээ"


def test_ssm_text_gate_ignores_enumeration():
    """Гейт SSM: акустический рез повтора разрешён только если текст подтвердил."""
    assert ssm.text_has_repeat(ENUM) is False
    assert ssm.text_has_repeat("где он сделал ээ где он сделал другое") is True
    assert ssm.repeated_phrase(ENUM) == ""


def test_link_alone_is_not_enough():
    """Один союз без содержания между заходами — это запинка, а не перечисление."""
    assert align.is_enumeration("я сделал а я сделал иначе".split(), 0, 3) is False
    assert align.is_enumeration("я сделал это а я сделал иначе".split(), 0, 4) is True


def test_candidate_is_widened_to_the_left():
    """Детектор зацепился за середину повтора («он сделал», а не «где он
    сделал») — гейт всё равно должен увидеть союз перед вторым заходом."""
    toks = ENUM.split()
    assert align.is_enumeration(toks, 1, 7) is True


def test_stub_of_the_clean_take_is_not_content():
    """Хвост брошенного захода — обрубок слова, которое сейчас прозвучит целиком
    («диси» ← «дисип»): содержательной мыслью не считается."""
    toks = "попадая в организм диси а попадая в организм дисип нормализует".split()
    assert align.is_enumeration(toks, 0, 5) is False
