# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Local word-level transcription via faster-whisper (GPU)."""
import json, os, hashlib, re
from core import cuda_env
from core.device import ct2_device
from core.fileio import atomic_json_dump
cuda_env.setup()

DEFAULT_MODEL_SIZE = "large-v3"


def words_cache_path(src, model_size=DEFAULT_MODEL_SIZE, engine=None, lang=None):
    """Путь кэша пословного транскрипта РЯДОМ С ИСХОДНИКОМ.

    Ключ включает модель/движок/язык. Раньше кэш звался просто `<src>.words.json`:
    прогон с `--model medium` молча отдавался при следующем запуске как large-v3,
    а `subtitle_xml` подхватывал чужой кэш и выдавал его за субтитры large-v3.
    Понять это можно было только вручную удалив файл рядом с камерой."""
    tag = hashlib.md5("|".join([str(model_size), str(engine or ""), str(lang or "")])
                      .encode("utf-8")).hexdigest()[:8]
    return os.path.splitext(src)[0] + f".words.{tag}.json"


def load_words_cache(path):
    """Читает кэш. Битый/пустой -> None, файл удаляется.

    Раньше чтение шло голым `json.load`: «Остановить» во время записи оставляло
    обрезанный JSON, и КАЖДЫЙ следующий запуск падал JSONDecodeError-ом, не
    подсказывая, что надо удалить файл рядом с исходником. Пустой список тоже
    считаем «нет кэша» — иначе тишина в записи навсегда фиксировала «0 слов»."""
    try:
        with open(path, encoding="utf-8") as f:
            words = json.load(f)
        if isinstance(words, list) and words:
            return words
    except Exception:
        pass
    try:
        os.remove(path)
    except Exception:
        pass
    return None


def save_words_cache(path, words):
    """Атомарная запись кэша (core.fileio.atomic_json_dump): прерывание не оставляет огрызок."""
    if not words:
        return                       # пустой транскрипт не кэшируем (см. load_words_cache)
    atomic_json_dump(path, words)


_MODEL = None  # cache (key, WhisperModel) so a batch loads large-v3 only once


def get_model(model_size="large-v3", device="cuda", compute_type="float16"):
    global _MODEL
    # Без NVIDIA CTranslate2 умеет только CPU (Metal/ROCm он не поддерживает вовсе),
    # и там нужен int8 вместо float16 — иначе падение на ровном месте.
    if device == "cuda" and compute_type == "float16":
        device, compute_type = ct2_device()
    key = (model_size, device, compute_type)
    if _MODEL is None or _MODEL[0] != key:
        if _MODEL is not None:
            release_model()                    # освободить VRAM старой модели ДО загрузки новой
        from faster_whisper import WhisperModel
        _MODEL = (key, WhisperModel(model_size, device=device, compute_type=compute_type))
    return _MODEL[1]


def release_model():
    """Free the cached Whisper model and its GPU memory (call after a batch finishes,
    so VRAM isn't held while the web UI idles)."""
    global _MODEL
    if _MODEL is None:
        return False
    _MODEL = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
    return True


# Авто-титры YouTube — НИКОГДА не произносятся вслух, режем всегда (по подстроке сегмента).
CREDITS = [
    "субтитры делал", "субтитры сделал", "субтитры создавал", "субтитры подготовил",
    "dimatorzok",
]
# Подписи с именами авторов титров («Редактор субтитров А.Семкин», «Корректор А. Егорова») —
# характерные галлюцинации Whisper. Режем всегда по исходному регистру с инициалом.
CREDITS_NAMED = re.compile(
    r"(?i:редактор\s+субтитров|корректор)\s+[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ]\w*"
)
# Голые слова («корректор», «редактор субтитров») без имени могут быть живой речью
# («корректор пришёл вовремя»). Режем только при низкой уверенности, как и CTA_BOILER.
CREDITS_BARE = [
    "редактор субтитров", "корректор",
]
# Дежурные концовки/призывы — их автор РЕАЛЬНО говорит («подписывайтесь», «ставьте лайки»,
# «спасибо за просмотр»). Whisper их же галлюцинирует на тишине/музыке. Поэтому режем ТОЛЬКО
# когда распозналось неуверенно (тишина/низкий logprob), а уверенную живую речь — оставляем.
CTA_BOILER = [
    "продолжение следует", "спасибо за просмотр", "спасибо за внимание",
    "подписывайтесь", "ставьте лайки", "до новых встреч", "продолжение в следующей",
    "subscribe", "thanks for watching",
]
HALLUCINATIONS = CREDITS + CREDITS_BARE + CTA_BOILER   # для обратной совместимости


def _is_hallucination(text):
    t = text.lower()
    return any(h in t for h in HALLUCINATIONS)


def _drop_segment(s):
    """Выкинуть сегмент? Титры YouTube и подписи с именем — всегда; явная галлюцинация
    на тишине — всегда; призывы (подписывайтесь и пр.) и голые роли (корректор) без имени —
    только если распозналось неуверенно (иначе это живая речь)."""
    raw = s.text or ""
    t = raw.lower()
    nsp = getattr(s, "no_speech_prob", 0.0); alp = getattr(s, "avg_logprob", 0.0)
    if any(h in t for h in CREDITS) or bool(CREDITS_NAMED.search(raw)):
        return True
    if nsp > 0.7 and alp < -0.6:                       # общий детектор галлюцинаций на не-речи
        return True
    low_conf = (nsp > 0.5 or alp < -0.7)
    if any(h in t for h in CTA_BOILER) and low_conf:
        return True                                    # призыв только при низкой уверенности
    if any(h in t for h in CREDITS_BARE) and low_conf:
        return True                                    # голая роль только при низкой уверенности
    return False


