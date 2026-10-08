#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стенд замера GPU-части нарезки: «до» для сервиса моделей.

Что меряем. Сейчас каждый ролик нарезки — свой процесс (`api/jobs.py` Popen): свой
`import torch`, свой CUDA-контекст, GigaAM грузится внутри замка `.gpu` и выгружается
после ролика (`transcribe_words_whole`), вздохи — под тем же замком (`_cut_breaths`),
эмоции — своя загрузка GigaAM-emo на каждый расчёт (`load_emo_model`). Стенд гоняет K
таких детей ОДНОВРЕМЕННО и раскладывает время каждого на «ждал карту / грузил модели /
считал», плюс снимает пик занятой видеопамяти карты.

Что НЕ делаем: ИИ-шаги (LLM), сборку XML, рендер. Только GPU-часть: распознавание
(та же `transcribe_words_whole` под тем же `gpu_lock`), вздохи (`_cut_breaths`),
эмоции (`load_emo_model` + расчёт на распознанных словах). Код нарезки стенд не
трогает — зовёт существующие функции; время загрузки моделей внутри них видно потому,
что загрузчики обёрнуты таймером (`_trace_attr`), а не потому, что их код переписан.

Почему замок берут и эмоции. В проде `compute_emphasis` живёт на этапе сборки и
`gpu_lock` не берёт: сборка и нарезка не пересекаются (общий лок джоба). Здесь же K
детей идут разом, и без замка они подняли бы K моделей на одну карту. На Windows
переполнение VRAM не даёт честный OOM — оно вешает машину, поэтому эмфаза тоже под
замком: её ожидание видно отдельной строкой в таблице.

Ничего не качать: веса GigaAM, Silero VAD и CED проверяются ДО запуска, и если их нет
в кеше — явная ошибка с названием модели. Дети получают `HF_HUB_OFFLINE=1`.

Ребёнок получает путь звука СТРОКОЙ: у родителя `--wav` — `action="append"`, и список
путей, ушедший в боевой вызов, ронял распознавание (`TypeError: Invalid file: [...]`).
Упавший ребёнок отдаёт полный трейсбек, и родитель печатает его целиком.

Режим `--service` — замер «после»: распознавание идёт через сервис моделей
(`core/model_service`), как в боевой нарезке. Сервис грузит GigaAM ОДИН раз на все
ролики, дети веса распознавания не читают вовсе (слова приходят от сервиса), а
вместо файлового замка распознавание ограничено слотами сервиса (`--slots`, по
умолчанию — по свободной VRAM). Дети подключаются к своему сервису: файл адреса,
ключ и заявка на старт лежат рядом с `--lock` (одна и та же переменная
`REELSI_MODEL_SERVICE_STATE` у родителя, детей и сервиса), поэтому боевой сервис
машины замер не трогает. В таблице тогда видно «ожидание» (слоты) без строк загрузки
моделей у детей, а подъём сервиса показан один раз — отдельной строкой прогона.

Тот же режим уводит в сервис и ВЗДОХИ с ЭМОЦИЯМИ: CED-tiny и голову `emo` сервис
держит в памяти, поэтому дети не берут под них файловый замок (иначе замер мерил бы
ожидание замка вместо боя) и не читают веса заново. Родитель прогревает все три
источника весов до детей (`--service`), так что чтение моделей остаётся расходом
прогона, а не первого ролика.

Отличия сервисного режима от прежнего, и все — «как в бою». Подъём сервиса (процесс,
импорт torch, чтение весов) делает РОДИТЕЛЬ до детей: это расход ПРОГОНА, а не ролика —
сервис один на машину и живёт между роликами, в бою до пяти минут простоя. В таблице он
стоит отдельной строкой и в «общее время» не входит; раньше он попадал в «загрузку»
первого ролика, и при N=2 оба ролика ждали один и тот же старт. И файловый замок
`gpu_lock("распознавание")` в этом режиме НЕ берётся — боевой `words_for_cut` берёт его
только на запасном пути, а сервисный путь ограничен слотами; с замком ролики ждали бы
друг друга и замер мерил бы не бой.

Запуск::

    py -3.10 tools/bench_gpu_cut.py --src D:/clips/a.mp4 --sec 120 --n 4
    py -3.10 tools/bench_gpu_cut.py --src D:/clips/a.mp4 --sec 120 --n 2 --service
    py -3.10 tools/bench_gpu_cut.py --wav a.wav --wav b.wav --n 2 --dry   # без GPU
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from typing import IO, Any, Callable, Iterator, Protocol, Sequence

# Скрипт запускают и как `python tools/bench_gpu_cut.py`: тогда sys.path[0] — папка
# tools, и `import core` не найдётся. Корень репозитория кладём сами.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Префикс строки, которой ребёнок отдаёт свои метки. stdout детей несёт и служебный
# вывод чужих библиотек (torch, transformers), поэтому запись ищем по префиксу, а не
# «по последней строке».
CHILD_MARKER = "BENCH_GPU_CUT_JSON="

# Сколько последних строк вывода ребёнка печатать, если своей записи он не отдал
# (упал до обработчика или снят по таймауту) — по хвосту видно причину.
CHILD_TAIL_LINES = 40

# Ровно те ключи, которыми замер уводит сервис моделей к себе (см.
# `_service_env_vars`). Список нужен не для чтения, а чтобы вернуть окружение
# процесса как было: ключ, которого до замера НЕ было, после замера обязан
# исчезнуть, а не остаться с чужим путём.
_SERVICE_ENV_KEYS = (
    "REELSI_JOB_LOCK",
    "REELSI_MODEL_SERVICE_STATE",
    "REELSI_MODEL_SERVICE_SLOTS",
    "REELSI_MODEL_SERVICE_IDLE",
)

DEFAULT_SEC = 120.0
DEFAULT_EMO_WORDS = 20
DEFAULT_ASR_MODEL = "v3_ctc"
DEFAULT_TIMEOUT_S = 3600.0
VRAM_PERIOD_S = 0.5

# Длительности заглушек `--dry`: прогон должен быть быстрым, но метки — измеримыми.
DRY_IMPORT_S = 0.05
DRY_LOAD_S = 0.05
DRY_COMPUTE_S = 0.05

