# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дрейф под наездами и плавный наезд с откатом на одной камере.

Было два разрыва:
  1. дрейф Камеры 1 с наездами на жёлтые: циклы наезда шли от ПОСТОЯННОГО значения, дрейфа до
     первого наезда не было, цель дрейфа ставилась только после последнего — видны были одни наезды;
  2. плавный наезд с откатом (pulse) на ОДНОЙ камере: импульсы ставились только на возвратах на
     камеру 1, а их у одной камеры нет — оставался фикс-шаблон `DEFAULT_CAM1_SCALE`.

Теперь наезд куска считается от линии дрейфа v→w (цель w выбрана до наездов), а импульс у одной
камеры — на каждой склейке Камеры 1. Ключи без наездов и ключи двух камер — прежние: числа ниже
сняты с версии до правки (git HEAD) на той же фикстуре.
Кадры: fps 60; наезд: подъезд за 0.1 с до фразы (6 кадров), подъезд 0.6 с (36), хвост 0.3 с (18).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.xml2ae import layout  # noqa: E402

FPS = 60.0

# Одна камера, две склейки: кусок 0..900 и 900..1800 (склейка Камеры 1 на кадре 900)
CAMS_ONE_CUT = [{"clips": [[0, 900, 0, 900, True], [900, 1800, 900, 1800, True]]}]
# Одна камера, четыре куска: склейки на 600, 1200, 1800
CAMS_THREE_CUTS = [{"clips": [[0, 600, 0, 600, True], [600, 1200, 600, 1200, True],
                              [1200, 1800, 1200, 1800, True], [1800, 2400, 1800, 2400, True]]}]

NO_CYCLES = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
             "long_on": False, "yellow_on": False, "words": []}


def _yellow(words, **extra):
    take = {"min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 1.0, "out_s": 1.0,
            "long_on": False, "yellow_on": True, "words": words}
    take.update(extra)
    return take


def _drift_line(keys, f0, f1):
    """Прямая дрейфа v→w по ключам куска: v — ключ в кадре f0, w — ключ в кадре f1."""
    v = next(k[1] for k in keys if k[0] == f0)
    w = next(k[1] for k in keys if k[0] == f1)
    return lambda t: v + (w - v) * (t - f0) / (f1 - f0)


# ---------------------------------------------------------------------------
# 1. Дрейф под наездами на жёлтые
# ---------------------------------------------------------------------------

def test_drift_cycles_ride_the_drift_line():
    """Фраза на 1200: наезд второго куска (900..1800) лежит на прямой дрейфа 900→1799.
    Подъезд a и отъезд d — на прямой (±0.2); цель дрейфа (1799, w) стоит в конце куска."""
    keys = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                   take=_yellow([(1200.0, 1230.0)]))
    line = _drift_line(keys, 900, 1799)
    piece = [k for k in keys if 900 <= k[0] <= 1799]
    a_keys = [k for k in piece if k[2] == 1 and k[3] == 0 and k[0] < 1799]
    d_keys = [k for k in piece if k[2] == 3 and k[3] == 1]
    assert a_keys and d_keys, f"наезд на жёлтое в куске не поставлен: {piece}"
    for k in a_keys + d_keys:
        assert abs(k[1] - line(k[0])) <= 0.2, f"ключ {k} не на линии дрейфа ({line(k[0]):.1f})"
    assert piece[-1][0] == 1799 and piece[-1][1] == keys[-1][1], "цель дрейфа не стоит в конце куска"


def test_drift_moves_before_the_first_push():
    """До первого наезда значение уже ДРЕЙФУЕТ: подъезд стоит не на cut-значении v, а на линии."""
    keys = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                   take=_yellow([(1200.0, 1230.0)]))
    v = next(k[1] for k in keys if k[0] == 900)
    a_val = next(k[1] for k in keys if k[0] == 1194)
    assert abs(a_val - v) > 5.0, f"подъезд стоит на {a_val}, как и значение среза {v}: дрейфа нет"


def test_drift_without_cycles_is_byte_identical_to_before():
    """Без наездов (и без take) ключи дрейфа — те же, что до правки (числа сняты с HEAD)."""
    keys_none = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                        take=None)
    assert keys_none == [(0, 182.0, 1), (62, 100.0, 2), (899, 133.7, 0), (900, 157.9, 0),
                         (1799, 102.5, 0)]
    keys_take = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                        take=dict(NO_CYCLES))
    assert keys_take == [(0, 182.0, 1, 0), (62, 100.0, 2, 0), (899, 133.7, 0, 0), (900, 157.9, 0, 0),
                         (1799, 102.5, 0, 0)]


