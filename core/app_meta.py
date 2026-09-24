# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# Single source of truth for Reelsi's public identity (OpenRouter display + web UI).
# Importable both as a script (top-level `app_meta`) and as a package module
# (`reelsi.app_meta`). Kept dependency-free (standard library only).
import copy
import http.client
import ipaddress
import json
import os
import socket
import sys
import urllib.parse
import urllib.request
from typing import IO, Any, Callable

from core import paths
from core.umsg import ReelsiError

APP_NAME = "Reelsi"
APP_VERSION = "0.2.1-beta"
APP_REFERER = "https://mxmlab.si/reelsi"

# Дефолтный Python-urllib User-Agent режется Cloudflare (403, error code 1010)
# у провайдеров вроде commandcode.ai.
APP_UA = f"{APP_NAME}/{APP_VERSION}"


def http_req(url: str, data: bytes | None = None, headers: dict[str, str] | None = None,
             method: str | None = None) -> urllib.request.Request:
    """Обертка над urllib.request.Request с дефолтным User-Agent.

    Если заголовок User-Agent (без учета регистра) отсутствует в headers,
    подставляет APP_UA, чтобы запросы не блокировались Cloudflare (error 1010).
    Исходный словарь headers не мутируется.
    """
    h = dict(headers) if headers else {}
    if not any(k.lower() == "user-agent" for k in h):
        h["User-Agent"] = APP_UA
    kwargs: dict[str, Any] = {}
    if method is not None:
        kwargs["method"] = method
    return urllib.request.Request(url, data=data, headers=h, **kwargs)


# Причины отказа по адресу: свойства ipaddress проверяются по порядку, первое
# совпадение и выигрывает. is_private у 169.254.169.254 тоже истинно (link-local
# входит в приватные диапазоны), а у 0.0.0.0 — как у всей нулевой сети, поэтому
# link-local и unspecified стоят раньше private: иначе про метаданные облака и про
# «нулевой» адрес в отказе было бы сказано невнятно.
_UNSAFE_IP = (
    ("is_loopback", "loopback (твоя же машина)"),
    ("is_link_local", "link-local (метаданные облака)"),
    ("is_unspecified", "неопределённый адрес (0.0.0.0 или ::)"),
    ("is_private", "внутренний адрес локальной сети"),
    ("is_reserved", "зарезервированный адрес"),
    ("is_multicast", "multicast-адрес"),
)


def unsafe_url_reason(url: str) -> str:
    """Почему по этому адресу скачивать НЕЛЬЗЯ; '' — адрес прошёл проверку.

    Адрес готового ролика приходит в ОТВЕТЕ внешней службы (OpenRouter отдаёт
    ссылки в `unsigned_urls`), а не от пользователя: враждебный или взломанный
    провайдер подсунул бы `file:///C:/Windows/win.ini` (читается локальный файл)
    или `http://127.0.0.1`/`http://169.254.169.254` (запрос уходит внутрь машины и
    в локальную сеть). Поэтому проверяются и схема, и КАЖДЫЙ адрес, в который
    резолвится имя: проверка одной строки хоста обходится именем, которое
    резолвится в приватный IP.

    Запросы к самому провайдеру этой проверкой не задеваются: его `base_url`
    выбирает пользователь, и `http://127.0.0.1:1234` (LM Studio) там штатен — зовут
    её только из пути скачивания готового ролика.

    Наружу отдаётся готовая причина текстом, а не исключение: вызывающий сам решает,
    отказ это (пробуем следующий адрес) или повод остановиться.
    """
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:                       # `http://[::1/x` и прочий мусор
        return "небезопасный адрес: ссылку не разобрать"
    if parts.scheme.lower() not in ("http", "https"):
        # data:, ftp:, пустая схема — тоже отказ: ролик качается только по сети
        return f"небезопасный адрес: схема «{parts.scheme or '—'}», а не http/https"
    host = parts.hostname or ""
    if not host:
        return "небезопасный адрес: в ссылке нет хоста"
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError) as e:
        # имя не резолвится — скачать по нему всё равно нельзя
        return f"небезопасный адрес: имя «{host}» не резолвится ({e})"
    if not infos:
        return f"небезопасный адрес: имя «{host}» не резолвится"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (IndexError, ValueError):
            return f"небезопасный адрес: имя «{host}» резолвится в непонятный адрес"
        for attr, why in _UNSAFE_IP:
            if getattr(ip, attr):
                return f"небезопасный адрес: {ip} — {why}"
    return ""


class UnsafeAddress(OSError):
    """Адрес не прошёл проверку — это отказ, а не сбой сети.

    Наследник OSError нарочно: в переборе адресов (core/aicut/video.py) ветка
    `except (TimeoutError, OSError)` берёт СЛЕДУЮЩИЙ адрес, а не роняет уже
    оплаченную задачу.
    """


