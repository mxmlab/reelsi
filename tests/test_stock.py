# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стоки как источник вставок: Pexels, Unsplash, Pixabay, Openverse, Coverr (в этом порядке).

Проверяются `core/stock.py` (порядок провайдеров, разбор кандидатов, выбор файла,
кеш ответов, скачивание в базу вставок), роуты `api/inserts.py` (`/api/stock_search`,
`/api/stock_pick`), действие `set_stock_keys` и поля ключей в ⚙.

Сети нет вовсе: `urllib.request.urlopen` подменён фейком ПО URL. Без этого тест либо
жёг бы чужой лимит запросов (200/ч у Pexels), либо падал бы от отсутствия интернета.
Проверка адреса (`unsafe_url_reason`) подменена на «безопасно»: она резолвит имя через
DNS, а DNS в тестах — тоже сеть.

Изоляция жёсткая: кеш стока (`stock.CACHE_PATH`) и индекс базы (`insertlib.INDEX_PATH`)
указывают в tmp_path, эмбеддер подменён на «нет», `ai_config.json` — свой, со
стендовыми ключами (боевой файл не читается и не пишется: путь подменяет conftest).

Провайдеры без ключа и за флагом выключены в autouse-фикстуре `iso` (`REELSI_STOCK_OFF`):
Openverse ключа не требует и иначе добавлял бы свою выдачу в каждый сценарий, написанный
про Pexels/Pixabay. Тесты Openverse и Coverr включают их обратно точечно.

Запуск: py -3.10 -m pytest tests/test_stock.py -q
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import insertlib, stock  # noqa: E402
from core.aicut import config as aicut_config  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

H = {"Host": "127.0.0.1:5001"}
PEXELS_KEY = "PEXELS-KEY-1111"
PIXABAY_KEY = "PIXABAY-KEY-2222"
UNSPLASH_KEY = "UNSPLASH-KEY-3333"

EMPTY_CACHE = {"mtime": 0, "data": None, "mat": None, "mat_items": None,
               "df": None, "cand_words": None, "N": 0}


