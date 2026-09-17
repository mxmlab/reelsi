# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Генерация видео: каталог моделей, их возможности и проверка запроса.

Главное здесь — VIDEO_MODELS: у каждой модели свой набор длительностей, разрешений,
пропорций и правил на референсы. Поля в интерфейсе режутся под выбранную модель по
этой таблице, а video_check ловит несовместимое ДО оплаченного вызова.
"""
import os, re, json, shutil, subprocess, math, tempfile
import urllib.request, urllib.error
from .config import APP_NAME, APP_REFERER, _profile_dict, apply_profile_headers, load_ai_config, save_ai_config
from core.umsg import umsg
from core.app_meta import console_emit, http_req


# ---- Генерация видео (Seedance 2 и др. на OpenRouter Video API) --------------
# Отдельный, АСИНХРОННЫЙ путь: POST {base_url}/videos -> {id, polling_url},
# дальше GET polling_url пока status != completed, потом скачиваем готовый mp4.
# Общий для всех моделей контракт OpenRouter (2026-07): model+prompt обязательны,
# duration/resolution/aspect_ratio/size/seed/generate_audio — опциональны, картинки-
# кадры идут в frame_images (first_frame/last_frame), референсы стиля/движения — в
# input_references. Мультимодальный вход Seedance («повторяй ритм камеры с Видео 1»)
# делается ДВОЯКО: сам файл — в input_references, а его подпись — и в поле text
# элемента, и (главное) НОМЕРОВАННОЙ строкой в промпт, откуда модель её и читает.
VIDEO_OFF = "__off__"           # active_video: генерация видео выключена (дефолт)
# Что показываем в UI как варианты для НЕИЗВЕСТНОЙ модели (провайдер валидирует сам).
# size/aspect_ratio взаимоисключающи — шлём то, что заполнено.
VIDEO_RESOLUTIONS = ["480p", "720p", "1080p", "2K", "4K"]
VIDEO_ASPECTS = ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "9:21"]
VIDEO_ROLES = ("reference", "first_frame", "last_frame")
VIDEO_PROMPT_SLOTS = ("a", "b")


def resolve_video_prompt_cfg(slot="a", speaker=None):
    """Настройки видео-промпта выбранного слота из профиля спикера.

    Профиль может прийти ключом/label или уже загруженным словарём. Отсутствие
    профиля и пустой слот намеренно равны чистому query: старые профили не требуют
    миграции и карточка не получает неожиданную общую приписку.
    """
    slot = slot or "a"
    if speaker:
        try:
            from core import speakers
            prof = speaker if isinstance(speaker, dict) else speakers.load(speaker)
            if isinstance(prof, dict):
                scfg = (prof.get("video_prompts") or {}).get(slot) or {}
                extra = str(scfg.get("extra") or "").strip()
                if extra:
                    pos = scfg.get("pos")
                    return {"extra": extra,
                            "pos": pos if pos in ("prefix", "suffix") else "suffix"}
        except Exception:
            pass
    return {"extra": "", "pos": "suffix"}


def build_video_prompt(query, slot="a", speaker=None):
    """Собрать запрос карточки и личную приписку на сервере.

    Приписка склеивается пробелом, как у картинок: это один предмет/сцена, а не
    два независимых требования, которые модель может разнести по кадру.
    """
    cfg = resolve_video_prompt_cfg(slot, speaker=speaker)
    parts = [str(query or "").strip()]
    if cfg["extra"]:
        parts.insert(0 if cfg["pos"] == "prefix" else 1, cfg["extra"])
    return " ".join(p for p in parts if p)


def video_insert_duration(duration_sec, model):
    """Длительность видео-вставки: вверх, затем 3…4 с и только по caps модели.

    Карточка хранит исходное окно отдельно: удлинять его ради ограничений модели
    нельзя, иначе монтаж тихо меняет свой тайминг. Неизвестной модели отдаём
    безопасную цель, известную, но без допустимой длины <=4 с — останавливаем до
    запуска VJOB и оплаченного запроса.
    """
    try:
        target = math.ceil(float(duration_sec))
    except (TypeError, ValueError):
        target = 3
    target = max(3, min(4, target))
    caps = video_caps(model)
    if caps is None:
        return target
    allowed = []
    for value in caps.get("durations") or []:
        try:
            duration = int(float(value))
        except (TypeError, ValueError):
            continue
        if duration >= target and duration <= 4:
            allowed.append(duration)
    if allowed:
        return min(allowed)
    raise SystemExit(umsg("video_insert_duration",
        f"Модель «{model}» не умеет ролик длиной {target}–4 с для видео-вставки",
        model=model, target=target))


def video_auto_duration(source_duration, model):
    """Выбрать длину raw-ролика по проверенному исходнику и caps модели.

    Длину округляем вверх: сгенерированный ролик не должен оказаться короче
    исходного. Если исходник длиннее потолка модели, берём её максимум. Для
    отсутствующего/битого probe используем минимальную длину caps.
    """
    caps = video_caps(model)
    vals = [] if not caps else caps.get("durations") or []
    allowed = []
    for value in vals:
        try:
            allowed.append(int(float(value)))
        except (TypeError, ValueError):
            continue
    allowed = sorted(set(x for x in allowed if x > 0))
    if not allowed:
        return 4
    try:
        target = math.ceil(float(source_duration))
    except (TypeError, ValueError):
        target = allowed[0]
    if target <= allowed[0]:
        return allowed[0]
    return next((x for x in allowed if x >= target), allowed[-1])


def _aspect_value(value):
    try:
        a, b = str(value).split(":", 1)
        a, b = float(a), float(b)
        return a / b if a > 0 and b > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def video_auto_aspect(width, height, model):
    """Ближайшая поддерживаемая пропорция к размерам исходника."""
    caps = video_caps(model)
    aspects = list(caps.get("aspect_ratios") or []) if caps else []
    if not aspects:
        return ""
    try:
        ratio = float(width) / float(height)
    except (TypeError, ValueError, ZeroDivisionError):
        return aspects[0]
    if ratio <= 0:
        return aspects[0]
    scored = [(abs(math.log(ratio / v)), i, aspect)
              for i, aspect in enumerate(aspects)
              if (v := _aspect_value(aspect))]
    return min(scored)[2] if scored else aspects[0]


def video_auto_shape(refs, model):
    """Вернуть raw-форму из первого видео, иначе первой картинки.

    Видео без размеров всё ещё является источником длительности, но для aspect
    пропускается; это не превращает отсутствующий probe в ложный 16:9.
    """
    refs = refs or []
    first_video = next((r for r in refs if r.get("kind") == "video"), None)
    first_image = next((r for r in refs if r.get("kind") == "image"), None)
    duration = video_auto_duration(first_video.get("duration") if first_video else None, model)
    source = "fallback"
    shape = None
    for ref, label in ((first_video, "video"), (first_image, "image")):
        if ref and ref.get("w") and ref.get("h"):
            shape = (ref.get("w"), ref.get("h")); source = label
            break
    aspect = video_auto_aspect(*(shape or (None, None)), model) if shape else video_auto_aspect(None, None, model)
    if first_video and first_video.get("duration") is not None:
        source = source if source != "fallback" else "video"
    return {"duration": duration, "aspect_ratio": aspect, "source": source}

# ---- ВСТРОЕННЫЙ каталог главных видео-моделей -------------------------------
# Снят с живого GET {base}/videos/models 2026-08-05. Нужен, чтобы поля страницы
# подстраивались ПОД МОДЕЛЬ сразу, без сетевого запроса: у Veo длины только 4/6/8 и
# две пропорции, у Hailuo единственное разрешение 2K, у Kling — 720p. Живой каталог
# (VIDEO_MODEL_CAPS) всегда главнее — по нему OpenRouter и валидирует запрос;
# встроенный добавляет то, чего в каталоге НЕТ ВООБЩЕ: референсы (r2v) и их лимиты
# (взяты из документации вендоров — best-effort, схемой OpenRouter не описаны).
_AR_SEEDANCE = ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"]
_NOTE_SEEDANCE = ("Мультимодальный r2v: до 9 фото и 3 видео-референсов, видео СУММАРНО "
                  "не длиннее 15с. Что взять с референса — пишется в промпте тегом "
                  "@image1/@video1. Первый/последний кадр — только ФОТО и не вместе "
                  "с референсами.")
_NOTE_VEO = ("Только 4, 6 или 8 секунд и только 16:9 / 9:16. Референсы — до 3 ФОТО "
             "(видео как референс не принимает) и не вместе с кадром.")
_NOTE_HAILUO = ("Единственное разрешение — 2K. Первый/последний кадр — фото. "
                "Референсы (файлом) каталогом не заявлены: подпись уйдёт в промпт.")
_NOTE_KLING = ("Только 720p и 16:9 / 9:16 / 1:1, seed не поддерживает. Первый/последний "
               "кадр — фото. Референсы (файлом) каталогом не заявлены.")
# Форматы картинок, которые модель принимает. Схемой OpenRouter не описаны — набраны
# из документации вендоров и практики. `strict` = проверено отказом (Veo НЕ берёт
# .webp — поймано пользователем 2026-08-05), такое блокируем; где уверенности нет,
# только предупреждаем на карточке: ложный запрет хуже лишнего предупреждения.
_IMG_ALL = ["jpeg", "png", "webp", "gif", "bmp", "tiff"]
_IMG_JPG_PNG = ["jpeg", "png"]
VIDEO_MODELS = {
    "bytedance/seedance-2.0": {
        "label": "Seedance 2.0 — мультимодальная (фото + видео-референсы)",
        "durations": list(range(4, 16)),
        "resolutions": ["480p", "720p", "1080p", "4K"], "aspect_ratios": _AR_SEEDANCE,
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": True,
        "references": True, "ref_images": 9, "ref_videos": 3, "ref_video_total_s": 15.0,
        "image_formats": _IMG_ALL, "image_formats_strict": False,
        "note": _NOTE_SEEDANCE},
    "bytedance/seedance-2.0-fast": {
        "label": "Seedance 2.0 Fast — то же, дешевле (до 720p)",
        "durations": list(range(4, 16)),
        "resolutions": ["480p", "720p"], "aspect_ratios": _AR_SEEDANCE,
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": True,
        "references": True, "ref_images": 9, "ref_videos": 3, "ref_video_total_s": 15.0,
        "image_formats": _IMG_ALL, "image_formats_strict": False,
        "note": _NOTE_SEEDANCE},
    "google/veo-3.1": {
        "label": "Veo 3.1 — Google, сильный звук и физика",
        "durations": [4, 6, 8], "resolutions": ["720p", "1080p", "4K"],
        "aspect_ratios": ["16:9", "9:16"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": True,
        "references": None, "ref_images": 3, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": True,
        "note": _NOTE_VEO},
    "google/veo-3.1-fast": {
        "label": "Veo 3.1 Fast — быстрее и дешевле",
        "durations": [4, 6, 8], "resolutions": ["720p", "1080p", "4K"],
        "aspect_ratios": ["16:9", "9:16"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": True,
        "references": None, "ref_images": 3, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": True,
        "note": _NOTE_VEO},
    "google/veo-3.1-lite": {
        "label": "Veo 3.1 Lite — самая дешёвая из Veo (до 1080p)",
        "durations": [4, 6, 8], "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": True,
        "references": None, "ref_images": 3, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": True,
        "note": _NOTE_VEO},
    "minimax/hailuo-3": {
        "label": "MiniMax Hailuo 3 — 2K, длина 5–15с",
        "durations": list(range(5, 16)), "resolutions": ["2K"],
        "aspect_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": False,
        "references": None, "ref_images": 0, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": False,
        "note": _NOTE_HAILUO},
    "kwaivgi/kling-v3.0-pro": {
        "label": "Kling 3.0 Pro — 720p, длина 3–15с",
        "durations": list(range(3, 16)), "resolutions": ["720p"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": False,
        "references": None, "ref_images": 0, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": False,
        "note": _NOTE_KLING},
    "kwaivgi/kling-v3.0-std": {
        "label": "Kling 3.0 Std — то же, дешевле",
        "durations": list(range(3, 16)), "resolutions": ["720p"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "frame_images": ["first_frame", "last_frame"], "audio": True, "seed": False,
        "references": None, "ref_images": 0, "ref_videos": 0, "ref_video_total_s": None,
        "image_formats": _IMG_JPG_PNG, "image_formats_strict": False,
        "note": _NOTE_KLING},
}
VIDEO_MODEL_HINTS = list(VIDEO_MODELS)     # порядок в выпадашке = порядок здесь

# Каталог видео-моделей (GET {base}/videos/models) -> сырые записи по id.
# Заполняется кнопкой «Проверить возможности» (api.video_models). По нему gen_video
# решает, ЧТО модель реально принимает: supported_parameters + типы входов
# (картинка/видео-референс). Пусто = список не подтянут: шлём как есть, провайдер
# сам отвергнет лишнее. Документированная схема input_references — ТОЛЬКО картинки
# (image_url), поля text на референсе нет; видео-референс и подпись-к-референсу
# схемой НЕ описаны — поэтому подписи всегда вплетаем в промпт (_video_prompt).
VIDEO_MODEL_CAPS = {}
# Ключ провайдера, для которого загружен VIDEO_MODEL_CAPS:
# (provider, base_url без хвостового «/» в нижнем регистре). None = не привязан/пуст.
VIDEO_CATALOG_KEY = None


def _catalog_key(prof):
    """Ключ источника каталога: (provider, base_url без хвостового «/» в нижнем регистре).
    Принимает словарь профиля или кортеж (provider, base_url). Пусто/невалидно -> None."""
    if not prof:
        return None
    if isinstance(prof, tuple) and len(prof) == 2:
        p, b = prof
        p_str = str(p or "").strip().lower()
        b_str = str(b or "").strip().rstrip("/").lower()
        return (p_str, b_str) if (p_str or b_str) else None
    if isinstance(prof, dict):
        p_str = str(prof.get("provider") or "").strip().lower()
        b_str = str(prof.get("base_url") or "").strip().rstrip("/").lower()
        return (p_str, b_str) if (p_str or b_str) else None
    return None


def _active_video_profile():
    """Разрешить активный видео-профиль с учётом возможного мока на фасаде aicut."""
    import sys
    _mod = sys.modules.get("core.aicut")
    _resolver = getattr(_mod, "resolve_video_profile", resolve_video_profile) if _mod else resolve_video_profile
    return _resolver()


# Маркер «профиль проверен, его нет»: позволяет video_model_list передать
# результат в video_caps без повторного чтения ai_config на каждой модели списка.
_NO_PROFILE = object()


def _catalog_matches(prof=None):
    """Совпадает ли ключ текущего каталога в памяти с профилем prof (или active_video).
    Если в месте чтения профиль не передан — пробуем _active_video_profile().
    Если профиля нет вовсе (CLI/изолированный тест): если ключ не был задан (каталог
    заполнен вручную/без ключа), разрешаем чтение; если ключ был задан под конкретного
    провайдера — без профиля чужой каталог не используем."""
    if not VIDEO_MODEL_CAPS:
        return False
    if prof is None:
        prof = _active_video_profile()
    if prof is None or prof is _NO_PROFILE:
        return VIDEO_CATALOG_KEY is None
    key = _catalog_key(prof)
    return key is not None and VIDEO_CATALOG_KEY == key


def set_video_catalog(key, entries):
    """Обновить глобальный каталог видео-моделей под указанный ключ источника.
    Объект словаря один на процесс: VIDEO_MODEL_CAPS не переприсваивается
    (сохраняется идентичность для импортёров aicut.VIDEO_MODEL_CAPS), а очищается
    и наполняется заново по правилам ensure_video_catalog (id/slug в нижнем регистре)."""
    global VIDEO_CATALOG_KEY
    VIDEO_MODEL_CAPS.clear()
    VIDEO_CATALOG_KEY = _catalog_key(key)
    if isinstance(entries, dict):
        for k, m in entries.items():
            if isinstance(m, dict):
                mid = (m.get("id") or m.get("slug") or k or "").lower()
            else:
                mid = str(k or "").lower()
            if mid:
                VIDEO_MODEL_CAPS[mid] = m
    elif isinstance(entries, (list, tuple)):
        for m in entries:
            if isinstance(m, dict):
                mid = (m.get("id") or m.get("slug") or "").lower()
                if mid:
                    VIDEO_MODEL_CAPS[mid] = m


def video_model_entry(model, prof=None):
    """Сырая запись каталога по id модели (или {}). Регистронезависимо.
    Возвращает запись, только если каталог в памяти соответствует профилю prof."""
    if not _catalog_matches(prof):
        return {}
    return VIDEO_MODEL_CAPS.get((model or "").lower()) or {}


def _caps_refs(entry):
    """Заявляет ли каталог input_references (референсы стиля/движения/персонажа) как
    ВХОД. Каталог OpenRouter (2026-07) перечисляет их СТРУКТУРНО: у Seedance 2.0
    есть `supported_frame_images` (первый/последний кадр), но поля про input_references
    в записи НЕТ вовсе. Поэтому: явный ключ -> его значение; иначе None = «каталог не
    подтверждает» (multimodal reference-to-video есть только в текстовом description)."""
    for k in ("supported_input_references", "input_references", "supported_references"):
        if k in entry:
            v = entry[k]
            return bool(v) if not isinstance(v, (list, tuple)) else len(v) > 0
    return None


def video_caps(model, prof=None):
    """Нормализованные возможности модели: ЖИВОЙ каталог + встроенный VIDEO_MODELS.
    None — модель неизвестна обоим (шлём как есть, провайдер сам отвергнет лишнее).

    Живой каталог главнее: по нему OpenRouter валидирует запрос (supported_durations/
    resolutions/aspect_ratios/sizes/frame_images — списки допустимых значений,
    generate_audio/seed — умеет ли). Встроенный докладывает то, чего в каталоге нет
    вовсе: референсы r2v и их лимиты (ref_images/ref_videos/ref_video_total_s)."""
    mid = (model or "").strip().lower()
    e = video_model_entry(mid, prof=prof)
    b = VIDEO_MODELS.get(mid) or {}
    if not e and not b:
        return None

    def _lst(key):
        v = e.get("supported_" + key)
        if isinstance(v, (list, tuple)) and v:
            return [str(x) for x in v]
        return [str(x) for x in (b.get(key) or [])]

    def _flag(ekey, bkey):
        return bool(e[ekey]) if (e and e.get(ekey) is not None) else bool(b.get(bkey))

    refs = _caps_refs(e) if e else None
    if refs is None:
        refs = b.get("references")             # каталог молчит — что знаем сами (None ок)
    return {"model": mid,
            "label": b.get("label") or "",
            "note": b.get("note") or "",
            "known": bool(b),                  # есть во встроенном каталоге
            "listed": bool(e),                 # подтверждена живым каталогом
            "durations": _lst("durations"),
            "resolutions": _lst("resolutions"),
            "aspect_ratios": _lst("aspect_ratios"),
            "sizes": _lst("sizes"),
            "frame_images": _lst("frame_images"),
            "audio": _flag("generate_audio", "audio"),
            "seed": _flag("seed", "seed"),
            "references": refs,
            "ref_images": b.get("ref_images"),
            "ref_videos": b.get("ref_videos"),
            "ref_video_total_s": b.get("ref_video_total_s"),
            "image_formats": list(b.get("image_formats") or []),
            "image_formats_strict": bool(b.get("image_formats_strict")),
            "raw": e}


# Формат картинки: как его называет ffprobe/Content-Type/расширение -> наш канон.
_IMG_FMT = {"mjpeg": "jpeg", "jpeg": "jpeg", "jpg": "jpeg", "png": "png", "apng": "png",
            "webp": "webp", "gif": "gif", "bmp": "bmp", "tiff": "tiff", "tif": "tiff",
            "avif": "avif", "heif": "heif", "heic": "heif"}


def image_format_of(ref):
    """Формат картинки-референса ('jpeg'/'png'/'webp'/…) или '' — не определили.
    Сначала то, что увидел ffprobe/HEAD (ссылка может быть вообще без расширения),
    потом расширение."""
    for src in (ref.get("fmt"), ref.get("codec"),
                str(ref.get("ctype") or "").split("/")[-1]):
        f = _IMG_FMT.get(str(src or "").lower())
        if f:
            return f
    ext = str(ref.get("url") or "").split("?")[0].split("#")[0].rsplit(".", 1)
    return _IMG_FMT.get(ext[-1].lower(), "") if len(ext) == 2 else ""


def ensure_video_catalog(prof, emit=None):
    """Подтянуть каталог видео-моделей провайдера, если его ещё нет в памяти
    (бесплатный GET). Нужен, чтобы предполёт знал ограничения модели ДАЖЕ когда
    страницу не открывали (CLI, свежий процесс) — и чтобы не улететь запросом на
    модель, которой у провайдера нет вовсе. Не отдался — молча работаем на встроенном."""
    global VIDEO_CATALOG_KEY
    key = _catalog_key(prof)
    if VIDEO_MODEL_CAPS and VIDEO_CATALOG_KEY == key:
        return
    VIDEO_MODEL_CAPS.clear()
    VIDEO_CATALOG_KEY = None
    if not prof or not key or not prof.get("base_url"):
        return
    try:
        base = prof["base_url"].rstrip("/")
        headers = {"Authorization": "Bearer " + (prof.get("api_key") or "")}
        if prof.get("provider") == "openrouter":
            headers["HTTP-Referer"] = APP_REFERER
            headers["X-Title"] = APP_NAME
        headers = apply_profile_headers(headers, prof)
        req = http_req(base + "/videos/models", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except Exception:
        return
    entries = data.get("data") or data.get("models") or []
    set_video_catalog(key, entries)
    if emit and VIDEO_MODEL_CAPS:
        emit("  видео: каталог провайдера — {count} моделей", count=len(VIDEO_MODEL_CAPS))


def video_model_list(prof=None):
    """Список моделей для выпадашки на странице «Видео»: встроенные главные (в своём
    порядке) + всё, что вернул живой каталог. Каждой — её caps, чтобы UI подстраивал
    поля СРАЗУ, без сетевого запроса."""
    if prof is None:
        prof = _active_video_profile() or _NO_PROFILE
    caps_dict = VIDEO_MODEL_CAPS if _catalog_matches(prof) else {}
    ids = list(VIDEO_MODELS) + [m for m in sorted(caps_dict)
                                if m not in VIDEO_MODELS]
    out = []
    for mid in ids:
        c = video_caps(mid, prof=prof) or {}
        c.pop("raw", None)                     # в raw длинный description — UI не нужен
        out.append({"id": mid, "label": c.get("label") or "",
                    "builtin": mid in VIDEO_MODELS, "caps": c})
    return out


_PROBE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Reelsi/1.0"


def _head_media(url, timeout=12):
    """Content-Type и размер по ссылке (HEAD, при отказе — GET первых байт) ->
    (тип, байты). Отличает ПРЯМУЮ ссылку на файл от ссылки на страницу: провайдер
    качает URL сам и на HTML отвечает отказом уже после отправки запроса."""
    for req in (urllib.request.Request(url, method="HEAD",
                                       headers={"User-Agent": _PROBE_UA}),
                # часть хостингов HEAD не умеет — тогда крошечный GET
                urllib.request.Request(url, headers={"User-Agent": _PROBE_UA,
                                                     "Range": "bytes=0-1"})):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                try:
                    size = int(r.headers.get("Content-Length") or 0)
                except ValueError:
                    size = 0
                return ct, size
        except Exception:
            continue
    return "", 0


def probe_media(url, timeout=25):
    """Что лежит по ГОТОВОЙ ссылке -> {kind, duration, width, height, ctype, size}
    ({} — не вышло). kind: image | video | audio | page (ссылка на СТРАНИЦУ, не файл).

    Зачем: (а) тип референса нельзя брать из расширения — у ссылок его часто нет
    вовсе, а фото и видео уходят в РАЗНЫЕ поля запроса; (б) Seedance режет r2v по
    СУММАРНОЙ длине видео-референсов (>15с -> 400 «video total duration»), и поймать
    это надо до отправки; (в) ссылка на страницу галереи вместо файла — самая частая
    ошибка, а провайдер о ней сообщит только после отправки. Не смогли (нет ffprobe,
    сайт не отдаёт) — не блокируем."""
    ctype, size = _head_media(url)
    by_ct = ("image" if ctype.startswith("image/") else
             "video" if ctype.startswith("video/") else
             "audio" if ctype.startswith("audio/") else
             "page" if (ctype.startswith("text/") or "html" in ctype) else "")
    if by_ct == "page":
        # HTML по ссылке — это страница, а не файл: ffprobe тут уже не нужен
        return {"kind": "page", "duration": 0.0, "width": 0, "height": 0,
                "codec": "", "ctype": ctype, "size": size}
    try:
        pr = subprocess.run(["ffprobe", "-v", "error", "-of", "json",
                             # без человеческого UA часть сайтов (Wikimedia) отдаёт 400
                             "-user_agent", "Mozilla/5.0 (compatible; Reelsi/1.0)",
                             "-show_entries", "format=duration:stream=width,height,codec_name",
                             url], capture_output=True, text=True, timeout=timeout)
        d = json.loads(pr.stdout or "{}")
    except Exception:
        d = {}
    streams = d.get("streams") or []
    if not streams:
        # ffprobe не открыл (нет его, сайт не отдал) — берём тип из Content-Type, а
        # если и его нет, НЕ выдумываем: пустой ответ честнее, чем «картинка», иначе
        # видео уйдёт в image_url и запрос отвергнут
        if by_ct:
            return {"kind": by_ct, "duration": 0.0, "width": 0, "height": 0,
                    "codec": "", "ctype": ctype, "size": size}
        return {}
    # потоки могут идти в любом порядке (AVI/MKV — аудио первым): размеры/кодек берём
    # с ПЕРВОГО ВИДЕО-потока, иначе они уедут в audio-поток и файл станет «картинкой»
    st = next((s for s in streams if (s.get("codec_type") or "").lower() == "video"), streams[0])
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    codec = str(st.get("codec_name") or "").lower()
    # ffprobe и картинку показывает видеопотоком — отличаем по кодеку и длительности
    is_img = codec in ("png", "mjpeg", "jpeg", "webp", "gif", "bmp", "tiff", "avif", "heif")
    if not st.get("width") and not st.get("height") and dur > 0:
        kind = "audio"                         # звук: кадра нет вовсе (mp3/wav/m4a)
    else:
        kind = "image" if (is_img or dur <= 0.35) else "video"
    info = {"kind": kind, "duration": round(dur, 2) if dur else 0.0,
            "width": st.get("width") or 0, "height": st.get("height") or 0,
            "codec": codec, "ctype": ctype, "size": size}
    # формат картинки нужен клиенту: у Veo .webp не принимается, и сказать об этом
    # надо на карточке, а не после отправки
    info["fmt"] = image_format_of(info) if kind == "image" else ""
    return info


def resolve_refs(refs, emit=None):
    """Досмотреть референсы ПЕРЕД отправкой: у каждой ссылки выясняем реальный тип и
    длину (probe_media) и пишем их в сам ref. Без этого тип берётся из расширения —
    а у ссылок его часто нет, и видео уходило бы в image_url (гарантированный отказ).
    Уже досмотренные (ref['probed'] == url) не трогаем — ffprobe не бесплатный."""
    emit = emit or (lambda *a, **k: None)
    for r in refs or []:
        url = str(r.get("url") or "").strip()
        if not url or r.get("probed") == url:
            continue
        info = probe_media(url)
        r["probed"] = url
        if not info:
            emit("  видео: не удалось прочитать «{url}» — считаю по расширению", url=url[:60])
            continue
        r["kind"] = info["kind"]
        if info["kind"] == "video":
            r["duration"] = info.get("duration") or 0
    return refs



def video_check(model, opts, refs, caps=None, probe=False, prompt=None, prof=None):
    """ПРЕДПОЛЁТНАЯ проверка запроса -> список проблем человеческим текстом (пусто =
    можно слать). Смысл: 400 от провайдера приходит на чужом языке и через полминуты
    ожидания, а половина отказов — правила, которые видно заранее.

    probe=True — досмотреть видео-референсы ffprobe'ом (суммарная длина r2v)."""
    caps = caps or video_caps(model, prof=prof)
    refs = refs or []
    opts = opts or {}
    bad = []
    # Ссылки, ведущие не на файл, отсекаем ДО всего: провайдер скачивает URL сам и
    # на HTML-странице (типовая ошибка — адрес галереи вместо картинки) отвечает
    # отказом уже после отправки.
    for r in refs:
        url = str(r.get("url") or "").strip()
        if str(r.get("kind") or "") == "page":
            bad.append(f"«{url[:60]}» — это ссылка на страницу, а не "
                       f"на файл: нужна ПРЯМАЯ ссылка (оканчивается на .jpg/.png/.mp4)")
        elif not url.lower().startswith("https://"):
            # раньше такой референс молча выбрасывался, а генерация всё равно шла:
            # юзер платил за ролик, в котором его референса нет
            bad.append(f"«{url[:60]}» — нужна https-ссылка: провайдер качает файл сам "
                       f"и локальные пути не принимает")
    if prompt is not None and not str(prompt).strip():
        # промпт обязателен у всех моделей, одни референсы не запрос
        bad.append("нужен текст запроса — одних референсов провайдеру мало")
    if not caps:
        # Модель незнакомая. Если каталог провайдера уже подтянут под этот профиль,
        # а её там нет — это не видео-модель (у юзера в профиле лежала несуществующая)
        # и запрос уйдёт в никуда; каталога нет или он от другого провайдера — пусть решает провайдер.
        if _catalog_matches(prof) and (model or "").strip().lower() not in VIDEO_MODEL_CAPS:
            bad.append(f"«{model}» — не видео-модель этого провайдера: выбери модель из списка")
        return bad

    def _in(val, allowed, what):
        if val in (None, "", "auto") or not allowed:
            return
        if str(val) not in allowed:
            bad.append(f"{what} «{val}» модель не принимает — можно: "
                       + ", ".join(allowed[:12]))
    _in(opts.get("duration"), caps["durations"], "длину")
    _in(opts.get("resolution"), caps["resolutions"], "разрешение")
    _in(opts.get("aspect_ratio"), caps["aspect_ratios"], "пропорции")
    _in(opts.get("size"), caps["sizes"], "размер")

    frames = [r for r in refs if (r.get("role") or "reference") != "reference"]
    plain = [r for r in refs if (r.get("role") or "reference") == "reference"]
    # Кадр и референсы — РАЗНЫЕ режимы (i2v и r2v). OpenRouter при обоих молча берёт
    # кадр, ByteDance отвечает ошибкой «first_frame + reference_images conflict».
    if frames and plain:
        bad.append("первый/последний кадр и референсы — разные режимы, вместе не "
                   "отправляются: оставь что-то одно")
    roles = {r.get("role") for r in frames}
    if "last_frame" in roles and "first_frame" not in roles:
        bad.append("последний кадр без первого не принимается — добавь первый кадр")
    for role in ("first_frame", "last_frame"):
        if role in roles and caps["frame_images"] and role not in caps["frame_images"]:
            bad.append(f"эта модель не принимает {'первый' if role == 'first_frame' else 'последний'} кадр")
    for role, word in (("first_frame", "Первый"), ("last_frame", "Последний")):
        if len([r for r in frames if r.get("role") == role]) > 1:
            bad.append(f"{word.lower()} кадр может быть только один")

    # Аудио-референс (@audio1 у Seedance) существует у вендора, но схемой OpenRouter
    # не передаётся — ушёл бы как «картинка» и получил отказ. Говорим честно.
    if [r for r in refs if str(r.get("kind") or "") == "audio"]:
        bad.append("аудио-референс через этот API не передать — звук задаётся словами "
                   "в промпте или берётся с видео-референса")
    vids = [r for r in plain if _ref_is_video(r)]
    imgs = [r for r in plain if not _ref_is_video(r) and str(r.get("kind") or "") != "audio"]
    if [r for r in frames if _ref_is_video(r)]:
        # то самое «видео поставил первым кадром, а оно не принимается»: кадр —
        # это КАРТИНКА (frame_images: image_url). Видео идёт только в референсы.
        bad.append("кадром может быть только фото — видео добавляется как «референс» "
                   "(движение/камера), а не как первый/последний кадр")
    if plain and caps.get("references") is False:
        bad.append("референсы (файлом) эта модель не принимает — останется только подпись в промпте")
    if vids and caps.get("ref_videos") == 0:
        bad.append("видео-референс эта модель не принимает — фото можно, видео нет "
                   "(для видео-референсов бери Seedance 2.0)")
    if caps.get("ref_images") and len(imgs) > caps["ref_images"]:
        bad.append(f"фото-референсов не больше {caps['ref_images']}, а их {len(imgs)}")
    if caps.get("ref_videos") and len(vids) > caps["ref_videos"]:
        bad.append(f"видео-референсов не больше {caps['ref_videos']}, а их {len(vids)}")

    # Формат картинки. Veo не берёт .webp (поймано пользователем) — такое блокируем;
    # где данных о модели меньше, только предупреждаем (video_warnings).
    fmts = caps.get("image_formats") or []
    if fmts and caps.get("image_formats_strict"):
        for r in refs:
            f = image_format_of(r) if not _ref_is_video(r) else ""
            if f and f not in fmts:
                bad.append(f"«.{f}» эта модель не принимает — нужен "
                           f"{' или '.join('.' + x for x in fmts)}: сконвертируй картинку")

    lim = caps.get("ref_video_total_s")
    if lim and vids:
        total, unknown = 0.0, False
        for r in vids:
            d = r.get("duration")
            if not d and probe:
                d = (probe_media(r.get("url") or "") or {}).get("duration") or 0
                r["duration"] = d              # запомним: пригодится и в логе
            if d:
                total += float(d)
            else:
                unknown = True
        if total > lim + 0.2:                  # +0.2: провайдер меряет с точностью до кадра
            bad.append(f"видео-референсы суммарно {total:.1f}с — модель берёт не больше "
                       f"{lim:g}с; обрежь или убери лишнее")
        elif unknown:
            pass                               # длину не узнали — не выдумываем ошибку
    return bad


