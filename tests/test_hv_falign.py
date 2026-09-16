# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тест forced alignment (задание HV): соответствие спанов токенов и слов без сдвига на разделителях."""
import logging
import os
import sys
from unittest.mock import MagicMock
import numpy as np
import pytest

torch = pytest.importorskip("torch")
torchaudio = pytest.importorskip("torchaudio")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Импорт на уровне модуля — не стилистика: у базового логгера "reelsi" applog ставит
# propagate=False, а pytest цепляет caplog-обработчик к непробрасывающим логгерам на
# входе в тест. Импортируй falign внутри теста — и caplog не увидит предупреждение
# (test_forced_align_spans_count_mismatch_fallback падал в одиночном прогоне).
from core import falign  # noqa: E402


def test_forced_align_token_spans_synthetic_emissions(monkeypatch):
    """Синтетические эмиссии: границы каждого из трёх слов совпадают с пиками своих букв, а не сдвигаются на разделитель."""
    vocab = {"<pad>": 0, "|": 1, "а": 2, "б": 3, "в": 4, "г": 5, "д": 6, "е": 7}
    proc = MagicMock()
    proc.tokenizer.get_vocab.return_value = vocab
    proc.tokenizer.pad_token_id = 0

    class DummyModel(torch.nn.Module):
        def __init__(self):
            super().__init__()

        def forward(self, x):
            # 8 фреймов:
            # 0: 'а' (2), 1: 'б' (3), 2: '|' (1), 3: 'в' (4), 4: 'г' (5), 5: '|' (1), 6: 'д' (6), 7: 'е' (7)
            T = 8
            V = len(vocab)
            logits = torch.full((1, T, V), -100.0)
            frames = [2, 3, 1, 4, 5, 1, 6, 7]
            for t, tok in enumerate(frames):
                logits[0, t, tok] = 100.0
            out = MagicMock()
            out.logits = logits
            return out

    monkeypatch.setattr(falign, "_MODEL", (proc, DummyModel(), "cpu"))

    # Длина клипа 8 секунд при sr=16000 -> длительность одного фрейма fd = 1.0 с
    sr = 16000
    audio = np.zeros(sr * 8, dtype=np.float32)
    res = falign.align_text(audio, "аб вг де", device="cpu", sr=sr)

    assert len(res) == 3, f"Ожидалось 3 слова, получено: {res}"
    # Слово 0: 'аб' -> фреймы 0..2 (0.0..2.0 с)
    assert res[0]["w"] == "аб"
    assert res[0]["start"] == 0.0
    assert res[0]["end"] == 2.0

    # Слово 1: 'вг' -> фреймы 3..5 (3.0..5.0 с). До правки было [2.0, 4.0] из-за захвата разделителя '|'
    assert res[1]["w"] == "вг"
    assert res[1]["start"] == 3.0, f"Слово 1 не должно захватывать разделитель: {res[1]}"
    assert res[1]["end"] == 5.0, f"Слово 1 должно содержать обе буквы: {res[1]}"

    # Слово 2: 'де' -> фреймы 6..8 (6.0..8.0 с). До правки было [4.0, 6.0]
    assert res[2]["w"] == "де"
    assert res[2]["start"] == 6.0, f"Слово 2 должно начинаться на фрейме 6: {res[2]}"
    assert res[2]["end"] == 8.0, f"Слово 2 должно заканчиваться на фрейме 8: {res[2]}"


def test_forced_align_spans_count_mismatch_fallback(caplog, monkeypatch):
    """Если torchaudio вернул не то же число спанов, что targets, срабатывает fallback с предупреждением в лог."""
    vocab = {"<pad>": 0, "|": 1, "а": 2}
    proc = MagicMock()
    proc.tokenizer.get_vocab.return_value = vocab
    proc.tokenizer.pad_token_id = 0

    class DummyModel(torch.nn.Module):
        def __init__(self):
            super().__init__()

        def forward(self, x):
            logits = torch.full((1, 2, len(vocab)), -100.0)
            logits[0, :, 2] = 100.0
            out = MagicMock()
            out.logits = logits
            return out

    monkeypatch.setattr(falign, "_MODEL", (proc, DummyModel(), "cpu"))

    # Подменим merge_tokens, чтобы он вернул неожиданное число спанов
    from collections import namedtuple
    Span = namedtuple("TokenSpan", ["token", "start", "end", "score"])

    orig_merge = torchaudio.functional.merge_tokens
    try:
        torchaudio.functional.merge_tokens = lambda aln, sc: [Span(token=2, start=0, end=1, score=1.0),
                                                              Span(token=2, start=1, end=2, score=1.0)]
        with caplog.at_level(logging.WARNING):
            audio = np.zeros(16000 * 2, dtype=np.float32)
            res = falign.align_text(audio, "а", device="cpu", sr=16000)
            assert res
            assert any("spans count" in rec.message or "spans length" in rec.message for rec in caplog.records)
    finally:
        torchaudio.functional.merge_tokens = orig_merge


def test_forced_align_words_uses_same_mapping(monkeypatch):
    """Второе место с этим же циклом — align_words: границы слова тоже идут по его буквам,
    разделитель не захватывается, а последняя буква последнего слова не теряется."""
    vocab = {"<pad>": 0, "|": 1, "а": 2, "б": 3, "в": 4, "г": 5, "д": 6, "е": 7}
    proc = MagicMock()
    proc.tokenizer.get_vocab.return_value = vocab
    proc.tokenizer.pad_token_id = 0

    class DummyModel(torch.nn.Module):
        def forward(self, x):
            frames = [2, 3, 1, 4, 5, 1, 6, 7]   # аб|вг|де — те же пики, что в тесте выше
            logits = torch.full((1, len(frames), len(vocab)), -100.0)
            for t, tok in enumerate(frames):
                logits[0, t, tok] = 100.0
            out = MagicMock()
            out.logits = logits
            return out

    monkeypatch.setattr(falign, "_MODEL", (proc, DummyModel(), "cpu"))
    # 8 секунд аудио и клип ровно 8 секунд (слова кончаются в 7.75) → кадр эмиссии = 1.0 с
    monkeypatch.setattr(torchaudio, "load", lambda p: (torch.zeros(1, 16000 * 8), 16000))
    words = [{"w": "аб", "start": 0.0, "end": 5.0},
             {"w": "вг", "start": 5.1, "end": 7.0},
             {"w": "де", "start": 7.1, "end": 7.75}]
    res = falign.align_words("dummy.wav", words, device="cpu", emit=lambda *a, **k: None)

    # До правки старты были [0.0, 2.0, 4.0]: спан разделителя присваивался первой букве
    # следующего слова, а последняя буква «де» не попадала ни в одно слово.
    assert [w["start"] for w in res] == [0.0, 3.0, 6.0], f"сдвиг на разделителе: {res}"
    assert res[2]["end"] == 8.1, f"последняя буква потеряна: {res[2]}"
