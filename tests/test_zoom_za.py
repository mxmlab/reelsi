# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания ZA: резкие скачки (cam1_zoom='jump').

- Наезд в начале (кадр 0 -> punch кадров);
- Наезды внутри длинных тейков;
- Массив CAM1_HOLDS в JSX и holds в scene_plan;
- _zoom_max на смешанных holds;
- keysAt в JS с массивом hold;
- верификация JSX через core.verify_jsx.
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

from core.xml2ae.layout import (  # noqa: E402
    ZOOM_BIG, ZOOM_PUNCH, TAKE_TAIL_S,
    _cam1_jump_keys, _zoom_max,
)
from test_cam1_zoom_start import _build_jsx, _cam1_scale_from_jsx  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

BASELINE_JUMP_KEYS = [
    [0, 126.9], [443, 117.3], [651, 132.6], [856, 112.1], [1609, 134.7],
    [1645, 114.7], [1779, 129], [2054, 122], [2341, 139.1], [2412, 125],
    [2751, 139.9], [2921, 113.6], [3161, 126.3], [3422, 138.9], [3691, 125.8],
    [3750, 119.9], [3989, 136.3], [4604, 114.1], [4860, 131.6], [5117, 117],
    [5326, 138.8], [5653, 124.5], [5877, 116], [6307, 131.6], [6503, 117]
]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _cam1_holds_from_jsx(jsx):
    m = re.search(r"var CAM1_HOLDS\s*=\s*(\[.*?\]);", jsx)
    assert m, "var CAM1_HOLDS не найден в JSX"
    return json.loads(m.group(1))


def test_jump_start_keys_and_cut_values(xml_subs, tmp_path):
    """1. jump + start: CAM1_SCALE[0] == [0, big, 1], второй ключ [pe, v1, 2];
    значения на катах (у ключей с hold=1) совпадают с прежними числами с main."""
    out_jsx = str(tmp_path / "jump_start.jsx")
    jsx = _build_jsx(xml_subs, out_jsx, style={"cam1_zoom": "jump", "cam1_zoom_start": True})
    keys = _cam1_scale_from_jsx(jsx)

    assert keys[0][0] == 0
    assert keys[0][1] == ZOOM_BIG
    assert keys[0][2] == 1

    pe = min(ZOOM_PUNCH, max(1, 443 - 2))
    assert keys[1][0] == pe
    assert keys[1][1] == BASELINE_JUMP_KEYS[0][1]
    assert keys[1][2] == 2

    # Проверяем значения на последующих катах (без учета наездов в тейках, т.к. take_zoom выключен)
    cut_keys = [[k[0], k[1]] for k in keys[1:]]
    assert cut_keys == [[pe, BASELINE_JUMP_KEYS[0][1]]] + BASELINE_JUMP_KEYS[1:]


def test_cam1_holds_in_jsx(xml_subs, tmp_path):
    """2. CAM1_HOLDS в JSX есть, длина = длине CAM1_SCALE, строки CAM1_HOLD= нет;
    в jump без наездов в тейках все 1, кроме ключа 0 (start=True)."""
    out_jsx = str(tmp_path / "jump_holds.jsx")
    jsx = _build_jsx(xml_subs, out_jsx, style={"cam1_zoom": "jump", "cam1_zoom_start": True})

    assert "var CAM1_HOLD=" not in jsx
    assert "var CAM1_HOLD =" not in jsx
    assert "var CAM1_HOLDS=" in jsx or "var CAM1_HOLDS =" in jsx

    keys = _cam1_scale_from_jsx(jsx)
    holds = _cam1_holds_from_jsx(jsx)
    assert len(holds) == len(keys)
    assert holds[0] == 0  # стартовый ключ: плавный наезд
    assert all(h == 1 for h in holds[1:])


