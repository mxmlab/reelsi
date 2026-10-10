# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Команды разметки: жёлтые слова, вставки, интро, план нарезки.

Тут же вся арифметика вокруг них — свободные окна под интро, зоны вставок, привязка
к границам фразы. Это не обвязка вызова модели, а правила, которые применяются к её
ответу (и без которых модель ставит вставки поверх речи).
"""
from __future__ import annotations
import os, re, json, math, time
from typing import Any, Callable, Sequence, cast

from .config import reason_budget, step_profile, step_reasoning
from .llm import _ask_json
from .prompts import (INSERTS_SCHEMA, INSERTS_SYSTEM, INTRO_SCHEMA, INTRO_SYSTEM,
                      YELLOW_SCHEMA, YELLOW_SYSTEM)
from core.app_meta import console_emit, env
from core.fileio import atomic_json_dump
from core import paths
from core import styles as _styles
from core.applog import get_logger
from core.umsg import ReelsiError

log = get_logger(__name__)


def _words_from_xml(xml_path: str) -> list[tuple[int, str, float, float]]:
    from core import xml2ae
    meta, cams, subs, _ = xml2ae.parse_full(xml_path)
    fps = meta["fps"]
    return [(k, w, s / fps, e / fps) for k, (s, e, w) in enumerate(subs)]


def _word_lines(words: Sequence[tuple[int, str, float, float]]) -> str:
    return "\n".join(f"{k}\t{w}\t{s:.2f}-{e:.2f}" for k, w, s, e in words)


# Частицы-отрицания: взятое моделью слово без них переворачивает смысл
# («ПОМОГАЕТ» -> «НЕ ПОМОГАЕТ»). Своё содержимое, а НЕ срез INTRO_PREFIX_WORDS:
# предлоги («В», «НА», «ЗА»…) из интро сюда не входят намеренно — владелец
# дописывал их перед жёлтым руками 5 раз из 236, а частица «не» — 135.
YELLOW_NEGATION_PARTICLES = frozenset({"НЕ", "НИ"})

# Знаки препинания по краям слова: в ленте субтитров частица приезжает и как «не,»,
# и как «НЕ» в кавычках. Ё к Е — тот же приём нормализации, что у интро-префиксов.
_PARTICLE_EDGE_PUNCT = "«»\"'()[]{}.,!?;:—–-…"


def _norm_particle(word: str) -> str:
    """Слово для сравнения с частицей: без краевых знаков препинания, регистр не важен, ё=е.

    «не,» / «НЕ.» / «Ни!» обязаны попадать в YELLOW_NEGATION_PARTICLES наравне с
    голым «НЕ» — иначе частица, которую модель/Премьер приклеили к пунктуации,
    молча остаётся белой.
    """
    w = word.strip().upper().replace("Ё", "Е")
    return w.strip(_PARTICLE_EDGE_PUNCT)


def _yellow_fix_negation(idx: list[int], words: Sequence[tuple[int, str, float, float]]) -> list[int]:
    """Частица «не»/«ни» перед жёлтым словом тоже жёлтая (03.10.2026).

    Замер владельца по 236 клипам: жёлтое слово с белым «НЕ» перед ним — 135 случаев,
    «НИ» — 3. Модель выбирает смысловой пик («ПОМОГАЕТ»), а частица остаётся белой —
    жёлтое на экране утверждает обратное сказанному. Предлоги владелец перед жёлтым
    дописывал руками 5 раз — их НЕ добавляем (см. YELLOW_NEGATION_PARTICLES).
    Возвращает дополненный список (дубликаты не плодятся, слово 0 не трогается).
    """
    have = set(idx)
    add = []
    for i in idx:
        if i <= 0 or (i - 1) in have:                   # начало ленты / частица уже жёлтая
            continue
        prev = _norm_particle(words[i - 1][1])
        if prev in YELLOW_NEGATION_PARTICLES:
            add.append(i - 1)
    return sorted(have | set(add)) if add else idx


def as_ints(seq: Any, lo: int | None = None, hi: int | None = None) -> list[int]:
    """Список из ответа модели -> список int, мусор молча отбрасывается.

    Без structured outputs (фолбэк «схема в промпте» после 400) модель свободно
    возвращает `["12", "третье"]` или `[{"idx": 3}]`, и голый `int(x)` ронял шаг
    целиком. Здесь каждый элемент разбирается отдельно; lo/hi — границы диапазона.
    """
    out = []
    for x in (seq or []):
        try:
            v = int(x)
        except (TypeError, ValueError, OverflowError):
            continue
        if lo is not None and v < lo:
            continue
        if hi is not None and v >= hi:
            continue
        out.append(v)
    return out


def cmd_yellow(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit, style: Any = None) -> Any:
    words = _words_from_xml(xml_path)
    # Количество акцентов определяет смысл текста, а не длина ролика.
    user = (f"В ролике {len(words)} слов. Слова ролика (индекс, слово, тайминг в секундах):\n"
            + _word_lines(words))
    if dry:
        emit((system or YELLOW_SYSTEM) + "\n---\n" + user); return None
    # Ответ — список индексов; принимаем любое число валидных индексов.
    _t0 = time.time()
    _lvl = step_reasoning("yellow")
    data = _ask_json(system or YELLOW_SYSTEM, user, YELLOW_SCHEMA,
                     model=model, url=url, max_tokens=reason_budget(6000, _lvl),
                     emit=emit, reasoning=_lvl, profile=step_profile("yellow"),
                     step="yellow")
    emit("  ⏱ ИИ-жёлтые: LLM-вызов {sec:.1f}с", sec=time.time() - _t0)
    idx = sorted(set(as_ints(data.get("yellow"), lo=0, hi=len(words))))
    _n0 = len(idx)
    idx = _yellow_fix_negation(idx, words)
    if len(idx) > _n0:
        # частица встаёт вплотную к выбранному слову: соседние жёлтые без зазора
        # остаются ОДНОЙ группой (auto_highlights не режет их палочкой)
        emit("  частица «не» к жёлтым: +{n}", n=len(idx) - _n0)
    # красим ПРЯМО в XML (цвет едет с клипом, переживает ручной до-монтаж; парсер AE читает сам)
    from core import xml2ae
    res = xml2ae.write_highlights(xml_path, idx)
    colored = res["colored"]
    # сайдкар — фолбэк для слов, которые не удалось покрасить (слишком длинные и пр.)
    out = os.path.splitext(xml_path)[0] + ".yellow.json"
    # atomic_json_dump: open(...,"w") усекал сайдкар ДО сериализации — «Стоп» или
    # крах в этот момент оставлял пустой файл вместо набора жёлтых слов (GZ, п. A)
    atomic_json_dump(out, {"yellow": idx})
    emit("жёлтых: {colored} покрашено в XML из {idx} выбранных ({words} слов)",
         colored=len(colored), idx=len(idx), words=len(words))
    emit("  " + " ".join(words[i][1] for i in colored))
    if res["skipped"]:
        emit("  не покрашены (фолбэк на .yellow.json): {items}",
             items=", ".join(f"{w}[{r}]" for _, w, r in res["skipped"]))
    # Сила жёлтых (core/emphasis.py) — прямо за покраской: жёлтые только что определены, и
    # сайдкар `<стем>.emph.json` нужен правилу «наезд только на сильные жёлтые». Зовём
    # предрасчёт ОДНОЙ двери со сборкой и превью; сбой силы шаг жёлтых не роняет.
    # `style` — какой стиль уедет в сборку, если вызывающий его знает: по нему решается,
    # включён ли вообще наезд на жёлтых (не знает — считаем, вреда нет).
    try:
        from core import xml2ae
        xml2ae.precompute.emphasis_precompute(xml_path, style, idx=idx, emit=emit)
    except ReelsiError:
        raise
    except Exception as ex:
        emit("  ! сила жёлтых не посчитана: {err}", err=ex)
    return {"path": out, "yellow": idx, "colored": colored, "total": len(words)}


# Слова стиля, которые модель не должна класть в query: query — голый предмет,
# он идёт и в поиск по стокам/базе, и в генератор. Стилевой хвост «3d icon» код
# больше НЕ дописывает (убран по просьбе юзера 2026-07-21).
_STYLE_WORDS = {"3d", "2d", "icon", "icons", "render", "rendering", "rendered", "png",
                "transparent", "background", "realistic", "flat", "vector", "clipart",
                "illustration", "isolated", "hd", "texture", "style"}


def _strip_style_words(q: Any) -> str:
    out = [t for t in str(q).split() if t.strip(".,;:!?\"'()").lower() not in _STYLE_WORDS]
    return " ".join(out).strip()


def _finite_float(v: Any) -> float | None:
    """float(v), если это КОНЕЧНОЕ число; иначе None (NaN, ±Infinity, строка, None, список).

    `float()` пропускает `nan`/`inf`, а сравнения с ними всегда ложны: `nan or 2.5`
    даёт `nan`, `min`/`max` его не режут — и `nan` уезжает и в `.jsx` (там `_r(nan)`
    падает), и в JSON (`jsonify` пишет голый `NaN` — невалидный JSON для браузера)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _snap_to_phrase(ins: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], emit: Callable[..., Any] = console_emit) -> None:
    """Прижать start_sec каждой вставки к началу её цитируемой фразы. Промпт требует
    копировать тайминг из ленты, но модели ставят его «на глаз» (главная причина
    «вставка не на своём месте») — ищем фразу в пословной ленте (окно с максимальным
    пересечением слов, при равенстве — ближайшее к таймингу модели) и берём старт
    первого слова окна."""
    def toks(s: Any) -> list[str]:
        return re.findall(r"[a-zа-я0-9]+", (s or "").lower().replace("ё", "е"))
    line = [("".join(toks(w)), s) for _, w, s, _ in words]
    for it in ins:
        ph = toks(it.get("phrase") or "")
        if len(ph) < 2 or not line:
            continue
        s0 = float(it.get("start_sec", 0) or 0)
        pset, m = set(ph), len(ph)
        best = None                                    # (score, -|t-s0|, t)
        for i in range(len(line)):
            win = set(w for w, _ in line[i:i + m])
            sc = len(pset & win) / m
            if sc < 0.6:
                continue
            cand = (sc, -abs(line[i][1] - s0), line[i][1])
            if best is None or cand > best:
                best = cand
        if best is None:
            emit("  ! фраза не найдена в субтитрах, тайминг модели как есть: {sec:.1f}с «{phrase}»",
                 sec=s0, phrase=str(it.get('phrase'))[:45])
            continue
        t = best[2]
        if abs(t - s0) > 0.5:
            emit("  тайминг по фразе: {from_sec:.1f}с -> {to_sec:.1f}с  «{phrase}»",
                 from_sec=s0, to_sec=t, phrase=str(it.get('phrase'))[:45])
        it["start_sec"] = round(t, 2)



