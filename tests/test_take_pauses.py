# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Паузы наездов камеры: после склейки и между наездами — ручки стиля для каждой камеры.

Было: цикл наезда мог начаться прямо на склейке, а следующий — через жёсткую 1 с после
прошлого отъезда; камера 2 не знала про наезды камеры 1. Теперь:
  * `cam1_take_after_cut` / `cam2_take_after_cut` — наезд не раньше склейки + N с;
  * `cam1_take_gap` / `cam2_take_gap` — следующий наезд не раньше конца прошлого + N с;
  * окна уже поставленных циклов ОБЕИХ камер лежат в общем списке `take["busy"]`.

Пустое `cam2_*` в стиле = значение Камеры 1 (парная ручка, как back_step_after).
Кадры: fps 60, отступ подъезда до фразы 0.1 с (6 кадров), подъезд 0.6 с (36), хвост 0.3 с (18).
"""
import gzip
import os
import random
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402
from core.xml2ae.plan_style import read_style  # noqa: E402

FPS = 60.0


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _take(words, hold_s=2.0, out_s=2.4, **extra):
    """Жёлтый наезд без длинного куска: фразы (t_start, t_end) в кадрах."""
    take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": hold_s, "out_s": out_s,
            "long_on": False, "yellow_on": True, "words": words}
    take.update(extra)
    return take


def _starts(keys):
    """Кадры подъездов: в каждом цикле первый ключ — подъезд с ЗНАЧЕНИЕМ покоя (hold 1)."""
    return [k[0] for k in keys if k[2] == 1 and k[3] == 0]


def _run(f, seg_end, take, lead_min_frame=None):
    return layout._take_zoom_segment_keys(
        f=f, seg_end=seg_end, v=100.0, fps=FPS, take=take,
        trng=random.Random(42), lead_min_frame=f if lead_min_frame is None else lead_min_frame)


# ---------------------------------------------------------------------------
# 1. Пауза после склейки
# ---------------------------------------------------------------------------

def test_phrase_right_after_cut_without_pause_is_as_before():
    """Ноль паузы — прежнее поведение: подъезд за 0.1 с до фразы, т.е. на кадре 6 куска с нуля."""
    keys = _run(0, 600, _take([(12.0, 24.0)], after_cut_s=0.0))
    assert keys[0][0] == 6.0


def test_phrase_right_after_cut_waits_after_cut_s():
    """Фраза через 0.2 с после склейки, пауза 1.0 с: подъезд не раньше кадра 60 (склейка + 1 с)."""
    keys = _run(0, 600, _take([(12.0, 24.0)], after_cut_s=1.0))
    assert keys, "наезд должен остаться: фраза со удержанием ещё звучит"
    assert keys[0][0] == 60.0, f"подъезд на {keys[0][0]}, должен быть на 60 (склейка + 1 с)"


def test_pause_after_cut_counts_from_piece_start_not_from_zero():
    """Пауза считается от склейки КУСКА (его первого кадра), а не от нуля ролика."""
    keys = _run(300, 900, _take([(312.0, 324.0)], after_cut_s=1.0))
    assert keys[0][0] >= 360.0, f"подъезд {keys[0][0]} раньше склейки 300 + 60"


def test_long_cut_waits_after_cut_s_and_centers_later():
    """Длинный кусок (12 с) без жёлтых: первый промежуток начинается после склейки + пауза,
    центр удержания сдвигается вправо (старый центр — кадр 360)."""
    take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": True, "yellow_on": False, "words": [], "after_cut_s": 1.0}
    keys = _run(0, 720, take)
    assert len(keys) == 4
    k_a, k_b, k_c, _k_d = keys
    assert k_a[0] >= 60.0, f"наезд длинного куска начался на {k_a[0]}, раньше склейки + 1 с"
    center = (k_b[0] + k_c[0]) / 2.0
    assert center > 360.0, f"центр {center} не сдвинулся после паузы (было 360)"


def test_every_push_key_is_after_cut_plus_pause():
    """Все ключи наезда (подъезд, пик, отъезд) лежат не раньше склейки + пауза."""
    keys = _run(0, 600, _take([(12.0, 24.0)], after_cut_s=1.0))
    assert all(k[0] >= 60.0 for k in keys), "ни один ключ наезда не раньше склейки + пауза"


# ---------------------------------------------------------------------------
# 2. Пауза между наездами
# ---------------------------------------------------------------------------

def test_second_push_respects_gap_after_first_end():
    """Две фразы через 1.5 с, пауза 2.0 с: второй наезд (если поставлен) не раньше конца
    первого + 120 кадров; иначе фраза не наезжает вовсе (удержание не успевает к опоре)."""
    gap_f = 120
    keys = _run(0, 1200, _take([(300.0, 320.0), (410.0, 430.0)], gap_s=2.0))
    starts = _starts(keys)
    first_end = keys[3][0]                           # отъезд первого цикла (d)
    assert len(keys) == 4 or starts[1] >= first_end + gap_f, \
        f"второй подъезд {starts[1:]} раньше {first_end} + {gap_f}"


def test_gap_moves_second_push_to_first_end_plus_gap():
    """Точная проверка правила: вторая фраза на кадре 474 при паузе 1 с (как было), а при
    паузе 2 с — подъезд переносится на конец первого (390) + 120 = 510."""
    phrases = [(300.0, 320.0), (480.0, 500.0)]
    short = dict(hold_s=0.5, out_s=0.5)
    old = _run(0, 1200, _take(phrases, gap_s=1.0, **short))
    new = _run(0, 1200, _take(phrases, gap_s=2.0, **short))
    assert old[3][0] == 390.0 and new[3][0] == 390.0, "конец первого цикла одинаков"
    assert _starts(old)[1] == 474.0, f"при паузе 1 с ждали 474, получено {_starts(old)}"
    assert _starts(new)[1] == 510.0, f"при паузе 2 с ждали 510, получено {_starts(new)}"


def test_gap_zero_lets_next_push_start_at_first_end():
    """Пауза 0 между наездами: опора второго — конец первого (390), без добавки.
    Фраза на 400 с подъездом 394 стартует сразу за отъездом; при паузе 1 с — только на 450."""
    phrases = [(300.0, 320.0), (400.0, 420.0)]
    zero = _run(0, 1200, _take(phrases, gap_s=0.0, hold_s=0.5, out_s=0.5))
    one = _run(0, 1200, _take(phrases, gap_s=1.0, hold_s=0.5, out_s=0.5))
    assert _starts(zero)[1] == 394.0
    assert _starts(one)[1] == 450.0


# ---------------------------------------------------------------------------
# 3. Камера 2 видит окна камеры 1 (общий список busy)
# ---------------------------------------------------------------------------

def test_cam2_push_waits_for_cam1_window_plus_gap():
    """Окно камеры 1 (100..400) в busy: цикл камеры 2 с фразой на 420 не раньше 400 + 120 кадров."""
    busy = [(100, 400)]
    take = _take([(420.0, 440.0)], gap_s=2.0, busy=busy)
    keys = _run(400, 1200, take)
    assert _starts(keys)[0] == 520.0, f"наезд камеры 2 на {_starts(keys)[0]}, ждали 520"
    assert busy[-1] == (520, 820), "цикл камеры 2 дописан в общий список окон"


def test_cam1_window_ends_before_phrase_changes_nothing():
    """Окно, которое кончилось задолго до фразы, опору не поднимает."""
    keys = _run(400, 1200, _take([(420.0, 440.0)], gap_s=2.0, busy=[(0, 100)]))
    assert _starts(keys)[0] == 414.0


def test_busy_none_or_empty_is_as_before():
    """Нет списка окон (или он пуст) — пауза между камерами не действует."""
    a = _run(400, 1200, _take([(420.0, 440.0)], gap_s=2.0))
    b = _run(400, 1200, _take([(420.0, 440.0)], gap_s=2.0, busy=[]))
    assert a == b and _starts(a)[0] == 414.0


def test_busy_gets_window_of_every_cycle_without_out_as_seg_end():
    """Цикл без отъезда (пик держится до конца куска) пишет окно до конца куска."""
    busy: list = []
    keys = _run(0, 200, _take([(100.0, 120.0)], busy=busy))
    assert keys, "наезд поставлен"
    assert busy == [(_starts(keys)[0], 200)]


# ---------------------------------------------------------------------------
# 4. Стиль: пауза — ручка, парная ручка камеры 2, схема
# ---------------------------------------------------------------------------

def test_style_defaults_and_cam2_follows_cam1():
    st = read_style(styles.resolve({}))
    assert st.cam1_take_after_cut == 1.0 and st.cam1_take_gap == 2.0
    assert st.cam2_take_after_cut == st.cam1_take_after_cut
    assert st.cam2_take_gap == st.cam1_take_gap


def test_style_cam2_follows_changed_cam1_until_own_value():
    st = read_style(styles.resolve({"cam1_take_after_cut": 0.5, "cam1_take_gap": 3.0}))
    assert (st.cam2_take_after_cut, st.cam2_take_gap) == (0.5, 3.0)


def test_style_cam2_own_value_wins_including_zero():
    st = read_style(styles.resolve({"cam1_take_after_cut": 0.5, "cam1_take_gap": 3.0,
                                    "cam2_take_after_cut": 0.0, "cam2_take_gap": 0.7}))
    assert st.cam1_take_after_cut == 0.5 and st.cam1_take_gap == 3.0
    assert st.cam2_take_after_cut == 0.0, "ноль у камеры 2 — своё значение, не пара"
    assert st.cam2_take_gap == 0.7


def test_style_cam1_zero_gap_is_kept():
    st = read_style(styles.resolve({"cam1_take_gap": 0, "cam1_take_after_cut": 0}))
    assert st.cam1_take_gap == 0.0 and st.cam1_take_after_cut == 0.0


def _fields(items):
    for it in items:
        if it.get("type") == "group":
            yield from _fields(it.get("items", []))
        elif it.get("type") == "field":
            yield it


def test_schema_pause_fields_and_pairs():
    by_key = {}
    for layer in style_schema.LAYERS:
        for f in _fields(layer.get("items", [])):
            by_key.setdefault(f.get("key"), f)
    for pre in ("cam1", "cam2"):
        for suf in ("take_after_cut", "take_gap"):
            assert f"{pre}_{suf}" in by_key, f"нет поля {pre}_{suf} в схеме"
    c1 = by_key["cam1_take_after_cut"]
    assert c1.get("ctl") == "num" and c1.get("min") == 0 and c1.get("max") == 5
    assert c1.get("label") == "Пауза после склейки, с"
    assert by_key["cam1_take_gap"].get("max") == 10
    assert by_key["cam1_take_gap"].get("label") == "Пауза между наездами, с"
    c2 = by_key["cam2_take_after_cut"]
    assert c2.get("nullable") is True and c2.get("fallback_key") == "cam1_take_after_cut"
    g2 = by_key["cam2_take_gap"]
    assert g2.get("nullable") is True and g2.get("fallback_key") == "cam1_take_gap"
    assert "fallback_key" not in c1 and not c1.get("nullable")


def test_base_keys_and_frame_independence():
    """Камера 1 — числа, размечены как не зависящие от кадра; камера 2 — None (парная ручка)."""
    from core import style_geometry
    assert styles.BASE["cam2_take_after_cut"] is None and styles.BASE["cam2_take_gap"] is None
    for k in ("cam1_take_after_cut", "cam1_take_gap"):
        assert k in style_geometry.FRAME_INDEPENDENT, f"{k} не в FRAME_INDEPENDENT"


# ---------------------------------------------------------------------------
# 1б. Пауза «после склейки» — от КОНЦА импульса на склейке (lead_min_frame > f)
# ---------------------------------------------------------------------------

def test_after_impulse_pause_counts_from_impulse_end():
    """Склейка f=100, импульс до f+62 (lead_min_frame), фраза через 1.1 с после склейки, пауза 1 с:
    подъезд не раньше f + 62 + after = 222. Раньше опора была max(162, 160) = 162 — сразу после
    отката импульса, и пауза на экране не была видна."""
    f = 100
    keys = _run(f, 900, _take([(166.0, 190.0)], after_cut_s=1.0), lead_min_frame=f + 62)
    assert keys, "наезд должен остаться: фраза со удержанием ещё звучит"
    assert keys[0][0] == f + 62 + 60, f"подъезд на {keys[0][0]}, ждали 222 (конец импульса + 1 с)"


def test_without_impulse_pause_counts_from_the_cut():
    """Движения на склейке нет (lead_min_frame == f): пауза от самой склейки, как раньше."""
    f = 100
    keys = _run(f, 900, _take([(166.0, 190.0)], after_cut_s=1.0))
    assert keys[0][0] == f + 60, f"подъезд на {keys[0][0]}, ждали 160 (склейка + 1 с)"


def test_long_cut_after_impulse_starts_from_impulse_end():
    """Длинный кусок 6.7 с без жёлтых, импульс на склейке (lead = f+62): первый промежуток
    начинается с f + 62 + after = 122, а не с 62. Старый промежуток давал подъезд на 80 кадре."""
    take = {"min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": True, "yellow_on": False, "words": [], "after_cut_s": 1.0}
    keys = _run(0, 400, take, lead_min_frame=62)
    assert keys, "длинный наезд поставлен"
    assert keys[0][0] >= 62 + 60, f"наезд длинного куска на {keys[0][0]}, раньше конца импульса + 1 с"


def test_pulse_cut_yellow_push_waits_after_impulse_end():
    """Через `_cam1_zoom_keys` (импульс на каждой склейке Камеры 1): фраза через 1.1 с после второй
    склейки (600). Импульс кончается на 663 (600 + 62 + 1), подъезд — не раньше 723."""
    cams = [{"clips": [[0, 600, 0, 600, True], [600, 1200, 600, 1200, True],
                       [1200, 1800, 1200, 1800, True]]}]
    keys = layout._cam1_zoom_keys(cams, big=182, lo=112, hi=140, fps=FPS, start=True,
                                  take=_take([(666.0, 690.0)], after_cut_s=1.0))
    pushes = [k[0] for k in keys if k[2] == 1 and k[3] == 0 and k[0] > 600]
    assert pushes, f"наезд на фразу не поставлен: {keys}"
    assert pushes[0] == 723, f"подъезд на {pushes[0]}, ждали 723 (конец импульса 663 + 60)"


# ---------------------------------------------------------------------------
# 5. Проводка: plan_camera кладёт в оба словаря один список busy и свои паузы
# ---------------------------------------------------------------------------

def test_scene_plan_passes_shared_busy_and_own_pauses(xml_subs, monkeypatch):
    seen = []
    real = layout._take_zoom_segment_keys

    def rec(f, seg_end, v, fps, take, trng, lead_min_frame):
        seen.append(dict(take))
        return real(f, seg_end, v, fps, take, trng, lead_min_frame)

    monkeypatch.setattr(layout, "_take_zoom_segment_keys", rec)
    style = {
        "cam1_zoom": "jump", "cam2_zoom": "jump",
        "cam1_take_zoom": True, "cam1_take_min": 3.0,
        "cam2_take_zoom": True, "cam2_take_min": 4.5,
        "cam1_take_after_cut": 0.5, "cam1_take_gap": 1.5,
        "cam2_take_after_cut": 0.25, "cam2_take_gap": 0.75,
    }
    xml2ae.scene_plan(xml_subs, style=dict(style), disclaimer="", intro_riser=False,
                      emit=lambda *a, **k: None)
    c1 = [t for t in seen if t.get("min_s") == 3.0]
    c2 = [t for t in seen if t.get("min_s") == 4.5]
    assert c1 and c2, "обе камеры должны дойти до наездов в тейках"
    assert all(t["after_cut_s"] == 0.5 and t["gap_s"] == 1.5 for t in c1)
    assert all(t["after_cut_s"] == 0.25 and t["gap_s"] == 0.75 for t in c2)
    assert len({id(t["busy"]) for t in c1 + c2}) == 1, "busy должен быть один список на обе камеры"
