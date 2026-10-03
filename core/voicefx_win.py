# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно чужого VST3-плагина поверх всех: поиск по PID, TOPMOST, центр рабочей области.

Зачем отдельный модуль. Окно плагина рисует САМ плагин (JUCE), и в его процесс мы
не вмешиваемся — управлять можно только окном снаружи, средствами WinAPI. Ровно
это и делает модуль: находит окна верхнего уровня, принадлежащие нашему процессу
окна (`core/voicefx_editor`), поднимает их над остальными окнами, ставит по центру
рабочей области монитора и выводит на передний план. Без этого владелец видел
ровно то, на что жаловался: окно открывалось ЗА браузером, без доступного крестика
и кнопки «свернуть» — то есть «настройки не открылись».

Почему окно оказывается в фоне. Процесс окна запускает сервер (Flask), а на
Windows право вывести окно на передний план есть только у того, кто на переднем
плане СЕЙЧАС. Поэтому перед запуском сервер разрешает РЕБЁНКУ забрать фокус
(`allow_foreground`) — системная дверь `AllowSetForegroundWindow` по PID.

Почему по PID, а не по заголовку. Заголовок окна плагина пишет сам плагин: он
бывает пустым, локализованным или меняется по ходу загрузки, и «найти окно по
имени» — это угадывание. PID — единственный признак, который принадлежит нам.

Что важно для читателя этого файла:

* функции WinAPI не вызываются напрямую, а берутся у подменяемого объекта
  (`cached()`): без этого окно нельзя было бы проверить тестом — настоящий
  `SetWindowPos` поднял бы окно поверх терминала;
* на не-Windows модуль не делает НИЧЕГО и отвечает «окна нет»: там своя оконная
  система, и окно плагина открывается активным само;
* поиск окна — ожидание с потолком (`timeout`): окно появляется не мгновенно
  (плагин грузится секундами), но ждать его вечно нельзя — процесс окна умеет
  умереть на загрузке, и тогда никто бы не пришёл.
