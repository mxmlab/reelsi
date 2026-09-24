# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Состояние заданий без Flask: лог, журнал, очередь этапов, локи, разбор ошибок.

Всё, что работает со СЛОВАРЁМ задания и с процессами и при этом не трогает HTTP-слой
(`request`, `jsonify`, `Blueprint`, `Response`). До этого разделения механика жила в
`api/_core.py` рядом с Flask: оркестрация рендера не могла её взять, не потянув за
собой веб-сервер, а CLI и тесты видели состояние задания только через HTTP-модуль.

ЭКЗЕМПЛЯРЫ состояния (`JOB`, `LOCK`, `RJOB`, `PJOB`) остаются у своих владельцев —
`api/_core.py` и модулей роутов: здесь только функции, которые получают словарь
задания параметром. Поэтому у `items_init`/`item_set`/`item_done`/`item_fail` и
`journal_*` первый аргумент — сам джоб, а `set_progress`/`set_stalled` берут ещё и
замок джоба. `api/_core.py` импортирует перенесённое отсюда и отдаёт модулям роутов
под прежними именами — те не правятся.

Журнал заданий (`job_state.json`) и межпроцессный лок видеокарты (`job.lock`) —
тоже состояние, поэтому пути и открытый хэндл лока живут здесь.
"""
import json, os, signal, subprocess, threading, time
from typing import IO, Any, Iterable

from core import paths
from core.fileio import atomic_json_dump
from core.app_meta import env
from core.umsg import ReelsiError, UMsg


def _json_safe(obj: Any) -> Any:
    """Приводит структуру к JSON-сериализуемому виду.

    Объекты, которые json.dumps не умеет сериализовать (URLError, Exception,
    Path и т. п.), заменяются на str(значение)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return str(obj)


def log_entry(line: str, /, vars: dict[str, Any] | None = None,
              **extra: Any) -> dict[str, Any] | str:
    """Сборка записи лога со структурными переменными.

    Возвращает dict(t=str(line), v=_json_safe(vars)) при непустых vars,
    иначе str(line). Не-JSON объекты (Exception, Path и т. п.) приводятся
    к str через _json_safe, защищая роуты статуса от падения в 500 при jsonify.
    """
    v = dict(vars) if isinstance(vars, dict) else (dict(extra) if extra else None)
    if extra and vars and isinstance(vars, dict):
        v = {**vars, **extra}
    if v:
        return {"t": str(line), "v": _json_safe(v)}
    return str(line)


# --------------------------------------------------------------------------- #
# Пользовательская ошибка (ReelsiError/SystemExit) в ответ и в лог
# --------------------------------------------------------------------------- #
def _umsg_of(e: BaseException) -> UMsg | None:
    """UMsg пользовательской ошибки: ReelsiError хранит его сам, SystemExit — в args[0].

    Второй канал нужен, пока в коде остаются броски SystemExit (настоящие выходы
    процесса и вызовы чужого кода): разбор один на оба класса."""
    u = getattr(e, "umsg", None)
    if isinstance(u, UMsg):
        return u
    a = e.args[0] if getattr(e, "args", None) else None
    return a if isinstance(a, UMsg) else None


def umsg_err(e: BaseException) -> dict[str, Any]:
    """ReelsiError (или SystemExit) из aicut/omni_asr/insertlib в ответ API.

    `raise ReelsiError(umsg('код', 'текст', var=…))` — динамическое пользовательское
    сообщение: отдаём текст (русский fallback) плюс код и переменные, по которым
    фронт берёт перевод ERR_<код> из словаря. Ошибку со строкой отдаём как раньше —
    одним текстом. SystemExit понимается наравне: он ещё живёт в чужих вызовах и
    настоящих выходах процесса, а ответ пользователю у них один и тот же.

    Flask тут не нужен: это словарь. Роут отдаёт его через `jsonify(**umsg_err(e))`,
    а поток задания берёт из него текст (`sysexit_text`)."""
    a = _umsg_of(e)
    if a is not None:
        safe_vars = _json_safe(a.vars) if a.vars else {}
        return {"error": a.msg, "err": a.code, "err_vars": safe_vars}
    return {"error": str(e), "err": None, "err_vars": None}


