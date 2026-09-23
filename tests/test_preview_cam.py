# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: кадр предпросмотра — весь исходник через одну матрицу камеры.

Что стерегут (каждый — пойманный дефект):

1. `ipvCamMatrix` — правило «экран = C + S·(R·p − C_c) + T» (как в AE после ZE): при
   S=1, rot=0, T=0 центр исходника встаёт в центр кадра, точка наезда C неподвижна при
   любом S, сдвиг T переносит всё на себя, а поворот вокруг центра исходника его не
   двигает (в AE крутится слой камеры вокруг своего якоря, а не нул).
2. `ipvCamPaint` берёт ИСХОДНЫЙ прямоугольник `0,0,vw,vh` — весь кадр, а не вырезку
   кадра экрана: вырезанный кусок при сдвиге и слежении тащил за собой края, и по
   краям открывались полосы и мазня, которых в AE нет (там двигается весь исходник).
3. Вставки на СВОБОДНЫХ нулах (кам2 и «кам1 на кам2») не едут с кадром: `pan` и
   слежение Камеры 1 на них не влияют. Вставка кам1 на кам1 — едет (дитя нула Камеры 1).
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_cam1_zoom_none.py)."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
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


def _js(*names):
    """Настоящие тела функций из static/app/85-inserts-view.js — для прогона в node."""
    with open(JS, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_func(src, n) for n in names)


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---- план сцены для node: кадр 1080×1920, ключи зума в кадрах, ease как у плана ----
_PLAN_JS = """
function zoomPlan(scale, rot, pan, cx, cy, follow){
  const z = {cx: cx, cy: cy, rot: rot, pan: pan, fit: 100,
             keys: [[0, scale]], ease: [[33.3333, 33.3333]]};
  if (follow) z.follow = {keys: [[0, follow]], ease: [[33.3333, 33.3333]]};
  // точка покоя вставок кам2 — из плана, как в жизни (ins_c2x/ins_c2y)
  return {w: 1080, h: 1920, ins_c2x: 540, ins_c2y: 330, zoom: z};
}
function applyM(m, p){return [m[0]*p[0] + m[2]*p[1] + m[4], m[1]*p[0] + m[3]*p[1] + m[5]];}
"""


@node
def test_matrix_center_anchor_shift_rotation():
    """1. Матрица кадра: центр исходника, неподвижная точка наезда, сдвиг, поворот."""
    code = _js("keysAt", "ipvZoomAt", "ipvCamShift", "ipvCamChild", "ipvCamMatrix") + _PLAN_JS + """
    const IPV = {fps: 60, plan: zoomPlan(100, 0, [0, 0], 0.5, 0.5)};
    // S=1, rot=0, T=0: центр исходника (p=0) встаёт в центр кадра
    const center = applyM(ipvCamMatrix(0), [0, 0]);

    // S=1.5, точка наезда 0.4/0.244: неподвижна именно она (её p = C от центра кадра)
    const cx = 0.4, cy = 0.244;
    const C = [cx*1080, cy*1920], Cc = [(cx-0.5)*1080, (cy-0.5)*1920];
    IPV.plan = zoomPlan(150, 0, [0, 0], cx, cy);
    const anchor = applyM(ipvCamMatrix(0), Cc);

    // T=(40,0) переносит всё на 40
    IPV.plan = zoomPlan(100, 0, [0, 0], cx, cy);
    const quiet = applyM(ipvCamMatrix(0), [100, 200]);
    IPV.plan = zoomPlan(100, 0, [40, 0], cx, cy);
    const moved = applyM(ipvCamMatrix(0), [100, 200]);

    // rot=90: центр исходника там же, где его даёт ipvCamChild(0,0,S) — поворот вокруг
    // центра исходника его не двигает, и без поворота он там же
    IPV.plan = zoomPlan(150, 0, [0, 0], cx, cy);
    const rot0 = applyM(ipvCamMatrix(0), [0, 0]);
    IPV.plan = zoomPlan(150, 90, [0, 0], cx, cy);
    const rot90 = applyM(ipvCamMatrix(0), [0, 0]);
    const child = ipvCamChild(0, 0, 1.5);
    console.log(JSON.stringify({center, anchor, C, quiet, moved, rot0, rot90, child}));
    """
    out = _run_node(code)

    assert out["center"] == [540, 960], "центр исходника не встал в центр кадра"
    assert abs(out["anchor"][0] - out["C"][0]) < 1e-6, "точка наезда уехала по X"
    assert abs(out["anchor"][1] - out["C"][1]) < 1e-6, "точка наезда уехала по Y"
    assert abs((out["moved"][0] - out["quiet"][0]) - 40) < 1e-9, "сдвиг pan не дошёл до матрицы"
    assert abs(out["moved"][1] - out["quiet"][1]) < 1e-9, "pan по X тронул Y"
    assert abs(out["rot90"][0] - out["rot0"][0]) < 1e-6, "поворот сдвинул центр исходника по X"
    assert abs(out["rot90"][1] - out["rot0"][1]) < 1e-6, "поворот сдвинул центр исходника по Y"
    # центр исходника = центр кадра + ipvCamChild(0,0,S): та же точка, что у детей нула
    assert abs(out["rot90"][0] - (540 + out["child"][0])) < 1e-6
    assert abs(out["rot90"][1] - (960 + out["child"][1])) < 1e-6