"""
from __future__ import annotations
import ctypes
import os
import sys
import time
from typing import Any, Sequence

# Константы WinAPI. Значения — из winuser.h; в typeshed их нет (модуль windll
# объявлен только для Windows-сборок), поэтому объявлены здесь числами.
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001                 # размер окна не трогаем: его выбрал плагин
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400
# «Поверх всех» и обратно: HWND_TOPMOST = -1, HWND_NOTOPMOST = -2 (знаковые числа).
SWP_TOPMOST = SWP_SHOWWINDOW | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING
SWP_UNTOPMOST = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER \
    | SWP_NOSENDCHANGING

HWND_TOPMOST = ctypes.c_void_p(-1)  # см. WinApi.set_pos: только указателем

MONITOR_DEFAULTTONEAREST = 2
# Зазор от краёв рабочей области: окно плагина не должно липнуть к панели задач.
MARGIN = 8
# Потолок ожидания окна: плагин грузится секундами, но не минутами.
WAIT_TIMEOUT = 20.0
WAIT_POLL = 0.05


class _RECT(ctypes.Structure):
    """Прямоугольник WinAPI (winuser.h): left/top/right/bottom."""

    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    """Сведения о мониторе WinAPI: размер структуры, границы и рабочая область."""

    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


class WinApi:
    """Тонкая обёртка над user32/kernel32 — и ЕДИНСТВЕННАЯ точка их вызова.

    Подменяется целиком: тест подставляет объект с теми же методами и проверяет,
    ЧТО мы попросили у системы, не открывая ни одного окна.
    """

    def __init__(self) -> None:
        # windll есть только в Windows-сборках ctypes; getattr — потому что mypy
        # проверяет и linux-платформу, а прямого атрибута там нет в типах
        # (та же причина, что в core/aerender.py и core/voicefx_proc.py).
        windll = getattr(ctypes, "windll", None)
        self.user32: Any = None if windll is None else windll.user32
        self.kernel32: Any = None if windll is None else windll.kernel32

    def process_id(self, hwnd: int) -> int:
        """PID процесса, которому принадлежит окно (0 — не вышло)."""
        pid = ctypes.c_ulong(0)
        self.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def windows_of_process(self, pid: int) -> list[int]:
        """Окна ВЕРХНЕГО УРОВНЯ этого PID: видимые, без владельца и без TOOLWINDOW.

        Отсев нарочно строгий. Окно с владельцем (`GetWindow(hwnd, GW_OWNER)`) —
        это всплывающие подсказки и служебные панели плагина: поднимать их поверх
        рабочего стола нельзя. `WS_EX_TOOLWINDOW` — палитры и невидимые окна
        каркаса JUCE. Остаются именно те окна, которые человек видит в панели
        задач и закрывает крестиком.
        """
        found: list[int] = []
        c_enum = getattr(ctypes, "WINFUNCTYPE", None)
        if c_enum is None:                          # урезанный ctypes — окон не найти
            return found
        enum_proc = c_enum(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32 = self.user32

        def _cb(hwnd: Any, _lparam: Any) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.GetWindow(hwnd, 4):       # GW_OWNER=4: окно с владельцем
                    return True
                ex = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
                if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
                    return True
                if self.process_id(int(hwnd)) == pid:
                    found.append(int(hwnd))
            except Exception:                       # noqa: BLE001 — окно умерло в переборе
                pass  # окно исчезло между перебором и запросом — пропускаем его
            return True

        user32.EnumWindows(enum_proc(_cb), 0)
        return found

    def process_exe(self, pid: int) -> str:
        """Путь .exe процесса ("" — не вышло). Нужен только для отсева ЧУЖИХ окон.

        По одному PID судить нельзя: систему можно переиспользовать номер, и
        «поднять поверх всех» чужое окно — это вмешательство в чужую программу
        (у нас такое правило: чужое не трогаем). Имя файла отвечает на вопрос
        «наш ли это процесс» точно.
        """
        if self.kernel32 is None:
            return ""
        query_limited = 0x1000              # PROCESS_QUERY_LIMITED_INFORMATION
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return ""
        h = windll.kernel32.OpenProcess(query_limited, False, pid)
        if not h:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                            ctypes.byref(size)):
                return ""
            return str(buf.value)
        except Exception:                           # noqa: BLE001 — чужая сборка Windows
            return ""
        finally:
            try:
                self.kernel32.CloseHandle(h)
            except Exception:                       # noqa: BLE001 — дескриптор уже закрыт
                pass  # закрывать нечего: система закрыла дескриптор сама

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """Прямоугольник окна (left, top, right, bottom) или None."""
        rect = _RECT()
        if not self.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def monitor_work_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """Рабочая область монитора, на котором стоит окно (без панели задач)."""
        mon = self.user32.MonitorFromWindow(ctypes.c_void_p(hwnd),
                                            MONITOR_DEFAULTTONEAREST)
        if not mon:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not self.user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(info)):
            return None
        r = info.rcWork
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))

    def set_pos(self, hwnd: int, x: int, y: int, cx: int, cy: int, flags: int) -> bool:
        """SetWindowPos: место, размер и «поверх всех» по флагам."""
        # HWND_TOPMOST — УКАЗАТЕЛЬ (-1), а не int: голое -1 ctypes передаёт 32-битным
        # int, в 64-битном процессе старшая половина регистра — мусор, и SetWindowPos
        # молча отвечает 0. Замер приёмки VW: окно осталось на (-3,-26) — заголовок с
        # крестиком за верхним краем экрана и НЕ поверх; c_void_p(-1) — встало в центр.
        ok = self.user32.SetWindowPos(ctypes.c_void_p(hwnd), HWND_TOPMOST,
                                      x, y, cx, cy, flags)
        return bool(ok)

    def allow_foreground(self, pid: int) -> None:
        """Разрешить ЭТОМУ процессу забрать передний план (ASFW_ANY = -1)."""
        self.user32.AllowSetForegroundWindow(pid)

    def bring_to_front(self, hwnd: int) -> None:
        """Вывести окно на передний план и дать ему фокус.

        ShowWindow поднимает уже TOPMOST-окно; SetForegroundWindow даёт фокус
        (клавиатура идёт в плагин). Система вправе в фокусе отказать — окно всё
        равно остаётся поверх остальных: TOPMOST ей для этого не нужен.
        """
        sw_restore = 9
        self.user32.ShowWindow(ctypes.c_void_p(hwnd), sw_restore)
        self.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))


# Кеш обёртки: ctypes-объекты берутся один раз. `None` — не Windows или WinAPI
# недоступна: дальше по коду это просто «окна нет», и ничего не делается.
_API: WinApi | None = None
_API_READY = False


def cached() -> WinApi | None:
    """Обёртка WinAPI или None (не Windows, нет ctypes). Подменяется в тестах."""
    global _API, _API_READY
    if not _API_READY:
        _API_READY = True
        if sys.platform == "win32":
            try:
                _API = WinApi()
            except Exception:                   # noqa: BLE001 — WinAPI не поднялась
                _API = None
    return _API


def is_own_process(exe: str) -> bool:
    """Окна процесса — наши? Сверка имени файла с нашим рабочим Python.

    Имя не отдала система (нет прав, не Windows) — считаем своими: процесс
    запустили МЫ, и не поднять его окно значило бы оставить его в фоне.

    Сверяем и с путём рабочего интерпретатора (`app_meta.py_exec`), и с именем
    `python*`: интерпретатор бывает любым (venv, `python3.10`), а чужой процесс
    под нашим номером — не наш.
    """
    if not exe:
        return True
    base = exe.replace("\\", "/").rsplit("/", 1)[-1].lower()
    try:
        from core.app_meta import py_exec
        own = os.path.basename(py_exec()).lower()
    except Exception:                           # noqa: BLE001 — нет окружения, не беда
        own = ""
    if own and base == own:
        return True
    return base.startswith("python") and base.endswith(".exe")


def place_windows(windows: Sequence[int], *, api: WinApi, topmost: bool = True,
                  margin: int = MARGIN) -> dict[str, Any]:
    """Поднять окна поверх всех и поставить по центру рабочей области монитора.

    Порядок для каждого окна один и тот же (и он важен): сначала место и размер по
    центру рабочей области, и только потом «поверх всех». Наоборот было бы видно
    на экране: окно уже поверх, но ещё прыгает в центр.

    Заголовок держим в пределах экрана: окно выше рабочей области — верхнюю
    границу прижимаем к её началу, иначе строка заголовка с крестиком уехала бы за
    верхний край, и закрыть окно мышкой стало бы нельзя (ровно та жалоба, из-за
    которой это и делается).

    Первое окно выводится на передний план — фокус отдаётся ему. Остальным
    (у плагина бывают свои панели) — только TOPMOST и центр: перебрасывать фокус
    между окнами одного плагина значит мигать ими на глазах у человека.
    """
    done: list[dict[str, Any]] = []
    for i, hwnd in enumerate(windows):
        rect = api.window_rect(hwnd)
        work = api.monitor_work_rect(hwnd)
        if rect is None or work is None:
            continue
        left, top, right, bottom = rect
        wl, wt, wr, wb = work
        w, h = max(1, right - left), max(1, bottom - top)
        x = wl + max(0, (wr - wl - w) // 2)
        y = wt + max(0, (wb - wt - h) // 2)
        # Зажим по рабочей области: окно выше/шире её — прижимаем к левому верхнему
        # углу с зазором, а не выгоняем заголовок за экран.
        x = max(wl + margin, min(x, max(wl + margin, wr - margin - w)))
        y = max(wt + margin, min(y, max(wt + margin, wb - margin - h)))
        flags = (SWP_TOPMOST if topmost else SWP_UNTOPMOST) | SWP_NOOWNERZORDER \
            | SWP_NOSENDCHANGING
        api.set_pos(hwnd, x, y, w, h, flags)
        done.append({"hwnd": hwnd, "rect": [x, y, w, h], "topmost": bool(topmost)})
        if i == 0 and topmost:
            api.bring_to_front(hwnd)
    return {"windows": done}


def windows_of(pid: int, *, api: WinApi) -> list[int]:
    """Наши окна этого процесса: [] — чужие или их ещё нет."""
    if not is_own_process(api.process_exe(pid)):
        return []
    return api.windows_of_process(pid)


def wait_for_windows(pid: int, *, api: WinApi, timeout: float = WAIT_TIMEOUT,
                     interval: float = WAIT_POLL) -> list[int]:
    """Ждать появления наших окон процесса; [] — не дождались за `timeout`.

    Окно плагина появляется не мгновенно (JUCE грузит библиотеку и строит GUI), а
    ждать вечно нельзя: процесс окна умеет умереть на загрузке, и тогда мы ждали бы
    его до конца времён.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        ours = windows_of(pid, api=api)
        if ours:
            return ours
        if time.monotonic() >= deadline:
            return []
        time.sleep(max(0.005, float(interval)))


