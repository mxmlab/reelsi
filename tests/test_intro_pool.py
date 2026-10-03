# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OW: ИИ-интро на пачку роликов — параллельно, как жёлтые и вставки.

Жёлтые и вставки давно идут пачкой: фронт гоняет клипы пулом (`runPool`), сервер
считает живые вызовы одной пачки (`_ai_begin(label, batch=…, step=…)`) и держит
лимит шага. ИИ-интро оставалось циклом по одному со `sleep(300)`, а роут звал
`_ai_begin("интро")` без пачки — то есть ни лимита шага, ни общей выгрузки модели
(её выгружает последний вызов пачки, а не первый закончивший).

Что сторожим:

1. роут `/api/ai_intro` передаёт `batch` и `step="intro"` в `_ai_begin` (образец —
   тест 10 в `tests/test_parallel_ai.py`);
2. `aiIntroAllRun` при шаге больше 1 идёт пулом, и обе ветки (пул и последовательный
   цикл) зовут ОДНО тело клипа — `aiIntroOne(c,batch)`: `batch` уходит в `/api/ai_intro`
   из неё, своей копии запроса у прогона нет (копии тела разъезжались);
   одиночный `aiIntroRun` `batch` не шлёт — он одиночный и ждёт всех;
3. `aiStepConc` — один зажим 1..16 на весь интерфейс, и `markupAllRun` зовёт
   именно его (вторая копия разошлась бы с первой);
4. поле `conc_markup` пишет число в ТРИ шага: `yellow`, `inserts` и `intro` —
   иначе интро молча шло бы по числу профиля модели;
5. подсказка поля и её перевод говорят про ИИ-интро.

Запуск: py -3.10 -m pytest tests/test_intro_pool.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402
import api.ai as ai_route  # noqa: E402
from core import aicut  # noqa: E402

APP = os.path.join(ROOT, "static", "app")
AE_JS = os.path.join(APP, "90-ae.js")
EDITOR_JS = os.path.join(APP, "70-editor.js")
SETTINGS_JS = os.path.join(APP, "10-settings.js")
HTML = os.path.join(ROOT, "templates", "index.html")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

H = {"Host": "127.0.0.1:5001"}

# Ключ словаря — сам русский текст подсказки поля conc_markup (см. i18n_extract.py).
HINT = ("Сколько роликов «Разметить всё» гонит сразу (жёлтые, вставки и ИИ-интро). "
        "Пусто — как в профиле модели (облако 4).")


