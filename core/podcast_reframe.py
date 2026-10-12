# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Перевод куска подкаста в рилс 9:16: рамка следует за говорящим.

Откуда задача (владелец, 2026-10-10): «трекинг отследил, где человек в камере, а код
поставил по центру». Здесь чистая часть плана и ffmpeg-разбор: склейки по резам,
ракурсы (камера стоит — один ракурс), головы, рамка 9:16 во всю высоту кадра и
кусочно-постоянный план X. Сборка файла ролика — отдельный шаг.

Головы берутся из разбора камер (`core/podcast_cams.py`, один раз на источник), а не из
RVM по каждому куску: перевод рилсов RVM не трогает. Здесь RVM не вызывается вовсе.

Времена во всех функциях — секунды; `shots` и `plan_crops` работают с отрезками
`(t0, t1)`. `plan_crops` отдаёт время ОТНОСИТЕЛЬНО начала куска (0 = начало куска).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import Counter
from typing import Any, Callable, Sequence

import numpy as np

from core import encoders, fileio, headtrack, podcast_ai, roto
from core.app_meta import wrap_emit
from core.draftrender import _run_ff
from core.umsg import ReelsiError, umsg
from core.xml2ae.parse import Cancelled, raise_if_cancelled

THUMB_W, THUMB_H = 64, 36
FACE_W = 1920           # кадры для рта — полное разрешение: в общем плане рот 5–6 px на 432 px
MOUTH_FPS = 10          # кадры для рта — 10 к/с (по спеке)
FACE_H = 1.3            # окно лица: от макушки вниз на 1.3 ширины головы
OUT_W, OUT_H = 1080, 1920
_PTS_RE = re.compile(r"pts_time:\s*([-+]?\d+(?:\.\d+)?)")


# Порог склейки по `scene`. Замер 2026-10-10 на подкасте владельца (short8.mkv, 4K AV1):
# настоящие склейки между ведущим и гостем — 0.221–0.239 (оба на тёмном фоне), шум — не
# выше 0.017 (медиана 0.0007). При 0.3 ВСЕ склейки пропадали, подкаст считался одной
# камерой. 0.1 — середина между шумом и склейками с запасом.
SCENE_THR = 0.1


def scene_cmd(src: str, a: float, b: float, thr: float = SCENE_THR) -> list[str]:
    """Команда ffmpeg: времена резов в [a, b) через select по scene, вывод в showinfo (stderr)."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
            "-vf", f"select='gt(scene,{thr})',showinfo", "-an", "-f", "null", "-"]


def parse_scene_times(stderr_text: str, a: float) -> list[float]:
    """Резы из вывода showinfo. При -ss до -i ffmpeg отсчитывает pts с нуля от a,
    поэтому абсолютное время = a + pts_time (зафиксировано тестом на синтетике)."""
    return [round(a + float(m.group(1)), 3) for m in _PTS_RE.finditer(stderr_text or "")]


def scene_cuts(src: str, a: float, b: float, thr: float = SCENE_THR,
               cancel: Callable[[], bool] | None = None) -> list[float]:
    """Абсолютные времена резов в [a, b): запуск scene_cmd через _run_ff."""
    res = _run_ff(scene_cmd(src, a, b, thr), cancel=cancel)
    if res is None:
        raise Cancelled()
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg не разобрал резы: {res.stderr[-300:]}")
    return parse_scene_times(res.stderr, a)


def shots(cuts: Sequence[float], a: float, b: float, min_len: float = 0.4) -> list[tuple[float, float]]:
    """Склейки куска [a, b) по временам резов. Склейка короче min_len сливается с ПРЕДЫДУЩЕЙ
    (первая — со следующей). Резы вне (a, b) игнорируются."""
    inside = sorted({float(c) for c in cuts if a < float(c) < b})
    pts = [float(a)] + inside + [float(b)]
    segs = [[x0, x1] for x0, x1 in zip(pts, pts[1:]) if x1 - x0 > 1e-9]
    if len(segs) >= 2 and segs[0][1] - segs[0][0] < min_len:
        segs[1][0] = segs[0][0]
        del segs[0]
    out: list[list[float]] = []
    for s in segs:
        if out and s[1] - s[0] < min_len:
            out[-1][1] = s[1]
        else:
            out.append(s)
    return [(x0, x1) for x0, x1 in out]


def thumb_cmd(src: str, t: float) -> list[str]:
    """Команда ffmpeg: один кадр в момент t, 64x36, серый, сырой вывод в stdout."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", src,
            "-frames:v", "1", "-vf", f"scale={THUMB_W}:{THUMB_H},format=gray",
            "-f", "rawvideo", "-"]


