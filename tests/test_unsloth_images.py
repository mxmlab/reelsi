# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты провайдера картинок Unsloth Studio.

Никаких реальных запросов: urllib.request.urlopen подменяется фейком, который
отвечает по URL и пишет журнал вызовов (метод, путь, тело). Таймер —
UNSLOTH_IDLE_SEC = 0.05, ждём ≤1 с.
"""
import base64
import io
import json
import os
import re
import sys
import threading
import time
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.aicut import images as img_mod  # noqa: E402
from core.aicut.images import (  # noqa: E402
    _gen_image_unsloth,
    gen_image,
    unsloth_cancel_ours,
    unsloth_unload_ours,
)
from core.umsg import ReelsiError  # noqa: E402

# Тестовые константы
_ROOT = "http://127.0.0.1:8888"
_MODEL_PATH = "unsloth/Qwen-Image-2.1-GGUF"
_GGUF = "qwen-image-2.1-Q8_0.gguf"
_MODEL_STR = f"{_MODEL_PATH}/{_GGUF}"
_FAKE_B64 = base64.b64encode(b"\x89PNG_TEST_IMAGE_DATA").decode()

# Эталонные тела запросов
_EXPECTED_LOAD_BODY = {
    "model_path": _MODEL_PATH,
    "gguf_filename": _GGUF,
    "memory_mode": "low_vram",
}


def _make_prof(model: str = _MODEL_STR, provider: str = "unsloth",
               base_url: str = f"{_ROOT}/v1") -> dict[str, Any]:
    return {
        "name": "test_unsloth",
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": "",
        "headers": {},
    }


# ---------------------------------------------------------------------------
# Фейк urlopen
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Имитирует http.client.HTTPResponse с контекстным менеджером."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._stream = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *a: Any) -> None:
        pass


class FakeUrlopen:
    """Фейк urlopen, отвечающий по URL. Ведёт журнал вызовов и поддерживает
    задержку для тестирования конкурентности."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str, Any]] = []  # (method, path, body)
        self.responses: dict[str, Any] = {}  # path -> response data/callable
        self.delay: float = 0.0  # задержка ответа (для теста 8)
        self.concurrent: int = 0  # счётчик одновременных вызовов
        self.max_concurrent: int = 0
        self._lock = threading.Lock()

    def __call__(self, req: Any, timeout: float = 30) -> _FakeResponse:
        url: str = req.full_url if hasattr(req, "full_url") else str(req)
        method: str = req.get_method() if hasattr(req, "get_method") else "GET"
        body: Any = None
        if hasattr(req, "data") and req.data:
            try:
                body = json.loads(req.data.decode("utf-8"))
            except Exception:
                body = req.data

        # Извлечь путь из URL
        path = url.replace(_ROOT, "")

        self.log.append((method, path, body))

        if self.delay > 0:
            with self._lock:
                self.concurrent += 1
                self.max_concurrent = max(self.max_concurrent, self.concurrent)
            time.sleep(self.delay)
            with self._lock:
                self.concurrent -= 1

        # Ответ по пути
        resp_data = self.responses.get(path)
        if callable(resp_data):
            resp_data = resp_data()
        if resp_data is None:
            resp_data = b"{}"
        if isinstance(resp_data, dict):
            resp_data = json.dumps(resp_data).encode("utf-8")
        elif isinstance(resp_data, str):
            resp_data = resp_data.encode("utf-8")
        return _FakeResponse(resp_data)


def _status_json(loaded: bool = False, repo_id: str = "",
                 gguf_filename: str = "", memory_mode: str = "") -> dict[str, Any]:
    return {
        "loaded": loaded,
        "repo_id": repo_id,
        "gguf_filename": gguf_filename,
        "memory_mode": memory_mode,
    }


def _gen_response_b64(b64: str = _FAKE_B64) -> dict[str, Any]:
    return {"data": [{"b64_json": b64}]}


