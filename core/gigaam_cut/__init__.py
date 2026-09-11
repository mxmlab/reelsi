# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""GigaAM whole-file нарезка (отдельный пакет, вызывается из omni_cut.py).

В отличие от VAD-пути (Qwen): GigaAM v3-CTC слушает ВЕСЬ файл целиком
и САМ отдаёт пословные тайминги (word_timestamps=True — CTC-кадры, ~40мс) —
никакого отдельного forced-align (wav2vec2) не нужно: текст и тайминги из
ОДНОЙ модели, ничего не расползается. Дальше 27b видит весь пословный текст
и решает drop-диапазоны (разметка: скобки [ ] в возвращённом тексте);
тишина > SILENCE_SEC режется всегда (как VAD).

После решения 27b идёт ПОСТ-ПРОХОД КОДОМ (postprocess): 27b — один вызов на
весь ролик и систематически спотыкается на дублях (оставляет РАННИЙ заход
вместо последнего), на обрубках слов у реза и на островках в пару кадров.
Три детерминированные чистки — dedupe_repeats / dedupe_fragments /
drop_truncated / drop_micro_keeps — снимают это без ИИ (см. tests/
test_gigaam_postprocess.py). Само правило «из повторов остаётся ПОСЛЕДНИЙ
заход» продублировано в промпте DECIDE_MARKUP_SYS.

VRAM-последовательность внутри run(): GigaAM -> free -> 27b (LM Studio) ->
free -> рендер. На 16 ГБ держим ОДНУ тяжёлую модель за раз.

    from core.gigaam_cut import run
    keep, cutlog, draft_path, info = run(wav_path, cams, offsets, out, scale, model, emit)

С 2026-08-06 это пакет, а не файл на 2310 строк:

| модуль   | что там |
|----------|---------|
| `tune`   | пороги нарезки и все функции, которые их читают (см. ниже) |
| `takes`  | дубли, повторы, пост-проход кодом |
| `asr`    | транскрипция GigaAM, выравнивание слов |
| `decide` | промпты решения и разбор ответа модели |
| `pipeline` | оркестратор: GigaAM -> 27b -> пост-проход -> черновик |

`tune` собран не по теме, а по ОГРАНИЧЕНИЮ, и разбирать его дальше нельзя, не
разобравшись сначала с ним: `apply_speaker` накладывает профиль спикера, записывая
значения прямо в globals() модуля `tune`. Функция, читающая порог по имени из
другого модуля (`from .tune import SNAP_DB`), получила бы значение по умолчанию,
связанное один раз, — и профиль спикера перестал бы на неё действовать. Молча:
ни ошибки, ни падения, просто чужая калибровка. Поэтому все функции, читающие
переписываемые пороги, лежат в `tune` вместе с ними.

По той же причине сами пороги НЕ переэкспортируются сюда. Кому они нужны снаружи
(тесты профилей спикеров) — берёт их как `gigaam_cut.tune.SNAP_DB`: так значение
читается в момент обращения, уже после наложения профиля.

Командная строка: была `python gigaam_cut.py`, стала `python -m core.gigaam_cut`.
"""
from .tune import (   # noqa: F401
                   ATTACK_MAX, CHUNK, EDGE_TAIL, FILLERS, HEAL_RUN, HEAL_SEG, HOLE_AIR,
                   NG_MARKERS, ONSET_BACK, ONSET_FWD, ONSET_RUN,
                   REPEAT_N, REPEAT_WIN, SR, START_PAD, TAKE_WIN, _CUT_GLOBALS,
                   _autotune_db, _cut_breaths, _envelope, _hush_edge, _ranges,
                   _silence_bounds, _speech_mask, _start_edge, _sys, _walk_sound,
                   _word_onset, apply_speaker, breath_model_path, drop_micro_keeps,
                   keep_intervals, keep_segments, refine_keep)
from .takes import (   # noqa: F401
                    _is_ng, _lev1, _same, _take_tail, _tok, _words_text, build_cutlog,
                    dedupe_fragments, dedupe_repeats, drop_truncated, find_takes,
                    force_takes, heal_fragments, keep_parallel_runs, postprocess,
                    veto_unique_drops)
from .asr import (   # noqa: F401
                  _align_full_chunked, _free_torch, _norm, _quiet_cut,
                  _transcribe_words_manual, _word, align_full, transcribe_words_whole)
from .decide import (   # noqa: F401
                     DECIDE_MARKUP_SCHEMA, DECIDE_MARKUP_SYS,
                     _decide_reasoning, align_markup, decide_markup, parse_markup)
from .pipeline import run   # noqa: F401
