# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Каждый id, который ищет фронт, существует: в разметке или создаётся в JS.

Обращение к несуществующему элементу в JS не падает — оно молча делает пустоту:
`$('qcount')` возвращает null, и либо код прикрыт проверкой и потому мёртв, либо
падает уже в браузере у пользователя. Так и жила панель слов предпросмотра нарезки:
`pvwOpen` рисовала в `#pvwords`, `#pvwintro`, `#pvwres`, `#pvwsave`, которых в
index.html нет с 2026-09-08 (контейнеры удалены, код остался), а строка счётчиков
очереди писала в `#qcount` из удалённого оверлея.

Правило: литеральный id из `$('…')` / `getElementById('…')` обязан найтись либо
среди `id="…"` в templates/index.html, либо среди id, которые JS создаёт сам
(`id="…"`, `.id='…'`, `setAttribute('id', '…')` и их конкатенации `id="stage_chk_'+k`).
Исключений нет: появилось расхождение — это либо мёртвый код, либо опечатка, и
чинится оно в коде, а не списком в тесте.

Запуск:  python -m pytest reelsi/tests -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402

HTML = os.path.join(ROOT, "templates", "index.html")

# $('x') и document.getElementById('x'); кавычки любые.
LOOKUP = re.compile(r"""(?:\$|getElementById)\(\s*['"]([^'"]*)['"]\s*\)""")
# id, создаваемый в JS: id="x" в строке разметки, el.id='x', setAttribute('id','x').
CREATE_LIT = re.compile(
    r"""(?:\bid\s*=\s*['"]|\.id\s*=\s*['"]|setAttribute\(\s*['"]id['"]\s*,\s*['"])([^'"]+)""")
# ...и его конкатенация: id="stage_chk_'+k+'" — литерал кончается на «_».
CREATE_CONCAT = re.compile(
    r"""(?:\bid\s*=\s*['"]|\.id\s*=\s*['"]|\$\(\s*['"]|"""
    r"""setAttribute\(\s*['"]id['"]\s*,\s*['"])([A-Za-z_][\w-]*_)['"]\s*\+""")
HTML_ID = re.compile(r'\bid="([^"]+)"')


def _read(path):
    return io.open(path, encoding="utf-8").read()


def test_every_looked_up_id_exists():
    """Литеральные id из JS есть в разметке или создаются в JS. Список исключений пуст."""
    html_ids = set(HTML_ID.findall(_read(HTML)))

    lookups = {}          # id -> ["файл:строка", ...]
    created = set()       # id, созданные в JS
    prefixes = set()      # префиксы конкатенаций: 'stage_chk_' и т.п.

    for path in app_meta.app_js_files():
        base = os.path.basename(path)
        for n, line in enumerate(_read(path).splitlines(), 1):
            for m in LOOKUP.finditer(line):
                lookups.setdefault(m.group(1), []).append("%s:%d" % (base, n))
            for m in CREATE_LIT.finditer(line):
                created.add(m.group(1))
            for m in CREATE_CONCAT.finditer(line):
                prefixes.add(m.group(1))

    assert lookups, "ни одного $('…') не нашлось — сканер сломан, тест ничего не проверяет"

    missing = {k: v for k, v in lookups.items()
               if k not in html_ids and k not in created
               and not any(k.startswith(p) for p in prefixes)}
    assert not missing, (
        "JS ищет элементы, которых нет ни в index.html, ни среди создаваемых в JS "
        "(мёртвый код или опечатка в id):\n"
        + "\n".join("  %s — %s" % (k, ", ".join(v[:4])) for k, v in sorted(missing.items())))


def test_no_id_is_created_twice_by_different_files():
    """Один id не создаётся в двух файлах интерфейса — иначе второй молча затрёт первый.

    Сканер тот же, что выше, но смотрит не «есть ли», а «сколько раз»: дубли в
    id-шниках ловятся в разметке (test_ui_static.test_new_tools_ids_are_unique), а в
    JS-разметке до сих пор не проверялись.
    """
    where = {}
    for path in app_meta.app_js_files():
        base = os.path.basename(path)
        for m in CREATE_LIT.finditer(_read(path)):
            where.setdefault(m.group(1), set()).add(base)
    dupes = {k: sorted(v) for k, v in where.items() if len(v) > 1}
    assert not dupes, "один id создаётся в нескольких файлах: " + repr(dupes)
