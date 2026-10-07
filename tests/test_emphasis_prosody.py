# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание WX5: тон и RMS считаются ОДИН раз на окно, `yin` вместо `pyin`.

Замер архитектора (cProfile, клип 51 жёлтое, режим voice): `emphasis_precompute` 144.7 с,
из них `librosa.pyin` 141.2 с (154 вызова, `_viterbi` 109 с). Причина — `pyin` звался на
КАЖДОЕ слово отдельно, а внутри него Витерби пересчитывал всю последовательность кадров.

Здесь проверяется новое правило целиком:

1. тон слова — медиана f0 ОЗВУЧЕННЫХ кадров его среза: 220 Гц выше 120 Гц и оба близки
   к правде (на синтетике это проверяется точно);
2. окно считается один раз: `prosody` получает ВСЕ слова окна за один вызов, `tone_track`
   зовётся на окно, а не на слово, и 100 слов синтетики укладываются в 3 с;
3. озвученность — по порогу RMS: кадры тише опорного уровня окна на `VOICED_DROP_DB`
   в тон не идут (иначе пауза с шумом «озвучила» бы слово);
4. `librosa.pyin` не зовётся ВООБЩЕ, а `librosa.yin` — зовётся (мутация «вернуть pyin»
   роняет и этот тест, и тест времени).

