# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""X/Y видеовставки двигают свободно при ЛЮБОМ масштабе.

Было (жалоба владельца 2026-09-19: «на шаге 2 у видеовставки есть X и Y, они работают не
всегда, некоторые видео просто не двигаются»): сдвиг зажимался запасом вылета ролика за
кадр — `_fill_slack` в xml2ae/layout.py, кламп `if k >= 1` в сборке (xml2ae/build.py) и
такие же клампы в предпросмотре (`insVideoPan` и `ipvInsPlace` в static/app/85-inserts-view.js).
Ландшафтный 16:9 ездил только по X (~1160 px в каждую сторону, по высоте запаса нет), а
вертикальный 9:16 — тот, у которого запаса нет ни по одной оси, — не двигался вовсе:
X и Y у него обнулялись.

Стало: позиция = ровно x/y, какие задал пользователь, — в плане сцены, в .jsx (INSERTS)
и в предпросмотре (px кадра × k), при любом масштабе. Уехав за край ролика, вставка
открывает то, что под ней, — кадр камеры; в AE ровно так же. Поля slackx/slacky в плане
остаются СПРАВКОЙ (сколько ролик вылезает за кадр при заполнении) — позицию они не режут,
и обе стороны считают их по-прежнему одинаково.

Ожидания клампа, переписанные под «без клампа» (было → стало):
  * `insVideoPan(vw,vh,x,y).x`: было `max(-slackx, min(slackx, x))` → стало `x`;
  * `insVideoPan(vw,vh,x,y).y`: было `max(-slacky, min(slacky, y))` → стало `y`;
  * `insVideoPan(0,0,x,y)`: было `{x:0,y:0}` → стало `{x,y}` как заданы (размеров нет —
    считать запас не из чего, но позицию это не отменяет);
  * `insVideoPan(...)` на утянутом в бесконечность ползунке: было `[slackx,-slacky]`
    → стало `[99999,-99999]`;
  * `ipvInsPlace` (видео, `sc >= 100`): было `max(-slackx, min(slackx, x))`
    → стало `x` (плюс сдвиг драга, без клампа);
  * `_fill_slack` и `slackx`/`slacky` в плане: без изменений — это справка, а не граница.

Здесь гоняются НЕ копии формул, а сам отгружаемый код: JS-функции вырезаются из боевого
файла (предпросмотр — node-стендом из tests/test_ins_video_preview.py), Python — напрямую
из layout/build. На одних и тех же входах.

Запуск: python -m pytest tests/test_insert_video_pan.py -q
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

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import _fill_slack, _fit_scale  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node"),
                                reason="сверка двух реализаций требует node в PATH")

# кадр и набор пропорций: ландшафт, вертикаль, 4K, квадрат, уже кадра
W, H = 1080, 1920
SIZES = [(1920, 1080), (3840, 2160), (1080, 1920), (1000, 1000), (720, 1280), (640, 480)]

# вставка-видео шага 3 ровно та, на которой ловился баг: вертикальный 9:16 (запаса вылета
# нет ни по одной оси) при sc=100 и сдвигом вправо-вверх
VERT = {"type": "video", "media": "C:/x/vert.mp4", "start_s": 12.0, "start_f": 0,
        "dur_s": 2.0, "dur_f": 0, "x": 200, "y": -150, "sc": 100}

# .jsx для вставки с x=y=0 — снят с кода ДО правки клампа: у нулевого сдвига зажимать
# нечего, поэтому запись обязана остаться побайтово прежней
JSX_ZERO = ('[{"t":"video","style":"cam2","media":"C:/x/vert.mp4","start":12,"end":14,'
            '"scale":44,"mosaic":false,"x":0,"y":0,"sc":100,"mw":100,"mh":100,"sin":0,'
            '"noexit":false,"front":true,"oncam2":false,"fit":100,"slackx":0,"slacky":0,'
            '"fitw":1080,"fith":1920}]')


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name} — её переименовали или удалили"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def _run_node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def app_js():
    """insVideoPan/insVideoFill — JS-предпросмотр вставок, вырезанные из боевого файла."""
    from core import app_meta
    pan = _func(app_meta.app_js_text(), "insVideoPan")
    fill = _func(app_meta.app_js_text(), "insVideoFill")
    script = (
        "var W=1080,H=1920;\n%s\n%s\n"
        "var out=%s.map(function(s){\n"
        "  var a=insVideoPan(s[0],s[1],0,0);\n"
        "  var far=insVideoPan(s[0],s[1],99999,-99999);\n"
        "  var nodims=insVideoPan(0,0,200,-150);\n"
        "  var fv=insVideoFill(s[0],s[1],100);\n"
        "  return {app:[a.sx,a.sy], far:[far.x,far.y], nodims:[nodims.x,nodims.y],"
        "          fill:fv.w/s[0]*100};});\n"
        "console.log(JSON.stringify(out));"
    ) % (pan, fill, json.dumps([list(s) for s in SIZES]))
    return _run_node(script)


