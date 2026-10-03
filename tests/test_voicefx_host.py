# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Живой хост плагинов: одна модель — плагин вставка на дорожке, окно его панель.

Голос превью всегда идёт через плагины вживую (процесс `core/voicefx_editor --live`),
а окно плагина — панель ЭТОГО ЖЕ хоста. Здесь проверяется то, что ломается молча:

* правка цепочки и открытие окна доходят до звучащего хоста командами, а поток
  вывода при этом не пересоздаётся и не замолкает;
* плагин, который не загрузился, пропускается, а окно соседа не подменяется;
* хост снимается по PID (закрыли превью) и сам, если страница замолчала;
* сервер держит ОДИН хост на клип, а правка настроек шумодава пересчитывает
  дорожку хоста на месте (пауза, потом подхват без прыжка);
* дорожка шумодава одного клипа не считается двумя заказчиками разом;
* страница поднимает хост, шлёт `chain` только при правке цепочки и гасит хост,
  когда плагинов не осталось.

Настоящие плагины владельца и звуковое устройство НЕ трогаются — только подмены.

Запуск:  py -3.10 -m pytest tests/test_voicefx_host.py -q
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import voicefx, voicefx_editor as ed  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")
STYLES_JS = ROOT / "static" / "app" / "95-styles.js"


def _wait(cond: Callable[[], Any], what: str, timeout: float = 20.0) -> None:
    """Дождаться условия: хост живёт в своих потоках, и «сразу» там не бывает."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("не дождались: " + what)


def _item(name: str, on: bool = True) -> dict[str, Any]:
    return {"path": f"C:/plugins/{name}.vst3", "name": "", "state_b64": "", "on": on}


# --------------------------------------------------------------------------- #
# Подмены: pedalboard, поток вывода, труба stdin
# --------------------------------------------------------------------------- #
class _Plugin:
    def __init__(self, path: str) -> None:
        self.path = path
        self.raw_state = b""
        self.opened = 0

    def show_editor(self) -> None:
        self.opened += 1

    def __call__(self, chunk: Any, sr: float, reset: bool = False) -> Any:
        """Как Valhalla: reset (перенастройка) допустим только в главном потоке."""
        if reset and threading.current_thread() is not threading.main_thread():
            raise ValueError(
                f"Plugin {self.path} must be reloaded on the main thread. "
                "Please pass `reset=False` if calling this plugin from a non-main thread.")
        return chunk


class _Board:
    def __init__(self, plugins: list[Any]) -> None:
        self.plugins = list(plugins)

    def __call__(self, chunk: Any, sr: float, reset: bool = True) -> Any:
        for p in self.plugins:
            chunk = p(chunk, sr, reset=reset)
        return chunk


class _Stream:
    """Подмена `pedalboard.io.AudioStream`: считает блоки, входы и выходы."""

    default_output_device_name = "fake"

    def __init__(self) -> None:
        self.blocks = 0
        self.entered = 0
        self.closed = 0
        self.running = False

    def __enter__(self) -> "_Stream":
        self.entered += 1
        self.running = True
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.running = False
        self.closed += 1
        return False

    def write(self, audio: Any, sample_rate: float) -> None:
        self.blocks += 1
        time.sleep(0.005)          # не крутить процессор вхолостую: блок реальный — 50 мс

    def close(self) -> None:
        self.closed += 1


class _Reader:
    """Мини-замена `pedalboard.io.AudioFile`: читает наш WAV, (каналы, сэмплы) float32."""

    def __init__(self, path: str, mode: str = "r", **_kw: Any) -> None:
        self.path = path
        with wave.open(path, "rb") as w:
            self.frames = w.getnframes()

    def __enter__(self) -> "_Reader":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def read(self, n: int) -> Any:
        with wave.open(self.path, "rb") as w:
            data = w.readframes(int(n))
            ch = w.getnchannels()
        arr = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
        return arr.reshape(-1, ch).T if ch > 1 else arr.reshape(1, -1)


class _Pedalboard:
    """Подмена pedalboard: помнит каждую загрузку и каждую собранную доску."""

    def __init__(self, stream: _Stream, broken: tuple[str, ...] = ()) -> None:
        self.stream = stream
        self.broken = broken
        self.loads: list[str] = []
        self.plugins: dict[str, _Plugin] = {}
        self.boards: list[_Board] = []
        self.io = SimpleNamespace(AudioFile=_Reader, AudioStream=lambda **kw: stream)

    def load_plugin(self, path: str, plugin_name: str | None = None) -> _Plugin:
        self.loads.append(path)
        if any(b in path for b in self.broken):
            raise ImportError(f"Unable to load plugin {path}: unsupported plugin format")
        plug = _Plugin(path)
        self.plugins[path] = plug
        return plug

    def Pedalboard(self, plugins: list[Any]) -> _Board:
        board = _Board(plugins)
        self.boards.append(board)
        return board


class _Pipe:
    """stdin процесса: команды кладёт тест, читает поток `_commands_from_stdin`."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()

    def __iter__(self) -> "_Pipe":
        return self

    def __next__(self) -> str:
        line = self.q.get()
        if line is None:
            raise StopIteration
        return line

    def send(self, obj: dict[str, Any]) -> None:
        self.q.put(json.dumps(obj, ensure_ascii=False) + "\n")


