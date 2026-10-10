# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""core/stock.py — стоки (Pexels, Unsplash, Pixabay, Openverse, Coverr) как источник вставок.

Когда подходящего файла нет ни в своей базе, ни в генерации, кадр берётся со стока:
поиск по тому же запросу карточки, показ кандидатов, скачивание ВЫБРАННОГО файла в
папку базы (`stock/<сток>/`) с файлом лицензии и записью в индекс. Дальше это обычный
файл базы: автоподбор находит его сам, в следующих роликах сток не нужен вовсе.

Решения, которые тут зашиты (и почему именно так):

- **Порядок провайдеров = приоритет**: Pexels, Unsplash, Pixabay, затем Openverse и
  Coverr. Сначала те, у кого выдача по делу (замер и причины — в docs/STOCK_PROVIDERS.md).
- **Хотлинк не годится**: Pixabay его прямо запрещает. Поэтому файл всегда качается к
  себе, а ответы поиска кешируются (Pexels и Pixabay это разрешают, ~24 ч) — иначе
  один прогон карточек выбивал бы лимит запросов.
- **Кеш — свой файл состояния**, значит своя переменная `REELSI_STOCK_CACHE`: без неё
  тестовый профиль 5098 писал бы в боевой кеш (правило проекта: у каждого файла
  состояния своя переменная `REELSI_*`).
- **Ориентация и размер — от формата ролика, а не «самый большой файл»**: вставка
  едет в кадр ролика (по умолчанию вертикальный 1080×1920) на 2–3 секунды, 4K тут не
  виден, а вес и декодирование в After Effects растут вчетверо. Ориентацию и целевой
  размер даёт core/frame.py: в горизонтальном ролике нужен горизонтальный кадр.
- **Файл прогоняется через `insertlib.to_ae_media`**: стоки отдают AV1 и `.webp`,
  которые AE не импортирует вовсе (сторожит core/verify_jsx.py).
- **Атрибуция обязательна и хранится рядом с файлом**: `<файл>.license.json` со
  стоком, id, автором, ссылкой на страницу, лицензией и датой скачивания. У Unsplash
  этого мало — их условия требуют ещё и отметить факт скачивания запросом к
  `links.download_location` (иначе приложение блокируют), у Openverse результат
  отбирается по лицензии, разрешающей коммерческое использование и изменение.
  Подробности — в `docs/STOCK_PROVIDERS.md`.
- **Провайдер без ключа молча пропускается**, а без ключа и без флага Coverr — тем
  более: список стоков не должен превращать «нет ключа» в отказ всего поиска.
  Openverse ключа не требует вовсе, поэтому сток работает даже на чистой установке.
- **Отключить провайдера совсем** можно переменной `REELSI_STOCK_OFF` (список имён
  через запятую): у Openverse ключа нет, и без выключателя его нельзя ни убрать из
  выдачи, ни проверить отказ «нет ни одного стока».
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import IO, Any

from core import frame
from core import paths
from core._pathguard import inside_dir, safe_name
from core.app_meta import (APP_NAME, APP_REFERER, console_emit, env, http_req,
                           unsafe_url_reason, wrap_emit)
from core.applog import get_logger
from core.fileio import _atomic_write, atomic_json_dump
from core.umsg import ReelsiError, umsg

log = get_logger(__name__)

# Порядок = приоритет. Замер 2026-10-10 на 30 запросах владельца (выдачу смотрели глазами):
# Pexels и Unsplash по делу почти всегда 3–4 кадра из 4. Pixabay на сложных запросах
# цепляет чужие теги (жирафы, обложки COVID, мотоциклы на «250»), а когда стоял раньше
# Unsplash, забивал недобор Pexels худшими кадрами — поэтому после Unsplash.
# Openverse в основном мусор, Coverr — видео-сток с наименее проверенной выдачей: оба
# последними (см. docs/STOCK_PROVIDERS.md).
PROVIDERS = ("pexels", "unsplash", "pixabay", "openverse", "coverr")
KINDS = ("photo", "video")

# Что умеет провайдер и что ему нужно для работы. `keyed` — ходит по ключу из
# ai_config.json; `flag` — включается только явной переменной окружения (Coverr:
# его нынешняя выдача не проверена, а провайдер с непроверенным контрактом не должен
# молча лезть в боевой поиск); `license` — из какого поля ответа берётся лицензия.
PROVIDER_META: dict[str, dict[str, Any]] = {
    "pexels":    {"kinds": ("photo", "video"), "keyed": True,  "flag": ""},
    "pixabay":   {"kinds": ("photo", "video"), "keyed": True,  "flag": ""},
    "unsplash":  {"kinds": ("photo",),         "keyed": True,  "flag": ""},
    "openverse": {"kinds": ("photo",),         "keyed": False, "flag": ""},
    "coverr":    {"kinds": ("video",),         "keyed": True,  "flag": "STOCK_COVERR"},
}

# Кеш ответов поиска. REELSI_STOCK_CACHE — как REELSI_INSERTLIB у базы вставок: у
# изолированного профиля кеш свой, иначе тестовая копия пишет в боевой файл.
CACHE_PATH = env("STOCK_CACHE") or paths.root("stock_cache.json")
CACHE_TTL = 24 * 3600                    # столько разрешено кешировать Pexels и Pixabay
SEARCH_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 120

# Целевой размер файла и потолок по большей стороне — от ФОРМАТА ролика
# (core/frame.py), а не от жёстких 1080×1920: в горизонтальный ролик нужен
# горизонтальный кадр, в квадратный — квадратный. Целевая высота — высота кадра,
# потолок — самая длинная сторона из возможных форматов: 4K-вставку на 2–3 секунды
# в кадре не видно, а вес и декодирование в After Effects растут вчетверо.
MAX_SIDE = max(max(w, h) for w, h in frame.FORMATS.values())


def _target_size(fmt: Any) -> tuple[int, int]:
    """(целевая высота файла, потолок по большей стороне) для формата ролика.

    Умолчание (9:16) — прежние 1920/1920: поиск и отбор файлов не меняются.
    """
    w, h = frame.frame_size(fmt)
    return h, max(MAX_SIDE, w)


