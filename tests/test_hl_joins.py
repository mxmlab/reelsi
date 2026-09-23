# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Склейка слов субтитров в строку (hl_joins) в After Effects.

  * golden: hl_joins пуст / None — .jsx побайтово прежний;
  * _stack_layout с joins: 3 слова, join между 0 и 1 -> rows [0, 0, 1];
  * joins и breaks взаимоисключающие: если индекс в обоих, break побеждает;
  * в .jsx при highlights=[0, 1] и hl_joins=[0] оба слова имеют row=0,
    разные X (curX + r_widths[wi] / 2), и центрирование (W - totW) / 2;
  * одиночные слова (не склеенные) центрируются по старому (W / 2);
  * hl_joins переиндексируется при удалении слов интро;
  * все десять дверей содержат hl_joins рядом с hl_breaks / hl_count;
  * сборка .jsx с hl_joins проходит проверку синтаксиса через node.
"""
import gzip
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import _stack_layout  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _set_word(xml_path, idx, new_text):
    tree = ET.parse(xml_path)
    cur = 0
    for c in tree.findall(".//clipitem"):
        eff = c.find(".//filter/effect")
        if eff is not None and eff.find("effectid") is not None and eff.find("effectid").text == "GraphicAndType":
            nm = eff.find("name")
            if nm is not None and nm.text:
                if cur == idx:
                    nm.text = new_text
                    break
                cur += 1
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def _build(xml, tmp_path, highlights=None, hl_breaks=None, hl_count=None, hl_joins=None,
           intro=None, intro_remove=None, name="out.jsx", **kw):
    path = str(tmp_path / name)
    xml2ae.to_ae_full(xml, jsx_path=path, highlights=highlights or [], hl_breaks=hl_breaks or [],
                      hl_count=hl_count or [], hl_joins=hl_joins, intro=intro or [],
                      intro_remove=intro_remove or [], intro_splits=[], disclaimer="",
                      emit=lambda *a, **k: None, **kw)
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()
    return content, path


def _subs_data(jsx):
    m = re.search(r"var SUBS=(\[.*?\]);", jsx)
    return json.loads(m.group(1)) if m else None


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def test_golden_empty_hl_joins_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Golden: при пустом hl_joins или None .jsx остаётся побайтово прежним."""
    jsx_none, _ = _build(xml_subs, tmp_path, hl_joins=None, name="out_none.jsx")
    jsx_empty, _ = _build(xml_subs, tmp_path, hl_joins=[], name="out_empty.jsx")

    assert jsx_none == jsx_empty
    # При пустом hl_joins шаблон SUBS_LOOP_WORDS_JOINED не используется
    assert "r_widths" not in jsx_none
    assert "totW" not in jsx_none
    # Форма каждого элемента SUBS ровно 6 элементов [start, end, text, hl, row, gend]
    subs = _subs_data(jsx_none)
    assert subs and len(subs[0]) == 6


def test_stack_layout_joins():
    """_stack_layout с joins: 3 слова, join между 0 и 1 -> rows [0, 0, 1]."""
    subs = [[0.0, 1.0, "word0"], [1.0, 2.0, "word1"], [2.0, 3.0, "word2"]]
    hl = {0, 1, 2}
    joins = {0}
    rows, gend = _stack_layout(subs, hl, breaks=set(), joins=joins)
    assert rows == [0, 0, 1]
    assert gend[0] == gend[1] == 3.0


def test_joins_and_breaks_mutually_exclusive(xml_subs, tmp_path):
    """joins и breaks взаимоисключающие: если индекс в обоих, break побеждает."""
    subs = [[0.0, 1.0, "word0"], [1.0, 2.0, "word1"], [2.0, 3.0, "word2"]]
    hl = {0, 1, 2}
    # Индекс 1 и в breaks, и в joins: break побеждает -> слово 2 начинает новую стопку с row 0
    rows, gend = _stack_layout(subs, hl, breaks={1}, joins={1})
    assert rows == [0, 1, 0]
    assert gend[1] != gend[2]

    # Проверка через to_ae_full
    jsx, _ = _build(xml_subs, tmp_path, highlights=[0, 1, 2], hl_breaks=[1], hl_joins=[1], name="out_mut.jsx")
    subs_data = _subs_data(jsx)
    assert subs_data is not None
    assert subs_data[1][4] == 1
    assert subs_data[2][4] == 0


