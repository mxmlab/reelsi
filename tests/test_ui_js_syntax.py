# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Синтаксис JS фронта (`static/app/*.js`) — node --check, как у .jsx в CI.

Джоба `jsx` в CI проверяет node --check'ом только ExtendScript, который уезжает в
After Effects, — и это заметно: файлы интерфейса не проверялись НИЧЕМ. Опечатка в
них не роняет ни один python-тест (те читают код регулярками), а страница падает
целиком: пятнадцать обычных <script>-тегов в общем скоупе, и синтаксическая ошибка
в одном убивает весь интерфейс, включая кнопки шага, которые до неё работали.

Проверяется каждый файл отдельно, а не склейка: браузер грузит их отдельными
<script>, и порядок имён задаётся app_meta.app_js_files() — список берётся из папки,
новый файл попадает и сюда.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402

pytestmark = pytest.mark.skipif(
    not shutil.which("node"),
    reason="node --check требует node в PATH (в CI он есть)")


def test_static_app_js_syntax():
    """Каждый файл static/app/ парсится node — как .jsx в CI-джобе jsx."""
    files = app_meta.app_js_files()
    assert len(files) >= 15, "файлов интерфейса стало меньше — список берётся из папки?"
    bad = []
    for path in files:
        p = subprocess.run(["node", "--check", path],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            bad.append("%s:\n%s" % (os.path.basename(path), p.stderr.strip()[:400]))
    assert not bad, ("файлы интерфейса не прошли node --check (страница не "
                     "загрузится целиком):\n" + "\n".join(bad))
