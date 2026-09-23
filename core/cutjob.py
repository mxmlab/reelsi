# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Классическая нарезка пары камер: умолчания (`CutOptions`) и сам прогон пары.

ПОЧЕМУ модуль есть (инверсия слоёв). `process_pair` жил в CLI-модуле
`reelsi.py`, а HTTP-слой (`api/jobs.py`) руками собирал ради него
`argparse.Namespace` — и умолчания нарезки (`cam_return=2`, `big_chunk=6.0` и
прочие) лежали в двух местах сразу: в `reelsi.build_parser()` и в этом Namespace.
Теперь умолчания объявлены один раз — полями `CutOptions`: CLI берёт из них
значения своих опций, API собирает `CutOptions` из opts запроса.

Пороги, которые и так были общим контрактом с фронтом (`model`, `scale`,
`vad_thresh`, `min_silence`, `pad`, `cam_return`), берутся из
`core/cutstages.DEFAULT_THRESHOLDS` — второй их копии здесь нет.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, fields
from typing import Any, Callable

from core import align
from core import cutstages
from core import sync
from core import vad
from core import xmlbuild
from core.app_meta import child_env, console_emit, module_cmd, wrap_emit
from core.umsg import ReelsiError

# Пороги классической нарезки — один источник на CLI и API (core/cutstages.py).
_TH = cutstages.DEFAULT_THRESHOLDS


@dataclass
class CutOptions:
    """Параметры классической нарезки (VAD + Whisper). Один источник умолчаний.

    Имена полей — как у флагов CLI (`--no-subs` -> `no_subs`) и как у ключей opts
    из API; перевод в обе стороны делается здесь, а не числами по месту.
    """

    no_subs: bool = False
    no_dedup: bool = False
    no_srt: bool = False
    ae: bool = False
    keep: str = "last"
    model: str = str(_TH["model"])
    scale: float = float(_TH["scale"])
    vad_thresh: float = float(_TH["vad_thresh"])
    min_silence: float = float(_TH["min_silence"])
    pad: float = float(_TH["pad"])
    no_cut: bool = False
    aggressive: bool = False
    restarts: bool = False
    forced_align: bool = False
    cam_return: int = int(_TH["cam_return"])
    big_chunk: float = 6.0
    # Пауза «мёртвого воздуха» в агрессивном режиме: флага в API нет, но process_pair
    # её читает — значит, умолчание обязано жить здесь, а не вторым числом в теле.
    pause_max: float = 1.0

    @classmethod
    def from_namespace(cls, ns: argparse.Namespace) -> "CutOptions":
        """`argparse.Namespace` CLI -> `CutOptions`.

        Поля перебираются по самому классу, а не выписываются списком: иначе список
        разъехался бы с классом при первом же новом флаге. Чего в Namespace нет —
        остаётся умолчанием класса.
        """
        d = cls()
        return cls(**{f.name: getattr(ns, f.name, getattr(d, f.name)) for f in fields(cls)})


def _forced_align(wav: str, words: list[dict[str, Any]],
                  emit: Callable[..., Any] = console_emit) -> list[dict[str, Any]]:

    """Уточнить тайминги слов через wav2vec2 в ОТДЕЛЬНОМ процессе (иначе cuDNN торча
    конфликтует с ctranslate2 Whisper → EXIT 127). Падение = возвращаем как было."""
    emit = wrap_emit(emit)
    d = tempfile.gettempdir()
    tag = uuid.uuid4().hex[:8]                # уникально: параллельные запуски не перетирают
    fin = os.path.join(d, f"_ac_fa_in_{tag}.json")
    fout = os.path.join(d, f"_ac_fa_out_{tag}.json")
    try:
        json.dump(words, open(fin, "w", encoding="utf-8"), ensure_ascii=False)
        r = subprocess.run(module_cmd("falign_cli", wav, fin, fout),
                           capture_output=True, text=True, encoding="utf-8", timeout=900,
                           env=child_env())
        for ln in (r.stdout or "").splitlines():
            if ln.strip():
                emit("  {line}", line=ln.strip())
        if os.path.exists(fout):
            return json.load(open(fout, encoding="utf-8"))
        emit("  forced align: нет результата {err}",
             err=(r.stderr or "")[-160:].replace("\n", " "))
    except ReelsiError: raise
    except Exception as e:
        emit("  forced align пропущен: {err}", err=str(e))
    finally:
        for f in (fin, fout):
            try:
                os.remove(f)
            except OSError:
                pass  # временные wav уже убраны
    return words


