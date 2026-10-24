# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: два текстовых поля вместо одного — desc и vis.

desc = что ЗАДУМАНО (исходная фраза/запрос), vis = что ВИДНО на картинке (только
vision). Vision больше никогда не пишет в desc; миграция в build_index перекладывает
старые vision-описания (desc_src='ai') в vis.

Запуск:  python -m pytest reelsi/tests/test_insertlib_vis.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib


def test_doc_text_joins_three_fields_in_order():
    """_doc_text склеивает desc, ru, vis в порядке «задумано -> русское -> видно» и
    пропускает пустые."""
    assert insertlib._doc_text("money bills flushed", "деньги в унитаз", "toilet bowl with paper money") \
        == "money bills flushed деньги в унитаз toilet bowl with paper money"
    assert insertlib._doc_text("money bills flushed", "", "toilet bowl with paper money") \
        == "money bills flushed toilet bowl with paper money"
    assert insertlib._doc_text("", "", "toilet bowl with paper money") == "toilet bowl with paper money"
    assert insertlib._doc_text("money bills flushed", "", "") == "money bills flushed"
    assert insertlib._doc_text("", "", "") == ""
    assert insertlib._doc_text(None, None, None) == ""


def test_needs_text_false_if_either_field_is_fit():
    """needs_text ложно, если пригоден хотя бы один из desc/vis."""
    p = "C:/x/img.png"
    # оба пустые — нуждается
    assert insertlib.needs_text({"desc": "", "vis": "", "path": p}) is True
    # только desc пригоден — не нуждается
    assert insertlib.needs_text({"desc": "money bills flushed down toilet", "vis": "", "path": p}) is False
    # только vis пригоден — не нуждается
    assert insertlib.needs_text({"desc": "", "vis": "toilet bowl with paper money", "path": p}) is False
    # оба пригодны — не нуждается
    assert insertlib.needs_text({"desc": "money bills", "vis": "toilet bowl with paper money", "path": p}) is False
    # desc_src='user' неприкосновенен, даже если оба пустые
    assert insertlib.needs_text({"desc": "", "vis": "", "desc_src": "user", "path": p}) is False


def test_migration_moves_ai_desc_to_vis(tmp_path, monkeypatch):
    """Запись desc_src='ai' без vis после build_index: vis = старый desc, desc пустой,
    desc_src='name'. Повторный build_index ничего не меняет (идемпотентность)."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()
    img_file = media_dir / "vial.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    old_items = [{
        "path": str(img_file),
        "name": "vial.png",
        "type": "photo",
        "used": 1,
        "desc": "laboratory vial with red liquid sample",
        "desc_src": "ai",
        "ru": "",
        "emb": [0.1, 0.2, 0.3],
    }]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    insertlib.build_index([str(media_dir)], use_emb=False)
    d1 = json.load(open(idx_path, encoding="utf-8"))
    it1 = d1["items"][0]
    assert it1["vis"] == "laboratory vial with red liquid sample"
    assert (it1["desc"] or "").strip() == ""
    assert it1["desc_src"] == "name"

    insertlib.build_index([str(media_dir)], use_emb=False)
    d2 = json.load(open(idx_path, encoding="utf-8"))
    it2 = d2["items"][0]
    assert it2["vis"] == "laboratory vial with red liquid sample"
    assert (it2["desc"] or "").strip() == ""
    assert it2["desc_src"] == "name"


def test_score_tokens_sees_vis_with_empty_desc():
    """Токенный фолбэк _score_tokens считает слова кандидата по desc+ru+vis, а не
    только по desc: запись с пустым desc и заполненным vis находится по vision-описанию."""
    it = {"desc": "", "ru": "", "vis": "toilet bowl with paper money"}
    q = {"toilet", "bowl", "paper", "money"}
    assert insertlib._score_tokens(q, it) == 1.0


def test_is_refusal_recognizes_model_refusals():
    """_is_refusal ловит типовые отказы vision (без учёта регистра) и не путает их с
    обычными описаниями."""
    for t in ("None of the objects depicted are real-world items with specific names",
              "none of the above",
              "I cannot identify the main object in this image",
              "There are no medications or pharmaceutical products shown"):
        assert insertlib._is_refusal(t), t
    for t in ("money bills flushed down toilet",
              "toilet bowl with paper money"):
        assert not insertlib._is_refusal(t), t


def test_migration_cleans_ai_desc_even_with_vis(tmp_path, monkeypatch):
    """Запись desc_src='ai' с уже заполненным vis после build_index: лучший текст остаётся
    в vis, desc (мусор слабой модели) пустой, desc_src='name'. Записи generated не трогаем —
    там фраза автора. Повторный build_index ничего не меняет."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()
    ai_file = media_dir / "vial.png"
    gen_file = media_dir / "tablet-box.png"
    ai_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    gen_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    old_items = [
        {"path": str(ai_file), "name": "vial.png", "type": "photo", "used": 1,
         "desc": "old weak gemma text", "desc_src": "ai", "ru": "",
         "vis": "laboratory vial with red liquid sample", "emb": [0.1, 0.2, 0.3]},
        {"path": str(gen_file), "name": "tablet-box.png", "type": "photo", "used": 1,
         "desc": "blister pack of tablets", "desc_src": "generated", "ru": "", "emb": None},
    ]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    insertlib.build_index([str(media_dir)], use_emb=False)
    d1 = json.load(open(idx_path, encoding="utf-8"))
    ai1 = {it["name"]: it for it in d1["items"]}["vial.png"]
    gen1 = {it["name"]: it for it in d1["items"]}["tablet-box.png"]
    assert ai1["vis"] == "laboratory vial with red liquid sample"
    assert (ai1["desc"] or "").strip() == ""
    assert ai1["desc_src"] == "name"
    assert gen1["desc"] == "blister pack of tablets"
    assert gen1["desc_src"] == "generated"

    insertlib.build_index([str(media_dir)], use_emb=False)
    d2 = json.load(open(idx_path, encoding="utf-8"))
    ai2 = {it["name"]: it for it in d2["items"]}["vial.png"]
    assert ai2["vis"] == "laboratory vial with red liquid sample"
    assert (ai2["desc"] or "").strip() == ""
    assert ai2["desc_src"] == "name"


def test_doc_text_skips_refusal_vis():
    """Отказ модели в vis не попадает в текст документа для эмбеддера, пока запись не
    переописана."""
    assert insertlib._doc_text("", "", "None of the objects depicted are real-world items") == ""
    assert insertlib._doc_text("money bills flushed", "", "none of the above") == "money bills flushed"
    assert insertlib._doc_text("money bills flushed", "", "toilet bowl with paper money") \
        == "money bills flushed toilet bowl with paper money"
