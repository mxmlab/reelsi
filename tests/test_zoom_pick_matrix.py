# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Прицел точки наезда: клик кладёт точку ИСХОДНИКА кадра, а не долю экрана.

Жалоба владельца: «наезжает не в ту точку, что я указываю» — на камере 2 в режиме
«скачки». Причина: и клик (zoomPickClick), и маркер (zoomPickMark) считались долей
ЭКРАНА сцены, а текущий зум/сдвиг/поворот кадра не учитывались. На камере 2 в
«скачках» кадр увеличен почти всегда (104–137 %, в тейках больше 200 %), и точка
уезжала; на камере 1 в «наезде с откатом» кадр почти всё время 100 %, потому там
ошибки и не видели.

Стережём (всё — контракты фронта, боевые функции из static/app, стенд node):
1. числом: кадр камеры 2 увеличен в 2.2 раза вокруг (0.504, 0.655), клик в экранную
   (0.327, 0.63) записывает cam2_zoom_cx ≈ 0.4235, а не 0.327; то же для камеры 1
   с зумом и сдвигом;
2. масштаб 1 и нулевые pan/rot — клик записывается как раньше, долей экрана;
3. обратный и прямой пересчёт — из ОДНОЙ матрицы кадра: точка, восстановленная из
   экрана, возвращается на то же место экрана (round-trip), для обеих камер, с pan и rot;
4. ограничение 0.02..0.98 применяется к точке ИСХОДНИКА, а не к экранной;
5. маркер рисуется по ПРЯМОЙ матрице: он совпадает с проекцией точки исходника и
   уезжает вместе с кадром (при зуме он НЕ там, где его доля на экране).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_preview_cam import _PLAN_JS, _func, _run_node  # noqa: E402

VIEW = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
PICK = os.path.join(ROOT, "static", "app", "95-styles.js")

PLAN = {"w": 1080, "h": 1920, "intro_scale": 100,
        "zoom": {"cx": 0.5, "cy": 0.5, "pan": [0, 0], "rot": 0, "fit": 100,
                 "keys": [[0, 100]], "holds": [0], "ease": [[16.6667, 16.6667]],
                 "cam2": {"cx": 0.504, "cy": 0.655, "fit": 100, "pan": [0, 0], "rot": 0,
                          "keys": [[0, 220]], "holds": [0], "ease": [[16.6667, 16.6667]]}}}
# Камера 1: свой зум и сдвиг; камера 2 неактивна (зума кам2 в плане нет).
PLAN_CAM1 = {"w": 1080, "h": 1920, "intro_scale": 100,
             "zoom": {"cx": 0.5, "cy": 0.5, "pan": [40, -30], "rot": 0, "fit": 100,
                      "keys": [[0, 160]], "holds": [0], "ease": [[16.6667, 16.6667]]}}
# Кадр как есть: масштаб 1, точка наезда в центре, ни сдвига, ни поворота.
PLAN_FLAT = {"w": 1080, "h": 1920, "intro_scale": 100,
             "zoom": {"cx": 0.5, "cy": 0.5, "pan": [0, 0], "rot": 0, "fit": 100,
                      "keys": [[0, 100]], "holds": [0], "ease": [[16.6667, 16.6667]],
                      "cam2": {"cx": 0.5, "cy": 0.5, "fit": 100, "pan": [0, 0], "rot": 0,
                               "keys": [[0, 100]], "holds": [0], "ease": [[16.6667, 16.6667]]}}}
# Обе камеры сразу: зум, сдвиг и поворот у каждой — для прямой и обратной сверки.
PLAN_BOTH = {"w": 1080, "h": 1920, "intro_scale": 100,
             "zoom": {"cx": 0.4, "cy": 0.35, "pan": [55, -40], "rot": 7, "fit": 100,
                      "keys": [[0, 150]], "holds": [0], "ease": [[16.6667, 16.6667]],
                      "cam2": {"cx": 0.504, "cy": 0.655, "fit": 100, "pan": [-25, 60],
                               "rot": -4, "keys": [[0, 220]], "holds": [0],
                               "ease": [[16.6667, 16.6667]]}}}


