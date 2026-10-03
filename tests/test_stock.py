# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OR: стоки Pexels и Pixabay как источник вставок.

Проверяются `core/stock.py` (поиск, порядок провайдеров, выбор файла, кеш ответов,
скачивание в базу вставок) и два роута `api/inserts.py` (`/api/stock_search`,
`/api/stock_pick`), плюс действие `set_stock_keys` и кнопка на карточке вставки.

Сети нет вовсе: `urllib.request.urlopen` подменён фейком ПО URL. Без этого тест либо
жёг бы чужой лимит запросов (200/ч у Pexels), либо падал бы от отсутствия интернета.

Изоляция жёсткая: кеш стока (`stock.CACHE_PATH`) и индекс базы (`insertlib.INDEX_PATH`)
указывают в tmp_path, эмбеддер подменён на «нет», `ai_config.json` — свой, со
стендовыми ключами (боевой файл не читается и не пишется: путь подменяет conftest).

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
    return tmp_path


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _write_cfg(pexels: str | None = PEXELS_KEY, pixabay: str | None = PIXABAY_KEY,
               rembg: bool = False) -> None:
    """Свой ai_config.json: ключи стендовые, снятие фона выключено (rembg тянул бы
    модель из сети). None у провайдера — поля в конфиге нет, то есть ключа нет."""
    cfg = {"active": "Стенд",
           "profiles": {"Стенд": {"provider": "lmstudio",
                                  "base_url": "http://localhost:1234/v1",
                                  "api_key": "", "model": "стенд-модель"}},
           "image_rembg": rembg,
           "stock": {k: v for k, v in (("pexels_key", pexels), ("pixabay_key", pixabay)) if v}}
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


def test_missing_pixabay_key_skips_provider_silently(iso, monkeypatch):
    """Ключа Pixabay нет — провайдер пропускается молча: результат Pexels, без ошибки."""
    _write_cfg(pixabay=None)
    net = FakeNet({"api.pexels.com/v1/search": _pexels_photos(2),
                   "pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)

    cands = stock.search("cat on the window", "photo", n=5)
    assert len(cands) == 2 and all(c["provider"] == "pexels" for c in cands)
    assert net.urls("pixabay") == []


# --------------------------------------------------------------------------- #
# 5. Ключей нет вовсе
# --------------------------------------------------------------------------- #
def test_no_keys_at_all_is_user_error(iso, monkeypatch):
    """Ни одного ключа — понятная ошибка с кодом stock_no_keys, а не пустой список."""
    _write_cfg(pexels=None, pixabay=None)
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
    с desc_src="stock"; повторный клик по тому же кандидату сети не касается."""
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
# 9. add_generated после выноса add_file
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
# 10. Роуты
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


def test_route_stock_routes_need_keys(iso, monkeypatch, client):
    """Ключей нет — роуты отвечают кодом stock_no_keys (переводится по ERR_stock_no_keys)."""
    _write_cfg(pexels=None, pixabay=None)
    net = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", net)
    d = _post(client, "/api/stock_search", {"query": "кот на окне", "type": "photo"})
    assert d.get("ok") is not True and d["err"] == "stock_no_keys"
    assert net.calls == []

    d = _post(client, "/api/stock_pick", {"candidate": "не объект", "dest": str(iso / "lib")})
    assert d.get("ok") is not True and d["err"] == "stock_bad_candidate"


# --------------------------------------------------------------------------- #
# 11. Ключи в настройках
# --------------------------------------------------------------------------- #
def test_set_stock_keys_mask_and_clear(iso, monkeypatch, client):
    """Маска «•••…» ключ не меняет, пустая строка — удаляет, наружу ключи только маской."""
    _write_cfg(pexels="zq1", pixabay="zq2")

    d = _post(client, "/api/ai_config", {"action": "set_stock_keys",
                                         "pexels_key": "•••zq1", "pixabay_key": "•••zq2"})
    assert d["ok"] is True
    assert d["stock"] == {"pexels_key": "•••zq1", "pixabay_key": "•••zq2"}
    saved = json.load(open(aicut_config.AI_CONFIG_PATH, encoding="utf-8"))["stock"]
    assert saved == {"pexels_key": "zq1", "pixabay_key": "zq2"}

    d = _post(client, "/api/ai_config", {"action": "set_stock_keys",
                                         "pexels_key": "", "pixabay_key": "zq3"})
    assert d["stock"] == {"pexels_key": "", "pixabay_key": "•••zq3"}
    saved = json.load(open(aicut_config.AI_CONFIG_PATH, encoding="utf-8"))["stock"]
    assert saved == {"pixabay_key": "zq3"}                   # пустая строка — ключа нет
    assert '"zq3"' not in json.dumps(d)                      # сырой ключ наружу не уходит

    # провайдер без ключа молча выключается: Pexels пропущен, ищем в Pixabay
    net = FakeNet({"pixabay.com/api/": _pixabay_photos()})
    monkeypatch.setattr(urllib.request, "urlopen", net)
    cands = stock.search("cat on the window", "photo", n=1)
    assert [c["provider"] for c in cands] == ["pixabay"]


# --------------------------------------------------------------------------- #
# 12. Фронт
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
    assert 'id="stk_pexels"' in html and 'id="stk_pixabay"' in html
    assert 'type="password" id="stk_pexels"' in html
    assert "Сохранить ключи" in html

    setup = open(os.path.join(ROOT, "static", "app", "10-settings.js"), encoding="utf-8").read()
    assert "function fillStockKeys()" in setup and "AICFG.stock" in setup
    assert "{action:'set_stock_keys'" in setup
