# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модульные тесты `core/ctc_asr.py`: универсальный CTC-ASR движок на transformers.

Проверяются все ключевые алгоритмы и решения модуля:
- `_quiet_cut()` (поиск центра самого тихого кадра в заданном диапазоне для шва);
- `_windows()` (нарезка аудио на окна CHUNK с непрерывным покрытием без дыр и перекрытий);
- `_decode_window()` (Greedy CTC декодирование: схлопывание повторов, пропуск pad/blank,
  разбиение по пробелам/разделителям, вычисление min prob, сдвиг по t0);
- `transcribe()` (склейка окон, пропуск огрызков < 0.125с, подгонка хвостов TAIL,
  вызов release_model);
- `get_model()` и `release_model()` (кэширование модели, очистка памяти, выбор устройства
  через pick_device при отсутствии CUDA, fallback на Wav2Vec2Processor);
- `_load_audio()` (сведение стерео в моно, ресемплинг до 16 кГц).
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
import torch

from core import ctc_asr


@pytest.fixture(autouse=True)
def reset_ctc_model() -> Any:
    """Гарантирует чистое состояние кэша модели ctc_asr._MODEL до и после теста."""
    ctc_asr._MODEL = None
    try:
        yield
    finally:
        ctc_asr._MODEL = None


# --------------------------------------------------------------------------- #
# _quiet_cut(): поиск тихого шва
# --------------------------------------------------------------------------- #
def test_quiet_cut_finds_silent_frame() -> None:
    """_quiet_cut находит центр тихой области внутри диапазона [lo:hi]."""
    sr = ctc_asr.SR  # 16000
    # Создаём 2 секунды аудио: громкое (1.0), а в диапазоне 0.8-1.2 секунды — тишина (0.001)
    audio = torch.ones(2 * sr, dtype=torch.float32)
    quiet_start = int(0.8 * sr)
    quiet_end = int(1.2 * sr)
    audio[quiet_start:quiet_end] = 0.001

    lo = int(0.5 * sr)
    hi = int(1.5 * sr)
    cut = ctc_asr._quiet_cut(audio, lo, hi, frame=0.05)

    # Точка реза должна оказаться строго внутри тихой области
    assert quiet_start <= cut <= quiet_end


def test_quiet_cut_short_range_returns_hi() -> None:
    """Если диапазон меньше одного кадра (nf < 1), возвращается hi."""
    audio = torch.ones(1000, dtype=torch.float32)
    # 50мс при 16кГц = 800 сэмплов. Диапазон длиной 400 сэмплов -> nf=0
    lo = 100
    hi = 500
    assert ctc_asr._quiet_cut(audio, lo, hi, frame=0.05) == hi


def test_quiet_cut_custom_frame() -> None:
    """_quiet_cut корректно работает с нестандартным размером кадра."""
    audio = torch.ones(3200, dtype=torch.float32)
    # frame=0.1 сек = 1600 сэмплов. В audio[1600:3200] тишина
    audio[1600:3200] = 0.01
    cut = ctc_asr._quiet_cut(audio, 0, 3200, frame=0.1)
    # Центр второго кадра: 1600 + 800 = 2400
    assert cut == 2400


# --------------------------------------------------------------------------- #
# _windows(): разбиение аудио на окна
# --------------------------------------------------------------------------- #
def test_windows_short_audio_single_window() -> None:
    """Аудио короче CHUNK (20 сек) не делится и возвращает одно окно [(0, n)]."""
    n = int(10 * ctc_asr.SR)
    audio = torch.zeros(n, dtype=torch.float32)
    wins = ctc_asr._windows(audio)
    assert wins == [(0, n)]


def test_windows_long_audio_continuous_coverage() -> None:
    """Длинное аудио делится на несколько окон без дыр и перекрытий (шов-в-шов)."""
    # 50 секунд аудио
    n = int(50 * ctc_asr.SR)
    audio = torch.zeros(n, dtype=torch.float32)
    wins = ctc_asr._windows(audio)

    assert len(wins) >= 3
    assert wins[0][0] == 0
    assert wins[-1][1] == n

    for i in range(len(wins) - 1):
        assert wins[i][1] == wins[i + 1][0], f"Разрыв между окнами {i} и {i + 1}: {wins[i]} и {wins[i + 1]}"


