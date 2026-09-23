# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Универсальный CTC-ASR (transformers) — «ГигаАМ для других языков».

Одна акустическая CTC-модель (wav2vec2 / MMS / любая `AutoModelForCTC`) слушает
звук и САМА отдаёт слова с таймингами: greedy-декод даёт для каждого токена
номер кадра, кадр -> секунды. Forced-align (wav2vec2 поверх чужого текста, как в
`falign`/`gigaam_cut.align_full`) здесь НЕ нужен — тайминги родные, из тех же
эмиссий, что и текст.

Плюс к таймингам считаем ВЕРОЯТНОСТЬ слова — это то, на чём держится
самопроверка стыков (`selfcheck`): слово, обрезанное катом, звучит огрызком и
получает низкую вероятность. Берём МИНИМУМ softmax по буквам слова, а не
среднее: CTC самоуверен, у него почти все буквы ~1.0, и среднее размывает ту
единственную покалеченную букву на срезе, ради которой всё и затевалось.

Список конкретных моделей (язык -> HF-id) живёт в `asr_engines.json` — добавить
язык = дописать строчку в JSON, код трогать не надо.

Использование:
    from core import ctc_asr
    words = ctc_asr.transcribe("a.wav", "jonatasgrosman/wav2vec2-large-xlsr-53-english")
    # -> [{"w","start","end","prob"}]

Как подпроцесс (так его зовёт asr_backends — падение CUDA не убьёт Flask):
    python ctc_asr.py a.wav --model <hf-id> --out words.json
