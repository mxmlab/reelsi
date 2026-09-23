# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Длительность медиафайла: ОДНА проба ffprobe на все места.

Копий было пять — `draftrender._src_dur`, `insertlib._thumb_b64`,
`omni_review._dur`, `xmlbuild.probe_audio_dur`, `aicut.video.probe_media` — и
расходились они ровно там, где начинается ошибка: одни отдавали 0.0, и это
уезжало в арифметику как «длительность ноль» (кадр для vision брался из начала
ролика, прогресс сборки делился на ноль), другие падали ValueError-ом. Здесь
поведение одно на всех: `None` — «не прочли» (нет файла, нет ffprobe, завис,
битый контейнер), а как это показать, решает вызывающий.

Кэш по (путь, mtime, размер): ffprobe — отдельный процесс на вызов, а длительность
спрашивают в цикле (прогресс сборки черновика — на каждый кадр). Файл перезаписали
— сменился ключ, спросим заново.
"""
import os
import subprocess

# Проба читает заголовок контейнера, а не декодирует видео, так что 30 с — это
# «процесс завис», а не «большой файл». Без таймаута зависший ffprobe держал бы
# HTTP-запрос подбора камер и поток джоба навсегда.
PROBE_TIMEOUT = 30

_CACHE: dict[tuple[str, int, int], float] = {}


def probe_duration(path: str | os.PathLike[str]) -> float | None:
    """Длительность медиа в секундах; None — не прочли (единое поведение при ошибке).

    Кэшируется только УДАЧНАЯ проба: файл могли ещё писать, и запомнить «нет
    длительности» на неизменённом ключе значило бы потерять её навсегда."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.abspath(path), int(st.st_mtime), st.st_size)
    if key in _CACHE:
        return _CACHE[key]
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=PROBE_TIMEOUT)
        dur = float((r.stdout or "").strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not dur > 0:          # 0, отрицательное и NaN — «не прочли»
        return None
    _CACHE[key] = dur
    return dur
