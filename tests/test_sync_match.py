# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Автоподбор камер по звуку: тот же дубль коррелирует, чужой материал — нет.

Синхронный дубль двух камер звучит одинаково даже при разных именах файлов и
разном времени старта записи — на этом стоит /api/cammatch (кнопка «Подбор по
звуку» на шаге 1). Здесь стерегутся сам признак (нормированный пик корреляции
огибающих) и его порог MATCH_MIN: ниже — «не тот дубль», и подбор не подставит
чужой файл.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys
import time

import numpy as np
import pytest
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import cams  # noqa: E402
from core import sync  # noqa: E402

SR = 16000


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _speech(sec, seed):
    """Речь-подобный сигнал: «слова» (шумовые вспышки 0.2–0.9 с) с паузами
    0.1–0.5 с — как реальная речь, чья огибающая затухает между словами.
    Один seed = тот же дубль, разные = другой материал."""
    rng = np.random.default_rng(seed)
    n = int(sec * SR)
    x = np.zeros(n)
    i = 0
    while i < n:
        word = min(int(rng.uniform(0.2, 0.9) * SR), n - i)
        x[i:i + word] = rng.standard_normal(word) * 0.4
        i += word + int(rng.uniform(0.1, 0.5) * SR)
    return (x * 30000).astype(np.int16)


def _wav(tmp_path, name, x, delay=0.0):
    if delay:
        x = np.roll(x, -int(delay * SR))   # B включилась ПОЗЖЕ: b[t] = a[t + delay]
    p = str(tmp_path / name)
    wavfile.write(p, SR, x)
    return p


def test_same_take_matches_and_reports_offset(tmp_path):
    # 60 с, а не 20: решение выносится только при MATCH_MIN_OVERLAP_SEC общего звука
    a = _wav(tmp_path, "a.wav", _speech(60.0, 1))
    b = _wav(tmp_path, "b.wav", _speech(60.0, 1), delay=2.3)   # камера B включилась позже
    c = _wav(tmp_path, "c.wav", _speech(60.0, 42))             # другой дубль
    off, sc = sync.match_wavs(a, b)
    assert abs(off - 2.3) < 0.05
    assert sc > 0.5
    _, sc2 = sync.match_wavs(a, c)
    assert sc2 < sync.MATCH_MIN


def test_short_clip_matches_inside_a_long_recording(tmp_path):
    """Дубль находится, даже если камера 2 писала смену ОДНИМ длинным файлом.

    Пойманный баг (2026-08-11): счёт делился на ПОЛНЫЕ нормы обоих файлов, поэтому
    падал как корень из отношения длин — верный дубль в файле вдесятеро длиннее давал
    0.290 и отсекался порогом. На реальной съёмке так терялись клипы, вырезанные из
    длинной записи (ng10.mov внутри TRN_8917.MP4: 0.336 старой формулой против 0.902
    новой). Нормируем по перекрытию — длина кандидата больше ни на что не влияет.

    `np.roll` здесь не годится: он даёт файлы ОДНОЙ длины, а ломалось именно на разной.
    """
    long_take = _speech(600.0, 5)
    whole = _wav(tmp_path, "whole.wav", long_take)               # вся смена, 600 с
    for start, dur in ((0.0, 300.0), (200.0, 60.0), (500.0, 45.0)):   # до 13x короче
        clip = long_take[int(start * SR):int((start + dur) * SR)]
        p = _wav(tmp_path, "clip_%d_%d.wav" % (start, dur), clip)
        off, sc = sync.match_wavs(p, whole)
        assert sc >= sync.MATCH_MIN, "кусок %.0fs из 600s не нашёлся: %.3f" % (dur, sc)
        assert abs(off + start) < 0.1, "сдвиг куска с %.0fs посчитан как %.2f" % (start, off)


