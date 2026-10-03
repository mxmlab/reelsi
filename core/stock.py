# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""core/stock.py — стоки Pexels и Pixabay как источник вставок.

Когда подходящего файла нет ни в своей базе, ни в генерации, кадр берётся со стока:
поиск по тому же запросу карточки, показ кандидатов, скачивание ВЫБРАННОГО файла в
папку базы (`stock/<сток>/`) с файлом лицензии и записью в индекс. Дальше это обычный
файл базы: автоподбор находит его сам, в следующих роликах сток не нужен вовсе.

Решения, которые тут зашиты (и почему именно так):

- **Порядок провайдеров = приоритет**: Pexels первым, Pixabay вторым (решение
  владельца). Envato и Coverr отпадают по своим условиям: у Envato запрещено
  автоматическое скачивание, у Coverr — отдавать их кадры как часть «сервисов монтажа».
- **Хотлинк не годится**: Pixabay его прямо запрещает. Поэтому файл всегда качается к
  себе, а ответы поиска кешируются (обоим стокам это разрешено, ~24 ч) — иначе один
  прогон карточек выбивал бы лимит запросов.
- **Кеш — свой файл состояния**, значит своя переменная `REELSI_STOCK_CACHE`: без неё
  тестовый профиль 5098 писал бы в боевой кеш (правило проекта: у каждого файла
  состояния своя переменная `REELSI_*`).
- **Ориентация и размер — от формата ролика, а не «самый большой файл»**: вставка
  едет в кадр ролика (по умолчанию вертикальный 1080×1920) на 2–3 секунды, 4K тут не
  виден, а вес и декодирование в After Effects растут вчетверо. Ориентацию и целевой
  размер даёт core/frame.py: в горизонтальном ролике нужен горизонтальный кадр.
- **Файл прогоняется через `insertlib.to_ae_media`**: стоки отдают AV1 и `.webp`,
  которые AE не импортирует вовсе (сторожит core/verify_jsx.py).
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
from core.app_meta import console_emit, env, http_req, wrap_emit
from core.applog import get_logger
from core.fileio import _atomic_write, atomic_json_dump
from core.umsg import ReelsiError, umsg

log = get_logger(__name__)

PROVIDERS = ("pexels", "pixabay")       # порядок = приоритет (Pexels первым)
KINDS = ("photo", "video")

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
NO_KEYS_TEXT = ("Не задан ни один ключ стока — впиши ключ Pexels или Pixabay "
                "в настройках ⚙ → Генерация → Стоки")
BAD_CANDIDATE_TEXT = ("Кандидат стока неполный (нет ссылки на файл) — "
                      "обнови поиск и выбери кадр заново")

_CACHE_LOCK = threading.RLock()          # чтение-изменение-запись кеша целиком под локом


def keys() -> dict[str, str]:
    """Ключи стоков из ai_config.json (раздел `stock`), по провайдеру.

    Читаем через тот же `load_ai_config`/`resolve_key`, что и профили ИИ: запись
    вида `env:ИМЯ` означает «взять из окружения», наружу при этом уходит имя
    переменной, а не секрет."""
    from core.aicut.config import load_ai_config, resolve_key
    section = load_ai_config().get("stock")
    section = section if isinstance(section, dict) else {}
    out: dict[str, str] = {}
    for prov in PROVIDERS:
        raw = resolve_key(section.get(prov + "_key"))
        out[prov] = raw.strip() if isinstance(raw, str) else ""
    return out


def has_keys() -> bool:
    """Есть ли ключ хотя бы у одного провайдера. Нужен роуту: отказ «ключей нет»
    должен приходить до запроса в сеть и с подсказкой, куда ключ вписать."""
    return any(keys().values())


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
    prov_keys = keys()
    if not any(prov_keys.values()):
        raise ReelsiError(umsg("stock_no_keys", NO_KEYS_TEXT))
    size = _target_size(fmt)
    orient = frame.orientation(fmt)
    out: list[dict[str, Any]] = []
    for prov in PROVIDERS:
        if len(out) >= n:
            break
        key = prov_keys.get(prov) or ""
        if not key:
            continue                                   # ключа нет — провайдер молча пропускаем
        try:
            out.extend(_provider_search(prov, key, q, kind, n - len(out), size, orient))
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

    Уже скачанный файл возвращается БЕЗ сети: повторный клик по тому же кандидату не
    должен ни жечь чужой лимит запросов, ни плодить копии.
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
    sub = os.path.join(os.path.abspath(dest_dir), "stock", prov)
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{_slug(cand)}-{cand.get('id')}{_ext_of(url, kind)}")
    fresh = not os.path.isfile(path)                    # «уже скачан» — по наличию файла
    if fresh:
        _download_file(url, path)
    from core import insertlib                           # ленивый импорт: numpy/PIL нужны не всем
    path = insertlib.to_ae_media(path, emit=emit)
    if fresh:
        # лицензия — по СТЕМУ файла в базе, то есть после перекодировки
        _write_license(path, cand)
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
    items = (_pexels_search(key, query, kind, n, size, orient) if provider == "pexels"
             else _pixabay_search(key, query, kind, n))
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


def _tags_of(v: Any) -> list[str]:
    """Теги стока списком: Pixabay отдаёт их строкой через запятую, но в части
    ответов приходит уже список — принимаем обе формы."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [t.strip() for t in str(v or "").split(",") if t.strip()]


# ---------- сеть ----------
def _get_json(req: urllib.request.Request) -> dict[str, Any]:
    """GET и разбор JSON. HTTP-ошибку не глотаем: её разбирает search (401/403 — про
    ключ, остальное — предупреждение и следующий провайдер)."""
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT_S) as r:
        data = json.load(r)
    return data if isinstance(data, dict) else {}


def _download_file(url: str, path: str) -> None:
    """Файл к себе (хотлинк запрещён условиями Pixabay). Пишем общей атомарной записью
    ядра: оборванная закачка не должна оставить огрызок под именем готового файла —
    «уже скачан» здесь проверяется по наличию файла."""
    with urllib.request.urlopen(http_req(url), timeout=DOWNLOAD_TIMEOUT_S) as r:
        def _write(f: IO[Any]) -> None:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)

        _atomic_write(path, _write, mode="wb")


def _write_license(path: str, cand: dict[str, Any]) -> None:
    """Файл лицензии рядом с файлом: сток, id, автор, ссылки, дата скачивания. Через
    год по нему видно, откуда кадр и на каких условиях он взят."""
    atomic_json_dump(os.path.splitext(path)[0] + ".license.json", {
        "provider": cand.get("provider"), "id": cand.get("id"),
        "author": cand.get("author") or "", "author_url": cand.get("author_url") or "",
        "page_url": cand.get("page_url") or "", "download_url": cand.get("download_url") or "",
        "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })


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


def _slug(cand: dict[str, Any]) -> str:
    """Имя файла — как в add_generated: из запроса карточки (латиница), нечитаемое в
    дефисы. Теги стока сюда не подмешиваем: имя должно быть коротким и узнаваемым."""
    base = str(cand.get("query") or "").strip() or str(cand.get("provider") or "stock")
    return re.sub(r"[^\w]+", "-", base.lower()).strip("-")[:48] or "stock"


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