def transcribe(wav_path, model_size="large-v3", lang="ru", device="cuda",
               compute_type="float16", model=None):
    model = model or get_model(model_size, device, compute_type)
    segments, info = model.transcribe(
        wav_path, language=lang, word_timestamps=True,
        vad_filter=True,                    # skip non-speech -> kills most hallucinations
        condition_on_previous_text=False,   # stop runaway repetition loops
        no_speech_threshold=0.6)
    words = []
    for s in segments:
        # титры YouTube / галлюцинации на тишине — вон; живые призывы («подписывайтесь») — оставляем
        if _drop_segment(s):
            continue
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                words.append({"w": t, "start": float(w.start), "end": float(w.end)})
    return words


def transcribe_segments(wav_path, intervals=None, model_size="large-v3", lang="ru",
                        device="cuda", compute_type="float16", model=None):
    """Транскрибировать КАЖДЫЙ речевой интервал отдельным (изолированным) вызовом
    Whisper. Дубли, разделённые паузой, сохраняются, а не «причёсываются» в один —
    у модели нет сквозного контекста между тактами. Тайминги — глобальные (сек).
    Ловит переснятия через паузу; рестарт без паузы остаётся внутри одного интервала."""
    import numpy as np
    from faster_whisper.audio import decode_audio
    model = model or get_model(model_size, device, compute_type)
    SR = 16000
    audio = decode_audio(wav_path, sampling_rate=SR)   # работает и с wav, и с видео
    if intervals is None:
        from core import vad
        intervals = vad.speech_intervals(wav_path)
    words = []
    for (s, e) in intervals:
        a0 = max(0, int(s * SR)); a1 = min(len(audio), int(e * SR))
        if a1 - a0 < int(0.10 * SR):
            continue
        clip = np.ascontiguousarray(audio[a0:a1])
        segs, _ = model.transcribe(
            clip, language=lang, word_timestamps=True,
            vad_filter=False,                   # интервал уже вырезан нашим VAD
            condition_on_previous_text=False,
            no_speech_threshold=0.6)
        for seg in segs:
            if _drop_segment(seg):
                continue
            for w in (seg.words or []):
                t = w.word.strip()
                if t:
                    words.append({"w": t, "start": float(w.start) + s,
                                  "end": float(w.end) + s})
    return words


def _ensure_wav(media):
    """Любой медиа (wav/mp4/mov) -> временный 16kHz mono wav (для vad + сравнения)."""
    import os, tempfile, numpy as np
    from scipy.io import wavfile
    from faster_whisper.audio import decode_audio
    if media.lower().endswith(".wav"):
        return media
    audio = decode_audio(media, sampling_rate=16000)
    import uuid
    tmp = os.path.join(tempfile.gettempdir(), f"_ac_cmp_{uuid.uuid4().hex[:8]}.wav")
    wavfile.write(tmp, 16000, (np.clip(audio, -1, 1) * 32767).astype(np.int16))
    return tmp


def compare(media):
    """Сравнить ЦЕЛЬНУЮ транскрипцию (как сейчас) и ПО ИНТЕРВАЛАМ — видно, сохранились
    ли переснятия. Печатает обе + разбивку по интервалам речи."""
    from core import vad
    wav = _ensure_wav(media)
    intervals = vad.speech_intervals(wav)
    print(f"Речевых интервалов (границы = паузы >0.30с): {len(intervals)}\n")
    whole = transcribe(wav)
    per = transcribe_segments(wav, intervals)
    print(f"=== ЦЕЛЬНО (как сейчас): {len(whole)} слов ===")
    print(" ".join(w["w"] for w in whole))
    print(f"\n=== ПО ИНТЕРВАЛАМ: {len(per)} слов ===")
    for k, (s, e) in enumerate(intervals):
        seg = [w["w"] for w in per if s - 0.05 <= w["start"] < e + 0.5]
        print(f"[{k:2d}] {s:6.1f}-{e:6.1f}с  {' '.join(seg)}")
    print(f"\nΔ слов: по интервалам {len(per)-len(whole):+d} против цельного "
          f"({len(whole)} -> {len(per)}). Больше слов ≈ сохранились дубли.")


if __name__ == "__main__":
    import argparse, io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description="Whisper транскрипция / сравнение режимов")
    ap.add_argument("media", nargs="?", default="reelsi/_a1.wav", help="wav или видео")
    ap.add_argument("--compare", action="store_true", help="цельно vs по интервалам")
    ap.add_argument("--segments", action="store_true", help="только по интервалам -> json")
    ap.add_argument("--out", default="reelsi/_words.json")
    a = ap.parse_args()
    if a.compare:
        compare(a.media)
    else:
        ws = transcribe_segments(a.media) if a.segments else transcribe(a.media)
        json.dump(ws, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"transcribed {len(ws)} words -> {a.out}")
