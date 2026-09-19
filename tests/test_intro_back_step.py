# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты межстрочного интервала заднего плана интро (задание ZT).

1. intro_line_ys со строкой заднего плана: back_step 0.2 -> 1.0 -> 3.0 даёт шаги
   x line_step (0.2/1.0/3.0), в т.ч. шаг ПОСЛЕ строки заднего плана.
2. back_gap в стиле -> убран миграцией при загрузке; сборка с ним не падает.
3. back_step=0.75 больше не подменяется на 0.45.
4. Превью (node): разные ys (от разного back_step) -> разные экранные позиции строки
   заднего плана в превью.
5. Схема: intro_scale/intro_line_step/intro_x/intro_cam в группе «Transform», вне
   групп камер «Камера 1» и «Камера 2».
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_LINE_STEP, intro_line_ys  # noqa: E402
from tests.test_intro_preview_ys import DOM_SIM, _ipvintro_region, _js_src, _run_node, node  # noqa: E402

PS = "TestInk-Regular"
T_CAM1, T_CAM2 = 1.0, 8.3


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _build_intro(xml_subs, tmp_path, intro, style=None, splits=None):
    path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "intro.jsx"),
                                   intro=intro, intro_splits=(splits or []),
                                   style=dict(style or {}, font=PS),
                                   disclaimer="", intro_riser=False,
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def test_intro_line_ys_back_step_scales():
    """1. intro_line_ys со строкой заднего плана: back_step 0.2 -> 1.0 -> 3.0 даёт шаги
    line_step * back_step, включая шаг ПОСЛЕ строки заднего плана."""
    lines = [
        {"words": ["Заголовок"]},
        {"words": ["Задний", "план"], "back": True},
        {"words": ["Подвал"]},
    ]

    for bstep in (0.2, 1.0, 3.0):
        ys = intro_line_ys(lines, bstep, any_back_in_clip=True, anchor="center")
        step_before = round(ys[1] - ys[0], 2)
        step_after = round(ys[2] - ys[1], 2)
        expected = round(INTRO_LINE_STEP * bstep, 2)
        assert step_before == expected, f"шаг ДО back при back_step={bstep}: {step_before} != {expected}"
        assert step_after == expected, f"шаг ПОСЛЕ back при back_step={bstep}: {step_after} != {expected}"

    # Также проверяем при step_k = 1.5 (множитель основного шага):
    # line_step = 160 * 1.5 = 240.
    for bstep in (0.5, 1.2):
        ys = intro_line_ys(lines, bstep, any_back_in_clip=True,
                           anchor="center", step_k=1.5)
        step_before = round(ys[1] - ys[0], 2)
        step_after = round(ys[2] - ys[1], 2)
        expected = round(INTRO_LINE_STEP * 1.5 * bstep, 2)
        assert step_before == expected
        assert step_after == expected


def test_back_gap_dropped_by_migration_and_assembly_safe(xml_subs, tmp_path):
    """2. back_gap в стиле убирается миграцией при загрузке; сборка с ним не падает."""
    raw = {"back_gap": 15.0, "back_step": 0.5, "intro_line_step": 120}
    migrated, changed = styles.migrate_style_dict(raw)
    assert changed is True
    assert "back_gap" not in migrated
    assert migrated["back_step"] == 0.5

    # resolve также не отдаёт back_gap
    resolved = styles.resolve({"back_gap": 25.0, "back_step": 0.8})
    assert "back_gap" not in resolved
    assert resolved["back_step"] == 0.8

    # Сборка фикстуры со стилем, содержащим back_gap, не падает
    intro = [
        {"words": ["Заголовок"], "color": "white", "times": [T_CAM1]},
        {"words": ["Малый"], "back": True, "color": "white", "times": [T_CAM1 + 0.5]},
        {"words": ["Конец"], "color": "white", "times": [T_CAM1 + 1.0]},
    ]
    jsx = _build_intro(xml_subs, tmp_path, intro, style={"back_gap": 20.0, "back_step": 0.8})
    # Ветка шаблона со строками заднего плана объявляет BACK_STEP одним var-списком
    # («var nL=GRP.length, BACK_STEP=…») — отдельного «var BACK_STEP» в .jsx нет и не было.
    assert "var nL=GRP.length, BACK_STEP=0.8," in jsx, "шаг заднего плана не доехал до шаблона"
    assert "back_gap" not in jsx


