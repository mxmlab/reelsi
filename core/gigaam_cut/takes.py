# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дубли, повторы и пост-проход кодом.

27b — один вызов на весь ролик, и он систематически спотыкается на одних и тех же
местах: оставляет РАННИЙ заход вместо последнего, режет по обрубку слова, плодит
островки в пару кадров. Эти чистки детерминированные и без ИИ — их и проверяют
tests/test_gigaam_postprocess.py.
"""
from __future__ import annotations
import re
from typing import Any, Sequence
from core import align
from . import tune as _tune
from .tune import (FILLERS, HEAL_RUN, HEAL_SEG, NG_MARKERS, REPEAT_N, REPEAT_WIN, TAKE_WIN,
                   _ranges, drop_micro_keeps, keep_segments)
from core.app_meta import console_emit, wrap_emit



def _words_text(words: Sequence[Any], a: int, b: int) -> str:
    return " ".join(words[i]["w"] for i in range(a, b + 1))


# --------------------------------------------------------------------------- #
# Пост-проход по решению 27b (детерминированный, без ИИ)
#
# 27b — один вызов на весь ролик, и он систематически спотыкается на трёх
# вещах: оставляет РАННИЙ дубль вместо последнего, оставляет обрубок слова на
# стыке («питание воло | волосяных луковиц») и роняет островки в 2-3 кадра.
# Всё это ловится кодом по пословным таймингам, поэтому не полагаемся на ИИ.
# --------------------------------------------------------------------------- #
def _tok(w: str | None) -> str:
    """Слово -> голые буквы (для сравнения повторов)."""
    return "".join(re.findall(r"[а-яёa-z]+", (w or "").lower()))


def _is_ng(words: list[dict[str, Any]], idxs: Any) -> bool:
    """Есть ли в куске мат/NG-реплика — такое обратно не возвращаем."""
    return any(any(t.startswith(m) or m in t for m in NG_MARKERS)
               for t in (_tok(words[i]["w"]) for i in idxs) if t)


def _same(t1: str, t2: str) -> bool:
    """Слова считаем одним и тем же, если совпали, одно — обрубок другого
    («воло»/«волосяных») или различие в одну букву («фолликулы»/«фоликулы»)."""
    if t1 == t2:
        return True
    a, b = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    if len(a) >= 4 and b.startswith(a):
        return True
    if len(a) >= 5 and len(b) - len(a) <= 1:
        return _lev1(a, b)                 # «фолликулы»/«фоликулы» — вставка буквы
    return False

def _lev1(a: str, b: str) -> bool:
    """Расстояние Левенштейна <= 1 (замена, вставка или удаление одной буквы)."""
    if a == b:
        return True
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    if len(b) - len(a) != 1:
        return False
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def find_takes(
    words: list[dict[str, Any]], max_n: int = REPEAT_N, win: float = TAKE_WIN, lookback: int = 14, emit: Any = console_emit
) -> list[tuple[int, int, int]]:
    """Фальстарты: место, где спикер НАЧАЛ фразу заново. Признак — начало
    сегмента с позиции j дословно повторяет начало недавнего сегмента с
    позиции i (>=2 слова, с поправкой на обрубки и опечатки ASR). Тогда
    [i, j-1] — брошенный заход, а чистовой начинается с j.

    Ищем именно ПОВТОР НАЧАЛА, а не заходы равной длины: спикер переснимает
    как получится («чтобы не дать» -> «чтобы не дать свету» -> «чтобы не
    дать свету засветить кадр»), период тут не постоянный. После
    находки планка `floor` двигается на j: всё левее уже решено, поэтому
    цепочка из трёх заходов разбирается по очереди и кластеры не пересекаются.

    ПЕРЕЧИСЛЕНИЕ сюда не попадает: «где он сделал вот это А где он сделал
    другое» — то же начало, но обе половины содержательны и связаны союзом
    (align.is_enumeration). Без этой проверки force_takes сносил всё до второго
    захода — жалоба юзера 2026-08-04.

    Возвращает [(start, end, last)] — start..end весь кластер (брошенный
    заход + начало чистового), last = первое слово чистового захода."""
    emit = wrap_emit(emit)
    toks = [_tok(w["w"]) for w in words]
    n_all = len(words)
    res: list[tuple[int, int, int]]
    enum: list[tuple[int, int]]
    j: int
    floor: int
    res, enum, j, floor = [], [], 1, 0
    while j < n_all:
        best = None
        for i in range(max(floor, j - lookback), j):
            m = 0
            while (m < max_n and j + m < n_all and i + m < j
                   and toks[i + m] and _same(toks[i + m], toks[j + m])):
                m += 1
            if m >= 2 and (best is None or m > best[1]):
                best = (i, m)
        # пересъёмка идёт по горячим следам: если от брошенного захода до
        # повтора больше win — это не заход, а осмысленный повтор мысли
        if best is None or words[j]["start"] - words[best[0]]["start"] > win:
            j += 1
            continue
        i, m = best
        if align.is_enumeration(toks, i, j):
            enum.append((i, j))        # перечисление, а не брошенный заход
            j += 1
            continue
        res.append((i, j + m - 1, j))
        floor = j                      # левее уже разобрано
        j += 1                         # цепочку заходов ловим следующим витком
    if res:
        emit("  фальстарты (код): {count} {list}", count=len(res), list=[(a, b) for a, b, _ in res], flush=True)
    if enum:
        emit("  перечисления (не режем): {count} {list}", count=len(enum), list=enum, flush=True)
    return res


def _take_tail(words: list[dict[str, Any]], drop: set[int], b: int, silence_bounds: Any = None) -> int:
    """Докуда тянется чистовой заход: граница кластера — это лишь то место, где
    закончилось совпадение с брошенным заходом («используем его уже» ← совпало,
    «очень давно» ← уже нет). Реальная фраза идёт до ПАУЗЫ, поэтому добираем
    вырезанные моделью слова вправо, пока не упрёмся в тишину или в чужой кусок."""
    sb = silence_bounds or ()
    end = b
    while end + 1 < len(words) and (end + 1) in drop and end not in sb:
        end += 1
    return end


def force_takes(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], takes: list[tuple[int, int, int]] | None = None, silence_bounds: Any = None,
    protect: Any = (), emit: Any = console_emit
) -> list[tuple[int, int, int]]:
    """ЖЕЛЕЗНОЕ ПРАВИЛО в коде: внутри кластера дублей остаётся ПОСЛЕДНИЙ заход,
    ранние выкидываются — независимо от того, что нарезала 27b (она стабильно
    промахивается индексами: называет верный заход в notes и режет соседний).

    Кластер, выкинутый моделью ЦЕЛИКОМ, — отдельный случай. Раньше мы его не
    трогали («значит это NG-блок»), но замер на C1355 показал, что модель ровно
    так же сносит и нормальный контент, повторённый трижды: ушла вся история
    «ещё пять лет когда я ездил в штаты выступать» вместе с чистовым заходом.
    Поэтому теперь ВОЗВРАЩАЕМ последний заход — до конца вырезанного куска, а не
    до границы кластера (иначе останется огрызок «ещё пять лет» без продолжения).
    Не возвращаем только блоки с мат/NG-маркерами: там модель права."""
    if takes is None:
        takes = find_takes(words, emit=emit)
    fixed: list[tuple[int, int, int]]
    revived: list[tuple[int, int]]
    fixed, revived = [], []
    for a, b, last in takes:
        span = range(a, b + 1)
        if any(i in protect for i in span):
            continue
        if not any(i in kept for i in span):        # блок выкинут целиком
            if _is_ng(words, span):                 # мат/NG — модель права
                continue
            end = _take_tail(words, drop, b, silence_bounds)
            if _is_ng(words, range(last, end + 1)):
                continue
            for i in range(last, end + 1):
                kept.add(i); drop.discard(i)
            revived.append((last, end))
        before = {i for i in span if i in kept}
        for i in span:
            if i >= last:
                kept.add(i); drop.discard(i)
            else:
                kept.discard(i); drop.add(i)
        tail = _take_tail(words, drop, b, silence_bounds)
        if tail > b and not _is_ng(words, range(b + 1, tail + 1)):
            for i in range(b + 1, tail + 1):     # хвост чистового захода за границей кластера
                kept.add(i); drop.discard(i)
        if before != {i for i in span if i in kept}:
            fixed.append((a, b, last))
    for lo, hi in revived:
        emit("  блок дублей был выкинут целиком — вернул последний заход «{text}»",
             text=_words_text(words, lo, min(hi, lo + 8))[:80], flush=True)
    if fixed:
        for a, b, last in fixed:
            emit("  дубль {a}-{b}: оставил последний заход «{text}»",
                 a=a, b=b, text=_words_text(words, last, b), flush=True)
    return fixed


def veto_unique_drops(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], takes: list[tuple[int, int, int]] | None = None, min_words: int = 5, min_sec: float = 1.5,
    protect: Any = (), emit: Any = console_emit
) -> list[int]:
    """Страховка от «модель снесла хороший кусок». 27b иногда режет диапазон
    гораздо шире брошенного захода — на C1353 она выкинула 0-44 вместо 14-20 и
    унесла с собой весь интро-хук («ты точно облысеешь если сядешь на курс…»).

    Логика: настоящий брак почти всегда ЗАКАНЧИВАЕТСЯ пересъёмкой, то есть его
    слова лежат в кластере фальстарта. Поэтому по КРАЯМ каждого вырезанного
    диапазона отматываем слова, которые ни в один кластер не входят, и если
    такой хвост длинный (>= min_words слов и >= min_sec секунд) — возвращаем:
    уникальный связный кусок речи никак не может быть дублем."""
    if takes is None:
        takes = find_takes(words, emit=lambda *a, **k: None)
    in_take: set[int] = set()
    for a, b, _last in takes:
        in_take.update(range(a, b + 1))
    back: list[int] = []
    for a, b in _ranges(sorted(drop)):
        for lo, hi, step in ((a, b, 1), (b, a, -1)):     # оба края диапазона
            run: list[int]
            i: int
            run, i = [], lo
            while (i - hi) * step <= 0 and i not in in_take and i not in protect:
                run.append(i); i += step
            if len(run) < min_words:
                continue
            i0, i1 = min(run), max(run)
            if words[i1]["end"] - words[i0]["start"] < min_sec:
                continue
            if _is_ng(words, run):
                continue
            kept.update(run); drop.difference_update(run)
            back.extend(run)
    if back:
        emit("  вернул уникальную речь (код): «{text}»",
             text=_words_text(words, min(back), max(back))[:80], flush=True)
    return back


def heal_fragments(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], silence_bounds: Any = None, takes: list[tuple[int, int, int]] | None = None,
    max_seg: float = HEAL_SEG, max_run: int = HEAL_RUN, protect: Any = (), emit: Any = console_emit
) -> list[int]:

    """Обрывок фразы: 27b оставила начало и дорезала короткое продолжение —
    «и список» (без «самых опасных»), «мне впервые» (без «про него рассказали»),
    «а с» (без «максимальным»). Если КОРОТКИЙ оставленный кусок упирается в
    короткий вырезанный хвост, которого нет ни в одном кластере дублей
    (то есть это не брошенный заход, а именно продолжение) — возвращаем его."""
    if takes is None:
        takes = find_takes(words, emit=lambda *a, **k: None)
    in_take: set[int] = set()
    for a, b, _last in takes:
        in_take.update(range(a, b + 1))
    back: list[int] = []
    for a, b in keep_segments(words, kept, silence_bounds):
        if words[b]["end"] - words[a]["start"] > max_seg:
            continue
        run: list[int]
        i: int
        run, i = [], b + 1
        while i < len(words) and i in drop and len(run) <= max_run:
            run.append(i); i += 1
        if not run or len(run) > max_run:
            continue
        if any(i in in_take or i in protect for i in run):
            continue
        if all(_tok(words[i]["w"]) in FILLERS for i in run) or _is_ng(words, run):
            continue                            # «наверное» и мат обратно не тащим
        kept.update(run); drop.difference_update(run)
        back.extend(run)
    if back:
        emit("  дорастил обрывки (код): вернул {words}",
             words=[words[i]["w"] for i in sorted(back)], flush=True)
    return back


def dedupe_repeats(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], max_n: int = REPEAT_N, protect: Any = (), emit: Any = console_emit
) -> list[int]:
    """Соседние повторы СРЕДИ ОСТАВЛЕННЫХ слов: «в организме в организме» ->
    остаётся ПОСЛЕДНИЙ заход, ранний уходит в drop (то же железное правило,
    что и в промпте, но здесь оно гарантировано). Ищем от длинных n-грамм к
    коротким, пока есть что чистить."""
    removed = []
    changed = True
    while changed:
        changed = False
        seq = sorted(kept)
        toks = [_tok(words[i]["w"]) for i in seq]
        for n in range(max_n, 0, -1):
            for p in range(0, len(seq) - 2 * n + 1):
                a, b = toks[p:p + n], toks[p + n:p + 2 * n]
                if not all(a) or a != b:
                    continue
                gone = seq[p:p + n]                    # ранний заход — на выброс
                if any(i in protect for i in gone):
                    continue
                for i in gone:
                    kept.discard(i); drop.add(i)
                removed.extend(gone)
                changed = True
                break
            if changed:
                break
    if removed:
        emit("  повторы (код): выкинул ранние заходы — слова {words}",
             words=sorted(removed), flush=True)
    return removed


def dedupe_fragments(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], win: float = REPEAT_WIN, max_n: int = REPEAT_N,
    protect: Any = (), emit: Any = console_emit
) -> list[int]:
    """Осколок дубля НЕ вплотную: «и список самых опасных … и список для причёски»
    — 27b оставил кусок соседнего захода. Выкидываем ПОЗДНЮЮ копию n-граммы
    (n>=2) — текст от этого не меняется при любом порядке заходов, уходит только
    заикание. Окно win секунд: дальше это уже осмысленный повтор, не запинка.
    Перечисление («…вот это А где он сделал другое») пропускаем: там поздняя
    копия — начало второго пункта, без неё фраза рассыпается."""
    removed = []
    changed = True
    while changed:
        changed = False
        seq = sorted(kept)
        toks = [_tok(words[i]["w"]) for i in seq]
        for q in range(len(seq)):
            for n in range(min(max_n, len(seq) - q), 1, -1):
                later = toks[q:q + n]
                if not all(later):
                    continue
                for p in range(q - 1, -1, -1):
                    if p + n > q:                    # копии пересекаются
                        continue
                    if p + n == q:                   # вплотную — это dedupe_repeats
                        continue
                    if words[seq[q]]["start"] - words[seq[p + n - 1]]["end"] > win:
                        break
                    if toks[p:p + n] != later:
                        continue
                    if align.is_enumeration(toks, p, q):
                        continue                     # перечисление, а не осколок
                    gone = seq[q:q + n]
                    if any(i in protect for i in gone):
                        continue
                    for i in gone:
                        kept.discard(i); drop.add(i)
                    removed.extend(gone)
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
    if removed:
        emit("  осколки дублей (код): выкинул слова {words}",
             words=sorted(removed), flush=True)
    return removed


def drop_truncated(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], min_len: int = 4, protect: Any = (), emit: Any = console_emit
) -> list[int]:

    """Обрубок слова на стыке: оставленное слово — строгий префикс следующего
    оставленного («воло» перед «волосяных», «производ» перед «производные»).
    Такой огрызок всегда мусор от реза — выкидываем."""
    removed = []
    seq = sorted(kept)
    for p in range(len(seq) - 1):
        i, j = seq[p], seq[p + 1]
        if i in protect:
            continue
        ti, tj = _tok(words[i]["w"]), _tok(words[j]["w"])
        if len(ti) >= min_len and len(tj) - len(ti) >= 2 and tj.startswith(ti):
            kept.discard(i); drop.add(i); removed.append(i)
    if removed:
        emit("  обрубки слов (код): выкинул {words}",
             words=[words[i]["w"] for i in removed], flush=True)
    return removed


def _self_repeat(toks: list[str], max_n: int = 6) -> bool:
    """Один и тот же n-грамм (n>=2) встречается дважды — раскачивающаяся
    пересъёмка («в итоге в итоге яички в итоге яички получают…»)."""
    for n in range(min(max_n, len(toks) // 2), 1, -1):
        seen = set()
        for i in range(len(toks) - n + 1):
            g = tuple(toks[i:i + n])
            if not all(g):
                continue
            if g in seen:
                return True
            seen.add(g)
    return False


def _stutter(toks: list[str]) -> bool:
    """Смежные дубли: «но но», «дальше дальше», «ставят ставить»."""
    return any(a and b and a == b for a, b in zip(toks, toks[1:]))


def _share_bigram(a: list[str], b: list[str]) -> bool:
    """Хвосты делят общий биграм — перефразировка, а не параллель («полгода как…»
    против «в полгода как…», «сталкиваются чаще всего» против «сталкиваюсь чаще
    всего»)."""
    if len(a) < 2 or len(b) < 2:
        return False
    bb = set(zip(b, b[1:]))
    return any(x in bb for x in zip(a, a[1:]))


def _stub(a: str, b: str) -> bool:
    """Одно слово — обрубок другого и покороче (порог 3 буквы: «вес»/«весом»,
    «уко»/«уколы»). В _same порог 4 — здесь пересъёмка с огрызком иначе уезжает
    в «разные хвосты»."""
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    return len(s) >= 3 and l.startswith(s)


def _neg_start(B: list[str]) -> bool:
    """Второй заход начинается с отрицания («нет», «наоборот», «а не», «но не»)."""
    if not B:
        return False
    if B[0] in ("нет", "наоборот"):
        return True
    if len(B) >= 2 and (B[0], B[1]) in (("а", "не"), ("но", "не")):
        return True
    return False


# Режиссёрские реплики посреди дубля. В NG_MARKERS их нет нарочно: те — мат и
# «стоп», общие для всех чисток; эти — сигнал «это пересъёмка, а не параллель»
# только для решения «вернуть ли вырезанное» (keep_parallel_runs).
RETALK_MARKERS = ("еще раз", "ещё раз", "не успе", "поехали", "давай")


def _is_parallel(A: list[str], B: list[str], min_prefix: int = 2) -> bool:
    """Вырезанный кусок — ПЕРВАЯ половина параллельной конструкции? True = вернуть.

    См. keep_parallel_runs: жалоба юзера про «одинаковые слова, начало вырезается».
    Параллель выглядит так: вторая половина (следующий оставленный прогон)
    повторяет НАЧАЛО вырезанного (>=2 слов) и расходится в хвостах. Пересъёмку
    (тот же текст чище) не возвращаем: там после общего префикса хвост первого —
    обрубок, повтор, перефразировка или «одна половина — часть другой»."""
    for start in range(len(B)):
        m = 0
        while m < len(A) and start + m < len(B) and A[m] and B[start + m] and A[m] == B[start + m]:
            m += 1
        if m < min_prefix:
            continue
        ta, tb = A[m:], B[start + m:]
        if not ta or not tb:
            continue                     # одна половина — обрубок/продолжение: пересъёмка
        if len(ta) < 2 or len(tb) < 2:
            continue                     # хвосты не «существенно разные»: «после…после»
        if _same(ta[0], tb[0]) or _stub(ta[0], tb[0]):
            continue                     # «вес»/«весом», «диси»/«дисип»
        if ta[1:] and tb[1:] and (tb[1:][:len(ta[1:])] == ta[1:] or ta[1:][:len(tb[1:])] == tb[1:]):
            continue                     # перефразировка: «для»/«от» давления
        if _share_bigram(ta, tb):
            continue                     # перефразировка: те же слова рядом
        if _self_repeat(ta) or _self_repeat(tb) or _stutter(ta) or _stutter(tb):
            continue                     # раскачка — не параллель
        return True
    # антитеза: вырезанного нет в параллели, но следующий заход начинает с отрицания
    if _neg_start(B) and not (len(B) >= len(A) and B[:len(A)] == A):
        return True
    return False


def keep_parallel_runs(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], model_drop: Any = None, look: int = 14, min_prefix: int = 2,
    protect: Any = (), emit: Any = console_emit
) -> list[int]:
    """Вернуть вырезанные ПЕРВЫЕ половины параллелей и антитез.

    Жалоба юзера 2026-08-13: «одинаковые слова при нарезке всё ещё вырезаются
    фразами — типа „многие думают так-то, многие думают так-то“, и вот начало
    вырезается. Или „мы думаем, что вот это правильно — нет, вот это правильно“».
    Модель режет по правилу «оставь чистовой заход», и в параллели/антитезе
    уходит посылка, без которой вторая половина бессмысленна. Код спорить с ней
    может только там, где вторая половина РЕАЛЬНО осталась: возвращаем кусок,
    если следующий оставленный прогон повторяет его начало и расходится в хвостах
    (анафора, союз не нужен) или начинает с отрицания. Настоящие пересъёмки не
    трогаем (там хвост первого — обрубок/повтор/перефразировка).

    model_drop — исходные (модельные) drop-диапазоны, снятые ДО механических
    чисток. Без него фильтр смотрел бы и на вырезы, которые сделал сам код
    (dedupe_repeats/force_takes: «и список самых опасных и список для причёски»),
    — а это пересъёмка, и возвращать её нельзя. У механики свой гейт
    (align.is_enumeration, союз), у модели — этот. Замер на 55 роликах с ручной
    доводкой: 43 возвращённых человеком куска, 40% из них — анафора/
    антитеза, фильтр сохраняет 82%, регрессия ~28 слов (служебные хвосты и
    параллели, срезанные ради темы)."""
    if model_drop is None:
        model_drop = set(drop)
    back: list[int] = []
    for a, b in _ranges(sorted(model_drop)):
        if any(i not in drop for i in range(a, b + 1)):
            continue                     # механические чистки уже вернули часть — не трогаем
        span = range(a, b + 1)
        if any(i in protect for i in span):
            continue
        toks = [_tok(words[i]["w"]) for i in span]
        if _is_ng(words, span):
            continue
        if any(m in " ".join(words[i]["w"] for i in span).lower() for m in RETALK_MARKERS):
            continue                     # посреди куска реплика оператору — пересъёмка
        if all(t in FILLERS for t in toks if t) or len(toks) < 2:
            continue
        if _self_repeat(toks) or _stutter(toks):
            continue                     # раскачивающаяся пересъёмка — модель права
        A = toks[:look]
        if not any(A):
            continue
        B: list[str]
        i: int
        B, i = [], b + 1
        while i < len(words) and len(B) < look:
            if i in kept:
                B.append(_tok(words[i]["w"]))
            elif B:
                break
            i += 1
        if not B or _self_repeat(B) or _stutter(B):
            continue
        if _is_parallel(A, B, min_prefix):
            for i in span:
                kept.add(i); drop.discard(i)
            back.extend(span)
    if back:
        emit("  вернул параллели и антитезы (код): слова {words}", words=sorted(back), flush=True)
    return back


def postprocess(
    words: list[dict[str, Any]], kept: set[int], drop: set[int], silence_bounds: Any = None, protect: Any = (), light: bool = False,
    emit: Any = console_emit, dedupe: bool | None = None, rule: dict[int, str] | None = None
) -> tuple[set[int], set[int]]:
    """Чистки подряд (порядок важен: повторы/обрубки могут породить новые
    микро-островки, поэтому островки — последними; force_takes — первым, он
    единственный может ВЕРНУТЬ слово в kept). protect — слова, которые только
    что вернула самопроверка швов: их не трогаем, иначе итерации зациклятся
    (вернули -> выкинули -> вернули).

    dedupe=False — снимает всё, что решает про ДУБЛИ: find_takes,
    force_takes, dedupe_repeats, dedupe_fragments, drop_truncated, и автоматически
    включает light (решение модели — готовая расстановка резов, спорить с ней
    кодом нельзя; остаётся только drop_micro_keeps). None — текущий
    tune.DEDUPE (профиль спикера / дефолт False).

    rule — атрибуция: dict {индекс слова: имя функции}, кому
    каждая вырезанная чистка принадлежит. Обновляется на месте: слова, ушедшие
    в drop новым правилом, получают его имя; вернувшиеся из drop (veto/heal/
    keep_parallel_runs) имя теряют. Инициализирует вызывающий (decide_markup).

    light=True — только МЕХАНИЧЕСКИЕ чистки (повторы, обрубки, островки), без
    force_takes/veto/heal. Эти трое компенсируют промахи модели ИНДЕКСАМИ и
    навязывают своё правило дублей; в режиме разметки решение модели — уже
    готовая расстановка резов по тексту, спорить с ней кодом нельзя."""
    if dedupe is None:
        dedupe = _tune.DEDUPE
    # Решение модели — уже готовая расстановка резов, спорить с ней кодом нельзя;
    # галка «Правка нарезки кодом» — единственный способ вернуть спор.
    if not dedupe:
        light = True
    protect = set(protect)
    model_drop = set(drop)                 # вырезы МОДЕЛИ — им и спорит keep_parallel_runs

    def tick(before: set[int], name: str | None) -> None:
        """После одной чистки: новые слова в drop — её правило; вернувшиеся —
        атрибуцию снять (их в cutlog уже нет, правило вводило бы в заблуждение)."""
        if rule is None:
            return
        if name is not None:
            for i in drop - before:
                rule[i] = name
        for i in before - drop:
            rule.pop(i, None)

    if not light:
        takes = None
        if dedupe:
            takes = find_takes(words, emit=emit)
            b = set(drop)
            force_takes(words, kept, drop, takes=takes, silence_bounds=silence_bounds,
                        protect=protect, emit=emit)
            tick(b, "force_takes")
        b = set(drop)
        veto_unique_drops(words, kept, drop, takes=takes, protect=protect, emit=emit)
        tick(b, None)
        b = set(drop)
        heal_fragments(words, kept, drop, silence_bounds, takes=takes,
                       protect=protect, emit=emit)
        tick(b, None)
    if dedupe:
        b = set(drop)
        dedupe_repeats(words, kept, drop, protect=protect, emit=emit)
        tick(b, "dedupe_repeats")
        b = set(drop)
        dedupe_fragments(words, kept, drop, protect=protect, emit=emit)
        tick(b, "dedupe_fragments")
        b = set(drop)
        drop_truncated(words, kept, drop, protect=protect, emit=emit)
        tick(b, "drop_truncated")
    b = set(drop)
    drop_micro_keeps(words, kept, drop, silence_bounds, protect=protect, emit=emit)
    tick(b, "drop_micro_keeps")
    if not light:
        # ПОСЛЕ дедупов и микро-островков: они режут по тексту и могли бы убрать
        # первую половину параллели заново (см. keep_parallel_runs). Смотрим только
        # на вырезы МОДЕЛИ — механические (пересъёмки) код и так режет верно.
        b = set(drop)
        keep_parallel_runs(words, kept, drop, model_drop=model_drop,
                           protect=protect, emit=emit)
        tick(b, None)
    return kept, drop


# Чем позднее в конвейере, тем конкретнее правило для диапазона (при ничьей в
# диапазоне, снятом разными чистками, победитель — то, что резало позже).
_RULE_ORDER = {"decide_markup": 0, "force_takes": 1, "dedupe_repeats": 2,
               "dedupe_fragments": 3, "drop_truncated": 4, "drop_micro_keeps": 5}


def _range_rule(rule: dict[int, str] | None, a: int, b: int) -> str:
    """Имя правила для диапазона вырезанных слов [a, b]. Слова в диапазоне могут
    быть сняты РАЗНЫМИ чистками (модель дорезала код и наоборот) — берём то, что
    сняло БОЛЬШИНСТВО слов; ничья — то, что резало позже в конвейере."""
    cnt: dict[str, int] = {}
    for i in range(a, b + 1):
        r = (rule or {}).get(i, "decide_markup")
        cnt[r] = cnt.get(r, 0) + 1
    return max(cnt, key=lambda r: (cnt[r], _RULE_ORDER.get(r, 0)))


def build_cutlog(
    words: list[dict[str, Any]], drop: set[int], silence_bounds: Any = (), breath_marks: Any = (), rule: dict[int, str] | None = None
) -> list[dict[str, Any]]:
    """Записи «что убрано» для .cuts.json (gigaam-путь): вырезанные слова по
    диапазонам, тишина, вздохи. Каждая запись несёт `rule` — имя функции/
    источника, снявшего кусок: так в редакторе нарезки видно, кто виноват в
    лишнем резе. rule — {индекс слова: имя} из postprocess.
    Возвращает записи, сортированные по t0 (как писалось раньше)."""
    cutlog = []
    for a, b in _ranges(sorted(drop)):
        cutlog.append({"t0": round(words[a]["start"], 2), "t1": round(words[b]["end"], 2),
                       "text": _words_text(words, a, b), "reason": "слова (GigaAM + 27b)",
                       "source": "вырезано", "rule": _range_rule(rule, a, b)})
    for i in sorted(silence_bounds):
        if i in drop or (i + 1) in drop:
            continue
        cutlog.append({"t0": round(words[i]["end"], 2), "t1": round(words[i + 1]["start"], 2),
                       "text": "(тишина)", "reason": f"тишина > {_tune.SILENCE_SEC:.1f}с (авто)",
                       "source": "тишина", "rule": "silence"})
    for m in breath_marks:
        if m.get("вырезано"):
            cutlog.append({"t0": round(m["t0"], 2), "t1": round(m["t1"], 2),
                           "text": f"({m['класс']})",
                           "reason": f"вздох/«кхе», уверенность {m['p']:.2f}",
                           "source": "вздох", "rule": "вздох"})
    cutlog.sort(key=lambda c: c["t0"])
    return cutlog