# Заголовки-удостоверения: имя пользователя и его ключи. При редиректе на ДРУГОЙ хост
# их переносить нельзя — `HTTPRedirectHandler` переносит все заголовки, кроме
# `Content-*`, и ключ провайдера уехал бы чужому серверу.
_AUTH_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


def _url_host(url: str) -> str:
    """Хост ссылки без порта, в нижнем регистре; '' — если ссылку не разобрать.

    Порт нарочно не берём: `https://api.example.test:8443` — тот же хозяин, что и
    `https://api.example.test`, просто другая дверь, и подписанная ссылка провайдера
    ключ там всё ещё ждёт.
    """
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:                       # `http://[::1/x` и прочий мусор
        return ""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Перехватчик редиректов для скачивания по чужому адресу.

    Проверить только стартовый адрес мало: честная внешняя ссылка отвечает 302 на
    `http://127.0.0.1:...` — и запрос уходит внутрь машины. Здесь КАЖДЫЙ новый адрес
    проходит ту же проверку, что и стартовый, а отказ — исключение, которое перебор
    адресов ловит как обычную неудачу.

    И второе: `HTTPRedirectHandler.redirect_request` переносит в новый запрос ВСЕ
    заголовки, кроме `Content-*`. Редирект на другой хост означал бы, что вместе с
    запросом туда уехал и `Authorization` — то есть ключ провайдера чужому серверу.
    Поэтому на чужой хост новый запрос собирается уже без авторизационных заголовков.
    """

    def redirect_request(self, req: urllib.request.Request, fp: IO[bytes], code: int,
                         msg: str, headers: http.client.HTTPMessage,
                         newurl: str) -> urllib.request.Request | None:
        # порядок важен: сначала небезопасный адрес (запрос внутрь машины), и только
        # потом заголовки — на адрес, который и так не годится, ключ тем более не шлём
        reason = unsafe_url_reason(newurl)
        if reason:
            raise UnsafeAddress(reason)
        if _url_host(newurl) == _url_host(req.full_url):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        # Заголовки переносит сам `HTTPRedirectHandler.redirect_request`, и берёт он их
        # из `req.headers`. Поэтому ему отдаётся копия запроса, у которой
        # авторизационных заголовков уже нет: новый Request собирается БЕЗ них, а не
        # чистится после сборки. Остальные заголовки (User-Agent и прочие) остаются.
        clean = copy.copy(req)
        clean.headers = {k: v for k, v in req.headers.items()
                         if k.lower() not in _AUTH_HEADERS}
        return super().redirect_request(clean, fp, code, msg, headers, newurl)


OUT_DIR_NAME = "Reelsi_out"
_OUT_DIR_LEGACY = "AutoCut_out"


def out_dir(base: str) -> str:
    """Папка результата рядом с материалом.

    Проект переименован из AutoCut в Reelsi, но на диске у пользователя уже лежит
    `AutoCut_out` с готовыми проектами. Просто сменить имя = сделать вид, что всей
    его прошлой работы нет: интерфейс открылся бы на пустой папке, а `.project.json`
    перестали бы находиться. Поэтому: есть `Reelsi_out` — работаем в ней, нет, но
    есть старая — работаем в старой, нет ни одной (чистая установка) — заводим новую.
    """
    if os.path.isdir(os.path.join(base, OUT_DIR_NAME)):
        return os.path.join(base, OUT_DIR_NAME)
    if os.path.isdir(os.path.join(base, _OUT_DIR_LEGACY)):
        return os.path.join(base, _OUT_DIR_LEGACY)
    return os.path.join(base, OUT_DIR_NAME)


def is_out_dir(name: str) -> bool:
    """Имя папки — это папка результата? Оба имени, старое и новое."""
    return (name or "").lower() in (OUT_DIR_NAME.lower(), _OUT_DIR_LEGACY.lower())


def env(name: str, default: str | None = None) -> str | None:
    """Переменная окружения `REELSI_<name>`, со старым `AUTOCUT_<name>` как запасным.

    Проект переименован из AutoCut в Reelsi, а старые имена могли остаться в
    launch.json, ярлыках и скриптах юзера — молча их игнорировать значит менять
    поведение на ровном месте. Новое имя приоритетнее, старое ещё работает.
    """
    return (os.environ.get("REELSI_" + name)
            or os.environ.get("AUTOCUT_" + name)
            or default)


_PY_EXEC_CACHED: str | None = None


def py_exec() -> str:
    """Путь к рабочему интерпретатору Python (с установленным torch/gigaam).

    Проект требует Python 3.10. Если текущий процесс запущен под другим Python
    (например, 3.13 в Windows PATH), фоновые ML-подпроцессы (нарезка, ASR) упадут с
    ModuleNotFoundError: No module named 'gigaam'/'torch'.
    Эта функция находит рабочий Python 3.10 в системе или возвращает sys.executable.
    """
    global _PY_EXEC_CACHED
    if _PY_EXEC_CACHED:
        return _PY_EXEC_CACHED

    custom = env("PYTHON")
    if custom and os.path.exists(custom):
        _PY_EXEC_CACHED = custom
        return custom

    if sys.version_info[:2] == (3, 10):
        _PY_EXEC_CACHED = sys.executable
        return sys.executable

    if os.name == "nt":
        candidates = []
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(os.path.join(local_app, "Programs", "Python", "Python310", "python.exe"))
        user_prof = os.environ.get("USERPROFILE")
        if user_prof:
            candidates.append(os.path.join(user_prof, "AppData", "Local", "Programs", "Python", "Python310", "python.exe"))
        prog_files = os.environ.get("ProgramFiles")
        if prog_files:
            candidates.append(os.path.join(prog_files, "Python310", "python.exe"))

        for c in candidates:
            if os.path.exists(c):
                _PY_EXEC_CACHED = c
                return c

    import shutil
    p310 = shutil.which("python3.10")
    if p310:
        _PY_EXEC_CACHED = p310
        return p310

    _PY_EXEC_CACHED = sys.executable
    return sys.executable


def module_cmd(name: str, *args: object, unbuffered: bool = False) -> list[str]:
    """Команда запуска модуля ядра подпроцессом: [python, (-u)?, -m, core.<name>, *args].

    ПОЧЕМУ через `-m`, а не по пути к файлу: движок переехал в пакет
    `core/`, и путь к файлу пришлось бы склеивать от папки того модуля, который
    запускает, — заново в каждом месте и заново при следующем переезде. `-m` вместе
    с PYTHONPATH из child_env работает из любой рабочей папки.
    """
    cmd = [py_exec()]
    if unbuffered:
        cmd.append("-u")
    cmd += ["-m", "core." + name]
    cmd += [str(a) for a in args]
    return cmd


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение подпроцесса с корнем репозитория в PYTHONPATH.

    Без него `-m core.omni_cut` не найдёт пакет: подпроцесс стартует с тем же cwd,
    что и родитель, а интерфейс поднимают из любой папки.
    """
    e = dict(os.environ if env is None else env)
    parts = [p for p in (e.get("PYTHONPATH") or "").split(os.pathsep) if p]
    if paths.ROOT not in parts:
        parts.insert(0, paths.ROOT)
    e["PYTHONPATH"] = os.pathsep.join(parts)
    return e


