# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Снятие фона на видеокарте (core/ort_gpu.py): своя папка onnxruntime-gpu.

Ничего не качается и не ставится: pip подменён заглушкой `subprocess.Popen`, onnxruntime
и rembg — заглушками через `sys.meta_path` (так видно, КТО и когда импортирует пакет).
Видеокарту подменяем флагом: проверка CUDA тут не нужна, нужна логика.
"""
import importlib.machinery
import importlib.metadata
import io
import os
import sys
import time
import types
from types import SimpleNamespace

import pytest
from flask import Flask

import api
from core import aicut, insertlib, model_service, ort_gpu, rembg_run
from core.umsg import ReelsiError, umsg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Заглушки и фикстуры
# --------------------------------------------------------------------------- #
def _pkg(path):
    """Распакованный пакет onnxruntime в папке: признак, по которому ort_gpu его находит."""
    pkg = os.path.join(path, "onnxruntime")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    return pkg


class _StubProc:
    """Процесс pip-заглушки: строки вывода и код возврата, никакой сети."""

    def __init__(self, lines, code):
        self.stdout = io.StringIO("".join(lines))
        self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _fake_pip(monkeypatch, calls, code=0, lines=("Collecting onnxruntime-gpu\n",)):
    """Подмена `subprocess.Popen`: запись команды, «распаковка» колеса в --target.

    При провале пишем ещё и `partial.txt` — недокачанный хвост. Тест смотрит, что он
    не попал в боевую папку: это и ловит установку мимо `DIR.part`."""
    def fake_popen(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if "--target" in cmd:
            target = cmd[cmd.index("--target") + 1]
            _pkg(target)
            if code != 0:
                with open(os.path.join(target, "partial.txt"), "w", encoding="utf-8") as f:
                    f.write("")
        return _StubProc(lines, code)

    monkeypatch.setattr(ort_gpu.subprocess, "Popen", fake_popen)


@pytest.fixture
def dists(monkeypatch):
    """Установленные дистрибутивы: подмена `importlib.metadata.version` по словарю имя -> версия.

    Настоящий site-packages здесь не нужен и мешает: на машине разработчика стоит
    onnxruntime-gpu, и тесты увидели бы его вместо задуманного набора пакетов."""
    installed = {"onnxruntime": "1.20.1"}
    real_version = importlib.metadata.version            # Flask читает версию werkzeug — тому нужна настоящая

    def fake_version(name):
        if not name.startswith("onnxruntime"):
            return real_version(name)
        if name in installed:
            return installed[name]
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    return installed


@pytest.fixture
def ort(tmp_path, monkeypatch, dists):
    """Папка пакета во временном каталоге, видеокарта есть. Системный дистрибутив — из `dists`
    (по умолчанию onnxruntime 1.20.1, как у тех, кто ставил пакет по инструкции до кнопки)."""
    d = str(tmp_path / "ort_gpu")
    monkeypatch.setattr(ort_gpu, "DIR", d)
    monkeypatch.setattr(ort_gpu, "JOB", {"running": False, "done": False, "step": "",
                                         "error": "", "log": []})
    monkeypatch.setattr(sys, "path", list(sys.path))     # activate() правит путь — откатываем
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    monkeypatch.setattr(ort_gpu, "gpu_present", lambda: True)
    return SimpleNamespace(dir=d, root=tmp_path, dists=dists)


class _StubLoader:
    """Заглушка модуля onnxruntime/rembg: видна только CPU, сессия не открывается."""

    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        mod.get_available_providers = lambda: ["CPUExecutionProvider"]
        mod.remove = lambda data, session=None: data
        mod.new_session = _no_session
        return mod

    def exec_module(self, module):
        return None


def _no_session(name, providers=None):
    """rembg-заглушка: сессию не открываем — тесту важен только порядок импорта."""
    raise ReelsiError(umsg("rembg_failed", "тест: сессию не открываем", err="test"))


class _Finder:
    """Ловит импорт onnxruntime и rembg: пишем в журнал, кто и когда их тянет."""

    def __init__(self, events):
        self.events = events

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ("onnxruntime", "rembg"):
            return None
        self.events.append(fullname)
        return importlib.machinery.ModuleSpec(fullname, _StubLoader())


def _no_service_remove(img, model):
    """Заглушка сервиса моделей: его нет — `insertlib.remove_bg` снимает фон сам."""
    raise model_service.ServiceUnavailable("тест: сервиса моделей нет")


@pytest.fixture
def traced(monkeypatch):
    """Журнал импортов: список имён в порядке обращения к ним (модули не остаются в sys.modules)."""
    events = []
    names = ("onnxruntime", "rembg")
    for n in names:
        monkeypatch.delitem(sys.modules, n, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Finder(events)] + sys.meta_path)
    monkeypatch.setattr(insertlib, "_RB_SESSION", None)
    monkeypatch.setattr(insertlib, "_RB_SESSION_NAME", None)
    monkeypatch.setattr(insertlib, "_RB_SESSION_GPU", False)
    monkeypatch.setattr(insertlib, "_RB_IDLE_TIMER", None)
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", True)
    # Устройство — процессор: VRAM не меряем, onnxruntime тянем только там, где он нужен.
    monkeypatch.setenv("REELSI_MODEL_SERVICE_DEVICE", "cpu")
    # Сервиса моделей нет: настоящий сервис из этого теста не поднимаем.
    monkeypatch.setattr(model_service, "remove_bg", _no_service_remove)
    monkeypatch.setattr(aicut, "rembg_model", lambda: "u2net")
    yield events
    for n in names:
        sys.modules.pop(n, None)


# --------------------------------------------------------------------------- #
# 1. activate: путь до импорта, без дублей, не трогает уже загруженный пакет
# --------------------------------------------------------------------------- #
def test_activate_without_folder_changes_nothing(ort):
    before = list(sys.path)
    assert ort_gpu.activate() is False
    assert sys.path == before


def test_activate_puts_folder_first_and_only_once(ort):
    _pkg(ort.dir)
    assert ort_gpu.activate() is True
    assert sys.path[0] == ort.dir
    assert ort_gpu.activate() is True
    assert sum(1 for p in sys.path if p == ort.dir) == 1


def test_activate_leaves_loaded_onnxruntime_alone(ort, monkeypatch):
    _pkg(ort.dir)
    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))
    before = list(sys.path)
    assert ort_gpu.activate() is False
    assert sys.path == before


# --------------------------------------------------------------------------- #
# 2. status: флаги по состоянию, и статус НЕ импортирует onnxruntime
# --------------------------------------------------------------------------- #
def test_status_nothing_installed(ort, traced):
    s = ort_gpu.status()
    assert s == {"installed": False, "active": False, "loaded_cpu": False, "cuda": None,
                 "gpu": True, "version": "1.20.1", "dir": ort.dir, "system_gpu": False}
    assert traced == []                                  # статус сам onnxruntime не грузит


def test_status_activates_installed_folder(ort, traced):
    """Пакет, поставленный кнопкой, встаёт в путь сразу, без перезапуска: status() сам
    вызывает activate() первым делом. onnxruntime при этом не импортируется."""
    _pkg(ort.dir)
    s = ort_gpu.status()
    assert s["installed"] is True and s["active"] is True
    assert s["loaded_cpu"] is False and s["cuda"] is None
    assert sys.path[0] == ort.dir
    assert traced == []


def test_status_active_before_load_asks_no_cuda(ort, traced):
    _pkg(ort.dir)
    ort_gpu.activate()
    s = ort_gpu.status()
    assert s["installed"] and s["active"]
    assert s["loaded_cpu"] is False and s["cuda"] is None
    assert traced == []                                  # cuda не спрашиваем: импорт зацементировал бы CPU-сборку


def test_status_system_onnxruntime_loaded_means_restart(ort, monkeypatch):
    _pkg(ort.dir)
    sysmod = types.ModuleType("onnxruntime")
    sysmod.__file__ = os.path.join(ort.root, "site-packages", "onnxruntime", "__init__.py")
    sysmod.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", sysmod)
    s = ort_gpu.status()
    assert s["loaded_cpu"] is True                        # системная сборка в процессе: нужен перезапуск
    assert s["cuda"] is False
    assert s["active"] is False
    assert ort.dir not in sys.path                        # путь не вставлен: поздно, пакет уже загружен


def test_status_module_from_folder_reports_cuda(ort, monkeypatch):
    _pkg(ort.dir)
    mod = types.ModuleType("onnxruntime")
    mod.__file__ = os.path.join(ort.dir, "onnxruntime", "__init__.py")
    mod.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", mod)
    s = ort_gpu.status()
    assert s["loaded_cpu"] is False and s["cuda"] is True


# --------------------------------------------------------------------------- #
# 3. установка: колесо в DIR.part, подмена папки, отказ не трогает старую копию
# --------------------------------------------------------------------------- #
def test_install_success_replaces_folder(ort, monkeypatch):
    _pkg(ort.dir)
    marker = os.path.join(ort.dir, "old_marker.txt")
    open(marker, "w").close()
    calls = []
    _fake_pip(monkeypatch, calls, code=0)
    ort_gpu._install("1.20.1")
    cmd = calls[0]
    assert "--target" in cmd and "--no-deps" in cmd
    assert "onnxruntime-gpu==1.20.1" in cmd
    assert cmd[cmd.index("--target") + 1] == ort.dir + ".part"
    assert os.path.isfile(os.path.join(ort.dir, "onnxruntime", "__init__.py"))
    assert not os.path.exists(ort.dir + ".part")
    assert not os.path.exists(marker)                    # старая копия убрана после успеха
    assert ort_gpu.JOB["done"] is True and ort_gpu.JOB["running"] is False
    assert ort_gpu.JOB["step"] == "done"


def test_install_failure_keeps_old_copy_and_drops_partial(ort, monkeypatch):
    _pkg(ort.dir)
    marker = os.path.join(ort.dir, "keep.txt")
    open(marker, "w").close()
    _fake_pip(monkeypatch, [], code=1)
    ort_gpu._install("1.20.1")
    assert os.path.isfile(marker)                        # старая копия цела
    assert not os.path.exists(os.path.join(ort.dir, "partial.txt"))   # хвост провала не попал в DIR
    assert not os.path.exists(ort.dir + ".part")         # и сама недокачанная папка снята
    assert ort_gpu.JOB["step"] == "fail" and ort_gpu.JOB["running"] is False
    assert "код возврата 1" in ort_gpu.JOB["error"]


def test_pip_failure_carries_install_code(ort, monkeypatch):
    _fake_pip(monkeypatch, [], code=2)
    with pytest.raises(ReelsiError) as ei:
        ort_gpu._run_pip(["pip"])
    assert ei.value.code == "ort_gpu_install_failed"


def test_install_without_package_is_failure(ort, monkeypatch):
    monkeypatch.setattr(ort_gpu.subprocess, "Popen", lambda cmd, **kw: _StubProc(["ok\n"], 0))
    ort_gpu._install("1.20.1")
    assert ort_gpu.JOB["step"] == "fail"
    assert not os.path.exists(ort.dir)


def test_locked_old_copy_is_left_untouched(ort, monkeypatch):
    """Сервер держит старый пакет: его папку переименовать нельзя — ничего не сносим."""
    _pkg(ort.dir)
    marker = os.path.join(ort.dir, "keep.txt")
    open(marker, "w").close()
    real_replace = os.replace

    def locked(src, dst):
        if os.path.abspath(src) == os.path.abspath(ort.dir):
            raise PermissionError("файл занят")
        return real_replace(src, dst)

    monkeypatch.setattr(ort_gpu.os, "replace", locked)
    _fake_pip(monkeypatch, [], code=0)
    ort_gpu._install("1.20.1")
    assert ort_gpu.JOB["step"] == "fail"
    assert os.path.isfile(marker)
    assert not os.path.exists(ort.dir + ".part")


def test_start_refuses_without_gpu_and_runs_no_pip(ort, monkeypatch):
    monkeypatch.setattr(ort_gpu, "gpu_present", lambda: False)
    calls = []
    _fake_pip(monkeypatch, calls)
    with pytest.raises(ReelsiError) as ei:
        ort_gpu.start_install()
    assert ei.value.code == "ort_gpu_no_gpu"
    assert calls == []
    assert ort_gpu.JOB["running"] is False


def test_start_refuses_without_onnxruntime(ort, monkeypatch):
    ort.dists.clear()                                    # ни одного дистрибутива onnxruntime
    calls = []
    _fake_pip(monkeypatch, calls)
    with pytest.raises(ReelsiError) as ei:
        ort_gpu.start_install()
    assert ei.value.code == "ort_gpu_no_ort"
    assert calls == []


def test_start_while_running_returns_false(ort):
    ort_gpu.JOB["running"] = True
    assert ort_gpu.start_install() is False


def test_start_runs_in_background_and_logs_pip_output(ort, monkeypatch):
    calls = []
    _fake_pip(monkeypatch, calls, code=0)
    assert ort_gpu.start_install() is True
    deadline = time.time() + 15
    while ort_gpu.JOB["running"] and time.time() < deadline:
        time.sleep(0.02)
    assert ort_gpu.JOB["done"] is True and ort_gpu.JOB["step"] == "done"
    assert os.path.isfile(os.path.join(ort.dir, "onnxruntime", "__init__.py"))
    assert "Collecting onnxruntime-gpu" in ort_gpu.job_status()["log"]


# --------------------------------------------------------------------------- #
# 4. insertlib: путь ставится до импорта onnxruntime и rembg
# --------------------------------------------------------------------------- #
def test_providers_activate_before_onnxruntime_import(traced, monkeypatch):
    monkeypatch.setattr(ort_gpu, "activate", lambda: traced.append("activate") or False)
    with pytest.raises(ReelsiError):              # rembg-заглушка сессию не открывает: важен только порядок
        rembg_run.open_session("u2net", True, None)
    assert traced[:2] == ["activate", "onnxruntime"]


def test_remove_bg_activate_before_rembg_import(traced, monkeypatch):
    monkeypatch.setattr(ort_gpu, "activate", lambda: traced.append("activate") or False)
    with pytest.raises(ReelsiError):
        insertlib.remove_bg(b"\x89PNG-not-really", trim=False, emit=lambda *a, **k: None)
    # rembg сам тянет onnxruntime: путь должен встать раньше обоих импортов
    assert traced[:2] == ["activate", "rembg"]


# --------------------------------------------------------------------------- #
# 5. API: статус и запуск установки
# --------------------------------------------------------------------------- #
def _client():
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    return app.test_client()


def test_api_status_has_fields_and_job(ort):
    d = _client().get("/api/ort_gpu").get_json()
    assert d["ok"] is True
    for key in ("installed", "active", "loaded_cpu", "cuda", "gpu", "version", "dir", "job",
                "system_gpu"):
        assert key in d
    assert d["dir"] == ort.dir
    assert d["job"]["running"] is False


def test_api_install_starts_through_start_install(ort, monkeypatch):
    seen = []
    monkeypatch.setattr(ort_gpu, "start_install", lambda: seen.append(1) or True)
    d = _client().post("/api/ort_gpu_install").get_json()
    assert d == {"ok": True, "started": True}
    assert seen == [1]


def test_api_install_refusal_is_user_error(ort, monkeypatch):
    monkeypatch.setattr(ort_gpu, "gpu_present", lambda: False)
    d = _client().post("/api/ort_gpu_install").get_json()
    assert d["err"] == "ort_gpu_no_gpu"
    assert d["error"]


# --------------------------------------------------------------------------- #
# 6. UI-контракт по исходнику: строка под выбором модели, кнопка, вызовы API
# --------------------------------------------------------------------------- #
def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_ui_row_sits_right_under_model_row():
    html = _read("templates/index.html")
    model = html.index('id="ais_rembgModelRow"')
    row = html.index('id="ort_gpuRow"')
    assert row > model
    assert "</select></div>" in html[model:row]          # строка модели закрыта раньше нашей
    block = html[row:row + 700]
    assert 'id="ort_gpuBtn"' in block and "ortGpuInstall()" in block


def test_ui_js_calls_api_and_shows_row_with_model_row():
    js = _read("static/app/10-settings.js")
    assert "'/api/ort_gpu'" in js
    assert "'/api/ort_gpu_install'" in js
    assert "$('ort_gpuRow').style.display=imgGenOn()" in js
    assert "if(imgGenOn())ortGpuFill();" in js
    assert "s.system_gpu" in js                          # системный onnxruntime-gpu: строка «работает», кнопки нет


# --------------------------------------------------------------------------- #
# 7. Системный дистрибутив: onnxruntime-gpu по инструкции распознаётся, качать не нужно
# --------------------------------------------------------------------------- #
def test_system_dist_finds_gpu_package(dists, traced):
    """Владелец поставил onnxruntime-gpu 1.23.2 по инструкции, дистрибутива onnxruntime нет:
    версия и признак системной видеокарты берутся из него, модуль при этом не импортируется."""
    dists.clear()
    dists["onnxruntime-gpu"] = "1.23.2"
    assert ort_gpu.system_dist() == ("onnxruntime-gpu", "1.23.2")
    assert ort_gpu.ort_version() == "1.23.2"
    assert ort_gpu.system_gpu() is True
    assert traced == []


def test_system_dist_cpu_package_is_not_gpu(dists):
    dists.clear()
    dists["onnxruntime"] = "1.20.1"
    assert ort_gpu.system_dist() == ("onnxruntime", "1.20.1")
    assert ort_gpu.system_gpu() is False


def test_system_dist_priority_and_other_builds(dists):
    dists.clear()
    dists["onnxruntime-directml"] = "1.19.2"
    assert ort_gpu.system_dist() == ("onnxruntime-directml", "1.19.2")
    dists["onnxruntime"] = "1.20.1"
    assert ort_gpu.system_dist() == ("onnxruntime", "1.20.1")     # первый по приоритету
    assert ort_gpu.system_gpu() is False


def test_system_dist_none_without_any_package(dists):
    dists.clear()
    assert ort_gpu.system_dist() is None
    assert ort_gpu.ort_version() is None
    assert ort_gpu.system_gpu() is False


def test_status_system_gpu_is_not_copied(ort, traced):
    """Системный onnxruntime-gpu: своя копия не нужна — путь не ставим, даже если папка есть."""
    ort.dists.clear()
    ort.dists["onnxruntime-gpu"] = "1.23.2"
    _pkg(ort.dir)
    s = ort_gpu.status()
    assert s["system_gpu"] is True and s["version"] == "1.23.2"
    assert s["active"] is False
    assert ort.dir not in sys.path
    assert traced == []


def test_activate_drops_stale_copy_when_system_gpu(ort):
    ort.dists.clear()
    ort.dists["onnxruntime-gpu"] = "1.23.2"
    _pkg(ort.dir)
    sys.path.insert(0, ort.dir)                           # осталась от прежней установки
    assert ort_gpu.activate() is False
    assert ort.dir not in sys.path


def test_start_refuses_with_system_gpu_and_runs_no_pip(ort, monkeypatch):
    ort.dists.clear()
    ort.dists["onnxruntime-gpu"] = "1.23.2"
    calls = []
    _fake_pip(monkeypatch, calls)
    with pytest.raises(ReelsiError) as ei:
        ort_gpu.start_install()
    assert ei.value.code == "ort_gpu_system"
    assert calls == []
    assert ort_gpu.JOB["running"] is False


def test_install_pins_version_of_system_cpu_package(ort, monkeypatch):
    """Системный onnxruntime (не gpu): колесо onnxruntime-gpu ставим ТОЧНО его версии."""
    ort.dists.clear()
    ort.dists["onnxruntime"] = "1.19.2"
    calls = []
    _fake_pip(monkeypatch, calls, code=0)
    assert ort_gpu.start_install() is True
    deadline = time.time() + 15
    while ort_gpu.JOB["running"] and time.time() < deadline:
        time.sleep(0.02)
    assert ort_gpu.JOB["done"] is True
    assert "onnxruntime-gpu==1.19.2" in calls[0]


def test_api_install_system_gpu_is_user_error(ort):
    ort.dists.clear()
    ort.dists["onnxruntime-gpu"] = "1.23.2"
    d = _client().post("/api/ort_gpu_install").get_json()
    assert d["err"] == "ort_gpu_system"
    assert d["error"]
