# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Генерация картинок для вставок (OpenRouter image API или chat-модель).

Промпт собирается из слотов: техническая часть (общая для всех) + пользовательская
для конкретного слота. Слоты нужны затем, что вставке в кадр и обложке нужны разные
указания, а техчасть у них одна и та же.
"""
from __future__ import annotations
import json
import socket
import threading
import time
import urllib.request, urllib.error
from typing import Any, Callable

from .config import APP_NAME, APP_REFERER, _profile_dict, apply_profile_headers, load_ai_config
from .llm import ai_log_append, cancel_reason, cancelled
from core.applog import get_logger
from core.umsg import ReelsiError, umsg
from core.app_meta import console_emit, http_req, unsafe_url_reason

log = get_logger(__name__)

# ---- Unsloth Studio (локальная генерация картинок) --------------------------
_UNSLOTH_LOCK = threading.Lock()
_UNSLOTH_OURS: tuple[str, str] | None = None
_UNSLOTH_TIMER: threading.Timer | None = None
UNSLOTH_IDLE_SEC: float = 90
UNSLOTH_POLL_SEC: float = 2.0
UNSLOTH_LOAD_TIMEOUT_SEC: float = 900


# ---- Генерация картинок-вставок (Nano Banana и т.п.) ------------------------
IMAGE_OFF = "__off__"           # значение active_image «генерация выключена» (дефолт)
# Обычная картинка 4–5 с, 90 — запас; мёртвое соединение не должно держать слот браузера 5–10 минут
IMAGE_TIMEOUT_S = 90
# OpenRouter /images у части промптов («large water bottles row») виснет наглухо (150с+),
# тогда как /chat/completions отдаёт ту же картинку за 4.8с. 40с — запас для Image API до отката на чат.
IMAGES_API_TIMEOUT_S = 40
IMAGE_MODEL_HINTS = ["google/gemini-3.1-flash-lite-image",   # ~$0.04/картинка (рекоменд.)
                     "google/gemini-3.1-flash-image",        # ~$0.08
                     "google/gemini-2.5-flash-image"]        # ~$0.04, прошлое поколение


def image_rembg_on() -> bool:
    """Убирать ли фон у сгенерённого (rembg → прозрачный PNG). По умолчанию ДА:
    image-модели отдают предмет на белом фоне, а вставки в базе — с альфой."""
    return bool(load_ai_config().get("image_rembg", True))


# Модель вырезания фона (rembg). u2net — дефолт, быстрая (~0.7 с на картинку); BiRefNet —
# чище края, но на процессоре ~8 с. Значения — имена моделей rembg как есть.
REMBG_DEFAULT = "u2net"
REMBG_MODELS = ("u2net", "birefnet-general")


def rembg_model() -> str:
    """Модель вырезания фона из общих настроек. Чужое или битое значение в конфиге —
    дефолт: вырезание не должно ломаться от руками испорченного файла."""
    v = load_ai_config().get("rembg_model")
    return v if v in REMBG_MODELS else REMBG_DEFAULT


# Слотов приписки ЧЕТЫРЕ: a/b под разные стили (на карточке вставки кнопки 1 и 2) и
# pa/pb — свои приписки для вставок с галкой «на подложке»: подложка уже
# из стиля, фото с вырезанным фоном, и стиль предмета у таких вставок свой.
# Настройки хранятся строго в профиле спикера (speakers/*.json -> image_prompts).
IMAGE_PROMPT_SLOTS = ("a", "b", "pa", "pb")


def resolve_image_prompt_cfg(slot: str = "a", speaker: Any = None) -> dict[str, Any]:
    """Настройки промпта генерации выбранного слота с учётом спикера.
    Цепочка разрешения:
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
        except ReelsiError: raise
        except Exception:
            pass  # в стиле нет ключа промпта — берём пустую добавку
    return {"extra": "", "pos": "suffix"}


def build_image_prompt(query: Any, cfg: dict[str, Any] | None = None, slot: str = "a", speaker: Any = None) -> str:
    """Собрать промпт генерации из предмета и стилевой приписки выбранного слота.
    Приписка клеится ПРОБЕЛОМ, а не запятой: «broken eyeglasses 3d icon» — это одна
    именная группа, а «broken eyeglasses, 3d icon» модель читает как два предмета
    (очки И отдельно иконка) и рисует композицию из двух объектов."""
    c = cfg or resolve_image_prompt_cfg(slot, speaker=speaker)
    parts = [str(query or "").strip()]
    if c["extra"]:
        parts.insert(0 if c["pos"] == "prefix" else 1, c["extra"])
    return " ".join(p for p in parts if p)


