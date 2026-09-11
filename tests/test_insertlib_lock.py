# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание BU: под глобальным локом _LOCK не должно быть ни одного сетевого вызова.

_emb_docs() ходит в LM Studio (timeout=120). Пока он висит, ЛЮБОЙ другой запрос к базе
вставок (подбор, info, миниатюры) стоял в очереди за локом — база выглядела мёртвой до
перезагрузки сервера. Тест: подменяем _emb_docs на функцию, ждущую события, и из другого
потока проверяем, что _LOCK в этот момент СВОБОДЕН. Если сетевой вызов снова уедет под
лок — acquire(blocking=False) вернёт False, и тест упадёт.

Запуск:  python -m pytest reelsi/tests/test_insertlib_lock.py -q
"""
import json
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _mini_index(tmp_path, monkeypatch, items, emb_model="nomic-x"):
    """Мини-индекс в temp. emb_model задаём обязательно: иначе код пойдёт в ЖИВОЙ
    LM Studio на localhost:1234 и тест внезапно начнёт зависеть от машины."""
    from core import insertlib
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    for it in items:
        open(it["path"], "wb").write(b"x")
    json.dump({"items": items, "dirs": [], "emb_model": emb_model,
               "emb_tag": insertlib.EMB_TAG},
              open(insertlib.INDEX_PATH, "w", encoding="utf-8"))
    insertlib._CACHE["data"] = None
    return insertlib


def _item(path, name="a.png", desc="liver 3d icon"):
    return {"path": str(path), "name": name, "type": "photo", "used": 0, "desc": desc}


def _make_case(case, tmp_path, monkeypatch):
    """Подготовить индекс и вернуть вызов функции, которая в пути выполнения дойдёт
    до _emb_docs (а значит — до подменяемого сетевого вызова)."""
    if case == "add_generated":
        lib = _mini_index(tmp_path, monkeypatch, [])
        def call():
            return lib.add_generated(b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "liver 3d icon",
                                     str(tmp_path / "gen"), embed=True)
    elif case == "embed_items":
        p = tmp_path / "a.png"
        lib = _mini_index(tmp_path, monkeypatch, [_item(p)])
        def call():
            return lib.embed_items([(str(p), "liver 3d icon")])
    elif case == "set_desc":
        p = tmp_path / "a.png"
        lib = _mini_index(tmp_path, monkeypatch, [_item(p)])
        def call():
            return lib.set_desc(str(p), "kidney organ")
    elif case == "adopt":
        p = tmp_path / "new.png"
        lib = _mini_index(tmp_path, monkeypatch, [])
        def call():
            return lib._index_adopt({}, {str(p): "kidney organ"})
    else:                                        # ensure_emb_tag
        p = tmp_path / "a.png"
        lib = _mini_index(tmp_path, monkeypatch, [_item(p)])
        d = json.load(open(lib.INDEX_PATH, encoding="utf-8"))
        d["emb_tag"] = "q1"                      # старая схема -> нужен пересчёт
        json.dump(d, open(lib.INDEX_PATH, "w", encoding="utf-8"))
        lib._CACHE["data"] = None
        def call():
            return lib._ensure_emb_tag()
    return lib, call


@pytest.mark.parametrize("case", [
    "add_generated", "embed_items", "set_desc", "adopt", "ensure_emb_tag",
])
def test_lock_free_during_emb_docs(case, tmp_path, monkeypatch):
    """Пока крутится «сетевой» _emb_docs, глобальный _LOCK свободен: параллельный
    запрос к базе (подбор, info, миниатюры) не должен стоять в очереди."""
    lib, call = _make_case(case, tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def fake_emb_docs(texts, model):
        entered.set()
        release.wait(10)                         # держим «сетевой вызов» открытым
        return [[0.5, 0.5, 0.5, 0.5]] * len(texts)

    monkeypatch.setattr(lib, "_emb_docs", fake_emb_docs)
    out = {}

    def worker():
        try:
            out["res"] = call()
        except BaseException as e:               # иначе умирающий поток не упадёт в тест
            out["err"] = e

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(10), "путь не дошёл до _emb_docs — тест ничего не проверяет"
    free = lib._LOCK.acquire(blocking=False)
    if free:
        lib._LOCK.release()
    release.set()
    t.join(10)
    assert "err" not in out, "рабочий поток упал: %r" % out.get("err")
    assert not t.is_alive(), "рабочий поток не завершился"
    assert free, "_LOCK занят во время _emb_docs — сетевой вызов держит глобальный лок"
