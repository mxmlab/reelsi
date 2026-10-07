# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание VO: наезд хайлайта на самой фразе.

Стерегут четыре вещи:

1. Фраза хайлайта 24.33–25.7 с (слова 54–55 клипа 25_C1606-009) в куске 22.65–30 с:
   подъезд начинается за 0.1 с до первого слова, пик приходит на 0.6 с внутрь фразы,
   удержание считается СВЕРХ конца фразы, а отъезд ставится, только если целиком
   помещается до склейки. Кусок до 30 с — отъезда нет (пик держится до склейки),
   тот же кусок до 35 с — отъезд есть и равен «Отъезд, с».
2. Хайлайт в куске, который длинного наезда не заслуживает (3 с в куске 0–6 с),
   всё равно наезжает: кусок короче порога длинного куска.
3. Две фразы в одном куске, куда отъезд не помещается: второй наезд не ставится —
   камера уже приближена, пик держится один.
4. Слова интро, выделенные цветом (color yellow/accent), попадают в те же фразы:
   индексы берутся из intro_remove, времена — из censor_source (слова до вырезания).
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
sys.path.insert(0, HERE)

from core import style_schema, xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402
from core.xml2ae.plan_camera import _intro_hl_words, _yellow_phrases  # noqa: E402

FPS = 60.0


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _take(words, **kw):
    take = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": False, "yellow_on": True, "words": words}
    take.update(kw)
    return take


def _keys(seg_end, words, f=0, lead=0, **kw):
    return layout._take_zoom_segment_keys(
        f=f, seg_end=seg_end, v=100.0, fps=FPS,
        take=_take(words, **kw), trng=random.Random(42), lead_min_frame=lead)


# ---------------------------------------------------------------------------
# 1. Фраза 24.33–25.7 с: наезд на слове, удержание сверх фразы, отъезд только целиком
# ---------------------------------------------------------------------------

def test_phrase_take_in_on_first_word_hold_beyond_end_no_out_at_cut():
    """Кусок 22.65–30 с, фраза 24.33–25.7 с, hold=2, out=2.4.

    Числа выведены из правила, а не сняты с прежнего поведения:
    a = t_start − 0.1 с = 24.23 с (1454 кадра), b = a + 0.6 с = 24.83 с (1490),
    c = max(t_end, b) + hold = max(25.7, 24.83) + 2 = 27.7 с (1662),
    d = c + 2.4 = 30.1 с (1806) — за куском (30 с минус хвост 0.3 с = 29.7 с, 1782),
    значит отъезда нет: ключей два, пик держится до склейки.
    """
    frames = int(round(22.65 * FPS))          # 1359 — начало куска
    seg_end = int(round(30.0 * FPS))          # 1800
    words = [(24.33 * FPS, 25.7 * FPS)]

    keys = _keys(seg_end, words, f=frames, lead=frames + 63)
    assert len(keys) == 2, f"отъезд не помещается — ожидались 2 ключа, получено {len(keys)}"
    assert keys[0][0] == 1454.0 and keys[0][1] == 100.0 and keys[0][2] == 1
    assert keys[1][0] == 1490.0 and keys[1][3] == 1, "пик обязан держаться (hold=1) до склейки"

    # Тот же кусок, но до 35 с: отъезд целиком помещается — 4 ключа, d = c + 2.4 с.
    keys35 = _keys(int(round(35.0 * FPS)), words, f=frames, lead=frames + 63)
    assert len(keys35) == 4, f"в куске до 35 с отъезд обязан быть, ключей {len(keys35)}"
    k_a, k_b, k_c, k_d = keys35
    assert (k_a[0], k_b[0], k_c[0]) == (1454.0, 1490.0, 1662.0)
    assert k_d[0] == k_c[0] + round(2.4 * FPS)


def test_phrase_in_short_segment_has_take():
    """Хайлайт на 3 с в куске 0–6 с: кусок короче порога длинного куска (8 с),
    но наезд на фразе есть: a = 2.9 с (174), b = 3.5 с (210), отъезд не влезает
    (c = 5.5 с, d = 7.9 с > 6 с минус хвост) — ключей два."""
    keys = _keys(int(round(6.0 * FPS)), [(3.0 * FPS, 3.2 * FPS)])
    assert len(keys) == 2, f"наезд на хайлайте обязан быть, ключей {len(keys)}"
    assert keys[0][0] == 174.0, "подъезд начинается за 0.1 с до первого слова фразы"
    assert keys[1][0] == 210.0, "пик приходит через 0.6 с после старта подъезда"