# Категории времени: импорт интерпретатором библиотек, ожидание замка, загрузка
# моделей, счёт. `всего` — время процесса целиком, и оно больше суммы четырёх.
_CATS = ("import", "wait", "load", "compute")

_Emit = Callable[..., Any]
_Words = list[dict[str, Any]]
_Keep = list[tuple[float, float]]


# --------------------------------------------------------------------------- #
# Вывод и метки времени
# --------------------------------------------------------------------------- #
def _force_utf8() -> None:
    """stdout/stderr в UTF-8: метки и таблица кириллические, а пайп на Windows —
    cp1252, и `sys.stdout.write` падал `UnicodeEncodeError` вместо записи."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # поток без поддержки кодировки (тестовый перехват) — как есть


def _stderr_emit(line: str = "", /, **vars: Any) -> None:
    """Вывод ребёнка — в stderr: stdout несёт ровно одну JSON-строку с метками.

    Понимает обе формы продуктового `emit`: `emit('текст')` и `emit('Шаблон {var}',
    var=…)`. Лишние подстановки (`flush=True` в вызовах нарезки) `str.format`
    игнорирует сам.
    """
    text = line
    if vars:
        try:
            text = line.format(**vars)
        except Exception:
            for k, v in vars.items():
                text = text.replace("{" + str(k) + "}", str(v))
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


class _Marks:
    """Метки времени одного ребёнка: секунды от старта его процесса.

    `events` — плоский список «что случилось, когда» (по нему видно порядок),
    `spans` — измеренные участки с категорией (по ним считается таблица).
    """

    def __init__(self, t0: float) -> None:
        self._t0 = t0
        self.events: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []
        self._open: dict[str, float] = {}

    def now(self) -> float:
        """Секунды от старта процесса (округление до миллисекунд)."""
        return round(time.monotonic() - self._t0, 3)

    def mark(self, name: str) -> None:
        """Одномоментная метка (порядок событий)."""
        self.events.append({"name": name, "t": self.now()})

    def begin(self, cat: str, name: str) -> None:
        """Начало измеряемого участка категории `cat`."""
        key = cat + ":" + name
        self._open[key] = time.monotonic()
        self.events.append({"name": cat + "_start", "what": name, "t": self.now()})

    def end(self, cat: str, name: str) -> None:
        """Конец участка: пишем спан и метку."""
        key = cat + ":" + name
        started = self._open.pop(key, None)
        t1 = time.monotonic()
        t0 = started if started is not None else t1
        self.spans.append({"cat": cat, "name": name,
                           "t0": round(t0 - self._t0, 3),
                           "t1": round(t1 - self._t0, 3)})
        self.events.append({"name": cat + "_end", "what": name, "t": self.now()})

    @contextlib.contextmanager
    def span(self, cat: str, name: str) -> Iterator[None]:
        """Участок категории `cat`: закрывается даже на исключении."""
        self.begin(cat, name)
        try:
            yield
        finally:
            self.end(cat, name)

    def wait(self, t_start: float, label: str) -> None:
        """Замок взят: ожидание от `t_start` до сейчас — участок категории wait."""
        t1 = time.monotonic()
        self.spans.append({"cat": "wait", "name": label,
                           "t0": round(t_start - self._t0, 3),
                           "t1": round(t1 - self._t0, 3)})
        self.mark("lock_acquired:" + label)


def _totals(spans: list[dict[str, Any]]) -> dict[str, float]:
    """Суммы по категориям. Из `compute` вычитаем загрузки, случившиеся ВНУТРИ него.

    GigaAM и модели вздохов грузятся изнутри той же функции, что и считает
    (`transcribe_words_whole`, `_cut_breaths`), а загрузчики обёрнуты таймером:
    без вычитания время модели попало бы и в «загрузку», и в «счёт».
    """
    out: dict[str, float] = {cat: 0.0 for cat in _CATS}
    loads = [s for s in spans if s.get("cat") == "load"]
    for sp in spans:
        cat = str(sp.get("cat"))
        dur = float(sp.get("t1", 0.0)) - float(sp.get("t0", 0.0))
        if cat == "compute":
            for load in loads:
                inside = (float(load["t0"]) >= float(sp["t0"])
                          and float(load["t1"]) <= float(sp["t1"]))
                if inside:
                    dur -= float(load["t1"]) - float(load["t0"])
        out[cat] = out.get(cat, 0.0) + max(0.0, dur)
    return out


# --------------------------------------------------------------------------- #
# Операции ребёнка: боевые и «сухие» (заглушки)
# --------------------------------------------------------------------------- #
class _Ops(Protocol):
    """Модельные шаги одного ролика. Боевые и заглушечные — взаимозаменяемы."""

    def prepare(self) -> None: ...

    def transcribe(self, wav: str, emit: _Emit) -> _Words: ...

    def cut_breaths(self, keep: _Keep, wav: str, words: _Words, out: str,
                    emit: _Emit) -> None: ...

    def load_emo(self) -> Any: ...

    def emotion(self, words: _Words, wav: str, limit: int, model: Any,
                emit: _Emit) -> None: ...

    def release_emo(self, model: Any) -> None: ...

    def vram_stats(self) -> tuple[int, int]: ...


def _trace_attr(obj: Any, name: str, marks: _Marks, label: str,
                key: int | None = None) -> None:
    """Обернуть загрузчик таймером: спан `load` вокруг настоящего вызова.

    Так время загрузки каждой модели видно, не трогая код нарезки: продуктовые
    функции зовут загрузчики по имени модуля, а имя мы подменили. `key` — номер
    позиционного аргумента с именем модели (у `gigaam.load_model` это голова).
    """
    real = getattr(obj, name)

    def traced(*args: Any, **kwargs: Any) -> Any:
        sub = label
        if key is not None and len(args) > key:
            sub = label + ":" + str(args[key])
        with marks.span("load", sub):
            return real(*args, **kwargs)

    setattr(obj, name, traced)


def _wav_path(wav: Any) -> str:
    """Заглушка принимает ровно то же, что и боевой вызов: путь строкой.

    Боевой `transcribe_words_whole` уходит в soundfile, а тот на списке путей падает
    `TypeError: Invalid file: [...]`. Без карты это иначе не поймать, поэтому проверку
    держим и в заглушке — она сторожит форму аргумента, а не только паузу.
    """
    if not isinstance(wav, str):
        raise TypeError("путь звука должен быть строкой, а не %s: %r"
                        % (type(wav).__name__, wav))
    return wav


def _service_models(args: argparse.Namespace) -> bool:
    """Вздохи и эмоции считает сервис — тот же признак, что у распознавания.

    `--dry` исключён нарочно: заглушкам сервис не нужен, и прогон обязан мерить тот
    же путь, что и раньше (с замками). Одна функция на всех: разойдись она у замка,
    распознавания и счёта — замер показал бы одно, а бой делал другое.
    """
    return bool(args.service) and not bool(args.dry)


class _DryOps:
    """Заглушки `--dry`: GPU и моделей нет, есть только такие же по форме паузы."""

    def __init__(self, marks: _Marks) -> None:
        self._marks = marks

    def prepare(self) -> None:
        with self._marks.span("import", "torch(dry)"):
            time.sleep(DRY_IMPORT_S)
        self._marks.mark("torch_ready")

    def transcribe(self, wav: str, emit: _Emit) -> _Words:
        _wav_path(wav)
        with self._marks.span("load", "gigaam:v3_ctc"):
            time.sleep(DRY_LOAD_S)
        return [{"w": "слово%d" % i, "start": float(i), "end": i + 0.6}
                for i in range(12)]

    def cut_breaths(self, keep: _Keep, wav: str, words: _Words, out: str,
                    emit: _Emit) -> None:
        with self._marks.span("load", "silero_vad"):
            time.sleep(DRY_LOAD_S)
        with self._marks.span("load", "ced"):
            time.sleep(DRY_LOAD_S)

    def load_emo(self) -> Any:
        with self._marks.span("load", "gigaam:emo"):
            time.sleep(DRY_LOAD_S)
        return object()

    def emotion(self, words: _Words, wav: str, limit: int, model: Any,
                emit: _Emit) -> None:
        time.sleep(DRY_COMPUTE_S)

    def release_emo(self, model: Any) -> None:
        return None

    def vram_stats(self) -> tuple[int, int]:
        return (0, 0)


class _RealOps:
    """Боевые шаги: те же функции нарезки, что и в проде, под теми же замками."""

    def __init__(self, args: argparse.Namespace, marks: _Marks) -> None:
        self._args = args
        self._marks = marks
        self._torch: Any = None
        # --service: слова просим у сервиса моделей, а не грузим GigaAM в ребёнке.
        self._svc: Any = None

    def prepare(self) -> None:
        """Импорт torch и подмена загрузчиков таймерами — ДО первого модельного шага.

        В `--service` общая часть тоже нужна, и не для красоты: вздохи и эмоции
        считает всё равно ребёнок, а их импорты и загрузки без этого попадали в
        «счёт» (замер: 6.6 с в строке «вздохи» против 0.7 с у процесса ролика —
        это был импорт torch и чтение CED, а не счёт). Распознавание при этом идёт
        через сервис: веса GigaAM ребёнок не читает вовсе.
        """
        from core import model_service
        self._svc = model_service
        if bool(self._args.service):
            # Холодный старт сервиса (если его ещё нет) — это тоже загрузка
            # моделей, и её надо видеть отдельной строкой, а не в «счёте».
            with self._marks.span("load", "gigaam:сервис"):
                ok = model_service.ensure_started()
                if ok:
                    # Веса головы — та же загрузка, что платит процесс ролика,
                    # только здесь её платит сервис и один раз на все ролики.
                    # Прогреваем ЗДЕСЬ: иначе чтение весов упало бы в «счёт»
                    # первого ролика, и сервисный режим выглядел бы медленнее
                    # процесса ровно на это время.
                    ok = model_service.preload(str(self._args.asr_model))
            if not ok:
                raise RuntimeError("сервис моделей не стартовал или не прочитал веса — "
                                   "запусти без --service")
            self._marks.mark("service_ready")
        with self._marks.span("import", "torch"):
            import torch
        self._marks.mark("torch_ready")
        if not bool(torch.cuda.is_available()):
            raise RuntimeError("torch не видит CUDA — стенд запускают на машине с "
                               "картой NVIDIA")
        torch.cuda.reset_peak_memory_stats()
        self._torch = torch
        with self._marks.span("import", "gigaam"):
            import gigaam
        with self._marks.span("import", "silero_vad"):
            import silero_vad
        with self._marks.span("import", "transformers"):
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        _trace_attr(gigaam, "load_model", self._marks, "gigaam", key=0)
        _trace_attr(silero_vad, "load_silero_vad", self._marks, "silero_vad")
        _trace_attr(AutoFeatureExtractor, "from_pretrained", self._marks, "ced:фронтенд")
        _trace_attr(AutoModelForAudioClassification, "from_pretrained", self._marks, "ced")

    def transcribe(self, wav: str, emit: _Emit) -> _Words:
        if self._svc is not None and bool(self._args.service):
            # Как в боевой нарезке: слова отдаёт сервис, torch в ребёнке не нужен.
            words = self._svc.transcribe(wav, str(self._args.asr_model))
            if not words:
                raise RuntimeError("сервис моделей не дал слов — смотри его вывод")
            return list(words)
        # Без сервиса — прежний путь: тот же `transcribe_words_whole`, что у
        # нарезки, с загрузкой весов под таймером (`_trace_attr`).
        from core.gigaam_cut.asr import transcribe_words_whole
        _full, words = transcribe_words_whole(wav, emit=emit,
                                              model_name=str(self._args.asr_model))
        return list(words)

    def cut_breaths(self, keep: _Keep, wav: str, words: _Words, out: str,
                    emit: _Emit) -> None:
        """Вздохи: в сервисном режиме CED считает сервис, локально — как раньше.

        Замок берёт `_child_main` (он же его и меряет) — здесь только выбор счёта:
        с `service=True` веса CED читает сервис, и `_cut_breaths` уходит туда.
        """
        from core.gigaam_cut.tune import _cut_breaths
        _cut_breaths(keep, None, wav, words, out, emit=emit,
                     service=_service_models(self._args))

    def load_emo(self) -> Any:
        if _service_models(self._args):
            return None                 # голову `emo` держит сервис — грузить нечего
        from core import emphasis
        # Именно ЛОКАЛЬНАЯ дверь: `load_emo_model` без сервиса сам его заводит
        # (`preload_emo`), и «замер без сервиса» мерил бы сервисный путь.
        return emphasis.load_emo_model_local()

    def emotion(self, words: _Words, wav: str, limit: int, model: Any,
                emit: _Emit) -> None:
        """Эмоция по окнам распознанных слов — как `compute_emphasis`, но без сайдкара.

        В сервисном режиме окно уезжает в сервис (`model_service.emotion_probs`), и
        модель этому процессу не нужна вовсе. Числа те же: сервис считает тем же
        `emphasis.emotion_probs`.
        """
        import soundfile as sf
        from core import emphasis
        audio, sr = sf.read(wav, dtype="float32")
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        rate = int(sr)
        served = _service_models(self._args)
        for word in words[:max(0, limit)]:
            window = emphasis._emo_window(audio, rate,
                                          (float(word["start"]), float(word["end"])), 0.0)
            if window is None:
                continue
            probs = (self._svc.emotion_probs(window, rate) if served
                     else emphasis.emotion_probs(window, model, rate))
            emphasis.emotion_score(probs)

    def release_emo(self, model: Any) -> None:
        if _service_models(self._args):
            return None                 # выгрузкой занят простой сервиса
        from core import emphasis
        emphasis.release_emo(model)

    def vram_stats(self) -> tuple[int, int]:
        """Пик памяти процесса в МиБ — в тех же единицах, что подпись в таблице.

        `torch.cuda.max_memory_allocated` отдаёт БАЙТЫ, а печаталось это под
        подписью «МиБ»: замер показывал 4000000000 вместо 4000. Переводим делением
        на 1024^2. В сервисном режиме torch в ребёнке есть (вздохи и эмоции
        считает он), но пик памяти РАСПОЗНАВАНИЯ живёт в процессе сервиса — его и
        спрашиваем, иначе в отчёте стояли бы нули, будто счёт ничего не занял.
        """
        if self._svc is not None and bool(self._args.service):
            stats = self._svc.vram_stats()
            return (int((stats or {}).get("max_memory_allocated", 0)),
                    int((stats or {}).get("max_memory_reserved", 0)))
        torch = self._torch
        if torch is None:
            return (0, 0)
        mib = 1024 * 1024
        allocated = int(torch.cuda.max_memory_allocated()) // mib
        reserved = getattr(torch.cuda, "max_memory_reserved", None)
        return (allocated, int(reserved()) // mib if callable(reserved) else 0)


# --------------------------------------------------------------------------- #
# Ребёнок: GPU-часть одного ролика и JSON-строка с метками
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _lock_stage(marks: _Marks, label: str) -> Iterator[None]:
    """Тот же `gpu_lock`, что у нарезки: ожидание меряем ДО входа в замок."""
    from core.gpulock import gpu_lock
    t_wait = time.monotonic()
    marks.mark("lock_wait:" + label)
    with gpu_lock(label, emit=_stderr_emit):
        marks.wait(t_wait, label)
        yield
    marks.mark("lock_release:" + label)


@contextlib.contextmanager
def _maybe_lock_stage(marks: _Marks, label: str, take: bool) -> Iterator[None]:
    """Замок участка — или ничего, когда работу делает сервис моделей.

    `take=False` — счёт ушёл в сервис, и замок только сериализовал бы детей: в бою
    его там нет (вздохи и эмоции через сервис идут без `gpu_lock`), и замер с
    замком мерил бы не то, что увидит человек.
    """
    if not take:
        yield
        return
    with _lock_stage(marks, label):
        yield


def _child_wav(value: Any) -> str:
    """Путь звука ребёнку — строкой.

    У родителя `--wav` объявлен `action="append"`, поэтому в ребёнке это СПИСОК путей.
    Раньше список уходил прямо в боевой `transcribe_words_whole` — и тот падал
    `TypeError: Invalid file: ['…bench_src0.wav']` уже на карте, в шаге распознавания.
    Ребёнку всегда дают ровно один вход, так что разворачиваем список к одному пути.
    """
    if isinstance(value, str):
        return value
    items = list(value)
    if len(items) != 1:
        raise ValueError("ребёнку нужен ровно один --wav, получено %d" % len(items))
    return _wav_path(items[0])


def _child_main(args: argparse.Namespace) -> int:
    """GPU-часть одного ролика без ИИ: распознавание, вздохи, эмоции."""
    _force_utf8()
    t0 = time.monotonic()
    marks = _Marks(t0)
    index = int(args.index)
    rec: dict[str, Any] = {"index": index, "pid": os.getpid(), "wav": None,
                           "dry": bool(args.dry), "asr_model": args.asr_model,
                           "marks": [], "spans": [], "totals": {}, "total_s": 0.0,
                           "vram": {}, "words": 0, "error": None, "traceback": None}
    try:
        os.makedirs(args.work, exist_ok=True)
        wav = _child_wav(args.wav)
        rec["wav"] = wav
        ops: _Ops = _DryOps(marks) if args.dry else _RealOps(args, marks)
        marks.mark("proc_start")
        ops.prepare()

        # 1) Распознавание — как в пайплайне. Локальный путь идёт под
        #    gpu_lock("распознавание"), а сервисный замок НЕ берёт: его заменяют
        #    слоты сервиса (`words_for_cut` ходит под `.gpu` только на запасном
        #    пути). Держать замок и в сервисном режиме значило бы мерить не бой:
        #    ролики ждали бы друг друга вместо того, чтобы считаться слотами.
        #    В `--dry` замок остаётся — заглушки карту не делят, и прогон должен
        #    мерить тот же путь, что и раньше.
        def _asr() -> _Words:
            with marks.span("compute", "asr"):
                return ops.transcribe(wav, _stderr_emit)

        if bool(args.dry) or not bool(args.service):
            with _lock_stage(marks, "распознавание"):
                words = _asr()
        else:
            words = _asr()
        if not words:
            raise RuntimeError("распознавание не дало ни одного слова — проверь --sec "
                               "и источник звука")
        marks.mark("asr_done")

        # 2) Вздохи — под gpu_lock("вздохи"), как в нарезке. Куски для детектора
        #    берём по распознанным словам: подгонка резов по звуку (refine_keep) сюда
        #    не входит — она не GPU, и мерить в ней нечего. В сервисном режиме CED
        #    считает сервис, и замок НЕ берётся: его заменяют слоты сервиса, ровно как
        #    у распознавания (боевой `_breath_stage` делает то же самое).
        keep: _Keep = [(float(words[0]["start"]), float(words[-1]["end"]))]
        out_xml = os.path.join(args.work, "bench_clip%d.xml" % index)
        with _maybe_lock_stage(marks, "вздохи", not _service_models(args)):
            with marks.span("compute", "breaths"):
                ops.cut_breaths(keep, wav, words, out_xml, _stderr_emit)
        marks.mark("breaths_done")

        # 3) Эмоции: голова `emo` (в сервисном режиме — его) и расчёт на словах
        #    (без LLM и сборки). В сервисном режиме замок тоже не нужен.
        with _maybe_lock_stage(marks, "эмоции", not _service_models(args)):
            model = ops.load_emo()
            try:
                with marks.span("compute", "emo"):
                    ops.emotion(words, wav, int(args.emo_words), model, _stderr_emit)
            finally:
                ops.release_emo(model)
        marks.mark("emo_done")

        allocated, reserved = ops.vram_stats()
        marks.mark("exit")
        rec.update({"marks": marks.events, "spans": marks.spans,
                    "totals": _totals(marks.spans), "total_s": marks.now(),
                    "vram": {"max_memory_allocated": allocated,
                             "max_memory_reserved": reserved},
                    "words": len(words)})
        code = 0
    except BaseException as ex:  # и SystemExit чужих вызовов: причина важнее тишины
        tb = traceback.format_exc()
        rec.update({"marks": marks.events, "spans": marks.spans,
                    "totals": _totals(marks.spans), "total_s": marks.now(),
                    "error": "%s: %s" % (type(ex).__name__, ex), "traceback": tb})
        # Трейсбек целиком — и в stderr, и в запись: родитель печатает его при падении
        # ребёнка, а по одной строке `error` причину в чужом стеке не найти.
        sys.stderr.write(tb)
        sys.stderr.flush()
        code = 2
    sys.stdout.write(CHILD_MARKER + json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


# --------------------------------------------------------------------------- #
# Родитель: подготовка звука, K детей разом, пик VRAM
# --------------------------------------------------------------------------- #
@dataclass
class _BenchResult:
    """Итог прогона: записи детей, стена, пик VRAM и напечатанная таблица."""

    records: list[dict[str, Any]]
    wall_s: float
    vram_peak_mib: int | None
    vram_samples: int
    dry: bool
    service: bool
    # Подъём сервиса моделей: расход ПРОГОНА (один раз на сервис), не ролика.
    # None — не в сервисном режиме; в таблицу идёт отдельной строкой.
    service_start_s: float | None
    table: str
    ok: bool


def _default_work() -> str:
    """Рабочая папка стенда по умолчанию — scratch в системном temp."""
    return os.path.join(tempfile.gettempdir(), "reelsi_gpubench")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bench_gpu_cut",
        description="Замер GPU-части нарезки: ожидание замка, загрузка моделей, счёт, VRAM.")
    parser.add_argument("--src", action="append", default=[], metavar="ВИДЕО",
                        help="видео, из которого взять первые --sec секунд звука "
                             "(можно несколько раз)")
    parser.add_argument("--wav", action="append", default=[], metavar="WAV",
                        help="готовый wav 16 кГц моно (можно несколько раз)")
    parser.add_argument("--sec", type=float, default=DEFAULT_SEC,
                        help="сколько секунд звука взять из --src (по умолчанию %(default)s)")
    parser.add_argument("--work", default=_default_work(), metavar="ПАПКА",
                        help="рабочая папка стенда (по умолчанию %(default)s)")
    parser.add_argument("--n", type=int, default=1,
                        help="сколько роликов гнать ОДНОВРЕМЕННО (по умолчанию %(default)s)")
    parser.add_argument("--dry", action="store_true",
                        help="без GPU: модели подменены заглушками (для теста)")
    parser.add_argument("--service", action="store_true",
                        help="распознавание — через сервис моделей (как в боевой "
                             "нарезке): GigaAM живёт в одном процессе, дети его не грузят")
    parser.add_argument("--slots", type=int, default=None,
                        help="сколько распознаваний разом у сервиса (по умолчанию — "
                             "по свободной VRAM); только с --service")
    parser.add_argument("--idle", type=float, default=None,
                        help="простой сервиса до завершения, сек (по умолчанию 300)")
    parser.add_argument("--emo-words", type=int, default=DEFAULT_EMO_WORDS,
                        help="на скольких первых словах считать эмоции (по умолчанию %(default)s)")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL,
                        help="голова GigaAM для распознавания (по умолчанию %(default)s)")
    parser.add_argument("--python", default=None,
                        help="интерпретатор для детей (по умолчанию: рабочий Python нарезки, "
                             "в --dry — текущий)")
    parser.add_argument("--lock", default=None, metavar="ФАЙЛ",
                        help="файл межпроцессного лока; gpu-лок встанет рядом (<файл>.gpu). "
                             "По умолчанию — боевой лок нарезки")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="потолок на весь прогон, сек (по умолчанию %(default)s)")
    parser.add_argument("--ffmpeg", default="ffmpeg",
                        help="чем извлекать звук из --src (по умолчанию %(default)s)")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--index", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _extract_wav(src: str, dst: str, sec: float, ffmpeg: str) -> None:
    """Первые `sec` секунд звука из видео — 16 кГц моно, как ждёт GigaAM."""
    cmd = [ffmpeg, "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
           "-t", f"{sec:.3f}", dst]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=max(120.0, sec * 10.0))
    except FileNotFoundError as ex:
        raise RuntimeError("ffmpeg не найден (%s): %s" % (ffmpeg, ex)) from ex
    if done.returncode != 0 or not os.path.isfile(dst):
        raise RuntimeError("ffmpeg не извлёк звук из %s: %s"
                           % (src, (done.stderr or "").strip()[:300]))


def _prepare_wavs(args: argparse.Namespace) -> list[str]:
    """Список wav для замера: готовые `--wav` плюс нарезанные из `--src`.

    Живые папки клипов не трогаем: звук ложится только в `--work`.
    """
    out: list[str] = []
    for wav in args.wav:
        path = os.path.abspath(str(wav))
        if not os.path.isfile(path):
            raise FileNotFoundError("нет wav: " + path)
        out.append(path)
    srcs = list(args.src)
    if srcs:
        work = os.path.abspath(str(args.work))
        os.makedirs(work, exist_ok=True)
        for i, src in enumerate(srcs):
            source = os.path.abspath(str(src))
            if not os.path.isfile(source):
                raise FileNotFoundError("нет видео: " + source)
            dst = os.path.join(work, "bench_src%d.wav" % i)
            _extract_wav(source, dst, float(args.sec), str(args.ffmpeg))
            out.append(dst)
    return out


def _preflight_models(args: argparse.Namespace) -> None:
    """Модели обязаны быть в кеше: стенд ничего не качает и падает явно."""
    import importlib.util

    from core.gigaam_cache import gigaam_dir

    cache = gigaam_dir()
    missing = [name for name in (str(args.asr_model), "emo")
               if not os.path.isfile(os.path.join(cache, name + ".ckpt"))]
    if missing:
        raise RuntimeError("нет весов GigaAM в кеше (качать запрещено): %s. Ждём в %s"
                           % (", ".join(missing), cache))
    if importlib.util.find_spec("silero_vad") is None:
        raise RuntimeError("нет пакета silero-vad — детектор вздохов не померить")
    import silero_vad

    onnx = os.path.join(os.path.dirname(silero_vad.__file__), "data", "silero_vad.onnx")
    if not os.path.isfile(onnx):
        raise RuntimeError("нет весов Silero VAD: " + onnx)
    if importlib.util.find_spec("transformers") is None:
        raise RuntimeError("нет пакета transformers — CED не померить")
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache",
                                                     "huggingface")
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(home, "hub")
    ced = os.path.join(hub, "models--mispeech--ced-tiny")
    if not os.path.isdir(ced):
        raise RuntimeError("нет весов CED (mispeech/ced-tiny) в кеше HuggingFace: " + ced)
    from core import breath

    ok, why = breath.available()
    if not ok:
        raise RuntimeError("детектор вздохов недоступен: " + str(why))


def _child_cmd(python: str, args: argparse.Namespace, index: int, wav: str) -> list[str]:
    """Команда ребёнка: тот же скрипт в режиме `--child`."""
    cmd = [python, os.path.abspath(__file__), "--child", "--index", str(index),
           "--wav", wav, "--work", os.path.abspath(str(args.work)),
           "--asr-model", str(args.asr_model), "--emo-words", str(args.emo_words),
           "--sec", str(args.sec)]
    if args.dry:
        cmd.append("--dry")
    if args.service:
        # Режим `--service` — свойство ЗАМЕРА, а не отдельный флаг ребёнка: сервис у
        # всех детей один, и каждый просит у него слова. Лок сервиса дети получают
        # переменной `REELSI_JOB_LOCK` (см. `_child_env`), а не аргументом.
        cmd.append("--service")
    return cmd


def _service_env_vars(args: argparse.Namespace) -> dict[str, str]:
    """Переменные окружения сервиса моделей — одни и те же у родителя и у детей.

    Файл адреса сервиса лежит рядом с локом (`REELSI_JOB_LOCK`), поэтому родитель
    обязан получить ТОТ ЖЕ лок, что и дети: иначе он поднял бы свой сервис в другом
    каталоге, а дети завели бы второй.

    Каталог состояния называем и ЯВНО — `REELSI_MODEL_SERVICE_STATE`, путём из
    ОДНОЙ функции ядра (`state_path_for`). Путь файла адреса читается при импорте
    ядра, и без явной переменной он зависел бы от того, кто раньше импортировал
    `core.jobstate`; с ней сервис, родитель и дети стенда идут одним путём, и
    адрес, ключ и заявка сервиса лежат ровно там, где их ищет клиент.
    """
    out: dict[str, str] = {}
    lock = ""
    if args.dry:
        # В --dry карту не занимаем и боевой замок не трогаем: у прогона свой.
        lock = os.path.join(os.path.abspath(str(args.work)), "job.dry.lock")
    elif args.lock:
        lock = os.path.abspath(str(args.lock))
    if lock:
        from core import model_service
        out["REELSI_JOB_LOCK"] = lock
        out["REELSI_MODEL_SERVICE_STATE"] = model_service.state_path_for(lock)
    if args.service:
        # Порт и ключ сервиса лягут рядом с ЭТИМ локом: у замера свой сервис, и
        # боевой (если он есть) не трогаем — как у изолированного профиля.
        if args.slots:
            out["REELSI_MODEL_SERVICE_SLOTS"] = str(int(args.slots))
        if args.idle:
            out["REELSI_MODEL_SERVICE_IDLE"] = str(float(args.idle))
    return out


@contextlib.contextmanager
def _service_env(args: argparse.Namespace) -> Iterator[None]:
    """Окружение сервиса — на время замера и с возвратом прежнего.

    `run_bench` зовут В ПРОЦЕССЕ (тесты), и запись в `os.environ` без возврата
    оставляла чужому коду в том же процессе лок и каталог сервиса замера: тесты
    сервиса, шедшие следом в воркере, читали адрес там, где их сервис его не
    писал, и «подменный модуль GigaAM ещё не загружен» падало на ровном месте.
    Локально это не воспроизводилось: тесты по воркерам `-n auto` раскладывались
    иначе. Отсюда запоминаем и возвращаем РОВНО те ключи, что ставит замер
    (`_SERVICE_ENV_KEYS`): чужие переменные процесса не трогаем вовсе.
    """
    prev = {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS}
    os.environ.update(_service_env_vars(args))
    try:
        yield
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)   # ключа до замера не было — не создаём
            else:
                os.environ[key] = value     # был — вернуть ровно прежнее значение


def _child_env(args: argparse.Namespace) -> dict[str, str]:
    """Окружение детей: корень в PYTHONPATH, свой лок, запрет на скачивание весов."""
    env = dict(os.environ)
    parts = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    if _ROOT not in parts:
        parts.insert(0, _ROOT)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(_service_env_vars(args))
    if not args.dry:
        # Ничего не качать: нет весов — from_pretrained падает с именем модели,
        # а не тянет гигабайты из сети на живой машине.
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def _start_service(args: argparse.Namespace) -> float | None:
    """Поднять сервис моделей до детей; сколько это заняло — или None, если не вышло.

    Подъём сервиса — расход ПРОГОНА, а не ролика: процесс сервиса один на машину и
    живёт между роликами (в бою — до пяти минут простоя), а веса он читает один раз
    на всех. Раньше этот подъём попадал в «загрузку» первого ролика: в N=2 оба
    ролика ждали один и тот же старт, и сервисный режим выглядел медленнее процесса
    ровно на него. Теперь он виден отдельной строкой прогона и в «общее время» не
    входит — как и остальная подготовка родителя (звук, проверка весов).

    Прогреваем ВСЕ три источника весов, которыми пользуется замер: голову
    распознавания, CED вздохов и голову `emo`. Иначе чтение модели попало бы в
    «счёт» первого ролика — ровно та ошибка, из-за которой сервисный режим и
    выглядел хуже процесса на ровном месте.
    """
    if not bool(args.service) or bool(args.dry):
        return None
    from core import model_service
    started = time.monotonic()
    if not model_service.ensure_started():
        return None
    for loaded in (model_service.preload(str(args.asr_model)),
                   model_service.preload_breath(),
                   model_service.preload_emo()):
        if not loaded:
            return None
    return time.monotonic() - started


def _read_lines(stream: IO[str] | None, sink: list[str]) -> None:
    """Поток чтения stdout одного ребёнка: строки кладём в его список."""
    if stream is None:
        return
    try:
        for line in stream:
            sink.append(line.rstrip("\r\n"))
    except Exception:
        pass  # поток оборвался (процесс снят) — читать больше нечего


def _parse_record(lines: list[str]) -> dict[str, Any] | None:
    """Последняя строка-запись ребёнка из его вывода (или None)."""
    for line in reversed(lines):
        if line.startswith(CHILD_MARKER):
            try:
                data = json.loads(line[len(CHILD_MARKER):])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _nvidia_memory_used() -> int | None:
    """Занятая видеопамять карты, МиБ (None — нет nvidia-smi или он не ответил)."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    values = [int(x.strip()) for x in (done.stdout or "").splitlines() if x.strip().isdigit()]
    return max(values) if values else None


