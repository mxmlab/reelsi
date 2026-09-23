# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: Камера 1 — слежение за головой по X.

Проверяет:
1. Голова уходит вправо -> off уменьшается (знак); при неподвижной голове — ключей только на краях клипов.
2. Край не открывается: для набора s (1.0, 1.3, 2.8) и головы у края — кадр всегда накрывает [0, W];
   при s*fit_w < W -> off == 0.
3. Кат: ключи (cut-1, ·) и (cut, ·) присутствуют.
4. to_ae_full с включённой галкой и подменённым headtrack.track: трекер вызван один раз,
   сайдкар записан; повторная сборка — не вызван (кэш); сменился mtime исходника — вызван снова.
   Ошибка трекера -> сборка проходит, в emit сообщение.
5. Галка выключена -> .jsx побайтово как на main.
6. head_at: линейная интерполяция, провалы (None) перешагиваются, за краями — ближайшее значение.
7. Превью через node: ipvCamChild включает off(t).
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

from core import headtrack, xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _func(src, name):
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


def test_follow_head_direction_and_stationary():
    """1. Голова уходит вправо -> off уменьшается (знак); при неподвижной голове — ключей только на краях клипов."""
    # Один клип Камеры 1 на 600 кадров (10 секунд при 60 fps)
    cams = [{"clips": [[0, 600, 0, 600, True, 100]]}]
    fps = 60.0
    W, H = 1080, 1920
    w_src, h_src = 1080, 1920
    zoom_keys = [(0, 140.0, 0)]
    holds = [False]

    # Сценарий A: голова уходит вправо (hx от 0.5 к 0.8)
    # Кадр должен сдвигаться влево, чтобы компенсировать -> off уменьшается (становится отрицательным)
    pts_moving = [[0.0, 0.5], [5.0, 0.7], [10.0, 0.8]]
    keys_moving = layout._cam1_follow_keys(
        cams=cams, pts=pts_moving, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
    )
    assert len(keys_moving) >= 2
    # off в конце меньше (левее), чем в начале
    assert keys_moving[-1][1] < keys_moving[0][1]

    # Сценарий B: голова неподвижна (hx постоянна)
    pts_static = [[0.0, 0.6], [10.0, 0.6]]
    keys_static = layout._cam1_follow_keys(
        cams=cams, pts=pts_static, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
    )
    # При неподвижной голове оператор держит константу, RDP оставляет только первый и последний кадр
    assert len(keys_static) == 2
    assert keys_static[0][0] == 0
    assert keys_static[1][0] == 599


def test_follow_boundary_clamping_and_narrow_frame():
    """2. Край не открывается: для набора s (1.0, 1.3, 2.8) и головы у края — кадр всегда накрывает [0, W];
    при s*fit_w < W -> off == 0."""
    cams = [{"clips": [[0, 300, 0, 300, True, 100]]}]
    fps = 60.0
    W, H = 1080, 1920
    w_src, h_src = 1080, 1920
    cam1_fit = 100.0
    fit_w = w_src * max(W / w_src, H / h_src) * (cam1_fit / 100.0)

    # Проверяем масштаб 1.0, 1.3, 2.8 при голове у левого (0.0) и правого (1.0) края
    for s_val in (1.0, 1.3, 2.8):
        for hx_val in (0.0, 1.0):
            for cx_val in (0.3, 0.5, 0.7):
                for pan_x_val in (-40.0, 0.0, 40.0):
                    zoom_keys = [(0, s_val * 100.0, 0)]
                    holds = [False]
                    pts = [[0.0, hx_val], [5.0, hx_val]]
                    keys = layout._cam1_follow_keys(
                        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
                        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
                        cx=cx_val, pan_x=pan_x_val, cam1_fit=cam1_fit,
                        target=0.5, smooth_s=0.6,
                    )
                    Cx = cx_val * W
                    s = s_val
                    for _f, off in keys:
                        left_edge = Cx + s * (W / 2.0 - fit_w / 2.0 - Cx) + pan_x_val + off
                        right_edge = Cx + s * (W / 2.0 + fit_w / 2.0 - Cx) + pan_x_val + off
                        assert left_edge <= 1e-4, f"Левый край открылся: {left_edge} > 0"
                        assert right_edge >= W - 1e-4, f"Правый край открылся: {right_edge} < {W}"

    # Узкий кадр: s*fit_w < W -> off обязано быть 0
    narrow_fit = 80.0  # fit_w = 864 < 1080
    zoom_keys_narrow = [(0, 100.0, 0)]
    pts_narrow = [[0.0, 0.9], [5.0, 0.9]]
    keys_narrow = layout._cam1_follow_keys(
        cams=cams, pts=pts_narrow, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys_narrow, holds=[False], fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=narrow_fit, target=0.5, smooth_s=0.6,
    )
    for _f, off in keys_narrow:
        assert off == 0.0, f"При s*fit_w < W ожидался off=0, получено {off}"


