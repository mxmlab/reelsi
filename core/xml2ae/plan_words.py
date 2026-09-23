# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подготовка слов плана сцены (остаток распила scene_plan).

Шесть этапов распила отдали свои блоки модулям `plan_*`; этот модуль забирает
ПОДГОТОВКУ СЛОВ — то, что делается со словами транскрипта до раскладки субтитров:
разметку из kwargs сборки (жёлтые, ручные разделители серий, слова со счётчиком,
склейки в строку) приводят к индексам существующих слов, слова интро убирают из
титров с переиндексацией всей разметки и пословных таймингов, `censor_source`
сохраняет ВСЕ слова для цензора голоса, а пословные тайминги дочитываются из сайдкара
`<стем>.words.json`, если их не передали аргументом.

Перенос ПОСТРОЧНЫЙ: поведение, числа и порядок операций не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено дословно,
а входы распаковываются в преамбуле.

Вход — один неизменяемый `WordsInputs`, выход — один `WordsPlan` со всем, что `scene_plan`
читает дальше. Стиля у блока нет вовсе: ни один ключ стиля на подготовку слов не влияет —
поэтому поля `style` во входе и нет (единственный из модулей `plan_*` без него).
"""
import os
from dataclasses import dataclass
from typing import Any

from core.fileio import json_load_soft


@dataclass(frozen=True)
class WordsInputs:
    """Вход подготовки слов: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. Разметка (`highlights`,
    `hl_breaks`, `hl_count`, `hl_joins`) приходит сырыми kwargs сборки: None — разметки
    нет вовсе, индексы вне диапазона слов отбрасываются здесь же.
    """
    # Слова транскрипта [(начало, конец, слово)] в кадрах.
    subs: list
    # Разметка по словам: жёлтые, ручные разделители серий, слова со счётчиком, склейки.
    highlights: Any
    hl_breaks: Any
    hl_count: Any
    hl_joins: Any
    # Пословные тайминги (None — не переданы: дочитываются из сайдкара рядом с XML).
    word_timings: Any
    # Путь XML: рядом с ним лежит сайдкар <стем>.words.json.
    xml_path: Any
    # Индексы слов интро: из титров уходят, но звучат (цензор считает по ним).
    intro_remove: Any


@dataclass(frozen=True)
class WordsPlan:
    """Выход подготовки слов: ровно те имена, что `scene_plan` читает дальше.

    `subs` — слова БЕЗ интро-слов (их читают `plan_subs`, `plan_audio`, `plan_camera`),
    `hl`/`brk`/`cnt`/`joins` — та же разметка после переиндексации, `censor_source` —
    ВСЕ слова до удаления (интро-слова звучат), `word_timings` — тайминги, урезанные и
    переиндексированные вместе со словами. `remove` (какие индексы убраны) наружу не
    отдаётся: сборка его дальше не читает.
    """
    subs: list
    hl: set
    brk: set
    cnt: set
    joins: set
    censor_source: list
    word_timings: Any


def plan_words(inp: WordsInputs) -> WordsPlan:
    """Слова плана сцены: разметка -> индексы, интро из титров, тайминги, `censor_source`.

    Тело — дословный перенос блока из scene_plan (до распила — строки 543-579):
    имена локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    subs = inp.subs
    highlights, hl_breaks = inp.highlights, inp.hl_breaks
    hl_count, hl_joins = inp.hl_count, inp.hl_joins
    word_timings, xml_path = inp.word_timings, inp.xml_path
    intro_remove = inp.intro_remove
    hl_raw = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    brk_raw = set(int(x) for x in (hl_breaks or []) if 0 <= int(x) < len(subs))
    cnt_raw = set(int(x) for x in (hl_count or []) if 0 <= int(x) < len(subs))
    joins_raw = set(int(x) for x in (hl_joins or []) if 0 <= int(x) < len(subs))
    joins_raw = joins_raw - brk_raw
    # Интро собирается и в режиме строк. Прежний запрет («со строками эта
    # связь ещё не продумана») снят: связь как раз прямая — слова интро вынимаются из subs
    # НИЖЕ и раньше, чем строятся строки (raw_lines), поэтому строки собираются из оставшихся
    # слов, а интро от режима субтитров не зависит.
    if word_timings is None and xml_path:
        _w_sidecar = os.path.splitext(xml_path)[0] + ".words.json"
        if os.path.isfile(_w_sidecar):
            word_timings = json_load_soft(_w_sidecar)
    eff_intro_remove = intro_remove or []
    remove = set(int(i) for i in eff_intro_remove if 0 <= int(i) < len(subs))
    censor_source = list(subs)                    # цензор считаем по ВСЕМ словам (интро-слова звучат)
    if remove:                                   # слова интро убираем из титров, хайлайты переиндексируем
        keep = [k for k in range(len(subs)) if k not in remove]
        remap = {old: new for new, old in enumerate(keep)}
        if word_timings is not None:
            if isinstance(word_timings, list) and len(word_timings) == len(subs):
                word_timings = [word_timings[k] for k in keep]
            elif isinstance(word_timings, dict) and isinstance(word_timings.get("words"), list) and len(word_timings["words"]) == len(subs):
                word_timings = dict(word_timings, words=[word_timings["words"][k] for k in keep])
        subs = [subs[k] for k in keep]
        hl = set(remap[k] for k in hl_raw if k in remap)
        brk = set(remap[k] for k in brk_raw if k in remap)
        cnt = set(remap[k] for k in cnt_raw if k in remap)
        joins = set(remap[k] for k in joins_raw if k in remap)
    else:
        hl = hl_raw
        brk = brk_raw
        cnt = cnt_raw
        joins = joins_raw
    return WordsPlan(subs=subs, hl=hl, brk=brk, cnt=cnt, joins=joins,
                     censor_source=censor_source, word_timings=word_timings)
