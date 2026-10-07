# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Local word-level transcription via faster-whisper (GPU)."""
import json, os, hashlib, re
from typing import Any, Callable, Sequence
from core import cuda_env
from core.app_meta import console_emit
from core.device import (ct2_device, free_vram_mib, is_oom_error, vram_total_mib,
                         whisper_fits)
from core.fileio import atomic_json_dump
from core.umsg import ReelsiError, cli_error, umsg
cuda_env.setup()

DEFAULT_MODEL_SIZE = "large-v3"

# Частота, которую ждёт Whisper: массив для `model.transcribe` обязан быть моно
# float32 именно на ней — иначе faster-whisper молча считает свои 16 кГц.
SR = 16000


def read_mono16k(wav_path: str) -> Any:
    """Прочитать wav в массив float32 16 кГц моно — то, что ждёт `model.transcribe`.

    Зачем не отдать путь. faster-whisper декодирует путь своим `decode_audio` через
    PyAV; PyAV 19 убрал аргумент `metadata_errors`, который faster-whisper 1.2 ещё
    передаёт, и КАЖДЫЙ вызов с путём падал `TypeError: open() got an unexpected
    keyword argument 'metadata_errors'` — на свежей установке субтитры не собирались
    вовсе. С массивом PyAV не участвует. Ремаплинг — только когда частота не 16 кГц
    (обычные пути проекта: wav от `core.sync.extract_audio` и склейка selfcheck).
    """
    import numpy as np
    import soundfile as sf
    try:
        a, sr = sf.read(wav_path, dtype="float32", always_2d=True)
    except ReelsiError:
        raise
    except Exception as e:
        raise ReelsiError(umsg("audio_read_failed",
                               f"{os.path.basename(wav_path)}: {type(e).__name__}: {e}",
                               path=wav_path, err=f"{type(e).__name__}: {e}"))
    y = a.mean(axis=1)                 # любой канал -> моно: моно остаётся собой
    if int(sr) != SR:
        import librosa
        y = librosa.resample(y, orig_sr=int(sr), target_sr=SR)
    return np.ascontiguousarray(y, dtype=np.float32)


def words_cache_path(src: str, model_size: str = DEFAULT_MODEL_SIZE, engine: str | None = None, lang: str | None = None) -> str:
    """Путь кэша пословного транскрипта РЯДОМ С ИСХОДНИКОМ.

    Ключ включает модель/движок/язык. Раньше кэш звался просто `<src>.words.json`:
    прогон с `--model medium` молча отдавался при следующем запуске как large-v3,
    а `subtitle_xml` подхватывал чужой кэш и выдавал его за субтитры large-v3.
    Понять это можно было только вручную удалив файл рядом с камерой."""
    tag = hashlib.md5("|".join([str(model_size), str(engine or ""), str(lang or "")])
                      .encode("utf-8")).hexdigest()[:8]
    return os.path.splitext(src)[0] + f".words.{tag}.json"


def load_words_cache(path: str) -> list[dict[str, Any]] | None:
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
    except ReelsiError: raise
    except Exception:
        pass  # кэш слов битый — распознаем заново
    try:
        os.remove(path)
    except ReelsiError: raise
    except OSError:
        pass  # битый кэш уже удалён
    return None


def save_words_cache(path: str, words: Sequence[dict[str, Any]] | None) -> None:
    """Атомарная запись кэша (core.fileio.atomic_json_dump): прерывание не оставляет огрызок."""
    if not words:
        return                       # пустой транскрипт не кэшируем (см. load_words_cache)
    atomic_json_dump(path, words)


_MODEL: tuple[tuple[str, str, str], Any] | None = None  # cache (key, WhisperModel) so a batch loads large-v3 only once

# Ступени Whisper по убыванию аппетита — по ним идём вниз, когда модель не влезает.
# Заказные имена остаются в порядке пользователя, но большая модель всегда может
# заменить меньшую: смысл шага — «взять ту, что поместится», а не «строго эту».
_WHISPER_LADDER = ("large-v3", "medium", "small")

# Сколько раз пробуем разную точность на ОДНОЙ ступени: float16 → int8_float16.
_WHISPER_DTYPES = ("float16", "int8_float16")


