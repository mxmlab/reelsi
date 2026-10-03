# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шумодав RoFormer: своё окружение, свои модели, установка и команда запуска.

Зачем отдельный движок. DeepFilterNet (core/voicefx.py) чистит паузы, а шорох
одежды поверх речи не берёт. Владелец сравнил на своём голосе шесть вариантов и
выбрал mel-band RoFormer: пакет `audio-separator` 0.47.0 (MIT) с моделями aufr33.
Это ЧУЖОЙ тяжёлый код — torch и onnxruntime-gpu, модель ~0.9 ГБ в видеопамяти,
десятки секунд счёта, — поэтому он не ставится в системный Python (там numpy 2.2.6
и пакеты проекта: их трогать нельзя) и не грузится в процесс сервера. Для него
заводится СВОЁ окружение, а считает он ДОЧЕРНИМ процессом, который завершается
вместе со своими потоками и видеопамятью (как VST, см. core/voicefx_render.py):

    <окружение>/Scripts/audio-separator.exe ВХОД.wav -m <модель> \\
        --model_file_dir <окружение>/models --output_dir ВЫХОД \\
        --output_format WAV --single_stem Dry

Окружение — venv с `--system-site-packages`: системный torch/CUDA берётся как есть
(своя сборка torch весила бы ещё гигабайты и разошлась бы с драйвером), а
audio-separator и его зависимости живут отдельно и системный Python не трогают.
Путь окружения — `REELSI_VOICE_SEP` (изолированный профиль задаёт свой, см.
tools/webui_test.py), по умолчанию `~/.reelsi/voice_sep`.

Установка — фоновое задание этого модуля: venv, `pip install "audio-separator[gpu]"`,
скачивание моделей. В сеть ходит ТОЛЬКО дочерний процесс (pip и сам
audio-separator): здесь нет ни urllib, ни requests, поэтому «тесты установщика не
ходят в сеть» — свойство кода, а не договорённость.

Модели по движкам (`voice_fx.denoise.engine`):

    roformer       denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt      — мягкий
    roformer_aggr  denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt — жёсткий

