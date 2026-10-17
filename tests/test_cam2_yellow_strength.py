# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""«Сила жёлтых» у Камеры 2: свои ручки порога, предела наездов и второго наезда.

Было: группа «Сила жёлтых» стояла только в разделе «Камера 1», а действовала на обе камеры.
Теперь у Камеры 2 — `cam2_hl_zoom_min_pct`, `cam2_hl_zoom_max_per_piece`,
`cam2_hl_zoom_second_min_s`. Пусто в стиле (None) = значение Камеры 1 (парная ручка, как
back_step_after), ноль — своё значение. Способ оценки (`hl_zoom_strength`) общий.
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
from core.xml2ae import layout, plan_camera  # noqa: E402
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


def _call(style):
    return dict(style=dict(style), disclaimer="", intro_riser=False,
                emit=lambda *a, **k: None)


# ---------------------------------------------------------------------------
# 1. Стиль: парная ручка, ноль — своё
# ---------------------------------------------------------------------------

def test_cam2_knobs_follow_cam1_by_default():
    st = read_style(styles.resolve({}))
    assert st.cam2_hl_zoom_min_pct == st.hl_zoom_min_pct == 70.0
    assert st.cam2_hl_zoom_max_per_piece == st.hl_zoom_max_per_piece == 2
    assert st.cam2_hl_zoom_second_min_s == st.hl_zoom_second_min_s == 8.0


def test_cam2_knobs_follow_changed_cam1_until_own_value():
    st = read_style(styles.resolve({"hl_zoom_min_pct": 40.0, "hl_zoom_max_per_piece": 3,
                                    "hl_zoom_second_min_s": 12.0}))
    assert (st.cam2_hl_zoom_min_pct, st.cam2_hl_zoom_max_per_piece,
            st.cam2_hl_zoom_second_min_s) == (40.0, 3, 12.0)


def test_cam2_own_value_wins_and_zero_is_own():
    st = read_style(styles.resolve({"hl_zoom_min_pct": 40.0,
                                    "cam2_hl_zoom_min_pct": 100.0,
                                    "cam2_hl_zoom_second_min_s": 0.0}))
    assert st.hl_zoom_min_pct == 40.0, "ручка камеры 1 не тронута камерой 2"
    assert st.cam2_hl_zoom_min_pct == 100.0
    assert st.cam2_hl_zoom_second_min_s == 0.0, "ноль у камеры 2 — своё значение, не пара"


def test_cam1_knobs_unchanged_by_cam2_values():
    st = read_style(styles.resolve({"cam2_hl_zoom_min_pct": 100.0}))
    assert st.hl_zoom_min_pct == 70.0
    assert styles.BASE["cam2_hl_zoom_min_pct"] is None


# ---------------------------------------------------------------------------
# 2. Чтение ручек в plan_camera: префикс выбирает свою пару
# ---------------------------------------------------------------------------

def test_take_yellow_reads_own_prefix():
    st = read_style(styles.resolve({"hl_zoom_min_pct": 0.0, "cam2_hl_zoom_min_pct": 100.0,
                                    "cam2_hl_zoom_max_per_piece": 1}))
    t1, t2 = {}, {}
    plan_camera._take_yellow(t1, [], None, True, style=st, prefix="cam1")
    plan_camera._take_yellow(t2, [], None, True, style=st, prefix="cam2")
    assert t1["yellow_min_pct"] == 0.0 and t1["yellow_max_per_piece"] == 2
    assert t2["yellow_min_pct"] == 100.0 and t2["yellow_max_per_piece"] == 1


def test_take_yellow_cam2_default_is_cam1_value():
    st = read_style(styles.resolve({"hl_zoom_min_pct": 55.0}))
    t2 = {}
    plan_camera._take_yellow(t2, [], None, True, style=st, prefix="cam2")
    assert t2["yellow_min_pct"] == 55.0


# ---------------------------------------------------------------------------
# 3. Порог меняет наезды: 100 — только самое сильное слово, 0 — каждое
# ---------------------------------------------------------------------------

def _strong_take(min_pct):
    """Один длинный кусок (30 с), три фразы силой 0.9 / 0.5 / 0.2; клип — те же силы."""
    return {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0, "out_s": 2.4,
            "long_on": False, "yellow_on": True, "yellow_strong": True,
            "words": [(300.0, 320.0), (700.0, 720.0), (1100.0, 1120.0)],
            "yellow_strengths": [0.9, 0.5, 0.2],
            "yellow_clip_scores": [0.9, 0.5, 0.2],
            "yellow_min_pct": min_pct, "yellow_max_per_piece": 3,
            "yellow_second_min_s": 1.0}


