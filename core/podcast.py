# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""podcast: проект подкаста, безопасные имена папок и команды yt-dlp (чистая часть).

ПОЧЕМУ модуль есть. Длинный подкаст режется на рилсы, и для этого нужно: завести
папку подкаста (имя — из названия), хранить проект `podcast.json` с кусками и не
дать кускам разъехаться (пересечения, выход за длину), скачать видео с YouTube в
высшем качестве и выбрать дорожку звука (оригинал или автодубляж).

Всё здесь — чистые функции без состояния: без сети, без запуска yt-dlp и без
HTTP-слоя. Процессы и джоб живут выше, в api/; этот модуль только собирает
аргументы и разбирает ответы — поэтому его можно проверить тестами без сети.

Команды возвращаются СПИСКАМИ, без shell: ссылка из запроса пользователя не может
стать частью командной строки оболочки. Значение, начинающееся с «-», yt-dlp
разобрал бы как опцию (`--exec`, `--config-location`), — такие значения отвергаем
до запуска, а не надеемся на `--`.
"""
import math
import os
import re
import secrets
import sys
from typing import Any

from core import fileio
from core.umsg import ReelsiError, umsg

PROJECT_FILE = "podcast.json"
SCHEMA_V = 1
SLUG_MAX = 60

# Всё, что не буква и не цифра, становится одним «_»: `_` сам по себе тоже не буква,
# поэтому повторы схлопываются тем же правилом. Классы явные, а не \w: \w пропустил бы
# комбинируемые знаки и прочие юникод-буквы, которых в имени папки быть не должно.
_SLUG_SPLIT = re.compile(r"[^A-Za-zА-Яа-яЁё0-9]+")

# Ссылки YouTube, которые мы принимаем. Хост допускает www./m. и голый домен.
_YT_RE = re.compile(
    r"^https?://(?:(?:www|m)\.)?youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/)[A-Za-z0-9_-]+"
    r"|^https?://youtu\.be/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)

# Селектор format_id попадает в `-f` yt-dlp, поэтому только безопасный алфавит.
_FORMAT_ID_RE = re.compile(r"[0-9A-Za-z_-]+")

# Расширения видео, в которые yt-dlp склеивает источник (см. download_cmd).
_SOURCE_EXTS = ("mkv", "mp4", "webm", "mov")

# [download]  12.3% of ~ 1.20GiB at  3.10MiB/s ETA 06:12 (frag 3/120)
# [download] 100% of  512.00MiB in 00:02:11 at 3.89MiB/s
_PROGRESS_RE = re.compile(
    r"^\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?P<tilde>~?)\s*(?P<size>\d[\d.]*[A-Za-z]+)"
    r"(?:\s+in\s+\S+)?"
    r"(?:\s+at\s+(?P<speed>.+?/s))?"
    r"(?:\s+ETA\s+(?P<eta>\S+))?"
)


def slugify(title: str) -> str:
    """Безопасное имя папки из названия: буквы (лат./кир.), цифры, «_»; регистр сохраняется.

    Пустой результат → «podcast», длина не больше 60 символов.
    """
    s = _SLUG_SPLIT.sub("_", str(title or ""))
    s = s.strip("_")[:SLUG_MAX].strip("_")
    return s or "podcast"


def project_path(folder: str) -> str:
    return os.path.join(folder, PROJECT_FILE)


def new_project(title: str, url: str, source: str, reels_wanted: int) -> dict[str, Any]:
    """Пустой проект подкаста по схеме спеки (docs/PODCAST_SPEC.md, раздел «Данные»)."""
    return {
        "v": SCHEMA_V,
        "title": title,
        "slug": slugify(title),
        "url": url,
        "source": source,
        "audio_track": None,
        "duration": 0.0,
        "reels_wanted": reels_wanted,
        "status": "new",
        "error": "",
        "asr_engine": "",
        "lang": "",
        "segments": [],
    }


def load_project(folder: str) -> dict[str, Any] | None:
    """Проект из папки или None: нет файла, битый JSON, не объект или чужая версия схемы."""
    data = fileio.json_load_soft(project_path(folder), None)
    if not isinstance(data, dict) or data.get("v") != SCHEMA_V:
        return None
    return data


def num_or_none(x: Any) -> float | None:
    """Число из JSON или None. bool — не число, хоть Python и считает его int."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    f = float(x)
    return f if math.isfinite(f) else None


