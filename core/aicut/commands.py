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
from core.app_meta import console_emit
from core.fileio import atomic_json_dump
from core.umsg import ReelsiError


def _words_from_xml(xml_path: str) -> list[tuple[int, str, float, float]]:
    from core import xml2ae
    meta, cams, subs, _ = xml2ae.parse_full(xml_path)
    fps = meta["fps"]
    return [(k, w, s / fps, e / fps) for k, (s, e, w) in enumerate(subs)]


def _word_lines(words: Sequence[tuple[int, str, float, float]]) -> str:
    return "\n".join(f"{k}\t{w}\t{s:.2f}-{e:.2f}" for k, w, s, e in words)


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


def cmd_yellow(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit) -> Any:
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
# Цель автоподбора — постоянная 13: десять фото и три видео. Длина ролика
# больше не повышает цель до 14–22 (BX отменён): плотность не растёт, набор всегда один.
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

    def spread(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
        if len(items) <= quota:
            return items
        if quota <= 0:
            return []
        if quota == 1:
            return [items[len(items) // 2]]
        # Сохраняем начало и конец покрытия, а избыток выбираем равномерно.
        n = len(items) - 1
        return [items[round(i * n / (quota - 1))] for i in range(quota)]

    return sorted(spread(photos, photo_quota) + spread(videos, video_quota),
                  key=lambda x: float(x.get("start_sec", 0) or 0))


def cmd_inserts(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit,
                count: int | None = None, avoid: list[dict[str, Any]] | None = None, rejected: list[dict[str, Any]] | None = None, window: tuple[float, float] | None = None) -> Any:
    """count/avoid — «добор недостающих»: сгенерить РОВНО count НОВЫХ вставок для других
    мест, не повторяя avoid (список уже выбранных: {type,start_sec,query}). При частичном
    доборе сайдкар .inserts.json НЕ перезаписываем (он держит полный набор).
    window=(t0,t1) — добор ТОЛЬКО в указанный промежуток секунд: та же проверка
    покрытия, что у «добор выброшенного», но для пустого хвоста ролика.
    rejected — память правок: предложения, которые юзер УДАЛЯЛ раньше
    ({type,start_sec,query}) — модель просим не повторять, похожие фильтруем кодом."""
    words = _words_from_xml(xml_path)
    dur = words[-1][3] if words else 0
    target = ins_target(dur)
    # Квота основного вызова постоянна; рекурсивный добор получает только недостающие типы.
    if count:
        keep_photo = sum(1 for a in (avoid or []) if a.get("type") != "video")
        keep_video = sum(1 for a in (avoid or []) if a.get("type") == "video")
        photo_quota = max(0, INS_PHOTO - keep_photo)
        video_quota = max(0, INS_VIDEO - keep_video)
    else:
        photo_quota, video_quota = INS_PHOTO, INS_VIDEO
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
        def _sim(q1: Any, q2: Any) -> float:
            a = set(re.findall(r"[а-яёa-z]+", (q1 or "").lower()))
            b = set(re.findall(r"[а-яёa-z]+", (q2 or "").lower()))
            return (len(a & b) / len(a | b)) if a and b else 0.0
        kept_ins = []
        for it in ins:
            hit = next((r for r in rejected
                        if (r.get("type") == "video") == (it.get("type") == "video")
                        and abs(float(r.get("start_sec", 0) or 0) - float(it.get("start_sec", 0) or 0)) <= 2.0
                        and _sim(r.get("query"), it.get("query")) >= 0.6), None)
            if hit:
                emit("  память правок: убрал повтор удалённого — ~{sec:.0f}с «{query}»",
                     sec=float(it.get('start_sec', 0)), query=str(it.get('query', ''))[:40])
            else:
                kept_ins.append(it)
        ins = kept_ins
    # 5) квота типов — детерминированно: что модель переложила сверх квоты этого вызова,
    # срезаем по времени. Дальше считаем недостачу и добираем только недостающие типы.
    ins = _cap_by_quota(ins, photo_quota, video_quota)
    # 6) добор недостающего. Отсев зонами/квотой честнее сдвига, но юзер нажал «подобрать
    # заново» и ждёт полный набор 13 (10 фото + 3 видео), а не «13 минус то, что модель
    # поставила не туда». Просим модель ровно недостающее число КАЖДОГО типа для ДРУГИХ
    # мест (avoid = оставшиеся). Рекурсия ровно на один уровень: у вложенного вызова
    # count уже задан, и квота там считается из avoid.
    if not count:
        n_photo = sum(1 for x in ins if x.get("type") != "video")
        n_video = sum(1 for x in ins if x.get("type") == "video")
        need = max(0, INS_PHOTO - n_photo) + max(0, INS_VIDEO - n_video)
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
                                 count=need, avoid=av, rejected=rejected) or {}).get("inserts") or []
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
        need2 = max(0, INS_PHOTO - n_photo) + max(0, INS_VIDEO - n_video)
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
                                      window=(last, tail_at)) or {}).get("inserts") or []
            except ReelsiError: raise
            except Exception as e:
                emit("  ! добор окна не удался: {err_type}: {err}", err_type=type(e).__name__, err=e)
                extra2 = []
            ins = sorted(ins + extra2[:need2], key=lambda x: float(x.get("start_sec", 0) or 0))
    # Финальная страховка полного набора: не больше общей и типовых квот.
    ins = _cap_by_quota(ins, INS_PHOTO, INS_VIDEO)
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
# Лимит переноса ДЛЯ ХУКА — отдельный, потому что порог 9 режет ровно то, что юзер
# собирает руками: на ручном эталоне (38 .jsx, правила интро, поправка
# 2026-08-14) медиана строки хука 8 символов, p90 13, max 16. Акценты остаются на 9:
# там 89 % строк в одно слово, порог с эталоном совпадает.
INTRO_HOOK_ROW_MAX_CHARS = 14

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
    части по ближайшей к середине границе слов (и так рекурсивно). Возвращает список
    кусков (списков слов). Одно слово не режем — переносим целиком. Среди кандидатов
    точка переноса, оставляющая служебное слово (INTRO_FUNC_WORDS) последним в куске,
    штрафуется и берётся только если других нет."""
    out, stack = [], [list(ws)]
    while stack:
        cur = stack.pop(0)
        if len(cur) < 2 or len(" ".join(cur)) <= limit:
            out.append(cur)
            continue
        half = len(" ".join(cur)) / 2.0            # режем там, где половина символов
        best, acc, bestd, best_bad = 1, 0, None, False
        for j in range(len(cur) - 1):
            acc += len(cur[j]) + 1
            d = abs(acc - half)
            bad = cur[j] in INTRO_FUNC_WORDS       # кусок останется БЕЗ своего слова
            if (bestd is None or (not bad and best_bad)
                    or (bad == best_bad and d < bestd)):
                best, bestd, best_bad = j + 1, d, bad
        stack = [cur[:best], cur[best:]] + stack
    return out


def _wrap_intro_rows(rows: list[dict[str, Any]], words: Sequence[tuple[int, str, float, float]], limit: int = INTRO_HOOK_ROW_MAX_CHARS) -> list[dict[str, Any]]:
    """Перенос длинных строк интро: каждую строку режем по словам на куски ≤ limit.
    Лимит по умолчанию — хуковый 14 (см. INTRO_HOOK_ROW_MAX_CHARS): строка хука у юзера
    бывает до 16 символов, и порог 9 резал её пополам. Первый кусок наследует break
    строки, продолжения получают break=False — тот же контракт, что у акцентов в
    _place_mids."""
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
    ПЕРВОЕ слово следующей строки назад (пока не упрётся в потолок 14 символов или
    не перестанет кончаться служебным словом). Пустые строки выкидываются; границы
    прекомпов модель ставит ненадёжно (прекомп на «ЖЕ», «ПО»), поэтому разбиение
    доверяется _hook_breaks, который вызывается следом."""
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
            if len(" ".join(cur["w"] + [nxt["w"][0]])) > INTRO_HOOK_ROW_MAX_CHARS:
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
    for item in sorted(groups, key=lambda x: x[0]):
        f, c, color = item[0], item[1], item[2]
        has_back = len(item) > 3
        back = bool(item[3]) if has_back else False
        if f < intro_len or f + c > len(words):        # не лезем в интро и за край
            continue
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


