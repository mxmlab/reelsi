# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Роуты безголового рендера в After Effects (шаг 3).

Свой джоб, как PXJOB у превью-прокси: рендер идёт минуты, и нарезка/сборка .jsx
не должны им блокироваться. Перед запуском AE — предполётная проверка verify_jsx:
пропавший файл в -noui пишется в $.writeln и уходит в никуда, а на выходе будет
.mov без куска камеры, о котором никто не узнает. Проверка ловит это за секунду
без всякого AE — есть пропажи, рендер не запускаем вовсе.

Здесь остаётся ровно HTTP-слой: держатель состояния, «Стоп» и роуты. Движок без
состояния живёт в `core/aerender.py`, оркестрация (запуск AfterFX и aerender,
сторожа простоя, разбор очереди, мастер-проект, ETA по ходу) — в
`core/render_job.py`: тот же код нужен CLI и тестам, а тянуть за собой Flask он не
должен.

Почему держатель остаётся ЗДЕСЬ: `RJOB` и `RLOCK` — объекты состояния, их читают
`/api/render_status`, журнал заданий и `tests/conftest.py`. Роуты передают держатель
в оркестрацию параметром, а не через глобальные имена чужого модуля.
"""
import os
import threading
from typing import Any
from flask import Response, jsonify, request

from core import render_job
from core.aerender import default_render_dir
from core.applog import get_logger
from core.render_job import RenderJob
from core.umsg import ReelsiError, umsg
from .inserts import _convert_inserts
from ._core import (JOB, LOCK, _cross_lock_acquire, _cross_lock_release, bp, jstr,
                    journal_bind, journal_interrupted, log_entry, umsg_err)

log = get_logger("reelsi.render")

RLOCK = threading.Lock()
# Текущий процесс рендера — ЗЕРКАЛО RJOB.proc: ровно это имя читает
# `webui._cleanup_on_exit`, гася при выходе сервера оставшийся aerender/AfterFX.
# Сам процесс живёт в держателе, сюда его кладёт хук on_proc (webui.py этим
# заданием не правится, поэтому зеркало нужно).
RPROC: Any = None
# Короткое имя нужно тому же `webui._cleanup_on_exit` и тестам: реализация одна на
# пакет — core.jobstate.kill_tree, которую зовёт core.render_job.kill_proc.
_kill_proc = render_job.kill_proc


def _mirror_proc(p: Any) -> None:
    """Держатель сменил текущий процесс — обновить модульное зеркало RPROC."""
    global RPROC
    RPROC = p


def remit(line: str, **vars: Any) -> None:
    """Строка в лог рендера (свой лог, не общий JOB).

    Живёт здесь, а не в core/render_job.py, намеренно: `core.app_meta.wrap_emit`
    пропускает колбэк как есть ТОЛЬКО если его модуль начинается на «api.» — с
    writer'ом из ядра строки вставок и сборки потеряли бы структурные переменные
    ({t, v}), по которым фронт переводит лог.
    """
    with RLOCK:
        entry = log_entry(line, vars)
        RJOB["log"].append(entry)
        if len(RJOB["log"]) > 2000:
            del RJOB["log"][:len(RJOB["log"]) - 2000]


RJOB = RenderJob(
    {"running": False, "done": False, "log": [], "pct": None, "cur": "",
     "ae": "", "out_dir": "", "result": [], "failed": [], "cancel": False,
     "eta": None, "eta_phase": None, "eta_total": None, "eta_preliminary": False,
     "stage_label": None, "stage_done": 0, "stage_total": 0},
    emit=remit, lock=RLOCK, on_proc=_mirror_proc,
    # Перекодировка вставок перед сборкой .jsx лежит в api/inserts.py (зовёт
    # разбор тела запроса), а ядро api не импортирует — держателю её даёт владелец.
    convert_inserts=lambda inserts, emit=None: _convert_inserts(inserts, emit=emit or remit))


def render_kill() -> None:
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess (AfterFX или aerender) с деревом.

    Тонкая обёртка: работу делает core.render_job, имя осталось здесь — его зовёт
    api/jobs.py из /api/cancel."""
    render_job.render_kill(RJOB)