def test_second_phrase_without_room_keeps_one_peak():
    """Две фразы в куске, куда отъезд первой не помещается (0–1300 кадров):
    пик один — второй наезд не ставится, камера уже приближена."""
    keys = _keys(1300, [(1000.0, 1020.0), (1100.0, 1120.0)])
    assert len(keys) == 2, f"второго наезда быть не должно, ключей {len(keys)}"
    assert keys[1][0] == 1030.0, "единственный пик — от первой фразы"

    # Для сравнения: в куске, где отъезд первой фразы помещается, наезды стоят оба.
    keys_long = _keys(1600, [(600.0, 620.0), (1000.0, 1020.0)])
    assert len(keys_long) == 8, f"два полных цикла — 8 ключей, получено {len(keys_long)}"


def test_camera_already_zoomed_second_phrase_extends_hold_only():
    """Продление удержания: у цикла без отъезда второй фразы в ключах нет вовсе —
    камера держит пик до склейки (c/d не эмитятся, у пика hold=1)."""
    keys = _keys(1300, [(1000.0, 1020.0), (1240.0, 1260.0)])
    assert [k[0] for k in keys] == [994.0, 1030.0]
    assert keys[-1][3] == 1


# ---------------------------------------------------------------------------
# 2. Группировка слов хайлайта во фразы
# ---------------------------------------------------------------------------

def test_phrase_grouping_by_index_and_time():
    """Подряд идущие слова — одна фраза; разрыв по времени > 0.6 с или по индексу
    (> 1) начинает новую. Конец фразы — конец её последнего слова."""
    items = [(0, 0.0, 10.0), (1, 12.0, 20.0), (2, 60.0, 70.0)]
    assert layout._hl_phrases(items, FPS) == [(0.0, 20.0), (60.0, 70.0)]
    # Пауза 36+1 кадров = больше 0.6 с — новая фраза
    assert layout._hl_phrases([(0, 0.0, 10.0), (1, 47.0, 60.0)], FPS) == [(0.0, 10.0), (47.0, 60.0)]
    # Индекс не соседний — новая фраза, даже когда слова встык
    assert layout._hl_phrases([(0, 0.0, 5.0), (5, 6.0, 9.0)], FPS) == [(0.0, 5.0), (6.0, 9.0)]
    assert layout._hl_phrases([], FPS) == []


# ---------------------------------------------------------------------------
# 3. Слова интро, выделенные цветом
# ---------------------------------------------------------------------------

def test_intro_highlight_words_take_indices_and_times():
    """Индексы — из intro_remove (тот же список, по которому plan_words вынимает слова
    интро), времена начала и конца — из списка слов ДО вырезания (censor_source)."""
    subs_all = [(0.0, 10.0, "КАК"), (20.0, 30.0, "ВЫКЛ"), (40.0, 50.0, "ФЕРМ"), (60.0, 70.0, "ДА")]
    groups = [[{"words": ["ВЫКЛ", "ФЕРМ"], "times": [0.33, 0.66], "color": "yellow"}]]
    words = _intro_hl_words(groups, [1, 2], subs_all)
    assert words == [(20.0, 30.0), (40.0, 50.0)]
    # Строка без цвета (color white) в наезды не идёт; вторая строка забирает свои индексы
    groups2 = [[{"words": ["КАК"], "times": [0.0], "color": "white"},
                {"words": ["ВЫКЛ"], "times": [0.33], "color": "accent"}]]
    assert _intro_hl_words(groups2, [0, 1], subs_all) == [(20.0, 30.0)]
    # Жёлтые слова ролика и слова интро собираются в один список фраз
    subs = [(0.0, 10.0, "КАК"), (40.0, 50.0, "ФЕРМ")]
    assert _yellow_phrases(subs, {0}, [(20.0, 30.0)], FPS) == [(0.0, 10.0), (20.0, 30.0)]