def _track(path: Path, seconds: float = 30.0) -> Path:
    n = int(seconds * ed.SR)
    data = np.zeros((n, 2), dtype="<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(ed.SR)
        w.writeframes(data.tobytes())
    return path


def _run_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chain: list[dict[str, Any]],
              drive: Callable[[_Pipe, _Pedalboard, _Stream, Path], None],
              broken: tuple[str, ...] = (),
              stream: "_Stream | None" = None) -> tuple[_Pedalboard, _Stream, list[dict[str, Any]]]:
    """Поднять хост в этом же процессе на подменах и отдать его сценарию `drive`.

    Сценарий шлёт команды из потока и в конце обязан прислать `stop`; сбой сценария
    тоже гасит хост — иначе тест повис бы, ожидая конца процесса.

    `stream` — свой поток вывода (падающий, с частотой устройства); None — обычная подмена.
    """
    track = _track(tmp_path / "track.wav")
    pointer = tmp_path / "track.json"
    pointer.write_text(json.dumps({"track": str(track)}), encoding="utf-8")
    stream = stream if stream is not None else _Stream()
    pb = _Pedalboard(stream, broken)
    pipe = _Pipe()
    monkeypatch.setattr(ed, "_pedalboard", lambda: pb)
    monkeypatch.setattr(sys, "stdin", pipe)
    events = tmp_path / "events.jsonl"
    job = {"chain": chain, "index": -1, "device": "fake", "track_file": str(pointer),
           "events_file": str(events), "start": 0.0, "paused": False}
    errors: list[BaseException] = []

    def _driver() -> None:
        try:
            drive(pipe, pb, stream, events)
        except BaseException as e:            # noqa: BLE001 — сбой сценария несём в тест
            errors.append(e)
        finally:
            pipe.send({"cmd": "stop"})

    th = threading.Thread(target=_driver, daemon=True)
    th.start()
    ed.execute_live(job, str(tmp_path / "state.bin"))
    th.join(timeout=10)
    if errors:
        raise errors[0]
    lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
    return pb, stream, [json.loads(ln) for ln in lines if ln.strip()]


