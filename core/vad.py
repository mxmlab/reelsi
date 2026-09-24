# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Energy-based voice activity detection -> speech intervals (seconds)."""
from __future__ import annotations
from typing import Any
import numpy as np
from scipy.io import wavfile


def _load(wav_path: str) -> tuple[int, Any]:
    sr, x = wavfile.read(wav_path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return sr, x.astype(np.float32) / 32768.0


def speech_intervals(wav_path: str, frame: float = 0.025, hop: float = 0.010,
                     thresh_db: float = 18.0, min_silence: float = 0.30, min_speech: float = 0.20,
                     pad: float = 0.08) -> list[tuple[float, float]]:
    """Return list of (start, end) seconds where speech is present.
    thresh_db: how far above the noise floor counts as speech."""
    sr, x = _load(wav_path)
    fl = max(1, int(frame * sr)); hl = max(1, int(hop * sr))
    n = max(0, (len(x) - fl) // hl + 1)
    if n == 0:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(x, fl)[::hl]
    # einsum считает сумму квадратов ПО ОКНАМ, не материализуя массив: `frames ** 2`
    # внутри np.mean создавал реальный массив в frame/hop = 2.5 раза длиннее исходника
    # (часовая запись -> ~576 МБ одним аллоком во Flask-процессе рядом с Whisper).
    rms = np.sqrt(np.einsum("ij,ij->i", frames, frames) / float(fl) + 1e-10)
    db = 20 * np.log10(rms + 1e-10)
    floor = np.percentile(db, 20)            # noise floor estimate
    speech = db > (floor + thresh_db)

    # frame indices -> time, then merge/clean
    t = np.arange(len(speech)) * hop
    iv = []
    i = 0
    while i < len(speech):
        if speech[i]:
            j = i
            while j < len(speech) and speech[j]:
                j += 1
            iv.append([t[i], t[min(j, len(t) - 1)] + frame])
            i = j
        else:
            i += 1
    # merge gaps shorter than min_silence
    merged: list[list[float]] = []
    for s, e in iv:
        if merged and s - merged[-1][1] < min_silence:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    # drop too-short, then pad
    out = []
    dur = len(x) / sr
    for s, e in merged:
        if e - s < min_speech:
            continue
        out.append((max(0.0, s - pad), min(dur, e + pad)))
    # re-merge if padding caused overlap
    final: list[tuple[float, float]] = []
    for s, e in out:
        if final and s <= final[-1][1]:
            final[-1] = (final[-1][0], max(final[-1][1], e))
        else:
            final.append((s, e))
    return final
