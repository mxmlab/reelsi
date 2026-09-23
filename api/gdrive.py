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
from flask import request, jsonify
from ._core import LOG_CAP, bp, jstr, kill_tree, umsg_err, sysexit_text
from core.applog import get_logger
from core.rclone import (checks_fields, clean_line, is_noise, parse_gdrive_link,
                         progress_line, rclone_cmd, rclone_remote, stats_fields)
from core.umsg import ReelsiError, umsg

log = get_logger("reelsi.gdrive")

# Сторож простоя rclone: 10 минут тишины в stdout -> принудительное завершение
RCLONE_STALL_SEC = 10 * 60
_RCLONE_STALLED = -137
# Поля разобранного прогресса, по изменению которых видно, что передача идёт
# `speed`/`eta` сюда не входят намеренно: они меняются
# и у блока статистики, напечатанного по таймеру `--stats 2s`, когда передача уже встала.
_PROGRESS_KEYS = ("bytes", "pct", "file", "file_pct", "i", "n", "ci", "cn")

GDPROC = None  # Текущий процесс rclone — для отмены через /api/cancel и сторожа простоя


def _kill_proc(p):
    """taskkill /T /F — дерево: rclone с дочерними процессами снимается полностью.

    Имя оставлено ради тестов: реализация теперь одна на пакет — `_core.kill_tree`.
    """
    kill_tree(p)


def gdrive_kill():
    """«Стоп» из интерфейса (/api/cancel зовёт): флаг джобу + реально убить
    текущий subprocess rclone с деревом."""
    with GDLOCK:
        GDJOB["cancel"] = True
        p = GDPROC
    if p and p.poll() is None:
        _kill_proc(p)


def _run_rclone(args, emit, stat=None):
    """rclone копией сабпроцессом со сторожем простоя и поддержкой отмены.
    Статистика уходит в `stat` (живой статус страницы), события — в `emit` (лог джоба).

    В лог прогресс дублируется раз в 10%: лог — это след событий, по которому
    потом видно, докуда дошло, а не лента процентов (наружу отдаются последние
    строки, и при статистике раз в 2 секунды в них не осталось бы ничего).

    Вывод читает ФОНОВЫЙ поток (образец — `_run_proc_afx` в api/render.py): пока
    главный поток сидит в `for line in p.stdout`, он не может ни заметить простой,
    ни снять зависший процесс — а зависшая сеть держала «Скачивание уже идёт»
    до перезапуска сервера. Все строки разбирает ОДИН обработчик `_handle`: если
    разбирать хвост вывода второй копией (после выхода процесса), прогресс уедет
    только в статус, а из лога конец передачи пропадёт.

    Простой считается по ИЗМЕНЕНИЮ разобранного прогресса, а не по факту строки:
    `--stats 2s` печатает блок статистики по таймеру, и раньше
    вставшая передача выглядела живой. Исключение на строке ловится на САМОЙ строке —
    поток чтения из-за одной непонятной строки больше не умирает.
    """
    global GDPROC
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         bufsize=1, creationflags=flags)
    with GDLOCK:
        GDPROC = p
    if GDJOB.get("cancel"):
        _kill_proc(p)

    logged = -10          # -10, а не 0: первый же блок (0%) отмечает в логе старт передачи
    # Время последней РЕАЛЬНОЙ активности: от него, а не от старта, считаем простой —
    # большая папка качается минутами, и рубить её нельзя.
    activity = [time.time()]
    # Последний разобранный прогресс по полям: активность — изменение значения
    # ЛЮБОГО поля прогресса относительно его прежнего значения (словарь last_stat[k]),
    # а не кортежа строки. Строки блока статистики (--stats 2s) имеют
    # разную форму и чередуются; раньше кортеж строки «менялся» на каждой строке
    # при полностью замороженных значениях, и вставшая передача не снималась.
    last_stat = {}

    def _handle(raw):
        nonlocal logged
        line = raw.rstrip()
        if not line.strip():
            return
        f = stats_fields(line)
        chk = None if f else checks_fields(line)
        prog = f or chk
        if prog:
            if f and stat:
                stat(f)
            # Прогресс изменился (байты/проценты/имя файла, счётчик файлов, проверки) —
            # передача жива. Скорость и ETA в ключ НЕ входят: они меняются у блока статистики
            # сами по себе и «оживили» бы вставшую передачу.
            changed = False
            for k in _PROGRESS_KEYS:
                if k in prog:
                    if k not in last_stat or last_stat[k] != prog[k]:
                        last_stat[k] = prog[k]
                        changed = True
            if changed:
                activity[0] = time.time()
            if f:
                pct = f.get("pct")
                if pct is not None and pct >= logged + 10:
                    logged = pct - pct % 10
                    emit(progress_line(line))
            return
        if is_noise(line):
            return                       # остальные строки блока статистики — не активность
        activity[0] = time.time()        # не статистика — живое событие rclone
        emit(clean_line(line))

    # Исключение ловится НА СТРОКУ: раньше `except Exception` стоял вокруг всего цикла,
    # поток чтения умирал на первой же неожиданной строке, активность замирала — и
    # сторож снимал ЗДОРОВОЕ скачивание через 10 минут.
    died = [None]

    def _pump():
        try:
            for line in p.stdout:
                try:
                    _handle(line)
                except ReelsiError: raise
                except Exception as e:
                    log.warning("rclone: строка вывода не разобрана: %s: %s",
                                type(e).__name__, e)
        except ReelsiError: raise
        except Exception as e:               # умерло само чтение потока, не разбор строки
            died[0] = f"{type(e).__name__}: {e}"
            log.warning("rclone: поток чтения вывода упал: %s", died[0])

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()

    stalled = False
    warned_dead = False
    try:
        while p.poll() is None:
            now = time.time()
            if died[0] is not None and not warned_dead:
                # Активность больше неоткуда взять: по простою НЕ убиваем — процесс
                # жив и может дописывать файл молча.
                warned_dead = True
                emit("⚠ поток чтения вывода rclone умер — активность не отслеживается, "
                     "скачивание не снимаю по простою (снять — «Стоп»)")
            if not warned_dead and now - activity[0] >= RCLONE_STALL_SEC:
                emit(f"⚠ rclone не отвечает {int(RCLONE_STALL_SEC // 60)} минут — "
                     f"скачивание снято (сторож простоя)")
                _kill_proc(p)
                stalled = True
                break
            if GDJOB.get("cancel"):
                _kill_proc(p)
                break
            time.sleep(0.5)
        rc = p.wait()
        # Хвост вывода (последние строки rclone) читается ещё мгновение после выхода;
        # ждём его, но не бесконечно: насос — демон и не должен держать джоб.
        pump.join(timeout=10.0)
    finally:
        with GDLOCK:
            if GDPROC is p:
                GDPROC = None
    return _RCLONE_STALLED if stalled else rc


