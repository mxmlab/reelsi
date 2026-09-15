# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Логирование в файл с ротацией для Reelsi.

Единственный источник настройки файлового лога: RotatingFileHandler (1 МБ x 3, UTF-8),
путь из REELSI_LOG / AUTOCUT_LOG или reelsi.log в корне репозитория.
"""
import logging
from logging.handlers import RotatingFileHandler
import os

from core import app_meta, paths

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name="reelsi"):
    """Возвращает логгер с подключенным выводом в RotatingFileHandler (1 МБ x 3).

    Файловый обработчик живёт ТОЛЬКО на базовом логгере 'reelsi' (один на процесс;
    при смене REELSI_LOG переназначается). У базового логгера propagate=False.
    get_logger(name) настраивает базовый логгер идемпотентно и возвращает
    logging.getLogger(name); дочерние 'reelsi.*' своих обработчиков не имеют
    и propagate=True. Файл создаётся лениво при первой записи (delay=True).
    """
    base_logger = logging.getLogger("reelsi")
    log_path = app_meta.env("LOG") or paths.root("reelsi.log")
    abs_path = os.path.abspath(log_path)

    # Проверяем, есть ли уже RotatingFileHandler на этот же файл на базовом логгере
    handler_exists = False
    for h in list(base_logger.handlers):
        if isinstance(h, RotatingFileHandler):
            if not handler_exists and os.path.abspath(getattr(h, "baseFilename", "")) == abs_path:
                handler_exists = True
            else:
                # Если путь к логу изменился или дублируется обработчик
                base_logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass

    if not handler_exists:
        log_dir = os.path.dirname(abs_path)
        if log_dir and not os.path.isdir(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError:
                pass

        handler = RotatingFileHandler(
            abs_path,
            maxBytes=1 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        base_logger.addHandler(handler)

    base_logger.propagate = False
    if base_logger.level == logging.NOTSET:
        base_logger.setLevel(logging.INFO)

    logger = logging.getLogger(name)
    if logger is not base_logger:
        logger.propagate = True
        # Дочерние 'reelsi.*' своих обработчиков не имеют
        for h in list(logger.handlers):
            if isinstance(h, RotatingFileHandler):
                logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass

    return logger