# Тексты, которые видит пользователь, — рядом с кодом: их перевод по коду ERR_<код>
# берёт интерфейс, а русский текст показывается как есть.
NO_KEYS_TEXT = ("Не задан ни один сток — включи ключ Pexels, Pixabay, Unsplash или "
                "Coverr в настройках ⚙ → Генерация → Стоки (Openverse работает без ключа)")
BAD_CANDIDATE_TEXT = ("Кандидат стока неполный (нет ссылки на файл) — "
                      "обнови поиск и выбери кадр заново")

_CACHE_LOCK = threading.RLock()          # чтение-изменение-запись кеша целиком под локом


# ---------- ключи и доступность провайдеров ----------
def keys() -> dict[str, str]:
    """Ключи стоков из ai_config.json (раздел `stock`), по провайдеру.

    Читаем через тот же `load_ai_config`/`resolve_key`, что и профили ИИ: запись
    вида `env:ИМЯ` означает «взять из окружения», наружу при этом уходит имя
    переменной, а не секрет. У провайдера без ключа значение — пустая строка."""
    from core.aicut.config import load_ai_config, resolve_key
    section = load_ai_config().get("stock")
    section = section if isinstance(section, dict) else {}
    out: dict[str, str] = {}
    for prov in PROVIDERS:
        raw = resolve_key(section.get(prov + "_key"))
        out[prov] = raw.strip() if isinstance(raw, str) else ""
    return out


def providers_off() -> frozenset[str]:
    """Провайдеры, выключенные переменной `REELSI_STOCK_OFF` (список через запятую).

    Нужно там, где провайдер нельзя выключить иначе: у Openverse нет ключа, и без
    этой двери он был бы неотключаемым — ни в отладке, ни в отказе «нет ни одного
    стока», ни в поиске по одному конкретному стоку."""
    raw = env("STOCK_OFF") or ""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def provider_ready(provider: str) -> bool:
    """Может ли провайдер ответить прямо сейчас (есть ключ / не нужен / флаг включён)."""
    meta = PROVIDER_META.get(provider)
    if not meta or provider in providers_off():
        return False
    flag = str(meta.get("flag") or "")
    if flag and not env(flag):
        return False
    if meta.get("keyed") and not (keys().get(provider) or ""):
        return False
    return True


def ready_providers() -> tuple[str, ...]:
    """Провайдеры, готовые к работе, в порядке приоритета."""
    return tuple(p for p in PROVIDERS if provider_ready(p))


def has_keys() -> bool:
    """Есть ли ХОТЬ ОДИН рабочий сток. Нужен роуту: отказ «стоков нет» должен
    приходить до запроса в сеть и с подсказкой, куда вписать ключ.

    Имя историческое (раньше все стоки были по ключу): теперь стоком считается и
    провайдер без ключа — Openverse. Отказ остаётся возможным: все провайдеры можно
    выключить `REELSI_STOCK_OFF`."""
    return bool(ready_providers())


def search(query: str, kind: str, n: int = 8, fmt: Any = None) -> list[dict[str, Any]]:
    """Кандидаты со стоков: опрашиваем провайдеров по порядку, пока не набрано `n`.

    Провайдер без ключа пропускается молча (ключ может быть только у одного), а
    сетевой отказ провайдера — предупреждение и следующий: частичная выдача лучше
    пустой, и 401 у одного стока не должен отменять второй. `kind` — "photo" | "video".

    `fmt` — формат кадра ролика (ключ `frame.FORMATS`) или None: у него берутся
    ориентация поиска (в вертикальный ролик нужен вертикальный кадр) и целевой
    размер файла. Ничего не задано — 9:16, как было.
    """
    q = (query or "").strip()
    if not q:
        raise ReelsiError(umsg("empty_query", "Пустой запрос — у вставки нет query"))
    if kind not in KINDS:
        raise ReelsiError(umsg("stock_bad_kind", f"Неизвестный тип стока «{kind}»", kind=kind))
    n = max(1, int(n))
    ready = ready_providers()
    if not ready:
        raise ReelsiError(umsg("stock_no_keys", NO_KEYS_TEXT))
    prov_keys = keys()
    size = _target_size(fmt)
    orient = frame.orientation(fmt)
    out: list[dict[str, Any]] = []
    for prov in ready:
        if len(out) >= n:
            break
        if kind not in PROVIDER_META[prov]["kinds"]:
            continue                       # у Unsplash нет видео, у Coverr — фото
        try:
            out.extend(_provider_search(prov, prov_keys.get(prov) or "", q, kind,
                                        n - len(out), size, orient))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                console_emit("  ⚠ ключ {provider} не принят (HTTP {code})",
                             provider=prov, code=e.code)
            else:
                console_emit("  ⚠ {provider}: HTTP {code}", provider=prov, code=e.code)
        except ReelsiError:
            raise
        except Exception as e:
            console_emit("  ⚠ {provider}: {err}", provider=prov, err=e)
    return out[:n]


