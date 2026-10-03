# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Движок RoFormer: настройка, своё окружение, смесь по силе и ключ кеша.

Живая модель здесь НЕ запускается, и ничего не качается: дочерний процесс —
заглушка (`_run_child`), внешние программы — заглушка `subprocess`, а установщик
подменён записью команд. Проверяются контракты, а не «шумодав работает» (это живая
проверка владельца): что уезжает в команду и в профиль, чем различаются ключи кеша,
что выходит из смеси и куда ставится окружение.

Про размер модели: настоящий файл весит 913 097 300 байт, и создать такой в тесте
нельзя. Правило «файл не обрезан» проверяется на уменьшенной константе
(`_small_model_bytes`), а сам размер — отдельным тестом: он взят с живого файла и
в кнопке установки показывается человеку.

Запуск:  py -3.10 -m pytest tests/test_voicefx_roformer.py -q
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers, voicefx, voicefx_sep  # noqa: E402
from core.app_meta import py_exec  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402


# --------------------------------------------------------------------------- #
# Изоляция: своё окружение, свой кеш, чистый JOB установщика
# --------------------------------------------------------------------------- #
# Чистое состояние установщика — теми же ключами, что объявляет core.voicefx_sep.JOB.
# Задаётся значениями, а не снимком ТЕКУЩЕГО состояния: к прогону оно бывает чужим.
# Сторож POST-роутов (tests/test_r8_ic_api.py) обходит все роуты с подменённым
# `Thread.start`; поднятая его запросом «установка» остаётся висеть `running=True`
# до конца сессии, и тогда `start_install()` здесь навсегда отвечает False.
_JOB_CLEAN: dict[str, Any] = {"running": False, "done": False, "i": 0, "n": 0, "step": "",
                              "cur": "", "pct": 0, "error": "", "log": []}


@pytest.fixture(autouse=True)
def sep_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Окружение RoFormer — во временном каталоге, состояние установщика — с нуля.

    Без подмены `ENV_DIR` тест смотрел бы в боевое `~/.reelsi/voice_sep`: «нет
    окружения» там было бы правдой сегодня и ложью завтра, а установка потрогала бы
    чужую папку. JOB задаётся ЧИСТЫМ, а не копией текущего: он модульный, и ход
    установки не должен приезжать из соседнего теста — ни своего, ни чужого файла.

    Путь задаётся И переменной, И модульной константой: `ENV_DIR` и `VOICEFX_DIR`
    вычисляются на импорте, а переменную заново спрашивают изолированный профиль и
    дочерний процесс — тесту нельзя зависеть от того, что лежит на диске машины.
    """
    env = tmp_path / "voice_sep"
    cache = tmp_path / "_voicefx"
    monkeypatch.setenv("REELSI_VOICE_SEP", str(env))
    monkeypatch.setenv("REELSI_VOICEFX_DIR", str(cache))
    monkeypatch.setattr(voicefx_sep, "ENV_DIR", str(env))
    monkeypatch.setattr(voicefx_sep, "JOB", {**_JOB_CLEAN, "log": []})
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(cache))
    return env


@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def _put_env() -> None:
    """Файлы окружения: питон и консольный скрипт audio-separator (пустышки)."""
    for exe in (voicefx_sep.env_python(), voicefx_sep.separator_exe()):
        p = Path(exe)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"stub")


def _put_model(engine: str, size: int = 64) -> None:
    """Файлы модели движка: .ckpt заданного размера и конфиг рядом."""
    d = Path(voicefx_sep.model_dir())
    d.mkdir(parents=True, exist_ok=True)
    (d / voicefx_sep.MODELS[engine]).write_bytes(b"x" * size)
    (d / voicefx_sep.model_config(engine)).write_bytes(b"cfg")


def _small_model_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Уменьшить «размер настоящей модели»: 913 МБ в тесте не создать."""
    monkeypatch.setattr(voicefx_sep, "MODEL_BYTES", 16)


