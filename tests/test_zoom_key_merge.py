# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Ключи зума на ОДНОМ кадре: склейка до holds/ease, иначе AE сдвигает HOLD.

Дефект (стенд AE, 01.10.2026): на склейке сходятся ключ скачка (jump/drift/pulse —
кадр среза) и ключ `a` подъезда хайлайта (`_take_zoom_segment_keys`): при
`a == cur_ref` подъезд встаёт на тот же кадр. AE `setValueAtTime` на занятый момент
НЕ добавляет ключ, а ПЕРЕПИСЫВАЕТ его — ключей в AE меньше, чем в массиве, и
`CAM*_HOLDS`/`CAM*_EASE` по индексам (`holds[k-1]`, `ease[z2]`) съезжают на один:
наезд становится HOLD, удержание — плавным (C1603: 103.9 держится до 2917 и прыгает
в 133.2; слежение под план открывает край кадра).

Тесты:
1. `_merge_same_frame_keys` — значение от последнего, вход (in) от первого, выход
   (out) от последнего; форма ключа (2/3/4 элемента) не меняется.
2. На РЕАЛЬНЫХ ключах генераторов (jump, drift) пара на кадре среза склеивается, и
   `holds`/`ease` склеенного ключа берут вход от скачка, выход от наезда.
3. Та же дверь зовётся в `plan_camera` для кам1 и кам2 — ключи зума плана и
   подстановок .jsx не содержат двух ключей на одном кадре у обеих камер, а .jsx
   сверяет `sc.numKeys` с длиной массива (защита от расхождения с AE).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402
from core.xml2ae.build import read_style  # noqa: E402
from core.xml2ae.layout import (EASE_DEFAULT, _cam1_drift_keys, _cam1_jump_keys,  # noqa: E402
                                _cam1_zoom_keys, _merge_same_frame_keys,
                                _zoom_key_eases, _zoom_key_holds)

FPS = 60
# Камера 1 показывает [0, 600], камера 2 — [600, 1200]: срез показа ровно на 600.
CAMS = [{"clips": [[0, 600, 0, 600, True]]},
        {"clips": [[600, 1200, 600, 1200, True]]}]
CUT = 600.0


