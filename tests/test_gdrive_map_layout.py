# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно «куда скачать каждую папку» (гугл-диск): строка не сжимает имя в столбик.

Симптом на окне 640 px: длинный путь папки камеры в выпадашке забирал почти всю ширину,
имя и размер переносились, «4.66 ГБ» уезжал на отдельную строку.

Контракт статический, по исходнику `gdMapAsk` (static/app/40-queue.js) и разметке модалки
(templates/index.html): левая колонка с `min-width:0`, имя в одну строку с многоточием и
`title`, размер в одну строку, правая колонка 55 %, модалка 720 px. Логику окна (collect,
refresh, промис) тест не трогает. Мутация «убрать min-width:0 у левой колонки» красит тест.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
INDEX_HTML = os.path.join(ROOT, "templates", "index.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _row_block():
    """Сборка строки окна: от `row.innerHTML=` до добавления строки в контейнер."""
    src = _read(QUEUE_JS)
    start = src.index("function gdMapAsk(")
    begin = src.index("row.innerHTML=", start)
    end = src.index("host.appendChild(row)", begin)
    return src[begin:end]


def test_left_column_can_shrink_below_its_content():
    """Левая колонка (имя и размер): flex-basis 0 и min-width:0 — иначе длинная выпадашка
    выдавливает её в столбик."""
    left = re.search(r'<div class="grow" style="([^"]*)"', _row_block())
    assert left, "левой колонки строки окна нет"
    assert "flex:1 1 0" in left.group(1), f"у левой колонки не flex:1 1 0: {left.group(1)!r}"
    assert "min-width:0" in left.group(1), f"у левой колонки нет min-width:0: {left.group(1)!r}"


def test_name_is_one_line_with_ellipsis_and_full_title():
    """Имя — одной строкой с многоточием, полное имя — в title."""
    block = _row_block()
    name = re.search(r'<div style="([^"]*)" title=', block)
    assert name, "строки имени с title в окне нет"
    for rule in ("white-space:nowrap", "overflow:hidden", "text-overflow:ellipsis"):
        assert rule in name.group(1), f"имя не в одну строку с многоточием: нет {rule}"
    assert "title=\"'+esc(r.name)+'\"" in block, "полное имя не вынесено в title"


def test_size_line_stays_on_one_line():
    """Строка «N файлов · размер» не переносится: иначе размер уезжает на свою строку."""
    size = re.search(r'<div class="muted" style="([^"]*)"', _row_block())
    assert size, "строки размера в окне нет"
    assert "white-space:nowrap" in size.group(1), f"размер переносится: {size.group(1)!r}"


def test_select_column_takes_55_percent_and_select_fills_it():
    """Правая колонка — 55 % с min-width:0, выпадашка на всю свою колонку."""
    block = _row_block()
    right = re.search(r'<div style="(flex:0 0 55%[^"]*)"', block)
    assert right, "правой колонки с долей 55 % в окне нет"
    assert "min-width:0" in right.group(1), f"у правой колонки нет min-width:0: {right.group(1)!r}"
    select = re.search(r'<select[^>]*style="([^"]*)"', block)
    assert select, "выпадашки в строке окна нет"
    assert "width:100%" in select.group(1), f"выпадашка не на всю колонку: {select.group(1)!r}"


def test_gdmap_modal_is_720_wide():
    """Модалка окна — 720 px (640 не хватало под длинный путь папки камеры)."""
    html = _read(INDEX_HTML)
    m = re.search(r'id="mbGdMap".*?<div class="modal" style="([^"]*)"', html, re.S)
    assert m, "модалка окна «куда скачать» не найдена"
    assert "max-width:720px" in m.group(1), f"модалка не 720 px: {m.group(1)!r}"