def _write_wav(path: str | Path, seconds: float, rate: int = 48000, channels: int = 1) -> None:
    """Настоящий WAV: длину результата читает сам код, а не заглушка байтами."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(seconds * rate) * channels * 2))


def _duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# --------------------------------------------------------------------------- #
# 1. Настройка: разбор и проверка движка, старая настройка = deepfilter
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,want", [
    ("deepfilter", "deepfilter"),
    ("roformer", "roformer"),
    ("roformer_aggr", "roformer_aggr"),
    ("roformer2", voicefx.DENOISE_ENGINE_DEFAULT),        # похоже на правду, но не оно
    ("", voicefx.DENOISE_ENGINE_DEFAULT),
    (5, voicefx.DENOISE_ENGINE_DEFAULT),                  # число вместо строки
    (True, voicefx.DENOISE_ENGINE_DEFAULT),
    (None, voicefx.DENOISE_ENGINE_DEFAULT),
])
def test_engine_is_parsed_or_falls_back(raw: Any, want: str) -> None:
    """Движок — строго из списка; чужое и битое значение не роняет обработку."""
    assert voicefx.normalize_fx({"denoise": {"engine": raw}})["denoise"]["engine"] == want


def test_old_profile_without_engine_goes_to_the_default_engine() -> None:
    """Профиль без поля движка — движок по умолчанию (RoFormer).

    Движок по умолчанию — выбор владельца: RoFormer берёт шорох одежды поверх речи,
    DeepFilterNet остаётся в списке, но по умолчанию не выбирается. Ключ кеша при
    этом тот же, что у явно записанного движка по умолчанию: старый профиль не
    начинает считаться дважды из-за одного отсутствующего поля.
    """
    old = {"denoise": {"on": True, "atten_db": 55}}
    norm = voicefx.normalize_fx(old)
    assert voicefx.DENOISE_ENGINE_DEFAULT == "roformer"
    assert norm["denoise"] == {"on": True, "engine": voicefx.DENOISE_ENGINE_DEFAULT,
                               "atten_db": 55, "mix": voicefx.DENOISE_MIX_DEFAULT}
    explicit = {"denoise": {"on": True, "engine": voicefx.DENOISE_ENGINE_DEFAULT,
                            "atten_db": 55}}
    assert voicefx.cache_path(__file__, old) == voicefx.cache_path(__file__, explicit), \
        "профиль без поля движка пекётся заново: ключ кеша разошёлся с явным движком"


def test_engine_and_mix_survive_profile_roundtrip(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка при записи профиля пропускает движки и силу, а мусор — нет."""
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path / "speakers"))
    for engine in voicefx.DENOISE_ENGINES:
        good = {"denoise": {"on": True, "engine": engine, "atten_db": 40, "mix": 70},
                "vst": [], "cut": True, "final": True}
        key, _path = speakers.save("Спикер " + engine, {"voice_fx": good})
        assert speakers.load(key) is not None
        assert voicefx.normalize_fx(speakers.load(key)["voice_fx"]) == good, engine
    for bad in ({"denoise": {"engine": "roformer_hard"}},
                {"denoise": {"engine": 5}},
                {"denoise": {"engine": None}},
                {"denoise": {"mix": 150}},
                {"denoise": {"mix": -1}},
                {"denoise": {"mix": "70"}},
                {"denoise": {"mix": True}}):
        with pytest.raises(ValueError):
            speakers.save("Тест", {"voice_fx": bad})


# --------------------------------------------------------------------------- #
# 2. Смесь по силе (синтетические массивы)
# --------------------------------------------------------------------------- #
def test_mix_samples_endpoints_are_exact() -> None:
    """0 % — исходник, 100 % — обработанный, и оба ПОБИТОВО.

    На 100 % результат обязан быть обработанным ровно: «только обработанный» через
    float-формулу подмешивал бы исходник на округлении — а это слышно на тихих
    местах, из-за которых движок и выбирали.
    """
    a = np.array([100, -200, 1000, -1000, 32767, -32768], dtype=np.int16)
    b = np.array([300, 200, 0, 5000, 32767, -32768], dtype=np.int16)
    assert np.array_equal(voicefx.mix_samples(a, b, 0), a)
    assert np.array_equal(voicefx.mix_samples(a, b, 100), b)
    # Сила за пределами 0..100 — тот же зажим, что у остальных настроек
    assert np.array_equal(voicefx.mix_samples(a, b, 250), b)
    assert np.array_equal(voicefx.mix_samples(a, b, -30), a)


