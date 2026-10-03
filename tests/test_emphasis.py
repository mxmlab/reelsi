# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание WX, пункты 1–2: сила жёлтых слов — эмоция фразы + ударение по звуку.

Здесь проверяется расчёт без GPU и без звука: модель эмфазы и чтение звука —
заглушки, признаков хватает, чтобы проверить ПОРЯДОК силы и кэш.

1. Порядок силы: слово с эмоциональной фразой (p(neutral) низкая) сильнее
   нейтрального; слово громче/выше/растянутее соседей получает больший `stress`.
2. Сайдкар `<стем>.emph.json` не пересчитывается, пока не изменились слова, набор
   жёлтых и mtime исходника, и пересчитывается, когда жёлтые сменились.
3. Монтажное время слова переводится в ИСХОДНОЕ время Камеры 1 тем же правилом, что
   у дорожки голоса (`plan_audio.voice_segments`): сверка делается на реальном XML.
4. Нет исходника/звука — сила считается без акустики и модель НЕ грузится.
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import emphasis  # noqa: E402
from core.xml2ae import plan_audio  # noqa: E402

FPS = 60.0
CAM1 = "C:/footage/cam1.mp4"


def _words(n, step=0.5, dur=0.4):
    """n слов с монтажными временами: (индекс, 'СЛОВО', начало, конец)."""
    return [emphasis.WordRef(idx=k, text="СЛОВО%d" % k,
                             start=k * step, end=k * step + dur) for k in range(n)]


@pytest.fixture()
def stub_audio(monkeypatch):
    """Звук-заглушка: «моно 16кГц» без файла; модель эмфазы не грузится вовсе."""
    import numpy as np
    audio = np.zeros(16000, dtype="float32")

    monkeypatch.setattr(emphasis, "load_emo_model",
                        lambda: (_ for _ in ()).throw(AssertionError("модель не должна грузиться")))
    monkeypatch.setattr(emphasis, "prosody", lambda *a, **k: {})
    return audio


def _parsed(cam_path=CAM1):
    """Разбор «XML»: Камера 1 показывает [0, 600] кадров исходника от начала.

    Клип: (монтаж_начало, монтаж_конец, исходник_in, исходник_out, включён).
    """
    cams = [{"path": cam_path, "name": "cam1",
             "clips": [(0.0, 600.0, 0.0, 600.0, True)]}]
    return ({"fps": FPS, "dur": 600.0}, cams, [], [])


def _compute(tmp_path, words, idx, monkeypatch, audio_for=None, intro=(), audio=True,
             cam_path=CAM1):
    """Посчитать силу на заглушках: аудио отдаёт `audio_for`, модель — эмоция."""
    import numpy as np
    xml = str(tmp_path / "clip.xml")
    if audio_for is None:
        arr = np.zeros(16000, dtype="float32")
        audio_for = lambda src: (arr, 22050)  # noqa: E731
    return emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=_parsed(cam_path), xml_path=xml, idx=idx, intro_words=intro,
        audio_for=audio_for if audio else None, emit=lambda *a, **k: None))


# --------------------------------------------------------------------------- #
# 1. Порядок силы: эмоция + ударение
# --------------------------------------------------------------------------- #

def test_emo_считается_как_единица_минус_нейтраль():
    """`emo = 1 − p(neutral)`: возмущённая фраза (angry 0.77) сильнее ровной (0.98)."""
    assert emphasis.emotion_score({"neutral": 1.0}) == 0.0
    assert emphasis.emotion_score({"neutral": 0.98}) == pytest.approx(0.02)
    assert emphasis.emotion_score({"neutral": 0.23}) == pytest.approx(0.77)
    # Мусор и пустой ответ читаются как «нейтрально», а не как «сильно»
    assert emphasis.emotion_score({}) == 0.0
    assert emphasis.emotion_score({"neutral": "нет"}) == 0.0


def test_ударение_растёт_со_громкостью_тоном_и_длительностью():
    """z-оценка признаков: слово громче, выше и растянутее соседей — сильнее."""
    idx = [0, 1, 2, 3, 4]
    # 2-е слово — выброс по всем трём признакам; у соседей признаки разные (иначе MAD=0
    # и z-оценка честно даёт ноль: «все слова одинаковые»).
    base = [(-31.0, 118.0, 0.19), (-30.0, 122.0, 0.21), (-16.0, 200.0, 0.55),
            (-29.0, 119.0, 0.22), (-32.0, 121.0, 0.18)]
    feat = dict(enumerate(base))
    st = emphasis.stress_norm(feat, idx)
    assert st[2] == max(st.values()), st
    assert st[2] > 0.9, "выброс по трём признакам обязан быть заметно выше середины"
    # Соседи, сидящие ровно на медиане признаков, — у самой середины шкалы (0.5):
    # одиночный выброс не должен перекашивать оценку остальных слов.
    assert 0.4 < st[1] < 0.6 and 0.4 < st[3] < 0.6, st
    assert st[0] < 0.5 and st[4] < 0.5, st
    # Слово без признаков (не попало в звук) — ровно середина, «обычное»
    assert emphasis.stress_norm({2: (0.0, 0.0, 0.1)}, [2])[2] == pytest.approx(0.5)