def _take(words, long_on=True):
    return {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": long_on, "yellow_on": True, "words": words}


def _frame_pairs(keys):
    """Пары соседних ключей на одном кадре (|Δ| < 0.5 кадра)."""
    return [(a, b) for a, b in zip(keys, keys[1:]) if abs(float(a[0]) - float(b[0])) < 0.5]


def _on_cut(keys):
    return [k for k in keys if abs(float(k[0]) - CUT) < 0.5]


def _js_len(literal):
    """Число ключей в JS-литерале массива [[...],[...]] (внешние скобки не считаем)."""
    s = literal.strip()
    assert s.startswith("[") and s.endswith("]"), s[:60]
    return s[1:-1].count("[")


# --------------------------------------------------------------------------- #
# 1. Правило склейки
# --------------------------------------------------------------------------- #
def test_merge_takes_value_from_last_and_hold_from_first():
    """Остаётся один ключ: значение и 4-е поле — от последнего, форма — как у него."""
    first, last = (1905, 111.8, 1, 0), (1905, 111.8, 0, 1)
    got = _merge_same_frame_keys([first, last])
    # 4-е поле = ВЫХОД ключа (`holds[i]` в JS: отрезок от этого ключа до следующего),
    # поэтому берётся от ПОСЛЕДНЕГО — в AE он и перезаписывает ключ на занятом моменте.
    # Вход (`holds[k-2]` для этого ключа) живёт в 4-м поле ПРЕДЫДУЩЕГО ключа и не тронут.
    assert got == [(1905.0, 111.8, 0, True)], got
    assert got[0][1] == last[1], "значение — от последнего"
    assert got[0][2] == last[2], "режим для ease — от последнего"
    assert got[0][3] == bool(last[3]), "выход (4-е поле) — от последнего"
    assert first[3] != bool(last[3]), "фикстура: у ключей кадра разные выходы"
    assert _zoom_key_holds(got) == [True]
    assert len(_zoom_key_eases(got)) == len(got) == len(_zoom_key_holds(got))

    # Три ключа на одном кадре: значение, режим и выход — от последнего.
    got3 = _merge_same_frame_keys([(100, 100.0, 0, 1), (100, 120.0, 1, 0), (100, 130.0, 2, 0)])
    assert got3 == [(100.0, 130.0, 2, False)], got3

    # Разные формы ключа: 4-элементный (take, hold 0) на месте 2-элементного (pulse).
    got_mixed = _merge_same_frame_keys([(100, 100.0), (100, 130.0, 1, 0)])
    assert got_mixed == [(100.0, 130.0, 1, False)], got_mixed

    # 3-элементные (drift): режим — от последнего, форма та же.
    got3e = _merge_same_frame_keys([(600, 114.4, 0), (600.0, 114.4, 1)])
    assert got3e == [(600.0, 114.4, 1)], got3e


def test_merge_keeps_key_shape_and_tolerance():
    """Форма ключа (2/3/4 элемента) и его тип (tuple/list) не меняются; >0.5 кадра — не пара."""
    assert _merge_same_frame_keys([(0, 100.0), (0, 120.0)]) == [(0.0, 120.0)]
    assert _merge_same_frame_keys([(0, 182.0, 1), (0, 100.0, 2)]) == [(0.0, 100.0, 2)]
    assert _merge_same_frame_keys([(0, 100.0, 1, 0), (1, 100.0, 2, 1)]) == [
        (0, 100.0, 1, 0), (1, 100.0, 2, 1)]
    assert _merge_same_frame_keys(None) == []
    assert _merge_same_frame_keys([(0, 100.0)]) == [(0, 100.0)]
    # Список остаётся списком (ручной cam1_scale из тестов и превью).
    assert _merge_same_frame_keys([[0, 100.0], [0, 120.0]]) == [[0.0, 120.0]]
    # Вход от первого, значение от последнего: кадр берётся у последнего.
    assert _merge_same_frame_keys([(10, 5.0, 0, 1), (10.4, 7.0, 3, 0)]) == [(10.4, 7.0, 3, False)]


# --------------------------------------------------------------------------- #
# 2. Реальные генераторы: склейка и HOLD/ease склеенного ключа
# --------------------------------------------------------------------------- #
def _real_keys(mode, words, long_on=True):
    take = _take(words, long_on)
    if mode == "jump":
        return _cam1_jump_keys(CAMS, lo=100, hi=140, fps=FPS, start=True, big=182, take=take)
    if mode == "drift":
        return _cam1_drift_keys(CAMS, lo=100, hi=160, fps=FPS, big=182, start=True, take=take)
    return _cam1_zoom_keys(CAMS, big=182, lo=112, hi=140, fps=FPS, start=True, take=take)


def test_jump_take_on_the_cut_frame_is_one_key_with_take_hold():
    """jump: ключ скачка и подъезд подъезда хайлайта на кадре среза — один ключ.

    Вход склеенного ключа — от скачка (его 4-й элемент), выход — от подъезда (его
    режим и hold): наезд после склейки идёт своей кривой, а не HOLD скачка.
    """
    keys = _real_keys("jump", [(CUT, CUT + 30)])
    pairs = _frame_pairs(keys)
    assert pairs, "фикстура обязана давать ключи на одном кадре (иначе тест пустой)"
    on_cut = _on_cut(keys)
    assert len(on_cut) == 2, on_cut

    got = _merge_same_frame_keys(keys)
    assert not _frame_pairs(got), f"после склейки остались ключи на одном кадре: {_frame_pairs(got)}"
    merged = _on_cut(got)
    assert len(merged) == 1, merged
    first, last = on_cut[0], on_cut[-1]
    # Значение, режим для ease и 4-е поле (выход ключа) — от последнего (подъезда):
    # в AE ключ на занятом моменте перезаписывает именно его.
    assert merged[0][1] == last[1], "значение обязано быть от последнего ключа кадра (наезда)"
    assert merged[0][2] == last[2], "режим для ease — от наезда"
    assert merged[0][3] == bool(last[3]), "4-е поле (выход) — от наезда"
    assert first[3] != bool(last[3]), "фикстура: у ключей кадра разные выходы"
    # Длины holds/ease — ровно по числу ключей массива (AE получит столько же).
    assert len(_zoom_key_holds(got)) == len(got)
    assert len(_zoom_key_eases(got)) == len(got)


def test_drift_take_on_the_cut_frame_keeps_drift_out():
    """drift: ключи дрейфа и подъезд на кадре среза склеиваются в один; выход — от последнего."""
    keys = _real_keys("drift", [(CUT, CUT + 30)])
    pairs = _frame_pairs(keys)
    assert pairs, "фикстура обязана давать ключи на одном кадре"
    on_cut = _on_cut(keys)
    assert len(on_cut) >= 2, on_cut

    got = _merge_same_frame_keys(keys)
    assert not _frame_pairs(got), f"после склейки остались ключи на одном кадре: {_frame_pairs(got)}"
    merged = _on_cut(got)
    assert len(merged) == 1, merged
    assert merged[0][1] == on_cut[-1][1], "значение — от последнего ключа кадра"


def test_pulse_has_no_collision_on_cut_frame():
    """pulse: ключи 2-элементные, подъезд хайлайта на кадр среза не встаёт — пар нет.

    Склейка для pulse — та же дверь, но на этом входе она ничего не меняет: второй
    копии правила у режима нет.
    """
    keys = _real_keys("pulse", [(CUT, CUT + 30)])
    assert not _frame_pairs(keys), f"pulse неожиданно дал пару на одном кадре: {_frame_pairs(keys)}"
    assert _merge_same_frame_keys(keys) == list(keys)


# --------------------------------------------------------------------------- #
# 3. Дверь plan_camera: в плане и в подстановках .jsx двух ключей на одном кадре нет
# --------------------------------------------------------------------------- #
def test_plan_and_jsx_arrays_have_no_two_keys_on_one_frame():
    """Ключи зума плана и подстановок .jsx для кам1 и кам2 — без пар на одном кадре.

    Собирается дверью `plan_camera` на кейсе настоящей коллизии: склейка обязана
    стоять ДО holds/ease, поэтому её видят и план (`zoom.keys`, `zoom.cam2.keys`), и
    подстановки (`CAM1_SCALE`/`CAM2_SCALE`).
    """
    from core.xml2ae.plan_camera import CameraInputs, plan_camera

    style = read_style(styles.resolve({
        "cam1_zoom": "jump", "cam2_zoom": "jump",
        "cam1_take_zoom": True, "cam1_yellow_zoom": True,
        "cam2_take_zoom": True, "cam2_yellow_zoom": True,
    }))
    words = [(0.0, 0.5, "a"), (10.0, 10.5, "b")]
    inp = CameraInputs(cams=CAMS, meta={"w": 1080, "h": 1920, "fps": FPS, "dur": 1200},
                       fps=FPS, subs=words, hl={0}, style=style,
                       cam1_scale=None, roto=False, xml_path="", cam1_frame=None)
    plan = plan_camera(inp)

    assert plan.cam1_scale and plan.cam2_scale, "обе камеры обязаны дать ключи"
    assert _frame_pairs(plan.cam1_scale) == [], "кам1: в ключах плана два ключа на одном кадре"
    assert _frame_pairs(plan.cam2_scale) == [], "кам2: в ключах плана два ключа на одном кадре"
    assert len(plan.holds) == len(plan.cam1_scale)
    assert len(plan.cam2_holds) == len(plan.cam2_scale)
    assert len(plan.zoom["ease"]) == len(plan.zoom["keys"])
    assert len(plan.zoom["cam2"]["ease"]) == len(plan.zoom["cam2"]["keys"])

    # Подстановки .jsx: у кам1 массив — сам литерал, у кам2 — внутри var CAM2_SCALE.
    assert _js_len(plan.cam1scale_js) == len(plan.cam1_scale), plan.cam1scale_js
    m2 = re.search(r"var CAM2_SCALE=(\[.*?\]);", plan.cam2_js)
    assert m2, plan.cam2_js
    assert _js_len(m2.group(1)) == len(plan.cam2_scale), m2.group(1)


def test_jsx_scale_block_logs_numkeys_mismatch():
    """Защита в .jsx: `sc.numKeys` сверяется с длиной массива, расхождение — в лог."""
    from core.xml2ae.plan_camera import _zoom_keys_js
    js = _zoom_keys_js("cam2null", "[]", EASE_DEFAULT, scale_name="CAM2_SCALE",
                       holds_name="CAM2_HOLDS", ease_name="CAM2_EASE")
    assert "if (sc.numKeys !== CAM2_SCALE.length)" in js
    assert '"ключи зума: в AE " + sc.numKeys + ", в плане " + CAM2_SCALE.length' in js
    assert "ease/HOLD могут сдвинуться" in js
