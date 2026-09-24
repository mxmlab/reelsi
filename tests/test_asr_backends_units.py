# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модульные тесты `core/asr_backends.py`: единый реестр ASR-движков.

Проверяются все ключевые решения модуля:
- `engines()` и `engine_meta()` (список встроенных и пользовательских движков,
  дефолты, нормализация имен whisper, неизвестные движки);
- `_custom()` (парсинг `asr_engines.json`, обработка битых JSON, пропуск невалидных записей,
  генерация ID и дефолтные параметры CTC);
- `register()` (регистрация внешнего движка в `ASR_BACKENDS`);
- `_widen()` (расширение коротких слов: границы, соседи, `min_dur`, `gap`);
- `_split_phrases()` (линейная интерполяция таймингов фраз в слова);
- `transcribe_words()` (маршрутизация по движкам, применение/пропуск словаря терминов,
  обработка ошибок `terms`);
- Адаптеры `_whisper`, `_ctc`, `_gigaam`, `_whisper_cpp`, `_omni` на заглушках:
  управление памятью (`unload_ours`), параметры вызовов, обработка сбоев и таймаутов.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from core import asr_backends
from core.umsg import ReelsiError


@pytest.fixture(autouse=True)
def restore_backends() -> Any:
    """Сохранение и восстановление словаря ASR_BACKENDS между тестами."""
    orig = dict(asr_backends.ASR_BACKENDS)
    try:
        yield
    finally:
        asr_backends.ASR_BACKENDS.clear()
        asr_backends.ASR_BACKENDS.update(orig)


# --------------------------------------------------------------------------- #
# engines() и engine_meta()
# --------------------------------------------------------------------------- #
def test_builtin_engines_catalog() -> None:
    """Встроенный каталог содержит все ожидаемые размеры Whisper, GigaAM, Omni, whisper.cpp."""
    all_engs = asr_backends.engines()
    ids = {e["id"] for e in all_engs}

    for size in asr_backends.WHISPER_SIZES:
        assert f"whisper:{size}" in ids
        assert f"whisper.cpp:{size}" in ids

    assert "gigaam" in ids
    assert "gigaam:v3_rnnt" in ids
    assert "gigaam:v3_e2e_rnnt" in ids
    assert "gigaam:multilingual_large_ctc" in ids
    assert "omni" in ids

    # Проверка обязательных полей контракта метаданных
    for e in all_engs:
        for k in ("id", "label", "lang", "kind", "prob", "subs", "selfcheck", "cut"):
            assert k in e, f"Ключ {k} отсутствует в метаданных движка {e.get('id')}"


def test_engine_meta_resolution() -> None:
    """engine_meta корректно обрабатывает None, дефолт whisper и префиксы."""
    # None и пустая строка нормализуются в whisper:large-v3
    meta_none = asr_backends.engine_meta(None)
    assert meta_none is not None
    assert meta_none["id"] == "whisper:large-v3"

    meta_empty = asr_backends.engine_meta("")
    assert meta_empty is not None
    assert meta_empty["id"] == "whisper:large-v3"

    meta_whisper = asr_backends.engine_meta("whisper")
    assert meta_whisper is not None
    assert meta_whisper["id"] == "whisper:large-v3"

    meta_gigaam = asr_backends.engine_meta("gigaam:v3_rnnt")
    assert meta_gigaam is not None
    assert meta_gigaam["kind"] == "ctc"
    assert meta_gigaam["gigaam"] == "v3_rnnt"

    meta_unknown = asr_backends.engine_meta("non_existent_engine_foo")
    assert meta_unknown is None