def test_сила_складывается_из_эмоции_и_ударения(tmp_path, monkeypatch):
    """`score = 0.5·emo + 0.5·stress`: эмоциональное слово впереди при том же звуке."""
    import numpy as np
    words = _words(10)
    # Звук одинаковый: у всех признаков одна величина -> z=0 -> stress=0.5
    monkeypatch.setattr(emphasis, "prosody",
                        lambda audio, sr, spans: {i: (-30.0, 120.0, 0.2) for i, _s, _e in spans})
    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    # Эмоция различается ПО ОКНУ: в звуке стоит метка точно на середине слова 8, и окно,
    # которое её накрыло, читается как возмущённое. Слова берём далеко друг от друга
    # (окно 2.5 с), иначе метка попала бы в окна соседей — как и в жизни.
    audio = np.zeros(10 * 22050, dtype="float32")
    audio[int(round((words[8].start + words[8].end) / 2 * 22050))] = 2.0
    monkeypatch.setattr(emphasis, "emotion_probs",
                        lambda window, model, sr: {"neutral": 0.2 if float(np.max(window)) > 1.0 else 1.0})
    scores = _compute(tmp_path, words, [0, 8], monkeypatch,
                      audio_for=lambda src: (audio, 22050))
    assert scores[8] > scores[0], "эмоциональное слово обязано быть сильнее нейтрального"
    assert scores[0] == pytest.approx(0.25), "0.5·0 + 0.5·0.5"


def test_слова_без_источника_нейтральны(tmp_path, monkeypatch):
    """Слово вне клипов Камеры 1 (перебивка) — сила 0.25 (эмоция 0, ударение 0.5)."""
    import numpy as np
    words = _words(3)
    segs = [{"ts": 0.0, "te": 60.0, "src": 0.0}]      # исходник только под первым словом
    monkeypatch.setattr(emphasis, "_voice_segments_frames", lambda clips: segs)
    monkeypatch.setattr(emphasis, "prosody",
                        lambda audio, sr, spans: {i: (-30.0, 120.0, 0.2) for i, _s, _e in spans})
    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    monkeypatch.setattr(emphasis, "emotion_probs",
                        lambda window, model, sr: {"neutral": 1.0})
    scores = _compute(tmp_path, words, [0, 1, 2], monkeypatch,
                      audio_for=lambda src: (np.zeros(16000, dtype="float32"), 22050))
    assert scores[0] == pytest.approx(0.25)
    assert scores[1] == pytest.approx(0.25) and scores[2] == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# 2. Кэш сайдкара
# --------------------------------------------------------------------------- #

def test_кэш_не_пересчитывает_без_изменений(tmp_path, monkeypatch):
    """Тот же клип, те же слова и жёлтые — второй прогон берёт сайдкар, а не считает."""
    import numpy as np
    words = _words(4)
    cam = tmp_path / "cam1.mp4"                 # исходник существует: путь нужен расчёту
    cam.write_bytes(b"\x00")
    monkeypatch.setattr(emphasis, "prosody",
                        lambda audio, sr, spans: {i: (-30.0, 120.0, 0.2) for i, _s, _e in spans})
    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    monkeypatch.setattr(emphasis, "emotion_probs", lambda window, model, sr: {"neutral": 0.5})
    calls = {"n": 0}

    def counting_audio(src):
        calls["n"] += 1
        return np.zeros(16000, dtype="float32"), 22050

    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=_parsed(str(cam)), xml_path=xml, idx=[0, 1],
        audio_for=counting_audio, emit=lambda *a, **k: None))
    assert calls["n"] == 1, "звук должен читаться ровно один раз"

    view = emphasis.read_emphasis(xml, words, (), [0, 1], str(cam), idx=[0, 1])
    assert view.valid and not view.uncomputed, "сайдкар не читается сразу после расчёта"
    assert set(view.scores) == {0, 1}
    again = emphasis.read_emphasis(xml, words, (), [0, 1], str(cam), idx=[0, 1])
    assert again.scores == view.scores and calls["n"] == 1
    assert emphasis.emph_path(xml).endswith(".emph.json")


