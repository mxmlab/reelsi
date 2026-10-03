# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шумодав считается один раз, VST — только вживую, запекание — только на сборке.

Тесты проверяют контракты:

* смена VST (добавили плагин, поменяли state) не вызывает `_denoise_any` второй
  раз (кеш дорожки шумодава);
* `/api/voicefx_bake` для превью не запускает цепочку VST вовсе;
* рендер цепочки: подменный плагин «только стерео» получает 2 канала (моно);
* незагружаемый плагин пропущен, остальные применены, причина с именем в ответе;
* сборка AE / XML / DRP / черновик берут `.voice.wav`, запечённый С плагинами
  под текущие настройки; сохранение профиля его не печёт;
* нарезка берёт голос ПОСЛЕ ВСЕЙ цепочки спикера — шумодав и плагины (та же
  дверь, что у вывода: `render_cached`).

Живые deep-filter, ffmpeg, VST и After Effects НЕ запускаются — только подмены.

Запуск:  python -m pytest tests/test_voicefx_vr.py -q
"""
import os
import shutil
import subprocess
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import speakers, voicefx, voicefx_render  # noqa: E402
from core.project_file import write_project  # noqa: E402


def _noop(*a, **k):
    """emit-заглушка: строки лога в тестах не нужны."""
    return None


# --------------------------------------------------------------------------- #
# Изоляция
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path, monkeypatch):
    """Запечённые треки — во временный каталог, не в боевую `_voicefx/`."""
    d = tmp_path / "_voicefx"
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(d))
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    monkeypatch.setenv("REELSI_VST3_SCAN", str(d / "vst3_scan.json"))
    return d


@pytest.fixture()
def speakers_dir(tmp_path, monkeypatch):
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


@pytest.fixture()
def cam1(tmp_path):
    """Файл камеры 1: содержимое не читается (ffmpeg подменён) — важно наличие."""
    p = tmp_path / "cam1.mp4"
    p.write_bytes(b"fake camera file")
    return str(p)


@pytest.fixture()
def xml_path(tmp_path):
    """XML нарезки."""
    p = tmp_path / "clip.xml"
    p.write_text("<x/>", encoding="utf-8")
    return str(p)


def _write_wav(path, seconds, rate=48000, channels=1):
    """Настоящий WAV (не заглушка байтами)."""
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(seconds * rate) * channels * 2))


def _fake_subprocess(calls, src_seconds=1.0, dn_seconds=0.97):
    """Подмена subprocess.run: ffmpeg «пишет» WAV, deep-filter — файл в -o."""
    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        program = os.path.basename(cmd[0]).lower()
        if "ffmpeg" in program:
            _write_wav(cmd[-1], src_seconds)
        elif "deep-filter" in program:
            out_dir = cmd[cmd.index("-o") + 1]
            os.makedirs(out_dir, exist_ok=True)
            _write_wav(os.path.join(out_dir, os.path.basename(cmd[-1])), dn_seconds)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake_run


def _deep_filter_calls(calls):
    return [c for c in calls if "deep-filter" in os.path.basename(c[0]).lower()]


def _speaker(speakers_dir, key="Голос", **fx):
    speakers.save(key, {"label": key, "voice_fx": fx})
    return speakers.load(key) or {}


def _write_sidecar(xml_path, cam1, speaker=None):
    """Сайдкар нарезки рядом с XML."""
    proj = {"cams": [cam1], "offsets": [0.0], "fps": 60, "cam_return": 2, "scale": 50.4,
            "keep": [[0.0, 1.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(os.path.splitext(xml_path)[0] + ".project.json", proj)


# --------------------------------------------------------------------------- #
# 1. Кеш дорожки шумодава: смена VST не пересчитывает шумодав
# --------------------------------------------------------------------------- #
def test_vst_change_does_not_rerun_denoise(tmp_path, monkeypatch, cam1):
    """Смена VST (добавили плагин, поменяли state) не вызывает _denoise_any второй раз.

    Дорожка шумодава кешируется отдельно по (исходник, настройки шумодава) без VST.
    Смена плагинов и их ручек не должна пересчитывать нейро-шумодав заново.
    """
    denoise_calls = []
    subprocess_calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(subprocess_calls))

    denoiser = tmp_path / "deep-filter.exe"
    denoiser.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(denoiser))

    dn = {"on": True, "engine": "deepfilter", "atten_db": 40}
    # Первый вызов с одним плагином — шумодав считается
    fx1 = {"denoise": dn, "vst": [{"path": "C:/first.vst3", "on": True}]}
    original_denoise = voicefx._denoise_any

    def counted_denoise(*args, **kwargs):
        denoise_calls.append(1)
        return original_denoise(*args, **kwargs)

    monkeypatch.setattr(voicefx, "_denoise_any", counted_denoise)

    # Подменяем _apply_vst чтобы не запускать дочерний процесс
    monkeypatch.setattr(voicefx, "_apply_vst", lambda *a, **k: [])

    out1 = tmp_path / "out1.wav"
    voicefx.render(cam1, fx1, str(out1))
    assert len(denoise_calls) == 1, "шумодав не посчитан с первого раза"

    # Второй вызов с ДРУГИМИ плагинами, но ТЕМ ЖЕ шумодавом — _denoise_any не зовётся,
    # потому что denoise_track берёт из кеша
    denoise_calls.clear()
    fx2 = {"denoise": dn, "vst": [{"path": "C:/second.vst3", "on": True, "state": "AAEE"}]}
    out2 = tmp_path / "out2.wav"
    voicefx.render(cam1, fx2, str(out2))
    assert len(denoise_calls) == 0, "шумодав пересчитан при смене VST (кеш дорожки не сработал)"


def test_denoise_cache_path_excludes_vst(cam1):
    """Ключ кеша дорожки шумодава не зависит от плагинов."""
    dn = {"on": True, "engine": "deepfilter", "atten_db": 40, "mix": 100}
    path_a = voicefx.denoise_cache_path(cam1, dn)
    path_b = voicefx.denoise_cache_path(cam1, dn)
    assert path_a == path_b, "один и тот же шумодав — разные ключи кеша"
    assert path_a.endswith(".wav") and voicefx.VOICEFX_DIR in path_a


# --------------------------------------------------------------------------- #
# 2. /api/voicefx_bake для превью не запускает цепочку VST
# --------------------------------------------------------------------------- #
def test_bake_for_preview_does_not_run_vst(monkeypatch, tmp_path, cam1):
    """Запекание для превью считает ТОЛЬКО дорожку шумодава, без VST.

    Превью играет дорожку шумодава, а плагины — вживую через хост. Запекание
    (`_voice_run_bake`) не должно вызывать `ensure_final_voice`, `render` с VST
    или `_apply_vst`.
    """
    import api.voicefx as api_vfx

    called = {"denoise_track": False, "ensure_final_voice": False, "_apply_vst": False}

    original_denoise_track = voicefx.denoise_track

    def spy_denoise_track(*a, **kw):
        called["denoise_track"] = True
        return original_denoise_track(*a, **kw)

    def spy_ensure(*a, **kw):
        called["ensure_final_voice"] = True
        return ("final.wav", "cache.wav")

    def spy_apply_vst(*a, **kw):
        called["_apply_vst"] = True
        return []

    monkeypatch.setattr(voicefx, "denoise_track", spy_denoise_track)
    monkeypatch.setattr(voicefx, "ensure_final_voice", spy_ensure)
    monkeypatch.setattr(voicefx, "_apply_vst", spy_apply_vst)

    # Подменяем subprocess (ffmpeg, deep-filter)
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    denoiser = tmp_path / "deep-filter.exe"
    denoiser.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(denoiser))

    xml = str(tmp_path / "clip.xml")
    fx = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
          "vst": [{"path": "C:/reverb.vst3", "on": True}]}

    # Имитируем _voice_reserve: занимаем замок и ставим слот
    with api_vfx.VOICELOCK:
        slot = api_vfx._voice_slot(xml)
        api_vfx._voice_set(slot, src=cam1, fx=fx, want=True, running=True, cancelled=False)
        api_vfx.VOICECUR["xml"] = xml
    api_vfx.VOICERUN.acquire(blocking=False)

    api_vfx._voice_run_bake(xml, cam1, fx)

    assert called["denoise_track"], "запекание для превью не вызвало denoise_track"
    assert not called["ensure_final_voice"], "запекание для превью вызвало ensure_final_voice"
    assert not called["_apply_vst"], "запекание для превью запустило цепочку VST"


# --------------------------------------------------------------------------- #
# 3. Рендер цепочки: моно → стерео для стерео-плагинов
# --------------------------------------------------------------------------- #
class _FakeAudioFile:
    """Мини-замена AudioFile: отдаёт моно или стерео тишину, записывает данные."""

    def __init__(self, target, mode="r", samplerate=0, num_channels=0, format=None):
        self.target = target
        self.mode = mode
        self.samplerate = float(samplerate or 48000)
        self.num_channels = int(num_channels or 1)
        self.frames = int(self.samplerate * 0.5) if mode == "r" else 0
        self.pos = 0
        self.written_shapes = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def tell(self):
        return self.pos

    def read(self, n):
        n = max(0, min(int(n), self.frames - self.pos))
        self.pos += n
        return np.zeros((self.num_channels, n), dtype="float32")

    def write(self, data):
        arr = np.asarray(data, dtype="float32")
        self.written_shapes.append(tuple(arr.shape))
        if hasattr(self.target, "write"):
            self.target.write(arr.tobytes())


class _FakeBoard:
    """Подменный board: записывает формы входных блоков."""
    def __init__(self):
        self.input_shapes = []

    def process(self, chunk, sr, reset=True):
        self.input_shapes.append(tuple(chunk.shape))
        return chunk


class _FakePB:
    """Подменный pedalboard: записывает загрузки и создания board."""
    def __init__(self, *, mono_channels=1):
        self.loaded = []
        self.boards = []
        self.mono_channels = mono_channels
        self.io = SimpleNamespace(AudioFile=lambda *a, **k: _FakeAudioFile(*a, num_channels=self.mono_channels, **{kk: vv for kk, vv in k.items() if kk != "num_channels"}))

    def load_plugin(self, path, plugin_name=None):
        self.loaded.append((path, plugin_name))
        return SimpleNamespace(path=path, name=plugin_name, raw_state=b"")

    def Pedalboard(self, plugins):
        board = _FakeBoard()
        self.boards.append(board)
        return board


def test_render_mono_source_gets_stereo_for_chain(tmp_path):
    """Моно-исходник дублируется в 2 канала перед цепочкой — стерео-плагины не падают.

    ValhallaVintageVerb падал: 'Plugin does not support 1-channel output'. Теперь
    моно дублируется в стерео перед обработкой, а результат усредняется обратно в
    моно для записи.
    """
    src = str(tmp_path / "mono.wav")
    out = str(tmp_path / "out.wav")
    _write_wav(src, 0.5, channels=1)

    board = _FakeBoard()

    # Создаём подменный pb с моно-файлом (1 канал)
    class MonoAudioFile(_FakeAudioFile):
        def __init__(self, target, mode="r", samplerate=0, num_channels=0, format=None):
            super().__init__(target, mode, samplerate,
                             num_channels=1 if mode == "r" else num_channels,
                             format=format)

    pb = SimpleNamespace(
        io=SimpleNamespace(AudioFile=MonoAudioFile),
    )

    voicefx_render.process_file(board, src, out, pb)

    # Плагин ОБЯЗАН получить 2 канала (стерео)
    assert board.input_shapes, "board.process не вызван"
    for shape in board.input_shapes:
        assert shape[0] == 2, f"плагин получил {shape[0]} каналов вместо 2 (стерео)"


# --------------------------------------------------------------------------- #
# 4. Незагружаемый плагин пропущен, остальные применены
# --------------------------------------------------------------------------- #
def test_broken_plugin_skipped_others_applied(tmp_path):
    """Незагружаемый плагин пропускается, причина с именем в ответе, остальные работают."""
    src = str(tmp_path / "in.wav")
    _write_wav(src, 0.5)

    chain = [
        {"path": "C:/good.vst3", "name": "GoodPlugin", "state_b64": "", "on": True},
        {"path": "C:/broken.vst3", "name": "BrokenPlugin", "state_b64": "", "on": True},
        {"path": "C:/also_good.vst3", "name": "", "state_b64": "", "on": True},
    ]

    class PB:
        def __init__(self):
            self.loaded = []
            self.io = SimpleNamespace(AudioFile=_FakeAudioFile)

        def load_plugin(self, path, plugin_name=None):
            if "broken" in path.lower():
                raise ImportError("unsupported plugin format or scan failure")
            self.loaded.append((path, plugin_name))
            return SimpleNamespace(path=path, name=plugin_name, raw_state=b"")

        def Pedalboard(self, plugins):
            return _FakeBoard()

    pb = PB()
    old_emit = voicefx_render.emit
    emitted = []
    voicefx_render.emit = lambda msg, **kw: emitted.append(msg.format(**kw) if kw else msg)
    try:
        plugins, skipped = voicefx_render.load_chain(pb, chain)
    finally:
        voicefx_render.emit = old_emit

    # Хороших плагинов загружено 2
    assert len(plugins) == 2
    assert len(pb.loaded) == 2

    # Пропущен один — с именем и причиной
    assert len(skipped) == 1
    assert skipped[0]["name"] == "BrokenPlugin"
    assert "unsupported" in skipped[0]["reason"].lower() or "scan failure" in skipped[0]["reason"].lower()

    # Причина с именем ушла в лог
    assert any("BrokenPlugin" in line and "пропущен" in line for line in emitted)


def test_all_plugins_broken_copies_source(tmp_path):
    """Все плагины не загрузились — выход равен входу, ошибки нет."""
    src = str(tmp_path / "in.wav")
    out = str(tmp_path / "out.wav")
    _write_wav(src, 0.5)

    job = {
        "src": src, "out": out,
        "chain": [{"path": "C:/broken.vst3", "name": "Bad", "state_b64": "", "on": True}],
    }

    class PB:
        def __init__(self):
            self.io = SimpleNamespace(AudioFile=_FakeAudioFile)

        def load_plugin(self, path, plugin_name=None):
            raise ImportError("bad plugin")

        def Pedalboard(self, plugins):
            return _FakeBoard()

    old_pb = voicefx_render.pedalboard
    voicefx_render.pedalboard = lambda: PB()
    old_emit = voicefx_render.emit
    voicefx_render.emit = _noop
    try:
        frames, skipped = voicefx_render.apply_job(job)
    finally:
        voicefx_render.pedalboard = old_pb
        voicefx_render.emit = old_emit

    assert os.path.isfile(out), "выход не создан при всех пропущенных плагинах"
    assert skipped and skipped[0]["name"] == "Bad"


# --------------------------------------------------------------------------- #
# 5. Сборка берёт .voice.wav С плагинами; сохранение профиля не печёт
# --------------------------------------------------------------------------- #
def test_clip_voice_wav_bakes_with_vst(xml_path, cam1, speakers_dir, tmp_path, monkeypatch):
    """clip_voice_wav (дверь читателей сборки) печёт голос С плагинами.

    Он зовёт ensure_final_voice → render_cached → render, который включает
    цепочку VST. Проверяем, что render_cached получает настройки с включёнными
    плагинами.
    """
    _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
             vst=[{"path": "C:/reverb.vst3", "on": True}])
    _write_sidecar(xml_path, cam1, speaker="Голос")

    seen = {}
    rendered = tmp_path / "cache.wav"
    rendered.write_bytes(b"RIFF processed voice")

    def fake_render_cached(src, fx, emit=_noop, progress=None, cancelled=None,
                           pid_of=None, skipped=None):
        seen["fx"] = voicefx.normalize_fx(fx)
        seen["src"] = src
        return str(rendered)

    monkeypatch.setattr(voicefx, "render_cached", fake_render_cached)
    monkeypatch.setattr(voicefx, "_copy_atomic", lambda s, d: shutil.copyfile(s, d))

    result = voicefx.clip_voice_wav(xml_path, emit=_noop)
    assert result, "clip_voice_wav вернул пустую строку"
    assert seen["src"] == cam1
    # Настройки ДОЛЖНЫ содержать включённые VST
    vst = seen["fx"]["vst"]
    assert any(p["on"] and p["path"] == "C:/reverb.vst3" for p in vst), \
        "clip_voice_wav не передал включённые VST в render_cached"


def test_save_speaker_does_not_bake(xml_path, cam1, speakers_dir, monkeypatch):
    """Сохранение профиля спикера НЕ печёт `.voice.wav`.

    Запекание происходит только при запросе сборки (clip_voice_wav,
    final_voice_for_build, ensure_final_voice) или при запекании для превью.
    Простое сохранение профиля — это JSON на диск, без рендера.
    """
    monkeypatch.setattr(voicefx, "render_cached",
                        lambda *a, **k: pytest.fail("сохранение профиля запустило рендер"))
    monkeypatch.setattr(voicefx, "denoise_track",
                        lambda *a, **k: pytest.fail("сохранение профиля запустило шумодав"))

    # Просто сохраняем профиль — никаких рендеров
    speakers.save("Голос", {"label": "Голос",
                            "voice_fx": {"denoise": {"on": True, "engine": "deepfilter",
                                                     "atten_db": 40},
                                         "vst": [{"path": "C:/p.vst3", "on": True,
                                                   "name": "", "state": ""}]}})
    assert os.path.isfile(os.path.join(str(speakers_dir), "Голос.json"))


# --------------------------------------------------------------------------- #
# 6. Нарезка берёт ВСЮ цепочку спикера — шумодав И плагины
# --------------------------------------------------------------------------- #
def test_cut_bakes_chain_like_output(speakers_dir, cam1, tmp_path, monkeypatch):
    """Нарезка слушает голос ПОСЛЕ ВСЕЙ цепочки: шумодав → плагины → analysis_wav.

    Решение владельца (02.10.2026): «до нарезки я просил накладывать всё, что стоит
    у спикера, для более правильного определения слов». Цепочку собирает ТА ЖЕ
    дверь, что и вывод (`render_cached`), поэтому здесь проверяется, что плагины
    пекутся ПОВЕРХ дорожки шумодава, а не вместо неё.
    """
    prof = _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
                    vst=[{"path": "C:/reverb.vst3", "on": True}])
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw camera audio")
    seen = {"vst": [], "analysis": []}

    def spy_denoise_track(src, dn, start=0.0, dur=None, emit=_noop,
                          cancelled=None, progress=None, pid_of=None):
        seen["denoise"] = (src, dn)
        track = tmp_path / "track.wav"
        track.write_bytes(b"RIFF denoise track")   # настоящая дорожка: её копирует печь
        return str(track)

    def fake_vst(src, plugins, out, emit, cancelled=None):
        seen["vst"].append((src, [p["path"] for p in plugins]))
        shutil.copyfile(src, out)
        return []

    monkeypatch.setattr(voicefx, "denoise_track", spy_denoise_track)
    monkeypatch.setattr(voicefx, "_apply_vst", fake_vst)
    monkeypatch.setattr(voicefx, "analysis_wav",
                        lambda full, dst, gain_db=0.0, emit=_noop:
                        seen["analysis"].append(full) or dst)

    result = voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop)
    assert result is True, "нарезка не применила обработку"
    assert seen["denoise"][0] == cam1, "нарезка обрабатывает не камеру 1"
    assert seen["vst"] == [(str(tmp_path / "track.wav"), ["C:/reverb.vst3"])], \
        "плагины не испечены поверх дорожки шумодава"
    assert seen["analysis"] == [voicefx.cache_path(cam1, prof["voice_fx"])], \
        "нарезка услышала не всю цепочку"


def test_cut_with_only_vst_bakes_raw_audio(speakers_dir, cam1, tmp_path, monkeypatch):
    """Шумодав выключен, плагины включены — цепочка идёт по СЫРОМУ звуку камеры.

    «Шумодав выключен» значит «звук камеры как есть», а не «обработки нет вовсе»:
    плагины у спикера стоят, и нарезка обязана слышать их так же, как ролик.
    """
    prof = _speaker(speakers_dir, denoise={"on": False},
                    vst=[{"path": "C:/reverb.vst3", "on": True}])
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw camera audio")
    seen = {"vst": [], "analysis": []}

    def spy_denoise_track(src, dn, start=0.0, dur=None, emit=_noop,
                          cancelled=None, progress=None, pid_of=None):
        seen["denoise"] = (src, dn)
        assert dn["on"] is False, "шумодав включён при выключенном в профиле"
        raw = tmp_path / "raw.wav"
        raw.write_bytes(b"RIFF raw camera audio")   # «дорожка» без шумодава = сырой звук
        return str(raw)

    def fake_vst(src, plugins, out, emit, cancelled=None):
        seen["vst"].append(src)
        shutil.copyfile(src, out)
        return []

    monkeypatch.setattr(voicefx, "denoise_track", spy_denoise_track)
    monkeypatch.setattr(voicefx, "_apply_vst", fake_vst)
    monkeypatch.setattr(voicefx, "analysis_wav",
                        lambda full, dst, gain_db=0.0, emit=_noop:
                        seen["analysis"].append(full) or dst)

    result = voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop)
    assert result is True, "нарезка обрабатывает звук без шумодава"
    assert seen["vst"] == [str(tmp_path / "raw.wav")], \
        "плагины испечены не по сырому звуку камеры"
    assert seen["analysis"] == [voicefx.cache_path(cam1, prof["voice_fx"])], \
        "нарезка услышала не цепочку плагинов"


# --------------------------------------------------------------------------- #
# 7. render_cached: без VST → denoise_track, с VST → cache_path
# --------------------------------------------------------------------------- #
def test_render_cached_no_vst_returns_denoise_track(tmp_path, monkeypatch, cam1):
    """Без включённых VST — отдаётся ДОРОЖКА ШУМОДАВА напрямую."""
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    denoiser = tmp_path / "deep-filter.exe"
    denoiser.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(denoiser))

    fx = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
          "vst": [{"path": "C:/p.vst3", "on": False}]}
    result = voicefx.render_cached(cam1, fx)

    norm = voicefx.normalize_fx(fx)
    expected = voicefx.denoise_cache_path(cam1, norm["denoise"])
    assert result == expected, "render_cached без VST не вернул denoise_cache_path"


def test_render_cached_with_vst_returns_cache_path(tmp_path, monkeypatch, cam1):
    """С включёнными VST — итог под ключом cache_path (включая плагины)."""
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    denoiser = tmp_path / "deep-filter.exe"
    denoiser.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(denoiser))
    monkeypatch.setattr(voicefx, "_apply_vst",
                        lambda src, plugins, out, emit, cancelled=None:
                        (shutil.copyfile(src, out), [])[1])

    fx = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
          "vst": [{"path": "C:/p.vst3", "on": True, "name": "", "state": ""}]}
    result = voicefx.render_cached(cam1, fx)

    expected = voicefx.cache_path(cam1, fx)
    assert result == expected, "render_cached с VST не вернул cache_path"


# --------------------------------------------------------------------------- #
# 8. Ошибки — человеческие (имя плагина + причина, не «код возврата 1»)
# --------------------------------------------------------------------------- #
def test_error_message_includes_plugin_name_and_reason():
    """Незагружаемый плагин: ошибка содержит ИМЯ плагина и причину, не просто код."""
    chain = [{"path": "C:/Bertom_DenoiserClassic.vst3", "name": "", "state_b64": "", "on": True}]

    class PB:
        def load_plugin(self, path, plugin_name=None):
            raise ImportError("unsupported plugin format or scan failure")

    old_emit = voicefx_render.emit
    emitted = []
    voicefx_render.emit = lambda msg, **kw: emitted.append(msg.format(**kw) if kw else msg)
    try:
        _plugins, skipped = voicefx_render.load_chain(PB(), chain)
    finally:
        voicefx_render.emit = old_emit

    assert len(skipped) == 1
    assert skipped[0]["name"] == "Bertom_DenoiserClassic", \
        "имя плагина не в ответе — человек не узнает, какой именно"
    assert "unsupported" in skipped[0]["reason"].lower(), \
        "причина не в ответе — вместо неё было бы 'код возврата 1'"
    assert any("Bertom_DenoiserClassic" in line for line in emitted), \
        "имя плагина не в логе"
