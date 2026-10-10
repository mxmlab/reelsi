# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Скрещивание слов двух ASR-движков: CTC задаёт слова и тайминги, второй движок правит написание.

Зачем. Нарезка слушает звук CTC-моделью GigaAM: тайминги слов у неё точные, но
написание бывает с ошибками («кус» вместо «курс»). Whisper пишет слова лучше, но
тайминги у него грубые, он иногда «съедает» слово, сочиняет слово в тишине и
схлопывает повторы («чтобы чтобы» → «чтобы»), а повторы нужны нарезке.

Правило модуля: CTC решает, какие слова есть и где они стоят; Whisper только
исправляет написание. Поэтому выходное слово берёт тайминги у CTC (кроме внутренней
границы пары `split`), а текст берёт у Whisper, только если Whisper совпал с CTC
по времени (гейт) и по написанию (сходство). Слово, которого нет в CTC, на выход
не попадает никогда.

Модуль чистый: ничего не грузит, не пишет файлов и не меняет входные списки.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

# Гейт и сходство считаются по нормализованным строкам: регистр, «ё» и знаки
# препинания не должны решать, одно ли это слово.
_NON_WORD = re.compile(r"[\W_]+")

# Потолок таблицы динамического программирования на один промежуток. Больший
# промежуток не выравниваем: CTC-слова остаются как есть, Whisper-слова выбрасываются.
_MAX_GAP_CELLS = 60 * 60

_STAT_KEYS: tuple[str, ...] = (
    "ctc_total",
    "txt_total",
    "both",
    "txt",
    "ctc",
    "merge",
    "split",
    "txt_dropped",
    "gap_too_big",
)


def norm(w: str) -> str:
    """Нормализация слова для сравнения: нижний регистр, «ё» как «е», только буквы и цифры."""
    return _NON_WORD.sub("", w.lower().replace("ё", "е"))


def sim(a: str, b: str) -> float:
    """Сходство двух слов от 0 до 1 по нормализованным строкам (`difflib`)."""
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def gate(c_start: float, c_end: float, t_start: float, t_end: float, time_tol: float = 0.3) -> bool:
    """Пересекаются ли отрезки CTC и Whisper с допуском `time_tol` секунд.

    Допуск нужен, потому что тайминги Whisper грубее CTC и сдвигаются на десятки
    миллисекунд; без него верные слова терялись бы на границах.
    """
    return t_start <= c_end + time_tol and t_end >= c_start - time_tol


