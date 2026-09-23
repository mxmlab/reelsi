# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Папки камер и файлы в них: где лежит материал и что из него годится в нарезку.

ПОЧЕМУ модуль есть (инверсия слоёв). `find_cam_dirs` и `list_videos`
жили в CLI-модуле `reelsi.py`, а импортировали их HTTP-слой (`api/files.py`,
`api/jobs.py`) и `doctor.py` — командная строка оказывалась фундаментом бэкенда.
Теперь они тут, в ядре, а CLI импортирует их отсюда наравне со всеми.
"""
import os
import re

from core import paths
from core.app_meta import env

# Рабочая папка с исходниками (камеры, музыка, insert_library) — на уровень выше
# самого репозитория. Переопределяется переменной окружения REELSI_BASE или флагом
# --base. Считается здесь, а не в CLI: папку материала спрашивают и бэкенд
# (api/_core.DEFAULT_BASE), и doctor, и своя копия этого выражения в каждом из них
# разъехалась бы молча.
DEFAULT_BASE = env("BASE") or os.path.dirname(paths.ROOT)


def _is_cam_dir(d: str) -> bool:
    # Признак «папка камеры» — на оба языка сразу: русское «камер…» или английское
    # «camera…» либо «cam» прямо перед цифрой («cam3»). На чистой английской
    # установке русских папок нет вовсе — без этого /api/cams отвечал «нет папок».
    low = d.lower()
    return low.startswith("камер") or low.startswith("camera") or bool(re.match(r"^cam\d", low))


def find_cam_dirs(base: str) -> list[str]:
    subs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    # Order by the NUMBER in the folder name ("камера1" -> 1) so camera 1 is always
    # first regardless of letter case. Plain/casefold sort would put "Камера2"
    # (uppercase К) before "камера1" and swap the cameras -> cut would follow cam2.
    cams = [d for d in subs if _is_cam_dir(d)]

    def key(d: str) -> tuple[int, str]:
        m = re.search(r"(\d+)", d)
        return (int(m.group(1)) if m else 10**9, d.casefold())

    return [os.path.join(base, d) for d in sorted(cams, key=key)]


def list_videos(d: str) -> list[str]:
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".mp4", ".mov", ".mxf")))
