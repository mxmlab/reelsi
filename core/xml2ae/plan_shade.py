# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Затемнение под интро: числа слоя-фигуры (остаток распила scene_plan).

`scene_plan` считал числа затемнения (ключ стиля `intro_shade`) одной формулой и клал
их в план — из плана их берут и шаблон (.jsx), и предпросмотр. Модуль забирает этот
расчёт целиком: сам словарь `plan["shade"]` (None при выключенной галке) и готовые числа
для JS слоя, который собирает `plan_intro_tpl.shade_js`.

Перенос ПОСТРОЧНЫЙ: числа и формулы не менялись ни на байт (проверяется эталоном
fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).

Вход — неизменяемый `ShadeInputs`: структура стиля, прочитанная один раз, и множитель
пиксельных констант кадра `_px` (его считает `scene_plan`: правило min(W, H)/1080 — одно
на все блоки). Выход — `ShadePlan.numbers` (None — галка снята).
"""
from dataclasses import dataclass
from typing import Any

from .jsutil import _r
from .layout import (SHADE_BLUR, SHADE_DY, SHADE_H, SHADE_OX, SHADE_OY, SHADE_SCALE,
                     SHADE_W, SHADE_X)
from .plan_style import StyleValues


@dataclass(frozen=True)
class ShadeInputs:
    """Вход затемнения под интро: стиль и множитель пиксельных констант кадра.

    `px` — `_px` из scene_plan (`layout._px_k`, min(W, H)/1080): им живут карточка
    вставки, её блюр и вылет, числа стиля и ЭТО затемнение. Кадр к моменту вызова уже
    окончательный (формат спикера главнее XML), поэтому второго правила нет.
    """
    # Стиль, прочитанный один раз (plan_style.read_style): галка intro_shade, её
    # непрозрачность, intro_y и общий масштаб интро (intro_scale_k).
    style: StyleValues
    px: float


# Числа затемнения — СЛОВАРЕМ, а не dataclass: тот же объект уезжает и в `plan["shade"]`
# (его читает предпросмотр), и в `_jd(...)` (им печатается INTRO_SHADE шаблона), а
# `_jd` — это `json.dumps`. Dataclass потребовал бы второй двери перевода в словарь,
# то есть второй копии состава полей; ключи словаря и есть контракт этих чисел.
ShadeNumbers = dict[str, Any]


@dataclass(frozen=True)
class ShadePlan:
    """Выход: числа затемнения для плана (`None` при выключенной галке).

    Выключенная галка — не «нет затемнения», а «ключа в плане нет»: превью слоя не
    заводит, подстановка шаблона пустая и .jsx прежний байт в байт (golden).
    """
    numbers: "ShadeNumbers | None"


def plan_shade(inp: ShadeInputs) -> ShadePlan:
    """Собрать числа затемнения под интро: чистая функция от `ShadeInputs`.

    Своих литералов чисел нет — все они из `layout` (SHADE_*) и стиля; готовый JS слоя
    собирает `plan_intro_tpl.shade_js` (текст подстановки живёт там же, где остальные
    подстановки интро — второй копии текста нет).
    """
    stv, k = inp.style, inp.px
    # Затемнение под интро (масштабирование KF): единственный источник чисел —
    # этот план, из него их берут и шаблон (.jsx), и предпросмотр. Выключенная галка = None:
    # подстановка в шаблоне пустая, .jsx не меняется ни на байт (golden). Координаты — в
    # системе нула «Камера 1» (та же, в которой стоит нул «интро»: [0, INTRO_Y], template.py).
    # k — множитель пиксельных констант кадра (`_px` = min(W, H)/1080): затемнение снято
    # с композиций шириной 1080 и растёт вместе с короткой стороной кадра — тем же правилом,
    # что карточка вставки и числа стиля. Раньше здесь стояло своё k = W/1080, и в 16:9
    # (1920×1080) затемнение росло в 1.78 раза, хотя текст интро — нет.
    # stv.intro_scale_k (intro_scale / 100, бывшее _G) — масштаб нула интро: затемнение
    # висит на нуле «Камера 1», поэтому его scale и сдвиг SHADE_DY от intro_y
    # масштабируются на него вслед за размером и положением текста интро.
    if not bool(stv.intro_shade):
        return ShadePlan(numbers=None)
    shade_plan = {
        "x": _r(SHADE_X * k), "y": _r(float(stv.intro_y) + SHADE_DY * k * stv.intro_scale_k),
        "scale": _r(SHADE_SCALE * stv.intro_scale_k), "w": _r(SHADE_W * k), "h": _r(SHADE_H * k),
        "ox": _r(SHADE_OX * k), "oy": _r(SHADE_OY * k), "blur": _r(SHADE_BLUR * k),
        "op": stv.intro_shade_op,
    }
    return ShadePlan(numbers=shade_plan)
