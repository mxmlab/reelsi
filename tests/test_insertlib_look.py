# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для заданий ET1/ET2: поле look в ядре базы вставок и пробросы до подбора.

look — приписка стиля картинки (из image_prompts.a.extra спикера). Спикеры с одинаковой
припиской автоматически делят одну библиотеку: add_generated пишет стиль в запись,
build_index хранит его при рескане, а match_many правит РАНГ, не трогая score — свой
стиль вперёд (+0.05), общая картинка без стиля как была (0), чужой стиль назад (-0.15).
ET2 — пробросы: обёртка match(look=...), резолв приписки в /api/insertlib_match.
Временный индекс — через REELSI_INSERTLIB (боевой insertlib.json не трогаем).

Запуск:  python -m pytest reelsi/tests/test_insertlib_look.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import insertlib  # noqa: E402
import api  # noqa: E402


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _look_fixture(tmp_path, monkeypatch):
    """Три записи с ОДИНАКОВЫМ вектором (свой стиль, пустой, чужой) — фикстура ET1.
    -> (пути own/general/foreign), индекс уже поднят и эмбеддер запросов замокан."""
    p_own = tmp_path / "own.png"
    p_general = tmp_path / "general.png"
    p_foreign = tmp_path / "foreign.png"
    for p in (p_own, p_general, p_foreign):
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    v = [1.0, 0.0]                          # одинаковый вектор у всех трёх
    items = [
        {"path": str(p_own), "name": "own.png", "type": "photo", "used": 0,
         "desc": "dark themed item", "emb": v, "added": 100.0, "look": "at dark background"},
        {"path": str(p_general), "name": "general.png", "type": "photo", "used": 0,
         "desc": "general stock item", "emb": v, "added": 101.0},
        {"path": str(p_foreign), "name": "foreign.png", "type": "photo", "used": 0,
         "desc": "bright themed item", "emb": v, "added": 102.0, "look": "at bright background"},
    ]
    idx_path = tmp_path / "insertlib.json"
    idx_data = {"dirs": [str(tmp_path)], "emb_model": "test-embed",
                "emb_tag": insertlib.EMB_TAG, "items": items}
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None
    monkeypatch.setattr(insertlib, "_emb_queries", lambda queries, model: [v])
    return p_own, p_general, p_foreign


def test_norm_look_collapses_and_lowercases():
    """_norm_look: схлопывает пробелы и регистр, из пустого даёт пустое."""
    assert insertlib._norm_look("  At   Dark  Background ") == "at dark background"
    assert insertlib._norm_look("at dark background") == "at dark background"
    assert insertlib._norm_look("") == ""
    assert insertlib._norm_look("   ") == ""
    assert insertlib._norm_look(None) == ""


def test_add_generated_stores_look_and_build_index_keeps_it(tmp_path, monkeypatch):
    """add_generated(look=...) кладёт нормализованный стиль в запись; повторный
    build_index (рескан) его сохраняет."""
    idx_path = str(tmp_path / "insertlib.json")
    monkeypatch.setenv("REELSI_INSERTLIB", idx_path)
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None

    dest_dir = str(tmp_path / "library")
    json.dump({"items": [], "dirs": [], "emb_model": "", "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    path = insertlib.add_generated(b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "3d liver icon",
                                   dest_dir, embed=False, look="  AT dark  background ")

    entry = insertlib._load()["items"][0]
    assert entry["path"] == path
    assert entry["look"] == "at dark background"

    insertlib.build_index([dest_dir], use_emb=False)
    entry2 = insertlib._load()["items"][0]
    assert entry2["look"] == "at dark background"


def test_match_many_look_orders_own_general_foreign(tmp_path, monkeypatch):
    """match_many(look=X): из трёх записей с ОДИНАКОВЫМ вектором (свой стиль, пустой,
    чужой) порядок свой -> общий -> чужой; запись с чужим стилем остаётся в выдаче,
    а score у всех трёх одинаковый (поправка — только в ранг)."""
    p_own, p_general, p_foreign = _look_fixture(tmp_path, monkeypatch)

    results = insertlib.match_many(["dark subject"], k=3, look="at dark background")[0]

    assert [r["path"] for r in results] == [str(p_own), str(p_general), str(p_foreign)]
    assert results[0]["look"] == "at dark background"
    assert results[1]["look"] is None
    assert results[2]["look"] == "at bright background"
    assert {r["score"] for r in results} == {1.0}


def test_match_wrapper_passes_look(tmp_path, monkeypatch):
    """Обёртка match(query, look=...): не падает и учитывает стиль — порядок
    свой -> общий -> чужой на той же фикстуре, score не меняется (ET2)."""
    p_own, p_general, p_foreign = _look_fixture(tmp_path, monkeypatch)

    results = insertlib.match("dark subject", k=3, look="at dark background")

    assert [r["path"] for r in results] == [str(p_own), str(p_general), str(p_foreign)]
    assert {r["score"] for r in results} == {1.0}


def test_api_insertlib_match_resolves_speaker_look(client, monkeypatch):
    """/api/insertlib_match: переданный спикер -> нормализованная приписка уезжает в
    match_many (резолв ТОЛЬКО здесь), без спикера и с пустой припиской — None."""
    from core import aicut
    calls = []
    monkeypatch.setattr(insertlib, "match_many",
                        lambda *a, **kw: (calls.append(kw) or [[]]))
    monkeypatch.setattr(insertlib, "info", lambda: {"emb_model": ""})

    def fake_cfg(slot, speaker=None):
        assert slot == "a"
        if speaker == "Пустой":
            return {"extra": "", "pos": "suffix"}
        return {"extra": "  Broken   Glass 3D ", "pos": "suffix"}
    monkeypatch.setattr(aicut, "resolve_image_prompt_cfg", fake_cfg)

    r = client.post("/api/insertlib_match", json={"queries": [{"q": "vial"}], "k": 5,
                    "speaker": "Тест"}, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert calls[-1]["look"] == "broken glass 3d"

    r = client.post("/api/insertlib_match", json={"queries": [{"q": "vial"}], "k": 5},
                    headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert calls[-1]["look"] is None

    r = client.post("/api/insertlib_match", json={"queries": [{"q": "vial"}], "k": 5,
                    "speaker": "Пустой"}, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert calls[-1]["look"] is None