def download(cand: dict[str, Any], dest_dir: str, emit: Any = console_emit) -> str:
    """Скачать выбранного кандидата В БАЗУ и вернуть путь файла-вставки.

    Файл ложится в `<dest_dir>/stock/<провайдер>/<slug>-<id>.<ext>`, рядом — файл
    лицензии. Потом файл проходит `insertlib.to_ae_media` (стоки отдают AV1 и .webp,
    которые AE не читает), у фото по галке «убирать фон» снимается фон, и запись
    уходит в индекс базы с `desc_src="stock"`.

    Адрес файла берётся у провайдера (`_file_url`), а не прямо из кандидата: у
    Unsplash ссылка `links.download_location` — это не картинка, а ручка их учёта
    скачиваний, и по её ответу приходит настоящий адрес.

    Уже скачанный файл возвращается БЕЗ повторного скачивания: повторный клик по
    тому же кандидату не должен плодить копии. Но учётный вызов провайдера (Unsplash)
    повторяется и в этом случае — иначе повторный выбор кадра выглядел бы для стока
    как «скачали мимо счётчика», а это ровно то, за что приложение блокируют (поймано
    мутацией: убрать вызов — тест Unsplash краснеет).
    """
    emit = wrap_emit(emit)
    prov = str(cand.get("provider") or "")
    kind = str(cand.get("kind") or "photo")
    kind = kind if kind in KINDS else "photo"           # чужой тип — считаем фото
    url = str(cand.get("download_url") or "").strip()
    if prov not in PROVIDERS or not url:
        raise ReelsiError(umsg("stock_bad_candidate", BAD_CANDIDATE_TEXT, provider=prov))
    if not str(dest_dir or "").strip():
        raise ReelsiError(umsg("need_folders", "Укажи хотя бы одну папку"))
    key = keys().get(prov) or ""
    file_url, extra = _file_url(prov, cand, url, key, emit)   # Unsplash: учёт + адрес
    sub = os.path.join(os.path.abspath(dest_dir), "stock", prov)
    os.makedirs(sub, exist_ok=True)
    # Имя файла собирается из ДАННЫХ ОТВЕТА стока, а не из запроса пользователя:
    # враждебный или взломанный провайдер подсунул бы id="../../../../tmp/OWNED",
    # и файл записался бы за пределами базы. `safe_name` чистит имя до склейки, а
    # `inside_dir` проверяет realpath уже готового пути: одной чистки мало — абсолютное
    # имя выбрасывает папку из склейки, а символическая ссылка внутри базы уводит файл
    # наружу при внешне безобидном имени.
    name = f"{_slug(cand)}-{safe_name(cand.get('id'), 'cand')}{_ext_of(file_url, kind)}"
    try:
        path = inside_dir(sub, name)
    except ValueError:
        raise ReelsiError(umsg("stock_bad_candidate", BAD_CANDIDATE_TEXT, provider=prov))
    fresh = not os.path.isfile(path)                    # «уже скачан» — по наличию файла
    if fresh:
        # Ключ — только к API провайдера. Файл качается с CDN (cdn.coverr.co, images.unsplash.com):
        # слать туда Bearer/Client-ID значило бы отдать ключ чужому хосту.
        _download_file(file_url, path, {"Accept": "*/*"})
    from core import insertlib                           # ленивый импорт: numpy/PIL нужны не всем
    path = insertlib.to_ae_media(path, emit=emit)
    if fresh:
        # лицензия — по СТЕМУ файла в базе, то есть после перекодировки
        _write_license(path, cand, extra, kind)
        emit("  сток {provider}: скачан {name}", provider=prov, name=os.path.basename(path))
        if kind == "photo":
            path = _strip_bg(path, emit)
        insertlib.add_file(path, _desc(cand), kind, src="stock", emit=emit)
    return path


def _strip_bg(path: str, emit: Any) -> str:
    """Фото-вставка — предмет без фона (как у сгенерённых). Нет rembg или модель не
    скачалась — это предупреждение, а не отказ: кадр кладём как есть, фон юзер снимет
    кнопкой на карточке."""
    from core import insertlib
    from core.aicut import image_rembg_on
    if not image_rembg_on():
        return path
    try:
        return insertlib.strip_bg_file(path, emit=emit)
    except ReelsiError:
        raise
    except Exception as e:
        emit("  ⚠ фон не убран: {err}", err=e)
        return path


# ---------- провайдеры ----------
def _provider_search(provider: str, key: str, query: str, kind: str,
                     n: int, size: tuple[int, int], orient: str) -> list[dict[str, Any]]:
    """Кандидаты одного провайдера: из кеша или запросом (с записью в кеш).

    `size` (целевая высота, потолок стороны) и `orient` — от формата ролика. Они
    входят в КЛЮЧ КЕША: ответы Pexels зависят от ориентации, и без этого поиск
    в 16:9 получил бы кандидатов вертикального поиска.
    """
    cached = _cache_get(provider, kind, query, n, size, orient)
    if cached is not None:
        return cached
    if provider == "pexels":
        items = _pexels_search(key, query, kind, n, size, orient)
    elif provider == "pixabay":
        items = _pixabay_search(key, query, kind, n)
    elif provider == "unsplash":
        items = _unsplash_search(key, query, n, orient)
    elif provider == "openverse":
        items = _openverse_search(query, n, orient)
    elif provider == "coverr":
        items = _coverr_search(key, query, n, size)
    else:
        items = []
    for it in items:
        it["query"] = query          # из запроса собирается desc записи индекса
    _cache_put(provider, kind, query, n, size, orient, items)
    return items


def _pexels_search(key: str, query: str, kind: str, n: int,
                   size: tuple[int, int], orient: str) -> list[dict[str, Any]]:
    """Pexels: ключ в заголовке Authorization (без «Bearer» — так в их документации).
    Ориентация — по формату ролика: кадр вставки повторяет кадр ролика."""
    endpoint = "https://api.pexels.com/v1/search" if kind == "photo" \
        else "https://api.pexels.com/videos/search"
    url = endpoint + "?" + urllib.parse.urlencode(
        {"query": query, "per_page": max(1, min(80, n)), "orientation": orient})
    data = _get_json(http_req(url, headers={"Authorization": key}))
    out: list[dict[str, Any]] = []
    if kind == "photo":
        for p in data.get("photos") or []:
            c = _pexels_photo(p) if isinstance(p, dict) else None
            if c:
                out.append(c)
        return out
    for v in data.get("videos") or []:
        if not isinstance(v, dict):
            continue
        f = _pexels_file(v.get("video_files"), size)
        if f:
            out.append(_pexels_video(v, f))
    return out


def _pexels_photo(p: dict[str, Any]) -> dict[str, Any] | None:
    """Фото Pexels: качаем `src.large2x` (дальше отдаём в базу уже им)."""
    src = p.get("src")
    src = src if isinstance(src, dict) else {}
    url = str(src.get("large2x") or src.get("original") or "")
    if not url:
        return None
    alt = str(p.get("alt") or "").strip()
    return {"provider": "pexels", "id": p.get("id"), "kind": "photo",
            "thumb": str(src.get("medium") or src.get("small") or url),
            "width": p.get("width"), "height": p.get("height"),
            "author": str(p.get("photographer") or ""),
            "author_url": str(p.get("photographer_url") or ""),
            "page_url": str(p.get("url") or ""),
            "download_url": url,
            "tags": [alt] if alt else []}


