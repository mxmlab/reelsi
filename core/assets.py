# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Resolve project assets (SFX / transitions) by logical role via assets/assets.json.
Non-destructive: keeps original filenames, the JSON maps role -> file. Missing role
or file -> empty string (feature just skips that asset)."""
import os, json
from core.applog import get_logger

log = get_logger("reelsi.assets")

ROLES = ("intro_riser", "whoosh", "transition", "highlight_pop", "glitch")


def resolver(base):
    d = os.path.join(base, "assets")
    cfg = os.path.join(d, "assets.json")
    m = {}
    if os.path.isfile(cfg):
        try:
            with open(cfg, encoding="utf-8") as f:
                data = json.load(f)
            # Тип проверяем ДО использования: `[]` или `{"whoosh": 5}` роняли сборку .jsx
            # AttributeError'ом в недрах xml2ae, а битый JSON молча означал «все SFX пропали».
            if isinstance(data, dict):
                m = data
            else:
                log.warning("Файл ассетов не словарь (%s): %s", type(data).__name__, cfg)
        except Exception as e:
            log.warning("Не удалось прочитать файл ассетов %s: %s", cfg, e)

    assets_dir_real = os.path.realpath(d)

    def path(role):
        val = m.get(role)
        if not isinstance(val, str):
            return ""            # не строка (число, список) — роль просто пропускаем
        fn = val.strip()
        if not fn:
            return ""
        p = os.path.join(d, fn)
        # Значение из файла — недоверенное: `../` в нём уводил сборку .jsx к любому
        # файлу на диске. Сверяем РЕАЛЬНЫЕ пути (после разворота симлинков и `..`).
        p_real = os.path.realpath(p)
        try:
            inside = os.path.commonpath([assets_dir_real, p_real]) == assets_dir_real
        except ValueError:       # другой диск — общего пути нет
            inside = False
        if not inside or p_real == assets_dir_real:
            return ""
        return p if os.path.isfile(p) else ""

    return path
