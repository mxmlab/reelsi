# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты сервиса моделей: один раз загруженный GigaAM, слоты, простой, клиент.

ЧТО ПРОВЕРЯЕМ И ПОЧЕМУ ТАК.

Замер стендом (`tools/bench_gpu_cut.py`) показал, что в нарезке дороже всего не
счёт, а загрузка: 147 с на десять роликов, из них ожидание файлового замка —
медиана 55 с, загрузка моделей 4.6–7.2 с при счёте 3–6 с. Сервис моделей меняет
это на «веса в памяти, слоты вместо замка». Тесты стерегут РОВНО эти свойства:

1. модель грузится ОДИН раз на все запросы, а не на каждый;
2. слотов ровно N: при N=1 второй клиент ждёт, при N=2 оба считают разом;
3. второй старт подключается к живому сервису, мёртвый файл адреса — новый;
4. простой завершает процесс, `shutdown()` тоже; `unload()` освобождает веса;
5. чужой `authkey` не пускают внутрь;
6. сервис недоступен — нарезка считает локально и называет причину;
7. процесс сервиса заводится БЕЗ консоли, но не оторванным от неё
   (`CREATE_NO_WINDOW`, а не `DETACHED_PROCESS`): у оторванного процесса каждый
   запуск `ffmpeg` — а GigaAM читает им каждое окно распознавания — стоит в разы
   дороже, и счёт ролика через сервис вырастал вшестеро;
8. `preload` читает веса до первого ролика, а `stats` отдаёт память сервиса:
   иначе загрузка весов попадала в «счёт» ролика, а память в отчёте стояла нулём;
9. двое клиентов, стартовавших разом, заводят ОДИН сервис (заявка `_claim_start`),
   и веса при двух первых запросах читаются один раз;
10. каталог состояния (адрес, ключ, заявка) у клиента и у сервиса ОДИН — его
    называет одна функция, и тот же путь уходит ребёнку-сервису переменной
    окружения: разойтись по каталогу они не могут;
11. адрес, по которому ещё никто не отвечает, сервисом не считается (слушатель
    открывается ДО записи адреса, а ждём мы ответивший порт, а не файл);
12. клиент, сдавшийся по таймауту старта, гасит СВОЙ процесс по PID: иначе
    брошенный сервис живёт до простоя и держит видеопамять;
13. простой отсчитывается и в БОЕВОМ процессе: тот, кто поднимает сервис
    (`--serve`), заводит сторожа простоя вместе с циклом приёма. Без этого
    боевой сервис не выходил по простою никогда — тесты заводили сторожа сами
    (`start_thread`), а боевой путь его не заводил, и процесс жил до
    перезагрузки, держа видеопамять;
14. вывод CLI модуля (`--stop`, `--status`, отказ слушателя) переживает консоль
    в cp1252: русский текст уходил `UnicodeEncodeError`, то есть «Стоп» падал на
    собственном ответе владельцу;
15. гашение по PID трогает ТОЛЬКО проверенный процесс сервиса (номер не наш и не
    родителя, командная строка — ровно `… -m core.model_service … --serve`, метка
    старта совпадает с `created` из адреса). Без этого проверка версии убивала
    процесс pytest (сервис в этих тестах поднят ВНУТРИ него), а в бою — чужой
    процесс, которому после перезагрузки достался номер из старого файла адреса;
16. остановка снимает приём СРАЗУ: после `stop()` и реального выхода потока приёма
    новое подключение на тот же адрес не обслуживается. Одного `close()` для этого
    мало — на Linux закрытие дескриптора из другого потока не снимает слушающий
    сокет, пока поток сидит в `accept`, и ядро продолжает принимать подключения:
    умирающий сервис ещё отвечает клиенту (проверяется и на живом сокете, и на
    стенде с Linux-поведением слушателя);
17. после возврата `wait_until_stopped()` у сервиса НЕ осталось живых рабочих
    потоков — обработчиков запросов, предзагрузки и сторожа простоя, — и веса,
    дочитанные уже после `stop()`, в общий кэш моделей не публикуются. Без этого
    «остановленный» сервис продолжал читать веса, следующий сервис (в тестах —
    следующий тест) находил модель в кэше и свои веса не читал, а рендер,
    выгружающий сервис ради VRAM, мог стартовать, пока поток прошлого сервиса
    ещё держал карту.

ЧЕМ ЭТИ ТЕСТЫ НЕ ЗАВИСЯТ ОТ МАШИНЫ. Набор гоняется и на Linux, и без видеокарты,
поэтому всё платформенное и железное здесь названо ЯВНО, а не угадывается:

- `SO_EXCLUSIVEADDRUSE` — опция ТОЛЬКО Windows; берётся `hasattr`, и на POSIX
  «занятый порт» держится обычным `bind` (там `Listener` сам ставит `SO_REUSEADDR`,
  который уже слушаемый адрес не отдаёт). Прямое обращение к имени роняло тест
  `AttributeError` ещё до проверок;
- `vram_fits` зовётся с подменённым на `cuda` устройством: на машине без карты
  `_service_device()` отвечает «процессор», и проверка VRAM пропускалась целиком —
  тест зеленел бы, ничего не проверив (в Linux-прогоне так и было);
- флаги процесса (`service_creationflags(nt=…)`), слоты и потолок памяти берут
  устройство и числа параметрами, а `pick_device` перед проверкой карты спрятан за
  monkeypatch; `torch`, когда нужен, — `importorskip`, то есть на машине без него
  тест честно пропускается, а не падает.

Весов GigaAM и карты здесь нет и быть не должно: сервис поднимается НАСТОЯЩИЙ
(слушатель, слоты, клиент, ответ), а вместо `gigaam` подставляется модуль
(`REELSI_MODEL_SERVICE_SUBST`) — он считает загрузки и умеет ждать так, чтобы
параллельность была видна. Устройство сервиса — `cpu`: слотовый счёт тогда по
ядрам, и тест не трогает живую карту рабочей машины.

Мутации, которые обязаны красить эти тесты: убрать семафор слотов (N=1 перестанет
ждать), убрать кэш моделей (загрузок станет по числу запросов), не проверять
`pid` и `ping` в файле адреса (мёртвый файл будет считаться живым), снять проверку
`authkey` (чужой ключ пройдёт), вернуть `DETACHED_PROCESS` в флаги создания
процесса, убрать прогрев весов (`preload`) из стенда, снять заявку на старт
сервиса (двое заведут по сервису), замок загрузки весов (прочитают дважды) и замок
загрузки подменного модуля (файл подмены выполнится дважды — и счётчик весов
достанется другому объекту модуля),
снять чтение `REELSI_MODEL_SERVICE_STATE` из `state_path` и передачу её в `_spawn`
(каталог состояния у клиента и сервиса разъедется), вернуть в `_wait_settings`
`if data: return data` (адрес без живого порта сочтут сервисом) и убрать
`_kill_started` (брошенный сервис останется держать VRAM), убрать сторожа простоя
из боевого запуска (`serve_thread` вместо `run_threads`) — процесс `--serve`
перестанет выходить по простою, и это увидят и тест боевого процесса, и сторож
`tests/conftest.py`; вернуть `print` в CLI модуля — краснеет тест cp1252; снять
проверку номера в `_pid_is_service` (погашение убивает pytest), вернуть в «авто»
подписи безусловный `cpu` (карта на машине есть, а подпись говорит «процессор»),
убрать `shutdown` слушателя из `stop()` (стенд Linux отдаёт `accept` клиента,
пришедшего после закрытия, и тот получает ответ от остановленного сервиса),
перестать ждать рабочих потоков в `wait_until_stopped` и снять проверку флага перед
публикацией весов в кэш — краснеет тест инварианта остановки.
Кэш весов фиксатор профиля чистит и НА ВХОДЕ теста: без этого «первый запрос»
зависел бы от того, что оставил в памяти соседний тест.

ЧЕМ ТЕСТЫ НЕ ЗАВИСЯТ ОТ СОСЕДЕЙ. Сервис поднимается ВНУТРИ процесса pytest, и
соседние тесты делят с ним и процесс, и модуль, и глобалы сервиса. Заражение
шло одним путём: ключ соединения `b"test-key"` был ОДИН на все тесты, а номер
порта на Linux освободившийся отдаётся снова. Клиент теста, придя в сервис
СОСЕДА (файл адреса ему назвал `_settings_path`, а тот ещё жив — `stop()` не
ждёт выхода потока приёма), читал оттуда же и ключ, поэтому рукопожатие
проходило, ответ приходил живыми словами, а счётчик ПОДМЕНЫ этого теста
оставался нулевым («веса прочитаны 0 раз вместо одного»). Закрыто здесь в
ТЕСТАХ, а не в боевом коде:

- у каждого теста СВОЙ файл адреса (`tmp_path` фиксатора `ws`) и СВОЙ ключ
  (`_TEST_KEY`): адрес и ключ чужого сервиса этому тесту никто не называет;
- фиксатор `_track_started_services` дожидается РЕАЛЬНОГО выхода потоков своих
  сервисов (`_stop_registered_services`): `stop()` только просит выйти (флаг,
  снятие приёма и закрытие слушателя), а поток приёма завершается сам — и до его
  выхода сервис ещё принимает соединения: ровно это окно и заражало соседа.

Проверка «мёртвый файл адреса переписан» сравнивает КЛЮЧ записи, а не номер
порта: на Linux порт после закрытия сразу выдаётся снова, и новый сервис
законно получает тот же номер — проверка номера краснела на ровном месте.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЛОВЯТ САМИ. Путь «запрос ушёл в чужой живой сервис» — гонка
Linux, и на Windows она не воспроизводится: там закрытие слушателя прерывает
`accept`, и поток выходит сразу. Поэтому мутация «вернуть общий ключ и не ждать
выхода» на Windows остаётся зелёной (проверено) — закрывает её не утверждение
этого файла, а ожидание выхода сервиса в фиксаторе.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import conftest  # noqa: E402  (реестр процессов сервиса, поднятых этим прогоном)
from core import model_service  # noqa: E402

# Подмена головы GigaAM: считает загрузки, а параллельность видит по счётчику
# «сколько моделей считают прямо сейчас». Считает не мгновенно (`configure(wait=…)`)
# и умеет ЖДАТЬ соседа по счёту (`configure(together=…)`): без паузы два запроса
# могут разойтись во времени, и потолок слотов измерялся бы случайно.
_FAKE_MODULE = textwrap.dedent('''\
    """Подмена GigaAM для тестов сервиса моделей (не продуктовый модуль)."""
    import threading
    import time

    LOADED = 0
    _in_flight = 0
    max_concurrent = 0
    _lock = threading.Lock()
    _wait = 0.05
    _load_wait = 0.2
    # Сколько распознаваний ОБЯЗАНО считаться разом при этом вызове (0 — без
    # ожидания соседа). Ставит тест потолка слотов: без этого мерка
    # `max_concurrent` зависела бы от того, успел ли второй поток дойти до счёта,
    # пока первый спит, — и под нагрузкой (полный прогон, `-n auto`) показывала 1
    # при двух слотах. Ждём соседа по счётчику, а не по секундам.
    _together = 0


    class _Word:
        """Слово в форме пакета gigaam: `text`, `start`, `end`."""

        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end


    class _Result:
        """Ответ `transcribe`: у него есть `words` (как у настоящего gigaam)."""

        def __init__(self, words):
            self.words = words


    class _Model:
        """Пустышка головы GigaAM: считает, сколько её экземпляров считают разом."""

        def __init__(self, head):
            self.head = head

        def transcribe(self, wav, word_timestamps=False):
            global _in_flight, max_concurrent
            with _lock:
                _in_flight += 1
                if _in_flight > max_concurrent:
                    max_concurrent = _in_flight
                together = _together
            try:
                _await_together(together)
                time.sleep(_wait)
                return _Result([
                    _Word(self.head + "_word", 0.0, 0.5),
                    _Word("second", 0.5, 1.0),
                ])
            finally:
                with _lock:
                    _in_flight -= 1


    def _await_together(together):
        """Дождаться, пока разом считаются `together` распознаваний.

        Нужно там, где мерят потолок слотов: счётчик `max_concurrent` тогда
        говорит о семафоре сервиса, а не о том, как потоки легли на процессор.
        Срок ожидания — страховка от зависания: по нему не сходятся только
        сломанные слоты, и тогда тест краснеет честно (1 вместо 2), а не висит.
        """
        if together <= 1:
            return
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with _lock:
                if _in_flight >= together:
                    return
            time.sleep(0.005)


    def configure(wait=0.05, together=1):
        """Пауза счёта и обязательная параллельность (1 — считать как получится)."""
        global _wait, _together
        _wait = float(wait)
        _together = int(together)


    # Затвор чтения весов: тест остановки держит им загрузку открытой, чтобы у
    # сервиса был ЖИВОЙ рабочий поток в миг `stop()`. Пусто — чтение обычное.
    _gate = None
    waiting = 0            # сколько чтений стоит на затворе прямо сейчас


    def set_gate(event):
        """Повесить затвор на чтение весов; None — снять затвор."""
        global _gate
        _gate = event


    def load_model(head, download_root=None):
        global LOADED, waiting
        gate = _gate
        if gate is not None:
            with _lock:
                waiting += 1
            try:
                gate.wait(60.0)     # срок — страховка от вечного зависания теста
            finally:
                with _lock:
                    waiting -= 1
        time.sleep(_load_wait)          # веса читаются не мгновенно: иначе гонку не поймать
        with _lock:
            LOADED += 1
        return _Model(head)
''')

# Подмена, у которой НЕ грузятся веса: каждый запрос распознавания получает
# отказ сервиса. Так проверяем не «порта нет», а «сервис ответил ошибкой».
_BROKEN_MODULE = textwrap.dedent('''\
    """Подмена GigaAM, которая не умеет грузить веса (для проверки отказа)."""


    def load_model(head, download_root=None):
        raise RuntimeError("весов головы %s нет в кеше" % head)
''')

# Подмена для проверки гонки ЗАГРУЗКИ МОДУЛЯ: пауза на уровне модуля растягивает
# окно между проверкой кэша и записью в него, а строка в файле-метке показывает,
# сколько раз файл был выполнен. Без паузы потоки разошлись бы во времени, и тест
# остался бы зелёным даже без замка — то есть ничего бы не сторожил.
_RACE_MODULE = '''\
import time

time.sleep(0.05)          # окно гонки: два первых запроса обязаны столкнуться

with open({marker!r}, "a", encoding="utf-8") as fh:
    fh.write("run\\n")

VALUE = 42
'''


# --------------------------------------------------------------------------- #
# Стенд: подменный модуль, окружение и сервис на временном профиле
# --------------------------------------------------------------------------- #
def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


# Счётчик ключей теста: `itertools.count` — без общего изменяемого числа.
_KEY_COUNTER = itertools.count(1)


