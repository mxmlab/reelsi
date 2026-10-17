# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Парная ручка панели стиля, когда пары нет в стиле: поле показывает BASE пары.

Симптом: в стиле владельца нет ни `cam1_take_after_cut`, ни `cam2_take_after_cut` (их берут
из BASE: 1.0 и 2.0). Поля «Пауза после склейки» Камеры 2 и других парных ручек были пустыми,
хотя сборка берёт значение пары: `plan_style._pair_sv` падает на BASE пары.

Причина: `stView` брал значение пары только из стиля (`CURSTYLE[fallback_key]`). Без него
поле откатывалось к BASE самой ручки, а у ключей Камеры 2 в BASE стоит None — поле пустое.

Фикс — в `stView` значение пары берётся из стиля, а если его там нет — из BASE пары.
`stStore` и `ST_FB_SHOWN` не менялись: поле, в котором стоит ровно показанное, ключ не заводит.

Тест гоняет БОЕВОЙ `static/app/94-stylepanel.js` под node (как `test_intro_back_after.py`),
а не свою копию `stView`.
"""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests.test_style_panel_js import _run_node  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт панели требует node в PATH")


# Пара (cam1) есть только в BASE: ни ключа ручки, ни ключа пары в стиле нет.
NO_PAIR_IN_STYLE = r"""
STSCHEMA = { base: { cam1_take_after_cut: 1.0, cam2_take_after_cut: null }, layers: [] };
CURSTYLE = {};
const f = { key: 'cam2_take_after_cut', ctl: 'num', nullable: true,
            fallback_key: 'cam1_take_after_cut' };
const shown = stView(f, CURSTYLE[f.key]);
const sameAsPair = stStore(f, '1');
const edited = stStore(f, '1.5');
console.log(JSON.stringify({ shown: shown, same: sameAsPair, edited: edited }));
"""

# Пара в стиле задана — показываем её значение, как и раньше.
PAIR_IN_STYLE = r"""
STSCHEMA = { base: { cam1_take_after_cut: 1.0, cam2_take_after_cut: null }, layers: [] };
CURSTYLE = { cam1_take_after_cut: 0.4 };
const f = { key: 'cam2_take_after_cut', ctl: 'num', nullable: true,
            fallback_key: 'cam1_take_after_cut' };
const shown = stView(f, CURSTYLE[f.key]);
console.log(JSON.stringify({ shown: shown }));
"""

# conv-ручка (проценты): пары нет в стиле — показываем BASE пары в процентах.
CONV_NO_PAIR = r"""
STSCHEMA = { base: { insert_c2_y: 0.773, insert_c2on1_y: null }, layers: [] };
CURSTYLE = {};
const f = { key: 'insert_c2on1_y', conv: 'frac_pct', fallback_key: 'insert_c2_y',
            nullable: true };
const shown = stView(f, CURSTYLE[f.key]);
const same = stStore(f, '77.3');
console.log(JSON.stringify({ shown: shown, same: same }));
"""


@node
def test_pair_base_shown_and_key_not_created(tmp_path):
    """1. Нет ни ключа ручки, ни ключа пары: поле показывает BASE пары (1); ровно это
    значение ключ не заводит (null), другое — заводит (1.5)."""
    res = _run_node(tmp_path, "pair_base_none.js", NO_PAIR_IN_STYLE)
    assert res["shown"] == 1, f"поле не показало BASE пары: {res['shown']!r}"
    assert res["same"] is None, "показанное значение пары завело ключ, которого в стиле не было"
    assert res["edited"] == 1.5, f"правка поля не доехала до стиля: {res['edited']!r}"


@node
def test_pair_in_style_still_wins(tmp_path):
    """2. Пара в стиле есть: поле показывает её значение (0.4), как и раньше."""
    res = _run_node(tmp_path, "pair_in_style.js", PAIR_IN_STYLE)
    assert res["shown"] == 0.4, f"значение пары из стиля перебито BASE: {res['shown']!r}"


@node
def test_conv_pair_base_shown_in_percent(tmp_path):
    """3. conv-ручка без пары в стиле: BASE пары 0.773 показан как 77.3 %, а ровно это
    значение ключ не заводит."""
    res = _run_node(tmp_path, "conv_pair_base.js", CONV_NO_PAIR)
    assert res["shown"] == 77.3, f"conv-поле не показало BASE пары в процентах: {res['shown']!r}"
    assert res["same"] is None, "показанный процент завёл ключ, которого в стиле не было"
