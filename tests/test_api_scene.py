# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""/api/scene: план сцены по тем же полям, что фоновая сборка, только
без .jsx и рото-масок — быстрый вызов для фронта/предпросмотра."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_scene_returns_plan(client):
    """План приходит целиком: метрики, камеры, субтитры, служебного _ae нет."""
    xml = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")
    r = client.post("/api/scene", json={"xml": xml}, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    plan = body["plan"]
    assert plan["fps"] == 60 and plan["w"] == 1080 and plan["h"] == 1920
    assert len(plan["cams"]) == 2 and all("clips" in c for c in plan["cams"])
    assert "_ae" not in plan and isinstance(plan["inserts"], list)


def test_api_scene_missing_file(client):
    """Пути нет — ошибка в формате остальных роутов (а не 500-HTML)."""
    r = client.post("/api/scene", json={"xml": "C:/нет-такого.xml"},
                    headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("error") and body.get("err") == "file_not_found"