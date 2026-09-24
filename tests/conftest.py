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
import sys
import tempfile
from typing import Any, Callable, Optional

import pytest

# Изоляция файлового лога и файлов состояния сессии тестов ДО любых импортов проекта.
# Модули бэкенда (api, core.aicut, core.terms и др.) связывают пути прямо на уровне модуля
# при импорте (через env(...) or paths.root(...)). Без ранней установки переменных
# модульные константы навсегда привязываются к боевым файлам в корне репозитория.
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="reelsi-tests-")
_TEST_LOG_FILE = os.path.join(_TEST_LOG_DIR, "reelsi.log")
os.environ["REELSI_LOG"] = _TEST_LOG_FILE
os.environ["AUTOCUT_LOG"] = _TEST_LOG_FILE
os.environ["REELSI_CRASH_LOG"] = os.path.join(_TEST_LOG_DIR, "reelsi_crash.log")
os.environ["REELSI_RUN_MARKER"] = os.path.join(_TEST_LOG_DIR, "reelsi.running")

os.environ["REELSI_AI_LOG"] = os.path.join(_TEST_LOG_DIR, "ai_calls.jsonl")
os.environ["REELSI_UI_STATE"] = os.path.join(_TEST_LOG_DIR, "ui_state.json")
os.environ["REELSI_JOB_LOCK"] = os.path.join(_TEST_LOG_DIR, "job.lock")
_TEST_VIDEO_DIR = os.path.join(_TEST_LOG_DIR, "_videogen")
os.environ["REELSI_VIDEO_DIR"] = _TEST_VIDEO_DIR
os.environ["REELSI_VIDEO_HISTORY"] = os.path.join(_TEST_VIDEO_DIR, "history.json")
os.environ["REELSI_TERMS"] = os.path.join(_TEST_LOG_DIR, "terms.json")
os.environ["REELSI_BADWORDS"] = os.path.join(_TEST_LOG_DIR, "badwords.user.txt")
os.environ["REELSI_OKWORDS"] = os.path.join(_TEST_LOG_DIR, "okwords.user.txt")
os.environ["REELSI_INSERTLIB"] = os.path.join(_TEST_LOG_DIR, "insertlib.json")
os.environ["REELSI_RENDER_STATS"] = os.path.join(_TEST_LOG_DIR, "render_stats.json")
os.environ["REELSI_MODELS_DEV"] = os.path.join(_TEST_LOG_DIR, "models_dev.json")
# Журнал заданий (job_state.json): без своей переменной тесты писали бы
# в боевой файл рабочей копии — а сторож изоляции внизу это заметит и завалит сессию.
os.environ["REELSI_JOB_STATE"] = os.path.join(_TEST_LOG_DIR, "job_state.json")

# Изоляция ai_config на уровне сессии тестов:
# REELSI_AI_CONFIG указывает на путь в сессионном каталоге, но файл изначально не создаётся,
# чтобы тесты использовали _default_ai_config() и не копировали чужие боевые ключи.
_TEST_AI_CONFIG = os.path.join(_TEST_LOG_DIR, "ai_config.json")
os.environ["REELSI_AI_CONFIG"] = _TEST_AI_CONFIG

# Настоящие системные функции отправки сигналов: сторож сигналов подменяет os.kill и os.killpg
# на уровне каждого теста, а настоящие реализации вызывает через эти ссылки.
# В тестах сторожа (tests/test_signal_guard.py) их можно подменить заглушками через monkeypatch.
_real_os_kill: Callable[..., Any] = os.kill
_real_os_killpg: Optional[Callable[..., Any]] = getattr(os, "killpg", None)
sys.modules.setdefault("tests.conftest", sys.modules[__name__])



import copy

from api import _core, gdrive, inserts, previewproxy, render, videogen
from core import app_meta, applog, insertlib, paths
from core import jobstate  # noqa: F401  (для sys.modules в фикстуре путей)
from core.aicut import config as _aicut_cfg
from core.aicut import llm

# Отключаем автозасев из личного ai_config.json в корне:
# в тестах конфиг изначально не существует и берётся строго из _default_ai_config()
_aicut_cfg._seed_ai_config = lambda: None

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