# --------------------------------------------------------------------------- #
# 1. Хост: цепочка на лету, окно по команде, звук не пересоздаётся
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(90)
def test_host_rebuilds_chain_and_opens_window_without_touching_the_stream(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`chain` перестраивает доску, `open_editor` открывает окно в том же процессе.

    Поток вывода один на всё время: не пересоздаётся ни при правке цепочки, ни при
    открытии окна, а блоки звука идут и после каждой команды — «звук не рвётся».
    Уже загруженный плагин берётся готовым (не грузится второй раз).
    """
    seen: dict[str, Any] = {}

    def drive(pipe: _Pipe, pb: _Pedalboard, stream: _Stream, events: Path) -> None:
        _wait(lambda: stream.blocks >= 3, "хост заиграл")
        seen["boards_before"] = len(pb.boards)
        pipe.send({"cmd": "chain", "chain": [_item("A"), _item("B")]})
        _wait(lambda: len(pb.boards) > seen["boards_before"], "цепочка перестроена")
        seen["after_chain"] = stream.blocks
        _wait(lambda: stream.blocks >= seen["after_chain"] + 3, "звук идёт после chain")
        pipe.send({"cmd": "seek", "at": 4.0})          # после перемотки — тоже без reset
        seen["after_seek"] = stream.blocks
        _wait(lambda: stream.blocks >= seen["after_seek"] + 3, "звук идёт после seek")
        pipe.send({"cmd": "open_editor", "index": 1, "path": _item("B")["path"]})
        _wait(lambda: "C:/plugins/B.vst3" in pb.plugins
              and pb.plugins["C:/plugins/B.vst3"].opened == 1, "окно плагина B открыто")
        seen["after_open"] = stream.blocks
        _wait(lambda: stream.blocks >= seen["after_open"] + 3, "звук идёт после окна")

    pb, stream, events = _run_host(tmp_path, monkeypatch, [_item("A")], drive)

    assert stream.entered == 1, "поток вывода пересоздан — при правке цепочки будет щелчок"
    assert stream.closed >= 1
    assert pb.loads.count(_item("A")["path"]) == 1, "живой плагин A загружен заново"
    assert pb.loads.count(_item("B")["path"]) == 1
    last = pb.boards[-1]
    assert [p.path for p in last.plugins] == [_item("A")["path"], _item("B")["path"]], \
        "новая цепочка не встала на доску"
    assert pb.plugins[_item("A")["path"]].opened == 0, "открыли окно соседа"
    names = [e["event"] for e in events]
    assert names.count("chain") >= 2, "события цепочки не доехали до файла событий"
    assert all(not e["skipped"] for e in events if e["event"] == "chain"),         "плагин пропущен: reset=True позвали не из главного потока"
    assert "editor_open" in names and "editor_closed" in names


@pytest.mark.timeout(90)
def test_host_skips_unloadable_plugin_and_opens_the_right_neighbour(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Незагружаемый плагин пропущен С ИМЕНЕМ, хост жив, «Настроить» по пути не путает соседей.

    Индексы панели (цепочка целиком) и хоста (только загруженные) разъезжаются, когда
    плагин выпал: путь строки — единственное, что их сводит. Окно пропущенного плагина
    открывать нечем — хост отвечает событием и играет дальше.
    """
    bad, good = _item("Bertom_DenoiserClassic"), _item("Fresh")

    def drive(pipe: _Pipe, pb: _Pedalboard, stream: _Stream, events: Path) -> None:
        _wait(lambda: stream.blocks >= 3, "хост заиграл без битого плагина")
        pipe.send({"cmd": "open_editor", "index": 1, "path": good["path"]})
        _wait(lambda: pb.plugins[good["path"]].opened == 1, "открыт плагин, а не сосед")
        pipe.send({"cmd": "open_editor", "index": 0, "path": bad["path"]})
        _wait(lambda: any(json.loads(ln).get("event") == "editor_failed"
                          for ln in events.read_text(encoding="utf-8").splitlines()),
              "отказ по пропущенному плагину")
        before = stream.blocks
        _wait(lambda: stream.blocks >= before + 3, "звук идёт после отказа")

    pb, stream, events = _run_host(tmp_path, monkeypatch, [bad, good], drive,
                                   broken=("Bertom_DenoiserClassic",))

    first = next(e for e in events if e["event"] == "chain")
    assert first["n"] == 1 and first["on"] == 1, first
    assert [s["name"] for s in first["skipped"]] == ["Bertom_DenoiserClassic"], first
    assert "unsupported" in first["skipped"][0]["reason"], "причина без текста ошибки плагина"
    assert stream.entered == 1


@pytest.mark.timeout(60)
def test_host_starts_paused_and_keeps_pause_across_track_swap(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Хост без окна поднимается ДО «Играть»: пока картинка стоит, звук молчит.

    Дорожка шумодава может досчитаться посреди паузы — замена трека паузу и место
    сохраняет: иначе хост заиграл бы сам поверх стоящей картинки.
    """
    track = _track(tmp_path / "track.wav", seconds=10.0)
    monkeypatch.setattr(ed, "_pedalboard", lambda: SimpleNamespace(io=SimpleNamespace(
        AudioFile=_Reader)))
    frag = ed.read_fragment(str(track))
    old = ed.PlayState(int(frag.shape[-1]))
    old.paused = True
    old.pos = 5 * ed.SR
    _, new_state = ed._swap_track(frag, old, None, "")
    assert new_state.paused is True, "замена трека сняла паузу"
    assert new_state.pos == 5 * ed.SR, "замена трека без названного места сдвинула позицию"
    _, named = ed._swap_track(frag, old, 2.0, "")
    assert named.pos == 2 * ed.SR and named.paused is True


class _DyingStream(_Stream):
    """Поток, у которого `write` падает: устройство отвалилось посреди игры."""

    def write(self, audio: Any, sample_rate: float) -> None:
        super().write(audio, sample_rate)
        raise RuntimeError("device lost")


@pytest.mark.timeout(60)
def test_host_write_failure_reaches_the_events_file(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`write` падает → в файле событий `audio_error` с причиной сбоя.

    Иначе хост молчит (устройство 44.1 кГц против 48), а страница глушит свой голос
    в пользу хоста, который не звучит, — человек слышит полную тишину и не знает почему.
    """
    def drive(pipe: _Pipe, pb: _Pedalboard, stream: _Stream, events: Path) -> None:
        def has_error() -> bool:
            try:
                return "audio_error" in events.read_text(encoding="utf-8")
            except OSError:
                return False          # хост ещё не завёл файл — сценарий стартовал первым
        _wait(has_error, "сбой звука")

    pb, stream, events = _run_host(tmp_path, monkeypatch, [_item("A")], drive,
                                   stream=_DyingStream())
    err = next((e for e in events if e["event"] == "audio_error"), None)
    assert err is not None and "device lost" in err["reason"], events
    assert stream.blocks >= 1, "звук не дошёл до потока — падать было нечему"


# --------------------------------------------------------------------------- #
# 2. Сессии на сервере: один хост на клип, снятие по PID, молчание страницы
# --------------------------------------------------------------------------- #
class _Stdin:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def write(self, text: str) -> None:
        for ln in text.splitlines():
            if ln.strip():
                self.lines.append(json.loads(ln))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Proc:
    """Подмена процесса хоста: жив, пока его не «убили»."""

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.stdin = _Stdin()
        self.dead = False
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _wait(lambda: self.returncode is not None, "процесс хоста ушёл", timeout or 10)
        return int(self.returncode or 0)


def _fake_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fx: dict[str, Any] | None = None,
                  src: str = "", pid: int = 4321) -> Any:
    proc = _Proc(pid)
    killed: list[int] = []

    def fake_kill(p: Any, what: str) -> None:
        killed.append(p.pid)
        p.returncode = 1

    monkeypatch.setattr(voicefx, "_kill", fake_kill)
    ses = voicefx._session_new(str(tmp_path / "w"), proc, state_out="", track_file="",
                               chain=[], index=-1, speaker="", fx=fx or {}, device="",
                               start=0.0, src=src, headless=True)
    ses.killed = killed                          # type: ignore[attr-defined]
    with voicefx.VOICE_SESSIONS_LOCK:
        voicefx.VOICE_SESSIONS[ses.sid] = ses
    return ses


@pytest.fixture(autouse=True)
def _clean_sessions() -> Any:
    yield
    with voicefx.VOICE_SESSIONS_LOCK:
        voicefx.VOICE_SESSIONS.clear()


def _client(monkeypatch: pytest.MonkeyPatch) -> Any:
    import webui
    from api import voicefx as apivfx
    webui.app.config["TESTING"] = True
    monkeypatch.setattr(apivfx, "VOICEHOST", {})
    return webui.app.test_client()


def test_one_host_per_clip_and_commands_reach_it(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Второй заход превью на тот же клип второй хост не поднимает; chain и окно доходят.

    Два процесса на клип — это два голоса разом на одном устройстве. Команды идут в
    stdin ЭТОГО процесса: правка цепочки (`chain`, с состоянием плагина в base64) и
    `open_editor` с индексом и путём плагина.
    """
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"x")
    made: list[Any] = []

    def fake_host(**kw: Any) -> Any:
        ses = _fake_session(tmp_path, monkeypatch, fx=voicefx.normalize_fx(kw.get("fx")),
                            src=kw["src"])
        made.append(ses)
        return ses

    monkeypatch.setattr(voicefx, "live_host", fake_host)
    c = _client(monkeypatch)
    fx_a = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
            "vst": [{"path": _item("A")["path"], "on": True}]}
    body = {"xml": str(tmp_path / "clip.xml"), "src": str(cam), "fx": fx_a}

    first = c.post("/api/voicefx_host", json=body).get_json()
    assert first["ok"] is True and first["fresh"] is True and first["headless"] is True, first
    again = c.post("/api/voicefx_host", json=body).get_json()
    assert again["sid"] == first["sid"] and again["fresh"] is False, again
    assert len(made) == 1, "на один клип поднят второй хост"

    fx_ab = {"denoise": fx_a["denoise"],
             "vst": [{"path": _item("A")["path"], "on": True},
                     {"path": _item("B")["path"], "on": False, "state": "QUJD"}]}
    d = c.post("/api/voicefx_live", json={"sid": first["sid"], "cmd": "chain", "fx": fx_ab}).get_json()
    assert d["ok"] is True and d["sent"] is True, d
    sent = made[0].proc.stdin.lines[-1]
    assert sent["cmd"] == "chain" and [i["on"] for i in sent["chain"]] == [True, False], sent
    assert sent["chain"][1]["state_b64"] == "QUJD", "состояние плагина не уехало хосту"
    assert not any(ln["cmd"] == "pause" for ln in made[0].proc.stdin.lines), \
        "правка одних плагинов остановила звук"

    e = c.post("/api/voicefx_host_edit", json={"sid": first["sid"], "index": 1,
                                                "path": _item("B")["path"]}).get_json()
    assert e["ok"] is True and e["window"] is True, e
    assert made[0].proc.stdin.lines[-1] == {"cmd": "open_editor", "index": 1,
                                            "path": _item("B")["path"]}


def test_audio_error_event_shows_in_the_live_status(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Событие `audio_error` из файла событий уезжает странице в статусе хоста.

    По нему превью понимает, что живой звук не работает, и возвращает звук себе —
    поэтому вместе с причиной снимается и `track_ready`.
    """
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        json.dumps({"event": "chain", "n": 1, "on": 1, "skipped": []}),
        json.dumps({"event": "audio_error",
                    "reason": "RuntimeError: sample rate 48000 != 44100"}),
    ]) + "\n", encoding="utf-8")
    ses = _fake_session(tmp_path, monkeypatch)
    ses.events_file = str(events)
    ses.track_ready = True

    voicefx.live_events(ses)
    assert ses.audio_error == "RuntimeError: sample rate 48000 != 44100", ses.audio_error
    assert ses.track_ready is False, "страница всё ещё считала бы, что звучит хост"

    c = _client(monkeypatch)
    got = c.get("/api/voicefx_live", query_string={"sid": ses.sid}).get_json()
    assert got["ok"] is True and got["running"] is True, got
    assert got["audio_error"] == "RuntimeError: sample rate 48000 != 44100", got
    assert got["track_ready"] is False, got


