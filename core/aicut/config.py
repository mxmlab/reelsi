# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Профили ИИ-провайдеров, ключи и режимы «размышлений».

База пакета: отсюда берут конфиг все остальные модули. Что умеет модель —
в каталоге models.dev (aicut/catalog.py); здесь остались только ФОЛБЭК-таблицы
подстрок на случай «каталога нет и кэша нет» (см. шапку секции с таблицами).
"""
import os, json

from core.app_meta import APP_REFERER, APP_NAME, env   # noqa: F401  (переэкспорт)

# HERE — корень репозитория, а НЕ папка пакета: ai_config.json всегда лежал
# рядом с aicut.py, и пакет не должен этого менять.
from core import paths

HERE = paths.ROOT

DEFAULT_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1")
DEFAULT_MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen3.6-27b-4bpw-16gb-vram")

# ---- Профили ИИ-провайдеров (как в Roo Code) --------------------------------
# REELSI_AI_CONFIG — отдельный конфиг для тестового профиля (см. REELSI_INSERTLIB):
# иначе переключение провайдера/модели на 5098 меняет их и в рабочем интерфейсе.
AI_CONFIG_PATH = env("AI_CONFIG") or paths.root("ai_config.json")

# ---- Лог ИИ-вызовов (токены по шагам) ----------------------------------------
# Каждый chat-вызов (нарезка/жёлтые/вставки/интро/план) пишет строку в этот JSONL:
# по нему видно, какой шаг сколько токенов сжёг (reasoning-модели умеют жечь весь
# бюджет — баг с 200k токенов ловился только по косвенным признакам, пока лога
# не было). REELSI_AI_LOG — отдельный файл для тестового профиля (как REELSI_AI_CONFIG):
# на 5098 лог не должен мешаться с боевым.
AI_LOG_PATH = env("AI_LOG") or paths.root("ai_calls.jsonl")
AI_LOG_CAP = 2000        # сколько ПОСЛЕДНИХ записей держать (авточистка хвостом)
AI_LOG_MAX_MB = 5        # порог размера файла, после которого чистим (дёшево, до N строк)
PROVIDER_PRESETS = {
    "lmstudio":    {"label": "LM Studio (локально)", "base_url": "http://localhost:1234/v1",
                    "models": ["qwen3.6-27b-4bpw-16gb-vram",
                               "qwen3.5-9b-claude-4.6-opus-reasoning-distilled"]},
    "commandcode": {"label": "CommandCode", "base_url": "https://api.commandcode.ai/provider/v1",
                    "models": ["meta/muse-spark-1.3-contributor", "claude-sonnet-5",
                               "gpt-5.6-luna", "deepseek/deepseek-v4-flash",
                               "google/gemini-3.8-flash"]},
    "anthropic":   {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com",
                    "models": ["claude-sonnet-5", "claude-haiku-4-5", "claude-opus-4-8"]},
    "openrouter":  {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                    "models": []},
    "deepseek":    {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                    "models": ["deepseek-chat", "deepseek-reasoner"]},
    "groq":        {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
                    "models": ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"]},
    "ollama":      {"label": "Ollama (локально)", "base_url": "http://localhost:11434/v1",
                    "models": ["qwen2.5:32b", "deepseek-r1:32b", "llama3.3"]},
    "openai":      {"label": "OpenAI-совместимый (свой URL)", "base_url": "", "models": []},
}

# ---- Reasoning (extended thinking / chain-of-thought) -----------------------
# Все поддерживаемые уровни усилий reasoning (от выключено до максимума):
# "off" — без размышлений; minimal/low/medium/high/xhigh/max — уровни усилий.
REASONING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# Уровень размышлений задаётся НА КАЖДЫЙ ШАГ отдельно, а не один на весь профиль:
# шаги слишком разные. Нарезке думать НАДО (замер на C1353: без размышлений модель
# систематически брала в скобки поздний заход дубля вместо раннего и уносила с ним
# уникальное продолжение; с medium все скобки встали правильно). Жёлтым словам,
# вставкам и интро думать НЕ надо — там reasoning жёг весь max_tokens впустую
# (126с/out=12001 против 3.3с/out=192). Отсюда дефолты:
STEP_REASONING_DEFAULT = {"cut": "medium", "yellow": "off", "inserts": "off",
                          "intro": "off"}
STEP_TITLES = {"cut": "Нарезка", "yellow": "Жёлтые слова", "inserts": "Вставки",
               "intro": "Интро"}
# Сколько токенов добавить на сами размышления (ответ считается отдельно).
# max/xhigh/minimal — уровни из каталога models.dev (caps().efforts), у них есть
# бюджет как у соседних знакомых уровней.
REASONING_BUDGET = {"off": 0, "minimal": 2000, "low": 4000, "medium": 8000,
                    "high": 16000, "xhigh": 24000, "max": 32000}


def step_reasoning(step):
    """Уровень размышлений для шага (из ai_config, иначе дефолт шага).

    Уровень может быть из каталога models.dev (low/high/max/xhigh и пр.) — валидный
    для модели шага, но не входящий в наши REASONING_LEVELS. Проверяем по каталогу.
    Порядок источников: сначала уровень, выбранный ДЛЯ МОДЕЛИ этого шага
    (reasoning_by_model[модель][шаг]) — сменил модель у шага, видишь свой выбор,
    а не чужой (задание по UI-состояниям); затем общий reasoning_steps[шаг];
    затем дефолт шага."""
    cfg = load_ai_config()
    prof = step_profile(step)
    pp = (cfg.get("profiles") or {}).get(prof) or {}
    # Память уровня НА МОДЕЛЬ: ключ — id модели как в профиле, без нормализации.
    # Значение принимаем только если оно валидно для ЭТОЙ модели по каталогу —
    # иначе хранимое для неё же medium снова уехало бы в «показываем одно, шлём другое».
    by_model = (cfg.get("reasoning_by_model") or {}).get(pp.get("model") or "")
    if by_model and isinstance(by_model, dict):
        lvl = by_model.get(step)
        if lvl:
            try:
                from . import catalog
                caps = catalog.caps(pp.get("provider"), pp.get("model"))
                if caps.get("efforts"):
                    if lvl in caps["efforts"] or lvl == "off":
                        return lvl
                elif lvl in REASONING_LEVELS or lvl == "off":
                    return lvl
            except Exception:
                pass                        # каталог недоступен — как раньше, дальше по цепочке
    lvl = (cfg.get("reasoning_steps") or {}).get(step)
    if lvl in REASONING_LEVELS or lvl == "off":
        return lvl
    if lvl:
        try:
            from . import catalog
            if catalog.valid_level(pp.get("provider"), pp.get("model"), lvl):
                return lvl
        except Exception:
            pass                        # каталог недоступен — дефолт, не падать
    return STEP_REASONING_DEFAULT.get(step, "off")


def effective_step_reasoning(step):
    """Что РЕАЛЬНО уйдёт в API на этом шаге (задание по UI-состояниям).

    step_reasoning возвращает выбранный уровень, а провайдер умеет не всё: у
    deepseek-v4-flash efforts = low/high/max, хранимый medium понижается до low.
    Показывать интерфейсу надо именно результат понижения, иначе селект и сводка
    снова врут (диагноз: три разных ответа на один вопрос). Каталог молчит про
    модель — понижать нечего, отдаём как выбрано."""
    prof = step_profile(step)
    pp = (load_ai_config().get("profiles") or {}).get(prof) or {}
    try:
        from . import catalog
        return catalog.nearest_supported_level(pp.get("provider"), pp.get("model"),
                                               step_reasoning(step))
    except Exception:
        return step_reasoning(step)         # каталог недоступен — как раньше


def step_profile(step):
    """Имя ИИ-профиля (МОДЕЛИ) для шага — из ai_config.step_profiles, иначе общий
    active. Профиль выбирается ОТДЕЛЬНО на каждый шаг (нарезка/жёлтые/вставки/интро):
    шагам нужны разные модели, а не одна на всё приложение. Невалидное/пустое имя
    (профиль переименован/удалён) -> откат на active, чтобы шаг не падал."""
    cfg = load_ai_config()
    name = (cfg.get("step_profiles") or {}).get(step)
    if name and name in (cfg.get("profiles") or {}):
        return name
    return cfg.get("active")


def reason_budget(base, level):
    """max_tokens с запасом на размышления — нужен ТОЛЬКО для Anthropic, где этот
    параметр обязателен по API. OpenAI-совместимым (LM Studio / OpenRouter / свой
    сервер) мы max_tokens не шлём вовсе: он не останавливает модель, а лишь
    обрывает уже посчитанный (и оплаченный) ответ — см. _ask_openai."""
    return int(base) + REASONING_BUDGET.get(level or "off", 0)

# --------------------------------------------------------------------------- #
# ФОЛБЭК-ТАБЛИЦЫ. Источник правды — каталог models.dev (aicut/catalog.py, caps()),
# эти списки — ЗАПАСНОЙ ПУТЬ на случай «каталога нет и кэша нет»: сеть недоступна
# и кэша с диска тоже. Новый слой не имеет права быть единственной точкой отказа
# ИИ-шагов, поэтому без каталога мы ведём себя как раньше, по подстрокам. Не
# править их под новые модели: расхождение с каталогом — нормальное состояние
# запасного пути, а не баг.
# --------------------------------------------------------------------------- #

# Подстроки id моделей, которые ЗАВЕДОМО поддерживают reasoning. Фолбэк, когда
# каталог models.dev недоступен (см. шапку секции): точное знание — в каталоге.
REASONING_MODELS = [
    "openai/o1", "openai/o3", "openai/o4", "openai/gpt-5",
    "deepseek/deepseek-r1", "deepseek/deepseek-reasoner",
    "qwen/qwq", "qwen/qwen3",
    "anthropic/claude-3.7-sonnet:thinking", "anthropic/claude-3.7-sonnet-thinking",
    "claude-3-7-sonnet", "claude-sonnet-4", "claude-opus-4",
    "x-ai/grok-4", "x-ai/grok-3-mini",
    "google/gemini-2.5-pro", "google/gemini-2.5-flash", "google/gemini-3",
    "mistral/magistral",
    "moonshotai/kimi-thinking", "perplexity/sonar-reasoning",
    "nvidia/nemotron",
    "tencent/hunyuan", "tencent/hy3",
]

# Примеры reasoning-моделей (ПОЛНЫЕ id) — стартовый набор, когда каталог недоступен.
REASONING_EXAMPLES = [
    "openai/o3", "openai/o3-mini", "openai/o4-mini", "openai/gpt-5",
    "deepseek/deepseek-r1", "qwen/qwq-32b", "qwen/qwen3-235b-a22b",
    "anthropic/claude-3.7-sonnet:thinking", "x-ai/grok-4", "x-ai/grok-3-mini",
    "google/gemini-2.5-pro", "google/gemini-2.5-flash",
    "mistral/magistral-medium", "moonshotai/kimi-thinking",
    "perplexity/sonar-reasoning", "nvidia/nemotron",
]

# Подстроки id моделей, которые ЗАВЕДОМО поддерживают prompt caching (cache_control).
# Фолбэк, когда каталог недоступен; точный список — в каталоге (caps().cache).
CACHE_MODELS = [
    "anthropic/claude",
    "openai/gpt-4", "openai/gpt-4o", "openai/o1", "openai/o3", "openai/o4",
    "deepseek/deepseek", "deepseek/deepseek-r1",
    "google/gemini-2.5", "google/gemini-3",
    "x-ai/grok",
    "qwen/qwq", "qwen/qwen3",
    "mistralai/", "mistral/",
    "meta/llama-3.1", "meta/llama-3.2", "meta/llama-3.3",
]


def model_supports_caching(mid):
    """Поддерживает ли модель provider-side prompt caching (cache_control).

    Источник правды — каталог models.dev (caps().cache). Этот фолбэк по подстрокам
    остаётся только на случай «каталога нет и кэша нет» (см. шапку секции)."""
    m = (mid or "").lower()
    if not m:
        return False
    return any(s in m for s in CACHE_MODELS)


def _default_ai_config():
    return {"active": "LM Studio", "profiles": {
        "LM Studio": {"provider": "lmstudio", "base_url": DEFAULT_URL,
                      "api_key": "", "model": DEFAULT_MODEL}}}


def _seed_ai_config():
    """Тестовый профиль (REELSI_AI_CONFIG) без своего файла — снять КОПИЮ с рабочего.
    Пустой конфиг сделал бы отдельный порт бесполезным (ни ключей, ни моделей), а
    читать рабочий файл напрямую нельзя: смена провайдера на тесте меняла бы его и в бою."""
    real = paths.root("ai_config.json")
    if AI_CONFIG_PATH == real or os.path.exists(AI_CONFIG_PATH) or not os.path.isfile(real):
        return
    try:
        import shutil
        shutil.copyfile(real, AI_CONFIG_PATH)
    except Exception:
        pass


def load_ai_config():
    """Профили провайдеров. Файла нет/битый -> дефолт (LM Studio, env-переменные)."""
    _seed_ai_config()
    try:
        cfg = json.load(open(AI_CONFIG_PATH, encoding="utf-8"))
        if isinstance(cfg.get("profiles"), dict) and cfg["profiles"]:
            # Миграция (задание CS): приписки к промптам генерации живут ТОЛЬКО
            # в профилях спикеров. Убираем устаревшие ключи из ai_config.json.
            if "image_prompt" in cfg or "image_prompt_b" in cfg:
                cfg.pop("image_prompt", None)
                cfg.pop("image_prompt_b", None)
                try:
                    save_ai_config(cfg)
                except Exception:
                    pass
            return cfg
    except Exception:
        pass
    return _default_ai_config()


def save_ai_config(cfg):
    # Атомарно: прямой open(...,"w") усекал файл ДО сериализации, и любое падение
    # в этот момент оставляло пустой конфиг -> load_ai_config молча уходил на дефолт,
    # а все API-ключи пропадали (файл в .gitignore, восстановить неоткуда).
    tmp = AI_CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, AI_CONFIG_PATH)


# ---- Ключи из переменных окружения (env:VAR_NAME) -------------------------
# Ключ вида env:МОЯ_ПЕРЕМЕННАЯ означает «взять из окружения»; в ai_config.json
# хранится именно ЭТА запись, а не значение.
KEY_ENV_PREFIX = "env:"


def key_env_name(value):
    """Имя переменной для записи вида env:FOO (с обрезкой пробелов), иначе None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.startswith(KEY_ENV_PREFIX):
        return v[len(KEY_ENV_PREFIX):].strip()
    return None


