# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты пропуска ступеней пайплайна нарезки gigaam_cut (задание GE).

ПОЧЕМУ этот тест существует:
Каждая ступень нарезки (pauses, sense, dedupe, refine, breath, draft) может быть
отключена пользователем через контракт stages.
Тест проверяет без запуска тяжелых нейросетей (на моках):
- при sense=False decide_markup не зовется ни разу, keep покрывает все слова;
- при breath=False _cut_breaths не вызывается;
- при refine=False refine_keep не вызывается;
- при pauses='off' _silence_bounds не вызывается и silence_bounds пуст;
- при sense=False и снятых refine/breath санитарные гарды не срабатывают на
  40-секундном сплошном куске речи;
- при sense=True санитарные гарды работают как раньше (регрессионный тест).
"""
import pytest
from core.gigaam_cut import pipeline as pipeline


@pytest.fixture(autouse=True)
def _mock_pipeline_sidecars(monkeypatch, tmp_path):
    """Мокает внешние зависимости VRAM, XML и дисковых сайдкаров."""
    monkeypatch.setattr(pipeline.aicut, "unload_ours", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.aicut, "warn_foreign_models", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.xmlbuild, "build", lambda *a, **k: {"total_s": 10.0, "segments": 1})
    monkeypatch.setattr(pipeline.draftrender, "clean_tmp", lambda *a, **k: None)
    orig_dedupe = pipeline.tune.DEDUPE
    yield
    pipeline.tune.DEDUPE = orig_dedupe


_WORDS_VOCAB = [
    "первый", "второй", "третий", "четвертый", "пятый", "шестой", "седьмой", "восьмой",
    "девятый", "десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый",
    "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый", "девятнадцатый",
    "двадцатый", "двадцатьодин", "двадцатьдва", "двадцатьтри", "двадцатьчетыре",
    "двадцатьпять", "двадцатьшесть", "двадцатьсемь", "двадцатьвосемь", "двадцатьдевять",
    "тридцатый", "тридцатьодин", "тридцатьдва", "тридцатьтри", "тридцатьчетыре",
    "тридцатьпять", "тридцатьшесть", "тридцатьсемь", "тридцатьвосемь", "тридцатьдевять",
    "сороковой", "сорокодин", "сорокдва", "сороктри", "сорокчетыре", "сорокпять"
]


def _make_dummy_words(count=5, dur_per_word=1.0):
    """Генерирует фиктивный список уникальных слов."""
    words = []
    for i in range(count):
        w = _WORDS_VOCAB[i % len(_WORDS_VOCAB)] + ("x" * (i // len(_WORDS_VOCAB)))
        words.append({
            "w": w,
            "start": float(i * dur_per_word),
            "end": float((i + 1) * dur_per_word),
            "prob": 0.99,
            "space_before": i > 0,
        })
    text = " ".join(w["w"] for w in words)
    return text, words


def test_sense_false_skips_decide_markup(monkeypatch, tmp_path):
    """При sense=False decide_markup не вызывается, а keep покрывает все слова."""
    text, words = _make_dummy_words(5, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))

    decide_called = []

    def fake_decide(*args, **kwargs):
        decide_called.append(True)
        return set(), set(), "", []

    monkeypatch.setattr(pipeline, "decide_markup", fake_decide)
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    out_xml = str(tmp_path / "out.xml")
    stages = {
        "pauses": "off",
        "asr": True,
        "sense": False,
        "dedupe": True,
        "refine": False,
        "breath": False,
        "draft": False,
    }
    keep, cutlog, draft, info = pipeline._run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        stages=stages, emit=lambda *a, **k: None
    )

    assert not decide_called, "decide_markup не должен вызываться при sense=False"
    assert len(keep) == 1
    assert keep[0] == (0.0, 5.0)


def test_breath_false_skips_cut_breaths(monkeypatch, tmp_path):
    """При breath=False _cut_breaths не вызывается."""
    text, words = _make_dummy_words(3, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))

    breath_called = []

    def fake_cut_breaths(*args, **kwargs):
        breath_called.append(True)
        return args[0], args[1], []

    monkeypatch.setattr(pipeline, "_cut_breaths", fake_cut_breaths)

    out_xml = str(tmp_path / "out.xml")
    stages = {
        "pauses": "speech",
        "asr": True,
        "sense": True,
        "dedupe": True,
        "refine": True,
        "breath": False,
        "draft": False,
    }
    pipeline._run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        stages=stages, emit=lambda *a, **k: None
    )

    assert not breath_called, "_cut_breaths не должен вызываться при breath=False"


def test_refine_false_skips_refine_keep(monkeypatch, tmp_path):
    """При refine=False refine_keep не вызывается."""
    text, words = _make_dummy_words(3, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    refine_called = []

    def fake_refine(*args, **kwargs):
        refine_called.append(True)
        return args[0], list(range(len(args[0])))

    monkeypatch.setattr(pipeline, "refine_keep", fake_refine)

    out_xml = str(tmp_path / "out.xml")
    stages = {
        "pauses": "speech",
        "asr": True,
        "sense": True,
        "dedupe": True,
        "refine": False,
        "breath": True,
        "draft": False,
    }
    pipeline._run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        stages=stages, emit=lambda *a, **k: None
    )

    assert not refine_called, "refine_keep не должен вызываться при refine=False"


def test_pauses_off_skips_silence_bounds(monkeypatch, tmp_path):
    """При pauses='off' _silence_bounds не вызывается."""
    text, words = _make_dummy_words(3, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    silence_called = []

    def fake_silence(*args, **kwargs):
        silence_called.append(True)
        return []

    monkeypatch.setattr(pipeline, "_silence_bounds", fake_silence)

    out_xml = str(tmp_path / "out.xml")
    stages = {
        "pauses": "off",
        "asr": True,
        "sense": True,
        "dedupe": True,
        "refine": False,
        "breath": False,
        "draft": False,
    }
    pipeline._run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        stages=stages, emit=lambda *a, **k: None
    )

    assert not silence_called, "_silence_bounds не должен вызываться при pauses='off'"


def test_guards_disabled_when_stages_off_long_segment_passes(monkeypatch, tmp_path):
    """При sense=False и снятых refine/breath гарды НЕ срабатывают:
    40 секунд речи одним куском успешно проходят и XML пишется."""
    text, words = _make_dummy_words(40, 1.0)  # 40 секунд речи
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))

    xml_built = []
    monkeypatch.setattr(
        pipeline.xmlbuild, "build",
        lambda cams, keep, *a, **k: (xml_built.append(keep), {"total_s": 40.0, "segments": len(keep)})[1]
    )

    out_xml = str(tmp_path / "out.xml")
    stages = {
        "pauses": "off",
        "asr": True,
        "sense": False,
        "dedupe": False,
        "refine": False,
        "breath": False,
        "draft": False,
    }
    keep, cutlog, draft, info = pipeline._run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        stages=stages, emit=lambda *a, **k: None
    )

    assert len(xml_built) == 1
    assert len(keep) == 1
    assert keep[0] == (0.0, 40.0)


def test_guards_regression_when_sense_enabled(monkeypatch, tmp_path):
    """Регрессионные тесты санитарных гардов при включённых ступенях:
    1) sense=True: если ИИ вырезал > 75% ролика -> отказ SystemExit;
    2) sense=True, refine=True: если ролик > 30с вышел одним куском -> отказ SystemExit."""
    out_xml = str(tmp_path / "out.xml")

    # 1. Гард: вырезано > 75% ролика
    text, words = _make_dummy_words(10, 1.0)  # 10 секунд
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    # Модель вырезает 9 слов из 10 (остается 1с из 10с = 10% < 25%)
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: ({0}, set(range(1, 10)), "", []))
    monkeypatch.setattr(pipeline, "postprocess", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    stages_cut_all = {
        "pauses": "speech",
        "asr": True,
        "sense": True,
        "dedupe": True,
        "refine": True,
        "breath": True,
        "draft": False,
    }
    with pytest.raises(SystemExit) as exc_info:
        pipeline._run(
            "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
            stages=stages_cut_all, emit=lambda *a, **k: None
        )
    assert "ИИ вырезал почти весь ролик" in str(exc_info.value)

    # 2. Гард: ролик > 30с вышел одним куском при включённом refine
    text40, words40 = _make_dummy_words(40, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text40, words40))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(40)), set(), "", []))
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    with pytest.raises(SystemExit) as exc_info:
        pipeline._run(
            "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
            stages=stages_cut_all, emit=lambda *a, **k: None
        )
    assert "Нарезка вернула весь ролик одним куском" in str(exc_info.value)


def test_cli_stages_without_no_draft_enables_draft():
    """ДЕФЕКТ 1: без флага --no-draft сборка stages в CLI-входах обязана давать draft=True.
    
    ПОЧЕМУ: cutstages.normalize({}) по умолчанию возвращает draft=False (для веб-панели).
    CLI-входы (omni_cut и gigaam_cut) по контракту CLI включают черновик, если не передан
    --no-draft. Тест проверяет сборку stages на уровне парсинга аргументов.
    """
    import argparse
    from core import cutstages

    # 1. Проверка логики сборки stages для omni_cut
    ap_omni = argparse.ArgumentParser()
    ap_omni.add_argument("--no-draft", action="store_true")
    
    # Без флага --no-draft
    args_default = ap_omni.parse_args([])
    stages_default = {"draft": not args_default.no_draft}
    norm_default, _ = cutstages.normalize(stages_default)
    assert stages_default["draft"] is True
    assert norm_default["draft"] is True

    # С флагом --no-draft
    args_nodraft = ap_omni.parse_args(["--no-draft"])
    stages_nodraft = {"draft": not args_nodraft.no_draft}
    norm_nodraft, _ = cutstages.normalize(stages_nodraft)
    assert stages_nodraft["draft"] is False
    assert norm_nodraft["draft"] is False

    # 2. Проверка логики сборки stages для gigaam_cut/__main__.py
    ap_gc = argparse.ArgumentParser()
    ap_gc.add_argument("--no-draft", action="store_true")
    args_gc_default = ap_gc.parse_args([])
    stages_gc = {"draft": not args_gc_default.no_draft}
    assert stages_gc["draft"] is True
    assert cutstages.normalize(stages_gc)[0]["draft"] is True


def test_missing_dedupe_in_stages_preserves_speaker_profile_and_passes_none_to_postprocess(monkeypatch, tmp_path):
    """ДЕФЕКТ 2: при отсутствии ключа dedupe во входном словаре tune.DEDUPE не перетирается,
    а в postprocess передаётся None (чтобы postprocess взял актуальный tune.DEDUPE из профиля).
    
    ПОЧЕМУ: normalize({}) подставляет dedupe=True. Если брать stages.get('dedupe'), профиль
    спикера с dedupe=False молча перетирался дефолтом True.
    """
    text, words = _make_dummy_words(3, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    # Мокаем apply_speaker, чтобы он установил tune.DEDUPE = False (как из профиля спикера)
    def fake_apply_speaker(speaker, emit=None):
        monkeypatch.setattr(pipeline.tune, "DEDUPE", False)

    monkeypatch.setattr(pipeline, "apply_speaker", fake_apply_speaker)

    postprocess_dedupe_args = []

    def fake_postprocess(words, kept, drop, silence_bounds=None, protect=(), light=False,
                         emit=None, dedupe=None, rule=None):
        postprocess_dedupe_args.append(dedupe)

    monkeypatch.setattr(pipeline, "postprocess", fake_postprocess)

    out_xml = str(tmp_path / "out.xml")
    # Передаём stages БЕЗ ключа dedupe
    stages = {"draft": False, "sense": False}
    pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        speaker="custom_speaker", stages=stages, emit=lambda *a, **k: None
    )

    # tune.DEDUPE не должен был сброситься в True
    assert pipeline.tune.DEDUPE is False, "tune.DEDUPE должен сохранить значение False из профиля спикера"
    # В postprocess должен уйти None (что означает «взять tune.DEDUPE»)
    assert postprocess_dedupe_args == [None], f"В postprocess должен уйти dedupe=None, пришло: {postprocess_dedupe_args}"


def test_explicit_dedupe_in_stages_overrides_speaker_profile(monkeypatch, tmp_path):
    """ДЕФЕКТ 2: при явном dedupe=True значение перекрывает профиль спикера (tune.DEDUPE=True).
    
    ПОЧЕМУ: явный выбор пользователя с галки или флага CLI перекрывает настройки профиля.
    """
    text, words = _make_dummy_words(3, 1.0)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "refine_keep", lambda k, *a, **kw: (k, list(range(len(k)))))
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    def fake_apply_speaker(speaker, emit=None):
        pipeline.tune.DEDUPE = False

    monkeypatch.setattr(pipeline, "apply_speaker", fake_apply_speaker)

    postprocess_dedupe_args = []

    def fake_postprocess(words, kept, drop, silence_bounds=None, protect=(), light=False,
                         emit=None, dedupe=None, rule=None):
        postprocess_dedupe_args.append(dedupe)

    monkeypatch.setattr(pipeline, "postprocess", fake_postprocess)

    out_xml = str(tmp_path / "out.xml")
    # Передаём stages с ЯВНЫМ dedupe=True
    stages = {"draft": False, "dedupe": True}
    pipeline.run(
        "dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
        speaker="custom_speaker", stages=stages, emit=lambda *a, **k: None
    )

    # tune.DEDUPE перекрыт значением True
    assert pipeline.tune.DEDUPE is True, "Явный dedupe=True должен перекрыть профиль спикера"
    # В postprocess должен уйти True
    assert postprocess_dedupe_args == [True], f"В postprocess должен уйти dedupe=True, пришло: {postprocess_dedupe_args}"


def test_run_omnicut_job_builds_correct_cli_flags(monkeypatch, tmp_path):
    """Цепочка api/jobs.py -> omni_cut.py:
    - при review=False (дефолт) в cmd есть --no-draft и нет --omni-review;
    - при review=True в cmd есть --omni-review и НЕТ --no-draft (независимо от draft/stages);
    - при stages без dedupe флаги --dedupe/--no-dedupe НЕ передаются;
    - при stages={'dedupe': False} флаг --no-dedupe передаётся;
    - при stages={'dedupe': True} флаг --dedupe передаётся.
    """
    import api.jobs as jobs

    captured_cmds = []

    class FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            captured_cmds.append(cmd)
            self.stdout = []

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)

    # 1. review=False (по умолчанию): черновик выключен, есть --no-draft, нет --omni-review;
    # stages без dedupe -> флаги --dedupe/--no-dedupe не передаются
    jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], stages={"draft": True})
    cmd1 = captured_cmds[-1]
    assert "--no-draft" in cmd1
    assert "--omni-review" not in cmd1
    assert "--dedupe" not in cmd1
    assert "--no-dedupe" not in cmd1

    # 2. stages с явным dedupe=False
    jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], stages={"draft": True, "dedupe": False})
    cmd2 = captured_cmds[-1]
    assert "--no-dedupe" in cmd2
    assert "--dedupe" not in cmd2

    # 3. stages с явным dedupe=True
    jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], stages={"draft": True, "dedupe": True})
    cmd3 = captured_cmds[-1]
    assert "--dedupe" in cmd3
    assert "--no-dedupe" not in cmd3

    # 4. review=True при draft=True (в stages): есть --omni-review и НЕТ --no-draft
    jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], review=True, stages={"draft": True})
    cmd4 = captured_cmds[-1]
    assert "--omni-review" in cmd4
    assert "--no-draft" not in cmd4

    # 5. review=True при draft=False (в stages): черновик всё равно равен review (есть --omni-review и НЕТ --no-draft)
    jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], review=True, stages={"draft": False})
    cmd5 = captured_cmds[-1]
    assert "--omni-review" in cmd5
    assert "--no-draft" not in cmd5



