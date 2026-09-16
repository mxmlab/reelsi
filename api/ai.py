# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ИИ: профили провайдеров, ключи, модели и одиночные вызовы (жёлтые/вставки/интро).

Ключи наружу отдаются ТОЛЬКО маской `•••xxxx` (_mask_ai_key), обратно принимаются либо
новые целиком, либо та же маска — тогда берётся сохранённый (_unmask_ai_key).
"""
import os, json, threading, time
import urllib.request
from flask import request, jsonify
from ._core import APP_NAME, APP_REFERER, _ai_begin, _ai_end, bp, emit, umsg_err, jstr
from .inserts import _insert_dest
from core.umsg import umsg
from core.app_meta import http_req, t






@bp.route("/api/ai_yellow", methods=["POST"])
def api_ai_yellow():
    """Ask the local LM Studio model to pick yellow-highlight words for an edited
    sequence XML. Writes <stem>.yellow.json next to it and returns the indices so
    the UI can load them into the highlight chips right away."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        ep = _ai_begin("жёлтые")
        try:
            from core import aicut
            res = aicut.cmd_yellow(xml_path, model=(jstr(d, "model") or None),
                                   url=(jstr(d, "url") or None), emit=emit)
            return jsonify(ok=True, yellow=res["yellow"], colored=res.get("colored", []),
                           total=res["total"])
        except SystemExit as e:      # LM Studio недоступен / отказ модели
            return jsonify(**umsg_err(e))
        except Exception as e:
            raise SystemExit(umsg("yellow_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        finally:
            _ai_end(ep)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/ai_inserts", methods=["POST"])
def api_ai_inserts():
    """Local LLM предлагает вставки (10 фото + 3 видео) по субтитрам XML. Выгружает
    модель после. Возвращает список для меню вставок."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        ep = _ai_begin("вставки")
        unload = False
        try:
            from core import aicut
            notes = []                      # сдвиги таймингов/зон видны в UI-логе, а не глушатся
            def _emit(line="", **vars):
                s = t(line, **vars) if line else ""
                notes.append(s)
                emit(line, **vars)         # дублируем в JOB-лог (серверный прогресс стриминга)

            res = aicut.cmd_inserts(xml_path, model=(jstr(d, "model") or None),
                                    count=d.get("count"), avoid=d.get("avoid"),
                                    rejected=d.get("rejected"),
                                    emit=_emit)
            unload = True
            # ins_target — цель набора (от длительности ролика): фронт хранит её в
            # c.insTarget и по ней считает «добрать», не зашивая 13 в JS.
            return jsonify(ok=True, inserts=res["inserts"], insTarget=res["ins_target"],
                           log=notes)
        except SystemExit as e:
            return jsonify(**umsg_err(e))
        except Exception as e:
            raise SystemExit(umsg("inserts_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        finally:
            _ai_end(ep, unload=unload)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


# ---- профили ИИ-провайдеров (шестерёнка в webui, как в Roo Code) ----------
def _mask_ai_key(k):
    from core import aicut
    if k and aicut.key_env_name(k) is not None:
        return k
    return ("•••" + k[-4:]) if k else ""


def _masked_profiles(cfg):
    from core import aicut
    res = {}
    for n, p in (cfg.get("profiles") or {}).items():
        k = p.get("api_key") or ""
        env_name = aicut.key_env_name(k)
        item = {**p, "api_key": _mask_ai_key(k)}
        if env_name is not None:
            val = os.environ.get(env_name, "")
            item["key_env_ok"] = bool(val.strip() if isinstance(val, str) else val)
        res[n] = item
    return res


def _unmask_ai_key(key, saved_name):
    """Ключ из формы: маска «•••…» = «не менял» -> вернуть сохранённый ключ профиля."""
    from core import aicut
    key = (key or "").strip()
    if key.startswith("•••"):
        saved = aicut.load_ai_config()["profiles"].get(saved_name or "", {})
        return saved.get("api_key") or ""
    return key


def _saved_profile_for_masked(p, name):
    """Профиль из формы с ключом-маской: отдать СОХРАНЁННЫЙ профиль целиком, иначе None.

    Маска значит «ключ не менял», но base_url и headers из тела запроса — это данные
    запроса, их подставляет кто угодно, а ключ к ним подставлялся настоящий. Снаружи
    это закрыто гвардом Sec-Fetch-Site, но вместе с XSS в интерфейсе (задание HL)
    давало увод сохранённого ключа на чужой адрес: /api/ai_test и /api/ai_models ходят
    туда, куда сказано в теле. Профиля нет — None, поведение как раньше.
    """
    if not (p.get("api_key") or "").strip().startswith("•••"):
        return None
    from core import aicut
    return aicut.load_ai_config()["profiles"].get(name or "") or None


def _omni_audio_check(prof):
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
    except Exception:
        pass                                              # сеть недоступна — не блокируем выбор
    return None


@bp.route("/api/ai_config", methods=["GET", "POST"])
def api_ai_config():
    """Профили ИИ-провайдеров. GET — {active, profiles(ключи маскированы), presets}.
    POST {action}: set_active {name} · save_profile {name, old_name?, profile,
    set_active?} · delete_profile {name}. Ключи живут ТОЛЬКО в ai_config.json на
    сервере (файл в .gitignore)."""
    from core import aicut
    if request.method == "GET":
        cfg = aicut.load_ai_config()
        # Возможности моделей профилей из каталога models.dev (задание BY): по ним
        # фронт показывает в селекте «Ум» уровни из каталога, а рядом с моделью —
        # контекст, потолок вывода и цену. Каталога нет/модель неизвестна — пусто,
        # фронт живёт по своим фолбэкам.
        model_caps = {}
        for p in (cfg.get("profiles") or {}).values():
            mid = (p.get("model") or "").strip()
            if not mid:
                continue
            model_caps[mid.lower()] = aicut.catalog.caps(p.get("provider"), mid)
        return jsonify(active=cfg.get("active"), profiles=_masked_profiles(cfg),
                       active_omni=cfg.get("active_omni") or aicut.OMNI_LOCAL,
                       active_cut_asr=cfg.get("active_cut_asr") or "gigaam",
                       active_image=cfg.get("active_image") or aicut.IMAGE_OFF,
                       active_video=cfg.get("active_video") or aicut.VIDEO_OFF,
                       image_model_hints=aicut.IMAGE_MODEL_HINTS,
                       video_model_hints=aicut.VIDEO_MODEL_HINTS,
                       video_model=aicut.video_model_cfg(),
                       video_resolution=aicut.video_resolution_cfg(aicut.video_model_cfg()),
                       video_models=aicut.video_model_list(),
                       video_resolutions=list(aicut.VIDEO_RESOLUTIONS),
                       video_aspects=list(aicut.VIDEO_ASPECTS),
                       image_rembg=aicut.image_rembg_on(),
                       glitch_glow=aicut.glitch_glow_mode(),
                       presets=aicut.PROVIDER_PRESETS,
                       reasoning_levels=list(aicut.REASONING_LEVELS),
                       reasoning_steps={k: aicut.step_reasoning(k)
                                        for k in aicut.STEP_REASONING_DEFAULT},
                       # Что РЕАЛЬНО уйдёт в API (после понижения невалидного уровня
                       # под модель из каталога) — единственное, что показывает интерфейс.
                       reasoning_effective={k: aicut.effective_step_reasoning(k)
                                            for k in aicut.STEP_REASONING_DEFAULT},
                       step_profiles={k: aicut.step_profile(k)
                                      for k in aicut.STEP_REASONING_DEFAULT},
                       reasoning_step_titles=aicut.STEP_TITLES,
                       reasoning_models=list(aicut.REASONING_MODELS),
                       reasoning_examples=list(aicut.REASONING_EXAMPLES),
                       reasoning_defaults={k: v.get("default")
                                           for k, v in model_caps.items()
                                           if v.get("default") is not None},
                       model_caps=model_caps)
    d = request.get_json() or {}
    act = d.get("action")
    cfg = aicut.load_ai_config()
    try:
        if act == "set_reasoning_step":
            # уровень «ума» ОТДЕЛЬНО на каждый шаг: нарезке думать надо, жёлтым/
            # вставкам/интро — нет (там reasoning жёг весь бюджет впустую). Принимаем
            # и уровни из каталога (max/xhigh/minimal у разных моделей), иначе выбор
            # «max» в селекте упал бы на валидации.
            step, lvl = jstr(d, "step"), jstr(d, "level")
            if step not in aicut.STEP_REASONING_DEFAULT:
                raise SystemExit(umsg("unknown_step", f"Неизвестный шаг «{step}»", step=step))
            valid = lvl in aicut.REASONING_LEVELS
            if not valid:
                prof = aicut.step_profile(step)
                pp = (cfg.get("profiles") or {}).get(prof) or {}
                valid = aicut.catalog.valid_level(pp.get("provider"), pp.get("model"), lvl)
            if not valid:
                raise SystemExit(umsg("unknown_reasoning_level", f"Неизвестный уровень «{lvl}»",
                                      lvl=lvl))
            cfg.setdefault("reasoning_steps", {})[step] = lvl
            # Память уровня НА МОДЕЛЬ (задание по UI-состояниям): сменил модель у шага —
            # увидишь уровень, который выбирал ДЛЯ НЕЁ. Ключ — id модели профиля шага
            # как есть, без нормализации. Модель не определилась (битый конфиг) —
            # пишем только в reasoning_steps, как раньше.
            model = (cfg.get("profiles") or {}).get(aicut.step_profile(step), {}).get("model")
            if model:
                cfg.setdefault("reasoning_by_model", {}).setdefault(model, {})[step] = lvl
        elif act == "set_step_profile":
            # МОДЕЛЬ (ИИ-профиль) ОТДЕЛЬНО на каждый шаг (нарезка/жёлтые/вставки/интро):
            # шагам нужны разные модели. Пустое имя = «как общий active» (сброс).
            step, name = jstr(d, "step"), jstr(d, "name")
            if step not in aicut.STEP_REASONING_DEFAULT:
                raise SystemExit(umsg("unknown_step", f"Неизвестный шаг «{step}»", step=step))
            sp = cfg.setdefault("step_profiles", {})
            if name and name not in cfg["profiles"]:
                raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
            if name:
                sp[step] = name
            else:
                sp.pop(step, None)          # сброс на общий active
        elif act == "set_active":
            name = jstr(d, "name")
            if name not in cfg["profiles"]:
                raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
            cfg["active"] = name
        elif act == "set_active_omni":
            # кто СЛУШАЕТ звук: "__local__" = локальная Qwen2.5-Omni, иначе имя профиля
            # (облачный = аудио-чанки уходят провайдеру; anthropic звук не принимает)
            name = jstr(d, "name") or aicut.OMNI_LOCAL
            if name not in aicut.OMNI_LOCAL_ENGINES:      # локальные движки (qwen/gigaam) — ок
                if name not in cfg["profiles"]:
                    raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
                prof = cfg["profiles"][name]
                if (prof.get("provider") or "") == "anthropic":
                    raise SystemExit(umsg("claude_no_audio",
                        "Claude API не принимает аудио — выбери аудио-модель "
                        "(например Gemini на OpenRouter) или «Локально»"))
                err = _omni_audio_check(prof)
                if err:
                    raise SystemExit(umsg("omni_audio_unsupported", err, err=err))
            cfg["active_omni"] = name
        elif act == "set_active_cut_asr":
            # кто СЛУШАЕТ звук при нарезке (пословные тайминги): дефолт "gigaam",
            # валидируется по флагу cut в каталоге asr_backends
            name = jstr(d, "name") or "gigaam"
            from core import asr_backends
            meta = asr_backends.engine_meta(name)
            if not meta or not meta.get("cut"):
                raise SystemExit(umsg("invalid_cut_asr", f"Движок «{name}» не годен для нарезки", name=name))
            cfg["active_cut_asr"] = name
        elif act == "set_active_image":
            # кто ГЕНЕРИТ картинки-вставки: "__off__" = выключено, иначе имя профиля
            # с image-моделью (Nano Banana); anthropic/lmstudio не умеют
            name = jstr(d, "name") or aicut.IMAGE_OFF
            if name != aicut.IMAGE_OFF:
                if name not in cfg["profiles"]:
                    raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
                if (cfg["profiles"][name].get("provider") or "") in ("anthropic", "lmstudio"):
                    raise SystemExit(umsg("provider_no_images",
                        "Этот провайдер не генерит картинки — нужен "
                        "OpenRouter/OpenAI-совместимый с image-моделью", provider=name))
            cfg["active_image"] = name
        elif act == "set_active_video":
            # кто ГЕНЕРИТ видео: "__off__" = выключено, иначе имя профиля с видео-
            # моделью (Seedance 2 на OpenRouter); anthropic/lmstudio не умеют
            name = jstr(d, "name") or aicut.VIDEO_OFF
            if name != aicut.VIDEO_OFF:
                if name not in cfg["profiles"]:
                    raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
                if (cfg["profiles"][name].get("provider") or "") in ("anthropic", "lmstudio"):
                    raise SystemExit(umsg("provider_no_video",
                        "Этот провайдер не генерит видео — нужен "
                        "OpenRouter/совместимый с видео-моделью", provider=name))
            cfg["active_video"] = name
        elif act == "set_video_model":
            # Модель видео выбирается в общей секции ⚙ «Разметка и AE», а не в
            # профиле: у Seedance/Veo/Hailuo/Kling разные возможности. Профиль
            # остаётся «провайдер + ключ» для вкладки Видео и карточек вставок.
            # Смена модели АТОМАРНО сбрасывает устаревшее разрешение: старое может
            # оказаться несовместимым с возможностями новой (известной) модели, и
            # иначе запрос улетел бы с невалидным size. Неизвестную модель не
            # трогаем — провайдер сам решит.
            new_model = jstr(d, "model").strip()
            old_model = aicut.video_model_cfg()
            cfg["video_model"] = new_model
            saved = str(cfg.get("video_resolution") or "").strip()
            if saved and new_model and new_model.lower() != old_model.lower():
                caps = aicut.video_caps(new_model)
                if caps is not None and caps.get("resolutions"):
                    if saved.lower() not in {str(r).lower() for r in caps["resolutions"]}:
                        cfg["video_resolution"] = ""
        elif act == "set_video_resolution":
            # Общее разрешение генерации видео (в ⚙ «Разметка и AE»). Пусто = провайдер
            # решает сам. Для известной модели с caps непустое значение проверяем на
            # поддержку ДО записи: устаревшее/несуществующее не сохраняем (structured
            # error), иначе оно разъехалось бы с резолвером и молча не отправилось бы.
            res = jstr(d, "resolution").strip()
            model = aicut.video_model_cfg()
            caps = aicut.video_caps(model)
            if res and caps is not None and caps.get("resolutions"):
                low = {str(r).lower() for r in caps["resolutions"]}
                if res.lower() not in low:
                    raise SystemExit(umsg("video_resolution_unsupported",
                        f"Модель «{model}» не поддерживает разрешение «{res}» — можно: "
                        + ", ".join(caps["resolutions"]),
                        model=model, resolution=res, list=", ".join(caps["resolutions"])))
            cfg["video_resolution"] = res
        elif act == "set_image_rembg":
            # убирать ли фон у сгенерённого (rembg): картинка ложится в базу уже с альфой
            cfg["image_rembg"] = bool(d.get("value"))
        elif act == "set_glitch_glow":
            val = d.get("value")
            if val not in aicut.GLITCH_GLOW_MODES:
                raise SystemExit(umsg("glitch_glow_mode_invalid",
                    f"Недопустимый режим свечения глитча «{val}» — можно: "
                    + ", ".join(aicut.GLITCH_GLOW_MODES),
                    mode=val, list=", ".join(aicut.GLITCH_GLOW_MODES)))
            cfg["glitch_glow"] = val
        elif act == "save_profile":
            name = jstr(d, "name").strip()
            if not name:
                raise SystemExit(umsg("empty_profile_name", "Пустое имя профиля"))
            p = d.get("profile")
            if p is not None and not isinstance(p, dict):
                raise SystemExit(umsg("bad_profile", "Поле profile должно быть объектом"))
            p = p or {}
            old_name = jstr(d, "old_name") or None
            # Ключ-маска «•••…» значит «ключ не менял». Но base_url и provider приходят
            # из ТЕЛА запроса: подставить к ним настоящий сохранённый ключ — это увести
            # ключ прежнего провайдера на чужой адрес (задание IC, п. 10). Штатный
            # сценарий был именно такой: сменил провайдера в списке (aiSetProv меняет
            # URL, маска остаётся), сохранил — и ключ уехал на новый адрес.
            saved_prof = cfg["profiles"].get(old_name or name) or {}
            provider = jstr(p, "provider") or "lmstudio"
            base_url = aicut.normalize_base_url(jstr(p, "base_url"))
            key_in = jstr(p, "api_key").strip()
            if (key_in.startswith("•••") and saved_prof
                    and (base_url != (saved_prof.get("base_url") or "")
                         or provider != (saved_prof.get("provider") or "lmstudio"))):
                raise SystemExit(umsg("key_mask_address_changed",
                    "Сменился адрес или провайдер — введи ключ заново: сохранённый ключ "
                    "к новому адресу не подставляется"))
            newp = {"provider": provider,
                    "base_url": base_url,
                    "api_key": _unmask_ai_key(key_in, old_name or name),
                    "model": jstr(p, "model").strip()}
            hdrs = (p.get("headers") if isinstance(p.get("headers"), dict)
                    else aicut.parse_headers_text(jstr(p, "headers_text")))
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
        elif act == "clone_profile":
            name = jstr(d, "name").strip()
            new_name = jstr(d, "new_name").strip()
            if not name:
                raise SystemExit(umsg("empty_profile_name", "Пустое имя профиля"))
            if not new_name:
                raise SystemExit(umsg("empty_clone_name", "Пустое имя для копии профиля"))
            if name not in cfg.get("profiles", {}):
                raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
            if new_name in cfg.get("profiles", {}):
                raise SystemExit(umsg("profile_exists", f"Профиль «{new_name}» уже существует", name=new_name))
            import copy
            cfg["profiles"][new_name] = copy.deepcopy(cfg["profiles"][name])
        elif act == "delete_profile":
            name = jstr(d, "name")
            if name not in cfg["profiles"]:
                raise SystemExit(umsg("no_profile", f"Нет профиля «{name}»", name=name))
            if len(cfg["profiles"]) <= 1:
                raise SystemExit(umsg("last_profile", "Нельзя удалить последний профиль"))
            del cfg["profiles"][name]
            if cfg.get("active") == name:
                cfg["active"] = next(iter(cfg["profiles"]))
            if cfg.get("active_omni") == name:
                cfg["active_omni"] = aicut.OMNI_LOCAL   # слух вернулся на локальную Omni
            if cfg.get("active_image") == name:
                cfg["active_image"] = aicut.IMAGE_OFF   # генерация картинок выключилась
            if cfg.get("active_video") == name:
                cfg["active_video"] = aicut.VIDEO_OFF   # генерация видео выключилась
            sp = cfg.get("step_profiles") or {}         # пошаговые привязки к удалённому
            for k in [k for k, v in sp.items() if v == name]:
                sp.pop(k, None)                          # -> откат шага на active
        else:
            raise SystemExit(umsg("unknown_action", f"Неизвестное действие: {act}", act=act))
        aicut.save_ai_config(cfg)
        return jsonify(ok=True, active=cfg["active"], profiles=_masked_profiles(cfg),
                       active_omni=cfg.get("active_omni") or aicut.OMNI_LOCAL,
                       active_cut_asr=cfg.get("active_cut_asr") or "gigaam",
                       active_image=cfg.get("active_image") or aicut.IMAGE_OFF,
                       active_video=cfg.get("active_video") or aicut.VIDEO_OFF,
                       video_model=aicut.video_model_cfg(),
                       video_resolution=aicut.video_resolution_cfg(aicut.video_model_cfg()),
                       image_rembg=bool(cfg.get("image_rembg", True)),
                       glitch_glow=cfg.get("glitch_glow") if cfg.get("glitch_glow") in aicut.GLITCH_GLOW_MODES else "builtin",
                       reasoning_steps={k: aicut.step_reasoning(k)
                                        for k in aicut.STEP_REASONING_DEFAULT},
                       # Что РЕАЛЬНО уйдёт в API (после понижения невалидного уровня
                       # под модель из каталога) — единственное, что показывает интерфейс.
                       reasoning_effective={k: aicut.effective_step_reasoning(k)
                                            for k in aicut.STEP_REASONING_DEFAULT},
                       step_profiles={k: aicut.step_profile(k)
                                      for k in aicut.STEP_REASONING_DEFAULT})
    except SystemExit as e:
        return jsonify(**umsg_err(e))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("ai_config_failed", f"{type(e).__name__}: {e}",
                                                  err=f"{type(e).__name__}: {e}"))))


@bp.route("/api/ai_test", methods=["POST"])
def api_ai_test():
    """Кнопка «Проверить»: мини-вызов через выбранный бэкенд. Тестирует данные из
    формы (body.profile, ключ-маска подменяется сохранённым) или активный профиль."""
    from core import aicut
    import time as _t
    d = request.get_json() or {}
    p = d.get("profile")
    if p is not None and not isinstance(p, dict):
        return jsonify(**umsg_err(SystemExit(umsg("bad_profile",
                                                  "Поле profile должно быть объектом"))))
    try:
        if p:
            # Ключ-маска: ключ, адрес и заголовки берём из сохранённого профиля ЦЕЛИКОМ,
            # значения формы для них игнорируем (см. _saved_profile_for_masked) — иначе
            # чужой base_url в теле уводил настоящий ключ на свой адрес.
            saved = _saved_profile_for_masked(p, jstr(d, "name"))
            if saved:
                raw_key = saved.get("api_key")
                raw_base = saved.get("base_url")
                hdrs = saved.get("headers") if isinstance(saved.get("headers"), dict) else None
            else:
                # профиля нет (маска при чужом имени) — прежнее поведение: ключ из формы,
                # а маска остаётся пустым ключом, а не уезжает провайдеру как есть
                raw_key = _unmask_ai_key(p.get("api_key"), jstr(d, "name"))
                raw_base = p.get("base_url")
                hdrs = p.get("headers") if isinstance(p.get("headers"), dict) else aicut.parse_headers_text(p.get("headers_text"))
            prof = {"provider": p.get("provider") or "lmstudio",
                    "base_url": aicut.normalize_base_url(raw_base or ""),
                    "api_key": aicut.resolve_key(raw_key),
                    "model": (p.get("model") or "").strip(),
                    "reasoning": (p.get("reasoning") or "off"),
                    "name": jstr(d, "name") or "(тест)"}
            if hdrs:
                prof["headers"] = hdrs
        else:
            prof = aicut.resolve_profile()
        if not prof["model"]:
            raise SystemExit(umsg("model_not_set", "Не указана модель"))
        aicut.clear_cancel()
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"], "additionalProperties": False}
        t0 = _t.time()
        ask = aicut._ask_anthropic if prof["provider"] == "anthropic" else aicut._ask_openai
        res = ask(prof, "Ты — проверка связи.", 'Ответь ровно {"ok": true}', schema,
                  max_tokens=2000, emit=lambda *a, **k: None, retries=0)
        return jsonify(ok=bool(res.get("ok")), ms=int((_t.time() - t0) * 1000))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("ai_test_failed", f"{type(e).__name__}: {e}",
                                                  err=f"{type(e).__name__}: {e}"))))


@bp.route("/api/ai_models", methods=["POST"])
def api_ai_models():
    """Кнопка «Обновить список»: модели провайдера. OpenAI-совместимые — GET
    {base}/models (работает у LM Studio и OpenRouter); Anthropic — Models API."""
    from core import aicut
    d = request.get_json() or {}
    p = d.get("profile")
    if p is not None and not isinstance(p, dict):
        return jsonify(**umsg_err(SystemExit(umsg("bad_profile",
                                                  "Поле profile должно быть объектом"))))
    p = p or {}
    provider = jstr(p, "provider") or "lmstudio"
    # Ключ-маска: адрес и заголовки — тоже из сохранённого профиля (см.
    # _saved_profile_for_masked): иначе запрос со НАСТОЯЩИМ ключом уходил на
    # base_url из тела.
    saved = _saved_profile_for_masked(p, jstr(d, "name"))
    if saved:
        key = aicut.resolve_key(saved.get("api_key"))
        raw_base = ((saved.get("base_url") or "").strip()
                or aicut.PROVIDER_PRESETS.get(provider, {}).get("base_url") or "")
        hdrs = saved.get("headers") if isinstance(saved.get("headers"), dict) else None
    else:
        key = aicut.resolve_key(_unmask_ai_key(p.get("api_key"), jstr(d, "name")))
        raw_base = ((p.get("base_url") or "").strip()
                or aicut.PROVIDER_PRESETS.get(provider, {}).get("base_url") or "")
        hdrs = p.get("headers") if isinstance(p.get("headers"), dict) else aicut.parse_headers_text(p.get("headers_text"))
    base = aicut.normalize_base_url(raw_base)
    try:
        if not base:
            raise SystemExit(umsg("base_url_not_set", "Не задан Base URL"))
        if provider == "anthropic":
            import anthropic
            client_kwargs = dict(api_key=key or None, base_url=base)
            anthropic_hdrs = aicut.apply_profile_headers({}, {"headers": hdrs})
            if anthropic_hdrs:
                client_kwargs["default_headers"] = anthropic_hdrs
            client = anthropic.Anthropic(**client_kwargs)
            ids = [m.id for m in client.models.list()]
            return jsonify(ok=True, models=sorted(ids), base_url=base)
        headers = {"Authorization": "Bearer " + key} if key else {}
        headers = aicut.apply_profile_headers(headers, {"headers": hdrs})
        data = None
        probe_err = None
        try:
            req = http_req(base + "/models", headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:
            probe_err = e
            is_404 = isinstance(e, urllib.error.HTTPError) and e.code == 404
            is_json_err = isinstance(e, (json.JSONDecodeError, ValueError))
            if (is_404 or is_json_err) and not base.endswith("/v1"):
                try:
                    req_v1 = http_req(base + "/v1/models", headers=headers)
                    with urllib.request.urlopen(req_v1, timeout=20) as r:
                        data = json.load(r)
                    base = base + "/v1"
                    probe_err = None
                except Exception:
                    pass
        if data is None and probe_err is not None:
            raise probe_err
        models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        # Возможности моделей — из каталога models.dev (задание BY), а не из
        # supported_parameters ответа провайдера: это единственный источник правды.
        # Кнопка «Обновить список» заодно обновляет и каталог (force).
        efforts = {}
        default_enabled = {}
        caps = {}
        aicut.catalog.ensure_catalog(force=True, emit=emit)
        for mid in models:
            c = aicut.catalog.caps(provider, mid)
            if c.get("efforts"):
                efforts[(mid or "").lower()] = c["efforts"]
            if c.get("default") is not None:
                default_enabled[(mid or "").lower()] = c["default"]
            caps[(mid or "").lower()] = {
                k: c.get(k) for k in ("ctx_limit", "out_limit", "cost", "default")}
        # Выделенный Image API — отдельный каталог (/images/models): FLUX и прочие
        # чисто-картиночные модели в общий /models не попадают, а через
        # /chat/completions отвечают 404. Подмешиваем их в подсказки поля «Модель»,
        # чтобы профиль «Картинки» вообще можно было настроить, и запоминаем
        # supported_parameters (по ним gen_image решает, что можно слать).
        image = []
        if provider != "anthropic":
            try:
                req = http_req(base + "/images/models", headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    idata = json.load(r)
                aicut.images.IMAGE_MODELS = {
                    (m.get("id") or "").lower(): (m.get("supported_parameters") or {})
                    for m in (idata.get("data") or []) if m.get("id")}
                image = [m.get("id") for m in (idata.get("data") or []) if m.get("id")]
                models = sorted(set(models) | set(image))
            except Exception:
                pass                              # каталог картинок не отдался — не блокируем
        # Видео-модели — тоже отдельный каталог (/videos/models): Seedance/Veo/Kling в
        # общий /models не попадают, поэтому в подсказках поля «Модель» их не было.
        # Подмешиваем и запоминаем записи (по ним video_caps фильтрует запрос).
        video = []
        if provider != "anthropic":
            try:
                req = http_req(base + "/videos/models", headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    vdata = json.load(r)
                ventries = vdata.get("data") or vdata.get("models") or []
                aicut.video.VIDEO_MODEL_CAPS = {
                    (m.get("id") or m.get("slug") or "").lower(): m
                    for m in ventries if (m.get("id") or m.get("slug"))}
                video = [m.get("id") or m.get("slug") for m in ventries
                         if (m.get("id") or m.get("slug"))]
                models = sorted(set(models) | set(video))
            except Exception:
                pass                              # каталог видео не отдался — не блокируем
        # Модели с reasoning (пометка «· reasoning» в дата-листе) — по каталогу.
        # efforts отдельно — там те, у кого каталог перечислил УРОВНИ усилий.
        reasoning = [mid for mid in models
                     if aicut.catalog.caps(provider, mid).get("reasoning") is True]
        return jsonify(ok=True, models=sorted(models), base_url=base, reasoning_models=reasoning,
                       efforts=efforts,
                       default_enabled=default_enabled,
                       caps=caps,
                       image_models=image, video_models=video)
    except SystemExit as e:
        return jsonify(**umsg_err(e))
    except Exception as e:
        return jsonify(**umsg_err(SystemExit(umsg("ai_models_failed", f"{type(e).__name__}: {e}",
                                                  err=f"{type(e).__name__}: {e}"))))


@bp.route("/api/ai_genimage", methods=["POST"])
def api_ai_genimage():
    """Сгенерить картинку-вставку по query (модель профиля «Картинки»).
    Сохраняется в <dest|insert_library>/generated/ + сразу в индекс insertlib
    (следующие ролики найдут её автоподбором бесплатно).
    Цена/скорость: FLUX Klein ~5с/$0.014, FLUX Pro ~15с/$0.030, Nano Banana ~3с/$0.034."""
    from core import aicut
    from core import insertlib
    d = request.get_json() or {}
    query = jstr(d, "query").strip()
    dest = _insert_dest(d)
    slot = jstr(d, "slot") or "a"                        # какая из двух приписок (кнопки 1/2)
    speaker = jstr(d, "speaker") or None                 # профиль спикера (задание CQ)
    try:
        if not query:
            raise SystemExit(umsg("empty_query", "Пустой запрос — у вставки нет query"))
        if slot not in aicut.IMAGE_PROMPT_SLOTS:
            raise SystemExit(umsg("unknown_prompt_slot", f"Неизвестный слот промпта «{slot}»",
                                  slot=slot))
        prompt = aicut.build_image_prompt(query, slot=slot, speaker=speaker)
        # приписка стиля отдельно от собранного промпта — в базу картинка ложится с
        # полем look, и подбор отдаёт её спикерам в их стиле (задания ET1/ET2)
        look = aicut.resolve_image_prompt_cfg(slot, speaker=speaker)["extra"]
        try:
            aicut.clear_cancel()
            _t = time.time()
            # **k обязателен: aicut зовёт emit структурно — emit("… {model}", model=…),
            # и глушилка без kwargs роняла УЖЕ СГЕНЕРЁННУЮ картинку на строчке лога
            # о цене (TypeError: unexpected keyword argument 'model') — деньги за
            # запрос списаны, результат выброшен.
            png = aicut.gen_image(prompt, emit=lambda *a, **k: None)
            _dt_gen = time.time() - _t
            # фон долой ДО сохранения — в базу картинка ложится уже прозрачной, как стоковые
            nobg, warn = True, ""
            _dt_rembg = 0.0
            if aicut.image_rembg_on():
                try:
                    _t = time.time()
                    png = insertlib.remove_bg(png)
                    _dt_rembg = time.time() - _t
                except Exception as e:                       # нет rembg / модель не скачалась
                    nobg, warn = False, f"фон не убран: {e}"
            else:
                nobg = False
            _t = time.time()
            path = insertlib.add_generated(png, query, dest, ru=jstr(d, "prompt"),
                                           look=look)
            _dt_add = time.time() - _t
            thumb = insertlib._thumb_b64(path)
            print(f"[ai_genimage] slot={slot} prompt={prompt!r} gen={_dt_gen:.1f}s "
                  f"rembg={_dt_rembg:.1f}s add={_dt_add:.1f}s", flush=True)
            return jsonify(ok=True, path=path, thumb=thumb, nobg=nobg, warn=warn)
        except SystemExit as e:
            return jsonify(**umsg_err(e))
        except Exception as e:
            raise SystemExit(umsg("genimage_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/rembg", methods=["POST"])
def api_rembg():
    """«Убрать фон» у уже выбранного файла-вставки (кнопка на карточке) — как
    Remove Background в фотошопе. Прозрачный <имя>-nobg.png кладём сразу в базу
    (<dest>/photos) и вносим в индекс с описанием=запрос; исходник цел."""
    from core import insertlib
    d = request.get_json() or {}
    path = jstr(d, "path").strip()
    dest = _insert_dest(d)
    try:
        if not path or not os.path.exists(path):
            raise SystemExit(umsg("file_missing", "Файл не найден"))
        try:
            out = insertlib.strip_bg_file(path, dest_dir=dest)
            changed = os.path.abspath(out) != os.path.abspath(path)
            if changed:                                  # в индекс, чтобы нашлась в след. роликах
                insertlib.adopt([{"path": out, "desc": jstr(d, "query"),
                                  "ru": jstr(d, "prompt")}], dest)
            return jsonify(ok=True, path=out, changed=changed)
        except SystemExit as e:
            return jsonify(**umsg_err(e))
        except Exception as e:
            raise SystemExit(umsg("rembg_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/ai_intro", methods=["POST"])
def api_ai_intro():
    """ИИ-разметка интро + акценты посреди ролика (local LM Studio). Выгружает модель после."""
    d = request.get_json() or {}
    xml_path = jstr(d, "xml").strip().strip('"')
    try:
        if not os.path.isfile(xml_path):
            raise SystemExit(umsg("file_not_found", f"Файл не найден: {xml_path}",
                                  path=xml_path))
        ep = _ai_begin("интро")
        unload = False
        try:
            from core import aicut
            notes = []                      # прогресс стрима и переносы строк — в UI-лог, не в /dev/null
            def _emit(line="", **vars):
                s = t(line, **vars) if line else ""
                notes.append(s)
                emit(line, **vars)

            # inserts из UI (если фронт держит актуальный список) — акценты встанут туда,
            # где вставок нет; иначе cmd_intro подхватит сайдкар .inserts.json
            res = aicut.cmd_intro(xml_path, model=(jstr(d, "model") or None), emit=_emit,
                                  inserts=(d.get("inserts") if isinstance(d.get("inserts"), list)
                                           else None))
            unload = True
            return jsonify(ok=True, intro_rows=res["intro_rows"], mid_groups=res["mid_groups"],
                           log=notes)
        except SystemExit as e:
            return jsonify(**umsg_err(e))
        except Exception as e:
            raise SystemExit(umsg("intro_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
        finally:
            _ai_end(ep, unload=unload)
    except SystemExit as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/ai_stop", methods=["POST"])
def api_ai_stop():
    """Стоп одиночного ИИ-вызова (интро/жёлтые/вставки): флаг CANCEL рвёт ретраи
    _ask_json, выгрузка модели обрывает текущую генерацию LM Studio и освобождает
    VRAM. Клиент к этому моменту уже abort-нул свой fetch."""
    try:
        try:
            from core import aicut
            ep = aicut.cancel_call()      # CANCEL + смена epoch: старый поток выйдет сам
            def _unload():
                # выгрузка отложена в поток — но если к этому моменту уже стартовал НОВЫЙ
                # вызов (юзер сменил reasoning и запустил заново), модель трогать нельзя
                if aicut.is_current(ep):
                    aicut.unload_ours()
            threading.Thread(target=_unload, daemon=True).start()
        except Exception as e:
            raise SystemExit(umsg("ai_stop_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except SystemExit as e:
        return jsonify(**umsg_err(e))
    return jsonify(ok=True)


@bp.route("/api/ai_stats", methods=["GET"])
def api_ai_stats():
    """Сводка ИИ-вызовов для модалки настроек: кто сколько думает и стоит.

    Читает ai_calls.jsonl и группирует по тройке (модель, шаг, уровень ума).
    Медианы, а не средние: один сорвавшийся вызов на 200k токенов перекашивает
    среднее. rt% — доля размышлений в выводе (медиана rt / медиана out): если она
    близка к 100%, модель думает на весь бюджет — ровно то, из-за чего была
    заведена эта таблица. Ошибки считаются отдельно и в медианы не попадают."""
    from core import aicut
    import statistics
    rows = []
    try:
        with open(aicut.AI_LOG_PATH, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except Exception:
        pass                                            # файла нет — пустая сводка
    groups = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("ok") is None:
            continue                                    # стартовая запись, токенов ещё нет
        key = (r.get("model") or "-", r.get("step") or "-",
               r.get("reasoning") or "off")
        g = groups.setdefault(key, {"in": [], "out": [], "rt": [], "ms": [], "err": 0})
        if r.get("ok") is False:
            g["err"] += 1
            continue
        for f in ("in", "out", "rt", "ms"):
            v = r.get(f)
            if isinstance(v, (int, float)) and v is not None:
                g[f].append(v)
    out = []
    for (model, step, lvl), g in groups.items():
        med = {k: (round(statistics.median(g[k]), 1) if g[k] else 0) for k in ("in", "out", "rt", "ms")}
        out.append({
            "model": model, "step": step, "lvl": lvl,
            "n": len(g["out"]), "in": med["in"], "out": med["out"], "rt": med["rt"],
            "rtpct": round(med["rt"] / med["out"] * 100) if med["out"] else 0,
            "sec": med["ms"] / 1000 if med["ms"] else 0, "err": g["err"],
        })
    out.sort(key=lambda g: (g["model"], g["step"], g["lvl"]))
    return jsonify(ok=True, groups=out)
