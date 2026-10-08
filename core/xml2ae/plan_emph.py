# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сила жёлтых (core/emphasis.py): чтение сайдкара `<стем>.emph.json` (остаток распила).

Наезд хайлайта в режиме «только сильные жёлтые» ставится не на каждую фразу, а на самые
сильные (см. `plan_camera` и `layout._take_zoom_segment_keys`), и силы приходят из
сайдкара. Читает его ОДНА дверь — этот модуль: сборка .jsx, превью (`/api/scene`) и
черновик видят одни и те же числа. Сайдкар считает предрасчёт
(`precompute.emphasis_precompute`) при сборке и в конце шага ИИ-жёлтых; здесь только
чтение и честное сообщение в лог о непосчитанном — второго расчёта силы в сборке нет.

Перенос ПОСТРОЧНЫЙ: нумерация слов, правило отбора и текст сообщений не менялись ни на
байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).

Вход — неизменяемый `EmphInputs`: слова плана ПОСЛЕ переиндексации `plan_words`, слова
интро из ПОЛНОГО списка (интро-слова звучат, поэтому их времена берут из `censor_source`)
и путь камеры 1. Числа нумерации — те же, что у плана: второй копии правила нет.
"""
from dataclasses import dataclass
from typing import Any

from core import emphasis as _emphasis

from .plan_intro import intro_hl_words
from .plan_style import StyleValues


@dataclass(frozen=True)
class EmphInputs:
    """Вход силы жёлтых: всё, что `scene_plan` знает к моменту вызова.

    `cams` — камеры из `parse_full` (берётся путь первой: сайдкар лежит рядом с исходником
    камеры 1), `subs` — слова плана БЕЗ слов интро (их вынул `plan_words`), `hl` — жёлтые
    после переиндексации, `intro`/`intro_splits`/`intro_remove` — строки интро и индексы
    вырезанных слов, `censor_source` — ПОЛНЫЙ список слов ролика (времена слов интро).
    """
    cams: list
    subs: list
    hl: Any
    intro: Any
    intro_splits: Any
    intro_remove: Any
    censor_source: list
    xml_path: str
    meta: dict
    style: StyleValues
    # Лог сборки: о непосчитанном сайдкаре план честно пишет, а не молчит.
    emit: Any


@dataclass(frozen=True)
class EmphPlan:
    """Выход: результат `core.emphasis.read_emphasis` (его читает `plan_camera`)."""
    emph: Any


def plan_emph(inp: EmphInputs) -> EmphPlan:
    """Прочитать силу жёлтых из сайдкара: чистая функция от `EmphInputs`.

    Ничего не считает: `read_emphasis` берёт готовый предрасчёт, а выбор компоненты
    (`mode=style.hl_zoom_strength`) делает она же — второй копии правила нет.
    """
    cams, subs, hl = inp.cams, inp.subs, inp.hl
    stv, meta, emit = inp.style, inp.meta, inp.emit
    _emp_src = (cams[0].get("path") if cams else "") or ""
    # Нумер слов — как у ПЛАНА: `subs` здесь уже без слов интро (их вынул `plan_words`),
    # а `hl` — та же разметка после переиндексации. Слова интро продолжают ряд
    # (`len(subs) + j`) — ровно так же их нумерует `plan_camera._yellow_need`, и тем же
    # нумером пишет сайдкар предрасчёт (`precompute.emphasis_precompute`, он зовёт
    # `plan_words` — одну дверь переиндексации, второй копии правила нет).
    # Времена слов интро берутся из ПОЛНОГО списка (`censor_source`): `intro_remove` —
    # индексы исходного списка ролика, до вырезания слов интро.
    _emp_words = _emphasis.word_refs(subs, meta["fps"])
    _emp_intro = intro_hl_words(inp.intro, inp.intro_splits, inp.intro_remove,
                               inp.censor_source)
    _emp_idx = [int(k) for k in hl] + [len(subs) + j for j in range(len(_emp_intro))]
    # Способ оценки силы (`hl_zoom_strength`) выбирает ПЛАН, и он же решает, какую
    # компоненту сайдкара взять (обе лежат рядом). Предрасчёт читает тот же ключ —
    # иначе в режиме «по голосу» он бы грузил модель эмоций впустую.
    _emph = _emphasis.read_emphasis(inp.xml_path, _emp_words, _emp_intro, hl, _emp_src,
                                    idx=_emp_idx, mode=stv.hl_zoom_strength)
    if not _emph.valid:
        emit("  · сила жёлтых не посчитана — наезд на каждую фразу хайлайта (как раньше)")
    elif _emph.uncomputed:
        emit("  · сила жёлтых не посчитана для {n} слов — наезд на каждую фразу "
             "(жёлтые правили после расчёта)", n=len(_emph.uncomputed))
    return EmphPlan(emph=_emph)
