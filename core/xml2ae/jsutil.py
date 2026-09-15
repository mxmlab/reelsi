# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подстановка значений в JS-шаблон: экранирование строк, числа, многострочный текст.

Мелко, но критично: невалидный литерал роняет импорт .jsx и весь проект AE целиком,
а узнать об этом иначе можно только открыв Adobe.
"""
import os, json


def _js(s):
    """JS-литерал строки: кавычки, слэши и управляющие символы снимает json.dumps
    (целый класс escape-багов закрыт по построению), а U+2028/U+2029 — отдельно.
    ExtendScript (ES3) считает их переводом строки: сырой символ рвёт литерал и
    валит импорт всего .jsx, а `node --check` (ES2019) этого не видит.

    ensure_ascii=True тут НЕ используется намеренно: кириллица уехала бы в \\uXXXX
    и поехал бы эталон tests/fixtures/golden_geometry.jsx (задание HT: эталоны не
    перегенерируются)."""
    return (json.dumps(str(s), ensure_ascii=False, separators=(",", ":"))
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _jd(obj):
    """Python-структура -> JS-литерал. Валидный JSON = валидный JS; json.dumps с
    ensure_ascii экранирует ВСЕ управляющие символы/кавычки/юникод — целый класс
    escape-багов (сырой \\n в слове рвал строку .jsx) закрыт по построению."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def _r(x, nd=4):
    """Компактный float для JS-структур (тайминги в сек, проценты): 4 знаков хватает
    с запасом (1 кадр @60fps = 0.0167с), а json не тащит хвосты вида .20000000004."""
    v = round(float(x), nd)
    return int(v) if v == int(v) else v


def _fill_js(rgb):
    """[r,g,b] 0..1 -> JS-массив; на входе список/кортеж или None."""
    r = list(rgb or [1, 0.9176, 0])[:3]
    while len(r) < 3:
        r.append(0.0)
    return "[%g,%g,%g]" % tuple(r)


def _asset_or(override, default_key, aset):
    """override: путь к файлу | ключ assets.json | None. None -> дефолтный ключ.
    Абсолютный существующий путь берётся как есть; иначе пробуем как ключ; иначе дефолт."""
    if override:
        s = str(override)
        if os.path.isabs(s) and os.path.isfile(s):
            return s
        got = aset(s)
        if got:
            return got
    return aset(default_key)


def _js_multiline(s):
    """JS string literal where Python newlines become AE line breaks (\\r):
    \\n -> \\r, \\r из входа выбрасываем — до экранирования."""
    return _js(str(s).replace("\r", "").replace("\n", "\r"))