def video_warnings(model, refs, caps=None, prompt=None, prof=None):
    """Не отказ, но стоит сказать вслух ДО оплаты: формат картинки под вопросом
    (вендор его не заявлял, но и отказом мы это не видели) и @-тег в промпте без
    приложенного файла. Блокировать такое нельзя — данные неточные."""
    caps = caps or video_caps(model, prof=prof)
    out = []
    if caps:
        fmts = caps.get("image_formats") or []
        if fmts and not caps.get("image_formats_strict"):
            for r in refs or []:
                f = image_format_of(r) if not _ref_is_video(r) else ""
                if f and f not in fmts:
                    out.append(f"«.{f}» эта модель может не принять (обычно берёт "
                               f"{', '.join('.' + x for x in fmts)}) — если откажет, "
                               f"сконвертируй в .jpg")
    for t in dangling_tags(prompt or "", refs):
        out.append(f"в запросе есть {t}, а такого референса нет — модель его ни с чем "
                   f"не свяжет")
    return out


def _ref_is_video(r):
    """Референс — видео? Сначала явный kind (его ставит ffprobe), потом расширение."""
    k = str((r or {}).get("kind") or "")
    if k:
        return k.startswith("video")
    return is_video_url((r or {}).get("url"))


def resolve_video_profile():
    """Профиль-«режиссёр» для генерации видео. None = выключено (дефолт).
    Нужен OpenRouter (или совместимый) с видео-эндпоинтом POST {base_url}/videos;
    anthropic/lmstudio видео не генерят."""
    cfg = load_ai_config()
    name = cfg.get("active_video") or VIDEO_OFF
    prof = cfg["profiles"].get(name)
    if name == VIDEO_OFF or prof is None:
        return None
    return _profile_dict(name, prof)


