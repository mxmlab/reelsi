# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подстановка значений в JS-шаблон: экранирование строк, числа, многострочный текст.

Мелко, но критично: невалидный литерал роняет импорт .jsx и весь проект AE целиком,
а узнать об этом иначе можно только открыв Adobe.
"""
import json
import os
import re
from typing import Any, Callable

# Символы, требующие явного экранирования в JS-литералах:
# - U+2028 (Line Separator), U+2029 (Paragraph Separator): ExtendScript (ES3)
#   считает их переводом строки, ломая строковый литерал; node --check (ES2019+) этого не видит.
# - Одиночные суррогаты U+D800..U+DFFF: имена файлов с нечитаемыми байтами (surrogateescape
#   на Linux) при записи .jsx в utf-8-sig вызывают UnicodeEncodeError.
# - U+FFFE, U+FFFF: noncharacters Юникода, недопустимые в XML и проблемные для парсеров.
_JS_UNSAFE_PAT = re.compile(r"[\u2028\u2029\ud800-\udfff\ufffe\uffff]")


def _js(s: Any) -> str:
    """JS-литерал строки: кавычки, слэши и управляющие символы экранирует json.dumps.
    Явно в \\uXXXX экранируются:
    - U+2028/U+2029: ExtendScript (ES3) считает их переводом строки: сырой символ
      рвёт литерал и валит импорт всего .jsx, а `node --check` (ES2019) этого не видит.
    - Одиночные суррогаты \\ud800-\\udfff: возникают при нечитаемых байтах в именах файлов
      (os.listdir с surrogateescape на Linux); сырой суррогат роняет запись файла в utf-8-sig
      (UnicodeEncodeError: surrogates not allowed).
    - U+FFFE/U+FFFF: несимволы (noncharacters) Юникода, проблемные для парсеров и XML.

    ensure_ascii=True тут НЕ используется намеренно: кириллица уехала бы в \\uXXXX
    и поехал бы эталон tests/fixtures/golden_geometry.jsx (эталоны не
    перегенерируются, кроме намеренной смены дефолтной сборки; тогда эталон обновляется
    в том же коммите, а изменение записывается в CHANGELOG.md). Вся кириллица и прочий
    валидный юникод остаются сырыми."""
    return _JS_UNSAFE_PAT.sub(
        lambda m: f"\\u{ord(m.group()):04x}",
        json.dumps(str(s), ensure_ascii=False, separators=(",", ":")),
    )


def _jd(obj: Any) -> str:
    """Python-структура -> JS-литерал. Валидный JSON = валидный JS; json.dumps с
    ensure_ascii экранирует ВСЕ управляющие символы/кавычки/юникод — целый класс
    escape-багов (сырой \\n в слове рвал строку .jsx) закрыт по построению."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def _r(x: float | int | str, nd: int = 4) -> int | float:
    """Компактный float для JS-структур (тайминги в сек, проценты): 4 знаков хватает
    с запасом (1 кадр @60fps = 0.0167с), а json не тащит хвосты вида .20000000004."""
    v = round(float(x), nd)
    return int(v) if v == int(v) else v


def _fill_js(rgb: Any) -> str:
    """[r,g,b] 0..1 -> JS-массив; на входе список/кортеж или None."""
    r = list(rgb or [1, 0.9176, 0])[:3]
    while len(r) < 3:
        r.append(0.0)
    return "[%g,%g,%g]" % tuple(r)


def _asset_or(override: Any, default_key: str, aset: Callable[[str], str]) -> str:
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


def _js_multiline(s: Any) -> str:
    """JS string literal where Python newlines become AE line breaks (\\r):
    \\n -> \\r, \\r из входа выбрасываем — до экранирования."""
    return _js(str(s).replace("\r", "").replace("\n", "\r"))
