# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Поиск и создание папок камер на оба языка сразу.

Зачем: find_cam_dirs искал только папки «камер…», и на чистой английской установке
папок не находил вовсе — человек упирался в «нет папок» на первом же экране.
Признак папки камеры теперь: «камер…», «camera…» либо «cam» перед цифрой.
Порядок остаётся по числу в имени — иначе «Камера2» встаёт раньше «камера1».
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import cams  # noqa: E402


def test_find_cam_dirs_ru_en_mixed(tmp_path):
    for name in ("камера1", "Camera2", "cam3", "прочее"):
        (tmp_path / name).mkdir()
    got = [os.path.basename(p) for p in cams.find_cam_dirs(str(tmp_path))]
    assert got == ["камера1", "Camera2", "cam3"]


@pytest.fixture()
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_cams_make_creates_en_folders(client, tmp_path):
    """Кнопка «Создать папки камер» при LANG=en создаёт camera1..N и возвращает
    тот же ответ, что /api/cams — список сразу читается."""
    r = client.post("/api/cams_make", json={"base": str(tmp_path), "n": 2, "lang": "en"})
    body = r.get_json()
    assert r.status_code == 200
    assert not body.get("error")
    assert [d["name"] for d in body["dirs"]] == ["camera1", "camera2"]


def test_cams_make_creates_ru_folders(client, tmp_path):
    r = client.post("/api/cams_make", json={"base": str(tmp_path), "n": 2, "lang": "ru"})
    body = r.get_json()
    assert r.status_code == 200
    assert not body.get("error")
    assert [d["name"] for d in body["dirs"]] == ["камера1", "камера2"]


def test_cams_empty_is_an_error(client, tmp_path):
    """Пустая папка: /api/cams отвечает понятной ошибкой (нет папок), а не пустым
    списком — фронт по ней показывает кнопку создания."""
    r = client.get("/api/cams?base=" + str(tmp_path))
    body = r.get_json()
    assert body.get("error")
    assert body.get("err") == "no_cam_folders"
