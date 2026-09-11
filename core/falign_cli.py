# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Forced alignment в ОТДЕЛЬНОМ процессе (torch/torchaudio), чтобы cuDNN торча не
конфликтовал с cuDNN ctranslate2 (faster-whisper) в основном процессе нарезки.

Использование:  python falign_cli.py <wav> <in_words.json> <out_words.json>
"""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from core import falign

wav, fin, fout = sys.argv[1], sys.argv[2], sys.argv[3]
words = json.load(open(fin, encoding="utf-8"))
out = falign.align_words(wav, words, emit=lambda *a: print(*a, flush=True))
json.dump(out, open(fout, "w", encoding="utf-8"), ensure_ascii=False)