def test_follow_cut_keys():
    """3. Кат: ключи (cut-1, ·) и (cut, ·) есть."""
    # Два клипа: 0..300 и 300..600
    cams = [{"clips": [
        [0, 300, 0, 300, True, 100],
        [300, 600, 1000, 1300, True, 100],
    ]}]
    fps = 60.0
    W, H = 1080, 1920
    w_src, h_src = 1080, 1920
    zoom_keys = [(0, 140.0, 0)]
    holds = [False]
    # На кат меняется голова
    pts = [[0.0, 0.3], [5.0, 0.3], [10.0, 0.7]]
    keys = layout._cam1_follow_keys(
        cams=cams, pts=pts, w_src=w_src, h_src=h_src,
        zoom_keys=zoom_keys, holds=holds, fps=fps, W=W, H=H,
        cx=0.5, pan_x=0.0, cam1_fit=100.0, target=0.5, smooth_s=0.6,
    )
    frame_set = {k[0] for k in keys}
    assert 299 in frame_set, "Ключ (cut-1) перед склейкой отсутствует"
    assert 300 in frame_set, "Ключ (cut) на склейке отсутствует"


def test_to_ae_full_track_caching_and_error_handling(xml_subs, tmp_path, monkeypatch):
    """4. to_ae_full с включённой галкой и подменённым headtrack.track:
    трекер вызван 1 раз, сайдкар записан; повторная сборка — кэш; сменился mtime — вызван снова.
    Ошибка трекера -> сборка проходит, в emit сообщение."""
    # Создаём реальный dummy-файл видео и подставляем его путь в XML
    fake_video = str(tmp_path / "CLIP-006.MP4")
    with open(fake_video, "wb") as f:
        f.write(b"\x00" * 1024)

    # Правим XML, чтобы путь к камере 1 указывал на fake_video
    xml_content = open(xml_subs, "r", encoding="utf-8").read()
    xml_patched = xml_content.replace("C%3a/footage/cam1/CLIP-006.MP4", fake_video.replace("\\", "/"))
    xml_patched_path = str(tmp_path / "timeline_patched.xml")
    with open(xml_patched_path, "w", encoding="utf-8") as f:
        f.write(xml_patched)

    track_calls = []

    def fake_track(video, ranges, emit=None, cancel=None, fps=10):
        track_calls.append((video, tuple(ranges)))
        return {
            "v": 1, "fps": 10, "w": 1080, "h": 1920,
            "pts": [[0.0, 0.5], [10.0, 0.5]],
        }

    monkeypatch.setattr(headtrack, "track", fake_track)

    emitted = []

    def capture_emit(msg, **kw):
        emitted.append(str(msg))

    out_jsx = str(tmp_path / "out1.jsx")
    style_follow = {"cam1_head_follow": True}

    # 1. Первый запуск: трекер вызывается, сайдкар пишется
    xml2ae.to_ae_full(xml_patched_path, jsx_path=out_jsx, style=style_follow,
                      disclaimer="", emit=capture_emit)
    assert len(track_calls) == 1
    sidecar_path = os.path.splitext(xml_patched_path)[0] + ".head.json"
    assert os.path.isfile(sidecar_path), "Сайдкар .head.json не записан"
    sidecar_data = json.load(open(sidecar_path, encoding="utf-8"))
    assert sidecar_data.get("v") == 1
    assert "pts" in sidecar_data

    # 2. Повторная сборка: данные берутся из кэша, трекер НЕ вызывается
    xml2ae.to_ae_full(xml_patched_path, jsx_path=out_jsx, style=style_follow,
                      disclaimer="", emit=capture_emit)
    assert len(track_calls) == 1, "При повторной сборке трекер был вызван повторно (кэш не сработал)"

    # 3. Смена mtime видео: сайдкар инвалидируется, трекер вызывается снова
    new_mtime = os.path.getmtime(fake_video) + 10.0
    os.utime(fake_video, (new_mtime, new_mtime))
    xml2ae.to_ae_full(xml_patched_path, jsx_path=out_jsx, style=style_follow,
                      disclaimer="", emit=capture_emit)
    assert len(track_calls) == 2, "После смены mtime трекер не был перезапущен"

    # 4. Ошибка трекера: сборка проходит, в emit сообщение
    def failing_track(video, ranges, emit=None, cancel=None, fps=10):
        raise RuntimeError("GPU out of memory")

    monkeypatch.setattr(headtrack, "track", failing_track)
    # Удаляем сайдкар, чтобы заставить вызваться failing_track
    if os.path.exists(sidecar_path):
        os.remove(sidecar_path)

    err_emitted = []
    xml2ae.to_ae_full(xml_patched_path, jsx_path=out_jsx, style=style_follow,
                      disclaimer="", emit=lambda msg, **kw: err_emitted.append(str(msg)))
    assert any("слежение за головой пропущено" in m for m in err_emitted), \
        f"В emit нет сообщения о пропуске слежения: {err_emitted}"