@pytest.fixture
def ws(tmp_path: Path) -> dict[str, Any]:
    """Временный профиль сервиса: свой файл адреса, свой звук и подменный модуль.

    Звук — НАСТОЯЩИЙ wav на секунду: распознавание читает его soundfile'ом, и без
    файла подменная модель просто не дойдёт до счёта (окна не будет вовсе), то
    есть потолок одновременных мерить было бы не на чем.

    Фиксатор помнит, сколько процессов сервиса уже завёл прогон, и на выходе
    гасит те, что появились при ЭТОМ тесте, — по PID своих запусков (реестр
    `conftest`), а не по имени: рядом работают сервисы других копий интерфейса и
    настоящие нарезки владельца. Иначе процесс живёт до простоя (пять минут на
    каждый такой тест) и всё это время держит видеопамять.
    """
    import numpy as np
    import soundfile as sf

    lock = tmp_path / "job.lock"
    lock.write_text("", encoding="utf-8")
    settings = tmp_path / "job.lock.modelsvc.json"
    wav = tmp_path / "asr.wav"
    sf.write(str(wav), np.zeros(16000, dtype="int16"), 16000, subtype="PCM_16")
    env = {
        # Сервис на процессоре: слотовый счёт по ядрам, живая карта не трогается.
        "REELSI_MODEL_SERVICE_DEVICE": "cpu",
        "REELSI_MODEL_SERVICE_SUBST": _write(tmp_path / "fake_gigaam.py", _FAKE_MODULE),
        "REELSI_MODEL_SERVICE_SLOTS": "1",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    # Кэш весов чистим и НА ВХОДЕ, не только на выходе: он живёт в процессе и
    # переживает тест, а «веса читаются один раз» — про ПЕРВЫЙ запрос. Без этого
    # тест зависел бы от того, кто и что оставил в кэше до него (так и краснел
    # `test_две_первые_просьбы_грузят_веса_один_раз` в одном прогоне из двух).
    model_service.unload_models()
    # Каталог слотов — временный: `core.jobstate` берёт путь при импорте, и без
    # подмены тест читал бы боевой файл адреса живой машины.
    original = model_service._settings_path
    model_service._settings_path = lambda: str(settings)  # type: ignore[assignment]
    spawned_before = len(conftest._SPAWNED_SERVICES)
    try:
        yield {"tmp": tmp_path, "lock": lock, "settings": settings, "wav": str(wav),
               "env": env}
    finally:
        model_service._settings_path = original
        model_service.unload_models()
        # Затвор чтения весов — состояние подменного модуля, а он живёт в процессе:
        # тест мог не дойти до снятия (падение утверждения), и тогда следующий тест
        # повис бы на чтении весов до срока затвора.
        subst = model_service._subst_modules.get(env["REELSI_MODEL_SERVICE_SUBST"])
        if subst is not None:
            subst.set_gate(None)
        _kill_service_processes(conftest._SPAWNED_SERVICES[spawned_before:])
        # Свой поток приёма — тоже за собой: `_join` в тесте ждёт выхода, но тест
        # мог не дойти до него (падение утверждения), и тогда живой сервис
        # принимал бы запросы следующего теста.
        _stop_registered_services()
        with contextlib.suppress(FileNotFoundError):
            settings.unlink()       # файл адреса погашенного сервиса не нужен
        os.environ.pop("REELSI_TEST_ASR_WAIT", None)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# Сервисы, поднятые ЭТИМ тестом (`_server`): их поток приёма фиксатор профиля
# обязан дождаться. Поток живёт дольше `stop()`: тот только просит выйти, а поток
# завершается сам (проверка флага между приёмами). Пока он жив, сервис прошлого
# теста ещё принимает соединения, и запрос следующего теста уходит к нему, а не к
# своему: тогда ответ приходит, а счётчики ПОДМЕНЫ этого теста не тронуты
# («прочитано 0 раз вместо одного»).
_LIVE_SERVICES: list[Any] = []
_TEST_KEY = b"test-key"          # ключ сервиса ЭТОГО теста (свой на каждый тест)


@pytest.fixture(autouse=True)
def _track_started_services() -> Any:
    """Своя учётная запись сервисов на КАЖДЫЙ тест: свой ключ и ожидание выхода.

    Фиксатор нарочно не просит `ws`: профиль подменяет `_settings_path` (и это
    касается только тех тестов, которые его получили). Фиксатор, зависящий от
    `ws`, заводил бы профиль и тестам без него — и ломал им боевой путь адреса
    (проверка «явный путь сильнее лока» в тесте каталога состояния).

    Своё состояние на тест — то же, что у изолированного профиля интерфейса:
    свой файл адреса (его даёт `ws`) и свой ключ. Ключ сам по себе заражение НЕ
    закрывает — клиент читает ключ из того же файла адреса, — но убирает общий
    изменяемый ключ из прогона: два теста с одним `b"test-key"` неразличимы, и
    «а чей это сервис?» потом не разобрать. Закрывает заражение ожидание выхода:
    пока поток приёма жив, сервис ещё принимает соединения, и запрос соседа
    уходит к нему.
    """
    global _TEST_KEY
    _TEST_KEY = ("test-key-%d-%d" % (os.getpid(), next(_KEY_COUNTER))).encode("ascii")
    _LIVE_SERVICES.clear()
    yield
    _stop_registered_services()


def _kill_service_processes(spawned: list[Any]) -> None:
    """Погасить процессы сервиса, поднятые ЭТИМ тестом, — по PID своих запусков.

    Процесс сервиса тест заводит настоящий, и он обязан уйти сам: по простою или по
    `shutdown`. Но тест нарезки, которому сервис отвечает «слов нет», уходит на
    запасной путь СРАЗУ, а процесс живёт ещё пять минут простоя: за прогон таких
    тестов набирались десятки живых процессов с фейковыми весами, и архитектор
    гасил их руками. Поэтому фиксатор профиля доводит дело до конца — и ТОЛЬКО по
    своим запускам: гасим те `Popen`, что тест завёл сам (их помнит реестр
    `conftest`), а искать «все python с `model_service`» по имени нельзя — рядом
    работают сервисы других копий интерфейса и настоящие нарезки владельца.

    Гасим через `Popen.kill()`, а не `os.kill(pid, 9)`: на Windows второй отвечает
    «PermissionError: Access is denied», и процесс остаётся жив.
    """
    for proc in spawned:
        if not model_service._pid_alive(int(getattr(proc, "pid", 0) or 0)):
            continue                 # вышел сам по простою — гасить нечего
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10.0)  # дождаться выхода: иначе процесс живёт ещё миг


def _server(settings: Path, slots: int = 1, idle: float | None = None,
            authkey: bytes | None = None, pid: int | None = None,
            created: float | None = None) -> Any:
    """Слушатель в СВОЁМ потоке и файл адреса к нему — как у боевого сервиса.

    Файл адреса — ТОЙ ЖЕ формы, что пишет `main` боевого пути, включая версию кода,
    устройство и метку старта: без них адрес выглядит как «сервис другой версии»
    (или как брошенный), и клиент честно погасил бы поднятый здесь сервис и завёл
    новый (см. `_settings_mismatch` и `_pid_is_service`). Поэтому `build` берётся у
    самого модуля, а не выдумывается.

    `pid`/`created` по умолчанию — ЭТОТ процесс: так адрес и получается «брошенным»
    (номер принадлежит не сервису, а pytest), и это ровно тот случай, из-за которого
    гашение по PID убивало сам pytest. Настоящий процесс сервиса пишет свои значения
    сам (см. `_spawn_service_process`).
    """
    port = model_service._pick_port()
    key = authkey if authkey is not None else _TEST_KEY
    service = model_service._Service(port, key, idle_s=idle, slots=slots)
    model_service._write_settings(str(settings), {
        "version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
        "port": port, "pid": os.getpid() if pid is None else pid,
        "created": (model_service._process_create_time(os.getpid())
                    if created is None else created),
        "authkey": key.hex(),
        "idle": service.idle_s,
        "build": model_service._code_fingerprint(),
        "device": model_service._service_device(),
    })
    service.start_thread()
    _LIVE_SERVICES.append(service)          # за ним следит фиксатор этого теста
    return service


def _join(service: Any) -> None:
    """Погасить слушатель и дождаться выхода потока (свои потоки не переживают тест)."""
    service.stop()
    service.wait_until_stopped(timeout=10)
    _stop_registered_services()


def _stop_registered_services(timeout: float = 10.0) -> None:
    """Остановить сервисы ЭТОГО теста и дождаться РЕАЛЬНОГО выхода их потоков.

    `stop()` снимает приём и закрывает слушатель, но цикл приёма выходит не сразу:
    поток завершается сам, между приёмами увидев флаг. Дожидаемся его здесь нарочно:
    тест, начавшийся в этом окне, получил бы свой адрес, а запрос ушёл бы к
    сервису соседа (номер порта на Linux отдаётся снова). Ожидание идемпотентно:
    погашенный сервис выходит сразу.
    """
    for service in list(_LIVE_SERVICES):
        with contextlib.suppress(Exception):
            service.stop()
            service.wait_until_stopped(timeout=timeout)


def _transcriptions(wav: str, count: int) -> list[Any]:
    """Слова от сервиса: `count` запросов идут ОДНОВРЕМЕННО."""
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(model_service.transcribe, wav, "v3_ctc")
                   for _ in range(count)]
    return [f.result(timeout=60) for f in futures]


def _warm_up(wav: str) -> Any:
    """Прогреть сервис: первый запрос грузит модель и подменный модуль.

    Модуль подмены кеширует сам сервис (`_subst_modules`) — до первого запроса
    его счётчики мерить нечего.
    """
    assert model_service.transcribe(wav, "v3_ctc"), "сервис не посчитал"
    return _fake_state()


def _fake_state() -> Any:
    """Состояние подменного модуля (он загружается первым же распознаванием).

    Ищем его в кеше сервиса (`_subst_modules`), а не в `sys.modules`: `exec_module`
    туда модуль не кладёт, и поиск по `sys.modules` нашёл бы его только случайно.
    """
    path = os.environ.get("REELSI_MODEL_SERVICE_SUBST") or ""
    module = model_service._subst_modules.get(path)
    if module is None:
        raise AssertionError("подменный модуль GigaAM ещё не загружен")
    return module


def _reset_fake(together: int = 1) -> None:
    """Обнулить счётчики подмены: состояние живёт в процессе между тестами.

    `together` — сколько распознаваний обязано считаться разом (1 — не ждать
    соседа): счётчики и настройки живут в процессе, и без сброса следующий тест
    унаследовал бы ожидание из теста потолка слотов.
    """
    state = _fake_state()
    state.LOADED = 0
    state.max_concurrent = 0
    state._in_flight = 0
    state.configure(wait=0.05, together=together)


def _clear_settings(ws: dict[str, Any]) -> None:
    """Убрать файл адреса: «сервиса нет» проверяется на пустом месте."""
    try:
        ws["settings"].unlink()
    except FileNotFoundError:
        pass


# Таймауты для тестов, поднимающих НАСТОЯЩИЙ процесс сервиса. Сроки короткие
# нарочно: процесс без сторожа простоя не выйдет никогда, и тест обязан сказать
# это утверждением, а не повиснуть на своём же ожидании.
PROC_START_TIMEOUT_S = 15.0     # холодный старт сервиса: импорт ядра и torch
PROC_EXIT_TIMEOUT_S = 8.0       # выход после idle=1 — секунды, не минуты
PROC_IDLE_S = 1.0               # простой тестового процесса
# Срок ответа на `ping` для тестов, где клиент говорит с НАСТОЯЩИМ слушателем под
# нагрузкой. `call` перед каждым запросом шлёт `ping` с сроком `PING_TIMEOUT_S` (3 с).
# На занятой машине (полный прогон `-n auto`) ответ мог не успеть: клиент решал,
# что сервиса нет, и заводил новый процесс — счёт шёл уже в нём, а счётчики
# подмены этого теста не менялись. Срок растянут, утверждения не трогаем: мёртвый
# порт по-прежнему отказывает сразу (соединение не открывается).
CLIENT_PING_PATIENCE_S = 30.0


def _broken_subst(ws: dict[str, Any]) -> None:
    """Переключить подмену на модуль, который падает на распознавании."""
    os.environ["REELSI_MODEL_SERVICE_SUBST"] = _write(ws["tmp"] / "broken_gigaam.py",
                                                      _BROKEN_MODULE)


def _spawn_service_process(ws: dict[str, Any], *args: str) -> subprocess.Popen[str]:
    """Поднять НАСТОЯЩИЙ процесс `python -m core.model_service --serve` — как бой.

    Стенд `_server` поднимает слушателя потоком, и цикл приёма в нём настоящий, а
    вот ТОЧКА ВХОДА процесса (`main`) в тех тестах не выполняется вовсе. Между тем
    именно она решает, будет ли у сервиса сторож простоя: без него боевой процесс
    живёт до перезагрузки. Поэтому здесь запускается настоящий процесс, с тем же
    окружением, что у теста (подменный `gigaam` и `cpu`), и со своим файлом адреса.
    """
    env = dict(os.environ)
    env["REELSI_MODEL_SERVICE_STATE"] = str(ws["settings"])
    return subprocess.Popen(
        [sys.executable, "-m", "core.model_service", "--serve",
         "--idle", str(PROC_IDLE_S), *args],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait_service_exit(proc: subprocess.Popen[str]) -> int:
    """Дождаться выхода процесса; не вышел — погасить по PID и сказать это тестом.

    Срок короткий нарочно: процесс без сторожа простоя не выйдет никогда, и тест
    обязан сказать это утверждением, а не повиснуть на своём же ожидании.
    """
    try:
        return int(proc.wait(timeout=PROC_EXIT_TIMEOUT_S))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10.0)
        raise AssertionError(
            "процесс сервиса с простым %.0fс не вышел за %.0fс — выгрузки по простою "
            "у боевого пути нет" % (PROC_IDLE_S, PROC_EXIT_TIMEOUT_S))


def _wait_live_settings(ws: dict[str, Any], timeout: float = PROC_START_TIMEOUT_S
                        ) -> dict[str, Any]:
    """Дождаться адреса НАСТОЯЩЕГО процесса сервиса (тот же ждать, что `ensure_started`)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = model_service.read_settings(str(ws["settings"]))
        if data and model_service._settings_alive(data):
            return dict(data)
        time.sleep(0.1)
    raise AssertionError("процесс сервиса не записал живой адрес за %.0fс" % timeout)


def _drop_service_process(proc: subprocess.Popen[str]) -> None:
    """Убрать за процессом, которому простой назначен ДОЛГИМ (`--idle 300`).

    Сервис обязан выходить сам, но ждать пять минут в тесте нельзя: просим его
    `shutdown` (свой адрес он снимет сам) и, если не вышел, гасим по PID — ровно
    своим `Popen`, а не поиском по имени.
    """
    model_service.shutdown()
    try:
        proc.wait(timeout=PROC_EXIT_TIMEOUT_S)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    proc.wait(timeout=10.0)


def _occupied_port() -> Any:
    """Занять порт на 127.0.0.1, чтобы `Listener` на нём упал (проверка отказа).

    Занятый порт отдаём `_pick_port`, а закрываем уже после утверждений: тест
    держит его собой, а не гадает, свободен ли он на этой машине.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Только владелец — и опция эта есть ТОЛЬКО на Windows: без неё соседний
    # `Listener` на том же адресе присаживается рядом с нашим сокетом, и «занятый
    # порт» оказывается свободным. На POSIX такого имени у модуля `socket` нет
    # (`AttributeError`, падал весь тест), да и нужды в нём нет: там `Listener` сам
    # ставит `SO_REUSEADDR`, а он не даёт занять адрес, который уже слушают.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


# --------------------------------------------------------------------------- #
# 1. Модель грузится один раз, слоты задают параллельность
# --------------------------------------------------------------------------- #
def test_две_параллельные_транскрипции_грузят_модель_один_раз(ws: dict[str, Any]) -> None:
    """Два клиента разом: `load_model` вызвана ОДИН раз, оба получили слова.

    Мутация: убрать кэш моделей (`_loaded_model`) — загрузок станет две, и тест
    покраснеет. Ровно это и было проблемой: каждый ролик читал веса заново.
    """
    service = _server(ws["settings"], slots=2)
    try:
        _warm_up(ws["wav"])               # первый запрос — он же загрузка модели
        _reset_fake()
        got = _transcriptions(ws["wav"], 2)
    finally:
        _join(service)

    assert all(words and words[0]["w"] == "v3_ctc_word" for words in got), f"ответы: {got}"
    state = _fake_state()
    assert state.LOADED == 0, (
        f"две параллельные просьбы прочитали веса {state.LOADED} раз — кэш не работает"
    )


