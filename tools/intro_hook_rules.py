# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Снять правила ХУКА интро с ручных .jsx (задание BF, 2026-08-14).

Данные: `AutoCut_out/*.jsx` — INTRO_GROUPS (прекомпы; строка = {words, color, times}).
В .jsx лежат только СТАРТОВЫЕ тайминги слов, поэтому длительности прекомпов и разрывы
считаются по стартам слов, а не по концам волны.

Хук отделяется от акцентов правилом «прекомпы подряд с начала ролика: разрыв до
предыдущего < 1.5 с и старт < 25 с».

Запуск: python -X utf8 tools/intro_hook_rules.py
"""
import glob
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
# Папка с ручными .jsx лежит РЯДОМ с репозиторием, а не внутри. Абсолютный путь тут был
# зашит константой — вместе с именем пользователя и его рабочей папкой; сторож
# tests/test_public_clean.py такое больше не пропускает. Переопределяется REELSI_JSX_DIR.
JSX_DIR = os.environ.get("REELSI_JSX_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "AutoCut_out")

HOOK_GAP = 1.5    # разрыв до предыдущего прекомпа, с
HOOK_START = 25   # старт прекомпа, с
PAUSE = 0.4       # порог паузы из BF


def intro_groups_from_jsx(jsx_path):
    raw = open(jsx_path, encoding="utf-8").read()
    m = re.search(r"var INTRO_GROUPS=(\[.*?\]);", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def med(vals):
    v = sorted(x for x in vals if x is not None)
    return v[len(v) // 2] if v else None


def pct(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return v[min(len(v) - 1, int(len(v) * p))]


def _split_hook(groups):
    """Группы -> (хук, акценты). Хук — прекомпы подряд с начала ролика, разрыв до
    предыдущего < HOOK_GAP с и старт < HOOK_START с; дальше всё — акценты."""
    hook, accents = [], []
    prev_end = None
    for g in sorted(groups, key=lambda x: (x[0]["times"] or [0])[0] if x else 1e9):
        if not g:
            continue                                 # пустой прекомп — артефакт сборки
        start = (g[0]["times"] or [0])[0]
        last = g[-1]["times"][-1] if g[-1].get("times") else start
        if prev_end is not None and (start - prev_end >= HOOK_GAP or start >= HOOK_START):
            accents.append(g)
            continue
        hook.append(g)
        prev_end = last
    return hook, accents


def main():
    total = {"hook": {"rows": 0, "words": [], "chars": [], "precomp_rows": [],
                      "precomp_words": [], "durs": []},
             "accent": {"rows": 0, "words": [], "chars": [], "precomp_rows": []}}
    hook_per_video = []
    gap_bound, gap_inside = [], []
    n_jsx = 0
    for f in sorted(glob.glob(os.path.join(JSX_DIR, "*.jsx"))):
        groups = intro_groups_from_jsx(f)
        if not groups:
            continue
        n_jsx += 1
        hook, accents = _split_hook(groups)
        hook_per_video.append(len(hook))
        for kind, gs in (("hook", hook), ("accent", accents)):
            for g in gs:
                starts = [t for ln in g for t in ln.get("times") or []]
                words = sum(len(ln.get("words") or []) for ln in g)
                total[kind]["precomp_rows"].append(len(g))
                if kind == "hook":
                    total[kind]["precomp_words"].append(words)
                    total[kind]["durs"].append(starts[-1] - starts[0])
                for ln in g:
                    ws = ln.get("words") or []
                    total[kind]["rows"] += 1
                    total[kind]["words"].append(len(ws))
                    total[kind]["chars"].append(len(" ".join(ws)))
                for k in range(1, len(g)):           # разрыв ВНУТРИ прекомпа (между строками)
                    a = (g[k - 1]["times"] or [0])[-1]
                    b = (g[k]["times"] or [0])[0]
                    if kind == "hook":
                        gap_inside.append(b - a)
        for k in range(1, len(hook)):                # разрыв НА ГРАНИЦЕ прекомпов хука
            a = hook[k - 1][-1]["times"][-1]
            b = hook[k][0]["times"][0]
            gap_bound.append(b - a)

    # ================= вывод =================
    print(f".jsx: {n_jsx} · хук-прекомпов: {sum(hook_per_video)} · строк хука: {total['hook']['rows']} "
          f"· прекомпов акцентов: {len(total['accent']['precomp_rows'])} "
          f"· строк акцентов: {total['accent']['rows']}")
    print()
    for kind, label in (("hook", "ХУК (начало ролика)"), ("accent", "АКЦЕНТЫ по середине")):
        t = total[kind]
        w = Counter(t["words"])
        c = t["chars"]
        pr = Counter(t["precomp_rows"])
        print(f"== {label} ==")
        print("слов в строке:", dict(sorted(w.items())))
        if c:
            print("символов в строке: медиана {} · p75 {} · p90 {} · max {}".format(
                med(c), pct(c, .75), pct(c, .9), max(c)))
        print("строк в прекомпе:", dict(sorted(pr.items())))
        if kind == "hook":
            pw = t["precomp_words"]
            du = t["durs"]
            print("слов в прекомпе: медиана {} · p25 {} · p90 {} · max {}".format(
                med(pw), pct(pw, .25), pct(pw, .9), max(pw)))
            print("прекомпов в хуке (на ролик):", dict(sorted(Counter(hook_per_video).items())))
            print("длительность прекомпа, с (по стартам): медиана {:.2f} · p90 {:.2f}".format(
                med(du), pct(du, .9)))
    print()
    print("== пауза границу прекомпа НЕ объясняет (хук) ==")
    print("разрыв НА границе, с: медиана {:.2f} · p90 {:.2f} · доля >= 0.4с: {:.0f}%".format(
        med(gap_bound), pct(gap_bound, .9), 100 * sum(1 for x in gap_bound if x >= PAUSE) / max(1, len(gap_bound))))
    print("разрыв ВНУТРИ прекомпа, с: медиана {:.2f} · p90 {:.2f} · доля >= 0.4с: {:.0f}%".format(
        med(gap_inside), pct(gap_inside, .9), 100 * sum(1 for x in gap_inside if x >= PAUSE) / max(1, len(gap_inside))))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
