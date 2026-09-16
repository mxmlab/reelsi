# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Add word subtitles to an ALREADY-EDITED sequence XML (exported from Premiere
after you cut re-takes by hand). Transcribes the camera-1 source, maps each word
onto the edited timeline via the XML's own clips (source in/out -> timeline start),
and inserts a subtitle graphics track + writes an .srt. This decouples cutting
(manual, reliable) from subtitles (automatic, perfectly aligned to the final cut).

CLI:  python reelsi/subtitle_xml.py "EditedFromPremiere.xml"
"""
import os, re, sys
from core import align
from core import xmlbuild
from core.fileio import atomic_text_write
from core.xml2ae import parse_full
from core.app_meta import console_emit, wrap_emit


FPS = 60


def map_words_to_clips(words, clips, min_frames=6, max_hold=0.5, fps=FPS):
    """words: source-second timestamps. clips: (start,end,in,out,enabled,scale) frames.
    Map each word to its timeline position via the clip whose SOURCE range holds it.
    Words in cut-out source regions are dropped. Returns [(w,start,end)] output frames.

    fps — частота секвенции: кадры в clips посчитаны в НЕЙ, а не в 60. Константа
    оставляла 25-кадровую секвенцию без субтитров вовсе: слово «не влезало» ни в один
    клип (GZ, п. E). Дефолт прежний — вызовы без fps не меняются."""
    return align.map_words_to_clips(words, clips, min_frames=min_frames, max_hold=max_hold, fps=fps)


def _build_subtitle_track(sub_words, start_id, fps=60):
    vtrack, n, longs, _ = xmlbuild.build_subtitle_track(sub_words, start_id, fps=fps)
    return vtrack, n, longs


def _sequence_video_close(txt):
    """Индекс закрывающего `</video>` у sequence/media — дорожка вставляется ПЕРЕД ним.

    `txt.index("</video>", <первое <media>>)` попадал в `<video>` внутри `<file>` первого
    клипа: определение файла лежит в той же секвенции, и дорожка субтитров уезжала внутрь
    `<file>` (проверено на tests/fixtures/timeline_nosubs.xml, GZ п. E). Поэтому ищем по
    вложенности: нужное закрытие — то, где счётчик `<video>` обнулился."""
    si = txt.index("<sequence")
    m = re.search(r"<media(?:\s[^>]*)?>", txt[si:])
    if not m:
        raise ValueError("в XML не найден <media> секвенции")
    v = re.search(r"<video(?:\s[^>]*)?>", txt[si + m.end():])
    if not v:
        raise ValueError("в XML не найден <video> секвенции")
    start = si + m.end() + v.start()
    depth = 0
    for tag in re.finditer(r"<(/?)video(?:\s[^>]*)?>", txt[start:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return start + tag.start()
    raise ValueError("в XML не найден закрывающий </video> секвенции")


def add_subtitles(xml_path, out_xml=None, model=None, emit=console_emit):
    emit = wrap_emit(emit)
    meta, cams, _, _ = parse_full(xml_path)
    if not cams or not cams[0]["path"]:
        raise ValueError("В XML не найден путь к видео камеры 1.")   # см. xml2ae: SystemExit не ловится except Exception
    src = cams[0]["path"]
    clips = cams[0]["clips"]
    emit("  камера 1: {name}  ({clips} клипов на таймлайне)",
         name=os.path.basename(src), clips=len(clips))

    # transcript (cache next to the source) — ключ кэша тот же, что в reelsi.py:
    # с моделью в имени, иначе medium-кэш подхватывался как large-v3
    from core import transcribe
    cache = transcribe.words_cache_path(src)
    words = transcribe.load_words_cache(cache)
    if words is None:                        # старый кэш без модели в имени = large-v3
        legacy = os.path.splitext(src)[0] + ".words.json"
        if os.path.exists(legacy):
            words = transcribe.load_words_cache(legacy)
            if words:
                emit("  транскрипт из старого кэша (имя без модели)")
    if words:
        emit("  транскрипт из кэша: {count} слов", count=len(words))
    else:
        emit("  транскрибирую исходник (Whisper)...")
        import tempfile, shutil
        from core import sync
        work = tempfile.mkdtemp(prefix="subxml_")    # раньше не удалялась: wav целого ролика в %TEMP%
        try:
            wav = os.path.join(work, "a.wav")
            sync.extract_audio(src, wav)
            words = transcribe.transcribe(wav, model=model)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        transcribe.save_words_cache(cache, words)
        emit("  {count} слов", count=len(words))

    words = align.clamp_word_times(words)
    sub_words = map_words_to_clips(words, clips, fps=meta["fps"])
    emit("  субтитров на таймлайне: {count}", count=len(sub_words))

    # insert a subtitle track + write .srt
    txt = open(xml_path, encoding="utf-8").read()
    maxid = max([int(m) for m in re.findall(r'clipitem-(\d+)', txt)] + [1000]) + 1
    subtrack, n, longs = _build_subtitle_track(sub_words, maxid, fps=meta["fps"])
    vend = _sequence_video_close(txt)
    new = txt[:vend] + subtrack + txt[vend:]
    out_xml = out_xml or (os.path.splitext(xml_path)[0] + "_subs.xml")
    # атомарно: XML с дорожкой субтитров — результат шага, усечённый файл не соберёшь
    atomic_text_write(out_xml, new, encoding="UTF-8")
    nsrt = align.make_srt(sub_words, os.path.splitext(out_xml)[0] + ".srt", fps=meta["fps"])
    if longs:
        emit("  ⚠ слишком длинные (без титра): {words}", words=", ".join(longs))
    emit("  -> {name}  ({subs} субтитров) + .srt ({nsrt})",
         name=os.path.basename(out_xml), subs=n, nsrt=nsrt)
    return {"out": out_xml, "subtitles": n, "long_words": longs}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python subtitle_xml.py edited.xml [out.xml]")
    try:
        add_subtitles(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    except ValueError as e:
        raise SystemExit(str(e))
