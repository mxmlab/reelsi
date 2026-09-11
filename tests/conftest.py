# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общие фикстуры и изоляция тестового окружения для pytest.

Автоматически изолирует файлы состояния (render_stats.json, ui_state.json,
job.lock, ai_calls.jsonl, models_dev.json, _videogen), чтобы тесты не писали
в боевые файлы рабочей копии.
"""
import pytest


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
