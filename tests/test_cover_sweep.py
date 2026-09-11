# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кто виден на таймлайне: заметающая прямая обязана давать РОВНО прежний ответ.

`cover_sweep` заменила два одинаковых квадратичных цикла (в `layout._show_segments` и
в `build.virtual_edl`), которые на каждую границу пересматривали весь список клипов.
Ускорение тут — не самоцель: от этой функции зависят и раскладка в AE, и предпросмотр,
и черновик, а расхождение на один элементарный отрезок означает сменившуюся камеру в
готовом ролике — глазами такое ловится только просмотром целиком.

Поэтому сверяемся с ПРЕЖНЕЙ реализацией (она тут же, эталоном) на случайных таймлайнах
и на краевых случаях: стык встык, дырки, перехлёст внутри одной камеры, дубли.

Запуск: python -m pytest reelsi/tests -q
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.xml2ae.layout import cover_sweep  # noqa: E402


def naive(raw):
    """Прежний код, дословно: победитель — max по ci, при равенстве первый в raw."""
    if not raw:
        return []
    bounds = sorted({t for cl in raw for t in (cl[0], cl[1])})
    out = []
    for b0, b1 in zip(bounds, bounds[1:]):
        cover = [k for k, cl in enumerate(raw) if cl[0] <= b0 and cl[1] >= b1]
        if not cover:
            continue
        top = max(raw[k][2] for k in cover)
        out.append((b0, b1, min(k for k in cover if raw[k][2] == top)))
    return out


def test_empty():
    assert cover_sweep([]) == []


def test_single_clip():
    assert cover_sweep([(0, 10, 0)]) == [(0, 10, 0)]


def test_topmost_camera_wins():
    """Верхняя дорожка перекрывает нижнюю ровно на своём куске."""
    raw = [(0, 100, 0), (40, 60, 1)]
    assert cover_sweep(raw) == [(0, 40, 0), (40, 60, 1), (60, 100, 0)]


def test_gap_is_not_covered():
    """Дырка между клипами не отдаётся никому — там в кадре ничего нет.
    Третье поле — ИНДЕКС клипа в raw, поэтому у второго куска это 1, а не номер камеры."""
    raw = [(0, 10, 0), (20, 30, 0)]
    assert cover_sweep(raw) == [(0, 10, 0), (20, 30, 1)]


def test_touching_clips_do_not_leak():
    """Стык встык: конец одного клипа НЕ делает его активным на следующем отрезке.
    Иначе клип камеры 1 «дотягивался» бы до следующего куска и подменял камеру."""
    raw = [(0, 10, 1), (10, 20, 0)]
    assert cover_sweep(raw) == [(0, 10, 0), (10, 20, 1)]
    assert [raw[k][2] for _b0, _b1, k in cover_sweep(raw)] == [1, 0]   # камеры именно такие


def test_overlap_inside_one_camera_keeps_first():
    """Перехлёст внутри камеры — брак нарезки, но выбор должен остаться прежним:
    выигрывает клип, который раньше в XML (verify_jsx на такое ругается отдельно)."""
    raw = [(0, 20, 0), (10, 30, 0)]
    assert cover_sweep(raw) == naive(raw)
    assert cover_sweep(raw)[1][2] == 0          # на 10..20 всё ещё первый клип


def test_matches_naive_on_random_timelines():
    """Главная проверка: 300 случайных таймлайнов, включая вырожденные."""
    rnd = random.Random(20260814)               # фиксированное зерно: падение воспроизводимо
    for n in range(300):
        ncams = rnd.choice([1, 1, 2, 2, 3])
        nclips = rnd.randint(0, 40)
        raw = []
        for _ in range(nclips):
            s = rnd.randrange(0, 200)
            e = s + rnd.randrange(0, 30)        # ноль тоже бывает — клип нулевой длины
            raw.append((s, e, rnd.randrange(ncams)))
        assert cover_sweep(raw) == naive(raw), f"расхождение на случае {n}: {raw}"


def test_matches_naive_on_dense_timeline():
    """Плотная стойка «как в жизни»: сотни коротких клипов подряд на двух камерах."""
    rnd = random.Random(7)
    raw, t = [], 0
    for _ in range(500):
        d = rnd.randint(5, 40)
        raw.append((t, t + d, rnd.randrange(2)))
        t += d - rnd.choice([0, 0, 0, 3])       # изредка перехлёст на 3 кадра
    assert cover_sweep(raw) == naive(raw)


def test_extra_fields_are_ignored():
    """virtual_edl кладёт четвёртым полем вход в исходник — оно не должно мешать."""
    raw = [(0, 100, 0, 555), (40, 60, 1, 777)]
    assert cover_sweep(raw) == [(0, 40, 0), (40, 60, 1), (60, 100, 0)]
