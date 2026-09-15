# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание HM, пункт 3: необработанное исключение в роуте → JSON, а не HTML-500.

В `api/` не было ни одного `errorhandler`: любое исключение вне `try` отдавало
HTML-страницу Flask. Фронт на КАЖДОМ ответе делает `.json()` и читает
`{error, err, err_vars}` (`static/app/00-core.js`, `errText`) — на HTML разбор
падал исключением, и пользователь не видел ни текста ошибки, ни кода.

Здесь стерегутся три вещи:
  * 500 приходит JSON-ом в форме `umsg_err`, причина — в stderr сервера;
  * `HTTPException` из недр роута сохраняет свой код (404 остаётся 404);
  * 404 на НЕСУЩЕСТВУЮЩИЙ `/api/...` остаётся прежним: это ошибка
    маршрутизации, `request.blueprint` у неё пуст, и обработчик Blueprint её не
    видит (проверено здесь, чтобы поведение не поменялось молча).

Запуск:  python -m pytest tests -q
"""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

# Точка отказа — view-функция УЖЕ зарегистрированного роута. Свой роут ради теста
# добавлять нельзя: он остался бы в Blueprint на весь прогон.
BOOM_ENDPOINT = "api.api_cutstages"
URL = "/api/cutstages"


@pytest.fixture
def app():
    from flask import Flask
    import api
    a = Flask(__name__)
    a.register_blueprint(api.bp)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def test_исключение_в_роуте_даёт_500_json(app, client, capsys):
    def boom():
        raise RuntimeError("бум в роуте")

    app.view_functions[BOOM_ENDPOINT] = boom
    r = client.get(URL)

    assert r.status_code == 500
    d = r.get_json()
    assert d, f"тело не JSON: {r.data[:200]!r}"
    assert d["err"] == "internal_error", d
    assert d["err_vars"] == {"err": "RuntimeError: бум в роуте"}, d
    assert "бум в роуте" in d["error"], d
    # Причина обязана остаться в stderr сервера: в лог джоба исключение роута не попадает.
    assert "Traceback" in capsys.readouterr().err


def test_http_exception_сохраняет_код(app, client, capsys):
    """404 из недр роута — по-прежнему 404, но телом JSON (фронт разбирает его
    тем же errText; раньше на этом месте была HTML-страница)."""
    from flask import abort

    def not_found():
        abort(404)

    app.view_functions[BOOM_ENDPOINT] = not_found
    r = client.get(URL)

    assert r.status_code == 404
    d = r.get_json()
    assert d and d["err"] == "http_error", d
    assert d["err_vars"] == {"code": 404}, d
    assert capsys.readouterr().err == "", "HTTPException — не повод печатать трейсбек"


def test_код_ответа_не_подменяется_на_500(app, client):
    """405 (метод не тот) — код от werkzeug, а не 500: гвард не должен превращать
    любую ошибку запроса в «внутреннюю ошибку сервера»."""
    r = client.post(URL)                       # роут только GET
    assert r.status_code == 405


def test_несуществующий_api_роут_остаётся_прежним_404(client):
    """Ошибка МАРШРУТИЗАЦИИ приходит не из роута: `request.blueprint` пуст, и
    обработчик Blueprint её не видит — тело остаётся HTML werkzeug.

    Это то, чего ждёт фронт: `static/app/40-queue.js:511` на 404 подставляет
    `t('не найден')` и разбирает тело через try/catch, так что HTML его не
    ломает. Поменялось бы это поведение — надо было бы менять и фронт."""
    r = client.get("/api/нет_такого_роута")
    assert r.status_code == 404
    assert r.get_json(silent=True) is None, "404 маршрутизации неожиданно стал JSON"
