# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сессия фона (rembg + onnxruntime): как ОТКРЫТЬ сессию на видеокарте или на процессоре.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Открытие сессии раньше жило в `core/insertlib.py` и звалось
только локальным путём вставок. Сервис моделей тоже снимает фон (одна копия модели на
машину, сверка VRAM, выгрузка по простою), и открывать сессию он обязан тем же кодом,
а не второй копией. Поэтому здесь всё про открытие; кэш сессии, таймер простоя и обрезка
полей остались в `core/insertlib.py`.

ЗАМЕР, ИЗ-ЗА КОТОРОГО ПОРЯДОК ИМЕННО ТАКОЙ (2026-10-23, RTX 4070 Ti SUPER 16 ГБ,
onnxruntime-gpu 1.23.2, модель `birefnet-general`, «две очистки разом» через
`insertlib.remove_bg`, свободная карта):

| Опции CUDA | Раунд 1 | Раунды 2–6 | Время пары |
|---|---|---|---|
| опции cuDNN без потолка арены | 15.7 ГБ VRAM + 2 ГБ RAM | 15.8 ГБ VRAM + 18.4 ГБ RAM | 10–13 с |
| + `arena_extend_strategy=kSameAsRequested`, `gpu_mem_limit=10 ГиБ` | 10.25 ГБ, RAM 66 МБ | 10.25 ГБ, RAM 72 МБ | 0.40 с |
| то же, 4 очистки разом | 10.7 ГБ | 10.7 ГБ | 0.42 с |
| потолок 6 или 8 ГиБ | — | — | падает: `bfc_arena` на узле `aspp_deforms` |

`u2net`: 1.2 ГБ VRAM, потолок 1 ГиБ держит, 0.18 с на пару.

Почему без потолка плохо:
1. Арена onnxruntime растёт удвоением и просит больше, чем есть на карте. Windows (WDDM)
   нехватку VRAM ошибкой не отдаёт: молча даёт общую память (RAM). Сессия числится «на
   CUDA», а считает в ~20 раз медленнее, и RAM растёт.
2. Свободную VRAM до открытия никто не проверял: Блендер или ComfyUI держат карту, а
   сессия всё равно открывалась на CUDA и уходила в RAM.
