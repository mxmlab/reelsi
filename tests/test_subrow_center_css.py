# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Строка субтитров шире кадра стоит по центру, а не уезжает вправо.

У строки превью `white-space:nowrap` и `text-align:center`, а коробка была ровно в ширину
кадра (`left:0;right:0`). Длинная склейка без переносов в неё не влезала, и CSS не центрирует
то, что не влезло: начало строки прибито к левому краю, хвост уходит вправо (на клипе
владельца «ПО ВАШИМ АНАЛИЗАМ» стояла правее центра на 8 % ширины кадра). Коробка вдвое шире
кадра с тем же центром — строка до двух ширин кадра встаёт по центру, как точечный текст в AE.
Замер в браузере: длинная строка — центр 217 px при центре кадра 132 до правки, 135 после.
"""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "static" / "app.css"


def test_subtitle_row_box_is_wider_than_the_frame_and_centered() -> None:
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"#ipvsub \.pvsubw\{([^}]*)\}", css)
    assert m, "правило строки субтитров превью не найдено"
    rule = m.group(1)
    assert "white-space:nowrap" in rule
    assert "left:-50%" in rule and "right:-50%" in rule, (
        "коробка строки снова в ширину кадра — длинная строка уедет вправо")