def test_host_stop_kills_own_process_by_pid_and_forgets_the_clip(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Закрыли превью — процесс хоста снят ПО PID, клип забыт, повторная просьба тихая."""
    from api import voicefx as apivfx
    ses = _fake_session(tmp_path, monkeypatch, pid=98765)
    c = _client(monkeypatch)
    xml = str(tmp_path / "clip.xml")
    apivfx.VOICEHOST[xml] = ses.sid

    d = c.post("/api/voicefx_host_stop", json={"sid": ses.sid, "xml": xml}).get_json()
    assert d["ok"] is True and d["stopped"] is True, d
    assert ses.killed == [98765], "снят не тот процесс (или не снят)"     # type: ignore[attr-defined]
    assert ses.done is True and ses.stopped is True
    assert xml not in apivfx.VOICEHOST, "клип остался за погашенным хостом"
    # Хоста уже нет — не ошибка: закрытие превью не должно падать 500-й
    again = c.post("/api/voicefx_host_stop", json={"sid": ses.sid, "xml": xml}).get_json()
    assert again["ok"] is True and again["stopped"] is False, again
    assert ses.killed == [98765], "второй раз убили уже ушедший процесс"  # type: ignore[attr-defined]


@pytest.mark.timeout(60)
def test_live_stop_really_ends_a_child_process(tmp_path: Path) -> None:
    """Настоящий процесс-пустышка снимается `live_stop` — после него живых не остаётся."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.PIPE, text=True)
    ses = voicefx._session_new(str(tmp_path / "w"), proc, state_out="", track_file="",
                               chain=[], index=-1, speaker="", fx={}, device="",
                               start=0.0, src="", headless=True)
    try:
        assert voicefx.live_stop(ses) is True
        proc.wait(timeout=20)
        assert proc.poll() is not None, "процесс хоста остался жив"
        assert ses.done is True
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.timeout(60)
def test_silent_page_takes_the_headless_host_down(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Вкладку закрыли, не погасив хост, — он снимается сам по молчанию опроса.

    Страница спрашивает о хосте ~2 раза в секунду; тишина дольше `HOST_IDLE_TTL` —
    значит, слушать некому, и процесс с плагинами не должен висеть до конца сервера.
    """
    monkeypatch.setattr(voicefx, "STATE_POLL", 0.01)
    monkeypatch.setattr(voicefx, "HOST_IDLE_TTL", 0.1)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.PIPE, text=True)
    ses = voicefx._session_new(str(tmp_path / "w"), proc, state_out="", track_file="",
                               chain=[], index=-1, speaker="", fx={}, device="",
                               start=0.0, src="", headless=True)
    ses.last_poll = time.time() - 5
    try:
        voicefx._watch_live(ses, None)
        proc.wait(timeout=20)
        assert ses.stopped is True and proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_window_open_host_is_not_taken_down_for_silence(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока окно плагина открыто, молчание страницы хост не снимает: человек крутит ручки."""
    monkeypatch.setattr(voicefx, "STATE_POLL", 0.01)
    monkeypatch.setattr(voicefx, "HOST_IDLE_TTL", 0.05)
    ses = _fake_session(tmp_path, monkeypatch)
    ses.last_poll = time.time() - 5
    ses.window = True
    calls = {"n": 0}
    real_poll = ses.proc.poll

    def poll() -> int | None:
        calls["n"] += 1
        if calls["n"] > 5:
            ses.proc.returncode = 0          # процесс «ушёл сам» — цикл кончается
        return real_poll()

    ses.proc.poll = poll                     # type: ignore[method-assign]
    voicefx._watch_live(ses, None)
    assert ses.stopped is False, "хост с открытым окном снят по молчанию страницы"


def test_new_denoise_settings_pause_the_host_and_recompute_in_place(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сменили настройки шумодава — хост встаёт на паузу и пересчитывает дорожку на месте.

    Иначе старый голос играл бы поверх звука страницы. Дорожка подхватывается без
    прыжка (`keep_pos`). А правка одних плагинов дорожку не трогает вовсе.
    """
    started: list[Any] = []
    monkeypatch.setattr(voicefx, "_prepare_track",
                        lambda ses, keep_pos=False: started.append(keep_pos))
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"x")
    dn1 = {"on": True, "engine": "deepfilter", "atten_db": 40}
    dn2 = {"on": True, "engine": "deepfilter", "atten_db": 70}
    ses = _fake_session(tmp_path, monkeypatch, src=str(cam),
                        fx=voicefx.normalize_fx({"denoise": dn1, "vst": []}))
    ses.track_ready = True

    voicefx.live_chain(ses, {"denoise": dn1, "vst": [{"path": _item("A")["path"], "on": True}]})
    time.sleep(0.1)
    assert started == [] and ses.track_ready is True, "правка плагина пересчитала шумодав"
    assert [ln["cmd"] for ln in ses.proc.stdin.lines] == ["chain"]

    voicefx.live_chain(ses, {"denoise": dn2, "vst": [{"path": _item("A")["path"], "on": True}]})
    _wait(lambda: started, "запущен пересчёт дорожки")
    assert started == [True], "замена дорожки без keep_pos: хост прыгнул бы на старт"
    assert [ln["cmd"] for ln in ses.proc.stdin.lines] == ["chain", "chain", "pause"]
    assert ses.track_ready is False, "страница решила бы, что хост уже звучит новой дорожкой"


def test_stale_track_is_not_delivered_after_settings_changed_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока дорожка считалась, настройки сменили ещё раз — старую хосту НЕ отдаём.

    Иначе она затёрла бы более новую, которую отдаст поток следующей смены.
    """
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"x")
    ses = _fake_session(tmp_path, monkeypatch, src=str(cam), fx=voicefx.normalize_fx(
        {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}, "vst": []}))
    delivered: list[str] = []
    monkeypatch.setattr(voicefx, "live_track", lambda s, path, at=0.0: delivered.append(path) or True)

    def slow_track(src: str, dn: dict[str, Any], **kw: Any) -> str:
        ses.fx = voicefx.normalize_fx({"denoise": {"on": True, "engine": "deepfilter",
                                                   "atten_db": 90}, "vst": []})
        return "stale.wav"

    monkeypatch.setattr(voicefx, "denoise_track", slow_track)
    voicefx._prepare_track(ses, True)
    assert delivered == [] and ses.track_ready is False


# --------------------------------------------------------------------------- #
# 3. Дорожка шумодава не считается двумя заказчиками разом
# --------------------------------------------------------------------------- #
def test_denoise_track_is_computed_once_for_concurrent_requests(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Запекание превью и хост просят одну дорожку одновременно — RoFormer идёт один раз.

    Два счёта по одному клипу — вдвое дольше и вдвое больше видеопамяти, а её
    переполнение на Windows вешает машину. Второй заказчик ждёт замок и берёт готовый файл.
    """
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    src = tmp_path / "cam1.mp4"
    src.write_bytes(b"x")
    runs: list[int] = []

    def fake_extract(s: str, out: str, start: float, dur: Any, emit: Any) -> None:
        Path(out).write_bytes(b"RIFF raw")

    def fake_denoise(raw: str, dn: dict[str, Any], work: str, *a: Any, **k: Any) -> str:
        runs.append(1)
        time.sleep(0.3)
        out = os.path.join(work, "dn.wav")
        Path(out).write_bytes(b"RIFF denoised")
        return out

    monkeypatch.setattr(voicefx, "_extract", fake_extract)
    monkeypatch.setattr(voicefx, "_denoise_any", fake_denoise)
    dn = voicefx.normalize_fx({"denoise": {"on": True, "engine": "roformer", "mix": 80}})["denoise"]
    got: list[str] = []
    threads = [threading.Thread(target=lambda: got.append(voicefx.denoise_track(str(src), dn)))
               for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert len(runs) == 1, "дорожка посчитана %d раз(а) одновременно" % len(runs)
    assert len(set(got)) == 1 and os.path.isfile(got[0])


# --------------------------------------------------------------------------- #
# 4. Страница: хост поднимается, chain — только при правке цепочки, гаснет без плагинов
# --------------------------------------------------------------------------- #
def _func_src(src: str, name: str) -> str:
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


@node
def test_page_starts_host_sends_chain_only_on_change_and_stops_without_plugins(
        tmp_path: Path) -> None:
    """Боевые функции страницы на подменах fetch: старт, chain, останов, окно без включённых.

    Плагин добавили — ровно одна команда `chain`, хост не перезапускается; ничего не
    менялось — ни одного запроса; плагины выключили — хост погашен по sid; окну хост
    нужен и без включённых плагинов.
    """
    js = STYLES_JS.read_text(encoding="utf-8")
    src = "\n".join(_func_src(js, n) for n in (
        "voiceFxHasVst", "voiceFxHostOn", "voiceFxLiveOn", "voiceFxWindowOn",
        "voiceFxHostKey", "voiceFxHostPost", "voiceFxHostSync", "voiceFxHostStart",
        "voiceFxHostPoll", "voiceFxHostNotes", "voiceFxHostGone", "voiceFxHostStop"))
    src += "\nfunction vtSrcAt(){return 12.5;}\nfunction vtNow(){return 3;}"   # двери плеера шага 1
    lets = re.search(r"let VFXHOST=\{[^\n]*\};", js)
    assert lets, "не нашлось состояние хоста VFXHOST"
    stand = r"""
const CALLS=[];const LOGS=[];const TIMERS=[];
let VOICEFXSPK='Голос';
let VOICEFXLIVE=null;
""" + lets.group(0) + r"""
globalThis.setTimeout=(fn,ms)=>{TIMERS.push(fn);return TIMERS.length;};
globalThis.clearTimeout=()=>{};
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+m);}
function t(s,v){return String(s).replace(/\{(\w+)\}/g,(m,k)=>(v&&v[k]!=null)?v[k]:m);}
function errText(d){return (d&&d.error)||'';}
function $(){return null;}
function voiceFxStatus(h,x){LOGS.push('status: '+x);}
function vtCam1(){return 'C:/cam1.mp4';}
function vtSrcAt(){return 12.5;}
function pvNow(){return 3;}
function fxDeviceGet(){return '';}
function vtOf(){return {live:1};}
function vtNow(){return 3;}
function vtTick(){LOGS.push('tick');}
function voiceFxRead(){return null;}
// Плеер шага 1 — редактор (ED): по нему voiceFxHostSync берёт xml и камеру клипа,
// когда её не назвали явно (правка ручки панели, кнопка «Настроить»).
const ED={xml:'C:/clip.xml',vt:{}};
let READY=false;
globalThis.fetch=async(url,opt)=>{
  const body=opt&&opt.body?JSON.parse(opt.body):null;
  CALLS.push([url,body]);
  let d={ok:true};
  if(url==='/api/voicefx_host')d={ok:true,sid:'77',fresh:true,track_ready:false,window:false,skipped:[]};
  else if(url.indexOf('/api/voicefx_live?sid=')===0)d={ok:true,running:true,track_ready:READY,window:false,skipped:[],state:''};
  return {json:async()=>d};};