# --------------------------------------------------------------------------- #
# _custom() и asr_engines.json
# --------------------------------------------------------------------------- #
def test_custom_engines_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Отсутствующий файл asr_engines.json возвращает пустой список без ошибок."""
    missing = str(tmp_path / "non_existent_engines.json")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", missing)
    assert asr_backends._custom() == []


def test_custom_engines_dict_with_engines_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Формат словаря с ключом 'engines': корректная генерация ID и дефолты."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text(json.dumps({
        "engines": [
            {"model": "user/wav2vec2-es", "lang": "es", "label": "Spanish CTC"},
            {"id": "my_french", "model": "org/w2v-fr", "lang": "fr", "device": "cpu"},
            {"id": "ctc:already_prefixed", "model": "org/w2v-it"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    custom = asr_backends._custom()
    assert len(custom) == 3

    # Первый: авто ID из lang
    assert custom[0]["id"] == "ctc:es"
    assert custom[0]["label"] == "Spanish CTC"
    assert custom[0]["model"] == "user/wav2vec2-es"
    assert custom[0]["device"] == "cuda"
    assert custom[0]["kind"] == "ctc"
    assert custom[0]["prob"] is True
    assert custom[0]["cut"] is True

    # Второй: явный ID без ctc: -> префикс добавляется
    assert custom[1]["id"] == "ctc:my_french"
    assert custom[1]["device"] == "cpu"

    # Третий: ID уже с ctc:, нет lang -> авто label и lang='?'
    assert custom[2]["id"] == "ctc:already_prefixed"
    assert custom[2]["lang"] == "?"


def test_custom_engines_invalid_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Битые типы в asr_engines.json пишут warning в лог и не роняют выполнение."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text(json.dumps("not_a_dict_or_list"), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    with caplog.at_level(logging.WARNING):
        assert asr_backends._custom() == []
    assert "ожидается список или словарь" in caplog.text


def test_custom_engines_dict_engines_not_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Словарь с 'engines', не являющимся списком, логирует предупреждение."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text(json.dumps({"engines": "wrong_type"}), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    with caplog.at_level(logging.WARNING):
        assert asr_backends._custom() == []
    assert "поле 'engines' должно быть списком" in caplog.text


def test_custom_engines_filter_invalid_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Записи с отсутствующим model или недопустимыми типами полей отфильтровываются."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text(json.dumps([
        "not_a_dict",
        {"id": "no_model"},
        {"model": ""},
        {"model": 12345},
        {"model": "good/model", "lang": 42},  # нестроковый lang
        {"model": "good/model2", "device": 1},  # нестроковый device
        {"model": "valid/model", "lang": "de"}
    ]), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    res = asr_backends._custom()
    assert len(res) == 1
    assert res[0]["model"] == "valid/model"
    assert res[0]["id"] == "ctc:de"


def test_custom_engines_syntax_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Синтаксическая ошибка в JSON логируется как предупреждение."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text("{broken json...", encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    with caplog.at_level(logging.WARNING):
        assert asr_backends._custom() == []
    assert "Не удалось прочитать" in caplog.text


# --------------------------------------------------------------------------- #
# register()
# --------------------------------------------------------------------------- #
def test_register_backend() -> None:
    """register регистрирует функцию в словаре ASR_BACKENDS."""
    dummy_fn = lambda wav, **kw: [{"w": "test", "start": 0.0, "end": 1.0}]  # noqa: E731
    asr_backends.register("my_custom_engine", dummy_fn)
    assert asr_backends.ASR_BACKENDS["my_custom_engine"] is dummy_fn


# --------------------------------------------------------------------------- #
# _widen(): расширение коротких слов (RNN-T)
# --------------------------------------------------------------------------- #
def test_widen_already_long_words_untouched() -> None:
    """Слова длиннее min_dur (0.14) не изменяются."""
    words = [
        {"w": "привет", "start": 1.0, "end": 1.5},
        {"w": "мир", "start": 2.0, "end": 2.2},
    ]
    res = asr_backends._widen(words, min_dur=0.14, gap=0.02)
    assert res == [
        {"w": "привет", "start": 1.0, "end": 1.5},
        {"w": "мир", "start": 2.0, "end": 2.2},
    ]


def test_widen_expands_forward_when_space_available() -> None:
    """Короткое слово растягивается вперёд на свободное место до start + min_dur."""
    words = [
        {"w": "да", "start": 0.5, "end": 0.54},
        {"w": "конечно", "start": 1.5, "end": 2.0},
    ]
    res = asr_backends._widen(words, min_dur=0.14, gap=0.02)
    assert res[0]["start"] == 0.5
    assert res[0]["end"] == round(0.5 + 0.14, 3)
    assert res[1]["start"] == 1.5


def test_widen_forward_capped_by_next_word() -> None:
    """Если следующее слово близко, расширение вперёд ограничивается next_start - gap."""
    words = [
        {"w": "в", "start": 1.0, "end": 1.04},
        {"w": "дом", "start": 1.10, "end": 1.40},
    ]
    # nxt = 1.10 - 0.02 = 1.08.
    # end = min(max(1.04, 1.0 + 0.14), max(1.04, 1.08)) = min(1.14, 1.08) = 1.08.
    # end - start = 0.08 < 0.14 -> расширение назад:
    # prv = 0.0 (первое слово). start = max(min(1.0, 1.08 - 0.14), 0.0) = max(0.94, 0.0) = 0.94.
    res = asr_backends._widen(words, min_dur=0.14, gap=0.02)
    assert res[0]["start"] == 0.94
    assert res[0]["end"] == 1.08
    assert round(res[0]["end"] - res[0]["start"], 3) == 0.14


def test_widen_bounded_by_previous_and_next_word() -> None:
    """Короткое слово между двумя соседями не наезжает на них с учётом gap."""
    words = [
        {"w": "он", "start": 0.0, "end": 0.50},
        {"w": "и", "start": 0.55, "end": 0.59},  # длительность 0.04
        {"w": "она", "start": 0.63, "end": 1.00},
    ]
    # nxt = 0.63 - 0.02 = 0.61. end становится 0.61.
    # Длительность 0.61 - 0.55 = 0.06 < 0.14.
    # prv = 0.50 + 0.02 = 0.52.
    # start = max(0.61 - 0.14, 0.52) = max(0.47, 0.52) = 0.52.
    res = asr_backends._widen(words, min_dur=0.14, gap=0.02)
    assert res[1]["start"] == 0.52
    assert res[1]["end"] == 0.61
    assert res[1]["start"] >= res[0]["end"] + 0.02
    assert res[1]["end"] <= res[2]["start"] - 0.02


def test_widen_single_short_word() -> None:
    """Одиночное короткое слово в начале расширяется вперёд до min_dur."""
    words = [{"w": "а", "start": 0.0, "end": 0.05}]
    res = asr_backends._widen(words, min_dur=0.14, gap=0.02)
    assert res[0]["start"] == 0.0
    assert res[0]["end"] == 0.14


# --------------------------------------------------------------------------- #
# _split_phrases(): интерполяция фраз в слова
# --------------------------------------------------------------------------- #
def test_split_phrases_empty_and_whitespace() -> None:
    """Пустой ввод или фразы с пробельным текстом возвращают пустой список."""
    assert asr_backends._split_phrases([]) == []
    assert asr_backends._split_phrases(None) == []
    assert asr_backends._split_phrases([{"text": "", "start": 0.0, "end": 1.0}]) == []
    assert asr_backends._split_phrases([{"text": "   ", "start": 0.0, "end": 1.0}]) == []


def test_split_phrases_single_token() -> None:
    """Фраза из одного слова получает тайминги всей фразы."""
    phrases = [{"text": "здравствуйте", "start": 1.25, "end": 2.75}]
    res = asr_backends._split_phrases(phrases)
    assert len(res) == 1
    assert res[0] == {"w": "здравствуйте", "start": 1.25, "end": 2.75}


def test_split_phrases_multiple_tokens_interpolation() -> None:
    """Фраза делится на слова с равномерной интерполяцией таймингов."""
    phrases = [
        {"text": "раз два три", "start": 0.0, "end": 3.0},
        {"text": "четыре пять", "start": 4.0, "end": 6.0},
    ]
    res = asr_backends._split_phrases(phrases)
    assert len(res) == 5

    assert res[0] == {"w": "раз", "start": 0.0, "end": 1.0}
    assert res[1] == {"w": "два", "start": 1.0, "end": 2.0}
    assert res[2] == {"w": "три", "start": 2.0, "end": 3.0}

    assert res[3] == {"w": "четыре", "start": 4.0, "end": 5.0}
    assert res[4] == {"w": "пять", "start": 5.0, "end": 6.0}


# --------------------------------------------------------------------------- #
# transcribe_words(): выбор движка, термины, fallback
# --------------------------------------------------------------------------- #
def test_transcribe_words_routes_to_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """transcribe_words для whisper передаёт нужный model_size."""
    mock_whisper = MagicMock(return_value=[{"w": "тест", "start": 0.0, "end": 0.5}])
    monkeypatch.setattr(asr_backends, "_whisper", mock_whisper)

    res = asr_backends.transcribe_words("clip.wav", engine="whisper:medium", use_terms=False)
    assert res == [{"w": "тест", "start": 0.0, "end": 0.5}]
    mock_whisper.assert_called_once_with("clip.wav", model_size="medium")


def test_transcribe_words_routes_to_ctc(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """transcribe_words для движка ctc: вызывает _ctc с моделью и устройством."""
    cfg = tmp_path / "asr_engines.json"
    cfg.write_text(json.dumps([{"id": "ctc:german", "model": "org/ctc-de", "device": "cuda"}]), encoding="utf-8")
    monkeypatch.setattr(asr_backends, "ENGINES_JSON", str(cfg))

    mock_ctc = MagicMock(return_value=[{"w": "hallo", "start": 0.1, "end": 0.6}])
    monkeypatch.setattr(asr_backends, "_ctc", mock_ctc)

    res = asr_backends.transcribe_words("audio.wav", engine="ctc:german", use_terms=False)
    assert res == [{"w": "hallo", "start": 0.1, "end": 0.6}]
    mock_ctc.assert_called_once_with("audio.wav", "org/ctc-de", "cuda")


def test_transcribe_words_routes_to_gigaam(monkeypatch: pytest.MonkeyPatch) -> None:
    """transcribe_words для gigaam вызывает _gigaam с именем головы."""
    mock_gigaam = MagicMock(return_value=[{"w": "привет", "start": 0.0, "end": 0.4}])
    monkeypatch.setattr(asr_backends, "_gigaam", mock_gigaam)

    res = asr_backends.transcribe_words("clip.wav", engine="gigaam:v3_rnnt", use_terms=False)
    assert res == [{"w": "привет", "start": 0.0, "end": 0.4}]
    mock_gigaam.assert_called_once_with("clip.wav", model_name="v3_rnnt")


def test_transcribe_words_routes_to_whisper_cpp(monkeypatch: pytest.MonkeyPatch) -> None:
    """transcribe_words для whisper.cpp вызывает _whisper_cpp с размером модели."""
    mock_wcpp = MagicMock(return_value=[{"w": "cpp", "start": 0.0, "end": 0.3}])
    monkeypatch.setattr(asr_backends, "_whisper_cpp", mock_wcpp)

    res = asr_backends.transcribe_words("clip.wav", engine="whisper.cpp:small", use_terms=False)
    assert res == [{"w": "cpp", "start": 0.0, "end": 0.3}]
    mock_wcpp.assert_called_once_with("clip.wav", size="small")


def test_transcribe_words_routes_to_registered_custom_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Движок из каталога со сторонним kind вызывает зарегистрированный колбэк из ASR_BACKENDS."""
    custom_entry = {"id": "special_asr", "label": "Special", "lang": "ru", "kind": "special",
                    "prob": False, "subs": True, "selfcheck": False, "cut": False}
    monkeypatch.setattr(asr_backends, "engines", lambda: [custom_entry])

    mock_special = MagicMock(return_value=[{"w": "custom", "start": 0.0, "end": 1.0}])
    asr_backends.register("special_asr", mock_special)

    res = asr_backends.transcribe_words("wav.wav", engine="special_asr", use_terms=False)
    assert res == [{"w": "custom", "start": 0.0, "end": 1.0}]
    mock_special.assert_called_once_with("wav.wav")


