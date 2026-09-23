# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""У каждого ключа стиля есть ручка, и ручка теперь одна — строка в схеме.

До JB ручка стиля заводилась ВРУЧНУЮ в трёх местах (templates/index.html — сам элемент,
fillStyleFields() — значение из стиля в поле, stEdit() — значение из поля обратно в
CURSTYLE), и ничто не проверяло, что она заведена. Так девять ключей styles.BASE жили
без ручки — поменять их из интерфейса было нельзя вообще, — а обратная дыра
(cam1_zoom, pop_lead, intro_riser_file интерфейс писал, а в BASE их не было) разводила
дефолт между стилем и build.py.

JB свёл обвязку к одному источнику: поле стиля заводится строкой в core/style_schema.py,
дефолт живёт только в styles.BASE, а панель (static/app/94-stylepanel.js) строится из
схемы. Поэтому сторож теперь про связку «схема <-> BASE» и про то, что ручной обвязки
не осталось ни в разметке, ни в других файлах JS.

Запуск: python -m pytest reelsi/tests -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta, style_schema, styles  # noqa: E402

PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _js_text():
    """Весь код интерфейса одной строкой: файлы static/app/ грузятся в общий скоуп."""
    return app_meta.app_js_text()


def _panel_js():
    """Код панели стиля — единственная дверь полей."""
    return _read(PANEL_JS)


def _code_only(js):
    """JS без комментариев: сторож про РУЧКУ, а закомментированная строка — не ручка."""
    js = re.sub(r"//.*$", "", js, flags=re.MULTILINE)
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    return js


def schema_items():
    """Обход схемы по порядку: [('layer'|'group'|'field', узел), ...]."""
    out = []

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                out.append(("group", it))
                walk(it.get("items", []))
            elif it.get("type") == "field":
                out.append(("field", it))

    for layer in style_schema.LAYERS:
        out.append(("layer", layer))
        walk(layer.get("items", []))
    return out


def schema_field(key):
    """Узел поля схемы по ключу (или по второму ключу пары X/Y); None — такого поля нет."""
    for kind, it in schema_items():
        if kind == "field" and (it.get("key") == key or it.get("key2") == key):
            return it
    return None


def schema_keys():
    """Ключи, которые панель показывает: поля (key и key2) и тумблеры слоёв/групп."""
    keys = set()
    for kind, it in schema_items():
        if kind == "field":
            if it.get("key"):
                keys.add(it["key"])
            if it.get("key2"):
                keys.add(it["key2"])
        if it.get("toggle"):
            keys.add(it["toggle"])
    return keys


def test_every_base_style_key_has_a_handle():
    """Каждый ключ styles.BASE заведён в схеме (или во внешних — label и intro_mode)."""
    missing = sorted(k for k in styles.BASE
                     if k not in schema_keys() and k not in style_schema.EXTERNAL)
    assert not missing, (
        "ключи styles.BASE без ручки в схеме (core/style_schema.py): " + ", ".join(missing)
    )


def test_every_schema_key_is_in_base():
    """Ключ схемы обязан быть в styles.BASE, иначе дефолт снова уедет в build.py."""
    unknown = sorted(k for k in schema_keys()
                     if k not in styles.BASE and k not in style_schema.EXTERNAL)
    assert not unknown, (
        "схема знает ключи, которых нет в styles.BASE: " + ", ".join(unknown)
    )


def test_fields_are_wired_only_in_the_panel():
    """fillStyleFields/stEdit объявлены ТОЛЬКО в панели: у поля не должно быть двух дверей."""
    panel = _panel_js()
    for fn in ("fillStyleFields", "stEdit"):
        assert re.search(r"\bfunction\s+%s\s*\(" % fn, panel), (
            "в 94-stylepanel.js нет function %s" % fn)
    bad = []
    for path in app_meta.app_js_files():
        name = os.path.basename(path)
        if name == "94-stylepanel.js":
            continue
        text = _code_only(_read(path))
        for fn in ("fillStyleFields", "stEdit"):
            if re.search(r"\bfunction\s+%s\s*\(" % fn, text):
                bad.append("%s: function %s" % (name, fn))
    assert not bad, "ручная обвязка полей стиля вернулась в другие файлы: " + ", ".join(bad)


def test_index_html_has_no_old_style_markup():
    """Полей стиля в разметке больше нет: их строит панель по схеме."""
    html = _read(os.path.join(ROOT, "templates", "index.html"))
    for token in ("stylepart_", "stsec_", "stfold", "stylegrid", "stylemats"):
        assert token not in html, "в index.html осталась старая разметка стиля: " + token
    assert 'id="stpanel"' in html and 'class="stpanel"' in html, (
        "в index.html нет контейнера панели #stpanel")
    assert 'role="tree"' in html, "панель перестала быть деревом (role=tree)"
