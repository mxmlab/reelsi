# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Квартер-качество ВСЕХ видеослоёв сборки AE (правка 2026-08-08).

Сборка на 4K/60: клипы камер, видеовставки, рото-копии и переходы должны
собираться в Draft (LayerQuality.DRAFT = 0.25 — «quarter»), иначе многослойный
проект жмёт таймлайн и рендер, а качество приходится выставлять руками на каждом
ролике. Тест стережёт, чтобы пометка не потерялась при переносе кода: новый
видеослой без draftQ() снова уедет в полном качестве.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402

SRC = xml2ae.AE_FULL


def _next(src, marker, size=140):
    i = src.index(marker)
    return src[i:i + size]


def test_draft_helper_exists():
    """Помощник и сам квартер-уровень прописаны в шаблоне."""
    assert "function draftQ(l)" in SRC
    assert "LayerQuality.DRAFT" in SRC
    assert "0.25" in SRC


def test_camera_clips_are_draft():
    assert "draftQ(lay);" in _next(SRC, "var lay = main.layers.add(src);")


def test_video_inserts_are_draft():
    assert "draftQ(vl);" in _next(SRC, "var vl=main.layers.add(vit);")


def test_roto_copies_are_draft():
    assert "draftQ(cc);" in _next(SRC, "var cc = main.layers.add(camSrc);")


def test_transition_layers_are_draft():
    assert "draftQ(tl);" in _next(SRC, "var tl=main.layers.add(transItem);")
