# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ИИ-разметка монтажа: локальная модель (LM Studio) ИЛИ облачный провайдер
(Anthropic Claude / OpenRouter / любой OpenAI-совместимый) — профили в ai_config.json,
переключение через шестерёнку в webui.

Видео/аудио в модель НЕ отправляются НИКОГДА: вся нужная информация уже есть в
пословном транскрипте Whisper (слова + тайминги), а решения о выделениях/вставках/
нарезке — текстовая задача. В облако (если выбран облачный профиль) уходит только текст.

Командная строка (была `python aicut.py`, стала `python -m core.aicut` — пакет):
  yellow  <edited.xml>       — выбрать слова под жёлтое выделение
                               -> <stem>.yellow.json (webui подхватывает сам)
  inserts <edited.xml>       — предложить вставки (тайминг + промпт для поиска) [WIP]
                               -> <stem>.inserts.txt
  plan    <raw_words.json>   — план нарезки сырого транскрипта [WIP]
                               -> <stem>.plan.json

Общие флаги: --dry-run (показать промпт, не звонить в модель),
             --template file.txt (свой system-промпт вместо встроенного),
             --model ID (переопределить модель LM Studio),
             --url http://host:port/v1 (эндпоинт LM Studio).

Конфиг: профили провайдеров в reelsi/ai_config.json (не в гите — там API-ключи!).
Фолбэк-окружение для LM Studio:
  LMSTUDIO_URL   (по умолчанию http://localhost:1234/v1)
  LMSTUDIO_MODEL (по умолчанию qwen3.6-27b-4bpw-16gb-vram)
Зависимости: стандартная библиотека (urllib); для провайдера Anthropic — pip install anthropic.

С 2026-08-06 это пакет, а не файл на 2721 строку:

| модуль     | что там |
|------------|---------|
| `config`   | профили провайдеров, ключи, таблицы reasoning и кэша промпта |
| `prompts`  | system-промпты и JSON-схемы ответов |
| `llm`      | вызов модели: стрим, отмена, поколения вызовов, выгрузка LM Studio |
| `images`   | генерация картинок для вставок |
| `video`    | каталог видео-моделей, их возможности, проверка запроса, генерация |
| `commands` | сами команды: жёлтые, вставки, интро, план |

Импорты ниже — переэкспорт РАДИ СОВМЕСТИМОСТИ, а не украшение: `aicut` восемь модулей
используют как пространство имён (`aicut.gen_video`, `aicut.VIDEO_MODELS`,
`aicut.begin_call` — семь десятков имён), и разводить их по новым адресам значило бы
переписать половину репозитория ради нулевого выигрыша.

Не переэкспортируются CANCEL и EPOCH: они ПЕРЕПРИСВАИВАЮТСЯ, связанное имя на фасаде
замерло бы на первом значении и «Стоп» перестал бы работать. Снаружи для этого есть
функции — cancelled(), cancel_call(), is_current().
"""
# DEFAULT_URL / DEFAULT_MODEL / HERE объявлены в заголовке config.py, а не в теле,
# поэтому переэкспортируются отдельной строкой.
from .config import DEFAULT_URL, DEFAULT_MODEL, HERE   # noqa: F401
from .config import (   # noqa: F401
                     AI_CONFIG_PATH, AI_LOG_PATH, CACHE_MODELS, GLITCH_GLOW_MODES, KEY_ENV_PREFIX, OMNI_GIGAAM, OMNI_LOCAL,
                     OMNI_LOCAL_ENGINES,
                     PROVIDER_PRESETS, REASONING_BUDGET, REASONING_EXAMPLES, REASONING_LEVELS,
                     REASONING_MODELS, STEP_REASONING_DEFAULT, STEP_TITLES,
                     _default_ai_config, _profile_dict, _seed_ai_config,
                     apply_profile_headers, cut_asr_engine, glitch_glow_mode, key_env_name, load_ai_config, model_supports_caching,
                     normalize_base_url, omni_local_engine, parse_headers_text, reason_budget,
                     resolve_key, resolve_omni_profile, resolve_profile, save_ai_config, step_profile,
                     effective_step_reasoning, step_reasoning)
from . import catalog  # noqa: F401   (возможности моделей из models.dev)
from .prompts import (   # noqa: F401
                      INSERTS_SCHEMA, INSERTS_SYSTEM, INTRO_SCHEMA, INTRO_SYSTEM,
                      YELLOW_SCHEMA, YELLOW_SYSTEM)
from .llm import (   # noqa: F401
                  BUSY_RETRIES, STALL_FIRST, STALL_MID, STALL_NOTE, STALL_RETRIES,
                  StreamStalled, UpstreamBusy, _anthropic_supports_thinking, _api_base,
                  _ask_anthropic, _ask_json, _ask_openai, _extract_json_obj, _lms_bin,
                  _read_stream, begin_call, cancel_call, cancel_reason, cancelled,
                  clear_cancel, ensure_loaded, is_current, loaded_info, loaded_models,
                  our_loaded_models, unload_ours, warn_foreign_models)
from .images import (   # noqa: F401
                     IMAGE_MODEL_HINTS, IMAGE_OFF, IMAGE_PROMPT_SLOTS,
                     IMAGE_MODELS,
                     _gen_image_chat, _gen_image_openrouter, _img_http_error,
                     build_image_prompt, gen_image, image_rembg_on,
                     resolve_image_profile, resolve_image_prompt_cfg)
from .video import (   # noqa: F401
                    VIDEO_MODEL_CAPS, VIDEO_ASPECTS, VIDEO_MODELS, VIDEO_MODEL_HINTS,
                    VIDEO_OFF, VIDEO_PROMPT_SLOTS, VIDEO_RESOLUTIONS, VIDEO_ROLES,
                    _AR_SEEDANCE, _IMG_ALL,
                    _IMG_FMT, _IMG_JPG_PNG, _NOTE_HAILUO, _NOTE_KLING, _NOTE_SEEDANCE,
                    _NOTE_VEO, _PROBE_UA, _VIDEO_ERR_HINTS, _VIDEO_TAG_ALIASES,
                    _VIDEO_URL_EXTS, _caps_refs, _catalog_key, _head_media, _ref_is_video,
                    _video_error_text, _video_http_error, _video_prompt, _video_ref_item,
                    build_video_prompt, dangling_tags, ensure_video_catalog, gen_video,
                    image_format_of,
                    is_video_url, normalize_video_tags, probe_media, resolve_refs,
                    resolve_video_profile, resolve_video_prompt_cfg, set_video_catalog,
                    video_caps, video_check,
                    video_auto_aspect, video_auto_duration, video_auto_shape,
                    video_insert_duration, video_model_cfg,
                    video_model_entry, video_model_list, video_ref_tag, video_ref_tags,
                    video_resolution_cfg, video_resolution_sync,
                    video_warnings)
from .commands import (   # noqa: F401
                       INS_END_ZONE, INS_MAX, INS_MIN, INS_MIN_DUR, INS_MIN_GAP,
                       INS_SEC_PER, INS_ZONE_PHOTO, INS_ZONE_VIDEO,
                       INTRO_EMPTY_WARN, INTRO_EST_WORDS, INTRO_FREE_MIN, INTRO_HOOK_PAUSE,
                       INTRO_HOOK_ROWS, INTRO_HOOK_ROW_MAX_CHARS, INTRO_HOOK_WORDS,
                       INTRO_MAX_WORDS, INTRO_MID_GAP, INTRO_MID_PER_SEC, INTRO_ROW_MAX_CHARS,
                       INTRO_FUNC_WORDS,
                       _STYLE_WORDS, _apply_zones, _busy_windows, _busy_windows_from_free,
                       _end_zone_word, _free_quota, _free_windows, _hook_breaks, _intro_defunc,
                       _intro_free_hint, _intro_look,
                       _place_mids, _snap_to_phrase, _split_words, _strip_style_words,
                       _word_lines, _words_from_xml, _wrap_intro_rows, as_ints, cmd_inserts,
                       cmd_intro, cmd_yellow, ins_end_sec, ins_target)
