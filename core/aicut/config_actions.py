# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Действия над ai_config.json: по функции на действие POST /api/ai_config.

Роут был на триста строк со ста с лишним ветвлениями и вложенностью до шестнадцати —
это не HTTP-логика, а диспетчер по полю `act`. Диспетчер живёт здесь: одна функция на
действие, одна сигнатура `(cfg, d) -> None`. Функция меняет `cfg` на месте или бросает
тот же `ReelsiError(umsg(…))`, что бросал роут, — тот же код ошибки и те же переменные
(интерфейс переводит их по коду). Роут остаётся тонким: GET — ответ, POST — найти
действие в `ACTIONS`, выполнить, сохранить, ответить.

Побочные действия (проверка «слушает ли модель звук» у omni-профиля, валидация
ASR-движка по каталогу) остались ровно там же, где были в ветках, — ДО сохранения
конфига и в том же порядке относительно него.

`d` — тело запроса как есть: чужие поля не читаются, а нестроковое значение строкового
поля даёт пустую строку (контракт `api._core.jstr`; здесь своя копия `_s`, потому что
core не имеет права зависеть от api — api импортирует core, а не наоборот).
"""
import copy
import json
import urllib.request
from typing import Any

from core.app_meta import APP_NAME, APP_REFERER, http_req
from core.umsg import ReelsiError, umsg

from . import catalog
from .config import (GLITCH_GLOW_MODES, OMNI_LOCAL, OMNI_LOCAL_ENGINES, REASONING_LEVELS,
                     STEP_REASONING_DEFAULT, normalize_base_url, parse_headers_text,
                     step_profile, unmask_ai_key)
from .images import IMAGE_OFF
from .video import VIDEO_OFF, video_caps, video_model_cfg


def _s(d: Any, key: str) -> str:
    """Строковое поле тела запроса: значение-не-строка или нет ключа -> ""."""
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, str) else ""


def _omni_audio_check(prof: dict[str, Any]) -> str | None:
    """Умеет ли модель профиля СЛУШАТЬ звук — проверка ПРИ ВЫБОРЕ Omni-профиля, а не
    через 5 минут нарезки. Для OpenRouter модальности берём из их публичного /models
    (architecture.input_modalities). Оффлайн/не нашли модель -> не блокируем (None)."""
    if (prof.get("provider") or "") != "openrouter":
        return None                                       # свой сервер — не знаем, не блокируем
    mid = (prof.get("model") or "").split(":")[0].strip()
    if not mid:
        return None
    # чистые ASR-модели (работают через /audio/transcriptions, а не /chat) — не блокируем:
    # у них аудио может не значиться во входных модальностях chat-эндпоинта
    if any(k in mid.lower() for k in ("asr", "parakeet", "whisper", "transcri", "-stt", "speech")):
        return None
    try:
        req = http_req("https://openrouter.ai/api/v1/models",
                       headers={"X-Title": APP_NAME, "HTTP-Referer": APP_REFERER})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
        for m in data.get("data", []):
            if (m.get("id") or "").split(":")[0] == mid:
                mods = ((m.get("architecture") or {}).get("input_modalities")) or []
                if "audio" not in mods:
                    return (f"Модель «{mid}» текстовая — звук не принимает "
                            f"(вход: {', '.join(mods) or '?'}). Для Omni нужна аудио-модель, "
                            f"например google/gemini-2.5-flash")
                return None
    except ReelsiError: raise
    except Exception:
        pass                                              # сеть недоступна — не блокируем выбор
    return None


def set_reasoning_step(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Уровень «ума» ОТДЕЛЬНО на каждый шаг: нарезке думать надо, жёлтым/вставкам/интро —
    нет (там reasoning жёг весь бюджет впустую). Принимаем и уровни из каталога
    (max/xhigh/minimal у разных моделей), иначе выбор «max» в селекте упал бы на валидации."""
    step, lvl = _s(d, "step"), _s(d, "level")
    if step not in STEP_REASONING_DEFAULT:
        raise ReelsiError(umsg("unknown_step", f"Неизвестный шаг «{step}»", step=step))
    valid = lvl in REASONING_LEVELS
    if not valid:
        prof = step_profile(step)
        pp = (cfg.get("profiles") or {}).get(prof) or {}
        valid = catalog.valid_level(pp.get("provider"), pp.get("model"), lvl)
    if not valid:
        raise ReelsiError(umsg("unknown_reasoning_level", f"Неизвестный уровень «{lvl}»",
                              lvl=lvl))
    cfg.setdefault("reasoning_steps", {})[step] = lvl
    # Память уровня НА МОДЕЛЬ (задание по UI-состояниям): сменил модель у шага —
    # увидишь уровень, который выбирал ДЛЯ НЕЁ. Ключ — id модели профиля шага
    # как есть, без нормализации. Модель не определилась (битый конфиг) —
    # пишем только в reasoning_steps, как раньше.
    model = (cfg.get("profiles") or {}).get(step_profile(step), {}).get("model")
    if model:
        cfg.setdefault("reasoning_by_model", {}).setdefault(model, {})[step] = lvl


