# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Интро камеры 2 — паритет с камерой 1: свои ручки и свой расчёт.

Раньше у интро, выпавшего на перебивку, ручек камеры 1 было ДВЕ на обе камеры:
галка «интро едет с камерой» (intro_cam) привязывала и нул «интро на кам2», а точка
масштабирования (intro_scale_anchor) задавала якорь и прекомпам камеры 2. Автоподгонка
размера этих групп считала зум КАМЕРЫ 1, хотя кадр на перебивке — камеры 2.

Теперь у камеры 2 свои ключи:
  intro_cam2          — «интро едет с камерой» (нул «интро на кам2»);
  intro_scale_anchor2 — точка масштабирования её прекомпов (comp | first | block);
  intro_roto_by_pos2  — «интро над рото в нижней половине (камера 2)», дефолт False
                        (под рото, как было).

Старые стили ключей не знают: миграция при чтении (styles.migrate_intro_cam2 и её
JS-пара stMigrateIntroCam2) наследует обе ручки от камеры 1 — вид не меняется.

Стережём:
1. дефолт и старые стили: .jsx побайтово равен golden (здесь — сборка BASE снимается
   тем же стендом, что tests/test_geometry_python.py); миграция трёх ключей (Python и JS);
2. intro_cam=True, intro_cam2=False: нул «интро на кам2» без родителя, нул «интро» —
   ребёнок нула Камеры 1; и наоборот;
3. тумблер intro_cam2 сам по себе меняет .jsx (сторож «каждая ручка»);
4. автоподстановка размера: смена зума КАМЕРЫ 1 не меняет ds группы на камере 2, а
   смена зума КАМЕРЫ 2 — меняет; у камеры 1 расчёт прежний;
5. intro_roto_by_pos2: порядок слоёв интро кам2 относительно рото меняется, а
   группа камеры 1 при этом не трогается;
6. intro_scale_anchor2: якорь масштабирования прекомпов кам2 уезжает в .jsx
   (INTRO_ANCHOR_Y), у камеры 1 остаются её числа.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, styles, xml2ae  # noqa: E402
from test_geometry_python import _build as _geo_build, _mask_assets  # noqa: E402
from test_intro_detach import _func, _jsx_layers, node  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3
# Группа на камере 1 и группа на перебивке: у каждой свой нул.
INTRO = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
         dict(words=["ВТОРАЯ", "КАМЕРА"], color="white", times=[T_CAM2])]
# Строка заведомо шире кадра — чтобы автофит её ужимал и ds реагировал на зум камеры.
WIDE = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["A" * 12], color="white", times=[T_CAM2])]
# Стиль нового формата: метка смысла intro_y2 стоит, значения читаются как есть.
NEW = {"intro_pos2_v": 2}


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


@pytest.fixture()
def wide_font(monkeypatch):
    """Ширина текста известна и от системных шрифтов не зависит: буква = ровно кегль."""
    monkeypatch.setattr(fonts, "text_width", lambda ps, text, size: float(size) * len(text))