def resolve_image_profile() -> dict[str, Any] | None:
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
IMAGE_MODELS: dict[str, Any] = {}


def _img_http_error(e: urllib.error.HTTPError, prof: dict[str, Any]) -> str:
    """Разбор HTTPError генерации картинки: фатальное -> ReelsiError, иначе строка
    для ретрая."""
    try:
        detail = e.read().decode("utf-8", "replace")[:300]
    except ReelsiError: raise
    except Exception:
        # тело ошибки — только подробность к коду (e.code уже известен и обработан ниже)
        detail = ""
    if e.code in (401, 403):
        raise ReelsiError(umsg("key_rejected", f"API-ключ не принят ({e.code}) — проверь профиль «{prof['name']}» в ⚙", code=e.code, name=prof["name"]))
    if e.code == 402:
        raise ReelsiError(umsg("no_credits", "у провайдера кончились кредиты (402) — пополни баланс"))
    if e.code == 429:
        raise ReelsiError(umsg("rate_limit", "лимит запросов провайдера (429) — подожди и повтори"))
    return f"{e.code}: {detail}"


def _is_timeout(e: BaseException) -> bool:
    if isinstance(e, (TimeoutError, socket.timeout)):
        return True
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
            return True
    return False


def _check_img_timeout(e: BaseException) -> None:
    """Истечение таймаута без повтора: мёртвое соединение не должно удваивать ожидание."""
    if _is_timeout(e):
        raise ReelsiError(umsg("img_timeout", f"провайдер не ответил за {IMAGE_TIMEOUT_S} с — повтори генерацию", s=IMAGE_TIMEOUT_S))


class _ImageTimeoutError(Exception):
    """Сбой провайдера: Image API (/images) не ответил вовремя — откат на /chat/completions."""


class _Image404Error(Exception):
    pass


def gen_image(prompt: str, prof: dict[str, Any] | None = None, emit: Callable[..., Any] = console_emit, retries: int = 1) -> bytes:
    """Одна картинка. Возвращает bytes (PNG/JPEG — как отдал провайдер).

    Модель в IMAGE_MODELS или каталог пуст -> Image API (POST /images).
    Если модели нет в каталоге и Image API ответил 404 -> откат на
    /chat/completions с modalities:["image"] (Nano Banana и др.).
    Если Image API не ответил за IMAGES_API_TIMEOUT_S (40с) — также откат на чат."""
    prof = prof or resolve_image_profile()
    if prof is None:
        raise ReelsiError(umsg("images_disabled", "генерация картинок выключена — выбери профиль «Картинки» в настройках ⚙"))
    if prof["provider"] in ("anthropic", "lmstudio"):
        raise ReelsiError(umsg("provider_no_images",
                              f"провайдер «{prof['provider']}» не генерит картинки — нужен "
                              f"OpenRouter/OpenAI-совместимый с image-моделью (FLUX, Nano Banana)",
                              provider=prof["provider"]))
    t0 = time.time()
    try:
        if prof["provider"] == "unsloth":
            res = _gen_image_unsloth(prompt, prof, emit=emit)
        else:
            try:
                res = _gen_image_openrouter(prompt, prof, emit=emit, retries=retries)
            except _ImageTimeoutError:
                # Сбой провайдера: OpenRouter /images у gemini-3.1-flash-lite-image на части промптов
                # («large water bottles row») виснет наглухо (150 с и повторно 60 с — TimeoutError),
                # а тот же промпт через /chat/completions даёт картинку за 4.8 с («a red apple on
                # white background» через /images — 4.5 с). Откатываемся на чат.
                emit("! Image API не ответил за {s} с — пробую через чат", s=IMAGES_API_TIMEOUT_S)
                res = _gen_image_chat(prompt, prof, emit=emit, retries=retries)
            except _Image404Error:
                res = _gen_image_chat(prompt, prof, emit=emit, retries=retries)
        ai_log_append("image", prof, ok=True, ms=(time.time() - t0) * 1000, err=None)
        return res
    except (ReelsiError, Exception, SystemExit) as e:
        ai_log_append("image", prof, ok=False, ms=(time.time() - t0) * 1000, err=str(e))
        raise


