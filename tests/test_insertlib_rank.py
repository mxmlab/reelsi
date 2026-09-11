# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задания EL: подбор по предмету и вес редких слов (лексический ранг).

Запуск:  python -m pytest reelsi/tests/test_insertlib_rank.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib


def test_subject_text():
    """_subject_text: чистит слова стиля и цвета, оставляет только предмет."""
    raw = "3D render of broken eyeglasses on dark backdrop"
    assert insertlib._subject_text(raw) == "of broken eyeglasses on"

    raw2 = "glowing blue syringe with medical liquid on white background"
    assert insertlib._subject_text(raw2) == "syringe with liquid on"

    # Если всё состоит из стилевых слов, возвращает исходную строку
    assert insertlib._subject_text("3d icon render") == "3d icon render"
    assert insertlib._subject_text("") == ""
    assert insertlib._subject_text(None) == ""


def test_lexical_rank_rare_word_boost(tmp_path, monkeypatch):
    """Фикстура из 5 записей: 1 с редким словом (df=1), 4 с частыми (df=4).
    Запрос с редким словом поднимает первую запись на 1 место, даже если косинус у неё ниже (0.55 против 0.65).
    """
    p1 = tmp_path / "retatrutide_pen.png"
    p2 = tmp_path / "item2.png"
    p3 = tmp_path / "item3.png"
    p4 = tmp_path / "item4.png"
    p5 = tmp_path / "item5.png"

    for p in (p1, p2, p3, p4, p5):
        p.write_text("fake")

    # Векторы размерности 2
    # Запрос qvec = [1.0, 0.0]
    # it1: vector [0.55, 0.83516] -> cos ~ 0.55
    # it2..5: vector [0.65, 0.76] -> cos ~ 0.65
    v_q = [1.0, 0.0]
    v_1 = [0.55, 0.835164]
    v_common = [0.65, 0.759934]

    items = [
        {"path": str(p1), "name": "retatrutide_pen.png", "type": "photo", "used": 0,
         "desc": "retatrutide pen injector", "emb": v_1, "added": 100.0},
        {"path": str(p2), "name": "item2.png", "type": "photo", "used": 0,
         "desc": "common gadget device injector", "emb": v_common, "added": 100.0},
        {"path": str(p3), "name": "item3.png", "type": "photo", "used": 0,
         "desc": "common gadget device tool", "emb": v_common, "added": 100.0},
        {"path": str(p4), "name": "item4.png", "type": "photo", "used": 0,
         "desc": "common gadget device machine", "emb": v_common, "added": 100.0},
        {"path": str(p5), "name": "item5.png", "type": "photo", "used": 0,
         "desc": "common gadget device apparatus", "emb": v_common, "added": 100.0},
    ]

    idx_path = tmp_path / "insertlib.json"
    idx_data = {
        "dirs": [str(tmp_path)],
        "emb_model": "test-embed",
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")

    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None

    # Мокаем _emb_queries
    monkeypatch.setattr(insertlib, "_emb_queries", lambda queries, model: [v_q])

    results = insertlib.match_many(["retatrutide pen"], k=5)[0]

    assert len(results) == 5
    # it1 с редким словом retatrutide выходит на 1-е место
    top1 = results[0]
    assert top1["path"] == str(p1)
    # score равен чистому косинусу ~0.55
    assert top1["score"] == 0.55
    # lex > 0
    assert top1["lex"] > 0.0
    # Остальные идут следом со score = 0.65 и меньшим/нулевым lex
    assert results[1]["score"] == 0.65


def test_score_equals_pure_cosine_and_lex_separated(tmp_path, monkeypatch):
    """score в выдаче match_many равен чистому косинусу, а лексический бонус отдаётся в поле lex."""
    p1 = tmp_path / "pen.png"
    p1.write_text("fake")

    # Вектор совпадает с запросом -> cos = 1.0
    items = [
        {"path": str(p1), "name": "pen.png", "type": "photo", "used": 0,
         "desc": "injection pen", "emb": [1.0, 0.0], "added": 100.0},
    ]
    idx_path = tmp_path / "insertlib.json"
    idx_data = {
        "dirs": [str(tmp_path)],
        "emb_model": "test-embed",
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None

    monkeypatch.setattr(insertlib, "_emb_queries", lambda queries, model: [[1.0, 0.0]])

    results = insertlib.match_many(["injection pen"], k=1)[0]
    assert len(results) == 1
    assert results[0]["score"] == 1.0
    assert "lex" in results[0]
    assert results[0]["lex"] == 0.35  # полное совпадение редких слов -> LEX_W * 1.0


def test_equal_cosine_tie_breaking(tmp_path, monkeypatch):
    """При равном косинусе кандидат с lex > 0 побеждает кандидата с lex == 0."""
    p1 = tmp_path / "syringe.png"
    p2 = tmp_path / "car.png"
    p1.write_text("fake")
    p2.write_text("fake")

    v = [1.0, 0.0]
    items = [
        {"path": str(p2), "name": "car.png", "type": "photo", "used": 0,
         "desc": "red automobile", "emb": v, "added": 100.0},
        {"path": str(p1), "name": "syringe.png", "type": "photo", "used": 0,
         "desc": "medical syringe", "emb": v, "added": 100.0},
    ]
    idx_path = tmp_path / "insertlib.json"
    idx_data = {
        "dirs": [str(tmp_path)],
        "emb_model": "test-embed",
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None

    monkeypatch.setattr(insertlib, "_emb_queries", lambda queries, model: [v])

    results = insertlib.match_many(["syringe"], k=2)[0]
    assert len(results) == 2
    # Оба имеют score 1.0
    assert results[0]["score"] == 1.0
    assert results[1]["score"] == 1.0
    # Но syringe имеет lex > 0, поэтому выходит на 1 место
    assert results[0]["path"] == str(p1)
    assert results[0]["lex"] > 0.0
    assert results[1]["path"] == str(p2)
    assert results[1]["lex"] == 0.0


def test_auto_threshold_embedding_mode(tmp_path, monkeypatch):
    """Поле auto (bool) в режиме эмбеддинга:
    - cos >= AUTO_COS (0.30) -> True
    - cos < AUTO_COS, но lex >= AUTO_LEX (0.20) -> True (точное попадание по имени)
    - cos < AUTO_COS и lex < AUTO_LEX -> False
    """
    assert insertlib.AUTO_COS == 0.30
    assert insertlib.AUTO_COS_TOK == 0.5
    assert insertlib.AUTO_LEX == 0.20

    p_high_cos = tmp_path / "high_cos.png"
    p_exact_name = tmp_path / "revolver_cylinder.mp4"
    p_low_match = tmp_path / "low_match.png"

    for p in (p_high_cos, p_exact_name, p_low_match):
        p.write_text("fake")

    # Вектор запроса = [1.0, 0.0]
    # high_cos: cos = 0.50, lex = 0.0 -> auto = True (cos >= 0.30)
    # exact_name: cos = 0.25, lex = 0.35 -> auto = True (lex >= 0.20, cos < 0.30)
    # low_match: cos = 0.25, lex = 0.0 -> auto = False (cos < 0.30 and lex < 0.20)
    v_q = [1.0, 0.0]
    v_high = [0.50, 0.866025]
    v_low = [0.25, 0.968246]

    items = [
        {"path": str(p_high_cos), "name": "high_cos.png", "type": "photo", "used": 0,
         "desc": "generic object in room", "emb": v_high, "added": 100.0},
        {"path": str(p_exact_name), "name": "revolver_cylinder.mp4", "type": "video", "used": 0,
         "desc": "man in dark clothes", "emb": v_low, "added": 100.0},
        {"path": str(p_low_match), "name": "low_match.png", "type": "photo", "used": 0,
         "desc": "something unrelated", "emb": v_low, "added": 100.0},
    ]

    idx_path = tmp_path / "insertlib.json"
    idx_data = {
        "dirs": [str(tmp_path)],
        "emb_model": "test-embed",
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None

    monkeypatch.setattr(insertlib, "_emb_queries", lambda queries, model: [v_q])

    results = insertlib.match_many(["revolver cylinder"], k=3)[0]
    assert len(results) == 3

    by_path = {r["path"]: r for r in results}

    # high_cos: cos 0.50 >= 0.30 -> auto True
    assert by_path[str(p_high_cos)]["score"] == 0.50
    assert by_path[str(p_high_cos)]["auto"] is True

    # exact_name: cos 0.25 < 0.30, но lex 0.35 >= 0.20 -> auto True
    assert by_path[str(p_exact_name)]["score"] == 0.25
    assert by_path[str(p_exact_name)]["lex"] >= 0.20
    assert by_path[str(p_exact_name)]["auto"] is True

    # low_match: cos 0.25 < 0.30, lex 0.0 < 0.20 -> auto False
    assert by_path[str(p_low_match)]["score"] == 0.25
    assert by_path[str(p_low_match)]["lex"] < 0.20
    assert by_path[str(p_low_match)]["auto"] is False


def test_auto_threshold_token_fallback_mode(tmp_path, monkeypatch):
    """Поле auto (bool) в режиме фолбэка по токенам (без эмбеддера):
    - token score >= AUTO_COS_TOK (0.5) -> True
    - token score < AUTO_COS_TOK (0.5) -> False
    """
    p_good = tmp_path / "apple_tree.png"
    p_poor = tmp_path / "apple_orange_banana_grape.png"

    for p in (p_good, p_poor):
        p.write_text("fake")

    items = [
        {"path": str(p_good), "name": "apple_tree.png", "type": "photo", "used": 0,
         "desc": "apple tree garden", "emb": None, "added": 100.0},
        {"path": str(p_poor), "name": "apple_orange_banana_grape.png", "type": "photo", "used": 0,
         "desc": "apple orange banana grape fruit basket", "emb": None, "added": 100.0},
    ]

    idx_path = tmp_path / "insertlib.json"
    idx_data = {
        "dirs": [str(tmp_path)],
        "emb_model": "",  # нет эмбеддера
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    idx_path.write_text(json.dumps(idx_data), encoding="utf-8")
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(idx_path))
    insertlib._CACHE["data"] = None

    # Запрос из 2 токенов: apple tree -> good имеет 2/2 = 1.0 >= 0.5 (auto=True)
    # Запрос из 4 токенов: apple tree garden fruit -> good имеет 3/4 = 0.75 >= 0.5 (auto=True)
    # Запрос из 4 токенов: apple car sky road -> poor имеет 1/4 = 0.25 < 0.5 (auto=False)
    res_good = insertlib.match_many(["apple tree"], k=2)[0]
    assert len(res_good) >= 1
    assert res_good[0]["path"] == str(p_good)
    assert res_good[0]["score"] >= 0.5
    assert res_good[0]["auto"] is True

    res_poor = insertlib.match_many(["apple car sky road"], k=2)[0]
    assert len(res_poor) >= 1
    # poor has 1 token match out of 4 -> score = 0.25 < 0.5 -> auto=False
    poor_item = [r for r in res_poor if r["path"] == str(p_poor)][0]
    assert poor_item["score"] == 0.25
    assert poor_item["auto"] is False

