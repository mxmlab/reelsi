# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Положение интро на камере 2 — своё, а не добавка к камере 1.

Жалоба: двигаешь интро на камере 1 — уезжает и интро на камере 2, причём «не так», а
скомпенсировать отрицательным intro_y2 нельзя. Причины (обе в коде, обе закрыты тут):

1. нул «интро на кам2» стоял на INTRO_Y + INTRO_Y2 (intro_y2 — добавка);
2. он был ребёнком нула Камеры 1: на перебивке её зум спрятан, но множил и позицию, и
   размер текста вокруг чужой точки наезда.

Теперь intro_y2 — положение само по себе, старые стили переводятся при чтении
(intro_y2 = intro_y + intro_y2, метка intro_pos2_v), нул кам2 — ребёнок нула КАМЕРЫ 2
при её зуме, иначе без родителя. Превью считает то же (ipvIntroChild с on2).
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

from core import styles, xml2ae  # noqa: E402
from test_intro_detach import (  # noqa: E402
    DOM_SIM, JS_REL, _func, _run_node, node, _jsx_layers)

T_CAM1, T_CAM2 = 1.0, 8.3          # секунды: в кадре Камера 1 / перебивка (см. test_intro_on_cam2)
INTRO = [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
         dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])]
# Стиль НОВОГО формата: метка стоит, значения читаются как есть.
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


def _scene(xml, style):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False, intro=INTRO,
                             intro_splits=[1], style=dict(style), emit=lambda *a: None)


