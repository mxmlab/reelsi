# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пост-проход gigaam_cut: чистка решения 27b кодом (повторы, обрубки, островки).

Кейсы сняты с реальной нарезки C1353 (NGAutoCut_out/01_C1353), где 27b оставил
ранние заходы дублей: «в организме в организме», «питание воло | волосяных»,
островок «чтобы» 0.28с и хвост «а» 0.04с.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import gigaam_cut as gc  # noqa: E402
from core import aicut  # noqa: E402


def mk(text, t0=0.0, dur=0.3, gap=0.1):
    """«слово слово …» -> список {w,start,end} с ровной сеткой таймингов."""
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + gap
    return out


def test_decide_prompt_duplicate_contract_is_explicit():
    prompt = gc.DECIDE_MARKUP_SYS
    for phrase in ("ВСЕ соседние", "РОВНО ОДИН", "в скобки ЦЕЛИКОМ",
                   "уникальные слова и продолжения", "duplicate_groups"):
        assert phrase in prompt


def test_decide_schema_duplicate_groups_is_strict():
    schema = gc.DECIDE_MARKUP_SCHEMA
    assert schema["required"] == ["text", "notes", "duplicate_groups"]
    item = schema["properties"]["duplicate_groups"]["items"]
    assert item["required"] == ["takes", "kept", "reason"]
    assert item["additionalProperties"] is False
    assert item["properties"]["takes"]["minimum"] == 2
    assert item["properties"]["kept"]["minimum"] == 1


def test_decide_markup_uses_brackets_for_cuts_and_logs_metadata(monkeypatch):
    words = mk("в организме в организме резко взлетает")
    response = {
        "text": "[в организме] в организме резко взлетает",
        "notes": "ранний заход",
        "duplicate_groups": [
            {"takes": 5, "kept": 5, "reason": "чистовой последний"},
            {"takes": 5, "kept": 5, "reason": "равная полнота"},
        ],
    }
    logs = []
    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: response)
    kept, drop, notes, _ = gc.decide_markup(words, "", emit=logs.append)
    assert sorted(drop) == [0, 1]
    assert sorted(kept) == [2, 3, 4, 5]
    assert notes == "ранний заход"
    assert any("duplicate_groups: 2" in line and "5→5" in line for line in logs)


def test_decide_markup_ignores_malformed_duplicate_metadata(monkeypatch):
    words = mk("одна фраза остается")
    response = {
        "text": "одна фраза остается", "notes": "",
        "duplicate_groups": [None, {"takes": 2, "kept": 3, "reason": "лишний"},
                              {"takes": 2, "kept": 1, "reason": 7}],
    }
    logs = []
    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: response)
    kept, drop, _, _ = gc.decide_markup(words, "", emit=logs.append)
    assert kept == set(range(len(words))) and not drop
    assert any("duplicate_groups: 0" in line for line in logs)


def test_decide_golden_markup_examples(monkeypatch):
    # Перечисление и антитеза не получают metadata-групп.
    words = mk("сначала кожа потом волосы это не вредно а полезно")
    response = {"text": " ".join(w["w"] for w in words), "notes": "",
                "duplicate_groups": []}
    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: response)
    kept, drop, _, _ = gc.decide_markup(words, "", emit=lambda *a, **k: None)
    assert kept == set(range(len(words))) and not drop

    # Оборванный последний заход целиком в скобках, предыдущий остаётся.
    words = mk("обсуждаем новый проект обсуждаем новый про")
    response = {"text": "обсуждаем новый проект [обсуждаем новый про]", "notes": "",
                "duplicate_groups": [{"takes": 2, "kept": 1, "reason": "обрыв"}]}
    monkeypatch.setattr(aicut, "_ask_json", lambda *a, **k: response)
    kept, drop, _, _ = gc.decide_markup(words, "", emit=lambda *a, **k: None)
    assert sorted(kept) == [0, 1, 2] and sorted(drop) == [3, 4, 5]