def test_windows_cuts_in_quiet_zones() -> None:
    """Окна режутся в тихих точках, найденных в SEARCH-зоне перед концом окна."""
    sr = ctc_asr.SR
    # 25 секунд аудио: окно 20 сек, поиск в интервале 16-20 сек.
    # Сделаем громким всё, кроме точки 18.0-18.1 сек
    audio = torch.ones(int(25 * sr), dtype=torch.float32)
    quiet_dip = int(18.0 * sr)
    audio[quiet_dip:quiet_dip + int(0.1 * sr)] = 0.0

    wins = ctc_asr._windows(audio)
    assert len(wins) == 2
    # Первая граница a1 должна попасть в район 18 сек
    assert abs(wins[0][1] - (quiet_dip + int(0.05 * sr))) < int(0.05 * sr)


# --------------------------------------------------------------------------- #
# _decode_window(): Greedy CTC декодирование окна
# --------------------------------------------------------------------------- #
class _DummyTokenizer:
    """Заглушка токенайзера HuggingFace для CTC."""

    def __init__(self, vocab: dict[int, str], pad_id: int = 0, delim: str = "|") -> None:
        self.vocab = vocab
        self.pad_token_id = pad_id
        self.word_delimiter_token = delim

    def convert_ids_to_tokens(self, tid: int) -> str:
        return self.vocab.get(tid, f"<unk_{tid}>")


def test_decode_window_basic() -> None:
    """_decode_window выполняет argmax, схлопывает повторы, делит на слова и сдвигает на t0."""
    # Словарь: 0: pad, 1: 'h', 2: 'e', 3: 'l', 4: 'o', 5: '|', 6: 'w', 7: 'r', 8: 'd'
    vocab = {0: "<pad>", 1: "h", 2: "e", 3: "l", 4: "o", 5: "|", 6: "w", 7: "r", 8: "d"}
    tok = _DummyTokenizer(vocab, pad_id=0, delim="|")
    proc = types.SimpleNamespace(tokenizer=tok)

    # Последовательность токенов:
    # 1 ('h'), 1 (повтор -> collapse), 2 ('e'), 3 ('l'), 0 (pad), 3 ('l'), 4 ('o'),
    # 5 ('|' разделитель слов),
    # 6 ('w'), 4 ('o'), 7 ('r'), 3 ('l'), 8 ('d')
    seq = [1, 1, 2, 3, 0, 3, 4, 5, 6, 4, 7, 3, 8]
    T = len(seq)
    V = 10

    # Создаём искусственные логиты с максимальными значениями для наших токенов
    logits = torch.zeros(T, V, dtype=torch.float32)
    for t_idx, tid in enumerate(seq):
        logits[t_idx, tid] = 10.0

    mock_model = MagicMock()
    mock_model.return_value = types.SimpleNamespace(logits=logits.unsqueeze(0))

    clip = torch.zeros(T * 320, dtype=torch.float32)  # SR=16000, 320 сэмплов на кадр = 0.02с
    t0 = 5.0

    words = ctc_asr._decode_window(proc, mock_model, "cpu", clip, t0=t0)
    assert len(words) == 2

    # Первое слово: 'hello'
    assert words[0]["w"] == "hello"
    assert words[0]["start"] >= t0
    assert words[0]["end"] > words[0]["start"]
    assert words[0]["prob"] > 0.9

    # Второе слово: 'world'
    assert words[1]["w"] == "world"
    assert words[1]["start"] >= words[0]["end"]


