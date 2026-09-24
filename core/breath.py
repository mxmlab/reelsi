# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вздохи, «кхе» и причмоки на границах кусков нарезки.

Зачем отдельный модуль. `gigaam_cut.refine_keep` убирает из кусков то, где НЕТ
слова и ТИХО. Остаётся третье: звук есть, слова нет — вдох, «кхм», чмоканье,
стук. Громкостью они не ловятся (кашель бывает громче речи), а маска речи их
накрывает добором волны, потому что паузы между словом и вздохом нет.

Как решаем. Три источника, каждый отвечает на то, в чём силён:
  * Silero VAD — покадрово (32мс) «речь / не речь», 0.9с на ролик на CPU;
  * CED-tiny (AudioSet, 5.5M) — участок В ИЗОЛЯЦИИ: речь от не-речи отделяет
    идеально (AUC 1.00 на ручной разметке), длинные вздохи зовёт Gasp/Breathing;
  * акустика — громкость, доля ВЧ, длительность, место в куске.
Ни один по отдельности вздох от хвоста слова не отличает (лучший AUC 0.84).
Поэтому решает логистическая регрессия, обученная на РУЧНОЙ доводке юзера
(`breath_model.json`, обучение — `tools/train_breath.py`): метка = убрал он этот
участок руками или оставил.

Две рабочие точки, а не одна: `p >= P_CUT` — режем сами (юзер: «что точно вдохи
и кашель — отметать»), `p >= P_MARK` — только помечаем в редакторе нарезки, режет
он одним кликом. Ложная метка ничего не стоит, ложный рез стоит слова.