def run_pp(words, kept_idx, silence="auto", dedupe=True):
    """Пост-проход + текст итоговых кусков ('|' = граница куска в таймлайне).
    По умолчанию границы тишины считаются как в бою (_silence_bounds).
    dedupe — флаг чистки дублей (задание CA): True = как раньше, False = только
    модель (пост-проход не режет дубли, остаётся только механика резов)."""
    if silence == "auto":
        silence = gc._silence_bounds(words)
    kept = set(kept_idx)
    drop = set(range(len(words))) - kept
    gc.postprocess(words, kept, drop, silence, dedupe=dedupe, emit=lambda *a, **k: None)
    assert kept.isdisjoint(drop) and kept | drop == set(range(len(words)))
    return " | ".join(gc._words_text(words, a, b)
                      for a, b in gc.keep_segments(words, kept, silence))


def test_adjacent_repeat_keeps_last():
    """dedupe=True — повтор чистится (последний заход остаётся); dedupe=False —
    решает только модель, повтор остаётся как есть (задание CA)."""
    words = mk("в организме в организме резко взлетает")
    assert run_pp(words, range(len(words))) == "в организме резко взлетает"
    assert run_pp(words, range(len(words)), dedupe=False) == \
        "в организме в организме резко взлетает"


def test_truncated_word_before_full_dropped():
    """Обрубок слова на стыке чистит drop_truncated — он же под флагом dedupe
    (всё, что решает про ДУБЛИ). Выключено — огрызок остаётся."""
    words = mk("намертво блокирует питание воло волосяных луковиц")
    assert run_pp(words, range(len(words))) == "намертво блокирует питание | волосяных луковиц"
    assert run_pp(words, range(len(words)), dedupe=False) == \
        "намертво блокирует питание воло волосяных луковиц"


def test_restart_keeps_later_take():
    """Спикер начал фразу заново — остаётся ПОСЛЕДНИЙ заход, ранний уходит
    целиком (главная жалоба на нарезку: оставляло первый кат из 2-3).
    dedupe=False — модель решает сама, ранний заход остаётся."""
    words = mk("и список самых опасных и список для прически")
    assert run_pp(words, range(len(words))) == "и список для прически"
    assert run_pp(words, range(len(words)), dedupe=False) == \
        "и список самых опасных и список для прически"


def test_enumeration_is_not_a_take():
    """Жалоба юзера 2026-08-04: перечисление резалось как дубль — от «где он
    сделал вот это а где он сделал другое» оставалась только вторая половина.
    Признак перечисления: союз перед вторым заходом + своя мысль у первого."""
    words = mk("где он сделал вот это а где он сделал другое")
    assert gc.find_takes(words, emit=lambda *a, **k: None) == []
    assert run_pp(words, range(len(words))) == "где он сделал вот это а где он сделал другое"


def test_enumeration_fragment_survives():
    """dedupe_fragments в отдельности: поздняя копия — начало второго пункта
    перечисления, без неё фраза рассыпается («вот это а другое»)."""
    words = mk("где он сделал вот это а где он сделал другое")
    kept, drop = set(range(len(words))), set()
    assert gc.dedupe_fragments(words, kept, drop, emit=lambda *a, **k: None) == []


def test_real_restart_with_filler_still_cut():
    """Обратная сторона: союз есть, но у первого захода нет своей мысли —
    только паразит. Это запинка, и она по-прежнему режется. С выключенной
    чисткой дублей остаётся как есть — решает только модель."""
    words = mk("чтобы не дать ээ а чтобы не дать свету засветить кадр")
    assert run_pp(words, range(len(words))) == "чтобы не дать свету засветить кадр"
    assert run_pp(words, range(len(words)), dedupe=False) == \
        "чтобы не дать ээ а чтобы не дать свету засветить кадр"


def test_fragment_of_take_dropped_text_unchanged():
    """dedupe_fragments в отдельности: осколок дубля не вплотную — выкидываем
    ПОЗДНЮЮ копию, текст остаётся тем же (подчищает то, что не попало в фальстарт)."""
    words = mk("и список самых опасных и список для прически")
    kept, drop = set(range(len(words))), set()
    assert gc.dedupe_fragments(words, kept, drop, emit=lambda *a, **k: None) == [4, 5]
    assert " ".join(words[i]["w"] for i in sorted(kept)) == \
        "и список самых опасных для прически"


