# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Слежение за головой и кривая зума AE (жалоба 01.10.2026: на наезде камеры 2
слева входил край слоя).

`_cam1_follow_keys` считал масштаб нула ЛИНЕЙНО между ключами зума, а AE ведёт его
по кривой с влиянием ease (скорость 0). На подъезде настоящий масштаб меньше
линейного — зажим сдвига разрешал больше, чем слой позволяет, и между отсчётами
слежения край слоя входил в кадр.

Тесты:
1. `_zoom_scale_at` (Python) и `keysAt` предпросмотра (JS, `static/app/85-inserts-view.js`)
   на одних ключах с влияниями ease дают одно и то же в 50 точках (расхождение ≤ 1e-3).
2. `_zoom_min_scale` берёт наименьшее значение кривой на отрезке, включая ключ внутри.
3. Покрытие кадра: покадрово по ключам зума и ключам слежения (Easy Ease) слой
   закрывает кадр на всей длине клипа.
4. Тот же клип БЕЗ правки (зажим по масштабу текущего кадра) — кадры с открытым краем
   есть: тест 3 не пустой.
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

from core.xml2ae import layout  # noqa: E402
from core.xml2ae.layout import (HL_EASE_IN, HL_EASE_OUT,  # noqa: E402
                                _cam1_follow_keys, _zoom_key_eases,
                                _zoom_min_scale, _zoom_scale_at)

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")
FPS = 60.0
W, H = 1080, 1920
SRC_W, SRC_H = 1080, 1920
FIT = SRC_W * max(W / SRC_W, H / SRC_H)          # ширина слоя при масштабе 1.0


# --------------------------------------------------------------------------- #
# 1. Python-кривая == keysAt предпросмотра
# --------------------------------------------------------------------------- #
def _js_func(src, name):
    """Тело функции name из боевого JS (приём tests/test_cam1_zoom_none.py)."""
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


