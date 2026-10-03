# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общая обвязка процессов, которые грузят ЧУЖОЙ VST3-плагин.

Зачем отдельный модуль. Процессов, работающих с плагином, три — список имён
(`core/voicefx_scan`), цепочка (`core/voicefx_render`) и окно настроек
(`core/voicefx_editor`), — и у каждого своя точка входа со своим `__main__`.
Две вещи обязаны быть сделаны в КАЖДОМ из них, и обе — до того, как в процесс
попадёт нативный код плагина:

1. **Системные окна ошибок Windows выключены** (`no_error_windows`). Плагин,
   который падает на загрузке (в живом прогоне так упал один, с обращением к
   памяти, а u-he Satin показал своё окно ошибки), иначе поднимает модальное
   «Application Error»: оно висит на экране до нажатия кнопки, а процесс с ним
   не умирает. Ночью такие окна держали машину владельца. С этим режимом падение
   завершает процесс молча — и сканирование продолжается со следующего плагина.

2. **Печать не падает на кириллице.** Без консоли stdout на Windows — cp1251, и
   русская строка роняет печать `UnicodeEncodeError` у самого процесса.

Почему в `__main__`, а не на импорте: модуль импортирует и родитель
(core/voicefx.py) — за типами и общими текстами, — а гасить окна ошибок и
перенастраивать свои потоки чужому процессу незачем.

Окна, которые показывает САМ плагин (свой GUI, диалог лицензии), этим не
подавляются: их рисует плагин в своём окне, а не система. Поэтому сканирование
списка запускается только явным действием человека (открытие блока «Голос» или
кнопка «Обновить список»), а не фоном при старте сервера.
"""
from __future__ import annotations
import json
import sys
from typing import Any, NoReturn

from core.app_meta import t

# Константы WinAPI. Значения — из winbase.h; в typeshed их нет (модуль windll
# объявлен только для Windows-сборок), поэтому объявлены здесь числом.
SEM_FAILCRITICALERRORS = 0x0001     # не показывать диалог критической ошибки
SEM_NOGPFAULTERRORBOX = 0x0002      # не показывать «Application Error» (GPF)
SEM_NOOPENFILEERRORBOX = 0x8000     # не показывать диалог «файл не найден»


def _kernel32() -> Any:
    """kernel32 через ctypes; None, если её нет (не Windows или урезанный ctypes).

    Отдельной функцией, а не строкой в `no_error_windows`: `windll.kernel32` —
    свойство-загрузчик ctypes, и подменить его в тесте, не тронув саму Windows,
    можно только на этом уровне.
    """
    import ctypes
    # getattr, а не `ctypes.windll`: в typeshed windll объявлен только для
    # Windows-сборок, а mypy проверяет и linux-платформу (см. core/aerender).
    windll = getattr(ctypes, "windll", None)
    return None if windll is None else windll.kernel32


def no_error_windows() -> None:
    """Выключить системные окна ошибок Windows. На других ОС — ничего.

    Зовётся ПЕРВОЙ строкой точки входа процесса с чужим плагином: чужой код
    грузится позже, а окно «Application Error» поднимается системой в момент
    падения — до этого момента режим уже должен стоять.
    """
    if sys.platform != "win32":
        return
    try:
        k32 = _kernel32()
        if k32 is not None:
            k32.SetErrorMode(
                SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)
    except Exception:                         # noqa: BLE001 — окна ошибок не повод не работать
        pass


def utf8_stdout() -> None:
    """stdout/stderr процесса — UTF-8: без консоли Windows они cp1251.

    Общая починка точек входа (была скопирована в каждом модуле): русская строка
    в cp1251-потоке роняет печать `UnicodeEncodeError`, и процесс умирает на
    строке лога, а не на работе. `reconfigure` есть не у каждого потока —
    служебная печать не критична, поэтому сбой глушится.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            # sys.stdout в типах — TextIO, а reconfigure есть только у
            # TextIOWrapper: на практике это он и есть, но проверку типов это не
            # устраивает (та же строка в core/umsg.cli_error).
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # TextIO не знает reconfigure
        except Exception:                     # noqa: BLE001 — поток без reconfigure
            pass


def emit(line: str = "", /, **vars: Any) -> None:
    """Строка хода работы в stdout — JSON-объектом, как ждёт родитель.

    Не текстом: родитель разбирает вывод построчно, и посторонняя строка
    (предупреждение numpy при импорте пакета) не должна ломать разбор. Перевод и
    подстановка переменных — общим `app_meta.t`: текст уезжает родителю уже
    готовым и попадает в лог той же строкой, что и у сервера.
    """
    if line:
        print(json.dumps({"msg": t(line, **vars)}, ensure_ascii=False), flush=True)


def fail(e: BaseException) -> NoReturn:
    """Ошибка процесса: JSON-строкой в stdout — чтобы родитель узнал КОД.

    `core.umsg.cli_error` печатает только текст: код перевода (`vst_unavailable`)
    потерялся бы, и англоязычный пользователь увидел бы русское сообщение (та же
    причина, по которой коды объявлены в api/voicefx.py).
    """
    print(json.dumps({"error": getattr(e, "code", None) or "", "text": str(e)},
                     ensure_ascii=False), flush=True)
    raise SystemExit(1)
