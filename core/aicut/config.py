# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Профили ИИ-провайдеров, ключи и режимы «размышлений».

База пакета: отсюда берут конфиг все остальные модули. Что умеет модель —
в каталоге models.dev (aicut/catalog.py); здесь остались только ФОЛБЭК-таблицы
подстрок на случай «каталога нет и кэша нет» (см. шапку секции с таблицами).
"""
from __future__ import annotations
import os, json, re, time, threading
import urllib.request, urllib.parse, urllib.error
from typing import Any, Callable

from core.app_meta import APP_REFERER, APP_NAME, env, http_req   # noqa: F401  (переэкспорт)
from core.fileio import atomic_json_dump

# Кэш total_slots локального llama-server (/props): url -> (ts_expire, slots)
_PROPS_CACHE: dict[str, tuple[float, int]] = {}
_PROPS_CACHE_LOCK = threading.Lock()

# Конфиг пишут два потока (api/ai.py и core/aicut/video.py): уникальный tmp спасает
# от перемешивания половин, но не от двух os.replace по одному пути на Windows.
_SAVE_LOCK = threading.Lock()

# HERE — корень репозитория, а НЕ папка пакета: ai_config.json всегда лежал
# рядом с aicut.py, и пакет не должен этого менять.
from core import paths
from core import device as _device
from core.umsg import ReelsiError
from core.applog import get_logger

log = get_logger(__name__)

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
    "unsloth":     {"label": "Unsloth Studio (локально)", "base_url": "http://127.0.0.1:8888/v1",
                    "models": ["unsloth/Qwen-Image-2.1-GGUF/qwen-image-2.1-Q8_0.gguf"]},
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

# Модели, которым effort уходит РОДНЫМ параметром, а не долей max_tokens.
# У Anthropic/Gemini и пр. OpenRouter считает reasoning.effort ПРОЦЕНТОМ от
# max_tokens запроса, поэтому потолок им обязателен (см. reason_budget). А у
# семейства openai/* effort передаётся родным параметром (reasoning_effort):
# модель думает столько, сколько считает нужным, и наш потолок её размышления
# не ограничивает — он обрывает уже посчитанный ответ. Именно так упала разметка
# вставок 02.10.2026: 6 вызовов подряд finish=length ровно по 26000 токенов
# (ответ 10000 + REASONING_BUDGET["high"]=16000), из них 25682–26000 на
# размышления. Таким моделям отдаём потолок вывода САМОЙ модели из каталога.
# Список — ПРЕФИКСЫ id моделей; другие семейства не трогаем.
NATIVE_EFFORT_PREFIXES = ["openai/"]


def uses_native_effort(model: str | None) -> bool:
    """effort этой модели — родной параметр, а не доля max_tokens (см. выше).

    Для таких моделей при включённом «уме» _ask_openai шлёт потолок вывода самой
    модели из каталога, а не наш бюджет (ответ + размышления): иначе размышления
    обрываются на нашем потолке, и шаг падает на finish=length."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in NATIVE_EFFORT_PREFIXES)


def step_reasoning(step: str) -> str:
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
            except ReelsiError: raise
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
        except ReelsiError: raise
        except Exception:
            pass                        # каталог недоступен — дефолт, не падать
    return STEP_REASONING_DEFAULT.get(step, "off")


def effective_step_reasoning(step: str) -> str:
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
    except ReelsiError: raise
    except Exception:
        return step_reasoning(step)         # каталог недоступен — как раньше


def step_profile(step: str) -> str | None:
    """Имя ИИ-профиля (МОДЕЛИ) для шага — из ai_config.step_profiles, иначе общий
    active. Профиль выбирается ОТДЕЛЬНО на каждый шаг (нарезка/жёлтые/вставки/интро):
    шагам нужны разные модели, а не одна на всё приложение. Невалидное/пустое имя
    (профиль переименован/удалён) -> откат на active, чтобы шаг не падал."""
    cfg = load_ai_config()
    name = (cfg.get("step_profiles") or {}).get(step)
    if name and name in (cfg.get("profiles") or {}):
        return name
    return cfg.get("active")