_UI_LANG_CACHED: str | None = None


def _detect_sys_lang() -> str:
    """Определение системного языка интерфейса (без учета REELSI_LANG)."""
    try:
        # Windows:
        if os.name == "nt" or sys.platform == "win32":
            try:
                import ctypes
                # windll есть только в Windows-сборках ctypes, а getattr — потому
                # что mypy в Linux-режиме (CI) этого атрибута в типах не видит, а
                # `# type: ignore` под Windows оказался бы лишним (warn_unused_ignores).
                windll = getattr(ctypes, "windll", None)
                if windll is not None:
                    lang_id = windll.kernel32.GetUserDefaultUILanguage()
                    if (lang_id & 0x3FF) == 0x19:
                        return "ru"
                    return "en"
            except ReelsiError: raise
            except Exception:
                pass  # ctypes недоступен — язык определим по переменным окружения

        # Posix / Linux / macOS (или fallback):
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            val = os.environ.get(var)
            if val:
                val = val.strip().lower()
                if val.startswith("ru"):
                    return "ru"
                return "en"

        return "en"
    except ReelsiError: raise
    except Exception:
        return "en"


def ui_lang(force_reload: bool = False) -> str:
    """Язык интерфейса по умолчанию ('ru' или 'en'). Единый источник для бэкенда и фронта.

    Приоритет:
    1. REELSI_LANG / AUTOCUT_LANG ('ru'/'en') — явное переопределение.
    2. Системный язык:
       - Windows: GetUserDefaultUILanguage() & 0x3ff == 0x19 -> 'ru'
       - Linux/macOS: LC_ALL / LC_MESSAGES / LANG / LANGUAGE -> 'ru'
    3. Не определилось — 'en'.
    Никогда не падает: любое исключение внутри — 'en'.
    Результат кэшируется в модуле.
    """
    global _UI_LANG_CACHED
    if not force_reload and _UI_LANG_CACHED is not None:
        return _UI_LANG_CACHED

    try:
        e = env("LANG")
        if e:
            e = e.strip().lower()
            res = "ru" if e.startswith("ru") else "en"
        else:
            res = _detect_sys_lang()
    except ReelsiError: raise
    except Exception:
        res = "en"

    _UI_LANG_CACHED = res
    return res


