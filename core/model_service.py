# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сервис моделей: GigaAM, CED вздохов и голова `emo` грузятся ОДИН раз на машину.

ЗАЧЕМ. До сих пор каждый ролик нарезки был своим процессом: свой `import torch`,
свой CUDA-контекст, своя загрузка весов GigaAM под файловым замком `.gpu` — и
выгрузка сразу после ролика. Замер стендом (`tools/bench_gpu_cut.py`) на десяти
роликах по 120 с звука: 147 с всего, медиана ожидания замка 55 с (максимум 123 с)
при счёте 3–6 с, а загрузка моделей 4.6–7.2 с — то есть БОЛЬШЕ самого счёта. Замок
честно сериализовал ролики, но весил дороже, чем экономил: карта простаивала между
участками, а веса читались с диска заново на каждый ролик.

ЧТО ЗДЕСЬ. Долгоживущий процесс, ОДИН на машину: держит веса GigaAM в памяти и
считает распознавание тем же кодом, что и нарезка
(`core.gigaam_cut.asr.transcribe_loaded` — общая функция «по готовой модели», без
копии логики). Ролик нарезки приходит сюда за словами и НЕ берёт общий файловый
замок: вместо него — слоты сервиса (сколько распознаваний идут разом). Замок
`.gpu` остаётся другим тяжёлым участкам (RoFormer, Whisper субтитров, рендер) — и
сервис перед каждым счётом сверяет свободную VRAM, чтобы чужой RoFormer и его не
вытеснил.

ВТОРАЯ ФАЗА: здесь же живут CED-tiny (голова вздохов) и голова `emo` (эмоция
фразы). Обе раньше читались ЗАНОВО в каждом ролике: вздохи — в процессе нарезки
под файловым замком `.gpu` (`core/breath.py`), эмоции — в процессе сборки на
каждый расчёт (`core/emphasis.py`). Замер десяти роликов: 94 с у фазы 1 против
64 с, и остаток ожидания (медиана 23 с) — ровно вздохи под общим замком, а эмоции
платили за чтение головы на каждый расчёт. Теперь обе модели держит сервис: клиент
шлёт звук или окно и получает числа, счёт ограничен теми же слотами, выгрузку
делает простой. Ключ кеша моделей — имя и ревизия (`mispeech/ced-tiny@<ревизия>`,
`emo`): сменишь ревизию в `core/breath.py` — сервис прочитает новую, а не отдаст
вероятности старыми весами.

ПОЧЕМУ VAD ОСТАЛСЯ В ПРОЦЕССЕ РОЛИКА. Silero VAD — ONNX на процессоре: карту он
не занимает вовсе, и гонять его через сервис значило бы платить за передачу звука
ради того, что и так считается рядом с признаками.

КАК ЗАВОДИТСЯ. Лениво, при первом запросе: нет живого сервиса — процесс ролика
запускает его отдельным процессом и подключается. Каталог состояния выбирает ОДНА
функция (`state_path`): адрес (`127.0.0.1:<порт>`), `authkey` и заявка на старт
лежат в одном файле рядом с файловым замком, права — только владельцу: слушатель
принимает лишь того, кто прочитал этот файл. Тот, кто сервис заводит, передаёт
готовый путь ребёнку переменной `REELSI_MODEL_SERVICE_STATE`, поэтому сервис и
клиент не могут разойтись по каталогу. Второй старт видит живой сервис и просто
подключается; мёртвый (файл есть, порт молчит) не убиваем по имени — просто заводим
новый на свободном порту, а файл переписываем. Адрес публикуется ПОСЛЕ того, как
слушатель открыт: в файле не бывает адреса, по которому никто не отвечает.

КАКОЕ УСТРОЙСТВО. `cuda`/`mps`/`cpu` выбирает `core.device.pick_device`, а ЧТО просить —
настройку «Где считать модели» (`core.aicut.config.model_device_cfg`: авто / видеокарта /
процессор). Настройка нужна двум людям: тем, у кого карты нет вовсе, и тем, кому карта
нужна под локальную LLM, — им модели выгоднее считать процессором. Смена настройки
НЕ требует рестарта сервера: в файле адреса лежит ключ устройства, клиент сверяет его
со своим и, если разошёлся, гасит старый сервис и заводит новый (см. `ensure_started`).

ВЕРСИЯ КОДА В АДРЕСЕ. В том же файле лежит отпечаток `_code_fingerprint()` — хеш
содержимого `model_service.py` и модулей, которыми он грузит веса (`breath`, `emphasis`,
`gigaam_cut.asr`, `gigaam_cache`, `device`). До него сервис жил до простоя и после
обновления кода отвечал СТАРЫМ кодом: правка, скажем, окна распознавания не доезжала до
машины, где сервис уже поднят, — и «поправил, а не работает» искали в правке. Сверяет
отпечаток клиент (и `webui.main` на старте сервера): разошёлся — старый гасится, новый
поднимается.

ВЫХОД НЕ ЗАВИСИТ ОТ ОС. Раньше сторожа простоя и `shutdown` будили приём
закрытием слушателя, и это работало только на Windows: там `close` прерывает
блокирующий `accept()`, а на Linux — нет, поток оставался в `accept` навсегда, и
сервис не выходил ни по простою, ни по просьбе (держал веса и видеопамять до
перезагрузки). Теперь решение о выходе принимает ФЛАГ (`_stopped`), а приём ждёт
соединения коротким сроком (`POLL_ACCEPT_S`) и проверяет флаг в цикле; вдобавок
`stop()` будит `accept` собственным подключением (`_Service.wake`) — чтобы выход
был немедленным, а не «в пределах срока опроса». Закрытие слушателя осталось —
порт освобождается, но ПЕРЕД ним приём снимается `shutdown(SHUT_RDWR)` на сокете
слушателя (`_Service._shutdown_listener`): на Linux `close()` из другого потока не
снимает сокет, пока поток приёма сидит в `accept()`, — ядро держит его и продолжает
ПРИНИМАТЬ новые подключения до конца срока опроса, то есть уже остановленный сервис
ещё отвечает клиенту. `shutdown` прекращает приём сразу и будит `accept`; на Windows
он безвреден. Боевой процесс после остановки потоков уходит жёстко
(`_exit_if_threads_hold`): если какой-то недемонический поток всё же держит
процесс, сервис обязан умереть, а не висеть с закрытым портом.

ОСТАНОВКА ЖДЁТ РАБОЧИХ ПОТОКОВ. `wait_until_stopped` возвращает True только
когда вышли И приём, И все рабочие потоки сервиса — обработчики запросов,
предзагрузка и сторож простоя; потоки наперечёт (`_Service._workers`). Без этого
«остановленный» сервис ещё работал: обработчик-демон дочитывал веса и клал модель
в общий на процесс кэш уже после `stop()`, а следующий сервис (в тестах — следующий
тест) находил её там и свои веса не читал. Поэтому же веса, дочитанные после
остановки, в кэш не публикуются (`_publish_model`).

ВЫГРУЗКА ПРИ РЕНДЕРЕ. Рендеру модели не нужны, а видеопамять нужна ему: `run_render_job`
перед стартом рендера зовёт `shutdown()`. Ошибка выгрузки — строка в журнале рендера,
а не отказ рендера: карту в худшем случае займёт чужой процесс, но ролик выйдет.

ПОЧЕМУ multiprocessing.connection, а не HTTP: это не публичный интерфейс, а
внутренняя шина между двумя процессами на одной машине — `Listener`/`Client` уже
несут и адрес, и `authkey`, и сериализацию ответа, отдельного протокола писать не
надо (`Connection` передаёт любой pickle: словари со словами, числа, None).

ПОЧЕМУ ПРОЦЕСС БЕЗ КОНСОЛИ, НО НЕ `DETACHED_PROCESS`. Сервис заводится
`CREATE_NO_WINDOW` (окна не появляется, консоль сервису не нужна), и это не
мелочь. Замер на десяти роликах по 120 с: с `DETACHED_PROCESS` счёт одного ролика
через сервис был 19.5 с против 2.9 с в процессе ролика — вшестеро хуже, хотя
устройство, `fp16` и число потоков у обоих одинаковы (проверено изнутри сервиса).
Виноват оказался не счёт, а ЗАПУСК ДОЧЕРНИХ ПРОЦЕССОВ: у процесса, оторванного
флагом `DETACHED_PROCESS`, каждый `CreateProcess` стоит ~0.4–0.9 с вместо ~0.05 с,
а GigaAM читает звук окна через `ffmpeg` — по запуску на каждое окно. Энкодер на
это время считает нормально (0.03–0.10 с на окно, как и в процессе ролика),
медленными были ровно окна: 0.43–0.51 с против 0.075 с. С `CREATE_NO_WINDOW`
окна снова 0.07–0.09 с. Плюс `DETACHED`-процесс теряет переданные ему потоки:
вывод сервиса не доходил до трубы родителя, то есть в журнал нарезки.

ЧЕГО ЗДЕСЬ НЕТ. Никакой логики распознавания: сервис зовёт общую функцию и
возвращает её результат. Подмена на время теста не нужна: тесты подставляют
фальшивый модуль `gigaam` (`exec_module`), и весь путь — слушатель, слоты, клиент,
ответ — идёт настоящий.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import random
import secrets
import socket
import subprocess
import sys
import threading
import time
from multiprocessing import connection
from multiprocessing.context import AuthenticationError
from typing import Any, Callable, Iterator, Protocol, Sequence

from core import device
from core import fileio, paths
from core.app_meta import child_env, console_emit, env, wrap_emit
from core.applog import get_logger
from core.jobstate import JOB_LOCK_PATH
from core.umsg import ReelsiError, cli_error

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Рядом с файловым замком — тот же каталог, что у `.gpu` и `job.lock`
# --------------------------------------------------------------------------- #
# Изолированные профили (`REELSI_JOB_LOCK`) и разные рабочие копии получают свой
# сервис: файл адреса и `authkey` лежат рядом с их локом, а не в общем месте.
_SETTINGS_SUFFIX = ".modelsvc.json"
# Переменная окружения с ГОТОВЫМ путём файла состояния. Её выставляет тот, кто
# заводит сервис (`_spawn` — ребёнку-сервису, стенд и изолированный профиль —
# всем), и по ней же его ищут клиенты: адрес, ключ и заявка на старт обязаны
# лежать в ОДНОМ файле у сервиса и у клиента. Разойдись они — клиент ждал бы
# адрес там, где сервис его не писал: сдался бы по таймауту старта, а запущенный
# им процесс жил бы до простоя и держал видеопамять (ровно этот дефект и был).
ENV_STATE = "MODEL_SERVICE_STATE"


def state_path_for(lock_path: str) -> str:
    """Файл состояния рядом с ЭТИМ локом — одна формула на сервис, клиентов и стенд.

    Тот же каталог, что у `.gpu`: `gpulock._gpu_lock_path()` берёт `JOB_LOCK_PATH`,
    и путь сервиса обязан жить там же — иначе тестовый профиль на 5098 стучался бы
    в боевой сервис чужой копии (в нём другой `ai_config` и другие веса).
    """
    return str(lock_path) + _SETTINGS_SUFFIX


def state_path() -> str:
    """Файл состояния ЭТОГО процесса: адрес, ключ и заявка — одна функция на всех.

    Путь берётся из `REELSI_MODEL_SERVICE_STATE`, если её выставил тот, кто завёл
    сервис (стенд с `--lock`, изолированный профиль, `_spawn` для ребёнка-сервиса),
    иначе — рядом с общим локом (`JOB_LOCK_PATH`). Одна дверь нарочно: каталог
    состояния у сервиса и у клиента не может разъехаться по порядку импортов.
    """
    own = _env_str(ENV_STATE)
    if own:
        return own
    return state_path_for(JOB_LOCK_PATH)


def _settings_path() -> str:
    """Файл состояния — прежнее имя `state_path` (его подменяют тесты)."""
    return state_path()


# --------------------------------------------------------------------------- #
# Сколько распознаваний идут разом (слоты)
# --------------------------------------------------------------------------- #
# Сколько видеопамяти занимает ОДНО распознавание: веса головы `v3_ctc` плюс
# рабочие буферы. `v3_ctc.ckpt` в кеше весов — 421 МиБ (замер файловой системы
# 2026-10-08), округляем вверх с запасом на активации.
# Больше одной головы в памяти сервис не держит: ключ кеша — имя головы, старая
# выгружается при смене.
ASR_VRAM_MIB = 512

# Запас, который обязан остаться СВОБОДНЫМ поверх цены слота. Чужой RoFormer,
# Whisper субтитров и рендер замок `.gpu` берут и без нашей проверки — но взять
# его они могут ровно в тот миг, когда мы уже считаем. На Windows переполнение
# VRAM не даёт честной ошибки: оно вешает машину целиком, без внятного отказа
# драйвера, поэтому 1 ГБ не «на всякий случай», а цена ошибки.
VRAM_RESERVE_MIB = 1024

# Потолок и пол слотов: больше четырёх распознаваний на одной карте смысла не
# имеют (счёт и так упирается в полосу памяти), ноль слотов — это отказ считать
# вовсе, а нужен хотя бы один.
MAX_SLOTS = 4
MIN_SLOTS = 1

# Сколько ждём, если свободной VRAM под слот сейчас нет (занял чужой RoFormer).
# Не падаем: владелец освободит карту сам, а распознавание — единственная дорога
# к словам нарезки. Опрос редкий: чаще nvidia-smi дёргать незачем.
VRAM_WAIT_S = 0.5
VRAM_WAIT_REPORT_S = 30.0

# Простой, после которого процесс сервиса завершается и отдаёт VRAM. Пять минут:
# короткая нарезка (следующий ролик сразу) сервис не перезапускает, а забытый
# процесс не держит карту до перезагрузки — её просят рендер и AE.
DEFAULT_IDLE_S = 300.0