# ---- Сторож изоляции репозитория ---------------------------
_ROOT_SNAPSHOT = {}

_SNAPSHOT_SKIP_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
})


def _snapshot_root_files():
    """Снимок всего дерева репозитория: путь относительно корня -> (mtime_ns, size).

    Пропускает служебные каталоги (.git, кэши, виртуальные окружения)
    и скомпилированные файлы *.pyc.
    """
    snap = {}
    root = paths.ROOT
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SNAPSHOT_SKIP_DIRS
                and not d.startswith((".pytest", ".ruff", "__pycache"))
            ]
            for fname in filenames:
                if fname.endswith(".pyc"):
                    continue
                if fname.startswith((".pytest", ".ruff", "__pycache")):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root).replace("\\", "/")
                try:
                    st = os.stat(full)
                    snap[rel] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    except OSError:
        return snap
    return snap


# Инициализируем снимок при загрузке conftest.py
_ROOT_SNAPSHOT = _snapshot_root_files()


def pytest_sessionstart(session):
    """Снимок файлов репозитория до начала прогона тестов."""
    global _ROOT_SNAPSHOT
    _ROOT_SNAPSHOT = _snapshot_root_files()


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
def signal_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сторож «тесты не шлют сигналы»: подменяет os.kill и os.killpg обёрткой,
    которая запрещает отправку сигналов в pid 0, pid 1, отрицательный pid,
    группу 0, группу 1, отрицательную группу, собственную группу процессов
    или собственный pid (если нет маркера allow_self_signal).
    """
    allow_self = request.node.get_closest_marker("allow_self_signal") is not None
    current_pid = os.getpid()

    def safe_kill(pid: int, sig: int) -> None:
        if sig == 0:
            # Проверка существования процесса: сигнал 0 не доставляется
            target = getattr(os.kill, "_real", None) or _real_os_kill
            target(pid, sig)
            return
        if pid == 0:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в pid=0")
        if pid == 1:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в pid=1")
        if pid < 0:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в отрицательный pid={pid}")
        if pid == current_pid and not allow_self:
            raise RuntimeError(
                f"Сторож сигналов: запрещена отправка сигнала {sig} в собственный процесс pid={pid} "
                "(маркер @pytest.mark.allow_self_signal отсутствует)"
            )
        target = getattr(os.kill, "_real", None) or _real_os_kill
        target(pid, sig)

    def safe_killpg(pgid: int, sig: int) -> None:
        if sig == 0:
            # Проверка существования группы процессов: сигнал 0 не доставляется
            target = getattr(os.killpg, "_real", None) or _real_os_killpg
            if target is not None:
                target(pgid, sig)
                return
            raise AttributeError("module 'os' has no attribute 'killpg'")
        if pgid == 0:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в группу pgid=0")
        if pgid == 1:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в группу pgid=1")
        if pgid < 0:
            raise RuntimeError(f"Сторож сигналов: запрещена отправка сигнала {sig} в отрицательную группу pgid={pgid}")
        getpgrp_fn = getattr(os, "getpgrp", None)
        if getpgrp_fn is not None and pgid == getpgrp_fn():
            raise RuntimeError(
                f"Сторож сигналов: запрещена отправка сигнала {sig} в свою группу процессов pgid={pgid}"
            )
        target = getattr(os.killpg, "_real", None) or _real_os_killpg
        if target is not None:
            target(pgid, sig)
            return
        raise AttributeError("module 'os' has no attribute 'killpg'")

    monkeypatch.setattr(os, "kill", safe_kill)
    monkeypatch.setattr(os, "killpg", safe_killpg, raising=False)


@pytest.fixture(autouse=True)
def isolate_state_files(tmp_path, monkeypatch):
    """Изоляция файлов состояния на каждый тест во временный каталог tmp_path."""
    render_stats = tmp_path / "render_stats.json"
    ui_state = tmp_path / "ui_state.json"
    job_lock = tmp_path / "job.lock"
    ai_log = tmp_path / "ai_calls.jsonl"
    models_dev = tmp_path / "models_dev.json"
    video_dir = tmp_path / "_videogen"
    video_hist = video_dir / "history.json"
    terms_path = tmp_path / "terms.json"
    badwords_path = tmp_path / "badwords.user.txt"
    okwords_path = tmp_path / "okwords.user.txt"
    insertlib_path = tmp_path / "insertlib.json"
    ai_config_path = tmp_path / "ai_config.json"
    job_state_path = tmp_path / "job_state.json"

    # Переменные окружения для функций, читающих их динамически
    monkeypatch.setenv("REELSI_RENDER_STATS", str(render_stats))
    monkeypatch.setenv("REELSI_UI_STATE", str(ui_state))
    monkeypatch.setenv("REELSI_JOB_LOCK", str(job_lock))
    monkeypatch.setenv("REELSI_AI_LOG", str(ai_log))
    monkeypatch.setenv("REELSI_MODELS_DEV", str(models_dev))
    monkeypatch.setenv("REELSI_VIDEO_DIR", str(video_dir))
    monkeypatch.setenv("REELSI_VIDEO_HISTORY", str(video_hist))
    monkeypatch.setenv("REELSI_TERMS", str(terms_path))
    monkeypatch.setenv("REELSI_BADWORDS", str(badwords_path))
    monkeypatch.setenv("REELSI_OKWORDS", str(okwords_path))
    monkeypatch.setenv("REELSI_INSERTLIB", str(insertlib_path))
    monkeypatch.setenv("REELSI_AI_CONFIG", str(ai_config_path))
    monkeypatch.setenv("REELSI_JOB_STATE", str(job_state_path))

    # Подмена модульных констант (там, где модуль уже импортирован)
    for mod_name in ("core.aicut.config", "core.aicut", "core.aicut.catalog", "api.ai", "api"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "AI_CONFIG_PATH"):
            monkeypatch.setattr(m, "AI_CONFIG_PATH", str(ai_config_path))
        if m and hasattr(m, "_seed_ai_config"):
            monkeypatch.setattr(m, "_seed_ai_config", lambda: None)

    for mod_name in ("core.aicut.config", "core.aicut", "core.aicut.llm"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "AI_LOG_PATH"):
            monkeypatch.setattr(m, "AI_LOG_PATH", str(ai_log))

    for mod_name in ("core.jobstate", "api._core", "api", "api.files"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "JOB_LOCK_PATH"):
            monkeypatch.setattr(m, "JOB_LOCK_PATH", str(job_lock))
        if m and hasattr(m, "UI_STATE_PATH"):
            monkeypatch.setattr(m, "UI_STATE_PATH", str(ui_state))
        if m and hasattr(m, "JOB_STATE_PATH"):
            monkeypatch.setattr(m, "JOB_STATE_PATH", str(job_state_path))

    for mod_name in ("api.videogen", "api"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "VIDEO_DIR"):
            monkeypatch.setattr(m, "VIDEO_DIR", str(video_dir))
        if m and hasattr(m, "VIDEO_OUT"):
            monkeypatch.setattr(m, "VIDEO_OUT", str(video_dir / "out"))
        if m and hasattr(m, "VIDEO_HIST_PATH"):
            monkeypatch.setattr(m, "VIDEO_HIST_PATH", str(video_hist))

    for mod_name in ("core.terms", "terms"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "TERMS_PATH"):
            monkeypatch.setattr(m, "TERMS_PATH", str(terms_path))

    for mod_name in ("core.censor", "censor"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "USER_PATHS"):
            monkeypatch.setattr(m, "USER_PATHS", {
                "bad": str(badwords_path),
                "ok": str(okwords_path),
            })

    for mod_name in ("core.insertlib", "insertlib"):
        m = sys.modules.get(mod_name)
        if m and hasattr(m, "INDEX_PATH"):
            monkeypatch.setattr(m, "INDEX_PATH", str(insertlib_path))


# ---- Шрифт фикстуры для приёмочных тестов геометрии интро -------
# В CI системных шрифтов нет, и приёмочные тесты «большого слева» там просто
# пропускались (pytest.skip, если не установлен шрифт стиля по умолчанию) — геометрия
# новой фичи не проверялась никогда. Поэтому шрифт едет вместе с тестами: Oswald-Regular
# (SIL OFL 1.1, кириллица, tests/fixtures/fonts/OFL.txt) — его каталог подставляется в
# поиск шрифтов core.fonts на время теста. Продовый код не меняется: только monkeypatch
# каталога и сброс кэша.
FIXTURE_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fixtures", "fonts")
FIXTURE_FONT_PS = "Oswald-Regular"


@pytest.fixture
def fixture_font(monkeypatch):
    """PostScript-имя шрифта из tests/fixtures/fonts, найденного поиском core.fonts.

    Каталог системных шрифтов на время теста подменяется каталогом фикстуры: так
    ожидания теста не зависят от того, что стоит на машине (и от того, стоит ли там
    вообще хоть один шрифт). Кэш списка шрифтов сбрасывается и на входе, и на выходе:
    monkeypatch вернёт каталог, но не кэш, а в кэше остался бы шрифт фикстуры.
    """
    from core import fonts
    path = os.path.join(FIXTURE_FONT_DIR, FIXTURE_FONT_PS + ".ttf")
    assert os.path.isfile(path), "нет шрифта фикстуры: %s" % path
    monkeypatch.setattr(fonts, "_FONT_DIRS", [FIXTURE_FONT_DIR])
    fonts.list_fonts(refresh=True)
    rec = next((r for r in fonts.list_fonts() if r["ps"] == FIXTURE_FONT_PS), None)
    assert rec, "шрифт фикстуры %s не нашёлся поиском core.fonts" % FIXTURE_FONT_PS
    assert os.path.dirname(rec["file"]) == FIXTURE_FONT_DIR, (
        "поиск шрифтов отдал не фикстуру, а системный файл: %s" % rec["file"])
    yield FIXTURE_FONT_PS
    fonts._CACHE = None


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
        # Журнал заданий: привязка джоба к журналу и записи «оборвано
        # перезапуском» — тоже состояние в памяти, соседним тестам они не нужны.
        _core._JOURNAL_BOUND.clear()
        _core._JOB_INTERRUPTED.clear()
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


@pytest.fixture(autouse=True)
def reset_tune_globals():
    """Снимок всех глобалов core.gigaam_cut.tune до теста и восстановление после.

    Импорт ленивый и в try: без torch модуль может не импортироваться —
    тогда фикстура ничего не делает.
    """
    try:
        from core.gigaam_cut import tune
    except Exception:
        yield
        return

    names = set(getattr(tune, "_CUT_GLOBALS", {}).values()) | {"SPEAKER", "_DB_TUNED", "DEDUPE"}
    saved = {name: getattr(tune, name) for name in names if hasattr(tune, name)}
    try:
        yield
    finally:
        for name, val in saved.items():
            setattr(tune, name, val)


def _check_root_snapshot(session):
    """Сравнить снимок файлов репозитория со снимком на старте сессии.

    При расхождении печатает список изменившихся/новых/удалённых файлов
    (до 20 путей и общее число) и завершает сессию с кодом ошибки (exitstatus = 1).
    """
    if not _ROOT_SNAPSHOT:
        return

    current = _snapshot_root_files()
    diffs = []
    for name in sorted(set(current) - set(_ROOT_SNAPSHOT)):
        diffs.append(f"новый файл: {name}")
    for name in sorted(set(_ROOT_SNAPSHOT) - set(current)):
        diffs.append(f"удалён файл: {name}")
    for name in sorted(set(_ROOT_SNAPSHOT) & set(current)):
        old_val = _ROOT_SNAPSHOT[name]
        cur_val = current[name]
        if isinstance(old_val, tuple) and isinstance(cur_val, tuple):
            if old_val != cur_val:
                diffs.append(f"изменён файл: {name}")
        elif isinstance(old_val, (int, float)) and isinstance(cur_val, tuple):
            if old_val != cur_val[0]:
                diffs.append(f"изменён файл: {name}")
        elif old_val != cur_val:
            diffs.append(f"изменён файл: {name}")
    if diffs:
        total = len(diffs)
        shown = diffs[:20]
        header = f"\n[СТОРОЖ ИЗОЛЯЦИИ] Тесты изменили файлы в репозитории (всего {total}):\n  "
        body = "\n  ".join(shown)
        tail = f"\n  ... и ещё {total - 20} путей\n" if total > 20 else "\n"
        sys.stderr.write(header + body + tail)
        session.exitstatus = 1


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
    _check_root_snapshot(session)
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