def resolve_key(value):
    """Разрешить ключ: если env:FOO — взять из os.environ, иначе вернуть само значение."""
    name = key_env_name(value)
    if name is not None:
        return os.environ.get(name, "")
    return value


def normalize_base_url(u):
    """Нормализация Base URL: обрезка пробелов, слэшей и случайных хвостов-эндпоинтов."""
    if not u:
        return ""
    u = str(u).strip().rstrip("/")
    endpoints = (
        "/chat/completions",
        "/completions",
        "/responses",
        "/models",
        "/images",
        "/videos",
    )
    for ep in endpoints:
        if u.endswith(ep):
            u = u[:-len(ep)].rstrip("/")
            break
    return u


def parse_headers_text(text):
    """Разбирает многострочный текст 'Имя: значение' в dict {str: str}.
    Пустые строки и строки без ':' пропускаются. Имя и значение обрезаются."""
    if not text or not isinstance(text, str):
        return {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def apply_profile_headers(headers, prof):
    """Возвращает новый словарь заголовков: базовые headers + сверху пользовательские из prof['headers'].
    Исходный словарь не мутирует."""
    out = dict(headers) if headers else {}
    if not prof or not isinstance(prof, dict):
        return out
    user_headers = prof.get("headers")
    if isinstance(user_headers, dict):
        for k, v in user_headers.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k] = resolve_key(v)
    return out


