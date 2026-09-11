# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты санитарного гарда нарезки и восстановления при сбое чтения звука (задание DN).

Проверяем:
1. Зеркальный гард: речь > 30с одним куском вызывает отказ (SystemExit).
2. Одна попытка перевыпустить WAV через sync.extract_audio при сбое чтения звука.
3. Диагностика в сообщении об ошибке (наличие, размер, файлы в каталоге).
"""
import os
import sys
import pytest
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.gigaam_cut import pipeline as pipeline


def test_audio_file_diag_existing(tmp_path):
    """Диагностика существующего файла: размер и список файлов в каталоге."""
    f = tmp_path / "test.wav"
    f.write_bytes(b"12345678")
    diag = pipeline._audio_file_diag(str(f))
    assert "существует, 8 байт" in diag
    assert "test.wav" in diag


def test_audio_file_diag_missing(tmp_path):
    """Диагностика отсутствующего файла: пометка НЕ существует и каталог."""
    missing = tmp_path / "missing.wav"
    diag = pipeline._audio_file_diag(str(missing))
    assert "НЕ существует" in diag


def test_mirror_guard_raises_on_single_chunk_60s(monkeypatch, tmp_path):
    """Зеркальный гард: keep из одного куска на 60-секундной речи -> SystemExit."""
    words = [{"w": "слово", "start": 0.0, "end": 60.0}]
    xml_out = str(tmp_path / "out.xml")

    monkeypatch.setattr(pipeline, "transcribe_words_whole",
                        lambda *a, **k: ("текст", words))
    monkeypatch.setattr(pipeline, "decide_markup",
                        lambda *a, **k: ({0}, set(), {}, []))
    monkeypatch.setattr(pipeline, "postprocess", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "refine_keep",
                        lambda keep, *a, **k: (keep, [0]))
    monkeypatch.setattr(pipeline, "_cut_breaths",
                        lambda keep, assign, *a, **k: (keep, assign, []))

    xml_built = False
    def fake_build(*a, **k):
        nonlocal xml_built
        xml_built = True
    monkeypatch.setattr(pipeline.xmlbuild, "build", fake_build)

    with pytest.raises(SystemExit) as exc_info:
        pipeline._run("dummy.wav", ["cam1.mp4"], [0.0], xml_out, scale="100%", emit=lambda *a, **k: None)

    assert "одним куском" in str(exc_info.value)
    assert not xml_built, "XML не должен собираться при срабатывании санитарного гарда"


def test_audio_retry_succeeds_after_reextract(monkeypatch, tmp_path):
    """Сбой чтения звука: перевыпуск WAV и успешное продолжение пайплайна."""
    words = [{"w": "раз", "start": 0.0, "end": 5.0}, {"w": "два", "start": 6.0, "end": 10.0}]
    xml_out = str(tmp_path / "out.xml")

    monkeypatch.setattr(pipeline, "transcribe_words_whole",
                        lambda *a, **k: ("текст", words))
    monkeypatch.setattr(pipeline, "decide_markup",
                        lambda *a, **k: ({0, 1}, set(), {}, []))
    monkeypatch.setattr(pipeline, "postprocess", lambda *a, **k: None)

    extracted = []
    def fake_extract(cam, out_wav):
        extracted.append((cam, out_wav))
    monkeypatch.setattr(pipeline.sync, "extract_audio", fake_extract)

    calls = 0
    def fake_refine(keep, *a, **k):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sf.LibsndfileError(1, "Corrupted audio file")
        return [(0.0, 5.0), (6.0, 10.0)], [0, 1]

    monkeypatch.setattr(pipeline, "refine_keep", fake_refine)
    monkeypatch.setattr(pipeline, "_cut_breaths",
                        lambda keep, assign, *a, **k: (keep, assign, []))
    monkeypatch.setattr(pipeline.xmlbuild, "build", lambda *a, **k: "ok")

    keep, cutlog, draft, info = pipeline._run(
        str(tmp_path / "a0.wav"), ["cam1.mp4"], [0.0], xml_out, scale="100%",
        no_draft=True, emit=lambda *a, **k: None)

    assert calls == 2
    assert len(extracted) == 1
    assert extracted[0][0] == "cam1.mp4"
    assert len(keep) == 2


def test_audio_retry_fails_raises_system_exit(monkeypatch, tmp_path):
    """Сбой чтения звука: если перевыпуск не помог — SystemExit с диагностикой."""
    words = [{"w": "раз", "start": 0.0, "end": 5.0}, {"w": "два", "start": 6.0, "end": 10.0}]
    xml_out = str(tmp_path / "out.xml")

    monkeypatch.setattr(pipeline, "transcribe_words_whole",
                        lambda *a, **k: ("текст", words))
    monkeypatch.setattr(pipeline, "decide_markup",
                        lambda *a, **k: ({0, 1}, set(), {}, []))
    monkeypatch.setattr(pipeline, "postprocess", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.sync, "extract_audio", lambda *a, **k: None)

    def always_fail(keep, *a, **k):
        raise sf.LibsndfileError(1, "System error opening wav")

    monkeypatch.setattr(pipeline, "refine_keep", always_fail)

    with pytest.raises(SystemExit) as exc_info:
        pipeline._run(str(tmp_path / "a0.wav"), ["cam1.mp4"], [0.0], xml_out, scale="100%",
                      no_draft=True, emit=lambda *a, **k: None)

    assert "Не удалось прочитать аудио" in str(exc_info.value)
    assert "Диагностика:" in str(exc_info.value)
