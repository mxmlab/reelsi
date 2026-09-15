# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Единственный источник путей: корень репозитория, данные репозитория, примеры.

ПОЧЕМУ этот модуль есть (задание GU). Движок переехал в пакет `core/`, и
`os.path.dirname(os.path.abspath(__file__))` из любого его модуля стал указывать
на `core/`, а не на корень репозитория. А в корне лежат ЛИЧНЫЕ файлы
пользователя: `ai_config.json` с ключами провайдеров, `insertlib.json` — база
вставок на полторы тысячи файлов, `terms.json`, `styles/`, `speakers/`. Молча
переехавший путь — это «интерфейс открылся без ключей и со пустой базой» без
единой ошибки в логе. Поэтому папку модуля в ядре не считает никто, кроме
этого файла: всё остальное берёт `ROOT`, `DATA` и `EXAMPLES` отсюда.

Переопределения через `env(...)` (`REELSI_AI_CONFIG`, `REELSI_JOB_LOCK` и
прочие) остаются в самих модулях: это личные настройки профиля, а не раскладка.
"""
import os

# Корень репозитория — на уровень выше самого пакета core/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Данные, которые лежат в репозитории (шаблоны, словари, эталоны, движки ASR).
DATA = os.path.join(ROOT, "data")
# Примеры личных файлов (*.example.json) — из них bootstrap заводит боевые.
EXAMPLES = os.path.join(ROOT, "examples")


def root(*parts):
    """Путь к личному файлу или папке пользователя — они живут в корне репозитория."""
    return os.path.join(ROOT, *parts)


def data(*parts):
    """Путь к данным, которые лежат в репозитории (data/)."""
    return os.path.join(DATA, *parts)


def require_source_tree():
    """Проверяет, что Reelsi запущен из клона репозитория (editable install).

    Обычный `pip install .` в site-packages не поддерживается: templates/, static/
    и личные файлы пользователя живут в корне клона.
    """
    missing = [d for d in ("templates", "static", "data") if not os.path.isdir(os.path.join(ROOT, d))]
    if missing:
        raise SystemExit(
            f"Error: Reelsi must be run from a git clone repository (editable install).\n"
            f"Please run 'pip install -e .' from the repository root.\n"
            f"Missing required source directories in {ROOT}: {', '.join(missing)}."
        )
