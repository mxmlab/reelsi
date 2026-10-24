# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""База вставок без LM Studio: векторы от сервиса моделей, у файла без текста — вектор картинки.

Сервис моделей здесь подменяется на уровне клиента (`model_service.embed_texts` /
`embed_images`) или обёрток `insertlib._emb_*`: тесты не поднимают процесс и не грузят
EmbeddingGemma. Индекс — во временной папке через INDEX_PATH (боевой insertlib.json не трогаем).
"""
import base64
import json
import os
from typing import Any

import pytest

from core import insertlib, model_service
from core.model_service import ServiceUnavailable

_JPEG = base64.b64encode(b"\xff\xd8\xff fake jpeg bytes").decode()


def _index(tmp_path, monkeypatch, items: list[dict[str, Any]], tag: str | None = None) -> Any:
    """Индекс во временной папке. По умолчанию — текущая схема (EMB_TAG)."""
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "idx.json"))
    for it in items:
        with open(it["path"], "wb") as f:
            f.write(b"x")
    with open(insertlib.INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items, "dirs": [], "emb_model": insertlib._emb_model(),
                   "emb_tag": tag or insertlib.EMB_TAG}, f, ensure_ascii=False)
    insertlib._CACHE["data"] = None
    return insertlib


def _read(lib: Any) -> dict[str, Any]:
    with open(lib.INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_решение_текст_или_картинка() -> None:
    """Один вектор на файл: текст, если есть описание; картинка — если описания нет вовсе.

    «Нет описания»: исходной фразы нет (desc_src == name) и зрительного (vis) тоже нет,
    либо предметный текст пуст. Мутация: всегда отдавать текст — тест краснеет.
    """
    assert insertlib._vector_source({"desc_src": "name", "desc": "a", "vis": ""}) == ("image", "")
    assert insertlib._vector_source({"desc_src": "user", "desc": "", "vis": ""}) == ("image", "")
    assert insertlib._vector_source({"desc_src": "name", "desc": "x",
                                     "vis": "toilet bowl with paper money"})[0] == "text"
    assert insertlib._vector_source({"desc_src": "generated", "desc": "liver 3d icon"}) == ("text", "liver")


def test_файл_без_текста_получает_вектор_картинки(tmp_path, monkeypatch) -> None:
    """Файл без описания уходит КАРТИНКОЙ (кадр через `_thumb_b64`), файл с текстом — текстом.

    Мутация: `_item_vectors` всегда в текст — здесь краснеет emb_src == "image".
    """
    plain = tmp_path / "plain.png"
    labeled = tmp_path / "labeled.png"
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(plain), "name": "plain.png", "type": "photo", "used": 0,
         "desc": "plain", "desc_src": "name"},
        {"path": str(labeled), "name": "labeled.png", "type": "photo", "used": 0,
         "desc": "liver organ", "desc_src": "generated"},
    ])
    texts, images = [], []
    monkeypatch.setattr(lib, "_thumb_b64", lambda p: _JPEG if p == str(plain) else None)
    monkeypatch.setattr(lib, "_emb_docs", lambda tx: texts.extend(tx) or [[0.1, 0.2]] * len(tx))
    monkeypatch.setattr(lib, "_emb_images", lambda bl: images.extend(bl) or [[0.9, 0.8]] * len(bl))
    res = lib._item_vectors([json.loads(json.dumps(it)) for it in _read(lib)["items"]])
    assert res[0] == ("image", [0.9, 0.8])                     # без текста — картинка
    assert res[1] == ("text", [0.1, 0.2])                      # с текстом — текст
    assert texts == ["liver organ"]
    assert images == [base64.b64decode(_JPEG)]


def test_видео_без_текста_берёт_кадр_через_thumb(tmp_path, monkeypatch) -> None:
    """У видео без описания вектор — по кадру из `_thumb_b64` (середина ролика), не по пути."""
    clip = tmp_path / "clip.mp4"
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(clip), "name": "clip.mp4", "type": "video", "used": 0,
         "desc": "clip", "desc_src": "name"},
    ])
    asked = []
    monkeypatch.setattr(lib, "_thumb_b64", lambda p: asked.append(p) or _JPEG)
    monkeypatch.setattr(lib, "_emb_images", lambda bl: [[0.5, 0.5]] * len(bl))
    src, vec = lib._item_vectors([{"path": str(clip), "desc_src": "name", "desc": "clip", "vis": ""}])[0]
    assert (src, vec) == ("image", [0.5, 0.5])
    assert asked == [str(clip)]


def test_сбой_кадра_оставляет_файл_без_вектора(tmp_path, monkeypatch) -> None:
    """Кадр не вытащился (ffmpeg нет) — файл без вектора, остальные не страдают."""
    bad = tmp_path / "bad.png"
    lib = _index(tmp_path, monkeypatch, [])
    monkeypatch.setattr(lib, "_thumb_b64", lambda p: None)
    monkeypatch.setattr(lib, "_emb_images", lambda bl: pytest.fail("без кадра вызова быть не должно"))
    assert lib._item_vectors([{"path": str(bad), "desc_src": "name", "desc": "bad", "vis": ""}]) \
        == [("image", None)]


def test_текст_идёт_пачками_по_EMB_CHUNK(tmp_path, monkeypatch) -> None:
    """Сотни описаний — не один огромный вызов: пачки по EMB_CHUNK, порядок ответов сохраняется."""
    lib = _index(tmp_path, monkeypatch, [])
    calls = []
    monkeypatch.setattr(lib, "_emb_docs", lambda tx: calls.append(len(tx)) or [[float(i)] for i in range(len(tx))])
    items = [{"path": "p%d.png" % i, "desc_src": "generated", "desc": "word%d alpha" % i}
             for i in range(lib.EMB_CHUNK + 3)]
    out = lib._item_vectors(items)
    assert calls == [lib.EMB_CHUNK, 3]
    assert len(out) == len(items) and all(src == "text" for src, _ in out)


def test_запрос_идёт_видом_query(tmp_path, monkeypatch) -> None:
    """Подбор кодирует запрос с видом «query», а описания — «document» (промпты модели разные).

    Мутация: запрос с видом «document» — тест краснеет.
    """
    p = tmp_path / "a.png"
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(p), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver organ", "desc_src": "generated", "emb": [1.0, 0.0], "emb_src": "text"},
    ])
    seen: list[tuple[list[str], str]] = []

    def record(texts: list[str], kind: str) -> list[list[float]]:
        seen.append((list(texts), kind))
        return [[1.0, 0.0]] * len(texts)

    monkeypatch.setattr(model_service, "embed_texts", record)
    res = lib.match_many(["liver organ"], k=1)
    assert seen == [(["liver organ"], "query")]
    assert res[0][0]["path"] == str(p)


def test_без_сервиса_подбор_токенами_не_падает(tmp_path, monkeypatch) -> None:
    """Сервиса нет: подбор уходит на токены и ничего не падает — отказ пишется в журнал."""
    p = tmp_path / "a.png"
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(p), "name": "a.png", "type": "photo", "used": 0,
         "desc": "liver organ", "desc_src": "generated", "emb": [1.0, 0.0], "emb_src": "text"},
    ])

    def down(*a: Any, **k: Any) -> None:
        raise ServiceUnavailable("сервис моделей не поднялся")

    monkeypatch.setattr(model_service, "embed_texts", down)
    res = lib.match_many(["liver organ"], k=1)
    assert res[0] and res[0][0]["path"] == str(p)


def test_пересчёт_схемы_переводит_тексты_и_картинки(tmp_path, monkeypatch) -> None:
    """Индекс по старой схеме (q5): пересчёт одним батчем берёт и текстовые, и картиночные записи.

    Файл без текста без пересчёта картинок остался бы без вектора. Мутация: пропускать
    картиночные записи в `_ensure_emb_tag` — тест краснеет на emb_src == "image".
    """
    t, im = tmp_path / "t.png", tmp_path / "i.png"
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(t), "name": "t.png", "type": "photo", "used": 0,
         "desc": "liver organ", "desc_src": "generated", "emb": [1.0, 0.0]},
        {"path": str(im), "name": "i.png", "type": "photo", "used": 0,
         "desc": "i", "desc_src": "name", "emb": [1.0, 0.0]},
    ], tag="q5")
    monkeypatch.setattr(lib, "_thumb_b64", lambda p: _JPEG if p == str(im) else None)
    monkeypatch.setattr(lib, "_emb_docs", lambda tx: [[0.0, 1.0]] * len(tx))
    monkeypatch.setattr(lib, "_emb_images", lambda bl: [[0.5, 0.5]] * len(bl))
    assert lib._ensure_emb_tag() is True
    d = _read(lib)
    assert d["emb_tag"] == lib.EMB_TAG
    by_name = {it["name"]: it for it in d["items"]}
    assert by_name["t.png"]["emb"] == [0.0, 1.0] and by_name["t.png"]["emb_src"] == "text"
    assert by_name["i.png"]["emb"] == [0.5, 0.5] and by_name["i.png"]["emb_src"] == "image"


def test_порог_автоподбора_082_не_берёт_084_берёт(tmp_path, monkeypatch) -> None:
    """Порог AUTO_COS = 0.83 на шкале EmbeddingGemma 2: кандидат с косинусом 0.82 и без лексического
    совпадения — не автоподбор; с косинусом 0.84 — автоподбор.

    Мутация: вернуть AUTO_COS = 0.30 — кандидат 0.82 станет автоподбором, тест краснеет.
    """
    assert insertlib.AUTO_COS == 0.83
    low, high = tmp_path / "zeta_one.png", tmp_path / "theta_two.png"
    # Вектор запроса [1, 0]: косинус равен первой компоненте нормированного вектора кандидата.
    v_low = [0.82, (1 - 0.82 ** 2) ** 0.5]
    v_high = [0.84, (1 - 0.84 ** 2) ** 0.5]
    lib = _index(tmp_path, monkeypatch, [
        {"path": str(low), "name": "zeta_one.png", "type": "photo", "used": 0,
         "desc": "qqq", "desc_src": "generated", "emb": v_low, "emb_src": "text"},
        {"path": str(high), "name": "theta_two.png", "type": "photo", "used": 0,
         "desc": "www", "desc_src": "generated", "emb": v_high, "emb_src": "text"},
    ])
    monkeypatch.setattr(lib, "_emb_queries", lambda queries: [[1.0, 0.0]])
    by_path = {r["path"]: r for r in lib.match_many(["alpha"], k=2)[0]}
    assert by_path[str(low)]["lex"] == 0.0 and by_path[str(high)]["lex"] == 0.0   # лексики нет
    assert by_path[str(low)]["score"] == 0.82 and by_path[str(low)]["auto"] is False
    assert by_path[str(high)]["score"] == 0.84 and by_path[str(high)]["auto"] is True


def test_ни_одного_упоминания_lm_studio_в_insertlib() -> None:
    """Сторож: база вставок больше не ходит в LM Studio — ни адреса, ни слов, ни переменных."""
    src = open(insertlib.__file__, encoding="utf-8").read().lower()
    for word in ("localhost:1234", "lmstudio", "lm studio", "embed_text_lm", "/chat/completions"):
        assert word not in src, word
    for name in ("describe_file", "auto_describe", "_vision_model", "_http_json", "_embed("):
        assert name not in src, name


def test_описания_через_роуты_удалены(tmp_path) -> None:
    """Роуты «Описать через ИИ» удалены из API: ни одного `insertlib_describe*` в модулях API."""
    api_dir = os.path.join(os.path.dirname(insertlib.__file__), "..", "api")
    for name in os.listdir(api_dir):
        if name.endswith(".py"):
            text = open(os.path.join(api_dir, name), encoding="utf-8").read()
            assert "insertlib_describe" not in text, name
