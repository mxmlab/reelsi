# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Инфраструктура бэкенда: Blueprint, состояние джоба, локи, общие пути.

Всё, что нужно ВСЕМ группам роутов и не относится ни к одной из них. Модули роутов
импортируют отсюда `bp` и вешают на него свои @bp.route.
"""
import json, os, threading, time, traceback
from core import paths

# HERE — корень репозитория: личные файлы пользователя (job.lock, ui_state.json)
# лежат там, а не в пакете. Корень считает ровно один модуль — core/paths.py,
# поэтому и sys.path тут больше не правится: пакет импортируется из корня.
HERE = paths.ROOT
from flask import Blueprint, request, jsonify
from werkzeug.exceptions import HTTPException
import reelsi
from core.umsg import UMsg, umsg

DEFAULT_BASE = reelsi.DEFAULT_BASE


def _json_safe(obj):
    """Приводит структуру к JSON-сериализуемому виду (задание HF).

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


def log_entry(line, vars=None, **extra):
    """Сборка записи лога со структурными переменными (задание HH).

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


def umsg_err(e):
    """SystemExit из aicut/omni_asr/insertlib в ответ API.

    `raise SystemExit(umsg('код', 'текст', var=…))` — динамическое пользовательское
    сообщение: отдаём текст (русский fallback) плюс код и переменные, по которым
    фронт берёт перевод ERR_<код> из словаря. Обычный SystemExit("текст") отдаём
    как раньше — одним текстом."""
    a = e.args[0] if e.args else None
    if isinstance(a, UMsg):
        safe_vars = _json_safe(a.vars) if a.vars else {}
        return {"error": a.msg, "err": a.code, "err_vars": safe_vars}
    return {"error": str(e), "err": None, "err_vars": None}


def jstr(d, key, default=""):
    """Строковое значение поля JSON-тела запроса (задание HY).

    Если d — словарь и значение по ключу key является строкой (str), возвращает
    его. В противном случае (ключа нет, значение None, число, список, словарь
    или d не dict) возвращает default (пустую строку). Защищает роуты от
    AttributeError при вызове .strip() на нестроковых типах.
    """
    if isinstance(d, dict):
        v = d.get(key)
        if isinstance(v, str):
            return v
    return default


# APP_NAME / APP_REFERER / app_out_dir самому _core не нужны — он их ПЕРЕЭКСПОРТИРУЕТ
# модулям роутов, чтобы у тех не расползался этот try/except по всему пакету.
try:
    from core.app_meta import (APP_REFERER, APP_NAME, env,   # noqa: F401
                          out_dir as app_out_dir)
except ImportError:
    from reelsi.app_meta import (APP_REFERER, APP_NAME, env,   # noqa: F401
                                 out_dir as app_out_dir)

bp = Blueprint("api", __name__)


# --------------------------------------------------------------------------- #
# Необработанное исключение в роуте — JSON, а не HTML-страница
# --------------------------------------------------------------------------- #
@bp.errorhandler(Exception)
def _json_error(e):
    """Роут упал необработанным исключением — отдать JSON в форме umsg_err.

    Фронт на любой ответ делает `.json()` и читает {error, err, err_vars}
    (static/app/00-core.js, errText): на HTML-500 разбор падал исключением, и
    пользователь не видел НИЧЕГО — ни текста, ни кода. HTTPException (404/405 из
    недр роута) сохраняет свой код, всё остальное — 500 с кодом internal_error.
    Причина по-прежнему печатается в stderr сервера: кроме трейсбека её видеть
    негде (в лог джоба исключение роута не попадает).
    """
    if isinstance(e, HTTPException):
        code = getattr(e, "code", None) or 500
        return jsonify(**umsg_err(SystemExit(umsg("http_error", f"Ошибка запроса ({code})",
                                                  code=code)))), code
    traceback.print_exc()
    err = f"{type(e).__name__}: {e}"
    return jsonify(**umsg_err(SystemExit(umsg("internal_error",
                                              f"Внутренняя ошибка сервера: {err}",
                                              err=err)))), 500


# --------------------------------------------------------------------------- #
# Защита от DNS rebinding (Host) и CSRF (Origin/Sec-Fetch-Site)
# --------------------------------------------------------------------------- #
# Сервер слушает только 127.0.0.1 и авторизации не имеет — это осознанно, локальный
# однопользовательский инструмент (см. SECURITY.md). Но одна дыра из этого всё же
# следует: любая страница, открытая в браузере юзера, может резолвить свой домен в
# 127.0.0.1 и обратиться к нашему API уже «со своего origin» — CORS в этом случае не
# мешает. А `/api/media` по замыслу отдаёт ЛЮБОЙ файл с диска, в том числе
# ai_config.json с ключами провайдеров.
#
# Лечится проверкой Host: настоящий локальный клиент всегда приходит с localhost или
# 127.0.0.1, подставной домен — нет. Ходить по имени машины или LAN-адресу всё равно
# нельзя: сервер на этих адресах не слушает.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _host_is_local(host):
    if not host:
        return False                     # HTTP/1.1 без Host — не браузер и не наш UI
    h = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return h.strip().lower() in _LOCAL_HOSTS


# --------------------------------------------------------------------------- #
# CSRF: изменяющий запрос с чужого origin
# --------------------------------------------------------------------------- #
# Проверки Host мало. При атаке из браузера Host как раз 127.0.0.1:5001, а «простой»
# POST (без preflight) чужая открытая страница отправить может — и /api/cancel,
# /api/video_cancel, /api/ai_stop или любой эндпоинт, терпящий пустое тело,
# выполнится (SECURITY.md относит это к уязвимостям). Браузер САМ проставляет
# Sec-Fetch-Site (страница его подделать не может), поэтому смотрим на него, а если
# его нет — на Origin. У curl/CLI/тестового клиента Flask нет ни того, ни другого:
# их не блокируем, иначе сломается весь внешний вызов API.
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_SITE_SAME = {"same-origin", "none"}


def _origin_is_local(origin):
    """Origin (`схема://хост:порт`) — наш интерфейс? Сравниваем ХОСТ: схема и порт
    к локальности не относятся (тестовый профиль живёт на 5098)."""
    if not origin:
        return False
    from urllib.parse import urlsplit
    try:
        h = urlsplit(origin).hostname
    except ValueError:            # битый Origin (например «http://[») — не наш
        return False
    return bool(h) and h.strip().lower() in _LOCAL_HOSTS


def _forbidden_origin():
    r = umsg_err(SystemExit(umsg("forbidden_origin", "запрос пришёл с чужого сайта")))
    return jsonify(**r), 403


@bp.before_request
def _block_dns_rebinding():
    if not _host_is_local(request.host):
        r = umsg_err(SystemExit(umsg("localhost_only", "только с localhost")))
        return jsonify(**r), 403
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        if site.strip().lower() not in _SITE_SAME:
            return _forbidden_origin()
    elif request.method in _MUTATING_METHODS:
        origin = request.headers.get("Origin")
        if origin is not None and not _origin_is_local(origin):
            return _forbidden_origin()


# Файлы, которые /api/media не отдаёт никогда. Он умеет отдать что угодно с диска —
# это нужно для превью материала, лежащего где попало, — но секреты через него утекать
# не должны ни при каком стечении обстоятельств. Список — только УЧЁТНЫЕ ДАННЫЕ: медиа
# лежит где попало, и запрещать «личное» вообще значит запрещать половину диска.
_NEVER_SERVE = ("ai_config.json", "ai_config.test.json", "rclone.conf")


def _never_serve(path):
    """Секрет ли это. Помимо имён из _NEVER_SERVE сверяем РЕАЛЬНЫЙ путь конфига rclone:
    он переезжает переменной REELSI_RCLONE_CONF (изолированный профиль на 5098), и там
    лежат токены гугл-диска — по одному имени такой файл не поймать. Сегодня сервер и
    так слушает только localhost (см. _block_dns_rebinding), но эта проверка — последний
    рубеж, который переживёт вынос интерфейса наружу (REMOTE_PLAN)."""
    if os.path.basename(path).lower() in _NEVER_SERVE:
        return True
    try:
        from .gdrive import rclone_conf
        return os.path.realpath(path) == os.path.realpath(rclone_conf())
    except Exception:      # конфига нет / путь не разрешается — имени выше достаточно
        return False

JOB = {"running": False, "log": [], "results": [], "failed": [], "done": False, "cancel": False,
       "log_base": 0,   # log_base = сколько строк срезано с начала (для ?since=)
       "kind": "", "label": "", "progress": None}  # kind: cut|draft|build; progress: {"i","n"}
LOCK = threading.Lock()
LOG_CAP = 4000          # ИИ-нарезка стримит тысячи строк — без кэпа лог растёт бесконечно


def emit(line, **vars):
    with LOCK:
        entry = log_entry(line, vars)
        JOB["log"].append(entry)
        over = len(JOB["log"]) - LOG_CAP
        if over > 0:
            del JOB["log"][:over]
            JOB["log_base"] += over


# --- одиночные ИИ-вызовы (жёлтые / вставки / интро): один в один момент ------------
# «Стоп» и перезагрузка страницы рвут fetch у клиента, но поток сервера ещё сидит в
# стриме провайдера. Считаем живые потоки и НЕ стартуем новый вызов, пока старый не
# вышел, иначе оба греют LM Studio, а тот, что доиграл первым, выгружает модель из-под
# второго (симптом: «показывает старый процесс», потом всё залипает).
AI_ACTIVE = 0
AI_WAIT_SEC = 25


def _ai_begin(label=""):
    """Занять одиночный ИИ-вызов. Отменяет предыдущий (по epoch) и ждёт, пока его
    поток реально умрёт. Возвращает номер вызова для _ai_end."""
    global AI_ACTIVE
    from core import aicut
    ep = aicut.begin_call()          # старый поток увидит чужой epoch и выйдет сам
    t0 = time.time()
    warned = False
    while True:
        with LOCK:
            if AI_ACTIVE == 0:
                AI_ACTIVE += 1
                return ep
            if time.time() - t0 >= AI_WAIT_SEC:
                AI_ACTIVE += 1
                break
        if not warned:
            warned = True
            if label:
                emit("⏳ жду завершения предыдущего ИИ-вызова ({label})…", label=label)
            else:
                emit("⏳ жду завершения предыдущего ИИ-вызова…")
        time.sleep(0.25)
    emit("! предыдущий ИИ-вызов не отпустил провайдера за {sec}с — стартую поверх него", sec=AI_WAIT_SEC)
    return ep


def _ai_end(ep, unload=False):
    """Освободить вызов. Выгружаем модель ТОЛЬКО если вызов всё ещё актуален —
    устаревший поток этим убил бы генерацию того, кто стартовал после него."""
    global AI_ACTIVE
    from core import aicut
    with LOCK:
        AI_ACTIVE = max(0, AI_ACTIVE - 1)
    if unload and aicut.is_current(ep):
        try:
            aicut.unload_ours()      # освободить VRAM после генерации (только наши модели)
        except Exception:
            pass


# Межпроцессный лок. LOCK/JOB живут В ПРОЦЕССЕ: два запущенных webui (случайно, или
# рабочий + тестовый на 5098) — это две копии JOB, и без файлового лока нарезка
# стартует в обоих сразу. Два Whisper/Omni в видеопамяти, а на Windows это не «упало
# с OOM», а повисшая машина. Лок держим ОТКРЫТЫМ ХЭНДЛОМ: если процесс умрёт, ОС
# снимет его сама — никаких зависших lock-файлов после аварии.
JOB_LOCK_PATH = env("JOB_LOCK") or paths.root("job.lock")
_JOB_LOCK_FH = None


def _cross_lock_acquire():
    global _JOB_LOCK_FH
    # Раньше здесь стоял короткий путь «лок уже наш (в этом процессе) — значит взяли».
    # Он делал межпроцессный лок НЕВИДИМЫМ внутри процесса: рендер (api/render.py)
    # держит его всё время работы, а параллельный job_start нарезки/сборки получал
    # True и стартовал вторую тяжёлую задачу на той же видеокарте (задание GZ, п. C).
    # ОС лок не реентерабелен и в одном процессе: второй хэндл на тот же файл
    # получает отказ (замер на Windows: PermissionError), поэтому короткий путь не нужен.
    fh = None
    try:
        fh = open(JOB_LOCK_PATH, "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        return False
    _JOB_LOCK_FH = fh
    return True


def _cross_lock_release():
    global _JOB_LOCK_FH
    fh, _JOB_LOCK_FH = _JOB_LOCK_FH, None
    if fh is None:
        return
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def job_finish():
    """Джоб закончился: снять running и отпустить межпроцессный лок."""
    with LOCK:
        JOB["running"] = False
        JOB["done"] = True
    _cross_lock_release()


def job_start(kind="", label="", **extra):
    """Атомарно занять JOB (защита от двойного клика). True = заняли, False = уже идёт.
    kind/label — структурный тип задачи для клиента (никакого сниффинга лога)."""
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, log=[], results=[], failed=[], done=False, cancel=False,
                   log_base=0, kind=kind, label=label, progress=None, insmoved={},
                   items=[], **extra)
    if not _cross_lock_acquire():          # соседний интерфейс уже что-то считает
        with LOCK:
            JOB["running"] = False
        return False
    try:
        from core import aicut
        aicut.clear_cancel()   # прошлый «Стоп» не должен убивать ИИ-шаги нового джоба
    except Exception:
        pass
    return True


def set_progress(i, n):
    """Структурный прогресс джоба (клип i из n) — клиент рисует бар по нему."""
    with LOCK:
        JOB["progress"] = {"i": int(i), "n": int(n)}


# --- очередь этапов пофайловая (задание FA) ------------
# Механика ОДНА на нарезку, сборку .jsx и рендер (JOB и RJOB). У джоба появляется
# список items — по одному элементу на файл набора, в порядке набора:
#   {"name": "<стем>", "stage": <код>, "pct": null, "path": "", "reason": ""}
# Коды: wait|cut|jsx|check|aep|render|done|error|stopped.
# ГЛАВНЫЙ ИНВАРИАНТ — ОДНО МЕСТО ЗАПИСИ: «готово/ошибка» решается ТОЛЬКО в
# item_done/item_fail, ровно там, где УЖЕ пишутся results/failed, тем же локом.
# Джоб и лок передаются аргументами, имя списка результатов — параметром:
# у JOB это results, у RJOB — result (существующие ключи переименовывать нельзя).
def _item(job, name):
    """Найти элемент очереди по имени стема. Нет такого — None (молча выйти)."""
    for it in job.get("items", []):
        if it.get("name") == name:
            return it
    return None


def items_init(job, lock, names):
    """Завести список items: по одному элементу в порядке набора, все stage="wait"."""
    with lock:
        job["items"] = [{"name": str(n), "stage": "wait", "pct": None,
                         "path": "", "reason": ""} for n in names]


def item_set(job, lock, name, **kw):
    """Поменять поля одного элемента очереди. Нет такого имени — молча выйти."""
    with lock:
        it = _item(job, name)
        if it is None:
            return
        it.update(kw)


def item_done(job, lock, name, path, bucket="results"):
    """Закончить работу над файлом: bucket.append(path) + stage="done", path=path.
    Единственный способ отметить «готово» вместе с записью результата.
    Запись в bucket — ВСЕГДА: готовый файл не должен пропасть из results, даже если
    элемент очереди по имени не найден (элемента нет — только не обновляем его)."""
    with lock:
        job.setdefault(bucket, []).append(path)
        it = _item(job, name)
        if it is not None:
            it.update(stage="done", path=str(path), pct=None)


def item_fail(job, lock, name, reason, bucket="failed"):
    """Упасть с файлом: bucket.append({"name","reason"}) + stage="error", reason=reason.
    Единственный способ отметить «ошибку» вместе с записью в failed.
    Запись в bucket — ВСЕГДА: падение не должно пропасть из failed, даже если
    элемент очереди по имени не найден (элемента нет — только не обновляем его)."""
    with lock:
        job.setdefault(bucket, []).append({"name": name, "reason": reason})
        it = _item(job, name)
        if it is not None:
            it.update(stage="error", reason=str(reason))


# Файл состояния UI. Переопределяется через REELSI_UI_STATE — иначе ЛЮБОЙ второй
# инстанс (например тестовый профиль launch.json на 5098) пишет в боевое
# состояние: открыл тестовую страницу, что-то тронул — затёр набор/очередь рабочего.
UI_STATE_PATH = env("UI_STATE") or paths.root("ui_state.json")
