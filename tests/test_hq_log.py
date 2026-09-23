# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: лог в файл, atexit-уборка процессов, логирование исключений."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_get_logger_writes_to_reelsi_log(tmp_path, monkeypatch):
    """get_logger пишет в REELSI_LOG и формат соответствует «время уровень модуль: сообщение»."""
    from core.applog import get_logger

    log_file = tmp_path / "custom_reelsi.log"
    monkeypatch.setenv("REELSI_LOG", str(log_file))

    logger_name = "reelsi.test_write"
    log = get_logger(logger_name)

    # Файл создаётся лениво при первой записи (delay=True)
    assert not log_file.exists(), "Файл лога не должен создаваться до первой записи"

    log.info("Тестовое сообщение 42")
    # Закроем/сбросим хэндлеры на базовом логгере для надёжного чтения на Windows
    for h in logging.getLogger("reelsi").handlers:
        h.flush()

    assert log_file.exists(), "Файл лога должен быть создан после записи"
    content = log_file.read_text(encoding="utf-8")
    assert "INFO" in content
    assert "reelsi.test_write:" in content
    assert "Тестовое сообщение 42" in content


def test_get_logger_idempotent(tmp_path, monkeypatch):
    """Повторные вызовы get_logger не дублируют RotatingFileHandler на базовом логгере."""
    from core.applog import get_logger

    log_file = tmp_path / "idempotent.log"
    monkeypatch.setenv("REELSI_LOG", str(log_file))

    logger_name = "reelsi.test_idempotent"
    log1 = get_logger(logger_name)
    base = logging.getLogger("reelsi")
    rot_handlers_1 = [h for h in base.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rot_handlers_1) == 1

    log2 = get_logger(logger_name)
    rot_handlers_2 = [h for h in base.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rot_handlers_2) == 1
    assert log1 is log2

    log2.info("Однократная запись")
    for h in base.handlers:
        h.flush()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_atexit_cleanup_calls_both_killers(monkeypatch):
    """_cleanup_on_exit вызывает api.jobs._kill_curproc и api.render._kill_proc для текущего RPROC."""
    import api.render as render
    from webui import _cleanup_on_exit

    kill_curproc_called = []
    kill_render_called = []

    monkeypatch.setattr("api.jobs._kill_curproc", lambda: kill_curproc_called.append(True))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # процесс ещё жив

    with render.RLOCK:
        old_rproc = render.RPROC
        render.RPROC = mock_proc

    try:
        monkeypatch.setattr(render, "_kill_proc", lambda p: kill_render_called.append(p))
        _cleanup_on_exit()
        assert len(kill_curproc_called) == 1, "_kill_curproc должен быть вызван"
        assert kill_render_called == [mock_proc], "_kill_proc должен быть вызван с процессом RPROC"
    finally:
        with render.RLOCK:
            render.RPROC = old_rproc


def test_atexit_cleanup_no_processes(monkeypatch):
    """_cleanup_on_exit не падает, если процессов нет."""
    import api.render as render
    from webui import _cleanup_on_exit

    kill_render_called = []
    monkeypatch.setattr("api.jobs._kill_curproc", lambda: None)
    monkeypatch.setattr(render, "_kill_proc", lambda p: kill_render_called.append(p))

    with render.RLOCK:
        old_rproc = render.RPROC
        render.RPROC = None

    try:
        _cleanup_on_exit()
        assert kill_render_called == [], "_kill_proc не должен вызываться, если RPROC is None"
    finally:
        with render.RLOCK:
            render.RPROC = old_rproc


def test_unload_ours_exception_logged(tmp_path, monkeypatch):
    """Падение unload_ours логируется с трейсбеком и не прерывает работу."""
    from core.applog import get_logger

    log_file = tmp_path / "unload_test.log"
    monkeypatch.setenv("REELSI_LOG", str(log_file))

    # Сконфигурируем логгер до вызова
    get_logger("reelsi.jobs")

    # Имитируем падение aicut.unload_ours внутри api.jobs / cancel
    from unittest.mock import MagicMock
    import types

    fake_aicut = types.ModuleType("core.aicut")
    fake_aicut.unload_ours = MagicMock(side_effect=RuntimeError("LM Studio connection failed"))
    fake_aicut.warn_foreign_models = MagicMock()
    fake_aicut.cancel_call = MagicMock(return_value="ep1")
    fake_aicut.is_current = MagicMock(return_value=True)
    import core
    monkeypatch.setitem(sys.modules, "core.aicut", fake_aicut)
    monkeypatch.setattr(core, "aicut", fake_aicut, raising=False)

    import api.jobs as jobs

    # Запускаем поток выгрузки синхронно, чтобы не зависеть от гонок потоков
    monkeypatch.setattr(jobs.threading.Thread, "start", lambda self: self.run())

    # Сохраняем исходное состояние cancel, чтобы не загрязнять другие тесты
    with jobs.LOCK:
        old_cancel = jobs.JOB.get("cancel", False)

    try:
        # Вызовем /api/cancel
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(jobs.bp)

        with app.test_client() as client:
            resp = client.post("/api/cancel")
            assert resp.status_code == 200

        for h in logging.getLogger("reelsi").handlers:
            h.flush()

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "LM Studio connection failed" in content
    finally:
        with jobs.LOCK:
            jobs.JOB["cancel"] = old_cancel