def prepare_window(pid: int, *, api: WinApi | None = None,
                   timeout: float = WAIT_TIMEOUT,
                   interval: float = WAIT_POLL) -> dict[str, Any]:
    """Дождаться окна процесса, поднять его поверх и вернуть, что вышло.

    Одна дверь на весь сценарий: ждём окно, ставим TOPMOST, центрируем, выводим
    на передний план. Кто именно зовёт — сервер (перед запуском разрешив ребёнку
    фокус) или сам процесс окна, — значения не имеет: работа одна и та же, и
    второй копии этого порядка быть не должно.
    """
    api = api if api is not None else cached()
    result: dict[str, Any] = {"windows": [], "waited": 0.0}
    if api is None:
        return result
    t0 = time.monotonic()
    found = wait_for_windows(pid, api=api, timeout=timeout, interval=interval)
    result["waited"] = round(time.monotonic() - t0, 3)
    if not found:
        return result
    result.update(place_windows(found, api=api, topmost=True))
    return result


def allow_foreground(pid: int, *, api: WinApi | None = None) -> bool:
    """Разрешить процессу `pid` забрать передний план; True — система согласилась.

    Зовётся ПЕРЕД запуском процесса окна: право вывести окно на передний план
    система даёт только тому, кто на переднем плане СЕЙЧАС, и «на потом» его не
    выдают. Сервер запущен из консоли владельца, то есть сам сейчас активен, —
    разрешение проходит.

    Отказ НЕ ошибка: окно всё равно поднимем (TOPMOST), просто фокус останется у
    прежнего окна. Поэтому наружу — False, а не исключение.
    """
    api = api if api is not None else cached()
    if api is None:
        return False
    try:
        api.allow_foreground(pid)
        return True
    except Exception:                           # noqa: BLE001 — окно важнее разрешения
        return False