def _gen_response_url(url: str) -> dict[str, Any]:
    return {"data": [{"url": url}]}


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_unsloth(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Перед каждым тестом сбрасываем _UNSLOTH_OURS и таймер."""
    # Сброс глобалей
    img_mod._UNSLOTH_OURS = None
    if img_mod._UNSLOTH_TIMER is not None:
        img_mod._UNSLOTH_TIMER.cancel()
        img_mod._UNSLOTH_TIMER = None
    # Ускоренный таймер
    monkeypatch.setattr(img_mod, "UNSLOTH_IDLE_SEC", 0.05)
    # Подавляем cancelled() — по умолчанию «не отменён»
    monkeypatch.setattr("core.aicut.images.cancelled", lambda: False)
    monkeypatch.setattr("core.aicut.images.cancel_reason", lambda: "test")
    yield
    # Очистка
    img_mod._UNSLOTH_OURS = None
    if img_mod._UNSLOTH_TIMER is not None:
        img_mod._UNSLOTH_TIMER.cancel()
        img_mod._UNSLOTH_TIMER = None


# ---------------------------------------------------------------------------
# Тест 1: Модель не загружена → порядок вызовов
# ---------------------------------------------------------------------------
def test_01_not_loaded_full_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель не загружена → unload_ours, GET status, POST load, цикл ожидания
    (load-progress + status), POST /v1/images/generations; байты из b64_json."""
    monkeypatch.setattr(img_mod, "UNSLOTH_POLL_SEC", 0.01)

    # После POST load первый status ещё не loaded, второй — loaded.
    status_counter: list[int] = [0]

    def status_response() -> dict[str, Any]:
        status_counter[0] += 1
        if status_counter[0] <= 1:
            # Первый вызов — из _unsloth_ensure_loaded до POST load
            return _status_json(loaded=False)
        # Второй — первый poll-опрос: ещё не загружена
        if status_counter[0] == 2:
            return _status_json(loaded=False)
        # Третий и далее — загружена
        return _status_json(loaded=True, repo_id=_MODEL_PATH,
                            gguf_filename=_GGUF, memory_mode="low_vram")

    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = status_response
    fake.responses["/api/inference/images/load"] = {"ok": True}
    fake.responses["/api/inference/images/load-progress"] = {
        "phase": "finalizing", "fraction": 1.0, "error": None,
    }
    fake.responses["/v1/images/generations"] = _gen_response_b64()
    monkeypatch.setattr("urllib.request.urlopen", fake)

    unload_calls: list[str] = []
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: unload_calls.append("unload_ours"))

    prof = _make_prof()
    result = _gen_image_unsloth("test prompt", prof, emit=lambda *a, **k: None)

    # Проверяем порядок
    assert len(unload_calls) == 1, "unload_ours должен быть вызван"
    methods_paths = [(m, p) for m, p, _ in fake.log]
    assert ("GET", "/api/inference/images/status") in methods_paths
    assert ("POST", "/api/inference/images/load") in methods_paths
    assert ("POST", "/v1/images/generations") in methods_paths

    # Проверяем тело POST load
    load_entry = [e for e in fake.log if e[1] == "/api/inference/images/load"]
    assert len(load_entry) == 1
    assert load_entry[0][2] == _EXPECTED_LOAD_BODY

    # Проверяем тело POST generations
    gen_entry = [e for e in fake.log if e[1] == "/v1/images/generations"]
    assert gen_entry[0][2]["response_format"] == "b64_json"

    # Проверяем результат — байты
    expected_bytes = base64.b64decode(_FAKE_B64)
    assert result == expected_bytes