Замер владельца (20 с звука, петличка): ~12 с вместе с загрузкой модели, пик
видеопамяти ~3.4 ГБ, выход 44.1 кГц стерео, сдвига по времени нет, длина та же.
"""
from __future__ import annotations

import math
import os
import queue
import subprocess
import threading
import time
from typing import Any

from core.app_meta import child_env, env, py_exec
from core.jobstate import kill_tree, pump_stdout, task_popen_kwargs
from core.umsg import ReelsiError, umsg

# Движки, которые умеет этот модуль. У deep-filter свой код в core/voicefx.py, и
# сюда он не входит: здесь всё, что живёт в чужом окружении.
ENGINES: tuple[str, ...] = ("roformer", "roformer_aggr")

# Имена файлов моделей — те самые, на которых владелец выбирал движок. Обе лежат в
# списке моделей audio-separator (models.json), поэтому качает их он сам, своими
# адресами: хардкодить ссылки здесь значило бы сломаться от смены хостинга.
MODELS: dict[str, str] = {
    "roformer": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
    "roformer_aggr": "denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt",
}

# Версия пакета — та, на которой владелец сравнивал движки. Прибита гвоздём: у
# чужого пакета смена версии меняет и поведение, и набор зависимостей.
SEP_VERSION = "0.47.0"
SEP_EXTRA = "gpu"                       # audio-separator[gpu]: onnxruntime-gpu к системному torch

# Своё окружение. Переменная — как у остальных личных папок: изолированный профиль
# задаёт свою, иначе кнопка «Установить» в тестовом профиле ставила бы окружение в
# боевой каталог, а он рабочий.
ENV_DIR: str = env("VOICE_SEP") or os.path.join(os.path.expanduser("~"), ".reelsi", "voice_sep")

# Размер загрузки для кнопки установки. Файлы моделей замерены на живом диске
# (913 097 300 байт .ckpt и 1 621 байт конфига рядом), колёса окружения — по
# установленному окружению владельца (0.45 ГБ на диске, загрузка — сжатые колёса).
MODEL_BYTES = 913_097_300
MODEL_CONFIG_BYTES = 1_621
ENV_DOWNLOAD_BYTES = 360 * 1024 * 1024

# Потолки на шаги установки. Загрузка идёт минутами (модель ~0.9 ГБ), но зависший
# pip или оборванная загрузка не должны держать установщик вечно: шаг снимается по
# PID вместе с деревом (jobstate.kill_tree).
VENV_TIMEOUT = 600.0
PIP_TIMEOUT = 3600.0
MODEL_TIMEOUT = 3600.0

# Хвост лога установки: человеку нужен ход работы, а не весь вывод pip на тысячу
# строк (его смотреть негде).
LOG_KEEP = 200

# Состояние установщика (одно на процесс) и замок к нему. Второй установщик в тот
# же каталог не поднимаем: два pip в один venv оставляют битое окружение.
JOB: dict[str, Any] = {"running": False, "done": False, "i": 0, "n": 0, "step": "",
                       "cur": "", "pct": 0, "error": "", "log": []}
LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Пути: окружение, исполняемые файлы, модели
# --------------------------------------------------------------------------- #
def _bin_dir() -> str:
    """Папка исполняемых файлов venv: Scripts на Windows, bin на остальных."""
    return os.path.join(ENV_DIR, "Scripts" if os.name == "nt" else "bin")


def _exe(name: str) -> str:
    """Путь к программе внутри venv (на Windows — с .exe)."""
    return os.path.join(_bin_dir(), name + (".exe" if os.name == "nt" else ""))


def env_python() -> str:
    """Питон СВОЕГО окружения: им ставится pip и им же запускается всё остальное."""
    return _exe("python")


def separator_exe() -> str:
    """Консольный скрипт audio-separator этого окружения (лаунчер его же питона)."""
    return _exe("audio-separator")


def env_ready() -> bool:
    """Окружение стоит: есть и питон, и сам audio-separator."""
    return os.path.isfile(env_python()) and os.path.isfile(separator_exe())


def model_dir() -> str:
    """Папка моделей внутри окружения — её же получает `--model_file_dir`."""
    return os.path.join(ENV_DIR, "models")


def model_path(engine: str) -> str:
    """Путь файла модели движка (пустая строка — движок не наш)."""
    name = MODELS.get(engine)
    return os.path.join(model_dir(), name) if name else ""


def model_config(engine: str) -> str:
    """Имя конфига рядом с моделью: из него audio-separator берёт параметры модели."""
    name = MODELS.get(engine, "")
    return (os.path.splitext(name)[0] + "_config.yaml") if name else ""


def model_ready(engine: str) -> bool:
    """Файлы модели на месте и не обрезаны.

    Обрезанную загрузку (`< MODEL_BYTES`) считаем отсутствующей: audio-separator
    пропускает уже существующий файл, и недокачанные 900 МБ всплыли бы потом
    ошибкой загрузки модели — «модель есть, а не работает».
    """
    name = MODELS.get(engine)
    if not name:
        return False
    d = model_dir()
    config = os.path.join(d, model_config(engine))
    try:
        return (os.path.getsize(os.path.join(d, name)) >= MODEL_BYTES
                and os.path.isfile(config))
    except OSError:
        return False


def installed(engine: str) -> bool:
    """Движок готов к работе: и окружение, и файлы модели."""
    return env_ready() and model_ready(engine)


def download_bytes() -> int:
    """Сколько качать при установке: точные размеры моделей плюс колёса окружения."""
    return len(ENGINES) * (MODEL_BYTES + MODEL_CONFIG_BYTES) + ENV_DOWNLOAD_BYTES


def size_text() -> str:
    """«~2.1 ГБ» — размер загрузки для кнопки установки (никогда не занижаем)."""
    gb = math.ceil(download_bytes() / (1024 ** 3) * 10) / 10
    return "~%.1f ГБ" % gb


def stamp(engine: str) -> str:
    """Отпечаток модели для ключа кеша: движок, файл, его размер и mtime.

    Модель — часть настройки: подменили файл (скачали другую версию) — запечённый
    трек и окна прослушивания посчитаны ДРУГИМ шумодавом, и отдавать их из кеша
    нельзя. Проба дешёвая (os.stat), а без файла остаётся хотя бы имя: у
    неустановленной модели отпечатки движков всё равно разные.
    """
    name = MODELS.get(engine)
    if not name:
        return ""
    try:
        st = os.stat(os.path.join(model_dir(), name))
    except OSError:
        return "model=" + name
    return "model=%s:%d:%.0f" % (name, st.st_size, st.st_mtime)


def require(engine: str) -> None:
    """Окружение и модель движка на месте? Нет — понятная ошибка с размером загрузки.

    Папка, имя модели и размер подставлены в РУССКИЙ текст сразу, и рядом едут те
    же переменные: в русском интерфейсе фронт показывает текст как есть (словарь
    нужен только английскому), а по коду ERR_roformer_missing и переменным берётся
    перевод. Без подстановки человек увидел бы «~{size}» вместо размера.
    """
    size = size_text()
    if not env_ready():
        raise ReelsiError(umsg("roformer_missing",
                               f"Нет окружения RoFormer ({ENV_DIR}) — нажми "
                               f"«Скачать RoFormer ({size})» в панели голоса",
                               dir=ENV_DIR, size=size))
    if not model_ready(engine):
        model = MODELS.get(engine, engine)
        raise ReelsiError(umsg("roformer_missing",
                               f"Нет модели RoFormer ({model}) в {model_dir()} — нажми "
                               f"«Скачать RoFormer ({size})» в панели голоса",
                               model=model, dir=model_dir(), size=size))


# --------------------------------------------------------------------------- #
# Команды: запуск шумодава и установка
# --------------------------------------------------------------------------- #
def separate_cmd(in_wav: str, out_dir: str, engine: str) -> list[str]:
    """Команда дочернего процесса — ровно та, которой владелец сравнивал движки.

    `--single_stem Dry` — модель отдаёт только очищенный голос (вторая дорожка
    «шум» нам не нужна и весит столько же). `--output_format WAV` — дальше файл
    пересэмплирует и сведёт в моно ffmpeg (core/voicefx._mono48): модель отдаёт
    44.1 кГц стерео, а голос проекта живёт в 48 кГц.

    Имя выходного файла собирает сам audio-separator
    (`<вход>_(dry)_<модель>.wav`), поэтому папку вывода даём пустую и забираем из
    неё единственный WAV (`separated_wav`) — повторять чужую формулу имени здесь
    значило бы сломаться от смены версии пакета.
    """
    return [separator_exe(), in_wav, "-m", MODELS[engine],
            "--model_file_dir", model_dir(), "--output_dir", out_dir,
            "--output_format", "WAV", "--single_stem", "Dry"]


def download_cmd(engine: str) -> list[str]:
    """Команда скачивания модели тем же окружением (без входа: только загрузка)."""
    return [separator_exe(), "-m", MODELS[engine],
            "--model_file_dir", model_dir(), "--download_model_only"]


def venv_cmd() -> list[str]:
    """Создать окружение питоном проекта: venv с системными пакетами.

    `--system-site-packages` — чтобы взялся системный torch с CUDA: своя сборка
    весила бы ещё гигабайты, а на Windows ещё и разошлась бы с драйвером.
    """
    return [py_exec(), "-m", "venv", "--system-site-packages", ENV_DIR]


def pip_cmd() -> list[str]:
    """Поставить audio-separator В СВОЁ окружение.

    Питон и pip — ИЗ ОКРУЖЕНИЯ (`<env>/python.exe -m pip`), а не системные: так
    системный numpy 2.2.6 и пакеты проекта остаются ровно теми же, что были.
    `--upgrade-strategy only-if-needed` — уже подходящие системные пакеты не
    переставляются в venv лишней копией.
    """
    return [env_python(), "-m", "pip", "install", "--upgrade-strategy", "only-if-needed",
            "audio-separator[%s]==%s" % (SEP_EXTRA, SEP_VERSION)]


def separated_wav(out_dir: str) -> str:
    """Свежий WAV из папки вывода процесса (пустая строка — процесс ничего не записал)."""
    try:
        names = [n for n in os.listdir(out_dir) if n.lower().endswith(".wav")]
    except OSError:
        return ""
    best, best_m = "", -1.0
    for name in names:
        path = os.path.join(out_dir, name)
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m > best_m:
            best, best_m = path, m
    return best


# --------------------------------------------------------------------------- #
# Установка: фоновое задание с прогрессом
# --------------------------------------------------------------------------- #
def _emit(line: str, /, **vars: Any) -> None:
    """Строка хода установки в лог задания.

    С шаблоном едет и словарь подстановки (`{"t": …, "v": …}`): переводит
    интерфейс своим словарём (static/app/00-core.js:fmtLog) — у сервера язык
    свой, у браузера свой.
    """
    entry: Any = {"t": line, "v": vars} if vars else line
    with LOCK:
        log: list[Any] = JOB["log"]
        log.append(entry)
        del log[:-LOG_KEEP]


def _log(text: str) -> None:
    """Готовая строка в лог: вывод чужого процесса (pip, audio-separator) переводить нечем."""
    with LOCK:
        log: list[Any] = JOB["log"]
        log.append(text)
        del log[:-LOG_KEEP]


def _step(step: str, i: int, cur: str = "") -> None:
    """Отметить начало шага: его номер, название и процент готовности."""
    with LOCK:
        n = int(JOB["n"]) or 1
        JOB.update(step=step, i=i, cur=cur, pct=int(round(i * 100 / n)) if step != "done" else 100)


def _fail(e: BaseException) -> None:
    """Установка не вышла: причина — в состояние задания (её читает панель голоса)."""
    text = str(e) if isinstance(e, ReelsiError) else "%s: %s" % (type(e).__name__, e)
    with LOCK:
        JOB.update(running=False, done=True, step="fail", error=text, pct=100)
    _emit("! установка не вышла: {err}", err=text)


def start_install() -> bool:
    """Запустить установку фоном. False — установка уже идёт.

    Фоновым потоком, а не в запросе: venv с пакетами и две модели — это минуты и
    сотни мегабайт, и HTTP-запрос столько не живёт. Видеокарта тут не занята
    (идёт загрузка), поэтому общий лок джоба не берём: установка не мешает нарезке.
    """
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, done=False, i=0, n=2 + len(ENGINES), step="venv",
                   cur="", pct=0, error="", log=[])
    threading.Thread(target=_install, name="voice-sep-install", daemon=True).start()
    return True


def _drop_truncated(engine: str) -> None:
    """Убрать недокачанную модель перед загрузкой: чужой код пропускает уже существующий файл."""
    path = model_path(engine)
    try:
        if os.path.isfile(path) and os.path.getsize(path) < MODEL_BYTES:
            _emit("обрезанная загрузка модели — качаю заново: {name}",
                  name=os.path.basename(path))
            os.remove(path)
    except OSError as e:
        raise ReelsiError(umsg("voicefx_sep_install_failed",
                               f"не убрать битую модель {path}: {e}", err=str(e)))


def _install() -> None:
    """Шаги установки: окружение -> пакеты -> модели. Ошибка — в состояние задания."""
    try:
        _step("venv", 1)
        _emit("ставлю окружение Python: {dir}", dir=ENV_DIR)
        _run_step(venv_cmd(), "окружение", VENV_TIMEOUT)

        _step("pip", 2)
        _emit("ставлю audio-separator[{extra}] {ver} в своё окружение",
              extra=SEP_EXTRA, ver=SEP_VERSION)
        _run_step(pip_cmd(), "пакеты", PIP_TIMEOUT)

        for k, engine in enumerate(ENGINES, 3):
            _step("model", k, cur=MODELS[engine])
            _drop_truncated(engine)
            _emit("качаю модель {model}", model=MODELS[engine])
            _run_step(download_cmd(engine), "модель " + MODELS[engine], MODEL_TIMEOUT)

        _step("done", 2 + len(ENGINES))
        _emit("готово: окружение и модели на месте")
    except ReelsiError as e:
        _fail(e)
        return
    except Exception as e:                       # noqa: BLE001 — установка не повод ронять поток молча
        _fail(e)
        return
    with LOCK:
        JOB.update(running=False, done=True, step="done", pct=100)


def _run_step(cmd: list[str], what: str, timeout: float) -> None:
    """Шаг установки дочерним процессом; его вывод едет в лог задания построчно.

    Читаем ФОНОВЫМ потоком (`jobstate.pump_stdout`) и с таймаутом на каждой
    строке: pip и загрузка модели печатают ход работы минутами, и блокирующее
    чтение не дало бы ни показать его, ни снять зависший шаг. Снятый шаг гасится
    вместе с деревом (`kill_tree`): pip, оставшийся висеть, держал бы venv.
    """
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             env=child_env(), **task_popen_kwargs())
    except OSError as e:
        raise ReelsiError(umsg("voicefx_sep_install_failed",
                               f"{what}: не запустился ({e})", err=str(e)))
    q = pump_stdout(p)
    started = time.time()
    try:
        eof = False
        while True:
            try:
                line = q.get(timeout=1.0)
            except queue.Empty:
                line = ""
            if line is None:
                eof = True
            elif line.strip():
                _log(line.rstrip())
            if eof and p.poll() is not None:
                break
            if time.time() - started > timeout:
                _emit("{what}: превышен таймаут ({sec} с) — процесс снят",
                      what=what, sec=int(timeout))
                raise ReelsiError(umsg("voicefx_sep_install_failed",
                                       f"{what}: превышен таймаут ({int(timeout)} с)",
                                       err="timeout"))
    finally:
        # Один выход на все случаи: шаг установки не должен пережить свой вызов —
        # иначе он держал бы файлы окружения и мешал следующей попытке.
        if p.poll() is None:
            kill_tree(p)
            try:
                p.wait(timeout=10)
            except Exception:                     # noqa: BLE001 — процесс уже мёртв или не убивается
                pass
    if p.returncode != 0:
        raise ReelsiError(umsg("voicefx_sep_install_failed",
                               f"{what}: код возврата {p.returncode}", err=str(p.returncode)))


# --------------------------------------------------------------------------- #
# Состояние для панели голоса
# --------------------------------------------------------------------------- #
def job_state() -> dict[str, Any]:
    """Ход установки: шаг, прогресс, причина отказа и хвост лога."""
    with LOCK:
        return {"running": bool(JOB["running"]), "done": bool(JOB["done"]),
                "i": int(JOB["i"]), "n": int(JOB["n"]),
                "step": str(JOB["step"]), "cur": str(JOB["cur"]),
                "pct": int(JOB["pct"]), "error": str(JOB["error"]),
                "log": list(JOB["log"])}


def status() -> dict[str, Any]:
    """Что стоит и чего не хватает: окружение, модели по движкам, ход установки."""
    return {"env": ENV_DIR, "models_dir": model_dir(), "size": size_text(),
            "size_bytes": download_bytes(), "ready": env_ready(), "version": SEP_VERSION,
            "engines": {e: {"model": MODELS[e], "installed": installed(e),
                            "config": model_config(e)} for e in ENGINES},
            "job": job_state()}
