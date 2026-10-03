# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звуковые устройства вывода — ОТДЕЛЬНЫМ процессом (запускает core/voicefx.py).

Зачем отдельный процесс. Список устройств спрашивает `pedalboard`, а он работает
поверх JUCE: сбор списка поднимает аудиосистему и тянет в процесс нативный код
(и его фоновые потоки). В сервере интерфейса этому делать нечего — ровно по той
же причине, по которой там не грузятся плагины (core/voicefx_scan.py).

    python -m core.voicefx_audio --devices

Наружу — одна строка JSON: {"devices": [имена], "default": имя}, а ошибка —
{"error", "text"}, как у прочих процессов обработки голоса. Общая обвязка — в
core/voicefx_proc.py (системные окна ошибок Windows, UTF-8, ошибка кодом).
"""
from __future__ import annotations
import json
import sys
from typing import Any, NoReturn, Sequence

from core import voicefx_proc
from core.umsg import ReelsiError, umsg


def pedalboard() -> Any:
    """Ленивый импорт pedalboard: без пакета звука нет вовсе."""
    try:
        import pedalboard as pb
    except ImportError:
        # Так отсутствие пакета подменяют тесты (`sys.modules[...] = None`):
        # импорт в этом случае тоже бросает ImportError, но у `None` нет ни одного
        # атрибута, и обращение к нему упало бы уже в чужом коде.
        found = getattr(sys.modules, "pedalboard", None)
    else:
        found = pb
    if found is None:
        raise ReelsiError(umsg("vst_unavailable",
                               "Нет пакета pedalboard — VST-плагины недоступны: "
                               "pip install pedalboard"))
    return found


def devices() -> dict[str, Any]:
    """Список устройств вывода и системное по умолчанию.

    Причина сбоя уезжает текстом: аудиосистемы может не быть вовсе (нет
    устройства, отобран доступ), и тогда человеку нужно объяснение, а не пустой
    список. Окно плагина от этого не зависит — без звука оно открывается.
    """
    pb = pedalboard()
    try:
        names = [str(n) for n in pb.io.AudioStream.output_device_names]
        default = str(pb.io.AudioStream.default_output_device_name or "")
    except Exception as e:                    # noqa: BLE001 — причину показываем фронту
        raise ReelsiError(umsg("voicefx_devices_failed",
                               f"Список устройств вывода не получен: {e}", err=str(e)))
    return {"devices": names, "default": default}


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа процесса: `--devices`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0].lstrip("-") != "devices":
        raise ReelsiError("Запуск: python -m core.voicefx_audio --devices")
    print(json.dumps(devices(), ensure_ascii=False), flush=True)
    return 0


def _fail(e: BaseException) -> NoReturn:
    """Ошибка процесса наружу: JSON-объектом с кодом, а не строкой в stderr.

    Не `core.umsg.cli_error`: тот печатает текст в stderr и теряет КОД перевода —
    родитель ждёт его из stdout (`voicefx_proc.fail`). Контракт тот же:
    `except ReelsiError` на точке входа, сообщение пользователю и код 1.
    """
    voicefx_proc.fail(e)


if __name__ == "__main__":
    # Сбор списка устройств поднимает аудиосистему JUCE, а её нативный код умеет
    # падать: окна ошибок Windows гасим ЗАРАНЕЕ (см. core/voicefx_proc.py).
    voicefx_proc.no_error_windows()
    voicefx_proc.utf8_stdout()
    try:
        sys.exit(main())
    except ReelsiError as e:
        _fail(e)