class _VramSampler:
    """Пик занятой видеопамяти: `nvidia-smi` раз в период, в своём потоке."""

    def __init__(self, period: float = VRAM_PERIOD_S) -> None:
        self._period = period
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_mib: int | None = None
        self.samples = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            used = _nvidia_memory_used()
            if used is not None:
                self.samples += 1
                if self.peak_mib is None or used > self.peak_mib:
                    self.peak_mib = used
            self._stop.wait(self._period)


def _median(values: list[float]) -> float:
    """Медиана (пустой список — 0.0: строк в таблице не меньше, чем роликов)."""
    return float(statistics.median(values)) if values else 0.0


def _maximum(values: list[float]) -> float:
    """Максимум (пустой список — 0.0)."""
    return max(values) if values else 0.0


def _format_table(result: _BenchResult) -> str:
    """Таблица по роликам и сводно + общее время, пик VRAM и загрузка по моделям."""
    out: list[str] = []
    mode = "dry (модели подменены)" if result.dry else "боевой (GPU)"
    out.append("== стенд GPU-части нарезки: %d роликов, режим %s =="
               % (len(result.records), mode))
    out.append("%8s  %9s  %9s  %9s  %9s"
               % ("ролик", "ожидание", "загрузка", "счёт", "всего"))
    for rec in result.records:
        totals = rec.get("totals") or {}
        out.append("%8d  %9.2f  %9.2f  %9.2f  %9.2f"
                   % (int(rec.get("index", 0)), float(totals.get("wait", 0.0)),
                      float(totals.get("load", 0.0)), float(totals.get("compute", 0.0)),
                      float(rec.get("total_s", 0.0))))
    for label, func in (("медиана", _median), ("максимум", _maximum)):
        out.append("%8s  %9.2f  %9.2f  %9.2f  %9.2f"
                   % (label,
                      func([float((r.get("totals") or {}).get("wait", 0.0))
                            for r in result.records]),
                      func([float((r.get("totals") or {}).get("load", 0.0))
                            for r in result.records]),
                      func([float((r.get("totals") or {}).get("compute", 0.0))
                            for r in result.records]),
                      func([float(r.get("total_s", 0.0)) for r in result.records])))
    out.append("")
    out.append("общее время %d роликов: %.2f с" % (len(result.records), result.wall_s))
    if result.service_start_s is not None:
        out.append("подъём сервиса моделей (один раз на прогон, в «общее время» "
                   "не входит): %.2f с" % result.service_start_s)
    if result.dry:
        out.append("пик VRAM карты: не снимался (--dry)")
    elif result.vram_peak_mib is None:
        out.append("пик VRAM карты: не снят (nvidia-smi не ответил)")
    else:
        out.append("пик VRAM карты: %d МиБ (nvidia-smi, %d замеров)"
                   % (result.vram_peak_mib, result.vram_samples))
    allocated = [int((r.get("vram") or {}).get("max_memory_allocated", 0))
                 for r in result.records]
    reserved = [int((r.get("vram") or {}).get("max_memory_reserved", 0))
                for r in result.records]
    # В сервисном режиме счётчик снят у СЕРВИСА (у детей torch нет вовсе) —
    # подпись говорит, чей это процесс, иначе цифра читается как «память ролика».
    whose = "в процессе сервиса" if result.service else "по процессам"
    out.append("%s: torch.cuda.max_memory_allocated макс %d МиБ, "
               "max_memory_reserved макс %d МиБ"
               % (whose, max(allocated) if allocated else 0,
                  max(reserved) if reserved else 0))
    by_model: dict[str, list[float]] = {}
    for rec in result.records:
        for span in (rec.get("spans") or []):
            if span.get("cat") == "load":
                name = str(span.get("name"))
                by_model.setdefault(name, []).append(
                    float(span.get("t1", 0.0)) - float(span.get("t0", 0.0)))
    if by_model:
        out.append("")
        out.append("загрузка моделей (медиана по роликам), с:")
        for name in sorted(by_model):
            out.append("  %-24s %.2f" % (name, _median(by_model[name])))
    return "\n".join(out)


