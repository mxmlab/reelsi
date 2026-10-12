# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Скачивание материала с гугл-диска: джоб, процесс rclone, сторож и роуты.

Чистая часть — путь к конфигу, разбор ссылки, выбор remote, сборка команды и
разбор вывода rclone — переехала в `core/rclone.py` (инверсия слоёв):
это функции без состояния, и HTTP-модулю незачем быть их домом. Здесь остаётся
то, что правда про HTTP и процесс: состояние джоба (GDJOB), запуск rclone с
чтением вывода в фоне, сторож простоя, отмена и роуты.

Почему именно rclone, а не своя качалка на requests — в докстроке `core/rclone.py`.
"""
import os, shutil, subprocess, threading, time
from typing import Any, Callable
from flask import Response, jsonify, request
from ._core import bp, job_log_add, job_log_since, jstr, kill_tree, umsg_err, sysexit_text
from core.applog import get_logger
from core.jobproc import STALLED_RC, run_watched
from core.rclone import (checks_fields, clean_line, is_noise, parse_gdrive_link,
                         progress_line, rclone_cmd, rclone_remote, stats_fields)
from core.umsg import ReelsiError, umsg

log = get_logger("reelsi.gdrive")

# Сторож простоя rclone: 10 минут тишины в stdout -> принудительное завершение
RCLONE_STALL_SEC = 10 * 60
_RCLONE_STALLED = STALLED_RC
# Поля разобранного прогресса, по изменению которых видно, что передача идёт
# `speed`/`eta` сюда не входят намеренно: они меняются
# и у блока статистики, напечатанного по таймеру `--stats 2s`, когда передача уже встала.
_PROGRESS_KEYS = ("bytes", "pct", "file", "file_pct", "i", "n", "ci", "cn")

GDPROC: Any = None  # Текущий процесс rclone — для отмены через /api/cancel и сторожа простоя


def _kill_proc(p: Any) -> None:
    """taskkill /T /F — дерево: rclone с дочерними процессами снимается полностью.

    Имя оставлено ради тестов: реализация теперь одна на пакет — `_core.kill_tree`.
    """
    kill_tree(p)


def gdrive_kill() -> None:
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess rclone с деревом."""
    with GDLOCK:
        GDJOB["cancel"] = True
        p = GDPROC
    if p and p.poll() is None:
        _kill_proc(p)


def _set_gdproc(p: subprocess.Popen[str] | None) -> None:
    global GDPROC
    with GDLOCK:
        GDPROC = p


def _gd_cancelled() -> bool:
    return bool(GDJOB.get("cancel"))


