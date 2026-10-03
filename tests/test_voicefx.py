# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OT: обработка голоса спикера — шумодав deep-filter, цепочка VST3, прослушивание.

Живые deep-filter, ffmpeg и VST здесь НЕ запускаются: подменяются subprocess и
pedalboard. Проверяются контракты, а не «шумодав работает» (это живая проверка в
отчёте задания): что уходит в команду, что возвращается наружу, что кладётся в
профиль спикера и что зовёт фронт.

Почему так подробно про команды: ошибка в них не падает, а тихо портит результат.
Забытый `-D` у deep-filter сдвигает выход относительно входа — запечённый голос
уезжает от картинки, и нарезка режет не там, где нужно.

Запуск:  python -m pytest tests/test_voicefx.py -q
"""
import base64
import json
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

from core import speakers, voicefx  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402


# --------------------------------------------------------------------------- #
# Изоляция: своя папка кеша и свежий кеш списка плагинов на каждый тест
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path, monkeypatch):
    """Запечённые треки и превью — во временный каталог.

    Без этого тест писал бы в боевую `_voicefx/` рабочей копии, а её сторож
    изоляции (tests/conftest.py) валит сессию за файлы в репозитории. Кеш имён
    VST3 — там же: список спрашивают через дочерний процесс, и файл состояния у
    него свой (REELSI_VST3_SCAN), иначе тест досканировал бы боевой список.
    """
    d = tmp_path / "_voicefx"
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(d))
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    monkeypatch.setenv("REELSI_VST3_SCAN", str(d / "vst3_scan.json"))
    return d


@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


@pytest.fixture
def src_wav(tmp_path):
    """Файл-источник: содержимое не читается (ffmpeg подменён), важно лишь наличие."""
    p = tmp_path / "cam1.mp4"
    p.write_bytes(b"fake camera file")
    return str(p)


# --------------------------------------------------------------------------- #
# Подделки: pedalboard и внешние процессы
# --------------------------------------------------------------------------- #
class _FakeAudioFile:
    """Мини-замена `pedalboard.io.AudioFile`: отдаёт тишину, пишет байты."""

    def __init__(self, target, mode="r", samplerate=0, num_channels=0, format=None):
        self.target = target
        self.mode = mode
        self.samplerate = float(samplerate or voicefx.SAMPLE_RATE)
        self.num_channels = int(num_channels or 1)
        self.frames = int(self.samplerate * 2 + 100) if mode == "r" else 0
        self.pos = 0                       # сколько кадров уже прочитано
        self.written = 0                   # сколько кадров записано

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
        self.written += arr.shape[-1]
        if hasattr(self.target, "write"):
            self.target.write(arr.tobytes())


class _FakePlugin:
    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.raw_state = b""


class _FakeBoard:
    def __init__(self, plugins):
        self.plugins = plugins
        self.calls = []                    # (форма блока, sr, reset)

    def process(self, chunk, sr, reset=True):
        self.calls.append((tuple(chunk.shape), float(sr), bool(reset)))
        return chunk


class _FakePedalboard:
    """Подмена модуля pedalboard: записывает, что и с каким состоянием загрузили.

    Имена внутри файлов (`VST3Plugin.get_plugin_names_for_file`) здесь ни при чём:
    их читает ДОЧЕРНИЙ процесс (core/voicefx_scan), у себя такого вызова нет —
    за этим следит сторож в tests/test_voicefx_proc.py.
    """

    def __init__(self):
        self.loaded = []                   # [(path, plugin_name)]
        self.boards = []
        self.io = SimpleNamespace(AudioFile=_FakeAudioFile)

    def load_plugin(self, path, plugin_name=None):
        self.loaded.append((path, plugin_name))
        return _FakePlugin(path, plugin_name)

    def Pedalboard(self, plugins):
        board = _FakeBoard(list(plugins))
        self.boards.append(board)
        return board


def _write_wav(path, seconds, rate=48000, channels=1):
    """Настоящий WAV (не заглушка байтами): длину результата читает сам render."""
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(seconds * rate) * channels * 2))


def _fake_subprocess(calls, src_seconds=1.0, dn_seconds=0.97):
    """Подмена `subprocess.run`: ffmpeg «пишет» WAV, deep-filter — файл в `-o`.

    Длины взяты из живого замера: `-D` отдаёт хвост на 30 мс короче входа
    (1440 сэмплов при 48 кГц) — именно это добирает `_pad_tail`.
    """
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


def _duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


@pytest.fixture
def denoiser(tmp_path, monkeypatch):
    """Живой deep-filter подменён файлом-заглушкой в REELSI_DEEP_FILTER."""
    exe = tmp_path / "deep-filter.exe"
    exe.write_bytes(b"fake binary")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(exe))
    return str(exe)


def _deep_filter_calls(calls):
    return [c for c in calls if "deep-filter" in os.path.basename(c[0]).lower()]


# --------------------------------------------------------------------------- #
# 1. normalize_fx: дефолты и зажим
# --------------------------------------------------------------------------- #
def test_normalize_fx_defaults():
    """Пустое и битое поле — дефолты: выключенный шумодав, прежний движок, пустой список."""
    empty = {"denoise": {"on": False, "engine": voicefx.DENOISE_ENGINE_DEFAULT,
                         "atten_db": voicefx.DENOISE_DB_DEFAULT,
                         "mix": voicefx.DENOISE_MIX_DEFAULT},
             "vst": [], "cut": False, "final": False}
    assert voicefx.normalize_fx(None) == empty
    assert voicefx.normalize_fx("нет") == empty
    assert voicefx.normalize_fx({}) == empty
    assert voicefx.normalize_fx({"denoise": "нет", "vst": "нет"}) == empty


@pytest.mark.parametrize("raw,want", [(-5, 0), (150, 100), ("x", voicefx.DENOISE_DB_DEFAULT),
                                      (None, voicefx.DENOISE_DB_DEFAULT), (True, voicefx.DENOISE_DB_DEFAULT),
                                      (55, 55)])
def test_normalize_fx_clamps_atten(raw, want):
    """Сила подавления — всегда 0..100; строка и bool в профиле не роняют обработку."""
    assert voicefx.normalize_fx({"denoise": {"on": True, "atten_db": raw}})["denoise"]["atten_db"] == want


def test_normalize_fx_drops_broken_plugins():
    """Битые элементы списка VST отбрасываются, у остальных чинятся поля."""
    raw = {"vst": ["строка", {"name": "без пути"}, {"path": "  "},
                   {"path": " C:/p.vst3 ", "name": 5, "state": "!!!не base64", "on": "да"},
                   {"path": "C:/q.vst3", "name": "Inner", "state": "AQID", "on": False}]}
    vst = voicefx.normalize_fx(raw)["vst"]
    assert [v["path"] for v in vst] == ["C:/p.vst3", "C:/q.vst3"]
    assert vst[0] == {"path": "C:/p.vst3", "name": "", "state": "", "on": True}
    assert vst[1] == {"path": "C:/q.vst3", "name": "Inner", "state": "AQID", "on": False}


def test_normalize_fx_migrates_old_cut_and_final():
    """Старый профиль с `cut`/`final=false` и включённым шумодавом обработку НЕ выключает.

    Галок «для нарезки» и «в итоговый трек» больше нет: решение одно — включён
    ИИ-шумодав (или плагин цепочки), и оно работает везде. Профили, записанные до
    этой правки, лежат с обеими галками снятыми; читаются они миграцией при чтении
    (`normalize_fx`), а не «как записано»: иначе обработка молча выключилась бы у
    всех, кто её настроил.
    """
    old = voicefx.normalize_fx({"denoise": {"on": True, "atten_db": 40},
                                "vst": [], "cut": False, "final": False})
    assert old["denoise"]["on"] is True
    assert old["cut"] is True and old["final"] is True, "старые галки выключили обработку"
    assert voicefx.voice_fx_on({"denoise": {"on": True}, "cut": False, "final": False}) is True

    # Битые галки галками и остаются — решает не они, а обработка
    off = voicefx.normalize_fx({"cut": "да", "final": 1})
    assert off["cut"] is False and off["final"] is False
    assert voicefx.voice_fx_on(off) is False, "обработка нашлась там, где её не настраивали"

    # Включённый плагин цепочки — тоже обработка, и галки это зеркалят
    vst = voicefx.normalize_fx({"vst": [{"path": "C:/p.vst3", "on": True}]})
    assert vst["cut"] is True and vst["final"] is True and voicefx.voice_fx_on(vst) is True

    # Плагин без пути (его выбрасывает нормализация) обработкой не считается
    assert voicefx.voice_fx_on({"vst": [{"path": "", "on": True}]}) is False


@pytest.mark.parametrize("raw,want", [
    ({"engine": "roformer"}, "roformer"),                  # явный выбор
    ({"engine": "roformer_aggr"}, "roformer_aggr"),
    ({"engine": "deepfilter"}, "deepfilter"),
    ({}, voicefx.DENOISE_ENGINE_DEFAULT),                # поля нет — движок по умолчанию
    ({"engine": "roformer2"}, voicefx.DENOISE_ENGINE_DEFAULT),
    ({"engine": 5}, voicefx.DENOISE_ENGINE_DEFAULT),
])
def test_engine_default_is_roformer(raw, want):
    """Движок по умолчанию — RoFormer (мягкий); чужое значение — он же, не «как-нибудь»."""
    assert voicefx.normalize_fx({"denoise": dict(raw, on=True)})["denoise"]["engine"] == want


def test_mix_default_is_full():
    """Доля обработанного по умолчанию — 100 %: смесь с исходником не «на глазок»."""
    assert voicefx.DENOISE_MIX_DEFAULT == 100
    assert voicefx.normalize_fx({"denoise": {"mix": 250}})["denoise"]["mix"] == 100
    assert voicefx.normalize_fx({"denoise": {"engine": "roformer"}})["denoise"]["mix"] == 100


# --------------------------------------------------------------------------- #
# 2. render: команда шумодава
# --------------------------------------------------------------------------- #
def test_render_runs_deep_filter_with_atten_and_delay(tmp_path, monkeypatch, src_wav, denoiser):
    """Шумодав включён: `-a <сила>` и ОБЯЗАТЕЛЬНЫЙ `-D` (компенсация задержки).

    Без `-D` выход сдвинут относительно входа — запечённый голос уедет от картинки
    ровно на задержку фильтра, и нарезка будет резать не там, где нужно."""
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    out = tmp_path / "out.wav"
    voicefx.render(src_wav, {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}}, str(out))

    assert out.is_file(), "render не положил результат"
    dn = _deep_filter_calls(calls)
    assert len(dn) == 1, f"deep-filter вызван {len(dn)} раз: {dn}"
    cmd = dn[0]
    assert cmd[cmd.index("-a") + 1] == "40"
    assert "-D" in cmd, "нет компенсации задержки — тайминг выхода не совпадёт со входом"
    assert cmd[cmd.index("-o") + 1] and cmd[-1].endswith(".wav")
    # Звук извлекает ffmpeg — и тоже ровно один раз
    assert len(calls) == 2 and "ffmpeg" in os.path.basename(calls[0][0]).lower()


def test_render_without_denoise_does_not_call_deep_filter(tmp_path, monkeypatch, src_wav, denoiser):
    """Шумодав выключен: бинарник не зовётся вовсе, в out_wav едет извлечённый звук."""
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    out = tmp_path / "out.wav"
    voicefx.render(src_wav, {"denoise": {"on": False, "atten_db": 40}}, str(out))

    assert out.is_file()
    assert _deep_filter_calls(calls) == [], "deep-filter позвали при выключенном шумодаве"
    assert _duration(str(out)) == pytest.approx(1.0, abs=0.001)


def test_render_keeps_source_duration_after_denoise(tmp_path, monkeypatch, src_wav, denoiser):
    """Запечённый голос длится столько же, сколько исходный звук.

    `-D` выравнивает голос по входу ценой срезанного хвоста (замер на живом звуке:
    30.000 с → 29.970 с, ровно 1440 сэмплов при 48 кГц). Хвост добирается тишиной:
    по этому файлу режет нарезка и он же уезжает в итоговый трек, и «голос кончился
    на 30 мс раньше картинки» — ошибка, которую потом ищут в After Effects руками.
    """
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls, dn_seconds=0.97))
    out = tmp_path / "out.wav"
    lines = []
    voicefx.render(src_wav, {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}}, str(out),
                   emit=lambda line="", **kw: lines.append(line))
    assert _duration(str(out)) == pytest.approx(1.0, abs=0.01), "длина разъехалась с исходной"
    with wave.open(str(out), "rb") as w:                      # хвост — именно тишина
        frames = w.readframes(w.getnframes())
        assert frames[-60:] == b"\x00" * 60
    assert any("хвост" in ln for ln in lines), "добор хвоста не виден в логе"


def test_pad_tail_leaves_longer_file_alone(tmp_path, monkeypatch):
    """Файл уже не короче цели — не трогаем (лишний проход по часу звука ни к чему)."""
    path = tmp_path / "long.wav"
    _write_wav(str(path), 1.0)
    monkeypatch.setattr(voicefx, "atomic_stream_write",
                        lambda *a, **k: pytest.fail("файл перезаписали зря"))
    voicefx._pad_tail(str(path), 0.5, lambda *a, **k: None)


def test_render_extracts_requested_piece(tmp_path, monkeypatch, src_wav, denoiser):
    """start/dur уезжают в ffmpeg: прослушивание берёт тот же кусок, что и «стало»."""
    calls = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess(calls))
    voicefx.render(src_wav, {}, str(tmp_path / "out.wav"), start=12.5, dur=20.0)
    cmd = calls[0]
    assert cmd[cmd.index("-ss") + 1] == "12.500"
    assert cmd[cmd.index("-t") + 1] == "20.000"
    # до -i: иначе ffmpeg декодирует файл с начала и добирается до места минутами
    assert cmd.index("-ss") < cmd.index("-i")
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == str(voicefx.SAMPLE_RATE)


def test_render_reports_missing_source(tmp_path, monkeypatch):
    """Нет исходника — понятная ошибка с кодом, а не падение ffmpeg."""
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess([]))
    with pytest.raises(ReelsiError) as e:
        voicefx.render(str(tmp_path / "нет-такого.mp4"), {}, str(tmp_path / "o.wav"))
    assert e.value.code == "file_not_found"


# --------------------------------------------------------------------------- #
# 3. render: цепочка VST
# --------------------------------------------------------------------------- #
def test_render_vst_chain_order_and_state(tmp_path, monkeypatch, src_wav):
    """Цепочка уезжает в ДОЧЕРНИЙ процесс: порядок как в списке, состояние base64.

    Плагин в процессе сервера не грузится вовсе (JUCE оставляет его потоки жить
    до конца процесса — см. tests/test_voicefx_proc.py): родитель кладёт задание
    на диск и запускает `python -m core.voicefx_render`. Здесь проверяется
    задание — что в него попало и в каком порядке.
    """
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess([]))
    jobs = []
    calls = []

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        calls.append(([str(c) for c in cmd], what, timeout))
        with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
            jobs.append(json.load(f))
        shutil.copyfile(src_wav, jobs[-1]["out"])     # «плагины отработали»
        return voicefx._ChildRun([{"done": True, "frames": 1}], [], 0)

    monkeypatch.setattr(voicefx, "_run_child", fake_child)
    state = base64.b64encode(b"\x01\x02\x03state").decode("ascii")
    fx = {"vst": [
        {"path": "C:/first.vst3", "name": "", "state": state, "on": True},
        {"path": "C:/skip.vst3", "name": "", "state": "", "on": False},
        {"path": "C:/shell.vst3", "name": "Inner", "state": "", "on": True},
    ]}
    out = tmp_path / "out.wav"
    voicefx.render(src_wav, fx, str(out))

    cmd, what, timeout = calls[0]
    assert cmd[1:4] == ["-m", "core.voicefx_render", "--job"], cmd
    assert what == "VST-цепочка" and timeout == voicefx.VST_RENDER_TIMEOUT
    chain = jobs[0]["chain"]
    assert [c["path"] for c in chain] == ["C:/first.vst3", "C:/shell.vst3"], \
        "порядок цепочки или пропуск выключенного плагина сломались"
    assert chain[1]["name"] == "Inner", "имя внутри оболочки не доехало до процесса"
    assert chain[0]["state_b64"] == state, "состояние плагина не доехало до процесса"
    assert all(c["on"] is True for c in chain), "в задание попал выключенный плагин"
    assert jobs[0]["out"] == str(out), "результат процесса кладётся не туда"
    assert out.is_file() and out.stat().st_size > 0


def test_render_without_enabled_vst_launches_nothing(tmp_path, monkeypatch, src_wav):
    """Все плагины выключены — ни pedalboard, ни дочернего процесса (пакета может не быть)."""
    def boom(*a, **kw):
        raise AssertionError("дочерний процесс запустили без включённых плагинов")
    monkeypatch.setattr(voicefx, "_run_child", boom)
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess([]))
    out = tmp_path / "out.wav"
    voicefx.render(src_wav, {"vst": [{"path": "C:/p.vst3", "on": False}]}, str(out))
    assert out.is_file()


# --------------------------------------------------------------------------- #
# 4. cache_path
# --------------------------------------------------------------------------- #
def test_cache_path_follows_settings_not_key_order(src_wav):
    """Ключ кеша меняется от настроек и не зависит от порядка ключей в профиле."""
    a = voicefx.cache_path(src_wav, {"denoise": {"on": True, "atten_db": 40}})
    b = voicefx.cache_path(src_wav, {"denoise": {"on": True, "atten_db": 60}})
    assert a != b, "смена силы шумодава не поменяла ключ кеша"
    c1 = voicefx.cache_path(src_wav, {"denoise": {"on": True, "atten_db": 40},
                                      "vst": [], "cut": True, "final": False})
    c2 = voicefx.cache_path(src_wav, {"final": False, "cut": True,
                                      "vst": [], "denoise": {"on": True, "atten_db": 40}})
    assert c1 == c2, "перестановка ключей в профиле плодит копии одного трека"
    assert a.startswith(str(voicefx.VOICEFX_DIR)) and a.endswith(".wav")


def test_cache_path_follows_source_file(src_wav, tmp_path):
    """Файл камеры перезаписали — ключ другой (иначе отдали бы старый трек)."""
    first = voicefx.cache_path(src_wav, {})
    with open(src_wav, "ab") as f:
        f.write(b" another chunk")
    assert voicefx.cache_path(src_wav, {}) != first


def test_render_cached_reuses_ready_file(monkeypatch, src_wav):
    """Готовый трек отдаётся из кеша — повторного рендера нет.

    Без VST: готовая дорожка шумодава (`denoise_cache_path`) отдаётся без рендера.
    С VST: готовый итог (`cache_path`) отдаётся без рендера.
    """
    # Без VST — путь через denoise_track, кеш по denoise_cache_path
    dn = voicefx.normalize_fx({})["denoise"]
    path = voicefx.denoise_cache_path(src_wav, dn)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF ready")
    monkeypatch.setattr(voicefx.subprocess, "run",
                        lambda *a, **k: pytest.fail("кеш не сработал — полезли в рендер"))
    assert voicefx.render_cached(src_wav, {}) == path


# --------------------------------------------------------------------------- #
# 5. deep_filter_path
# --------------------------------------------------------------------------- #
def test_deep_filter_path_prefers_explicit_env(tmp_path, monkeypatch):
    """REELSI_DEEP_FILTER приоритетнее PATH и своей папки."""
    explicit = tmp_path / "my-deep-filter"
    explicit.write_bytes(b"bin")
    on_path = tmp_path / "deep-filter.exe"
    on_path.write_bytes(b"bin")
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(explicit))
    monkeypatch.setattr(voicefx.shutil, "which", lambda name: str(on_path))
    assert voicefx.deep_filter_path() == str(explicit)


def test_deep_filter_path_finds_local_dir(tmp_path, monkeypatch):
    """Нет переменной и нет в PATH — ищем в своей папке, в том числе в подпапке."""
    monkeypatch.delenv("REELSI_DEEP_FILTER", raising=False)
    monkeypatch.setattr(voicefx.shutil, "which", lambda name: None)
    bin_dir = tmp_path / "deep_filter" / "bin"
    nested = bin_dir / "Release"
    nested.mkdir(parents=True)
    name = "deep-filter.exe" if sys.platform == "win32" else "deep-filter"
    exe = nested / name
    exe.write_bytes(b"bin")
    monkeypatch.setattr(voicefx, "DEEP_FILTER_BIN_DIR", str(bin_dir))
    assert voicefx.deep_filter_path() == str(exe)


def test_deep_filter_missing_is_clear_error(tmp_path, monkeypatch, src_wav):
    """Шумодава нет нигде: deep_filter_path → None, render — ошибка с кодом."""
    monkeypatch.delenv("REELSI_DEEP_FILTER", raising=False)
    monkeypatch.setattr(voicefx.shutil, "which", lambda name: None)
    monkeypatch.setattr(voicefx, "DEEP_FILTER_BIN_DIR", str(tmp_path / "пусто"))
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_subprocess([]))
    assert voicefx.deep_filter_path() is None
    with pytest.raises(ReelsiError) as e:
        voicefx.render(src_wav, {"denoise": {"on": True, "engine": "deepfilter"}}, str(tmp_path / "o.wav"))
    assert e.value.code == "deepfilter_missing"
    assert e.value.vars.get("dir"), "в ошибке нет папки, куда положить бинарник"


def test_deep_filter_failure_is_reported(tmp_path, monkeypatch, src_wav, denoiser):
    """Бинарник упал: код ошибки и хвост вывода, а не молчаливый пустой файл."""
    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        if "ffmpeg" in os.path.basename(cmd[0]).lower():
            with open(cmd[-1], "wb") as f:
                f.write(b"RIFF....WAVEfake")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "InvalidFile: not a wav\n")
    lines = []
    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    with pytest.raises(ReelsiError) as e:
        voicefx.render(src_wav, {"denoise": {"on": True, "engine": "deepfilter"}}, str(tmp_path / "o.wav"),
                       emit=lambda line="", **kw: lines.append(line))
    assert e.value.code == "deepfilter_failed"
    assert "InvalidFile" in str(e.value)
    assert any("deep-filter" in ln for ln in lines), "причина не ушла в лог"


# --------------------------------------------------------------------------- #
# 6. list_vst3
# --------------------------------------------------------------------------- #
def _found(root):
    """Найденные *.vst3 в каталоге теста — то, что делает обход папок.

    Подменяет `voicefx._found_vst3`: `REELSI_VST3_DIRS` только ДОБАВЛЯЕТ каталоги
    к системным, и тест упирался бы в общий каталог плагинов машины (а настоящий
    плагин в тесте — чужая DLL: см. tests/test_voicefx_proc.py).
    """
    out = []
    for base, _dirs, files in os.walk(root):
        out += [os.path.join(base, n) for n in files if n.lower().endswith(".vst3")]
    return sorted(out)


def _fake_scan(monkeypatch, names_for):
    """Заглушка дочернего процесса сканирования: имена — функцией от пути.

    Возвращает список вызовов [(команда, задание или None)]: сам родитель имён не
    читает (это делает процесс — JUCE из живого не выгружается), и проверяется
    здесь ровно то, что он передал и как разобрал ответ. Ответ процесс пишет
    ФАЙЛОМ (родитель им пользуется и после падения процесса), поэтому заглушка
    пишет туда же, куда писал бы настоящий сканер.
    """
    calls = []

    def fake_child(cmd, what, timeout, emit=voicefx.console_emit, cancelled=None):
        cmd = [str(c) for c in cmd]
        if "--job" not in cmd:
            calls.append((cmd, None))          # проверка пакета: ни читать, ни писать
            return voicefx._ChildRun([{"scanned": 0}], [], 0)
        with open(cmd[cmd.index("--job") + 1], encoding="utf-8") as f:
            job = json.load(f)
        calls.append((cmd, job))
        done = {item["key"]: {"ok": True, "names": list(names_for(item["path"]))}
                for item in job["paths"]}
        with open(cmd[cmd.index("--results") + 1], "w", encoding="utf-8") as f:
            json.dump(done, f)
        return voicefx._ChildRun([{"scanned": len(job["paths"])}], [], 0)

    monkeypatch.setattr(voicefx, "_run_child", fake_child)
    return calls


def _scan_only(calls):
    """Вызовы, которые правда читали плагины (у проверки пакета задания нет)."""
    return [(cmd, job) for cmd, job in calls if job is not None]


def test_list_vst3_expands_shells(tmp_path, monkeypatch):
    """Оболочка (несколько имён внутри файла) — по элементу на имя, одиночка — один."""
    (tmp_path / "a.vst3").write_bytes(b"bundle")
    (tmp_path / "Shell.vst3").write_bytes(b"bundle")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))

    def names_for(path):
        return ["One", "Two"] if os.path.basename(path) == "Shell.vst3" else ["a"]
    _fake_scan(monkeypatch, names_for)

    mine = [p for p in voicefx.list_vst3() if os.path.dirname(p["path"]) == str(tmp_path)]
    assert {p["name"] for p in mine} == {"", "One", "Two"}, mine
    assert sorted(p["title"] for p in mine) == ["One", "Two", "a"], mine
    shell = [p for p in mine if p["path"].endswith("Shell.vst3")]
    assert len(shell) == 2 and {p["name"] for p in shell} == {"One", "Two"}
    single = [p for p in mine if p["path"].endswith("a.vst3")]
    assert single[0]["name"] == "" and single[0]["title"] == "a"
def test_list_vst3_finds_plugins_in_vendor_subfolders(tmp_path, monkeypatch):
    """Плагины раскладывают по подпапкам вендора — обход рекурсивный, но не в бандл."""
    vendor = tmp_path / "ValhallaDSP"
    vendor.mkdir()
    (vendor / "Supermassive.vst3").write_bytes(b"bundle")
    inside = tmp_path / "Bundle.vst3" / "Contents" / "x86_64-win"
    inside.mkdir(parents=True)
    (inside / "Bundle.vst3").write_bytes(b"inner")     # внутренний файл бандла
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    calls = _fake_scan(monkeypatch, lambda path: ["plugin"])

    mine = sorted(p["path"] for p in voicefx.list_vst3() if str(tmp_path) in p["path"])
    assert mine == sorted([str(tmp_path / "Bundle.vst3"), str(vendor / "Supermassive.vst3")]), mine
    assert not any("Contents" in p for p in mine), "зашли внутрь бандла — плагин задвоился"
    # В процесс уехали ровно найденные пути: чтение имён — его работа, не наша.
    scans = _scan_only(calls)
    sent = sorted(p["path"] for p in scans[0][1]["paths"] if str(tmp_path) in p["path"])
    assert sent == mine
    assert scans[0][0][1:4] == ["-m", "core.voicefx_scan", "--job"], scans[0]


def test_list_vst3_is_answered_from_disk_cache(tmp_path, monkeypatch):
    """Повторный список без изменений не читает плагины вовсе."""
    (tmp_path / "a.vst3").write_bytes(b"bundle")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    calls = _fake_scan(monkeypatch, lambda path: ["a"])
    first = voicefx.list_vst3()
    assert len(_scan_only(calls)) == 1, "первый список должен был уехать в процесс"

    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)   # кеш в памяти сброшен: остаётся диск
    second = voicefx.list_vst3()
    assert second == first, "список из кеша на диске разошёлся с первым"
    assert len(_scan_only(calls)) == 1, "второй список снова читал плагины вместо кеша"
    # Пакет всё равно проверяется: без pedalboard список бесполезен, и человеку
    # нужна ошибка про пакет, а не выпадающий список, который не работает.
    assert "--check" in calls[-1][0], calls


def test_list_vst3_rescans_only_changed_plugin(tmp_path, monkeypatch):
    """Изменился один плагин — в процесс уезжает только он."""
    old = tmp_path / "old.vst3"
    new = tmp_path / "new.vst3"
    old.write_bytes(b"bundle")
    new.write_bytes(b"bundle")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    calls = _fake_scan(monkeypatch, lambda path: [os.path.basename(path)])

    voicefx.list_vst3()
    monkeypatch.setattr(voicefx, "_VST3_CACHE", None)
    new.write_bytes(b"bundle updated")                  # размер и mtime другие → ключ другой

    got = voicefx.list_vst3()
    scans = _scan_only(calls)
    assert len(scans) == 2, "изменение плагина не привело к досканированию"
    # Чужие папки VST3 (общая папка Windows) сюда не смотрим: тест про наш каталог
    assert [p["path"] for p in scans[1][1]["paths"] if str(tmp_path) in p["path"]] == [str(new)], \
        "досканировали не только изменившийся плагин"
    # Старый плагин остался в списке из кеша, новый прочитан заново. Имя одиночки
    # из файла не берём (оболочка — отдельный случай): заголовок — имя файла.
    assert {p["title"] for p in got if str(tmp_path) in p["path"]} == {"old", "new"}


def test_vst3_without_pedalboard_is_clear_error(monkeypatch, tmp_path):
    """Нет пакета pedalboard — ошибка про пакет, а не пустой список.

    Дочерний процесс здесь настоящий: ошибка рождается в нём (`core/voicefx_scan`),
    а родитель отдаёт её наружу ТЕМ ЖЕ кодом — по коду фронт ищет перевод.
    Поддельный `pedalboard` первым в PYTHONPATH бросает ImportError на импорте:
    настоящий пакет (а с ним JUCE) в тест не попадает.
    """
    (tmp_path / "a.vst3").write_bytes(b"bundle")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pedalboard.py").write_text(
        "raise ImportError('pedalboard нет: подделка для теста')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(stub), ROOT]))

    with pytest.raises(ReelsiError) as e:
        voicefx.list_vst3()
    assert e.value.code == "vst_unavailable"


def test_vst3_scan_timeout_is_not_a_hang(tmp_path, monkeypatch):
    """Зависший процесс снимается по PID: список отдаётся без него, работа не виснет.

    Подробно (что попало в кеш и почему список не пустой) — в
    tests/test_voicefx_proc.py; здесь проверяем само «не виснет»: вызов
    возвращается, а процесс снят по PID.
    """
    (tmp_path / "a.vst3").write_bytes(b"bundle")
    monkeypatch.setenv("REELSI_VST3_DIRS", str(tmp_path))
    monkeypatch.setattr(voicefx, "_found_vst3", lambda: _found(tmp_path))
    killed = []

    class _Child:
        pid = 424242
        returncode = 0

        def poll(self):
            return None                       # «плагин завис»: процесс жив, вывода нет

        def wait(self, timeout=None):
            return 0                          # «снялся»: ждать после kill нечего

    def fake_popen(cmd, **kw):
        return _Child()

    monkeypatch.setattr(voicefx.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(voicefx, "kill_tree", lambda p: killed.append(p.pid))
    monkeypatch.setattr(voicefx, "VST_SCAN_TIMEOUT", 0.2)

    assert voicefx.list_vst3() == [], "зависший плагин попал в список"
    assert killed == [424242], "зависший процесс не снят по PID"


# --------------------------------------------------------------------------- #
# 7. edit_plugin
# --------------------------------------------------------------------------- #
def test_edit_plugin_runs_editor_and_returns_state(tmp_path, monkeypatch):
    """Редактор запускается модулем ядра, состояние едет файлами, ответ — base64."""
    calls = []
    new_state = b"\x08rawstate\x00"

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        assert cmd[1:3] == ["-m", "core.voicefx_editor"], cmd
        assert cmd[cmd.index("--path") + 1] == "C:/p.vst3"
        assert cmd[cmd.index("--name") + 1] == "Inner"
        with open(cmd[cmd.index("--state-in") + 1], "rb") as f:
            assert f.read() == b"\x01\x02\x03"
        with open(cmd[cmd.index("--state-out") + 1], "wb") as f:
            f.write(new_state)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)

    got = voicefx.edit_plugin("C:/p.vst3", "Inner", base64.b64encode(b"\x01\x02\x03").decode("ascii"))
    assert base64.b64decode(got) == new_state
    assert len(calls) == 1


def test_edit_plugin_without_state_skips_state_in(tmp_path, monkeypatch):
    """Состояния нет — `--state-in` не передаём: пустые байты плагину не состояние."""
    calls = []

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        with open(cmd[cmd.index("--state-out") + 1], "wb") as f:
            f.write(b"fresh")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    assert base64.b64decode(voicefx.edit_plugin("C:/p.vst3", "", "")) == b"fresh"
    assert "--state-in" not in calls[0]


def test_edit_plugin_reports_failure(tmp_path, monkeypatch):
    """Плагин не открылся — наверх код voicefx_edit_failed с причиной из stderr."""
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess([str(c) for c in cmd], 1, "", "ImportError: not a VST3\n")
    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    with pytest.raises(ReelsiError) as e:
        voicefx.edit_plugin("C:/p.vst3", "", "")
    assert e.value.code == "voicefx_edit_failed"
    assert "not a VST3" in str(e.value)


# --------------------------------------------------------------------------- #
# 8. Профиль спикера (core/speakers.py)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    "нет",                                                    # не объект
    {"denoise": "нет"},                                       # не объект
    {"denoise": {"on": "да"}},                                # галка не bool
    {"denoise": {"engine": "roformer_hard"}},                 # чужой движок
    {"denoise": {"engine": 5}},                               # движок числом
    {"denoise": {"atten_db": 150}},                           # сила вне 0..100
    {"denoise": {"atten_db": "40"}},                          # сила строкой
    {"denoise": {"mix": 150}},                                # доля вне 0..100
    {"denoise": {"mix": "70"}},                               # доля строкой
    {"denoise": {"нет_такого": 1}},                           # чужой ключ
    {"vst": {}},                                              # не список
    {"vst": ["строка"]},                                      # элемент не объект
    {"vst": [{"path": ""}]},                                  # плагин без пути
    {"vst": [{"path": "p.vst3", "on": 1}]},                   # галка не bool
    {"vst": [{"path": "p.vst3", "чужое": 1}]},                # чужой ключ в элементе
    {"cut": "да"},                                            # галка не bool
    {"чужое": 1},                                             # чужое поле целиком
])
def test_save_rejects_broken_voice_fx(tmp_path, monkeypatch, bad):
    """Битый voice_fx в профиль не пишется: молча сохранённый мусор всплыл бы в нарезке."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        speakers.save("Тест", {"voice_fx": bad})