# ---------------------------------------------------------------------------
# Тест 2: Вторая картинка — модель уже загружена → load не зовётся
# ---------------------------------------------------------------------------
def test_02_already_loaded_skips_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Статус loaded=True, наша модель в low_vram → load не вызывается."""
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF, memory_mode="low_vram")
    fake.responses["/v1/images/generations"] = _gen_response_b64()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    # Имитируем, что мы уже загрузили модель
    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")

    prof = _make_prof()
    result = _gen_image_unsloth("test prompt 2", prof, emit=lambda *a, **k: None)

    load_calls = [e for e in fake.log if "/load" in e[1]]
    assert len(load_calls) == 0, "load не должен вызываться"
    assert result == base64.b64decode(_FAKE_B64)


# ---------------------------------------------------------------------------
# Тест 3: В Studio другая модель → ReelsiError с кодом unsloth_foreign_model
# ---------------------------------------------------------------------------
def test_03_foreign_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """В Studio другая модель → ReelsiError(unsloth_foreign_model), ни load, ни unload, ни генерации."""
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id="other/model", gguf_filename="other.gguf",
        memory_mode="low_vram")
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    with pytest.raises(ReelsiError) as exc_info:
        _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    assert exc_info.value.code == "unsloth_foreign_model"
    # Не должно быть ни load, ни unload, ни generations
    non_status = [e for e in fake.log if "/status" not in e[1]]
    assert len(non_status) == 0, f"Лишние вызовы: {non_status}"


# ---------------------------------------------------------------------------
# Тест 4: Та же модель, но memory_mode: "auto" → unsloth_foreign_model
# ---------------------------------------------------------------------------
def test_04_wrong_memory_mode_foreign(monkeypatch: pytest.MonkeyPatch) -> None:
    """Та же модель, но memory_mode auto → unsloth_foreign_model."""
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF,
        memory_mode="auto")
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    with pytest.raises(ReelsiError) as exc_info:
        _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    assert exc_info.value.code == "unsloth_foreign_model"


# ---------------------------------------------------------------------------
# Тест 5: Ответ с относительным url вместо b64_json → байты скачаны
# ---------------------------------------------------------------------------
def test_05_relative_url_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ с url (относительный) вместо b64_json → байты скачиваются с <корень>+url."""
    fake_image_bytes = b"FAKE_PNG_IMAGE_BYTES"
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF, memory_mode="low_vram")
    fake.responses["/v1/images/generations"] = _gen_response_url("/api/images/output/123.png")
    fake.responses["/api/images/output/123.png"] = fake_image_bytes
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")

    prof = _make_prof()
    result = _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    # Проверяем, что скачали по правильному URL
    dl_calls = [e for e in fake.log if "/api/images/output/123.png" in e[1]]
    assert len(dl_calls) == 1
    assert result == fake_image_bytes


# ---------------------------------------------------------------------------
# Тест 6: Таймер простоя → POST unload через ~UNSLOTH_IDLE_SEC
# ---------------------------------------------------------------------------
def test_06_idle_timer_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    """После генерации через ~UNSLOTH_IDLE_SEC вызван POST unload; если _UNSLOTH_OURS пуст — не ходим."""
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF, memory_mode="low_vram")
    fake.responses["/v1/images/generations"] = _gen_response_b64()
    fake.responses["/api/inference/images/unload"] = {"ok": True}
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")

    prof = _make_prof()
    _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    # Ждём таймер (UNSLOTH_IDLE_SEC = 0.05, ждём до 1 с)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        unload_calls = [e for e in fake.log if "/unload" in e[1]]
        if unload_calls:
            break
        time.sleep(0.02)

    unload_calls = [e for e in fake.log if "/unload" in e[1]]
    assert len(unload_calls) >= 1, "POST unload не вызван таймером простоя"

    # Проверяем, что при пустом _UNSLOTH_OURS таймер не ходит в Studio
    fake2 = FakeUrlopen()
    fake2.responses["/api/inference/images/status"] = _status_json(loaded=False)
    fake2.responses["/v1/images/generations"] = _gen_response_b64()
    monkeypatch.setattr("urllib.request.urlopen", fake2)

    img_mod._UNSLOTH_OURS = None
    # unsloth_unload_ours при пустом _UNSLOTH_OURS ничего не делает
    unsloth_unload_ours()
    studio_calls = [e for e in fake2.log
                    if "/api/inference" in e[1]]
    assert len(studio_calls) == 0, "При пустом _UNSLOTH_OURS не должны ходить в Studio"