def test_jsx_matches_main_when_follow_disabled(xml_subs, tmp_path):
    """5. Галка выключена -> .jsx побайтово как на main."""
    out_def = str(tmp_path / "def.jsx")
    out_expl = str(tmp_path / "expl.jsx")

    path_def, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=out_def, style={},
                                       disclaimer="", emit=lambda *a: None)
    path_expl, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=out_expl,
                                        style={"cam1_head_follow": False},
                                        disclaimer="", emit=lambda *a: None)

    jsx_def = open(path_def, encoding="utf-8-sig").read()
    jsx_expl = open(path_expl, encoding="utf-8-sig").read()

    assert jsx_def == jsx_expl
    assert "CAM1_FOLLOW" not in jsx_def


def test_head_at_interpolation_gaps_edges():
    """6. head_at: линейная интерполяция, провалы (None) перешагиваются, за краями — ближайшее значение."""
    pts = [
        [0.0, 0.4],
        [1.0, 0.6],
        [2.0, None],  # провал детекции
        [3.0, 0.8],
    ]

    # Интерполяция между нормальными точками: t=0.5 -> 0.5
    assert abs(headtrack.head_at(pts, 0.5) - 0.5) < 1e-4

    # Перешагивание через провал (None): линейно между t=1.0 (0.6) и t=3.0 (0.8) -> t=2.0 -> 0.7
    assert abs(headtrack.head_at(pts, 2.0) - 0.7) < 1e-4

    # За левым краем -> ближайшее (0.4)
    assert abs(headtrack.head_at(pts, -1.0) - 0.4) < 1e-4

    # За правым краем -> ближайшее (0.8)
    assert abs(headtrack.head_at(pts, 5.0) - 0.8) < 1e-4

    # Пустые точки или только None -> None
    assert headtrack.head_at([], 1.0) is None
    assert headtrack.head_at([[0.0, None], [1.0, None]], 0.5) is None


@node
def test_preview_node_cam_child_includes_off():
    """7. Превью через node: ipvCamChild включает off(t)."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    fn_child = _func(src, "ipvCamChild")
    fn_keys = _func(src, "keysAt")
    fn_shift = _func(src, "ipvCamShift")
    code = f"""
    {fn_keys}
    const IPV = {{
      fps: 60,
      plan: {{
        w: 1080,
        h: 1920,
        zoom: {{
          cx: 0.5,
          cy: 0.5,
          pan: [10, 20],
          follow: {{
            keys: [[0, 5], [60, 25]],
            ease: [[33.3333, 33.3333], [33.3333, 33.3333]]
          }}
        }}
      }}
    }};
    {fn_shift}
    {fn_child}

    // В момент t=0: off = 5 -> X = pan_x + off = 10 + 5 = 15; Y = 20
    const at0 = ipvCamChild(0, 0, 1.0, 0.0);
    // В момент t=1.0 (кадр 60): off = 25 -> X = 10 + 25 = 35; Y = 20
    const at1 = ipvCamChild(0, 0, 1.0, 1.0);
    console.log(JSON.stringify({{ at0, at1 }}));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert abs(out["at0"][0] - 15) < 1e-2
    assert abs(out["at0"][1] - 20) < 1e-2
    assert abs(out["at1"][0] - 35) < 1e-2
    assert abs(out["at1"][1] - 20) < 1e-2