def thumbs(src: str, times: Sequence[float], cancel: Callable[[], bool] | None = None) -> list[np.ndarray]:
    """Кадр 64x36 серым в каждый момент times (обычно середина склейки)."""
    out: list[np.ndarray] = []
    for t in times:
        if cancel and cancel():
            raise Cancelled()
        p = subprocess.run(thumb_cmd(src, float(t)), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=60)
        if p.returncode != 0 or len(p.stdout) != THUMB_W * THUMB_H:
            raise RuntimeError(f"ffmpeg не отдал кадр {float(t):.3f} с: {src}")
        out.append(np.frombuffer(p.stdout, np.uint8).reshape(THUMB_H, THUMB_W).copy())
    return out


def group_angles(thumb_list: Sequence[Any], thr: float = 18.0) -> list[int]:
    """Номер ракурса на каждую склейку. Склейка входит в первый ракурс, у ПЕРВОЙ склейки
    которого средний модуль разности (0–255) меньше thr; иначе открывается новый ракурс.
    Номера идут по порядку появления с 0."""
    reps: list[np.ndarray] = []
    out: list[int] = []
    for th in thumb_list:
        g = np.asarray(th, dtype=float)
        for n, rep in enumerate(reps):
            if float(np.mean(np.abs(g - rep))) < thr:
                out.append(n)
                break
        else:
            reps.append(g)
            out.append(len(reps) - 1)
    return out


def reassign_nearest(thumb_list: Sequence[Any], angles: Sequence[int], rounds: int = 2) -> list[int]:
    """Переназначить склейки к БЛИЖАЙШЕМУ ракурсу по опорным миниатюрам (П11).

    group_angles отдаёт склейку первому ракурсу, у опоры которого разность под порогом:
    общий план прилипает к ракурсу гостя, хотя к своему ракурсу ближе вдвое (замер владельца
    2026-10-11: 17.7 при пороге 18 против 7.7). Опора ракурса — медоид: член с минимальной
    суммой расстояний до остальных членов (для 1–2 членов — первый). Каждая склейка уходит к
    ракурсу с ближайшей опорой; так повторяем `rounds` раз, опоры пересчитываются. Пустые
    ракурсы убираются, номера — по первому появлению, как у group_angles."""
    n = len(angles)
    if n == 0:
        return []
    X = np.stack([np.asarray(t, dtype=np.float32).reshape(-1) for t in thumb_list])
    cur = [int(a) for a in angles]
    for _ in range(max(1, rounds)):
        groups: dict[int, list[int]] = {}
        for i, a in enumerate(cur):
            groups.setdefault(a, []).append(i)
        keys = sorted(groups)
        medoids: list[int] = []
        for k in keys:
            g = groups[k]
            if len(g) <= 2:
                medoids.append(g[0])
                continue
            sums = [float(np.abs(X[g] - X[i]).mean(axis=1).sum()) for i in g]
            medoids.append(g[int(np.argmin(sums))])
        M = X[medoids]
        cur = [keys[int(np.argmin(np.abs(M - X[i]).mean(axis=1)))] for i in range(n)]
    remap: dict[int, int] = {}
    out: list[int] = []
    for a in cur:
        if a not in remap:
            remap[a] = len(remap)
        out.append(remap[a])
    return out


