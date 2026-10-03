# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""WX6: эмоция окна считается по МАССИВУ, без файла и без ffmpeg.

Живой прогон архитектора сорвался на `emotion_probs`: она звала `model.get_probs(window)`,
а `GigaAMEmo.get_probs` ждёт ПУТЬ к файлу и внутри гонит ffmpeg (`prepare_wav` ->
`load_audio`). На массиве это `TypeError: expected str, bytes or os.PathLike object,
not ndarray`, и режим «по эмоциям» с настоящей моделью не работал вовсе.

Две проверки:

1. заглушка модели, у которой `get_probs` падает как настоящая (на массиве), а
   `forward`/`head`/`id2name` есть: `emotion_probs` считает через `forward` и отдаёт
   словарь четырёх эмоций, а в модель уезжает 16 кГц. Мутация «вернуть
   `model.get_probs(window)`» краснит тест;
2. настоящая голова `emo` (`gigaam.load_model('emo', device='cpu')`): синус 2.5 с ->
   словарь четырёх эмоций с суммой ≈ 1. GPU не трогается, весов нет — тест пропускается.
"""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import emphasis  # noqa: E402


class _StubEmo:
    """Заглушка GigaAM-Emo: `get_probs` падает на массиве, как настоящая.

    `forward` отдаёт `[B, C, T]` (форма энкодера), `head` — логиты `[B, 4]`,
    `id2name` — те же четыре эмоции. `seen` — то, что реально доехало до модели.
    """

    def __init__(self):
        import torch
        self._device = torch.device("cpu")
        self._dtype = torch.float32
        self.id2name = {0: "angry", 1: "sad", 2: "neutral", 3: "positive"}
        self.logits = torch.tensor([[2.0, 1.0, 0.5, 0.0]], dtype=torch.float32)
        self.seen = None
        self.forward_calls = 0

    def get_probs(self, wav_file):
        # GigaAMEmo.get_probs(wav_file: str): `prepare_wav` -> `load_audio` -> ffmpeg.
        raise TypeError("expected str, bytes or os.PathLike object, not ndarray")

    def forward(self, wav, length):
        import torch
        self.forward_calls += 1
        self.seen = (wav, length)
        enc = torch.zeros((wav.shape[0], 4, 16), dtype=wav.dtype)
        return enc, torch.full((wav.shape[0],), enc.shape[-1])

    def head(self, pooled):
        return self.logits


def test_окно_считается_через_forward_без_файла():
    """`emotion_probs` идёт путём forward/pooling/head/softmax, а не через `get_probs`."""
    torch = pytest.importorskip("torch", reason="нужен torch: расчёт идёт тензорами")
    np = pytest.importorskip("numpy")
    model = _StubEmo()
    wav = np.zeros(int(2.5 * 22050), dtype="float32")     # 2.5 с в частоте проекта

    probs = emphasis.emotion_probs(wav, model, 22050)

    assert model.forward_calls == 1, "окно обязано уехать в forward, а не в get_probs"
    assert set(probs) == set(model.id2name.values()), probs
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6), "softmax обязан дать сумму 1"
    # Раскладка по `id2name` и softmax — по логитам заглушки [2, 1, 0.5, 0]:
    # проверяем порядок (какая эмоция к какому имени), а не только сумму.
    exp = [math.exp(v) for v in (2.0, 1.0, 0.5, 0.0)]
    total = sum(exp)
    for i, name in model.id2name.items():
        assert probs[name] == pytest.approx(exp[i] / total, rel=1e-6)

    wav_in, length = model.seen
    assert wav_in.ndim == 2 and wav_in.shape[0] == 1, "в модель едет батч [1, отсчёты]"
    assert wav_in.dtype == torch.float32, "на CPU модель ждёт float32"
    assert wav_in.device.type == "cpu" and length.device.type == "cpu"
    assert int(length[0]) == int(wav_in.shape[-1])
    # Ресемпл в 16 кГц: 2.5 с при 22050 — это 40000 отсчётов модели, а не 55125.
    assert abs(int(wav_in.shape[-1]) - int(2.5 * emphasis.EMO_SR)) <= 2, \
        "окно обязано доехать в частоте модели (%d Гц)" % emphasis.EMO_SR


def test_настоящая_модель_emo_отдаёт_четыре_эмоции():
    """Интеграционный: настоящая голова emo на CPU — тот прогон, что и поймал баг."""
    pytest.importorskip("torch", reason="нужен torch: расчёт идёт тензорами")
    np = pytest.importorskip("numpy")
    gigaam = pytest.importorskip("gigaam", reason="пакет gigaam не установлен")

    cache = os.path.expanduser(str(getattr(gigaam, "_CACHE_DIR", "~/.cache/gigaam")))
    ckpt = os.path.join(cache, "emo.ckpt")
    if not os.path.isfile(ckpt):
        pytest.skip("весов emo нет в кэше gigaam: %s" % ckpt)
    try:
        model = gigaam.load_model("emo", device="cpu")
    except Exception as ex:                       # битый вес / модель не собирается
        pytest.skip("модель emo не грузится: %s" % ex)

    try:
        t = np.arange(int(2.5 * emphasis.EMO_SR), dtype="float32") / float(emphasis.EMO_SR)
        wav = (0.3 * np.sin(2.0 * math.pi * 180.0 * t)).astype("float32")
        probs = emphasis.emotion_probs(wav, model, emphasis.EMO_SR)
    finally:
        # `release_emo` не зовём: он дёргает torch.cuda — GPU в этих тестах не трогаем.
        del model
        import gc
        gc.collect()

    assert set(probs) == {"angry", "sad", "neutral", "positive"}, probs
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-3), probs
