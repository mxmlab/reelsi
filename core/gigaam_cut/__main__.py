# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Командная строка пакета:  python -m core.gigaam_cut file.wav --cams … --out …

Раньше это был хвост gigaam_cut.py и запускалось как `python gigaam_cut.py`.
Блок переехал дословно; сменился только способ запуска.
"""
import argparse, sys
from core import arrowfix  # noqa: F401  # предзагрузка pyarrow до torch во избежание краша arrow.dll, не переставлять ниже
from .pipeline import run
from core.app_meta import console_emit

if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m core.gigaam_cut")
    ap.add_argument("wav")
    ap.add_argument("--cams", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=50.4)
    ap.add_argument("--model")
    ap.add_argument("--speaker", default=None,
                    help="профиль спикера (speakers/*.json): свои пороги нарезки")
    ap.add_argument("--no-draft", action="store_true", help="без чернового .draft.mp4")
    ap.add_argument("--no-sense", action="store_true", help="без ИИ-разметки смысловых кусков")
    ap.add_argument("--no-refine", action="store_true", help="без подгона резов")
    ap.add_argument("--no-breath", action="store_true", help="без вырезания вздохов")
    ap.add_argument("--no-pauses", action="store_true", help="без вырезания пауз")
    ap.add_argument("--dedupe", "--no-dedupe", dest="dedupe", action=argparse.BooleanOptionalAction,
                    default=None, help="чистка дублей")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    stages = {}
    # Явный draft: в CLI без флага --no-draft черновик включён (дефект 1: normalize подставляет False без ключа)
    stages["draft"] = not a.no_draft
    if a.dedupe is not None:
        stages["dedupe"] = a.dedupe
    if a.no_sense:
        stages["sense"] = False
    if a.no_refine:
        stages["refine"] = False
    if a.no_breath:
        stages["breath"] = False
    if a.no_pauses:
        stages["pauses"] = "off"
    keep, cutlog, draft, info = run(
        a.wav, a.cams, [0.0] * len(a.cams), a.out, a.scale,
        model=a.model, speaker=a.speaker, stages=stages, emit=console_emit)
    print("keep:", keep)
    print("cutlog:", len(cutlog), "draft:", draft)