def test_threshold_100_keeps_only_the_strongest_phrase():
    keys = layout._take_zoom_segment_keys(f=0, seg_end=1800, v=100.0, fps=FPS,
                                          take=_strong_take(100.0), trng=random.Random(42),
                                          lead_min_frame=0)
    assert len(keys) == 4, f"порог 100 — один наезд (4 ключа), получено {len(keys)}"
    assert keys[0][0] == 294.0, "наезд на самое сильное слово (300 − 6)"


def test_threshold_0_keeps_every_phrase():
    keys = layout._take_zoom_segment_keys(f=0, seg_end=1800, v=100.0, fps=FPS,
                                          take=_strong_take(0.0), trng=random.Random(42),
                                          lead_min_frame=0)
    assert len(keys) == 12, f"порог 0 — три наезда (12 ключей), получено {len(keys)}"


def _walk_takes(xml, style, highlights):
    """Перехват takes по камерам: `scene_plan` ставит наезды, и каждый take виден здесь."""
    seen = []
    real = layout._take_zoom_segment_keys

    def rec(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=None):
        if take.get("yellow_on"):
            seen.append(dict(take))
        return real(f, seg_end, v, fps, take, trng, lead_min_frame, base_at=base_at)

    layout._take_zoom_segment_keys = rec
    try:
        xml2ae.scene_plan(xml, highlights=highlights, **_call(style))
    finally:
        layout._take_zoom_segment_keys = real
    return seen


def test_scene_plan_gives_cam2_its_own_threshold(xml_subs):
    """Камера 1 — порог 0 (как задано), камера 2 — порог 100 (своё значение). Проводка через
    plan_camera: в takes обоих камер свои `yellow_min_pct`, а не один общий."""
    style = {"cam1_yellow_zoom": True, "cam2_yellow_zoom": True,
             "cam1_yellow_zoom_strong": True, "cam2_yellow_zoom_strong": True,
             "hl_zoom_min_pct": 0.0, "cam2_hl_zoom_min_pct": 100.0,
             "cam1_zoom": "jump", "cam2_zoom": "jump"}
    hl = list(range(0, 60))
    seen = _walk_takes(xml_subs, style, hl)
    assert seen, "наезды на жёлтые не дошли до сборки"
    mins = {t["yellow_min_pct"] for t in seen}
    assert mins == {0.0, 100.0}, f"пороги обеих камер: {mins} (ждали 0.0 и 100.0)"


# ---------------------------------------------------------------------------
# 4. Схема: группа камеры 2, пары и подписи
# ---------------------------------------------------------------------------

def _fields(items):
    for it in items:
        if it.get("type") == "group":
            yield from _fields(it.get("items", []))
        elif it.get("type") == "field":
            yield it


def test_schema_cam2_strength_group_after_cam2_zoom():
    sch = style_schema.schema()
    sec = {s["id"]: s for s in sch["layers"]}
    c2_ids = [g["id"] for g in sec["cam2"]["items"]]
    assert "cam2.zoom_strength" in c2_ids
    assert c2_ids.index("cam2.zoom_strength") == c2_ids.index("cam2.zoom") + 1
    grp = next(g for g in sec["cam2"]["items"] if g["id"] == "cam2.zoom_strength")
    assert grp["label"] == "Сила жёлтых"
    assert grp["tip"].startswith("Ручки камеры 2.")
    keys = [f["key"] for f in _fields(grp["items"])]
    assert keys == ["cam2_hl_zoom_min_pct", "cam2_hl_zoom_max_per_piece", "cam2_hl_zoom_second_min_s"]
    for f in _fields(grp["items"]):
        assert f.get("nullable") is True
        assert f.get("fallback_key") == f["key"].replace("cam2_", "", 1)


def test_schema_cam1_group_keeps_strength_and_states_shared_method():
    sch = style_schema.schema()
    sec = {s["id"]: s for s in sch["layers"]}
    grp = next(g for g in sec["cam1"]["items"] if g["id"] == "cam1.zoom_strength")
    keys = [f["key"] for f in _fields(grp["items"])]
    assert keys == ["hl_zoom_strength", "hl_zoom_min_pct", "hl_zoom_max_per_piece",
                    "hl_zoom_second_min_s"]
    assert "Способ оценки — общий для обеих камер" in grp["tip"]
    assert "Ручки общие для обеих камер" not in grp["tip"]
    for f in _fields(grp["items"]):
        assert "fallback_key" not in f and not f.get("nullable")