def test_save_writes_valid_voice_fx(tmp_path, monkeypatch):
    """Корректный voice_fx сохраняется и читается обратно как есть.

    Галок назначения в профиле больше нет (решение одно — включена обработка или
    нет), поэтому в записанном виде их не проверяем: `normalize_fx` выводит их из
    шумодава и цепочки — и они обязаны встать в True у включённой обработки.
    """
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    good = {"denoise": {"on": True, "engine": "roformer", "atten_db": 40, "mix": 70},
            "vst": [{"path": "C:/p.vst3", "name": "Inner", "state": "AQID", "on": True},
                    {"path": "C:/q.vst3", "name": "", "state": "", "on": False}]}
    key, path = speakers.save("Голосистый", {"voice_fx": good})
    assert os.path.isfile(path)
    assert speakers.load(key)["voice_fx"] == good
    # И оно же переживает нормализацию без изменений: строгая проверка и зажим
    # не должны расходиться (иначе профиль «сохранился», а обработка другая).
    norm = voicefx.normalize_fx(speakers.load(key)["voice_fx"])
    assert norm == dict(good, cut=True, final=True)
    assert norm["cut"] is True and norm["final"] is True, "включённая обработка выключила себя"


# --------------------------------------------------------------------------- #
# 9. Роуты (api/voicefx.py)
# --------------------------------------------------------------------------- #
def test_route_vst_list(client, monkeypatch):
    plugins = [{"path": "C:/p.vst3", "name": "", "title": "p"}]
    monkeypatch.setattr(voicefx, "list_vst3", lambda refresh=False: plugins)
    d = client.get("/api/voicefx_vst_list").get_json()
    assert d["ok"] is True and d["plugins"] == plugins


