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
import os, sys, argparse, traceback
from core.app_meta import out_dir
from core.cams import DEFAULT_BASE, find_cam_dirs, list_videos
from core.cutjob import CutOptions, process_pair
from core.umsg import ReelsiError, cli_error


def choose(prompt, options):
    try:
        if sys.stdin.isatty():
            import questionary
            r = questionary.select(prompt, choices=options).ask()
            if r is not None:
                return r
    except ReelsiError: raise
    except Exception:
        pass  # questionary нет или нет TTY — спросим цифрой ниже
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


def build_parser():
    # Умолчания нарезки — из CutOptions (core/cutjob.py), своих чисел у CLI нет:
    # раньше те же числа лежали ещё и в argparse.Namespace, который собирал api/jobs.py.
    cut = CutOptions()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--cam1"); ap.add_argument("--cam2")
    ap.add_argument("--cam3"); ap.add_argument("--cam4")
    ap.add_argument("--cams", type=int, default=2, choices=[1, 2, 3, 4],
                    help="сколько камер (мультикам)")
    ap.add_argument("--cam-return", type=int, default=cut.cam_return,
                    help="возврат на камеру 1 каждые N катов")
    ap.add_argument("--big-chunk", type=float, default=cut.big_chunk,
                    help="большой кусок (с) -> камера 1")
    ap.add_argument("--out", default=None, help="single-pair XML path")
    ap.add_argument("--outdir", default=None, help="batch output folder")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--no-srt", action="store_true")
    ap.add_argument("--ae", action="store_true", help="also emit a .jsx for After Effects")
    ap.add_argument("--keep", choices=["first", "last"], default=cut.keep)
    ap.add_argument("--model", default=cut.model)
    ap.add_argument("--scale", type=float, default=cut.scale)
    ap.add_argument("--vad-thresh", type=float, default=cut.vad_thresh)
    ap.add_argument("--min-silence", type=float, default=cut.min_silence)
    ap.add_argument("--pad", type=float, default=cut.pad)
    ap.add_argument("--single", action="store_true", help="одна камера (= --cams 1)")
    ap.add_argument("--no-cut", action="store_true", help="только субтитры, без нарезки")
    ap.add_argument("--aggressive", action="store_true", help="агрессивная нарезка (режет филлеры и паузы)")
    ap.add_argument("--pause-max", type=float, default=cut.pause_max,
                    help="макс. пауза между словами (агрессивный режим)")
    ap.add_argument("--forced-align", action="store_true",
                    help="принудительное выравнивание границ слов (forced alignment)")
    return ap


def main():
    from core.paths import require_source_tree
    require_source_tree()
    ap = build_parser()
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
            process_pair(cams, out_xml, CutOptions.from_namespace(args), model=model)
            ok += 1
        except ReelsiError: raise
        except Exception:
            print(f"  ОШИБКА на #{i}:\n" + traceback.format_exc())
    print(f"\nГотово: {ok}/{len(queue)}.  Файлы в {outdir}")


if __name__ == "__main__":
    try:
        main()
    except ReelsiError as e:
        cli_error(e)