def test_fragment_before_full_take_also_ok():
    """Обратный порядок (оставлен ранний осколок + поздний полный) — тот же текст.
    dedupe=False — чистки дублей нет, осколок остаётся."""
    words = mk("и список и список самых опасных для прически")
    assert run_pp(words, range(len(words))) == "и список самых опасных для прически"
    assert run_pp(words, range(len(words)), dedupe=False) == \
        "и список и список самых опасных для прически"


def test_heal_fragment_gets_its_tail_back():
    """27b оставила начало фразы и дорезала короткое продолжение — возвращаем
    («и список» -> «и список самых опасных»)."""
    words = mk("собрал и список самых опасных для прически")
    kept = {0, 1, 2, 5, 6}                       # «самых опасных» дорезано моделью
    assert run_pp(words, kept) == "собрал и список самых опасных для прически"


def test_heal_does_not_restore_filler():
    """…но слово-паразит обратно не тащим — его 27b выкинула правильно."""
    words = mk("сейчас я наверное облегчу жизнь")
    kept = {0, 1, 3, 4}
    assert run_pp(words, kept) == "сейчас я | облегчу жизнь"


def test_two_word_island_survives():
    """Два слова подряд — контент, даже если короче MIN_KEEP («во вторых» = 0.56с)."""
    words = (mk("прямо в кожу", t0=0.0)
             + [{"w": "во", "start": 9.0, "end": 9.2},
                {"w": "вторых", "start": 9.3, "end": 9.56}]
             + mk("защищать фолликулы", t0=14.0))
    assert run_pp(words, range(len(words))) == \
        "прямо в кожу | во вторых | защищать фолликулы"


def test_micro_island_dropped():
    """Однословный островок короче MIN_KEEP выкидывается (кадр-другой в таймлайне)."""
    words = mk("читай сохраняй и подписывайся", dur=0.4) + \
        [{"w": "а", "start": 20.0, "end": 20.04}]
    assert run_pp(words, range(len(words))) == "читай сохраняй и подписывайся"


def test_distant_repeat_kept():
    """Повтор дальше REPEAT_WIN — осмысленный, не запинка: не трогаем."""
    words = mk("защищать фолликулы", t0=0.0) + mk("нужно защищать фолликулы", t0=30.0)
    assert run_pp(words, range(len(words))) == \
        "защищать фолликулы | нужно защищать фолликулы"


def test_short_valuable_segment_survives():
    """Одинокое слово длиннее MIN_KEEP — контент, а не мусор."""
    words = [{"w": "безопасности", "start": 7.92, "end": 8.73}] + mk("когда ты ставишь", t0=9.9)
    assert run_pp(words, range(len(words))) == "безопасности | когда ты ставишь"


def test_silence_splits_segments_and_micro_filter_sees_split():
    """Островок считается ПОСЛЕ разрыва по тишине, иначе 0.28с «чтобы» выживет."""
    words = mk("прямо в кожу", t0=0.0) + [{"w": "чтобы", "start": 5.0, "end": 5.28}] + \
        mk("разрушить луковицу", t0=9.0)
    assert run_pp(words, range(len(words))) == "прямо в кожу | разрушить луковицу"


def test_protect_blocks_cleanup():
    """Слова, возвращённые починкой швов, пост-проход не трогает (иначе зацикливание).
    dedupe=False — чистить и нечего, результат тот же (все остаются)."""
    words = mk("в организме в организме резко")
    kept, drop = set(range(len(words))), set()
    gc.postprocess(words, kept, drop, None, protect={0, 1}, emit=lambda *a, **k: None)
    assert kept == set(range(len(words)))
    kept, drop = set(range(len(words))), set()
    gc.postprocess(words, kept, drop, None, protect={0, 1}, dedupe=False,
                   emit=lambda *a, **k: None)
    assert kept == set(range(len(words)))