def test_route_vst_list_refresh_is_explicit(client, monkeypatch):
    """`?refresh=1` (кнопка «Обновить список») доходит до ядра, а не теряется."""
    seen = []
    monkeypatch.setattr(voicefx, "list_vst3",
                        lambda refresh=False: (seen.append(refresh), [])[1])
    assert client.get("/api/voicefx_vst_list").get_json()["ok"] is True
    assert client.get("/api/voicefx_vst_list?refresh=1").get_json()["ok"] is True
    assert seen == [False, True], seen


def test_route_vst_list_reports_missing_package(client, monkeypatch):
    """Код ошибки ядра уезжает фронту тем же кодом (по нему ищется перевод)."""
    def boom(refresh=False):
        raise ReelsiError(umsg("vst_unavailable", "Нет пакета pedalboard"))
    monkeypatch.setattr(voicefx, "list_vst3", boom)
    d = client.get("/api/voicefx_vst_list").get_json()
    assert d.get("err") == "vst_unavailable" and d.get("error")


def test_route_edit(client, monkeypatch):
    """Окно плагина открывается ЖИВЫМ: ответ сразу с номером сессии, не после закрытия.

    Раньше запрос висел всё время, пока окно открыто (и до этого считал шумодав
    фрагмента клипа) — отсюда жалоба «окно открывается в фоне и долго». Теперь
    состояние плагина забирается по закрытию окна и уезжает в профиль спикера
    (api/voicefx.py:_save_live), а роут отвечает немедленно.
    """
    seen = {}

    def fake_live(path, name, **kw):
        seen.update(path=path, name=name)
        seen.update(kw)
        return SimpleNamespace(sid="9001", t0=0.0, opened_at=0.0)
    monkeypatch.setattr(voicefx, "edit_plugin_live", fake_live)
    d = client.post("/api/voicefx_edit",
                    json={"path": "C:/p.vst3", "name": "Inner", "state": "AA"}).get_json()
    assert d["ok"] is True and d["sid"] == "9001" and d["running"] is True, d
    assert seen["path"] == "C:/p.vst3" and seen["name"] == "Inner"


