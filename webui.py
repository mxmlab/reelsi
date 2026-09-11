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
# Без консоли (пайп/сервис) stdout у Python — cp1251/cp1252: любой print() русского текста
# (ytmusic «случайная музыка», roto и т.п.) валит запрос UnicodeEncodeError. Чиним на входе.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
HERE = os.path.dirname(os.path.abspath(__file__))
from flask import Flask
from api import bp, DEFAULT_BASE
from core.app_meta import app_js_files, APP_VERSION, ui_lang, dict_en_json, I18N_FILE

app = Flask(__name__)
app.register_blueprint(bp)

_TPL = os.path.join(HERE, "templates", "index.html")
_STATIC = os.path.join(HERE, "static")
_CACHE = {"mtime": None, "html": ""}
_I18N = I18N_FILE


def _dict_en():
    """Словарь перевода — ВСТРАИВАЕТСЯ В СТРАНИЦУ, а не грузится запросом.

    Загрузка через fetch создаёт гонку: часть интерфейса (строки камер, статус
    нарезки) успевает отрисоваться раньше, чем словарь приехал, и остаётся на
    русском. Пост-обход DOM это не чинит — строки с подстановкой («Камера 1»)
    собраны из шаблона «Камера {n}» и ни одному ключу уже не равны.
    Встроенный словарь доступен синхронно, до первой отрисовки.
    """
    return dict_en_json()


def _app_scripts(v):
    """<script>-теги интерфейса в порядке загрузки.

    Список берётся из папки (app_meta.app_js_files), а не выписан в шаблоне: иначе
    добавленный файл легко не подключить, и часть интерфейса тихо перестала бы
    работать — без ошибки в консоли, просто «кнопка не нажимается».
    """
    return "\n".join(
        '<script src="/static/app/%s?v=%s"></script>' % (os.path.basename(p), v)
        for p in app_js_files())


def _page():
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
def index():
    return _page()


if __name__ == "__main__":
    from core import bootstrap
    for _msg in bootstrap.ensure_user_files():
        print(f"  + {_msg}")
    port = int(os.environ.get("PORT") or 5001)
    url = f"http://127.0.0.1:{port}"
    print(f"Reelsi Web UI v{APP_VERSION} -> {url}")
    if not (os.environ.get("REELSI_NO_BROWSER") or os.environ.get("AUTOCUT_NO_BROWSER")):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(port=port, threaded=True, use_reloader=False)
