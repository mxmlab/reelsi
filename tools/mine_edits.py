# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
"""Майнинг ручных правок: разница `<стем>.xml` и `<стем>.xml.bak` — это всё, что
пользователь исправил руками. Скрипт собирает замены по словам и раскладывает их на
две рабочие кучи (термины и плохие слова) плюс справочную (числа/грамматика — только
счётчик, решено с пользователем их не автоматизировать).

    python reelsi/tools/mine_edits.py                 # отчёт (по умолчанию)
    python reelsi/tools/mine_edits.py --apply         # внести термины (плохие слова — нет)
    python reelsi/tools/mine_edits.py --apply --all   # + однократные в общую кучу отчёта

Вносит ОДИН файл: `terms.json` (термины + варианты ослышек, снятие спорных вариантов),
и только с явным `--apply`.

**Плохие слова не вносятся никогда** — ни с `--apply`, ни с `--all`, они остаются в
отчёте предложением. Прогон, который их вносил, засыпал `badwords.user.txt` мусором
выравнивания диффа («и», «из», «жизни»), а `censor` сверяет по ПОДСТРОКЕ: одна основа
«и» зацензурила 106 слов из 203 в клипе. Список правится только руками (⚙ → «Слова»).

Классификация замены (старое -> новое):

- плохое слово = ЛЕВАЯ часть, если в правой появилась `*`, а в левой её не было.
  Слово, которое пользователь закрыл звёздочкой вручную, — кандидат в цензуру, но
  только кандидат: в отчёт, не в список. Раскладка кандидатов по кучам:
    1) длина основы >= 4 букв (короче 4 — мусор выравнивания «и», «из», «пк», в отчёте
       отдельно «слишком короткие»);
    2) встречаемость >= 2 раз (однократные — в отчёте отдельно «однократные», с `--all`
       попадают в общую кучу);
    3) стоп-лист из `okwords.txt`: слова из белого списка в плохие не предлагаются;
- термин = правая часть (то, на что исправил), варианты = левые части. Термин — это
  название, которого ASR не знает: латиница/цифры («MOTS-C», «TB500») или короткая
  аббревиатура без гласных («ЛПНП»). Вариант прогоняется через защиту `terms.learn`
  (транслит чужого термина) — спорный в словарь не идёт, попадает в отчёт;
- числа и грамматика — только счётчик в отчёте, ничего не предлагается.
"""
import argparse
import glob
import os
import re
import sys
from collections import OrderedDict
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import terms  # noqa: E402
from core import censor  # noqa: E402

MAX_NGRAM = terms.MAX_NGRAM          # цепочку длиннее склейкой не предлагаем
_VOWELS = set("аеёиоуыэюя")
_SKIP_DIRS = {"reelsi", "exp", "plans", "chell2", "assets", "music",
              "insert_library", "roto", "Adobe After Effects Auto-Save",
              "Adobe Premiere Pro Auto-Save", "Adobe Premiere Pro Audio Previews"}


def find_pairs(root):
    """Все пары `<стем>.xml` / `<стем>.xml.bak` в папках результата уровнем ниже root.
    Папка считается папкой результата, если в ней есть хоть один `*.xml.bak` — так
    не нужно держать список имён, который неизбежно отстанет от реальности."""
    pairs = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return pairs
    for name in entries:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder) or name in _SKIP_DIRS or name.startswith("."):
            continue
        baks = sorted(glob.glob(os.path.join(folder, "*.xml.bak")))
        for bak in baks:
            xml = bak[:-4]                       # "<стем>.xml.bak" -> "<стем>.xml"
            if os.path.isfile(xml):
                pairs.append((xml, bak))
    return pairs


def words_of(xml_path):
    """Список слов-субтитров в порядке таймлайна (то же, что aicut.commands)."""
    from core import xml2ae
    _, _, subs, _ = xml2ae.parse_full(xml_path)
    return [w for _, _, w in subs]


def classify(old_txt, new_txt):
    """Вид замены: bad (цензура звёздочкой), term (название), num/grammar.
    Термин решает НОВАЯ (правая) часть — «термин = то, на что исправил». Старая часть
    может быть аббревиатурой, но если исправили её в обычное слово — это не термин
    («СП» -> «-» — просто правка, а не название)."""
    if "*" in new_txt and "*" not in old_txt:
        return "bad"
    if re.search(r"[a-zA-Z]", new_txt):
        return "term"                            # латиница — ASR её не знает
    if re.search(r"[0-9]", old_txt) or re.search(r"[0-9]", new_txt):
        return "num"                             # цифры и диапазоны
    if _is_abbrev(new_txt):
        return "term"                            # «ЛПНП» — кириллическая аббревиатура
    return "grammar"


