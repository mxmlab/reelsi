# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты `core/asr_merge.py`: скрещивание слов CTC и Whisper.

Проверяются решения модуля: тайминги берёт CTC, текст правит Whisper; сочинённые
Whisper слова отбрасываются; гейты по времени и по сходству; склейка 2:1 и
деление 1:2; пустые входы; неизменность входа; инварианты на случайных данных.
"""
from __future__ import annotations

import copy
import random
from typing import Any

import pytest

from core.asr_merge import merge_words, norm


def _w(text: str, start: float, end: float, **extra: Any) -> dict[str, Any]:
    word: dict[str, Any] = {"w": text, "start": start, "end": end}
    word.update(extra)
    return word


def _texts(words: list[dict[str, Any]]) -> list[str]:
    return [x["w"] for x in words]


def test_identical_words_are_both_and_keep_whisper_text():
    ctc = [_w("привет", 1.0, 1.3), _w("мир", 1.4, 1.7)]
    txt = [_w("Привет,", 1.02, 1.32), _w("мир", 1.42, 1.72)]
    out, stats = merge_words(ctc, txt)
    assert [x["src"] for x in out] == ["both", "both"]
    assert out[0]["w"] == "Привет,"
    assert out[1]["w"] == "мир"
    assert stats["both"] == 2


def test_replacement_takes_ctc_timing_and_whisper_spelling():
    ctc = [_w("кус", 1.0, 1.3)]
    txt = [_w("курс", 1.05, 1.35)]
    out, _ = merge_words(ctc, txt)
    assert len(out) == 1
    word = out[0]
    assert word["w"] == "курс"
    assert word["start"] == 1.0
    assert word["end"] == 1.3
    assert word["src"] == "txt"
    assert word["ctc_w"] == "кус"


def test_whisper_ate_word_keeps_ctc_word():
    ctc = [_w("а", 1.0, 1.2), _w("б", 1.2, 1.4), _w("в", 1.4, 1.6)]
    txt = [_w("а", 1.02, 1.22), _w("в", 1.42, 1.62)]
    out, _ = merge_words(ctc, txt)
    assert _texts(out) == ["а", "б", "в"]
    assert out[1]["src"] == "ctc"
    assert out[1]["start"] == 1.2 and out[1]["end"] == 1.4


def test_whisper_invented_word_in_silence_is_dropped():
    ctc = [_w("а", 1.0, 1.2), _w("б", 1.2, 1.4)]
    txt = [_w("а", 1.02, 1.22), _w("б", 1.22, 1.42), _w("сочинил", 5.0, 5.3)]
    out, stats = merge_words(ctc, txt)
    assert _texts(out) == ["а", "б"]
    assert stats["txt_dropped"] == 1


def test_repeat_is_kept_when_whisper_collapses_it():
    ctc = [_w("чтобы", 1.0, 1.3), _w("чтобы", 1.5, 1.8), _w("не", 2.0, 2.2)]
    txt = [_w("чтобы", 1.45, 1.75), _w("не", 2.02, 2.2)]
    out, _ = merge_words(ctc, txt)
    assert _texts(out) == ["чтобы", "чтобы", "не"]
    assert [x["start"] for x in out if x["w"] == "чтобы"] == [1.0, 1.5]


def test_two_ctc_words_merge_into_one_whisper_word():
    ctc = [_w("по", 1.0, 1.2, prob=0.9), _w("этому", 1.2, 1.6, prob=0.7)]
    txt = [_w("поэтому", 1.0, 1.6)]
    out, stats = merge_words(ctc, txt)
    assert len(out) == 1
    word = out[0]
    assert word["w"] == "поэтому"
    assert word["start"] == 1.0 and word["end"] == 1.6
    assert word["src"] == "merge"
    assert word["ctc_w"] == "по этому"
    assert word["prob"] == pytest.approx(0.7)
    assert stats["merge"] == 1


def test_one_ctc_word_splits_into_two_whisper_words_by_length():
    ctc = [_w("вобщем", 1.0, 1.6)]
    txt = [_w("в", 1.0, 1.1), _w("общем", 1.1, 1.6)]
    out, stats = merge_words(ctc, txt)
    assert _texts(out) == ["в", "общем"]
    assert [x["src"] for x in out] == ["split", "split"]
    assert out[0]["start"] == pytest.approx(1.0)
    assert out[1]["end"] == pytest.approx(1.6)
    assert out[0]["end"] == pytest.approx(1.0 + 0.6 * (1 / 6))
    assert out[0]["end"] == pytest.approx(out[1]["start"])
    assert stats["split"] == 2


def test_time_gate_rejects_far_whisper_word():
    ctc = [_w("кус", 1.0, 1.3)]
    txt = [_w("курс", 3.0, 3.3)]
    out, stats = merge_words(ctc, txt)
    assert len(out) == 1
    assert out[0]["w"] == "кус"
    assert out[0]["src"] == "ctc"
    assert stats["txt_dropped"] == 1


def test_similarity_gate_rejects_different_word_at_same_time():
    ctc = [_w("кот", 1.0, 1.3)]
    txt = [_w("собака", 1.0, 1.3)]
    out, stats = merge_words(ctc, txt)
    assert len(out) == 1
    assert out[0]["w"] == "кот"
    assert out[0]["src"] == "ctc"
    assert stats["txt_dropped"] == 1


def test_empty_inputs():
    out, stats = merge_words([], [_w("а", 1.0, 1.2)])
    assert out == []
    assert stats["ctc_total"] == 0 and stats["txt_total"] == 1
    assert stats["txt_dropped"] == 1

    ctc = [_w("а", 1.0, 1.2), _w("б", 1.2, 1.4)]
    out, stats = merge_words(ctc, [])
    assert _texts(out) == ["а", "б"]
    assert [x["src"] for x in out] == ["ctc", "ctc"]
    assert stats["txt_dropped"] == 0


def test_inputs_are_not_mutated():
    ctc = [_w("по", 1.0, 1.2, prob=0.9), _w("этому", 1.2, 1.6, prob=0.7), _w("кус", 2.0, 2.3)]
    txt = [_w("поэтому", 1.0, 1.6), _w("курс", 2.05, 2.35)]
    ctc_before = copy.deepcopy(ctc)
    txt_before = copy.deepcopy(txt)
    merge_words(ctc, txt)
    assert ctc == ctc_before
    assert txt == txt_before


_VOCAB = [
    "привет", "курс", "кус", "чтобы", "не", "по", "этому", "поэтому",
    "вобщем", "в", "общем", "кот", "собака", "мир", "день", "сегодня", "видео", "слово",
]


def _random_case(rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ctc: list[dict[str, Any]] = []
    t = 0.0
    for _ in range(rng.randint(0, 40)):
        t += rng.uniform(0.05, 0.5)
        dur = rng.uniform(0.1, 0.6)
        ctc.append(_w(rng.choice(_VOCAB), round(t, 3), round(t + dur, 3), prob=round(rng.random(), 3)))
        t += dur
    txt: list[dict[str, Any]] = []
    for word in ctc:
        if rng.random() < 0.1:
            continue
        text = word["w"]
        if rng.random() < 0.2:
            pos = rng.randrange(len(text))
            text = text[:pos] + rng.choice("абвгдежз") + text[pos + 1:]
        shift = rng.uniform(-0.1, 0.1)
        txt.append(_w(text, round(word["start"] + shift, 3), round(word["end"] + shift, 3)))
    span = t + 5.0
    for _ in range(rng.randint(0, 4)):
        start = rng.uniform(0.0, span)
        txt.append(_w(rng.choice(_VOCAB), round(start, 3), round(start + 0.3, 3)))
    txt.sort(key=lambda x: x["start"])
    return ctc, txt


def _check_invariants(ctc: list[dict[str, Any]], txt: list[dict[str, Any]]) -> None:
    out, stats = merge_words(ctc, txt)
    eps = 1e-6
    ctc_starts = [x["start"] for x in ctc]
    ctc_ends = [x["end"] for x in ctc]
    txt_texts = {x["w"] for x in txt}

    # Объединение отрезков выхода равно объединению отрезков CTC. Исключение одно:
    # слово merge по контракту тянется от начала первого CTC-слова до конца второго,
    # поэтому тишина между ними тоже входит в его отрезок. Её и добавляем в эталон.
    def union(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
        res: list[list[float]] = []
        for s, e in sorted(spans):
            if res and s <= res[-1][1] + eps:
                res[-1][1] = max(res[-1][1], e)
            else:
                res.append([s, e])
        return [(s, e) for s, e in res]

    extra: list[tuple[float, float]] = []
    for o in out:
        if o["src"] == "merge":
            c1 = next(c for c in ctc if abs(c["start"] - o["start"]) < eps)
            c2 = next(c for c in ctc if abs(c["end"] - o["end"]) < eps)
            extra.append((c1["end"], c2["start"]))
    got = union([(x["start"], x["end"]) for x in out])
    want = union([(x["start"], x["end"]) for x in ctc] + extra)
    assert len(got) == len(want)
    for (gs, ge), (ws, we) in zip(got, want):
        assert gs == pytest.approx(ws, abs=eps) and ge == pytest.approx(we, abs=eps)

    # Каждое CTC-слово попадает ровно в одно выходное слово (или в одну пару split).
    for c in ctc:
        covering = [o for o in out if o["start"] < c["end"] - eps and o["end"] > c["start"] + eps]
        if len(covering) == 2:
            assert all(o["src"] == "split" for o in covering), c
        else:
            assert len(covering) == 1, (c, covering)

    # Времена выхода — времена CTC, кроме внутренней границы split. Текст без CTC-пары не появляется.
    for o in out:
        assert o["src"] in ("both", "txt", "ctc", "merge", "split")
        if o["src"] != "split":
            assert any(abs(o["start"] - s) < eps for s in ctc_starts), o
            assert any(abs(o["end"] - e) < eps for e in ctc_ends), o
        if o["src"] != "ctc":
            assert o["w"] in txt_texts or o["src"] in ("merge", "split"), o
    splits = [o for o in out if o["src"] == "split"]
    assert len(splits) % 2 == 0
    for first, second in zip(splits[::2], splits[1::2]):
        assert first["end"] == pytest.approx(second["start"], abs=eps)
        assert any(abs(first["start"] - s) < eps for s in ctc_starts)
        assert any(abs(second["end"] - e) < eps for e in ctc_ends)

    assert stats["ctc_total"] == len(ctc)
    assert stats["txt_total"] == len(txt)
    assert sum(stats[k] for k in ("both", "txt", "ctc", "merge", "split")) == len(out)


def test_random_cases_keep_invariants():
    rng = random.Random(0)
    for _ in range(200):
        ctc, txt = _random_case(rng)
        _check_invariants(ctc, txt)


def test_norm_ignores_case_yo_and_punctuation():
    assert norm("Ёлка,") == norm("елка") == "елка"
