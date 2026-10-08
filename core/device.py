# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Выбор вычислительного устройства: cuda -> mps -> cpu.

До этого каждый модуль решал сам, и решал одинаково: `"cuda" if
torch.cuda.is_available() else "cpu"`. На маке это означало «всё на CPU» даже там,
где Metal справился бы, — не потому что нельзя, а потому что вариант mps никто не
написал (разбор по платформам — `docs/PLATFORMS.md`).

ВАЖНО: ни одна ветка кроме cuda/cpu на живом железе НЕ ПРОВЕРЕНА — Apple Silicon и
Radeon у нас нет. На Windows с NVIDIA поведение ровно прежнее: cuda находится первой,
до mps дело не доходит.

ROCm (AMD) отдельной ветки не требует: сборка PyTorch под ROCm выдаёт себя за
`torch.cuda` — код остаётся тем же, меняется только колесо при установке.
"""
import os
import platform
import subprocess
from typing import Any
from core.umsg import ReelsiError

# MPS покрывает не все операции, и без этой переменной инференс падает на первой же
# неподдержанной вместо того, чтобы досчитать её на CPU. Ставим ДО импорта torch —
# позже он её уже не перечитает. setdefault: если юзер выставил осознанно, не спорим.
if platform.system() == "Darwin":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

VALID = ("cuda", "mps", "cpu")

# Настройка «Где считать модели» (сервис моделей): авто / видеокарта / процессор.
# Значения нарочно те же, что у `pick_device`, а не свои («gpu»/«cpu»): выбор уезжает
# прямо в него, и второй словарь с переводом одного и того же разошёлся бы молча.
# `auto` — не устройство, а «реши сам» (cuda, потом mps, потом cpu).
DEVICE_AUTO = "auto"
DEVICE_CHOICES = (DEVICE_AUTO,) + VALID


def model_device() -> str:
    """Выбор «Где считать модели» из сервисного конфига (`ai_config.json`).

    Настройка живёт рядом с соседними настройками нарезки (см. `core.aicut.config`),
    а не в localStorage: сервис — ОТДЕЛЬНЫЙ процесс, и в браузер за ней он не пойдёт.
    Значение проверяется здесь, а не у вызывающего: битую запись (чужое слово, число)
    трактуем как «авто», чтобы сервис не остался вовсе без устройства.

    Импорт `core.aicut.config` — ВНУТРИ функции: тот сам импортирует этот модуль
    (`step_concurrency` считает потолок по карте), и импорт на уровне модуля замкнул бы
    кольцо. Отказ конфига (нет файла, битый JSON) — тоже «авто».
    """
    try:
        from core.aicut.config import load_ai_config
        val = load_ai_config().get("model_device")
    except Exception:
        return DEVICE_AUTO
    return val if val in DEVICE_CHOICES else DEVICE_AUTO


def pick_device(force: str | None = None) -> str:
    """Явный выбор (аргумент или env) приоритетнее авто. Всегда возвращает строку."""
    d = (force or "").strip().lower()
    if d == DEVICE_AUTO:
        d = ""                       # «авто» — это и есть ветка решения ниже
    if d in VALID:
        return d
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except ReelsiError: raise
    except Exception:
        pass  # torch недоступен/без GPU — работаем на CPU
    return "cpu"


def auto_device_without_torch() -> str:
    """Устройство для «авто» БЕЗ импорта torch: cuda -> mps -> cpu.

    Второй копии решения быть не должно: подпись под настройкой («где считает
    сервис») и сам сервис обязаны называть ОДНО устройство, иначе интерфейс обещает
    не то, что будет. `pick_device` отвечает точнее (он спрашивает torch), но в
    процессе интерфейса torch нет и быть не должно: импорт тянет CUDA и сотни
    мегабайт контекста. Здесь тот же порядок, но по признакам, видимым без torch:

    - карта NVIDIA видна `nvidia-smi` — «cuda». Проверка идёт на ЛЮБОЙ платформе:
      имя `nvidia-smi` не содержит имён платформ, и на x86-линуксе с NVIDIA это
      работает ровно так же (там эта же мера уже запасной ход у `free_vram_mib`);
    - macOS на arm64 — «mps». Сборка torch под Intel-мак Metal не даёт, а
      `pick_device` до этой ветки не доходит именно из-за отсутствия ускорения;
    - всё прочее — «cpu».

    Функция только для «авто»: выбор человека (`cuda`/`cpu`) досюда не доходит.
    """
    if nvidia_free_mib() is not None:
        return "cuda"
    if platform.system() == "Darwin" and platform.machine().lower() == "arm64":
        return "mps"
    return "cpu"


def autocast_dtype(dev: str) -> Any:
    """fp16 — только там, где он проверенно быстрее и стабильнее.

    На CUDA половинная точность даёт 2x скорость и вдвое меньше памяти. На MPS fp16
    поддержан, но у нас нет железа, чтобы убедиться, что RVM на нём не сыплет NaN, —
    а тихо испорченная альфа-маска видна только рендером в AE. Пока честный fp32:
    медленнее, зато предсказуемо.
    """
    import torch
    return torch.float16 if dev == "cuda" else torch.float32


def ct2_device(device: str | None = None, compute_type: str | None = None) -> tuple[str, str]:
    """(device, compute_type) для faster-whisper. Отдельно от pick_device — НЕ описка.

    faster-whisper работает на CTranslate2, а он не поддерживает ни Metal, ни ROCm:
    на маке это не «медленнее», этого просто нет. Отдать ему `mps` — гарантированное
    падение, поэтому здесь ровно два исхода: CUDA или CPU. На CPU float16 тоже не
    вариант (CTranslate2 его там не считает) — берём int8, он для CPU и сделан.

    Быстрая транскрипция на Mac и AMD решается только сменой движка на whisper.cpp —
    см. `docs/PLATFORMS.md`.
    """
    if device:
        return device, (compute_type or ("float16" if device == "cuda" else "int8"))
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", (compute_type or "float16")
    except ReelsiError: raise
    except Exception:
        pass  # torch недоступен/без GPU — работаем на CPU
    return "cpu", (compute_type or "int8")


# --------------------------------------------------------------------------- #
# Бюджет видеопамяти: сколько роликов и какая модель влезают в карту
# --------------------------------------------------------------------------- #
# Замер на живом задании (карта 4096 МиБ): четыре процесса нарезки держали по
# 568 МиБ — и это НЕ веса моделей, а CUDA-контекст, который torch создаёт при
# первом обращении к карте и держит до выхода процесса (пик под работой 696 МиБ).
# Отсюда два числа: 600 МиБ на процесс ролика (медиана замера с запасом) и
# ~1160 МиБ накладных расходов у faster-whisper (тот же контекст плюс рабочие
# буферы распознавания).
CUT_ROLE_VRAM_MIB = 600
WHISPER_OVERHEAD_MIB = 1160

# Оценка весов Whisper в МиБ: float16 — примерно размер файла модели, int8_float16 —
# втрое меньше (половина от int8 плюс fp16-активации). Порядок — по убыванию
# аппетита; большие модели идут первыми намеренно: шаг «на ступень меньше» это и
# есть переход к следующей записи.
WHISPER_VRAM_MIB: dict[str, dict[str, int]] = {
    "large-v3": {"float16": 4300, "int8_float16": 1400},
    "medium": {"float16": 2200, "int8_float16": 700},
    "small": {"float16": 1200, "int8_float16": 400},
}


def _nvidia_smi_free_mib() -> int | None:
    """Свободная VRAM через `nvidia-smi` — путь на случай, когда torch не поднялся
    или собран без CUDA (тот же запасной ход, что в `doctor.py`)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return int((r.stdout or "").strip().splitlines()[0])
    except Exception:
        return None  # нет nvidia-smi / не разобрали вывод — считаем, что меры нет