def test_drift_with_cycles_pinned_numbers():
    """Наезд под дрейфом — новые числа (сняты после правки): подъезд 1194 на 139.8 и не на 157.9."""
    keys = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                   take=_yellow([(1200.0, 1230.0)]))
    assert keys == [(0, 182.0, 1, 0), (62, 100.0, 2, 0), (899, 133.7, 0, 0), (900, 157.9, 0, 0),
                    (1194.0, 139.8, 1, 0), (1230.0, 189.7, 2, 1), (1290.0, 184.6, 3, 0),
                    (1350.0, 130.2, 3, 1), (1799, 102.5, 0, 0)]


# ---------------------------------------------------------------------------
# 2. Импульс на одной камере — на каждой склейке
# ---------------------------------------------------------------------------

def test_one_camera_pulse_lands_on_each_cut():
    """Три склейки Камеры 1 — три импульса на них (пик в [112, 140], откат в 100 за 62 кадра)."""
    keys = layout._cam1_zoom_keys(CAMS_THREE_CUTS, big=182, lo=112, hi=140, fps=FPS, start=True,
                                  take=None)
    frames = [k[0] for k in keys]
    for cut in (600, 1200, 1800):
        peak = next(k for k in keys if k[0] == cut)
        assert 112 <= peak[1] <= 140, f"пик на склейке {cut}: {peak}"
        assert (cut + 62, 100.0) in [(k[0], k[1]) for k in keys], f"нет отката за 62 кадра после {cut}"
    assert 562 not in frames and 987 not in frames, "остался фикс-шаблон DEFAULT_CAM1_SCALE"
    assert keys[0] == (0, 182) and keys[1] == (62, 100.0)


def test_one_camera_pulse_numbers_are_pinned():
    """Числа импульса одной камеры (seed от склеек) — фиксированы: правило воспроизводимо."""
    keys = layout._cam1_zoom_keys(CAMS_THREE_CUTS, big=182, lo=112, hi=140, fps=FPS, start=True,
                                  take=None)
    assert keys == [(0, 182), (62, 100.0), (600.0, 135.3), (662, 100.0), (1200.0, 119.3),
                    (1262, 100.0), (1800.0, 129.1), (1862, 100.0)]


def test_one_camera_take_splits_pieces_at_cuts():
    """Одна камера с наездом на жёлтое во втором куске: куски — между склейками, а наезд — в 2-м."""
    keys = layout._cam1_zoom_keys(CAMS_THREE_CUTS, big=182, lo=112, hi=140, fps=FPS, start=True,
                                  take=_yellow([(900.0, 930.0)]))
    frames = [k[0] for k in keys]
    for cut in (600, 1200, 1800):
        assert cut in frames, f"импульс на склейке {cut} потерян: {frames}"
    # подъезд за 0.1 с до фразы (900 − 6 = 894), пик через 0.6 с
    assert 894.0 in frames and 930.0 in frames, f"наезд на фразу не поставлен: {frames}"


def test_one_camera_take_piece_bounds_follow_cuts():
    """Куски одной камеры с тейками — отрезки между склейками, а не один кусок на весь ролик."""
    seen = []
    real = layout._take_zoom_segment_keys

    def rec(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=None):
        seen.append((int(f), int(seg_end)))
        return real(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=base_at)

    layout._take_zoom_segment_keys = rec
    try:
        layout._cam1_zoom_keys(CAMS_THREE_CUTS, big=182, lo=112, hi=140, fps=FPS, start=True,
                               take=_yellow([(900.0, 930.0)]))
    finally:
        layout._take_zoom_segment_keys = real
    assert seen == [(0, 600), (600, 1200), (1200, 1800), (1800, 2400)], seen


# ---------------------------------------------------------------------------
# 3. Мутация: линия дрейфа отключена — наезд снова от постоянного значения
# ---------------------------------------------------------------------------

def test_mutation_flat_base_is_caught(monkeypatch):
    """Если наезд считать от постоянного v (base_at=None), тест на прямую краснеет."""
    real = layout._take_zoom_segment_keys

    def flat(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=None):
        return real(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=None)

    monkeypatch.setattr(layout, "_take_zoom_segment_keys", flat)
    keys = layout._cam1_drift_keys(CAMS_ONE_CUT, lo=100, hi=160, fps=FPS, big=182, start=True,
                                   take=_yellow([(1200.0, 1230.0)]))
    line = _drift_line(keys, 900, 1799)
    a = next(k for k in keys if k[0] == 1194)
    with pytest.raises(AssertionError):
        assert abs(a[1] - line(a[0])) <= 0.2