def test_кэш_пересчитывается_при_смене_жёлтых(tmp_path, monkeypatch):
    """Набор жёлтых входит в ключ: другой набор — сайдкар не годен (пересчёт)."""
    import numpy as np
    words = _words(4)
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"\x00")
    monkeypatch.setattr(emphasis, "prosody", lambda *a, **k: {})
    monkeypatch.setattr(emphasis, "load_emo_model", lambda: object())
    monkeypatch.setattr(emphasis, "release_emo", lambda model: None)
    monkeypatch.setattr(emphasis, "emotion_probs", lambda window, model, sr: {"neutral": 0.5})
    xml = str(tmp_path / "clip.xml")
    open(xml, "w", encoding="utf-8").close()
    emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=_parsed(str(cam)), xml_path=xml, idx=[0, 1],
        audio_for=lambda src: (np.zeros(16000, dtype="float32"), 22050),
        emit=lambda *a, **k: None))

    assert emphasis.read_emphasis(xml, words, (), [0, 1], str(cam), idx=[0, 1]).valid
    assert not emphasis.read_emphasis(xml, words, (), [0, 2], str(cam), idx=[0, 2]).valid, \
        "смена жёлтых обязана обнулить кэш"
    # Новое жёлтое слово добавлено после расчёта — оно НЕ посчитано, и об этом честно
    miss = emphasis.read_emphasis(xml, words, (), [0, 1], str(cam), idx=[0, 1, 2])
    assert miss.valid and miss.uncomputed == [2]


# --------------------------------------------------------------------------- #
# 3. Монтажное время -> исходное время Камеры 1
# --------------------------------------------------------------------------- #

@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_куски_голоса_совпадают_с_дорожкой_звука(xml_subs):
    """Правило «какой кусок исходника играет» — то же, что у `voice_segments`.

    Ключ силы считается по монтажу, а звук берётся из ИСХОДНИКА: разъедутся правила —
    слово получит чужой звук, и этого не увидеть ни в плане, ни в AE.
    """
    from core import xml2ae
    meta, cams, _subs, _xi = xml2ae.parse_full(xml_subs)
    fps = float(meta["fps"])
    mine = emphasis._voice_segments_frames(cams[0]["clips"])
    theirs = plan_audio.voice_segments(cams[0]["clips"], fps)
    assert [(round(s["ts"] / fps, 6), round(s["te"] / fps, 6), round(s["src"] / fps, 6))
            for s in mine] == [(round(t["ts"], 6), round(t["te"], 6), round(t["src"], 6))
                               for t in theirs]


def test_время_слова_переводится_в_исходник(xml_subs):
    """Слово монтажа -> время исходника по куску, накрывающему его СЕРЕДИНУ."""
    from core import xml2ae
    meta, cams, subs, _xi = xml2ae.parse_full(xml_subs)
    fps = float(meta["fps"])
    words = emphasis.word_refs(subs, fps)
    segs = emphasis._voice_segments_frames(cams[0]["clips"])
    times = emphasis._source_times(segs, words, fps)
    assert len(times) == len(words)
    for w, t in zip(words, times):
        if t is None:
            continue
        seg = next(s for s in segs
                   if s["ts"] / fps <= (w.start + w.end) / 2 <= s["te"] / fps)
        base = seg["src"] / fps
        assert t[0] == pytest.approx(base + (w.start - seg["ts"] / fps), abs=1e-6)
        assert t[1] - t[0] == pytest.approx(w.end - w.start, abs=1e-6)


# --------------------------------------------------------------------------- #
# 4. Нет звука — модель не грузится, сила нейтральна
# --------------------------------------------------------------------------- #

def test_нет_исходника_модель_не_грузится(tmp_path, monkeypatch):
    """Исходника на диске нет — считаем без акустики: модель не грузим, не падаем."""
    words = _words(3)
    monkeypatch.setattr(emphasis, "load_emo_model",
                        lambda: (_ for _ in ()).throw(AssertionError("модель не нужна")))
    monkeypatch.setattr(emphasis, "prosody",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("звука нет")))
    xml = str(tmp_path / "clip.xml")
    scores = emphasis.compute_emphasis(emphasis.EmphasisInputs(
        words=words, parsed=_parsed(), xml_path=xml, idx=[0, 1, 2],
        emit=lambda *a, **k: None))
    assert scores == {0: pytest.approx(0.25), 1: pytest.approx(0.25), 2: pytest.approx(0.25)}
    data = json.loads(open(emphasis.emph_path(xml), encoding="utf-8").read())
    assert data["key"]["v"] == emphasis.EMPH_VERSION
    assert data["key"]["yellow"] == [0, 1, 2]
