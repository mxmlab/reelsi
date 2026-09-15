# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты безопасности (задание HL, ветка feat/sec-p0).

1. `WORK_DIR=`: путь из stdout пускается в CURWORK только если это рабочий каталог
   нарезки в %TEMP% (omnicut_*/gigaamcut_*). Иначе «Стоп» сносил rmtree ЛЮБОЙ путь,
   который напечатал ответ модели переводом строки в `notes`.
2. XSS в static/app/*.js: данные с сервера, диска и от модели попадают в разметку
   только через esc(); данные в обработчики — только через data-* (esc() внутри
   onclick не спасает: браузер раскодирует &#39; в атрибуте ДО компиляции JS).
3. Сохранённый ключ: маска «•••…» заставляет брать из профиля ВЕСЬ набор
   (api_key, base_url, headers) и игнорировать base_url/headers из тела запроса —
   иначе чужой адрес в теле уводил настоящий ключ на свою сторону.
4. `int(query)` не роняет роут в HTML-500: ?pps=abc и ?since=abc.

Запуск:  py -3.10 -m pytest tests/test_sec_p0.py -q
"""
import io
import json
import os
import sys
import tempfile
import urllib.request
from unittest.mock import MagicMock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from api import jobs  # noqa: E402


def _js(name):
    """Исходник фронта как текст (переносы строк — как в файле, CRLF не мешает)."""
    with open(os.path.join(ROOT, "static", "app", name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def ai_config(tmp_path, monkeypatch):
    """Подменить ai_config.json тестовым файлом.

    Рабочий конфиг пользователя не читаем и не пишем — там его ключи. Заодно глушим
    каталог models.dev: `/api/ai_models` тянет его сам, а в тестах сети нет, и его
    кэш на диске не должен подменяться ответом поддельного urlopen.
    """
    from core import aicut
    cfg_file = tmp_path / "ai_config.json"
    monkeypatch.setattr(aicut.config, "AI_CONFIG_PATH", str(cfg_file))
    monkeypatch.setattr(aicut.catalog, "ensure_catalog", lambda *a, **k: {})
    return cfg_file


def _write_config(path, name, **extra):
    prof = {"provider": "openai", "base_url": "https://legit-api.openai.com/v1",
            "api_key": "sk-legit-secret", "model": "gpt-4o"}
    prof.update(extra)
    path.write_text(json.dumps({"active": name, "profiles": {name: prof}}), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. WORK_DIR= : валидация пути (api/jobs.py)
# --------------------------------------------------------------------------- #
def test_is_safe_work_dir():
    """Юнит-тест функции проверки пути:
    - корректные пути omnicut_* и gigaamcut_* прямо в temp -> True
    - чужой путь, путь вне temp, temp целиком -> False
    - обход через '..' (и наружу, и в соседний каталог) -> False
    - не-строки -> False
    """
    temp_dir = tempfile.gettempdir()

    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "omnicut_12345")) is True
    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "gigaamcut_abcde")) is True

    # Чужой путь
    assert jobs.is_safe_work_dir("C:\\Windows\\System32") is False
    assert jobs.is_safe_work_dir("/etc/passwd") is False
    assert jobs.is_safe_work_dir(ROOT) is False

    # Внутри temp, но имя не начинается с omnicut_/gigaamcut_
    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "some_other_folder")) is False
    # Сам каталог temp (его rmtree снёс бы весь %TEMP%)
    assert jobs.is_safe_work_dir(temp_dir) is False

    # Обход через '..'
    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "omnicut_123", "..", "..", "evil")) is False
    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "omnicut_123", "..", "other")) is False
    # Вложенный каталог внутри рабочего — движок удаляет только сам mkdtemp
    assert jobs.is_safe_work_dir(os.path.join(temp_dir, "omnicut_123", "sub")) is False

    # Невалидные типы
    assert jobs.is_safe_work_dir("") is False
    assert jobs.is_safe_work_dir("   ") is False
    assert jobs.is_safe_work_dir(None) is False


# --------------------------------------------------------------------------- #
# 2. XSS: esc() для данных и data-* для обработчиков (static/app/*.js)
# --------------------------------------------------------------------------- #
# Места, куда данные приходят с сервера, диска или от модели, а уезжают в разметку.
# Проверка статическая: JS в наборе тестов не исполняется, а именно эти строки —
# точки вставки из п. 2 и «вторые двери», найденные обходом static/app/*.js.
_ESCAPED = {
    "40-queue.js": [
        "esc(errText(d))",                       # caminfo: текст ошибки с сервера
        "<span>'+esc(name)+'</span>",            # имя файла на диске в списке удаления
        "esc(t('Спикер: {n}'",                   # метка спикера внутри data-t
    ],
    "10-settings.js": [
        "esc(t('Убрать запомненный вариант",     # ослышка словаря внутри data-t
        "'<option value=\"'+esc(l)+'\"'",        # уровень усилий из каталога моделей
    ],
    "30-video.js": [
        "esc(errText(d))",                       # список задач не прочитался
        "esc(it.path||'')",                      # путь файла задачи в title
    ],
}


@pytest.mark.parametrize("name", sorted(_ESCAPED))
def test_frontend_escapes_server_data(name):
    src = _js(name)
    missing = [s for s in _ESCAPED[name] if s not in src]
    assert not missing, f"{name}: данные уезжают в разметку без esc(): {missing}"


def test_video_history_handlers_use_dataset():
    """vidHistShow/vidHistReuse/vidHistDel получают ключ задачи через data-k.

    Внутри `onclick="vidHistShow('+k+')"` экранирование не работает в принципе:
    браузер раскодирует &#39; в значении атрибута ДО компиляции JS, и кавычка в
    ключе и рвёт разметку, и выполняет чужой код (п. 3 задания).
    """
    src = _js("30-video.js")
    assert "onclick=\"vidHistShow(this.dataset.k)\"" in src
    assert "onclick=\"vidHistReuse(this.dataset.k)\"" in src
    assert "onclick=\"vidHistDel(this.dataset.k)\"" in src
    assert "data-k=" in src
    # обратной дороги быть не должно: данных внутри JS-строки обработчика
    assert "vidHistShow('" not in src
    assert "vidHistReuse('" not in src
    assert "vidHistDel('" not in src


# --------------------------------------------------------------------------- #
# 3. Сохранённый ключ и чужой base_url (api/ai.py)
# --------------------------------------------------------------------------- #
def test_ai_models_masked_key_ignores_request_base_url(client, ai_config, monkeypatch):
    """Ключ-маска: base_url и headers берутся из сохранённого профиля, а не из тела."""
    _write_config(ai_config, "legit_prof", headers={"X-Custom": "legit"})
    seen = []                                   # (url, Authorization) каждого сетевого вызова

    def fake_urlopen(req, *args, **kwargs):
        seen.append((req.full_url, req.get_header("Authorization") or ""))
        resp = MagicMock()
        resp.__enter__.return_value = io.BytesIO(
            json.dumps({"data": [{"id": "gpt-4o"}]}).encode("utf-8"))
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    r = client.post("/api/ai_models", json={
        "name": "legit_prof",
        "profile": {"provider": "openai", "api_key": "••••••••",
                    "base_url": "https://attacker-evil.com/v1",
                    "headers": {"X-Custom": "evil"}},
    }, headers={"Host": "127.0.0.1:5001"})

    assert r.status_code == 200, r.get_data(as_text=True)
    assert seen, "ни одного сетевого вызова — тест ничего не проверил"
    assert any("sk-legit-secret" in auth for _, auth in seen), \
        f"сохранённый ключ не подставился: {seen}"
    assert any("legit-api.openai.com" in u for u, _ in seen), \
        f"запрос должен был уйти на сохранённый base_url: {seen}"
    assert not [u for u, _ in seen if "attacker-evil.com" in u], \
        f"утечка: ключ ушёл на base_url из тела запроса: {seen}"


def test_ai_test_masked_key_ignores_request_base_url(client, ai_config, monkeypatch):
    """То же для кнопки «Проверить»: она ходит выбранным бэкендом по prof."""
    from core import aicut
    _write_config(ai_config, "legit_prof", headers={"X-Custom": "legit"})
    used = {}

    def fake_ask_openai(prof, *args, **kwargs):
        used.update(prof)
        return {"ok": True}

    monkeypatch.setattr(aicut, "_ask_openai", fake_ask_openai)

    r = client.post("/api/ai_test", json={
        "name": "legit_prof",
        "profile": {"provider": "openai", "api_key": "••••••••",
                    "base_url": "https://attacker-evil.com/v1",
                    "headers": {"X-Custom": "evil"}, "model": "gpt-4o"},
    }, headers={"Host": "127.0.0.1:5001"})

    assert r.status_code == 200, r.get_data(as_text=True)
    assert "legit-api.openai.com" in used.get("base_url", ""), \
        f"в вызов ушёл base_url из тела: {used.get('base_url')}"
    assert used.get("api_key") == "sk-legit-secret", "ключ взят не из сохранённого профиля"
    assert (used.get("headers") or {}).get("X-Custom") == "legit", \
        f"заголовки взяты из тела запроса: {used.get('headers')}"


# --------------------------------------------------------------------------- #
# 4. int(query) -> не 500
# --------------------------------------------------------------------------- #
def test_waveform_non_int_pps_not_500(client):
    """/api/waveform?pps=abc не должен падать в HTML-500 (ValueError)."""
    r = client.get("/api/waveform?path=nonexistent_file.wav&pps=abc",
                   headers={"Host": "127.0.0.1:5001"})
    assert r.status_code != 500
    d = r.get_json()
    assert d and (d.get("err") == "no_file" or "error" in d)


def test_insertlib_describe_status_non_int_since_not_500(client):
    """/api/insertlib_describe_status?since=abc не должен падать в HTML-500."""
    r = client.get("/api/insertlib_describe_status?since=abc",
                   headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    d = r.get_json()
    assert d and "running" in d
