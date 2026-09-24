# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Акустический детект повторов (self-similarity по MFCC) + точка реза по энергии/zero-crossing.
Всё чистый DSP (numpy/librosa), без моделей и без транскрипта — не зависит от ослышек ASR.

find_repeats(y) -> список (lag, rep_start, rep_end, score) — reparandum (первый заход) на выброс.
snap_cut(y, t)  -> ближайшая «тихая» точка (минимум энергии + zero-crossing), чтобы не резать слова.
"""
from __future__ import annotations
from typing import Any
import numpy as np

SR = 16000
HOP = 160
FPS = SR / HOP


def _mfcc(y: Any) -> Any:
    import librosa
    m = librosa.feature.mfcc(y=y.astype(np.float32), sr=SR, n_mfcc=20, hop_length=HOP, n_fft=400)
    m = np.vstack([m, librosa.feature.delta(m)])
    return (m - m.mean(1, keepdims=True)) / (m.std(1, keepdims=True) + 1e-6)


def find_repeats(
    y: Any, min_rep: float = 0.35, thr: float = 0.55, min_run: float = 0.30
) -> list[tuple[float, float, float, float]]:
    """Найти повторяющиеся куски по диагональным полосам SSM. Вернуть непересекающиеся
    (lag_sec, start_sec, end_sec, score) — [start,end) = первый заход (reparandum) на выброс."""
    m = _mfcc(y)
    T = m.shape[1]
    if T < int(min_rep * FPS) * 2:
        return []
    x = m / (np.linalg.norm(m, axis=0, keepdims=True) + 1e-9)
    S = x.T @ x
    ksm = max(1, int(0.08 * FPS))
    cands = []
    for L in range(int(min_rep * FPS), int(T * 0.6)):
        d = np.array([S[i, i + L] for i in range(T - L)])
        d = np.convolve(d, np.ones(ksm) / ksm, "same")
        i = 0
        while i < len(d):
            if d[i] > thr:
                j = i
                while j < len(d) and d[j] > thr:
                    j += 1
                if (j - i) / FPS >= min_run:
                    cands.append((L / FPS, i / FPS, (i + L) / FPS, (j - i) / FPS * float(d[i:j].mean())))
                i = j
            else:
                i += 1
    cands.sort(key=lambda c: -c[3])
    out: list[tuple[float, float, float, float]] = []
    for c in cands:
        if all(not (c[1] < o[2] and o[1] < c[2]) for o in out):   # непересекающиеся
            out.append(c)
    return sorted(out, key=lambda c: c[1])


import re as _re

from core import align as _align  # общий гейт перечислений (см. align.is_enumeration)


def _norm(w: str) -> str:
    return _re.sub(r"[^\w]+", "", w.lower())


def repeated_phrase(text: str, min_span: int = 2, max_gap: int = 6) -> str:
    """Вернуть текст повторённой связки (для cut-log), '' если нет."""
    raw = [w for w in _re.split(r"\s+", text or "") if w]
    toks = [_norm(w) for w in raw]
    n = len(toks)
    for i in range(n):
        for L in range(min_span, (n - i) // 2 + 1):
            for m in range(L, L + max_gap + 1):
                if i + m + L > n:
                    break
                if toks[i:i + L] and toks[i:i + L] == toks[i + m:i + m + L]:
                    if _align.is_enumeration(toks, i, i + m):
                        continue                      # перечисление — не повтор
                    return " ".join(raw[i:i + L])
    return ""


def text_has_repeat(text: str, min_span: int = 2, max_gap: int = 6) -> bool:
    """Есть ли в ТЕКСТЕ повтор (связка из ≥2 слов, произнесённая дважды рядом)? Гейт для
    SSM: режем повтор в звуке только если транскрипт это подтверждает (меньше ложных).
    Перечисление («где он сделал вот это, а где он сделал другое») повтором НЕ считаем —
    иначе акустика срезала бы первую половину (см. align.is_enumeration)."""
    toks = [t for t in (_norm(w) for w in _re.split(r"\s+", text or "")) if t]
    n = len(toks)
    for i in range(n):
        for L in range(min_span, (n - i) // 2 + 1):
            for m in range(L, L + max_gap + 1):
                if i + m + L > n:
                    break
                if toks[i:i + L] == toks[i + m:i + m + L]:
                    if _align.is_enumeration(toks, i, i + m):
                        continue
                    return True
    return False


def snap_cut(y: Any, t_sec: float, win: float = 0.15, sil_ratio: float = 0.30) -> tuple[float, bool]:
    """Сдвинуть точку реза к минимуму энергии в окне ±win + zero-crossing. Вернуть
    (time, ok): ok=False если даже минимум громче sil_ratio·(медианной энергии клипа) —
    значит настоящей паузы тут нет (середина слова), резать НЕЛЬЗЯ."""
    n = len(y)
    k = max(1, int(0.01 * SR))
    env = np.convolve(np.abs(y), np.ones(k) / k, "same")
    med = np.median(env) + 1e-9
    c = int(t_sec * SR); w = int(win * SR)
    a, b = max(0, c - w), min(n, c + w)
    if b - a < 10:
        return t_sec, False
    loc = a + int(np.argmin(env[a:b]))
    ok = env[loc] < sil_ratio * med                       # реально тихо?
    z0, z1 = max(0, loc - int(0.02 * SR)), min(n - 1, loc + int(0.02 * SR))
    t = loc / SR
    for i in range(loc, z1):
        if y[i] * y[i + 1] <= 0:
            t = i / SR; break
    else:
        for i in range(loc, z0, -1):
            if y[i] * y[i - 1] <= 0:
                t = i / SR; break
    return t, ok


def _zc(y: Any, t: float) -> float:
    i = int(t * SR); n = len(y)
    for d in range(int(0.015 * SR)):
        for j in (i + d, i - d):
            if 0 < j < n - 1 and y[j] * y[j + 1] <= 0:
                return j / SR
    return t


def breath_cut_ranges(
    y: Any, off: float = 0.0, min_gap: float = 0.40, keep_pad: float = 0.12, sil_ratio: float = 0.45
) -> list[tuple[float, float]]:
    """Вырезать вздохи/паузы ВНУТРИ интервала: низко-энергетические участки длиннее
    min_gap. Режем середину, оставляя keep_pad у речи (не клиппит), с zero-crossing."""
    n = len(y)
    k = max(1, int(0.02 * SR))
    env = np.convolve(np.abs(y), np.ones(k) / k, "same")
    thr = sil_ratio * (np.median(env) + 1e-9)
    sil = env < thr
    out, i = [], 0
    while i < n:
        if sil[i]:
            j = i
            while j < n and sil[j]:
                j += 1
            if (j - i) / SR > min_gap:
                a = _zc(y, i / SR + keep_pad); b = _zc(y, j / SR - keep_pad)
                if b - a > 0.06:
                    out.append((a + off, b + off))
            i = j
        else:
            i += 1
    return out


def repeat_cut_ranges(y: Any, text: str = "", off: float = 0.0) -> list[tuple[float, float]]:
    """Диапазоны на выброс (абс. секунды, +off): режем повтор ТОЛЬКО если (1) текст его
    подтверждает и (2) обе точки реза попадают в реальную тишину (иначе не режем — лучше
    оставить повтор, чем разрезать слово)."""
    if text and not text_has_repeat(text):
        return []
    out = []
    for lag, rs, re, sc in find_repeats(y):
        a, aok = snap_cut(y, rs)
        b, bok = snap_cut(y, re)
        if aok and bok and b - a > 0.08:
            out.append((a + off, b + off))
    return out
