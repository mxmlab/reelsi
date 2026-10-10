# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обработка голоса спикера: локальный ИИ-шумодав deep-filter + цепочка VST3.

Зачем: как в DaVinci Resolve — у нового спикера (ещё не откалиброванного) голос
камеры 1 чистится ИИ-шумодавом с регулируемой силой, дальше идёт цепочка
VST3-плагинов. Результат запекается в WAV ДО After Effects: в проект уезжает уже
обработанный голос, и по нему же режет нарезка.

Куда едет результат — решает ОДНО правило: обработка включена ⇔ работает
ИИ-шумодав (`denoise.on`) ИЛИ в цепочке есть включённый плагин VST (`voice_fx_on`).
Включена — работает ВЕЗДЕ и ВЕСЬ: файл нарезки подменяет `analysis_wav` в
`apply_cut_fx` (голос после ВСЕЙ цепочки — шумодав и VST-плагины), рядом с XML
ложится запечённый `<стем>.voice.<key8>.wav`
(`ensure_final_voice`), на который ссылаются собранные проект AE
(`final_voice_for_build`), Premiere XML (`core/xmlbuild`), `.drp`
(`api/build.py`), черновой рендер (`core/draftrender`) и все превью. Имя версии
лежит в сайдкаре `.voice.json`, и читатели ходят за ним в резолвер
`final_voice_path`: под постоянным именем трек приходилось бы ЗАМЕНЯТЬ на месте,
а на Windows замена падает, если старый файл кто-то держит открытым (плеер
превью, отдача `/api/media`, открытый проект AE/Premiere) — новый звук не
появлялся, и это было молча. Отдельных
решений «для нарезки» и «в итоговый трек» больше нет: галки `cut`/`final` в
профиле остались только как ЗЕРКАЛО этого правила (старые профили с
`cut`/`final=false` читаются миграцией и обработку не выключают).

Движок шумодава выбирается полем `voice_fx.denoise.engine`:

* `deepfilter` — прежний deep-filter: предел подавления в дБ (`atten_db`);
* `roformer` / `roformer_aggr` — mel-band RoFormer в СВОЁМ окружении
  (`core/voicefx_sep`), `mix` — доля обработанного в смеси с исходником, %.

Профиль без поля движка работает на RoFormer (движок по умолчанию).
RoFormer считает дочерний процесс ЧУЖОГО окружения
(~3.4 ГБ видеопамяти на замере), поэтому он идёт под общим замком видеокарты
(`core/gpulock`): на Windows переполнение VRAM не даёт честной ошибки — оно вешает
машину.

Всё опционально. Нет deep-filter — не работает только шумодав; нет пакета
pedalboard — только VST. Поэтому pedalboard импортируется ЛЕНИВО, внутри функций:
пакет тянет за собой JUCE и нативный код, а нужен он ровно тогда, когда у спикера
реально включены плагины. Окружение RoFormer ставится отдельно и в системный
Python не лезет (`core/voicefx_sep`): нет его — нет только этого движка.

ЧУЖОЙ ПЛАГИН ЖИВЁТ ТОЛЬКО В ДОЧЕРНЕМ ПРОЦЕССЕ. `load_plugin` и
`get_plugin_names_for_file` открывают DLL плагина, а JUCE поднимает внутри неё
фоновые потоки и не останавливает их до выгрузки библиотеки — которой из живого
процесса не бывает. Замер на сервере: `GET /api/voicefx_vst_list` отвечал 13 с,
после чего процесс крутился на ~1500 % процессора, а потоков стало на 23 больше
и не падало до перезапуска. Поэтому список имён собирает `core/voicefx_scan`, а
цепочку применяет `core/voicefx_render` — оба печатают ход работы и выходят
вместе со всеми потоками. Зависший процесс снимается по PID (таймаут или
отмена), окно плагина устроено так же (`core/voicefx_editor`).

Настройки живут в профиле спикера (поле `voice_fx`, см. normalize_fx), запечённый
трек — в кеше по хешу настроек (cache_path). Окно плагина открывается отдельным
процессом (core/voicefx_editor.py): show_editor() из JUCE обязан идти в главном
потоке, а Flask живёт в рабочих.
"""
from __future__ import annotations
import base64
import binascii
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Sequence

import numpy as np

from core import paths
from core import speakers
from core import styles
from core import sync
from core import voicefx_sep
from core import voicefx_win
from core.app_meta import child_env, console_emit, module_cmd, t
from core.fileio import atomic_json_dump, atomic_stream_write, json_load_soft, quarantine_unreadable
from core.gpulock import gpu_lock
from core.jobstate import kill_tree, pump_stdout, task_popen_kwargs
from core.project_file import read_project
from core.umsg import ReelsiError, umsg

# Запечённые треки и превью: своя папка в корне репозитория (рядом с личными
# файлами пользователя), а не в %TEMP% — её должно быть видно и можно удалить
# руками. REELSI_VOICEFX_DIR — для изолированного профиля (tools/webui_test.py):
# тестовое прослушивание не должно сваливаться в боевую папку.
VOICEFX_DIR = os.environ.get("REELSI_VOICEFX_DIR") or paths.root("_voicefx")

# Предел подавления по умолчанию: 40 дБ — слышно, что шум ушёл, и голос ещё цел
# (100 = глушится всё, включая речь; 0 = обработки нет вовсе).
DENOISE_DB_DEFAULT = 40
# Движки шумодава. Свой список, а не «что придёт»: значение уезжает в профиль и в
# ключ кеша, и чужое там означало бы «обработка есть, а движка нет».
DENOISE_ENGINE_DEEP = "deepfilter"
DENOISE_ENGINES: tuple[str, ...] = (DENOISE_ENGINE_DEEP,) + voicefx_sep.ENGINES
# Профиль без поля движка работает на RoFormer: владелец выбрал его как движок по
# умолчанию (DeepFilterNet остаётся в списке — им чистят паузы, но шорох одежды
# поверх речи он не берёт).
DENOISE_ENGINE_DEFAULT = "roformer"
# Сила RoFormer — доля обработанного в смеси с исходником, %: 100 = только
# обработанный голос (100 — штатный режим: владелец выбирал движок по этому звуку).
DENOISE_MIX_DEFAULT = 100
# Сколько секунд звука берём на прослушивание и сколько живёт превью: папка
# копится файлами по 20 с на каждое нажатие, а слушают их в тот же вечер.
PREVIEW_DUR = 20.0
PREVIEW_TTL = 24 * 3600
# Блок потоковой обработки VST. Секунда — компромисс: час стерео в памяти это
# ~700 МБ float32 (на Windows это своп), а мелкий блок рвёт плагины с хвостом.
BLOCK_SECONDS = 1.0
SAMPLE_RATE = 48000
# Потолки на внешние процессы. 30 с звука deep-filter обрабатывает за 2.4 с, так
# что час — это «процесс завис», а не «длинный файл»; без таймаута зависший
# бинарник держал бы HTTP-запрос прослушивания навсегда.
DEEP_FILTER_TIMEOUT = 3600
FFMPEG_TIMEOUT = 3600
# RoFormer считает медленнее: замер владельца — 20 с звука за ~10 с ВМЕСТЕ с
# загрузкой модели, то есть ~30 с звука в минуту. Потолок в 4 часа — это «процесс
# завис» (столько звука в одном клипе не бывает), а не «длинный файл»; зависший
# снимается по PID, иначе он держал бы видеопамять до перезагрузки.
ROFORMER_TIMEOUT = 4 * 3600
# Окно плагина человек держит открытым сколько хочет — это его время, а не зависание.
EDIT_TIMEOUT = 3600
# Потолки на процессы, которые работают с ЧУЖИМ плагином. Список имён читается
# за секунды (замер: весь список — 13 с), так что минута это «плагин завис», а не
# «долго»: зависший снимается по PID, остальные плагины в списке остаются.
VST_SCAN_TIMEOUT = 60
# Цепочка — это обработка звука: час стерео плагины считают минутами.
VST_RENDER_TIMEOUT = 3600
# Сбор списка устройств поднимает аудиосистему: секунды, а не минуты.
DEVICES_TIMEOUT = 60

# Где искать шумодав, если он не прописан явно и не лежит в PATH.
DEEP_FILTER_BIN_DIR = os.path.join(os.path.expanduser("~"), ".reelsi", "deep_filter", "bin")

# Кеш списка VST3 в памяти процесса: обход папок — открытие десятков файлов, а
# список спрашивают при каждом открытии блока в UI. Имена внутри файлов лежат в
# кеше на диске (core/voicefx_scan): сюда они попадают уже собранными.
_VST3_CACHE: list[dict[str, str]] | None = None
# Кеш устройств вывода: их сбор поднимает аудиосистему, а список нужен каждый раз,
# когда открывается блок настроек голоса.
_DEVICES_CACHE: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Шумодав deep-filter
# --------------------------------------------------------------------------- #
def deep_filter_path() -> str | None:
    """Где взять бинарник шумодава: REELSI_DEEP_FILTER, PATH, ~/.reelsi/deep_filter/bin.

    Возвращает путь или None — вызывающий сам решает, ошибка это или «шумодав
    просто недоступен» (как whisper_cli_path у whisper.cpp).
    """
    explicit = os.environ.get("REELSI_DEEP_FILTER")
    if explicit and os.path.isfile(explicit):
        return explicit
    bin_name = "deep-filter.exe" if sys.platform == "win32" else "deep-filter"
    on_path = shutil.which(bin_name)
    if on_path:
        return on_path
    # Прямо И рекурсивно: архив с бинарником распаковывается и в подпапку
    # (bin/Release, build/bin) — заставлять человека переносить файл руками лишнее.
    local = os.path.join(DEEP_FILTER_BIN_DIR, bin_name)
    if os.path.isfile(local):
        return local
    for root, _dirs, files in os.walk(DEEP_FILTER_BIN_DIR):
        for f in files:
            if f == bin_name:
                return os.path.join(root, f)
    return None


def _require_deep_filter() -> str:
    """Путь к шумодаву или понятная ошибка (код deepfilter_missing)."""
    path = deep_filter_path()
    if not path:
        raise ReelsiError(umsg("deepfilter_missing",
                               "Нет шумодава deep-filter — положи его в {dir}",
                               dir=DEEP_FILTER_BIN_DIR))
    return path


# --------------------------------------------------------------------------- #
# Настройки (поле voice_fx профиля спикера)
# --------------------------------------------------------------------------- #
def _int_clamp(v: Any, lo: int, hi: int, default: int) -> int:
    """Число, зажатое в [lo, hi]; битое (строка, bool, None, NaN) — дефолт."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    try:
        n = int(v)
    except (OverflowError, ValueError):
        return default                # inf/NaN в JSON не бывает, а в профиле — бывает
    return max(lo, min(hi, n))


def _is_b64(s: str) -> bool:
    """Строка — корректный base64? Пустая считается корректной (состояния нет)."""
    if not s:
        return True
    try:
        base64.b64decode(s, validate=True)
        return True
    except (binascii.Error, ValueError):
        return False


def voice_fx_on(fx: Any) -> bool:
    """ОБРАБОТКА ГОЛОСА ВКЛЮЧЕНА — ОДНО правило на все двери и все превью.

    Включено ⇔ работает ИИ-шумодав (`denoise.on`) ИЛИ в цепочке есть включённый
    плагин VST. Галок «для нарезки» и «в итоговый трек» больше нет: включил
    ИИ-шумодав — он работает везде и весь (нарезка, итоговый трек AE/DRP/Premiere
    XML, черновой рендер и все превью). Второй копии этого решения нет ни в одном
    модуле: двери зовут ЕГО, а не читают флаги `cut`/`final` и не пересчитывают
    условие у себя.

    Принимает и сырое поле профиля, и результат `normalize_fx` — ответ один:
    нормализация не меняет ни `denoise.on`, ни включённость плагинов.
    """
    src = fx if isinstance(fx, dict) else {}
    dn = src.get("denoise")
    if isinstance(dn, dict) and dn.get("on") is True:
        return True
    vst = src.get("vst")
    if not isinstance(vst, list):
        return False
    # Плагин без пути загрузить нечем (normalize_fx его выбрасывает) — он и не
    # считается: иначе профиль с одним мусорным элементом «включал» бы обработку.
    return any(isinstance(p, dict) and bool(p.get("path")) and p.get("on") is not False
               for p in vst)


def normalize_fx(raw: Any) -> dict[str, Any]:
    """Настройки обработки голоса с дефолтами и зажимом значений.

    Профиль спикера — личный JSON, и правят его руками: строка вместо числа,
    чужой ключ или битый элемент списка не должны ронять ни нарезку, ни
    прослушивание. Поэтому всё неизвестное отбрасывается, а битое заменяется
    дефолтом (тот же приём, что у ins_quota в core/aicut/commands.py).

    Галки `cut`/`final` здесь больше не читаются как отдельные решения, а
    ВЫВОДЯТСЯ из одного правила (`voice_fx_on`): профиль, записанный до этой
    правки, с `cut`/`final=false` и включённым шумодавом обязан работать с
    обработкой, а не молча её выключать. Ключи оставлены — их читают старые
    сайдкары и профили, — но оба теперь зеркало одного ответа.
    """
    src = raw if isinstance(raw, dict) else {}
    raw_dn = src.get("denoise")
    raw_dn = raw_dn if isinstance(raw_dn, dict) else {}
    # Движок: чужое или битое значение — движок по умолчанию, а не «как-нибудь».
    raw_engine = raw_dn.get("engine")
    engine = raw_engine if isinstance(raw_engine, str) and raw_engine in DENOISE_ENGINES \
        else DENOISE_ENGINE_DEFAULT

    raw_vst = src.get("vst")
    vst: list[dict[str, Any]] = []
    for item in (raw_vst if isinstance(raw_vst, list) else []):
        if not isinstance(item, dict):
            continue                          # элемент списка — не объект
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue                          # плагин без пути загрузить нечем
        name = item.get("name")
        state = item.get("state")
        on = item.get("on")
        vst.append({
            "path": path.strip(),
            "name": name.strip() if isinstance(name, str) else "",
            # Битый base64 не пропускаем к pedalboard: raw_state принимает только
            # валидное состояние, на мусоре плагин роняет процесс целиком.
            "state": state if isinstance(state, str) and _is_b64(state) else "",
            "on": on if isinstance(on, bool) else True,
        })

    # Одно правило — и оно же в галках назначения: они зеркало, а не решение.
    on = voice_fx_on(src)
    return {
        # Сила у движков разная и живёт рядом: у deep-filter это предел подавления
        # в дБ, у RoFormer — доля обработанного в смеси, %. Оба значения хранятся
        # всегда: переключение движка туда-обратно не должно терять настройку.
        "denoise": {"on": raw_dn.get("on") is True,
                    "engine": engine,
                    "atten_db": _int_clamp(raw_dn.get("atten_db"), 0, 100, DENOISE_DB_DEFAULT),
                    "mix": _int_clamp(raw_dn.get("mix"), 0, 100, DENOISE_MIX_DEFAULT)},
        "vst": vst,
        "cut": on,
        "final": on,
    }


# --------------------------------------------------------------------------- #
# Кеш запечённого трека
# --------------------------------------------------------------------------- #
def _digest(src: str, fx: Any, *extra: Any) -> str:
    """Ключ кеша: sha1 от источника (realpath+mtime+size) и нормализованных настроек.

    sort_keys — чтобы перестановка полей в профиле не считалась сменой настроек и
    не плодила копии одного и того же трека.

    Настройки берутся нормализованными, поэтому в ключе уже есть и движок шумодава,
    и сила (дБ у deep-filter, доля у RoFormer). Сверх этого в ключ идёт ОТПЕЧАТОК
    МОДЕЛИ (`voicefx_sep.stamp`): файл модели — часть настройки, и подменённый
    .ckpt обязан обесценить запечённый трек и окна прослушивания.
    """
    try:
        st = os.stat(src)
    except OSError:
        mtime, size = 0.0, 0        # файла ещё нет — ключ всё равно нужен
    else:
        mtime, size = st.st_mtime, st.st_size
    norm = normalize_fx(fx)
    parts = [os.path.realpath(src), repr(mtime), str(size),
             json.dumps(norm, sort_keys=True, ensure_ascii=False),
             voicefx_sep.stamp(str(norm["denoise"]["engine"]))]
    parts += [repr(x) for x in extra]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def cache_path(src: str, fx: dict[str, Any]) -> str:
    """Путь запечённого трека для пары (файл камеры, настройки обработки)."""
    return os.path.join(VOICEFX_DIR, _digest(src, fx) + ".wav")


