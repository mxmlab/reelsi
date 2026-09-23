# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Subprocess wrapper for GigaAM subtitle generation.

Runs GigaAM whole-file transcription with NATIVE word timestamps
(word_timestamps=True — text and timings from the one CTC model, no wav2vec2
forced-align), writing the result ([{"w","start","end"}]) as JSON to a temp
file and printing ONLY that file path to stdout.

Why a separate process: the heavy GigaAM GPU work can hit a CUDA OOM or a
native segfault. Running it HERE means such a failure can only kill this
helper process — never the Flask server. This mirrors exactly how the cutting
path isolates GigaAM via omni_cut.py (gigaam_cut.run). VRAM is released at the
end (_free_torch), just like gigaam_cut.run() does, so the process exits with
a clean GPU.
"""
import os, sys, json, tempfile, traceback, logging
from core.umsg import ReelsiError, cli_error



def _emit(*a, **k):
    # gigaam_cut calls emit(msg, flush=True); accept and ignore any keyword
    # args (flush, etc.) so we stay compatible with the standard `print` API.
    print(*a, file=sys.stderr, flush=True)


def main():
    wav = sys.argv[1]
    # argv[2] (необязательный) — чекпойнт GigaAM: v3_ctc (деф.) / v3_rnnt /
    # v3_e2e_rnnt (с пунктуацией) / multilingual_large_ctc и т.д.
    model_name = sys.argv[2] if len(sys.argv) > 2 else "v3_ctc"
    # Clean, standard logging to stderr. This guarantees sys.stdout/stderr are
    # real file objects (not custom _emit-based wrappers) and that any library
    # logging (transformers/datasets/torch/rich) is captured on stderr, never on
    # our stdout result channel. Done BEFORE importing the heavy gigaam_cut deps.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s:%(name)s:%(message)s")
    from core import gigaam_cut
    _text, words = gigaam_cut.transcribe_words_whole(wav, emit=_emit, model_name=model_name)
    # release VRAM exactly like gigaam_cut.run() does, so the process exits clean
    gigaam_cut._free_torch()
    # normalize to the uniform [{"w","start","end"}] contract (defensive)
    out = []
    for w in words:
        if not (isinstance(w, dict) and "w" in w):
            continue
        d = {"w": w["w"], "start": float(w["start"]), "end": float(w["end"])}
        if w.get("prob") is not None:        # вероятность нужна самопроверке стыков
            d["prob"] = float(w["prob"])
        out.append(d)
    # pid в имени: субтитры в webui и самопроверка в старом UI писали в ОДИН файл,
    # и один из клипов получал чужие слова с чужими таймингами — молча
    tmp_out = os.path.join(tempfile.gettempdir(),
                           "_gigaam_subs_%s_%d.json" % (model_name, os.getpid()))
    with open(tmp_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return tmp_out


if __name__ == "__main__":
    try:
        real_stdout = sys.stdout
        sys.stdout = sys.stderr          # keep any library stdout noise off our result channel
        try:
            path = main()
        except ReelsiError: raise
        except Exception as e:
            sys.stdout = real_stdout
            # Full traceback first (captured in r.stderr by the parent for
            # diagnosis), then a concise one-line error as the LAST line so the
            # UI (which shows err[-1]) still gets a clear message.
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            sys.stderr.write("GIGAAM_SUBS_ERROR: %s: %s\n" % (type(e).__name__, e))
            sys.exit(1)
        sys.stdout = real_stdout
        sys.stdout.write(path)
    except ReelsiError as e:
        cli_error(e)
