# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
"""Офлайн-замер разбивки хука: разметка ИИ против итога владельца.

Данные владельца лежат вне репозитория и в гит не попадают — путь приходит
аргументом (или берётся из папок выходов спикеров в их профилях).

Как читается клип (все файлы — рядом с XML, один стем):

    <stem>.intro.json  разметка ИИ (её собрал порог ПРЕДЫДУЩЕЙ ревизии)
    <stem>.clip.json   снимок клипа: clip.job.introRows — итог владельца после правок
                       и clip.job.styleKey — стиль клипа (из него ручка длины строки)
    <stem>.words.json  пословный транскрипт на таймлайне

Хук — строки до первой с записью `from` (у акцентов посреди ролика `from` есть,
у строк хука его нет). Слова хука берутся из `.words.json`. Длина строки — ручка
ИНТРО СТИЛЯ клипа (intro_row_max; без стиля — дефолт BASE, 20). `--style NAME`
переопределяет стиль для всех клипов — удобно для сравнения ручек.

ВАРИАНТЫ ЗАМЕРА (--mode):

  * `rows` (по умолчанию) — что реально отдаст новый код: правила применяются к
    СТРОКАМ ИИ, как они пришли от модели. Склейки тут нет нарочно — модель режет
    фразу по смыслу сама, и строка короче ручки не трогается;
  * `glue` — «если бы фразу пересобрали с нуля»: строки ИИ склеиваются обратно в
    фразу внутри каждого прекомпа (граница прекомпа — поле break) и заново
    прогоняются конвейером cmd_intro. Это НИЖНЯЯ оценка: правила умеют только
    резать и подклеивать служебные слова, а короткую строку из длинной фразы не
    делают — сегментацию даёт модель, и без неё владельческие короткие строки
    (2 слова) не воспроизводятся вовсе.

Конвейер обоих вариантов — тот же, что у cmd_intro:
_intro_defunc -> _intro_fix_prefix -> _wrap_intro_rows -> _hook_breaks.

Замер: доля строк владельца, чьи границы (начало-конец по словам) повторились
ровно (и обратная доля — сколько предсказанных строк есть у владельца); число
строк, кончающихся служебным словом (INTRO_FUNC_WORDS).

СЛОВО-ПРИЗЫВ («напишите мне слово «консультация»…», кавычки в последних 15 % слов): в
сводке строка «призыв» — в скольких роликах призыв стал последним акцентом: было (ИИ как
есть) и стало (_place_call_word поверх mid_groups). Нужны тайминги в .words.json
(start/end) и вставки в .inserts.json; без таймингов ролик считается, но не мерится.

Запуск:
    python -X utf8 tools/intro_eval.py <папка> [<папка> ...]
    python -X utf8 tools/intro_eval.py                  # папки выходов из профилей спикеров
    python -X utf8 tools/intro_eval.py <папка> --speaker "Спикер" --mode glue --limit 20
    python -X utf8 tools/intro_eval.py <папка> --style "Узкий" --mode rows
"""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Any, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import speakers        # noqa: E402
from core.aicut import commands  # noqa: E402

Row = dict[str, Any]
Bounds = tuple[int, int]
Word = tuple[int, str, float, float]
MODES = ("rows", "glue")