# --------------------------------------------------------------------------- #
# Фейковая сеть и стенд
# --------------------------------------------------------------------------- #
class _Resp:
    """Ответ фейковой сети. Тело отдаём порциями: `_download_file` читает файл
    блоками, а `json.load` — целиком (`read()` без аргумента)."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int | None = None) -> bytes:
        chunk = self._data[self._pos:] if n is None else self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeNet:
    """Подменённый urlopen: URL -> ответ стенда. Что не описано — ошибка, а не выход
    в настоящую сеть: тест обязан падать, а не ходить к Pexels по-настоящему."""

    def __init__(self, routes: dict | None = None, files: dict | None = None) -> None:
        self.routes = dict(routes or {})      # подстрока URL -> тело ответа | исключение
        self.files = dict(files or {})        # точный URL -> байты файла (скачивание)
        self.calls: list[str] = []
        self.reqs: list[urllib.request.Request] = []

    def __call__(self, req: urllib.request.Request, timeout: int | None = None) -> _Resp:
        url = getattr(req, "full_url", str(req))
        self.calls.append(url)
        self.reqs.append(req)
        if url in self.files:
            return _Resp(self.files[url])
        for frag, payload in self.routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return _Resp(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        raise urllib.error.URLError("сеть в тестах запрещена: " + url)

    def urls(self, frag: str = "") -> list[str]:
        return [u for u in self.calls if frag in u]

    def reqs_for(self, frag: str) -> list[urllib.request.Request]:
        return [r for r in self.reqs if frag in r.full_url]


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    """Кеш, индекс базы и рабочая папка — в tmp_path: в рабочую копию тест не пишет."""
    monkeypatch.setattr(stock, "CACHE_PATH", str(tmp_path / "stock_cache.json"))
    monkeypatch.setenv("REELSI_STOCK_CACHE", str(tmp_path / "stock_cache.json"))
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "insertlib.json"))
    monkeypatch.setattr(insertlib, "_CACHE", dict(EMPTY_CACHE))
    # _seed_index снял бы копию с боевого insertlib.json, а _emb_model пошёл бы в LM Studio
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    # Провайдеры без ключа и за флагом — выключены: их включает тест, который их проверяет.
    monkeypatch.setenv("REELSI_STOCK_OFF", "openverse,coverr")
    monkeypatch.delenv("REELSI_STOCK_COVERR", raising=False)
    # unsafe_url_reason резолвит имя (DNS — сеть): в тестах адрес считаем безопасным,
    # саму проверку стережёт test_security_fixes.
    monkeypatch.setattr(stock, "unsafe_url_reason", lambda url: "")
    return tmp_path


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _write_cfg(pexels: str | None = PEXELS_KEY, pixabay: str | None = PIXABAY_KEY,
               unsplash: str | None = None, coverr: str | None = None,
               rembg: bool = False) -> None:
    """Свой ai_config.json: ключи стендовые, снятие фона выключено (rembg тянул бы
    модель из сети). None у провайдера — поля в конфиге нет, то есть ключа нет."""
    fields = (("pexels_key", pexels), ("pixabay_key", pixabay),
              ("unsplash_key", unsplash), ("coverr_key", coverr))
    cfg = {"active": "Стенд",
           "profiles": {"Стенд": {"provider": "lmstudio",
                                  "base_url": "http://localhost:1234/v1",
                                  "api_key": "", "model": "стенд-модель"}},
           "image_rembg": rembg,
           "stock": {k: v for k, v in fields if v}}
    with open(aicut_config.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def _write_index(path: str, items: list | None = None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"dirs": [], "emb_model": "", "emb_tag": insertlib.EMB_TAG,
                   "items": list(items or [])}, f, ensure_ascii=False)


def _read_index(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _post(client, url: str, payload: dict) -> dict:
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _params(url: str) -> dict:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


# --------------------------------------------------------------------------- #
# Ответы стенда
# --------------------------------------------------------------------------- #
def _pexels_photos(n: int = 3) -> dict:
    return {"photos": [
        {"id": 100 + i, "width": 4000, "height": 6000,
         "url": f"https://www.pexels.com/photo/{100 + i}/",
         "photographer": f"Автор {i}", "photographer_url": f"https://www.pexels.com/@a{i}",
         "alt": f"cat number {i}",
         "src": {"original": f"https://images.pexels.com/photos/{100 + i}/orig.jpg",
                 "large2x": f"https://images.pexels.com/photos/{100 + i}/large2x.jpg",
                 "large": f"https://images.pexels.com/photos/{100 + i}/large.jpg",
                 "medium": f"https://images.pexels.com/photos/{100 + i}/medium.jpg",
                 "small": f"https://images.pexels.com/photos/{100 + i}/small.jpg"}}
        for i in range(n)]}


def _pexels_video_files() -> list:
    """Варианты файла ролика: 4K (больше 1920 — мимо), ландшафт, вертикаль 1080×1920
    (цель), webm (AE его не читает) и мелочь."""
    return [
        {"id": 1, "quality": "uhd", "file_type": "video/mp4", "width": 3840, "height": 2160,
         "link": "https://videos.pexels.com/4k.mp4"},
        {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1920, "height": 1080,
         "link": "https://videos.pexels.com/landscape.mp4"},
        {"id": 3, "quality": "hd", "file_type": "video/mp4", "width": 1080, "height": 1920,
         "link": "https://videos.pexels.com/portrait.mp4"},
        {"id": 4, "quality": "hd", "file_type": "video/webm", "width": 1080, "height": 1920,
         "link": "https://videos.pexels.com/portrait.webm"},
        {"id": 5, "quality": "sd", "file_type": "video/mp4", "width": 540, "height": 960,
         "link": "https://videos.pexels.com/small.mp4"},
    ]


def _pexels_videos(n: int = 1) -> dict:
    return {"videos": [
        {"id": 500 + i, "width": 3840, "height": 2160, "duration": 12,
         "url": f"https://www.pexels.com/video/{500 + i}/",
         "image": f"https://images.pexels.com/videos/{500 + i}/thumb.jpg",
         "user": {"name": f"Оператор {i}", "url": f"https://www.pexels.com/@op{i}"},
         "video_files": _pexels_video_files()}
        for i in range(n)]}


def _pixabay_photos() -> dict:
    return {"total": 2, "hits": [
        {"id": 7, "pageURL": "https://pixabay.com/photos/cat-7/", "tags": "cat, animal, pet",
         "previewURL": "https://cdn.pixabay.com/7_preview.jpg",
         "webformatURL": "https://cdn.pixabay.com/7_web.jpg",
         "largeImageURL": "https://cdn.pixabay.com/7_large.jpg",
         "imageWidth": 4000, "imageHeight": 6000, "user": "Аня", "user_id": 42},
        {"id": 8, "pageURL": "https://pixabay.com/photos/cat-8/", "tags": "kitten",
         "previewURL": "https://cdn.pixabay.com/8_preview.jpg",
         "webformatURL": "https://cdn.pixabay.com/8_web.jpg",
         "largeImageURL": "https://cdn.pixabay.com/8_large.jpg",
         "imageWidth": 3000, "imageHeight": 4500, "user": "Пётр", "user_id": 43},
    ]}


def _pixabay_videos() -> dict:
    """У первого ролика есть large, у второго — только medium (берём его)."""
    return {"total": 2, "hits": [
        {"id": 9, "pageURL": "https://pixabay.com/videos/cat-9/", "tags": "cat, kitten",
         "duration": 8, "user": "Аня", "user_id": 42,
         "videos": {"large": {"url": "https://cdn.pixabay.com/9_large.mp4", "width": 1920,
                              "height": 1080, "thumbnail": "https://cdn.pixabay.com/9_l.jpg"},
                    "medium": {"url": "https://cdn.pixabay.com/9_medium.mp4", "width": 1280,
                               "height": 720, "thumbnail": "https://cdn.pixabay.com/9_m.jpg"}}},
        {"id": 10, "pageURL": "https://pixabay.com/videos/cat-10/", "tags": "pet",
         "duration": 5, "user": "Пётр", "user_id": 43,
         "videos": {"medium": {"url": "https://cdn.pixabay.com/10_medium.mp4", "width": 1280,
                               "height": 720, "thumbnail": "https://cdn.pixabay.com/10_m.jpg"}}},
    ]}


def _unsplash_photos(n: int = 2) -> dict:
    """Ответ поиска Unsplash: `urls.full` — то, что качаем, `links.download_location` —
    учётная ручка (не картинка), по ней приложение обязано отметить скачивание."""
    return {"total": n, "results": [
        {"id": f"u{100 + i}", "width": 4000, "height": 6000,
         "alt_description": f"cat number {i}",
         "description": f"Описание {i}",
         "tags": [{"title": "cat"}, {"title": "window"}],
         "urls": {"full": f"https://images.unsplash.com/u{100 + i}-full.jpg",
                  "regular": f"https://images.unsplash.com/u{100 + i}-regular.jpg",
                  "small": f"https://images.unsplash.com/u{100 + i}-small.jpg"},
         "links": {"html": f"https://unsplash.com/photos/u{100 + i}",
                   "download_location":
                       f"https://api.unsplash.com/photos/u{100 + i}/download?ixid=track{i}"},
         "user": {"name": f"Фотограф {i}", "username": f"user{i}",
                  "links": {"html": f"https://unsplash.com/@user{i}"}}}
        for i in range(n)]}


def _openverse_images() -> dict:
    """Ответ Openverse: годные лицензии (CC0/BY/BY-SA) вперемешку с NC и ND — последние
    брать нельзя (некоммерческая и «без производных»), и фильтр обязан их выбросить."""
    return {"result_count": 5, "results": [
        {"id": "ov-1", "title": "cat window", "creator": "Автор ОВ", "creator_url":
            "https://openverse.org/u/1", "url": "https://cdn.openverse.org/1.jpg",
         "thumbnail": "https://api.openverse.org/v1/images/ov-1/thumb/",
         "foreign_landing_url": "https://example.org/1", "license": "cc0",
         "license_version": "1.0", "source": "flickr", "width": 4000, "height": 6000},
        {"id": "ov-2", "title": "cat", "creator": "Автор НЦ",
         "url": "https://cdn.openverse.org/2.jpg", "license": "by-nc",
         "foreign_landing_url": "https://example.org/2"},
        {"id": "ov-3", "title": "kitten", "creator": "Автор НД",
         "url": "https://cdn.openverse.org/3.jpg", "license": "by-nd",
         "foreign_landing_url": "https://example.org/3"},
        {"id": "ov-4", "title": "pet cat", "creator": "Автор СА",
         "url": "https://cdn.openverse.org/4.jpg", "license": "by-sa",
         "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
         "foreign_landing_url": "https://example.org/4"},
        {"id": "ov-5", "title": "никакой ссылки", "creator": "Автор ПДМ",
         "url": "", "license": "pdm"},
    ]}


def _coverr_videos() -> dict:
    """Ответ Coverr по записи их документации (GET /videos?urls=true): список в `hits`,
    ссылки на файлы в `urls`. cv-2 — с плейсхолдером `{token}`, которого мы не качаем."""
    return {"hits": [
        {"id": "cv-1", "title": "cat walking", "duration": 7,
         "max_width": 1080, "max_height": 1920, "is_vertical": True,
         "urls": {"mp4": "https://cdn.coverr.co/cv-1.mp4",
                  "mp4_preview": "https://cdn.coverr.co/cv-1-preview.mp4",
                  "poster": "https://cdn.coverr.co/cv-1-poster.jpg"},
         "creator": {"name": "Оператор К", "portfolio_url": "https://coverr.co/@k"},
         "link": "https://coverr.co/videos/cv-1", "license": "Coverr License"},
        {"id": "cv-2", "title": "крупный план", "max_width": 1920, "max_height": 1080,
         "urls": {"mp4": "https://cdn.coverr.co/cv-2.mp4?token={token}"},
         "link": "https://coverr.co/videos/cv-2"},
    ]}


def _cand(**extra) -> dict:
    """Кандидат, как его отдаёт поиск: минимум полей, нужных скачиванию."""
    c = {"provider": "pexels", "id": 101, "kind": "photo",
         "thumb": "https://images.pexels.com/photos/101/medium.jpg",
         "width": 4000, "height": 6000, "author": "Автор 0",
         "author_url": "https://www.pexels.com/@a0",
         "page_url": "https://www.pexels.com/photo/101/",
         "download_url": "https://images.pexels.com/photos/101/large2x.jpg",
         "tags": ["cat", "window"], "query": "cat on the window"}
    c.update(extra)
    return c


# --------------------------------------------------------------------------- #
# 1. Pexels: фото
# --------------------------------------------------------------------------- #
def test_pexels_photo_candidates(iso, monkeypatch):
    """Три фото в ответе — три кандидата: качаем src.large2x, провайдер и автор на месте."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(3),
                   "pixabay.com/api/": {"hits": []}})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "photo", n=8)
    assert len(cands) == 3
    assert [c["download_url"] for c in cands] == [
        f"https://images.pexels.com/photos/{100 + i}/large2x.jpg" for i in range(3)]
    assert all(c["provider"] == "pexels" and c["kind"] == "photo" for c in cands)
    assert cands[0]["author"] == "Автор 0"
    assert cands[0]["author_url"] == "https://www.pexels.com/@a0"
    assert cands[0]["page_url"] == "https://www.pexels.com/photo/100/"
    assert cands[0]["thumb"].endswith("medium.jpg")
    assert cands[0]["tags"] == ["cat number 0"] and cands[0]["query"] == "cat on the window"
    # ключ уходит заголовком Authorization — как в документации Pexels (без «Bearer»)
    assert net.reqs[0].get_header("Authorization") == PEXELS_KEY
    assert _params(net.calls[0])["per_page"] == "8"
    assert net.calls[0].startswith("https://api.pexels.com/v1/search")   # Pexels первым


