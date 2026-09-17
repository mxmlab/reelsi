# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты санитарного гарда нарезки и сброса профиля спикера (задание LA).

ПОЧЕМУ эти тесты существуют:
1. Если модель забраковала весь текст или оставила меньше 25% речи,
   нарезка обязана завершиться ошибкой ДО чистки кодом (postprocess),
   не позволяя veto_unique_drops восстановить вырезанное при dedupe=True
   и выдать сбой модели за успех. Готовые out.xml и сайдкары не должны
   перезаписываться.
2. Умолчание dedupe должно быть строго False (в cutstages, tune, CUT_DEFAULTS, to_reelsi_opts).
3. apply_speaker(None) обязан сбрасывать все пороги gigaam_cut.tune к исходным значениям.
"""
from pathlib import Path
import pytest
from core import cutstages, speakers
from core.gigaam_cut import pipeline, tune

_WORDS_VOCAB = [
    "первый", "второй", "третий", "четвертый", "пятый", "шестой", "седьмой", "восьмой",
    "девятый", "десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый",
    "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый", "девятнадцатый",
    "двадцатый", "двадцатьодин", "двадцатьдва", "двадцатьтри", "двадцатьчетыре",
    "двадцатьпять", "двадцатьшесть", "двадцатьсемь", "двадцатьвосемь", "двадцатьдевять",
    "тридцатый",
]


@pytest.fixture(autouse=True)
def _restore_tune_globals():
    """Сохраняет и восстанавливает глобалы tune после каждого теста."""
    saved = {g: getattr(tune, g) for g in tune._CUT_GLOBALS.values()}
    saved["SPEAKER"] = tune.SPEAKER
    saved["_DB_TUNED"] = tune._DB_TUNED
    yield
    for k, v in saved.items():
        setattr(tune, k, v)


@pytest.fixture
def _mock_pipeline_env(monkeypatch):
    """Подменяет внешние вызовы (VRAM, аудио-фильтры, сборку XML)."""
    monkeypatch.setattr(pipeline.aicut, "unload_ours", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.aicut, "warn_foreign_models", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))
    monkeypatch.setattr(pipeline.draftrender, "clean_tmp", lambda *a, **k: None)

    def fake_build(cams, keep, offsets, out_path, **kw):
        Path(out_path).write_bytes(b"<xml>new_cut</xml>")
        return {"total_s": sum(e - s for s, e in keep), "segments": len(keep)}

    monkeypatch.setattr(pipeline.xmlbuild, "build", fake_build)


def _make_dummy_words(count=20, dur_per_word=1.0):
    """Генерирует список уникальных слов заданной длительности."""
    words = []
    for i in range(count):
        w = _WORDS_VOCAB[i % len(_WORDS_VOCAB)]
        words.append({
            "w": w,
            "start": float(i * dur_per_word),
            "end": float((i + 1) * dur_per_word),
            "prob": 0.99,
            "space_before": i > 0,
        })
    text = " ".join(w["w"] for w in words)
    return text, words


def test_model_drops_all_words_dedupe_true_raises_system_exit(monkeypatch, tmp_path, _mock_pipeline_env):
    """Модель забраковала все слова при dedupe=True -> отказ ДО чистки кодом, out.xml не тронут."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # Модель бракует все слова: kept пустой, drop содержит все индексы
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(), set(range(len(words))), "", []))

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    with pytest.raises(SystemExit) as exc_info:
        pipeline.run(
            "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
            stages={"draft": False, "sense": True, "dedupe": True},
            emit=lambda *a, **k: None,
        )

    assert "ИИ вырезал почти весь ролик" in str(exc_info.value)
    # Существующий XML должен остаться побайтно неизменным
    assert out_xml.read_bytes() == b"<xml>original_cut</xml>"
    # Сайдкары не должны создаваться при отказе
    assert not (tmp_path / "out.project.json").exists()
    assert not (tmp_path / "out.cuts.json").exists()


def test_model_drops_all_words_dedupe_false_raises_system_exit(monkeypatch, tmp_path, _mock_pipeline_env):
    """Модель забраковала все слова при dedupe=False -> отказ, out.xml не тронут."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(), set(range(len(words))), "", []))

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    with pytest.raises(SystemExit) as exc_info:
        pipeline.run(
            "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
            stages={"draft": False, "sense": True, "dedupe": False},
            emit=lambda *a, **k: None,
        )

    assert "ИИ вырезал почти весь ролик" in str(exc_info.value)
    assert out_xml.read_bytes() == b"<xml>original_cut</xml>"
    assert not (tmp_path / "out.project.json").exists()
    assert not (tmp_path / "out.cuts.json").exists()


def test_model_leaves_10_percent_dedupe_true_raises_system_exit(monkeypatch, tmp_path, _mock_pipeline_env):
    """Модель оставила 10% речи при dedupe=True -> гард срабатывает ДО veto_unique_drops."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # 2 слова из 20 = 10% от 20 секунд (порог 25%)
    kept = {0, 1}
    drop = set(range(2, 20))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (kept, drop, "", []))

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    with pytest.raises(SystemExit) as exc_info:
        pipeline.run(
            "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
            stages={"draft": False, "sense": True, "dedupe": True},
            emit=lambda *a, **k: None,
        )

    assert "ИИ вырезал почти весь ролик" in str(exc_info.value)
    assert out_xml.read_bytes() == b"<xml>original_cut</xml>"
    assert not (tmp_path / "out.project.json").exists()
    assert not (tmp_path / "out.cuts.json").exists()


def test_model_leaves_80_percent_passes(monkeypatch, tmp_path, _mock_pipeline_env):
    """Модель оставила 80% речи -> нарезка проходит успешно, XML и сайдкары записаны."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # 16 слов из 20 = 80% (порог 25% пройден)
    kept = set(range(16))
    drop = set(range(16, 20))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (kept, drop, "", []))

    out_xml = tmp_path / "out.xml"
    keep, cutlog, draft, info = pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
        stages={"draft": False, "sense": True, "dedupe": True},
        emit=lambda *a, **k: None,
    )

    assert len(keep) > 0
    assert out_xml.read_bytes() == b"<xml>new_cut</xml>"
    assert (tmp_path / "out.project.json").exists()
    assert (tmp_path / "out.cuts.json").exists()


def test_dedupe_defaults_everywhere():
    """Единое умолчание dedupe=False во всех точках входа и конфигурациях."""
    norm_stages, _ = cutstages.normalize({})
    assert norm_stages["dedupe"] is False
    assert tune.DEDUPE is False
    assert speakers.CUT_DEFAULTS["dedupe"] is False
    opts = cutstages.to_reelsi_opts(stages=None)
    assert opts["dedup"] is False


def test_apply_speaker_resets_defaults_on_none(monkeypatch):
    """apply_speaker(None) восстанавливает умолчания модульных порогов tune."""
    orig_silence = tune.SILENCE_SEC
    orig_dedupe = tune.DEDUPE
    custom_prof = {
        "label": "Тестовый спикер",
        "cut": {"silence_sec": 1.75, "dedupe": True},
    }
    monkeypatch.setattr("core.speakers.load", lambda name: custom_prof if name == "test_sp" else None)

    # Применяем кастомный профиль
    tune.apply_speaker("test_sp")
    assert tune.SILENCE_SEC == 1.75
    assert tune.DEDUPE is True
    assert tune.SPEAKER == custom_prof

    # Сбрасываем вызовом apply_speaker(None)
    res = tune.apply_speaker(None)
    assert res is None
    assert tune.SPEAKER is None
    assert tune.SILENCE_SEC == orig_silence
    assert tune.DEDUPE == orig_dedupe
