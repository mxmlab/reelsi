# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Замок видеокарты берут только NVIDIA-участки: остальные кодеки чужой голос не ждут.

Пока идёт обработка голоса, замок видеокарты занят целиком: RoFormer считает на CUDA
и держит ~3.4 ГБ VRAM (`core/voicefx`, `core/gpulock`). Рядом с ним задача с кодеком
Quick Sync, AMF или «Процессор» карту NVIDIA не занимает вовсе — и ждать ей нечего.
Жалоба владельца ровно об этом: «выбран другой кодек, а он ждёт и не делает».

Три пары «запекание голоса + X»:

1. рендер без AE — `core/render_job.run_render_builtin` (кодек мастера, `core/webrender`);
2. черновой mp4 — `core/draftrender.render_draft` (кодек черновика);
3. сборка прокси камер — `core/draftrender.build_preview_proxy` / `build_render_proxy`.

В каждой паре: кодек не-NVIDIA → работа идёт при ЗАНЯТОМ замке; NVENC → ждёт замка
(VRAM под NVENC остаётся защищённой: на этой машине сессия hevc_nvenc держит ~348 МиБ).

Запуск: python -m pytest tests/test_gpulock_codec.py -q
"""
import contextlib
import os
import sys
import threading
import types
from typing import Any, Iterator, Sequence

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core.draftrender as draftrender  # noqa: E402
import core.render_job as render_job  # noqa: E402
from core import encoders  # noqa: E402
from core.gpulock import codec_gpu_lock, gpu_lock  # noqa: E402
from core.render_job import RenderJob  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

# Сколько ждём работу, которой ждать НЕ надо: поддельный рендер — это запись файла, ему
# хватает миллисекунд. Потолок нужен откату: с безусловным замком работа встанет, и
# тест обязан упасть, а не висеть (общий потолок теста — tests/pytest.ini timeout).
NO_WAIT_SEC = 12.0
# Сколько убеждаемся, что NVENC-работа СТОИТ на замке, прежде чем его отпустить.
WAITS_AT_LEAST_SEC = 0.5


@pytest.fixture(autouse=True)
def gpu_lock_file_in_tmp(tmp_path, monkeypatch):
    """Файл замка видеокарты — в tmp_path: боевой `job.lock.gpu` в корне не трогаем."""
    import core.gpulock as gpulock
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))


@contextlib.contextmanager
def voice_bake_holds_gpu_lock() -> Iterator[None]:
    """«Идёт запекание голоса»: замок занят — так его держит RoFormer (`core/voicefx`).

    Замок берётся в отдельном потоке настоящим `gpu_lock`: он межпроцессный и
    нереентерабельный, поэтому занятый замок виден и внутри процесса (второй хэндл
    на тот же файл получает отказ и на Windows, и на Linux).
    """
    ready, stop = threading.Event(), threading.Event()

    def hold() -> None:
        with gpu_lock("шумодав RoFormer", emit=lambda *a, **k: None):
            ready.set()
            stop.wait(60.0)

    t = threading.Thread(target=hold, daemon=True, name="fake-voice-bake")
    t.start()
    assert ready.wait(10.0), "замок видеокарты не взялся — проверять нечего"
    try:
        yield
    finally:
        stop.set()
        t.join(10.0)


class Waiter:
    """Работа в отдельном потоке: жива ли она ещё — то есть ждёт ли она."""

    def __init__(self, what: str, target: Any, args: Sequence[Any] = (),
                 kwargs: dict[str, Any] | None = None):
        self.what = what
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(target, list(args),
                                                               kwargs or {}),
                                       daemon=True, name="waiter")
        self.thread.start()

    def _run(self, target: Any, args: list[Any], kwargs: dict[str, Any]) -> None:
        try:
            target(*args, **kwargs)
        except BaseException as e:                       # noqa: BLE001 — уносим в тест
            self.error = e

    def wait(self, seconds: float) -> None:
        """Дождаться конца работы; не дождались — падаем с внятной причиной."""
        self.thread.join(seconds)
        assert not self.thread.is_alive(), (
            f"{self.what} ждёт замок видеокарты, хотя карту NVIDIA не занимает "
            f"(не закончил за {seconds:g} с)")
        if self.error is not None:
            raise self.error

    def still_waiting(self, seconds: float) -> None:
        """Убедиться, что работа СТОИТ (берёт замок и ждёт его освобождения)."""
        self.thread.join(seconds)
        assert self.thread.is_alive(), (
            f"{self.what} прошёл, не дождавшись замка видеокарты: NVENC карту занимает, "
            f"и его замок бережёт VRAM")


def _job() -> RenderJob:
    """Джоб рендера в том виде, в каком его читает `run_render_job`."""
    return RenderJob({"running": True, "done": False, "log": [], "pct": None, "cur": "",
                      "ae": "", "out_dir": "", "result": [], "failed": [], "cancel": False,
                      "items": [], "eta": None, "eta_phase": None, "eta_total": None,
                      "eta_preliminary": False, "stage_label": None, "stage_done": 0,
                      "stage_total": 0})


def _force_codec(monkeypatch, family: str) -> encoders.Choice:
    """Подменить выбор кодека мастера: проба кодека — это запуск ffmpeg, в тестах он лишний."""
    choice = encoders.Choice(family=family,
                             args=encoders.codec_args(encoders.codec_name(family, "master"),
                                                      "master"),
                             label=encoders.LABELS.get(family, family))
    monkeypatch.setattr(encoders, "pick",
                        lambda purpose, setting=None, prober=None: choice)
    return choice


def _fake_webrender(monkeypatch) -> dict[str, Any]:
    """Подменить сам рендер: проверяем замок, а не Chrome и ffmpeg."""
    seen: dict[str, Any] = {}

    def render(xml: str, out: str, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        seen["xml"] = xml
        with open(out, "wb") as f:
            f.write(b"mp4")
        return {"ok": True, "out": out, "frames": 1, "fps": 60, "w": 1080, "h": 1920}

    import core.webrender as webrender
    monkeypatch.setattr(webrender, "render", render)
    return seen


def _fake_ff(monkeypatch) -> list[str]:
    """Подменить ffmpeg: пишет «готовый» файл и запоминает кодек захода."""
    used: list[str] = []

    def run(cmd: list[str], **kw: Any) -> Any:
        used.append(draftrender._codec_name(cmd))
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(draftrender, "_run_ff", run)
    return used


def _draft_prepared(tmp_path, monkeypatch, family: str) -> tuple[str, list[str]]:
    """Черновик по подделанному EDL и ffmpeg: кодек — по семейству `family`."""
    from core import xml2ae
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(xml2ae, "virtual_edl", lambda p, ncams=None: {
        "segs": [{"ci": 0, "ts": 0.0, "te": 1.0, "src": 0.0}],
        "audio": [{"ci": 0, "ts": 0.0, "te": 1.0, "src": 0.0}],
        "cams": [{"path": str(clip)}],
        "w": 1080, "h": 1920, "words": []})
    monkeypatch.setattr(draftrender, "hw_encoder",
                        lambda refresh=False: encoders.codec_name(family, "draft"))
    return str(xml), _fake_ff(monkeypatch)


def _proxy_prepared(tmp_path, monkeypatch, family: str) -> tuple[str, list[str]]:
    """Прокси камеры по подделанному ffmpeg: кодек — по семейству `family`."""
    src = tmp_path / "cam1.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(draftrender, "_display_dims", lambda s: (1920, 1080, False))
    monkeypatch.setattr(draftrender, "_src_fps", lambda s: 25.0)
    monkeypatch.setattr(draftrender, "hw_encoder",
                        lambda refresh=False: encoders.codec_name(family, "draft"))
    return str(src), _fake_ff(monkeypatch)


def _label(codec: str | None) -> str:
    """Подпись семейства для сообщений: у «cpu» кодека нет, он и есть процессор."""
    return "CPU" if codec is None else str(codec)


# --------------------------------------------------------------------------- #
# Дверь замка: кого она пускает
# --------------------------------------------------------------------------- #
def test_codec_gpu_lock_takes_the_lock_only_for_nvidia(monkeypatch):
    """NVENC и «nvidia» — замок; Quick Sync, AMF, VideoToolbox и процессор — нет."""
    taken: list[str] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Iterator[None]:
        taken.append(label)
        yield

    import core.gpulock as gpulock
    monkeypatch.setattr(gpulock, "gpu_lock", fake_lock)

    for codec in ("hevc_nvenc", "h264_nvenc", "nvidia"):
        with codec_gpu_lock(codec, "проба"):
            pass
    assert taken == ["проба", "проба", "проба"], f"NVENC не взял замок: {taken}"

    taken.clear()
    for codec in ("hevc_qsv", "h264_qsv", "h264_amf", "hevc_amf", "h264_videotoolbox",
                  "libx264", "libx265", "cpu"):
        with codec_gpu_lock(codec, "проба"):
            pass
    assert taken == [], f"не-NVIDIA кодек взял замок видеокарты: {taken}"


def test_roformer_bake_still_holds_the_gpu_lock(monkeypatch, tmp_path):
    """Запекание голоса замок БЕРЁТ — иначе пары «голос + X» проверять не на чем."""
    from core import voicefx, voicefx_sep
    taken: list[str] = []

    @contextlib.contextmanager
    def fake_lock(label: str = "", emit: Any = None) -> Iterator[None]:
        taken.append(label)
        yield

    monkeypatch.setattr(voicefx, "gpu_lock", fake_lock)
    monkeypatch.setattr(voicefx_sep, "require", lambda engine: None)
    monkeypatch.setattr(voicefx_sep, "separate_cmd", lambda *a, **k: ["separate"])
    monkeypatch.setattr(voicefx, "_run_child",
                        lambda *a, **k: voicefx._ChildRun(answers=[], errors=[],
                                                          returncode=1))
    with pytest.raises(ReelsiError):
        voicefx._denoise_roformer(str(tmp_path / "in.wav"), "roformer", str(tmp_path),
                                  lambda *a, **k: None)
    assert taken == ["шумодав RoFormer"], f"RoFormer не взял замок видеокарты: {taken}"


# --------------------------------------------------------------------------- #
# Пара 1: запекание голоса + рендер без AE
# --------------------------------------------------------------------------- #
def _builtin_render(tmp_path, monkeypatch, family: str) -> tuple[Any, dict[str, Any]]:
    job = _job()
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    _force_codec(monkeypatch, family)
    seen = _fake_webrender(monkeypatch)
    return job, {"job": job, "norm": [{"xml_path": str(xml)}], "outdir": None,
                 "render_dir": str(tmp_path), "engine": "builtin",
                 "host": "127.0.0.1:5099", "seen": seen}


def test_builtin_render_with_cpu_codec_does_not_wait_for_voice(tmp_path, monkeypatch):
    """«Процессор» в настройках — рендер без AE идёт при занятом замке видеокарты."""
    job, ctx = _builtin_render(tmp_path, monkeypatch, "cpu")
    seen = ctx.pop("seen")
    with voice_bake_holds_gpu_lock():
        Waiter("рендер без AE (кодек «Процессор»)", render_job.run_render_job,
               [job, ctx["norm"], ctx["outdir"], ctx["render_dir"]],
               {"engine": ctx["engine"], "host": ctx["host"]}).wait(NO_WAIT_SEC)
    assert seen.get("codec") is not None, "кодек мастера не доехал до рендера"
    assert seen["codec"].args[1] == "libx265", seen["codec"].args
    assert job["result"], f"рендер не собрался: {job['failed']}"


def test_builtin_render_with_nvenc_waits_for_voice(tmp_path, monkeypatch):
    """NVENC карту занимает: рендер без AE ждёт замок, пока голос не досчитан."""
    job, ctx = _builtin_render(tmp_path, monkeypatch, "nvidia")
    ctx.pop("seen")
    with voice_bake_holds_gpu_lock():
        waiter = Waiter("рендер без AE (NVENC)", render_job.run_render_job,
                        [job, ctx["norm"], ctx["outdir"], ctx["render_dir"]],
                        {"engine": ctx["engine"], "host": ctx["host"]})
        waiter.still_waiting(WAITS_AT_LEAST_SEC)
    waiter.wait(NO_WAIT_SEC)
    assert job["result"], f"рендер не собрался после освобождения замка: {job['failed']}"


# --------------------------------------------------------------------------- #
# Пара 2: запекание голоса + черновой mp4
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("family,waits", [("cpu", False), ("nvidia", True)])
def test_draft_render_waits_only_for_nvidia(tmp_path, monkeypatch, family, waits):
    """Черновик: кодек не-NVIDIA — при занятом замке, NVENC — ждёт его."""
    xml, used = _draft_prepared(tmp_path, monkeypatch, family)

    def run() -> None:
        draftrender.render_draft(xml, use_proxy=False, emit=lambda *a, **k: None)

    with voice_bake_holds_gpu_lock():
        waiter = Waiter(f"черновик ({_label(encoders.codec_name(family, 'draft'))})", run)
        if waits:
            waiter.still_waiting(WAITS_AT_LEAST_SEC)
        else:
            waiter.wait(NO_WAIT_SEC)
    if waits:
        waiter.wait(NO_WAIT_SEC)
    assert used, "ffmpeg не звался — проверять нечего"
    assert used[0] == encoders.codec_name(family, "draft"), used


# --------------------------------------------------------------------------- #
# Пара 3: запекание голоса + сборка прокси камер
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("family,waits", [("cpu", False), ("nvidia", True)])
def test_preview_proxy_waits_only_for_nvidia(tmp_path, monkeypatch, family, waits):
    """Прокси камер: кодек не-NVIDIA — при занятом замке, NVENC — ждёт его."""
    src, used = _proxy_prepared(tmp_path, monkeypatch, family)
    dst = str(tmp_path / "pv_cam1.mp4")

    def run() -> None:
        draftrender.build_preview_proxy(src, dst, height=720, emit=lambda *a, **k: None)

    with voice_bake_holds_gpu_lock():
        waiter = Waiter(f"превью-прокси ({_label(encoders.codec_name(family, 'draft'))})",
                        run)
        if waits:
            waiter.still_waiting(WAITS_AT_LEAST_SEC)
        else:
            waiter.wait(NO_WAIT_SEC)
    if waits:
        waiter.wait(NO_WAIT_SEC)
    assert os.path.isfile(dst), "прокси не собрался"
    assert used and used[0] == encoders.codec_name(family, "draft"), used


def test_render_proxy_is_cpu_only_and_takes_no_lock(tmp_path, monkeypatch):
    """Прокси рендера без AE (ключевой кадр каждый) — всегда x264: замка не берёт."""
    src, used = _proxy_prepared(tmp_path, monkeypatch, "nvidia")
    dst = str(tmp_path / "pv_cam1_allintra.mp4")

    def run() -> None:
        draftrender.build_render_proxy(src, dst, height=720, emit=lambda *a, **k: None)

    with voice_bake_holds_gpu_lock():
        Waiter("all-intra прокси", run).wait(NO_WAIT_SEC)
    assert os.path.isfile(dst), "прокси не собрался"
    assert used == ["libx264"], f"all-intra прокси закодирован не процессором: {used}"
