# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Папка проекта и ассетов, лесенка шрифтов (остаток распила scene_plan).

Модуль забирает из `scene_plan` два соседних решения, у которых общий вход — путь XML
и стиль:

* **папка проекта и ассетов**: `base` (аргумент сборки, иначе папка XML) и `asset_base` —
  та же папка, а если рядом с XML нет `assets/assets.json`, то `assets/` рядом с
  установкой Reelsi (ролик могли экспортировать в другую папку). Резолвер ассетов
  (`core.assets.resolver`) строится здесь же — он читает `assets.json` один раз;
* **лесенка шрифтов «пусто = как базовый»**: базовый, жёлтых, интро и жёлтых интро,
  акцентный и «заднего плана» с их регистрами. Шрифты остаются ОТДЕЛЬНЫМИ входами
  модулей `plan_*` (как были): структура стиля держит сырые значения, лесенку собирает
  этот модуль.

Перенос ПОСТРОЧНЫЙ: поведение и порядок операций не менялись ни на байт (проверяется
эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана). Имена
локальных переменных оставлены как в scene_plan.

Вход — один неизменяемый `AssetsInputs`, выход — один `AssetsPlan`. Стиль приходит
структурой `StyleValues` одним полем `style`: второго чтения ключей стиля нет.
"""
import os
from dataclasses import dataclass
from typing import Any

from .layout import _project_base
from .parse import HERE
from .plan_style import StyleValues


@dataclass(frozen=True)
class AssetsInputs:
    """Вход блока папки и шрифтов: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan: `base` — аргумент сборки
    (None — папка проекта берётся по XML), `xml_path` — путь XML, `style` — структура
    стиля, прочитанная ОДИН раз.
    """
    base: Any
    xml_path: Any
    style: StyleValues


@dataclass(frozen=True)
class AssetsPlan:
    """Выход блока папки и шрифтов: ровно те имена, что `scene_plan` читает дальше.

    `base` — папка проекта (её читают звук и вставки), `aset` — резолвер ассетов
    (ризер, переход, whoosh, звук глитча), шрифты — отдельные входы модулей `plan_*`.
    """
    base: Any
    asset_base: Any
    aset: Any
    font_ps: Any
    hl_font_ps: Any
    intro_font_ps: Any
    intro_hl_font_ps: Any
    accent_font_ps: Any
    accent_case: Any
    back_font_ps: Any
    back_case: Any


def plan_assets(inp: AssetsInputs) -> AssetsPlan:
    """Папка проекта и ассетов плюс шрифты: `base`/`asset_base`/`aset` и лесенка шрифтов.

    Тело — дословный перенос блока из scene_plan (до распила — строки 581-602):
    имена локальных переменных оставлены прежними, поэтому ни одна строка не переписана.
    """
    base, xml_path = inp.base, inp.xml_path
    stv = inp.style
    base = base or _project_base(xml_path)
    from core import assets as _assets
    # assets live next to the XML's project OR in assets/ next to the Reelsi install.
    # This matters when the edited sequence is exported to some other folder.
    asset_base = base
    if not os.path.isfile(os.path.join(base, "assets", "assets.json")):
        alt = os.path.dirname(HERE)
        if os.path.isfile(os.path.join(alt, "assets", "assets.json")):
            asset_base = alt
    aset = _assets.resolver(asset_base)
    # Шрифты — отдельные входы модулей (шрифты остаются как есть),
    # поэтому лесенку «пусто = как базовый» собираем здесь из сырых значений структуры.
    font_ps = stv.font  # st уже резолвнут выше
    hl_font_ps = stv.hl_font or font_ps
    intro_font_ps = stv.intro_font or font_ps        # шрифты интро: пусто = как субтитры
    intro_hl_font_ps = stv.intro_hl_font or hl_font_ps
    # Акцентный шрифт строк интро: PostScript-имя; пусто = выключено.
    # Значение живой в стиле, в шаблон и план едет через данные строки (accent_font).
    accent_font_ps = str(stv.accent_font).strip()
    accent_case = str(stv.accent_case).strip()
    back_font_ps = str(stv.back_font).strip()
    back_case = str(stv.back_case).strip()
    return AssetsPlan(base=base, asset_base=asset_base, aset=aset,
                      font_ps=font_ps, hl_font_ps=hl_font_ps,
                      intro_font_ps=intro_font_ps, intro_hl_font_ps=intro_hl_font_ps,
                      accent_font_ps=accent_font_ps, accent_case=accent_case,
                      back_font_ps=back_font_ps, back_case=back_case)
