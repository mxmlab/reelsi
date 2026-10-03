# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Разбор таблицы LUT (.cube): размер, домен и точки в порядке файла.

Только чтение: раскладку по таблице делает тот, кто накладывает (шейдер превью),
а в сборку LUT уезжает прожигом в видео камеры.
"""
from __future__ import annotations
import os
from typing import Any

from core.umsg import ReelsiError, umsg

# Разобранные таблицы: (реальный путь, mtime) -> результат. Таблицу просят на каждую
# смену клипа и камеры в превью, а 33^3 точек — это сотни килобайт текста.
# Ключ с mtime, а не просто путь: файл могли заменить на диске, и старая таблица
# молча красила бы кадр по-старому до перезапуска сервера.
_CACHE: dict[tuple[str, float], dict[str, Any]] = {}


def _bad(path: str, why: str) -> ReelsiError:
    """Ошибка разбора. Один код на всё: и битый файл, и не та длина данных."""
    return ReelsiError(umsg("lut_bad", f"LUT не прочитался: {os.path.basename(path)} — {why}",
                            path=os.path.basename(path), why=why))


def _rgb(parts: list[str], path: str, line_no: int) -> list[float]:
    """Три числа из строки вида `DOMAIN_MIN 0 0 0`."""
    if len(parts) != 4:
        raise _bad(path, f"строка {line_no}: ожидались три числа")
    try:
        return [float(parts[1]), float(parts[2]), float(parts[3])]
    except ValueError:
        raise _bad(path, f"строка {line_no}: не число") from None


def _parse(path: str) -> dict[str, Any]:
    """Прочитать файл и разложить его на size/domain/data (без кеша)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        raise _bad(path, f"файл не читается ({e.strerror or e})") from e
    size = 0
    dmin = [0.0, 0.0, 0.0]
    dmax = [1.0, 1.0, 1.0]
    data: list[float] = []
    for line_no, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()   # комментарий — до конца строки
        if not line:
            continue
        parts = line.split()
        head = parts[0].upper()
        if head == "TITLE":
            continue                          # название таблицы — для человека
        if head == "LUT_1D_SIZE":
            raise ReelsiError(umsg("lut_1d_unsupported",
                                   f"LUT по 1D-таблице не поддерживается: {os.path.basename(path)}",
                                   path=os.path.basename(path)))
        if head == "LUT_3D_SIZE":
            if len(parts) != 2:
                raise _bad(path, f"строка {line_no}: у LUT_3D_SIZE ожидалось одно число")
            try:
                size = int(parts[1])
            except ValueError:
                raise _bad(path, f"строка {line_no}: размер не число") from None
            continue
        if head in ("DOMAIN_MIN", "DOMAIN_MAX"):
            vals = _rgb(parts, path, line_no)
            if head == "DOMAIN_MIN":
                dmin = vals
            else:
                dmax = vals
            continue
        if len(parts) != 3:
            raise _bad(path, f"строка {line_no}: ожидались три числа")
        try:
            data.extend((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            raise _bad(path, f"строка {line_no}: не число") from None
    if size < 1:
        raise _bad(path, "нет строки LUT_3D_SIZE")
    want = size * size * size * 3
    if len(data) != want:
        raise _bad(path, f"точек {len(data) // 3}, а по размеру {size} нужно {want // 3}")
    return {"size": size, "domain_min": dmin, "domain_max": dmax, "data": data}


def load_cube(path: str) -> dict[str, Any]:
    """Таблица LUT: {"size", "domain_min", "domain_max", "data"}.

    `data` — тройки чисел в порядке файла (красный меняется быстрее всех), как их
    и ждёт 3D-текстура. Битый файл и не та длина — `ReelsiError(umsg("lut_bad"))`,
    1D-таблица — `ReelsiError(umsg("lut_1d_unsupported"))`.

    Возвращаем копию словаря: `data` в кеше общая (она только читается), а вот
    домен и размеры вызывающий вправе поправить у себя, не портя кеш.
    """
    try:
        real = os.path.realpath(path)
        mtime = os.stat(path).st_mtime
    except OSError as e:
        raise _bad(path, f"файл не читается ({e.strerror or e})") from e
    hit = _CACHE.get((real, mtime))
    if hit is None:
        hit = _parse(path)
        for key in [k for k in _CACHE if k[0] == real]:
            del _CACHE[key]                     # старая версия того же файла больше не нужна
        _CACHE[(real, mtime)] = hit
    return {"size": hit["size"], "domain_min": list(hit["domain_min"]),
            "domain_max": list(hit["domain_max"]), "data": hit["data"]}
