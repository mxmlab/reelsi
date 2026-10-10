# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модель вырезания фона (`rembg_model`): u2net по умолчанию или birefnet-general.

Пакет rembg подменяется заглушкой: onnx-модели в тестах не грузим (веса тяжёлые, сеть
запрещена). Заглушка красит результат цветом модели, поэтому по пикселям видно, какой
моделью сделана вырезка, — и кэш `<стем>.nobg.png` проверяется по содержимому, а не
по догадке.

Конфиг — свой файл во временном каталоге (`AI_CONFIG_PATH` подменён), личный
ai_config.json не читается и не пишется.
"""
import io
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest
from flask import Flask
from PIL import Image

from core import insertlib
from core.aicut import config as aicut_config
import api
from core import aicut

# Цвет вырезки по моделям: по нему тест видит, какой моделью сделан файл.
_COLOR = {"u2net": (255, 0, 0), "birefnet-general": (0, 0, 255)}


def _rgba_png(rgb, size=8):
    """PNG с непрозрачным квадратом 4x4 в центре и пустыми полями — как после rembg."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(2, 6):
        for y in range(2, 6):
            im.putpixel((x, y), tuple(rgb) + (255,))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class _FakeRembg:
    """Заглушка rembg: что создавали и чем вырезали — записываем, onnx-модель не грузим."""

    def __init__(self):
        self.sessions_created = []   # имена моделей, под которые создана сессия
        self.removals = []           # имя модели сессии, которой сделали вырезку

    def new_session(self, name="u2net"):
        self.sessions_created.append(name)
        return SimpleNamespace(name=name)

    def remove(self, data, session=None):
        self.removals.append(session.name)
        return _rgba_png(_COLOR[session.name])


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "ai_config.json"
    cfg_path.write_text(json.dumps({"active": "Тест", "profiles": {
        "Тест": {"provider": "lmstudio", "base_url": "http://localhost:1/v1",
                 "api_key": "", "model": "m"}}}, ensure_ascii=False), encoding="utf-8")
    # Файл есть — автозасев с боевого конфига не сработает; подменяем на всякий случай.
    monkeypatch.setattr(aicut_config, "AI_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(aicut_config, "_seed_ai_config", lambda: None)
    monkeypatch.setattr(insertlib, "_RB_SESSION", None)
    monkeypatch.setattr(insertlib, "_RB_SESSION_NAME", None)
    fake = _FakeRembg()
    mod = types.ModuleType("rembg")
    mod.new_session = fake.new_session
    mod.remove = fake.remove
    monkeypatch.setitem(sys.modules, "rembg", mod)
    return SimpleNamespace(cfg=cfg_path, fake=fake, tmp=tmp_path)


def _set_model(env, model):
    """Записать rembg_model в конфиг (None — убрать ключ, как в старом конфиге)."""
    d = json.loads(env.cfg.read_text(encoding="utf-8"))
    if model is None:
        d.pop("rembg_model", None)
    else:
        d["rembg_model"] = model
    env.cfg.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _center_rgb(path):
    """Цвет центра вырезки (после обрезки полей квадрат занимает всё изображение)."""
    with Image.open(path) as im:
        return tuple(im.convert("RGBA").getpixel((1, 1)))[:3]


def test_default_model_is_u2net(env):
    assert aicut.rembg_model() == "u2net"
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net"]
    assert env.fake.removals == ["u2net"]


def test_unknown_value_in_config_falls_back_to_u2net(env):
    _set_model(env, "sam2")
    assert aicut.rembg_model() == "u2net"


def test_birefnet_is_used_when_chosen(env):
    _set_model(env, "birefnet-general")
    assert aicut.rembg_model() == "birefnet-general"
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["birefnet-general"]
    assert env.fake.removals == ["birefnet-general"]


def test_model_switch_opens_new_session_and_drops_old(env):
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net"]

    _set_model(env, "birefnet-general")
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "birefnet-general"]
    assert insertlib._RB_SESSION_NAME == "birefnet-general"
    assert env.fake.removals == ["u2net", "birefnet-general"]

    # Та же модель — сессия переиспользуется, новая не создаётся.
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "birefnet-general"]


def test_cutout_cache_is_model_specific(env):
    """Смена модели не отдаёт прошлую вырезку из кэша <стем>.nobg.png."""
    pic = env.tmp / "pic.png"
    pic.write_bytes(_rgba_png((200, 200, 200)))

    dst = insertlib.nobg_path(str(pic))
    assert os.path.basename(dst) == "pic.png.nobg.png"
    assert env.fake.removals == ["u2net"]
    assert _center_rgb(dst) == _COLOR["u2net"]

    # Та же модель: кэш берём, вырезку заново не делаем.
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["u2net"]

    # Смена модели: кэш той же картинки пересчитан, а не отдан старым.
    _set_model(env, "birefnet-general")
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["u2net", "birefnet-general"]
    assert _center_rgb(dst) == _COLOR["birefnet-general"]


def test_legacy_cache_without_tag_counts_as_u2net(env):
    """Кэш, сделанный до выбора модели (без метки), остаётся годным для u2net."""
    pic = env.tmp / "old.png"
    pic.write_bytes(_rgba_png((200, 200, 200)))
    dst = str(pic) + ".nobg.png"
    Image.open(io.BytesIO(_rgba_png(_COLOR["u2net"]))).save(dst, "PNG")  # без метки
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == []

    _set_model(env, "birefnet-general")
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["birefnet-general"]


def test_api_get_and_set_rembg_model(env):
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/api/ai_config").get_json()["rembg_model"] == "u2net"

    ok = client.post("/api/ai_config",
                     json={"action": "set_rembg_model", "value": "birefnet-general"}).get_json()
    assert ok["ok"] is True
    assert ok["rembg_model"] == "birefnet-general"
    assert client.get("/api/ai_config").get_json()["rembg_model"] == "birefnet-general"

    bad = client.post("/api/ai_config",
                      json={"action": "set_rembg_model", "value": "sam2"}).get_json()
    assert bad.get("err") == "rembg_model_invalid"
    assert client.get("/api/ai_config").get_json()["rembg_model"] == "birefnet-general"
