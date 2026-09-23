# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты гарда покрытия текста моделью и гарда доли речи в omni_cut.

ПОЧЕМУ эти тесты существуют:
1. Если модель вернула пустой ответ, отказ или текст другого ролика,
   align_markup даёт cover < MIN_COVER (0.5). Раньше пустой drop маскировал
   сбой под «резать нечего» и перезаписывал out.xml 100% исходником.
   Теперь decide_markup обязан падать с ReelsiError, защищая готовые файлы.
2. В omni_cut при allow_long_drop=False вето возвращало длинные интервалы,
   сводя llm_drop к 0 и маскируя сбой модели до 100% успеха. Санитарный гард
   доли речи обязан срабатывать ДО возврата длинных интервалов.
"""
import json
from pathlib import Path
import pytest
from core import aicut, omni_cut
from core.gigaam_cut import decide, pipeline, tune
from core.umsg import ReelsiError

_WORDS_VOCAB = [
    "первый", "второй", "третий", "четвертый", "пятый", "шестой", "седьмой", "восьмой",
    "девятый", "десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый",
    "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый", "девятнадцатый",
    "двадцатый",
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


@pytest.mark.parametrize("bad_response", [
    {},
    {"text": ""},
    {"text": "Извините, не могу помочь"},
    {"text": "совершенно другой текст про космические корабли и дальние планеты галактики"},
])
def test_unrelated_or_empty_model_response_raises_reelsierror(
    monkeypatch, tmp_path, _mock_pipeline_env, bad_response
):
    """Ответ модели не про этот ролик -> ReelsiError, исходный XML цел, сайдкаров нет."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: bad_response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    with pytest.raises(ReelsiError) as exc_info:
        pipeline.run(
            "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
            stages={"draft": False, "sense": True, "dedupe": False},
            emit=lambda *a, **k: None,
        )

    msg = str(exc_info.value)
    assert "ответ модели не про этот ролик" in msg
    assert "ничего не перезаписываю" in msg
    assert "проверь модель и промпт" in msg.lower()
    # Файлы нарезки не должны быть затронуты
    assert out_xml.read_bytes() == b"<xml>original_cut</xml>"
    assert not (tmp_path / "out.project.json").exists()
    assert not (tmp_path / "out.cuts.json").exists()


def test_clean_full_text_without_brackets_succeeds_empty_drop(
    monkeypatch, tmp_path, _mock_pipeline_env
):
    """Весь наш текст без скобок -> нарезка успешна, drop пуст, XML перезаписан."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    response = {"text": text, "notes": "резать нечего", "duplicate_groups": []}
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    keep, cutlog, draft, info = pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
        stages={"draft": False, "sense": True, "dedupe": False},
        emit=lambda *a, **k: None,
    )

    assert len(keep) > 0
    assert out_xml.read_bytes() == b"<xml>new_cut</xml>"
    assert (tmp_path / "out.project.json").exists()
    cuts_file = tmp_path / "out.cuts.json"
    assert cuts_file.exists()
    cuts_data = json.loads(cuts_file.read_text(encoding="utf-8"))
    assert len(cuts_data) == 0


def test_text_with_bracketed_fragment_cuts_fragment(
    monkeypatch, tmp_path, _mock_pipeline_env
):
    """Наш текст с фрагментом в скобках -> нарезка успешна, фрагмент вырезан."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # Заключаем слова 2 и 3 ("третий четвертый") в скобки
    marked_words = list(_WORDS_VOCAB[:20])
    marked_words[2] = "[" + marked_words[2]
    marked_words[3] = marked_words[3] + "]"
    marked_text = " ".join(marked_words)

    response = {"text": marked_text, "notes": "вырезан дубль", "duplicate_groups": []}
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    keep, cutlog, draft, info = pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
        stages={"draft": False, "sense": True, "dedupe": False},
        emit=lambda *a, **k: None,
    )

    assert out_xml.read_bytes() == b"<xml>new_cut</xml>"
    cuts_file = tmp_path / "out.cuts.json"
    assert cuts_file.exists()
    cuts_data = json.loads(cuts_file.read_text(encoding="utf-8"))
    assert len(cuts_data) == 1
    assert "третий четвертый" in cuts_data[0]["text"]


