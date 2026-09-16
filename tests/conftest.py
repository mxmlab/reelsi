# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие фикстуры и изоляция тестового окружения для pytest.

Автоматически изолирует файлы состояния (render_stats.json, ui_state.json,
job.lock, ai_calls.jsonl, models_dev.json, _videogen), чтобы тесты не писали
в боевые файлы рабочей копии.
"""
import os
import tempfile

import pytest

# Изоляция файлового лога сессии тестов ДО любых импортов проекта.
# Модули бэкенда (api.gdrive, api.render и др.) запрашивают логгер прямо на уровне модуля
# при импорте (get_logger("reelsi.*")). Фикстура pytest опоздает, и RotatingFileHandler
# базового логгера успеет привязаться к боевому reelsi.log в корне репозитория.
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="reelsi-tests-")
_TEST_LOG_FILE = os.path.join(_TEST_LOG_DIR, "reelsi.log")
os.environ["REELSI_LOG"] = _TEST_LOG_FILE
os.environ["AUTOCUT_LOG"] = _TEST_LOG_FILE



@pytest.fixture
def case_insensitive_fs(tmp_path):
    """Пропустить тест, если файловая система чувствительна к регистру (пробник на tmp_path).

    Пробник, а не sys.platform: на macOS ФС по умолчанию без учёта регистра.
    """
    probe = tmp_path / "Probe.txt"
    probe.write_text("probe", encoding="utf-8")
    is_insensitive = os.path.exists(tmp_path / "probe.txt")
    probe.unlink(missing_ok=True)
    if not is_insensitive:
        pytest.skip("ФС чувствительна к регистру — тест рассчитан на case-insensitive ФС")


@pytest.fixture(autouse=True)
def isolate_state_files(tmp_path, monkeypatch):
    """Изоляция файлов состояния на каждый тест во временный каталог tmp_path."""
    monkeypatch.setenv("REELSI_RENDER_STATS", str(tmp_path / "render_stats.json"))
    monkeypatch.setenv("REELSI_UI_STATE", str(tmp_path / "ui_state.json"))
    monkeypatch.setenv("REELSI_JOB_LOCK", str(tmp_path / "job.lock"))
    monkeypatch.setenv("REELSI_AI_LOG", str(tmp_path / "ai_calls.jsonl"))
    monkeypatch.setenv("REELSI_MODELS_DEV", str(tmp_path / "models_dev.json"))
    video_dir = tmp_path / "_videogen"
    monkeypatch.setenv("REELSI_VIDEO_DIR", str(video_dir))
    monkeypatch.setenv("REELSI_VIDEO_HISTORY", str(video_dir / "history.json"))


@pytest.fixture(autouse=True)
def reset_job_state():
    """Сбросить флаги отмены, статус running и состояние джобов между тестами.

    Тесты вызывают `POST /api/cancel`, хелперы тестов (например `test_render.py`
    `_preflight`) ставят `running=True`, или джобы падают без снятия флагов. В бою
    их сбрасывает старт следующей задачи, а в тестах между файлами — никто.
    Сбрасываем `cancel` и `running` у всех шести словарей джобов процесса
    (`JOB`, `RJOB`, `GDJOB`, `VJOB`, `PXJOB`, `ILL_JOB`), а также `_LOCAL.epoch`
    и флаг отмены в `core.aicut.llm`.
    """
    try:
        from core import applog
        applog.get_logger()
    except Exception:
        pass
    yield

    try:
        from core.aicut import llm
        llm._LOCAL.__dict__.pop("epoch", None)
        llm.clear_cancel()
    except Exception:
        pass
    try:
        from api import _core
        with _core.LOCK:
            _core.JOB["cancel"] = False
            _core.JOB["running"] = False
    except Exception:
        pass
    try:
        from api import render
        with render.RLOCK:
            render.RJOB["cancel"] = False
            render.RJOB["running"] = False
    except Exception:
        pass
    try:
        from api import gdrive
        with gdrive.GDLOCK:
            gdrive.GDJOB["cancel"] = False
            gdrive.GDJOB["running"] = False
    except Exception:
        pass
    try:
        from api import videogen
        with videogen.VLOCK:
            videogen.VJOB["cancel"] = False
            videogen.VJOB["running"] = False
    except Exception:
        pass
    try:
        from api import previewproxy
        with previewproxy.PXLOCK:
            previewproxy.PXJOB["running"] = False
    except Exception:
        pass
    try:
        from api import inserts
        with inserts.ILL_LOCK:
            inserts.ILL_JOB["running"] = False
    except Exception:
        pass
    try:
        from core import applog
        applog.get_logger()
    except Exception:
        pass

