# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Один выключатель обработки голоса: правило `voice_fx_on` у ВСЕХ потребителей.

Решение владельца: включил ИИ-шумодав — он работает ВЕЗДЕ и ВЕСЬ (нарезка, итоговый
трек AE/DRP/Premiere XML, черновой рендер и все превью). Отдельных галок «для
нарезки» и «в итоговый трек» больше нет, а старые профили с `cut`/`final=false`
обработку не выключают.

Здесь проверяется ровно то, что нельзя увидеть глазами: что КАЖДАЯ дверь спрашивает
одно и то же правило, а не решает по-своему. Дверь, оставшаяся со своим условием,
ломается молча — обработка просто не доезжает до одного из мест.

Запуск:  py -3.10 -m pytest tests/test_voice_switch.py -q
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers, voicefx  # noqa: E402
from core import project_file  # noqa: E402

SPEAKER = "Голос"
# Профиль СТАРОГО вида: галки сняты, шумодав включён. Именно такие лежат у владельца,
# и именно их обязан читать каждый потребитель — иначе обработка молча выключится.
OLD_FX: dict[str, Any] = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40},
                          "vst": [], "cut": False, "final": False}
OFF_FX: dict[str, Any] = {"denoise": {"on": False}, "vst": []}


def _noop(*a: Any, **k: Any) -> None:
    """emit-заглушка: строки лога в тестах не нужны."""
    return None


def _write_wav(path: str, seconds: float, rate: int = 48000, channels: int = 1) -> None:
    """Настоящий WAV: длину результата читает сам render."""
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(seconds * rate) * channels * 2))


@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Кеш обработки и профили спикеров — в tmp_path, не в боевых папках."""
    d = tmp_path / "_voicefx"
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(d))
    voices = tmp_path / "speakers"
    voices.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(voices))
    return d


@pytest.fixture
def clip(tmp_path: Path) -> tuple[str, str]:
    """Клип: XML, сайдкар нарезки со спикером и файл камеры 1."""
    cam1 = tmp_path / "cam1.mp4"
    cam1.write_bytes(b"camera")
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    return str(xml), str(cam1)


def _speaker(fx: dict[str, Any]) -> None:
    speakers.save(SPEAKER, {"label": SPEAKER, "voice_fx": fx})


def _sidecar(xml: str, cam1: str) -> None:
    project_file.write_project(os.path.splitext(xml)[0] + ".project.json",
                               {"cams": [cam1], "offsets": [0.0], "fps": 60,
                                "keep": [[0.0, 1.0]], "speaker": SPEAKER})


def _baked_voice(xml: str, cam1: str, fx: dict[str, Any]) -> str:
    """Голос «уже запечён»: файл рядом с XML плюс ключ кеша в `.voice.json`."""
    dst = voicefx.final_voice_path(xml)
    Path(dst).write_bytes(b"RIFF voice")
    with io.open(os.path.splitext(xml)[0] + ".voice.json", "w", encoding="utf-8") as f:
        json.dump({"key": voicefx.final_voice_key(cam1, fx), "src": cam1}, f)
    return dst


# --------------------------------------------------------------------------- #
# 1. Двери: каждая спрашивает ОДНО правило, а не решает по-своему
# --------------------------------------------------------------------------- #
def test_reader_doors_follow_the_rule(clip: tuple[str, str]) -> None:
    """Читатели (XML, DRP, черновик) идут за правилом, а не за «файл лежит».

    Правило одно — `core.voicefx.clip_voice_wav`: спикер клипа из сайдкара плюс
    включённая обработка. Выключили шумодав — старый `<стем>.voice.wav` рядом с XML
    больше НЕ читается: иначе XML, `.drp` и черновик продолжали бы звучать
    обработанным голосом при снятом выключателе.
    """
    xml, cam1 = clip
    _speaker(OLD_FX)
    _sidecar(xml, cam1)
    voice = _baked_voice(xml, cam1, OLD_FX)

    # Включено (и это старый профиль со снятыми галками!) — читатели берут голос
    assert voicefx.clip_voice_wav(xml) == voice, "старый профиль выключил обработку"
    assert voicefx.clip_final_fx(xml) is not None

    # Выключили обработку — тот же файл рядом с XML больше не в деле
    _speaker(OFF_FX)
    assert voicefx.clip_voice_wav(xml) == "", "выключенная обработка осталась в XML/DRP/черновике"

    # Нет сайдкара — тоже пусто: правило читается из сайдкара нарезки
    os.remove(os.path.splitext(xml)[0] + ".project.json")
    _speaker(OLD_FX)
    assert voicefx.clip_voice_wav(xml) == "", "голос нашёлся без сайдкара нарезки"


def test_processing_doors_follow_the_rule(clip: tuple[str, str],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Нарезка и сборка AE спрашивают то же правило.

    Правило подменяется целиком (`voice_fx_on`): если бы у двери было СВОЁ условие
    (флаги `cut`/`final`, «файл есть»), она бы его не заметила и пошла работать.

    Превью-прокси в этом списке больше нет: он голосом не занимается вовсе — прокси
    всегда со звуком камеры, а обработанный голос играет отдельной дорожкой плеера и
    печётся дверью `/api/voicefx_bake` (её проверяют тесты запекания выше).
    """
    xml, cam1 = clip
    _speaker(OLD_FX)
    _sidecar(xml, cam1)
    _baked_voice(xml, cam1, OLD_FX)
    seen: list[Any] = []
    real = voicefx.voice_fx_on

    def spy(fx: Any) -> bool:
        seen.append(fx)
        return real(fx)

    monkeypatch.setattr(voicefx, "voice_fx_on", spy)
    assert voicefx.clip_final_fx(xml) is not None
    assert seen, "двери не спросили правило вовсе"

    # Выключено — ни одна дверь не берётся за работу
    monkeypatch.setattr(voicefx, "voice_fx_on", lambda fx: False)
    monkeypatch.setattr(voicefx, "render_cached",
                        lambda *a, **k: pytest.fail("рендер по выключенной обработке"))
    monkeypatch.setattr(voicefx, "analysis_wav",
                        lambda *a, **k: pytest.fail("подмена файла нарезки без обработки"))
    assert voicefx.apply_cut_fx(str(Path(xml).with_suffix(".wav")), cam1,
                                speakers.load(SPEAKER), emit=_noop) is False
    assert voicefx.clip_final_fx(xml) is None
    assert voicefx.clip_voice_wav(xml) == ""
    assert voicefx.final_voice_for_build(xml, cam1, emit=_noop) is None