# --------------------------------------------------------------------------- #
# 2. Pexels: видео
# --------------------------------------------------------------------------- #
def test_pexels_video_picks_portrait_mp4(iso, monkeypatch):
    """Из video_files — mp4 ближайший к 1080×1920 и не больше 1920 по большей стороне;
    сам запрос идёт с orientation=portrait."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/videos/search": _pexels_videos(1)})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "video", n=4)
    assert len(cands) == 1
    c = cands[0]
    assert c["kind"] == "video" and c["provider"] == "pexels"
    assert c["download_url"] == "https://videos.pexels.com/portrait.mp4"   # не 4K и не webm
    assert (c["width"], c["height"]) == (1080, 1920)      # размеры ФАЙЛА, а не ролика
    assert c["duration"] == 12 and c["author"] == "Оператор 0"

    url = net.urls("api.pexels.com/videos/search")[0]
    assert _params(url)["orientation"] == "portrait"
    assert _params(url)["query"] == "cat on the window"


# --------------------------------------------------------------------------- #
# 3. Pixabay: фото и видео
# --------------------------------------------------------------------------- #
def test_pixabay_photo_and_video(iso, monkeypatch):
    """Фото — largeImageURL; видео — `large`, а без него `medium`. per_page не меньше 3."""
    _write_cfg(pexels=None)
    net = FakeNet({"pixabay.com/api/videos/": _pixabay_videos(),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    photos = stock.search("cat on the window", "photo", n=2)
    assert [c["download_url"] for c in photos] == ["https://cdn.pixabay.com/7_large.jpg",
                                                   "https://cdn.pixabay.com/8_large.jpg"]
    assert photos[0]["provider"] == "pixabay" and photos[0]["kind"] == "photo"
    assert photos[0]["tags"] == ["cat", "animal", "pet"] and photos[0]["query"] == "cat on the window"
    assert photos[0]["author_url"] == "https://pixabay.com/users/Аня-42/"
    photo_url = net.urls("pixabay.com/api/?")[0]
    assert _params(photo_url)["key"] == PIXABAY_KEY
    assert _params(photo_url)["image_type"] == "photo"
    assert int(_params(photo_url)["per_page"]) >= 3

    vids = stock.search("cat on the window", "video", n=2)
    assert [c["download_url"] for c in vids] == ["https://cdn.pixabay.com/9_large.mp4",
                                                 "https://cdn.pixabay.com/10_medium.mp4"]
    assert vids[0]["kind"] == "video" and vids[0]["duration"] == 8
    assert (vids[0]["width"], vids[0]["height"]) == (1920, 1080)
    assert vids[0]["thumb"] == "https://cdn.pixabay.com/9_l.jpg"


# --------------------------------------------------------------------------- #
# 4. Порядок провайдеров и добор
# --------------------------------------------------------------------------- #
def test_order_pexels_first_then_pixabay(iso, monkeypatch):
    """Pexels отдал 2 из 5 — остаток добираем у Pixabay."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "photo", n=5)
    # из 5 просимых нашлось 4 (у Pixabay в стенде всего 2 фото) — порядок: Pexels, потом Pixabay
    assert [c["provider"] for c in cands] == ["pexels", "pexels", "pixabay", "pixabay"]
    assert _params(net.urls("pixabay.com/api/?")[0])["per_page"] == "3"  # добор остатка