def test_route_edit_without_path(client):
    d = client.post("/api/voicefx_edit", json={"name": "Inner"}).get_json()
    assert d.get("err") == "voicefx_no_plugin"


def test_route_preview(client, monkeypatch, tmp_path):
    before, after = str(tmp_path / "before.wav"), str(tmp_path / "after.wav")
    seen = {}

    def fake_preview(src, fx, start):
        seen.update(src=src, fx=fx, start=start)
        return before, after
    monkeypatch.setattr(voicefx, "preview", fake_preview)
    d = client.post("/api/voicefx_preview",
                    json={"src": "C:/cam1.mp4", "fx": {"denoise": {"on": True}}, "start": 30}).get_json()
    assert d["ok"] is True and d["orig"] == before and d["processed"] == after
    assert seen["src"] == "C:/cam1.mp4" and seen["start"] == 30.0
    assert seen["fx"] == {"denoise": {"on": True}}


def test_route_preview_without_src(client):
    """Пустой src: первый файл из папки камер не подставляем — это звук другого клипа."""
    d = client.post("/api/voicefx_preview", json={"fx": {}}).get_json()
    assert d.get("err") == "voicefx_no_src"
    assert "клип" in d.get("error", "")


def test_route_preview_forwards_core_error(client, monkeypatch):
    """Ошибка шумодава уезжает с кодом и переменными — фронт покажет перевод."""
    def boom(src, fx, start):
        raise ReelsiError(umsg("deepfilter_missing", "Нет шумодава deep-filter — положи его в {dir}",
                               dir="C:/bin"))
    monkeypatch.setattr(voicefx, "preview", boom)
    d = client.post("/api/voicefx_preview", json={"src": "C:/cam1.mp4"}).get_json()
    assert d.get("err") == "deepfilter_missing" and d.get("err_vars") == {"dir": "C:/bin"}


