# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тест стенда GPU-части нарезки: dry-прогон K=2 на синтетическом wav.

Настоящую карту тест не трогает: `--dry` подменяет модели заглушками, а межпроцессный
замок прогона уводится в рабочую папку стенда (боевой `job.lock` не занимается).
Проверяем ровно то, за чем стенд и нужен: родитель поднял двух детей, собрал их
JSON-строки, метки времени идут по возрастанию, а таблица напечатана.

Отдельно сторожим вход ребёнка: путь звука доходит до боевого вызова СТРОКОЙ (у
родителя `--wav` — `action="append"`, и список путей ронял распознавание на карте),
а на падении ребёнок отдаёт полный трейсбек, а не одну строку с типом ошибки.

Запуск:  python -m pytest tests/test_bench_gpu_cut.py -q
"""
from __future__ import annotations

import os
import sys
import wave
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bench_gpu_cut import (  # noqa: E402
    _SERVICE_ENV_KEYS,
    _child_main,
    _parse_args,
    _parse_record,
    run_bench,
)


def _silent_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    """16 кГц моно: ровно то, что стенд ждёт на входе."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * int(rate * seconds) * 2)
    return path


def test_dry_k2_печатает_таблицу_и_собирает_строки_детей(tmp_path, capsys):
    """`--dry --n 2`: таблица, порядок меток, две записи от двух РАЗНЫХ процессов."""
    first = _silent_wav(tmp_path / "a.wav")
    second = _silent_wav(tmp_path / "b.wav")
    work = tmp_path / "work"

    result = run_bench(["--dry", "--n", "2", "--wav", str(first), "--wav", str(second),
                        "--work", str(work)])

    printed = capsys.readouterr().out
    assert result.ok
    # Таблица напечатана целиком: шапка, обе строки роликов, сводка и итог.
    for word in ("ролик", "ожидание", "загрузка", "счёт", "всего"):
        assert word in printed, word
    assert "медиана" in printed and "максимум" in printed
    assert "общее время 2 роликов" in printed
    assert result.table in printed
    # Родитель собрал строки ОБОИХ детей — и это разные процессы.
    assert len(result.records) == 2
    assert sorted(int(rec["index"]) for rec in result.records) == [0, 1]
    assert len({int(rec["pid"]) for rec in result.records}) == 2
    assert result.wall_s > 0.0


def test_dry_метки_упорядочены_и_фазы_посчитаны(tmp_path):
    """Метки не убывают, спаны неотрицательны, загрузка/счёт/ожидание не пустые."""
    wav = _silent_wav(tmp_path / "one.wav")
    result = run_bench(["--dry", "--n", "1", "--wav", str(wav),
                        "--work", str(tmp_path / "work")])

    record = result.records[0]
    assert record["error"] is None
    assert record["words"] > 0
    times = [float(mark["t"]) for mark in record["marks"]]
    assert times == sorted(times), times
    assert record["marks"][0]["name"] == "proc_start"
    assert record["marks"][-1]["name"] == "exit"
    names = [mark["name"] for mark in record["marks"]]
    for stage in ("распознавание", "вздохи", "эмоции"):
        assert "lock_wait:" + stage in names, names
        assert "lock_acquired:" + stage in names, names
    for span in record["spans"]:
        assert float(span["t1"]) >= float(span["t0"]), span
    totals = record["totals"]
    assert totals["load"] > 0.0 and totals["compute"] > 0.0
    assert totals["import"] > 0.0
    # Загрузки, случившиеся внутри счёта, из счёта вычтены: сумма фаз не больше всего.
    assert totals["load"] + totals["compute"] + totals["wait"] <= record["total_s"] + 0.05


def test_dry_путь_звука_ребёнку_строкой(tmp_path):
    """`--dry --n 2`: до ребёнка доходит путь СТРОКОЙ, а не список из `action="append"`.

    Список, ушедший в боевое распознавание, роняет ребёнка на карте —
    `TypeError: Invalid file: ['…wav']`; заглушка проверяет ту же форму аргумента,
    поэтому ошибка видна и без GPU.
    """
    wav = _silent_wav(tmp_path / "a.wav")

    result = run_bench(["--dry", "--n", "2", "--wav", str(wav),
                        "--work", str(tmp_path / "work")])

    assert result.ok
    assert len(result.records) == 2
    for record in result.records:
        assert record["error"] is None, record["error"]
        assert isinstance(record["wav"], str), record["wav"]
        assert record["wav"] == str(wav)


@pytest.mark.parametrize("before", [True, False], ids=["ключи_были", "ключей_не_было"])
def test_стенд_возвращает_окружение_процесса(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: bool
) -> None:
    """`run_bench(["--dry", …])` возвращает `os.environ` как было — по ВСЕМ ключам сервиса.

    Мутация: убрать возврат (оставить `os.environ.update` насовсем) — тест
    краснеет. Именно так текло в CI: `run_bench(["--dry", …])` шёл в процессе
    pytest, и следом тесты сервиса в том же воркере читали чужой лок и чужой
    каталог состояния («подменный модуль GigaAM ещё не загружен»), а
    `_child_env` видел в `os.environ` переменную от прошлого теста.

    Проверяем ОБА случая: ключ был (вернуть ровно прежнее значение, а не снять) и
    ключа не было (снять, а не оставить чужой путь). Значения-отметки чужие
    нарочно: совпади они со свежими — тест прошёл бы и без возврата.
    """
    # Свой звук: без него замер падает раньше, чем доходит до окружения.
    wav = _silent_wav(tmp_path / "a.wav")
    marks = {key: os.path.join(str(tmp_path), "чужой-" + key) for key in _SERVICE_ENV_KEYS}
    for key, value in marks.items():
        if before:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    env_before = {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS}

    result = run_bench(["--dry", "--n", "1", "--wav", str(wav),
                        "--work", str(tmp_path / "work")])

    assert result.ok
    assert {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS} == env_before, (
        "замер оставил в окружении процесса свои переменные сервиса — "
        "следующий тест в этом воркере пойдёт в чужой сервис"
    )


