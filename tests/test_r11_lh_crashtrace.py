# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты отслеживания падений и маркера активности сервера Reelsi."""
from datetime import datetime
import faulthandler
import json
import os
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest

from core import crashtrace
from core.app_meta import APP_VERSION


@pytest.fixture(autouse=True)
def crashtrace_isolation(tmp_path, monkeypatch):
    """Изолирует файлы логов и маркеров во временную папку и восстанавливает хуки."""
    test_log = tmp_path / "reelsi.log"
    test_crash_log = tmp_path / "reelsi_crash.log"
    test_marker = tmp_path / "reelsi.running"

    monkeypatch.setenv("REELSI_LOG", str(test_log))
    monkeypatch.setenv("REELSI_CRASH_LOG", str(test_crash_log))
    monkeypatch.setenv("REELSI_RUN_MARKER", str(test_marker))

    orig_sys_hook = sys.excepthook
    orig_th_hook = threading.excepthook
    orig_fh_enabled = faulthandler.is_enabled()

    yield {
        "log": test_log,
        "crash_log": test_crash_log,
        "marker": test_marker,
    }

    crashtrace.uninstall()
    sys.excepthook = orig_sys_hook
    threading.excepthook = orig_th_hook
    if not orig_fh_enabled:
        try:
            faulthandler.disable()
        except Exception:
            pass


