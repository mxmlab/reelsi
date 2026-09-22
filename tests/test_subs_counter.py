# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Счётчик цифр на словах субтитров в After Effects.

  * golden: hl_count пуст — .jsx побайтово прежний;
  * слово "1500" в hl_count — в .jsx у него Slider Control 0 -> 1500 и выражение с Math.round;
  * слово "12,5" — toFixed(1) с заменой точки на запятую;
  * слово "привет" в hl_count — никакого Slider Control (не число);
  * hl_count переиндексируется при удалении слов интро;
  * все десять дверей содержат hl_count рядом с hl_breaks;
  * сборка .jsx со счётчиком проходит проверку синтаксиса через node.
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


def _build(xml, tmp_path, hl_count=None, intro=None, intro_remove=None, name="out.jsx", **kw):
    path = str(tmp_path / name)
    xml2ae.to_ae_full(xml, jsx_path=path, hl_count=hl_count, intro=intro or [],
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


def test_golden_empty_hl_count_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Golden: при пустом hl_count или None .jsx остаётся побайтово прежним."""
    jsx_none, _ = _build(xml_subs, tmp_path, hl_count=None, name="out_none.jsx")
    jsx_empty, _ = _build(xml_subs, tmp_path, hl_count=[], name="out_empty.jsx")

    assert jsx_none == jsx_empty
    assert "ADBE Slider Control" not in jsx_none
    assert "Slider Control" not in jsx_none
    assert 'effect("Slider Control")' not in jsx_none
    # Форма каждого элемента SUBS ровно 6 элементов [start, end, text, hl, row, gend]
    subs = _subs_data(jsx_none)
    assert subs and len(subs[0]) == 6


def test_word_1500_slider_and_math_round(xml_subs, tmp_path):
    """Слово '1500' в hl_count: Slider Control 0 -> 1500 и выражение с Math.round."""
    _set_word(xml_subs, 0, "1500")
    jsx, _ = _build(xml_subs, tmp_path, hl_count=[0], name="out_1500.jsx")

    assert '"ADBE Slider Control"' in jsx
    assert '"ADBE Slider Control-0001"' in jsx
    assert "slP.setValueAtTime(t0, 0);" in jsx
    assert "slP.setValueAtTime(t0 + HL_DUR, cnt[0]);" in jsx
    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx

    subs = _subs_data(jsx)
    assert subs is not None
    # У первого слова есть 7-й элемент со счётчиком [1500, Math.round(...)]
    assert len(subs[0]) == 7
    assert subs[0][6] == [1500, 'Math.round(effect("Slider Control")("Slider"))']
    # У других слов 7-й элемент null
    assert subs[1][6] is None


def test_word_decimal_comma_tofixed(xml_subs, tmp_path):
    """Слово '12,5' в hl_count: toFixed(1) с заменой точки на запятую."""
    _set_word(xml_subs, 0, "12,5")
    jsx, _ = _build(xml_subs, tmp_path, hl_count=[0], name="out_comma.jsx")

    assert 'toFixed(1).replace(\\".\\", \\",\\")' in jsx
    subs = _subs_data(jsx)
    assert subs is not None
    assert subs[0][6] == [12.5, '(effect("Slider Control")("Slider")).toFixed(1).replace(".", ",")']


def test_word_non_number_ignored(xml_subs, tmp_path):
    """Слово 'привет' в hl_count: не число -> никакого Slider Control."""
    _set_word(xml_subs, 0, "привет")
    jsx, _ = _build(xml_subs, tmp_path, hl_count=[0], name="out_non_number.jsx")

    assert "ADBE Slider Control" not in jsx
    assert "Slider Control" not in jsx
    subs = _subs_data(jsx)
    assert subs is not None
    assert len(subs[0]) == 6


def test_hl_count_remap_on_intro_remove(xml_subs, tmp_path):
    """hl_count переиндексируется при удалении слов интро."""
    _set_word(xml_subs, 0, "ИНТРО1")
    _set_word(xml_subs, 1, "ИНТРО2")
    _set_word(xml_subs, 2, "1500")

    # Удаляем первые 2 слова через intro_remove. Слово с исходным индексом 2 становится индексом 0.
    intro = [dict(words=["ИНТРО1", "ИНТРО2"], color="white", times=[1.0])]
    jsx, _ = _build(xml_subs, tmp_path, hl_count=[2], intro=intro, intro_remove=[0, 1], name="out_remap.jsx")

    subs = _subs_data(jsx)
    assert subs is not None
    assert subs[0][2] == "1500"
    assert subs[0][6] == [1500, 'Math.round(effect("Slider Control")("Slider"))']

    # Если в hl_count указано слово, ушедшее в интро (индекс 0), оно не попадает в субтитры
    jsx_removed, _ = _build(xml_subs, tmp_path, hl_count=[0], intro=intro, intro_remove=[0, 1], name="out_del.jsx")
    subs_del = _subs_data(jsx_removed)
    assert subs_del is not None
    assert len(subs_del[0]) == 6
    assert "ADBE Slider Control" not in jsx_removed


def test_every_door_contains_hl_count():
    """Все двери содержат поле hl_count рядом с hl_breaks.

    Дверей было десять: панель слов предпросмотра нарезки (pvwSaveYellow) удалена
    вместе со своими контейнерами, её дверь из списка ушла — остальные девять
    проверяются как раньше.
    """
    ae_js = open(os.path.join(ROOT, "static", "app", "90-ae.js"), encoding="utf-8").read()
    ins_js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), encoding="utf-8").read()
    api_build = open(os.path.join(ROOT, "api", "build.py"), encoding="utf-8").read()
    xml_build = open(os.path.join(ROOT, "core", "xml2ae", "build.py"), encoding="utf-8").read()

    # 1. 90-ae.js: defJob
    assert re.search(r"highlights:\s*\[\],\s*hl_breaks:\s*\[\],\s*hl_count:\s*\[\]", ae_js)
    # 2. 90-ae.js: clearHl
    assert re.search(r"hl_breaks=\[\];[^\n\r]*hl_count=\[\];", ae_js)
    # 3. 90-ae.js: selectAE
    assert re.search(r"BRK=new Set\([^\)]*\);[^\n\r]*CNT=new Set\([^\)]*hl_count[^\)]*\);", ae_js)
    # 4. 90-ae.js: captureAE
    assert re.search(r"j\.hl_breaks=[^\n\r]*;[^\n\r]*j\.hl_count=[^\n\r]*CNT[^\n\r]*;", ae_js)
    # 5. 90-ae.js: jobForBuild
    assert re.search(r"hl_breaks:j\.hl_breaks\|\|\[\],\s*hl_count:j\.hl_count\|\|\[\]", ae_js)
    # 6. 90-ae.js: tojsx
    assert re.search(r"hl_breaks:\(HLXML===xml\)\?\[\.\.\.BRK\]:\[\],\s*hl_count:\(HLXML===xml\)\?\[\.\.\.CNT\]:\[\]", ae_js)
    # 7. 85-inserts-view.js: ipvPlanBody
    assert re.search(r"hl_breaks:\(HLXML===xml\)\?\[\.\.\.BRK\]:\[\],[^\n\r]*\r?\n\s*hl_count:\(HLXML===xml\)\?\[\.\.\.CNT\]:\[\]", ins_js)
    # 8. api/build.py: _norm_build_jobs
    assert re.search(r"hl_breaks=j\.get\(\"hl_breaks\"\)[^\n\r]*,\r?\n\s*hl_count=j\.get\(\"hl_count\"\)", api_build)
    # 9. xml2ae/build.py: scene_plan & parse
    assert re.search(r"hl_breaks=None,\s*hl_count=None", xml_build)
    assert re.search(r"brk_raw\s*=.*?hl_breaks.*?\n\s*cnt_raw\s*=.*?hl_count", xml_build)


@node
def test_subs_counter_syntax_node(xml_subs, tmp_path):
    """Сборка .jsx со счётчиком проходит проверку синтаксиса через node."""
    _set_word(xml_subs, 0, "1500")
    _set_word(xml_subs, 1, "12,5")
    jsx, jsx_path = _build(xml_subs, tmp_path, hl_count=[0, 1], name="out_syntax.jsx")

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


def test_chip_cnt_css_class_and_no_inline_style():
    """Кнопка счётчика создаётся общей функцией, а класс .chip-cnt описан в app.css.

    Панель слов предпросмотра нарезки (pvwRender) удалена — чипы рисует одна общая
    функция wordChipEl, у неё и проверяем отсутствие inline-стиля.
    """
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()

    chip = _func(pvw_js, "wordChipEl")
    assert "cntBtn.style" not in chip, "стиль кнопки счётчика снова правится инлайном"

    assert ".chip-cnt" in css, "класс .chip-cnt не найден в app.css"
    assert ".chip-cnt.on" in css, "состояние .chip-cnt.on не найдено в app.css"


def test_chip_cnt_created_by_single_common_function():
    """.chip-cnt создаётся общей функцией, а не двумя разными."""
    ins_js = open(os.path.join(ROOT, "static", "app", "80-inserts.js"), encoding="utf-8").read()
    pvw_js = open(os.path.join(ROOT, "static", "app", "60-preview.js"), encoding="utf-8").read()

    # В 80-inserts.js нет своей отдельной копии создания .chip-cnt
    assert "chip-cnt" not in ins_js

    # В 60-preview.js создание кнопки .chip-cnt происходит ровно в одном месте — wordChipEl
    chip_cnt_creations = re.findall(r"className\s*=\s*['\"]chip-cnt['\"]", pvw_js)
    assert len(chip_cnt_creations) == 1, f"Ожидалось ровно одно создание .chip-cnt, найдено: {len(chip_cnt_creations)}"

    # Кнопка создаётся внутри общей функции wordChipEl
    assert "chip-cnt" in _func(pvw_js, "wordChipEl")


def test_is_number_word_single_instance():
    """isNumberWord существует в единственном экземпляре во всех static/app/*.js."""
    app_dir = os.path.join(ROOT, "static", "app")
    count = 0
    found_in = []
    for fname in sorted(os.listdir(app_dir)):
        if fname.endswith(".js"):
            fpath = os.path.join(app_dir, fname)
            content = open(fpath, encoding="utf-8").read()
            defs = re.findall(r"\bfunction\s+isNumberWord\b", content)
            if defs:
                count += len(defs)
                found_in.append(fname)
    assert count == 1, f"isNumberWord найдена {count} раз в файлах: {found_in}"
    assert found_in == ["60-preview.js"]