def _build(xml, tmp_path, style, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=INTRO,
                                   intro_splits=[1], intro_mode="word", style=dict(style),
                                   disclaimer="", intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _ys(plan):
    """y базовой позиции блока: [камера 1, камера 2]."""
    g = plan["intro"]
    assert [x["on2"] for x in g] == [False, True], "предпосылка: первая группа на кам1, вторая на кам2"
    return g[0]["y"], g[1]["y"]


# ------------------------------------------------ 1. независимость от кам1 (план и .jsx)

def test_сдвиг_камеры_1_не_двигает_камеру_2_в_плане(xml_subs):
    c1_a, c2_a = _ys(_scene(xml_subs, dict(NEW, intro_y=0, intro_y2=50)))
    c1_b, c2_b = _ys(_scene(xml_subs, dict(NEW, intro_y=300, intro_y2=50)))
    assert c1_b - c1_a == pytest.approx(300), "интро камеры 1 не поехало вслед за intro_y"
    assert c2_b == c2_a, "интро камеры 2 поехало вслед за intro_y камеры 1"


def test_положение_камеры_2_задаёт_только_intro_y2(xml_subs):
    _, c2_a = _ys(_scene(xml_subs, dict(NEW, intro_y=0, intro_y2=0)))
    _, c2_b = _ys(_scene(xml_subs, dict(NEW, intro_y=0, intro_y2=120)))
    assert c2_b - c2_a == pytest.approx(120)


def test_сдвиг_камеры_1_не_меняет_нул_кам2_в_jsx(xml_subs, tmp_path):
    a, pa = _build(xml_subs, tmp_path, dict(NEW, intro_y=0, intro_y2=50), "a.jsx")
    b, pb = _build(xml_subs, tmp_path, dict(NEW, intro_y=300, intro_y2=50), "b.jsx")
    assert "INTRO_Y=300;" in b and "INTRO_Y=0;" in a
    assert "var INTRO_Y2=50;" in a and "var INTRO_Y2=50;" in b
    la, lb = _jsx_layers(pa), _jsx_layers(pb)
    assert la["интро на кам2"]["pos"] == lb["интро на кам2"]["pos"] == [1080 / 2, 1920 / 2 + 50]
    assert lb["интро"]["pos"] != la["интро"]["pos"], "нул камеры 1 обязан ехать за intro_y"


def test_отрицательное_положение_камеры_2_доезжает_до_нула(xml_subs, tmp_path):
    """Минус — это просто выше: значение не зажимается и не теряется по дороге в .jsx."""
    _, path = _build(xml_subs, tmp_path, dict(NEW, intro_y=200, intro_y2=-180))
    lay = _jsx_layers(path)
    assert lay["интро на кам2"]["pos"] == [1080 / 2, 1920 / 2 - 180]


# ------------------------------------------------ 2. миграция старых стилей

def test_миграция_складывает_старые_значения():
    st = styles.resolve({"intro_y": 100, "intro_y2": 40})
    assert st["intro_y"] == 100 and st["intro_y2"] == 140
    assert st["intro_pos2_v"] == 2


def test_миграция_не_идёт_второй_раз():
    once = styles.resolve({"intro_y": 100, "intro_y2": 40})
    twice = styles.resolve(dict(once))
    assert twice["intro_y2"] == 140, "стиль с меткой пересчитался повторно"
    # новый стиль с теми же числами — положение как записано (кам2 нарочно на 40)
    fresh = styles.resolve({"intro_pos2_v": 2, "intro_y": 100, "intro_y2": 40})
    assert fresh["intro_y2"] == 40


def test_миграция_старый_стиль_без_intro_y2_наследует_положение_камеры_1():
    """Раньше intro_y2 = 0 значило «кам2 там же, где кам1» — так и остаётся."""
    assert styles.resolve({"intro_y": 100})["intro_y2"] == 100


def test_дефолтный_стиль_не_мигрирует():
    st = styles.resolve(None)
    assert st["intro_y"] == 0 and st["intro_y2"] == 0 and st["intro_pos2_v"] == 2
    assert styles.resolve({})["intro_y2"] == 0


# ------------------------------------------------ 2а. миграция ручек интро кам2

def test_миграция_ручек_кам2_наследует_камеру_1():
    """Стиль без intro_cam2/intro_scale_anchor2 берёт значения камеры 1 — вид тот же."""
    off = styles.resolve({"intro_cam": False, "intro_scale_anchor": "first"})
    assert off["intro_cam2"] is False
    assert off["intro_scale_anchor2"] == "first"

    on = styles.resolve({"intro_cam": True})
    assert on["intro_cam2"] is True and on["intro_scale_anchor2"] == "comp"

    empty = styles.resolve({})
    assert empty["intro_cam2"] is True and empty["intro_scale_anchor2"] == "comp"


def test_миграция_не_перебивает_явные_ключи_кам2():
    """Явно заданные ручки кам2 миграция не трогает (даже рядом со снятой галкой кам1)."""
    st = styles.resolve({"intro_cam": False, "intro_cam2": True,
                         "intro_scale_anchor": "block", "intro_scale_anchor2": "first"})
    assert st["intro_cam"] is False and st["intro_cam2"] is True
    assert st["intro_scale_anchor"] == "block" and st["intro_scale_anchor2"] == "first"


def test_копия_в_браузере_мигрирует_ручки_кам2_тем_же_правилом():
    """stMigrateIntroCam2 (95-styles.js) — то же правило, что styles.migrate_intro_cam2."""
    src = open(os.path.join(ROOT, "static", "app", "95-styles.js"), encoding="utf-8").read()
    body = _func(src, "stMigrateIntroCam2")
    if not shutil.which("node"):
        pytest.skip("требуется node в PATH")
    script = body + """
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
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["a"] == [False, "first"], "JS не наследует ручки кам2 от камеры 1"
    assert out["b"] == [True, "comp"]
    assert out["c"] == [True, "first"], "JS перебил явные ключи кам2"


def test_старый_стиль_даёт_то_же_положение_кам2_что_до_правки(xml_subs):
    """Старый стиль (a, b): раньше нул стоял на a + b. Теперь — на a + b через миграцию."""
    a, b = 100, 40
    _, base = _ys(_scene(xml_subs, dict(NEW, intro_y=0, intro_y2=0)))
    _, old = _ys(_scene(xml_subs, {"intro_y": a, "intro_y2": b}))          # без метки
    assert old - base == pytest.approx(a + b)


def test_файл_стиля_на_диске_не_переписывается(tmp_path, monkeypatch):
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path))
    p = tmp_path / "old.json"
    raw = json.dumps({"label": "old", "intro_y": 100, "intro_y2": 40,
                      "layer_order": ["subs", "video", "roto", "photo", "intro"]})
    p.write_text(raw, encoding="utf-8")
    got = styles.get("old")
    assert got["intro_y2"] == 140 and got["intro_pos2_v"] == 2
    assert p.read_text(encoding="utf-8") == raw, "миграция при чтении переписала файл стиля"
    # правка через patch: файл переводится ДО правки, чужие поля не складываются повторно
    styles.patch("old", {"intro_y2": 10})
    again = styles.get("old")
    assert again["intro_y"] == 100 and again["intro_y2"] == 10 and again["intro_pos2_v"] == 2


def test_копия_в_браузере_мигрирует_тем_же_правилом():
    """stMigrateIntroPos2 (95-styles.js) — то же правило, что styles.migrate_intro_pos2."""
    src = open(os.path.join(ROOT, "static", "app", "95-styles.js"), encoding="utf-8").read()
    body = _func(src, "stMigrateIntroPos2")
    import subprocess
    if not shutil.which("node"):
        pytest.skip("требуется node в PATH")
    script = body + """
    const a = stMigrateIntroPos2({intro_y: 100, intro_y2: 40});
    const b = stMigrateIntroPos2({intro_y: 100, intro_y2: 40, intro_pos2_v: 2});
    console.log(JSON.stringify([a.intro_y2, a.intro_pos2_v, b.intro_y2]));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip().splitlines()[-1]) == [140, 2, 40]


