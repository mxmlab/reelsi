# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Комплексные тесты: понятная настройка анимации зума (обе камеры).

Проверяет:
1. Схема: cam1.zoom и cam2.zoom — три подгруппы (zoom_start, zoom_cut, zoom_take)
   с идентичной структурой и контролами ctl="range".
2. Наезд в куске при pulse, drift, jump, none — общая генерация для всех режимов.
3. Правило A: подъезд хайлайта 0.6 с от старта за 0.1 с до слова, удержание — сверх
   конца фразы (а не разгон 1.6 с до слова, как было).
4. Правило B: длинный кусок (14 с) с двумя фразами: цикл получает только та, чей
   отъезд помещается целиком; вторая держит пик до склейки.
5. Режим yellow_mode="only": при отсутствии жёлтых слов наезда нет; при наличии —
   наезд на фразу (пик через 0.6 с после старта подъезда).
6. Миграция cam_zoom_v2:
   - take_yellow (True/False) -> take_yellow_mode ("snap"/"off")
   - camN_zoom == "none" -> camN_zoom_start = False
   - camN_zoom != "jump" и camN_take_zoom == True -> camN_take_zoom = False
   - cam_zoom_v выставляется в 2
7. Сортировка диапазона lo <= hi в styles.resolve().
8. Дефолты не меняют golden_geometry.jsx.
"""
from __future__ import annotations

import gzip
import os
import random
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import style_schema, styles  # noqa: E402
from core.xml2ae import layout  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _find_node_by_id(items: list, node_id: str) -> dict | None:
    for it in items:
        if it.get("id") == node_id:
            return it
        if "items" in it:
            found = _find_node_by_id(it["items"], node_id)
            if found is not None:
                return found
    return None


def test_schema_cam_zoom_subgroups_and_symmetry():
    """Схема: в разделах cam1 и cam2 есть группа camN.zoom «Анимация зума», в ней ровно
    camN.zoom_start, camN.zoom_cut, camN.zoom_take в этом порядке; прямо в разделе камеры их нет.
    """
    for prefix in ("cam1", "cam2"):
        cam_layer = next(l for l in style_schema.LAYERS if l["id"] == prefix)
        top_ids = [it.get("id") for it in cam_layer.get("items", [])]
        assert f"{prefix}.zoom" in top_ids, f"В разделе {prefix} нет группы {prefix}.zoom"
        for sub in ("zoom_start", "zoom_cut", "zoom_take", "zoom_yellow", "zoom_cycle"):
            assert f"{prefix}.{sub}" not in top_ids, (
                f"Подгруппа {prefix}.{sub} не должна лежать прямо в разделе {prefix}"
            )

        g_wrapper = next(it for it in cam_layer["items"] if it.get("id") == f"{prefix}.zoom")
        assert g_wrapper.get("type") == "group"
        assert g_wrapper.get("label") == "Анимация зума"
        assert g_wrapper.get("fx") is False
        assert g_wrapper.get("toggle") is None
        inner_ids = [it.get("id") for it in g_wrapper.get("items", [])]
        assert inner_ids == [
            f"{prefix}.zoom_start",
            f"{prefix}.zoom_cut",
            f"{prefix}.zoom_take",
            f"{prefix}.zoom_yellow",
            f"{prefix}.zoom_cycle",
        ], f"Внутри {prefix}.zoom неверный состав или порядок подгрупп: {inner_ids}"

        g_start = _find_node_by_id(style_schema.LAYERS, f"{prefix}.zoom_start")
        g_cut = _find_node_by_id(style_schema.LAYERS, f"{prefix}.zoom_cut")
        g_take = _find_node_by_id(style_schema.LAYERS, f"{prefix}.zoom_take")
        g_yellow = _find_node_by_id(style_schema.LAYERS, f"{prefix}.zoom_yellow")
        g_cycle = _find_node_by_id(style_schema.LAYERS, f"{prefix}.zoom_cycle")
        assert g_start is not None, f"Подгруппа {prefix}.zoom_start не найдена"
        assert g_cut is not None, f"Подгруппа {prefix}.zoom_cut не найдена"
        assert g_take is not None, f"Подгруппа {prefix}.zoom_take не найдена"
        assert g_yellow is not None, f"Подгруппа {prefix}.zoom_yellow не найдена"
        assert g_cycle is not None, f"Подгруппа {prefix}.zoom_cycle не найдена"

        # zoom_start
        assert g_start.get("toggle") == f"{prefix}_zoom_start"
        start_keys = [it["key"] for it in g_start.get("items", []) if it.get("type") == "field"]
        assert start_keys == [f"{prefix}_zoom_big"]

        # zoom_cut
        cut_fields = [it for it in g_cut.get("items", []) if it.get("type") == "field"]
        assert cut_fields[0]["key"] == f"{prefix}_zoom"
        assert cut_fields[0]["ctl"] == "select"
        # range zoom_lo / zoom_hi
        assert cut_fields[1]["key"] == f"{prefix}_zoom_lo"
        assert cut_fields[1]["key2"] == f"{prefix}_zoom_hi"
        assert cut_fields[1]["ctl"] == "range"
        # range drift_lo / drift_hi
        assert cut_fields[2]["key"] == f"{prefix}_drift_lo"
        assert cut_fields[2]["key2"] == f"{prefix}_drift_hi"
        assert cut_fields[2]["ctl"] == "range"

        # zoom_take (длинный кусок)
        assert g_take.get("toggle") == f"{prefix}_take_zoom"
        take_fields = [it for it in g_take.get("items", []) if it.get("type") == "field"]
        assert take_fields[0]["key"] == f"{prefix}_take_min"

        # zoom_yellow (жёлтые слова)
        assert g_yellow.get("toggle") == f"{prefix}_yellow_zoom"
        # Задание WX: в группе ровно одна ручка — галка «наезд только на сильные жёлтые»
        # (сила слова считается в core/emphasis.py). Других полей тут нет.
        yellow_fields = [it["key"] for it in g_yellow.get("items", []) if it.get("type") == "field"]
        assert yellow_fields == [f"{prefix}_yellow_zoom_strong"], yellow_fields

        # zoom_cycle (наезд и отъезд)
        cycle_fields = [it for it in g_cycle.get("items", []) if it.get("type") == "field"]
        assert cycle_fields[0]["key"] == f"{prefix}_take_lo"
        assert cycle_fields[0]["key2"] == f"{prefix}_take_hi"
        assert cycle_fields[0]["ctl"] == "range"
        assert cycle_fields[0].get("plus") is True
        assert cycle_fields[1]["key"] == f"{prefix}_take_hold"
        assert cycle_fields[2]["key"] == f"{prefix}_take_out"


def test_take_zoom_generated_in_all_cut_modes():
    """Наезд в куске генерируется при всех 4 режимах на склейке: jump, pulse, drift, none."""
    fps = 60.0
    cams = [{"clips": [[0, 600, 0, "cam1.mov", True]]}]
    take = {"min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}

    # 1. jump
    keys_jump = layout._cam1_jump_keys(cams, fps=fps, start=False, take=take)
    take_jump = [k for k in keys_jump if k[0] > 0]
    assert len(take_jump) == 4, "jump должен дать 4 ключа цикла наезда"

    # 2. pulse
    keys_pulse = layout._cam1_zoom_keys(cams, fps=fps, start=False, take=take)
    take_pulse = [k for k in keys_pulse if k[0] > 0]
    assert len(take_pulse) == 4, "pulse должен дать 4 ключа цикла наезда"

    # 3. drift
    keys_drift = layout._cam1_drift_keys(cams, fps=fps, start=False, take=take)
    take_drift = [k for k in keys_drift if k[1] > 115.0]
    assert len(take_drift) >= 2, "drift должен содержать ключи подъёма в куске"

    # 4. none: база 100%, подъем на +25..40%
    base_scale = 100.0
    keys_none = layout._take_zoom_segment_keys(
        f=0, seg_end=600, v=base_scale, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys_none) == 4
    # Ключ b (пик) и c (конец холда) имеют масштаб 100 + [25..40]% = 125..140%
    assert 125.0 <= keys_none[1][1] <= 140.0
    assert 125.0 <= keys_none[2][1] <= 140.0


def test_take_zoom_compressed_lead_rule_a():
    """Правило A: жёлтое слово на 1.0 с (кадр 60) — наезд успевает, подъезд 0.6 с.

    Новое правило: подъезд стартует за 0.1 с до слова (кадр 54) и длится
    0.6 с — пик на кадре 90, уже внутри слова. Удержание 2 с считается сверх конца
    слова: c = max(60, 90) + 120 = 210, отъезд d = 210 + 144 = 354 помещается.
    """
    fps = 60.0
    take = {
        "min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0,
        "words": [60.0], "yellow_mode": "snap",
    }

    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=600, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 4
    k_a, k_b, k_c, k_d = keys
    assert k_a[0] == 54
    assert k_b[0] == 90
    assert k_c[0] == 210
    assert k_d[0] == 354


def test_take_zoom_two_cycles_in_14s_rule_b():
    """Правило B: кусок 14 с (840 кадров) с двумя фразами — циклов столько, сколько
    помещается целиком.

    Первая фраза (кадр 240) отъезжает: (234, 270, 360, 504). Вторая (кадр 600) отъезда
    не получает — c = 720, d = 864 > 840 − 18: пик держится до склейки, ключей 6.
    """
    fps = 60.0
    take = {
        "min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 1.5,
        "words": [240.0, 600.0], "yellow_mode": "snap",
    }

    keys = layout._take_zoom_segment_keys(
        f=0, seg_end=840, v=100.0, fps=fps, take=take,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys) == 6, f"Ожидалось 6 ключей (цикл + пик без отъезда), получено {len(keys)}"
    # Пик 1-го цикла: a = 234, b = 234 + 36 = 270
    assert keys[1][0] == 270
    # Пик 2-го цикла: a = 594, b = 630 (отъезда нет — держится до склейки)
    assert keys[5][0] == 630


def test_take_yellow_mode_only():
    """Режим 'only': без подходящих жёлтых слов ключей нет, с подходящим — есть.

    Слово на кадре 300: a = 294, b = 330, c = 450, полный отъезд d = 594 вылезает за
    кусок 600 − 18 — ключей два.
    """
    fps = 60.0

    # Без жёлтых слов: only даёт 0 ключей, snap даёт 4 ключа
    take_only_empty = {
        "min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0,
        "words": None, "yellow_mode": "only",
    }
    keys_only_empty = layout._take_zoom_segment_keys(
        f=0, seg_end=600, v=100.0, fps=fps, take=take_only_empty,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys_only_empty) == 0

    take_snap_empty = {
        "min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0,
        "words": None, "yellow_mode": "snap",
    }
    keys_snap_empty = layout._take_zoom_segment_keys(
        f=0, seg_end=600, v=100.0, fps=fps, take=take_snap_empty,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys_snap_empty) == 4

    # С жёлтым словом: only даёт наезд (2 ключа — отъезд не помещается)
    take_only_word = {
        "min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0,
        "words": [300.0], "yellow_mode": "only",
    }
    keys_only_word = layout._take_zoom_segment_keys(
        f=0, seg_end=600, v=100.0, fps=fps, take=take_only_word,
        trng=random.Random(42), lead_min_frame=0,
    )
    assert len(keys_only_word) == 2
    assert keys_only_word[0][0] == 294
    assert keys_only_word[1][0] == 330


def test_migration_cam_zoom_v3():
    """Миграция настроек зума под версию 3."""
    # 1. take_yellow True + take_zoom True -> yellow_zoom True, False -> False
    s1 = {"cam1_take_zoom": True, "cam1_take_yellow": True, "cam2_take_yellow": False}
    m1 = styles.migrate_cam_zoom(s1)
    assert m1["cam1_take_yellow_mode"] == "snap"
    assert m1["cam2_take_yellow_mode"] == "off"
    assert m1["cam1_yellow_zoom"] is True
    assert m1["cam2_yellow_zoom"] is False
    assert m1["cam1_take_out"] == 2.4
    assert m1["cam2_take_out"] == 2.4
    assert m1["cam_zoom_v"] == 3

    # take_zoom False + take_yellow True -> yellow_zoom False
    s1_off = {"cam1_take_zoom": False, "cam1_take_yellow": True}
    m1_off = styles.migrate_cam_zoom(s1_off)
    assert m1_off["cam1_yellow_zoom"] is False
    assert m1_off["cam1_take_zoom"] is False

    # 2. camN_zoom == "none" -> сброс camN_zoom_start в False
    s2 = {"cam1_zoom": "none", "cam1_zoom_start": True}
    m2 = styles.migrate_cam_zoom(s2)
    assert m2["cam1_zoom_start"] is False

    # 3. camN_zoom != "jump" и camN_take_zoom == True -> сброс take_zoom в False
    s3 = {"cam1_zoom": "pulse", "cam1_take_zoom": True}
    m3 = styles.migrate_cam_zoom(s3)
    assert m3["cam1_take_zoom"] is False

    # 4. yellow_mode "only" -> yellow_zoom=True, take_zoom=False
    s4 = {"cam1_zoom": "jump", "cam1_take_zoom": True, "cam1_take_yellow_mode": "only"}
    m4 = styles.migrate_cam_zoom(s4)
    assert m4["cam1_yellow_zoom"] is True
    assert m4["cam1_take_zoom"] is False


def test_resolve_swaps_inverted_range():
    """styles.resolve() автоматически упорядочивает lo <= hi."""
    st = {
        "cam1_zoom_lo": 140, "cam1_zoom_hi": 112,
        "cam2_drift_lo": 130, "cam2_drift_hi": 105,
        "cam1_take_lo": 40, "cam1_take_hi": 25,
    }
    res = styles.resolve(st)
    assert res["cam1_zoom_lo"] == 112
    assert res["cam1_zoom_hi"] == 140
    assert res["cam2_drift_lo"] == 105
    assert res["cam2_drift_hi"] == 130
    assert res["cam1_take_lo"] == 25
    assert res["cam1_take_hi"] == 40


def test_defaults_match_golden_geometry(xml_subs, tmp_path):
    """С дефолтными настройками сборка побайтово совпадает с golden_geometry.jsx."""
    from tests.test_geometry_python import _build as _golden_build, _mask_assets
    golden_path = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
    if os.path.isfile(golden_path):
        golden = _mask_assets(open(golden_path, "r", encoding="utf-8-sig").read())
        base_jsx = _mask_assets(_golden_build(xml_subs, tmp_path, {}))
        assert base_jsx == golden, "Сборка с дефолтным стилем разошлась с golden_geometry.jsx"
