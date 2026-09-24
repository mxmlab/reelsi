# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Модульные тесты для core/cutjob.py: параметры, выравнивание, стадии process_pair.

Проверяется бизнес-логика и принятие решений без загрузки моделей и без запуска After Effects:
- CutOptions: значения по умолчанию из cutstages.DEFAULT_THRESHOLDS; from_namespace со всеми
  полями, со значениями по умолчанию при неполном Namespace, с игнорированием лишних атрибутов.
- _forced_align: успешное уточнение таймингов через falign_cli, логирование строк stdout,
  поведение при отсутствии выходного файла (возврат исходных слов и лог ошибки stderr),
  поведение при исключении subprocess (возврат исходных слов и лог исключения),
  проброс ReelsiError, гарантированная очистка временных файлов fin/fout в блоке finally.
- process_pair:
  - форматы входа камер: путь строкой и списком, фильтрация пустых слотов;
  - режим no_cut (только субтитры): ограничение одной камерой, замер длительности через wave,
    пропуск VAD и поиска межкамерных оффсетов;
  - мультикам: поиск оффсетов для дополнительных камер, назначение камер через assign_cameras;
  - одиночная камера: отсутствие назначения камер (assign=None);
  - транскрипция: пропуск при (no_subs and no_dedup), использование кэша исходника,
    попадание в старый кэш без модели для дефолтной модели, промах кэша с вызовом Whisper
    и сохранением кэша;
  - вызов _forced_align при forced_align=True и отсутствие вызова при False;
  - дедупликация: find_repeat_ranges против find_restarts в зависимости от флага restarts;
  - логирование удалённых повторов/рестартов (rep_log);
  - агрессивный режим: поиск и удаление филлеров, пауз тишины (dead_air_ranges),
    фильтрация сегментов без распознанных слов;
  - фильтрация коротких сегментов нулевой длительности в кадрах (< 1 кадра при 60 fps);
  - построение субтитров и SRT (no_srt=False vs True);
  - экспорт в After Effects (opts.ae=True): вызов xml2ae.to_ae_full, логирование нефатальных
    ошибок в summary, проброс ReelsiError;
  - предупреждение о слишком длинных словах (>19 символов);
  - обработка ошибок сборки: ReelsiError и SystemExit оборачиваются в RuntimeError с причиной;
  - очистка временного каталога reelsi_* в блоке finally даже при возникновении ошибки.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core import align, cutjob, cutstages, sync, vad, xmlbuild
from core.cutjob import CutOptions, _forced_align, process_pair
from core.umsg import ReelsiError


@pytest.fixture
def emitted() -> Any:
    """Сборщик строк emit: emit(шаблон, **переменные) с сохранением отформатированных строк."""
    lines: list[str] = []

    def _emit(*args: Any, **kwargs: Any) -> None:
        msg = str(args[0]) if args else str(kwargs.pop("line", ""))
        if kwargs:
            try:
                formatted = msg.format(**kwargs)
            except Exception:
                formatted = f"{msg} {kwargs}"
        else:
            formatted = msg
        lines.append(formatted)

    _emit.lines = lines  # type: ignore[attr-defined]
    return _emit


