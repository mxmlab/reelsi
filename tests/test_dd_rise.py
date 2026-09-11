# SPDX-License-Identifier: AGPL-3.0-or-later
"""Тесты для задания DD: анимация вставок «rise» и уход субтитров."""

import gzip
import os
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import styles
from core.xml2ae.build import scene_plan


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_styles_insert_anim_default():
    """insert_anim по умолчанию равен 'zoom'."""
    assert styles.BASE.get("insert_anim") == "zoom"
    res = styles.resolve(None)
    assert res.get("insert_anim") == "zoom"
    res_rise = styles.resolve({"insert_anim": "rise"})
    assert res_rise.get("insert_anim") == "rise"


def test_rise_first_insert_img_2420(xml_subs):
    """Сверка первой вставки 01_IMG_2420 с таблицей из TASKS.md."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
    }
    # Первая вставка из ADAutoCut_out/01_IMG_2420.inserts.json
    inserts = [{
        "type": "photo",
        "media": "media/p1.png",
        "start_s": 6.5,
        "dur_s": 2.3,
        "scale": 44,
        "sc": 100,
        "x": 0,
        "y": 0,
        "style": "cam2",
    }]

    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    ins0 = plan["inserts"][0]
    anim = ins0["anim"]

    # 1. Позиция: подъём снизу на 132 px в точку покоя [540, 1152]
    # rest_y = 1920 * 0.6 = 1152.0, pos_start = 1152 + 132 = 1284.0
    assert "position" in anim
    assert anim["position"] == [
        [6.5, [540.0, 1284.0]],
        [7.0, [540.0, 1152.0]],
    ]

    # 2. Масштаб: рост от 70.6% осевшего S (44 * 0.706 = 31.064) до 44.0
    assert "scale" in anim
    assert anim["scale"] == [
        [6.5, 31.064],
        [7.0, 44.0],
    ]

    # 3. Непрозрачность: 0 -> 100 за 0.5 с, выход 100 -> 0 за 0.47 с (8.8 - 0.47 = 8.33)
    assert "opacity" in anim
    assert anim["opacity"] == [
        [6.5, 0.0],
        [7.0, 100.0],
        [8.33, 100.0],
        [8.8, 0.0],
    ]

    # 4. Блюр отсутствует
    assert "blur" not in anim

    # 5. sub_hide: субтитры уходят на окно вставки
    assert "sub_hide" in plan
    assert plan["sub_hide"] == [
        [6.5, 100.0],
        [7.0, 0.0],
        [8.33, 0.0],
        [8.8, 100.0],
    ]


def test_sub_hide_overlapping_windows(xml_subs):
    """Вставки внахлёст объединяются в одно окно ухода субтитров."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
    }
    # Две вставки с перекрытием: [6.5, 8.8] и [8.0, 10.5]
    inserts = [
        {"type": "photo", "media": "p1.png", "start_s": 6.5, "dur_s": 2.3, "style": "cam2"},
        {"type": "photo", "media": "p2.png", "start_s": 8.0, "dur_s": 2.5, "style": "cam2"},
    ]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    # Объединённое окно: [6.5, 10.5] -> 4 ключа
    # enter = 0.5 (6.5 -> 7.0), exit = 0.47 (10.5 - 0.47 = 10.03 -> 10.5)
    assert plan["sub_hide"] == [
        [6.5, 100.0],
        [7.0, 0.0],
        [10.03, 0.0],
        [10.5, 100.0],
    ]


def test_sub_hide_disjoint_windows(xml_subs):
    """Непересекающиеся вставки дают раздельные окна ухода субтитров."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
    }
    inserts = [
        {"type": "photo", "media": "p1.png", "start_s": 2.0, "dur_s": 2.0, "style": "cam2"},
        {"type": "photo", "media": "p2.png", "start_s": 6.0, "dur_s": 2.0, "style": "cam2"},
    ]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    assert len(plan["sub_hide"]) == 8
    assert plan["sub_hide"] == [
        [2.0, 100.0], [2.5, 0.0], [3.53, 0.0], [4.0, 100.0],
        [6.0, 100.0], [6.5, 0.0], [7.53, 0.0], [8.0, 100.0],
    ]


def test_zoom_mode_no_sub_hide(xml_subs):
    """При insert_anim='zoom' sub_hide пуст, а в anim есть blur и нет position у cam2."""
    st = {
        "insert_anim": "zoom",
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
    }
    inserts = [{"type": "photo", "media": "p1.png", "start_s": 2.0, "dur_s": 2.0, "style": "cam2"}]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    assert plan["sub_hide"] == []
    ins0 = plan["inserts"][0]
    assert "blur" in ins0["anim"]
    assert "position" not in ins0["anim"]


def test_sub_hide_noexit_window(xml_subs):
    """Вставка с noexit (срез по смене камеры) возвращает субтитры через 1 кадр после конца."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": True,
        "intro_riser": False,
        "roto": False,
    }
    # Вставка [5.0, 8.0] срезается по смене камеры на 7.3833 с noexit=True (fps=60 -> 1 кадр = 0.0167)
    inserts = [{
        "type": "photo",
        "media": "media/p1.png",
        "start_s": 5.0,
        "dur_s": 3.0,
        "style": "cam2",
    }]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    ins0 = plan["inserts"][0]
    assert ins0["noexit"] is True
    assert ins0["end"] == 7.3833

    # sub_hide: возврат рывком на 1 кадр позже конца (7.3833 + 1/60 = 7.4)
    assert plan["sub_hide"] == [
        [5.0, 100.0],
        [5.5, 0.0],
        [7.3833, 0.0],
        [7.4, 100.0],
    ]


def test_sub_hide_overlapping_noexit(xml_subs):
    """Окно внахлёст: если последняя вставка noexit=True, всё окно завершается с noexit."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": True,
        "intro_riser": False,
        "roto": False,
    }
    # Первая [4.0, 6.0] (noexit=False), вторая [5.5, 8.0] -> срез на 7.3833 (noexit=True)
    inserts = [
        {"type": "photo", "media": "p1.png", "start_s": 4.0, "dur_s": 2.0, "style": "cam2"},
        {"type": "photo", "media": "p2.png", "start_s": 5.5, "dur_s": 2.5, "style": "cam2"},
    ]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    assert plan["sub_hide"] == [
        [4.0, 100.0],
        [4.5, 0.0],
        [7.3833, 0.0],
        [7.4, 100.0],
    ]