def heads_of_points(pts: Sequence[Any]) -> list[list[float]]:
    """Головы по точкам трека: [[hx, top, hw], …] слева направо. Точки без голов не считаются.

    Число голов — модальное (при равной частоте — больше: две головы в ничьей — не одна).
    Позиция i-й головы — медиана (hx, top, hw) по точкам с этим числом голов. hw есть,
    только если он у всех этих точек (старый трек без третьего числа даёт двухэлементные головы).
    Нет голов — []."""
    hs = [p[2] for p in pts if len(p) >= 3 and p[2]]
    if not hs:
        return []
    counts = Counter(len(h) for h in hs)
    n = max(counts, key=lambda k: (counts[k], k))
    rows = [h for h in hs if len(h) == n]
    out: list[list[float]] = []
    for i in range(n):
        head = [round(float(np.median([h[i][0] for h in rows])), 4),
                round(float(np.median([h[i][1] for h in rows])), 4)]
        hws = [h[i][2] for h in rows if len(h[i]) >= 3]
        if len(hws) == len(rows):
            head.append(round(float(np.median(hws)), 4))
        out.append(head)
    return out


def crop_width(src_w: int, src_h: int) -> int:
    """Ширина окна 9:16 во всю высоту кадра; чётная, не шире кадра."""
    cw = min(int(round(src_h * 9 / 16)), src_w)
    return cw - cw % 2


def crop_x(hx: float | None, src_w: int, src_h: int) -> int:
    """Левый край окна 9:16 во всю высоту кадра по центру hx (доля ширины; None — центр).
    Ширина окна — чётная; край упирается в [0, src_w - cw] и тоже чётный."""
    cw = crop_width(src_w, src_h)
    c = 0.5 if hx is None else float(hx)
    x = int(round(c * src_w - cw / 2))
    x = max(0, min(x, src_w - cw))
    return x - x % 2


def plan_crops(shot_list: Sequence[tuple[float, float]], angles: Sequence[int],
               heads_by_shot: Sequence[Sequence[float]],
               speakers: Sequence[tuple[float, float, int]] | None = None,
               *, src_w: int, src_h: int) -> list[dict[str, Any]]:
    """Кусочно-постоянный план рамки [{"t0", "t1", "x", "angle", "head"}] по склейкам.

    shot_list — склейки куска в относительном времени, angles — ракурс каждой (только
    для отчёта), heads_by_shot[k] — позиции hx голов склейки k слева направо.
    Одна голова — её X; нет голов — центр; две и больше: при speakers (t0, t1, индекс
    головы слева направо) склейка дробится по говорящему, не покрытые куски — между
    крайними головами; без speakers — между крайними головами. Соседние части с одинаковым
    X склеиваются (сохраняются ракурс и голова первой из них)."""
    pieces: list[list[Any]] = []
    for k, ((s0, s1), ang) in enumerate(zip(shot_list, angles)):
        heads = list(heads_by_shot[k] or []) if k < len(heads_by_shot) else []
        mid_x = crop_x((heads[0] + heads[-1]) / 2, src_w, src_h) if len(heads) >= 2 else None
        if len(heads) <= 1:
            x = crop_x(heads[0] if heads else None, src_w, src_h)
            pieces.append([s0, s1, x, ang, 0 if heads else None])
            continue
        if speakers is None:
            pieces.append([s0, s1, mid_x, ang, None])
            continue
        marks = {s0, s1}
        for sp in speakers:
            for t in (sp[0], sp[1]):
                marks.add(min(max(t, s0), s1))
        cuts = sorted(marks)
        for u0, u1 in zip(cuts, cuts[1:]):
            if u1 - u0 <= 1e-9:
                continue
            um = (u0 + u1) / 2
            who = next((sp[2] for sp in speakers if sp[0] <= um < sp[1]), None)
            if who is not None and 0 <= who < len(heads):
                pieces.append([u0, u1, crop_x(heads[who], src_w, src_h), ang, who])
            else:
                pieces.append([u0, u1, mid_x, ang, None])

    out: list[dict[str, Any]] = []
    for t0, t1, x, ang, head in pieces:
        if out and out[-1]["x"] == x:
            out[-1]["t1"] = round(float(t1), 3)
        else:
            out.append({"t0": round(float(t0), 3), "t1": round(float(t1), 3),
                        "x": x, "angle": ang, "head": head})
    return out