def validate_segments(segments: Any, duration: Any) -> list[str]:
    """Ошибки кусков по-русски; пустой список — куски в порядке.

    Инварианты спеки: 0 <= start < end; end <= duration (если длина известна);
    после сортировки по start куски не пересекаются (касание концом в начало — можно);
    у каждого непустой id, и id не повторяются.
    """
    if not isinstance(segments, list):
        return ["куски должны быть списком"]
    dur = num_or_none(duration) or 0.0
    errs: list[str] = []
    ids: set[str] = set()
    spans: list[tuple[float, float, str]] = []
    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, dict):
            errs.append(f"кусок №{i}: не объект")
            continue
        sid = seg.get("id")
        if not isinstance(sid, str) or not sid:
            errs.append(f"кусок №{i}: нет id")
            label = f"№{i}"
        else:
            label = sid
            if sid in ids:
                errs.append(f"id {sid} повторяется")
            ids.add(sid)
        start, end = num_or_none(seg.get("start")), num_or_none(seg.get("end"))
        if start is None or end is None:
            errs.append(f"кусок {label}: начало и конец должны быть числами")
            continue
        if not 0 <= start < end:
            errs.append(f"кусок {label}: нужно 0 ≤ начало < конец ({start:.2f}–{end:.2f} с)")
            continue
        if dur > 0 and end > dur:
            errs.append(f"кусок {label}: конец {end:.2f} с дальше длины подкаста ({dur:.2f} с)")
        spans.append((start, end, label))
    # Пересечение ищем по максимальному концу уже пройденных кусков, а не по соседу:
    # так ловится и кусок, целиком спрятанный внутри длинного.
    spans.sort()
    reach: float | None = None
    reach_label = ""
    for start, end, label in spans:
        if reach is not None and start < reach:
            errs.append(f"кусок {label} пересекается с {reach_label}")
        if reach is None or end > reach:
            reach, reach_label = end, label
    return errs


def save_project(folder: str, proj: dict[str, Any]) -> None:
    """Записать проект атомарно. Нарушены инварианты кусков — бросает ReelsiError, файл не трогает."""
    errs = validate_segments(proj.get("segments", []), proj.get("duration", 0.0))
    if errs:
        raise ReelsiError(umsg("podcast_bad_segments",
                               "Куски подкаста не сходятся: {problems}",
                               problems="; ".join(errs)))
    os.makedirs(folder, exist_ok=True)
    fileio.atomic_json_dump(project_path(folder), proj, indent=2)


def new_segment_id() -> str:
    """Устойчивый id куска: 6 hex-символов. Правка границ id не меняет."""
    return secrets.token_hex(3)


def is_youtube(url: str) -> bool:
    """Ссылка на видео YouTube: watch?v=, youtu.be/, shorts/, live/ (http или https)."""
    return isinstance(url, str) and _YT_RE.match(url) is not None


def _yt_url(url: Any) -> str:
    if not isinstance(url, str):
        raise ValueError("ссылка должна быть строкой")
    if url.startswith("-"):
        raise ValueError("ссылка не может начинаться с «-»: " + url[:80])
    if not is_youtube(url):
        raise ValueError("не ссылка YouTube: " + url[:80])
    return url


def probe_cmd(url: str) -> list[str]:
    """Команда проверки дорожек: `yt-dlp -J` без скачивания, JSON в stdout."""
    url = _yt_url(url)
    return [sys.executable, "-m", "yt_dlp", "-J", "--no-playlist",
            "--js-runtimes", "node", "--", url]


def _lang_pref_is_original(lp: Any) -> bool:
    # YouTube ставит оригиналу language_preference >= 10, дубляжу — -1.
    v = num_or_none(lp)
    return v is not None and v >= 10


