# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Снятие фона на видеокарте: onnxruntime-gpu из настроек, в свою папку.

Зачем. BiRefNet на видеокарте (~0.3 с на картинку вместо 8–10 с на процессоре) нужен
пакет `onnxruntime-gpu`, а системный `onnxruntime` — тот, что тянет rembg, — с ним не
уживается. Раньше переход делали руками: `pip uninstall onnxruntime && pip install
onnxruntime-gpu`. Теперь это кнопка в настройках: колесо `onnxruntime-gpu` той же версии
ставится в ОТДЕЛЬНУЮ папку, и эта папка встаёт в `sys.path` ПЕРЕД системным пакетом.

Системный пакет не трогаем вовсе. На Windows сервер держит загруженные DLL onnxruntime,
и `pip uninstall` из работающего процесса падает: файлы заняты. Своя папка этой
проблемы не имеет.

Почему путь вставляется ДО импорта. Питон берёт первый найденный `onnxruntime` и держит
его на весь процесс. Если системный уже загружен (`sys.modules`), подменить его нельзя —
нужен перезапуск сервера, и `status()` это показывает (`loaded_cpu`).

Почему статус не импортирует onnxruntime. Импорт зацементировал бы системную сборку в
процессе, и кнопка «Скачать» перестала бы что-либо менять. Поэтому `cuda` спрашивается
только у уже загруженного модуля.

Что ставим. Колесо берёт pip с `--no-deps`: зависимости onnxruntime (numpy, protobuf и
прочее) уже стоят в системе, второй их копии не нужно. CUDA и cuDNN DLL приходят из
`torch/lib` (это делает `core/rembg_run.py:_cuda_dll_dirs`), сами колёса их не несут.
"""
from __future__ import annotations

import importlib.metadata
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from core.app_meta import child_env, env
from core.jobstate import kill_tree, pump_stdout, task_popen_kwargs
from core.umsg import ReelsiError, umsg

# Папка пакета. Переменная — как у шумодава (core/voicefx_sep.py): изолированный профиль
# задаёт свою, иначе кнопка в тестовом профиле ставила бы колесо в боевую папку.
DIR: str = env("ORT_GPU") or os.path.join(os.path.expanduser("~"), ".reelsi", "ort_gpu")

# Потолок на pip: колесо весит ~250 МБ, зависший загрузчик не должен держать установку вечно.
PIP_TIMEOUT = 1800.0

# Хвост лога: человеку нужен ход работы, а не весь вывод pip. В ответ API уходит короче —
# панель опрашивает раз в секунду и показывает только последнюю строку.
LOG_KEEP = 200
LOG_SHOWN = 30

# Состояние установки (одно на процесс) и замок к нему. Общий лок джоба здесь НЕ берём:
# это загрузка по сети, видеокарта в ней не занята, и нарезке она не мешает.
JOB: dict[str, Any] = {"running": False, "done": False, "step": "", "error": "", "log": []}
LOCK = threading.Lock()


# Дистрибутивы, которыми может стоять модуль `onnxruntime`. Порядок — приоритет: первый
# найденный даёт версию. Смотрим ДИСТРИБУТИВ, а не модуль: у onnxruntime-gpu модуль тот же,
# что у onnxruntime, а имя пакета (по инструкции владельца — `onnxruntime-gpu`) другое.
ORT_DISTS: tuple[str, ...] = ("onnxruntime", "onnxruntime-gpu", "onnxruntime-directml",
                              "onnxruntime-openvino")
GPU_DIST = "onnxruntime-gpu"


def _dist_version(name: str) -> str | None:
    """Версия дистрибутива из метаданных, без импорта. Не установлен — None."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def system_dist() -> tuple[str, str] | None:
    """Какой дистрибутив даёт модуль onnxruntime: (имя, версия) первого найденного из ORT_DISTS.

    Без импорта модуля: импорт зацементировал бы системную сборку в процессе (см. status).
    Ничего из списка не стоит — None.
    """
    for name in ORT_DISTS:
        ver = _dist_version(name)
        if ver is not None:
            return name, ver
    return None


def ort_version() -> str | None:
    """Версия системного onnxruntime (любого из ORT_DISTS). Пакета нет — None."""
    d = system_dist()
    return d[1] if d else None


def system_gpu() -> bool:
    """Системный дистрибутив — onnxruntime-gpu: видеокарта уже работает, своя копия не нужна."""
    return _dist_version(GPU_DIST) is not None


def installed() -> bool:
    """В папке лежит распакованный пакет onnxruntime (признак — его `__init__.py`)."""
    return os.path.isfile(os.path.join(DIR, "onnxruntime", "__init__.py"))


