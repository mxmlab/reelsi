# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: каждый файл интерфейса `static/app/*.js` разбирается без синтаксической ошибки.

Файлы грузятся обычными <script> в один общий скоуп: синтаксическая ошибка в ОДНОМ из них
обрывает весь интерфейс — функции следующих файлов не объявлены, страница грузит пустое
состояние и сохраняет его поверх настоящего (так 01.10.2026 потерялась лишняя скобка в
`50-chrome.js`, а набор тестов был зелёный: стенды вырезают отдельные функции, файл целиком
не разбирал никто).
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "static", "app")
FILES = sorted(f for f in os.listdir(APP) if f.endswith(".js"))


@pytest.mark.skipif(not shutil.which("node"), reason="разбор требует node в PATH")
@pytest.mark.parametrize("name", FILES)
def test_static_app_js_parses(name):
    p = subprocess.run(["node", "--check", os.path.join(APP, name)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    assert p.returncode == 0, f"{name}: синтаксическая ошибка\n{p.stderr[-600:]}"
