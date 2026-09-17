# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Трассировка аварийных падений и слежение за жизненным циклом сервера Reelsi.

На Windows нативные сбои (CUDA/torch, C-расширения, DLL) завершают процесс мгновенно
без вызова обработчиков Python, а исключения в фоновых потоках не видны без консоли.
Этот модуль обеспечивает:
1. faulthandler в файл reelsi_crash.log для перехвата нативных сбоев (SIGSEGV/Access Violation);
2. sys.excepthook и threading.excepthook для записи неперехваченных исключений в лог;
3. Маркер reelsi.running для обнаружения нештатного падения предыдущего сеанса при новом запуске
   (с чтением хвоста crash-лога и опросом Windows EventLog).
"""
import atexit
from datetime import datetime, timedelta, timezone
import faulthandler
import json
import logging
import os
import re
import subprocess
import sys
import threading
import traceback

from core import app_meta, paths
from core.app_meta import APP_VERSION

_CRASH_FILE_HANDLE = None
_ORIG_SYS_EXCEPTHOOK = None
_ORIG_THREADING_EXCEPTHOOK = None
_FAULTHANDLER_WAS_ENABLED = False
_CURRENT_MARKER_PATH: str | None = None
_REGISTERED_MARKERS: set[str] = set()


def get_log_dir() -> str:
    """Каталог логов Reelsi (каталог файла REELSI_LOG / reelsi.log)."""
    log_file = app_meta.env("LOG") or paths.root("reelsi.log")
    return os.path.dirname(os.path.abspath(log_file))


def get_crash_log_path() -> str:
    """Путь к файлу аварийного лога (REELSI_CRASH_LOG или reelsi_crash.log рядом с логом)."""
    custom = app_meta.env("CRASH_LOG")
    if custom:
        return os.path.abspath(custom)
    return os.path.join(get_log_dir(), "reelsi_crash.log")


def get_run_marker_path(port=None) -> str:
    """Путь к маркеру активности сервера (REELSI_RUN_MARKER или reelsi.<port>.running рядом с логом)."""
    custom = app_meta.env("RUN_MARKER")
    if custom:
        return os.path.abspath(custom)
    fname = f"reelsi.{port}.running" if port else "reelsi.running"
    return os.path.join(get_log_dir(), fname)


def is_pid_alive(pid: int) -> bool:
    """Проверяет, жив ли процесс с заданным PID, без убийства и сигналов.

    На Windows использует OpenProcess с проверкой кода завершения STILL_ACTIVE (259).
    На POSIX использует os.kill(pid, 0).
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
            if handle:
                exit_code = wintypes.DWORD()
                still_active = 259
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    alive = (exit_code.value == still_active)
                else:
                    alive = False
                kernel32.CloseHandle(handle)
                return alive
            err = kernel32.GetLastError()
            # ERROR_ACCESS_DENIED (5) означает, что процесс существует, но защищён (жив)
            if err == 5:
                return True
            return False
        except Exception:
            # Запасной вариант через tasklist
            try:
                out = subprocess.check_output(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    text=True,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return str(pid) in out
            except Exception:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def _parse_iso_datetime(dt_str: str):
    """Безопасный парсинг ISO даты-времени с поддержкой произвольного числа микросекунд."""
    if not dt_str:
        return None
    try:
        clean = dt_str.strip().replace("Z", "+00:00")
        # Python 3.10 fromisoformat поддерживает не более 6 цифр микросекунд
        clean = re.sub(r"(\.\d{1,6})\d*", r"\1", clean)
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _extract_wevtutil_events(output: str, prev_started: str = None) -> list[str]:
    """Извлекает из вывода wevtutil события с упоминанием python за время жизни запуска."""
    if not output:
        return []
    raw_blocks = re.split(r"(?=^Event\[\d+\])", output, flags=re.MULTILINE)
    matching = []

    prev_start_dt = _parse_iso_datetime(prev_started)

    for block in raw_blocks:
        block_clean = block.strip()
        if not block_clean:
            continue
        if "python" not in block_clean.lower():
            continue

        if prev_start_dt is not None:
            m = re.search(r"Date:\s*(\S+)", block_clean)
            if m:
                ev_dt = _parse_iso_datetime(m.group(1))
                if ev_dt is not None:
                    # Приводим к одному типу таймзоны для сравнения
                    if prev_start_dt.tzinfo is None and ev_dt.tzinfo is not None:
                        ev_dt = ev_dt.astimezone().replace(tzinfo=None)
                    elif prev_start_dt.tzinfo is not None and ev_dt.tzinfo is None:
                        ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                    # Фильтруем события, случившиеся заметно раньше старта запуска
                    if ev_dt < prev_start_dt - timedelta(seconds=2):
                        continue
        matching.append(block_clean)
    return matching


def _check_previous_crash(log: logging.Logger, marker_path: str, crash_path: str):
    """Проверяет маркер предыдущего запуска и логирует предупреждение, если процесс умер не штатно."""
    if not os.path.isfile(marker_path):
        return

    prev_data = None
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            prev_data = json.load(f)
    except Exception as e:
        log.warning("Маркер предыдущего запуска повреждён: %s", e)
        return

    if not isinstance(prev_data, dict):
        return

    prev_pid = prev_data.get("pid")
    prev_started = prev_data.get("started")
    if not prev_pid or is_pid_alive(prev_pid):
        return

    # Процесс умер не штатно (маркер не был удалён atexit)
    log.warning("Прошлый запуск (PID %s, время старта %s) завершился не штатно", prev_pid, prev_started)

    # Хвост reelsi_crash.log (последние 40 строк, если файл свежее старта)
    if os.path.isfile(crash_path):
        try:
            mtime = os.path.getmtime(crash_path)
            is_fresher = True
            prev_start_dt = _parse_iso_datetime(prev_started)
            if prev_start_dt is not None:
                is_fresher = (mtime >= prev_start_dt.timestamp() - 1.0)
            if is_fresher:
                with open(crash_path, "r", encoding="utf-8", errors="replace") as cf:
                    lines = cf.readlines()
                tail = lines[-40:]
                tail_text = "".join(tail).strip()
                if tail_text:
                    log.warning("Хвост reelsi_crash.log (последние %d строк):\n%s", len(tail), tail_text)
        except Exception as e:
            log.warning("Не удалось прочитать crash-лог: %s", e)

    # Опрос журнала событий Application Windows
    if os.name == "nt":
        try:
            cmd = [
                "wevtutil", "qe", "Application",
                '/q:*[System[(EventID=1000 or EventID=1001 or EventID=1026)]]',
                "/c:10", "/rd:true", "/f:text",
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode == 0 and res.stdout:
                events = _extract_wevtutil_events(res.stdout, prev_started)
                for ev in events:
                    log.warning("Запись журнала Application Windows:\n%s", ev)
            elif res.returncode != 0:
                log.warning("wevtutil завершился с ошибкой: код %d", res.returncode)
        except Exception as e:
            log.warning("Не удалось получить события Application Windows: %s", e)


def _write_marker(marker_path: str, port=None):
    """Атомарная запись маркера запущенного сервера."""
    data = {
        "pid": os.getpid(),
        "started": datetime.now().astimezone().isoformat(),
        "version": APP_VERSION,
        "port": port,
    }
    dir_name = os.path.dirname(marker_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{marker_path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, marker_path)


def _remove_marker(marker_path: str = None, port=None):
    """Удаление маркера при штатном выходе (atexit).

    Удаляет маркер только если он принадлежит текущему процессу.
    """
    if marker_path is None:
        marker_path = _CURRENT_MARKER_PATH or get_run_marker_path(port=port)
    try:
        if os.path.isfile(marker_path):
            try:
                with open(marker_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("pid") == os.getpid():
                    os.remove(marker_path)
            except Exception:
                os.remove(marker_path)
    except Exception:
        pass


def install(log=None, port=None):
    """Инициализирует faulthandler, excepthooks и маркер активности процесса.

    1. Включает faulthandler в файл reelsi_crash.log;
    2. Перехватывает unhandled exceptions в главном и фоновых потоках;
    3. Проверяет предыдущий маркер запуска на признаки аварийного падения;
    4. Записывает новый маркер reelsi.<port>.running и регистрирует его удаление в atexit.
    """
    global _CRASH_FILE_HANDLE, _ORIG_SYS_EXCEPTHOOK, _ORIG_THREADING_EXCEPTHOOK
    global _FAULTHANDLER_WAS_ENABLED, _CURRENT_MARKER_PATH, _REGISTERED_MARKERS

    if log is None:
        from core.applog import get_logger
        log = get_logger("reelsi")

    crash_path = get_crash_log_path()
    marker_path = get_run_marker_path(port=port)
    _CURRENT_MARKER_PATH = marker_path

    os.makedirs(os.path.dirname(crash_path), exist_ok=True)
    os.makedirs(os.path.dirname(marker_path), exist_ok=True)

    # 1. Проверяем прошлый запуск ДО перезаписи маркера
    _check_previous_crash(log, marker_path, crash_path)

    # 2. Перезаписываем маркер своим
    _write_marker(marker_path, port=port)

    # 3. Регистрируем удаление маркера при штатном выходе
    if marker_path not in _REGISTERED_MARKERS:
        atexit.register(_remove_marker, marker_path)
        _REGISTERED_MARKERS.add(marker_path)

    # 4. faulthandler в файл reelsi_crash.log
    _FAULTHANDLER_WAS_ENABLED = faulthandler.is_enabled()
    if _CRASH_FILE_HANDLE is not None:
        try:
            _CRASH_FILE_HANDLE.close()
        except Exception:
            pass
        _CRASH_FILE_HANDLE = None

    _CRASH_FILE_HANDLE = open(crash_path, "a", encoding="utf-8")
    _CRASH_FILE_HANDLE.write(
        f"\n--- crash trace session started: {datetime.now().astimezone().isoformat()} "
        f"(PID {os.getpid()}, v{APP_VERSION}) ---\n"
    )
    _CRASH_FILE_HANDLE.flush()
    faulthandler.enable(file=_CRASH_FILE_HANDLE)

    # 5. sys.excepthook и threading.excepthook
    _ORIG_SYS_EXCEPTHOOK = sys.excepthook
    _ORIG_THREADING_EXCEPTHOOK = threading.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                log.error("Необработанное исключение:\n%s", tb_text)
            except Exception:
                pass
        if _ORIG_SYS_EXCEPTHOOK is not None:
            try:
                _ORIG_SYS_EXCEPTHOOK(exc_type, exc_value, exc_tb)
            except Exception:
                pass
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args):
        if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                tb = getattr(args, "exc_traceback", getattr(args, "exc_tb", None))
                tb_text = "".join(traceback.format_exception(args.exc_type, args.exc_value, tb))
                thread_name = getattr(args.thread, "name", "unknown")
                log.error("Необработанное исключение в потоке %s:\n%s", thread_name, tb_text)
            except Exception:
                pass
        if _ORIG_THREADING_EXCEPTHOOK is not None:
            try:
                _ORIG_THREADING_EXCEPTHOOK(args)
            except Exception:
                pass
        else:
            threading.__excepthook__(args)

    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook

    return {
        "crash_log": crash_path,
        "marker": marker_path,
    }


def uninstall():
    """Восстанавливает прежнее состояние faulthandler и хуков (для изоляции тестов)."""
    global _CRASH_FILE_HANDLE, _ORIG_SYS_EXCEPTHOOK, _ORIG_THREADING_EXCEPTHOOK
    global _REGISTERED_MARKERS, _CURRENT_MARKER_PATH

    _REGISTERED_MARKERS.clear()
    _CURRENT_MARKER_PATH = None

    if _ORIG_SYS_EXCEPTHOOK is not None:
        sys.excepthook = _ORIG_SYS_EXCEPTHOOK
        _ORIG_SYS_EXCEPTHOOK = None
    if _ORIG_THREADING_EXCEPTHOOK is not None:
        threading.excepthook = _ORIG_THREADING_EXCEPTHOOK
        _ORIG_THREADING_EXCEPTHOOK = None

    if not _FAULTHANDLER_WAS_ENABLED:
        try:
            faulthandler.disable()
        except Exception:
            pass

    if _CRASH_FILE_HANDLE is not None:
        try:
            _CRASH_FILE_HANDLE.close()
        except Exception:
            pass
        _CRASH_FILE_HANDLE = None