# ---------- кто говорит: движение рта ----------

def face_box(hx: float, top: float, hw: float, W: int, H: int) -> tuple[int, int, int, int]:
    """Окно лица в пикселях полного кадра W×H: (x0, x1, y0, y1).

    По X — центр hx·W, ширина hw·W (ширина головы). По Y — от макушки top·H вниз на
    FACE_H·hw·W (лицо ниже макушки примерно на полторы ширины головы). Край кадра обрезает окно."""
    half = float(hw) * W / 2.0
    cx = float(hx) * W
    x0 = max(0, min(int(round(cx - half)), W))
    x1 = max(0, min(int(round(cx + half)), W))
    y0 = max(0, min(int(round(float(top) * H)), H))
    y1 = max(y0, min(H, int(round(y0 + FACE_H * float(hw) * W))))
    return x0, x1, y0, y1


def mouth_activity(faces: Sequence[Sequence[np.ndarray]]) -> list[float]:
    """Энергия «рта» на голову по её окнам лица (кадры окна — из face_crops).

    Среднее |Δ| между соседними кадрами в НИЖНЕЙ трети окна минус то же в ВЕРХНЕЙ, не меньше 0.
    Верхняя треть — лоб и глаза, нижняя — рот и подбородок. Движение всей головы (наклон,
    поворот) даёт одинаковую разность в обеих третях и вычитается. Меньше двух кадров — 0."""
    res: list[float] = []
    for frames in faces:
        if len(frames) < 2:
            res.append(0.0)
            continue
        # int16, чтобы разность не ушла в uint8 (кадры — серые, 0..255).
        win = np.stack([np.asarray(f) for f in frames]).astype(np.int16)
        hh = win.shape[1]
        t = hh // 3
        if t < 1 or win.size == 0:
            res.append(0.0)
            continue
        d = np.abs(np.diff(win, axis=0))
        energy = float(d[:, hh - t:, :].mean()) - float(d[:, :t, :].mean())
        res.append(max(0.0, energy))
    return res


def _has_words(words: Sequence[dict[str, Any]], t0: float, t1: float) -> bool:
    return any(float(w["start"]) < t1 and float(w["end"]) > t0 for w in words)


