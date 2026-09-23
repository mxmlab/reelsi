# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NC: состояние заданий переживает перезапуск, зависшая нарезка видна.

Три дефекта внешнего ревью 2026-09-19 (P1-5), и все три — про одно: у фонового
задания нет состояния, которое видно СНАРУЖИ процесса.

1. `JOB`/`RJOB`/`VJOB` живут в памяти. После перезапуска сервера `/api/status`
   отдавал дефолты, и «не запускалось» было не отличить от «умерло на 90 %»: человек
   начинал работу заново поверх незакрытой нарезки. Журнал заданий (`job_state.json`)
   хранит снимок «задание, запущено, элементы, прогресс, статус» и незакрытую запись
   отдаёт как `interrupted`.
2. Главный путь нарезки сидел в блокирующем `for line in p.stdout`: зависший процесс
   висел бесконечно, и в статусе этого не было видно. Теперь вывод читает фоновый
   поток, а главный цикл считает тишину (`CUT_STALL_S`): флаг `stalled` + строка в лог,
   процесс НЕ убивается — долгая ASR молчит законно.
3. На POSIX «Стоп» убивал только родителя: внук (omni_asr с моделями) оставался жив с
   занятой видеопамятью. Процессы заданий стартуют в своей группе и гасятся группой.

Запуск: python -m pytest tests/test_job_state.py -q
"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from api import _core, jobs, render  # noqa: E402
from core import jobstate  # noqa: E402

HDR = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from webui import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def journal_in_tmp(tmp_path, monkeypatch):
    """Журнал — в tmp_path: модуль держит путь константой, её и переставляем.
    (В conftest REELSI_JOB_STATE уже уведён в сессионный каталог — на случай, если
    какой-то тест дёрнет задание мимо этой фикстуры.)"""
    monkeypatch.setattr(jobstate, "JOB_STATE_PATH", str(tmp_path / "job_state.json"))
    _core._JOURNAL_BOUND.clear()
    _core._JOB_INTERRUPTED.clear()
    yield
    _core._JOURNAL_BOUND.clear()
    _core._JOB_INTERRUPTED.clear()


def _journal():
    with open(jobstate.JOB_STATE_PATH, encoding="utf-8") as f:
        return json.load(f)["jobs"]


def _release_job():
    """Снять JOB и межпроцессный лок — как это делает смерть процесса сервера."""
    with _core.LOCK:
        _core.JOB.update(running=False, done=False, items=[], progress=None,
                         kind="", label="", stalled=False)
    _core._cross_lock_release()


def test_journal_start_item_finish():
    """1а. Старт задания пишет журнал (running + очередь), смена элемента обновляет
    снимок, финиш закрывает запись (done)."""
    assert jobs.job_start(kind="cut", label="ИИ-нарезка"), "JOB не занялся"
    try:
        rec = _journal()["job"]
        assert rec["status"] == "running" and rec["kind"] == "cut"
        assert rec["label"] == "ИИ-нарезка"
        assert rec["items"] == [] and rec["progress"] is None

        _core.items_init(_core.JOB, _core.LOCK, ["01_C0233", "02_C0234"])
        _core.set_progress(1, 2)
        _core.item_set(_core.JOB, _core.LOCK, "01_C0233", stage="cut")

        rec = _journal()["job"]
        assert [it["name"] for it in rec["items"]] == ["01_C0233", "02_C0234"]
        assert rec["item"] == "01_C0233", "в журнале не видно, на чём задание стоит"
        assert rec["progress"] == {"i": 1, "n": 2}
        assert rec["status"] == "running"

        _core.item_done(_core.JOB, _core.LOCK, "01_C0233", "01_C0233.xml")
        rec = _journal()["job"]
        assert rec["items"][0]["stage"] == "done"
        assert rec["item"] == "02_C0234", "после готового клипа «текущий» — следующий в очереди"
    finally:
        _core.job_finish()

    rec = _journal()["job"]
    assert rec["status"] == "done", "финиш задания не закрыл запись журнала"