@pytest.fixture()
def xml_subs(tmp_path):
    """Клип-фикстура сборки (та же, что у golden-тестов геометрии)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def vertical_dims(monkeypatch):
    """Размеры 9:16 для vert.mp4: файла в тесте нет, а геометрия без размеров не считается.

    Подменяем `_media_dims` в build (как в бою вернул бы `draftrender._display_dims`).
    """
    from core.xml2ae import build as build_mod

    def _dims(path):
        return (1080, 1920) if (path or "").endswith("vert.mp4") else None

    monkeypatch.setattr(build_mod, "_media_dims", _dims)


def _build_jsx(xml, tmp_path, inserts):
    """Собрать .jsx теми же входами, что golden-тесты геометрии (эталон тут не нужен)."""
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   inserts=[dict(x) for x in inserts],
                                   style={"intro_riser": False}, disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _ins_jsons(jsx):
    return json.loads(re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1))


def _ins_record(jsx):
    """Запись INSERTS как есть, текстом — для сверки «побайтово»."""
    return re.search(r"var INSERTS=(\[.*?\]);", jsx).group(1)


def test_slack_matches_between_jsx_and_preview(app_js):
    """Запас вылета у layout.py и у предпросмотра совпадает — он остался СПРАВКОЙ.

    Позицию он больше не режет (см. тесты ниже), но считается по-прежнему в одном месте:
    разъедутся молча — предпросмотр перестанет показывать, сколько ролику есть куда ехать.
    """
    for size, r in zip(SIZES, app_js):
        py = list(_fill_slack(size[0], size[1], W, H, 1.0))
        assert py == r["app"], f"{size[0]}x{size[1]}: layout {py} != предпросмотр {r['app']}"


def test_fit_matches_between_jsx_and_preview(app_js):
    """Масштаб заполнения кадра (уходит в .jsx как ins.fit) — тоже как в предпросмотре."""
    for size, r in zip(SIZES, app_js):
        py = _fit_scale(size[0], size[1], True, W, H, 1.0)
        assert abs(py - r["fill"]) < 1e-6, f"{size[0]}x{size[1]}: layout {py} != предпросмотр {r['fill']}"


def test_landscape_pans_wide_and_not_at_all_vertically(app_js):
    """16:9 в вертикальном кадре: по ширине вылет большой, по высоте — нулевой."""
    r = app_js[SIZES.index((1920, 1080))]
    assert r["app"][0] > 1000, "ландшафтный ролик обязан иметь вылет по X"
    assert r["app"][1] == 0, "по высоте вылета нет — ролик ровно по высоте кадра"


def test_vertical_source_has_no_slack_at_all(app_js):
    """Ролик ровно по кадру не вылезает за него ни по одной оси — запаса нет вовсе."""
    r = app_js[SIZES.index((1080, 1920))]
    assert r["app"] == [0, 0]


def test_far_drag_is_not_clamped_anymore(app_js):
    """Утянутый в бесконечность ползунок уезжает как задан: клампа запасом больше нет.

    Было: `far == [slackx, -slacky]` (упирались в вылет ролика за кадр) → стало: ровно
    то, что просили, — 99999/-99999. Владелец: X и Y двигают свободно при любом масштабе.
    """
    for size, r in zip(SIZES, app_js):
        assert r["far"] == [99999, -99999], f"{size[0]}x{size[1]}: {r['far']}"


def test_no_dims_still_keeps_the_given_position(app_js):
    """Размеров файла нет — запас посчитать не из чего, но позицию это не отменяет.

    Было: `insVideoPan(0,0,200,-150) == {x:0,y:0}` → стало `{x:200,y:-150}` (кадр вставки
    не остаётся приколотым к центру, пока грузятся метаданные).
    """
    for size, r in zip(SIZES, app_js):
        assert r["nodims"] == [200, -150], f"{size[0]}x{size[1]}: {r['nodims']}"


def test_vertical_916_keeps_x_y_in_plan_and_jsx(xml_subs, vertical_dims, tmp_path):
    """Главный случай бага: 9:16, sc=100, x=200, y=-150 — числа доезжают целыми.

    Было: план (и .jsx следом) отдавал x=0, y=0 — запас вылета у вертикального ролика
    нулевой, и кламп `if k >= 1` обнулял сдвиг, видео стояло на месте. Стало: x=200,
    y=-150 и в плане сцены, и в данных INSERTS собранного .jsx.
    """
    ins = [dict(VERT)]
    plan = xml2ae.scene_plan(xml_subs, inserts=ins, emit=lambda *a: None)
    assert len(plan["inserts"]) == 1
    item = plan["inserts"][0]
    assert (item["x"], item["y"]) == (200, -150), f"план зажал позицию: {item['x']},{item['y']}"
    # запас остался справкой: у 9:16 он нулевой — и именно поэтому кламп всё и обнулял
    assert (item["slackx"], item["slacky"]) == (0, 0), "у вертикального ролика вылета нет"
    assert (item["fitw"], item["fith"]) == (1080, 1920), "коробка заполнения — кадр исходника"

    data = _ins_jsons(_build_jsx(xml_subs, tmp_path, ins))
    assert len(data) == 1
    assert (data[0]["x"], data[0]["y"]) == (200, -150), \
        f".jsx зажал позицию: {data[0]['x']},{data[0]['y']}"


def test_zero_offset_insert_is_byte_identical(xml_subs, vertical_dims, tmp_path):
    """x=y=0 — запись INSERTS побайтово прежняя (зажимать нулевой сдвиг нечего).

    Сторож от лишних правок в сборке: у нулевой позиции кламп и без клампа дают одно и
    то же, поэтому строка обязана совпасть с эталоном, снятым до правки.
    """
    ins = [dict(VERT, x=0, y=0)]
    assert _ins_record(_build_jsx(xml_subs, tmp_path, ins)) == JSX_ZERO
    # и план у нулевой позиции такой же, как был: x/y без изменений, справка на месте
    item = xml2ae.scene_plan(xml_subs, inserts=ins, emit=lambda *a: None)["inserts"][0]
    assert (item["x"], item["y"]) == (0, 0)


def test_preview_moves_the_frame_by_x_and_y_times_k():
    """Превью: кадр вставки едет на x·k / y·k — 9:16, sc=100, без клампа запасом.

    Гоняется node-стенд из ME (tests/test_ins_video_preview.py): боевые функции
    `ipvOverlayPlan`/`ipvInsPlace` на мини-DOM, сцена ровно кадр 1080 (k=1) и полкадра
    (k=0.5) — сдвиг масштабируется вместе с кадром превью. Было (sc>=100): кламп
    `max(-slack, min(slack, x))`, у 9:16 давал translate(0px,0px) — вставка стояла.
    """
    from tests.test_ins_video_preview import _DOM_JS, _js as preview_js, _run_node as node_run
    code = preview_js() + _DOM_JS + """
    STAGE_W=1080;
    var full=build([mkItem(100,{fitw:1080,fith:1920,slackx:0,slacky:0,x:200,y:-150})]);
    var tfFull=full.style.transform, w=full.style.width, h=full.style.height;
    STAGE_W=540;                                    // полкадра превью: k=0.5
    var half=build([mkItem(100,{fitw:1080,fith:1920,slackx:0,slacky:0,x:200,y:-150})]);
    console.log(JSON.stringify({tfFull:tfFull, w:w, h:h, tfHalf:half.style.transform}));
    """
    out = node_run(code)

    assert out["tfFull"] == "translate(200px,-150px)", \
        f"кадр вставки не поехал на x/y: {out['tfFull']}"
    assert out["w"] == "1080px" and out["h"] == "1920px", \
        f"9:16 при sc=100 обязан быть ровно по кадру: {out['w']}x{out['h']}"
    assert out["tfHalf"] == "translate(100px,-75px)", \
        f"сдвиг не масштабируется на k превью: {out['tfHalf']}"


def test_preview_does_not_clamp_a_landscape_pan_either():
    """Тот же стенд, но 16:9: раньше по Y не двигалось ВООБЩЕ, по X — только до вылета.

    Было: x=99999 упирался в slackx (~1166), y=-99999 — в нулевой slacky. Стало: числа
    как заданы; уехав за край ролика, кадр открывает камеру под вставкой.
    """
    from tests.test_ins_video_preview import _DOM_JS, _js as preview_js, _run_node as node_run
    code = preview_js() + _DOM_JS + """
    var f=insVideoFill(1920,1080,100);
    var v=build([mkItem(100,{fitw:f.w,fith:f.h,slackx:(f.w-1080)/2,slacky:0,x:99999,y:-99999})]);
    console.log(JSON.stringify({tf:v.style.transform, w:v.style.width}));
    """
    out = node_run(code)

    assert out["tf"] == "translate(99999px,-99999px)", \
        f"ландшафтную вставку снова зажали запасом вылета: {out['tf']}"


def test_video_start_uses_clamped_sin():
    """`файл с` (sin) не должен уводить outPoint за конец исходника: AE ругается и
    вставки в проекте не будет. Раньше startTime считался от сырого ins.sin —
    контракт держим тем, что в шаблоне остаётся именно ограниченная переменная."""
    src = xml2ae.AE_FULL
    assert "vl.startTime=t0-vsin;" in src, "startTime снова считается от неограниченного sin"
    assert re.search(r"vsin\s*=\s*Math\.min\(\s*vsin\s*,\s*Math\.max\(0,\s*vdur", src), \
        "пропал зажим sin по длительности исходного файла"