def test_decode_window_empty_when_all_blank_or_special() -> None:
    """Окно только из pad и специальных токенов возвращает пустой список слов."""
    vocab = {0: "<pad>", 1: "<s>", 2: "</s>", 3: "<unk>"}
    tok = _DummyTokenizer(vocab, pad_id=0)
    proc = types.SimpleNamespace(tokenizer=tok)

    seq = [0, 0, 1, 0, 2, 3, 0]
    logits = torch.zeros(len(seq), 5, dtype=torch.float32)
    for t_idx, tid in enumerate(seq):
        logits[t_idx, tid] = 10.0

    mock_model = MagicMock()
    mock_model.return_value = types.SimpleNamespace(logits=logits.unsqueeze(0))

    clip = torch.zeros(len(seq) * 320, dtype=torch.float32)
    words = ctc_asr._decode_window(proc, mock_model, "cpu", clip, t0=0.0)
    assert words == []


def test_decode_window_sentencepiece_delimiter() -> None:
    """Токены с префиксом границы слова \u2581 коммитят предыдущее слово."""
    # 1: '▁при', 2: 'вет', 3: '▁мир'
    vocab = {0: "<pad>", 1: " ▁при", 2: "вет", 3: "▁мир"}
    # Исправим 1 на '▁при':
    vocab[1] = "▁при"
    tok = _DummyTokenizer(vocab, pad_id=0, delim="|")
    proc = types.SimpleNamespace(tokenizer=tok)

    seq = [1, 2, 3]
    logits = torch.zeros(len(seq), 5, dtype=torch.float32)
    for t_idx, tid in enumerate(seq):
        logits[t_idx, tid] = 10.0

    mock_model = MagicMock()
    mock_model.return_value = types.SimpleNamespace(logits=logits.unsqueeze(0))

    clip = torch.zeros(len(seq) * 320, dtype=torch.float32)
    words = ctc_asr._decode_window(proc, mock_model, "cpu", clip, t0=1.0)
    assert len(words) == 2
    assert words[0]["w"] == "привет"
    assert words[1]["w"] == "мир"


def test_decode_window_empty_sequence() -> None:
    """Если T == 0, функция возвращает пустой список."""
    tok = _DummyTokenizer({0: "<pad>"}, pad_id=0)
    proc = types.SimpleNamespace(tokenizer=tok)

    mock_model = MagicMock()
    mock_model.return_value = types.SimpleNamespace(logits=torch.zeros(0, 5).unsqueeze(0))

    clip = torch.zeros(100, dtype=torch.float32)
    assert ctc_asr._decode_window(proc, mock_model, "cpu", clip, t0=0.0) == []


# --------------------------------------------------------------------------- #
# transcribe(): склейка окон, хвосты, release_model
# --------------------------------------------------------------------------- #
def test_transcribe_orchestration(monkeypatch: pytest.MonkeyPatch) -> None:
    """transcribe объединяет окна, отбрасывает огрызки < 0.125с и подгоняет хвосты TAIL."""
    sr = ctc_asr.SR

    monkeypatch.setattr(ctc_asr, "get_model", lambda mid, dev: ("fake_proc", "fake_model", dev))
    monkeypatch.setattr(ctc_asr, "_load_audio", lambda path: torch.zeros(50 * sr, dtype=torch.float32))

    # Три окна: 0-20с, 20-40с, и микро-огрызок 40-40.05с (меньше SR//8 = 2000 сэмплов)
    wins = [(0, 20 * sr), (20 * sr, 40 * sr), (40 * sr, int(40.05 * sr))]
    monkeypatch.setattr(ctc_asr, "_windows", lambda audio: wins)

    decoded_calls: list[tuple[float, int]] = []

    def mock_decode(proc: Any, model: Any, dev: str, clip: Any, t0: float) -> list[dict[str, Any]]:
        decoded_calls.append((t0, len(clip)))
        if t0 == 0.0:
            return [
                {"w": "первое", "start": 1.0, "end": 1.5, "prob": 0.9},
                {"w": "второе", "start": 1.55, "end": 1.8, "prob": 0.85},
            ]
        elif t0 == 20.0:
            return [{"w": "третье", "start": 21.0, "end": 21.4, "prob": 0.95}]
        return [{"w": "огрызок", "start": 40.0, "end": 40.04, "prob": 0.5}]

    monkeypatch.setattr(ctc_asr, "_decode_window", mock_decode)
    release_mock = MagicMock(return_value=True)
    monkeypatch.setattr(ctc_asr, "release_model", release_mock)

    emitted: list[str] = []

    def mock_emit(msg: str, **kw: Any) -> None:
        emitted.append(msg.format(**kw))

    words = ctc_asr.transcribe("test.wav", "org/ctc", emit=mock_emit, release=True)

    # Микро-окно пропущено: всего 2 вызова _decode_window
    assert len(decoded_calls) == 2
    assert len(words) == 3
    assert [w["w"] for w in words] == ["первое", "второе", "третье"]

    # Проверка TAIL = 0.10:
    # 'первое' кончалось на 1.5, следующее слово на 1.55.
    # end = min(1.5 + 0.10, max(1.5, 1.55 - 0.02)) = min(1.60, 1.53) = 1.53
    assert words[0]["end"] == 1.53

    # Последнее слово 'третье': nxt = 21.4 + 0.10 = 21.50; nxt - 0.02 = 21.48
    assert words[2]["end"] == 21.48

    release_mock.assert_called_once()
    assert any("3 слов" in m for m in emitted)


