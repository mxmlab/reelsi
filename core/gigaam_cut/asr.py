# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Транскрипция GigaAM и выравнивание слов.

GigaAM v3-CTC слушает файл целиком и сам отдаёт пословные тайминги: текст и тайминги
из ОДНОЙ модели, отдельный forced-align не нужен и ничего не расползается.
Здесь же освобождение VRAM.
"""
from __future__ import annotations
import re
from typing import Any
import numpy as np
import soundfile as sf
from .tune import CHUNK, SR
from core.app_meta import console_emit, wrap_emit
from core.umsg import ReelsiError



# --------------------------------------------------------------------------- #
# Утилиты VRAM / аудио
# --------------------------------------------------------------------------- #
def _free_torch() -> None:
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU недоступны — чистить нечего


def _norm(t: str | None) -> str:
    return " ".join(re.findall(r"[а-яёa-z]+", (t or "").lower()))


# --------------------------------------------------------------------------- #
# Шаг 1+2 одним махом: GigaAM целиком -> пословные тайминги (word_timestamps)
# --------------------------------------------------------------------------- #
def _word(w: Any, t0: float = 0.0) -> dict[str, Any]:
    """gigaam.Word -> наш словарь [{w,start,end}] (t0 — сдвиг окна, сек).
    `prob` gigaam не отдаёт; если появится — подхватим (её показывает self-check)."""
    d = {"w": w.text, "start": round(float(w.start) + t0, 3),
         "end": round(float(w.end) + t0, 3)}
    p = getattr(w, "prob", None)
    if p is not None:
        d["prob"] = round(float(p), 3)
    return d


def transcribe_words_whole(
    wav_path: str, emit: Any = console_emit, model_name: str = "v3_ctc"
) -> tuple[str, list[dict[str, Any]]]:
    """GigaAM слушает весь файл и САМ отдаёт слова с таймингами
    (word_timestamps=True) — текст и тайминги из одной модели, forced-align
    (wav2vec2) не нужен. Возвращает (full_text, [{"w","start","end"}] в сек).

    model_name — какая голова GigaAM слушает (все дают пословные тайминги):
      v3_ctc               — акустический CTC: быстрый, для НАРЕЗКИ (нужны тайминги);
      v3_rnnt              — RNN-T: внутренняя языковая модель правит слова, точнее текст;
      v3_e2e_ctc/_e2e_rnnt — то же + пунктуация и нормализация текста (для СУБТИТРОВ);
      multilingual[_large]_ctc — 70+ языков.

    pyannote-longform отключён (гейтед-веса + виндовые баги pyannote/speechbrain),
    поэтому слушаем окнами ~18с со швами в САМОЙ ТИХОЙ точке (чтобы не разрезать
    слово на границе окна), transcribe(word_timestamps=True) на окно, тайминги
    сдвигаем на начало окна. Модель выгружается сразу после."""
    import gigaam
    emit("GigaAM: загрузка {model} (whole-file, word_timestamps)…", model=model_name, flush=True)
    model = gigaam.load_model(model_name)
    try:
        words = _transcribe_words_manual(model, wav_path, emit=emit)
    finally:
        del model
        _free_torch()
    words = [w for w in words if w["w"].strip()]
    words.sort(key=lambda w: w["start"])
    full_text = " ".join(w["w"] for w in words)
    emit("GigaAM whole ({model}): {count} слов (родные тайминги)",
         model=model_name, count=len(words), flush=True)
    return full_text, words


def transcribe_words_for_cut(
    wav_path: str, engine: str = "gigaam", emit: Any = console_emit
) -> tuple[str, list[dict[str, Any]]]:
    """Единая точка входа для нарезки: распознавание слов с родными таймингами.

    Принимает движок с признаком cut=True из каталога asr_backends (CTC):
    - gigaam* -> transcribe_words_whole(wav_path, emit=emit, model_name=<голова из поля 'gigaam'>);
    - ctc:* -> asr_backends.transcribe_words(wav_path, engine=engine), full_text из слов;
    - движки без cut=True (RNN-T, whisper, omni) -> ValueError с понятным объяснением.

    Возвращает (full_text, words), где words — [{"w", "start", "end"}].
    """
    emit = wrap_emit(emit)
    from core import asr_backends
    meta = asr_backends.engine_meta(engine)
    if not meta or not meta.get("cut"):
        raise ValueError(
            f"Движок «{engine}» не годен для нарезки (требуются родные пословные тайминги CTC, cut=True)"
        )

    if meta.get("gigaam"):
        model_name = meta["gigaam"]
        return transcribe_words_whole(wav_path, emit=emit, model_name=model_name)
    else:
        words = asr_backends.transcribe_words(wav_path, engine=meta["id"], emit=emit)
        words = [w for w in (words or []) if (w.get("w") or "").strip()]
        words.sort(key=lambda w: w["start"])
        full_text = " ".join(w["w"] for w in words)
        return full_text, words


def _quiet_cut(x: Any, lo: int, hi: int, sr: int, frame: float = 0.05) -> int:
    """Самая тихая точка (центр самого тихого 50мс-кадра) в x[lo:hi]."""
    f = max(1, int(frame * sr))
    seg = np.abs(x[lo:hi].astype(np.float32))
    nf = len(seg) // f
    if nf < 1:
        return hi
    rms = seg[:nf * f].reshape(nf, f).mean(axis=1)
    return lo + int(np.argmin(rms)) * f + f // 2


def _transcribe_words_manual(
    model: Any, wav_path: str, emit: Any = console_emit, win: float = 18.0, search: float = 6.0, sr: int = SR
) -> list[dict[str, Any]]:

    """Фолбэк без pyannote: окна ~win сек, но шов кладём в самую тихую точку
    последних `search` сек окна — рез между окнами не попадает в слово.
    Каждое окно transcribe(word_timestamps=True), тайминги + начало окна.

    Окно 18с и torch.cuda.empty_cache() ПОСЛЕ каждого окна — раньше этот фолбэк
    ронял процесс с кодом 3221225477 (access violation): на Windows WDDM пик VRAM
    в тихом пооконном GPU-цикле переполнял память без ловимого OOM и убивал драйвер.
    Меньше окно + сброс кэша держат запас; прогресс печатаем — крах локализуется."""
    import os, tempfile
    a, _sr = sf.read(wav_path, dtype="int16")
    if _sr != sr:
        emit("  ! wav не 16кГц — GigaAM ожидает 16кГц, возможны артефакты", flush=True)
    if a.ndim > 1:
        a = a.mean(1).astype("int16")
    n = len(a)
    step = int(win * sr)
    words: list[dict[str, Any]]
    pos: int
    nwin: int
    words, pos, nwin = [], 0, 0
    est = max(1, int(n / step) + 1)              # грубая оценка числа окон для прогресса
    while pos < n:
        if n - pos > step:
            s1 = _quiet_cut(a, pos + step - int(search * sr), pos + step, sr)
        else:
            s1 = n
        if s1 - pos < int(0.5 * sr):
            break
        tmp = os.path.join(tempfile.gettempdir(), "_gc_win_%d_%d.wav" % (os.getpid(), pos))
        try:
            sf.write(tmp, a[pos:s1], sr, subtype="PCM_16")
            r = model.transcribe(tmp, word_timestamps=True)
            t0 = pos / sr
            for w in (r.words or []):
                if w.text:
                    words.append(_word(w, t0))
            nwin += 1
        except ReelsiError: raise
        except Exception as ex:
            emit("  окно {sec}s не расшифровалось: {err}", sec=pos // sr, err=str(ex), flush=True)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass  # временное окно уже убрано
            _free_torch()                        # держим VRAM в узде (Windows WDDM)
        if nwin % 20 == 0 and nwin:
            emit("  longform-окна (тихие швы): {cur}/~{est}…", cur=nwin, est=est, flush=True)
        pos = s1
    emit("  longform-окна (тихие швы): {chunks} кусков, {words} слов",
         chunks=nwin, words=len(words), flush=True)
    return words


# --------------------------------------------------------------------------- #
# Шаг 2: forced-align полного текста -> пословные тайминги
# --------------------------------------------------------------------------- #
def align_full(
    wav_path: str, text: str, emit: Any = console_emit, device: str = "cuda"
) -> list[dict[str, Any]]:
    """Выровнять полный текст GigaAM по звуку целиком (wav2vec2).
    Эмиссии считаем чанками по CHUNK сек, но forced_align делаем ОДИН на весь
    файл по склеенным эмиссиям (текст-то один общий — по-чанковый align с полным
    текстом сжимал бы все слова в первый чанк). Кадры переводим в секунды через
    ПО-ЧАНКОВУЮ карту «кадр -> время»: у каждого чанка свой точный
    fd = (a1-a0)/T_chunk, поэтому дрейф conv-фронтенда wav2vec2 не копится
    даже на больших файлах.
    Фолбэк (OOM/ошибка): пооконный align_text с пропорциональной разбивкой слов."""
    emit = wrap_emit(emit)
    import torch, torchaudio
    from core.falign import get_model
    proc, model, dev = get_model(device)
    vocab = proc.tokenizer.get_vocab()
    blank = proc.tokenizer.pad_token_id
    delim = vocab.get("|")

    wav, sr = torchaudio.load(wav_path)
    audio = wav.mean(0) if wav.shape[0] > 1 else wav[0]
    if sr != SR:
        audio = torchaudio.functional.resample(audio, sr, SR)
    dur = len(audio) / SR

    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]

    def norm(w: str) -> str:
        return "".join(c for c in w.lower() if c in vocab and c != "|")

    # Build targets + meta (word index per token, -1 for delimiter)
    targets: list[Any]
    meta: list[int]
    targets, meta = [], []
    for k, w in enumerate(words):
        s = norm(w)
        if not s:
            continue
        if targets:
            targets.append(delim); meta.append(-1)
        for c in s:
            targets.append(vocab[c]); meta.append(k)
    if not targets:
        return []

    # --- эмиссии чанками + карта «кадр -> секунды» с точным fd каждого чанка ---
    step = int(CHUNK * SR)
    em_chunks: list[Any] = []
    frame_t: list[float] = []                     # frame_t[i] = время НАЧАЛА кадра i, сек
    for a0 in range(0, len(audio), step):
        a1 = min(a0 + step, len(audio))
        clip = audio[a0:a1].to(dev)
        if clip.numel() < SR // 8:
            continue
        with torch.inference_mode():
            logits = model(clip.unsqueeze(0)).logits
            em = torch.log_softmax(logits, dim=-1)  # [1, T_chunk, V]
        T_chunk = em.shape[1]
        if T_chunk == 0:
            continue
        em_chunks.append(em.cpu())
        fd_chunk = (a1 - a0) / SR / T_chunk         # точный fd ЭТОГО чанка
        t0 = a0 / SR
        frame_t.extend(t0 + i * fd_chunk for i in range(T_chunk))
    if not em_chunks:
        return []
    frame_t.append(dur)              # сентинел: конец последнего кадра

    # --- ОДИН forced_align на весь файл (полный текст = полные эмиссии) ---
    try:
        em_all = torch.cat(em_chunks, dim=1).to(dev)      # [1, T, V]
        aln, sc = torchaudio.functional.forced_align(
            em_all, torch.tensor([targets], device=dev), blank=blank)
        spans = torchaudio.functional.merge_tokens(aln[0], sc[0])
        word_times: dict[int, list[float]]
        ti: int
        word_times, ti = {}, 0       # word_idx -> [start_sec, end_sec]
        last = len(frame_t) - 1
        for sp in spans:
            # «|» (разделитель слов в target) — НЕ blank: без отсева его span съедал
            # meta-запись (ti сдвигался) и все следующие слова выравнивались со сдвигом
            # на одну позицию (аудит, систематическая ошибка таймингов субтитров).
            # Разделители в meta и так перешагиваются в while ниже — здесь просто не
            # трогаем ti.
            if sp.token in (blank, delim):
                continue
            while ti < len(meta) and meta[ti] == -1:
                ti += 1
            if ti >= len(meta):
                break
            wk = meta[ti]; ti += 1
            start_sec = frame_t[min(sp.start, last)]
            end_sec = frame_t[min(sp.end, last)]
            if wk in word_times:
                word_times[wk][0] = min(word_times[wk][0], start_sec)
                word_times[wk][1] = max(word_times[wk][1], end_sec)
            else:
                word_times[wk] = [start_sec, end_sec]
    except ReelsiError: raise
    except Exception as e:
        emit("  align_full не удался ({err_type}: {err}) — фолбэк по окнам",
             err_type=type(e).__name__, err=str(e), flush=True)
        return _align_full_chunked(audio, words, dur, emit)

    if not word_times:
        emit("  align_full: не выровнено ни одного слова — фолбэк по окнам", flush=True)
        return _align_full_chunked(audio, words, dur, emit)

    out: list[dict[str, Any]] = [{"w": words[k], "start": round(word_times[k][0], 3),
            "end": round(word_times[k][1], 3)} for k in sorted(word_times.keys())]

    # хвост как в falign: не откусывать окончания
    TAIL = 0.12
    for i, w in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else w["end"] + TAIL
        w["end"] = round(min(w["end"] + TAIL, max(w["end"], nxt - 0.02)), 3)
    emit("  align_full: {aligned}/{total} слов выровнено",
         aligned=len(out), total=len(words), flush=True)
    return out


def _align_full_chunked(
    audio: Any, words: list[str], dur: float, emit: Any = console_emit, device: str = "cuda"
) -> list[dict[str, Any]]:
    """Фолбэк: режем аудио на окна CHUNK сек, каждое окно align_text'ом с
    пропорциональной долей слов. Менее точен на стыках окон, но не падает."""
    from core import falign
    n = len(words)
    out: list[dict[str, Any]]
    wi: int
    out, wi = [], 0
    step = int(CHUNK * SR)
    for a0 in range(0, len(audio), step):
        a1 = min(a0 + step, len(audio))
        w0 = wi
        w1 = int(round(a1 / len(audio) * n)) if a1 < len(audio) else n
        w1 = max(w1, w0)
        seg = words[w0:w1]
        wi = w1
        if not seg:
            continue
        clip = audio[a0:a1].cpu().numpy().astype("float32")
        try:
            sw = falign.align_text(clip, " ".join(seg))
        except ReelsiError: raise
        except Exception:
            sw = []
        for w in sw:
            out.append({"w": w["w"], "start": round(a0 / SR + w["start"], 3),
                        "end": round(a0 / SR + w["end"], 3)})
    emit("  align_full (chunked): {aligned}/{total} слов",
         aligned=len(out), total=n, flush=True)
    return out
