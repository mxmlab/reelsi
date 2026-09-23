# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: пригодность описания вставки, поле ru и пересчёт эмбеддингов.

Запуск:  python -m pytest reelsi/tests/test_insertlib_desc.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib


def test_needs_desc_six_cases(tmp_path):
    """needs_desc на 6 случаях: пусто, desc==имя файла, 1 слово, нормальное en, нормальное ru, user."""
    test_file = str(tmp_path / "folder" / "my_cool_image.png")

    # 1. Пусто (desc="", None, пробелы)
    assert insertlib.needs_desc({"desc": "", "path": test_file}) is True
    assert insertlib.needs_desc({"desc": "   ", "path": test_file}) is True
    assert insertlib.needs_desc({"desc": None, "path": test_file}) is True

    # 2. desc == имя файла (_norm_text)
    norm_name = insertlib._norm_text(test_file)
    assert insertlib.needs_desc({"desc": norm_name, "path": test_file}) is True

    # 3. Меньше 2 значимых слов (слово = [a-zа-яё]{3,})
    assert insertlib.needs_desc({"desc": "dog", "path": test_file}) is True
    assert insertlib.needs_desc({"desc": "кот", "path": test_file}) is True
    assert insertlib.needs_desc({"desc": "a 123", "path": test_file}) is True

    # 4. Нормальное английское описание (>= 2 значимых слов)
    assert insertlib.needs_desc({"desc": "3d icon of cup", "path": test_file}) is False
    assert insertlib.needs_desc({"desc": "golden trophy render", "path": test_file}) is False

    # 5. Нормальное русское описание (>= 2 значимых слов)
    assert insertlib.needs_desc({"desc": "золотой кубок на столе", "path": test_file}) is False
    assert insertlib.needs_desc({"desc": "стеклянная виала", "path": test_file}) is False

    # 6. desc_src="user" с одним словом — всегда пригодно (False)
    assert insertlib.needs_desc({"desc": "dog", "desc_src": "user", "path": test_file}) is False
    assert insertlib.needs_desc({"desc": "", "desc_src": "user", "path": test_file}) is False


def test_doc_text():
    """_doc_text: без ru = сам desc; с ru = склейка через пробел; лишние пробелы схлопнуты."""
    assert insertlib._doc_text("golden trophy", "") == "golden trophy"
    assert insertlib._doc_text("golden trophy", None) == "golden trophy"
    assert insertlib._doc_text("golden trophy", "золотой кубок") == "golden trophy золотой кубок"
    assert insertlib._doc_text("  golden   trophy  ", "  золотой   кубок  ") == "golden trophy золотой кубок"
    assert insertlib._doc_text("", "золотой кубок") == "золотой кубок"
    assert insertlib._doc_text(None, "золотой кубок") == "золотой кубок"
    assert insertlib._doc_text("", "") == ""


def test_adopt_with_prompt_stores_ru(tmp_path, monkeypatch):
    """adopt с prompt кладёт ru в запись индекса."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None

    src_dir = tmp_path / "downloads"
    src_dir.mkdir()
    dest_dir = tmp_path / "insert_library"
    dest_dir.mkdir()

    img_file = src_dir / "sample_insert.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    # Инициализируем пустой индекс
    json.dump({"items": [], "dirs": [], "emb_model": "", "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    items = [{
        "path": str(img_file),
        "desc": "medicine vial syringe",
        "ru": "аптечная виала со шприцем",
        "mw": 120,
        "mh": 120,
    }]

    moved = insertlib.adopt(items, str(dest_dir))
    assert len(moved) == 1

    loaded = insertlib._load()
    assert loaded is not None
    entry = loaded["items"][0]
    assert entry["desc"] == "medicine vial syringe"
    assert entry["ru"] == "аптечная виала со шприцем"
    assert entry["desc_src"] == "adopted"
    assert entry["mw"] == 120
    assert entry["mh"] == 120


def test_build_index_preserves_ru_and_emb(tmp_path, monkeypatch):
    """build_index не теряет ru при рескане и не пересчитывает вектор зря."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    insertlib._CACHE["data"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()
    img_file = media_dir / "trophy.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    old_items = [{
        "path": str(img_file),
        "name": "trophy.png",
        "type": "photo",
        "used": 1,
        "desc": "golden cup trophy",
        "desc_src": "adopted",
        "ru": "золотой кубок",
        "emb": [0.1, 0.2, 0.3],
    }]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "mock-emb",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    called_emb_docs = []

    def mock_emb_docs(texts, model):
        called_emb_docs.append(list(texts))
        return [[0.9, 0.9, 0.9] for _ in texts]

    monkeypatch.setattr(insertlib, "_emb_model", lambda: "mock-emb")
    monkeypatch.setattr(insertlib, "_emb_docs", mock_emb_docs)

    # Скан без изменений: ru сохраняется, вектор остаётся прежним (mock_emb_docs не вызывается для этого элемента)
    insertlib.build_index([str(media_dir)], use_emb=True)
    d1 = json.load(open(idx_path, encoding="utf-8"))
    assert len(d1["items"]) == 1
    assert d1["items"][0]["ru"] == "золотой кубок"
    assert d1["items"][0]["emb"] == [0.1, 0.2, 0.3]
    assert len(called_emb_docs) == 0