def test_veto_does_not_resurrect_ng_block():
    """Уникальный, но БРАКОВАННЫЙ кусок (мат/NG) обратно не возвращаем —
    на C1354 иначе воскресало «блять кепка ебаная падает» в начале ролика."""
    words = mk("блять кепка ебаная падает девушки вы когда видите знаменитостей")
    kept = set(range(4, len(words)))
    assert run_pp(words, kept) == "девушки вы когда видите знаменитостей"


def test_veto_returns_unique_speech():
    """…а уникальную связную речь — возвращаем (интро-хук C1353)."""
    words = mk("ты точно провалишься если возьмёшься за это и не будешь соблюдать правила")
    kept = set(range(8, len(words)))
    assert run_pp(words, kept) == \
        "ты точно провалишься если возьмёшься за это и не будешь соблюдать правила"


# --- эталон юзера: реальный кусок C1355 (4 захода подряд) -------------------
#
# Разметка сделана юзером вручную по пословной расшифровке:
#   «за две недели» — хвост предыдущей фразы, остаётся;
#   32-58 — четыре брошенных захода «в большой рекламе мы используем…»,
#           выкинуть ВСЁ, включая полный ранний заход 37-45;
#   59-67 — чистовой заход, остаётся;
#   68-91 — брошенные заходы «ещё пять лет когда я ездил в штаты учиться»;
#   92-100 — чистовой заход, остаётся.
# Индексы в фикстуре сдвинуты на 26 (файл начинается со слова 26).

def c1355_words():
    raw = json.load(open(os.path.join(HERE, "fixtures", "c1355_takes.json"),
                         encoding="utf-8"))
    return [{"w": w, "start": s, "end": e} for w, s, e in raw]


IDEAL_C1355 = ("проекты всего лишь за две недели | в большой рекламе мы "
               "используем его уже очень давно | еще пять лет когда я ездил "
               "в штаты учиться")


def test_c1355_takes_match_user_markup():
    """Всё оставлено (модель ничего не выкинула) — код сам разбирает 4 захода.
    dedupe=False — чистка дублей не режет, остаётся только разбиение по тишине."""
    words = c1355_words()
    assert run_pp(words, range(len(words))) == IDEAL_C1355
    assert run_pp(words, range(len(words)), dedupe=False) == (
        "проекты всего лишь за две недели | в большой рекламе мы используем "
        "в большой рекламе мы используем его уже очень давно в большой рекламе "
        "мы его используем в большой рекламе мы используем его уже в большой "
        "рекламе мы используем его уже очень давно | еще пять лет | еще пять лет "
        "когда я ездил в штаты учиться еще пять лет когда я ездил в штаты "
        "учиться еще пять лет | еще пять лет когда я ездил в штаты учиться")


def test_c1355_takes_when_model_dropped_everything():
    """Модель выкинула ВЕСЬ блок дублей (так делает режим цитат) — при dedupe=True
    последний заход возвращается. С выключенной чисткой дублей (dedupe=False)
    код не трогает решение модели — остаётся только то, что оставила модель."""
    words = c1355_words()
    kept = set(range(0, 6))                      # осталось только «суставы … недели»
    assert run_pp(words, kept) == IDEAL_C1355
    assert run_pp(words, kept, dedupe=False) == \
        "проекты всего лишь за две недели"


def test_c1355_takes_when_model_kept_wrong_take():
    """Модель оставила РАННИЙ заход (исходная жалоба) — код переставляет на поздний.
    dedupe=False — ранний заход остаётся, ничего не переставляется."""
    words = c1355_words()
    kept = set(range(0, 20)) | set(range(42, 66))
    assert run_pp(words, kept) == IDEAL_C1355
    assert run_pp(words, kept, dedupe=False) == (
        "проекты всего лишь за две недели | в большой рекламе мы используем "
        "в большой рекламе мы используем его уже очень давно | еще пять лет | "
        "еще пять лет когда я ездил в штаты учиться еще пять лет когда я ездил "
        "в штаты учиться еще пять лет")


# --- режим разметки: модель возвращает весь текст, резы — в скобках ---------

def test_markup_parses_and_aligns():
    words = mk("а бэ цэ дэ е")
    marked = gc.parse_markup("а [бэ цэ] дэ е")
    drop, cover = gc.align_markup(words, marked, emit=lambda *a, **k: None)
    assert drop == {1, 2} and cover == 1.0