def test_transcribe_words_unknown_engine_fallback_to_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Неизвестный движок по контракту docstring откатывается к whisper (поведение по умолчанию)."""
    mock_whisper = MagicMock(return_value=[{"w": "fallback", "start": 0.0, "end": 0.5}])
    monkeypatch.setattr(asr_backends, "_whisper", mock_whisper)

    res = asr_backends.transcribe_words("clip.wav", engine="completely_unknown_engine", use_terms=False)
    assert res == [{"w": "fallback", "start": 0.0, "end": 0.5}]
    mock_whisper.assert_called_once_with("clip.wav")



def test_transcribe_words_applies_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    """При use_terms=True вызывается terms.fix_words."""
    raw_words = [{"w": "риелси", "start": 0.0, "end": 0.5}]
    fixed_words = [{"w": "Reelsi", "start": 0.0, "end": 0.5}]

    monkeypatch.setattr(asr_backends, "_whisper", lambda wav, **kw: raw_words)
    mock_fix = MagicMock(return_value=fixed_words)
    monkeypatch.setattr("core.terms.fix_words", mock_fix)

    res = asr_backends.transcribe_words("clip.wav", engine="whisper", use_terms=True)
    assert res == fixed_words
    mock_fix.assert_called_once()


def test_transcribe_words_terms_error_does_not_fail_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой словаря терминов (не ReelsiError) не роняет транскрипцию, а логирует предупреждение."""
    raw_words = [{"w": "слово", "start": 0.0, "end": 0.5}]
    monkeypatch.setattr(asr_backends, "_whisper", lambda wav, **kw: raw_words)
    monkeypatch.setattr("core.terms.fix_words", MagicMock(side_effect=RuntimeError("словарь повреждён")))

    emitted_messages: list[str] = []

    def mock_emit(msg: str, **kw: Any) -> None:
        emitted_messages.append(msg.format(**kw))

    res = asr_backends.transcribe_words("clip.wav", engine="whisper", emit=mock_emit, use_terms=True)
    assert res == raw_words
    assert any("словарь терминов пропущен" in m for m in emitted_messages)


