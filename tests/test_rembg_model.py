# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модель вырезания фона (`rembg_model`): u2net по умолчанию или birefnet-general.

Пакет rembg подменяется заглушкой: onnx-модели в тестах не грузим (веса тяжёлые, сеть
запрещена). Заглушка красит результат цветом модели, поэтому по пикселям видно, какой
моделью сделана вырезка, — и кэш `<стем>.nobg.png` проверяется по содержимому, а не
по догадке.

Конфиг — свой файл во временном каталоге (`AI_CONFIG_PATH` подменён), личный
ai_config.json не читается и не пишется.
"""
import io
import json
import os
import sys
import time
import types
from types import SimpleNamespace

import pytest
from flask import Flask
from PIL import Image

from core import insertlib
from core.aicut import config as aicut_config
import api
from core import aicut

# Цвет вырезки по моделям: по нему тест видит, какой моделью сделан файл.
_COLOR = {"u2net": (255, 0, 0), "birefnet-general": (0, 0, 255)}


def _rgba_png(rgb, size=8):
    """PNG с непрозрачным квадратом 4x4 в центре и пустыми полями — как после rembg."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(2, 6):
        for y in range(2, 6):
            im.putpixel((x, y), tuple(rgb) + (255,))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# Провайдеры машины с onnxruntime-gpu: CUDA с опциями cuDNN первым, CPU запасным.
_CUDA_OPTS = {"cudnn_conv_use_max_workspace": "1", "cudnn_conv_algo_search": "HEURISTIC"}
_CUDA_PROVIDERS = [("CUDAExecutionProvider", _CUDA_OPTS), "CPUExecutionProvider"]


def _uses_cuda(providers):
    """Первый провайдер — CUDA; в списке он идёт кортежем (имя, опции)."""
    first = providers[0]
    return (first[0] if isinstance(first, tuple) else first) == "CUDAExecutionProvider"


class _FakeRembg:
    """Заглушка rembg: что создавали и чем вырезали — записываем, onnx-модель не грузим.
    cuda_broken — сессия на видеокарте падает (как без нужных DLL), на CPU работает."""

    def __init__(self):
        self.sessions_created = []   # имена моделей, под которые создана сессия (успешно)
        self.session_calls = []      # (имя модели, providers) — каждая попытка открыть сессию
        self.removals = []           # имя модели сессии, которой сделали вырезку
        self.cuda_broken = False

    def new_session(self, name="u2net", providers=None):
        self.session_calls.append((name, providers))
        if self.cuda_broken and providers and _uses_cuda(providers):
            raise RuntimeError("cudnn64_9.dll не найдена")
        self.sessions_created.append(name)
        return SimpleNamespace(name=name)

    def remove(self, data, session=None):
        self.removals.append(session.name)
        return _rgba_png(_COLOR[session.name])


class _RecordingLog:
    """Заглушка журнала: предупреждения собираем, чтобы тест видел причину отката на CPU."""

    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "ai_config.json"
    cfg_path.write_text(json.dumps({"active": "Тест", "profiles": {
        "Тест": {"provider": "lmstudio", "base_url": "http://localhost:1/v1",
                 "api_key": "", "model": "m"}}}, ensure_ascii=False), encoding="utf-8")
    # Файл есть — автозасев с боевого конфига не сработает; подменяем на всякий случай.
    monkeypatch.setattr(aicut_config, "AI_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(aicut_config, "_seed_ai_config", lambda: None)
    monkeypatch.setattr(insertlib, "_RB_SESSION", None)
    monkeypatch.setattr(insertlib, "_RB_SESSION_NAME", None)
    monkeypatch.setattr(insertlib, "_RB_SESSION_GPU", False)
    monkeypatch.setattr(insertlib, "_RB_IDLE_TIMER", None)
    # DLL из torch здесь не подключаем: тест на них сам сбрасывает флаг и подменяет os.add_dll_directory.
    monkeypatch.setattr(insertlib, "_CUDA_DLLS_DONE", True)
    log = _RecordingLog()
    monkeypatch.setattr(insertlib, "log", log)
    fake = _FakeRembg()
    mod = types.ModuleType("rembg")
    mod.new_session = fake.new_session
    mod.remove = fake.remove
    monkeypatch.setitem(sys.modules, "rembg", mod)
    # onnxruntime тоже заглушка: тесты не зависят от того, стоит ли на машине onnxruntime-gpu.
    ort = types.ModuleType("onnxruntime")
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    yield SimpleNamespace(cfg=cfg_path, fake=fake, tmp=tmp_path, ort=ort, log=log)
    # Таймер выгрузки не должен пережить тест: иначе он сбросит сессию в чужом тесте.
    if insertlib._RB_IDLE_TIMER is not None:
        insertlib._RB_IDLE_TIMER.cancel()


def _set_model(env, model):
    """Записать rembg_model в конфиг (None — убрать ключ, как в старом конфиге)."""
    d = json.loads(env.cfg.read_text(encoding="utf-8"))
    if model is None:
        d.pop("rembg_model", None)
    else:
        d["rembg_model"] = model
    env.cfg.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _center_rgb(path):
    """Цвет центра вырезки (после обрезки полей квадрат занимает всё изображение)."""
    with Image.open(path) as im:
        return tuple(im.convert("RGBA").getpixel((1, 1)))[:3]


def test_default_model_is_u2net(env):
    assert aicut.rembg_model() == "u2net"
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net"]
    assert env.fake.removals == ["u2net"]


def test_unknown_value_in_config_falls_back_to_u2net(env):
    _set_model(env, "sam2")
    assert aicut.rembg_model() == "u2net"


def test_birefnet_is_used_when_chosen(env):
    _set_model(env, "birefnet-general")
    assert aicut.rembg_model() == "birefnet-general"
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["birefnet-general"]
    assert env.fake.removals == ["birefnet-general"]


def test_model_switch_opens_new_session_and_drops_old(env):
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net"]

    _set_model(env, "birefnet-general")
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "birefnet-general"]
    assert insertlib._RB_SESSION_NAME == "birefnet-general"
    assert env.fake.removals == ["u2net", "birefnet-general"]

    # Та же модель — сессия переиспользуется, новая не создаётся.
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "birefnet-general"]