def _pick_whisper_candidates(model_size: str, compute_type: str) -> list[tuple[str, str]]:
    """Цепочка «модель+точность» от заказанной к самой скромной, без повторов.

    Оценки и порядок — в `core/device.py` (`WHISPER_VRAM_MIB`): сначала пробуем
    запрошенную точность в половинной, затем ту же модель в int8 (весов вдвое
    меньше), затем ступень ниже — как и советует разбор нехватки памяти. Ошибка
    здесь не стоит ничего: неудачная попытка ловится вызывающим и заменяется
    следующей.
    """
    if model_size not in _WHISPER_LADDER:
        return [(model_size, compute_type)]      # своя/локальная модель: лестница не наша
    sizes = list(_WHISPER_LADDER[_WHISPER_LADDER.index(model_size):])
    order: list[str] = []
    for size in sizes:
        for compute in _WHISPER_DTYPES:
            if size == model_size and compute == compute_type:
                order.insert(0, compute)         # заказную точность пробуем ПЕРВОЙ
            elif compute not in order:
                order.append(compute)
    return [(size, compute) for size in sizes for compute in order]


def _fallback_note(from_size: str, from_ct: str, to_size: str, to_ct: str) -> str:
    """Строка «что взяли вместо чего» — с размером карты, чтобы причина была видна."""
    vram = vram_total_mib()
    card = "{:.1f} ГБ".format(vram / 1024) if vram else "карта"
    return (f"{card}: {from_size} в {from_ct} не влезла — "
            f"взяли {to_size}/{to_ct}")


def _walk_transcribe(wav_path: str, candidates: list[tuple[str, str]], lang: str,
                     make_model: Callable[[str, str], Any],
                     attempt: Callable[[Any], list[dict[str, Any]]],
                     emit: Callable[..., Any] | None, where: str) -> list[dict[str, Any]]:
    """Перебрать цепочку «модель+точность» до первого успеха.

    Падение по нехватке памяти — не поломка, а сигнал взять ступень скромнее:
    `large-v3` в float16 на карте 3.7 ГБ не влезает (~3 ГБ весов + контекст), а на
    `int8_float16` тот же файл считается. Всё остальное (нет файла модели, битые
    веса) наверх уходит сразу: повторять его на трёх размерах — терять время и
    прятать настоящую причину.

    Между попытками модель освобождается ЯВНО: на Windows занятая видеопамять не
    даёт честный OOM, а вешает машину — ждать сборщик мусора тут нельзя.
    """
    emit_fn = emit or console_emit
    last_oom: BaseException | None = None
    for i, (size, ct) in enumerate(candidates):
        fits = whisper_fits(size, ct, free_vram_mib())
        # Заказанную ступень пробуем ВСЕГДА, даже если по нашей оценке она не влезает:
        # оценка примерная, а живая попытка честная — и именно её падение даёт
        # настоящую причину. Сверка по карте решает только про ЗАПАСНЫЕ ступени:
        # их пропускаем, чтобы не занимать карту впустую.
        if not fits and i:
            emit_fn("  ↷ {size}/{ct} пропущена: не влезает в свободную память",
                    size=size, ct=ct)
            continue
        if i:
            emit_fn("  ↷ не влезло — пробую {size}/{ct}", size=size, ct=ct)
        try:
            return attempt(make_model(size, ct))
        except ReelsiError:
            raise
        except Exception as e:
            if not is_oom_error(e):
                raise                            # не память — это настоящая ошибка
            last_oom = e
            release_model()                      # соседняя ступень должна получить карту чистой
            if i + 1 < len(candidates):
                nxt, nxt_ct = candidates[i + 1]
                emit_fn("  ⚠ " + _fallback_note(size, ct, nxt, nxt_ct))
    vram = vram_total_mib()
    card = f"{vram / 1024:.1f}" if vram else "?"
    tried = ", ".join(f"{s}/{c}" for s, c in candidates)
    # umsg("код", …) — одной строкой: так код видит сторож словаря
    # (`tests/test_i18n.py::_backend_err_codes`), иначе перевод молча не проверяется.
    raise ReelsiError(umsg("whisper_gpu_fallback",
        f"Карта {card} ГБ: не влезла ни одна ступень Whisper ({tried}) и в половинной "
        f"точности, и в int8. Возьми модель меньше в настройках субтитров. "
        f"Последняя ошибка: {str(last_oom)[:200]}",
        where=where, tried=tried, card=card, err=str(last_oom)[:200]))