def _is_abbrev(s):
    """Короткая аббревиатура без гласных (ЛП, ЛПНП, ZPHС) — это название, а не слово."""
    s = re.sub(r"[^0-9a-zа-яё]", "", s.lower())
    return 2 <= len(s) <= 6 and not any(c in _VOWELS for c in s)


def collect(root):
    """(пары, замены[{old, new, clip, kind}], ошибки чтения)."""
    pairs = find_pairs(root)
    edits, errs = [], []
    for xml, bak in pairs:
        try:
            old = words_of(bak)
            new = words_of(xml)
        except Exception as e:
            errs.append((os.path.basename(xml), f"{type(e).__name__}: {e}"))
            continue
        sm = SequenceMatcher(None, old, new)
        clip = os.path.basename(xml)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "replace":
                continue
            op, np = old[i1:i2], new[j1:j2]
            edits.append(dict(old=op, new=np, clip=clip,
                              kind=classify(" ".join(op), " ".join(np))))
    return pairs, edits, errs


def _unq(items):
    """Дубликаты в списке роликов не нужны (но счёт = числу вхождений)."""
    return sorted(set(items))


def build_report(pairs, edits, errs, terms_data, apply=False, ok_stems=None, all_bad=False):
    """Раскладываем замены по кучам и собираем отчёт (и правки для --apply)."""
    counts = {"term": 0, "bad": 0, "num": 0, "grammar": 0}
    if ok_stems is None:
        ok_stems = censor._ok()
    # термины: {термин: {вариант: [ролики]}}
    terms_prop = OrderedDict()
    # плохие слова: {стем: [ролики]}
    bad_raw = OrderedDict()
    for e in edits:
        counts[e["kind"]] += 1
        if e["kind"] == "bad":
            for w in e["old"]:
                stem = re.sub(r"[^0-9a-zа-яё]", "", (w or "").lower())
                if not stem or "*" in stem:
                    continue
                # Стоп-лист обычных слов из okwords.txt
                if any(ok in stem for ok in ok_stems):
                    continue
                bad_raw.setdefault(stem, []).append(e["clip"])
        elif e["kind"] == "term":
            term = " ".join(e["new"])
            # только ОДНОСЛОВНЫЕ правые части: многословные («ОДОБРЕНИЕ FDA», «A BPC-157»)
            # — это мусор выравнивания диффа, а не название, которое ASR ослышался
            if " " in term or len(e["new"]) > MAX_NGRAM or \
                    not re.search(r"[0-9a-zа-яё]", term, re.IGNORECASE):
                continue
            for w in e["old"]:
                v = re.sub(r"\s+", " ", (w or "").strip())
                if not v or not re.search(r"[0-9a-zа-яё]", v, re.IGNORECASE):
                    continue
                t = terms_prop.setdefault(term, OrderedDict())
                t.setdefault(v, []).append(e["clip"])

    # Распределение плохих слов по трём категориям:
    # 1. Слишком короткие (len < 4) — только отдельным блоком, никогда не вносятся автоматически
    # 2. Однократные (len >= 4, 1 вхождение) — в отчёт отдельным блоком
    # 3. Повторяющиеся (len >= 4, >= 2 вхождений) — самые вероятные кандидаты
    bad_ready = OrderedDict()
    bad_single = OrderedDict()
    bad_short = OrderedDict()
    for stem, clips in bad_raw.items():
        if len(stem) < 4:
            bad_short[stem] = clips
        elif len(clips) == 1:
            bad_single[stem] = clips
        else:
            bad_ready[stem] = clips

    # ---- два прохода: сначала принять предложенные термины, потом снять спорные
    # варианты у существующих (порядок важен: «модси» у ДСИП спорен ровно потому, что
    # в словарь входит MOTS-C — без прохода по принятым термин не снять бы) ----
    existing = [dict(it, variants=list(it.get("variants") or []))
                for it in terms_data["terms"]]
    # снимок вариантов ДО прогона: чистка ниже трогает только их
    existing_vars = [set(it["variants"]) for it in existing]
    # финальное состояние словаря: существующие термины + принятые предложенные.
    # Ключ — СЛИТНЫЙ: «TB500» и «TB-500» — один препарат, иначе вариант «TV50»
    # упрётся в чужую запись того же названия и уйдёт в «спорно» зря.
    final = OrderedDict()
    for it in existing:
        final[terms._norm_tight(it["term"])] = it

    def _other_collision(nv, own_term, universe):
        """На какой чужой термин ТОЧНО ложится ослышка (None = ни на какой).

        Здесь источник — РУЧНАЯ правка пользователя: он сам заменил это слово на этот
        термин, и его решение сильнее любой похожести. Поэтому блокируем только точное
        совпадение с именем чужого термина или с его вариантом — вот это и правда
        сломало бы чужой термин. Похожесть с транслитом (`terms._collides`) остаётся в
        `learn()`, где источник — догадка: там «модси» рядом с «ДСИП» опасен. А тут он
        ровно то, о чём просили: пользователь пять раз руками чинил «МОДСИ» на «MOTS-C»,
        и защита не давала это запомнить (жалоба 2026-08-19).
        own_term исключаем по слитному ключу: «MOT» и «MOTS-C» — родные, не коллизия."""
        own = terms._norm_tight(own_term)
        for k, it in universe.items():
            if k == own:
                continue
            if nv == terms._norm(it["term"]):
                return it["term"]
            if any(nv == terms._norm(v) for v in it["variants"]):
                return it["term"]
        return None

    def _other_similar(nv, own_term, universe):
        """На какой чужой термин ослышка ПОХОЖА — только чтобы предупредить в отчёте."""
        own = terms._norm_tight(own_term)
        for k, it in universe.items():
            if k != own and terms._collides(nv, it):
                return it["term"]
        return None

    accepted = OrderedDict()                      # термины, принятые в словарь (новые)
    disputed = []                                 # (термин, вариант, с чем ТОЧНО совпал)
    warned = []                                   # (термин, вариант, на что похож) — внесён
    for term, variants in terms_prop.items():
        tk = terms._norm_tight(term)
        if tk in final:
            tgt = final[tk]                       # мержим в существующий термин
        else:
            tgt = {"term": term, "variants": []}
            final[tk] = tgt                       # виден остальным проверкам сразу
            accepted[tk] = tgt
        known = {terms._norm(x) for x in tgt["variants"]}
        for v, clips in variants.items():
            nv = terms._norm(v)
            if nv in known:
                continue
            hit = _other_collision(nv, term, final)
            if hit is not None:
                disputed.append((term, v, hit))
                continue
            tgt["variants"].append(v)
            near = _other_similar(nv, term, final)
            if near is not None:
                warned.append((term, v, near))
    # термины, у которых НЕ прошёл ни один вариант, в словарь не попадают вовсе
    # (термин без варианта и без похожести ничего не чинит): «ZPHС» с одним спорным
    # вариантом остаётся только в отчёте, на усмотрение пользователя
    for tk, tgt in list(final.items()):
        if tk in accepted and not tgt["variants"]:
            del final[tk]
            del accepted[tk]
    # Чистка СТАРЫХ вариантов — по ПОХОЖЕСТИ, а не по точному совпадению: ровно ради
    # ради этого всё и затевалось («модси» лежал вариантом ДСИП и подменял слово).
    # Правило тут другое, чем при добавлении, и это намеренно: добавляем по решению
    # человека (точное совпадение — единственный стоп), чистим по подозрению.
    # Смотрим только на варианты, которые лежали в словаре ДО этого прогона: внесённые
    # сейчас — его же решение, снимать их обратно тем же проходом нельзя.
    removed = []                                  # (термин, вариант, на что похоже)
    for it, before in zip(existing, existing_vars):
        for v in list(it.get("variants") or []):
            if v not in before:
                continue
            nv = terms._norm(v)
            hit = _other_similar(nv, it["term"], final)
            if hit is not None:
                it["variants"].remove(v)
                removed.append((it["term"], v, hit))

    return dict(counts=counts, terms_prop=terms_prop, bad_prop=bad_ready,
                bad_ready=bad_ready, bad_single=bad_single, bad_short=bad_short,
                bad_raw=bad_raw,
                final=final, accepted=accepted, disputed=disputed, warned=warned, removed=removed,
                errors=errs, pairs=pairs)


