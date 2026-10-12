# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты core/podcast_cams.py (П11): разбор камер один раз на подкаст и перевод кусков без RVM.

Без GPU и без настоящего RVM: `headtrack.track` подменён счётчиком, который по участкам отдаёт
заданные головы; склейки и миниатюры подменены таймлайном (список склеек с метками ракурсов).
Настоящим ffmpeg идут только сборка рилса на синтетике (`convert_segment`) — с requires_ffmpeg.
"""
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

from core import fileio, headtrack, podcast_cams, podcast_reframe as pr, roto
from core.encoders import Choice
from core.umsg import ReelsiError
from core.xml2ae.parse import Cancelled

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нет ffmpeg")

# Таймлайн подкаста: (начало, конец, ракурс). A, B, C — уверенные (3 склейки и 20+ с каждый),
# D — мелкий ракурс из одной склейки.
TIMELINE = [(0, 25, "A"), (25, 50, "B"), (50, 75, "C"), (75, 100, "A"), (100, 125, "B"),
            (125, 150, "C"), (150, 175, "A"), (175, 200, "B"), (200, 201, "D"), (201, 220, "C")]
NO_D = [(0, 25, "A"), (25, 50, "B"), (50, 75, "C"), (75, 100, "A"), (100, 125, "B"),
        (125, 150, "C"), (150, 175, "A"), (175, 200, "B"), (200, 220, "C")]
ANGLE_VAL = {"A": 40, "B": 120, "C": 200, "D": 80}     # попарно дальше порога 18
HEADS = {"A": [[0.25, 0.3]], "B": [[0.75, 0.3]], "C": [[0.2, 0.3], [0.8, 0.3]], "D": [[0.5, 0.3]]}


def _label(shots, t):
    for s0, s1, lab in shots:
        if s0 <= t < s1:
            return lab
    raise AssertionError(f"нет склейки в t={t}")


class _FakeTrack:
    """Подмена headtrack.track: точки 5 к/с по участкам, головы — по ракурсу склейки.

    overrides(t, метка) → головы или None (тогда штатные). calls — список вызовов,
    на каждый — список участков."""

    def __init__(self, shots, overrides=None):
        self.shots = shots
        self.overrides = overrides
        self.calls = []

    def __call__(self, video, ranges, emit=None, cancel=None, fps=10, heads=False):
        self.calls.append([tuple(r) for r in ranges])
        pts = []
        for a, b in ranges:
            for k in range(int(round((b - a) * fps))):
                t = round(a + k / fps, 4)
                lab = _label(self.shots, t)
                hs = self.overrides(t, lab) if self.overrides else None
                hs = HEADS[lab] if hs is None else hs
                pts.append([t, hs[0][0] if hs else None, hs])
        return {"v": 1, "fps": fps, "w": 1920, "h": 1080, "pts": pts, "heads": True}


def _setup(monkeypatch, shots, overrides=None):
    """Подмена склеек, миниатюр, размера кадра и RVM по таймлайну `shots`. Возвращает фейк RVM."""
    fake = _FakeTrack(shots, overrides)
    monkeypatch.setattr(roto, "_probe", lambda v: (1920, 1080, 25.0, "25"))
    monkeypatch.setattr(pr, "scene_cuts",
                        lambda src, a, b, thr=pr.SCENE_THR, cancel=None: [s0 for s0, _, _ in shots[1:]])
    monkeypatch.setattr(pr, "thumbs",
                        lambda src, times, cancel=None: [
                            np.full((36, 64), ANGLE_VAL[_label(shots, t)], np.uint8) for t in times])
    monkeypatch.setattr(headtrack, "track", fake)
    return fake


def _source(tmp_path, name="talk.mp4"):
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    src = media / name
    src.write_bytes(b"x" * 64)
    return str(src)


def _proj_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    return str(d)


def _proj(src, shots):
    return {"source": src, "slug": "talk", "duration": float(shots[-1][1])}


def _emit_log():
    msgs = []

    def emit(line, **v):
        msgs.append(line.format(**v) if v else line)
    return msgs, emit


# ---------- analyze: один вызов RVM на выборки, второй — только на непонятные ----------

def test_analyze_три_уверенных_и_мелкий_два_вызова_track(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)
    fake = _setup(monkeypatch, TIMELINE)
    msgs, emit = _emit_log()

    cams = podcast_cams.analyze(proj_dir, _proj(src, TIMELINE), emit=emit)

    assert len(fake.calls) == 2, "RVM: один вызов на выборки и один на непонятные"
    assert len(fake.calls[0]) == 9, "три уверенных ракурса по три выборки"
    assert len(fake.calls[1]) == 1, "мелкий ракурс — по одной пачке на его склейку"
    assert cams["shots"][8] == [200.0, 201.0]
    assert cams["angles"] == [0, 1, 2, 0, 1, 2, 0, 1, 3, 2]
    info = cams["angle_info"]
    assert info["0"]["confident"] and info["1"]["confident"] and info["2"]["confident"]
    assert info["3"]["confident"] is False and info["3"]["n_shots"] == 1
    assert info["0"]["heads"] == [[0.25, 0.3]]
    assert info["2"]["heads"] == [[0.2, 0.3], [0.8, 0.3]]
    assert cams["shot_heads"] == {"8": [[0.5, 0.3]]}
    assert "ракурсов: 4 (уверенных: 3)" in msgs
    assert "непонятных склеек: 1" in msgs
    assert any(m.startswith("RVM: кадров ") for m in msgs)
    assert os.path.isfile(os.path.join(proj_dir, "cams.json"))


def test_analyze_выборки_разошлись_по_числу_голов(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)

    def two_heads_in_b_middle_sample(t, lab):
        # Средняя выборка ракурса B (склейка 100–125 с) видит две головы вместо одной.
        if lab == "B" and 100 <= t < 125:
            return [[0.75, 0.3], [0.9, 0.3]]
        return None

    fake = _setup(monkeypatch, TIMELINE, overrides=two_heads_in_b_middle_sample)

    cams = podcast_cams.analyze(proj_dir, _proj(src, TIMELINE))

    assert len(fake.calls) == 2
    assert cams["angle_info"]["1"]["confident"] is False
    assert cams["angle_info"]["1"]["heads"] == []
    # все три склейки ракурса B — в shot_heads, плюс мелкая D
    assert sorted(cams["shot_heads"], key=int) == ["1", "4", "7", "8"]
    assert len(fake.calls[1]) == 4


def test_analyze_выборки_разошлись_по_разбросу_hx(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)

    def shifted_b_middle_sample(t, lab):
        # Голова та же по числу, но средняя выборка сдвинута на 0.2 по X: допуск при 1920×1080
        # — agree_tol ≈ 0.079, так что расхождение явно больше допуска, а не у самой границы.
        if lab == "B" and 100 <= t < 125:
            return [[0.55, 0.3]]
        return None

    _setup(monkeypatch, TIMELINE, overrides=shifted_b_middle_sample)

    cams = podcast_cams.analyze(proj_dir, _proj(src, TIMELINE))

    assert cams["angle_info"]["1"]["confident"] is False
    assert {"1", "4", "7"} <= set(cams["shot_heads"])


def test_analyze_всё_уверенно_track_один_раз(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)
    fake = _setup(monkeypatch, NO_D)

    cams = podcast_cams.analyze(proj_dir, _proj(src, NO_D))

    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == 9
    assert cams["shot_heads"] == {}
    assert all(v["confident"] for v in cams["angle_info"].values())


def test_повторный_analyze_без_track_и_ffmpeg(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)
    fake = _setup(monkeypatch, TIMELINE)
    proj = _proj(src, TIMELINE)
    podcast_cams.analyze(proj_dir, proj)
    n_first = len(fake.calls)

    def boom(*a, **k):
        raise AssertionError("повторный разбор не должен трогать ffmpeg")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(pr, "scene_cuts", boom)
    monkeypatch.setattr(pr, "thumbs", boom)
    again = podcast_cams.analyze(proj_dir, proj)

    assert len(fake.calls) == n_first
    assert again["angles"] == [0, 1, 2, 0, 1, 2, 0, 1, 3, 2]


def test_источник_изменился_по_mtime_пересчёт(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)
    fake = _setup(monkeypatch, TIMELINE)
    proj = _proj(src, TIMELINE)
    podcast_cams.analyze(proj_dir, proj)
    assert podcast_cams.load(proj_dir, proj) is not None

    st = os.stat(src)
    os.utime(src, (st.st_atime, st.st_mtime + 100))
    assert podcast_cams.load(proj_dir, proj) is None
    podcast_cams.analyze(proj_dir, proj)
    assert len(fake.calls) == 4, "после смены mtime — полный пересчёт (два вызова RVM)"


def test_load_нет_файла_и_чужой_источник_none(tmp_path, monkeypatch):
    src = _source(tmp_path)
    other = _source(tmp_path, "other.mp4")
    proj_dir = _proj_dir(tmp_path)
    assert podcast_cams.load(proj_dir, {"source": src}) is None
    _setup(monkeypatch, TIMELINE)
    podcast_cams.analyze(proj_dir, {"source": src, "duration": 220.0})
    assert podcast_cams.load(proj_dir, {"source": src}) is not None
    assert podcast_cams.load(proj_dir, {"source": other}) is None


def test_analyze_без_длины_отказ(tmp_path, monkeypatch):
    src = _source(tmp_path)
    _setup(monkeypatch, TIMELINE)
    monkeypatch.setattr(podcast_cams.media, "probe_duration", lambda p: None)
    with pytest.raises(ReelsiError) as ei:
        podcast_cams.analyze(_proj_dir(tmp_path), {"source": src})
    assert ei.value.code == "podcast_no_duration"


def test_analyze_битый_cams_отложен_а_не_затёрт(tmp_path, monkeypatch):
    src = _source(tmp_path)
    proj_dir = _proj_dir(tmp_path)
    with open(os.path.join(proj_dir, "cams.json"), "w", encoding="utf-8") as fh:
        fh.write("{не json")
    _setup(monkeypatch, TIMELINE)
    podcast_cams.analyze(proj_dir, _proj(src, TIMELINE))
    names = os.listdir(proj_dir)
    assert any(n.startswith("cams.json.bad-") for n in names)
    assert "cams.json" in names


# ---------- правила головы и выборки (замер 2026-10-11) ----------

def test_filter_heads_микрофоны_и_плечи_не_головы():
    # Общий план: стойки микрофонов (макушка 0.61) отсеиваются, головы — 0.27 и 0.78.
    assert podcast_cams.filter_heads([[0.27, 0.27], [0.65, 0.61], [0.78, 0.25]]) == \
        [[0.27, 0.27], [0.78, 0.25]]
    # Крупный план: плечо гостя с макушкой 0.44 — не голова, рядом ведущий с макушкой 0.09.
    assert podcast_cams.filter_heads([[0.09, 0.44], [0.43, 0.09]]) == [[0.43, 0.09]]
    assert podcast_cams.filter_heads([]) == []


def test_heads_of_фильтр_до_модального_числа():
    # В каждом кадре стойка микрофона — третья «голова»; без фильтра модальное число было бы 3.
    frame = [[0.27, 0.27], [0.65, 0.61], [0.78, 0.25]]
    pts = [[round(t * 0.2, 2), 0.27, frame] for t in range(5)]
    assert podcast_cams._heads_of(pts) == [[0.27, 0.27], [0.78, 0.25]]


def test_agree_tol_четверть_окна_9_16():
    # 3840×2160: окно 1214 px из 3840 → 0.25 × 0.316 ≈ 0.079 (было 0.05).
    assert podcast_cams.agree_tol(3840, 2160) == pytest.approx(0.079, abs=5e-4)


def test_выборки_гостя_с_разбросом_в_допуске_согласны():
    tol = podcast_cams.agree_tol(3840, 2160)
    ok, heads = podcast_cams._agree([[[0.58, 0.3]], [[0.60, 0.3]], [[0.64, 0.3]]], tol)
    assert ok is True and heads == [[0.6, 0.3]]


def test_выборки_гостя_с_большим_разбросом_не_согласны():
    tol = podcast_cams.agree_tol(3840, 2160)
    ok, heads = podcast_cams._agree([[[0.50, 0.3]], [[0.60, 0.3]], [[0.70, 0.3]]], tol)
    assert ok is False and heads == []


def test_выборки_с_шириной_головы_дают_медиану_ширины():
    tol = podcast_cams.agree_tol(3840, 2160)
    ok, heads = podcast_cams._agree([[[0.58, 0.3, 0.05]], [[0.60, 0.3, 0.06]], [[0.64, 0.3, 0.07]]], tol)
    assert ok is True and heads == [[0.6, 0.3, 0.06]]
    # ширина есть не у всех выборок — в ответе её нет (двухэлементная голова, запасная ширина)
    ok, heads = podcast_cams._agree([[[0.58, 0.3, 0.05]], [[0.60, 0.3]], [[0.64, 0.3, 0.07]]], tol)
    assert ok is True and heads == [[0.6, 0.3]]


def test_выбор_выборок_25_50_75_процентов():
    assert podcast_cams._samples(list(range(8))) == [1, 3, 5]      # не 0 и не 7
    assert podcast_cams._samples([4, 5, 6]) == [4, 5, 6]           # три склейки — все три
    assert podcast_cams._samples([10, 11, 12, 13]) == [10, 11, 12]


# ---------- convert_segment без RVM: головы из cams.json ----------

def _write_cams(proj_dir, src, shots, angles, angle_info, shot_heads):
    st = os.stat(src)
    fileio.atomic_json_dump(os.path.join(proj_dir, "cams.json"), {
        "v": 1, "src": os.path.abspath(src), "size": st.st_size, "mtime": st.st_mtime,
        "src_w": 1280, "src_h": 720, "scene_thr": pr.SCENE_THR,
        "shots": [list(s) for s in shots], "angles": angles,
        "angle_info": angle_info, "shot_heads": shot_heads})


def _info(confident, heads, n=3, dur=30.0):
    return {"n_shots": n, "dur": dur, "confident": confident, "heads": heads, "samples": []}


def _forbid_rvm_and_analyze(monkeypatch):
    """convert_segment не зовёт ни RVM, ни разбор: cams.json годен, всё берётся из него."""
    calls = []

    def track(*a, **k):
        calls.append(1)
        raise AssertionError("convert_segment не должен звать RVM")

    def analyze(*a, **k):
        raise AssertionError("cams.json годен — разбор не нужен")

    monkeypatch.setattr(headtrack, "track", track)
    monkeypatch.setattr(podcast_cams, "analyze", analyze)
    return calls


def _reel_source(path):
    # 0–2 с: слева чёрный, справа белый (один ракурс); 2–4 с: жёлтый (другой ракурс).
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=black:s=640x720:d=2:r=25",
                    "-f", "lavfi", "-i", "color=white:s=640x720:d=2:r=25",
                    "-f", "lavfi", "-i", "color=yellow:s=1280x720:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=48000",
                    "-filter_complex", "[0:v][1:v]hstack=inputs=2[l];[l][2:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def _probe_json(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height:format=duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _encoder_stubs(monkeypatch):
    monkeypatch.setattr(pr.podcast_ai, "load_words", lambda folder: [])
    monkeypatch.setattr(pr.encoders, "pick", lambda purpose, *a, **k: Choice("cpu", [], "cpu"))
    monkeypatch.setattr(pr.encoders, "codec_name", lambda family, purpose: "libx264")


@requires_ffmpeg
def test_convert_segment_без_track_рамка_из_cams(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    _reel_source(src)
    proj_dir = _proj_dir(tmp_path)
    # 0–2 с — ракурс 0 (уверенный, голова слева, рамка из angle_info);
    # 2–4 с — ракурс 1 (непонятный, голова из shot_heads своей склейки).
    _write_cams(proj_dir, str(src), [(0.0, 2.0), (2.0, 4.0)], [0, 1],
                {"0": _info(True, [[0.25, 0.3]]), "1": _info(False, [], n=1, dur=2.0)},
                {"1": [[0.75, 0.3]]})
    rtrack = _forbid_rvm_and_analyze(monkeypatch)
    _encoder_stubs(monkeypatch)
    proj = {"source": str(src), "slug": "demo", "duration": 4.0}

    out = pr.convert_segment(proj_dir, proj, {"start": 0.0, "end": 4.0}, 0)

    assert rtrack == []
    assert os.path.basename(out) == "demo_01.mp4" and os.path.isfile(out)
    info = _probe_json(out)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    a_ = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
    assert (v["width"], v["height"]) == (1080, 1920)
    assert a_["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 4.0) <= 0.15
    with open(out[:-4] + ".reframe.json", encoding="utf-8") as fh:
        rj = json.load(fh)
    assert [s["heads_from"] for s in rj["shots"]] == ["angle", "shot"]
    assert rj["shots"][0]["heads"] == [[0.25, 0.3]]
    assert rj["shots"][1]["heads"] == [[0.75, 0.3]]
    assert [p["x"] for p in rj["plan"]] == [pr.crop_x(0.25, 1280, 720), pr.crop_x(0.75, 1280, 720)]


def test_convert_segment_cancel_removes_part(tmp_path, monkeypatch):
    src = _source(tmp_path, "c.mp4")
    proj_dir = _proj_dir(tmp_path)
    _write_cams(proj_dir, src, [(0.0, 4.0)], [0], {"0": _info(True, [])}, {})
    _forbid_rvm_and_analyze(monkeypatch)
    _encoder_stubs(monkeypatch)
    monkeypatch.setattr(roto, "_probe", lambda v: (1280, 720, 25.0, "25"))

    def fake_run(cmd, cancel=None, **kw):
        open(cmd[-1], "wb").write(b"partial")      # ffmpeg успел начать писать
        return None                                 # и был отменён

    monkeypatch.setattr(pr, "_run_ff", fake_run)
    with pytest.raises(Cancelled):
        pr.convert_segment(proj_dir, {"source": src, "slug": "c", "duration": 4.0},
                           {"start": 0, "end": 4}, 0)
    reels = os.path.join(proj_dir, "reels")
    assert not [p for p in os.listdir(reels) if p.endswith(".part.mp4")]


def test_convert_segment_ffmpeg_failure_is_user_error(tmp_path, monkeypatch):
    src = _source(tmp_path, "e.mp4")
    proj_dir = _proj_dir(tmp_path)
    _write_cams(proj_dir, src, [(0.0, 4.0)], [0], {"0": _info(True, [])}, {})
    _forbid_rvm_and_analyze(monkeypatch)
    _encoder_stubs(monkeypatch)
    monkeypatch.setattr(roto, "_probe", lambda v: (1280, 720, 25.0, "25"))

    class _Res:
        returncode = 1
        stderr = "Invalid argument"

    def fake_run(cmd, cancel=None, **kw):
        open(cmd[-1], "wb").write(b"partial")
        return _Res()

    monkeypatch.setattr(pr, "_run_ff", fake_run)
    with pytest.raises(ReelsiError) as ei:
        pr.convert_segment(proj_dir, {"source": src, "slug": "e", "duration": 4.0},
                           {"start": 0, "end": 4}, 0)
    assert ei.value.code == "reel_bake_failed"
    reels = os.path.join(proj_dir, "reels")
    assert not [p for p in os.listdir(reels) if p.endswith(".part.mp4")]


def test_shots_of_cams_кусок_посреди_склейки_берёт_её_ракурс(tmp_path):
    src = _source(tmp_path, "m.mp4")
    proj_dir = _proj_dir(tmp_path)
    _write_cams(proj_dir, src, [(0.0, 10.0), (10.0, 20.0)], [0, 1],
                {"0": _info(True, [[0.25, 0.3]]), "1": _info(True, [[0.75, 0.3]])}, {})
    cams = podcast_cams.load(proj_dir, {"source": src})
    inside = pr._shots_of_cams(cams, 4.0, 8.0)
    assert [(p["s0"], p["s1"], p["angle"], p["heads_from"]) for p in inside] == [(4.0, 8.0, 0, "angle")]
    assert inside[0]["heads"] == [[0.25, 0.3]]
    across = pr._shots_of_cams(cams, 8.0, 12.0)
    assert [(p["s0"], p["s1"], p["angle"]) for p in across] == [(8.0, 10.0, 0), (10.0, 12.0, 1)]


def test_cams_json_без_hw_читается_и_hw_сохраняется(tmp_path):
    # Старый cams.json (головы из двух чисел) читается; новый (с шириной) отдаёт её дальше.
    src = _source(tmp_path, "h.mp4")
    proj_dir = _proj_dir(tmp_path)
    _write_cams(proj_dir, src, [(0.0, 10.0)], [0], {"0": _info(True, [[0.25, 0.3]])}, {})
    cams = podcast_cams.load(proj_dir, {"source": src})
    assert cams is not None
    [old] = pr._shots_of_cams(cams, 0.0, 10.0)
    assert old["heads"] == [[0.25, 0.3]]
    _write_cams(proj_dir, src, [(0.0, 10.0)], [0], {"0": _info(True, [[0.25, 0.3, 0.07]])}, {})
    cams = podcast_cams.load(proj_dir, {"source": src})
    [new] = pr._shots_of_cams(cams, 0.0, 10.0)
    assert new["heads"] == [[0.25, 0.3, 0.07]]


# ---------- говорящий на общем плане: синтетика 1920×1080, две головы ----------

def _two_heads_video(path, dur=2, fps=25):
    """1920×1080: два диска-головы r=40 (макушка 260) на тёмных телах. Рот у левого мигает
    в нижней трети его лица, у правого — руки (560..700 строк, 1380..1500 колонок) мотаются
    далеко под лицом. Кадры пишутся в ffmpeg по одному. Звук — синус: bake_cmd берёт звук 0:a:0."""
    W, H = 1920, 1080
    yy, xx = np.mgrid[0:H, 0:W]
    base = np.full((H, W), 30, np.uint8)
    base[420:, 380:580] = 60
    base[420:, 1340:1540] = 60
    for cx in (480, 1440):
        base[(xx - cx) ** 2 + (yy - 300) ** 2 <= 40 ** 2] = 150
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "1920x1080",
         "-r", str(fps), "-i", "-", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}:sample_rate=48000",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)], stdin=subprocess.PIPE)
    try:
        for k in range(int(dur * fps)):
            f = base.copy()
            amp = 120 if k % 2 else 60
            f[340:352, 466:494] = amp          # рот левого
            f[560:700, 1380:1500] = amp        # руки под лицом правого
            proc.stdin.write(f.tobytes())
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise AssertionError("ffmpeg не собрал синтетику")


@requires_ffmpeg
@pytest.mark.parametrize("with_hw", [True, False], ids=["с_шириной", "старый_без_ширины"])
def test_convert_segment_говорящий_левый_по_рту_на_общем_плане(tmp_path, monkeypatch, with_hw):
    src = tmp_path / "two.mp4"
    _two_heads_video(src)
    proj_dir = _proj_dir(tmp_path)

    def head(hx):
        # Макушка 260 px из 1080; ширина головы 80 px из 1920. Без ширины — запасные 0.045.
        return [hx, 260 / 1080, 80 / 1920] if with_hw else [hx, 260 / 1080]

    _write_cams(proj_dir, str(src), [(0.0, 2.0)], [0],
                {"0": _info(True, [head(0.25), head(0.75)], n=1, dur=2.0)}, {})
    _forbid_rvm_and_analyze(monkeypatch)
    _encoder_stubs(monkeypatch)
    monkeypatch.setattr(pr.podcast_ai, "load_words", lambda folder: [{"start": 0.0, "end": 2.0}])
    proj = {"source": str(src), "slug": "two", "duration": 2.0}

    out = pr.convert_segment(proj_dir, proj, {"start": 0.0, "end": 2.0}, 0)

    assert os.path.isfile(out)
    with open(out[:-4] + ".reframe.json", encoding="utf-8") as fh:
        rj = json.load(fh)
    assert rj["speakers"], "на склейке с двумя головами говорящий должен быть решён"
    assert all(s[2] == 0 for s in rj["speakers"]), rj["speakers"]
    assert all(p["head"] == 0 for p in rj["plan"]), rj["plan"]
