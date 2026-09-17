# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Pluggable ASR backends — единый реестр «кто слушает звук».

Один источник истины: имя движка -> `transcribe_words(wav_path, engine=…) ->
[{"w","start","end"[,"prob"]}]` (секунды от начала переданного wav). Отсюда
берут движки И субтитры (шаг 2 «Разметка»), И самопроверка стыков на главной.

Имена движков:
    "whisper" / "whisper:large-v3" / "whisper:medium" / "whisper:small"
    "whisper.cpp:large-v3" / "whisper.cpp:medium" / "whisper.cpp:small"
                     — нативный whisper-cli (whisper_cpp.py): Metal на Mac,
                       Vulkan на AMD; без пословной вероятности
    "gigaam"                 — GigaAM-v3 (RU), CTC, родные тайминги
    "ctc:<ключ>"             — любая CTC-модель из `asr_engines.json` (другие языки)
    "omni"                   — Omni (Qwen/облако), фразы -> слова интерполяцией

Метаданные каждого движка (`engines()`) описывают ЯЗЫК и способность отдавать
пословную ВЕРОЯТНОСТЬ (`prob`). Вероятность — не украшение: на ней держится
самопроверка стыков (слово, обрезанное катом, получает низкую вероятность),
поэтому движки без неё в селектор самопроверки не попадают.

Добавить язык = дописать запись в `asr_engines.json` (см. файл), код не трогать.