def _profile_dict(name, prof):
    res = {"provider": prof.get("provider") or "lmstudio",
           "base_url": prof.get("base_url") or PROVIDER_PRESETS.get(
               prof.get("provider") or "lmstudio", {}).get("base_url") or DEFAULT_URL,
           "api_key": resolve_key(prof.get("api_key") or ""),
           "model": prof.get("model") or DEFAULT_MODEL,
           # уровень reasoning (extended thinking) для OpenRouter: off/low/medium/high
           "reasoning": (prof.get("reasoning") or "off"),
           "name": name}
    raw_h = prof.get("headers")
    if isinstance(raw_h, dict):
        h = {k: resolve_key(v) for k, v in raw_h.items()
             if isinstance(k, str) and isinstance(v, str)}
        if h:
            res["headers"] = h
    return res


def resolve_profile(model=None, url=None, name=None):
    """Профиль по имени (name — для выбора модели ПОШАГОВО, см. step_profile), иначе
    активный. Плюс переопределения model/url (CLI/обратная совместимость).
    Возвращает dict {provider, base_url, api_key, model, name}."""
    cfg = load_ai_config()
    if not (name and name in cfg.get("profiles", {})):
        name = cfg.get("active")
    prof = cfg["profiles"].get(name)
    if prof is None:
        name, prof = next(iter(cfg["profiles"].items()))
    p = _profile_dict(name, prof)
    if model:
        p["model"] = model
    if url:
        p["base_url"] = url
    return p