""" + src + r"""
(async()=>{
  const vst=(o)=>({path:'C:/p/'+o+'.vst3',name:'',state:'',on:true});
  const fxA={denoise:{on:true},vst:[vst('A')]};
  const fxAB={denoise:{on:true},vst:[vst('A'),vst('B')]};
  const fxOff={denoise:{on:true},vst:[Object.assign(vst('A'),{on:false})]};
  const urls=()=>CALLS.map(c=>c[0]+(c[1]&&c[1].cmd?':'+c[1].cmd:''));
  const out={};
  await voiceFxHostSync(fxA);
  out.start=urls();out.sid=VOICEFXLIVE&&VOICEFXLIVE.sid;
  out.liveBefore=voiceFxLiveOn();
  const startBody=CALLS[0][1];
  out.startBody={start:startBody.start,speaker:startBody.speaker,src:startBody.src};
  CALLS.length=0;
  await voiceFxHostSync(fxA);out.same=urls();
  CALLS.length=0;
  await voiceFxHostSync(fxAB);out.added=urls();
  out.chainLen=CALLS[0][1].fx.vst.length;
  CALLS.length=0;
  READY=true;await TIMERS.shift()();          // опрос: дорожка досчиталась
  out.liveAfter=voiceFxLiveOn();
  CALLS.length=0;
  await voiceFxHostSync(fxOff);out.off=urls();out.gone=VOICEFXLIVE===null;
  out.stopSid=CALLS[0]&&CALLS[0][1]&&CALLS[0][1].sid;
  CALLS.length=0;
  await voiceFxHostSync(fxOff,{window:true});out.window=urls();
  console.log(JSON.stringify(out));
})();
"""
    path = tmp_path / "host_stand.js"
    path.write_text(stand, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    res = json.loads(proc.stdout.strip().splitlines()[-1])

    assert res["start"] == ["/api/voicefx_host"], res
    assert res["sid"] == "77" and res["liveBefore"] is False, \
        "звук считается живым, пока дорожка шумодава не досчитана"
    assert res["startBody"] == {"start": 12.5, "speaker": "Голос", "src": "C:/cam1.mp4"}, res
    assert res["same"] == [], "ничего не менялось, а хосту что-то ушло"
    assert res["added"] == ["/api/voicefx_live:chain"], \
        "добавили плагин — нужна ровно одна команда chain, без перезапуска хоста"
    assert res["chainLen"] == 2
    assert res["liveAfter"] is True, "дорожка досчиталась, а звук хосту не перешёл"
    assert res["off"] == ["/api/voicefx_host_stop"] and res["gone"] is True, \
        "плагинов не осталось, а хост жив"
    assert res["stopSid"] == "77", "погашен не тот хост"
    assert res["window"] == ["/api/voicefx_host"], "окну не поднялся хост на выключенном плагине"


@pytest.mark.timeout(90)
def test_chain_edit_waits_for_the_open_window_and_then_applies_in_main_thread(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока окно плагина открыто, chain откладывается (событие) и применяется по закрытию.

    Перестройка цепочки идёт в главном потоке (pedalboard требует подготовку плагина
    именно там), а он занят `show_editor`. Звук всё это время идёт прежней доской.
    """
    release = threading.Event()
    threads: list[Any] = []

    def slow_show(self: _Plugin) -> None:
        self.opened += 1
        release.wait(20)

    monkeypatch.setattr(_Plugin, "show_editor", slow_show)
    orig_call = _Plugin.__call__

    def spy(self: _Plugin, chunk: Any, sr: float, reset: bool = False) -> Any:
        if reset:
            threads.append(threading.current_thread() is threading.main_thread())
        return orig_call(self, chunk, sr, reset=reset)

    monkeypatch.setattr(_Plugin, "__call__", spy)

    def drive(pipe: _Pipe, pb: _Pedalboard, stream: _Stream, events: Path) -> None:
        _wait(lambda: stream.blocks >= 3, "хост заиграл")
        pipe.send({"cmd": "open_editor", "index": 0, "path": _item("A")["path"]})
        _wait(lambda: pb.plugins[_item("A")["path"]].opened == 1, "окно открыто")
        boards = len(pb.boards)
        pipe.send({"cmd": "chain", "chain": [_item("A"), _item("B")]})
        _wait(lambda: "chain_deferred" in events.read_text(encoding="utf-8"), "отложено")
        n = stream.blocks
        _wait(lambda: stream.blocks >= n + 3, "звук идёт при открытом окне")
        assert len(pb.boards) == boards, "цепочку перестроили при открытом окне"
        release.set()
        _wait(lambda: len(pb.boards) > boards, "цепочка применена после закрытия окна")

    pb, stream, _events = _run_host(tmp_path, monkeypatch, [_item("A")], drive)
    assert [p.path for p in pb.boards[-1].plugins] == [_item("A")["path"], _item("B")["path"]]
    assert threads and all(threads), "reset позвали не из главного потока"