Запуск: python -m pytest tests/test_emphasis_prosody.py -q
"""
import os
import sys
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------- #
# Заглушка `librosa` на stdlib+numpy
# --------------------------------------------------------------------------- #
# В CI `librosa` не стоит (набор опциональных пакетов не тянет её зависимости), а тон
# зовётся ленивым импортом `core/emphasis.py` — то есть без пакета признак просто
# выключается. Тесты ниже проверяют АРИФМЕТИКУ тона (медиана озвученных кадров, один
# расчёт на окно, порог RMS), а не качество `yin`: синтетика — синусы и ровная тишина,
# и заглушка считает на них тот же ответ. Поэтому подменяем МОДУЛЬ через `sys.modules`
# (правило проекта: не `monkeypatch.setattr("pkg.Attr")`), а не пропускаем проверки.
def _frame_views(y, frame_length, hop_length):
    """Кадры (n_frames, frame_length) с ЦЕНТРАЛЬНЫМ окном — как `util.frame` в librosa.

    Число кадров и их смещение обязаны совпасть с настоящим `librosa`: по ним
    `emphasis._frame_index` переводит отсчёт звука в кадр тона, и промах на кадр
    уехал бы в медиану слова.
    """
    import numpy as np
    y = np.asarray(y, dtype="float32")
    n = len(y)
    n_frames = 1 + n // hop_length
    pad = frame_length // 2
    yp = np.pad(y, (pad, pad))
    idx = (np.arange(frame_length)[None, :]
           + hop_length * np.arange(n_frames)[:, None])
    return yp[idx]


def _yin(y, fmin=65.0, fmax=500.0, sr=22050, frame_length=2048, hop_length=512,
         trough_threshold=0.1, **_kw):
    """`librosa.yin` на numpy: f0 по кадрам, NaN там, где периодичности не видно.

    Шаги те же, что в оригинале: разностная функция -> кумулятивная нормировка ->
    первый минимум ниже порога -> параболическая подгонка. На синтетическом синусе
    (эти тесты) даёт ту же частоту; сложный звук здесь и не нужен — настоящую
    точность тона проверяет боевой `librosa` в рабочем окружении.
    """
    import numpy as np
    frames = _frame_views(y, frame_length, hop_length)
    span = max(1, int(np.floor(frame_length / 2.0)))
    # Период ищем в полосе fmin..fmax: вне её минимум разностной функции — не голос.
    lo = max(2, int(np.floor(sr / fmax)))
    hi = min(span - 2, int(np.ceil(sr / fmin)) + 1)
    out = []
    for start in range(0, len(frames), 256):            # порциями: кадры — это МБ
        data = frames[start:start + 256].astype("float32")
        nj = len(data)
        diff = np.zeros((nj, span), dtype="float32")
        for tau in range(1, span):
            d = data[:, tau:] - data[:, :-tau]
            diff[:, tau] = np.mean(d * d, axis=1)
        # Кумулятивная нормировка: d'(0)=1, дальше d(tau)*tau / сумма d(1..tau)
        cum = np.cumsum(diff, axis=1)
        cum[:, 0] = 1.0
        cmnd = diff * np.arange(span)[None, :] / np.maximum(cum, 1e-12)
        cmnd[:, 0] = 1.0
        band = cmnd[:, lo:hi + 1]
        tau = lo + np.argmin(band, axis=1)
        cm = cmnd[np.arange(nj), tau]
        # Параболическая подгонка по трём точкам вокруг минимума
        t0 = np.clip(tau - 1, 0, span - 1)
        t2 = np.clip(tau + 1, 0, span - 1)
        y0 = cmnd[np.arange(nj), t0]
        y2 = cmnd[np.arange(nj), t2]
        den = 2.0 * (2.0 * cm - y0 - y2)
        shift = np.where(np.abs(den) > 1e-12, (y2 - y0) / np.where(den == 0, 1.0, den), 0.0)
        period = np.clip(tau + shift, 1.0, None)
        f0 = sr / period
        out.append(np.where(cm < trough_threshold, f0, np.nan).astype("float32"))
    return np.concatenate(out) if out else np.zeros(0, dtype="float32")


def _rms(y, frame_length=2048, hop_length=512, **_kw):
    """`librosa.feature.rms`: форма (1, n_frames) — как у настоящего."""
    import numpy as np
    frames = _frame_views(y, frame_length, hop_length).astype("float64")
    return np.sqrt(np.mean(frames * frames, axis=1))[None, :].astype("float32")


class _Feature:
    rms = staticmethod(_rms)


_fake_librosa = types.ModuleType("librosa")
_fake_librosa.yin = _yin
_fake_librosa.pyin = _yin                            # только чтобы тест №4 мог его подменить
_fake_librosa.feature = _Feature()
_fake_librosa.__file__ = __file__
sys.modules["librosa"] = _fake_librosa

from core import emphasis  # noqa: E402

SR = 22050
FPS = 60.0
CAM1 = "C:/footage/cam1.mp4"


# --------------------------------------------------------------------------- #
# Синтетика: тон известной частоты на слове, пауза между словами
# --------------------------------------------------------------------------- #
def _sine(y, t0, dur, hz, amp=0.3, sr=SR):
    """Вписать в `y` синус `hz` от `t0` на `dur` секунд (амплитуда `amp`)."""
    import numpy as np
    a0, a1 = int(round(t0 * sr)), int(round((t0 + dur) * sr))
    t = np.arange(a1 - a0) / float(sr)
    y[a0:a1] += (amp * np.sin(2.0 * np.pi * hz * t)).astype("float32")
    return y


def _speech(spans, sr=SR, noise=1e-4, seed=7):
    """«Речь»: массив до конца последнего слова, в словах — тихий шум.

    Шум, а не чистые нули: `librosa.yin` на нулях даёт NaN сам по себе, и на такой
    синтетике порог озвученности ничего бы не проверял.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    end = max((e for _i, _s, e in spans), default=0.0)
    y = (rng.standard_normal(int(round(end * sr)) + sr) * noise).astype("float32")
    return y


def _words(n, step=0.5, dur=0.35):
    """`n` слов ролика с монтажными временами (как в соседних тестах силы)."""
    return [emphasis.WordRef(idx=k, text="СЛОВО%d" % k, start=k * step, end=k * step + dur)
            for k in range(n)]


def _parsed(cam_path=CAM1, frames=100000.0):
    """Разбор «XML»: Камера 1 показывает исходник от начала без склеек."""
    cams = [{"path": cam_path, "name": "cam1",
             "clips": [(0.0, frames, 0.0, frames, True)]}]
    return ({"fps": FPS, "dur": frames}, cams, [], [])


# --------------------------------------------------------------------------- #
# 1. Тон и ударение на синтетике: 220 Гц выше 120 Гц
# --------------------------------------------------------------------------- #

