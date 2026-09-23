# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: в static/app/*.js нет двух объявлений function <имя> верхнего уровня с одним именем."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_duplicate_toplevel_js_functions():
    pattern = re.compile(r"^(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(")
    app_dir = os.path.join(ROOT, "static", "app")
    js_files = sorted(glob.glob(os.path.join(app_dir, "*.js")))
    assert js_files, f"Файлы static/app/*.js не найдены в {app_dir}"

    funcs = {}
    for path in js_files:
        rel = os.path.relpath(path, ROOT)
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                m = pattern.match(line)
                if m:
                    name = m.group(1)
                    funcs.setdefault(name, []).append((rel, line_no))

    duplicates = {name: locs for name, locs in funcs.items() if len(locs) > 1}
    assert not duplicates, f"Обнаружены дубликаты функций верхнего уровня в static/app/*.js: {duplicates}"
