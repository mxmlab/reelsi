# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Детектор вздохов: кандидаты, применение решения, разобранная модель.

Сами модели (Silero/CED) тут не гоняем — тесты должны идти без сети и GPU.
Проверяем контракты, на которых всё держится: где ищем вздохи, что режем, что
камера не перепутается и что экспортированная модель считает то же, что sklearn.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys
import types

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import breath  # noqa: E402


WORDS = [{"w": "раз", "start": 1.0, "end": 1.5},
         {"w": "два", "start": 2.0, "end": 2.4},
         {"w": "три", "start": 3.0, "end": 3.5}]


def test_candidates_are_gaps_between_words():
    """Кандидат — место БЕЗ слова: голова куска, дырка между словами, хвост."""
    c = breath.candidates([(0.8, 3.9)], WORDS)
    got = [(round(x["t0"], 2), round(x["t1"], 2), x["pos"]) for x in c]
    assert got == [(0.8, 1.0, "голова"), (1.5, 2.0, "середина"),
                   (2.4, 3.0, "середина"), (3.5, 3.9, "хвост")]


def test_candidates_skip_too_short_and_too_long():
    """Стык волн (<MIN_DUR) — не кандидат, и длинные паузы тоже не наше дело:
    их режет `refine_keep`, а лезть детектором в секундную дыру опасно."""
    words = [{"w": "а", "start": 0.5, "end": 0.6}, {"w": "б", "start": 2.5, "end": 3.0}]
    c = breath.candidates([(0.45, 3.05)], words)          # голова 0.05с, дыра 1.9с
    assert [x["pos"] for x in c] == []


def test_apply_cuts_only_confident_and_keeps_camera():
    """Режем только уверенные метки, и подкуски наследуют камеру родителя —
    иначе камера прыгнет прямо на вздохе посреди фразы."""
    keep = [(0.0, 5.0), (6.0, 8.0)]
    marks = [{"t0": 2.0, "t1": 2.4, "p": 0.99, "речь": 0.01},   # режем
             {"t0": 7.0, "t1": 7.3, "p": 0.40, "речь": 0.01}]   # только метка
    out, cut, parents = breath.apply(keep, marks, p_cut=0.95, min_island=0.3)
    assert len(cut) == 1
    assert parents == [0, 0, 1]                    # первый кусок разрезан надвое
    assert not any(a <= 2.2 <= b for a, b in out)
    assert any(abs(a - 6.0) < 1e-6 for a, b in out)


def test_apply_never_cuts_when_it_might_be_speech():
    """Страховка важнее вероятности: высокая вероятность РЕЧИ на участке
    запрещает автоматический рез при любом p (ложный рез стоит слова)."""
    keep = [(0.0, 5.0)]
    marks = [{"t0": 2.0, "t1": 2.4, "p": 1.0, "речь": 0.9}]
    out, cut, parents = breath.apply(keep, marks, p_cut=0.95)
    assert cut == [] and out == keep and parents == [0]


def test_model_file_matches_features():
    """Модель обучена ровно на текущем наборе признаков (иначе молча поедут
    веса и детектор начнёт резать наугад)."""
    if not os.path.exists(breath.MODEL_JSON):
        pytest.skip("нет breath_model.json")
    m = breath.load_model()
    assert m["features"] == breath.FEATURES
    X = np.zeros((3, len(breath.FEATURES)))
    p = breath.predict(X, m)
    assert p.shape == (3,) and np.all((p >= 0) & (p <= 1))


def test_predict_reads_trees_like_sklearn():
    """Разобранный в json бустинг считается тем же обходом дерева, что и sklearn:
    один пень с порогом по первому признаку."""
    mdl = {"kind": "бустинг", "features": breath.FEATURES, "baseline": 0.0,
           "trees": [{"feature": [0, 0, 0], "thr": [0.5, 0, 0],
                      "left": [1, 0, 0], "right": [2, 0, 0],
                      "leaf": [False, True, True], "value": [0.0, -2.0, 2.0]}]}
    X = np.zeros((2, len(breath.FEATURES)))
    X[1, 0] = 1.0
    p = breath.predict(X, mdl)
    assert p[0] < 0.2 and p[1] > 0.8