def _build(xml, tmp_path, style, intro=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=INTRO if intro is None else intro,
                                   intro_splits=[1], style=dict(style), intro_mode="word",
                                   disclaimer="", intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _scene(xml, style, intro=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=INTRO if intro is None else intro,
                             intro_splits=[1], style=dict(style), emit=lambda *a: None)


def _on2(plan, which):
    """Группа плана: which=0 — камера 1, which=1 — перебивка."""
    g = [x for x in plan["intro"] if bool(x["on2"]) == bool(which)]
    assert g, f"в плане нет группы на камере {'2' if which else '1'}"
    return g[0]


# ============================================ 1. дефолт = эталон, миграции

def test_дефолтная_сборка_по_прежнему_равна_эталону(xml_subs, tmp_path):
    """Дефолт (обе галки True, оба якоря comp): .jsx побайтово прежний."""
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert _mask_assets(_geo_build(xml_subs, tmp_path, style={})) == golden


def test_старые_стили_наследуют_ручки_кам2_от_камеры_1(xml_subs):
    """Стиль без intro_cam2/intro_scale_anchor2 читается как раньше (камера 1)."""
    off = styles.resolve(dict(NEW, intro_cam=False, intro_scale_anchor="first"))
    assert off["intro_cam2"] is False and off["intro_scale_anchor2"] == "first"
    assert styles.resolve(NEW)["intro_cam2"] is True
    assert styles.resolve(NEW)["intro_scale_anchor2"] == "comp"
    # дефолт roto-by-pos у кам2 — False: группы на перебивке стоят под рото, как было
    assert styles.resolve(NEW)["intro_roto_by_pos2"] is False
    assert _scene(xml_subs, NEW)["intro"][1]["above_roto"] is False


def test_js_миграция_ручек_кам2_тем_же_правилом():
    src = open(os.path.join(ROOT, "static", "app", "95-styles.js"), encoding="utf-8").read()
    script = _func(src, "stMigrateIntroCam2") + """
    const a = stMigrateIntroCam2({intro_cam: false, intro_scale_anchor: 'first'});
    const b = stMigrateIntroCam2({});
    const c = stMigrateIntroCam2({intro_cam: false, intro_cam2: true,
                                  intro_scale_anchor: 'block', intro_scale_anchor2: 'first'});
    console.log(JSON.stringify({a: [a.intro_cam2, a.intro_scale_anchor2],
                                b: [b.intro_cam2, b.intro_scale_anchor2],
                                c: [c.intro_cam2, c.intro_scale_anchor2]}));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got["a"] == [False, "first"]
    assert got["b"] == [True, "comp"]
    assert got["c"] == [True, "first"]


# ============================================ 2. родитель нула «интро на кам2»

def test_интро_кам2_откреплено_а_кам1_привязано(xml_subs, tmp_path):
    """intro_cam=True, intro_cam2=False: нул кам2 без родителя, нул кам1 — на своей камере."""
    st = dict(NEW, cam2_zoom="pulse", intro_cam=True, intro_cam2=False)
    jsx, path = _build(xml_subs, tmp_path, st, name="c1on_c2off.jsx")
    assert "if(cam1null){ introNull.parent=cam1null;" in jsx
    assert "introNull2.parent" not in jsx
    lay = _jsx_layers(path)
    assert lay["интро"]["parent"] == "Камера 1"
    assert lay["интро на кам2"]["parent"] is None


def test_интро_кам2_привязано_а_кам1_откреплено(xml_subs, tmp_path):
    """intro_cam=False, intro_cam2=True: наоборот — и это уже не одна галка."""
    st = dict(NEW, cam2_zoom="pulse", intro_cam=False, intro_cam2=True)
    jsx, path = _build(xml_subs, tmp_path, st, name="c1off_c2on.jsx")
    assert "if(cam1null && INTRO_CAM){ introNull.parent=cam1null;" in jsx
    assert "introNull2.parent=cam2null" in jsx
    lay = _jsx_layers(path)
    assert lay["интро"]["parent"] is None
    assert lay["интро на кам2"]["parent"] == "Камера 2"


def test_камера_2_неактивна_нул_кам2_стоит_в_координатах_кадра(xml_subs, tmp_path):
    """Без зума камеры 2 её нула в сборке нет — привязывать нул кам2 не к чему."""
    jsx, path = _build(xml_subs, tmp_path, dict(NEW, intro_y2=30), name="nocam2.jsx")
    assert "introNull2.parent" not in jsx
    lay = _jsx_layers(path)
    assert lay["интро на кам2"]["parent"] is None
    assert lay["интро на кам2"]["pos"] == [1080 / 2, 1920 / 2 + 30]


def test_тумблер_intro_cam2_меняет_сборку(xml_subs, tmp_path):
    """Сторож «каждая ручка»: снятая галка камеры 2 обязана менять .jsx."""
    on = _build(xml_subs, tmp_path, dict(NEW, cam2_zoom="pulse", intro_cam2=True),
                name="knob_on.jsx")[0]
    off = _build(xml_subs, tmp_path, dict(NEW, cam2_zoom="pulse", intro_cam2=False),
                 name="knob_off.jsx")[0]
    assert on != off, "intro_cam2 не влияет на сборку"
    assert "introNull2.parent=cam2null" in on
    assert "introNull2.parent" not in off


# ============================================ 3. автоподстановка размера

def test_автофит_кам2_считает_по_зуму_кам2_а_не_кам1(wide_font, xml_subs):
    """ds группы на перебивке зависит от зума КАМЕРЫ 2, а не камеры 1.

    Строка на перебивке шире кадра (12 букв × кегль): зум камеры 2 её ужимает, и ds
    падает. Зум камеры 1 на эту группу не влияет вовсе — раньше влиял (считался её зум).
    """
    base = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="none"), WIDE), 1)
    cam1_big = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="jump", cam1_zoom_big=260,
                                          cam2_zoom="none"), WIDE), 1)
    cam2_big = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="pulse",
                                          cam2_zoom_big=260), WIDE), 1)

    assert base["ds"] < 100, "предпосылка: строка шире кадра — автофит обязан её ужать"
    assert cam1_big["ds"] == pytest.approx(base["ds"]), \
        "зум КАМЕРЫ 1 всё ещё меняет размер интро на камере 2"
    assert cam2_big["ds"] < base["ds"], \
        "зум КАМЕРЫ 2 не ужал строку — автофит считает чужой кадр"


def test_автофит_кам1_по_прежнему_считает_по_зуму_кам1(wide_font, xml_subs):
    """У камеры 1 расчёт не менялся: растёт зум её камеры — падает ds её группы."""
    base = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="none"), WIDE), 0)
    big = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="jump", cam1_zoom_big=260,
                                     cam2_zoom="none"), WIDE), 0)
    assert big["ds"] < base["ds"], "автофит группы камеры 1 перестал видеть её зум"


def test_откреплённое_интро_кам2_зум_не_учитывает(wide_font, xml_subs):
    """intro_cam2=False: зум камеры 2 в автофит группы не входит (как у камеры 1)."""
    on = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="pulse",
                                    cam2_zoom_big=260, intro_cam2=True), WIDE), 1)
    off = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="pulse",
                                     cam2_zoom_big=260, intro_cam2=False), WIDE), 1)
    flat = _on2(_scene(xml_subs, dict(NEW, cam1_zoom="none", cam2_zoom="none",
                                      intro_cam2=False), WIDE), 1)
    assert off["ds"] != on["ds"], "снятая галка кам2 не убрала зум из автофита"
    assert off["ds"] == pytest.approx(flat["ds"]), \
        "откреплённый автофит кам2 всё ещё считает её зум"


# ============================================ 4. порядок слоёв над рото

def _above_arr(jsx):
    m = re.search(r"var INTRO_ABOVE_ROTO\s*=\s*(\[.*?\]);", jsx)
    return json.loads(m.group(1)) if m else None


def test_roto_by_pos2_поднимает_группу_кам2_над_рото(xml_subs, tmp_path, monkeypatch):
    """intro_roto_by_pos2: группа кам2 из нижней половины встаёт над рото.

    Группа кам2 сдвинута вниз (gy), группа кам1 — на месте. Рото-слои подменены
    заглушкой (без GPU): проверяется маршрутизация слоёв, а не маски.
    """
    mock_data = json.dumps([
        {"ci": 0, "ts": 0.0, "te": 5.0, "cs": 0.0, "scale": 100, "mf": 1,
         "mask": "C:/x/mask.mp4"}])
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: mock_data)
    intro = [dict(words=["ВЕРХНЕЕ"], times=[T_CAM1]),
             dict(words=["НИЖНЕЕ"], times=[T_CAM2], gy=900)]

    off = _build(xml_subs, tmp_path, dict(NEW, cam2_zoom="pulse"), intro,
                 name="roto_off.jsx")[0]
    assert _above_arr(off) is None, "без галки подстановок быть не должно"

    on = _build(xml_subs, tmp_path, dict(NEW, cam2_zoom="pulse", intro_roto_by_pos2=True),
                intro, name="roto_on.jsx")[0]
    assert _above_arr(on) == [0, 1], \
        "группа камеры 2 из нижней половины не поднялась над рото"
    assert "introAboveRoto.push(iL)" in on


def test_roto_by_pos2_не_трогает_группу_камеры_1(xml_subs, tmp_path, monkeypatch):
    """Галка камеры 2 не поднимает группу камеры 1 даже из нижней половины."""
    mock_data = json.dumps([
        {"ci": 0, "ts": 0.0, "te": 5.0, "cs": 0.0, "scale": 100, "mf": 1,
         "mask": "C:/x/mask.mp4"}])
    monkeypatch.setattr(xml2ae.build, "_roto_js", lambda *a, **k: mock_data)
    intro = [dict(words=["НИЖНЕЕ"], times=[T_CAM1], gy=900),
             dict(words=["ВЕРХНЕЕ"], times=[T_CAM2])]
    jsx = _build(xml_subs, tmp_path,
                 dict(NEW, cam2_zoom="pulse", intro_roto_by_pos2=True), intro,
                 name="roto_c1.jsx")[0]
    assert _above_arr(jsx) is None, "галке камеры 2 нечего делать на группе камеры 1"


# ============================================ 5. точка масштабирования кам2

def test_scale_anchor2_уезжает_в_jsx_и_план(xml_subs, tmp_path):
    """intro_scale_anchor2: якорь прекомпов кам2 свой, у камеры 1 — свои числа.

    Группа кам2 из ДВУХ строк: "first" ставит якорь на Y первой строки (выше центра),
    а "comp" — в центр композиции прекомпа (960). Группа кам1 остаётся на дефолтном comp.
    """
    # три строки, две группы: [строка 1 | строки 2-3] — у группы кам2 две строки
    intro = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
             dict(words=["ВТОРАЯ"], color="white", times=[T_CAM2]),
             dict(words=["СТРОКА"], color="white", times=[T_CAM2 + 0.5])]
    assert "anchor_y" not in _on2(_scene(xml_subs, NEW, intro), 1), \
        "при дефолтном comp поля anchor_y у группы быть не должно"

    plan = _scene(xml_subs, dict(NEW, intro_scale_anchor2="first"), intro)
    c2 = _on2(plan, 1)
    c1 = _on2(plan, 0)
    assert len(c2["ys"]) == 2, "предпосылка теста: у группы кам2 две строки"
    assert "anchor_y" in c2, "intro_scale_anchor2 не доехал до плана"
    assert c2["anchor_y"] == pytest.approx(c2["ys"][0]), \
        "якорь не на первой строке блока (intro_scale_anchor2 не сработал)"
    assert c2["anchor_y"] < 960.0, \
        "якорь остался в центре композиции прекомпа вместо первой строки блока"
    assert "anchor_y" not in c1, "ручка камеры 2 подвинула якорь группы камеры 1"

    jsx = _build(xml_subs, tmp_path, dict(NEW, intro_scale_anchor2="first"), intro,
                 name="anchor2.jsx")[0]
    off = _build(xml_subs, tmp_path, dict(NEW), intro, name="anchor_comp.jsx")[0]
    assert "var INTRO_ANCHOR_Y=" not in off, \
        "оба режима comp — подстановок якоря в .jsx быть не должно"
    m = re.search(r"var INTRO_ANCHOR_Y=(\[.*?\]), INTRO_ANCHOR_DY=", jsx)
    assert m, "в .jsx нет массива INTRO_ANCHOR_Y"
    ys = json.loads(m.group(1))
    assert ys[1] == pytest.approx(c2["anchor_y"]), \
        "в .jsx уехал не тот якорь, что посчитал план"
    assert ys[1] < 960.0, "якорь кам2 остался центром композиции прекомпа"


# ============================================ 6. сторож ручек

@node
def test_ручки_кам2_есть_в_схеме_и_BASE():
    """Все три ручки — в схеме (своими типами) и в BASE (значения = прежнее поведение)."""
    for key in ("intro_cam2", "intro_scale_anchor2", "intro_roto_by_pos2"):
        field = watcher.schema_field(key)
        assert field, f"в схеме нет ручки {key}"
        assert key in styles.BASE, f"в styles.BASE нет ключа {key}"
    assert watcher.schema_field("intro_cam2")["ctl"] == "bool"
    assert watcher.schema_field("intro_roto_by_pos2")["ctl"] == "bool"
    assert watcher.schema_field("intro_scale_anchor2")["ctl"] == "select"
    assert styles.BASE["intro_cam2"] is True
    assert styles.BASE["intro_scale_anchor2"] == "comp"
    assert styles.BASE["intro_roto_by_pos2"] is False


def test_ручки_кам2_в_группах_схемы():
    """Камерные ручки интро собраны в intro.cam1 и intro.cam2 с одинаковым порядком и подписями.

    Тень прекомпа обеих камер — в одной группе intro.cshadow (поля
    intro_comp_shadow_fill/opacity и intro_comp_shadow2_fill/opacity переехали туда).
    """
    from core.style_schema import LAYERS
    intro = next(s for s in LAYERS if s["id"] == "intro")

    # Группа intro.order удалена, в intro.tr камерных ключей нет
    assert not any(g["id"] == "intro.order" for g in intro["items"])
    tr = next(g for g in intro["items"] if g["id"] == "intro.tr")
    assert "intro_cam" not in [it["key"] for it in tr["items"]]
    cshadow = next(g for g in intro["items"] if g["id"] == "intro.cshadow")
    cshadow_keys = [it["key"] for it in cshadow["items"]]
    assert cshadow_keys[:4] == [
        "intro_comp_shadow_fill", "intro_comp_shadow_opacity",
        "intro_comp_shadow2_fill", "intro_comp_shadow2_opacity",
    ], f"тень прекомпа обеих камер обязана лежать в intro.cshadow: {cshadow_keys}"

    cam1 = next(g for g in intro["items"] if g["id"] == "intro.cam1")
    cam2 = next(g for g in intro["items"] if g["id"] == "intro.cam2")

    expected_cam1 = [
        "intro_y", "intro_anchor", "intro_scale_anchor", "intro_cam",
        "intro_margin", "intro_fit_max", "intro_roto_by_pos",
    ]
    expected_cam2 = [
        "intro_y2", "intro_anchor2", "intro_scale_anchor2", "intro_cam2",
        "intro_margin2", "intro_fit_max2", "intro_roto_by_pos2",
    ]
    assert [it["key"] for it in cam1["items"]] == expected_cam1
    assert [it["key"] for it in cam2["items"]] == expected_cam2

    # Подписи камерных ручек свои у каждой камеры (ручки ширины — по камерам), остальные
    # совпадают: «Якорь», «Точка масштабирования» и галки читаются одинаково.
    expected_cam1_labels = [
        "Положение по Y", "Якорь", "Точка масштабирования", "Едет с камерой",
        "Отступ от краёв, % (камера 1)", "Масштаб интро, % (камера 1)",
        "Над рото в нижней половине",
    ]
    expected_cam2_labels = [
        "Положение по Y", "Якорь", "Точка масштабирования", "Едет с камерой",
        "Отступ от краёв, % (камера 2)", "Масштаб интро, % (камера 2)",
        "Над рото в нижней половине",
    ]
    assert [it["label"] for it in cam1["items"]] == expected_cam1_labels
    assert [it["label"] for it in cam2["items"]] == expected_cam2_labels