def _pexels_file(files: Any, size: tuple[int, int] = (1920, 1920)) -> dict[str, Any] | None:
    """Файл видео из `video_files`: только mp4, не больше потолка по большей стороне,
    ближайший по высоте к кадру ролика. Не подошёл ни один — кандидата нет: тянуть 4K
    ради двухсекундной вставки хуже, чем пропустить этот ролик.

    `size` — (целевая высота, потолок стороны) от формата ролика (`_target_size`).
    Дефолт — прежние 1920×1920 (вертикальный кадр): прямые вызовы и старые тесты
    отбирают файлы ровно как раньше."""
    target_h, max_side = size
    best: dict[str, Any] | None = None
    best_key: tuple[int, int] | None = None
    for f in files if isinstance(files, list) else []:
        if not isinstance(f, dict):
            continue
        link = str(f.get("link") or "")
        ftype = str(f.get("file_type") or "").lower()
        if ftype != "video/mp4" and not link.lower().split("?")[0].endswith(".mp4"):
            continue
        w, h = _int(f.get("width")), _int(f.get("height"))
        if not w or not h or max(w, h) > max_side:
            continue
        k = (abs(h - target_h), -h)      # при равной разнице — что крупнее
        if best_key is None or k < best_key:
            best, best_key = f, k
    return best


