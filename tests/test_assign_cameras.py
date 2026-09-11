# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Раскладка камер: начало и конец на кам1, между ними строгое чередование, а
несходящаяся чётность гасится ОДНИМ сдвоенным кам1 на самой короткой паре соседей.

Правило сформулировано юзером 2026-07-22 и действует на всех путях (нарезка,
редактор нарезки, окно раскладки) — все они зовут align.assign_cameras.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import align


def segs(*durs, gap=0.5):
    """Куски заданной длительности подряд (с зазором — как в реальной нарезке)."""
    out, t = [], 0.0
    for d in durs:
        out.append((t, t + d))
        t += d + gap
    return out


def doubles(assign):
    """Индексы, где камера НЕ сменилась (i-1 и i одинаковые)."""
    return [i for i in range(1, len(assign)) if assign[i] == assign[i - 1]]


def test_odd_count_pure_alternation():
    """Нечётное число кусков: чередование сходится само, сдвоек нет."""
    a = align.assign_cameras(segs(*[3.0] * 7), 2)
    assert a == [0, 1, 0, 1, 0, 1, 0]
    assert not doubles(a)


def test_even_count_doubles_once():
    """Чётное: ровно одна сдвойка; при равных кусках она достаётся камере 1."""
    a = align.assign_cameras(segs(*[3.0] * 8), 2)
    assert a[0] == 0 and a[-1] == 0
    d = doubles(a)
    assert len(d) == 1
    assert a[d[0]] == 0


def test_double_lands_on_shortest_pair():
    """Сдвойку кладём на пару самых КОРОТКИХ соседей — её меньше видно."""
    a = align.assign_cameras(segs(4.0, 4.0, 4.0, 0.4, 0.3, 4.0, 4.0, 4.0), 2)
    assert doubles(a) == [4]                    # пара (3,4) — те самые коротыши
    assert a == [0, 1, 0, 1, 1, 0, 1, 0]


def test_double_never_at_the_start():
    """Даже если самые короткие куски в самом начале — первый кусок в пару не берём:
    ролик обязан открыться чистой сменой планов."""
    a = align.assign_cameras(segs(0.3, 0.3, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0), 2)
    assert doubles(a)[0] >= 2
    assert a[0] == 0 and a[1] != 0


def test_ties_prefer_the_end():
    """Все куски равны — сдвойка уезжает как можно ближе к концу."""
    a = align.assign_cameras(segs(*[3.0] * 10), 2)
    assert doubles(a) == [9]
    assert a == [0, 1, 0, 1, 0, 1, 0, 1, 0, 0]


def test_edges_are_cam1_for_any_length():
    """Первый и последний кусок — камера 1 при любом числе кусков и камер."""
    for n_cams in (2, 3, 4):
        for k in range(1, 25):
            a = align.assign_cameras(segs(*[2.0] * k), n_cams)
            assert len(a) == k
            assert a[0] == 0, (n_cams, k)
            assert a[-1] == 0, (n_cams, k)
            assert len(doubles(a)) <= 1, (n_cams, k)


def test_three_cams_share_the_off_slots():
    """При 3+ камерах ракурсы 2 и 3 делятся поровну, и один и тот же не-первый
    ракурс никогда не идёт подряд."""
    a = align.assign_cameras(segs(*[3.0] * 9), 3)
    assert a[0] == 0 and a[-1] == 0
    assert abs(a.count(1) - a.count(2)) <= 1
    assert all(not (a[i] == a[i - 1] != 0) for i in range(1, len(a)))


def test_three_cams_keep_big_chunk_on_cam1():
    """3+ камеры: большой кусок (>= 6с) держим на камере 1 — правило нужно именно
    здесь, при двух камерах оно ломало бы чередование."""
    a = align.assign_cameras(segs(2.0, 2.0, 2.0, 12.0, 2.0, 2.0, 2.0), 3)
    assert a[3] == 0, a
    assert a[0] == 0 and a[-1] == 0


def test_two_cams_ignore_big_chunks():
    """Две камеры: большой кусок НЕ ломает чередование."""
    a = align.assign_cameras(segs(2.0, 2.0, 2.0, 12.0, 2.0, 2.0, 2.0), 2)
    assert a == [0, 1, 0, 1, 0, 1, 0]


def test_single_camera_is_all_zero():
    assert align.assign_cameras(segs(*[3.0] * 5), 1) == [0] * 5


def test_two_segments_are_both_cam1():
    """Два куска: начало И конец должны быть кам1 — чередоваться негде."""
    assert align.assign_cameras(segs(3.0, 3.0), 2) == [0, 0]
