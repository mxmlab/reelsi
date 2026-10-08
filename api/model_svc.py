# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сервис моделей: потолок роликов для настроек нарезки (лёгкий GET).

Один роут и нарочно лёгкий. Настройкам нужно показать под «Роликов одновременно»
серую строку «на этой машине модели: видеокарта, слотов N» — и показать её при
открытии ⚙, то есть в процессе сервера интерфейса. Считать это должен ТОТ ЖЕ код,
что считает сервис (`core.model_service.slot_count`), иначе подпись обещала бы одно
число, а сервис держал другое.

Почему отдельным роутом, а не полем `GET /api/ai_config`: сервиса моделей сам
`ai_config` не знает (он читает настройку через `core.device`), и складывать
машинную подпись в конфиг профилей ИИ значило бы смешать «чем режем» и «на чём
считаем». Заодно роут не поднимает сервис и НЕ импортирует torch: свободную
видеопамять отдаёт `nvidia-smi` (`core.device.free_vram_mib`), а `torch` в сервере
интерфейса не нужен вовсе — правило проверяется тестом в подпроцессе.

Значение настройки читает `slot_count` через `core.device` → `ai_config.json`;
пишет её действие `set_model_device` роута `/api/ai_config` (см.
`core/aicut/config_actions.py`), и это ОДНО поле: подпись и выбор не могут разойтись.
"""
from typing import Any

from flask import Response, jsonify

from ._core import bp, umsg_err
from core.umsg import ReelsiError, umsg


@bp.route("/api/model_cap", methods=["GET"])
def api_model_cap() -> Response:
    """Сколько моделей эта машина считает разом: устройство и число слотов.

    `device` — `cuda`/`mps`/`cpu` (его переводит фронт: «видеокарта»/«процессор»),
    `slots` — сколько распознаваний идёт параллельно, `auto` — выбран ли режим
    «авто» (подпись про слабую карту и чужую LLM говорит именно про него), `setting`
    — как есть в настройке. Поднять сервис или посчитать что-то тяжёлое нельзя:
    роут зовётся при каждом открытии ⚙.
    """
    from core import model_service
    try:
        cap: dict[str, Any] = model_service.model_cap()
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
    except Exception as e:
        # Код ошибки — ОДНОЙ строкой с текстом: словарь перевода ищет `umsg("код"` в
        # исходниках (tests/test_i18n.py), и перенос вызова на строку выше оставил бы
        # ERR_model_cap_failed в словаре «висящим без дела».
        err = ReelsiError(umsg("model_cap_failed", "Не удалось посчитать потолок моделей: {err}", err=str(e)))
        return jsonify(**umsg_err(err))
    return jsonify(ok=True, **cap)
