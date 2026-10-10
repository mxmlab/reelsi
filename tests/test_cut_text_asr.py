# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Второй движок «на текст» при нарезке: настройка, второй проход и встройка в пайплайн.

ПОЧЕМУ этот тест существует:
- Whisper правит НАПИСАНИЕ слов CTC, тайминги остаются CTC. Тест фиксирует, что слова
  для нарезки приходят в форме CTC (нижний регистр, без пунктуации), а субтитры — как у
  Whisper (с регистром и пунктуацией).
- Если второй движок падает, нарезка не должна падать: текст остаётся от CTC, а модель
  обязательно выгружается (на Windows занятая VRAM вешает машину).
- Субтитры пишутся в XML сразу при нарезке, но только когда настройка включена.
"""
import contextlib
import json
import os

import pytest

from core import aicut
from core import transcribe as transcribe_mod
from core import terms
from core import xml2ae
from core.aicut import config_actions
from core.gigaam_cut import pipeline
from core.gigaam_cut import textpass
from core.umsg import ReelsiError


def _cfg_file(tmp_path, monkeypatch, data):
    path = tmp_path / "ai_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("core.aicut.config.AI_CONFIG_PATH", str(path))
    return path


def _collect_emit(sink):
    def emit(line="", /, **kw):
        sink.append(line.format(**kw) if kw else line)
    return emit


# --------------------------------------------------------------------------- #
# 1. cut_text_asr_engine
# --------------------------------------------------------------------------- #

def test_cut_text_asr_engine_default_off(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch, {"profiles": {"LM": {}}})
    assert aicut.cut_text_asr_engine() == ""


def test_cut_text_asr_engine_whisper_turbo_on(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch, {"profiles": {"LM": {}}, "active_cut_text_asr": "whisper:large-v3-turbo"})
    assert aicut.cut_text_asr_engine() == "whisper:large-v3-turbo"


@pytest.mark.parametrize("engine", ["gigaam", "нет_такого"])
def test_cut_text_asr_engine_bad_value_off_with_warning(tmp_path, monkeypatch, engine):
    _cfg_file(tmp_path, monkeypatch, {"profiles": {"LM": {}}, "active_cut_text_asr": engine})
    seen = []
    assert aicut.cut_text_asr_engine(emit=_collect_emit(seen)) == ""
    assert len(seen) == 1
    assert "второй проход выключен" in seen[0]
    assert engine in seen[0]


# --------------------------------------------------------------------------- #
# 2. set_active_cut_text_asr (действие /api/ai_config)
# --------------------------------------------------------------------------- #

def test_set_active_cut_text_asr_empty_switches_off():
    cfg = {"active_cut_text_asr": "whisper:large-v3"}
    config_actions.set_active_cut_text_asr(cfg, {"name": ""})
    assert cfg["active_cut_text_asr"] == ""


def test_set_active_cut_text_asr_saves_whisper():
    cfg: dict = {}
    config_actions.set_active_cut_text_asr(cfg, {"name": "whisper:large-v3"})
    assert cfg["active_cut_text_asr"] == "whisper:large-v3"


def test_set_active_cut_text_asr_rejects_ctc():
    with pytest.raises(ReelsiError) as exc:
        config_actions.set_active_cut_text_asr({}, {"name": "gigaam"})
    assert exc.value.code == "invalid_cut_text_asr"


# --------------------------------------------------------------------------- #
# 3–4. text_pass
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_gpu(monkeypatch):
    """Без замка GPU и без словаря; счётчик выгрузок модели."""
    released = []
    monkeypatch.setattr(textpass, "gpu_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(terms, "fix_words", lambda words, emit=None: list(words))
    monkeypatch.setattr(transcribe_mod, "release_model", lambda: released.append(1) or True)
    return released


def test_text_pass_fixes_text_keeps_ctc_timing(monkeypatch, no_gpu):
    ctc = [{"w": "кус", "start": 1.0, "end": 1.3}]
    whisper = [{"w": "Курс,", "start": 1.02, "end": 1.33}]
    monkeypatch.setattr(transcribe_mod, "transcribe", lambda *a, **k: whisper)

    cut, sub = textpass.text_pass("x.wav", ctc, "whisper:large-v3-turbo", lambda *a, **k: None)

    assert cut[0]["w"] == "курс"
    assert cut[0]["start"] == 1.0 and cut[0]["end"] == 1.3
    assert sub is not None
    assert sub[0]["w"] == "Курс,"
    assert len(no_gpu) == 1


def test_text_pass_failure_returns_ctc_and_releases_model(monkeypatch, no_gpu):
    ctc = [{"w": "кус", "start": 1.0, "end": 1.3}]

    def boom(*a, **k):
        raise RuntimeError("VRAM")

    monkeypatch.setattr(transcribe_mod, "transcribe", boom)
    seen = []

    cut, sub = textpass.text_pass("x.wav", ctc, "whisper:large-v3-turbo", _collect_emit(seen))

    assert cut is ctc
    assert sub is None
    assert len(no_gpu) == 1
    assert any("не отработал" in s for s in seen)


# --------------------------------------------------------------------------- #
# 5. Пайплайн: субтитры сразу в XML, только когда настройка включена
# --------------------------------------------------------------------------- #

@pytest.fixture
def pipe(monkeypatch, tmp_path):
    """Пайплайн без GPU, без LM Studio и без настоящего XML: build запоминает sub_words."""
    monkeypatch.setattr(pipeline.aicut, "unload_ours", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.aicut, "warn_foreign_models", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.draftrender, "clean_tmp", lambda *a, **k: None)
    monkeypatch.setattr(xml2ae, "write_srt_for", lambda *a, **k: None)

    built = []

    def fake_build(cams, keep, offsets, out, **kw):
        built.append(kw.get("sub_words"))
        return {"total_s": 3.0, "segments": len(keep)}

    monkeypatch.setattr(pipeline.xmlbuild, "build", fake_build)

    ctc_words = [
        {"w": "первый", "start": 0.0, "end": 1.0, "prob": 0.99, "space_before": False},
        {"w": "второй", "start": 1.0, "end": 2.0, "prob": 0.99, "space_before": True},
        {"w": "третий", "start": 2.0, "end": 3.0, "prob": 0.99, "space_before": True},
    ]
    text = " ".join(w["w"] for w in ctc_words)
    monkeypatch.setattr(pipeline, "transcribe_words_whole", lambda *a, **k: (text, ctc_words))
    monkeypatch.setattr(pipeline, "decide_markup", lambda *a, **k: (set(range(3)), set(), "", []))

    monkeypatch.setattr(pipeline, "_silence_bounds", lambda *a, **k: [])
    stages = {"pauses": "speech", "sense": False, "dedupe": False,
              "refine": False, "breath": False, "draft": False}
    out = str(tmp_path / "out.xml")

    def run():
        pipeline.run("dummy.wav", ["cam1.mp4"], [0.0], out, 50.4,
                     stages=stages, emit=lambda *a, **k: None)

    return {"run": run, "built": built, "out": out, "dir": tmp_path}


def test_pipeline_text_engine_off_writes_no_sidecar(monkeypatch, pipe):
    monkeypatch.setattr(pipeline.aicut, "cut_text_asr_engine", lambda *a, **k: "")
    pipe["run"]()
    assert pipe["built"] == [None]
    assert not os.path.exists(os.path.splitext(pipe["out"])[0] + ".words.json")


def test_pipeline_text_engine_on_writes_subs_and_sidecar(monkeypatch, pipe):
    monkeypatch.setattr(pipeline.aicut, "cut_text_asr_engine", lambda *a, **k: "whisper:large-v3-turbo")
    sub_src = [
        {"w": "Первый,", "start": 0.0, "end": 1.0},
        {"w": "Второй.", "start": 1.0, "end": 2.0},
        {"w": "Третий", "start": 2.0, "end": 3.0},
    ]
    cut_words = [dict(w, w=w["w"].lower().strip(".,")) for w in sub_src]
    calls = []

    def fake_text_pass(wav, words, engine, emit):
        calls.append(engine)
        return cut_words, sub_src

    monkeypatch.setattr(textpass, "text_pass", fake_text_pass)

    pipe["run"]()

    assert calls == ["whisper:large-v3-turbo"]
    sub_words = pipe["built"][0]
    assert isinstance(sub_words, list) and sub_words
    assert all(isinstance(x["start"], int) for x in sub_words)   # кадры при 60 fps
    side_path = os.path.splitext(pipe["out"])[0] + ".words.json"
    assert os.path.isfile(side_path)
    with open(side_path, encoding="utf-8") as f:
        side = json.load(f)
    assert [x["w"] for x in side] == ["Первый,", "Второй.", "Третий"]
    assert side[0]["start"] == 0.0 and side[-1]["end"] == 3.0
