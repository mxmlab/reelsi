# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Список установленных шрифтов с PostScript-именами (как их видит After Effects).

Нужно для UI: базовый шрифт + выбор жирного варианта той же семьи для жёлтых слов.
Читает nameID 6 (PostScript) и 16/1 (типографская семья) из файлов шрифтов через fontTools.
У вариативных шрифтов (fvar) перечисляет именованные экземпляры — AE показывает их все,
а nameID 6 даёт только дефолтное начертание (задание BO). Кэшируется в памяти.
Если fontTools нет — вернёт пустой список (UI останется свободным вводом).
"""
import os, logging

logging.getLogger("fontTools").setLevel(logging.ERROR)   # не спамить в консоль на кривых шрифтах

_CACHE = None

# Кэш глифсетов по (файл, координаты осей): файл шрифта на сборку открывается один раз (задание BP).
_GS_CACHE = {}

# Кэш границ глифа по (файл, координаты осей, имя глифа) — контуры не пересчитываются
# на каждой строке ролика (задание A1).
_GB_CACHE = {}

_FONT_DIRS = [
    # Windows
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    # macOS
    "/Library/Fonts",
    "/System/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    # Linux
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
    os.path.expanduser("~/.fonts"),
]
_EXT = (".ttf", ".otf", ".ttc")


WIDTH_CLASS_TO_STRETCH = {
    1: 50,
    2: 62.5,
    3: 75,
    4: 87.5,
    5: 100,
    6: 112.5,
    7: 125,
    8: 150,
    9: 200,
}


def _parse_fallback_style(name):
    """(weight, stretch, italic) по имени начертания/файла/PS (запасной источник, задание DA)."""
    low = (name or "").lower().replace("-", " ").replace("_", " ")
    # italic / oblique
    italic = any(k in low for k in ("italic", "oblique", "ital", "obli", "kursiv", "slanted"))

    # stretch / width
    stretch = 100
    if any(k in low for k in ("ultra condensed", "ultracondensed")):
        stretch = 50
    elif any(k in low for k in ("extra condensed", "extracondensed")):
        stretch = 62.5
    elif any(k in low for k in ("semi condensed", "semicondensed")):
        stretch = 87.5
    elif any(k in low for k in ("condensed", "narrow", "cond")):
        stretch = 75
    elif any(k in low for k in ("ultra expanded", "ultraexpanded")):
        stretch = 200
    elif any(k in low for k in ("extra expanded", "extraexpanded")):
        stretch = 150
    elif any(k in low for k in ("semi expanded", "semiexpanded")):
        stretch = 112.5
    elif any(k in low for k in ("expanded", "wide")):
        stretch = 125

    # weight
    weight = 400
    if any(k in low for k in ("extra black", "extrablack", "ultra black", "ultrablack")):
        weight = 950
    elif any(k in low for k in ("black", "heavy")):
        weight = 900
    elif any(k in low for k in ("extra bold", "extrabold", "ultra bold", "ultrabold")):
        weight = 800
    elif any(k in low for k in ("semi bold", "semibold", "demi bold", "demibold", "semi-bold", "demi-bold")):
        weight = 600
    elif "bold" in low:
        weight = 700
    elif "medium" in low:
        weight = 500
    elif any(k in low for k in ("extra light", "extralight", "ultra light", "ultralight")):
        weight = 200
    elif any(k in low for k in ("thin", "hairline")):
        weight = 100
    elif "light" in low:
        weight = 300
    elif any(k in low for k in ("regular", "normal", "plain", "book", "standard")):
        weight = 400

    return weight, stretch, italic


def _names(tt, path):
    """Записи {ps, family, weight, stretch, italic, var?, file} из name/fvar одного шрифта fontTools.

    Для файла без fvar — одна запись (как было). Для вариативного — записи всех
    именованных экземпляров fvar плюс дефолтное начертание: AE показывает их все,
    а nameID 6 даёт только дефолт (задание BO). var — координаты осей экземпляра,
    weight/stretch/italic — параметры начертания (задание DA).
    file — путь к файлу (нужен заданию BP для замера ширины строки).
    """
    try:
        nm = tt["name"]
    except Exception:
        return []
    fam = nm.getDebugName(16) or nm.getDebugName(1)   # 16 = типографская семья, иначе 1
    sub = nm.getDebugName(17) or nm.getDebugName(2)   # 17 = типографское начертание, иначе 2
    ps = nm.getDebugName(6)
    if not ps:
        return []
    fam = (fam or ps).strip()
    ps = ps.strip()

    os2 = None
    try:
        os2 = tt.get("OS/2")
    except Exception:
        os2 = None

    head = None
    try:
        head = tt.get("head")
    except Exception:
        head = None

    fb_w, fb_st, fb_it = _parse_fallback_style(f"{sub or ''} {ps or ''}")

    base_w = None
    if os2 is not None and hasattr(os2, "usWeightClass") and 1 <= os2.usWeightClass <= 1000:
        base_w = int(os2.usWeightClass)
    elif head is not None and hasattr(head, "macStyle") and (head.macStyle & 0x01):
        base_w = 700
    if base_w is None:
        base_w = fb_w

    base_st = None
    if os2 is not None and hasattr(os2, "usWidthClass") and os2.usWidthClass in WIDTH_CLASS_TO_STRETCH:
        base_st = WIDTH_CLASS_TO_STRETCH[os2.usWidthClass]
    if base_st is None:
        base_st = fb_st

    base_it = None
    if os2 is not None and hasattr(os2, "fsSelection"):
        base_it = bool((os2.fsSelection & 0x01) or (os2.fsSelection & 0x200))
    elif head is not None and hasattr(head, "macStyle"):
        base_it = bool(head.macStyle & 0x02)
    if base_it is None:
        base_it = fb_it

    fvar = None
    try:
        fvar = tt.get("fvar")
    except Exception:
        fvar = None
    if fvar is None:
        return [{
            "ps": ps,
            "family": fam,
            "weight": base_w,
            "stretch": base_st,
            "italic": bool(base_it),
            "file": path,
        }]

    def_coords = {a.axisTag: a.defaultValue for a in fvar.axes}
    def_w = int(round(def_coords["wght"])) if "wght" in def_coords else base_w
    def_st = (int(def_coords["wdth"]) if int(def_coords["wdth"]) == def_coords["wdth"] else round(def_coords["wdth"], 1)) if "wdth" in def_coords else base_st
    def_it = (def_coords["ital"] >= 0.5) if "ital" in def_coords else ((def_coords["slnt"] != 0) if "slnt" in def_coords else bool(base_it))

    out = [{
        "ps": ps,
        "family": fam,
        "weight": def_w,
        "stretch": def_st,
        "italic": def_it,
        "var": def_coords,
        "file": path,
    }]
    for inst in fvar.instances:
        ips = nm.getDebugName(inst.postscriptNameID)
        isub = nm.getDebugName(inst.subfamilyNameID)
        if not ips:
            # postscriptNameID пуст (0xFFFF): Windows/AE собирают имя канонически —
            # дефолт до дефиса + '-' + subfamilyName без пробелов (SFPro-CondensedSemibold)
            if not isub:
                continue
            ips = ps.split("-")[0] + "-" + isub.replace(" ", "")
        coords = dict(inst.coordinates)
        ifb_w, ifb_st, ifb_it = _parse_fallback_style(f"{isub or ''} {ips or ''}")
        inst_w = int(round(coords["wght"])) if "wght" in coords else (base_w if base_w is not None else ifb_w)
        inst_st = (int(coords["wdth"]) if int(coords["wdth"]) == coords["wdth"] else round(coords["wdth"], 1)) if "wdth" in coords else (base_st if base_st is not None else ifb_st)
        inst_it = (coords["ital"] >= 0.5) if "ital" in coords else ((coords["slnt"] != 0) if "slnt" in coords else (bool(base_it) or ifb_it))

        out.append({
            "ps": ips.strip(),
            "family": fam,
            "weight": inst_w,
            "stretch": inst_st,
            "italic": inst_it,
            "var": coords,
            "file": path,
        })
    return out


def _read_file(path):
    out = []
    try:
        from fontTools.ttLib import TTFont, TTCollection
    except Exception:
        return out
    try:
        if path.lower().endswith(".ttc"):
            coll = TTCollection(path, lazy=True, fontNumber=-1)
            for tt in coll.fonts:
                out.extend(_names(tt, path))
            coll.close()
        else:
            tt = TTFont(path, lazy=True, fontNumber=0)
            out.extend(_names(tt, path))
            tt.close()
    except Exception:
        pass
    return out


def list_fonts(refresh=False):
    """[{"ps", "family", "file", "var"?}, ...], отсортировано, без дублей по ps."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    seen, items = set(), []
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                if os.path.splitext(f)[1].lower() not in _EXT:
                    continue
                for rec in _read_file(os.path.join(root, f)):
                    if rec["ps"] and rec["ps"] not in seen:
                        seen.add(rec["ps"])
                        items.append(rec)
    items.sort(key=lambda x: (x["family"].lower(), x["ps"].lower()))
    _CACHE = items
    return items