# ------------------------------------------------ 3. родитель нула «интро на кам2»

def test_нул_кам2_не_ребёнок_камеры_1_ни_при_каких_галках(xml_subs, tmp_path):
    for i, st in enumerate(({}, {"intro_cam": True}, {"intro_cam": False},
                            {"cam2_zoom": "pulse"}, {"cam2_zoom": "pulse", "intro_cam": False})):
        jsx, _ = _build(xml_subs, tmp_path, st, "p%d.jsx" % i)
        assert "introNull2.parent=cam1null" not in jsx, f"нул кам2 привязан к камере 1 при {st}"


def test_без_зума_кам2_нул_без_родителя(xml_subs, tmp_path):
    """Камера 2 неактивна (ни зума, ни трансформа) — нула cam2null в сборке нет вовсе."""
    jsx, path = _build(xml_subs, tmp_path, dict(NEW, intro_y2=30))
    assert "introNull2.parent" not in jsx
    lay = _jsx_layers(path)
    assert lay["интро на кам2"]["parent"] is None


def test_при_зуме_кам2_нул_ребёнок_нула_камеры_2(xml_subs, tmp_path):
    st = dict(NEW, intro_y2=30, cam2_zoom="pulse", intro_x=20)
    jsx, path = _build(xml_subs, tmp_path, st)
    assert "introNull2.parent=cam2null" in jsx
    # родитель назначается ПОСЛЕ объявления cam2null: раньше он был бы null
    assert jsx.index("var cam2null") < jsx.index("introNull2.parent=cam2null")
    assert re.search(r"introNull2\.property\(\"ADBE Transform Group\"\)\.property\(\"ADBE Position\"\)"
                     r"\.setValue\(\[20,INTRO_Y2\]\)", jsx)


def test_откреплённый_по_своей_галке_нул_кам2_без_родителя(xml_subs, tmp_path):
    """intro_cam2=False — нул кам2 в координатах кадра даже при активном зуме камеры 2."""
    jsx, path = _build(xml_subs, tmp_path,
                       dict(NEW, cam2_zoom="pulse", intro_cam2=False))
    assert "introNull2.parent" not in jsx
    lay = _jsx_layers(path)
    assert lay["интро на кам2"]["parent"] is None


def test_галка_камеры_1_не_двигает_нул_камеры_2(xml_subs, tmp_path):
    """Ручки камер независимы: сняв одну, вторая остаётся привязанной.

    Старый стиль (ключа intro_cam2 нет) наследует обе ручки от камеры 1 — миграция
    `migrate_intro_cam2`; дальше галки снимаются поодиночке.
    """
    # ключа intro_cam2 нет -> наследуется от intro_cam (старый стиль)
    old = _build(xml_subs, tmp_path,
                 dict(NEW, cam2_zoom="pulse", intro_cam=False), "old.jsx")
    assert "introNull2.parent" not in old[0], \
        "старый стиль снятой галки камеры 1 обязан открепить и интро на перебивке"

    only1 = _build(xml_subs, tmp_path,
                   dict(NEW, cam2_zoom="pulse", intro_cam=False, intro_cam2=True),
                   "only1.jsx")
    assert "if(cam1null && INTRO_CAM){ introNull.parent=cam1null;" in only1[0]
    assert "introNull2.parent=cam2null" in only1[0]
    lay1 = _jsx_layers(only1[1])
    assert lay1["интро на кам2"]["parent"] == "Камера 2"

    only2 = _build(xml_subs, tmp_path,
                   dict(NEW, cam2_zoom="pulse", intro_cam=True, intro_cam2=False),
                   "only2.jsx")
    assert "if(cam1null){ introNull.parent=cam1null;" in only2[0]
    assert "introNull2.parent" not in only2[0]
    lay2 = _jsx_layers(only2[1])
    assert lay2["интро на кам2"]["parent"] is None