INS_ZONE_PHOTO = 6.0     # фото не раньше 6-й секунды (начало ролика держит только речь)
INS_ZONE_VIDEO = 10.0    # видео — не раньше 10-й
INS_MIN_GAP = 2.5        # минимальный зазор между стартами соседних вставок
INS_MIN_DUR = 1.0        # короче — мигание в кадре, смысла нет
# Дефолтная цель автоподбора — 13: десять фото и три видео. Личное число
# для спикера задаётся в его профиле (поле inserts) и возвращается ins_quota.
INS_TARGET = 13
INS_PHOTO = 10
INS_VIDEO = 3
# Старые имена экспортируются фасадом пакета; оставляем их как совместимые алиасы.
# Совместимость со старыми потребителями; расчёт цели больше его не использует.
INS_SEC_PER = 5.4
INS_MIN = INS_TARGET
INS_MAX = INS_TARGET


def ins_target(dur: float | None) -> int:
    return INS_TARGET


def _quota_val(v: Any, default: int) -> int:
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 30:
        return v
    return default


def _speaker_profile(speaker: Any = None) -> dict[str, Any] | None:
    """Профиль спикера словарём: сам dict, профиль по ключу/label или None.

    Одна дверь для всего, что читает настройки спикера в подборе вставок (квота и
    выключатель вставок по названиям): иначе «квоту прочитали одним способом, выключатель другим»
    разъезжается ровно на битом профиле.
    """
    if not speaker:
        return None
    if isinstance(speaker, dict):
        return speaker
    if not isinstance(speaker, str):
        return None
    try:
        from core import speakers
        prof = speakers.load(speaker)
    except ReelsiError: raise
    except Exception:
        # битый профиль не роняет подбор: дефолт 10+3 лучше, чем упавший шаг 2
        return None
    return prof if isinstance(prof, dict) else None


def ins_quota(speaker: Any = None) -> tuple[int, int]:
    """Квота вставок (фото, видео) для спикера: из профиля или дефолт (10, 3).

    speaker — ключ, label или dict (профиль целиком или словарь inserts).
    """
    prof = _speaker_profile(speaker)
    if not prof:
        return INS_PHOTO, INS_VIDEO

    raw_ins = prof.get("inserts") if "inserts" in prof else prof
    if not isinstance(raw_ins, dict):
        return INS_PHOTO, INS_VIDEO

    ph = _quota_val(raw_ins.get("photo"), INS_PHOTO) if "photo" in raw_ins else INS_PHOTO
    vid = _quota_val(raw_ins.get("video"), INS_VIDEO) if "video" in raw_ins else INS_VIDEO

    if ph + vid < 1:
        return INS_PHOTO, INS_VIDEO
    return ph, vid


def named_inserts_on(speaker: Any = None) -> bool:
    """Включены ли вставки по названиям у спикера: поле named_inserts профиля, дефолт ВКЛ.

    Выключатель живёт в профиле спикера («Вставки по названиям»), и профиль, как у квоты,
    берётся у ТОГО ЖЕ спикера: клип спрашивает свой. Поля нет — включено: профиль без
    правок не должен менять поведение, а мусор в поле (строка, число) читается как
    «не выключали». Профили, сохранённые до переименования, держат старое поле
    drug_inserts — читаем его, если нового нет.
    """
    prof = _speaker_profile(speaker)
    if not prof:
        return True
    for key in ("named_inserts", "drug_inserts"):
        val = prof.get(key)
        if isinstance(val, bool):
            return val
    return True



# Зона конца — как зона начала: числом, а не словами. Доля 6% длины в интервале
# [4, 9] с. До BX «самый конец (призыв подписаться)» каждая модель толковала по-своему,
# и на длинных роликах зона растягивалась на 30 с пустоты.
INS_END_ZONE = (0.06, 4.0, 9.0)


def ins_end_sec(dur: float | None) -> float:
    frac, lo, hi = INS_END_ZONE
    return max(lo, min(hi, frac * (dur or 0)))


def _end_zone_word(words: Sequence[tuple[int, str, float, float]], dur: float) -> int | None:
    """Индекс первого слова запретной зоны конца: старт попадает после границы
    `dur - ins_end_sec(dur)`. Граница числом, а не «самый конец» — иначе модель сама
    решает, сколько это, и на длинных роликах тянет зону на полминуты."""
    end_sec = ins_end_sec(dur)
    if not end_sec or not words:
        return None
    return next((k for k, _, s, _ in words if s >= dur - end_sec), None)


def _apply_zones(ins: list[dict[str, Any]], dur: float, occupied: Sequence[float] = (), emit: Callable[..., Any] = console_emit) -> list[dict[str, Any]]:
    """Отсев вставок, попавших в запретные зоны (начало ролика, впритык к соседу,
    хвост ролика). Вставка УДАЛЯЕТСЯ, а не сдвигается — и это главное.

    Раньше здесь стоял `s = max(s0, 6.0)` плюс каскад `s = last + 2.5`: вставка про
    фразу со 2-й секунды переезжала на 6-ю и иллюстрировала уже ДРУГОЙ текст, а каскад
    тащил за собой соседей — весь «поезд» вставок начала ролика вставал не по смыслу.
    После `_snap_to_phrase` тайминг привязан к цитате, двигать его нельзя вообще.
    Единственное, что подгоняем, — длительность у самого конца ролика (старт не трогаем).
    """
    kept, last = [], -1e9
    for it in sorted(ins, key=lambda x: float(x.get("start_sec", 0) or 0)):
        s = float(it.get("start_sec", 0) or 0)
        typ = it.get("type") or "photo"
        q = str(it.get("query", ""))[:45]
        zone = INS_ZONE_VIDEO if typ == "video" else INS_ZONE_PHOTO
        if s < zone:
            emit("  запретная зона: убрал {type} на {sec:.1f}с (раньше {zone:.0f}с) «{query}»",
                 type=typ, sec=s, zone=zone, query=q)
            continue
        if s - last < INS_MIN_GAP:
            emit("  впритык к соседней ({gap:.1f}с): убрал вставку на {sec:.1f}с «{query}»",
                 gap=s - last, sec=s, query=q)
            continue
        if any(abs(s - o) < INS_MIN_GAP for o in occupied):
            emit("  впритык к уже выбранной: убрал вставку на {sec:.1f}с «{query}»",
                 sec=s, query=q)
            continue
        if dur:
            tail = dur - s
            if tail < INS_MIN_DUR:
                emit("  конец ролика: убрал вставку на {sec:.1f}с «{query}»",
                     sec=s, query=q)
                continue
            if tail < float(it.get("duration_sec") or 0):
                it["duration_sec"] = round(tail, 2)
        kept.append(it)
        last = s
    return kept


