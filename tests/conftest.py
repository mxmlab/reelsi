# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие фикстуры и изоляция тестового окружения для pytest.

Автоматически изолирует файлы состояния (render_stats.json, ui_state.json,
job.lock, ai_calls.jsonl, models_dev.json, _videogen), чтобы тесты не писали
в боевые файлы рабочей копии.
"""
import os

import pytest


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
def clear_cancel_flags():
    """Убрать за `POST /api/cancel`: он ставит ОБЩИЕ на процесс флаги отмены, а снимает
    их в бою только старт следующей задачи (job_start и api_*_run), — в тестах же
    между файлами не снимает никто. Поймано прогоном HU (2026-09-15):

    * `JOB["cancel"]` — `run_omnicut_job` выходит по нему ДО запуска процесса, и
      `test_pipeline_stages::test_run_omnicut_job_builds_correct_cli_flags` падал
      IndexError'ом после `test_api_security` (`pytest tests/test_api_security.py
      tests/test_pipeline_stages.py` — падение воспроизводится и без правок HU);
    * `GDJOB["cancel"]` — `test_gdrive::test_упавший_rclone_не_считается_успехом`
      видел чужую отмену и вместо кода возврата получал «скачивание остановлено».

    Флаг `aicut.CANCEL` тут же: раньше его снимала своя фикстура в test_api_security.
    """
    yield
    try:
        from core import aicut
        aicut.clear_cancel()
    except Exception:
        pass
    from api import _core, gdrive, render, videogen
    with _core.LOCK:
        _core.JOB["cancel"] = False
    with render.RLOCK:
        render.RJOB["cancel"] = False
    with gdrive.GDLOCK:
        gdrive.GDJOB["cancel"] = False
    with videogen.VLOCK:
        videogen.VJOB["cancel"] = False
