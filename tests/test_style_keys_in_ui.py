# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""У каждого ключа стиля есть ручка в UI, и сторож не даёт завести ключ без неё (задание FB).

Ручка стиля заводилась ВРУЧНУЮ в трёх местах (templates/index.html — сам элемент,
fillStyleFields() — значение из стиля в поле, stEdit() — значение из поля обратно в
CURSTYLE), и ничто не проверяло, что она заведена. Так девять ключей styles.BASE жили
без ручки — поменять их из интерфейса было нельзя вообще, и стиль приходилось править
прямо в styles/*.json. Обратная дыра: cam1_zoom, pop_lead, intro_riser_file интерфейс
писал, а в styles.BASE их не было — дефолты жили врассыпную по xml2ae/build.py.

Тест стережёт связку в обе стороны: каждый ключ styles.BASE обязан писаться в CURSTYLE
из JS (значит у него есть ручка), и каждый ключ, который JS так пишет, обязан быть в
styles.BASE (иначе дефолт снова разъедется между стилем и build.py). Списка исключений
нет: после задания FB он пустой, и пусть таким остаётся.

Запуск: python -m pytest reelsi/tests -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402


def _js_text():
    parts = []
    for name in sorted(os.listdir(os.path.join(ROOT, "static", "app"))):
        if name.endswith(".js"):
            parts.append(io.open(os.path.join(ROOT, "static", "app", name),
                                 encoding="utf-8").read())
    return "\n".join(parts)


def _code_only(js):
    """JS без комментариев: сторож про РУЧКУ, а закомментированная строка — не ручка."""
    js = re.sub(r"//.*$", "", js, flags=re.MULTILINE)
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    return js


def _written_keys(js):
    """Ключи, которые JS пишет в CURSTYLE: `CURSTYLE.<key>=` и `CURSTYLE['<key>']=`."""
    js = _code_only(js)
    out = set()
    for m in re.finditer(r"CURSTYLE\.([A-Za-z_][A-Za-z0-9_]*)\s*=", js):
        out.add(m.group(1))
    for m in re.finditer(r"CURSTYLE\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]\s*=", js):
        out.add(m.group(1))
    return out


def test_every_base_style_key_has_a_handle():
    """Каждый ключ styles.BASE пишется в CURSTYLE из JS — значит у него есть ручка."""
    js = _js_text()
    written = _written_keys(js)
    missing = sorted(k for k in styles.BASE if k not in written)
    assert not missing, (
        "ключи styles.BASE без ручки в UI (нет записи CURSTYLE.<key>= в static/app/*.js): "
        + ", ".join(missing)
    )


def test_every_js_written_style_key_is_in_base():
    """Ключ, который JS пишет в CURSTYLE, обязан быть в styles.BASE.

    Иначе дефолт живёт врассыпную по xml2ae/build.py, а не в стиле, и правка в
    интерфейсе расходится с тем, что подставляет сборка.
    """
    js = _js_text()
    written = _written_keys(js)
    unknown = sorted(k for k in written if k not in styles.BASE)
    assert not unknown, (
        "JS пишет в CURSTYLE ключи, которых нет в styles.BASE: " + ", ".join(unknown)
    )
