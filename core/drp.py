# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Чтение и запись проектов DaVinci Resolve (`.drp`).

Нужно потому, что графику (титры) не переносит НИ ОДИН обменный формат — ни
FCP7 XML, ни FCPXML, ни AAF/OTIO. Родной формат Resolve — единственный, который
её несёт. Полное описание формата, границы возможного и план работ — в
`DRP_SPEC.md`; здесь только то, что уже проверено на реальном проекте.

Готово: `build()` собирает проект целиком из `drp_template.drp` — камеры, нарезка,
синхрон, раскладка мультикама, пословные титры Fusion и вставки (фото/видео).
Проверено на Resolve Studio 21.0.3: картинка и звук на месте.

НЕ готово: кнопки в интерфейсе (фаза 5) и прогон по всем XML (фаза 6).

Формат приватный и недокументированный. При обновлении Resolve сверять заново —
проще всего экспортом того же таймлайна из Resolve и построчным сравнением.
"""
import os
import re
import struct
import uuid
import zipfile
import zlib

from core import paths
from core.xmltext import xml_text as _esc

SRC_FPS = 30000 / 1001                 # NTSC — в каком темпе считаются таймкоды камер

KEY_DATA = "0_data".encode("utf-16-be")
NODES_MARK = b"\x78\xda"               # zlib(9) — начало графа нодов внутри композиции


# ---- контейнер композиции --------------------------------------------------

def split_comp(hex_blob):
    """`<CompositionBA>` -> (префикс, текст композиции, граф нодов, смещение длины).

    Префикс отдаём как есть: в нём бинарные ключи, воспроизводить их не нужно —
    достаточно поправить в нём поле длины при сборке (см. `join_comp`).
    """
    outer = zlib.decompress(bytes.fromhex(hex_blob)[4:])
    k = outer.index(KEY_DATA) + len(KEY_DATA)
    len_off = k + 5                    # 4 байта типа + 1 байт флага
    start = len_off + 4
    declared = int.from_bytes(outer[len_off:len_off + 4], "big")
    if declared != len(outer) - start:
        raise ValueError(f"контейнер повреждён: заявлено {declared} байт данных, "
                         f"фактически {len(outer) - start}")
    i = outer.index(NODES_MARK, start)
    return outer[:start], outer[start:i - 4], zlib.decompress(outer[i:]), len_off


def join_comp(prefix, comp_text, nodes, len_off):
    """Обратно в hex. Поле длины данных ОБЯЗАТЕЛЬНО пересчитывается: Resolve
    читает по нему, и если оставить старое, титр открывается пустым (чёрный слой —
    именно этот баг, ловился долго)."""
    payload = comp_text + len(nodes).to_bytes(4, "little") + zlib.compress(nodes, 9)
    buf = bytearray(prefix)
    buf[len_off:len_off + 4] = len(payload).to_bytes(4, "big")
    outer = bytes(buf) + payload
    return (len(outer).to_bytes(4, "big") + zlib.compress(outer, 9)).hex()


def timemap(frames, fps=60):
    """`MediaTimemapBA` титра Fusion — один double, длительность клипа в секундах."""
    return "02" + struct.pack(">d", frames / fps).hex()


def media_timemap(dur_s, src_fps=SRC_FPS, nominal=None):
    """`MediaTimemapBA` медиаклипа — ПЯТЬ doubles, карта самого исходника.

    Одна и та же у всех клипов одной камеры: это описание файла, а не куска.
    Снято с рабочего проекта: [L, 0, L + 1/60, 0, L], где L = (кадров−1)/30.
    Если поставить сюда длительность клипа (как у титра), картинка застывает.
    Делитель — НОМИНАЛ частоты (30 для NTSC 29.97, 25 для PAL 25): так же, как
    в `media_start_time`; у PAL номинал совпадает с реальной частотой.
    """
    if nominal is None:
        nominal = round(src_fps)
    frames = round(dur_s * src_fps)
    L = (frames - 1) / nominal
    mid = (2 * frames - 1) / (2 * nominal)     # НЕ L + 1/60: порядок операций даёт
    return "02" + b"".join(struct.pack(">d", x)     # другой последний бит мантиссы
                           for x in (L, 0.0, mid, 0.0, L)).hex()


def _drop_frame_fps(fps):
    """Drop-frame бывает только у NTSC-производных (29.97/59.94) — у PAL-камер
    (25) счётчик кадров не пропускает ничего, хотя точку с запятой ffprobe
    отдаёт и там (это запись формата, а не признак drop-frame)."""
    return abs(fps - 30000 / 1001) < 0.01 or abs(fps - 60000 / 1001) < 0.02


def timecode_frames(tc, fps=None):
    """Таймкод -> номер кадра. `;` = drop-frame: счётчик пропускает кадры в начале
    каждой минуты, кроме каждой десятой. Пропуск на минуту = 2 * round(номинал/30):
    у 29.97 это 2, у 59.94 — 4 (жёсткая двойка давала 59.94-часу 3601.8 с вместо
    3600 и уезжающий на 1.8 с клип). Без fps — legacy-NTSC (счёт на 30, drop при
    `;`). С fps — счёт в номинале этой частоты (round), drop только у 29.97/59.94:
    для камер PAL 25 счёт идёт на 25 без пропусков."""
    parts = [int(x) for x in tc.replace(";", ":").split(":")]
    h, m, s, f = (parts + [0, 0, 0, 0])[:4]
    if fps is None:
        nominal, drop = 30, ";" in tc
    else:
        nominal = round(fps)
        drop = ";" in tc and _drop_frame_fps(fps)
    frames = (h * 3600 + m * 60 + s) * nominal + f
    if drop:
        drop_per_min = 2 * round(nominal / 30)
        total_min = h * 60 + m
        frames -= drop_per_min * (total_min - total_min // 10)
    return frames


def media_start_time(tc, fps=None, nominal=None):
    """`MediaStartTime` у клипа таймлайна — кадры в НОМИНАЛЬНЫХ секундах (÷30).

    Не путать с `MediaExtents[0]` в медиапуле: там та же величина, но в реальных
    (÷29.97). Для `01;57;31;11` это 7044.3 против 7051.3443 — разошлись на семь
    секунд, и клип уезжает. С fps (25 у PAL-камер) номинал совпадает с частотой,
    и оба деления дают одно и то же.
    """
    if fps is None:
        return timecode_frames(tc) / (nominal if nominal is not None else 30)
    return timecode_frames(tc, fps) / round(fps)


# ---- титры -----------------------------------------------------------------

def set_input(nodes, key, value):
    """Заменить вход Text+ или ДОБАВИТЬ его.

    Добавлять приходится потому, что Fusion не сериализует значения по умолчанию:
    у белого титра нет ни `Red1`, ни `Size` — заменять там нечего.
    """
    pat = re.compile(r"(\b" + re.escape(key) + r" = Input \{ Value = )[^,}]*(,?\s*\})")
    if pat.search(nodes):
        return pat.sub(lambda m: m.group(1) + value + m.group(2), nodes, count=1)
    return nodes.replace("StyledText = Input",
                         f"{key} = Input {{ Value = {value}, }}, StyledText = Input", 1)


def set_text(nodes, text):
    """Подставить текст в `StyledText` титра.

    Значение в композиции Fusion — ЛИТЕРАЛ Lua, а не XML-строка: обратный слеш и
    перевод строки рвут композицию (`\\` начинает escape-последовательность, голый
    перевод строки закрывает литерал). Экранируем их ДО кавычек: порядок важен,
    иначе слеши, добавленные переводами строк, удвоятся. Кавычка по-прежнему
    заменяется апострофом — так было и так задумано (`"` внутри `"…"` не escape)."""
    def esc(t):
        return (t.replace("\\", "\\\\").replace("\r\n", "\\n").replace("\r", "\\n")
                 .replace("\n", "\\n").replace('"', "'"))

    return re.sub(r'(StyledText = Input \{ Value = ")[^"]*(")',
                  lambda m: m.group(1) + esc(text) + m.group(2),
                  nodes, count=1)


def retime_comp(comp_text, frames):
    """Диапазоны композиции под длительность титра."""
    c = comp_text
    for k in ("RenderRange", "GlobalRange"):
        c = re.sub(k + r" = \{ \d+, \d+ \}", f"{k} = {{ 0, {frames - 1} }}", c, count=1)
    c = re.sub(r"GlobalEnd = \d+", f"GlobalEnd = {frames - 1}", c, count=1)
    return re.sub(r"CurrentTime = \d+", "CurrentTime = 0", c, count=1)


GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def new_ids(element_xml):
    """Свежие DbId: они обязаны быть уникальны на весь проект."""
    return re.sub(r'DbId="' + GUID + r'"', lambda m: f'DbId="{uuid.uuid4()}"', element_xml)


def new_pool_ids(element_xml):
    """Свежие идентификаторы у КЛОНИРОВАННОЙ записи медиапула.

    Мало заменить `DbId="…"`: своя личность у записи хранится ещё в
    `<UniqueMediaPoolItemId>` и в GUID внутри zstd-блоба. Если их не обновить,
    две записи выглядят для Resolve одним и тем же медиа — первая камера уходит
    в офлайн, вторая показывает застывший кадр, звук есть, но не играет.
    `<MpFolder>` НЕ трогаем: он указывает на папку Master.
    """
    # Единая таблица старый -> новый. Блобы ССЫЛАЮТСЯ на элементы XML: например
    # ключ MediaRef в FieldsBlob — это DbId элемента <BtAudioInfo>, то есть звук
    # записи. Если генерировать GUID в XML и в блобе порознь, ссылка рвётся и
    # звука нет: клипы зелёные, но без волны. Поэтому одна таблица на всё.
    mapping = {}

    def remap(old):
        if old not in mapping:
            mapping[old] = str(uuid.uuid4())
        return mapping[old]

    el = re.sub(r'(DbId=")(' + GUID + r')(")',
                lambda m: m.group(1) + remap(m.group(2)) + m.group(3), element_xml)
    el = re.sub(r"(<UniqueMediaPoolItemId>)(" + GUID + r")(<)",
                lambda m: m.group(1) + remap(m.group(2)) + m.group(3), el, count=1)

    def fresh(data):
        """GUID лежат в UTF-16BE и UTF-8, все одной длины — меняем на месте.
        Известные по таблице получают свою пару, остальные — новый идентификатор."""
        for enc in ("utf-16-be", "utf-8"):
            for old in sorted(set(re.findall(GUID, data.decode(enc, "replace")))):
                data = data.replace(old.encode(enc), remap(old).encode(enc))
        return data

    def fix_blob(m):
        tag, h = m.group(1), m.group(2)
        try:
            if is_zstd_blob(h):
                head, data = unpack_fields(h)
                return f"<{tag}>" + pack_fields(head, fresh(data)) + f"</{tag}>"
            return f"<{tag}>" + fresh(bytes.fromhex(h)).hex() + f"</{tag}>"
        except Exception:
            return m.group(0)                  # чужой блоб — не трогаем

    # ВСЕ блобы, а не только FieldsBlob: свои идентификаторы есть и у <Time>,
    # и у <TracksBA> — это личность звуковой дорожки исходника.
    return re.sub(r"<(\w+)>([0-9a-f]{40,})</\1>", fix_blob, el)


# ---- protobuf (блоб <Clip> в записи медиапула) ------------------------------

def _varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1
        v |= (x & 0x7F) << s; s += 7
        if not x & 0x80:
            return v, i


def _put_varint(v):
    out = bytearray()
    while True:
        x = v & 0x7F; v >>= 7
        out.append(x | (0x80 if v else 0))
        if not v:
            return bytes(out)


def pb_fields(data):
    """-> [(номер поля, тип, начало значения, конец значения)] верхнего уровня."""
    out, i = [], 0
    while i < len(data):
        try:
            tag, i = _varint(data, i)
        except IndexError:
            break
        fn, wt = tag >> 3, tag & 7
        if wt == 2:
            ln, j = _varint(data, i)
            out.append((fn, wt, j, j + ln)); i = j + ln
        elif wt == 0:
            _, j = _varint(data, i)
            out.append((fn, wt, i, j)); i = j
        elif wt == 5:
            out.append((fn, wt, i, i + 4)); i += 4
        elif wt == 1:
            out.append((fn, wt, i, i + 8)); i += 8
        else:
            break
    return out


def pb_set_str(data, field_no, text):
    """Заменить строковое поле; длина пересчитывается, поэтому не на месте."""
    new = text.encode("utf-8")
    for fn, wt, a, b in pb_fields(data):
        if fn == field_no and wt == 2:
            head = _put_varint((fn << 3) | 2) + _put_varint(len(new))
            start = a - len(_put_varint(b - a)) - len(_put_varint((fn << 3) | 2))
            return data[:start] + head + new + data[b:]
    return data


def pb_get_str(data, field_no):
    for fn, wt, a, b in pb_fields(data):
        if fn == field_no and wt == 2:
            return data[a:b].decode("utf-8", "replace")
    return None


# ---- файл ------------------------------------------------------------------

def read(path):
    """-> dict имя_в_архиве -> bytes."""
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def write(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def seq_name(files):
    """Имя файла таймлайна внутри архива (uuid в названии у каждого проекта свой)."""
    return next(n for n in files if n.startswith("SeqContainer/"))


# ---- медиапул --------------------------------------------------------------

ZSTD_MAGIC = "28b52ffd"
KEY_EXTENTS = "MediaExtents".encode("utf-16-be")


def timecode_seconds(tc, fps=None):
    """Стартовый таймкод исходника в РЕАЛЬНЫХ секундах (÷29.97 для NTSC, ÷25 для
    PAL) — так он лежит в `MediaExtents` медиапула. Для `MediaStartTime` у клипа
    нужны номинальные, см. `media_start_time`. Без fps — legacy÷SRC_FPS."""
    return timecode_frames(tc, fps) / (fps if fps else SRC_FPS)


def is_zstd_blob(hex_blob):
    raw = bytes.fromhex(hex_blob)
    return len(raw) > 13 and raw[9:13].hex() == ZSTD_MAGIC


def unpack_fields(hex_blob):
    """`FieldsBlob` медиапула -> (голова 9 байт, распакованные байты)."""
    import zstandard
    raw = bytes.fromhex(hex_blob)
    return raw[:9], zstandard.ZstdDecompressor().decompress(raw[9:])


def pack_fields(head, data):
    """Обратно. Второе поле шапки = длина всего блоба минус 8 — сверено на живом файле."""
    import zstandard
    body = head[8:9] + zstandard.ZstdCompressor(level=19).compress(data)
    return (head[:4] + len(body).to_bytes(4, "big") + body).hex()


def set_media_extents(hex_blob, start_s, dur_s):
    """Переписать [стартовый таймкод, длительность] в секундах.

    Поле фиксированного размера (два double LE), поэтому правится на месте по
    имени ключа — разбирать весь контейнер не нужно. Resolve берёт длительность
    ИМЕННО отсюда, а не переспрашивает файл: проверено перенацеливанием проекта
    на исходник другой длины.
    """
    head, data = unpack_fields(hex_blob)
    i = data.find(KEY_EXTENTS)
    if i < 0:
        raise ValueError("в блобе нет MediaExtents")
    o = i + len(KEY_EXTENTS) + 4 + 1 + 4        # тип + флаг + длина
    buf = bytearray(data)
    buf[o:o + 16] = struct.pack("<dd", start_s, dur_s)
    return pack_fields(head, bytes(buf))


def get_media_extents(hex_blob):
    _, data = unpack_fields(hex_blob)
    i = data.find(KEY_EXTENTS)
    o = i + len(KEY_EXTENTS) + 4 + 1 + 4
    return struct.unpack("<dd", data[o:o + 16])


# ---- контейнер ключей (Time / Geometry / TracksBA и вложенные) --------------
#
# Формат: [4 длина имени][имя UTF-16BE][4 тип][значение]. У типов 10 (строка) и
# 12 (блок) значение идёт как [1 флаг][4 длина][данные], у остальных — просто
# 4 или 8 байт. Полную грамматику разбирать не нужно: всё, что мы меняем, либо
# фиксированного размера, либо строка той же длины (таймкод всегда HH:MM:SS:FF).

def kv_value(data, name):
    """-> (смещение значения, длина) или None. Ищем по имени ключа в UTF-16BE."""
    key = name.encode("utf-16-be")
    i = data.find(key)
    while i > 0:
        if int.from_bytes(data[i - 4:i], "big") == len(key):    # это правда ключ
            o = i + len(key)
            typ = int.from_bytes(data[o:o + 4], "big")
            # после типа всегда идёт байт-флаг, только потом значение
            if typ in (10, 12):
                size = int.from_bytes(data[o + 5:o + 9], "big")
                return o + 9, size
            return o + 5, 8 if typ in (4, 6) else 4
        i = data.find(key, i + 1)
    return None


def kv_set(data, name, raw):
    """Переписать значение НА МЕСТЕ. Длина обязана совпасть — иначе поехали бы
    длины вложенных контейнеров, а их мы не пересчитываем."""
    pos = kv_value(data, name)
    if pos is None:
        raise KeyError(f"в контейнере нет ключа {name}")
    off, size = pos
    if len(raw) != size:
        raise ValueError(f"{name}: ожидалось {size} байт, дано {len(raw)}")
    return data[:off] + raw + data[off + size:]


def kv_get(data, name):
    pos = kv_value(data, name)
    return None if pos is None else data[pos[0]:pos[0] + pos[1]]


def _hexblob(el, tag, fn):
    """Применить fn к hex-содержимому всех тегов tag внутри элемента."""
    return re.sub("<" + tag + r">([0-9a-f]{40,})</" + tag + ">",
                  lambda m: "<" + tag + ">" + fn(m.group(1)) + "</" + tag + ">", el)


def set_media_descriptor(entry_xml, path, pr, mtime="Thu Jan 01 00:00:00 2026",
                         photo=False):
    """Переписать запись медиапула под конкретный файл.

    Запись — это полный дескриптор медиа, а не ссылка: путь, имя, кодек, таймкод,
    число кадров, частота, разрешение и звук разложены по шести вложенным блобам.
    Клонировать запись и править только `MediaExtents` мало — внутри останется
    прежний файл, и Resolve сочтёт две камеры одним медиа (одна уходит в офлайн,
    вторая показывает застывший кадр).

    `photo=True` — фото-вставка. Структура записи у фото ДРУГАЯ (снята с экспорта
    Resolve): нет `Timecode` в <Time> (вместо него `StartFrame`), нет аудио и
    <TracksBA> вовсе, `FieldsBlob` не zstd. Поэтому для фото не трогаем Time и
    звук, а длительность из probe не нужна вовсе.

    Всё, что здесь меняется, либо фиксированного размера, либо строка той же
    длины (таймкод всегда `HH:MM:SS:FF`), кроме путей в `<Clip>` — там protobuf
    и длина пересчитывается.
    """
    folder, base = os.path.split(os.path.abspath(path))
    tc = pr["timecode"].replace(";", ":")            # в <Time> двоеточия
    fps = pr.get("fps")                              # None — legacy NTSC 29.97
    frames = round(pr["dur_s"] * (fps or SRC_FPS))

    def clip(h):                                      # путь и имя файла
        if not is_zstd_blob(h):
            return h
        head, d = unpack_fields(h)
        for fno, val in ((1, folder), (2, base), (3, mtime), (6, base)):
            if pb_get_str(d, fno) is not None:
                d = pb_set_str(d, fno, val)
            elif fno != 6:                            # у звукового <Clip> поля 6 нет
                d = pb_set_str(d, fno, val)
        return pack_fields(head, d)

    def time(h):
        d = bytes.fromhex(h)
        d = kv_set(d, "Timecode", tc.encode("utf-16-be"))
        d = kv_set(d, "NumFrames", frames.to_bytes(4, "big"))
        if fps:
            # FrameRate из шаблона зашит в NTSC (29.97); для PAL-камер (25)
            # Resolve раскладывает кадры клипа по этому полю — без правки
            # картинка в Resolve не совпадает с нарезкой (звук при этом верен,
            # он идёт по времени, а не по кадрам).
            d = kv_set(d, "FrameRate", struct.pack("<d", fps) + b"\x00\x00\x00\x00\x00\x00\x00\x01")
        return d.hex()

    def geometry(h):
        d = bytes.fromhex(h)
        res = pr["height"].to_bytes(8, "big") + pr["width"].to_bytes(8, "big")
        return kv_set(d, "Resolution", res).hex()

    def tracks(h):
        d = bytes.fromhex(h)
        d = kv_set(d, "StartTime", struct.pack(">d", timecode_seconds(pr["timecode"], fps)))
        sr = int.from_bytes(kv_get(d, "SampleRate") or b"\x00\x00\xbb\x80", "big")
        d = kv_set(d, "Duration", round(pr["dur_s"] * sr).to_bytes(8, "big"))
        return d.hex()

    el = _hexblob(entry_xml, "Clip", clip)
    if not photo:
        el = _hexblob(el, "Time", time)
        # у фото нет ключа Resolution в Geometry (только ScanType) — менять нечего
        el = _hexblob(el, "Geometry", geometry)
        el = _hexblob(el, "TracksBA", tracks)
    return _hexblob(el, "FieldsBlob", lambda h: set_media_extents(
        h, timecode_seconds(pr["timecode"], fps), pr["dur_s"]) if is_zstd_blob(h) else h)


# ---- сборка проекта --------------------------------------------------------

TEMPLATE = paths.data("drp_template.drp")

# стиль титров — из HIGHLIGHT_SPEC.md
SUB_FONT, SUB_STYLE = "Open Sans", "Bold"
SUB_SIZE = 140 / 1920                  # кегль 140 при высоте кадра 1920
SUB_Y_FROM_TOP = 0.5964                # якорь строки; у Fusion отсчёт снизу
SUB_WHITE, SUB_YELLOW = (1.0, 1.0, 1.0), (1.0, 0.9176, 0.0)
SUB_FIT_CHARS = 14                     # длиннее — ужимаем, как в xmlbuild


def _elements(vec_xml, tag):
    """Разбить `<...Vec>` на элементы верхнего уровня."""
    return re.findall(r"<Element>\s*<" + tag + r"\b.*?</" + tag + r">\s*</Element>",
                      vec_xml, re.S)


def _section(text, tag):
    m = re.search(r"<" + tag + r">(.*?)</" + tag + r">", text, re.S)
    return (m.group(1), m.start(1), m.end(1)) if m else ("", -1, -1)


def _set(el, tag, value):
    """Подстановка через лямбду: в путях Windows есть \\U и прочее, что re.sub
    в строке-замене принимает за escape-последовательность и падает. Текст
    экранируем через core.xmltext.xml_text."""
    new = f"<{tag}>{_esc(value)}</{tag}>"
    return re.sub(r"<" + tag + r">[^<]*</" + tag + r">|<" + tag + r"/>",
                  lambda m: new, el, count=1)


def _set_items(track_el, clips_xml):
    """Заменить содержимое <Items> у дорожки (у пустой дорожки тег самозакрыт).

    Через лямбду: в клипах лежат пути Windows, а re.sub разбирает строку-замену
    как шаблон и спотыкается о \\U из C:\\Users.
    """
    new = "<Items>" + clips_xml + "</Items>"
    if "<Items>" in track_el:
        return re.sub(r"<Items>.*?</Items>", lambda m: new, track_el, count=1, flags=re.S)
    return track_el.replace("<Items/>", new, 1)


def _sub_nodes(base, text, frames, color, scale):
    n = set_text(base, text)
    n = re.sub(r'(Font = Input \{ Value = ")[^"]*(")', r"\g<1>" + SUB_FONT + r"\g<2>", n, count=1)
    n = re.sub(r'(Style = Input \{ Value = ")[^"]*(")', r"\g<1>" + SUB_STYLE + r"\g<2>", n, count=1)
    n = set_input(n, "GlobalOut", str(frames - 1))
    n = set_input(n, "Size", f"{SUB_SIZE * scale:.6f}")
    for key, val in zip(("Red1", "Green1", "Blue1"), color):
        n = set_input(n, key, f"{val:.6f}")
    return set_input(n, "Center", "{ 0.5, %.6f }" % (1.0 - SUB_Y_FROM_TOP))


def build(out_path, cams, segments, offsets, assign=None, sub_words=(), yellow=(),
          inserts=(), name=None, template=None, fps=60, probe=None):
    """Собрать проект DaVinci Resolve из шаблона.

    Вход тот же, что у `xmlbuild.build`: камеры, оставленные куски (секунды),
    сдвиги синхрона, раскладка мультикама. `sub_words` — [(start, end, текст)] в
    кадрах таймлайна (как отдаёт `xml2ae.parse_full`), `yellow` — индексы жёлтых,
    `inserts` — [{type, media, start, end}] в кадрах.

    Раскладка дорожек снизу вверх: камеры 1..N (камера 1 всегда есть, остальные —
    по наличию, до 4), сразу над камерами субтитры, выше — вставки ФОТО, ещё выше —
    ВИДЕО. Так и Resolve это показывает, и ждёт `parse_full`.
    """
    if probe is None:
        from core import xmlbuild
        probe = xmlbuild.probe
    files = read(template or TEMPLATE)
    sname = seq_name(files)
    seq = files[sname].decode("utf-8")
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")

    # --- образцы, которые будем клонировать
    vvec, vb, ve = _section(seq, "VideoTrackVec")
    avec, ab, ae = _section(seq, "AudioTrackVec")
    vtracks, atracks = _elements(vvec, "Sm2TiTrack"), _elements(avec, "Sm2TiTrack")
    if not vtracks or not atracks:
        raise RuntimeError("шаблон .drp без дорожек — подменили файл?")
    t_vtrack = vtracks[0]
    t_ttrack = next((t for t in vtracks if "Fusion Composition" in t), vtracks[-1])
    t_atrack = atracks[0]
    t_vclip = _elements(_section(t_vtrack, "Items")[0], "Sm2TiVideoClip")[0]
    t_aclip = _elements(_section(t_atrack, "Items")[0], "Sm2TiAudioClip")[0]
    t_title = _elements(_section(t_ttrack, "Items")[0], "Sm2TiVideoClip")[0]

    # фото-вставки живут в отдельном треке шаблона (клип помечен Name-маркером) —
    # у них другой MediaTimemapBA (контейнер ключей, не 5 doubles) и нет звука,
    # поэтому клонировать камерный клип для фото нельзя.
    # В отличие от камерных, фото-клик в шаблоне НЕ обёрнут в `<Element>` (в
    # `<Items>` каждый клип Resolve держит в обёртке `<Element>`, но образец —
    # единственное место, где удобно держать его отдельно). Поэтому полный клип
    # собирается из трека-носителя: `<Element><клип></Element>`.
    photo_pat = re.compile(r"<Element>\s*<Sm2TiVideoClip\b.*?"
                           + re.escape("INSERT_PHOTO_SAMPLE")
                           + r".*?</Sm2TiVideoClip>\s*</Element>", re.S)
    t_photo_clip = None
    for t in vtracks:
        m = photo_pat.search(t)
        if m:
            t_photo_clip = m.group(0)
            break
    if t_photo_clip is None:               # шаблон без обёртки (старые версии)
        photo_pat = re.compile(r"<Sm2TiVideoClip\b.*?"
                               + re.escape("INSERT_PHOTO_SAMPLE")
                               + r".*?</Sm2TiVideoClip>", re.S)
        for t in vtracks:
            m = photo_pat.search(t)
            if m:
                t_photo_clip = m.group(0)
                break

    mvec, mb, me = _section(mp, "MediaVec")
    pool_recs = _elements(mvec, "Sm2MpVideoClip")
    t_pool = pool_recs[0]
    # запись-образец фото помечена вложенным MarkIn; без неё фото не соберётся
    t_pool_photo = next((r for r in pool_recs if "INSERT_PHOTO_SAMPLE" in r), None)
    keep_pool = [e for e in _elements(mvec, "Sm2MpTimelineClip")] + \
                [e for e in _elements(mvec, "Sm2MpGenerator")]

    # --- медиапул: по записи на каждый файл (камеры + медиа вставок)
    media = list(cams) + [x["media"] for x in inserts if x.get("media")]
    kind_of = {x["media"]: x.get("type") for x in inserts if x.get("media")}
    pool_xml, ref = [], {}
    for path in dict.fromkeys(media):                       # без повторов, порядок стабилен
        pr = probe(path)
        is_photo = kind_of.get(path) == "photo"
        tpl = t_pool_photo if is_photo and t_pool_photo else t_pool
        el = new_pool_ids(tpl)                              # не только DbId — см. функцию
        did = re.search(r'DbId="([0-9a-f-]+)"', el).group(1)
        ref[path] = (did, pr)
        el = _set(el, "Name", os.path.basename(path))
        el = set_media_descriptor(el, path, pr, photo=is_photo)
        pool_xml.append(el)

    def media_clip(tpl, path, start, dur, src_in, off=False, photo=False):
        did, pr = ref[path]
        fps = pr.get("fps")
        el = new_ids(tpl)
        el = _set(el, "Name", os.path.basename(path))
        el = _set(el, "Start", start)
        el = _set(el, "Duration", dur)
        el = _set(el, "In", src_in)
        el = _set(el, "MediaRef", did)
        el = _set(el, "MediaStartTime", f"{media_start_time(pr['timecode'], fps):g}")
        el = _set(el, "MediaFilePath", path)
        if fps:
            # шаблонный MediaFrameRate — номинал NTSC (30.0); для PAL (25)
            # Resolve читает кадры клипа по нему, см. пул FrameRate выше
            el = _set(el, "MediaFrameRate",
                      (struct.pack("<d", round(fps)) + b"\x00\x00\x00\x00\x00\x00\x00\x01").hex())
        # Flags=2 — клип выключен. Так Resolve хранит раскладку мультикама: камера 1
        # всегда видна, камера k>0 — только на своих кусках (в XML это <enabled>).
        el = _set(el, "Flags", 2 if off else 0)
        # у медиаклипа карта ОТ ИСХОДНИКА, одна на все клипы камеры (см. media_timemap).
        # У фото своя карта-контейнер ключей (начинается не с типа 02) — её не трогаем.
        if not photo:
            el = _set(el, "MediaTimemapBA", media_timemap(pr["dur_s"], fps or SRC_FPS))
        return el

    # --- камеры: клипЫ по кускам нарезки
    n = len(cams)
    assign = assign or [0] * len(segments)
    vclips = [[] for _ in range(n)]
    aclips = [[] for _ in range(n)]
    tl = 0
    for k, (s, e) in enumerate(segments):
        length = round(e * fps) - round(s * fps)
        if length <= 0:
            continue
        active = assign[k] if k < len(assign) else 0
        for c in range(n):
            src_in = round((s - offsets[c]) * fps)
            off = c > 0 and active != c              # камера 1 — база, всегда видна
            vclips[c].append(media_clip(t_vclip, cams[c], tl, length, src_in, off))
            aclips[c].append(media_clip(t_aclip, cams[c], tl, length, src_in))
        tl += length

    video_tracks = [_set_items(new_ids(t_vtrack), "".join(vclips[c])) for c in range(n)]
    audio_tracks = [_set_items(new_ids(t_atrack), "".join(aclips[c])) for c in range(n)]

    # --- титры: сразу над камерами, под вставками (эталонный формат: камеры,
    # субтитры, фото, видео — parse_full ждёт именно такой порядок)
    if sub_words:
        prefix, comp, nodes, off = split_comp(
            re.search(r"<CompositionBA>([0-9a-f]+)</CompositionBA>", t_title).group(1))
        comp_txt, nodes_txt = comp.decode("utf-8", "surrogateescape"), nodes.decode("utf-8")
        yellow = set(yellow)
        items = []
        for i, (st, en, word) in enumerate(sub_words):
            dur = max(1, en - st)
            scale = 1.0 if len(word) <= SUB_FIT_CHARS else SUB_FIT_CHARS / len(word)
            nd = _sub_nodes(nodes_txt, word, dur, SUB_YELLOW if i in yellow else SUB_WHITE, scale)
            el = new_ids(t_title)
            el = re.sub(r"<CompositionBA>[0-9a-f]+</CompositionBA>",
                        "<CompositionBA>" + join_comp(
                            prefix, retime_comp(comp_txt, dur).encode("utf-8", "surrogateescape"),
                            nd.encode("utf-8"), off) + "</CompositionBA>", el)
            el = _set(el, "Name", f"sub{i + 1}")
            el = _set(el, "Start", st)
            el = _set(el, "Duration", dur)
            el = _set(el, "MediaTimemapBA", timemap(dur, fps))
            items.append(el)
        video_tracks.append(_set_items(new_ids(t_ttrack), "".join(items)))

    # --- вставки: фото на свою дорожку над субтитрами, видео выше всех
    for kind in ("photo", "video"):
        got = [x for x in inserts if x.get("type") == kind and x.get("media")]
        if not got:
            continue
        tpl = t_photo_clip if kind == "photo" else t_vclip
        if kind == "photo" and tpl is None:
            continue                                   # шаблон без фото-образца
        video_tracks.append(_set_items(new_ids(t_vtrack), "".join(
            media_clip(tpl, x["media"], x["start"], x["end"] - x["start"],
                       x.get("sin", 0), photo=(kind == "photo")) for x in got)))

    # Запись самого таймлайна в медиапуле тоже несёт длительность — без правки
    # в пуле остаётся хронометраж шаблона.
    def fix_timeline_len(el):
        if "Sm2MpTimelineClip" not in el:
            return el
        m = re.search(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", el)
        if not m or not is_zstd_blob(m.group(1)):
            return el
        fixed = set_media_extents(m.group(1), 0.0, tl / fps)
        return el[:m.start(1)] + fixed + el[m.end(1):]

    keep_pool = [fix_timeline_len(el) for el in keep_pool]

    # --- сборка
    seq = seq[:vb] + "".join(video_tracks) + seq[ve:]
    vshift = len("".join(video_tracks)) - (ve - vb)
    seq = seq[:ab + vshift] + "".join(audio_tracks) + seq[ae + vshift:]
    files[sname] = seq.encode("utf-8")
    files["MediaPool/Master/MpFolder.xml"] = (
        mp[:mb] + "".join(pool_xml + keep_pool) + mp[me:]).encode("utf-8")
    if name:
        files["project.xml"] = re.sub(rb"<ProjectName>[^<]*</ProjectName>",
                                      f"<ProjectName>{_esc(name)}</ProjectName>".encode("utf-8"),
                                      files["project.xml"], count=1)
    write(out_path, files)
    return {"cameras": n, "clips": sum(len(v) for v in vclips),
            "subtitles": len(sub_words), "inserts": len([x for x in inserts if x.get("media")]),
            "total_frames": tl}
