# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Снятие фона в сервисе моделей: сессия одна на модель, простой её выгружает.

Сервис поднимается В ПРОЦЕССЕ теста (слушатель в своём потоке, как в
`tests/test_model_service.py`), устройство — процессор (`REELSI_MODEL_SERVICE_DEVICE`),
видеокарта не трогается. Пакет rembg подменяется файлом (`REELSI_MODEL_SERVICE_SUBST_REMBG`):
он считает открытые сессии, а вырезка помечает картинку именем модели.
"""
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from core import model_service
from core.umsg import ReelsiError

# Подмена rembg: `OPENED` — имена моделей, под которые открыли сессию; вырезка — маркер.
# `MAX_INFLIGHT` — сколько вырезок шли разом (замок прогонов держит 1); `FAIL` — непустой
# список делает вырезку сбоем счёта (как переполнение арены).
_FAKE_REMBG = '''\
"""Подмена rembg для теста сервиса: считаем открытые сессии, вырезка помечает картинку."""
import threading as _threading
import time as _time
OPENED = []
FAIL = []
INFLIGHT = [0]
MAX_INFLIGHT = [0]
_LOCK = _threading.Lock()


class _Session:
    def __init__(self, name):
        self.name = name
        self.inner_session = None


def new_session(name="u2net", providers=None):
    OPENED.append(name)
    return _Session(name)


def remove(data, session=None):
    with _LOCK:
        INFLIGHT[0] += 1
        MAX_INFLIGHT[0] = max(MAX_INFLIGHT[0], INFLIGHT[0])
    try:
        _time.sleep(0.2)
        if FAIL:
            raise RuntimeError("BFCArena: Available memory is smaller than requested bytes")
        return b"PNG:" + session.name.encode("utf-8") + b":" + data
    finally:
        with _LOCK:
            INFLIGHT[0] -= 1
'''

# Живые сервисы ЭТОГО теста: фикстура гасит их на выходе, даже если тест упал.
_LIVE: list[Any] = []


@pytest.fixture
def rb_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Профиль сервиса: свой файл адреса, процессор и подменный rembg.

    `sys.modules["rembg"]` и `["onnxruntime"]` подменяются через monkeypatch: подмена
    сервиса их туда кладёт, а на выходе теста ключи уходят — чужие тесты не увидят
    заглушку вместо настоящего пакета (или его отсутствия).
    """
    settings = tmp_path / "job.lock.modelsvc.json"
    fake = tmp_path / "fake_rembg.py"
    fake.write_text(_FAKE_REMBG, encoding="utf-8")
    monkeypatch.setenv("REELSI_MODEL_SERVICE_DEVICE", "cpu")
    monkeypatch.setenv("REELSI_MODEL_SERVICE_SUBST_REMBG", str(fake))
    monkeypatch.setattr(model_service, "_settings_path", lambda: str(settings))
    monkeypatch.setitem(sys.modules, "rembg", None)          # ключ уйдёт на выходе теста
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    model_service.unload_models()
    try:
        yield {"tmp": tmp_path, "settings": settings, "fake": str(fake),
               "subst": model_service._exec_module(str(fake))}
    finally:
        for service in list(_LIVE):
            service.stop()
            service.wait_until_stopped(timeout=30)
        _LIVE.clear()
        model_service.unload_models()


def _serve(settings: Path, idle: float | None = None, slots: int = 1) -> Any:
    """Слушатель сервиса в СВОЁМ потоке и файл адреса к нему — как у боевого пути."""
    port = model_service._pick_port()
    key = secrets.token_bytes(32)
    service = model_service._Service(port, key, idle_s=idle, slots=slots)
    service.open_listener()             # слушатель ДО адреса — как боевой путь
    model_service._write_settings(str(settings), {
        "version": model_service.SETTINGS_VERSION, "host": "127.0.0.1",
        "port": port, "pid": os.getpid(),
        "created": model_service._process_create_time(os.getpid()),
        "authkey": key.hex(), "idle": service.idle_s,
        "build": model_service._code_fingerprint(),
        "device": model_service._service_device(),
    })
    service.start_thread()
    _LIVE.append(service)
    return service


def _join(service: Any) -> None:
    service.stop()
    service.wait_until_stopped(timeout=30)


def _wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def test_две_просьбы_фона_открывают_сессию_один_раз(rb_ws: dict[str, Any]) -> None:
    """Две картинки подряд: сессия модели открыта ОДИН раз, причина процессора — в первом ответе.

    Мутация: убрать кэш `_rembg_sessions` из `_loaded_rembg` — сессий станет две, и тест покраснеет.
    """
    service = _serve(rb_ws["settings"])
    try:
        first = model_service.remove_bg(b"A", "u2net")
        second = model_service.remove_bg(b"B", "u2net")
    finally:
        _join(service)

    assert first["png"] == b"PNG:u2net:A"
    assert second["png"] == b"PNG:u2net:B"
    assert rb_ws["subst"].OPENED == ["u2net"], "сессия открыта не один раз"
    assert first["gpu"] is False
    assert first["note"] == "настройка: процессор"
    assert second["note"] == "", "причина процессора — только на открытии, не на каждой картинке"


