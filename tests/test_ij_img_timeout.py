# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт таймаута генерации картинок и журнала ИИ-вызовов (+ дополнение 1).

1. Таймаут 40 с для Image API (_gen_image_openrouter, IMAGES_API_TIMEOUT_S)
   и 90 с для Chat completions (_gen_image_chat, IMAGE_TIMEOUT_S).
2. При таймауте Image API происходит откат на Chat completions с emit-сообщением.
3. При таймауте обоих путей поднимается ReelsiError(umsg('img_timeout', ...))
   без повторных попыток запроса.
4. gen_image логирует каждый вызов в ai_calls.jsonl (step='image', ok=True/False,
   ms=..., err=...).
"""
import base64
import io
import json
import os
import socket
import sys
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import aicut  # noqa: E402
from core.aicut import config, images, llm  # noqa: E402
from core.umsg import ReelsiError, UMsg  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ai_log(tmp_path, monkeypatch):
    """Изолируем ai_calls.jsonl во временную папку."""
    log_path = str(tmp_path / "ai_calls.jsonl")
    monkeypatch.setattr(llm, "AI_LOG_PATH", log_path)
    monkeypatch.setattr(config, "AI_LOG_PATH", log_path)
    monkeypatch.setattr(aicut, "AI_LOG_PATH", log_path)
    aicut.clear_cancel()
    return log_path


def _read_log_entries(log_path):
    if not os.path.isfile(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def dummy_profile():
    return {
        "name": "img_prof",
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite-image",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-test",
    }


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self, *a):
        return self._data

    def __enter__(self):
        return io.BytesIO(self._data)

    def __exit__(self, *a):
        return False


def test_images_timeout_fallback_to_chat_success(dummy_profile, _isolate_ai_log, monkeypatch):
    """/images бросает TimeoutError, чат отдаёт картинку -> байты вернулись;
    /images вызван 1 раз с timeout=40, чат 1 раз с timeout=90; в журнале одна запись ok=True."""
    calls = []
    expected_png = b"PNG_CHAT_FALLBACK_BYTES"
    b64_png = base64.b64encode(expected_png).decode("ascii")

    chat_resp = json.dumps({
        "choices": [{
            "message": {
                "images": [{"image_url": {"url": f"data:image/png;base64,{b64_png}"}}],
            },
        }],
    }).encode("utf-8")

    emitted = []

    def fake_emit(msg, **vars):
        emitted.append((msg, vars))

    def fake_urlopen(req, timeout=None):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        calls.append((url, timeout))
        if "/images" in url:
            raise TimeoutError("Image API timed out")
        return _FakeResp(chat_resp)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = images.gen_image("prompt", prof=dummy_profile, emit=fake_emit, retries=2)
    assert res == expected_png
    assert len(calls) == 2
    assert "/images" in calls[0][0] and calls[0][1] == 40
    assert "/chat/completions" in calls[1][0] and calls[1][1] == 90

    # Проверка emit при откате на чат
    assert any("! Image API не ответил за {s} с — пробую через чат" in m[0] for m in emitted)

    entries = _read_log_entries(_isolate_ai_log)
    assert len(entries) == 1
    assert entries[0]["step"] == "image"
    assert entries[0]["ok"] is True
    assert entries[0]["err"] is None
    assert isinstance(entries[0]["ms"], int)


def test_both_paths_timeout_raises_img_timeout(dummy_profile, _isolate_ai_log, monkeypatch):
    """Оба пути бросают TimeoutError -> ReelsiError с кодом img_timeout,
    каждый путь вызван ровно 1 раз, запись ok=False в журнале."""
    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        calls.append((url, timeout))
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ReelsiError) as exc_info:
        images.gen_image("prompt", prof=dummy_profile, retries=2)

    err = exc_info.value.umsg
    assert isinstance(err, UMsg), "ошибка должна быть UMsg"
    assert err.code == "img_timeout"
    assert "90" in str(exc_info.value), f"str(исключения) должно содержать число секунд: {exc_info.value}"

    assert len(calls) == 2
    assert "/images" in calls[0][0] and calls[0][1] == 40
    assert "/chat/completions" in calls[1][0] and calls[1][1] == 90

    entries = _read_log_entries(_isolate_ai_log)
    assert len(entries) == 1
    assert entries[0]["step"] == "image"
    assert entries[0]["ok"] is False
    assert "90" in str(entries[0]["err"])


def test_both_paths_socket_timeout_and_urlerror(dummy_profile, _isolate_ai_log, monkeypatch):
    """socket.timeout и URLError(timeout) на обоих путях также дают ReelsiError(img_timeout)."""
    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        calls.append((url, timeout))
        raise urllib.error.URLError(socket.timeout("The read operation timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ReelsiError) as exc_info:
        images.gen_image("prompt", prof=dummy_profile, retries=2)

    err = exc_info.value.umsg
    assert isinstance(err, UMsg)
    assert err.code == "img_timeout"
    assert "90" in str(exc_info.value)
    assert len(calls) == 2


def test_img_success_timeout_40_and_logs(dummy_profile, _isolate_ai_log, monkeypatch):
    """подмена проверяет, что пришёл timeout=40 на /images, и отдаёт base64 PNG -> байты вернулись,
    в журнале step='image', ok=True, ms — число."""
    seen_timeouts = []
    expected_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest"
    b64_png = base64.b64encode(expected_png).decode("ascii")

    resp_payload = json.dumps({
        "data": [{"b64_json": b64_png}],
        "usage": {"cost": 0.04},
    }).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        seen_timeouts.append(timeout)
        return _FakeResp(resp_payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = images.gen_image("a cute cat", prof=dummy_profile, retries=1)

    assert res == expected_png
    assert seen_timeouts == [40], f"Ожидался timeout=40, получено: {seen_timeouts}"

    entries = _read_log_entries(_isolate_ai_log)
    assert len(entries) == 1
    assert entries[0]["step"] == "image"
    assert entries[0]["ok"] is True
    assert isinstance(entries[0]["ms"], int)
    assert entries[0]["err"] is None


def test_img_chat_path_receives_timeout_90(dummy_profile, monkeypatch):
    """путь чата (_gen_image_chat) получает timeout=90."""
    seen_timeouts = []
    expected_png = b"PNG_CHAT_BYTES"
    b64_png = base64.b64encode(expected_png).decode("ascii")

    chat_resp = json.dumps({
        "choices": [{
            "message": {
                "images": [{"image_url": {"url": f"data:image/png;base64,{b64_png}"}}],
            },
        }],
    }).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        seen_timeouts.append(timeout)
        return _FakeResp(chat_resp)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = images._gen_image_chat("chat prompt", dummy_profile)
    assert res == expected_png
    assert seen_timeouts == [90], f"Ожидался timeout=90, получено: {seen_timeouts}"


def test_gen_image_fallback_to_chat_receives_timeout_90_and_logs(dummy_profile, _isolate_ai_log, monkeypatch):
    """gen_image при 404 откатывается на chat-модель, /images с 40, chat с 90, пишется в лог."""
    seen_timeouts = []
    expected_png = b"PNG_FALLBACK_BYTES"
    b64_png = base64.b64encode(expected_png).decode("ascii")

    chat_resp = json.dumps({
        "choices": [{
            "message": {
                "images": [{"image_url": {"url": f"data:image/png;base64,{b64_png}"}}],
            },
        }],
    }).encode("utf-8")

    call_idx = 0

    def fake_urlopen(req, timeout=None):
        nonlocal call_idx
        seen_timeouts.append(timeout)
        call_idx += 1
        if call_idx == 1:
            fp = io.BytesIO(b'{"error": "not found"}')
            raise urllib.error.HTTPError(req.get_full_url(), 404, "Not Found", {}, fp)
        return _FakeResp(chat_resp)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = images.gen_image("prompt", prof=dummy_profile)
    assert res == expected_png
    assert seen_timeouts == [40, 90]

    entries = _read_log_entries(_isolate_ai_log)
    assert len(entries) == 1
    assert entries[0]["step"] == "image"
    assert entries[0]["ok"] is True
    assert isinstance(entries[0]["ms"], int)


def test_img_cancellation_logs_ok_false(dummy_profile, _isolate_ai_log, monkeypatch):
    """Отмена (cancelled()) поднимает ReelsiError и пишет ok=False в журнал."""
    monkeypatch.setattr(images, "cancelled", lambda: True)

    with pytest.raises(ReelsiError):
        images.gen_image("prompt", prof=dummy_profile)

    entries = _read_log_entries(_isolate_ai_log)
    assert len(entries) == 1
    assert entries[0]["step"] == "image"
    assert entries[0]["ok"] is False