def _glyph_set(file, coords):
    """(glyphSet, unitsPerEm, cmap) по (файл, координаты осей), с кэшем (задание BP).

    У вариативных шрифтов координаты осей берутся из записи list_fonts (поле var),
    и getGlyphSet(location=...) отдаёт ширины ИМЕННО этого экземпляра — hmtx напрямую
    вернул бы ширины дефолта, а для Condensed это ошибка почти в полтора раза.
    Шрифт не открылся — None (шрифта нет в системе)."""
    key = (file, coords)
    ent = _GS_CACHE.get(key)
    if ent is None:
        try:
            from fontTools.ttLib import TTFont
            tt = TTFont(file, lazy=True, fontNumber=0)
            ent = (tt.getGlyphSet(location=dict(coords)), tt["head"].unitsPerEm,
                   tt.getBestCmap())
        except Exception:
            ent = None
        _GS_CACHE[key] = ent
    return ent


def _glyph_bounds(file, coords, gn):
    """(xMin, yMin, xMax, yMax) контура глифа или None (пустой контур/шрифт не открылся).
    Кэш по (файл, координаты осей, имя глифа): у строки ролика десятки глифов, а контур
    одного глифа на весь кегль один и тот же (задание A1)."""
    key = (file, coords, gn)
    if key not in _GB_CACHE:
        ent = _glyph_set(file, coords)
        b = None
        if ent:
            try:
                from fontTools.pens.boundsPen import BoundsPen
                pen = BoundsPen(ent[0])
                ent[0][gn].draw(pen)
                b = pen.bounds
            except Exception:
                b = None
        _GB_CACHE[key] = b
    return _GB_CACHE[key]


