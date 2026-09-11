# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""ВСЕ нулы главного компа собираются в шапку, сразу под субтитрами.

Баг, ради которого тест: подъём нулов был написан поимённым списком (камеры →
«вставки кам1» → «вставки кам2» → «интро»), и каждый заведённый позже нул в него
забывали дописать. «Вставки кам1 на кам2» и «интро на кам2» так и оставались
закопаны между клипами камер — найти их в таймлайне можно было только прокруткой.
Теперь после явного порядка идёт добор ЛЮБОГО оставшегося нула композиции.

Проверяем и обратную сторону — сверку: `_kind` обязан узнавать нул по флагу
`isNull` из дампа AE, а не по списку имён, иначе ярус «нул» отстанет так же.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import verify_ae  # noqa: E402
from core import xml2ae  # noqa: E402

SRC = xml2ae.AE_FULL


def test_явный_порядок_перечисляет_все_нулы_сборки():
    assert "nullOrder.push(insNull1, insNull2, insNull1b, introNull, introNull2);" in SRC


def test_хвостом_добираются_нулы_вне_списка():
    """Тот самый предохранитель: новый нул попадёт в шапку сам."""
    assert "isNull=!!qL.nullLayer;" in SRC
    assert "if(!known) nullOrder.push(qL);" in SRC


def test_блок_нулов_встаёт_под_субтитрами():
    assert "var nullAnchor=subLayer;" in SRC
    assert "qN.moveAfter(nullAnchor); nullAnchor=qN;" in SRC


def test_дамп_ае_отдаёт_флаг_нула():
    jsx = open(os.path.join(ROOT, "tools", "ae_inspect.jsx"), encoding="utf-8").read()
    assert "o.isNull=L.nullLayer;" in jsx


def test_сверка_узнаёт_нул_по_флагу_а_не_по_имени():
    assert verify_ae._kind({"name": "мой нул", "isNull": True}) == "нул"
    assert verify_ae._kind({"name": "интро на кам2"}) == "нул"      # запасной путь: старый дамп


def test_ярус_нулов_обязан_лежать_выше_кадров():
    assert "нул" in verify_ae.LAYER_ORDER
    for lo in ("переходы", "рото", "вставки/интро", "камеры"):
        assert ("нул", lo) in verify_ae.ORDER_PAIRS
    assert ("субтитры", "нул") in verify_ae.ORDER_PAIRS      # субтитры всё же выше
