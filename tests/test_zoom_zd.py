# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты:
1. _cam1_follow_keys с min_scale: зум-ключи 200% (HOLD) -> 300% (плавно) -> 200%;
   голова уходит вправо. На 200% поправка 0 (при min_scale=250), на 300% — ненулевая,
   после отъезда возвращается к 0 (последний ключ клипа == 0). min_scale=0 — ключи
   равны нынешним.
2. Кат в клип ниже порога при ненулевой поправке — ключ на кате == 0.
3. _cam1_jump_keys с words: один длинный тейк, фраза хайлайта на кадре 900 — наезд
   на самой фразе (a = 894 за 0.1 с до слова, b = 930 — через 0.6 с), а свободный
   наезд строится в промежутке до неё; без words — прежний свободный цикл посередине.
4. Фраза слишком близко к концу куска (пик не влезает) — цикла нет, работает длинный
   наезд куска; фраза, у которой пик успевает, но отъезд нет, даёт два ключа.
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
    """3. _cam1_jump_keys с words: фраза хайлайта наезжает на себя, свободный наезд
    строится в самом длинном свободном промежутке до неё.

    Кусок 0..1500: фраза на кадре 900 (a = 894 — за 0.1 с до слова, b = 930 — через
    0.6 с, c = 1050, d = 1194). Свободный промежуток перед фразой — 0..834 (первое
    слово фразы минус зазор 1 с), в нём удержание 2 с по центру промежутка (b = 357)
    с прежним подъездом 1.6 с: a = 261, c = 477, d = 621.
    """
    fps = 60.0
    cams = [{"clips": [[0, 1500, 0, "cam1.mov", True]]}]
    take_base = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}

    # Без words: центр удержания 2.0 с в середине куска 0..1500 (середина 750: 690..810,
    # подъезд 1.6 с = 96 кадров -> a = 594, b = 690, c = 810, d = 954)
    keys_no_words = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_base)
    extra_no_words = [k for k in keys_no_words if k[0] > 0]
    assert len(extra_no_words) == 4
    assert extra_no_words[0][0] == 594.0  # 690 - 96 (подъезд 1.6 с к пику 690)
    assert extra_no_words[1][0] == 690.0  # пик начала удержания (середина 750 - hold/2 60)
    assert extra_no_words[2][0] == 810.0  # конец удержания (690 + hold 120)
    assert extra_no_words[3][0] == 954.0  # возврат в 100% (810 + out 144)

    # С words: фраза на кадре 900 наезжает на себя (894, 930, 1050, 1194),
    # а свободный наезд в окне 0..834 (b = 357, a = 261) идёт первым
    take_words = dict(take_base, words=[900])
    keys_words = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_words)
    extra_words = [k for k in keys_words if k[0] > 0]
    assert len(extra_words) == 8
    # Первый свободный цикл в окне 0..834 (центр удержания 417: b=357, c=477, a=261, d=621)
    assert extra_words[0][0] == 261.0
    assert extra_words[1][0] == 357.0
    assert extra_words[2][0] == 477.0
    assert extra_words[3][0] == 621.0
    # Второй цикл — наезд на фразу 900: a=894 (900−6), b=930 (894+36), c=1050 (930+120), d=1194 (1050+144)
    assert extra_words[4][0] == 894.0
    assert extra_words[5][0] == 930.0
    assert extra_words[6][0] == 1050.0
    assert extra_words[7][0] == 1194.0


def test_cam1_jump_keys_phrase_too_close_to_cut():
    """4. Фраза ближе минимального подъезда к концу куска не наезжает вовсе: пик
    (t_start + 0.6 с) не влезает в кусок минус хвост — остаётся только длинный цикл.

    Фраза на кадре 1470 в куске 0..1500: a = 1464, b = 1500 > 1500 − 18 даже с
    подъездом-минимумом (1464 + 30 = 1494) — цикла нет, длинный наезд куска
    (a=594, b=690, c=810, d=954) остаётся.
    """
    fps = 60.0
    cams = [{"clips": [[0, 1500, 0, "cam1.mov", True]]}]
    take_base = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}

    take_close = dict(take_base, words=[1470])
    keys = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_close)
    extra = [k for k in keys if k[0] > 0]
    assert len(extra) == 4, f"фраза у среза не должна наезжать: ключей {len(extra)}"
    assert extra[0][0] == 594.0  # 690 − 96 (подъезд длинного цикла 1.6 с)
    assert extra[1][0] == 690.0  # пик длинного цикла (середина 750 − hold/2 60)

    # Слово, у которого подъезд до пика успевает, — наезжает: фраза 1300 (a = 1294,
    # b = 1330, c = 1450) отъезда не получает (d = 1594 > 1482) — два ключа, а
    # свободный промежуток 0..1234 (фраза минус зазор 1 с) получает длинный цикл
    # (a=461, b=557, c=677, d=821).
    take_ok = dict(take_base, words=[1300])
    extra_ok = [k for k in layout._cam1_jump_keys(cams, fps=fps, start=False, take=take_ok) if k[0] > 0]
    assert [k[0] for k in extra_ok] == [461.0, 557.0, 677.0, 821.0, 1294.0, 1330.0]


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
