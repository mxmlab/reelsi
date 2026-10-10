# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Второй проход текста при нарезке: Whisper правит НАПИСАНИЕ слов CTC.

CTC даёт точные тайминги, но написание с ошибками; Whisper пишет грамотно, но его
тайминги грубые. Поэтому тайминги и число слов остаются от CTC, а текст берётся у
Whisper там, где слова совпали по времени и по звучанию (`core.asr_merge`).

Из одного прохода получаются две ленты:
- для нарезки — текст в форме CTC (нижний регистр, без пунктуации): нарезка сравнивает
  слова между собой (дубли, повторы), и запятая Whisper не должна менять эти сравнения;
- для субтитров — текст Whisper как есть (регистр и пунктуация).

Выгрузка модели здесь своя, а не `asr_backends.transcribe_words`: тот вызывает
`aicut.unload_ours()`, который выгрузит модели LM Studio, которыми в это время может
решать соседний ролик (нарезка идёт по нескольку роликов параллельно).
"""
from __future__ import annotations
import string
from typing import Any, Callable

from core import transcribe
from core import terms
from core.asr_merge import merge_words
from core.gpulock import gpu_lock

# Пунктуация Whisper, которую снимаем с краёв слов для нарезки. Дефисы внутри слова
# не трогаем: «по-русски» должно остаться одним словом.
_EDGE_PUNCT = string.punctuation + "«»—–…“”„"


def _cut_form(w: str) -> str:
    """Форма слова, как у CTC: нижний регистр, без пунктуации по краям."""
    return w.strip(_EDGE_PUNCT).lower()


def text_pass(wav_path: str, words: list[dict[str, Any]], engine: str,
              emit: Callable[..., Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Прогнать ролик через Whisper и поправить им написание слов CTC.

    Возвращает `(слова_для_нарезки, слова_для_субтитров)`. При любой ошибке второго
    движка — `(words, None)`: нарезка из-за него падать не должна, текст остаётся от CTC.
    """
    try:
        size = engine.split(":", 1)[1]
        with gpu_lock("текст нарезки", emit=emit):
            try:
                txt = transcribe.transcribe(wav_path, model_size=size, emit=emit)
            finally:
                # Выгружаем ВСЕГДА, и при падении тоже: занятая VRAM на Windows вешает машину.
                transcribe.release_model()
        merged, st = merge_words(words, txt, time_tol=0.3, sim_min=0.3)
        sub: list[dict[str, Any]] = [{"w": m["w"], "start": m["start"], "end": m["end"]}
                                     for m in merged]
        try:
            sub = terms.fix_words(sub, emit=emit)
        except Exception as ex:               # словарь не должен ронять второй проход
            emit("⚠ словарь терминов пропущен: {err}", err=str(ex))
        cut: list[dict[str, Any]] = []
        for m in merged:
            w = _cut_form(m["w"]) or m.get("ctc_w") or m["w"]
            cut.append(dict(m, w=w))
    except Exception as ex:
        emit("⚠ текст нарезки: {engine} не отработал ({err}) — текст остаётся от CTC",
             engine=engine, err=str(ex))
        return words, None
    emit("  текст нарезки: {engine} — исправлено {txt}, склеено {merge}, разделено {split}, "
         "без пары (как у CTC) {ctc}, выброшено слов Whisper {dropped}",
         engine=engine, txt=st["txt"], merge=st["merge"], split=st["split"],
         ctc=st["ctc"], dropped=st["txt_dropped"])
    return cut, sub