def test_transcribe_words_terms_reelsi_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """ReelsiError из terms.fix_words пробрасывается дальше без глушения."""
    monkeypatch.setattr(asr_backends, "_whisper", lambda wav, **kw: [{"w": "a", "start": 0.0, "end": 1.0}])
    monkeypatch.setattr("core.terms.fix_words", MagicMock(side_effect=ReelsiError("Фатальная ошибка терминов")))

    with pytest.raises(ReelsiError) as exc_info:
        asr_backends.transcribe_words("clip.wav", engine="whisper", use_terms=True)
    assert "Фатальная ошибка терминов" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# Адаптер _whisper
# --------------------------------------------------------------------------- #
def test_whisper_calls_unload_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """_whisper освобождает VRAM перед запуском и гарантированно выгружает модель после."""
    mock_unload = MagicMock()
    mock_warn = MagicMock()
    mock_transcribe = MagicMock(return_value=[{"w": "w", "start": 0.0, "end": 1.0}])
    mock_release = MagicMock()

    monkeypatch.setattr("core.aicut.unload_ours", mock_unload)
    monkeypatch.setattr("core.aicut.warn_foreign_models", mock_warn)
    monkeypatch.setattr("core.transcribe.transcribe", mock_transcribe)
    monkeypatch.setattr("core.transcribe.release_model", mock_release)

    res = asr_backends._whisper("test.wav", model_size="small")
    assert res == [{"w": "w", "start": 0.0, "end": 1.0}]
    mock_unload.assert_called_once()
    mock_warn.assert_called_once()
    mock_transcribe.assert_called_once_with("test.wav", model_size="small")
    mock_release.assert_called_once()


