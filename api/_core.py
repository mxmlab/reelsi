# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Инфраструктура бэкенда: Blueprint, состояние джоба, локи, общие пути.

Всё, что нужно ВСЕМ группам роутов и не относится ни к одной из них. Модули роутов
импортируют отсюда `bp` и вешают на него свои @bp.route.

Механика состояния задания (лог, журнал, очередь этапов, межпроцессный лок,
разбор пользовательских ошибок) уехала в `core/jobstate.py`: она не трогает Flask и
нужна ещё и оркестрации рендера (`core/render_job.py`). Имена перенесённого
импортируются сюда и раздаются модулям роутов как раньше — те не правятся.
ЭКЗЕМПЛЯРЫ состояния (JOB, LOCK, их джобы) остаются здесь: у них один владелец.
"""
import glob, os, threading, time, traceback
from typing import Any

from core import jobstate, paths

# HERE — корень репозитория: личные файлы пользователя (job.lock, ui_state.json)
# лежат там, а не в пакете. Корень считает ровно один модуль — core/paths.py,
# поэтому и sys.path тут больше не правится: пакет импортируется из корня.
HERE = paths.ROOT
from flask import Blueprint, Response, request, jsonify
from werkzeug.exceptions import HTTPException
from core.cams import DEFAULT_BASE
from core.umsg import ReelsiError, umsg
from core.applog import get_logger

# Перенесённое в core/jobstate.py — под прежними именами для модулей роутов.
# JOB_LOCK_PATH, _JOB_LOCK_FH, JOB_STATE_PATH НЕ реэкспортируются: они перепривязываются
# или подменяются в тестах — реэкспорт приводил бы к устареванию копии в api._core.
from core.jobstate import (  # noqa: F401
    _JOURNAL_BOUND, _JOB_INTERRUPTED,
    JOURNAL_LOCK, _cross_lock_acquire, _cross_lock_release,
    item_done, item_fail, item_set, items_init, journal_bind, journal_boot,
    journal_finish, journal_interrupted, journal_touch, journal_write, kill_tree,
    log_entry, sysexit_text, task_popen_kwargs, umsg_err)

log = get_logger(__name__)


def jstr(d: Any, key: str, default: str = "") -> str:
    """Строковое значение поля JSON-тела запроса.

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
# модулям роутов, чтобы у тех не расползался этот импорт по всему пакету.
# Запасной ветки `from reelsi.app_meta import …` тут больше нет: она
# осталась от времён, когда ядро лежало в пакете `reelsi/`, и сработать уже не могла —
# `reelsi` это модуль CLI, а не пакет. Заодно api/ больше не импортирует CLI вовсе.
from core.app_meta import (APP_REFERER, APP_NAME, env,   # noqa: F401
                           out_dir as app_out_dir)

bp = Blueprint("api", __name__)


# --------------------------------------------------------------------------- #
# Необработанное исключение в роуте — JSON, а не HTML-страница
# --------------------------------------------------------------------------- #
@bp.errorhandler(ReelsiError)
def _json_reelsi_error(e: ReelsiError) -> Response:
    """Пользовательская ошибка из роута, который её не поймал, — JSON, а не 500.

    Такой роут раньше обрывал запрос вовсе: SystemExit — не Exception, и Flask его
    не ловил, а фронт на сетевой ошибке не показывал ни текста, ни кода. Отдаём
    ровно тот же ответ, что роут отдал бы сам через `umsg_err` (и тот же код 200,
    что у соседних роутов с `jsonify(**umsg_err(e))`): это ошибка пользователя, а
    не сбой сервера, — в `internal_error` её заворачивать нельзя."""
    return jsonify(**umsg_err(e))