def nvidia_free_mib() -> int | None:
    """Свободная VRAM ОДНИМ `nvidia-smi`, без torch — для процессов без него.

    Имя публичное нарочно: сервер интерфейса не держит torch вовсе (он нужен только
    процессам счёта), а подпись «сколько моделей тянет эта машина» посчитать надо.
    `free_vram_mib` тут не годится: он ПЕРВЫМ делом пробует torch и импортирует его —
    в UI-процессе это сотни мегабайт и CUDA-контекст на ровном месте.
    """
    return _nvidia_smi_free_mib()


def free_vram_mib() -> int | None:
    """Свободная видеопамять в МиБ или None, если карты нет (или мера недоступна).

    None — это «мерить нечего»: выше по стеку он значит «потолка нет», а не «ноль
    памяти». Иначе на машине без CUDA (CI, мак) любой параллелизм схлопнулся бы в 1.
    Порядок тот же, что в `doctor.py`: torch, затем nvidia-smi.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return int(torch.cuda.mem_get_info()[0] // (1024 * 1024))
    except ReelsiError:
        raise
    except Exception:
        pass  # torch недоступен/без GPU либо драйвер не отдал меру — пробуем nvidia-smi
    return _nvidia_smi_free_mib()


def vram_total_mib() -> int | None:
    """Полный объём карты в МиБ или None, если карты нет (для сообщений человеку)."""
    try:
        import torch
        if torch.cuda.is_available():
            return int(torch.cuda.mem_get_info()[1] // (1024 * 1024))
    except ReelsiError:
        raise
    except Exception:
        pass  # torch недоступен/без GPU — размер карты останется неизвестным
    return None


def parallel_width_budget(free_mib: int | None = None) -> int | None:
    """Сколько роликов ИИ-нарезки тянет карта, или None — потолка нет.

    `free_mib` — для тестов: без него мера берётся у железа. `K = free // 600`,
    но не меньше 1: даже на занятой карте один ролик запускаем — отказ «0 роликов»
    был бы непонятнее, чем честный OOM.
    """
    free = free_vram_mib() if free_mib is None else free_mib
    if free is None:
        return None
    return max(1, int(free) // CUT_ROLE_VRAM_MIB)


def whisper_fits(size: str, compute_type: str, free_mib: int | None = None) -> bool:
    """Влезает ли faster-whisper этой модели и точности в свободную память.

    Мера неизвестна (None) → считаем, что влезает: гадать по незнанию и молча
    менять модель пользователю хуже, чем попробовать и один раз упасть с внятной
    причиной (запасной путь по факту падения остаётся).
    """
    free = free_vram_mib() if free_mib is None else free_mib
    if free is None:
        return True
    cost = whisper_vram_mib(size, compute_type)
    if cost is None:
        return True
    return cost + WHISPER_OVERHEAD_MIB <= int(free)


def whisper_vram_mib(size: str, compute_type: str) -> int | None:
    """Оценка весов модели в МиБ; None — модели такой ступени мы не знаем."""
    return (WHISPER_VRAM_MIB.get(str(size or "")) or {}).get(str(compute_type or ""))


def is_oom_error(exc: BaseException) -> bool:
    """Это ошибка нехватки видеопамяти?

    Одной функцией нарочно: CUDA может сказать `torch.OutOfMemoryError`, голый
    `RuntimeError: CUDA failed with error out of memory`, а CTranslate2 поверх —
    `... failed to allocate ...`. Разбирать это в четырёх местах по-разному уже
    пробовали — получалось «в одном месте поймали, в другом уронили сервер».
    """
    if isinstance(exc, MemoryError):
        return True
    if type(exc).__name__ == "OutOfMemoryError":      # torch.cuda.OutOfMemoryError
        return True
    text = str(exc).lower()
    return "out of memory" in text or "insufficient memory" in text


def empty_cache(dev: str | None = None) -> None:
    """Отдать память обратно. На Windows переполнение VRAM не даёт честный OOM —
    оно вешает машину целиком, поэтому кэш чистим явно, а не надеемся на GC."""
    try:
        import torch
        if dev in (None, "cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()
        if dev in (None, "mps"):
            mps = getattr(torch, "mps", None)
            if mps is not None and getattr(torch.backends, "mps", None) and \
                    torch.backends.mps.is_available():
                mps.empty_cache()
    except ReelsiError: raise
    except Exception:
        pass  # torch/GPU недоступны — чистить нечего
