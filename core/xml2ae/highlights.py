# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Жёлтые выделения и правка слова прямо в XML.

Опасная часть модуля: пишет в ФАЙЛ ПОЛЬЗОВАТЕЛЯ. Отсюда _backup_once (копия рядом
перед первой правкой) и _write_xml_prolog — Premiere не открывает XML без пролога,
а ElementTree его не пишет.
"""
import os, html, base64
from typing import Any, cast
import xml.etree.ElementTree as ET
from core.applog import get_logger
from core.fileio import atomic_text_write
from .parse import _b64decode, _txt
from core.umsg import ReelsiError

log = get_logger("reelsi.xml2ae.highlights")




# Заливка текста субтитра зашита в бинарный блоб «Source Text» (parameterid=1) Essential
# Graphics. У БЕЛОГО (дефолт) цвет не дописан; у цветного 3 байта RGB идут в самый конец
# блоба сразу после якоря-vtable ниже. Проверено: ловит только реально окрашенные слова,
# 0 ложных на тысячах белых.
_COLOR_ANCHOR = bytes([0x07, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x00])


def _word_color(clip: Any) -> tuple[int, int, int] | None:
    """RGB (0..255) заливки слова-субтитра или None, если белый/дефолт."""
    for p in clip.findall(".//filter/effect/parameter"):
        if (p.findtext("parameterid") or "") != "1":
            continue
        v = p.find("value")
        if v is None or not v.text or not v.text.strip():
            return None
        try:
            b = base64.b64decode(v.text.strip())
        except ReelsiError: raise
        except Exception:
            return None
        s = b.rstrip(b"\x00")
        if s.endswith(_COLOR_ANCHOR):               # после якоря пусто -> цвет не задан (белый)
            return None
        i = s.rfind(_COLOR_ANCHOR)
        if i < 0:
            return None
        rgb = s[i + len(_COLOR_ANCHOR):]
        if len(rgb) == 3 and rgb != b"\x00\x00\x00":
            return (rgb[0], rgb[1], rgb[2])
        return None
    return None


SEP_GAP_SEC = 0.40   # пауза между соседними жёлтыми >= этого = разные фразы -> палочка-разделитель.
                     # Раньше стоял порог в 1 кадр: любой микрозазор рвал ФРАЗУ на слова-одиночки.
                     # 0.40с = та же граница фразы, что и в нарезке (gigaam_cut.tune.GAP) — слова внутри
                     # одной фразы (зазор < 0.4с) остаются одной стопкой, палка только на реальной паузе.


def auto_highlights(xml_path: str) -> dict[str, list[int]]:
    """По цвету слов в XML (Премьер): не-белые -> жёлтые; между соседними жёлтыми с зазором
    по времени (не впритык) -> разделитель. Порядок индексов = как у parse_full (subs, по start).
    -> dict(yellow=[idx], breaks=[idx]). Пусто, если цветных слов нет."""
    root = ET.parse(xml_path).getroot()
    seq: Any = root.find(".//sequence")
    fps = int(cast(str, _txt(seq.find("rate"), "timebase", "60"))) or 60
    rows: list[Any] = []                                        # (start, end, color) по всем словам-субтитрам
    for tr in seq.findall(".//media/video/track"):
        clips = tr.findall("clipitem")
        is_sub = any((c.find(".//filter/effect/effectid") is not None and
                      c.find(".//filter/effect/effectid").text == "GraphicAndType") for c in clips)
        if not is_sub:
            continue
        for c in clips:
            eff = c.find(".//filter/effect")
            word = html.unescape((_txt(eff, "name") or "").strip()) if eff is not None else ""
            if word:
                rows.append((int(cast(str, _txt(c, "start"))), int(cast(str, _txt(c, "end"))), _word_color(c)))
    rows.sort(key=lambda x: x[0])                    # тот же порядок, что subs в parse_full
    yellow = [k for k, (s, e, col) in enumerate(rows) if col is not None]
    yset = set(yellow)
    breaks: list[int] = []
    sep_frames = SEP_GAP_SEC * fps
    for k in range(len(rows) - 1):
        if k in yset and (k + 1) in yset:            # два подряд жёлтых
            gap = rows[k + 1][0] - rows[k][1]        # зазор по времени (кадры)
            if gap >= sep_frames:                    # реальная пауза -> разные фразы, разделить стопку
                breaks.append(k)
    return dict(yellow=yellow, breaks=breaks)


def _sub_value_elem(clip: Any) -> Any:
    """<value>-элемент Source Text (parameterid==1) слова-субтитра, или None."""
    for p in clip.findall(".//filter/effect/parameter"):
        if (p.findtext("parameterid") or "") == "1":
            return p.find("value")
    return None


def write_highlights(xml_path: str, indices: Any, out_path: str | None = None) -> dict[str, Any]:
    """Покрасить выбранные слова-субтитры ПРЯМО в XML (вместо сайдкара .yellow.json):
    в блоб Source Text подставляется покрашенный вариант того же слова из библиотеки
    subtitle_blobs. Тогда auto_highlights/_word_color видят слово как выделенное, цвет
    едет вместе с клипом (переживает ручной до-монтаж в Премьере — нет каверзы индексов).

    indices — в порядке parse_full (subs, сортировка по start), как их отдаёт cmd_yellow.
    Возвращает dict(colored=[идекс], skipped=[(idx,word,'причина')]). Слова длиннее самого
    большого покрашенного шаблона пропускаются (остаются на сайдкар-фолбэк)."""
    from core import subtitle_blobs as sb
    lib = sb.colour_library()
    root: Any = ET.parse(xml_path).getroot()
    seq: Any = root.find(".//sequence")
    items: list[Any] = []                                           # (start, word, value_elem) — как в parse_full
    for tr in seq.findall(".//media/video/track"):
        clips = tr.findall("clipitem")
        if not any((c.find(".//filter/effect/effectid") is not None and
                    c.find(".//filter/effect/effectid").text == "GraphicAndType") for c in clips):
            continue
        for c in clips:
            word = html.unescape((_txt(c.find(".//filter/effect"), "name") or "").strip())
            word = " ".join(word.replace("\r", "").replace("\n", "").split())
            if word:
                items.append((int(cast(str, _txt(c, "start"))), word, _sub_value_elem(c)))
    items.sort(key=lambda x: x[0])                       # тот же порядок, что subs в parse_full
    colored, skipped = [], []
    want = set(int(i) for i in indices)
    for k, (start, word, ve) in enumerate(items):
        if k not in want:
            continue
        if ve is None:
            skipped.append((k, word, "нет Source Text")); continue
        wb = len(word.encode("utf-8"))
        if wb > lib.max_len:
            skipped.append((k, word, f"слишком длинное ({wb}B > {lib.max_len}B)")); continue
        try:
            ve.text = lib.make(word)
        except ReelsiError: raise
        except Exception as e:
            skipped.append((k, word, str(e))); continue
        colored.append(k)
    # запись с сохранением пролога (<?xml?> + <!DOCTYPE xmeml>) — ET их не пишет
    _write_xml_prolog(root, xml_path, out_path)
    return dict(colored=colored, skipped=skipped)


def _sub_items(seq: Any, with_track: bool = False) -> list[Any]:
    """Слова-субтитры таймлайна в порядке parse_full: [(start, word, clipitem, effect), ...]."""
    items: list[Any] = []
    for tr in seq.findall(".//media/video/track"):
        clips = tr.findall("clipitem")
        if not any((c.find(".//filter/effect/effectid") is not None and
                    c.find(".//filter/effect/effectid").text == "GraphicAndType") for c in clips):
            continue
        for c in clips:
            eff = c.find(".//filter/effect")
            word = html.unescape((_txt(eff, "name") or "").strip())
            word = " ".join(word.replace("\r", "").replace("\n", "").split())
            if word:
                if with_track:
                    items.append((int(cast(str, _txt(c, "start"))), word, c, eff, tr))
                else:
                    items.append((int(cast(str, _txt(c, "start"))), word, c, eff))
    items.sort(key=lambda x: x[0])
    return items


def _backup_once(xml_path: str, out_path: str | None = None) -> None:
    """Перед ПЕРВОЙ правкой XML на месте — копия <файл>.xml.bak (оригинал из Премьера).
    Уже есть .bak или пишем в другой файл — ничего не делаем."""
    if out_path and os.path.abspath(out_path) != os.path.abspath(xml_path):
        return
    bak = xml_path + ".bak"
    try:
        if not os.path.isfile(bak):
            import shutil
            shutil.copy2(xml_path, bak)
    except ReelsiError: raise
    except Exception as e:
        # Бэкап — страховка, не повод падать. Но и молчать нельзя: пользователь
        # должен знать, что копии оригинала рядом нет (от пустого файла спасает
        # атомарная запись, от «правки не туда» — уже нет).
        log.warning("ошибка создания бэкапа %s: %s", bak, e)


def _write_xml_prolog(root: Any, xml_path: str, out_path: str | None = None) -> None:
    """Записать XML с прологом (<?xml?> + <!DOCTYPE xmeml>) — ET их не пишет.

    Запись атомарная (core/fileio.atomic_text_write): «Стоп» или сбой в момент
    прямой записи оставлял на месте XML пользователя пустой файл."""
    _backup_once(xml_path, out_path)
    raw = open(xml_path, encoding="utf-8").read()
    cut = raw.find("<xmeml")
    prolog = raw[:cut] if cut > 0 else '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
    body = ET.tostring(root, encoding="unicode")
    atomic_text_write(out_path or xml_path, prolog + body)


def set_highlights(xml_path: str, indices: Any, out_path: str | None = None) -> dict[str, Any]:
    """ЯВНО задать набор жёлтых слов (не только добавить, как write_highlights): выбранные —
    красим (покрашенный блоб), а покрашенные, но НЕ выбранные — возвращаем в белый блоб.
    indices — в порядке parse_full. -> dict(colored=[...], skipped=[(idx,word,'причина')])."""
    from core import subtitle_blobs as sb
    clib, wlib = sb.colour_library(), sb.library()
    root: Any = ET.parse(xml_path).getroot()
    seq: Any = root.find(".//sequence")
    want = set(int(i) for i in indices)
    colored, skipped = [], []
    for k, (start, word, c, eff) in enumerate(_sub_items(seq)):
        ve = _sub_value_elem(c)
        if ve is None:
            if k in want:
                skipped.append((k, word, "нет Source Text"))
            continue
        try:
            is_col = sb.blob_is_coloured(_b64decode(ve.text))
        except ReelsiError: raise
        except Exception:
            is_col = False
        wb = len(word.encode("utf-8"))
        if k in want:                                     # хотим жёлтое
            if is_col:
                colored.append(k); continue               # уже покрашено
            blob = _make_blob(clib, word, want_col=True)
            if blob is None:
                skipped.append((k, word, f"блоб не собрался ({wb}B)")); continue
            ve.text = blob; colored.append(k)
        else:                                             # хотим белое
            if not is_col:
                continue                                  # уже белое
            blob = _make_blob(wlib, word, want_col=False)
            if blob is None:
                skipped.append((k, word, f"блоб не собрался ({wb}B)")); continue
            ve.text = blob                                # снять цвет
    _write_xml_prolog(root, xml_path, out_path)
    return dict(colored=colored, skipped=skipped)


def _make_blob(lib: Any, word: str, want_col: bool) -> str | None:
    """Собрать блоб Source Text и ПРОВЕРИТЬ его чтением обратно. -> base64 или None.

    Раньше здесь стоял слепой гард `len(word) > lib.max_len -> пропустить`. Он остался
    с эпохи до `subtitle_blobs._grow()`: цветная библиотека кончается на 28 байтах
    (14 кириллических букв), поэтому «ГИПЕРЧУВСТВИТЕЛЬНОСТЬ» и прочие длинные термины
    молча не красились, хотя `make()` их уже умеет. Гард по длине снят, но вместо
    слепого доверия блоб проверяется: текст читается обратно и сверяется цветность —
    если сборка врёт, слово честно уходит в `skipped`, а не в битый XML.
    """
    from core import subtitle_blobs as sb
    try:
        blob = lib.make(word)
        raw = _b64decode(blob)
        if sb._read_text(raw) != word:
            return None
        if bool(sb.blob_is_coloured(raw)) != bool(want_col):
            return None
        return blob
    except ReelsiError: raise
    except Exception:
        return None


def edit_word(xml_path: str, index: int, text: str, out_path: str | None = None) -> dict[str, Any]:
    """Переписать ТЕКСТ слова-субтитра #index (порядок parse_full): и блоб Source Text
    (той же цветности — цвет сохраняется), и <name> эффекта (его читает parse_full), и
    <name> клипайтема. -> dict(ok=True, word=...) либо dict(error=...)."""
    from core import subtitle_blobs as sb
    text = " ".join((text or "").replace("\r", "").replace("\n", "").split())
    if not text:
        return dict(error="пустой текст")
    root: Any = ET.parse(xml_path).getroot()
    seq: Any = root.find(".//sequence")
    items = _sub_items(seq)
    if index < 0 or index >= len(items):
        return dict(error="индекс вне диапазона")
    start, word, c, eff = items[index]
    ve = _sub_value_elem(c)
    if ve is None:
        return dict(error="нет Source Text у слова")
    try:
        coloured = sb.blob_is_coloured(_b64decode(ve.text))
    except ReelsiError: raise
    except Exception:
        coloured = False
    lib = sb.colour_library() if coloured else sb.library()
    wb = len(text.encode("utf-8"))
    # Собираем через _make_blob (как set_highlights): библиотека умеет РАСТИТЬ блоб
    # (_grow), а слепой гард по lib.max_len резал «ОТВЕТСТВЕННОСТЬ» (30 байт против
    # 28 у цветной библиотеки) — слово не переименовывалось вовсе (GZ, п. H).
    # Вместо доверия длине блоб проверяется чтением обратно.
    blob = _make_blob(lib, text, want_col=coloured)
    if blob is None:
        return dict(error=f"блоб не собрался ({wb}B)")
    ve.text = blob
    en = eff.find("name")                                 # <name> эффекта — источник слова для parse_full
    if en is not None:
        en.text = text
    cn = c.find("name")                                   # <name> клипайтема (для вида в Премьере)
    if cn is not None:
        cn.text = text
    _write_xml_prolog(root, xml_path, out_path)
    return dict(ok=True, word=text)


GAP_JOIN_SEC = 0.30   # Пауза, до которой слова считаются идущими ПОДРЯД. Удалил слово —
                      # следующее подхватывает его время. Больше — это уже
                      # реальная пауза в речи: на месте удалённого слова должна остаться
                      # тишина, двигать следующее нельзя.


def _seq_fps(seq: Any, default: int = 60) -> int:
    """Частота секвенции (кадров/с) — в тех же единицах, что start/end клипов."""
    rate = seq.find("rate") if seq is not None else None
    if rate is None:
        return default
    try:
        return int(_txt(rate, "timebase", str(default)) or default) or default
    except (TypeError, ValueError):
        return default


def _track_word_clips(tr: Any) -> list[tuple[int, Any]]:
    """Клипы-слова ОДНОЙ дорожки: [(start, clipitem), ...] по порядку start — так же,
    как их собирает _sub_items (удалять надо соседа по своей дорожке, а не по таймлайну)."""
    rows: list[tuple[int, Any]] = []
    for c in tr.findall("clipitem"):
        eff = c.find(".//filter/effect")
        if eff is None:
            continue
        word = html.unescape((_txt(eff, "name") or "").strip())
        word = " ".join(word.replace("\r", "").replace("\n", "").split())
        s = _txt(c, "start")
        if word and s is not None:
            rows.append((int(s), c))
    rows.sort(key=lambda x: x[0])
    return rows


def _move_clip_head(c: Any, new_start: int | float) -> bool:
    """Перенести начало клипа на new_start, НЕ трогая его конец.

    `start` — то, что читают `_sub_items`/`parse_full` как начало слова. Вместе с ним
    растягивается окно источника: `out = in + (end - start)` — ровно так субтитр-клипы
    собирает `core.subs` (`GFX_IN` там ФИКСИРОВАННЫЙ in-point графики, а длина источника
    равна длине клипа). Сам `in` поэтому не двигаем: до `GFX_IN` в источнике графики нет.
    `pproTicksOut` пересчитываем тем же тактом, что уже записан в файле: Премьер читает
    такты, и с прежним значением клип остался бы в нём коротким.
    -> True, если начало реально сдвинулось."""
    se, ee = c.find("start"), c.find("end")
    if se is None or se.text is None or ee is None or ee.text is None:
        return False
    try:
        old, end = int(se.text), int(ee.text)
    except ValueError:
        return False
    new_start = int(new_start)
    if new_start >= old:
        return False
    se.text = str(new_start)
    ine, oue = c.find("in"), c.find("out")
    try:
        i0, o0 = int(ine.text), int(oue.text)
    except (AttributeError, TypeError, ValueError):
        return True                    # клипа без in/out не бывает, но падать из-за них нечем
    if o0 > i0:
        ti, to = c.find("pproTicksIn"), c.find("pproTicksOut")
        try:
            tpu = (int(to.text) - int(ti.text)) / (o0 - i0)     # тактов на единицу источника
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            tpu = None
        o1 = i0 + max(1, end - new_start)
        oue.text = str(o1)
        if tpu is not None:
            try:
                to.text = str(int(ti.text) + int(round((o1 - i0) * tpu)))
            except (TypeError, ValueError) as ex:
                log.warning("не пересчитал pproTicksOut клипа: %s", ex)
    return True


def _hand_over_start(tr: Any, cur: Any, start: int | float, end: Any, fps: float) -> bool:
    """Отдать время удаляемого слова следующему слову ТОЙ ЖЕ дорожки.

    Клип слова вынимается из дорожки, соседей это не двигает — на месте удалённого
    оставалась дыра (в reelsi_batch.aep: 1.2 с тишины в субтитрах после «РЕКОМЕНДУЕТ»,
    счётчик всплывал на секунду позже). Если следующее слово шло подряд (зазор не больше
    GAP_JOIN_SEC), его начало переносится на начало удаляемого, а конец остаётся: слово
    просто живёт дольше. Удаление нескольких слов подряд работает цепочкой — каждое
    следующее подтягивается к началу ПЕРВОГО удалённого.
    -> True, если начало следующего слова сдвинулось."""
    try:
        endv = int(end)
    except (TypeError, ValueError):
        return False                   # без конца удаляемого зазор не посчитать
    rows = _track_word_clips(tr)
    pos = next((k for k, (_s, cc) in enumerate(rows) if cc is cur), None)
    if pos is None or pos + 1 >= len(rows):
        return False                   # последнее слово дорожки — отдавать время некому
    n_start, n_clip = rows[pos + 1]
    gap = n_start - endv
    if gap < 0 or gap > GAP_JOIN_SEC * fps:
        return False                   # перехлёст или пауза — это не «слово шло подряд»
    return _move_clip_head(n_clip, int(start))


def delete_word(xml_path: str, index: int, out_path: str | None = None) -> dict[str, Any]:
    """Удалить слово-субтитр #index (порядок parse_full) из XML целиком.

    Слово отдаёт своё время следующему: если сразу за удаляемым в его
    дорожке стоит клип-слово и зазор между ними не больше GAP_JOIN_SEC, начало
    следующего переносится на начало удаляемого (конец не меняется). Иначе — как было.
    Индексы наборов (HL/BRK/CNT/интро) сдвигает по-прежнему shift_indices.
    -> dict(ok=True, index=index, word=...) либо dict(error=...)."""
    try:
        root = ET.parse(xml_path).getroot()
    except ReelsiError: raise
    except Exception as e:
        return dict(error=str(e))
    seq = root.find(".//sequence")
    if seq is None:
        return dict(error="нет sequence в XML")
    items = _sub_items(seq, with_track=True)
    if index < 0 or index >= len(items):
        return dict(error="индекс вне диапазона")
    start, word, c, eff, tr = items[index]
    _hand_over_start(tr, c, start, _txt(c, "end"), _seq_fps(seq))
    tr.remove(c)
    _write_xml_prolog(root, xml_path, out_path)
    return dict(ok=True, index=index, word=word)


def shift_indices(indices: Any, del_idx: int) -> Any:
    """Сдвиг набора индексов слов при удалении слова #del_idx:
    индекс == del_idx убирается; индексы > del_idx уменьшаются на 1;
    индексы < del_idx не меняются.
    Поддерживает set, list или отдельный int/None.
    """
    if indices is None:
        return None
    if isinstance(indices, (int, float)):
        idx = int(indices)
        if idx == del_idx:
            return None
        return idx - 1 if idx > del_idx else idx
    if isinstance(indices, set):
        out: Any = set()
        for i in indices:
            s = shift_indices(i, del_idx)
            if s is not None:
                out.add(s)
        return out
    if isinstance(indices, list):
        out: list[dict[str, Any]] = []  # type: ignore[no-redef]  # переиспользование out для list
        for i in indices:
            s = shift_indices(i, del_idx)
            if s is not None:
                out.append(s)
        return out
    return indices


def shift_intro_rows(rows: list[dict[str, Any]], del_idx: int) -> list[dict[str, Any]]:
    """Сдвиг строк интро при удалении слова #del_idx:
    - если слово потреблено строкой, её count уменьшается на 1;
    - строки с count <= 0 удаляются;
    - поле from: равное del_idx -> None, больше del_idx -> from - 1.
    """
    out = []
    off = 0
    for r in rows:
        r = dict(r)
        r_from = r.get("from")
        if r_from is not None and r_from >= 0:
            off = r_from
        c = max(0, int(r.get("count", 0)))
        in_row = (off <= del_idx < off + c)
        off += c
        if in_row:
            c -= 1
            r["count"] = c
        r["from"] = shift_indices(r_from, del_idx)
        if c > 0:
            out.append(r)
    return out