@bp.errorhandler(Exception)
def _json_error(e: Exception) -> Response | tuple[Response, int]:
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
        return jsonify(**umsg_err(ReelsiError(umsg("http_error", f"Ошибка запроса ({code})",
                                                   code=code)))), code
    traceback.print_exc()
    err = f"{type(e).__name__}: {e}"
    return jsonify(**umsg_err(ReelsiError(umsg("internal_error",
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


def _host_is_local(host: str) -> bool:
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

# GET-роуты с ПОБОЧНЫМ действием: открывают диалог выбора файла
# (Tk-подпроцесс, /api/pick*) или пишут кэш волны РЯДОМ с файлом (/api/waveform).
# Чужой странице хватало <img src> или <script src> на такой адрес — ответ ей не
# прочитать, но диалог уже открылся, а файл на диске появился. Sec-Fetch-Site их и
# так закрывал, а вот Origin проверялся только у изменяющих методов: браузер,
# отправивший простой GET с Origin и без Sec-Fetch-Site, проходил. Поэтому у этих
# путей Origin проверяется ровно как у POST. Остальные GET (статусы, списки) — как
# раньше: чтение чужой странице ничего не даёт, а сломать его легко.
_SIDE_EFFECT_GETS = ("/api/pickmedia", "/api/pickfiles", "/api/pickone",
                     "/api/pickaudio", "/api/pickdir", "/api/waveform")


def _origin_check_required() -> bool:
    """Проверять ли Origin у этого запроса: он обязателен у изменяющих методов и у
    побочных GET (см. _SIDE_EFFECT_GETS)."""
    if request.method in _MUTATING_METHODS:
        return True
    return request.method == "GET" and request.path in _SIDE_EFFECT_GETS


def _origin_is_local(origin: str) -> bool:
    """Origin (`схема://хост:порт`) — наш интерфейс? Сравниваем ХОСТ: схема и порт
    к локальности не относятся (тестовый профиль живёт на 5098)."""
    if not origin:
        return False
    from urllib.parse import urlsplit
    try:
        h = urlsplit(origin).hostname
    except ValueError:            # битый Origin (например «http://[») — не наш
        return False
    if not h:                     # схема без хоста («file:///») — не наш origin
        return False
    return h.strip().lower() in _LOCAL_HOSTS


def _forbidden_origin() -> tuple[Response, int]:
    r = umsg_err(ReelsiError(umsg("forbidden_origin", "запрос пришёл с чужого сайта")))
    return jsonify(**r), 403


@bp.before_request
def _block_dns_rebinding() -> Response | tuple[Response, int] | None:
    if not _host_is_local(request.host):
        r = umsg_err(ReelsiError(umsg("localhost_only", "только с localhost")))
        return jsonify(**r), 403
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        if site.strip().lower() not in _SITE_SAME:
            return _forbidden_origin()
    elif _origin_check_required():
        origin = request.headers.get("Origin")
        if origin is not None and not _origin_is_local(origin):
            return _forbidden_origin()
    return None          # «не блокируем» — у before_request это и есть None


@bp.before_request
def _check_json_body() -> tuple[Response, int] | None:
    """Тело запроса обязано быть JSON-объектом.

    Тела-массивы (`[1, 2]`), строки (`"x"`), числа (`5`) роняли 18 роутов в 500:
    каждый роут ждёт словарь и делает `d.get(...)`. Пустые тела и не-JSON не
    трогаем — роуты сами решают, допускать ли их.
    """
    if request.method in ("POST", "PUT", "PATCH") and request.path.startswith("/api/"):
        data = None
        if request.is_json:
            data = request.get_json(silent=True)
        elif request.data and request.data.strip():
            try:
                import json
                data = json.loads(request.data)
            except ReelsiError: raise
            except Exception:
                data = None
        if data is not None and not isinstance(data, dict):
            r = umsg_err(ReelsiError(umsg("bad_body", "Тело запроса должно быть JSON-объектом")))
            return jsonify(**r), 400
    return None          # тело не проверяем — «пропускаем запрос», как и раньше


# Файлы, которые /api/media не отдаёт никогда. Он умеет отдать что угодно с диска —
# это нужно для превью материала, лежащего где попало, — но секреты через него утекать
# не должны ни при каком стечении обстоятельств. Список — только УЧЁТНЫЕ ДАННЫЕ: медиа
# лежит где попало, и запрещать «личное» вообще значит запрещать половину диска.
_NEVER_SERVE = ("ai_config.json", "ai_config.test.json", "rclone.conf")


def _never_serve(path: str) -> bool:
    """Секрет ли это. Помимо имён из _NEVER_SERVE сверяем РЕАЛЬНЫЙ путь конфига rclone:
    он переезжает переменной REELSI_RCLONE_CONF (изолированный профиль на 5098), и там
    лежат токены гугл-диска — по одному имени такой файл не поймать.

    Сверяем basename и присланного пути, и его os.path.realpath: симлинк с
    безобидным именем (например, harmless.png -> ai_config.json) иначе обходит денилист
    секретов. Сегодня сервер и так слушает только localhost (см. _block_dns_rebinding),
    но эта проверка — последний рубеж, который переживёт вынос интерфейса наружу (REMOTE_PLAN).

    Сравнение через os.path.samefile с известными существующими секретами:
    жёсткая ссылка (os.link) оставляет безобидное имя (clip.mp4) и не раскрывается через
    os.path.realpath (указывает напрямую на тот же inode/file index ФС). Сравнение
    по samefile ловит и симлинки, и жёсткие ссылки. Любое OSError при проверке означает,
    что по этому признаку файл секретом не является (имя и realpath проверены ранее)."""
    if not path:
        return False
    if os.path.basename(path).lower() in _NEVER_SERVE:
        return True
    try:
        real = os.path.realpath(path)
    except ReelsiError: raise
    except Exception:
        real = path
    if os.path.basename(real).lower() in _NEVER_SERVE:
        return True
    rc_conf: str | None = None
    try:
        from core.rclone import rclone_conf
        rc_conf = rclone_conf()
        if rc_conf and real == os.path.realpath(rc_conf):
            return True
    except ReelsiError: raise
    except Exception:      # конфига нет / путь не разрешается — имени выше достаточно
        pass  # конфига rclone нет / путь не разрешается — проверки по имени выше хватило
    try:
        if os.path.exists(path):
            candidates: list[str] = []
            try:
                import core.aicut.config as _ai_config
                ai_cfg = getattr(_ai_config, "AI_CONFIG_PATH", None)
                if ai_cfg:
                    candidates.append(ai_cfg)
            except ReelsiError: raise
            except Exception:
                pass  # модуль конфига ИИ не импортировался — путь добавят следующие строки
            try:
                candidates.append(paths.root("ai_config.json"))
                candidates.append(paths.root("ai_config.test.json"))
            except ReelsiError: raise
            except Exception:
                pass  # путь конфига не построился — в списке секретов его просто не будет
            if rc_conf:
                candidates.append(rc_conf)
            for secret in candidates:
                try:
                    if secret and os.path.exists(secret) and os.path.samefile(path, secret):
                        return True
                except OSError:
                    continue
    except OSError:
        pass  # сравнить пути не удалось — считаем, что это не секрет
    return False


def sidecar_path(base: str, suffix: str) -> str:
    """Путь к сайдкару рядом с базовым файлом и проверка его безопасности.

    Строит путь `<stem><suffix>` рядом с `base` (или `<base><suffix>`, если у
    `base` нет расширения).

    Отказывает (ReelsiError), если сайдкар — секрет по `_never_serve` или
    ссылка (симлинк/жёсткая ссылка), уводящая из каталога базового файла
    (realpath сайдкара не в том же каталоге, что realpath базы).
    Обычный файл или отсутствие файла — путь возвращается как есть.
    """
    if not base or not isinstance(base, str) or not base.strip():
        return ""
    clean_base = base.strip().strip('"')
    stem = os.path.splitext(clean_base)[0]
    target = (stem + suffix) if suffix else clean_base

    if _never_serve(target):
        raise ReelsiError(umsg("forbidden_sidecar", f"Недопустимый сайдкар: {target}", path=target))

    if os.path.lexists(target):
        try:
            real_base = os.path.realpath(clean_base)
            real_base_dir = os.path.normcase(os.path.dirname(real_base))
        except ReelsiError: raise
        except Exception:
            real_base_dir = os.path.normcase(os.path.dirname(os.path.abspath(clean_base)))

        try:
            real_target = os.path.realpath(target)
            real_target_dir = os.path.normcase(os.path.dirname(real_target))
        except ReelsiError: raise
        except Exception:
            real_target_dir = os.path.normcase(os.path.dirname(os.path.abspath(target)))

        if real_base_dir != real_target_dir:
            raise ReelsiError(umsg("forbidden_sidecar", f"Недопустимый сайдкар: {target}", path=target))

    return target


def is_reelsi_target(path: Any, kind: str) -> bool:
    """Своя ли цель у роута, который по ней УДАЛЯЕТ: нарезка (`kind="cut"`) или папка вывода (`kind="outdir"`).

    ПОЧЕМУ отдельная функция: путь приезжает в ТЕЛЕ ЗАПРОСА, то есть это данные
    снаружи, а роут по нему сносит файлы. `/api/clip_delete` считал stem от любого
    имени и удалял всё `<stem>.*` рядом — тело `{"xml": ".../Documents/notes.txt"}`
    уносило notes.txt и notes.<что угодно> (внешнее ревью 2026-09-22, P0-2).
    `/api/clean_tmp` отдавал присланный каталог в `draftrender.clean_tmp` и в
    `shutil.rmtree` по `roto/_cache`, то есть чистил чужую папку (P0-3). Последним
    рубежом оставались только localhost и проверка Origin — это защита от чужой
    страницы, а не проверка цели: опечатка в теле, старый клиент или свой скрипт на
    той же машине проходили насквозь. Следующий удаляющий роут должен брать готовое
    отсюда, а не писать заново.

    `cut` — цель обязана быть файлом `.xml` (ровно; регистр не важен), файл
    существует, и это выход нарезки: рядом лежит `<stem>.project.json` ЛИБО корень
    самого XML — `xmeml` (не разобрался ElementTree — не нарезка).

    `outdir` — цель обязана быть каталогом и либо совпадать с папкой вывода
    приложения (`app_out_dir`, как её берут соседние роуты) или лежать ВНУТРИ неё,
    либо содержать хоть один `*.project.json`. Чужая папка под это не подходит, а
    своя пустая (свежая `Reelsi_out` без нарезок) — подходит по пути.

    Функция только отвечает «можно ли трогать»: ничего не удаляет и не бросает
    исключений на кривом пути. Текст ошибки и её umsg-код (`not_a_cut` /
    `not_out_dir`) — дело роута, чтобы сухой прогон и удаление отбивались одинаково.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    if kind == "cut":
        try:
            if os.path.splitext(path)[1].lower() != ".xml" or not os.path.isfile(path):
                return False
            try:
                proj_path = sidecar_path(path, ".project.json")
                if proj_path and os.path.isfile(proj_path):
                    return True
            except ReelsiError:
                pass  # сайдкар-секрет/ссылка наружу → не считаем проектным признаком, дальше решаем по самому XML
            import xml.etree.ElementTree as ET
            # Локальное имя тега: `{ns}xmeml` — всё ещё xmeml, а любая другая
            # ошибка разбора означает мусор, а не нарезку.
            tag = str(ET.parse(path).getroot().tag).split("}")[-1]
        except ReelsiError: raise
        except Exception:
            return False
        return tag.strip().lower() == "xmeml"
    if kind == "outdir":
        if not os.path.isdir(path):
            return False
        try:
            real = os.path.normcase(os.path.realpath(path))
            own = os.path.normcase(os.path.realpath(app_out_dir(DEFAULT_BASE)))
        except (OSError, ValueError):
            real = own = ""
        if own and (real == own or real.startswith(own + os.sep)):
            return True
        return bool(glob.glob(os.path.join(path, "*.project.json")))
    return False

JOB: dict[str, Any] = {
    "running": False, "log": [], "results": [], "failed": [], "done": False, "cancel": False,
    "log_base": 0,   # log_base = сколько строк срезано с начала (для ?since=)
    "kind": "", "label": "", "progress": None,  # kind: cut|draft|build; progress: {"i","n"}
    "stalled": False}   # процесс нарезки молчит дольше CUT_STALL_S
LOCK = threading.Lock()
LOG_CAP = 4000          # ИИ-нарезка стримит тысячи строк — без кэпа лог растёт бесконечно


def emit(line: str, /, **vars: Any) -> None:
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


def _ai_begin(label: str = "") -> int:
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


def _ai_end(ep: int, unload: bool = False) -> None:
    """Освободить вызов. Выгружаем модель ТОЛЬКО если вызов всё ещё актуален —
    устаревший поток этим убил бы генерацию того, кто стартовал после него."""
    global AI_ACTIVE
    from core import aicut
    with LOCK:
        AI_ACTIVE = max(0, AI_ACTIVE - 1)
    if unload and aicut.is_current(ep):
        try:
            aicut.unload_ours()      # освободить VRAM после генерации (только наши модели)
        except ReelsiError: raise
        except Exception as ex:
            log.warning("модели ИИ не выгрузились после генерации: %s — "
                        "видеопамять остаётся занятой", ex)


# Межпроцессный лок задач (job.lock) и журнал заданий (job_state.json) переехали в
# core/jobstate.py: их берут и роуты (через этот модуль), и оркестрация рендера.
# Импорт и имена — в шапке файла; здесь остаётся то, что привязано к ЭКЗЕМПЛЯРАМ
# состояния этого модуля (JOB, LOCK) — их владелец один.


def job_finish() -> None:
    """Джоб закончился: снять running и отпустить межпроцессный лок."""
    with LOCK:
        JOB["running"] = False
        JOB["done"] = True
    journal_finish(JOB)          # закрытая запись в журнале: «не оборвано»
    _cross_lock_release()


def job_start(kind: str = "", label: str = "", **extra: Any) -> bool:
    """Атомарно занять JOB (защита от двойного клика). True = заняли, False = уже идёт.
    kind/label — структурный тип задачи для клиента (никакого сниффинга лога)."""
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, log=[], results=[], failed=[], done=False, cancel=False,
                   log_base=0, kind=kind, label=label, progress=None, insmoved={},
                   items=[], stalled=False, **extra)
    if not _cross_lock_acquire():          # соседний интерфейс уже что-то считает
        with LOCK:
            JOB["running"] = False
        return False
    try:
        from core import aicut
        aicut.clear_cancel()   # прошлый «Стоп» не должен убивать ИИ-шаги нового джоба
    except ReelsiError: raise
    except Exception as ex:
        log.warning("прошлый «Стоп» не сбросился (%s) — новый джоб "
                    "может прерваться на первом ИИ-шаге", ex)
    # Журнал заданий: снимок «что запущено» — сразу, до первого клипа.
    # После перезапуска сервера по нему видно, что задание было и на чём оборвалось.
    journal_bind(JOB, kind=kind or "cut", label=label)
    return True


def set_progress(i: int, n: int, name: str | None = None) -> None:
    """Структурный прогресс джоба (клип i из n) — клиент рисует бар по нему.

    name — имя обрабатываемого файла: без него в оверлее видно «клип i из n», но не
    видно, над каким клипом идёт работа (задание «единый прогресс»). Старые вызовы с
    двумя аргументами работают как раньше: ключа name в прогрессе просто нет.

    Обёртка: сама запись живёт в core/jobstate.py, а словарь JOB и его замок —
    здесь, и владелец у них один.
    """
    jobstate.set_progress(JOB, LOCK, i, n, name)


def set_stalled(flag: bool) -> None:
    """Флаг «процесс нарезки молчит дольше CUT_STALL_S».

    Процесс при этом НЕ убивается: долгая ASR молчит законно. Клиент показывает
    флаг в статусе, чтобы зависшая нарезка была видна, а не выглядела работой.

    Обёртка: запись — в core/jobstate.py, словарь JOB и замок — здесь.
    """
    jobstate.set_stalled(JOB, LOCK, flag)


# Файл состояния UI. Переопределяется через REELSI_UI_STATE — иначе ЛЮБОЙ второй
# инстанс (например тестовый профиль launch.json на 5098) пишет в боевое
# состояние: открыл тестовую страницу, что-то тронул — затёр набор/очередь рабочего.
UI_STATE_PATH = env("UI_STATE") or paths.root("ui_state.json")
