# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Download the audio track from a YouTube (or any yt-dlp-supported) URL."""
from __future__ import annotations
import os, sys, subprocess, random
from typing import Any, Callable
from core.app_meta import console_emit

AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".opus", ".flac", ".ogg")

# Скачивание трека идёт минуты, но yt-dlp умеет и зависнуть навсегда (сеть, чужой
# ответ, ретраи). Скачивание идёт СИНХРОННО внутри джоба нарезки — без таймаута
# зависший yt-dlp держал бы JOB до перезапуска сервера.
YTDLP_TIMEOUT = 1800


def random_track(outdir: str | None, emit: Callable[..., Any] = console_emit, seed: object = None) -> str | None:
    """Случайный (или детерминированный по seed) аудиофайл из папки скачанной музыки (или None, если пусто)."""
    if not outdir or not os.path.isdir(outdir):
        return None
    files = sorted([os.path.join(outdir, f) for f in os.listdir(outdir)
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT])
    if not files:
        emit("  в папке нет аудио: {dir}", dir=outdir)
        return None
    if seed is not None:
        import hashlib
        idx = int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16) % len(files)
        p = os.path.abspath(files[idx])
    else:
        p = os.path.abspath(random.choice(files))
    emit("  случайная музыка: {name}", name=os.path.basename(p))
    return p


def is_url(s: object) -> bool:
    return isinstance(s, str) and s.strip().lower().startswith(("http://", "https://"))


def download_audio(url: str, outdir: str, fmt: str = "m4a", emit: Callable[..., Any] = console_emit) -> str:
    """Download bestaudio → {outdir}/track_N.{fmt} (next free number, ASCII name).
    Returns the path; on failure raises with yt-dlp's reason."""
    os.makedirs(outdir, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(outdir, f"track_{n}.{fmt}")):
        n += 1
    stem = f"track_{n}"
    out_tmpl = os.path.join(outdir, stem + ".%(ext)s")
    emit("  скачиваю аудио с YouTube...")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", fmt,
             "--audio-quality", "0", "--no-playlist", "-o", out_tmpl, url],
            capture_output=True, timeout=YTDLP_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp не смог скачать: превышен таймаут (%d мин)"
                           % (YTDLP_TIMEOUT // 60))
    path = os.path.join(outdir, stem + "." + fmt)
    if r.returncode != 0 or not os.path.isfile(path):
        err = (r.stderr or b"").decode("utf-8", "replace")
        tail = " ".join(l for l in err.splitlines() if l.strip())[-400:]
        raise RuntimeError("yt-dlp не смог скачать: " + (tail or f"код {r.returncode}"))
    emit("  музыка: {name}", name=os.path.basename(path))
    return path


def resolve(music: str | None, outdir: str, emit: Callable[..., Any] = console_emit) -> Any:

    """music may be a URL (download) or an existing local path. Returns a path or None."""
    if not music:
        return None
    if is_url(music):
        return download_audio(music, outdir, emit=emit)
    if os.path.isfile(music):
        return os.path.abspath(music)
    raise FileNotFoundError(f"Музыка не найдена: {music}")
