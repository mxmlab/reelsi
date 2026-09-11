# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обучение детектора вздохов на РУЧНОЙ доводке нарезки.

Разметка берётся из работы юзера и ничего не требует руками: рядом с каждым
роликом лежат `<stem>.cuts.json` (что вырезал ИИ) и `<stem>.project.json` (что в
итоге осталось ПОСЛЕ его правок в редакторе). Если project.json новее cuts.json —
ролик правили руками, и разница между автонарезкой и его версией это и есть
метки: участок без слов, который он убрал = вздох/«кхе», оставил = хвост слова.

    python tools/train_breath.py                      # ролики из Reelsi_out рядом
    python tools/train_breath.py --dirs D:/out1 D:/out2
    python tools/train_breath.py --cache .breath_cache  # кэш wav+слов между запусками

Пишет `breath_model.json` рядом с модулем и печатает ЧЕСТНУЮ оценку:
пороги подбираются на N-1 роликах и проверяются на отложенном (leave-one-out).
"""
import os
import sys
import json
import glob
import time
import argparse
import subprocess

import numpy as np

# Скрипт живёт в tools/, репозиторий — на уровень выше: без корня в sys.path
# не найдётся пакет core.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import paths   # noqa: E402
from core import arrowfix  # noqa: F401  # предзагрузка pyarrow до torch во избежание краша arrow.dll, не переставлять ниже
from core import breath
from core import align


def _clips(dirs):
    """Ролики с ручной доводкой: project.json заметно новее cuts.json."""
    out = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.project.json"))):
            c = p[:-len(".project.json")] + ".cuts.json"
            if not os.path.exists(c):
                continue
            if os.path.getmtime(p) - os.path.getmtime(c) <= 60:
                continue
            try:
                pj = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            cam = (pj.get("cams") or [None])[0]
            if cam and os.path.exists(cam) and pj.get("keep"):
                out.append((p[:-len(".project.json")], cam, pj["keep"]))
    return out


def _prepare(clips, cache):
    """wav 16кГц + слова GigaAM для каждого ролика (кэш между запусками).
    Модель грузится ОДИН раз на все ролики: 34 ролика = ~30с."""
    os.makedirs(cache, exist_ok=True)
    todo = []
    for stem, cam, _ in clips:
        base = os.path.basename(stem)
        wav = os.path.join(cache, base + ".wav")
        wj = os.path.join(cache, base + ".words.json")
        if os.path.exists(wj):
            continue
        if not os.path.exists(wav):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", cam, "-vn", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
        todo.append((base, wav, wj))
    if todo:
        from core import gigaam_cut as G
        import gigaam
        print("транскрибирую %d роликов…" % len(todo), flush=True)
        t0 = time.time()
        model = gigaam.load_model("v3_ctc")
        try:
            for i, (base, wav, wj) in enumerate(todo, 1):
                w = [x for x in G._transcribe_words_manual(model, wav,
                                                           emit=lambda *a, **k: None)
                     if x["w"].strip()]
                w.sort(key=lambda x: x["start"])
                json.dump({"words": w}, open(wj, "w", encoding="utf-8"),
                          ensure_ascii=False)
                print("  [%2d/%2d] %-22s %4d слов (%.0fс)"
                      % (i, len(todo), base, len(w), time.time() - t0), flush=True)
        finally:
            del model
            G._free_torch()


def _auto_keep(stem, words):
    """Восстановить АВТОнарезку (до ручных правок) из cuts.json + слов."""
    from core import gigaam_cut as G
    cuts = json.load(open(stem + ".cuts.json", encoding="utf-8"))
    drops = [(c["t0"], c["t1"]) for c in cuts if c.get("source") == "вырезано"]
    kept = set()
    for i, w in enumerate(words):
        m = 0.5 * (w["start"] + w["end"])
        if not any(a - 0.05 <= m <= b + 0.05 for a, b in drops):
            kept.add(i)
    keep0 = G.keep_intervals(words, kept, G._silence_bounds(words))
    return G.refine_keep(keep0, _wav_of(stem), words=words,
                         emit=lambda *a, **k: None)[0]


_WAVS = {}


def _wav_of(stem):
    return _WAVS[stem]


def dataset(dirs, cache):
    clips = _clips(dirs)
    if not clips:
        raise SystemExit("не нашёл роликов с ручной доводкой в: " + ", ".join(dirs))
    print("роликов с ручной доводкой: %d" % len(clips))
    _prepare(clips, cache)
    X, Y, G_, meta = [], [], [], []
    for stem, cam, hand in clips:
        base = os.path.basename(stem)
        _WAVS[stem] = os.path.join(cache, base + ".wav")
        words = json.load(open(os.path.join(cache, base + ".words.json"),
                               encoding="utf-8"))["words"]
        auto = _auto_keep(stem, words)
        cands = breath.candidates(auto, words)
        if not cands:
            continue
        removed = align.subtract_ranges(auto, [(float(s), float(e)) for s, e in hand])
        x, cls = breath.features(_WAVS[stem], cands, words)
        for i, c in enumerate(cands):
            ov = sum(max(0.0, min(c["t1"], b) - max(c["t0"], a)) for a, b in removed)
            X.append(x[i])
            Y.append(1 if ov / (c["t1"] - c["t0"]) > 0.6 else 0)
            G_.append(base)
            meta.append(dict(c, клип=base, класс=cls[i][0]))
        print("  %-22s кандидатов %3d, из них убрано руками %3d"
              % (base, len(cands), sum(Y[-len(cands):])), flush=True)
    return np.array(X, dtype="float64"), np.array(Y), np.array(G_), meta


def fit(X, Y, trees=200, depth=3):
    """Градиентный бустинг (sklearn) -> РАЗОБРАННЫЙ в json.

    Линейная модель на этих признаках давала 59% точности на отложенных роликах,
    бустинг — 77%: связи нелинейные (тихий короткий хвост и громкий короткий
    «кхе» — оба вздохи, а между ними по громкости лежит речь). В прод уезжают
    только пороги и листья, sklearn там не нужен.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(
        max_iter=trees, learning_rate=0.06, max_depth=depth, min_samples_leaf=15,
        l2_regularization=1.0, class_weight="balanced", random_state=0)
    clf.fit(X, Y)
    out = []
    for stage in clf._predictors:
        n = stage[0].nodes
        out.append(dict(feature=[int(x) for x in n["feature_idx"]],
                        thr=[float(x) for x in n["num_threshold"]],
                        left=[int(x) for x in n["left"]],
                        right=[int(x) for x in n["right"]],
                        leaf=[bool(x) for x in n["is_leaf"]],
                        value=[float(x) for x in n["value"]]))
    return dict(kind="бустинг", features=breath.FEATURES,
                baseline=float(np.ravel(clf._baseline_prediction)[0]), trees=out)