def test_intro_words_join_highlight_phrases(xml_subs):
    """Сборка с выделенной цветом строкой интро: её слова дают наезд, без строки — нет."""
    meta, cams, subs, _xi = xml2ae.parse_full(xml_subs)
    fps = meta["fps"]
    st = {"cam1_zoom": "jump", "cam1_take_zoom": False, "cam1_yellow_zoom": True}

    # Пара соседних слов внутри длинного куска камеры 1: они обязаны собраться в одну
    # фразу (разрыв по времени <= 0.6 с), и наезд по ней обязан уместиться в кусок.
    segs = [(s0, s1) for s0, s1, ci in layout._show_segments(cams)
            if ci == 0 and s1 - s0 > 4 * fps]
    assert segs, "фикстура обязана иметь длинный кусок камеры 1"
    s0, s1 = max(segs, key=lambda p: p[1] - p[0])
    pair = None
    for k in range(len(subs) - 1):
        if (s0 + 0.5 * fps < subs[k][0] < s1 - 3 * fps
                and subs[k + 1][0] - subs[k][0] <= 0.6 * fps):
            pair = (k, k + 1)
            break
    assert pair is not None, "фикстура: не нашлось пары соседних слов для фразы"
    a, b = pair
    row = {"words": [subs[k][2] for k in (a, b)],
           "times": [round(subs[k][0] / fps, 2) for k in (a, b)],
           "color": "yellow"}

    no = xml2ae.scene_plan(xml_subs, style=st, intro=[], intro_remove=[], highlights=[],
                           emit=lambda *x, **k: None)
    yes = xml2ae.scene_plan(xml_subs, style=st, intro=[row], intro_remove=[a, b],
                            highlights=[], emit=lambda *x, **k: None)
    keys_no = no["zoom"]["keys"]
    keys_yes = yes["zoom"]["keys"]
    assert len(keys_yes) > len(keys_no), "строка интро color='yellow' не дала наезда"
    frames = [k[0] for k in keys_yes]
    a_frame = round(subs[a][0] - 0.1 * fps)
    assert a_frame in frames and a_frame + round(0.6 * fps) in frames, \
        "наезд на фразе интро обязан идти по тому же правилу (0.1 с до, 0.6 с подъезд)"


# ---------------------------------------------------------------------------
# 4. Схема: тень прекомпа одной группой, группа хайлайта
# ---------------------------------------------------------------------------

CSHADOW_KEYS = ["intro_comp_shadow_fill", "intro_comp_shadow_opacity",
                "intro_comp_shadow2_fill", "intro_comp_shadow2_opacity",
                "intro_comp_shadow_dir", "intro_comp_shadow_dist",
                "intro_comp_shadow_soft"]


def _intro_layer():
    return next(l for l in style_schema.schema()["layers"] if l["id"] == "intro")


def test_shadow_fields_live_in_one_group():
    """Четыре поля тени прекомпа стоят в intro.cshadow, их нет в группах интро
    «Камера 1»/«Камера 2»; порядок — камера 1, камера 2, затем направление/дистанция/мягкость."""
    intro = _intro_layer()
    cshadow = next(g for g in intro["items"] if g["id"] == "intro.cshadow")
    keys = [it["key"] for it in cshadow["items"] if it.get("type") == "field"]
    assert keys == CSHADOW_KEYS, f"состав/порядок группы intro.cshadow: {keys}"

    labels = [it["label"] for it in cshadow["items"] if it.get("type") == "field"][:4]
    assert labels == ["Цвет, камера 1", "Непрозрачность тени, камера 1, %",
                      "Цвет, камера 2", "Непрозрачность тени, камера 2, %"]

    for gid in ("intro.cam1", "intro.cam2"):
        grp = next(g for g in intro["items"] if g["id"] == gid)
        gkeys = [it.get("key") for it in grp["items"] if it.get("type") == "field"]
        for k in CSHADOW_KEYS[:4]:
            assert k not in gkeys, f"{k} остался в группе {gid}"


def test_yellow_zoom_group_is_highlight_in_schema():
    """Группа camN.zoom_yellow называется «Хайлайт», ключ стиля camN_yellow_zoom не менялся."""
    sch = style_schema.schema()
    for sec in ("cam1", "cam2"):
        layer = next(l for l in sch["layers"] if l["id"] == sec)
        zoom = next(g for g in layer["items"] if g.get("id") == f"{sec}.zoom")
        zy = next(it for it in zoom["items"] if it.get("id") == f"{sec}.zoom_yellow")
        assert zy["label"] == "Хайлайт", f"метка группы {sec}.zoom_yellow: {zy['label']}"
        assert zy["toggle"] == f"{sec}_yellow_zoom", "ключ стиля галки менять нельзя"
