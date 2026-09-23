# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: камера (этап 6).

Камера уехала из `scene_plan` в `core/xml2ae/plan_camera.py` одной дверью `plan_camera`:
параметры Камеры 1 из стиля (точка наезда, pan, поворот), ключи зума по режимам
(pulse/jump/drift/none) с наездами в тейках и «заполнением кадра», тип интерполяции
ключей (`holds`), разметка рото, слежение за головой и подстановки шаблона
(CAM1_SCALE/CAM1_HOLDS/CAM1_EASE/CAM1_FOLLOW, якорь точки наезда, позиции и повороты
рото-копий).

Сторож держит СТЫК: дверь, вызванная НАПРЯМУЮ на фикстуре, обязана отдать ровно то, что
`scene_plan` кладёт в план (`plan["zoom"]`, `plan["roto"]`) и в шаблон (те же подстановки
в собранном .jsx). Разъедутся — предпросмотр покажет один кадр, а AE соберёт другой, и
увидеть это можно только в AE.

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока: разбор XML,
резолв стиля, слова субтитров и индексы жёлтых (наезды в тейках), флаг ротоскопа и путь
XML (рядом с ним кэш трека головы). Своей копии арифметики камеры тут нет намеренно —
иначе сторож проверял бы копию правила. Общее с другими блоками не копируется: `_sv`/
`_sv_or` берутся из build, ключи зума и разметка рото считает `layout`.

