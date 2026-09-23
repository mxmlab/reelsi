# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NM: пользовательские ошибки — своим классом ReelsiError, а не SystemExit.

SystemExit — наследник BaseException, а не Exception: его молча пропускает любой
`except Exception`, а в потоке `threading` он уносит работу без следа в логе. Канал
пользовательских ошибок (`raise SystemExit(umsg("код", "текст"))`, около 250 мест)
переведён на `core.umsg.ReelsiError(Exception)`. Цена перехода одна: там, где
`except Exception` раньше ПРОПУСКАЛ ошибку наверх, он начал бы её ловить, — поэтому
перед каждым таким обработчиком, чьё `try` может бросить пользовательскую ошибку,
стоит `except ReelsiError: raise`.

Здесь стерегутся четыре вещи:
  * статически — в core/, api/ и корневых *.py не осталось `raise SystemExit(…)` с
    сообщением (целочисленные выходы процесса — законны) и у каждой точки входа
    `if __name__ == "__main__"` есть перехват ReelsiError;
  * поведенчески — ReelsiError из роута без своего обработчика отдаёт JSON с кодом
    ошибки, а не 500;
  * поведенчески — ReelsiError в цели потока фонового задания доезжает до лога
    задания и до списка failed (то, что чинили для SystemExit);
  * поведенчески — командная строка печатает текст ошибки в stderr и выходит с
    кодом 1, без трейсбека;
  * и главное правило «поведение не меняется»: /api/terms без поля `terms` отдаёт
    ровно тот же код и текст, что и раньше, — тест красный, если в api/presets.py
    забыт `except ReelsiError: raise`.

Запуск:  python -m pytest tests/test_reelsi_error.py -q
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

# Тот самый текст из core/xml2ae/build.py (roto_incomplete) — по нему проверяем, что
# до пользователя доехала ПРИЧИНА, а не тип исключения.
ROTO = "рото не посчитано для 2 из 3 кусков (первая причина: маска не найдена)"

# Броски SystemExit с сообщением, которые остаются осознанно: (файл, строка, причина).
# Список пуст намеренно — целочисленные выходы процесса (core/umsg.cli_error,
# core/omni_asr) под правило не попадают, а `sys.exit("текст")` в main() командной
# строки трогать запрещает.
SYSEXIT_ALLOWED = ()


def _target_files():
    """Файлы под правило: core/, api/ и корневые точки входа."""
    files = []
    for d in ("core", "api"):
        for dirpath, dirnames, filenames in os.walk(ROOT / d):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for name in sorted(filenames):
                if name.endswith(".py"):
                    files.append(Path(dirpath) / name)
    for name in ("doctor.py", "reelsi.py", "webui.py"):
        files.append(ROOT / name)
    return files


def _main_blocks(tree):
    """Тела `if __name__ == "__main__"` верхнего уровня."""
    for node in tree.body:
        if isinstance(node, ast.If) and any(
                isinstance(c, ast.Constant) and c.value == "__main__"
                for c in ast.walk(node.test)):
            yield node


