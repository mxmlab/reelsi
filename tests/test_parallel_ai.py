# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты параллельных вызовов ИИ, реестра вызовов, лимитов concurrency и выгрузки."""
from __future__ import annotations

import json
import threading
import time
from typing import Any
import urllib.error

import pytest

import api
import api._core as _core
import api.ai as ai_route
from core import aicut
from core.aicut import config as ai_config
from core.aicut import config_actions as ai_actions
from core.aicut import llm as ai_llm
from core.umsg import ReelsiError

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client() -> Any:
    """Тестовый клиент Flask с зарегистрированным blueprint api."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_registry_batch_and_single_cancellation() -> None:
    """1. Реестр: два begin_call("b1") -> оба is_current; затем одиночный begin_call() ->
    оба прежних отменены; begin_call("b2") отменяет живые вызовы b1."""
    try:
        ep1 = aicut.begin_call("b1")
        ep2 = aicut.begin_call("b1")
        assert aicut.is_current(ep1)
        assert aicut.is_current(ep2)

        # Одиночный вызов отменяет все предыдущие вызовы
        ep3 = aicut.begin_call()
        assert not aicut.is_current(ep1)
        assert not aicut.is_current(ep2)
        assert aicut.is_current(ep3)

        aicut.end_call(ep3)

        # Вызовы новой пачки b2 отменяют вызовы пачки b1
        ep4 = aicut.begin_call("b1")
        assert aicut.is_current(ep4)
        ep5 = aicut.begin_call("b2")
        assert not aicut.is_current(ep4)
        assert aicut.is_current(ep5)
    finally:
        with ai_llm._EPOCH_LOCK:
            ai_llm._LIVE.clear()
            ai_llm._DEAD.clear()
            ai_llm.CANCEL = False


def test_cancel_call_and_idle_since_regression() -> None:
    """2. cancel_call() отменяет всех; idle_since(stamp) -> True сразу после «Стопа»
    и False, если после него стартовал новый вызов. Регрессия на попутный баг."""
    try:
        ep1 = aicut.begin_call("b1")
        ep2 = aicut.begin_call("b1")
        stamp = aicut.cancel_call()

        assert aicut.cancelled()
        assert not aicut.is_current(ep1)
        assert not aicut.is_current(ep2)
        assert aicut.idle_since(stamp) is True

        # Стартовал новый вызов после момента отмены
        ep3 = aicut.begin_call("b2")
        assert aicut.idle_since(stamp) is False
        assert aicut.is_current(ep3)
    finally:
        with ai_llm._EPOCH_LOCK:
            ai_llm._LIVE.clear()
            ai_llm._DEAD.clear()
            ai_llm.CANCEL = False


def test_ai_stop_route_calls_unload_ours(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """3. Роут /api/ai_stop: после него unload_ours вызван, если нового вызова не было."""
    unloaded_event = threading.Event()

    monkeypatch.setattr(aicut, "unload_ours", lambda **kw: unloaded_event.set())

    r = client.post("/api/ai_stop", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}

    assert unloaded_event.wait(timeout=2.0) is True


def test_ai_end_batch_unloads_only_last_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """4. _ai_end: два вызова одной пачки, оба с unload=True; после первого unload_ours
    не вызван, после второго — вызван ровно один раз."""
    unloaded: list[bool] = []
    monkeypatch.setattr(aicut, "unload_ours", lambda **kw: unloaded.append(True))

    try:
        ep1 = aicut.begin_call("batch_test")
        ep2 = aicut.begin_call("batch_test")

        with _core.LOCK:
            _core.AI_ACTIVE = 2
            _core.AI_ACTIVE_BY["batch_test"] = 2
            _core.AI_BATCH_BY_EP[ep1] = "batch_test"
            _core.AI_BATCH_BY_EP[ep2] = "batch_test"

        _core._ai_end(ep1, unload=True)
        assert len(unloaded) == 0

        _core._ai_end(ep2, unload=True)
        assert len(unloaded) == 1
    finally:
        with _core.LOCK:
            _core.AI_ACTIVE = 0
            _core.AI_ACTIVE_BY.clear()
            _core.AI_BATCH_BY_EP.clear()
        with ai_llm._EPOCH_LOCK:
            ai_llm._LIVE.clear()
            ai_llm._DEAD.clear()
            ai_llm.CANCEL = False


def test_batch_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """5. Лимит пачки: step_concurrency подменён на 2, пять потоков одной пачки внутри
    _ai_begin..._ai_end (работа — sleep(0.2)) -> одновременно внутри не больше 2, все 5 прошли."""
    monkeypatch.setattr(aicut, "step_concurrency", lambda step: 2)

    cur_active = 0
    max_active = 0
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker() -> None:
        nonlocal cur_active, max_active
        try:
            ep = _core._ai_begin("test_worker", batch="limit_batch", step="yellow")
            try:
                with lock:
                    cur_active += 1
                    if cur_active > max_active:
                        max_active = cur_active
                time.sleep(0.2)
            finally:
                with lock:
                    cur_active -= 1
                _core._ai_end(ep)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors
    assert max_active == 2
    assert _core.AI_ACTIVE == 0


def test_queue_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """6. Отмена в очереди: лимит 1, второй поток ждёт слота, cancel_call() ->
    второй получает ReelsiError, счётчики вернулись к 0."""
    monkeypatch.setattr(aicut, "step_concurrency", lambda step: 1)

    thread2_error: list[Exception] = []
    thread2_started = threading.Event()

    # Занимаем слот первым вызовом
    ep1 = _core._ai_begin("holder", batch="cancel_batch", step="yellow")

    def worker2() -> None:
        try:
            thread2_started.set()
            ep2 = _core._ai_begin("waiter", batch="cancel_batch", step="yellow")
            _core._ai_end(ep2)
        except Exception as e:
            thread2_error.append(e)

    t2 = threading.Thread(target=worker2)
    t2.start()
    assert thread2_started.wait(timeout=2.0) is True
    time.sleep(0.2)

    # Отменяем всё через cancel_call
    aicut.cancel_call()

    # Освобождаем первый вызов
    _core._ai_end(ep1)

    t2.join(timeout=5.0)
    assert len(thread2_error) == 1
    assert isinstance(thread2_error[0], ReelsiError)
    assert _core.AI_ACTIVE == 0
    assert _core.AI_ACTIVE_BY.get("cancel_batch", 0) == 0


def test_step_concurrency_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """7. step_concurrency: lmstudio без поля -> 1; openrouter без поля -> 4; поле 3 -> 3;
    поле 'x', 0, True, 99 -> умолчание провайдера; loopback-профиль с /props -> 4;
    /props падает -> 1; повторный вызов в течение 60 с не ходит в сеть второй раз."""
    fake_profiles: dict[str, Any] = {
        "prof_lms": {"provider": "lmstudio"},
        "prof_openrouter": {"provider": "openrouter"},
        "prof_custom3": {"provider": "openrouter", "concurrency": 3},
        "prof_invalid_str": {"provider": "openrouter", "concurrency": "x"},
        "prof_invalid_zero": {"provider": "openrouter", "concurrency": 0},
        "prof_invalid_bool": {"provider": "openrouter", "concurrency": True},
        "prof_invalid_big": {"provider": "openrouter", "concurrency": 99},
        "prof_loopback": {"provider": "openai", "base_url": "http://127.0.0.1:8080/v1"},
    }

    monkeypatch.setattr(ai_config, "load_ai_config", lambda: {
        "active": "prof_lms",
        "profiles": fake_profiles,
        "step_profiles": {
            "s_lms": "prof_lms",
            "s_openrouter": "prof_openrouter",
            "s_custom3": "prof_custom3",
            "s_invalid_str": "prof_invalid_str",
            "s_invalid_zero": "prof_invalid_zero",
            "s_invalid_bool": "prof_invalid_bool",
            "s_invalid_big": "prof_invalid_big",
            "s_loopback": "prof_loopback",
        },
    })

    assert ai_config.step_concurrency("s_lms") == 1
    assert ai_config.step_concurrency("s_openrouter") == 4
    assert ai_config.step_concurrency("s_custom3") == 3
    assert ai_config.step_concurrency("s_invalid_str") == 4
    assert ai_config.step_concurrency("s_invalid_zero") == 4
    assert ai_config.step_concurrency("s_invalid_bool") == 4
    assert ai_config.step_concurrency("s_invalid_big") == 4

    # Loopback с опросом /props
    urlopen_calls = 0

    class DummyResponse:
        def __enter__(self) -> DummyResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps({"total_slots": 4}).encode("utf-8")

    def mock_urlopen(req: Any, timeout: float = 2) -> DummyResponse:
        nonlocal urlopen_calls
        urlopen_calls += 1
        return DummyResponse()

    ai_config._PROPS_CACHE.clear()
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    assert ai_config.step_concurrency("s_loopback") == 4
    assert urlopen_calls == 1

    # Повторный вызов использует кэш и не ходит в сеть
    assert ai_config.step_concurrency("s_loopback") == 4
    assert urlopen_calls == 1

    # /props падает -> фолбэк 1
    ai_config._PROPS_CACHE.clear()

    def mock_urlopen_fail(req: Any, timeout: float = 2) -> DummyResponse:
        raise urllib.error.URLError("connection failed")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_fail)
    assert ai_config.step_concurrency("s_loopback") == 1


def test_ensure_loaded_parallel_flag_and_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """8. ensure_loaded(..., parallel=4) кладёт --parallel 4 в команду lms load; при parallel=1
    флага нет; два потока одновременно -> lms load вызван один раз."""
    commands_run: list[list[str]] = []
    loaded_state: set[str] = set()

    monkeypatch.setattr(ai_llm, "_lms_bin", lambda: "/mock/bin/lms")
    monkeypatch.setattr(ai_llm, "loaded_models", lambda url=None: set(loaded_state))

    def mock_run(cmd: list[str], **kwargs: Any) -> Any:
        commands_run.append(list(cmd))
        if len(cmd) >= 3 and cmd[1] == "load":
            time.sleep(0.05)
            loaded_state.add(cmd[2])
        return None

    monkeypatch.setattr(ai_llm.subprocess, "run", mock_run)

    # parallel=4 -> есть флаг --parallel 4
    ai_llm.ensure_loaded("model_p4", parallel=4)
    load_cmds_p4 = [c for c in commands_run if c[1] == "load" and c[2] == "model_p4"]
    assert len(load_cmds_p4) == 1
    assert "--parallel" in load_cmds_p4[0]
    idx = load_cmds_p4[0].index("--parallel")
    assert load_cmds_p4[0][idx + 1] == "4"

    # parallel=1 -> флага нет
    commands_run.clear()
    loaded_state.clear()
    ai_llm.ensure_loaded("model_p1", parallel=1)
    load_cmds_p1 = [c for c in commands_run if c[1] == "load" and c[2] == "model_p1"]
    assert len(load_cmds_p1) == 1
    assert "--parallel" not in load_cmds_p1[0]

    # Два потока одновременно -> lms load ровно 1 раз
    commands_run.clear()
    loaded_state.clear()

    t1 = threading.Thread(target=lambda: ai_llm.ensure_loaded("shared_model", parallel=2))
    t2 = threading.Thread(target=lambda: ai_llm.ensure_loaded("shared_model", parallel=2))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    load_cmds_shared = [c for c in commands_run if c[1] == "load" and c[2] == "shared_model"]
    assert len(load_cmds_shared) == 1


def test_save_profile_concurrency_validation() -> None:
    """9. save_profile: concurrency "3" -> в профиле 3; пусто -> ключа нет; "20" -> ReelsiError."""
    cfg: dict[str, Any] = {"profiles": {}, "active": "test"}

    # concurrency "3" -> число 3
    ai_actions.save_profile(cfg, {"name": "p1", "profile": {"provider": "openrouter", "concurrency": "3"}})
    assert cfg["profiles"]["p1"]["concurrency"] == 3

    # concurrency пустая строка -> ключа нет
    ai_actions.save_profile(cfg, {"name": "p2", "profile": {"provider": "openrouter", "concurrency": "   "}})
    assert "concurrency" not in cfg["profiles"]["p2"]

    # concurrency None -> ключа нет
    ai_actions.save_profile(cfg, {"name": "p3", "profile": {"provider": "openrouter", "concurrency": None}})
    assert "concurrency" not in cfg["profiles"]["p3"]

    # concurrency "20" -> ReelsiError
    with pytest.raises(ReelsiError):
        ai_actions.save_profile(cfg, {"name": "p4", "profile": {"provider": "openrouter", "concurrency": "20"}})

    # concurrency 0 -> ReelsiError
    with pytest.raises(ReelsiError):
        ai_actions.save_profile(cfg, {"name": "p5", "profile": {"provider": "openrouter", "concurrency": "0"}})

    # concurrency "abc" -> ReelsiError
    with pytest.raises(ReelsiError):
        ai_actions.save_profile(cfg, {"name": "p6", "profile": {"provider": "openrouter", "concurrency": "abc"}})


def test_routes_pass_batch_and_step(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """10. /api/ai_inserts и /api/ai_yellow передают batch и step в _ai_begin."""
    begin_calls: list[dict[str, Any]] = []

    def mock_ai_begin(label: str = "", batch: str | None = None, step: str | None = None) -> int:
        begin_calls.append({"label": label, "batch": batch, "step": step})
        return 999

    monkeypatch.setattr(ai_route, "_ai_begin", mock_ai_begin)
    monkeypatch.setattr(ai_route, "_ai_end", lambda ep, unload=False: None)
    monkeypatch.setattr(ai_route.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(aicut, "cmd_yellow", lambda *a, **kw: {"ok": True, "yellow": [], "total": 0})
    monkeypatch.setattr(aicut, "cmd_inserts", lambda *a, **kw: {"inserts": [], "ins_target": 13})

    r1 = client.post("/api/ai_yellow", json={"xml": "dummy.xml", "batch": "my_batch_yellow"}, headers=H)
    assert r1.status_code == 200
    assert len(begin_calls) == 1
    assert begin_calls[0]["batch"] == "my_batch_yellow"
    assert begin_calls[0]["step"] == "yellow"

    r2 = client.post("/api/ai_inserts", json={"xml": "dummy.xml", "batch": "my_batch_inserts"}, headers=H)
    assert r2.status_code == 200
    assert len(begin_calls) == 2
    assert begin_calls[1]["batch"] == "my_batch_inserts"
    assert begin_calls[1]["step"] == "inserts"


def test_ai_config_get_returns_step_concurrency(client: Any) -> None:
    """11. /api/ai_config GET отдаёт step_concurrency со всеми шагами, а также step_local и step_profiles."""
    r = client.get("/api/ai_config", headers=H)
    assert r.status_code == 200
    data = r.get_json()
    assert "step_concurrency" in data
    step_conc = data["step_concurrency"]
    for step in aicut.STEP_REASONING_DEFAULT:
        assert step in step_conc
        assert isinstance(step_conc[step], int)
        assert 1 <= step_conc[step] <= 16
    assert "step_local" in data
    assert "step_profiles" in data
    for step in ("yellow", "inserts", "intro"):
        assert step in data["step_local"]
        assert isinstance(data["step_local"][step], bool)
        assert step in data["step_profiles"]


def test_ai_begin_concurrency_error_does_not_leak_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """12. _ai_begin: сбой step_concurrency не оставляет номер вызова в _LIVE (idle_since(0) -> True)."""
    def _boom(step: str | None) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(aicut, "step_concurrency", _boom)

    with ai_llm._EPOCH_LOCK:
        ai_llm._LIVE.clear()
        ai_llm._DEAD.clear()
        ai_llm.CANCEL = False

    with pytest.raises(RuntimeError, match="boom"):
        _core._ai_begin("test_leak", batch="b", step="yellow")

    assert aicut.idle_since(0) is True


def test_batch_per_step_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """13. При step_concurrency=1 у обоих шагов один batch с шагами yellow и inserts
    занимает два слота одновременно, а второй yellow того же batch ждёт."""
    monkeypatch.setattr(aicut, "step_concurrency", lambda step: 1)

    try:
        ep_yellow1 = _core._ai_begin("yellow_1", batch="shared_batch", step="yellow")
        ep_inserts = _core._ai_begin("inserts_1", batch="shared_batch", step="inserts")

        assert _core.AI_ACTIVE == 2
        assert _core.AI_ACTIVE_BY.get(("shared_batch", "yellow"), 0) == 1
        assert _core.AI_ACTIVE_BY.get(("shared_batch", "inserts"), 0) == 1

        yellow2_started = threading.Event()
        yellow2_done = threading.Event()
        yellow2_ep: list[int] = []

        def worker() -> None:
            yellow2_started.set()
            ep = _core._ai_begin("yellow_2", batch="shared_batch", step="yellow")
            yellow2_ep.append(ep)
            yellow2_done.set()
            _core._ai_end(ep)

        t = threading.Thread(target=worker)
        t.start()
        assert yellow2_started.wait(timeout=2.0) is True
        time.sleep(0.2)

        # Второй yellow ждёт своего слота
        assert not yellow2_done.is_set()

        # Освобождаем первый yellow — второй yellow получает слот и завершается
        _core._ai_end(ep_yellow1)
        assert yellow2_done.wait(timeout=3.0) is True
        t.join(timeout=3.0)

        _core._ai_end(ep_inserts)
        assert _core.AI_ACTIVE == 0
    finally:
        with _core.LOCK:
            _core.AI_ACTIVE = 0
            _core.AI_ACTIVE_BY.clear()
            _core.AI_BATCH_BY_EP.clear()
        with ai_llm._EPOCH_LOCK:
            ai_llm._LIVE.clear()
            ai_llm._DEAD.clear()
            ai_llm.CANCEL = False