def test_markup_unclosed_bracket_cuts_to_end():
    """Модель оборвалась и не закрыла скобку — режем до конца, а не молча всё оставляем."""
    words = mk("а бэ цэ дэ")
    drop, _ = gc.align_markup(words, gc.parse_markup("а [бэ цэ дэ"),
                              emit=lambda *a, **k: None)
    assert drop == {1, 2, 3}


def test_markup_paraphrase_keeps_word():
    """Модель подправила слово — на нём разметки нет, слово ОСТАЁТСЯ.
    Молча резать то, чего модель не показывала, нельзя."""
    words = mk("берёшь шаблон или его производ дальше")
    drop, cover = gc.align_markup(
        words, gc.parse_markup("берёшь шаблон или его производные дальше"),
        emit=lambda *a, **k: None)
    assert drop == set() and cover < 1.0


def test_markup_skipped_tail_is_kept():
    """Модель не дописала конец текста — недостающие слова остаются в нарезке."""
    words = mk("раз два три четыре пять шесть")
    drop, cover = gc.align_markup(words, gc.parse_markup("раз [два] три"),
                                  emit=lambda *a, **k: None)
    assert drop == {1} and cover < 1.0


def test_markup_user_case_end_to_end():
    """Эталон юзера по C1355, если модель разметила его правильно. light-путь
    (только механика): с выключенной чисткой дублей результат тот же — в этой
    разметке код и так не резал дубли, снял только микро-островки."""
    words = c1355_words()
    txt = []
    for i, w in enumerate(words):
        o = i + 26
        if o in (32, 68):
            txt.append("[")
        txt.append(w["w"])
        if o in (58, 91):
            txt.append("]")
    drop, cover = gc.align_markup(words, gc.parse_markup(" ".join(txt)),
                                  emit=lambda *a, **k: None)
    kept = set(range(len(words))) - drop
    sb = gc._silence_bounds(words)
    gc.postprocess(words, kept, drop, sb, light=True, emit=lambda *a, **k: None)
    assert cover == 1.0
    assert " | ".join(gc._words_text(words, a, b)
                      for a, b in gc.keep_segments(words, kept, sb)) == IDEAL_C1355
    kept2, drop2 = set(kept), set(drop)
    gc.postprocess(words, kept2, drop2, sb, light=True, dedupe=False,
                   emit=lambda *a, **k: None)
    assert " | ".join(gc._words_text(words, a, b)
                      for a, b in gc.keep_segments(words, kept2, sb)) == IDEAL_C1355


@pytest.mark.parametrize("fn", [gc.dedupe_repeats, gc.dedupe_fragments, gc.drop_truncated])
def test_noop_on_clean_text(fn):
    words = mk("этот процесс можно полностью остановить если действовать по схеме")
    kept, drop = set(range(len(words))), set()
    assert fn(words, kept, drop, emit=lambda *a, **k: None) == []
    assert kept == set(range(len(words))) and not drop


# --------------------------------------------------------------------------- #
# keep_parallel_runs (задание BB): анафора и антитеза не уходят в drop.
# --------------------------------------------------------------------------- #
def pp_keep(words, drop_idx):
    """Что останется в drop после keep_parallel_runs."""
    drop = set(drop_idx)
    kept = set(range(len(words))) - drop
    gc.keep_parallel_runs(words, kept, drop, emit=lambda *a, **k: None)
    assert kept.isdisjoint(drop) and kept | drop == set(range(len(words)))
    return sorted(drop)


def test_anaphora_without_conjunction_is_restored():
    """Жалоба юзера 2026-08-13: «кто то испытывает голод, кто то переносит
    спокойнее» — модель резала первую половину по правилу «оставь чистовой
    заход». Союза нет, но это анафора: начало общее, хвосты разные."""
    words = mk("кто то действительно испытывает сильный голод а "
               "кто то переносит препарат гораздо спокойнее")
    assert pp_keep(words, range(7)) == []