def test_transcribe_release_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """При release=False release_model не вызывается."""
    monkeypatch.setattr(ctc_asr, "get_model", lambda mid, dev: ("fake_proc", "fake_model", dev))
    monkeypatch.setattr(ctc_asr, "_load_audio", lambda path: torch.zeros(16000, dtype=torch.float32))
    monkeypatch.setattr(ctc_asr, "_windows", lambda audio: [(0, 16000)])
    monkeypatch.setattr(ctc_asr, "_decode_window", lambda *a, **k: [])
    release_mock = MagicMock()
    monkeypatch.setattr(ctc_asr, "release_model", release_mock)

    ctc_asr.transcribe("test.wav", "org/ctc", release=False)
    release_mock.assert_not_called()


# --------------------------------------------------------------------------- #
# get_model() и release_model()
# --------------------------------------------------------------------------- #
def test_get_model_caching_and_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_model кэширует модель и не загружает её повторно при тех же параметрах."""
    mock_proc = MagicMock()
    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.eval.return_value = mock_model

    auto_proc_mock = MagicMock(return_value=mock_proc)
    auto_model_mock = MagicMock(return_value=mock_model)

    fake = types.ModuleType("transformers")
    fake.AutoProcessor = MagicMock()  # type: ignore[attr-defined]
    fake.AutoProcessor.from_pretrained = auto_proc_mock
    fake.AutoModelForCTC = MagicMock()  # type: ignore[attr-defined]
    fake.AutoModelForCTC.from_pretrained = auto_model_mock
    fake.Wav2Vec2Processor = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    # Первый вызов: загрузка
    p1, m1, d1 = ctc_asr.get_model("org/ctc-model", device="cuda")
    assert p1 is mock_proc
    assert m1 is mock_model
    assert d1 == "cuda"
    assert auto_proc_mock.call_count == 1
    assert auto_model_mock.call_count == 1

    # Второй вызов с теми же параметрами: из кэша
    p2, m2, d2 = ctc_asr.get_model("org/ctc-model", device="cuda")
    assert p2 is mock_proc
    assert auto_proc_mock.call_count == 1
    assert auto_model_mock.call_count == 1

    # Третий вызов с другой моделью: перезагрузка
    ctc_asr.get_model("org/other-model", device="cuda")
    assert auto_proc_mock.call_count == 2
    assert auto_model_mock.call_count == 2


def test_get_model_picks_device_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """При device='cuda' без доступной NVIDIA pick_device выбирает mps/cpu."""
    mock_proc = MagicMock()
    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.eval.return_value = mock_model

    fake = types.ModuleType("transformers")
    fake.AutoProcessor = MagicMock()  # type: ignore[attr-defined]
    fake.AutoProcessor.from_pretrained = MagicMock(return_value=mock_proc)
    fake.AutoModelForCTC = MagicMock()  # type: ignore[attr-defined]
    fake.AutoModelForCTC.from_pretrained = MagicMock(return_value=mock_model)
    fake.Wav2Vec2Processor = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr("core.device.pick_device", lambda: "cpu")

    _, _, chosen_dev = ctc_asr.get_model("org/ctc-model", device="cuda")
    assert chosen_dev == "cpu"
    mock_model.to.assert_called_with("cpu")


def test_get_model_processor_fallback_to_wav2vec2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если AutoProcessor падает (на моделях с LM), берётся Wav2Vec2Processor."""
    mock_w2v_proc = MagicMock()
    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.eval.return_value = mock_model

    fake = types.ModuleType("transformers")
    fake.AutoProcessor = MagicMock()  # type: ignore[attr-defined]
    fake.AutoProcessor.from_pretrained = MagicMock(side_effect=ImportError("No pyctcdecode"))
    w2v_proc_mock = MagicMock(return_value=mock_w2v_proc)
    fake.Wav2Vec2Processor = MagicMock()  # type: ignore[attr-defined]
    fake.Wav2Vec2Processor.from_pretrained = w2v_proc_mock
    fake.AutoModelForCTC = MagicMock()  # type: ignore[attr-defined]
    fake.AutoModelForCTC.from_pretrained = MagicMock(return_value=mock_model)
    monkeypatch.setitem(sys.modules, "transformers", fake)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    p, _, _ = ctc_asr.get_model("org/ctc-model", device="cuda")
    assert p is mock_w2v_proc
    w2v_proc_mock.assert_called_once_with("org/ctc-model")


