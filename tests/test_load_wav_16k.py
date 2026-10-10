# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты `core.falign.load_wav_16k`: звук читается через soundfile, а не через torchaudio.

Что проверяем без условий: форма, dtype, частота 16 кГц, сведение стерео в моно средним
(а не первым каналом). Сверка бит в бит с прежней цепочкой на torchaudio — отдельный тест:
если в окружении torchaudio не читает файлы (нет torchcodec), он пропускается с причиной.
"""
from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
sf = pytest.importorskip("soundfile")

from core import falign  # noqa: E402

# (частота, каналы): моно и стерео на 16 кГц, стерео на 44.1 кГц, моно на 48 кГц
CASES = [
    pytest.param(16000, 1, id="mono-16k"),
    pytest.param(16000, 2, id="stereo-16k"),
    pytest.param(44100, 2, id="stereo-44.1k"),
    pytest.param(48000, 1, id="mono-48k"),
]


def _write(tmp_path, sr: int, ch: int) -> str:
    """WAV PCM16 на полсекунды. Каналы разные по содержанию — иначе среднее и первый канал совпадут."""
    rng = np.random.default_rng(sr * 10 + ch)
    data = rng.uniform(-0.6, 0.6, size=(sr // 2, ch))
    path = tmp_path / f"in_{sr}_{ch}.wav"
    sf.write(str(path), data, sr, subtype="PCM_16")
    return str(path)


def _old_chain(path: str):
    """Прежняя цепочка из трёх копий в core/: torchaudio.load → моно → ресемпл до 16 кГц."""
    import torchaudio

    wav, sr = torchaudio.load(path)
    audio = wav.mean(0) if wav.shape[0] > 1 else wav[0]
    if sr != 16000:
        audio = torchaudio.functional.resample(audio, sr, 16000)
    return audio


@pytest.mark.parametrize("sr,ch", CASES)
def test_load_wav_16k_shape_dtype_rate(tmp_path, sr: int, ch: int) -> None:
    audio = falign.load_wav_16k(_write(tmp_path, sr, ch))
    assert isinstance(audio, torch.Tensor)
    assert audio.dtype == torch.float32
    assert audio.ndim == 1
    # полсекунды на 16 кГц = 8000 отсчётов; ресемпл может дать ±1 отсчёт на округлении
    assert abs(audio.shape[0] - 8000) <= 1


def test_load_wav_16k_stereo_is_channel_mean(tmp_path) -> None:
    path = _write(tmp_path, 16000, 2)
    a, _ = sf.read(path, dtype="float32", always_2d=True)          # [N, C]
    channels_mean = torch.from_numpy(np.ascontiguousarray(a.T)).mean(0)
    audio = falign.load_wav_16k(path)
    assert torch.equal(audio, channels_mean)
    # не первый канал: иначе сведение «wav[0]» прошло бы тест
    assert not torch.equal(audio, torch.from_numpy(np.ascontiguousarray(a[:, 0])))


@pytest.mark.parametrize("sr,ch", CASES)
def test_load_wav_16k_matches_old_torchaudio_chain(tmp_path, sr: int, ch: int) -> None:
    path = _write(tmp_path, sr, ch)
    try:
        old = _old_chain(path)
    except Exception as exc:  # нет torchcodec и т. п. — сверку честно пропускаем
        pytest.skip(f"torchaudio.load в окружении не работает: {type(exc).__name__}: {exc}")
    assert torch.equal(falign.load_wav_16k(path), old)