I18N_FILE = os.path.join(paths.ROOT, "static", "i18n", "en.json")
_I18N_DICT: dict[str, Any] | None = None


def dict_en_json() -> str:
    """Сырой JSON словаря перевода для встраивания в HTML."""
    try:
        with open(I18N_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "{}"


def _get_i18n_dict() -> dict[str, Any]:
    """Ленивая загрузка словаря в dict (только при ui_lang() == 'en')."""
    global _I18N_DICT
    if _I18N_DICT is None:
        try:
            raw = dict_en_json()
            _I18N_DICT = json.loads(raw) if raw else {}
        except ReelsiError: raise
        except Exception:
            _I18N_DICT = {}
    return _I18N_DICT


def t(key: str, **vars: Any) -> str:
    """Перевод строки по словарю static/i18n/en.json.

    Ключ — сам русский текст. При ui_lang() == 'ru' словарь даже не читается
    (fallback = ключ). При 'en' берётся значение из словаря или сам ключ.
    Подстановка {name} работает как в JS t().
    """
    if not key:
        return ""
    key_str = str(key)
    if ui_lang() == "en":
        val = _get_i18n_dict().get(key_str, key_str)
    else:
        val = key_str
    if vars:
        try:
            val = val.format(**vars)
        except ReelsiError: raise
        except Exception:
            for k, v in vars.items():
                val = val.replace("{" + str(k) + "}", str(v))
    return val


def console_emit(line: str = "", **vars: Any) -> None:
    """Консольный вывод логов с переводом по словарю static/i18n/en.json.

    Единый дефолт `emit=console_emit` для продуктовых модулей вместо `emit=print`.
    Понимает и старую форму emit('строка'), и новую emit('Шаблон {var}', var=val).
    """
    print(t(line, **vars) if line else "", flush=True)


def wrap_emit(emit_fn: Callable[..., Any] | None = None) -> Callable[..., Any]:
    """Обернуть колбэк лога, чтобы он безопасно принимал как старую форму emit('текст'),
    так и новую emit('Шаблон {var}', var=val). Если переданный колбэк не принимает
    произвольные **vars (например lambda s: None, lambda *a: None или print),
    он получит строку с подставленными переменными."""
    if not emit_fn:
        return console_emit
    if emit_fn is console_emit:
        return console_emit
    fn_name = getattr(emit_fn, "__name__", "")
    fn_mod = getattr(emit_fn, "__module__", "") or ""
    if fn_name in ("emit", "remit", "_emit") and ("api." in fn_mod or fn_mod.startswith("api")):
        return emit_fn
    def _e(line: str = "", **vars: Any) -> Any:
        if not vars:
            try:
                return emit_fn(line)
            except TypeError:
                return None
        try:
            formatted = line.format(**vars)
        except ReelsiError: raise
        except Exception:
            formatted = line
            for k, v in vars.items():
                formatted = formatted.replace("{" + str(k) + "}", str(v))
        try:
            return emit_fn(formatted)
        except TypeError:
            try:
                return emit_fn(line, **vars)
            except ReelsiError: raise
            except Exception:
                return None
    return _e


APP_JS_DIR = os.path.join(paths.ROOT, "static", "app")


def app_js_files() -> list[str]:
    """Файлы интерфейса в ПОРЯДКЕ ЗАГРУЗКИ (он же — порядок имён).

    До 2026-08-06 это был один `static/app.js` на 3827 строк. Теперь их четырнадцать,
    и грузятся они обычными <script>-тегами в общий скоуп — то есть порядок значим:
    объявления функций поднимаются в пределах СВОЕГО файла, а не всего набора.

    Порядок задан числовым префиксом в имени, а список берётся из папки, а не
    выписан руками. Так его нельзя разойтись: страница, тест полноты перевода,
    сборщик строк и проверка стиля читают одно и то же. Добавил файл — он подхватился
    везде; забыть дописать его куда-то невозможно.
    """
    if not os.path.isdir(APP_JS_DIR):
        return []
    return [os.path.join(APP_JS_DIR, f)
            for f in sorted(os.listdir(APP_JS_DIR)) if f.endswith(".js")]


def app_js_text() -> str:
    """Весь код интерфейса одной строкой — для тестов и сборщика строк перевода."""
    return "\n".join(open(p, encoding="utf-8").read() for p in app_js_files())


__all__ = ["APP_NAME", "APP_VERSION", "APP_REFERER", "APP_UA", "http_req", "env", "out_dir", "is_out_dir", "OUT_DIR_NAME",
           "APP_JS_DIR", "app_js_files", "app_js_text", "ui_lang", "t", "dict_en_json", "I18N_FILE",
           "console_emit", "wrap_emit"]

