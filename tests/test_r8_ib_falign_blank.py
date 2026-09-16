# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IB (круг 8), п. 7: `merge_tokens` идёт с тем же `blank`, что `forced_align`.

`merge_tokens` по умолчанию ищет нули (`blank=0`) и удаляет спаны токена 0. У модели
с `pad_token_id != 0` (например 5) blank-спаны остаются в списке: число спанов
перестаёт совпадать с числом целей, `falign` уходит в «legacy»-ветку — разделитель
съедает слот слова, а последняя буква слова теряется.

Новый файл, а не test_hv_falign.py: тот написан под старую сигнатуру (подделка
`merge_tokens` из двух аргументов), здесь проверяется именно `pad_token_id != 0`.

Запуск: py -3.10 -m pytest tests/test_r8_ib_falign_blank.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

torch = pytest.importorskip("torch")
torchaudio = pytest.importorskip("torchaudio")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import falign  # noqa: E402

# <pad> — ПЯТЫЙ, а не нулевой: ровно тот случай, где merge_tokens без blank= врёт.
VOCAB = {"<pad>": 5, "|": 1, "а": 2, "б": 3, "в": 4, "г": 6, "д": 7}
# Кадры: аб|<pad>|вг — буквы по пикам, между ними пустые кадры модели
FRAMES = [2, 3, 5, 1, 5, 4, 6, 5]


def _proc():
    proc = MagicMock()
    proc.tokenizer.get_vocab.return_value = VOCAB
    proc.tokenizer.pad_token_id = 5
    return proc


class _DummyModel(torch.nn.Module):
    def forward(self, x):
        logits = torch.full((1, len(FRAMES), len(VOCAB)), -100.0)
        for t, tok in enumerate(FRAMES):
            logits[0, t, tok] = 100.0
        out = MagicMock()
        out.logits = logits
        return out


@pytest.fixture()
def model(monkeypatch):
    monkeypatch.setattr(falign, "_MODEL", (_proc(), _DummyModel(), "cpu"))


def test_границы_слов_при_pad_token_id_5(model):
    """8 секунд аудио и 8 кадров модели -> кадр эмиссии 1.0с.

    До правки blank-спаны (токен 5) оставались в списке, `falign` уходил в legacy:
    слово «вг» начиналось на кадре разделителя (3.0 вместо 5.0), а буква «г»
    терялась (конец 6.0 вместо 7.0)."""
    audio = np.zeros(16000 * 8, dtype=np.float32)
    res = falign.align_text(audio, "аб вг", device="cpu", sr=16000)

    assert [w["w"] for w in res] == ["аб", "вг"], res
    assert (res[0]["start"], res[0]["end"]) == (0.0, 2.0), res[0]
    assert (res[1]["start"], res[1]["end"]) == (5.0, 7.0), \
        f"границы второго слова поехали на blank-спанах: {res[1]}"


def test_align_words_при_pad_token_id_5(model, monkeypatch):
    """Второе место с тем же `merge_tokens` — `align_words` (уточнение таймингов)."""
    monkeypatch.setattr(torchaudio, "load", lambda p: (torch.zeros(1, 16000 * 8), 16000))
    words = [{"w": "аб", "start": 0.0, "end": 5.0},
             {"w": "вг", "start": 5.1, "end": 7.0}]
    res = falign.align_words("dummy.wav", words, device="cpu", emit=lambda *a, **k: None)

    fd = 7.25 / len(FRAMES)                    # a1 = 7.0 + 0.25; кадр эмиссии, сек
    assert [w["start"] for w in res] == [0.0, round(5 * fd, 3)], \
        f"слово начинается на кадре разделителя: {res}"
    # конец последней буквы «г» — кадр 7; сверху штатный хвост TAIL=0.12,
    # у последнего слова он подрезан на 0.02
    assert res[-1]["end"] == round(round(7 * fd, 3) + 0.1, 3), \
        f"последняя буква потеряна: {res[-1]}"