def _write_fake_wav(path: str | Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> None:
    """Создать валидный PCM WAV-файл указанной длительности."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration_sec))


# =========================================================================== #
# 1. CutOptions: умолчания и from_namespace
# =========================================================================== #

def test_cutoptions_дефолтные_значения() -> None:
    """Значения по умолчанию совпадают с DEFAULT_THRESHOLDS и ожидаемыми константами."""
    opts = CutOptions()
    th = cutstages.DEFAULT_THRESHOLDS

    assert opts.no_subs is False
    assert opts.no_dedup is False
    assert opts.no_srt is False
    assert opts.ae is False
    assert opts.keep == "last"
    assert opts.model == str(th["model"])
    assert opts.scale == float(th["scale"])
    assert opts.vad_thresh == float(th["vad_thresh"])
    assert opts.min_silence == float(th["min_silence"])
    assert opts.pad == float(th["pad"])
    assert opts.no_cut is False
    assert opts.aggressive is False
    assert opts.restarts is False
    assert opts.forced_align is False
    assert opts.cam_return == int(th["cam_return"])
    assert opts.big_chunk == 6.0
    assert opts.pause_max == 1.0


def test_from_namespace_переносит_все_поля() -> None:
    """from_namespace переносит все поля, когда они явно заданы в Namespace."""
    custom_values = {
        "no_subs": True,
        "no_dedup": True,
        "no_srt": True,
        "ae": True,
        "keep": "first",
        "model": "small",
        "scale": 42.5,
        "vad_thresh": 22.0,
        "min_silence": 0.45,
        "pad": 0.15,
        "no_cut": True,
        "aggressive": True,
        "restarts": True,
        "forced_align": True,
        "cam_return": 4,
        "big_chunk": 9.5,
        "pause_max": 2.2,
    }
    ns = argparse.Namespace(**custom_values)
    opts = CutOptions.from_namespace(ns)
    assert asdict(opts) == custom_values


def test_from_namespace_пропущенные_поля_берут_дефолты() -> None:
    """Отсутствующие в Namespace атрибуты получают умолчания класса CutOptions."""
    ns = argparse.Namespace(model="medium", aggressive=True)
    opts = CutOptions.from_namespace(ns)
    defaults = CutOptions()

    assert opts.model == "medium"
    assert opts.aggressive is True
    assert opts.no_subs == defaults.no_subs
    assert opts.scale == defaults.scale
    assert opts.cam_return == defaults.cam_return
    assert opts.pause_max == defaults.pause_max


def test_from_namespace_игнорирует_лишние_атрибуты() -> None:
    """Лишние атрибуты Namespace не ломают создание CutOptions и не попадают в него."""
    ns = argparse.Namespace(
        extra_attr="ignored_value",
        cam1="video.mp4",
        preset="interview",
        model="tiny",
    )
    opts = CutOptions.from_namespace(ns)
    assert opts.model == "tiny"
    assert not hasattr(opts, "extra_attr")
    assert not hasattr(opts, "cam1")
    assert not hasattr(opts, "preset")


# =========================================================================== #
# 2. _forced_align: уточнение таймингов через сабпроцесс
# =========================================================================== #

def test_forced_align_успех_возвращает_новые_тайминги_без_stdout(
    monkeypatch: pytest.MonkeyPatch, emitted: Any
) -> None:
    """При успешном запуске falign_cli возвращаются слова из fout."""
    words = [{"w": "привет", "start": 1.0, "end": 2.0}]
    aligned = [{"w": "привет", "start": 1.15, "end": 1.95}]

    captured_fin: list[str] = []
    captured_fout: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        fin_path = cmd[cmd.index("-m") + 3]
        fout_path = cmd[cmd.index("-m") + 4]
        captured_fin.append(fin_path)
        captured_fout.append(fout_path)

        assert os.path.exists(fin_path)
        with open(fin_path, encoding="utf-8") as f:
            assert json.load(f) == words

        with open(fout_path, "w", encoding="utf-8") as f:
            json.dump(aligned, f)

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _forced_align("fake.wav", words, emit=emitted)

    assert result == aligned
    assert captured_fin and not os.path.exists(captured_fin[0])
    assert captured_fout and not os.path.exists(captured_fout[0])


def test_forced_align_выводит_строки_выравнивателя_и_берёт_его_тайминги(
    monkeypatch: pytest.MonkeyPatch, emitted: Any
) -> None:
    """При непустом stdout falign_cli логирует строки и возвращает выровненные слова."""
    words = [{"w": "привет", "start": 1.0, "end": 2.0}]
    aligned = [{"w": "привет", "start": 1.15, "end": 1.95}]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        fout_path = cmd[cmd.index("-m") + 4]
        with open(fout_path, "w", encoding="utf-8") as f:
            json.dump(aligned, f)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="aligned 1 word\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _forced_align("fake.wav", words, emit=emitted)
    assert result == aligned


def test_forced_align_нет_fout_возвращает_исходные_слова_и_логирует_stderr(
    monkeypatch: pytest.MonkeyPatch, emitted: Any
) -> None:
    """Если falign_cli не создал fout (крах или пустой вывод), возвращаются исходные слова."""
    words = [{"w": "тест", "start": 0.5, "end": 1.0}]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="falign internal crash error"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _forced_align("fake.wav", words, emit=emitted)

    assert result == words
    assert any("forced align: нет результата falign internal crash error" in s for s in emitted.lines)


def test_forced_align_исключение_возвращает_исходные_слова(
    monkeypatch: pytest.MonkeyPatch, emitted: Any
) -> None:
    """Исключение subprocess.run перехватывается, логируется и возвращает исходные слова."""
    words = [{"w": "слово", "start": 0.0, "end": 0.8}]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("wav2vec2 out of memory")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _forced_align("fake.wav", words, emit=emitted)

    assert result == words
    assert any("forced align пропущен: wav2vec2 out of memory" in s for s in emitted.lines)


def test_forced_align_reelsi_error_пробрасывается(
    monkeypatch: pytest.MonkeyPatch, emitted: Any
) -> None:
    """ReelsiError не подавляется, а пробрасывается дальше."""
    words = [{"w": "слово", "start": 0.0, "end": 0.8}]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise ReelsiError("Критический сбой окружения")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ReelsiError, match="Критический сбой окружения"):
        _forced_align("fake.wav", words, emit=emitted)


# =========================================================================== #
# 3. process_pair: входные параметры камер и очистка временной папки
# =========================================================================== #

@pytest.fixture
def mock_common(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Базовые заглушки для изоляции внешних подсистем."""
    monkeypatch.setattr(sync, "extract_audio", lambda src, dst, *a, **k: _write_fake_wav(dst, 3.0))
    monkeypatch.setattr(sync, "find_offset", lambda w1, w2, *a, **k: (0.0, 1.0))
    monkeypatch.setattr(vad, "speech_intervals", lambda *a, **k: [(0.0, 3.0)])

    build_calls: list[dict[str, Any]] = []

    def fake_build(
        cams: list[str],
        segments: list[tuple[float, float]],
        offsets: list[float],
        out_xml: str,
        assign: Any = None,
        scale: float = 50.4,
        sub_words: Any = None,
        music_path: str | None = None,
    ) -> dict[str, Any]:
        info = {
            "total_s": 3.0,
            "segments": len(segments),
            "subtitles": len(sub_words) if sub_words else 0,
            "long_words": [],
        }
        build_calls.append({
            "cams": cams,
            "segments": segments,
            "offsets": offsets,
            "out_xml": out_xml,
            "assign": assign,
            "scale": scale,
            "sub_words": sub_words,
            "music_path": music_path,
        })
        return info

    monkeypatch.setattr(xmlbuild, "build", fake_build)
    return {"build_calls": build_calls}


def test_process_pair_камера_строкой_и_фильтрация_пустых_слотов(
    tmp_path: Path, mock_common: dict[str, Any], emitted: Any
) -> None:
    """cams может быть строкой или списком с пустыми элементами."""
    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    # 1. cams строкой
    process_pair("cam1.mp4", out_xml, opts, emit=emitted)
    assert mock_common["build_calls"][-1]["cams"] == ["cam1.mp4"]
    assert any("Камеры: cam1.mp4" in s for s in emitted.lines)

    # 2. cams списком с пустыми слотами
    process_pair(["cam1.mp4", "", None, "cam2.mp4"], out_xml, opts, emit=emitted)  # type: ignore[list-item]
    assert mock_common["build_calls"][-1]["cams"] == ["cam1.mp4", "cam2.mp4"]
    assert any("Камеры: cam1.mp4, cam2.mp4" in s for s in emitted.lines)


def test_process_pair_временная_папка_удаляется_всегда(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Временный каталог reelsi_* удаляется в блоке finally даже при ошибке сборки."""
    created_work_dirs: list[str] = []
    real_mkdtemp = cutjob.tempfile.mkdtemp

    def spy_mkdtemp(**kwargs: Any) -> str:
        d = real_mkdtemp(**kwargs)
        created_work_dirs.append(d)
        return d

    monkeypatch.setattr(cutjob.tempfile, "mkdtemp", spy_mkdtemp)

    # Успешный запуск
    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)
    process_pair("cam1.mp4", out_xml, opts, emit=emitted)
    assert created_work_dirs
    assert not os.path.exists(created_work_dirs[0])

    # Запуск с ошибкой сборки
    monkeypatch.setattr(
        xmlbuild, "build", MagicMock(side_effect=ReelsiError("Ошибка генерации XML"))
    )
    with pytest.raises(RuntimeError, match="Ошибка генерации XML"):
        process_pair("cam1.mp4", out_xml, opts, emit=emitted)
    assert len(created_work_dirs) == 2
    assert not os.path.exists(created_work_dirs[1])


# =========================================================================== #
# 4. process_pair: режим no_cut (только субтитры)
# =========================================================================== #

def test_process_pair_режим_no_cut_ограничивает_одну_камеру_и_читает_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """В режиме no_cut берётся только одна камера, длительность через wave, без VAD и синка."""
    vad_called = False
    offset_called = False

    def boom_vad(*a: Any, **k: Any) -> list[tuple[float, float]]:
        nonlocal vad_called
        vad_called = True
        return []

    def boom_offset(*a: Any, **k: Any) -> tuple[float, float]:
        nonlocal offset_called
        offset_called = True
        return (0.0, 1.0)

    monkeypatch.setattr(vad, "speech_intervals", boom_vad)
    monkeypatch.setattr(sync, "find_offset", boom_offset)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_cut=True, no_subs=True, no_dedup=True)

    info = process_pair(["cam1.mp4", "cam2.mp4"], out_xml, opts, emit=emitted)

    assert not vad_called, "VAD не должен вызываться в режиме no_cut"
    assert not offset_called, "find_offset не должен вызываться в режиме no_cut"
    assert mock_common["build_calls"][-1]["cams"] == ["cam1.mp4"]
    assert mock_common["build_calls"][-1]["assign"] is None
    # Длительность wav-заглушки 3.0 секунды
    assert mock_common["build_calls"][-1]["segments"] == [(0.0, 3.0)]
    assert mock_common["build_calls"][-1]["offsets"] == [0.0]
    assert any("(только субтитры)" in s for s in emitted.lines)
    assert any("без нарезки: весь ролик 3s" in s for s in emitted.lines)
    assert info["total_s"] == 3.0


# =========================================================================== #
# 5. process_pair: мультикам и одиночная камера
# =========================================================================== #

def test_process_pair_мультикам_синк_и_раскладка_камер(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Для N > 1 камер вычисляются оффсеты и вызывается assign_cameras."""
    offsets_returned = [(0.25, 0.95), (-0.15, 0.88)]
    offset_idx = 0

    def fake_offset(w1: str, w2: str) -> tuple[float, float]:
        nonlocal offset_idx
        res = offsets_returned[offset_idx]
        offset_idx += 1
        return res

    monkeypatch.setattr(sync, "find_offset", fake_offset)
    monkeypatch.setattr(vad, "speech_intervals", lambda *a, **k: [(0.0, 2.0), (2.5, 5.0)])

    assign_args: dict[str, Any] = {}

    def fake_assign(
        segments: list[tuple[float, float]], n: int, return_every: int, big_chunk_sec: float
    ) -> list[tuple[float, float, int]]:
        assign_args["segments"] = segments
        assign_args["n"] = n
        assign_args["return_every"] = return_every
        assign_args["big_chunk_sec"] = big_chunk_sec
        return [(0.0, 2.0, 1), (2.5, 5.0, 2)]

    monkeypatch.setattr(align, "assign_cameras", fake_assign)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(
        cam_return=3,
        big_chunk=7.5,
        vad_thresh=17.5,
        min_silence=0.35,
        pad=0.1,
        no_subs=True,
        no_dedup=True,
    )

    process_pair(["cam1.mp4", "cam2.mp4", "cam3.mp4"], out_xml, opts, emit=emitted)

    assert mock_common["build_calls"][-1]["offsets"] == [0.0, 0.25, -0.15]
    assert assign_args["n"] == 3
    assert assign_args["return_every"] == 3
    assert assign_args["big_chunk_sec"] == 7.5
    assert mock_common["build_calls"][-1]["assign"] == [(0.0, 2.0, 1), (2.5, 5.0, 2)]
    assert any("синк К2: +0.250s (увер. 0.95)" in s for s in emitted.lines)
    assert any("синк К3: -0.150s (увер. 0.88)" in s for s in emitted.lines)


def test_process_pair_одиночная_камера_не_назначает_камеры(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Для N = 1 камеры assign_cameras не вызывается, assign равен None."""
    assign_called = False

    def boom_assign(*a: Any, **k: Any) -> list[Any]:
        nonlocal assign_called
        assign_called = True
        return []

    monkeypatch.setattr(align, "assign_cameras", boom_assign)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert not assign_called
    assert mock_common["build_calls"][-1]["assign"] is None


# =========================================================================== #
# 6. process_pair: транскрипция, кэш и forced_align
# =========================================================================== #

def test_process_pair_no_subs_и_no_dedup_пропускает_транскрипцию(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Если отключены и субтитры, и дедупликация, транскрипция не вызывается."""
    from core import transcribe

    monkeypatch.setattr(
        transcribe, "transcribe", MagicMock(side_effect=RuntimeError("transcribe called"))
    )
    monkeypatch.setattr(
        transcribe, "load_words_cache", MagicMock(side_effect=RuntimeError("load_cache called"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)
    assert mock_common["build_calls"][-1]["sub_words"] is None


def test_process_pair_попадание_в_кэш_слов(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При наличии кэша исходника transcribe не вызывается, слова берутся из кэша."""
    from core import transcribe

    cached_words = [{"w": "из_кэша", "start": 0.5, "end": 1.0}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "words.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: cached_words)
    monkeypatch.setattr(
        transcribe, "transcribe", MagicMock(side_effect=RuntimeError("transcribe called"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=False, no_dedup=False, model="medium")

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert any("транскрипт из кэша: 1 слов" in s for s in emitted.lines)


def test_process_pair_старый_кэш_для_дефолтной_модели(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Если модель по умолчанию (large-v3) и основного кэша нет, ищется старый кэш без модели."""
    from core import transcribe

    cached_words = [{"w": "из_старого_кэша", "start": 0.2, "end": 0.8}]
    legacy_file = tmp_path / "out.words.json"
    legacy_file.write_text("{}", encoding="utf-8")

    def fake_load(p: str) -> list[dict[str, Any]] | None:
        if p == str(legacy_file):
            return cached_words
        return None

    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "new.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", fake_load)
    monkeypatch.setattr(
        transcribe, "transcribe", MagicMock(side_effect=RuntimeError("transcribe called"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(model=transcribe.DEFAULT_MODEL_SIZE, no_subs=False, no_dedup=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert any("транскрипт из старого кэша (имя без модели)" in s for s in emitted.lines)


def test_process_pair_промах_кэша_вызывает_whisper_и_сохраняет(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При промахе кэша вызывается Whisper transcribe и результат сохраняется в кэш."""
    from core import transcribe

    new_words = [{"w": "новое", "start": 0.1, "end": 0.9}]
    saved_cache: list[tuple[str, list[dict[str, Any]]]] = []

    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: None)
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, model_size, model: new_words)
    monkeypatch.setattr(
        transcribe, "save_words_cache", lambda path, wds: saved_cache.append((path, wds))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(model="small", no_subs=False, no_dedup=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert any("транскрибирую (Whisper small)..." in s for s in emitted.lines)
    assert any("1 слов" in s for s in emitted.lines)
    assert len(saved_cache) == 1
    assert saved_cache[0][1] == new_words


def test_process_pair_вызывает_forced_align_при_флаге(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При opts.forced_align=True вызывается _forced_align."""
    from core import transcribe

    words = [{"w": "слово", "start": 0.0, "end": 1.0}]
    fa_called: list[bool] = []

    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    def fake_fa(wav: str, wds: list[dict[str, Any]], emit: Any) -> list[dict[str, Any]]:
        fa_called.append(True)
        return [{"w": "слово", "start": 0.05, "end": 0.95}]

    monkeypatch.setattr(cutjob, "_forced_align", fake_fa)

    out_xml = str(tmp_path / "out.xml")

    # 1. forced_align = True -> вызывается
    opts_fa = CutOptions(forced_align=True, no_subs=False, no_dedup=False)
    process_pair(["cam1.mp4"], out_xml, opts_fa, emit=emitted)
    assert fa_called == [True]

    # 2. forced_align = False -> не вызывается
    fa_called.clear()
    opts_no_fa = CutOptions(forced_align=False, no_subs=False, no_dedup=False)
    process_pair(["cam1.mp4"], out_xml, opts_no_fa, emit=emitted)
    assert fa_called == []


# =========================================================================== #
# 7. process_pair: дедупликация и агрессивный режим
# =========================================================================== #

def test_process_pair_дедупликация_find_repeat_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При restarts=False вызывается find_repeat_ranges и удалённые повторы логируются."""
    from core import transcribe

    words = [
        {"w": "привет", "start": 0.1, "end": 0.4},
        {"w": "привет", "start": 0.6, "end": 0.9},
    ]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    called_find_repeat = False

    def fake_repeat(wds: list[dict[str, Any]], keep: str) -> tuple[list[tuple[float, float]], list[tuple[float, float, str]]]:
        nonlocal called_find_repeat
        called_find_repeat = True
        assert keep == "first"
        return ([(0.6, 0.9)], [(0.6, 0.9, "привет")])

    monkeypatch.setattr(align, "find_repeat_ranges", fake_repeat)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_dedup=False, restarts=False, keep="first", no_subs=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert called_find_repeat
    assert any("повторов/рестартов удалено: 1" in s for s in emitted.lines)
    assert any("− «привет»" in s for s in emitted.lines)


def test_process_pair_дедупликация_find_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При restarts=True вызывается find_restarts."""
    from core import transcribe

    words = [{"w": "фраза", "start": 0.1, "end": 0.9}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    called_find_restarts = False

    def fake_restarts(wds: list[dict[str, Any]], keep: str) -> tuple[list[tuple[float, float]], list[tuple[float, float, str]]]:
        nonlocal called_find_restarts
        called_find_restarts = True
        assert keep == "last"
        return ([(0.1, 0.4)], [(0.1, 0.4, "перезаход")])

    monkeypatch.setattr(align, "find_restarts", fake_restarts)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_dedup=False, restarts=True, keep="last", no_subs=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert called_find_restarts
    assert any("повторов/рестартов удалено: 1" in s for s in emitted.lines)
    assert any("− «перезаход»" in s for s in emitted.lines)


def test_process_pair_агрессивный_режим_филлеры_паузы_и_шум(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """В агрессивном режиме удаляются филлеры, мёртвый воздух и пустые сегменты шума."""
    from core import transcribe

    words = [
        {"w": "хм", "start": 0.2, "end": 0.5},
        {"w": "речь", "start": 1.0, "end": 1.4},
    ]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    # Исходные сегменты от VAD: один нормальный с речью, второй шумовой (вздох/вода) без слов
    monkeypatch.setattr(vad, "speech_intervals", lambda *a, **k: [(0.0, 1.8), (2.2, 2.8)])

    monkeypatch.setattr(align, "is_filler", lambda w: w == "хм")
    monkeypatch.setattr(align, "dead_air_ranges", lambda wds, max_pause: [(0.5, 0.9)])

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(aggressive=True, pause_max=0.6, no_dedup=True, no_subs=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert any("филлеров (хм/кашель) вырезано: 1" in s for s in emitted.lines)
    assert any("пауз/мёртвого воздуха вырезано: 1" in s for s in emitted.lines)
    assert any("сегментов без речи (вздохи/вода/шум) убрано: 2" in s for s in emitted.lines)


# =========================================================================== #
# 8. process_pair: фильтрация коротких сегментов, SRT и After Effects
# =========================================================================== #

def test_process_pair_фильтрация_сегментов_нулевой_длительности_кадров(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Сегменты, у которых round(e*60) - round(s*60) <= 0, отбрасываются."""
    monkeypatch.setattr(vad, "speech_intervals", lambda *a, **k: [(1.0, 1.005), (2.0, 4.0)])

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    passed_segments = mock_common["build_calls"][-1]["segments"]
    assert passed_segments == [(2.0, 4.0)]


def test_process_pair_генерация_srt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Флаг no_srt управляет созданием .srt файла через align.make_srt."""
    from core import transcribe

    words = [{"w": "титры", "start": 0.1, "end": 0.8}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    srt_calls: list[str] = []
    monkeypatch.setattr(align, "make_srt", lambda mapped, path: srt_calls.append(path) or 3)

    out_xml = str(tmp_path / "out.xml")

    # 1. no_srt = False -> вызывается make_srt
    opts_srt = CutOptions(no_srt=False, no_subs=False, no_dedup=False)
    process_pair(["cam1.mp4"], out_xml, opts_srt, emit=emitted)
    expected_srt_path = str(tmp_path / "out.srt")
    assert srt_calls == [expected_srt_path]
    assert any("+ .srt (3)" in s for s in emitted.lines)

    # 2. no_srt = True -> не вызывается
    srt_calls.clear()
    opts_no_srt = CutOptions(no_srt=True, no_subs=False, no_dedup=False)
    process_pair(["cam1.mp4"], out_xml, opts_no_srt, emit=emitted)
    assert srt_calls == []


def test_process_pair_генерация_ae_jsx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При opts.ae=True и наличии sub_words вызывается xml2ae.to_ae_full."""
    from core import transcribe, xml2ae

    words = [{"w": "графика", "start": 0.2, "end": 0.7}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)

    ae_calls: list[tuple[str, str]] = []

    def fake_to_ae(xml_p: str, jsx_p: str) -> tuple[None, int, int]:
        ae_calls.append((xml_p, jsx_p))
        return (None, 5, 15)

    monkeypatch.setattr(xml2ae, "to_ae_full", fake_to_ae)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(ae=True, no_subs=False, no_dedup=False)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    expected_jsx = str(tmp_path / "out.jsx")
    assert ae_calls == [(out_xml, expected_jsx)]
    assert any("+ .jsx (5кл/15суб)" in s for s in emitted.lines)


def test_process_pair_ae_jsx_нефатальная_ошибка_логируется_без_краха(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Нефатальная ошибка (Exception) при генерации JSX не прерывает нарезку."""
    from core import transcribe, xml2ae

    words = [{"w": "слово", "start": 0.1, "end": 0.5}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)
    monkeypatch.setattr(
        xml2ae, "to_ae_full", MagicMock(side_effect=RuntimeError("JSX syntax error"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(ae=True, no_subs=False, no_dedup=False)

    info = process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)
    assert info["total_s"] == 3.0
    assert any("[jsx err: JSX syntax error]" in s for s in emitted.lines)


def test_process_pair_ae_jsx_reelsi_error_пробрасывается(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """ReelsiError при сборке JSX пробрасывается наружу."""
    from core import transcribe, xml2ae

    words = [{"w": "слово", "start": 0.1, "end": 0.5}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda c, m: str(tmp_path / "cache.json"))
    monkeypatch.setattr(transcribe, "load_words_cache", lambda p: words)
    monkeypatch.setattr(
        xml2ae, "to_ae_full", MagicMock(side_effect=ReelsiError("Сбой шаблона AE"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(ae=True, no_subs=False, no_dedup=False)

    with pytest.raises(ReelsiError, match="Сбой шаблона AE"):
        process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)


# =========================================================================== #
# 9. process_pair: предупреждения и ошибки xmlbuild
# =========================================================================== #

def test_process_pair_длинные_слова_предупреждение(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """При наличии long_words в ответе build выдаётся предупреждение в лог."""
    def fake_build(*a: Any, **k: Any) -> dict[str, Any]:
        return {
            "total_s": 5.0,
            "segments": 1,
            "subtitles": 1,
            "long_words": ["электрокардиографический"],
        }

    monkeypatch.setattr(xmlbuild, "build", fake_build)

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)
    assert any("⚠ слишком длинные слова (>19 букв) — без титра, добавь вручную: электрокардиографический" in s for s in emitted.lines)


def test_process_pair_ошибка_xmlbuild_systemexit_оборачивается_в_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_common: dict[str, Any], emitted: Any
) -> None:
    """SystemExit из xmlbuild.build перехватывается, логируется и превращается в RuntimeError."""
    monkeypatch.setattr(
        xmlbuild, "build", MagicMock(side_effect=SystemExit("Собирать нечего"))
    )

    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    with pytest.raises(RuntimeError, match="Собирать нечего"):
        process_pair(["cam1.mp4"], out_xml, opts, emit=emitted)

    assert any("⚠ XML не создан — Собирать нечего" in s for s in emitted.lines)


def test_process_pair_музыка_передаётся_в_xmlbuild(
    tmp_path: Path, mock_common: dict[str, Any], emitted: Any
) -> None:
    """Параметр music_path корректно передаётся в xmlbuild.build."""
    out_xml = str(tmp_path / "out.xml")
    opts = CutOptions(no_subs=True, no_dedup=True)

    process_pair(["cam1.mp4"], out_xml, opts, emit=emitted, music_path="background.mp3")

    assert mock_common["build_calls"][-1]["music_path"] == "background.mp3"
