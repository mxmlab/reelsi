# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: вставки (этап 3).

Вставки уехали из `scene_plan` в `core/xml2ae/plan_inserts.py` двумя дверями: подготовка
таймингов (`plan_insert_timings`) и сборка данных (`plan_inserts`). Между ними в
`scene_plan` лежит зависимый код — звук считает `has_video` по уже проставленному типу
вставки, поэтому одной дверью не обойтись.

Сторож держит СТЫК: обе двери, вызванные НАПРЯМУЮ на фикстуре со вставками (фото кам2,
фото кам1, видео, подложка), обязаны отдать ровно то, что `scene_plan` кладёт в план
(`plan["inserts"]`) и в шаблон (`plan["_ae"]["inserts"]` — строка INSERTS). Разъедутся —
.jsx соберётся не по тому, что рисует предпросмотр, и увидеть это можно только в AE.

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока (разбор XML,
резолв стиля, `_cam_change_sec` из катов, ключи подложки/анимации/сдвигов), иначе сравнение
шло бы вхолостую. Общее с другими блоками не копируется: `_sv`/`_sv_or` берутся из build,
размеры видео — `build._media_dims` (его подменяет tests/test_insert_video_pan.py, и
scene_plan читает ровно это имя), точки смены камеры — из `layout._cam_change_frames`.

Запуск: python -m pytest tests/test_plan_inserts_split.py -q
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib, styles, xml2ae  # noqa: E402
from core.xml2ae import build as build_mod  # noqa: E402
from core.xml2ae.build import read_style  # noqa: E402
from core.xml2ae.jsutil import _jd, _r  # noqa: E402
from core.xml2ae.layout import _cam_change_frames  # noqa: E402
from core.xml2ae.plan_inserts import (InsertTimingInputs, InsertsInputs,  # noqa: E402
                                      plan_insert_timings, plan_inserts)

VERT = "C:/x/vert.mp4"          # файла нет: размеры даёт подмена build._media_dims


@pytest.fixture()
def xml_subs(tmp_path):
    """Ролик фикстуры: 1-я секунда — кам1, 8-я — перебивка (та же, что у golden-теста)."""
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


@pytest.fixture(autouse=True)
def _no_rembg(monkeypatch):
    """rembg в тестах не зовём: снятие фона — чужая дверь (core/insertlib), здесь важно
    только то, что путь доезжает до плана и .jsx."""
    monkeypatch.setattr(insertlib, "remove_bg", lambda data, trim=True, emit=None: data)


@pytest.fixture(autouse=True)
def _vertical_dims(monkeypatch):
    """Размеры 9:16 для vert.mp4 (файла нет): как в tests/test_insert_video_pan.py —
    подмена живёт в модуле build, и дверь обязана читать именно её."""
    real = build_mod._media_dims
    monkeypatch.setattr(build_mod, "_media_dims",
                        lambda p: (1080, 1920) if (p or "").endswith("vert.mp4") else real(p))


def _png(path, w, h, color=(180, 40, 40, 255)):
    from PIL import Image
    Image.new("RGBA", (int(w), int(h)), color).save(str(path))
    return str(path)


def _ins(tmp_path, plate=False):
    """Вставки сторожа: фото на перебивке (кам2), фото на основной камере (кам1),
    видео (с размерами из подмены) и — по флагу — фото на подложке."""
    photo = _png(tmp_path / "photo.png", 800, 600)
    wide = _png(tmp_path / "wide.png", 1400, 500)
    items = [
        {"type": "photo", "style": "cam2", "media": photo, "start_s": 8.0, "dur_s": 1.5},
        {"type": "photo", "style": "cam1", "media": wide, "start_s": 1.0, "dur_s": 2.0},
        {"type": "video", "style": "cam2", "media": VERT, "start_s": 4.0, "dur_s": 2.0,
         "sc": 80, "x": 40, "y": -30, "sin": 0.5},
    ]
    if plate:
        items.append({"type": "photo", "style": "cam2", "media": photo, "start_s": 10.0,
                      "dur_s": 1.5, "plate": True, "kx": 15, "ky": -20, "x": 5, "y": 6})
    return items