def _pexels_video(v: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    """Кандидат-видео Pexels по выбранному файлу: размеры берём у ФАЙЛА (у самого
    ролика они исходные, 4K), автора — из user."""
    user = v.get("user")
    user = user if isinstance(user, dict) else {}
    return {"provider": "pexels", "id": v.get("id"), "kind": "video",
            "thumb": str(v.get("image") or ""),
            "width": f.get("width") or v.get("width"),
            "height": f.get("height") or v.get("height"),
            "duration": v.get("duration"),
            "author": str(user.get("name") or ""),
            "author_url": str(user.get("url") or ""),
            "page_url": str(v.get("url") or ""),
            "download_url": str(f.get("link") or ""),
            "tags": []}                        # тегов у видео Pexels в ответе нет


def _pixabay_search(key: str, query: str, kind: str, n: int) -> list[dict[str, Any]]:
    """Pixabay: ключ параметром `key`. `per_page` у них от 3 до 200 — меньше нельзя
    (запрос отбивается 400), поэтому дно прижимаем к 3."""
    base = "https://pixabay.com/api/" if kind == "photo" else "https://pixabay.com/api/videos/"
    params: dict[str, Any] = {"key": key, "q": query, "per_page": max(3, min(200, n))}
    if kind == "photo":
        params["image_type"] = "photo"
    data = _get_json(http_req(base + "?" + urllib.parse.urlencode(params)))
    out: list[dict[str, Any]] = []
    for h in data.get("hits") or []:
        if not isinstance(h, dict):
            continue
        c = _pixabay_photo(h) if kind == "photo" else _pixabay_video(h)
        if c:
            out.append(c)
    return out


def _pixabay_photo(h: dict[str, Any]) -> dict[str, Any] | None:
    """Фото Pixabay: качаем `largeImageURL` (хотлинк запрещён — файл всегда к себе)."""
    url = str(h.get("largeImageURL") or "")
    if not url:
        return None
    return {"provider": "pixabay", "id": h.get("id"), "kind": "photo",
            "thumb": str(h.get("previewURL") or h.get("webformatURL") or url),
            "width": h.get("imageWidth"), "height": h.get("imageHeight"),
            "author": str(h.get("user") or ""),
            "author_url": _pixabay_author_url(h),
            "page_url": str(h.get("pageURL") or ""),
            "download_url": url,
            "tags": _tags_of(h.get("tags"))}


def _pixabay_video(h: dict[str, Any]) -> dict[str, Any] | None:
    """Видео Pixabay: `large`, а если его нет — `medium` (остальные варианты
    заведомо мельче кадра вставки)."""
    vids = h.get("videos")
    vids = vids if isinstance(vids, dict) else {}
    v = vids.get("large")
    v = v if isinstance(v, dict) else None
    if not v:
        v = vids.get("medium")
        v = v if isinstance(v, dict) else None
    if not v:
        return None
    url = str(v.get("url") or "")
    if not url:
        return None
    return {"provider": "pixabay", "id": h.get("id"), "kind": "video",
            "thumb": str(v.get("thumbnail") or ""),
            "width": v.get("width"), "height": v.get("height"),
            "duration": h.get("duration"),
            "author": str(h.get("user") or ""),
            "author_url": _pixabay_author_url(h),
            "page_url": str(h.get("pageURL") or ""),
            "download_url": url,
            "tags": _tags_of(h.get("tags"))}


def _pixabay_author_url(h: dict[str, Any]) -> str:
    """Ссылка на автора: отдельным полем Pixabay её не отдаёт, но отдаёт `user` и
    `user_id`, из которых собирается канонический адрес профиля."""
    user, uid = str(h.get("user") or "").strip(), h.get("user_id")
    return f"https://pixabay.com/users/{user}-{uid}/" if user and uid else ""


# ---------- Unsplash (фото по ключу) ----------
# Их условия (https://unsplash.com/documentation) требуют от приложения двух вещей
# сверх обычной ссылки на автора: отмечать КАЖДОЕ скачивание запросом к
# `links.download_location` и хранить автора/ссылку. Первое — не украшение: без него
# ключ отзывают. Поэтому скачивание идёт в два шага (_file_url -> _unsplash_track).
UNSPLASH_ORIENT = {"portrait": "portrait", "landscape": "landscape", "square": "squarish"}


def _unsplash_search(key: str, query: str, n: int, orient: str) -> list[dict[str, Any]]:
    """Unsplash: ключ заголовком `Authorization: Client-ID <key>`, поиск фото.

    Ориентация — от формата ролика: в вертикальный ролик нужен вертикальный кадр.
    `orientation=squarish` у них отвечает за квадрат (`square` они не принимают)."""
    params: dict[str, Any] = {"query": query, "per_page": max(1, min(30, n))}
    want = UNSPLASH_ORIENT.get(orient)
    if want:
        params["orientation"] = want
    data = _get_json(http_req("https://api.unsplash.com/search/photos?"
                              + urllib.parse.urlencode(params),
                              headers={"Authorization": f"Client-ID {key}"}))
    out: list[dict[str, Any]] = []
    for p in data.get("results") or []:
        if not isinstance(p, dict):
            continue
        c = _unsplash_photo(p)
        if c:
            out.append(c)
    return out


def _unsplash_photo(p: dict[str, Any]) -> dict[str, Any] | None:
    """Фото Unsplash: качаем `urls.full` (2000px по длинной стороне — как `large2x`
    у Pexels), у кандидата хранится ИМЕННО `links.download_location`: он не картинка,
    а учётная ручка, и по ней `_unsplash_track` получает настоящий адрес."""
    urls = p.get("urls")
    urls = urls if isinstance(urls, dict) else {}
    links = p.get("links")
    links = links if isinstance(links, dict) else {}
    user = p.get("user")
    user = user if isinstance(user, dict) else {}
    track = str(links.get("download_location") or "")
    url = str(urls.get("full") or urls.get("regular") or "")
    if not track or not url:
        return None
    return {"provider": "unsplash", "id": p.get("id"), "kind": "photo",
            "thumb": str(urls.get("small") or urls.get("regular") or url),
            "width": p.get("width"), "height": p.get("height"),
            "author": str(user.get("name") or ""),
            "author_url": _unsplash_author_url(user),
            "page_url": str(links.get("html") or ""),
            "download_url": url,                 # запасной адрес: если учёт не ответил
            "track_url": track,                  # учётный вызов их условий
            "tags": _unsplash_tags(p),
            "license": "Unsplash License (free, attribution required)"}


def _unsplash_author_url(user: dict[str, Any]) -> str:
    """Ссылка на автора: `links.html` профиля, иначе канонический адрес по имени."""
    links = user.get("links")
    links = links if isinstance(links, dict) else {}
    url = str(links.get("html") or "")
    if url:
        return url
    name = str(user.get("username") or "").strip()
    return f"https://unsplash.com/@{name}" if name else ""


def _unsplash_tags(p: dict[str, Any]) -> list[str]:
    """Теги ответа: `alt_description` (одна строка) плюс `tags[].title`, если он есть.
    По ним автоподбор найдёт кадр в следующих роликах, даже если запрос был другим."""
    out: list[str] = []
    alt = str(p.get("alt_description") or p.get("description") or "").strip()
    if alt:
        out.append(alt)
    for t in p.get("tags") or []:
        if isinstance(t, dict):
            title = str(t.get("title") or "").strip()
            if title:
                out.append(title)
    return out


def _unsplash_track(track_url: str, key: str) -> str:
    """Отметить скачивание у Unsplash и получить настоящий адрес файла.

    Это не «необязательный красивый жест»: `download_location` — единственная дверь,
    которой их API считает скачивания, и без этого вызова ключ приложения отзывают.
    Ответ — объект с полем `url` (та же картинка, что в `urls.full`, но уже учтённая).
    Учёт не удался — берём запасной адрес кандидата: кадр важнее счётчика, но
    предупредить об этом надо, потому что повтор сразу заметен по выдаче."""
    # Адрес учёта тоже пришёл в ответе поиска, а не от пользователя: та же проверка,
    # что на скачивании. Отказ — ValueError, его `_file_url` превращает в предупреждение
    # и идёт по прямой ссылке (которая сама проверяется в _download_file).
    reason = unsafe_url_reason(track_url)
    if reason:
        raise ValueError(reason)
    params = urllib.parse.urlencode({"utm_source": APP_NAME.lower(),
                                     "utm_medium": "referral"})
    url = track_url + ("&" if "?" in track_url else "?") + params
    data = _get_json(http_req(url, headers=_headers("unsplash", key, accept="application/json")))
    return str(data.get("url") or "")


def _openverse_search(query: str, n: int, orient: str) -> list[dict[str, Any]]:
    """Openverse: ключ НЕ нужен. Берём только лицензии, разрешающие коммерческое
    использование и изменение (`license_type=commercial,modification`) — заказываем
    фильтр у API и повторно проверяем ответ у себя (`_openverse_ok`): полагаться на
    параметр в одиночку нельзя, а неверная лицензия тут — юридический риск, а не
    неудобство."""
    params: dict[str, Any] = {"q": query, "page_size": max(1, min(20, n)),
                              "license_type": "commercial,modification",
                              "mature": "false"}
    aspect = OPENVERSE_ASPECT.get(orient)
    if aspect:
        params["aspect_ratio"] = aspect
    data = _get_json(http_req("https://api.openverse.org/v1/images/?"
                              + urllib.parse.urlencode(params),
                              headers=_openverse_headers()))
    out: list[dict[str, Any]] = []
    for p in data.get("results") or []:
        if not isinstance(p, dict):
            continue
        c = _openverse_photo(p)
        if c:
            out.append(c)
    return out


# Ориентация Openverse: их `aspect_ratio` — tall/wide/square.
OPENVERSE_ASPECT = {"portrait": "tall", "landscape": "wide", "square": "square"}

# Лицензии Creative Commons, подходящие для коммерческой вставки. У Openverse ответ
# приходит полем `license` (иногда кратко, `cc0`/`by`/`by-sa`, иногда адресом) и
# отдельными `license_version`. ND/NC отвергаем ЛЮБОЙ формой записи.
_OPENVERSE_OK = ("cc0", "pdm", "by", "by-sa")
_OPENVERSE_BAD = ("nc", "nd", "sampling", "devnations")


def _openverse_ok(p: dict[str, Any]) -> bool:
    """Разрешены ли лицензией коммерческое использование и изменение.

    NC (некоммерческая) и ND (без производных) для вставки в ролик не годятся, и
    проверяются по СУФФИКСАМ записи (`by-nc`, `by-nd`, `by-nc-sa`, `by-nc-nd`), а не
    по вхождению подстроки: подстрочный поиск «nd» нашёл бы его в «подlnd» и отверг
    бы годную лицензию. `by` и `by-sa` — годятся обе."""
    raw = str(p.get("license") or "").strip().lower()
    lic = raw.rsplit("/", 1)[-1]                 # бывает и полным адресом cc.org
    base = lic.split("-")[0]
    if base not in _OPENVERSE_OK:
        return False
    parts = lic.split("-")[1:]
    # `sampling`/`devnations`/`nc`/`nd` в любом месте записи — отказ.
    return not any(part in _OPENVERSE_BAD for part in parts)


def _openverse_photo(p: dict[str, Any]) -> dict[str, Any] | None:
    """Фото Openverse: качаем `url` (полноразмерный оригинал). Лицензия и автор
    кладутся в кандидата, а оттуда — в файл лицензии рядом с файлом базы."""
    if not _openverse_ok(p):
        return None
    url = str(p.get("url") or "")
    if not url:
        return None
    creator = str(p.get("creator") or p.get("creator_name") or "").strip()
    title = str(p.get("title") or "").strip()
    tags = _tags_of(title)
    return {"provider": "openverse", "id": p.get("id"), "kind": "photo",
            "thumb": str(p.get("thumbnail") or url),
            "width": p.get("width"), "height": p.get("height"),
            "author": creator,
            "author_url": str(p.get("creator_url") or ""),
            "page_url": str(p.get("foreign_landing_url") or p.get("detail_url") or ""),
            "download_url": url,
            "tags": tags,
            "license": _openverse_license(p),
            "source": str(p.get("source") or "")}


def _openverse_license(p: dict[str, Any]) -> str:
    """Лицензия Openverse строкой: имя и версия, а если ответ отдал готовый адрес —
    он. Это то, что уходит в файл лицензии и по чему через год видно условия."""
    raw = str(p.get("license") or "").strip()
    lic = str(p.get("license_url") or "").strip()
    ver = str(p.get("license_version") or "").strip()
    name = raw.rsplit("/", 1)[-1].lower()
    if ver and name and ver not in name:
        name = f"{name}-{ver}"
    if lic:
        return f"{name} ({lic})" if name else lic
    return name.upper() if name else ""


def _openverse_headers() -> dict[str, str]:
    """Заголовки Openverse: их API просит отпечаток приложения, а с контактом
    (`REELSI_STOCK_IDENT`) даёт более высокий лимит запросов. Контакт личный, в коде
    его нет — переменной нет, значит просто анонимный лимит."""
    h = {"User-Agent": f"{APP_NAME} (+{APP_REFERER})"}
    ident = env("STOCK_IDENT") or ""
    if ident:
        h["X-Client-Id"] = ident
    return h


# ---------- Coverr (видео по ключу, за флагом) ----------
# Схема по их документации (api.coverr.co/docs): GET /videos?query=…&page_size=…&urls=true.
# Список кандидатов — в `hits`. Ссылки на файлы отдаются только с `urls=true`, в объекте
# `urls`: `mp4`, `mp4_download` (и низкое `mp4_preview`). В этих ссылках плейсхолдер
# `{token}`, как именно его подставлять, документация не объясняет — такие ссылки не
# качаем (без токена они не работают), кандидат пропускается. Поэтому провайдер
# остаётся за флагом `REELSI_STOCK_COVERR` до проверки на живом ключе.
# Ключ — заголовком `Authorization: Bearer`, а не `api_key` в адресе: адрес попадает в
# тексты ошибок и журналы, ключ в них не должен.


def _coverr_search(key: str, query: str, n: int, size: tuple[int, int]) -> list[dict[str, Any]]:
    """Coverr: поиск видео. `urls=true` обязателен — без него ссылок на файлы в ответе нет."""
    params = {"query": query, "page_size": max(1, min(20, n)), "urls": "true"}
    data = _get_json(http_req("https://api.coverr.co/videos?" + urllib.parse.urlencode(params),
                              headers=_headers("coverr", key, accept="application/json")))
    out: list[dict[str, Any]] = []
    for v in data.get("hits") or []:
        if not isinstance(v, dict):
            continue
        c = _coverr_video(v, size)
        if c:
            out.append(c)
    return out


def _coverr_video(v: dict[str, Any], size: tuple[int, int]) -> dict[str, Any] | None:
    """Видео Coverr: файл — из `_coverr_file` (mp4 без плейсхолдера, не больше потолка).
    Автор и лицензия в документации не описаны: читаем то, что пришло, а если поля нет —
    пишем это прямо, а не выдумываем лицензию."""
    f = _coverr_file(v, size)
    if not f:
        return None
    urls = v.get("urls")
    urls = urls if isinstance(urls, dict) else {}
    creator = v.get("creator")
    creator = creator if isinstance(creator, dict) else {}
    author = str(creator.get("name") or creator.get("full_name") or v.get("author") or "")
    author_url = str(creator.get("portfolio_url") or creator.get("profile_url")
                     or v.get("author_url") or "")
    return {"provider": "coverr", "id": v.get("id"), "kind": "video",
            "thumb": str(v.get("poster") or urls.get("poster") or urls.get("thumbnail")
                         or v.get("thumbnail") or ""),
            "width": f.get("width"), "height": f.get("height"),
            "duration": v.get("duration"),
            "author": author,
            "author_url": author_url,
            "page_url": str(v.get("link") or v.get("page_url") or ""),
            "download_url": str(f.get("url") or ""),
            "tags": _tags_of(v.get("tags")) or _tags_of(v.get("title")),
            "license": str(v.get("license") or "не указана в ответе API — см. страницу видео")}


def _coverr_file(v: dict[str, Any], size: tuple[int, int]) -> dict[str, Any] | None:
    """Файл видео Coverr: `urls.mp4`, иначе `urls.mp4_download`. Только mp4, без `{`
    (нерасшифрованный плейсхолдер токена) и не больше потолка по большей стороне."""
    urls = v.get("urls")
    urls = urls if isinstance(urls, dict) else {}
    _target_h, max_side = size
    w, h = _coverr_size(v)
    if w and h and max(w, h) > max_side:
        return None                       # 4K ради двухсекундной вставки не нужен
    for field in ("mp4", "mp4_download"):
        link = str(urls.get(field) or "")
        if not link or "{" in link or not link.split("?")[0].lower().endswith(".mp4"):
            continue
        return {"url": link, "width": w or None, "height": h or None}
    return None


def _coverr_size(v: dict[str, Any]) -> tuple[int, int]:
    """Размеры кадра Coverr: `max_width`/`max_height` из ответа, иначе `dimensions`,
    иначе пропорции `aspect_ratio`, иначе 16:9 (их типичный кадр). Нужны, чтобы не
    утащить 4K."""
    w, h = _int(v.get("max_width")), _int(v.get("max_height"))
    if w and h:
        return w, h
    dims = v.get("dimensions")
    if isinstance(dims, dict):
        w, h = _int(dims.get("width")), _int(dims.get("height"))
        if w and h:
            return w, h
    ratio = str(v.get("aspect_ratio") or "").strip()
    parts = ratio.replace("x", ":").split(":")
    if len(parts) == 2 and _int(parts[0]) and _int(parts[1]):
        return _int(parts[0]) * 120, _int(parts[1]) * 120
    return 1920, 1080


# ---------- адрес файла и заголовки ----------
def _headers(provider: str, key: str, accept: str) -> dict[str, str]:
    """Заголовки запроса к API провайдера: ключ у каждого в своём месте (Unsplash —
    `Client-ID`, Coverr — `Bearer`). Для адреса файла не используется (см. download)."""
    h: dict[str, str] = {}
    if provider == "unsplash" and key:
        h["Authorization"] = f"Client-ID {key}"
    elif provider == "coverr" and key:
        h["Authorization"] = f"Bearer {key}"
    if accept:
        h["Accept"] = accept
    return h


def _file_url(provider: str, cand: dict[str, Any], url: str, key: str,
              emit: Any = console_emit) -> tuple[str, dict[str, Any]]:
    """Настоящий адрес файла и дополнительные поля для файла лицензии.

    У Unsplash ссылка `links.download_location` — учётная ручка, а не картинка:
    сначала отмечаем скачивание (их условие), потом берём адрес из ответа. Не
    ответила — качаем запасной `urls.full`, но повтор сразу заметен по выдаче, и об
    этом говорит предупреждение. У Openverse оригинал — уже в кандидате, а в лицензию
    добавляются лицензия/источник/теги: это и есть их требование к атрибуции."""
    extra: dict[str, Any] = {}
    if provider == "unsplash":
        # Лицензия Unsplash — часть их условий, а не мелочь: имя её фиксировано.
        extra = {"license": str(cand.get("license") or "")}
        track = str(cand.get("track_url") or "").strip()
        if track:
            try:
                real = _unsplash_track(track, key)
            except ReelsiError:
                raise
            except Exception as e:
                emit("  ⚠ Unsplash: не отметил скачивание ({err}) — "
                     "качаю по прямой ссылке", err=e)
                real = ""
            if real:
                return real, extra
        return url, extra
    if provider == "openverse":
        extra = {"license": str(cand.get("license") or ""),
                 "source": str(cand.get("source") or ""),
                 "tags": _tags_of(cand.get("tags"))}
    elif provider == "coverr":
        extra = {"license": str(cand.get("license") or "")}
    return url, extra


def _get_json(req: urllib.request.Request) -> dict[str, Any]:
    """GET и разбор JSON. HTTP-ошибку не глотаем: её разбирает search (401/403 — про
    ключ, остальное — предупреждение и следующий провайдер)."""
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT_S) as r:
        data = json.load(r)
    return data if isinstance(data, dict) else {}