# Локальные Omni-движки (слушают звук на GPU, ничего не уходит в облако):
OMNI_LOCAL = "__local__"          # Qwen2.5-Omni-7B (дефолт)
OMNI_GIGAAM = "__gigaam__" # GigaAMv3
OMNI_LOCAL_ENGINES = {OMNI_LOCAL: "qwen", OMNI_GIGAAM: "gigaam"}


def resolve_omni_profile():
    """Профиль для ОБЛАЧНОЙ Omni-транскрипции. None = локальный движок (см.
    omni_local_engine). ВНИМАНИЕ: облачный профиль отправляет АУДИО-чанки провайдеру."""
    cfg = load_ai_config()
    name = cfg.get("active_omni") or OMNI_LOCAL
    if name in OMNI_LOCAL_ENGINES:
        return None
    prof = cfg["profiles"].get(name)
    if prof is None:
        return None
    return _profile_dict(name, prof)


def omni_local_engine():
    """Какой ЛОКАЛЬНЫЙ движок слушает звук: 'qwen' | 'gigaam'. Для облачного
    профиля не вызывается (resolve_omni_profile вернёт dict). Неизвестное имя -> qwen."""
    name = load_ai_config().get("active_omni") or OMNI_LOCAL
    return OMNI_LOCAL_ENGINES.get(name, "qwen")


def cut_asr_engine(emit=None):
    """Какой ASR-движок делает пословные тайминги для нарезки (задание GH).

    Берётся из ai_config.json (active_cut_asr), дефолт 'gigaam'.
    Если сохранённый движок отсутствует в каталоге или не годен под рез (cut != True),
    откатывается на 'gigaam' и сообщает об этом в лог/консоль."""
    cfg = load_ai_config()
    engine = cfg.get("active_cut_asr") or "gigaam"
    try:
        from core import asr_backends
        meta = asr_backends.engine_meta(engine)
        if meta and meta.get("cut"):
            return engine
        msg = f"⚠ Движок нарезки «{engine}» не найден или не годен для нарезки (cut=True) — откат на gigaam"
        if emit:
            emit(msg)
        else:
            print(msg, flush=True)
    except Exception:
        pass
    return "gigaam"

