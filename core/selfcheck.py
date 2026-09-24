# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Самопроверка нарезки (этап 2 «уроки video-use»): не обрезали ли слова на стыках.

Идея: склеить keep-аудио как оно будет звучать после монтажа → Whisper с пословными
вероятностями → слово с НИЗКОЙ вероятностью, прижатое к стыку сегментов, = скорее
всего обрезано катом → расширить границу этого сегмента на +pad_step (в тишину,
не залезая на соседний сегмент) → одна повторная проверка (self-eval, max 1 итерация).

Медиа/LLM не участвуют: только уже извлечённый 16кГц wav камеры 1 + Whisper
(та же модель, что и субтитры; грузится ПОСЛЕ выгрузки LM Studio — на 16 ГБ VRAM
две модели одновременно не живут).

Программно:
    from core import selfcheck
    keep2, report = selfcheck.check_and_fix(wav, keep, emit=print)
"""
from __future__ import annotations
import os
from typing import Any, Callable, Sequence
from core.app_meta import console_emit, wrap_emit
from core.umsg import ReelsiError
from core.applog import get_logger

log = get_logger(__name__)


PAD_STEP = 0.08      # сек: на сколько расширять проблемную границу (в тишину)
TOL = 0.15           # сек: слово считается «на стыке», если его край ближе этого к стыку
PMIN = 0.50          # вероятность Whisper ниже — слово «сомнительное» (обычно обрезок)
MIN_GAP = 0.02       # сек: не смыкать соседние keep-сегменты вплотную
SR = 16000


def concat_keep(wav_path: str, keep: Sequence[Any], out_wav: str) -> tuple[list[float], list[int]]:
    """Склеить keep-интервалы wav в один файл. Возвращает список ВНУТРЕННИХ стыков
    (сек, в таймлайне склейки): стык k = конец сегмента k / начало сегмента k+1."""
    import soundfile as sf
    import numpy as np
    a, sr = sf.read(wav_path, dtype="int16")
    if a.ndim > 1:
        a = a.mean(1).astype("int16")
    parts, junctions, used, t = [], [], [], 0.0
    for i, (s, e) in enumerate(keep):
        a0, a1 = max(0, int(s * sr)), min(len(a), int(e * sr))
        if a1 <= a0:
            continue                         # вырожденный сегмент в склейку не попал
        parts.append(a[a0:a1])
        t += (a1 - a0) / sr
        junctions.append(t)
        used.append(i)                       # его РЕАЛЬНЫЙ индекс в keep
    if not parts:
        raise RuntimeError("keep пуст — нечего проверять")
    sf.write(out_wav, np.concatenate(parts), sr)
    # used возвращаем, потому что пропуск сегмента сдвигал нумерацию: стык N в
    # склейке != keep[N]. Правился сосед — «расширяю границу» удлиняло случайный
    # кусок, а реальный обрубок оставался. Теперь стык k лежит между
    # keep[used[k]] и keep[used[k+1]].
    return junctions[:-1], used              # последний «стык» = конец файла, не стык


def _norm_engine(engine: str | None) -> str:
    """Старые значения («large-v3») = размеры Whisper; новые — id из asr_backends."""
    engine = (engine or "whisper").strip()
    if engine in ("whisper", "large-v3", "medium", "small"):
        return "whisper:%s" % (engine if engine != "whisper" else "large-v3")
    return engine


def _transcribe_words(wav: str, model: Any = None, engine: str = "whisper:large-v3") -> list[dict[str, Any]]:
    """Склейка -> [{w,start,end,prob}]. Whisper зовём напрямую (нужен уже
    загруженный `model` и vad_filter=False — по склейке и так одна речь),
    остальные движки (GigaAM / CTC других языков) — через общий реестр
    `asr_backends`, они отдают тот же контракт со своей CTC-вероятностью."""
    engine = _norm_engine(engine)
    if not engine.startswith("whisper"):
        from core import asr_backends
        # словарь терминов тут выключен: он склеивает несколько слов в один термин,
        # а самопроверка сверяет слова с нарезкой один в один
        return asr_backends.transcribe_words(wav, engine=engine, use_terms=False)
    from core import transcribe as tr
    m = model or tr.get_model(engine.split(":", 1)[1])
    segs, _info = m.transcribe(wav, language="ru", word_timestamps=True,
                               vad_filter=False, condition_on_previous_text=False)
    out = []
    for s in segs:
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                out.append({"w": t, "start": float(w.start), "end": float(w.end),
                            "prob": float(getattr(w, "probability", 1.0) or 1.0)})
    return out


def analyze(words: Sequence[dict[str, Any]], junctions: Sequence[float], tol: float = TOL, pmin: float = PMIN) -> list[dict[str, Any]]:
    """Сомнительные слова на стыках. Возвращает [{junction(idx), side('end'|'start'),
    t(стык, сек склейки), w, prob}]: side='end' — обрезан конец сегмента j,
    side='start' — обрезано начало сегмента j+1."""
    bad = []
    for j, t in enumerate(junctions):
        for w in words:
            if w.get("prob", 1.0) >= pmin:
                continue
            if abs(w["end"] - t) <= tol:
                bad.append({"junction": j, "side": "end", "t": t, "w": w["w"], "prob": w.get("prob", 1.0)})
            elif abs(w["start"] - t) <= tol:
                bad.append({"junction": j, "side": "start", "t": t, "w": w["w"], "prob": w.get("prob", 1.0)})
    # один стык — одна правка на сторону (несколько сомнительных слов не множат сдвиг)
    seen, uniq = set(), []
    for b in bad:
        key = (b["junction"], b["side"])
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return uniq


def analyze_straddle(ref_words: Sequence[dict[str, Any]], keep: Sequence[Any], tol: float = 0.02) -> list[dict[str, Any]]:
    """Для CTC-движков: рез, ПРОХОДЯЩИЙ ПОСЕРЕДИНЕ слова, по исходному звуку.

    Почему не по вероятностям, как у Whisper: вероятность Whisper знает язык, и
    обрубок «купи» от «купили» проваливается по ней сразу. У CTC вероятность
    чисто акустическая — «купи» произнесено чётко, модель уверена, и обрезка
    остаётся незамеченной. Зато CTC даёт точные границы слов, поэтому берём
    прямой признак: слово начинается ДО реза и заканчивается ПОСЛЕ него.

    ref_words — транскрипция ИСХОДНОГО wav (не склейки!), тайминги в его времени.
    Формат результата — тот же, что у analyze(), так что apply_fixes работает как есть."""
    bad = []
    for j in range(len(keep)):
        cuts = []
        if j + 1 < len(keep):
            cuts.append(("end", keep[j][1]))          # конец сегмента j
            cuts.append(("start", keep[j + 1][0]))    # начало сегмента j+1
        for side, t in cuts:
            for w in ref_words:
                if w["start"] < t - tol and w["end"] > t + tol:
                    bad.append({"junction": j, "side": side, "t": t,
                                "w": w["w"], "prob": w.get("prob", 1.0)})
                    break
    seen, uniq = set(), []
    for b in bad:
        key = (b["junction"], b["side"])
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return uniq


def apply_fixes(keep: Sequence[Any], fixes: Sequence[dict[str, Any]], total_dur: float, pad_step: float = PAD_STEP, used: Sequence[int] | None = None) -> list[tuple[float, float]]:
    """Расширить границы проблемных сегментов (не залезая на соседей/за края).

    used — соответствие «номер стыка -> индекс в keep» из concat_keep. Нужно,
    когда в keep есть вырожденные сегменты: они не попадают в склейку, и без
    карты стык k правил бы keep[k] вместо keep[used[k]] (чужую границу).
    """
    keep = [list(se) for se in keep]
    for f in fixes:
        k = f["junction"]
        # индексы сегментов ПО КРАЯМ стыка k
        if used is not None:
            if k + 1 >= len(used):
                continue
            j, jn = used[k], used[k + 1]
        else:
            j, jn = k, k + 1
        if f["side"] == "end" and j < len(keep):
            hi = keep[jn][0] - MIN_GAP if jn < len(keep) else total_dur
            keep[j][1] = min(keep[j][1] + pad_step, max(keep[j][1], hi))
        elif f["side"] == "start" and jn < len(keep):
            lo = keep[j][1] + MIN_GAP
            keep[jn][0] = max(keep[jn][0] - pad_step, min(keep[jn][0], lo))
    return [tuple(se) for se in keep]


def check_and_fix(wav_path: str, keep: Sequence[Any], emit: Callable[..., Any] = console_emit, model: Any = None, model_size: str | None = None,
                  engine: str = "whisper:large-v3", tmp_dir: str | None = None, max_iter: int = 1, release_after: bool = True) -> tuple[Any, dict[str, Any]]:
    """Проверка + авто-фикс. Возвращает (keep, report):
    report = {"fixed": [описания правок], "left": [что осталось после повтора], "words": n}.
    max_iter=1: одна волна правок + одна контрольная проверка.

    engine — любой движок из `asr_backends` (Whisper любого размера, GigaAM,
    CTC другого языка). model_size — старое имя параметра (только размер
    Whisper), оставлено для совместимости вызовов."""
    emit = wrap_emit(emit)
    import tempfile
    engine = _norm_engine(model_size or engine)
    tmp_dir = tmp_dir or tempfile.gettempdir()
    os.makedirs(tmp_dir, exist_ok=True)
    cw = os.path.join(tmp_dir, "selfcheck_concat_%d.wav" % os.getpid())   # pid: параллельные прогоны не мешаются
    total_dur = None
    try:
        import soundfile as sf
        total_dur = sf.info(wav_path).duration
    except ReelsiError: raise
    except Exception:
        total_dur = max(e for _s, e in keep) + 1.0
    whisper = engine.startswith("whisper")
    report: dict[str, Any] = {"fixed": [], "left": [], "words": 0, "engine": engine}
    ref = None                     # транскрипция исходного wav (CTC-путь), считается один раз
    used = None                    # карта «стык -> индекс в keep» (whisper-путь, см. concat_keep)
    for it in range(max_iter + 1):
        if whisper:
            # Whisper: слушаем СКЛЕЙКУ — его вероятность знает язык, и обрубок
            # на стыке проваливается по ней.
            junctions, used = concat_keep(wav_path, keep, cw)
            words = _transcribe_words(cw, model=model, engine=engine)
            report["words"] = len(words)
            bad = analyze(words, junctions, pmin=PMIN)
            nj = len(junctions)
        else:
            # CTC (GigaAM / другие языки): вероятность чисто акустическая, обрубок
            # «купи» от «купили» она не ловит. Зато границы слов точные — слушаем
            # ИСХОДНЫЙ звук (один раз на все итерации) и ищем резы посреди слова.
            if ref is None:
                ref = _transcribe_words(wav_path, model=model, engine=engine)
                report["words"] = len(ref)
            words = ref
            bad = analyze_straddle(ref, keep)
            nj = max(0, len(keep) - 1)
        if not bad:
            if it:
                emit("  self-check: стыки чистые ({junctions} стыков, {words} слов) — после правки",
                     junctions=nj, words=len(words))
            else:
                emit("  self-check: стыки чистые ({junctions} стыков, {words} слов)",
                     junctions=nj, words=len(words))
            break
        def why(b: dict[str, Any]) -> str:                           # у whisper-пути признак — вероятность,
            return (f"«{b['w']}» у стыка {b['t']:.1f}с "     # у CTC — рез внутри слова
                    + (f"(p={b['prob']:.2f})" if whisper else "(рез внутри слова)"))
        if it >= max_iter:                    # правки исчерпаны — доложить, что осталось
            report["left"] = [why(b) for b in bad]
            emit("  self-check: осталось сомнительных стыков: {count} (см. cut-log)", count=len(bad))
            break
        for b in bad:
            if whisper:
                emit("  self-check: «{word}» у стыка {t:.1f}с (p={prob:.2f}) обрезано — расширяю границу на +{step:.2f}с",
                     word=b['w'], t=b['t'], prob=b['prob'], step=PAD_STEP)
            else:
                emit("  self-check: «{word}» у стыка {t:.1f}с (рез внутри слова) обрезано — расширяю границу на +{step:.2f}с",
                     word=b['w'], t=b['t'], step=PAD_STEP)
        report["fixed"] += [why(b) for b in bad]
        keep = apply_fixes(keep, bad, total_dur, used=(used if whisper else None))
    try:
        os.remove(cw)
    except OSError:
        pass  # временный wav уже убран
    if release_after and model is None and engine.startswith("whisper"):
        try:
            from core import transcribe as tr
            if tr.release_model():
                emit("  self-check: Whisper выгружен")
        except ReelsiError: raise
        except Exception as ex:
            log.warning("Whisper не выгрузился после self-check: %s — "
                        "видеопамять остаётся занятой", ex)
    return keep, report
