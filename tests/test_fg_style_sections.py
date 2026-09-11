# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FG: окно стиля — сворачиваемые секции и скрытие настроек выключенного рото.

Вкладка «Текст» — 52 контрола в одной плоской сетке, что к чему не понять. Настройки
ротоскопа показывались всегда, даже когда «Авто-ротоскоп» снят. Теперь: внутри вкладок
сворачиваемые секции (details.stsec), а «Устройство рото»/«Низ маски»/«Рото только на
Камере 1» спрятаны в обёртку #rotowrap, видимую только при включённом #roto. Обёртка
управляется из ОБЕИХ дверей — rotoSync() (клик по галке) и fillStyleFields() (загрузка
стиля) — иначе после F5 она разойдётся с галкой. Состояние секций живёт в localStorage
(reelsi_stylesec_<имя>), дефолт — «Субтитры» развёрнута.

Ничего из настроек не удалено и не переименовано; сторож ключей стиля
(test_style_keys_in_ui.py) зелёный без правок.

Запуск: python -m pytest reelsi/tests -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402

HTML = io.open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
JS = app_meta.app_js_text()

# Секции по вкладкам: заголовок summary, id details, ключ localStorage.
SECTIONS = [
    ("stsec_subs", "Субтитры"), ("stsec_yellow", "Жёлтые слова"),
    ("stsec_subbg", "Плашка субтитров"), ("stsec_caption", "Подпись"),
    ("stsec_intro", "Интро"), ("stsec_disc", "Дисклеймер"),
    ("stsec_cam1", "Камера 1"), ("stsec_topline", "Верхняя строка"),
    ("stsec_blur", "Размытие на старте"),
    ("stsec_photo", "Фото"), ("stsec_roto", "Ротоскоп"),
]


def test_every_section_has_title_and_toggle():
    """Каждая секция — details с summary-заголовком (переключатель сворачивания)."""
    for sec_id, title in SECTIONS:
        m = re.search(r'<details class="stfold stsec"[^>]*id="%s"[^>]*><summary>([^<]+)</summary>' % sec_id, HTML)
        assert m, f"секция {sec_id} не нашлась как details.stsec с summary"
        assert m.group(1) == title, f"{sec_id}: заголовок {m.group(1)!r} != {title!r}"
        assert "stsec-b" in HTML[m.start():m.start() + 400], f"{sec_id}: нет тела секции"


def test_subs_section_open_by_default():
    """«Субтитры» развёрнута по умолчанию (open), остальные свёрнуты."""
    assert re.search(r'<details class="stfold stsec"[^>]*id="stsec_subs"[^>]* open>', HTML), (
        "секция «Субтитры» не open по умолчанию")
    for sec_id, _t in SECTIONS:
        if sec_id == "stsec_subs":
            continue
        assert not re.search(r'<details class="stfold stsec"[^>]*id="%s"[^>]* open>' % sec_id, HTML), (
            f"секция {sec_id} не должна быть open по умолчанию")


def test_roto_settings_inside_wrapper():
    """«Устройство рото», «Низ маски» и «Рото только на Камере 1» — внутри #rotowrap."""
    wrap = re.search(r'<div id="rotowrap"[^>]*>(.*?)</div>\s*</div></details>', HTML, re.S)
    assert wrap, "обёртка #rotowrap не нашлась"
    body = wrap.group(1)
    for field in ('id="st_rotodev"', 'id="rotobottom"', 'id="roto_cam1only"'):
        assert field in body, f"поле {field} не внутри #rotowrap"


def test_roto_wrapper_controlled_by_both_doors():
    """Обёртка рото упоминается в rotoSync() и в fillStyleFields() — обе двери."""
    # rotoSync: клик по галке «Авто-ротоскоп» прячет/показывает обёртку
    body = JS[JS.index("function rotoSync()"):JS.index("// ---- живые подсказки")]
    assert "rotoWrapUI()" in body, "rotoSync не управляет обёрткой рото"
    # fillStyleFields: при загрузке стиля обёртка приводится к галке (иначе после F5 разойдётся)
    ff = JS[JS.index("function fillStyleFields()"):JS.index("function updateStyleDiffDots()")]
    assert "rotoWrapUI()" in ff, "fillStyleFields не приводит обёртку к галке"
    # сама функция прячет/показывает по #roto
    rw = JS[JS.index("function rotoWrapUI()"):JS.index("// Сворачиваемые секции")]
    assert "$('roto')" in rw and "rotowrap" in rw, "rotoWrapUI не читает галку #roto"


def test_section_state_in_localstorage():
    """Состояние секций запоминается в localStorage (reelsi_stylesec_<имя>), дефолт — «Субтитры»."""
    assert "reelsi_stylesec_" in JS, "нет ключа localStorage для секций"
    assert "STYLESEC_DEFAULT={'stsec_subs':true}" in JS, "дефолт «Субтитры» развёрнута не задан"
    assert "localStorage.setItem(styleSecKey(d.id)" in JS, "состояние секции не пишется"
    assert "styleSecApply()" in JS, "секции не восстанавливаются при загрузке"
    # восстановление вызывается и при заполнении полей стиля (после F5)
    ff = JS[JS.index("function fillStyleFields()"):JS.index("function updateStyleDiffDots()")]
    assert "styleSecApply()" in ff, "состояние секций не применяется при загрузке стиля"
