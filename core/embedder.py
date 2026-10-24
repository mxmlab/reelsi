# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Эмбеддер EmbeddingGemma 2: векторы текста и картинки для базы вставок.

ЗАЧЕМ. База вставок искала по описанию через LM Studio (nomic-embed-text-v2). Замер
2026-10-24 на 135 ручных выборах владельца «запрос → файл»: nomic — Hit@1 37.0 %,
Hit@5 74.1 %; EmbeddingGemma 2 по тому же тексту — 41.5 % и 76.3 %; по пикселям —
37.8 % и 70.4 %. Модель держит текст и картинку в ОДНОМ пространстве, поэтому файлу
без текста можно дать вектор картинки, а запрос остаётся текстом.

ЧТО ЗДЕСЬ. Загрузка модели и кодирование — без состояния. Модель держит сервис моделей
(`core.model_service`, команды `embed_text` и `embed_image`), база вставок ходит к нему,
а не сюда напрямую: одна копия весов на машину, как у распознавания и фона.

ПОЧЕМУ ОДИН ВЕКТОР НА ФАЙЛ. Слитый вектор текста и картинки проиграл и тексту (замер),
поэтому у файла вектор либо текстовый, либо картиночный — признак пишется в `emb_src`.

ПАМЯТЬ. Веса ~1.4 ГиБ видеопамяти, пик с батчем картинок — ~2.6 ГиБ. `VRAM_MIB` — цена
слота с запасом, по ней сервис ждёт свободной карты перед счётом.
"""
from __future__ import annotations

import io
from typing import Any, Sequence

# Модель и её имя в индексе: смена имени переписывает векторы базы (см. EMB_TAG в insertlib).
MODEL_ID = "google/embeddinggemma-2"
# Пик видеопамяти с батчем картинок (замер: 2617 МиБ при батче 16), округлён вверх.
VRAM_MIB = 3072
# Картинок в одном вызове кодировщика: больше — пик памяти растёт, меньше — медленнее.
IMAGE_BATCH = 16
# Промпты модели: запрос и описание документа кодируются по-разному, иначе поиск
# «короткий запрос -> длинное описание» плывёт (как было с префиксами nomic).
_PROMPTS = {"query": "SearchQuery", "document": "Document"}


def load(device: str) -> Any:
    """Модель на устройстве `device` (cuda / mps / cpu). Читается с диска один раз — кэш у сервиса."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_ID, device=device)


def _rows(vecs: Any) -> list[list[float]]:
    """Матрица кодировщика (numpy или список) -> списки обычных float: едет по проводу."""
    return [[float(x) for x in row] for row in vecs]


def encode_texts(model: Any, texts: Sequence[str], kind: str) -> list[list[float]]:
    """Векторы текстов. `kind`: "query" — запрос, "document" — описание файла."""
    prompt = _PROMPTS.get(kind)
    if prompt is None:
        raise ValueError("kind должен быть query или document, не %r" % (kind,))
    if not texts:
        return []
    return _rows(model.encode(list(texts), prompt_name=prompt, normalize_embeddings=True))


def encode_images(model: Any, images: Sequence[bytes]) -> list[list[float]]:
    """Векторы картинок (байты JPEG/PNG). Батчами по IMAGE_BATCH, без префикса."""
    from PIL import Image
    out: list[list[float]] = []
    for i in range(0, len(images), IMAGE_BATCH):
        batch = [Image.open(io.BytesIO(b)).convert("RGB") for b in images[i:i + IMAGE_BATCH]]
        out.extend(_rows(model.encode(batch, normalize_embeddings=True)))
    return out
