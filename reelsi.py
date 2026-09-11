# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Reelsi — interactive CLI.

Single or BATCH: queue up several camera pairs, leave it running, and it writes
numbered XML + SRT for every pair into one output folder.

Per pair: sync -> cut silences -> multicam -> remove repeated phrases ->
word-by-word subtitle graphics -> Premiere FCP7 XML (+ .srt).

Usage:
    python reelsi/reelsi.py                      # interactive queue -> batch
    python reelsi/reelsi.py --outdir Готово       # custom output folder
    python reelsi/reelsi.py --cam1 a.mp4 --cam2 b.mp4 --out one.xml   # single
    python reelsi/reelsi.py --no-subs --no-dedup  # just the multicam cut
"""
import os, sys, argparse, tempfile, json, traceback, re, subprocess, shutil, uuid
HERE = os.path.dirname(os.path.abspath(__file__))
from core import sync
from core import vad
from core import align
from core import xmlbuild
from core.app_meta import child_env, console_emit, env, module_cmd, out_dir, wrap_emit

# Рабочая папка с исходниками (камеры, музыка, insert_library) — на уровень выше
# самого пакета. Переопределяется переменной окружения REELSI_BASE или флагом --base.
DEFAULT_BASE = env("BASE") or os.path.dirname(HERE)


def _forced_align(wav, words, emit=console_emit):

    """Уточнить тайминги слов через wav2vec2 в ОТДЕЛЬНОМ процессе (иначе cuDNN торча
    конфликтует с ctranslate2 Whisper → EXIT 127). Падение = возвращаем как было."""
    emit = wrap_emit(emit)
    d = tempfile.gettempdir()
    tag = uuid.uuid4().hex[:8]                # уникально: параллельные запуски не перетирают
    fin = os.path.join(d, f"_ac_fa_in_{tag}.json")
    fout = os.path.join(d, f"_ac_fa_out_{tag}.json")
    try:
        json.dump(words, open(fin, "w", encoding="utf-8"), ensure_ascii=False)
        r = subprocess.run(module_cmd("falign_cli", wav, fin, fout),
                           capture_output=True, text=True, encoding="utf-8", timeout=900,
                           env=child_env())
        for ln in (r.stdout or "").splitlines():
            if ln.strip():
                emit("  {line}", line=ln.strip())
        if os.path.exists(fout):
            return json.load(open(fout, encoding="utf-8"))
        emit("  forced align: нет результата {err}",
             err=(r.stderr or "")[-160:].replace("\n", " "))
    except Exception as e:
        emit("  forced align пропущен: {err}", err=str(e))
    finally:
        for f in (fin, fout):
            try:
                os.remove(f)
            except OSError:
                pass
    return words


def _is_cam_dir(d):
    # Признак «папка камеры» — на оба языка сразу: русское «камер…» или английское
    # «camera…» либо «cam» прямо перед цифрой («cam3»). На чистой английской
    # установке русских папок нет вовсе — без этого /api/cams отвечал «нет папок».
    low = d.lower()
    return low.startswith("камер") or low.startswith("camera") or bool(re.match(r"^cam\d", low))


def find_cam_dirs(base):
    subs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    # Order by the NUMBER in the folder name ("камера1" -> 1) so camera 1 is always
    # first regardless of letter case. Plain/casefold sort would put "Камера2"
    # (uppercase К) before "камера1" and swap the cameras -> cut would follow cam2.
    cams = [d for d in subs if _is_cam_dir(d)]

    def key(d):
        m = re.search(r"(\d+)", d)
        return (int(m.group(1)) if m else 10**9, d.casefold())

    return [os.path.join(base, d) for d in sorted(cams, key=key)]


def list_videos(d):
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".mp4", ".mov", ".mxf")))


def choose(prompt, options):
    try:
        if sys.stdin.isatty():
            import questionary
            r = questionary.select(prompt, choices=options).ask()
            if r is not None:
                return r
    except Exception:
        pass
    print("\n" + prompt)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    while True:
        r = input("Номер: ").strip()
        if r.isdigit() and 1 <= int(r) <= len(options):
            return options[int(r) - 1]
        print("Неверный номер.")


def build_queue(base, n_cams=2):
    """n_cams cameras (1..4). Returns a list of camera-path tuples."""
    dirs = find_cam_dirs(base)
    if len(dirs) < n_cams:
        sys.exit(f"Нужно {n_cams} папок камер, нашёл {len(dirs)} в {base}")
    dirs = dirs[:n_cams]
    vids = [list_videos(d) for d in dirs]
    labels = [os.path.basename(d) for d in dirs]
    queue = []
    print("Камеры: " + ", ".join(labels))
    print("Набери очередь. После каждой спрошу, добавить ли ещё.\n")
    while True:
        print(f"=== #{len(queue) + 1} ===")
        picks = tuple(os.path.join(dirs[k], choose(
            f"Видео ({labels[k]}):" if n_cams == 1 else f"Камера {k+1} ({labels[k]}):", vids[k]))
            for k in range(n_cams))
        if picks in queue:
            print("  ! уже в очереди — пропускаю")
        else:
            queue.append(picks)
            print(f"  + {' + '.join(os.path.basename(p) for p in picks)}   (в очереди: {len(queue)})")
        more = input("Добавить ещё? [Y/n]: ").strip().lower()
        if more in ("n", "no", "нет", "н"):
            break
    print(f"\nИтоговая очередь ({len(queue)}):")
    for i, picks in enumerate(queue, 1):
        print(f"  {i}. " + " + ".join(os.path.basename(p) for p in picks))
    ans = input("Запускаю обработку? [Y/n]: ").strip().lower()
    if ans in ("n", "no", "нет", "н"):
        sys.exit("Отменено.")
    return queue


def process_pair(cams, out_xml, args, model=None, emit=console_emit, music_path=None):
    """cams: list of camera file paths (1..4), camera 1 first."""
    if isinstance(cams, str):
        cams = [cams]
    cams = [c for c in cams if c]             # drop empty slots
    out_base = os.path.splitext(out_xml)[0]
    no_cut = getattr(args, "no_cut", False)   # only subtitles, don't cut
    if no_cut:
        cams = cams[:1]                       # subtitles-only works on one video
    N = len(cams)
    if no_cut:
        emit("  Камеры: {cams}   (только субтитры)", cams=", ".join(os.path.basename(c) for c in cams))
    else:
        emit("  Камеры: {cams}", cams=", ".join(os.path.basename(c) for c in cams))
    work = tempfile.mkdtemp(prefix="reelsi_")
    try:
        wavs = [os.path.join(work, f"a{k}.wav") for k in range(N)]
        emit("  извлекаю аудио...")
        sync.extract_audio(cams[0], wavs[0])

        offsets = [0.0]
        if no_cut:
            import wave
            with wave.open(wavs[0]) as wf:
                dur = wf.getnframes() / float(wf.getframerate())
            segments = [(0.0, dur)]           # whole clip, uncut
            emit("  без нарезки: весь ролик {dur:.0f}s", dur=dur)
        else:
            for k in range(1, N):
                sync.extract_audio(cams[k], wavs[k])
                off, conf = sync.find_offset(wavs[0], wavs[k])
                offsets.append(off)
                emit("  синк К{cam}: {off:+.3f}s (увер. {conf:.2f})",
                     cam=k + 1, off=off, conf=conf)
            segments = vad.speech_intervals(wavs[0], thresh_db=args.vad_thresh,
                                            min_silence=args.min_silence, pad=args.pad)
            emit("  речь: {count} сегментов", count=len(segments))

        words = None
        if not (args.no_subs and args.no_dedup):
            # кэш — по ИСХОДНИКУ (переживает смену outdir/номера в очереди, как в
            # subtitle_xml); в имени ключ модели, иначе medium-кэш выдавался за large-v3
            from core import transcribe
            src_cache = transcribe.words_cache_path(cams[0], args.model)
            words = transcribe.load_words_cache(src_cache)
            if words is None:
                # кэши старого формата (без модели в имени) писались дефолтной large-v3 —
                # берём их только когда её и просят, иначе честно транскрибируем заново
                if args.model == transcribe.DEFAULT_MODEL_SIZE:
                    for lg in (os.path.splitext(cams[0])[0] + ".words.json",
                               out_base + ".words.json"):
                        if os.path.exists(lg):
                            words = transcribe.load_words_cache(lg)
                            if words:
                                emit("  транскрипт из старого кэша (имя без модели)")
                                break
            if words:
                emit("  транскрипт из кэша: {count} слов", count=len(words))
            else:
                emit("  транскрибирую (Whisper {model})...", model=args.model)
                words = transcribe.transcribe(wavs[0], model_size=args.model, model=model)
                transcribe.save_words_cache(src_cache, words)
                emit("  {count} слов", count=len(words))

        if words:
            words = align.clamp_word_times(words)   # fix Whisper's ballooned durations
            if getattr(args, "forced_align", False):
                words = _forced_align(wavs[0], words, emit=emit)
    finally:
        shutil.rmtree(work, ignore_errors=True)     # wav-ы пары (гигабайты в %TEMP%)

    if words and not no_cut:
        aggressive = getattr(args, "aggressive", False)
        ranges = []
        if not args.no_dedup:
            if getattr(args, "restarts", False):     # ИИ-нарезка: ловим и рестарты (near-повторы)
                drep, rep_log = align.find_restarts(words, keep=args.keep)
            else:
                drep, rep_log = align.find_repeat_ranges(words, keep=args.keep)
            ranges += drep
            if rep_log:
                emit("  повторов/рестартов удалено: {count}", count=len(rep_log))
                for _s, _e, _ph in rep_log:
                    emit("    − «{phrase}»", phrase=_ph)
        if aggressive:                          # extras only in aggressive mode
            fillers = [(w["start"], w["end"]) for w in words if align.is_filler(w["w"])]
            if fillers:
                emit("  филлеров (хм/кашель) вырезано: {count}", count=len(fillers))
            gaps = align.dead_air_ranges(words, max_pause=getattr(args, "pause_max", 1.0))
            if gaps:
                emit("  пауз/мёртвого воздуха вырезано: {count}", count=len(gaps))
            ranges += fillers + gaps
        if ranges:
            segments = align.subtract_ranges(segments, ranges)
            words = [w for w in words if not any(a <= 0.5*(w['start']+w['end']) < b for a, b in ranges)]
        if aggressive:
            # VAD ловит вздохи/питьё воды/шум как «речь» (там есть звук), но Whisper в них
            # ничего не распознаёт → сегмент без единого слова = не речь → выкидываем.
            # Убирает и стартовые вздохи (до первого слова dead_air их не трогает).
            def _has_word(s, e):        # пересечение (устойчиво к сдвигу таймингов forced-align)
                return any(w["start"] < e + 0.05 and w["end"] > s - 0.05 for w in words)
            n0 = len(segments)
            segments = [(s, e) for s, e in segments if _has_word(s, e)]
            if n0 - len(segments):
                emit("  сегментов без речи (вздохи/вода/шум) убрано: {count}", count=n0 - len(segments))

    # keep only frame-positive segments so the camera assignment lines up 1:1
    segments = [(s, e) for s, e in segments if round(e*60) - round(s*60) > 0]
    assign = None
    if N > 1 and not no_cut:
        assign = align.assign_cameras(segments, N,
                                      return_every=getattr(args, "cam_return", 2),
                                      big_chunk_sec=getattr(args, "big_chunk", 6.0))

    mapped = align.map_words(words, segments) if words else None
    sub_words = mapped if (mapped and not args.no_subs) else None
    info = xmlbuild.build(cams, segments, offsets, out_xml, assign=assign,
                          scale=args.scale, sub_words=sub_words, music_path=music_path)
    if info.get("long_words"):
        emit("  ⚠ слишком длинные слова (>19 букв) — без титра, добавь вручную: {words}",
             words=", ".join(info["long_words"]))
    extra = ""
    if mapped and not args.no_srt:
        n = align.make_srt(mapped, out_base + ".srt")
        extra += f" + .srt ({n})"
    if sub_words and args.ae:
        try:
            from core import xml2ae
            _, nc, ns = xml2ae.to_ae_full(out_xml, out_base + ".jsx")
            extra += f" + .jsx ({nc}кл/{ns}суб)"
        except Exception as e:
            extra += f" [jsx err: {e}]"
    emit("  -> {name}  ({sec:.0f}s, {segs} сег., {subs} суб.){extra}",
         name=os.path.basename(out_xml), sec=info['total_s'],
         segs=info['segments'], subs=info['subtitles'], extra=extra)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--cam1"); ap.add_argument("--cam2")
    ap.add_argument("--cam3"); ap.add_argument("--cam4")
    ap.add_argument("--cams", type=int, default=2, choices=[1, 2, 3, 4],
                    help="сколько камер (мультикам)")
    ap.add_argument("--cam-return", type=int, default=2, help="возврат на камеру 1 каждые N катов")
    ap.add_argument("--big-chunk", type=float, default=6.0, help="большой кусок (с) -> камера 1")
    ap.add_argument("--out", default=None, help="single-pair XML path")
    ap.add_argument("--outdir", default=None, help="batch output folder")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--no-srt", action="store_true")
    ap.add_argument("--ae", action="store_true", help="also emit a .jsx for After Effects")
    ap.add_argument("--keep", choices=["first", "last"], default="last")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--scale", type=float, default=50.4)
    ap.add_argument("--vad-thresh", type=float, default=18.0)
    ap.add_argument("--min-silence", type=float, default=0.30)
    ap.add_argument("--pad", type=float, default=0.08)
    ap.add_argument("--single", action="store_true", help="одна камера (= --cams 1)")
    ap.add_argument("--no-cut", action="store_true", help="только субтитры, без нарезки")
    ap.add_argument("--aggressive", action="store_true", help="агрессивная нарезка (режет филлеры и паузы)")
    ap.add_argument("--pause-max", type=float, default=1.0, help="макс. пауза между словами (агрессивный режим)")
    args = ap.parse_args()
    n_cams = 1 if args.single else args.cams

    if args.cam1:
        queue = [tuple(c for c in (args.cam1, args.cam2, args.cam3, args.cam4) if c)]
    else:
        queue = build_queue(args.base, n_cams=n_cams)

    outdir = args.outdir or out_dir(args.base)
    os.makedirs(outdir, exist_ok=True)
    print(f"\nОчередь: {len(queue)} пар.  Папка вывода: {outdir}\n")

    # preload Whisper once for the whole batch
    model = None
    if not (args.no_subs and args.no_dedup):
        print(f"Гружу модель Whisper {args.model} (один раз на всю очередь)...")
        from core import transcribe
        model = transcribe.get_model(args.model)

    ok = 0
    for i, cams in enumerate(queue, 1):
        cams = list(cams)
        stem = os.path.splitext(os.path.basename(cams[0]))[0]
        if len(queue) == 1 and args.out:
            out_xml = args.out
        else:
            out_xml = os.path.join(outdir, f"{i:02d}_{stem}.xml")
        print(f"[{i}/{len(queue)}] {stem}")
        try:
            process_pair(cams, out_xml, args, model=model)
            ok += 1
        except Exception:
            print(f"  ОШИБКА на #{i}:\n" + traceback.format_exc())
    print(f"\nГотово: {ok}/{len(queue)}.  Файлы в {outdir}")


if __name__ == "__main__":
    main()
