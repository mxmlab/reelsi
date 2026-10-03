# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно плагина: поверх всех, звук вместе с видео, настройки сохраняются сами.

Три вещи, каждая ломается молча и каждая — из жалобы владельца:

1. **Окно видно сразу и поверх.** Окно плагина рисует JUCE в СВОЁМ процессе, и
   найти его можно только по PID — заголовок пишет плагин (бывает пустым и
   меняется), а сам процесс мог умереть на загрузке. Здесь проверяются три
   действия над найденным окном: TOPMOST, центр рабочей области (заголовок в
   пределах экрана) и передний план, а также право забрать фокус, которое сервер
   выдаёт ребёнку ДО запуска. Настоящий WinAPI подменён: тест без окон.

2. **Звук идёт с видео, а не петлёй.** Процесс окна играет трек голоса клипа (до
   VST) и едет по командам из stdin: `play`/`seek` задают позицию в СЕКУНДАХ
   исходника камеры 1, `pause` останавливает. Настоящие плагины не грузятся —
   цепочка и поток вывода подменены, как в tests/test_voice_live.py.

3. **Сохранение само.** По закрытию окна состояние плагина уезжает в профиль
   спикера клипа ТЕМ ЖЕ путём, что прежняя кнопка: свежий профиль, правка одного
   поля `voice_fx`, остальные поля не тронуты. Редактор подменён, профиль — во
   временной папке.

Запуск:  python -m pytest tests/test_voice_window.py -q
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers, voicefx, voicefx_editor as ed, voicefx_win  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")
STYLES_JS = ROOT / "static" / "app" / "95-styles.js"
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"


# --------------------------------------------------------------------------- #
# 1. Окно поверх всех: подменяемый WinAPI, ни одного настоящего окна
# --------------------------------------------------------------------------- #
class _FakeWinApi:
    """Подмена WinAPI: помнит, что у неё попросили, и отдаёт заданные окна.

    `windows_of_process` отдаёт окна по списку «сколько раз спросили»: так окно
    «появляется» не с первого раза, и видно, что мы его ЖДЁМ, а не считаем, что
    его нет. Ни один вызов не уходит в систему — тест без окон.
    """

    def __init__(self, windows: list[int] | None = None,
                 appear_after: int = 0, exe: str = r"C:\Python310\python.exe") -> None:
        self._windows = list(windows if windows is not None else [4242])
        self._appear_after = appear_after
        self._calls = 0
        self._exe = exe
        self.rects: dict[int, tuple[int, int, int, int]] = {4242: (100, 100, 900, 700)}
        self.work = (0, 0, 1920, 1040)
        self.sets: list[dict[str, Any]] = []
        self.fronts: list[int] = []
        self.foreground: list[int] = []
        self.unowned: list[int] = []

    def process_id(self, hwnd: int) -> int:
        return 777

    def windows_of_process(self, pid: int) -> list[int]:
        self._calls += 1
        if self._calls <= self._appear_after:
            return []
        return list(self._windows)

    def process_exe(self, pid: int) -> str:
        return self._exe

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        return self.rects.get(hwnd)

    def monitor_work_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        return self.work

    def set_pos(self, hwnd: int, x: int, y: int, cx: int, cy: int, flags: int) -> bool:
        self.sets.append({"hwnd": hwnd, "x": x, "y": y, "cx": cx, "cy": cy,
                          "flags": flags})
        return True

    def bring_to_front(self, hwnd: int) -> None:
        self.fronts.append(hwnd)

    def allow_foreground(self, pid: int) -> None:
        self.foreground.append(pid)


def test_windows_are_found_by_pid_and_raised_on_top():
    """Окно процесса поднимается поверх всех и центрируется по рабочей области.

    Окно 800x600 на мониторе 1920x1040 (панель задач съела 140 px по высоте) —
    центр считается по РАБОЧЕЙ области, а не по всему экрану: иначе низ окна уехал
    бы под панель задач, и крестик оказался бы в недоступном месте.
    """
    api = _FakeWinApi()
    res = voicefx_win.prepare_window(777, api=api, timeout=1.0, interval=0.0)

    assert res["windows"] and res["windows"][0]["hwnd"] == 4242, res
    assert res["windows"][0]["topmost"] is True, "окно не помечено «поверх всех»"
    assert api.fronts == [4242], "окно не выведено на передний план"
    set_call = api.sets[0]
    swp_topmost = voicefx_win.SWP_TOPMOST | voicefx_win.SWP_NOOWNERZORDER \
        | voicefx_win.SWP_NOSENDCHANGING
    assert set_call["flags"] == swp_topmost, "SetWindowPos позвали не с TOPMOST"
    # Центр рабочей области: x = (1920-800)/2 = 560, y = (1040-600)/2 = 220
    assert (set_call["x"], set_call["y"]) == (560, 220), set_call
    # Размер окна не меняем: его выбрал плагин (SWP_NOSIZE в TOPMOST-флаге нет —
    # размер передаём тот же, что был, а не «как получится»).
    assert (set_call["cx"], set_call["cy"]) == (800, 600), set_call


def test_window_waits_until_it_appears_but_not_forever():
    """Окно появляется не мгновенно: его ждут, а не считают отсутствующим.

    Плагин грузит библиотеку и строит GUI секундами. Ждать вечно тоже нельзя:
    процесс окна умеет умереть на загрузке — тогда потолок ожидания отдаёт пустой
    ответ, и человек получает доступ к настройкам, а не висящий интерфейс.
    """
    late = _FakeWinApi(appear_after=3)
    res = voicefx_win.prepare_window(777, api=late, timeout=1.0, interval=0.0)
    assert res["windows"], "окно, появившееся не сразу, потеряно"

    never = _FakeWinApi(appear_after=10_000)
    empty = voicefx_win.prepare_window(777, api=never, timeout=0.05, interval=0.0)
    assert empty["windows"] == [], "окно, которого нет, всё равно подняли"
    assert never.fronts == [], "поднимали несуществующее окно"
    assert never.sets == [], "двигали несуществующее окно"