def _keys_js():
    """keysAt и его помощники из боевого static/app/85-inserts-view.js."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    return "\n".join(_js_func(src, n) for n in ("keysAt", "aeEase", "bezierY", "bezierT"))


@node
def test_zoom_scale_at_matches_preview_keys_at():
    """Python-кривая масштаба совпадает с keysAt предпросмотра в 50 точках (≤ 1e-3).

    Ключи как у наезда с отъездом: подъезд 0.6 с (in 90 у приходящего ключа),
    удержание и откат в 100 — на них кривая максимально далека от прямой.
    """
    keys = [(0, 100.0), (12, 100.0), (48, 150.0), (120, 150.0), (192, 100.0), (300, 100.0)]
    holds = [False, False, False, False, False, False]
    ease = _zoom_key_eases(keys)
    # Отъезд (150 -> 100): out 35 у уходящего и in 90 у приходящего — эталонная кривая.
    assert ease[3] == [float(HL_EASE_IN), float(HL_EASE_OUT)], "ключ отката потерял влияние out"
    assert ease[4] == [float(HL_EASE_IN), float(HL_EASE_OUT)], "ключ отката потерял влияние in"

    js_pts = [[k[0] / FPS, k[1]] for k in keys]
    code = (_keys_js() + f"""
    const keys = {json.dumps(js_pts)};
    const ease = {json.dumps(ease)};
    const holds = {json.dumps(holds)};
    const out = [];
    for (let i = 0; i < 50; i++) {{
        const f = {keys[0][0]} + (i * ({keys[-1][0]} - {keys[0][0]}) / 49.0);
        out.push([f, keysAt(keys, ease, f / {FPS}, holds)]);
    }}
    console.log(JSON.stringify(out));
    """)
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr
    ref = json.loads(p.stdout)
    assert len(ref) == 50

    bad = []
    for f, pct in ref:
        py = _zoom_scale_at(keys, f, holds=holds) * 100.0
        if abs(py - pct) > 1e-3:
            bad.append((f, py, pct))
    assert not bad, f"Python-кривая разошлась с keysAt в {len(bad)} точках: {bad[:5]}"

    # Тест не пустой: кривая уходит от прямой заметно (иначе сверка ничего не ловит).
    lin_max = max(abs(_zoom_scale_at(keys, f, holds=holds) * 100.0
                      - (100.0 + (150.0 - 100.0) * (f - 12.0) / 36.0))
                  for f in range(12, 49))
    assert lin_max > 4.0, f"кривая почти совпала с прямой: {lin_max}"

    # HOLD держит значение левого ключа, как в предпросмотре.
    hold_keys = [(0, 150.0), (60, 100.0)]
    assert _zoom_scale_at(hold_keys, 30, holds=[True, False]) == pytest.approx(1.5)
    assert _zoom_scale_at(hold_keys, 30, holds=[False, False]) < 1.5


def test_zoom_min_scale_takes_key_inside_the_span():
    """Наименьший масштаб на отрезке — с учётом ключа внутри: между ключами кривая
    монотонна, но на концах отрезка минимум не обязан лежать."""
    keys = [(0, 100.0), (30, 150.0), (90, 100.0)]
    holds = [False, False, False]
    worst = _zoom_min_scale(keys, 0, 90, holds=holds)
    assert worst == pytest.approx(1.0, abs=1e-9), "минимум на отрезке с ключами не найден"
    assert _zoom_min_scale(keys, 0, 15, holds=holds) < _zoom_scale_at(keys, 15, holds=holds)
    # На отрезке без ключей внутри минимум — на одном из концов.
    assert _zoom_min_scale(keys, 30, 60, holds=holds) == pytest.approx(
        _zoom_scale_at(keys, 60, holds=holds), abs=1e-9)


# --------------------------------------------------------------------------- #
# 2-4. Покрытие кадра: края слоя на каждом кадре показа камеры 2
# --------------------------------------------------------------------------- #
# Откат 1.6 с: 150 -> 110 (кадры 36..132; out 35 у уходящего и in 90 у приходящего —
# на откате кривая НИЖЕ прямой), дальше 110. Голова у правого края: желаемый сдвиг
# постоянен и на 150 % допустим, но ужимается вместе с масштабом. Масштаб не
# опускается ниже 110 % — слой шире кадра, край обязан быть закрыт.
ZOOM_KEYS = [(0, 150.0), (36, 150.0), (132, 110.0), (599, 110.0)]
ZOOM_HOLDS = [True, False, True, False]
FIT_K = 140.0                       # заполнение кадра: слой заметно шире кадра
LAYER_W = FIT * FIT_K / 100.0
EDGE_TOL = 2.0                      # px: ключи округляются до 0.01 — щель в доли px не дефект


def _follow_keys():
    # Раскладка как у боевой сборки: камера 1 (индекс 0) закрыта, показывает камера 2.
    cams = [{"clips": [[0, 0, 0, 0, False, 100.0]]},
            {"clips": [[0, 600, 0, 600, True, 100.0]]}]
    pts = [[t / 10.0, 0.75 + 0.15 * math.sin(t / 3.0)] for t in range(0, 61)]  # голова уходит к краю
    return _cam1_follow_keys(
        cams=cams, pts=pts, w_src=SRC_W, h_src=SRC_H,
        zoom_keys=ZOOM_KEYS, holds=ZOOM_HOLDS, fps=FPS, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=FIT_K, target=0.5, smooth_s=0.05,
        min_scale=0.0, cam=2,
    )


def _follow_at(keys, f):
    """Значение ключей слежения линейно между отсчётами (как ставит plan_camera)."""
    return _linear_scale(keys, f, holds=[False] * len(keys), fit=100.0) * 100.0


def _linear_scale(keys, f, *, ease=None, holds=None, fit=100.0):
    """Прежний `_zoom_at`: линейно между ключами, HOLD держит левый ключ (мутация).

    Сигнатура как у `_zoom_scale_at`/`_zoom_min_scale`: их зовут и с `ease=`, и с `fit=`.
    """
    raw = [(float(k[0]), float(k[1])) for k in (keys or []) if len(k) >= 2]
    if not raw:
        return 1.0
    k = fit / 100.0
    if f <= raw[0][0]:
        return raw[0][1] / 100.0 * k
    if f >= raw[-1][0]:
        return raw[-1][1] / 100.0 * k
    for i in range(len(raw) - 1):
        f0, v0 = raw[i]
        f1, v1 = raw[i + 1]
        if f0 <= f < f1:
            if holds and i < len(holds) and holds[i]:
                return v0 / 100.0 * k
            return (v0 + (v1 - v0) * (f - f0) / (f1 - f0)) / 100.0 * k
    return raw[-1][1] / 100.0 * k


def _worst_gap(keys, scale=None):
    """Покадровый проход клипа: (кадры с открытым краем, наибольшая щель в px).

    `scale` — чем считать масштаб на кадре (по умолчанию кривая AE).
    """
    scale = scale or (lambda f: _zoom_scale_at(ZOOM_KEYS, f, holds=ZOOM_HOLDS) * 100.0)
    bad, worst = [], 0.0
    for f in range(0, 600):
        s = scale(f) / 100.0
        off = _follow_at(keys, f)
        left = 0.5 * W - s * LAYER_W / 2.0 + off
        right = 0.5 * W + s * LAYER_W / 2.0 + off
        gap = max(0.0, left, W - right)
        if gap > EDGE_TOL:
            bad.append(f)
            worst = max(worst, gap)
    return bad, worst


def test_head_follow_keeps_the_frame_covered_on_every_frame():
    """Ноль кадров с открытым краем: ключи слежения считаются по худшему масштабу
    до следующего отсчёта, без прореживания _rdp, линейно."""
    keys = _follow_keys()
    assert keys, "слежение не дало ключей — тест бесполезен"
    bad, worst = _worst_gap(keys)
    assert not bad, f"кадр открыт на {len(bad)} кадрах, щель до {worst:.1f} px: {bad[:10]}"


def test_rdp_mutation_leaves_the_frame_open():
    """Мутация: возврат _rdp(clip_samples, eps=4.0) открывает край кадра.

    Линия между прореженными ключами отходит от зажатых отсчётов и выходит за
    предел (в реальном AE — жалоба на край 0.5–3.6 px на 5 из 6 клипов спикера).
    """
    keys = _follow_keys()
    keys_rdp = layout._rdp(keys, eps=4.0)
    assert len(keys_rdp) < len(keys) // 5, "прореживание _rdp не сработало на эталоне"
    bad, worst = _worst_gap(keys_rdp)
    assert len(bad) > 10, f"с _rdp кадров с открытым краем всего {len(bad)}"
    assert worst > EDGE_TOL, f"щель с _rdp {worst:.2f} px не превысила допуск {EDGE_TOL}"


@node
def test_preview_follow_interpolates_linearly():
    """Превью: значение follow в момент между ключами — линейная интерполяция плана."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    js_funcs = "\n".join(_js_func(src, n) for n in ("keysAt", "aeEase", "bezierY", "bezierT", "ipvCamShift"))
    code = (js_funcs + """
    const IPV = {
      fps: 60,
      plan: {
        zoom: {
          pan: [10, 20],
          follow: {
            keys: [[0, 0], [60, 60]],
            linear: true
          }
        }
      }
    };
    const shift0 = ipvCamShift(0, 'cam1');
    const shift25 = ipvCamShift(0.25, 'cam1');
    const shift50 = ipvCamShift(0.5, 'cam1');
    const shift100 = ipvCamShift(1.0, 'cam1');
    console.log(JSON.stringify({shift0, shift25, shift50, shift100}));
    """)
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["shift0"] == [10, 20]
    assert out["shift25"] == [25, 20]    # 10 + 15 (линейно; с ease было бы ~8.5)
    assert out["shift50"] == [40, 20]    # 10 + 30 (линейно)
    assert out["shift100"] == [70, 20]   # 10 + 60 (линейно)


def test_linear_scale_clamp_leaves_the_frame_open(monkeypatch):
    """Прежний алгоритм (линейный масштаб + зажим по текущему кадру) открывает край.

    Мутация: `_zoom_scale_at` подменяется линейным `_zoom_at`, `_zoom_min_scale` —
    масштабом текущего кадра. Ключи строятся этой мутацией, а края считаются по
    настоящей кривой (её и ведёт AE) — как в рендере.
    """
    saved_scale, saved_min = layout._zoom_scale_at, layout._zoom_min_scale
    layout._zoom_scale_at = _linear_scale
    layout._zoom_min_scale = lambda keys, f0, f1, ease=None, holds=None, fit=100.0: \
        _linear_scale(keys, f0, ease=ease, holds=holds, fit=fit)
    try:
        keys = _follow_keys()
    finally:
        layout._zoom_scale_at, layout._zoom_min_scale = saved_scale, saved_min
    bad, worst = _worst_gap(keys)
    assert len(bad) > 5, f"прежний алгоритм дал всего {len(bad)} кадров с краем"
    assert worst > 10.0, f"щель прежнего алгоритма {worst:.3f} px — мельче кадра"
