# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож: содержимое не распирает колонки — `.grow` сжимается, длинный текст режется.

Дефект (скриншот владельца, ⚙ «Настройки» → «Подключения»): правая колонка с формой
профиля была шире окна — горизонтальная прокрутка внизу, обрезаны кнопки «Обновить
список»/«Удалить» и правый край полей; в списке профилей имя «Unsloth Studio /
Qwen-Image» обрезано краем, без многоточия.

Причина: `.grow{flex:1}` без `min-width:0`. Авто-минимум flex-элемента равен ширине
содержимого, поэтому колонку распирали поле модели с кнопкой, выпадающие списки и ряд
кнопок `.actbar` без переноса. Список профилей — flex-контейнер: `text-overflow` на нём
не работает, текст надо оборачивать в блочный span.

Сторожим:

1. `static/app.css` — у `.grow` есть `min-width:0`; у `.aisItem` — `text-overflow:ellipsis`,
   у `.aisItem .nm` — обрезка одной строкой; ряд кнопок переносится, строка «Модель»
   (`.conn-ctl`) сжимает поле и не рвёт кнопку; `#ais_reas` переносится; у ползунков
   и `#edcut` есть нижняя граница (иначе `min-width:0` прячет их целиком);
2. `templates/index.html` — строка «Модель» и колонка настроек размечены этими классами;
3. `static/app/10-settings.js` — `aiSetList` кладёт имя в `.aisItem .nm`, а полное имя
   дублирует в `title` пункта.

Мутация: убрать `min-width:0` из правила `.grow` — падает `test_grow_has_min_width_zero`.

Запуск: py -3.10 -m pytest tests/test_grow_overflow.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CSS = os.path.join(ROOT, "static", "app.css")
HTML = os.path.join(ROOT, "templates", "index.html")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _rule(css: str, selector: str) -> str:
    """Тело правила по селектору без пробелов (как в соседних CSS-тестах)."""
    m = re.search(re.escape(selector) + r"\{([^}]+)\}", css)
    assert m, "в app.css нет правила %s" % selector
    return m.group(1).replace(" ", "")


def _extract(src: str, marker: str) -> str:
    """Кусок исходника от marker до парной закрывающей скобки."""
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


