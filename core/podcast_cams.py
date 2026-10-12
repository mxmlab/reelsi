# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Разбор камер подкаста — один раз на источник (П11), `<папка подкаста>/cams.json`.

Владелец, 2026-10-11: «Основная идея деления на сцены — не пропускать всё через RVM, а взять
кадр и по нему понять, куда ставить рамку на все склейки ракурса разом. На непонятных — отдельный
пропуск RVM». Камера стоит — рамка ракурса одна, поэтому RVM нужен на выборку, а не на каждый кадр.

Схема: склейки всего источника → миниатюры середин → ракурсы (с переназначением к ближайшему)
→ уверенный ракурс (3+ склейки и 20+ с) проверяем по трём выборкам: головы согласны — ракурс
уверенный, его склейки берут головы из `angle_info`. Непонятные (мелкий ракурс или выборки
разошлись) получают RVM по каждой своей склейке: `shot_heads`. RVM — два вызова `track` на
весь источник, не больше: один на выборки, второй (если нужен) на непонятные склейки.
"""
from __future__ import annotations

import bisect
import os
from typing import Any, Callable

import numpy as np

from core import fileio, headtrack, media, roto
from core import podcast_reframe as pr
from core.app_meta import wrap_emit
from core.applog import get_logger
from core.umsg import ReelsiError, umsg
from core.xml2ae.parse import raise_if_cancelled

log = get_logger(__name__)

CAMS_FILE = "cams.json"
V = 1
TRACK_FPS = 5                 # частота выборки RVM: как у трека головы кусков
CONF_MIN_SHOTS = 3            # уверенный ракурс: склеек не меньше трёх
CONF_MIN_DUR = 20.0           # и суммарно не меньше 20 с
SAMPLE_HALF = 0.5             # участок выборки — 1 с вокруг середины склейки
SHOT_EDGE = 0.05              # участок не лезет на края склейки
AGREE_FRAC = 0.25             # допуск согласия выборок: четверть ширины окна 9:16 (см. agree_tol)
HEAD_DTOP = 0.15              # голова — только если её макушка не ниже самой высокой в кадре на 0.15 высоты


def cams_path(proj_dir: str) -> str:
    return os.path.join(proj_dir, CAMS_FILE)


def agree_tol(src_w: int, src_h: int) -> float:
    """Допуск разброса hx головы по выборкам — доля ширины кадра.

    Замер 2026-10-11: гость в крупном плане — центр головы 0.58 / 0.60 / 0.64, человек чуть
    двигается, а прежний допуск 0.05 его отвергал. Окно 9:16 занимает ~0.316 ширины кадра,
    поэтому допуск — четверть окна: для 3840×2160 это 0.079."""
    return AGREE_FRAC * pr.crop_width(src_w, src_h) / src_w


def filter_heads(heads: list[list[float]], dtop: float = HEAD_DTOP) -> list[list[float]]:
    """Оставить головы, чья макушка top <= min(top) + dtop (доли высоты кадра).

    Замер 2026-10-11: стойки микрофонов дают «головы» с макушкой 0.61–0.63 высоты, у людей
    в общем плане 0.25–0.29; плечо гостя в крупном плане — «голова» с макушкой 0.44, у самого
    ведущего 0.07–0.13. Настоящая голова — самая высокая в кадре или почти вровень с ней."""
    if not heads:
        return []
    top_min = min(float(h[1]) for h in heads)
    return [h for h in heads if float(h[1]) <= top_min + dtop]


def load(proj_dir: str, proj: dict[str, Any]) -> dict[str, Any] | None:
    """Готовый cams.json, если он годен для текущего источника (путь, размер, mtime), иначе None."""
    src = proj.get("source")
    if not src:
        return None
    src = os.path.abspath(str(src))
    if not os.path.isfile(src):
        return None
    data = fileio.json_load_soft(cams_path(proj_dir))
    if not isinstance(data, dict) or data.get("v") != V:
        return None
    st = os.stat(src)
    if data.get("src") != src or data.get("size") != st.st_size \
            or abs(float(data.get("mtime") or 0.0) - st.st_mtime) >= 1e-4:
        return None
    return data


def _pachka(shot: tuple[float, float]) -> tuple[float, float] | None:
    """Участок RVM для склейки: 1 с вокруг середины, обрезанный внутрь склейки. Нет места — None."""
    s0, s1 = shot
    mid = (s0 + s1) / 2
    a = max(s0 + SHOT_EDGE, mid - SAMPLE_HALF)
    b = min(s1 - SHOT_EDGE, mid + SAMPLE_HALF)
    if b - a <= 1e-3:
        return None
    return (round(a, 3), round(b, 3))


def _track_pachki(src: str, jobs: list[tuple[tuple[float, float], int]],
                  emit: Callable[..., Any], cancel: Callable[[], bool] | None) -> dict[int, list[Any]]:
    """ОДИН вызов RVM на все участки `jobs` [(участок, номер склейки)] → точки по номеру склейки.

    Точка относится к участку, в который попадает её время; участки не перекрываются
    (между ними минимум 2 × SHOT_EDGE), поэтому граница толерантна только на кадр ffmpeg."""
    if not jobs:
        return {}
    ranges = [r for r, _ in jobs]
    owners = [i for _, i in jobs]
    raise_if_cancelled(cancel)
    tr = headtrack.track(src, ranges, emit=emit, cancel=cancel, fps=TRACK_FPS, heads=True)
    order = sorted(range(len(ranges)), key=lambda j: ranges[j][0])
    starts = [ranges[j][0] for j in order]
    out: dict[int, list[Any]] = {i: [] for i in owners}
    for p in tr.get("pts") or []:
        t = float(p[0])
        k = bisect.bisect_right(starts, t + 1e-3) - 1
        if k < 0:
            continue
        j = order[k]
        if t <= ranges[j][1] + 0.02:
            out[owners[j]].append(p)
    return out


def _heads_of(pts: list[Any]) -> list[list[float]]:
    """Головы пачки: в каждом кадре — только верхние макушки (filter_heads), потом модальное число.

    Фильтр — до подсчёта числа голов: иначе микрофон в каждом кадре делает «две головы» вместо
    одной и выборка ракурса не сходится (замер 2026-10-11)."""
    return pr.heads_of_points([[p[0], p[1], filter_heads(p[2])]
                               for p in pts if len(p) >= 3 and p[2]])


def _samples(idx: list[int]) -> list[int]:
    """Выборка ракурса: склейки на 25, 50 и 75 % по порядку — не первая и не последняя.

    Первая и последняя склейки выпуска — заставка и концовка (плашки, перебивки), по ним
    голова не берётся. При трёх склейках — все три (int(q·(n−1)) дал бы дубль)."""
    n = len(idx)
    if n <= 3:
        return list(idx)
    return [idx[int(q * (n - 1))] for q in (0.25, 0.5, 0.75)]


def _agree(heads_list: list[list[list[float]]], tol: float) -> tuple[bool, list[list[float]]]:
    """Выборки ракурса согласны: одно число голов и разброс hx каждой головы ≤ tol (agree_tol).
    Согласны — (True, медианы голов), нет — (False, [])."""
    counts = {len(h) for h in heads_list}
    if len(counts) != 1:
        return False, []
    n = counts.pop()
    heads: list[list[float]] = []
    for i in range(n):
        hxs = [h[i][0] for h in heads_list]
        tops = [h[i][1] for h in heads_list]
        if max(hxs) - min(hxs) > tol:
            return False, []
        head = [round(float(np.median(hxs)), 4), round(float(np.median(tops)), 4)]
        hws = [h[i][2] for h in heads_list if len(h[i]) >= 3]
        if len(hws) == len(heads_list):
            head.append(round(float(np.median(hws)), 4))
        heads.append(head)
    return True, heads


def analyze(proj_dir: str, proj: dict[str, Any], emit: Callable[..., Any] | None = None,
            cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Разбор камер источника: годный cams.json возвращается как есть (без RVM и ffmpeg),
    иначе считается заново и пишется атомарно. Вызывающий держит общий лок тяжёлых задач."""
    cached = load(proj_dir, proj)
    if cached is not None:
        emit = wrap_emit(emit)
        emit("разбор камер: взят готовый cams.json")
        return cached
    emit = wrap_emit(emit)
    src = os.path.abspath(str(proj["source"]))
    st = os.stat(src)
    src_w, src_h, _, _ = roto._probe(src)
    dur = float(proj.get("duration") or 0.0) or float(media.probe_duration(src) or 0.0)
    if dur <= 0:
        raise ReelsiError(umsg("podcast_no_duration", "Не узнать длину подкаста: {path}", path=src))

    raise_if_cancelled(cancel)
    sh = pr.shots(pr.scene_cuts(src, 0.0, dur, cancel=cancel), 0.0, dur)
    emit("склейки: {n}", n=len(sh))

    raise_if_cancelled(cancel)
    thumb_list = pr.thumbs(src, [(s0 + s1) / 2 for s0, s1 in sh], cancel=cancel)
    angles = pr.reassign_nearest(thumb_list, pr.group_angles(thumb_list))
    n_ang = max(angles) + 1 if angles else 0
    members = {k: [i for i, a in enumerate(angles) if a == k] for k in range(n_ang)}
    dur_of = {k: sum(sh[i][1] - sh[i][0] for i in idx) for k, idx in members.items()}

    # Выборка уверенного ракурса: склейки на 25, 50 и 75 % (при трёх — все).
    sample_of = {k: _samples(idx) for k, idx in members.items()
                 if len(idx) >= CONF_MIN_SHOTS and dur_of[k] >= CONF_MIN_DUR}
    jobs1 = []
    for k in sorted(sample_of):
        for i in sample_of[k]:
            rng = _pachka(sh[i])
            if rng is not None:
                jobs1.append((rng, i))
    got1 = _track_pachki(src, jobs1, emit, cancel)
    total_pts = sum(len(v) for v in got1.values())

    tol = agree_tol(src_w, src_h)
    info: dict[str, Any] = {}
    confident: set[int] = set()
    for k in range(n_ang):
        entry: dict[str, Any] = {"n_shots": len(members[k]), "dur": round(dur_of[k], 3),
                                 "confident": False, "heads": [], "samples": []}
        if k in sample_of:
            samples = [{"shot": i, "heads": _heads_of(got1.get(i, []))}
                       for i in sample_of[k]]
            entry["samples"] = [{"shot": s["shot"], "heads": s["heads"]} for s in samples]
            ok, heads = _agree([s["heads"] for s in samples], tol)
            if ok:
                entry["confident"] = True
                entry["heads"] = heads
                confident.add(k)
        info[str(k)] = entry

    # Непонятные: мелкий ракурс или выборки разошлись — RVM по каждой их склейке (один вызов).
    unsure = [k for k in range(n_ang) if k not in confident]
    jobs2 = []
    for k in unsure:
        for i in members[k]:
            rng = _pachka(sh[i])
            if rng is not None:
                jobs2.append((rng, i))
    got2 = _track_pachki(src, jobs2, emit, cancel)
    total_pts += sum(len(v) for v in got2.values())
    shot_heads = {str(i): _heads_of(got2.get(i, []))
                  for k in unsure for i in members[k]}

    emit("ракурсов: {k} (уверенных: {m})", k=n_ang, m=len(confident))
    emit("RVM: кадров {x}", x=total_pts)
    emit("непонятных склеек: {y}", y=sum(len(members[k]) for k in unsure))

    data: dict[str, Any] = {
        "v": V, "src": src, "size": st.st_size, "mtime": st.st_mtime,
        "src_w": src_w, "src_h": src_h, "scene_thr": pr.SCENE_THR,
        "shots": [[round(s0, 3), round(s1, 3)] for s0, s1 in sh],
        "angles": [int(a) for a in angles],
        "angle_info": info,
        "shot_heads": shot_heads,
    }
    # Битый cams.json откладываем, а не затираем (как .head.json): по нему видно, что было не так.
    bad = fileio.quarantine_unreadable(cams_path(proj_dir), valid=lambda d: isinstance(d, dict))
    if bad:
        log.warning("cams.json не прочитан — отложен в %s", bad)
    fileio.atomic_json_dump(cams_path(proj_dir), data)
    return data
