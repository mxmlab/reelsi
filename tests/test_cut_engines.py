# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты движков нарезки и флага cut в каталоге asr_backends."""
import json
import os
import sys
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import aicut
from core import asr_backends
from core.gigaam_cut.asr import transcribe_words_for_cut


def test_catalog_cut_flag():
    """У каждого движка есть булев ключ cut, и cut=True только у CTC-моделей."""
    all_engines = asr_backends.engines()
    assert len(all_engines) > 0

    for e in all_engines:
        assert "cut" in e, f"У движка {e['id']} отсутствует ключ 'cut'"
        assert isinstance(e["cut"], bool), f"У движка {e['id']} 'cut' не bool"

    cut_true_ids = {e["id"] for e in all_engines if e["cut"]}
    # gigaam и gigaam:multilingual_large_ctc обязаны иметь cut=True
    assert "gigaam" in cut_true_ids
    assert "gigaam:multilingual_large_ctc" in cut_true_ids

    # Сторож: RNN-T, Whisper, whisper.cpp и Omni в нарезку не попадают (cut=False)
    assert "gigaam:v3_rnnt" not in cut_true_ids
    assert "gigaam:v3_e2e_rnnt" not in cut_true_ids
    assert "omni" not in cut_true_ids
    assert "whisper:large-v3" not in cut_true_ids
    assert "whisper.cpp:large-v3" not in cut_true_ids


def test_custom_engine_gets_cut_true(tmp_path):
    """Пользовательская CTC-модель из asr_engines.json получает cut: True."""
    custom_json = tmp_path / "asr_engines.json"
    custom_json.write_text(
        json.dumps([
            {"id": "ctc:custom_de", "label": "Custom German CTC", "lang": "de", "model": "user/wav2vec2-de"}
        ]),
        encoding="utf-8"
    )
    with patch("core.asr_backends.ENGINES_JSON", str(custom_json)):
        custom = asr_backends._custom()
        assert len(custom) == 1
        assert custom[0]["id"] == "ctc:custom_de"
        assert custom[0]["cut"] is True


def test_transcribe_words_for_cut_rejects_non_cut_engines():
    """transcribe_words_for_cut падает с понятным сообщением на движках без cut: True."""
    for bad_eng in ["whisper:large-v3", "gigaam:v3_rnnt", "omni", "whisper.cpp:medium", "non_existent"]:
        with pytest.raises(ValueError) as exc:
            transcribe_words_for_cut("fake.wav", engine=bad_eng)
        assert "не годен для нарезки" in str(exc.value)


def test_transcribe_words_for_cut_calls_correct_gigaam_model():
    """transcribe_words_for_cut передает верное имя головы в transcribe_words_whole."""
    fake_words = [{"w": "тест", "start": 0.0, "end": 0.5}]

    with patch("core.gigaam_cut.asr.transcribe_words_whole", return_value=("тест", fake_words)) as mock_whole:
        text, words = transcribe_words_for_cut("fake.wav", engine="gigaam:multilingual_large_ctc")
        assert text == "тест"
        assert words == fake_words
        mock_whole.assert_called_once()
        _, kwargs = mock_whole.call_args
        assert kwargs.get("model_name") == "multilingual_large_ctc"

    with patch("core.gigaam_cut.asr.transcribe_words_whole", return_value=("тест", fake_words)) as mock_whole:
        text, words = transcribe_words_for_cut("fake.wav", engine="gigaam")
        assert text == "тест"
        mock_whole.assert_called_once()
        _, kwargs = mock_whole.call_args
        assert kwargs.get("model_name") == "v3_ctc"


def test_cut_asr_engine_fallback_on_missing(tmp_path):
    """Если сохраненный движок пропал из каталога или не имеет cut, возвращается gigaam."""
    cfg_path = tmp_path / "ai_config.json"
    cfg_path.write_text(json.dumps({"profiles": {"LM": {}}, "active": "LM", "active_cut_asr": "deleted_model"}), encoding="utf-8")

    with patch("core.aicut.config.AI_CONFIG_PATH", str(cfg_path)):
        # Удаленный движок -> gigaam
        assert aicut.cut_asr_engine() == "gigaam"

    cfg_path.write_text(json.dumps({"profiles": {"LM": {}}, "active": "LM", "active_cut_asr": "whisper:large-v3"}), encoding="utf-8")
    with patch("core.aicut.config.AI_CONFIG_PATH", str(cfg_path)):
        # Движок без cut: True -> gigaam
        assert aicut.cut_asr_engine() == "gigaam"