def test_get_logger_defaults(monkeypatch):
    """get_logger использует paths.root('reelsi.log') по умолчанию и параметры ротации 1МБ x 3."""
    from core import paths
    from core.applog import get_logger

    monkeypatch.delenv("REELSI_LOG", raising=False)
    monkeypatch.delenv("AUTOCUT_LOG", raising=False)

    get_logger("reelsi.test_defaults")
    base = logging.getLogger("reelsi")
    handler = next(h for h in base.handlers if isinstance(h, RotatingFileHandler))
    assert handler.baseFilename == os.path.abspath(paths.root("reelsi.log"))
    assert handler.maxBytes == 1 * 1024 * 1024
    assert handler.backupCount == 3
    assert handler.delay is True


def test_render_exception_logged(tmp_path, monkeypatch):
    """При исключении в рендере полный трейсбек пишется в лог-файл, а 'внутренняя ошибка' сохраняется в RJOB."""
    from core.applog import get_logger
    import api.render as render

    log_file = tmp_path / "render_err.log"
    monkeypatch.setenv("REELSI_LOG", str(log_file))

    # Сконфигурируем логгер
    get_logger("reelsi.render")

    with render.RLOCK:
        render.RJOB["failed"] = []

    # Подменим _run_render_single так, чтобы падало с исключением
    def fake_render_single(*args, **kwargs):
        raise RuntimeError("AE render pipeline crashed unexpectedly")

    monkeypatch.setattr(render, "_run_render_single", fake_render_single)

    batch = [{"xml_path": "clip1.xml"}]
    # Вызываем ветку с одиночным рендером в потоке (набор — уже нормализованный,
    # как его отдаёт api_render_run)
    render._run_render_job(batch, str(tmp_path), str(tmp_path / "out"))

    for h in logging.getLogger("reelsi").handlers:
        h.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "AE render pipeline crashed unexpectedly" in content
    assert "Сбой в потоке рендера" in content

    with render.RLOCK:
        failed = render.RJOB["failed"]
        assert any(f.get("reason") == "внутренняя ошибка" for f in failed)


def test_apply_state_error_key_in_en_json():
    """Ключ ошибки applyState присутствует в static/i18n/en.json и переведён."""
    en_path = os.path.join(ROOT, "static", "i18n", "en.json")
    with open(en_path, "r", encoding="utf-8") as f:
        en = json.load(f)

    key = "⚠ состояние интерфейса не восстановлено: "
    assert key in en, f"Ключ '{key}' должен быть в en.json"
    assert en[key].strip(), f"Перевод для '{key}' не должен быть пустым"
    assert not any('\u0400' <= c <= '\u04FF' for c in en[key]), "Перевод не должен содержать кириллицы"


def test_shared_rotating_handler_single_instance(tmp_path, monkeypatch):
    """После get_logger('reelsi.jobs'), get_logger('reelsi.render'), get_logger('reelsi') ровно один RotatingFileHandler на файл (на базовом), запись через 'reelsi.jobs' попадает в файл ровно один раз."""
    from core.applog import get_logger

    log_file = tmp_path / "shared.log"
    monkeypatch.setenv("REELSI_LOG", str(log_file))

    log_jobs = get_logger("reelsi.jobs")
    log_render = get_logger("reelsi.render")
    log_base = get_logger("reelsi")

    base_rot = [h for h in log_base.handlers if isinstance(h, RotatingFileHandler)]
    jobs_rot = [h for h in log_jobs.handlers if isinstance(h, RotatingFileHandler)]
    render_rot = [h for h in log_render.handlers if isinstance(h, RotatingFileHandler)]

    all_rot = base_rot + jobs_rot + render_rot
    assert len(all_rot) == 1, f"У всех трёх ровно один RotatingFileHandler на файл, получено: {len(all_rot)}"
    assert len(base_rot) == 1, "RotatingFileHandler должен быть на базовом логгере 'reelsi'"
    assert len(jobs_rot) == 0, "У 'reelsi.jobs' не должно быть собственных обработчиков"
    assert len(render_rot) == 0, "У 'reelsi.render' не должно быть собственных обработчиков"

    log_jobs.info("Запись через reelsi.jobs")
    for h in base_rot:
        h.flush()

    assert log_file.exists()
    lines = [line for line in log_file.read_text(encoding="utf-8").strip().splitlines() if "Запись через reelsi.jobs" in line]
    assert len(lines) == 1, f"Запись через reelsi.jobs должна попадать в файл ровно один раз, получено: {lines}"