def _rate(p, Y, thr):
    hit = int(((p >= thr) & (Y == 1)).sum())
    fp = int(((p >= thr) & (Y == 0)).sum())
    return hit, fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="+",
                    # старые имена папок остаются: там лежат уже размеченные ролики,
                    # выкинуть их из обучающей выборки из-за переименования проекта
                    # значило бы потерять данные
                    default=[os.path.join(os.path.dirname(paths.ROOT), d)
                             for d in ("Reelsi_out", "AutoCut_out",
                                       "MKAutoCut_out", "NGAutoCut_out")])
    ap.add_argument("--cache", default=paths.root("_breath_cache"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--speaker", default=None,
                    help="обучить ЛИЧНУЮ модель спикера: папка берётся из его "
                         "профиля (speakers/*.json), результат — "
                         "breath_model.<спикер>.json, путь прописывается в профиль")
    a = ap.parse_args()
    out = a.out or breath.MODEL_JSON
    if a.speaker:
        # Вздохи у каждого свои: общая модель, обученная на смеси, срабатывает на
        # одном спикере и молчит на другом. Учим по ЕГО папке и складываем рядом.
        from core import speakers as _sp
        prof = _sp.load(a.speaker)
        if prof is None:
            ap.error(f"нет профиля спикера «{a.speaker}» (см. reelsi/speakers/)")
        d = (prof.get("outdir") or "").strip()
        if not os.path.isdir(d):
            ap.error(f"в профиле «{a.speaker}» не задана существующая папка результата")
        a.dirs = [d]
        key = _sp._key(prof.get("label") or a.speaker)
        out = a.out or paths.root(f"breath_model.{key}.json")
    dirs = [d for d in a.dirs if os.path.isdir(d)]
    X, Y, G_, meta = dataset(dirs, a.cache)
    print("\nвсего кандидатов %d, из них вырезано руками %d (%.0f%%)"
          % (len(Y), Y.sum(), 100.0 * Y.mean()))

    # --- честная проверка: обучаемся на N-1 роликах, меряем на отложенном ---
    oof = np.zeros(len(Y))
    for g in np.unique(G_):
        m = G_ != g
        mdl = fit(X[m], Y[m])
        oof[~m] = breath.predict(X[~m], mdl)
    print("\nна отложенных роликах (leave-one-clip-out):")
    for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
        hit, fp = _rate(oof, Y, thr)
        prec = hit / max(hit + fp, 1)
        print("  порог %.2f: поймано %3d/%3d вздохов (%.0f%%), ложных %3d, точность %.0f%%"
              % (thr, hit, int(Y.sum()), 100.0 * hit / max(Y.sum(), 1), fp, 100 * prec))

    mdl = fit(X, Y)
    mdl["сколько_роликов"] = int(len(np.unique(G_)))
    mdl["сколько_примеров"] = int(len(Y))
    if a.speaker:
        mdl["спикер"] = a.speaker
    json.dump(mdl, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n-> %s (%d роликов, %d примеров, %d деревьев)"
          % (out, mdl["сколько_роликов"], len(Y), len(mdl["trees"])))
    if a.speaker:
        # Прописываем модель в профиль сами: иначе обучил и забыл подключить.
        from core import speakers as _sp
        prof = _sp.load(a.speaker)
        prof["breath_model"] = os.path.basename(out)
        _sp.save(prof.get("label") or a.speaker, prof)
        print("   прописал в профиль «%s» -> breath_model: %s"
              % (prof.get("label") or a.speaker, os.path.basename(out)))
    # на чём модель чаще всего делит — грубая, но полезная подсказка
    use = {}
    for t in mdl["trees"]:
        for f, lf in zip(t["feature"], t["leaf"]):
            if not lf:
                use[breath.FEATURES[f]] = use.get(breath.FEATURES[f], 0) + 1
    top = sorted(use.items(), key=lambda x: -x[1])[:8]
    print("чаще всего делит по:", ", ".join("%s×%d" % x for x in top))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