def test_jsx_horizontal_layout_joined_words(xml_subs, tmp_path):
    """При highlights=[0, 1] и hl_joins=[0] оба слова имеют row=0, одинаковый Y, разные X и центрирование."""
    jsx, _ = _build(xml_subs, tmp_path, highlights=[0, 1], hl_joins=[0], name="out_joined.jsx")
    subs = _subs_data(jsx)
    assert subs is not None
    # Оба слова на row 0
    assert subs[0][4] == 0
    assert subs[1][4] == 0

    # Шаблон со склейкой включился: расчёт общей ширины, центрирование и X для каждого слова
    assert "r_widths" in jsx
    assert "var totW = 0;" in jsx
    assert "var curX = (SW - totW) / 2;" in jsx
    assert "var wCenter = (r_layers.length > 1) ? (curX + r_widths[wi] / 2) : (SW / 2);" in jsx
    assert "var finalY = POSY + row*HL_STEP;" in jsx
    assert "[wCenter, finalY]" in jsx


def test_jsx_unjoined_words_centered(xml_subs, tmp_path):
    """Одиночные слова (когда в ряду одно слово) центрируются по SW / 2."""
    jsx, _ = _build(xml_subs, tmp_path, highlights=[0, 1, 2], hl_joins=[0], name="out_unjoined.jsx")
    subs = _subs_data(jsx)
    assert subs is not None
    # Слова 0 и 1 на ряду 0, слово 2 на ряду 1
    assert subs[0][4] == 0
    assert subs[1][4] == 0
    assert subs[2][4] == 1

    # В коде шаблона тернарный оператор для r_layers.length == 1 возвращает SW / 2
    assert "(r_layers.length > 1) ? (curX + r_widths[wi] / 2) : (SW / 2)" in jsx

    # При пустом hl_joins используется базовый шаблон с [SW/2, finalY]
    jsx_empty, _ = _build(xml_subs, tmp_path, highlights=[0, 1, 2], hl_joins=[], name="out_base.jsx")
    assert "[SW/2, finalY]" in jsx_empty


def test_hl_joins_remap_on_intro_remove(xml_subs, tmp_path):
    """hl_joins переиндексируется при удалении слов интро."""
    _set_word(xml_subs, 0, "ИНТРО1")
    _set_word(xml_subs, 1, "ИНТРО2")
    _set_word(xml_subs, 2, "СЛОВО1")
    _set_word(xml_subs, 3, "СЛОВО2")

    intro = [dict(words=["ИНТРО1", "ИНТРО2"], color="white", times=[1.0])]
    # Слова 0, 1 удаляются. Исходный индекс 2 становится 0, 3 становится 1.
    # Склеиваем слова 2 и 3 -> hl_joins=[2]. После ремапа это join [0].
    jsx, _ = _build(xml_subs, tmp_path, highlights=[2, 3], hl_joins=[2],
                    intro=intro, intro_remove=[0, 1], name="out_remap_join.jsx")

    subs = _subs_data(jsx)
    assert subs is not None
    assert subs[0][2] == "СЛОВО1"
    assert subs[1][2] == "СЛОВО2"
    assert subs[0][4] == 0
    assert subs[1][4] == 0
    assert "r_widths" in jsx