def test_route_preview_wraps_unexpected_failure(client, monkeypatch):
    """Неожиданное исключение — код voicefx_preview_failed, а не 500 без объяснения."""
    def boom(src, fx, start):
        raise OSError("ffmpeg не найден")
    monkeypatch.setattr(voicefx, "preview", boom)
    d = client.post("/api/voicefx_preview", json={"src": "C:/cam1.mp4", "start": "мусор"}).get_json()
    assert d.get("err") == "voicefx_preview_failed" and "ffmpeg" in d.get("error", "")


# --------------------------------------------------------------------------- #
# 10. Фронт: блок настроек и запись профиля
# --------------------------------------------------------------------------- #
def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _fn_body(src, start, end=None):
    i = src.index(start)
    return src[i:src.index(end, i)] if end else src[i:]


def test_speaker_voice_block_is_a_summary_only():
    """В профиле спикера — ТОЛЬКО сводка значений (#spkVoice), ручек там нет.

    Крутят обработку голоса в одном месте — в панели «Голос» превью нарезки
    (#pvvoice), где её слышно на звуке клипа. Ручки в профиле означали бы вторую
    копию разметки и второго читателя тех же значений (владелец: «в профиле хранятся
    только значения»).
    """
    html = _read(os.path.join("templates", "index.html"))
    assert html.count('id="spkVoice"') == 1, "в модалке спикера нет (или задвоена) сводка #spkVoice"
    assert html.count('id="pvvoice"') == 1, "в превью нет (или задвоена) панель #pvvoice"
    for el_id in ("spk_dn_on", "spk_dn_atten", "spk_dn_atten_num", "spk_vst_list",
                  "spk_vst_pick", "spk_fx_cut", "spk_fx_final", "spk_fx_start",
                  "spk_fx_orig", "spk_fx_proc", "spk_fx_device", "spk_fx_play"):
        assert 'id="%s"' % el_id not in html, "ручка #%s осталась в профиле спикера" % el_id
    assert 'data-t="Шумодав и плагины на голос камеры 1' in html, "пропала подсказка «!»"
    assert html.index('id="spkVoice"') < html.index('id="spk_ins_photo"'), \
        "блок голоса должен идти перед блоком картинок-вставок"