# ---------------------------------------------------------------------------
# Тест 7: warn_foreign_models при _UNSLOTH_OURS задан → POST unload; при пустом — нет
# ---------------------------------------------------------------------------
def test_07_warn_foreign_models_unloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """warn_foreign_models() при _UNSLOTH_OURS задан → POST unload; при пустом — нет."""
    from core.aicut.llm import warn_foreign_models

    # Случай 1: _UNSLOTH_OURS задан → выгрузка
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF, memory_mode="low_vram")
    fake.responses["/api/inference/images/unload"] = {"ok": True}
    monkeypatch.setattr("urllib.request.urlopen", fake)
    # Подменяем loaded_info, чтобы warn_foreign_models не лезла в LM Studio
    monkeypatch.setattr("core.aicut.llm.loaded_info", lambda url=None: [])

    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")

    warn_foreign_models(emit=lambda *a, **k: None)

    unload_calls = [e for e in fake.log if "/unload" in e[1]]
    assert len(unload_calls) >= 1, "POST unload не вызван warn_foreign_models"

    # Случай 2: _UNSLOTH_OURS пуст → не ходим в Studio
    fake2 = FakeUrlopen()
    monkeypatch.setattr("urllib.request.urlopen", fake2)

    img_mod._UNSLOTH_OURS = None

    warn_foreign_models(emit=lambda *a, **k: None)
    studio_calls = [e for e in fake2.log if "/api/inference" in e[1]]
    assert len(studio_calls) == 0, "При пустом _UNSLOTH_OURS не должны ходить в Studio"


# ---------------------------------------------------------------------------
# Тест 8: Два потока одновременно → запросы /v1/images/generations не перекрываются
# ---------------------------------------------------------------------------
def test_08_concurrent_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Два потока генерируют одновременно → запросы не перекрываются."""
    fake = FakeUrlopen()
    fake.delay = 0.2  # ответ генерации держит 0.2 с
    fake.responses["/api/inference/images/status"] = _status_json(
        loaded=True, repo_id=_MODEL_PATH, gguf_filename=_GGUF, memory_mode="low_vram")
    fake.responses["/v1/images/generations"] = _gen_response_b64()
    fake.responses["/api/inference/images/unload"] = {"ok": True}
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")
    prof = _make_prof()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            _gen_image_unsloth("concurrent test", prof, emit=lambda *a, **k: None)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"Ошибки в потоках: {errors}"
    # Максимум одновременных вызовов = 1 (благодаря _UNSLOTH_LOCK)
    assert fake.max_concurrent <= 1, \
        f"Одновременных запросов: {fake.max_concurrent}, ожидалось ≤1"


# ---------------------------------------------------------------------------
# Тест 9: /api/ai_stop при _UNSLOTH_OURS → POST generate/cancel
# ---------------------------------------------------------------------------
def test_09_cancel_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/ai_stop при _UNSLOTH_OURS задан → POST generate/cancel вызван."""
    fake = FakeUrlopen()
    fake.responses["/api/inference/images/generate/cancel"] = {"ok": True}
    monkeypatch.setattr("urllib.request.urlopen", fake)

    img_mod._UNSLOTH_OURS = (_ROOT, f"{_MODEL_PATH}|{_GGUF}")

    unsloth_cancel_ours()

    cancel_calls = [e for e in fake.log if "/generate/cancel" in e[1]]
    assert len(cancel_calls) == 1, "POST generate/cancel не вызван"

    # Если _UNSLOTH_OURS пуст — cancel не вызывается
    fake2 = FakeUrlopen()
    monkeypatch.setattr("urllib.request.urlopen", fake2)
    img_mod._UNSLOTH_OURS = None

    unsloth_cancel_ours()
    assert len(fake2.log) == 0, "cancel вызван при пустом _UNSLOTH_OURS"