def test_prepare_track_raw_first_then_swap_denoised(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без кеша шумодава сперва отдаётся сырой трек, затем заменяется на шумодав."""
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"x")
    dn = {"on": True, "engine": "deepfilter", "atten_db": 40}
    ses = _fake_session(tmp_path, monkeypatch, src=str(cam),
                        fx=voicefx.normalize_fx({"denoise": dn, "vst": []}))
    ses.start = 5.0
    delivered: list[tuple[str, Any]] = []
    monkeypatch.setattr(voicefx, "live_track",
                        lambda s, path, at=0.0: delivered.append((path, at)) or True)

    def fake_denoise(src: str, d: dict[str, Any], **kw: Any) -> str:
        if not d.get("on"):
            return "raw_track.wav"
        return "denoised_track.wav"

    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise)
    voicefx._prepare_track(ses, keep_pos=False)
    assert len(delivered) == 2
    assert delivered[0] == ("raw_track.wav", 5.0)
    assert delivered[1] == ("denoised_track.wav", None)
    assert ses.track_ready is True
    assert ses.track_input == "denoised"


def test_prepare_track_denoise_off_gives_raw(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Выключенный шумодав сразу отдаёт сырой звук камеры."""
    cam = tmp_path / "cam1.mp4"
    cam.write_bytes(b"x")
    dn = {"on": False, "engine": "deepfilter", "atten_db": 40}
    ses = _fake_session(tmp_path, monkeypatch, src=str(cam),
                        fx=voicefx.normalize_fx({"denoise": dn, "vst": []}))
    ses.start = 2.0
    delivered: list[tuple[str, Any]] = []
    monkeypatch.setattr(voicefx, "live_track",
                        lambda s, path, at=0.0: delivered.append((path, at)) or True)

    def fake_denoise(src: str, d: dict[str, Any], **kw: Any) -> str:
        return "raw_cam.wav"

    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise)
    voicefx._prepare_track(ses, keep_pos=False)
    assert len(delivered) == 1
    assert delivered[0] == ("raw_cam.wav", 2.0)
    assert ses.track_ready is True
    assert ses.track_input == "raw"


