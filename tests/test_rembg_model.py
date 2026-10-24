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
import threading
import time
import types
from types import SimpleNamespace

import pytest
from flask import Flask
from PIL import Image

from core import insertlib, model_service, rembg_run
from core.aicut import config as aicut_config
import api
from core import aicut, device
from core.umsg import ReelsiError, umsg

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


# Провайдеры машины с onnxruntime-gpu: CUDA с опциями (cuDNN и потолок арены u2net) первым,
# CPU запасным. Потолок u2net — 1 ГиБ (замер 2026-10-23, см. `core/rembg_run.py`).
_CUDA_OPTS = {"cudnn_conv_use_max_workspace": "1", "cudnn_conv_algo_search": "HEURISTIC",
              "arena_extend_strategy": "kSameAsRequested",
              "gpu_mem_limit": str(1024 * 1024 * 1024)}
_CUDA_PROVIDERS = [("CUDAExecutionProvider", _CUDA_OPTS), "CPUExecutionProvider"]


def _no_service(img, model):
    """Заглушка сервиса моделей: сервиса нет — `insertlib.remove_bg` уходит на локальный путь."""
    raise model_service.ServiceUnavailable("тест: сервиса моделей нет")


def _uses_cuda(providers):
    """Первый провайдер — CUDA; в списке он идёт кортежем (имя, опции)."""
    first = providers[0]
    return (first[0] if isinstance(first, tuple) else first) == "CUDAExecutionProvider"


def _provider_names(providers):
    """Имена провайдеров без опций — так их отдаёт настоящая сессия onnxruntime."""
    return [p[0] if isinstance(p, tuple) else p for p in providers]


class _FakeRembg:
    """Заглушка rembg: что создавали и чем вырезали — записываем, onnx-модель не грузим.
    cuda_broken — сессия на видеокарте падает (как без нужных DLL), на CPU работает.
    Сессия отдаёт `inner_session.get_providers()` — ровно как настоящая: по нему
    `rembg_run` проверяет, на чём сессия открылась на самом деле."""

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
        inner = SimpleNamespace(get_providers=lambda: _provider_names(providers))
        return SimpleNamespace(name=name, inner_session=inner)

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
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", True)
    log = _RecordingLog()
    monkeypatch.setattr(rembg_run, "log", log)
    # Устройство — процессор: тесты не зависят от живой карты машины (VRAM не меряем вовсе).
    monkeypatch.setenv("REELSI_MODEL_SERVICE_DEVICE", "cpu")
    # Сервиса моделей в этих тестах нет: локальный путь снимает фон сам. Настоящий сервис
    # отсюда не поднимаем (тест на сервис — в своём файле, с подменой его ответа).
    monkeypatch.setattr(model_service, "remove_bg", _no_service)
    fake = _FakeRembg()
    mod = types.ModuleType("rembg")
    mod.new_session = fake.new_session
    mod.remove = fake.remove
    monkeypatch.setitem(sys.modules, "rembg", mod)
    # onnxruntime тоже заглушка: тесты не зависят от того, стоит ли на машине onnxruntime-gpu.
    ort = types.ModuleType("onnxruntime")
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    yield SimpleNamespace(cfg=cfg_path, fake=fake, tmp=tmp_path, ort=ort, log=log, mp=monkeypatch)
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
    """Машина с onnxruntime-gpu и видеокартой: CUDA в списке провайдеров, устройство — видеокарта,
    свободной VRAM 16 ГиБ (сама карта в тесте не нужна)."""
    env.ort.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    env.mp.setenv("REELSI_MODEL_SERVICE_DEVICE", "cuda")
    env.mp.setattr(device, "nvidia_free_mib", lambda: 16384)


def test_gpu_session_opens_with_cudnn_options(env):
    _gpu(env)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.session_calls == [("u2net", _CUDA_PROVIDERS)]
    assert env.fake.removals == ["u2net"]


def test_cpu_setting_never_asks_vram(env, monkeypatch):
    """«Процессор» в настройке: nvidia-smi не зовём вовсе — меры VRAM на процессоре не нужны."""
    def _must_not_run():
        raise AssertionError("на процессоре VRAM не меряют")
    monkeypatch.setattr(device, "nvidia_free_mib", _must_not_run)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.session_calls == [("u2net", ["CPUExecutionProvider"])]


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
    # Откат на процессор тоже выгружается по простою: таймер заведён (см. test_cpu_session_is_unloaded_after_idle).
    assert insertlib._RB_IDLE_TIMER is not None
    with Image.open(io.BytesIO(out)) as im:
        assert im.mode == "RGBA"


def test_gpu_session_is_unloaded_after_idle(env, monkeypatch):
    _gpu(env)
    monkeypatch.setattr(rembg_run, "IDLE_S", 0.05)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    deadline = time.monotonic() + 5
    while insertlib._RB_SESSION is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert insertlib._RB_SESSION is None
    assert insertlib._RB_SESSION_NAME is None

    # После выгрузки следующий вызов открывает сессию заново.
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net", "u2net"]