def test_journal_interrupted_after_restart(client):
    """1б. «Перезапуск сервера» посреди клипа: память пуста, журнал на диске —
    /api/status отдаёт задание как interrupted, с именем элемента и прогрессом.
    Новое задание журнал перезаписывает, и оборванная запись больше не всплывает."""
    assert jobs.job_start(kind="cut", label="ИИ-нарезка")
    _core.items_init(_core.JOB, _core.LOCK, ["01_C0233", "02_C0234"])
    _core.set_progress(2, 2)
    _core.item_set(_core.JOB, _core.LOCK, "02_C0234", stage="cut")

    # Перезапуск: состояние в памяти (JOB, межпроцессный лок, флаги этого старта)
    # пропадает целиком — остаётся только файл журнала.
    _release_job()
    _core._JOB_INTERRUPTED.clear()
    _core.journal_boot()

    d = client.get("/api/status", headers=HDR).get_json()
    assert d["running"] is False and d["done"] is False
    it = d["interrupted"]
    assert it, "оборванное задание не видно в /api/status"
    assert it["status"] == "interrupted"
    assert it["label"] == "ИИ-нарезка"
    assert it["item"] == "02_C0234", "не сказано, на каком клипе оборвалось"
    assert it["progress"] == {"i": 2, "n": 2}

    # Следующее задание перезаписывает журнал: старая запись больше не «оборвана».
    assert jobs.job_start(kind="cut", label="ИИ-нарезка")
    try:
        d = client.get("/api/status", headers=HDR).get_json()
        assert d["interrupted"] is None
        assert _journal()["job"]["status"] == "running"
    finally:
        _core.job_finish()


def test_render_has_its_own_journal_slot(client):
    """1в. Рендер — свой слот журнала: перезапуск сервера посреди рендера не путается
    с нарезкой и виден в /api/render_status (со своим клипом)."""
    _core.journal_bind(render.RJOB, "render", "Рендер AE", "pct")
    with render.RLOCK:
        render.RJOB.update(items=[{"name": "01_C0233", "stage": "aep", "pct": None,
                                   "path": "", "reason": ""}], pct=0.4)
    _core.journal_touch(render.RJOB)

    rec = _journal()["render"]
    assert rec["kind"] == "render" and rec["status"] == "running"
    assert rec["items"][0]["name"] == "01_C0233" and rec["progress"] == 0.4

    _core._JOB_INTERRUPTED.clear()
    _core.journal_boot()
    d = client.get("/api/render_status", headers=HDR).get_json()
    assert d["interrupted"] and d["interrupted"]["status"] == "interrupted"
    assert d["interrupted"]["item"] == "01_C0233"
    # Нарезку чужой рендер не трогает: слоты разные.
    assert client.get("/api/status", headers=HDR).get_json()["interrupted"] is None


def test_cut_stall_is_reported_and_process_is_not_killed(monkeypatch, tmp_path):
    """2. Сторож нарезки: молчащий процесс дольше CUT_STALL_S — флаг stalled в статусе
    и строка «нет вывода N мин» в логе, но процесс ЖИВ (долгая ASR молчит законно)."""
    from webui import app
    app.config["TESTING"] = True

    monkeypatch.setattr(jobs, "CUT_STALL_S", 0.3)
    killed = []
    monkeypatch.setattr(jobs, "kill_tree", lambda p: killed.append(p))
    monkeypatch.setattr(jobs, "CURWORK", [])

    release = threading.Event()

    class SilentStdout:
        """Вывод процесса: молчит, пока тест не отпустит (процесс при этом жив)."""

        def __iter__(self):
            return self

        def __next__(self):
            release.wait(30)
            raise StopIteration

    class SilentProc:
        """Процесс нарезки, который ничего не печатает и не заканчивается."""

        def __init__(self):
            self.pid = 424242
            self.stdout = SilentStdout()
            self.returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    proc = SilentProc()
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: proc)

    done = threading.Event()

    def _run():
        jobs.run_omnicut_job(str(tmp_path), [["cam1.mp4"]], stages={"draft": True})
        done.set()

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not _core.JOB.get("stalled"):
            time.sleep(0.05)
        assert _core.JOB.get("stalled") is True, "сторож не заметил молчание процесса"
        assert not killed, "сторож убил живой процесс — так нельзя, ждём вывода"
        assert proc.poll() is None
        log = [str(x) for x in _core.JOB["log"]]
        assert any("нет вывода" in ln for ln in log), f"в логе нет строки про простой: {log[-3:]}"

        with app.test_client() as c:
            d = c.get("/api/status", headers=HDR).get_json()
        assert d["stalled"] is True, "флаг stalled не доехал до /api/status"
    finally:
        release.set()
        th.join(timeout=15)

    assert done.is_set(), "задание не закончилось после возобновления вывода"
    assert _core.JOB.get("stalled") is False, "флаг простоты остался висеть после процесса"
    assert not killed, "живой процесс нарезки кто-то снял"


@pytest.mark.skipif(os.name == "nt",
                    reason="POSIX-ветка: дерево гасит группа процессов; на Windows — taskkill /T")