def _run_node(tmp_path: Any, name: str, script: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON (последняя строка stdout)."""
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, "node: %s" % (proc.stderr or proc.stdout)[:600]
    lines = [x.strip() for x in proc.stdout.strip().splitlines() if x.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


# ---- 1. CSS: сжатие, перенос, многоточие -----------------------------------

def test_grow_has_min_width_zero() -> None:
    """У `.grow` есть `min-width:0`: без него flex-элемент не сжимается меньше своего
    содержимого и распирает родителя вбок вместе с горизонтальной прокруткой.

    Мутация «убрать min-width:0 из .grow» валит этот тест.
    """
    rule = _rule(_read(CSS), ".grow")
    assert "flex:1" in rule, "у .grow пропал flex:1: %s" % rule
    assert "min-width:0" in rule, \
        "flex-элемент снова не может сжаться (контент распирает колонку): %s" % rule


def test_settings_window_clips_and_wraps() -> None:
    """Окно настроек: пункт списка профилей режется многоточием, ряд кнопок переносится,
    строка «Модель» сжимает поле и не рвёт кнопку, «уровни ума» переносятся."""
    css = _read(CSS)

    item = _rule(css, ".aisItem")
    for frag in ("text-overflow:ellipsis", "overflow:hidden", "white-space:nowrap"):
        assert frag in item, "в .aisItem нет %s: %s" % (frag, item)

    name = _rule(css, ".aisItem .nm")
    for frag in ("min-width:0", "overflow:hidden", "text-overflow:ellipsis",
                 "white-space:nowrap"):
        assert frag in name, "имя профиля в .aisItem .nm не режется многоточием: %s" % name

    foot = _rule(css, ".conn-foot")
    assert "flex-wrap:wrap" in foot, \
        "ряд кнопок профиля снова выдавливает колонку за край окна: %s" % foot

    ctl = _rule(css, ".conn-ctl")
    assert "flex-wrap:wrap" in ctl, "строка «Модель» не переносится: %s" % ctl
    assert "min-width:0" in ctl, "строка «Модель» снова распирает колонку: %s" % ctl
    assert "min-width:130px" in _rule(css, ".conn-ctl>input,.conn-ctl>select"), \
        "поле модели не сжимается: %s" % _rule(css, ".conn-ctl>input,.conn-ctl>select")
    btn = _rule(css, ".conn-ctl>button")
    assert "flex:none" in btn and "white-space:nowrap" in btn, \
        "кнопка «Обновить список» рвётся или уезжает за край: %s" % btn

    reas = _rule(css, "#ais_reas")
    assert "white-space:normal" in reas, \
        "строка «уровни ума: … контекст … вывод …» не переносится: %s" % reas

    assert ".aislayout{flex-direction:column}" in css, \
        "на узком окне колонка вкладок не ложится сверху строкой"
    assert ".conn{flex-direction:column}" in css, \
        "на узком окне список профилей и форма не встают друг под друга"

    # Ноль — не везде выход: ползунок и строка-итог с нулевым минимумом исчезают целиком.
    rng = _rule(css, "input[type=range].grow")
    assert "min-width:120px" in rng, "ползунок перемотки может сжаться в невидимую точку: %s" % rng
    cut = _rule(css, "#edcut")
    assert "min-width" in cut, "строка-итог монтажа может сжаться в пустоту: %s" % cut


def test_settings_markup_uses_overflow_classes() -> None:
    """Разметка окна настроек: колонка формы — `.grow`, строка «Модель» — `.conn-ctl`
    со сжимаемым полем, ряд кнопок профиля — `.conn-foot`."""
    html = _read(HTML)

    assert 'class="conn-ctl"' in html, "строка «Модель» не размечена .conn-ctl"
    assert re.search(r'<input id="ais_model"[^>]*style="flex:1"', html) is None, \
        "у поля модели остался инлайн flex:1 вместо правил .conn-ctl"
    assert re.search(r'<button class="sm" onclick="aiSetModels\(\)"', html), \
        "кнопка «Обновить список» пропала из строки модели"

    col = re.search(r'<div class="grow" style="display:flex;flex-direction:column', html)
    assert col, "правая колонка настроек больше не .grow"
    assert re.search(r'<div class="conn-foot">\s*<span id="connDirty"', html), \
        "подвала формы нет: кнопки «Отмена»/«Сохранить» остались без своего ряда"
    assert '<div id="ais_reas" class="muted"' in html, "строка «уровни ума» пропала"


# ---- 2. aiSetList: имя в .nm и полное имя в title --------------------------

@node
def test_profile_items_carry_full_name_in_title(tmp_path: Any) -> None:
    """Реальный `aiSetList`: имя каждого профиля лежит в `.aisItem .nm` (там работает
    многоточие), полное имя продублировано в `title` пункта, кавычки и скобки
    экранированы в обоих местах."""
    script = """
const fs=require('fs');
let AISNAMES=[];
let AIEDIT='Unsloth Studio / Qwen-Image';
const AICFG={active:'Claude <Sonnet>',profiles:{
  'Claude <Sonnet>':{},'Unsloth Studio / Qwen-Image':{},'LM Studio "local"':{}}};
const EL={};
function $(id){return EL[id]||(EL[id]={innerHTML:''});}
function t(s){return s;}
__ESC__
__LIST__
aiSetList();
fs.writeSync(1, JSON.stringify({html:$('aisList').innerHTML})+'\\n');
process.exit(0);
"""
    script = (script
              .replace("__ESC__", _extract(_read(CORE_JS), "function esc("))
              .replace("__LIST__", _extract(_read(SETTINGS_JS), "function aiSetList(")))
    html = _run_node(tmp_path, "ais_list.js", script)["html"]

    # Пункты разбираем по кускам, а не одним регэкпом: «>» в имени (esc его не трогает)
    # оборвал бы разбор атрибутов на середине.
    chunks = html.split('<div class="aisItem')[1:]
    assert len(chunks) == 4, "пунктов списка профилей не четыре: %r" % html

    items = []
    titles = {}
    for ch in chunks:
        cls = ch[:ch.index('"')]
        m = re.search(r'title="([^"]*)"', ch)
        name = re.search(r'<span class="nm">([^<]*)</span>', ch)
        assert name, "имя профиля не в .aisItem .nm (многоточие не сработает): %r" % ch
        items.append((cls, name.group(1)))
        titles[name.group(1)] = m.group(1) if m else None

    # 1. Имя профиля целиком — и в span, и в title
    assert titles.get("Unsloth Studio / Qwen-Image") == "Unsloth Studio / Qwen-Image", \
        "у длинного имени нет полного title: %r" % titles
    # 2. Активный профиль по-прежнему помечен точкой, редактируемый — классом .on
    assert items[0][0] == "", "точка активности уехала не к тому пункту: %r" % items[0][0]
    assert '<span class="dot">●</span>' in chunks[0], "активный профиль потерял точку"
    assert items[1][0] == " on", "редактируемый профиль потерял класс .on: %r" % items[1][0]
    # 3. Кавычки и угловые скобки не рвут атрибут и текст (esc экранирует «<» и «"»)
    assert titles.get("Claude &lt;Sonnet>") == "Claude &lt;Sonnet>", \
        "имя со скобками не экранировано в title: %r" % titles
    assert titles.get("LM Studio &quot;local&quot;") == "LM Studio &quot;local&quot;", \
        "имя с кавычками не экранировано в title: %r" % titles
    # 4. Служебный пункт «＋ Новый профиль» тоже со span (обрезается так же)
    assert "＋ Новый профиль" in titles, "пункт «Новый профиль» потерял .nm: %r" % titles
    # 5. Имя в списке не обрезано в разметке — режет его CSS
    assert "Unsloth Studio / Qwen-Image</span>" in html, \
        "имя обрезано ещё в разметке — многоточие должно ставить CSS"


def test_ai_set_list_source_keeps_title_and_nm() -> None:
    """Тот же сторож без node: в исходнике `aiSetList` видны `title` пункта и `.nm`."""
    src = _extract(_read(SETTINGS_JS), "function aiSetList(")
    assert 'title="' in src, "полное имя профиля не попадает в title пункта"
    assert '<span class="nm">' in src, "имя профиля не обёрнуто в .nm (многоточие не сработает)"
