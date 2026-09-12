# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт C: рендер занимает ОБЩИЙ лок задач.

`/api/render_run` смотрел только на `RJOB["running"]` и не брал ни `JOB`, ни
межпроцессный `job.lock`: рендер стартовал поверх идущей нарезки или сборки — две
тяжёлые задачи на одной видеокарте, да ещё обе пишут `<outdir>/<stem>.jsx`.

Лок тот же, что у `job_start` (нарезка, сборка). Проверяем обе стороны: занято —
отказ; рендер кончился (успех / ошибка / стоп) — лок свободен.

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


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def lock_state(tmp_path, monkeypatch):
    """Свой файл лока задач и чистое состояние JOB/RJOB на входе и на выходе."""
    from api import _core, render

    monkeypatch.setattr(_core, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr(_core, "_JOB_LOCK_FH", None)
    with _core.LOCK:
        _core.JOB.update(running=False, cancel=False, failed=[], results=[])
    with render.RLOCK:
        render.RJOB.update(running=False, done=False, cancel=False, result=[], failed=[],
                           items=[])
    yield
    _core._cross_lock_release()
    with _core.LOCK:
        _core.JOB["running"] = False
    with render.RLOCK:
        render.RJOB.update(running=False, done=False, cancel=False)


def _post_render(client, tmp_path, jobs=1):
    return client.post("/api/render_run", json={
        "jobs": [{"xml_path": str(tmp_path / f"clip{i}.xml")} for i in range(jobs)],
        "render_dir": str(tmp_path / "out")})


def test_рендер_при_занятой_задаче_отказ(client, tmp_path, monkeypatch, lock_state):
    """Идёт нарезка (или сборка) — рендер не стартует: отказ тот же, что у сборки."""
    from api import _core, render

    monkeypatch.setattr(render, "_run_render_job", lambda *a: None)   # поток не должен родиться
    assert _core.job_start(kind="cut", label="Нарезка") is True
    try:
        r = _post_render(client, tmp_path)
    finally:
        _core.job_finish()

    d = r.get_json()
    assert d.get("err") == "busy_wait", d
    assert render.RJOB["running"] is False


def test_рендер_при_занятом_межпроцессном_локе_отказ(client, tmp_path, monkeypatch,
                                                      lock_state):
    """Лок держит соседняя копия интерфейса (второй webui) — тоже отказ."""
    from api import render

    monkeypatch.setattr(render, "_cross_lock_acquire", lambda: False)
    monkeypatch.setattr(render, "_run_render_job", lambda *a: None)

    d = _post_render(client, tmp_path).get_json()

    assert d.get("err") == "busy_wait", d
    assert render.RJOB["running"] is False


def test_эндпоинт_занимает_лок_на_время_рендера(client, tmp_path, monkeypatch,
                                                lock_state):
    """Пока рендер идёт, лок занят — нарезка и сборка в него не пролезут."""
    from api import _core, render

    started = threading.Event()
    seen = {}

    def fake_run(jobs, outdir, render_dir):
        seen["lock"] = _core._JOB_LOCK_FH is not None
        started.set()

    monkeypatch.setattr(render, "_run_render_job", fake_run)
    _post_render(client, tmp_path)
    assert started.wait(timeout=10), "рендер не стартовал"

    assert seen["lock"] is True, "лок задач не занят во время рендера"
    assert _core._JOB_LOCK_FH is not None
    with _core.LOCK:
        assert _core.JOB["running"] is False
    # соседний джоб в этом же процессе лок уже не возьмёт
    assert _core._cross_lock_acquire() is False
    with render.RLOCK:
        render.RJOB["running"] = False       # поток подменён — диспетчер сам не сбросит


def test_после_рендера_лок_свободен(tmp_path, monkeypatch, lock_state):
    """Три исхода рендера (нормальный выход / ошибка / «Стоп») — во всех лок отпущен."""
    from api import _core, render
    from api import build as build_mod

    def fake_norm_ok(jobs):
        assert _core._JOB_LOCK_FH is not None, "лок задач не занят во время рендера"
        return [{"xml_path": str(tmp_path / "clip.xml")}]

    def fake_norm_empty(jobs):
        return []

    def fake_norm_boom(jobs):
        assert _core._JOB_LOCK_FH is not None, "лок задач не занят во время рендера"
        raise ValueError("xml не найден")

    monkeypatch.setattr(build_mod, "_norm_build_jobs", fake_norm_ok)
    monkeypatch.setattr(render, "_run_render_single", lambda *a: None)
    assert _core._cross_lock_acquire() is True         # так же, как эндпоинт
    render._run_render_job([{"xml_path": "clip.xml"}], "", str(tmp_path))
    assert _core._JOB_LOCK_FH is None, "лок остался занят после рендера"
    with render.RLOCK:
        assert render.RJOB["running"] is False

    monkeypatch.setattr(build_mod, "_norm_build_jobs", fake_norm_boom)
    assert _core._cross_lock_acquire() is True
    render._run_render_job([{"xml_path": "clip.xml"}], "", str(tmp_path))
    assert _core._JOB_LOCK_FH is None, "лок остался занят после ошибки рендера"

    monkeypatch.setattr(build_mod, "_norm_build_jobs", fake_norm_empty)
    assert _core._cross_lock_acquire() is True
    render._run_render_job([{"xml_path": "clip.xml"}], "", str(tmp_path))
    assert _core._JOB_LOCK_FH is None, "лок остался занят после «Набор пуст»"

    # «Стоп» на наборе из двух файлов: до работы не доходит, диспетчер выходит сам
    monkeypatch.setattr(build_mod, "_norm_build_jobs",
                        lambda jobs: [{"xml_path": str(tmp_path / "a.xml")},
                                      {"xml_path": str(tmp_path / "b.xml")}])
    with render.RLOCK:
        render.RJOB["cancel"] = True
    assert _core._cross_lock_acquire() is True
    render._run_render_job([{"xml_path": "a.xml"}], "", str(tmp_path))
    assert _core._JOB_LOCK_FH is None, "лок остался занят после «Стоп»"
    with render.RLOCK:
        render.RJOB["cancel"] = False
