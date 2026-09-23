# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание HP: тесты на откат потоков, атомарность AI_ACTIVE и лок превью-прокси.

Запуск:  python -m pytest tests/test_hp_concurrency.py -q
"""
import os
import sys
import threading
import time
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
    """Свой файл лока задач и чистое состояние джобов."""
    from api import _core
    from api.render import RJOB, RLOCK
    from api.videogen import VJOB, VLOCK
    from api.gdrive import GDJOB, GDLOCK
    from api.inserts import ILL_JOB
    from api.previewproxy import PXJOB, PXLOCK

    monkeypatch.setattr(_core, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr(_core, "_JOB_LOCK_FH", None)
    with _core.LOCK:
        _core.JOB.update(running=False, cancel=False, failed=[], results=[])
        _core.AI_ACTIVE = 0
    with RLOCK:
        RJOB.update(running=False, done=False, log=[])
    with VLOCK:
        VJOB.update(running=False, done=False, log=[])
    with GDLOCK:
        GDJOB.update(running=False, done=False, log=[])
    ILL_JOB.update(running=False, done=0, total=0, log=[], error="")
    with PXLOCK:
        PXJOB.update(running=False, done=False, log=[], i=0, n=0, cur="")

    yield

    _core._cross_lock_release()
    with _core.LOCK:
        _core.JOB["running"] = False
        _core.AI_ACTIVE = 0
    with RLOCK:
        RJOB["running"] = False
    with VLOCK:
        VJOB["running"] = False
    with GDLOCK:
        GDJOB["running"] = False
    ILL_JOB["running"] = False
    with PXLOCK:
        PXJOB["running"] = False


@pytest.fixture
def no_threads(monkeypatch):
    """threading.Thread.start бросает исключение при исчерпании лимита потоков."""
    def boom(self):
        raise RuntimeError(BOOM)

    monkeypatch.setattr(threading.Thread, "start", boom)


# =========================================================================== #
# 3. Старт потока без полного отката: render / inserts / videogen / gdrive
# =========================================================================== #

def test_render_run_thread_fail_resets_running(client, tmp_path, clean_state, no_threads):
    """api/render.py: при сбое старта потока RJOB['running'] сбрасывается и лок отпускается."""
    from api import _core
    from api.render import RJOB, RLOCK

    render_dir = tmp_path / "renders"
    # Файл набора обязан существовать: api_render_run нормализует набор ДО старта
    # потока и на пропаже отвечает внятной ошибкой, не доходя до рождения потока.
    xml = tmp_path / "clip.xml"
    xml.write_text("<xml/>", encoding="utf-8")
    body = {"jobs": [{"xml": str(xml)}], "render_dir": str(render_dir)}

    r = client.post("/api/render_run", json=body)
    assert r.status_code == 500
    with RLOCK:
        assert RJOB["running"] is False, "RJOB['running'] остался True после сбоя старта"
    assert _core._JOB_LOCK_FH is None, "межпроцессный лок остался занят"


def test_inserts_describe_thread_fail_resets_running(client, clean_state, no_threads):
    """api/inserts.py: при сбое старта потока ILL_JOB['running'] сбрасывается."""
    from api.inserts import ILL_JOB

    r = client.post("/api/insertlib_describe", json={"only_missing": True})
    assert r.status_code == 500
    assert ILL_JOB["running"] is False, "ILL_JOB['running'] остался True после сбоя старта"


def test_inserts_describe_double_start_busy(client, clean_state):
    """api/inserts.py: повторный запуск при работающем возвращает 'Описание уже идёт'."""
    from api.inserts import ILL_JOB

    ILL_JOB["running"] = True
    r = client.post("/api/insertlib_describe", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("err") == "describe_busy"


def test_videogen_thread_fail_resets_running(client, clean_state, no_threads, monkeypatch):
    """api/videogen.py: при сбое старта потока VJOB['running'] сбрасывается."""
    from api.videogen import VJOB, VLOCK
    from core import aicut

    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {"name": "test", "provider": "openrouter"})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "test-model")
    monkeypatch.setattr(aicut, "ensure_video_catalog", lambda prof: None)
    monkeypatch.setattr(aicut, "video_check", lambda *a, **k: [])
    monkeypatch.setattr(aicut, "video_insert_duration", lambda d, m: 5)
    monkeypatch.setattr(aicut, "video_resolution_cfg", lambda m: "720p")
    monkeypatch.setattr(aicut, "video_auto_aspect", lambda *a, **k: "9:16")

    body = {"prompt": "cinematic cat", "duration": 5}
    r = client.post("/api/video_gen", json=body)
    assert r.status_code == 500
    with VLOCK:
        assert VJOB["running"] is False, "VJOB['running'] остался True после сбоя старта"


def test_gdrive_download_thread_fail_resets_running(client, tmp_path, clean_state, no_threads, monkeypatch):
    """api/gdrive.py: при сбое старта потока GDJOB['running'] сбрасывается."""
    import shutil
    from api.gdrive import GDJOB, GDLOCK
    from api import gdrive

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/rclone")
    monkeypatch.setattr(gdrive, "rclone_remote", lambda: "gdrive:")
    monkeypatch.setattr(gdrive, "rclone_cmd", lambda *a: ["rclone", "version"])

    body = {"url": "https://drive.google.com/file/d/1234567890abcdef/view", "dest": str(tmp_path / "downloads")}
    r = client.post("/api/gdrive_download", json=body)
    assert r.status_code == 500
    with GDLOCK:
        assert GDJOB["running"] is False, "GDJOB['running'] остался True после сбоя старта"


# =========================================================================== #
# 4. AI_ACTIVE: атомарность проверки и увеличения
# =========================================================================== #

def test_ai_begin_atomic_concurrency(clean_state, monkeypatch):
    """_ai_begin: проверка AI_ACTIVE == 0 и инкремент под LOCK.
    Два одновременных вызова при AI_ACTIVE == 0: ровно один проходит сразу,
    второй ожидает освобождения первого."""
    from api import _core
    from core import aicut

    epochs = [101, 102]
    barrier = threading.Barrier(2)

    def mock_begin_call():
        ep = epochs.pop(0)
        barrier.wait(timeout=2)
        return ep

    monkeypatch.setattr(aicut, "begin_call", mock_begin_call)
    monkeypatch.setattr(_core, "AI_WAIT_SEC", 2)

    real_lock = _core.LOCK
    class SlowLock:
        def __enter__(self):
            time.sleep(0.05)
            return real_lock.__enter__()
        def __exit__(self, *args):
            return real_lock.__exit__(*args)
    monkeypatch.setattr(_core, "LOCK", SlowLock())

    passed = []
    order = []

    def caller(id_):
        ep = _core._ai_begin(label=f"worker_{id_}")
        passed.append(id_)
        order.append((id_, time.time()))
        # имитируем работу ИИ вызова
        time.sleep(0.4)
        _core._ai_end(ep)

    t1 = threading.Thread(target=caller, args=(1,))
    t2 = threading.Thread(target=caller, args=(2,))

    t1.start()
    t2.start()

    t1.join(timeout=3)
    t2.join(timeout=3)

    assert len(passed) == 2
    # Разница во времени между проходами должна быть не менее 0.35 с (первый держал вызов)
    time_diff = abs(order[1][1] - order[0][1])
    assert time_diff >= 0.35, f"Оба потока вошли одновременно (diff={time_diff:.3f}s)!"


# =========================================================================== #
# 5. Превью-прокси: лок GPU при сборке
# =========================================================================== #

def test_preview_proxy_gpu_lock_busy(client, tmp_path, clean_state, monkeypatch):
    """api/previewproxy.py: когда лок GPU занят, сборка не стартует и возвращается 'busy'."""
    from api import _core, previewproxy

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    # Имитируем план с несозданным прокси
    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h: ([("/path/cam1.mp4", "/path/pv.mp4", False)], str(tmp_path)))

    # Занимаем кросс-лок заранее
    assert _core._cross_lock_acquire() is True

    r = client.post("/api/preview_proxy", json={"xml": str(xml), "build": True})
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("err") == "busy"
    with previewproxy.PXLOCK:
        assert previewproxy.PXJOB["running"] is False


def test_preview_proxy_gpu_lock_acquired_and_released(client, tmp_path, clean_state, monkeypatch):
    """api/previewproxy.py: свободный лок захватывается и отпускается после сборки."""
    from api import _core, previewproxy
    from core import draftrender

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h: ([("/path/cam1.mp4", "/path/pv.mp4", False)], str(tmp_path)))

    built = []
    lock_held_during_build = []

    def mock_build(src, dst, height=720, emit=None, progress=None):
        built.append((src, dst))
        # Проверяем, что лок занят (повторный acquire возвращает False)
        lock_held_during_build.append(_core._JOB_LOCK_FH is not None)

    monkeypatch.setattr(draftrender, "build_preview_proxy", mock_build)

    r = client.post("/api/preview_proxy", json={"xml": str(xml), "build": True})
    assert r.status_code == 200
    assert r.get_json().get("ok") is True

    # Ждём завершения потока сборщика
    for _ in range(50):
        with previewproxy.PXLOCK:
            if not previewproxy.PXJOB["running"]:
                break
        time.sleep(0.05)

    assert len(built) == 1
    assert lock_held_during_build == [True]
    # После завершения лок освобождён
    assert _core._JOB_LOCK_FH is None


def test_preview_proxy_no_build_does_not_acquire_lock(client, tmp_path, clean_state, monkeypatch):
    """api/previewproxy.py: запрос без build=True не берёт лок GPU."""
    from api import _core, previewproxy

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h: ([("/path/cam1.mp4", "/path/pv.mp4", False)], str(tmp_path)))

    acquire_called = []
    orig_acquire = _core._cross_lock_acquire

    def tracking_acquire():
        acquire_called.append(True)
        return orig_acquire()

    monkeypatch.setattr(_core, "_cross_lock_acquire", tracking_acquire)

    r = client.post("/api/preview_proxy", json={"xml": str(xml), "build": False})
    assert r.status_code == 200
    assert r.get_json().get("ok") is True
    assert len(acquire_called) == 0, "_cross_lock_acquire был вызван при build=False!"


def test_ai_begin_timeout_behavior(clean_state, monkeypatch):
    """_ai_begin: при таймауте AI_WAIT_SEC печатает предупреждение и стартует поверх."""
    from api import _core
    from core import aicut

    monkeypatch.setattr(aicut, "begin_call", lambda: 999)
    monkeypatch.setattr(_core, "AI_WAIT_SEC", 0.05)

    emits = []
    monkeypatch.setattr(_core, "emit", lambda *a, **k: emits.append((a, k)))

    with _core.LOCK:
        _core.AI_ACTIVE = 1

    ep = _core._ai_begin(label="timeout_test")
    assert ep == 999
    with _core.LOCK:
        assert _core.AI_ACTIVE == 2
    assert any("не отпустил провайдера" in str(a) for a, _ in emits)
    _core._ai_end(ep)


def test_preview_proxy_thread_fail_releases_gpu_lock(client, tmp_path, clean_state, no_threads, monkeypatch):
    """api/previewproxy.py: сбой старта потока отпускает лок GPU и сбрасывает running."""
    from api import _core, previewproxy

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h: ([("/path/cam1.mp4", "/path/pv.mp4", False)], str(tmp_path)))

    r = client.post("/api/preview_proxy", json={"xml": str(xml), "build": True})
    assert r.status_code == 500
    with previewproxy.PXLOCK:
        assert previewproxy.PXJOB["running"] is False
    assert _core._JOB_LOCK_FH is None, "межпроцессный лок GPU остался занят при сбое старта"
