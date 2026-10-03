# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OX: «Настроить» плагин под живой звук.

Пока окно плагина открыто, фрагмент клипа играет ПО КРУГУ через всю цепочку в
реальном времени — ручка в окне слышна сразу. Это и есть предмет проверки: раньше
окно открывалось без звука, а «Прослушать» считало два WAV заранее.

Живых плагинов, устройств и звука здесь НЕТ и быть не должно: колонки у владельца,
а `pedalboard` тянет JUCE. Подменяются `pedalboard`, `pedalboard.io.AudioStream` и
`subprocess.run`; проверяются контракты — что уезжает в процесс окна, что он играет,
чем заканчивает.

Четыре места, где ошибка не падает, а тихо портит:
* незапущенный поток звука (`AudioStream` без `__enter__`) — `write` возвращается,
  а из колонок не идёт ничего: окно открыто, ручки крутятся в тишине;
* индекс открываемого плагина — считается по ВСЕЙ цепочке, а не по «включённым»:
  иначе у человека с выключенным плагином в середине открывалось бы окно СОСЕДА;
* `reset=False` после первого блока — сброс состояния на каждом блоке превращает
  ревербератор в кашу на каждом стыке;
* фейд на стыке петли — без него на каждом круге щёлкает.

Запуск:  python -m pytest tests/test_voice_live.py -q
"""
import base64
import json
import math
import os
import re
import subprocess
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import voicefx, voicefx_editor as ed  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402

SR = voicefx.SAMPLE_RATE
FRAG_SECONDS = 1.0                  # фрагмент короче блока: стык петли точно в тесте


# --------------------------------------------------------------------------- #
# Подделки: pedalboard, поток звука, WAV и внешние процессы
# --------------------------------------------------------------------------- #
class _FakePlugin:
    """Плагин: помнит состояние, «показывает окно» и отдаёт заданное состояние."""

    def __init__(self, path, name, state=None):
        self.path = path
        self.name = name
        self.raw_state = b""
        self.initial = state
        self.opened = 0

    def __call__(self, chunk, sr, reset=False):
        # Подготовка плагина главным потоком (voicefx_editor._prime): фейку нечего считать
        return chunk

    def show_editor(self):
        self.opened += 1
        if self.initial is not None:
            self.raw_state = self.initial      # «человек покрутил ручки»


class _FakeAudioFile:
    """Мини-замена `pedalboard.io.AudioFile`: отдаёт синус тихо меняющейся частоты.

    Частота низкая нарочно: у настоящего голоса соседние сэмплы близки, и тест
    петли должен ловить ЩЕЛЧОК СКЛЕЙКИ, а не собственную крутизну сигнала.
    """

    def __init__(self, target, mode="r", samplerate=0, num_channels=0, format=None):
        self.mode = mode
        self.samplerate = float(samplerate or SR)
        self.num_channels = int(num_channels or 1)
        self.frames = int(self.samplerate * FRAG_SECONDS) if mode == "r" else 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n):
        i = np.arange(int(n), dtype="float32")
        wave_ = 0.5 * np.sin(2 * math.pi * 5.0 * i / self.samplerate)
        return np.tile(wave_, (self.num_channels, 1))

    def write(self, data):                # нужен только сигнатуре pb.io.AudioFile «w»
        return None


class _FakeBoard:
    """Цепочка: пишет, с каким `reset` её позвали, звук пропускает как есть."""

    def __init__(self, plugins):
        self.plugins = list(plugins)
        self.calls = []

    def __call__(self, chunk, sr, reset=True):
        self.calls.append((tuple(chunk.shape), float(sr), bool(reset)))
        return chunk


class _FakeStream:
    """Подмена `pedalboard.io.AudioStream`: собирает то, что в него записали.

    `running` и `write`, который в незапущенный поток бросает RuntimeError, — та же
    ловушка, что у настоящего потока: созданный, но не запущенный (`__enter__` не
    звали) он остаётся `running == False`, и звука нет. У настоящего `write` при
    этом молча возвращается (замер владельца: 0.5 с звука «записалось» за 0.89 с);
    здесь он падает, чтобы дефект был виден тестом, а не тишиной в колонках.

    `__exit__` закрывает поток, как настоящий (`io/AudioStream.h`: выход — это
    stop + close разом), поэтому счётчик `closed` растёт именно от него.
    """
    default_output_device_name = "Default Fake Output"

    def __init__(self):
        self.written = []
        self.closed = 0
        self.entered = 0
        self.exited = 0
        self.running = False
        self.on_written = None            # зовётся после каждой записи (тест гасит петлю)

    def __enter__(self):
        self.entered += 1
        self.running = True
        return self

    def __exit__(self, *exc):
        self.exited += 1
        self.running = False
        self.close()
        return False

    def write(self, audio, sample_rate):
        if not self.running:
            raise RuntimeError("не запущен")
        self.written.append(np.asarray(audio, dtype="float32"))
        if self.on_written is not None:
            self.on_written(len(self.written))

    def close(self):
        self.closed += 1


def _looping(blocks):
    """Поток и флаг стопа, который встаёт после N записанных блоков.

    Петля по замыслу бесконечная (играет, пока открыто окно), а тест должен
    закончиться: стоп ставится из `write`, когда записан последний нужный блок.

    Поток НЕ запущен: в `execute_job` его запускает `_open_stream`, а тесты петли
    входят в него сами (`with stream:`), как это делает рабочий процесс.
    """
    stream = _FakeStream()
    stop = ed.threading.Event()
    stream.on_written = lambda n: stop.set() if n >= blocks else None
    return stream, stop


class _StreamApi:
    """Подмена `pb.io.AudioStream`: помнит, с чем её открывали, и отдаёт поток.

    `default_output_device_name` — там же, где у настоящего класса: по нему
    проверяется, что пустое устройство в задании не превратилось в `None`
    («без вывода» — то есть в тишину).
    """
    default_output_device_name = _FakeStream.default_output_device_name

    def __init__(self, stream=None, error=None):
        self.stream = stream if stream is not None else _FakeStream()
        self.error = error
        self.calls = []                   # kwargs каждого открытия потока

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self.stream


class _FakePedalboard:
    """Подмена модуля pedalboard: записывает загрузки, созданные цепочки и потоки."""

    def __init__(self, stream=None, stream_error=None):
        self.loaded = []                  # [(path, plugin_name)]
        self.boards = []
        self.stream_api = _StreamApi(stream, stream_error)
        self.io = SimpleNamespace(AudioFile=_FakeAudioFile, AudioStream=self.stream_api)

    def load_plugin(self, path, plugin_name=None):
        self.loaded.append((path, plugin_name))
        state = None
        if plugin_name is None:
            state = b"state-of-" + os.path.basename(str(path)).encode("ascii")
        return _FakePlugin(path, plugin_name, state)

    def Pedalboard(self, plugins):
        board = _FakeBoard(plugins)
        self.boards.append(board)
        return board


def _fake_pb(monkeypatch, stream):
    """Подменить pedalboard у ПРОЦЕССА ОКНА (сервер плагинов не грузит вовсе).

    В сервере (`core/voicefx.py`) pedalboard больше не появляется: цепочку
    применяет дочерний процесс (`core/voicefx_render`), а список имён читает
    `core/voicefx_scan`. Поэтому подменяется только `core/voicefx_editor` — тот
    процесс, который окно и открывает.
    """
    pb = _FakePedalboard(stream)
    monkeypatch.setattr(ed, "_pedalboard", lambda: pb)
    return pb


def _write_frag(path, seconds=FRAG_SECONDS, channels=2):
    """Фрагмент — настоящий WAV: длина файла читается как длина звука."""
    n = int(seconds * SR)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.zeros((n, channels), dtype="<i2").tobytes())
    return str(path)


def _fake_run(calls):
    """Подмена `subprocess.run`: ffmpeg пишет фрагмент, deep-filter — свой файл,
    процесс окна отдаёт состояние."""
    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        program = os.path.basename(cmd[0]).lower()
        if "ffmpeg" in program:
            _write_frag(cmd[-1], channels=1)
        elif "deep-filter" in program:
            out_dir = cmd[cmd.index("-o") + 1]
            os.makedirs(out_dir, exist_ok=True)
            _write_frag(os.path.join(out_dir, os.path.basename(cmd[-1])), channels=1)
        elif "--state-out" in cmd:
            # Процесс окна: отдаёт состояние — так его читает edit_plugin
            with open(cmd[cmd.index("--state-out") + 1], "wb") as f:
                f.write(b"state-from-window")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def _job_from_cmd(cmd):
    """Задание, которое сервер положил на диск для процесса окна."""
    with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path, monkeypatch):
    """Кеши ядра — во временный каталог: боевая `_voicefx/` принадлежит владельцу."""
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    monkeypatch.setattr(voicefx, "_DEVICES_CACHE", None)
    monkeypatch.setenv("REELSI_VST3_SCAN", str(tmp_path / "vst3_scan.json"))


@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


@pytest.fixture
def denoiser(tmp_path, monkeypatch):
    """Живой deep-filter подменён файлом-заглушкой в REELSI_DEEP_FILTER."""
    exe = tmp_path / "deep-filter.exe"
    exe.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(exe))
    return str(exe)


# Цепочка так, как её хранит профиль спикера (ключ состояния — `state`)
CHAIN = [
    {"path": "C:/fresh.vst3", "name": "", "state": "AQID", "on": True},
    {"path": "C:/ozone.vst3", "name": "", "state": "", "on": False},
    {"path": "C:/shell.vst3", "name": "Valhalla", "state": "", "on": True},
]


def _job_chain(chain=CHAIN):
    """Та же цепочка в формате ЗАДАНИЯ: там состояние зовётся `state_b64`.

    Путать эти два ключа нельзя: с чужим именем состояние молча не доехало бы до
    плагина, и человек настраивал бы заводские ручки вместо своих.
    """
    return [{"path": c["path"], "name": c["name"], "state_b64": c["state"], "on": c["on"]}
            for c in chain]


# --------------------------------------------------------------------------- #
# 1. Сервер: edit_plugin собирает задание
# --------------------------------------------------------------------------- #
def test_edit_plugin_with_src_writes_job(monkeypatch, tmp_path):
    """С `src`: фрагмент на диске, задание цепочкой/индексом/устройством, процессу — `--job`."""
    calls = []
    fill = _fake_run(calls)
    seen = []                       # задание и его фрагмент — пока временная папка жива
    new_state = b"\x07from the window"

    def run(cmd, **kw):
        argv = [str(c) for c in cmd]
        if "--job" not in argv:
            return fill(cmd, **kw)          # ffmpeg извлекает звук, deep-filter чистит
        calls.append(argv)
        job = _job_from_cmd(argv)
        seen.append((job, os.path.isfile(job["frag"])))
        with open(cmd[cmd.index("--state-out") + 1], "wb") as f:
            f.write(new_state)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(voicefx.subprocess, "run", run)

    got = voicefx.edit_plugin("C:/shell.vst3", "Valhalla", "",
                              src="C:/cam1.mp4", start=7.5, fx={"vst": CHAIN},
                              index=2, device="Speakers (Waves SoundGrid)")

    assert base64.b64decode(got) == new_state
    assert "--path" not in calls[-1], "в режиме задания старые аргументы не нужны"
    job, frag_exists = seen[0]
    assert frag_exists, "фрагмент не лёг на диск до запуска окна"
    assert job["index"] == 2, "индекс открываемого плагина потерялся"
    assert job["device"] == "Speakers (Waves SoundGrid)"
    # Цепочка — в порядке профиля, ВЫКЛЮЧЕННЫЙ тоже едет: его можно открыть и включить
    assert [(c["path"], c["on"]) for c in job["chain"]] == \
        [("C:/fresh.vst3", True), ("C:/ozone.vst3", False), ("C:/shell.vst3", True)]
    assert job["chain"][0]["state_b64"] == "AQID", "состояние плагина не доехало"
    assert job["chain"][2]["name"] == "Valhalla", "имя внутри оболочки потерялось"
    assert job["frag"].endswith(".wav"), "фрагмент — не WAV"
    # Фрагмент — тот же кусок клипа, что у прослушивания
    ffmpeg = calls[0]
    assert ffmpeg[ffmpeg.index("-ss") + 1] == "7.500"
    assert ffmpeg[ffmpeg.index("-t") + 1] == "%.3f" % voicefx.PREVIEW_DUR


def test_edit_plugin_src_fragment_goes_through_denoiser(monkeypatch, denoiser):
    """Включён шумодав — фрагмент едет уже очищенным (и только он, без VST)."""
    calls = []
    fill = _fake_run(calls)
    jobs = []
    raw = []

    def run(cmd, **kw):
        argv = [str(c) for c in cmd]
        if "--job" in argv:
            jobs.append(_job_from_cmd(argv))
        if "ffmpeg" in os.path.basename(argv[0]).lower():
            raw.append(argv[-1])            # куда ffmpeg кладёт извлечённый звук
        return fill(cmd, **kw)
    monkeypatch.setattr(voicefx.subprocess, "run", run)

    voicefx.edit_plugin("C:/p.vst3", "", "", src="C:/cam1.mp4",
                        fx={"denoise": {"on": True, "engine": "deepfilter", "atten_db": 55},
                            "vst": [{"path": "C:/p.vst3", "on": True}]})

    dn = [c for c in calls if "deep-filter" in os.path.basename(c[0]).lower()]
    assert len(dn) == 1, f"шумодав вызван {len(dn)} раз: {dn}"
    assert dn[0][dn[0].index("-a") + 1] == "55"
    # Плагины в фрагмент не запекаются: их крутит окно — иначе цепочка слышна дважды.
    # И в окно уезжает именно ОЧИЩЕННЫЙ файл, а не извлечённый из клипа (наличие
    # фрагмента на диске к этому моменту проверяет тест задания: папка уже убрана).
    assert jobs, "задание не собрано"
    assert os.path.realpath(jobs[0]["frag"]) != os.path.realpath(raw[0]), \
        "в окно уехал неочищенный фрагмент"


def test_edit_plugin_without_src_keeps_old_command(monkeypatch, tmp_path):
    """Без `src` — прежняя команда без звука: `--path/--state-in/--state-out`, без `--job`."""
    calls = []

    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        assert "--job" not in cmd, "окно без звука поехало в режим задания"
        assert cmd[cmd.index("--path") + 1] == "C:/p.vst3"
        assert cmd[cmd.index("--name") + 1] == "Inner"
        with open(cmd[cmd.index("--state-in") + 1], "rb") as f:
            assert f.read() == b"\x01\x02\x03"
        with open(cmd[cmd.index("--state-out") + 1], "wb") as f:
            f.write(b"fresh")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(voicefx.subprocess, "run", run)

    got = voicefx.edit_plugin("C:/p.vst3", "Inner",
                              base64.b64encode(b"\x01\x02\x03").decode("ascii"))
    assert base64.b64decode(got) == b"fresh"
    assert len(calls) == 1


def test_route_edit_passes_live_fields(client, monkeypatch):
    """Роут доносит до ядра всю живую часть: src, start, fx, index, device, спикер.

    Окно открывается ЖИВЫМ (`edit_plugin_live`): запрос возвращается сразу с номером
    сессии, а не висит до закрытия окна — на этом и стоит «открывается долго».
    Спикер едет в ядро затем, чтобы состояние плагина по закрытию окна уехало в его
    профиль САМО (api/voicefx.py:_save_live).
    """
    seen = {}

    def fake_live(path, name, **kw):
        seen.update(path=path, name=name)
        seen.update(kw)
        return SimpleNamespace(sid="4242", t0=1.0, opened_at=1.5)
    monkeypatch.setattr(voicefx, "edit_plugin_live", fake_live)

    d = client.post("/api/voicefx_edit", json={
        "path": "C:/p.vst3", "name": "Inner", "state": "AA", "src": "C:/cam1.mp4",
        "start": 12, "fx": {"vst": CHAIN}, "index": 2, "device": "Main 1/2 (Minifuse 1)",
        "speaker": "Мясников"
    }).get_json()

    assert d["ok"] is True and d["sid"] == "4242", d
    assert d["running"] is True and d["state"] == "", "окно живёт — состояния ещё нет"
    assert seen["path"] == "C:/p.vst3"
    assert seen["src"] == "C:/cam1.mp4" and seen["start"] == 12.0
    assert seen["index"] == 2 and seen["device"] == "Main 1/2 (Minifuse 1)"
    assert seen["speaker"] == "Мясников"
    assert seen["fx"] == {"vst": CHAIN}
    assert callable(seen["on_close"]), "по закрытию окна состояние некуда сохранить"


def test_route_edit_without_src_is_silent_mode(client, monkeypatch, denoiser):
    """Без `src` окно по-прежнему открывается без звука (и не выдумывает мусор).

    Живой процесс окна подменяем: настоящий открыл бы окно плагина и не вернулся.
    """
    seen = {}
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_run([]))
    monkeypatch.setattr(voicefx, "edit_plugin_live",
                        lambda path, name, **kw: seen.update(kw) or
                        SimpleNamespace(sid="7", t0=1.0, opened_at=0.0))

    d = client.post("/api/voicefx_edit", json={"path": "C:/p.vst3"}).get_json()

    assert d["ok"] is True
    assert seen["src"] == "" and seen["index"] == -1 and seen["device"] == ""
    assert seen["start"] == 0.0 and seen["fx"] == {} and seen["speaker"] == ""


# --------------------------------------------------------------------------- #
# 2. Процесс окна: цепочка, открытие, состояние
# --------------------------------------------------------------------------- #
def test_job_loads_enabled_chain_and_opens_the_index(monkeypatch, tmp_path):
    """В звук — только включённые и по порядку; открывается плагин ПО ИНДЕКСУ цепочки."""
    stream, stop = _looping(3)
    pb = _fake_pb(monkeypatch, stream)
    out = tmp_path / "out.bin"
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 2, "device": "Main 1/2 (Minifuse 1)"}

    ed.execute_job(job, str(out))

    assert [p[0] for p in pb.loaded] == ["C:/fresh.vst3", "C:/ozone.vst3", "C:/shell.vst3"], \
        "грузим всю цепочку: открыть можно и выключенный плагин"
    assert pb.loaded[2][1] == "Valhalla", "имя внутри оболочки потерялось по дороге"
    assert pb.boards[0].plugins[0].raw_state == b"\x01\x02\x03", "состояние не восстановлено"
    assert [p.path for p in pb.boards[0].plugins] == ["C:/fresh.vst3", "C:/shell.vst3"], \
        "в звук попал выключенный плагин (или порядок цепочки съехал)"
    # Окно показано ровно третьему плагину цепочки — не первому включённому.
    # У этого плагина своего состояния не было: значит, состояние на выходе — то,
    # что «накрутил» он сам, а не чужой плагин (подменённый показывает это байтами).
    assert [p.opened for p in pb.boards[0].plugins] == [0, 1], "открыт не тот плагин"
    assert out.read_bytes() == b"", "состояние записано не с открытого плагина"
    assert stream.closed == 1, "поток не закрыт после окна"
    assert stream.written, "звук не пошёл в устройство"
    assert pb.stream_api.calls[0]["output_device_name"] == "Main 1/2 (Minifuse 1)", \
        "устройство из задания не доехало до потока"


def test_job_opens_disabled_plugin_without_putting_it_in_the_sound(monkeypatch, tmp_path):
    """Индекс на выключенный: окно открывается, в цепочке звука его нет."""
    stream, stop = _looping(3)
    pb = _fake_pb(monkeypatch, stream)
    out = tmp_path / "out.bin"
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 1, "device": ""}

    ed.execute_job(job, str(out))

    assert len(pb.loaded) == 3, "выключенный плагин должен быть загружен"
    assert [p.path for p in pb.boards[0].plugins] == ["C:/fresh.vst3", "C:/shell.vst3"]
    # Состояние уходит именно того плагина, чьё окно закрыли
    assert out.read_bytes() == b"state-of-ozone.vst3", "записано состояние соседа по цепочке"


def test_job_index_out_of_chain_is_an_error(monkeypatch, tmp_path):
    """Индекс за пределами цепочки — ошибка задания, а не чужое окно."""
    _fake_pb(monkeypatch, _FakeStream())
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 9, "device": ""}
    with pytest.raises(ReelsiError) as e:
        ed.execute_job(job, str(tmp_path / "out.bin"))
    assert "9" in str(e.value)


def test_main_job_mode_reads_the_job_file(monkeypatch, tmp_path):
    """`--job` — точка входа процесса окна: задание файлом, состояние в `--state-out`."""
    stream, stop = _looping(3)
    _fake_pb(monkeypatch, stream)
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps({"frag": _write_frag(tmp_path / "frag.wav"),
                                    "chain": _job_chain(), "index": 0, "device": ""}),
                        encoding="utf-8")
    out = tmp_path / "out.bin"

    assert ed.main(["--job", str(job_path), "--state-out", str(out)]) == 0
    assert out.read_bytes() == b"state-of-fresh.vst3", "состояние не ушло в --state-out"


def test_job_reports_broken_file(monkeypatch, tmp_path):
    """Битое задание — понятная ошибка, а не падение с трейсбеком."""
    _fake_pb(monkeypatch, _FakeStream())
    bad = tmp_path / "job.json"
    bad.write_text("{не json", encoding="utf-8")
    with pytest.raises(ReelsiError) as e:
        ed.main(["--job", str(bad), "--state-out", str(tmp_path / "o.bin")])
    assert "задани" in str(e.value)


# --------------------------------------------------------------------------- #
# 3. Петля: фрагмент играет по кругу, стык не щёлкает
# --------------------------------------------------------------------------- #
def _blocks_per_lap():
    """Сколько блоков укладывается в один круг фрагмента."""
    return int(FRAG_SECONDS * SR / ed.BLOCK)


def test_loop_plays_fragment_round_and_round(monkeypatch, tmp_path):
    """За N блоков фрагмент пройден больше одного раза, и все блоки — со звуком."""
    laps = 3
    stream, stop = _looping(_blocks_per_lap() * laps + 1)
    pb = _fake_pb(monkeypatch, stream)

    with stream:                          # рабочий процесс входит в поток до петли
        ed.play_loop(ed.read_fragment(_write_frag(tmp_path / "frag.wav")), pb.Pedalboard([]),
                     stream, stop, lambda msg: None)

    assert len(stream.written) >= 3, "петля не пошла: блоков меньше, чем нужно"
    assert all(b.shape[0] == ed.CHANNELS for b in stream.written), "блок не стерео"
    played = sum(b.shape[-1] for b in stream.written)
    assert played > laps * int(FRAG_SECONDS * SR), \
        f"фрагмент не пройден {laps} раза: сыграно {played} сэмплов"


def test_loop_boundary_is_faded(monkeypatch, tmp_path):
    """На стыке петли нет ступеньки: конец круга и начало следующего — в пределах фейда.

    Без фейда конец фрагмента склеивается с началом как есть, и разница может быть
    во всю амплитуду — на слух это щелчок на каждом круге. Здесь у сигнала разрыв
    в 0.5 (синус 5 Гц: последний сэмпл ≈ +0.083, первый ≈ −0.417), фейд 5 мс
    растягивает его по 240 сэмплам — это ≈0.002 на сэмпл.
    """
    stream, stop = _looping(_blocks_per_lap() + 1)
    pb = _fake_pb(monkeypatch, stream)

    with stream:                          # незапущенному потоку write не пишет (ловушка фейка)
        ed.play_loop(ed.read_fragment(_write_frag(tmp_path / "frag.wav")), pb.Pedalboard([]),
                     stream, stop, lambda msg: None)

    samples = np.concatenate([b.reshape(-1) for b in stream.written])
    wraps = [i for i in range(1, samples.shape[0]) if i % int(FRAG_SECONDS * SR) == 0]
    assert wraps, "стык петли не попал в записанное"
    limit = 6 * 0.5 / ed.FADE          # 0.5 — разрыв сигнала, 6 — с запасом на округление
    for i in wraps:
        jump = abs(float(samples[i]) - float(samples[i - 1]))
        assert jump < limit, f"щелчок на стыке петли ({jump:.4f} при пределе {limit:.4f})"
    assert float(np.abs(samples).max()) > 0.1, "играла тишина — проверять нечего"


def test_loop_keeps_plugin_state_between_blocks(monkeypatch, tmp_path):
    """reset=True из рабочего потока не зовётся НИКОГДА: плагины готовит главный поток."""
    stream, stop = _looping(5)
    pb = _fake_pb(monkeypatch, stream)
    board = pb.Pedalboard([])

    with stream:
        ed.play_loop(ed.read_fragment(_write_frag(tmp_path / "frag.wav")), board, stream,
                     stop, lambda msg: None)

    resets = [c[2] for c in board.calls]
    assert len(resets) > 1 and not any(resets), resets
    assert all(c[1] == ed.SR for c in board.calls), "частота уехала от 48 кГц"


def test_loop_stops_on_the_flag(monkeypatch, tmp_path):
    """Сигнал остановки прекращает звук: закрытое окно не играет в пустую комнату."""
    stream = _FakeStream()
    pb = _fake_pb(monkeypatch, stream)
    stop = ed.threading.Event()
    stop.set()

    with stream:
        ed.play_loop(ed.read_fragment(_write_frag(tmp_path / "frag.wav")), pb.Pedalboard([]),
                     stream, stop, lambda msg: None)

    assert stream.written == [], "после остановки в устройство всё ещё писали"


# --------------------------------------------------------------------------- #
# 4. Звук: поток запущен и остановлен, пустое устройство — системное
# --------------------------------------------------------------------------- #
def test_stream_is_started_for_the_whole_loop_and_stopped_once(monkeypatch, tmp_path):
    """Поток ЗАПУЩЕН на всё время петли и остановлен ровно один раз.

    Без `__enter__` настоящий AudioStream остаётся `running == False`: `write`
    возвращается, а из колонок не идёт ничего (замер владельца: 0.5 с звука
    «записалось» за 0.89 с). Фейк на такую запись бросает RuntimeError, поэтому
    дефект виден тестом, а не замером на живой машине.
    """
    stream, stop = _looping(3)
    _fake_pb(monkeypatch, stream)
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 0, "device": "Main 1/2 (Minifuse 1)"}

    ed.execute_job(job, str(tmp_path / "out.bin"))

    assert stream.written, "в устройство не ушло ни одного блока: поток не запущен"
    assert stream.entered == 1, "поток запущен не один раз — вход и выход не парные"
    assert stream.exited == 1, "по закрытию окна __exit__ вызван не один раз"
    assert not stream.running, "поток остался запущенным после закрытия окна"


def test_empty_device_plays_to_the_system_default(monkeypatch, tmp_path):
    """Пустое устройство в задании — системное по умолчанию, а не `None`.

    У настоящего AudioStream `None` значит «без вывода»: поток открылся бы, `write`
    возвращался бы, и человек настраивал бы плагин в тишине.
    """
    stream, stop = _looping(1)
    pb = _fake_pb(monkeypatch, stream)
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 0, "device": ""}

    ed.execute_job(job, str(tmp_path / "out.bin"))

    opened = pb.stream_api.calls
    assert opened, "поток вывода не открывали"
    assert opened[0]["output_device_name"] == _FakeStream.default_output_device_name, \
        "пустое устройство уехало в поток как «без вывода» — звука не будет"
    assert stream.written, "в устройство по умолчанию ничего не ушло"


# --------------------------------------------------------------------------- #
# 4б. Частота УСТРОЙСТВА: 44.1 кГц — ресемплер на выходе цепочки
# --------------------------------------------------------------------------- #
class _DevStream(_FakeStream):
    """Поток устройства: его частота — то, что требует `write` (владелец: 44.1 кГц)."""

    default_output_device_name = _FakeStream.default_output_device_name

    def __init__(self, rate):
        super().__init__()
        self.sample_rate = rate
        self.rates = []

    def write(self, audio, sample_rate):
        self.rates.append(float(sample_rate))
        super().write(audio, sample_rate)


class _FakeResampler:
    """Заглушка `pedalboard.io.StreamResampler`: длину меняет по отношению частот."""

    made = []

    def __init__(self, source, target, channels):
        self.ratio = float(target) / float(source)
        self.channels = int(channels)
        _FakeResampler.made.append(self)

    def process(self, chunk):
        n = int(round(chunk.shape[-1] * self.ratio))
        return np.asarray(chunk, dtype="float32")[:, :n]


def _resampling_pb():
    """pedalboard с потоком устройства и ресемплером-заглушкой (без нативного кода)."""
    return SimpleNamespace(io=SimpleNamespace(AudioFile=_FakeAudioFile,
                                              StreamResampler=_FakeResampler))


def test_play_live_writes_in_the_device_rate(monkeypatch, tmp_path):
    """Устройство 44.1 кГц: блоки уезжают В ЕГО частоте и укороченными.

    Владелец: устройство по умолчанию 44.1 кГц, а хост писал 48 — pedalboard бросал
    RuntimeError на первой же записи, живой звук пропадал целиком, а страница при этом
    глушила свой голос «потому что звучит хост». Плагины считают на 48, как раньше:
    ресемплер стоит ПОСЛЕ цепочки.
    """
    _FakeResampler.made.clear()
    monkeypatch.setattr(ed, "_pedalboard", lambda: _resampling_pb())
    frag = ed.read_fragment(_write_frag(tmp_path / "frag.wav"))
    board = _FakeBoard([])
    stream, stop = _DevStream(44100), ed.threading.Event()
    errors = []
    stream.on_written = lambda n: stop.set() if n >= 2 else None

    with stream:
        ed.play_live(frag, board, stream, stop, errors.append, ed.queue.Queue())

    assert not errors, errors
    assert _FakeResampler.made, "на 44.1 кГц ресемплер не включился"
    assert stream.rates == [44100.0] * len(stream.written), stream.rates
    want = int(round(ed.BLOCK * 44100 / 48000))
    assert all(b.shape[-1] == want for b in stream.written), [b.shape for b in stream.written]
    assert all(c[1] == ed.SR for c in board.calls), "плагины получили не 48 кГц"


def test_play_live_keeps_48k_without_the_resampler(monkeypatch, tmp_path):
    """Устройство 48 кГц — всё как раньше: ресемплер не создаётся, блок целый."""
    _FakeResampler.made.clear()
    monkeypatch.setattr(ed, "_pedalboard", lambda: _resampling_pb())
    frag = ed.read_fragment(_write_frag(tmp_path / "frag.wav"))
    board = _FakeBoard([])
    stream, stop = _DevStream(48000), ed.threading.Event()
    stream.on_written = lambda n: stop.set() if n >= 1 else None

    with stream:
        ed.play_live(frag, board, stream, stop, lambda m: None, ed.queue.Queue())

    assert _FakeResampler.made == [], "на 48 кГц ресемплер включился"
    assert stream.rates == [48000.0], stream.rates
    assert stream.written[0].shape[-1] == ed.BLOCK


# --------------------------------------------------------------------------- #
# 5. Сбой звука окно не роняет
# --------------------------------------------------------------------------- #
def test_window_opens_when_stream_fails(monkeypatch, tmp_path, capsys):
    """Устройства нет: окно открыто, состояние записано, причина — строкой в stderr."""
    pb = _FakePedalboard(stream_error=OSError("no device"))
    monkeypatch.setattr(ed, "_pedalboard", lambda: pb)
    out = tmp_path / "out.bin"
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 0, "device": ""}

    ed.execute_job(job, str(out))

    assert out.read_bytes() == b"state-of-fresh.vst3", "окно не открылось или состояние потеряно"
    assert pb.boards[0].plugins[0].opened == 1
    assert "звука нет" in capsys.readouterr().err, "причина не сказана одной строкой"


def test_failed_write_does_not_hang_the_window(monkeypatch, tmp_path, capsys):
    """Устройство отвалилось посреди игры: поток останавливается, окно закрывается."""
    class _Dying(_FakeStream):
        def write(self, audio, sample_rate):
            self.written.append(np.asarray(audio, dtype="float32"))
            raise RuntimeError("device lost")

    pb = _FakePedalboard(stream=_Dying())
    monkeypatch.setattr(ed, "_pedalboard", lambda: pb)
    out = tmp_path / "out.bin"
    job = {"frag": _write_frag(tmp_path / "frag.wav"), "chain": _job_chain(),
           "index": 0, "device": ""}

    ed.execute_job(job, str(out))          # не должно зависнуть на join

    assert out.read_bytes() == b"state-of-fresh.vst3"
    assert "звук прервался" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 6. Устройства вывода и фронт
# --------------------------------------------------------------------------- #
def test_route_devices(client, monkeypatch):
    """`/api/voicefx_devices` отдаёт список и системное устройство по умолчанию."""
    monkeypatch.setattr(voicefx, "output_devices",
                        lambda refresh=False: {"devices": ["Main 1/2 (Minifuse 1)", "SPDIF"],
                                               "default": "Main 1/2 (Minifuse 1)"})
    d = client.get("/api/voicefx_devices").get_json()
    assert d["ok"] is True
    assert d["devices"] == ["Main 1/2 (Minifuse 1)", "SPDIF"]
    assert d["default"] == "Main 1/2 (Minifuse 1)"


def test_route_devices_forwards_core_error(client, monkeypatch):
    """Сбой списка устройств уезжает фронту кодом, а не 500-й."""
    def boom(refresh=False):
        raise ReelsiError(umsg("voicefx_devices_failed", "нет аудиосистемы"))
    monkeypatch.setattr(voicefx, "output_devices", boom)

    d = client.get("/api/voicefx_devices").get_json()
    assert d.get("err") == "voicefx_devices_failed" and d.get("error")


def test_output_devices_reads_child_process(monkeypatch):
    """Список берётся у дочернего процесса и кешируется: сбор поднимает аудиосистему.

    Собирает его ОТДЕЛЬНЫЙ процесс (core/voicefx_audio): pedalboard работает поверх
    JUCE и тянет нативный код с фоновыми потоками — в сервере интерфейса этому
    делать нечего (та же причина, что у списка плагинов). Здесь проверяется, что
    родитель зовёт ровно этот модуль и разбирает его ответ.
    """
    calls = []

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        calls.append(([str(c) for c in cmd], what, timeout))
        return voicefx._ChildRun([{"devices": ["Speakers (Waves SoundGrid)",
                                               "Main 1/2 (Minifuse 1)"],
                                   "default": "Speakers (Waves SoundGrid)"}], [], 0)
    monkeypatch.setattr(voicefx, "_run_child", fake_child)

    first = voicefx.output_devices()
    second = voicefx.output_devices()
    assert first == second == {"devices": ["Speakers (Waves SoundGrid)",
                                           "Main 1/2 (Minifuse 1)"],
                               "default": "Speakers (Waves SoundGrid)"}
    assert len(calls) == 1, "список устройств собирается на каждый запрос"
    assert calls[0][0][1:4] == ["-m", "core.voicefx_audio", "--devices"], calls[0]
    assert calls[0][2] == voicefx.DEVICES_TIMEOUT


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _fn_body(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_front_markup_has_devices_select():
    """«Куда играть» — в панели «Голос» превью, рядом с выключателем «ИИ-шумодав».

    Панель целиком строится одной функцией разметки (voiceFxRender), поэтому список
    устройств ищется по data-vfx, а не по id: id в разметке нет — иначе на два места
    (превью и профиль) он бы задвоился.
    """
    js = _read(os.path.join("static", "app", "95-styles.js"))
    panel = _fn_body(js, "function voiceFxRender(host,fx,opts){", "\n// Значения панели")
    assert 'data-vfx="device"' in panel, "в панели нет выпадающего списка устройств"
    assert "Куда играть" in panel, "подпись списка пропала"
    assert "Устройство вывода для живого прослушивания" in panel, "пропала подсказка «!»"
    # Список заполняется при отрисовке панели — иначе он пустой
    assert "fxDeviceFill(host)" in panel, "панель не заполняет список устройств"
    html = _read(os.path.join("templates", "index.html"))
    assert html.count('id="pvvoice"') == 1, "в превью нет места под панель «Голос»"


def test_front_vst_edit_sends_live_fields():
    """`vstFxEdit` — панель живого хоста: цепочка и индекс едут хосту, окно опрашивается.

    Окно не отдельный процесс со своим звуком: `vstFxEdit` приводит хост к цепочке
    панели (`voiceFxHostSync`, с `window` и `force`) и шлёт команду «открой окно
    плагина» в УЖЕ звучащий процесс. Хост поднимает `voiceFxHostStart` (клип, место
    бегунка, устройство, спикер), а опрос (`voiceFxHostPoll`) возвращает состояние
    плагина в строку цепочки по закрытию окна. Звук при этом не прерывается.
    """
    js = _read(os.path.join("static", "app", "95-styles.js"))
    body = _fn_body(js, "async function vstFxEdit(el){", "\n// Панель «Голос» превью нарезки")
    assert "'/api/voicefx_host_edit'" in body
    assert "voiceFxRead(host)" in body, "хосту не уезжает вся цепочка с состояниями"
    assert "voiceFxHostSync(fx,{window:true,force:true})" in body, \
        "перед окном хост не приводится к цепочке панели"
    assert "index:idx" in body, "не сказано, какой плагин открывать"
    assert "path:row.dataset.path" in body, "путь плагина не уехал — у пропущенного индексы разъедутся"
    assert "vtCam1(ED)" in body, "клип для звука не взят"
    assert re.search(r"t\('Клип не выбран — окно без звука'\)", body), \
        "без клипа нет объяснения, почему тихо"
    start = _fn_body(js, "function voiceFxHostStart(fx,key){", "\n// Опрос хоста")
    assert "'/api/voicefx_host'" in start
    assert "device:fxDeviceGet()" in start, "устройство вывода не уехало"
    assert "vtSrcAt(P,typeof vtNow==='function'?vtNow(P):0)" in start and "start:" in start, \
        "место в клипе не уехало (одна дверь vtSrcAt на все плееры)"
    assert "speaker:VOICEFXSPK" in start, "спикер не уехал — сохранять состояние некуда"
    # Опрос хоста: пока живо — живой звук, закрылось окно — состояние в строку
    poll = _fn_body(js, "function voiceFxHostPoll(){", "\n// Что не так с хостом")
    assert "'/api/voicefx_live?sid='" in poll, "панель не опрашивает хост"
    assert "row.dataset.state=d.state" in poll, "состояние плагина не вернулось в строку"
    assert "voiceFxLiveOn" in js, "превью не узнаёт, что звук идёт через плагин"
    # Подсказка «!» у кнопки «Настроить» и состояние — в самой строке цепочки
    assert "Окно плагина откроется СВЕРХУ" in js, "пропала подсказка про окно поверх"


def test_front_device_choice_lives_in_localstorage():
    """Устройство — настройка машины: помнится в localStorage, без падений при запрете."""
    js = _read(os.path.join("static", "app", "95-styles.js"))
    body = _fn_body(js, "function fxDeviceGet()", "\n// Список устройств спрашиваем")
    assert "localStorage.getItem(FX_DEV_KEY)" in body
    assert "localStorage.setItem(FX_DEV_KEY" in body
    assert body.count("catch(e)") >= 2, "чтение и запись localStorage не защищены"
    fill = _fn_body(js, "async function fxDeviceFill(host){", "\n// Окно плагина открывает СЕРВЕР")
    assert "'/api/voicefx_devices'" in fill and "t('По умолчанию')" in fill
    assert "vfxEl(host,'device')" in fill, "список устройств заполняется не в своей панели"