def sysexit_text(e: BaseException) -> str:
    """Понятный текст пользовательской ошибки для потоков заданий.

    `raise ReelsiError(umsg('код', 'текст', var=…))` — принятый в проекте канал
    ошибок (около 250 мест; раньше это был SystemExit). Синхронные роуты
    ловят её и отдают через umsg_err, а потоки заданий ловили только Exception, а
    SystemExit — BaseException: ошибка («рото не посчитано…», «сборка прервана…»)
    уходила из потока мимо лога и UI. Разбор umsg живёт в ОДНОМ месте — umsg_err;
    второй копии тут нет."""
    return umsg_err(e)["error"]


# --------------------------------------------------------------------------- #
# Процессы заданий: убийство дерева и аргументы запуска
# --------------------------------------------------------------------------- #
def kill_tree(p: subprocess.Popen[Any]) -> None:
    """Убить процесс ВМЕСТЕ С ДЕТЬМИ (POSIX).

    Раньше эта функция была скопирована в трёх местах (api/gdrive.py, api/render.py,
    api/jobs.py) и копии разъезжались. Windows: `taskkill /F /T /PID` (две попытки —
    `/T` иногда таймаутит), затем `p.kill()` как последний шанс хотя бы за родителя.
    POSIX: свои процессы заданий стартуют в СВОЕЙ группе (см. task_popen_kwargs),
    поэтому дерево гасится одним сигналом группе — иначе `p.kill()` снимал только
    родителя, а внук (omni_asr с моделями) оставался жив с занятой видеопамятью.
    """
    if os.name == "nt":
        for _ in range(2):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, timeout=15)
                if p.poll() is not None:
                    return
            except ReelsiError: raise
            except Exception:
                pass  # процесс уже убит или не убивается — ниже добираем p.kill()
    else:
        # getattr, а не прямые имена: в типах эти POSIX-функции объявлены только для
        # не-Windows сборок Python, а mypy проверяет ОБЕ ветки (os.name он не сужает) —
        # прямой вызов дал бы [attr-defined] на Windows-хосте, а `# type: ignore` на
        # Linux оказался бы лишним и упал бы на warn_unused_ignores.
        getpgid = getattr(os, "getpgid")
        getpgrp = getattr(os, "getpgrp")
        killpg = getattr(os, "killpg")
        sigkill = getattr(signal, "SIGKILL")
        try:
            pgid = getpgid(p.pid)
        except OSError:                    # процесс уже кончился — ниже p.kill()
            pgid = None
        # Группы 0/1 и группу СЕРВЕРА не трогаем никогда:
        # pgid 0 — текущая группа процессов, pgid 1 — init/системная группа (ловили на CI:
        # фальшивый pid=1 в тесте слал SIGKILL всей группе init и убивал контейнер runner).
        # Процесс, стартовавший без своей сессии (старый код, чужая обвязка),
        # сидит в нашей группе — killpg убил бы и нас.
        if pgid is not None and pgid > 1 and pgid != getpgrp():
            try:
                killpg(pgid, sigkill)
            except OSError:
                pass  # группы уже нет (процесс умер сам) — дерево добирает p.kill() ниже
    try:
        p.kill()
    except ReelsiError: raise
    except Exception:
        pass  # процесс уже мёртв (гонка с выходом) — убивать нечего


def task_popen_kwargs() -> dict[str, Any]:
    """Аргументы `subprocess.Popen` для процессов заданий (нарезка, рендер, rclone).

    На POSIX задание стартует в СВОЕЙ группе процессов: «Стоп» гасит группу целиком
    (`kill_tree` -> os.killpg), а без этого снимался только родитель, и внук
    (omni_asr/AfterFX с моделями) оставался жив с занятой видеопамятью. На Windows
    своя группа не нужна и не помогает: дерево там гасит `taskkill /T` по родству
    процессов — поведение оставляем как было.
    """
    return {"start_new_session": True} if os.name == "posix" else {}


# --------------------------------------------------------------------------- #
# Межпроцессный лок задач (одна видеокарта на все копии интерфейса)
# --------------------------------------------------------------------------- #
# Межпроцессный лок. LOCK/JOB живут В ПРОЦЕССЕ: два запущенных webui (случайно, или
# рабочий + тестовый на 5098) — это две копии JOB, и без файлового лока нарезка
# стартует в обоих сразу. Два Whisper/Omni в видеопамяти, а на Windows это не «упало
# с OOM», а повисшая машина. Лок держим ОТКРЫТЫМ ХЭНДЛОМ: если процесс умрёт, ОС
# снимет его сама — никаких зависших lock-файлов после аварии.
JOB_LOCK_PATH = env("JOB_LOCK") or paths.root("job.lock")
_JOB_LOCK_FH: IO[bytes] | None = None