@pytest.mark.parametrize("before", [True, False], ids=["ключи_были", "ключей_не_было"])
def test_окружение_сервиса_возвращается_и_со_слотами(
    monkeypatch: pytest.MonkeyPatch, before: bool
) -> None:
    """`_service_env` возвращает и слоты с простоем — ключи, которых `--dry` сам не ставит.

    Слоты и простой замер ставит только в сервисном режиме (`--service`), а тот без
    карты не поднять: подменяем саму выдачу переменных и сторожим РАЗНИЦУ «до/после»
    по всем четырём ключам. Мутация: возвращать только лок и каталог состояния —
    `REELSI_MODEL_SERVICE_SLOTS` остался бы с чужим потолком.
    """
    from tools import bench_gpu_cut as bench

    fresh = {key: "свой-" + key for key in _SERVICE_ENV_KEYS}
    for key in _SERVICE_ENV_KEYS:
        if before:
            monkeypatch.setenv(key, "чужой-" + key)
        else:
            monkeypatch.delenv(key, raising=False)
    env_before = {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS}
    monkeypatch.setattr(bench, "_service_env_vars", lambda args: dict(fresh))

    args = bench._parse_args(["--wav", "a.wav", "--n", "1", "--service",
                              "--slots", "2", "--idle", "30", "--lock", "C:/tmp/bench.lock"])
    with bench._service_env(args):
        assert {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS} == fresh, (
            "на время замера окружение обязано быть сервисным"
        )

    assert {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS} == env_before, (
        "после замера окружение процесса обязано вернуться как было"
    )


def test_стенд_дети_и_родитель_видят_один_лок_сервиса(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пока идёт замер, дети получают ТОТ ЖЕ лок и каталог сервиса, что родитель.

    Возврат окружения не должен развести родителя и детей: лок и каталог состояния
    считает одна функция ядра, и одна и та же переменная обязана уйти обоим —
    иначе родитель поднял бы сервис в одном каталоге, а дети завели бы второй.
    Проверяем на САМОМ вызове: перехватываем `env`, с которым стартует ребёнок, и
    сверяем с `os.environ` родителя В ЭТОТ момент (а не после возврата).
    """
    import subprocess

    wav = _silent_wav(tmp_path / "a.wav")
    seen: list[tuple[dict[str, str], dict[str, str | None]]] = []
    real_popen = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        env = kwargs.get("env")
        if isinstance(env, dict):
            # Окружение родителя В ЭТОТ момент, а не после возврата: возврат
            # окружения и после него обязан быть чистым — это проверяет соседний
            # тест, а здесь важен миг старта ребёнка.
            own = {key: os.environ.get(key) for key in _SERVICE_ENV_KEYS}
            seen.append((dict(env), own))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)

    result = run_bench(["--dry", "--n", "1", "--wav", str(wav),
                        "--work", str(tmp_path / "work")])

    assert result.ok
    assert len(seen) == 1, "ребёнок не стартовал — проверять нечего"
    child, own = seen[0]
    # Лок прогона — свой, в рабочей папке стенда: `--dry` боевой замок не трогает.
    assert child.get("REELSI_JOB_LOCK") == os.path.join(
        str(tmp_path / "work"), "job.dry.lock"), child
    # Во время замера родитель видит РОВНО то же, что уходит детям.
    for key in _SERVICE_ENV_KEYS:
        assert child.get(key) == own[key], (
            "ребёнок и родитель разошлись по %s: родитель поднял бы сервис в одном "
            "каталоге, а дети завели бы второй" % key
        )


def test_падение_ребёнка_отдаёт_полный_трейсбек(tmp_path, capsys):
    """Ребёнок отдаёт трейсбек целиком — и в запись, и в stderr: по одной строке
    `error` причину в чужом стеке не найти, а родитель печатает его как есть."""
    first = _silent_wav(tmp_path / "a.wav")
    second = _silent_wav(tmp_path / "b.wav")
    # Два входа на одного ребёнка — заведомо ложный вызов: нужен ровно один путь.
    args = _parse_args(["--child", "--dry", "--index", "0",
                        "--wav", str(first), "--wav", str(second),
                        "--work", str(tmp_path / "work")])

    code = _child_main(args)

    captured = capsys.readouterr()
    record = _parse_record(captured.out.splitlines())
    assert code == 2
    assert record is not None
    assert str(record["error"]).startswith("ValueError")
    trace = str(record["traceback"])
    assert "Traceback (most recent call last)" in trace
    assert "ValueError" in trace and "--wav" in trace
    assert "Traceback (most recent call last)" in captured.err