def denoise_cache_path(src: str, dn: dict[str, Any], start: float = 0.0,
                       dur: float | None = None) -> str:
    """Путь ДОРОЖКИ ШУМОДАВА: ключ — ТОЛЬКО исходник и настройки шумодава.

    Плагинов в ключе нет нарочно. Нейро-шумодав — единственный шаг, который
    считается минутами, и считается он ОДИН РАЗ на (исходник, настройки
    шумодава). Смена плагина, его ручки или порядка в цепочке эту дорожку не
    пересчитывает никогда: цепочка едет поверх готового файла.

    `start`/`dur` в ключе — ради фрагментов прослушивания: кусок клипа это
    другая дорожка, и подменять ею весь клип нельзя.
    """
    return os.path.join(VOICEFX_DIR,
                        _digest(src, {"denoise": dn}, round(float(start), 3), dur) + ".wav")


# Замки по пути дорожки: в процессе сервера дорожку одного клипа могут заказать двое —
# запекание превью (со строкой хода) и живой хост (ему тоже нужен трек). Без замка это
# два RoFormer по одному клипу разом: вдвое дольше и вдвое больше видеопамяти, а
# переполнение VRAM на Windows вешает машину. С замком второй ждёт первого и берёт
# готовый файл из кеша.
_TRACK_LOCKS: dict[str, threading.Lock] = {}
_TRACK_LOCKS_GUARD = threading.Lock()


def _track_lock(path: str) -> threading.Lock:
    """Замок счёта этой дорожки (по одному на путь; создаётся при первой просьбе)."""
    with _TRACK_LOCKS_GUARD:
        lock = _TRACK_LOCKS.get(path)
        if lock is None:
            lock = _TRACK_LOCKS[path] = threading.Lock()
        return lock


def denoise_track(src: str, dn: dict[str, Any], start: float = 0.0,
                  dur: float | None = None, emit: Callable[..., Any] = console_emit,
                  cancelled: Callable[[], bool] | None = None,
                  progress: Callable[[int, int], None] | None = None,
                  pid_of: Callable[[int], None] | None = None) -> str:
    """Дорожка шумодава: голос камеры 1 ПОСЛЕ шумодава и ДО плагинов — путь к WAV.

    Одна дверь на все места, где нужен голос без цепочки: запекание для превью,
    живой звук в панели плагина и ОСНОВА цепочки на выводе (`render`). Нарезка
    зовёт её через `render_cached` — не напрямую: голос нарезки обязан звучать как
    в ролике, то есть с плагинами (`apply_cut_fx`). Кеш — по источнику и
    настройкам шумодава (`denoise_cache_path`), поэтому смена плагинов и их ручек
    сюда не заглядывает вовсе.

    Шумодав выключен — дорожка равна извлечённому звуку камеры 1 (это ffmpeg, и он
    тоже кешируется): «шумодав выключен» должно значить «звук камеры как есть», а
    не «другой путь обработки».
    """
    path = denoise_cache_path(src, dn, start, dur)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _track_lock(path):
        if os.path.isfile(path):          # пока ждали замок, её посчитал другой заказчик
            return path
        work = _workdir(os.path.dirname(path))
        try:
            raw = os.path.join(work, "in.wav")
            _extract(src, raw, start, dur, emit)
            cur = raw
            if dn["on"]:
                cur = _denoise_any(raw, dn, work, emit, cancelled, progress, pid_of)
            _copy_atomic(cur, path)
            return path
        finally:
            shutil.rmtree(work, ignore_errors=True)   # промежуточные WAV — за собой


# --------------------------------------------------------------------------- #
# Сам рендер: ffmpeg -> deep-filter -> VST3
# --------------------------------------------------------------------------- #
def _workdir(out_dir: str) -> str:
    """Временная папка рядом с целью — на том же диске, что и результат.

    Рядом, а не в %TEMP%: промежуточный WAV весит десятки мегабайт на час звука,
    а %TEMP% бывает на другом томе (и его чистит посторонний процесс).
    """
    try:
        return tempfile.mkdtemp(prefix="_voicefx_", dir=out_dir)
    except OSError:
        return tempfile.mkdtemp(prefix="_voicefx_")   # папка недоступна — системная


def _run(cmd: Sequence[str], what: str, timeout: int, emit: Callable[..., Any]) -> None:
    """Внешний процесс с таймаутом; вывод — в лог при ошибке, наружу — код ошибки."""
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        emit("{what}: превышен таймаут ({timeout} с) — процесс снят", what=what, timeout=timeout)
        raise ReelsiError(umsg("voicefx_render_failed",
                               f"{what}: превышен таймаут ({timeout} с)", err="timeout"))
    except OSError as e:
        raise ReelsiError(umsg("voicefx_render_failed", f"{what}: не запустился ({e})", err=str(e)))
    if r.returncode != 0:
        lines = [ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
        tail = lines[-1] if lines else f"код возврата {r.returncode}"
        # В лог — хвост вывода (в нём и причина), в сообщение — он же: полный
        # stderr ffmpeg на битом файле это сотни строк.
        emit("{what}: не отработал — {err}", what=what, err=tail)
        raise ReelsiError(umsg("voicefx_render_failed", f"{what}: {tail}", err=tail))


def _extract(src: str, out_wav: str, start: float, dur: float | None,
             emit: Callable[..., Any]) -> None:
    """Звук источника в WAV 48 кГц с ИСХОДНЫМ числом каналов.

    `-ac` не ставим нарочно: голос камеры 1 бывает и стерео, а шумодав с
    плагинами работают с тем, что пришло. `-D` у deep-filter компенсирует
    задержку, и тайминг выхода совпадает со входом — на этом стоит вся нарезка.
    """
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]        # до -i: seek, а не декод с нуля
    cmd += ["-i", src, "-vn"]
    if dur is not None:
        cmd += ["-t", f"{float(dur):.3f}"]
    cmd += ["-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", out_wav]
    _run(cmd, "ffmpeg (звук из видео)", FFMPEG_TIMEOUT, emit)