def video_model_cfg():
    """Какой моделью генерим: общий выбор из ⚙ (ai_config.video_model) -> модель
    профиля -> первая встроенная. Профиль остаётся только провайдером и ключом."""
    cfg = load_ai_config()
    prof = cfg["profiles"].get(cfg.get("active_video") or "") or {}
    return str(cfg.get("video_model") or prof.get("model") or VIDEO_MODEL_HINTS[0]).strip()


def video_resolution_cfg(model=None, prof=None):
    """Разрешение генерации видео из общего ai_config.video_resolution.

    Пусто/нет = провайдер решает сам (""). Для ИЗВЕСТНОЙ модели с непустыми caps
    отдаём сохранённое ТОЛЬКО если оно поддерживается (сравнение без учёта регистра,
    наружу — КАНОНИЧНОЕ значение из caps); устаревшее (модель сменилась и не
    поддерживает) — НЕ отправляем, возвращаем "". Для НЕИЗВЕСТНОЙ модели (caps нет)
    непустое сохранённое пропускаем: провайдер сам решит, валидно оно или нет.
    """
    cfg = load_ai_config()
    saved = str(cfg.get("video_resolution") or "").strip()
    if not saved:
        return ""
    caps = video_caps(model, prof=prof)
    if caps is not None and caps.get("resolutions"):
        low = {str(r).lower(): str(r) for r in caps["resolutions"]}
        return low.get(saved.lower()) or ""
    return saved