def _gen_image_openrouter(prompt: str, prof: dict[str, Any], emit: Callable[..., Any] = console_emit, retries: int = 1) -> bytes:
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
            raise ReelsiError(cancel_reason())
        req = http_req(url, data=json.dumps(payload).encode("utf-8"),
                       headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=IMAGES_API_TIMEOUT_S) as r:
                resp = json.load(r)
        except (TimeoutError, socket.timeout):
            # OpenRouter /images у gemini-3.1-flash-lite-image на части промптов («large water bottles row»)
            # виснет (150с), тогда как /chat/completions отдаёт за 4.8с. Без повтора — сразу откат на чат.
            raise _ImageTimeoutError()
        except urllib.error.HTTPError as e:
            if e.code == 404:                   # модель не картиночная / не в Image API
                if in_catalog:
                    raise ReelsiError(umsg("img_model_not_found",
                                          f"модель «{prof['model']}» не найдена в Image API — "
                                          f"выбери image-модель (FLUX, Nano Banana) в профиле «Картинки» ⚙",
                                          model=prof["model"]))
                raise _Image404Error()
            last = _img_http_error(e, prof)
        except urllib.error.URLError as e:
            if _is_timeout(e):
                raise _ImageTimeoutError()
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
    raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после {retries + 1} попыток: {last}", tries=retries + 1, last=last))


def _gen_image_chat(prompt: str, prof: dict[str, Any], emit: Callable[..., Any] = console_emit, retries: int = 1) -> bytes:
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
            raise ReelsiError(cancel_reason())
        req = http_req(url, data=json.dumps(payload).encode("utf-8"),
                       headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT_S) as r:
                resp = json.load(r)
        except (TimeoutError, socket.timeout) as e:
            _check_img_timeout(e)
        except urllib.error.HTTPError as e:
            last = _img_http_error(e, prof)
        except urllib.error.URLError as e:
            _check_img_timeout(e)
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
    raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после {retries + 1} попыток: {last}", tries=retries + 1, last=last))


def _parse_unsloth_model(model_str: str) -> tuple[str, str]:
    """Формат поля «модель» профиля: <владелец>/<репо> или <владелец>/<репо>/<файл>.gguf.
    Последний сегмент на .gguf = gguf_filename, остальное = model_path."""
    m = (model_str or "").strip()
    if "/" in m and m.lower().endswith(".gguf"):
        model_path, _, gguf = m.rpartition("/")
        return model_path.strip(), gguf.strip()
    return m, ""


def _unsloth_root(prof: dict[str, Any]) -> str:
    base = (prof.get("base_url") or "http://127.0.0.1:8888").strip().rstrip("/")
    if base.lower().endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base or "http://127.0.0.1:8888"