def _funcs():
    """Обе семьи боевых функций: кадр (85-inserts-view.js) и прицел (95-styles.js)."""
    with open(PICK, "r", encoding="utf-8") as f:
        pick = f.read()
    with open(VIEW, "r", encoding="utf-8") as f:
        view = f.read()
    return "\n".join(_func(pick, n) for n in
                     ("zoomPickCam", "zoomPickSource", "zoomPickPoint", "zoomPickKeys",
                      "zoomPickTarget", "zoomPickMark", "isCam2Active", "zoomPickClick")) + "\n" + \
        "\n".join(_func(view, n) for n in
                  ("keysAt", "ipvZoomAt", "ipvCamShift", "ipvCamMatrix", "ipvCamChild"))


def _world(plan, body, curstyle="{}"):
    """Сцена 1080x1920 в пикселях композиции (k=1), кадр «стоит» на tm=0."""
    return f"""
    {_PLAN_JS}
    {_funcs()}
    const STAGE = {{clientWidth: 1080, clientHeight: 1920,
                    appendChild: function(el){{ globalThis.MARK = el; return el; }}}};
    globalThis.$ = function(id){{ return id === 'ipvstage' ? STAGE : null; }};
    globalThis.ipvNow = function(){{ return 0; }};
    globalThis.ZOOM_PICK = 'cam2';
    globalThis.ZOOM_HOVER = false;
    globalThis.CURSTYLE = {curstyle};
    globalThis.IPV = {{fps: 60, plan: {json.dumps(plan, ensure_ascii=False)}}};
    {body}
    """


def _apply(m, px, py):
    return [m[0] * px + m[2] * py + m[4], m[1] * px + m[3] * py + m[5]]


def test_click_lands_on_the_source_point_of_camera_2():
    """1а. Зум 2.2 вокруг (0.504, 0.655): клик в (0.327, 0.63) даёт 0.4235, а не 0.327.

    Значение берётся боевым zoomPickPoint — той же дверью, что зовёт клик
    (zoomPickClick), поэтому подмена её на долю экрана тест роняет.
    """
    body = """
    const p = zoomPickPoint(0.327, 0.63, 0, 'cam2');
    console.log(JSON.stringify({p: p, m: ipvCamMatrix(0, 'cam2')}));
    """
    out = _run_node(_world(PLAN, body))
    assert out["m"][0] == pytest.approx(2.2), "предпосылка теста: кадр камеры 2 увеличен"
    assert out["p"][0] == pytest.approx(0.4235, abs=1e-4), \
        "клик на увеличенном кадре камеры 2 записан долей экрана, а не точкой исходника"
    # по Y промах меньше (кадр выше), но он тот же: 0.63 против 0.6436
    assert out["p"][1] == pytest.approx(0.6436, abs=1e-4)
    assert out["p"][1] != pytest.approx(0.63, abs=1e-3)


def test_click_lands_on_the_source_point_of_camera_1_with_zoom_and_pan():
    """1б. Камера 1: зум 1.6 и pan (40, −30) — клик тоже пересчитывается в исходник.

    Матрица кадра: точка наезда в (540+40, 960−30) = (580, 930), масштаб 1.6 (доля
    экрана (0.4, 0.6) — px композиции (432, 1152)). Источник = C + (p − C)/1.6 =
    (487.5, 1068.75) -> доли кадра (0.41435, 0.57227). Раньше записалась бы доля экрана.
    """
    body = """
    const s = zoomPickPoint(0.4, 0.6, 0, 'cam1');
    const free = zoomPickPoint(0.4, 0.6, 0, 'cam2');
    console.log(JSON.stringify({s: s, free: free}));
    """
    out = _run_node(_world(PLAN_CAM1, body))
    assert out["s"][0] == pytest.approx(0.4143519, abs=1e-6), \
        "сдвиг/зум камеры 1 не учтены в точке исходника по X"
    assert out["s"][1] == pytest.approx(0.5722656, abs=1e-6), \
        "сдвиг/зум камеры 1 не учтены в точке исходника по Y"
    assert out["free"] == pytest.approx([0.4, 0.6], abs=1e-9), \
        "кадр камеры 1 протёк в пересчёт точки камеры 2"


