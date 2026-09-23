# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты целостности скачивания и устойчивого опроса статуса генерации видео.

Проверяем:
1. Защита от HTML/не-видео ответов при скачивании (не оставляем мусор и пробуем следующий URL).
2. Контроль Content-Length и размера (обрыв скачивания не принимается за успех).
3. Валидация сигнатур видеоконтейнера (MP4/MOV vs WebM) и автоматическое переименование в .webm.
4. Опрос статуса устойчив к HTML-ошибкам 200, тайм-аутам и непредвиденным JSON-структурам (списки).
"""
import io
import json
import os
import socket
import time
import urllib.request

import pytest

from core import aicut
from core.umsg import ReelsiError


class _FakeResponse:
    """Имитация urllib HTTPResponse с read, headers и контекст-менеджером."""

    def __init__(self, data=b"", headers=None, code=200):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._data = io.BytesIO(data)
        self.headers = headers or {}
        self.code = code

    def read(self, amt=None):
        if amt is None:
            return self._data.read()
        return self._data.read(amt)

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def _prof():
    return {
        "name": "test_kc_video",
        "provider": "openrouter",
        "base_url": "https://api.example.test",
        "api_key": "test-key-kc",
        "model": "bytedance/seedance-2.0",
    }


def _setup_base_env(monkeypatch):
    """Отключаем сетевой опрос каталога, сон и DNS во время тестов.

    Резолвер подменяем заглушкой: стража адреса (SSRF) интересует IP, а не имя, а
    сети в тестах быть не должно — любое имя резолвится в публичный адрес.
    """
    monkeypatch.setattr(aicut, "ensure_video_catalog", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port=None, *a, **k: [
                            (socket.AF_INET, socket.SOCK_STREAM, 6,
                             "", ("93.184.216.34", 0))])


def _fake_network(monkeypatch, fake_urlopen):
    """Подмена сети на уровне OpenerDirector.open.

    Скачивание ролика идёт СВОИМ opener'ом (перехватчик редиректов, core/app_meta.py),
    а опрос статуса — через urllib.request.urlopen; общая точка входа у них одна,
    поэтому подменяем её, а не модульный urlopen.
    """
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open",
        lambda self, req, data=None, timeout=None: fake_urlopen(req))


def test_download_skips_html_and_saves_valid_mp4(tmp_path, monkeypatch):
    """HTML с кодом 200 на первом URL отбрасывается, второй нормальный MP4 скачивается."""
    _setup_base_env(monkeypatch)
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42valid_payload_for_mp4_container"

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_kc_1",
                "polling_url": "https://api.example.test/videos/vid_kc_1",
            }))
        if "/videos/vid_kc_1" in url and not url.endswith("/content?index=0"):
            return _FakeResponse(json.dumps({
                "status": "completed",
                "usage": {"cost": 0.04},
                "urls": ["https://cdn.example.test/err_page.mp4", "https://cdn.example.test/real.mp4"],
            }))
        if "err_page.mp4" in url:
            return _FakeResponse(
                b"<!DOCTYPE html><html><body>Error 200 Page</body></html>",
                headers={"Content-Type": "text/html"},
            )
        if "real.mp4" in url:
            return _FakeResponse(
                mp4_bytes,
                headers={"Content-Type": "video/mp4", "Content-Length": str(len(mp4_bytes))},
            )
        raise RuntimeError(f"Неожиданный URL: {url}")

    _fake_network(monkeypatch, fake_urlopen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert res["path"].endswith(".mp4")
    assert os.path.exists(res["path"])
    with open(res["path"], "rb") as f:
        assert f.read() == mp4_bytes

    # Проверяем, что временные файлы .part удалены и в папке остался только результат
    files = os.listdir(tmp_path)
    assert files == [os.path.basename(res["path"])]


def test_download_truncated_body_aborts_and_cleans_out_dir(tmp_path, monkeypatch):
    """Оборванное тело (Content-Length больше факта) на всех URL приводит к ReelsiError и очистке."""
    _setup_base_env(monkeypatch)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_trunc_99",
                "polling_url": "https://api.example.test/videos/vid_trunc_99",
            }))
        if "/videos/vid_trunc_99" in url and not url.endswith("/content?index=0"):
            return _FakeResponse(json.dumps({
                "status": "completed",
                "urls": ["https://cdn.example.test/trunc.mp4"],
            }))
        # Любая попытка скачивания отдаёт 10 байт при заголовке 1000 байт
        return _FakeResponse(
            b"0123456789",
            headers={"Content-Type": "video/mp4", "Content-Length": "1000"},
        )

    _fake_network(monkeypatch, fake_urlopen)

    with pytest.raises(ReelsiError) as excinfo:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=_prof())

    err_text = str(excinfo.value)
    assert "оборвано: 10 из 1000 байт" in err_text
    assert "vid_trunc_99" in err_text
    # В папке не должно остаться никаких мусорных или недокачанных файлов
    assert os.listdir(tmp_path) == []


def test_download_invalid_signature_rejected(tmp_path, monkeypatch):
    """Файл без сигнатуры контейнера при типе octet-stream отклоняется."""
    _setup_base_env(monkeypatch)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_bad_sig",
                "polling_url": "https://api.example.test/videos/vid_bad_sig",
            }))
        if "/videos/vid_bad_sig" in url and not url.endswith("/content?index=0"):
            return _FakeResponse(json.dumps({
                "status": "completed",
                "urls": ["https://cdn.example.test/not_video.dat"],
            }))
        garbage = b"this is completely not a video container header"
        return _FakeResponse(
            garbage,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(garbage))},
        )

    _fake_network(monkeypatch, fake_urlopen)

    with pytest.raises(ReelsiError) as excinfo:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=_prof())

    err_text = str(excinfo.value)
    assert "не видеофайл" in err_text
    assert "vid_bad_sig" in err_text
    assert os.listdir(tmp_path) == []


def test_download_webm_signature_renames_extension(tmp_path, monkeypatch):
    """Сигнатура WebM/Matroska приводит к сохранению файла с расширением .webm."""
    _setup_base_env(monkeypatch)
    webm_bytes = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01webm_content"

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_webm_1",
                "polling_url": "https://api.example.test/videos/vid_webm_1",
            }))
        if "/videos/vid_webm_1" in url and not url.endswith("/content?index=0"):
            return _FakeResponse(json.dumps({
                "status": "completed",
                "urls": ["https://cdn.example.test/video.webm"],
            }))
        return _FakeResponse(
            webm_bytes,
            headers={"Content-Type": "video/webm", "Content-Length": str(len(webm_bytes))},
        )

    _fake_network(monkeypatch, fake_urlopen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert res["path"].endswith(".webm")
    assert os.path.exists(res["path"])
    with open(res["path"], "rb") as f:
        assert f.read() == webm_bytes
    assert os.listdir(tmp_path) == [os.path.basename(res["path"])]


def test_poll_three_html_responses_then_completed_succeeds(tmp_path, monkeypatch):
    """Три ответа HTML(200) во время опроса статуса не роняют задачу, 4-й completed доводит до успеха."""
    _setup_base_env(monkeypatch)
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42ok_video_stream"
    poll_count = 0

    def fake_urlopen(req, *args, **kwargs):
        nonlocal poll_count
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_poll_html3",
                "polling_url": "https://api.example.test/videos/vid_poll_html3",
            }))
        if "/videos/vid_poll_html3" in url and not url.endswith("/content?index=0"):
            poll_count += 1
            if poll_count <= 3:
                # Первые 3 опроса возвращают HTML с кодом 200 (например заглушка шлюза)
                return _FakeResponse(
                    b"<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head></html>",
                    headers={"Content-Type": "text/html"},
                )
            return _FakeResponse(json.dumps({
                "status": "completed",
                "urls": ["https://cdn.example.test/final.mp4"],
            }))
        if "final.mp4" in url:
            return _FakeResponse(
                mp4_bytes,
                headers={"Content-Type": "video/mp4", "Content-Length": str(len(mp4_bytes))},
            )
        raise RuntimeError(f"Неожиданный URL: {url}")

    _fake_network(monkeypatch, fake_urlopen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert res["id"] == "vid_poll_html3"
    assert os.path.exists(res["path"])
    assert poll_count == 4


def test_poll_four_html_responses_raises_reelsierror_without_jsondecode_trace(tmp_path, monkeypatch):
    """Четыре HTML-ответа 200 подряд вызывают ReelsiError с id задачи и poll_url без трассировки JSON."""
    _setup_base_env(monkeypatch)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_poll_html4",
                "polling_url": "https://api.example.test/videos/vid_poll_html4",
            }))
        if "/videos/vid_poll_html4" in url:
            return _FakeResponse(
                b"<!DOCTYPE html><html><body>Backend Cloudflare Error</body></html>",
                headers={"Content-Type": "text/html"},
            )
        raise RuntimeError(f"Неожиданный URL: {url}")

    _fake_network(monkeypatch, fake_urlopen)

    with pytest.raises(ReelsiError) as excinfo:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=_prof())

    err_text = str(excinfo.value)
    assert "vid_poll_html4" in err_text
    assert "https://api.example.test/videos/vid_poll_html4" in err_text
    assert "JSONDecodeError" not in err_text
    assert "опрос статуса не удался" in err_text


def test_poll_timeout_error_four_times_raises_reelsierror(tmp_path, monkeypatch):
    """TimeoutError в urlopen четыре раза подряд вызывает ReelsiError с id задачи и ссылкой."""
    _setup_base_env(monkeypatch)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_poll_timeout",
                "polling_url": "https://api.example.test/videos/vid_poll_timeout",
            }))
        if "/videos/vid_poll_timeout" in url:
            raise TimeoutError("timed out reading status socket")
        raise RuntimeError(f"Неожиданный URL: {url}")

    _fake_network(monkeypatch, fake_urlopen)

    with pytest.raises(ReelsiError) as excinfo:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=_prof())

    err_text = str(excinfo.value)
    assert "vid_poll_timeout" in err_text
    assert "https://api.example.test/videos/vid_poll_timeout" in err_text
    assert "опрос статуса не удался" in err_text


def test_poll_json_list_handled_as_poll_error_not_attribute_error(tmp_path, monkeypatch):
    """Ответ-список JSON вместо объекта считается ошибкой опроса без падения с AttributeError."""
    _setup_base_env(monkeypatch)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("/videos"):
            return _FakeResponse(json.dumps({
                "id": "vid_poll_list",
                "polling_url": "https://api.example.test/videos/vid_poll_list",
            }))
        if "/videos/vid_poll_list" in url:
            # Не-dict JSON ответ: список
            return _FakeResponse(
                b'[{"step": 1}, {"step": 2}]',
                headers={"Content-Type": "application/json"},
            )
        raise RuntimeError(f"Неожиданный URL: {url}")

    _fake_network(monkeypatch, fake_urlopen)

    with pytest.raises(ReelsiError) as excinfo:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=_prof())

    err_text = str(excinfo.value)
    assert "vid_poll_list" in err_text
    assert "https://api.example.test/videos/vid_poll_list" in err_text
    assert "опрос статуса не удался" in err_text