def _unsloth_ensure_loaded(root: str, model_path: str, gguf: str, emit: Callable[..., Any] = console_emit) -> None:
    global _UNSLOTH_OURS
    req = http_req(f"{root}/api/inference/images/status")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            st = json.load(r)
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        if isinstance(e, urllib.error.HTTPError):
            raise  # HTTP-ответы пробрасываем как есть
        raise ReelsiError(umsg("unsloth_unreachable",
                               "Unsloth Studio не отвечает на {root} — запусти приложение Unsloth Studio",
                               root=root)) from e

    is_loaded = bool(st.get("loaded"))
    repo_id = st.get("repo_id") or ""
    gguf_fn = st.get("gguf_filename") or ""
    mem_mode = st.get("memory_mode") or ""

    if is_loaded:
        if repo_id == model_path and (not gguf or gguf_fn == gguf) and mem_mode == "low_vram":
            return
        loaded_model = f"{repo_id}/{gguf_fn}" if (repo_id and gguf_fn) else (repo_id or gguf_fn or "unknown")
        raise ReelsiError(umsg("unsloth_foreign_model",
                               "В Unsloth Studio загружена «{model}» (режим {mode}) — "
                               "выгрузи её в Studio: Reelsi грузит свою модель сам, только в low_vram",
                               model=loaded_model, mode=mem_mode or "unknown"))

    from .llm import unload_ours
    unload_ours(emit=emit)

    model_label = f"{model_path}/{gguf}" if gguf else model_path
    emit("  Unsloth Studio: загружаю {model} (low_vram)…", model=model_label)

    load_body: dict[str, Any] = {
        "model_path": model_path,
    }
    if gguf:
        load_body["gguf_filename"] = gguf
    load_body["memory_mode"] = "low_vram"

    req_load = http_req(f"{root}/api/inference/images/load",
                        data=json.dumps(load_body).encode("utf-8"),
                        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req_load, timeout=900) as _:
            pass
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        if isinstance(e, urllib.error.HTTPError):
            err_detail = ""
            try:
                err_detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                # тело ответа не прочиталось — подробность берём из текста самого исключения
                err_detail = str(e)
            raise ReelsiError(umsg("unsloth_load_failed",
                                   "Unsloth Studio не загрузил {model}: {err}",
                                   model=model_path, err=err_detail)) from e
        raise ReelsiError(umsg("unsloth_unreachable",
                               "Unsloth Studio не отвечает на {root} — запусти приложение Unsloth Studio",
                               root=root)) from e

    key = f"{model_path}|{gguf}"
    _UNSLOTH_OURS = (root, key)

    # Ждём реальной готовности модели: POST load возвращается сразу,
    # модель грузится в фоне ~40–60 с.
    t0_wait = time.time()
    last_emit = 0.0
    while True:
        if cancelled():
            raise ReelsiError(cancel_reason())

        elapsed = time.time() - t0_wait
        if elapsed > UNSLOTH_LOAD_TIMEOUT_SEC:
            raise ReelsiError(umsg("unsloth_load_timeout",
                                   "Unsloth Studio не загрузил {model} за {sec} с",
                                   model=model_label, sec=int(elapsed)))

        time.sleep(UNSLOTH_POLL_SEC)

        # Проверяем прогресс загрузки (error поле)
        try:
            req_prog = http_req(f"{root}/api/inference/images/load-progress")
            with urllib.request.urlopen(req_prog, timeout=30) as r_prog:
                prog = json.load(r_prog)
            prog_err = prog.get("error")
            if prog_err:
                raise ReelsiError(umsg("unsloth_load_failed",
                                       "Unsloth Studio не загрузил {model}: {err}",
                                       model=model_label, err=str(prog_err)))
        except ReelsiError:
            raise
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError):
                raise
            raise ReelsiError(umsg("unsloth_unreachable",
                                   "Unsloth Studio не отвечает на {root} — запусти приложение Unsloth Studio",
                                   root=root)) from e

        # Проверяем статус модели
        try:
            req_st = http_req(f"{root}/api/inference/images/status")
            with urllib.request.urlopen(req_st, timeout=30) as r_st:
                st2 = json.load(r_st)
        except ReelsiError:
            raise
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError):
                raise
            raise ReelsiError(umsg("unsloth_unreachable",
                                   "Unsloth Studio не отвечает на {root} — запусти приложение Unsloth Studio",
                                   root=root)) from e

        if (st2.get("loaded")
                and st2.get("repo_id") == model_path
                and (not gguf or st2.get("gguf_filename") == gguf)):
            return  # модель загружена и совпадает — готово

        elapsed = time.time() - t0_wait
        if elapsed - last_emit >= 10.0:
            emit("  Unsloth Studio: модель грузится… {sec:.0f} с", sec=elapsed)
            last_emit = elapsed