def test_cam1_jump_keys_synthetic():
    """3. _cam1_jump_keys на синтетических cams:
    - take=None: только ключи старта и катов
    - take включён: в длинном тейке 4 доп. ключа (a,v,1,0),(b,vm,2,1),(c,vm,1,0),(d,v,2,1)
    - короткий тейк без доп. ключей
    - тейк, где отъезд не влезает: 2 доп. ключа."""
    fps = 60.0
    # clips: [in, out, track, filename, active]
    # cam 1: 0..1500 (25 с), затем cam 2: 1500..1620 (2 с), затем cam 1: 1620..1860 (4 с, короткий)
    cams = [
        {"clips": [[0, 1500, 0, "cam1.mov", True], [1620, 1860, 0, "cam1.mov", True]]},
        {"clips": [[1500, 1620, 0, "cam2.mov", True]]},
    ]

    # take = None
    keys_no_take = _cam1_jump_keys(cams, fps=fps, start=True, take=None)
    # Ключи: старт 0, pe, кат 1500 (переключение на cam2), кат 1620 (возврат на cam1)
    # Проверяем, что нет промежуточных ключей
    assert all(len(k) == 4 for k in keys_no_take)
    frames_no_take = [k[0] for k in keys_no_take]
    assert 0 in frames_no_take
    assert 1500 in frames_no_take
    assert 1620 in frames_no_take

    # take включен: min_s=8, lo=25, hi=40, hold_s=2.0
    take_cfg = {"min_s": 8.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0}
    keys_take = _cam1_jump_keys(cams, fps=fps, start=True, take=take_cfg)
    assert len(keys_take) == len(keys_no_take) + 4

    # Проверяем 4 доп. ключа внутри первого тейка (0..1500)
    extra_keys = [k for k in keys_take if 0 < k[0] < 1500 and k[0] != keys_no_take[1][0]]
    assert len(extra_keys) == 4
    k_a, k_b, k_c, k_d = extra_keys
    v = keys_no_take[1][1]  # значение первого тейка
    vm = k_b[1]

    # порядок (a,v,1,0), (b,vm,2,1), (c,vm,1,0), (d,v,2,1)
    assert k_a[1] == v and k_a[2] == 1 and k_a[3] == 0
    assert k_b[1] == vm and k_b[2] == 2 and k_b[3] == 1
    assert k_c[1] == vm and k_c[2] == 1 and k_c[3] == 0
    assert k_d[1] == v and k_d[2] == 2 and k_d[3] == 1

    ratio = vm / v
    assert 1.25 - 1e-2 <= ratio <= 1.40 + 1e-2
    assert k_d[0] <= 1500 - round(TAKE_TAIL_S * fps)

    # Тейк, где отъезд не влезает: seg_end такой, что b <= seg_end - 2, но d > seg_end - TAKE_TAIL_S*fps
    # a = 1.6*60 = 96, b = 96 + 1.6*60 = 192, c = 192 + 2*60 = 312, d = 312 + 1.5*60 = 402
    # seg_end - tail = seg_end - 120. d <= seg_end - 120 <=> seg_end >= 522.
    # Если seg_end = 450 (~7.5 с, но с min_s=6 пройдет), то b=192 <= 448, но d=402 > 450-120=330.
    cams_no_tail = [
        {"clips": [[0, 450, 0, "cam1.mov", True]]},
    ]
    keys_no_tail = _cam1_jump_keys(cams_no_tail, fps=fps, start=False, take={"min_s": 6.0, "lo": 25.0, "hi": 40.0, "hold_s": 2.0})
    # start=False -> ключ 0: (0, 100, 0, 1). Доп. ключей должно быть ровно 2: (a, v, 1, 0), (b, vm, 2, 1)
    extra_no_tail = [k for k in keys_no_tail if k[0] > 0]
    assert len(extra_no_tail) == 2
    assert extra_no_tail[0][3] == 0
    assert extra_no_tail[1][3] == 1


def test_zoom_max_mixed_holds():
    """4. _zoom_max на смешанных holds (руками собранные ключи):
    окно внутри HOLD-отрезка берёт v_i, окно внутри плавного — линейное;
    «всё HOLD»/«всё плавно» = прежние ответы."""
    fps = 60.0
    # ключи: (0, 100), (60, 150), (120, 200)
    keys = [(0, 100.0), (60, 150.0), (120, 200.0)]

    # Всё HOLD
    assert _zoom_max(keys, fps, 0.1, 0.9, holds=[True, True, True]) == 100.0
    assert _zoom_max(keys, fps, 0.9, 1.1, holds=[True, True, True]) == 150.0

    # Всё плавно: окно 0.2..0.8 сек (кадры 12..48) -> линейно 100..150 -> max при t=0.8: 100 + 50*0.8 = 140.0
    assert abs(_zoom_max(keys, fps, 0.2, 0.8, holds=[False, False, False]) - 140.0) < 1e-4

    # Смешанный: [0, 60) — плавно (holds[0]=False), [60, 120) — HOLD (holds[1]=True)
    # Окно внутри плавного [0.2, 0.8]: 140.0
    assert abs(_zoom_max(keys, fps, 0.2, 0.8, holds=[False, True, True]) - 140.0) < 1e-4
    # Окно внутри HOLD [1.2, 1.8]: должно взять v=150.0 (ключ 60..120), а не линейную интерполяцию
    assert _zoom_max(keys, fps, 1.2, 1.8, holds=[False, True, True]) == 150.0


@node
def test_node_keys_at_hold_array():
    """5. Превью: keysAt с массивом hold — прогнать через node:
    на HOLD-отрезке значение левого ключа, на плавном — строго между концами."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()

    # Извлекаем необходимые функции: keysAt, aeEase, bezierT, bezierY
    from test_cam1_zoom_none import _func
    fn_keys_at = _func(src, "keysAt")
    fn_ae_ease = _func(src, "aeEase")
    fn_bez_t = _func(src, "bezierT")
    fn_bez_y = _func(src, "bezierY")

    code = f"""
    {fn_ae_ease}
    {fn_bez_t}
    {fn_bez_y}
    {fn_keys_at}

    const keys = [[0, 100], [1.0, 150], [2.0, 200]];
    const ease = [[35, 35], [90, 35], [90, 90]];
    // отрезок 0..1 плавный (hold=0), отрезок 1..2 HOLD (hold=1)
    const holds = [0, 1, 1];

    const val_smooth = keysAt(keys, ease, 0.5, holds);
    const val_hold = keysAt(keys, ease, 1.5, holds);

    console.log(JSON.stringify({{ smooth: val_smooth, hold: val_hold }}));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    res = json.loads(p.stdout)
    # На HOLD-отрезке [1.0, 2.0) значение строго 150 (левый ключ)
    assert res["hold"] == 150
    # На плавном отрезке [0, 1.0) значение строго между 100 и 150
    assert 100 < res["smooth"] < 150


def test_verify_jsx_on_jump(xml_subs, tmp_path, monkeypatch):
    """core.verify_jsx на собранном JSX проходит без ошибок."""
    out_jsx = str(tmp_path / "jump_verify.jsx")
    _build_jsx(xml_subs, out_jsx, style={"cam1_zoom": "jump", "cam1_take_zoom": True})

    from core import verify_jsx
    orig_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: True if "CLIP-006" in str(p) else orig_exists(p))
    report = verify_jsx.verify(out_jsx, xml_path=xml_subs)
    assert not report.errors, f"Ошибки verify_jsx: {report.errors}"