def _intro_look(color: Any, back: Any, nwords: int) -> tuple[str, str]:
    """Вывод оформления (anim, fx) по смыслу строки (цвет, задний план, длина).

    Единственный источник правды об оформлении строк интро и акцентов.
    Основано на замере ручной разметки (.jsx) двух спикеров (298 строк):
    - свечение (fx='glow') стоит ТОЛЬКО на цветной строке (77 из 77);
    - белая строка не светится НИКОГДА (0 из 174);
    - accent ВСЕГДА с анимацией (97–100%) и со свечением (74–92%): при nwords <= 2
      ставится glitch, при более длинных строках — reveal;
    - glitch стоит только на цветной строке;
    - back — почти всегда белая строка и мягкая анимация (reveal), никогда не glitch;
    - у жёлтых устойчивого правила нет (анимация 49–53%, свечение 33–45% — это дело вкуса,
      а не правило), поэтому им оформление не навязывается.
    """
    if color == "accent":
        return ("glitch" if nwords <= 2 else "reveal", "glow")
    if back:
        return ("reveal", "")
    return ("", "")


def cmd_intro(xml_path: str, system: str | None = None, dry: bool = False, model: str | None = None, url: str | None = None, emit: Callable[..., Any] = console_emit,
              inserts: list[dict[str, Any]] | None = None) -> Any:
    """ИИ-разметка интро (строки первых слов) + акценты-группы посреди ролика.
    inserts — уже выбранные вставки ({start_sec,duration_sec}); если не переданы,
    подхватываем сайдкар <stem>.inserts.json. Нужны, чтобы акценты вставали ТАМ,
    ГДЕ ВСТАВОК НЕТ.
    Возвращает {intro_rows:[{count,color,break,back,anim,fx}],
                mid_groups:[{from,count,color,break,back,anim,fx}]}."""
    words = _words_from_xml(xml_path)
    dur = words[-1][3] if words else 0
    if inserts is None:
        side = os.path.splitext(xml_path)[0] + ".inserts.json"
        try:
            inserts = json.load(open(side, encoding="utf-8")).get("inserts") or []
        except ReelsiError: raise
        except Exception:
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
        emit((system or INTRO_SYSTEM) + "\n---\n" + user); return None
    _t0 = time.time()
    _lvl = step_reasoning("intro")
    # Размышления растут с числом слов: плоские 6000 + 8000 не хватило на ролике
    # в 280 слов (13997 из 14000 ушло на размышления, ответ оборвался четыре раза
    # подряд); множитель 20 даёт на таком ролике 11600 базы (~19600 при medium).
    data = _ask_json(system or INTRO_SYSTEM, user, INTRO_SCHEMA,
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
        nrows = _wrap_intro_rows(rows, words)
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
    mids = _place_mids(groups, words, intro_len, busy, emit=emit)
    for r in rows + mids:
        r["anim"], r["fx"] = _intro_look(r.get("color"), r.get("back"), r.get("count", 1))
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