@pytest.fixture
def client() -> Any:
    """Тестовый клиент Flask с зарегистрированным blueprint api."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _js_function(path: str, name: str) -> str:
    """Тело функции: от объявления до следующей функции верхнего уровня.

    `async` перед именем есть у aiIntroRun/aiIntroAllRun/aiIntroOne и нет у
    aiStepConc — поэтому объявление ищем по имени, а не по паре «async function».
    """
    js = _read(path)
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), js)
    assert m, "не нашёл функцию %s в %s" % (name, os.path.basename(path))
    ends = [i for i in (js.find("\nasync function ", m.end()),
                        js.find("\nfunction ", m.end())) if i > 0]
    return js[m.start():min(ends) if ends else len(js)]


def _ai_intro_bodies(fn_src: str) -> list[str]:
    """Тела запросов `/api/ai_intro` в функции — тем же приёмом, что и в
    tests/test_markup_pool_js.py: до первой закрывающей скобки. Вложенные объекты
    вставок (`x=>({start_sec…})`) в группу не влезают, но это и не нужно: `batch`
    стоит до них (проверка `\\bbatch\\b` по такому куску работает). Зовут роут
    по-разному: пакет — aiPost (свой прогресс-оверлей), одиночный — aiFetch
    (кнопка «Стоп» рядом с кнопкой), и обе формы проверяются одинаково.
    """
    found = re.findall(r"ai(?:Post|Fetch)\(\s*['\"]/api/ai_intro['\"]\s*,\s*\{([^}]+)\}", fn_src)
    assert found, "не нашёл вызов /api/ai_intro"
    return found


# ---- 1. Роут передаёт пачку и шаг ------------------------------------------

def test_intro_route_passes_batch_and_step(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """1. /api/ai_intro передаёт batch и step="intro" в _ai_begin (и чистит batch)."""
    begin_calls: list[dict[str, Any]] = []

    def mock_ai_begin(label: str = "", batch: str | None = None, step: str | None = None) -> int:
        begin_calls.append({"label": label, "batch": batch, "step": step})
        return 999

    monkeypatch.setattr(ai_route, "_ai_begin", mock_ai_begin)
    monkeypatch.setattr(ai_route, "_ai_end", lambda ep, unload=False: None)
    monkeypatch.setattr(ai_route.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(aicut, "cmd_intro", lambda *a, **kw: {"intro_rows": [], "mid_groups": []})

    r = client.post("/api/ai_intro", json={"xml": "dummy.xml", "batch": "my_batch_intro"}, headers=H)
    assert r.status_code == 200
    assert begin_calls == [{"label": "интро", "batch": "my_batch_intro", "step": "intro"}]

    # Без batch (одиночный вызов кнопкой): пачки нет, а шаг шагом и остаётся
    r2 = client.post("/api/ai_intro", json={"xml": "dummy.xml"}, headers=H)
    assert r2.status_code == 200
    assert begin_calls[1]["batch"] is None
    assert begin_calls[1]["step"] == "intro"

    # Длинный batch обрезается, как у жёлтых и вставок
    r3 = client.post("/api/ai_intro", json={"xml": "dummy.xml", "batch": "b" * 100}, headers=H)
    assert r3.status_code == 200
    assert begin_calls[2]["batch"] == "b" * 64


# ---- 2. Фронт: пакетный прогон пулом, одиночный — без пачки ----------------

def test_batch_intro_runs_through_pool() -> None:
    """2. aiIntroAllRun: свой batch, ширина из шага intro, пул и прогресс пула.

    Тело на клип уехало в общий aiIntroOne — здесь проверяется, что прогон зовёт
    ИМЕННО его (пул — с batch, последовательный цикл — без) и что своей копии
    запроса к /api/ai_intro у прогона не осталось: копии тела разъезжались.
    """
    src = _js_function(AE_JS, "aiIntroAllRun")

    assert re.search(r"const\s+batch\s*=\s*'in'\s*\+\s*Date\.now\(\)\.toString\(36\)", src), \
        "пачка интро не получает свой batch"
    assert re.search(r"aiStepConc\(\s*'intro'\s*\)", src), \
        "ширина пула берётся не у шага intro"
    assert re.search(r"if\(\s*n\s*>\s*1\s*\)", src), \
        "нет ветки «шаг больше одного»: пул не включается"
    assert re.search(r"runPool\(\s*list\s*,\s*n\s*,\s*async\s+c\s*=>", src), \
        "пакетный ИИ-интро не идёт пулом (runPool)"
    assert "loadAIProfiles()" in src, \
        "AICFG может быть ещё не загружен — профили не подтягиваются"

    # Прогресс: в шапке — «сколько готово из скольких», без имён роликов в работе
    # (они и так в строках списка, форма — в static/app/55-progress.js)
    assert re.search(r"progQueue\(\s*t\('ИИ интро'\)\s*,\s*done\s*,\s*N\s*\)", src), \
        "прогресс очереди не показывает, сколько роликов уже размечено"
    assert re.search(r"progStep\(\s*t\('ИИ размечает…'\)\s*,\s*done\s*/\s*N\s*\)", src), \
        "после клипа прогресс не двигается по числу завершённых"

    # Обе ветки зовут ОДНО тело клипа: пул — с batch, последовательный цикл — без
    assert re.search(r"await\s+aiIntroOne\(\s*c\s*,\s*batch\s*\)", src), \
        "пул не зовёт общий aiIntroOne с batch"
    assert re.search(r"await\s+aiIntroOne\(\s*c\s*\)", src), \
        "последовательный цикл не зовёт общий aiIntroOne"
    assert "/api/ai_intro" not in src, \
        "у прогона снова своя копия запроса — тело клипа обязано быть одно (aiIntroOne)"
    # Счётчики ok/fail/skip — по возврату тела: сон между клипами в пуле не нужен
    for frag in ("ok++", "fail++", "skip++"):
        assert frag in src, "пул интро потерял счётчик %s" % frag


def test_intro_one_is_the_only_clip_body() -> None:
    """2б. aiIntroOne — единственное тело клипа: запрос с batch, панель, saveState
    и возврат 'ok' | 'fail' | 'skip' (по нему прогон считает ok/fail/skip)."""
    src = _js_function(AE_JS, "aiIntroOne")

    # batch уходит в /api/ai_intro отсюда — в пуле его больше взять неоткуда
    assert re.search(r"\bbatch\b", _ai_intro_bodies(src)[0]), \
        "запрос интро уходит без batch"
    assert re.search(r"aiPost\(\s*'/api/ai_intro'", src), \
        "тело клипа ушло с aiPost на другой роут"
    # Возврат различает три исхода: счётчики прогона считаются по нему
    assert "return 'skip'" in src, "пропуск без субтитров не отличим от ошибки"
    for val in ("'ok'", "'fail'"):
        assert val in src, "тело клипа не различает исход %s" % val
    # Обновление открытого клипа и сохранение — здесь же, в одной копии
    for frag in ("renderIntro()", "captureAE()", "aewRender()", "saveState()"):
        assert frag in src, "тело клипа потеряло %s" % frag


def test_single_intro_run_has_no_batch() -> None:
    """3. Одиночный aiIntroRun (кнопка на клипе) batch не шлёт и пулом не идёт."""
    src = _js_function(AE_JS, "aiIntroRun")
    assert re.search(r"\bbatch\b", _ai_intro_bodies(src)[0]) is None, \
        "одиночный ИИ-интро уехал в пачку"
    assert "runPool" not in src, "одиночный ИИ-интро пошёл пулом"


# ---- 3. Один зажим на интерфейс --------------------------------------------

def test_ai_step_conc_declared_once_and_used() -> None:
    """3. aiStepConc объявлен один раз (в 70-editor.js) и зовётся из markupAllRun."""
    js_files = sorted(f for f in os.listdir(APP) if f.endswith(".js"))
    holders = [f for f in js_files if re.search(r"function\s+aiStepConc\s*\(", _read(os.path.join(APP, f)))]
    assert holders == ["70-editor.js"], \
        "aiStepConc объявлен не один раз или не в 70-editor.js: %s" % holders

    editor = _read(EDITOR_JS)
    assert len(re.findall(r"function\s+aiStepConc\s*\(", editor)) == 1, "копий aiStepConc больше одной"

    conc = _js_function(EDITOR_JS, "aiStepConc")
    assert re.search(r"typeof\s+AICFG\s*!==\s*'undefined'", conc), \
        "нет проверки, загружен ли AICFG (был бы ReferenceError)"
    assert re.search(r"Math\.max\(\s*1\s*,\s*Math\.min\(\s*16", conc), "зажим 1..16 потерялся"

    markup = _js_function(EDITOR_JS, "markupAllRun")
    assert re.search(r"aiStepConc\(\s*'(?:yellow|inserts)'\s*\)", markup), \
        "markupAllRun не зовёт общий aiStepConc"


# ---- 4. Поле «Роликов одновременно» пишет и intro --------------------------

def test_conc_markup_writes_intro_step() -> None:
    """4. conc_markup — это число для всей разметки: yellow, inserts и intro."""
    body = _js_function(SETTINGS_JS, "setStepConcurrency")
    assert "conc_markup" in body, "разметка и нарезка не различаются в обработчике"
    for step in ("'yellow'", "'inserts'", "'intro'"):
        assert step in body, "поле разметки не пишет шаг %s" % step
    assert "conc_cut" not in body.split("conc_markup")[1], \
        "обработчик разметки тронул поле нарезки"


# ---- 5. Подсказка поля и перевод -------------------------------------------

def test_hint_and_translation_mention_intro() -> None:
    """5. Подсказка говорит про ИИ-интро, и перевод у неё есть (ключ = русский текст)."""
    html = _read(HTML)
    assert ('data-t="%s"' % HINT) in html, "подсказка поля conc_markup не упоминает ИИ-интро"

    en = json.loads(_read(EN))
    assert HINT in en, "подсказка потеряла перевод — на английском остался бы старый текст"
    assert not re.search(r"[А-Яа-яЁё]", en[HINT]), "перевод остался русским"
    assert "yellow words and inserts" not in en[HINT], "перевод не обновлён под новый текст"
