# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Ссылки «Кадр и зум камеры — в стиле героя» в редакторе спикера больше нет.

Дверь не работала (владелец: «не работает открытие стиля, давай просто уберём эту
кнопку»), и ушла вместе с обвязкой, которая держалась только на ней:

1. сама ссылка под полем «Формат» и её «!» (`#spk_camstyle`, `#spk_camstyle_hint`)
   вместе с обработчиком `spkCamStyleGo`;
2. сеанс правки шаблона (`SPKSTYLESESS`): пока панель стиля стояла в модалке
   спикера, `captureAE` не писал стиль в задание открытого клипа, а закрытие
   модалки возвращало панель к снимку — и то и другое существовало ровно ради
   ссылки, поэтому из `50-chrome.js` и `90-ae.js` это ушло;
3. второй хозяин блока `#stylebox` — хост `#spkstyle` в редакторе спикера: у
   `styleToModal` больше нет параметра, блок стиля переезжает только во вкладку
   «Стиль» предпросмотра AE;
4. `stGroupOfField` (`94-stylepanel.js`) — поиск группы по ключу поля, которым
   ссылка находила «Transform» Камеры 1.

Общий механизм панели остался: `stFocusGroup` зовёт сам `renderStylePanel`, а
`styleToModal`/`styleHome` — вкладка «Стиль» предпросмотра (`80-inserts.js`).

Сторож держит именно УДАЛЕНИЕ: вернётся ссылка, обработчик, сеанс или второй
хозяин блока стиля — тест краснеет. Полуссылка без обработчика — тот же
ReferenceError по клику, поэтому разметка и код проверяются вместе.

Мутация: вернуть в `index.html` кнопку `<button id="spk_camstyle" onclick="spkCamStyleGo()">`
— падает `test_link_markup_is_gone`.

Запуск: py -3.10 -m pytest tests/test_cam_style_link_gone.py -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402

HTML = os.path.join(ROOT, "templates", "index.html")
CSS = os.path.join(ROOT, "static", "app.css")
AE_JS = os.path.join(ROOT, "static", "app", "90-ae.js")
CHROME_JS = os.path.join(ROOT, "static", "app", "50-chrome.js")
INSERTS_JS = os.path.join(ROOT, "static", "app", "80-inserts.js")
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")

# То, что жило ТОЛЬКО ради ссылки: имя — в разметке, коде интерфейса или CSS.
GONE = ("spk_camstyle", "spkCamStyleGo", "spkStyleLinkUI", "spkStyleSess",
        "SPKSTYLESESS", "spkStyleSnap", "stGroupOfField")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _fn(src, marker):
    """Тело функции от `marker` до парной закрывающей скобки (как в соседних тестах).

    Регэкспом до `\\n}` не обойтись: у коротких дверей вида
    `function closeModal(id){…else _closeModal(id);}` закрывающая скобка стоит в
    той же строке, и тело обрезалось бы вместе с проверяемой веткой.
    """
    assert marker in src, "не нашёл в исходнике: %s" % marker
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("не закрылась скобка у %s" % marker)


def test_link_markup_is_gone():
    """Ни ссылки, ни её «!», ни хоста панели в редакторе спикера, ни обработчика на поле стиля.

    Обработчик `onchange` на `#spk_style` — вторая половина ссылки: он гасил её,
    когда у спикера не выбран стиль. Удалили бы только кнопку — поле стиля осталось
    бы звать `spkStyleLinkUI`, и смена стиля у спикера падала бы ReferenceError'ом.
    """
    html = _read(HTML)
    assert 'id="spk_camstyle"' not in html, "ссылка «Кадр и зум камеры — в стиле героя» вернулась"
    assert 'id="spk_camstyle_hint"' not in html, "«!» убранной ссылки остался в разметке"
    assert 'id="spkstyle"' not in html, "хост панели стиля в редакторе спикера вернулся"
    assert 'id="spk_style"' in html, "поле «Стиль AE» у спикера пропало — убирали только ссылку"
    sel = re.search(r'<select id="spk_style"[^>]*>', html)
    assert sel, "селектор «Стиль AE» не нашёлся в разметке"
    assert "onchange" not in sel.group(0), \
        "на поле «Стиль AE» остался обработчик убранной ссылки: %r" % sel.group(0)