def test_mix_samples_half_is_half_sum() -> None:
    """50 % — полусумма исходника и обработанного, посэмплово."""
    a = np.array([100, -200, 1000, -1000], dtype=np.int16)
    b = np.array([300, 200, 0, 5000], dtype=np.int16)
    assert np.array_equal(voicefx.mix_samples(a, b, 50),
                          np.array([200, 0, 500, 2000], dtype=np.int16))


def test_mix_wav_keeps_source_length_and_format(tmp_path: Path) -> None:
    """Смесь блоков: длина — как у исходника, хвост берётся из исходника.

    Модель отдаёт чуть короче входа — по этому файлу режет нарезка и он же уезжает
    в итоговый трек, поэтому «голос кончился на 30 мс раньше картинки» недопустим.
    """
    orig, proc, out = (tmp_path / n for n in ("orig.wav", "proc.wav", "mix.wav"))
    _write_wav(orig, 2.0)
    _write_wav(proc, 1.5)
    voicefx._mix_wav(str(orig), str(proc), str(out), 100, lambda *a, **k: None)
    assert _duration(out) == pytest.approx(2.0, abs=0.001)
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == voicefx.SAMPLE_RATE and w.getnchannels() == 1


def test_mix_wav_rejects_other_format(tmp_path: Path) -> None:
    """Стерео с моно не смешиваем: это ошибка, а не «как-нибудь сведём»."""
    orig, proc, out = (tmp_path / n for n in ("s.wav", "m.wav", "o.wav"))
    _write_wav(orig, 0.5, channels=2)
    _write_wav(proc, 0.5, channels=1)
    with pytest.raises(ReelsiError) as e:
        voicefx._mix_wav(str(orig), str(proc), str(out), 50, lambda *a, **k: None)
    assert e.value.code == "roformer_failed"


# --------------------------------------------------------------------------- #
# 3. Запуск: команда дочернего процесса и путь окружения
# --------------------------------------------------------------------------- #
def test_environment_paths_live_in_own_dir() -> None:
    """Питон, консольный скрипт и модели — внутри СВОЕГО окружения."""
    env = voicefx_sep.ENV_DIR
    assert os.path.dirname(os.path.dirname(voicefx_sep.env_python())) == env
    assert os.path.dirname(os.path.dirname(voicefx_sep.separator_exe())) == env
    assert voicefx_sep.model_dir() == os.path.join(env, "models")
    assert voicefx_sep.model_path("roformer").startswith(voicefx_sep.model_dir())
    # Модель и её конфиг лежат рядом и называются по движку
    assert voicefx_sep.model_path("roformer").endswith(
        "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt")
    assert voicefx_sep.model_path("roformer_aggr").endswith(
        "denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt")
    assert voicefx_sep.model_config("roformer_aggr").endswith("_config.yaml")
    assert voicefx_sep.model_path("deepfilter") == "", "deep-filter — не наша модель"


def test_separate_command_shape(tmp_path: Path) -> None:
    """Команда — тот же консольный скрипт, которым владелец сравнивал движки."""
    cmd = voicefx_sep.separate_cmd("in.wav", str(tmp_path / "out"), "roformer")
    assert cmd[0] == voicefx_sep.separator_exe()
    assert cmd[1] == "in.wav"
    assert cmd[cmd.index("-m") + 1] == voicefx_sep.MODELS["roformer"]
    assert cmd[cmd.index("--model_file_dir") + 1] == voicefx_sep.model_dir()
    assert cmd[cmd.index("--output_dir") + 1] == str(tmp_path / "out")
    assert cmd[cmd.index("--output_format") + 1] == "WAV"
    assert cmd[cmd.index("--single_stem") + 1] == "Dry"