def _gen_image_unsloth(prompt: str, prof: dict[str, Any], emit: Callable[..., Any] = console_emit) -> bytes:
    global _UNSLOTH_TIMER
    import base64
    root = _unsloth_root(prof)
    model_path, gguf = _parse_unsloth_model(prof.get("model") or "")
    t0 = time.time()
    with _UNSLOTH_LOCK:
        if _UNSLOTH_TIMER is not None:
            _UNSLOTH_TIMER.cancel()
            _UNSLOTH_TIMER = None

        try:
            _unsloth_ensure_loaded(root, model_path, gguf, emit=emit)

            if cancelled():
                raise ReelsiError(cancel_reason())

            gen_url = f"{root}/v1/images/generations"
            payload = {
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            }
            headers = {"Content-Type": "application/json"}
            if prof.get("api_key"):
                headers["Authorization"] = f"Bearer {prof['api_key']}"
            headers = apply_profile_headers(headers, prof)

            req = http_req(gen_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    resp = json.load(r)
            except urllib.error.HTTPError as e:
                last = _img_http_error(e, prof)
                raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после 1 попыток: {last}", tries=1, last=last))
            except (TimeoutError, socket.timeout) as e:
                _check_img_timeout(e)
                last = str(e)
                raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после 1 попыток: {last}", tries=1, last=last))
            except urllib.error.URLError as e:
                _check_img_timeout(e)
                last = str(e)
                raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после 1 попыток: {last}", tries=1, last=last))

            item = ((resp.get("data") or [{}])[0]) or {}
            b64 = item.get("b64_json")
            if b64:
                img_bytes = base64.b64decode(b64)
            elif item.get("url"):
                img_url = item["url"]
                if img_url.startswith("http://") or img_url.startswith("https://"):
                    # Абсолютный адрес — его назвал провайдер, а не пользователь:
                    # `file://` и `http://127.0.0.1` читали бы с диска и уводили запрос
                    # внутрь машины. Свой base_url (LM Studio на 127.0.0.1) сюда не
                    # попадает вовсе — ему соответствует ОТНОСИТЕЛЬНЫЙ путь ниже.
                    reason = unsafe_url_reason(img_url)
                    if reason:
                        raise ReelsiError(umsg("gen_failed",
                            f"генерация картинки упала после 1 попыток: {reason}",
                            tries=1, last=reason))
                    full_url = img_url
                else:
                    full_url = f"{root.rstrip('/')}/{img_url.lstrip('/')}"
                req_dl = http_req(full_url)
                with urllib.request.urlopen(req_dl, timeout=60) as r_dl:
                    img_bytes = r_dl.read()
            else:
                last = "Unsloth Studio не вернул картинку, ответ: " + json.dumps(resp, ensure_ascii=False)[:200]
                raise ReelsiError(umsg("gen_failed", f"генерация картинки упала после 1 попыток: {last}", tries=1, last=last))

            dt = time.time() - t0
            kb = max(1, len(img_bytes) // 1024)
            model_label = prof.get("model") or model_path
            emit("  картинка: {model} ({kb}КБ, локально, {sec:.0f} с)",
                 model=model_label, kb=kb, sec=dt)

            return img_bytes
        finally:
            if _UNSLOTH_OURS is not None:
                if _UNSLOTH_TIMER is not None:
                    _UNSLOTH_TIMER.cancel()
                _UNSLOTH_TIMER = threading.Timer(UNSLOTH_IDLE_SEC, unsloth_unload_ours)
                _UNSLOTH_TIMER.daemon = True
                _UNSLOTH_TIMER.start()


def unsloth_unload_ours(emit: Callable[..., Any] = console_emit) -> None:
    """Выгрузить модель из Unsloth Studio, если она была загружена Reelsi."""
    global _UNSLOTH_OURS, _UNSLOTH_TIMER
    if _UNSLOTH_OURS is None:
        return
    with _UNSLOTH_LOCK:
        if _UNSLOTH_OURS is None:
            return
        root, key = _UNSLOTH_OURS
        try:
            if _UNSLOTH_TIMER is not None:
                _UNSLOTH_TIMER.cancel()
                _UNSLOTH_TIMER = None
            model_path, _, gguf = key.partition("|")
            try:
                req = http_req(f"{root}/api/inference/images/status")
                with urllib.request.urlopen(req, timeout=30) as r:
                    st = json.load(r)
                if (st.get("loaded") and
                    st.get("repo_id") == model_path and
                    (not gguf or st.get("gguf_filename") == gguf)):
                    req_unload = http_req(f"{root}/api/inference/images/unload",
                                          data=b"{}",
                                          headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req_unload, timeout=60) as _:
                        pass
                    model_label = f"{model_path}/{gguf}" if gguf else model_path
                    emit("  Unsloth Studio: выгрузил {model}", model=model_label)
            except ReelsiError:
                raise
            except Exception as e:
                log.warning("Unsloth Studio: сбой выгрузки модели: %s", e)
        finally:
            _UNSLOTH_OURS = None
            if _UNSLOTH_TIMER is not None:
                _UNSLOTH_TIMER.cancel()
                _UNSLOTH_TIMER = None


def unsloth_cancel_ours() -> None:
    """Отменить текущую генерацию в Unsloth Studio, если есть _UNSLOTH_OURS."""
    ours = _UNSLOTH_OURS
    if not ours:
        return
    root, _ = ours
    try:
        req = http_req(f"{root}/api/inference/images/generate/cancel",
                       data=b"{}",
                       headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
    except ReelsiError:
        raise
    except Exception as e:
        log.warning("Unsloth Studio: сбой отмены генерации: %s", e)