def _spread_quota(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """Обрезка ОДНОГО типа до квоты: начало, середина и конец покрытия сохраняются,
    лишнее убирается равномерно по хронометражу."""
    if len(items) <= quota:
        return items
    if quota <= 0:
        return []
    if quota == 1:
        return [items[len(items) // 2]]
    # Сохраняем начало и конец покрытия, а избыток выбираем равномерно.
    n = len(items) - 1
    return [items[round(i * n / (quota - 1))] for i in range(quota)]


def _cap_by_quota(ins: list[dict[str, Any]], photo_quota: int, video_quota: int) -> list[dict[str, Any]]:
    """Детерминированная обрезка типов до квот ЭТОГО вызова.

    Модель может прислать больше нужного типа — лишнее отбирается равномерно по
    хронометражу с сохранением начала, середины и конца. Удалённый избыток не добирается;
    результат никогда не превышает заданные квоты (для полного набора — 10 фото и 3 видео).
    """
    # Неизвестный тип сохраняем как фото для обратной совместимости.
    photos = sorted((x for x in ins if x.get("type") != "video"),
                    key=lambda x: float(x.get("start_sec", 0) or 0))
    videos = sorted((x for x in ins if x.get("type") == "video"),
                    key=lambda x: float(x.get("start_sec", 0) or 0))

    return sorted(_spread_quota(photos, photo_quota) + _spread_quota(videos, video_quota),
                  key=lambda x: float(x.get("start_sec", 0) or 0))


def _start_sec(x: dict[str, Any]) -> float:
    """Старт вставки числом (для сортировок и сравнений)."""
    return float(x.get("start_sec", 0) or 0)


def _weakest_ai_index(ai: list[dict[str, Any]], others: Sequence[dict[str, Any]]) -> int:
    """Индекс самой слабой ИИ-фото-вставки в `ai` (соседей считаем и по `others`).

    «Силы» вставки модель не размечает, поэтому слабость — плотность: у какой ближайший
    сосед ближе всех, тот момент и так уже покрыт, и потерять её дешевле всего. При равном
    зазоре уходит ПОЗДНЯЯ, затем — правая в списке (детерминированно, без случайности).
    """
    pool = list(ai) + list(others)
    best_i, best_key = 0, None
    for i, it in enumerate(ai):
        t = _start_sec(it)
        gap = min((abs(t - _start_sec(o)) for j, o in enumerate(pool) if j != i), default=1e9)
        key = (gap, -t, -i)
        if best_key is None or key < best_key:
            best_i, best_key = i, key
    return best_i


def _is_named(x: dict[str, Any]) -> bool:
    """Вставка по названию (auto='named'). 'drug' — метка тех же вставок, сохранённых до переименования."""
    return x.get("auto") in ("named", "drug")


def _cap_with_named(ins: list[dict[str, Any]], photo_quota: int, video_quota: int) -> list[dict[str, Any]]:
    """Финальная обрезка типов, где вставки по названиям в приоритете.

    Вставки по названиям входят в ТУ ЖЕ квоту фото, но не вытесняются ею: если фото стало
    больше квоты, первыми уходят самые слабые ИИ-вставки (см. _weakest_ai_index) — вставка
    поставлена по названному в речи слову, и её место в ролике не выдумано. Их больше самой
    квоты — режем равномерно, как обычные фото (_spread_quota): квота всё равно жёсткая.
    Видео режется как раньше.
    """
    photos = sorted((x for x in ins if x.get("type") != "video"), key=_start_sec)
    named = [x for x in photos if _is_named(x)]
    ai = [x for x in photos if not _is_named(x)]
    while len(ai) + len(named) > photo_quota and ai:
        ai.pop(_weakest_ai_index(ai, named))
    if len(named) > photo_quota:
        named = _spread_quota(named, photo_quota)
    videos = sorted((x for x in ins if x.get("type") == "video"), key=_start_sec)
    return sorted(ai + named + _spread_quota(videos, video_quota), key=_start_sec)


def _sim_words(q1: Any, q2: Any) -> float:
    """Похожесть двух query по словам: пересечение / объединение (0, если пусто)."""
    a = set(re.findall(r"[а-яёa-z]+", (q1 or "").lower()))
    b = set(re.findall(r"[а-яёa-z]+", (q2 or "").lower()))
    return (len(a & b) / len(a | b)) if a and b else 0.0


def _rejected_hit(rejected: Sequence[dict[str, Any]], it: dict[str, Any]) -> dict[str, Any] | None:
    """Первая удалённая юзером правка, на которую похожа вставка: тот же тип (видео/фото),
    старт в ±2 с и пересечение слов query ≥ 60 %. Промпт мягкий, код жёсткий."""
    t = _start_sec(it)
    return next((r for r in rejected
                 if (r.get("type") == "video") == (it.get("type") == "video")
                 and abs(_start_sec(r) - t) <= 2.0
                 and _sim_words(r.get("query"), it.get("query")) >= 0.6), None)


# ---------- вставки по названиям ----------
# Личный словарь `named_inserts.json` (лежит в корне репозитория, в гит не попадает; пример
# с нейтральными словами — data/named_inserts.example.json). Владелец ставит вставку там,
# где в речи назван предмет, руками: картинка в базе есть, а подбор «по смыслу фразы» такие
# вставки делает вскользь или не делает вовсе. Поэтому тут ДЕТЕРМИНИРОВАННЫЙ проход по
# словам ролика ПОСЛЕ ответа модели: слово из словаря -> картинка из базы вставок ->
# фото-вставка на момент слова. Словаря нет — проход молча выключен: ни ошибки, ни строки
# в логе на каждый ролик.
NAMED_INSERTS_PATH = env("NAMED_INSERTS") or paths.root("named_inserts.json")
NAMED_GAP_SEC = 4.0       # ближе этого к уже стоящей вставке название не встаёт
NAMED_SAME_SEC = 20.0     # одно название — не чаще раза в это окно (повторы не плодят вставок)
NAMED_STEM_MIN = 3        # короче основы название в словаре не берём вовсе
NAMED_PHOTO_DUR = 2.5     # длительность фото-вставки по умолчанию (та же, что у ИИ-фото)

_NAMED_CACHE: dict[str, Any] = {"mtime": None, "cfg": None}


def _str_list(v: Any) -> list[str]:
    """Список строк из JSON: не список — пусто, мусорные элементы — мимо."""
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def _named_config() -> dict[str, Any]:
    """Личный словарь названий, разобранный: aliases, secondary, prefer, avoid.

    Формат — в data/named_inserts.example.json. Каноническое имя -> формы названия в речи
    и в базе; `_prefer` — слова картинки, без которых она не годится; `_avoid` — сюжетные
    слова, которые портят картинку (штраф); `secondary` — вторичные формы (эфиры и т.п.),
    картинку по ним ищут только тогда, когда по основным названиям совпадений нет.
    Ключи с «_» — служебные, именем предмета они не бывают. Читается по mtime. Файла нет,
    JSON битый или запись кривая — пустой словарь, молча: шаг вставок из-за словаря не
    падает, названий просто не будет.
    """
    empty: dict[str, Any] = {"aliases": {}, "secondary": {}, "prefer": [], "avoid": []}
    try:
        mtime: int = os.stat(NAMED_INSERTS_PATH).st_mtime_ns
    except OSError:
        return empty
    if _NAMED_CACHE.get("mtime") == mtime and isinstance(_NAMED_CACHE.get("cfg"), dict):
        return cast(dict[str, Any], _NAMED_CACHE["cfg"])
    raw: Any = None
    try:
        with open(NAMED_INSERTS_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = None
    cfg: dict[str, Any] = empty
    if isinstance(raw, dict):
        aliases: dict[str, list[str]] = {}
        for name, forms in raw.items():
            if not isinstance(name, str) or name.startswith("_") or not name.strip():
                continue
            if isinstance(forms, list):
                aliases[name.strip()] = _str_list(forms)
        sec: dict[str, list[str]] = {}
        s_raw = raw.get("secondary")
        if isinstance(s_raw, dict):
            for name, forms in s_raw.items():
                if isinstance(name, str) and name.strip() and isinstance(forms, list):
                    sec[name.strip()] = _str_list(forms)
        cfg = {"aliases": aliases, "secondary": sec,
               "prefer": _str_list(raw.get("_prefer")), "avoid": _str_list(raw.get("_avoid"))}
    _NAMED_CACHE["mtime"], _NAMED_CACHE["cfg"] = mtime, cfg
    return cfg


def _named_index(cfg: dict[str, Any]) -> dict[tuple[str, ...], str]:
    """Индекс словаря: последовательность ОСНОВ названия -> каноническое имя.

    Ключ — кортеж основ, поэтому одна таблица ловит и односложные названия, и формы из
    нескольких слов («гормон роста»): поиск идёт окном по ленте слов ролика. Форма с основой
    короче NAMED_STEM_MIN в ключ не попадает — короткие куски речи слишком общие.
    """
    from core import insertlib
    idx: dict[tuple[str, ...], str] = {}
    secondary: dict[str, list[str]] = cfg["secondary"]
    for name, forms in cfg["aliases"].items():
        # вторичные формы (эфиры и т.п.) тоже называют предмет в речи; разница — в find_named
        for form in [name, *forms, *secondary.get(name, [])]:
            stems = tuple(insertlib.word_stem(t) for t in insertlib.stem_tokens(form))
            if not stems or any(len(s) < NAMED_STEM_MIN for s in stems) or all(s.isdigit() for s in stems):
                continue
            idx.setdefault(stems, name)
    return idx


def _named_mentions(words: Sequence[tuple[int, str, float, float]],
                    index: dict[tuple[str, ...], str]) -> list[tuple[float, str, str]]:
    """Упоминания названий в словах ролика: (старт слова, каноническое имя, как сказано).

    Слова ролика выкладываются в ОДНУ ленту основ, и по ней скользит окно: форма из
    нескольких слов собирается из соседних слов, односложная ловится на одном слове.
    Название сравнивается по ОСНОВЕ (insertlib.word_stem), поэтому падежные формы —
    одно и то же слово, а «тренер» — не «трен» (чужой корень не склеивается).
    """
    from core import insertlib
    flat: list[tuple[str, int]] = []                 # (основа, позиция слова в ленте)
    for pos, (_k, w, _s, _e) in enumerate(words):
        for tok in insertlib.stem_tokens(w):
            flat.append((insertlib.word_stem(tok), pos))
    out: list[tuple[float, str, str]] = []
    for n in sorted({len(k) for k in index}):
        for i in range(len(flat) - n + 1):
            name = index.get(tuple(flat[i + j][0] for j in range(n)))
            if not name:
                continue
            p0, p1 = flat[i][1], flat[i + n - 1][1]
            out.append((float(words[p0][2]), name,
                        " ".join(words[q][1] for q in range(p0, p1 + 1))))
    out.sort(key=lambda m: m[0])
    return out


def _named_inserts(ins: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]],
                   dur: float, speaker: Any = None,
                   rejected: Sequence[dict[str, Any]] | None = None,
                   emit: Callable[..., Any] = console_emit
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    """Детерминированный проход по словарю: название в речи -> фото-вставка с картинкой.

    Возвращает (вставки, строки лога «по названиям: …»). Вставка встаёт ровно на слово-
    упоминание, с уже заполненным `media` (фронт её больше не ищет) и пометкой
    `auto='named'` — по ней видно, откуда вставка, и по ней же она не вытесняется квотой.
    Правила: картинка обязана быть в базе вставок (иначе вставки нет, а в лог идёт
    «картинки нет» — факт про базу); ближе NAMED_GAP_SEC к уже стоящей вставке не встаём —
    показанный момент не дублируем; одно название не чаще раза в NAMED_SAME_SEC; запретные
    зоны начала/конца и память удалённых правок — как у обычных фото-вставок.
    Выключатель — поле named_inserts профиля спикера (дефолт ВКЛ).
    """
    if not named_inserts_on(speaker):
        return list(ins), []
    cfg = _named_config()
    index = _named_index(cfg)
    if not index:
        return list(ins), []
    from core import insertlib
    aliases: dict[str, list[str]] = cfg["aliases"]
    secondary: dict[str, list[str]] = cfg["secondary"]
    fresh: list[dict[str, Any]] = []
    note_by_id: dict[int, tuple[float, str]] = {}
    notes: list[tuple[float, str]] = []
    taken = [_start_sec(x) for x in ins]
    placed: dict[str, float] = {}
    no_pic: set[str] = set()
    for t, name, said in _named_mentions(words, index):
        if any(abs(t - s) < NAMED_GAP_SEC for s in taken):
            continue
        last = placed.get(name)
        if last is not None and t - last < NAMED_SAME_SEC:
            continue
        hit = insertlib.find_named([name, *aliases.get(name, [])], secondary=secondary.get(name, []),
                                   prefer=cfg["prefer"], avoid=cfg["avoid"])
        if not hit:
            if name not in no_pic:
                no_pic.add(name)
                notes.append((t, f"{name} — картинки нет"))
            continue
        path = str(hit["path"])
        it: dict[str, Any] = {"type": "photo", "start_sec": round(t, 2),
                              "duration_sec": NAMED_PHOTO_DUR, "phrase": said,
                              "query": name, "media": path, "auto": "named"}
        if rejected and _rejected_hit(rejected, it):
            continue                    # юзер удалил такую вставку — не возвращаем её заново
        note_by_id[id(it)] = (t, f"{name} {t:.1f} с (картинка {os.path.basename(path)})")
        placed[name] = t
        taken.append(t)
        fresh.append(it)
    if not fresh:
        return list(ins), [text for _t, text in sorted(notes)]
    # Запретные зоны, мин. зазор и хвост ролика — тем же кодом, что у ИИ-вставок: правило
    # одно на всех. Отсеянное название не попадает и в лог (вставки нет — и строки нет).
    merged = _apply_zones(list(ins) + fresh, dur, (), emit=emit)
    for x in merged:
        note = note_by_id.get(id(x))
        if note:
            notes.append(note)
    return merged, [text for _t, text in sorted(notes)]


def cmd_inserts(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit,
                count: int | None = None, avoid: list[dict[str, Any]] | None = None, rejected: list[dict[str, Any]] | None = None, window: tuple[float, float] | None = None,
                speaker: Any = None) -> Any:
    """count/avoid — «добор недостающих»: сгенерить РОВНО count НОВЫХ вставок для других
    мест, не повторяя avoid (список уже выбранных: {type,start_sec,query}). При частичном
    доборе сайдкар .inserts.json НЕ перезаписываем (он держит полный набор).
    window=(t0,t1) — добор ТОЛЬКО в указанный промежуток секунд: та же проверка
    покрытия, что у «добор выброшенного», но для пустого хвоста ролика.
    rejected — память правок: предложения, которые юзер УДАЛЯЛ раньше
    ({type,start_sec,query}) — модель просим не повторять, похожие фильтруем кодом."""
    words = _words_from_xml(xml_path)
    dur = words[-1][3] if words else 0
    ph_total, vid_total = ins_quota(speaker)
    target = ph_total + vid_total
    # Квота основного вызова постоянна; рекурсивный добор получает только недостающие типы.
    if count:
        keep_photo = sum(1 for a in (avoid or []) if a.get("type") != "video")
        keep_video = sum(1 for a in (avoid or []) if a.get("type") == "video")
        photo_quota = max(0, ph_total - keep_photo)
        video_quota = max(0, vid_total - keep_video)
    else:
        photo_quota, video_quota = ph_total, vid_total
    user = (f"Длина ролика ~{dur:.0f} секунд. Слова ролика (индекс, слово, тайминг в секундах):\n"
            + _word_lines(words))
    # В доборе общий target не упоминается: модель получает только дефициты типов.
    if count:
        user += (f"\n\nНУЖНО РОВНО {photo_quota} новых фото (type=photo) и {video_quota} новых "
                 f"видео (type=video) — для ДРУГИХ моментов ролика. В ЭТАПЕ 1 опиши "
                 f"{photo_quota + video_quota} сильных моментов.")
    else:
        user += (f"\n\nНУЖНО РОВНО {target} вставок: {photo_quota} фото (type=photo) и "
                 f"{video_quota} видео (type=video). В ЭТАПЕ 1 опиши {target}–{target + 3} "
                 f"сильных моментов — покрывай ими ВЕСЬ хронометраж равномерно, от первой "
                 f"разрешённой фразы до последней.")
    # Граница запретной зоны — конкретным индексом слова: «раньше 6 сек» словами модель
    # прикидывает на глаз и всё равно цитирует первую фразу ролика.
    ok = next((k for k, _, s, _ in words if s >= INS_ZONE_PHOTO), None)
    if ok:
        user += (f"\n\nЗАПРЕТНАЯ ЗОНА НАЧАЛА: слова 0–{ok - 1} (до {INS_ZONE_PHOTO:.0f} с) не "
                 f"иллюстрируются вообще — фразы оттуда не бери и не переноси их идеи дальше. "
                 f"Первая разрешённая фраза начинается со слова {ok} «{words[ok][1]}» "
                 f"({words[ok][2]:.2f}с); для видео — с {INS_ZONE_VIDEO:.0f}-й секунды.")
    zend = _end_zone_word(words, dur)
    if zend is not None:
        # Граница числом, как у начала: «самый конец» словами модель растягивала на 30 с.
        user += (f"\n\nЗАПРЕТНАЯ ЗОНА КОНЦА: слова {zend}–{len(words) - 1} "
                 f"(после {dur - ins_end_sec(dur):.1f} с) — призыв подписаться, "
                 f"не иллюстрируется. Последняя разрешённая фраза кончается словом "
                 f"{zend - 1} «{words[zend - 1][1]}» ({words[zend - 1][3]:.2f}с).")
    if window:
        t0, t1 = window
        user += (f"\n\nНУЖНЫ вставки ТОЛЬКО для промежутка {t0:.1f}–{t1:.1f} секунд ролика. "
                 f"Бери фразы, которые ЗВУЧАТ внутри этого промежутка.")
    if count:
        av: Any = "\n".join(f"- {a.get('type','photo')} на ~{float(a.get('start_sec',0)):.0f}с: {a.get('query','')}"
                       for a in (avoid or [])) or "—"
        user += (f"\n\nНЕ повторяй уже выбранные (их темы и тайминги):\n{av}\n"
                 f"Возьми другие места и другие идеи картинок.")
    if rejected:
        rj = "\n".join(f"- {r.get('type','photo')} на ~{float(r.get('start_sec',0)):.0f}с: {r.get('query','')}"
                       for r in rejected[:20])
        user += ("\n\nЮЗЕР РАНЕЕ УДАЛИЛ эти предложения (не понравились) — "
                 f"НЕ предлагай похожие темы в тех же местах:\n{rj}")
    if dry:
        emit((system or INSERTS_SYSTEM) + "\n---\n" + user); return None
    _t0 = time.time()
    _lvl = step_reasoning("inserts")
    data = _ask_json(system or INSERTS_SYSTEM, user, INSERTS_SCHEMA,
                     model=model, url=url, max_tokens=reason_budget(10000, _lvl),
                     emit=emit, temperature=0.8, reasoning=_lvl, profile=step_profile("inserts"),
                     step="inserts")
    emit("  ⏱ ИИ-вставки: LLM-вызов {sec:.1f}с", sec=time.time() - _t0)
    if data.get("analysis"):
        emit("анализ модели:\n{analysis}", analysis=str(data["analysis"]).strip())
    ins = data.get("inserts", [])
    # Ответ модели — данные, а не гарантия: на фолбэке «схема в промпте» (провайдер
    # ответил 400 на structured outputs) в списке бывают строки и null, и первый же
    # it.get(...) ронял шаг AttributeError — уже после оплаченного вызова (GZ, п. G).
    ins = [it for it in ins if isinstance(it, dict)]
    # Числа из ответа модели — такие же данные, а не гарантия, как и типы: `NaN` и
    # `Infinity` проходят и `float()`, и сортировку ниже. Не-числовой start_sec сбрасываем
    # в None до привязки: _snap_to_phrase получает шанс привязать вставку к цитате.
    # Вставку, чей старт и после привязки не стал конечным числом, отбрасываем.
    # Нечисловая длительность — 0, дальше штатный кламп.
    for it in ins:
        if _finite_float(it.get("start_sec")) is None:
            it["start_sec"] = None
        if _finite_float(it.get("duration_sec")) is None:
            it["duration_sec"] = 0
    # 1) тайминг: прижать start_sec к началу цитируемой фразы (модель врёт «на глаз»)
    _snap_to_phrase(ins, words, emit=emit)
    dated = []
    for it in ins:
        if _finite_float(it.get("start_sec")) is None:
            emit("  ! вставка отброшена: start_sec не число ({raw})",
                 raw=repr(it.get("start_sec"))[:40])
            continue
        dated.append(it)
    ins = dated
    # 2) query фото: голый предмет — чистим стилевые слова, если модель их всё же дописала
    for it in ins:
        if it.get("type") != "video":
            it["query"] = _strip_style_words(it.get("query") or "")
        it.pop("tag", None)                             # хвост старых сайдкаров/ответов
    # Однословный query («cup», «keys») ищет и генерит что попало — по нему не понять,
    # ЧТО за предмет задумывался. Промпт этого требует, но слабые модели всё равно
    # обрубают (DeepSeek, 2026-08-04). Молча не чиним (перевести prompt нечем) — но
    # показываем в логе рядом с русской подписью, чтобы было видно, что править.
    _short = [it for it in ins if it.get("type") != "video"
              and len(str(it.get("query") or "").split()) < 2]
    for it in _short:
        emit("  ⚠ куцый query «{query}» (~{sec:.0f}с) — по-русски задумано: «{prompt}»",
             query=it.get('query'), sec=float(it.get('start_sec', 0)),
             prompt=str(it.get('prompt') or '—')[:60])
    ins.sort(key=lambda x: float(x.get("start_sec", 0)))
    # 3) длительность — из ответа модели, границы схемой не заданы: 0 или 40с уехали бы
    # в .jsx как есть (в AE это либо мигание в кадр, либо вставка на пол-ролика).
    # Считается ДО зон: по ней там режется хвост у последней вставки.
    for it in ins:
        try:
            d = float(it.get("duration_sec") or 0)
        except (TypeError, ValueError):
            d = 0.0
        it["duration_sec"] = round(min(max(d or 2.5, 1.0), 6.0), 2)
    # 4) запретные зоны — жёстко в коде: мелкие модели игнорируют их в промпте.
    # Попавшие в зону вставки УДАЛЯЮТСЯ (см. _apply_zones), недостачу добираем ниже.
    occupied = sorted(float(a.get("start_sec", 0) or 0) for a in (avoid or []))
    ins = _apply_zones(ins, dur, occupied, emit=emit)
    # Память правок: отсев сгенерированного, слишком похожего на удалённое юзером
    # (тот же тип, старт в ±2с, пересечение слов query >= 60%). Промпт мягкий, код жёсткий.
    # ВАЖНО — сравнивать можно только ЗДЕСЬ, после _snap_to_phrase и раздвижки по
    # запретным зонам: фронт кладёт в ins_rejected уже СКОРРЕКТИРОВАННЫЙ start_sec.
    # Раньше отсев шёл по сырому таймингу модели: удалённая вставка со снапом на 12.0с
    # против сырых 14.5с давала Δ=2.5 > 2.0 — фильтр промахивался, и она возвращалась.
    if rejected:
        kept_ins = []
        for it in ins:
            if _rejected_hit(rejected, it):
                emit("  память правок: убрал повтор удалённого — ~{sec:.0f}с «{query}»",
                     sec=float(it.get('start_sec', 0)), query=str(it.get('query', ''))[:40])
            else:
                kept_ins.append(it)
        ins = kept_ins
    # 5) квота типов — детерминированно: что модель переложила сверх квоты этого вызова,
    # срезаем по времени. Дальше считаем недостачу и добираем только недостающие типы.
    ins = _cap_by_quota(ins, photo_quota, video_quota)
    # 6) добор недостающего. Отсев зонами/квотой честнее сдвига, но юзер нажал «подобрать
    # заново» и ждёт полный набор из профиля спикера (по умолчанию 10 фото + 3 видео), а не
    # «13 минус то, что модель поставила не туда». Просим модель ровно недостающее число КАЖДОГО типа для ДРУГИХ
    # мест (avoid = оставшиеся). Рекурсия ровно на один уровень: у вложенного вызова
    # count уже задан, и квота там считается из avoid.
    if not count:
        n_photo = sum(1 for x in ins if x.get("type") != "video")
        n_video = sum(1 for x in ins if x.get("type") == "video")
        need = max(0, ph_total - n_photo) + max(0, vid_total - n_video)
    else:
        need = 0
    if need > 0 and not count:
        n_video = sum(1 for x in ins if x.get("type") == "video")
        refill_log = ("  добор {}: в наборе {} фото и {} видео, не хватает {}"
                      .format(need, len(ins) - n_video, n_video, need))
        emit(refill_log)
        av = [{"type": x.get("type"), "start_sec": x.get("start_sec"),
               "query": x.get("query") or ""} for x in ins]
        try:
            extra = (cmd_inserts(xml_path, system=system, model=model, url=url, emit=emit,
                                 count=need, avoid=av, rejected=rejected,
                                 speaker=speaker) or {}).get("inserts") or []
        except ReelsiError: raise
        except Exception as e:                       # добор не критичен: отдаём что есть
            emit("  ! добор не удался: {err_type}: {err}", err_type=type(e).__name__, err=e)
            extra = []
        ins = sorted(ins + extra[:need], key=lambda x: float(x.get("start_sec", 0) or 0))
    # 7) пустой хвост — та же проверка покрытия, но по времени, а не по отсеву: модель
    # может принести полный набор, сбив вставки в середину. Если от последней вставки до
    # запретной зоны конца дыра больше двух шагов — добираем ровно в этот промежуток.
    if not count and ins and dur:
        n_photo = sum(1 for x in ins if x.get("type") != "video")
        n_video = sum(1 for x in ins if x.get("type") == "video")
        need2 = max(0, ph_total - n_photo) + max(0, vid_total - n_video)
        end_sec = ins_end_sec(dur)
        tail_at = dur - end_sec
        step = dur / target
        last = float(ins[-1].get("start_sec") or 0)
        gap = tail_at - last
        if gap > 2 * step and need2 > 0:
            emit("  между {from_sec:.1f}с и {to_sec:.1f}с вставок нет — добираю",
                 from_sec=last, to_sec=tail_at)
            av2 = [{"type": x.get("type"), "start_sec": x.get("start_sec"),
                    "query": x.get("query") or ""} for x in ins]
            try:
                extra2 = (cmd_inserts(xml_path, system=system, model=model, url=url,
                                      emit=emit, count=need2, avoid=av2, rejected=rejected,
                                      window=(last, tail_at),
                                      speaker=speaker) or {}).get("inserts") or []
            except ReelsiError: raise
            except Exception as e:
                emit("  ! добор окна не удался: {err_type}: {err}", err_type=type(e).__name__, err=e)
                extra2 = []
            ins = sorted(ins + extra2[:need2], key=lambda x: float(x.get("start_sec", 0) or 0))
    # 8) по названиям: слово из личного словаря получает фото-вставку с его картинкой из
    # базы — детерминированно и ПОСЛЕ ответа модели (она такие вставки делает вскользь).
    # Только в полном наборе: в доборе (count) вставки уже посчитаны этим же вызовом, и
    # второй проход продублировал бы их.
    if not count:
        ins, named_notes = _named_inserts(ins, words, dur, speaker, rejected=rejected, emit=emit)
        if named_notes:
            emit("по названиям: {list}", list=", ".join(named_notes))
    # Финальная страховка полного набора: не больше общей и типовых квот. Вставки по названиям
    # в эту квоту входят, но вытесняют ИИ-вставки (см. _cap_with_named).
    ins = _cap_with_named(ins, ph_total, vid_total)
    out = os.path.splitext(xml_path)[0] + ".inserts.json"
    if not count:                                   # полный набор -> обновляем сайдкар; добор -> нет
        atomic_json_dump(out, {"inserts": ins}, indent=1)
    nv = sum(1 for x in ins if x.get("type") == "video")
    emit("вставок: {total} ({photo} фото + {video} видео) -> {file}",
         total=len(ins), photo=len(ins) - nv, video=nv, file=os.path.basename(out))
    return {"path": out, "inserts": ins, "ins_target": target}



def _busy_windows(inserts: list[dict[str, Any]] | None) -> list[tuple[float, float]]:
    """Секунды, занятые вставками (фото/видео): [(start, end), …], по возрастанию."""
    return sorted((float(i.get("start_sec") or 0),
                   float(i.get("start_sec") or 0) + float(i.get("duration_sec") or 2.5))
                  for i in (inserts or []) if i.get("start_sec") is not None)


# Оценка длины интро для КАРТЫ РОЛИКА: сколько слов уйдёт в интро, точно известно только
# после ответа модели, а карту надо собрать до вызова. По ручному эталону интро — 9–21
# слово, берём середину: свободные окна начинаем считать после этого слова.
INTRO_EST_WORDS = 14
# Окно короче — акцент туда не влезает (группа из 1–3 слов звучит ~1–2 с).
INTRO_FREE_MIN = 2.5


def _free_windows(words: Sequence[tuple[int, str, float, float]], inserts: list[dict[str, Any]] | None, after: float = 0.0, min_len: float = INTRO_FREE_MIN) -> list[tuple[float, float]]:
    """Куски хронометража, НЕ занятые вставками (и не попавшие в интро): [(a, b), …].

    Порог 2.5 с, а не 8: при 13 вставках на 100-секундный ролик окон длиннее 8 с почти
    не остаётся, и карта говорила модели «ставить негде». Разбор ручной разметки (9
    клипов, 148 акцентов) показал обратное — акценты стоят и в коротких промежутках."""
    dur = words[-1][3] if words else 0
    free, t = [], after
    for a, b in _busy_windows(inserts):
        if a - t >= min_len:
            free.append((t, a))
        t = max(t, b)
    if dur - t >= min_len:
        free.append((t, dur))
    return free


def _free_quota(a: float, b: float, per: float | None = None) -> int:
    """Сколько акцентов просить в свободное окно: один на INTRO_MID_PER_SEC секунд,
    но не меньше одного — пустое окно и есть то место, ради которого всё затевается."""
    return max(1, int(round((b - a) / (per or INTRO_MID_PER_SEC))))


def _intro_free_hint(words: Sequence[tuple[int, str, float, float]], free: list[tuple[float, float]]) -> str:
    """КАРТА РОЛИКА для модели: свободные окна с КВОТОЙ на каждое, занятые вставками
    секунды и паузы в речи. Квота по окнам, а не общее число на ролик: задача — занять
    текстом за спиной именно те места, где вставок нет, а не набрать счётчик где угодно."""
    out = []
    if free:
        out.append("СВОБОДНО от вставок — СЮДА И СТАВЬ АКЦЕНТЫ (окно → сколько нужно):\n"
                   + "\n".join(f"  {a:.0f}–{b:.0f}с → {_free_quota(a, b)}" for a, b in free))
    busy = [w for w in _busy_windows_from_free(words, free)]
    if busy:
        out.append("ЗАНЯТО вставками, сюда не целься (сек): "
                   + ", ".join(f"{a:.0f}–{b:.0f}" for a, b in busy))
    pauses = [(words[k][3], words[k + 1][2]) for k in range(len(words) - 1)
              if words[k + 1][2] - words[k][3] >= 1.0]
    if pauses:
        out.append("Паузы в речи — там кадр особенно пустой (сек): "
                   + ", ".join(f"{a:.0f}–{b:.0f}" for a, b in pauses[:20]))
    return "\n".join(out)


def _busy_windows_from_free(words: Sequence[tuple[int, str, float, float]], free: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Инверсия свободных окон — чтобы в карте «занято» и «свободно» не разъезжались."""
    dur = words[-1][3] if words else 0
    out, t = [], 0.0
    for a, b in free:
        if a - t > 0.05:
            out.append((t, a))
        t = b
    if dur - t > 0.05:
        out.append((t, dur))
    return out


INTRO_ROW_MAX_CHARS = 9       # длиннее — строка не влезает по ширине, рвём по словам
# Лимит переноса ДЛЯ ХУКА — ручка стиля «длина строки интро» (ключ intro_row_max, core/
# styles.py, дефолт 20). Замер владельца по 48 роликам: его строки хука доходят до ~20
# символов, когда фраза — одно смысловое целое («ни в коем случае»); прежние 14 рвали их
# по буквам. Константа — дефолт для вызовов без стиля; cmd_intro берёт число из стиля клипа.
INTRO_HOOK_ROW_MAX_CHARS = int(_styles.BASE["intro_row_max"])
# Порог СКЛЕЙКИ служебного слова с первым словом следующей строки (_intro_defunc). Своя
# константа, а не ручка стиля: замер владельца (52 ролика) — при 20 склейка тянет лишнее
# («ЕСЛИ ВЫ» -> «ЕСЛИ ВЫ НА», «ПОТОМУ ЧТО» -> «ПОТОМУ ЧТО НАМ»), совпадение падает
# с 79 % до 73 %; при 14 потерь нет.
INTRO_DEFUNC_MAX_CHARS = 14

# Служебные слова: предлоги, союзы, частицы, местоимения — верхним регистром, как слова
# приходят из XML. ОДИН список на весь модуль: им же считается доля строк хука,
# кончающихся служебным словом (tools/intro_hook_check.py). Нужен, потому что строка не
# должна кончаться предлогом или союзом, оторванным от своего слова: ИИ после BF стал
# рвать фразу где попало («БОЛЬШИНСТВО НА / КУРСЕ», хвост «СТАВЯТ ПО»).
INTRO_FUNC_WORDS = frozenset({
    # предлоги
    "БЕЗ", "БЛАГОДАРЯ", "В", "ВДОЛЬ", "ВМЕСТО", "ВНУТРИ", "ВО", "ВОКРУГ", "ВРОДЕ",
    "ДЛЯ", "ДО", "ЗА", "ИЗ", "ИЗ-ЗА", "ИЗ-ПОД", "К", "КО", "КРОМЕ", "МЕЖДУ", "МИМО",
    "НА", "НАД", "НАПРОТИВ", "О", "ОБ", "ОКОЛО", "ОТ", "ПЕРЕД", "ПО", "ПОД", "ПОМИМО",
    "ПОСЛЕ", "ПРИ", "ПРО", "С", "СО", "СРЕДИ", "У", "ЧЕРЕЗ",
    # союзы
    "А", "БУДТО", "ДА", "ЕСЛИ", "ЖЕ", "И", "ИЛИ", "КАК", "КОГДА", "ЛИ", "НО", "ПОКА",
    "ПОТОМУ", "РАЗ", "СЛОВНО", "ТАК", "ТО", "ТОЖЕ", "ТАКЖЕ", "ХОТЯ", "ЧЕМ", "ЧТО",
    "ЧТОБЫ",
    # частицы
    "БЫ", "ВОН", "ВОТ", "ВРЯД", "ДАЖЕ", "ЕЩЁ", "ЕЩЕ", "ИМЕННО", "ЛИШЬ", "НЕ", "НИ",
    "УЖ",
    # местоимения
    "ВСЕ", "ВСЁ", "ВСЕХ", "ВСЯ", "ВЫ", "ЕГО", "ЕЁ", "ЕЕ", "ЕЙ", "ЕМУ", "ИМ", "ИХ",
    "КТО", "МЕНЯ", "МНЕ", "МЫ", "НАМ", "НАС", "НИКТО", "НИЧТО", "ОН", "ОНА", "ОНИ",
    "ОНО", "САМ", "САМА", "САМИ", "СЕБЕ", "СЕБЯ", "ТА", "ТАМ", "ТАКОЙ", "ТЕ", "ТЕБЕ",
    "ТЕБЯ", "ТО", "ТОТ", "ТУТ", "ТЫ", "ЭТА", "ЭТИ", "ЭТО", "ЭТОТ", "Я",
})


def _split_words(ws: Sequence[str], limit: int = INTRO_ROW_MAX_CHARS) -> list[list[str]]:
    """Перенос по словам: список слов, который длиннее limit символов, режем на две
    части по границе слов (и так рекурсивно). Возвращает список кусков (списков слов).
    Одно слово не режем — переносим целиком.

    Цена разреза (2026-10-09): разрез сразу ПОСЛЕ слова из INTRO_PREFIX_WORDS («не»,
    предлог, указательное) запрещён, как и кусок, который остаётся из одного такого
    слова: «НЕ» обязано стоять в одной строке со СЛЕДУЮЩИМ словом. После прочего
    служебного слова (INTRO_FUNC_WORDS) разрез штрафуется и берётся только если
    без штрафа нельзя. Среди равных — ближайший к середине. Если допустимых разрезов
    нет — строку не режем: строка длиннее limit лучше отрыва. Сравнение — через upper()."""
    out, stack = [], [list(ws)]
    while stack:
        cur = stack.pop(0)
        if len(cur) < 2 or len(" ".join(cur)) <= limit:
            out.append(cur)
            continue
        half = len(" ".join(cur)) / 2.0            # режем там, где половина символов
        best, acc, bestkey = None, 0, None
        for j in range(len(cur) - 1):
            acc += len(cur[j]) + 1
            up = cur[j].upper()
            if up in INTRO_PREFIX_WORDS:           # «не»/предлог остался бы без своего слова
                continue
            if j == len(cur) - 2 and cur[-1].upper() in INTRO_PREFIX_WORDS:
                continue                           # правый кусок стал бы одним таким словом
            penalty = 1 if up in INTRO_FUNC_WORDS else 0
            key = (penalty, abs(acc - half))
            if bestkey is None or key < bestkey:
                best, bestkey = j + 1, key
        if best is None:                           # допустимого разреза нет — строку не режем
            out.append(cur)
            continue
        stack = [cur[:best], cur[best:]] + stack
    return out


def _wrap_intro_rows(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], limit: int = INTRO_HOOK_ROW_MAX_CHARS) -> list[dict[str, Any]]:
    """Перенос длинных строк интро: каждую строку режем по словам на куски ≤ limit.
    limit — ручка стиля intro_row_max (см. INTRO_HOOK_ROW_MAX_CHARS); строки короче
    ручки не трогаются. Первый кусок наследует break строки, продолжения получают
    break=False — тот же контракт, что у акцентов в _place_mids."""
    out, k = [], 0
    for r in rows:
        n = r["count"]
        ws = [w[1] for w in words[k:k + n]]
        k += n
        for ci, chunk in enumerate(_split_words(ws, limit)):
            item = {"count": len(chunk), "color": r["color"],
                    "break": (bool(r.get("break")) if ci == 0 else False)}
            if "back" in r:
                item["back"] = bool(r["back"])
            out.append(item)
    return out


# Страховка разбиения хука на прекомпы, если модель разбиение не прислала (локальные
# модели без structured outputs так и делают) или нарезала переросшие прекомпы. Правила
# — числа ручного эталона (38 .jsx, поправка 2026-08-14): прекомп 2–3 строки, 3–5 слов,
# хук 3–5 прекомпов; пауза границу не объясняет, а только подсказывает.
INTRO_HOOK_ROWS = 3       # в текущем прекомпе уже 3 строки — следующая начинает новый
INTRO_HOOK_WORDS = 5      # в текущем прекомпе уже 5 слов — следующая строка уже за ним
INTRO_HOOK_PAUSE = 0.4    # ≥2 строк и пауза ≥ этой перед строкой — смысловая граница


def _hook_split(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], start: int) -> list[dict[str, Any]]:
    """Разложить ленту строк хука на прекомпы по правилам эталона, начиная со слова
    start (не 0, когда режем переросший прекомп модели). Первая строка ленты — голова."""
    out, off = cast(list[dict[str, Any]], []), start
    cur_rows, cur_words = 0, 0
    for r in rows:
        n = max(1, r.get("count") or 1)
        pause = (words[off][2] - words[off - 1][3]
                 if 0 < off < len(words) else 0.0)
        new = (cur_rows >= INTRO_HOOK_ROWS
               or (cur_rows >= 2 and pause >= INTRO_HOOK_PAUSE)
               or cur_words >= INTRO_HOOK_WORDS)
        if new:
            cur_rows, cur_words = 0, 0
        item = {"count": n, "color": r["color"],
                "break": new or not out}
        if "back" in r:
            item["back"] = bool(r["back"])
        out.append(item)
        cur_rows += 1
        cur_words += n
        off += n
    return out


def _hook_trim(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], start: int) -> list[dict[str, Any]]:
    """Дорезка прекомпа модели, переросшего 4 строки или 6 слов (p90 эталона).
    Мелкие прекомпы не трогаем — её разбиение не переигрываем."""
    n_words = sum(max(1, r.get("count") or 1) for r in rows)
    if len(rows) <= 4 and n_words <= 6:
        return rows
    return _hook_split(rows, words, start)


def _intro_defunc(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]]) -> list[dict[str, Any]]:
    """Служебное слово не остаётся последним в строке (2026-08-14).

    Модель поняла «до 14 символов» как цель и рвёт фразу где попало: «СТАВЯТ / ПО»,
    «ДЛЯ / ПРОФЕССИОНАЛЬНЫХ». _split_words такие строки не чинит — они короче лимита
    и в перенос не попадают. Здесь: строка, кончающаяся служебным словом, тащит
    ПЕРВОЕ слово следующей строки назад (пока не упрётся в потолок INTRO_DEFUNC_MAX_CHARS
    или не перестанет кончаться служебным словом). Пустые строки выкидываются; границы
    прекомпов модель ставит ненадёжно (прекомп на «ЖЕ», «ПО»), поэтому разбиение
    доверяется _hook_breaks, который вызывается следом.

    Порог склейки НЕ зависит от ручки стиля (см. INTRO_DEFUNC_MAX_CHARS).
    Из ЦВЕТНОЙ строки слово не тянем: жёлтая/accent строка — смысловой пик, и перенос её
    первого слова наверх снимает цвет всей строки (опустевшая строка удаляется). Отрыв
    «не»/предлога перед цветной строкой закрывает _intro_fix_prefix."""
    out, k = [], 0
    for r in rows:
        n = max(1, r.get("count") or 1)
        item = {"w": [w[1] for w in words[k:k + n]],
                "color": r["color"], "break": r.get("break", False)}
        if "back" in r:
            item["back"] = bool(r["back"])
        out.append(item)
        k += n
    changed = True
    while changed:
        changed = False
        for i in range(len(out) - 1):
            cur, nxt = out[i], out[i + 1]
            if not cur["w"] or not nxt["w"]:
                continue
            if cur["w"][-1] not in INTRO_FUNC_WORDS:
                continue
            if nxt.get("color") in ("yellow", "accent"):
                continue                       # цветной строке слово не отрываем
            if len(" ".join(cur["w"] + [nxt["w"][0]])) > INTRO_DEFUNC_MAX_CHARS:
                continue
            cur["w"].append(nxt["w"][0])
            nxt["w"] = nxt["w"][1:]
            if not nxt["w"]:
                del out[i + 1]
            changed = True
            break
    res = []
    for r in out:
        item = {"count": len(r["w"]), "color": r["color"],
                "break": bool(r["break"])}
        if "back" in r:
            item["back"] = bool(r["back"])
        res.append(item)
    return res


# Слова, которые не должны отрываться от следующего слова: «не», «ни», предлоги,
# указательные/определительные. «НЕ»/«НИ» переезжают в начало СЛЕДУЮЩЕЙ строки всегда, прочие —
# только если следующая строка цветная или back (правило владельца 2026-10-09, _intro_fix_prefix).
INTRO_PREFIX_WORDS = frozenset({
    "НЕ", "НИ",
    # предлоги (закрытый список из задания)
    "В", "ВО", "НА", "О", "ОБ", "БЕЗ", "ДЛЯ", "ДО", "ИЗ", "К", "КО", "ПО", "ПОД",
    "ПРИ", "ПРО", "С", "СО", "У", "ЗА", "ОТ", "НАД", "ПЕРЕД", "ЧЕРЕЗ",
    # указательные/определительные
    "ЭТИ", "ЭТОТ", "ЭТА", "ЭТО", "КАЖДОГО", "КАЖДЫЙ", "КАЖДАЯ", "КАЖДОЕ",
})


def _intro_fix_prefix(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]]) -> list[dict[str, Any]]:
    """«Не»/предлог/указательное не отрываются от слова (02.10.2026).

    Строка кончается «НЕ» или «НИ» — слово переезжает в начало СЛЕДУЮЩЕЙ строки при любом
    её цвете («ТАК НЕ / ДЕЛАТЬ» -> «ТАК / НЕ ДЕЛАТЬ»). Прочие слова INTRO_PREFIX_WORDS
    (предлоги, указательные) переезжают только в цветную (yellow/accent) или back-строку:
    перед белой «ЕСЛИ ВЫ НА / КУРСЕ» владелец оставляет как есть. Строка из ОДНОГО
    служебного слова склеивается со следующей. Пустые строки удаляются. Для хука
    (intro_rows) — по count; для mid_groups это делает _place_mids (по полю from).
    """
    # Развернуть rows в список слов
    out, k = [], 0
    for r in rows:
        n = max(1, r.get("count") or 1)
        item = {"w": [w[1] for w in words[k:k + n]],
                "color": r["color"], "break": r.get("break", False)}
        if "back" in r:
            item["back"] = bool(r["back"])
        out.append(item)
        k += n
    # Проход: если цветная/back строка начинается со слова, а перед ним в предыдущей
    # строке стоит предлог/«не» — перетащить в начало цветной строки
    changed = True
    while changed:
        changed = False
        for i in range(1, len(out)):
            cur = out[i]
            prev = out[i - 1]
            if not prev["w"] or not cur["w"]:
                continue
            last = prev["w"][-1].upper()
            if last not in INTRO_PREFIX_WORDS:
                continue
            # «НЕ»/«НИ» — всегда, при любом цвете следующей строки; предлоги и указательные —
            # только перед цветной/back-строкой (перед белой «ЕСЛИ ВЫ НА / КУРСЕ» не трогаем)
            if last not in ("НЕ", "НИ") and not (
                    cur.get("color") in ("yellow", "accent") or cur.get("back")):
                continue
            # Перетащить последнее слово из предыдущей строки
            cur["w"].insert(0, prev["w"].pop())
            if not prev["w"]:
                del out[i - 1]
            changed = True
            break
    # Строка из одного предлога — склеить со следующей
    changed = True
    while changed:
        changed = False
        for i in range(len(out) - 1):
            cur = out[i]
            if len(cur["w"]) == 1 and cur["w"][0].upper() in INTRO_PREFIX_WORDS:
                nxt = out[i + 1]
                nxt["w"] = cur["w"] + nxt["w"]
                # Если текущая была head прекомпа, следующая наследует break
                if cur.get("break"):
                    nxt["break"] = True
                del out[i]
                changed = True
                break
    # Собрать обратно
    res = []
    for r in out:
        if not r["w"]:
            continue
        item = {"count": len(r["w"]), "color": r["color"],
                "break": bool(r["break"])}
        if "back" in r:
            item["back"] = bool(r["back"])
        res.append(item)
    if res:
        res[0]["break"] = True             # первая строка — голова хука по определению
    return res


def _hook_breaks(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]]) -> list[dict[str, Any]]:
    """Механическая страховка разбиения хука на прекомпы.

    Модель разбиение может не прислать вовсе — тогда хук собирался ОДНИМ прекомпом.
    Здесь: если модель не прислала ни одного break — расставляем целиком по правилам
    эталона; если прислала — её разбиение не переигрываем, режем только прекомпы
    крупнее 4 строк или 6 слов (p90 эталона)."""
    breaks = [i for i, r in enumerate(rows) if r.get("break")]
    if len(breaks) <= 1:                               # только голова хука (по определению)
        return _hook_split(rows, words, 0)
    out, group, gstart, off = cast(list[dict[str, Any]], []), cast(list[dict[str, Any]], []), 0, 0
    for r in rows:
        if r.get("break") and group:
            out += _hook_trim(group, words, gstart)
            group = []
        if not group:
            gstart = off                               # первое слово нового прекомпа
        group.append(r)
        off += max(1, r.get("count") or 1)
    if group:
        out += _hook_trim(group, words, gstart)
    return out


# Сколько секунд после конца предыдущего акцента место считается занятым. Раньше здесь
# стоял глухой порог «не ближе 6 секунд ПО СТАРТУ», и он выбрасывал 40% разметки: сверка
# с ручным эталоном (9 роликов, 148 акцентов в собранных .jsx) дала медианный разрыв
# 4.2 с, p10 = 1.2 с, минимум 0.4 с. Порог 6 с оставлял 53% эталона, правило «не наезжать
# на конец предыдущей группы + 0.4 с» — 93%. Смысл страховки в том, чтобы два прекомпа не
# висели одновременно (кросс-фейд превращается в кашу), а не в том, чтобы задавать ритм.
INTRO_MID_GAP = 0.4
# Целевая плотность акцентов: один на INTRO_MID_PER_SEC секунд СВОБОДНОГО (не занятого
# вставками) времени. Считается именно по свободным окнам, а не по длине ролика: смысл
# текста за спиной — занять места, где вставок нет. В ручном эталоне 132 акцента из 148
# стоят в свободных окнах общей длиной 507 с — это один на 3.8 с; делитель 4 даёт цель
# 131 против фактических 132.
INTRO_MID_PER_SEC = 4.0
# Свободное окно длиннее этого осталось без акцента -> пишем в лог поимённо.
INTRO_EMPTY_WARN = 6.0
# Потолок интро. Модель может вернуть count=400 на ролике из 180 слов: перенос строк
# тогда пропускался (sum > len(words)), ВСЕ акценты отсекались фильтром `f < intro_len`,
# и в .jsx уезжало интро, забирающее весь ролик в текст за спиной — без единой ошибки.
# 24, а не 20: в ручном эталоне интро доходит до 21 слова.
INTRO_MAX_WORDS = 24


def _place_mids(groups: list[tuple[Any, ...]], words: Sequence[tuple[int, str, float, float]], intro_len: int, busy: list[tuple[float, float]], emit: Callable[..., Any] = console_emit) -> list[dict[str, Any]]:
    """Отбор и раскладка акцентов-групп: [(from, count, color, back)] -> строки INTRO.

    Выбрасываем группу, если она лезет в интро/за край, перекрыта вставкой (там кадр
    занят, текста за спиной не видно) или наезжает на предыдущий акцент. Всё, что
    выброшено, пишем в лог: молчаливый `continue` скрывал главную потерю разметки."""
    mids, last_end = [], -1e9
    pending = []
    for item in groups:
        f, c, color = item[0], item[1], item[2]
        has_back = len(item) > 3
        back = bool(item[3]) if has_back else False
        if f < intro_len or f + c > len(words):        # не лезем в интро и за край
            continue
        # «не»/предлог/указательное не отрываются от своего слова — ДО вставок и разреза
        # (2026-10-09). Правило одно для всех цветов (решение владельца 2026-10-09).
        # Начало: перед группой слова из INTRO_PREFIX_WORDS переезжают в неё, цепочкой
        # («НИ В КОЕМ СЛУЧАЕ» — группа «КОЕМ СЛУЧАЕ» начинается с «НИ»). Конец: группа,
        # кончающаяся таким словом, добирает следующее; если добирать нечего (конец
        # ролика) — служебное слово снимается с конца.
        while f - 1 >= intro_len and words[f - 1][1].upper() in INTRO_PREFIX_WORDS:
            f, c = f - 1, c + 1
        while c > 0 and f + c < len(words) and words[f + c - 1][1].upper() in INTRO_PREFIX_WORDS:
            c += 1                                 # добрать следующее слово
        while c > 0 and words[f + c - 1][1].upper() in INTRO_PREFIX_WORDS:
            c -= 1                                 # ролик кончился: снять служебное с конца
        if c == 0:
            continue
        pending.append((f, c, color, back, has_back))
    # порядок — уже по скорректированным началам: сдвиг «не» назад мог поставить группу раньше
    for f, c, color, back, has_back in sorted(pending, key=lambda x: x[0]):
        t, t_end = words[f][2], words[f + c - 1][3]
        if any(a < t_end and t < b for a, b in busy):  # акцент только ТАМ, ГДЕ ВСТАВОК НЕТ
            emit("  акцент с «{word}» ({sec:.0f}с) отброшен: перекрыт вставкой",
                 word=words[f][1], sec=t)
            continue
        if t < last_end + INTRO_MID_GAP:               # предыдущая группа ещё висит в кадре
            emit("  акцент с «{word}» ({sec:.0f}с) отброшен: наезжает на предыдущий",
                 word=words[f][1], sec=t)
            continue
        last_end = t_end
        # тот же перенос по словам, что и у интро: длинный акцент — стопка строк в одном
        # прекомпе. break=True только у головной строки (несёт `from`); продолжения
        # (break=False, from=None) добирают слова подряд и встают в тот же прекомп.
        for ci, chunk in enumerate(_split_words([w[1] for w in words[f:f + c]])):
            row = {"from": (f if ci == 0 else None), "count": len(chunk),
                   "color": color, "break": (ci == 0)}
            if has_back:
                row["back"] = back
            mids.append(row)
    return mids


# Слово-призыв («напишите мне слово «консультация»…») — то, что зритель должен унести с
# конца ролика. Промпт просит ставить на него последний акцент, но модель этого не
# гарантирует: призыв мог оказаться перекрыт или уступить хвост акцентов. Поэтому гарантия в коде.
INTRO_CALL_TAIL = 0.15        # хвост ролика, где ищем призыв: доля слов…
INTRO_CALL_TAIL_MIN = 30      # …но не меньше стольких слов
INTRO_CALL_MAX_WORDS = 3      # кавычки длиннее — цитата, а не призыв: не трогаем
# Открывающая кавычка -> допустимые закрывающие. ASCII-кавычка закрывается сама собой.
_QUOTE_OPEN: dict[str, tuple[str, ...]] = {"«": ("»",), "„": ("“", "”"), "“": ("”",), '"': ('"',)}
_QUOTE_TRIM = '«»„“”"' + ".,!?:; "
# Метка в INTRO_SYSTEM вместо числа длины строки хука: cmd_intro подставляет ручку стиля
# (intro_row_max). Не str.format — в промпте есть фигурные скобки JSON.
INTRO_ROW_MAX_TOKEN = "<ROW_MAX>"


def _quote_spans(words: Sequence[tuple[int, str, float, float]]) -> list[tuple[int, int]]:
    """Пары кавычек в ленте: [(a, b), …] — индекс слова с открывающей кавычкой и индекс
    слова с закрывающей. Кавычки могут стоять на соседних словах или внутри одного."""
    out: list[tuple[int, int]] = []
    start = 0
    closers: tuple[str, ...] = ()
    for i, w in enumerate(words):
        for ch in str(w[1]):
            if closers:
                if ch in closers:
                    out.append((start, i))
                    closers = ()
            elif ch in _QUOTE_OPEN:
                start, closers = i, _QUOTE_OPEN[ch]
    return out


def _call_span(words: Sequence[tuple[int, str, float, float]]) -> tuple[int, int] | None:
    """Слово-призыв: (a, b) — первое и последнее слово ПОСЛЕДНЕЙ пары кавычек, если она
    кончается в хвосте ролика (последние 15 % слов, не меньше 30) и не длиннее трёх слов.
    Иначе None: кавычек в хвосте нет — решает промпт."""
    n = len(words)
    if not n:
        return None
    tail = min(n, max(INTRO_CALL_TAIL_MIN, math.ceil(n * INTRO_CALL_TAIL)))
    lo = n - tail
    spans = [s for s in _quote_spans(words) if s[1] >= lo]
    if not spans:
        return None
    a, b = spans[-1]
    if a < lo or b - a + 1 > INTRO_CALL_MAX_WORDS:
        return None
    return a, b


def _mid_groups(mids: Sequence[dict[str, Any]]) -> list[tuple[int, int, int, int]]:
    """Акценты посреди ролика по группам: [(i0, i1, f, count), …] — строки mids[i0:i1]
    одной группы (голова с break и её продолжения), f — первое слово, count — слов в группе."""
    out: list[list[int]] = []
    for i, r in enumerate(mids):
        n = int(r.get("count") or 0)
        if r.get("break") or not out:
            f = r.get("from")
            out.append([i, i + 1, 0 if f is None else int(f), n])
        else:
            out[-1][1] = i + 1
            out[-1][3] += n
    return [(g[0], g[1], g[2], g[3]) for g in out]


def _place_call_word(mids: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], intro_len: int,
                     busy: Sequence[tuple[float, float]],
                     emit: Callable[..., Any] = console_emit) -> list[dict[str, Any]]:
    """Слово-призыв — последний акцент ролика. mids — строки после _place_mids.

    Призыв в интро или под вставкой не трогаем, пишем в лог. Иначе: если акцент уже
    покрывает призыв целиком — все акценты после него удаляем. Если нет — удаляем акценты,
    которые кончаются позже призыва минус зазор INTRO_MID_GAP (то есть идут после него или
    налезают на его зазор), и ставим группу из слов в кавычках: жёлтую, без заднего плана.
    Оформление (anim/fx) ставится позже тем же кодом, что у прочих акцентов."""
    span = _call_span(words)
    if span is None:
        return mids
    a, b = span
    word = " ".join(str(w[1]).strip(_QUOTE_TRIM) for w in words[a:b + 1]).strip()
    t0, t1 = words[a][2], words[b][3]
    if a < intro_len:
        emit("  призыв «{word}» в интро — акцент не ставлю", word=word)
        return mids
    if any(s < t1 and t0 < e for s, e in busy):
        emit("  призыв «{word}» перекрыт вставкой", word=word)
        return mids
    groups = _mid_groups(mids)
    cover = [g for g in groups if g[2] <= a and g[2] + g[3] - 1 >= b]
    if cover:
        emit("  призыв: «{word}» — последний акцент", word=word)
        return mids[:cover[-1][1]]
    keep = 0                                   # группы идут по времени: с первой задетой — хвост
    for i0, i1, f, cnt in groups:
        if words[min(f + cnt - 1, len(words) - 1)][3] > t0 - INTRO_MID_GAP:
            break
        keep = i1
    rows = []
    for ci, chunk in enumerate(_split_words([str(w[1]) for w in words[a:b + 1]])):
        rows.append({"from": (a if ci == 0 else None), "count": len(chunk),
                     "color": "yellow", "break": ci == 0, "back": False})
    emit("  призыв: «{word}» — последний акцент", word=word)
    return mids[:keep] + rows


def _intro_look(color: Any, back: Any, nwords: int,
                 group_pos: int = 0, group_size: int = 1) -> tuple[str, str]:
    """Вывод оформления (anim, fx) по смыслу строки (цвет, задний план, длина).

    Единственный источник правды об оформлении строк интро и акцентов.
    Пересчитано по ручной разметке владельца (02.10.2026, 400 строк; было 228 = 57 %):
    - accent → glitch, ЛЮБОЙ длины (108 из 109 accent-строк; раньше 3+ слова уходили
      в reveal — у владельца этого нет);
    - back → up при 2+ словах (10 из 16), при одном слове — без анимации (reveal,
      как было, владелец back-строкам не ставит);
    - группа из 4+ строк (white/yellow) → первая reveal, остальные right, каскад
      (15 из 17 групп);
    - белая строка 2–3 слова → up (вне каскада); белая 1 слово → без анимации
      (57 из 69);
    - жёлтая 1 слово → без анимации (105 из 124);
    - иначе — без анимации.
    Правило пары «1+1 → left/right» УДАЛЕНО: у владельца так 3 пары из 43, остальные
    40 пар без анимации. Свечения (fx) разметка не ставит никому — его даёт стиль,
    ключ intro_accent_glow. Итог замера — совпадение 327 из 400 строк (82 %).

    group_pos — позиция строки в группе (0-based), group_size — число строк в группе.
    Без них группа считается из одной строки: правило каскада «4+» не срабатывает.
    """
    if color == "accent":
        return ("glitch", "")
    if back:
        return ("up" if nwords >= 2 else "", "")
    if group_size >= 4 and color in ("white", "yellow"):
        return ("reveal" if group_pos == 0 else "right", "")
    if color == "white" and 2 <= nwords <= 3:
        return ("up", "")
    return ("", "")


def _intro_row_max(style: Any = None) -> int:
    """Ручка стиля «длина строки интро» (intro_row_max) для переноса длинных строк хука.

    style — имя пресета, dict стиля или None (тогда BASE). Читается тем же resolve, что и
    сборка; битое число в стиле — дефолт BASE.
    """
    try:
        return int(_styles.resolve(style)["intro_row_max"])
    except (TypeError, ValueError):
        return INTRO_HOOK_ROW_MAX_CHARS


def cmd_intro(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit,
              inserts: list[dict[str, Any]] | None = None, style: Any = None) -> Any:
    """ИИ-разметка интро (строки первых слов) + акценты-группы посреди ролика.
    inserts — уже выбранные вставки ({start_sec,duration_sec}); если не переданы,
    подхватываем сайдкар <stem>.inserts.json. Нужны, чтобы акценты вставали ТАМ,
    ГДЕ ВСТАВОК НЕТ.
    style — стиль КЛИПА (имя, dict или None = BASE): из него длина строки хука
    (intro_row_max) — ею режутся длинные строки переносом.
    Возвращает {intro_rows:[{count,color,break,back,anim,fx}],
                mid_groups:[{from,count,color,break,back,anim,fx}]}."""
    row_max = _intro_row_max(style)
    intro_sys = (system or INTRO_SYSTEM).replace(INTRO_ROW_MAX_TOKEN, str(row_max))
    words = _words_from_xml(xml_path)
    dur = words[-1][3] if words else 0
    if inserts is None:
        side = os.path.splitext(xml_path)[0] + ".inserts.json"
        try:
            inserts = json.load(open(side, encoding="utf-8")).get("inserts") or []
        except ReelsiError: raise
        except Exception as e:
            # сайдкара нет — норма (вставок не было); битый — акценты пойдут без учёта вставок,
            # поэтому пишем в журнал, а не молча
            if os.path.exists(side):
                log.warning("сайдкар вставок не прочитан (%s) — акценты интро без учёта вставок", e)
            inserts = []
    # Цель считается ПО СВОБОДНЫМ ОКНАМ, а не по длине ролика: смысл текста за спиной —
    # занять места, где вставок нет. Квота на каждое окно уходит в задание числом — в
    # системном промпте «примерно один на 6 секунд» модель читает как пожелание, а
    # конкретное «в окне 65–73с нужно 2» выполняет.
    intro_est = words[min(len(words), INTRO_EST_WORDS) - 1][3] if words else 0
    free = _free_windows(words, inserts, after=intro_est)
    want = sum(_free_quota(a, b) for a, b in free) or max(4, round(dur / INTRO_MID_PER_SEC))
    user = (f"Длина ролика ~{dur:.0f} секунд, всего {len(words)} слов. "
            f"НУЖНО ПРИМЕРНО {want} акцентов (mid_groups) — по квоте свободных окон ниже. "
            f"Пустое окно без единого акцента — главная ошибка этой разметки.\n"
            f"Слова ролика (индекс, слово, тайминг в секундах):\n" + _word_lines(words))
    hint = _intro_free_hint(words, free)
    if hint:
        user += "\n\nКАРТА РОЛИКА (для выбора мест под акценты):\n" + hint
    if dry:
        emit(intro_sys + "\n---\n" + user); return None
    _t0 = time.time()
    _lvl = step_reasoning("intro")
    # Размышления растут с числом слов: плоские 6000 + 8000 не хватило на ролике
    # в 280 слов (13997 из 14000 ушло на размышления, ответ оборвался четыре раза
    # подряд); множитель 20 даёт на таком ролике 11600 базы (~19600 при medium).
    data = _ask_json(intro_sys, user, INTRO_SCHEMA,
                     model=model, url=url,
                     max_tokens=reason_budget(6000 + len(words) * 20, _lvl),
                     emit=emit, reasoning=_lvl, profile=step_profile("intro"),
                     step="intro")
    emit("  ⏱ ИИ-интро: LLM-вызов {sec:.1f}с", sec=time.time() - _t0)
    rows: list[dict[str, Any]] = []
    for r in (data.get("intro_rows") or []):
        if not isinstance(r, dict):
            continue                                   # без structured outputs прилетает что угодно
        try:
            n = int(r.get("count") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if n > 0:
            c = r.get("color")
            color = c if c in ("white", "yellow", "accent") else "white"
            rows.append({"count": max(1, n),
                         "color": color,
                         "break": bool(r.get("break")),
                         "back": bool(r.get("back"))})
    if rows:
        rows[0]["break"] = True            # первая строка — голова хука по определению
    intro_max = min(len(words), INTRO_MAX_WORDS)
    total = sum(r["count"] for r in rows)
    if total > intro_max:
        emit("  интро урезано: модель просила {total} слов, беру {max} (в ролике {words} слов)",
             total=total, max=intro_max, words=len(words))
        cut, left = cast(list[dict[str, Any]], []), intro_max
        for r in rows:
            if left <= 0:
                break
            cut.append({"count": min(r["count"], left), "color": r["color"],
                        "break": r.get("break", False),
                        "back": bool(r.get("back"))})
            left -= cut[-1]["count"]
        rows = cut
    intro_len = sum(r["count"] for r in rows)
    if intro_len <= len(words):                        # длинные строки рвём переносом
        n0 = len(rows)
        rows = _intro_defunc(rows, words)              # служебное слово не в конце строки
        if len(rows) != n0:
            emit("  склейка служебных слов: {before} -> {after} строк", before=n0, after=len(rows))
        n1 = len(rows)
        rows = _intro_fix_prefix(rows, words)          # «не»/предлог не отрываются от слова
        if len(rows) != n1:
            emit("  перенос предлогов: {before} -> {after} строк", before=n1, after=len(rows))
        nrows = _wrap_intro_rows(rows, words, row_max)
        if len(nrows) != len(rows):
            emit("  перенос строк интро: {before} -> {after}", before=len(rows), after=len(nrows))
        rows = nrows
    if rows:                                           # страховка: модель без break
        rows = _hook_breaks(rows, words)
        emit("  хук: {rows} строк, {precomps} прекомпов",
             rows=len(rows), precomps=sum(1 for r in rows if r['break']))
    busy = _busy_windows(inserts)
    groups = []
    for g in (data.get("mid_groups") or []):
        if not isinstance(g, dict):
            continue
        try:
            c = g.get("color")
            color = c if c in ("white", "yellow", "accent") else "white"
            groups.append((int(g.get("from") or 0), max(1, int(g.get("count") or 1)),
                           color, bool(g.get("back"))))
        except (TypeError, ValueError, OverflowError):
            continue
    mids = _place_mids(groups, words, intro_len, busy, emit=emit)   # «не»/предлог — внутри
    mids = _place_call_word(mids, words, intro_len, busy, emit=emit)   # призыв — последним акцентом
    # Оформление по группам: позиция строки и размер группы определяют анимацию.
    # Группа — строки между break (одна анимация на группу, где правило не говорит иного).
    for batch in (rows, mids):
        grp_start, grp_end = 0, 1
        for i, r in enumerate(batch):
            if r.get("break") or i == 0:
                # начало новой группы — посчитать её размер до следующего break
                grp_start = i
                grp_end = i + 1
                while grp_end < len(batch) and not batch[grp_end].get("break"):
                    grp_end += 1
            r["anim"], r["fx"] = _intro_look(r.get("color"), r.get("back"),
                                              r.get("count", 1),
                                              group_pos=i - grp_start,
                                              group_size=grp_end - grp_start)
    ngrp = sum(1 for m in mids if m["break"])
    # Пустые окна называем поимённо: «мало акцентов» ни о чём не говорит, а «65–73с без
    # акцента» — это ровно то место, куда потом руками лезет юзер.
    heads = [words[m["from"]][2] for m in mids if m["break"] and m["from"] is not None]
    intro_t = words[intro_len - 1][3] if 0 < intro_len <= len(words) else 0.0
    empty = [(a, b) for a, b in _free_windows(words, inserts, after=intro_t)
             if b - a >= INTRO_EMPTY_WARN and not any(a <= t < b for t in heads)]
    for a, b in empty:
        emit("  окно {from_sec:.0f}–{to_sec:.0f}с осталось без акцента (свободно от вставок)",
             from_sec=a, to_sec=b)
    if ngrp < want - 3 or empty:                       # видно в логе, что модель поскупилась
        emit("  акцентов {ngrp} при цели ~{want} (модель прислала {groups}, пустых окон {empty})",
             ngrp=ngrp, want=want, groups=len(groups), empty=len(empty))
    out = os.path.splitext(xml_path)[0] + ".intro.json"
    atomic_json_dump(out, {"intro_rows": rows, "mid_groups": mids}, indent=1)
    emit("интро: {rows} строк ({words} слов) + {mids} акцентов посреди ролика",
         rows=len(rows), words=intro_len, mids=ngrp)
    return {"path": out, "intro_rows": rows, "mid_groups": mids}