def test_window_above_screen_keeps_its_title_visible():
    """Окно выше рабочей области — заголовок с крестиком остаётся на экране.

    Ровно на это жаловался владелец: «окно открывается без доступного крестика».
    Окно 400x2000 при рабочей области 1040 px по высоте прижимается к её верхнему
    краю с зазором, а не центрируется в минус.
    """
    api = _FakeWinApi()
    api.rects[4242] = (0, 0, 400, 2000)
    res = voicefx_win.prepare_window(777, api=api, timeout=1.0, interval=0.0)

    assert res["windows"]
    assert api.sets[0]["y"] == voicefx_win.MARGIN, api.sets[0]
    assert api.sets[0]["cy"] == 2000, "высоту окна менять нельзя — его размер выбрал плагин"


def test_foreign_process_window_is_not_touched():
    """Чужой процесс под нашим номером не трогаем: PID система переиспользует.

    «Поднять поверх всех» чужое окно — это вмешательство в чужую программу, и
    правило проекта простое: процессы по имени не убиваем, чужое не двигаем.
    """
    api = _FakeWinApi(exe=r"C:\Program Files\Other\editor.exe")
    res = voicefx_win.prepare_window(777, api=api, timeout=0.2, interval=0.0)
    assert res["windows"] == [] and api.sets == [], "тронули чужое окно"


def test_foreground_permission_is_asked_before_the_child_starts():
    """Право на передний план просим ДО запуска процесса окна.

    Система выдаёт его «сейчас или никогда»: если окно уже открылось, просить
    поздно. Поэтому дверь зовётся сразу после `Popen` и до первого кадра окна.
    """
    api = _FakeWinApi()
    assert voicefx_win.allow_foreground(777, api=api) is True
    assert api.foreground == [777], "разрешение не запрошено"


def test_not_windows_is_a_quiet_noop(monkeypatch):
    """Не Windows (или WinAPI не поднялась) — ничего не делаем и не падаем."""
    monkeypatch.setattr(voicefx_win, "cached", lambda: None)
    assert voicefx_win.prepare_window(777)["windows"] == []
    assert voicefx_win.allow_foreground(777) is False


# --------------------------------------------------------------------------- #
# 2. Процесс окна: команды из stdin двигают позицию чтения трека
# --------------------------------------------------------------------------- #
class _FakePlugin:
    """Плагин: помнит состояние и «показывает окно» (окно рисует сам плагин)."""

    def __init__(self, path: str, name: str, state: bytes | None = None) -> None:
        self.path = path
        self.name = name
        self.raw_state = b""
        self.initial = state
        self.opened = 0

    def __call__(self, chunk, sr, reset=False):
        # Подготовка плагина главным потоком (voicefx_editor._prime): фейку нечего считать
        return chunk

    def show_editor(self) -> None:
        self.opened += 1
        if self.initial is not None:
            self.raw_state = self.initial


class _FakeBoard:
    """Цепочка: пропускает звук как есть и помнит, с каким `reset` её позвали."""

    def __init__(self, plugins: list[Any]) -> None:
        self.plugins = list(plugins)
        self.calls: list[tuple[tuple[int, ...], float, bool]] = []

    def __call__(self, chunk: Any, sr: float, reset: bool = True) -> Any:
        self.calls.append((tuple(chunk.shape), float(sr), bool(reset)))
        return chunk


class _FakeStream:
    """Подмена `pedalboard.io.AudioStream`: собирает то, что в него записали."""

    default_output_device_name = "Default Fake Output"

    def __init__(self) -> None:
        self.written: list[np.ndarray] = []
        self.running = False
        self.entered = 0
        self.closed = 0

    def __enter__(self) -> "_FakeStream":
        self.entered += 1
        self.running = True
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.running = False
        self.close()
        return False

    def write(self, audio: Any, sample_rate: float) -> None:
        if not self.running:
            raise RuntimeError("не запущен")
        self.written.append(np.asarray(audio, dtype="float32"))

    def close(self) -> None:
        self.closed += 1


class _RealWavReader:
    """Мини-замена `pedalboard.io.AudioFile`: читает НАШ WAV теми же типами.

    Форма данных — как у pedalboard: (каналы, сэмплы), float32. Никакого нативного
    кода: тесты не грузят ни плагины владельца, ни JUCE.
    """

    def __init__(self, path: str, mode: str = "r", **_kw: Any) -> None:
        self.path, self.mode = path, mode
        with wave.open(path, "rb") as w:
            self.frames = w.getnframes() if mode == "r" else 0

    def __enter__(self) -> "_RealWavReader":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def read(self, n: int) -> Any:
        with wave.open(self.path, "rb") as w:
            data = w.readframes(int(n))
            ch = w.getnchannels()
        arr = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
        return arr.reshape(-1, ch).T if ch > 1 else arr.reshape(1, -1)


class _FakePedalboard:
    """Подмена pedalboard: загрузки, цепочки и чтение WAV (без нативного кода)."""

    def __init__(self, stream: _FakeStream) -> None:
        self.loaded: list[tuple[str, str | None]] = []
        self.boards: list[_FakeBoard] = []
        self.io = SimpleNamespace(AudioFile=_RealWavReader, AudioStream=lambda **kw: stream)

    def load_plugin(self, path: str, plugin_name: str | None = None) -> _FakePlugin:
        self.loaded.append((path, plugin_name))
        return _FakePlugin(path, plugin_name or "")

    def Pedalboard(self, plugins: list[Any]) -> _FakeBoard:
        board = _FakeBoard(plugins)
        self.boards.append(board)
        return board


@pytest.fixture(autouse=True)
def _fake_pedalboard(monkeypatch):
    """`pedalboard` в CI может не стоять (пакет опциональный) — тесты окна читают им WAV.

    Подменяем ту же дверь, что в бою (`ed._pedalboard`), фейком на stdlib: чтение
    тестовых WAV идёт тем же путём, а импорта нативного пакета нет вовсе.
    """
    monkeypatch.setattr(ed, "_pedalboard", lambda: _FakePedalboard(_FakeStream()))