"""
import os, sys, json
from core.app_meta import console_emit, wrap_emit
from core.umsg import ReelsiError, cli_error


SR = 16000
CHUNK = 20.0          # сек: окно инференса (VRAM ~ линейно от длины окна)
SEARCH = 4.0          # сек: в последних SEARCH сек окна ищем самую тихую точку под шов
TAIL = 0.10           # сек: не откусывать окончание слова (как в falign)

_MODEL = None         # (model_id, device) -> (processor, model)


def get_model(model_id, device="cuda"):
    """Загрузить (и закэшировать) CTC-модель. Возвращает (processor, model, device)."""
    global _MODEL
    import torch
    from transformers import AutoModelForCTC, AutoProcessor, Wav2Vec2Processor
    # "cuda" здесь — это дефолт «как раньше», а не осознанный выбор юзера: если
    # NVIDIA нет, спрашиваем pick_device (на маке отдаст mps, а не сразу cpu)
    if device == "cuda" and not torch.cuda.is_available():
        from core.device import pick_device
        device = pick_device()
    if _MODEL is None or _MODEL[0] != (model_id, device):
        release_model()
        try:
            proc = AutoProcessor.from_pretrained(model_id)
        except ReelsiError: raise
        except Exception:
            # У части моделей в репозитории лежит процессор с языковой моделью
            # (Wav2Vec2ProcessorWithLM) — он тянет pyctcdecode, которого у нас нет.
            # Нам нужен только токенайзер под greedy-декод: берём обычный процессор
            # (ровно так же поступает falign с этой же моделью).
            proc = Wav2Vec2Processor.from_pretrained(model_id)
        model = AutoModelForCTC.from_pretrained(model_id).to(device).eval()
        _MODEL = ((model_id, device), proc, model)
    return _MODEL[1], _MODEL[2], device


def release_model():
    """Освободить VRAM (зовём после батча — как falign.release_model)."""
    global _MODEL
    if _MODEL is None:
        return False
    _MODEL = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU недоступны — чистить нечего
    return True


def _load_audio(wav_path):
    """float32-моно 16кГц (torchaudio: он и так в зависимостях falign/gigaam_cut)."""
    import torchaudio
    wav, sr = torchaudio.load(wav_path)
    audio = wav.mean(0) if wav.shape[0] > 1 else wav[0]
    if sr != SR:
        audio = torchaudio.functional.resample(audio, sr, SR)
    return audio


def _quiet_cut(audio, lo, hi, frame=0.05):
    """Самая тихая точка (центр самого тихого 50мс-кадра) в audio[lo:hi] — кладём
    туда шов между окнами, чтобы рез не попал в середину слова."""
    import torch
    f = max(1, int(frame * SR))
    seg = audio[lo:hi].abs()
    nf = len(seg) // f
    if nf < 1:
        return hi
    rms = seg[:nf * f].reshape(nf, f).mean(dim=1)
    return lo + int(torch.argmin(rms)) * f + f // 2


def _windows(audio):
    """Границы окон ~CHUNK сек со швами в тихих точках. -> [(a0, a1), …] в сэмплах."""
    n = len(audio)
    step, search = int(CHUNK * SR), int(SEARCH * SR)
    out, a0 = [], 0
    while a0 < n:
        a1 = min(a0 + step, n)
        if a1 < n:
            a1 = _quiet_cut(audio, max(a0 + step - search, a0 + SR), a1)
        out.append((a0, a1))
        a0 = a1
    return out


def _decode_window(proc, model, device, clip, t0):
    """Greedy-CTC по одному окну -> [{w,start,end,prob}] в АБСОЛЮТНЫХ секундах.

    Токен «эмитится» на кадре, где argmax != blank и отличается от предыдущего
    (стандартное CTC-схлопывание). Кадр -> секунды через точный шаг ЭТОГО окна
    (dur/T): у conv-фронтенда шаг чуть плавает от длины входа, поэтому считаем
    его по окну, а не берём номинальные 20мс — иначе тайминги уезжают."""
    import torch
    tok = proc.tokenizer
    with torch.inference_mode():
        logits = model(clip.unsqueeze(0).to(device)).logits[0]      # [T, V]
        probs = torch.softmax(logits.float(), dim=-1)
        conf, ids = probs.max(dim=-1)
    ids, conf = ids.cpu().tolist(), conf.cpu().tolist()
    T = len(ids)
    if not T:
        return []
    fd = (len(clip) / SR) / T                                       # сек на кадр
    blank = tok.pad_token_id
    delim = getattr(tok, "word_delimiter_token", "|")

    words, chars, frames, confs = [], [], [], []

    def commit():
        text = "".join(chars).strip()
        if text and frames:
            words.append({"w": text,
                          "start": round(t0 + frames[0] * fd, 3),
                          "end": round(t0 + (frames[-1] + 1) * fd, 3),
                          "prob": round(min(confs), 3)})
        chars.clear(); frames.clear(); confs.clear()

    prev = None
    for i, tid in enumerate(ids):
        if tid == blank or tid == prev:
            prev = tid
            continue
        prev = tid
        ch = tok.convert_ids_to_tokens(tid)
        if ch in (delim, " ", "|") or ch.startswith("▁"):           # граница слова
            commit()
            ch = ch[1:] if ch.startswith("▁") else ""
            if not ch:
                continue
        if ch in ("<s>", "</s>", "<pad>", "<unk>"):
            continue
        chars.append(ch); frames.append(i); confs.append(conf[i])
    commit()
    return words


def transcribe(wav_path, model_id, device="cuda", emit=console_emit, release=True):
    """Весь файл окнами -> [{w,start,end,prob}] в секундах от начала файла."""
    emit = wrap_emit(emit)
    proc, model, dev = get_model(model_id, device)
    audio = _load_audio(wav_path)
    wins = _windows(audio)
    emit("CTC ({model}): {windows} окон, {sec:.1f} сек",
         model=model_id, windows=len(wins), sec=len(audio) / SR)
    words = []
    for (a0, a1) in wins:
        if a1 - a0 < SR // 8:                                       # огрызок < 0.125с
            continue
        words += _decode_window(proc, model, dev, audio[a0:a1], a0 / SR)
    words.sort(key=lambda w: w["start"])
    for i, w in enumerate(words):                                   # хвост как в falign
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + TAIL
        w["end"] = round(min(w["end"] + TAIL, max(w["end"], nxt - 0.02)), 3)
    emit("CTC: {count} слов (родные тайминги, без forced-align)", count=len(words))
    if release:
        release_model()
    return words


if __name__ == "__main__":
    try:
        import argparse, logging, traceback
        ap = argparse.ArgumentParser(description="CTC-ASR: слова с таймингами и вероятностями")
        ap.add_argument("wav")
        ap.add_argument("--model", required=True, help="HF-id или локальный путь CTC-модели")
        ap.add_argument("--device", default="cuda")
        ap.add_argument("--out", help="куда писать JSON (иначе — временный файл)")
        a = ap.parse_args()
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                            format="%(levelname)s:%(name)s:%(message)s")
        real_stdout = sys.stdout
        sys.stdout = sys.stderr          # библиотечный шум — на stderr, stdout = только путь
        try:
            import tempfile
            out = a.out or os.path.join(tempfile.gettempdir(), "_ctc_asr.json")
            ws = transcribe(a.wav, a.model, a.device, emit=lambda *x, **k: print(*x))
            json.dump(ws, open(out, "w", encoding="utf-8"), ensure_ascii=False)
            sys.stdout = real_stdout
            print(out)
        except ReelsiError: raise
        except Exception as e:
            sys.stdout = real_stdout
            sys.stderr.write(traceback.format_exc())
            sys.stderr.write("CTC_ASR_ERROR: %s: %s\n" % (type(e).__name__, e))
            sys.exit(1)
    except ReelsiError as e:
        cli_error(e)