def test_whisper_releases_model_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """_whisper выгружает модель даже при исключении во время транскрипции."""
    mock_transcribe = MagicMock(side_effect=RuntimeError("CUDA OOM in Whisper"))
    mock_release = MagicMock()

    monkeypatch.setattr("core.aicut.unload_ours", MagicMock())
    monkeypatch.setattr("core.aicut.warn_foreign_models", MagicMock())
    monkeypatch.setattr("core.transcribe.transcribe", mock_transcribe)
    monkeypatch.setattr("core.transcribe.release_model", mock_release)

    with pytest.raises(RuntimeError) as exc:
        asr_backends._whisper("test.wav")
    assert "CUDA OOM in Whisper" in str(exc.value)
    mock_release.assert_called_once()


def test_whisper_release_model_reelsi_error_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """_whisper пробрасывает ReelsiError из release_model."""
    monkeypatch.setattr("core.aicut.unload_ours", MagicMock())
    monkeypatch.setattr("core.aicut.warn_foreign_models", MagicMock())
    monkeypatch.setattr("core.transcribe.transcribe", MagicMock(return_value=[]))
    monkeypatch.setattr("core.transcribe.release_model", MagicMock(side_effect=ReelsiError("Не удалось закрыть процесс")))

    with pytest.raises(ReelsiError):
        asr_backends._whisper("test.wav")