def merge_words(
    ctc: list[dict[str, Any]],
    txt: list[dict[str, Any]],
    *,
    time_tol: float = 0.3,
    sim_min: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Скрестить слова CTC и Whisper. Возвращает `(слова, статистика)`.

    Слова — словари `{"w", "start", "end", ...}` в секундах; прочие ключи CTC-слова
    переносятся в выход. Выходное слово получает `src`:
    - `both` — оба движка написали одно и то же (текст Whisper, он с регистром и пунктуацией);
    - `txt` — Whisper поправил написание CTC, тайминги CTC, `ctc_w` = текст CTC;
    - `ctc` — слова CTC без пары Whisper (Whisper его «съел»);
    - `merge` — два слова CTC стали одним словом Whisper («по» + «этому» → «поэтому»);
    - `split` — одно слово CTC стало двумя словами Whisper («вобщем» → «в общем»),
      граница между ними делит отрезок CTC пропорционально длинам слов.

    В статистике ключи `both`, `txt`, `ctc`, `merge`, `split` — число выходных слов
    с этим `src`; `txt_dropped` — слова Whisper, выброшенные как сочинённые;
    `gap_too_big` — число промежутков, которые не выравнивались.

    Входные списки и словари не меняются.
    """
    stats: dict[str, int] = {k: 0 for k in _STAT_KEYS}
    stats["ctc_total"] = len(ctc)
    stats["txt_total"] = len(txt)
    out: list[dict[str, Any]] = []
    pc = 0
    pt = 0
    for i, j in _anchors(ctc, txt, time_tol):
        out.extend(_gap(ctc[pc:i], txt[pt:j], stats, time_tol, sim_min))
        out.append(_one(ctc[i], txt[j]))
        pc, pt = i + 1, j + 1
    out.extend(_gap(ctc[pc:], txt[pt:], stats, time_tol, sim_min))
    for word in out:
        stats[word["src"]] += 1
    out.sort(key=lambda x: x["start"])
    return out, stats


def _anchors(ctc: list[dict[str, Any]], txt: list[dict[str, Any]], time_tol: float) -> list[tuple[int, int]]:
    """Якоря: совпавшие по написанию пары, которые ещё и пересекаются по времени."""
    matcher = difflib.SequenceMatcher(
        None,
        [norm(x["w"]) for x in ctc],
        [norm(x["w"]) for x in txt],
        autojunk=False,
    )
    pairs: list[tuple[int, int]] = []
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            i = block.a + k
            j = block.b + k
            if gate(ctc[i]["start"], ctc[i]["end"], txt[j]["start"], txt[j]["end"], time_tol):
                pairs.append((i, j))
    return pairs


def _gap(
    c: list[dict[str, Any]],
    t: list[dict[str, Any]],
    stats: dict[str, int],
    time_tol: float,
    sim_min: float,
) -> list[dict[str, Any]]:
    """Выравнивание одного промежутка между якорями."""
    if len(c) * len(t) > _MAX_GAP_CELLS:
        # Слишком большой кусок: Whisper в нём ненадёжен целиком, верим CTC как есть.
        stats["gap_too_big"] += 1
        stats["txt_dropped"] += len(t)
        return [_word(x, x["w"], x["start"], x["end"], "ctc", None) for x in c]
    out: list[dict[str, Any]] = []
    for kind, a, b in _gap_ops(c, t, time_tol, sim_min):
        if kind == "ctc":
            out.append(_word(c[a], c[a]["w"], c[a]["start"], c[a]["end"], "ctc", None))
        elif kind == "drop":
            stats["txt_dropped"] += 1
        elif kind == "11":
            out.append(_one(c[a], t[b]))
        elif kind == "21":
            out.append(_merge(c[a], c[a + 1], t[b]))
        else:
            out.extend(_split(c[a], t[b], t[b + 1]))
    return out


def _gap_ops(
    c: list[dict[str, Any]],
    t: list[dict[str, Any]],
    time_tol: float,
    sim_min: float,
) -> list[tuple[str, int, int]]:
    """Минимальное по стоимости выравнивание промежутка.

    Возвращает операции `(вид, индекс CTC, индекс Whisper)` в порядке следования;
    индексы — позиция, с которой операция начинается.
    """
    n = len(c)
    m = len(t)
    inf = float("inf")
    cost: list[list[float]] = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[str, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for a in range(n + 1):
        for b in range(m + 1):
            here = cost[a][b]
            if here == inf:
                continue
            moves: list[tuple[int, int, float, str]] = []
            if a < n:
                moves.append((a + 1, b, 1.0, "ctc"))
            if b < m:
                moves.append((a, b + 1, 1.0, "drop"))
            if a < n and b < m:
                # Гейт по времени не даёт слову Whisper из другого места стать парой
                # слову CTC: без него «курс» в тишине заменил бы «кус» на звуке.
                if gate(c[a]["start"], c[a]["end"], t[b]["start"], t[b]["end"], time_tol):
                    s = sim(c[a]["w"], t[b]["w"])
                    if s >= sim_min:
                        moves.append((a + 1, b + 1, 1.0 - s, "11"))
            if a + 1 < n and b < m:
                if gate(c[a]["start"], c[a + 1]["end"], t[b]["start"], t[b]["end"], time_tol):
                    s = sim(c[a]["w"] + c[a + 1]["w"], t[b]["w"])
                    if s >= sim_min:
                        moves.append((a + 2, b + 1, 1.1 - s, "21"))
            if a < n and b + 1 < m:
                if gate(c[a]["start"], c[a]["end"], t[b]["start"], t[b + 1]["end"], time_tol):
                    s = sim(c[a]["w"], t[b]["w"] + t[b + 1]["w"])
                    if s >= sim_min:
                        moves.append((a + 1, b + 2, 1.1 - s, "12"))
            for na, nb, step, kind in moves:
                if here + step < cost[na][nb]:
                    cost[na][nb] = here + step
                    back[na][nb] = (kind, a, b)
    ops: list[tuple[str, int, int]] = []
    a = n
    b = m
    while (a, b) != (0, 0):
        prev = back[a][b]
        if prev is None:
            raise RuntimeError("выравнивание промежутка не сошлось")
        kind, pa, pb = prev
        ops.append((kind, pa, pb))
        a, b = pa, pb
    ops.reverse()
    return ops


def _word(
    base: dict[str, Any],
    text: str,
    start: float,
    end: float,
    src: str,
    ctc_text: str | None,
) -> dict[str, Any]:
    """Новое выходное слово: ключи базового CTC-слова плюс переписанные текст и тайминг."""
    word = {k: v for k, v in base.items() if k not in ("w", "start", "end")}
    word.update(w=text, start=start, end=end, src=src)
    if ctc_text is not None:
        word["ctc_w"] = ctc_text
    return word


def _one(c: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Пара 1:1: тайминги CTC, текст Whisper."""
    src = "both" if norm(c["w"]) == norm(t["w"]) else "txt"
    ctc_text = None if t["w"] == c["w"] else c["w"]
    return _word(c, t["w"], c["start"], c["end"], src, ctc_text)


def _merge(c1: dict[str, Any], c2: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Два слова CTC стали одним словом Whisper: отрезок от начала первого до конца второго."""
    word = _word(c1, t["w"], c1["start"], c2["end"], "merge", f"{c1['w']} {c2['w']}")
    probs = [x["prob"] for x in (c1, c2) if "prob" in x]
    if probs:
        # Уверенность склеенного слова не выше слабейшей из двух частей.
        word["prob"] = min(probs)
    return word


def _split(c: dict[str, Any], t1: dict[str, Any], t2: dict[str, Any]) -> list[dict[str, Any]]:
    """Одно слово CTC стало двумя словами Whisper: граница делит отрезок по длинам слов."""
    l1 = len(norm(t1["w"]))
    l2 = len(norm(t2["w"]))
    frac = l1 / (l1 + l2) if l1 + l2 else 0.5
    mid = c["start"] + (c["end"] - c["start"]) * frac
    return [
        _word(c, t1["w"], c["start"], mid, "split", c["w"]),
        _word(c, t2["w"], mid, c["end"], "split", c["w"]),
    ]
