# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вызов модели: стрим, отмена, поколения вызовов, выгрузка LM Studio.

CANCEL и EPOCH живут здесь и здесь же ПЕРЕПРИСВАИВАЮТСЯ. Наружу они не
переэкспортируются намеренно: `from .llm import CANCEL` связал бы имя один раз, и
отмена перестала бы работать. Снаружи есть функции — cancelled(), is_current().

Поколение вызова (EPOCH) нужно, чтобы «Стоп» и перезагрузка страницы не убивали
вызов, стартовавший ПОСЛЕ них: устаревший поток видит чужой epoch и выходит сам.
"""
import os, re, json, shutil, subprocess, time, threading
import urllib.request, urllib.error
from .config import (APP_NAME, APP_REFERER, DEFAULT_URL, REASONING_LEVELS,
                     AI_LOG_PATH, AI_LOG_CAP, AI_LOG_MAX_MB,
                     REASONING_BUDGET,
                     apply_profile_headers, model_supports_caching, resolve_profile)
from . import catalog
from core.fileio import atomic_text_write
from core.umsg import ReelsiError, umsg
from core.app_meta import console_emit, http_req, t
from core.applog import get_logger

log = get_logger(__name__)

# Лок ИИ-лога: записи идут из потоков джоба/одиночных вызовов, а чистка
# переписывает файл — без замка две записи могли скушать друг друга.
_AI_LOG_LOCK = threading.Lock()


def ai_log_append(step, prof, ok, in_t=None, out_t=None, rt=None, finish=None,
                  ms=None, err=None):
    """Дописать одну запись ИИ-вызова в ai_calls.jsonl (JSONL, по строке на вызов).

    step — имя шага ("cut"/"yellow"/"inserts"/"intro"/"plan" или что передал
    вызывающий), prof — словарь профиля (модель/провайдер/reasoning), токены и
    время — что вернул провайдер. Ошибки тоже пишутся (ok=False): сгоревшие 200k
    токенов видны именно по паре «огромный out/rt + err» — на этом ловился баг.

    Авточистка: файл держим не больше AI_LOG_MAX_MB и не больше AI_LOG_CAP строк —
    при превышении переписываем, оставляя хвост (самое свежее). Чистка только при
    росте размера, чтобы не переписывать файл на каждую строку. Переписывает
    core.fileio.atomic_text_write — запись не оставит полупустой файл, если что-то
    упадёт.
    """
    entry = {
        "ts": int(time.time()), "step": step or "-",
        "model": (prof or {}).get("model") or "-",
        "provider": (prof or {}).get("provider") or "-",
        "reasoning": (prof or {}).get("reasoning") or "off",
        "ok": ok, "in": in_t, "out": out_t, "rt": rt,
        "finish": finish, "ms": (int(ms) if ms is not None else None), "err": err,
    }
    try:
        with _AI_LOG_LOCK:
            os.makedirs(os.path.dirname(AI_LOG_PATH) or ".", exist_ok=True)
            with open(AI_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _ai_log_prune_if_big()
    except ReelsiError: raise
    except Exception:
        pass                                   # лог не должен ронять вызов модели


def _ai_log_prune_if_big():
    """Если файл разросся — переписать, оставив последние AI_LOG_CAP строк.

    Держим ровно КАП последних (файл append-only, записи идут в хронологическом
    порядке — хвост и есть свежее). Проверку размера делаем на каждой записи
    (getsize дёшев), переписывание — только когда превышен порог."""
    try:
        if os.path.getsize(AI_LOG_PATH) <= AI_LOG_MAX_MB * 1024 * 1024:
            return
        with open(AI_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= AI_LOG_CAP:
            return
        atomic_text_write(AI_LOG_PATH, "".join(lines[-AI_LOG_CAP:]))
    except ReelsiError: raise
    except Exception:
        pass                                   # чистка — не критичный путь


# Кнопка «Стоп» в webui (/api/ai_stop): выгрузка модели обрывает текущую генерацию,
# а этот флаг не даёт ретраю _ask_json перезагрузить модель и начать заново.
# Эндпоинты, запускающие новый ИИ-вызов, сбрасывают его в False.
CANCEL = False

# ПОКОЛЕНИЕ ИИ-ВЫЗОВА. Одного флага CANCEL мало: «Стоп» рвёт fetch у КЛИЕНТА, а поток
# сервера ещё живёт внутри стрима. Если сразу запустить вызов заново (типовой случай:
# отменил medium-reasoning, поставил off), он ставил CANCEL=False — старый поток терял
# признак отмены, продолжал жечь провайдера, а по завершении дёргал unload_ours() и
# убивал генерацию НОВОГО вызова: UI «залипал». Теперь у каждого вызова свой номер:
# старт нового и «Стоп» его увеличивают, поток с устаревшим номером обязан умереть сам.
EPOCH = 0
_EPOCH_LOCK = threading.Lock()
_LOCAL = threading.local()          # epoch потока; нет его (CLI, потоки джоба) — старое поведение


def begin_call():
    """Начать новый одиночный ИИ-вызов из текущего потока: отменить все прошлые
    (их номер устареет) и снять флаг «Стоп». Возвращает номер вызова."""
    global CANCEL, EPOCH
    with _EPOCH_LOCK:
        EPOCH += 1
        CANCEL = False
        _LOCAL.epoch = EPOCH
        return EPOCH


def cancel_call():
    """Кнопка «Стоп»: отменить текущий вызов. Возвращает номер, до которого отменено —
    по нему отложенная выгрузка модели поймёт, что вызов уже сменился новым."""
    global CANCEL, EPOCH
    with _EPOCH_LOCK:
        CANCEL = True
        EPOCH += 1
        return EPOCH


def clear_cancel():
    """Снять «Стоп» для вызова БЕЗ своего номера (джоб, тест связи, генерация
    картинки — они идут пачками, менять им поколение нельзя: отменят друг друга).
    Потоки прошлого одиночного вызова это не воскрешает — их держит устаревший epoch."""
    global CANCEL
    with _EPOCH_LOCK:
        CANCEL = False


def cancelled():
    """Отменён ли ИИ-вызов ТЕКУЩЕГО потока: нажали «Стоп» либо поверх нас стартовал
    новый вызов. Потоки без своего номера (CLI, джоб) смотрят только на CANCEL."""
    ep = getattr(_LOCAL, "epoch", None)
    return bool(CANCEL) if ep is None else (bool(CANCEL) or ep != EPOCH)


def cancel_reason():
    """Почему вызов прерван — «Стоп» или его вытеснил новый запуск (важно различать:
    во втором случае ошибка прилетит клиенту, которого уже нет, а работает новый)."""
    ep = getattr(_LOCAL, "epoch", None)
    if ep is not None and ep != EPOCH and not CANCEL:
        return umsg("cancel_replaced", "вызов заменён новым запуском")
    return umsg("cancelled", "остановлено кнопкой «Стоп»")


def is_current(ep=None):
    """Актуален ли вызов (наш или заданный) — можно ли трогать общий ресурс, например
    выгружать модель LM Studio: устаревший поток этим убил бы чужую генерацию."""
    if ep is None:
        ep = getattr(_LOCAL, "epoch", None)
    return ep is None or (ep == EPOCH and not CANCEL)


# --- Управление VRAM LM Studio (16 ГБ впритык: держим загруженной ОДНУ модель) ---
def _lms_bin():
    p = shutil.which("lms")
    if p:
        return p
    for c in ("~/.lmstudio/bin/lms.exe", "~/.lmstudio/bin/lms"):
        c = os.path.expanduser(c)
        if os.path.isfile(c):
            return c
    return None


def _api_base(url):
    b = (url or DEFAULT_URL).rstrip("/")
    return b[:-3] if b.endswith("/v1") else b


def loaded_info(url=None):
    """Записи загруженных сейчас моделей (нативный REST /api/v0/models). None = не удалось."""
    try:
        with urllib.request.urlopen(_api_base(url) + "/api/v0/models", timeout=5) as r:
            data = json.load(r)
        return [m for m in data.get("data", []) if m.get("state") == "loaded"]
    except ReelsiError: raise
    except Exception:
        return None


def loaded_models(url=None):
    """id загруженных сейчас моделей. None = не удалось."""
    info = loaded_info(url)
    return None if info is None else [m["id"] for m in info]


# Реестр моделей LM Studio, загруженных ТОЛЬКО нами (Reelsi).
# Жалоба 2026-08-21: выгрузка `lms unload --all` слепо сносила чужие модели, загруженные
# пользователем руками для другой работы, в том числе при работе в облаке.
# Список живёт в памяти процесса, потокобезопасен: перезапустили сервер — список пуст,
# значит наших моделей нет и мы никого не трогаем.
_OUR_MODELS = set()
_OUR_MODELS_LOCK = threading.Lock()


def our_loaded_models():
    """Копия множества моделей LM Studio, загруженных Reelsi."""
    with _OUR_MODELS_LOCK:
        return set(_OUR_MODELS)


def unload_ours(emit=console_emit):
    """Выгрузить ТОЛЬКО те модели LM Studio, которые Reelsi загрузил сам.

    Если ничего своего не загружено (частый случай — работаем на облачном провайдере
    или только что запустились), функция молчит и в LM Studio не ходит вообще:
    чужие модели пользователя остаются в памяти."""
    with _OUR_MODELS_LOCK:
        to_unload = list(_OUR_MODELS)
    if not to_unload:
        return
    lms = _lms_bin()
    if not lms:
        return
    for m in to_unload:
        try:
            subprocess.run([lms, "unload", m], capture_output=True, timeout=60)
            with _OUR_MODELS_LOCK:
                _OUR_MODELS.discard(m)
            emit("  LM Studio: выгружена {model}", model=m)
        except ReelsiError: raise
        except Exception as e:
            emit("  (lms unload не сработал: {err})", err=e)


def ensure_loaded(model, url=None, ttl=1800, emit=console_emit):
    """Гарантировать, что модель загружена в LM Studio.
    Выгружает ТОЛЬКО наши ранее загруженные модели (если загружали другую),
    чужие модели пользователя не трогает (жалоба 2026-08-21).
    Best-effort: если lms/REST недоступны, молча полагаемся на JIT."""
    loaded = loaded_models(url)
    if loaded is not None and model in loaded:
        with _OUR_MODELS_LOCK:
            _OUR_MODELS.add(model)
        return
    lms = _lms_bin()
    if not lms:
        return
    try:
        # Выгружаем только НАШИ модели, чужие не трогаем
        with _OUR_MODELS_LOCK:
            to_unload = [m for m in _OUR_MODELS if m != model]
        for m in to_unload:
            try:
                subprocess.run([lms, "unload", m], capture_output=True, timeout=60)
                with _OUR_MODELS_LOCK:
                    _OUR_MODELS.discard(m)
            except ReelsiError: raise
            except Exception as ex:
                log.warning("не выгрузил модель «%s» из LM Studio: %s — "
                            "видеопамяти может не хватить", m, ex)
        subprocess.run([lms, "load", model, "--gpu", "max", "--ttl", str(ttl)],
                       capture_output=True, timeout=300)
        with _OUR_MODELS_LOCK:
            _OUR_MODELS.add(model)
        emit("  LM Studio: загружена {model}", model=model)
    except ReelsiError: raise
    except Exception as e:
        emit("  (lms load не сработал: {err}; полагаюсь на JIT)", err=e)


def warn_foreign_models(emit=console_emit):
    """Предупредить в лог, если в LM Studio висит сторонняя (не наша) модель,
    которая может занять VRAM перед тяжёлым локальным шагом (ASR/GigaAM/Omni).
    Информирование вместо самоуправства: работу не останавливает и модель не выгружает."""
    try:
        info = loaded_info()
        if not info:
            return
        with _OUR_MODELS_LOCK:
            ours = set(_OUR_MODELS)
        foreign = [m for m in info if m.get("type") != "embeddings" and m.get("id") not in ours]
        for m in foreign:
            mid = m.get("id") or "unknown"
            emit("⚠ В LM Studio загружена сторонняя модель «{model}» — может не хватить VRAM", model=mid)
    except ReelsiError: raise
    except Exception:
        pass  # LM Studio не ответил — предупреждать о чужих моделях не о чем


def _extract_json_obj(text):
    """Вытащить последний сбалансированный {...}-объект из текста. Reasoning-модели
    (qwen3, deepseek-r1) обрамляют ответ размышлениями и <think>…</think>; итоговый
    JSON — в самом конце. Сканируем от последней '}' назад к её паре '{'."""
    text = text or ""
    end = text.rfind("}")
    if end < 0:
        return text
    depth = 0
    for i in range(end, -1, -1):
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                return text[i:end + 1]
    return text[:end + 1]


def _ask_json(system, user, schema, model=None, url=None, max_tokens=4096, emit=console_emit,

              temperature=0.3, retries=1, reasoning=None, profile=None, step=None):
    """Один структурированный JSON-вызов АКТИВНОГО провайдера (профиль из ai_config.json).
    model/url — переопределения (CLI/обратная совместимость). Диспетчер:
    anthropic -> Claude API (SDK); остальные -> OpenAI-совместимый chat/completions.

    reasoning — уровень размышлений ТОЛЬКО для этого вызова (off/low/medium/high),
    поверх уровня профиля. Нужен потому, что шаги разные: жёлтым словам и вставкам
    думать не надо (там reasoning жёг весь бюджет), а нарезке — надо: на C1353
    без размышлений модель систематически брала в скобки ПОЗДНИЙ заход дубля
    вместо раннего, с размышлениями — все скобки встали правильно."""
    prof = resolve_profile(model, url, name=profile)
    if reasoning:
        prof = dict(prof, reasoning=reasoning)
    if prof["provider"] == "anthropic":
        return _ask_anthropic(prof, system, user, schema, max_tokens=max_tokens,
                              emit=emit, retries=retries, step=step)
    return _ask_openai(prof, system, user, schema, max_tokens=max_tokens, emit=emit,
                       temperature=temperature, retries=retries, step=step)


class UpstreamBusy(RuntimeError):
    """Временная ошибка апстрима (5xx) — имеет смысл просто повторить запрос."""


class StreamStalled(UpstreamBusy):
    """Поток жив (кипэлайвы идут), но ответ перестал расти — апстрим замолчал.

    Лечится тем же повтором, что и занятость, но счётчик СВОЙ и короче: занятость
    отдаётся сразу и ждать её дёшево, а каждый повтор зависшего стрима стоит
    STALL_MID секунд ожидания вслепую. Три таких повтора в пакетном прогоне — это
    десяток минут, за которые юзер успевает решить, что всё сломалось."""


# Сколько раз повторять запрос, когда апстрим провайдера занят (5xx в теле потока).
# Отдельно от `retries` (тот — бюджет починки битого JSON, по умолчанию 1): занятость
# лечится ТОЛЬКО ожиданием, и одного повтора мало — free-эндпоинты OpenRouter отдают
# ResourceExhausted пачками. Паузы 2/4/6с.
BUSY_RETRIES = 3
STALL_RETRIES = 1

# Сколько секунд ответ может НЕ РАСТИ, прежде чем считать вызов зависшим.
# STALL_MID — после того, как чанки уже пошли: живая генерация даёт их каждые
# 1-3с (замер на реальных вызовах: тики 3/6/9/13/16с, каждый с приростом), так
# что полторы минуты тишины посреди ответа — это не «медленно», это конец.
# STALL_FIRST — пока не пришло ничего: сюда попадают очередь free-эндпоинта и
# загрузка локальной модели в VRAM, их обрывать нельзя.
STALL_MID = 90
STALL_FIRST = 240
STALL_NOTE = 15          # с какой тишины писать про неё в лог, не дожидаясь обрыва


def _read_stream(r, emit, tick=3.0):
    """Собрать SSE-поток chat/completions в форму обычного (не-стримингового) тела:
    {"choices": [{"message": {...}, "finish_reason": ...}], "usage": {...}}, чтобы
    разбор ниже не менялся.

    Зачем стрим: (1) видно, что ИИ работает, а не завис — раз в tick секунд шлём в
    лог, сколько уже пришло; сразу видно и «модель ушла в размышления» (растут только
    они, ответ пуст); (2) «Стоп» рвёт соединение на ближайшем чанке, а не ждёт конца
    генерации.

    Тик и сторож простоя считаются на КАЖДОЙ строке, включая кипэлайвы. Раньше они
    стояли после `continue` для не-data строк, и это давало худший из возможных
    исходов (поймано 2026-08-10 на пакетной разметке интро): модель написала 505
    симв. за 16с, апстрим замолчал НАВСЕГДА, а OpenRouter продолжил слать
    ": OPENROUTER PROCESSING". Лог замер на 16-й секунде, вызов висел 11+ минут и
    сам бы не отвалился никогда — `urlopen(timeout=600)` меряет тишину В СОКЕТЕ, а
    её не было: каждый кипэлайв обнулял таймаут. Пакетный прогон встал намертво."""
    content, reasoning, finish, usage = [], [], None, {}
    t0 = last = grew = time.time()
    nc = nr = 0                                       # длины считаем на ходу: sum() на каждой строке — O(n²)
    seen = (0, 0)                                     # длины на момент последнего РОСТА ответа
    for line in r:
        if cancelled():
            raise ReelsiError(cancel_reason())
        line = line.decode("utf-8", "replace").strip()
        if line.startswith("data:"):                  # не-data — ": OPENROUTER PROCESSING" и пустые
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                ev = json.loads(body)
            except json.JSONDecodeError:
                ev = None
            if ev is not None and ev.get("error"):    # OpenRouter кладёт ошибки и в 200-поток
                err = ev["error"]
                code = err.get("code") if isinstance(err, dict) else None
                txt = json.dumps(err, ensure_ascii=False)[:400]
                if isinstance(code, int) and code >= 500:
                    # апстрим провайдера отвалился (free-эндпоинты OpenRouter ловят
                    # ResourceExhausted пачками) — это лечится повтором, а не правкой
                    raise UpstreamBusy(txt)
                raise ReelsiError(umsg("provider_error", "провайдер вернул ошибку: " + txt, txt=txt))
            if ev is not None:
                if ev.get("usage"):
                    usage = ev["usage"]               # приходит последним чанком (include_usage)
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        content.append(d["content"])
                        nc += len(d["content"])
                    # размышления идут отдельным полем и в content НЕ попадают (у разных провайдеров:
                    # reasoning, reasoning_content, reasoning_text, reasoning_details)
                    rt = d.get("reasoning") or d.get("reasoning_content") or d.get("reasoning_text")
                    if not rt and d.get("reasoning_details"):
                        rd = d["reasoning_details"]
                        if isinstance(rd, list):
                            rt = "".join(item.get("text", "") for item in rd if isinstance(item, dict) and item.get("text"))
                        elif isinstance(rd, dict):
                            rt = rd.get("text") or rd.get("content")
                    if rt:
                        reasoning.append(rt)
                        nr += len(rt)
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        now = time.time()
        if (nc, nr) != seen:                          # РОСТ ответа, а не просто «строка пришла»
            seen, grew = (nc, nr), now
        idle = now - grew
        # Сторож простоя. Меряем тишину по РОСТУ ответа, а не по байтам в сокете:
        # именно кипэлайв делает молчащий апстрим неотличимым от живого. Пока ни
        # одного чанка не было — окно шире: очередь на free-эндпоинте и загрузка
        # локальной модели в VRAM легко съедают минуту, и это не поломка.
        if idle >= (STALL_MID if seen != (0, 0) else STALL_FIRST):
            raise StreamStalled(f"ответ не растёт {idle:.0f}с "
                                + (f"(пришло {nc} симв.)" if nc else
                                   f"(размышлений {nr} симв., ответа нет)" if nr else
                                   "(не пришло ни одного чанка)"))
        if now - last >= tick:
            last = now
            el = now - t0
            # Так видно РОВНО две вещи, ради которых тик и нужен: модель ещё жива
            # (секунды растут) и на что она тратит время — думает или уже пишет
            # ответ. Плюс напоминание, что ждать не обязательно: есть «Стоп».
            if nc:
                if nr:
                    emit("  … {el:.0f}с · пишет ответ: {nc} симв. (размышлений {nr} симв.){silent}",
                         el=el, nc=nc, nr=nr,
                         silent=(" " + t("· тишина {idle:.0f}с (провайдер шлёт только кипэлайвы)", idle=idle)
                                 if idle >= STALL_NOTE else ""))
                else:
                    emit("  … {el:.0f}с · пишет ответ: {nc} симв.{silent}",
                         el=el, nc=nc,
                         silent=(" " + t("· тишина {idle:.0f}с (провайдер шлёт только кипэлайвы)", idle=idle)
                                 if idle >= STALL_NOTE else ""))
            elif nr:
                emit("  … {el:.0f}с · думает: {nr} симв. размышлений, ответа пока нет{silent}{stop}",
                     el=el, nr=nr,
                     silent=(" " + t("· тишина {idle:.0f}с (провайдер шлёт только кипэлайвы)", idle=idle)
                             if idle >= STALL_NOTE else ""),
                     stop=(" " + t("· не хочешь ждать — «Стоп»") if el >= 30 else ""))
            else:
                emit("  … {el:.0f}с · ждём первый чанк от провайдера{silent}{stop}",
                     el=el,
                     silent=(" " + t("· тишина {idle:.0f}с (провайдер шлёт только кипэлайвы)", idle=idle)
                             if idle >= STALL_NOTE else ""),
                     stop=(" " + t("· не хочешь ждать — «Стоп»") if el >= 30 else ""))
    raw = "".join(content).strip() or "".join(reasoning)
    return {"choices": [{"message": {"content": raw}, "finish_reason": finish}],
            "usage": usage}


def _ask_openai(prof, system, user, schema, max_tokens=4096, emit=console_emit,

                temperature=0.3, retries=1, step=None):
    """Обёртка над _ask_openai_impl: ловит любой исход вызова (успех/ReelsiError)
    и пишет его в ai_calls.jsonl с токенами. Импортируется наружу (api/ai.py,
    aicut.__init__), сигнатура прежняя + необязательный `step` для имён шагов.
    Стартовую запись (ok=null) пишем СРАЗУ, а не в конце: вызов, который думает
    минуты, обязан быть виден в логе с самого начала, иначе «где мой вызов?»"""
    t0 = time.time()
    ai_log_append(step, prof, ok=None)
    try:
        return _ask_openai_impl(prof, system, user, schema, max_tokens=max_tokens,
                                emit=emit, temperature=temperature, retries=retries,
                                step=step, _t0=t0)
    except (ReelsiError, SystemExit) as e:
        ai_log_append(step, prof, ok=False, ms=(time.time() - t0) * 1000, err=str(e))
        raise


def _downgrade_level(lvl, supported, provider=None, model=None):
    """Уровень на ступень ниже для повтора битого JSON: тот же бюджет
    размышлений второй раз не жжём. По каталогу efforts (порядок в нём — это
    порядок провайдера), иначе по нашему порядку уровней."""
    if lvl == "off":
        return lvl
    if supported and lvl in supported:
        i = supported.index(lvl)
        return supported[i - 1] if i > 0 else "off"
    if supported:
        # lvl не в efforts модели — понижаем до ближайшего СНИЗУ. Единственная
        # копия этой логики живёт в каталоге (nearest_supported_level), здесь её
        # держать нельзя: интерфейс и отправка разъехались бы (задание по UI-состояниям).
        return catalog.nearest_supported_level(provider, model, lvl)
    order = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
    i = order.index(lvl) if lvl in order else 2
    return order[max(i - 1, 0)]


def _ask_openai_impl(prof, system, user, schema, max_tokens=4096, emit=console_emit,
                     temperature=0.3, retries=1, step=None, _t0=None):
    """OpenAI-совместимый путь: LM Studio (локально) / OpenRouter / свой сервер.
    Ответ читаем стримом (см. _read_stream) — прогресс в логе и мгновенный «Стоп».
    Битый JSON (модель залипла) лечится повтором (для LM Studio — ещё и перезагрузкой
    модели), при повторе уровень размышлений понижается на ступень, чтобы не жечь тот
    же бюджет дважды. Параметр, не понятый провайдером (400 на reasoning /
    cache_control / response_format), выключается и запрос повторяется без него —
    бесплатно, попытка не тратится."""
    url = (prof["base_url"] or DEFAULT_URL).rstrip("/")
    model = prof["model"]
    is_local = prof["provider"] == "lmstudio"
    # Возможности модели из каталога models.dev — что шлём и чего не
    # шлём. Неизвестная модель (свой сервер, локальная сборка) — всё None, и
    # ведём себя как раньше; каталог недоступен — то же самое (не точка отказа).
    c = catalog.caps(prof["provider"], model, emit=emit)
    # temperature: каталог знает, принимает ли модель этот параметр (у luna в
    # каталоге false — 0.8 молча не действовал). Не принимает — не шлём и пишем
    # строку в лог: молча потерянная ручка это ровно то, на чём мы уже попались.
    send_temp = c["temperature"] is not False
    use_max_completion = False
    send_stream_options = True
    if c["temperature"] is False:
        emit("! модель {model} не принимает temperature — {temp} не действует",
             model=model, temp=temperature)
    # response_format: умеет structured_output — шлём схему; нет (в каталоге false)
    # — сразу схема в промпт, без круга «400 -> повтор». None (каталог молчит) —
    # как раньше: пробуем, на 400 выключаем.
    use_rf = True if c["structured_output"] is not False else False
    use_cache = prof["provider"] == "openrouter" and (
        c["cache"] if c["cache"] is not None else model_supports_caching(model))
    # OpenRouter reasoning vs OpenAI reasoning_effort:
    # OpenRouter ожидает payload["reasoning"] = {"effort": lvl} (или budget_tokens / enabled: False).
    # Стандарт OpenAI (o1/o3/gpt-5, CommandCode, Groq, Ollama, LM Studio, vLLM) ожидает reasoning_effort: lvl.
    is_openrouter = prof["provider"] == "openrouter"
    use_reasoning = is_openrouter
    use_effort = is_local or prof["provider"] in ("openai", "commandcode", "deepseek", "groq", "ollama")
    rkind = c["reasoning_kind"]             # effort / budget_tokens / toggle / None
    supported = c["efforts"] or []         # уровни effort-модели (порядок провайдера)
    last_err, attempt, busy, stalled = None, 0, 0, 0
    lvl_base = prof.get("reasoning") or "off"
    lvl_prev = lvl_base
    while attempt <= retries:
        if cancelled():
            raise ReelsiError(cancel_reason())
        if attempt and last_err is not None:
            emit("! ответ модели не разобран ({err}) — повторяю ({attempt}/{retries})…",
                 err=last_err, attempt=attempt, retries=retries)
            if is_local:
                unload_ours(emit=emit)                # перезагрузка лечит залипание
        if is_local:
            ensure_loaded(model, url, emit=emit)     # выгрузит прочие модели и загрузит нужную
        # Фактический уровень на эту попытку: при повторе после битого JSON понижаем
        # на ступень (высокий -> medium -> low -> off) — тот же бюджет размышлений
        # второй раз жечь нельзя, иначе и повтор утонет в том же размышлении.
        # Потом проверяем уровень по каталогу: невалидный провайдер молча мапит в
        # свой default_effort, и это уже стоило пользователю дня.
        lvl = lvl_prev if attempt else lvl_base
        if attempt and lvl != "off":
            lv = _downgrade_level(lvl, supported, prof["provider"], model)
            if lv != lvl:
                emit("! ответ модели не разобран на «{lvl}» — повторяю с «{to_lvl}», бюджет размышлений не жжём дважды",
                     lvl=lvl, to_lvl=lv)
                lvl = lv
        if lvl != "off" and supported:
            fixed = catalog.nearest_supported_level(prof["provider"], model, lvl, emit=emit)
            if fixed != lvl:
                emit("! уровень «{lvl}» модель не поддерживает (есть: {supported}) — беру ближайший ниже: {fixed}",
                     lvl=lvl, supported=", ".join(supported), fixed=fixed)
                lvl = fixed
        if lvl != (prof.get("reasoning") or "off"):
            prof["reasoning"] = lvl    # ФАКТИЧЕСКИ отправленный уровень — в ai_calls.jsonl
        sys_txt = system
        # max_tokens шлём ТОЛЬКО когда ум включён. При off он не останавливает модель
        # (провайдер тарифицирует полный ответ), а нам обрывает уже готовый результат:
        # на C1355 deepseek-v4-flash отдал 9445 токенов, из них 8122 размышлений, и
        # ответ обрезался по нашему же потолку — оплачено, но выброшено. А вот при
        # включённом уме отсутствие потолка даёт обратное: OpenRouter считает effort
        # ПРОЦЕНТОМ от max_tokens запроса (low ≈ 20%), и без него low на модели с
        # выводом 393k — это 78 тысяч токенов раздумий, «забивает всё окно и не выводит
        # вывод». Бюджет уже посчитан шагом: max_tokens = ответ + REASONING_BUDGET
        # (reason_budget в commands.py/omni_cut.py); шлём его, не превышая out_limit
        # модели из каталога.
        payload = {"model": model}
        if send_temp:
            payload["temperature"] = temperature
        if use_rf:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "result", "strict": True, "schema": schema}}
        else:                                        # фолбэк: схема прямо в промпте
            sys_txt = (system + "\n\nОтветь СТРОГО одним JSON-объектом по этой JSON-схеме, "
                       "без пояснений и без markdown:\n" + json.dumps(schema, ensure_ascii=False))
        sys_content = sys_txt
        if use_cache:
            # cache_control кладём в КОНЕЦ статического префикса (system),
            # чтобы провайдер переиспользовал его между вызовами (inserts/yellow
            # шлют один и тот же system+схему, меняется только user-сообщение).
            sys_content = [{"type": "text", "text": sys_txt,
                            "cache_control": {"type": "ephemeral"}}]
        payload["messages"] = [{"role": "system", "content": sys_content},
                               {"role": "user", "content": user}]
        # OpenRouter reasoning (extended thinking).
        # ВАЖНО: «не прислали параметр» != «выключено». Модели, которые думают
        # ПО УМОЛЧАНИЮ (deepseek-v4-flash, hunyuan и пр.), без явного запрета
        # уходят в размышления на весь max_tokens — замер на ролике из 224 слов:
        # жёлтые 126с/out=12001 (обрезано -> битый JSON -> повтор) против
        # 4.2с/out=184 с {"enabled": false}. Поэтому для OFF шлём явный запрет,
        # для включённого ума — по типу управления из каталога:
        # effort (уровень из efforts), budget_tokens (явное число) или toggle
        # (только вкл/выкл). Провайдер, не понявший параметр, ответит 400 ->
        # use_reasoning=False и повтор без него (см. обработку ниже).
        if use_reasoning and (lvl == "off" or lvl in REASONING_LEVELS or supported):
            if lvl == "off":
                payload["reasoning"] = {"enabled": False}
            elif rkind == "budget_tokens":
                payload["reasoning"] = {"budget_tokens": REASONING_BUDGET.get(lvl, 0)}
            elif rkind == "toggle":
                payload["reasoning"] = {"enabled": True}
            else:
                payload["reasoning"] = {"effort": lvl}
            emit("[reasoning] model={model} -> reasoning={reasoning}",
                 model=model, reasoning=payload['reasoning'])
            if lvl != "off":
                # Потолок вывода при включённых размышлениях обязателен: OpenRouter
                # пересчитывает effort в ПРОЦЕНТ от max_tokens, и без него low на
                # модели с большим выводом разрастается в десятки тысяч токенов
                # раздумий (см. комментарий выше). При off потолок не шлём как раньше.
                mt = max_tokens
                if c.get("out_limit"):
                    mt = min(mt, c["out_limit"])
                tok_key = "max_completion_tokens" if use_max_completion else "max_tokens"
                payload[tok_key] = mt
                emit("[max_tokens] model={model} -> max_tokens={mt} (ум {lvl}, бюджет размышлений {budget})",
                     model=model, mt=mt, lvl=lvl, budget=REASONING_BUDGET.get(lvl, 0))
        elif use_effort and (lvl == "off" or lvl in REASONING_LEVELS or supported):
            if rkind == "budget_tokens" and lvl != "off":
                payload["reasoning"] = {"budget_tokens": REASONING_BUDGET.get(lvl, 0)}
                emit("[reasoning] model={model} -> reasoning={reasoning}",
                     model=model, reasoning=payload['reasoning'])
            else:
                if lvl != "off":
                    eff = "low" if lvl == "minimal" else lvl
                    payload["reasoning_effort"] = eff
                    emit("[reasoning] model={model} -> reasoning_effort={effort}",
                         model=model, effort=payload['reasoning_effort'])
                elif is_local or c.get("default"):
                    payload["reasoning_effort"] = "none"
            if lvl != "off":
                mt = max_tokens
                if c.get("out_limit"):
                    mt = min(mt, c["out_limit"])
                tok_key = "max_completion_tokens" if use_max_completion else "max_tokens"
                payload[tok_key] = mt
                emit("[max_tokens] model={model} -> max_tokens={mt} (ум {lvl}, бюджет размышлений {budget})",
                     model=model, mt=mt, lvl=lvl, budget=REASONING_BUDGET.get(lvl, 0))
        if use_cache:
            emit("[cache] model={model} -> cache_control=ephemeral", model=model)
        headers = {"Content-Type": "application/json"}
        if prof.get("api_key"):
            headers["Authorization"] = "Bearer " + prof["api_key"]
        if prof["provider"] == "openrouter":         # атрибуция OpenRouter (рекомендована ими)
            headers["HTTP-Referer"] = APP_REFERER
            headers["X-Title"] = APP_NAME
        headers = apply_profile_headers(headers, prof)
        payload["stream"] = True                     # прогресс в UI + «Стоп» рвёт соединение
        if send_stream_options:
            payload["stream_options"] = {"include_usage": True}
        req = http_req(url + "/chat/completions",
                       data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                resp = _read_stream(r, emit)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except ReelsiError: raise
            except Exception:
                detail = ""
            detail_low = detail.lower()
            if e.code in (400, 422):
                # 1. temperature не принята (reasoning модели o1, o3, o4, gpt-5, deepseek)
                if send_temp and any(w in detail_low for w in ("temperature", "temp is not", "unsupported value: temperature")):
                    send_temp = False
                    emit("! провайдер не принял temperature — повторяю без неё")
                    continue
                # 2. max_tokens vs max_completion_tokens (o1, o3, gpt-4o свежие)
                if "max_completion_tokens" in detail_low or ("max_tokens" in detail_low and any(w in detail_low for w in ("supported", "use", "instead", "parameter"))):
                    if not use_max_completion:
                        use_max_completion = True
                        emit("! провайдер требует max_completion_tokens — повторяю с ним")
                        continue
                # 3. stream_options не принят
                if "stream_options" in detail_low:
                    if send_stream_options:
                        send_stream_options = False
                        emit("! провайдер не принял stream_options — повторяю без них")
                        continue
                # 4. reasoning не принят провайдером
                if (use_effort or use_reasoning) and "reason" in detail_low:
                    use_reasoning = False
                    use_effort = False
                    emit("! провайдер не принял параметры reasoning ({code}) — повторяю без них", code=e.code)
                    continue
                # 5. structured outputs не приняты
                if use_rf and any(w in detail_low for w in ("response_format", "json_schema", "schema", "strict", "additionalproperties", "structured")):
                    use_rf = False
                    payload.pop("response_format", None)
                    sys_txt = (system + "\n\nОтветь СТРОГО одним JSON-объектом по этой JSON-схеме, "
                               "без пояснений и без markdown:\n" + json.dumps(schema, ensure_ascii=False))
                    if use_cache:
                        payload["messages"][0]["content"] = [{"type": "text", "text": sys_txt, "cache_control": {"type": "ephemeral"}}]
                    else:
                        payload["messages"][0]["content"] = sys_txt
                    emit("! провайдер не принял structured outputs ({code}) — повторяю со схемой в промпте", code=e.code)
                    continue
                # 6. cache_control не принят
                if use_cache and "cache" in detail_low:
                    use_cache = False
                    payload["messages"][0]["content"] = sys_txt
                    emit("! провайдер не принял cache_control — повторяю без кэширования")
                    continue
            if e.code in (401, 403):
                # НЕ прячем ответ провайдера: 403 у OpenRouter — это не только
                # «плохой ключ», но и модерация, data policy, недоступность
                # модели для ключа. Без текста ошибки причину не найти.
                raise ReelsiError(umsg("provider_refused",
                                      f"провайдер отказал ({e.code}): {detail or 'без деталей'} "
                                      f"[модель {model}]",
                                      code=e.code, detail=detail or "без деталей", model=model))
            if e.code == 402:
                raise ReelsiError(umsg("no_credits", "у провайдера кончились кредиты (402) — пополни баланс"))
            if e.code == 429:
                raise ReelsiError(umsg("rate_limit", "лимит запросов провайдера (429) — подожди и повтори"))
            if e.code in (500, 502, 503, 504):
                if busy >= BUSY_RETRIES:
                    raise ReelsiError(umsg("provider_unavailable",
                                          f"провайдер недоступен после {BUSY_RETRIES} повторов: {e.code}: {detail}. "
                                          f"Повтори запуск или смени модель в настройках ⚙",
                                          retries=BUSY_RETRIES, err=f"{e.code}: {detail}"))
                busy += 1
                last_err = None
                emit("! апстрим провайдера занят — повторяю ({busy}/{retries})…",
                     busy=busy, retries=BUSY_RETRIES)
                time.sleep(2 * busy)
                continue
            raise ReelsiError(umsg("provider_code", f"провайдер вернул {e.code}: {detail}", code=e.code, detail=detail))
        except StreamStalled as e:
            # Апстрим замолчал посреди ответа, а соединение держится кипэлайвами.
            # Ловим ДО UpstreamBusy (наследник) — счётчик свой, короткий.
            if stalled >= STALL_RETRIES:
                raise ReelsiError(umsg("provider_stalled",
                                      f"провайдер перестал отвечать посреди ответа ({e}) и не "
                                      f"ожил после {STALL_RETRIES} повтора. Запусти шаг заново "
                                      f"или смени модель в настройках ⚙",
                                      retries=STALL_RETRIES, err=e))
            stalled += 1
            last_err = None                      # не наш битый JSON: max_tokens не трогаем
            emit("! провайдер замолчал посреди ответа ({err}) — повторяю ({stalled}/{retries})…",
                 err=e, stalled=stalled, retries=STALL_RETRIES)
            continue
        except TimeoutError as e:
            # Сокет молчал целиком (даже кипэлайвов не было) — urlopen(timeout=…).
            # Без своей ветки это уезжало наверх голым «TimeoutError:» мимо umsg.
            raise ReelsiError(umsg("provider_timeout",
                                  f"провайдер молчал дольше таймаута ({e}) — соединение "
                                  f"оборвано. Запусти шаг заново или смени модель в настройках ⚙",
                                  err=e))
        except UpstreamBusy as e:
            # «Апстрим занят» (5xx в теле 200-потока) — не наша ошибка и не битый JSON,
            # у неё СВОЙ счётчик BUSY_RETRIES: общий `retries` = 1 (это бюджет починки
            # JSON), и занятый провайдер отваливался с первого же раза. Попытка не
            # тратится из бюджета JSON — max_tokens и схему трогать незачем.
            if busy >= BUSY_RETRIES:
                raise ReelsiError(umsg("provider_unavailable",
                                      f"провайдер недоступен после {BUSY_RETRIES} повторов: {e}. "
                                      f"Повтори запуск или смени модель в настройках ⚙",
                                      retries=BUSY_RETRIES, err=e))
            busy += 1
            last_err = None                      # не наш битый JSON: max_tokens не трогаем
            emit("! апстрим провайдера занят — повторяю ({busy}/{retries})…",
                 busy=busy, retries=BUSY_RETRIES)
            time.sleep(2 * busy)
            continue
        except urllib.error.URLError as e:
            who = "LM Studio" if is_local else "провайдер"
            hint = ("Запущен ли сервер и загружена ли модель?" if is_local
                    else "Проверь URL/интернет в настройках ⚙")
            raise ReelsiError(umsg("no_response",
                                  f"{who} не отвечает ({url}): {e}. {hint}",
                                  who=who, url=url, err=e))

        if not resp.get("choices"):     # OpenRouter кладёт ошибки и в 200-тело
            _body = json.dumps(resp, ensure_ascii=False)[:400]
            raise ReelsiError(umsg("empty_response", "провайдер не вернул ответ: " + _body, body=_body))
        ch = resp["choices"][0]
        msg = ch["message"]
        # Некоторые reasoning-модели кладут итоговый JSON в reasoning_content,
        # оставляя content пустым — берём то, что не пусто.
        raw = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "")
        u = resp.get("usage") or {}
        emit("  модель: {model}  токены: in={in_tok} out={out_tok}",
             model=model, in_tok=u.get('prompt_tokens', '?'), out_tok=u.get('completion_tokens', '?'))
        # Обрезанный ответ НЕЛЬЗЯ парсить: _extract_json_obj берёт последний
        # сбалансированный {...}, а у оборванного массива объектов это последний
        # ЦЕЛЫЙ элемент — json.loads проходит, нужного ключа нет, и шаг молча
        # возвращал пустой результат («вставок: 0») вместо повтора.
        if ch.get("finish_reason") == "length":
            # своего потолка мы больше не ставим, так что это лимит САМОГО провайдера
            rt = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            raise ReelsiError(umsg("output_cut",
                                  f"провайдер оборвал ответ по своему лимиту вывода "
                                  f"({u.get('completion_tokens', '?')} токенов"
                                  + (f", из них {rt} на размышления" if rt else "") + "). "
                                  "Понизь уровень «ума» на этом шаге или возьми модель "
                                  "с большим выводом.",
                                  tokens=u.get("completion_tokens", "?")))
        ai_log_append(step, prof, ok=True,
                      in_t=u.get("prompt_tokens"), out_t=u.get("completion_tokens"),
                      rt=(u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                      finish=ch.get("finish_reason"),
                      ms=(time.time() - (_t0 or time.time())) * 1000)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(_extract_json_obj(raw))
            except json.JSONDecodeError as e:
                last_err = e
                lvl_prev = lvl
                attempt += 1
                continue
        # Схема могла не примениться (фолбэк use_rf=False) — проверяем, что пришёл
        # объект с обязательными полями, иначе это обрывок/пример, а не результат.
        need = [k for k in (schema.get("required") or []) if not isinstance(data, dict) or k not in data]
        if need:
            last_err = "в ответе нет обязательных полей: " + ", ".join(need)
            lvl_prev = lvl
            attempt += 1
            continue
        return data
    raise ReelsiError(umsg("bad_json",
                          f"ИИ вернул битый JSON после {retries + 1} попыток — модель залипла, "
                          f"запусти шаг ещё раз (или смени модель в настройках ⚙)",
                          tries=retries + 1))

def _anthropic_supports_thinking(mid):
    """Умеет ли Claude настраиваемый extended thinking (reasoning).

    Включаем у Claude 3.7 Sonnet и Claude 4 Sonnet/Opus (thinking задаётся бюджетом
    токенов). У Sonnet 5 / Opus 4.8 / Haiku 4.5 thinking адаптивный — не-дефолтные
    значения они отклоняют, поэтому их НЕ трогаем (вернём False)."""
    m = (mid or "").lower()
    if "thinking" in m:
        return True
    if "claude-3-7-sonnet" in m or "claude-3.7-sonnet" in m:
        return True
    # Реальные id Claude 4: claude-sonnet-4[-0][-дата], claude-opus-4[-0|-1][-дата]
    # (НЕ «claude-4-sonnet»). Важно НЕ зацепить 4.5/4.8 (claude-sonnet-4-5,
    # claude-opus-4-8, haiku-4-5) — у них thinking адаптивный, бюджет отклоняют.
    if re.search(r"claude-sonnet-4(-0)?(-\d{6,8})?$", m):
        return True
    if re.search(r"claude-opus-4(-[01])?(-\d{6,8})?$", m):
        return True
    return False


def _ask_anthropic(prof, system, user, schema, max_tokens=4096, emit=console_emit, retries=1,
                   step=None):
    """Claude API (см. _ask_openai: та же обёртка с логом вызовов в ai_calls.jsonl)."""
    t0 = time.time()
    ai_log_append(step, prof, ok=None)
    try:
        return _ask_anthropic_impl(prof, system, user, schema, max_tokens=max_tokens,
                                   emit=emit, retries=retries, step=step, _t0=t0)
    except (ReelsiError, SystemExit) as e:
        ai_log_append(step, prof, ok=False, ms=(time.time() - t0) * 1000, err=str(e))
        raise


def _ask_anthropic_impl(prof, system, user, schema, max_tokens=4096, emit=console_emit,
                        retries=1, step=None, _t0=None):
    """Anthropic Claude API (официальный SDK): structured outputs через
    output_config.format — валидный JSON по схеме гарантирован. Для моделей с
    настраиваемым extended thinking (Claude 3.7 Sonnet / Claude 4 Sonnet/Opus) уровень
    reasoning из профиля задаёт бюджет токенов размышлений; у прочих (Sonnet 5 / Opus 4.8,
    thinking адаптивный) thinking не трогаем."""
    try:
        import anthropic
    except ImportError:
        raise ReelsiError(umsg("anthropic_missing", "для провайдера Anthropic нужен пакет: pip install anthropic"))
    if not prof.get("api_key"):
        raise ReelsiError(umsg("anthropic_no_key", "не задан API-ключ Anthropic — открой настройки ⚙"))
    base = (prof.get("base_url") or "").rstrip("/")
    client_kwargs = dict(api_key=prof["api_key"],
                         base_url=base or "https://api.anthropic.com")
    user_headers = apply_profile_headers({}, prof)
    if user_headers:
        client_kwargs["default_headers"] = user_headers
    client = anthropic.Anthropic(**client_kwargs)
    model = prof["model"]
    # бюджет токенов размышлений по уровню reasoning; None = thinking не включаем
    lvl = prof.get("reasoning")
    thinking_budget = None
    if lvl in ("low", "medium", "high") and _anthropic_supports_thinking(model):
        thinking_budget = {"low": 2000, "medium": 8000, "high": 16000}[lvl]

    def _call(thinking):
        mt = max_tokens
        if thinking and thinking >= mt:
            mt = thinking + 4000          # max_tokens СТРОГО > budget_tokens
        kwargs = dict(model=model, max_tokens=mt, system=system,
                      messages=[{"role": "user", "content": user}],
                      output_config={"format": {"type": "json_schema", "schema": schema}})
        if thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking}
            kwargs["temperature"] = 1.0    # Anthropic требует temperature=1 при thinking
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    last_err = None
    tried_without_thinking = False
    for attempt in range(retries + 1):
        if cancelled():
            raise ReelsiError(cancel_reason())
        if attempt:
            emit("! битый JSON ({err}) — повторяю ({attempt}/{retries})…",
                 err=last_err, attempt=attempt, retries=retries)
        cur = thinking_budget if not tried_without_thinking else None
        try:
            resp = _call(cur)
        except anthropic.AuthenticationError:
            raise ReelsiError(umsg("key_rejected", "API-ключ Anthropic не принят (401) — проверь ключ в настройках ⚙", code=401, name="Anthropic"))
        except anthropic.PermissionDeniedError as e:
            raise ReelsiError(umsg("forbidden", f"Anthropic отказал в доступе (403): {getattr(e, 'message', e)}", err=getattr(e, "message", e)))
        except anthropic.RateLimitError:
            raise ReelsiError(umsg("rate_limit", "лимит запросов Anthropic (429) — подожди и повтори"))
        except anthropic.APIStatusError as e:
            msg = getattr(e, "message", "") or ""
            if cur and ("thinking" in msg.lower() or e.status_code == 400):
                tried_without_thinking = True
                emit("! модель не приняла extended thinking — повторяю без него")
                continue
            raise ReelsiError(umsg("anthropic_code", f"Anthropic API {e.status_code}: {msg}", status=e.status_code, msg=msg))
        except anthropic.APIConnectionError as e:
            raise ReelsiError(umsg("anthropic_no_connection", f"нет связи с Anthropic API: {e}", err=e))
        if resp.stop_reason == "refusal":
            raise ReelsiError(umsg("safety", "Anthropic отклонил запрос (safety) — попробуй другой профиль"))
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        u = resp.usage
        emit("  модель: {model}  токены: in={in_tok} out={out_tok}",
             model=model, in_tok=u.input_tokens, out_tok=u.output_tokens)
        # Обрезанный ответ парсить нельзя (см. _ask_openai): _extract_json_obj берёт
        # последний сбалансированный {...}, у оборванного массива объектов это
        # последний ЦЕЛЫЙ элемент — json.loads проходит, обязательного поля нет, и шаг
        # молча отдавал пустой результат. У OpenAI тут отказ, у Claude была строка в лог.
        if resp.stop_reason == "max_tokens":
            raise ReelsiError(umsg("output_cut",
                                  f"провайдер оборвал ответ по своему лимиту вывода "
                                  f"({u.output_tokens} токенов). "
                                  "Понизь уровень «ума» на этом шаге или возьми модель "
                                  "с большим выводом.",
                                  tokens=u.output_tokens))
        ai_log_append(step, prof, ok=True,
                      in_t=u.input_tokens, out_t=u.output_tokens,
                      finish=resp.stop_reason,
                      ms=(time.time() - (_t0 or time.time())) * 1000)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(_extract_json_obj(raw))
            except json.JSONDecodeError as e:
                last_err = e
                continue
        # Схема могла не примениться (провайдер игнорирует output_config) — проверяем
        # обязательные поля с повтором, ровно как на пути OpenAI.
        need = [k for k in (schema.get("required") or [])
                if not isinstance(data, dict) or k not in data]
        if need:
            last_err = "в ответе нет обязательных полей: " + ", ".join(need)
            continue
        return data
    raise ReelsiError(umsg("bad_json",
                          f"Claude вернул битый JSON после {retries + 1} попыток — "
                          f"запусти шаг ещё раз",
                          tries=retries + 1))