def test_release_model_frees_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """release_model очищает глобал _MODEL, вызывает gc.collect и empty_cache."""
    ctc_asr._MODEL = (("dummy", "cpu"), MagicMock(), MagicMock())
    empty_cache_mock = MagicMock()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", empty_cache_mock)

    assert ctc_asr.release_model() is True
    assert ctc_asr._MODEL is None
    empty_cache_mock.assert_called_once()

    # Повторный вызов, когда модель уже None: возвращает False
    assert ctc_asr.release_model() is False


# --------------------------------------------------------------------------- #
# _load_audio(): torchaudio, стерео -> моно, ресемплинг
# --------------------------------------------------------------------------- #
def test_load_audio_mono(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одноканальное 16 кГц аудио возвращается как 1D тензор."""
    fake_tensor = torch.zeros(1, 16000)
    monkeypatch.setattr("torchaudio.load", lambda path: (fake_tensor, 16000))

    audio = ctc_asr._load_audio("mono.wav")
    assert audio.shape == (16000,)


def test_load_audio_stereo_downmix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Двухканальное аудио усредняется по каналам (mean(0))."""
    ch1 = torch.ones(1, 16000)
    ch2 = torch.full((1, 16000), 3.0)
    stereo = torch.cat([ch1, ch2], dim=0)
    monkeypatch.setattr("torchaudio.load", lambda path: (stereo, 16000))

    audio = ctc_asr._load_audio("stereo.wav")
    assert audio.shape == (16000,)
    assert torch.allclose(audio, torch.full((16000,), 2.0))


def test_load_audio_resampling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аудио с частотой 44100 ресемплируется в 16000."""
    fake_tensor = torch.zeros(1, 44100)
    monkeypatch.setattr("torchaudio.load", lambda path: (fake_tensor, 44100))
    resample_mock = MagicMock(return_value=torch.zeros(16000))
    monkeypatch.setattr("torchaudio.functional.resample", resample_mock)

    audio = ctc_asr._load_audio("audio.wav")
    assert audio.shape == (16000,)
    resample_mock.assert_called_once()
    assert resample_mock.call_args[0][1] == 44100
    assert resample_mock.call_args[0][2] == 16000
