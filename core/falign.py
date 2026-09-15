# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Forced alignment: точные пословные тайминги через wav2vec2 (по звуку).

Whisper даёт ТЕКСТ, но его тайминги приблизительны (систематически запаздывают,
иногда раздувают длительность слова). Здесь акустическая модель выравнивает уже
распознанный текст по аудио → точные start/end каждого слова. Это чинит и «кривые»
резы дублей, и раскладку субтитров.

Слова без кириллицы (цифры, латиница, «%») выровнять нельзя (нет в словаре модели) —
для них тайминг Whisper остаётся, а если рядом есть выровненные соседи, время
подтягивается к ним (интерполяция), чтобы не выпадать из общего ряда.
"""
import os, re
from core.app_meta import console_emit, wrap_emit
from core.applog import get_logger

log = get_logger("reelsi.falign")

# ВНИМАНИЕ: НЕ звать cuda_env.setup() — он ставит в PATH cuDNN от ctranslate2 (faster-whisper),
# а torch/wav2vec2 нужен свой встроенный cuDNN → иначе краш cudnnGetLibConfig (EXIT 127).

MODEL_ID = os.environ.get("FALIGN_MODEL", "jonatasgrosman/wav2vec2-large-xlsr-53-russian")
_MODEL = None      # кэш (proc, model, device) — грузим один раз на батч


def _resolve(device):
    """"cuda" в сигнатурах — исторический дефолт «как было», а не выбор юзера.
    Без NVIDIA спрашиваем pick_device: на маке это mps, а не сразу cpu."""
    if device != "cuda":
        return device
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        from core.device import pick_device
        return pick_device()
    except Exception:
        return "cpu"


def get_model(device="cuda"):
    global _MODEL
    device = _resolve(device)
    if _MODEL is None:
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        proc = Wav2Vec2Processor.from_pretrained(MODEL_ID)
        model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID).to(device).eval()
        _MODEL = (proc, model, device)
    return _MODEL


def release_model():
    """Освободить VRAM под wav2vec2 (вызывать после батча)."""
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
    except Exception:
        pass
    return True


def align_text(audio_f32, text, device="cuda", sr=16000):
    """Выровнять сырой ТЕКСТ (без исходных времён) по короткому аудио-клипу (float32 16k).
    Вернуть [{w,start,end}] в секундах ОТ НАЧАЛА клипа. Слова без кириллицы пропускаются."""
    import torch, torchaudio, numpy as np
    proc, model, dev = get_model(device)
    vocab = proc.tokenizer.get_vocab()
    blank = proc.tokenizer.pad_token_id
    delim = vocab.get("|")
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]

    def norm(w):
        return "".join(c for c in w.lower() if c in vocab and c != "|")

    targets, meta = [], []
    for k, w in enumerate(words):
        s = norm(w)
        if not s:
            continue
        if targets:
            targets.append(delim); meta.append(-1)
        for c in s:
            targets.append(vocab[c]); meta.append(k)
    clip = torch.tensor(np.ascontiguousarray(audio_f32, dtype=np.float32))
    if not targets or clip.numel() < sr // 8:
        return []
    with torch.inference_mode():
        em = torch.log_softmax(model(clip.to(dev).unsqueeze(0)).logits, dim=-1)
    T = em.shape[1]
    fd = (len(clip) / sr) / T
    try:
        aln, sc = torchaudio.functional.forced_align(
            em, torch.tensor([targets], device=dev), blank=blank)
        spans = torchaudio.functional.merge_tokens(aln[0], sc[0])
    except Exception:
        return []
    # merge_tokens склеивает подряд идущие одинаковые токены: на удвоенной букве
    # («класс») два таргета сливаются в один спан, и спанов становится меньше целей.
    # Тогда пара «спан i ↔ meta[i]» рвётся — уходим на прежний путь, но с логом.
    wf = {}
    if len(spans) == len(meta):
        for sp, wk in zip(spans, meta):
            if wk == -1 or sp.token == blank:
                continue
            if wk in wf:
                wf[wk][0] = min(wf[wk][0], sp.start)
                wf[wk][1] = max(wf[wk][1], sp.end)
            else:
                wf[wk] = [sp.start, sp.end]
    else:
        log.warning("forced align: spans count (%d) != meta count (%d), fallback to legacy mapping", len(spans), len(meta))
        ti = 0
        for sp in spans:
            if sp.token == blank:
                continue
            while ti < len(meta) and meta[ti] == -1:
                ti += 1
            if ti >= len(meta):
                break
            wk = meta[ti]; ti += 1
            if wk in wf:
                wf[wk][0] = min(wf[wk][0], sp.start); wf[wk][1] = max(wf[wk][1], sp.end)
            else:
                wf[wk] = [sp.start, sp.end]
    out = []
    for k, w in enumerate(words):
        if k in wf:
            out.append({"w": w, "start": round(wf[k][0]*fd, 3), "end": round(wf[k][1]*fd, 3)})
    return out


def _chunks(words, max_len=24.0, gap=0.4, min_len=3.0):
    """Резать список слов на чанки по паузам (границы = тихие места), до ~max_len сек."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and ((w["start"] - words[i - 1]["end"] > gap and
                     w["end"] - words[cur[0]]["start"] > min_len) or
                    w["end"] - words[cur[0]]["start"] > max_len):
            out.append(cur); cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def align_words(wav_path, words, device="cuda", emit=console_emit):
    """Вернуть КОПИЮ words с уточнёнными start/end (forced alignment по звуку)."""
    emit = wrap_emit(emit)
    if not words:
        return words
    import torch, torchaudio
    proc, model, dev = get_model(device)
    vocab = proc.tokenizer.get_vocab()
    blank = proc.tokenizer.pad_token_id
    delim = vocab.get("|")
    SR = 16000
    wav, sr = torchaudio.load(wav_path)          # [C, N]; чисто torch, без ctranslate2
    audio = wav.mean(0) if wav.shape[0] > 1 else wav[0]
    if sr != SR:
        audio = torchaudio.functional.resample(audio, sr, SR)
    dur = len(audio) / SR

    def norm(w):
        return "".join(c for c in w.lower() if c in vocab and c != "|")

    out = [dict(w) for w in words]
    n_ok = 0
    for ch in _chunks(words):
        a0 = max(0.0, words[ch[0]]["start"] - 0.25)
        a1 = min(dur, words[ch[-1]]["end"] + 0.25)
        clip = audio[int(a0 * SR):int(a1 * SR)].to(dev)
        if clip.numel() < SR // 10:
            continue
        with torch.inference_mode():
            em = torch.log_softmax(model(clip.unsqueeze(0)).logits, dim=-1)
        T = em.shape[1]
        fd = (a1 - a0) / T                       # длительность одного кадра эмиссии, сек
        targets, meta = [], []                   # meta[j] = индекс слова (или -1 для делимитера)
        for k in ch:
            s = norm(words[k]["w"])
            if not s:
                continue
            if targets:
                targets.append(delim); meta.append(-1)
            for c in s:
                targets.append(vocab[c]); meta.append(k)
        if not targets:
            continue
        try:
            aln, sc = torchaudio.functional.forced_align(
                em, torch.tensor([targets], device=dev), blank=blank)
            spans = torchaudio.functional.merge_tokens(aln[0], sc[0])
        except Exception:
            continue
        # Соответствие «спан i ↔ meta[i]» — то же самое, что в align_text
        # (там же разбор случая «спанов меньше целей»: удвоенная буква).
        wf = {}
        if len(spans) == len(meta):
            for sp, wk in zip(spans, meta):
                if wk == -1 or sp.token == blank:
                    continue
                if wk in wf:
                    wf[wk][0] = min(wf[wk][0], sp.start)
                    wf[wk][1] = max(wf[wk][1], sp.end)
                else:
                    wf[wk] = [sp.start, sp.end]
        else:
            log.warning("forced align: spans count (%d) != meta count (%d), fallback to legacy mapping", len(spans), len(meta))
            ti = 0
            for sp in spans:
                if sp.token == blank:
                    continue
                while ti < len(meta) and meta[ti] == -1:
                    ti += 1
                if ti >= len(meta):
                    break
                wk = meta[ti]; ti += 1
                if wk in wf:
                    wf[wk][0] = min(wf[wk][0], sp.start); wf[wk][1] = max(wf[wk][1], sp.end)
                else:
                    wf[wk] = [sp.start, sp.end]
        for wk, (fs, fe) in wf.items():
            out[wk]["start"] = round(a0 + fs * fd, 3)
            out[wk]["end"] = round(a0 + fe * fd, 3)
            out[wk]["_fa"] = True
            n_ok += 1

    # wav2vec2 ставит конец слова впритык (по последней фонеме) — добавляем «хвост»,
    # чтобы резы/субтитры не откусывали окончания. Не залезаем в начало следующего слова.
    TAIL = 0.12
    for i, w in enumerate(out):
        if not w.get("_fa"):
            continue
        nxt = out[i + 1]["start"] if i + 1 < len(out) else w["end"] + TAIL
        w["end"] = round(min(w["end"] + TAIL, max(w["end"], nxt - 0.02)), 3)

    # не выровненные (цифры/латиница) — подтянуть к соседям, чтобы не выпадали из ряда
    for i, w in enumerate(out):
        if w.get("_fa"):
            w.pop("_fa", None)
            continue
        prev_e = out[i - 1]["end"] if i > 0 else w["start"]
        nxt_s = out[i + 1]["start"] if i + 1 < len(out) else w["end"]
        if nxt_s >= prev_e:                      # уместить слово в зазор между соседями
            span = min(w["end"] - w["start"], max(0.05, nxt_s - prev_e))
            w["start"] = round(prev_e, 3)
            w["end"] = round(min(nxt_s, prev_e + span), 3)
    emit("  forced align: уточнено {aligned}/{total} слов", aligned=n_ok, total=len(words))
    return out