def test_install_commands_never_touch_system_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окружение ставит питон проекта, а пакеты — питон ОКРУЖЕНИЯ.

    Это и есть «системный Python и его пакеты не трогаются»: `pip install` уходит в
    `<env>/python.exe`, и системный numpy 2.2.6 остаётся ровно тем же.
    """
    calls: list[list[str]] = []
    _install_stub(monkeypatch, calls)
    assert voicefx_sep.start_install() is True
    _wait_install()

    venv = calls[0]
    assert venv == voicefx_sep.venv_cmd()
    assert venv[0] == py_exec(), "окружение создаётся не питоном проекта"
    assert "--system-site-packages" in venv, "без системных пакетов нет системного torch/CUDA"
    assert venv[-1] == voicefx_sep.ENV_DIR

    pip = calls[1]
    assert pip[0] == voicefx_sep.env_python(), "pip запущен не из своего окружения"
    assert pip[1:4] == ["-m", "pip", "install"]
    assert "audio-separator[gpu]==%s" % voicefx_sep.SEP_VERSION in pip
    assert "--upgrade-strategy" in pip, "без only-if-needed системные пакеты уехали бы в venv копией"

    # Системный питон звали РОВНО ОДИН раз — на создание venv
    assert [c for c in calls if c[0] == py_exec()] == [venv]
    # Модели качает то же окружение, без входа (--download_model_only)
    for cmd, engine in zip(calls[2:], voicefx_sep.ENGINES):
        assert cmd[0] == voicefx_sep.separator_exe()
        assert cmd[cmd.index("-m") + 1] == voicefx_sep.MODELS[engine]
        assert cmd[cmd.index("--model_file_dir") + 1] == voicefx_sep.model_dir()
        assert "--download_model_only" in cmd
    assert voicefx_sep.JOB["step"] == "done" and voicefx_sep.JOB["error"] == ""


def test_install_drops_truncated_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недокачанная модель убирается перед загрузкой, а не остаётся «на месте».

    audio-separator пропускает уже существующий файл, и обрезанные 900 МБ всплыли бы
    потом ошибкой загрузки модели — «модель есть, а не работает».
    """
    _put_env()
    _small_model_bytes(monkeypatch)
    _put_model("roformer", size=4)                 # обрезанная загрузка
    seen: list[bool] = []
    model = Path(voicefx_sep.model_path("roformer"))

    def on_run(cmd: list[str]) -> None:
        if "--download_model_only" in cmd and cmd[cmd.index("-m") + 1] == voicefx_sep.MODELS["roformer"]:
            seen.append(model.is_file())

    _install_stub(monkeypatch, [], on_run=on_run)
    assert voicefx_sep.start_install() is True
    _wait_install()
    assert seen == [False], "обрезанная модель не убрана — чужой код её пропустит"
    assert voicefx_sep.JOB["step"] == "done"


def test_second_install_is_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """Два установщика в один каталог не поднимаются: pip из двух процессов ломает venv."""
    calls: list[list[str]] = []
    _install_stub(monkeypatch, calls)
    voicefx_sep.JOB["running"] = True
    assert voicefx_sep.start_install() is False
    assert calls == []