def process_pair(cams: list[str] | str, out_xml: str, opts: CutOptions,
                 model: str | None = None, emit: Callable[..., Any] = console_emit,
                 music_path: str | None = None) -> dict[str, Any]:
    """cams: list of camera file paths (1..4), camera 1 first. opts: CutOptions."""
    if isinstance(cams, str):
        cams = [cams]
    cams = [c for c in cams if c]             # drop empty slots
    out_base = os.path.splitext(out_xml)[0]
    no_cut = opts.no_cut                      # only subtitles, don't cut
    if no_cut:
        cams = cams[:1]                       # subtitles-only works on one video
    N = len(cams)
    if no_cut:
        emit("  Камеры: {cams}   (только субтитры)", cams=", ".join(os.path.basename(c) for c in cams))
    else:
        emit("  Камеры: {cams}", cams=", ".join(os.path.basename(c) for c in cams))
    work = tempfile.mkdtemp(prefix="reelsi_")
    try:
        wavs = [os.path.join(work, f"a{k}.wav") for k in range(N)]
        emit("  извлекаю аудио...")
        sync.extract_audio(cams[0], wavs[0])

        offsets = [0.0]
        if no_cut:
            import wave
            with wave.open(wavs[0]) as wf:
                dur = wf.getnframes() / float(wf.getframerate())
            segments = [(0.0, dur)]           # whole clip, uncut
            emit("  без нарезки: весь ролик {dur:.0f}s", dur=dur)
        else:
            for k in range(1, N):
                sync.extract_audio(cams[k], wavs[k])
                off, conf = sync.find_offset(wavs[0], wavs[k])
                offsets.append(off)
                emit("  синк К{cam}: {off:+.3f}s (увер. {conf:.2f})",
                     cam=k + 1, off=off, conf=conf)
            segments = vad.speech_intervals(wavs[0], thresh_db=opts.vad_thresh,
                                            min_silence=opts.min_silence, pad=opts.pad)
            emit("  речь: {count} сегментов", count=len(segments))

        words: list[dict[str, Any]] | None = None
        if not (opts.no_subs and opts.no_dedup):
            # кэш — по ИСХОДНИКУ (переживает смену outdir/номера в очереди, как в
            # subtitle_xml); в имени ключ модели, иначе medium-кэш выдавался за large-v3
            from core import transcribe
            src_cache = transcribe.words_cache_path(cams[0], opts.model)
            words = transcribe.load_words_cache(src_cache)
            if words is None:
                # кэши старого формата (без модели в имени) писались дефолтной large-v3 —
                # берём их только когда её и просят, иначе честно транскрибируем заново
                if opts.model == transcribe.DEFAULT_MODEL_SIZE:
                    for lg in (os.path.splitext(cams[0])[0] + ".words.json",
                               out_base + ".words.json"):
                        if os.path.exists(lg):
                            words = transcribe.load_words_cache(lg)
                            if words:
                                emit("  транскрипт из старого кэша (имя без модели)")
                                break
            if words:
                emit("  транскрипт из кэша: {count} слов", count=len(words))
            else:
                emit("  транскрибирую (Whisper {model})...", model=opts.model)
                words = transcribe.transcribe(wavs[0], model_size=opts.model, model=model)
                transcribe.save_words_cache(src_cache, words)
                emit("  {count} слов", count=len(words))

        if words:
            words = align.clamp_word_times(words)   # fix Whisper's ballooned durations
            if opts.forced_align:
                words = _forced_align(wavs[0], words, emit=emit)
    finally:
        shutil.rmtree(work, ignore_errors=True)     # wav-ы пары (гигабайты в %TEMP%)

    if words and not no_cut:
        aggressive = opts.aggressive
        ranges: list[tuple[float, float]] = []
        if not opts.no_dedup:
            if opts.restarts:                        # ИИ-нарезка: ловим и рестарты (near-повторы)
                drep, rep_log = align.find_restarts(words, keep=opts.keep)
            else:
                drep, rep_log = align.find_repeat_ranges(words, keep=opts.keep)
            ranges += drep
            if rep_log:
                emit("  повторов/рестартов удалено: {count}", count=len(rep_log))
                for _s, _e, _ph in rep_log:
                    emit("    − «{phrase}»", phrase=_ph)
        if aggressive:                          # extras only in aggressive mode
            fillers = [(w["start"], w["end"]) for w in words if align.is_filler(w["w"])]
            if fillers:
                emit("  филлеров (хм/кашель) вырезано: {count}", count=len(fillers))
            gaps = align.dead_air_ranges(words, max_pause=opts.pause_max)
            if gaps:
                emit("  пауз/мёртвого воздуха вырезано: {count}", count=len(gaps))
            ranges += fillers + gaps
        if ranges:
            segments = align.subtract_ranges(segments, ranges)
            words = [w for w in words if not any(a <= 0.5*(w['start']+w['end']) < b for a, b in ranges)]
        if aggressive:
            # VAD ловит вздохи/питьё воды/шум как «речь» (там есть звук), но Whisper в них
            # ничего не распознаёт → сегмент без единого слова = не речь → выкидываем.
            # Убирает и стартовые вздохи (до первого слова dead_air их не трогает).
            def _has_word(s: float, e: float) -> bool:        # пересечение (устойчиво к сдвигу таймингов forced-align)
                return any(w["start"] < e + 0.05 and w["end"] > s - 0.05 for w in words)
            n0 = len(segments)
            segments = [(s, e) for s, e in segments if _has_word(s, e)]
            if n0 - len(segments):
                emit("  сегментов без речи (вздохи/вода/шум) убрано: {count}", count=n0 - len(segments))

    # keep only frame-positive segments so the camera assignment lines up 1:1
    segments = [(s, e) for s, e in segments if round(e*60) - round(s*60) > 0]
    assign = None
    if N > 1 and not no_cut:
        assign = align.assign_cameras(segments, N,
                                      return_every=opts.cam_return,
                                      big_chunk_sec=opts.big_chunk)

    mapped = align.map_words(words, segments) if words else None
    sub_words = mapped if (mapped and not opts.no_subs) else None
    try:
        info = xmlbuild.build(cams, segments, offsets, out_xml, assign=assign,
                              scale=opts.scale, sub_words=sub_words, music_path=music_path)
    except (ReelsiError, SystemExit) as e:
        # Пустой монтаж (немой дубль, скринкаст: vad не нашёл речи) — build файл не тронул.
        # «Готовым» его помечать нечем: без этой ветки прогон печатал «-> 01_C1432.xml
        # (0s, 0 сег., 0 суб.)» и джоб отмечал клип собранным, а прошлая нарезка терялась.
        # Ключ строки — общий «XML не создан» (тот же, что у джоба нарезки): очередь
        # пометит клип упавшим и пойдёт дальше.
        emit("  ⚠ XML не создан — {reason}", reason=str(e))
        raise RuntimeError(str(e)) from None
    if info.get("long_words"):
        emit("  ⚠ слишком длинные слова (>19 букв) — без титра, добавь вручную: {words}",
             words=", ".join(info["long_words"]))
    extra = ""
    if mapped and not opts.no_srt:
        n = align.make_srt(mapped, out_base + ".srt")
        extra += f" + .srt ({n})"
    if sub_words and opts.ae:
        try:
            from core import xml2ae
            _, nc, ns = xml2ae.to_ae_full(out_xml, out_base + ".jsx")
            extra += f" + .jsx ({nc}кл/{ns}суб)"
        except ReelsiError: raise
        except Exception as e:
            extra += f" [jsx err: {e}]"
    emit("  -> {name}  ({sec:.0f}s, {segs} сег., {subs} суб.){extra}",
         name=os.path.basename(out_xml), sec=info['total_s'],
         segs=info['segments'], subs=info['subtitles'], extra=extra)
    return info