def test_anaphora_second_example_restored():
    words = mk("вечный курс это не приговор но вот вечный курс без контроля это "
               "очень плохая история")
    assert pp_keep(words, range(6)) == []


def test_antithesis_is_restored():
    """«Мы думаем, что вот это правильно — нет, вот это правильно»: вторая
    половина начинает с отрицания, первая — посылка, её резать нельзя."""
    words = mk("мы думаем что вот это правильно нет вот это правильно")
    assert pp_keep(words, range(6)) == []


def test_correlative_short_tails_still_cut():
    """«ни на гематокрит, ни на холестерин» — хвосты в одно слово, порог
    «существенно разные хвосты» (>=2 слов) такой случай не пропускает. Это
    осознанное ограничение: тот же порог держит пересъёмки «после…после»
    (замер BB, 2026-08-13)."""
    words = mk("ни на гематокрит ни на холестерин а именно сочетание")
    assert pp_keep(words, range(3)) == [0, 1, 2]


def test_retake_with_stub_is_not_restored():
    """«попадая в организм диси | попадая в организм дисип…» — чистовой заход
    снят поверх обрубка. Хвост первого — огрызок слова, это пересъёмка: не
    возвращаем, иначе фикс отключил бы нарезку."""
    words = mk("попадая в организм диси попадая в организм дисип нормализует")
    assert pp_keep(words, range(5)) == [0, 1, 2, 3, 4]


def test_rephrase_is_not_restored():
    """«для давления | от давления» — перефразировка (тот же текст, одна
    буква), человек такой хвост не возвращает. Не путать с анафорой."""
    words = mk("ровно так же как препараты для давления "
               "ровно так же как препараты от давления")
    assert pp_keep(words, range(7)) == list(range(7))


def test_ng_block_is_not_restored():
    words = mk("давай заново бля попадая в организм дисип нормализует")
    assert pp_keep(words, range(3)) == [0, 1, 2]


def test_rambling_retake_is_not_restored():
    """«в итоге в итоге яички в итоге яички получают…» — раскачка, а не
    параллель: повторяющийся n-грамм внутри вырезанного."""
    words = mk("в итоге в итоге яички в итоге яички получают нестабильную поддержку")
    assert pp_keep(words, range(9)) == list(range(9))


def test_anaphora_survives_postprocess_order():
    """Порядок в postprocess: keep_parallel_runs — последним, иначе дедупы
    срезали бы первую половину заново."""
    words = mk("кто то действительно испытывает сильный голод а "
               "кто то переносит препарат гораздо спокойнее")
    kept = set(range(7, len(words)))          # модель вырезала первую половину
    drop = set(range(7))
    gc.postprocess(words, kept, drop, gc._silence_bounds(words),
                   emit=lambda *a, **k: None)
    assert sorted(drop) == []


# --------------------------------------------------------------------------- #
# dedupe-флаг (задание CA): выключено — чистки дублей не зовутся вовсе.
# --------------------------------------------------------------------------- #
def test_dedupe_off_skips_dedupe_cleanups(monkeypatch):
    """dedupe=False — force_takes/dedupe_repeats/dedupe_fragments/drop_truncated
    не вызываются вообще (решает только модель), слова не режутся. veto/heal
    при этом остаются активными — они только ВОЗВРАЩАЮТ, это механика резов."""
    calls = []
    for name in ("force_takes", "dedupe_repeats", "dedupe_fragments", "drop_truncated"):
        orig = getattr(gc.takes, name)

        def wrap(*a, _n=name, _o=orig, **k):
            calls.append(_n)
            return _o(*a, **k)
        monkeypatch.setattr(gc.takes, name, wrap)
    words = mk("в организме в организме резко взлетает")
    kept, drop = set(range(len(words))), set()
    gc.postprocess(words, kept, drop, gc._silence_bounds(words), dedupe=False,
                   emit=lambda *a, **k: None)
    assert calls == [], "чистки дублей вызваны при dedupe=False: %s" % calls
    assert sorted(kept) == list(range(len(words))), "модель ничего не резала — код порезал"