def _child_dump(rec: dict[str, Any] | None, collected: list[str]) -> str:
    """Полный трейсбек упавшего ребёнка: из его записи, иначе — хвост его вывода.

    По одной строке `error` причину в чужом стеке не найти: ребёнок отдаёт трейсбек
    целиком, и родитель печатает его как есть.
    """
    if rec is not None:
        tb = str(rec.get("traceback") or "").strip()
        if tb:
            return tb
    return "\n".join(collected[-CHILD_TAIL_LINES:]).strip()


def _parent_run(args: argparse.Namespace) -> _BenchResult:
    """Родитель: звук, K детей разом, снятие VRAM, таблица.

    Окружение сервиса ставит вызывающий (`run_bench`) — на время замера и с
    возвратом прежнего: переменные обязаны быть видны и ядру, и детям, но НЕ
    обязаны переживать сам вызов.
    """
    _force_utf8()
    if int(args.n) < 1:
        raise ValueError("--n должно быть >= 1")
    wavs = _prepare_wavs(args)
    if not wavs:
        raise ValueError("нечего мерить: задай --src (видео) или --wav (готовый звук)")
    if not args.dry:
        _preflight_models(args)
    service_start_s = _start_service(args)
    if args.service and service_start_s is None:
        raise RuntimeError("сервис моделей не поднялся — запусти без --service")
    if args.python:
        python = str(args.python)
    elif args.dry:
        # Заглушкам torch не нужен: берём тот же интерпретатор, что и у теста.
        python = sys.executable
    else:
        from core.app_meta import py_exec
        python = py_exec()
    os.makedirs(os.path.abspath(str(args.work)), exist_ok=True)
    env = _child_env(args)
    procs: list[subprocess.Popen[str]] = []
    lines: list[list[str]] = []
    readers: list[threading.Thread] = []
    sampler = _VramSampler()
    started = time.monotonic()
    if not args.dry:
        sampler.start()
    try:
        for index in range(int(args.n)):
            wav = wavs[index % len(wavs)]
            proc = subprocess.Popen(_child_cmd(python, args, index, wav), cwd=_ROOT,
                                    env=env, stdout=subprocess.PIPE, stderr=None,
                                    text=True, encoding="utf-8", errors="replace",
                                    bufsize=1)
            procs.append(proc)
            lines.append([])
            reader = threading.Thread(target=_read_lines,
                                      args=(proc.stdout, lines[index]), daemon=True)
            reader.start()
            readers.append(reader)
        deadline = started + float(args.timeout)
        while any(proc.poll() is None for proc in procs):
            if time.monotonic() > deadline:
                for proc in procs:
                    if proc.poll() is None:
                        try:
                            proc.kill()
                        except Exception:
                            pass  # процесс уже умер — убивать нечего
                raise RuntimeError("дети не уложились в --timeout %.0f с — сняты"
                                   % float(args.timeout))
            time.sleep(0.1)
        wall_s = time.monotonic() - started
    finally:
        sampler.stop()
        for reader in readers:
            reader.join(timeout=10.0)
    records: list[dict[str, Any]] = []
    for index, collected in enumerate(lines):
        rec = _parse_record(collected)
        if rec is None:
            raise RuntimeError("ребёнок %d не отдал запись (код %s):\n%s"
                               % (index, procs[index].returncode,
                                  _child_dump(rec, collected)))
        if rec.get("error"):
            raise RuntimeError("ребёнок %d упал: %s\n%s"
                               % (index, rec["error"], _child_dump(rec, collected)))
        records.append(rec)
    result = _BenchResult(records=records, wall_s=wall_s, vram_peak_mib=sampler.peak_mib,
                          vram_samples=sampler.samples, dry=bool(args.dry),
                          service=bool(args.service), service_start_s=service_start_s,
                          table="", ok=True)
    result.table = _format_table(result)
    sys.stdout.write(result.table + "\n")
    sys.stdout.flush()
    return result


def run_bench(argv: Sequence[str]) -> _BenchResult:
    """Прогнать стенд по аргументам и напечатать таблицу (для тестов и вызовов из кода).

    Окружение сервиса ставится на время прогона: лок и каталог состояния считает
    одна функция ядра (`state_path_for`), и ОДНА И ТА ЖЕ переменная уходит и
    родителю, и детям — иначе путь сервиса зависел бы от того, кто раньше
    импортировал `core.jobstate`. По выходу `os.environ` возвращается как был:
    вызывающий живёт в своём процессе и чужого лока с каталогом сервиса видеть не
    должен. У CLI свой процесс, и там возврат не виден никому.
    """
    args = _parse_args(list(argv))
    with _service_env(args):
        return _parent_run(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: `--child` — режим ребёнка, иначе родитель."""
    _force_utf8()
    args = _parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    if args.child:
        return _child_main(args)
    try:
        result = _parent_run(args)
    except Exception as ex:
        sys.stderr.write("стенд упал: %s: %s\n" % (type(ex).__name__, ex))
        sys.stderr.flush()
        return 1
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