def test_live_payload_carries_track_input(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ _live_payload отдаёт track_input и input клиенту."""
    from api import voicefx as api_voicefx
    ses = _fake_session(tmp_path, monkeypatch)
    ses.track_input = "raw"
    payload = api_voicefx._live_payload(ses)
    assert payload["track_input"] == "raw"
    assert payload["input"] == "raw"
    ses.track_input = "denoised"
    payload = api_voicefx._live_payload(ses)
    assert payload["track_input"] == "denoised"
    assert payload["input"] == "denoised"


@node
def test_page_returns_its_own_sound_when_the_host_audio_fails(tmp_path: Path) -> None:
    """`audio_error` в статусе хоста: живой звук выключен, глушение снято, причина — в панель.

    Хост молчит (устройство не приняло частоту), а страница глушила свой голос
    «потому что звучит хост» — человек слышал полную тишину. Теперь по `audio_error`
    живой звук считается выключенным, и звучит своя дорожка обработанного голоса
    (или камера) — а в панель «Голос» уезжает причина словами.
    """
    prev = (ROOT / "static" / "app" / "60-preview.js").read_text(encoding="utf-8")
    styles = STYLES_JS.read_text(encoding="utf-8")
    src = "\n".join(_func_src(prev, n) for n in (
        "vtOf", "vtAudioCam", "vtPlaying", "vtSrcAt", "vtLiveOn", "vtLiveUpdate", "vtLiveExpect",
        "vtLiveRate", "vtLiveCmd", "vtLiveSid", "vtGate", "vtTick", "vtIsEd", "vtMuteHost"))
    src += "\n" + "\n".join(_func_src(styles, n) for n in (
        "voiceFxLiveOn", "voiceFxHostPoll", "voiceFxHostNotes", "voiceFxHostGone"))
    lets = re.search(r"let VFXHOST=\{[^\n]*\};", styles)
    assert lets, "не нашлось состояние хоста VFXHOST"
    stand = r"""
const CALLS=[];const LOGS=[];const TIMERS=[];const STATUS=[];
let VOICEFXLIVE=null;
let AUDIOERR='';
const VT_LIVE_DRIFT=0.4;
const VT_DRIFT=0.15;
""" + lets.group(0) + r"""
globalThis.setTimeout=(fn,ms)=>{TIMERS.push(fn);return TIMERS.length;};
globalThis.clearTimeout=()=>{};
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+m);}
function t(s,v){return String(s).replace(/\{(\w+)\}/g,(m,k)=>(v&&v[k]!=null)?v[k]:m);}
function errText(d){return (d&&d.error)||'';}
function $(){return null;}
function voiceFxStatus(h,x){STATUS.push(String(x));}
function voiceFxHostSync(){return Promise.resolve(false);}
function voiceFxRead(){return null;}
function vtNow(){return 0;}
const CAM={muted:false};
const EL={paused:true,readyState:4,currentTime:0,
  play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;}};
const PLAY={vids:[CAM],audio:[{ts:0,te:100,src:0}],aidx:0,playing:true,scrubbing:false,
  vt:{on:true,el:EL,path:'C:/voice.wav',live:null}};
globalThis.fetch=async(url,opt)=>{
  const body=opt&&opt.body?JSON.parse(opt.body):null;
  CALLS.push([url,body]);
  return {json:async()=>({ok:true,running:true,track_ready:true,window:false,skipped:[],
    audio_error:AUDIOERR,state:'',track_input:'raw',input:'raw'})};};