def _active_cam_at(cams, t_sec, fps):
    """Индекс показываемой камеры в момент t: то же правило, что `_active_cam_at`
    в scene_plan (и camAt в .jsx)."""
    f = t_sec * fps + 1e-4
    best = 0
    for ci in range(len(cams)):
        for cl in cams[ci]["clips"]:
            if cl[4] and cl[0] <= f < cl[1]:
                best = ci
                break
    return best


def _doors(xml, style=None, inserts=None):
    """Обе двери вставок с входами ровно такими, какими их собрал бы scene_plan.

    Повторяет только ЧТЕНИЯ scene_plan: разбор XML, резолв стиля, чтение структуры
    стиля (`read_style`), точки смены камеры из катов и подменяемый `build._media_dims`.
    Своей копии арифметики вставок здесь нет намеренно — иначе сторож проверял бы
    копию правила.
    """
    meta, cams, _subs, _xi = xml2ae.parse_full(xml)
    st = styles.resolve(dict(style or {}))
    fps = meta["fps"] or 60
    style_values = read_style(st)
    timings = plan_insert_timings(InsertTimingInputs(
        inserts=[dict(x) for x in (inserts or [])], fps=fps,
        cam_change_sec=[f / fps for f in _cam_change_frames(cams)],
        active_cam_at=lambda t: _active_cam_at(cams, t, fps),
        style=style_values, emit=lambda *a, **k: None))
    ip = plan_inserts(InsertsInputs(
        inserts=timings.inserts, meta=meta, fps=fps, clip_end=timings.clip_end,
        media_dims=build_mod._media_dims, style=style_values,
        emit=lambda *a, **k: None))
    return timings, ip


def _call(style=None, inserts=None):
    """Аргументы сборки — одни и те же у scene_plan и у то_ae_full."""
    return dict(inserts=[dict(x) for x in (inserts or [])], style=dict(style or {}),
                disclaimer="", intro_riser=False, emit=lambda *a, **k: None)


def _check(xml, style=None, inserts=None):
    """Сверить обе двери с планом scene_plan; -> (plan, InsertTimings, InsertsPlan)."""
    plan = xml2ae.scene_plan(xml, **_call(style, inserts))
    timings, ip = _doors(xml, style=style, inserts=inserts)
    # данные вставок: их читает план (предпросмотр, /api/scene) и они же уезжают в .jsx
    assert ip.inserts == plan["inserts"]
    assert _jd(ip.inserts) == plan["_ae"]["inserts"]
    # окна ВИДЕОвставок: по ним группа интро уезжает наверх (INTRO_FRONT) — правило одно
    assert ip.video_segs == [(i["start"], i["end"]) for i in plan["inserts"]
                             if i.get("t") == "video"]
    return plan, timings, ip


def test_plan_inserts_photo_video_plate(xml_subs, tmp_path):
    """Четыре вида вставок фикстуры: фото кам2, фото кам1, видео и фото на подложке —
    данные двери совпадают с планом, а «без фона» доезжает путём .nobg.png."""
    plate_file = _png(tmp_path / "plate.png", 2048, 2048)
    plan, timings, ip = _check(xml_subs, style={"insert_plate_file": plate_file},
                               inserts=_ins(tmp_path, plate=True))
    got = plan["inserts"]
    assert [i["t"] for i in got] == ["photo", "photo", "video", "photo"], \
        "фикстура: вставки не разошлись по типам"
    assert [i["style"] for i in got] == ["cam2", "cam1", "cam2", "cam2"], \
        "стиль фото выбран не по активной камере"
    assert got[2].get("fit") and got[2].get("fitw"), "у видео нет геометрии из размеров файла"
    assert got[3]["plate"] is True and got[3]["media"].endswith("photo.png.nobg.png"), \
        "подложка не подменила путь фото на кэш без фона"
    assert len(timings.inserts) == len(ip.inserts) == 4
    # в .jsx едет ровно эта строка (шаблон получает подстановку INSERTS)
    jsx, _n, _s = xml2ae.to_ae_full(xml_subs, return_source=True,
                                    **_call({"insert_plate_file": plate_file},
                                            _ins(tmp_path, plate=True)))
    assert "var INSERTS=" + ip.inserts_js + ";" in jsx, "INSERTS в .jsx не из этой двери"