def test_pexels_short_then_unsplash_before_pixabay(iso, monkeypatch):
    """Pexels отдал 2 из 5 — следующий опрашиваемый Unsplash, а не Pixabay.

    Порядок — по замеру 2026-10-10 (Pixabay на сложных запросах тянет чужие теги, а раньше
    Unsplash забивал недобор Pexels худшими кадрами). Проверка по порядку обращений в сеть:
    Unsplash спрошен раньше Pixabay, и добор остатка у Pixabay — на 3 (минимум у него).
    Под старым порядком (pixabay раньше unsplash) этот тест краснеет."""
    _write_cfg(unsplash=UNSPLASH_KEY)
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "api.unsplash.com/search/photos": _unsplash_photos(2),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "photo", n=5)
    assert [c["provider"] for c in cands] == ["pexels", "pexels", "unsplash", "unsplash", "pixabay"]
    first_uns = next(i for i, u in enumerate(net.calls) if "api.unsplash.com" in u)
    first_pix = next(i for i, u in enumerate(net.calls) if "pixabay.com/api/" in u)
    assert first_uns < first_pix
    assert _params(net.urls("api.unsplash.com")[0])["per_page"] == "3"   # остаток после Pexels
    assert _params(net.urls("pixabay.com/api/?")[0])["per_page"] == "3"  # добор остатка


def test_unsplash_429_falls_through_to_pixabay(iso, monkeypatch):
    """Demo-режим Unsplash: 50 запросов в час. Отказ 429 не валит поиск — Unsplash
    пропускается с предупреждением, остаток добирает Pixabay."""
    _write_cfg(unsplash=UNSPLASH_KEY)
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "api.unsplash.com/search/photos": urllib.error.HTTPError(
                       "https://api.unsplash.com/search/photos", 429, "Too Many Requests", None, None),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    notes: list[str] = []
    monkeypatch.setattr(stock, "console_emit",
                        lambda line="", **v: notes.append(line.format(**v) if v else line))

    cands = stock.search("cat on the window", "photo", n=5)
    assert [c["provider"] for c in cands] == ["pexels", "pexels", "pixabay", "pixabay"]
    assert net.urls("api.unsplash.com") != []                 # Unsplash спросили, он отказал
    assert net.urls("pixabay.com/api/") != []                 # и добор пошёл к Pixabay
    assert any("unsplash: HTTP 429" in n for n in notes), notes


def test_missing_pixabay_key_skips_provider_silently(iso, monkeypatch):
    """Ключа Pixabay нет — провайдер пропускается молча: результат Pexels, без ошибки."""
    _write_cfg(pixabay=None)
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "photo", n=5)
    assert len(cands) == 2 and all(c["provider"] == "pexels" for c in cands)
    assert net.urls("pixabay") == []