def step_is_local(step: str) -> bool:
    """Локальный ли профиль шага: lmstudio или base_url на loopback.

    Вынесено из step_concurrency — та же логика проверки провайдера и адреса,
    но нужна отдельно для решения о параллельности нарезки (cut_parallel_width).
    """
    name = step_profile(step)
    prof = (load_ai_config().get("profiles") or {}).get(name) or {} if name else {}
    provider = prof.get("provider") or "lmstudio"
    if provider == "lmstudio":
        return True
    base_url = str(prof.get("base_url") or PROVIDER_PRESETS.get(provider, {}).get("base_url") or DEFAULT_URL)
    parsed = urllib.parse.urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def step_concurrency(step: str) -> int:
    """Число одновременных запросов для шага.

    Порядок источников: переопределение НА ШАГЕ (вкладки «Нарезка»/«Разметка»,
    step_concurrency_override — его пишет действие set_step_concurrency), затем
    явное из профиля (1..16), иначе 1 для lmstudio, для локального сервера
    (loopback) — опрос /props (total_slots), для остальных (облако) — 4."""
    cfg = load_ai_config()
    # Переопределение шага — ПЕРВЫМ: поле профиля осталось УМОЛЧАНИЕМ для шагов,
    # у которых своего числа нет, иначе настройка на вкладке молча не работала бы.
    ov = (cfg.get("step_concurrency_override") or {}).get(step)
    if isinstance(ov, int) and not isinstance(ov, bool) and 1 <= ov <= 16:
        return ov
    name = step_profile(step)
    prof = (cfg.get("profiles") or {}).get(name) or {} if name else {}
    c = prof.get("concurrency")
    if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 16:
        return c

    if step_is_local(step):
        provider = prof.get("provider") or "lmstudio"
        if provider == "lmstudio":
            return 1
        base_url = str(prof.get("base_url") or PROVIDER_PRESETS.get(provider, {}).get("base_url") or DEFAULT_URL)
        with _PROPS_CACHE_LOCK:
            cached = _PROPS_CACHE.get(base_url)
            if cached is not None and time.time() < cached[0]:
                return cached[1]
        slots = 1
        try:
            clean_url = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
            props_url = f"{clean_url}/props"
            req = http_req(props_url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                val = data.get("total_slots")
                if isinstance(val, int) and not isinstance(val, bool) and val >= 1:
                    slots = max(1, min(16, val))
        except ReelsiError: raise
        except Exception:
            slots = 1
        with _PROPS_CACHE_LOCK:
            _PROPS_CACHE[base_url] = (time.time() + 60.0, slots)
        return slots

    return 4


def cut_parallel_width(mode: str, review: bool, n_clips: int) -> int:
    """Сколько роликов ИИ-нарезки гнать одновременно.

    - mode != "gigaam" или review → 1 (старый путь и ревью параллелить нельзя).
    - step_is_local("cut") → 1 (локальная модель решения на той же GPU, что и распознавание).
    - Иначе min(step_concurrency("cut"), n_clips), не меньше 1 — и НЕ больше того, что
      тянет видеокарта: каждый процесс ролика держит CUDA-контекст ~570 МиБ (замер:
      четыре процесса = 2307 из 4096 МиБ), поэтому заданные 10 роликов на 4 ГБ — это
      гарантированный OOM в подпроцессе, а в интерфейсе «код 1» без причины.
    """
    if mode != "gigaam" or review:
        return 1
    if step_is_local("cut"):
        return 1
    width = max(1, min(step_concurrency("cut"), n_clips))
    budget = _device.parallel_width_budget()          # None — карты нет, потолка нет
    if budget is None or width <= budget:
        return width
    # Строка про потолок — в лог задания: без неё «просил 10, порезало 6» выглядит
    # как потерянная настройка, а не как предел карты.
    vram = _device.vram_total_mib()
    card = "{:.1f}".format(vram / 1024) if vram else "?"
    log.info("карта %s ГиБ, каждый ролик держит ~%d МиБ — одновременно не больше %d "
             "(просили %d)", card, _device.CUT_ROLE_VRAM_MIB, budget, width)
    return budget


def reason_budget(base: int, level: str) -> int:
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


def model_supports_caching(mid: str | None) -> bool:
    """Поддерживает ли модель provider-side prompt caching (cache_control).

    Источник правды — каталог models.dev (caps().cache). Этот фолбэк по подстрокам
    остаётся только на случай «каталога нет и кэша нет» (см. шапку секции)."""
    m = (mid or "").lower()
    if not m:
        return False
    return any(s in m for s in CACHE_MODELS)


def _default_ai_config() -> dict[str, Any]:
    return {"active": "LM Studio", "profiles": {
        "LM Studio": {"provider": "lmstudio", "base_url": DEFAULT_URL,
                      "api_key": "", "model": DEFAULT_MODEL}}}


def _seed_ai_config() -> None:
    """Тестовый профиль (REELSI_AI_CONFIG) без своего файла — снять КОПИЮ с рабочего.
    Пустой конфиг сделал бы отдельный порт бесполезным (ни ключей, ни моделей), а
    читать рабочий файл напрямую нельзя: смена провайдера на тесте меняла бы его и в бою."""
    real = paths.root("ai_config.json")
    if AI_CONFIG_PATH == real or os.path.exists(AI_CONFIG_PATH) or not os.path.isfile(real):
        return
    try:
        import shutil
        shutil.copyfile(real, AI_CONFIG_PATH)
    except ReelsiError: raise
    except Exception as ex:
        log.warning("не скопировал боевой ai_config.json в %s: %s — "
                    "профиль останется без ключей", AI_CONFIG_PATH, ex)


def load_ai_config() -> dict[str, Any]:
    """Профили провайдеров. Файла нет/битый -> дефолт (LM Studio, env-переменные)."""
    _seed_ai_config()
    try:
        cfg = json.load(open(AI_CONFIG_PATH, encoding="utf-8"))
        if isinstance(cfg.get("profiles"), dict) and cfg["profiles"]:
            # Миграция: приписки к промптам генерации живут ТОЛЬКО
            # в профилях спикеров. Убираем устаревшие ключи из ai_config.json.
            if "image_prompt" in cfg or "image_prompt_b" in cfg:
                cfg.pop("image_prompt", None)
                cfg.pop("image_prompt_b", None)
                try:
                    save_ai_config(cfg)
                except ReelsiError: raise
                except Exception as ex:
                    log.warning("не переписал ai_config.json после чистки "
                                "старых промптов: %s", ex)
            return cfg
    except ReelsiError: raise
    except Exception as ex:
        log.warning("ai_config.json не прочитан (%s): %s — "
                    "беру настройки по умолчанию", AI_CONFIG_PATH, ex)
    return _default_ai_config()


def save_ai_config(cfg: dict[str, Any]) -> None:
    # Атомарно: прямой open(...,"w") усекал файл ДО сериализации, и любое падение
    # в этот момент оставляло пустой конфиг -> load_ai_config молча уходил на дефолт,
    # а все API-ключи пропадали (файл в .gitignore, восстановить неоткуда).
    # Имя tmp уникально (mkstemp внутри atomic_json_dump): конфиг пишут ДВА потока —
    # api/ai.py и core/aicut/video.py. Уникального tmp мало: два одновременных
    # os.replace по ОДНОМУ пути на Windows дают WinError 5 «Access is denied»
    # (замер: 6 потоков по 30 записей — 36 падений), поэтому запись сериализуем.
    with _SAVE_LOCK:
        atomic_json_dump(AI_CONFIG_PATH, cfg, indent=1)
        # 0600 на POSIX: в конфиге лежат API-ключи провайдеров, а
        # SECURITY.md обещает защиту правами ОС. atomic_json_dump новому файлу ставит
        # права по umask (обычно 0644) — то есть ключи читал бы любой пользователь
        # машины. На Windows прав user/group нет вовсе, os.chmod управляет только
        # битом «только чтение» — там режим оставляем как есть.
        if os.name == "posix":
            try:
                os.chmod(AI_CONFIG_PATH, 0o600)
            except OSError:
                pass        # ФС без прав (FAT, сетевой диск) — не повод не сохранить


# ---- Ключи из переменных окружения (env:VAR_NAME) -------------------------
# Ключ вида env:МОЯ_ПЕРЕМЕННАЯ означает «взять из окружения»; в ai_config.json
# хранится именно ЭТА запись, а не значение.
KEY_ENV_PREFIX = "env:"


def key_env_name(value: Any) -> str | None:
    """Имя переменной для записи вида env:FOO (с обрезкой пробелов), иначе None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.startswith(KEY_ENV_PREFIX):
        return v[len(KEY_ENV_PREFIX):].strip()
    return None


def resolve_key(value: Any) -> Any:
    """Разрешить ключ: если env:FOO — взять из os.environ, иначе вернуть само значение."""
    name = key_env_name(value)
    if name is not None:
        return os.environ.get(name, "")
    return value


# ---- Маска ключа наружу ------------------------------------------------------
# Ключ уходит в интерфейс ТОЛЬКО маской «•••xxxx»: браузер — не место для секрета
# (localStorage, история, devtools, чужое расширение). Маска необратима и НИКОГДА не
# сохраняется вместо ключа: «•••xxxx», записанная в файл, — это потерянный ключ, а
# профиль с ней интерфейс показал бы как рабочий. Обратно маска принимается только
# как «ключ не менял» и подменяется СОХРАНЁННЫМ ключом профиля.


def mask_ai_key(k: str) -> str:
    """Ключ наружу: «•••xxxx» (последние 4 символа).

    env:VAR не маскируется: это имя переменной окружения, а не секрет (значение
    подставит resolve_key уже на сервере)."""
    if k and key_env_name(k) is not None:
        return k
    return ("•••" + k[-4:]) if k else ""


def masked_profiles(cfg: dict[str, Any]) -> dict[str, Any]:
    """Профили для интерфейса: те же поля, но ключ и СВОИ ЗАГОЛОВКИ закрыты маской.

    У env-ключа добавляется key_env_ok — «переменная есть в окружении сервера»:
    без него интерфейс не отличит рабочий профиль от профиля с забытой переменной."""
    res = {}
    for n, p in (cfg.get("profiles") or {}).items():
        k = p.get("api_key") or ""
        env_name = key_env_name(k)
        item = {**p, "api_key": mask_ai_key(k)}
        if "headers" in item:
            item["headers"] = mask_headers(item.get("headers"))
        if env_name is not None:
            val = os.environ.get(env_name, "")
            item["key_env_ok"] = bool(val.strip() if isinstance(val, str) else val)
        res[n] = item
    return res


def unmask_ai_key(key: str | None, saved_name: str | None) -> str:
    """Ключ из формы: маска «•••…» = «не менял» -> вернуть сохранённый ключ профиля."""
    key = (key or "").strip()
    if key.startswith("•••"):
        saved = load_ai_config()["profiles"].get(saved_name or "", {})
        return saved.get("api_key") or ""
    return key


def mask_header_value(v: str) -> str:
    """Значение своего заголовка наружу — той же маской, что ключ («•••xxxx»).

    Заголовок — поддерживаемый канал секретов (`apply_profile_headers`): там
    `Authorization`, `x-api-key` и прочие токены. `GET /api/ai_config` отдавал их
    открытым текстом в браузер (localStorage, история, devtools), и в DOM они
    светились в поле «Свои заголовки». `env:VAR` не маскируется: это имя
    переменной, а не секрет — значение подставит `resolve_key` на сервере."""
    if not v:
        return v
    if key_env_name(v) is not None:
        return v
    return "•••" + v[-4:]


def mask_headers(hdrs: Any) -> Any:
    """Словарь своих заголовков профиля с закрытыми значениями (ключи как есть)."""
    if not isinstance(hdrs, dict):
        return hdrs
    return {k: mask_header_value(v) if isinstance(v, str) else v for k, v in hdrs.items()}


def unmask_header_value(v: str, saved: Any) -> str:
    """Значение заголовка из формы: маска «•••…» = «не менял» -> сохранённое значение.

    Без этого сохранение с маской записало бы в конфиг саму маску — то есть
    потеряло бы токен, а профиль показывался бы как рабочий (ровно тот же класс
    ошибки, от которого защищён ключ профиля)."""
    v = (v or "").strip()
    if v.startswith("•••") and isinstance(saved, str) and saved:
        return saved
    return v


def unmask_headers(hdrs: Any, saved: Any) -> dict[str, str]:
    """Словарь заголовков из формы: значения-маски подменяются сохранёнными.

    Ключ-маска при смене адреса и провайдера уже отвергается в `save_profile`, так
    что сохранённый токен к чужому адресу этой дверью не уедет."""
    saved_h = saved if isinstance(saved, dict) else {}
    if not isinstance(hdrs, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in hdrs.items():
        if not (isinstance(k, str) and isinstance(v, str)):
            continue
        out[k] = unmask_header_value(v, saved_h.get(k))
    return out


def resolve_header_mask(hdrs: Any, prof_name: str | None) -> dict[str, str]:
    """Заголовки для ЖИВОГО вызова (проверка, список моделей): маски не уезжают.

    Интерфейс шлёт форму как есть, а в ней значение из `GET /api/ai_config` уже
    закрыто маской. Отправить маску провайдеру значит сломать проверку связи на
    ровном месте (`•••xxxx` вместо токена) — поэтому маска подменяется сохранённым
    значением профиля."""
    saved = load_ai_config()["profiles"].get(prof_name or "", {})
    return unmask_headers(hdrs, saved.get("headers") if isinstance(saved, dict) else None)


def saved_profile_for_masked(p: dict[str, Any], name: str | None) -> dict[str, Any] | None:
    """Профиль из формы с ключом-маской: отдать СОХРАНЁННЫЙ профиль целиком, иначе None.

    Маска значит «ключ не менял», но base_url и headers из тела запроса — это данные
    запроса, их подставляет кто угодно, а ключ к ним подставлялся настоящий. Снаружи
    это закрыто гвардом Sec-Fetch-Site, но вместе с XSS в интерфейсе
    давало увод сохранённого ключа на чужой адрес: /api/ai_test и /api/ai_models ходят
    туда, куда сказано в теле. Профиля нет — None, поведение как раньше.
    """
    if not (p.get("api_key") or "").strip().startswith("•••"):
        return None
    return load_ai_config()["profiles"].get(name or "") or None


def normalize_base_url(u: Any) -> Any:
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


def parse_headers_text(text: str | None) -> dict[str, str]:
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


def apply_profile_headers(headers: Any, prof: Any) -> Any:
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


def _profile_dict(name: str | None, prof: dict[str, Any]) -> dict[str, Any]:
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
    c = prof.get("concurrency")
    if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 16:
        res["concurrency"] = c
    return res


def resolve_profile(model: str | None = None, url: str | None = None, name: str | None = None) -> dict[str, Any]:
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


def resolve_omni_profile() -> dict[str, Any] | None:
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


def omni_local_engine() -> str:
    """Какой ЛОКАЛЬНЫЙ движок слушает звук: 'qwen' | 'gigaam'. Для облачного
    профиля не вызывается (resolve_omni_profile вернёт dict). Неизвестное имя -> qwen."""
    name = load_ai_config().get("active_omni") or OMNI_LOCAL
    return OMNI_LOCAL_ENGINES.get(name, "qwen")


def cut_asr_engine(emit: Callable[..., Any] | None = None) -> str:
    """Какой ASR-движок делает пословные тайминги для нарезки.

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
    except ReelsiError: raise
    except Exception as ex:
        log.warning("движок нарезки «%s» не проверен: %s — беру gigaam",
                    engine, ex)
    return "gigaam"


def model_device_cfg() -> str:
    """«Где считать модели» (сервис моделей): `auto` / `cuda` / `cpu`.

    Читает то же поле, что пишет действие `set_model_device`, — но через модуль
    устройства (`core.device.model_device`), а не своим разбором: слово проверяется
    в ОДНОМ месте, и «что сохранили» и «что применили» не могут разойтись. Отказ
    конфига (нет файла, битый JSON) — «авто»: сервис обязан подняться на любой машине.
    """
    try:
        return _device.model_device()
    except Exception:
        return _device.DEVICE_AUTO


# Свечение жёлтого глитча интро: встроенные эффекты или Deep Glow 2
GLITCH_GLOW_MODES = ("builtin", "deepglow2")


def glitch_glow_mode() -> str:
    """Режим свечения жёлтого глитча: 'builtin' (Blur + Glo2) | 'deepglow2' (сторонний плагин)."""
    val = load_ai_config().get("glitch_glow")
    return val if val in GLITCH_GLOW_MODES else "builtin"


def ae_build_workers_cfg() -> Any:
    """Значение настройки ae_build_workers из ai_config (по умолчанию 'auto')."""
    env = os.environ.get("REELSI_AE_BUILD_WORKERS")
    if env is not None:
        return env
    try:
        return load_ai_config().get("ae_build_workers", "auto")
    except Exception:
        return "auto"