def test_plan_insert_timings_snap_cut_and_move(xml_subs, tmp_path):
    """Тайминги и привязка к монтажу: старт впритык до ката прижимается к кату, конец на
    кате срезается без выхода, схлопнувшееся окно переносится в новый шот — и ровно эти
    числа (start/end/noexit) уезжают в план."""
    meta, cams, _s, _x = xml2ae.parse_full(xml_subs)
    fps = meta["fps"] or 60
    cuts = [f / fps for f in _cam_change_frames(cams)]
    photo = _png(tmp_path / "photo.png", 800, 600)
    ins = [
        # старт впритык ПЕРЕД катом -> прижим к кату
        {"type": "photo", "style": "cam2", "media": photo, "start_s": cuts[0] - 0.2,
         "dur_s": 2.0},
        # конец на самом кате -> жёсткий срез
        {"type": "photo", "style": "cam2", "media": photo, "start_s": cuts[0] + 0.5,
         "dur_s": cuts[1] - cuts[0] - 0.55},
        # окно схлопнулось катом -> перенос старта на кат и задуманная длительность
        {"type": "photo", "style": "cam2", "media": photo, "start_s": cuts[1] - 0.6,
         "dur_s": 2.0},
    ]
    plan, timings, _ip = _check(xml_subs, inserts=ins)
    got = plan["inserts"]
    assert timings.inserts[0]["start_s"] == cuts[0], "старт не прижат к кату"
    # конец не двигаем: сдвинулся только старт, длительность уменьшилась на сдвиг
    assert got[0]["start"] == _r(cuts[0]) and got[0]["end"] == _r(cuts[0] + 1.8)
    assert got[1]["noexit"] is True, "конец на кате не срезан"
    assert abs(got[1]["end"] - _r(cuts[1] - 0.05)) < 1e-9
    assert timings.inserts[2]["start_s"] == cuts[1], "схлопнувшееся окно не перенесено на кат"
    assert got[2]["start"] == _r(cuts[1]) and got[2]["end"] == _r(cuts[1] + 2.0), \
        "перенос не доехал до плана"
    assert got[2]["noexit"] is False, "перенесённая вставка играет выход"


def test_plan_inserts_style_knobs(xml_subs, tmp_path):
    """Ручки, меняющие ветки блока: анимация rise/none, выключенный snap, принудительный
    стиль фото, видео «за человеком» и сдвиги точки покоя — стык держится на каждой."""
    ins = _ins(tmp_path)
    for style in ({"insert_anim": "rise"}, {"insert_anim": "none"},
                  {"insert_snap_cut": False}, {"insert_style": "cam1"},
                  {"insert_style": "cam2"}, {"insert_video_front": False},
                  {"insert_c1_x": 30, "insert_c1_y": -15, "insert_c2_x": 0.4,
                   "insert_c2_y": 0.3, "insert_c1on2_x": -25, "insert_c1on2_y": 12,
                   "insert_style": "cam1"}):
        plan, _t, ip = _check(xml_subs, style=style, inserts=ins)
        assert ip.inserts == plan["inserts"]
    # ветки действительно разные (иначе цикл выше ничего не проверяет)
    rise = xml2ae.scene_plan(xml_subs, **_call({"insert_anim": "rise"}, ins))["inserts"][0]
    none = xml2ae.scene_plan(xml_subs, **_call({"insert_anim": "none"}, ins))["inserts"][0]
    typed = xml2ae.scene_plan(xml_subs, **_call({}, ins))["inserts"][0]
    assert "blur" not in rise["anim"] and "position" in rise["anim"]
    assert set(none["anim"]) == {"scale", "opacity"}
    assert "blur" in typed["anim"] and "position" not in typed["anim"]


