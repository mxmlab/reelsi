# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож приоритета драга интро над вставками в AE-превью (задание 3).

Слой вставок #ipvins лежит ВЫШЕ слоя интро #ipvintro (z-index из layer_order:
фото 7, видео 9, интро 6), а .ipvwrap принимает указатель. Поэтому нажатие над
строкой интро, закрытой вставкой, уходило в обработчик вставок и тащило вставку.
Теперь нажатие над .iline или ручкой масштаба пробивает стек (ipvIntroHitAt) и
уходит в общее тело драга интро — ipvIntroDragStart / ipvIntroScaleReset.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")


def _src():
    with open(JS, encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    return _balanced(src, src.index("{", m.end() - 1), name)


def _listener(src, selector, event):
    """Вырезать тело `$('<selector>').addEventListener('<event>',e=>{...})`."""
    m = re.search(
        r"\$\(['\"]%s['\"]\)\.addEventListener\(['\"]%s['\"],\s*e\s*=>\s*\{"
        % (re.escape(selector), re.escape(event)), src)
    assert m, f"не найден обработчик {event} у #{selector}"
    return _balanced(src, src.index("{", m.end() - 1), f"{event} #{selector}")


def _balanced(src, i, name):
    """Кусок исходника от `{` до парной `}` включительно."""
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def test_intro_helpers_exist_and_hit_test_pierces_stack():
    """ipvIntroDragStart / ipvIntroScaleReset / ipvIntroHitAt есть; хит-тест — по стеку."""
    src = _src()
    drag = _func(src, "ipvIntroDragStart")
    reset = _func(src, "ipvIntroScaleReset")
    hit = _func(src, "ipvIntroHitAt")

    # тело драга переехало целиком: движение группы и ручки масштаба на месте
    assert "ipvIntroPos" in drag and "captureAE" in drag and "ipvPlanSoon" in drag
    assert re.search(r"if\s*\(\s*handle\s*\)", drag), "ручка масштаба в драге не различается"
    assert "INTRO[h].gs=100" in reset.replace(" ", "")

    # хит-тест: пробивает стек и узнаёт строку и ручку внутри слоя интро
    assert "elementsFromPoint" in hit
    assert "'#ipvintro'" in hit
    assert "'.iline'" in hit
    assert "'.intro-scale-handle'" in hit


def test_ipvins_pointerdown_checks_intro_before_insert():
    """В #ipvins pointerdown ipvIntroHitAt стоит РАНЬШЕ поиска .ipvwrap."""
    body = _listener(_src(), "ipvins", "pointerdown")
    i_hit = body.find("ipvIntroHitAt")
    # именно вызов, а не упоминание в комментарии
    i_wrap = body.find("closest('.ipvwrap')")
    assert i_hit >= 0, "в обработчике вставок нет ipvIntroHitAt"
    assert i_wrap >= 0, "в обработчике вставок пропал поиск .ipvwrap"
    assert i_hit < i_wrap, "вставка перехватывает нажатие раньше, чем проверено интро"
    assert re.search(r"ipvIntroDragStart\(\s*e\s*,\s*ih\.handle\s*\)", body), \
        "попадание в интро должно уходить в ipvIntroDragStart"


def test_ipvins_dblclick_resets_intro_scale():
    """У #ipvins есть dblclick, который по ручке масштаба зовёт ipvIntroScaleReset."""
    body = _listener(_src(), "ipvins", "dblclick")
    assert "ipvIntroHitAt" in body
    assert re.search(r"ipvIntroScaleReset\(\s*e\s*\)", body)


def test_ipvintro_handlers_delegate_and_keep_no_second_body():
    """Обработчики #ipvintro зовут общие функции, а не держат вторую копию тела."""
    src = _src()
    pd = _listener(src, "ipvintro", "pointerdown")
    assert re.search(r"ipvIntroDragStart\(\s*e\s*,\s*handle\s*\)", pd)
    # ни расчёта масштаба, ни собственной подписки на движение — всё в ipvIntroDragStart
    assert "introHeadIdx" not in pd
    assert "pointermove" not in pd

    dc = _listener(src, "ipvintro", "dblclick")
    assert re.search(r"ipvIntroScaleReset\(\s*e\s*\)", dc)
    assert "introHeadIdx" not in dc
    assert "INTRO" not in dc