def _denoise(raw_wav: str, atten_db: int, work: str, emit: Callable[..., Any]) -> str:
    """Прогнать WAV через deep-filter; вернуть путь обработанного файла.

    `-a` — предел подавления в дБ (0 — без обработки, 100 — глушит всё), `-D` —
    компенсация задержки: голос выравнивается по входу (иначе он запаздывает на
    задержку STFT и lookahead модели, и нарезка, посчитанная по обработанному
    голосу, уехала бы от картинки). Плата за выравнивание — срезанный хвост в эту
    же задержку, его возвращает `_pad_tail`.
    """
    cli = _require_deep_filter()
    out_dir = os.path.join(work, "dn")
    os.makedirs(out_dir, exist_ok=True)
    emit("Голос: ИИ-шумодав, подавление {db} дБ", db=atten_db)
    try:
        r = subprocess.run([cli, "-a", str(atten_db), "-D", "-o", out_dir, raw_wav],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=DEEP_FILTER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ReelsiError(umsg("deepfilter_failed", "deep-filter: превышен таймаут "
                               f"({DEEP_FILTER_TIMEOUT} с)", err="timeout"))
    except OSError as e:
        raise ReelsiError(umsg("deepfilter_failed", f"deep-filter: не запустился ({e})", err=str(e)))
    if r.returncode != 0:
        lines = [ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
        tail = lines[-1] if lines else f"код возврата {r.returncode}"
        emit("deep-filter: не отработал — {err}", err=tail)
        raise ReelsiError(umsg("deepfilter_failed", f"deep-filter: {tail}", err=tail))
    out = os.path.join(out_dir, os.path.basename(raw_wav))
    if not os.path.isfile(out):
        raise ReelsiError(umsg("deepfilter_failed",
                               "deep-filter не записал результат — смотри лог", err=out_dir))
    return out


def _wav_duration(path: str) -> float:
    """Длительность WAV в секундах (0.0 — файл не читается как WAV).

    Своими руками через wave, а не через ffprobe: сюда приходит только наш же
    извлечённый PCM, а лишний процесс на каждый рендер ни к чему (общая проба
    длительности — core/media.probe_duration — умеет любое медиа и стоит ffprobe).
    """
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate() or SAMPLE_RATE
            return w.getnframes() / float(rate)
    except (OSError, EOFError, wave.Error):
        return 0.0


def _pad_tail(path: str, seconds: float, emit: Callable[..., Any]) -> None:
    """Дописать тишину в хвост, чтобы длительность совпала с длительностью входа.

    Замер на живом звуке (deep-filter 0.5.6, 30 с при 48 кГц): без `-D` на выходе
    ровно 1440000 сэмплов (30.000 с), с `-D` — 1438560 (29.970 с), то есть ровно
    1440 сэмплов = 30 мс. Это и есть задержка STFT и lookahead модели, которую `-D`
    компенсирует: голос в обоих случаях стоит на своём месте (пик корреляции со
    входом на лаге 0, ширина пика ±1 сэмпл), а хвост в эту задержку модель уже не
    отдаёт. Без добора запечённый голос был бы короче исходного звука — а по нему
    режет нарезка, и он же уезжает в итоговый трек: длительности обязаны совпасть,
    иначе «голос кончился раньше картинки» ловится уже в After Effects.
    """
    try:
        with wave.open(path, "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            have = w.getnframes()
    except (OSError, EOFError, wave.Error):
        return                        # не WAV — добирать нечего, рендер не валим
    want = int(round(float(seconds) * rate))
    if want <= have:
        return
    silence = b"\x00" * ((want - have) * channels * width)

    def _write(out: IO[bytes]) -> None:
        with wave.open(path, "rb") as r, wave.open(out, "wb") as o:
            o.setnchannels(channels)
            o.setsampwidth(width)
            o.setframerate(rate)
            while True:
                chunk = r.readframes(rate)      # по секунде: файл бывает на час
                if not chunk:
                    break
                o.writeframes(chunk)
            o.writeframes(silence)
    atomic_stream_write(path, _write)
    emit("Голос: хвост {ms:.0f} мс добит тишиной до длины входа", ms=(want - have) / rate * 1000)


# --------------------------------------------------------------------------- #
# Шумодав RoFormer (своё окружение, дочерний процесс)
# --------------------------------------------------------------------------- #
def _wav_fmt(path: str) -> tuple[int, int, int] | None:
    """Формат WAV (каналы, ширина сэмпла, частота) или None — файл не читается.

    Своими руками через wave: сюда приходит только наш же PCM из `_mono48`, а
    лишний процесс на каждый рендер ни к чему (та же причина, что у `_wav_duration`).
    """
    try:
        with wave.open(path, "rb") as w:
            return w.getnchannels(), w.getsampwidth(), w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def _mono48(src: str, dst: str, emit: Callable[..., Any]) -> str:
    """WAV в 48 кГц МОНО — общий вид входа и выхода RoFormer.

    Пересэмплирует и сводит в моно ffmpeg (тот же приём, что у звука камеры):
    RoFormer отдаёт 44.1 кГц стерео, а голос проекта живёт в 48 кГц. Смешивать
    обработанное с исходником можно только в одном формате — иначе «полусумма»
    складывала бы разные каналы.
    """
    _run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1",
          "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", dst],
         "ffmpeg (голос: моно 48 кГц)", FFMPEG_TIMEOUT, emit)
    return dst


def mix_samples(original: np.ndarray, processed: np.ndarray, percent: int) -> np.ndarray:
    """Смесь обработанного с исходником по силе: доля обработанного в процентах.

    100 — только обработанный (штатный режим RoFormer), 0 — исходник как есть,
    50 — полусумма. Считаем во float и зажимаем в int16: целочисленное деление
    на полпути потеряло бы младшие биты, и «полусумма» перестала бы быть
    полусуммой. Концы обрабатываются отдельно — на 100 % результат обязан быть
    ПОБИТОВО равен обработанному (иначе «только обработанный» тихо подмешивал бы
    исходник на округлении float).
    """
    r = max(0, min(100, int(percent))) / 100.0
    if r <= 0.0:
        return original.astype(np.int16, copy=True)
    if r >= 1.0:
        return processed.astype(np.int16, copy=True)
    a = original.astype(np.float32, copy=False)
    b = processed.astype(np.float32, copy=False)
    return np.clip(np.rint(a + (b - a) * r), -32768, 32767).astype(np.int16)


def _mix_wav(orig_wav: str, proc_wav: str, out_wav: str, percent: int,
             emit: Callable[..., Any]) -> str:
    """Смешать обработанный WAV с исходником по силе; длина — как у исходника.

    Блоками по секунде: час моно 48 кГц — это 345 МБ на массив, а в памяти их два
    плюс float-копия (core/voicefx_render делает так же и по той же причине).
    Файлы сюда приходят одного формата — оба сделаны `_mono48`; другой формат это
    ошибка, а не «как-нибудь сведём»: молча смешать стерео с моно значит испортить
    голос, которого потом никто не услышит до After Effects.

    Обработанного может не хватить на хвост (модель отдаёт чуть короче) — хвост
    остаётся ИСХОДНИКОМ: длина обязана совпасть с длительностью входа, по этому
    файлу режет нарезка и он же уезжает в итоговый трек.
    """
    fmt = _wav_fmt(orig_wav)
    if fmt is None or fmt != _wav_fmt(proc_wav) or fmt[1] != 2:
        raise ReelsiError(umsg("roformer_failed",
                               "RoFormer: выход не 48 кГц моно — смесь невозможна",
                               err="format"))
    channels, width, rate = fmt
    frame = channels * width

    def _write(out: IO[bytes]) -> None:
        with wave.open(orig_wav, "rb") as a, wave.open(proc_wav, "rb") as b, \
                wave.open(out, "wb") as o:
            o.setnchannels(channels)
            o.setsampwidth(width)
            o.setframerate(rate)
            while True:
                chunk = a.readframes(rate)        # по секунде: файл бывает на час
                if not chunk:
                    break
                proc = b.readframes(len(chunk) // frame)
                keep = min(len(chunk), len(proc)) // frame
                if keep <= 0:
                    o.writeframes(chunk)          # обработка кончилась — хвост как есть
                    continue
                head = keep * frame
                mixed = mix_samples(np.frombuffer(chunk[:head], dtype="<i2"),
                                    np.frombuffer(proc[:head], dtype="<i2"), percent)
                o.writeframes(mixed.tobytes() + chunk[head:])
    atomic_stream_write(out_wav, _write)
    emit("Голос: RoFormer смешан с исходником — обработанного {pct} %", pct=percent)
    return out_wav


def _denoise_roformer(mono_wav: str, engine: str, work: str, emit: Callable[..., Any],
                      cancelled: Callable[[], bool] | None = None,
                      progress: Callable[[int, int], None] | None = None,
                      pid_of: Callable[[int], None] | None = None) -> str:
    """Прогнать WAV через RoFormer ДОЧЕРНИМ процессом; вернуть путь обработанного.

    Окружение и модели — свои (`core/voicefx_sep`), команда — тот же консольный
    скрипт audio-separator, которым владелец сравнивал движки. Процесс держит
    ~3.4 ГБ видеопамяти, поэтому идёт под общим замком видеокарты (`core/gpulock`):
    на Windows переполнение VRAM не даёт честной ошибки — оно вешает машину.
    Отмена и таймаут снимают процесс по PID (`_run_child`), иначе он остался бы
    держать видеопамять до перезагрузки. `pid_of` отдаёт его PID наружу — «Стоп»
    снимает счёт, не дожидаясь следующей строки процесса.

    `progress(i, n)` — ход работы: audio-separator печатает его сам (`N/M` в
    строке), и по нему интерфейс показывает проценты. Своей оценки «сколько
    осталось» здесь не считаем: она врала бы на каждом куске.

    Наружу — уже приведённый к 48 кГц моно файл: `_mix_wav` смешивает его с
    исходником, а смесь разных форматов — это не смесь.
    """
    voicefx_sep.require(engine)
    out_dir = os.path.join(work, "sep")
    os.makedirs(out_dir, exist_ok=True)
    emit("Голос: ИИ-шумодав RoFormer — {model}", model=voicefx_sep.MODELS[engine])
    with gpu_lock("шумодав RoFormer", emit):
        run = _run_child(voicefx_sep.separate_cmd(mono_wav, out_dir, engine),
                         "RoFormer", ROFORMER_TIMEOUT, emit, cancelled,
                         progress=progress, pid_of=pid_of)
    if run.errors:
        raise run.errors[0]
    if run.returncode != 0:
        raise ReelsiError(umsg("roformer_failed",
                               f"RoFormer: код возврата {run.returncode} — смотри лог",
                               err=str(run.returncode)))
    proc = voicefx_sep.separated_wav(out_dir)
    if not proc:
        raise ReelsiError(umsg("roformer_failed",
                               "RoFormer не записал результат — смотри лог", err=out_dir))
    return _mono48(proc, os.path.join(work, "sep_mono.wav"), emit)


def _denoise_any(raw_wav: str, dn: dict[str, Any], work: str, emit: Callable[..., Any],
                 cancelled: Callable[[], bool] | None = None,
                 progress: Callable[[int, int], None] | None = None,
                 pid_of: Callable[[int], None] | None = None) -> str:
    """Шумодав, выбранный движком (`denoise.engine`): deep-filter или RoFormer.

    Одна дверь на все места, где чистится голос — запекание, прослушивание «было/
    стало» и живой звук в окне плагина: у движка не должно быть второго пути,
    который обрабатывает иначе. Результат всегда одной длительности с входом:
    deep-filter срезает хвост задержки (`_pad_tail`), у RoFormer длину держит
    смесь (`_mix_wav`).

    `progress` понимает только RoFormer: deep-filter о ходе работы молчит, и
    выдумывать за него проценты нечем — полоса остаётся «идёт работа».
    `pid_of` — PID дочернего счёта RoFormer (у deep-filter процесс свой, но ход
    его не нужен: он не держит видеопамять минутами).
    """
    if dn["engine"] == DENOISE_ENGINE_DEEP:
        out = _denoise(raw_wav, int(dn["atten_db"]), work, emit)
        _pad_tail(out, _wav_duration(raw_wav), emit)
        return out
    mono = _mono48(raw_wav, os.path.join(work, "in_mono.wav"), emit)
    proc = _denoise_roformer(mono, str(dn["engine"]), work, emit, cancelled, progress, pid_of)
    return _mix_wav(mono, proc, os.path.join(work, "dn_mix.wav"), int(dn["mix"]), emit)


# --------------------------------------------------------------------------- #
# Процессы, которые работают с чужим плагином
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ChildRun:
    """Итог дочернего процесса с чужим плагином: ответ, ошибки, код возврата.

    Отдельным типом, а не исключением: процесс с плагином умеет падать нативно
    (обращение к памяти), и для сканера это штатная ситуация — «этот плагин не
    грузится, идём к следующему». Исключением сюда уезжают только решения самого
    родителя: отмена и таймаут (`_run_child`).

    `tail` — последняя содержательная строка вывода процесса. Нужна ровно для
    одного: процесс с чужим плагином умирает нативно, своего `{"error"}` не
    печатает, и человеку вместо «код возврата 1» надо показать причину из stderr
    (имя плагина и что с ним не так).
    """

    answers: list[dict[str, Any]]
    errors: list[ReelsiError]
    returncode: int
    tail: str = ""

    def answer(self) -> dict[str, Any]:
        """Последний JSON-объект процесса (его ответ) или пустой словарь."""
        return self.answers[-1] if self.answers else {}


def _kill(p: subprocess.Popen[Any], what: str) -> None:
    """Снять процесс по PID (вместе с деревом) и подождать, пока он умрёт.

    Не `p.kill()` в одиночку: у плагина бывают свои дочерние процессы, и на
    Windows они остались бы жить с занятой библиотекой. `kill_tree` — общая
    реализация проекта (core/jobstate), она же гасит нарезку и рендер.
    """
    kill_tree(p)
    try:
        p.wait(timeout=10)
    except Exception:                         # noqa: BLE001 — процесс уже мёртв или не убивается
        pass
    print(t("{what}: процесс снят по PID {pid}", what=what, pid=p.pid), flush=True)


def _lines(emit: Callable[..., Any] | None, what: str,
           out: list[dict[str, Any]]) -> Callable[[str], None]:
    """Приём строк процесса: JSON-протокол дочерних модулей — в лог вызывающего.

    Протокол (см. core/voicefx_scan.py, core/voicefx_audio.py,
    core/voicefx_render.py): строка либо JSON-объект, либо просто текст.
    Разбирается и то и другое: предупреждение самого Python (например, numpy при
    импорте пакета) приходит обычным текстом, и терять его в логе не за чем.

    Разобранные объекты складываются в `out`: ответ процесса (`devices` и
    прочие поля) нужен вызывающему целиком, а не строкой лога. `emit=None` —
    ответ забираем молча: список плагинов зовут из роута, и его служебные строки
    в консоль сервера не нужны.
    """
    def _one(line: str) -> None:
        text = line.strip()
        if not text:
            return
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                pass                          # не JSON, а текст со скобкой — печатаем как есть
            else:
                if isinstance(data, dict):
                    out.append(data)
                    if data.get("msg") and emit is not None:
                        emit(str(data["msg"]))
                    return
        if emit is not None:
            emit("{what}: {line}", what=what, line=text)
    return _one


def _raise_child(run: _ChildRun, what: str) -> None:
    """Разобрать итог процесса для НЕсканирующих вызовов: ошибка — исключением.

    Сканеру падение нужно как данные (см. `_rescan_vst3`), а рендеру и списку
    устройств — как отказ: молча вернуть пустой список значило бы показать
    человеку «ничего не нашлось» вместо причины. Код ошибки сохраняем: по нему
    фронт берёт перевод (api/voicefx.py, _FORWARDED).
    """
    if run.errors:
        raise run.errors[0]
    if run.returncode != 0:
        # Причина важнее кода: процесс с чужим плагином падает нативно и своего
        # `{"error"}` не печатает — человеку нужна последняя строка его вывода
        # (имя плагина и что случилось), а «код возврата 1» не говорит ничего.
        why = run.tail or f"код возврата {run.returncode}"
        raise ReelsiError(umsg("voicefx_render_failed", f"{what}: {why}", err=why))


def _progress_emit(emit: Callable[..., Any] | None,
                   progress: Callable[[int, int], None]) -> Callable[..., None]:
    """Обёртка над emit: из строки чужого процесса достаёт ход работы (`N/M`).

    RoFormer (audio-separator) печатает ход сам — полосой вида `45%|…| 9/20`.
    Другого источника процентов у нас нет, а выдумывать свою оценку «сколько
    осталось» значило бы врать на каждом куске. Разбор живёт ЗДЕСЬ и только
    здесь: и запекание клипа, и прослушивание идут одной дорогой.
    """
    def _one(line: str = "", /, **vars: Any) -> None:
        if emit is not None:
            emit(line, **vars)
        text = str(vars.get("line") or line or "")
        m = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if not m:
            return
        i, n = int(m.group(1)), int(m.group(2))
        if n > 0 and 0 <= i <= n:
            progress(i, n)
    return _one


def _run_child(cmd: list[str], what: str, timeout: int,
               emit: Callable[..., Any] | None = console_emit,
               cancelled: Callable[[], bool] | None = None,
               progress: Callable[[int, int], None] | None = None,
               pid_of: Callable[[int], None] | None = None) -> _ChildRun:
    """Дочерний процесс с чужим плагином: вывод — в лог, отмена и таймаут — по PID.

    Читаем вывод ФОНОВЫМ потоком (`jobstate.pump_stdout`), а не `for line in
    p.stdout`: пока главный поток сидит на чтении, ни отмену не заметить, ни
    таймаут не отсчитать, а полная труба останавливает процесс навсегда. Ровно на
    этом встала нарезка, когда вывод AfterFX набил буфер (см. pump_stdout).

    Отмена убивает процесс СРАЗУ: плагин с задержкой на блок иначе досчитывал бы
    свою секунду до конца, а «Стоп» ждал бы его молча. Зависший снимается по PID
    по таймауту; в обоих случаях процесс снимается вместе с деревом (`_kill`).

    Наружу — `_ChildRun` (ответ, ошибки, код возврата), а НЕ исключение: процесс
    с чужим плагином умеет падать нативно, и разбирать это должен вызывающий.
    Сканеру нужно знать, что плагин уронил процесс и продолжить со следующего
    (см. `_rescan_vst3`), поэтому «упал» — это данные отчёта, а не сбой родителя.
    Отмену и таймаут родитель всё равно поднимает исключением: это его решение,
    а не свойство плагина.

    `pid_of(pid)` — «вот PID того самого процесса, который мы запустили»: по нему
    «Стоп» снимает счёт СРАЗУ и не ждёт следующего витка цикла чтения. Ждём мы
    вывод блокирующе (до секунды) и ход шумодава печатает не каждый виток, а
    `cancelled` спрашивается лишь после него — без этого «Стоп» молчал бы до
    ближайшей строки процесса, а на длинном куске это секунды.
    """
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             env=child_env(), **task_popen_kwargs())
    except OSError as e:
        raise ReelsiError(umsg("voicefx_render_failed", f"{what}: не запустился ({e})",
                               err=str(e)))
    if pid_of is not None:
        pid_of(p.pid)
    q = pump_stdout(p)
    answers: list[dict[str, Any]] = []
    line_out = _lines(_progress_emit(emit, progress) if progress is not None else emit,
                      what, answers)
    errors: list[ReelsiError] = []
    # Последняя содержательная строка вывода: у нативно упавшего процесса только
    # она и есть причина (см. `_raise_child`).
    tail = ""

    def _drain(block: bool) -> bool:
        """Разобрать накопленные строки процесса; True — вывод кончился (EOF).

        «Вывод кончился» и «процесс вышел» — разные события: последние строки
        бывают в трубе уже после выхода, поэтому ждём EOF, а не `poll()`.
        """
        nonlocal tail
        while True:
            try:
                line = q.get(timeout=1.0 if block else 0.0)
            except queue.Empty:
                return False
            if line is None:
                return True
            text = line.strip()
            if text:
                tail = text
            data: Any = None
            if text.startswith("{"):
                try:
                    data = json.loads(text)
                except ValueError:
                    data = None
            if isinstance(data, dict) and data.get("error") is not None:
                # Ошибку не печатаем сразу: наружу она уходит целиком и с кодом —
                # по коду фронт берёт перевод (api/voicefx.py, _FORWARDED).
                errors.append(ReelsiError(umsg(str(data.get("error") or "voicefx_render_failed"),
                                               str(data.get("text") or what))))
            else:
                line_out(line)

    t0 = time.time()
    try:
        eof = False
        while True:
            eof = _drain(True) or eof
            if cancelled is not None and cancelled():
                raise ReelsiError(umsg("voicefx_cancelled", f"{what}: отменено"))
            if eof and p.poll() is not None:
                break
            if time.time() - t0 > timeout:
                if emit is not None:
                    emit("{what}: превышен таймаут ({timeout} с) — процесс снят",
                         what=what, timeout=timeout)
                raise ReelsiError(umsg("voicefx_render_failed",
                                       f"{what}: превышен таймаут ({timeout} с)", err="timeout"))
    finally:
        # Один выход на все случаи — отмена, таймаут, любое исключение разбора:
        # процесс с чужим плагином не должен пережить свой вызов НИКОГДА, иначе он
        # остался бы держать плагин и его потоки до перезагрузки (ровно то, от чего
        # уходили), а «Стоп» ждал бы конца блока.
        if p.poll() is None:
            _kill(p, what)
    _drain(False)
    return _ChildRun(answers, errors, p.returncode, tail)


def _copy_atomic(src: str, dst: str) -> None:
    """Скопировать WAV в цель атомарно (tmp + fsync + os.replace, core/fileio)."""
    def _write(f: IO[bytes]) -> None:
        with open(src, "rb") as r:
            shutil.copyfileobj(r, f, 1024 * 1024)
    atomic_stream_write(dst, _write)


def _apply_vst(src: str, plugins: list[dict[str, Any]], out_wav: str,
               emit: Callable[..., Any],
               cancelled: Callable[[], bool] | None = None) -> list[dict[str, str]]:
    """Цепочка VST3 по порядку — ДОЧЕРНИМ процессом (core/voicefx_render).

    Почему процессом: `load_plugin` открывает DLL плагина, JUCE поднимает в ней
    фоновые потоки и не останавливает их до выгрузки библиотеки. В сервере это
    стоило ~1500 % процессора и +23 потоков навсегда (см. докстринг модуля);
    здесь процесс уходит вместе с плагинами, а в лог вызывающего едут его строки.

    Наружу — файл WAV: `raw_state` это байты, в аргументах командной строки им
    делать нечего (как у окна плагина), а звук в память не влезает.

    Возвращает ПРОПУЩЕННЫЕ плагины: незагружаемый плагин не роняет цепочку, а
    выпадает из неё, и причина с его ИМЕНЕМ едет вызывающему — человеку её
    показывает панель «Голос» («Bertom_DenoiserClassic не загрузился — пропущен»).
    """
    work = tempfile.mkdtemp(prefix="_voicefx_vst_")
    try:
        job = os.path.join(work, "job.json")
        atomic_json_dump(job, {
            "src": src, "out": out_wav,
            "chain": [{"path": p["path"], "name": p["name"], "state_b64": p["state"],
                       "on": True} for p in plugins]}, indent=1)
        run = _run_child(module_cmd("voicefx_render", "--job", job),
                         "VST-цепочка", VST_RENDER_TIMEOUT, emit, cancelled)
        _raise_child(run, "VST-цепочка")
        skipped: list[dict[str, str]] = []
        for answer in run.answers:
            items = answer.get("skipped")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        skipped.append({"name": str(it.get("name") or ""),
                                        "reason": str(it.get("reason") or "")})
        for it in skipped:
            emit("Голос: плагин не загрузился — пропущен ({n})", n=it["name"])
        return skipped
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render(src: str, fx: dict[str, Any], out_wav: str, start: float = 0.0,
           dur: float | None = None, emit: Callable[..., Any] = console_emit,
           cancelled: Callable[[], bool] | None = None,
           progress: Callable[[int, int], None] | None = None,
           pid_of: Callable[[int], None] | None = None,
           skipped: list[dict[str, str]] | None = None) -> str:
    """Собрать обработанный голос в WAV и вернуть путь к нему.

    1. берётся ДОРОЖКА ШУМОДАВА (`denoise_track`): извлечённый звук камеры, а с
       включённым шумодавом — он же после выбранного движка (`denoise.engine`);
    2. есть включённые VST — цепочка ДОЧЕРНИМ процессом ложится ПОВЕРХ дорожки;
    3. результат кладётся в out_wav атомарно.

    Порядок такой нарочно: шумодав считается один раз и кешируется отдельно
    (`denoise_cache_path`), поэтому ни смена плагина, ни его ручки, ни правка
    порядка в цепочке НЕ пересчитывают нейро-шумодав — они пересобирают только
    цепочку поверх готового файла. Раньше тем же ключом кеша был итог с
    плагинами, и каждая правка ручки VST гоняла RoFormer по всему клипу заново.

    Ничего не включено — в out_wav уезжает дорожка шумодава, то есть извлечённый
    звук камеры (это и есть «было» в прослушивании). Длительность результата
    равна длительности извлечённого звука: хвост, срезанный `-D` у шумодава,
    добирается тишиной (см. `_pad_tail`) — по запечённому голосу режет нарезка и
    он же уезжает в итоговый трек, а «голос кончился на 30 мс раньше картинки»
    ищут руками в After Effects.

    Задержку САМИХ VST-плагинов не компенсируем: pedalboard отдаёт
    `reported_latency_samples`, но сверить её на живом плагине здесь нечем (окна
    плагинов открывает человек). Плагин с задержкой сдвинет голос — это видно на
    прослушивании «было/стало» и лечится выбором плагина без lookahead.

    `cancelled` — «Стоп» из интерфейса: зовётся на каждом шаге обработки, и
    цепочка (единственный долгий шаг с чужим кодом) снимается по PID.

    `progress(i, n)` — ход работы шумодава: у RoFormer он свой (печатает чужой
    процесс), у deep-filter его нет вовсе — проценты тогда не выдумываются.

    `pid_of(pid)` — PID дочернего счёта шумодава: по нему «Стоп» снимает процесс
    СРАЗУ, не дожидаясь следующего витка чтения его вывода (`_run_child`).

    `skipped` — необязательный список-приёмник: в него кладутся плагины, которые
    не загрузились и были пропущены (имя и причина). Список, а не возвращаемое
    значение: у `render` уже есть ответ — путь к файлу, и ломать его нельзя.
    """
    norm = normalize_fx(fx)
    if not os.path.isfile(src):
        raise ReelsiError(umsg("file_not_found", f"Файл не найден: {src}", path=src))
    out_dir = os.path.dirname(os.path.abspath(out_wav))
    os.makedirs(out_dir, exist_ok=True)
    track = denoise_track(src, norm["denoise"], start, dur, emit, cancelled, progress, pid_of)
    plugins = [p for p in norm["vst"] if p["on"]]
    if plugins:
        bad = _apply_vst(track, plugins, out_wav, emit, cancelled)
        if skipped is not None:
            skipped.extend(bad)
    else:
        _copy_atomic(track, out_wav)
    return out_wav


def render_cached(src: str, fx: dict[str, Any], emit: Callable[..., Any] = console_emit,
                  progress: Callable[[int, int], None] | None = None,
                  cancelled: Callable[[], bool] | None = None,
                  pid_of: Callable[[int], None] | None = None,
                  skipped: list[dict[str, str]] | None = None) -> str:
    """Готовый трек из кеша, а нет — собрать и положить в кеш.

    Плагинов в цепочке нет — отдаётся ДОРОЖКА ШУМОДАВА: она и есть результат, и
    у неё свой кеш (`denoise_cache_path`, ключ без VST). Иначе — итог с цепочкой
    под ключом настроек (`cache_path`): тот же голос с теми же ручками второй раз
    не считается, а смена движка, силы или плагинов даёт другой файл. Имя файла в
    кеше НЕ ПЕРЕЗАПИСЫВАЕТСЯ: сменил настройки — это другой файл.

    `progress`, `cancelled` и `pid_of` едут в `render` без изменений: счёт на
    минуты зовут из запекания клипа, где «Стоп» обязан снять процесс по PID.
    """
    norm = normalize_fx(fx)
    if not any(p["on"] for p in norm["vst"]):
        return denoise_track(src, norm["denoise"], 0.0, None, emit, cancelled, progress, pid_of)
    path = cache_path(src, fx)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return render(src, fx, path, emit=emit, progress=progress,
                  cancelled=cancelled, pid_of=pid_of, skipped=skipped)


# --------------------------------------------------------------------------- #
# Подключение: нарезка и итоговый голос — по ОДНОМУ правилу (voice_fx_on)
# --------------------------------------------------------------------------- #
def style_voice_db(profile: Any) -> float:
    """Громкость голоса из стиля спикера (`voice_db`, дБ). Стиля нет — 0.

    Нарезка слушает тот же уровень, что уедет в ролик: стиль спикера — обычный
    пресет AE (`styles/*.json`), и громкость голоса живёт в нём.
    """
    style = profile.get("style") if isinstance(profile, dict) else None
    return float(styles.resolve(style).get("voice_db") or 0.0)


def analysis_wav(full: str, dst: str, gain_db: float = 0.0,
                 emit: Callable[..., Any] = console_emit) -> str:
    """Голос для НАРЕЗКИ: моно `sync.SR`, PCM, с громкостью стиля — поверх `dst`.

    Тот же ffmpeg, что извлекает звук камеры (`sync.extract_audio`): нарезка
    слушает моно 16 кГц, и подменяемый файл обязан быть таким же — иначе пороги
    VAD, вздохи и распознавание считались бы по другому звуку.

    Пишем во временный файл рядом, а подмену `dst` делает
    `core.fileio.atomic_stream_write`: сбой ffmpeg на середине записи не должен
    оставить нарезку без звука вовсе — по `dst` она уже режет.
    """
    tmp = dst + ".voice.tmp.wav"
    cmd = ["ffmpeg", "-y", "-i", full, "-vn", "-ac", "1", "-ar", str(sync.SR)]
    if abs(float(gain_db)) > 1e-9:
        cmd += ["-af", "volume=%gdB" % float(gain_db)]
    cmd += ["-c:a", "pcm_s16le", tmp, "-loglevel", "error"]
    try:
        _run(cmd, "ffmpeg (голос для нарезки)", FFMPEG_TIMEOUT, emit)

        def _write(f: IO[bytes]) -> None:
            with open(tmp, "rb") as r:
                shutil.copyfileobj(r, f, 1024 * 1024)
        atomic_stream_write(dst, _write)
        os.remove(tmp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass  # временного файла нет (ffmpeg не дошёл до записи) — убирать нечего
        raise
    return dst


def apply_cut_fx(wav0: str, cam1: str, speaker: Any,
                 emit: Callable[..., Any] = console_emit) -> bool:
    """Нарезка по обработанному голосу: подменить `wav0` (одно правило — `voice_fx_on`).

    `speaker` — профиль спикера или его ключ. True — файл нарезки заменён
    обработанным голосом, False — обрабатывать нечего или не вышло.

    Отдельной галки «для нарезки» нет: включённая обработка означает, что нарезка
    слушает тот же обработанный голос, что уедет в ролик. Решение — `voice_fx_on`,
    второй копии условия здесь нет.

    Слушается ВСЯ цепочка спикера, как её слышит владелец (решение от 02.10.2026):
    звук камеры 1 → шумодав (если включён) → включённые VST-плагины цепочки →
    `analysis_wav` с громкостью стиля. Собирает её ТА ЖЕ дверь, что и вывод
    (`render_cached` → `render` → `denoise_track` + `_apply_vst`): второй копии
    обработки у нарезки нет, и цепочка здесь звучит ровно так же, как в ролике.
    Так было не всегда: раньше нарезка слушала дорожку шумодава без плагинов, и
    слова, которые плагин чинит или глушит, распознавались по другому звуку.
    Шумодав выключен — обрабатывать нечего, если только в цепочке не включены
    плагины: тогда цепочка идёт по сырому звуку камеры (её собирает всё тот же
    `render_cached`), а не по копии того же звука.

    Смещения камер посчитаны к этому моменту по СЫРОМУ звуку (`sync.find_offset`):
    подмена касается только файла, по которому режут (распознавание, вздохи,
    пороги), — «в нарезке голос звучит так же, как в ролике».

    Нарезка не должна падать из-за обработки: сбой цепочки (в том числе плагина)
    — предупреждение в лог и нарезка по дорожке шумодава, а сбой и её — по сырому
    звуку. Загрузку плагинов делает дочерний процесс (`_apply_vst`), и его ошибка
    приходит сюда как обычное исключение: чужой плагин не должен оставить нарезку
    без звука.
    """
    prof = speaker if isinstance(speaker, dict) else speakers.load(speaker)
    if not isinstance(prof, dict):
        return False
    fx = normalize_fx(prof.get("voice_fx"))
    if not voice_fx_on(fx):
        return False
    emit("голос: обработка для нарезки…")
    gain = style_voice_db(prof)
    try:
        # Вся цепочка и её кеш целиком (исходник + шумодав + плагины с состоянием):
        # повторная нарезка того же клипа без правок не печёт ничего заново.
        full = render_cached(cam1, fx, emit=emit)
    except Exception as e:                       # noqa: BLE001 — нарезка важнее обработки
        emit("! голос для нарезки: обработка не удалась ({err}) — "
             "слушаю дорожку шумодава", err=e)
        try:
            full = denoise_track(cam1, fx["denoise"], emit=emit)
        except Exception as e2:                  # noqa: BLE001 — нарезка важнее шумодава
            emit("! голос для нарезки не обработан ({err}) — режу по сырому звуку", err=e2)
            return False
    try:
        analysis_wav(full, wav0, gain_db=gain, emit=emit)
    except Exception as e:                       # noqa: BLE001 — нарезка важнее обработки
        emit("! голос для нарезки не обработан ({err}) — режу по сырому звуку", err=e)
        return False
    return True


def final_voice_path(xml_path: str) -> str:
    """Итоговый голос клипа РЯДОМ с XML — имя версии из сайдкара, иначе легаси.

    Рядом с XML, а не в `_voicefx`: на этот файл ссылается собранный проект AE,
    а кеш запечённых треков чистят (это кеш) — проект остался бы без голоса.

    ОДНА дверь для всех читателей: имя собирается здесь, а не у каждого своего.
    Имя версионное (`<стем>.voice.<key8>.wav`), и лежит оно в сайдкаре
    `.voice.json` полем `"file"`: под постоянным именем трек приходилось бы
    ЗАМЕНЯТЬ на месте, а на Windows замена падает, если старый файл кто-то держит
    открытым (плеер превью играет именно его, `/api/media` отдаёт его же,
    открытый проект AE/Premiere держит его сам). Сбой был молчаливым: новый звук
    не появлялся, а в логе оставалась одна строка.

    Имя берётся ТОЛЬКО если файл на месте: сайдкар мог пережить убранный файл, и
    отдавать читателю мёртвый путь нельзя. Нет сайдкара, нет поля или файла — старое
    `<стем>.voice.wav`: клипы, запечённые до версионных имён, читаются как читались.
    """
    saved = json_load_soft(_final_meta_path(xml_path))
    name = saved.get("file") if isinstance(saved, dict) else None
    if isinstance(name, str) and name:
        # basename: сайдкар — обычный JSON рядом с XML, а уходить по чужому пути из
        # него читатель не должен (тот же приём, что у списка прожжённых LUT).
        dst = os.path.join(os.path.dirname(xml_path), os.path.basename(name))
        if os.path.isfile(dst):
            return dst
    return _legacy_final_voice_path(xml_path)


def _legacy_final_voice_path(xml_path: str) -> str:
    """Старое имя итогового голоса `<стем>.voice.wav` — только для совместимости."""
    return os.path.splitext(xml_path)[0] + ".voice.wav"


def _versioned_final_voice_path(xml_path: str, key: str) -> str:
    """Версионное имя итогового голоса: `<стем>.voice.<key8>.wav` рядом с XML.

    `key8` — первые 8 символов ключа кеша (`final_voice_key`): сменили настройки —
    другое имя, и новый трек ложится РЯДОМ со старым, а не поверх него. Старый
    файл никто не держит «на запись», поэтому занятость его чужой рукой больше не
    ломает запекание.
    """
    return os.path.splitext(xml_path)[0] + ".voice." + key[:8] + ".wav"


def _final_voice_files(xml_path: str) -> list[str]:
    """Все версии итогового голоса клипа на диске (легаси и версионные).

    Шаблон экранируется (`glob.escape`): стем — это имя файла пользователя, и
    квадратная скобка в нём иначе превратила бы обход в поиск совсем не того.
    """
    stem = glob.escape(os.path.splitext(xml_path)[0])
    found = glob.glob(stem + ".voice*.wav")
    legacy = _legacy_final_voice_path(xml_path)
    if legacy not in found:
        found.append(legacy)
    return found


def _prune_final_voice(xml_path: str, keep: str) -> None:
    """Убрать ПРОЧИЕ версии итогового голоса клипа (занятую пропустить молча).

    Копии копятся на каждую смену настроек, а час стерео — это десятки мегабайт
    рядом с XML: держать их все нельзя. Удаление best-effort — занятый файл
    (плеер, отдача `/media`, открытый проект) удалится в следующий раз; из-за него
    запекание падать не должно, оно и было сломано ровно этим.
    """
    keep_name = os.path.normcase(os.path.basename(keep))
    for path in _final_voice_files(xml_path):
        if os.path.normcase(os.path.basename(path)) == keep_name:
            continue
        try:
            os.remove(path)
        except OSError:
            pass                      # занят читателем — уберём в следующий раз


def _final_meta_path(xml_path: str) -> str:
    """Сайдкар итогового голоса: ключ кеша, из которого он запечён, файл и исходник."""
    return os.path.splitext(xml_path)[0] + ".voice.json"


def final_voice_key(cam1: str, fx: Any) -> str:
    """Ключ запечённого итогового голоса: имя трека в кеше обработки.

    Он же лежит в сайдкаре `.voice.json` рядом с XML. Ключ, а не «есть файл»:
    настройки сменили — трек запечён под другие, и подхватывать его нельзя.
    """
    return os.path.basename(cache_path(cam1, fx))


def final_voice_ready(xml_path: str, cam1: str, fx: Any) -> bool:
    """Итоговый голос запечён ИМЕННО под эти настройки?

    Сверка тем же ключом, что у `ensure_final_voice`: сайдкар `.voice.json` с ключом
    кеша плюс ФАЙЛ, на который сайдкар указывает (резолвер `final_voice_path`).
    Ключ без файла (или чужой ключ) — «не готов»: трек собран под другие настройки
    либо уже убран, и подхватывать его нельзя.
    """
    if not cam1:
        return False
    saved = json_load_soft(_final_meta_path(xml_path))
    if not isinstance(saved, dict) or saved.get("key") != final_voice_key(cam1, fx):
        return False
    return os.path.isfile(final_voice_path(xml_path))


def clip_final_fx(xml_path: str) -> dict[str, Any] | None:
    """Настройки обработки голоса клипа, если обработка ВКЛЮЧЕНА, иначе None.

    Спикер лежит в сайдкаре `<стем>.project.json` (поле `speaker` — так его пишет
    нарезка). ЭТО И ЕСТЬ ОДНО ПРАВИЛО на все двери: сборка AE
    (`final_voice_for_build`), запекание в фоне превью (api/previewproxy),
    читатели XML/DRP/черновика (`clip_voice_wav`) решают по нему, нужен ли клипу
    обработанный голос. Спикера нет или обработка пуста (шумодав выключен и
    включённых плагинов нет) — None: запекать нечего, и всё идёт со звуком камеры.

    Галочки «в итоговый трек» в профиле больше нет — решение одно (`voice_fx_on`),
    и старый профиль с `final=false` обработку не выключает.
    """
    proj = read_project(os.path.splitext(xml_path)[0] + ".project.json")
    if not proj:
        return None
    prof = speakers.load(proj.get("speaker"))
    if not isinstance(prof, dict):
        return None
    fx = normalize_fx(prof.get("voice_fx"))
    # Пустая обработка — это не «обработка без итогового трека», а её отсутствие:
    # запечённый трек был бы просто копией звука камеры, зато полным прогоном
    # ffmpeg на час.
    if not voice_fx_on(fx):
        return None
    return fx


def clip_cam1(xml_path: str) -> str:
    """Файл камеры 1 клипа из сайдкара нарезки ("" — нет сайдкара или камеры).

    Голос клипа считается по звуку ИМЕННО этого файла, и взять его больше неоткуда:
    сайдкар `<стем>.project.json` пишет нарезка (`core/omni_cut.py`), и там же
    лежит список камер. Читатель голоса обязан знать, по какому звуку печь, иначе
    «свежий трек под текущие настройки» испечь нечем.
    """
    proj = read_project(os.path.splitext(xml_path)[0] + ".project.json")
    if not proj:
        return ""
    cams = proj.get("cams")
    if not isinstance(cams, list) or not cams:
        return ""
    first = cams[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        path = first.get("path")
        return path if isinstance(path, str) else ""
    return ""


def clip_voice_wav(xml_path: str, emit: Callable[..., Any] = console_emit) -> str:
    """Итоговый голос клипа для ЧТЕНИЯ или пустая строка — одна дверь читателей.

    Читатели (Premiere XML — `core/xmlbuild`, `.drp` — `api/build.py`, черновой
    рендер — `core/draftrender`) не решают сами, нужен ли обработанный голос: они
    спрашивают ЭТУ функцию. Правило внутри одно — `clip_final_fx` (спикер клипа из
    сайдкара + `voice_fx_on`).

    Файла нет или он запечён ПОД ДРУГИЕ настройки — читатель получает СВЕЖИЙ:
    трек печётся здесь же, той же дверью, что у сборки (`ensure_final_voice`), с
    цепочкой VST под текущие ручки. Раньше читатель молча отдавал пустую строку, и
    проект уезжал со звуком камеры — «плагины настроил, а в XML их нет». Ошибка
    запекания — предупреждение в лог и пустая строка: голос это надстройка, ради
    него сборку не валим (звук тогда идёт с камеры).
    """
    fx = clip_final_fx(xml_path)
    if fx is None:
        return ""
    cam1 = clip_cam1(xml_path)
    if not cam1 or not os.path.isfile(cam1):
        return ""
    try:
        final, _cache = ensure_final_voice(xml_path, cam1, fx, emit=emit)
        return final
    except Exception as e:                       # noqa: BLE001 — сборка важнее голоса
        emit("! обработанный голос не подключён ({err}) — звук идёт с камеры", err=e)
        return ""


def clear_final_voice(xml_path: str, emit: Callable[..., Any] = console_emit) -> bool:
    """Убрать запечённый голос клипа (ВСЕ версии `<стем>.voice*.wav` и сайдкар).

    Обработка выключена — файлы наши (их писал `ensure_final_voice`), и, оставшись
    на диске, они бы продолжали звучать в превью и уезжать в XML: «выключил
    шумодав — звук исходный» иначе не выполнить. Версий бывает несколько (имя
    меняется вместе с настройками), поэтому убираются ВСЕ, а не та, что назвал
    резолвер: иначе прошлые запечённые треки остались бы рядом с XML навсегда.
    Кеш обработки (`_voicefx`) не трогаем — включили обратно, трек вернётся из
    него без повторного счёта.

    True — что-то убрали.
    """
    gone = False
    for path in _final_voice_files(xml_path) + [_final_meta_path(xml_path)]:
        try:
            os.remove(path)
            gone = True
        except FileNotFoundError:
            pass                                  # файла и не было — убирать нечего
        except OSError:
            pass                                  # занят плеером/сборкой — не повод падать
    try:
        from core import xmlbuild
        synced = xmlbuild.sync_xml_voice(xml_path, voice="")
    except Exception:  # noqa: BLE001 — сбой синхронизации не ломает очистку, сбой виден строкой ниже
        synced = False
    if not synced:
        # Файл голоса уже убран, а XML мог остаться со ссылкой на него: AE и Resolve
        # откроют пустой трек без предупреждения. Строка — единственный след.
        emit("! голос: убран, но XML не переключён на обычный звук — в нём может остаться ссылка на убранный файл")
    return gone


def restore_cache_from_final(cache: str, final: str) -> bool:
    """Вернуть трек в кеш из итогового файла рядом с XML; True — вернули.

    Кеш обработки — именно кеш, его чистят, а итоговый `<стем>.voice.<key8>.wav`
    рядом с XML остаётся: на него ссылается собранный проект AE. Превью играет
    НЕИЗМЕНЯЕМЫЙ файл кеша (`cache_path`), и его пропажа не должна ни отдавать
    браузеру мёртвый URL, ни запускать счёт заново на минуты: итоговый файл — та же
    самая запись, `ensure_final_voice` скопировал её из этого же ключа. Копия
    байт-в-байт.
    """
    if os.path.isfile(cache) or not os.path.isfile(final):
        return False
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    _copy_atomic(final, cache)
    return True


# Замки запекания итогового голоса по клипу: один счёт на клип, остальные ждут.
# Своя пара (замок клипа + общий на словарь), как у замков дорожки выше.
_FINAL_LOCKS: dict[str, threading.Lock] = {}
_FINAL_LOCKS_GUARD = threading.Lock()


def _final_lock(xml_path: str) -> threading.Lock:
    """Замок запекания ЭТОГО клипа (по realpath; создаётся при первой просьбе)."""
    try:
        key = os.path.normcase(os.path.realpath(xml_path))
    except OSError:
        key = os.path.normcase(os.path.abspath(xml_path))
    with _FINAL_LOCKS_GUARD:
        lock = _FINAL_LOCKS.get(key)
        if lock is None:
            lock = _FINAL_LOCKS[key] = threading.Lock()
        return lock


def _sync_xml_voice(xml_path: str, voice: str, emit: Callable[..., Any] = console_emit) -> bool:
    """Подменить аудиодорожку клипа в XML на путь голоса — сбой не валит запекание.

    Сбой виден строкой в `emit` (той же, что у запекания): без неё сборка уедет с
    прежним звуком, и никто не узнает, почему голос «не применился».
    """
    try:
        from core import xmlbuild
        synced = xmlbuild.sync_xml_voice(xml_path, voice=voice)
    except Exception:  # noqa: BLE001 — сбой синхронизации не валит запекание, сбой виден строкой ниже
        synced = False
    if not synced:
        emit("! голос: запечён, но XML не переключён на него — в сборке пойдёт прежний звук")
    return synced


def ensure_final_voice(xml_path: str, cam1: str, fx: Any,
                       emit: Callable[..., Any] = console_emit,
                       progress: Callable[[int, int], None] | None = None,
                       cancelled: Callable[[], bool] | None = None,
                       pid_of: Callable[[int], None] | None = None) -> tuple[str, str]:
    """Запечь голос клипа; вернуть ОБЕ его копии: (рядом с XML, в кеше обработки).

    Две копии — не удобство, а лечение живого дефекта. Превью играло файл рядом с
    XML — файл под ОДНИМ И ТЕМ ЖЕ именем, который перезаписывался на каждую смену
    настроек: браузер отдавал старый трек из кеша и склеивал куски РАЗНЫХ версий
    («сменил ручку — а звук прежний»), а на Windows замена файла, открытого
    сервером на отдачу (или чужим плеером), ещё и НЕ ПРОХОДИЛА: `os.replace` падал
    `PermissionError`, новый голос не появлялся вовсе, и это было молча. Поэтому
    превью играет НЕИЗМЕНЯЕМУЮ копию в кеше (`cache_path`: имя по содержимому
    настроек), а рядом с XML ложится ВЕРСИОННЫЙ файл
    `<стем>.voice.<key8>.wav` — имя меняется вместе с настройками, старый файл
    никто не трогает, и занятость его чужой рукой больше ничего не ломает.

    Копия, а не ссылка: файл рядом с XML читает After Effects, и он обязан
    пережить чистку `_voicefx` (это кеш). Ключ кеша и имя файла лежат рядом в
    `.voice.json`: тот же ключ — файл уже запечён, второй раз не копируем (час
    стерео — это гигабайты и минуты); файл кеша тогда на месте по определению ключа.
    Прочие версии голоса этого клипа убираются best-effort (`_prune_final_voice`).

    Счёт на клип ОДИН: заказов на один и тот же клип бывает два разом (превью шага 3
    и повторное нажатие), и второй ждёт замок клипа, а после него видит готовый
    файл (`final_voice_ready`) — считать заново нечего. Без замка оба успевали
    начать счёт и второй падал на занятом файле, хотя первый уже записал верный.

    `progress(i, n)` — ход счёта шумодава (у RoFormer он свой): по нему превью
    показывает, что работа идёт, а не «прокси 0 %». `cancelled` и `pid_of` — та же
    отмена: «Стоп» снимает счёт по PID, а не ждёт его конца.
    """
    if final_voice_ready(xml_path, cam1, fx):
        # Ключ тот же — файл кеша на месте ПО ОПРЕДЕЛЕНИЮ ключа: не рендерим и не ищем.
        return _ready_final_voice(xml_path, cam1, fx, emit=emit)
    with _final_lock(xml_path):
        # Соперник по этому клипу мог всё запечь, пока мы ждали замок: тогда готовое
        # берём как есть — второй счёт на тот же ключ был бы платой ни за что.
        if final_voice_ready(xml_path, cam1, fx):
            return _ready_final_voice(xml_path, cam1, fx, emit=emit)
        key = final_voice_key(cam1, fx)
        cache = render_cached(cam1, fx, emit=emit, progress=progress,
                              cancelled=cancelled, pid_of=pid_of)
        dst = _versioned_final_voice_path(xml_path, key)
        if not os.path.isfile(dst):
            # Файл ЭТОЙ версии на месте (имя — ключ настроек) — не переписываем: его
            # может держать плеер превью, а содержимое в нём ровно то же (запекание
            # кешируется тем же ключом). Так «потеряли сайдкар» чинится без замены
            # занятого файла — тем самым сбоем, ради которого имя и стало версионным.
            _copy_atomic(cache, dst)
        # Сайдкар — ПОСЛЕ появления файла: читатель, увидевший имя раньше файла,
        # получил бы мёртвый путь (резолвер отдаёт имя, только если файл на месте).
        # Битый сайдкар (final_voice_ready выше отдал «не готов») откладываем, а не затираем.
        bad = quarantine_unreadable(_final_meta_path(xml_path), valid=lambda d: isinstance(d, dict))
        if bad:
            emit("сайдкар голоса не прочитан — отложен в {path}", path=bad)
        atomic_json_dump(_final_meta_path(xml_path),
                         {"key": key, "src": cam1, "file": os.path.basename(dst)}, indent=1)
        _prune_final_voice(xml_path, keep=dst)
        emit("голос: итоговый трек запечён ({path})", path=dst)
        _sync_xml_voice(xml_path, dst, emit=emit)
        return dst, cache


def _ready_final_voice(xml_path: str, cam1: str, fx: Any,
                       emit: Callable[..., Any] = console_emit) -> tuple[str, str]:
    """Уже запечённый голос клипа: (рядом с XML, в кеше) — без счёта и копирования.

    Заодно best-effort приборка прочих версий (`_prune_final_voice`): прошлую
    версию мог держать плеер, и тогда её не убрали — «удалится в следующий раз»
    и есть этот заход.
    """
    dst = final_voice_path(xml_path)
    _sync_xml_voice(xml_path, dst, emit=emit)
    _prune_final_voice(xml_path, keep=dst)
    return dst, cache_path(cam1, fx)


def final_voice_for_build(xml_path: str, cam1: str,
                          emit: Callable[..., Any] = console_emit) -> str | None:
    """Путь итогового голоса для сборки AE или None (правило — `clip_final_fx`).

    Правило одно: спикер из сайдкара и включённая обработка (`voice_fx_on`). Нет
    правила — None: сборка идёт со звуком камеры, как раньше.

    Любая ошибка — предупреждение и None: голос это надстройка, из-за него проект
    AE не должен остаться несобранным. Саму строку этой ошибки читает сборка
    (`api/build.py`, `VOICE_FAIL_MARK`): по ней клип с включённой обработкой, но без
    подключённого голоса, попадает в ИТОГ сборки предупреждением с причиной, а не
    уезжает молча со звуком камеры.
    """
    if not cam1:
        return None
    try:
        fx = clip_final_fx(xml_path)
        if fx is None:
            return None
        # Путь РЯДОМ С XML: читатели сборки ходят в него, а не в кеш (тот чистят).
        final, _cache = ensure_final_voice(xml_path, cam1, fx, emit=emit)
        return final
    except Exception as e:                       # noqa: BLE001 — сборка важнее голоса
        emit("! обработанный голос не подключён ({err}) — звук идёт с камеры", err=e)
        return None


# --------------------------------------------------------------------------- #
# Прослушивание «было / стало»
# --------------------------------------------------------------------------- #
def _prune_preview(d: str) -> None:
    """Убрать превью старше суток: папка копится на каждое нажатие «Прослушать»."""
    limit = time.time() - PREVIEW_TTL
    try:
        names = os.listdir(d)
    except OSError:
        return                        # папки ещё нет — чистить нечего
    for name in names:
        path = os.path.join(d, name)
        try:
            if os.path.getmtime(path) < limit:
                os.remove(path)
        except OSError:
            pass  # файл уже убран (или занят плеером) — чистка кеша не повод падать


def preview(src: str, fx: dict[str, Any], start: float = 0.0,
            dur: float = PREVIEW_DUR) -> tuple[str, str]:
    """Два WAV для прослушивания: (как звучит сейчас, как звучит с обработкой).

    Имена — с хешем настроек и места: подкрутил силу шумодава или переставил
    плагины — считаем заново, вернулся к прежним — играем готовое.
    """
    d = os.path.join(VOICEFX_DIR, "preview")
    os.makedirs(d, exist_ok=True)
    _prune_preview(d)
    key = _digest(src, fx, round(float(start), 3), round(float(dur), 3))
    before = os.path.join(d, "orig_%s.wav" % key)
    after = os.path.join(d, "fx_%s.wav" % key)
    if not os.path.isfile(before):
        render(src, {}, before, start=start, dur=dur)      # пустые настройки = только извлечение
    if not os.path.isfile(after):
        render(src, fx, after, start=start, dur=dur)
    return before, after


# --------------------------------------------------------------------------- #
# VST3: список плагинов и окно настроек
# --------------------------------------------------------------------------- #
def vst3_dirs() -> list[str]:
    """Папки поиска VST3 по платформе + дополнительные из REELSI_VST3_DIRS.

    Windows — общая папка плагинов, Mac — системная и пользовательская, Linux —
    ~/.vst3 и /usr/lib/vst3. Дополнительные папки (свой набор плагинов на другом
    диске) перечисляются через os.pathsep, как PATH.
    """
    if sys.platform == "win32":
        prog_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
        dirs = [os.path.join(prog_files, "Common Files", "VST3")]
    elif sys.platform == "darwin":
        dirs = ["/Library/Audio/Plug-Ins/VST3",
                os.path.join(os.path.expanduser("~"), "Library", "Audio", "Plug-Ins", "VST3")]
    else:
        dirs = [os.path.join(os.path.expanduser("~"), ".vst3"), "/usr/lib/vst3"]
    extra = (os.environ.get("REELSI_VST3_DIRS") or "").split(os.pathsep)
    dirs += [p.strip().strip('"') for p in extra if p.strip()]
    return dirs


def _scan_vst3(base: str, found: list[str]) -> None:
    """Собрать пути *.vst3 в папке. Внутрь бандла не заходим.

    На Windows `.vst3` — это папка-бандл (`X.vst3/Contents/x86_64-win/X.vst3`):
    зайдя внутрь, мы нашли бы тот же плагин второй раз и с чужим именем.
    """
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return                        # папки нет или нет прав — это не ошибка списка
    for name in names:
        full = os.path.join(base, name)
        if name.lower().endswith(".vst3"):
            found.append(full)
        elif os.path.isdir(full):
            _scan_vst3(full, found)   # плагины раскладывают по подпапкам вендора


def _found_vst3() -> list[str]:
    """Пути всех найденных *.vst3 — обход папок без открытия самих плагинов.

    Обход дешёвый (имена файлов), а вот ЧИТАТЬ имена внутри файла нельзя: это
    загрузка чужой DLL в наш процесс (см. докстринг модуля). Поэтому здесь
    только пути, а имена собирает дочерний процесс.
    """
    paths: list[str] = []
    for d in vst3_dirs():
        if os.path.isdir(d):
            _scan_vst3(d, paths)
    return paths


def _scan_payload(path: str) -> dict[str, str]:
    """Элемент задания сканирования: путь и ключ кеша (путь + mtime + размер).

    Ключ считает родитель: по нему он же ищет ответ в кеше, и второй раз считать
    mtime в процессе-сканере нечем.
    """
    from core import voicefx_scan
    return {"path": path, "key": voicefx_scan.file_key(path)}


def _scan_attempt(work: str, job: list[dict[str, str]],
                  attempt: int) -> dict[str, dict[str, Any]]:
    """Одна попытка сканирования — дочерним процессом.

    Процесс пишет результаты на диск после КАЖДОГО плагина, поэтому падение или
    зависание не теряет уже прочитанное: `_rescan_vst3` читает файл и продолжает
    с того плагина, на котором остановились.

    Файл результатов у КАЖДОЙ попытки свой (`attempt`): плагин, на котором упал
    предыдущий процесс, помечен в том файле «начали и не закончили», и, если
    писать поверх, новая попытка приняла бы его за уже прочитанный. Улику о
    падении разбирает родитель, а не следующий процесс.

    Зависший плагин снимается по PID таймаутом (`_run_child`) — иначе процесс
    остался бы держать плагин и его потоки до перезагрузки, ровно то, от чего
    уходили. `ReelsiError` с `err="timeout"` здесь значит «этот плагин завис»:
    вызывающий помечает его «не грузится» и продолжает со следующего, а не
    отдаёт человеку пустой список из-за одного битого файла.

    Отмену здесь не спрашивают: сканирование запускает человек из интерфейса и
    ждёт список, отменять его нечем (у «Прослушать» и рендера своя отмена).
    """
    from core import voicefx_scan
    job_path = os.path.join(work, "scan.json")
    results = os.path.join(work, "results.%d.json" % attempt)
    atomic_json_dump(job_path, {"paths": job}, indent=1)
    run = _run_child(module_cmd("voicefx_scan", "--job", job_path, "--results", results),
                     "VST3 (список плагинов)", VST_SCAN_TIMEOUT, None)
    done = voicefx_scan.read_results(results)
    if not done and run.errors:
        # Ни одного ответа и ошибка: до списка дело не дошло (нет pedalboard или
        # битый пакет) — человеку нужна эта ошибка, а не пустой выпадающий список.
        raise run.errors[0]
    return done


def _check_package() -> None:
    """Проверить pedalboard процессом, ничего не сканируя.

    Нужно, когда сканировать нечего, а список всё равно собирается — при каждом
    открытии блока «Голос». Без пакета список бесполезен (грузить плагины нечем),
    и человеку нужна ошибка про пакет, а не выпадающий список, который молча
    ничего не делает. Работа при этом пустая: процесс только импортирует пакет.
    """
    run = _run_child(module_cmd("voicefx_scan", "--check"),
                     "VST3 (проверка пакета)", VST_SCAN_TIMEOUT, None)
    _raise_child(run, "VST3 (проверка пакета)")


def _rescan_vst3(paths: list[str]) -> None:
    """Досканировать то, чего нет в кеше на диске, — ДОЧЕРНИМ процессом.

    Список плагинов собирается ВНЕ процесса сервера, и это не только про потоки
    JUCE: один плагин из живого прогона уронил сканер обращением к памяти, а
    u-he Satin показал своё окно ошибки. Поэтому падение одного плагина не должно
    терять остальные:

    * процесс пишет ответ после каждого плагина (core/voicefx_scan), а перед
      загрузкой помечает его `ok: null` — по этой записи родитель и находит
      виновника падения;
    * упавший или зависший плагин ложится в кеш записью «не грузится» вместе с
      mtime и размером файла, и повторно не грузится, пока файл не изменился: иначе
      одно и то же падение ловилось бы при каждом открытии блока, а окно ошибки
      всплывало бы на экране у человека;
    * в лог уезжает строка с именем файла, а в списке его нет.

    Кеш пишет ТОЛЬКО этот код: у него в руках и результаты упавших процессов, и
    прежние записи. Второго писателя (в дочернем процессе) нет нарочно — гонка
    двух процессов за один файл состояния съела бы записи.
    """
    from core import voicefx_scan
    cache = voicefx_scan.cache_path()
    entries = voicefx_scan.read_cache(cache)
    pending = [_scan_payload(p) for p in paths
               if voicefx_scan.file_key(p) not in entries]
    if not pending:
        # Сканировать нечего, но пакет проверяем: список без pedalboard бесполезен
        # (грузить плагины нечем), и человеку нужна ошибка про пакет, а не пустой
        # выпадающий список. Проверка — тоже дочерним процессом: pedalboard тянет
        # в процесс JUCE, и серверу это не нужно даже ради «есть ли пакет».
        _check_package()
        return
    work = tempfile.mkdtemp(prefix="_voicefx_scan_")
    try:
        left = list(pending)
        # Заходов ровно столько, сколько плагинов, и это не «попытки на удачу»:
        # процесс, падающий на каждом плагине, кончается вместе со списком.
        for attempt in range(len(pending)):
            try:
                done = _scan_attempt(work, left, attempt)
            except ReelsiError as e:
                if e.vars.get("err") != "timeout":
                    raise
                # Плагин ЗАВИС: процесс снят по PID (время ждал человек). Помечаем
                # того, на ком встали, «не грузится» и читаем остальных — иначе
                # один битый файл отменял бы весь список.
                read_by_child = voicefx_scan.read_results(
                    os.path.join(work, "results.%d.json" % attempt))
                left = _mark_hung(cache, entries, left, read_by_child)
                if not left:
                    break
                continue
            if not done:
                break
            entries.update(_merge_scan(cache, entries, left, done))
            # «Прочитан» — тот, у кого в КЕШЕ есть запись `ok: true/false`.
            # Файл результатов каждой попытки свой, и улика о падении (`ok: null`)
            # остаётся только в нём: судить по ней нельзя — процесс всякий раз
            # начинается с начала списка и падал бы на том же плагине вечно.
            left = [x for x in left
                    if entries.get(x["key"], {}).get("ok") is None]
            if not left:
                break                     # прочитано всё: поднимать процесс не за чем
        if left:
            # Заходы кончились, а часть плагинов так и не прочитана: последний
            # заход, судя по всему, упал на первом же. Помечаем их «не грузится»,
            # чтобы блок не пытался грузить их при каждом открытии.
            for item in left:
                console_emit("VST3: плагин не грузится и пропущен — {name}",
                             name=os.path.basename(item["path"]))
                entries[item["key"]] = {"ok": False, "names": []}
            voicefx_scan.write_cache(cache, entries)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _mark_hung(cache: str, entries: dict[str, dict[str, Any]],
               left: list[dict[str, str]],
               read_by_child: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Пометить «не грузится» плагин, на котором процесс встал, — и вернуть остальных.

    Процесс перед загрузкой каждого плагина пишет `ok: null`; в файле зависшей
    попытки такая запись ровно одна — на плагине, который не отдал ответ. Он и
    есть виновник: помечаем его в кеше (с ключом файла — пока файл не изменился,
    второй раз его не грузят), а оставшиеся возвращаем вызывающему, чтобы он
    поднял процесс заново. Один зависший плагин не отменяет список целиком.

    Если `ok: null` нет вовсе (процесс встал до первого плагина или уже на
    разборе задания), виновника назвать нельзя — тогда помечаем весь остаток:
    следующий заход повторил бы то же самое.
    """
    from core import voicefx_scan
    stuck = [item for item in left
             if read_by_child.get(item["key"], {}).get("ok") is None]
    if not stuck:
        stuck = list(left)
    fresh = {item["key"]: {"ok": False, "names": []} for item in stuck}
    entries.update(fresh)
    voicefx_scan.write_cache(cache, entries)
    for item in stuck:
        console_emit("VST3: плагин не отвечает и пропущен — {name}",
                     name=os.path.basename(item["path"]))
    return [item for item in left if item["key"] not in fresh]


def _merge_scan(cache: str, entries: dict[str, dict[str, Any]],
                job: list[dict[str, str]],
                done: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Свести ответы процесса в записи кеша; написать кеш и сказать в лог.

    Запись `ok: null` значит «плагин начали и не закончили» — то есть на нём
    процесс и упал (или завис и был снят). Такой плагин помечается «не грузится»
    и в список не попадает, а остальные читаются дальше: один битый плагин не
    отменяет список целиком.

    Возвращает только НОВЫЕ записи — по ним вызывающий понимает, что ещё не
    прочитано, и поднимает процесс заново.
    """
    from core import voicefx_scan
    fresh: dict[str, dict[str, Any]] = {}
    for item in job:
        answer = done.get(item["key"])
        if answer is None:
            continue                      # до этого плагина процесс не дошёл — читаем в следующий раз
        if answer.get("ok") is True:
            fresh[item["key"]] = {"ok": True, "names": list(answer.get("names") or [])}
            continue
        if answer.get("ok") is None:
            console_emit("VST3: плагин не грузится и пропущен — {name}",
                         name=os.path.basename(item["path"]))
        fresh[item["key"]] = {"ok": False, "names": []}
    if fresh:
        entries.update(fresh)
        voicefx_scan.write_cache(cache, entries)
    return fresh


def list_vst3(refresh: bool = False) -> list[dict[str, str]]:
    """Найденные VST3: [{"path", "name", "title"}].

    `name` — имя плагина ВНУТРИ файла (нужно оболочкам вроде WaveShell: сам файл
    их не грузит), у одиночного плагина пусто. Оболочка даёт по элементу на
    каждое имя — в UI выбирают имя, а не обёртку.

    Имена читает дочерний процесс (core/voicefx_scan): JUCE оставляет в процессе
    потоки чужих плагинов навсегда, а список спрашивают при каждом открытии блока
    настроек голоса. Имена ложатся в кеш на диске, ключ — путь + mtime + размер:
    повторное открытие блока не запускает НИЧЕГО, а после установки нового
    плагина досканируется только он. Кеш в памяти процесса — поверх дискового.

    Фоном этот список не собирается нигде: сканирование — только явное действие
    (открытие блока «Голос» или кнопка «Обновить список»). Окно, которое показал
    САМ плагин (диалог лицензии, ошибка загрузки), подавить нельзя — оно висит на
    экране, пока его не закроют, и запускать такое при старте сервера нельзя.
    """
    global _VST3_CACHE
    if _VST3_CACHE is not None and not refresh:
        return [dict(x) for x in _VST3_CACHE]
    from core import voicefx_scan
    paths = _found_vst3()
    _rescan_vst3(paths)
    entries = voicefx_scan.read_cache(voicefx_scan.cache_path())

    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        item = entries.get(voicefx_scan.file_key(path))
        if item is None or item.get("ok") is not True:
            continue                  # файл не читается (чужой формат, битый) — пропускаем
        names = item.get("names") or []
        stem = os.path.splitext(os.path.basename(path))[0]
        # Больше одного имени — оболочка: она держит пачку плагинов, и грузить
        # надо конкретное имя, иначе pedalboard возьмёт первое попавшееся.
        for name in (names if len(names) > 1 else [""]):
            key = (os.path.realpath(path), name)
            if key in seen:
                continue              # плагин виден из двух папок (или из бандла)
            seen.add(key)
            out.append({"path": path, "name": name, "title": name or stem})
    out.sort(key=lambda x: (x["title"].lower(), x["path"].lower()))
    _VST3_CACHE = out
    return [dict(x) for x in out]


def _edit_fragment(src: str, fx: dict[str, Any], start: float, work: str,
                   emit: Callable[..., Any]) -> str:
    """Фрагмент клипа для живого прослушивания в окне плагина — путь к WAV.

    Обработка ровно одна: шумодав, если он включён (движок — из настроек, см.
    `_denoise_any`). VST-плагины сюда НЕ входят — их крутит и слышит сам процесс
    окна, в реальном времени; запечь их в фрагмент значило бы услышать цепочку
    дважды.

    Длительность — как у прослушивания «было/стало» (PREVIEW_DUR): человек слушает
    то же место клипа, что и кнопкой «Прослушать».
    """
    raw = os.path.join(work, "raw.wav")
    _extract(src, raw, max(0.0, float(start)), PREVIEW_DUR, emit)
    if fx["denoise"]["on"]:
        # Хвост, срезанный `-D` у deep-filter, добирается внутри `_denoise_any`:
        # без этого фрагмент короче запрошенного куска, и петля съезжает на 30 мс
        # каждый круг (у RoFormer длину держит смесь с исходником).
        return _denoise_any(raw, fx["denoise"], work, emit)
    return raw


def _edit_job(path: str, name: str, fx: Any, index: int, frag: str, device: str) -> dict[str, Any]:
    """Задание для процесса окна: фрагмент, ВСЯ цепочка и что в ней открыть.

    Цепочка едет целиком, вместе с выключенными плагинами: процесс окна ставит в
    звук только включённые (как `render`), но открыть человек вправе и выключенный —
    послушать его отдельно и потом включить. `index` — позиция в ЭТОМ списке.
    """
    chain = [{"path": p["path"], "name": p["name"], "state_b64": p["state"], "on": p["on"]}
             for p in normalize_fx(fx)["vst"]]
    return {"frag": frag, "chain": chain,
            "index": index if 0 <= index < len(chain) else -1, "device": device}


def system_output_devices() -> dict[str, Any]:
    """Список устройств вывода БЕЗ pedalboard — для запасного пути.

    «Какие устройства есть» — вопрос звуковой системы, а не плагинов, и раньше он
    падал ровно потому, что список собирал pedalboard (`core/voicefx_audio`). Без
    пакета панель голоса оставалась без выбора устройства вовсе, хотя выбрать
    «По умолчанию» можно и без него.

    На Windows имена читаются из реестра (MMDevices\\Render) — это тот же список,
    что показывает системный микшер, и лишних пакетов он не требует. Системное
    устройство так не назвать (реестр не говорит, какое из них выбрано), поэтому
    `default` пуст: пустое значение значит «как в системе», и это здесь правда.
    На прочих платформах перечислять нечем — пустой список БЕЗ ошибки: причину
    назовёт вызывающий.
    """
    if os.name != "nt":
        return {"devices": [], "default": ""}
    return {"devices": _win_output_devices(), "default": ""}


def _win_output_devices() -> list[str]:
    """Имена устройств вывода Windows из реестра диспетчера звука.

    Модуль и его функции берутся через `getattr`, а не обычным обращением: строгий
    `mypy` разбирает этот файл и под `--platform linux` (кросс-проверка CI), где у
    `winreg` нет ни `OpenKey`, ни `HKEY_LOCAL_MACHINE`, и обычные обращения дали бы
    ошибки в чужом окружении. Зовётся только на Windows (см. `system_output_devices`).

    `importlib`, а не `import winreg`: обычный import под платформой linux в mypy —
    «не найден модуль», и это тоже упало бы в чужом окружении.
    """
    import importlib
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        return []
    # Поимённые локальные: `Any` — то, что `getattr` вернул из модуля без стубов.
    hkey_const: Any = getattr(winreg, "HKEY_LOCAL_MACHINE", None)
    open_key: Any = getattr(winreg, "OpenKey", None)
    enum_key: Any = getattr(winreg, "EnumKey", None)
    query_info: Any = getattr(winreg, "QueryInfoKey", None)
    query_val: Any = getattr(winreg, "QueryValueEx", None)
    if hkey_const is None or open_key is None or enum_key is None \
            or query_info is None or query_val is None:
        return []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    names: list[str] = []
    try:
        with open_key(hkey_const, base) as root:
            for i in range(query_info(root)[0]):
                try:
                    guid = enum_key(root, i)
                    ep = os.path.join(base, guid, "Properties")
                    with open_key(hkey_const, ep) as props:
                        name = str(query_val(
                            props, "{a45c254e-df1c-4efd-8020-67d146a850e0},2")[0])
                except OSError:
                    continue
                if name and name not in names:
                    names.append(name)
    except OSError:
        return []
    return names


def output_devices(refresh: bool = False) -> dict[str, Any]:
    """Устройства вывода звука для живого прослушивания: {devices, default}.

    Список спрашивают при каждом открытии блока настроек, а его сбор поднимает
    аудиосистему целиком — потому кеш, как у списка VST3.

    Собирает его ДОЧЕРНИЙ процесс (core/voicefx_audio): pedalboard работает
    поверх JUCE и тянет в процесс нативный код с фоновыми потоками — в сервере
    интерфейса этому делать нечего (та же причина, что у списка плагинов).

    БЕЗ pedalboard список всё равно отдаётся — запасным путём (реестр Windows,
    `system_output_devices`), а не ошибкой про плагины: устройства вывода — это
    звуковая система, и включать их в одну судьбу с VST3 нельзя. Почему список
    пуст или неполон, знает `devices_reason()` — отдельной дверью, а не полем
    ответа: контракт `{devices, default}` у этого роута был и остаётся.
    """
    global _DEVICES_CACHE
    if _DEVICES_CACHE is not None and not refresh:
        return {"devices": list(_DEVICES_CACHE["devices"]),
                "default": _DEVICES_CACHE["default"]}
    what = "Устройства вывода"
    try:
        run = _run_child(module_cmd("voicefx_audio", "--devices"),
                         what, DEVICES_TIMEOUT, None)
        _raise_child(run, what)
        data = run.answer()
        names = [str(n) for n in (data.get("devices") or [])]
        default = str(data.get("default") or "")
    except ReelsiError as e:
        if e.code != "vst_unavailable":
            raise
        # Пакета нет — но устройства перечисляются и без него: звук камеры и
        # «По умолчанию» обязаны работать у человека без плагинов.
        names, default = _devices_without_plugins()
    _DEVICES_CACHE = {"devices": names, "default": default}
    return {"devices": list(names), "default": default}


# Причина, по которой список устройств отдан запасным путём (пусто — обычный путь).
# Модульная переменная, а не поле ответа `output_devices`: поле меняло бы контракт
# роута `{devices, default}`, а причина нужна ровно одному вызывающему — панели.
_DEVICES_REASON = ""


def devices_reason(refresh: bool = False) -> str:
    """Почему список устройств может быть неполон (`''` — обычный путь).

    Без pedalboard устройства перечисляет звуковая система, и человеку надо
    сказать, что именно он теряет; при живом пакете причина пуста.
    """
    if _DEVICES_CACHE is None or refresh:
        output_devices(refresh)
    return _DEVICES_REASON


def _devices_without_plugins() -> tuple[list[str], str]:
    """Запасной путь списка устройств: (устройства, умолчание).

    Заодно ставит `_DEVICES_REASON` — то, ЧТО именно человек теряет без пакета:
    с ним панель говорит «плагины недоступны», а не молчит про пустой список.
    """
    global _DEVICES_REASON
    whose = system_output_devices()
    names = [str(n) for n in whose["devices"]]
    default = str(whose["default"] or "")
    # Текст переводится по ключу целиком (`t(d.reason)` на фронте), поэтому он один и
    # без подстановок: подробность ошибки уже сказана выше самим исключением.
    _DEVICES_REASON = ("Нет пакета pedalboard — плагины VST3 недоступны, устройства "
                       "вывода показаны звуковой системой: pip install pedalboard")
    return names, default


def edit_plugin(path: str, name: str = "", state_b64: str = "", *,
                src: str = "", start: float = 0.0, fx: Any = None,
                index: int = -1, device: str = "") -> str:
    """Открыть окно настроек плагина и вернуть новое состояние (base64).

    Отдельным ПРОЦЕССОМ и новой сессией: show_editor() из JUCE обязан идти в
    главном потоке, а Flask живёт в рабочих — из потока окно не открывается.
    Состояние едет файлами (raw_state — это байты, в аргументах командной строки
    ему делать нечего).

    `src` включает ЖИВОЕ прослушивание: пока окно открыто, фрагмент клипа играет по
    кругу через всю цепочку, и ручка в окне слышна сразу. Для этого в процесс окна
    едет файл задания (`--job`): фрагмент, цепочка целиком, индекс открываемого
    плагина и устройство вывода. Без `src` поведение прежнее — окно без звука.
    """
    work = tempfile.mkdtemp(prefix="_voicefx_edit_")
    try:
        state_in = os.path.join(work, "in.bin")
        state_out = os.path.join(work, "out.bin")
        cmd = module_cmd("voicefx_editor")
        if src:
            frag = _edit_fragment(src, normalize_fx(fx), start, work, console_emit)
            job = os.path.join(work, "job.json")
            atomic_json_dump(job, _edit_job(path, name, fx, index, frag, device), indent=1)
            cmd += ["--job", job]
        else:
            cmd += ["--path", path, "--name", name]
            raw = base64.b64decode(state_b64) if state_b64 else b""
            if raw:
                with open(state_in, "wb") as f:
                    f.write(raw)
                cmd += ["--state-in", state_in]
        cmd += ["--state-out", state_out]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=EDIT_TIMEOUT, env=child_env())
        except subprocess.TimeoutExpired:
            raise ReelsiError(umsg("voicefx_edit_failed",
                                   f"Окно плагина не закрылось за {EDIT_TIMEOUT} с", err="timeout"))
        except OSError as e:
            raise ReelsiError(umsg("voicefx_edit_failed",
                                   f"Редактор плагина не запустился: {e}", err=str(e)))
        if r.returncode != 0:
            lines = [ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
            tail = lines[-1] if lines else f"код возврата {r.returncode}"
            raise ReelsiError(umsg("voicefx_edit_failed", f"Плагин не открылся: {tail}", err=tail))
        if not os.path.isfile(state_out):
            raise ReelsiError(umsg("voicefx_edit_failed",
                                   "Плагин закрылся, не отдав состояние", err=state_out))
        with open(state_out, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Живое окно плагина: сессия, трек всего клипа, автосохранение состояния
# --------------------------------------------------------------------------- #
# Окно плагина открывает ОТДЕЛЬНЫЙ процесс (`core/voicefx_editor`), и сервер
# держит с ним связь, пока оно открыто: по ней едут команды звука (play/seek/
# pause — «звук идёт вместе с видео превью»), а по закрытию забирается состояние
# плагина. Сессия живёт в процессе сервера: два окна одновременно — это два
# независимых процесса и две записи (GPU один, но окна не конфликтуют).
@dataclass
class EditorSession:
    """Открытое окно плагина: процесс, каналы связи и что известно о звуке.

    Отдельным типом, а не словарём: у сессии есть ЖИЗНЕННЫЙ ЦИКЛ (окно открыто →
    команды → закрытие → состояние → запись профиля), и разложить его по
    вызывающим значило бы повторять один и тот же разбор в трёх местах.
    """

    sid: str
    work: str
    proc: subprocess.Popen[Any]
    state_out: str
    track_file: str
    chain: list[dict[str, Any]]
    index: int
    speaker: str
    fx: dict[str, Any]
    device: str
    start: float
    src: str
    t0: float
    opened_at: float = 0.0            # когда окно реально показалось (замер)
    track_ready: bool = False
    track_input: str = ""             # какой вход сейчас звучит: "raw" или "denoised"
    state: str = ""                   # состояние плагина в base64 (после закрытия)
    exit_code: int | None = None
    done: bool = False
    saved: bool = False               # профиль спикера записан (автосохранение окна)
    error: str = ""
    # Живой хост: окно ему не обязательно (`headless`), а про пропущенный плагин и
    # закрытое окно сервер узнаёт из файла событий (`events_file`) — свой stdout
    # хост отдаёт в `DEVNULL`, и читать его некому.
    headless: bool = False
    events_file: str = ""
    skipped: list[dict[str, str]] = field(default_factory=list)
    # Почему звук хоста НЕ идёт (устройство не приняло частоту, поток отвалился).
    # Пусто — звук идёт; непусто — страница обязана вернуть звук СЕБЕ, а не глушить
    # свой голос в пользу хоста, который молчит.
    audio_error: str = ""
    events_seen: int = 0              # сколько строк файла событий уже разобрано
    window: bool = False              # окно плагина открыто прямо сейчас
    state_seen: bytes = b""           # состояние, уже отданное на запись профиля
    states: list[dict[str, str]] = field(default_factory=list)   # последний dump_states
    states_seq: int = 0               # сколько событий `states` уже разобрано
    open_path: str = ""               # плагин, чьё окно открывали (путь, не индекс)
    last_poll: float = 0.0            # когда страница в последний раз спрашивала о хосте
    stopped: bool = False             # хост снят НАМИ (`live_stop`), а не ушёл сам


VOICE_SESSIONS: dict[str, EditorSession] = {}
VOICE_SESSIONS_LOCK = threading.Lock()
# Сколько сессия живёт в реестре уже закрытой: фронт успевает забрать состояние и
# показать «сохранено». Дольше держать нечего — окна нет, временная папка убрана.
SESSION_TTL = 15 * 60
# Как часто сервер заглядывает в состояние живого хоста, пока тот играет: по этому
# же опросу забирается состояние плагина по закрытию окна и читается файл событий.
# 0.2 с — незаметно для человека («закрыл окно — настройки уже у спикера») и дёшево.
STATE_POLL = 0.2
# Хост без окна живёт, пока страница о нём спрашивает (опрос ~2 раза в секунду). Вкладку
# закрыли без «погасить» (крах, F5 в неудачный момент) — хост, играющий в пустую комнату
# и держащий плагины (а у некоторых это ядра процессора), сам снимается по этому молчанию.
HOST_IDLE_TTL = 180.0
# Сколько ждём событие `states` (состояния ВСЕХ плагинов) перед гашением хоста. Число
# маленькое нарочно: гашение идёт на закрытии превью и на смене клипа, и ждать там
# долго нельзя. Замер: хост отвечает на `dump_states` за единицы миллисекунд, 2 с —
# потолок на «хост уже мёртв, а команда ушла в закрытую трубу».
DUMP_STATES_WAIT = 2.0


def _b64(raw: bytes) -> str:
    """Состояние плагина в том виде, в каком оно лежит в профиле спикера.

    Одна дверь на все места, где состояние едет base64 (события хоста `state` и
    `states`, чтение файла состояния): разъехавшиеся кодировки дали бы профиль,
    который `normalize_fx` молча выбросил бы как мусор.
    """
    return base64.b64encode(raw).decode("ascii")


def _session_new(work: str, proc: subprocess.Popen[Any], **kw: Any) -> EditorSession:
    """Завести сессию: id — номер процесса (он же и признак «окно открыто»).

    Номер процесса, а не счётчик: он один на живое окно, а по нему же ищутся окна
    WinAPI (`core/voicefx_win`), то есть второй идентификации не заводится.
    """
    sid = str(proc.pid)
    now = time.time()
    return EditorSession(sid=sid, work=work, proc=proc, t0=now, last_poll=now, **kw)


def live_session(sid: str) -> EditorSession | None:
    """Сессия по id или None: нет — значит окна уже нет (закрыто/не открывалось)."""
    with VOICE_SESSIONS_LOCK:
        return VOICE_SESSIONS.get(str(sid or ""))


def live_sessions() -> list[EditorSession]:
    """Все живые сессии — по ним видно, открыто ли окно (и чьё)."""
    with VOICE_SESSIONS_LOCK:
        return list(VOICE_SESSIONS.values())


def live_command(session: EditorSession, cmd: dict[str, Any]) -> bool:
    """Отправить команду в процесс окна; False — канал уже закрыт (окно закрылось).

    Строкой JSON в stdin: контракт описан в `core/voicefx_editor` (play/seek/
    pause/track/stop). Ошибка записи — это «окно закрыли», а не сбой сервера: и
    игра, и перемотка в этот момент просто не нужны.
    """
    stream = session.proc.stdin
    if stream is None or session.proc.poll() is not None:
        return False
    try:
        stream.write(json.dumps(cmd, ensure_ascii=False) + "\n")
        stream.flush()
        return True
    except (OSError, ValueError):
        return False


def live_track(session: EditorSession, path: str, at: float | None = 0.0) -> bool:
    """Сменить трек в открытом окне (шумодав досчитал весь клип).

    Сначала команда, потом запись указателя: команда несёт сам путь WAV, а
    указатель — для того, кто стартовал ПОЗЖЕ (второй процесс окна, перезапуск):
    без него новый трек не подхватился бы, пока окно не переоткроют.
    """
    # `at=None` — «место не называю»: хост остаётся там, где стоит (замена дорожки на
    # ходу, когда сменили настройки шумодава), а не прыгает на стартовую позицию.
    cmd: dict[str, Any] = {"cmd": "track", "path": path}
    if at is not None:
        cmd["at"] = float(at)
    sent = live_command(session, cmd)
    if session.track_file:
        atomic_json_dump(session.track_file,
                         {"track": path, "at": float(at if at is not None else 0.0)}, indent=1)
    return sent


def live_chain(session: EditorSession, fx: Any) -> bool:
    """Перестроить цепочку живого хоста НА ЛЕТУ: шумодав при этом не считается.

    Дорожка шумодава от цепочки не зависит (её ключ — только исходник и настройки
    шумодава), поэтому добавление, снятие галки, порядок и новая ручка едут одной
    командой: процесс хоста пересобирает доску, а поток вывода остаётся тем же —
    звук не рвётся дольше самой перестройки (core/voicefx_editor._apply_chain).

    Цепочка едет ЦЕЛИКОМ, вместе с выключенными плагинами: их держат загруженными,
    и включение галки не оплачивается вторыми секундами загрузки. Список плагинов
    сессии обновляется здесь же — по нему потом ищется плагин, чьё окно открыли
    (api/voicefx.py:_save_live): панель могла переставить строки, и записать
    состояние одному плагину вместо другого — это тихо испортить чужую настройку.
    """
    norm = normalize_fx(fx)
    items = [{"path": p["path"], "name": p["name"], "state_b64": p["state"],
              "on": p["on"]} for p in norm["vst"]]
    old_dn = normalize_fx(session.fx)["denoise"]
    sent = live_command(session, {"cmd": "chain", "chain": items})
    if sent:
        session.chain = [dict(p) for p in norm["vst"]]
        session.fx = norm
        if norm["denoise"] != old_dn and session.src:
            # Сменили настройки САМОГО шумодава: дорожка хоста устарела. Пока новая
            # считается (или берётся из кеша), хост молчит — иначе старый голос играл
            # бы поверх звука страницы, — и подхватит её командой `track` на месте.
            live_command(session, {"cmd": "pause"})
            session.track_ready = False
            threading.Thread(target=_prepare_track, args=(session, True),
                             name="voicefx-track", daemon=True).start()
    return sent


def live_dump_request(session: EditorSession) -> int:
    """Попросить хост отдать состояния всех плагинов; вернуть «до» — метку разбора.

    Метка (сколько событий `states` уже разобрано) нужна ожиданию: файл событий
    общий и растёт, и без неё легко принять за ответ СТАРОЕ событие.
    """
    before = session.states_seq
    session.states = []
    live_command(session, {"cmd": "dump_states"})
    return before


def live_dump_wait(session: EditorSession, before: int,
                   timeout: float = DUMP_STATES_WAIT) -> list[dict[str, str]]:
    """Дождаться события `states` после метки `before`; нет его — пустой список.

    Ждём недолго: окно могло закрыться между командой и обработкой, а хост — уйти
    сам. Пустой ответ — это честное «отдать нечего», и профиль по нему не пишется.
    """
    deadline = time.time() + max(0.0, float(timeout))
    step = 0.02
    while time.time() < deadline:
        live_events(session)
        if session.states_seq > before:
            return list(session.states)
        if session.proc.poll() is not None:
            break                      # хост ушёл — ждать больше нечего
        time.sleep(step)
        step = min(0.1, step * 2)      # первый ответ близко, дальше ждём реже
    live_events(session)               # последний взгляд: событие могло приехать на исходе
    return list(session.states) if session.states_seq > before else []


def live_dump_states(session: EditorSession, timeout: float = DUMP_STATES_WAIT
                     ) -> list[dict[str, str]]:
    """Забрать состояние ВСЕХ загруженных плагинов у живого хоста.

    Состояние знает ТОЛЬКО процесс хоста, и до этой двери оно уезжало в профиль
    спикера лишь по закрытию окна. А хост гасят и по другим поводам (закрыли
    превью, сменили клип, ушли с шага), причём иногда — с открытым окном: там
    `live_stop` снимает процесс по PID, и накрученное в окне пропадало. Поэтому
    перед гашением хост обязан отдать ВСЁ, что у него загружено.

    Команда `dump_states`, ответ — событие `states` в файле событий (`live_events`
    его разбирает). Отдаём разобранное как есть: запись профиля делает сервер.
    """
    return live_dump_wait(session, live_dump_request(session), timeout)


def live_open_editor(session: EditorSession, index: int, path: str = "") -> bool:
    """Открыть окно плагина в УЖЕ ИГРАЮЩЕМ хосте — панель того же звука.

    Звук не прерывается и процесс не перезапускается: окно рисует ГЛАВНЫЙ поток
    хоста (`core/voicefx_editor._open_editor`), а звук живёт в рабочем и команд не
    ждёт. Подъём окна поверх всех — отдельным потоком: он ждёт ПОЯВЛЕНИЯ окна
    (плагин грузится секундами), а команда обязана вернуться сразу.

    Путь плагина едет вместе с индексом: у пропущенного при загрузке плагина индексы
    панели и хоста разъезжаются, и без пути «Настроить» открыло бы окно СОСЕДА.
    """
    session.index = int(index)
    session.open_path = str(path or "")
    sent = live_command(session, {"cmd": "open_editor", "index": int(index),
                                  "path": str(path or "")})
    if sent:
        session.window = True
        api = voicefx_win.cached()
        if api is not None:
            threading.Thread(target=_raise_window, args=(session, api),
                             name="voicefx-window", daemon=True).start()
    return sent


def live_stop(session: EditorSession,
              stop_hook: Callable[[EditorSession, list[dict[str, str]]], None] | None = None
              ) -> bool:
    """Погасить живой хост по PID: превью закрыли, клип сменили, плагины выключили.

    Обычная команда `stop` тут не годится: она гасит ЗВУК, но процесс остаётся жив
    (он ещё пригодится — окно могло быть открыто), и «после закрытия превью не
    осталось ни одного процесса» превратилось бы в «остался висеть». Гасим дерево
    по PID процесса хоста (`_kill`), чужой PID не трогаем: он наш от начала до конца.

    ПЕРЕД гашением хост отдаёт состояние ВСЕХ загруженных плагинов (`dump_states`), и
    `stop_hook` (сервер) пишет их в профиль спикера. Без этого гашение при открытом
    окне теряло бы накрученное: состояние знает только процесс хоста, а снятие по PID
    ответа уже не даёт. Хук зовётся ПОКА сессия жива (процесс ещё не снят).
    """
    if session.proc.poll() is not None:
        live_finish(session, session.state, session.proc.returncode)
        return False
    # Флаг ДО убийства: поток-наблюдатель увидит выход процесса и по флагу поймёт, что
    # ненулевой код — наша работа, а не падение плагина (иначе в лог уедет «состояние
    # не сохранено», хотя хост просто закрыли вместе с превью).
    session.stopped = True
    # СНАЧАЛА состояние ВСЕХ загруженных плагинов, потом гашение. Окно могло быть
    # открыто (закрыли превью, сменили клип, ушли с шага) — состояние знает только
    # процесс хоста, и, сняв его по PID без `dump_states`, мы потеряли бы всё, что
    # человек накрутил в окнах. Ждём событие недолго (см. DUMP_STATES_WAIT): хост
    # может быть уже мёртв, и это не повод не гасить.
    dumped = live_dump_states(session, DUMP_STATES_WAIT)
    if dumped and stop_hook is not None:
        stop_hook(session, dumped)
    try:
        _kill(session.proc, "живой звук")
    except Exception as e:                        # noqa: BLE001 — хост важнее отчётности
        session.error = f"{type(e).__name__}: {e}"
    live_finish(session, session.state, session.proc.returncode)
    return True


def live_events(session: EditorSession) -> None:
    """Разобрать НОВЫЕ строки файла событий хоста: что пропущено и что с окном.

    Хост живёт часами, а stdout у него уходит в `DEVNULL`: единственный канал, по
    которому сервер узнаёт про пропущенный плагин (событие `chain` со `skipped`),
    про открытое/закрытое окно (`editor_open`/`editor_closed`/`editor_failed`) и про
    сбой звука (`audio_error` — устройство не приняло частоту, поток отвалился), —
    этот файл. Читаем построчно и с запоминанием, сколько уже разобрано: файл
    растёт в течение сессии, и повторно разбирать старое нечего.
    """
    path = session.events_file
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines[session.events_seen:]:
        text = line.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("event") or "")
        if name == "chain":
            skipped = data.get("skipped")
            session.skipped = [dict(s) for s in skipped] if isinstance(skipped, list) else []
        elif name == "audio_error":
            # Звук хоста не идёт (устройство не приняло частоту, поток отвалился):
            # `track_ready` снимаем ВМЕСТЕ с причиной — страница по нему понимает, что
            # живой звук не работает, и возвращает звук своей дорожке или камере.
            # Иначе она глушит свой голос «потому что звучит хост» и человек слышит тишину.
            session.audio_error = str(data.get("reason") or "звука нет")
            session.track_ready = False
        elif name == "states":
            # Состояния ВСЕХ загруженных плагинов — по команде `dump_states` (её шлют
            # ПЕРЕД гашением хоста). Разбираем и складываем как есть: запись профиля
            # делает сервер (`_save_live`), ему пути плагинов и нужны.
            plugins = data.get("plugins")
            rows: list[dict[str, str]] = []
            for it in (plugins if isinstance(plugins, list) else []):
                if not isinstance(it, dict):
                    continue
                path = str(it.get("path") or "")
                state = str(it.get("state_b64") or "")
                if not path or not state:
                    continue
                try:
                    base64.b64decode(state, validate=True)
                except (binascii.Error, ValueError):
                    continue                      # битый base64 в профиль не уезжает
                rows.append({"path": path, "state_b64": state})
            session.states = rows
            session.states_seq += 1
        elif name == "editor_open":
            session.window = True
        elif name in ("editor_closed", "editor_failed"):
            session.window = False
    session.events_seen = len(lines)


def live_finish(session: EditorSession, state_b64: str, code: int | None) -> None:
    """Закрыть сессию: сохранить забранное состояние и убрать её из реестра.

    Запись профиля делает СЕРВЕР (api/voicefx.py): он один знает про спикеров и
    запекание, а ядро — про звук и плагины. Здесь только факт «окно закрылось,
    вот состояние» — и уборка за собой: временная папка живёт до этого момента,
    потому что в ней лежит трек, который процесс окна читает.
    """
    session.state = state_b64
    session.exit_code = code
    session.done = True
    proc = session.proc
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass  # канал уже закрыт (процесс ушёл) — закрывать нечего
    shutil.rmtree(session.work, ignore_errors=True)


def live_reap() -> None:
    """Убрать сессии, которые никто не забрал: иначе временные папки копятся."""
    limit = time.time() - SESSION_TTL
    with VOICE_SESSIONS_LOCK:
        for sid in [s for s, v in VOICE_SESSIONS.items() if v.done and v.t0 < limit]:
            VOICE_SESSIONS.pop(sid, None)


def _monitor_session(session: EditorSession,
                     on_close: Callable[[EditorSession], None] | None) -> None:
    """Вести живую сессию в своём потоке: поднять окно, досчитать трек, отдать состояние.

    Три дела одного потока и все — ФОНОВЫЕ: HTTP-запрос на открытие хоста обязан
    вернуться сразу («открывается долго» — это как раз про то, что он висел до
    закрытия окна). Порядок:

    1. окно поднимается поверх всех, как только появится (`voicefx_win`), и
       замеряется «от клика до окна» — если окно вообще просили: живой хост превью
       поднимается БЕЗ окна, и поднимать там нечего;
    2. параллельно считается трек всего клипа после шумодава и ДО плагинов — из
       общего кеша дорожки, поэтому повторное открытие хоста его не ждёт;
    3. состояние плагина забирается СРАЗУ по закрытию окна (`_watch_live`), а не по
       выходу процесса: хост продолжает играть, и ждать его конца значило бы
       записать профиль спикера только тогда, когда превью закрыли;
    4. по выходу процесса сессия закрывается, и состояние уезжает вызывающему
       (`on_close`) — если оно вообще есть: у хоста без окна записывать нечего.

    Сбой любого шага не должен оставить сессию висеть вечно: исключение уезжает
    в `session.error`, и окно всё равно закрывается — просто без сохранения.
    """
    try:
        # Подъём окна — ОТДЕЛЬНЫМ потоком: он ждёт появления окна до 20 с (плагин
        # грузится не мгновенно), а выход процесса надо заметить сразу. В одном
        # потоке ожидание окна задержало бы и запись профиля по закрытию. Живому
        # хосту превью окно не нужно вовсе (`index < 0`) — там поднимать нечего.
        api = voicefx_win.cached()
        if api is not None and session.index >= 0:
            threading.Thread(target=_raise_window, args=(session, api),
                             name="voicefx-window", daemon=True).start()
        prep = threading.Thread(target=_prepare_track,
                                args=(session,), name="voicefx-track", daemon=True)
        prep.start()
        _watch_live(session, on_close)
        code = session.proc.wait()
        live_events(session)              # последние события хоста: он мог выйти, не дождавшись опроса
        if session.stopped:
            live_finish(session, session.state, 0)
            return
        if code == 0:
            fresh = _state_now(session.state_out)
            if fresh and fresh != session.state_seen:
                session.state_seen = fresh
                session.state = base64.b64encode(fresh).decode("ascii")
        elif not session.error:
            # Ненулевой код — падение плагина, а не «человек закрыл окно»: у закрытия
            # код 0. Причину пишем, состояния не будет.
            session.error = t("Плагин закрылся с кодом {code}", code=code)
        live_finish(session, session.state, code)
        # Профиль пишем, только если состояние ЕСТЬ: хост без окна не отдаёт ничего,
        # и «сохранение» пустого состояния затёрло бы живые ручки спикера.
        if on_close is not None and session.state:
            on_close(session)
    except Exception as e:                        # noqa: BLE001 — поток не должен падать молча
        session.error = f"{type(e).__name__}: {e}"
        live_finish(session, "", None)
        if on_close is not None and session.state:
            on_close(session)


def _state_now(path: str) -> bytes:
    """Состояние плагина из файла сессии: пусто — окно ещё не закрывалось."""
    if not path:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _watch_live(session: EditorSession,
                on_close: Callable[[EditorSession], None] | None) -> None:
    """Вести живую сессию, ПОКА процесс жив: события хоста и состояние плагина.

    Хост живёт всё время, пока открыто превью, и окно в нём открывают по команде
    (`live_open_editor`). Поэтому состояние плагина забирается СРАЗУ по закрытию
    окна (`_state_now`), а не по выходу процесса: иначе настройки уехали бы в
    профиль спикера только тогда, когда превью закрыли, — то есть никогда, если
    человек закрыл только окно. Ради этого же опроса читается файл событий
    (`live_events`): по нему сервер узнаёт про пропущенный плагин и замечает, что
    окно закрылось, пока хост играет.

    Пишем профиль ТОЛЬКО по новому состоянию — файл состояния читается по кругу, а
    каждая запись профиля читает и пишет его целиком.
    """
    while session.proc.poll() is None:
        live_events(session)
        if (session.headless and not session.window
                and time.time() - session.last_poll > HOST_IDLE_TTL):
            # Страница молчит: вкладку закрыли, не погасив хост. Гасим сами, по PID.
            live_stop(session)
            return
        fresh = _state_now(session.state_out)
        if fresh and fresh != session.state_seen:
            session.state_seen = fresh
            session.state = base64.b64encode(fresh).decode("ascii")
            session.exit_code = None              # хост жив: это не падение, а закрытие окна
            if on_close is not None:
                on_close(session)
        time.sleep(STATE_POLL)


def _raise_window(session: EditorSession, api: voicefx_win.WinApi) -> None:
    """Найти окно процесса, поднять его поверх всех и замерить «от клика до окна».

    Отдельным потоком: ожидание окна — секунды, и в общем мониторе оно задержало бы
    и обнаружение закрытия, и запись профиля. Сбой подъёма окна — не сбой сессии:
    окно может остаться в фоне, но звук и сохранение работают.
    """
    try:
        result = voicefx_win.prepare_window(session.proc.pid, api=api)
        if result.get("windows"):
            session.opened_at = time.time()
    except Exception as e:                        # noqa: BLE001 — окно важнее украшений
        session.error = f"{type(e).__name__}: {e}"


def _prepare_track(session: EditorSession, keep_pos: bool = False) -> None:
    """Досчитать трек клипа (шумодав, БЕЗ плагинов) и отдать его процессу хоста.

    Трек — голос ВСЕГО клипа: превью шлёт «играть с этой секунды», и секунда эта
    отсчитывается от нуля камеры 1. Плагины в трек НЕ запекаются: их крутит и
    слышит сам процесс хоста в реальном времени — иначе цепочка слышна дважды.

    Кеш — ОБЩИЙ с запеканием превью (`denoise_cache_path`, ключ «исходник +
    настройки шумодава»): дорожка, уже посчитанная для превью или нарезки, для
    хоста не считается заново. Раньше здесь стоял `render_cached` с полным `fx`
    (пустая цепочка VST), а это ДРУГОЙ ключ — «того же» файла в кеше не было ни у
    одного клипа, и открытие окна запускало RoFormer на весь клип заново.
    Готовый трек едет процессу командой, а указатель остаётся на диске — по нему
    его подхватит и тот процесс, что стартовал позже.

    `keep_pos` — замена дорожки в уже играющем хосте (сменили настройки шумодава):
    место и паузу хост сохраняет, стартовую позицию не подставляем.

    ВХОД хост выбирает сам и всегда в одну сторону — к дорожке шумодава: посчитана
    (или считается) — играет ОНА, потому что в итог уедет именно она; сырой звук
    остаётся только тем, кому играть больше нечего (кеша нет — пока RoFormer
    считает, человеку нужно слышать голос, а не тишину).
    """
    try:
        if not session.src or not os.path.isfile(session.src):
            # Клипа нет — играть нечего: окно открыто ради ручек, трек не считаем
            # (иначе в лог уехала бы ошибка про несуществующий файл на ровном месте).
            return
        dn = normalize_fx(session.fx)["denoise"]
        dn_path = denoise_cache_path(session.src, dn)
        has_cache = os.path.isfile(dn_path) if dn_path else False
        is_preview = (session.index < 0)
        # ВХОД ХОСТА — дорожка шумодава, если она включена И уже посчитана: хост обязан
        # играть тот же голос, что уйдёт в итог, а не сырой звук камеры. Сырому звуку
        # остаётся ровно один случай — считать нечего было (кеша нет): тогда он играет
        # СРАЗУ (человеку есть что слушать, пока идёт шумодав), а на дорожку хост
        # пересаживается командой `track`, когда она появится.
        if not is_preview or keep_pos or not dn.get("on") or has_cache:
            track = denoise_track(session.src, dn, emit=console_emit)
            if normalize_fx(session.fx)["denoise"] != dn:
                # Пока считалось, настройки шумодава сменили ещё раз: эта дорожка уже
                # чужая, а свежую отдаст поток, запущенный той сменой. Отдай мы её — она
                # затёрла бы более новую.
                return
            session.track_ready = True
            session.track_input = "denoised" if dn.get("on") else "raw"
            # `keep_pos` — замена на ходу: хост остаётся на своём месте (`live_track` без `at`).
            if not live_track(session, track, None if keep_pos else session.start):
                # Хост ушёл, пока трек считался: файл-указатель всё равно пишем —
                # он наш, и по нему трек подхватит следующий процесс хоста.
                if session.track_file:
                    atomic_json_dump(session.track_file,
                                     {"track": track, "at": float(session.start)}, indent=1)
            return
        # Хост превью, дорожки шумодава в кеше нет — СНАЧАЛА отдать хосту сырой звук
        # камеры (тот же путь, что denoise_track при выключенном шумодаве: извлечение
        # ffmpeg с кешем), а готовую дорожку досчитать следом и пересадить хост на неё.
        raw_track = denoise_track(session.src, dict(dn, on=False), emit=console_emit)
        session.track_ready = True
        session.track_input = "raw"
        if not live_track(session, raw_track, session.start):
            if session.track_file:
                atomic_json_dump(session.track_file,
                                 {"track": raw_track, "at": float(session.start)}, indent=1)
        track = denoise_track(session.src, dn, emit=console_emit)
        if normalize_fx(session.fx)["denoise"] != dn:
            return
        session.track_ready = True
        session.track_input = "denoised" if dn.get("on") else "raw"
        if not live_track(session, track, None):
            if session.track_file:
                atomic_json_dump(session.track_file,
                                 {"track": track, "at": float(session.start)}, indent=1)
    except Exception as e:                        # noqa: BLE001 — звук важнее украшений
        session.error = f"{type(e).__name__}: {e}"
        console_emit("! живой звук хоста не посчитан ({err})", err=e)


def edit_plugin_live(path: str, name: str = "", *, src: str, start: float = 0.0,
                     fx: Any = None, index: int = -1, device: str = "",
                     speaker: str = "",
                     headless: bool = False,
                     gain_db: float = 0.0,
                     on_close: Callable[[EditorSession], None] | None = None) -> EditorSession:
    """Поднять живой хост клипа и вернуть сессию — НЕ дожидаясь закрытия окна.

    Отличие от `edit_plugin` ровно одно: запрос не висит, пока человек крутит
    ручки. Всё остальное — тот же процесс, то же состояние плагина, та же запись
    профиля по закрытию (её делает `on_close`). Поэтому «открыть окно» занимает
    секунды: ни шумодава, ни запекания в этом вызове нет — они идут фоном, а окно
    показывается сразу.

    `src` — файл камеры 1: по его звуку считается трек. Пустой `src` — хост без
    звука (прежнее поведение): человеку нечего слушать, но настроить плагин можно.

    `headless` — хост БЕЗ окна: звук превью идёт через цепочку вживую, а окно
    выбранного плагина открывается потом командой (`live_open_editor`) в этом же
    процессе. Так плагины едут вживую ВСЕГДА, а не только пока открыто окно.

    `path`/`name` не используются: хост грузит цепочку целиком (`fx`), а не один
    плагин. Оставлены в подписи потому, что вызов один на оба случая, и второе имя
    («открой окно плагина») читалось бы вызывающим как «а куда путь?».
    """
    norm = normalize_fx(fx)
    work = tempfile.mkdtemp(prefix="_voicefx_edit_")
    state_out = os.path.join(work, "out.bin")
    track_file = os.path.join(work, "track.json")
    events_file = os.path.join(work, "events.jsonl")
    # Пустой указатель: процесс хоста ждёт его появления, а окно уже открыто.
    atomic_json_dump(track_file, {}, indent=1)
    job = os.path.join(work, "job.json")
    atomic_json_dump(job, {
        "chain": [{"path": p["path"], "name": p["name"], "state_b64": p["state"],
                   "on": p["on"]} for p in norm["vst"]],
        "index": -1 if headless else int(index),
        "device": device,
        "track_file": track_file,
        "events_file": events_file,
        "start": max(0.0, float(start)),
        "gain_db": float(gain_db),
        "src": src,
        "speaker": speaker,
        # Хост без окна поднимается ДО «Играть»: пока картинка стоит, звук молчит,
        # даже если дорожка шумодава досчиталась посреди паузы.
        "paused": bool(headless),
    }, indent=1)
    cmd = module_cmd("voicefx_editor", "--job", job, "--live",
                     "--state-out", state_out)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", env=child_env(), **task_popen_kwargs())
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise ReelsiError(umsg("voicefx_edit_failed",
                               f"Редактор плагина не запустился: {e}", err=str(e)))
    # Право забрать передний план — ДО того, как окно появится: система выдаёт его
    # «сейчас или никогда», а окно плагина открывается уже после этого. Хосту без
    # окна оно не нужно, но и не вредит: окно откроют позже, тем же процессом.
    voicefx_win.allow_foreground(proc.pid)
    session = _session_new(work, proc, state_out=state_out, track_file=track_file,
                           chain=norm["vst"], index=-1 if headless else int(index),
                           speaker=speaker, fx=norm, device=device,
                           start=max(0.0, float(start)), src=src,
                           headless=bool(headless), events_file=events_file)
    if not headless and 0 <= session.index < len(session.chain):
        session.open_path = str(session.chain[session.index].get("path") or "")
    session.window = not headless and session.index >= 0
    with VOICE_SESSIONS_LOCK:
        VOICE_SESSIONS[session.sid] = session
    live_reap()
    threading.Thread(target=_monitor_session, args=(session, on_close),
                     name="voicefx-monitor", daemon=True).start()
    return session


def live_host(*, src: str, start: float = 0.0, fx: Any = None, device: str = "",
              speaker: str = "",
              gain_db: float = 0.0,
              on_close: Callable[[EditorSession], None] | None = None) -> EditorSession:
    """Поднять ЖИВОЙ ХОСТ клипа без окна: голос превью играет через цепочку.

    Это и есть «плагины — вживую ВСЕГДА»: пока открыто превью клипа с включёнными
    плагинами, звук идёт через них, а окно («Настроить») лишь показывает панель уже
    звучащего хоста. Дорожка шумодава считается фоном (`_prepare_track`, общий кеш) —
    хост поднимается сразу, а не через минуты счёта.

    Выключенных плагинов в звук не попадает ни одного — тогда хост не нужен вовсе, и
    превью играет дорожку шумодава в браузере: решает это вызывающий (`live_chain`
    умеет и пустую цепочку, а `core/voicefx_editor` на пустой цепочке без окна
    завершается сам).
    """
    return edit_plugin_live("", "", src=src, start=start, fx=fx, index=-1,
                            device=device, speaker=speaker, headless=True,
                            gain_db=gain_db,
                            on_close=on_close)