def test_cutout_cache_is_model_specific(env):
    """Смена модели не отдаёт прошлую вырезку из кэша <стем>.nobg.png."""
    pic = env.tmp / "pic.png"
    pic.write_bytes(_rgba_png((200, 200, 200)))

    dst = insertlib.nobg_path(str(pic))
    assert os.path.basename(dst) == "pic.png.nobg.png"
    assert env.fake.removals == ["u2net"]
    assert _center_rgb(dst) == _COLOR["u2net"]

    # Та же модель: кэш берём, вырезку заново не делаем.
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["u2net"]

    # Смена модели: кэш той же картинки пересчитан, а не отдан старым.
    _set_model(env, "birefnet-general")
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["u2net", "birefnet-general"]
    assert _center_rgb(dst) == _COLOR["birefnet-general"]


def test_legacy_cache_without_tag_counts_as_u2net(env):
    """Кэш, сделанный до выбора модели (без метки), остаётся годным для u2net."""
    pic = env.tmp / "old.png"
    pic.write_bytes(_rgba_png((200, 200, 200)))
    dst = str(pic) + ".nobg.png"
    Image.open(io.BytesIO(_rgba_png(_COLOR["u2net"]))).save(dst, "PNG")  # без метки
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == []

    _set_model(env, "birefnet-general")
    assert insertlib.nobg_path(str(pic)) == dst
    assert env.fake.removals == ["birefnet-general"]


def test_api_get_and_set_rembg_model(env):
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/api/ai_config").get_json()["rembg_model"] == "u2net"

    ok = client.post("/api/ai_config",
                     json={"action": "set_rembg_model", "value": "birefnet-general"}).get_json()
    assert ok["ok"] is True
    assert ok["rembg_model"] == "birefnet-general"
    assert client.get("/api/ai_config").get_json()["rembg_model"] == "birefnet-general"

    bad = client.post("/api/ai_config",
                      json={"action": "set_rembg_model", "value": "sam2"}).get_json()
    assert bad.get("err") == "rembg_model_invalid"
    assert client.get("/api/ai_config").get_json()["rembg_model"] == "birefnet-general"


# --- Видеокарта: onnxruntime-gpu, откат на CPU, выгрузка после простоя ----------------

def _gpu(env):
    """Машина с onnxruntime-gpu: CUDA в списке провайдеров (видеокарты в тесте нет)."""
    env.ort.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_providers_are_cpu_only_without_cuda(env):
    assert insertlib._rembg_providers() == ["CPUExecutionProvider"]


def test_providers_put_cuda_with_cudnn_options_first(env):
    """Опции cuDNN из замера (10.2 с → 0.27 с): без них CUDA в 13 раз медленнее, терять нельзя."""
    _gpu(env)
    assert insertlib._rembg_providers() == _CUDA_PROVIDERS