def speakers_for_shot(t0: float, t1: float,
                      frames_by_window: Sequence[tuple[float, float, Sequence[Sequence[np.ndarray]]]],
                      heads: Sequence[Sequence[float]], words: Sequence[dict[str, Any]],
                      hold: float = 1.5, margin: float = 0.15) -> list[tuple[float, float, int]]:
    """Говорящий по окнам склейки [t0, t1) — индекс головы слева направо.

    frames_by_window — (wt0, wt1, окна лиц) окон по 1 с: окна лиц[i] — кадры головы i за окно
    (как у face_crops); времена — относительные куска, как и слова `words` (start/end).
    На окно — mouth_activity, говорящий — argmax. Окно
    решает только при словах внутри: без слов держим прежнего. Перевес лидера над вторым
    меньше margin (доля от лидера) — тоже держим. Смена не чаще раза в hold с (от
    последней смены). Первое окно без решения — голова с максимумом суммарной энергии
    по склейке. Соседние окна с одним говорящим склеиваются; результат покрывает [t0, t1)
    без дыр."""
    n = len(heads)
    if n <= 1 or not frames_by_window:
        return [(float(t0), float(t1), 0)]
    energies = [mouth_activity(faces) for (_, _, faces) in frames_by_window]
    total = [sum(e[i] for e in energies) for i in range(n)]
    default = max(range(n), key=lambda i: (total[i], -i))

    owners: list[int] = []
    cur: int | None = None
    since = 0.0
    for (wt0, wt1, _), e in zip(frames_by_window, energies):
        cand: int | None = None
        if _has_words(words, wt0, wt1):
            order = sorted(range(n), key=lambda i: -e[i])
            lead, second = order[0], order[1]
            if e[lead] > 0 and e[lead] - e[second] >= margin * e[lead]:
                cand = lead
        if cur is None:
            cur = cand if cand is not None else default
            since = float(wt0)
        elif cand is not None and cand != cur and float(wt0) - since >= hold:
            cur, since = cand, float(wt0)
        owners.append(cur)

    segs: list[list[Any]] = []
    for (wt0, wt1, _), who in zip(frames_by_window, owners):
        if segs and segs[-1][2] == who:
            segs[-1][1] = float(wt1)
        else:
            start = segs[-1][1] if segs else float(t0)
            segs.append([start, float(wt1), who])
    segs[0][0] = float(t0)
    segs[-1][1] = float(t1)
    return [(float(a_), float(b_), int(c_)) for a_, b_, c_ in segs]


# ---------- ffmpeg: кадры рта, файл команд, запекание ----------

FACE_STALL_SEC = 120     # ffmpeg кадров лиц молчит дольше — снимаем (зависший вешал перевод в рилсы)
FACE_CHUNK = 8           # кадров в пачке: 8 × 2 МБ на кадр 1920×1080 — память, а не скорость


def face_crops(src: str, a: float, b: float, heads: Sequence[Sequence[float]],
               fps: int = MOUTH_FPS, width: int = FACE_W,
               cancel: Callable[[], bool] | None = None,
               stall_sec: float = FACE_STALL_SEC) -> list[list[np.ndarray]]:
    """Окна лиц каждой головы по кадрам куска [a, b): список на голову, в нём — кадр за кадром.

    Кадры читаются ПОТОКОМ в полном разрешении (width, по умолчанию 1920), серым. Из каждого
    кадра сразу вырезаются окна голов (face_box), сам кадр не хранится: 1920×1080 — 2 МБ на
    кадр, минута при 10 к/с — 1.2 ГБ. Окно — копия, а не вид на кадр: вид держал бы весь кадр.
    Высота задаётся явно из размера исходника (`scale=w:h`), а не `-2`, иначе reshape разъедется.

    Сторож: если `cancel()` стал True (кнопка «Стоп») или ffmpeg не отдал ни одного кадра дольше
    `stall_sec`, ffmpeg снимается. Чтение трубы тогда кончается, и исключение бросает этот вызов:
    `Cancelled` при отмене, RuntimeError при простое. Раньше таймаута и отмены не было совсем.
    """
    if not heads:
        return []
    w_src, h_src, _, _ = roto._probe(src)
    H = 2 * int(round(h_src * width / (2.0 * w_src)))
    boxes = [face_box(float(h[0]), float(h[1]), headtrack.head_hw(h), width, H) for h in heads]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
           "-i", src, "-vf", f"fps={fps},scale={width}:{H},format=gray",
           "-f", "rawvideo", "-"]
    frame_bytes = width * H
    out: list[list[np.ndarray]] = [[] for _ in heads]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    last = [time.time()]          # время последнего полученного пачки кадров
    stalled = [False]
    done = threading.Event()

    def _watch() -> None:
        # Отдельный поток: главный сидит в чтении трубы и сам не заметит ни простоя, ни отмены.
        while not done.wait(0.5):
            if cancel is not None and cancel():
                p.kill()
                return
            if time.time() - last[0] >= stall_sec:
                stalled[0] = True
                p.kill()
                return

    watch = threading.Thread(target=_watch, daemon=True)
    watch.start()
    try:
        for batch in roto.iter_frames(p.stdout, frame_bytes, FACE_CHUNK):
            last[0] = time.time()
            raise_if_cancelled(cancel)
            for raw in batch:
                frame = np.frombuffer(raw, np.uint8).reshape(H, width)
                for k, (x0, x1, y0, y1) in enumerate(boxes):
                    out[k].append(frame[y0:y1, x0:x1].copy())
    finally:
        # Декодер не нужен ни при отмене, ни при сбое: гасим его, не дожидаясь конца потока.
        done.set()
        if p.poll() is None:
            p.kill()
        p.wait()
        if p.stdout:
            p.stdout.close()
    err = p.stderr.read() if p.stderr else b""
    if p.stderr:
        p.stderr.close()
    raise_if_cancelled(cancel)
    if stalled[0]:
        raise RuntimeError(f"ffmpeg молчит больше {int(stall_sec)} с — кадры лиц не читаются, "
                           "снят сторожем простоя")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg не отдал кадры лиц: {err[-300:]!r}")
    return out