# Перемычки для замеров и тестов. `SLOTS`: замер «сколько слотов при такой
# карте» и тест «N=1 ждёт, N=2 разом» — без пересчёта по живому железу. `IDLE`:
# тест простоя не должен ждать пять минут. `DEVICE`: тест сервиса не должен
# трогать живую карту (иначе опрос VRAM на занятой видеокарте ждал бы её вечно —
# ровно то, что сервис и делает в бою, но в тесте это лишнее ожидание).
#
# Имена БЕЗ приставки: `core.app_meta.env` сам подставляет `REELSI_` (со старым
# `AUTOCUT_` запасным), и полное имя здесь давало `REELSI_REELSI_...` — перемычка
# молча не работала (тест сервиса падал на живую карту). Полные имена — в
# `_hint_env`, чтобы в сообщениях и тестах было видно, что выставлять.
ENV_SLOTS = "MODEL_SERVICE_SLOTS"
ENV_IDLE = "MODEL_SERVICE_IDLE"
ENV_DEVICE = "MODEL_SERVICE_DEVICE"
# Подмены весов для тестов: `gigaam` (головы GigaAM) и `transformers` (CED вздохов).
# Имена — константами, а не литералами по месту чтения: сторож тестов ищет ровно их.
ENV_SUBST = "MODEL_SERVICE_SUBST"
ENV_SUBST_CED = "MODEL_SERVICE_SUBST_CED"


def _hint_env(name: str) -> str:
    """Полное имя переменной окружения — для сообщений и тестов."""
    return "REELSI_" + name


def _cli_out(line: str = "") -> None:
    """Печать строки в stdout БЕЗ ошибки кодировки — дверь, как у `cli_error`.

    У консоли Windows кодировка не UTF-8 (cp866/cp1252), и русский текст в `print`
    уходил `UnicodeEncodeError: 'charmap' codec can't encode …` — то есть
    `--stop`/`--status` падали на своём же ответе владельцу. `reconfigure` (та же
    починка, что в `core.umsg.cli_error` для stderr) переводит поток в UTF-8 ещё
    до печати, а `errors="replace"` оставляет текст читаемым, если поток всё же
    окажется с однобайтовой кодировкой.

    Строка без перевода нарочно: `console_emit` берёт словарь интерфейса, а
    сервис — не UI, и `t()` тут ждал бы импорта того, чего у сервиса нет.
    """
    try:
        # sys.stdout в типах — TextIO, а reconfigure есть только у TextIOWrapper:
        # на практике это он и есть, но проверку типов это не устраивает.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass                  # поток без reconfigure — текст всё равно печатаем
    print(line, flush=True)


# Таймауты клиента. Пинг короткий: мёртвый порт обязан отвечать «сервиса нет»
# быстро, чтобы запасной путь не ждал. Транскрипция длинная: 120 с звука — это
# десятки окон на карте, и обрывать честный счёт по трёхсекундному сроку нельзя.
PING_TIMEOUT_S = 3.0
TRANSCRIBE_TIMEOUT_S = 900.0
# Сколько ждём НОВЫЙ сервис, пока он поднимет слушателя (импорт torch и gigaam
# внутри). Замер на этой машине: холодный старт 4.6–7.2 с, берём с запасом.
# Ждать дольше бессмысленно: процесс на старте либо падает (нет torch, нет весов),
# либо поднимается за считанные секунды, а запасной путь ждать не должен.
START_TIMEOUT_S = 15.0
# Заявку на старт старше этого считаем брошенной: стартер упал, не сняв её. Меньше
# START_TIMEOUT_S брать нельзя — с живого стартера заявку сняли бы, и на карте
# оказались бы два сервиса, то есть ровно то, от чего заявка и заведена.
START_CLAIM_STALE_S = 60.0
# Сторож процесса-владельца: на каждой проверке живости он ждёт столько и, если
# сервис за это время не ответил «я жив», запускает новый.
MONITOR_TIMEOUT_S = 30.0

# Метка версии формата файла адреса: старый/чужой формат — не наш сервис.
SETTINGS_VERSION = 1

# Насколько метка старта процесса в файле адреса может разойтись с настоящим временем
# старта (секунды). Ноль тут нельзя: на POSIX `/proc` отдаёт время в тиках, `ps` —
# с точностью до секунды, а сам сервис берёт метку у `psutil`; на Windows FILETIME
# и `psutil` тоже считают от разных начал. Терпимый разброс в две секунды при этом
# на порядки меньше времени жизни сервиса, а переиспользованный номер в него не
# укладывается (процесс, занявший номер после перезагрузки, стартует позже на минуты).
PROCESS_START_TOLERANCE_S = 2.0


class ServiceUnavailable(RuntimeError):
    """Сервис моделей недоступен: не стартовал, упал, ответил отказом.

    Отдельный класс, а не `RuntimeError` вообще: наверх это уходит ОДНОЙ веткой
    (нарезка считает локально и пишет причину), и отличить «сервиса нет» от
    «модель не загрузилась» вызывающему нужно без разбора текста.
    """