def video_resolution_sync(model=None, prof=None):
    """Переоценить сохранённое разрешение против актуальных caps модели и СБРОСИТЬ
    устаревшее на сервере. Живой каталог мог измениться ПОСЛЕ сохранения (смена модели
    или свежий /videos/models убрал значение) — иначе осталось бы скрытое устаревшее
    значение, которое UI показывал бы как валидное. Возвращает итоговое разрешение."""
    cfg = load_ai_config()
    saved = str(cfg.get("video_resolution") or "").strip()
    if not saved:
        return ""
    caps = video_caps(model, prof=prof)
    if caps is not None and caps.get("resolutions"):
        if saved.lower() not in {str(r).lower() for r in caps["resolutions"]}:
            cfg["video_resolution"] = ""
            try:
                save_ai_config(cfg)
            except Exception:
                pass
    return video_resolution_cfg(model, prof=prof)


_VIDEO_URL_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def is_video_url(url):
    """Ссылка ведёт на видео (по расширению, query-строку отбрасываем). Иначе — фото."""
    u = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return u.endswith(_VIDEO_URL_EXTS)


def _video_ref_item(url, caption="", frame_type=None, kind=None):
    """Один элемент input_references или frame_images по ГОТОВОЙ HTTPS-ссылке.
    OpenRouter Video API качает референс по URL — локальные файлы/data-URI он
    отвергает («Only HTTPS URLs are allowed», проверено на seedance-2.0). Видео ->
    video_url, картинка -> image_url. caption дублируется в text (страховка)."""
    is_vid = (kind == "video") if kind else is_video_url(url)
    if is_vid:
        item = {"type": "video_url", "video_url": {"url": url}}
    else:
        item = {"type": "image_url", "image_url": {"url": url}}
    if frame_type:
        item["frame_type"] = frame_type
    if caption:
        item["text"] = caption
    return item