Каждый адаптер сам следит за VRAM (зовёт `aicut.unload_ours()` на входе) и
возвращает ОДИН И ТОТ ЖЕ пословный контракт, на который расчитан `xmlbuild`.
Движок по умолчанию — "whisper", поэтому нетронутый селектор ничего не меняет.
"""
import os, json, subprocess, tempfile, logging
from core import paths
from core.app_meta import child_env, console_emit, module_cmd, wrap_emit

ENGINES_JSON = paths.data("asr_engines.json")

# name -> callable(wav_path, **opts) -> [{"w","start","end"[,"prob"]}]
ASR_BACKENDS = {}

WHISPER_SIZES = ["large-v3", "medium", "small"]

# Встроенные движки. lang: 'multi' | ISO-код; prob: даёт ли пословную вероятность
# (нужна самопроверке); subs/selfcheck/cut: где движок предлагается в UI.
# cut: True — отдаёт родные границы звучания слова (CTC), годен для нарезки;
# False — RNN-T (эмиссия токенов), whisper (грубые границы) или omni (без таймингов).
_BUILTIN = [{"id": "whisper:%s" % s, "label": "Whisper %s" % s, "lang": "multi",
             "kind": "whisper", "prob": True, "subs": s == "large-v3", "selfcheck": True, "cut": False}
            for s in WHISPER_SIZES] + [
    # GigaAM: один и тот же энкодер, разные головы. CTC — чистая акустика (быстро,
    # для нарезки); RNN-T — внутренняя языковая модель ПРАВИТ слова; e2e — сверх
    # того пунктуация и нормализация текста (это то, что нужно субтитрам).
    {"id": "gigaam", "label": "GigaAM-v3 (RU, CTC)", "lang": "ru", "kind": "ctc",
     "prob": False, "subs": True, "selfcheck": True, "cut": True, "gigaam": "v3_ctc"},
    # RNN-T-головы в самопроверку и нарезку НЕ пускаем: они отдают кадр эмиссии токена, а не
    # границы звучания (см. _widen) — искать «рез посреди слова» и резать по ним нельзя.
    {"id": "gigaam:v3_rnnt", "label": "GigaAM-v3 RNN-T (RU, точнее слова)", "lang": "ru",
     "kind": "ctc", "prob": False, "subs": True, "selfcheck": False, "cut": False, "gigaam": "v3_rnnt"},
    {"id": "gigaam:v3_e2e_rnnt", "label": "GigaAM-v3 e2e RNN-T (RU, слова + пунктуация)",
     "lang": "ru", "kind": "ctc", "prob": False, "subs": True, "selfcheck": False, "cut": False,
     "gigaam": "v3_e2e_rnnt"},
    {"id": "gigaam:multilingual_large_ctc", "label": "GigaAM multilingual 600M (70+ языков, CTC)",
     "lang": "multi", "kind": "ctc", "prob": False, "subs": True, "selfcheck": True, "cut": True,
     "gigaam": "multilingual_large_ctc"},
    {"id": "omni", "label": "Omni (Qwen / облако)", "lang": "multi", "kind": "omni",
     "prob": False, "subs": True, "selfcheck": False, "cut": False},
    # whisper.cpp: те же веса Whisper, но нативный движок — единственный способ
    # дать Mac (Metal) и AMD (Vulkan) транскрипцию быстрее CPU (CTranslate2 их
    # не поддерживает). Пословной вероятности нет, а самопроверка стыков на ней
    # держится — поэтому selfcheck: False (как RNN-T-головы GigaAM).
    {"id": "whisper.cpp:large-v3", "label": "whisper.cpp large-v3 (Metal/Vulkan)",
     "lang": "multi", "kind": "whisper_cpp", "prob": False, "subs": True,
     "selfcheck": False, "cut": False},
    {"id": "whisper.cpp:medium", "label": "whisper.cpp medium (Metal/Vulkan)",
     "lang": "multi", "kind": "whisper_cpp", "prob": False, "subs": True,
     "selfcheck": False, "cut": False},
    {"id": "whisper.cpp:small", "label": "whisper.cpp small (Metal/Vulkan)",
     "lang": "multi", "kind": "whisper_cpp", "prob": False, "subs": True,
     "selfcheck": False, "cut": False},
]


def _custom():
    """CTC-движки других языков из `asr_engines.json` (правится руками).
    Формат записи: {"id","label","lang","model"[,"device"]}. Битый JSON или не тот тип
    пишут предупреждение в лог и возвращают пустой список, не роняя UI."""
    if not os.path.exists(ENGINES_JSON):
        return []
    try:
        with open(ENGINES_JSON, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as exc:
        logging.getLogger(__name__).warning("Не удалось прочитать %s: %s", ENGINES_JSON, exc)
        return []

    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        engines_val = raw.get("engines")
        if not isinstance(engines_val, list):
            logging.getLogger(__name__).warning("%s: поле 'engines' должно быть списком", ENGINES_JSON)
            return []
        items = engines_val
    else:
        logging.getLogger(__name__).warning("%s: ожидается список или словарь с ключом 'engines'", ENGINES_JSON)
        return []

    out = []
    for e in items:
        if not isinstance(e, dict):
            continue
        model = e.get("model")
        if not isinstance(model, str) or not model:
            continue
        if any(e.get(k) is not None and not isinstance(e.get(k), str) for k in ("id", "label", "lang", "device")):
            continue
        eid = e.get("id") or ("ctc:" + (e.get("lang") or model).split("/")[-1])
        out.append({"id": eid if eid.startswith("ctc:") else "ctc:" + eid,
                    "label": e.get("label") or eid, "lang": e.get("lang") or "?",
                    "kind": "ctc", "prob": True, "subs": True, "selfcheck": True, "cut": True,
                    "model": model, "device": e.get("device") or "cuda"})
    return out


def engines():
    """Все движки: встроенные + пользовательские (для UI и валидации)."""
    return _BUILTIN + _custom()


def engine_meta(engine):
    """Метаданные движка по имени. «whisper» = «whisper:large-v3»."""
    engine = engine or "whisper"
    if engine == "whisper":
        engine = "whisper:large-v3"
    for e in engines():
        if e["id"] == engine:
            return e
    return None


def transcribe_words(wav_path, engine="whisper", emit=console_emit, use_terms=True, **opts):
    """Единая точка входа. Неизвестный движок -> whisper (поведение по умолчанию
    не меняется, если селектор не трогали).

    На выходе — проход по словарю терминов (terms.py): названий и аббревиатур из своей
    темы ни один движок не знает и подставляет похожее слово. Чиним ЗДЕСЬ, в одной точке, а
    не в gen_subs: так поправленные слова получают и субтитры, и всё, что читает ленту.
    use_terms=False — для самопроверки: там слова сверяются с нарезкой один в один, а
    словарь склеивает несколько слов в один термин и сбил бы сверку."""
    emit = wrap_emit(emit)
    meta = engine_meta(engine)
    if meta is None:
        words = _whisper(wav_path, **opts)
    elif meta["kind"] == "whisper":
        opts.setdefault("model_size", meta["id"].split(":", 1)[1])
        words = _whisper(wav_path, **opts)
    elif meta["id"].startswith("ctc:"):
        words = _ctc(wav_path, meta["model"], meta.get("device", "cuda"), **opts)
    elif meta.get("gigaam"):
        words = _gigaam(wav_path, model_name=meta["gigaam"], **opts)
    elif meta["kind"] == "whisper_cpp":
        size = meta["id"].split(":", 1)[1] if ":" in meta["id"] else "large-v3"
        words = _whisper_cpp(wav_path, size=size, **opts)
    else:
        words = ASR_BACKENDS[meta["id"]](wav_path, **opts)
    if not use_terms:
        return words
    try:
        from core import terms
        return terms.fix_words(words, emit=emit)
    except Exception as e:                    # словарь не должен ронять транскрипцию
        emit("⚠ словарь терминов пропущен: {err}", err=str(e))
        return words


def register(name, fn):
    ASR_BACKENDS[name] = fn


# --------------------------------------------------------------------------- #
# Whisper (faster-whisper, GPU) — the original hardcoded path
# --------------------------------------------------------------------------- #
def _whisper(wav_path, **opts):
    from core import aicut
    from core import transcribe
    aicut.unload_ours()                      # free VRAM for Whisper
    aicut.warn_foreign_models()
    try:
        return transcribe.transcribe(wav_path, **opts)
    finally:
        # Выгружаем модель даже при падении транскрипции, иначе занятая VRAM
        # намертво вешает последующие запуски на Windows вместо OOM
        try:
            transcribe.release_model()
        except Exception:
            pass


register("whisper", _whisper)


# --------------------------------------------------------------------------- #
# CTC других языков (transformers): текст И тайминги И вероятности из одной
# акустической модели — тот же принцип, что у GigaAM, forced-align не нужен.
# --------------------------------------------------------------------------- #
def _ctc(wav_path, model_id, device="cuda", **opts):
    """Отдельным процессом (как GigaAM): CUDA OOM / нативный краш в тяжёлой
    GPU-части не должен убивать Flask. Результат — путь к JSON в stdout."""
    from core import aicut
    aicut.unload_ours()                      # освободить VRAM (LM Studio и т.п.)
    aicut.warn_foreign_models()
    # уникальное имя: общий %TEMP% + два параллельных прогона = чужой результат
    fd, out = tempfile.mkstemp(prefix="_ctc_asr_", suffix=".json")
    os.close(fd)
    os.remove(out)                           # ctc_asr.py создаст файл сам
    cmd = module_cmd("ctc_asr", wav_path,
                     "--model", model_id, "--device", device, "--out", out)
    try:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=1800, env=child_env())
        except subprocess.TimeoutExpired:
            raise RuntimeError("CTC (%s): превышен таймаут (30 мин)" % model_id)
        if r.returncode != 0:
            raw = (r.stderr or r.stdout or "").strip()
            err = [l for l in raw.splitlines() if l.strip()]
            raise RuntimeError(raw if "CTC_ASR_ERROR" in raw else
                               (err[-1] if err else "CTC-движок завершился с ошибкой"))
        path = (r.stdout or "").strip().splitlines()
        path = path[-1] if path else ""
        if not path or not os.path.isfile(path):
            raise RuntimeError("CTC (%s): пустой результат" % model_id)
        words = json.load(open(path, encoding="utf-8"))
        if not isinstance(words, list):
            raise RuntimeError("CTC (%s): неожиданный формат результата" % model_id)
        return words
    finally:
        try:
            os.remove(out)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# GigaAM (локально): весь файл одной моделью -> слова с родными таймингами
# --------------------------------------------------------------------------- #
def _widen(words, min_dur=0.14, gap=0.02):
    """Растянуть слишком короткие слова (RNN-T-головы).

    У CTC кадр эмиссии = момент звучания буквы, длительность слова честная. У
    RNN-T токены слова часто вылетают ОДНИМ кадром, и слово получает 0.04с — в
    субтитрах это вспышка на 2 кадра. Растягиваем такое слово в СВОБОДНУЮ паузу
    (сначала вперёд, до следующего слова, потом назад), не наезжая на соседей."""
    for i, w in enumerate(words):
        if w["end"] - w["start"] >= min_dur:
            continue
        nxt = words[i + 1]["start"] - gap if i + 1 < len(words) else w["start"] + min_dur
        w["end"] = round(min(max(w["end"], w["start"] + min_dur), max(w["end"], nxt)), 3)
        if w["end"] - w["start"] < min_dur:
            prv = words[i - 1]["end"] + gap if i else 0.0
            w["start"] = round(max(min(w["start"], w["end"] - min_dur), prv), 3)
    return words


def _gigaam(wav_path, model_name="v3_ctc", **opts):
    """GigaAM целиком по файлу -> [{"w","start","end"}] (родные тайминги CTC/RNN-T).

    model_name — голова GigaAM: v3_ctc (быстро, чистая акустика), v3_rnnt
    (внутренняя языковая модель правит слова), v3_e2e_rnnt (+ пунктуация и
    нормализация — для субтитров), multilingual_large_ctc (70+ языков).

    Runs GigaAM in a SEPARATE process (see gigaam_subs.py) so a CUDA OOM /
    native segfault during the heavy GPU work CANNOT kill the Flask server
    — exactly how the cutting path isolates GigaAM via omni_cut.py. VRAM is
    freed inside the subprocess, so it exits with a clean GPU.
    Returns the uniform [{"w","start","end"}] contract xmlbuild.build expects."""
    from core import aicut
    aicut.unload_ours()                      # free LM Studio VRAM in this process
    aicut.warn_foreign_models()
    try:
        # encoding обязателен: сабпроцесс пишет русский лог в stderr, а text=True
        # без него декодирует консольной cp1252 и валит поток-читатель (stderr=None,
        # returncode мусорный) — снаружи это выглядело как «GigaAM упал».
        r = subprocess.run(module_cmd("gigaam_subs", wav_path, model_name),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800, env=child_env())
    except subprocess.TimeoutExpired:
        raise RuntimeError("GigaAM: превышен таймаут генерации субтитров (30 мин)")
    if r.returncode != 0:
        raw = (r.stderr or r.stdout or "").strip()
        if "GIGAAM_SUBS_ERROR" in raw:
            # The subprocess already wrote a concise one-line error as the last
            # line AND the full traceback above it — surface both for diagnosis.
            raise RuntimeError(raw)
        err = raw.splitlines()
        err = err[-1] if err else "GigaAM subprocess завершился с ошибкой"
        raise RuntimeError(err)
    res_path = (r.stdout or "").strip()
    if not res_path or not os.path.isfile(res_path):
        raise RuntimeError("GigaAM: пустой результат ('%s')" % r.stdout[:200])
    try:
        words = json.load(open(res_path, encoding="utf-8"))
    except Exception:
        raise RuntimeError("GigaAM: не удалось разобрать результат")
    if not isinstance(words, list):
        raise RuntimeError("GigaAM: неожиданный формат результата (ожидался список слов)")
    return _widen(words) if model_name.endswith("rnnt") else words


register("gigaam", _gigaam)


# --------------------------------------------------------------------------- #
# whisper.cpp (локально, нативно): Metal на Mac, Vulkan на AMD — быстрее CPU,
# которого CTranslate2 не умеет обходить. Вся логика — в whisper_cpp.py.
# --------------------------------------------------------------------------- #
def _whisper_cpp(wav_path, size="large-v3", **opts):
    """Прогнать whisper-cli в отдельном процессе (паттерн _ctc): нативный краш
    не убивает Flask. Возвращает общий контракт [{"w","start","end"}] (сек)."""
    from core import whisper_cpp
    return whisper_cpp.transcribe(wav_path, size=size)


# --------------------------------------------------------------------------- #
# Omni (Qwen / GigaAM) — phrase-level -> word-level (interpolated)
# --------------------------------------------------------------------------- #
def _split_phrases(phrases):
    """Разбить фразы [{start,end,text}] на слова с линейной интерполяцией
    таймингов внутри фразы. Контракт: [{w,start,end}] в секундах."""
    words = []
    for ph in phrases or []:
        text = (ph.get("text") or "").strip()
        if not text:
            continue
        s, e = float(ph.get("start", 0.0)), float(ph.get("end", 0.0))
        toks = text.split()
        if not toks:
            continue
        n = len(toks)
        for i, t in enumerate(toks):
            ws = s + (e - s) * (i / n)
            we = s + (e - s) * ((i + 1) / n)
            words.append({"w": t, "start": round(ws, 3), "end": round(we, 3)})
    return words


def _omni(wav_path, engine=None, refine=False, **opts):
    """Omni-транскрипция клипа. Конкретный локальный движок (qwen/gigaam)
    берётся из `aicut.omni_local_engine()` — единый источник истины (как в нарезке).
    Запускаем `omni_asr.py` отдельным процессом (устоявшийся паттерн в omni_cut.py),
    чтобы тяжёлый torch/bnb не мешался с ctranslate2 в одном процессе. Фразы
    [{start,end,text}] режем на слова с интерполяцией; при refine=True уточняем
    словные тайминги через `gigaam_cut.align_full` (если доступно)."""
    from core import aicut
    aicut.unload_ours()
    aicut.warn_foreign_models()
    eng = engine or aicut.omni_local_engine()   # 'qwen' | 'gigaam'
    # уникальное имя + чистка: раньше при returncode 0 без записи результата
    # молча читался ПРОШЛЫЙ файл (чужие субтитры вместо ошибки)
    fd, out = tempfile.mkstemp(prefix="_omni_subs_", suffix=".json")
    os.close(fd)
    os.remove(out)
    cmd = module_cmd("omni_asr", wav_path, "--out", out)
    if eng:
        cmd += ["--engine", eng]
    try:
        # Таймаут вместо вечного висяка: omni_asr молотит минуты, но при загрузке
        # движка/недодиске умеет и не завершиться вообще. Зависший процесс
        # жрал бы VRAM, пока юзер не перезапустит сервер — subprocess без хэндла
        # не убить даже «Стопом» (CURPROC хранит только omni_cut).
        # stderr захватываем через PIPE, чтобы при ошибке дать пользователю хвост stderr,
        # а stdout оставляем унаследованным, чтобы прогресс обработки доходил до лога.
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, timeout=3600, env=child_env())
        if not os.path.isfile(out):
            raise RuntimeError("Omni-субтитры: движок не записал результат")
        phrases = json.load(open(out, encoding="utf-8"))
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "replace").strip()
        tail = err[-300:] if err else str(e)
        raise RuntimeError(f"Omni-субтитры: {tail}") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Omni-субтитры: процесс завис (> 1 ч) — прервано")
    finally:
        try:
            os.remove(out)
        except Exception:
            pass
    if refine:
        try:
            from core import gigaam_cut
            full_text = " ".join(
                (p.get("text") or "").strip()
                for p in phrases if (p.get("text") or "").strip())
            if full_text:
                return gigaam_cut.align_full(wav_path, full_text)
        except Exception:
            pass
    return _split_phrases(phrases)


register("omni", _omni)