def test_flat_frame_writes_the_screen_fraction_as_before():
    """2. Масштаб 1, pan/rot нулевые (обе камеры) — точка исходника равна доле экрана."""
    body = """
    const a = zoomPickPoint(0.327, 0.63, 0, 'cam1');
    const b = zoomPickPoint(0.327, 0.63, 0, 'cam2');
    console.log(JSON.stringify({a: a, b: b}));
    """
    out = _run_node(_world(PLAN_FLAT, body))
    assert out["a"] == pytest.approx([0.327, 0.63], abs=1e-9), \
        "при масштабе 1 и нулевом сдвиге клик обязан записываться как раньше"
    assert out["b"] == pytest.approx([0.327, 0.63], abs=1e-9)


def test_source_to_screen_round_trip_for_both_cameras():
    """3. Прямая и обратная — из ОДНОЙ матрицы: точка возвращается туда же на экране."""
    body = """
    const res = {};
    for (const tg of ['cam1', 'cam2']) {
      const ux = 0.33, uy = 0.71;
      const src = zoomPickPoint(ux, uy, 0, tg);
      const m = ipvCamMatrix(0, zoomPickCam(tg));
      // точка наезда хранится в кадре без зума: до матрицы снимаем горизонт (R⁻¹)
      const s = Math.sqrt(m[0]*m[3] - m[1]*m[2]), co = m[0]/s, si = m[1]/s;
      const qx = (src[0]-0.5)*1080, qy = (src[1]-0.5)*1920;
      const px = co*qx + si*qy, py = -si*qx + co*qy;
      const back = [(m[0]*px + m[2]*py + m[4]) / 1080,
                    (m[1]*px + m[3]*py + m[5]) / 1920];
      res[tg] = {src: src, back: back, ux: ux, uy: uy};
    }
    console.log(JSON.stringify(res));
    """
    out = _run_node(_world(PLAN_BOTH, body))
    for tg in ("cam1", "cam2"):
        got = out[tg]
        assert got["back"][0] == pytest.approx(got["ux"], abs=1e-9), \
            f"{tg}: прямая матрица не вернула точку на то же место экрана"
        assert got["back"][1] == pytest.approx(got["uy"], abs=1e-9)


def test_clamp_applies_to_the_source_point():
    """4. 0.02..0.98 — на точке ИСХОДНИКА (боевым zoomPickPoint), а не на экранной.

    Кадр, УМЕНЬШЕННЫЙ до 0.5 вокруг (0.504, 0.655): он занимает лишь четверть экрана,
    и по краям исходник кончился. Локальный (0, 0) без ограничения дал бы −0.008 —
    в .jsx уходит 0.02.
    """
    plan = {"w": 1080, "h": 1920, "intro_scale": 100,
            "zoom": {"cx": 0.5, "cy": 0.5, "pan": [0, 0], "rot": 0, "fit": 100,
                     "keys": [[0, 100]], "holds": [0], "ease": [[16.6667, 16.6667]],
                     "cam2": {"cx": 0.504, "cy": 0.655, "fit": 100, "pan": [0, 0], "rot": 0,
                              "keys": [[0, 50]], "holds": [0], "ease": [[16.6667, 16.6667]]}}}
    body = """
    const raw = zoomPickSource(0, 0, 0, 'cam2');
    const pt = zoomPickPoint(0, 0, 0, 'cam2');
    const mid = zoomPickPoint(0.5, 0.5, 0, 'cam2');
    console.log(JSON.stringify({raw: raw, pt: pt, mid: mid, m: ipvCamMatrix(0, 'cam2')}));
    """
    out = _run_node(_world(plan, body))
    assert out["m"][0] == pytest.approx(0.5), "предпосылка теста: кадр уменьшен"
    assert out["raw"][0] < 0.02 and out["raw"][1] < 0.02, \
        "предпосылка: у края экрана точка исходника выходит за кадр"
    assert out["pt"][0] == pytest.approx(0.02), \
        "ограничение 0.02..0.98 применено не к точке исходника"
    assert out["pt"][1] == pytest.approx(0.02)
    # экранный центр — его и вернул обратный пересчёт: точка в кадре, ограничением не задета
    assert out["mid"] == pytest.approx([0.496, 0.345], abs=1e-6)


