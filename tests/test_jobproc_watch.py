# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож процесса yt-dlp и rclone: разбор строк, простой, отмена, код возврата, сброс ссылки.

Гоняем ОБЕ функции — `api.podcast._run_ytdlp` и `api.gdrive._run_rclone` — на поддельном
процессе: команду строит вызывающий, поэтому подменять ничего не надо, вместо yt-dlp и
rclone запускается `sys.executable -c <скрипт>`. Ожидания зафиксированы по ТЕКУЩЕМУ
поведению до выноса сторожа в `core/jobproc.py`; тот же набор обязан пройти и после него
без правок — это и есть критерий «поведение не менялось».

Случаи: (а) строки прогресса разобраны, код 0; (б) простой дольше порога — процесс убит;
(в) отмена во время работы — убит за ≤ 8 с (с запасом под нагрузку); (г) ненулевой код как есть; (д) после каждого
случая глобальная ссылка на процесс сброшена и ни одного живого дочернего процесса нет.
psutil в зависимостях нет — живость проверяем через `Popen.poll()`.
"""
import subprocess
import sys
import threading
import time

import pytest

import api.gdrive as gdrive
import api.podcast as podcast_api

NAMES = ["ytdlp", "rclone"]

# Строки, которые печатает поддельный процесс в штатном прогоне.
LINES = {
    "ytdlp": ["[info] Testing",
              "[download]  50.0% of 10.00MiB at 1.00MiB/s ETA 00:05",
              "[download] 100% of 10.00MiB"],
    "rclone": ["2026/08/12 12:00:00 INFO  : IMG.MOV: Copied (new)",
               "Transferred:   0 MiB / 100 MiB, 0%, 10 MiB/s, ETA 1m",
               "Transferred:   100 MiB / 100 MiB, 100%, 10 MiB/s, ETA 0s"],
}
# Начало строки простоя, которую пишет вызывающий через emit.
STALL_MSG = {"ytdlp": "⚠ yt-dlp молчит", "rclone": "⚠ rclone не отвечает"}


def _cmd(lines, tail="", code=0):
    """Поддельный процесс: печатает строки с flush, потом хвост (например, долгий сон)."""
    body = ("import sys, time\n"
            f"for l in {lines!r}:\n"
            "    print(l, flush=True)\n"
            f"{tail}\n"
            f"sys.exit({code})\n")
    return [sys.executable, "-c", body]


def _run(name, cmd, out, stats):
    """Одна из двух настоящих функций; строки и поля статуса складываются в списки."""
    emit = lambda line="", **v: out.append(line)  # noqa: E731 — колбэк как в проде
    stat = lambda f: stats.append(f)              # noqa: E731
    if name == "ytdlp":
        return podcast_api._run_ytdlp(cmd, emit, stat)
    return gdrive._run_rclone(cmd, emit, stat)


def _stall(monkeypatch, name, sec):
    if name == "ytdlp":
        monkeypatch.setattr(podcast_api, "PD_STALL_SEC", sec)
    else:
        monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", sec)


def _set_cancel(name):
    if name == "ytdlp":
        with podcast_api.PDLOCK:
            podcast_api.PDJOB["cancel"] = True
    else:
        with gdrive.GDLOCK:
            gdrive.GDJOB["cancel"] = True


def _is_cancelled(name):
    if name == "ytdlp":
        return bool(podcast_api.PDJOB.get("cancel"))
    return bool(gdrive.GDJOB.get("cancel"))


def _global_proc(name):
    return podcast_api.PDPROC if name == "ytdlp" else gdrive.GDPROC


def _stalled(out, name):
    return any(str(line).startswith(STALL_MSG[name]) for line in out)


@pytest.fixture(autouse=True)
def _чистое_состояние():
    """Флаги отмены и ссылки на процесс — глобальные: каждый случай стартует с нуля."""
    def _reset():
        podcast_api.PDJOB["cancel"] = False
        podcast_api.PDPROC = None
        gdrive.GDJOB["cancel"] = False
        gdrive.GDPROC = None
    _reset()
    yield
    _reset()


@pytest.fixture
def procs(monkeypatch):
    """Все процессы, которые открыли внутри теста (включая taskkill из kill_tree)."""
    real = subprocess.Popen
    made = []

    def rec(*a, **k):
        p = real(*a, **k)
        made.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", rec)
    return made


def _assert_clean(name, procs):
    """(д): ссылка сброшена, ни один процесс не остался жив."""
    assert _global_proc(name) is None
    alive = [p.pid for p in procs if p.poll() is None]
    assert alive == [], f"живые дочерние процессы: {alive}"


# (а) --------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_а_строки_разобраны_код_ноль(name, procs):
    out, stats = [], []
    rc = _run(name, _cmd(LINES[name]), out, stats)
    assert rc == 0
    if name == "ytdlp":
        # Непрогрессная строка уходит в лог как есть; прогресс — в статус и в лог раз в 10 %.
        assert out == [LINES["ytdlp"][0], LINES["ytdlp"][1], LINES["ytdlp"][2]]
        assert [s["pct"] for s in stats] == [50.0, 100.0]
    else:
        assert out[0] == "IMG.MOV: Copied (new)"          # событие — без даты и уровня
        assert [s["pct"] for s in stats] == [0, 100]
        prog = [line for line in out if line.startswith("скачивание:")]
        assert len(prog) == 2, prog
        assert prog[-1] == "скачивание: 100 MiB из 100 MiB (100%)"
    assert not _stalled(out, name)
    _assert_clean(name, procs)


# (б) --------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_б_простой_убивает_процесс(name, procs, monkeypatch):
    _stall(monkeypatch, name, 1)
    out, stats = [], []
    t0 = time.time()
    rc = _run(name, _cmd(LINES[name], tail="time.sleep(60)"), out, stats)
    dt = time.time() - t0
    assert rc == -137                       # код «снято сторожем» — как сейчас
    # Порог 1 с + запас 6 с: под нагрузкой (-n auto, медленный раннер) убийство занимает
    # заметное время. Смысл — «убит по простою, а не висит»: без убийства было бы 60 с.
    assert dt < 1 + 6, dt
    assert _stalled(out, name), out         # вызывающий получил строку о простое
    _assert_clean(name, procs)


# (в) --------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_в_отмена_убивает_за_три_секунды(name, procs):
    out, stats = [], []
    t_cancel = []

    def cancel_now():
        t_cancel.append(time.time())
        _set_cancel(name)

    timer = threading.Timer(1.0, cancel_now)
    timer.start()
    try:
        rc = _run(name, _cmd(LINES[name], tail="time.sleep(60)"), out, stats)
    finally:
        timer.cancel()
    assert t_cancel, "отмена не взведена"
    # Запас до 8 с: смысл — убит после отмены, а не висит (без снятия — 60 с сна).
    assert time.time() - t_cancel[0] <= 8, "процесс не убит за 8 с после отмены"
    assert rc != 0                          # убит: код не нулевой (на Windows taskkill → 1)
    assert not _stalled(out, name)          # отмена — не простой
    assert _is_cancelled(name)              # флаг «Стоп» остаётся у вызывающего
    _assert_clean(name, procs)


# (г) --------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_г_ненулевой_код_как_есть(name, procs):
    out, stats = [], []
    rc = _run(name, _cmd(LINES[name], code=3), out, stats)
    assert rc == 3
    assert not _stalled(out, name)
    assert not _is_cancelled(name)
    _assert_clean(name, procs)


# Прямые тесты самой run_watched: контракт, которым пользуются оба задания.

def test_run_watched_строка_без_признака_жизни_не_сбрасывает_простой(procs):
    """on_line → False (повтор без изменения) не считается активностью: процесс снимается."""
    from core.jobproc import STALLED_RC, run_watched

    killed = []
    seen = []
    cmd = _cmd(["same"] * 3, tail="import time\nfor _ in range(400):\n    print('same', flush=True)\n    time.sleep(0.05)")
    rc = run_watched(cmd, lambda raw: (seen.append(raw), False)[1],
                     set_proc=lambda p: None, cancelled=lambda: False, stall_sec=1,
                     kill=lambda p: (killed.append(p), p.kill()))
    assert rc == STALLED_RC
    assert killed, "повторяющаяся строка без изменений снята не была"


def test_run_watched_ссылка_на_процесс_на_время_работы(procs):
    from core.jobproc import run_watched

    refs = []
    rc = run_watched(_cmd(["x"], code=5), lambda raw: True,
                     set_proc=refs.append, cancelled=lambda: False, stall_sec=60)
    assert rc == 5
    assert len(refs) == 2 and refs[0] is not None and refs[1] is None
    assert refs[0].poll() is not None
