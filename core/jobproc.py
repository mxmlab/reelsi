# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож внешнего процесса задания: запуск, чтение вывода, простой, отмена, убийство дерева.

Общая механика `yt-dlp` (api/podcast.py) и `rclone` (api/gdrive.py): раньше две копии
разъезжались — в одной сторож умел то, чего не умела другая. Здесь один цикл; разбор
строк вывода остаётся у вызывающего и приходит колбэком `on_line`.

Устройство (и почему так):
* вывод читает ФОНОВЫЙ поток, главный опрашивает процесс. Пока главный сидит в
  `for line in p.stdout`, он не заметит ни простоя, ни отмены — зависшая сеть держала
  задание до перезапуска сервера;
* простой — это отсутствие ИЗМЕНЕНИЙ: `on_line` возвращает True, когда строка —
  живое событие или сдвинула прогресс. Строка статистики, повторённая по таймеру,
  «активностью» не считается (вставшая передача не выглядит живой);
* если поток чтения умер сам, активности взять неоткуда: по простою процесс НЕ снимаем —
  он может молча дописывать файл. Предупреждаем один раз (`on_dead`), снимает «Стоп»;
* исключение на одной строке ловится на САМОЙ строке и логируется: поток не умирает
  из-за непонятной строки;
* убийство — `kill_tree` (дерево), но колбэк `kill` можно подменить: тесты и
  вызывающие подменяют `_kill_proc` своего модуля, и подмена должна действовать.
"""
import os
import subprocess
import threading
import time
from typing import Any, Callable

from core.applog import get_logger
from core.jobstate import kill_tree

log = get_logger("reelsi.jobproc")

# Код возврата процесса, которого сняли сторожем простоя. Один на оба задания: вызывающий
# отличает «снято по простою» от обычного падения по этому значению.
STALLED_RC = -137

# Опрос состояния процесса и флага отмены: отмена снимает процесс не позже этого шага.
_POLL_SEC = 0.5


def run_watched(cmd: list[str], on_line: Callable[[str], bool], *,
                set_proc: Callable[[subprocess.Popen[str] | None], None],
                cancelled: Callable[[], bool],
                stall_sec: float,
                on_stall: Callable[[], None] | None = None,
                on_dead: Callable[[], None] | None = None,
                kill: Callable[[Any], None] | None = None) -> int:
    """Запустить `cmd`, читать его вывод строками и следить за ним до конца.

    `on_line(raw)` получает строку как есть (с переводом строки) и возвращает True, если
    строка — признак жизни процесса. `set_proc` получает процесс на время работы и None
    после — глобальная ссылка для «Стоп» из интерфейса. `cancelled()` — флаг отмены:
    поднят — процесс снимается. `stall_sec` — сколько секунд без активности терпим;
    `on_stall` пишет строку о простое ДО убийства. Возвращает код процесса или
    `STALLED_RC`, если сняли по простою.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1,
                         creationflags=flags, env=env)
    kill_fn: Callable[[Any], None] = kill if kill is not None else kill_tree
    set_proc(p)
    if cancelled():                    # «Стоп» нажали, пока процесс запускался
        kill_fn(p)

    # Время последней ПРИЗНАКА жизни (см. докстроку модуля): от него считаем простой.
    activity = [time.time()]
    died = [False]

    stdout = p.stdout

    def _pump() -> None:
        try:
            if stdout is None:
                return
            for raw in stdout:
                try:
                    if on_line(raw):
                        activity[0] = time.time()
                except Exception as e:  # одна непонятная строка не должна убить чтение
                    log.warning("строка вывода процесса не разобрана: %s: %s",
                                type(e).__name__, e)
        except Exception as e:          # умерло само чтение потока
            died[0] = True
            log.warning("поток чтения вывода процесса упал: %s: %s", type(e).__name__, e)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()

    stalled = False
    warned_dead = False
    try:
        while p.poll() is None:
            if died[0] and not warned_dead:
                warned_dead = True
                if on_dead is not None:
                    on_dead()
            if not warned_dead and time.time() - activity[0] >= stall_sec:
                if on_stall is not None:
                    on_stall()
                kill_fn(p)
                stalled = True
                break
            if cancelled():
                kill_fn(p)
                break
            time.sleep(_POLL_SEC)
        rc = p.wait()
        # Хвост вывода ещё дочитываем, но не бесконечно: насос — демон и джоб не держит.
        pump.join(timeout=10.0)
    finally:
        set_proc(None)
    return STALLED_RC if stalled else rc