def test_back_step_075_not_replaced(xml_subs, tmp_path):
    """3. back_step=0.75 больше не подменяется на 0.45."""
    data = {"back_step": 0.75}
    migrated, changed = styles.migrate_style_dict(dict(data))
    assert migrated["back_step"] == 0.75

    resolved = styles.resolve({"back_step": 0.75})
    assert resolved["back_step"] == 0.75

    intro = [
        {"words": ["A"], "color": "white", "times": [T_CAM1]},
        {"words": ["B"], "back": True, "color": "white", "times": [T_CAM1 + 0.5]},
    ]
    jsx = _build_intro(xml_subs, tmp_path, intro, style={"back_step": 0.75})
    assert "var nL=GRP.length, BACK_STEP=0.75," in jsx, "0.75 не доехало до шаблона как есть"
    assert "BACK_STEP=0.45," not in jsx, "0.75 снова подменяется на 0.45"


@node
def test_preview_node_back_step_changes_screen_positions(tmp_path):
    """4. Превью (node): разные ys (от разного back_step) дают разные позиции строки заднего плана."""
    intro1 = [{
        "inAt": 1.0, "outEnd": 5.0, "fade": 0.75, "front": False,
        "ys": [900.0, 948.0, 996.0],
        "lines": [
            {"color": "white", "words": ["Верх"], "times": [1.0]},
            {"color": "white", "back": True, "words": ["Задний"], "times": [1.2]},
            {"color": "white", "words": ["Низ"], "times": [1.4]},
        ],
    }]
    intro2 = [{
        "inAt": 1.0, "outEnd": 5.0, "fade": 0.75, "front": False,
        "ys": [900.0, 1140.0, 1380.0],
        "lines": [
            {"color": "white", "words": ["Верх"], "times": [1.0]},
            {"color": "white", "back": True, "words": ["Задний"], "times": [1.2]},
            {"color": "white", "words": ["Низ"], "times": [1.4]},
        ],
    }]

    checks = r"""
    // Запуск 1: план с ys1
    IPV.intro = @INTRO1@;
    IPV.introCur = -2;
    ipvIntro(1.5);
    const rows1 = ioEl.querySelectorAll('.iline');
    assert.strictEqual(rows1.length, 3);
    const top1 = parseFloat(rows1[1].style.top);

    // Запуск 2: план с ys2 (больший шаг заднего плана)
    IPV.intro = @INTRO2@;
    IPV.introCur = -2;
    ipvIntro(1.5);
    const rows2 = ioEl.querySelectorAll('.iline');
    assert.strictEqual(rows2.length, 3);
    const top2 = parseFloat(rows2[1].style.top);

    // Разные ys обязаны давать разные экранные позиции
    assert.notStrictEqual(top1, top2, 'позиция back-строки не изменилась: top1=' + top1 + ', top2=' + top2);
    // k = 540 / 1080 = 0.5: сдвиг по экрану = (1140 - 948) * 0.5 = 96.0 px
    const diff = Math.round((top2 - top1) * 100) / 100;
    assert.strictEqual(diff, 96, 'сдвиг по экрану обязан быть равен разности ys * k: получено ' + diff);

    console.log("OK: back line preview position changes with ys");
    """
    script = (
        DOM_SIM.replace("@INTRO@", "[]")
        + "\n"
        + _ipvintro_region(_js_src())
        + "\n"
        + checks.replace("@INTRO1@", json.dumps(intro1, ensure_ascii=False))
                .replace("@INTRO2@", json.dumps(intro2, ensure_ascii=False))
    )
    res = _run_node(tmp_path, "test_preview_back_step.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: back line preview position changes with ys" in res.stdout


def _intro_groups_by_id(items):
    """id -> группа среди ЛЮБЫХ вложенных групп слоя: «Задний план» (intro.back) лежит
    внутри «Текст» (intro.text), а не прямо в слое — плоский обход его не находит."""
    found = {}

    def walk(nodes):
        for it in nodes:
            if it.get("type") == "group" or it.get("id"):
                if it.get("id"):
                    found[it["id"]] = it
                walk(it.get("items", []))

    walk(items)
    return found


def test_schema_intro_transform_and_camera_groups():
    """5. Схема стиля: intro_scale/intro_line_step/intro_x/intro_cam в группе «Transform»,
    вне групп камер («Камера 1» и «Камера 2»)."""
    intro_layer = None
    for layer in style_schema.LAYERS:
        if layer.get("id") == "intro":
            intro_layer = layer
            break
    assert intro_layer is not None, "Слой intro не найден в схеме"

    groups = _intro_groups_by_id(intro_layer.get("items", []))
    assert "intro.tr" in groups, "Группа intro.tr (Transform) отсутствует"
    assert "intro.cam1" in groups, "Группа intro.cam1 (Камера 1) отсутствует"
    assert "intro.cam2" in groups, "Группа intro.cam2 (Камера 2) отсутствует"

    tr_group = groups["intro.tr"]
    cam1_group = groups["intro.cam1"]
    cam2_group = groups["intro.cam2"]

    assert tr_group.get("label") == "Transform"
    assert cam1_group.get("label") == "Камера 1"
    assert cam2_group.get("label") == "Камера 2"

    def get_field_keys(group):
        keys = set()
        for item in group.get("items", []):
            if item.get("key"):
                keys.add(item["key"])
            if item.get("key2"):
                keys.add(item["key2"])
            if item.get("toggle"):
                keys.add(item["toggle"])
        return keys

    tr_keys = get_field_keys(tr_group)
    cam1_keys = get_field_keys(cam1_group)
    cam2_keys = get_field_keys(cam2_group)

    expected_tr = {"intro_scale", "intro_line_step", "intro_x", "intro_cam"}
    expected_cam1 = {"intro_y", "intro_anchor"}
    expected_cam2 = {"intro_y2", "intro_anchor2"}

    assert expected_tr.issubset(tr_keys), f"В Transform должны быть {expected_tr}, найдено {tr_keys}"
    assert expected_cam1.issubset(cam1_keys), f"В Камера 1 должны быть {expected_cam1}, найдено {cam1_keys}"
    assert expected_cam2.issubset(cam2_keys), f"В Камера 2 должны быть {expected_cam2}, найдено {cam2_keys}"

    # Ручки Transform не должны быть в камерах
    assert tr_keys.isdisjoint(cam1_keys), f"Пересечение Transform и Камера 1: {tr_keys & cam1_keys}"
    assert tr_keys.isdisjoint(cam2_keys), f"Пересечение Transform и Камера 2: {tr_keys & cam2_keys}"

    # Ручка back_step в группе intro.back
    back_group = groups.get("intro.back")
    assert back_group is not None, "Группа intro.back отсутствует"
    back_field = None
    for it in back_group.get("items", []):
        if it.get("key") == "back_step":
            back_field = it
            break
    assert back_field is not None, "Поле back_step не найдено в intro.back"
    assert back_field["label"] == "Межстрочный заднего плана, %"
    assert back_field.get("conv") == "frac_pct_int"
    assert (back_field.get("min"), back_field.get("max")) == (10, 300)
    assert (back_field.get("lim_min"), back_field.get("lim_max")) == (10, 300)
    assert back_field.get("step") == 1

    # back_gap нигде не должно быть
    all_keys = set()

    def collect_all_keys(items):
        for it in items:
            for k in ("key", "key2", "toggle"):
                if it.get(k):
                    all_keys.add(it[k])
            if it.get("items"):
                collect_all_keys(it["items"])

    for layer in style_schema.LAYERS:
        collect_all_keys(layer.get("items", []))
    assert "back_gap" not in all_keys, "Поле back_gap всё ещё присутствует в схеме"