def test_speaker_editor_shows_summary_and_keeps_voice_fx_untouched():
    """openSpeaker рисует сводку, saveSpeaker voice_fx НЕ трогает вовсе.

    Поле voice_fx пишет только панель превью: если редактор профиля станет писать
    его из своей разметки, он затрёт настройки пустотой — ручек-то у него больше нет.
    """
    js = _read(os.path.join("static", "app", "95-styles.js"))
    save = _fn_body(js, "async function saveSpeaker()", "\nasync function delSpeaker")
    assert "data.voice_fx" not in save, "редактор профиля пишет voice_fx из своей разметки"
    assert "spkVoiceData" not in js and "spkVoiceFill" not in js, "вернулась старая пара блок/чтение"
    opn = _fn_body(js, "function openSpeaker(key)", "\nasync function saveSpeaker")
    assert "voiceFxRender($('spkVoice'),p.voice_fx,{mode:'summary'})" in opn, \
        "openSpeaker не рисует сводку голоса той же функцией разметки"
    # Роуты обработки: панель зовёт окно плагина и список плагинов, а плеер превью —
    # дверь запекания голоса всего клипа (`/api/voicefx_bake`), готовый трек играется
    # через /api/media. Окна по 20 с больше нет: она считала шумодав заново на каждом
    # краю окна, и видео вставало.
    prev = _read(os.path.join("static", "app", "60-preview.js"))
    assert "'/api/voicefx_bake'" in prev and "'/api/media?path='" in prev
    # Ход спрашивают про ЭТОТ клип: у задания своё состояние на каждый xml
    assert "'/api/voicefx_bake_status?xml='" in prev
    assert "'/api/voicefx_preview'" not in prev, "вернулось окно прослушивания по 20 с"
    # Окно плагина — панель ЖИВОГО хоста: хост поднимается отдельной дверью, окно
    # открывается командой в уже звучащем процессе, а не отдельным запуском.
    assert "'/api/voicefx_host'" in js and "'/api/voicefx_host_edit'" in js
    assert "'/api/voicefx_vst_list'" in js
    # Кнопка «Обновить список» — явное действие: список перечитывается с refresh,
    # и это единственное место, где он берётся заново (фоном он не собирается).
    html = _read(os.path.join("templates", "index.html"))
    assert "vstFxList(this,true)" in js, "нет кнопки «Обновить список» с refresh"
    assert "refresh?'?refresh=1':''" in _fn_body(js, "async function vstFxList(", "\nfunction fxDeviceGet"), \
        "фронт не передаёт refresh — кеш в памяти сервера не сбросить"
    assert "spk_vst_pick" not in html, "в разметке остался старый выпадающий список плагинов"