def test_no_door_decides_by_cut_or_final_flags() -> None:
    """В коде дверей не осталось чтения флагов `cut`/`final` и своей копии условия.

    Это сторож от возврата: условие «обработка включена» живёт в одной функции
    (`voice_fx_on`), а галки — только её зеркало в нормализации профиля.
    """
    doors = ("core/voicefx.py", "core/omni_cut.py", "core/xmlbuild.py",
             "core/draftrender.py", "api/build.py", "api/previewproxy.py",
             "core/xml2ae/build.py", "api/voicefx.py")
    for rel in doors:
        src = io.open(ROOT / rel, encoding="utf-8").read()
        assert '["cut"]' not in src, f"{rel}: дверь решает по флагу cut"
        assert '["final"]' not in src, f"{rel}: дверь решает по флагу final"
        assert "denoise\"][\"on\"] and" not in src, f"{rel}: своя копия условия «включено»"
    # Нарезка и omni_cut зовут правило по имени, а не пересчитывают его
    # (нарезка зовёт `apply_cut_fx`, а правило `voice_fx_on` — внутри неё; итоговый
    # голос с плагинами нарезка не печёт — только вывод)
    assert "apply_cut_fx(" in io.open(ROOT / "core/omni_cut.py", encoding="utf-8").read()
    assert "voice_fx_on(" in io.open(ROOT / "core/voicefx.py", encoding="utf-8").read()
    # Читатели XML/DRP/черновика ходят ОДНОЙ дверью
    for rel in ("core/xmlbuild.py", "core/draftrender.py", "api/build.py"):
        src = io.open(ROOT / rel, encoding="utf-8").read()
        assert "clip_voice_wav(" in src, f"{rel}: читатель решает про голос сам"
    # Превью-прокси голосом больше не занимается: прокси всегда со звуком камеры, а
    # обработанный голос играет дорожкой плеера и печётся дверью /api/voicefx_bake.
    # Вернувшееся сюда решение про голос — это снова пересборка прокси файла камеры на
    # каждую правку ручки шумодава.
    prev = io.open(ROOT / "api/previewproxy.py", encoding="utf-8").read()
    for gone in ("clip_final_fx(", "clip_voice_wav(", "voice_fx_on(", "ensure_final_voice("):
        assert gone not in prev, f"превью-прокси снова решает про голос: {gone}"
    build = io.open(ROOT / "core/xml2ae/build.py", encoding="utf-8").read()
    assert "final_voice_for_build(" in build