# ---------------------------------------------------------------------------
# Тест 10: gen_image с provider: "unsloth" идёт в _gen_image_unsloth
# ---------------------------------------------------------------------------
def test_10_gen_image_routes_to_unsloth(monkeypatch: pytest.MonkeyPatch) -> None:
    """gen_image с профилем provider='unsloth' идёт в _gen_image_unsloth, а не в OpenRouter."""
    routed: list[str] = []

    def fake_unsloth(prompt: str, prof: dict[str, Any], emit: Any = None) -> bytes:
        routed.append("unsloth")
        return b"fake"

    def fake_openrouter(prompt: str, prof: dict[str, Any], emit: Any = None,
                        retries: int = 1) -> bytes:
        routed.append("openrouter")
        return b"fake"

    monkeypatch.setattr(img_mod, "_gen_image_unsloth", fake_unsloth)
    monkeypatch.setattr(img_mod, "_gen_image_openrouter", fake_openrouter)
    # Подменяем ai_log_append, чтобы не лезть в файловую систему
    monkeypatch.setattr("core.aicut.images.ai_log_append",
                        lambda *a, **k: None)

    prof = _make_prof()
    gen_image("test", prof=prof)

    assert routed == ["unsloth"], f"Маршрутизация: {routed}, ожидалось ['unsloth']"


# ---------------------------------------------------------------------------
# Тест 11: Фронт — в insGenBatch есть ветка для unsloth
# ---------------------------------------------------------------------------
def test_11_frontend_unsloth_branch() -> None:
    """В static/app/10-settings.js в insGenBatch есть ветка для unsloth."""
    js_path = os.path.join(ROOT, "static", "app", "10-settings.js")
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Ищем функцию insGenBatch
    assert "insGenBatch" in content, "insGenBatch не найден в 10-settings.js"

    # Внутри insGenBatch должна быть ветка для provider === 'unsloth'
    # Ищем паттерн: provider==='unsloth' или provider === 'unsloth'
    pattern = re.compile(r"provider\s*===?\s*['\"]unsloth['\"]")
    assert pattern.search(content), \
        "В insGenBatch нет ветки для провайдера unsloth"


