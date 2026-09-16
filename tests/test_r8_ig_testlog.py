# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IG (круг 8): тесты не должны писать в боевой `reelsi.log` в корне копии.

1. При импорте модулей бэкенда (`api.gdrive`, `api.render` и т.д.) вызывается `get_logger`,
   который вешает `RotatingFileHandler` на базовый логгер `reelsi`.
2. До правки `tests/conftest.py` не выставлял `REELSI_LOG` на уровне модуля до импортов,
   поэтому `applog` брал путь по умолчанию `paths.root('reelsi.log')`, и при возникновении
   ошибок в тестах трейсбеки попадали в рабочий логгер разработчика.
3. Проверяем, что после `import api` ни один файловый хэндлер не смотрит в корень репозитория,
   а запись через логгеры модулей уходит во временный файл сессии.

Запуск: py -3.10 -m pytest tests/test_r8_ig_testlog.py -q -p no:cacheprovider
"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_import_api_logger_points_to_tmp_not_root():
    """После import api ни один RotatingFileHandler базового логгера reelsi не смотрит на reelsi.log в корне."""
    import api  # noqa: F401
    from core import paths

    base_logger = logging.getLogger("reelsi")
    handlers = [h for h in base_logger.handlers if isinstance(h, RotatingFileHandler)]
    assert handlers, "Базовый логгер reelsi должен иметь хотя бы один RotatingFileHandler после import api"

    root_log = os.path.abspath(paths.root("reelsi.log"))
    for h in handlers:
        handler_path = os.path.abspath(h.baseFilename)
        assert handler_path != root_log, (
            f"RotatingFileHandler базового логгера смотрит в боевой reelsi.log ({root_log})"
        )
        assert "reelsi-tests-" in handler_path, (
            f"Путь лога должен находиться во временной папке с префиксом reelsi-tests-, получено: {handler_path}"
        )

    env_log = os.environ.get("REELSI_LOG")
    assert env_log, "Переменная окружения REELSI_LOG должна быть установлена"
    assert os.path.abspath(env_log) != root_log, "REELSI_LOG указывает на корень репозитория"
    assert "reelsi-tests-" in env_log, "REELSI_LOG должна содержать префикс reelsi-tests-"


def test_logging_does_not_touch_root_reelsi_log():
    """Запись логов из модулей API не создаёт и не изменяет reelsi.log в корне репозитория."""
    import api.gdrive as gdrive
    import api.render as render
    from core import paths

    root_log_path = Path(paths.root("reelsi.log"))
    # В основной копии владельца боевой лог есть, в свежей копии и в CI — нет: сравниваем
    # его состояние до и после, а не сам факт существования.
    def _state():
        return (root_log_path.stat().st_size, root_log_path.stat().st_mtime_ns) if root_log_path.exists() else None
    before = _state()

    # Пишем через логгеры модулей
    render.log.info("Тестовая запись IG: рендер")
    gdrive.log.warning("Тестовая запись IG: gdrive")

    for h in logging.getLogger("reelsi").handlers:
        h.flush()

    assert _state() == before, "запись лога из модулей тронула боевой reelsi.log в корне репозитория"

    env_log = os.environ.get("REELSI_LOG")
    assert env_log and os.path.exists(env_log), "Файл лога во временной папке должен существовать после записи"
    content = Path(env_log).read_text(encoding="utf-8")
    assert "Тестовая запись IG: рендер" in content
    assert "Тестовая запись IG: gdrive" in content
