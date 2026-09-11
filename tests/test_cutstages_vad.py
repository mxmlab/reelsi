# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты ветки «паузы по громкости»: построение opts для reelsi.py (задание GF).

ПОЧЕМУ этот тест существует:
Ветку нарезки по энергии звука (reelsi.py / VAD) перевели на серверное построение opts
из единого контракта ступеней cutstages.py вместо хардкода в JS (40-queue.js:371).
Тест проверяет:
- stages с одними паузами (dedupe=False) дают opts с subs=False и dedup=False
  (условие reelsi.py: no_subs and no_dedup -> Whisper не импортируется);
- dedupe=True даёт dedup=True (Whisper нужен для поиска дублей), subs=False, srt=False;
- pauses='off' даёт no_cut=True;
- дефолтные пороги совпадают с хардкодом в JS (model=large-v3, scale=50.4, vad_thresh=18,
  min_silence=0.30, pad=0.08, cam_return=2);
- переданный словарь thresholds переопределяет дефолты;
- эндпоинт /api/run принимает stages и строит opts на сервере;
- эндпоинт /api/run со старым телом (opts без stages) работает как раньше (регресс-тест);
- эндпоинт /api/run при передаче и stages, и opts отдаёт приоритет stages;
- эндпоинт /api/run без stages и без opts возвращает ошибку no_opts.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import cutstages  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# 1. Построение opts из ступеней (to_reelsi_opts)
# --------------------------------------------------------------------------- #

def test_vad_pauses_only_opts_skips_whisper():
    """Набор «только паузы по громкости» (dedupe=False):
    opts['subs']=False и opts['dedup']=False.
    В reelsi.py это даёт args.no_subs=True и args.no_dedup=True, поэтому
    условие `not (args.no_subs and args.no_dedup)` ложно — Whisper не импортируется."""
    stages = {"pauses": "loud", "dedupe": False}
    opts = cutstages.to_reelsi_opts(stages)

    assert opts["subs"] is False
    assert opts["srt"] is False
    assert opts["dedup"] is False
    assert opts["no_cut"] is False
    assert opts["ae"] is False
    assert opts["keep"] == "last"
    assert opts["aggressive"] is False
    assert opts["ai_yellow"] is False


def test_vad_dedupe_true_enables_dedup_whisper_while_subs_stay_off():
    """dedupe=True даёт dedup=True (Whisper поднимется для поиска дублей),
    но субтитры (subs и srt) ВСЕГДА остаются выключенными (их делает /api/gen_subs)."""
    stages = {"pauses": "loud", "dedupe": True}
    opts = cutstages.to_reelsi_opts(stages)

    assert opts["dedup"] is True
    assert opts["subs"] is False
    assert opts["srt"] is False
    assert opts["no_cut"] is False


def test_vad_pauses_off_sets_no_cut():
    """pauses='off' даёт no_cut=True (нарезка по громкости отключена)."""
    stages = {"pauses": "off", "dedupe": False}
    opts = cutstages.to_reelsi_opts(stages)

    assert opts["no_cut"] is True
    assert opts["subs"] is False
    assert opts["srt"] is False


def test_vad_thresholds_defaults_match_js_hardcode():
    """Дефолтные пороги обязаны поимённо совпадать с хардкодом в static/app/40-queue.js:371:
    model='large-v3', scale=50.4, vad_thresh=18, min_silence=0.30, pad=0.08, cam_return=2."""
    assert cutstages.DEFAULT_THRESHOLDS["model"] == "large-v3"
    assert cutstages.DEFAULT_THRESHOLDS["scale"] == 50.4
    assert cutstages.DEFAULT_THRESHOLDS["vad_thresh"] == 18
    assert cutstages.DEFAULT_THRESHOLDS["min_silence"] == 0.30
    assert cutstages.DEFAULT_THRESHOLDS["pad"] == 0.08
    assert cutstages.DEFAULT_THRESHOLDS["cam_return"] == 2

    # Проверяем, что to_reelsi_opts подставляет ровно эти дефолты при отсутствии overrides
    opts = cutstages.to_reelsi_opts({"pauses": "loud"})
    assert opts["model"] == "large-v3"
    assert opts["scale"] == 50.4
    assert opts["vad_thresh"] == 18
    assert opts["min_silence"] == 0.30
    assert opts["pad"] == 0.08
    assert opts["cam_return"] == 2


def test_vad_custom_thresholds_override_defaults():
    """Переданный словарь thresholds переопределяет указанные пороги, оставляя остальные по дефолту."""
    custom = {
        "vad_thresh": 24.0,
        "min_silence": 0.50,
        "scale": 70.0,
    }
    opts = cutstages.to_reelsi_opts({"pauses": "loud"}, thresholds=custom)
    assert opts["vad_thresh"] == 24.0
    assert opts["min_silence"] == 0.50
    assert opts["scale"] == 70.0
    # Не тронутые пороги остались дефолтными
    assert opts["model"] == "large-v3"
    assert opts["pad"] == 0.08
    assert opts["cam_return"] == 2


