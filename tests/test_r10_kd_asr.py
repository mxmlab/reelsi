# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты распознавания (KD): фильтр титров, парсинг asr_engines.json, сбой GigaAM."""
import json
import subprocess
import sys
import tempfile
import numpy as np
import pytest
import soundfile as sf

from core import asr_backends
from core import omni_asr
from core.transcribe import _drop_segment


class _DummySegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def test_drop_segment_credits_and_bare():
    """Проверка разделения авто-титров, подписей с именами и живой речи."""
    # Живая речь со словами «корректор» / «редактор субтитров» при высокой уверенности -> оставить
    assert not _drop_segment(_DummySegment("корректор пришёл вовремя", no_speech_prob=0.02, avg_logprob=-0.1))
    assert not _drop_segment(_DummySegment("редактор субтитров опоздал", no_speech_prob=0.02, avg_logprob=-0.1))

    # Те же фразы при низкой уверенности Whisper -> выкинуть
    assert _drop_segment(_DummySegment("корректор пришёл вовремя", no_speech_prob=0.8, avg_logprob=-0.9))
    assert _drop_segment(_DummySegment("редактор субтитров опоздал", no_speech_prob=0.8, avg_logprob=-0.9))

    # Подписи авторов субтитров с инициалами/именами (галлюцинация) -> выкинуть всегда
    assert _drop_segment(_DummySegment(
        "Редактор субтитров А.Семкин Корректор А.Егорова", no_speech_prob=0.02, avg_logprob=-0.1
    ))
    assert _drop_segment(_DummySegment("Корректор А. Егорова", no_speech_prob=0.02, avg_logprob=-0.1))
    assert _drop_segment(_DummySegment("редактор субтитров A.Smith", no_speech_prob=0.02, avg_logprob=-0.1))

    # Классические авто-титры YouTube -> выкинуть всегда
    assert _drop_segment(_DummySegment("субтитры сделал DimaTorzok", no_speech_prob=0.02, avg_logprob=-0.1))

    # Дежурные призывы при высокой уверенности -> оставить (прежнее поведение)
    assert not _drop_segment(_DummySegment("подписывайтесь на канал", no_speech_prob=0.02, avg_logprob=-0.1))


@pytest.mark.parametrize("content", [
    "null",
    "42",
    '"x"',
    '{"engines": 42}',
    '{"engines": null}',
    '[1, "a", {"model": 5}, {"model": "m", "id": 7}]',
    '{"invalid json": ',
])
def test_custom_asr_engines_malformed(tmp_path, monkeypatch, content):
    """Битый или некорректный asr_engines.json возвращает [] без исключений."""
    p = tmp_path / "asr_engines.json"
    p.write_text(content, encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(p))

    res = asr_backends._custom()
    assert res == []


def test_custom_asr_engines_valid(tmp_path, monkeypatch):
    """Корректная запись в asr_engines.json успешно парсится."""
    p = tmp_path / "asr_engines.json"
    p.write_text(json.dumps([{"model": "org/m", "lang": "de"}]), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(p))

    res = asr_backends._custom()
    assert len(res) == 1
    assert res[0]["id"] == "ctc:de"
    assert res[0]["lang"] == "de"
    assert res[0]["model"] == "org/m"
    assert res[0]["device"] == "cuda"
    assert res[0]["kind"] == "ctc"


def test_omni_asr_gigaam_failure_exits_cleanly(tmp_path, monkeypatch, capsys):
    """При ошибке инференса GigaAM процесс omni_asr падает с кодом 1, выводя ошибку
    в stderr, не записывает json и удаляет временные wav."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    wav_file = tmp_path / "test.wav"
    sf.write(str(wav_file), np.zeros(16000, dtype=np.int16), 16000, subtype="PCM_16")

    intervals_file = tmp_path / "intervals.json"
    intervals_file.write_text(json.dumps([[0.0, 1.0]]), encoding="utf-8")

    out_file = tmp_path / "out.omni.json"

    class FailingGigaAM:
        def transcribe(self, path):
            raise RuntimeError("CUDA memory allocation failed")

        def transcribe_longform(self, path):
            raise RuntimeError("CUDA memory allocation failed")

    mock_gigaam = type(sys)("gigaam")
    mock_gigaam.load_model = lambda name: FailingGigaAM()
    monkeypatch.setitem(sys.modules, "gigaam", mock_gigaam)

    with pytest.raises(SystemExit) as exc_info:
        omni_asr.main([
            str(wav_file),
            "--intervals", str(intervals_file),
            "--out", str(out_file),
            "--engine", "gigaam",
        ])

    assert exc_info.value.code != 0
    assert not out_file.exists()

    captured = capsys.readouterr()
    assert "Ошибка GigaAM инференса на куске 0.0–1.0: CUDA memory allocation failed" in captured.err

    # Проверяем отсутствие оставшихся временных wav
    remaining_wavs = list(tmp_path.glob("_omni_gigaam_*.wav"))
    assert len(remaining_wavs) == 0


def test_omni_caller_wraps_called_process_error(tmp_path, monkeypatch):
    """_omni оборачивает CalledProcessError в RuntimeError с текстом из stderr."""
    fake_wav = str(tmp_path / "fake.wav")

    def mock_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args[0] if args else kwargs.get("cmd"),
            stderr="Ошибка GigaAM инференса на куске 0.0–1.0: out of memory\n".encode("utf-8")
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError) as exc_info:
        asr_backends._omni(fake_wav, engine="gigaam")

    assert "Ошибка GigaAM инференса" in str(exc_info.value)