def test_require_explains_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет окружения или модели — ошибка с кодом, папкой, моделью и размером загрузки.

    В русском тексте значения подставлены сразу (человек читает именно его), а
    переменные едут рядом — по ним фронт берёт перевод ERR_roformer_missing.
    """
    with pytest.raises(ReelsiError) as e:
        voicefx_sep.require("roformer")
    assert e.value.code == "roformer_missing"
    assert voicefx_sep.ENV_DIR in str(e.value), "в ошибке нет папки окружения"
    assert voicefx_sep.size_text() in str(e.value), "в ошибке нет размера загрузки"
    assert e.value.vars.get("dir") == voicefx_sep.ENV_DIR
    assert e.value.vars.get("size") == voicefx_sep.size_text()

    _put_env()
    _small_model_bytes(monkeypatch)
    with pytest.raises(ReelsiError) as e:
        voicefx_sep.require("roformer_aggr")
    assert e.value.code == "roformer_missing"
    assert voicefx_sep.MODELS["roformer_aggr"] in str(e.value)
    assert voicefx_sep.model_dir() in str(e.value)

    _put_model("roformer_aggr")
    voicefx_sep.require("roformer_aggr")           # теперь всё на месте — не падает
    assert voicefx_sep.installed("roformer_aggr") is True
    assert voicefx_sep.installed("roformer") is False


def test_install_size_is_not_understated() -> None:
    """В кнопке установки — размер, посчитанный по живым файлам моделей."""
    models = len(voicefx_sep.ENGINES) * (voicefx_sep.MODEL_BYTES + voicefx_sep.MODEL_CONFIG_BYTES)
    assert voicefx_sep.download_bytes() == models + voicefx_sep.ENV_DOWNLOAD_BYTES
    assert voicefx_sep.size_text() == "~2.1 ГБ", voicefx_sep.size_text()
    assert voicefx_sep.MODEL_BYTES == 913_097_300, "размер модели взят с живого файла"


def test_installer_does_not_go_to_network_itself() -> None:
    """Сторож: сеть установщика — только дочерние процессы.

    pip и audio-separator качают сами, а в самом модуле нет ни urllib, ни requests,
    ни socket: поэтому «тесты установщика не ходят в сеть» — свойство кода, а не
    договорённость. Появится прямой сетевой вызов — тест краснеет.
    """
    src = (ROOT / "core" / "voicefx_sep.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    net = {"urllib", "requests", "http", "socket", "ftplib", "httpx", "aiohttp"}
    assert not (imported & net), f"установщик полез в сеть сам: {sorted(imported & net)}"


# --------------------------------------------------------------------------- #
# 4. Рендер: дочерний процесс, моно 48 кГц и длина результата
# --------------------------------------------------------------------------- #
def _fake_ffmpeg(calls: list[list[str]], src_seconds: float = 1.0):
    """Подмена `subprocess.run`: ffmpeg «пишет» WAV по ключам своей же команды.

    Реальный ffmpeg тут не нужен: проверяется, что у него ПРОСЯТ моно 48 кГц и что
    длина результата сошлась, а не как он ресемплит.
    """
    def fake_run(cmd: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        channels = int(cmd[cmd.index("-ac") + 1]) if "-ac" in cmd else 2
        rate = int(cmd[cmd.index("-ar") + 1]) if "-ar" in cmd else 44100
        try:
            seconds = _duration(cmd[cmd.index("-i") + 1])
        except Exception:
            seconds = src_seconds                  # файл камеры — не WAV
        _write_wav(cmd[-1], seconds, rate=rate, channels=channels)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake_run


def _fake_separator(calls: list[list[str]], seconds: float | None = None):
    """Заглушка дочернего процесса: пишет «обработанный» WAV в `--output_dir`.

    Файл — 44.1 кГц стерео, как у живой модели: приведение к 48 кГц моно делает
    родитель (`_mono48`), и именно это здесь и проверяется.
    """
    def fake_child(cmd: Any, what: str, timeout: int,
                   emit: Any = voicefx.console_emit,
                   cancelled: Any = None,
                   progress: Any = None,
                   pid_of: Any = None) -> voicefx._ChildRun:
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if progress is not None:
            progress(1, 2)                    # ход работы: его печатает сам RoFormer
            progress(2, 2)
        out_dir = cmd[cmd.index("--output_dir") + 1]
        os.makedirs(out_dir, exist_ok=True)
        dur = seconds if seconds is not None else _duration(cmd[1])
        name = os.path.splitext(os.path.basename(cmd[1]))[0] + "_(dry)_model.wav"
        _write_wav(os.path.join(out_dir, name), dur, rate=44100, channels=2)
        return voicefx._ChildRun([{"done": True}], [], 0)
    return fake_child


@pytest.fixture
def cam1(tmp_path: Path) -> str:
    """Файл камеры 1: содержимое не читается (ffmpeg подменён) — важно наличие."""
    p = tmp_path / "cam1.mp4"
    p.write_bytes(b"fake camera file")
    return str(p)


def test_roformer_render_runs_own_environment_and_resamples(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cam1: str) -> None:
    """RoFormer: дочерний процесс своего окружения, вход/выход — моно 48 кГц.

    Модель отдаёт 44.1 кГц стерео, а голос проекта — 48 кГц моно, поэтому и вход
    (для смеси), и выход приводятся ffmpeg'ом к одному виду.
    """
    _put_env()
    _small_model_bytes(monkeypatch)
    _put_model("roformer")
    ffmpeg: list[list[str]] = []
    sep: list[list[str]] = []
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_ffmpeg(ffmpeg))
    monkeypatch.setattr(voicefx, "_run_child", _fake_separator(sep))
    out = tmp_path / "out.wav"

    voicefx.render(cam1, {"denoise": {"on": True, "engine": "roformer", "mix": 70}},
                   str(out))

    assert out.is_file(), "рендер не положил результат"
    assert len(sep) == 1, f"дочерний процесс звали {len(sep)} раз"
    assert sep[0][0] == voicefx_sep.separator_exe(), "процесс поднят не из своего окружения"
    assert sep[0][1].endswith("in_mono.wav"), "на вход модели уехал не моно-файл"
    mono = [c for c in ffmpeg if "-ac" in c]
    assert len(mono) == 2, "вход и выход RoFormer обязаны быть в одном формате"
    for cmd in mono:
        assert cmd[cmd.index("-ac") + 1] == "1", "не моно: смесь сложила бы разные каналы"
        assert cmd[cmd.index("-ar") + 1] == str(voicefx.SAMPLE_RATE), "не 48 кГц"
    assert _duration(out) == pytest.approx(1.0, abs=0.01), "длина разъехалась со входом"


def test_roformer_render_keeps_length_when_model_is_short(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cam1: str) -> None:
    """Короткий выход модели: длину держит смесь с исходником, а не тишина.

    Замер владельца: сдвига по времени нет и длина та же, но полагаться на это
    нельзя — по запечённому голосу режет нарезка, и он же уезжает в итоговый трек.
    """
    _put_env()
    _small_model_bytes(monkeypatch)
    _put_model("roformer")
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_ffmpeg([]))
    monkeypatch.setattr(voicefx, "_run_child", _fake_separator([], seconds=0.8))
    out = tmp_path / "out.wav"
    voicefx.render(cam1, {"denoise": {"on": True, "engine": "roformer", "mix": 100}},
                   str(out))
    assert _duration(out) == pytest.approx(1.0, abs=0.01)


def test_roformer_render_without_environment_is_clear_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cam1: str) -> None:
    """Окружения нет — код roformer_missing (фронт покажет кнопку установки)."""
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_ffmpeg([]))
    monkeypatch.setattr(voicefx, "_run_child",
                        lambda *a, **k: pytest.fail("процесс подняли без окружения"))
    with pytest.raises(ReelsiError) as e:
        voicefx.render(cam1, {"denoise": {"on": True, "engine": "roformer"}},
                       str(tmp_path / "out.wav"))
    assert e.value.code == "roformer_missing"


def test_roformer_render_failure_is_reported(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cam1: str) -> None:
    """Процесс упал — код roformer_failed, а не молчаливый пустой файл."""
    _put_env()
    _small_model_bytes(monkeypatch)
    _put_model("roformer")
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_ffmpeg([]))
    monkeypatch.setattr(voicefx, "_run_child",
                        lambda *a, **k: voicefx._ChildRun([], [], 3))
    with pytest.raises(ReelsiError) as e:
        voicefx.render(cam1, {"denoise": {"on": True, "engine": "roformer"}},
                       str(tmp_path / "out.wav"))
    assert e.value.code == "roformer_failed"


def test_deepfilter_engine_does_not_need_roformer_env(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cam1: str) -> None:
    """Старый движок работает без окружения RoFormer — он ему не нужен вовсе."""
    monkeypatch.setenv("REELSI_DEEP_FILTER", str(tmp_path / "deep-filter.exe"))
    monkeypatch.setattr(voicefx.subprocess, "run", _fake_ffmpeg([]))
    monkeypatch.setattr(voicefx, "_run_child",
                        lambda *a, **k: pytest.fail("RoFormer позвали на deep-filter"))

    def fake_denoise(raw: str, atten: int, work: str, emit: Any) -> str:
        out = os.path.join(work, "dn.wav")
        _write_wav(out, 1.0)
        return out

    monkeypatch.setattr(voicefx, "_denoise", fake_denoise)
    out = tmp_path / "out.wav"
    voicefx.render(cam1, {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}},
                   str(out))
    assert out.is_file()


# --------------------------------------------------------------------------- #
# 5. Ключ кеша: движок, модель и сила
# --------------------------------------------------------------------------- #
def test_cache_key_follows_engine_model_and_strength(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ кеша различает движки, файл модели и силу обработки.

    Ключ — это «какой шумодав и с какой силой»: смена движка или подменённый файл
    модели обязаны обесценить запечённый трек и окна прослушивания, иначе человек
    слышал бы голос, обработанный другим движком.
    """
    src = __file__
    _put_env()
    _small_model_bytes(monkeypatch)
    _put_model("roformer")
    _put_model("roformer_aggr")

    def key(engine: str, **dn: Any) -> str:
        fx = {"denoise": dict({"on": True, "engine": engine}, **dn)}
        return voicefx.cache_path(src, fx)

    deep = key("deepfilter")
    soft = key("roformer")
    aggr = key("roformer_aggr")
    assert len({deep, soft, aggr}) == 3, "движки делят один ключ кеша"
    assert key("roformer", mix=50) != key("roformer", mix=100), "сила не в ключе"
    assert key("roformer", atten_db=10) != key("roformer", atten_db=90)
    assert key("deepfilter", atten_db=40) != key("deepfilter", atten_db=60)
    # Порядок ключей в профиле — не смена настроек (как было и раньше)
    a = voicefx.cache_path(src, {"denoise": {"mix": 70, "engine": "roformer", "on": True}})
    b = voicefx.cache_path(src, {"denoise": {"on": True, "engine": "roformer", "mix": 70}})
    assert a == b

    # Файл модели подменили (другая версия) — трек посчитан другим шумодавом
    before = key("roformer")
    _put_model("roformer", size=128)
    assert key("roformer") != before, "подменённая модель не обесценила кеш"