def test_cpu_session_is_unloaded_after_idle(env, monkeypatch):
    """Процессорная сессия тоже выгружается по простою: иначе следующая очистка не спросит
    видеокарту, даже когда карта освободилась (раньше процессорная жила до перезапуска)."""
    monkeypatch.setattr(rembg_run, "IDLE_S", 0.05)
    insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert insertlib._RB_IDLE_TIMER is not None
    deadline = time.monotonic() + 5
    while insertlib._RB_SESSION is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert insertlib._RB_SESSION is None
    assert insertlib._RB_SESSION_NAME is None


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
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", False)
    monkeypatch.setattr(rembg_run, "_CUDA_DLL_DIRS", [])
    rembg_run._cuda_dll_dirs()
    rembg_run._cuda_dll_dirs()
    assert added == [str(torch_dir / "lib")]
    assert rembg_run._CUDA_DLL_DIRS == [str(torch_dir / "lib")]


def test_cuda_dll_dirs_skipped_without_add_dll_directory(env, monkeypatch):
    """Не Windows: `os.add_dll_directory` нет — ничего не делаем и не падаем."""
    monkeypatch.delattr(os, "add_dll_directory", raising=False)
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", False)
    rembg_run._cuda_dll_dirs()
    assert rembg_run._CUDA_DLLS_DONE is True


def test_cuda_dll_dirs_survive_missing_torch(env, monkeypatch):
    """Нет torch — это предупреждение в журнал, а не падение: откат на CPU всё равно случится."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(os, "add_dll_directory", lambda p: p, raising=False)
    monkeypatch.setattr(rembg_run, "_CUDA_DLLS_DONE", False)
    rembg_run._cuda_dll_dirs()
    assert len(env.log.warnings) == 1


# --- Сервис моделей: просьба уходит сервису, локальная сессия при этом не открывается --------

def _center_of_bytes(data):
    """Цвет центра PNG из байтов (после обрезки полей квадрат занимает всё изображение)."""
    with Image.open(io.BytesIO(data)) as im:
        return tuple(im.convert("RGBA").getpixel((1, 1)))[:3]


def test_live_service_answers_instead_of_local_session(env, monkeypatch):
    """Сервис жив: его ответ — результат, а локальная сессия здесь НЕ открывается."""
    asked = []

    def service(img, model):
        asked.append(model)
        return {"png": _rgba_png(_COLOR["birefnet-general"]), "gpu": False, "note": ""}

    monkeypatch.setattr(model_service, "remove_bg", service)
    _set_model(env, "birefnet-general")
    out = insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert asked == ["birefnet-general"], "модель уходит сервису по имени"
    assert env.fake.sessions_created == [], "при живом сервисе локальная сессия не открывается"
    assert env.fake.removals == []
    assert _center_of_bytes(out) == _COLOR["birefnet-general"]


def test_service_processor_note_is_logged(env, monkeypatch):
    """Сервис открыл сессию процессором: причина — в журнал тем же сообщением, что и локально."""
    monkeypatch.setattr(model_service, "remove_bg", lambda img, model: {
        "png": _rgba_png(_COLOR["u2net"]), "gpu": False, "note": "нет CUDA в onnxruntime"})
    lines = []
    insertlib.remove_bg(_rgba_png((9, 9, 9)),
                        emit=lambda line="", **kw: lines.append(line.format(**kw)))
    assert any("фон снимается процессором: нет CUDA в onnxruntime" in s for s in lines), lines


def test_ошибка_живого_сервиса_не_уводит_в_локальную_копию(env, monkeypatch):
    """Сервис жив и ответил отказом: ошибка наружу, локальная сессия НЕ открыта.

    Мутация: ловить `ReelsiError` вместе с `ServiceUnavailable` в `insertlib.remove_bg` —
    локальная сессия откроется, тест покраснеет.
    """
    def refused(img, model):
        raise ReelsiError(umsg("rembg_failed", "фон не снялся: {err}", err="арена переполнена"))

    monkeypatch.setattr(model_service, "remove_bg", refused)
    with pytest.raises(ReelsiError):
        insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == [], "отказ живого сервиса не открывает вторую копию модели"


def test_два_прогона_фона_локально_идут_по_одному(env, monkeypatch):
    """Локальный путь: два потока разом — прогоны фона один за раз, как в сервисе.

    Мутация: убрать `_RB_RUN_LOCK` из `_rb_local_png` — одновременных прогонов станет 2, тест покраснеет.
    """
    rembg_mod = sys.modules["rembg"]
    state = {"now": 0, "max": 0}
    state_lock = threading.Lock()

    def remove(data, session=None):
        with state_lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            time.sleep(0.2)
            return _rgba_png(_COLOR[session.name])
        finally:
            with state_lock:
                state["now"] -= 1

    monkeypatch.setattr(rembg_mod, "remove", remove)
    results = []
    errors = []

    def ask():
        try:
            results.append(insertlib.remove_bg(_rgba_png((9, 9, 9))))
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    assert len(results) == 2
    assert state["max"] == 1, "два прогона фона шли разом"


def test_no_service_falls_back_to_local_session(env, monkeypatch):
    """Сервиса нет (`ServiceUnavailable`): фон снимаем здесь, сессия открывается локально."""
    monkeypatch.setattr(model_service, "remove_bg", _no_service)
    out = insertlib.remove_bg(_rgba_png((9, 9, 9)))
    assert env.fake.sessions_created == ["u2net"]
    assert _center_of_bytes(out) == _COLOR["u2net"]
