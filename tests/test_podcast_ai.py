# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты core/podcast_ai.py: фразы, прилипание границ, расшифровка, ИИ-выбор, настройки.

Без сети, GPU и ffmpeg: `_run_ff`, `transcribe_words`, `_ask_json` и каталог моделей
подменяются по модулю-владельцу. Конфиг ИИ пишется только во временный файл
(REELSI_AI_CONFIG из conftest), боевой ai_config.json тесты не трогают.
"""
import os
from types import SimpleNamespace

import pytest

from core import aicut, fileio, podcast, podcast_ai
from core.umsg import ReelsiError

URL = "https://www.youtube.com/watch?v=abcDEF12345"


def _w(text, start, end):
    return {"w": text, "start": start, "end": end}


def _flow(n=60, step=0.55, length=0.5):
    """Слова с паузой 0.05 с: внутри речи нет чистых границ, есть только начало и конец записи."""
    return [_w(f"w{i}", round(i * step, 2), round(i * step + length, 2)) for i in range(n)]


def _seg(start, end, title="t", why="w"):
    return {"start": start, "end": end, "title": title, "why": why}


# --- фразы и время ---

def test_phrases_split_by_pause():
    words = [_w("a", 0.0, 0.5), _w("b", 1.0, 1.4), _w("c", 2.5, 3.0)]
    # пауза a→b 0.5 < 0.6: одна фраза; пауза b→c 1.1 ≥ 0.6: новая
    out = podcast_ai.phrases(words)
    assert [p["text"] for p in out] == ["a b", "c"]
    assert out[0]["start"] == 0.0 and out[0]["end"] == 1.4
    assert out[1]["start"] == 2.5


def test_phrases_split_by_period_without_pause():
    words = [_w("Конец.", 0.0, 0.5), _w("Новое", 0.55, 1.0)]
    out = podcast_ai.phrases(words)
    assert [p["text"] for p in out] == ["Конец.", "Новое"]


def test_fmt_time():
    assert podcast_ai.fmt_time(3725.2) == "1:02:05"
    assert podcast_ai.fmt_time(65) == "01:05"
    assert podcast_ai.fmt_time(3600) == "1:00:00"
    assert podcast_ai.fmt_time(-3) == "00:00"


# --- прилипание границ ---

def test_snap_start_sticks_to_word_after_pause():
    # a3 кончается в 3.0, b1 начинается в 4.0 — пауза 1.0. Сырой start 5.2 стоит в середине
    # фразы b: ближе всего b2 (5.1), но перед ним пауза 0.1. Должно прилипнуть к b1 (4.0).
    words = [_w("a2", 1.1, 2.0), _w("a3", 2.1, 3.0),
             _w("b1", 4.0, 5.0), _w("b2", 5.1, 6.0), _w("b3", 6.1, 7.0)]
    segs, _ = podcast_ai.snap_segments([_seg(5.2, 7.0)], words, 1, 100.0, min_len=1.0)
    assert segs[0]["start"] == 4.0


def test_snap_end_sticks_to_word_before_pause():
    # a2 кончается в 2.0 (за ним пауза 0.1), a3 — в 3.0 (за ним пауза 1.0). Сырой end 2.05
    # ближе к a2, но граница должна встать на a3: за ним есть пауза.
    words = [_w("a1", 0.0, 1.0), _w("a2", 1.1, 2.0), _w("a3", 2.1, 3.0),
             _w("b1", 4.0, 5.0), _w("b2", 5.1, 6.0)]
    segs, _ = podcast_ai.snap_segments([_seg(0.0, 2.05)], words, 1, 100.0, min_len=1.0)
    assert segs[0]["end"] == 3.0


def test_snap_overlap_is_resolved_and_validates():
    words = _flow()
    raw = [_seg(0.0, 7.0), _seg(6.0, 15.0)]  # второй начинается внутри первого
    segs, _ = podcast_ai.snap_segments(raw, words, 2, 100.0)
    assert len(segs) == 2
    first, second = segs
    # у второго старт — начало первого слова ПОСЛЕ конца первого куска
    assert second["start"] >= first["end"]
    assert second["start"] == words[13]["start"]
    assert podcast.validate_segments(segs, 100.0) == []


def test_snap_short_segment_dropped_with_warning():
    words = _flow()
    raw = [_seg(0.0, 7.0), _seg(16.0, 19.0, title="короткий")]
    segs, warns = podcast_ai.snap_segments(raw, words, 2, 100.0)
    assert len(segs) == 1
    assert any("короткий" in w and "короче" in w for w in warns)


def test_snap_takes_first_n_of_five_and_ids_are_unique():
    raw = [_seg(1.0 + 10 * i, 8.0 + 10 * i, title=f"т{i}") for i in range(5)]
    segs, warns = podcast_ai.snap_segments(raw, [], 3, 200.0)
    assert len(segs) == 3
    assert [s["title"] for s in segs] == ["т0", "т1", "т2"]
    assert len({s["id"] for s in segs}) == 3
    assert any("взяли первые 3" in w for w in warns)


def test_snap_fewer_answers_than_n_warns():
    raw = [_seg(1.0, 9.0), {"start": "x", "end": 9.0}]
    segs, warns = podcast_ai.snap_segments(raw, [], 3, 200.0)
    assert len(segs) == 1
    assert "ИИ дал 1 из 3" in warns


def test_snap_ids_unique_over_many_segments():
    raw = [_seg(10.0 * i, 10.0 * i + 8.0) for i in range(60)]
    segs, _ = podcast_ai.snap_segments(raw, [], 60, 1000.0)
    assert len({s["id"] for s in segs}) == 60


# --- расшифровка ---

@pytest.fixture
def audio_env(tmp_path, monkeypatch):
    """Источник, ffmpeg-заглушка (пишет временный wav), длина записи 100 с."""
    source = tmp_path / "src.mkv"
    source.write_bytes(b"video")
    ff_calls = []

    def fake_ff(cmd, cancel=None, **kw):
        ff_calls.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"RIFF")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(podcast_ai, "_run_ff", fake_ff)
    monkeypatch.setattr(podcast_ai.media, "probe_duration", lambda p: 100.0)
    return SimpleNamespace(source=str(source), ff_calls=ff_calls, folder=str(tmp_path / "pod"))


def test_transcribe_passes_lang_only_to_whisper(audio_env, monkeypatch):
    words = [_w("привет", 0.0, 0.5), _w("мир", 0.6, 1.0)]
    seen = []

    def fake_transcribe(wav, engine, emit=None, **opts):
        seen.append((engine, dict(opts)))
        return words

    monkeypatch.setattr(podcast_ai.asr_backends, "transcribe_words", fake_transcribe)
    monkeypatch.setattr(podcast_ai.aicut, "podcast_lang", lambda: "ru")

    # whisper: язык уходит
    monkeypatch.setattr(podcast_ai.aicut, "podcast_asr_engine", lambda emit=None: "whisper")
    proj = podcast.new_project("Выпуск", URL, audio_env.source, 2)
    podcast_ai.transcribe(audio_env.folder, proj)
    assert seen[-1][0] == "whisper"
    assert seen[-1][1].get("lang") == "ru"

    # GigaAM (CTC): языка в вызове быть не должно
    monkeypatch.setattr(podcast_ai.aicut, "podcast_asr_engine", lambda emit=None: "gigaam")
    proj = podcast.new_project("Выпуск", URL, audio_env.source, 2)
    podcast_ai.transcribe(audio_env.folder, proj)
    assert seen[-1][0] == "gigaam"
    assert "lang" not in seen[-1][1]


def test_transcribe_writes_words_and_project_fields(audio_env, monkeypatch):
    words = [_w("привет", 0.0, 0.5), _w("мир", 0.6, 1.0)]
    monkeypatch.setattr(podcast_ai.asr_backends, "transcribe_words",
                        lambda wav, engine, emit=None, **opts: words)
    monkeypatch.setattr(podcast_ai.aicut, "podcast_asr_engine", lambda emit=None: "whisper")
    monkeypatch.setattr(podcast_ai.aicut, "podcast_lang", lambda: None)
    proj = podcast.new_project("Выпуск", URL, audio_env.source, 2)

    out = podcast_ai.transcribe(audio_env.folder, proj)

    assert out == words
    assert podcast_ai.load_words(audio_env.folder) == words
    assert proj["asr_engine"] == "whisper"
    assert proj["lang"] == ""
    assert proj["duration"] == 100.0
    saved = podcast.load_project(audio_env.folder)
    assert saved["asr_engine"] == "whisper"
    assert os.path.isfile(os.path.join(audio_env.folder, "audio16k.wav"))


def test_transcribe_empty_words_raises(audio_env, monkeypatch):
    monkeypatch.setattr(podcast_ai.asr_backends, "transcribe_words",
                        lambda wav, engine, emit=None, **opts: [])
    monkeypatch.setattr(podcast_ai.aicut, "podcast_asr_engine", lambda emit=None: "whisper")
    monkeypatch.setattr(podcast_ai.aicut, "podcast_lang", lambda: None)
    proj = podcast.new_project("Выпуск", URL, audio_env.source, 2)
    with pytest.raises(ReelsiError) as ei:
        podcast_ai.transcribe(audio_env.folder, proj)
    assert ei.value.code == "podcast_no_words"
    assert not os.path.exists(os.path.join(audio_env.folder, "words.json"))


def test_extract_wav_reuses_fresh_file(audio_env):
    os.makedirs(audio_env.folder, exist_ok=True)
    out = os.path.join(audio_env.folder, "audio16k.wav")
    with open(out, "wb") as f:
        f.write(b"RIFF")
    # wav новее источника: ffmpeg не зовём
    os.utime(out, (os.path.getmtime(audio_env.source) + 10,) * 2)
    assert podcast_ai.extract_wav(audio_env.source, audio_env.folder) == out
    assert audio_env.ff_calls == []


def test_extract_wav_ffmpeg_failure_raises_and_keeps_no_tmp(audio_env, monkeypatch):
    def failing(cmd, cancel=None, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(b"half")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(podcast_ai, "_run_ff", failing)
    with pytest.raises(ReelsiError) as ei:
        podcast_ai.extract_wav(audio_env.source, audio_env.folder)
    assert ei.value.code == "podcast_wav_failed"
    assert not os.path.exists(os.path.join(audio_env.folder, "audio16k.wav"))
    assert not os.path.exists(os.path.join(audio_env.folder, "audio16k.tmp.wav"))


# --- ИИ-выбор ---

def _prepare_choose(folder, reels_wanted=2):
    os.makedirs(folder, exist_ok=True)
    fileio.atomic_json_dump(os.path.join(folder, "words.json"), _flow())
    proj = podcast.new_project("Выпуск", URL, os.path.join(folder, "src.mkv"), reels_wanted)
    proj["duration"] = 100.0
    proj["status"] = "transcribed"
    podcast.save_project(folder, proj)
    return proj


def test_choose_writes_ready_segments(tmp_path, monkeypatch):
    folder = str(tmp_path / "pod")
    proj = _prepare_choose(folder, 2)
    calls = []

    def fake_ask(system, user, schema, **kw):
        calls.append(user)
        return {"segments": [
            {"start": 1.0, "end": 8.0, "title": "Первый", "why": "крючок"},
            {"start": 12.0, "end": 20.0, "title": "Второй", "why": "вывод"},
        ]}

    monkeypatch.setattr(podcast_ai.aicut, "_ask_json", fake_ask)
    monkeypatch.setattr(podcast_ai.aicut.catalog, "caps", lambda *a, **k: {})

    warns = podcast_ai.choose(folder, proj)

    assert isinstance(warns, list)
    assert calls and calls[0].startswith("Фрагментов: 2\n")
    saved = podcast.load_project(folder)
    assert saved["status"] == "ready"
    assert len(saved["segments"]) == 2
    assert podcast.validate_segments(saved["segments"], saved["duration"]) == []
    assert all(s["reel"] == "" for s in saved["segments"])


def test_choose_context_too_small_raises_and_keeps_project(tmp_path, monkeypatch):
    folder = str(tmp_path / "pod")
    proj = _prepare_choose(folder, 2)
    before = open(podcast.project_path(folder), "rb").read()
    asked = []
    monkeypatch.setattr(podcast_ai.aicut, "_ask_json",
                        lambda *a, **k: asked.append(1) or {"segments": []})
    monkeypatch.setattr(podcast_ai.aicut.catalog, "caps", lambda *a, **k: {"ctx_limit": 10})

    with pytest.raises(ReelsiError) as ei:
        podcast_ai.choose(folder, proj)

    assert ei.value.code == "podcast_ctx"
    assert asked == []
    assert open(podcast.project_path(folder), "rb").read() == before


def test_fits_context_bounds():
    assert podcast_ai.fits_context("x" * 10_000, {}) is True
    # 600 символов ≈ 200 токенов: 200 < 0.8 * 200 неверно
    assert podcast_ai.fits_context("x" * 600, {"ctx_limit": 200}) is False
    assert podcast_ai.fits_context("x" * 300, {"ctx_limit": 1000}) is True


# --- настройки шага ---

def _save_cfg(extra):
    cfg = {"active": "Тест",
           "profiles": {"Тест": {"provider": "lmstudio",
                                 "base_url": "http://localhost:1234/v1",
                                 "api_key": "", "model": "тестовая"}}}
    cfg.update(extra)
    aicut.save_ai_config(cfg)


def test_podcast_asr_engine_unknown_falls_back_to_whisper():
    _save_cfg({"active_podcast_asr": "нет-такого-движка"})
    msgs = []
    assert aicut.podcast_asr_engine(lambda s, **kw: msgs.append(s)) == "whisper"
    assert msgs and "откат на whisper" in msgs[0]


def test_podcast_asr_engine_accepts_non_cut_engine():
    # подкасту не нужен cut=True: движок без права резать годится
    _save_cfg({"active_podcast_asr": "gigaam:v3_rnnt"})
    assert aicut.podcast_asr_engine() == "gigaam:v3_rnnt"


def test_podcast_asr_engine_default_is_whisper():
    _save_cfg({})
    assert aicut.podcast_asr_engine() == "whisper"


def test_podcast_lang_auto_and_code():
    _save_cfg({"podcast_lang": "auto"})
    assert aicut.podcast_lang() is None
    _save_cfg({"podcast_lang": "en"})
    assert aicut.podcast_lang() == "en"
    _save_cfg({})
    assert aicut.podcast_lang() is None


def test_step_podcast_reasoning_default():
    assert aicut.STEP_REASONING_DEFAULT["podcast"] == "medium"
    assert aicut.STEP_TITLES["podcast"] == "Подкаст"


def test_конец_куска_режется_по_длине_записи_без_слов():
    # Слов нет, поэтому границы к словам не прилипают. Модель дала конец 200 с, а запись 100 с:
    # кусок обязан закончиться на длине записи, а не выйти за неё.
    segs, _ = podcast_ai.snap_segments([_seg(10.0, 200.0)], [], 1, 100.0)
    assert len(segs) == 1
    assert segs[0]["end"] == 100


# --------------------------------------------------------------------------- #
# Настройки подкаста в ai_config (П4): движок расшифровки и язык
# --------------------------------------------------------------------------- #

def test_set_active_podcast_asr_accepts_non_cut_engine():
    """Подкаст берёт любой движок каталога: Whisper cut не помечен, а для не-русского нужен он."""
    from core.aicut import config_actions
    cfg: dict = {}
    config_actions.set_active_podcast_asr(cfg, {"name": "whisper:medium"})
    assert cfg["active_podcast_asr"] == "whisper:medium"
    config_actions.set_active_podcast_asr(cfg, {"name": "whisper"})   # голое имя — дефолт сервера
    assert cfg["active_podcast_asr"] == "whisper"


def test_set_active_podcast_asr_empty_name_means_whisper():
    from core.aicut import config_actions
    cfg: dict = {}
    config_actions.set_active_podcast_asr(cfg, {})
    assert cfg["active_podcast_asr"] == "whisper"


def test_set_active_podcast_asr_rejects_invented_engine():
    from core.aicut import config_actions
    with pytest.raises(ReelsiError) as exc:
        config_actions.set_active_podcast_asr({}, {"name": "выдуманный:движок"})
    assert exc.value.code == "invalid_podcast_asr"


def test_set_podcast_lang_accepts_auto_and_codes():
    from core.aicut import config_actions
    cfg: dict = {}
    for lang in ("auto", "ru", "en", "pt", "zh-HK"):
        config_actions.set_podcast_lang(cfg, {"lang": lang})
        assert cfg["podcast_lang"] == lang


@pytest.mark.parametrize("bad", ["xx;rm", "", "r", "toolongcode", "ru en", "ру", "en/../x"])
def test_set_podcast_lang_rejects_garbage(bad):
    from core.aicut import config_actions
    cfg: dict = {}
    with pytest.raises(ReelsiError) as exc:
        config_actions.set_podcast_lang(cfg, {"lang": bad})
    assert exc.value.code == "invalid_podcast_lang"
    assert "podcast_lang" not in cfg


def test_set_podcast_lang_non_string_is_rejected():
    from core.aicut import config_actions
    with pytest.raises(ReelsiError) as exc:
        config_actions.set_podcast_lang({}, {"lang": 5})
    assert exc.value.code == "invalid_podcast_lang"


def test_podcast_actions_are_registered():
    """Оба действия попали в таблицу диспетчера POST /api/ai_config."""
    from core.aicut import config_actions
    assert config_actions.ACTIONS["set_active_podcast_asr"] is config_actions.set_active_podcast_asr
    assert config_actions.ACTIONS["set_podcast_lang"] is config_actions.set_podcast_lang