def test_install_creates_marker_and_atexit_removes_it(crashtrace_isolation):
    """install() создаёт маркер с текущим PID, а функция очистки его удаляет."""
    marker_file = crashtrace_isolation["marker"]
    assert not marker_file.exists()

    crashtrace.install(port=5001)
    assert marker_file.exists(), "Файл reelsi.running должен быть создан"

    data = json.loads(marker_file.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["port"] == 5001
    assert data["version"] == APP_VERSION
    assert "started" in data

    # Вызываем функцию удаления маркера напрямую, как при atexit
    crashtrace._remove_marker(str(marker_file))
    assert not marker_file.exists(), "Маркер должен быть удалён при штатном выходе"


def test_marker_from_dead_pid_logs_warning_and_crash_tail(crashtrace_isolation, monkeypatch):
    """Маркер от мёртвого PID + crash-лог вызывают WARNING с хвостом, сбой wevtutil не роняет install."""
    marker_file = crashtrace_isolation["marker"]
    crash_file = crashtrace_isolation["crash_log"]

    dead_pid = 999999
    started_time = datetime.now().astimezone().isoformat()
    marker_data = {
        "pid": dead_pid,
        "started": started_time,
        "version": "0.2.0-beta",
        "port": 5001,
    }
    marker_file.write_text(json.dumps(marker_data), encoding="utf-8")

    # Создаём crash-лог с 50 строками
    crash_lines = [f"Crash line {i}\n" for i in range(1, 51)]
    crash_file.write_text("".join(crash_lines), encoding="utf-8")

    # Подменяем проверку живости: мёртвый PID
    monkeypatch.setattr(crashtrace, "is_pid_alive", lambda pid: False if pid == dead_pid else True)

    # Подменяем subprocess.run так, чтобы опрос wevtutil завершился ошибкой
    def mock_subprocess_run(*args, **kwargs):
        raise RuntimeError("wevtutil failure simulation")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    mock_log = MagicMock()
    # install не должен выбросить исключение из-за отказа wevtutil
    crashtrace.install(log=mock_log, port=5002)

    # Проверяем, что залогирован WARNING про нештатное завершение
    warning_calls = [call.args for call in mock_log.warning.call_args_list]
    found_crash_warning = any("не штатно" in str(args) for args in warning_calls)
    assert found_crash_warning, "Должно быть предупреждение о нештатном завершении"

    # Проверяем, что выведен хвост лога (последние 40 строк)
    found_tail = any("Crash line 50" in str(args) and "Crash line 11" in str(args) for args in warning_calls)
    assert found_tail, "Хвост crash-лога (до 40 строк) должен попасть в предупреждение"

    # Маркер должен быть перезаписан текущим процессом
    new_data = json.loads(marker_file.read_text(encoding="utf-8"))
    assert new_data["pid"] == os.getpid()
    assert new_data["port"] == 5002


def test_marker_from_alive_pid_does_not_warn(crashtrace_isolation, monkeypatch):
    """Маркер от живого процесса не вызывает ложных предупреждений о падении."""
    marker_file = crashtrace_isolation["marker"]
    marker_data = {
        "pid": os.getpid(),
        "started": datetime.now().astimezone().isoformat(),
        "version": APP_VERSION,
        "port": 5001,
    }
    marker_file.write_text(json.dumps(marker_data), encoding="utf-8")

    mock_log = MagicMock()
    crashtrace.install(log=mock_log, port=5001)

    assert not mock_log.warning.called, "Для живого процесса не должно быть предупреждений"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_threading_excepthook_logs_traceback(crashtrace_isolation):
    """Исключение в фоновом threading.Thread пишется в log.error с трассировкой."""
    mock_log = MagicMock()
    crashtrace.install(log=mock_log)

    err_msg = "test_thread_unhandled_failure_xyz"

    def thread_target():
        raise RuntimeError(err_msg)

    t = threading.Thread(target=thread_target, name="TestWorkerThread")
    t.start()
    t.join()

    assert mock_log.error.called, "log.error должен быть вызван при падении потока"
    error_calls = [str(call.args) for call in mock_log.error.call_args_list]
    assert any(err_msg in s for s in error_calls), "Трассировка ошибки потока должна быть в логе"
    assert any("TestWorkerThread" in s for s in error_calls), "Имя потока должно быть в логе"


def test_faulthandler_is_enabled(crashtrace_isolation):
    """faulthandler активен после install, crash-лог инициализирован заголовком."""
    crash_file = crashtrace_isolation["crash_log"]
    crashtrace.install()

    assert faulthandler.is_enabled(), "faulthandler должен быть включён"
    assert crash_file.exists(), "Файл reelsi_crash.log должен существовать"

    content = crash_file.read_text(encoding="utf-8")
    assert "crash trace session started" in content
    assert str(os.getpid()) in content
    assert APP_VERSION in content


def test_extract_wevtutil_events_filters_python_and_date():
    """_extract_wevtutil_events фильтрует события по слову python и дате запуска."""
    sample_output = """
Event[1]
  Log Name: Application
  Source: Application Error
  Date: 2026-09-17T15:30:00.0000000Z
  Description: Faulting application name: python.exe
Event[2]
  Log Name: Application
  Source: Other App
  Date: 2026-09-17T15:30:00.0000000Z
  Description: Notepad crashed
Event[3]
  Log Name: Application
  Source: Application Error
  Date: 2026-09-17T12:00:00.0000000Z
  Description: Old python crash before start
"""
    started_iso = "2026-09-17T15:00:00+00:00"
    events = crashtrace._extract_wevtutil_events(sample_output, prev_started=started_iso)

    assert len(events) == 1
    assert "python.exe" in events[0]
    assert "Notepad" not in events[0]
    assert "Old python" not in events[0]


def test_is_pid_alive_edge_cases():
    """is_pid_alive корректно обрабатывает текущий PID и заведомо невалидные PID."""
    assert crashtrace.is_pid_alive(os.getpid()) is True
    assert crashtrace.is_pid_alive(-1) is False
    assert crashtrace.is_pid_alive(0) is False
    assert crashtrace.is_pid_alive(None) is False


def test_get_run_marker_path_custom_and_default(tmp_path, monkeypatch):
    """get_run_marker_path учитывает REELSI_RUN_MARKER, а при его отсутствии — порт."""
    monkeypatch.delenv("REELSI_RUN_MARKER", raising=False)
    monkeypatch.setenv("REELSI_LOG", str(tmp_path / "reelsi.log"))

    assert crashtrace.get_run_marker_path() == str(tmp_path / "reelsi.running")
    assert crashtrace.get_run_marker_path(5001) == str(tmp_path / "reelsi.5001.running")
    assert crashtrace.get_run_marker_path("5098") == str(tmp_path / "reelsi.5098.running")

    # При заданном REELSI_RUN_MARKER он возвращается как есть независимо от порта
    custom_marker = str(tmp_path / "custom.running")
    monkeypatch.setenv("REELSI_RUN_MARKER", custom_marker)
    assert crashtrace.get_run_marker_path() == custom_marker
    assert crashtrace.get_run_marker_path(5001) == custom_marker


def test_separate_markers_per_port(tmp_path, monkeypatch):
    """Два install с разными портами создают два разных маркера; сбой на порту A не влияет на B."""
    monkeypatch.delenv("REELSI_RUN_MARKER", raising=False)
    test_log = tmp_path / "reelsi.log"
    monkeypatch.setenv("REELSI_LOG", str(test_log))

    port_a = 5001
    port_b = 5098

    # 1. Два install с разными портами создают два разных маркера
    res_a = crashtrace.install(port=port_a)
    res_b = crashtrace.install(port=port_b)

    marker_a = tmp_path / f"reelsi.{port_a}.running"
    marker_b = tmp_path / f"reelsi.{port_b}.running"

    assert marker_a.exists(), f"Маркер для порта {port_a} должен существовать"
    assert marker_b.exists(), f"Маркер для порта {port_b} должен существовать"
    assert res_a["marker"] == str(marker_a)
    assert res_b["marker"] == str(marker_b)
    assert marker_a != marker_b

    # 2. Маркер порта A от «мёртвого» PID
    dead_pid = 999999
    marker_a.write_text(json.dumps({
        "pid": dead_pid,
        "started": datetime.now().astimezone().isoformat(),
        "version": APP_VERSION,
        "port": port_a,
    }), encoding="utf-8")

    monkeypatch.setattr(crashtrace, "is_pid_alive", lambda pid: False if pid == dead_pid else True)

    # Старт порта B: не должен давать предупреждений о сбое порта A
    mock_log_b = MagicMock()
    crashtrace.install(log=mock_log_b, port=port_b)
    warning_calls_b = [call.args for call in mock_log_b.warning.call_args_list]
    found_crash_b = any("не штатно" in str(args) for args in warning_calls_b)
    assert not found_crash_b, "Старт порта B НЕ должен выдавать предупреждение о падении порта A"

    # Старт порта A: должен дать предупреждение о сбое
    mock_log_a = MagicMock()
    crashtrace.install(log=mock_log_a, port=port_a)
    warning_calls_a = [call.args for call in mock_log_a.warning.call_args_list]
    found_crash_a = any("не штатно" in str(args) for args in warning_calls_a)
    assert found_crash_a, "Старт порта A должен выдать предупреждение о нештатном завершении"

    # Проверяем удаление именно своего файла
    crashtrace._remove_marker(str(marker_a))
    crashtrace._remove_marker(str(marker_b))
    assert not marker_a.exists()
    assert not marker_b.exists()


def test_crash_warning_text_mentions_killed_or_crashed(crashtrace_isolation, monkeypatch):
    """Предупреждение о нештатном выходе содержит фразу '(упал или был снят принудительно)'."""
    marker_file = crashtrace_isolation["marker"]
    dead_pid = 999999
    marker_data = {
        "pid": dead_pid,
        "started": datetime.now().astimezone().isoformat(),
        "version": APP_VERSION,
        "port": 5001,
    }
    marker_file.write_text(json.dumps(marker_data), encoding="utf-8")
    monkeypatch.setattr(crashtrace, "is_pid_alive", lambda pid: False)
    mock_log = MagicMock()
    crashtrace.install(log=mock_log, port=5001)

    warning_calls = [str(call.args) for call in mock_log.warning.call_args_list]
    assert any("завершился не штатно (упал или был снят принудительно)" in s for s in warning_calls)


def test_install_in_non_main_thread_catches_value_error(crashtrace_isolation):
    """Вызов install из фонового потока ловит ValueError от signal.signal и не падает."""
    err = []

    def worker():
        try:
            crashtrace.install(port=5003)
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert not err, f"install в фоновом потоке выбросил исключение: {err}"


def test_sigterm_in_subprocess_removes_marker(tmp_path):
    """Процесс ставит install, завершается сигналом SIGTERM -> маркер удалён, следующий запуск чист."""
    log_file = tmp_path / "reelsi.log"
    marker_file = tmp_path / "reelsi.5001.running"

    sub_code = """
import os, sys, signal
from core import crashtrace

res = crashtrace.install(port=5001)
marker = res["marker"]
if not os.path.isfile(marker):
    sys.exit(2)

if os.name == "nt":
    signal.raise_signal(signal.SIGTERM)
else:
    os.kill(os.getpid(), signal.SIGTERM)
"""
    env = os.environ.copy()
    env["REELSI_LOG"] = str(log_file)
    env.pop("REELSI_RUN_MARKER", None)
    env["PYTHONPATH"] = os.path.abspath(".")

    proc = subprocess.run(
        [sys.executable, "-c", sub_code],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"stdout: {proc.stdout}, stderr: {proc.stderr}"
    assert not marker_file.exists(), "Маркер должен быть удалён обработчиком SIGTERM"

    # Следующий install не должен логировать предупреждений о нештатном выходе
    mock_log = MagicMock()
    env_save = os.environ.get("REELSI_LOG")
    try:
        os.environ["REELSI_LOG"] = str(log_file)
        crashtrace.install(log=mock_log, port=5001)
        assert not any("не штатно" in str(args) for args in [c.args for c in mock_log.warning.call_args_list])
    finally:
        crashtrace.uninstall()
        if env_save is not None:
            os.environ["REELSI_LOG"] = env_save


def test_os_kill_sigterm_in_subprocess(tmp_path):
    """Штатная остановка через os.kill(os.getpid(), signal.SIGTERM); на Windows - skip."""
    if os.name == "nt":
        pytest.skip("На Windows os.kill(pid, SIGTERM) вызывает TerminateProcess в обход обработчиков Python")

    log_file = tmp_path / "reelsi.log"
    marker_file = tmp_path / "reelsi.5001.running"

    sub_code = """
import os, sys, signal
from core import crashtrace

res = crashtrace.install(port=5001)
marker = res["marker"]
if not os.path.isfile(marker):
    sys.exit(2)
os.kill(os.getpid(), signal.SIGTERM)
"""
    env = os.environ.copy()
    env["REELSI_LOG"] = str(log_file)
    env.pop("REELSI_RUN_MARKER", None)
    env["PYTHONPATH"] = os.path.abspath(".")

    proc = subprocess.run(
        [sys.executable, "-c", sub_code],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert not marker_file.exists()

