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
import json, os, shutil, subprocess, threading, time
from typing import Any, Callable
from flask import Response, jsonify, request
from ._core import bp, job_log_add, job_log_since, jstr, kill_tree, umsg_err, sysexit_text
from core.applog import get_logger
from core.jobproc import STALLED_RC, run_watched
from core.rclone import (checks_fields, clean_line, is_noise, parse_gdrive_link,
                         progress_line, rclone_cmd, rclone_list_cmd, rclone_remote,
                         root_groups, stats_fields, top_folders)
from core.umsg import ReelsiError, umsg

log = get_logger("reelsi.gdrive")

# Сторож простоя rclone: 10 минут тишины в stdout -> принудительное завершение
RCLONE_STALL_SEC = 10 * 60
# Листинг папки (rclone lsjson -R): большая папка — секунды, зависший rclone — минуту
RCLONE_LIST_TIMEOUT_SEC = 60
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
# part/parts — какая по счёту папка раскладки качается (в обычном скачивании parts=1)
GDFRESH: dict[str, Any] = {"cur": "запуск rclone…", "i": 0, "n": 0, "pct": None, "bytes": "", "total": "",
           "speed": "", "eta": "", "file": "", "file_pct": None, "cancel": False,
           "cancelled": False, "part": 0, "parts": 0}
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


def _download_job(steps: list[tuple[list[str], tuple[str, dict[str, Any]] | None]], url: str) -> None:
    """Скачивание: команды rclone по очереди, в одном потоке.

    steps — (команда, подпись папки). Обычное скачивание — одна команда без подписи;
    раскладка по папкам ссылки — по команде на папку, подпись («папка «X» → …») идёт в лог
    перед каждой. «Стоп» рвёт текущую команду и следующие не запускает. Итог «не удалось» —
    если упала хоть одна: ошибка одной папки не отменяет остальные, они качаются дальше.
    """
    ok = False
    try:
        _gemit(f"качаю {url}")
        codes: list[int] = []
        for i, (cmd, label) in enumerate(steps, 1):
            if _gd_cancelled():
                break
            with GDLOCK:
                GDJOB["part"] = i
            if label is not None:
                _gemit(label[0], **label[1])
            codes.append(_run_rclone(cmd, _gemit, _gstat))
        bad = [c for c in codes if c != 0]
        ok = not bad and len(codes) == len(steps)
        code = bad[0] if bad else 0          # по первой упавшей команде — текст статуса
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


def _map_steps(remote: str, url: str, raw_map: Any) -> list[tuple[list[str], tuple[str, dict[str, Any]] | None]]:
    """Раскладка из запроса → команды rclone, по порядку.

    Элемент {dir, dest}: dir — имя подпапки ссылки, "" — файлы самой ссылки.
    Элемент {files, prefix, dest}: группа файлов корня (см. `core/rclone.root_groups`);
    prefix — приставка группы, только для подписи в логе (пустой — «прочие файлы корня»).
    Пустой dest — «не качать»: элемент пропускается. Папку назначения создаём здесь же,
    как dest обычного скачивания. Битая форма отвечает внятной ошибкой gdrive_map_bad, а не 500.
    """
    if not isinstance(raw_map, list):
        raise ReelsiError(umsg("gdrive_map_bad", "Раскладка по папкам задана неверно: {err}",
                               err="ждали список"))
    steps: list[tuple[list[str], tuple[str, dict[str, Any]] | None]] = []
    try:
        for el in raw_map:
            if not isinstance(el, dict):
                raise ValueError("элемент раскладки — не объект")
            to = el.get("dest")
            if not isinstance(to, str):
                raise ValueError("dest — строка")
            to = to.strip().strip('"')
            if "files" in el:
                files, prefix = el.get("files"), el.get("prefix") or ""
                if (not isinstance(files, list) or not files
                        or not all(isinstance(f, str) for f in files)):
                    raise ValueError("files — непустой список имён файлов")
                if not isinstance(prefix, str):
                    raise ValueError("prefix — строка")
                if not to:
                    continue
                cmd = rclone_cmd(remote, url, to, root_only=True, files=files)
                label = (("файлы «{prefix}…» → {dest}", {"prefix": prefix, "dest": to}) if prefix
                         else ("прочие файлы корня → {dest}", {"dest": to}))
            else:
                name = el.get("dir")
                if not isinstance(name, str):
                    raise ValueError("dir — строка")
                if not to:
                    continue
                cmd = rclone_cmd(remote, url, to, sub=name or None, root_only=not name)
                label = (("папка «{dir}» → {dest}", {"dir": name, "dest": to}) if name
                         else ("файлы корня ссылки → {dest}", {"dest": to}))
            os.makedirs(to, exist_ok=True)
            steps.append((cmd, label))
    except ValueError as e:
        raise ReelsiError(umsg("gdrive_map_bad", "Раскладка по папкам задана неверно: {err}",
                               err=str(e)))
    if not steps:
        raise ReelsiError(umsg("gdrive_map_bad", "Раскладка по папкам задана неверно: {err}",
                               err="ни одна папка не выбрана"))
    return steps