def ink_extent(ps_name, text, size_px):
    """(asc, desc) чернил строки, px: максимум yMax и минус минимум yMin по глифам
    (задание A1). Нужно для шага строк интро: шаг считают по ЗАЗОРУ между буквами,
    а он зависит от шрифта — «хвост» вниз у верхней строки плюс высота букв нижней.
    Раньше шаг был жёсткими пикселями, и после смены шрифта малые строки наезжали на
    строку над ними (в AE это правил пользователь руками).

    Пробелы пропускаем (чернил не дают, а yMin/yMax у них нулевые). Шрифта, файла или
    глифа нет — None: по неизвестной высоте шаг не считаем, остаётся базовый (то же
    решение, что у text_width: неизвестное ширине/высоте хуже, чем прежнее поведение).
    """
    rec = next((r for r in list_fonts() if r["ps"] == ps_name), None)
    if not rec or not rec.get("file"):
        return None
    coords = tuple(sorted((rec.get("var") or {}).items()))
    ent = _glyph_set(rec["file"], coords)
    if not ent:
        return None
    _gs, upm, cmap = ent
    if not upm:
        return None
    asc = desc = None
    try:
        for ch in str(text or ""):
            if ch.isspace():
                continue
            gn = cmap.get(ord(ch))
            if gn is None:                   # глифа нет — высоту не знаем
                return None
            b = _glyph_bounds(rec["file"], coords, gn)
            if not b:
                continue                     # пустой контур (пробел-подобный глиф)
            y_min, y_max = b[1], b[3]
            asc = y_max if asc is None or y_max > asc else asc
            desc = -y_min if desc is None or -y_min > desc else desc
    except Exception:
        return None
    if asc is None:                          # чернил нет вовсе (одни пробелы)
        asc = desc = 0.0
    k = float(size_px) / upm
    return (asc * k, desc * k)


def text_width(ps_name, text, size_px):
    """Ширина строки (px) тем шрифтом, что увидит After Effects (задание BP). None,
    если шрифт не найден — автофит для этой группы НЕ применяется (ужать по неизвестной
    ширине хуже, чем не ужать). Сумма горизонтальных advance'ов по cmap, делённая на
    unitsPerEm и умноженная на size_px. Кернинг игнорируем: он даёт единицы процентов,
    а запас INTRO_FIT_W это 8%."""
    rec = next((r for r in list_fonts() if r["ps"] == ps_name), None)
    if not rec or not rec.get("file"):
        return None
    coords = tuple(sorted((rec.get("var") or {}).items()))
    ent = _glyph_set(rec["file"], coords)
    if not ent:
        return None
    gs, upm, cmap = ent
    total = 0.0
    try:
        for ch in str(text or ""):
            gn = cmap.get(ord(ch))
            if gn is None:               # глифа нет — ширину не знаем, не ужимаем
                return None
            total += gs[gn].width
    except Exception:
        return None
    return total * float(size_px) / upm


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    fs = list_fonts()
    print(f"шрифтов: {len(fs)}")
    for x in fs:
        if "sfpro" in x["ps"].lower() or "geologica" in x["ps"].lower():
            print(f"  {x['ps']}   [{x['family']}]")