def _run_rclone(args: list[str], emit: Callable[..., Any], stat: Callable[..., Any] | None = None) -> int:
    """rclone копией сабпроцессом: разбор вывода здесь, запуск и сторож — `core.jobproc.run_watched`.

    Статистика уходит в `stat` (живой статус страницы), события — в `emit` (лог джоба).

    В лог прогресс дублируется раз в 10%: лог — это след событий, по которому
    потом видно, докуда дошло, а не лента процентов (наружу отдаются последние
    строки, и при статистике раз в 2 секунды в них не осталось бы ничего).

    Все строки разбирает ОДИН обработчик `_handle`: если разбирать хвост вывода второй
    копией (после выхода процесса), прогресс уедет только в статус, а из лога конец
    передачи пропадёт.

    Простой считается по ИЗМЕНЕНИЮ разобранного прогресса, а не по факту строки:
    `--stats 2s` печатает блок статистики по таймеру, и раньше вставшая передача
    выглядела живой. Изменение значения ЛЮБОГО поля прогресса относительно его прежнего
    значения (`last_stat[k]`), а не кортежа строки: строки блока статистики чередуются и
    имеют разную форму. Скорость и ETA в ключ не входят — они меняются у блока статистики
    сами по себе и «оживили» бы вставшую передачу.
    """
    logged = -10          # -10, а не 0: первый же блок (0%) отмечает в логе старт передачи
    # Последний разобранный прогресс по полям (см. докстроку): словарь, а не кортеж строки.
    last_stat: dict[str, Any] = {}

    def _handle(raw: str) -> bool:
        nonlocal logged
        line = raw.rstrip()
        if not line.strip():
            return False
        f = stats_fields(line)
        chk = None if f else checks_fields(line)
        prog = f or chk
        if prog:
            if f and stat:
                stat(f)
            # Прогресс изменился (байты/проценты/имя файла, счётчик файлов, проверки) —
            # передача жива.
            changed = False
            for k in _PROGRESS_KEYS:
                if k in prog:
                    if k not in last_stat or last_stat[k] != prog[k]:
                        last_stat[k] = prog[k]
                        changed = True
            if f:
                pct = f.get("pct")
                if pct is not None and pct >= logged + 10:
                    logged = pct - pct % 10
                    emit(progress_line(line))
            return changed
        if is_noise(line):
            return False                 # остальные строки блока статистики — не активность
        emit(clean_line(line))           # не статистика — живое событие rclone
        return True

    return run_watched(
        args, _handle, set_proc=_set_gdproc, cancelled=_gd_cancelled,
        stall_sec=RCLONE_STALL_SEC,
        on_stall=lambda: emit(f"⚠ rclone не отвечает {int(RCLONE_STALL_SEC // 60)} минут — "
                              f"скачивание снято (сторож простоя)"),
        # Активность больше неоткуда взять: по простою НЕ снимаем — процесс жив и может
        # дописывать файл молча. Снимает только «Стоп».
        on_dead=lambda: emit("⚠ поток чтения вывода rclone умер — активность не отслеживается, "
                             "скачивание не снимаю по простою (снять — «Стоп»)"),
        kill=_kill_proc)


# failed: None пока идём, True/False по итогу — страница показывала «скачивание
# завершено» на любом исходе, и упавший rclone выглядел как успех. Имя не `ok`:
# ответ и так завёрнут в ok=True («запрос принят»), и два разных смысла у одного
# ключа — это 500 на ровном месте.
# cancelled: «Стоп» нажал человек — это НЕ ошибка: раньше отмена
# приезжала на страницу как failed=True и рисовалась красным тостом «не удалось».
GDFRESH: dict[str, Any] = {"cur": "запуск rclone…", "i": 0, "n": 0, "pct": None, "bytes": "", "total": "",
           "speed": "", "eta": "", "file": "", "file_pct": None, "cancel": False,
           "cancelled": False}
GDJOB: dict[str, Any] = dict(GDFRESH, running=False, done=False, failed=None, log=[], log_base=0,
             url="", started=0)
GDLOCK = threading.Lock()


def _gemit(line: Any, /, **vars: Any) -> None:
    # Через log_entry, как лог остальных джобов: шаблон с переменными переводится на клиенте.
    # Раньше здесь был str(line) — перевод в логе rclone не работал.
    job_log_add(GDJOB, GDLOCK, line, vars)


def _gstat(fields: dict[str, Any]) -> None:
    """Поля прогресса из статистики rclone — в состояние джоба."""
    with GDLOCK:
        GDJOB.update(fields)


