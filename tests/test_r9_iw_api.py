# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IW: API — тело запроса, наборы, set_yellow, маска ключа.

Тесты проверяют 5 пунктов задания IW (ветка feat/r9-api):
1. Граница Blueprint в api/_core.py: POST/PUT/PATCH на /api/* с JSON не-объектом
   ([1, 2], "x", 5) возвращает 400 bad_body, а не падает в 500 на d.get().
2. _norm_or_error в api/build.py и api/render.py: inserts: 5 не роняет build_run
   в 500 TypeError, списковые поля набора проверяются на list.
3. /api/set_yellow: indices не список или отсутствует -> bad_indices, байты XML
   файла не изменяются (не перезаписывается пустым списком).
4. api/ai.py: нормализация обеих сторон base_url при проверке маски ключа
   (сохранённый http://127.0.0.1:1234/v1/ сохраняется при маске без смены адреса).
   static/app/10-settings.js: событие input на ais_url при ключе-маске очищает ключ
   и выставляет предупреждение ais_key_warn.
5. Защита отката jstr в api/videogen.py: нестроковые значения в строковых полях
   не приводят к 500.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def xml_file(tmp_path):
    p = tmp_path / "sample.xml"
    p.write_text("<xmeml version='4'><sequence></sequence></xmeml>", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# 1. Граница Blueprint: не-объект в JSON -> 400 bad_body
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_body", [[1, 2], "x", 5])
@pytest.mark.parametrize("route", [
    "/api/editor_save",
    "/api/cams_make",
    "/api/cammatch",
    "/api/savestyle",
    "/api/ai_config",
])
def test_non_object_json_body_returns_400_bad_body(client, route, bad_body):
    """Роуты, падавшие в 500 на d.get(), теперь получают 400 bad_body на границе."""
    r = client.post(route, json=bad_body, headers=H)
    assert r.status_code == 400, f"{route} вернул {r.status_code} вместо 400"
    d = r.get_json()
    assert d.get("err") == "bad_body", f"{route} вернул err={d.get('err')}"
    assert "JSON-объектом" in d.get("error", "")


# --------------------------------------------------------------------------- #
# 2. _norm_or_error: build_run и render_run на inserts: 5
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("route,expected_err", [
    ("/api/build_run", "build_set_invalid"),
    ("/api/render_run", "render_set_invalid"),
])
def test_build_and_render_run_inserts_numeric_not_500(client, monkeypatch, xml_file,
                                                      route, expected_err):
    """inserts: 5 в элементе набора не роняет build_run в 500 TypeError."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    r = client.post(route, json={"jobs": [{"xml": xml_file, "inserts": 5}]}, headers=H)
    assert r.status_code == 200, f"{route} вернул {r.status_code}"
    d = r.get_json()
    assert d.get("ok") is not True
    assert d.get("err") == expected_err, d
    assert "inserts" in d.get("error", ""), d


@pytest.mark.parametrize("field", [
    "intro", "intro_remove", "intro_splits", "hl_breaks", "hl_count", "hl_joins"
])
def test_build_run_list_fields_validated(client, monkeypatch, xml_file, field):
    """Списковые поля набора не списком вызывают build_set_invalid с именем поля."""
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    r = client.post("/api/build_run",
                    json={"jobs": [{"xml": xml_file, field: "not_a_list"}]},
                    headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("err") == "build_set_invalid", d
    assert field in d.get("error", ""), d


# --------------------------------------------------------------------------- #
# 3. set_yellow: indices не список или отсутствует -> bad_indices, XML не тронут
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload", [
    {},                     # indices отсутствует
    {"indices": 5},         # indices — число
    {"indices": "all"},     # indices — строка
    {"indices": None},      # indices — None
])
def test_set_yellow_bad_indices_does_not_modify_xml(client, tmp_path, payload):
    """indices не список -> bad_indices, байты XML файла идентичны до и после."""
    xml = tmp_path / "test_subs.xml"
    content = b"""<xmeml version="4">
  <sequence>
    <media>
      <video>
        <track>
          <generatoritem>
            <effect>
              <name>Subtitles</name>
              <effectid>GraphicAndType</effectid>
            </effect>
          </generatoritem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>"""
    xml.write_bytes(content)
    body = {"xml": str(xml), **payload}
    r = client.post("/api/set_yellow", json=body, headers=H)
    d = r.get_json()
    assert d.get("ok") is not True, f"Ожидался отказ, получено: {d}"
    assert d.get("err") == "bad_indices", f"Ожидался err=bad_indices, получено: {d}"
    assert xml.read_bytes() == content, "Файл XML был изменён при невалидных indices!"


# --------------------------------------------------------------------------- #
# 4. ai.py: нормализация saved base_url при маске + 10-settings.js bind
# --------------------------------------------------------------------------- #
def test_save_profile_mask_key_preserves_on_normalized_url(client, monkeypatch, tmp_path):
    """Профиль с http://127.0.0.1:1234/v1/ сохраняется при маске без смены адреса."""
    from core.aicut import config
    cfg_file = tmp_path / "ai_config.json"
    cfg_data = {
        "active": "test_prof",
        "profiles": {
            "test_prof": {
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1/",
                "api_key": "secret-key-123",
                "model": "qwen2.5",
            }
        }
    }
    monkeypatch.setattr(config, "AI_CONFIG_PATH", str(cfg_file))
    import json
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

    # Сохраняем тот же профиль с маской ключа и URL без слэша (или со слэшем)
    r = client.post("/api/ai_config", json={
        "action": "save_profile",
        "name": "test_prof",
        "old_name": "test_prof",
        "profile": {
            "provider": "lmstudio",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "••••••",
            "model": "qwen2.5",
        }
    }, headers=H)
    d = r.get_json()
    assert d.get("ok") is True, f"Ошибка сохранения профиля: {d}"
    # Ключ должен остаться исходным
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["profiles"]["test_prof"]["api_key"] == "secret-key-123"


def test_settings_js_has_ais_url_input_binding():
    """static/app/10-settings.js слушает input на ais_url для сброса маски ключа."""
    import re
    js_path = os.path.join(ROOT, "static", "app", "10-settings.js")
    with open(js_path, encoding="utf-8") as f:
        src = f.read()
    # Проверяем наличие слушателя input на ais_url
    assert re.search(r"['\"]ais_url['\"].*?addEventListener\(\s*['\"]input['\"]", src, re.DOTALL), \
        "В 10-settings.js нет addEventListener('input') на ais_url"


# --------------------------------------------------------------------------- #
# 5. videogen.py: регресс jstr — числа в строковых полях не дают 500
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("endpoint,payload", [
    ("/api/video_probe", {"url": 123}),
    ("/api/video_models", {"model": 123}),
    ("/api/video_history", {"action": 123, "key": 123}),
])
def test_videogen_numeric_fields_survive_without_500(client, endpoint, payload):
    """Откат jstr на .strip() давал бы AttributeError и 500 на числах."""
    r = client.post(endpoint, json=payload, headers=H)
    assert r.status_code != 500, f"{endpoint} упал в 500 на {payload}"