# --------------------------------------------------------------------------- #
# 2. Запекание голоса клипа: готов — путь, нет — ход работы
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_bake_route_returns_ready_voice(clip: tuple[str, str]) -> None:
    """Готовая ДОРОЖКА ШУМОДАВА под теми же настройками отдаётся сразу, без счёта."""
    import webui
    webui.app.config["TESTING"] = True
    c = webui.app.test_client()
    xml, cam1 = clip
    voice = _baked_voice(xml, cam1, OLD_FX)
    # Превью играет дорожку шумодава из КЕША (ключ — исходник и настройки шумодава,
    # плагинов в нём нет), а `<стем>.voice.wav` рядом с XML остаётся читателям
    # AE/DRP/XML и запросом превью не трогается.
    track = voicefx.denoise_cache_path(cam1, voicefx.normalize_fx(OLD_FX)["denoise"])
    os.makedirs(os.path.dirname(track), exist_ok=True)
    _write_wav(track, 0.1)

    d = c.post("/api/voicefx_bake", json={"xml": xml, "src": cam1, "fx": OLD_FX}).get_json()
    assert d["ok"] is True and d["ready"] is True and d["running"] is False, d
    assert d["path"] == track, "готовая дорожка не отдана"
    assert os.path.isfile(voice), "итоговый трек рядом с XML пропал"


def test_bake_route_clears_voice_when_processing_is_off(clip: tuple[str, str]) -> None:
    """Выключили обработку — запечённый голос клипа убран, а не «полежал и сойдёт».

    Иначе старое «выключил шумодав — звук исходный» не выполнялось бы: файл рядом с
    XML продолжают читать XML, `.drp` и черновик.
    """
    import webui
    webui.app.config["TESTING"] = True
    c = webui.app.test_client()
    xml, cam1 = clip
    voice = _baked_voice(xml, cam1, OLD_FX)
    meta = os.path.splitext(xml)[0] + ".voice.json"

    d = c.post("/api/voicefx_bake", json={"xml": xml, "src": cam1, "fx": OFF_FX}).get_json()
    assert d["ok"] is True and d["ready"] is False and d["running"] is False, d
    assert not os.path.exists(voice), "выключенная обработка оставила запечённый голос"
    assert not os.path.exists(meta), "сайдкар голоса остался"
    assert voicefx.clip_voice_wav(xml) == ""