def load_json(path: str) -> Any:
    """JSON или None: битый файл в замере пропускаем, а не роняем весь прогон."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def hook_rows(rows: Sequence[Any]) -> list[Row]:
    """Строки хука: до первой строки с `from` (дальше идут акценты посреди ролика)."""
    out: list[Row] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("from") is not None:
            break
        out.append(r)
    return out


def word_count(rows: Sequence[Row]) -> int:
    return sum(max(1, int(r.get("count") or 1)) for r in rows)


def bounds(rows: Sequence[Row]) -> list[Bounds]:
    """Границы строк в словах: (первое слово, последнее слово), обе включительно."""
    out: list[Bounds] = []
    k = 0
    for r in rows:
        n = max(1, int(r.get("count") or 1))
        out.append((k, k + n - 1))
        k += n
    return out


def func_ends(rows: Sequence[Row], words: Sequence[str]) -> int:
    """Сколько строк кончается служебным словом (предлог/союз/частица/местоимение)."""
    n = 0
    for _, last in bounds(rows):
        if 0 <= last < len(words) and words[last].upper() in commands.INTRO_FUNC_WORDS:
            n += 1
    return n


def glued_rows(rows: Sequence[Row]) -> list[Row]:
    """Склейка строк ИИ внутри каждого прекомпа обратно в одну фразу.

    Прекомп — от строки с break до следующей с break: владелец держит в нём одну
    фразу, и пересборка с нуля меряется именно по фразе.
    """
    out: list[Row] = []
    for r in rows:
        n = max(1, int(r.get("count") or 1))
        if r.get("break") or not out:
            out.append({"count": n, "color": r.get("color") or "white",
                        "break": True, "back": bool(r.get("back"))})
        else:
            out[-1]["count"] = int(out[-1]["count"]) + n
    return out


def new_rules(rows: Sequence[Row], words: Sequence[Word], row_max: int) -> list[Row]:
    """Прогнать строки через конвейер cmd_intro (в том же порядке). Ручка стиля режет
    только перенос длинных строк (_wrap_intro_rows); склейка служебных слов — порог 14."""
    res = commands._intro_defunc(list(rows), words)
    res = commands._intro_fix_prefix(res, words)
    res = commands._wrap_intro_rows(res, words, row_max)
    if res:
        res = commands._hook_breaks(res, words)
    return res


def matched(owner: Sequence[Bounds], pred: Sequence[Bounds]) -> int:
    """Сколько границ владельца повторилось в предсказании ровно."""
    have = set(pred)
    return sum(1 for b in owner if b in have)


def clip_report(intro_path: str, style_override: str | None = None) -> Row | None:
    """Замер одного клипа по обоим вариантам. None — нет пары сайдкаров или слов."""
    stem = intro_path[: -len(".intro.json")]
    intro = load_json(intro_path)
    clip = load_json(stem + ".clip.json")
    words_raw = load_json(stem + ".words.json")
    if not isinstance(intro, dict) or not isinstance(clip, dict):
        return None
    if not isinstance(words_raw, list) or not words_raw:
        return None
    # В бою cmd_intro получает слова из XML ВЕРХНИМ регистром (служебные списки — тоже):
    # на строчных _intro_defunc не срабатывает и замер врёт. Поэтому приводим здесь.
    words = [str(w.get("w") or "").upper() for w in words_raw if isinstance(w, dict)]
    # Конвейер cmd_intro ждёт ту же ленту, что даёт _words_from_xml: (индекс, слово,
    # начало, конец). Тайминги в разбивке не участвуют (паузы нужны только
    # _hook_split, и там они лишь подсказка), поэтому в замере они нулевые.
    wtuples: list[Word] = [(i, w, 0.0, 0.0) for i, w in enumerate(words)]
    c = clip.get("clip")
    job = (c or {}).get("job") if isinstance(c, dict) else None
    if not isinstance(job, dict):
        return None
    owner = hook_rows(job.get("introRows") or [])
    ai = hook_rows(intro.get("intro_rows") or [])
    if not owner or not ai:
        return None
    # Ручка длины строки — из стиля клипа (как у сборки), --style перекрывает её.
    style = style_override or (job.get("styleKey") or None)
    row_max = commands._intro_row_max(style)
    preds = {
        "old": list(ai),
        "rows": new_rules(ai, wtuples, row_max),
        "glue": new_rules(glued_rows(ai), wtuples, row_max),
    }
    ob = bounds(owner)
    rep: Row = {"clip": os.path.basename(stem), "owner_lines": len(ob),
                "owner_func": func_ends(owner, words), "owner_words": word_count(owner),
                "ai_words": word_count(ai), "row_max": row_max,
                # владелец правил этот хук: его границы не совпали с границами ИИ как есть
                "edited": ob != bounds(ai)}
    for key, pred in preds.items():
        pb = bounds(pred)
        rep[key + "_lines"] = len(pb)
        rep[key + "_hit"] = matched(ob, pb)
        rep[key + "_rows_hit"] = matched(pb, ob)
        rep[key + "_func"] = func_ends(pred, words)
    return rep


def _ends_last(mids: Sequence[Row], a: int, b: int) -> bool:
    """Последняя группа акцентов покрывает призыв (слова a..b) целиком."""
    groups = commands._mid_groups(list(mids))
    if not groups:
        return False
    _, _, f, cnt = groups[-1]
    return f <= a and f + cnt - 1 >= b


def timed_words(words_raw: Sequence[Any]) -> list[Word] | None:
    """Лента с настоящими таймингами (start/end, секунды) или None, если их в сайдкаре нет:
    без них зазоры и вставки не посчитать, и измерять призыв нечем."""
    out: list[Word] = []
    for w in words_raw:
        s = w.get("start")
        e = w.get("end")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            return None
        out.append((len(out), str(w.get("w") or "").upper(), float(s), float(e)))
    return out or None


def call_report(intro_path: str) -> Row | None:
    """Слово-призыв в одном ролике (только .intro.json, .words.json и .inserts.json рядом).

    call — в хвосте ролика есть кавычки с призывом; call_timed — есть тайминги, значит
    замер честный; call_before — призыв был последним акцентом в ответе ИИ как есть;
    call_after — стал бы после новых правил (_place_call_word). None — нет файлов."""
    stem = intro_path[: -len(".intro.json")]
    intro = load_json(intro_path)
    words_raw = load_json(stem + ".words.json")
    if not isinstance(intro, dict) or not isinstance(words_raw, list):
        return None
    dicts = [w for w in words_raw if isinstance(w, dict)]
    rep: Row = {"clip": os.path.basename(stem), "call": False, "call_timed": False,
                "call_before": False, "call_after": False}
    span = commands._call_span([(i, str(w.get("w") or "").upper(), 0.0, 0.0)
                                for i, w in enumerate(dicts)])
    if span is None:
        return rep
    rep["call"] = True
    timed = timed_words(dicts)
    if timed is None:
        return rep
    rep["call_timed"] = True
    a, b = span
    mids = [r for r in (intro.get("mid_groups") or []) if isinstance(r, dict)]
    intro_len = word_count(hook_rows(intro.get("intro_rows") or []))
    ins = load_json(stem + ".inserts.json")
    inserts = (ins.get("inserts") or []) if isinstance(ins, dict) else []
    busy = commands._busy_windows(inserts)
    rep["call_before"] = _ends_last(mids, a, b)
    after = commands._place_call_word(list(mids), timed, intro_len, busy,
                                      emit=lambda *x, **k: None)
    rep["call_after"] = _ends_last(after, a, b)
    return rep


def call_summary(calls: Sequence[Row]) -> None:
    """Слово-призыв: в скольких роликах с кавычками в хвосте он стал последним акцентом."""
    print("-" * 100)
    tail = [r for r in calls if r.get("call")]
    if not tail:
        print("призыв: кавычек в хвосте ролика нет ни в одном .intro.json — мерить нечего")
        return
    timed = [r for r in tail if r.get("call_timed")]
    if not timed:
        print(f"призыв: роликов с кавычками в хвосте {len(tail)}, но таймингов в .words.json нет — "
              "замер не сделан")
        return
    was = sum(1 for r in timed if r.get("call_before"))
    now = sum(1 for r in timed if r.get("call_after"))
    print(f"призыв: роликов с кавычками в хвосте {len(tail)} (замерено с таймингами {len(timed)}) · "
          f"призыв последним акцентом: было (ИИ как есть) {was}, стало (новые правила) {now}")

def dirs_from_profiles(only: str | None = None) -> list[str]:
    """Папки выходов спикеров из их профилей — в порядке ключей, без повторов."""
    out: list[str] = []
    for key, prof in sorted(speakers.all_speakers().items()):
        if only and only not in (key, prof.get("label")):
            continue
        for field in ("outdir", "jsxdir"):
            d = str(prof.get(field) or "").strip()
            if d and os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def collect(paths: Sequence[str]) -> list[str]:
    """Все .intro.json под папками, по алфавиту (порядок прогона не должен плавать)."""
    out: list[str] = []
    for p in paths:
        out += glob.glob(os.path.join(p, "**", "*.intro.json"), recursive=True)
    return sorted(set(out))


def print_table(reports: Sequence[Row], mode: str) -> None:
    """Таблица по клипам: владелец, строки ИИ и выбранный вариант новых правил."""
    print("%-30s %6s %6s %6s %6s %10s %10s %8s %8s" % (
        "клип", "ручка", "влад", "стар", "нов", "границ стар", "границ нов", "функ ст", "функ нв"))
    for r in reports:
        print("%-30s %6d %6d %6d %6d %10d %10d %8d %8d" % (
            str(r["clip"])[:30], int(r["row_max"]), int(r["owner_lines"]), int(r["old_lines"]),
            int(r[mode + "_lines"]), int(r["old_hit"]), int(r[mode + "_hit"]),
            int(r["old_func"]), int(r[mode + "_func"])))


def summary(reports: Sequence[Row]) -> None:
    """Итог двумя срезами: все хуки и только те, что владелец правил (границы его итога
    не совпали с границами ИИ). Правленые хуки — самый честный срез: на неправленых
    совпадение ничего не говорит о правилах."""
    print("-" * 100)
    _summary_block("все хуки", reports)
    edited = [r for r in reports if r.get("edited")]
    print("-" * 100)
    _summary_block("только правленые владельцем хуки", edited)


def _summary_block(title: str, reports: Sequence[Row]) -> None:
    """Одна сводка по набору клипов: строки владельца, и по каждому варианту правил."""
    owner = sum(int(r["owner_lines"]) for r in reports)
    if not owner:
        print("%s: нет строк владельца — мерить нечего" % title)
        return
    print("%s · клипов %d · строк владельца %d (слов %d, у ИИ %d) · владелец кончает "
          "служебным словом %d (%.0f%%)"
          % (title, len(reports), owner, sum(int(r["owner_words"]) for r in reports),
             sum(int(r["ai_words"]) for r in reports),
             sum(int(r["owner_func"]) for r in reports),
             100.0 * sum(int(r["owner_func"]) for r in reports) / owner))
    for tag, key in (("старые (строки ИИ как есть)", "old"),
                     ("новые, по строкам ИИ (--mode rows)", "rows"),
                     ("новые, склейка+правила (--mode glue)", "glue")):
        lines = sum(int(r[key + "_lines"]) for r in reports) or 1
        hit = sum(int(r[key + "_hit"]) for r in reports)
        back = sum(int(r[key + "_rows_hit"]) for r in reports)
        func = sum(int(r[key + "_func"]) for r in reports)
        print("%-38s строк %4d · границы владельца %3d из %d (%.0f%%) · "
              "свои строки у владельца %3d (%.0f%%) · служебных в конце %3d (%.0f%%)"
              % (tag, lines, hit, owner, 100.0 * hit / owner, back,
                 100.0 * back / lines, func, 100.0 * func / lines))


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only: str | None = None
    style: str | None = None
    limit = 0
    mode = "rows"
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--speaker":
            i += 1
            only = args[i] if i < len(args) else None
        elif a == "--style":
            i += 1
            style = args[i] if i < len(args) else None
        elif a == "--limit":
            i += 1
            limit = int(args[i]) if i < len(args) else 0
        elif a == "--mode":
            i += 1
            mode = args[i] if i < len(args) else "rows"
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            paths.append(a)
        i += 1
    if mode not in MODES:
        print("неизвестный --mode: %s (есть %s)" % (mode, ", ".join(MODES)))
        return 2
    if not paths:
        paths = dirs_from_profiles(only)
    if not paths:
        print("не нашлось ни одной папки: дай путь аргументом или заведи outdir у спикера")
        return 1
    files = collect(paths)
    if not files:
        print("под папками нет ни одного .intro.json: " + ", ".join(paths))
        return 1
    reports: list[Row] = []
    skipped = 0
    for f in files:
        rep = clip_report(f, style)
        if rep is None:
            skipped += 1
            continue
        reports.append(rep)
        if limit and len(reports) >= limit:
            break
    if not reports:
        print("нет клипов с парой .intro.json/.clip.json/.words.json "
              "(проверено %d .intro.json)" % len(files))
        return 1
    print("папок %d · .intro.json %d · в замер попало %d (без пары или без слов %d) · "
          "режим %s" % (len(paths), len(files), len(reports), skipped, mode))
    print_table(reports, mode)
    summary(reports)
    call_summary([r for r in (call_report(f) for f in files) if r is not None])
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
