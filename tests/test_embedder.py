# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модуль эмбеддера `core.embedder`: загрузка, промпты запроса и документа, батчи картинок.

Пакет `sentence_transformers` подменяется ЦЕЛИКОМ через `sys.modules`: в CI его может не
быть, а веса EmbeddingGemma тесты не читают. Картинки — настоящие PNG, их открывает Pillow.
"""
import io
import sys
import types
from typing import Any

import pytest
from PIL import Image

from core import embedder


def _fake_st(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Подменный `sentence_transformers`: запоминает загрузки и вызовы `encode`."""
    mod = types.ModuleType("sentence_transformers")
    mod.LOADS = []  # type: ignore[attr-defined]
    mod.CALLS = []  # type: ignore[attr-defined]

    class SentenceTransformer:
        def __init__(self, name: str, device: str | None = None) -> None:
            mod.LOADS.append((name, device))  # type: ignore[attr-defined]

        def encode(self, inputs: Any, prompt_name: str | None = None,
                   normalize_embeddings: bool = False) -> list[list[float]]:
            mod.CALLS.append({"n": len(inputs), "prompt_name": prompt_name,  # type: ignore[attr-defined]
                              "normalize": normalize_embeddings,
                              "types": {type(x).__name__ for x in inputs}})
            return [[1.0, 0.0] for _ in inputs]

    mod.SentenceTransformer = SentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return mod


def _png(color: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (color, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_load_берёт_имя_модели_и_устройство(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load` строит EmbeddingGemma 2 на переданном устройстве — имя модели одно на весь модуль."""
    fake = _fake_st(monkeypatch)
    embedder.load("cuda")
    assert fake.LOADS == [("google/embeddinggemma-2", "cuda")]  # type: ignore[attr-defined]
    assert embedder.MODEL_ID == "google/embeddinggemma-2"


def test_запрос_и_документ_кодируются_разными_промптами(monkeypatch: pytest.MonkeyPatch) -> None:
    """query -> prompt_name=SearchQuery, document -> Document; векторы нормируются.

    Мутация: поменять промпты местами или убрать prompt_name — тест краснеет.
    """
    fake = _fake_st(monkeypatch)
    model = embedder.load("cpu")
    vecs = embedder.encode_texts(model, ["собака на пляже"], "query")
    embedder.encode_texts(model, ["boxer hitting a punching bag"], "document")
    calls = fake.CALLS  # type: ignore[attr-defined]
    assert [c["prompt_name"] for c in calls] == ["SearchQuery", "Document"]
    assert all(c["normalize"] is True for c in calls)
    assert vecs == [[1.0, 0.0]]


def test_неизвестный_вид_текста_отказ(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind вне query/document — ValueError, а не молчаливый промпт по умолчанию."""
    _fake_st(monkeypatch)
    model = embedder.load("cpu")
    with pytest.raises(ValueError):
        embedder.encode_texts(model, ["x"], "passage")


def test_пустой_список_текстов_не_зовёт_модель(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_st(monkeypatch)
    model = embedder.load("cpu")
    assert embedder.encode_texts(model, [], "query") == []
    assert fake.CALLS == []  # type: ignore[attr-defined]


def test_картинки_режутся_батчами_по_16_как_pil_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """40 картинок -> три вызова encode по 16, 16 и 8; каждая картинка приходит PIL.Image без префикса.

    Мутация: IMAGE_BATCH=64 или передача байтов вместо PIL.Image — тест краснеет.
    """
    fake = _fake_st(monkeypatch)
    model = embedder.load("cpu")
    blobs = [_png(i) for i in range(40)]
    vecs = embedder.encode_images(model, blobs)
    calls = fake.CALLS  # type: ignore[attr-defined]
    assert embedder.IMAGE_BATCH == 16
    assert [c["n"] for c in calls] == [16, 16, 8]
    assert all(c["types"] == {"Image"} for c in calls)
    assert all(c["prompt_name"] is None for c in calls)
    assert len(vecs) == 40