def _transcribe_with(model: Any, wav_path: str, lang: str) -> list[dict[str, Any]]:
    """Один проход распознавания готовой моделью (тело `transcribe`, без выбора модели)."""
    segments, info = model.transcribe(
        read_mono16k(wav_path), language=lang, word_timestamps=True,
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


def get_model(model_size: str = "large-v3", device: str = "cuda", compute_type: str = "float16") -> Any:
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


def release_model() -> bool:
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
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU недоступны — чистить нечего
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


def _is_hallucination(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in HALLUCINATIONS)


def _drop_segment(s: Any) -> bool:
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


def transcribe(wav_path: str, model_size: str = "large-v3", lang: str = "ru", device: str = "cuda",
               compute_type: str = "float16", model: Any = None,
               emit: Callable[..., Any] | None = console_emit) -> list[dict[str, Any]]:
    """Путь wav -> слова. Сигнатура прежняя, а ВНУТРЬ модели уходит массив
    (`read_mono16k`): с путём faster-whisper декодировал его PyAV'ом и падал на
    свежей установке (см. причину в `read_mono16k`).

    Модель, которая не влезла в карту, — не конец работы: цепочка ступеней
    (`_pick_whisper_candidates`) пробует ту же модель в int8, затем размер меньше,
    и лишь когда не вышло ничего, поднимает `ReelsiError` с кодом и причиной.
    Переданная снаружи готовая `model` повтору не подлежит — её выбрал вызывающий.
    """
    if model is not None:
        return _transcribe_with(model, wav_path, lang)
    candidates = _pick_whisper_candidates(model_size, compute_type)
    return _walk_transcribe(
        wav_path, candidates, lang,
        make_model=lambda size, ct: get_model(size, device, ct),
        attempt=lambda m: _transcribe_with(m, wav_path, lang),
        emit=emit, where=model_size)


def transcribe_segments(wav_path: str, intervals: Sequence[tuple[float, float]] | None = None, model_size: str = "large-v3", lang: str = "ru",
                        device: str = "cuda", compute_type: str = "float16", model: Any = None,
                        emit: Callable[..., Any] | None = console_emit) -> list[dict[str, Any]]:
    """Транскрибировать КАЖДЫЙ речевой интервал отдельным (изолированным) вызовом
    Whisper. Дубли, разделённые паузой, сохраняются, а не «причёсываются» в один —
    у модели нет сквозного контекста между тактами. Тайминги — глобальные (сек).
    Ловит переснятия через паузу; рестарт без паузы остаётся внутри одного интервала.

    Модель берётся запасным путём — как в `transcribe`: без него нехватка памяти на
    первом же интервале роняла весь ролик, хотя ступень пониже на этой карте
    считается. Готовая `model` снаружи повтору не подлежит."""
    from faster_whisper.audio import decode_audio
    from core import vad
    if intervals is None:
        intervals = vad.speech_intervals(wav_path)
    SR = 16000
    audio = decode_audio(wav_path, sampling_rate=SR)   # работает и с wav, и с видео
    if model is None:
        def _attempt(m: Any) -> list[dict[str, Any]]:
            return _run_segments(m, audio, intervals or [], lang, SR)

        return _walk_transcribe(
            wav_path, _pick_whisper_candidates(model_size, compute_type), lang,
            make_model=lambda size, ct: get_model(size, device, ct),
            attempt=_attempt, emit=emit, where=model_size)
    return _run_segments(model, audio, intervals or [], lang, SR)


def _run_segments(model: Any, audio: Any, intervals: Sequence[tuple[float, float]],
                  lang: str, sr: int) -> list[dict[str, Any]]:
    """Все речевые интервалы одной уже загруженной моделью (тело `transcribe_segments`).

    Отдельной функцией, потому что запасной путь по памяти повторяет не загрузку, а
    весь проход: готовая модель не знает, в какую ступень её выбрали.
    """
    import numpy as np
    words = []
    for (s, e) in intervals:
        a0 = max(0, int(s * sr)); a1 = min(len(audio), int(e * sr))
        if a1 - a0 < int(0.10 * sr):
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


def _ensure_wav(media: str) -> str:
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


def compare(media: str) -> None:
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
    try:
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
    except ReelsiError as e:
        cli_error(e)