def test_set_desc_recomputes_vector_with_ru(tmp_path, monkeypatch):
    """set_desc при наличии ru склеивает desc + ' ' + ru и пересчитывает эмбеддинг."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    insertlib._CACHE["data"] = None

    img_file = tmp_path / "trophy.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    items = [{
        "path": str(img_file),
        "name": "trophy.png",
        "type": "photo",
        "used": 1,
        "desc": "golden cup",
        "desc_src": "ai",
        "ru": "золотой кубок",
        "emb": [0.1, 0.2, 0.3],
    }]
    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "mock-emb",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    called_emb_docs = []

    def mock_emb_docs(texts, model):
        called_emb_docs.append(list(texts))
        return [[0.9, 0.9, 0.9] for _ in texts]

    monkeypatch.setattr(insertlib, "_emb_model", lambda: "mock-emb")
    monkeypatch.setattr(insertlib, "_emb_docs", mock_emb_docs)

    res = insertlib.set_desc(str(img_file), "shiny award cup")
    assert res.get("ok") is True
    assert res.get("desc") == "shiny award cup"

    d = json.load(open(idx_path, encoding="utf-8"))
    entry = d["items"][0]
    assert entry["desc"] == "shiny award cup"
    assert entry["ru"] == "золотой кубок"
    assert entry["desc_src"] == "user"
    assert entry["emb"] == [0.9, 0.9, 0.9]

    assert len(called_emb_docs) == 1
    assert called_emb_docs[0] == ["shiny award cup золотой кубок"]


def test_auto_describe_picks_empty_adopted_desc(tmp_path, monkeypatch):
    """auto_describe(only_missing=True) берёт в работу запись с desc_src='adopted' и пустым
    описанием, но пишет ТОЛЬКО в vis: desc и desc_src не трогает."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    insertlib._CACHE["data"] = None

    img_file = tmp_path / "adopted_empty.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    items = [{
        "path": str(img_file),
        "name": "adopted_empty.png",
        "type": "photo",
        "used": 1,
        "desc": "",                     # Пустое описание
        "desc_src": "adopted",          # Ранее блокировало auto_describe!
        "ru": "",
        "emb": None,
    }]
    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "mock-emb",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    try:
        from core import aicut
        monkeypatch.setattr(aicut, "ensure_loaded", lambda model: None)
    except Exception:
        pass

    monkeypatch.setattr(insertlib, "_vision_model", lambda: "mock-vl")
    monkeypatch.setattr(insertlib, "describe_file", lambda path, model: "a fresh 3D icon of a microscope")
    monkeypatch.setattr(insertlib, "_emb_model", lambda: "mock-emb")
    monkeypatch.setattr(insertlib, "_emb_docs", lambda texts, model: [[0.5, 0.5, 0.5] for _ in texts])

    res = insertlib.auto_describe(only_missing=True)
    assert res.get("count") == 1

    loaded = insertlib._load()
    entry = loaded["items"][0]
    assert entry["vis"] == "a fresh 3D icon of a microscope"
    assert (entry["desc"] or "").strip() == ""     # desc не трогаем
    assert entry["desc_src"] == "adopted"          # desc_src не трогаем
    assert entry["emb"] == [0.5, 0.5, 0.5]
