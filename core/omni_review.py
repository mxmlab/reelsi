# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Omni-ревью чернового монтажа (этап 4 «уроки video-use», ЭКСПЕРИМЕНТ, выкл по умолчанию).

Qwen2.5-Omni-7B СМОТРИТ draft.mp4 кусками (~45с, видео+звук) и пишет замечания
ревьюера: неудачные склейки, обрезанные слова, затянутые статичные места, где
просится вставка. Результат — <stem>.review.json + строки в лог. Ничего не меняет
в монтаже — только советы (юзер решает сам).

Отдельный процесс (тяжёлый torch/bnb ~7.5 ГБ VRAM, как omni_asr) — грузится и
выходит, освобождая память. Запускать ТОЛЬКО когда LM Studio/Whisper выгружены.

    python omni_review.py <draft.mp4> [--chunk 45] [--out X.review.json]
"""
import sys, os, json, subprocess, argparse
from typing import Any, cast
from core import media
from core.umsg import ReelsiError, cli_error
try:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
except ReelsiError: raise
except Exception:
    pass  # поток без reconfigure — служебная печать не критична

SYS = ("Ты — придирчивый ревьюер ЧЕРНОВОГО монтажа вертикального talking-head ролика. "
       "Тебе показывают кусок черновика (низкое качество картинки — это нормально, черновик). "
       "Отметь ТОЛЬКО реальные проблемы монтажа:\n"
       "1) склейка, где мысль обрывается или слово съедено;\n"
       "2) заметный повтор одной фразы (дубль не вырезан);\n"
       "3) затянутый статичный кусок без смены плана (>12с), где просится перебивка/вставка;\n"
       "4) странный скачок/рассинхрон звука и картинки.\n"
       "Формат ответа: по одному замечанию на строку, «М:СС — что не так» (тайминг ВНУТРИ куска). "
       "Если проблем нет — ответь ровно: ок")


def _dur(path: str) -> float:
    """Длительность черновика, сек; 0.0 — не прочли (общая проба core/media.py)."""
    return media.probe_duration(path) or 0.0


def _cut_chunk(src: str, t0: float, t1: float, dst: str) -> str:
    """Кусок черновика для Omni: маленький и быстрый (черновик уже 720p/30).

    Таймаут: кусок 45 с в 480p собирается секундами, так что 600 с — это «ffmpeg
    завис», а не «долгий кусок»; без него запущенный вручную ревьюер висел бы вечно."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.2f}", "-i", src,
                    "-t", f"{t1 - t0:.2f}", "-vf", "scale=-2:480,fps=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                    "-c:a", "aac", "-b:a", "64k", dst], check=True, capture_output=True,
                    timeout=600)
    return dst


def review_chunk(proc: Any, model: Any, mp4: str) -> str:
    import torch
    from qwen_omni_utils import process_mm_info
    conv = [{"role": "system", "content": [{"type": "text", "text": SYS}]},
            {"role": "user", "content": [{"type": "video", "video": mp4}]}]
    text = proc.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
    audios, images, videos = process_mm_info(conv, use_audio_in_video=True)
    inputs = proc(text=text, audio=audios, images=images, videos=videos,
                  return_tensors="pt", padding=True, use_audio_in_video=True)
    inputs = inputs.to(model.device).to(model.dtype)
    with torch.inference_mode():
        ids = model.generate(**inputs, return_audio=False, max_new_tokens=300)
    gen = ids[:, inputs["input_ids"].shape[1]:]
    return proc.batch_decode(gen, skip_special_tokens=True,
                             clean_up_tokenization_spaces=False)[0].strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", help="черновой mp4 (<stem>.draft.mp4)")
    ap.add_argument("--chunk", type=float, default=45.0, help="длина куска, сек")
    ap.add_argument("--out")
    a = ap.parse_args()
    dur = _dur(a.draft)
    if dur <= 0:
        sys.exit("не читается черновик: " + a.draft)
    from core import omni_asr  # реюз загрузки Qwen-Omni (bnb 4-bit)
    print(f"Omni-ревью: {os.path.basename(a.draft)} ({dur:.0f}с, кусками по {a.chunk:.0f}с)", flush=True)
    proc, model = omni_asr.load_model()
    print("model loaded", flush=True)
    import tempfile
    notes = []
    t = 0.0
    i = 0
    while t < dur - 1.0:
        t1 = min(t + a.chunk, dur)
        piece = os.path.join(tempfile.gettempdir(), f"_omni_review_{i}.mp4")
        try:
            _cut_chunk(a.draft, t, t1, piece)
            txt = review_chunk(proc, model, piece)
        except ReelsiError: raise
        except Exception as ex:
            txt = f"(ошибка куска: {ex})"
        finally:
            try:
                os.remove(piece)
            except OSError:
                pass  # кусок уже убран
        clean = (txt or "").strip()
        if clean and clean.lower() not in ("ок", "ok", "ок."):
            notes.append({"t0": round(t, 1), "t1": round(t1, 1), "notes": clean})
            print(f"[{t:5.0f}-{t1:5.0f}с] {clean}", flush=True)
        else:
            print(f"[{t:5.0f}-{t1:5.0f}с] ок", flush=True)
        t = t1
        i += 1
    stem = a.draft
    for suf in (".draft.mp4", ".mp4"):
        if stem.lower().endswith(suf):
            stem = stem[:-len(suf)]
            break
    dst = a.out or (stem + ".review.json")
    json.dump(notes, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"-> {dst}  (замечаний: {len(notes)})", flush=True)


if __name__ == "__main__":
    try:
        main()
    except ReelsiError as e:
        cli_error(e)