@bp.route("/api/render_run", methods=["POST"])
def api_render_run() -> Response:
    """Запустить безголовый рендер. body: {jobs, outdir?, render_dir}.
    Свой RJOB, но ОБЩИЙ лок задач с нарезкой и сборкой .jsx:
    две тяжёлые задачи на одной видеокарте одновременно не идут."""
    d = request.get_json() or {}
    try:
        from .build import _norm_or_error
        norm = _norm_or_error(d.get("jobs") or [], "render_set_invalid")
        if not norm:
            raise ReelsiError(umsg("set_empty", "Набор пуст"))
        render_dir = jstr(d, "render_dir").strip().strip('"') or default_render_dir()
        try:
            os.makedirs(render_dir, exist_ok=True)
        except OSError as e:
            raise ReelsiError(umsg("render_outdir",
                                  f"не создать папку вывода: {render_dir} — {e}",
                                  dir=render_dir, err=str(e)))
        with RLOCK:
            if RJOB["running"]:
                raise ReelsiError(umsg("render_busy",
                                      "Рендер уже идёт — дождись конца или нажми «Остановить»"))
        # Общий лок задач (тот же, что берёт job_start у нарезки и сборки): без него
        # рендер стартовал поверх идущего джоба — две тяжёлые задачи на одной
        # видеокарте, да ещё обе пишут <outdir>/<stem>.jsx. JOB["running"] проверяем
        # отдельно: между «занял JOB» и «взял лок» у job_start есть окно.
        with LOCK:
            job_busy = JOB["running"]
        if job_busy or not _cross_lock_acquire():
            raise ReelsiError(umsg("busy_wait",
                                  "Уже выполняется другая задача — дождись или смотри Логи"))
        try:
            with RLOCK:
                RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                            out_dir=render_dir, result=[], failed=[], cancel=False,
                            items=[], eta=None, eta_phase=None, eta_total=None,
                            eta_preliminary=False, stage_label=None, stage_done=0,
                            stage_total=0)
            # Журнал заданий: после перезапуска сервера /api/render_status
            # отдаёт рендер как interrupted — с клипом, на котором он оборвался.
            journal_bind(RJOB, "render", "Рендер AE", "pct")
            threading.Thread(target=render_job.run_render_job,
                             args=(RJOB, norm, jstr(d, "outdir").strip().strip('"'),
                                   render_dir),
                             daemon=True).start()
        except ReelsiError: raise
        except Exception:
            with RLOCK:
                RJOB["running"] = False
            _cross_lock_release()          # поток не родился — лок не оставляем занятым
            raise
        return jsonify(ok=True)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/render_status")
def api_render_status() -> Response:
    """Состояние рендер-джоба: running/done/pct/cur/ae/out_dir/result/failed/eta + лог.
    interrupted — рендер, оборванный перезапуском сервера (журнал заданий)."""
    with RLOCK:
        return jsonify(ok=True, running=RJOB["running"], done=RJOB["done"],
                       pct=RJOB["pct"], cur=RJOB["cur"], ae=RJOB["ae"],
                       out_dir=RJOB["out_dir"], result=RJOB["result"],
                       failed=list(RJOB["failed"]), items=RJOB.get("items") or [],
                       eta=RJOB.get("eta"), eta_phase=RJOB.get("eta_phase"),
                       eta_total=RJOB.get("eta_total"),
                       eta_preliminary=RJOB.get("eta_preliminary", False),
                       stage_label=RJOB.get("stage_label"),
                       stage_done=RJOB.get("stage_done", 0),
                       stage_total=RJOB.get("stage_total", 0),
                       default_dir=default_render_dir(),
                       interrupted=journal_interrupted("render"),
                       log=RJOB["log"][-200:])