# --------------------------------------------------------------------------- #
# Адаптер _ctc
# --------------------------------------------------------------------------- #
def test_ctc_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ctc запускает подпроцесс ctc_asr, читает JSON результат и удаляет временный файл."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    created_files: list[str] = []

    def fake_subprocess_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        # cmd: [... "--out", <out_path>]
        out_idx = cmd.index("--out") + 1
        out_path = cmd[out_idx]
        created_files.append(out_path)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([{"w": "hola", "start": 0.0, "end": 0.5, "prob": 0.95}], f)
        return subprocess.CompletedProcess(cmd, 0, stdout=out_path + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    words = asr_backends._ctc("audio.wav", "org/mms-spanish", device="cuda")
    assert words == [{"w": "hola", "start": 0.0, "end": 0.5, "prob": 0.95}]
    # Проверка, что временный файл удален
    for f in created_files:
        assert not os.path.exists(f)


def test_ctc_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ctc выбрасывает понятный RuntimeError при таймауте."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    def fake_run(*args: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ctc_asr", timeout=1800)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        asr_backends._ctc("audio.wav", "org/model")
    assert "превышен таймаут" in str(exc.value)


def test_ctc_error_reporting(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ctc поднимает текст ошибки из stderr при ненулевом коде возврата."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    def fake_run_ctc_err(*args: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["ctc_asr"], 1, stdout="", stderr="CTC_ASR_ERROR: Torch not found\n")

    monkeypatch.setattr(subprocess, "run", fake_run_ctc_err)

    with pytest.raises(RuntimeError) as exc:
        asr_backends._ctc("audio.wav", "org/model")
    assert "CTC_ASR_ERROR: Torch not found" in str(exc.value)


def test_ctc_empty_or_corrupt_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ctc падает с RuntimeError, если результат подпроцесса не файл или не список."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    # Пустой stdout (нет пути к файлу)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(["ctc"], 0, stdout="", stderr=""))
    with pytest.raises(RuntimeError) as exc1:
        asr_backends._ctc("audio.wav", "org/model")
    assert "пустой результат" in str(exc1.value)

    # JSON не является списком
    def fake_run_dict(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        out_path = cmd[cmd.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"error": "not a list"}, f)
        return subprocess.CompletedProcess(cmd, 0, stdout=out_path, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_dict)
    with pytest.raises(RuntimeError) as exc2:
        asr_backends._ctc("audio.wav", "org/model")
    assert "неожиданный формат результата" in str(exc2.value)


# --------------------------------------------------------------------------- #
# Адаптер _gigaam
# --------------------------------------------------------------------------- #
def test_gigaam_ctc_and_rnnt_widen(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """_gigaam вызывает _widen только для голов rnnt, а для ctc возвращает как есть."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    raw_words = [{"w": "в", "start": 0.0, "end": 0.04}]  # короче 0.14

    out_file = tmp_path / "gigaam_res.json"
    out_file.write_text(json.dumps(raw_words), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(["gigaam_subs"], 0, stdout=str(out_file), stderr=""))

    # Для ctc: слово остаётся 0.04
    ctc_res = asr_backends._gigaam("audio.wav", model_name="v3_ctc")
    assert ctc_res[0]["end"] == 0.04

    # Для rnnt: слово расширяется до 0.14
    rnnt_res = asr_backends._gigaam("audio.wav", model_name="v3_rnnt")
    assert rnnt_res[0]["end"] == 0.14


def test_gigaam_subprocess_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_gigaam поднимает ошибку таймаута."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    def fake_timeout(*a: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="gigaam_subs", timeout=1800)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    with pytest.raises(RuntimeError) as exc:
        asr_backends._gigaam("audio.wav", model_name="v3_ctc")
    assert "превышен таймаут" in str(exc.value)


def test_gigaam_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_gigaam поднимает ошибку из stderr при returncode != 0."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(["gigaam_subs"], 1,
                                                                   stdout="",
                                                                   stderr="GIGAAM_SUBS_ERROR: Memory error\nTraceback..."))
    with pytest.raises(RuntimeError) as exc:
        asr_backends._gigaam("audio.wav", model_name="v3_ctc")
    assert "GIGAAM_SUBS_ERROR: Memory error" in str(exc.value)


def test_gigaam_invalid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """_gigaam падает с ошибкой разбора, если результат некорректный JSON."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not json", encoding="utf-8")

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(["gigaam_subs"], 0, stdout=str(bad_file), stderr=""))
    with pytest.raises(RuntimeError) as exc:
        asr_backends._gigaam("audio.wav", model_name="v3_ctc")
    assert "не удалось разобрать результат" in str(exc.value)