# --------------------------------------------------------------------------- #
# 6. Роуты: состояние окружения и установка
# --------------------------------------------------------------------------- #
def test_route_status_reports_engines_and_size(client: Any) -> None:
    """Панель узнаёт, что стоит и сколько качать (кнопка установки — по этому ответу)."""
    d = client.get("/api/voicefx_roformer").get_json()
    assert d["ok"] is True
    assert d["env"] == voicefx_sep.ENV_DIR and d["size"] == voicefx_sep.size_text()
    assert set(d["engines"]) == set(voicefx_sep.ENGINES)
    for engine, info in d["engines"].items():
        assert info["model"] == voicefx_sep.MODELS[engine]
        assert info["installed"] is False
    assert d["job"]["running"] is False


def test_route_install_starts_background_job(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Установка запускается фоном и отвечает сразу — её ход читают тем же роутом."""
    calls: list[list[str]] = []
    _install_stub(monkeypatch, calls)
    d = client.post("/api/voicefx_roformer_install").get_json()
    assert d["ok"] is True and d["started"] is True and d["size"] == voicefx_sep.size_text()
    _wait_install()
    assert calls, "роут не запустил установку"


def test_route_status_forwards_core_error(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка ядра уезжает с кодом — по нему фронт берёт перевод."""
    def boom() -> dict[str, Any]:
        raise ReelsiError(umsg("roformer_missing", "нет окружения"))
    monkeypatch.setattr(voicefx_sep, "status", boom)
    d = client.get("/api/voicefx_roformer").get_json()
    assert d.get("err") == "roformer_missing" and d.get("error")


# --------------------------------------------------------------------------- #
# Заглушки установщика
# --------------------------------------------------------------------------- #
class _StubProc:
    """Заглушка процесса установки: строки вывода, код возврата, никакой сети."""

    def __init__(self, lines: tuple[str, ...], code: int) -> None:
        self.stdout: Iterator[str] = iter(lines)
        self.returncode = code
        self.pid = 4242

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def _install_stub(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]],
                  on_run: Any = None) -> None:
    """Подмена `subprocess.Popen` установщика: команды записываются, ничего не запускается.

    Живой шаг установки — это чужой pip и загрузка моделей из сети; в тестах их нет
    вовсе: заглушка печатает пару строк вывода и, если надо, «скачивает» модель
    файлом в своё окружение.
    """
    def fake_popen(cmd: Any, **kw: Any) -> _StubProc:
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if on_run is not None:
            on_run(cmd)
        if "--download_model_only" in cmd:
            engine = next((e for e in voicefx_sep.ENGINES
                           if voicefx_sep.MODELS[e] == cmd[cmd.index("-m") + 1]), "")
            if engine:
                _put_model(engine)
        return _StubProc(("строка установки\n",), 0)

    monkeypatch.setattr(voicefx_sep.subprocess, "Popen", fake_popen)


def _wait_install(timeout: float = 15.0) -> None:
    """Дождаться конца установки: она идёт своим потоком (как в сервере)."""
    deadline = time.time() + timeout
    while voicefx_sep.JOB["running"] and time.time() < deadline:
        time.sleep(0.02)
    assert not voicefx_sep.JOB["running"], "установка не закончилась за отведённое время"