def test_providers_are_cpu_only_without_onnxruntime(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)   # import даёт ImportError
    assert insertlib._rembg_providers() == ["CPUExecutionProvider"]


def test_gpu_session_opens_with_cudnn_options(env):
    _gpu(env)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.session_calls == [("u2net", _CUDA_PROVIDERS)]
    assert env.fake.removals == ["u2net"]


def test_cuda_failure_falls_back_to_cpu_with_warning(env):
    """Сессия на видеокарте не поднялась: откат на CPU, причина в журнале, картинка обработана."""
    _gpu(env)
    env.fake.cuda_broken = True
    out = insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.session_calls == [
        ("u2net", _CUDA_PROVIDERS),
        ("u2net", ["CPUExecutionProvider"]),
    ]
    assert env.fake.sessions_created == ["u2net"]
    assert env.fake.removals == ["u2net"]
    assert len(env.log.warnings) == 1
    assert "cudnn64_9.dll" in env.log.warnings[0]
    assert insertlib._RB_IDLE_TIMER is None, "CPU-сессия не выгружается, таймер не заводим"
    with Image.open(io.BytesIO(out)) as im:
        assert im.mode == "RGBA"


def test_gpu_session_is_unloaded_after_idle(env, monkeypatch):
    _gpu(env)
    monkeypatch.setattr(insertlib, "REMBG_GPU_IDLE_SEC", 0.05)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    deadline = time.monotonic() + 5
    while insertlib._RB_SESSION is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert insertlib._RB_SESSION is None
    assert insertlib._RB_SESSION_NAME is None

    # После выгрузки следующий вызов открывает сессию заново.
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "u2net"]


def test_cpu_session_is_kept_after_idle(env, monkeypatch):
    monkeypatch.setattr(insertlib, "REMBG_GPU_IDLE_SEC", 0.05)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    time.sleep(0.2)
    assert insertlib._RB_SESSION is not None
    assert insertlib._RB_IDLE_TIMER is None


def test_stale_idle_timer_does_not_unload(env, monkeypatch):
    """Таймер, который сработал, пока вызов перезапускал его, сессию не трогает: поколение устарело."""
    sentinel = object()
    monkeypatch.setattr(insertlib, "_RB_SESSION", sentinel)
    monkeypatch.setattr(insertlib, "_RB_SESSION_NAME", "u2net")
    with insertlib._RB_LOCK:
        insertlib._rb_arm_idle_timer()
        stale = insertlib._RB_IDLE_GEN
        insertlib._rb_arm_idle_timer()
    insertlib._rb_idle_unload(stale)
    assert insertlib._RB_SESSION is sentinel
    insertlib._rb_idle_unload(insertlib._RB_IDLE_GEN)
    assert insertlib._RB_SESSION is None


def test_cuda_dll_dirs_added_once_from_torch_lib(env, monkeypatch, tmp_path):
    """DLL CUDA/cuDNN берём из torch/lib один раз за процесс; хэндлы держим живыми."""
    torch_dir = tmp_path / "torch"
    (torch_dir / "lib").mkdir(parents=True)
    fake_torch = types.ModuleType("torch")
    fake_torch.__file__ = str(torch_dir / "__init__.py")
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    added = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: added.append(p) or p, raising=False)
    monkeypatch.setattr(insertlib, "_CUDA_DLLS_DONE", False)
    monkeypatch.setattr(insertlib, "_CUDA_DLL_DIRS", [])
    insertlib._cuda_dll_dirs()
    insertlib._cuda_dll_dirs()
    assert added == [str(torch_dir / "lib")]
    assert insertlib._CUDA_DLL_DIRS == [str(torch_dir / "lib")]


def test_cuda_dll_dirs_skipped_without_add_dll_directory(env, monkeypatch):
    """Не Windows: `os.add_dll_directory` нет — ничего не делаем и не падаем."""
    monkeypatch.delattr(os, "add_dll_directory", raising=False)
    monkeypatch.setattr(insertlib, "_CUDA_DLLS_DONE", False)
    insertlib._cuda_dll_dirs()
    assert insertlib._CUDA_DLLS_DONE is True


def test_cuda_dll_dirs_survive_missing_torch(env, monkeypatch):
    """Нет torch — это предупреждение в журнал, а не падение: откат на CPU всё равно случится."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(os, "add_dll_directory", lambda p: p, raising=False)
    monkeypatch.setattr(insertlib, "_CUDA_DLLS_DONE", False)
    insertlib._cuda_dll_dirs()
    assert len(env.log.warnings) == 1
