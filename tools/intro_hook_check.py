# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Числа приёмки хука ИИ-интро (задание BF2, 2026-08-14): два числа из таблицы —
доля строк в одно слово и доля строк, кончающихся служебным словом.

Эталон (38 ручных .jsx, правила интро): строк в одно слово 63 %,
строк со служебным словом на конце 15 %. Цель прогона — >= 50 % и <= 20 %.

Список служебных слов — импорт из aicut (INTRO_FUNC_WORDS), второй копии нет.

Запуск: python -X utf8 tools/intro_hook_check.py <stem>.intro.json [<stem>.intro.json ...]
Рядом с .intro.json обязан лежать .xml того же стемы — слова строк берутся оттуда.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402
from core.aicut.commands import INTRO_FUNC_WORDS  # noqa: E402


def check(intro_path):
    """Один .intro.json -> (строк в одно слово, строк со служебным словом в конце, всего строк)."""
    if not intro_path.endswith(".intro.json"):
        raise ValueError(f"жду путь к .intro.json, пришло: {intro_path}")
    xml_path = intro_path[:-len(".intro.json")] + ".xml"
    words = aicut._words_from_xml(xml_path)
    data = json.load(open(intro_path, encoding="utf-8"))
    one_word = func_end = total = 0
    k = 0
    for r in data.get("intro_rows") or []:
        n = max(1, r.get("count") or 1)
        ws = [w[1] for w in words[k:k + n]]
        k += n
        if len(ws) != n:                           # хвост обрезан потолком INTRO_MAX_WORDS
            continue
        total += 1
        if n == 1:
            one_word += 1
        if ws and ws[-1] in INTRO_FUNC_WORDS:
            func_end += 1
    p_one = 100 * one_word / total if total else 0
    p_func = 100 * func_end / total if total else 0
    print(f"{os.path.basename(intro_path)}: строк {total} · в одно слово {p_one:.0f}% "
          f"({one_word}/{total}) · со служебным словом на конце {p_func:.0f}% "
          f"({func_end}/{total})")
    return one_word, func_end, total


def main():
    if len(sys.argv) < 2:
        print("использование: python tools/intro_hook_check.py <stem>.intro.json [...]")
        return 1
    s1 = sf = st = 0
    for p in sys.argv[1:]:
        one, func, total = check(p)
        s1 += one
        sf += func
        st += total
    if len(sys.argv) > 2 and st:
        print(f"итого: в одно слово {100 * s1 / st:.0f}% ({s1}/{st}) · "
              f"со служебным словом {100 * sf / st:.0f}% ({sf}/{st})")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