def test_no_link_code_is_left_in_the_interface():
    """В коде интерфейса не осталось ни одного имени из обвязки ссылки."""
    bad = []
    for path in app_meta.app_js_files() + [CSS]:
        text = _read(path)
        for name in GONE:
            for n, line in enumerate(text.splitlines(), 1):
                if name in line:
                    bad.append("%s:%d: %s" % (os.path.basename(path), n, line.strip()[:110]))
    assert not bad, ("обвязка убранной ссылки осталась в коде:\n" + "\n".join(bad))


def test_style_panel_has_one_host_again():
    """У блока `#stylebox` снова один хозяин — вкладка «Стиль» предпросмотра.

    `styleToModal` без параметра и без `#spkstyle` в `styleHome`: вернув второй
    хозяин, легко снова получить панель, запертую в закрытой модалке (или два
    разных ответа на вопрос «где сейчас поля стиля»).
    """
    src = _read(INSERTS_JS)
    assert "function styleToModal(hostId)" not in src, \
        "у styleToModal вернулся параметр-хозяин: вторым хозяином был редактор спикера"
    m = re.search(r"function styleToModal\(\)\{(.*?)\}", src, re.S)
    assert m, "в 80-inserts.js не нашлась функция styleToModal()"
    body = m.group(1)
    assert "'aewstyle'" in body, "styleToModal больше не перевозит блок во вкладку предпросмотра"
    home = re.search(r"function styleHome\(\)\{(.*?)\}", src, re.S)
    assert home, "в 80-inserts.js не нашлась функция styleHome()"
    assert "spkstyle" not in home.group(1), "styleHome снова прячет хост редактора спикера"
    # Дверь переезда осталась ровно у вкладки предпросмотра — и в разметке, и в коде
    html = _read(HTML)
    assert 'id="aewstyle"' in html and 'id="styleslot"' in html, \
        "пропал слот блока стиля или вкладка «Стиль» предпросмотра"


def test_session_guard_is_gone_from_capture_and_close():
    """Сеанса правки стиля нет ни в `captureAE`, ни в закрытии модалок.

    Пока сеанс был, `captureAE` не писал стиль панели в задание клипа, а
    `closeModal('mbSpeaker')` спрашивал про несохранённые правки шаблона. Без
    ссылки сеанс открыть нечем — обе ветки обязаны уйти, иначе в закрытии модалки
    остаётся мёртвая дверь, которая ждёт события, которого не будет.
    """
    ae = _read(AE_JS)
    body = _fn(ae, "function captureAE(")
    assert "spkStyleSess" not in body, "captureAE снова смотрит на сеанс правки стиля"
    assert "j.styleKey=" in body, \
        "captureAE больше не пишет стиль в задание клипа — это была работа сеанса, а не общая"
    door = _fn(_read(CHROME_JS), "function closeModal(")
    assert "spkStyleSess" not in door, \
        "закрытие модалки снова спрашивает про сеанс правки стиля из редактора спикера"


def test_panel_keeps_the_general_pointer_mechanism():
    """Указка панели на группу (`stFocusGroup`) осталась — её зовёт сам `renderStylePanel`.

    Общий механизм трогать не следовало: `stGroupOfField` был поиском группы под
    ссылку, а показ группы с подсветкой — умение самой панели (схема приезжает
    запросом, и указку доводит `renderStylePanel`).
    """
    src = _read(PANEL_JS)
    assert "function stFocusGroup(" in src, "показ группы в панели стиля пропал вместе со ссылкой"
    assert "stFocusGroup(ST_FOCUS_WANT)" in src, \
        "renderStylePanel больше не доводит указку — механизм остался без вызова"
    css = _read(CSS)
    assert ".st-focus" in css, "пропала подсветка строки, которую просили показать"