def video_ref_tag(kind_is_video, img_n, vid_n):
    """@-тег референса по официальному синтаксису Seedance 2.0: изображения ->
    @image1..@image9, видео -> @video1..@video3 (модель связывает файл с промптом
    именно по этому тегу; «Референс N» она игнорирует)."""
    return f"@video{vid_n}" if kind_is_video else f"@image{img_n}"


def video_ref_tags(refs):
    """Список @-тегов для refs в их порядке (нумерация по ТИПУ — как ждёт Seedance).
    Отдаётся и в UI (показать тег на карточке), и в _video_prompt (вплести в текст)."""
    tags, img_n, vid_n = [], 0, 0
    for r in refs or []:
        is_vid = _ref_is_video(r)
        if is_vid:
            vid_n += 1
        else:
            img_n += 1
        tags.append(video_ref_tag(is_vid, img_n, vid_n))
    return tags


# Синонимы @-тегов: как бы юзер ни написал (@photo/@фото/@видео) — приводим к тому,
# что понимает Seedance (@image/@video/@audio). Бесцифровой тег -> ...1.
_VIDEO_TAG_ALIASES = [
    (r"@(?:фото|photo|картин\w*|pic|img|image)", "@image"),
    (r"@(?:видео|видос\w*|video|vid|clip|клип)", "@video"),
    (r"@(?:аудио|audio|звук|sound|music|музык\w*)", "@audio"),
]


