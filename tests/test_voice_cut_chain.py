# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Нарезка слушает голос ПОСЛЕ ВСЕЙ цепочки спикера: шумодав + VST-плагины.

Решение владельца (02.10.2026): «до нарезки я просил накладывать всё, что стоит у
спикера, для более правильного определения слов». Раньше `apply_cut_fx` подменял
файл нарезки ТОЛЬКО дорожкой шумодава, а при выключенном шумодаве и включённых
плагинах не делал ничего — слова распознавались по другому звуку, чем уезжал в
ролик.

Здесь проверяются границы нового правила, которые нельзя увидеть глазами:

* плагины вкл, шумодав вкл → плагины испечены ПОВЕРХ дорожки шумодава, в файл
  нарезки ушёл результат цепочки;
* плагины вкл, шумодав выкл → цепочка печётся по СЫРОМУ звуку камеры;
* плагин упал → предупреждение в лог с ИМЕНЕМ плагина, файл = дорожка шумодава;
* повторная нарезка без правок → печь не зовут вовсе (кеш);
* выключенный плагин (`on:false`) в цепочку не попадает.

Настоящие VST-плагины, ffmpeg, GPU и звуковые устройства НЕ запускаются: печь
плагинов подменяется заглушкой, дочерний процесс не поднимается.

Запуск:  py -3.10 -m pytest tests/test_voice_cut_chain.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import speakers, voicefx  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402


def _noop(*a: Any, **k: Any) -> None:
    """emit-заглушка: строки лога в тестах не нужны."""
    return None


def _lines_to(lines: list[str]) -> Any:
    """emit-заглушка в список: шаблон и переменные склеиваются, как это делает t().

    Лог модуля — структурный (`emit("…{n}", n=...)`), поэтому просто складывать
    первый аргумент нельзя: в списке оказался бы шаблон без имени плагина.
    """
    def _one(line: str = "", /, **vars: Any) -> None:
        lines.append(str(vars.get("line") or line).format(**vars) if vars else str(line))
    return _one