def _systemexit_raises(tree):
    """Номера строк `raise SystemExit(…)` с аргументом, который не целое число."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)
                and exc.func.id == "SystemExit"):
            continue
        if (len(exc.args) == 1 and isinstance(exc.args[0], ast.Constant)
                and isinstance(exc.args[0].value, int)):
            continue          # настоящий выход процесса: SystemExit(1)
        yield node.lineno


def test_нет_бросков_SystemExit_с_сообщением():
    """Пользовательская ошибка бросается ReelsiError, а не SystemExit."""
    bad = []
    for path in _target_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno in _systemexit_raises(tree):
            if (rel, lineno) not in {(f, ln) for f, ln, _ in SYSEXIT_ALLOWED}:
                bad.append("%s:%d" % (rel, lineno))
    assert not bad, ("пользовательская ошибка снова бросается SystemExit "
                     "(её пропустит `except Exception` и проглотит поток):\n  "
                     + "\n  ".join(bad))


def test_у_каждой_точки_входа_есть_перехват_ReelsiError():
    """ReelsiError из main() — текст в stderr и код 1, а не трейсбек.

    Раньше это делал сам Python с `SystemExit(UMsg)`; теперь точку входа заворачивает
    `except ReelsiError: cli_error(e)`. Забытая обёртка — это трейсбек на ровном месте."""
    missing = []
    for path in _target_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in _main_blocks(tree):
            if "cli_error" not in text:
                missing.append("%s:%d" % (rel, node.lineno))
    assert not missing, ("у точки входа нет перехвата ReelsiError:\n  "
                         + "\n  ".join(missing))


# --------------------------------------------------------------------------- #
# Роут, который свою ошибку не поймал
# --------------------------------------------------------------------------- #
# Точка отказа — view-функция УЖЕ зарегистрированного роута: свой роут ради теста
# добавлять нельзя, он остался бы в Blueprint на весь прогон.
BOOM_ENDPOINT = "api.api_cutstages"
BOOM_URL = "/api/cutstages"


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


def test_ReelsiError_из_роута_отдаёт_json_с_кодом(app, client):
    """Обработчик ReelsiError у Blueprint: тот же JSON, что отдал бы сам роут.

    Раньше на этом месте был обрыв запроса вовсе (SystemExit — не Exception, Flask
    его не ловил), а 500 с internal_error прятал бы от пользователя код и текст."""
    from core.umsg import ReelsiError, umsg

    def boom():
        raise ReelsiError(umsg("roto_incomplete", ROTO, n=2, m=3))

    app.view_functions[BOOM_ENDPOINT] = boom
    r = client.get(BOOM_URL)

    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert d["err"] == "roto_incomplete", d
    assert d["error"] == ROTO, d
    assert d["err_vars"] == {"n": 2, "m": 3}, d


def test_прочие_исключения_остаются_500(app, client):
    """Обычное исключение роута по-прежнему 500 с internal_error: обработчик
    ReelsiError не должен перехватывать чужие ошибки."""

    def boom():
        raise RuntimeError("бум в роуте")

    app.view_functions[BOOM_ENDPOINT] = boom
    r = client.get(BOOM_URL)

    assert r.status_code == 500, r.status_code
    assert r.get_json()["err"] == "internal_error", r.get_json()


def test_terms_без_поля_отдаёт_прежний_код_и_текст(client):
    """Правило «поведение не меняется» на живом примере из api/presets.py.

    Внутри try бросается пользовательская ошибка, рядом `except Exception`, который
    упаковывает ЛЮБУЮ ошибку в terms_failed. Без `except ReelsiError: raise` наша
    ошибка перехватилась бы им и текст стал бы «ReelsiError: terms: поле не передано».
    """
    r = client.post("/api/terms", json={})

    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert d["err"] == "terms_failed", d
    assert d["error"] == "terms: поле не передано", d
    assert d["err_vars"] == {"err": "terms: поле не передано"}, d


# --------------------------------------------------------------------------- #
# Поток фонового задания
# --------------------------------------------------------------------------- #
def _log_text(log_list):
    """Лог задания одной строкой: записи структурные ({t, v}), строки — как есть."""
    return "\n".join(e["t"].format(**e.get("v", {})) if isinstance(e, dict) and "t" in e
                     else str(e) for e in log_list)


def test_ReelsiError_в_потоке_задания_доезжает_до_лога(tmp_path, monkeypatch):
    """То, что чинили раньше, но уже для ReelsiError.

    Цель потока ловит (ReelsiError, SystemExit) одинаково: причина попадает в failed
    и в лог задания, а задание не остаётся висеть running."""
    from api import build as apibuild
    from api._core import JOB
    from core import xml2ae
    from core.umsg import ReelsiError, umsg

    JOB.update(running=True, done=False, log=[], results=[], failed=[], cancel=False,
               log_base=0, items=[])
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xml/>", encoding="utf-8")

    def boom(*a, **kw):
        raise ReelsiError(umsg("roto_incomplete", ROTO, n=2, m=3, err="маска не найдена"))

    monkeypatch.setattr(xml2ae, "to_ae_full", boom)

    escaped = None
    try:
        apibuild._run_build_job([{"xml_path": str(xml)}], "separate", str(tmp_path / "out"))
    except BaseException as e:          # BaseException: SystemExit в потоке — тот самый дефект
        escaped = e

    assert escaped is None, "ReelsiError вышел из потока задания: %r" % (escaped,)
    assert JOB["failed"], "падение не попало в failed"
    assert JOB["failed"][0]["name"] == "01_clip", JOB["failed"]
    assert ROTO in JOB["failed"][0]["reason"], JOB["failed"]
    assert ROTO in _log_text(JOB["log"]), "причины нет в логе задания"
    assert JOB["running"] is False, "задание осталось висеть running=True"


# --------------------------------------------------------------------------- #
# Командная строка
# --------------------------------------------------------------------------- #
# Минимальная секвенция xmeml, которая разбирается, но не даёт камеры: понятный отказ
# «В XML не найден путь к видео камеры 1» — без моделей, сети и ffmpeg.
NO_CAM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="5">
  <sequence>
    <duration>25</duration>
    <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
        <format><samplecharacteristics><width>1080</width><height>1920</height>
        </samplecharacteristics></format>
        <track>
          <clipitem><name>c1</name><start>0</start><end>25</end></clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
"""

NO_CAM_TEXT = "В XML не найден путь к видео камеры 1."


def test_командная_строка_печатает_текст_и_выходит_с_единицей(tmp_path):
    """`python -m core.subtitle_xml` с пользовательской ошибкой: текст в stderr, код 1.

    PYTHONIOENCODING из окружения убираем намеренно: без консоли у Python на Windows
    stderr — cp1252, и русский текст уезжал бы в \\uXXXX, если бы cli_error не
    переключал кодировку.
    """
    xml = tmp_path / "nocam.xml"
    xml.write_text(NO_CAM_XML, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env["PYTHONPATH"] = str(ROOT)

    r = subprocess.run([sys.executable, "-m", "core.subtitle_xml", str(xml),
                        str(tmp_path / "out.xml")],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)

    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert NO_CAM_TEXT in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, "пользовательская ошибка ушла трейсбеком:\n" + r.stderr
