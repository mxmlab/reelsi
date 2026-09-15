# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка собранного .jsx БЕЗ After Effects.

Единственная настоящая проверка анимаций — рендер в AE, и она такой и остаётся.
Но целый класс поломок виден раньше и без AE: битый синтаксис, слово с переносом
строки, вставка .webp (AE её не импортирует вовсе) или CMYK-JPEG (AE падает на
импорте и не собирает проект целиком), пропавший с диска файл,
камера с перехлёстом клипов, `ci` за пределами массива камер. Всё это раньше
ловилось глазами в AE через 40 минут рото — теперь ловится за секунду.

Проверки — это записанные грабли из ARCHITECTURE.md («Подводные камни»), а не
абстрактный линтер. Каждая привязана к реальному багу.

Запуск:
    python -m core.verify_jsx Reelsi_out/01_ng10.jsx
    python -m core.verify_jsx Reelsi_out/*.jsx --xml Reelsi_out/01_ng10.xml
    python -m core.verify_jsx Reelsi_out            # все .jsx в папке

Код возврата: 0 — чисто, 1 — есть ERR. WARN на код возврата не влияет.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Что именно AE не читает — ОДИН источник правды, insertlib: там же живёт to_ae_image,
# который это чинит. Раньше обе тройки констант стояли и здесь копией: значения совпадали,
# но добавь формат в один файл — верификатор и конвертер молча разошлись бы.
from core.insertlib import (AE_UNSUPPORTED as AE_BAD_IMAGE,   # noqa: E402  webp/avif — слоя не будет вовсе
                       AE_RASTER as RASTER_EXT,          # noqa: E402  растр, у которого проверяем цветовую модель
                       AE_OK_MODES,                      # noqa: E402  CMYK роняет importFile и весь скрипт
                       AE_EXT_FORMAT,                    # noqa: E402, F401  расширение -> формат PIL
                       AE_BAD_VCODEC,                    # noqa: E402  av1/vp9 — тоже роняет importFile
                       image_real_format,                # noqa: E402  содержимое не совпадает с расширением
                       _vcodec)                          # noqa: E402  кодек виден только ffprobe'ом

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".psd", ".ai", ".exr", ".tga"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".m4v", ".webm", ".mpg", ".mpeg"}

# Структуры, которые Python кладёт в шаблон AE_FULL и читает JSX в AE.
# Контракт — в ARCHITECTURE.md, раздел «Контракты данных».
WANTED = ("CAM", "SUBS", "ROTO", "INTRO_GROUPS", "INSERTS", "CAM1_SCALE")


class Report:
    """Накопитель проблем по одному файлу. `scope` — префикс сообщений: в склейке
    «один .jsx на всё» без него не понять, в каком из таймлайнов беда."""

    def __init__(self, path):
        self.path = path
        self.scope = ""
        self.errors = []
        self.warns = []
        self.info = []

    def err(self, msg):
        self.errors.append(self.scope + msg)

    def warn(self, msg):
        self.warns.append(self.scope + msg)

    def note(self, msg):
        self.info.append(self.scope + msg)

    @property
    def ok(self):
        return not self.errors


# ---------------------------------------------------------------- извлечение

def extract_structs(raw):
    """`var NAME=<json>;` -> {NAME: значение}.

    Все структуры собираются `json.dumps` (см. `xml2ae._jd`), поэтому валидный
    JSON. Режем не по `;` — строка может содержать что угодно — а `raw_decode`:
    он разбирает ровно одно значение и сам говорит, где оно кончилось.
    """
    out = {}
    dec = json.JSONDecoder()
    for name in WANTED:
        m = re.search(r"\bvar\s+%s\s*=\s*" % name, raw)
        if not m:
            continue
        try:
            val, _end = dec.raw_decode(raw, m.end())
        except ValueError as e:
            out[name] = ("__BROKEN__", str(e))
            continue
        out[name] = val
    return out


# Сборка «один .jsx на всё» (`xml2ae.build_all`) — это N таймлайнов подряд через этот
# разделитель, и `var CAM=` в файле столько же раз. Разбирали только первое вхождение,
# то есть проверялся ТОЛЬКО первый таймлайн, а остальные молча ехали в AE непроверенными
# (2026-08-01: AV1-вставка во втором таймлайне AutoCut_all.jsx — верификатор чист, AE лёг).
TIMELINE_SEP = re.compile(r"^//\s*=+\s*следующий таймлайн\s*=+\s*$", re.M)


def split_timelines(raw):
    """.jsx -> список кусков-таймлайнов (для одиночного файла — один кусок)."""
    return TIMELINE_SEP.split(raw)


# ---------------------------------------------------------------- проверки

def check_syntax(path, raw, rep):
    """`node --check`. На расширение .jsx node отвечает ERR_UNKNOWN_FILE_EXTENSION,
    поэтому копируем в .js (ровно тот рецепт, что записан в ARCHITECTURE.md)."""
    if not shutil.which("node"):
        rep.warn("node не найден в PATH — синтаксис .jsx не проверен")
        return
    # Имя УНИКАЛЬНОЕ на каждый вызов: фиксированное reelsi_syntax_check.js в общей
    # TEMP расстреливали две сессии разом (своя и чужой прогон) — node читал чужие
    # обрубки и ложно кричал «синтаксис битый» (2026-08-18).
    fd, tmp = tempfile.mkstemp(prefix="reelsi_syntax_", suffix=".js")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        p = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if p.returncode != 0:
            first = (p.stderr or "").strip().splitlines()
            rep.err("синтаксис JS битый: " + (first[1] if len(first) > 1 else (first[0] if first else "?")))
    except Exception as e:                                  # noqa: BLE001
        rep.warn("не удалось прогнать node --check: %s" % e)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def strip_js(raw):
    """Код без комментариев и строковых литералов (на их месте — пробелы, чтобы не
    склеивались соседние слова). Мини-автомат, а не регулярка: в комментариях есть
    апострофы, а в строках — пути с `//`, и одна регулярка на всё путает одно с другим."""
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "/" and i + 1 < n and raw[i + 1] == "/":
            j = raw.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and raw[i + 1] == "*":
            j = raw.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and raw[j] != c:
                j += 2 if raw[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


# Имя вида INS_C2_Y_FR: ВЕРХНИЙ_РЕГИСТР — так в шаблоне названы ВСЕ настройки сборки.
_CONST = re.compile(r"(?<![.\w$])([A-Z][A-Z0-9_]{2,})\b(?!\s*:)")
_NAME = re.compile(r"[A-Za-z_$][\w$]*")


def _declared(code):
    """Все имена, объявленные в коде: `var a=1, B=2, C;`, имена функций и их аргументы."""
    out = set()
    for m in re.finditer(r"\bfunction\s+([A-Za-z_$][\w$]*)?\s*\(([^)]*)\)", code):
        if m.group(1):
            out.add(m.group(1))
        out.update(p.strip() for p in m.group(2).split(",") if p.strip())
    for m in re.finditer(r"\bvar\b", code):
        i, depth, name_here = m.end(), 0, True
        while i < len(code):
            c = code[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:      # вышли из скобок for(var …) — объявление кончилось
                    break
                depth -= 1
            elif depth == 0 and (c == ";" or c == "\n"):
                break
            elif depth == 0 and c == ",":
                name_here = True
            elif depth == 0 and name_here and (c.isalpha() or c in "_$"):
                nm = _NAME.match(code, i)
                out.add(nm.group(0))
                i = nm.end()
                name_here = False
                continue
            elif depth == 0 and c == "=":
                name_here = False
            i += 1
    return out


def check_undeclared(raw, rep):
    """Настройка используется, но нигде не объявлена.

    Геометрия уезжала из ExtendScript в Python по частям, и `var INS_C2_PEAK/INS_C2_Y_FR`
    из шапки убрали, а обращение к INS_C2_Y_FR в ветке кам2 осталось: node --check молчит
    (синтаксис-то целый), а AE падает «INS_C2_Y_FR is undefined» на первой же вставке
    кам2 — после 40 минут сборки (2026-08-12)."""
    code = strip_js(raw)
    declared = _declared(code)
    bad = sorted({m.group(1) for m in _CONST.finditer(code)} - declared)
    for name in bad:
        rep.err("%s используется, но нигде не объявлен — AE упадёт «%s is undefined»"
                % (name, name))


def check_line_separators(raw, rep):
    """Сырые U+2028 (Line Separator) и U+2029 (Paragraph Separator).

    В ES3 (ExtendScript в AE) они считаются переводом строки: строковый литерал
    рвётся и падает импорт всего .jsx. `node --check` этого не видит — в ES2019+
    они в строках легальны. Экранирует их `core/xml2ae/jsutil._js`."""
    if "\u2028" in raw or "\u2029" in raw:
        rep.err("сырые U+2028/U+2029 в тексте .jsx — ExtendScript (ES3) считает их "
                "переводом строки и упадёт при импорте")


def check_bom(path, rep):
    """.jsx пишется с BOM (utf-8-sig) — ExtendScript иначе читает кириллицу мусором."""
    with open(path, "rb") as f:
        if f.read(3) != b"\xef\xbb\xbf":
            rep.warn("нет BOM (utf-8-sig) — AE может прочитать кириллицу мусором")


def _media_problem(media, rep, what):
    """Общая проверка пути к медиа: существует, читаемо для AE."""
    if not media:
        rep.err("%s: пустой путь к медиа" % what)
        return
    ext = os.path.splitext(media)[1].lower()
    if ext in AE_BAD_IMAGE:
        rep.err("%s: %s — AE не импортирует %s, слоя в композиции не появится"
                % (what, os.path.basename(media), ext))
    if not os.path.exists(media):
        rep.err("%s: файла нет на диске — %s" % (what, media))
        return
    if ext in RASTER_EXT:
        bad_fmt = image_real_format(media)
        if bad_fmt:
            rep.err("%s: %s — внутри %s, а расширение %s: "
                    "AE выбирает импортёр по расширению и упадёт на импорте"
                    % (what, os.path.basename(media), bad_fmt, ext))
        mode = _image_mode(media)
        if mode and mode not in AE_OK_MODES:
            rep.err("%s: %s — картинка в %s, AE упадёт на импорте "
                    "(«Unsupported video bit depth») и не соберёт ВЕСЬ проект"
                    % (what, os.path.basename(media), mode))
    if ext in VIDEO_EXT:
        codec = _codec_cached(media)
        if codec in AE_BAD_VCODEC:
            rep.err("%s: %s — видео в %s, AE упадёт на импорте («The source compression "
                    "type is not supported») и не соберёт ВЕСЬ проект"
                    % (what, os.path.basename(media), codec.upper()))


_CODECS = {}


def _codec_cached(path):
    """ffprobe на файл — один раз за прогон: рото-маски и переходы повторяются в каждом .jsx."""
    key = os.path.abspath(path)
    if key not in _CODECS:
        _CODECS[key] = _vcodec(path)
    return _CODECS[key]


def _image_mode(path):
    """Цветовая модель картинки по заголовку (PIL пиксели не читает). -> None если нечем."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return im.mode
    except Exception:                                       # noqa: BLE001
        return None


def check_cam(cams, rep):
    if not isinstance(cams, list) or not cams:
        rep.err("CAM пуст — в проекте не будет ни одной камеры")
        return
    for i, c in enumerate(cams):
        tag = "CAM[%d]" % i
        _media_problem(c.get("path"), rep, tag)
        clips = c.get("clips") or []
        if not clips:
            rep.err("%s: нет ни одного клипа" % tag)
            continue
        prev_end = None
        for j, cl in enumerate(clips):
            if len(cl) < 6:
                rep.err("%s.clips[%d]: ожидалось [start,end,in,out,enabled,scale], пришло %r" % (tag, j, cl))
                continue
            start, end, src_in, src_out = cl[0], cl[1], cl[2], cl[3]
            if not (start < end):
                rep.err("%s.clips[%d]: start >= end (%s >= %s)" % (tag, j, start, end))
            if not (src_in < src_out):
                rep.err("%s.clips[%d]: in >= out в исходнике (%s >= %s)" % (tag, j, src_in, src_out))
            if prev_end is not None and start < prev_end:
                rep.err("%s.clips[%d]: перехлёст с предыдущим клипом (start %s < prev end %s)"
                        % (tag, j, start, prev_end))
            prev_end = end
        rep.note("%s: %d клип(ов)" % (tag, len(clips)))


def check_subs(subs, rep):
    if not isinstance(subs, list):
        rep.err("SUBS не список")
        return
    if not subs:
        rep.note("SUBS: пусто (субтитров нет)")
        return
    prev_start = None
    for i, s in enumerate(subs):
        if len(s) < 6:
            rep.err("SUBS[%d]: ожидалось [start,end,word,hl,row,gend], пришло %r" % (i, s))
            continue
        start, end, word, hl, row, gend = s[0], s[1], s[2], s[3], s[4], s[5]
        if not (start < end):
            rep.err("SUBS[%d] %r: start >= end (%s >= %s)" % (i, word, start, end))
        if not isinstance(word, str) or not word.strip():
            rep.err("SUBS[%d]: пустое слово" % i)
        elif "\n" in word or "\r" in word:
            # Слово, перенесённое в титре Премьера, рвало JS-строку -> «Unable to
            # execute script at line N». Защита в _js()/parse_full, тест — здесь.
            rep.err("SUBS[%d] %r: сырой перенос строки в слове — .jsx не выполнится" % (i, word))
        if hl not in (0, 1):
            rep.err("SUBS[%d] %r: hl=%r, допустимо 0 или 1" % (i, word, hl))
        if not isinstance(row, int) or row < 0:
            rep.err("SUBS[%d] %r: row=%r" % (i, word, row))
        if gend < end:
            rep.err("SUBS[%d] %r: gend < end (%s < %s) — общий конец связки раньше слова"
                    % (i, word, gend, end))
        if prev_start is not None and start < prev_start:
            rep.err("SUBS[%d] %r: слова не по возрастанию времени" % (i, word))
        prev_start = start
    rep.note("SUBS: %d слов, из них жёлтых %d" % (len(subs), sum(1 for s in subs if len(s) > 3 and s[3] == 1)))


def check_roto(roto, ncams, rep):
    if not isinstance(roto, list):
        rep.err("ROTO не список")
        return
    for i, r in enumerate(roto):
        tag = "ROTO[%d]" % i
        ci = r.get("ci", 0)
        # var ci=(rr.ci||0) в шаблоне — индексация ОТ НУЛЯ
        if not isinstance(ci, int) or ci < 0 or (ncams and ci >= ncams):
            rep.err("%s: ci=%r вне диапазона камер (0..%d)" % (tag, ci, max(ncams - 1, 0)))
        ts, te = r.get("ts"), r.get("te")
        if ts is None or te is None or not (ts < te):
            rep.err("%s: ts >= te (%s >= %s)" % (tag, ts, te))
        mf = r.get("mf", 1)
        if not mf or mf < 1:
            rep.err("%s: mf=%r — маска не может быть КРУПНЕЕ исходника" % (tag, mf))
        _media_problem(r.get("mask"), rep, tag)
    if roto:
        rep.note("ROTO: %d кусков" % len(roto))


def check_inserts(inserts, rep):
    if not isinstance(inserts, list):
        rep.err("INSERTS не список")
        return
    for i, x in enumerate(inserts):
        tag = "INSERTS[%d]" % i
        t, style, media = x.get("t"), x.get("style"), x.get("media")
        if t not in ("photo", "video"):
            rep.err("%s: t=%r, допустимо photo|video" % (tag, t))
        if style not in ("cam1", "cam2"):
            rep.err("%s: style=%r, допустимо cam1|cam2" % (tag, style))
        start, end = x.get("start"), x.get("end")
        if start is None or end is None or not (start < end):
            rep.err("%s: start >= end (%s >= %s)" % (tag, start, end))
        # геометрия из Python (задание B): видео — масштаб заполнения + запас панорамы,
        # фото — окна входа/выхода. Поле отсутствует, если размер не прочитался — это ок.
        fit = x.get("fit")
        if fit is not None and not (isinstance(fit, (int, float)) and fit > 0):
            rep.err("%s: fit=%r — масштаб заполнения должен быть числом > 0" % (tag, fit))
        for sk in ("slackx", "slacky"):
            if sk in x and not isinstance(x[sk], (int, float)):
                rep.err("%s: %s=%r — запас панорамы должен быть числом" % (tag, sk, x[sk]))
        for ek in ("en", "ex"):
            if ek in x and not (isinstance(x[ek], (int, float)) and 0 <= x[ek] <= 1):
                rep.err("%s: %s=%r — окно входа/выхода должно быть в [0,1] сек" % (tag, ek, x[ek]))
        _media_problem(media, rep, tag)
        # Тип решает в AE всё: фото = стоп-кадр с наездом, видео = футаж с whoosh.
        # Автоподбор умел подставить mp4 под «фото» (ARCHITECTURE.md, 2026-07-21).
        if media:
            ext = os.path.splitext(media)[1].lower()
            if t == "photo" and ext in VIDEO_EXT:
                rep.err("%s: t=photo, а файл %s — видео" % (tag, ext))
            if t == "video" and ext in IMAGE_EXT:
                rep.err("%s: t=video, а файл %s — картинка" % (tag, ext))
    if inserts:
        rep.note("INSERTS: %d (фото %d, видео %d)"
                 % (len(inserts),
                    sum(1 for x in inserts if x.get("t") == "photo"),
                    sum(1 for x in inserts if x.get("t") == "video")))


def check_intro(groups, rep):
    if not isinstance(groups, list):
        rep.err("INTRO_GROUPS не список")
        return
    # Клип БЕЗ интро — это `[[]]`, одна пустая группа: так его отдаёт scene_plan, и так
    # он лежит во всех собранных .jsx. Шаблон её пропускает (`if(!GRP.length) continue;`),
    # в AE не появляется ничего. Ругаться тут было нельзя: предполёт рендера валил ЛЮБОЙ
    # клип без интро и не пускал его в AE вовсе (найдено 2026-08-14). Пустая группа СРЕДИ
    # непустых — по-прежнему ошибка, ниже она ловится.
    if groups == [[]]:
        return
    for gi, g in enumerate(groups):
        if not g:
            rep.err("INTRO_GROUPS[%d]: пустая группа — прекомп без содержимого" % gi)
            continue
        for li, line in enumerate(g):
            tag = "INTRO_GROUPS[%d][%d]" % (gi, li)
            words = line.get("words") or []
            times = line.get("times") or []
            if not words:
                rep.err("%s: строка без слов" % tag)
            if times and len(times) != len(words):
                rep.err("%s: слов %d, таймингов %d" % (tag, len(words), len(times)))
            for w in words:
                if "\n" in w or "\r" in w:
                    rep.err("%s: перенос строки в слове интро — .jsx не выполнится" % tag)
    if groups:
        rep.note("INTRO_GROUPS: %d прекомп(ов)" % len(groups))


def check_cam1scale(scale, rep):
    if not isinstance(scale, list):
        rep.err("CAM1_SCALE не список")
        return
    prev_f = None
    for i, kf in enumerate(scale):
        if len(kf) < 2:
            rep.err("CAM1_SCALE[%d]: ожидалось [frame, percent], пришло %r" % (i, kf))
            continue
        f, pct = kf[0], kf[1]
        if prev_f is not None and f < prev_f:
            rep.err("CAM1_SCALE[%d]: кадры не по возрастанию (%s после %s)" % (i, f, prev_f))
        if not (10 <= pct <= 400):
            rep.warn("CAM1_SCALE[%d]: масштаб %s%% выглядит дико" % (i, pct))
        prev_f = f
    if scale:
        rep.note("CAM1_SCALE: %d ключей" % len(scale))


# ------------------------------------------------------- сверка с исходником

def cross_check_xml(xml_path, structs, rep, ncams=None):
    """Сверка с XML, из которого собран .jsx: столько ли камер, слов, вставок.

    Ровно та ручная проверка, что описана в ARCHITECTURE.md («parse_full(xml,
    ncams=...) — сверить число камер/вставок»), только автоматом.
    """
    try:
        from core import xml2ae
    except Exception as e:                                  # noqa: BLE001
        rep.warn("не удалось импортировать xml2ae для сверки с XML: %s" % e)
        return
    try:
        meta, cams, subs, ins = xml2ae.parse_full(xml_path, ncams=ncams)
    except Exception as e:                                  # noqa: BLE001
        rep.err("parse_full упал на %s: %s" % (os.path.basename(xml_path), e))
        return
    jsx_cams = structs.get("CAM") or []
    jsx_subs = structs.get("SUBS") or []
    jsx_ins = structs.get("INSERTS") or []
    if len(jsx_cams) != len(cams):
        rep.err("камер в XML %d, в .jsx %d" % (len(cams), len(jsx_cams)))
    if len(jsx_subs) != len(subs):
        rep.err("слов-субтитров в XML %d, в .jsx %d" % (len(subs), len(jsx_subs)))
    if len(jsx_ins) != len(ins):
        rep.err("вставок в XML %d, в .jsx %d" % (len(ins), len(jsx_ins)))
    rep.note("сверено с %s: камер %d, слов %d, вставок %d"
             % (os.path.basename(xml_path), len(cams), len(subs), len(ins)))


# ---------------------------------------------------------------- сборка

def verify(path, xml_path=None, ncams=None):
    rep = Report(path)
    if not os.path.exists(path):
        rep.err("файла нет: %s" % path)
        return rep
    raw = open(path, encoding="utf-8-sig", errors="replace").read()

    check_bom(path, rep)
    check_line_separators(raw, rep)
    check_syntax(path, raw, rep)
    check_undeclared(raw, rep)

    blocks = split_timelines(raw)
    first = None
    for i, blk in enumerate(blocks):
        rep.scope = "таймлайн %d/%d: " % (i + 1, len(blocks)) if len(blocks) > 1 else ""
        structs = check_block(blk, rep)
        if first is None:
            first = structs
    rep.scope = ""

    if xml_path:
        if len(blocks) > 1:
            rep.warn("--xml сверяется только с ПЕРВЫМ таймлайном: в файле их %d" % len(blocks))
        cross_check_xml(xml_path, first or {}, rep, ncams=ncams)
    return rep


def check_block(raw, rep):
    """Все структурные проверки одного таймлайна. -> его structs."""
    structs = extract_structs(raw)
    for name in WANTED:
        v = structs.get(name)
        if v is None:
            rep.err("в .jsx нет структуры %s" % name)
        elif isinstance(v, tuple) and v and v[0] == "__BROKEN__":
            rep.err("%s не разбирается как JSON: %s" % (name, v[1]))
            structs.pop(name)

    cams = structs.get("CAM")
    if isinstance(cams, list):
        check_cam(cams, rep)
    if isinstance(structs.get("SUBS"), list):
        check_subs(structs["SUBS"], rep)
    if isinstance(structs.get("ROTO"), list):
        check_roto(structs["ROTO"], len(cams) if isinstance(cams, list) else 0, rep)
    if isinstance(structs.get("INSERTS"), list):
        check_inserts(structs["INSERTS"], rep)
    if isinstance(structs.get("INTRO_GROUPS"), list):
        check_intro(structs["INTRO_GROUPS"], rep)
    if isinstance(structs.get("CAM1_SCALE"), list):
        check_cam1scale(structs["CAM1_SCALE"], rep)
    return structs


def _targets(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "*.jsx")))
        elif any(ch in p for ch in "*?"):
            out += sorted(glob.glob(p))
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Проверка .jsx без After Effects")
    ap.add_argument("paths", nargs="+", help=".jsx, маска или папка")
    ap.add_argument("--xml", help="исходный XML — сверить число камер/слов/вставок")
    ap.add_argument("--cams", type=int, default=None, help="ncams для parse_full при сверке")
    ap.add_argument("-q", "--quiet", action="store_true", help="только проблемы")
    a = ap.parse_args(argv)

    try:                                                    # кириллица в cp1251-консоли
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # noqa: BLE001
        pass

    files = _targets(a.paths)
    if not files:
        print("[ERR] не найдено ни одного .jsx")
        return 1

    bad = 0
    for p in files:
        rep = verify(p, xml_path=a.xml, ncams=a.cams)
        head = "%s %s" % ("[OK ]" if rep.ok else "[ERR]", os.path.basename(p))
        print(head)
        for m in rep.errors:
            print("   [ERR]  " + m)
        for m in rep.warns:
            print("   [WARN] " + m)
        if not a.quiet:
            for m in rep.info:
                print("   .      " + m)
        if not rep.ok:
            bad += 1

    if len(files) > 1:
        print("\nИтого: %d файл(ов), с ошибками %d" % (len(files), bad))
    print("\nНапоминание: анимации, ключи и рото проверяются ТОЛЬКО рендером в AE.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
