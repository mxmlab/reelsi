# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Генерация картинок для вставок (OpenRouter image API или chat-модель).

Промпт собирается из слотов: техническая часть (общая для всех) + пользовательская
для конкретного слота. Слоты нужны затем, что вставке в кадр и обложке нужны разные
указания, а техчасть у них одна и та же.
"""
import json
import urllib.request, urllib.error
from .config import APP_NAME, APP_REFERER, _profile_dict, apply_profile_headers, load_ai_config
from .llm import cancel_reason, cancelled
from core.umsg import umsg
from core.app_meta import console_emit, http_req



# ---- Генерация картинок-вставок (Nano Banana и т.п.) ------------------------
IMAGE_OFF = "__off__"           # значение active_image «генерация выключена» (дефолт)
IMAGE_MODEL_HINTS = ["google/gemini-3.1-flash-lite-image",   # ~$0.04/картинка (рекоменд.)
                     "google/gemini-3.1-flash-image",        # ~$0.08
                     "google/gemini-2.5-flash-image"]        # ~$0.04, прошлое поколение


def image_rembg_on():
    """Убирать ли фон у сгенерённого (rembg → прозрачный PNG). По умолчанию ДА:
    image-модели отдают предмет на белом фоне, а вставки в базе — с альфой."""
    return bool(load_ai_config().get("image_rembg", True))


# Слотов приписки ДВА (a и b) под разные стили (на карточке вставки кнопки 1 и 2).
# Настройки хранятся строго в профиле спикера (speakers/*.json -> image_prompts).
IMAGE_PROMPT_SLOTS = ("a", "b")


def resolve_image_prompt_cfg(slot="a", speaker=None):
    """Настройки промпта генерации выбранного слота с учётом спикера.
    Цепочка разрешения (задание CS):
    1) профиль спикера этого клипа (speaker: имя, label или dict) -> image_prompts[slot]
    2) если у клипа нет тега спикера, нет профиля или у спикера пусто -> приписки нет (extra: '')
    """
    slot = slot or "a"
    if speaker:
        try:
            from core import speakers
            prof = speaker if isinstance(speaker, dict) else speakers.load(speaker)
            if isinstance(prof, dict):
                ips = prof.get("image_prompts") or {}
                scfg = ips.get(slot) or {}
                extra = str(scfg.get("extra") or "").strip()
                if extra:
                    pos = scfg.get("pos")
                    return {
                        "extra": extra,
                        "pos": pos if pos in ("prefix", "suffix") else "suffix",
                    }
        except Exception:
            pass
    return {"extra": "", "pos": "suffix"}


def build_image_prompt(query, cfg=None, slot="a", speaker=None):
    """Собрать промпт генерации из предмета и стилевой приписки выбранного слота.
    Приписка клеится ПРОБЕЛОМ, а не запятой: «broken eyeglasses 3d icon» — это одна
    именная группа, а «broken eyeglasses, 3d icon» модель читает как два предмета
    (очки И отдельно иконка) и рисует композицию из двух объектов."""
    c = cfg or resolve_image_prompt_cfg(slot, speaker=speaker)
    parts = [str(query or "").strip()]
    if c["extra"]:
        parts.insert(0 if c["pos"] == "prefix" else 1, c["extra"])
    return " ".join(p for p in parts if p)


def resolve_image_profile():
    """Профиль-«художник» для генерации картинок-вставок. None = выключено (дефолт).
    Годится только OpenAI-совместимый провайдер с image-моделью (OpenRouter/свой);
    anthropic/lmstudio картинки не генерят."""
    cfg = load_ai_config()
    name = cfg.get("active_image") or IMAGE_OFF
    prof = cfg["profiles"].get(name)
    if name == IMAGE_OFF or prof is None:
        return None
    return _profile_dict(name, prof)


# Точные id image-моделей (выделенный Image API, /images/models)
# -> их supported_parameters. Заполняется из api/ai.py кнопкой «Обновить список».
# Пусто — значит список не подтянут: шлём минимальный запрос (model+prompt), он
# валиден для всех моделей.
IMAGE_MODELS = {}


def _img_http_error(e, prof):
    """Разбор HTTPError генерации картинки: фатальное -> SystemExit, иначе строка
    для ретрая."""
    try:
        detail = e.read().decode("utf-8", "replace")[:300]
    except Exception:
        detail = ""
    if e.code in (401, 403):
        raise SystemExit(umsg("key_rejected", f"API-ключ не принят ({e.code}) — проверь профиль «{prof['name']}» в ⚙", code=e.code, name=prof["name"]))
    if e.code == 402:
        raise SystemExit(umsg("no_credits", "у провайдера кончились кредиты (402) — пополни баланс"))
    if e.code == 429:
        raise SystemExit(umsg("rate_limit", "лимит запросов провайдера (429) — подожди и повтори"))
    return f"{e.code}: {detail}"


class _Image404Error(Exception):
    pass


def gen_image(prompt, prof=None, emit=console_emit, retries=1):
    """Одна картинка. Возвращает bytes (PNG/JPEG — как отдал провайдер).

    Модель в IMAGE_MODELS или каталог пуст -> Image API (POST /images).
    Если модели нет в каталоге и Image API ответил 404 -> откат на
    /chat/completions с modalities:["image"] (Nano Banana и др.)."""
    prof = prof or resolve_image_profile()
    if prof is None:
        raise SystemExit(umsg("images_disabled", "генерация картинок выключена — выбери профиль «Картинки» в настройках ⚙"))
    if prof["provider"] in ("anthropic", "lmstudio"):
        raise SystemExit(umsg("provider_no_images",
                              f"провайдер «{prof['provider']}» не генерит картинки — нужен "
                              f"OpenRouter/OpenAI-совместимый с image-моделью (FLUX, Nano Banana)",
                              provider=prof["provider"]))
    try:
        return _gen_image_openrouter(prompt, prof, emit=emit, retries=retries)
    except _Image404Error:
        return _gen_image_chat(prompt, prof, emit=emit, retries=retries)


def _gen_image_openrouter(prompt, prof, emit=console_emit, retries=1):
    """Выделенный Image API: POST /images {model, prompt} ->
    {"data": [{"b64_json": ..., "media_type": ...}], "usage": {"cost": $}}.
    Замер: FLUX.2 Klein ~4с/$0.014 за картинку 1024×1024."""
    import base64
    url = prof["base_url"].rstrip("/") + "/images"
    payload = {"model": prof["model"], "prompt": prompt}
    # Параметры сверх model/prompt у каждой модели свои (FLUX: output_format/n/seed/
    # input_references; gpt-image: background/quality/resolution). Шлём только то,
    # что модель заявила в supported_parameters — иначе просто молча игнорируется.
    sp = IMAGE_MODELS.get((prof["model"] or "").lower()) or {}
    if "output_format" in sp:
        payload["output_format"] = "png"        # PNG: без артефактов JPEG под rembg
    headers = {"Content-Type": "application/json"}
    if prof.get("api_key"):
        headers["Authorization"] = "Bearer " + prof["api_key"]
    if prof.get("provider") == "openrouter":
        headers["HTTP-Referer"] = APP_REFERER
        headers["X-Title"] = APP_NAME
    headers = apply_profile_headers(headers, prof)
    last = None
    in_catalog = (prof["model"] or "").lower() in IMAGE_MODELS
    for attempt in range(retries + 1):
        if cancelled():
            raise SystemExit(cancel_reason())
        req = http_req(url, data=json.dumps(payload).encode("utf-8"),
                       headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:                   # модель не картиночная / не в Image API
                if in_catalog:
                    raise SystemExit(umsg("img_model_not_found",
                                          f"модель «{prof['model']}» не найдена в Image API — "
                                          f"выбери image-модель (FLUX, Nano Banana) в профиле «Картинки» ⚙",
                                          model=prof["model"]))
                raise _Image404Error()
            last = _img_http_error(e, prof)
        except urllib.error.URLError as e:
            last = str(e)
        else:
            item = ((resp.get("data") or [{}])[0]) or {}
            b64 = item.get("b64_json") or ""
            if b64:
                cost = ((resp.get("usage") or {}).get("cost"))
                if isinstance(cost, (int, float)):
                    emit("  картинка: {model} ({kb}КБ, ${cost:.3f})",
                         model=prof['model'], kb=len(b64) // 1370, cost=cost)
                else:
                    emit("  картинка: {model} ({kb}КБ)",
                         model=prof['model'], kb=len(b64) // 1370)
                return base64.b64decode(b64)
            last = ("Image API не вернул картинку, ответ: "
                    + json.dumps(resp, ensure_ascii=False)[:200])
        emit("! генерация не удалась ({err}) — повтор {attempt}/{retries}",
             err=last, attempt=attempt + 1, retries=retries)
    raise SystemExit(umsg("gen_failed", f"генерация картинки упала после {retries + 1} попыток: {last}", tries=retries + 1, last=last))


def _gen_image_chat(prompt, prof, emit=console_emit, retries=1):
    """Старый путь: OpenAI-совместимый /chat/completions с modalities:["image"]
    (base64 в choices[0].message.images[0].image_url.url)."""
    import base64
    url = prof["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": prof["model"], "modalities": ["image", "text"],
               "messages": [{"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json"}
    if prof.get("api_key"):
        headers["Authorization"] = "Bearer " + prof["api_key"]
    headers = apply_profile_headers(headers, prof)
    last = None
    for attempt in range(retries + 1):
        if cancelled():
            raise SystemExit(cancel_reason())
        req = http_req(url, data=json.dumps(payload).encode("utf-8"),
                       headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            last = _img_http_error(e, prof)
        except urllib.error.URLError as e:
            last = str(e)
        else:
            imgs = ((resp.get("choices") or [{}])[0].get("message") or {}).get("images") or []
            data_url = (imgs[0].get("image_url") or {}).get("url", "") if imgs else ""
            if data_url.startswith("data:") and "base64," in data_url:
                emit("  картинка: {model} ({kb}КБ)",
                     model=prof['model'], kb=len(data_url) // 1370)
                return base64.b64decode(data_url.split("base64,", 1)[1])
            last = ("модель не вернула картинку — точно image-модель? ответ: "
                    + json.dumps(resp, ensure_ascii=False)[:200])
        emit("! генерация не удалась ({err}) — повтор {attempt}/{retries}",
             err=last, attempt=attempt + 1, retries=retries)
    raise SystemExit(umsg("gen_failed", f"генерация картинки упала после {retries + 1} попыток: {last}", tries=retries + 1, last=last))