# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FG (в редакции JB): сворачиваемые группы панели и скрытие настроек выключенного рото.

До JB настройки стиля лежали плоской сеткой в пяти вкладках, внутри — свёрнутые
секции (details.stsec), а «Устройство рото»/«Низ маски»/«Рото только на Камере 1»
прятались в обёртку #rotowrap, видимую только при включённом рото. Обёртку надо было
приводить к галке из ДВУХ дверей — rotoSync() (клик по галке) и fillStyleFields()
(загрузка стиля), иначе после F5 она расходилась с галкой.

JB свёл это к общим правилам панели: строки и группы строятся из схемы
(core/style_schema.py), раскрытие запоминается в localStorage `reelsi_sttw_<id>`,
а видимость считает одна updateStyleVisibility() — она вызывается и из
fillStyleFields(), и из stEdit(), то есть ровно из тех же двух дверей. Поэтому тест
сторожит уже их: заголовки у слоёв и групп, дефолт раскрытия, принадлежность полей
рото слою с тумблером `roto` и обе двери видимости.

Запуск: python -m pytest reelsi/tests -q
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402

JS = app_meta.app_js_text()
PANEL = watcher._panel_js()


def _items():
    return watcher.schema_items()


def test_every_layer_and_group_has_title_and_toggle():
    """У каждого слоя и каждой группы схемы есть заголовок — строка-переключатель в панели."""
    rows = [(kind, it) for kind, it in _items() if kind in ("layer", "group")]
    assert rows, "в схеме не осталось ни слоёв, ни групп"
    for kind, it in rows:
        assert it.get("label"), f"{kind} {it.get('id')}: нет заголовка"
        if kind == "group":
            assert it.get("id"), "у группы нет id — состояние раскрытия негде хранить"
        if it.get("items"):
            assert it["items"], f"{it.get('id')}: пустое тело группы"
    # панель рисует строку-заголовок с треугольником и aria-expanded (задание JB п. 4)
    assert "'role', 'treeitem'" in PANEL
    assert "aria-expanded" in PANEL and "sttw" in PANEL


def test_subs_layer_open_by_default():
    """По умолчанию раскрыты слой `subs` и группа `subs.text` — остальное свёрнуто."""
    assert "return id === 'subs' || id === 'subs.text';" in PANEL, (
        "дефолт раскрытия панели изменился (ждали: раскрыт слой subs и группа subs.text)")
    ids = {it.get("id") for kind, it in _items() if kind in ("layer", "group")}
    assert {"subs", "subs.text"} <= ids, "в схеме нет слоя subs или группы subs.text"


def test_roto_settings_live_in_the_roto_layer():
    """«Низ маски», «Устройство рото» и «Рото только на Камере 1» — в слое с тумблером roto."""
    layer = next((it for kind, it in _items()
                  if kind == "layer" and it.get("id") == "roto"), None)
    assert layer, "в схеме пропал слой roto"
    assert layer.get("toggle") == "roto", "у слоя рото пропал тумблер включения"
    keys = set()
    for kind, it in _items():
        if kind != "field":
            continue
        keys.add(it.get("key"))
    for key in ("roto_bottom", "roto_device", "roto_cam1_only"):
        assert key in keys, f"поле {key} пропало из схемы"
        assert watcher.schema_field(key) is not None
    # поля рото объявлены внутри того же слоя, что и тумблер
    inside = [it.get("key") for it in layer.get("items", [])]
    assert inside == ["roto_bottom", "roto_device", "roto_cam1_only"], inside


def test_visibility_is_driven_from_both_doors():
    """Видимость приводят к значению тумблера ОБЕ двери: fillStyleFields() и stEdit()."""
    vis = PANEL[PANEL.index("function updateStyleVisibility()"):
                PANEL.index("function updateStyleDiffDots()")]
    assert "stbody_" in vis and "show_if" in vis, "updateStyleVisibility не прячет поля/группы"
    # тело группы прячется по её тумблеру (раньше это делала обёртка #rotowrap)
    assert "item.toggle" in vis, "видимость группы не читает её тумблер"
    for fn in ("fillStyleFields", "stEdit"):
        body = PANEL[PANEL.index("function %s()" % fn):]
        body = body[:body.index("\nfunction ")]
        assert "updateStyleVisibility();" in body, (
            "%s не приводит видимость к тумблерам — после F5 она разойдётся" % fn)


def test_expanded_state_in_localstorage():
    """Раскрытие строк живёт в localStorage `reelsi_sttw_<id>` и переживает F5."""
    assert "reelsi_sttw_" in PANEL, "нет ключа localStorage для раскрытия строк"
    assert "localStorage.setItem('reelsi_sttw_'" in PANEL, "состояние строки не пишется"
    assert "localStorage.getItem('reelsi_sttw_'" in PANEL, "состояние строки не читается"
    # доступ к localStorage — в try/catch: в приватном окне он бросает, и панель
    # не должна из-за этого не построиться
    body = PANEL[PANEL.index("function isExpanded("):PANEL.index("function getStyleParent(")]
    assert body.count("try {") >= 2 and body.count("catch (e)") >= 2, (
        "localStorage читается/пишется без try/catch")
    assert re.search(r"function isExpanded\(", PANEL), "пропала isExpanded"