def _norm(path: str) -> str:
    """Путь для сравнения: абсолютный, с нормализованным регистром и разделителями."""
    return os.path.normcase(os.path.abspath(path))


def _in_path() -> bool:
    """Папка пакета уже стоит в `sys.path`."""
    return any(_norm(p) == _norm(DIR) for p in sys.path if p)


def _from_dir(mod: Any) -> bool:
    """Загруженный модуль onnxruntime взят ИЗ нашей папки (а не из системы)."""
    f = getattr(mod, "__file__", None) or ""
    return bool(f) and _norm(f).startswith(_norm(DIR) + os.sep)


def _drop_from_path() -> None:
    """Убрать папку пакета из `sys.path`: системный onnxruntime-gpu главнее и своей копии."""
    sys.path[:] = [p for p in sys.path if not (p and _norm(p) == _norm(DIR))]


def activate() -> bool:
    """Поставить папку пакета в `sys.path` ПЕРЕД системными пакетами. True — папка в пути.

    Ничего не импортирует. Вызывать ДО первого импорта onnxruntime (`core/insertlib.py`
    и `doctor.py`): позже он уже загружен из системы, и путь ничего не даст. Повторный
    вызов не дублирует запись.

    С системным onnxruntime-gpu папку не ставим: своя копия не нужна, а системный пакет
    главнее. Если папка всё же в пути (осталась от прежней установки), убираем её оттуда.
    """
    if system_gpu():
        _drop_from_path()
        return False
    if _in_path():
        return True
    if not installed() or "onnxruntime" in sys.modules:
        return False
    sys.path.insert(0, DIR)
    return True