# --------------------------------------------------------------------------- #
# Слоты и запас видеопамяти
# --------------------------------------------------------------------------- #
def _env_int(name: str) -> int | None:
    """Целое из переменной окружения; мусор и пустое — None (перемычка молчит)."""
    raw = (env(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_float(name: str) -> float | None:
    """Дробное из переменной окружения; мусор и пустое — None."""
    raw = (env(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_str(name: str) -> str | None:
    """Строка из переменной окружения; пустое — None (перемычка молчит)."""
    return (env(name) or "").strip() or None


def _device_override() -> str | None:
    """Перемычка `REELSI_MODEL_SERVICE_DEVICE` — устройство помимо настройки.

    Нужна тестам, замерам и стенду: им важно считать на выбранном устройстве
    независимо от того, что человек поставил в интерфейсе. Отдельной дверью, потому
    что по ней же решает `model_cap`: подпись настроек обязана называть то же
    устройство, что и сервис, а стенд пришпиливает сервис к `cpu` именно ею.
    """
    return _env_str(ENV_DEVICE)


def _service_device() -> str:
    """Устройство сервиса: настройка «Где считать модели», перемычка `DEVICE` — сильнее.

    Настройка (авто / видеокарта / процессор) живёт в сервисном конфиге и нужна тем,
    у кого карты нет вовсе или кому карта нужна под локальную LLM. Перемычка
    `REELSI_MODEL_SERVICE_DEVICE` сильнее настройки нарочно: тестам и замерам нужно
    устройство, не зависящее от того, что человек выбрал в интерфейсе (иначе тест
    сервиса трогал бы живую карту рабочей машины).
    """
    return device.pick_device(force=_device_override() or model_device_cfg())


def model_device_cfg() -> str:
    """Настройка «Где считать модели» — одним местом, с откатом на «авто».

    Импорт внутри функции: `core.aicut.config` сам импортирует `core.device` (считает
    потолок роликов по карте), и импорт на уровне модуля замкнул бы кольцо.
    """
    try:
        from core.aicut.config import model_device_cfg as cfg
        return cfg()
    except Exception:  # конфиг устройства необязателен: не прочёлся — «авто», как по умолчанию
        return device.DEVICE_AUTO


# Модули, которыми сервис грузит веса: их содержимое входит в отпечаток версии.
# Пути берём У САМИХ МОДУЛЕЙ (`__file__`), а не собираем от корня: список файлов,
# сложенный руками, разошёлся бы с кодом на первой же правке путей. `gigaam_cache`
# и `device` здесь потому, что они решают, откуда читаются веса и на какое
# устройство: их правка — тоже «сервис другой версии». Свой файл — через
# `core.paths`: папку модуля в ядре не считает от `__file__` никто, кроме него.
_FINGERPRINT_MODULES = ("core.breath", "core.emphasis", "core.device", "core.gigaam_cache",
                        "core.gigaam_cut.asr")


def _fingerprint_files() -> list[str]:
    """Файлы, которые попадают в отпечаток версии: сам сервис и модули весов."""
    out = [paths.root("core", "model_service.py")]
    for name in _FINGERPRINT_MODULES:
        try:
            module = __import__(name, fromlist=["__file__"])
            path = getattr(module, "__file__", None)
            if path:
                out.append(os.path.abspath(path))
        except Exception:
            continue                  # модуля нет/не импортируется — снимем то, что есть
    return out


def _code_fingerprint() -> str:
    """Версия кода сервиса: хеш содержимого сервиса и модулей весов.

    НЕ `APP_VERSION` и не mtime, и это осознанный выбор. Версия приложения — одно
    число на весь проект: правка окна распознавания её не меняет, и сервис, поднятый
    до неё, отвечал бы старым кодом (ровно тот дефект, от которого заведён отпечаток).
    mtime дёшев, но врёт: `git checkout`, распаковка архива и синхронизация папки
    ставят новые времена файлам с ТЕМ ЖЕ содержимым — каждый такой заход гасил бы
    живой сервис и читал веса заново. Хеш содержимого не врёт ни в одну сторону.
    Читается он редко (старт сервиса, подключение клиента, старт сервера), и это
    те же файлы, которые процесс уже открывает при импорте, — на фоне чтения весов
    (гигабайты) это ничто. Не прочитался файл — помечаем отпечаток пустой строкой:
    «версию не знаю» означает «перезапусти сервис», а не «всё совпало».
    """
    h = hashlib.sha256()
    for path in _fingerprint_files():
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"?")
        h.update(b"\0")
    return h.hexdigest()[:16]


def device_free_vram_mib() -> int | None:
    """Свободная видеопамять от `core.device` — дверью, а не через модуль по имени.

    Дверь нужна из-за имени: у `slot_count` параметр называется `device` (устройство),
    и обращение `device.free_vram_mib()` внутри него уходило бы в СТРОКУ устройства, а
    не в модуль (`AttributeError: 'str' object has no attribute 'free_vram_mib'`,
    поймано тестом потолка). Тесты, которым карту мерить нельзя, подменяют одну эту
    функцию.
    """
    return device.free_vram_mib()


def device_nvidia_free_mib() -> int | None:
    """Свободная видеопамять через `nvidia-smi`, БЕЗ импорта torch (см. `model_cap`)."""
    free = getattr(device, "nvidia_free_mib", None)
    return int(free()) if callable(free) else None


def slot_count(free_mib: int | None = None, device: str | None = None) -> int:
    """Сколько распознаваний сервис считает разом.

    На карте — по свободной видеопамяти и цене одного распознавания:
    `free // (512 + 1024)`, но не меньше 1 и не больше 4. Нижняя граница не
    прихоть: карта может быть занята чужим RoFormer, и «ноль слотов» означал бы
    отказ считать вовсе — лучше подождать и посчитать, чем не посчитать никогда.

    На процессоре карты нет вовсе, и мерить нечего: слотов — половина логических
    ядер (GigaAM на CPU ест одно ядро на распознавание, вторую половину оставляем
    остальной нарезке), но не больше четырёх.

    `free_mib=None` — мера берётся у железа (`core.device.free_vram_mib`); None
    оттуда значит «мерить нечего» (CI, мак) и потому потолка нет: 4.
    `device` — то же число для НАЗВАННОГО устройства: им интерфейс показывает
    потолок роликов, не поднимая сервис и не зная ничего о его состоянии (см.
    `model_cap`). None — устройство берётся у этого процесса.
    """
    own = _env_int(ENV_SLOTS)
    if own is not None:
        return max(MIN_SLOTS, min(MAX_SLOTS, own))
    dev = _service_device() if device is None else device
    if dev == "cpu":
        cores = os.cpu_count() or 2
        return max(MIN_SLOTS, min(MAX_SLOTS, cores // 2))
    free = device_free_vram_mib() if free_mib is None else free_mib
    if free is None:
        return MAX_SLOTS
    return max(MIN_SLOTS, min(MAX_SLOTS, int(free) // (ASR_VRAM_MIB + VRAM_RESERVE_MIB)))


def vram_fits(free_mib: int | None = None) -> bool:
    """Хватает ли свободной VRAM на слот с запасом.

    Мера неизвестна (None) — считаем, что хватает: гадать по незнанию и ждать
    чужой RoFormer, которого нет, хуже, чем попробовать и получить честный OOM.
    Мера 0 — «карты нет» по договору `core.device` — тоже пропускаем: на CPU и
    маке VRAM ни при чём.
    """
    if _service_device() == "cpu":
        return True
    free = device.free_vram_mib() if free_mib is None else free_mib
    if free in (None, 0):
        return True
    return int(free) >= ASR_VRAM_MIB + VRAM_RESERVE_MIB


def wait_for_vram(emit: Callable[..., Any] = console_emit,
                  fits: Callable[[], bool] | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> None:
    """Ждать, пока на карте освободится место под слот. Не падать — ждать.

    Сервис живёт дольше одного ролика: карту в это время берут вздохи, RoFormer
    или рендер (они по-прежнему ходят под файловым замком `.gpu`, и сервис их не
    видит). Отказать в распознавании из-за чужого участка значило бы уронить
    нарезку на ровном месте — а ждать честно: владелец карту отпустит.
    """
    check = fits if fits is not None else vram_fits
    if check():
        return
    took = 0.0
    warned = False
    while not check():
        if not warned and took >= VRAM_WAIT_REPORT_S:
            warned = True
            emit("сервис моделей: карта занята, жду свободной видеопамяти "
                 "({sec}с) — распознавание встанет в очередь", sec=int(took))
        sleep(VRAM_WAIT_S)
        took += VRAM_WAIT_S


# --------------------------------------------------------------------------- #
# Файл адреса: адрес, ключ и pid сервиса
# --------------------------------------------------------------------------- #
def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким pid. Чужой процесс ТОЛЬКО спрашиваем, не трогаем.

    Windows: `OpenProcess` + `GetExitCodeProcess` (код STILL_ACTIVE = 259).
    POSIX: `os.kill(pid, 0)` не шлёт сигнала, а лишь проверяет право и живость.
    Оба способа — read-only проверка; убивать процессы по имени в проекте нельзя.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = getattr(ctypes, "windll").kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259              # STILL_ACTIVE
        except Exception:
            # ctypes не сработал (нет прав, урезанная сборка) — своей мерой
            # считаем «не знаю»: пусть решает чтение файла настроек.
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # процесс есть, просто он не наш
    except OSError:
        return False
    return True


# Хвост команды запуска сервиса: `python -m core.model_service --serve` (у процесса
# может стоять `-u` перед `-m`). По нему процесс ОТЛИЧАЕТСЯ от чужого: номер
# процесса переиспользуется, и после перезагрузки в старом файле адреса может стоять
# номер совсем другого процесса — гасить его нельзя ни при каких обстоятельствах.
SERVICE_MODULE = "core.model_service"
SERVICE_SERVE_FLAG = "--serve"
# Команда-будильник: её шлёт `_Service.stop()` сам себе (`_Service.wake`), чтобы
# цикл приёма вышел СРАЗУ, а не на следующем опросе таймаута. Отдельным словом, а
# не `ping`: это не запрос к сервису, и в журнал о нём писать нечего.
WAKE_COMMAND = "__wake__"
# Срок ожидания приёма (секунды). ЗАЧЕМ ТАЙМАУТ, А НЕ ОДНО ЛИШЬ ЗАКРЫТИЕ
# СЛУШАТЕЛЯ: закрытие сокета из другого потока прерывает блокирующий `accept()`
# не везде — на Windows да, на Linux нет, и там поток приёма висел в `accept`
# навсегда. Тогда сервис не выходил ни по простою, ни по `shutdown`, держал
# видеопамять до перезагрузки, а сторож тестовой сессии валил чужой тест.
# Поэтому приём ждёт соединения С ТАЙМАУТОМ и проверяет флаг выхода в цикле:
# это работает на обеих ОС и не зависит от поведения платформы при `close`.
# Срок мал нарочно: 0.2 с незаметны и для простоя (пять минут), и для `shutdown`
# (кажется мгновенным), а будильник `wake` снимает даже эту задержку.
POLL_ACCEPT_S = 0.2


def _psutil_process(pid: int) -> Any | None:
    """`psutil.Process` для pid, если psutil есть; None — его нет или процесс чужой.

    psutil — основная мера: он отдаёт и командную строку, и время старта процесса
    одинаково на Windows и POSIX. Он есть в зависимостях (его берёт стенд замеров),
    но обязательным здесь не считается: без него работают запасные меры ниже, а их
    молчание значит только «не проверить» — то есть «не наш процесс», а не «гаси».
    """
    try:
        import psutil
    except Exception:  # psutil необязателен: без него работают запасные меры ниже (см. docstring)
        return None
    try:
        return psutil.Process(pid)
    except Exception:  # процесса уже нет или нет прав на него: «не наш», значит не гасим
        return None


def _process_create_time(pid: int) -> float | None:
    """Время старта процесса в секундах эпохи; None — не узнать.

    Время старта — вторая мера против переиспользования номера: даже если командная
    строка совпала (одна и та же команда сервиса в другой копии репозитория), метка
    старта в файле адреса укажет, тот ли это процесс. На Windows — `psutil`, затем
    `GetProcessTimes` (ctypes, без сторонних библиотек); на POSIX — `/proc`, затем
    `ps`. Всё здесь ТОЛЬКО читает.
    """
    proc = _psutil_process(pid)
    if proc is not None:
        try:
            return float(proc.create_time())
        except Exception:
            pass                  # процесс успел выйти — спросим у системы ниже
    if os.name == "nt":
        return _windows_create_time(pid)
    return _posix_create_time(pid)


def _windows_create_time(pid: int) -> float | None:
    """Время старта процесса Windows через `GetProcessTimes` (ctypes, read-only)."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_ = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                          ctypes.byref(exit_), ctypes.byref(kernel),
                                          ctypes.byref(user))
            if not ok:
                return None
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return ticks / 10_000_000.0 - 11644473600.0   # 100-нс тики от 1601 к эпохе
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # время старта не узнать — None: вызывающий трактует это как «не проверить», не гасит
        return None


def _posix_create_time(pid: int) -> float | None:
    """Время старта процесса POSIX — метка эпохи: `/proc` (точно), затем `ps`.

    `/proc/<pid>/stat` отдаёт `starttime` в тиках от ЗАГРУЗКИ, а не от эпохи, —
    переводим через `btime` из `/proc/stat` (второе начало координат): сравнивать
    метку файла адреса с тиками от загрузки значило бы всегда получать «не наш».
    """
    try:
        # `getattr`, а не прямое имя: на Windows-host mypy проверяет и эту ветку, а
        # `os.sysconf` там в типах не объявлен (`attr-defined`), хотя на POSIX есть.
        sysconf = getattr(os, "sysconf")
        clk = float(sysconf("SC_CLK_TCK"))               # тиков в секунду
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as fh:
            # Имя процесса в скобках может содержать пробелы — отрезаем по последней ')'
            fields = fh.read().rsplit(")", 1)[-1].split()
        starttime = float(fields[19])
        with open("/proc/stat", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("btime "):
                    boot = float(line.split()[1])
                    return boot + starttime / clk
    except Exception:
        pass                  # /proc нет (не Linux) или формат другой — пробуем `ps`
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if not out:
            return None
        return float(time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")))
    except Exception:  # ps не ответил или формат чужой — None: время не известно, процесс не трогаем
        return None


def _windows_cmdline(pid: int) -> str | None:
    """Командная строка процесса Windows: сперва `wmic`, затем `PowerShell` (CIM).

    `wmic` снят с новых сборок Windows 11, поэтому есть второй ход через CIM:
    `Win32_Process.CommandLine` отдаёт то же самое. Обе команды ТОЛЬКО читают.
    """
    for cmd in (["wmic", "process", "where", "ProcessId=%d" % pid, "get",
                 "CommandLine", "/value"],
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\")"
                 ".CommandLine" % pid]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:  # wmic снят в новых Windows — пробуем следующую команду; ни одна не сработала — None
            continue
        text = (r.stdout or "").strip()
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("COMMANDLINE="):
                line = line.split("=", 1)[1].strip()
            if line:
                return line
    return None


def _process_cmdline(pid: int) -> str | None:
    """Командная строка процесса; None — не узнать (значит, не наш процесс)."""
    proc = _psutil_process(pid)
    if proc is not None:
        try:
            cmdline = proc.cmdline()
            text = " ".join(str(a) for a in cmdline) if cmdline else ""
            if text:
                return text
        except Exception:
            pass              # psutil не отдал строку (права, процесс вышел) — ниже
    if os.name == "nt":
        return _windows_cmdline(pid)
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return fh.read().decode("utf-8", "replace").replace("\0", " ").strip()
    except Exception:  # файла нет (процесс вышел) или нет прав — командной строки нет, это не наш процесс
        return None


def _cmdline_is_service(text: str | None) -> bool:
    """Это командная строка НАШЕГО сервиса: `… -m core.model_service … --serve`.

    Ждём ровно `-m`, затем `core.model_service` и где-то дальше `--serve`. Одного
    вхождения слов мало: строка могла попасть в чужую команду как аргумент (например,
    путь к тесту или чужая команда с нашим текстом внутри), а такой процесс гасить
    нельзя. Разбор «по глаголам» отсекает это: у чужого вызова нет `-m` перед именем
    модуля.
    """
    if not text:
        return False
    words = text.split()
    for i, word in enumerate(words[:-1]):
        if word == "-m" and words[i + 1].replace("\\", "/").endswith(SERVICE_MODULE):
            return SERVICE_SERVE_FLAG in words[i + 2:]
    return False


def _pid_is_service(pid: int, data: dict[str, Any] | None = None) -> bool:
    """Гасить по PID можно ТОЛЬКО проверенный процесс сервиса — иначе нельзя вовсе.

    ЧТО БЫЛО. `_restart_stale` брал PID из файла адреса и звал kill. Тест поднимает
    сервис ВНУТРИ процесса pytest, и его номер попадал в адрес — гашение убивало сам
    pytest (`TerminateProcess` по текущему процессу, вывод обрывался без итога, а
    полный набор из-за этого висел час). В бою то же самое опаснее: после
    перезагрузки номер из старого файла принадлежит уже другому процессу.

    Проверки, все три обязательны:
      1. номер — не наш процесс и не родитель (свой процесс не гасим никогда:
         родитель сервис не заводит, а в тесте родитель — сам pytest);
      2. командная строка — ровно `… -m core.model_service … --serve`;
      3. время старта процесса совпадает с меткой `created` из файла адреса (если она
         есть): переиспользованный номер её не переживёт.

    Не прошёл проверку — процесс НЕ трогаем; вызывающий считает адрес брошенным и
    просто убирает файл.
    """
    if pid <= 0 or pid in (os.getpid(), os.getppid()):
        return False
    if not _pid_alive(pid):
        return False
    if not _cmdline_is_service(_process_cmdline(pid)):
        return False
    marked = (data or {}).get("created")
    if isinstance(marked, (int, float)) and not isinstance(marked, bool) and marked > 0:
        actual = _process_create_time(pid)
        if actual is None or abs(float(actual) - float(marked)) > PROCESS_START_TOLERANCE_S:
            return False
    return True


# Права файла адреса: только владелец. На этом и держится `authkey` — файл
# читает только владелец, значит и подключиться может только он. На Windows
# `chmod` выставляет лишь флаг «только чтение», но файл всё равно лежит в личном
# каталоге пользователя.
SETTINGS_FILE_MODE = 0o600


def _write_settings(path: str, data: dict[str, Any]) -> None:
    """Записать файл адреса, закрыв его от остальных пользователей машины.

    Пишем общей атомарной записью ядра (`core.fileio.atomic_json_dump`): она уже
    делает то, что здесь было скопировано руками — уникальное имя tmp в том же
    каталоге, `fsync` перед подменой и `os.replace` в конце. Половинчатый JSON
    означал бы «сервиса нет» на живом сервисе: файл читает другой процесс в любой
    момент. Права нового файла задаём явно (`file_mode`) — адрес хранит `authkey`,
    и умолчание по umask оставило бы его читаемым для всех.
    """
    fileio.atomic_json_dump(path, data, file_mode=SETTINGS_FILE_MODE)


def read_settings(path: str | None = None) -> dict[str, Any] | None:
    """Прочитать файл адреса. Нет файла / битый / не наш формат — None.

    Битым файлом сервис не считается: «не разобрали» и «сервиса нет» для
    вызывающего одно и то же — надо заводить новый, а не падать.
    """
    import json
    p = path or _settings_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return None
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != SETTINGS_VERSION:
        return None
    if not (isinstance(data.get("host"), str) and isinstance(data.get("port"), int)
            and isinstance(data.get("authkey"), str)):
        return None
    return data


def _settings_alive(data: dict[str, Any] | None, timeout: float = PING_TIMEOUT_S) -> bool:
    """Живой ли сервис из файла адреса: порт отвечает И процесс ещё есть.

    Двух проверок мало по отдельности. Мёртвый файл (сервис упал, порт занял
    чужой процесс) выглядит как живой по одному лишь `connect` — поэтому здесь
    настоящий запрос `ping` по протоколу сервиса. А живой порт при мёртвом pid
    означает, что файл остался от прошлого запуска и порт успел достаться кому-то
    другому — тоже «не наш сервис».
    """
    if not data:
        return False
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
        return False
    try:
        resp = _request("ping", data, timeout=timeout, attempts=1)
    except ServiceUnavailable:
        return False
    return bool(resp and resp.get("ok"))


def _running_pid(data: dict[str, Any] | None) -> int:
    """PID живого сервиса из файла адреса; 0 — сервиса нет.

    Отдельной дверью нарочно: по этому номеру уходящий сервис не затирает чужой адрес
    (`_clear_settings_if_own`), и он же нужен `drop_stale`, чтобы новый сервис не
    начал писать адрес в файл, который старый уносит с собой.
    """
    pid = (data or {}).get("pid")
    return int(pid) if isinstance(pid, int) and pid > 0 else 0


def _settings_build(data: dict[str, Any] | None) -> str:
    """Версия кода сервиса из файла адреса; пустая строка — «версию не знаю»."""
    return str((data or {}).get("build") or "")


def _settings_mismatch(data: dict[str, Any] | None, build: str) -> bool:
    """Настройка/версия расходится с работающим сервисом: устройство или код.

    `build` передаётся вызывающим и считается ОДИН раз на вызов старта: отпечаток —
    это чтение содержимого пяти файлов, и звать его на каждую сверку незачем.

    Версия сверяется с `build`, а не со свежим `_code_fingerprint()` внутри — иначе
    тест (и стенд) не смог бы подставить «сервис другой версии»: подмена отпечатка
    была бы видна и этому процессу тоже, и сверка всегда сходилась бы.

    Устройство сверяем и как настройку, и как фактическое (`_service_device`): у
    процесса-клиента устройство могло быть задано перемычкой
    (`REELSI_MODEL_SERVICE_DEVICE`), и тогда сравнивать одни слова в конфиге мало —
    важно, на чём сервис СЧИТАЕТ. Отпечатка в файле нет — это адрес, записанный
    прежней версией сервиса (или чужим стендом): «версию не знаю» означает
    «перезапусти», как и в `_code_fingerprint`.
    """
    if not data:
        return False
    return (_settings_build(data) != build
            or str(data.get("device") or "") != _service_device())


def _restart_stale(path: str, data: dict[str, Any]) -> None:
    """Погасить сервис другой версии/устройства и СНЯТЬ его файл адреса.

    Файл убираем здесь, а не в `ensure_started`, нарочно: гасить сервис и тут же
    читать «занят ли он» по тому же файлу — это гонка, в которой свежий сервис мог бы
    решить, что место занято, и не записать адрес.

    СПЕРВА ВЕЖЛИВО: `shutdown` живому сервису — он сам снимет свой файл и выйдет.
    Убийство по PID — только если сервис не ответил И номер прошёл проверку
    `_pid_is_service`: это НЕ ЛЮБОЙ процесс с таким номером, а именно наш сервис.
    Иначе файл адреса считается брошенным, и процесс не трогают вовсе (в тесте по
    этому пути стоял сам pytest, и гашение обрывало прогон без итога).
    """
    pid = _running_pid(data)
    if shutdown():
        console_emit("сервис моделей: устройство или версия кода сменились — "
                     "погасил старый сервис (pid {pid})", pid=pid or "?")
    if pid and _pid_alive(pid) and _pid_is_service(pid, data):
        # Сервис не отозвался на просьбу — гасим ЕГО процесс по PID (по имени
        # процессов в проекте не трогают никогда).
        _kill_started(_PidProc(pid), path)
    elif pid and _pid_alive(pid):
        console_emit("сервис моделей: файл адреса брошен — процесс {pid} не наш, "
                     "не трогаю его", pid=pid)
    clear_settings(path)


class _PidProc:
    """Процесс по одному лишь PID — для `_kill_started` (поля `kill()` и `pid`).

    `_restart_stale` знает только номер процесса (Popen у него нет: сервис завёл
    кто-то другой), а `_kill_started` работает по обоим полям. Гасит тот же способ,
    что «Стоп» интерфейса, — `core.jobstate.kill_pid`: второй копии правил «какие
    группы не трогать» в проекте быть не должно. По имени процессов не ищут никогда.
    """

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)

    def kill(self) -> None:
        from core.jobstate import kill_pid
        kill_pid(self.pid)


def _stop_and_clear_stale(path: str, build: str) -> None:
    """Погасить сервис другой версии/устройства, если он жив (мёртвый файл — просто убрать)."""
    data = read_settings(path)
    if data and _settings_alive(data) and _settings_mismatch(data, build):
        _restart_stale(path, data)
        return
    if _is_stale(path):
        clear_settings(path)          # мёртвый адрес: иначе новый примем за старый


def clear_settings(path: str | None = None) -> None:
    """Убрать файл адреса (сервис завершился — файл больше не нужен)."""
    try:
        os.remove(path or _settings_path())
    except OSError:
        pass                  # файла уже нет — убирать нечего


# --------------------------------------------------------------------------- #
# Слушатель: один процесс, много подключений, слоты вместо файлового замка
# --------------------------------------------------------------------------- #
_models: dict[str, Any] = {}
# CED вздохов и голова `emo` — отдельными кешами, а не в `_models`: там ключ — имя
# головы GigaAM, и старую голову выгружают при смене. Сложи их в один словарь —
# переключение «распознавание -> вздохи -> распознавание» читало бы веса заново на
# каждом шаге, то есть ровно то, от чего сервис и заведён.
_ced_models: dict[str, Any] = {}
_emo_models: dict[str, Any] = {}
_models_lock = threading.Lock()
# Замок САМОЙ загрузки весов, отдельно от кэша: два первых запроса разом успевали
# оба пройти проверку кэша и оба читали веса с диска — две копии модели на карте
# (замер: 2.45 с на прогрев вместо 1.93 с). Сервис ровно за тем и заведён, чтобы
# веса читались один раз.
_models_load_lock = threading.Lock()


def _publish_model(cache: dict[str, Any], key: str, model: Any,
                   stopped: threading.Event | None) -> None:
    """Положить прочитанные веса в ОБЩИЙ кэш — если сервис ещё не остановлен.

    ЗАЧЕМ ПРОВЕРКА ФЛАГА. Чтение весов длится секунды, и поток-обработчик живёт
    дольше `stop()`: он уже внутри `load_model`, когда сервис останавливают.
    Опубликуй он свою модель в кэш — «остановленный» сервис оставил бы след в
    общем на процесс словаре, а следующий сервис (в тестах — следующий тест)
    нашёл бы там готовую модель и СВОИ веса не прочитал («прочитано 0 раз вместо
    одного»). Отдельного признака остановки не заводим: флаг сервиса
    (`_Service._stopped`) означает ровно «работа сервису запрещена», и второй
    флаг с тем же смыслом разошёлся бы с первым на первой же правке.

    Проверка стоит ПОД ТЕМ ЖЕ замком, что и запись, а сам флаг выставляет `stop()`
    тоже под ним: иначе остановка вклинилась бы между проверкой и записью — и
    гонка осталась бы (ровно тот плавающий отказ, ради которого проверка и
    заведена). `stopped is None` — вызов вне службы: публикуем как раньше.
    """
    with _models_lock:
        if stopped is not None and stopped.is_set():
            return
        cache.clear()
        cache[key] = model


def _loaded_model(head: str, stopped: threading.Event | None = None) -> Any:
    """Модель головы `head`, загруженная ОДИН раз (веса читаются с диска при первом
    распознавании и живут до простоя или `unload()`).

    Загрузка идёт через тот же `gigaam.load_model(..., download_root=...)`, что и
    раньше: папку весов выбирает единственное место — `core.gigaam_cache.gigaam_dir`.
    `stopped` — флаг остановки сервиса: если он выставлен, дочитанные веса в общий
    кэш не попадают (`_publish_model`), но этому запросу модель ещё послужит.
    """
    with _models_lock:
        model = _models.get(head)
        if model is not None:
            return model
    with _models_load_lock:
        with _models_lock:                    # за время ожидания могли загрузить её же
            model = _models.get(head)
            if model is not None:
                return model
        import gigaam
        from core.gigaam_cache import gigaam_dir
        fresh = gigaam.load_model(head, download_root=gigaam_dir())
        # Две головы разом на карту не влезают, а ключ кеша — имя головы: старую
        # выгружаем, иначе при переключении головы память копилась бы.
        _publish_model(_models, head, fresh, stopped)
    return fresh


def unload_models() -> None:
    """Выгрузить веса из памяти сервиса (VRAM свободна, процесс и адрес живы).

    Чистим ВСЕ кеши, а не только головы GigaAM: `unload()` зовёт шаг рендера, и
    оставленный CED или `emo` держал бы видеопамять, которую просят рендер и AE.
    """
    with _models_lock:
        _models.clear()
        _ced_models.clear()
        _emo_models.clear()
    _free_torch()


def _loaded_ced(stopped: threading.Event | None = None) -> Any:
    """CED-tiny в памяти сервиса: ключ кеша — имя модели и её ревизия.

    Ревизия в ключе нарочно: `core.breath` держит закреплённую ревизию
    (`CED_REVISION`), и после её смены сервис обязан прочитать НОВЫЕ веса, а не
    отдать вероятности посчитанными старыми. `stopped` — флаг остановки сервиса:
    дочитанные веса остановленного сервиса в общий кэш не публикуются.
    """
    from core import breath
    key = "%s@%s" % (breath.CED_ID, breath.CED_REVISION)
    with _models_lock:
        got = _ced_models.get(key)
        if got is not None:
            return got
    with _models_load_lock:
        with _models_lock:                    # за время ожидания могли загрузить её же
            got = _ced_models.get(key)
            if got is not None:
                return got
        fresh = breath.load_ced()
        _publish_model(_ced_models, key, fresh, stopped)
    return fresh


def _loaded_emo(stopped: threading.Event | None = None) -> Any:
    """Голова `emo` GigaAM в памяти сервиса: ключ — имя головы, читается один раз.

    Берём `emphasis.load_emo_model_local`, а не `load_emo_model`: последний сам
    спрашивает сервис (`preload_emo`), и внутри сервиса это был бы запрос к себе —
    то есть вечная рекурсия вместо загрузки весов. `stopped` — флаг остановки
    сервиса: дочитанные веса остановленного сервиса в общий кэш не публикуются.
    """
    from core import emphasis
    key = "emo"
    with _models_lock:
        got = _emo_models.get(key)
        if got is not None:
            return got
    with _models_load_lock:
        with _models_lock:
            got = _emo_models.get(key)
            if got is not None:
                return got
        fresh = emphasis.load_emo_model_local()
        _publish_model(_emo_models, key, fresh, stopped)
    return fresh


def _free_torch() -> None:
    """`gc.collect()` + `torch.cuda.empty_cache()` — как в `gigaam_cut.asr`."""
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass                  # torch/GPU недоступны — чистить нечего


def _memory_stats() -> dict[str, int]:
    """Пик памяти процесса сервиса в МиБ (ответ на `stats`).

    Раньше память сервиса не спрашивал никто: стенд мерил только своих детей, а у
    них распознавания нет вовсе — в таблице под сервисным режимом стояли нули, будто
    счёт ничего не занял. Мерит тот же счётчик, что и стенд в процессе ролика
    (`torch.cuda.max_memory_allocated`), и в тех же единицах.

    На процессоре и без torch — нули: мерить нечего, а импортировать torch ради
    нулей в отчёте не нужно (в бою он уже загружен, в тестах его нет вовсе).
    """
    zeros = {"max_memory_allocated": 0, "max_memory_reserved": 0}
    if _service_device() == "cpu":
        return zeros
    try:
        import torch
        if not torch.cuda.is_available():
            return zeros
        mib = 1024 * 1024
        reserved = getattr(torch.cuda, "max_memory_reserved", None)
        return {"max_memory_allocated": int(torch.cuda.max_memory_allocated()) // mib,
                "max_memory_reserved": int(reserved()) // mib if callable(reserved) else 0}
    except Exception:
        return zeros          # torch/GPU недоступны — мера неизвестна, а не «ноль важен»


_subst_modules: dict[str, Any] = {}
# Замок самой загрузки подменного модуля, как `_models_load_lock` у весов: два
# одновременных ПЕРВЫХ запроса оба промахивались по кэшу и выполняли файл подмены
# каждый сам. Объектов модуля выходило два: `sys.modules["gigaam"]` получал один,
# `_subst_modules[path]` — другой (последний записавший). Веса грузил первый, а
# тест читал счётчик второго и видел «прочитано 0 раз вместо одного».
_subst_modules_lock = threading.Lock()


def _exec_module(path: str) -> Any:
    """Загрузить модуль Python по пути (тестовая перемычка вместо весов GigaAM).

    Обычному запуску не нужна: без переменной сервис берёт настоящий `gigaam`.
    Нужна тестам — прогнать НАСТОЯЩИЙ путь сервиса (слушатель, слоты, клиент,
    ответ) без весов на диске и без карты.

    Модуль запоминаем по пути: `exec_module` НЕ кладёт его в `sys.modules`, и без
    кеша каждое распознавание выполняло бы файл заново — счётчики подмены
    обнулялись бы, а тест «модель грузится один раз» мерил бы не то.

    Проверка кэша, выполнение файла и запись идут под ОДНИМ замком: два первых
    запроса разом иначе выполняли подмену дважды, и счётчик весов доставался то
    одному объекту модуля, то другому. Двойной проверки нет нарочно: перемычка
    тестовая, боевой путь сюда не заходит, и на попадании в кэш замок без спора
    стоит наносекунды.
    """
    import importlib.util
    with _subst_modules_lock:
        cached = _subst_modules.get(path)
        if cached is not None:
            return cached
        name = "_modelsvc_subst_%d" % random.randint(0, 1 << 30)
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ServiceUnavailable("не разобрать модуль подмены: %s" % path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _subst_modules[path] = module
        return module


def _install_subst(fake: Any) -> None:
    """Подменить `gigaam` подменным модулем — тестовая перемычка.

    Подмены одного `gigaam` довольно: его импортирует `_loaded_model`, а окна
    слушает САМА МОДЕЛЬ (`transcribe(word_timestamps=True)`), и её отдаёт
    `load_model` подменного модуля. Значит, веток в боевом коде не нужно вовсе —
    обычный запуск сюда не заходит (`MODEL_SERVICE_SUBST` у него нет).
    """
    import sys as _sys
    _sys.modules["gigaam"] = fake


def _install_subst_from_env() -> None:
    """Подмены по переменным окружения — перед ЛЮБЫМ чтением весов.

    Одной функцией нарочно: веса читает и распознавание, и прогрев (`_preload`), и
    обойти подмену на одном из путей значило бы грузить настоящие веса там, где
    тест ждёт пустышку. Две подмены — по одной на источник весов: `gigaam` (головы
    GigaAM) и `transformers` (CED-tiny вздохов).
    """
    subst = _env_str(ENV_SUBST)
    if subst:
        _install_subst(_exec_module(subst))
    ced = _env_str(ENV_SUBST_CED)
    if ced:
        _install_subst_ced(_exec_module(ced))


def _install_subst_ced(fake: Any) -> None:
    """Подменить `transformers` подменным модулем — тестовая перемычка для CED.

    CED читает веса одним модулем (`AutoFeatureExtractor.from_pretrained` и
    `AutoModelForAudioClassification.from_pretrained` в `breath.load_ced`), поэтому
    подмены довольно. Обычному запуску не нужна: без `MODEL_SERVICE_SUBST_CED`
    сервис берёт настоящий `transformers`.
    """
    import sys as _sys
    _sys.modules["transformers"] = fake


def transcribe_loaded(head: str, wav_path: str,
                      emit: Callable[..., Any] = console_emit,
                      stopped: threading.Event | None = None) -> list[dict[str, Any]]:
    """Распознать файл готовой моделью головы `head` — ОБЩАЯ точка входа сервиса.

    Модель берётся из кеша (`_loaded_model`): один раз прочитанные веса живут до
    смены головы, `unload()` или простоя. Считает
    `core.gigaam_cut.asr.transcribe_loaded` — копии логики распознавания здесь нет
    нарочно, иначе сервис и локальный путь разъехались бы на первой же правке.

    Подмена (`REELSI_MODEL_SERVICE_SUBST`) встаёт на место модуля `gigaam`: кеш
    моделей тогда работает поверх подменного `load_model`, и тест «веса читаются
    один раз» меряет НАСТОЯЩИЙ путь сервиса, а не обходной.

    `stopped` — флаг остановки сервиса: он уходит в `_loaded_model`, и веса,
    дочитанные уже после `stop()`, в общий кэш не публикуются.
    """
    _install_subst_from_env()
    from core.gigaam_cut.asr import transcribe_loaded as asr_transcribe
    model = _loaded_model(head, stopped)
    return asr_transcribe(model, wav_path, emit=emit)


class _Service:
    """Слушатель сервиса: соединения, слоты, простой.

    Один экземпляр на процесс. Запускается в СВОЁМ потоке (`serve_thread`), и это
    не украшение: ожидание простоя и закрытие слушателя из того же потока, что
    обслуживает запрос, оставило бы соединение висеть.
    """

    def __init__(self, port: int, authkey: bytes, idle_s: float | None = None,
                 slots: int | None = None,
                 emit: Callable[..., Any] = console_emit) -> None:
        self.port = int(port)
        self.authkey = authkey
        self.idle_s = DEFAULT_IDLE_S if idle_s is None else float(idle_s)
        # Слоты считаем один раз на старте: карта, занятая на момент запуска
        # сервиса, — это карта, на которой и предстоит считать.
        self.slots = slot_count() if slots is None else max(MIN_SLOTS, min(MAX_SLOTS, int(slots)))
        self.emit = wrap_emit(emit)
        self._sem = threading.Semaphore(self.slots)
        self._active = 0
        self._last_used = time.monotonic()
        self._lock = threading.Lock()
        self._listener: Any = None
        self._accept_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stopped = threading.Event()
        # Рабочие потоки сервиса — обработчики соединений и сторож простоя — под
        # учётом. Не для красоты: `wait_until_stopped` обязан дождаться их ВСЕХ,
        # иначе «остановленный» сервис ещё читает веса и считает (см. докстринг
        # `wait_until_stopped`). Поток снимает себя с учёта сам, в `finally`.
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()

    # --- учёт рабочих потоков --------------------------------------------- #
    def _start_worker(self, target: Callable[..., Any], *args: Any) -> threading.Thread:
        """Завести рабочий поток сервиса и поставить его на учёт ДО старта.

        На учёт ставим до `start()`: иначе поток, успевший отработать, снял бы
        себя с учёта раньше, чем попал туда, и `wait_until_stopped` не увидел бы
        его вовсе. Потоки демонические (выход процесса они не держат) — учёт
        нужен не для жизни процесса, а для инварианта остановки.
        """
        thread = threading.Thread(target=self._run_worker, args=(target, args), daemon=True)
        with self._workers_lock:
            self._workers.add(thread)
        thread.start()
        return thread

    def _run_worker(self, target: Callable[..., Any], args: tuple[Any, ...]) -> None:
        """Обёртка рабочего потока: снять себя с учёта, чем бы работа ни кончилась."""
        try:
            target(*args)
        finally:
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    # --- обработка одного соединения -------------------------------------- #
    def handle(self, conn: Any) -> None:
        """Запрос-ответ по одному соединению. Соединение короткое: один запрос."""
        try:
            cmd, payload = conn.recv()
            if cmd == WAKE_COMMAND:
                # Будильник `stop()`: отвечать нечего — он и не ждёт ответа.
                # Ветка отдельная, чтобы будильник не попал в «неизвестную команду»
                # и не оставил в журнале лишнюю строку на каждом выходе сервиса.
                return
            if cmd == "ping":
                conn.send({"ok": True, "slots": self.slots, "busy": self._busy()})
            elif cmd == "transcribe":
                conn.send(self._transcribe(payload))
            elif cmd == "breath":
                conn.send(self._breath(payload))
            elif cmd == "emo":
                conn.send(self._emo(payload))
            elif cmd == "load":
                conn.send(self._preload(payload))
            elif cmd == "stats":
                conn.send({"ok": True, **_memory_stats()})
            elif cmd == "unload":
                unload_models()
                conn.send({"ok": True})
            elif cmd == "shutdown":
                # Ответ ДО остановки: клиент должен узнать, что просьба принята,
                # а не гадать по оборванному соединению.
                conn.send({"ok": True})
                self.stop()
            else:
                conn.send({"ok": False, "error": "неизвестная команда: %r" % (cmd,)})
        except (EOFError, OSError):
            pass              # клиент ушёл (таймаут, «Стоп») — отвечать некому
        except Exception as ex:                      # чужой сбой не должен ронять сервис
            with contextlib.suppress(Exception):
                conn.send({"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)})
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    def _busy(self) -> int:
        """Сколько распознаваний идёт прямо сейчас (для ответа на `ping`)."""
        with self._lock:
            return self._active

    @contextlib.contextmanager
    def _slot(self) -> Iterator[None]:
        """Слот сервиса: не больше `slots` работ разом, счёт `_active` — для `ping`.

        Слот берётся ДО проверки VRAM: иначе два запроса разом оба увидели бы
        свободную память и оба пошли бы считать.
        """
        self._sem.acquire()
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()

    def _transcribe(self, payload: Any) -> dict[str, Any]:
        """Распознавание под слотом: не больше `slots` разом, с проверкой VRAM."""
        head = str((payload or {}).get("head") or "")
        wav = str((payload or {}).get("wav") or "")
        if not head or not wav:
            return {"ok": False, "error": "нужны head и wav"}
        with self._slot():
            try:
                wait_for_vram(emit=self.emit)
                words = transcribe_loaded(head, wav, emit=self.emit, stopped=self._stopped)
                return {"ok": True, "words": list(words)}
            except Exception as ex:  # ошибка уходит вызывающему в ответе {ok: False, error}, сервис не падает
                return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}

    def _breath(self, payload: Any) -> dict[str, Any]:
        """Вздохи: вероятности CED-tiny по границам окон — под слотом, как счёт слов.

        Считает `core.breath.ced_probs` — тот же счёт, что у локального пути: копии
        логики в сервисе нет, и числа сходятся до бита. Звук сервис читает САМ, той
        же дверью, что клиент (`breath.read_mono`), поэтому по проводу едет путь, а
        не мегабайты отсчётов.

        Вероятности уходят списком списков, а не массивом numpy: формат ответа
        остаётся обычными числами, а float32 -> float -> float32 переводится точно,
        поэтому локальный и сервисный ответы сравнимы побайтно.
        """
        wav = str((payload or {}).get("wav") or "")
        spans = list((payload or {}).get("spans") or [])
        if not wav or not spans:
            return {"ok": False, "error": "нужны wav и spans"}
        _install_subst_from_env()
        with self._slot():
            try:
                wait_for_vram(emit=self.emit)
                from core import breath
                probs = breath.ced_probs(breath.read_mono(wav),
                                         [(float(a), float(b)) for a, b in spans],
                                         ced=_loaded_ced(self._stopped))
                return {"ok": True, "probs": [[float(x) for x in row] for row in probs]}
            except Exception as ex:  # ошибка уходит вызывающему в ответе {ok: False, error}, сервис не падает
                return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}

    def _emo(self, payload: Any) -> dict[str, Any]:
        """Эмоция окна звука: вероятности головы `emo` — под слотом, модель в сервисе.

        Считает `core.emphasis.emotion_probs` над окном, которое прислал клиент:
        окно уже прочитано из исходника Камеры 1, и второй раз его читать негде.
        """
        window = (payload or {}).get("window")
        sr = int((payload or {}).get("sr") or 0)
        if window is None or sr <= 0:
            return {"ok": False, "error": "нужны window и sr"}
        _install_subst_from_env()
        with self._slot():
            try:
                wait_for_vram(emit=self.emit)
                from core import emphasis
                probs = emphasis.emotion_probs(window, _loaded_emo(self._stopped), sr)
                return {"ok": True, "probs": {str(k): float(v) for k, v in probs.items()}}
            except Exception as ex:  # ошибка уходит вызывающему в ответе {ok: False, error}, сервис не падает
                return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}

    def _preload(self, payload: Any) -> dict[str, Any]:
        """Прочитать веса, ничего не считая: прогрев сервиса до первого ролика.

        Нужно замеру: без прогрева «загрузка» сервиса попадала в «счёт» первого
        ролика, и сервисный режим выглядел хуже процесса на время чтения весов.
        Одна дверь на три источника: голова GigaAM (`head`), CED вздохов (`ced`) и
        голова эмоций (`emo`) — счёт у них общий (`_loaded_model`/`_loaded_ced`/
        `_loaded_emo`), второго пути к весам не появляется.
        """
        head = str((payload or {}).get("head") or "")
        ced = bool((payload or {}).get("ced"))
        emo = bool((payload or {}).get("emo"))
        if not head and not ced and not emo:
            return {"ok": False, "error": "нужна голова, ced или emo"}
        _install_subst_from_env()
        with self._slot():
            try:
                wait_for_vram(emit=self.emit)
                if ced:
                    _loaded_ced(self._stopped)
                elif emo:
                    _loaded_emo(self._stopped)
                else:
                    _loaded_model(head, self._stopped)
                return {"ok": True}
            except Exception as ex:  # ошибка уходит вызывающему в ответе {ok: False, error}, сервис не падает
                return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}

    # --- цикл слушателя --------------------------------------------------- #
    def _listener_socket(self) -> Any:
        """Открыть слушатель на своём порту (одна дверь для сервиса и тестов)."""
        from multiprocessing.connection import Listener
        return Listener(("127.0.0.1", self.port), authkey=self.authkey)

    def _sleep_accept(self, listener: Any) -> None:
        """Поставить на приём короткий таймаут — иначе выход зависит от ОС.

        Закрытие слушателя из другого потока прерывает блокирующий `accept()` на
        Windows, но НЕ на Linux: там поток приёма остаётся в `accept` навсегда, и
        сервис не выходит ни по простою, ни по `shutdown`. С таймаутом приём
        возвращается сам, и флаг выхода (`_stopped`) читается в цикле — это
        одинаково надёжно на обеих ОС и не полагается на поведение `close`.

        Таймаут ставится на сокет СЛУШАТЕЛЯ (`_listener._socket`), а не на
        `Listener`: у него самого параметра нет. Принятое соединение таймаут не
        наследует — `SocketListener.accept` переводит его в блокирующий режим
        (`setblocking(True)`), поэтому длинный счёт и `recv` клиента им не тронуты.
        Не вышло добраться до сокета (другая реализация `Listener`, чужой объект) —
        молча живём как раньше: приём разбудит закрытие или `wake`, а сломать
        сервис на этом месте хуже, чем задержать выход на срок опроса.
        """
        with contextlib.suppress(Exception):
            listener._listener._socket.settimeout(POLL_ACCEPT_S)

    def _shutdown_listener(self, listener: Any) -> None:
        """Снять приём со слушателя — ДО его закрытия, иначе на Linux порт живёт.

        ЗАЧЕМ. `close()` слушателя из ДРУГОГО потока не снимает сокет, пока поток
        приёма сидит в `accept()`: на Linux ядро держит сокет и продолжает
        ПРИНИМАТЬ новые подключения до возврата из `accept` — у нас это срок опроса
        (`POLL_ACCEPT_S`). То есть после `stop()` уже остановленный сервис ещё
        успевает ответить клиенту, который в этот миг искал живой сервис.
        `shutdown(SHUT_RDWR)` прекращает приём сразу и будит `accept`; на Windows он
        безвреден: там приём прерывает уже `close`, а по несоединённому сокету
        `shutdown` просто отказывает (это и глушим).

        Путь к сокету — тот же, что у `_sleep_accept` (`_listener._socket`): у него
        и стоит срок опроса. Закрытый слушатель снимать нечем и не нужно:
        `Listener.close()` обнуляет внутренности (`_listener`), а шум в потоке
        приёма на уже закрытом сокете был бы отказом службы на ровном месте.
        Другой реализации `Listener` не знаем — тогда молча живём как раньше:
        приём снимет срок опроса, а порт освободит `close`. Сломать сервис на этом
        месте хуже, чем выйти на срок позже.
        """
        inner = getattr(listener, "_listener", None)
        sock = getattr(inner, "_socket", None)
        if sock is None:
            return                    # слушатель уже закрыт — снимать нечего
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)

    def wake(self) -> None:
        """Разбудить цикл приёма своим подключением — быстрый выход из `accept`.

        Второй ход к тому же делу, что таймаут (`_sleep_accept`): закрытие
        слушателя будит `accept` не везде, поэтому приём опрашивается, но опрос
        стоит до `POLL_ACCEPT_S` на каждый выход. Своё подключение снимает эту
        задержку вовсе: `accept` возвращается сразу, а флаг `_stopped` уже
        выставлен — цикл выходит.

        Соединение НАСТОЯЩЕЕ (адрес и ключ свои): подделка на уровне сокета
        потребовала бы второй копии рукопожатия `Listener`. Сервиса уже нет
        (порт молчит) или ключ не тот — не беда: выходу это не мешает, таймаут
        добьёт ожидание. Порт переиспользован ЧУЖИМ процессом — тот ответит
        отказом рукопожатия, и мы это глушим: чужой процесс не наш и трогать его
        нечем. Соединение закрываем САМИ и сразу, не ожидая ответа: будим, а не
        разговариваем. Оставленное открытым, оно держало бы приём в рукопожатии
        `deliver_challenge` до самого таймаута — то есть дало бы ровно ту задержку
        выхода, ради снятия которой будильник и заведён.
        """
        listener = self._listener
        if listener is None:
            return                    # слушателя нет — будить нечего
        try:
            sock = socket.create_connection(("127.0.0.1", self.port),
                                            timeout=POLL_ACCEPT_S)
        except OSError:
            return                    # порт уже молчит — будить некого
        try:
            sock.sendall((WAKE_COMMAND + "\n").encode("utf-8"))
        except OSError:
            pass                      # слушатель закрылся на гонке — выход уже решён
        finally:
            with contextlib.suppress(OSError):
                sock.close()

    def open_listener(self) -> Any:
        """Открыть слушатель: порт занят и соединения принимаются.

        Отдельным шагом от цикла приёма нарочно: адрес сервиса публикуется ТОЛЬКО
        после этого вызова. Обратный порядок (в файл, потом слушатель) оставлял в
        файле адрес, по которому ещё никто не отвечает, — а между записью и
        слушателем стоит `_Service.__init__` с импортом torch (замер: адрес в файле
        на 0.16 с, порт принимает на 1.75 с). Клиент читал адрес, получал отказ
        соединения и решал «сервис не поднялся», а заведённый им процесс жил до
        простоя. Повторный вызов (цикл приёма) слушателя не переоткрывает.

        Здесь же на приём ставится таймаут (`_sleep_accept`): цикл приёма обязан
        сам проверять флаг выхода, а не ждать, что его разбудит `close`.
        """
        if self._listener is None:
            self._listener = self._listener_socket()
            self._sleep_accept(self._listener)
        return self._listener

    def serve_thread(self) -> None:
        """Приём подключений, пока сервис не остановлен.

        Каждое соединение обслуживается СВОИМ потоком: подключений может быть
        несколько разом (пул роликов), и последовательный приём задерживал бы
        второго клиента на всё время счёта первого — слоты бы не работали.

        Приём НЕ блокирующий навсегда (`_sleep_accept`): он ждёт соединения
        коротким сроком и между ожиданиями проверяет `_stopped`. Так выход сервиса
        не зависит от того, прерывает ли ОС `accept()` закрытием слушателя: на
        Windows прерывает, на Linux нет — и раньше именно на Linux сервис не
        выходил ни по простою, ни по `shutdown`. Часы внутри приёма не заводятся
        нарочно: простой считает отдельный сторож (`_monitor_idle`).
        """
        listener = self.open_listener()
        try:
            # `self._listener` здесь НЕ переписываем: `open_listener` уже положил
            # туда тот самый слушатель, а лишняя запись затирала бы подменённый в
            # тесте (и `stop` закрывал бы не тот объект, которым принимаем).
            while not self._stopped.is_set():
                if self._stopped.is_set():
                    return
                try:
                    conn = listener.accept()
                except OSError:
                    # Слушатель закрыт сторожем ИЛИ сработал таймаут опроса
                    # (`socket.timeout` — тоже OSError). Различать их не нужно:
                    # решение принимает флаг остановки, а НЕ причина отказа. Раньше
                    # здесь стоял безусловный `return`, и на Linux он означал выход
                    # по таймауту, а не по остановке: цикл умирал через
                    # `POLL_ACCEPT_S` после старта, и сервис перестал бы принимать
                    # соединения вовсе (при этом снаружи это выглядело как «вышел по
                    # простою» — дефект прятался).
                    if self._stopped.is_set():
                        return
                    continue
                except AuthenticationError:
                    # Чужой ключ или оборванное рукопожатие: `accept()` у
                    # Connection сам проверяет `authkey`. Поднимать это в потоке
                    # приёма нельзя — необработанное исключение в потоке убивает
                    # цикл, то есть незваный гость останавливал бы сервис для всех.
                    continue
                self._start_worker(self.handle, conn)
        finally:
            # Приём кончился: снимаем его ДО закрытия (`_shutdown_listener`), и
            # только потом освобождаем порт. Закрытие слушателя без снятия приёма
            # оставляет его принимающим до возврата из `accept` (на Linux — весь
            # срок опроса), и остановленный сервис ещё отвечает клиенту.
            self._shutdown_listener(listener)
            with contextlib.suppress(Exception):
                listener.close()

    def _monitor_idle(self, poll_s: float = 0.2) -> None:
        """Завершить сервис после простоя (счёт не идёт и срок вышел)."""
        while not self._stopped.wait(poll_s):
            if self._idle_expired():
                self.emit("сервис моделей: простой {sec}с — завершаюсь, "
                          "видеопамять свободна", sec=int(self.idle_s))
                self.stop()
                return

    def _idle_expired(self) -> bool:
        """Пора завершаться? Нет — если идёт счёт или не истёк срок простоя."""
        with self._lock:
            active, last = self._active, self._last_used
        return active == 0 and (time.monotonic() - last) >= self.idle_s

    def stop(self) -> None:
        """Остановить сервис: цикл приёма выйдет, процесс завершится сам.

        Три хода, и все нужны. Флаг `_stopped` — решение о выходе: его читает
        таймаутный приём (`_sleep_accept`) сам. Своё подключение (`wake`) снимает
        ожидание опроса: без него выход занимал бы до `POLL_ACCEPT_S`, а с ним —
        сразу. `shutdown` слушателя (`_shutdown_listener`) — ДО закрытия: на Linux
        `close()` из другого потока не снимает сокет, пока поток приёма сидит в
        `accept()`, и ядро продолжает принимать новые подключения — то есть
        остановленный сервис ещё отвечает. Порядок именно такой: сперва флаг
        (приём увидит его даже от оборванного соединения), потом будильник, потом
        снятие приёма и закрытие слушателя.

        Флаг ставится ПОД ЗАМКОМ КЭША МОДЕЛЕЙ нарочно: поток, дочитавший веса уже
        после остановки, проверяет этот флаг под тем же замком перед публикацией
        (`_publish_model`). Без общего замка остановка вклинилась бы между его
        проверкой и записью, и остановленный сервис всё-таки оставил бы модель в
        общем кэше — то есть ровно та гонка, от которой заведён инвариант.
        """
        with _models_lock:
            self._stopped.set()
        self.wake()
        listener = self._listener
        if listener is None:
            return
        self._shutdown_listener(listener)
        with contextlib.suppress(Exception):
            listener.close()        # освобождает порт; приём уже снят выше

    def wait_until_stopped(self, timeout: float = 5.0) -> bool:
        """Дождаться выхода приёма И всех рабочих потоков сервиса.

        ИНВАРИАНТ, который здесь держится: после возврата True у сервиса не
        осталось живых рабочих потоков — ни обработчиков запросов, ни предзагрузки
        (`load`), ни сторожа простоя. Возврат False значит «за `timeout` не все
        успели»: сервис остановлен, но какой-то его поток ещё дочитывает веса или
        считает.

        ЗАЧЕМ. Раньше ожидался только поток приёма, а обработчики были демонами без
        учёта: «остановленный» сервис продолжал читать веса и клал модель в общий
        на процесс кэш уже после `stop()`. Следующий сервис (в тестах — следующий
        тест) находил там готовую модель, свои веса не читал — и это был плавающий
        отказ «прочитано 0 раз вместо одного». В бою то же самое опаснее: рендер,
        выгружающий сервис ради VRAM, мог стартовать, пока поток прошлого сервиса
        ещё держал карту.

        Срок ОДИН на всех, а не свой каждому потоку: иначе ожидание N потоков
        длилось бы N × timeout. Приём ждём ПЕРВЫМ: новых рабочих потоков, кроме
        него, не заводит никто, — значит после его выхода список рабочих уже не
        растёт, и оставшихся можно просто дождаться.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        accept = self._accept_thread
        if accept is not None:
            accept.join(timeout=max(0.0, deadline - time.monotonic()))
            if accept.is_alive():
                return False
        while True:
            with self._workers_lock:
                alive = [worker for worker in self._workers if worker.is_alive()]
            if not alive:
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            alive[0].join(timeout=left)

    def start_monitor(self) -> threading.Thread:
        """Запустить сторожа простоя — ОТДЕЛЬНО от цикла приёма.

        Отдельным шагом нарочно: боевой процесс (`main`) обслуживает соединения в
        СВОЁМ потоке, и сторожа туда не заводил никто. Приём и простой — два
        разных дела, и склеенные в одном `start_thread` они оставляли боевой сервис
        БЕЗ выгрузки по простою: процесс жил до перезагрузки и держал видеопамять,
        которую просят рендер и AE (ровно этот дефект и был).

        Сторож идёт через `_start_worker`: он рабочий поток сервиса, и остановка
        обязана его дождаться (он выходит по тому же флагу `_stopped`).
        """
        monitor = self._start_worker(self._monitor_idle)
        self._monitor_thread = monitor
        return monitor

    def start_thread(self) -> threading.Thread:
        """Запустить приём и сторожа простоя; ждать — `wait_until_stopped`."""
        accept = threading.Thread(target=self.serve_thread, daemon=True)
        self._accept_thread = accept
        accept.start()
        self.start_monitor()
        return accept

    def run_threads(self) -> None:
        """Боевой запуск: сторож простоя живёт, пока идёт цикл приёма.

        Сторож — ДО цикла: слушатель к этому мигу уже открыт (`open_listener`
        вызывается до публикации адреса), и клиенты, подключившиеся первыми, не
        должны ждать, пока мы заведём часовой поток.
        """
        self.start_monitor()
        self.serve_thread()


# --------------------------------------------------------------------------- #
# Клиент: подключиться, попросить, вернуть слова
# --------------------------------------------------------------------------- #
def _authkey(data: dict[str, Any]) -> bytes:
    """Ключ соединения из файла адреса.

    В файле ключ лежит ШЕСТНАДЦАТЕРИЧНОЙ строкой (JSON не умеет байты), и
    раскодировать его обязан клиент: `listener` получает настоящие байты, и
    сравнение с ASCII-формой hex-строки давало `AuthenticationError: digest
    received was wrong` на верном ключе. Мусор в поле — тоже отказ соединения,
    но не падение: пусть клиент скажет «сервиса нет» и уйдёт на запасной путь.
    """
    raw = str(data.get("authkey") or "")
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return b""


def _request(cmd: str, data: dict[str, Any], payload: Any = None,
             timeout: float = PING_TIMEOUT_S, attempts: int = 2) -> dict[str, Any]:
    """Один запрос к сервису через `multiprocessing.connection.Client`.

    `attempts` — сколько раз пробуем ОТКРЫТЬ соединение. Две попытки нужны ровно
    на гонку старта: слушатель мог ещё не подняться (мы только что прочитали файл
    адреса), а спустя миг он уже принимает. Больше двух попыток при мёртвом порте
    — это уже ожидание того, чего не будет: вызывающий должен быстро узнать
    «сервиса нет» и уйти на запасной путь.

    Таймаут один на попытку, а не на весь цикл: иначе мёртвый порт, потративший
    срок на первую попытку, получал бы вторую с уже истёкшим сроком и висел бы
    молча. `OSError` с флагом `e.persistent` — это отказ платформы (порт молчит,
    ключ не тот), и повторять его незачем: сразу наружу.
    """
    host = str(data.get("host") or "127.0.0.1")
    port = int(data.get("port") or 0)
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(0.2)
        try:
            with connection.Client((host, port), authkey=_authkey(data)) as conn:
                conn.send((cmd, payload))
                if not conn.poll(timeout):
                    raise ServiceUnavailable("сервис моделей не ответил за %.0fс" % timeout)
                resp = conn.recv()
            if not isinstance(resp, dict):
                raise ServiceUnavailable("сервис моделей ответил не словарём")
            return resp
        except ServiceUnavailable:
            raise
        except OSError as ex:
            last = ex
            if getattr(ex, "persistent", False):
                break             # порт закрыт наглухо — повторять нечего
        except Exception as ex:   # чужой ключ, обрыв, битый pickle
            last = ex
    raise ServiceUnavailable("%s: %s" % (type(last).__name__, last))


# Флаги создания процесса сервиса на Windows. Оба нужны и оба проверены замером:
# окна в докстринге модуля (`DETACHED_PROCESS` вместо `CREATE_NO_WINDOW` — счёт
# вшестеро медленнее, потому что у оторванного процесса дорог каждый
# `CreateProcess`, а он там на каждое окно распознавания — это `ffmpeg`).
CREATE_NO_WINDOW = 0x08000000        # консоль сервису не нужна и окна не появляется
CREATE_NEW_PROCESS_GROUP = 0x00000200  # своя группа: Ctrl+C родителя до сервиса не дойдёт
# Отдельной константой — чтобы её видно было в тесте: этот флаг здесь стоять НЕ
# должен, и «почему» расписано в докстринге модуля.
DETACHED_PROCESS = 0x00000008


def service_creationflags(nt: bool | None = None) -> int:
    """Флаги `subprocess` для процесса сервиса: на Windows — без консоли и своей группой.

    Отдельной функцией, а не строкой внутри `_spawn`: ровно её и проверяет тест,
    которому карта и веса не нужны вовсе (см. `tests/test_model_service.py`).
    `nt` — для теста POSIX-ветки на Windows-машине; None — по факту платформы.
    """
    if not (os.name == "nt" if nt is None else nt):
        return 0                          # POSIX: свою сессию задаёт start_new_session
    return CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def _spawn() -> subprocess.Popen[Any]:
    """Запустить процесс сервиса отдельно от нас (`-m core.model_service --serve`).

    Процесс обязан ПЕРЕЖИТЬ того, кто его позвал: сервис заводят ролики нарезки,
    а живёт он дольше всех. Поэтому — своя сессия (POSIX) и своя группа процессов
    на Windows (Ctrl+C родителя до сервиса не доходит). Окна сервис не открывает:
    `CREATE_NO_WINDOW` — процесс без консоли.

    `DETACHED_PROCESS` тут НЕ стоит нарочно, хотя отвязывает от консоли родителя
    надёжнее: у оторванного процесса каждый `CreateProcess` дороже в разы
    (замер — в докстринге модуля), а окна GigaAM читает через `ffmpeg`, то есть
    платит за запуск на каждом окне. Вывод сервиса уходит в родительскую консоль:
    там его и читает человек (`console_emit` сервиса печатает туда же).

    Возвращаем `Popen` — по нему видно, не умер ли сервис на старте (нет torch,
    нет весов): ждать адрес от мёртвого процесса бессмысленно.
    """
    from core.app_meta import module_cmd
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = service_creationflags()
    else:
        kwargs["start_new_session"] = True
    # Каталог состояния называем ребёнку ЯВНО: сервис обязан писать адрес ровно
    # туда, где его ждёт клиент. Одного унаследованного `REELSI_JOB_LOCK` мало —
    # путь состояния читается при импорте `core.jobstate`, и порядок импортов в
    # ребёнке мог бы дать другой лок (тогда клиент ждал бы адрес впустую).
    env = child_env()
    env[_hint_env(ENV_STATE)] = _settings_path()
    return subprocess.Popen(module_cmd("model_service", "--serve"), env=env,
                            cwd=paths.root(),
                            **kwargs)


def _wait_settings(path: str, proc: subprocess.Popen[Any] | None, timeout: float,
                   after: float | None = None,
                   sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic) -> dict[str, Any] | None:
    """Ждать адрес ЖИВОГО сервиса не дольше `timeout`.

    Одного «файл появился» мало: к моменту запуска нового процесса по этому пути
    может лежать адрес СТАРОГО, уже мёртвого сервиса — и в нём живой pid (номер
    успел достаться другому процессу). Прошлый вариант на этом и попадался:
    `_wait_settings` возвращал мёртвый адрес, `_settings_alive` звал новый запуск,
    и так по кругу, пока не выйдет срок (на живых тестах это ровно минута).

    И второго «файл появился» мало: адрес, по которому ещё никто не слушает, —
    это не «сервис поднялся», а «файл успел записаться». Сервис импортирует torch
    в `_Service.__init__`, и адрес, записанный ДО слушателя, лежал в файле секунды,
    пока порт молчал: клиент читал его, получал отказ соединения и решал «сервиса
    нет» — а процесс, который он сам завёл, оставался жить до простоя. Поэтому
    принимаем запись, только если сервис по ней ОТВЕЧАЕТ (`_settings_alive`).

    И запись принимаем только сделанную ПОСЛЕ старта процесса (`after` — метка
    времени из `time.time()`, ею же помечен файл). Умер ли процесс, смотрим по
    РОДИТЕЛЬСКОМУ `Popen`: ошибка запуска (нет torch, нет весов) видна сразу, и
    ждать её бессмысленно.
    """
    deadline = clock() + timeout
    while clock() < deadline:
        if proc is not None and proc.poll() is not None:
            return None
        if after is None or _settings_mtime(path) >= after:
            data = read_settings(path)
            if data and _settings_alive(data):
                return data
        sleep(0.1)
    return None


def _settings_mtime(path: str) -> float:
    """Время последней записи файла адреса; 0.0 — файла нет."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _is_stale(path: str) -> bool:
    """Файл адреса остался от МЁРТВОГО сервиса (или его нет) — его можно убрать.

    Убираем именно мёртвый: у живого сервиса файл — его собственность, и стирать
    его значит отрезать от сервиса других клиентов (например, идущий ролик).

    «Живой» здесь значит ЖИВОЙ СЕРВИС, а не «номер занят кем-то»: номер процесса
    переиспользуется, и после перезагрузки в старом файле стоит чужой процесс.
    Такой файл — брошенный, и держать его на диске незачем: иначе он выглядел бы
    как живой сервис и новый не записался бы (`ensure_started`).
    """
    data = read_settings(path)
    if data is None:
        return True
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        return not _pid_is_service(pid, data)
    return False


def _start_mark_path(path: str) -> str:
    """Файл-заявка «сервис завожу я» — рядом с файлом адреса."""
    return path + ".start"


def _claim_start(path: str, stale_s: float = START_CLAIM_STALE_S) -> bool:
    """Взять право завести сервис: заявка создаётся АТОМАРНО (`O_EXCL`).

    Двоим клиентам, стартовавшим разом, одного `_settings_alive` мало: оба видят
    «сервиса нет» и оба заводят свой процесс — на карте оказываются два
    CUDA-контекста и две копии весов (замер: два процесса и 2.45 с на прогрев
    вместо 1.93 с, а лишний сервис живёт до простоя и держит видеопамять).
    Взявший заявку заводит сервис, остальные ждут его адрес.

    Заявка старше `stale_s` — брошенная (стартер упал, не дойдя до `_release_start`):
    её убираем, чтобы сервис можно было завести снова. Не создать заявку вовсе (нет
    прав, экзотическая файловая система) — не отказ: заводим сервис, как раньше.
    """
    mark = _start_mark_path(path)
    try:
        fd = os.open(mark, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(mark) >= stale_s:
                os.remove(mark)
        except OSError:
            pass                  # заявку сняли прямо сейчас — попробуем ещё раз
        return False
    except OSError:
        return True
    os.close(fd)
    return True


def _release_start(path: str) -> None:
    """Снять заявку: сервис поднят (или завести его не вышло)."""
    with contextlib.suppress(OSError):
        os.remove(_start_mark_path(path))


def ensure_started(timeout: float = START_TIMEOUT_S, path: str | None = None) -> bool:
    """Есть ли живой сервис: живой — True, завели новый — True, не вышло — False.

    Порядок ровно такой:
    1. файл адреса и живой сервис по нему → подключаемся, ничего не запускаем
       (второй ролик не поднимает второй сервис — на этом стоит вся экономия);
    2. файла нет или он мёртвый → убираем мёртвый файл, берём заявку на старт
       (`_claim_start`) и запускаем процесс, ожидая адрес, записанный ПОСЛЕ его
       старта; не взяли заявку — сервис уже поднимает кто-то другой, ждём его адрес
       (два сервиса на одну карту — это два CUDA-контекста и две копии весов);
    3. новый сервис адреса не дал (упал на старте) → False, нарезка считает
       локально.

    Мёртвый сервис мы НЕ ищем по имени процесса и не убиваем: «файл есть, порт
    молчит» — это оставшийся от аварии файл, а не живой сосед (процессы по имени
    в проекте не трогают никогда). Новый сервис занимает свободный порт, файл
    переписывается — старый процесс, если он всё-таки жив, останется без клиентов
    и завершится по простою сам.

    Живой сервис ДРУГОЙ ВЕРСИИ или ДРУГОГО УСТРОЙСТВА гасим и заводим заново
    (`_stop_and_clear_stale`): иначе после обновления кода машина считала бы старым
    кодом, а после смены «где считать модели» — на прежнем устройстве до конца
    простоя. Это тот же путь, что «сервиса нет»: почистили адрес, взяли заявку,
    подняли новый.
    """
    p = path or _settings_path()
    build = _code_fingerprint()        # отпечаток — чтение пяти файлов: считаем один раз
    data = read_settings(p)
    if _settings_alive(data) and not _settings_mismatch(data, build):
        return True
    _stop_and_clear_stale(p, build)
    deadline = time.monotonic() + timeout
    while not _claim_start(p):
        if _settings_alive(read_settings(p)):
            return True
        if time.monotonic() >= deadline:
            return False               # чужой старт не удался — считаем, что сервиса нет
        time.sleep(0.1)
    try:
        # Заявку мы могли взять сразу после чужого старта: сервис уже поднялся, а
        # адрес мы ещё не увидели (стартер снимает заявку ЗА адресом). Проверяем —
        # иначе на карте оказались бы два сервиса ровно в этом окне.
        if _settings_alive(read_settings(p)):
            return True
        return _spawn_and_wait(p, deadline)
    finally:
        _release_start(p)              # чужой клиент теперь ждёт адрес, а не заводит свой


def drop_stale(path: str | None = None) -> bool:
    """Погасить сервис другой версии кода или другого устройства. True — погасили.

    Зовётся на старте сервера (`webui.main`): после обновления кода на машине может
    остаться живой сервис прежней версии, и первый же запрос клипа пошёл бы к нему.
    Живого сервиса СВОЕЙ версии это не касается вовсе: его трогать нечего — он и есть
    нужный (`ensure_started` в этом случае просто подключается к нему).

    Новый сервис здесь НЕ заводится нарочно: старт сервера не считает ничего, и
    поднимать процесс с весами (минуты VRAM до простоя) ради проверки версии значило
    бы держать карту занятой всё время, пока открыт интерфейс.
    """
    p = path or _settings_path()
    build = _code_fingerprint()
    data = read_settings(p)
    if not (data and _settings_alive(data) and _settings_mismatch(data, build)):
        return False
    _restart_stale(p, data)
    return True


class _Killable(Protocol):
    """Что `_kill_started` нужно от процесса: номер и умение погаснуть.

    Протокол, а не `subprocess.Popen`: гасим и свой `Popen`, и процесс, о котором
    знаем ТОЛЬКО номер (`_PidProc` — сервис завёл кто-то другой, см. `drop_stale`).
    Требовать от второго `Popen` значило бы заводить его там, где его нет.
    """

    pid: int

    def kill(self) -> None: ...


def _kill_started(proc: _Killable, path: str) -> None:
    """Погасить СВОЙ несостоявшийся сервис — по PID, а не по имени.

    Клиент, который сам завёл сервис и не дождался его, обязан убрать за собой:
    иначе процесс живёт до простоя (пять минут) и всё это время держит
    видеопамять, которую просят рендер и AE. Гасим ровно свой процесс —
    `TerminateProcess` по своему PID (на POSIX — сигнал своему процессу). Искать
    «все python с model_service» по имени нельзя: рядом работают сервисы других
    копий интерфейса, и чужой сервис — не наша собственность.

    Заодно убираем оставшийся от него файл адреса: `_is_stale` снял бы его при
    следующем старте, но до тех пор любой клиент читал бы адрес мёртвого сервиса.
    """
    pid = int(getattr(proc, "pid", 0) or 0)
    try:
        proc.kill()
    except Exception as ex:
        console_emit("сервис моделей: свой процесс {pid} не погасился ({err})",
                     pid=pid, err=ex)
    else:
        console_emit("сервис моделей: гашу свой процесс {pid} — он не поднял слушателя",
                     pid=pid)
    data = read_settings(path)
    if data and int(data.get("pid") or 0) == pid:
        clear_settings(path)


def _spawn_and_wait(path: str, deadline: float) -> bool:
    """Запустить процесс сервиса и дождаться живого адреса (заявка уже наша)."""
    started = time.time()
    try:
        proc: subprocess.Popen[Any] = _spawn()
    except Exception as ex:
        console_emit("сервис моделей: не удалось запустить процесс ({err})", err=ex)
        return False
    left = max(0.0, deadline - time.monotonic())
    data = _wait_settings(path, proc, left, after=started)
    if data is None:
        console_emit("сервис моделей: процесс не поднял слушателя за {sec}с",
                     sec=int(left))
        _kill_started(proc, path)          # за собой: иначе держит VRAM до простоя
        return False
    # Отвечать по этому адресу сервис уже проверен (`_wait_settings` зовёт
    # `_settings_alive`): второго пинга здесь не нужно.
    return True


def call(cmd: str, payload: Any = None, timeout: float = TRANSCRIBE_TIMEOUT_S,
         ensure: bool = True, deadline: float | None = None) -> dict[str, Any]:
    """Выполнить команду сервиса. `ServiceUnavailable` — если его нет или он отказал.

    Перед запросом — `ping`: он дешёвый и отличает живой сервис от мёртвого файла
    адреса. Без него клиент уходил бы на запасной путь только по таймауту счёта
    (до 900 с) — то есть «Стоп» и упавший сервис подвешивали нарезку.
    """
    path = _settings_path()
    data = read_settings(path)
    if not _settings_alive(data, timeout=PING_TIMEOUT_S):
        if not ensure or (deadline is not None and time.monotonic() > deadline):
            raise ServiceUnavailable("сервис моделей не запущен")
        if not ensure_started(path=path):
            raise ServiceUnavailable("сервис моделей не стартовал")
        data = read_settings(path)
        if not _settings_alive(data, timeout=PING_TIMEOUT_S):
            raise ServiceUnavailable("сервис моделей не отвечает после старта")
    resp = _request(cmd, data or {}, payload, timeout=timeout)
    if not resp.get("ok"):
        raise ServiceUnavailable(str(resp.get("error") or "сервис моделей отказал"))
    return resp


def transcribe(wav_path: str, head: str) -> list[dict[str, Any]] | None:
    """Слова из сервиса: `list` — посчитано, None — сервиса нет (запасной путь).

    Форма ответа — ровно `[{w,start,end}]`, как у локального распознавания:
    нарезка не должна знать, откуда пришли слова.

    `None` вместо исключения нарочно: вызывающий (ролик нарезки) обязан уметь
    посчитать сам, и разбирать, почему именно сервиса не оказалось, ему не нужно.
    """
    try:
        resp = call("transcribe", {"wav": wav_path, "head": head})
    except ServiceUnavailable:
        return None
    words = resp.get("words")
    return list(words) if isinstance(words, list) else None


def unload() -> bool:
    """Выгрузить веса из памяти сервиса, оставив сам процесс. False — сервиса нет.

    Нужно шагу рендера: ему карта нужна целиком, а поднимать сервис заново после
    выгрузки — это те же секунды чтения весов, что и при холодном старте.
    """
    try:
        call("unload")
    except ServiceUnavailable:
        return False
    return True


def breath_probs(wav_path: str, spans: Sequence[tuple[float, float]]) -> Any:
    """Вероятности CED по границам окон (`wav_path`) — массив, считает сервис.

    `ServiceUnavailable` — сервиса нет или он отказал: вызывающему (детектору
    вздохов) это сигнал считать в своём процессе и взять файловый замок `.gpu`.
    Форма ответа — та же матрица `(len(spans), num_labels)`, что у локального
    счёта: нарезка не должна знать, откуда пришли числа.
    """
    resp = call("breath", {"wav": wav_path,
                           "spans": [[float(a), float(b)] for a, b in spans]})
    probs = resp.get("probs")
    if not isinstance(probs, list):
        raise ServiceUnavailable("сервис моделей не отдал вероятности вздохов")
    import numpy as np
    return np.asarray(probs, dtype="float32")


def emotion_probs(window: Any, sr: int) -> dict[str, float]:
    """Вероятности эмоций окна звука (`{angry, sad, neutral, positive}`) — из сервиса.

    Окно уже прочитано клиентом из исходника Камеры 1, поэтому по проводу едет оно
    само, а не путь: читать его второй раз в сервисе было бы нечем.
    `ServiceUnavailable` — сервиса нет: модель эмоций поднимает вызывающий.
    """
    resp = call("emo", {"window": window, "sr": int(sr)})
    probs = resp.get("probs")
    if not isinstance(probs, dict):
        raise ServiceUnavailable("сервис моделей не отдал вероятности эмоций")
    return {str(k): float(v) for k, v in probs.items()}


def preload_breath() -> bool:
    """Прочитать веса CED в сервисе заранее. False — сервиса нет (считаем локально).

    Прогрев и проверка готовности — одно и то же действие: если сервис жив и веса
    CED у него в памяти, детектор вздохов ролика не будет ждать чтения весов и не
    возьмёт файловый замок. Отказ (нет весов, нет transformers) — тот же False:
    нарезка уходит на прежний путь, под `.gpu`, и говорит причину в журнал.
    """
    try:
        call("load", {"ced": True})
    except ServiceUnavailable:
        return False
    return True


def preload_emo() -> bool:
    """Прочитать голову `emo` в сервисе заранее. False — сервиса нет (считаем здесь).

    Одна дверь у расчёта силы жёлтых: модель эмоций держит сервис, и повторный
    расчёт не платит за чтение весов и `import torch` (раньше `load_emo_model` шёл
    на каждый расчёт и `release_emo` — сразу после).
    """
    try:
        call("load", {"emo": True})
    except ServiceUnavailable:
        return False
    return True


def preload(head: str) -> bool:
    """Прочитать веса головы заранее, не считая слов. False — сервиса нет.

    Прогрев: первый ролик после него не платит за чтение весов внутри своего
    счёта. Нужно замеру (в таблице «загрузка» и «счёт» — разные столбцы), но
    годится и боевому пути: поднять сервис до нарезки дешевле, чем посреди неё.
    """
    try:
        call("load", {"head": head})
    except ServiceUnavailable:
        return False
    return True


def vram_stats() -> dict[str, int] | None:
    """Пик памяти сервиса в МиБ; None — сервиса нет (мерить не у кого).

    `ensure=False`: спросить память у мёртвого сервиса нельзя, но и поднимать
    новый ради одной строки в отчёте не нужно — стенд зовёт это после прогона.
    """
    try:
        resp = call("stats", ensure=False)
    except ServiceUnavailable:
        return None
    return {"max_memory_allocated": int(resp.get("max_memory_allocated") or 0),
            "max_memory_reserved": int(resp.get("max_memory_reserved") or 0)}


def shutdown() -> bool:
    """Попросить сервис завершиться и дождаться, что порт умолк. False — его нет.

    Просьба уходит живому сервису (`shutdown` в `call` заводит его, только если
    его не было, — здесь это лишнее: гасить нечего). Ответ приходит ДО остановки:
    обрыв соединения — это не отказ, а принятая просьба.
    """
    try:
        call("shutdown", ensure=False)
    except ServiceUnavailable:
        return False
    path = _settings_path()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _settings_alive(read_settings(path), timeout=0.5):
            return True
        time.sleep(0.1)
    return True


def service_alive() -> bool:
    """Живой ли сервис (для диагностики и тестов)."""
    return _settings_alive(read_settings(_settings_path()))


def model_cap() -> dict[str, Any]:
    """Потолок моделей на этой машине — без запуска сервиса и БЕЗ импорта torch.

    Ответ интерфейсу (⚙ → «Нарезка» → «Роликов одновременно»): на каком устройстве
    считает сервис, сколько распознаваний идёт разом и выбрано ли устройство вручную.
    Считает ТЕМ ЖЕ кодом, что сервис (`slot_count`), — иначе подпись в настройках
    обещала бы одно число, а сервис держал другое.

    Живой сервис НЕ поднимаем: подпись в настройках не стоит минут чтения весов и
    занятой видеопамяти. И `torch` тут не импортируем — намеренно: сервер интерфейса
    живёт без него, а импорт тянет за собой CUDA и сотни мегабайт контекста, которых
    в UI-процессе не должно быть вовсе. Правило проверяется тестом в подпроцессе
    (`sys.modules` после вызова). Поэтому:
      - выбор ВРУЧНУЮ (`видеокарта`/`процессор`) не требует догадок вообще, и
        перемычка `REELSI_MODEL_SERVICE_DEVICE` — тоже: по ней сервис и пришпилен
        к устройству (стенд, тесты), значит и подпись обязана говорить её слово;
      - «авто» решает `core.device.auto_device_without_torch` — тем же порядком, что
        `pick_device` (cuda -> mps -> cpu), но без torch: карту видно `nvidia-smi`.
        До этого здесь стоял безусловный `cpu`, и на машине с видеокартой подпись
        обещала процессор, тогда как сервис считал на карте;
      - видеопамять для слотов берётся у `nvidia-smi` (`device_nvidia_free_mib`).
    """
    chosen = _device_override() or model_device_cfg()
    if chosen != device.DEVICE_AUTO:
        dev = chosen                                   # выбор человека: гадать не о чем
    else:
        # «Авто». torch в этом процессе мог остаться от соседа (`sys.modules`): тогда
        # его ответ и точнее, и бесплатнее — нового импорта не будет. Нет torch —
        # решает `core.device` по признакам, видимым без него, и решает ОДНОЙ
        # функцией с сервисом: разойтись в ответе подпись и сервис не могут.
        try:
            if sys.modules.get("torch") is not None:
                dev = device.pick_device(force=device.DEVICE_AUTO)
            else:
                dev = device.auto_device_without_torch()
        except Exception as e:
            # Подпись в статусе — не выбор устройства: сбой здесь показываем «cpu» и пишем в журнал.
            log.warning("устройство для подписи статуса не определено (%s), показываю cpu", type(e).__name__)
            dev = "cpu"
    auto = chosen == device.DEVICE_AUTO
    if dev == "cpu":
        return {"device": dev, "slots": slot_count(device=dev),
                "auto": auto, "setting": chosen}
    free = device_nvidia_free_mib()
    return {"device": dev, "slots": slot_count(free_mib=free, device=dev),
            "auto": auto, "setting": chosen}


def engine_head(engine: str) -> str:
    """Какая голова GigaAM нужна движку нарезки; '' — движок не через GigaAM.

    Сервис умеет ровно головы GigaAM: `ctc:*` (faster-whisper-подобные CTC) и
    прочие движки остаются локальными — у них своя загрузка и своя VRAM. Пустая
    строка значит «сервис этим движком не занимается», и нарезка идёт прежним
    путём: спрашивать сервис о том, чего он не умеет, незачем.

    Неизвестный движок — `ValueError` от самого каталога (`engine_meta`) — это
    дефект конфигурации, а не «сервиса нет»: пусть падает, как падало.
    """
    from core import asr_backends
    meta = asr_backends.engine_meta(engine)
    if not meta or not meta.get("cut"):
        return ""
    return str(meta.get("gigaam") or "")


# --------------------------------------------------------------------------- #
# Точка входа процесса сервиса: `python -m core.model_service --serve`
# --------------------------------------------------------------------------- #
def _pick_port() -> int:
    """Свободный порт на 127.0.0.1. Гонку закрывает сам `Listener`:
    не успел занять — старт падает и повторяется с другим портом."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _clear_settings_if_own(path: str) -> None:
    """Убрать файл адреса, если он НАШ: чужой сервис его уже переписал — не трогаем.

    Признак владельца — `pid` внутри файла: у сервиса, который завёл себя заново на
    свободном порту, в адресе стоит ЕГО номер, и уходящий процесс не должен стирать
    чужой адрес (клиент читал бы пустое место, хотя живой сервис есть). Сверять
    время записи файла с меткой старта нельзя: адрес пишется ПОСЛЕ этой метки, и
    свой же файл выглядел бы чужим — адрес мёртвого сервиса оставался на диске.
    """
    data = read_settings(path)
    if data and int(data.get("pid") or 0) == os.getpid():
        clear_settings(path)


def _exit_if_threads_hold() -> None:
    """Гарантированный выход процесса сервиса после остановки потоков.

    Обычный выход (`return` из `main`) ждёт завершения ВСЕХ нечужих потоков, а
    сервис живёт долгоживущими потоками: приём, сторож простоя, обработчики
    соединений. Обслуживающие потоки — демоны и выход не держат, но если хоть один
    недемонический поток остался (чужая библиотека, рукопожатие `Listener`), сервис
    не завершится НИКОГДА: порт закрыт, файл адреса убран, а процесс висит и держит
    видеопамять, которую просят рендер и AE — ровно тот дефект, от которого заведён
    и простой. Поэтому после финализации (`_clear_settings_if_own`) процесс
    завершается жёстко. Убирать за собой больше нечего: файл адреса уже убран,
    веса — память процесса, буферы вывода сброшены (`_cli_out` печатает с flush).
    """
    lingering = [t for t in threading.enumerate()
                 if t is not threading.main_thread() and not t.daemon]
    if lingering:
        _cli_out("сервис моделей: потоки не завершились (%d) — выхожу принудительно"
                 % len(lingering))
    os._exit(0)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI сервиса: `--serve` — работать до простоя, `--status`/`--stop` — спросить.

    Весь вывод идёт через `_cli_out`: русский текст в cp1252-консоли Windows ронял
    `--stop`/`--status` ошибкой кодировки (см. `_cli_out`).
    """
    parser = argparse.ArgumentParser(prog="core.model_service",
                                     description="Долгоживущий сервис распознавания GigaAM")
    parser.add_argument("--serve", action="store_true", help="поднять сервис")
    parser.add_argument("--status", action="store_true", help="жив ли сервис")
    parser.add_argument("--stop", action="store_true", help="попросить сервис завершиться")
    parser.add_argument("--idle", type=float, default=None,
                        help="простой до завершения, с (по умолчанию %(default)s)")
    parser.add_argument("--slots", type=int, default=None,
                        help="сколько распознаваний разом (по умолчанию — по VRAM)")
    args = parser.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    if args.stop:
        _cli_out("остановлен" if shutdown() else "сервиса не было")
        return 0
    if args.status:
        alive = service_alive()
        _cli_out("жив" if alive else "нет сервиса")
        return 0 if alive else 1
    if not args.serve:
        parser.print_help()
        return 2

    idle = args.idle if args.idle is not None else _env_float(ENV_IDLE)
    path = _settings_path()
    port = _pick_port()
    key = secrets.token_bytes(32)
    service = _Service(port, key, idle_s=idle, slots=args.slots, emit=_cli_out)
    try:
        # Слушатель открываем ДО записи адреса. Порядок принципиален: в `_Service`
        # читаются веса устройства (импорт torch — секунды), и адрес, записанный
        # раньше слушателя, лежал в файле для клиентов, пока порт молчал: клиент
        # получал отказ соединения, решал «сервис не поднялся» и уходил на
        # локальный путь, а этот процесс жил до простоя и держал видеопамять.
        service.open_listener()
    except OSError as ex:
        # Порт увели между `_pick_port` и `Listener` — это не «сервис не поднялся»:
        # зовущий получит `ServiceUnavailable` от `ping` и заведёт новый процесс.
        _cli_out("сервис моделей: слушатель не открылся (%s)" % ex)
        clear_settings(path)
        return 1
    _write_settings(path, {"version": SETTINGS_VERSION, "host": "127.0.0.1",
                           "port": port, "pid": os.getpid(),
                           # Метка старта ЭТОГО процесса: по ней клиент отличает наш
                           # сервис от чужого, которому после перезагрузки достался
                           # тот же номер. Без неё `_pid_is_service` проверил бы только
                           # командную строку — этого мало против переиспользованного номера.
                           "created": _process_create_time(os.getpid()),
                           "authkey": key.hex(),
                           "idle": idle if idle is not None else DEFAULT_IDLE_S,
                           # Версия кода и устройство — ключи, по которым КЛИЕНТ решает,
                           # подключаться к этому сервису или погасить его и завести свой
                           # (см. `_settings_mismatch`). Без них поднятый до обновления
                           # сервис отвечал бы старым кодом, а выбранный «процессор» не
                           # применялся бы до конца простоя.
                           "build": _code_fingerprint(),
                           "device": _service_device()})
    try:
        # Слушатель уже открыт (`open_listener` выше), и сторож простоя заводится
        # ВМЕСТЕ с циклом приёма (`run_threads`). Без сторожа процесс не выходил по
        # простою никогда: клиенты уходили, а сервис держал веса и видеопамять до
        # перезагрузки — выгрузку ждали только тесты в своём потоке, боевой путь
        # её не заводил.
        service.run_threads()
    finally:
        # Файл адреса — собственность процесса: убрать его обязан тот, кто уходит,
        # в том числе когда цикл приёма вышел исключением (иначе клиенты читали бы
        # адрес мёртвого сервиса).
        _clear_settings_if_own(path)
    # Выход гарантирован, а не «как получится»: после остановки потоков процесс
    # обязан уйти (см. `_exit_if_threads_hold`) — иначе держит видеопамять.
    _exit_if_threads_hold()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReelsiError as e:
        # Пользовательская ошибка — текст в stderr и код 1, без трейсбека: сервис
        # заводят процессом из нарезки, и её журнал должен получить причину, а не
        # стек вызовов. Та же обёртка, что у остальных точек входа ядра.
        cli_error(e)
