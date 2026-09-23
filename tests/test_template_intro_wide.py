# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Широкие интро-прекомпы (правка 2026-08-08) и переезд автофита в Python.

Интро-прекомпы создаются шириной INTRO_WIDE × кадр: текст внутри раскладывается
в полный кегль и центрируется по IW/2, а размер на ролике правится скейлом слоя
в мастере — заходить в композ для этого больше не нужно. Автофит длинных строк
считает scene_plan: в шаблоне остался только масштаб слоя от
готового ds (96.8·ds/100), второй копии формулы нет.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402

SRC = xml2ae.AE_FULL


def test_wide_constant_is_declared():
    assert "var INTRO_WIDE=3;" in SRC


def test_intro_precomp_uses_wide_width():
    i = SRC.index('var ic=app.project.items.addComp("текст интро"')
    seg = SRC[i:i + 220]
    assert ", IW, H, 1.0," in seg, "прекомп интро создаётся не в IW (широкой) ширину"
    assert "var IW=Math.round(W*INTRO_WIDE);" in SRC


def test_lines_center_inside_wide_comp():
    assert "setValue([IW/2, lineY]);" in SRC, "построчный режим центрирует по W, а не по IW"
    assert "var x=IW/2 - lineW/2;" in SRC, "пословный режим центрирует по W, а не по IW"


def test_autofit_moved_to_python():
    """Автофит убрали из шаблона: шаблон применяет только масштаб слоя
    от готового ds и ничего не досчитывает — иначе превью и AE разъехались бы."""
    assert "IW*INTRO_FIT_W" not in SRC, "автофит остался в шаблоне (должен жить в scene_plan)"
    assert "var INTRO_FIT_W=" not in SRC, "мёртвая константа в шаблоне (источник — layout.py)"
    assert "var iSc=96.8*gDs/100;" in SRC, "масштаб слоя от готового ds обязан остаться"
