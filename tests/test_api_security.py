# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Границы локального API.

Reelsi — локальный однопользовательский инструмент без авторизации, и это осознанно
(модель угроз — в SECURITY.md). Но из «слушаем только 127.0.0.1» ещё не следует
безопасность: браузер юзера открывает и чужие страницы, а те могут резолвить свой
домен в 127.0.0.1 и обратиться к нашему API уже со своего origin — CORS тогда не
мешает. При том, что /api/media по замыслу отдаёт ЛЮБОЙ файл с диска, включая
ai_config.json с ключами провайдеров.

Здесь стерегутся обе двери: проверка Host и запрет отдавать конфиг с ключами.

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


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# Host: защита от DNS rebinding
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", ["localhost:5001", "127.0.0.1:5001", "127.0.0.1",
                                  "localhost", "LOCALHOST:5001", "[::1]:5001"])
def test_local_hosts_allowed(client, host):
    """Обычная работа не должна пострадать: и localhost, и 127.0.0.1, и IPv6."""
    r = client.get("/api/media?path=", headers={"Host": host})
    assert r.status_code != 403, f"{host} заблокирован, а это нормальный клиент"


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:5001",
                                  "attacker.local", "192.168.1.50:5001"])
def test_foreign_hosts_rejected(client, host):
    """Подставной домен, резолвящийся в 127.0.0.1, — тот самый DNS rebinding."""
    r = client.get("/api/media?path=whatever", headers={"Host": host})
    assert r.status_code == 403


def test_rebinding_guard_covers_every_route(client):
    """Проверка обязана стоять на ВСЕХ /api/*, а не на одном media: утечь могут и
    состояние UI, и список файлов, и профили провайдеров."""
    for path in ("/api/ui_state", "/api/cams", "/api/status", "/api/fonts",
                 "/api/styles", "/api/speakers", "/api/waveform?path=x"):
        r = client.get(path, headers={"Host": "evil.example.com"})
        assert r.status_code == 403, f"{path} доступен чужому Host"


def test_guard_is_registered_on_the_blueprint():
    """Защита висит на самом Blueprint, а не на отдельных функциях: новый эндпоинт
    получает её автоматически. Иначе первый же добавленный роут окажется открытым,
    и заметить это будет некому."""
    handlers = api.bp.deferred_functions
    assert api._block_dns_rebinding.__name__ == "_block_dns_rebinding"
    assert handlers, "у Blueprint нет отложенных регистраций — before_request потерян"


# --------------------------------------------------------------------------- #
# /api/media: ключи провайдеров через него не отдаются
# --------------------------------------------------------------------------- #
def test_media_refuses_ai_config(client, tmp_path):
    """Эндпоинт умеет отдать любой файл — это нужно для превью материала, лежащего
    где попало. Но ai_config.json содержит ключи, и он исключён навсегда."""
    cfg = tmp_path / "ai_config.json"
    cfg.write_text('{"profiles": {"x": {"api_key": "sk-СЕКРЕТ"}}}', encoding="utf-8")
    r = client.get(f"/api/media?path={cfg}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403
    assert b"sk-" not in r.data


def test_media_refuses_test_profile_config(client, tmp_path):
    """У изолированного профиля свой конфиг — с такими же настоящими ключами."""
    cfg = tmp_path / "ai_config.test.json"
    cfg.write_text('{"profiles": {}}', encoding="utf-8")
    r = client.get(f"/api/media?path={cfg}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403


def test_media_still_serves_normal_files(client, tmp_path):
    """Запрет не должен превратиться в «ничего не отдаём»: превью обязано работать."""
    f = tmp_path / "clip.txt"
    f.write_bytes(b"video-bytes")
    r = client.get(f"/api/media?path={f}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert r.data == b"video-bytes"


def test_missing_file_is_404_not_500(client, tmp_path):
    r = client.get(f"/api/media?path={tmp_path / 'нет.mp4'}",
                   headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 404


def test_media_refuses_rclone_conf(client, tmp_path):
    """В rclone.conf лежит refresh-токен гугл-диска — по своей ценности это тот же
    ai_config.json, но в список его забыли внести (аудит 2026-08-14)."""
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gd]\ntoken = {\"refresh_token\":\"СЕКРЕТ\"}\n", encoding="utf-8")
    r = client.get(f"/api/media?path={conf}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403
    assert b"refresh_token" not in r.data


def test_media_refuses_rclone_conf_moved_by_env(client, tmp_path, monkeypatch):
    """Путь конфига переопределяется REELSI_RCLONE_CONF (изолированный профиль 5098),
    и имя файла там может быть любым — ловим по реальному пути, а не по имени."""
    conf = tmp_path / "мой_профиль.conf"
    conf.write_text("[gd]\ntoken = СЕКРЕТ\n", encoding="utf-8")
    monkeypatch.setenv("REELSI_RCLONE_CONF", str(conf))
    r = client.get(f"/api/media?path={conf}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403


def test_export_routes_use_the_same_guard(client, tmp_path, monkeypatch):
    """Секреты не отдаёт не только /api/media: /api/export_xml читает файл по пути
    точно так же. Проверка одна на всех — иначе она разъедется по копиям."""
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gd]\ntoken = СЕКРЕТ\n", encoding="utf-8")
    r = client.get(f"/api/export_xml?path={conf}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403


def test_host_helper_rejects_empty():
    """HTTP/1.1 без Host — это не наш интерфейс и не браузер."""
    assert not api._host_is_local("")
    assert not api._host_is_local(None)
    assert api._host_is_local("127.0.0.1:5001")