def gpu_present() -> bool:
    """Видеокарта с CUDA видна torch. Нет torch или драйвера — False, а не ошибка."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — для нас это одно и то же: видеокарты нет
        return False


def status() -> dict[str, Any]:
    """Что стоит и что загружено в процессе. Ничего не импортирует: см. модуль.

    Первым делом — `activate()`: пакет, поставленный кнопкой, встаёт в путь сразу, без
    перезапуска сервера, и статус говорит правду (`active`). Если системный onnxruntime уже
    загружен, `activate()` ничего не вставит, и `loaded_cpu` покажет, что нужен перезапуск.

    cuda — None, пока onnxruntime в процессе не загружен: спрашивать его сейчас значило бы
    загрузить системную сборку и зацементировать её до перезапуска.
    """
    activate()
    mod = sys.modules.get("onnxruntime")
    cuda: bool | None = None
    if mod is not None:
        try:
            cuda = "CUDAExecutionProvider" in mod.get_available_providers()
        except Exception:  # noqa: BLE001 — сломанный пакет считаем без видеокарты
            cuda = False
    return {"installed": installed(), "active": _in_path(),
            "loaded_cpu": mod is not None and not _from_dir(mod),
            "cuda": cuda, "gpu": gpu_present(), "version": ort_version(), "dir": DIR,
            "system_gpu": system_gpu()}


# --------------------------------------------------------------------------- #
# Установка: фоновое задание с ходом работы в логе
# --------------------------------------------------------------------------- #
def _emit(line: str) -> None:
    """Строка хода установки в лог задания (и наша, и вывод pip — пишем как есть)."""
    with LOCK:
        log: list[str] = JOB["log"]
        log.append(line)
        del log[:-LOG_KEEP]


def _fail(e: BaseException) -> None:
    """Установка не вышла: причина — в состояние задания (её читает панель настроек)."""
    text = str(e) if isinstance(e, ReelsiError) else "%s: %s" % (type(e).__name__, e)
    with LOCK:
        JOB.update(running=False, done=True, step="fail", error=text)
    _emit("! установка не вышла: " + text)


def start_install() -> bool:
    """Поставить onnxruntime-gpu фоном. False — установка уже идёт. Отказ — ReelsiError.

    Отказы проверяем ДО потока, чтобы причина дошла до кнопки сразу, а не строкой в логе:
    системный onnxruntime-gpu уже стоит (качать нечего, pip не зовём), без системного
    onnxruntime не по чему подобрать версию (и фон не снимается вовсе), без видеокарты
    CUDA колесо бесполезно.
    """
    if system_gpu():
        raise ReelsiError(umsg("ort_gpu_system",
                               "уже стоит системный onnxruntime-gpu — качать не нужно"))
    ver = ort_version()
    if ver is None:
        raise ReelsiError(umsg("ort_gpu_no_ort",
                               "нет пакета onnxruntime: версию для onnxruntime-gpu берём по нему, "
                               "а без него фон не снимается вовсе"))
    if not gpu_present():
        raise ReelsiError(umsg("ort_gpu_no_gpu",
                               "нет видеокарты NVIDIA с CUDA — onnxruntime-gpu на ней не запустится"))
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, done=False, step="pip", error="", log=[])
    threading.Thread(target=_install, args=(ver,), name="ort-gpu-install", daemon=True).start()
    return True


def _install(ver: str) -> None:
    """Колесо в `DIR.part`, потом подмена папки. Ошибка — в состояние задания.

    Недокачанная папка никогда не активируется: путь в `sys.path` встаёт только для
    `DIR`, а `DIR.part` переименовывается в неё целиком после успешного pip."""
    part = DIR + ".part"
    try:
        shutil.rmtree(part, ignore_errors=True)          # остаток прошлой неудачной попытки
        _emit("ставлю onnxruntime-gpu==%s в свою папку: %s" % (ver, DIR))
        _run_pip([sys.executable, "-m", "pip", "install", "--no-deps", "--target", part,
                  "onnxruntime-gpu==" + ver])
        if not os.path.isfile(os.path.join(part, "onnxruntime", "__init__.py")):
            raise ReelsiError(umsg("ort_gpu_install_failed",
                                   "pip отработал, но пакета onnxruntime в папке нет",
                                   err="pip installed no onnxruntime package"))
        _swap(part)
    except Exception as e:  # noqa: BLE001 — установка не повод ронять поток молча
        shutil.rmtree(part, ignore_errors=True)
        _fail(e)
        return
    with LOCK:
        JOB.update(running=False, done=True, step="done", error="")
    _emit("готово: onnxruntime-gpu стоит в своей папке")


def _swap(part: str) -> None:
    """Новая папка на место старой. Старую сначала ПЕРЕИМЕНОВЫВАЕМ, а не удаляем.

    Сервер, который держит старый пакет, держит и его DLL: переименование такой папки на
    Windows падает ДО того, как что-то удалено, и старая копия остаётся целой. Удаление
    на полпути оставило бы обрубок с `__init__.py`, который activate() принял бы за
    готовый пакет и который ломал бы rembg целиком, включая процессор."""
    old = DIR + ".old"
    shutil.rmtree(old, ignore_errors=True)
    moved = os.path.isdir(DIR)
    try:
        if moved:
            os.replace(DIR, old)
    except OSError as e:
        raise ReelsiError(umsg("ort_gpu_install_failed",
                               "старая копия занята (сервер её держит): перезапустите сервер "
                               "и повторите установку (%s)" % e, err=str(e)))
    try:
        os.replace(part, DIR)
    except OSError as e:
        if moved:
            os.replace(old, DIR)                         # вернуть прежнюю копию на место
        raise ReelsiError(umsg("ort_gpu_install_failed",
                               "не переставить папку %s (%s)" % (DIR, e), err=str(e)))
    shutil.rmtree(old, ignore_errors=True)


def _run_pip(cmd: list[str]) -> None:
    """pip дочерним процессом; его вывод едет в лог задания построчно.

    Читаем фоновым потоком (`jobstate.pump_stdout`) с таймаутом на каждой строке: pip
    качает минутами, и блокирующее чтение не дало бы ни показать ход, ни снять зависший
    процесс. Снятый процесс гасим вместе с деревом (`kill_tree`)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             env=child_env(), **task_popen_kwargs())
    except OSError as e:
        raise ReelsiError(umsg("ort_gpu_install_failed", "pip не запустился (%s)" % e, err=str(e)))
    q = pump_stdout(p)
    deadline = time.time() + PIP_TIMEOUT
    try:
        while True:
            if time.time() > deadline:
                raise ReelsiError(umsg("ort_gpu_install_failed",
                                       "pip: превышен таймаут (%d с)" % int(PIP_TIMEOUT),
                                       err="pip timed out"))
            try:
                line = q.get(timeout=1.0)
            except queue.Empty:
                continue
            if line is None:                             # EOF stdout: pip закончил вывод
                break
            if line.strip():
                _emit(line.rstrip())
        p.wait(timeout=60)
    finally:
        # Один выход на все случаи: висящий pip держал бы папку занятой для следующей попытки.
        if p.poll() is None:
            kill_tree(p)
            try:
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001 — процесс уже мёртв или не убивается
                pass
    if p.returncode != 0:
        raise ReelsiError(umsg("ort_gpu_install_failed",
                               "pip: код возврата %s" % p.returncode,
                               err="pip exit code %s" % p.returncode))


def job_status() -> dict[str, Any]:
    """Ход установки для панели: шаг, причина отказа и хвост лога."""
    with LOCK:
        return {"running": bool(JOB["running"]), "done": bool(JOB["done"]),
                "step": str(JOB["step"]), "error": str(JOB["error"]),
                "log": list(JOB["log"][-LOG_SHOWN:])}