def set_step_profile(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """МОДЕЛЬ (ИИ-профиль) ОТДЕЛЬНО на каждый шаг (нарезка/жёлтые/вставки/интро):
    шагам нужны разные модели. Пустое имя = «как общий active» (сброс)."""
    step, name = _s(d, "step"), _s(d, "name")
    if step not in STEP_REASONING_DEFAULT:
        raise ReelsiError(umsg("unknown_step", f"Неизвестный шаг «{step}»", step=step))
    sp = cfg.setdefault("step_profiles", {})
    if name and name not in cfg["profiles"]:
        raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
    if name:
        sp[step] = name
    else:
        sp.pop(step, None)          # сброс на общий active


def set_active(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Общий активный профиль: модель по умолчанию для всех шагов."""
    name = _s(d, "name")
    if name not in cfg["profiles"]:
        raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
    cfg["active"] = name


def set_active_omni(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Кто СЛУШАЕТ звук: "__local__" = локальная Qwen2.5-Omni, иначе имя профиля
    (облачный = аудио-чанки уходят провайдеру; anthropic звук не принимает)."""
    name = _s(d, "name") or OMNI_LOCAL
    if name not in OMNI_LOCAL_ENGINES:      # локальные движки (qwen/gigaam) — ок
        if name not in cfg["profiles"]:
            raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
        prof = cfg["profiles"][name]
        if (prof.get("provider") or "") == "anthropic":
            raise ReelsiError(umsg("claude_no_audio",
                "Claude API не принимает аудио — выбери аудио-модель "
                "(например Gemini на OpenRouter) или «Локально»"))
        err = _omni_audio_check(prof)
        if err:
            raise ReelsiError(umsg("omni_audio_unsupported", err, err=err))
    cfg["active_omni"] = name


def set_active_cut_asr(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Кто СЛУШАЕТ звук при нарезке (пословные тайминги): дефолт "gigaam",
    валидируется по флагу cut в каталоге asr_backends."""
    name = _s(d, "name") or "gigaam"
    from core import asr_backends
    meta = asr_backends.engine_meta(name)
    if not meta or not meta.get("cut"):
        raise ReelsiError(umsg("invalid_cut_asr", f"Движок «{name}» не годен для нарезки",
                              name=name))
    cfg["active_cut_asr"] = name


def set_active_image(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Кто ГЕНЕРИТ картинки-вставки: "__off__" = выключено, иначе имя профиля
    с image-моделью (Nano Banana); anthropic/lmstudio не умеют."""
    name = _s(d, "name") or IMAGE_OFF
    if name != IMAGE_OFF:
        if name not in cfg["profiles"]:
            raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
        if (cfg["profiles"][name].get("provider") or "") in ("anthropic", "lmstudio"):
            raise ReelsiError(umsg("provider_no_images",
                "Этот провайдер не генерит картинки — нужен "
                "OpenRouter/OpenAI-совместимый с image-моделью", provider=name))
    cfg["active_image"] = name


def set_active_video(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Кто ГЕНЕРИТ видео: "__off__" = выключено, иначе имя профиля с видео-
    моделью (Seedance 2 на OpenRouter); anthropic/lmstudio не умеют."""
    name = _s(d, "name") or VIDEO_OFF
    if name != VIDEO_OFF:
        if name not in cfg["profiles"]:
            raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
        if (cfg["profiles"][name].get("provider") or "") in ("anthropic", "lmstudio"):
            raise ReelsiError(umsg("provider_no_video",
                "Этот провайдер не генерит видео — нужен "
                "OpenRouter/совместимый с видео-моделью", provider=name))
    cfg["active_video"] = name


def set_video_model(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Модель видео выбирается в общей секции ⚙ «Разметка и AE», а не в профиле:
    у Seedance/Veo/Hailuo/Kling разные возможности. Профиль остаётся «провайдер + ключ»
    для вкладки Видео и карточек вставок. Смена модели АТОМАРНО сбрасывает устаревшее
    разрешение: старое может оказаться несовместимым с возможностями новой (известной)
    модели, и иначе запрос улетел бы с невалидным size. Неизвестную модель не трогаем —
    провайдер сам решит."""
    new_model = _s(d, "model").strip()
    old_model = video_model_cfg()
    cfg["video_model"] = new_model
    saved = str(cfg.get("video_resolution") or "").strip()
    if saved and new_model and new_model.lower() != old_model.lower():
        caps = video_caps(new_model)
        if caps is not None and caps.get("resolutions"):
            if saved.lower() not in {str(r).lower() for r in caps["resolutions"]}:
                cfg["video_resolution"] = ""


def set_video_resolution(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Общее разрешение генерации видео (в ⚙ «Разметка и AE»). Пусто = провайдер решает
    сам. Для известной модели с caps непустое значение проверяем на поддержку ДО записи:
    устаревшее/несуществующее не сохраняем (structured error), иначе оно разъехалось бы
    с резолвером и молча не отправилось бы."""
    res = _s(d, "resolution").strip()
    model = video_model_cfg()
    caps = video_caps(model)
    if res and caps is not None and caps.get("resolutions"):
        low = {str(r).lower() for r in caps["resolutions"]}
        if res.lower() not in low:
            raise ReelsiError(umsg("video_resolution_unsupported",
                f"Модель «{model}» не поддерживает разрешение «{res}» — можно: "
                + ", ".join(caps["resolutions"]),
                model=model, resolution=res, list=", ".join(caps["resolutions"])))
    cfg["video_resolution"] = res


def set_image_rembg(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Убирать ли фон у сгенерённого (rembg): картинка ложится в базу уже с альфой."""
    cfg["image_rembg"] = bool(d.get("value"))


def set_glitch_glow(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Режим свечения жёлтого глитча интро: встроенные эффекты или Deep Glow 2."""
    val = d.get("value")
    if val not in GLITCH_GLOW_MODES:
        raise ReelsiError(umsg("glitch_glow_mode_invalid",
            f"Недопустимый режим свечения глитча «{val}» — можно: "
            + ", ".join(GLITCH_GLOW_MODES),
            mode=val, list=", ".join(GLITCH_GLOW_MODES)))
    cfg["glitch_glow"] = val


def save_profile(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Профиль из формы: провайдер, адрес, ключ, модель, свои заголовки.

    Ключ-маска «•••…» значит «ключ не менял». Но base_url и provider приходят из ТЕЛА
    запроса: подставить к ним настоящий сохранённый ключ — это увести ключ прежнего
    провайдера на чужой адрес. Штатный сценарий был именно такой:
    сменил провайдера в списке (aiSetProv меняет URL, маска остаётся), сохранил — и
    ключ уехал на новый адрес."""
    name = _s(d, "name").strip()
    if not name:
        raise ReelsiError(umsg("empty_profile_name", "Пустое имя профиля"))
    p = d.get("profile")
    if p is not None and not isinstance(p, dict):
        raise ReelsiError(umsg("bad_profile", "Поле profile должно быть объектом"))
    p = p or {}
    old_name = _s(d, "old_name") or None
    saved_prof = cfg["profiles"].get(old_name or name) or {}
    provider = _s(p, "provider") or "lmstudio"
    base_url = normalize_base_url(_s(p, "base_url"))
    saved_url = normalize_base_url(_s(saved_prof, "base_url"))
    key_in = _s(p, "api_key").strip()
    if (key_in.startswith("•••") and saved_prof
            and (base_url != saved_url
                 or provider != (saved_prof.get("provider") or "lmstudio"))):
        raise ReelsiError(umsg("key_mask_address_changed",
            "Сменился адрес или провайдер — введи ключ заново: сохранённый ключ "
            "к новому адресу не подставляется"))
    newp: dict[str, Any] = {"provider": provider,
            "base_url": base_url,
            "api_key": unmask_ai_key(key_in, old_name or name),
            "model": _s(p, "model").strip()}
    hdrs = (p.get("headers") if isinstance(p.get("headers"), dict)
            else parse_headers_text(_s(p, "headers_text")))
    if hdrs:
        newp["headers"] = hdrs
    # «Ум» в профиле больше не редактируется (переехал на страницы, по шагам),
    # но старое значение не затираем: вдруг вернёмся к профильному уровню
    _old = cfg["profiles"].get(old_name or name) or {}
    if _old.get("reasoning"):
        newp["reasoning"] = _old["reasoning"]
    if old_name and old_name != name and old_name in cfg["profiles"]:
        del cfg["profiles"][old_name]          # переименование
        if cfg.get("active") == old_name:
            cfg["active"] = name
        # активные привязки к старому имени переезжают на новое (без этого
        # переименованный профиль молча отключал генерацию картинки/видео)
        for k in ("active_omni", "active_image", "active_video"):
            if cfg.get(k) == old_name:
                cfg[k] = name
        # пошаговые привязки к старому имени переезжают на новое
        sp = cfg.get("step_profiles") or {}
        for k, v in list(sp.items()):
            if v == old_name:
                sp[k] = name
    cfg["profiles"][name] = newp
    if d.get("set_active") or cfg.get("active") not in cfg["profiles"]:
        cfg["active"] = name


def clone_profile(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Копия профиля под новым именем: ключи и заголовки переносятся как есть."""
    name = _s(d, "name").strip()
    new_name = _s(d, "new_name").strip()
    if not name:
        raise ReelsiError(umsg("empty_profile_name", "Пустое имя профиля"))
    if not new_name:
        raise ReelsiError(umsg("empty_clone_name", "Пустое имя для копии профиля"))
    if name not in cfg.get("profiles", {}):
        raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
    if new_name in cfg.get("profiles", {}):
        raise ReelsiError(umsg("profile_exists", f"Профиль «{new_name}» уже существует",
                              name=new_name))
    cfg["profiles"][new_name] = copy.deepcopy(cfg["profiles"][name])


def delete_profile(cfg: dict[str, Any], d: dict[str, Any]) -> None:
    """Удаление профиля: привязки (общий active, шаги, omni, картинки, видео) не должны
    остаться на несуществующем имени — иначе шаг молча падал бы на пустом профиле."""
    name = _s(d, "name")
    if name not in cfg["profiles"]:
        raise ReelsiError(umsg("no_profile", f"Нет профиля «{name}»", name=name))
    if len(cfg["profiles"]) <= 1:
        raise ReelsiError(umsg("last_profile", "Нельзя удалить последний профиль"))
    del cfg["profiles"][name]
    if cfg.get("active") == name:
        cfg["active"] = next(iter(cfg["profiles"]))
    if cfg.get("active_omni") == name:
        cfg["active_omni"] = OMNI_LOCAL   # слух вернулся на локальную Omni
    if cfg.get("active_image") == name:
        cfg["active_image"] = IMAGE_OFF   # генерация картинок выключилась
    if cfg.get("active_video") == name:
        cfg["active_video"] = VIDEO_OFF   # генерация видео выключилась
    sp = cfg.get("step_profiles") or {}         # пошаговые привязки к удалённому
    for k in [k for k, v in sp.items() if v == name]:
        sp.pop(k, None)                          # -> откат шага на active


# Действия POST /api/ai_config: ключ — значение поля `action` в теле запроса. Таблица —
# единственный диспетчер: неизвестное действие роут отбивает той же ошибкой, что и
# раньше (unknown_action с именем действия в переменных).
ACTIONS = {
    "set_reasoning_step": set_reasoning_step,
    "set_step_profile": set_step_profile,
    "set_active": set_active,
    "set_active_omni": set_active_omni,
    "set_active_cut_asr": set_active_cut_asr,
    "set_active_image": set_active_image,
    "set_active_video": set_active_video,
    "set_video_model": set_video_model,
    "set_video_resolution": set_video_resolution,
    "set_image_rembg": set_image_rembg,
    "set_glitch_glow": set_glitch_glow,
    "save_profile": save_profile,
    "clone_profile": clone_profile,
    "delete_profile": delete_profile,
}