def _cross_lock_acquire() -> bool:
    global _JOB_LOCK_FH
    # Раньше здесь стоял короткий путь «лок уже наш (в этом процессе) — значит взяли».
    # Он делал межпроцессный лок НЕВИДИМЫМ внутри процесса: рендер (core/render_job.py)
    # держит его всё время работы, а параллельный job_start нарезки/сборки получал
    # True и стартовал вторую тяжёлую задачу на той же видеокарте.
    # ОС лок не реентерабелен и в одном процессе: второй хэндл на тот же файл
    # получает отказ (замер на Windows: PermissionError), поэтому короткий путь не нужен.
    fh = None
    try:
        fh = open(JOB_LOCK_PATH, "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            # getattr — по той же причине, что в kill_tree: в типах msvcrt объявлен
            # только для Windows-сборок Python, а mypy проверяет обе ветки.
            getattr(msvcrt, "locking")(fh.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        else:
            import fcntl
            # getattr — по той же причине, что в kill_tree: в типах fcntl объявлен
            # только для не-Windows сборок Python.
            getattr(fcntl, "flock")(fh.fileno(),
                                    getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))
    except ReelsiError: raise
    except Exception:
        if fh is not None:
            try:
                fh.close()
            except ReelsiError: raise
            except Exception:
                pass  # закрыть не удалось — файл всё равно не наш, дескриптор освободит GC
        return False
    _JOB_LOCK_FH = fh
    return True


def _cross_lock_release() -> None:
    global _JOB_LOCK_FH
    fh, _JOB_LOCK_FH = _JOB_LOCK_FH, None
    if fh is None:
        return
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            getattr(msvcrt, "locking")(fh.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
        else:
            import fcntl
            getattr(fcntl, "flock")(fh.fileno(), getattr(fcntl, "LOCK_UN"))
    except ReelsiError: raise
    except Exception:
        pass  # явное снятие блокировки не удалось — ниже fh.close() отпускает её сам
    try:
        fh.close()
    except ReelsiError: raise
    except Exception:
        pass  # дескриптор закроет GC — блокировка уже снята


# --------------------------------------------------------------------------- #
# Прогресс и флаг простоя джоба
# --------------------------------------------------------------------------- #
def set_progress(job: dict[str, Any], lock: threading.Lock, i: int, n: int,
                 name: str | None = None) -> None:
    """Структурный прогресс джоба (клип i из n) — клиент рисует бар по нему.

    name — имя обрабатываемого файла: без него в оверлее видно «клип i из n», но не
    видно, над каким клипом идёт работа (задание «единый прогресс»). Старые вызовы с
    двумя аргументами работают как раньше: ключа name в прогрессе просто нет.
    """
    with lock:
        prog: dict[str, Any] = {"i": int(i), "n": int(n)}
        if name is not None:
            prog["name"] = str(name)
        job["progress"] = prog


def set_stalled(job: dict[str, Any], lock: threading.Lock, flag: bool) -> None:
    """Флаг «процесс нарезки молчит дольше CUT_STALL_S».

    Процесс при этом НЕ убивается: долгая ASR молчит законно. Клиент показывает
    флаг в статусе, чтобы зависшая нарезка была видна, а не выглядела работой.
    """
    with lock:
        job["stalled"] = bool(flag)


# --------------------------------------------------------------------------- #
# Журнал заданий: состояние переживает перезапуск сервера
# --------------------------------------------------------------------------- #
# JOB, RJOB и VJOB живут В ПАМЯТИ: после рестарта сервера /api/status отдавал
# дефолты, и «задание не запускалось» было не отличить от «умерло на 90 %».
# Журнал — маленький JSON со снимком последнего задания каждого вида: пишется на
# старте, на смене элемента очереди и на финише. НЕ на каждую строку лога: строк
# бывают тысячи, а журналу достаточно ответить «что шло и докуда дошло».
JOB_STATE_PATH = env("JOB_STATE") or paths.root("job_state.json")
JOURNAL_LOCK = threading.Lock()
# Слот задания по его виду: JOB один на нарезку/сборку/черновик, у рендера и
# генерации видео свои джобы — в журнале они не должны вытеснять друг друга
# (генерация видео идёт в облаке и спокойно живёт параллельно нарезке).
_SLOT_BY_KIND = {"cut": "job", "draft": "job", "build": "job",
                 "render": "render", "video": "video"}
# Стадии очереди, на которых файл реально в работе (не wait/done/error/stopped).
_LIVE_STAGES = ("cut", "jsx", "check", "aep", "render")
# id(job) -> запись привязки. Ключ — id(), поэтому рядом лежит и САМ словарь: без
# ссылки он может быть собран сборщиком мусора, а его id — переиспользован чужим
# словарём (и чужое состояние уехало бы в журнал под нашим именем).
_JOURNAL_BOUND: dict[int, dict[str, Any]] = {}
# Слот -> запись, оборванная перезапуском сервера. Заполняется ОДИН раз на старте
# (journal_boot) и снимается, когда в этот слот стартует новое задание.
_JOB_INTERRUPTED: dict[str, dict[str, Any]] = {}


def _journal_read() -> dict[str, Any]:
    """Содержимое журнала (или {}): битый/отсутствующий файл — не повод падать статусу."""
    try:
        with open(JOB_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except ReelsiError: raise
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _journal_item(items: list[dict[str, Any]]) -> str:
    """Имя элемента, на котором задание остановилось: сначала тот, что В РАБОТЕ,
    иначе первый незавершённый. По нему UI говорит, докуда дошло."""
    for it in items:
        if it.get("stage") in _LIVE_STAGES:
            return str(it.get("name") or "")
    for it in items:
        if it.get("stage") not in ("done", "error", "stopped"):
            return str(it.get("name") or "")
    return ""


def journal_write(slot: str, kind: str, label: str = "", status: str = "running",
                  items: list[dict[str, Any]] | None = None,
                  progress: Any = None, started: int | float | None = None) -> dict[str, Any]:
    """Записать снимок задания в журнал (атомарно, core.fileio).

    Сбой записи не должен ронять задание: журнал вспомогательный, о неудаче
    сообщаем в консоль и работаем дальше (как _vhist_write у истории видео)."""
    items = [dict(it) for it in (items or []) if isinstance(it, dict)]
    now = int(time.time())
    rec: dict[str, Any] = {"slot": slot, "kind": kind, "label": str(label or ""),
                           "started": int(started or now), "updated": now, "status": status,
                           "item": _journal_item(items), "items": items, "progress": progress}
    with JOURNAL_LOCK:
        jobs = _journal_read().get("jobs")
        jobs = dict(jobs) if isinstance(jobs, dict) else {}
        jobs[slot] = rec
        if status == "running":
            _JOB_INTERRUPTED.pop(slot, None)   # новое задание переписало оборванное
        try:
            # ensure_ascii=False уже внутри atomic_json_dump: кириллица в журнале
            # остаётся читаемой, а повторный аргумент — ошибка вызова (ловилась тестом).
            atomic_json_dump(JOB_STATE_PATH, {"version": 1, "jobs": jobs}, indent=1)
        except ReelsiError: raise
        except Exception as e:
            print("job state:", e)
    return rec


def journal_bind(job: dict[str, Any], kind: str, label: str = "",
                 progress_key: str = "progress") -> dict[str, Any]:
    """Привязать джоб к журналу: смена элементов очереди пойдёт в файл сама.

    Привязка — по объекту джоба (id()), поэтому items_init/item_set/item_done/
    item_fail пишут журнал ОДНИМ местом и для JOB, и для RJOB: отдельного хука на
    два десятка вызовов не заводим (главный инвариант очереди этапов)."""
    entry: dict[str, Any] = {"job": job, "slot": _SLOT_BY_KIND.get(kind, kind), "kind": kind,
                             "label": label, "progress_key": progress_key,
                             "started": int(time.time())}
    _JOURNAL_BOUND[id(job)] = entry
    journal_write(entry["slot"], kind, label, "running",
                  items=job.get("items") or [], progress=job.get(progress_key),
                  started=entry["started"])
    return entry


def journal_touch(job: dict[str, Any], status: str = "running") -> None:
    """Переписать журнал текущим состоянием ПРИВЯЗАННОГО джоба (смена элемента,
    финиш). Джоб не привязан — молча выходим: журнал не про него."""
    entry = _JOURNAL_BOUND.get(id(job))
    if not entry or entry.get("job") is not job:
        return
    journal_write(entry["slot"], entry["kind"], entry["label"], status,
                  items=job.get("items") or [], progress=job.get(entry["progress_key"]),
                  started=entry["started"])


def journal_finish(job: dict[str, Any]) -> None:
    """Финиш задания: в журнале остаётся закрытая запись (status=done)."""
    journal_touch(job, status="done")


def journal_interrupted(slot: str) -> dict[str, Any] | None:
    """Задание этого слота, оборванное перезапуском сервера (или None)."""
    rec = _JOB_INTERRUPTED.get(slot)
    return dict(rec) if rec else None


def journal_boot() -> dict[str, Any]:
    """Старт сервера: незакрытая запись журнала — задание, оборванное перезапуском.

    «running» в файле означает ровно это: задание писали, а финиша не было, значит
    процесс сервера умер посреди работы. Помечаем такие записи в ПАМЯТИ (в файле
    оставляем как есть: иначе следующий перезапуск счёл бы их закрытыми)."""
    jobs = _journal_read().get("jobs")
    if not isinstance(jobs, dict):
        return {}
    lost: dict[str, dict[str, Any]] = {}
    for slot, rec in jobs.items():
        if isinstance(rec, dict) and rec.get("status") == "running":
            r = dict(rec)
            r["status"] = "interrupted"
            lost[slot] = r
    _JOB_INTERRUPTED.update(lost)
    return lost


journal_boot()


# --- очередь этапов пофайловая ------------
# Механика ОДНА на нарезку, сборку .jsx и рендер (JOB и RJOB). У джоба появляется
# список items — по одному элементу на файл набора, в порядке набора:
#   {"name": "<стем>", "stage": <код>, "pct": null, "path": "", "reason": ""}
# Коды: wait|cut|jsx|check|aep|render|done|error|stopped.
# ГЛАВНЫЙ ИНВАРИАНТ — ОДНО МЕСТО ЗАПИСИ: «готово/ошибка» решается ТОЛЬКО в
# item_done/item_fail, ровно там, где УЖЕ пишутся results/failed, тем же локом.
# Джоб и лок передаются аргументами, имя списка результатов — параметром:
# у JOB это results, у RJOB — result (существующие ключи переименовывать нельзя).
def _item(job: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Найти элемент очереди по имени стема. Нет такого — None (молча выйти)."""
    for it in job.get("items", []):
        if it.get("name") == name:
            return it
    return None


def items_init(job: dict[str, Any], lock: threading.Lock, names: Iterable[str]) -> None:
    """Завести список items: по одному элементу в порядке набора, все stage="wait"."""
    with lock:
        job["items"] = [{"name": str(n), "stage": "wait", "pct": None,
                         "path": "", "reason": ""} for n in names]
    journal_touch(job)          # снимок очереди в журнал заданий


def item_set(job: dict[str, Any], lock: threading.Lock, name: str, **kw: Any) -> None:
    """Поменять поля одного элемента очереди. Нет такого имени — молча выйти."""
    with lock:
        it = _item(job, name)
        if it is None:
            return
        it.update(kw)
    journal_touch(job)


def item_done(job: dict[str, Any], lock: threading.Lock, name: str, path: Any,
              bucket: str = "results") -> None:
    """Закончить работу над файлом: bucket.append(path) + stage="done", path=path.
    Единственный способ отметить «готово» вместе с записью результата.
    Запись в bucket — ВСЕГДА: готовый файл не должен пропасть из results, даже если
    элемент очереди по имени не найден (элемента нет — только не обновляем его)."""
    with lock:
        job.setdefault(bucket, []).append(path)
        it = _item(job, name)
        if it is not None:
            it.update(stage="done", path=str(path), pct=None)
    journal_touch(job)


def item_fail(job: dict[str, Any], lock: threading.Lock, name: str, reason: Any,
              bucket: str = "failed") -> None:
    """Упасть с файлом: bucket.append({"name","reason"}) + stage="error", reason=reason.
    Единственный способ отметить «ошибку» вместе с записью в failed.
    Запись в bucket — ВСЕГДА: падение не должно пропасть из failed, даже если
    элемент очереди по имени не найден (элемента нет — только не обновляем его)."""
    with lock:
        job.setdefault(bucket, []).append({"name": name, "reason": reason})
        it = _item(job, name)
        if it is not None:
            it.update(stage="error", reason=str(reason))
    journal_touch(job)