def test_тон_слова_это_медиана_озвученных_кадров():
    """Слово A — 120 Гц, слово B — 220 Гц: тон B выше, порядок stress прежний.

    Тон слова обязан быть близок к настоящей частоте, а не к «случайному числу»: медиана
    берётся по озвученным кадрам, и на ровном синтетическом тоне это ровно его частота.
    """
    spans = [(0, 0.0, 0.6), (1, 1.0, 1.6), (2, 2.0, 2.4)]     # третье слово — шум паузы
    y = _speech(spans)
    _sine(y, 0.0, 0.6, 120.0)
    _sine(y, 1.0, 0.6, 220.0)

    feat = emphasis.prosody(y, SR, spans)
    f0_a, f0_b = feat[0][1], feat[1][1]
    assert f0_a is not None and f0_b is not None, "синус обязан дать тон: %r" % (feat,)
    assert f0_a == pytest.approx(120.0, rel=0.1), f0_a
    assert f0_b == pytest.approx(220.0, rel=0.1), f0_b
    assert f0_b > f0_a, "тон 220 Гц обязан быть выше 120 Гц: %r" % (feat,)

    st = emphasis.stress_norm(feat, [0, 1, 2])
    assert st[1] > st[0], (
        "слово выше соседей обязано получить большее ударение: %r" % (st,))
    assert st[1] > 0.5, "слово выше соседей обязано быть выше середины шкалы: %r" % (st,)


def test_тон_слова_не_тянется_за_переходом_в_начале():
    """Тон — медиана озвученных кадров, а не первая/тишайшая частота слова.

    Слово с коротким переходом в начале (180 Гц) и основной частью на 320 Гц обязано
    дать ~320 Гц: мутация «минимум/первый кадр вместо медианы» (питч-трекер шумит на
    переходах) краснит этот тест.
    """
    import numpy as np
    sr = SR
    rng = np.random.default_rng(3)
    y = (rng.standard_normal(int(1.5 * sr)) * 1e-4).astype("float32")
    n = int(0.6 * sr)
    t = np.arange(n) / float(sr)
    f = np.where(t < 0.2, 180.0, 320.0)
    y[:n] += (0.4 * np.sin(np.cumsum(2.0 * np.pi * f / sr))).astype("float32")

    f0 = emphasis._word_f0(emphasis.tone_track(y, sr), 0, n)
    assert f0 == pytest.approx(320.0, rel=0.1), (
        "тон уехал на переход в начале слова: %r" % (f0,))


def test_ударение_растёт_с_громкостью_тоном_и_длительностью_на_синтетике():
    """Среднее слово громче, выше и длиннее соседей — оно и самое сильное.

    Проверяется не только тон (его даёт `yin`), но и весь признак силы: сравнение идёт с
    СОСЕДНИМИ словами, поэтому «сильное» здесь — свойство слова в своей окрестности.
    """
    spans = [(0, 0.0, 0.4), (1, 1.0, 1.4), (2, 2.0, 2.8), (3, 3.0, 3.4)]
    y = _speech(spans)
    _sine(y, 0.0, 0.4, 130.0, amp=0.15)
    _sine(y, 1.0, 0.4, 135.0, amp=0.15)
    _sine(y, 2.0, 0.8, 300.0, amp=0.60)       # громче, выше и вдвое длиннее
    _sine(y, 3.0, 0.4, 132.0, amp=0.15)

    feat = emphasis.prosody(y, SR, spans)
    st = emphasis.stress_norm(feat, [0, 1, 2, 3])
    assert st[2] == max(st.values()), st
    assert st[2] > 0.9, "выброс по трём признакам обязан быть заметно выше середины: %r" % (st,)
    assert 0.4 < st[0] < 0.6 and 0.4 < st[3] < 0.6, st


# --------------------------------------------------------------------------- #
# 2. Один расчёт тона на окно и время
# --------------------------------------------------------------------------- #

def _emphasis_on_audio(arr, sr, words, idx, tmp_path, monkeypatch, window=None):
    """`compute_emphasis` на готовом массиве звука; `window` — кусок по запросу окна."""
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()

    def audio_window(src, w0, w1):
        return (window(w0, w1) if window is not None else arr), sr

    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    monkeypatch.setattr(emphasis, "emotion_probs", lambda win, model, sr: {"neutral": 0.5})
    return emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=_parsed(), xml_path=xml, idx=idx, mode="voice",
        audio_window=audio_window, emit=lambda *a, **k: None)), xml