def _download_file(url: str, path: str, headers: dict[str, str] | None = None) -> None:
    """Файл к себе (хотлинк запрещён условиями Pixabay). Пишем общей атомарной записью
    ядра: оборванная закачка не должна оставить огрызок под именем готового файла —
    «уже скачан» здесь проверяется по наличию файла.

    Адрес приходит в ОТВЕТЕ стока, а не от пользователя: `file:///C:/Windows/win.ini`
    читал бы локальный файл, `http://127.0.0.1`/`http://169.254.169.254` уводил бы
    запрос внутрь машины. Проверка — та же `unsafe_url_reason`, что на пути готового
    ролика (core/aicut/video.py): одна дверь на всех, кто качает по чужому адресу."""
    reason = unsafe_url_reason(url)
    if reason:
        raise ReelsiError(umsg("stock_unsafe_url", f"Сток отдал непригодный адрес: {reason}",
                               reason=reason))
    with urllib.request.urlopen(http_req(url, headers=headers), timeout=DOWNLOAD_TIMEOUT_S) as r:
        def _write(f: IO[Any]) -> None:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)

        _atomic_write(path, _write, mode="wb")


def _write_license(path: str, cand: dict[str, Any], extra: dict[str, Any],
                   kind: str) -> None:
    """Файл лицензии рядом с файлом: сток, id, автор, ссылки, лицензия, дата.

    Через год по нему видно, откуда кадр и на каких условиях он взят. `extra` —
    то, что знает только сеть (`license`/`source`/`tags`), а не сам кандидат: у
    Openverse лицензия приходит в ответе поиска, у Unsplash — в момент скачивания."""
    fmt = os.path.splitext(path)[1] or (".mp4" if kind == "video" else ".jpg")
    stem = os.path.basename(os.path.splitext(path)[0])
    atomic_json_dump(os.path.splitext(path)[0] + ".license.json", {
        "provider": cand.get("provider"), "id": cand.get("id"), "kind": kind,
        "author": cand.get("author") or "", "author_url": cand.get("author_url") or "",
        "page_url": cand.get("page_url") or "", "download_url": cand.get("download_url") or "",
        "license": cand.get("license") or extra.get("license") or "",
        "source": cand.get("source") or extra.get("source") or "",
        "tags": _tags_of(cand.get("tags")) or _tags_of(extra.get("tags")),
        # Атрибуция рядом с самим файлом: у фото-вставки лицензию читают глазами,
        # и строка «файл + автор + лицензия» лежит тут же, а не только в JSON.
        "attribution": (f"{cand.get('author') or '?'} · {cand.get('provider') or '?'}"
                        f" · {stem}{fmt}"),
        "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })


# ---------- упрощение запроса (за стендом, в бой НЕ включено) ----------
# Правило живёт в ядре, чтобы его мог позвать и боевой поиск, и стенд сравнения
# (`tools/stock_compare.py`): сейчас стенд показывает им «короткий» вариант запроса,
# а решение включать ли его в поиск карточки — отдельное.
_SIMPLE_DROP = frozenset("""
a an the and or of to in on at by for with without from into onto over under above below
behind beside between near next around against during after before while as is are was were
this that these those his her its their our your my some any very much many more most less
person people man woman child concept idea symbol representation showing show shows depicts
depicting featuring feature closeup close shot view scene background foreground wallpaper
photo photos image images picture pictures video videos footage stock
""".split())


def simplify_query(query: str) -> str:
    """Короткий вариант запроса для стока: предмет + 1–2 признака (английские слова).

    Правило простое и объяснимое, а не «модель переписала»: убрать служебные слова
    (предлоги, связки, «concept of», «shown on a wooden desk») и оставить первые
    значимые слова — сток ищет по словам, и длинная фраза размывает выдачу. Предмет
    сохраняем на месте, а признаки — до трёх слов всего: длиннее запрос у стока
    начинает матчиться по любому слову, и в выдачу лезет мусор.

    Известные имена-признаки не выкидываем: «cat on the window» -> «cat window»,
    «concept of clean energy» -> «clean energy». Русский запрос чистить нечем
    (словарь английский) — возвращается как есть, схлопнутыми пробелами.

    Замер 2026-10-09 на 18 запросах владельца: короткий вариант не лучше полного и иногда теряет предмет («hands supporting fragile» без «sprout») — в бой не включена.
    """
    words = re.findall(r"[A-Za-z0-9']+", str(query or "").lower())
    keep = [w for w in words if len(w) > 1 and w not in _SIMPLE_DROP]
    if not keep:
        return " ".join(words)
    if len(keep) == 1:
        return keep[0]
    return " ".join(keep[:3])


# ---------- кеш ответов ----------
def _cache_key(provider: str, kind: str, query: str, n: int,
               size: tuple[int, int], orient: str) -> str:
    """Ключ кеша — provider|kind|query|n|формат. Запрос нормализуем регистром: «Cat»
    и «cat» для стока одно и то же, а лимит запросов общий на всех. Формат (размер и
    ориентация) в ключе обязателен: выдача Pexels зависит от ориентации, и поиск в
    16:9 иначе получил бы кандидатов вертикального поиска."""
    return "|".join((provider, kind, query.strip().lower(), str(int(n)),
                     f"{int(size[0])}x{int(size[1])}{orient}"))


def _cache_load() -> dict[str, Any]:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except ReelsiError:
        raise
    except Exception:
        return {}          # файла нет или он битый — кеша просто нет
    return data if isinstance(data, dict) else {}


def _cache_get(provider: str, kind: str, query: str, n: int,
               size: tuple[int, int], orient: str) -> list[dict[str, Any]] | None:
    """Кандидаты из кеша, если запись моложе суток; иначе None (идём в сеть)."""
    with _CACHE_LOCK:
        rec = _cache_load().get(_cache_key(provider, kind, query, n, size, orient))
    if not isinstance(rec, dict):
        return None
    ts, items = rec.get("ts"), rec.get("items")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not isinstance(items, list):
        return None
    if time.time() - ts > CACHE_TTL:
        return None        # старше суток кешировать нельзя — это условие обоих стоков
    return [it for it in items if isinstance(it, dict)]


def _cache_put(provider: str, kind: str, query: str, n: int,
               size: tuple[int, int], orient: str,
               items: list[dict[str, Any]]) -> None:
    """Записать ответ и заодно выбросить протухшее: иначе файл растёт вечно."""
    key = _cache_key(provider, kind, query, n, size, orient)
    with _CACHE_LOCK:
        data = _cache_load()
        data[key] = {"ts": time.time(), "items": items}
        for k, rec in list(data.items()):
            ts = rec.get("ts") if isinstance(rec, dict) else None
            if not isinstance(ts, (int, float)) or time.time() - ts > CACHE_TTL:
                data.pop(k, None)
        try:
            atomic_json_dump(CACHE_PATH, data)
        except ReelsiError:
            raise
        except Exception as e:
            # Кеш — ускорение, а не данные: не записался — поиск просто сходит в сеть
            log.warning("кеш стока не записан (%s): %s", CACHE_PATH, e)


# ---------- мелочи ----------
def _int(v: Any) -> int:
    """Размер из ответа стока числом; «нет», строка или мусор — 0 («неизвестно»)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return int(v)


def _tags_of(v: Any) -> list[str]:
    """Теги стока списком: Pixabay отдаёт их строкой через запятую, но в части
    ответов приходит уже список — принимаем обе формы."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [t.strip() for t in str(v or "").split(",") if t.strip()]


def _slug(cand: dict[str, Any]) -> str:
    """Имя файла — как в add_generated: из запроса карточки (латиница), нечитаемое в
    дефисы. Теги стока сюда не подмешиваем: имя должно быть коротким и узнаваемым.
    `safe_name` — тот же санитайз, что у id: запрос тоже приходит извне (ИИ-модель
    собирает его по ответу провайдера), и разделители пути из него недопустимы."""
    base = str(cand.get("query") or "").strip() or str(cand.get("provider") or "stock")
    slug = re.sub(r"[^\w]+", "-", base.lower()).strip("-")[:48]
    return safe_name(slug, "stock")


def _ext_of(url: str, kind: str) -> str:
    """Расширение по ссылке. Незнакомое (сток отдал что-то новое) — по типу вставки:
    имя файла должно говорить AE, что внутри."""
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".mp4", ".mov", ".webm"):
        return ext
    return ".jpg" if kind == "photo" else ".mp4"


def _desc(cand: dict[str, Any]) -> str:
    """Описание записи индекса: запрос карточки + теги стока — по нему файл найдёт
    автоподбор в следующих роликах. У видео Pexels тегов нет, остаётся запрос."""
    parts = [str(cand.get("query") or "").strip(), " ".join(_tags_of(cand.get("tags")))]
    return " ".join(p for p in parts if p)