def test_bake_route_reports_progress_from_the_denoiser(clip: tuple[str, str],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Проценты запекания берутся у шумодава (`N/M`), а не выдумываются.

    RoFormer печатает ход работы сам: строка вида `45%|…| 9/20`. Разбор живёт в
    `_progress_emit`, и через него проценты доезжают до состояния задания — по ним
    превью показывает «голос обрабатывается, k %». Здесь проверяются оба конца:
    строка чужого процесса -> проценты и ход работы -> `progress` у `render`.
    """
    lines: list[str] = []
    seen: list[tuple[int, int]] = []

    def emit(line: str = "", /, **kw: Any) -> None:
        lines.append(str(kw.get("line") or line))

    voicefx._progress_emit(emit, lambda i, n: seen.append((i, n)))(
        "RoFormer: {line}", what="RoFormer", line=" 45%|███       | 9/20 [00:12<00:15]")
    assert seen == [(9, 20)], seen
    assert lines == [" 45%|███       | 9/20 [00:12<00:15]"], "строка не ушла в лог"
    assert seen == [(9, 20)], "повторный вызов разбор не повторил"

    # Ход работы доезжает до самого render: заглушка дочернего процесса зовёт progress
    cam1 = clip[1]
    calls: list[tuple[int, int]] = []

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        cmd = [str(x) for x in cmd]
        _write_wav(cmd[-1], 1.0)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_child(cmd: list[str], what: str, timeout: int, emit: Any = None,
                   cancelled: Any = None, progress: Any = None,
                   pid_of: Any = None) -> Any:
        if progress is not None:
            progress(1, 4)
            progress(4, 4)
        argv = [str(c) for c in cmd]
        out_dir = argv[argv.index("--output_dir") + 1]
        os.makedirs(out_dir, exist_ok=True)
        _write_wav(os.path.join(out_dir, os.path.basename(argv[1]) + "_(dry)_model.wav"),
                   1.0, rate=44100, channels=2)
        return voicefx._ChildRun([{"done": True}], [], 0)

    from core import voicefx_sep
    monkeypatch.setattr(voicefx_sep, "env_ready", lambda: True)
    monkeypatch.setattr(voicefx_sep, "model_ready", lambda engine: True)
    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    monkeypatch.setattr(voicefx, "_run_child", fake_child)
    voicefx.render(cam1, {"denoise": {"on": True, "engine": "roformer", "mix": 100}},
                   str(Path(cam1).with_suffix(".out.wav")),
                   progress=lambda i, n: calls.append((i, n)))
    assert calls == [(1, 4), (4, 4)], calls


def test_bake_route_reports_failure_without_baking(clip: tuple[str, str],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка запекания — в состояние задания, а не молчание: превью играет камеру.

    Поток фоновый: исключение из него не должно улетать в никуда (иначе превью
    висело бы «обрабатывается» вечно).
    """
    from api import voicefx as apivfx

    xml, cam1 = clip
    _speaker(OLD_FX)
    _sidecar(xml, cam1)
    # Запекание превью — это дорожка шумодава: её и роняем.
    monkeypatch.setattr(voicefx, "denoise_track",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет модели")))
    assert apivfx._voice_start(xml, cam1, voicefx.normalize_fx(OLD_FX)) is True
    # Поток короткий (одна заглушка) — дожидаемся его конца
    for _ in range(200):
        with apivfx.VOICELOCK:
            if not apivfx.VOICEJOB["running"]:
                break
        time.sleep(0.01)
    with apivfx.VOICELOCK:
        assert apivfx.VOICEJOB["running"] is False, "задание осталось «идущим»"
        assert "нет модели" in apivfx.VOICEJOB["error"], apivfx.VOICEJOB


def test_bake_status_route_reports_the_job(client: Any) -> None:
    """Ход запекания отдаётся фронту: по нему рисуется «голос обрабатывается, k %».

    Своё состояние задание отдаёт тем же запросом, что и сборка прокси: второго окна
    прогресса в интерфейсе нет (`progOpen`/`progItem`/`progDone`).
    """
    d = client.get("/api/voicefx_bake_status").get_json()
    assert d["ok"] is True, d
    for key in ("running", "done", "pct", "i", "n", "path", "error", "log"):
        assert key in d, f"в состоянии запекания нет поля {key}"


def test_bake_route_rejects_missing_clip(client: Any) -> None:
    """Нет XML — понятная ошибка с кодом, а не падение роута."""
    d = client.post("/api/voicefx_bake", json={"xml": "C:/нет-такого.xml"}).get_json()
    assert d.get("err") == "voicefx_no_xml", d


# --------------------------------------------------------------------------- #
# 3. Один выключатель в профиле: писать больше нечего, читается миграцией
# --------------------------------------------------------------------------- #
def test_profile_needs_no_cut_and_final() -> None:
    """`voice_fx` без `cut`/`final` — норма: решение одно и выводится из обработки."""
    key, _path = speakers.save("Новый", {"voice_fx": {"denoise": {"on": True}}})
    prof = speakers.load(key) or {}
    assert "cut" not in prof["voice_fx"] and "final" not in prof["voice_fx"]
    fx = voicefx.normalize_fx(prof["voice_fx"])
    assert fx["cut"] is True and fx["final"] is True
    assert voicefx.voice_fx_on(prof["voice_fx"]) is True