def test_keyless_provider_covers_when_keyed_ones_absent(iso, monkeypatch):
    """Ключей нет ни у одного стока — поиск всё равно работает: Openverse ключа не требует.

    Это и есть смысл провайдера без ключа: на чистой установке кадры ищутся сразу, без
    похода в настройки. Проверяем, что выдача пришла именно от Openverse и что
    провайдеры с ключом в сеть не пошли."""
    _write_cfg(pexels=None, pixabay=None)
    net = FakeNet({"api.openverse.org/v1/images/": _openverse_images()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setenv("REELSI_STOCK_OFF", "")          # Openverse включён

    cands = stock.search("cat on the window", "photo", n=4)
    assert [c["provider"] for c in cands] == ["openverse", "openverse"]
    assert net.urls("pexels") == [] and net.urls("pixabay") == []


# --------------------------------------------------------------------------- #
# 5. Стоков нет вовсе
# --------------------------------------------------------------------------- #
def test_no_providers_at_all_is_user_error(iso, monkeypatch):
    """Все стоки выключены — понятная ошибка с кодом stock_no_keys, а не пустой список."""
    _write_cfg(pexels=None, pixabay=None)
    monkeypatch.setenv("REELSI_STOCK_OFF", ",".join(stock.PROVIDERS))
    net = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", net)

    with pytest.raises(ReelsiError) as e:
        stock.search("cat on the window", "photo")
    assert e.value.code == "stock_no_keys"
    assert "Стоки" in str(e.value)          # подсказка, куда вписать ключ
    assert net.calls == []                  # в сеть не пошли


# --------------------------------------------------------------------------- #
# 6. Кеш ответов (24 ч)
# --------------------------------------------------------------------------- #
def test_cache_prevents_second_request_and_expires(iso, monkeypatch):
    """Повторный тот же поиск в сеть не идёт; запись старше 24 ч — идём заново."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "pixabay.com/api/": {"hits": []}})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    assert len(stock.search("cat on the window", "photo", n=3)) == 2
    calls = len(net.calls)
    assert calls > 0
    assert len(stock.search("cat on the window", "photo", n=3)) == 2
    assert len(net.calls) == calls           # второй раз — из кеша, сети нет

    cache_path = stock.CACHE_PATH
    assert os.path.isfile(cache_path)
    data = json.load(open(cache_path, encoding="utf-8"))
    # Ключ кеша по контракту: provider|kind|query|n|формат. Формат (размер и
    # ориентация) в ключе обязателен — выдача Pexels зависит от ориентации, и поиск
    # в 16:9 иначе получил бы кандидатов вертикального поиска. Поиск без формата —
    # это 9:16, размер 1080×1920 (core/frame.py).
    assert "pexels|photo|cat on the window|3|1920x1920portrait" in data
    for rec in data.values():
        rec["ts"] = time.time() - stock.CACHE_TTL - 60            # сутки с хвостиком назад
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    assert len(stock.search("cat on the window", "photo", n=3)) == 2
    assert len(net.calls) > calls            # протухшую запись не отдали, сходили в сеть


# --------------------------------------------------------------------------- #
# 7. Отказ провайдера
# --------------------------------------------------------------------------- #
def test_http_401_warns_and_falls_through(iso, monkeypatch):
    """401 у Pexels — предупреждение про ключ, результат приходит от Pixabay."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/v1/search": urllib.error.HTTPError(
                       "https://api.pexels.com/v1/search", 401, "Unauthorized", None, None),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    notes: list[str] = []
    monkeypatch.setattr(stock, "console_emit",
                        lambda line="", **v: notes.append(line.format(**v) if v else line))

    cands = stock.search("cat on the window", "photo", n=2)
    assert [c["provider"] for c in cands] == ["pixabay", "pixabay"]
    assert any("ключ pexels не принят" in n for n in notes), notes


# --------------------------------------------------------------------------- #
# 8. Скачивание в базу
# --------------------------------------------------------------------------- #
def test_download_puts_file_license_and_index(iso, monkeypatch):
    """Файл и файл лицензии — в stock/<провайдер>/, через to_ae_media, запись в индексе
    с desc_src="stock"; повторный клик по тому же кандидату не качает заново."""
    _write_cfg()
    _write_index(insertlib.INDEX_PATH)
    base = iso / "lib"
    file_url = "https://images.pexels.com/photos/101/large2x.jpg"
    net = FakeNet(files={file_url: b"\xff\xd8\xff" + b"jpeg-body" * 100})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    converted: list[str] = []
    monkeypatch.setattr(insertlib, "to_ae_media",
                        lambda p, emit=None: (converted.append(p), p)[1])

    path = stock.download(_cand(), str(base))

    assert os.path.normcase(path) == os.path.normcase(
        str(base / "stock" / "pexels" / "cat-on-the-window-101.jpg"))
    assert open(path, "rb").read().startswith(b"\xff\xd8\xff")
    lic = json.load(open(os.path.join(os.path.dirname(path),
                                      "cat-on-the-window-101.license.json"), encoding="utf-8"))
    assert lic["provider"] == "pexels" and lic["id"] == 101
    assert lic["author"] == "Автор 0" and lic["download_url"] == file_url
    assert lic["page_url"] == "https://www.pexels.com/photo/101/" and lic["downloaded_at"]
    assert lic["kind"] == "photo" and "Автор 0" in lic["attribution"]   # атрибуция рядом
    assert converted == [path]                    # перекодировка AE-совместимости вызвана

    items = _read_index(insertlib.INDEX_PATH)["items"]
    assert len(items) == 1
    it = items[0]
    assert os.path.normcase(it["path"]) == os.path.normcase(path)
    assert it["type"] == "photo" and it["desc_src"] == "stock" and it["used"] == 0
    assert it["desc"] == "cat on the window cat window"       # запрос + теги стока

    calls = len(net.calls)
    again = stock.download(_cand(), str(base))
    assert os.path.normcase(again) == os.path.normcase(path)
    assert len(net.calls) == calls                # уже скачан — без сети
    assert len(_read_index(insertlib.INDEX_PATH)["items"]) == 1     # и без дубля в индексе


# --------------------------------------------------------------------------- #
# 9. Unsplash: поиск и обязательный учёт скачивания
# --------------------------------------------------------------------------- #
def test_unsplash_photo_candidates(iso, monkeypatch):
    """Ключ уходит заголовком Client-ID; кандидат хранит `links.download_location`
    (учётную ручку), а не только адрес картинки."""
    _write_cfg(pexels=None, pixabay=None, unsplash=UNSPLASH_KEY)
    net = FakeNet({"api.unsplash.com/search/photos": _unsplash_photos(2)})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setenv("REELSI_STOCK_OFF", "")          # остальные стоки не мешают

    cands = stock.search("cat on the window", "photo", n=2)
    assert len(cands) == 2 and all(c["provider"] == "unsplash" for c in cands)
    c = cands[0]
    assert c["download_url"] == "https://images.unsplash.com/u100-full.jpg"
    assert c["track_url"] == "https://api.unsplash.com/photos/u100/download?ixid=track0"
    assert c["author"] == "Фотограф 0" and c["author_url"] == "https://unsplash.com/@user0"
    assert c["page_url"] == "https://unsplash.com/photos/u100"
    assert c["tags"] == ["cat number 0", "cat", "window"]
    assert "Unsplash" in c["license"]

    req = net.reqs_for("api.unsplash.com/search/photos")[0]
    assert req.get_header("Authorization") == "Client-ID " + UNSPLASH_KEY
    assert _params(req.full_url)["orientation"] == "portrait"


def test_unsplash_download_calls_download_location(iso, monkeypatch):
    """Скачивание Unsplash идёт в ДВА шага: отметка `download_location` (их условие),
    затем файл по адресу из её ответа; автор и лицензия — в файле лицензии."""
    _write_cfg(pexels=None, pixabay=None, unsplash=UNSPLASH_KEY)
    _write_index(insertlib.INDEX_PATH)
    track = "https://api.unsplash.com/photos/u100/download?ixid=track0"
    real = "https://images.unsplash.com/u100-tracked.jpg"
    net = FakeNet({track.split("?")[0]: {"url": real, "download_location": track}},
                  files={real: b"\xff\xd8\xff" + b"jpeg" * 50})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    base = iso / "lib"
    cand = _cand(provider="unsplash", id="u100", kind="photo",
                 download_url="https://images.unsplash.com/u100-full.jpg", track_url=track,
                 author="Фотограф 0", author_url="https://unsplash.com/@user0",
                 page_url="https://unsplash.com/photos/u100",
                 license="Unsplash License (free, attribution required)")

    path = stock.download(cand, str(base), emit=lambda *a, **k: None)

    assert os.path.normcase(path) == os.path.normcase(
        str(base / "stock" / "unsplash" / "cat-on-the-window-u100.jpg"))
    # 1) отметка скачивания ушла с их ключом (без неё ключ приложения отзывают)
    marks = net.urls("api.unsplash.com/photos/u100/download")
    assert marks, "download_location не вызван — Unsplash этого не прощает"
    assert net.reqs_for("api.unsplash.com/photos/u100/download")[0].get_header(
        "Authorization") == "Client-ID " + UNSPLASH_KEY
    # 2) файл скачан по адресу ИЗ ОТВЕТА отметки, а не по ссылке кандидата
    assert net.urls(real), "файл скачан не по адресу из ответа download_location"
    assert not net.urls("u100-full.jpg"), "качали прямую ссылку мимо учёта"
    lic = json.load(open(os.path.join(os.path.dirname(path),
                                      "cat-on-the-window-u100.license.json"), encoding="utf-8"))
    assert lic["author"] == "Фотограф 0"
    assert lic["license"] == "Unsplash License (free, attribution required)"

    # повторный выбор того же кадра: файл не качаем заново, но отметка повторяется
    marks_before, files_before = len(net.urls("/download")), len(net.urls(real))
    stock.download(cand, str(base), emit=lambda *a, **k: None)
    assert len(net.urls("/download")) == marks_before + 1
    assert len(net.urls(real)) == files_before


def test_unsplash_track_failure_falls_back_to_direct_url(iso, monkeypatch):
    """Учёт не ответил (например, 403) — качаем прямую ссылку и говорим об этом:
    кадр важнее счётчика, но молчать нельзя, повтор сразу видно по выдаче."""
    _write_cfg(pexels=None, pixabay=None, unsplash=UNSPLASH_KEY)
    _write_index(insertlib.INDEX_PATH)
    track = "https://api.unsplash.com/photos/u100/download?ixid=track0"
    direct = "https://images.unsplash.com/u100-full.jpg"
    net = FakeNet({track.split("?")[0]: urllib.error.HTTPError(track, 403, "Forbidden",
                                                               None, None)},
                  files={direct: b"\xff\xd8\xff" + b"jpeg" * 20})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    notes: list[str] = []

    stock.download(_cand(provider="unsplash", id="u100", kind="photo",
                         download_url=direct, track_url=track),
                   str(iso / "lib"), emit=notes.append)

    assert net.urls(direct)
    assert any("не отметил скачивание" in n for n in notes), notes


# --------------------------------------------------------------------------- #
# 10. Openverse: ключа нет, лицензии фильтруются
# --------------------------------------------------------------------------- #
def test_openverse_license_filter(iso, monkeypatch):
    """Openverse без ключа: берём только CC0/PDM/BY/BY-SA, NC и ND отбрасываем."""
    _write_cfg(pexels=None, pixabay=None)
    net = FakeNet({"api.openverse.org/v1/images/": _openverse_images()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setenv("REELSI_STOCK_OFF", "")

    cands = stock.search("cat on the window", "photo", n=8)
    assert [c["id"] for c in cands] == ["ov-1", "ov-4"]      # NC, ND и «без ссылки» — мимо
    c = cands[0]
    assert c["provider"] == "openverse" and c["download_url"] == "https://cdn.openverse.org/1.jpg"
    assert c["author"] == "Автор ОВ" and c["license"] == "CC0-1.0"   # имя лицензии с версией
    assert c["page_url"] == "https://example.org/1" and c["tags"] == ["cat window"]
    url = net.urls("api.openverse.org")[0]
    # фильтр заказан у API и не отключён нами
    assert _params(url)["license_type"] == "commercial,modification"
    assert _params(url)["aspect_ratio"] == "tall"
    assert "key" not in _params(url) and "api_key" not in _params(url)   # ключ не нужен


def test_openverse_download_stores_license_and_author(iso, monkeypatch):
    """Скачанное из Openverse ложится в базу с лицензией, источником, тегами и автором."""
    _write_cfg(pexels=None, pixabay=None)
    _write_index(insertlib.INDEX_PATH)
    monkeypatch.setenv("REELSI_STOCK_OFF", "")
    file_url = "https://cdn.openverse.org/1.jpg"
    net = FakeNet(files={file_url: b"\xff\xd8\xff" + b"jpeg" * 30})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    base = iso / "lib"

    path = stock.download(_cand(provider="openverse", id="ov-1", kind="photo",
                                download_url=file_url, author="Автор ОВ",
                                author_url="https://openverse.org/u/1",
                                page_url="https://example.org/1",
                                tags=["cat", "window"], license="CC0-1.0",
                                source="flickr", query="cat on the window"),
                          str(base), emit=lambda *a, **k: None)

    lic = json.load(open(os.path.join(os.path.dirname(path),
                                      "cat-on-the-window-ov-1.license.json"), encoding="utf-8"))
    assert lic["provider"] == "openverse" and lic["license"] == "CC0-1.0"
    assert lic["source"] == "flickr" and lic["tags"] == ["cat", "window"]
    assert lic["author"] == "Автор ОВ" and lic["author_url"] == "https://openverse.org/u/1"
    assert "Автор ОВ" in lic["attribution"]


# --------------------------------------------------------------------------- #
# 11. Coverr: за флагом, разбор ответа
# --------------------------------------------------------------------------- #
def test_coverr_behind_flag_and_video_parsing(iso, monkeypatch):
    """Без `REELSI_STOCK_COVERR` провайдер молчит (даже с ключом), с флагом — отдаёт
    видео; фото он не умеет, поэтому в фото-поиске его нет."""
    _write_cfg(pexels=None, pixabay=None, coverr="COVERR-KEY-4444")
    net = FakeNet({"api.coverr.co/videos": _coverr_videos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setenv("REELSI_STOCK_OFF", "openverse")     # остаётся только Coverr

    # флага нет — Coverr пропущен, а других стоков без ключей нет вовсе
    with pytest.raises(ReelsiError) as e:
        stock.search("cat", "video")
    assert e.value.code == "stock_no_keys"
    assert net.calls == []

    monkeypatch.setenv("REELSI_STOCK_OFF", "")
    monkeypatch.setenv("REELSI_STOCK_COVERR", "1")
    cands = stock.search("cat", "video", n=2)
    # cv-2 с нерасшифрованным {token} отброшен: без подстановки ссылка не работает
    assert [c["provider"] for c in cands] == ["coverr"]
    c = cands[0]
    assert c["download_url"] == "https://cdn.coverr.co/cv-1.mp4"
    assert c["thumb"] == "https://cdn.coverr.co/cv-1-poster.jpg"
    assert c["author"] == "Оператор К" and c["author_url"] == "https://coverr.co/@k"
    assert c["license"] == "Coverr License" and c["kind"] == "video"
    # запрос по их схеме: query, urls=true; ключ — заголовком Bearer, а не в адресе
    url = net.urls("api.coverr.co")[0]
    assert _params(url)["query"] == "cat" and _params(url)["urls"] == "true"
    assert "COVERR-KEY-4444" not in url and "api_key" not in url
    assert net.reqs_for("api.coverr.co")[0].get_header(
        "Authorization") == "Bearer COVERR-KEY-4444"
    # фото Coverr не умеет — в фото-поиске его колонки нет
    assert stock.KINDS == ("photo", "video")
    assert "photo" not in stock.PROVIDER_META["coverr"]["kinds"]


def test_coverr_download_sends_no_key(iso, monkeypatch, tmp_path):
    """Ключ Coverr — только к их API. Файл качается с CDN: заголовка с ключом там быть не должно."""
    _write_cfg(pexels=None, pixabay=None, coverr="COVERR-KEY-4444")
    net = FakeNet({"api.coverr.co/videos": _coverr_videos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setenv("REELSI_STOCK_OFF", "openverse")
    monkeypatch.setenv("REELSI_STOCK_COVERR", "1")
    cand = stock.search("cat", "video", n=1)[0]
    seen: list[dict] = []
    monkeypatch.setattr(stock, "_download_file",
                        lambda url, path, headers=None: (seen.append(headers or {}),
                                                         open(path, "wb").write(b"mp4")))
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    monkeypatch.setattr(insertlib, "add_file", lambda *a, **k: None)
    stock.download(cand, str(tmp_path / "lib"), emit=lambda *a, **k: None)
    assert seen and all("Authorization" not in h for h in seen), seen


def test_unsplash_unsafe_track_url_falls_back(iso, monkeypatch):
    """Адрес учёта пришёл в ответе стока: на закрытый адрес мы не ходим, а качаем
    прямую ссылку с предупреждением (та же проверка, что на скачивании)."""
    _write_cfg(pexels=None, pixabay=None, unsplash=UNSPLASH_KEY)
    _write_index(insertlib.INDEX_PATH)
    bad_track = "http://127.0.0.1:8080/photos/u100/download"
    direct = "https://images.unsplash.com/u100-full.jpg"
    net = FakeNet(files={direct: b"\xff\xd8\xff" + b"jpeg" * 20})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setattr(stock, "unsafe_url_reason",
                        lambda url: "небезопасный адрес" if "127.0.0.1" in url else "")
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    notes: list[str] = []
    stock.download(_cand(provider="unsplash", id="u100", kind="photo",
                         download_url=direct, track_url=bad_track),
                   str(iso / "lib"), emit=notes.append)
    assert not net.urls("127.0.0.1") and net.urls(direct)
    assert any("не отметил скачивание" in n for n in notes), notes


# --------------------------------------------------------------------------- #
# 12. Упрощение запроса (в бой НЕ включено, стенду нужно)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src,want", [
    ("cat on the window", "cat window"),
    ("concept of clean energy", "clean energy"),
    ("a copper pot standing on a wooden table", "copper pot standing"),
    ("3d icon of a broken eyeglasses", "3d icon broken"),
    ("cat", "cat"),
    ("", ""),
    ("кот на окне", ""),
])
def test_simplify_query(src, want):
    """Правило упрощения: предмет + 1–2 признака, служебные слова прочь."""
    assert stock.simplify_query(src) == want


def test_simplify_is_not_used_by_search(iso, monkeypatch):
    """Упрощение НЕ включено в боевой поиск: в запрос уходит фраза карточки как есть."""
    _write_cfg()
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(1)})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    stock.search("concept of clean energy", "photo", n=1)
    assert _params(net.urls("api.pexels.com")[0])["query"] == "concept of clean energy"


# --------------------------------------------------------------------------- #
# 13. add_generated после выноса add_file
# --------------------------------------------------------------------------- #
def test_add_generated_writes_same_index_entry(iso, monkeypatch):
    """Сгенерённая картинка ложится в индекс ровно как раньше: тип, desc, desc_src, look."""
    _write_cfg()
    _write_index(insertlib.INDEX_PATH)
    base = iso / "lib"
    png = b"\x89PNG\r\n\x1a\n" + "картинка".encode("utf-8")

    path = insertlib.add_generated(png, "cat 3d icon", str(base), emit=lambda *a, **k: None,
                                   ru="кошка", look=" 3D   Icon ")

    assert os.path.isfile(path) and path.endswith(".png")
    items = _read_index(insertlib.INDEX_PATH)["items"]
    assert len(items) == 1
    it = items[0]
    assert it["path"] == path and it["name"] == os.path.basename(path)
    assert it["type"] == "photo" and it["desc"] == "cat 3d icon"
    assert it["desc_src"] == "generated" and it["ru"] == "кошка"
    assert it["look"] == "3d icon"           # _norm_look: регистр и пробелы схлопнуты
    assert it["emb"] is None and it["used"] == 0


# --------------------------------------------------------------------------- #
# 14. Роуты
# --------------------------------------------------------------------------- #
def test_route_stock_search_and_pick(iso, monkeypatch, client):
    """POST /api/stock_search — кандидаты; POST /api/stock_pick — файл в базе и путь в ответе."""
    _write_cfg()
    _write_index(insertlib.INDEX_PATH)
    file_url = "https://images.pexels.com/photos/100/large2x.jpg"
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(1)},
                  files={file_url: b"\xff\xd8\xff" + b"jpeg" * 50})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    monkeypatch.setattr(insertlib, "_thumb_b64", lambda p: "ТУМБ")

    d = _post(client, "/api/stock_search", {"query": "cat on the window", "type": "photo"})
    assert d["ok"] is True and len(d["results"]) == 1
    assert d["results"][0]["download_url"] == file_url

    dest = str(iso / "lib")
    d = _post(client, "/api/stock_pick", {"candidate": d["results"][0], "dest": dest})
    assert d["ok"] is True and d["thumb"] == "ТУМБ"
    assert os.path.normcase(d["path"]) == os.path.normcase(
        os.path.join(dest, "stock", "pexels", "cat-on-the-window-100.jpg"))
    assert os.path.isfile(d["path"])
    assert _read_index(insertlib.INDEX_PATH)["items"][0]["desc_src"] == "stock"


@pytest.mark.parametrize("payload,code", [
    ({}, "empty_query"),                                        # запрос потерялся
    ({"query": "  ", "type": "photo"}, "empty_query"),
    ({"query": "кот", "type": "видео"}, "stock_bad_kind"),       # чужой тип вставки
])
def test_route_stock_search_bad_input(iso, monkeypatch, client, payload, code):
    """Кривое тело — umsg-ошибка с кодом, а не 500 и не запрос в сеть."""
    _write_cfg()
    net = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", net)
    d = _post(client, "/api/stock_search", payload)
    assert d.get("ok") is not True and d["err"] == code
    assert net.calls == []


def test_route_stock_routes_need_a_provider(iso, monkeypatch, client):
    """Стоков нет вовсе — роуты отвечают кодом stock_no_keys (переводится по
    ERR_stock_no_keys). Ключи при этом могут быть: важно, что не работает НИ ОДИН сток."""
    _write_cfg(pexels=None, pixabay=None)
    monkeypatch.setenv("REELSI_STOCK_OFF", ",".join(stock.PROVIDERS))
    net = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", net)
    d = _post(client, "/api/stock_search", {"query": "кот на окне", "type": "photo"})
    assert d.get("ok") is not True and d["err"] == "stock_no_keys"
    assert net.calls == []

    d = _post(client, "/api/stock_pick", {"candidate": "не объект", "dest": str(iso / "lib")})
    assert d.get("ok") is not True and d["err"] == "stock_bad_candidate"


def test_route_stock_search_without_any_key_uses_keyless_provider(iso, monkeypatch, client):
    """Без ключей роут НЕ отказывает: Openverse ищет без ключа."""
    _write_cfg(pexels=None, pixabay=None)
    monkeypatch.setenv("REELSI_STOCK_OFF", "")
    net = FakeNet({"api.openverse.org/v1/images/": _openverse_images()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    d = _post(client, "/api/stock_search", {"query": "cat on the window", "type": "photo"})
    assert d["ok"] is True and [c["provider"] for c in d["results"]] == ["openverse", "openverse"]


# --------------------------------------------------------------------------- #
# 15. Ключи в настройках
# --------------------------------------------------------------------------- #
def test_set_stock_keys_mask_and_clear(iso, monkeypatch, client):
    """Маска «•••…» ключ не меняет, пустая строка — удаляет, наружу ключи только маской."""
    _write_cfg(pexels="zq1", pixabay="zq2")

    d = _post(client, "/api/ai_config", {"action": "set_stock_keys",
                                         "pexels_key": "•••zq1", "pixabay_key": "•••zq2"})
    assert d["ok"] is True
    assert d["stock"]["pexels_key"] == "•••zq1" and d["stock"]["pixabay_key"] == "•••zq2"
    saved = json.load(open(aicut_config.AI_CONFIG_PATH, encoding="utf-8"))["stock"]
    assert saved == {"pexels_key": "zq1", "pixabay_key": "zq2"}

    d = _post(client, "/api/ai_config", {"action": "set_stock_keys",
                                         "pexels_key": "", "pixabay_key": "zq3"})
    assert d["stock"]["pexels_key"] == "" and d["stock"]["pixabay_key"] == "•••zq3"
    saved = json.load(open(aicut_config.AI_CONFIG_PATH, encoding="utf-8"))["stock"]
    assert saved == {"pixabay_key": "zq3"}                   # пустая строка — ключа нет
    assert '"zq3"' not in json.dumps(d)                      # сырой ключ наружу не уходит

    # провайдер без ключа молча выключается: Pexels пропущен, ищем в Pixabay
    net = FakeNet({"pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    cands = stock.search("cat on the window", "photo", n=1)
    assert [c["provider"] for c in cands] == ["pixabay"]


def test_set_stock_keys_covers_new_providers(iso, monkeypatch, client):
    """Unsplash и Coverr сохраняются тем же действием и так же маскируются наружу;
    провайдер без поля в теле не трогается (Openverse ключа не заводит вовсе)."""
    _write_cfg(pexels="zq1", pixabay=None)
    d = _post(client, "/api/ai_config", {"action": "set_stock_keys",
                                         "unsplash_key": "env:STOCK_UNSPLASH",
                                         "coverr_key": "cov-7777"})
    assert d["ok"] is True
    assert d["stock"]["unsplash_key"] == "env:STOCK_UNSPLASH"    # env — имя, не секрет
    assert d["stock"]["coverr_key"] == "•••7777"
    assert d["stock"]["pexels_key"] == "•••zq1"                  # не тронут — не в теле
    saved = json.load(open(aicut_config.AI_CONFIG_PATH, encoding="utf-8"))["stock"]
    assert saved == {"pexels_key": "zq1", "unsplash_key": "env:STOCK_UNSPLASH",
                     "coverr_key": "cov-7777"}
    assert "cov-7777" not in json.dumps(d)                       # сырой ключ наружу не уходит
    assert "openverse_key" not in saved                          # у Openverse ключа нет


# --------------------------------------------------------------------------- #
# 16. Фронт
# --------------------------------------------------------------------------- #
def test_frontend_has_stock_button_and_keys():
    """Кнопка «Сток» на карточке вставки, сброс stockOpts рядом с libOpts, поля ключей в ⚙."""
    src = open(os.path.join(ROOT, "static", "app", "80-inserts.js"), encoding="utf-8").read()
    assert "onclick=\"insStockFor('+i+')\"" in src
    assert "async function insStockFor(i)" in src
    assert "async function insStockPick(i,j)" in src
    assert "x.stockShown?insStockRow(x,i):''" in src
    assert re.search(r"libOpts=null;[^\n]*x\.stockOpts=null", src), \
        "stockOpts не сбрасывается там же, где libOpts (смена запроса)"
    assert "/api/stock_search" in src and "/api/stock_pick" in src
    assert "t('Сток')" in src and "t('качаю со стока…')" in src

    html = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
    # Ключи всех провайдеров с ключом — в ⚙; у Openverse поля нет (ключ не нужен).
    # Строки ключей идут в порядке опроса стоков (как в core/stock.PROVIDERS): Pexels,
    # Unsplash, Pixabay, Coverr. Порядок строк — часть контракта ⚙, а не случайность вёрстки.
    keyed = [p for p in stock.PROVIDERS if stock.PROVIDER_META[p]["keyed"]]
    assert keyed == ["pexels", "unsplash", "pixabay", "coverr"]
    for field in ("stk_" + p for p in keyed):
        assert f'id="{field}"' in html, field
        assert f'type="password" id="{field}"' in html, field
    rows = [html.index(f'id="stk_{p}"') for p in keyed]
    assert rows == sorted(rows), "строки ключей в ⚙ не в порядке опроса стоков"
    assert "stk_openverse" not in html
    assert "Сохранить ключи" in html

    setup = open(os.path.join(ROOT, "static", "app", "10-settings.js"), encoding="utf-8").read()
    assert "function fillStockKeys()" in setup and "AICFG.stock" in setup
    assert "{action:'set_stock_keys'" in setup
    # Поля ⚙ — в порядке опроса стоков (см. core/stock.PROVIDERS, замер 2026-10-10)
    assert "STK_FIELDS=['pexels','unsplash','pixabay','coverr']" in setup