@pytest.mark.xdist_group("model_service")
@pytest.mark.parametrize("slots,expected", [(1, 1), (2, 2)])
def test_слоты_держат_потолок_одновременных(
    ws: dict[str, Any], slots: int, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`slots` одновременных распознаваний: N=1 — по одному, N=2 — разом.

    Модель-пустышка считает не мгновенно (`configure(wait=2.0)`), а счётчик
    `max_concurrent` показывает, сколько моделей считали ОДНОВРЕМЕННО. При одном
    слоте второй запрос семафор не пускает — потолок 1; при двух оба заходят
    внутрь и уплотняются — потолок 2. Мутация: снять семафор — при `slots=1`
    счётчик покажет 2, и тест покраснеет.
    """
    monkeypatch.setattr(model_service, "PING_TIMEOUT_S", CLIENT_PING_PATIENCE_S)
    service = _server(ws["settings"], slots=slots)
    try:
        _warm_up(ws["wav"])
        # Ждём соседа по счёту (`together=expected`) и держим паузу: только тогда
        # `max_concurrent` меряет семафор, а не расписание потоков. Сломанный
        # семафор не сведёт счётчики — ожидание кончится по сроку, и тест краснеет.
        _reset_fake(together=expected)
        # Пауза счёта — 2 с: второй клиент приходит с разницей в сотни миллисекунд
        # (два пинга и два подключения, под нагрузкой — дольше). Короткая пауза
        # (0.3 с) делала мутацию «снят семафор» при одном слоте невидимой: второй
        # запрос приходил уже после окончания первого, и счётчик показывал 1.
        _fake_state().configure(wait=2.0, together=expected)
        got = _transcriptions(ws["wav"], 2)
    finally:
        _join(service)

    assert all(words for words in got), f"ответы: {got}"
    assert _fake_state().max_concurrent == expected, (
        f"при {slots} слотах разом считали {_fake_state().max_concurrent} — "
        f"потолок не держится"
    )


def test_конечная_неудача_распознавания_не_подвешивает_клиента(ws: dict[str, Any]) -> None:
    """Ошибка внутри сервиса — быстрый `None` у клиента, процесс продолжает жить.

    Подмена падает на загрузке весов: ошибка должна дойти до клиента ответом, а
    не остаться в недрах сервиса. Мутация: уронить слушатель вместе с
    обработчиком — следующий запрос повис бы.
    """
    _broken_subst(ws)
    service = _server(ws["settings"], slots=1)
    try:
        assert model_service.transcribe(ws["wav"], "v3_ctc") is None
        assert model_service.service_alive(), "сервис умер вместе с обработчиком"
    finally:
        _join(service)


def test_чужой_authkey_не_пускают(ws: dict[str, Any]) -> None:
    """Соединение с другим ключом отвергается, а со своим — проходит.

    Файл адреса хранит ключ и права только владельцу: подключиться может лишь
    тот, кто прочитал файл. Мутация: убрать `authkey` у слушателя — тест
    покраснеет (чужой пройдёт).
    """
    from multiprocessing import connection

    service = _server(ws["settings"], slots=1, authkey=b"right-key")
    try:
        settings = model_service.read_settings(str(ws["settings"])) or {}
        assert settings["authkey"] == b"right-key".hex()
        with pytest.raises(Exception):
            with connection.Client(("127.0.0.1", int(settings["port"])),
                                   authkey=b"wrong-key") as conn:
                conn.send(("ping", None))
                conn.poll(3.0)
                conn.recv()
        assert model_service.service_alive(), "свой ключ перестал пускать"
    finally:
        _join(service)


# --------------------------------------------------------------------------- #
# 2. Второй старт: живой сервис — подключаемся, мёртвый файл — новый
# --------------------------------------------------------------------------- #
def test_второй_старт_подключается_к_живому(ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Живой сервис — нового процесса НЕ запускаем (второй ролик не платит за старт)."""
    _clear_settings(ws)
    service = _server(ws["settings"], slots=1)
    spawned: list[int] = []
    monkeypatch.setattr(model_service, "_spawn", lambda: spawned.append(1))
    try:
        assert model_service.ensure_started(timeout=5.0) is True
    finally:
        _join(service)

    assert not spawned, "сервис уже был жив, а мы запустили второй процесс"


def test_мёртвый_файл_адреса_даёт_новый_сервис(ws: dict[str, Any],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл есть, порт молчит → сервис считается мёртвым и заводится новый.

    Мутация: убрать `ping` из проверки живости (оставить только `pid`) — мёртвый
    файл с живым pid сочли бы живым и `_spawn` не позвали.
    """
    dead = {"version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
            "port": model_service._pick_port(), "pid": os.getpid(),
            "authkey": "00" * 32, "idle": 300}
    assert model_service._settings_alive(dead) is False, "мёртвый порт сочли живым"
    ws["settings"].write_text(json.dumps(dead), encoding="utf-8")

    server: dict[str, Any] = {}

    def spawn() -> None:
        # Сервис поднимаем в тесте (процесс запускать незачем: нас интересует
        # решение `ensure_started`, а не `Popen`), но с ЧУЖИМ pid — иначе проверка
        # «мёртвый оказался живым» ничего не докажет.
        model_service._write_settings(str(ws["settings"]), {
            "version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
            "port": model_service._pick_port(), "pid": 1,
            "authkey": (b"new-key").hex(), "idle": 300,
        })
        server["service"] = _server(ws["settings"], slots=1, authkey=b"new-key")

    monkeypatch.setattr(model_service, "_spawn", spawn)
    try:
        assert model_service.ensure_started(timeout=10.0) is True
        fresh = model_service.read_settings(str(ws["settings"])) or {}
        # Критерий «файл переписан ЖИВЫМ сервисом» — не номер порта: на Linux
        # освободившийся порт сразу выдаётся снова, и новый сервис законно
        # получает ТОТ ЖЕ номер (проверка `port != dead["port"]` краснела на
        # ровном месте). Сравниваем то, что у нового сервиса гарантированно
        # своё, — ключ соединения: его заводит сам сервис, и в мёртвой записи он
        # другой. Плюс живой сервис обязан отвечать по этому адресу.
        assert fresh["authkey"] != dead["authkey"], (
            "мёртвый файл адреса не переписан: в нём остался ключ прежней записи")
        assert model_service.service_alive() is True
        live = model_service.read_settings(str(ws["settings"])) or {}
        assert live.get("port") and live.get("authkey") == fresh["authkey"], live
    finally:
        _join(server["service"])


def test_каталог_состояния_один_у_клиента_и_сервиса(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Адрес, ключ и заявка сервиса лежат ровно там, где их ищет клиент.

    Путь состояния читался только из `JOB_LOCK_PATH` — константы, снятой один раз
    при импорте `core.jobstate`: сервис, поднятый с другим окружением (свой порядок
    импортов, чужая `REELSI_JOB_LOCK`), писал адрес в другом каталоге, клиент ждал
    его впустую, сдавался по таймауту старта — а поднятый процесс жил до простоя и
    держал видеопамять. Теперь каталог состояния называет ОДНА функция
    (`state_path_for`), и тот, кто заводит сервис, передаёт готовый путь ребёнку
    переменной окружения.

    Мутации: убрать чтение `REELSI_MODEL_SERVICE_STATE` из `state_path` — краснеет
    проверка «явный путь сильнее лока»; не передать переменную в `_spawn` —
    последняя проверка.
    """
    lock = str(tmp_path / "bench.gpu")
    monkeypatch.setattr(model_service, "JOB_LOCK_PATH", lock)
    monkeypatch.delenv("REELSI_MODEL_SERVICE_STATE", raising=False)

    # Адрес, ключ и заявка на старт — в ОДНОМ файле рядом с локом.
    assert model_service._settings_path() == model_service.state_path_for(lock)
    assert model_service._settings_path() == lock + ".modelsvc.json"
    assert model_service._start_mark_path(model_service._settings_path()) == (
        lock + ".modelsvc.json.start")

    # Явно названный каталог состояния сильнее унаследованного лока.
    own = str(tmp_path / "own.modelsvc.json")
    monkeypatch.setenv("REELSI_MODEL_SERVICE_STATE", own)
    assert model_service._settings_path() == own, "явный путь состояния не учтён"

    seen: dict[str, Any] = {}

    class _FakePopen:
        """Заглушка процесса: ловим команду и окружение, ничего не запуская."""

        def __init__(self, cmd: list[str], **kw: Any) -> None:
            seen["cmd"] = cmd
            seen.update(kw)

    monkeypatch.setattr(model_service.subprocess, "Popen", _FakePopen)
    assert isinstance(model_service._spawn(), _FakePopen)
    assert seen["cmd"][-1] == "--serve", seen["cmd"]
    env = seen["env"]
    assert env["REELSI_MODEL_SERVICE_STATE"] == own, (
        "сервис завели бы в другом каталоге состояния: %r"
        % (env.get("REELSI_MODEL_SERVICE_STATE"),)
    )


def test_адрес_без_живого_порта_сервисом_не_считается(ws: dict[str, Any]) -> None:
    """Файл адреса, по которому никто не отвечает, — ещё не «сервис поднялся».

    Слушатель открывается после конструктора `_Service`, а в конструкторе читаются
    веса устройства (импорт torch — секунды). Прежний `_wait_settings` отдавал такую
    запись сразу: клиент получал отказ соединения, объявлял «сервис моделей не
    поднялся» и уходил на локальный путь, а запущенный им процесс жил до простоя.

    Мутация: вернуть `if data: return data` без `_settings_alive` — тест краснеет.
    """
    dead = {"version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
            "port": model_service._pick_port(), "pid": os.getpid(),
            "authkey": "00" * 32, "idle": 300}
    ws["settings"].write_text(json.dumps(dead), encoding="utf-8")

    assert model_service._wait_settings(str(ws["settings"]), None, 0.5) is None, (
        "адрес, по которому никто не слушает, приняли за живой сервис"
    )

    service = _server(ws["settings"], slots=1)
    try:
        live = model_service._wait_settings(str(ws["settings"]), None, 5.0)
        assert live is not None, "живой сервис не дождались"
        assert int(live.get("port") or 0) != int(dead["port"]), live
    finally:
        _join(service)


# --------------------------------------------------------------------------- #
# 3. Простой и явные команды жизненного цикла
# --------------------------------------------------------------------------- #
def test_простой_завершает_слушатель(ws: dict[str, Any]) -> None:
    """Простой больше `idle` — цикл приёма выходит (процесс отдаст VRAM и умрёт)."""
    service = _server(ws["settings"], slots=1, idle=0.4)
    assert service.wait_until_stopped(timeout=10.0) is True, (
        "слушатель не завершился по простою"
    )


@contextlib.contextmanager
def _listener_without_close_wakeup(service: Any, allow_wake: bool = True) -> Any:
    """Слушатель, закрытие которого НЕ прерывает приём, — как `accept()` на Linux.

    Замена для `_listener_socket`: приём в службе остаётся НАСТОЯЩИМ (поток, цикл,
    обработчик, будильник), а слушатель ведёт себя так, как ведёт себя `accept` на
    Linux.

    ЗАЧЕМ. Раньше выход службы держался на том, что `listener.close()` прерывает
    блокирующий `accept()` из другого потока. На Windows так и есть, на Linux — НЕТ:
    замер в CI-образе показал, что там поток приёма остаётся в `accept` навсегда, и
    служба не выходит ни по простою, ни по `shutdown` (держит веса и видеопамять, а
    сторож тестовой сессии валит последний тест воркера). Дефект только на Linux,
    поэтому проверяем не ОС, а ТРЕБОВАНИЕ к коду: выход не зависит от того,
    прерывает ли ОС `accept` закрытием.

    У того же поведения есть ВТОРАЯ сторона, и она боевая: пока поток сидит в
    `accept`, закрытый дескриптор на Linux продолжает принимать подключения — то
    есть после `stop()` остановленный сервис ещё отвечает клиенту. Прекратить это
    может только `shutdown` слушающего сокета, и стенд стережёт и его (`shutdown`
    ниже).

    ЧТО ДЕЛАЕТ ЗАМЕНА:
      1. срок ожидания (`settimeout`) запоминается и отдаётся обратно — тест видит,
         что служба его ПОСТАВИЛА, а не полагается на поведение `accept`. Без срока
         `accept` ждёт вечно: ровно так ведёт себя настоящий сокет Linux, которого
         закрытие не будит;
      2. первый `accept` отдаёт пустышку-соединение, у которого нечего читать
         (`recv` — конец файла): служба получает подключение и СРАЗУ его закрывает.
         Рукопожатия на пустышке нет — в этих тестах оно не проверяется (его
         стережёт тест чужого `authkey`);
      3. дальше `accept` отдаёт соединение будильника (`wake` на него приходит и его
         принимают) либо, если будильник снят, ждёт до СВОЕГО срока и не возвращает
         ничего;
      4. `close` слушателя НЕ будит приём и НЕ снимает его — как закрытие
         дескриптора на Linux, пока поток сидит в `accept`. Подключение, положенное
         в `state["late"]` (клиент, пришедший уже после закрытия), блокирующийся
         `accept` отдаёт службе и НАСТОЯЩЕМУ обработчику — ровно так ядро Linux
         отдаёт соединения, пришедшие после `close`;
      5. `shutdown` на сокете слушателя снимает приём: `accept` возвращается отказом
         (`OSError`), а `late` больше не отдаётся. Его отсутствие — и есть дефект
         «остановленный сервис ещё отвечает».

    `allow_wake=False` — будильник до приёма не доходит: нужен там, где проверяется
    именно ОПРОС, и подключение сервиса приняли бы за «выход по флагу».

    Отдаёт `(fake, state)`: `fake` идёт в службу, `state["stub"]` — пустышка первого
    подключения (по ней тест видит, что приём работает), `state["late"]` — место для
    клиента, пришедшего после закрытия.
    """
    import socket
    from unittest import mock

    state: dict[str, Any] = {"n": 0, "timeout": None, "stub": None, "wake": None,
                             "closed": False, "shutdown": None, "late": None}

    class _StubConn:
        """Соединение-пустышка: читать нечего, писать некуда, закрывается молча."""

        def __init__(self) -> None:
            self.closed = False

        def recv(self) -> Any:
            raise EOFError("пустышка: данных нет")

        def send(self, _obj: Any) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class _FakeSocket:
        """Сокет слушателя: срок спрашивают (`getsockname`), ставят и запоминают."""

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", int(service.port))

        def settimeout(self, value: float | None) -> None:
            state["timeout"] = value

        def shutdown(self, how: int) -> None:
            """`shutdown` слушающего сокета: приём снят (на Linux он и будит `accept`)."""
            state["shutdown"] = how

    class _WaitListener:
        """Внутренность `Listener`: один сокет — срок опроса и есть предмет проверки."""

        def __init__(self) -> None:
            self._socket = _FakeSocket()

    def _late_conn() -> Any:
        """Подключение, пришедшее ПОСЛЕ закрытия, — только если приём ещё не снят.

        Ядро Linux отдаёт такие соединения блокирующемуся `accept` (дескриптор-то
        закрыт другим потоком, а принимает он). `shutdown` это прекращает — потому
        здесь и стоит проверка: так стенд и ловит дефект.
        """
        if state["late"] is not None and state["closed"] and state["shutdown"] is None:
            conn, state["late"] = state["late"], None
            return conn
        return None

    def _block_without_result() -> Any:
        """Ждать, как настоящий `accept` Linux: вернуться можно по сроку ИЛИ по беде.

        Возвращает раньше срока в двух случаях, и оба — поведение ядра: пришло
        подключение (его отдаём) и снят приём (`shutdown` — отказом).
        """
        if state["timeout"] is None:
            raise AssertionError(
                "приём ждёт вечно: срок ожидания (POLL_ACCEPT_S) не поставлен — на "
                "Linux такой сервис не вышел бы ни по простою, ни по shutdown")
        deadline = time.monotonic() + float(state["timeout"])
        while True:
            if state["shutdown"] is not None:
                raise OSError("приём снят: shutdown слушающего сокета")
            conn = _late_conn()
            if conn is not None:
                return conn
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def accept() -> Any:
        state["n"] += 1
        if state["shutdown"] is not None:
            raise OSError("приём снят: shutdown слушающего сокета")
        if state["n"] == 1:
            return state["stub"]
        conn = _late_conn()
        if conn is not None:
            return conn
        if allow_wake and state["wake"] is not None:
            conn, state["wake"] = state["wake"], None
            return conn
        return _block_without_result()

    def close() -> None:
        """Закрытие слушателя: приём НЕ будит и НЕ снимает — как на Linux."""
        state["closed"] = True

    fake = mock.MagicMock()
    fake.__enter__ = mock.Mock(return_value=fake)
    fake.__exit__ = mock.Mock(return_value=False)
    fake.close = mock.Mock(side_effect=close)
    fake._listener = _WaitListener()
    fake.accept = mock.Mock(side_effect=accept)
    state["stub"] = _StubConn()
    wake_client, wake_server = socket.socketpair()
    state["wake"] = wake_server
    fake.stub = state["stub"]
    try:
        yield (fake, state)
    finally:
        for sock in (wake_client, wake_server, state["wake"], state["late"]):
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close()


class _LateClient:
    """Клиент, подключившийся в окне остановки: по нему видно, обслужили его или нет.

    Отвечает на любой запрос и запоминает ОТВЕТ сервиса (`served`): обработчик
    службы зовёт `recv`, а потом `send`, поэтому обслуженное подключение отличимо
    от оборванного. Рукопожатия нет — его в этих тестах заменяет сам стенд.
    """

    def __init__(self) -> None:
        self.served = False
        self.closed = False

    def recv(self) -> Any:
        return ("ping", None)

    def send(self, _obj: Any) -> None:
        self.served = True

    def close(self) -> None:
        self.closed = True


def _wait_for(condition: Any, timeout: float = 5.0) -> bool:
    """Дождаться условия (для проверок, которые ждут счётчик, а не событие)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def test_выход_по_простою_не_зависит_от_закрытия_слушателя(ws: dict[str, Any],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Простой завершает службу и там, где закрытие слушателя НЕ прерывает приём.

    Так ведёт себя Linux: `accept` продолжает ждать, `close` его не будит. Если бы
    выход по-прежнему держался на закрытии, поток приёма остался бы жив навсегда —
    ровно дефект Linux-прогона. Здесь проверяется, что он выходит: решение о выходе
    принимает флаг остановки, а приём ждёт соединения коротким сроком и читает этот
    флаг в цикле.

    Ждём выхода ПОСЛЕ подключения-пустышки и после срока опроса: сначала приём
    обязан обслужить соединение (значит, на таймауте он не «выходит молча»), и
    только потом простой закрывает службу.

    Мутации: вернуть приём к одному лишь `listener.accept()` без таймаута — поток
    остаётся жив навсегда, тест краснеет по своему сроку; вернуть выход на любом
    `OSError` без проверки флага — приём умирает через `POLL_ACCEPT_S`, и краснеет
    проверка «поток приёма жив до простоя».
    """
    service = model_service._Service(model_service._pick_port(), b"test-key",
                                     idle_s=1.0, slots=1)
    with _listener_without_close_wakeup(service) as (fake, state):
        monkeypatch.setattr(service, "_listener_socket", lambda: fake)
        accept = service.start_thread()
        assert _wait_for(lambda: fake.stub.closed), "приём не обслужил первое соединение"
        # Срок опроса ставит САМА служба: без него приём ждёт вечно (так и ведёт
        # себя сокет Linux, которого `close` не будит) и не выйдет ни по простою,
        # ни по `shutdown`. Мутация «убрать `settimeout`» краснеет ровно здесь.
        assert _wait_for(lambda: state["timeout"] == model_service.POLL_ACCEPT_S), (
            "приём не опрашивается: срок ожидания равен %r, а должен быть %.1fс — "
            "на Linux такой сервис не вышел бы ни по простою, ни по shutdown"
            % (state["timeout"], model_service.POLL_ACCEPT_S)
        )
        # Ждём БОЛЬШЕ срока опроса: таймаут обязан оставить цикл приёма живым, а не
        # стать выходом «сам собой» (мутация «выходить на любом OSError»).
        time.sleep(model_service.POLL_ACCEPT_S + 0.15)
        assert accept.is_alive(), (
            "приём вышел по таймауту опроса, а не по остановке: сервис перестал бы "
            "принимать соединения через %.1fс после старта" % model_service.POLL_ACCEPT_S
        )
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "слушатель не завершился по простою: выход зависит от того, прерывает "
            "ли ОС accept закрытием слушателя"
        )
    assert not accept.is_alive(), "поток приёма остался жить"


def test_stop_гасит_службу_и_там_где_закрытие_не_будит(ws: dict[str, Any],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """`stop()` выходит и на слушателе, который не просыпается от закрытия.

    Второй ход к тому же требованию: `shutdown` шлёт просьбу, обработчик зовёт
    `stop`, и приём обязан выйти — на Linux закрытие слушателя его не будит.
    Выход решает флаг (`_stopped`), а немедленным его делает будильник (`wake`) и
    снятие приёма (`_shutdown_listener`): без флага приём остался бы в `accept` до
    бесконечности, без них обоих выход занял бы весь срок опроса.
    """
    service = model_service._Service(model_service._pick_port(), b"test-key",
                                     idle_s=300.0, slots=1)
    with _listener_without_close_wakeup(service) as (fake, state):
        monkeypatch.setattr(service, "_listener_socket", lambda: fake)
        service.start_thread()
        assert _wait_for(lambda: fake.stub.closed), "приём не обслужил первое соединение"
        service.stop()
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "явный `stop` не разбудил приём: выход остался за закрытием слушателя"
        )
        # Число вызовов accept не проверяем: если цикл увидел флаг до следующего
        # accept, он честно выходит после первого — это гонка теста, не дефект.
        assert state["timeout"] == model_service.POLL_ACCEPT_S, (
            "служба не поставила срок опроса на приём: он равен %r" % (state["timeout"],)
        )


def test_остановка_снимает_приём_до_закрытия_слушателя(
        ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Стенд Linux: клиент, пришедший в окне остановки, службой НЕ обслуживается.

    Вторая сторона того же поведения, и она боевая. На Linux `close()` слушателя из
    другого потока не снимает сокет, пока поток приёма сидит в `accept()`: ядро
    продолжает ПРИНИМАТЬ подключения и отдаёт их этому самому `accept` — то есть
    после `stop()` уже остановленный сервис ещё отвечает клиенту, который в этот миг
    искал живой сервис. Прекращает это только `shutdown(SHUT_RDWR)` на слушающем
    сокете, и вызывать его надо ДО `close`.

    Стенд ведёт себя как Linux (`_listener_without_close_wakeup`): закрытие приём не
    будит, а клиент, положенный в `state["late"]`, отдаётся `accept` ПОСЛЕ закрытия и
    до `shutdown` — и уходит НАСТОЯЩЕМУ обработчику службы. Проверяем не факт
    вызова, а инвариант: обслужен ли этот клиент. Клиента кладём ДО `stop()`, но
    только убедившись, что приём УЖЕ ждёт второго подключения (`state["n"] >= 2`):
    иначе цикл успел бы выйти по флагу, не заглянув в `accept` вовсе, и тест зеленел
    бы, ничего не проверив.

    Будильник снят нарочно (`wake` — пустышка, `allow_wake=False`): здесь проверяется
    не он, а снятие приёма. С будильником замер не вышел бы: своё подключение к
    молчащему порту стенда отдаёт `0.2с` ожидания — ровно срок опроса, и цикл успел
    бы выйти по флагу прежде, чем закрытие слушателя стало бы видно `accept`.

    Мутация «убрать `shutdown`» (снятие приёма) краснеет здесь: `late`-клиент
    уходит обработчику и получает ответ. На Windows тест всё равно ловит мутацию —
    стенд воспроизводит Linux, а не платформу прогона.
    """
    service = model_service._Service(model_service._pick_port(), _TEST_KEY,
                                     idle_s=300.0, slots=1)
    with _listener_without_close_wakeup(service, allow_wake=False) as (fake, state):
        monkeypatch.setattr(service, "_listener_socket", lambda: fake)
        monkeypatch.setattr(service, "wake", lambda: None)   # будильник снят нарочно
        accept = service.start_thread()
        assert _wait_for(lambda: fake.stub.closed), "приём не обслужил первое соединение"
        assert _wait_for(lambda: state["n"] >= 2), (
            "приём не встал на второе ожидание: подсунуть ему клиента нечего")
        late = _LateClient()
        state["late"] = late                # клиент приходит уже после `close()`
        service.stop()
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "поток приёма не завершился: остановка приёма не снята")
        assert not _wait_for(lambda: late.served, timeout=0.5), (
            "остановленный сервис обслужил новое подключение: приём не снят "
            "`shutdown` до `close` — на Linux такой сервис ещё отвечает"
        )
    assert not accept.is_alive(), "поток приёма остался жить"


def test_остановленный_сервис_не_обслуживает_новых_подключений(
        ws: dict[str, Any]) -> None:
    """После `stop()` и выхода приёма НАСТОЯЩИЙ порт новые подключения не принимает.

    Инвариант боевого кода на настоящем слушателе: адрес, порт и ключ — свои,
    подключение — настоящее (`_request`). После возврата `stop()` и
    `wait_until_stopped()` сервис обязан отказать или оборвать соединение, а не
    обслужить клиента. `wait_until_stopped` возвращает True ТОЛЬКО когда поток
    приёма действительно завершился, и это здесь проверяется по самому потоку: пока
    он жив, миг остановки не наступил и утверждать нечего.

    На Windows тест зелёный и без снятия приёма — там закрытие слушателя прерывает
    `accept` само. Linux-сторону того же требования стережёт стенд
    (`test_остановка_снимает_приём_до_закрытия_слушателя`), а здесь инвариант
    проверяется на живом сокете, доступном обеим ОС.
    """
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        assert model_service.transcribe(ws["wav"], "v3_ctc"), "сервис не посчитал"
        data = model_service.read_settings(str(ws["settings"]))
        assert data is not None, "файл адреса сервиса не прочитан"
        service.stop()
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "поток приёма не завершился: проверять «не обслуживает» не на чем")
        thread = service._accept_thread
        assert thread is not None and not thread.is_alive(), (
            "`wait_until_stopped` вернул True, а поток приёма жив")
        with pytest.raises(model_service.ServiceUnavailable):
            model_service._request("ping", data, timeout=1.0, attempts=1)
        assert model_service.service_alive() is False, "сервис отвечает после остановки"
    finally:
        _join(service)


def test_остановка_дожидается_рабочих_потоков_и_держит_их_вне_кэша(
        ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Инвариант остановки: живых рабочих потоков нет, и в общий кэш они не пишут.

    ЧТО ПРОВЕРЯЕТСЯ. `wait_until_stopped` возвращает True только когда вышли И
    приём, И все рабочие потоки сервиса — обработчики запросов, предзагрузка
    (`load`) и сторож простоя. Чтение весов длится секунды, и раньше обработчик
    никем не учитывался: сервис «останавливался», а его поток продолжал читать
    веса и клал готовую модель в общий на процесс кэш уже после `stop()`. Следующий
    сервис (в тестах — следующий тест) находил модель в кэше и СВОИ веса не читал:
    отсюда и плавающее «LOADED == 0 при непустых ответах».

    СТЕНД. Чтение весов в подмене повешено на `threading.Event` (`set_gate`):
    запрос встаёт ВНУТРИ `load_model`, и у сервиса появляется живой рабочий поток
    ровно в миг остановки. Пока событие не отпущено, `wait_until_stopped` обязан
    говорить False; после отпускания — True, а кэш `_models` остаётся пустым.

    Мутация: ждать только поток приёма (как было) — краснеет первое утверждение
    «остановка объявлена завершённой, пока рабочий поток ещё читает веса».
    """
    monkeypatch.setattr(model_service, "_models", {})
    service = _server(ws["settings"], slots=1, idle=300.0)
    # Модуль подмены нужен ДО запроса: затвор вешается на его `load_model`, а
    # первый же запрос в него и встанет (`_install_subst_from_env` — та же дверь,
    # которой сервис ставит подмену перед чтением весов).
    model_service._install_subst_from_env()
    state = _fake_state()
    gate = threading.Event()
    state.set_gate(gate)

    def ask() -> None:
        model_service.transcribe(ws["wav"], "v3_ctc")

    client = threading.Thread(target=ask)
    client.start()
    try:
        assert _wait_for(lambda: state.waiting == 1), (
            "запрос не дошёл до чтения весов — держать остановку нечем")
        service.stop()
        assert service.wait_until_stopped(timeout=2.0) is False, (
            "остановка объявлена завершённой, пока рабочий поток ещё читает веса")
        gate.set()
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "остановка не дождалась рабочего потока")
    finally:
        gate.set()
        state.set_gate(None)          # затвор — состояние подмены, общее на процесс
        service.stop()
        service.wait_until_stopped(timeout=10.0)
        client.join(timeout=10.0)

    assert model_service._models == {}, (
        "остановленный сервис опубликовал модель в общий кэш: %r"
        % (model_service._models,))


def test_приём_переживает_таймаут_опроса(ws: dict[str, Any]) -> None:
    """Служба жива после срока опроса и отвечает на следующий запрос.

    Слушатель НАСТОЯЩИЙ: проверяется, что `accept` с таймаутом не превращает
    молчание порта в остановку службы. Мутация: выйти на любом `OSError` без
    проверки флага — приём умирает через `POLL_ACCEPT_S`, и второй запрос уходит в
    пустоту (`service_alive` говорит «нет сервиса»), хотя останавливать его никто
    не просил. Ровно так и вело бы себя «лечение» закрытием слушателя на Linux.
    """
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        assert model_service.transcribe(ws["wav"], "v3_ctc"), "первый запрос не прошёл"
        time.sleep(model_service.POLL_ACCEPT_S * 2 + 0.2)      # порт молчит дольше опроса
        assert model_service.service_alive() is True, (
            "служба перестала принимать соединения после срока опроса приёма")
        assert model_service.transcribe(ws["wav"], "v3_ctc"), "второй запрос не прошёл"
    finally:
        _join(service)


def test_опроса_достаточно_для_выхода_без_будильника(ws: dict[str, Any],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Выход держится на ОПРОСЕ приёма, а не на будильнике `stop()`.

    Главный ход к дефекту Linux. `stop()` делает два дела: ставит флаг остановки и
    будит `accept` своим подключением. Работает и одно, и другое; но если выход
    держится ТОЛЬКО на будильнике, то первая же помеха ему (или платформа, где
    своё подключение до приёма не доходит) снова оставит сервис висеть в `accept`.
    Здесь будильник снят нарочно (`wake` — пустышка), слушатель закрывается, но его
    закрытие приём НЕ будит (`_listener_without_close_wakeup`, как Linux): выйти
    можно только одним — вернуться из `accept` по СВОЕМУ сроку и увидеть флаг.

    Мутация: убрать `settimeout` из `_sleep_accept` — приём ждёт вечно, и тест
    краснеет на сроке ожидания (а не зеленеет на чужой беде).
    """
    service = model_service._Service(model_service._pick_port(), b"test-key",
                                     idle_s=0.3, slots=1)
    with _listener_without_close_wakeup(service, allow_wake=False) as (fake, state):
        monkeypatch.setattr(service, "_listener_socket", lambda: fake)
        monkeypatch.setattr(service, "wake", lambda: None)   # будильник снят нарочно
        accept = service.start_thread()
        assert _wait_for(lambda: fake.stub.closed), "приём не обслужил первое соединение"
        assert service.wait_until_stopped(timeout=10.0) is True, (
            "простой не завершил приём: выход держится на закрытии слушателя и "
            "будильнике, а не на опросе со сроком"
        )
        assert state["timeout"] == model_service.POLL_ACCEPT_S, (
            "служба не поставила срок опроса на приём: он равен %r" % (state["timeout"],)
        )
    assert not accept.is_alive(), "поток приёма остался жить"


def test_простой_считается_от_последнего_запроса(ws: dict[str, Any]) -> None:
    """Простой отсчитывается от ПОСЛЕДНЕГО запроса, а не от старта сервиса.

    Счёт идёт, пока `idle` не истёк ПОСЛЕ последнего запроса: клиент, пришедший
    за секунду до срока, обязан получить ответ, а не оборванное соединение, а
    процесс — выйти ровно через `idle` после него, а не сразу.

    Ждём не «секунды по часам», а СЛЕДУЮЩЕГО опроса сторожа (`_monitor_idle`
    просыпается каждые 0.2 с) плюс секунду на сам выход: под полной нагрузкой
    (`-n auto`) поток сторожа просыпается не мгновенно, и жёсткий срок в `idle`
    краснел бы на ровном месте, ничего не доказывая.
    """
    service = _server(ws["settings"], slots=1, idle=0.5)
    try:
        assert model_service.transcribe(ws["wav"], "v3_ctc"), "запрос к сервису не прошёл"
        assert service.wait_until_stopped(timeout=0.2 + 1.0) is True, (
            "после запроса сервис не вышел по простою"
        )
    finally:
        _join(service)


def test_боевой_процесс_выходит_по_простою(ws: dict[str, Any]) -> None:
    """Процесс `--serve` без единого запроса выходит по простою (idle=1 — за ≤ 3 с).

    Точка входа (`main`) заводила только цикл приёма, а сторожа простоя — нет:
    клиенты уходили, а сервис жил до перезагрузки и держал видеопамять. Здесь
    проверяется именно БОЕВОЙ путь: процесс запускается как в нарезке
    (`python -m core.model_service --serve`), за запросами не приходит никто.
    """
    started = time.monotonic()
    proc = _spawn_service_process(ws, "--slots", "1")
    try:
        assert model_service._wait_settings(str(ws["settings"]), proc,
                                            PROC_START_TIMEOUT_S) is not None, (
            "боевой процесс не поднял слушателя"
        )
        code = _wait_service_exit(proc)
        lived = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10.0)
        model_service.clear_settings(str(ws["settings"]))
    assert code == 0, "боевой процесс завершился не штатно: код %s" % code
    assert lived < 3.0, (
        "процесс с простым %.0fс жил %.1fс — простой не отсчитывался"
        % (PROC_IDLE_S, lived)
    )
    assert not model_service.read_settings(str(ws["settings"])), (
        "файл адреса остался после выхода сервиса"
    )


def test_боевой_процесс_выходит_через_idle_после_запроса(ws: dict[str, Any]) -> None:
    """Процесс `--serve` обслужил запрос и вышел через `idle` ПОСЛЕ него.

    Сервис с одним роликом: запрос считается, после него отсчитывается простой, и
    процесс уходит. Ровно этого не делал боевой путь (сторожа не заводил никто),
    и забытый сервис держал видеопамять, которую просят рендер и AE.
    """
    proc = _spawn_service_process(ws, "--slots", "1")
    try:
        assert model_service._wait_settings(str(ws["settings"]), proc,
                                            PROC_START_TIMEOUT_S) is not None, (
            "боевой процесс не поднял слушателя"
        )
        assert model_service.transcribe(ws["wav"], "v3_ctc"), "боевой процесс не посчитал"
        code = _wait_service_exit(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10.0)
        model_service.clear_settings(str(ws["settings"]))
    assert code == 0, "боевой процесс завершился не штатно: код %s" % code


def test_shutdown_гасит_сервис(ws: dict[str, Any]) -> None:
    """`shutdown()` — слушатель закрыт и порт больше не отвечает."""
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        assert model_service.service_alive() is True
        assert model_service.shutdown() is True
        assert service.wait_until_stopped(timeout=10.0) is True, "слушатель не закрылся"
        assert model_service.service_alive() is False, "сервис отвечает после shutdown"
    finally:
        _join(service)


def test_cli_не_падает_на_кодировке_консоли(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вывод CLI модуля переживает cp1252-консоль: русского текста в ней нет.

    `python -m core.model_service --stop` в консоли Windows падал
    `UnicodeEncodeError: 'charmap' codec can't encode …` — stdout там не UTF-8, а
    ответ сервиса русский. Проверяем ВЕСЬ вывод модуля: `--stop`, `--status` (оба
    ответа) и сообщение об отказе слушателя; поток — как у консоли, cp1252 и
    строгий (`errors="strict"`): молчаливая замена спрятала бы ту же ошибку.
    """
    captured = io.BytesIO()

    def cp1252_stream() -> io.TextIOWrapper:
        """Поток с кодировкой консоли Windows: что не влезло — тот и падает."""
        return io.TextIOWrapper(captured, encoding="cp1252", errors="strict", newline="")

    monkeypatch.setattr(sys, "stdout", cp1252_stream())
    monkeypatch.setattr(model_service, "service_alive", lambda: False)
    assert model_service.main(["--status"]) == 1
    monkeypatch.setattr(model_service, "shutdown", lambda: False)
    assert model_service.main(["--stop"]) == 0
    monkeypatch.setattr(model_service, "service_alive", lambda: True)
    assert model_service.main(["--status"]) == 0
    blocker = _occupied_port()
    try:
        monkeypatch.setattr(model_service, "_pick_port", lambda: int(blocker.getsockname()[1]))
        assert model_service.main(["--serve"]) == 1
    finally:
        blocker.close()
    sys.stdout.flush()
    text = captured.getvalue().decode("utf-8")
    assert "остановлен" not in text and "нет сервиса" in text, text
    assert "сервиса не было" in text, text
    assert "жив" in text, text
    assert "слушатель не открылся" in text, (
        "сообщение об отказе слушателя не через дверь CLI: %r" % text
    )


def test_unload_освобождает_веса_не_гася_сервис(ws: dict[str, Any]) -> None:
    """`unload()` чистит кеш моделей, но процесс остаётся — следующая просьба грузит заново."""
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        _warm_up(ws["wav"])
        _reset_fake()
        assert model_service.transcribe(ws["wav"], "v3_ctc")
        assert _fake_state().LOADED == 0, "вторая просьба прочитала веса заново"
        assert model_service.unload() is True
        assert model_service.service_alive() is True, "unload погасил сервис"
        assert model_service.transcribe(ws["wav"], "v3_ctc")
        assert _fake_state().LOADED == 1, "после unload модель не загрузилась заново"
    finally:
        _join(service)


def test_сервис_заводится_без_консоли_но_не_оторванным() -> None:
    """Процесс сервиса — без консоли (`CREATE_NO_WINDOW`), но НЕ `DETACHED_PROCESS`.

    Это не придирка к флагу, а замер: у оторванного процесса каждый `CreateProcess`
    стоит 0.4–0.9 с вместо 0.05 с, а GigaAM читает звук окна через `ffmpeg` — по
    запуску на каждое окно. С `DETACHED_PROCESS` окна распознавания шли 0.43–0.51 с
    против 0.075 с в процессе ролика, и счёт ролика через сервис вырастал с 2.9 с
    до 19.5 с. Мутация: вернуть `DETACHED_PROCESS` — тест краснеет.
    """
    flags = model_service.service_creationflags(nt=True)
    assert flags & model_service.DETACHED_PROCESS == 0, (
        "сервис заводится оторванным от консоли: запуск дочерних процессов в нём "
        "дорожает в разы, а там ffmpeg на каждое окно распознавания"
    )
    assert flags & model_service.CREATE_NO_WINDOW, flags
    assert model_service.service_creationflags(nt=False) == 0, (
        "на POSIX своих флагов нет — свою сессию задаёт start_new_session"
    )


def test_прогрев_читает_веса_до_первого_ролика(ws: dict[str, Any]) -> None:
    """`preload` грузит веса, и первый счёт за них уже не платит.

    Замеру это нужно ровно затем, чтобы время чтения весов не попадало в «счёт»
    первого ролика. Мутация: убрать вызов `_loaded_model` из `_preload` — счётчик
    загрузок останется нулевым, и тест покраснеет.
    """
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        assert model_service.preload("v3_ctc") is True
        assert _fake_state().LOADED == 1, "прогрев не прочитал веса"
        _reset_fake()
        assert model_service.transcribe(ws["wav"], "v3_ctc")
        assert _fake_state().LOADED == 0, "ролик прочитал веса заново после прогрева"
    finally:
        _join(service)


def test_сервис_отдаёт_свою_память(ws: dict[str, Any]) -> None:
    """`stats` отвечает памятью сервиса (на процессоре — нулями), не гася процесс.

    В сервисном режиме стенда torch у детей нет вовсе, и в отчёте стояли нули —
    будто распознавание ничего не заняло. Мутация: убрать команду `stats` —
    клиент вернёт `None` вместо словаря, и тест покраснеет.
    """
    _clear_settings(ws)
    assert model_service.vram_stats() is None, "сервиса нет, а память откуда-то взялась"
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        stats = model_service.vram_stats()
        assert stats is not None, "живой сервис не отдал память"
        assert set(stats) == {"max_memory_allocated", "max_memory_reserved"}, stats
        assert model_service.service_alive(), "stats погасил сервис"
    finally:
        _join(service)


def test_две_первые_просьбы_грузят_веса_один_раз(ws: dict[str, Any],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Два ПЕРВЫХ запроса разом: веса читаются один раз, а не «по разу на запрос».

    Раньше оба потока успевали пройти проверку кэша до того, как первый положит
    модель, и читали веса каждый сам (замер: 2.45 с на прогрев вместо 1.93 с), а
    на карте оказывались ДВЕ копии модели — вторая затирала кэш первой. Мутация:
    снять `_models_load_lock` — счётчик загрузок станет 2, и тест покраснеет.

    Запросы обязаны быть ПЕРВЫМИ, поэтому кэш весов чистится здесь ЯВНО, и счётчик
    подмены начинается с нуля. Прежде тест молча рассчитывал на уборку фиксатора:
    кэш живёт в процессе и переживает тест, и стоило фиксатору разок не убрать за
    соседом — тест видел готовую модель и падал «прочитано 0 раз вместо одного».
    Ровно это и случилось в одном прогоне Linux из двух.
    """
    monkeypatch.setattr(model_service, "_models", {})
    service = _server(ws["settings"], slots=2)
    try:
        got = _transcriptions(ws["wav"], 2)      # оба запроса — ПЕРВЫЕ, без прогрева
    finally:
        _join(service)

    assert all(words for words in got), f"ответы: {got}"
    state = _fake_state()
    assert state.LOADED == 1, (
        f"веса прочитаны {state.LOADED} раза вместо одного — кэш загрузки не держит"
    )


def _load_subst_together(path: str, start: threading.Barrier) -> Any:
    """Позвать `_exec_module` так, чтобы все потоки столкнулись на входе.

    Барьер, а не только пауза в теле модуля: так «одновременность» — свойство
    теста, а не удачного расклада потоков по ядрам.
    """
    start.wait(timeout=30.0)
    return model_service._exec_module(path)


def test_одновременные_первые_просьбы_грузят_подмену_один_раз(tmp_path: Path) -> None:
    """N потоков разом зовут `_exec_module` по одному пути: один объект, один запуск.

    Ровно здесь и была гонка: проверка кэша `_subst_modules` и `exec_module` шли без
    замка, и два ПЕРВЫХ запроса выполняли файл подмены каждый сам. Объектов модуля
    выходило два: `sys.modules["gigaam"]` получал один, а в кэш попадал другой
    (последний записавший), поэтому веса грузил один объект, а счётчик читался у
    другого — «прочитано 0 раз вместо одного» в части прогонов. Живых потоков после
    теста при этом не оставалось, и по одному счётчику причина не читалась.

    Мутация: снять замок `_subst_modules_lock` — файл подмены выполнится больше
    одного раза, и потоки получат разные объекты модуля; тест краснеет.
    """
    marker = tmp_path / "runs.txt"
    path = _write(tmp_path / "race_gigaam.py", _RACE_MODULE.format(marker=str(marker)))
    parties = 8
    start = threading.Barrier(parties)
    try:
        with ThreadPoolExecutor(max_workers=parties) as pool:
            futures = [pool.submit(_load_subst_together, path, start)
                       for _ in range(parties)]
            modules = [future.result(timeout=60) for future in futures]
    finally:
        # Кэш сервиса живёт в процессе и переживает тест: своей записи в нём не
        # оставляем, иначе следующий тест получил бы готовый модуль с чужим путём.
        model_service._subst_modules.pop(path, None)

    assert len({id(module) for module in modules}) == 1, (
        "потоки получили разные объекты подменного модуля — каждый выполнил файл сам"
    )
    assert marker.read_text(encoding="utf-8").splitlines() == ["run"], (
        "файл подмены выполнен не один раз"
    )
    assert modules[0].VALUE == 42, "подменный модуль выполнен не целиком"


def test_два_клиента_разом_заводят_один_сервис(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Двое стартовали разом — сервис заводит ОДИН, второй ждёт его адрес.

    Мутация: убрать заявку (`_claim_start`/`_release_start`) — `_spawn` позовут
    дважды, и на карте окажутся два CUDA-контекста и две копии весов, а лишний
    сервис проживёт до простоя, держа видеопамять.
    """
    import threading
    import time

    _clear_settings(ws)
    spawned: list[int] = []
    server: dict[str, Any] = {}

    def spawn() -> None:
        spawned.append(len(spawned) + 1)
        time.sleep(0.3)          # сервис поднимается не мгновенно — иначе гонки нет
        server["service"] = _server(ws["settings"], slots=1, authkey=b"race-key")

    monkeypatch.setattr(model_service, "_spawn", spawn)
    results: list[bool] = []

    def client() -> None:
        results.append(model_service.ensure_started(timeout=10.0))

    threads = [threading.Thread(target=client) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    try:
        assert results == [True, True], f"оба клиента должны получить сервис: {results}"
        assert len(spawned) == 1, f"сервис завели {len(spawned)} раза вместо одного"
    finally:
        _join(server["service"])


def test_брошенная_заявка_на_старт_не_блокирует_сервис(
    ws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заявка от упавшего стартера (старая) убирается — сервис заводится снова.

    Без этого упавший меж двух делом стартер оставлял бы заявку навсегда, и
    сервис не поднялся бы больше никогда. Мутация: убрать проверку возраста
    заявки — `_spawn` не позовут вовсе.
    """
    import time

    _clear_settings(ws)
    mark = model_service._start_mark_path(str(ws["settings"]))
    with open(mark, "w", encoding="utf-8") as f:
        f.write("стартер упал")
    old = time.time() - (model_service.START_CLAIM_STALE_S + 5.0)
    os.utime(mark, (old, old))

    server: dict[str, Any] = {}

    def spawn() -> None:
        server["service"] = _server(ws["settings"], slots=1, authkey=b"fresh-key")

    monkeypatch.setattr(model_service, "_spawn", spawn)
    try:
        assert model_service.ensure_started(timeout=10.0) is True, "заявка заблокировала старт"
    finally:
        _join(server["service"])


def test_shutdown_без_сервиса_не_падает(ws: dict[str, Any],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Гасить нечего — `False`, а не исключение (сервис мог уже уйти по простою).

    `_spawn` подменён отказом нарочно: иначе проверка «сервиса нет» сама завела
    бы настоящий процесс сервиса и мерила бы уже не то.
    """
    _clear_settings(ws)
    monkeypatch.setattr(model_service, "_spawn", lambda: (_ for _ in ()).throw(
        OSError("нет интерпретатора")))
    assert model_service.shutdown() is False
    assert model_service.unload() is False
    assert model_service.service_alive() is False


def test_сдавшийся_клиент_гасит_свой_сервис(ws: dict[str, Any],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Не дождались адреса — процесс, который завёл САМ клиент, гаснет по PID.

    Брошенный сервис живёт до простоя (пять минут) и всё это время держит
    видеопамять, которую просят рендер и AE. Гасим ровно свой процесс: искать
    «все python с model_service» по имени нельзя — рядом работают сервисы других
    копий интерфейса, и они не наши. Настоящий процесс здесь не запускается:
    `_spawn` подменён заглушкой, поэтому реальных сигналов тест не шлёт.

    Мутация: убрать `_kill_started` из `_spawn_and_wait` — тест краснеет.
    """
    _clear_settings(ws)

    class _Proc:
        """Процесс сервиса, который слушателя так и не открыл."""

        pid = 0x5EED
        killed = False

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            self.killed = True

    proc = _Proc()
    monkeypatch.setattr(model_service, "_spawn", lambda: proc)

    assert model_service.ensure_started(0.3) is False
    assert proc.killed, "брошенный сервис остался жив и держит видеопамять до простоя"
    assert model_service.service_alive() is False


# --------------------------------------------------------------------------- #
# 4. Слоты и запас VRAM — чистая арифметика
# --------------------------------------------------------------------------- #
def test_слоты_по_свободной_карте(monkeypatch: pytest.MonkeyPatch) -> None:
    """Слоты считаются как `free // (512 + 1024)`, в границах 1..4.

    Устройство подменено на `cuda`: профиль тестов ставит `cpu` (чтобы сервис не
    трогал живую карту), и без подмены эта ветка не проверялась бы вовсе.
    """
    monkeypatch.delenv("REELSI_MODEL_SERVICE_SLOTS", raising=False)
    monkeypatch.setattr(model_service, "_service_device", lambda: "cuda")
    assert model_service.slot_count(free_mib=4096) == 2
    assert model_service.slot_count(free_mib=7000) == 4
    assert model_service.slot_count(free_mib=1536) == 1
    assert model_service.slot_count(free_mib=100) == 1, "карта занята — всё равно один слот"
    assert model_service.slot_count(free_mib=None) == model_service.MAX_SLOTS


def test_слоты_на_процессоре_по_ядрам(monkeypatch: pytest.MonkeyPatch) -> None:
    """Процессор: слотов — половина логических ядер, но не больше четырёх.

    Мутация: снять ветку `cpu` — на машине без карты потолок стал бы 4 всегда.
    """
    monkeypatch.delenv("REELSI_MODEL_SERVICE_SLOTS", raising=False)
    monkeypatch.setattr(model_service, "_service_device", lambda: "cpu")
    monkeypatch.setattr(model_service.os, "cpu_count", lambda: 16)
    assert model_service.slot_count() == 4
    monkeypatch.setattr(model_service.os, "cpu_count", lambda: 6)
    assert model_service.slot_count() == 3
    monkeypatch.setattr(model_service.os, "cpu_count", lambda: 2)
    assert model_service.slot_count() == 1


def test_запас_vram_считается_с_резервом(monkeypatch: pytest.MonkeyPatch) -> None:
    """Слот влезает только вместе с запасом: 1536 МиБ мало, 1535 — ещё меньше.

    Устройство подменено на `cuda` ЯВНО: на машине без видеокарты `_service_device`
    отвечает «процессор», и `vram_fits` пропускает проверку целиком — тест зеленел
    бы, ничего не проверив, и зависел бы от железа (в Linux-прогоне проверки VRAM
    не было вовсе). `free_mib` при этом назван числом: мерить живую карту незачем.
    """
    monkeypatch.delenv("REELSI_MODEL_SERVICE_DEVICE", raising=False)
    monkeypatch.setattr(model_service, "_service_device", lambda: "cuda")
    assert model_service.vram_fits(free_mib=1600) is True
    assert model_service.vram_fits(free_mib=model_service.ASR_VRAM_MIB) is False
    assert model_service.vram_fits(free_mib=None) is True, "мера неизвестна — не гадаем"


def test_ожидание_свободной_vram_не_падает() -> None:
    """Занятая карта — сервис ждёт, а не отказывает: чужой RoFormer карту отпустит.

    `sleep` подменён: тест проверяет счётчик попыток, а не реальные секунды.
    """
    checks = {"n": 0, "slept": 0.0}

    def fits() -> bool:
        checks["n"] += 1
        return checks["n"] >= 3              # свободно стало на третьей проверке

    def sleep(sec: float) -> None:
        checks["slept"] += sec

    model_service.wait_for_vram(emit=lambda *a, **k: None, fits=fits, sleep=sleep)
    assert checks["n"] == 3, f"ждали не до свободной VRAM: {checks}"
    assert checks["slept"] == pytest.approx(model_service.VRAM_WAIT_S), (
        f"между проверками спали {checks['slept']}с — сервис крутил бы холостой цикл"
    )


# --------------------------------------------------------------------------- #
# 5. Движки: сервис берёт только головы GigaAM
# --------------------------------------------------------------------------- #
def test_сервис_берёт_только_головы_gigaam() -> None:
    """`engine_head`: у gigaam-движков голова, у остальных пусто, у RNN-T — тоже.

    Пусто значит «сервис этим не занимается»: `ctc:*`, whisper и omni идут
    прежним локальным путём.
    """
    assert model_service.engine_head("gigaam") == "v3_ctc"
    assert model_service.engine_head("gigaam:multilingual_large_ctc") == "multilingual_large_ctc"
    assert model_service.engine_head("gigaam:v3_rnnt") == "", "RNN-T не для нарезки"
    assert model_service.engine_head("whisper") == ""
    assert model_service.engine_head("ctc:large-v3") == ""


# --------------------------------------------------------------------------- #
# 6. Нарезка: сервис недоступен — считаем локально и называем причину
# --------------------------------------------------------------------------- #
def test_pipeline_без_сервиса_считает_локально_и_пишет_причину() -> None:
    """Сервиса нет → запасной путь под `gpu_lock` и строка с причиной в журнале.

    Мутация: убрать строку причины — тест краснеет; убрать запасной путь — тоже.
    """
    from core.gigaam_cut import pipeline

    calls: list[str] = []

    def fallback(wav: str, emit: Any = None) -> Any:
        calls.append(wav)
        return "слово", [{"w": "слово", "start": 0.0, "end": 1.0}]

    lines: list[str] = []

    def emit(line: str = "", /, **vars: Any) -> None:
        lines.append(line.format(**vars) if vars else line)

    text, words = pipeline.words_for_cut("a.wav", "gigaam", emit, fallback=fallback)

    assert calls == ["a.wav"], "локальный путь не позвали"
    assert text == "слово" and words == [{"w": "слово", "start": 0.0, "end": 1.0}]
    note = [ln for ln in lines if "сервис моделей недоступен" in ln]
    assert note, f"нет строки причины в журнале: {lines}"
    assert "— считаю в этом процессе" in note[0], note[0]


def test_pipeline_с_сервисом_локальный_путь_не_зовут(ws: dict[str, Any]) -> None:
    """Живой сервис отдал слова — локальное распознавание не вызывается вовсе."""
    from core.gigaam_cut import pipeline

    service = _server(ws["settings"], slots=1)
    called: list[str] = []

    def fallback(wav: str, emit: Any = None) -> Any:
        called.append(wav)
        raise AssertionError("локальный путь не должен вызываться при живом сервисе")

    try:
        text, words = pipeline.words_for_cut(ws["wav"], "gigaam", lambda *a, **k: None,
                                             fallback=fallback)
    finally:
        _join(service)

    assert not called
    assert text == "v3_ctc_word second", text
    assert [w["w"] for w in words] == ["v3_ctc_word", "second"]


def test_pipeline_падающий_сервис_уходит_на_локальный_путь(ws: dict[str, Any]) -> None:
    """Сервис отвечает отказом распознавания — нарезка считает сама.

    Так проверяется не «порт молчит», а именно отказ сервиса: клиент должен
    увидеть ошибку и уйти на запасной путь, а не унести её наверх.
    """
    from core.gigaam_cut import pipeline

    _broken_subst(ws)
    service = _server(ws["settings"], slots=1)
    lines: list[str] = []

    def emit(line: str = "", /, **vars: Any) -> None:
        lines.append(line.format(**vars) if vars else line)

    try:
        _text, words = pipeline.words_for_cut(
            ws["wav"], "gigaam", emit,
            fallback=lambda wav, emit=None: ("слово", [{"w": "слово", "start": 0.0, "end": 1.0}]))
    finally:
        _join(service)

    assert [w["w"] for w in words] == ["слово"], words
    assert any("сервис моделей недоступен" in ln for ln in lines), lines


# --------------------------------------------------------------------------- #
# 7. Стенд замера: режим `--service` и единицы VRAM
# --------------------------------------------------------------------------- #
def test_стенд_в_сервисном_режиме_шлёт_детей_через_сервис() -> None:
    """`--service` доезжает до ребёнка, и дети получают свой каталог сервиса.

    Мутация: не передать `--service` в команду ребёнка — замер «после» померил бы
    прежний путь (своя загрузка весов) и показал бы не то, что в бою.
    """
    import sys

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    args = bench._parse_args(["--wav", "a.wav", "--n", "2", "--service",
                              "--slots", "2", "--idle", "30",
                              "--lock", "C:/tmp/bench.lock"])
    cmd = bench._child_cmd("python", args, 0, "a.wav")
    assert "--service" in cmd, cmd
    env = bench._child_env(args)
    assert env["REELSI_JOB_LOCK"] == os.path.abspath("C:/tmp/bench.lock"), env
    # Каталог состояния — ОДНОЙ функцией ядра: и родитель, и дети, и сервис идут
    # одним путём, а не каждый своим (иначе клиент ждёт адрес там, где сервис его
    # не писал).
    assert env["REELSI_MODEL_SERVICE_STATE"] == model_service.state_path_for(
        os.path.abspath("C:/tmp/bench.lock")), env
    assert env["REELSI_MODEL_SERVICE_SLOTS"] == "2", env
    assert env["REELSI_MODEL_SERVICE_IDLE"] == "30.0", env

    # Без --service ничего сервисного в окружении не появляется.
    plain = bench._parse_args(["--wav", "a.wav", "--n", "1"])
    assert "--service" not in bench._child_cmd("python", plain, 0, "a.wav")
    assert "REELSI_MODEL_SERVICE_SLOTS" not in bench._child_env(plain)
    assert "REELSI_MODEL_SERVICE_STATE" not in bench._child_env(plain)


def test_стенд_прогревает_сервис_до_замера(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В сервисном режиме `prepare` и поднимает сервис, и читает веса заранее.

    Порядок именно такой: сперва `ensure_started`, потом `preload` — иначе
    прогревать нечего. Мутация: убрать `preload` — время чтения весов вернётся в
    «счёт» первого ролика, и сервисный замер покажет не то, что видит бой.

    Тест НЕ зависит от соседей и нагрузки. Файл состояния сервиса лежит в своём
    каталоге (`REELSI_MODEL_SERVICE_STATE`), подъём сервиса и прогрев подменены, а
    `torch`/`gigaam`/`silero_vad`/`transformers` — заглушки в `sys.modules`. Двух
    последних вещей требует сам `prepare` (он грузит модели для вздохов и эмоций и
    проверяет CUDA), но этому тесту они не нужны вовсе: он стережёт ПОРЯДОК двух
    вызовов. Настоящий импорт torch был здесь и главной причиной падений под
    `-n auto`: под нагрузкой он не укладывался в таймаут теста, и краснел тест про
    прогрев, к импорту отношения не имеющий. Порядок проверяем событием (`preload`
    обязан увидеть уже поднятый сервис), а не временем: ожидание «на всякий»
    скрыло бы регресс.
    """
    import argparse
    import types

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    # Своё состояние сервиса: без явной переменной путь берётся из общего лока
    # профиля тестов, а он один на все воркеры.
    monkeypatch.setenv("REELSI_MODEL_SERVICE_STATE",
                       str(tmp_path / "bench.modelsvc.json"))

    # Заглушки модулей: `prepare` обязан их импортировать и обернуть таймером, но
    # ни весов, ни карты, ни времени на импорт этому тесту не нужно.
    class _Cuda:
        """CUDA-часть torch: стенд проверяет карту и сбрасывает пик памяти."""

        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def reset_peak_memory_stats() -> None:
            return None

    torch_stub = types.ModuleType("torch")
    torch_stub.cuda = _Cuda               # type: ignore[attr-defined]
    gigaam_stub = types.ModuleType("gigaam")
    gigaam_stub.load_model = lambda *a, **k: None       # type: ignore[attr-defined]
    silero_stub = types.ModuleType("silero_vad")
    silero_stub.load_silero_vad = lambda *a, **k: None  # type: ignore[attr-defined]
    transformers_stub = types.ModuleType("transformers")

    class _Pretrained:
        """Загрузчик модели CED: `_trace_attr` оборачивает `from_pretrained`."""

        @classmethod
        def from_pretrained(cls, *a: Any, **k: Any) -> None:
            return None

    transformers_stub.AutoFeatureExtractor = _Pretrained   # type: ignore[attr-defined]
    transformers_stub.AutoModelForAudioClassification = _Pretrained  # type: ignore[attr-defined]
    for name, module in (("torch", torch_stub), ("gigaam", gigaam_stub),
                         ("silero_vad", silero_stub), ("transformers", transformers_stub)):
        monkeypatch.setitem(sys.modules, name, module)

    calls: list[str] = []
    started = threading.Event()

    def ensure_started(**kwargs: Any) -> bool:
        calls.append("start")
        started.set()
        return True

    def preload(head: str) -> bool:
        # Прогрев возможен только по поднятому сервису: до `ensure_started` событие
        # ещё не выставлено, и порядок «прогрев раньше старта» виден сразу.
        assert started.is_set(), "preload позвали ДО подъёма сервиса"
        calls.append("preload:" + head)
        return True

    monkeypatch.setattr(model_service, "ensure_started", ensure_started)
    monkeypatch.setattr(model_service, "preload", preload)
    ops = bench._RealOps(argparse.Namespace(asr_model="v3_ctc", service=True),
                         bench._Marks(0.0))
    ops.prepare()
    assert calls == ["start", "preload:v3_ctc"], calls


def test_стенд_поднимает_сервис_до_детей(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подъём сервиса — до детей и отдельной строкой таблицы, а не в «счёте» ролика.

    Подъём (процесс, импорт torch, веса) платится ОДИН раз на сервис, а не на
    ролик: при N=2 оба ролика ждали один и тот же старт, и сервисный режим
    выглядел медленнее процесса ровно на него. Мутация: убрать `_start_service`
    из прогона — старт вернётся в «загрузку» ролика.

    Прогрев идёт по ВСЕМ трём источникам весов, которыми пользуется замер: голова
    распознавания, CED вздохов и голова `emo`. Иначе чтение модели попало бы в
    «счёт» первого ролика — ровно та ошибка, из-за которой сервисный режим и
    выглядел хуже процесса.
    """
    import argparse

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    calls: list[str] = []

    def ensure_started(**kwargs: Any) -> bool:
        calls.append("start")
        return True

    def preload(head: str) -> bool:
        calls.append("preload:" + head)
        return True

    def preload_breath() -> bool:
        calls.append("preload:breath")
        return True

    def preload_emo() -> bool:
        calls.append("preload:emo")
        return True

    monkeypatch.setattr(model_service, "ensure_started", ensure_started)
    monkeypatch.setattr(model_service, "preload", preload)
    monkeypatch.setattr(model_service, "preload_breath", preload_breath)
    monkeypatch.setattr(model_service, "preload_emo", preload_emo)
    svc = argparse.Namespace(service=True, dry=False, asr_model="v3_ctc", work="w")
    plain = argparse.Namespace(service=False, dry=False, asr_model="v3_ctc", work="w")

    started = bench._start_service(svc)
    assert started is not None and started >= 0.0, started
    assert calls == ["start", "preload:v3_ctc", "preload:breath", "preload:emo"], calls
    assert bench._start_service(plain) is None, "без --service сервис поднимать нечего"

    result = bench._BenchResult(records=[], wall_s=1.0, vram_peak_mib=None, vram_samples=0,
                                dry=False, service=True, service_start_s=started,
                                table="", ok=True)
    table = bench._format_table(result)
    assert "подъём сервиса" in table, table
    assert "не входит" in table, table


def test_стенд_берёт_память_сервиса_в_сервисном_режиме() -> None:
    """Пик памяти берётся у сервиса: у ребёнка в этом режиме torch нет вовсе.

    Раньше функция отдавала нули, и в таблице выходило, будто распознавание через
    сервис не заняло памяти. Мутация: вернуть нули — тест краснеет.
    """
    import argparse

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    ops = bench._RealOps(argparse.Namespace(asr_model="v3_ctc", service=True),
                         bench._Marks(0.0))
    ops._svc = argparse.Namespace(          # torch в ребёнке нет — только сервис
        vram_stats=lambda: {"max_memory_allocated": 1234, "max_memory_reserved": 1500})
    assert ops.vram_stats() == (1234, 1500), ops.vram_stats()


def test_стенд_печатает_vram_в_мибибайтах() -> None:
    """Пик памяти процесса — МиБ, а не байты под подписью «МиБ».

    `torch.cuda.max_memory_allocated` отдаёт байты: раньше в таблице стояло
    4000000000 под подписью «МиБ». Проверяем перевод делением на 1024^2.
    """
    import argparse
    import sys

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    ops = bench._RealOps(argparse.Namespace(asr_model="v3_ctc", service=False),
                         bench._Marks(0.0))

    class _Cuda:
        @staticmethod
        def max_memory_allocated() -> int:
            return 4 * 1024 * 1024 * 1024        # 4 ГиБ = 4096 МиБ

        @staticmethod
        def max_memory_reserved() -> int:
            return 2 * 1024 * 1024 * 1024

    ops._torch = argparse.Namespace(cuda=_Cuda)
    assert ops.vram_stats() == (4096, 2048), ops.vram_stats()


def test_стенд_в_сервисном_режиме_не_берёт_замок_на_вздохи_и_эмоции() -> None:
    """`--service` уводит вздохи и эмоции в сервис, и файловый замок там не берётся.

    С замком дети ждали бы друг друга и замер показывал бы «ожидание» там, где в
    бою его нет вовсе: вздохи и эмоции через сервис идут без `gpu_lock` — это и
    есть предмет замера. Мутация: вернуть замок в сервисном режиме — тест краснеет.
    """
    import argparse

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import bench_gpu_cut as bench

    served = argparse.Namespace(service=True, dry=False, asr_model="v3_ctc", work="w")
    plain = argparse.Namespace(service=False, dry=False, asr_model="v3_ctc", work="w")
    dry = argparse.Namespace(service=True, dry=True, asr_model="v3_ctc", work="w")

    assert bench._service_models(served) is True
    assert bench._service_models(plain) is False
    assert bench._service_models(dry) is False, (
        "--dry меряет прежний путь: заглушкам сервис не нужен, и замок обязан остаться"
    )


# --------------------------------------------------------------------------- #
# 8. Вздохи (CED) и эмоции (emo) — тоже в сервисе
# --------------------------------------------------------------------------- #
_CED_CLASSES = 4          # классы AudioSet у подмены: ширина матрицы CED


def _ced_loader(loads: dict[str, int] | None = None, pause: float = 0.0) -> Any:
    """Подмена `breath.load_ced`: считает загрузки и отдаёт предсказуемый CED.

    Форма ответа та же, что у настоящей пары (фронтенд + модель): `fe(clips, ...)`
    отдаёт словарь тензоров, `m(**enc).logits` — матрицу по классам, а
    `config.num_labels` задаёт её ширину. Числа зависят от САМИХ участков (среднее
    по отсчётам окна), а не только от их числа: иначе «через сервис == локально»
    проверяло бы одно тождество, и разъехавшиеся границы окон прошли бы unnoticed.
    """
    import torch
    import types

    class _Fe:
        """Фронтенд: «кодирует» окна, отдавая их же батчем (10 с — как настоящий)."""

        @classmethod
        def from_pretrained(cls, *a: Any, **k: Any) -> Any:
            return cls()

        def __call__(self, clips: Any, sampling_rate: int = 0,
                     return_tensors: str = "") -> dict[str, Any]:
            return {"clips": torch.as_tensor(np.asarray(clips, dtype="float32"))}

    class _Model:
        """Модель: `config.num_labels` + `__call__` -> `logits` формы `[B, C]`."""

        config = types.SimpleNamespace(num_labels=_CED_CLASSES)

        @classmethod
        def from_pretrained(cls, *a: Any, **k: Any) -> Any:
            return cls()

        def to(self, dev: Any) -> Any:
            return self

        def eval(self) -> Any:
            return self

        def __call__(self, **enc: Any) -> Any:
            clips = enc["clips"]
            level = clips.abs().mean(dim=-1, keepdim=True)         # [B, 1]
            row = torch.linspace(0.0, 1.0, _CED_CLASSES)           # [C]
            return types.SimpleNamespace(logits=level * row)

    def load() -> Any:
        if pause:
            time.sleep(pause)         # веса читаются не мгновенно: иначе гонки не поймать
        if loads is not None:
            loads["n"] = loads.get("n", 0) + 1
        return (_Fe(), _Model(), "cpu")     # устройство cpu: живую карту тест не трогает

    return load


def _fake_emo(loads: dict[str, int] | None = None, pause: float = 0.0) -> Any:
    """Подмена головы `emo`: считает загрузки и считает вероятности по окну."""

    def load() -> Any:
        if pause:
            time.sleep(pause)
        if loads is not None:
            loads["n"] = loads.get("n", 0) + 1
        return _StubEmo()

    return load


class _StubEmo:
    """Голова `emo` для теста: `forward`/`head`/`id2name`, как у настоящей."""

    def __init__(self) -> None:
        import torch
        self._device = "cpu"
        self._dtype = torch.float32
        self.id2name = {0: "angry", 1: "sad", 2: "neutral", 3: "positive"}
        self.forward_calls = 0

    def forward(self, wav: Any, length: Any) -> Any:
        import torch
        self.forward_calls += 1
        level = float(wav.abs().mean())
        enc = torch.full((int(wav.shape[0]), 4, 8), level, dtype=torch.float32)
        return enc, torch.full((int(wav.shape[0]),), enc.shape[-1])

    def head(self, pooled: Any) -> Any:
        import torch
        return torch.tensor([[2.0, 1.0, 0.5, 0.0]], dtype=torch.float32) * float(pooled.abs().mean())


def _ramp_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> str:
    """Звук с разными отсчётами по времени: окна обязаны отличаться друг от друга.

    Файл-фикстура `ws` — нули, и на нём CED вернул бы одну и ту же матрицу для
    любых границ: сравнение «через сервис == локально» ничего бы не доказывало.
    """
    import numpy as np
    import soundfile as sf
    t = np.arange(int(seconds * rate), dtype="float32") / float(rate)
    sf.write(str(path), (0.6 * np.sin(2.0 * np.pi * 11.0 * t)).astype("float32"), rate)
    return str(path)


def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Забыть прочитанные веса в обоих кешах: тест обязан мерить СВОИ загрузки.

    Кеш CED и головы `emo` живёт в процессе (у сервиса — свой словарь, у
    локального пути — глобал `core.breath`), а процессы между тестами одни и те
    же: без сброса счётчик загрузок показал бы ноль на чужой модели.
    """
    from core import breath
    monkeypatch.setattr(model_service, "_ced_models", {})
    monkeypatch.setattr(model_service, "_emo_models", {})
    monkeypatch.setattr(breath, "_CED", None)


def test_ced_в_сервисе_читается_один_раз_на_двух_клиентов(ws: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Два клиента разом просят вздохи: веса CED читаются ОДИН раз.

    Мутация: убрать кеш `_loaded_ced` — загрузок станет две, и тест покраснеет.
    Ровно это и было проблемой: CED читался заново в каждом ролике нарезки, под
    файловым замком, и медиана ожидания замка была 23 с из 64 с ролика.
    """
    import numpy as np
    pytest.importorskip("torch")

    _fresh_caches(monkeypatch)
    loads: dict[str, int] = {}
    monkeypatch.setattr("core.breath.load_ced", _ced_loader(loads, pause=0.2))
    wav = _ramp_wav(ws["tmp"] / "ramp.wav")
    spans = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.6)]

    service = _server(ws["settings"], slots=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(model_service.breath_probs, wav, spans)
                       for _ in range(2)]
            got = [f.result(timeout=60) for f in futures]
    finally:
        _join(service)

    assert all(isinstance(g, np.ndarray) and g.shape == (len(spans), _CED_CLASSES)
               for g in got), [getattr(g, "shape", g) for g in got]
    assert loads.get("n") == 1, (
        f"веса CED прочитаны {loads.get('n')} раз вместо одного — кеш сервиса не держит"
    )


def test_emo_в_сервисе_читается_один_раз_на_двух_клиентов(ws: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Два клиента разом просят эмоцию окна: голова `emo` читается ОДИН раз.

    Раньше `load_emo_model` шёл на КАЖДЫЙ расчёт силы жёлтых и `release_emo` — сразу
    после. Мутация: убрать кеш `_loaded_emo` — загрузок станет две.
    """
    pytest.importorskip("torch")

    _fresh_caches(monkeypatch)
    loads: dict[str, int] = {}
    monkeypatch.setattr("core.emphasis.load_emo_model_local", _fake_emo(loads, pause=0.2))
    window = np.zeros(int(1.0 * 22050), dtype="float32")
    window[500:1500] = 0.5

    service = _server(ws["settings"], slots=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(model_service.emotion_probs, window, 22050)
                       for _ in range(2)]
            got = [f.result(timeout=60) for f in futures]
    finally:
        _join(service)

    assert all(set(g) == {"angry", "sad", "neutral", "positive"} for g in got), got
    assert loads.get("n") == 1, (
        f"веса головы emo прочитаны {loads.get('n')} раз вместо одного — кеша нет"
    )


def test_вздохи_через_сервис_равны_локальным(ws: dict[str, Any],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Числа CED через сервис == локальным: подменённая модель, один и тот же звук.

    Сравниваются матрицы целиком (`tobytes`), а не «примерно равны»: разойтись они
    могут ровно на границах окон — сервис читает звук сам, и его окна обязаны
    совпасть с клиентскими. Мутация: считать в сервисе другой батч или свои границы
    — тест краснеет.
    """
    pytest.importorskip("torch")

    _fresh_caches(monkeypatch)
    loader = _ced_loader()
    monkeypatch.setattr("core.breath.load_ced", loader)
    wav = _ramp_wav(ws["tmp"] / "ramp.wav")
    spans = [(0.0, 0.4), (0.35, 0.9), (1.1, 1.7)]

    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        served = model_service.breath_probs(wav, spans)
    finally:
        _join(service)

    from core import breath
    local = breath.ced_probs(breath.read_mono(wav), spans, ced=loader())
    assert served.shape == local.shape, (served.shape, local.shape)
    assert served.tobytes() == local.tobytes(), (
        "вероятности CED через сервис разошлись с локальными:\n%s\n%s" % (served, local)
    )


@pytest.mark.xdist_group("model_service")
def test_эмоции_через_сервис_равны_локальным(ws: dict[str, Any],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Вероятности эмоций через сервис == локальным (подменённая голова `emo`).

    Окно уезжает в сервис целиком, и счёт там идёт тем же `emotion_probs`: числа
    обязаны совпасть до последнего бита, иначе сила жёлтых у собранного клипа
    отличалась бы от посчитанной здесь.
    """
    import numpy as np
    pytest.importorskip("torch")

    _fresh_caches(monkeypatch)
    loader = _fake_emo()
    monkeypatch.setattr("core.emphasis.load_emo_model_local", loader)
    monkeypatch.setattr(model_service, "PING_TIMEOUT_S", CLIENT_PING_PATIENCE_S)
    window = np.zeros(int(0.8 * 22050), dtype="float32")
    window[200:900] = 0.4

    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        served = model_service.emotion_probs(window, 22050)
    finally:
        _join(service)

    from core import emphasis
    local = emphasis.emotion_probs(window, loader(), 22050)
    assert served == local, (served, local)


def test_сервиса_нет_вздохи_идут_под_замком_и_с_причиной(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сервис недоступен → вздохи считаются здесь, под `gpu_lock("вздохи")`, с причиной.

    Мутации: убрать запасной путь (считать без замка) — краснеет проверка замка и
    `service=False`; убрать строку причины — краснеет последняя проверка.
    """
    import contextlib

    from core.gigaam_cut import pipeline

    taken: list[str] = []
    calls: list[bool] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Any:
        taken.append(label)
        yield

    def fake_breaths(keep: Any, assign: Any, wav: str, words: Any, out: str,
                     emit: Any = None, service: bool = False) -> Any:
        calls.append(service)
        return keep, assign, []

    lines: list[str] = []

    def emit(line: str = "", /, **vars: Any) -> None:
        lines.append(line.format(**vars) if vars else line)

    monkeypatch.setattr(pipeline, "gpu_lock", fake_lock)
    monkeypatch.setattr(pipeline, "breath_detector_ready", lambda: True)
    monkeypatch.setattr(model_service, "preload_breath", lambda: False)
    monkeypatch.setattr(pipeline, "_cut_breaths", fake_breaths)

    keep, assign, marks = pipeline._breath_stage(
        [(0.0, 1.0)], None, "a.wav", [], "out.xml", emit)

    assert keep == [(0.0, 1.0)] and assign is None and marks == []
    assert taken == ["вздохи"], "вздохи без сервиса обязаны идти под замком видеокарты"
    assert calls == [False], "запасной путь обязан считать в этом процессе"
    assert any("сервис моделей недоступен" in ln and "вздохи" in ln for ln in lines), lines


def test_сервис_есть_вздохи_идут_без_замка(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сервис готов считать CED → вздохи через него, и файловый замок НЕ берётся.

    С замком ролики ждали бы друг друга ровно так же, как до сервиса: веса уже в
    его памяти, а одновременный счёт ограничивают слоты. Мутация: вернуть
    `gpu_lock` вокруг сервисного пути — тест краснеет.
    """
    import contextlib

    from core.gigaam_cut import pipeline

    taken: list[str] = []
    calls: list[bool] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Any:
        taken.append(label)
        yield

    def fake_breaths(keep: Any, assign: Any, wav: str, words: Any, out: str,
                     emit: Any = None, service: bool = False) -> Any:
        calls.append(service)
        return keep, assign, []

    monkeypatch.setattr(pipeline, "gpu_lock", fake_lock)
    monkeypatch.setattr(pipeline, "breath_detector_ready", lambda: True)
    monkeypatch.setattr(model_service, "preload_breath", lambda: True)
    monkeypatch.setattr(pipeline, "_cut_breaths", fake_breaths)

    pipeline._breath_stage([(0.0, 1.0)], None, "a.wav", [], "out.xml",
                           lambda *a, **k: None)

    assert taken == [], "сервисный путь не должен брать файловый замок"
    assert calls == [True], "вздохи обязаны уехать в сервис"


def test_отказ_сервиса_на_вздохах_уходит_под_замок(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сервис отказал посреди счёта → шаг повторяется здесь, под замком, с причиной.

    Так закрыт настоящий риск: если сервис умер между «он жив» и «посчитай», веса
    нельзя грузить мимо замка — на Windows переполнение VRAM вешает машину.
    Мутация: проглотить `ServiceUnavailable` внутри `_cut_breaths` — шаг остался бы
    несделанным, и тест краснеет.
    """
    import contextlib

    from core.gigaam_cut import pipeline

    taken: list[str] = []
    calls: list[bool] = []
    lines: list[str] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Any:
        taken.append(label)
        yield

    def fake_breaths(keep: Any, assign: Any, wav: str, words: Any, out: str,
                     emit: Any = None, service: bool = False) -> Any:
        calls.append(service)
        if service:
            raise model_service.ServiceUnavailable("сервис моделей не ответил за 900с")
        return keep, assign, []

    def emit(line: str = "", /, **vars: Any) -> None:
        lines.append(line.format(**vars) if vars else line)

    monkeypatch.setattr(pipeline, "gpu_lock", fake_lock)
    monkeypatch.setattr(pipeline, "breath_detector_ready", lambda: True)
    monkeypatch.setattr(model_service, "preload_breath", lambda: True)
    monkeypatch.setattr(pipeline, "_cut_breaths", fake_breaths)

    pipeline._breath_stage([(0.0, 1.0)], None, "a.wav", [], "out.xml", emit)

    assert calls == [True, False], "после отказа сервиса шаг обязан повториться здесь"
    assert taken == ["вздохи"], taken
    assert any("отказал на вздохах" in ln for ln in lines), lines


def test_детектор_выключен_сервис_о_вздохах_не_спрашивают(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет json модели вздохов → сервис не поднимаем и о CED не спрашиваем.

    Считать всё равно нечего, а прогрев CED читал бы веса впустую (на машине без
    обученной модели — на каждой нарезке).
    """
    import contextlib

    from core.gigaam_cut import pipeline

    taken: list[str] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Any:
        taken.append(label)
        yield

    def boom() -> bool:
        raise AssertionError("сервис о вздохах спросили при выключенном детекторе")

    monkeypatch.setattr(pipeline, "gpu_lock", fake_lock)
    monkeypatch.setattr(pipeline, "breath_detector_ready", lambda: False)
    monkeypatch.setattr(model_service, "preload_breath", boom)
    monkeypatch.setattr(pipeline, "_cut_breaths", lambda k, a, *args, **kw: (k, a, []))

    pipeline._breath_stage([(0.0, 1.0)], None, "a.wav", [], "out.xml",
                           lambda *a, **k: None)
    assert taken == ["вздохи"], taken


def test_эмоции_без_сервиса_берут_модель_здесь(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_emo_model` без сервиса отдаёт модель ЭТОГО процесса, а не обёртку."""
    from core import emphasis
    sentinel = object()
    monkeypatch.setattr(model_service, "preload_emo", lambda: False)
    monkeypatch.setattr(emphasis, "load_emo_model_local", lambda: sentinel)
    assert emphasis.load_emo_model() is sentinel


def test_эмоции_с_сервисом_весов_здесь_не_читают(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сервис жив → модель эмоций это обёртка, и `release_emo` ничего не грузит.

    `release_emo` у модели из сервиса не должен ни читать `torch`, ни отпускать
    чужие веса: выгрузкой занят простой сервиса. Мутация: звать `_free_torch` для
    обёртки — тест краснеет (в этом процессе torch не импортирован).
    """
    from core import emphasis

    def boom() -> Any:
        raise AssertionError("модель обязана жить в сервисе")

    monkeypatch.setattr(model_service, "preload_emo", lambda: True)
    monkeypatch.setattr(emphasis, "load_emo_model_local", boom)

    model = emphasis.load_emo_model()
    assert isinstance(model, emphasis.ServiceEmo)
    assert emphasis.release_emo(model) is None, "release_emo упал на модели из сервиса"


# --------------------------------------------------------------------------- #
# 9. Выбор устройства: «где считать модели» — видеокарта или процессор
# --------------------------------------------------------------------------- #
def _ai_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Свой ai_config.json на время теста: настройка машинная, боевую не трогаем.

    Путь конфига `core.aicut.config` берёт при импорте (`AI_CONFIG_PATH`), поэтому
    подменяем саму константу, а не переменную окружения, — как это делает conftest.
    """
    from core.aicut import config as aicut_config
    path = tmp_path / "ai_config.json"
    monkeypatch.setattr(aicut_config, "AI_CONFIG_PATH", str(path))
    return path


def _write_ai_config(path: Path, **fields: Any) -> None:
    """Минимальный конфиг: один профиль (иначе `load_ai_config` уйдёт на дефолт)."""
    path.write_text(json.dumps({"active": "Тест", "profiles": {
        "Тест": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                 "api_key": "", "model": "m"}}, **fields}, ensure_ascii=False),
        encoding="utf-8")


def test_устройство_сервиса_берётся_из_настройки(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """«Где считать модели» = процессор → сервис считает на `cpu`, слоты — по ядрам.

    Настройка живёт в сервисном конфиге (не в localStorage): сервис — ОТДЕЛЬНЫЙ
    процесс, и в браузер за ней он не пойдёт. Мутация: не читать настройку (звать
    `pick_device()` без force) — на машине с картой сервис остался бы на `cuda`,
    а слотовый счёт — по видеопамяти, и тест покраснеет.
    """
    path = _ai_config_path(tmp_path, monkeypatch)
    _write_ai_config(path, model_device="cpu")
    assert model_service.model_device_cfg() == "cpu", "настройка не дочитана до сервиса"

    # Перемычка `DEVICE` сильнее настройки (нужна тестам и замерам) — снимаем её,
    # иначе проверяли бы перемычку, а не конфиг.
    monkeypatch.delenv("REELSI_MODEL_SERVICE_DEVICE", raising=False)
    monkeypatch.delenv("REELSI_MODEL_SERVICE_SLOTS", raising=False)
    assert model_service._service_device() == "cpu", (
        "сервис считал бы не на том устройстве, что выбрано в настройках")
    monkeypatch.setattr(model_service.os, "cpu_count", lambda: 6)
    assert model_service.slot_count() == 3, "на процессоре слоты считаются по ядрам"

    # Своё число слотов у сервиса остаётся сильнее любой настройки.
    monkeypatch.setenv("REELSI_MODEL_SERVICE_SLOTS", "1")
    assert model_service.slot_count() == 1, "перемычка слотов перестала работать"


def test_слоты_для_названного_устройства_не_зависят_от_этого_процесса(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`slot_count(device=…)` — арифметика для ЯВНО названного устройства.

    Так интерфейс считает подпись под «Роликов одновременно», не поднимая сервис
    и не зная, на чём считает он сам. `free_mib` назван явно: мерить живую карту
    тест не должен.
    """
    monkeypatch.delenv("REELSI_MODEL_SERVICE_SLOTS", raising=False)
    monkeypatch.setattr(model_service.os, "cpu_count", lambda: 8)
    assert model_service.slot_count(free_mib=7000, device="cpu") == 4
    assert model_service.slot_count(free_mib=4096, device="cuda") == 2
    assert model_service.slot_count(free_mib=1536, device="cuda") == 1


def test_выбор_устройства_валидируется() -> None:
    """Действие `set_model_device`: известное слово — пишем, чужое — отказ.

    Мутация: убрать проверку — в конфиг попало бы слово, которого `pick_device` не
    знает, и сервис молча остался бы на прежнем устройстве, а настройка врала бы.
    """
    from core.aicut import config_actions

    cfg: dict[str, Any] = {}
    config_actions.set_model_device(cfg, {"value": "cpu"})
    assert cfg["model_device"] == "cpu"
    config_actions.set_model_device(cfg, {"value": "cuda"})
    assert cfg["model_device"] == "cuda"
    # Пусто — «авто» (значение по умолчанию), а не отказ: так снимается выбор.
    config_actions.set_model_device(cfg, {"value": ""})
    assert cfg["model_device"] == "auto"
    with pytest.raises(Exception):
        config_actions.set_model_device(cfg, {"value": "supergpu"})
    assert cfg["model_device"] == "auto", "чужое слово всё-таки записалось"


def test_настройка_читается_через_модуль_устройства(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Чужое/битое значение в конфиге — «авто», а не поломка сервиса.

    Мутация: отдать из `core.device.model_device` что попало — `pick_device` получил
    бы неизвестное слово, и сервис поднялся бы «сам не зная на чём».
    """
    from core import device
    path = _ai_config_path(tmp_path, monkeypatch)
    _write_ai_config(path, model_device="videocard")
    assert device.model_device() == device.DEVICE_AUTO
    assert device.pick_device(force=device.model_device()) in device.VALID
    _write_ai_config(path, model_device=7)
    assert device.model_device() == device.DEVICE_AUTO, "число прошло за устройство"


# --------------------------------------------------------------------------- #
# 10. Версия кода в адресе: сервис другой версии гасится
# --------------------------------------------------------------------------- #
def test_сервис_другой_версии_гасится_и_заводится_новый(ws: dict[str, Any]) -> None:
    """Адрес сервиса с ЧУЖИМ отпечатком версии → старый гасится, поднимается новый.

    Старый сервис поднят ОТДЕЛЬНЫМ ПРОЦЕССОМ (`--idle 300`), как в бою: раньше он
    поднимался потоком в процессе pytest, и гашение по PID убивало сам pytest —
    прогон обрывался без итоговой строки. Теперь номер процесса ещё и проверяется
    (`_pid_is_service`), а по брошенному адресу процесс не трогают вовсе.

    До этого сервис жил до простоя и отвечал СТАРЫМ кодом: правка, скажем, окна
    распознавания не доезжала до машины, где сервис уже поднят, — и «поправил, а не
    работает» искали в правке. Мутация: убрать сверку отпечатка (`_settings_mismatch`
    всегда False) — новый сервис не заведут, и тест покраснеет.
    """
    _clear_settings(ws)
    old_proc = _spawn_service_process(ws, "--idle", "300")
    try:
        old = dict(_wait_live_settings(ws))
        assert old.get("pid") == old_proc.pid, (
            "адрес записан не тем процессом, который поднят: %r" % (old,))
        assert old.get("build"), "сервис не записал в адрес версию своего кода"
        assert old.get("device") == model_service._service_device(), old
        assert old.get("created"), (
            "сервис не записал метку своего старта — номер процесса остался бы "
            "непроверенным")

        # Сервис ТОЙ ЖЕ версии — просто подключаемся, ничего не гасим.
        assert model_service.ensure_started(timeout=5.0) is True
        assert model_service.read_settings(str(ws["settings"])).get("pid") == old["pid"], (
            "живой сервис своей версии зачем-то перезапустили")

        # Отпечаток разошёлся — это сервис ДРУГОЙ версии: старый гасится (ответил на
        # `shutdown` и вышел сам), новый заводится с фальшивой головой GigaAM.
        stale = dict(old, build=old["build"] + "0" * 16)
        model_service._write_settings(str(ws["settings"]), stale)
        assert model_service.ensure_started(timeout=20.0) is True
        fresh = model_service.read_settings(str(ws["settings"])) or {}
        assert model_service.service_alive(), "новый сервис не отвечает"
    finally:
        with contextlib.suppress(Exception):
            model_service.unload_models()
        model_service.shutdown()               # новый сервис — просьбой, как бой
        _drop_service_process(old_proc)        # старый: если ещё жив, он с простым 300

    assert old_proc.poll() is not None, "старый сервис другой версии не погашен"
    assert fresh.get("pid") != old["pid"], "новый сервис не занял свой адрес"
    assert fresh.get("build") == model_service._code_fingerprint(), fresh
    assert fresh.get("device") == model_service._service_device(), fresh


def test_drop_stale_гасит_чужую_версию_не_поднимая_новую(ws: dict[str, Any]) -> None:
    """`drop_stale` (старт сервера) гасит чужую версию и НЕ заводит новую.

    Старт интерфейса ничего не считает: поднятый там сервис держал бы видеопамять до
    простоя. А сервис СВОЕЙ версии не трогается вовсе — гасить нечего.
    """
    _clear_settings(ws)
    service = _server(ws["settings"], slots=1, idle=300.0)
    try:
        assert model_service.drop_stale() is False, "живой сервис своей версии погасили"
    finally:
        _join(service)

    # Чужой отпечаток: сервис гасится, новый не поднимается.
    service = _server(ws["settings"], slots=1, idle=300.0)
    live = model_service.read_settings(str(ws["settings"])) or {}
    model_service._write_settings(str(ws["settings"]),
                                  dict(live, build=live["build"] + "0" * 16))
    try:
        assert model_service.drop_stale() is True, "сервис другой версии не погашен"
        assert not model_service.read_settings(str(ws["settings"])), (
            "адрес погашенного сервиса остался на диске")
    finally:
        _join(service)


def test_адрес_без_версии_считается_чужой_версией(ws: dict[str, Any]) -> None:
    """Адрес без ключа версии — «версию не знаю», то есть сервис гасится, а не принимается.

    Файл мог остаться от прошлой сборки: принять его значит считать ролик старым кодом.
    """
    _clear_settings(ws)
    service = _server(ws["settings"], slots=1, idle=300.0)
    live = model_service.read_settings(str(ws["settings"])) or {}
    older = {k: v for k, v in live.items() if k != "build"}
    build = model_service._code_fingerprint()
    assert model_service._settings_mismatch(older, build) is True, (
        "адрес без версии кода приняли за свой — сервис отвечал бы старым кодом")
    assert model_service._settings_mismatch(live, build) is False, "свою же версию сочли чужой"
    _join(service)


# --------------------------------------------------------------------------- #
# 10б. Гашение по PID — только проверенный процесс сервиса
# --------------------------------------------------------------------------- #
def test_адрес_с_текущим_pid_не_гасит_свой_процесс(ws: dict[str, Any]) -> None:
    """PID в адресе — ЭТОТ процесс: файл убирается, процесс не трогают.

    Ровно этот случай убивал pytest: тест поднимает сервис потоком внутри своего
    процесса (`_server`), и в адресе стоял номер pytest — `drop_stale` гасил его
    `TerminateProcess`, прогон обрывался без итоговой строки (rc=1), а полный набор
    с многопроцессным запуском висел час: воркеры гибли, мастер их ждал.

    Мутация: убрать проверку `pid in (os.getpid(), os.getppid())` — тест краснеет:
    процесс исчезает вместе с прогоном, и это видно по ненулевому коду возврата.
    """
    _clear_settings(ws)
    service = _server(ws["settings"], slots=1, idle=300.0)
    # Отпечаток чужой — это сервис «другой версии», то есть по старому коду его
    # полагалось погасить по PID. Теперь номер проверку не прошёл — файл просто убран.
    live = model_service.read_settings(str(ws["settings"])) or {}
    model_service._write_settings(str(ws["settings"]),
                                  dict(live, build=live["build"] + "0" * 16))

    assert model_service.drop_stale() is True, "брошенный адрес не убран с диска"
    assert not model_service.read_settings(str(ws["settings"])), (
        "адрес брошенного файла остался на диске")
    assert model_service._pid_alive(os.getpid()), "гашение убило собственный процесс"
    _join(service)


def test_чужой_процесс_по_pid_не_гасят(ws: dict[str, Any]) -> None:
    """PID в адресе — чужой живой процесс: файл убирается, процесс остаётся жив.

    В бою это случай после перезагрузки: номер из старого файла адреса достался
    другому процессу. Гасить его нельзя ни при каких обстоятельствах, поэтому
    проверяется и командная строка (`… -m core.model_service … --serve`), и время
    старта процесса. Гасит чужой процесс САМ тест — по своему `Popen`.
    """
    _clear_settings(ws)
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        # Живой порт и рабочий ключ — НАШИ (иначе файл сочли бы мёртвым и до проверки
        # номера дело не дошло бы); чужой здесь ровно номер процесса.
        service = _server(ws["settings"], slots=1, idle=300.0, pid=stranger.pid)
        data = model_service.read_settings(str(ws["settings"])) or {}
        assert data.get("pid") == stranger.pid, data
        assert model_service._settings_alive(data) is True, data
        # Отпечаток чужой: по старому коду это был бы «сервис другой версии», и его
        # погасили бы по PID — то есть убили бы посторонний процесс.
        model_service._write_settings(str(ws["settings"]),
                                      dict(data, build=data["build"] + "0" * 16))

        assert model_service._pid_is_service(stranger.pid, data) is False, (
            "чужой процесс приняли за сервис — его погасили бы по PID")
        # `drop_stale` на этом адресе: файл убирается, чужой процесс не трогают.
        assert model_service.drop_stale() is True
        assert not model_service.read_settings(str(ws["settings"])), (
            "адрес брошенного файла остался на диске")
        assert model_service._pid_alive(stranger.pid), "чужой процесс погашен по PID"
        assert model_service._pid_alive(os.getpid()), "гашение убило собственный процесс"
    finally:
        with contextlib.suppress(Exception):
            service.stop()
            service.wait_until_stopped(timeout=10)
        stranger.kill()
        stranger.wait(timeout=10.0)


def test_настоящий_процесс_сервиса_узнаётся_по_pid(ws: dict[str, Any]) -> None:
    """Настоящий процесс `--serve` проверку проходит: иначе его не погасить никогда.

    Проверка «не наш процесс» не имеет права отвергать НАШ сервис: иначе брошенный
    сервис остался бы жив до простоя и держал видеопамять, которую просят рендер и AE.
    Здесь оба хода сразу: командная строка и метка старта в адресе.
    """
    _clear_settings(ws)
    proc = _spawn_service_process(ws, "--idle", "300")
    try:
        data = _wait_live_settings(ws)
        assert data.get("pid") == proc.pid, data
        assert model_service._pid_is_service(proc.pid, data) is True, (
            "настоящий сервис не прошёл проверку — его не погасили бы по PID")
        # Метка старта — то, что отличает сервис от переиспользованного номера:
        # подменённая метка проверку обязана провалить.
        assert model_service._pid_is_service(
            proc.pid, dict(data, created=float(data["created"]) + 3600.0)) is False, (
            "процесс с чужой меткой старта сочли нашим сервисом")
    finally:
        _drop_service_process(proc)


# --------------------------------------------------------------------------- #
# 11. Потолок для настроек: GET без torch и без запуска сервиса
# --------------------------------------------------------------------------- #
@pytest.fixture
def client() -> Any:
    """Тестовый клиент Flask: роут потолка зовётся так же, как из интерфейса."""
    from flask import Flask
    import api
    app = Flask("model_cap_test")
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_роут_потолка_отдаёт_устройство_и_слоты(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GET /api/model_cap` — то же, что `model_cap`, но по HTTP (как зовёт фронт).

    Один вызов на роут, и через НАСТОЯЩИЙ тестовый клиент: `tools/route_coverage.py`
    видит только такие вызовы, и роут без него считался бы «без тестов».
    """
    path = _ai_config_path(tmp_path, monkeypatch)
    _write_ai_config(path, model_device="cpu")
    r = client.get("/api/model_cap", headers={"Host": "127.0.0.1:5001"})
    body = r.get_json()
    assert r.status_code == 200 and body.get("ok") is True, body
    assert body["setting"] == "cpu" and body["device"] == "cpu", body
    assert body["auto"] is False, body
    assert isinstance(body["slots"], int) and 1 <= body["slots"] <= model_service.MAX_SLOTS, body


def test_потолок_виден_без_импорта_torch() -> None:
    """`GET /api/model_cap` отдаёт устройство и число слотов, НЕ импортируя torch.

    Сервер интерфейса живёт без torch, и подпись под «Роликов одновременно» не имеет
    права его туда притащить: свободную видеопамять отдаёт `nvidia-smi`. Проверка —
    в ЧИСТОМ подпроцессе: если бы torch импортировал кто-то из соседей, тест в общем
    процессе ничего бы не доказал. Заодно проверяется, что роут НЕ поднимает сервис:
    в реестре запусков `conftest` не появляется ни одного процесса.
    """
    code = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, %r)
        from flask import Flask
        import api
        app = Flask("cap"); app.register_blueprint(api.bp)
        r = app.test_client().get("/api/model_cap", headers={"Host": "127.0.0.1:5001"})
        body = r.get_json()
        print(json.dumps({"status": r.status_code, "body": body,
                          "torch": "torch" in sys.modules}))
    """) % ROOT
    env = dict(os.environ)
    env["REELSI_NO_BROWSER"] = "1"
    # Устройство пришпилено к процессору: иначе «авто» на этой машине честно назвало
    # бы видеокарту (её видит `nvidia-smi`), и тест мерил бы живую карту, а не подпись.
    env["REELSI_MODEL_SERVICE_DEVICE"] = "cpu"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["torch"] is False, "GET потолка импортирует torch"
    assert got["status"] == 200, got
    body = got["body"]
    assert body.get("ok") is True, body
    assert body.get("device") in ("cuda", "mps", "cpu"), body
    slots = body.get("slots")
    assert isinstance(slots, int) and 1 <= slots <= model_service.MAX_SLOTS, body
    # В тестовом профиле устройство пришпилено к процессору (`REELSI_MODEL_SERVICE_DEVICE`):
    # иначе подпись на машине с картой мерила бы живую видеопамять, а тест — её занятость.
    assert body["device"] == "cpu", body


def test_авто_в_подписи_видит_карту_без_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Авто» в подписи настроек: карта есть — `cuda`, карты нет — `cpu`. Без torch.

    До этого ветка «авто» без torch отвечала безусловным `cpu`, и на машине с
    видеокартой подпись в ⚙ обещала процессор, тогда как сервис выбирал карту. Ответ
    берётся у `core.device.auto_device_without_torch` — того же решения, что у сервиса.

    Мутация: вернуть `else: dev = "cpu"` — первый утверждённый ответ краснеет.
    `torch` из `sys.modules` убран нарочно: иначе тест проверял бы ветку с torch,
    которую в UI-процессе занимать нельзя.
    """
    monkeypatch.setattr(model_service, "model_device_cfg", lambda: "auto")
    monkeypatch.delenv("REELSI_MODEL_SERVICE_DEVICE", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    # Карта видна `nvidia-smi` — подпись обязана назвать видеокарту.
    monkeypatch.setattr(model_service.device, "nvidia_free_mib", lambda: 7000)
    assert model_service.model_cap()["device"] == "cuda", (
        "при «Авто» и видимой карте подпись настроек говорит «процессор»")
    assert 1 <= model_service.model_cap()["slots"] <= model_service.MAX_SLOTS

    # Карты нет — процессор (на этой платформе mps быть не может).
    monkeypatch.setattr(model_service.device, "nvidia_free_mib", lambda: None)
    monkeypatch.setattr(model_service.device.platform, "system", lambda: "Windows")
    assert model_service.model_cap()["device"] == "cpu", "без карты ждали процессор"


def test_ручной_выбор_в_подписи_не_переспрашивает_карту(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выбор человека сильнее признаков: «процессор» так и остаётся процессором.

    Выбор ВРУЧНУЮ не требует догадок вовсе — иначе человек, у которого карта занята
    локальной LLM, получал бы в подписи «видеокарта» и неверные слоты.
    """
    monkeypatch.delenv("REELSI_MODEL_SERVICE_DEVICE", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(model_service.device, "nvidia_free_mib", lambda: 7000)
    monkeypatch.setattr(model_service, "model_device_cfg", lambda: "cpu")
    body = model_service.model_cap()
    assert body["device"] == "cpu" and body["auto"] is False, body
    monkeypatch.setattr(model_service, "model_device_cfg", lambda: "cuda")
    body = model_service.model_cap()
    assert body["device"] == "cuda" and body["auto"] is False, body


def test_drop_stale_на_старте_сервера(monkeypatch: pytest.MonkeyPatch) -> None:
    """`webui.main` при старте гасит сервис другой версии — одним вызовом `drop_stale`.

    Без него после обновления кода на машине оставался бы живой сервис прежней
    версии, и первый же запрос клипа пошёл бы к нему.
    """
    import webui
    calls: list[int] = []
    monkeypatch.setattr(model_service, "drop_stale",
                        lambda path=None: calls.append(1) or True)
    assert webui._drop_stale_model_service() is True
    assert calls == [1], "webui не спросил у сервиса его версию"
    # Ошибка (нет прав, чужой процесс не отозвался) — не отказ запуска интерфейса.
    def boom(path: Any = None) -> bool:
        raise OSError("файла нет")
    monkeypatch.setattr(model_service, "drop_stale", boom)
    assert webui._drop_stale_model_service() is False