def test_vad_loud_pauses_only_exact_dict():
    """Полная сверка итогового словаря opts для набора «только паузы по громкости»."""
    opts = cutstages.to_reelsi_opts({"pauses": "loud", "dedupe": False, "draft": False})
    expected = {
        "subs": False,
        "srt": False,
        "no_cut": False,
        "dedup": False,
        "ae": False,
        "keep": "last",
        "aggressive": False,
        "ai_yellow": False,
        "model": "large-v3",
        "scale": 50.4,
        "vad_thresh": 18,
        "min_silence": 0.30,
        "pad": 0.08,
        "cam_return": 2,
    }
    assert opts == expected


# --------------------------------------------------------------------------- #
# 2. Тесты эндпоинта /api/run
# --------------------------------------------------------------------------- #

def test_api_run_with_stages_builds_server_opts(client, monkeypatch, tmp_path):
    """POST /api/run со stages строит opts на сервере через cutstages.to_reelsi_opts."""
    fake_cam = tmp_path / "cam1.mp4"
    fake_cam.write_text("dummy")

    captured_args = {}

    def fake_run_job(base, outdir, pairs, opts):
        captured_args["base"] = base
        captured_args["outdir"] = outdir
        captured_args["pairs"] = pairs
        captured_args["opts"] = opts

    monkeypatch.setattr("api.jobs.run_job", fake_run_job)
    monkeypatch.setattr("api.jobs.job_start", lambda **kw: True)

    def fake_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("threading.Thread.start", fake_thread_start)

    payload = {
        "base": str(tmp_path),
        "outdir": str(tmp_path / "out"),
        "camdirs": [str(tmp_path)],
        "pairs": [["cam1.mp4"]],
        "stages": {"pauses": "loud", "dedupe": False},
        "thresholds": {"vad_thresh": 20},
    }

    r = client.post("/api/run", json=payload, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}

    opts = captured_args.get("opts")
    assert opts is not None
    assert opts["subs"] is False
    assert opts["dedup"] is False
    assert opts["no_cut"] is False
    assert opts["vad_thresh"] == 20
    assert opts["model"] == "large-v3"


def test_api_run_legacy_opts_backward_compatibility(client, monkeypatch, tmp_path):
    """POST /api/run со старым телом (opts без stages) передаёт opts как есть (регресс)."""
    fake_cam = tmp_path / "cam1.mp4"
    fake_cam.write_text("dummy")

    captured_args = {}

    def fake_run_job(base, outdir, pairs, opts):
        captured_args["opts"] = opts

    monkeypatch.setattr("api.jobs.run_job", fake_run_job)
    monkeypatch.setattr("api.jobs.job_start", lambda **kw: True)

    def fake_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("threading.Thread.start", fake_thread_start)

    legacy_opts = {
        "subs": True,
        "no_cut": False,
        "aggressive": False,
        "srt": True,
        "ae": False,
        "dedup": True,
        "ai_yellow": False,
        "keep": "last",
        "model": "medium",
        "scale": "50.4",
        "vad_thresh": "15",
        "min_silence": "0.40",
        "pad": "0.10",
        "cam_return": 3,
    }
    payload = {
        "base": str(tmp_path),
        "outdir": str(tmp_path / "out"),
        "camdirs": [str(tmp_path)],
        "pairs": [["cam1.mp4"]],
        "opts": legacy_opts,
    }

    r = client.post("/api/run", json=payload, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert captured_args.get("opts") == legacy_opts


def test_api_run_stages_takes_precedence_over_opts(client, monkeypatch, tmp_path):
    """Если переданы и stages, и opts, сервер обязан строить opts по stages."""
    fake_cam = tmp_path / "cam1.mp4"
    fake_cam.write_text("dummy")

    captured_args = {}

    def fake_run_job(base, outdir, pairs, opts):
        captured_args["opts"] = opts

    monkeypatch.setattr("api.jobs.run_job", fake_run_job)
    monkeypatch.setattr("api.jobs.job_start", lambda **kw: True)

    def fake_thread_start(self):
        self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("threading.Thread.start", fake_thread_start)

    payload = {
        "base": str(tmp_path),
        "outdir": str(tmp_path / "out"),
        "camdirs": [str(tmp_path)],
        "pairs": [["cam1.mp4"]],
        "stages": {"pauses": "loud", "dedupe": False},
        "opts": {"subs": True, "dedup": True},  # старый мусор, должен игнорироваться
    }

    r = client.post("/api/run", json=payload, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    opts = captured_args.get("opts")
    assert opts["subs"] is False
    assert opts["dedup"] is False


def test_api_run_missing_opts_and_stages_returns_error(client, tmp_path):
    """Если в запросе нет ни stages, ни opts, возвращается ошибка no_opts."""
    fake_cam = tmp_path / "cam1.mp4"
    fake_cam.write_text("dummy")

    payload = {
        "base": str(tmp_path),
        "outdir": str(tmp_path / "out"),
        "camdirs": [str(tmp_path)],
        "pairs": [["cam1.mp4"]],
    }

    r = client.post("/api/run", json=payload, headers={"Host": "127.0.0.1:5001"})
    body = r.get_json()
    assert body.get("err") == "no_opts"