def print_report(root, apply=False, all_bad=False, terms_path=None, badwords_path=None, okwords_path=None):
    # переопределения пути ДО чтения словаря: прогон «на копии» должен и проверять
    # коллизии по копии, а не по боевому terms.json
    if terms_path:
        terms.TERMS_PATH = terms_path
        terms._CACHE = {"mtime": -1, "data": None}
    if badwords_path:
        censor.USER_PATHS = dict(censor.USER_PATHS, bad=badwords_path)
        censor._cache = dict(censor._cache)
        censor._cache["bad"] = (None, None, censor.DEFAULT_BAD)
    if okwords_path:
        censor.USER_PATHS = dict(censor.USER_PATHS, ok=okwords_path)
        censor._cache = dict(censor._cache)
        censor._cache["ok"] = (None, None, censor.DEFAULT_OK)

    pairs, edits, errs = collect(root)
    terms_data = terms.load()
    rep = build_report(pairs, edits, errs, terms_data, apply=apply, all_bad=all_bad)

    n_bad_unq = len(rep["bad_ready"]) + len(rep["bad_single"]) + len(rep["bad_short"])
    n_num = rep["counts"]["num"] + rep["counts"]["grammar"]
    print(f"пар xml/xml.bak: {len(rep['pairs'])}")
    print(f"замен всего: {len(edits)}  "
          f"(термины {rep['counts']['term']}, звёздочки {rep['counts']['bad']} "
          f"({n_bad_unq} уникальных), числа+грамматика {n_num})")
    if rep["errors"]:
        print(f"\nне прочитались ({len(rep['errors'])}):")
        for name, err in rep["errors"]:
            print(f"  {name}: {err}")
    print("\n=== термины (предлагается) ===")
    for term, variants in rep["terms_prop"].items():
        print(f"{term}")
        for v, clips in variants.items():
            print(f"  <- {v} ({len(clips)}: {', '.join(_unq(clips)[:4])})")
    if rep["disputed"]:
        print("\n=== не внесено (вариант СОВПАДАЕТ с другим термином) ===")
        for term, v, hit in rep["disputed"]:
            print(f"  {term}: «{v}» — это уже «{hit}», внести нельзя")
    if rep["warned"]:
        print("\n=== внесено, хотя похоже на другой термин (глянь глазами) ===")
        for term, v, hit in rep["warned"]:
            print(f"  {term}: «{v}» похоже на «{hit}»")
    if rep["removed"]:
        print("\n=== снято (варианты, похожие на другой термин) ===")
        for term, v, hit in rep["removed"]:
            print(f"  {term}: «{v}» похоже на «{hit}»")

    print("\n=== плохие слова (кандидаты — вносить руками) ===")
    for stem, clips in rep["bad_ready"].items():
        print(f"{stem} ({len(clips)}: {', '.join(_unq(clips)[:4])})")

    if rep["bad_single"]:
        print("\n=== плохие слова (однократные) ===")
        for stem, clips in rep["bad_single"].items():
            print(f"{stem} ({len(clips)}: {', '.join(_unq(clips)[:4])})")

    if rep["bad_short"]:
        print("\n=== плохие слова (слишком короткие — реши сам) ===")
        for stem, clips in rep["bad_short"].items():
            print(f"{stem} ({len(clips)}: {', '.join(_unq(clips)[:4])})")

    if not apply:
        return 0

    # ---- применение: ТОЛЬКО terms.json ----
    out_terms = list(rep["final"].values())
    for it in out_terms:
        it["variants"] = it["variants"][:terms.MAX_VARIANTS]
    n_new = len(rep["accepted"])
    terms.save({"terms": out_terms})
    for it in out_terms:
        if terms._norm_tight(it["term"]) in rep["accepted"]:
            print(f"+ термин «{it['term']}» с {len(it['variants'])} вариантами")

    # Плохие слова НЕ вносятся — ни с --apply, ни с --all. Один такой прогон засыпал
    # badwords.user.txt мусором выравнивания диффа («и», «из», «жизни»), а сверка идёт по
    # ПОДСТРОКЕ: основа «и» зацензурила 106 слов из 203 в клипе. Слишком дёшево ломается,
    # чтобы список пополнялся сам — отчёт выше показывает кандидатов, решение и правка
    # руками (⚙ → «Слова»).
    n_bad = len(rep["bad_ready"]) + (len(rep["bad_single"]) if all_bad else 0)
    if n_bad:
        print("")
        print(f"плохие слова НЕ вносятся автоматически ({n_bad} кандидат(ов) выше) — "
              f"нужные перенеси руками в ⚙ → «Слова»")
    print(f"внесено: новых терминов {n_new}, не внесено {len(rep['disputed'])}, "
          f"внесено с предупреждением {len(rep['warned'])}, снято вариантов {len(rep['removed'])}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="внести термины в terms.json (плохие слова НЕ вносятся)")
    ap.add_argument("--report", action="store_true",
                    help="только отчёт (по умолчанию)")
    ap.add_argument("--all", action="store_true",
                    help="считать однократные слова такими же кандидатами")
    ap.add_argument("--root", default=None,
                    help="рабочая папка с папками результата (по умолчанию — уровнем выше)")
    ap.add_argument("--terms", default=None, help="файл словаря терминов (копия для пробы)")
    ap.add_argument("--badwords", default=None, help="файл плохих слов (копия для пробы)")
    ap.add_argument("--okwords", default=None, help="файл белого списка (копия для пробы)")
    a = ap.parse_args(argv)
    root = a.root or os.path.dirname(ROOT)
    return print_report(root, apply=a.apply, all_bad=a.all, terms_path=a.terms,
                        badwords_path=a.badwords, okwords_path=a.okwords)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