@node
def test_paint_draws_whole_source():
    """2. drawImage зовётся с исходным прямоугольником 0,0,vw,vh (весь кадр), не с вырезкой.

    Заодно: при пустом `plan.lumetri` фильтр превью снят (`'none'`) и документа не трогает."""
    # ipvCamPaint надевает на холст фильтр Lumetri, поэтому в сборку идут и он
    # сам, и всё, что он зовёт: без них node падал на ReferenceError, а не проверял рисование.
    code = _js("keysAt", "ipvZoomAt", "ipvCamShift", "ipvCamMatrix",
               "ipvLmSmooth", "ipvLumetriTone", "ipvLumetriTable",
               "ipvLumetriFilter", "ipvCamPaint") + _PLAN_JS + """
    const calls = [];
    const dom = [];                                    // следы обращений к документу
    const ctx = {imageSmoothingEnabled: true, imageSmoothingQuality: '',
      setTransform: function(){calls.push(['setTransform'].concat([].slice.call(arguments)));},
      clearRect: function(){calls.push(['clearRect'].concat([].slice.call(arguments)));},
      drawImage: function(){calls.push(['drawImage'].concat([].slice.call(arguments).slice(1)));}};
    const cv = {width: 0, height: 0, style: {}, _c: null, getContext: () => ctx};
    const st = {getBoundingClientRect: () => ({width: 1080, height: 1920})};
    function $(id){return id === 'ipvcam' ? cv : (id === 'ipvstage' ? st : null);}
    // Мини-DOM: фильтру он нужен ТОЛЬКО при включённом Lumetri. Здесь plan.lumetri пуст,
    // поэтому любое обращение к document — дефект: при null документа не требуется.
    const document = {body: null,
      createElementNS: function(){dom.push('createElementNS'); return null;}};
    const window = {devicePixelRatio: 1};
    const IPV = {curCi: 0, fps: 60, vids: [{readyState: 4, videoWidth: 1920, videoHeight: 1080}],
                 plan: zoomPlan(150, 3, [40, 0], 0.5, 0.5)};
    ipvCamPaint(1.5);
    const d = calls.filter(c => c[0] === 'drawImage')[0];
    const tf = calls.filter(c => c[0] === 'setTransform');
    const clr = calls.filter(c => c[0] === 'clearRect')[0];
    const lm = ipvLumetriFilter();                    // plan.lumetri пуст — обязан быть 'none'
    console.log(JSON.stringify({draw: d.slice(1), wipe: tf[0].slice(1),
                                last: tf[tf.length-1].slice(1), clr: clr.slice(1),
                                lm: lm, dom: dom.length}));
    """
    out = _run_node(code)

    # Кадр целиком: исходник 0,0,vw,vh, а не вырезка кадра экрана
    assert out["draw"][:4] == [0, 0, 1920, 1080], \
        "drawImage рисует не весь исходник — вернулась вырезка (полосы по краям кадра)"
    # Заполнение кадра: 1920×1080 в кадр 1080×1920 -> f = 1920/1080, прямоугольник от центра
    f = 1920 / 1080
    for got, want in zip(out["draw"][4:], [-1920 * f / 2, -1080 * f / 2, 1920 * f, 1080 * f]):
        assert abs(got - want) < 1e-6, f"прямоугольник вывода не заполняет кадр: {out['draw'][4:]}"
    # Чистка — при единичной матрице, иначе clearRect вычистит угол (мазня по краям)
    assert out["wipe"] == [1, 0, 0, 1, 0, 0], "холст чистится не при единичной матрице"
    assert out["clr"] == [0, 0, 1080, 1920], "clearRect не во весь холст"
    # Кадр рисуется ПО МАТРИЦЕ: S=1.5, rot=3°, pan=(40,0), точка наезда в центре кадра.
    # transform-origin не ставится — setTransform задаёт матрицу целиком (dpr=1, k=1).
    last = out["last"]
    assert abs(math.hypot(last[0], last[1]) - 1.5) < 1e-9, "масштаб кадра не доехал до матрицы"
    assert abs(math.degrees(math.atan2(last[1], last[0])) - 3) < 1e-6, "поворот не доехал"
    assert abs(last[4] - 580) < 1e-9 and abs(last[5] - 960) < 1e-9, \
        f"сдвиг pan не доехал до матрицы: {last[4:]}"
    # Фильтр Lumetri в этом плане пуст: он обязан сняться и НЕ лезть в документ
    assert out["lm"] == "none", f"пустой plan.lumetri не снял фильтр превью: {out['lm']}"
    assert out["dom"] == 0, "фильтр трогает документ при пустом plan.lumetri"