Без silero-vad/transformers или без весов модуль молча выключается: нарезка
работает как раньше (`available()` вернёт False с причиной).
"""
from __future__ import annotations
import os
import json
from typing import Any

import numpy as np

from core import paths
from core.device import pick_device
from core.app_meta import console_emit, wrap_emit

SR = 16000
MIN_DUR = 0.10        # сек: короче — не участок, а стык волн
MAX_DUR = 1.20        # сек: длиннее — это уже не вздох, туда лезть страшно
P_CUT = 0.95          # вероятность, с которой режем сами (точность 77% на отложенных)
P_MARK = 0.50         # вероятность, с которой помечаем в редакторе (~7 меток на ролик)
SPEECH_MAX = 0.25     # выше этой вероятности речи не режем НИКОГДА (страховка)
AIR = 0.03            # сек: воздух, который оставляем у речи при резе
CED_ID = "mispeech/ced-tiny"
CED_REVISION = "ace276d29dd0bb3f3517b0fa8cf300738c409019"
MODEL_JSON = paths.data("breath_model.json")

# AudioSet: что считаем событием (не речью) и что речью
EVENT_IDS = {"вздох": 41, "выдох": 26, "оханье": 44, "кашель": 47,
             "кхе": 48, "чих": 49, "шмыг": 50}
SPEECH_IDS = [0, 1, 2, 3, 70]

_CED: Any = None           # (feature_extractor, model, device) — грузим один раз
_VAD: Any = None


# --------------------------------------------------------------------------- #
# Доступность
# --------------------------------------------------------------------------- #
def available(path: str | None = None) -> tuple[bool, str]:
    """(bool, причина). Проверяем ДО нарезки, чтобы не падать в середине джоба.

    path — модель конкретного спикера (у каждого свои вдохи и своя комната,
    см. speakers.py); None = общая breath_model.json."""
    try:
        import silero_vad            # noqa: F401
    except Exception as e:
        return False, f"нет silero-vad ({type(e).__name__})"
    try:
        import transformers          # noqa: F401
    except Exception as e:
        return False, f"нет transformers ({type(e).__name__})"
    p = path or MODEL_JSON
    if not os.path.exists(p):
        return False, f"нет {os.path.basename(p)} (обучи: tools/train_breath.py)"
    return True, ""


# --------------------------------------------------------------------------- #
# Кандидаты
# --------------------------------------------------------------------------- #
def candidates(
    keep: Any, words: list[dict[str, Any]], min_dur: float = MIN_DUR, max_dur: float = MAX_DUR
) -> list[dict[str, Any]]:
    """Места внутри кусков, где НЕТ слова: начало куска, хвост куска, середина.

    Ровно то же, что юзер правит руками. Позиция важна: хвост куска — самый
    частый случай (13 из 18 в разборе), и цена ошибки там другая, чем в середине.
    """
    out = []
    for pi, (s, e) in enumerate(keep):
        inside = [w for w in words if w["end"] > s and w["start"] < e]
        inside.sort(key=lambda w: w["start"])
        edges = [s] + [x for w in inside for x in (w["start"], w["end"])] + [e]
        for i in range(0, len(edges) - 1, 2):
            a, b = edges[i], edges[i + 1]
            if not (min_dur <= b - a <= max_dur):
                continue
            pos = ("голова" if i == 0 else
                   "хвост" if i == len(edges) - 2 else "середина")
            out.append({"t0": round(a, 3), "t1": round(b, 3), "pos": pos, "piece": pi})
    return out


# --------------------------------------------------------------------------- #
# Признаки
# --------------------------------------------------------------------------- #
def _vad_probs(y: Any) -> tuple[Any, float]:
    """Вероятность речи на каждые 32мс. Модель — ONNX (torch-JIT падал на Windows)."""
    global _VAD
    import torch
    from silero_vad import load_silero_vad
    if _VAD is None:
        _VAD = load_silero_vad(onnx=True)
    _VAD.reset_states()
    return np.array([float(_VAD(torch.from_numpy(y[i:i + 512]), SR))
                     for i in range(0, max(0, len(y) - 512), 512)]), 512.0 / SR


def _ced(y: Any, spans: list[tuple[float, float]], batch: int = 32) -> Any:
    """AudioSet-вероятности для каждого участка В ИЗОЛЯЦИИ.

    Участок кладём в середину 10с тишины — родная длина модели. Тайлинг (зациклить
    кусок) пробовали: получается периодический гул, и модель слышит «Music».
    Скользящее окно по всему ролику тоже пробовали — соседняя речь забивает окно
    (AUC 0.53). Работает только изоляция.
    """
    global _CED
    import torch
    from transformers import AutoModelForAudioClassification, AutoFeatureExtractor
    if _CED is None:
        dev = pick_device()
        fe = AutoFeatureExtractor.from_pretrained(
            CED_ID, revision=CED_REVISION, trust_remote_code=True)
        m = AutoModelForAudioClassification.from_pretrained(
            CED_ID, revision=CED_REVISION, trust_remote_code=True).to(dev).eval()
        _CED = (fe, m, dev)
    fe, m, dev = _CED
    n = int(10.0 * SR)
    out = np.zeros((len(spans), m.config.num_labels), dtype="float32")
    with torch.no_grad():
        for i in range(0, len(spans), batch):
            clips = []
            for a, b in spans[i:i + batch]:
                x = y[max(0, int(a * SR)):int(b * SR)][:n]
                z = np.zeros(n, dtype="float32")
                z[(n - len(x)) // 2:(n - len(x)) // 2 + len(x)] = x
                clips.append(z)
            enc = fe(clips, sampling_rate=SR, return_tensors="pt")
            enc = {k: v.to(dev) for k, v in enc.items()}
            # ВАЖНО: CED отдаёт УЖЕ вероятности, sigmoid поверх схлопывает всё в 0.5
            out[i:i + len(clips)] = m(**enc).logits.float().cpu().numpy()
    return out


FEATURES = ["длительность", "хвост", "голова", "громкость_макс", "громкость_ср",
            "вч", "vad_ср", "vad_мин", "vad_макс", "ced_речь", "ced_событие",
            "пауза_до", "пауза_после"]


def features(
    wav_path: str, cands: list[dict[str, Any]], words: list[dict[str, Any]]
) -> tuple[Any, list[tuple[str, float]]]:
    """Матрица признаков (len(cands) x len(FEATURES)) в порядке FEATURES."""
    import soundfile as sf
    from core import gigaam_cut as G  # ленивый импорт: G импортирует нас
    y, _ = sf.read(wav_path, dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)
    db, hf, floor, hop = G._envelope(wav_path)
    vad, vhop = _vad_probs(y)
    ced = _ced(y, [(c["t0"], c["t1"]) for c in cands])
    ev = np.array([EVENT_IDS[k] for k in EVENT_IDS])
    X = np.zeros((len(cands), len(FEATURES)), dtype="float32")
    for i, c in enumerate(cands):
        a, b = c["t0"], c["t1"]
        i0, i1 = int(a / hop), min(len(db) - 1, int(b / hop))
        seg = db[i0:i1 + 1] - floor if i1 >= i0 else np.array([0.0])
        j0, j1 = int(a / vhop), min(len(vad) - 1, int(b / vhop))
        v = vad[j0:j1 + 1] if j1 >= j0 and len(vad) else np.array([1.0])
        prev = [w["end"] for w in words if w["end"] <= a + 0.01]
        nxt = [w["start"] for w in words if w["start"] >= b - 0.01]
        X[i] = [b - a,
                1.0 if c["pos"] == "хвост" else 0.0,
                1.0 if c["pos"] == "голова" else 0.0,
                float(seg.max()), float(seg.mean()),
                float(hf[i0:i1 + 1].mean()) if i1 >= i0 else 0.0,
                float(v.mean()), float(v.min()), float(v.max()),
                float(ced[i, SPEECH_IDS].max()), float(ced[i, ev].max()),
                float(a - max(prev)) if prev else 9.0,
                float(min(nxt) - b) if nxt else 9.0]
    cls = []
    for i in range(len(cands)):
        k = max(EVENT_IDS, key=lambda k: ced[i, EVENT_IDS[k]])
        cls.append((k, float(ced[i, EVENT_IDS[k]])))
    return X, cls


# --------------------------------------------------------------------------- #
# Модель
# --------------------------------------------------------------------------- #
def load_model(path: str = MODEL_JSON) -> dict[str, Any]:
    d = json.load(open(path, encoding="utf-8"))
    if d.get("features") != FEATURES:
        raise ValueError("breath_model.json обучен на других признаках — переобучи "
                         "(python tools/train_breath.py)")
    return d


def predict(X: Any, mdl: dict[str, Any]) -> Any:
    """Вероятность «это вздох/кхе, который юзер бы убрал».

    Модель — градиентный бустинг, но хранится РАЗОБРАННОЙ в json (пороги и листья
    деревьев), а не пиклом sklearn: в проде не нужен ни sklearn, ни совпадение его
    версии. sklearn нужен только для обучения (`train_breath.py`).
    """
    X = np.asarray(X, dtype="float64")
    if mdl.get("kind") == "линейная":
        z = (X - np.array(mdl["mean"])) / np.array(mdl["std"])
        return 1.0 / (1.0 + np.exp(-(z @ np.array(mdl["w"]) + mdl["b"])))
    out = np.full(len(X), float(mdl["baseline"]))
    for t in mdl["trees"]:
        f, thr = t["feature"], t["thr"]
        left, right, leaf, val = t["left"], t["right"], t["leaf"], t["value"]
        for i in range(len(X)):
            k = 0
            while not leaf[k]:
                k = left[k] if X[i, f[k]] <= thr[k] else right[k]
            out[i] += val[k]
    return 1.0 / (1.0 + np.exp(-out))


def detect(
    wav_path: str, keep: Any, words: list[dict[str, Any]], model: dict[str, Any] | None = None, emit: Any = console_emit, path: str | None = None
) -> list[dict[str, Any]]:
    """[{t0,t1,p,pos,класс,piece}] — по убыванию вероятности.

    path — json модели этого спикера (профиль, поле `breath_model`); model —
    уже загруженная модель, приоритетнее path."""
    emit = wrap_emit(emit)
    ok, why = available(path if model is None else None)
    if not ok:
        emit("  детектор вздохов выключен: {why}", why=why, flush=True)
        return []
    mdl = model or load_model(path or MODEL_JSON)
    cands = candidates(keep, words)
    if not cands:
        return []
    X, cls = features(wav_path, cands, words)
    p = predict(X, mdl)
    isp = FEATURES.index("ced_речь")
    for c, pi, xi, (k, pk) in zip(cands, p, X, cls):
        c["p"] = round(float(pi), 3)
        c["класс"] = k
        c["p_класс"] = round(pk, 3)
        c["речь"] = round(float(xi[isp]), 3)
    return sorted(cands, key=lambda c: -c["p"])


def apply(
    keep: Any, marks: list[dict[str, Any]], p_cut: float = P_CUT, air: float = AIR, min_island: float = 0.35, speech_max: float = SPEECH_MAX
) -> tuple[Any, list[dict[str, Any]], list[int]]:
    """Вырезать уверенные метки. Возвращает (keep, что вырезано).

    Порога вероятности мало: страховка — CED-вероятность РЕЧИ на этом участке.
    Разбор ложных срабатываний на ручной разметке: у них речь 0.04 (то есть это
    тоже не речь, юзер просто не стал чистить), а единственный спорный случай был
    ровно с высокой речью. Поэтому речь выше speech_max не режем НИКОГДА, какой
    бы ни была вероятность модели — метку в редакторе он увидит и решит сам."""
    cut = [m for m in marks
           if m["p"] >= p_cut and m.get("речь", 0.0) < speech_max]
    if not cut:
        return keep, [], list(range(len(keep)))
    from core import align
    drops = [(m["t0"] + air, m["t1"] - air) for m in cut if m["t1"] - m["t0"] > 2 * air]
    out: list[tuple[float, float]]
    parents: list[int]
    out, parents = [], []
    for a, b in align.subtract_ranges(keep, drops):
        if b - a < min_island:
            continue
        mid = 0.5 * (a + b)
        src = [i for i, (s, e) in enumerate(keep) if s <= mid <= e]
        out.append((a, b))
        parents.append(src[0] if src else 0)      # камеру наследуем от родителя
    return out, cut, parents