def normalize_video_tags(text):
    """Привести @-упоминания в тексте к канону Seedance (@image/@video/@audio + число).
    Синонимы -> канон; бесцифровой тег (@video) -> @video1."""
    import re
    t = str(text or "")
    for pat, rep in _VIDEO_TAG_ALIASES:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    # @video/@image/@audio без цифры -> ...1 (@video -> @video1); с цифрой не трогаем
    t = re.sub(r"@(image|video|audio)(?!\d)", r"@\g<1>1", t, flags=re.IGNORECASE)
    return t


def _video_prompt(prompt, refs):
    """Финальный промпт: сцена + инструкции к референсам через @-упоминания
    (@image1/@video1). Именно так Seedance 2.0 связывает приложенный файл с текстом.
    Юзер может САМ вписать @video1 в промпт (синонимы @photo/@видео нормализуются) —
    тогда его размещение уважаем и авто-строку по этому тегу НЕ добавляем; иначе
    подпись с карточки вплетается предложением (Reference @videoN: …)."""
    base = normalize_video_tags(str(prompt or "").strip())
    tags = video_ref_tags(refs)
    notes = []
    for r, tag in zip(refs or [], tags):
        if tag in base:                         # юзер уже упомянул тег в промпте — не дублируем
            continue
        cap = str(r.get("caption") or "").strip()
        role = r.get("role") or "reference"
        if role == "first_frame":
            note = f"Use {tag} as the first frame"
        elif role == "last_frame":
            note = f"Use {tag} as the last frame"
        else:
            note = f"Reference {tag}"
        if cap:
            note += f": {cap}"
        notes.append(note + ".")
    parts = ([base] if base else []) + notes
    return " ".join(parts).strip()


def dangling_tags(text, refs):
    """@-теги, упомянутые в промпте, к которым НЕТ приложенного файла. Модель такой
    тег не свяжет ни с чем — а деньги за генерацию уже возьмут, поэтому предупреждаем."""
    have = set(video_ref_tags(refs))
    used = set(re.findall(r"@(?:image|video|audio)\d+", normalize_video_tags(text or ""),
                          flags=re.IGNORECASE))
    return sorted(t for t in used if t.lower() not in {h.lower() for h in have})


class _PollBadResponse(Exception):
    """Ответ опроса статуса не является JSON-объектом (HTML-ошибка 200, список и т.п.)."""
    pass