def test_все_слова_окна_считаются_одним_вызовом(tmp_path, monkeypatch):
    """`prosody` зовётся ОДИН раз на окно и получает ВСЕ его слова за раз.

    Раньше он звался на каждое слово, и `yin` (а до него `pyin`) пересчитывал тон по
    всему массиву заново — 154 вызова `pyin` и 141 с на клипе архитектора. Мутация
    «вернуть пословный вызов» краснит этот счётчик.
    """
    calls = []
    real = emphasis.prosody

    def spy(audio, sr, spans):
        calls.append([i for i, _s, _e in spans])
        return real(audio, sr, spans)

    monkeypatch.setattr(emphasis, "prosody", spy)
    words = _words(30, step=0.5, dur=0.35)
    hl = [10, 11, 12]
    src = [(w.start, w.end) for w in words]
    want = set(range(10 - emphasis.STRESS_NEIGHBORS, 12 + emphasis.STRESS_NEIGHBORS + 1))

    arr = _speech([(i, s, e) for i, (s, e) in enumerate(src)])
    _emphasis_on_audio(arr, SR, words, hl, tmp_path, monkeypatch)

    assert len(calls) == 1, (
        "тон считается не по окну, а по частям: %d вызовов prosody" % len(calls))
    assert set(calls[0]) == want, "в одном окне посчитаны не все его слова: %r" % (calls[0],)
    # Окно запрашивалось целиком (stress_spans объединяет соседей): массив отдан как есть
    assert max(calls[0]) - min(calls[0]) >= 2 * emphasis.STRESS_NEIGHBORS - 1, calls[0]


def test_тон_окна_считается_один_раз_на_много_слов(monkeypatch):
    """`tone_track` зовётся ОДИН раз, сколько бы слов в окне ни было.

    Это структурная проверка «один расчёт на окно, а слова берут срезы»: пословный
    проход снова гонял бы `tone_track` (а с ним `yin`) на каждое слово и вернул бы
    прежние минуты. Мутация «считать тон на слово» краснит счётчик.
    """
    calls = []
    real = emphasis.tone_track

    def spy(audio, sr):
        calls.append(1)
        return real(audio, sr)

    monkeypatch.setattr(emphasis, "tone_track", spy)
    spans = [(k, k * 0.5, k * 0.5 + 0.35) for k in range(9)]
    y = _speech(spans)
    for k, s, e in spans:
        _sine(y, s, e - s, 120.0 if k % 2 else 240.0)

    feat = emphasis.prosody(y, SR, spans)
    assert calls == [1], "тон окна посчитан %d раз вместо одного" % len(calls)
    assert feat[0][1] == pytest.approx(240.0, rel=0.1), feat[0]
    assert feat[1][1] == pytest.approx(120.0, rel=0.1), feat[1]


@pytest.mark.perf
def test_100_слов_синтетики_меньше_3_секунд(tmp_path, monkeypatch):
    """Бюджет задания: клип со 100 жёлтыми считается за считанные секунды, не минуты.

    Ролик 20 с, сто слов подряд (одно склеенное окно), три жёлтых. Мутация «вернуть pyin»
    стоит на таком окне ~2.5 с только на одной его длине и валит тест; на настоящем клипе
    архитектора тем же путём набегали минуты.

    Маркер `perf`: абсолютный бюджет меряет скорость машины, а не код, и в CI джобы идут
    с `-m "not perf"` — иначе медленный раннер красит сборку на ровном месте.
    """
    spans = [(k, k * 0.2, k * 0.2 + 0.15) for k in range(100)]
    arr = _speech(spans)
    for k, s, e in spans:
        _sine(arr, s, e - s, 140.0 if k % 2 else 260.0, amp=0.3)

    # Первый вызов numba компилирует ядро `yin` (секунды) — он не в бюджете расчёта
    emphasis.prosody(arr[: SR], SR, [(0, 0.0, 0.5)])

    words = _words(100, step=0.2, dur=0.15)
    t0 = time.monotonic()
    scores, xml = _emphasis_on_audio(arr, SR, words, [7, 50, 93], tmp_path, monkeypatch)
    dt = time.monotonic() - t0

    assert set(scores) == {7, 50, 93}, scores
    assert os.path.isfile(emphasis.emph_path(xml)), "сайдкар не записан"
    assert dt < 3.0, "100 слов синтетики посчитаны за %.2f с (бюджет 3 с)" % dt