def test_a_long_foreign_file_does_not_beat_the_threshold(tmp_path):
    """Длинный чужой файл обязан остаться ниже порога.

    Обратная сторона нормировки по перекрытию: чем короче перекрытие, тем выше
    забирается ЛУЧШИЙ случайный сдвиг — в длинном файле окон для примерки много, и
    какое-нибудь совпадёт. Поймано этим же тестом 2026-08-11: 15-секундный чужой
    кусок против файла на 300 с давал 0.513 при пороге 0.50. Лечится не порогом
    (верный дубль даёт ~1.0 при любой длине), а требованием минимума общего звука —
    MATCH_MIN_OVERLAP_SEC.
    """
    long_foreign = _speech(300.0, 12)
    for dur in (60.0, 120.0):
        a = _wav(tmp_path, "a_%d.wav" % dur, _speech(dur, 11))
        b = _wav(tmp_path, "b_%d.wav" % dur, long_foreign)
        _, sc = sync.match_wavs(a, b)
        assert sc < sync.MATCH_MIN, "чужой длинный файл прошёл порог: %.3f" % sc


def test_too_little_common_audio_is_reported_as_no_match(tmp_path):
    """Короткому куску просто не хватает звука на решение — отвечаем «не нашлось».

    Догадка тут дороже промаха: «похожего не нашлось» юзер видит сразу и выберет
    файл руками, а чужая камера, молча попавшая в пару, тихо испортит нарезку.
    """
    src = _speech(300.0, 3)
    whole = _wav(tmp_path, "whole.wav", src)
    tiny = _wav(tmp_path, "tiny.wav", src[:int(20.0 * SR)])   # 20 с < MATCH_MIN_OVERLAP_SEC
    off, sc = sync.match_wavs(tiny, whole)
    assert (off, sc) == (0.0, 0.0), "20-секундный кусок обязан дать «не нашлось»"
    # ровно тот же кусок, но подлиннее порога — уже находится
    enough = _wav(tmp_path, "enough.wav", src[:int(60.0 * SR)])
    _, sc2 = sync.match_wavs(enough, whole)
    assert sc2 >= sync.MATCH_MIN


def test_threshold_sits_between_measured_noise_and_measured_matches():
    """Порог живёт в пустой полосе между шумом и совпадениями (замер на реальной
    съёмке 2026-08-11: чужое <= 0.29, свои 0.78-1.00). Числа в комментарии к
    MATCH_MIN — не украшение: если порог уедет к краю полосы, подбор начнёт либо
    терять дубли, либо подставлять чужие."""
    assert 0.35 <= sync.MATCH_MIN <= 0.70
    assert 0.0 < sync.MATCH_MIN_OVERLAP <= 1.0
    assert sync.MATCH_MIN_OVERLAP_SEC >= 30.0


@pytest.fixture
def no_length_gate(monkeypatch):
    """Роутинговые тесты ниже подсовывают огибающие в десяток отсчётов: они стерегут
    ВЫБОР файла (лучший, не сам себя, порог), а не акустику. Минимум общего звука
    (MATCH_MIN_OVERLAP_SEC) на таких игрушечных данных не выполним по построению —
    снимаем именно его, порог MATCH_MIN остаётся боевым."""
    monkeypatch.setattr(sync, "MATCH_MIN_OVERLAP_SEC", 0.0)


