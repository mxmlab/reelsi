# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Возможности моделей из каталога models.dev — единственный источник правды.

Раньше «что умеет модель» жило в списках подстрок в config.py (REASONING_MODELS и
пр.), и они разъезжались с реальностью. Каталог models.dev — открытый, публичный,
тот же самый читают opencode и pi (кэш opencode — ~/.cache/opencode/models.json).
Наш код ходит в него за всеми возможностями модели: caps().

Каталог — НЕ точка отказа ИИ-шагов: сеть недоступна — работаем на прошлом кэше с
диска и пишем строку в лог; кэша нет — caps() вернёт всё None, и вызывающие ведут
себя как раньше (фолбэк-списки в config.py). Обновляется раз в сутки при первом
обращении, кнопка «Обновить» в ⚙ тянет его силой (force).
"""
import os, json, time
import urllib.request

from .config import AI_CONFIG_PATH, APP_NAME, APP_REFERER
from core.app_meta import env, http_req
from core.fileio import atomic_json_dump

CATALOG_URL = "https://models.dev/api.json"
CATALOG_TTL = 86400        # раз в сутки

# Наш слуг провайдера -> ключ в каталоге models.dev. Совпадают только те, где
# слуг и правда означает вендора. "openai" у нас — «OpenAI-совместимый, свой
# URL» (прокси, агрегатор), а не вендор OpenAI: взять его запись как свою значит
# показать чужую цену. "lmstudio" — локальная сборка, вендора у неё нет.
CATALOG_KEY = {"openrouter": "openrouter", "anthropic": "anthropic"}


def catalog_cache_path():
    """Кэш каталога — файл models_dev.json рядом с ai_config.json.

    Своя REELSI_MODELS_DEV: без неё изолированный профиль 5098 (REELSI_AI_CONFIG
    указывает на ai_config.test.json в той же папке) писал бы в боевой кэш и
    разменивал бы его свежесть с рабочим интерфейсом."""
    return env("MODELS_DEV") or os.path.join(os.path.dirname(AI_CONFIG_PATH), "models_dev.json")


def _load_disk(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_disk(path, data):
    """Записать кэш атомарно (core.fileio.atomic_json_dump).

    Имя tmp уникально (mkstemp внутри fileio), а не фиксированный path+".tmp": две
    одновременные кнопки «Обновить список» (вкладка ИИ и вкладка Видео) писали в
    ОДИН и тот же файл, и содержимое кэша перемешивалось — на этом и поймали."""
    try:
        atomic_json_dump(path, data)
    except Exception:
        pass                                  # кэш — не критичный путь


# Состояние каталога в памяти процесса: словарь и время загрузки. По ним не
# долбим сеть чаще раза в сутки (CATALOG_TTL).
_CATALOG = None
_CATALOG_TS = 0.0


def ensure_catalog(emit=None, force=False):
    """Каталог models.dev в памяти процесса (кэш на диск). force — кнопка
    «Обновить»: тянем сеть в любом случае.

    Порядок: свежий кэш с диска -> сеть -> старый кэш (сеть не ответила) ->
    пустой словарь. Сбой сети не роняет ИИ-шаги: работаем на прошлом кэше и
    пишем строку в лог; кэша нет — все возможности «unknown» (caps вернёт
    всё None) и вызывающие ведут себя как раньше."""
    global _CATALOG, _CATALOG_TS
    emit = emit or (lambda *a, **k: None)
    now = time.time()
    if not force and _CATALOG is not None and now - _CATALOG_TS < CATALOG_TTL:
        return _CATALOG
    path = catalog_cache_path()
    disk = _load_disk(path)
    if not force and disk and now - os.path.getmtime(path) < CATALOG_TTL:
        _CATALOG, _CATALOG_TS = disk, now
        return disk
    try:
        req = http_req(CATALOG_URL, headers={
            "X-Title": APP_NAME, "HTTP-Referer": APP_REFERER})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        _save_disk(path, data)
        _CATALOG, _CATALOG_TS = data, now
        emit("каталог models.dev: обновлён ({count} провайдеров)", count=len(data))
        return data
    except Exception as e:
        if disk:
            _CATALOG, _CATALOG_TS = disk, now
            emit("! каталог models.dev недоступен ({err}) — работаю на кэше", err=e)
            return disk
        _CATALOG, _CATALOG_TS = {}, now
        emit("! каталог models.dev недоступен ({err}) и кэша нет — возможности моделей неизвестны", err=e)
        return {}



def caps(provider, model, emit=None):
    """Что умеет модель из каталога models.dev.

    Возвращает {reasoning, reasoning_kind, efforts, structured_output, temperature,
    out_limit, ctx_limit, cache, default, cost, catalog_provider}. Неизвестная модель
    (свой сервер, локальная сборка LM Studio) или каталога нет — все поля None,
    вызывающий делает как раньше.

    reasoning_kind — как провайдером управляются размышления: "effort" (уровень из
    efforts), "budget_tokens" (явное число) или "toggle" (только вкл/выкл). У модели
    может быть несколько типов (deepseek-v4-flash: toggle + effort) — берём
    информативный effort, если он есть.
    default — «думает по умолчанию»: в каталоге это toggle-тип (размышления можно
    ВЫКЛЮЧИТЬ — значит по умолчанию они включены); для таких моделей off в селекте
    «Ум» это активное действие, а не «ничего не делать».
    cache — поддерживает ли provider-side prompt caching (в каталоге это наличие
    cache_read в цене). cost — USD за миллион токенов (для цены рядом с моделью).
    catalog_provider — имя провайдера каталога, откуда взята запись (при точном
    совпадении по шагу 1 это сам provider; ничего не нашли — None)."""
    data = ensure_catalog(emit=emit)
    if not isinstance(data, dict):
        data = {}

    entry = None
    cat_prov = None
    exact_match = False

    m_str = model or ""
    p_str = provider or ""

    # 1. Точное совпадение под своим провайдером
    k = CATALOG_KEY.get(p_str)
    if k:
        prov = data.get(k)
        if prov:
            entry = (prov.get("models") or {}).get(m_str)
            if entry:
                cat_prov = k
                exact_match = True

    # 1b. Литеральный data[provider] — когда имя нашего слуга совпало с именем
    # провайдера каталога, но в CATALOG_KEY его нет (наш "openai" против вендора OpenAI).
    # Запись берём (лимиты и уровни усилий те же), но exact_match остаётся False (cost = None).
    if not entry and p_str in data:
        p_prov = data.get(p_str)
        if p_prov:
            p_entry = (p_prov.get("models") or {}).get(m_str)
            if p_entry:
                entry = p_entry
                cat_prov = p_str

    # 2. Вендор из префикса id (deepseek/deepseek-v4-flash -> deepseek, deepseek-v4-flash)
    if not entry and "/" in m_str:
        vendor, _, tail = m_str.partition("/")
        v_prov = data.get(vendor)
        if v_prov:
            entry = (v_prov.get("models") or {}).get(tail)
            if entry:
                cat_prov = vendor

    # 3. Провайдер openrouter — точное совпадение id
    if not entry:
        or_prov = data.get("openrouter")
        if or_prov:
            entry = (or_prov.get("models") or {}).get(m_str)
            if entry:
                cat_prov = "openrouter"

    # 4. Любой другой провайдер каталога с точным совпадением id (по алфавиту).
    # У одной и той же модели разные хостеры описаны в каталоге с разной полнотой,
    # и «первый по алфавиту» иногда самый бедный; уровни усилий — свойство самой
    # модели, поэтому берём запись, где они есть.
    if not entry:
        for p_name in sorted(data.keys()):
            p_entry = ((data[p_name] or {}).get("models") or {}).get(m_str)
            if p_entry and p_entry.get("reasoning_options"):
                entry = p_entry
                cat_prov = p_name
                break
        if not entry:
            for p_name in sorted(data.keys()):
                p_entry = ((data[p_name] or {}).get("models") or {}).get(m_str)
                if p_entry:
                    entry = p_entry
                    cat_prov = p_name
                    break

    if not entry:
        return {"reasoning": None, "reasoning_kind": None, "efforts": None,
                "structured_output": None, "temperature": None,
                "out_limit": None, "ctx_limit": None, "cache": None,
                "default": None, "cost": None, "catalog_provider": None}
    opts = entry.get("reasoning_options") or []
    kinds = [o.get("type") for o in opts if isinstance(o, dict)]
    kind, efforts = None, None
    if "effort" in kinds:
        kind = "effort"
        efforts = next((o.get("values") for o in opts if o.get("type") == "effort"), None)
    elif "budget_tokens" in kinds:
        kind = "budget_tokens"
    elif "toggle" in kinds:
        kind = "toggle"
    limit = entry.get("limit") or {}
    cost = entry.get("cost") or {}
    cost_val = ({"input": cost.get("input"), "output": cost.get("output")} if cost else None) if exact_match else None
    return {
        "reasoning": entry.get("reasoning") if isinstance(entry.get("reasoning"), bool) else None,
        "reasoning_kind": kind,
        "efforts": list(efforts) if efforts else None,
        "structured_output": entry.get("structured_output"),
        "temperature": entry.get("temperature"),
        "out_limit": limit.get("output"),
        "ctx_limit": limit.get("context"),
        "cache": bool(cost.get("cache_read")),
        "default": "toggle" in kinds,
        "cost": cost_val,
        "catalog_provider": cat_prov,
    }


def valid_level(provider, model, lvl, emit=None):
    """Принимает ли модель этот уровень «ума» (для set_reasoning_step).

    Уровни из каталога (effort-значения: low/high/max/xhigh/none/minimal) плюс
    «off»."""
    if lvl == "off":
        return True
    c = caps(provider, model, emit=emit)
    if c.get("efforts"):
        return lvl in c["efforts"]
    return False


# Порядок уровней для понижения «на ступень» при повторе битого JSON (задание BW).
# «max»/«xhigh»/«minimal»/«none» из каталога efforts тоже участвуют: понижение идёт
# по рангу, а не по нашему фиксированному набору.
_LEVEL_RANK = {"off": 0, "none": 0, "minimal": 1, "low": 2, "medium": 3,
               "high": 4, "xhigh": 5, "max": 6}


def nearest_supported_level(provider, model, lvl, emit=None):
    """Ближайший уровень СНИЗУ из efforts модели в каталоге (задание BW).

    У deepseek-v4-flash в каталоге только low/high/max — слать medium нельзя:
    провайдер молча мапит невалидный уровень в свой default_effort (high) и жжёт
    на размышления 78 тысяч токенов («забивает всё окно и не выводит вывод»).
    Уровней ниже выбранного нет — выключаем (off), чем слать заведомо невалидный.
    Каталог молчит про модель (efforts пустые) — возвращаем уровень как есть:
    без знания о модели понижать нечего. Единственный источник правды здесь —
    каталог: копия этой логики в llm.py удалена, чтобы ответы «что реально
    уходит в API» и «что показывает интерфейс» не разъезжались (задание по
    UI-состояниям)."""
    if lvl == "off":
        return lvl
    supported = caps(provider, model, emit=emit)["efforts"] or []
    if not supported:
        return lvl
    cands = [l for l in supported if _LEVEL_RANK.get(l, 99) <= _LEVEL_RANK.get(lvl, 99)]
    return (max(cands, key=lambda l: _LEVEL_RANK.get(l, 0)) if cands else "off")
