# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Предзагрузка pyarrow (arrow.dll) до импорта torch.

В одном процессе pyarrow (файл arrow.dll) падает с access violation c0000005,
если он загружается ПОСЛЕ torch и именно через цепочку:
  breath.py -> transformers -> candidate_generator -> sklearn -> pandas -> pyarrow

Краш не меняет код возврата (процесс выходит с 0), но портит состояние WinAPI:
последующие системные вызовы отдают мусор (например, FileNotFoundError [WinError 2]
при вызове subprocess.run(["ffprobe", ...]) или [WinError 6714] при чтении файлов).

Если pyarrow загружен ПЕРВЫМ (до torch), связка torch + sklearn не падает.
Замеры (Application log, Event ID 1001, модуль arrow.dll; крашей на 3 запуска):
  A: import torch; import pyarrow                      -> 0
  L: import torch; import pandas                       -> 0
  M: import sklearn.metrics; import torch              -> 0
  N: import pandas; import torch                       -> 0
  I: import torch, sklearn.metrics                     -> 3 краша
  O: import pyarrow; import torch, sklearn.metrics     -> 0 крашей

Цена предзагрузки: ~0.10 с.
Сам факт импорта этого модуля безопасно предзагружает pyarrow. Никаких функций
вызывать не нужно. Если pyarrow не установлен в окружении — импорт тихо завершается.
"""
try:
    import pyarrow  # noqa: F401
except Exception:
    pass
