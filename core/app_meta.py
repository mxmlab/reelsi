# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# Single source of truth for Reelsi's public identity (OpenRouter display + web UI).
# Importable both as a script (top-level `app_meta`) and as a package module
# (`reelsi.app_meta`). Kept dependency-free (standard library only).
import json
import os
import sys
import urllib.request

from core import paths

APP_NAME = "Reelsi"
APP_VERSION = "0.1.0-beta"
APP_REFERER = "https://mxmlab.si/reelsi"

# Дефолтный Python-urllib User-Agent режется Cloudflare (403, error code 1010)
# у провайдеров вроде commandcode.ai.
APP_UA = f"{APP_NAME}/{APP_VERSION}"


def http_req(url, data=None, headers=None, method=None):
    """Обертка над urllib.request.Request с дефолтным User-Agent.

    Если заголовок User-Agent (без учета регистра) отсутствует в headers,
    подставляет APP_UA, чтобы запросы не блокировались Cloudflare (error 1010).
    Исходный словарь headers не мутируется.
    """
    h = dict(headers) if headers else {}
    if not any(k.lower() == "user-agent" for k in h):
        h["User-Agent"] = APP_UA
    kwargs = {}
    if method is not None:
        kwargs["method"] = method
    return urllib.request.Request(url, data=data, headers=h, **kwargs)

OUT_DIR_NAME = "Reelsi_out"
_OUT_DIR_LEGACY = "AutoCut_out"


def out_dir(base):
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


def is_out_dir(name):
    """Имя папки — это папка результата? Оба имени, старое и новое."""
    return (name or "").lower() in (OUT_DIR_NAME.lower(), _OUT_DIR_LEGACY.lower())


def env(name, default=None):
    """Переменная окружения `REELSI_<name>`, со старым `AUTOCUT_<name>` как запасным.

    Проект переименован из AutoCut в Reelsi, а старые имена могли остаться в
    launch.json, ярлыках и скриптах юзера — молча их игнорировать значит менять
    поведение на ровном месте. Новое имя приоритетнее, старое ещё работает.
    """
    return (os.environ.get("REELSI_" + name)
            or os.environ.get("AUTOCUT_" + name)
            or default)


_PY_EXEC_CACHED = None


def py_exec():
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


def module_cmd(name, *args, unbuffered=False):
    """Команда запуска модуля ядра подпроцессом: [python, (-u)?, -m, core.<name>, *args].

    ПОЧЕМУ через `-m`, а не по пути к файлу (задание GU): движок переехал в пакет
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


def child_env(env=None):
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


_UI_LANG_CACHED = None


def _detect_sys_lang():
    """Определение системного языка интерфейса (без учета REELSI_LANG)."""
    try:
        # Windows:
        if os.name == "nt" or sys.platform == "win32":
            try:
                import ctypes
                lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
                if (lang_id & 0x3FF) == 0x19:
                    return "ru"
                return "en"
            except Exception:
                pass

        # Posix / Linux / macOS (или fallback):
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            val = os.environ.get(var)
            if val:
                val = val.strip().lower()
                if val.startswith("ru"):
                    return "ru"
                return "en"

        return "en"
    except Exception:
        return "en"


def ui_lang(force_reload=False):
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
    except Exception:
        res = "en"

    _UI_LANG_CACHED = res
    return res


I18N_FILE = os.path.join(paths.ROOT, "static", "i18n", "en.json")
_I18N_DICT = None


def dict_en_json():
    """Сырой JSON словаря перевода для встраивания в HTML."""
    try:
        with open(I18N_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "{}"


def _get_i18n_dict():
    """Ленивая загрузка словаря в dict (только при ui_lang() == 'en')."""
    global _I18N_DICT
    if _I18N_DICT is None:
        try:
            raw = dict_en_json()
            _I18N_DICT = json.loads(raw) if raw else {}
        except Exception:
            _I18N_DICT = {}
    return _I18N_DICT


def t(key, **vars):
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
        except Exception:
            for k, v in vars.items():
                val = val.replace("{" + str(k) + "}", str(v))
    return val


def console_emit(line="", **vars):
    """Консольный вывод логов с переводом по словарю static/i18n/en.json.

    Единый дефолт `emit=console_emit` для продуктовых модулей вместо `emit=print`.
    Понимает и старую форму emit('строка'), и новую emit('Шаблон {var}', var=val).
    """
    print(t(line, **vars) if line else "", flush=True)


def wrap_emit(emit_fn=None):
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
    def _e(line="", **vars):
        if not vars:
            try:
                return emit_fn(line)
            except TypeError:
                return None
        try:
            formatted = line.format(**vars)
        except Exception:
            formatted = line
            for k, v in vars.items():
                formatted = formatted.replace("{" + str(k) + "}", str(v))
        try:
            return emit_fn(formatted)
        except TypeError:
            try:
                return emit_fn(line, **vars)
            except Exception:
                return None
    return _e


APP_JS_DIR = os.path.join(paths.ROOT, "static", "app")


def app_js_files():
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


def app_js_text():
    """Весь код интерфейса одной строкой — для тестов и сборщика строк перевода."""
    return "\n".join(open(p, encoding="utf-8").read() for p in app_js_files())


__all__ = ["APP_NAME", "APP_VERSION", "APP_REFERER", "APP_UA", "http_req", "env", "out_dir", "is_out_dir", "OUT_DIR_NAME",
           "APP_JS_DIR", "app_js_files", "app_js_text", "ui_lang", "t", "dict_en_json", "I18N_FILE",
           "console_emit", "wrap_emit"]