def test_unload_выгружает_фон_и_следующая_просьба_открывает_заново(rb_ws: dict[str, Any]) -> None:
    """Выгрузка (рендеру нужна карта целиком) чистит и сессии фона, а следующая просьба открывает её снова.

    Мутация: убрать `_rembg_sessions.clear()` из `unload_models` — сессия останется, и тест покраснеет.
    """
    service = _serve(rb_ws["settings"])
    try:
        model_service.remove_bg(b"A", "u2net")
        assert model_service.unload() is True
        assert model_service._rembg_sessions == {}, "unload обязан отдать и сессию фона"
        model_service.remove_bg(b"B", "u2net")
    finally:
        _join(service)

    assert rb_ws["subst"].OPENED == ["u2net", "u2net"]


def test_простой_фона_выгружает_сессию_сервис_жив(rb_ws: dict[str, Any],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Без просьб сессия фона выгружается по своему сроку, сервис при этом живёт и откроет её снова.

    Мутация: убрать `self._rembg_idle_unload()` из `_monitor_idle` — сессия не выгрузится, тест покраснеет.
    """
    monkeypatch.setenv("REELSI_MODEL_SERVICE_REMBG_IDLE", "0.3")
    service = _serve(rb_ws["settings"])
    try:
        model_service.remove_bg(b"A", "u2net")
        assert _wait_for(lambda: not model_service._rembg_sessions), "сессия фона не выгрузилась по простою"
        assert model_service.service_alive(), "выгрузка фона не должна гасить сервис"
        again = model_service.remove_bg(b"B", "u2net")
    finally:
        _join(service)

    assert again["png"] == b"PNG:u2net:B"
    assert rb_ws["subst"].OPENED == ["u2net", "u2net"]


def test_выгрузка_по_простою_не_трогает_просьбу_в_работе(rb_ws: dict[str, Any],
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока просьба стоит в очереди или вырезает, сессия не уходит из-под неё — даже при давнем простое."""
    monkeypatch.setenv("REELSI_MODEL_SERVICE_REMBG_IDLE", "0.01")
    service = model_service._Service(model_service._pick_port(), secrets.token_bytes(8),
                                     idle_s=None, slots=1)
    sentinel = (object(), False, "")
    model_service._rembg_sessions["u2net"] = sentinel
    service._rembg_last = 0.0                  # простой давно вышел — остановить может только работа
    service._rembg_busy = 1
    assert service._rembg_idle_unload() is False
    assert model_service._rembg_sessions.get("u2net") is sentinel
    service._rembg_busy = 0
    assert service._rembg_idle_unload() is True
    assert model_service._rembg_sessions == {}


def test_два_прогона_фона_идут_по_одному(rb_ws: dict[str, Any]) -> None:
    """Две просьбы разом при двух слотах: прогоны фона всё равно строго по одному.

    Слоты пускают просьбы параллельно, а арена одного прогона BiRefNet второму места не
    оставляет (замер на железе: обе падали). Замок прогонов (`_rembg_run_lock`) держит очерёдность.
    Мутация: убрать замок из `_rembg` — одновременных прогонов станет 2, тест покраснеет.
    """
    service = _serve(rb_ws["settings"], slots=2)
    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    def ask(img: bytes) -> None:
        try:
            results.append(model_service.remove_bg(img, "u2net"))
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=ask, args=(b"A",)),
               threading.Thread(target=ask, args=(b"B",))]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        _join(service)

    assert not errors, errors
    assert sorted(r["png"] for r in results) == [b"PNG:u2net:A", b"PNG:u2net:B"]
    assert rb_ws["subst"].MAX_INFLIGHT[0] == 1, "два прогона фона шли разом"


def test_сбой_счёта_выбрасывает_сессию_и_следующая_просьба_открывает_новую(rb_ws: dict[str, Any]) -> None:
    """Сбой прогона: ошибка наружу, сессия выброшена, следующая просьба открывает чистую арену.

    Мутация: убрать `_discard_rembg_sessions()` из `_rembg` — сессия останется, тест покраснеет.
    """
    subst = rb_ws["subst"]
    service = _serve(rb_ws["settings"])
    try:
        model_service.remove_bg(b"A", "u2net")
        subst.FAIL.append(1)
        with pytest.raises(ReelsiError):
            model_service.remove_bg(b"B", "u2net")
        assert model_service._rembg_sessions == {}, "после сбоя счёта сессия должна уйти"
        subst.FAIL.clear()
        again = model_service.remove_bg(b"C", "u2net")
    finally:
        _join(service)

    assert again["png"] == b"PNG:u2net:C"
    assert subst.OPENED == ["u2net", "u2net"], "после сбоя сессия открыта заново"


def test_отказ_сервиса_по_умолчанию_ServiceUnavailable_а_сырой_вызов_отдаёт_ответ(rb_ws: dict[str, Any]) -> None:
    """`call` по умолчанию превращает `ok: False` в `ServiceUnavailable` (как и раньше);
    с `raise_on_error=False` ответ отдаётся вызывающему как есть."""
    service = _serve(rb_ws["settings"])
    try:
        with pytest.raises(model_service.ServiceUnavailable):
            model_service.call("rembg", {"img": "не байты", "model": "u2net"})
        resp = model_service.call("rembg", {"img": "не байты", "model": "u2net"},
                                  raise_on_error=False)
    finally:
        _join(service)

    assert resp["ok"] is False and "img" in resp["error"]