# --------------------------------------------------------------------------- #
# Адаптер _whisper_cpp
# --------------------------------------------------------------------------- #
def test_whisper_cpp_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """_whisper_cpp вызывает whisper_cpp.transcribe с указанным размером."""
    import core
    mock_wcpp = types.ModuleType("whisper_cpp")
    mock_wcpp.transcribe = MagicMock(return_value=[{"w": "native", "start": 0.0, "end": 1.0}])  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.whisper_cpp", mock_wcpp)
    monkeypatch.setattr(core, "whisper_cpp", mock_wcpp, raising=False)

    res = asr_backends._whisper_cpp("audio.wav", size="medium")
    assert res == [{"w": "native", "start": 0.0, "end": 1.0}]
    mock_wcpp.transcribe.assert_called_once_with("audio.wav", size="medium")


# --------------------------------------------------------------------------- #
# Адаптер _omni
# --------------------------------------------------------------------------- #
def test_omni_success_and_refine(monkeypatch: pytest.MonkeyPatch) -> None:
    """_omni вызывает omni_asr, режет фразы на слова, а при refine=True пытается выровнять."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.omni_local_engine = MagicMock(return_value="qwen")  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    phrases = [{"text": "привет всем", "start": 0.0, "end": 2.0}]

    def fake_omni_run(cmd: list[str], **kw: Any) -> None:
        out_path = cmd[cmd.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(phrases, f)

    monkeypatch.setattr(subprocess, "run", fake_omni_run)

    # 1. refine = False: интерполяция
    words = asr_backends._omni("audio.wav", refine=False)
    assert len(words) == 2
    assert words[0] == {"w": "привет", "start": 0.0, "end": 1.0}
    assert words[1] == {"w": "всем", "start": 1.0, "end": 2.0}

    # 2. refine = True: вызывает gigaam_cut.align_full
    refined_words = [{"w": "привет", "start": 0.0, "end": 0.8}, {"w": "всем", "start": 0.9, "end": 1.9}]
    mock_align = MagicMock(return_value=refined_words)
    monkeypatch.setattr("core.gigaam_cut.align_full", mock_align)

    words_refined = asr_backends._omni("audio.wav", refine=True)
    assert words_refined == refined_words
    mock_align.assert_called_once_with("audio.wav", "привет всем")


def test_omni_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """_omni обрабатывает CalledProcessError, TimeoutExpired и не записанный файл."""
    mock_aicut = types.ModuleType("aicut")
    mock_aicut.unload_ours = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.warn_foreign_models = MagicMock()  # type: ignore[attr-defined]
    mock_aicut.omni_local_engine = MagicMock(return_value="qwen")  # type: ignore[attr-defined]
    monkeypatch.setitem(os.sys.modules, "core.aicut", mock_aicut)

    # 1. CalledProcessError
    def fake_err(*a: Any, **kw: Any) -> Any:
        raise subprocess.CalledProcessError(1, "omni_asr", stderr=b"Omni model failed to load")

    monkeypatch.setattr(subprocess, "run", fake_err)
    with pytest.raises(RuntimeError) as exc1:
        asr_backends._omni("audio.wav")
    assert "Omni-субтитры: Omni model failed to load" in str(exc1.value)

    # 2. TimeoutExpired
    def fake_timeout(*a: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired("omni_asr", 3600)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    with pytest.raises(RuntimeError) as exc2:
        asr_backends._omni("audio.wav")
    assert "процесс завис" in str(exc2.value)

    # 3. Файл не записан (returncode 0, но out файл отсутствует)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    with pytest.raises(RuntimeError) as exc3:
        asr_backends._omni("audio.wav")
    assert "движок не записал результат" in str(exc3.value)
