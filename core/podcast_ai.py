# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""podcast_ai: расшифровка подкаста и ИИ-выбор кусков для рилсов (порция П2).

Конвейер: звук 16 кГц из источника → пословная расшифровка (движок из ⚙ → «Подкаст») →
фразы с метками времени → ИИ отдаёт N кусков с границами по секундам → границы
прилипают к словам (не режем посреди фразы) → проект подкаста `podcast.json`.

ПОЧЕМУ границы считаются здесь, а не верятся модели. Модель смотрит на текст и
называет время примерно: её «10:32» может попасть в середину фразы. Поэтому каждая
граница прилипает к слову расшифровки: начало — к слову после паузы, конец — к слову
перед паузой. Тогда кусок не обрывает речь на полуслове и не тянет чужую фразу.

Без ffmpeg и GPU в самом модуле: звук делает `_run_ff`, расшифровку — `asr_backends`,
ответ модели — `aicut._ask_json`. Все три подменяются в тестах по модулю-владельцу.
Модуль не знает про HTTP и `api/`: джоб и роуты живут выше (порция П3).
"""
import bisect
import math
import os
from typing import Any, Callable

from core import aicut, asr_backends, fileio, media, podcast
from core.podcast import num_or_none
from core.app_meta import console_emit
from core.draftrender import _run_ff
from core.umsg import ReelsiError, umsg

WAV_FILE = "audio16k.wav"
WAV_TMP_FILE = "audio16k.tmp.wav"
WORDS_FILE = "words.json"

# Пауза, с которой фраза и граница куска считаются «чистыми» (см. snap_segments).
PAUSE_CLEAN = 0.3
# Пауза между словами, с которой начинается новая фраза (см. phrases).
PHRASE_GAP = 0.6
# Окно поиска слова вокруг границы, которую дала модель, секунды.
SNAP_WINDOW = 3.0
# Кусок короче этого — вырожденный, выкидываем (см. snap_segments).
MIN_LEN = 5.0
TITLE_MAX = 120
WHY_MAX = 300

# Ответ модели — куски с границами. Схема той же формы, что YELLOW_SCHEMA в aicut.
SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {"segments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "start": {"type": "number"},
            "end": {"type": "number"},
            "title": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["start", "end", "title", "why"],
        "additionalProperties": False,
    }}},
    "required": ["segments"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Звук и расшифровка
# --------------------------------------------------------------------------- #

def extract_wav(source: str, folder: str, emit: Callable[..., Any] | None = None,
                cancel: Callable[[], bool] | None = None) -> str:
    """Звук 16 кГц моно для расшифровки → `<folder>/audio16k.wav`.

    Готовый файл не пересоздаём, если он не старше источника: расшифровка тяжёлая,
    а звук из того же видео одинаковый. Пишем во временный файл и переименовываем —
    оборванный ffmpeg не оставит под финальным именем обрубок, который примут за готовый.
    """
    out = os.path.join(folder, WAV_FILE)
    try:
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(source):
            return out
    except OSError:
        pass  # источника нет или не прочтён — пусть ffmpeg скажет, что не так
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, WAV_TMP_FILE)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", source, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", tmp]
    res = _run_ff(cmd, cancel=cancel)
    if res is None:
        fileio.remove_quiet(tmp)
        raise ReelsiError(umsg("podcast_wav_failed",
                               "Не удалось подготовить звук для расшифровки ({why})",
                               why="отменено"))
    if res.returncode != 0:
        fileio.remove_quiet(tmp)
        raise ReelsiError(umsg("podcast_wav_failed",
                               "Не удалось подготовить звук для расшифровки ({why})",
                               why=f"ffmpeg, код {res.returncode}"))
    fileio.move_file(tmp, out)
    if emit:
        emit("звук для расшифровки готов")
    return out


def transcribe(folder: str, proj: dict[str, Any], emit: Callable[..., Any] | None = None,
               cancel: Callable[[], bool] | None = None) -> list[dict[str, Any]]:
    """Пословная расшифровка подкаста → `words.json`, в проект — движок, язык, длина.

    Язык передаём ТОЛЬКО whisper: у GigaAM/CTC такого аргумента нет, и лишний `lang`
    уронил бы вызов. Проект сохраняем здесь же: эти поля нужны и следующему шагу, и
    перезапуску сервера, а не только памяти текущего джоба.
    """
    source = proj.get("source") or ""
    wav = extract_wav(source, folder, emit=emit, cancel=cancel)
    if cancel is not None and cancel():
        raise ReelsiError(umsg("podcast_cancelled", "Расшифровка отменена"))
    engine = aicut.podcast_asr_engine(emit)
    lang = aicut.podcast_lang()
    opts: dict[str, Any] = {}
    meta = asr_backends.engine_meta(engine) or {}
    if meta.get("kind") == "whisper":
        opts["lang"] = lang
    words = asr_backends.transcribe_words(wav, engine, emit=emit, **opts)
    if not words:
        raise ReelsiError(umsg("podcast_no_words",
                               "Расшифровка пустая: в звуке не нашлось слов"))
    fileio.atomic_json_dump(os.path.join(folder, WORDS_FILE), words)
    duration = media.probe_duration(source)
    if not duration:
        duration = max(float(w["end"]) for w in words)
    proj["asr_engine"] = engine
    proj["lang"] = lang or ""
    proj["duration"] = float(duration)
    podcast.save_project(folder, proj)
    return words


def load_words(folder: str) -> list[dict[str, Any]]:
    """Слова расшифровки из папки; нет файла, битый или не список → []."""
    data = fileio.json_load_soft(os.path.join(folder, WORDS_FILE), None)
    return data if isinstance(data, list) else []


def phrases(words: list[dict[str, Any]], gap: float = PHRASE_GAP) -> list[dict[str, Any]]:
    """Фразы для модели: `{"start","end","text"}`.

    Новая фраза начинается, если пауза до следующего слова ≥ gap или текущее слово
    кончает предложение (. ? ! …). Фраза — единица, на которую модель ориентируется:
    метка времени у фразы — это начало её первого слова.
    """
    out: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends_sentence = str(w.get("w", "")).strip().endswith((".", "?", "!", "…"))
        pause = float(nxt["start"]) - float(w["end"]) if nxt is not None else None
        if nxt is None or pause >= gap or ends_sentence:
            out.append({
                "start": float(cur[0]["start"]),
                "end": float(cur[-1]["end"]),
                "text": " ".join(str(x.get("w", "")).strip() for x in cur),
            })
            cur = []
    return out


def fmt_time(sec: float) -> str:
    """Метка времени для модели: «mm:ss», а от часа — «h:mm:ss»."""
    v = num_or_none(sec)
    total = int(max(0.0, v if v is not None else 0.0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- #
# Выбор кусков (ИИ) и прилипание границ
# --------------------------------------------------------------------------- #

def build_prompt(n: int, phr: list[dict[str, Any]], min_len: int = 30,
                 max_len: int = 90) -> tuple[str, str]:
    """(system, user) для выбора N кусков. user — фразы с метками «[мм:сс | секунды]»."""
    system = (
        "Ты монтажёр подкаста. Найди в расшифровке подкаста ровно "
        f"{n} самостоятельных фрагментов для вертикальных роликов; каждый — законченная "
        "мысль с крючком в начале, понятная без контекста; "
        f"длина {min_len}–{max_len} с; фрагменты не пересекаются; "
        "start/end — секунды из меток; title — короткий заголовок на языке подкаста; "
        "why — почему зайдёт. Ответ — только JSON по схеме."
    )
    lines = [f"[{fmt_time(p['start'])} | {p['start']:.1f}] {p['text']}" for p in phr]
    user = f"Фрагментов: {n}\n" + "\n".join(lines)
    return system, user


def fits_context(text: str, model_caps: dict[str, Any] | None) -> bool:
    """Влезает ли текст в контекст модели шага.

    Оценка «символы / 3» грубая, поэтому берём 80 % лимита, а не сам лимит. Нет лимита
    в каталоге — считаем, что влезает: честнее пробовать, чем отказать на пустом месте.
    """
    ctx = (model_caps or {}).get("ctx_limit")
    if not ctx:
        return True
    return len(text) / 3 < ctx * 0.8


def ask_segments(phr: list[dict[str, Any]], n: int,
                 emit: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """Один ИИ-вызов шага «Подкаст»: сырые куски из ответа или []. Контекст — у вызывающего."""
    system, user = build_prompt(n, phr)
    lvl = aicut.step_reasoning("podcast")
    data = aicut._ask_json(system, user, SEGMENTS_SCHEMA,
                           max_tokens=aicut.reason_budget(4000, lvl),
                           emit=emit or console_emit, reasoning=lvl,
                           profile=aicut.step_profile("podcast"), step="podcast")
    segs = data.get("segments") if isinstance(data, dict) else None
    return segs if isinstance(segs, list) else []


def _pause_before(ws: list[dict[str, Any]], i: int) -> float:
    # Начало записи — тоже чистая граница: перед первым словом пауза «бесконечна».
    return float(ws[i]["start"]) - float(ws[i - 1]["end"]) if i > 0 else math.inf


def _pause_after(ws: list[dict[str, Any]], i: int) -> float:
    # Конец записи — чистая граница: после последнего слова пауза «бесконечна».
    return float(ws[i + 1]["start"]) - float(ws[i]["end"]) if i + 1 < len(ws) else math.inf


def _snap_start(ws: list[dict[str, Any]], t: float, window: float) -> float:
    """Начало слова, ближайшего к t в окне; среди них сначала те, перед которыми пауза.

    Пауза важна: кусок, начатый сразу после паузы, не даёт услышать хвост чужой фразы.
    Нет слов в окне — t не трогаем, модель была права или слова вне расшифровки.
    """
    cand = [i for i, w in enumerate(ws) if abs(float(w["start"]) - t) <= window]
    if not cand:
        return t
    clean = [i for i in cand if _pause_before(ws, i) >= PAUSE_CLEAN]
    pool = clean or cand
    best = min(pool, key=lambda i: abs(float(ws[i]["start"]) - t))
    return float(ws[best]["start"])


def _snap_end(ws: list[dict[str, Any]], t: float, window: float) -> float:
    """Конец слова, ближайшего к t в окне; среди них сначала те, за которыми пауза."""
    cand = [i for i, w in enumerate(ws) if abs(float(w["end"]) - t) <= window]
    if not cand:
        return t
    clean = [i for i in cand if _pause_after(ws, i) >= PAUSE_CLEAN]
    pool = clean or cand
    best = min(pool, key=lambda i: abs(float(ws[i]["end"]) - t))
    return float(ws[best]["end"])


def snap_segments(raw: Any, words: list[dict[str, Any]], n: int, duration: float,
                  window: float = SNAP_WINDOW,
                  min_len: float = MIN_LEN) -> tuple[list[dict[str, Any]], list[str]]:
    """Куски модели → куски проекта с границами по словам. Возвращает (куски, предупреждения).

    Порядок: первые n корректных ответов; границы прилипают к словам; конец режем по
    длине записи; пересечения разводим (следующий начинается с первого слова после конца
    предыдущего); короткие выкидываем; каждому даём id.
    """
    warnings: list[str] = []
    ws = sorted(
        (w for w in (words or []) if isinstance(w, dict)
         and num_or_none(w.get("start")) is not None and num_or_none(w.get("end")) is not None),
        key=lambda w: float(w["start"]))
    starts = [float(w["start"]) for w in ws]

    # 1. первые n корректных кусков: нужны числа и start < end
    valid: list[tuple[dict[str, Any], float, float]] = []
    for k, item in enumerate(raw if isinstance(raw, list) else [], 1):
        s = num_or_none(item.get("start")) if isinstance(item, dict) else None
        e = num_or_none(item.get("end")) if isinstance(item, dict) else None
        if s is None or e is None or not s < e:
            warnings.append(f"кусок №{k} от ИИ пропущен: нужны числа start < end")
            continue
        valid.append((item, s, e))
    if len(valid) > n:
        warnings.append(f"ИИ дал {len(valid)} кусков, взяли первые {n}")
    valid = valid[:n]
    if len(valid) < n:
        warnings.append(f"ИИ дал {len(valid)} из {n}")

    # 2. прилипание: границы — к словам, конец не дальше длины записи
    cands: list[dict[str, Any]] = []
    for item, s, e in valid:
        start = _snap_start(ws, s, window) if ws else s
        end = _snap_end(ws, e, window) if ws else e
        if duration and duration > 0:
            end = min(end, float(duration))
        cands.append({
            "start": max(0.0, start), "end": end,
            "title": str(item.get("title") or "").strip()[:TITLE_MAX],
            "why": str(item.get("why") or "").strip()[:WHY_MAX],
        })

    # 3. пересечения и короткие: по началу, следующий начинается после конца предыдущего
    kept: list[dict[str, Any]] = []
    for seg in sorted(cands, key=lambda c: c["start"]):
        if kept and seg["start"] < kept[-1]["end"]:
            j = bisect.bisect_left(starts, kept[-1]["end"])
            if j >= len(ws) or starts[j] >= seg["end"]:
                warnings.append(f"кусок «{seg['title'] or '?'}» убран: "
                                "перекрывает предыдущий и места не остаётся")
                continue
            seg["start"] = starts[j]
        if seg["end"] - seg["start"] < min_len:
            warnings.append(f"кусок «{seg['title'] or '?'}» убран: короче {min_len:g} с")
            continue
        kept.append(seg)

    # 4. id и итог
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    for seg in kept:
        sid = podcast.new_segment_id()
        while sid in used:
            sid = podcast.new_segment_id()
        used.add(sid)
        out.append({"id": sid, "start": seg["start"], "end": seg["end"],
                    "title": seg["title"], "why": seg["why"], "reel": ""})
    if len(out) < n and len(valid) >= n:
        warnings.append(f"после правки границ осталось {len(out)} из {n}")
    return out, warnings


def choose(folder: str, proj: dict[str, Any], emit: Callable[..., Any] | None = None) -> list[str]:
    """ИИ-выбор кусков: слова → фразы → ответ модели → границы → `podcast.json`.

    Статус «ready» ставим только после успешной записи кусков. Не влезает в контекст
    модели шага — отказ ДО вызова модели, файл проекта не трогаем. Возвращает предупреждения.
    """
    words = load_words(folder)
    if not words:
        raise ReelsiError(umsg("podcast_no_words",
                               "Расшифровки нет: сначала расшифруй подкаст"))
    n = int(proj.get("reels_wanted") or 0)
    if n < 1:
        raise ReelsiError(umsg("podcast_no_reels", "Не задано, сколько рилсов нужно"))
    phr = phrases(words)
    _, user = build_prompt(n, phr)
    # Контекст проверяем по модели ТОГО ЖЕ профиля, что и вызов (step_profile), а не по active.
    prof = aicut.resolve_profile(name=aicut.step_profile("podcast"))
    caps = aicut.catalog.caps(prof.get("provider"), prof.get("model"), emit=emit or console_emit)
    if not fits_context(user, caps):
        raise ReelsiError(umsg(
            "podcast_ctx",
            "Расшифровка не влезает в контекст модели шага «Подкаст» — "
            "выбери модель с большим контекстом в ⚙"))
    raw = ask_segments(phr, n, emit=emit)
    segs, warns = snap_segments(raw, words, n, float(proj.get("duration") or 0.0))
    proj["segments"] = segs
    proj["status"] = "ready"
    podcast.save_project(folder, proj)
    return warns