def test_marker_is_the_projection_of_the_source_point():
    """5. Маркер — по прямой матрице: совпадает с проекцией и не равен доле на экране."""
    body = """
    globalThis.document = {createElement: function(){ return {style: {}, id: ''}; }};
    globalThis.ipvRenderMode = function(){ return false; };
    const m = ipvCamMatrix(0, 'cam2');
    function proj(cx, cy){
      return [(m[0]*(cx-0.5)*1080 + m[2]*(cy-0.5)*1920 + m[4]) / 1080 * 100,
              (m[1]*(cx-0.5)*1080 + m[3]*(cy-0.5)*1920 + m[5]) / 1920 * 100];
    }
    const res = {};
    for (const pt of [[0.4235, 0.4205], [0.5, 0.5], [0.504, 0.655]]) {
      CURSTYLE.cam2_zoom_cx = pt[0]; CURSTYLE.cam2_zoom_cy = pt[1];
      zoomPickMark();
      res[pt[0] + '/' + pt[1]] = {got: [MARK.style.left, MARK.style.top], exp: proj(pt[0], pt[1])};
    }
    console.log(JSON.stringify(res));
    """
    out = _run_node(_world(PLAN, body, curstyle="{cam2_zoom_cx: 0.5, cam2_zoom_cy: 0.5}"))
    for key, got in out.items():
        gx = float(got["got"][0].rstrip("%"))
        gy = float(got["got"][1].rstrip("%"))
        assert gx == pytest.approx(got["exp"][0], abs=1e-6), f"{key}: маркер не по матрице кадра"
        assert gy == pytest.approx(got["exp"][1], abs=1e-6), f"{key}: маркер не по матрице кадра"
    # и предпосылка: на увеличенном кадре точка исходника стоит НЕ там, где её доля экрана
    one = out["0.4235/0.4205"]
    assert abs(one["exp"][0] - 42.35) > 0.1, "предпосылка: маркер совпал с долей экрана"


def test_picked_point_stays_put_when_zoom_changes_with_horizon():
    """Точка, поставленная прицелом, неподвижна при наезде и при повёрнутом кадре.

    Инвариант прицела: щёлкнул в место экрана — при любом другом масштабе этот пиксель
    кадра остаётся на месте. С горизонтом точка исходника и точка наезда расходятся
    (в AE якорь нула не поворачивается вместе со слоем), и запись точки исходника
    уводила неподвижную точку в сторону.
    """
    body = """
    const z = IPV.plan.zoom.cam2;
    const ux = 0.31, uy = 0.62;
    const pt = zoomPickSource(ux, uy, 0, 'cam2');
    // пиксель исходника под курсором при текущем масштабе
    const m0 = ipvCamMatrix(0, 'cam2');
    const det = m0[0]*m0[3] - m0[1]*m0[2];
    const dx = ux*1080 - m0[4], dy = uy*1920 - m0[5];
    const sx = (m0[3]*dx - m0[2]*dy)/det, sy = (-m0[1]*dx + m0[0]*dy)/det;
    // ставим точку и смотрим, где этот пиксель при двух разных масштабах
    z.cx = pt[0]; z.cy = pt[1];
    const at = [];
    for (const k of [100, 260]) {
      z.keys = [[0, k]];
      const m = ipvCamMatrix(0, 'cam2');
      at.push([m[0]*sx + m[2]*sy + m[4], m[1]*sx + m[3]*sy + m[5]]);
    }
    console.log(JSON.stringify({at: at}));
    """
    out = _run_node(_world(PLAN_BOTH, body))
    a, b = out["at"]
    assert a[0] == pytest.approx(b[0], abs=1e-6), "пиксель под прицелом уехал по X при наезде"
    assert a[1] == pytest.approx(b[1], abs=1e-6), "пиксель под прицелом уехал по Y при наезде"