def test_every_door_contains_hl_joins():
    """Все двери содержат поле hl_joins рядом с hl_breaks / hl_count.

    Дверей было десять: панель слов предпросмотра нарезки (pvwSaveYellow) удалена
    вместе со своими контейнерами, её дверь из списка ушла — остальные девять
    проверяются как раньше.
    """
    ae_js = open(os.path.join(ROOT, "static", "app", "90-ae.js"), encoding="utf-8").read()
    ins_js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), encoding="utf-8").read()
    api_build = open(os.path.join(ROOT, "api", "build.py"), encoding="utf-8").read()
    xml_build = open(os.path.join(ROOT, "core", "xml2ae", "build.py"), encoding="utf-8").read()

    # 1. 90-ae.js: defJob
    assert re.search(r"hl_count:\s*\[\],\s*hl_joins:\s*\[\]", ae_js)
    # 2. 90-ae.js: clearHl
    assert re.search(r"c\.job\.hl_joins\s*=\s*\[\];[\s\S]*?JNS\s*=\s*new Set\(\);", ae_js)
    # 3. 90-ae.js: selectAE
    assert re.search(r"JNS\s*=\s*new Set\(j\.hl_joins\s*\|\|\s*\[\]\);", ae_js)
    # 4. 90-ae.js: captureAE
    assert re.search(r"j\.hl_joins\s*=\s*\(HLXML===c\.xml\)\s*\?\s*\[\.\.\.JNS\]\s*:\s*\[\];", ae_js)
    # 5. 90-ae.js: jobForBuild
    assert re.search(r"hl_joins:\s*j\.hl_joins\s*\|\|\s*\[\]", ae_js)
    # 6. 90-ae.js: tojsx
    assert re.search(r"hl_joins:\s*\(HLXML===xml\)\s*\?\s*\[\.\.\.JNS\]\s*:\s*\[\]", ae_js)
    # 7. 85-inserts-view.js: ipvPlanBody
    assert re.search(r"hl_joins:\s*\(HLXML===xml\)\s*\?\s*\[\.\.\.JNS\]\s*:\s*\[\]", ins_js)
    # 8. api/build.py: _norm_build_jobs
    assert re.search(r"hl_joins(?:\s*:[^=]+)?\s*=\s*j\.get\(\"hl_joins\"\)", api_build)
    # 9. xml2ae/build.py: scene_plan & to_ae_full
    assert re.search(r"hl_joins(?:\s*:[^=]+)?\s*=\s*None", xml_build)
    assert re.search(r"joins_raw\s*=", xml_build)


@node
def test_hl_joins_syntax_node(xml_subs, tmp_path):
    """Сборка .jsx с hl_joins проходит проверку синтаксиса через node."""
    _set_word(xml_subs, 0, "1 -")
    _set_word(xml_subs, 1, "2")
    jsx, jsx_path = _build(xml_subs, tmp_path, highlights=[0, 1], hl_joins=[0], name="out_syntax_joins.jsx")

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


def test_words_paint_join_no_dash_char():
    """При склейке (isJns) палочка исчезает: нет присваивания '—'.

    Панель слов предпросмотра нарезки (pvwPaint) удалена — отрисовка чипов и палочек
    осталась ОДНОЙ общей функцией wordsPaint, её и стережём: у склейки textContent
    пустой, у разрыва — «|».
    """
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()
    paint_code = _func(pvw_js, "wordsPaint")
    assert "el.textContent=''" in paint_code, "у склейки палочка не гасится"
    assert "el.textContent='|'" in paint_code, "у разрыва палочка не рисуется"
    assert "'—'" not in paint_code
    assert '"—"' not in paint_code


def test_aew_break_handler_checks_ctrl_meta():
    """В AE-превью обработчик палочки смотрит на ctrlKey/metaKey (склейка вместо разрыва)."""
    ins_js = open(os.path.join(ROOT, "static", "app", "80-inserts.js"), encoding="utf-8").read()
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()

    # В 80-inserts.js больше нет старого обработчика без ctrlKey
    assert "BRK.has(prev.i)?BRK.delete" not in ins_js

    # Отрисовка палочки использует общую функцию wordBreakEl
    assert "wordBreakEl" in ins_js

    # В общей функции wordBreakEl обработчики клика и клавиш проверяют ctrlKey и metaKey
    m = re.search(r"function wordBreakEl\([\s\S]*?\n\}", pvw_js)
    assert m, "wordBreakEl не найден в 60-preview.js"
    code = m.group(0)
    assert "ctrlKey" in code and "metaKey" in code


def test_single_chip_and_break_render_function():
    """Обе панели используют одну общую функцию отрисовки чипа и палочки.
    В 80-inserts.js больше нет своей копии создания .chip и .brk.
    """
    ins_js = open(os.path.join(ROOT, "static", "app", "80-inserts.js"), encoding="utf-8").read()
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()

    # В 80-inserts.js нет создания элементов .chip и .brk
    assert "className='chip'" not in ins_js and 'className="chip"' not in ins_js
    assert "className='brk'" not in ins_js and 'className="brk"' not in ins_js

    # Обе панели используют общие функции wordChipEl и wordBreakEl
    assert "wordChipEl(cfg" in ins_js or "wordChipEl(" in ins_js
    assert "wordBreakEl(cfg" in ins_js or "wordBreakEl(" in ins_js
    assert "wordChipEl(cfg" in pvw_js or "wordChipEl(" in pvw_js
    assert "wordBreakEl(cfg" in pvw_js or "wordBreakEl(" in pvw_js