def _deps_present(monkeypatch):
    """Сделать вид, что silero-vad и transformers на месте.

    `available()` проверяет их ДО файла модели, поэтому на машине без этих пакетов
    (чистый CI, свежий клон) тест про имя файла получал причину «нет silero-vad» и
    падал — проверял не то, что заявлял. Ветка про файл обязана проверяться
    независимо от того, что установлено у того, кто гоняет тесты.
    """
    for name in ("silero_vad", "transformers"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def test_missing_dependency_is_named(monkeypatch, tmp_path):
    """Нет пакета — причина называет ПАКЕТ, а не файл модели.

    Порядок проверок в available() значим: сказать «нет breath_model.json» тому, у
    кого на самом деле не стоит silero-vad, — отправить человека обучать модель
    вместо `pip install`."""
    monkeypatch.setitem(sys.modules, "silero_vad", None)
    monkeypatch.setattr(breath, "MODEL_JSON", str(tmp_path / "нет.json"))
    ok, why = breath.available()
    assert not ok and "silero-vad" in why


def test_unavailable_degrades_quietly(monkeypatch, tmp_path):
    """Нет весов — нарезка идёт как раньше, без падений посреди джоба.
    В причине называем ИМЕННО тот файл, которого нет: у спикера может быть своя
    модель, и «нет breath_model.json» тогда сбивало бы с толку."""
    _deps_present(monkeypatch)
    monkeypatch.setattr(breath, "MODEL_JSON", str(tmp_path / "нет.json"))
    ok, why = breath.available()
    assert not ok and "нет.json" in why
    assert breath.detect("нет.wav", [(0.0, 1.0)], WORDS,
                         emit=lambda *a, **k: None) == []
    # то же самое для личной модели спикера
    ok, why = breath.available(str(tmp_path / "breath_model.Кто-то.json"))
    assert not ok and "breath_model.Кто-то.json" in why


def test_cut_breaths_skips_when_models_unavailable(monkeypatch, tmp_path):
    """Без внешних моделей _cut_breaths тихо пропускает шаг и возвращает keep как есть."""
    from core.gigaam_cut import tune as tune
    _deps_present(monkeypatch)
    monkeypatch.setattr(breath, "MODEL_JSON", str(tmp_path / "нет.json"))
    wav = tmp_path / "dummy.wav"
    wav.write_bytes(b"")
    keep = [(0.0, 1.0)]
    assign = [1]
    k, a, marks = tune._cut_breaths(keep, assign, str(wav), WORDS, "out.xml",
                                    emit=lambda *a, **k: None)
    assert k == keep and a == assign and marks == []


def test_cut_breaths_raises_on_bad_audio_when_model_present(monkeypatch):
    """WAV нет на диске, модель доступна — _cut_breaths обязан бросить FileNotFoundError."""
    from core.gigaam_cut import tune as tune
    monkeypatch.setattr(breath, "available", lambda *a, **k: (True, ""))
    keep = [(0.0, 3.0)]
    assign = [0]
    with pytest.raises(FileNotFoundError):
        tune._cut_breaths(keep, assign, "nonexistent.wav", WORDS, "out.xml",
                          emit=lambda *a, **k: None)


def test_cut_breaths_catches_errors_and_returns_input(monkeypatch, tmp_path):
    """При любых ошибках внутри детектора (модели, инференс) _cut_breaths не падает, а пропускает шаг."""
    from core.gigaam_cut import tune as tune
    wav = tmp_path / "dummy.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(breath, "detect", lambda *a, **k: (_ for _ in ()).throw(OSError("ошибка")))
    keep = [(0.0, 3.0)]
    assign = [0]
    logs = []
    k, a, marks = tune._cut_breaths(keep, assign, str(wav), WORDS, "out.xml",
                                    emit=lambda msg, **k: logs.append(msg))
    assert k == keep and a == assign and marks == []
    assert any("детектор вздохов не отработал" in log for log in logs)

