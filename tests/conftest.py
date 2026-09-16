# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие фикстуры и изоляция тестового окружения для pytest.

Автоматически изолирует файлы состояния (render_stats.json, ui_state.json,
job.lock, ai_calls.jsonl, models_dev.json, _videogen), чтобы тесты не писали
в боевые файлы рабочей копии.
"""
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
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



import copy

from api import _core, gdrive, inserts, previewproxy, render, videogen
from core import app_meta, applog, insertlib
from core.aicut import llm

_INITIAL_JOB = copy.deepcopy(_core.JOB)
_INITIAL_RJOB = copy.deepcopy(render.RJOB)
_INITIAL_GDJOB = copy.deepcopy(gdrive.GDJOB)
_INITIAL_VJOB = copy.deepcopy(videogen.VJOB)
_INITIAL_PXJOB = copy.deepcopy(previewproxy.PXJOB)
_INITIAL_ILL_JOB = copy.deepcopy(inserts.ILL_JOB)
_INITIAL_INSERTLIB_CACHE = copy.deepcopy(insertlib._CACHE)

try:
    applog.get_logger()
except Exception:
    pass


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


def reset_all_job_state():
    """Сбросить флаги отмены, статус running и полное состояние джобов и кэшей в начальное состояние."""
    try:
        llm._LOCAL.__dict__.pop("epoch", None)
        llm.clear_cancel()
    except Exception:
        pass
    try:
        with _core.LOCK:
            _core.JOB.clear()
            _core.JOB.update(copy.deepcopy(_INITIAL_JOB))
    except Exception:
        pass
    try:
        with render.RLOCK:
            render.RJOB.clear()
            render.RJOB.update(copy.deepcopy(_INITIAL_RJOB))
    except Exception:
        pass
    try:
        with gdrive.GDLOCK:
            gdrive.GDJOB.clear()
            gdrive.GDJOB.update(copy.deepcopy(_INITIAL_GDJOB))
    except Exception:
        pass
    try:
        with videogen.VLOCK:
            videogen.VJOB.clear()
            videogen.VJOB.update(copy.deepcopy(_INITIAL_VJOB))
    except Exception:
        pass
    try:
        with previewproxy.PXLOCK:
            previewproxy.PXJOB.clear()
            previewproxy.PXJOB.update(copy.deepcopy(_INITIAL_PXJOB))
    except Exception:
        pass
    try:
        with inserts.ILL_LOCK:
            inserts.ILL_JOB.clear()
            inserts.ILL_JOB.update(copy.deepcopy(_INITIAL_ILL_JOB))
    except Exception:
        pass
    try:
        app_meta._UI_LANG_CACHED = None
    except Exception:
        pass
    try:
        insertlib._CACHE.clear()
        insertlib._CACHE.update(copy.deepcopy(_INITIAL_INSERTLIB_CACHE))
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_job_state():
    """Сбросить состояние джобов и кэшей между тестами."""
    yield
    reset_all_job_state()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Переармирование логгера ПОСЛЕ завершения всех фикстур теста (включая monkeypatch)."""
    yield
    try:
        applog.get_logger()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    """Убрать временный каталог с логом тестов после завершения всей сессии.

    На Windows открытый файл невозможно удалить (PermissionError). Поэтому
    сначала закрываем и снимаем с логгера reelsi все RotatingFileHandler, чьи файлы
    лежат внутри _TEST_LOG_DIR, затем удаляем каталог через shutil.rmtree.
    """
    logger = logging.getLogger("reelsi")
    norm_test_dir = os.path.normcase(os.path.realpath(_TEST_LOG_DIR))
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler):
            bf = getattr(h, "baseFilename", None)
            if bf:
                norm_bf = os.path.normcase(os.path.realpath(bf))
                try:
                    is_inside = os.path.commonpath([norm_test_dir, norm_bf]) == norm_test_dir
                except ValueError:
                    is_inside = False
                if is_inside:
                    try:
                        h.close()
                    except Exception:
                        pass
                    logger.removeHandler(h)
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