# failed: None пока идём, True/False по итогу — страница показывала «скачивание
# завершено» на любом исходе, и упавший rclone выглядел как успех. Имя не `ok`:
# ответ и так завёрнут в ok=True («запрос принят»), и два разных смысла у одного
# ключа — это 500 на ровном месте.
# cancelled: «Стоп» нажал человек — это НЕ ошибка: раньше отмена
# приезжала на страницу как failed=True и рисовалась красным тостом «не удалось».
GDFRESH = {"cur": "запуск rclone…", "i": 0, "n": 0, "pct": None, "bytes": "", "total": "",
           "speed": "", "eta": "", "file": "", "file_pct": None, "cancel": False,
           "cancelled": False}
GDJOB = dict(GDFRESH, running=False, done=False, failed=None, log=[], log_base=0,
             url="", started=0)
GDLOCK = threading.Lock()


def _gemit(line):
    with GDLOCK:
        GDJOB["log"].append(str(line))
        over = len(GDJOB["log"]) - LOG_CAP
        if over > 0:
            del GDJOB["log"][:over]
            GDJOB["log_base"] += over


def _gstat(fields):
    """Поля прогресса из статистики rclone — в состояние джоба."""
    with GDLOCK:
        GDJOB.update(fields)


def _download_job(cmd, url):
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
def api_gdrive_download():
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
def api_gdrive_status():
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
        start = max(0, since - GDJOB["log_base"])
        d = {k: v for k, v in GDJOB.items() if k not in ("log", "log_base")}
        d["log"] = GDJOB["log"][start:]
        d["log_total"] = GDJOB["log_base"] + len(GDJOB["log"])
        d["elapsed"] = int(time.time()) - GDJOB["started"] if GDJOB["started"] else 0
    return jsonify(ok=True, **d)
