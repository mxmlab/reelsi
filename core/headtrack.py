# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Трек положения головы спикера по маске человека RVM (задание ZC).

Использует Robust Video Matting (core.roto) на участках показа Камеры 1:
на каждом кадре находит центр верхней полосы (12% высоты) силуэта человека.
Результат кэшируется в сайдкар <стем>.head.json рядом с XML.
"""
import json
import os
import subprocess

from core import fileio, roto
from core.app_meta import wrap_emit


def merge_ranges(ranges):
    """Слить перекрывающиеся и смежные диапазоны [(a, b), ...]."""
    if not ranges:
        return []
    cleaned = [(float(a), float(b)) for a, b in ranges if float(b) > float(a)]
    if not cleaned:
        return []
    sorted_r = sorted(cleaned, key=lambda r: (r[0], r[1]))
    merged = [list(sorted_r[0])]
    for cur_a, cur_b in sorted_r[1:]:
        if cur_a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], cur_b)
        else:
            merged.append([cur_a, cur_b])
    return [(r[0], r[1]) for r in merged]


def ranges_cover(cached_ranges, requested_ranges, eps=0.05):
    """Проверить, что cached_ranges полностью покрывают requested_ranges."""
    merged_cached = merge_ranges(cached_ranges)
    merged_req = merge_ranges(requested_ranges)
    for req_a, req_b in merged_req:
        covered = False
        for c_a, c_b in merged_cached:
            if c_a <= req_a + eps and c_b >= req_b - eps:
                covered = True
                break
        if not covered:
            return False
    return True


def track(video, ranges, emit=None, cancel=None, fps=10):
    """RVM-трекинг головы спикера по участкам исходника ranges ([(a, b), ...]).

    На кадр: alpha > 0.5, верхняя строка силуэта top, полоса [top, top + 0.12*h),
    hx = среднее X пикселей полосы / w. Нет человека — hx = None.
    Ответ: {"v": 1, "fps": 10, "w": w_src, "h": h_src, "pts": [[t_src, hx], ...]}.
    """
    emit = wrap_emit(emit)
    video = os.path.abspath(video)
    w_src, h_src, _, _ = roto._probe(video)
    if not w_src or not h_src:
        raise RuntimeError(f"ffprobe не отдал размеры видео: {video}")

    merged = merge_ranges(ranges)
    if not merged:
        return {"v": 1, "fps": fps, "w": w_src, "h": h_src, "pts": []}

    import numpy as np
    import torch

    ow = 432
    oh = int(round((h_src * 432.0 / w_src) / 2.0)) * 2
    frame_bytes = ow * oh * 3

    model, dev, dtype = roto._load()
    downsample_ratio = 0.5
    chunk = 8 if dev == "cuda" else 2
    pts = []

    try:
        with torch.inference_mode():
            for a, b in merged:
                if cancel and cancel():
                    break
                rec = [None] * 4
                dec = ["ffmpeg", "-v", "error"]
                if dev == "cuda" and roto._nvdec_ok(video):
                    dec += ["-hwaccel", "cuda"]
                dec += [
                    "-ss", f"{a:.3f}",
                    "-to", f"{b:.3f}",
                    "-i", video,
                    "-vf", f"fps={fps},scale=432:-2",
                    "-f", "rawvideo",
                    "-pix_fmt", "rgb24",
                    "pipe:1",
                ]
                p_dec = subprocess.Popen(dec, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         bufsize=frame_bytes * (chunk + 1))
                frame_idx = 0
                try:
                    while True:
                        if cancel and cancel():
                            break
                        frames = []
                        for _ in range(chunk):
                            raw = roto._read_exact(p_dec.stdout, frame_bytes)
                            if len(raw) < frame_bytes:
                                break
                            frames.append(np.frombuffer(raw, np.uint8).reshape(oh, ow, 3))
                        if not frames:
                            break
                        src = torch.from_numpy(np.stack(frames)).to(dev)
                        src = src.permute(0, 3, 1, 2).unsqueeze(0).to(dtype).div_(255)
                        _fgr, pha, *rec = model(src, *rec, downsample_ratio)
                        alphas = (pha[0, :, 0] > 0.5).cpu().numpy()
                        for t_idx in range(len(frames)):
                            mask = alphas[t_idx]
                            t_src = round(a + (frame_idx + t_idx) / float(fps), 4)
                            if not np.any(mask):
                                hx = None
                            else:
                                ys, xs = np.where(mask)
                                top = int(ys.min())
                                stripe = (ys >= top) & (ys < top + 0.12 * oh)
                                stripe_xs = xs[stripe]
                                if len(stripe_xs) == 0:
                                    hx = None
                                else:
                                    hx = round(float(np.mean(stripe_xs)) / ow, 4)
                            pts.append([t_src, hx])
                        frame_idx += len(frames)
                finally:
                    try:
                        if p_dec.poll() is None:
                            p_dec.kill()
                        p_dec.wait(timeout=5)
                    except Exception:
                        pass
                    if p_dec.stdout:
                        try:
                            p_dec.stdout.close()
                        except Exception:
                            pass
    finally:
        try:
            roto.release(emit=emit)
        except Exception:
            pass

    return {"v": 1, "fps": fps, "w": w_src, "h": h_src, "pts": pts}


def load_cached(xml_path, video):
    """Проверить валидность сайдкара <стем>.head.json и вернуть данные или None.

    Годен, если файл существует, v == 1, путь, размер и mtime видео совпадают.
    """
    if not xml_path or not video:
        return None
    video = os.path.abspath(video)
    if not os.path.isfile(video):
        return None
    head_path = os.path.splitext(xml_path)[0] + ".head.json"
    if not os.path.isfile(head_path):
        return None
    data = fileio.json_load_soft(head_path)
    if (
        isinstance(data, dict)
        and data.get("v") == 1
        and os.path.abspath(data.get("video", "")) == video
        and data.get("size") == os.path.getsize(video)
        and abs(data.get("mtime", 0) - os.path.getmtime(video)) < 1e-4
    ):
        return data
    return None


def load_or_track(xml_path, video, ranges, emit=None, cancel=None, fps=10):
    """Загрузить трек из <стем>.head.json или посчитать заново и сохранить."""
    emit = wrap_emit(emit)
    video = os.path.abspath(video)
    if not os.path.isfile(video):
        raise FileNotFoundError(f"Видео не найдено: {video}")
    size = os.path.getsize(video)
    mtime = os.path.getmtime(video)
    head_path = os.path.splitext(xml_path)[0] + ".head.json" if xml_path else None
    merged_ranges = merge_ranges(ranges)

    cached = load_cached(xml_path, video)
    if cached is not None and ranges_cover(cached.get("ranges", []), merged_ranges):
        if head_path:
            emit("  · трек головы: из кэша {path}", path=os.path.basename(head_path))
        return cached

    emit("  · трек головы Камеры 1 (RVM)...")
    res = track(video, merged_ranges, emit=emit, cancel=cancel, fps=fps)
    data = dict(res)
    data["video"] = video
    data["size"] = size
    data["mtime"] = mtime
    data["ranges"] = [list(r) for r in merged_ranges]

    if head_path:
        fileio.atomic_text_write(head_path, json.dumps(data, ensure_ascii=False, indent=2))
        emit("  · сохранён трек головы {path}", path=os.path.basename(head_path))
    return data


def head_at(pts, t):
    """Линейная интерполяция, провалы (None) перешагиваются, за краями — ближайшее значение."""
    if not pts:
        return None
    valid = [(float(p[0]), float(p[1])) for p in pts if len(p) >= 2 and p[1] is not None]
    if not valid:
        return None
    if t <= valid[0][0]:
        return valid[0][1]
    if t >= valid[-1][0]:
        return valid[-1][1]
    lo, hi = 0, len(valid) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if valid[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    t0, v0 = valid[lo]
    t1, v1 = valid[hi]
    if t1 <= t0:
        return v0
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
