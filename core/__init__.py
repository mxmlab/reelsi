# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Движок Reelsi: нарезка, субтитры, сборка .jsx, ИИ-шаги.

Пакет заведён: в корне репозитория остаются только точки входа
(`webui.py`, `reelsi.py`, `doctor.py`), движок живёт здесь, рядом — `data/`
(данные из репозитория), `examples/`, `tools/`, `docs/`. Личные файлы
пользователя (`ai_config.json`, `insertlib.json`, `terms.json`, `styles/`,
`speakers/`) остаются в КОРНЕ репозитория — см. `core/paths.py`.

Импорты внутри пакета — абсолютные (`from core import align`, `from core.styles
import X`). Относительные `from .` внутри `aicut/`, `gigaam_cut/`, `xml2ae/`
остаются как были. Так модуль не грузится дважды — как `styles` и как
`core.styles`, — иначе у двух копий разное состояние кэшей и путей.
"""