def audio_tracks(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Звуковые дорожки из ответа `yt-dlp -J`: по одной лучшей на язык.

    Лучшая в языке — с максимальным abr. Оригинал — language_preference >= 10 или слово
    «original» в format_note. Дубляж (`dub`) — не оригинал и либо в format_note слово
    «dubbed», либо у видео есть оригинал на другом языке. ПОЧЕМУ второе: у YouTube https-
    форматы автодубляжа слова «dubbed» не несут («Russian, medium»), а оригинал рядом есть.
    Порядок: сначала оригиналы, потом по коду языка.
    HLS (m3u8) не берём: у него нет abr, и выбор по битрейту бессмысленен.
    """
    if not isinstance(info, dict):
        return []
    cands: list[dict[str, Any]] = []
    for f in info.get("formats") or []:
        if not isinstance(f, dict):
            continue
        if f.get("vcodec") != "none" or f.get("acodec") in (None, "none"):
            continue
        if f.get("protocol") != "https" or not f.get("format_id"):
            continue
        lang = f.get("language") or ""
        note = str(f.get("format_note") or "")
        original = _lang_pref_is_original(f.get("language_preference")) \
            or "original" in note.lower()
        cands.append({
            "format_id": str(f["format_id"]),
            "lang": lang,
            "label": note.split(",")[0].strip() or lang,
            "original": original,
            "abr": num_or_none(f.get("abr")) or 0.0,
            "dubbed_note": "dubbed" in note.lower(),
        })
    any_original = any(c["original"] for c in cands)
    best: dict[str, dict[str, Any]] = {}
    for c in cands:
        cur = best.get(c["lang"])
        if cur is None or c["abr"] > cur["abr"]:
            best[c["lang"]] = c
    out: list[dict[str, Any]] = []
    for c in best.values():
        dub = (not c["original"]) and (c["dubbed_note"] or any_original)
        out.append({"format_id": c["format_id"], "lang": c["lang"], "label": c["label"],
                    "original": c["original"], "dub": dub, "abr": c["abr"]})
    return sorted(out, key=lambda t: (not t["original"], t["lang"]))


def default_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Дорожка по умолчанию: первый оригинал, иначе первая; пусто → None."""
    for t in tracks:
        if t.get("original"):
            return t
    return tracks[0] if tracks else None


def download_cmd(url: str, dest_dir: str, format_id: str | None) -> list[str]:
    """Команда скачивания видео в высшем качестве, склейка в mkv.

    Селектор: при format_id — сначала эта звуковая дорожка, затем любая; иначе любая.
    `bv*+ba` — лучшее видео с лучшим аудио, `/b` — запасной готовый файл.
    """
    url = _yt_url(url)
    dest = str(dest_dir)
    if dest.startswith("-"):
        raise ValueError("папка назначения не может начинаться с «-»: " + dest[:80])
    if format_id is None:
        sel = "bv*+ba/b"
    else:
        fid = str(format_id)
        # format_id уходит в селектор `-f`; проверяем алфавит, а не экранируем. «-» в начале
        # отсекаем отдельно: такой id в YouTube не бывает, а значение с «-» похоже на опцию.
        if fid.startswith("-") or not _FORMAT_ID_RE.fullmatch(fid):
            raise ValueError("format_id: только буквы, цифры, «_» и «-»: " + fid[:40])
        sel = f"bv*+ba[format_id={fid}]/bv*+ba/b"
    # -N 8 и 10M-куски — ПОЧЕМУ: одним соединением YouTube режет до 45–200 КиБ/с (замер
    # 2026-10-10: ETA 8 ч на видео 1.3 ГиБ, 30 мин на звук 106 МиБ). `--http-chunk-size 10M`
    # спасает видео (41 МиБ/с), но звуковую дорожку с языком (`140-1`) — нет (90 КиБ/с);
    # `-N 8` вместе с ним даёт звук 13.5 МиБ/с (7 с), видео ~43 МиБ/с.
    return [sys.executable, "-m", "yt_dlp", "--no-playlist", "--js-runtimes", "node",
            "--newline", "-N", "8", "--http-chunk-size", "10M",
            "-f", sel, "--merge-output-format", "mkv",
            "-o", os.path.join(dest, "source.%(ext)s"), "--", url]


def progress_fields(line: str) -> dict[str, Any] | None:
    """Строка прогресса yt-dlp → поля или None.

    Процент, размер (как в строке, с «~» если был), скорость и ETA. `[Merger]` — отдельная
    стадия: склейка видео и звука в mkv. Всё прочее (Destination, предупреждения) — None.
    """
    s = str(line or "").strip()
    if s.startswith("[Merger]"):
        return {"stage": "merge"}
    m = _PROGRESS_RE.match(s)
    if not m:
        return None
    return {
        "pct": float(m.group("pct")),
        "size": m.group("tilde") + m.group("size"),
        "speed": m.group("speed") or "",
        "eta": m.group("eta") or "",
    }


def downloaded_source(dest_dir: str) -> str | None:
    """Путь к скачанному `source.<видео-ext>` или None.

    Имена точные: `source.mkv.part` и промежуточный `source.f137.mp4` не совпадают
    ни с одним кандидатом, поэтому недокачанное не примется за готовое.
    """
    for ext in _SOURCE_EXTS:
        p = os.path.join(dest_dir, f"source.{ext}")
        if os.path.isfile(p):
            return p
    return None
