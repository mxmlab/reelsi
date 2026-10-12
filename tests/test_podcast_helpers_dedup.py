# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие мелочи подкаста, которые раньше были копиями: core.podcast.num_or_none (был _num в
podcast и podcast_ai) и core.fileio.remove_quiet (был _rm_quiet в podcast_ai и podcast_reframe)."""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import fileio, podcast, podcast_ai  # noqa: E402


def test_num_or_none_число_и_не_число():
    assert podcast.num_or_none(3) == 3.0 and isinstance(podcast.num_or_none(3), float)
    assert podcast.num_or_none(2.5) == 2.5
    assert podcast.num_or_none(True) is None            # bool — не число
    assert podcast.num_or_none("3") is None
    assert podcast.num_or_none(None) is None
    assert podcast.num_or_none(float("nan")) is None
    assert podcast.num_or_none(math.inf) is None


def test_podcast_ai_берёт_то_же_число_что_podcast():
    """Копии не осталось: и разбор ИИ, и проект читают числа одной функцией."""
    assert podcast_ai.num_or_none is podcast.num_or_none
    assert not hasattr(podcast_ai, "_num")


def test_remove_quiet_удаляет_и_молчит_если_нет(tmp_path):
    p = tmp_path / "tmp.wav"
    p.write_bytes(b"x")
    fileio.remove_quiet(str(p))
    assert not p.exists()
    fileio.remove_quiet(str(p))                          # уже нет — без исключения
    fileio.remove_quiet(str(tmp_path / "нет" / "такого.wav"))