def _download_job(cmd: list[str], url: str) -> None:
    ok = False
    try:
        _gemit(f"качаю {url}")
        code = _run_rclone(cmd, _gemit, _gstat)
        ok = code == 0
        if GDJOB.get("cancel"):
            cur = "скачивание остановлено"
        elif code == _RCLONE_STALLED:
            cur = f"нет данных {int(RCLONE_STALL_SEC // 60)} минут"
        elif ok:
            cur = "скачивание завершено"
        else:
            cur = f"rclone упал с кодом {code}"
        with GDLOCK:
            GDJOB["cur"] = cur
        _gemit(cur)          # чем кончилось — видно и в логе, не только в статусе
    except (ReelsiError, SystemExit) as e:
        # SystemExit (umsg) — BaseException: без ветки скачивание отмечалось упавшим,
        # но БЕЗ причины — ни в логе, ни в GDJOB["cur"] её не было.
        txt = sysexit_text(e)
        _gemit(f"ОШИБКА: {txt}")
        with GDLOCK:
            GDJOB["cur"] = f"ошибка: {txt}"
    except ReelsiError: raise
    # ошибка уходит в журнал задачи (_gemit) и в статус GDJOB — видна, не глотается
    except Exception as e:
        _gemit(f"ОШИБКА: {type(e).__name__}: {e}")
        with GDLOCK:
            GDJOB["cur"] = f"ошибка: {e}"
    finally:
        # Отмена — не ошибка: failed=False + cancelled=True, страница покажет
        # нейтральное «скачивание остановлено» без красного тоста.
        cancelled = bool(GDJOB.get("cancel"))
        with GDLOCK:
            GDJOB.update(running=False, done=True, cancelled=cancelled,
                         failed=(not ok) and not cancelled)


@bp.route("/api/gdrive_download", methods=["POST"])
def api_gdrive_download() -> Response:
    """Скачать материал по ссылке гугл-диска. body: {url, dest}.
    Свой джоб (GDJOB), не общий JOB: нарезка не должна ждать гигабайт с диска —
    тот же образец, что у превью-прокси (PXJOB)."""
    d = request.get_json(silent=True) or {}
    url = jstr(d, "url").strip()
    dest = jstr(d, "dest").strip().strip('"')
    try:
        spec = parse_gdrive_link(url)
        if not spec:
            raise ReelsiError(umsg("bad_link", "Не похоже на ссылку гугл-диска (нужна ссылка "
                                "на файл или папку)"))
        with GDLOCK:
            if GDJOB["running"]:
                raise ReelsiError(umsg("download_busy", "Скачивание уже идёт — дождись конца"))
        if not shutil.which("rclone"):
            raise ReelsiError(umsg("rclone_missing", "rclone не найден в PATH. Поставь rclone и один раз "
                                "настрой гугл-диск: rclone config"))
        try:
            remote = rclone_remote()
        except RuntimeError as e:
            raise ReelsiError(umsg("rclone_not_configured", str(e), err=str(e)))
        try:
            os.makedirs(dest, exist_ok=True)
            if not os.path.isdir(dest):
                raise ReelsiError(umsg("no_dest", f"Не получается создать папку загрузки: {dest}",
                                      path=dest))
            cmd = rclone_cmd(remote, url, dest)
        except OSError as e:
            raise ReelsiError(umsg("dest_error", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        with GDLOCK:
            GDJOB.update(GDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         url=url, started=int(time.time()))
        try:
            threading.Thread(target=_download_job, args=(cmd, url), daemon=True).start()
        except ReelsiError: raise
        except Exception:
            with GDLOCK:
                GDJOB["running"] = False
            raise
        return jsonify(ok=True, remote=remote, kind=spec["kind"], dest=dest)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/gdrive_status")
def api_gdrive_status() -> Response:
    """Прогресс фонового скачивания с гугл-диска.

    `?since=` — сколько строк лога уже у клиента (тот же уговор, что у
    /api/video_status): раньше отдавались последние 40 строк без счётчика, и
    страница не могла понять, какие из них новые, — поэтому лог rclone до неё
    вообще не доходил.
    """
    try:
        since = int(request.args.get("since") or 0)
    except ValueError:
        since = 0
    with GDLOCK:
        log, total = job_log_since(GDJOB, since)
        d = {k: v for k, v in GDJOB.items() if k not in ("log", "log_base")}
        d["log"] = log
        d["log_total"] = total
        d["elapsed"] = int(time.time()) - GDJOB["started"] if GDJOB["started"] else 0
    return jsonify(ok=True, **d)