def test_stop_kills_grandchild_on_posix(tmp_path, monkeypatch):
    """3. «Стоп» на POSIX гасит ВНУКА: omni_cut порождает omni_asr, и убийство одного
    родителя оставляло модель с занятой видеопамятью."""
    pidfile = tmp_path / "grandchild.pid"
    child_src = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open(sys.argv[1], 'w').write(str(p.pid))\n"
        "time.sleep(120)\n")
    # Так же, как боевой run_omnicut_job: процесс задания — в своей группе.
    p = subprocess.Popen([sys.executable, "-c", child_src, str(pidfile)],
                         **jobs.task_popen_kwargs())
    grand = None
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not pidfile.is_file():
            time.sleep(0.05)
        assert pidfile.is_file(), "дочерний процесс не успел запустить внука"
        grand = int(pidfile.read_text().strip())

        monkeypatch.setattr(jobs, "CURPROC", p)
        monkeypatch.setattr(jobs, "CURWORK", [])
        jobs._kill_curproc()

        p.wait(timeout=10)                       # родителя ещё надо дождаться (зомби)
        assert _wait_gone(grand), "внук (omni_asr) пережил «Стоп» — VRAM осталась занята"
    finally:
        for pid in (p.pid, grand):
            if pid:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass


def _wait_gone(pid, timeout=10.0):
    """Процесс с этим pid кончился (зомби не в счёт — родителя тест дожидается сам)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


class _PosixOsProxy:
    """Прокси над os для изоляции POSIX-ветки kill_tree на Windows.

    ПОЧЕМУ: monkeypatch os.name = 'posix' напрямую в модуле os ломает pathlib.Path
    (он пытается создать PosixPath на Windows при форматировании ошибок pytest).
    Подмена атрибута os в jobstate изолирует ветвление без поломки окружения.
    """
    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(os, name)


@pytest.mark.parametrize("bad_pgid", [0, 1])
def test_kill_tree_does_not_killpg_pgid_0_or_1(monkeypatch, bad_pgid):
    """При getpgid -> 0 или 1 kill_tree НЕ зовёт killpg, но p.kill() зовёт.

    Группа 0 (текущая) и группа 1 (init/системная) защищены от сигналов;
    тест симулирует POSIX-ветку на любой ОС через безопасный прокси jobstate.os.
    """
    killed = []
    killpg_calls = []

    class _P:
        pid = 42424

        def poll(self):
            return None

        def kill(self):
            killed.append(self)

    proxy = _PosixOsProxy(
        name="posix",
        getpgid=lambda pid: bad_pgid,
        getpgrp=lambda: 9999,
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )
    monkeypatch.setattr(jobstate, "os", proxy)
    monkeypatch.setattr(jobstate.signal, "SIGKILL", 9, raising=False)

    p = _P()
    jobstate.kill_tree(p)

    assert killpg_calls == [], f"killpg не должен вызываться для pgid={bad_pgid}"
    assert len(killed) == 1, "p.kill() должен быть вызван"


def test_kill_tree_kills_pgid_greater_than_1(monkeypatch):
    """При валидном pgid > 1 kill_tree зовёт killpg(pgid, SIGKILL) и p.kill()."""
    killed = []
    killpg_calls = []

    class _P:
        pid = 42424

        def poll(self):
            return None

        def kill(self):
            killed.append(self)

    proxy = _PosixOsProxy(
        name="posix",
        getpgid=lambda pid: 500,
        getpgrp=lambda: 9999,
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )
    monkeypatch.setattr(jobstate, "os", proxy)
    monkeypatch.setattr(jobstate.signal, "SIGKILL", 9, raising=False)

    p = _P()
    jobstate.kill_tree(p)

    assert killpg_calls == [(500, 9)], f"killpg должен быть вызван для pgid=500, получено {killpg_calls}"
    assert len(killed) == 1, "p.kill() должен быть вызван"


def test_kill_tree_does_not_killpg_own_group(monkeypatch):
    """Если pgid равен группе сервера, killpg НЕ вызывается."""
    killed = []
    killpg_calls = []

    class _P:
        pid = 42424

        def poll(self):
            return None

        def kill(self):
            killed.append(self)

    proxy = _PosixOsProxy(
        name="posix",
        getpgid=lambda pid: 9999,
        getpgrp=lambda: 9999,
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )
    monkeypatch.setattr(jobstate, "os", proxy)
    monkeypatch.setattr(jobstate.signal, "SIGKILL", 9, raising=False)

    p = _P()
    jobstate.kill_tree(p)

    assert killpg_calls == []
    assert len(killed) == 1

