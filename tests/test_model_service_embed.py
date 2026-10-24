# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Эмбеддер в сервисе моделей: модель грузится одна на машину, `unload` её выгружает.

Сервис поднимается В ПРОЦЕССЕ теста (слушатель в своём потоке, как в
`tests/test_model_service_rembg.py`), устройство — процессор (`REELSI_MODEL_SERVICE_DEVICE`).
Пакет `sentence_transformers` подменяется файлом (`REELSI_MODEL_SERVICE_SUBST_EMB`): он
считает загрузки и вызовы, а векторы — из длины текста. Веса EmbeddingGemma не читаются.
"""
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from core import embedder, model_service
from core.model_service import ServiceUnavailable

# conftest по умолчанию подменяет клиент «сервиса нет»; здесь проверяется САМ клиент с сервисом.
# Настоящие функции берём при импорте модуля — до того, как какая-либо фикстура их подменит.
_REAL_EMBED_TEXTS = model_service.embed_texts
_REAL_EMBED_IMAGES = model_service.embed_images


@pytest.fixture(autouse=True)
def _real_embed_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_service, "embed_texts", _REAL_EMBED_TEXTS)
    monkeypatch.setattr(model_service, "embed_images", _REAL_EMBED_IMAGES)

# Подмена sentence_transformers: `LOADS` — загрузки модели, `CALLS` — вызовы encode.
_FAKE_ST = '''\
"""Подмена sentence_transformers для теста эмбеддера в сервисе."""
import numpy as np
LOADS = []
CALLS = []


class SentenceTransformer:
    def __init__(self, name, device=None):
        LOADS.append((name, device))

    def encode(self, inputs, prompt_name=None, normalize_embeddings=False):
        CALLS.append((len(inputs), prompt_name))
        rows = [[float(len(x)) if isinstance(x, str) else float(x.size[0]), 1.0] for x in inputs]
        return np.array(rows, dtype="float32")
'''

_LIVE: list[Any] = []


@pytest.fixture
def emb_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Профиль сервиса: свой файл адреса, процессор и подменный sentence_transformers.

    `sys.modules["sentence_transformers"]` кладёт подмена сервиса; ключ уходит на выходе
    теста (через monkeypatch), чтобы чужие тесты не увидели заглушку.
    """
    settings = tmp_path / "job.lock.modelsvc.json"
    fake = tmp_path / "fake_st.py"
    fake.write_text(_FAKE_ST, encoding="utf-8")
    monkeypatch.setenv("REELSI_MODEL_SERVICE_DEVICE", "cpu")
    monkeypatch.setenv("REELSI_MODEL_SERVICE_SUBST_EMB", str(fake))
    monkeypatch.setattr(model_service, "_settings_path", lambda: str(settings))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    model_service.unload_models()
    try:
        yield {"tmp": tmp_path, "settings": settings,
               "subst": model_service._exec_module(str(fake))}
    finally:
        for service in list(_LIVE):
            service.stop()
            service.wait_until_stopped(timeout=30)
        _LIVE.clear()
        model_service.unload_models()


def _serve(settings: Path) -> Any:
    """Слушатель сервиса в СВОЁМ потоке и файл адреса к нему — как у боевого пути."""
    port = model_service._pick_port()
    key = secrets.token_bytes(32)
    service = model_service._Service(port, key, idle_s=None, slots=1)
    service.open_listener()
    model_service._write_settings(str(settings), {
        "version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
        "port": port, "pid": os.getpid(),
        "created": model_service._process_create_time(os.getpid()),
        "authkey": key.hex(), "idle": service.idle_s,
        "build": model_service._code_fingerprint(),
        "device": model_service._service_device(),
    })
    service.start_thread()
    _LIVE.append(service)
    return service


def _join(service: Any) -> None:
    service.stop()
    service.wait_until_stopped(timeout=30)


def _png(color: int) -> bytes:
    import io
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (color, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_эмбеддер_в_сервисе_грузится_один_раз(emb_ws: dict[str, Any]) -> None:
    """Запрос и документ подряд: модель загружена ОДИН раз, промпты разные.

    Мутация: убрать кэш `_loaded_embedder` — загрузок станет две, тест покраснеет.
    """
    service = _serve(emb_ws["settings"])
    try:
        q = model_service.embed_texts(["собака на пляже"], "query")
        d = model_service.embed_texts(["boxer hitting a punching bag"], "document")
    finally:
        _join(service)
    sub = emb_ws["subst"]
    assert len(sub.LOADS) == 1, f"модель прочитана {len(sub.LOADS)} раз вместо одного"
    assert sub.LOADS[0][0] == embedder.MODEL_ID
    assert [p for _, p in sub.CALLS] == ["SearchQuery", "Document"]
    assert q == [[float(len("собака на пляже")), 1.0]]
    assert d == [[float(len("boxer hitting a punching bag")), 1.0]]


def test_unload_выгружает_эмбеддер(emb_ws: dict[str, Any]) -> None:
    """После `unload` следующий запрос грузит модель заново: кэш сброшен, процесс жив."""
    service = _serve(emb_ws["settings"])
    try:
        model_service.embed_texts(["кофемашина"], "query")
        assert model_service.unload() is True
        model_service.embed_texts(["кофемашина"], "query")
    finally:
        _join(service)
    assert len(emb_ws["subst"].LOADS) == 2


def test_картинки_идут_через_сервис_векторами(emb_ws: dict[str, Any]) -> None:
    """Байты картинок уходят в сервис, назад — по вектору на каждую картинку."""
    service = _serve(emb_ws["settings"])
    try:
        vecs = model_service.embed_images([_png(1), _png(2), _png(3)])
    finally:
        _join(service)
    assert len(vecs) == 3
    assert all(isinstance(x, float) for v in vecs for x in v)
    assert emb_ws["subst"].CALLS[-1][0] == 3


def test_неизвестный_вид_текста_даёт_ServiceUnavailable(emb_ws: dict[str, Any]) -> None:
    """Сервис отказал (kind не query/document) — клиент получает ServiceUnavailable, а не падение.

    Вызывающий (база вставок) уйдёт на токенный путь: отказ счёта — не «сервиса нет», но
    наружу он выходит той же веткой.
    """
    service = _serve(emb_ws["settings"])
    try:
        with pytest.raises(ServiceUnavailable):
            model_service.embed_texts(["x"], "passage")
    finally:
        _join(service)


def test_vram_эмбеддера_проверяется_по_его_цене(monkeypatch: pytest.MonkeyPatch) -> None:
    """Слот эмбеддера ждёт по `VRAM_MIB` (3072 + запас), а не по цене распознавания (512 + запас).

    На 3500 МиБ свободных распознавание встанет, эмбеддер — нет. Мутация: передать в
    `vram_fits` по умолчанию цену распознавания — тест краснеет.
    """
    monkeypatch.setattr(model_service, "_service_device", lambda: "cuda")
    assert model_service.vram_fits(free_mib=3500) is True
    assert model_service.vram_fits(free_mib=3500, need_mib=embedder.VRAM_MIB) is False
    assert model_service.vram_fits(free_mib=embedder.VRAM_MIB + model_service.VRAM_RESERVE_MIB,
                                   need_mib=embedder.VRAM_MIB) is True
