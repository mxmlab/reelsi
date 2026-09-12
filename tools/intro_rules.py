# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Снять правила интро с ручной работы пользователя (задание Z, 2026-08-13).

Данные: `MKAutoCut_out/*.intro.json` (36 роликов спикера A) + рядом `.xml` со словами
и таймингами; где есть собранный `.jsx` того же клипа — сверка с ним (INTRO_GROUPS).

Схема intro.json: `intro_rows` — строки первых слов (`count` = слов в строке),
`mid_groups` — акценты (`from` = индекс слова-якоря, `count`, `color`, `break` =
начало нового прекомпа). По словам пользователя это в основном его ручная разметка.

Что считаем:
  1. строки интро: число слов и ШИРИНУ при кегле 140 метриками шрифта
     (SFPro-CondensedSemibold из стиля; в системе нет — SFProDisplay-Semibold);
  2. прекомпы (по break): размер в строках, длительность, пауза перед первым словом,
     смена камеры — что отличает break от не-break;
  3. сверка с .jsx: число прекомпов и структура строк.

Запуск: python -X utf8 tools/intro_rules.py
"""
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import app_meta
from core import xml2ae

HERE = os.path.dirname(os.path.abspath(__file__))
# Пути НЕ захардкожены: базовую папку (родителя репозитория) задаёт REELSI_BASE,
# как у остальных скриптов (app_meta.env, см. reelsi.py DEFAULT_BASE). На чужой
# машине пути всё равно свои, а клона с локальными данными не бывает.
BASE = app_meta.env("BASE") or os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(BASE, "MKAutoCut_out")
JSX_DIRS = [os.path.join(BASE, "AutoCut_out"), BASE]

FONT_FILE = r"C:\WINDOWS\Fonts\SFProDisplay-Semibold.ttf"
INTRO_PX = 140          # кегль интро в AE (INTRO_SPEC.md)
IW = 1080               # ширина кадра-вертикали
FIT_W = 0.92            # INTRO_FIT_W


def _text_width(tt, text):
    upem = tt["head"].unitsPerEm
    cmap = tt.getBestCmap()
    hmtx = tt["hmtx"]
    sp = cmap.get(0x20, 3)
    units = 0
    for ch in text:
        g = cmap.get(ord(ch))
        units += hmtx[g][0] if g else hmtx[sp][0]
    return units / upem * INTRO_PX


def camera_at(cam_tracks, frame):
    for ci, tr in enumerate(cam_tracks):
        for cl in tr["clips"]:
            if cl[0] <= frame < cl[1]:
                return ci
    return -1


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


def split_mid_groups_precomps(mids, subs=()):
    cur = []
    all_pc = []          # прекомпы этого ролика: (fi, last_i, n_lines)
    n_null = 0
    for g in mids + [{"break": True}]:          # фиктивный break в конце закрывает последний
        if g.get("break") and cur:
            fi = next((x["from"] for x in cur if x.get("from") is not None), None)
            li = next((x["from"] for x in reversed(cur) if x.get("from") is not None), None)
            if fi is not None and li is not None and 0 <= fi <= li < len(subs):
                for x in reversed(cur):
                    if x.get("from") == li:
                        li = min(li + max(0, int(x.get("count") or 1)) - 1, len(subs) - 1)
                        break
                all_pc.append((fi, li, len(cur)))
            cur = []
        if not (mids and g is mids[-1] and g.get("break")):
            if g.get("from") is None:
                n_null += 1
            cur.append(g)
    return all_pc, n_null


def main():
    from fontTools.ttLib import TTFont
    tt = TTFont(FONT_FILE, lazy=True)

    all_lines = []        # все строки интро: {n, pct, kind}
    precomps = []         # прекомпы из intro.json: {n_lines, glen, pause, cam}
    gaps = []             # разрыв конец прекомпа -> начало следующего, сек
    nulls_all = []        # акцентов без слова-якоря (from=null) на ролик
    jsx_precomps = []     # прекомпы из .jsx: {n_lines, words_per_line}
    jsx_stats = {"found": 0, "match": 0, "no_jsx": 0, "rows": []}

    for f in sorted(glob.glob(os.path.join(OUT_DIR, "*.intro.json"))):
        stem = os.path.basename(f).replace(".intro.json", "")
        xml_path = os.path.join(OUT_DIR, stem + ".xml")
        if not os.path.isfile(xml_path):
            continue
        try:
            meta, cam_tracks, subs, _ = xml2ae.parse_full(xml_path)
        except Exception:
            continue
        fps = meta["fps"]
        d = json.load(open(f, encoding="utf-8"))
        words = [w for (_, _, w) in subs]

        # ---- 1. строки интро: intro_rows (хук) + строки акцентов ----
        off = 0
        for r in d.get("intro_rows", []):
            n = max(0, int(r.get("count") or 0))
            text = " ".join(words[off:off + n]); off += n
            all_lines.append({"n": n, "pct": round(_text_width(tt, text) / IW * 100, 1),
                              "kind": "hook"})
        for g in d.get("mid_groups", []):
            fi = g.get("from")
            n = max(0, int(g.get("count") or 1))
            if fi is None or fi < 0 or fi >= len(subs):
                all_lines.append({"n": n, "pct": None, "kind": "accent"})
                continue
            text = " ".join(words[fi:fi + n])
            all_lines.append({"n": n, "pct": round(_text_width(tt, text) / IW * 100, 1),
                              "kind": "accent"})

        # ---- 2. прекомпы по break: разбиение mid_groups ----
        mids = d.get("mid_groups", [])
        all_pc, n_null = split_mid_groups_precomps(mids, subs)
        for fi, last_i, n_lines in all_pc:
            s0 = subs[fi][0]
            glen = (subs[last_i][1] - s0) / fps
            prev_end = subs[fi - 1][1] if fi > 0 else s0
            pause = (s0 - prev_end) / fps
            cam = camera_at(cam_tracks, s0)
            precomps.append({"n_lines": n_lines, "glen": round(glen, 2),
                             "pause": round(pause, 2), "cam": cam})
        # разрыв между прекомпами: конец предыдущего прекомпа -> начало следующего
        for k in range(1, len(all_pc)):
            end_prev = subs[all_pc[k - 1][1]][1] / fps
            start_cur = subs[all_pc[k][0]][0] / fps
            gaps.append(round(start_cur - end_prev, 2))
        nulls_all.append(n_null)

        # ---- 3. сверка с .jsx ----
        jsx_path = next((os.path.join(DD, stem + ".jsx") for DD in JSX_DIRS
                         if os.path.isfile(os.path.join(DD, stem + ".jsx"))), None)
        if not jsx_path:
            jsx_stats["no_jsx"] += 1
            continue
        jsx_stats["found"] += 1
        groups = intro_groups_from_jsx(jsx_path)
        if groups is None:
            jsx_stats["no_jsx"] += 1
            continue
        precomps_intro = (1 if d.get("intro_rows") else 0) \
            + (sum(1 for g in mids if g.get("break")))
        jsx_pc = len(groups)
        same = jsx_pc == precomps_intro
        if same:
            jsx_stats["match"] += 1
        jsx_stats["rows"].append({"clip": stem, "precomps": precomps_intro,
                                  "jsx_precomps": jsx_pc, "match": same})
        for gr in groups:
            n_lines = len(gr)
            jsx_precomps.append({"n_lines": n_lines,
                                 "words_per_line": [len(ln.get("words", [])) for ln in gr]})

    # ================= вывод =================
    print("== 1. СЛОВА В СТРОКЕ И ШИРИНА ==")
    for kind in ("hook", "accent"):
        sub = [x for x in all_lines if x["kind"] == kind]
        n_hist = Counter(x["n"] for x in sub)
        p = [x["pct"] for x in sub if x["pct"] is not None]
        print("  [{}] строк {} · слов в строке {} · медиана ширины {:.1f}% · p90 {:.1f}%".format(
            kind, len(sub), dict(sorted(n_hist.items())),
            med(p) or 0, pct(p, .9) or 0))
    n_hist = Counter(x["n"] for x in all_lines)
    print("все строки:", dict(sorted(n_hist.items())))
    p = [x["pct"] for x in all_lines if x["pct"] is not None]
    print("ширина всех строк, % кадра: min {:.1f} · p25 {:.1f} · медиана {:.1f} · "
          "p75 {:.1f} · p90 {:.1f} · max {:.1f}".format(min(p), pct(p, .25), med(p),
                                                       pct(p, .75), pct(p, .9), max(p)))
    print("строк, не вылезающих за 92% (автофит не трогал):",
          sum(1 for x in p if x <= FIT_W * 100), "/", len(p))
    by_n = {}
    for n in sorted(n_hist):
        v = [x["pct"] for x in all_lines if x["n"] == n and x["pct"] is not None]
        by_n[n] = round(med(v), 1) if v else None
    print("медиана ширины по числу слов в строке:", by_n)

    print()
    print("== 2. ПРЕКОМПЫ ПО intro.json ==")
    print("прекомпов:", len(precomps))
    ln = Counter(x["n_lines"] for x in precomps)
    print("строк (акцентов) в прекомпе:", dict(sorted(ln.items())))
    print("акцентов без слова-якоря (from=null), всего на роликах:",
          sum(nulls_all), "=", round(sum(nulls_all) / max(1, len(nulls_all)), 1), "/ролик")
    gl = [x["glen"] for x in precomps]
    pa = [x["pause"] for x in precomps]
    print("длительность прекомпа, с: медиана {:.2f} · p25 {:.2f} · p90 {:.2f}".format(
        med(gl), pct(gl, .25), pct(gl, .9)))
    print("пауза перед первым словом прекомпа, с: медиана {:.2f} · p25 {:.2f} · p90 {:.2f}".format(
        med(pa), pct(pa, .25), pct(pa, .9)))
    print("прекомпов с паузой > 0.4с (порог INTRO_MID_GAP):",
          sum(1 for x in pa if x is not None and x > 0.4), "/", len(pa))
    print("разрыв конец прекомпа -> начало следующего, с: медиана {:.2f} · p90 {:.2f}".format(
        med(gaps), pct(gaps, .9)))
    print("камера на первом слове прекомпа:", dict(sorted(Counter(x["cam"] for x in precomps).items())))

    print()
    print("== 3. ПРЕКОМПЫ ИЗ .jsx (что реально собралось) ==")
    print(".jsx найден:", jsx_stats["found"], "· совпало прекомпов:", jsx_stats["match"],
          "· .jsx нет:", jsx_stats["no_jsx"])
    diffs = [r for r in jsx_stats["rows"] if not r["match"]]
    print("расхождений прекомпов:", len(diffs))
    for r in diffs:
        print("   ", r["clip"], "intro", r["precomps"], "vs jsx", r["jsx_precomps"])
    ln2 = Counter(x["n_lines"] for x in jsx_precomps)
    print("строк в прекомпе (.jsx):", dict(sorted(ln2.items())))
    wpl = [w for x in jsx_precomps for w in x["words_per_line"]]
    print("слов в строке (.jsx):", dict(sorted(Counter(wpl).items())))
    print("медиана слов в строке (.jsx):", med(wpl))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR, help="папка с *.intro.json")
    ap.add_argument("--jsx-dirs", nargs="*", default=JSX_DIRS,
                    help="папки с собранными .jsx")
    args = ap.parse_args()
    OUT_DIR, JSX_DIRS = args.out, args.jsx_dirs or JSX_DIRS
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