# ---------------------------------------------------------------------------
# Тест 12: status отвечает URLError → ReelsiError(unsloth_unreachable), load не вызван
# ---------------------------------------------------------------------------
def test_12_status_urlerror_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """status отвечает URLError → ReelsiError с кодом unsloth_unreachable, load не вызван."""
    import urllib.error
    import urllib.request

    call_log: list[tuple[str, str]] = []

    def fake_urlopen(req: Any, timeout: float = 30) -> Any:
        url: str = req.full_url if hasattr(req, "full_url") else str(req)
        path = url.replace(_ROOT, "")
        call_log.append(("urlopen", path))
        if "/status" in path:
            raise urllib.error.URLError("Connection refused")
        return _FakeResponse(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    with pytest.raises(ReelsiError) as exc_info:
        _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    assert exc_info.value.code == "unsloth_unreachable"

    # load не должен быть вызван
    load_calls = [p for _, p in call_log if "/load" in p]
    assert len(load_calls) == 0, f"load вызван, хотя status бросил URLError: {call_log}"


# ---------------------------------------------------------------------------
# Тест 13: POST load вернул loaded=false → ждём status, генерация только после готовности
# ---------------------------------------------------------------------------
def test_13_load_waits_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """load отвечает loaded=false; status до load — false, в цикле дважды false,
    затем true с нашей моделью → генерация строго после последнего status."""
    monkeypatch.setattr(img_mod, "UNSLOTH_POLL_SEC", 0.01)
    # Таймер простоя выносим за пределы теста: его status/unload попали бы в журнал
    # ПОСЛЕ генерации и сломали проверку порядка (в фикстуре UNSLOTH_IDLE_SEC = 0.05).
    monkeypatch.setattr(img_mod, "UNSLOTH_IDLE_SEC", 30.0)

    # Первый status — до POST load, 2-й и 3-й — опросы цикла, 4-й — модель готова.
    status_counter: list[int] = [0]

    def status_response() -> dict[str, Any]:
        status_counter[0] += 1
        if status_counter[0] <= 3:
            return _status_json(loaded=False)
        return _status_json(loaded=True, repo_id=_MODEL_PATH,
                            gguf_filename=_GGUF, memory_mode="low_vram")

    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = status_response
    fake.responses["/api/inference/images/load"] = {"loaded": False}
    fake.responses["/api/inference/images/load-progress"] = {"error": None}
    fake.responses["/v1/images/generations"] = _gen_response_b64()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    result = _gen_image_unsloth("test prompt 13", prof, emit=lambda *a, **k: None)

    status_idx = [i for i, (_, p, _) in enumerate(fake.log)
                  if p == "/api/inference/images/status"]
    gen_idx = [i for i, (_, p, _) in enumerate(fake.log)
               if p == "/v1/images/generations"]
    # status до load + два «ещё нет» + один «готово»: цикл ожидания реально работал
    assert len(status_idx) == 4, f"опросов status: {len(status_idx)}, ожидалось 4"
    assert len(gen_idx) == 1, f"запросов генерации: {len(gen_idx)}, ожидался 1"
    assert gen_idx[0] > status_idx[-1], \
        f"генерация ушла раньше готовности модели: {fake.log}"

    assert result == base64.b64decode(_FAKE_B64)


# ---------------------------------------------------------------------------
# Тест 14: load-progress вернул error → ReelsiError(unsloth_load_failed), генерации нет
# ---------------------------------------------------------------------------
def test_14_load_progress_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """load-progress вернул error: «CUDA out of memory» → ReelsiError(unsloth_load_failed);
    запроса генерации нет, _UNSLOTH_OURS не пуст (модель наша — выгружать её нам)."""
    monkeypatch.setattr(img_mod, "UNSLOTH_POLL_SEC", 0.01)
    # Таймер простоя не должен успеть обнулить _UNSLOTH_OURS до проверки.
    monkeypatch.setattr(img_mod, "UNSLOTH_IDLE_SEC", 30.0)

    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(loaded=False)
    fake.responses["/api/inference/images/load"] = {"loaded": False}
    fake.responses["/api/inference/images/load-progress"] = {
        "phase": "loading", "fraction": 0.1, "error": "CUDA out of memory",
    }
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    with pytest.raises(ReelsiError) as exc_info:
        _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    assert exc_info.value.code == "unsloth_load_failed"

    gen_calls = [e for e in fake.log if e[1] == "/v1/images/generations"]
    assert len(gen_calls) == 0, f"генерация после сбоя загрузки: {gen_calls}"
    assert img_mod._UNSLOTH_OURS is not None, \
        "_UNSLOTH_OURS потерян — модель осталась в Studio без учёта"


# ---------------------------------------------------------------------------
# Тест 15: status в цикле всегда loaded=false → ReelsiError(unsloth_load_timeout)
# ---------------------------------------------------------------------------
def test_15_load_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """status в цикле всегда loaded=false → по потолку UNSLOTH_LOAD_TIMEOUT_SEC
    ReelsiError(unsloth_load_timeout), генерации нет."""
    monkeypatch.setattr(img_mod, "UNSLOTH_POLL_SEC", 0.01)
    monkeypatch.setattr(img_mod, "UNSLOTH_LOAD_TIMEOUT_SEC", 0.05)

    fake = FakeUrlopen()
    fake.responses["/api/inference/images/status"] = _status_json(loaded=False)
    fake.responses["/api/inference/images/load"] = {"loaded": False}
    fake.responses["/api/inference/images/load-progress"] = {"error": None}
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("core.aicut.llm.unload_ours",
                        lambda emit=None: None)

    prof = _make_prof()
    with pytest.raises(ReelsiError) as exc_info:
        _gen_image_unsloth("test", prof, emit=lambda *a, **k: None)

    assert exc_info.value.code == "unsloth_load_timeout"

    gen_calls = [e for e in fake.log if e[1] == "/v1/images/generations"]
    assert len(gen_calls) == 0, f"генерация без готовой модели: {gen_calls}"
