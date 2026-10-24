# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Открытие сессии фона (`core/rembg_run.py`): опции арены, проверка видеокарты по VRAM,
проверка факта по провайдерам сессии, откат на процессор.

Пакеты rembg и onnxruntime тут НЕ нужны (в CI их нет): оба модуля подменяются через
`sys.modules`. Видеокарты в тесте тоже нет: свободную VRAM передаём числом.
"""
import sys
import types
from types import SimpleNamespace

import pytest

from core import model_service, ort_gpu, rembg_run
from core.umsg import ReelsiError

MIB = 1024 * 1024


class _Session:
    """Сессия rembg: `inner_session.get_providers()` отдаёт то, что сессия сообщает о себе."""

    def __init__(self, name, reported):
        self.name = name
        self.inner_session = SimpleNamespace(get_providers=lambda: list(reported))


class _Rembg:
    """Заглушка rembg: записываем, как открывали сессию. `reported` — что сессия скажет о
    своих провайдерах (None — то, что запросили). `fail_cuda` — открытие на CUDA падает."""

    def __init__(self):
        self.calls = []          # (имя модели, providers) каждой попытки
        self.reported = None
        self.fail_cuda = False

    def new_session(self, name="u2net", providers=None):
        self.calls.append((name, providers))
        names = [p[0] if isinstance(p, tuple) else p for p in providers]
        if self.fail_cuda and names[0] == "CUDAExecutionProvider":
            raise RuntimeError("cudnn64_9.dll не найдена")
        return _Session(name, self.reported if self.reported is not None else names)

    def remove(self, data, session=None):
        return ("removed", session.name, data)


class _RecordingLog:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


@pytest.fixture
def rb(monkeypatch):
    rembg = _Rembg()
    mod = types.ModuleType("rembg")
    mod.new_session = rembg.new_session
    mod.remove = rembg.remove
    monkeypatch.setitem(sys.modules, "rembg", mod)
    ort = types.ModuleType("onnxruntime")
    ort.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setattr(ort_gpu, "activate", lambda: False)     # папка пакета — не тема этих тестов
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", True)     # DLL из torch не подключаем
    log = _RecordingLog()
    monkeypatch.setattr(rembg_run, "log", log)
    return SimpleNamespace(rembg=rembg, ort=ort, log=log, mp=monkeypatch)


# --- 1. Опции арены -------------------------------------------------------------------

def test_cuda_options_cap_arena_for_birefnet(rb):
    opts = rembg_run.cuda_options("birefnet-general")
    assert opts["arena_extend_strategy"] == "kSameAsRequested"
    assert opts["gpu_mem_limit"] == str(10240 * MIB)
    assert opts["cudnn_conv_use_max_workspace"] == "1"
    assert opts["cudnn_conv_algo_search"] == "HEURISTIC"


def test_cuda_options_cap_arena_for_u2net(rb):
    opts = rembg_run.cuda_options("u2net")
    assert opts["arena_extend_strategy"] == "kSameAsRequested"
    assert opts["gpu_mem_limit"] == str(1024 * MIB)


def test_unknown_model_gets_the_largest_cap(rb):
    assert rembg_run.cuda_options("sam2")["gpu_mem_limit"] == str(10240 * MIB)


def test_vram_need_is_arena_plus_service_reserve(rb):
    assert rembg_run.vram_need_mib("u2net") == 1024 + model_service.VRAM_RESERVE_MIB
    assert rembg_run.vram_need_mib("birefnet-general") == 10240 + model_service.VRAM_RESERVE_MIB


# --- 2. Видеокарта занята: процессор, причина с обоими числами -------------------------

def test_busy_vram_falls_back_to_cpu_with_both_numbers(rb):
    need = rembg_run.vram_need_mib("birefnet-general")
    session, on_gpu, why = rembg_run.open_session("birefnet-general", True, need - 1)
    assert on_gpu is False
    assert str(need - 1) in why and str(need) in why
    assert rb.rembg.calls == [("birefnet-general", ["CPUExecutionProvider"])]


def test_exact_free_vram_is_enough(rb):
    need = rembg_run.vram_need_mib("birefnet-general")
    _, on_gpu, why = rembg_run.open_session("birefnet-general", True, need)
    assert on_gpu is True and why == ""


# --- 3. Видеокарта: факт подтверждён, опции CUDA из cuda_options -----------------------

def test_cuda_session_confirmed_by_providers_gets_cuda_options(rb):
    session, on_gpu, why = rembg_run.open_session("birefnet-general", True, 16000)
    assert on_gpu is True and why == ""
    assert session.name == "birefnet-general"
    [(name, providers)] = rb.rembg.calls
    assert name == "birefnet-general"
    assert providers[0] == ("CUDAExecutionProvider", rembg_run.cuda_options("birefnet-general"))
    assert providers[1] == "CPUExecutionProvider"


# --- 4. Запрошена CUDA, сессия фактически на процессоре -------------------------------

def test_session_reporting_cpu_is_not_counted_as_gpu(rb):
    rb.rembg.reported = ["CPUExecutionProvider"]
    session, on_gpu, why = rembg_run.open_session("u2net", True, 16000)
    assert on_gpu is False
    assert why == "onnxruntime открыл сессию на процессоре"
    assert len(rb.log.warnings) == 1
    assert session.name == "u2net"


# --- 5. Настройка «процессор»: VRAM не спрашиваем --------------------------------------

def test_cpu_setting_skips_gpu_checks(rb):
    asked = []
    rb.ort.get_available_providers = lambda: asked.append(1) or ["CUDAExecutionProvider"]
    session, on_gpu, why = rembg_run.open_session("u2net", False, None)
    assert on_gpu is False
    assert why == "настройка: процессор"
    assert rb.rembg.calls == [("u2net", ["CPUExecutionProvider"])]
    assert asked == [], "при настройке «процессор» onnxruntime о CUDA не спрашиваем"
    assert rb.log.warnings == []


# --- Прочие пути открытия ---------------------------------------------------------------

def test_no_cuda_in_onnxruntime_means_cpu(rb):
    rb.ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    _, on_gpu, why = rembg_run.open_session("u2net", True, 16000)
    assert on_gpu is False and why == "нет CUDA в onnxruntime"


def test_missing_onnxruntime_means_cpu(rb, monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # import даёт ImportError
    _, on_gpu, why = rembg_run.open_session("u2net", True, 16000)
    assert on_gpu is False and why == "нет CUDA в onnxruntime"
    assert rb.rembg.calls == [("u2net", ["CPUExecutionProvider"])]


def test_cuda_open_error_falls_back_to_cpu_with_reason(rb):
    rb.rembg.fail_cuda = True
    session, on_gpu, why = rembg_run.open_session("u2net", True, 16000)
    assert on_gpu is False
    assert why.startswith("видеокарта не открылась: RuntimeError")
    assert "cudnn64_9.dll" in why
    assert [names for _, names in rb.rembg.calls] == [
        [("CUDAExecutionProvider", rembg_run.cuda_options("u2net")), "CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]
    assert len(rb.log.warnings) == 1


def test_missing_rembg_is_a_reelsi_error(rb, monkeypatch):
    monkeypatch.setitem(sys.modules, "rembg", None)
    with pytest.raises(ReelsiError):
        rembg_run.open_session("u2net", False, None)


def test_predict_runs_rembg_remove_on_session(rb):
    session, _, _ = rembg_run.open_session("u2net", False, None)
    assert rembg_run.predict(session, b"img") == ("removed", "u2net", b"img")
