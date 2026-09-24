# SPDX-License-Identifier: AGPL-3.0-or-later
# -*- coding: utf-8 -*-
"""Собрать ключи t('...') из static/app/*.js и показать, чего нет в en.json.

Временный инструмент для фазы перевода JS-строк (Фаза 3 плана). Через некоторое
время логично влить в tools/i18n_extract.py, когда работа закончится.
"""
import io
import json
import os
import re
import sys
from typing import Any, cast

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "static", "app")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

# t('...') с произвольным содержимым: строка может содержать \' и {n}
T = re.compile(r"\bt\('((?:[^'\\]|\\.)*)'")

CYR = re.compile(r"[А-Яа-яЁё]")


def _js_unescape(s: str) -> str:
    """Ключ в исходнике — с экранированием, в словаре — как есть (t() ищет по
    runtime-строке). Иначе ключ с \n «не находится», и перевод молча не работает."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r",
                                       "\\": "\\", "'": "'", '"': '"',
                                       "0": "\0"}.get(m.group(1), m.group(1)), s)


def main() -> int:
    keys: dict[str, list[str]] = {}
    for f in sorted(os.listdir(APP)):
        if not f.endswith(".js"):
            continue
        src = io.open(os.path.join(APP, f), encoding="utf-8").read()
        for m in T.finditer(src):
            k = _js_unescape(m.group(1))
            if CYR.search(k):
                keys.setdefault(k, []).append(f)
    have = {}
    if os.path.exists(EN):
        have = json.load(io.open(EN, encoding="utf-8"))
    missing = {k: v for k, v in keys.items() if k not in have}
    print(f"t('...') ключей всего: {len(keys)}, без перевода: {len(missing)}")
    for k in sorted(missing):
        print("  ", k, "|", ",".join(sorted(set(missing[k]))))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            cast(Any, _s).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
