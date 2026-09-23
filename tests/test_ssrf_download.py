# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NR: готовый ролик скачивается только по безопасному адресу (SSRF).

Ссылки на ролик приходят в ОТВЕТЕ провайдера, а не от пользователя: враждебный или
взломанный провайдер подсовывает `file:///etc/passwd` (читается локальный файл) или
`http://169.254.169.254/...` (запрос уходит в метаданные облака). Здесь стерегутся
ровно такие адреса — loopback, метаданные, `file://`, имя, резолвящееся в приватный
IP, и редирект внутрь, — а ещё то, что проверка НЕ задевает запросы к самому
провайдеру (`base_url` выбирает пользователь, `http://127.0.0.1:1234` там штатен) и
не мешает обычной внешней ссылке.

Второй сторож — тот же ключ при РЕДИРЕКТЕ: `HTTPRedirectHandler` переносит в новый
запрос все заголовки, кроме `Content-*`, поэтому `Authorization` уезжал на чужой хост,
куда увёл редирект. Здесь проверяется, что на другом хосте его нет (а `Cookie` и
`Proxy-Authorization` ведут себя так же), а на своём — другой путь или другой порт —
он на месте: подписанные ссылки провайдера так и работают.

Сети в тестах нет: транспорт (`AbstractHTTPHandler.do_open`) и резолвер
(`socket.getaddrinfo`) подменены заглушками.
"""
import email.message
import io
import json
import os
import socket
import time
import urllib.request

import pytest

from core import aicut
from core.umsg import ReelsiError

BASE = "https://api.example.test"
PUBLIC_IP = "93.184.216.34"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"reelsi-download-payload"


def _prof(base=BASE):
    return {"name": "test_ssrf", "provider": "openrouter", "base_url": base,
            "api_key": "test-key-ssrf", "model": "bytedance/seedance-2.0"}


class _Resp:
    """Ответ транспорта — то, что urllib ждёт от http.client.HTTPResponse."""

    def __init__(self, body=b"", status=200, headers=None):
        self._data = io.BytesIO(body)
        self.code = self.status = status
        self.reason = self.msg = "OK"
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, amt=None):
        # именно поток: shutil.copyfileobj читает порциями до пустого ответа
        return self._data.read() if amt is None else self._data.read(amt)

    def info(self):
        return self.headers

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_resp(payload):
    return _Resp(json.dumps(payload).encode("utf-8"),
                 headers={"Content-Type": "application/json"})


def _video_resp(body=MP4, ctype="video/mp4"):
    return _Resp(body, headers={"Content-Type": ctype, "Content-Length": str(len(body))})


def _redirect_resp(location):
    return _Resp(status=302, headers={"Location": location})


def _provider_routes(urls, vid="", base=BASE):
    """Маршруты провайдера: постановка задачи и её статус со ссылками на ролик.

    `vid` пустой — провайдер не назвал id, и тогда фолбэк-адрес `base_url + id` в
    перебор не попадает: проверяются ровно те адреса, что пришли в ответе.
    """
    job = {"polling_url": f"{base}/videos/job1"}
    if vid:
        job["id"] = vid
    return {
        f"{base}/videos": _json_resp(job),
        f"{base}/videos/job1": _json_resp({"status": "completed",
                                           "usage": {"cost": 0.04},
                                           "urls": list(urls)}),
    }


def _req_headers(req):
    """Заголовки запроса одним словарём в нижнем регистре.

    urllib держит их в двух местах: обычные (`add_header`) и «нередиректные»
    (`add_unredirected_header` — туда кладёт своё сам opener). Ключ провайдера может
    уехать любым из них, поэтому смотрим оба.
    """
    hdrs = {}
    for src in (req.headers, req.unredirected_hdrs):
        for k, v in src.items():
            hdrs[k.lower()] = v
    return hdrs


def _redirect_from_content_ep(target, vid="vid_rd"):
    """Маршруты прогона, где ключ реально шлётся: перебор начинается с
    content-эндпоинта С авторизацией, а тот отвечает 302 на `target`.
    -> (маршруты, адрес content-эндпоинта)."""
    content_ep = f"{BASE}/videos/{vid}/content?index=0"
    routes = _provider_routes([], vid=vid)
    routes[content_ep] = _redirect_resp(target)
    routes[target] = _video_resp()
    return routes, content_ep


def _patch_transport(monkeypatch, routes, seen=None):
    """Подменить транспорт urllib заготовками ответов; вернуть список адресов.

    Подменяется `AbstractHTTPHandler.do_open` — ниже уровня редиректов, поэтому
    перехватчик из кода отрабатывает по-настоящему. Адрес, которого нет в routes,
    роняет тест: значит код пошёл туда, куда ходить не должен.
    `seen` — необязательный список: в него кладутся пары (адрес, заголовки запроса),
    по ним и видно, уехал ключ провайдера на чужой хост или остался дома.
    """
    calls = []

    def _do_open(handler, http_class, req, **kwargs):
        calls.append(req.full_url)
        if seen is not None:
            seen.append((req.full_url, _req_headers(req)))
        resp = routes.get(req.full_url)
        if resp is None:
            raise AssertionError(f"запрос по неожиданному адресу: {req.full_url}")
        return resp

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", _do_open)
    return calls


def _resolver(by_host=None, default=PUBLIC_IP):
    """Заглушка socket.getaddrinfo: {имя: адрес|список адресов}, прочим — default."""
    def fake(host, port=None, *args, **kwargs):
        got = (by_host or {}).get(host, default)
        if isinstance(got, str):
            got = [got]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in got]

    return fake


def _prepare(monkeypatch, routes, resolver, seen=None):
    """Обвязка прогона: без сети, без пауз опроса и без сетевого каталога моделей."""
    calls = _patch_transport(monkeypatch, routes, seen=seen)
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    # gen_video зовёт ensure_video_catalog из своего модуля — подменяем там же
    monkeypatch.setattr(aicut.video, "ensure_video_catalog", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def _refused(tmp_path, monkeypatch, routes, resolver, prof=None):
    """Прогон, который обязан кончиться отказом скачать: -> (текст, адреса запросов)."""
    calls = _prepare(monkeypatch, routes, resolver)
    with pytest.raises(ReelsiError) as err:
        aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                        out_dir=str(tmp_path), prof=prof or _prof())
    return str(err.value), calls


def test_loopback_address_is_refused_without_download(tmp_path, monkeypatch):
    """`http://127.0.0.1:8080/v.mp4` — запрос внутрь машины, а не за роликом."""
    url = "http://127.0.0.1:8080/v.mp4"
    text, calls = _refused(tmp_path, monkeypatch, _provider_routes([url]),
                           _resolver({"127.0.0.1": "127.0.0.1"}))
    assert "небезопасный адрес" in text and "127.0.0.1" in text
    assert url not in calls                    # ни одного запроса по этому адресу
    assert os.listdir(tmp_path) == []


def test_cloud_metadata_address_is_refused(tmp_path, monkeypatch):
    """169.254.169.254 — метаданные облака (именно так воруют ключи из ВМ)."""
    url = "http://169.254.169.254/latest/meta-data/"
    text, calls = _refused(tmp_path, monkeypatch, _provider_routes([url]),
                           _resolver({"169.254.169.254": "169.254.169.254"}))
    assert "небезопасный адрес" in text and "169.254.169.254" in text
    assert url not in calls
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "file:///etc/passwd"])
def test_file_scheme_is_refused(tmp_path, monkeypatch, url):
    """`file://` — это чтение локального файла, а не скачивание ролика."""
    text, calls = _refused(tmp_path, monkeypatch, _provider_routes([url]), _resolver())
    assert "небезопасный адрес" in text and "file" in text
    assert url not in calls
    assert os.listdir(tmp_path) == []


def test_external_name_resolving_to_private_ip_is_refused(tmp_path, monkeypatch):
    """Имя выглядит внешним, а резолвится в 10.0.0.5: проверять надо адрес, а не строку."""
    cdn = "https://cdn.example.test/v.mp4"
    text, calls = _refused(tmp_path, monkeypatch, _provider_routes([cdn]),
                           _resolver({"cdn.example.test": "10.0.0.5"}))
    assert "небезопасный адрес" in text and "10.0.0.5" in text
    assert cdn not in calls
    assert os.listdir(tmp_path) == []


def test_redirect_to_loopback_is_refused(tmp_path, monkeypatch):
    """Внешняя ссылка отвечает 302 на 127.0.0.1 — редирект проверяется так же."""
    cdn = "https://cdn.example.test/v.mp4"
    target = "http://127.0.0.1:8080/v.mp4"
    routes = _provider_routes([cdn])
    routes[cdn] = _redirect_resp(target)
    # ответ по адресу редиректа заготовлен: не сработает страж — ролик скачается
    routes[target] = _video_resp()
    text, calls = _refused(tmp_path, monkeypatch, routes,
                           _resolver({"127.0.0.1": "127.0.0.1"}))
    assert "небезопасный адрес" in text and "127.0.0.1" in text
    assert calls == [f"{BASE}/videos", f"{BASE}/videos/job1", cdn]
    assert os.listdir(tmp_path) == []


def test_public_address_downloads_as_before(tmp_path, monkeypatch):
    """Обычная внешняя ссылка качается как раньше: ролик на месте, мусора нет."""
    cdn = "https://cdn.example.test/real.mp4"
    routes = _provider_routes([cdn], vid="vid_ok")
    routes[cdn] = _video_resp()
    calls = _prepare(monkeypatch, routes, _resolver({"cdn.example.test": PUBLIC_IP}))

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert res["path"].endswith(".mp4") and os.path.exists(res["path"])
    with open(res["path"], "rb") as f:
        assert f.read() == MP4
    assert calls == [f"{BASE}/videos", f"{BASE}/videos/job1", cdn]
    assert os.listdir(tmp_path) == [os.path.basename(res["path"])]


def test_provider_on_loopback_is_still_polled(tmp_path, monkeypatch):
    """Проверка адреса — ТОЛЬКО для скачивания. `base_url` выбирает пользователь, и
    `http://127.0.0.1:1234` (LM Studio) там штатен: опрос статуса к нему идёт.
    В заглушке резолвера провайдер тоже «внутренний» — попади он под проверку,
    опроса бы не было вовсе."""
    base = "http://127.0.0.1:1234/v1"
    cdn = "https://cdn.example.test/v.mp4"
    routes = _provider_routes([cdn], base=base)
    routes[cdn] = _video_resp()
    calls = _prepare(monkeypatch, routes, _resolver({"127.0.0.1": "127.0.0.1"}))

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof(base))

    assert calls[0] == f"{base}/videos"
    assert f"{base}/videos/job1" in calls       # тот самый вызов за статусом задачи
    assert os.path.exists(res["path"])


def test_redirect_to_another_host_drops_authorization(tmp_path, monkeypatch):
    """302 на ДРУГОЙ публичный хост — ключ провайдера туда не уезжает, ролик качается.

    Адрес, куда `Authorization` шлётся осознанно, — content-эндпоинт на хосте
    провайдера. Провайдер уводит его на внешний CDN: целевой хост публичный и по IP
    «безопасный», но ЧУЖОЙ, а `HTTPRedirectHandler` переносит в новый запрос все
    заголовки, кроме `Content-*`. Ключ тут снимается, а не запрос: скачивание обязано
    доехать — оплаченный ролик не теряем.
    """
    other = "https://cdn.example.test/real.mp4"
    routes, content_ep = _redirect_from_content_ep(other)
    seen = []
    calls = _prepare(monkeypatch, routes, _resolver(), seen=seen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert calls == [f"{BASE}/videos", f"{BASE}/videos/job1", content_ep, other]
    got = dict(seen)
    assert got[content_ep]["authorization"] == "Bearer test-key-ssrf"   # свой хост — ушёл
    assert "authorization" not in got[other]                            # чужой — нет
    with open(res["path"], "rb") as f:
        assert f.read() == MP4
    assert os.listdir(tmp_path) == [os.path.basename(res["path"])]


@pytest.mark.parametrize("target", [
    f"{BASE}/files/real.mp4",                   # тот же хост, другой путь
    "https://api.example.test:8443/real.mp4",   # тот же хост, другой порт
])
def test_redirect_on_same_host_keeps_authorization(tmp_path, monkeypatch, target):
    """Редирект внутри своего хоста ключ НЕ теряет: подписанные ссылки провайдера так
    и работают. Порт — не другой хозяин: снимать заголовок из-за одного порта нельзя."""
    routes, content_ep = _redirect_from_content_ep(target, vid="vid_same")
    seen = []
    calls = _prepare(monkeypatch, routes, _resolver(), seen=seen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=_prof())

    assert calls == [f"{BASE}/videos", f"{BASE}/videos/job1", content_ep, target]
    got = dict(seen)
    assert got[content_ep]["authorization"] == "Bearer test-key-ssrf"
    assert got[target]["authorization"] == "Bearer test-key-ssrf"
    with open(res["path"], "rb") as f:
        assert f.read() == MP4


def test_redirect_to_another_host_drops_cookie_and_proxy_authorization(tmp_path, monkeypatch):
    """`Cookie` и `Proxy-Authorization` — такие же удостоверения, как `Authorization`:
    на чужом хосте им делать нечего."""
    other = "https://cdn.example.test/real.mp4"
    routes, content_ep = _redirect_from_content_ep(other, vid="vid_cookie")
    prof = dict(_prof(), headers={"Cookie": "sid=secret",
                                  "Proxy-Authorization": "Basic c2VjcmV0"})
    seen = []
    calls = _prepare(monkeypatch, routes, _resolver(), seen=seen)

    res = aicut.gen_video("test prompt", opts={"model": "bytedance/seedance-2.0"},
                          out_dir=str(tmp_path), prof=prof)

    assert calls == [f"{BASE}/videos", f"{BASE}/videos/job1", content_ep, other]
    got = dict(seen)
    assert got[content_ep]["cookie"] == "sid=secret"                # свой хост — ушли
    assert got[content_ep]["proxy-authorization"] == "Basic c2VjcmV0"
    assert "cookie" not in got[other]                               # чужой — нет
    assert "proxy-authorization" not in got[other]
    with open(res["path"], "rb") as f:
        assert f.read() == MP4