""" + src + r"""
(async()=>{
  const out={};
  VOICEFXLIVE={sid:'7',running:true,track_ready:true,window:false,skipped:[],audio_error:''};
  vtTick(PLAY,0);
  out.liveBefore=voiceFxLiveOn();
  out.mutedBefore=CAM.muted;out.liveMuteBefore=!!PLAY.liveMute;
  AUDIOERR='RuntimeError: sample rate 48000 != 44100';
  voiceFxHostPoll(PLAY);
  await TIMERS.shift()();               // опрос: сервер сказал, что звук хоста не идёт
  out.liveAfter=voiceFxLiveOn();
  out.err=VOICEFXLIVE&&VOICEFXLIVE.audio_error;
  out.status=STATUS.slice();
  vtTick(PLAY,0);
  out.liveMuteAfter=!!PLAY.liveMute;
  out.mutedAfter=CAM.muted;
  out.trackPlaying=!EL.paused;
  console.log(JSON.stringify(out));
})();
"""
    path = tmp_path / "audio_error_stand.js"
    path.write_text(stand, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    res = json.loads(proc.stdout.strip().splitlines()[-1])

    assert res["liveBefore"] is True and res["mutedBefore"] is True, res
    assert res["liveMuteBefore"] is True, "до сбоя камера не была заглушена"
    assert res["liveAfter"] is False, "звук хоста сломан, а страница считает его живым"
    assert res["err"] == "RuntimeError: sample rate 48000 != 44100", res
    assert res["liveMuteAfter"] is False, "глушение своего голоса не снято"
    assert res["mutedAfter"] is False or res["trackPlaying"] is True, res
    assert any("живой звук плагинов не работает" in s for s in res["status"]), res["status"]


# --------------------------------------------------------------------------- #
# 5. Громкость живого хоста: одна формула на оба ползунка
# --------------------------------------------------------------------------- #
@node
def test_live_host_gain_is_voice_db_plus_listening_volume(tmp_path: Path) -> None:
    """Громкость хоста = `voice_db` + 20·log10(MEDIA_VOL) — и в команде, и в задании.

    Хост играет своим процессом, мимо страницы: без этого ползунок громкости
    прослушивания не касался бы голоса из плагинов, а громкость голоса спикера
    отвергалась бы роутом. Формула ОДНА (`voiceFxLiveOutDb`): её зовут смена обоих
    ползунков, `applyDbGains` и подъём хоста (`gain_db` задания), иначе они разошлись бы.
    """
    js = STYLES_JS.read_text(encoding="utf-8")
    preview = (ROOT / "static" / "app" / "60-preview.js").read_text(encoding="utf-8")
    src = "\n".join(_func_src(js, n) for n in (
        "voiceFxHostOn", "voiceFxLiveOutDb", "voiceFxLiveGain", "voiceFxHostStart"))
    src += "\n" + "\n".join(_func_src(preview, n) for n in ("vtCam1", "vtSrcAt", "vtNow"))
    lets = re.search(r"let VFXHOST=\{[^\n]*\};", js)
    assert lets, "не нашлось состояние хоста VFXHOST"
    stand = r"""
const CALLS=[];
let VOICEFXSPK='Голос';
let VOICEFXLIVE=null;
let MEDIA_VOL=1;
let CURSTYLE={voice_db:-6,music_db:-20};
""" + lets.group(0) + r"""
globalThis.setTimeout=()=>1;
globalThis.clearTimeout=()=>{};
function uiLog(m){}
function toast(m){}
function t(s,v){return String(s);}
function errText(d){return (d&&d.error)||'';}
function $(){return null;}
function voiceFxStatus(h,x){}
function voiceFxHostNotes(d){}
function voiceFxHostPoll(P){}
function voiceFxHostGone(d,P){}
function fxDeviceGet(){return '';}
function vtOf(P){return {live:null};}
function vtTick(){}
globalThis.fetch=async(url,opt)=>{
  CALLS.push([url,opt&&opt.body?JSON.parse(opt.body):null]);
  return {json:async()=>({ok:true,sid:'77',fresh:true,track_ready:false,window:false,
    skipped:[]})};};
const P={xml:'C:/clip.xml',cams:[{path:'C:/cam1.mp4'}],audio:[{ts:0,te:60,src:100}],
  aidx:0,vids:[{currentTime:100}],vt:{}};
const out={};
VOICEFXLIVE={sid:'7',running:true,track_ready:true};
globalThis.voiceFxHostPost=async(url,body)=>{CALLS.push([url,body]);
  return {ok:true,sid:'77',fresh:true,track_ready:false,window:false,skipped:[]};};
""" + src + r"""
(async()=>{
  // 1. Общая громкость 50 %: −6.02 дБ поверх громкости голоса
  MEDIA_VOL=0.5;
  CURSTYLE.voice_db=-6;
  voiceFxLiveGain();
  out.playedGain=CALLS[0]&&CALLS[0][1];
  // 2. Ноль на прослушивании — тишина, а не «очень тихо»
  MEDIA_VOL=0;
  voiceFxLiveGain();
  out.silent=CALLS[1]&&CALLS[1][1];
  // 3. Ползунок прослушивания вернули — уровень снова считается
  MEDIA_VOL=1;
  voiceFxLiveGain();
  out.fullGain=CALLS[2]&&CALLS[2][1];
  // 4. Задание подъёма хоста несёт тот же итог обоих ползунков
  MEDIA_VOL=0.5;
  await voiceFxHostStart({vst:[]},'k',P);
  const start=CALLS.find(c=>c[0]==='/api/voicefx_host');
  out.startGain=start&&start[1].gain_db;
  out.fx=start&&start[1].fx;
  console.log(JSON.stringify(out));
})();
"""
    path = tmp_path / "live_gain_stand.js"
    path.write_text(stand, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    res = json.loads(proc.stdout.strip().splitlines()[-1])

    assert res["playedGain"]["cmd"] == "gain", res["playedGain"]
    assert abs(res["playedGain"]["db"] - (-6 - 20 * math.log10(2))) < 0.01, res["playedGain"]
    assert res["silent"]["db"] <= -100, "нуль прослушивания не дал тишины: %s" % res["silent"]
    assert res["fullGain"]["db"] == -6.0, res["fullGain"]
    assert abs(res["startGain"] - (-6 - 20 * math.log10(2))) < 0.01, res
    assert res["fx"] == {"vst": []}, "задание подъёма хоста потеряло цепочку"
