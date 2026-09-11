# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Отсев уже нарезанных дублей (/api/newtakes).

Автоподбор камер по звуку гоняет корреляцию по КАЖДОМУ файлу папки камеры, а
папка копится съёмками: без отсева одна кнопка стоит минуты и добавляет в
очередь давно готовое. «Уже нарезан» определяется тремя способами (сайдкар
прогона, пути внутри XML, имя файла) — здесь стережётся каждый, потому что
ошибка в любую сторону одинаково плоха: лишний прогон на час или молча
потерянный дубль.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import xmlbuild  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def ask(client, outdir, files):
    r = client.post("/api/newtakes", json={"outdir": str(outdir), "files": files})
    return r.get_json()


def write_xml(outdir, name, cams):
    """XML-результат: пути камер лежат в нём так же, как их пишет xmlbuild."""
    body = "".join("<file id='f%d'><pathurl>%s</pathurl></file>" % (i, xmlbuild.pathurl(c))
                   for i, c in enumerate(cams))
    (outdir / name).write_text("<xmeml>%s</xmeml>" % body, encoding="utf-8")


def test_нарезанное_отделяется_от_нового(tmp_path):
    """Базовый случай: имя XML = префикс очереди + имя исходника."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_C1432.xml", [str(tmp_path / "cam1" / "C1432.MP4")])
    from api.files import _cut_sources
    names, stems = _cut_sources(str(out))
    assert "c1432.mp4" in names and "c1432" in stems


def test_сайдкар_ловит_переименованный_xml(client, tmp_path):
    """Юзер переименовал результат — по имени дубль уже не узнать, а сайдкар
    прогона (<stem>.project.json) помнит исходник. Это главный источник: он
    крошечный, а XML весит мегабайты."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "интервью про пептиды.xml").write_text("<xmeml/>", encoding="utf-8")
    (out / "интервью про пептиды.project.json").write_text(
        json.dumps({"cams": ["D:/съёмка/камера1\\C1437.MP4", "D:/съёмка/Камера2\\C1436.MP4"]}),
        encoding="utf-8")
    d = ask(client, out, ["C1437.MP4", "C1500.MP4"])
    assert d["done"] == ["C1437.MP4"] and d["new"] == ["C1500.MP4"]


def test_без_сайдкара_смотрим_внутрь_xml(client, tmp_path):
    """Сайдкар потеряли (старая нарезка, ручная чистка папки) — источник берём
    из самого XML, иначе дубль пошёл бы на второй круг."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "готовое.xml", [str(tmp_path / "cam1" / "C1437.MP4")])
    d = ask(client, out, ["C1437.MP4", "C1500.MP4"])
    assert d["done"] == ["C1437.MP4"] and d["new"] == ["C1500.MP4"]


def test_регистр_имени_не_значит_ничего(client, tmp_path):
    """Windows: C1437.MP4 и c1437.mp4 — один файл. Иначе дубль «новый» на ровном месте."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_C1437.xml", [str(tmp_path / "cam1" / "C1437.MP4")])
    d = ask(client, out, ["c1437.mp4"])
    assert d["done"] == ["c1437.mp4"] and d["new"] == []


def test_бэкап_прошлой_сборки_не_считается_нарезкой(client, tmp_path):
    """<stem>.xml.bak — копия перед пересборкой, а не результат по НОВОМУ дублю."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_C1500.xml.bak", [str(tmp_path / "cam1" / "C1500.MP4")])
    d = ask(client, out, ["C1500.MP4"])
    assert d["new"] == ["C1500.MP4"] and d["done"] == []


def test_нет_папки_результата_значит_новые_все(client, tmp_path):
    """Первый прогон спикера: папки ещё нет. Это не ошибка — иначе кнопка молча
    не добавит ничего."""
    d = ask(client, tmp_path / "нет такой", ["C1500.MP4"])
    assert d["ok"] and d["new"] == ["C1500.MP4"] and d.get("no_outdir")


def test_порядок_файлов_сохраняется(client, tmp_path):
    """Очередь собирается в порядке съёмки — перетасовать её нельзя."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_B.xml", [str(tmp_path / "cam1" / "B.MP4")])
    d = ask(client, out, ["A.MP4", "B.MP4", "C.MP4", "D.MP4"])
    assert d["new"] == ["A.MP4", "C.MP4", "D.MP4"]


def test_битый_сайдкар_не_роняет_ответ(client, tmp_path):
    """Рваный project.json (крах в момент записи) — не повод отказать: падаем
    на имя XML, оно тут есть."""
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_C1437.xml", [str(tmp_path / "cam1" / "C1437.MP4")])
    (out / "01_C1437.project.json").write_text('{"cams": [', encoding="utf-8")
    d = ask(client, out, ["C1437.MP4", "C1500.MP4"])
    assert d["done"] == ["C1437.MP4"] and d["new"] == ["C1500.MP4"]


def test_список_берётся_из_папки_камеры(client, tmp_path):
    """files можно не слать: с dir сервер сам читает видео папки камеры."""
    cam = tmp_path / "cam1"
    cam.mkdir()
    for n in ("C1437.MP4", "C1500.MP4", "заметки.txt"):
        (cam / n).write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    write_xml(out, "01_C1437.xml", [str(cam / "C1437.MP4")])
    r = client.post("/api/newtakes", json={"outdir": str(out), "dir": str(cam)})
    d = r.get_json()
    assert d["new"] == ["C1500.MP4"] and d["done"] == ["C1437.MP4"]