# --------------------------------------------------------------------------- #
# rule в .cuts.json (задание CA): кто снял кусок, видно в каждой записи.
# --------------------------------------------------------------------------- #
def test_cutlog_rule_on_every_record():
    """Каждая запись .cuts.json (gigaam-путь) несёт rule — имя функции/источника,
    снявшего кусок. Без него «кто виноват в лишнем резе» не видно: всё выглядит
    как «GigaAM + 27b»."""
    words = mk("в организме в организме резко взлетает")
    kept, drop = set(range(len(words))), set()
    rule = {i: "decide_markup" for i in drop}
    gc.postprocess(words, kept, drop, gc._silence_bounds(words), rule=rule,
                   emit=lambda *a, **k: None)
    cutlog = gc.build_cutlog(words, drop, gc._silence_bounds(words), rule=rule)
    assert cutlog, "ничего не вырезано — тест проверяет не то"
    allowed = {"decide_markup", "force_takes", "dedupe_repeats", "dedupe_fragments",
               "drop_truncated", "drop_micro_keeps", "silence", "вздох"}
    for c in cutlog:
        assert c["rule"] in allowed, "чужое правило: %r" % c
        assert c["t1"] > c["t0"] and c["text"], "битая запись: %r" % c


def test_cutlog_structure_preserved_except_rule():
    """Структура записей при включённой чистке не меняется: ровно те же поля и
    значения, что писались до задания CA, плюс rule — «совпадает побайтово,
    кроме rule» на уровне записи."""
    words = mk("и список самых опасных и список для прически")
    kept, drop = set(range(len(words))), set()
    rule = {i: "decide_markup" for i in drop}
    gc.postprocess(words, kept, drop, gc._silence_bounds(words), rule=rule,
                   emit=lambda *a, **k: None)
    cutlog = gc.build_cutlog(words, drop, gc._silence_bounds(words), rule=rule)
    assert cutlog
    for c in cutlog:
        assert set(c) == {"t0", "t1", "text", "reason", "source", "rule"}, c
        assert c["reason"] == "слова (GigaAM + 27b)" and c["source"] == "вырезано", c


def test_cutlog_silence_records_get_rule():
    """Тишина тоже несёт rule ('silence') — иначе вопрос «кто виноват» закрыт
    не для всех записей .cuts.json."""
    words = mk("раз", t0=0.0) + [{"w": "два", "start": 2.0, "end": 2.3}]
    sb = gc._silence_bounds(words)
    assert sb, "пауза > 0.8с обязана дать жёсткую границу"
    cutlog = gc.build_cutlog(words, set(), sb, rule={})
    sil = [c for c in cutlog if c["source"] == "тишина"]
    assert sil and all(c["rule"] == "silence" for c in sil)


def test_postprocess_dedupe_off_ne_menyaet_reshenie_modeli():
    """dedupe=False не спорит с решением модели: не режет перечисления и не возвращает оффтопик.

    Ловушки:
    (а) перечисление с повторяющимся началом («не буду и запрещать тоже ничего не буду»)
        — при dedupe=True force_takes ошибочно режет его;
    (б) вырезанный моделью уникальный хвост из >= 5 слов длиной >= 1.5 с
        («по мне видно что прям тут уснул») — при dedupe=True veto_unique_drops
        ошибочно возвращает его.
    При dedupe=False ни kept, ни drop не должны измениться (точное равенство множеств).
    """
    words = mk(
        "не буду и запрещать тоже ничего не буду по мне видно что прям тут уснул",
        dur=0.35, gap=0.1
    )
    # Модель оставила перечисление (0..7) и вырезала оффтопик в конце (8..14)
    model_kept = set(range(8))
    model_drop = set(range(8, len(words)))
    kept = set(model_kept)
    drop = set(model_drop)
    sb = gc._silence_bounds(words)

    gc.postprocess(words, kept, drop, sb, dedupe=False, emit=lambda *a, **k: None)

    assert kept == model_kept, (
        f"kept изменился при dedupe=False: добавлено {kept - model_kept}, "
        f"удалено {model_kept - kept}"
    )
    assert drop == model_drop, (
        f"drop изменился при dedupe=False: удалено {model_drop - drop}, "
        f"добавлено {drop - model_drop}"
    )


