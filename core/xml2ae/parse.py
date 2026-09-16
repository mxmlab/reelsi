# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Разбор xmeml-таймлайна: клипы камер, слова-субтитры, вставки, интро, рото.

Вход — либо наш собственный XML, либо последовательность, выгруженная из Premiere
ПОСЛЕ ручной правки монтажа. Отсюда берут структуру и сборка .jsx, и правка слов.
"""
import os, html
import xml.etree.ElementTree as ET

# HERE — корень репозитория, а НЕ папка пакета: assets/ лежат уровнем выше него.
from core import paths

HERE = paths.ROOT
from core.app_meta import is_out_dir   # noqa: F401  (переэкспорт для сборки)


class Cancelled(Exception):
    """Сборку прервали кнопкой «Стоп». Отдельный тип, а не общая ошибка: вызывающий
    пишет в лог «остановлено», а не «ОШИБКА» с трейсбеком, и не заносит файл в failed."""


def _txt(el, tag, default=None):
    c = el.find(tag)
    return c.text if c is not None and c.text is not None else default


import posixpath
import re


# Разбор pathurl живёт В ОДНОМ месте — в `xmlbuild.unpathurl`. Здесь была вторая копия
# того же преобразования, и копии предсказуемо разъехались: 2026-08-14 починили жёсткий
# бэкслэш в xmlbuild (на Linux «/tmp/a/cam.mp4» превращался в несуществующий
# «\tmp\a\cam.mp4»), а сборка читала ЭТУ копию и продолжала терять все пути.
from core.xmlbuild import unpathurl as _decode_pathurl   # noqa: E402


def _parent_name(path):
    """Имя папки, в которой лежит файл, — НЕ зависящее от разделителя пути.

    Разделитель зависит от системы: `_decode_pathurl` ставит `os.sep`, то есть на
    Windows путь приезжает как его пишет Premiere (`C:\\footage\\cam1\\CLIP.MP4`),
    а на Linux и macOS — со слешами. Здесь годятся оба. На Linux
    `os.path` обратный слеш разделителем не считает — `dirname` отдаёт пустую
    строку, и камера теряет имя: в .jsx уезжает `"name":""`, а в After Effects
    слой остаётся безымянным. Поймано на CI 2026-08-11; до этого сборка шла
    только на Windows, где os.path понимает оба разделителя, и баг не проявлялся.
    Поэтому слеши нормализуем и разбираем posixpath'ом — одинаково на любой ОС.
    """
    p = str(path or "").replace("\\", "/")
    name = posixpath.basename(posixpath.dirname(p))
    # Файл прямо в корне диска: os.path на Windows отдаёт здесь пустую строку,
    # posixpath видит «C:» обычным именем. Ровняемся на Windows — поведение сборки
    # менять нельзя, чинится только потеря имени на других ОС.
    return "" if re.fullmatch(r"[A-Za-z]:", name) else name


def _clip_scale(clip):
    for eff in clip.findall(".//filter/effect"):
        if _txt(eff, "effectid") == "basic":
            for p in eff.findall("parameter"):
                if _txt(p, "parameterid") == "scale":
                    try:
                        return float(_txt(p, "value", "100"))
                    except ValueError:
                        return 100.0
    return 100.0


_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".avif")
CAM_MIN_CLIPS = 5   # трек = камера, если один файл нарезан на >= стольких клипов


def _is_image(p):
    return p and os.path.splitext(p)[1].lower() in _IMG_EXT


def _b64decode(s):
    import base64
    return base64.b64decode(s or "")


def parse_full(xml_path, ncams=None):
    """Parse a timeline into (meta, cam_tracks, subs, inserts).
    Track convention (deterministic when ncams is given): the first `ncams` non-subtitle
    video tracks are cameras 1..N (bottom→top), every track above them holds inserts
    (photo track, then video track). The subtitle track is found by content anywhere.
    When ncams is None, fall back to a heuristic (one file cut into many clips = camera)."""
    root = ET.parse(xml_path).getroot()
    seq = root.find(".//sequence")
    # Частота секвенции. <timebase> — НОМИНАЛ (30 у NTSC), реальную частоту задаёт
    # <ntsc>TRUE</ntsc>: 29.97 = 30*1000/1001. Пока читался один <timebase>, секвенция
    # 29.97 (так её пишут и Премьер, и наш xmlbuild для исходников) считалась 30-й, а
    # кадры XML — они в единицах timebase — делились на 30: за час монтажа импорт
    # уезжал на 3.6 с. timebase 0 (пустой <rate>) ронял всё деление ZeroDivisionError'ом
    # в align.py — пустая частота трактуется как 60. Свои секвенции — 60 без NTSC,
    # для них fps остаётся целым 60 (эталоны не меняются).
    rate = seq.find("rate")
    timebase = int(_txt(rate, "timebase", "60") or 60) or 60
    ntsc = (_txt(rate, "ntsc", "FALSE") or "").strip().upper() == "TRUE"
    fps = timebase * 1000 / 1001 if ntsc else timebase
    fmt = seq.find(".//media/video/format/samplecharacteristics")
    w = int(_txt(fmt, "width", "1080")); h = int(_txt(fmt, "height", "1920"))
    dur = int(_txt(seq, "duration", "0"))
    if dur <= 0:
        # sequence без <duration>: порог «покрывает >= 40% таймлайна» схлопывался в
        # 0.4 кадра, и КАЖДЫЙ не-картиночный трек проходил как камера — трек
        # видео-вставок становился «Камерой 2» (в AE — футаж вставки на весь хрон,
        # в алерте «камер: 2»). Побочно dur=0 отключал рото. Выводим из клипов.
        ends = []
        for c in seq.findall(".//clipitem"):
            try:
                ends.append(int(_txt(c, "end", "0") or 0))
            except (TypeError, ValueError):
                pass
        dur = max(ends) if ends else 0

    # file id -> pathurl (pathurl appears only on a file's first use)
    fmap = {}
    for f in seq.findall(".//clipitem//file"):
        fid = f.get("id"); pu = f.find("pathurl")
        if fid and pu is not None and pu.text and fid not in fmap:
            fmap[fid] = _decode_pathurl(pu.text)

    def clip_path(c):
        f = c.find(".//file")
        return fmap.get(f.get("id")) if f is not None else None

    def is_sub_track(clips):
        return any((c.find(".//filter/effect/effectid") is not None and
                    c.find(".//filter/effect/effectid").text == "GraphicAndType") for c in clips)

    def as_camera(clips):
        rows = []
        for c in clips:
            if _txt(c, "start") is None or _txt(c, "end") is None:
                continue                                   # переход/служебный элемент без таймингов
            rows.append((int(_txt(c, "start")), int(_txt(c, "end")), int(_txt(c, "in", "0")),
                         int(_txt(c, "out", "0")), (_txt(c, "enabled", "TRUE") == "TRUE"),
                         _clip_scale(c)))
        path = next((clip_path(c) for c in clips if clip_path(c)), None)
        return {"path": path, "name": _parent_name(path) if path else "Камера",
                "clips": rows}

    def as_inserts(clips):
        out = []
        for c in clips:
            p = clip_path(c)
            if p and _txt(c, "start") is not None and _txt(c, "end") is not None:
                out.append(dict(type="photo" if _is_image(p) else "video", media=p,
                                start=int(_txt(c, "start")), end=int(_txt(c, "end")),
                                sin=int(_txt(c, "in", "0"))))   # source in-point (для видео)
        return out

    from collections import Counter
    subs, cam_tracks, insert_clips = [], [], []
    non_sub = []                                       # (clips) видео-треков не-субтитров, снизу вверх
    for tr in seq.findall(".//media/video/track"):
        clips = tr.findall("clipitem")
        if not clips:
            continue
        if is_sub_track(clips):
            for c in clips:
                word = html.unescape((_txt(c.find(".//filter/effect"), "name") or "").strip())
                # слово могло перенестись в титре Премьера: убираем перенос строки (склеиваем),
                # прочие пробелы нормализуем — иначе сырой \n рвёт JS-строку в .jsx
                word = " ".join(word.replace("\r", "").replace("\n", "").split())
                if word:
                    subs.append((int(_txt(c, "start")), int(_txt(c, "end")), word))
            continue
        non_sub.append(clips)

    def _looks_camera(clips):
        known = [p for p in (clip_path(c) for c in clips) if p]
        img_track = bool(known) and all(_is_image(p) for p in known)   # трек картинок = не камера
        top_n = Counter(known).most_common(1)[0][1] if known else 0    # один файл, нарезанный на N клипов
        covered = sum(int(_txt(c, "end", "0")) - int(_txt(c, "start", "0")) for c in clips)
        return (not img_track) and (top_n >= CAM_MIN_CLIPS or covered >= 0.4 * max(dur, 1))

    # ncams (если задан) — ВЕРХНЯЯ граница числа камер, а не жёсткое число. Трек становится камерой,
    # только если реально похож на камеру (не картинки, покрывает таймлайн / много клипов одного файла);
    # иначе фото/видео-трек вставок ошибочно берётся как «камера». Частый случай: 1-камерный файл
    # собирают с общим для набора ncams=2 → трек фото-вставок = «Камера 2», и видно лишь одно фото.
    for clips in non_sub:
        if (ncams is None or len(cam_tracks) < ncams) and _looks_camera(clips):
            cam_tracks.append(as_camera(clips))
        else:
            insert_clips += as_inserts(clips)

    subs.sort(key=lambda x: x[0])
    insert_clips.sort(key=lambda x: x["start"])
    # timebase/ntsc — рядом с fps: кадры XML остаются в единицах timebase, а секунды
    # получаются делением на fps. Кому нужно писать частоту ОБРАТНО в XML (кто не
    # делит, а форматирует), берёт номинал отсюда, а не округлённый fps.
    result = dict(w=w, h=h, fps=fps, timebase=timebase, ntsc=ntsc,
                  dur=dur, name=_txt(seq, "name", "Reelsi")), cam_tracks, subs, insert_clips
    return result