def _track_wav(path: Path, seconds: float = 10.0, channels: int = 2) -> Path:
    """Трек-«линейка»: значение сэмпла кодирует ЕГО НОМЕР (шагом в 1000 сэмплов).

    Так по записанному блоку видно, С КАКОГО МЕСТА читали: `_ruler_at(sample)`
    даёт ожидаемое значение для любой позиции. Монотонная линейка, а не голос:
    тест проверяет позицию чтения, а не звук.
    """
    n = int(seconds * ed.SR)
    idx = np.arange(n, dtype=np.int64)
    mono = ((idx // 1000) % 30000).astype("<i2")
    data = np.repeat(mono.reshape(-1, 1), channels, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(ed.SR)
        w.writeframes(data.tobytes())
    return path


def _ruler_at(sample: int) -> float:
    """Ожидаемое значение трека-линейки на этом сэмпле (см. `_track_wav`)."""
    return float((sample // 1000) % 30000) / 32768.0


def _same_ruler(block: Any, sample: int) -> bool:
    """Первый сэмпл блока — тот же, что у линейки на этом месте.

    Сравнение с допуском: `read_fragment` приводит звук во float32, а один шаг
    линейки — это 1/32768, то есть float32 округляет его на младших битах.
    """
    return abs(float(block[0][0]) - _ruler_at(sample)) < 1e-4


def test_play_seek_and_pause_commands_move_the_reading_position(tmp_path):
    """`seek` встаёт на названную секунду исходника, `pause` останавливает чтение.

    Позиция приходит СЕКУНДАМИ: превью считает её тем же `vtSrcAt` (время
    таймлайна → исходное время камеры 1), что и у своей дорожки. На треке-линейке
    видно, что после `seek at=5` читается ровно 5-я секунда, а не «примерно туда».

    Команды приходят МЕЖДУ блоками — так их и шлёт превью: состояние читается
    перед каждым блоком, а не все сразу на старте.
    """
    track = _track_wav(tmp_path / "track.wav")
    frag = ed.read_fragment(str(track))
    board = _FakeBoard([])
    stream = _FakeStream()
    stop = ed.threading.Event()
    commands: "ed.queue.Queue[dict[str, Any]]" = ed.queue.Queue()
    errors: list[str] = []
    commands.put({"cmd": "play", "at": 0.0})
    blocks = {"n": 0}
    original = stream.write

    def _write(audio: Any, rate: float) -> None:
        original(audio, rate)
        blocks["n"] += 1
        if blocks["n"] == 1:
            commands.put({"cmd": "seek", "at": 5.0})      # «перемотал бегунок»
        elif blocks["n"] == 2:
            commands.put({"cmd": "pause"})                # «поставил на паузу»
        elif blocks["n"] >= 3:
            stop.set()

    stream.write = _write                       # type: ignore[method-assign]

    with stream:
        ed.play_live(frag, board, stream, stop, errors.append, commands)

    assert not errors, errors
    assert stream.running is False, "поток остался запущенным"
    assert len(stream.written) == 3, "звук не пошёл"
    assert _same_ruler(stream.written[0], 0), "игра началась не с нуля трека"
    # Перемотка: трек читается РОВНО с 5-й секунды исходника
    assert _same_ruler(stream.written[1], 5 * ed.SR), \
        "перемотка не сдвинула позицию чтения: %s" % float(stream.written[1][0][0])
    # Пауза: тишина ровно на блок (поток не пересоздаётся, окно не переоткрывается)
    paused = stream.written[2]
    assert paused.shape[-1] == ed.BLOCK, "на паузе блок не того размера"
    assert float(np.abs(paused).max()) == 0.0, "на паузе читался трек, а не тишина"
    # reset=True из рабочего потока звать нельзя (pedalboard: «must be reloaded on the
    # main thread»): хвост после перемотки вымывается прогоном тишины FLUSH, reset — ни разу
    assert not any(c[2] for c in board.calls), board.calls
    assert [c[0] for c in board.calls].count((2, ed.FLUSH)) == 1, board.calls
    assert all(c[1] == ed.SR for c in board.calls), "частота уехала от 48 кГц"


def test_pause_keeps_the_stream_and_resumes_from_the_same_place(tmp_path):
    """Пауза не закрывает поток: игра идёт дальше с того же места, без щелчка.

    Пересоздание потока вывода на каждое «Играть» — это щелчок и задержка на
    открытие устройства. Поэтому пауза пишет тишину того же размера, а позиция
    остаётся на месте: следующий «play» продолжает ровно оттуда.
    """
    track = _track_wav(tmp_path / "track.wav")
    frag = ed.read_fragment(str(track))
    board = _FakeBoard([])
    stream = _FakeStream()
    stop = ed.threading.Event()
    commands: "ed.queue.Queue[dict[str, Any]]" = ed.queue.Queue()
    blocks = {"n": 0}
    original = stream.write

    def _write(audio: Any, rate: float) -> None:
        original(audio, rate)
        blocks["n"] += 1
        if blocks["n"] == 1:
            commands.put({"cmd": "pause"})
        elif blocks["n"] == 2:
            commands.put({"cmd": "play", "at": 0.05})     # то же место, что и было
        elif blocks["n"] >= 4:
            stop.set()

    stream.write = _write                       # type: ignore[method-assign]
    with stream:
        ed.play_live(frag, board, stream, stop, lambda m: None, commands)

    assert stream.entered == 1 and stream.closed == 1, "поток пересоздали на паузе"
    assert float(np.abs(stream.written[1]).max()) == 0.0, "на паузе звук всё равно шёл"
    resumed = stream.written[-1]
    assert _same_ruler(resumed, int(0.05 * ed.SR)), \
        "после паузы игра пошла не с того же места: %s" % float(resumed[0][0])


def test_track_replace_switches_to_the_full_clip(tmp_path):
    """Пока играет фрагмент, шумодав досчитал весь клип — трек подменяется.

    Живое окно открывается ДО шумодава (в этом и смысл «открывается быстро»), а
    потом родитель присылает готовый трек командой `track`. Звук обязан перейти на
    него, не закрывая окно.
    """
    short = _track_wav(tmp_path / "short.wav", seconds=2.0)
    full = _track_wav(tmp_path / "full.wav", seconds=30.0)
    board = _FakeBoard([])
    stream = _FakeStream()
    stop = ed.threading.Event()
    commands: "ed.queue.Queue[dict[str, Any]]" = ed.queue.Queue()
    blocks = {"n": 0}
    original = stream.write

    def _write(audio: Any, rate: float) -> None:
        original(audio, rate)
        blocks["n"] += 1
        if blocks["n"] == 2:
            commands.put({"cmd": "track", "path": str(full), "at": 20.0})
        if blocks["n"] >= 3:
            stop.set()

    stream.write = _write                       # type: ignore[method-assign]
    with stream:
        ed.play_live(ed.read_fragment(str(short)), board, stream, stop,
                     lambda m: None, commands)

    assert blocks["n"] == 3, "замена трека не дала сыграть третий блок"
    after = stream.written[-1]
    assert _same_ruler(after, int(20.0 * ed.SR)), \
        "после замены трека звук пошёл не с названного места: %s" % float(after[0][0])


def test_live_job_passes_the_start_position(monkeypatch, tmp_path):
    """Живой старт играет с места, где стоит бегунок превью.

    Позиция приходит из задания (`start`), а трек грузится уже в рабочем потоке:
    окно к этому моменту открыто, и человек мог промотать превью вперёд — «Играть»
    обязано начаться оттуда, а не с нуля клипа.
    """
    track = _track_wav(tmp_path / "track.wav")
    stream = _FakeStream()
    pb = _FakePedalboard(stream)
    monkeypatch.setattr(ed, "_pedalboard", lambda: pb)
    stop = ed.threading.Event()
    original = stream.write
    n = {"v": 0}

    def _write(audio: Any, rate: float) -> None:
        original(audio, rate)
        n["v"] += 1
        if n["v"] >= 1:
            stop.set()
    stream.write = _write                       # type: ignore[method-assign]

    pointer = tmp_path / "track.json"
    pointer.write_text(json.dumps({"track": str(track), "at": 7.0}), encoding="utf-8")
    job = {"track_file": str(pointer), "start": 7.0, "index": 0, "device": "",
           "chain": [{"path": "C:/p.vst3", "name": "", "state_b64": "", "on": True}]}
    stream.__enter__()
    try:
        ed._prepared_play(job, pb.Pedalboard([]), stream, stop, [], ed.queue.Queue())
    finally:
        stream.__exit__(None, None, None)

    assert stream.written, "живой трек не пошёл"
    assert _same_ruler(stream.written[0], int(7.0 * ed.SR)), \
        "живой звук начался не с места бегунка: %s" % float(stream.written[0][0][0])


# --------------------------------------------------------------------------- #
# 3. Роут живого звука: команда уходит в stdin открытого окна
# --------------------------------------------------------------------------- #
class _FakeStdin:
    """Труба процесса окна: собирает команды строками JSON."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, text: str) -> int:
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Живая сессия окна: процесс-заглушка, труба команд, пустая временная папка."""
    monkeypatch.setattr(voicefx, "VOICE_SESSIONS", {})
    monkeypatch.setattr(voicefx, "VOICE_SESSIONS_LOCK", voicefx.threading.Lock())
    stdin = _FakeStdin()
    proc = SimpleNamespace(pid=31337, stdin=stdin, poll=lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    ses = voicefx._session_new(str(work), proc, state_out=str(work / "out.bin"),
                               track_file="", chain=[], index=0, speaker="",
                               fx={}, device="", start=0.0, src="")
    voicefx.VOICE_SESSIONS[ses.sid] = ses
    return ses, stdin


@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_route_live_sends_the_command_to_the_window(session, client):
    """`/api/voicefx_live` доносит play/seek/pause до stdin ИМЕННО этого окна."""
    ses, stdin = session
    d = client.post("/api/voicefx_live",
                    json={"sid": ses.sid, "cmd": "seek", "at": 12.5}).get_json()

    assert d["ok"] is True and d["sent"] is True, d
    assert len(stdin.lines) == 1, stdin.lines
    sent = json.loads(stdin.lines[0])
    assert sent == {"cmd": "seek", "at": 12.5}, sent

    d = client.post("/api/voicefx_live", json={"sid": ses.sid, "cmd": "pause"}).get_json()
    assert d["ok"] is True
    assert json.loads(stdin.lines[1]) == {"cmd": "pause", "at": 0.0}


def test_route_live_reads_the_session_state(session, client):
    """Опрос окна отдаёт «живо ли оно, готов ли трек, забрано ли состояние»."""
    ses, _stdin = session
    d = client.get("/api/voicefx_live", query_string={"sid": ses.sid}).get_json()
    assert d["ok"] is True and d["running"] is True and d["done"] is False, d
    assert d["track_ready"] is False and d["saved"] is False
    assert d["pid"] == 31337, "номер процесса окна потерялся — окно по нему и ищут"


def test_route_live_without_a_window_is_a_clear_error(client):
    """Окна нет — внятная ошибка с кодом, а не 500 и не пустой ответ.

    По коду фронт снимает с превью глушение своего голоса: живой звук кончился,
    и обработанный голос клипа должен снова играть дорожкой.
    """
    d = client.post("/api/voicefx_live",
                    json={"sid": "нет-такого", "cmd": "play", "at": 1}).get_json()
    assert d.get("ok") is not True and d.get("err") == "voicefx_no_window", d
    assert d.get("error")

    d = client.get("/api/voicefx_live", query_string={"sid": "424242"}).get_json()
    assert d.get("err") == "voicefx_no_window", d


def test_route_live_rejects_an_unknown_command(session, client):
    """Чужая команда не уезжает в трубу окна: там её разбирает чужой процесс."""
    ses, stdin = session
    d = client.post("/api/voicefx_live",
                    json={"sid": ses.sid, "cmd": "полёт"}).get_json()
    assert d.get("err") == "voicefx_no_window" and stdin.lines == [], d


def test_route_live_gain_reaches_the_window_with_its_number(session, client):
    """`gain` доходит до stdin окна ВМЕСТЕ с `db` — иначе ползунок громкости немой.

    Раньше громкость голоса спикера шла этим путём, а команды `gain` в списке
    разрешённых не было: роут отвечал «неизвестная команда», хост не получал
    ничего, и ползунок не влиял на голос, звучащий из его процесса.
    """
    ses, stdin = session
    d = client.post("/api/voicefx_live",
                    json={"sid": ses.sid, "cmd": "gain", "db": -6}).get_json()

    assert d["ok"] is True and d["sent"] is True, d
    assert len(stdin.lines) == 1, stdin.lines
    sent = json.loads(stdin.lines[0])
    assert sent == {"cmd": "gain", "db": -6.0}, sent


def test_route_live_gain_rejects_junk_without_sending(session, client):
    """Мусор и NaN в громкости — ошибка, и в трубу окна НЕ уезжает ничего.

    Хост разбирает число своим процессом: команда с `null`/строкой на месте `db`
    оставила бы прежний уровень молча, а `inf` — прошла бы дальше. Границы
    (−60…+24 дБ) — часть контракта: за ними число уже не «громкость».
    """
    ses, stdin = session
    for bad in ("громко", None, float("nan"), float("inf"), -61, 25):
        d = client.post("/api/voicefx_live",
                        json={"sid": ses.sid, "cmd": "gain", "db": bad}).get_json()
        assert d.get("ok") is not True and d.get("error"), f"{bad!r}: {d}"
        assert stdin.lines == [], f"{bad!r}: команда всё-таки уехала"
    # Без самого поля `db` громкость тоже не команда
    d = client.post("/api/voicefx_live", json={"sid": ses.sid, "cmd": "gain"}).get_json()
    assert d.get("ok") is not True and stdin.lines == [], d
    # Пределы включительно — это ещё громкость
    for good in (-60, 24):
        d = client.post("/api/voicefx_live",
                        json={"sid": ses.sid, "cmd": "gain", "db": good}).get_json()
        assert d["ok"] is True, f"{good!r}: {d}"
    assert [json.loads(x)["db"] for x in stdin.lines] == [-60.0, 24.0], stdin.lines


def test_live_command_is_false_when_the_window_is_gone(session):
    """Процесс окна вышел — команда не пишется в мёртвую трубу, а честно False."""
    ses, stdin = session
    ses.proc.poll = lambda: 0                   # окно закрылось
    assert voicefx.live_command(ses, {"cmd": "play", "at": 0.0}) is False
    assert stdin.lines == []
    assert voicefx.live_session(ses.sid) is not None, "закрытая сессия пропала из реестра"


def test_live_track_writes_the_pointer_and_tells_the_window(session, tmp_path):
    """Замена трека: команда процессу И указатель на диск — для того, кто стартовал позже."""
    ses, stdin = session
    track = _track_wav(tmp_path / "full.wav")
    ses.track_file = str(tmp_path / "track.json")

    assert voicefx.live_track(ses, str(track), 3.5) is True
    assert json.loads(stdin.lines[0]) == {"cmd": "track", "path": str(track), "at": 3.5}
    saved = json.loads(open(ses.track_file, encoding="utf-8").read())
    assert saved == {"track": str(track), "at": 3.5}, saved


# --------------------------------------------------------------------------- #
# 4. Сохранение само: закрыл окно — профиль спикера получил новое состояние
# --------------------------------------------------------------------------- #
CHAIN_ITEM = {"path": "C:/plug.vst3", "name": "Inner", "state": "AQID", "on": True}
OTHER_FIELDS = {
    "label": "Мясников",
    "cut": {"onset_db": 24, "onset_fall": 25.0},
    "lut": {"1": "C:/lut/cam1.cube"},
    "camdirs": ["C:/cam"],
    "breath_model": "model.json",
    "ref": "ref.json",
}


@pytest.fixture
def speaker_dir(tmp_path, monkeypatch):
    """Профиль спикера — во временной папке: боевая `speakers/` личная."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


class _ClosedProc:
    """Процесс окна, который уже закрылся: `wait` сразу отдаёт код выхода."""

    def __init__(self, code: int = 0) -> None:
        self.pid = 5150
        self.stdin = None
        self._code = code

    def wait(self, timeout: float | None = None) -> int:
        return self._code

    def poll(self) -> int:
        return self._code


def test_monitor_writes_the_state_when_the_window_closes(speaker_dir, tmp_path,
                                                       monkeypatch):
    """Монитор окна: процесс вышел — состояние ЗАБРАНО и уехало в профиль спикера.

    Это весь путь «сохранение само» целиком, а не по частям: ждём выход процесса,
    читаем `--state-out`, пишем профиль. Окно сюда не нужно — процесс подменён, и
    ни один плагин не грузится.
    """
    import api.voicefx as route

    monkeypatch.setattr(voicefx.voicefx_win, "cached", lambda: None)   # окон не ищем
    key, path = speakers.save("Мясников", dict(OTHER_FIELDS,
                                               voice_fx={"vst": [dict(CHAIN_ITEM)]}))
    work = tmp_path / "work"
    work.mkdir()
    state_out = work / "out.bin"
    new_state = b"\x05from the window"
    state_out.write_bytes(new_state)

    ses = voicefx._session_new(str(work), _ClosedProc(0), state_out=str(state_out),
                               track_file="", chain=[dict(CHAIN_ITEM)], index=0,
                               speaker=key, fx={}, device="", start=0.0, src="")
    monkeypatch.setattr(voicefx, "_prepare_track", lambda s: None)   # шумодав не считаем

    voicefx._monitor_session(ses, route._save_live)

    assert ses.done is True and ses.exit_code == 0, ses
    assert ses.saved is True and ses.state, "монитор не сохранил состояние"
    after = json.loads(open(path, encoding="utf-8").read())
    assert after["voice_fx"]["vst"][0]["state"] == route_b64(new_state)
    for field in OTHER_FIELDS:
        assert after[field] == OTHER_FIELDS[field], "поле профиля %r поехало" % field


def route_b64(raw: bytes) -> str:
    """Состояние в том виде, в каком оно лежит в профиле (тот же base64)."""
    return base64.b64encode(raw).decode("ascii")


def test_monitor_reports_a_crashed_plugin_and_does_not_write(speaker_dir, tmp_path,
                                                            monkeypatch):
    """Плагин упал (код не 0) — состояние не пишем, причину говорим.

    «Человек закрыл окно» и «плагин упал» — разные события: у закрытия код 0. Пустой
    записью поверх рабочих ручек мы бы молча испортили настройку.
    """
    import api.voicefx as route

    monkeypatch.setattr(voicefx.voicefx_win, "cached", lambda: None)
    key, path = speakers.save("Мясников", {"voice_fx": {"vst": [dict(CHAIN_ITEM)]}})
    before = open(path, "rb").read()
    work = tmp_path / "work"
    work.mkdir()
    state_out = work / "out.bin"
    state_out.write_bytes(b"\x00crash")

    ses = voicefx._session_new(str(work), _ClosedProc(1), state_out=str(state_out),
                               track_file="", chain=[dict(CHAIN_ITEM)], index=0,
                               speaker=key, fx={}, device="", start=0.0, src="")
    monkeypatch.setattr(voicefx, "_prepare_track", lambda s: None)

    voicefx._monitor_session(ses, route._save_live)

    assert open(path, "rb").read() == before, "профиль перезаписан после падения плагина"
    assert ses.saved is False and ses.error, "о падении плагина не сказано"


def test_window_close_writes_the_state_to_the_speaker(speaker_dir):
    """Закрытие окна → профиль спикера получил НОВОЕ состояние плагина, и только оно.

    Это и есть «сохранение само»: тот же путь, что у прежней кнопки, — профиль
    читается свежим, правится ОДНО поле `voice_fx`. Остальные поля (пороги, LUT,
    папки, модель вздохов) уезжают теми же, что лежали: соседние панели не затёрты.
    """
    import api.voicefx as route

    key, path = speakers.save("Мясников", dict(OTHER_FIELDS,
                                               voice_fx={"vst": [dict(CHAIN_ITEM)]}))
    before = json.loads(open(path, encoding="utf-8").read())
    assert before["voice_fx"]["vst"][0]["state"] == "AQID"

    new_state = base64.b64encode(b"\x09new-state").decode("ascii")
    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 0)
    ses = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                               chain=[dict(CHAIN_ITEM)], index=0, speaker=key,
                               fx={}, device="", start=0.0, src="")
    route._save_live_done(ses, new_state, 0)

    after = json.loads(open(path, encoding="utf-8").read())
    assert after["voice_fx"]["vst"][0]["state"] == new_state, "состояние не сохранено"
    assert ses.saved is True
    # Чужие поля профиля не тронуты — ни одного
    for field, value in OTHER_FIELDS.items():
        assert after[field] == value, "поле профиля %r уехало не тем, что было" % field
    assert speakers.load(key)["voice_fx"]["vst"][0]["path"] == "C:/plug.vst3"


def test_window_close_keeps_other_plugins_state(speaker_dir):
    """Состояние уезжает ТОЛЬКО тому плагину, чьё окно открыли.

    В цепочке три плагина, окно открыли у второго: первый и третий обязаны
    остаться со своими состояниями — иначе «покрутил один плагин, слетели все».
    Состояния записаны КОРРЕКТНЫМ base64: битое `normalize_fx` отбрасывает (так и
    задумано — мусор в плагин не уезжает), и подменять его тут нечем.
    """
    import api.voicefx as route

    keep_a = base64.b64encode(b"keep-a").decode("ascii")
    old_b = base64.b64encode(b"old-b").decode("ascii")
    keep_c = base64.b64encode(b"keep-c").decode("ascii")
    chain = [{"path": "C:/a.vst3", "name": "", "state": keep_a, "on": True},
             {"path": "C:/b.vst3", "name": "", "state": old_b, "on": True},
             {"path": "C:/c.vst3", "name": "", "state": keep_c, "on": False}]
    key, path = speakers.save("Мясников", {"voice_fx": {"vst": chain}})

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 0)
    ses = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                               chain=[dict(c) for c in chain], index=1, speaker=key,
                               fx={}, device="", start=0.0, src="")
    new_b = base64.b64encode(b"new-b").decode("ascii")
    route._save_live_done(ses, new_b, 0)

    vst = json.loads(open(path, encoding="utf-8").read())["voice_fx"]["vst"]
    assert [p["state"] for p in vst] == [keep_a, new_b, keep_c], vst
    assert [p["on"] for p in vst] == [True, True, False], "галками правили не мы"


def test_window_close_without_state_does_not_touch_the_profile(speaker_dir):
    """Плагин закрылся с ошибкой и состояния не отдал — профиль остаётся прежним.

    Пустая запись затёрла бы рабочие ручки плагина: молча испортить настройку хуже,
    чем не сохранить новую.
    """
    import api.voicefx as route

    key, path = speakers.save("Мясников", {"voice_fx": {"vst": [dict(CHAIN_ITEM)]}})
    before = open(path, "rb").read()

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 1)
    ses = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                               chain=[dict(CHAIN_ITEM)], index=0, speaker=key,
                               fx={}, device="", start=0.0, src="")
    route._save_live_done(ses, "", 1)

    assert open(path, "rb").read() == before, "профиль перезаписан без состояния"
    assert ses.saved is False and ses.error, "о неудаче не сказано"


def test_window_close_when_the_plugin_was_removed(speaker_dir):
    """Плагин убрали из цепочки, пока окно было открыто — чужому состоянию места нет."""
    import api.voicefx as route

    key, path = speakers.save("Мясников", {"voice_fx": {"vst": []}})
    before = open(path, "rb").read()

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: 0)
    ses = voicefx._session_new(str(speaker_dir), proc, state_out="", track_file="",
                               chain=[dict(CHAIN_ITEM)], index=0, speaker=key,
                               fx={}, device="", start=0.0, src="")
    route._save_live_done(ses, "AQID", 0)

    assert open(path, "rb").read() == before, "состояние записали чужому плагину"
    assert ses.saved is False


# --------------------------------------------------------------------------- #
# 5. Трек для живого звука: голос всего клипа ДО плагинов, из кеша
# --------------------------------------------------------------------------- #
def test_live_track_is_the_denoised_voice_without_vst(monkeypatch, tmp_path):
    """Трек хоста — голос ВСЕГО клипа после шумодава и БЕЗ плагинов.

    Плагины крутит и слышит сам процесс хоста: запеки их в трек — и цепочка
    слышалась бы дважды. А шумодав обязан быть внутри: он не «эффект в окне», а
    часть голоса, которую слушают. Считается он ОБЩИМ кешем дорожки
    (`denoise_track`): тот же файл, что у запекания превью, — иначе окно запускало
    бы RoFormer на весь клип заново, хотя дорожка уже посчитана.
    """
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    seen: list[dict[str, Any]] = []

    def fake_denoise(src, dn, **kw):
        seen.append(dict(dn))
        return str(tmp_path / "track.wav")
    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise)

    def full_render(*a, **k):
        raise AssertionError("трек хоста посчитан полным рендером")
    monkeypatch.setattr(voicefx, "render_cached", full_render)

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(voicefx, "live_track",
                        lambda ses, path, at=0.0: sent.append({"path": path, "at": at}) or True)

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: None)
    ses = voicefx._session_new(str(tmp_path), proc, state_out="", track_file="",
                               chain=[dict(CHAIN_ITEM)], index=0, speaker="",
                               fx={"denoise": {"on": True, "engine": "roformer", "mix": 70},
                                   "vst": [dict(CHAIN_ITEM)]},
                               device="", start=4.0, src=str(tmp_path / "cam1.mp4"))
    (tmp_path / "cam1.mp4").write_bytes(b"x")
    voicefx._prepare_track(ses)

    assert ses.track_ready is True
    assert seen and seen[0]["mix"] == 70, "шумодав не уехал в трек"
    assert sent == [{"path": str(tmp_path / "track.wav"), "at": 4.0}], sent


def test_live_track_without_a_clip_is_not_computed(monkeypatch, tmp_path):
    """Клипа нет — трек не считаем: окно открыто ради ручек, а не ради звука."""
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    called: list[int] = []
    monkeypatch.setattr(voicefx, "denoise_track",
                        lambda *a, **k: called.append(1) or "x.wav")

    proc = SimpleNamespace(pid=1, stdin=None, poll=lambda: None)
    ses = voicefx._session_new(str(tmp_path), proc, state_out="", track_file="",
                               chain=[], index=0, speaker="", fx={}, device="",
                               start=0.0, src="")
    voicefx._prepare_track(ses)

    assert called == [], "шумодав считали, хотя клипа нет"
    assert ses.track_ready is False and not ses.error


# --------------------------------------------------------------------------- #
# 6. Фронт: пока окно открыто, звук идёт через плагин, свой голос молчит
# --------------------------------------------------------------------------- #
def _fn_body(src: str, start: str, end: str | None = None) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)] if end else src[i:]


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` до сбалансированной закрывающей скобки."""
    import re
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


LIVE_STAND = r"""
// ---- мини-плеер: то, что нужно живому звуку ----
let SENT=[],LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function $(id){return null;}
globalThis.fetch=async(url,opt)=>{SENT.push([String(url),opt?JSON.parse(opt.body):null]);
  return {json:async()=>({ok:true,running:true,sent:true})};};
const VT_DRIFT=0.15,VT_QUIET=400,VT_POLL=1000;
// Пороги живого звука — те же числа, что в 60-preview.js: стенд проверяет
// поведение, а не настройку порогов.
const VT_LIVE_DRIFT=0.4,VT_LIVE_MS=250;
let MEDIA_VOL=1;
function voiceWiring(){}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function errText(d){return (d&&(d.error||d.err))||'';}
function clipByXml(){return {job:{speaker:'Голос'}};}
function clipLabel(c){return (c&&c.label)||'';}
let SPEAKERS={};
function progOpen(){} function progItem(){} function progDone(){}
function progUpdate(){} function progMini(){} function hideProg(){}
function vtPrep(){LOGS.push('prep');}
function vtProgOpen(){} function vtPoll(){} function vtUse(){}
function voiceFxLive(){return true;}
// Элемент <video> — ровно те поля, которых касается живой звук
function El(tag){this.tag=tag;this.muted=false;this.played=0;this.pauseLog=0;
  this.paused=true;this.playbackRate=1;this.style={};}
El.prototype.play=function(){this.played++;this.paused=false;return Promise.resolve();};
El.prototype.pause=function(){this.paused=true;this.pauseLog++;};
"""


@node
def test_preview_sends_live_commands_and_mutes_its_own_voice(tmp_path):
    """Пока окно открыто, превью шлёт play/seek/pause и глушит СВОЙ голос.

    Видео остаётся хозяином: команды считаются от его позиции (`vtSrcAt`), а
    звук камеры 1 и дорожка `vt` молчат — иначе слышно два голоса разом. Камеру 2
    никто не глушит: окно обрабатывает только голос камеры 1.
    """
    styles = STYLES_JS.read_text(encoding="utf-8")
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    src = (LIVE_STAND
           + _func_src(preview, "vtOf")
           + _func_src(preview, "vtSrcAt")
           + _func_src(preview, "vtAudioCam")
           + _func_src(preview, "vtPlaying")
           + _func_src(preview, "vtDetach")
           + _func_src(preview, "vtLiveOn")
           + _func_src(preview, "vtSetMute")
           + _func_src(preview, "vtLiveUpdate")
           + _func_src(preview, "vtLiveRate")
           + _func_src(preview, "vtLiveExpect")
           + _func_src(preview, "vtLiveCmd")
           + _func_src(preview, "vtLiveSid")
           + _func_src(preview, "vtLivePause")
           + _func_src(preview, "vtIsEd")
           + _func_src(preview, "vtMuteHost")
           + _func_src(styles, "voiceFxLiveOn"))
    body = r"""
let VOICEFXLIVE={sid:'42',running:true};
const P={audio:[{ts:0,te:10,src:100}],aidx:0,playing:false,scrubbing:false,vids:[],
  vt:{on:true,el:{paused:false,pause:function(){this.paused=true;}},path:'C:/v.wav'}};
const cam1=new El('video'),cam2=new El('video');P.vids=[cam1,cam2];
// Пауза: команда «pause», свой голос не глушим — на паузе слышно, что делает ручка
vtLiveUpdate(P,3);
const paused={sent:SENT.slice(),mute:cam1.muted,voiceMute:!!P.voiceMute};
// Играем: команда идёт через живое окно, звук камеры 1 заглушён, камера 2 — нет
P.playing=true;
vtLiveUpdate(P,3);
const playing={sent:SENT.slice(),mute:cam1.muted,cam2:cam2.muted,voice:!!P.voiceMute,
  trackPaused:P.vt.el.paused,voiceMute:!!P.voiceMute};
// Стык монтажа: другой кусок — позиция прыгает, уходит seek, а не play
P.audio=[{ts:0,te:10,src:100},{ts:10,te:20,src:300}];P.aidx=1;P.vids[0].currentTime=300;
vtLiveUpdate(P,10.5);
const splice={sent:SENT.slice()};
// Окно закрылось: звук возвращается дорожке vt, и команд больше не шлём
VOICEFXLIVE=null;
const before=SENT.length;
vtLiveUpdate(P,12);
const closed={mute:cam1.muted,voice:!!P.voiceMute,added:SENT.length-before,
  liveOn:vtLiveOn(P)};
console.log(JSON.stringify({paused:paused,playing:playing,splice:splice,closed:closed}));
"""
    path = tmp_path / "live_stand.js"
    path.write_text(src + "\n" + body, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
    res = json.loads(lines[-1])

    # Пауза: одна команда, свой голос на месте (ручку слышно на паузе)
    assert [c[1]["cmd"] for c in res["paused"]["sent"]] == ["pause"], res["paused"]
    assert res["paused"]["mute"] is False, "на паузе звук камеры заглушён — ручку не слышно"
    # Играем: та же пауза уже не повторяется, звук камеры 1 молчит, камера 2 звучит
    playing = res["playing"]
    assert [c[1]["cmd"] for c in playing["sent"]] == ["pause", "play"], playing["sent"]
    assert playing["mute"] is True, "звук камеры 1 не заглушён — слышно два голоса"
    assert playing["cam2"] is False, "заглушили камеру 2, а её окно не обрабатывает"
    assert playing["trackPaused"] is True, "дорожка обработанного голоса продолжает играть"
    assert playing["voiceMute"] is False, "флаг дорожки спорит с живым окном"
    # Стык: seek на позицию НОВОГО куска (10.5 с монтажа = 300.5 с исходника),
    # а не продолжение с прежней
    cmds = [c[1] for c in res["splice"]["sent"]]
    assert cmds[-1]["cmd"] == "seek" and cmds[-1]["at"] == 300.5, cmds
    # Окно закрылось: команды прекратились, звук вернулся дорожке
    closed = res["closed"]
    assert closed["added"] == 0, "после закрытия окна превью всё ещё шлёт команды"
    assert closed["mute"] is False, "звук не вернулся дорожке vt"
    assert closed["liveOn"] is False, res


def test_panel_marks_the_live_voice_in_one_switch():
    """`voiceFxLiveOn` — одна дверь «звук идёт через плагин»: её читает и плеер.

    Второй копии решения «окно открыто» в превью нет: состояние живёт на странице
    (VOICEFXLIVE, ставит панель), а плеер спрашивает эту функцию.
    """
    js = STYLES_JS.read_text(encoding="utf-8")
    body = _fn_body(js, "function voiceFxLiveOn(){", "\n// Разметка блока")
    assert "VOICEFXLIVE" in body and "running" in body, body
    prev = PREVIEW_JS.read_text(encoding="utf-8")
    tick = _fn_body(prev, "function vtTick(P,tm){", "\n// Пересчёт после правки ручки")
    assert "vtLiveOn(P)" in tick and "vtLiveUpdate(P,tm)" in tick, \
        "плеер не отдаёт звук живому окну"


def test_live_route_is_declared_with_both_verbs():
    """Дверь живого звука объявлена один раз и умеет читать и писать."""
    route = (ROOT / "api" / "voicefx.py").read_text(encoding="utf-8")
    assert route.count('@bp.route("/api/voicefx_live"') == 1, "дверь живого звука задвоена"
    body = _fn_body(route, '@bp.route("/api/voicefx_live"', "\n@bp.route")
    assert 'methods=["GET", "POST"]' in body, "у двери нет ни чтения, ни записи"


@node
def test_live_playback_does_not_seek_while_the_video_just_plays(tmp_path):
    """Обычное проигрывание 3 с — ни одной перемотки окну, только один «play».

    Регрессия приёмки VW: расхождение считалось от МЕСТА последней команды, и через
    0,4 с проигрывания уходил seek — и так каждые 0,4 с: щелчок и сброс плагинов
    (хвост ревербератора обрубался). Правильно — от того места, где звук окна
    должен быть сейчас (команда + прошедшее время).
    """
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    styles = STYLES_JS.read_text(encoding="utf-8")
    src = (LIVE_STAND
           + "".join(_func_src(preview, n) for n in (
               "vtOf", "vtSrcAt", "vtAudioCam", "vtPlaying", "vtDetach", "vtLiveOn", "vtLiveUpdate",
               "vtLiveRate", "vtLiveExpect", "vtLiveCmd", "vtLiveSid", "vtLivePause",
               "vtIsEd", "vtMuteHost"))
           + _func_src(styles, "voiceFxLiveOn"))
    body = r"""
let NOW=1000;Date.now=()=>NOW;
let VOICEFXLIVE={sid:'42',running:true};
const P={audio:[{ts:0,te:60,src:100}],aidx:0,playing:true,scrubbing:false,vids:[],
  vt:{on:false,el:null,path:''}};
P.vids=[new El('video'),new El('video')];
for(let k=0;k<=180;k++){NOW=1000+k*16.7;vtLiveUpdate(P,2+k*0.0167);}
console.log(JSON.stringify(SENT.map(c=>c[1].cmd)));
"""
    path = tmp_path / "live_play.js"
    path.write_text(src + "\n" + body, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    cmds = json.loads(proc.stdout.strip().splitlines()[-1])
    assert cmds.count("seek") <= 1 and cmds.count("play") <= 1, cmds


def test_set_pos_passes_hwnd_topmost_as_a_pointer():
    """HWND_TOPMOST уходит в SetWindowPos указателем, а не голым int -1.

    Регрессия приёмки VW: с `-1` ctypes передаёт 32-битный int, в 64-битном
    процессе SetWindowPos молча возвращал 0 — окно плагина оставалось на (-3,-26),
    заголовок с крестиком за краем экрана, и не поверх остальных.
    """
    import ctypes
    from core import voicefx_win

    calls = []

    class _User32:
        def SetWindowPos(self, *a):
            calls.append(a)
            return 1

    api = voicefx_win.WinApi.__new__(voicefx_win.WinApi)
    api.user32 = _User32()
    api.kernel32 = None
    assert api.set_pos(123, 10, 20, 300, 200, 0) is True
    after = calls[0][1]
    assert isinstance(after, ctypes.c_void_p), type(after)
    assert after.value == ctypes.c_void_p(-1).value
