# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Цвет камер через Lumetri: девять значений стиля (остаток распила scene_plan).

`scene_plan` собирал словари LUMETRI/LUMETRI2 из стиля — их читают и .jsx (подстановки
`lumetri_decl`/`lumetri_cam`/`lumetri_roto`, план_ae), и превью (`plan["lumetri"]`),
второй копии нет. Модуль забирает этот сбор: одна дверь на оба словаря.

Экспозиция клипа (kwarg `exposure` с шага AE) ПРИБАВЛЯЕТСЯ к стилевой: раньше её нёс
EXPOSURE ровно на тех же слоях. Выключенная галка — None: подстановки шаблона прежние,
.jsx побайтово как раньше (golden). Ключи читаются ЯВНО (не склейкой "lm_"+k): сторож
схемы (`tests/test_r11_li_every_knob`) ищет ручку в коде по её имени.

Перенос ПОСТРОЧНЫЙ: числа и порядок ключей не менялись ни на байт (проверяется эталоном
fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
"""
from dataclasses import dataclass
from typing import Any

from .plan_style import StyleValues


@dataclass(frozen=True)
class LumetriInputs:
    """Вход цвета камер: структура стиля и экспозиция клипа с шага AE.

    Экспозиция приходит kwarg'ом сборки (`exposure`), а не из стиля: в .jsx её несла
    покадровая экспозиция клипа, и там она прибавляется к стилевой.
    """
    style: StyleValues
    exposure: Any


@dataclass(frozen=True)
class LumetriPlan:
    """Выход: словари Lumetri камеры 1 и камеры 2 (None — галка снята).

    `lum2` непустой ТОЛЬКО при разомкнутой цепочке связи (`lm2_link`): при связанной
    значения Камеры 2 берутся из первого словаря, и второго в плане нет.
    """
    lum: "dict[str, float] | None"
    lum2: "dict[str, float] | None"


def plan_lumetri(inp: LumetriInputs) -> LumetriPlan:
    """Собрать значения Lumetri: чистая функция от `LumetriInputs`."""
    stv, exposure = inp.style, inp.exposure
    lum = None
    if stv.lm_on:
        lum = {
            "exposure": stv.lm_exposure + float(exposure or 0),
            "contrast": stv.lm_contrast,
            "highlights": stv.lm_highlights,
            "shadows": stv.lm_shadows,
            "whites": stv.lm_whites,
            "blacks": stv.lm_blacks,
            "temp": stv.lm_temp,
            "tint": stv.lm_tint,
            "sat": stv.lm_sat,
        }
    lum2 = None
    if not stv.lm2_link and stv.lm2_on:
        lum2 = {
            "exposure": stv.lm2_exposure + float(exposure or 0),
            "contrast": stv.lm2_contrast,
            "highlights": stv.lm2_highlights,
            "shadows": stv.lm2_shadows,
            "whites": stv.lm2_whites,
            "blacks": stv.lm2_blacks,
            "temp": stv.lm2_temp,
            "tint": stv.lm2_tint,
            "sat": stv.lm2_sat,
        }
    return LumetriPlan(lum=lum, lum2=lum2)