@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Кеш обработки и профили спикеров — в tmp_path, не в боевых папках."""
    d = tmp_path / "_voicefx"
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(d))
    voices = tmp_path / "speakers"
    voices.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(voices))
    return d


@pytest.fixture()
def cam1(tmp_path: Path) -> str:
    """Файл камеры 1: содержимое не читается (ffmpeg подменён) — важно наличие."""
    p = tmp_path / "cam1.mp4"
    p.write_bytes(b"fake camera file")
    return str(p)


def _speaker(**fx: Any) -> dict[str, Any]:
    """Профиль спикера с обработкой голоса; возвращает его же словарём."""
    key = "Голос"
    speakers.save(key, {"label": key, "voice_fx": fx})
    return speakers.load(key) or {}


def _cut_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
               seen: dict[str, list[Any]], *, vst=None) -> dict[str, Any]:
    """Заглушки печи: дорожка шумодава, цепочка плагинов и ffmpeg нарезки.

    Ни один дочерний процесс не поднимается: `_apply_vst` (та же дверь, которой
    вывод печёт плагины) подменяется заглушкой, `denoise_track` не считает
    шумодав вовсе, `analysis_wav` не запускает ffmpeg.
    """
    def fake_denoise_track(src: str, dn: dict[str, Any], start: float = 0.0,
                           dur: float | None = None, emit: Any = _noop,
                           cancelled: Any = None, progress: Any = None,
                           pid_of: Any = None) -> str:
        seen.setdefault("denoise", []).append((src, dn))
        path = str(tmp_path / "track.wav")
        with open(path, "wb") as f:            # настоящая дорожка — файл, а не путь
            f.write(b"RIFF denoise track")
        return path

    def fake_vst(src: str, plugins: list[dict[str, Any]], out: str, emit: Any,
                 cancelled: Any = None) -> list[dict[str, str]]:
        seen.setdefault("vst", []).append((src, [p["path"] for p in plugins]))
        if vst is not None:
            return vst(src, plugins, out, emit, cancelled)
        shutil.copyfile(src, out)          # заглушка «плагины сработали»: файл на месте
        return []

    def fake_analysis(full: str, dst: str, gain_db: float = 0.0,
                      emit: Any = _noop) -> str:
        seen.setdefault("analysis", []).append(full)
        with open(dst, "wb") as f:
            f.write(b"RIFF cut voice")
        return dst

    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise_track)
    monkeypatch.setattr(voicefx, "_apply_vst", fake_vst)
    monkeypatch.setattr(voicefx, "analysis_wav", fake_analysis)
    return seen


def _cut_file(tmp_path: Path) -> Path:
    """Файл нарезки с сырым звуком: подмена обязана его перезаписать."""
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw camera audio")
    return wav0


# --------------------------------------------------------------------------- #
# 1. Цепочка целиком: шумодав → плагины → файл нарезки
# --------------------------------------------------------------------------- #
def test_cut_with_plugins_and_denoise_bakes_the_chain(cam1, tmp_path, monkeypatch):
    """Плагины вкл, шумодав вкл: печь плагинов зовут по ДОРОЖКЕ ШУМОДАВА, файл подменён.

    Ключевая проверка задания: нарезка слышит не дорожку шумодава, а её результат
    ПОСЛЕ цепочки. Цепочку собирает та же дверь, что и вывод (`render_cached`),
    поэтому аргумент печи — готовый трек кеша, а не сырой звук камеры.
    """
    prof = _speaker(denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
                    vst=[{"path": "C:/reverb.vst3", "on": True}])
    wav0 = _cut_file(tmp_path)
    seen: dict[str, list[Any]] = {}
    _cut_fakes(monkeypatch, tmp_path, seen)

    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop) is True

    assert seen["denoise"] == [(cam1, {"on": True, "engine": "deepfilter",
                                       "atten_db": 40, "mix": 100})], seen["denoise"]
    assert seen["vst"] == [(str(tmp_path / "track.wav"), ["C:/reverb.vst3"])], \
        "плагины испечены не поверх дорожки шумодава"
    # Печётся под ключом кеша ВСЕЙ цепочки: повтор без правок возьмёт готовое
    chain = voicefx.cache_path(cam1, prof["voice_fx"])
    assert chain.endswith(".wav") and os.path.isfile(chain), "цепочка не запечена в кеш"
    assert seen["analysis"] == [chain], "в файл нарезки ушёл не результат цепочки"
    assert wav0.read_bytes() == b"RIFF cut voice", "файл нарезки не подменён"


def test_cut_with_plugins_only_bakes_raw_camera_audio(cam1, tmp_path, monkeypatch):
    """Плагины вкл, шумодав выкл: печь плагинов по СЫРОМУ звуку, файл подменён.

    «Шумодав выключен» — это «звук камеры как есть», а не «обработки нет вовсе»:
    раньше эта комбинация вообще ничего не делала, и нарезка шла по звуку камеры,
    пока в ролике играли плагины.
    """
    prof = _speaker(denoise={"on": False},
                    vst=[{"path": "C:/reverb.vst3", "on": True}])
    wav0 = _cut_file(tmp_path)
    seen: dict[str, list[Any]] = {}
    _cut_fakes(monkeypatch, tmp_path, seen)

    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop) is True

    assert seen["denoise"] == [(cam1, {"on": False, "engine": "roformer",
                                       "atten_db": 40, "mix": 100})], seen["denoise"]
    # Дорожка без шумодава — извлечённый звук камеры: по нему и печём плагины
    assert seen["vst"] == [(str(tmp_path / "track.wav"), ["C:/reverb.vst3"])], \
        "плагины испечены не по сырому звуку"
    assert seen["analysis"] == [voicefx.cache_path(cam1, prof["voice_fx"])]
    assert wav0.read_bytes() == b"RIFF cut voice", "файл нарезки не подменён"


def test_cut_skips_disabled_plugin(cam1, tmp_path, monkeypatch):
    """Выключенный плагин (`on:false`) в цепочку не попадает, включённый — попадает."""
    prof = _speaker(denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
                    vst=[{"path": "C:/off.vst3", "on": False},
                         {"path": "C:/on.vst3", "on": True}])
    wav0 = _cut_file(tmp_path)
    seen: dict[str, list[Any]] = {}
    _cut_fakes(monkeypatch, tmp_path, seen)

    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop) is True
    assert seen["vst"] == [(str(tmp_path / "track.wav"), ["C:/on.vst3"])], \
        "выключенный плагин уехал в цепочку нарезки"


# --------------------------------------------------------------------------- #
# 2. Кеш: повтор без правок ничего не печёт
# --------------------------------------------------------------------------- #
def test_cut_second_run_bakes_nothing(cam1, tmp_path, monkeypatch):
    """Повторная нарезка того же клипа без правок не печёт цепочку заново.

    Ключ кеша — исходник + настройки шумодава + цепочка плагинов с их состоянием
    (`_digest`), поэтому вторая нарезка берёт готовый файл: печь плагинов и
    шумодав не зовут вовсе.
    """
    prof = _speaker(denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
                    vst=[{"path": "C:/reverb.vst3", "on": True, "state": "AAEE"}])
    seen: dict[str, list[Any]] = {}
    _cut_fakes(monkeypatch, tmp_path, seen)

    wav0 = _cut_file(tmp_path)
    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop) is True
    assert len(seen["vst"]) == 1 and os.path.isfile(voicefx.cache_path(cam1, prof["voice_fx"]))

    wav0.write_bytes(b"raw camera audio")          # второй клип начинается с сырого звука
    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_noop) is True

    assert len(seen["vst"]) == 1, "второй прогон снова испёк плагины (кеш не сработал)"
    assert len(seen["analysis"]) == 2, "второй прогон не подменил файл нарезки"
    assert wav0.read_bytes() == b"RIFF cut voice"


# --------------------------------------------------------------------------- #
# 3. Сбой плагина: предупреждение и нарезка по дорожке шумодава
# --------------------------------------------------------------------------- #
def test_cut_plugin_failure_falls_back_to_denoise_track(cam1, tmp_path, monkeypatch):
    """Плагин упал: предупреждение с ИМЕНЕМ в логе, файл = дорожка шумодава.

    Нарезка важнее обработки: чужой плагин умеет падать нативно, и из-за него
    клип не должен остаться без обработки вовсе. Пропущенный плагин называем по
    имени — иначе человек не поймёт, что именно выпало из цепочки.
    """
    prof = _speaker(denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
                    vst=[{"path": "C:/broken.vst3", "name": "Broken", "on": True}])
    wav0 = _cut_file(tmp_path)
    seen: dict[str, list[Any]] = {}

    def failing_vst(src, plugins, out, emit, cancelled=None):
        emit("Голос: плагин не загрузился — пропущен ({n})", n="Broken")
        raise ReelsiError(umsg("voicefx_render_failed",
                               "VST-цепочка: Broken не загрузился"))

    _cut_fakes(monkeypatch, tmp_path, seen, vst=failing_vst)
    lines: list[str] = []

    assert voicefx.apply_cut_fx(str(wav0), cam1, prof, emit=_lines_to(lines)) is True

    assert any("Broken" in ln for ln in lines), "в логе нет имени пропущенного плагина"
    assert any("не удалась" in ln for ln in lines), "сбой цепочки не виден в логе"
    # Печь пробовали поверх дорожки шумодава, откатились на неё же — без плагинов
    assert seen["denoise"] == [(cam1, voicefx.normalize_fx(prof["voice_fx"])["denoise"])] * 2, \
        "дорожка шумодава взята не по тому же звуку камеры"
    assert seen["analysis"] == [str(tmp_path / "track.wav")], \
        "нарезка ушла не по дорожке шумодава"
    assert wav0.read_bytes() == b"RIFF cut voice", "файл нарезки не подменён дорожкой"