def test_plan_inserts_dims_come_from_build(xml_subs, tmp_path, monkeypatch):
    """Размеры видео дверь берёт из build, а не своей копией: подмена `build._media_dims`
    (tests/test_insert_video_pan.py) обязана доезжать и до плана, и до .jsx."""
    ins = _ins(tmp_path)
    plan, timings, ip = _check(xml_subs, inserts=ins)
    assert plan["inserts"][2].get("fit"), "фикстура: у видео нет геометрии"
    monkeypatch.setattr(build_mod, "_media_dims", lambda p: None)
    plan2, _t2, ip2 = _check(xml_subs, inserts=ins)
    assert "fit" not in plan2["inserts"][2] and "fit" not in ip2.inserts[2], \
        "дверь считает размеры своей копией _media_dims"


def test_plan_inserts_matches_without_inserts(xml_subs):
    """Ролик без вставок: обе двери не выдумывают ничего — пустой список и пустая строка."""
    plan, timings, ip = _check(xml_subs)
    assert plan["inserts"] == []
    assert ip.inserts == [] and ip.inserts_js == "[]" and ip.video_segs == []
    assert timings.inserts == []


def test_plan_insert_timings_touches_only_its_copy(xml_subs, tmp_path):
    """Правки «по месту» остаются на КОПИЯХ: словари вызывающего (вставки из UI) целы —
    scene_plan делает копии ДО вызова блока, и дверь их не портит (иначе вторая сборка
    того же ролика получила бы уже прижатые тайминги)."""
    ins = _ins(tmp_path)
    frozen = [dict(x) for x in ins]
    meta, cams, _s, _x = xml2ae.parse_full(xml_subs)
    fps = meta["fps"] or 60
    copies = [dict(x) for x in ins]            # ровно то, что делает scene_plan
    plan_insert_timings(InsertTimingInputs(
        inserts=copies, fps=fps,
        cam_change_sec=[f / fps for f in _cam_change_frames(cams)],
        active_cam_at=lambda t: _active_cam_at(cams, t, fps),
        style=read_style(styles.resolve({"insert_snap_cut": True})),
        emit=lambda *a, **k: None))
    assert ins == frozen, "тайминги правят словари вызывающего"

def test_plan_inserts_plate_geometry_ties_plan_and_jsx(xml_subs, tmp_path):
    """Подложка считается один раз: те же ps/px/py уезжают и в план, и в .jsx (второй
    копии геометрии подложки в шаблоне нет)."""
    plate_file = _png(tmp_path / "plate.png", 1024, 1024)
    style = {"insert_plate_file": plate_file}
    ins = _ins(tmp_path, plate=True)
    plan = xml2ae.scene_plan(xml_subs, **_call(style, ins))
    jsx, _n, _s = xml2ae.to_ae_full(xml_subs, return_source=True, **_call(style, ins))
    want = plan["inserts"][3]                     # фото на подложке — последнее
    got = json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))[3]
    assert got.get("plate") is True, "в .jsx уехала не подложка"
    for key in ("ps", "px", "py", "scale", "plate"):
        assert got[key] == want[key], f"ins.{key}: в .jsx {got[key]!r}, в плане {want[key]!r}"


def test_plan_inserts_has_no_duplicates_and_no_cycle():
    """Двери не заводят своих копий общего: обёрток стиля, размеров файла, активной камеры
    и точек смены камеры — и не импортируют build.py (тот импортирует их сам, цикл сломал бы
    `import core.xml2ae`)."""
    import inspect

    from core.xml2ae import plan_inserts as mod
    src = inspect.getsource(mod)
    for name in ("def _sv(", "def _sv_or(", "def _media_dims(", "def _active_cam_at(",
                 "def _cam_change_frames("):
        assert name not in src, f"в plan_inserts.py завелась копия {name}"
    for imp in ("from .build", "from core.xml2ae.build", "import build"):
        assert imp not in src, f"plan_inserts.py тянет build.py: {imp}"
