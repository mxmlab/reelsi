# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания ZD:
1. _cam1_follow_keys с min_scale: зум-ключи 200% (HOLD) -> 300% (плавно) -> 200%;
   голова уходит вправо. На 200% поправка 0 (при min_scale=250), на 300% — ненулевая,
   после отъезда возвращается к 0 (последний ключ клипа == 0). min_scale=0 — ключи
   равны нынешним.
2. Кат в клип ниже порога при ненулевой поправке — ключ на кате == 0.
3. _cam1_jump_keys с words: один длинный тейк, жёлтое слово на кадре 900 — ключ
   (900, vm, 2, 1), a = 900 - 96; то же без words — a = f + 96 (прежнее);
   значения vm в обоих случаях равны.
4. Слово слишком близко к кату (w - 96 < f + 18) — берётся следующее подходящее, а если
   его нет — прежнее правило.
5. Сборка фикстуры с cam1_take_yellow=True и без жёлтых слов — .jsx как без галки.
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
from core.xml2ae import layout  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_cam1_follow_keys_min_scale():
    """1. _cam1_follow_keys с min_scale: зум-ключи 200% (HOLD) -> 300% (плавно) -> 200%;
    голова уходит вправо. На 200% поправка 0 (при min_scale=250), на 300% — ненулевая,
    после отъезда возвращается к 0 (последний ключ клипа == 0). min_scale=0 — ключи равны нынешним.
    """
    cams = [{"clips": [[0, 600, 0, 600, True, 100]]}]
    fps = 60.0
    W, H = 1080, 1920
    w_src, h_src = 1080, 1920
    zoom_keys = [(0, 200.0), (120, 200.0), (200, 300.0), (300, 200.0), (600, 200.0)]
    holds = [True, False, False, True]
    pts = [[0.0, 0.5], [10.0, 0.8]]

    # С min_scale=0 ключи идентичны вызову без min_scale (дефолт 0.0)
    keys_default = layout._cam1_follow_keys(
        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
    )
    keys_min0 = layout._cam1_follow_keys(
        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
        min_scale=0.0,
    )
    assert keys_default == keys_min0

    # С min_scale=250.0:
    keys_min250 = layout._cam1_follow_keys(
        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
        min_scale=250.0,
    )
    # На 200% в начале (например кадр 0..90) поправка 0
    early_keys = [k for k in keys_min250 if k[0] <= 90]
    assert early_keys
    assert all(k[1] == 0.0 for k in early_keys)

    # На 300% (вокруг кадра 200) поправка ненулевая
    peak_keys = [k for k in keys_min250 if 160 <= k[0] <= 240]
    assert peak_keys
    assert any(k[1] != 0.0 for k in peak_keys)

    # После отъезда возвращается к 0, последний ключ клипа == 0
    assert keys_min250[-1][1] == 0.0


def test_cam1_follow_keys_cut_to_subthreshold_clip():
    """2. Кат в клип ниже порога при ненулевой поправке — ключ на кате == 0."""
    # Два клипа кам1: 0..300 (зум 300%, выше порога) и 300..600 (зум 150%, ниже порога 200, но выше 100%: при 100% поправку обнулил бы зажим края, а не порог)
    cams = [
        # второй клип берёт исходник с 5 с: голова там уже смещена (hx=0.7), и без
        # порога поправка на кате была бы ненулевой
        {"clips": [[0, 300, 0, 300, True, 100], [300, 600, 300, 600, True, 100]]},
    ]
    fps = 60.0
    W, H = 1080, 1920
    w_src, h_src = 1080, 1920
    zoom_keys = [(0, 300.0), (299, 300.0), (300, 150.0), (600, 150.0)]
    holds = [True, False, True]
    pts = [[0.0, 0.5], [10.0, 0.9]]

    keys = layout._cam1_follow_keys(
        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
        min_scale=200.0,
    )
    # В первом клипе (до 300) поправка ненулевая перед катом
    k_before_cut = [k for k in keys if k[0] == 299]
    assert k_before_cut and k_before_cut[0][1] != 0.0

    # На кате 300 — ключ ровно 0
    k_at_cut = [k for k in keys if k[0] == 300]
    assert k_at_cut
    assert k_at_cut[0][1] == 0.0


def test_cam1_jump_keys_with_words():
    """3. _cam1_jump_keys с words: один длинный тейк, жёлтое слово на кадре 900 — ключ
    (900, vm, 2, 1), a = 900 - 96; то же без words — a = f + 96 (прежнее);
    значения vm в обоих случаях равны.
    """
    fps = 60.0
    cams = [{"clips": [[0, 1500, 0, "cam1.mov", True]]}]
    take_base = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}

    # Без words
    keys_no_words = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_base)
    extra_no_words = [k for k in keys_no_words if k[0] > 0]
    assert len(extra_no_words) >= 2
    assert extra_no_words[0][0] == 96
    assert extra_no_words[1][0] == 192
    vm_no_words = extra_no_words[1][1]

    # С words: жёлтое слово на кадре 900
    take_words = dict(take_base, words=[900])
    keys_words = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_words)
    extra_words = [k for k in keys_words if k[0] > 0]
    assert len(extra_words) >= 2
    assert extra_words[0] == (804, 100.0, 1, 0)
    assert extra_words[1] == (900, vm_no_words, 2, 1)


def test_cam1_jump_keys_word_too_close_to_cut():
    """4. Слово слишком близко к кату (w - 96 < f + 18) — берётся следующее подходящее, а если
    его нет — прежнее правило.
    """
    fps = 60.0
    cams = [{"clips": [[0, 1500, 0, "cam1.mov", True]]}]
    take_base = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}

    # Первое слово 100 слишком близко: 100 - 96 = 4 < 0 + 18.
    # Второе слово 900 подходит: 900 - 96 = 804 >= 18.
    take_two_words = dict(take_base, words=[100, 900])
    keys_two = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_two_words)
    extra_two = [k for k in keys_two if k[0] > 0]
    assert extra_two[0][0] == 804
    assert extra_two[1][0] == 900

    # Если подходящих слов нет (только слово 100) — прежнее правило (a = 96, b = 192)
    take_one_close = dict(take_base, words=[100])
    keys_one = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_one_close)
    extra_one = [k for k in keys_one if k[0] > 0]
    assert extra_one[0][0] == 96
    assert extra_one[1][0] == 192


def test_build_take_yellow_without_yellow_words(xml_subs, tmp_path):
    """5. Сборка фикстуры с cam1_take_yellow=True и без жёлтых слов — .jsx как без галки."""
    assert "cam1_take_yellow" in styles.BASE

    st_no_yellow = {
        "cam1_zoom": "jump",
        "cam1_take_zoom": True,
        "cam1_take_min": 3.0,
        "cam1_take_yellow": False,
    }
    st_with_yellow = {
        "cam1_zoom": "jump",
        "cam1_take_zoom": True,
        "cam1_take_min": 3.0,
        "cam1_take_yellow": True,
    }

    jsx_no_yellow, _, _ = xml2ae.to_ae_full(
        xml_subs,
        return_source=True,
        style=st_no_yellow,
        highlights=[],
        emit=lambda *a, **k: None,
    )
    jsx_with_yellow, _, _ = xml2ae.to_ae_full(
        xml_subs,
        return_source=True,
        style=st_with_yellow,
        highlights=[],
        emit=lambda *a, **k: None,
    )
    assert jsx_no_yellow == jsx_with_yellow