def test_extract_audio_has_a_timeout_and_cache_is_pruned(tmp_path, monkeypatch):
    """Извлечение звука не висит вечно, а кэш огибающих не растёт вечно.

    /api/cammatch гоняет ffmpeg по всей папке синхронно в потоке запроса, отменить
    его нечем: без `timeout` один битый файл вешал бы запрос навсегда (политика
    аудита 2026-08-09 — таймауты на все ffmpeg-вызовы, этот её пропустил).
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    sync.extract_audio("in.mp4", str(tmp_path / "out.wav"))
    assert seen.get("timeout"), "ffmpeg извлечения звука запущен без timeout"
    assert seen.get("check") is True

    # чистка кэша: старое уходит, свежее остаётся
    monkeypatch.setattr(sync, "_ENV_CACHE", str(tmp_path))
    old = tmp_path / "old.npy"; new = tmp_path / "new.npy"
    old.write_bytes(b"x"); new.write_bytes(b"x")
    ancient = time.time() - (sync.ENV_CACHE_TTL_DAYS + 1) * 86400
    os.utime(str(old), (ancient, ancient))
    sync._prune_env_cache()
    assert not old.exists() and new.exists()


def test_cammatch_picks_the_best_and_skips_itself(client, tmp_path, monkeypatch,
                                                  no_length_gate):
    cam = tmp_path / "cam"; other = tmp_path / "other"
    cam.mkdir(); other.mkdir()
    (cam / "main.mp4").write_bytes(b"x")
    for f in ("ok1.mp4", "ok2.mp4", "bad.mp4"):
        (other / f).write_bytes(b"x")

    envs = {
        "main.mp4": (np.ones(10), 100.0),
        "ok1.mp4": (np.ones(10), 100.0),                   # 100% совпадение
        "ok2.mp4": (np.array([1] * 5 + [0] * 5), 100.0),   # ~71% — проигрывает
        "bad.mp4": (np.array([1, -1] * 5), 100.0),         # ~0 — чужой материал
    }
    monkeypatch.setattr(sync, "video_envelope",
                        lambda v: envs[os.path.basename(v)])
    monkeypatch.setattr(cams, "list_videos", lambda d: sorted(os.listdir(d)))
    d = client.post("/api/cammatch", json={"cam1": str(cam / "main.mp4"),
                                           "dirs": [str(other)]}).get_json()
    assert d["ok"], d
    m = d["matches"][0]
    assert m["name"] == "ok1.mp4" and m["score"] > 0.9
    assert m["offset"] == 0.0


def test_cammatch_reports_no_match_below_threshold(client, tmp_path, monkeypatch,
                                                   no_length_gate):
    cam = tmp_path / "cam"; other = tmp_path / "other"
    cam.mkdir(); other.mkdir()
    (cam / "main.mp4").write_bytes(b"x")
    (other / "z.mp4").write_bytes(b"x")
    # всё — «чужой» материал: пик корреляции не дотянет до MATCH_MIN
    monkeypatch.setattr(sync, "video_envelope",
                        lambda v: (np.array([1.0, -1.0] * 5), 100.0))
    monkeypatch.setattr(cams, "list_videos", lambda d: sorted(os.listdir(d)))
    d = client.post("/api/cammatch", json={"cam1": str(cam / "main.mp4"),
                                           "dirs": [str(other)]}).get_json()
    # cam1 сюда не входит (другой каталог), у кандидата огибающая — переменный
    # знак: с «постоянной» огибающей cam1 корреляция ~0
    monkeypatch.setattr(sync, "video_envelope",
                        lambda v: (np.array([1.0, -1.0] * 5), 100.0) if v.endswith("z.mp4")
                                  else (np.ones(10), 100.0))
    d = client.post("/api/cammatch", json={"cam1": str(cam / "main.mp4"),
                                           "dirs": [str(other)]}).get_json()
    assert d["ok"] and d["matches"][0]["name"] is None


def test_cammatch_joins_dir_and_name_on_the_server(client, tmp_path, monkeypatch,
                                                   no_length_gate):
    """Папку и ИМЯ файла камеры 1 склеивает СЕРВЕР (os.path.join), как в /api/run.

    Пойманный баг (2026-08-11): фронт лепил разделитель сам — `dir + '\\\\' + имя`, —
    и на Linux/macOS выходило «/home/u/cam\\file.mp4». Файл не находился, и обе
    кнопки подбора по звуку всегда отвечали «нет видео камеры 1». Под Windows
    незаметно, поэтому стережём контракт, а не платформу.
    """
    cam = tmp_path / "cam"; other = tmp_path / "other"
    cam.mkdir(); other.mkdir()
    (cam / "main.mp4").write_bytes(b"x")
    (other / "ok.mp4").write_bytes(b"x")
    monkeypatch.setattr(sync, "video_envelope", lambda v: (np.ones(10), 100.0))
    monkeypatch.setattr(cams, "list_videos", lambda d: sorted(os.listdir(d)))

    d = client.post("/api/cammatch", json={"cam1dir": str(cam), "cam1": "main.mp4",
                                           "dirs": [str(other)]}).get_json()
    assert d["ok"], d
    assert d["cam1"] == "main.mp4" and d["matches"][0]["name"] == "ok.mp4"

    # целый путь в cam1 (как звали роут до правки) обязан работать по-прежнему
    d2 = client.post("/api/cammatch", json={"cam1": str(cam / "main.mp4"),
                                            "dirs": [str(other)]}).get_json()
    assert d2["ok"] and d2["matches"][0]["name"] == "ok.mp4"