Запуск: python -m pytest tests/test_plan_camera_split.py -q
"""
import gzip
import inspect
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import styles, xml2ae  # noqa: E402
from core.xml2ae.build import read_style  # noqa: E402
from core.xml2ae.jsutil import _jd, _r  # noqa: E402
from core.xml2ae.layout import _span_roto_plan  # noqa: E402
from core.xml2ae.plan_camera import CameraInputs, plan_camera  # noqa: E402

# Трек головы: то же синтетическое движение, что подставляет test_r11_li_every_knob.py —
# без него ветка слежения не проверилась бы вовсе (ролика фикстуры на диске нет).
HEAD = {"v": 1, "fps": 10, "w": 2160, "h": 3840,
        "pts": [[i / 10.0, 0.5 + 0.15 * ((i % 40) / 40.0)] for i in range(0, 400)]}


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


def _call(style=None, **kw):
    """Аргументы сборки — одни и те же у scene_plan и у двери камеры (свежие копии)."""
    args = dict(style=dict(style or {}), disclaimer="", intro_riser=False,
                emit=lambda *a, **k: None)
    args.update(kw)
    return args


def _door(xml, style=None, highlights=None, **kw):
    """Дверь камеры с входами ровно такими, какими их собрал бы scene_plan.

    Повторяет только ЧТЕНИЯ scene_plan: разбор XML, резолв стиля, чтение структуры
    стиля (`read_style`), индексы жёлтых (по ним ставятся наезды в тейках) и путь XML
    (кэш трека головы). Своей копии арифметики камеры здесь нет намеренно.
    """
    meta, cams, subs, _xi = xml2ae.parse_full(xml)
    st = styles.resolve(dict(style or {}))
    fps = meta["fps"] or 60
    hl = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    return plan_camera(CameraInputs(
        cams=cams, meta=meta, fps=fps, subs=subs, hl=hl,
        style=read_style(st),
        cam1_scale=kw.get("cam1_scale"), roto=bool(kw.get("roto")), xml_path=xml))


def _jsx(xml, style=None, **kw):
    """Собранный .jsx (без записи файла) — теми же аргументами, что у плана."""
    src, _n, _s = xml2ae.to_ae_full(xml, return_source=True, **_call(style, **kw))
    return src


def _check(xml, style=None, **kw):
    """Сверить дверь камеры с планом scene_plan; -> (plan, CameraPlan).

    Сверяется не только «план против двери», но и связка данных камеры с подстановками
    шаблона: ключи -> CAM1_SCALE/CAM1_HOLDS/CAM1_EASE, holds -> zoom["holds"].
    """
    plan = xml2ae.scene_plan(xml, **_call(style, **kw))
    cam = _door(xml, style=style, **kw)
    ae = plan["_ae"]
    assert cam.zoom == plan["zoom"], "план камеры разошёлся с дверью plan_camera"
    assert cam.roto == plan["roto"], "разметка рото разошлась с дверью plan_camera"
    assert [1 if h else 0 for h in cam.holds] == plan["zoom"]["holds"], \
        "holds разошлись с zoom[\"holds\"] плана"
    # Подстановки шаблона собираются из ЭТИХ ключей, а не из своей копии: формат — тот же,
    # что печатает план (при расхождении .jsx собрался бы не по плану).
    keys_js = _jd([([_r(f), _r(v)] + ([int(rest[0])] if rest else []))
                   for f, v, *rest in (cam.cam1_scale or [])])
    assert cam.cam1scale_js == ae["cam1scale"] == keys_js, "CAM1_SCALE не из ключей двери"
    assert cam.cam1holds_js == ae["cam1holds"] == _jd(plan["zoom"]["holds"]), \
        "CAM1_HOLDS не из holds двери"
    assert cam.cam1_ease_js == ae["cam1_ease"], "CAM1_EASE не из двери"
    assert plan["zoom"]["keys"] == cam.cam1_scale, "ключи плана не из двери"
    assert (ae["cam1_cx"], ae["cam1_cy"]) == (cam.cam1_cx, cam.cam1_cy)
    return plan, cam


def test_plan_camera_matches_plan_and_jsx(xml_subs):
    """Дефолтный стиль: ключи зума, holds и разметка рото из одной двери — и в плане,
    и в .jsx (там же подстановки CAM1_SCALE/CAM1_HOLDS/CAM1_EASE)."""
    plan, cam = _check(xml_subs)
    assert len(cam.cam1_scale) > 1, "фикстура: зум без ключей — сторож проверял бы пустоту"
    assert len(cam.holds) == len(cam.cam1_scale)
    jsx = _jsx(xml_subs)
    assert "var CAM1_SCALE=" + cam.cam1scale_js + ";" in jsx, "CAM1_SCALE в .jsx не из двери"
    assert "var CAM1_HOLDS=" + cam.cam1holds_js + ";" in jsx, "CAM1_HOLDS в .jsx не из двери"
    assert "CAM1_EASE=" + cam.cam1_ease_js + ";" in jsx, "CAM1_EASE в .jsx не из двери"
    assert "var CAM1_FIT=100;" in jsx, "заполнение кадра не уехало в ключи зума"
    # дефолты стиля: камера не сдвинута — подстановки пустые, .jsx прежний (golden)
    assert cam.cam1_anchor == "" and cam.cam1_rot_decl == ""
    assert not cam.cam1_follow_decl and not cam.cam1_follow_js


@pytest.mark.parametrize("style", [
    {"cam1_zoom": "none"},
    {"cam1_zoom": "pulse"},
    {"cam1_zoom": "drift"},
    {"cam1_zoom": "jump"},
    {"cam1_zoom": "jump", "cam1_take_zoom": True, "cam1_take_hi": 170},
])
def test_plan_camera_zoom_modes(xml_subs, style):
    """Режимы зума: каждая ветка доезжает до плана и .jsx через дверь (ключи — из неё)."""
    plan, cam = _check(xml_subs, style=style)
    assert plan["zoom"]["keys"] == cam.cam1_scale
    assert "var CAM1_SCALE=" + cam.cam1scale_js + ";" in _jsx(xml_subs, style=style)
    if style["cam1_zoom"] != "none":
        assert len(cam.cam1_scale) > 1, f"{style}: ветка режима не дала ключей"


def test_plan_camera_zoom_modes_differ(xml_subs):
    """Ветки режимов действительно разные — иначе параметризация выше ничего не проверяет."""
    keys = {}
    for mode in ("none", "pulse", "drift", "jump"):
        keys[mode] = [k[1] for k in _check(xml_subs, style={"cam1_zoom": mode})[1].cam1_scale]
    assert keys["none"] == [100.0], "режим none обязан дать один ключ 100%"
    assert keys["pulse"] != keys["jump"] != keys["drift"], "режимы зума дали одни и те же ключи"


def test_plan_camera_frame_fill_multiplies_keys_once(xml_subs):
    """«Заполнение кадра» умножает ключи РОВНО раз: в плане они уже ×k, а CAM1_FIT=100
    (иначе превью и AE умножали бы дважды), и хвост ключа (mode дрейфа) не портится."""
    base = _check(xml_subs, style={"cam1_zoom": "drift"})[1]
    fit = _check(xml_subs, style={"cam1_zoom": "drift", "cam1_fit": 130})[1]
    assert [k[0] for k in fit.cam1_scale] == [k[0] for k in base.cam1_scale]
    for got, ref in zip(fit.cam1_scale, base.cam1_scale):
        assert got[1] == pytest.approx(round(ref[1] * 1.3, 2))
        assert got[2:] == ref[2:], "хвост ключа (режим дрейфа) умножению не подлежит"
    assert fit.zoom["fit"] == 100.0 and base.zoom["fit"] == 100.0
    assert "var CAM1_FIT=100;" in _jsx(xml_subs, style={"cam1_zoom": "drift", "cam1_fit": 130})


@pytest.mark.parametrize("style,moved,rotated", [
    ({"cam1_zoom_cx": 0.4, "cam1_zoom_cy": 0.244}, True, False),
    ({"cam1_pan_x": 40}, True, False),
    ({"cam1_pan_y": -25}, True, False),
    ({"cam1_rot": -1.2}, False, True),
    ({"cam1_pan_x": 40, "cam1_rot": 2.5}, True, True),
])
def test_plan_camera_shift_and_rotation_reach_jsx(xml_subs, style, moved, rotated):
    """Точка наезда, pan и поворот: подстановки непустые и уезжают в .jsx теми же
    строками, что отдаёт дверь. Поворот своей галки не имеет (cam1_moved его не
    включает) — у него свои подстановки, и пустыми они быть не обязаны."""
    plan, cam = _check(xml_subs, style=style)
    jsx = _jsx(xml_subs, style=style)
    assert bool(cam.cam1_anchor) is moved, "якорь точки наезда собран не по сдвигу камеры"
    if moved:
        assert cam.cam1_anchor in jsx, "якорь точки наезда в .jsx не из двери"
        assert cam.roto_pos_cc and cam.roto_pos_cc in jsx, \
            "компенсация позиции рото в .jsx не из двери"
        assert cam.roto_pos_mk in jsx
    else:
        assert cam.roto_pos_cc == "" and cam.roto_pos_mk == "", \
            "без сдвига камеры подстановки позиции рото обязаны быть пустыми (golden)"
    if rotated:
        assert cam.cam1_rot_decl and cam.cam1_rot_decl in jsx
        assert cam.cam1_rot_cam in jsx
        assert cam.roto_rot_cc in jsx and cam.roto_rot_mk in jsx
    else:
        assert cam.cam1_rot_decl == "" and cam.roto_rot_cc == "" and cam.roto_rot_mk == "", \
            "поворот 0 обязан дать пустые подстановки (golden)"
    # точка наезда уехала ровно тем числом, что в стиле (доли кадра уезжают как есть)
    if "cam1_zoom_cx" in style:
        assert (cam.cam1_cx, cam.cam1_cy) == (0.4, 0.244)
        assert plan["zoom"]["cx"] == 0.4 and plan["zoom"]["cy"] == 0.244


def test_plan_camera_defaults_are_empty_substitutions(xml_subs):
    """Дефолтная камера (0.5/0.5, pan 0, поворот 0): подстановки пустые, .jsx побайтово
    как без ручек — иначе golden держался бы на честном слове."""
    empty = _jsx(xml_subs, style={})
    same = _jsx(xml_subs, style={"cam1_zoom_cx": 0.5, "cam1_zoom_cy": 0.5,
                                 "cam1_pan_x": 0, "cam1_pan_y": 0, "cam1_rot": 0})
    assert empty == same, "явные дефолты камеры изменили .jsx"


@pytest.mark.parametrize("two_arg_cache", [False, True])
def test_plan_camera_follow_keys_and_cache_branches(xml_subs, monkeypatch, two_arg_cache):
    """Слежение за головой: ключи CAM1_FOLLOW из двери — и в плане, и в .jsx.

    Кэш читается тем же вызовом с запасной веткой: трекер без параметра `ranges` (так его
    подменяет test_r11_li_every_knob.py) обязан отработать через except TypeError — обе
    ветки дают одни и те же ключи, второй копии правила нет.
    """
    from core import headtrack
    if two_arg_cache:
        monkeypatch.setattr(headtrack, "load_cached", lambda xml_path, video: HEAD)
    else:
        monkeypatch.setattr(headtrack, "load_cached",
                            lambda xml_path, video, ranges=None: HEAD)
    style = {"cam1_head_follow": True, "cam1_head_min": 120}
    plan, cam = _check(xml_subs, style=style)
    follow = plan["zoom"].get("follow")
    assert follow, "фикстура: слежение не дало ключей — сторож проверял бы пустоту"
    assert follow == cam.zoom["follow"]
    assert len(follow["keys"]) > 1 and len(follow["ease"]) == len(follow["keys"])
    assert cam.cam1_follow_decl and cam.cam1_follow_decl in _jsx(xml_subs, style=style)
    assert cam.cam1_follow_js and cam.cam1_follow_js in _jsx(xml_subs, style=style)
    # галка снята — слежения нет ни в плане, ни в .jsx
    plan_off, cam_off = _check(xml_subs, style={"cam1_head_follow": False})
    assert "follow" not in plan_off["zoom"]
    assert cam_off.cam1_follow_decl == "" and cam_off.cam1_follow_js == ""


def test_plan_camera_roto_markup(xml_subs):
    """Разметка рото: фрагменты из двери — те же, что в плане (по ним to_ae_full делает
    маски), и правило «нужен исходник камеры» осталось одно."""
    plan, cam = _check(xml_subs, roto=True)
    assert cam.roto, "фикстура: рото без фрагментов — сторож проверял бы пустоту"
    assert cam.roto == plan["roto"]
    assert set(cam.roto[0]) == {"ci", "ts", "te", "src_start", "src_end", "scale"}
    meta, cams, _s, _x = xml2ae.parse_full(xml_subs)
    raw = _span_roto_plan(cams, 0, int(meta["dur"]), meta["fps"])
    assert len(cam.roto) == len([p for p in raw if cams[p["ci"]].get("path")]), \
        "правило «нужен исходник камеры» разошлось с layout._span_roto_plan"
    # галка снята — разметки нет (полоса «здесь рото» в превью не горит)
    assert _check(xml_subs, roto=False)[1].roto == []
    assert plan["_ae"]["roto"] == "[]"


def test_plan_camera_explicit_keys_and_highlights(xml_subs):
    """Ключи из kwarg перебивают режим, а наезды «по жёлтым» считаются по словам субтитров:
    одни и те же входы у сборки и у двери — одни и те же ключи."""
    keys = [(0, 100), (120, 160), (300, 120)]
    plan, cam = _check(xml_subs, cam1_scale=[(0, 100), (120, 160), (300, 120)],
                       style={"cam1_fit": 125})
    assert [(k[0], k[1]) for k in cam.cam1_scale] == \
        [(f, round(v * 1.25, 2)) for f, v in keys], "fit не умножил явные ключи"
    assert plan["zoom"]["keys"] == cam.cam1_scale
    take = {"cam1_zoom": "jump", "cam1_take_zoom": True, "cam1_take_yellow": True}
    plain, _c1 = _check(xml_subs, style=take)
    # Жёлтыми помечаем ВСЕ слова: наезд в тейке ждёт жёлтое слово с запасом на подъезд
    # (TAKE_YELLOW_MIN_LEAD_S) и влезает только в длинный тейк — пара первых слов
    # фикстуры туда не попадает, и ветка осталась бы непроверенной.
    all_words = list(range(len(xml2ae.parse_full(xml_subs)[2])))
    yellow, cam_y = _check(xml_subs, style=take, highlights=all_words)
    assert plain["zoom"]["keys"] != yellow["zoom"]["keys"], \
        "наезды по жёлтым не зависят от слов — ветка не проверена"
    assert "var CAM1_SCALE=" + cam_y.cam1scale_js + ";" in _jsx(xml_subs, style=take,
                                                               highlights=all_words)


def test_plan_camera_no_duplicates_and_no_cycle():
    """Дверь не заводит копий общего: обёрток стиля, ключей зума, разметки рото и слежения —
    и не импортирует build.py (тот импортирует её сам, цикл сломал бы `import core.xml2ae`)."""
    from core.xml2ae import plan_camera as mod
    src = inspect.getsource(mod)
    for name in ("def _sv(", "def _sv_or(", "def _zoom_key_holds(", "def _zoom_key_eases(",
                 "def _cam1_zoom_keys(", "def _cam1_jump_keys(", "def _cam1_drift_keys(",
                 "def _span_roto_plan(", "def _cam1_follow_keys("):
        assert name not in src, f"в plan_camera.py завелась копия {name}"
    for imp in ("from .build", "from core.xml2ae.build", "import build"):
        assert imp not in src, f"plan_camera.py тянет build.py: {imp}"


def test_scene_plan_keeps_no_camera_internals():
    """Сам scene_plan камеру больше не считает: ручек режимов, слежения и разметки рото в
    его теле нет — иначе после распила осталась бы вторая копия правила."""
    from core.xml2ae import build as build_mod
    src = inspect.getsource(build_mod.scene_plan)
    for name in ("cam1_take_yellow", "cam1_take_zoom", "cam1_drift_lo", "cam1_head_x",
                 "cam1_pan_x", "_zoom_key_holds", "_zoom_key_eases", "_span_roto_plan",
                 "_cam1_follow_keys", "_cam1_zoom_keys"):
        assert name not in src, f"в scene_plan осталась камера: {name}"
    assert "plan_camera(CameraInputs(" in src, "scene_plan не зовёт дверь камеры"
