# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: долгие операции в insertlib (build_index)
не затирают свежие параллельные правки (reject, add_generated, set_desc)."""
import json
import os

import pytest

from core import insertlib

# Минимальный валидный PNG 1x1
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _isolate_insertlib(tmp_path, monkeypatch):
    """Изолируем индекс и кэш во временную директорию, отключаем сеть."""
    idx_path = str(tmp_path / "test_insertlib.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: "fake-emb-model")

    # Имитация aicut.ensure_loaded
    try:
        from core import aicut
        monkeypatch.setattr(aicut, "ensure_loaded", lambda model: None)
    except Exception:
        pass

    insertlib._CACHE["data"] = None
    insertlib._CACHE["mtime"] = 0
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    yield idx_path

    insertlib._CACHE["data"] = None
    insertlib._CACHE["mtime"] = 0


def _create_image(path, content=TINY_PNG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return str(path)


def _init_index(items, dirs=None, emb_model="fake-emb-model"):
    data = {
        "dirs": dirs or [],
        "emb_model": emb_model,
        "emb_tag": insertlib.EMB_TAG,
        "items": items,
    }
    with open(insertlib.INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mtime"] = 0
    return data


def test_build_index_preserves_concurrent_reject_and_add_generated(tmp_path, monkeypatch):
    """build_index не должен терять reject и add_generated, случившиеся во время скана/эмбеддингов."""
    media_dir = tmp_path / "media"
    path_a = _create_image(media_dir / "a.png")
    gen_dir = tmp_path / "gen"

    item_a = {"path": path_a, "name": "a.png", "type": "photo", "used": 0, "desc": "icon a",
              "desc_src": "user"}          # с текстом — путь эмбеддинга текста (не картинки)
    _init_index([item_a], dirs=[str(media_dir)])

    def fake_emb_docs(texts):
        # Во время расчёта эмбеддингов вызываем reject и add_generated
        insertlib.reject(path_a, "unwanted query")
        insertlib.add_generated(TINY_PNG, "gen icon", str(gen_dir), embed=False)
        return [[0.3] * 4 for _ in texts]

    monkeypatch.setattr(insertlib, "_emb_docs", fake_emb_docs)

    res = insertlib.build_index([str(media_dir)])
    assert res["count"] >= 1

    d = json.load(open(insertlib.INDEX_PATH, encoding="utf-8"))
    by_p = {os.path.normcase(it["path"]): it for it in d["items"]}

    assert by_p[os.path.normcase(path_a)].get("rej") == ["unwanted query"]
    assert any(it.get("desc") == "gen icon" for it in d["items"])


def test_build_index_preserves_concurrent_set_desc_and_clears_emb(tmp_path, monkeypatch):
    """build_index сохраняет set_desc, сделанный во время скана, и сбрасывает emb."""
    media_dir = tmp_path / "media"
    path_a = _create_image(media_dir / "a.png")

    item_a = {"path": path_a, "name": "a.png", "type": "photo", "used": 0, "desc": "old desc",
              "desc_src": "user"}          # с текстом — путь эмбеддинга текста (не картинки)
    _init_index([item_a], dirs=[str(media_dir)])

    calling = False

    def fake_emb_docs(texts):
        nonlocal calling
        if not calling:
            calling = True
            insertlib.set_desc(path_a, "desc updated during scan")
        return [[0.7] * 4 for _ in texts]

    monkeypatch.setattr(insertlib, "_emb_docs", fake_emb_docs)

    insertlib.build_index([str(media_dir)])

    d = json.load(open(insertlib.INDEX_PATH, encoding="utf-8"))
    by_p = {os.path.normcase(it["path"]): it for it in d["items"]}
    rec = by_p[os.path.normcase(path_a)]

    assert rec["desc"] == "desc updated during scan"
    assert rec["desc_src"] == "user"
    assert rec.get("emb") is None


def test_import_media_prefix_boundary(tmp_path):
    """import_media не должен пропускать папку tmp/library_backup при базе tmp/library."""
    lib_dir = tmp_path / "library"
    backup_dir = tmp_path / "library_backup"

    _create_image(lib_dir / "photos" / "already.png")
    _create_image(backup_dir / "need_import.png")

    res = insertlib.import_media(
        [str(backup_dir), str(lib_dir)],
        dest=str(lib_dir),
        since_ts=0,
        recursive=True,
    )

    assert res["count"] == 1
    dest_imported = lib_dir / "photos" / "need_import.png"
    assert dest_imported.exists()
