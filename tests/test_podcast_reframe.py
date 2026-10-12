# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты core/podcast_reframe.py и режима heads в core/headtrack.py.

Без GPU и без RVM: модель трека подменяется фейком на CPU, ffmpeg настоящий — синтетика
в tmp_path (цвета, склейки) и декодирование для track.
"""
import shutil
import subprocess

import numpy as np
import pytest

from core import headtrack, podcast_reframe as pr, roto

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нет ffmpeg")


# ---------- маски голов ----------

def _canvas(h=200, w=320):
    return np.zeros((h, w), dtype=bool)


def _disk(m, cx, cy, r):
    h, w = m.shape
    yy, xx = np.mgrid[0:h, 0:w]
    m |= (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_heads_one_figure_one_head():
    m = _canvas()
    m[100:200, 96:224] = True          # тело
    _disk(m, 160, 60, 24)              # голова, верхняя строка 36
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 1
    assert abs(heads[0][0] - 0.5) <= 0.02
    assert abs(heads[0][1] - 36 / 200) <= 0.02


def test_heads_two_figures_merged_into_one_blob():
    m = _canvas()
    m[110:200, 40:280] = True          # общее тело-стол снизу, фигуры сидят на нём
    _disk(m, 100, 86, 24)
    _disk(m, 220, 86, 24)
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 2
    assert abs(heads[0][0] - 100 / 320) <= 0.02
    assert abs(heads[1][0] - 220 / 320) <= 0.02
    assert heads[0][0] < heads[1][0]   # слева направо


def test_heads_closer_than_min_sep_keep_higher():
    m = _canvas()
    m[120:200, 40:280] = True
    m[60:120, 150:156] = True          # высокая макушка, top = 60
    m[70:120, 162:168] = True          # низкая макушка в 12 px от неё (< 0.08*w)
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 1
    assert abs(heads[0][1] - 60 / 200) <= 0.01


def test_heads_noise_pixel_over_shoulder_is_not_a_head():
    m = _canvas()
    m[120:200, 40:280] = True          # плечи/тело
    _disk(m, 160, 96, 24)              # настоящая голова
    m[119, 80] = True                  # один пиксель над плечом
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 1
    assert abs(heads[0][0] - 0.5) <= 0.02


def test_heads_empty_mask():
    assert headtrack.heads_from_mask(np.zeros((10, 10), dtype=bool)) == []


# Холст 400×640: при h=400 строка top + 0.04·h лежит на 16 px ниже макушки, там диск
# радиуса 24 почти в полную ширину (по 2r), поэтому замер hw ≈ 2r/w сходится в ±25 %.

def test_head_width_from_mask_is_diameter_of_circle_head():
    m = np.zeros((400, 640), dtype=bool)
    m[200:400, 200:440] = True         # тело
    _disk(m, 320, 100, 24)             # голова радиуса 24, макушка 76
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 1 and len(heads[0]) == 3
    assert heads[0][2] == pytest.approx(2 * 24 / 640, rel=0.25)


def test_head_width_each_head_has_its_own():
    m = np.zeros((400, 640), dtype=bool)
    m[200:400, 60:580] = True          # общее тело-стол
    _disk(m, 200, 150, 24)             # большая голова слева
    _disk(m, 440, 150, 12)             # маленькая справа
    heads = headtrack.heads_from_mask(m)
    assert len(heads) == 2
    assert heads[0][2] == pytest.approx(2 * 24 / 640, rel=0.25)
    assert heads[1][2] == pytest.approx(2 * 12 / 640, rel=0.25)
    assert heads[0][2] > 1.5 * heads[1][2]


def test_head_hw_default_for_two_element_head():
    # Старый трек и старый cams.json: голова без третьего числа — запасная ширина 0.045.
    assert headtrack.head_hw([0.3, 0.2]) == pytest.approx(headtrack.HEAD_HW_DEFAULT)
    assert headtrack.head_hw([0.3, 0.2, 0.07]) == pytest.approx(0.07)
    assert headtrack.HEAD_HW_DEFAULT == pytest.approx(0.045)


def test_head_at_accepts_three_element_points():
    pts = [[0.0, 0.3, [[0.3, 0.2]]], [1.0, 0.5, [[0.5, 0.2]]], [2.0, None, []]]
    assert headtrack.head_at(pts, 0.5) == pytest.approx(0.4)
    assert headtrack.head_at(pts, 1.5) == pytest.approx(0.5)


# ---------- track: формат ответа и вызов heads_from_mask ----------

class _FakeRVM:
    """Подмена модели RVM: отдаёт альфу с фигурой (тело + голова) для любого кадра."""

    def __call__(self, src, *args):
        t, oh, ow = src.shape[1], src.shape[-2], src.shape[-1]
        m = np.zeros((oh, ow), dtype=np.float32)
        m[int(oh * 0.5):, int(ow * 0.3):int(ow * 0.7)] = 1.0
        yy, xx = np.mgrid[0:oh, 0:ow]
        m[(xx - ow * 0.5) ** 2 + (yy - oh * 0.3) ** 2 <= (oh * 0.1) ** 2] = 1.0
        import torch
        pha = torch.from_numpy(m).view(1, 1, 1, oh, ow).expand(1, t, 1, oh, ow).clone()
        return src, pha


def _synthetic_clip(tmp_path, name="clip.mp4", dur=1):
    path = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=red:s=320x180:d={dur}:r=10", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return str(path)


@pytest.fixture
def fake_rvm(monkeypatch):
    import torch
    monkeypatch.setattr(roto, "_probe", lambda video: (320, 180, 10.0, 10))
    monkeypatch.setattr(roto, "_load", lambda: (_FakeRVM(), "cpu", torch.float32))
    monkeypatch.setattr(roto, "release", lambda emit=None: None)


@requires_ffmpeg
def test_track_default_format_is_two_elements(tmp_path, fake_rvm, monkeypatch):
    calls = []
    real = headtrack.heads_from_mask
    monkeypatch.setattr(headtrack, "heads_from_mask", lambda m, *a, **k: calls.append(1) or real(m, *a, **k))
    video = _synthetic_clip(tmp_path)
    res = headtrack.track(video, [(0.0, 1.0)], fps=10)
    assert res["pts"], "трек должен отдать точки"
    assert all(len(p) == 2 for p in res["pts"])
    assert "heads" not in res
    assert calls == [], "heads_from_mask не должна вызываться без heads=True"


@requires_ffmpeg
def test_track_heads_mode_adds_heads_per_point(tmp_path, fake_rvm):
    video = _synthetic_clip(tmp_path)
    res = headtrack.track(video, [(0.0, 1.0)], fps=10, heads=True)
    assert res["heads"] is True
    assert res["pts"]
    for p in res["pts"]:
        assert len(p) == 3
        assert len(p[2]) == 1
        assert abs(p[2][0][0] - 0.5) <= 0.02
        assert len(p[2][0]) == 3                 # [hx, top, hw]


# ---------- ffmpeg: резы, склейки ----------

def test_parse_scene_times_adds_offset():
    text = ("[Parsed_showinfo_1 @ 0x1] n:   0 pts:  15 pts_time:1.5 duration:1\n"
            "[Parsed_showinfo_1 @ 0x1] n:   1 pts:  57 pts_time:2.8 duration:1\n"
            "frame=  12 fps=0.0\n")
    assert pr.parse_scene_times(text, 0.5) == [2.0, 3.3]


@requires_ffmpeg
def test_scene_cuts_on_synthetic_red_blue_green(tmp_path):
    # Красный 2 с, синяя вставка 0.3 с, зелёный 2 с. Резы (абсолютно): ~2.0 и ~2.3.
    src = tmp_path / "scene.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=red:s=320x180:d=2:r=25",
                    "-f", "lavfi", "-i", "color=blue:s=320x180:d=0.3:r=25",
                    "-f", "lavfi", "-i", "color=green:s=320x180:d=2:r=25",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    cuts = pr.scene_cuts(str(src), 0.5, 4.3)
    assert len(cuts) == 2
    assert abs(cuts[0] - 2.0) <= 0.1
    assert abs(cuts[1] - 2.3) <= 0.1
    sh = pr.shots(cuts, 0.5, 4.3)
    # короткая синяя склейка (0.3 с) слита с предыдущей
    assert len(sh) == 2
    assert sh[0][0] == pytest.approx(0.5)
    assert sh[0][1] == pytest.approx(cuts[1], abs=1e-6)
    assert sh[1][0] == pytest.approx(cuts[1], abs=1e-6)
    assert sh[1][1] == pytest.approx(4.3)


@requires_ffmpeg
def test_scene_cuts_finds_dark_scene_cut_at_default_threshold(tmp_path):
    # Два ТЁМНЫХ кадра разного содержания: серый прямоугольник слева, затем справа, на чёрном.
    # Скор склейки ~0.215 — как у настоящих склеек подкаста владельца (0.22–0.24), а не выше
    # порога 0.3. При 0.3 такой рез пропадал: подкаст считался одной камерой.
    src = tmp_path / "dark.mp4"
    box = "color=c=black:s=320x180:d=1:r=25,drawbox=x={x}:y=40:w=100:h=100:color=0x484848:t=fill"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", box.format(x=20),
                    "-f", "lavfi", "-i", box.format(x=190),
                    "-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    cuts = pr.scene_cuts(str(src), 0.0, 2.0)
    assert len(cuts) == 1
    assert abs(cuts[0] - 1.0) <= 0.1


def test_shots_merge_short_into_previous_and_ignore_outside():
    # короткая средняя склейка — в предыдущую
    assert pr.shots([2.0, 2.3], 0.5, 4.3) == [(0.5, 2.3), (2.3, 4.3)]
    # короткая первая — в следующую
    assert pr.shots([0.2, 3.0], 0.0, 4.0) == [(0.0, 3.0), (3.0, 4.0)]
    # резы вне (a, b) игнорируются
    assert pr.shots([-1.0, 0.0, 10.0], 0.0, 4.0) == [(0.0, 4.0)]


@requires_ffmpeg
def test_thumbs_and_group_angles_on_synthetic(tmp_path):
    # Красный 2 с + синий 2 с: кадр 0.5 и 1.0 — один ракурс, кадр 2.5 — другой.
    src = tmp_path / "rb.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=red:s=320x180:d=2:r=10",
                    "-f", "lavfi", "-i", "color=blue:s=320x180:d=2:r=10",
                    "-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    th = pr.thumbs(str(src), [0.5, 1.0, 2.5])
    assert all(t.shape == (36, 64) and t.dtype == np.uint8 for t in th)
    assert pr.group_angles(th) == [0, 0, 1]


def test_group_angles_greedy_by_first_shot_of_angle():
    a = np.full((36, 64), 128, np.uint8)
    b = np.full((36, 64), 128, np.uint8)
    c = np.full((36, 64), 200, np.uint8)
    assert pr.group_angles([a, b, c]) == [0, 0, 1]
    assert pr.group_angles([a, c, b]) == [0, 1, 0]


# ---------- ракурсы, рамка ----------

def test_heads_of_points_modal_count_and_medians():
    pts = [[0.0, 0.3, [[0.3, 0.2]]], [0.1, None, []], [0.2, 0.31, [[0.31, 0.2]]],
           [0.3, 0.5, [[0.2, 0.3], [0.8, 0.3]]]]
    # точки без голов не считаются; одна голова встречается чаще (2 из 3) — медиана по ним: 0.3 и 0.31
    assert pr.heads_of_points(pts) == [[0.305, 0.2]]
    assert pr.heads_of_points([[0.0, None, []]]) == []
    # ничья по частоте: больше голов (две головы — не одна)
    tie = [[0.0, 0.3, [[0.3, 0.2]]], [0.1, 0.2, [[0.2, 0.3], [0.8, 0.3]]]]
    assert pr.heads_of_points(tie) == [[0.2, 0.3], [0.8, 0.3]]


def test_heads_of_points_медиана_ширины_головы():
    pts = [[0.0, 0.3, [[0.3, 0.2, 0.06]]], [0.1, 0.3, [[0.31, 0.2, 0.08]]],
           [0.2, 0.3, [[0.32, 0.2, 0.07]]]]
    assert pr.heads_of_points(pts) == [[0.31, 0.2, 0.07]]
    # смешанные точки (часть без ширины) — ширины нет, голова двухэлементная
    mixed = [[0.0, 0.3, [[0.3, 0.2, 0.06]]], [0.1, 0.3, [[0.31, 0.2]]]]
    assert pr.heads_of_points(mixed) == [[0.305, 0.2]]


def test_reassign_nearest_moves_shot_to_closest_angle():
    # Сценарий 1:04 (владелец, 2026-10-11): B под порогом к A (17 < 18), к C ближе (8).
    A, B, C = (np.full((36, 64), v, np.uint8) for v in (100, 117, 125))
    thumbs = [A, A, B, C, C, C]
    g = pr.group_angles(thumbs)
    assert g == [0, 0, 0, 1, 1, 1]              # до переназначения B — у A
    r = pr.reassign_nearest(thumbs, g)
    assert r[2] == r[3]                          # после — в ракурсе C
    assert r[2] != r[0]
    assert r == [0, 0, 1, 1, 1, 1]               # номера по первому появлению


def test_reassign_nearest_drops_empty_and_renumbers():
    a = np.full((36, 64), 10, np.uint8)
    b = np.full((36, 64), 200, np.uint8)
    assert pr.reassign_nearest([a, b, a], [5, 9, 5]) == [0, 1, 0]
    assert pr.reassign_nearest([], []) == []


def test_crop_x_1920x1080():
    assert pr.crop_x(0.5, 1920, 1080) == 656     # cw = 608
    assert pr.crop_x(0.0, 1920, 1080) == 0
    assert pr.crop_x(1.0, 1920, 1080) == 1920 - 608
    assert pr.crop_x(None, 1920, 1080) == 656


def test_plan_crops_one_head_per_shot():
    shot_list = [(0.0, 5.0), (5.0, 10.0)]
    plan = pr.plan_crops(shot_list, [0, 1], [[0.3], [0.5]], src_w=1920, src_h=1080)
    assert [p["x"] for p in plan] == [pr.crop_x(0.3, 1920, 1080), pr.crop_x(0.5, 1920, 1080)]
    assert plan[0]["angle"] == 0 and plan[1]["angle"] == 1


def test_plan_crops_головы_по_склейке_а_не_по_ракурсу():
    # Одна и та же метка ракурса 0, но у второй склейки своя голова: рамка идёт за склейкой.
    shot_list = [(0.0, 5.0), (5.0, 10.0)]
    plan = pr.plan_crops(shot_list, [0, 0], [[0.3], [0.7]], src_w=1920, src_h=1080)
    assert [p["x"] for p in plan] == [pr.crop_x(0.3, 1920, 1080), pr.crop_x(0.7, 1920, 1080)]


def test_plan_crops_two_heads_split_by_speakers():
    shot_list = [(0.0, 5.0), (5.0, 10.0)]
    heads = [[0.3], [0.2, 0.8]]
    speakers = [(5.0, 7.5, 0), (7.5, 10.0, 1)]
    plan = pr.plan_crops(shot_list, [0, 1], heads, speakers=speakers, src_w=1920, src_h=1080)
    assert len(plan) == 3
    assert plan[1]["t0"] == 5.0 and plan[1]["t1"] == 7.5 and plan[1]["head"] == 0
    assert plan[2]["t0"] == 7.5 and plan[2]["t1"] == 10.0 and plan[2]["head"] == 1
    assert plan[1]["x"] != plan[2]["x"]


def test_plan_crops_two_heads_without_speakers_uses_center():
    shot_list = [(0.0, 5.0)]
    plan = pr.plan_crops(shot_list, [1], [[0.2, 0.8]], src_w=1920, src_h=1080)
    assert len(plan) == 1
    assert plan[0]["x"] == pr.crop_x(0.5, 1920, 1080)
    assert plan[0]["head"] is None


def test_plan_crops_merges_equal_neighbours():
    plan = pr.plan_crops([(0.0, 5.0), (5.0, 10.0)], [0, 0], [[0.5], [0.5]], src_w=1920, src_h=1080)
    assert plan == [{"t0": 0.0, "t1": 10.0, "x": 656, "angle": 0, "head": 0}]


# ---------- кто говорит: движение рта ----------
# Кадр 200×320 (малый, для окон). Головы hx=0.25 и 0.75, макушка 0.3, hw=0.18 → окно
# face_box: по X 51..109 и 211..269, по Y 60..135 (75 строк). Верхняя треть — строки 60..84,
# нижняя — 110..134. Рот — в нижней трети, моргание — в верхней.

HEADS = [[0.25, 0.3, 0.18], [0.75, 0.3, 0.18]]
MOUTH_BOX = {0: (115, 129, 70, 91), 1: (115, 129, 230, 251)}   # (y0, y1, x0, x1): рот, нижняя треть
HEAD_BOX = {0: (60, 135, 51, 109), 1: (60, 135, 211, 269)}     # всё окно головы


def _toggle(n, boxes, h=200, w=320):
    """n кадров h×w: области boxes [(box, amp)] попеременно 0 и amp (движение)."""
    frames = []
    for k in range(n):
        f = np.zeros((h, w), np.uint8)
        if k % 2:
            for (y0, y1, x0, x1), amp in boxes:
                f[y0:y1, x0:x1] = amp
        frames.append(f)
    return frames


def _cut(frames, heads=HEADS):
    """Окна лиц по кадрам — как face_crops: на голову список кадров окна (копии, через face_box)."""
    h, w = frames[0].shape
    out = []
    for hd in heads:
        x0, x1, y0, y1 = pr.face_box(float(hd[0]), float(hd[1]), headtrack.head_hw(hd), w, h)
        out.append([f[y0:y1, x0:x1].copy() for f in frames])
    return out


def _talk(who, amp=200, n=10):
    return _cut(_toggle(n, [(MOUTH_BOX[who], amp)]))


def _silence(n=10):
    return _cut(_toggle(n, []))


def test_mouth_only_talking_head_counts():
    e = pr.mouth_activity(_talk(1, n=20))
    assert e[0] == 0.0
    assert e[1] > 0.0


def test_mouth_whole_head_motion_gives_no_lead():
    # Вся голова (верх и низ окна) двигается одинаково: вычитание верхней трети гасит её.
    e = pr.mouth_activity(_cut(_toggle(20, [(HEAD_BOX[0], 150), (HEAD_BOX[1], 150)])))
    assert e[0] == pytest.approx(0.0, abs=1e-9)
    assert e[1] == pytest.approx(0.0, abs=1e-9)


def test_mouth_motion_in_upper_third_is_not_speech():
    # Моргание/лоб — верхняя треть: энергия отрицательна и обрезается до нуля.
    e = pr.mouth_activity(_cut(_toggle(10, [((62, 80, 60, 100), 150)])))
    assert e == [0.0, 0.0]


def test_mouth_fewer_than_two_frames_is_zero():
    assert pr.mouth_activity(_talk(0, n=1)) == [0.0, 0.0]


def test_mouth_at_full_scale_two_heads_80px():
    # Кадр 1920×1080, головы по 80 px: окно 80×104 (строки 324..427), нижняя треть — 394..427.
    # Рот левого — 400..412; у правого покоя нет, у левого рот мигает — перевес за ним.
    W, H = 1920, 1080
    heads = [[0.25, 0.3, 80 / W], [0.75, 0.3, 80 / W]]
    frames = _toggle(10, [((400, 412, 466, 494), 120)], h=H, w=W)
    e = pr.mouth_activity(_cut(frames, heads))
    assert e[0] > 0.0
    assert e[1] == 0.0


def test_hands_under_second_head_do_not_leak_into_its_window():
    # Руки (560..700 строк, 1380..1500 колонок) мотаются под лицом правого. Окно лица 80 px
    # их не захватывает; окно 0.18·W (345 px, высота 449) — захватило бы и дало правому перевес.
    W, H = 1920, 1080
    heads = [[0.25, 0.3, 80 / W], [0.75, 0.3, 80 / W]]
    frames = _toggle(10, [((400, 412, 466, 494), 120), ((560, 700, 1380, 1500), 60)], h=H, w=W)
    e = pr.mouth_activity(_cut(frames, heads))
    assert e[0] > 0.0
    assert e[1] == 0.0


def test_face_box_geometry_1920():
    # hx·W ± hw·W/2 по X; top·H вниз на 1.3·hw·W по Y; край кадра обрезает.
    assert pr.face_box(0.25, 0.3, 80 / 1920, 1920, 1080) == (440, 520, 324, 428)
    assert pr.face_box(0.0, 0.0, 0.1, 1920, 1080) == (0, 96, 0, 250)
    assert pr.face_box(1.0, 0.99, 0.1, 1920, 1080) == (1824, 1920, 1069, 1080)


def _words(*spans):
    return [{"start": s, "end": e} for s, e in spans]


def test_speakers_hold_blocks_switch_within_1s_of_last_change():
    wins = [(0.0, 1.0, _talk(0)), (1.0, 2.0, _talk(1)), (2.0, 3.0, _talk(1))]
    words = _words((0.1, 0.9), (1.1, 1.9), (2.1, 2.9))
    out = pr.speakers_for_shot(0.0, 3.0, wins, HEADS, words)
    # смена на 1-й секунде отложена (1.0 - 0.0 < 1.5), на 2-й — проходит
    assert out == [(0.0, 2.0, 0), (2.0, 3.0, 1)]


def test_speakers_margin_10_percent_keeps_speaker():
    both = _cut(_toggle(10, [(MOUTH_BOX[0], 200), (MOUTH_BOX[1], 180)]))   # перевес 10 % < 15 %
    wins = [(0.0, 1.0, _talk(1)), (1.0, 2.0, both)]
    words = _words((0.1, 0.9), (1.1, 1.9))
    assert pr.speakers_for_shot(0.0, 2.0, wins, HEADS, words) == [(0.0, 2.0, 1)]


def test_speakers_window_without_words_keeps_speaker():
    wins = [(0.0, 1.0, _talk(0)), (1.0, 2.0, _talk(1))]
    words = _words((0.1, 0.9))
    assert pr.speakers_for_shot(0.0, 2.0, wins, HEADS, words) == [(0.0, 2.0, 0)]


def test_speakers_first_undecided_window_takes_max_total_energy():
    wins = [(0.0, 1.0, _silence()), (1.0, 2.0, _talk(1))]
    assert pr.speakers_for_shot(0.0, 2.0, wins, HEADS, []) == [(0.0, 2.0, 1)]


def test_speakers_cover_shot_without_gaps():
    wins = [(0.2, 1.2, _talk(0)), (1.2, 2.7, _talk(0))]
    words = _words((0.3, 2.5))
    out = pr.speakers_for_shot(0.2, 2.7, wins, HEADS, words)
    assert out == [(0.2, 2.7, 0)]
    out = pr.speakers_for_shot(0.2, 2.7, [(0.2, 1.2, _silence()), (1.2, 2.7, _talk(1))],
                               HEADS, _words((1.3, 2.5)))
    assert out[0][0] == pytest.approx(0.2) and out[-1][1] == pytest.approx(2.7)
    assert all(a[1] == pytest.approx(b[0]) for a, b in zip(out, out[1:]))


# ---------- ffmpeg-строки: файл команд, путь фильтра, bake_cmd ----------

def test_sendcmd_text_skips_first_element():
    plan = [{"t0": 0.0, "x": 118}, {"t0": 2.0, "x": 758}, {"t0": 3.5, "x": 118}]
    assert pr.sendcmd_text(plan) == "2.000 crop x 758;\n3.500 crop x 118;\n"
    assert pr.sendcmd_text(plan[:1]) == ""


def test_ff_filter_path_escapes_drive_colon():
    assert pr.ff_filter_path("C:\\a b\\c.txt") == "C\\:/a b/c.txt"


def test_bake_cmd_single_shot_has_no_sendcmd_and_encodes_aac():
    cmd = pr.bake_cmd("in.mp4", 0.0, 4.0, [{"t0": 0.0, "x": 118}], 1280, 720,
                      "out.part.mp4", "C:\\x\\c.txt", ["-c:v", "libx264"])
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "sendcmd" not in fc
    assert "crop=w=404:h=ih:x=118:y=0" in fc
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "320k"


def test_bake_cmd_many_shots_uses_sendcmd_with_escaped_path():
    plan = [{"t0": 0.0, "x": 118}, {"t0": 2.0, "x": 758}]
    cmd = pr.bake_cmd("in.mp4", 0.0, 4.0, plan, 1280, 720, "o.mp4", "C:\\x y\\c.txt", [])
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "sendcmd=f='C\\:/x y/c.txt'" in fc


# ---------- ffmpeg на синтетике: кадры рта ----------

def _src_1080p(tmp_path, dur=1):
    src = tmp_path / "hd.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=1920x1080:rate=10:duration={dur}", "-pix_fmt", "yuv420p",
                    str(src)], check=True)
    return str(src)


@requires_ffmpeg
def test_face_crops_windows_at_full_resolution(tmp_path):
    src = _src_1080p(tmp_path)
    # Первая голова 80 px (окно 80×104); вторая без ширины — запасные 0.045 (86×112).
    heads = [[0.25, 0.3, 80 / 1920], [0.75, 0.3]]
    out = pr.face_crops(src, 0.0, 1.0, heads)
    assert len(out) == 2 and all(len(fr) == 10 for fr in out)
    assert all(f.shape == (104, 80) and f.dtype == np.uint8 for f in out[0])
    assert all(f.shape == (112, 86) for f in out[1])
    # Целый кадр не возвращается ни разу: только окна голов.
    assert not any(f.shape == (1080, 1920) for fr in out for f in fr)


@requires_ffmpeg
def test_face_crops_window_is_the_frame_region(tmp_path):
    src = _src_1080p(tmp_path)
    win = pr.face_crops(src, 0.0, 1.0, [[0.25, 0.3, 80 / 1920]])[0][0]
    full = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-frames:v", "1",
                           "-vf", "scale=1920:1080,format=gray", "-f", "rawvideo", "-"],
                          stdout=subprocess.PIPE, check=True).stdout
    ref = np.frombuffer(full, np.uint8).reshape(1080, 1920)
    assert np.array_equal(win, ref[324:428, 440:520])


def test_face_crops_no_heads_reads_nothing():
    assert pr.face_crops("не-существует.mp4", 0.0, 1.0, []) == []


# --- имя рилса и папка рилсов (П-новое, 2026-10-11) ---

def test_имя_рилса_занятое_чужим_файлом_уходит_на_следующий_номер(tmp_path):
    """Чужой `<slug>_01.mp4` в папке камеры 1 не трогаем: кусок 1 берёт `_02`."""
    import os
    reels = tmp_path / "reels"
    reels.mkdir()
    (reels / "vypusk_01.mp4").write_bytes(b"foreign")
    out = pr.reel_path(str(reels), "vypusk", 0)
    assert os.path.basename(out) == "vypusk_02.mp4"
    assert (reels / "vypusk_01.mp4").read_bytes() == b"foreign"


def test_имя_рилса_свой_рилс_куска_переписывается_на_месте(tmp_path):
    """Перевод того же куска второй раз — тот же файл, а не копия с новым номером."""
    reels = tmp_path / "reels"
    reels.mkdir()
    own = reels / "vypusk_02.mp4"
    own.write_bytes(b"own")
    assert pr.reel_path(str(reels), "vypusk", 1, own=str(own)) == str(own)


def test_имя_рилса_занятый_reframe_json_тоже_занимает_номер(tmp_path):
    import os
    reels = tmp_path / "reels"
    reels.mkdir()
    (reels / "vypusk_01.reframe.json").write_text("{}", encoding="utf-8")
    assert os.path.basename(pr.reel_path(str(reels), "vypusk", 0)) == "vypusk_02.mp4"


def _stub_bake(monkeypatch, cams_shots):
    """Конвейер куска без ffmpeg и GPU: разбор камер, слова, кодек и сам проход ffmpeg."""
    import os
    from types import SimpleNamespace
    from core import encoders, podcast_ai, podcast_cams
    monkeypatch.setattr(roto, "_probe", lambda p: (1920, 1080, 25.0, 100))
    monkeypatch.setattr(podcast_cams, "load", lambda d, p: {
        "shots": cams_shots, "angles": [0] * len(cams_shots), "angle_info": {}, "shot_heads": {}})
    monkeypatch.setattr(podcast_ai, "load_words", lambda d: [])
    monkeypatch.setattr(encoders, "pick", lambda role: SimpleNamespace(family="x"))
    monkeypatch.setattr(encoders, "codec_name", lambda fam, role: "libx264")
    monkeypatch.setattr(encoders, "codec_args", lambda codec, role: [])

    def fake_run(cmd, cancel=None):
        out = cmd[-1]                              # последний аргумент — временный .part.mp4
        with open(out, "wb") as fh:
            fh.write(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(pr, "_run_ff", fake_run)
    return os


def test_перевод_куска_пишет_в_папку_рилсов_и_не_затирает_чужой(tmp_path, monkeypatch):
    os = _stub_bake(monkeypatch, [(0.0, 60.0)])
    proj_dir = tmp_path / "podcasts" / "Выпуск"
    proj_dir.mkdir(parents=True)
    cam1 = tmp_path / "cam1"
    cam1.mkdir()
    (cam1 / "vypusk_01.mp4").write_bytes(b"foreign")
    proj = {"source": str(tmp_path / "src.mp4"), "slug": "vypusk"}
    seg = {"id": "aaa111", "start": 10.0, "end": 40.0, "title": "", "why": "", "reel": ""}
    out = pr.convert_segment(str(proj_dir), proj, seg, 0, reels_dir=str(cam1))
    assert out == str(cam1 / "vypusk_02.mp4")
    assert seg["reel"] == out                                     # в проекте — фактический путь
    assert (cam1 / "vypusk_01.mp4").read_bytes() == b"foreign"      # чужой не тронут
    assert (cam1 / "vypusk_02.mp4").read_bytes() == b"mp4"
    assert os.path.isfile(cam1 / ".vypusk_02.crop.txt")           # sendcmd рядом с рилсом
    assert os.path.isfile(cam1 / "vypusk_02.reframe.json")
    assert not os.path.exists(proj_dir / "reels")                 # в папке подкаста ничего не легло


def test_перевод_куска_без_папки_рилсов_идёт_в_reels_подкаста(tmp_path, monkeypatch):
    os = _stub_bake(monkeypatch, [(0.0, 60.0)])
    proj_dir = tmp_path / "podcasts" / "Выпуск"
    proj_dir.mkdir(parents=True)
    proj = {"source": str(tmp_path / "src.mp4"), "slug": "vypusk"}
    seg = {"id": "aaa111", "start": 10.0, "end": 40.0, "title": "", "why": "", "reel": ""}
    out = pr.convert_segment(str(proj_dir), proj, seg, 1)
    assert out == os.path.join(str(proj_dir), "reels", "vypusk_02.mp4")
    assert os.path.isfile(os.path.join(str(proj_dir), "reels", ".vypusk_02.crop.txt"))