3. Факт открытия на видеокарте не проверялся: onnxruntime откатывается на процессор молча.
"""
from __future__ import annotations

import os
from typing import Any

from core import model_service, ort_gpu
from core.applog import get_logger
from core.umsg import ReelsiError

log = get_logger(__name__)

# Арена onnxruntime на видеокарте, МиБ, по моделям. Потолок обязателен (см. докстринг модуля).
ARENA_MIB: dict[str, int] = {"birefnet-general": 10240, "u2net": 1024}
# Модель вне таблицы — с потолком самой большой: меньше может не открыться, больше — арена
# уйдёт в общую память.
ARENA_MIB_DEFAULT = 10240
# Простой сессии, сек., после которого её выгружают — и с видеокарты, и с процессора.
# Следующая очистка откроет сессию заново и снова спросит видеокарту: карта, занятая
# сейчас, может освободиться к следующей пачке вставок.
IDLE_S = 60

# DLL CUDA/cuDNN из torch подключаем один раз за процесс (см. `_cuda_dll_dirs`).
_CUDA_DLLS_DONE = False
_CUDA_DLL_DIRS: list[Any] = []   # хэндлы add_dll_directory: держим их живыми


def _arena_mib(name: str) -> int:
    """Потолок арены модели `name` в МиБ."""
    return ARENA_MIB.get(name, ARENA_MIB_DEFAULT)


def cuda_options(name: str) -> dict[str, str]:
    """Опции провайдера CUDA для сессии модели `name` (значения — строки, как в onnxruntime).

    `cudnn_*` — из первого замера: без них CUDA заметно медленнее. Две последние опции —
    потолок арены (см. ARENA_MIB и таблицу в докстринге модуля): без них арена удваивается,
    просит больше карты, а Windows отдаёт остаток через общую память без ошибки.
    """
    return {
        "cudnn_conv_use_max_workspace": "1",
        "cudnn_conv_algo_search": "HEURISTIC",
        "arena_extend_strategy": "kSameAsRequested",
        "gpu_mem_limit": str(_arena_mib(name) * 1024 * 1024),
    }


def vram_need_mib(name: str) -> int:
    """Сколько свободной VRAM нужно, чтобы открыть сессию модели `name` на видеокарте.

    Арена модели плюс запас сервиса моделей (тот же `VRAM_RESERVE_MIB`, своей копии нет).
    """
    return _arena_mib(name) + model_service.VRAM_RESERVE_MIB


def _cuda_dll_dirs() -> None:
    """DLL CUDA/cuDNN для onnxruntime-gpu берём из `torch/lib`: torch cu126 несёт их с собой.

    Без них сессия на видеокарте не создаётся: Windows не находит библиотеки cuDNN.
    Добавляем один раз за процесс. На не-Windows `os.add_dll_directory` нет — пропуск.
    Ошибка не фатальна: сессия откатится на процессор (см. `open_session`).
    """
    global _CUDA_DLLS_DONE
    if _CUDA_DLLS_DONE:
        return
    _CUDA_DLLS_DONE = True
    add = getattr(os, "add_dll_directory", None)
    if add is None:
        return
    try:
        import torch
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            _CUDA_DLL_DIRS.append(add(lib))
    except Exception as e:  # torch не импортируется — сессия попробует без него и откатится
        log.warning("DLL CUDA из torch не подключены (%s)", e)


def _onnx_has_cuda() -> bool:
    """Видит ли onnxruntime провайдер CUDA (стоит onnxruntime-gpu). Нет onnxruntime — нет CUDA."""
    try:
        import onnxruntime
    except ImportError:  # без onnxruntime rembg не работает вовсе; причина видна в doctor
        return False
    return "CUDAExecutionProvider" in onnxruntime.get_available_providers()


def _on_cuda(session: Any) -> bool:
    """Открыта ли сессия на видеокарте ПО ФАКТУ: первый провайдер onnxruntime — CUDA.

    Достаём через getattr: у подменной сессии (тесты) может не быть ни внутренней сессии,
    ни `get_providers`. Не подтвердили — считаем, что сессия на процессоре.
    """
    inner = getattr(session, "inner_session", None)
    get_providers = getattr(inner, "get_providers", None)
    if not callable(get_providers):
        return False
    providers = list(get_providers())
    return bool(providers) and providers[0] == "CUDAExecutionProvider"


def open_session(name: str, want_gpu: bool, free_mib: int | None) -> tuple[Any, bool, str]:
    """Открыть сессию модели `name`: (сессия, на_видеокарте, причина_если_процессор).

    Видеокарта — только если выполнены все три условия: устройство выбрано (`want_gpu`),
    onnxruntime видит CUDA и свободной VRAM хватает на арену модели с запасом. `free_mib`
    None — меры нет (CI, мак): не отказываем, как и `model_service.vram_fits`. Не вышло —
    процессор, а причина по-русски: её покажем человеку.

    Факт проверяем ПОСЛЕ открытия (`_on_cuda`): onnxruntime откатывается на процессор
    молча, и «сессия создана» ещё не значит «на видеокарте».

    `ort_gpu.activate()` — ДО импорта onnxruntime и rembg: если системный onnxruntime уже
    загружен, папка onnxruntime-gpu его не подменит (см. `core/ort_gpu.py`).
    """
    ort_gpu.activate()
    cuda = False
    why = ""
    if not want_gpu:
        why = "настройка: процессор"
    elif not _onnx_has_cuda():
        why = "нет CUDA в onnxruntime"
    elif free_mib is not None and free_mib < vram_need_mib(name):
        why = "видеокарта занята: свободно {free} МиБ, фону нужно {need}".format(
            free=free_mib, need=vram_need_mib(name))
    else:
        cuda = True
    try:
        from rembg import new_session
    except ImportError:
        raise ReelsiError("для снятия фона нужен пакет rembg — «pip install rembg» "
                          "(или сними галку «убирать фон» в ⚙)")
    if not cuda:
        return new_session(name, providers=["CPUExecutionProvider"]), False, why
    _cuda_dll_dirs()
    try:
        session = new_session(name, providers=[("CUDAExecutionProvider", cuda_options(name)),
                                               "CPUExecutionProvider"])
    except ReelsiError:
        raise
    except Exception as e:
        log.warning("фон на видеокарте не открылся (%s: %s) — откат на процессор",
                    type(e).__name__, e)
        why = "видеокарта не открылась: %s: %s" % (type(e).__name__, e)
        return new_session(name, providers=["CPUExecutionProvider"]), False, why
    if not _on_cuda(session):
        log.warning("фон: сессия %s открыта на процессоре, хотя просили видеокарту", name)
        return session, False, "onnxruntime открыл сессию на процессоре"
    return session, True, ""


def predict(session: Any, img: bytes) -> bytes:
    """Вырезать фон из картинки `img` (байты) открытой сессией: результат — PNG с альфой."""
    from rembg import remove
    return remove(img, session=session)