@pytest.mark.perf
def test_время_одного_окна_против_pyin():
    """`tone_track` на окне 20 с — меньше секунды, `pyin` на той же длине — заметно больше.

    Это эталон «насколько стало быстрее»: числа берутся на одном и том же массиве, так что
    тест не зависит от машины — важно только, что `yin` укладывается в бюджет.

    Маркер `perf` — по той же причине, что у теста выше: это абсолютный бюджет времени.
    """
    arr = _speech([(0, 0.0, 20.0)])
    _sine(arr, 0.0, 20.0, 180.0)
    emphasis.tone_track(arr[: SR], SR)          # прогрев numba
    t0 = time.monotonic()
    emphasis.tone_track(arr, SR)
    dt = time.monotonic() - t0
    assert dt < 1.0, "тон окна 20 с считается %.2f с — это снова медленный путь" % dt


# --------------------------------------------------------------------------- #
# 3. Озвученность по порогу RMS
# --------------------------------------------------------------------------- #

def test_кадры_тише_порога_не_идут_в_тон():
    """Громкое слово озвучено, шум паузы — нет: тон не уезжает на шум.

    Опорный уровень окна — процентиль RMS, а не медиана: окно вокруг жёлтых бывает
    большей частью паузой, и медиана такого окна — тишина (порог не отсекал бы ничего).
    """
    import numpy as np
    spans = [(0, 0.0, 0.5), (1, 1.0, 1.5), (2, 2.0, 2.5)]
    y = _speech(spans, noise=1e-5)
    _sine(y, 0.0, 0.5, 150.0, amp=0.5)
    track = emphasis.tone_track(y, SR)

    loud = emphasis._word_f0(track, 0, int(0.5 * SR))
    quiet = emphasis._word_f0(track, int(1.0 * SR), int(1.5 * SR))
    assert loud == pytest.approx(150.0, rel=0.1), loud
    assert quiet is None, (
        "шум паузы попал в тон (%.1f Гц): порог озвученности не работает" % (quiet or 0.0))
    # В самом окне озвученных кадров меньше, чем всего: тишина отсеклась
    assert 0 < int(np.count_nonzero(track.voiced)) < track.voiced.size, track.voiced.size


# --------------------------------------------------------------------------- #
# 4. Движок тона: yin, а не pyin
# --------------------------------------------------------------------------- #

def test_тон_считается_yin_и_ни_разу_pyin(monkeypatch):
    """Расчёт зовёт `librosa.yin` и НЕ зовёт `librosa.pyin`.

    Мутация «вернуть pyin» (и вместе с ним Витерби) роняет этот тест сразу: 109 с из 141
    на клипе архитектора пришли именно оттуда.
    """
    import librosa
    seen = {"yin": 0, "pyin": 0}
    yin, pyin = librosa.yin, librosa.pyin

    def spy_yin(*a, **k):
        seen["yin"] += 1
        return yin(*a, **k)

    def spy_pyin(*a, **k):
        seen["pyin"] += 1
        return pyin(*a, **k)

    monkeypatch.setattr(librosa, "yin", spy_yin)
    monkeypatch.setattr(librosa, "pyin", spy_pyin)

    spans = [(0, 0.0, 0.5), (1, 1.0, 1.5)]
    y = _speech(spans)
    _sine(y, 0.0, 0.5, 150.0)
    _sine(y, 1.0, 0.5, 150.0)
    emphasis.prosody(y, SR, spans)

    assert seen["yin"] >= 1, "тон посчитан без `yin`"
    assert seen["pyin"] == 0, "тон снова считается `pyin` (%d вызовов)" % seen["pyin"]