# ------------------------------------------------ 4. превью: та же формула

def _preview_js(checks, plan):
    src = open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()
    body = "\n".join(_func(src, n) for n in
                     ("ipvCamChild", "ipvCam2Point", "ipvIntroChild", "ipvIntroPos"))
    return DOM_SIM.replace("@PLAN@", json.dumps(plan)) + "\n" + body + "\n" + checks


@node
def test_превью_группа_кам2_не_едет_за_зумом_кам1(tmp_path):
    plan = {"w": 1080, "h": 1920, "intro_cam": True, "intro_cam2": True, "zoom": {"cx": 0.3, "cy": 0.4},
            "intro_scale": 100,
            "intro": [{"dx": 0, "dy": 0, "y": 100, "ds": 100, "on2": False, "cam": True},
                      {"dx": 0, "dy": 0, "y": 100, "ds": 100, "on2": True, "cam": True}]}
    checks = r"""
    const io = $('ipvintro');
    ipvIntroPos(1, 2.0);
    const a = io.style.transform;
    CAM.s = 3.5; CAM.shift = [700, -500];
    ipvIntroPos(1, 2.0);
    assert.strictEqual(io.style.transform, a, 'группа на кам2 поехала за зумом камеры 1: ' + a);
    assert(a.indexOf('scale(0.968)') >= 0, 'зум камеры 1 попал в размер текста кам2: ' + a);
    assert(a.indexOf('translate(0px,50px)') >= 0, 'база кам2 не в координатах кадра: ' + a);
    ipvIntroPos(0, 2.0);
    assert(io.style.transform !== a, 'группа кам1 обязана ехать за своим зумом');
    console.log('OK cam2 free');
    """
    res = _run_node(tmp_path, "t_c2free.js", _preview_js(checks, plan))
    assert res.returncode == 0, f"{res.stderr}\n{res.stdout}"
    assert "OK cam2 free" in res.stdout


@node
def test_превью_при_зуме_кам2_блок_едет_от_её_точки_наезда(tmp_path):
    plan = {"w": 1080, "h": 1920, "intro_cam": True, "intro_cam2": True,
            "zoom": {"cx": 0.5, "cy": 0.5, "cam2": {"cx": 0.25, "cy": 0.5}},
            "intro_scale": 100,
            "intro": [{"dx": 0, "dy": 0, "y": 100, "ds": 100, "on2": True, "cam": True}]}
    checks = r"""
    const io = $('ipvintro');
    ipvIntroPos(0, 2.0);
    // s=2, точка наезда кам2 (0.25, 0.5): от центра dx=-270, dy=0 -> x = (1-2)*(-270) = 270,
    // y = 2*100 = 200; на превью k=0.5 -> translate(135px,100px), масштаб 0.968*2
    const t = io.style.transform;
    assert(t.indexOf('translate(135px,100px)') >= 0, 'позиция от точки наезда кам2: ' + t);
    assert(t.indexOf('scale(1.936)') >= 0, 'зум кам2 не умножен в масштаб: ' + t);
    // Своя галка камеры 2 (intro_cam2), а НЕ камеры 1: сняв только intro_cam,
    // блок на перебивке обязан остаться на месте
    IPV.plan.intro_cam = false;
    ipvIntroPos(0, 2.0);
    assert(io.style.transform.indexOf('scale(1.936)') >= 0,
      'галке камеры 1 нечего делать на группе камеры 2: ' + io.style.transform);
    IPV.plan.intro_cam2 = false;
    ipvIntroPos(0, 2.0);
    assert(io.style.transform.indexOf('scale(0.968)') >= 0, 'открепление не сняло зум кам2');
    console.log('OK cam2 zoom');
    """
    res = _run_node(tmp_path, "t_c2zoom.js", _preview_js(checks, plan))
    assert res.returncode == 0, f"{res.stderr}\n{res.stdout}"
    assert "OK cam2 zoom" in res.stdout