def gen_video(prompt, refs=None, opts=None, out_dir=None, prof=None,
              emit=console_emit, should_cancel=None, poll_every=5.0, max_wait=1800):
    """Сгенерировать одно видео. Блокирующая (зовётся в фоновом потоке api/videogen.py).

    refs — [{url, caption, role}] (url — ГОТОВАЯ https-ссылка; role: reference|
    first_frame|last_frame). opts — {duration, resolution, aspect_ratio, size, seed,
    audio, model}.
    Возвращает {path, cost, id, ms}. Ошибка -> SystemExit с человеческим текстом."""
    import time as _t
    prof = prof or resolve_video_profile()
    if prof is None:
        raise SystemExit(umsg("video_disabled", "генерация видео выключена — выбери профиль «Видео» в ⚙"))
    if prof["provider"] in ("anthropic", "lmstudio"):
        raise SystemExit(umsg("provider_no_video",
                              f"провайдер «{prof['provider']}» не генерит видео — нужен "
                              f"OpenRouter/совместимый с видео-моделью (Seedance 2)",
                              provider=prof["provider"]))
    refs = refs or []
    opts = opts or {}
    should_cancel = should_cancel or (lambda: False)
    out_dir = out_dir or os.getcwd()
    # id моделей у провайдера в нижнем регистре — иначе 404 на ровном месте
    model = (opts.get("model") or prof["model"] or VIDEO_MODEL_HINTS[0]).strip().lower()

    base = prof["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + (prof.get("api_key") or ""),
               "HTTP-Referer": APP_REFERER, "X-Title": APP_NAME}
    headers = apply_profile_headers(headers, prof)

    # валидируем параметры по КАТАЛОГУ (если подтянут кнопкой «Проверить возможности»):
    # у Seedance это списки допустимых длин/разрешений/пропорций/размеров. Недопустимое
    # не шлём (иначе 400 и деньги на ветер). caps=None -> каталога нет, шлём как есть.
    ensure_video_catalog(prof, emit=emit)      # ограничения модели знать надо ДО отправки
    caps = video_caps(model, prof=prof)
    # ПРЕДПОЛЁТ. Сначала выясняем, ЧТО реально лежит по каждой ссылке (тип и длина —
    # по расширению их знать нельзя), потом проверяем правила модели. Иначе это же
    # прилетит 400-м на чужом языке через полминуты ожидания.
    if refs:
        emit("  видео: проверяю референсы…")
        resolve_refs(refs, emit=emit)
    bad = video_check(model, opts, refs, caps=caps, probe=True, prompt=prompt, prof=prof)
    if bad:
        raise SystemExit(umsg("bad_opts", "; ".join(bad), list="; ".join(bad)))
    payload = {"model": model, "prompt": _video_prompt(prompt, refs)}
    for w in video_warnings(model, refs, caps=caps, prompt=prompt, prof=prof):
        emit("  видео: {warning}", warning=w)
    def _put(key, value, allowed=None, label=None):
        if allowed and str(value) not in allowed:
            emit("  видео: {key} «{value}» не в списке модели ({list}) — не шлю",
                 key=label or key, value=value,
                 list=f"{', '.join(allowed[:8])}{'…' if len(allowed) > 8 else ''}")
            return
        payload[key] = value

    if opts.get("duration"):
        try:
            _put("duration", int(opts["duration"]),
                 caps and caps["durations"] or None, "длина")
        except (TypeError, ValueError):
            pass
    if opts.get("resolution"):
        _put("resolution", str(opts["resolution"]),
             caps and caps["resolutions"] or None, "разрешение")
    # size ЯВНО перекрывает aspect_ratio (пиксели точнее) — шлём что-то одно
    if str(opts.get("size") or "").strip():
        _put("size", str(opts["size"]).strip(), caps and caps["sizes"] or None, "размер")
    elif opts.get("aspect_ratio"):
        _put("aspect_ratio", str(opts["aspect_ratio"]),
             caps and caps["aspect_ratios"] or None, "пропорции")
    if opts.get("seed") not in (None, "", "auto"):
        if caps and not caps["seed"]:
            emit("  видео: seed модель не поддерживает — не шлю")
        else:
            try:
                payload["seed"] = int(opts["seed"])
            except (TypeError, ValueError):
                pass
    # generate_audio шлём ЯВНО (true/false). Если параметр не послать, модель решает
    # сама, а Seedance по умолчанию генерит СО ЗВУКОМ — из-за этого звук появлялся,
    # хотя галка снята. caps.audio False = модель звук вообще не умеет -> не шлём.
    if caps is None or caps["audio"]:
        payload["generate_audio"] = bool(opts.get("audio"))
    elif opts.get("audio"):
        emit("  видео: звук модель не генерит — не шлю generate_audio")

    # Референсы — ГОТОВЫЕ HTTPS-ссылки (OpenRouter качает файл по URL; локальные/
    # data-URI он отвергает). Подпись в любом случае уже в промпте (_video_prompt).
    refs_ok = caps["references"] if caps else None   # None=каталог не подтверждает
    frame_ok = caps["frame_images"] if caps else None
    frame_imgs, input_refs = [], []
    for r in refs:
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        if not url.lower().startswith("https://"):
            emit("  видео: «{url}» — не HTTPS-ссылка, референс пропущен (OpenRouter качает только по https://); подпись ушла в промпт",
                 url=url[:60])
            continue
        role = r.get("role") or "reference"
        cap = r.get("caption") or ""
        kind = "video" if _ref_is_video(r) else "image"
        if role in ("first_frame", "last_frame"):
            if frame_ok and role not in frame_ok:
                emit("  видео: {role} модель не принимает; ссылка пропущена, подпись ушла в промпт",
                     role=role)
                continue
            frame_imgs.append(_video_ref_item(url, cap, frame_type=role, kind=kind))
        else:
            if refs_ok is False:
                # каталог явно НЕ заявляет input_references — ссылку не шлём (почти
                # наверняка 400), но подпись уже в промпте (_video_prompt)
                emit("  видео: референсы модель не заявляет (каталог); ссылка пропущена, подпись ушла в промпт")
                continue
            input_refs.append(_video_ref_item(url, cap, kind=kind))
    if frame_imgs:
        payload["frame_images"] = frame_imgs
    if input_refs:
        payload["input_references"] = input_refs

    emit("  видео: {model} — отправляю запрос ({refs} референс(ов), {frames} кадр(ов))…",
         model=model, refs=len(input_refs), frames=len(frame_imgs))
    t0 = _t.time()

    def _post(url, data):
        req = http_req(url, data=json.dumps(data).encode("utf-8"),
                       headers=headers)
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)

    def _get(url):
        req = http_req(url, headers=headers)
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read()
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        snippet = re.sub(r"\s+", " ", text).strip()[:120]
        try:
            res = json.loads(text)
        except ValueError:
            raise _PollBadResponse(snippet or "не JSON")
        if not isinstance(res, dict):
            raise _PollBadResponse(snippet or "не dict")
        return res

    try:
        job = _post(base + "/videos", payload)
    except urllib.error.HTTPError as e:
        raise SystemExit(_video_http_error(e, prof, "запрос генерации"))
    except urllib.error.URLError as e:
        raise SystemExit(umsg("provider_no_connection", f"нет связи с провайдером видео: {e}", err=e))

    vid = job.get("id") or job.get("job_id") or ""
    poll_url = job.get("polling_url") or (base + "/videos/" + vid if vid else "")
    if not poll_url:
        raise SystemExit(umsg("no_task_id",
                              "провайдер не вернул id/polling_url: "
                              + json.dumps(job, ensure_ascii=False)[:200],
                              resp=json.dumps(job, ensure_ascii=False)[:200]))
    emit("  видео: задача {vid} принята, жду готовности…", vid=vid or '—')

    def _stop():
        """«Стоп» после отправки: наша остановка опроса задачу у провайдера НЕ
        отменяет — она досчитается и будет оплачена. Публичной схемой отмена не
        описана, но статус cancelled и вебхук video.generation.cancelled есть, значит
        эндпоинт существует: пробуем оба вероятных, молча (404/405 = не умеет)."""
        for meth, url in (("POST", f"{base}/videos/{vid}/cancel"),
                          ("DELETE", f"{base}/videos/{vid}")):
            try:
                req = http_req(url, headers=headers, method=meth,
                               data=b"" if meth == "POST" else None)
                with urllib.request.urlopen(req, timeout=20):
                    return SystemExit("остановлено по кнопке — провайдер отменил задачу")
            except Exception:
                continue
        return SystemExit(f"остановлено по кнопке. ВНИМАНИЕ: провайдер отмену не принял — "
                          f"задача {vid} может досчитаться и списаться; готовое видео "
                          f"будет тут: {base}/videos/{vid}")

    status, cost, urls = "", None, []
    errs = 0                                             # подряд идущие ошибки опроса
    while _t.time() - t0 < max_wait:
        if should_cancel():
            raise _stop()
        _t.sleep(poll_every)
        if should_cancel():
            raise _stop()
        try:
            st = _get(poll_url)
            errs = 0
        except urllib.error.HTTPError as e:
            # не роняем задачу на ОДНОЙ ошибке опроса: провайдер мог моргнуть уже
            # ПОСЛЕ того как всё сгенерировал и списал деньги (было: 401 в самом конце)
            errs += 1
            if errs >= 4:
                raise SystemExit(_video_http_error(e, prof, "опрос статуса"))
            emit("  видео: опрос статуса {code} — повтор ({errs}/3)…",
                 code=e.code, errs=errs)
            continue
        except urllib.error.URLError:
            continue                                    # временный сетевой сбой — ещё раз
        except (_PollBadResponse, TimeoutError, OSError) as e:
            errs += 1
            reason = str(e) or e.__class__.__name__
            if errs >= 4:
                raise SystemExit(umsg("video_poll_failed",
                                      f"опрос статуса не удался ({reason}). "
                                      f"Задача {vid} могла досчитаться и списаться — "
                                      f"проверь её у провайдера: {poll_url}",
                                      reason=reason, vid=vid, poll_url=poll_url))
            emit("  видео: опрос статуса ({reason}) — повтор ({errs}/3)…",
                 reason=reason, errs=errs)
            continue
        status = (st.get("status") or "").lower()
        cost = ((st.get("usage") or {}).get("cost")) or cost
        if status in ("completed", "succeeded", "success"):
            urls = st.get("unsigned_urls") or st.get("urls") or []
            break
        if status in ("failed", "error", "canceled", "cancelled"):
            err = st.get("error") or st.get("message") or json.dumps(st, ensure_ascii=False)
            msg, hint = _video_error_text(err if isinstance(err, str)
                                          else json.dumps(err, ensure_ascii=False))
            raise SystemExit(umsg("video_status_failed",
                                  (f"{hint}. Провайдер: {msg}" if hint else
                                   f"провайдер вернул статус «{status}»: {msg}"),
                                  status=status, msg=msg))
        emit("  видео: {status}… ({sec}с)",
             status=status or 'в очереди', sec=int(_t.time() - t0))
    else:
        raise SystemExit(umsg("video_timeout", f"видео не готово за {max_wait}с — прервано по таймауту", max_wait=max_wait))

    # СКАЧИВАНИЕ. Тонкость (из-за неё терялся уже ОПЛАЧЕННЫЙ результат): OpenRouter
    # отдаёт unsigned_urls, но по факту им НУЖЕН заголовок авторизации (иначе 401 —
    # хотя генерация завершена и списаны деньги). Поэтому пробуем каждый URL и С, и
    # БЕЗ авторизации; плюс фолбэк на content-эндпоинт. Первый успех — берём.
    emit("  видео: готово у провайдера, скачиваю (задача {vid})…", vid=vid or '—')
    content_ep = (base + "/videos/" + vid + "/content?index=0") if vid else ""
    remote = (urls[0] if urls else content_ep)          # что показать, если не скачается
    # Порядок попыток. urls[0] — CDN-ссылка (unsigned_urls), обычно ПОДПИСАННАЯ и
    # авторизацию не ждёт; ключ туда не шлём (это чужой провайдеру хост — утечка).
    # Bearer пробуем ТОЛЬКО когда хост совпал с базовым (там это может быть тот же
    # API). Сначала без авторизации — именно так подписанную ссылку и положено
    # открывать; восстановление ОПЛАЧЕННОГО результата не теряется: content-эндпоинт
    # на базовом хосте всё равно пробуем с авторизацией.
    from urllib.parse import urlparse
    base_host = urlparse(base).netloc.lower()
    tries = []
    for u in urls:
        same = urlparse(u).netloc.lower() == base_host
        tries += [(u, False)] + ([(u, True)] if same else [])
    if content_ep:
        tries += [(content_ep, True), (content_ep, False)]
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", (vid or "video"))[:60] or "video"
    # имя файла с моделью: в папке лежат ролики от разных моделей, «seedance_*» врало
    tag = re.sub(r"[^\w.-]+", "-", model.split("/")[-1])[:24] or "video"
    out_base = os.path.join(out_dir, f"{tag}_{safe}")
    out_path = f"{out_base}.mp4"

    last = None
    for url, with_auth in tries:
        tmp_path = None
        try:
            h = apply_profile_headers({"Authorization": headers["Authorization"]} if "Authorization" in headers else {}, prof) if with_auth else {}
            req = http_req(url, headers=h)
            with urllib.request.urlopen(req, timeout=600) as r:
                fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".dl_", suffix=".part")
                with os.fdopen(fd, "wb") as f:
                    shutil.copyfileobj(r, f)

                r_headers = getattr(r, "headers", None) or (r.info() if hasattr(r, "info") else {})
                ctype = (r_headers.get("Content-Type") or r_headers.get("content-type") or "").strip()
                ctype_low = ctype.lower()
                if ctype_low.startswith("text/") or "html" in ctype_low or "json" in ctype_low:
                    last = f"не видео ({ctype})"
                    continue

                sz = os.path.getsize(tmp_path)
                cl_raw = r_headers.get("Content-Length") or r_headers.get("content-length")
                if cl_raw is not None:
                    try:
                        cl = int(cl_raw)
                    except (ValueError, TypeError):
                        cl = None
                    if cl is not None and sz != cl:
                        last = f"оборвано: {sz} из {cl} байт"
                        continue

                if sz == 0:
                    last = "пустой файл"
                    continue

                with open(tmp_path, "rb") as f:
                    head = f.read(12)
                is_mp4 = len(head) >= 8 and head[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"}
                is_webm = len(head) >= 4 and head[:4] == b"\x1a\x45\xdf\xa3"
                if not (is_mp4 or is_webm):
                    last = "не видеофайл"
                    continue

                out_path = f"{out_base}.webm" if is_webm else f"{out_base}.mp4"
                os.replace(tmp_path, out_path)
                tmp_path = None
                last = None
                break
        except urllib.error.HTTPError as e:
            last = f"{e.code}"
            if e.code not in (401, 403, 404):            # не про доступ — перебор не поможет
                break
        except urllib.error.URLError as e:
            last = str(e)
            break
        except (TimeoutError, OSError) as e:
            last = str(e) or e.__class__.__name__
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    if last is not None:
        # видео СГЕНЕРИРОВАНО и оплачено — не теряем его: отдаём прямую ссылку
        raise SystemExit(umsg("video_download_failed",
                              (f"видео СОЗДАНО (${cost:.3f}), но не скачалось ({last}). "
                               f"Оно доступно ~48ч — скачай вручную: {remote}  (задача {vid})"
                               if isinstance(cost, (int, float)) else
                               f"видео создано, но не скачалось ({last}). "
                               f"Доступно ~48ч: {remote} (задача {vid})"),
                              last=last, remote=remote, vid=vid))

    kb = os.path.getsize(out_path) // 1024
    ms = int((_t.time() - t0) * 1000)
    if isinstance(cost, (int, float)):
        emit("  видео готово: {path} ({kb}КБ, {sec}с, ${cost:.3f})",
             path=os.path.basename(out_path), kb=kb, sec=ms // 1000, cost=cost)
    else:
        emit("  видео готово: {path} ({kb}КБ, {sec}с)",
             path=os.path.basename(out_path), kb=kb, sec=ms // 1000)
    return {"path": out_path, "cost": cost, "id": vid, "ms": ms}



# Что отвечает провайдер (по-английски, в JSON внутри JSON) -> что делать по-русски.
# Порядок важен: первое совпадение и выигрывает.
_VIDEO_ERR_HINTS = [
    (r"total duration.*?(\d+(?:\.\d+)?)\s*(?:seconds|s)?.*?\br2v\b|"
     r"\br2v\b.*?total duration.*?(\d+(?:\.\d+)?)",
     "видео-референсы суммарно длиннее, чем берёт модель (Seedance — 15с на ВСЕ видео "
     "вместе): обрежь их или оставь одно"),
    (r"only https urls?", "референс должен быть ПРЯМОЙ https-ссылкой на файл — "
                          "локальные файлы провайдер не качает"),
    (r"download.*(image|video)|failed to (download|fetch)",
     "провайдер не смог скачать референс по ссылке — она должна открываться без "
     "авторизации (проверь в режиме инкогнито)"),
    (r"first_frame.*reference|reference.*first_frame|conflict",
     "кадр и референсы вместе не отправляются — оставь что-то одно"),
    (r"duration", "недопустимая длина ролика — нажми «Проверить возможности» и возьми "
                  "длину из списка модели"),
    (r"resolution|size", "недопустимое разрешение/размер для этой модели"),
    (r"aspect", "недопустимые пропорции для этой модели"),
    (r"sensitive|moderation|policy|copyright",
     "запрос или результат не прошёл модерацию провайдера — перефразируй "
     "(без имён персонажей, брендов, политики)"),
]


def _video_error_text(detail):
    """Вытащить человеческую суть из ответа провайдера. OpenRouter вкладывает ошибку
    апстрима строкой в свой JSON («message»: «HTTP 400: {...}»), и в UI прилетала
    каша из скобок. -> (короткий текст ошибки, подсказка что делать | '')."""
    msg = str(detail or "")
    seen = set()
    for _ in range(4):                          # message в message в message
        try:
            d = json.loads(msg[msg.index("{"):])
        except Exception:
            break
        m = d.get("error") if isinstance(d.get("error"), dict) else d
        nxt = str((m or {}).get("message") or "")
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        msg = nxt
    msg = re.sub(r"\s*Request id:\s*\S+", "", msg).strip(" .{}\"'")
    low = msg.lower()
    for pat, hint in _VIDEO_ERR_HINTS:
        if re.search(pat, low, re.S):
            return msg[:300], hint
    return msg[:300], ""


def _video_http_error(e, prof, where):
    """HTTPError видео-эндпоинта -> UMsg с кодом (для SystemExit)."""
    try:
        detail = e.read().decode("utf-8", "replace")[:1200]
    except Exception:
        detail = ""
    if e.code in (401, 403):
        return umsg("key_rejected", f"API-ключ не принят ({e.code}) — проверь профиль «{prof['name']}» в ⚙",
                    code=e.code, name=prof["name"])
    if e.code == 402:
        return umsg("no_credits", "у провайдера кончились кредиты (402) — пополни баланс")
    if e.code == 429:
        return umsg("rate_limit", "лимит запросов провайдера (429) — подожди и повтори")
    msg, hint = _video_error_text(detail)
    if e.code == 404:
        return umsg("video_http",
                    (f"видео-эндпоинт/модель не найдены ({where}) — проверь, что провайдер "
                     f"поддерживает Video API и модель верна: {msg}"),
                    code=e.code, where=where, msg=msg)
    if hint:
        return umsg("video_http", f"{hint}. Провайдер: {msg}", code=e.code, msg=msg)
    return umsg("video_http", f"{where}: {e.code} {msg or detail[:300]}",
                code=e.code, where=where, msg=msg)