@node
def test_free_inserts_do_not_follow_cam1():
    """3. Свободные вставки (кам2, «кам1 на кам2») не едут с кадром; кам1 на кам1 — едет."""
    code = _js("keysAt", "ipvZoomAt", "ipvCamShift", "ipvCamChild", "ipvInsPlace") + _PLAN_JS + """
    const el = {style: {}, querySelector: () => null};
    const wr = {dataset: {ins: '0'}, clientWidth: 1080, firstChild: el};
    const card = {w: 400, h: 300, pw: 800, ph: 600};
    const IPV = {fps: 60, insShift: null, plan: null};
    const x = {card: card, style: 'cam2', x: 0, y: 0, scale: 44, sc: 100, anim: {}};
    function put(pl){IPV.plan = pl; ipvInsPlace(wr, x, 0); return el.style.transform;}
    const cam2quiet = put(zoomPlan(100, 0, [0, 0], 0.5, 0.5, 0));
    const cam2move  = put(zoomPlan(100, 0, [40, 0], 0.5, 0.5, 25));
    x.style = 'cam1';
    const cam1quiet = put(zoomPlan(100, 0, [0, 0], 0.5, 0.5, 0));
    const cam1move  = put(zoomPlan(100, 0, [40, 0], 0.5, 0.5, 25));
    console.log(JSON.stringify({cam2quiet, cam2move, cam1quiet, cam1move}));
    """
    out = _run_node(code)

    # Кам2 сидит на свободном нуле: ни pan (40 px), ни слежение (25 px) её не двигают
    assert out["cam2quiet"] == out["cam2move"] == "translate(0px,-630px)", \
        f"вставка кам2 поехала за кадром: {out['cam2quiet']} -> {out['cam2move']}"
    # Кам1 на кам1 — дитя нула Камеры 1: сдвиг со слежением доезжает (40 + 25)
    assert out["cam1quiet"] == "translate(0px,0px)", out["cam1quiet"]
    assert out["cam1move"] == "translate(65px,0px)", \
        f"вставка кам1 не поехала со сдвигом кадра: {out['cam1move']}"
