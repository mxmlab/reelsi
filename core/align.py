# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Map transcript words onto the edited (silence-removed) timeline, and detect
repeated phrases for optional removal.

All source times are in cam1 seconds; output positions are frames at the sequence rate (`fps`, default 60).
"""
import re
from core import subs
from core.fileio import atomic_text_write
FPS = 60


def assign_cameras(segments, n_cams, return_every=2, big_chunk_sec=6.0):
    """Активная камера (0-based, 0 = камера 1) на каждый кусок монтажа.
    Одна функция на все пути (нарезка, редактор нарезки, окно раскладки).

    ОБЩЕЕ правило (юзер, 2026-07-22): ПЕРВЫЙ и ПОСЛЕДНИЙ куски — всегда камера 1,
    ролик открывается и закрывается основным планом. Дальше по числу камер:

    * **2 камеры** (`_assign_two`) — строгое чередование 1-2-1-2. Дополнительных
      правил нет: выбирать не из чего, любое отступление читается как сбой ритма.
    * **3+ камеры** (`_assign_many`) — прежняя раскладка: БОЛЬШОЙ кусок
      (>= `big_chunk_sec`) идёт на камеру 1 (длинную мысль держим на основном плане,
      и это единственный случай, когда камера повторяется подряд); после
      `return_every` не-первых кусков возвращаемся на камеру 1; остальные куски —
      наименее использованному ракурсу, чтобы 2..N делились поровну.
      Чередовать здесь нечего: ракурсов много, и «строго через один» лишило бы
      смысла и большие куски, и балансировку."""
    n = max(1, n_cams)
    k = len(segments)
    if n == 1 or k == 0:
        return [0] * k
    if k <= 2:                                # 1 кусок — кам1; 2 — оба кам1 (начало И конец)
        return [0] * k
    out = _assign_two(segments) if n == 2 else _assign_many(segments, n, return_every, big_chunk_sec)
    return out


def _assign_two(segments):
    """Две камеры: строгое чередование, начало и конец на кам1.

    Чётность. Чередование от кам1 приходит обратно в кам1, только если кусков
    НЕЧЁТНОЕ число. Если чётное — «чередуемся» и «кончаем на кам1» несовместимы, и
    ровно в одном месте камера ДЕРЖИТСЯ два куска подряд (дальше чередование идёт
    «с другой ноги», поэтому конец снова попадает на кам1). Место сдвойки:
      - пара САМЫХ КОРОТКИХ соседних кусков — задержку камеры на паре коротышей
        глазом почти не поймать;
      - никогда не в начале: первый кусок в пару не попадает;
      - при равной длине пар — ближе к КОНЦУ и лучше на КАМЕРЕ 1 («не сменили план»
        читается ровнее, чем два коротких куска подряд со второй камеры — тот самый
        паттерн кам2-кам2, который правили аудитом)."""
    k = len(segments)
    alt = lambda i, shift=0: (i + shift) % 2          # noqa: E731 — 0,1,0,1…
    if k % 2 == 1:                            # чётность сходится сама
        return [alt(i) for i in range(k)]
    # с индекса p фаза сдвигается: на паре (p-1, p) камера держится. p >= 2 — первый
    # кусок в пару не попадает; p нечётный сдваивает кам1, чётный — вторую камеру.
    dur = [float(e) - float(s) for s, e in segments]
    p = min(range(2, k), key=lambda q: (dur[q - 1] + dur[q], q % 2 == 0, -q))
    return [alt(i) if i < p else alt(i, 1) for i in range(k)]


def _assign_many(segments, n, return_every, big_chunk_sec):
    """3+ камеры: базовая раскладка (большой кусок — на кам1, возврат на кам1 каждые
    `return_every` катов, иначе наименее занятый ракурс), плюс общее требование
    «последний кусок — камера 1»."""
    big = [(e - s) >= big_chunk_sec for s, e in segments]
    usage = [0] * n
    out = []
    run = 0                                   # подряд идущих не-первых кусков

    def least(prev):                          # наименее занятая камера != prev, не кам1
        cand = [c for c in range(1, n) if c != prev] or list(range(1, n))
        return min(cand, key=lambda c: (usage[c], c))

    for i in range(len(segments)):
        prev = out[i - 1] if i > 0 else None
        want1 = (i == 0) or big[i] or run >= return_every
        cam = 0 if (want1 and (prev != 0 or big[i])) else least(prev)
        # не сидим на кам1 прямо перед большим куском (он её займёт) -> шаг в сторону
        if cam == 0 and i > 0 and not big[i] and i + 1 < len(segments) and big[i + 1]:
            alt = least(prev)
            if alt != prev:
                cam = alt
        if cam == prev and cam != 0:          # не-первый ракурс подряд — запрещено
            cam = 0
        usage[cam] += 1
        run = 0 if cam == 0 else run + 1
        out.append(cam)
    # хвост: последний кусок обязан быть на кам1. Если из-за этого кам1 сдвоилась с
    # предпоследним — уводим ПРЕДпоследний на свободный ракурс (в конце это незаметнее,
    # чем в начале). Большой предпоследний оставляем как есть: он на кам1 по праву.
    if out[-1] != 0:
        out[-1] = 0
        if len(out) > 2 and out[-2] == 0 and not big[-2]:
            cand = [c for c in range(1, n) if c != out[-3]]
            if cand:
                out[-2] = min(cand, key=lambda c: (out.count(c), c))
    return out


def timeline_map(segments, fps=FPS):
    """segments: list of (s,e) seconds kept. Return list of
    (s, e, out_start_frame, out_end_frame) and total frames."""
    rows = []
    tl = 0
    for s, e in segments:
        length = round(e * fps) - round(s * fps)
        if length <= 0:
            continue
        rows.append((s, e, tl, tl + length))
        tl += length
    return rows, tl


def map_words_to_clips(words, clips, min_frames=6, max_hold=0.5, fps=FPS, gap_fill=True):
    """words: source-second timestamps. clips: (start,end,in,out,...) frames.
    Map each word to its timeline position via the clip whose SOURCE range holds it.
    Assignment is strictly by midpoint of the word in source frames (ci <= sf < co).
    Words in cut-out source regions are dropped. Returns [(w,start,end)] output frames.

    fps — частота секвенции: кадры в clips посчитаны в НЕЙ, а не в 60. Константа
    оставляла 25-кадровую секвенцию без субтитров вовсе: слово «не влезало» ни в один
    клип (GZ, п. E). Дефолт прежний — вызовы без fps не меняются."""
    hold = round(max_hold * fps)
    placed = []
    for wd in words:
        ts, te = wd["start"], wd["end"]
        sf = round(0.5 * (ts + te) * fps)          # source-frame midpoint
        for clip in clips:
            cs, ce, ci, co = clip[0], clip[1], clip[2], clip[3]
            if ci <= sf < co:
                a = cs + (round(ts * fps) - ci)
                b = cs + (round(te * fps) - ci)
                a = max(cs, min(ce - 1, a)); b = max(a + 1, min(ce, b))
                placed.append({"w": wd["w"], "start": a, "end": b, "_e0": b, "_ce": ce})
                break
    placed.sort(key=lambda p: p["start"])
    # merge consecutive identical words
    merged = []
    for p in placed:
        if merged and _norm(p["w"]) == _norm(merged[-1]["w"]) and p["start"] <= merged[-1]["end"] + 2:
            merged[-1]["end"] = max(merged[-1]["end"], p["end"])
            merged[-1]["_e0"] = max(merged[-1]["_e0"], p["_e0"])
        else:
            merged.append(p)
    # gap-fill capped at max_hold, but not past the clip end
    if gap_fill:
        for i, p in enumerate(merged):
            limit = p["_ce"]
            if i + 1 < len(merged) and merged[i + 1]["start"] <= p["_ce"]:
                limit = merged[i + 1]["start"]
            p["end"] = max(p["start"] + min_frames, min(limit, p["_e0"] + hold, p["_ce"]))
    for p in merged:
        p.pop("_e0", None); p.pop("_ce", None)
    return merged


def map_words(words, segments, min_frames=6, gap_fill=True, max_hold=0.5, fps=FPS):
    """Place each word on the output timeline using map_words_to_clips.
    Segments (s, e) in seconds are converted to clips frames.
    Assignment is strictly by word midpoint (dropped if midpoint is cut out).
    Titles are never shorter than min_frames. Consecutive identical words are merged.
    Returns list of dict(w, start, end) in OUTPUT frames."""
    rows, _ = timeline_map(segments, fps=fps)
    clips = [(fs, fe, round(s * fps), round(e * fps)) for (s, e, fs, fe) in rows]
    return map_words_to_clips(words, clips, min_frames=min_frames, max_hold=max_hold,
                               fps=fps, gap_fill=gap_fill)


_norm_re = re.compile(r"[^\w]+", re.UNICODE)
def _norm(w):
    return _norm_re.sub("", w.lower())


# filler / cough tokens to drop (and cut) — clearly non-words
FILLERS = {"хм", "хмм", "эм", "эмм", "ммм", "мм", "эээ", "ээ", "кхм", "гм",
           "ааа", "мхм", "угу", "эх", "кха", "апчхи"}


def is_filler(word):
    return _norm(word) in FILLERS


def dead_air_ranges(words, max_pause=1.0, pad=0.12):
    """Aggressive-mode extra: cut gaps BETWEEN transcribed words longer than
    max_pause (breaths/hesitations VAD kept as faint 'speech'), leaving a small pad."""
    out = []
    for i in range(1, len(words)):
        gap = words[i]["start"] - words[i - 1]["end"]
        if gap > max_pause:
            a = words[i - 1]["end"] + pad
            b = words[i]["start"] - pad
            if b - a > 0.1:
                out.append((a, b))
    return out


def clamp_word_times(words, max_dur=1.2, tail=0.55):
    """Fix Whisper's ballooned word timestamps: a single word whose span is longer
    than max_dur has swallowed a stumble+pause — the real utterance is at the END,
    so re-anchor start to end-tail. Prevents 1-2s+ stretched titles shown too early."""
    out = []
    for w in words:
        if w["end"] - w["start"] > max_dur:
            w = dict(w)
            w["start"] = round(w["end"] - tail, 3)
        out.append(w)
    return out


# Перечисление — НЕ дубль. «где он сделал вот это, А где он сделал другое»:
# начало повторяется дословно, но это параллельная конструкция, и обе половины
# несут своё. Детекторы повторов видели тут брошенный заход и сносили всё до
# второго вхождения (жалоба юзера 2026-08-04: «вырезается до „а где он сделал
# другое“»). Отличаем по ДВУМ признакам сразу: перед вторым вхождением стоит
# союз-связка, и у первого есть СВОЙ содержательный хвост. У настоящей
# пересъёмки хвост другой — паразит («ээ», «ну») или обрубок слова, которое
# сейчас прозвучит целиком.
ENUM_LINKS = {"а", "и", "но", "или", "либо", "то", "потом", "затем", "далее",
              "тоже", "также", "зато", "иногда", "порой", "плюс"}
_ENUM_EMPTY = FILLERS | ENUM_LINKS | {"ну", "вот", "короче", "типа", "значит",
                                      "наверное", "просто", "так", "уже"}


def _stub_of(tok, ahead):
    """Слово — обрубок одного из ближайших слов второго захода («диси» ←
    «дисип»)? Тогда это запинка, а не отдельная мысль."""
    for a in ahead:
        s, l = (tok, a) if len(tok) <= len(a) else (a, tok)
        if len(s) >= 4 and l.startswith(s):
            return True
    return False


def is_enumeration(toks, i, j, look=6):
    """Похоже на перечисление, а не на брошенный заход? `toks` — весь поток
    нормализованных слов, i и j (i < j) — начала двух дословно совпадающих
    заходов. True = резать нельзя, обе половины содержательны.

    Кандидат сначала раздвигается ВЛЕВО: детекторы цепляются за середину
    повтора («он сделал» вместо «где он сделал»), и союз-связка оказывается
    внутри совпавшего куска, а не перед вторым заходом."""
    while i > 0 and j - 1 > i and toks[i - 1] and toks[i - 1] == toks[j - 1]:
        i -= 1
        j -= 1
    m = 0
    while i + m < j and j + m < len(toks) and toks[i + m] and toks[i + m] == toks[j + m]:
        m += 1
    mid = toks[i + m:j]                       # хвост первого захода + связка
    if not mid or mid[-1] not in ENUM_LINKS:
        return False
    ahead = toks[j + m:j + m + look]
    return any(t and t not in _ENUM_EMPTY and not _stub_of(t, ahead)
               for t in mid[:-1])


def find_repeat_ranges(words, min_words=1, max_span=10, keep="last"):
    """Detect immediately-repeated word spans (re-takes / stutters) and return
    source-time ranges (start_s, end_s) to remove, plus a human log.
    keep='last' removes the earlier copy (typical good take is the retake)."""
    toks = [_norm(w["w"]) for w in words]
    n = len(words)
    drop = [False] * n
    log = []
    i = 0
    while i < n:
        if drop[i]:
            i += 1; continue
        best = None
        # try the longest repeated adjacent span first
        for L in range(min(max_span, (n - i) // 2), min_words - 1, -1):
            a = toks[i:i + L]
            b = toks[i + L:i + 2 * L]
            if a == b and all(t for t in a):
                best = L; break
        if best:
            L = best
            if keep == "last":
                rng = (i, i + L)              # drop first copy
            else:
                rng = (i + L, i + 2 * L)       # drop second copy
            for k in range(*rng):
                drop[k] = True
            phrase = " ".join(words[k]["w"] for k in range(i, i + L))
            log.append((words[rng[0]]["start"], words[rng[1]-1]["end"], phrase))
            i += L  # advance past the kept copy's start
        else:
            i += 1
    # merge dropped words into contiguous time ranges
    ranges = []
    for s, e, _ in log:
        if ranges and s - ranges[-1][1] < 0.05:
            ranges[-1] = (ranges[-1][0], e)
        else:
            ranges.append((s, e))
    return ranges, log


def find_restarts(words, min_span=2, max_span=10, max_gap=6, keep="last"):
    """Detect RE-STARTS: a phrase begun, then started over — the two attempts share
    a common beginning but the first is abandoned (its tail differs / is cut off).
    Signature: words[i:i+L] repeats at words[i+m:i+m+L] with m>=L (the extra m-L words
    are the abandoned tail). Generalizes find_repeat_ranges (which is the m==L exact
    case). Returns (time_ranges_to_remove, log) — same contract, so it drops straight
    into the cut via subtract_ranges. keep='last' drops the first (abandoned) attempt.
    min_span>=2 avoids cutting single-word stutters that may be intentional."""
    toks = [_norm(w["w"]) for w in words]
    n = len(words)
    drop = [False] * n
    log = []
    i = 0
    while i < n:
        if drop[i]:
            i += 1; continue
        best = None                                  # (L, m): span length, gap to repeat
        for L in range(min(max_span, n - i), min_span - 1, -1):
            a = toks[i:i + L]
            if not all(a):
                continue
            for m in range(L, L + max_gap + 1):      # m>=L: abandoned attempt length
                if i + m + L > n:
                    break
                if toks[i + m:i + m + L] == a:
                    best = (L, m); break
            if best:
                break
        if best:
            L, m = best
            if is_enumeration(toks, i, i + m):
                i += 1                                # перечисление, а не заход
                continue
            if keep == "last":
                a0, a1 = i, i + m                     # drop abandoned first attempt
            else:
                a0, a1 = i + m, i + m + L             # drop the retake instead
            for k in range(a0, a1):
                drop[k] = True
            phrase = " ".join(words[k]["w"] for k in range(a0, a1))
            log.append((words[a0]["start"], words[a1 - 1]["end"], phrase))
            i = a1 if keep == "last" else i + m
        else:
            i += 1
    ranges = []
    for s, e, _ in log:
        if ranges and s - ranges[-1][1] < 0.05:
            ranges[-1] = (ranges[-1][0], e)
        else:
            ranges.append((s, e))
    return ranges, log


def _ts(frames, fps=FPS):
    """Кадры -> время SRT. fps — частота, В КОТОРОЙ посчитаны кадры, а не всегда 60:
    у 25-кадровой секвенции деление на константу давало время в 2.4 раза меньше
    реального (subtitle_xml отдаёт сюда meta["fps"]). Перевод — общий, в
    core/subs.format_srt_time, своей копии с переносами тут нет."""
    return subs.format_srt_time(frames / fps)


def make_srt(sub_words, path, max_chars=42, max_gap_frames=36, min_cue_frames=18, fps=FPS):
    """Group output-timeline words (frames) into readable cues and write .srt.
    Keeps original case/punctuation. Timings match the edited timeline.
    fps — частота таймлайна, в кадрах которой пришли sub_words (дефолт прежний)."""
    cues = []
    cur = []
    for wd in sub_words:
        if cur:
            gap = wd["start"] - cur[-1]["end"]
            text = " ".join(x["w"] for x in cur)
            ends_sent = cur[-1]["w"][-1:] in ".!?…"
            if gap > max_gap_frames or len(text) >= max_chars or ends_sent:
                cues.append(cur); cur = []
        cur.append(wd)
    if cur:
        cues.append(cur)
    lines = []
    for i, c in enumerate(cues, 1):
        start = c[0]["start"]; end = max(c[-1]["end"], start + min_cue_frames)
        text = " ".join(x["w"] for x in c).strip()
        lines.append(f"{i}\n{_ts(start, fps)} --> {_ts(end, fps)}\n{text}\n")
    # атомарно: .srt — результат шага; пустой файл на месте живого вводит в заблуждение
    atomic_text_write(path, "\n".join(lines))
    return len(cues)


def subtract_ranges(segments, drop_ranges, pad=0.0):
    """Remove drop_ranges (seconds) from segments; return new segment list."""
    if not drop_ranges:
        return segments
    drops = sorted((max(0, a - pad), b + pad) for a, b in drop_ranges)
    out = []
    for s, e in segments:
        cur = [(s, e)]
        for da, db in drops:
            nxt = []
            for cs, ce in cur:
                if db <= cs or da >= ce:
                    nxt.append((cs, ce))
                else:
                    if cs < da:
                        nxt.append((cs, da))
                    if db < ce:
                        nxt.append((db, ce))
            cur = nxt
        out.extend((a, b) for a, b in cur if b - a > 0.05)
    return out