def sendcmd_text(plan: Sequence[dict[str, Any]]) -> str:
    """Файл команд sendcmd: `t crop x X;` для каждой смены плана, кроме первой (её X
    задаётся в самом фильтре). Времена — относительные куска."""
    return "".join(f"{p['t0']:.3f} crop x {int(p['x'])};\n" for p in plan[1:])


def ff_filter_path(p: str) -> str:
    """Путь для опции фильтра ffmpeg (`sendcmd=f=…`): обратные слеши — в прямые, двоеточие
    диска экранируется (иначе фильтрграф примет его за разделитель опций)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def bake_cmd(src: str, a: float, b: float, plan: Sequence[dict[str, Any]], src_w: int, src_h: int,
             out: str, cmd_path: str, codec_args_list: Sequence[str]) -> list[str]:
    """Один проход ffmpeg: кусок [a, b) → кадр 9:16 1080×1920, звук AAC 320k.

    Рамка — crop с первым X плана; смены X — sendcmd из файла cmd_path. Одна смена —
    sendcmd не нужен. Звук явно кодируется в AAC: opus из mkv Premiere не берёт."""
    cw = crop_width(src_w, src_h)
    x0 = int(plan[0]["x"]) if plan else crop_x(None, src_w, src_h)
    crop = f"crop=w={cw}:h=ih:x={x0}:y=0"
    scale = f"scale={OUT_W}:{OUT_H}:flags=lanczos,format=yuv420p[v]"
    if len(plan) > 1:
        fc = f"[0:v]sendcmd=f='{ff_filter_path(cmd_path)}',{crop},{scale}"
    else:
        fc = f"[0:v]{crop},{scale}"
    return ["ffmpeg", "-y", "-hide_banner", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
            "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0", *codec_args_list,
            "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", out]


# ---------- сборка рилса из куска ----------


def _shot_speakers(src: str, s0: float, s1: float, a: float, heads: Sequence[Sequence[float]],
                   words_rel: Sequence[dict[str, Any]],
                   cancel: Callable[[], bool] | None = None) -> list[tuple[float, float, int]]:
    """Говорящие на одной склейке: s0, s1 — абсолютные, слова и результат — относительные
    куска (относительно a). Окна лиц — одним проходом по склейке (face_crops), окна по 1 с."""
    faces = face_crops(src, s0, s1, heads, cancel=cancel)
    rs0, rs1 = s0 - a, s1 - a
    wins: list[tuple[float, float, list[list[np.ndarray]]]] = []
    j = 0
    while rs0 + j < rs1 - 1e-9:
        wt0 = rs0 + j
        wt1 = min(wt0 + 1.0, rs1)
        k0 = int(round((wt0 - rs0) * MOUTH_FPS))
        k1 = int(round((wt1 - rs0) * MOUTH_FPS))
        wins.append((wt0, wt1, [head_frames[k0:k1] for head_frames in faces]))
        j += 1
    return speakers_for_shot(rs0, rs1, wins, heads, words_rel)


def _shots_of_cams(cams: dict[str, Any], a: float, b: float) -> list[dict[str, Any]]:
    """Склейки куска [a, b) из глобального разбора камер (П11).

    Каждая глобальная склейка, обрезанная по [a, b); кусок, начавшийся посреди склейки, берёт
    её ракурс. Головы склейки: `shot_heads[i]`, если разбор пропустил её через RVM (ракурс
    непонятный), иначе головы ракурса `angle_info[angle]`. RVM здесь не вызывается."""
    info = cams.get("angle_info") or {}
    own = cams.get("shot_heads") or {}
    out: list[dict[str, Any]] = []
    for i, ((g0, g1), ang) in enumerate(zip(cams["shots"], cams["angles"])):
        s0, s1 = max(float(g0), a), min(float(g1), b)
        if s1 - s0 <= 1e-9:
            continue
        if str(i) in own:
            src_tag, heads = "shot", own[str(i)]
        else:
            src_tag, heads = "angle", (info.get(str(ang)) or {}).get("heads") or []
        # Ширину головы (третье число) сохраняем; старые головы без неё остаются двухэлементными.
        out.append({"s0": s0, "s1": s1, "angle": int(ang), "heads_from": src_tag,
                    "heads": [[float(v) for v in h[:3]] for h in heads]})
    return out


def reel_path(reels_dir: str, slug: str, index: int, own: str = "") -> str:
    """Путь рилса `<slug>_<NN>.mp4` (NN — номер куска по времени с 01) в папке рилсов.

    Занятое имя — следующий свободный номер, НЕ перезапись чужого файла. «Занято» — есть mp4
    или `.reframe.json` с этим именем. Свой рилс этого куска (`own` — его `reel` из проекта)
    занятым не считается: перевод того же куска переписывает свой файл, а не плодит копию.
    ПОЧЕМУ так: папка рилсов — папка камеры 1 спикера, там лежит всё, что он снимал; тихая
    перезапись чужого `<slug>_01.mp4` уничтожила бы файл, которого подкаст не создавал."""
    own_norm = os.path.normcase(os.path.abspath(own)) if own else ""
    n = index + 1
    while True:
        base = os.path.join(reels_dir, f"{slug}_{n:02d}")
        out = base + ".mp4"
        if own_norm and os.path.normcase(os.path.abspath(out)) == own_norm:
            return out
        if not os.path.exists(out) and not os.path.exists(base + ".reframe.json"):
            return out
        n += 1


def convert_segment(proj_dir: str, proj: dict[str, Any], seg: dict[str, Any], index: int,
                    emit: Callable[..., Any] | None = None,
                    cancel: Callable[[], bool] | None = None,
                    reels_dir: str | None = None) -> str:
    """Кусок `seg` подкаста → вертикальный рилс `<reels_dir>/<slug>_<NN>.mp4`.

    reels_dir — папка рилсов (папка камеры 1 спикера); по умолчанию `<подкаст>/reels`.
    Склейки и головы берутся из разбора камер (`podcast_cams`, один раз на источник — RVM
    здесь не работает) → говорящие по рту (склейка с 2+ головами) → план рамки → один проход
    ffmpeg. Рядом: `.<имя рилса>.crop.txt` (sendcmd) и `<имя рилса>.reframe.json` (склейки с
    ракурсом и источником голов `heads_from`, говорящие, план; времена в json — относительные
    куска). Пишет во временный `*.part.mp4` и переименовывает. Возвращает путь; заполняет seg["reel"]."""
    # Импорт здесь, а не наверху: podcast_cams сам берёт ffmpeg-разбор отсюда (круговая зависимость).
    from core import podcast_cams

    emit = wrap_emit(emit)
    src = proj["source"]
    a, b = float(seg["start"]), float(seg["end"])
    slug = proj.get("slug") or "podcast"

    raise_if_cancelled(cancel)
    src_w, src_h, _, _ = roto._probe(src)
    cams = podcast_cams.load(proj_dir, proj)
    if cams is None:
        cams = podcast_cams.analyze(proj_dir, proj, emit=emit, cancel=cancel)
    pieces = _shots_of_cams(cams, a, b)
    sh = [(p["s0"], p["s1"]) for p in pieces]
    angles = [p["angle"] for p in pieces]
    heads_xy = [p["heads"] for p in pieces]
    heads_by_shot = [[hp[0] for hp in hl] for hl in heads_xy]
    emit("склейки куска: {n}, ракурсов: {k}", n=len(sh), k=len(set(angles)))

    words = [w for w in podcast_ai.load_words(proj_dir)
             if float(w["end"]) > a and float(w["start"]) < b]
    words_rel = [{"start": float(w["start"]) - a, "end": float(w["end"]) - a} for w in words]
    speakers: list[tuple[float, float, int]] = []
    multi = [k for k, hl in enumerate(heads_xy) if len(hl) >= 2]
    for n_done, k in enumerate(multi, 1):
        raise_if_cancelled(cancel)
        emit("говорящий: склейка {i} из {n}", i=n_done, n=len(multi))
        s0, s1 = sh[k]
        speakers.extend(_shot_speakers(src, s0, s1, a, heads_xy[k], words_rel, cancel))

    rel_shots = [(s0 - a, s1 - a) for s0, s1 in sh]
    plan = plan_crops(rel_shots, angles, heads_by_shot, speakers=speakers,
                      src_w=src_w, src_h=src_h)
    emit("рамка: {n} смен", n=len(plan))

    reels = reels_dir or os.path.join(proj_dir, "reels")
    os.makedirs(reels, exist_ok=True)
    out = reel_path(reels, slug, index, own=str(seg.get("reel") or ""))
    base = out[:-len(".mp4")]
    part = base + ".part.mp4"
    js_path = base + ".reframe.json"
    # sendcmd-файл с именем рилса: папка камеры 1 общая для всех подкастов и клипов, `.01.crop.txt`
    # там затёр бы чужой план рамки
    cmd_path = os.path.join(reels, f".{os.path.basename(base)}.crop.txt")
    with open(cmd_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sendcmd_text(plan))

    choice = encoders.pick("master")
    codec = encoders.codec_name(choice.family, "master")
    # Теги цвета без `-vf`: кадры уже YUV, фильтр RGB→YUV здесь не нужен.
    tags = [*encoders.COLOR_TAGS, *encoders.VUI_ARGS.get(codec, []), *encoders.VUI_BSF.get(codec, [])]
    enc = [*encoders.codec_args(codec, "master"), *tags]
    cmd = bake_cmd(src, a, b, plan, src_w, src_h, part, cmd_path, enc)

    emit("кодирую…")
    try:
        res = _run_ff(cmd, cancel=cancel)
    except BaseException:
        fileio.remove_quiet(part)
        raise
    if res is None:
        fileio.remove_quiet(part)
        raise Cancelled()
    if res.returncode != 0:
        fileio.remove_quiet(part)
        raise ReelsiError(umsg("reel_bake_failed", "не удалось собрать рилс «{name}»: {why}",
                               name=os.path.basename(out), why=(res.stderr or "")[-300:]))
    fileio.move_file(part, out)

    fileio.atomic_json_dump(js_path, {
        "v": 1, "src": src, "a": a, "b": b, "src_w": src_w, "src_h": src_h,
        "shots": [{"t0": round(p["s0"] - a, 3), "t1": round(p["s1"] - a, 3),
                   "angle": p["angle"], "heads_from": p["heads_from"],
                   "heads": p["heads"]} for p in pieces],
        "speakers": [[round(t0, 3), round(t1, 3), int(w)] for t0, t1, w in speakers],
        "plan": plan,
    })
    seg["reel"] = out
    return out
