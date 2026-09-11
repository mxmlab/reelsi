# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт глушилки лога в ИИ-роутах — POST /api/ai_genimage.

Пойманный баг (2026-08-22): роут отдавал в aicut глушилку `lambda *a: None`, а
весь aicut зовёт emit СТРУКТУРНО — `emit("картинка: {model} …", model=…, cost=…)`.
Строчка о цене печатается ПОСЛЕ успешной генерации, поэтому падало так: запрос к
провайдеру оплачен, картинка получена — и выброшена с
`TypeError: <lambda>() got an unexpected keyword argument 'model'`.

Стережётся именно это: любая глушилка, уезжающая в aicut, обязана принимать
`**vars`. Сеть не трогается — `gen_image` подменяется и сам зовёт emit структурно.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import aicut  # noqa: E402
from core import insertlib  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_структурный_лог_не_роняет_готовую_картинку(client, tmp_path, monkeypatch):
    """emit со **vars внутри aicut не должен ронять уже сгенерённую картинку."""
    seen = []

    def fake_gen_image(prompt, prof=None, emit=None, retries=1):
        # ровно то, что делает aicut/images.py после успешного ответа провайдера
        emit("  картинка: {model} ({kb}КБ, ${cost:.3f})",
             model="black-forest-labs/flux-2-klein", kb=42, cost=0.014)
        seen.append(prompt)
        return b"PNG-bytes"

    monkeypatch.setattr(aicut, "gen_image", fake_gen_image)
    monkeypatch.setattr(aicut, "build_image_prompt", lambda q, slot="a", speaker=None: q)
    monkeypatch.setattr(aicut, "resolve_image_prompt_cfg",
                        lambda slot, speaker=None: {"extra": ""})
    monkeypatch.setattr(aicut, "image_rembg_on", lambda: False)
    out = tmp_path / "generated" / "x.png"
    monkeypatch.setattr(insertlib, "add_generated",
                        lambda png, query, dest, ru="", look="": str(out))
    monkeypatch.setattr(insertlib, "_thumb_b64", lambda p: "")

    r = client.post("/api/ai_genimage", json={"query": "кот", "dest": str(tmp_path)})
    body = r.get_json()
    assert body.get("ok") is True, body
    assert seen == ["кот"], "генерация не дошла до провайдера"