@bp.route("/api/gdrive_download", methods=["POST"])
def api_gdrive_download() -> Response:
    """Скачать материал по ссылке гугл-диска. body: {url, dest, map?}.
    map — раскладка по папкам ссылки (см. _map_steps). Нет map — всё одной командой, как раньше.
    Свой джоб (GDJOB), не общий JOB: нарезка не должна ждать гигабайт с диска —
    тот же образец, что у превью-прокси (PXJOB)."""
    d = request.get_json(silent=True) or {}
    url = jstr(d, "url").strip()
    dest = jstr(d, "dest").strip().strip('"')
    raw_map = d.get("map")
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
            steps: list[tuple[list[str], tuple[str, dict[str, Any]] | None]]
            if raw_map is None:
                steps = [(rclone_cmd(remote, url, dest), None)]
            else:
                steps = _map_steps(remote, url, raw_map)
        except OSError as e:
            raise ReelsiError(umsg("dest_error", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        with GDLOCK:
            GDJOB.update(GDFRESH, running=True, done=False, failed=None, log=[], log_base=0,
                         url=url, started=int(time.time()), parts=len(steps))
        try:
            threading.Thread(target=_download_job, args=(steps, url), daemon=True).start()
        except ReelsiError: raise
        except Exception:
            with GDLOCK:
                GDJOB["running"] = False
            raise
        return jsonify(ok=True, remote=remote, kind=spec["kind"], dest=dest, parts=len(steps))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/gdrive_list", methods=["POST"])
def api_gdrive_list() -> Response:
    """Что лежит в ссылке на папку: подпапки и файлы корня — до скачивания. body: {url}.

    Нужно, чтобы спросить, куда класть каждую подпапку: папка с несколькими камерами
    внутри раньше качалась одной кучей, и списки камер её подпапок не видели. Файлы корня
    отдаются группами (groups) — по приставке имени, как у каждой камеры своя. Для ссылки
    на файл rclone не запускаем — списывать нечего, отвечаем kind='file'.
    """
    d = request.get_json(silent=True) or {}
    url = jstr(d, "url").strip()
    try:
        spec = parse_gdrive_link(url)
        if not spec:
            raise ReelsiError(umsg("bad_link", "Не похоже на ссылку гугл-диска (нужна ссылка "
                                "на файл или папку)"))
        if spec["kind"] == "file":
            return jsonify(ok=True, kind="file")
        if not shutil.which("rclone"):
            raise ReelsiError(umsg("rclone_missing", "rclone не найден в PATH. Поставь rclone и один раз "
                                "настрой гугл-диск: rclone config"))
        try:
            remote = rclone_remote()
        except RuntimeError as e:
            raise ReelsiError(umsg("rclone_not_configured", str(e), err=str(e)))
        try:
            proc = subprocess.run(rclone_list_cmd(remote, url), capture_output=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=RCLONE_LIST_TIMEOUT_SEC,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ReelsiError(umsg("gdrive_list_timeout", "rclone не ответил за {sec} секунд: "
                                   "папка большая или сеть медленная. Попробуй ещё раз",
                                   sec=RCLONE_LIST_TIMEOUT_SEC))
        if proc.returncode != 0:
            lines = [x for x in (proc.stderr or "").splitlines() if x.strip()]
            last = clean_line(lines[-1]) if lines else f"код {proc.returncode}"
            raise ReelsiError(umsg("gdrive_list_failed", "Не получилось прочитать папку гугл-диска: "
                                   "{err}", err=last))
        try:
            items = json.loads(proc.stdout or "[]")
        except ValueError:
            raise ReelsiError(umsg("gdrive_list_failed", "Не получилось прочитать папку гугл-диска: "
                                   "{err}", err="rclone вернул не JSON"))
        res = top_folders(items)
        # groups — файлы корня по приставке имени: окно раскладки предлагает группу целиком
        return jsonify(ok=True, kind="folder", groups=root_groups(res["root"]), **res)
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
