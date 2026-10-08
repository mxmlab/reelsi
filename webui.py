# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Reelsi web UI — ОСНОВНОЙ интерфейс (мастер 3 шагов).

    python reelsi/webui.py        ->  http://127.0.0.1:5001/

Бэкенд (все /api/*) — в пакете api/ (Blueprint). Фронт — templates/index.html +
static/app.css + static/app/*.js (правки фронта — там, сервер перечитывает
шаблон на лету, F5 достаточно). Этот файл — только склейка.
"""
import os, sys, threading, webbrowser
import json as _json
from typing import Any, cast

# Импорт до первого try: сторож `except ReelsiError` ниже обязан видеть это имя.
from core.umsg import ReelsiError, cli_error
# Без консоли (пайп/сервис) stdout у Python — cp1251/cp1252: любой print() русского текста
# (ytmusic «случайная музыка», roto и т.п.) валит запрос UnicodeEncodeError. Чиним на входе.
for _s in (sys.stdout, sys.stderr):
    try:
        cast(Any, _s).reconfigure(encoding="utf-8", errors="replace")
    except ReelsiError: raise
    except Exception:
        pass  # поток без reconfigure — печатаем с errors=replace
HERE = os.path.dirname(os.path.abspath(__file__))
from flask import Flask
from api import bp, DEFAULT_BASE
from core.app_meta import app_js_files, APP_VERSION, ui_lang, dict_en_json, I18N_FILE

app = Flask(__name__)
# Защита от исчерпания памяти при отправке гигантских тел.
# Загрузок файлов нет — только JSON. Крупнейшие реальные JSON-тела (ui_state, наборы
# рендера) занимают не более нескольких мегабайт, ссылки в генерации видео — URL,
# поэтому 32 МБ даёт надёжный запас. 413 отдаёт JSON через @bp.errorhandler(Exception).
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.register_blueprint(bp)

_TPL = os.path.join(HERE, "templates", "index.html")
_TPL_RENDER = os.path.join(HERE, "templates", "render.html")
_STATIC = os.path.join(HERE, "static")
# Файлы, которых на странице рендера быть не должно: 99-boot.js — запуск ОСНОВНОГО
# интерфейса (восстановление состояния, сканы папок, зеркало ui_state каждые 2.5 с).
# У страницы рендера свой запуск: она не показывает панелей и не имеет права писать
# чужое состояние интерфейса.
RENDER_SKIP_JS = ("99-boot.js",)
# Порт UI — модульной константой: его читает не только main(), но и проверка Host
# (api/_core._host_is_local сверяет порт из Host с ЭТИМ портом: у изолированного
# профиля 5098, у копии на другом порту — свой, зашивать 5001 нельзя).
PORT = int(os.environ.get("PORT") or 5001)
_CACHE: dict[str, Any] = {"mtime": None, "html": ""}
_CACHE_RENDER: dict[str, Any] = {"mtime": None, "html": ""}
_I18N = I18N_FILE


def _dict_en() -> str:
    """Словарь перевода — ВСТРАИВАЕТСЯ В СТРАНИЦУ, а не грузится запросом.

    Загрузка через fetch создаёт гонку: часть интерфейса (строки камер, статус
    нарезки) успевает отрисоваться раньше, чем словарь приехал, и остаётся на
    русском. Пост-обход DOM это не чинит — строки с подстановкой («Камера 1»)
    собраны из шаблона «Камера {n}» и ни одному ключу уже не равны.
    Встроенный словарь доступен синхронно, до первой отрисовки.
    """
    return dict_en_json()


def _app_scripts(v: int | str, skip: tuple[str, ...] = ()) -> str:
    """<script>-теги интерфейса в порядке загрузки.

    Список берётся из папки (app_meta.app_js_files), а не выписан в шаблоне: иначе
    добавленный файл легко не подключить, и часть интерфейса тихо перестала бы
    работать — без ошибки в консоли, просто «кнопка не нажимается».

    `skip` — имена файлов, которых на странице быть не должно (см. RENDER_SKIP_JS):
    файл из папки всё равно подхватывается везде, где он нужен, а тут отсекается
    по имени, а не выписыванием списка руками.
    """
    return "\n".join(
        '<script src="/static/app/%s?v=%s"></script>' % (os.path.basename(p), v)
        for p in app_js_files() if os.path.basename(p) not in skip)


def _page() -> str:
    """index.html + подстановки. Кэш по mtime — правка шаблона видна по F5 без рестарта."""
    mt = max([os.path.getmtime(_TPL),
              os.path.getmtime(os.path.join(_STATIC, "app.css")),
              os.path.getmtime(_I18N) if os.path.exists(_I18N) else 0]
             + [os.path.getmtime(p) for p in app_js_files()])
    if _CACHE["mtime"] != mt:
        html = open(_TPL, encoding="utf-8").read()
        html = html.replace("__BASE_JSON__", _json.dumps(DEFAULT_BASE))
        html = html.replace("__I18N_EN__", _dict_en())
        html = html.replace("__UI_LANG__", ui_lang())
        html = html.replace("__APP_JS__", _app_scripts(int(mt)))
        html = html.replace("__APP_VERSION__", APP_VERSION)
        html = html.replace("__V__", str(int(mt)))          # cache-busting статики
        _CACHE.update(mtime=mt, html=html)
    return _CACHE["html"]


@app.route("/")
def index() -> str:
    return _page()


def _page_render() -> str:
    """templates/render.html + подстановки. Кэш по mtime — как у главной страницы.

    Страница рендера грузит ТЕ ЖЕ файлы static/app/, кроме запуска основного
    интерфейса (см. RENDER_SKIP_JS): отрисовка кадра у неё общая с предпросмотром,
    а панели и состояние — свои, и чужие ей не нужны.
    """
    mt = max([os.path.getmtime(_TPL_RENDER),
              os.path.getmtime(os.path.join(_STATIC, "app.css")),
              os.path.getmtime(_I18N) if os.path.exists(_I18N) else 0]
             + [os.path.getmtime(p) for p in app_js_files()])
    if _CACHE_RENDER["mtime"] != mt:
        html = open(_TPL_RENDER, encoding="utf-8").read()
        html = html.replace("__BASE_JSON__", _json.dumps(DEFAULT_BASE))
        html = html.replace("__I18N_EN__", _dict_en())
        html = html.replace("__UI_LANG__", ui_lang())
        html = html.replace("__APP_JS__", _app_scripts(int(mt), RENDER_SKIP_JS))
        html = html.replace("__V__", str(int(mt)))          # cache-busting статики
        _CACHE_RENDER.update(mtime=mt, html=html)
    return cast(str, _CACHE_RENDER["html"])


@app.route("/render")
def render_page() -> str:
    """Страница рендера без AE: сцена в натуральном размере кадра, кадр за кадром.

    Параметры: `xml` — клип, `body` — id тела сборки (его кладёт POST /api/render_body),
    `pxh` — короткая сторона прокси камер. Кадры снимает core/webrender/capture.mjs.
    """
    return _page_render()


def _cleanup_on_exit() -> None:
    """Завершение дочерних процессов при выходе сервера (atexit).

    Освобождает VRAM: процессы нарезки (omni_cut/ASR) и aerender/After Effects.
    Хук безопасен и не падает, если активных процессов нет.
    """
    try:
        from api.jobs import _kill_curproc
        _kill_curproc()
    except ReelsiError: raise
    except Exception:
        pass  # активных процессов нет — гасить нечего
    try:
        from api.render import RPROC, RLOCK, _kill_proc
        with RLOCK:
            p = RPROC
        if p and getattr(p, "poll", lambda: None)() is None:
            _kill_proc(p)
    except ReelsiError: raise
    except Exception:
        pass  # рендер не запущен — гасить нечего


def _drop_stale_model_service() -> bool:
    """Погасить сервис моделей ДРУГОЙ ВЕРСИИ, оставшийся от прошлого запуска.

    После обновления кода на машине может жить сервис прежней версии — он держит веса
    и отвечает старым кодом, пока не выйдет по простою (пять минут). Первый же запрос
    клипа пошёл бы к нему. Поэтому старт сервера гасит такой сервис (см.
    `core.model_service.drop_stale`): «поправил, а не работает» больше не ждёт простоя.

    Живой сервис СВОЕЙ версии не трогаем, и новый здесь не заводим: старт интерфейса
    ничего не считает, а поднятый процесс держал бы видеопамять до простоя. Ошибка
    (нет прав на файл, чужой процесс не отозвался) — строка в логе сервера, а не
    отказ запуска интерфейса.
    """
    from core import model_service
    try:
        return bool(model_service.drop_stale())
    except ReelsiError:
        raise
    except Exception as e:
        print(f"  сервис моделей: старую версию не погасил ({e})")
        return False


def main() -> None:
    import atexit
    from core.applog import get_logger
    from core.paths import require_source_tree
    require_source_tree()
    atexit.register(_cleanup_on_exit)
    from core import bootstrap
    for _msg in bootstrap.ensure_user_files():
        print(f"  + {_msg}")
    _drop_stale_model_service()
    port = PORT
    url = f"http://127.0.0.1:{port}"
    print(f"Reelsi Web UI v{APP_VERSION} -> {url}")
    log = get_logger("reelsi")
    from core import crashtrace
    crashtrace.install(log, port=port)
    log.info(f"Reelsi Web UI v{APP_VERSION} -> {url} (port {port})")
    if not (os.environ.get("REELSI_NO_BROWSER") or os.environ.get("AUTOCUT_NO_BROWSER")):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except ReelsiError as e:
        cli_error(e)
