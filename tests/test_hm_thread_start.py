# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание HM, пункт 2: старт потока после захвата лока — под try/except.

`/api/omnicut_run`, `/api/run`, `/api/draft_render` (api/jobs.py) и
`/api/build_run` (api/build.py) занимали `JOB` и межпроцессный `job.lock`
через `job_start()`, а потом запускали поток БЕЗ защиты. `RuntimeError: can't
start new thread` (исчерпан лимит потоков ОС) выбрасывался наружу, но лок и
`JOB["running"]` оставались занятыми до перезапуска сервера: любой следующий
джоб получал «Уже выполняется» навсегда. Образец — `api/render.py:2002`.

Запуск:  python -m pytest tests -q
"""
import os
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

BOOM = "can't start new thread"


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def clean_state(tmp_path, monkeypatch):
    """Свой файл лока задач и чистое состояние JOB на входе и на выходе."""
    from api import _core

    monkeypatch.setattr(_core, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr(_core, "_JOB_LOCK_FH", None)
    with _core.LOCK:
        _core.JOB.update(running=False, cancel=False, failed=[], results=[])
    yield
    _core._cross_lock_release()
    with _core.LOCK:
        _core.JOB["running"] = False
        _core.JOB["cancel"] = False


@pytest.fixture
def no_threads(monkeypatch):
    """`threading.Thread.start` бросает ровно то, что бросает ОС при исчерпании потоков."""
    def boom(self):
        raise RuntimeError(BOOM)

    monkeypatch.setattr(threading.Thread, "start", boom)


def _clip(tmp_path):
    """Файл камеры/XML, который эндпоинты требуют существующим."""
    cam1 = tmp_path / "camera1"
    cam1.mkdir(exist_ok=True)
    (cam1 / "01.mp4").write_bytes(b"video")
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    return cam1, xml


def _released():
    """Лок и JOB свободны: следующий джоб обязан стартовать."""
    from api import _core

    assert _core._JOB_LOCK_FH is None, "межпроцессный лок остался занят"
    with _core.LOCK:
        assert _core.JOB["running"] is False, "JOB[\"running\"] остался занят"
    assert _core.job_start(kind="cut", label="проверка") is True, "лок не отпущен"
    _core.job_finish()


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 500, (f"{path}: поток не родился, а ответ {r.status_code}: "
                                  f"{r.get_json(silent=True)}")
    d = r.get_json()
    assert d and d.get("err") == "internal_error", d
    assert BOOM in (d.get("error") or ""), d
    return d


def test_omnicut_run_отпускает_лок(client, tmp_path, clean_state, no_threads):
    cam1, _xml = _clip(tmp_path)
    _post(client, "/api/omnicut_run",
          {"outdir": str(tmp_path / "out"), "camdirs": [str(cam1)], "pairs": [["01.mp4"]]})
    _released()


def test_run_отпускает_лок(client, tmp_path, clean_state, no_threads):
    cam1, _xml = _clip(tmp_path)
    _post(client, "/api/run",
          {"outdir": str(tmp_path / "out"), "camdirs": [str(cam1)], "pairs": [["01.mp4"]],
           "stages": {}})
    _released()


def test_draft_render_отпускает_лок(client, tmp_path, clean_state, no_threads):
    _cam1, xml = _clip(tmp_path)
    _post(client, "/api/draft_render", {"xml": str(xml)})
    _released()


def test_build_run_отпускает_лок(client, tmp_path, clean_state, no_threads):
    _cam1, xml = _clip(tmp_path)
    _post(client, "/api/build_run", {"jobs": [{"xml": str(xml)}], "mode": "separate"})
    _released()


def test_следующий_джоб_стартует_после_сорванного_старта(client, tmp_path, clean_state,
                                                         monkeypatch):
    """То же самое, но глазами пользователя: после сорванного старта нарезка
    запускается с первого нажатия, а не «Уже выполняется» до перезапуска сервера."""
    from api import _core

    cam1, _xml = _clip(tmp_path)
    calls = []

    def boom(self):
        raise RuntimeError(BOOM)

    monkeypatch.setattr(threading.Thread, "start", boom)
    body = {"outdir": str(tmp_path / "out"), "camdirs": [str(cam1)], "pairs": [["01.mp4"]]}
    assert client.post("/api/omnicut_run", json=body).status_code == 500

    monkeypatch.setattr(threading.Thread, "start",
                        lambda self: calls.append(self._target.__name__))
    r = client.post("/api/omnicut_run", json=body)
    assert r.status_code == 200 and r.get_json().get("ok") is True, r.get_json()
    assert calls == ["run_omnicut_job"], calls
    with _core.LOCK:
        assert _core.JOB["running"] is True
    _core.job_finish()