def test_omni_cut_model_drops_all_long_intervals_raises_reelsierror(monkeypatch):
    """omni_cut: модель выкидывает все длинные различные интервалы -> ReelsiError ДО вето."""
    texts = [
        {"start": float(i * 3), "end": float((i + 1) * 3), "text": f"содержательный кусок {i}"}
        for i in range(8)
    ]
    # Все 8 интервалов длинные (>= 2.5с), не дублируют соседей.
    # Модель просит выкинуть все 8.
    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: {"drop": list(range(8)), "notes": "брак"})

    with pytest.raises(ReelsiError) as exc_info:
        omni_cut.decide(texts, emit=lambda *a, **k: None, allow_long_drop=False)

    msg = str(exc_info.value)
    assert "ИИ вырезал почти весь ролик" in msg
    assert "Ничего не перезаписываю" in msg


def test_omni_cut_guard_keep_helper():
    """Проверка вспомогательной функции _guard_keep в omni_cut."""
    intervals = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0), (9.0, 12.0)]
    # Меньше 25% (3с из 12с = 25%, 2с < 25%) -> ReelsiError
    with pytest.raises(ReelsiError):
        omni_cut._guard_keep([(0.0, 2.0)], intervals)
    # Пустой keep -> ReelsiError
    with pytest.raises(ReelsiError):
        omni_cut._guard_keep([], intervals)
    # >= 25% речи -> проходит успешно
    omni_cut._guard_keep([(0.0, 6.0)], intervals)


def test_min_cover_constant_value():
    """Константа MIN_COVER равна 0.5."""
    assert decide.MIN_COVER == 0.5


def test_min_cover_no_cut_constant_value():
    """Константа MIN_COVER_NO_CUT равна 0.9."""
    assert decide.MIN_COVER_NO_CUT == 0.9


def test_half_transcript_without_brackets_raises_reelsierror(
    monkeypatch, tmp_path, _mock_pipeline_env
):
    """Половина транскрипта без скобок (cover=0.5, drop пуст) -> ReelsiError (порог 0.9)."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # Ровно половина слов ролика без скобок
    half_text = " ".join(w["w"] for w in words[:10])
    response = {"text": half_text, "notes": "обрыв ответа", "duplicate_groups": []}
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    with pytest.raises(ReelsiError) as exc_info:
        pipeline.run(
            "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
            stages={"draft": False, "sense": True, "dedupe": False},
            emit=lambda *a, **k: None,
        )

    msg = str(exc_info.value)
    assert "ответ модели не про этот ролик" in msg
    assert "ничего не перезаписываю" in msg
    assert out_xml.read_bytes() == b"<xml>original_cut</xml>"


def test_ninety_two_percent_transcript_without_brackets_succeeds(
    monkeypatch, tmp_path, _mock_pipeline_env
):
    """92% транскрипта без скобок (cover=0.92 >= 0.9, drop пуст) -> успешно проходит."""
    text, words = _make_dummy_words(count=25, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # 23 слова из 25 = 92%
    ninety_two_text = " ".join(w["w"] for w in words[:23])
    response = {"text": ninety_two_text, "notes": "почти весь текст", "duplicate_groups": []}
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    keep, cutlog, draft, info = pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
        stages={"draft": False, "sense": True, "dedupe": False},
        emit=lambda *a, **k: None,
    )

    assert len(keep) > 0
    assert out_xml.read_bytes() == b"<xml>new_cut</xml>"
    cuts_file = tmp_path / "out.cuts.json"
    assert cuts_file.exists()
    cuts_data = json.loads(cuts_file.read_text(encoding="utf-8"))
    assert len(cuts_data) == 0


def test_half_transcript_with_brackets_succeeds(
    monkeypatch, tmp_path, _mock_pipeline_env
):
    """Половина транскрипта со скобками (cover=0.5, drop не пуст) -> успешно проходит (порог 0.5)."""
    text, words = _make_dummy_words(count=20, dur_per_word=1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # 10 слов из 20 (50%), одно слово в скобках -> drop не пуст
    half_words = [w["w"] for w in words[:10]]
    half_words[2] = "[" + half_words[2] + "]"
    half_text = " ".join(half_words)
    response = {"text": half_text, "notes": "вырезано слово", "duplicate_groups": []}
    monkeypatch.setattr(pipeline.aicut, "_ask_json", lambda *a, **k: response)

    out_xml = tmp_path / "out.xml"
    out_xml.write_bytes(b"<xml>original_cut</xml>")

    keep, cutlog, draft, info = pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], str(out_xml), 50.4,
        stages={"draft": False, "sense": True, "dedupe": False},
        emit=lambda *a, **k: None,
    )

    assert len(keep) > 0
    assert out_xml.read_bytes() == b"<xml>new_cut</xml>"
    cuts_file = tmp_path / "out.cuts.json"
    assert cuts_file.exists()
    cuts_data = json.loads(cuts_file.read_text(encoding="utf-8"))
    assert len(cuts_data) == 1

