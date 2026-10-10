# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук камеры 1 для редактора шага 1 — POST /api/preview_audio.

Редактор играет звук не элементом <video>, а буфером Web Audio: каждый блок правки
встаёт в очередь с точностью до сэмпла (static/app/60-preview.js, блок `ea*`). Буферу
нужен WAV, который браузер декодирует целиком; исходник 4K для этого не годится.
Роут вынимает звук камеры 1 клипа (та же камера, что у /api/aicut_preview и у
обработанного голоса) в `_tmp/pa_*.wav` — один раз на файл камеры, дальше из кэша.

Проверяется:
1. звук вынут: WAV 48 кГц 16 бит, в `_tmp` рядом с XML, путь — в ответе;
2. второй запрос берёт кэш и ffmpeg не зовёт;
3. нет XML или камеры — понятная ошибка, а не 500;
4. авто-уборка `_tmp` перед нарезкой бережёт `pa_*.wav` вместе с прокси.

Запуск:  py -3.10 -m pytest tests/test_preview_audio_route.py -q
"""
import os
import shutil
import subprocess
import sys
import wave

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import draftrender, xml2ae  # noqa: E402

ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg в PATH")


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def clip(tmp_path, monkeypatch):
    """XML клипа и «камера 1» — секунда тона в стерео 44,1 кГц (не 48: роут обязан
    привести частоту сам). Разбор XML подменяем: проверяется роут, а не парсер EDL."""
    src = tmp_path / "cam" / "cam1.wav"
    src.parent.mkdir()
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1:sample_rate=44100", "-ac", "2", str(src)],
                       check=True)
    xml = tmp_path / "out" / "01_clip.xml"
    xml.parent.mkdir()
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(xml2ae, "virtual_edl", lambda p: {"cams": [{"path": str(src)}]})
    return xml, src


@ffmpeg
def test_the_route_extracts_camera_audio_into_tmp(client, clip):
    xml, src = clip
    d = client.post("/api/preview_audio", json={"xml": str(xml)}).get_json()
    assert d.get("ok") is True, d
    path = d["path"]
    assert os.path.dirname(path) == os.path.join(str(xml.parent), "_tmp"), path
    assert os.path.basename(path).startswith("pa_") and path.endswith(".wav"), path
    assert d["src"] == str(src)
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 48000, "звук не приведён к 48 кГц"
        assert w.getsampwidth() == 2 and w.getnchannels() == 2, "формат WAV не pcm16 / каналы не те"
        assert abs(w.getnframes() / 48000 - 1.0) < 0.05, "длина звука не совпала с исходником"
    assert not any(n.endswith(".part.wav") for n in os.listdir(os.path.dirname(path))), (
        "недописанный .part остался в _tmp")


@ffmpeg
def test_the_second_request_takes_the_cache(client, clip, monkeypatch):
    xml, _src = clip
    first = client.post("/api/preview_audio", json={"xml": str(xml)}).get_json()
    calls = []
    monkeypatch.setattr(draftrender, "build_preview_audio",
                        lambda s, d: calls.append((s, d)) or None)
    again = client.post("/api/preview_audio", json={"xml": str(xml)}).get_json()
    assert again.get("ok") is True and again["path"] == first["path"], again
    assert calls == [], "звук вынимается заново при готовом кэше"


def test_missing_xml_or_camera_is_a_clear_error(client, tmp_path, monkeypatch):
    d = client.post("/api/preview_audio", json={"xml": str(tmp_path / "nope.xml")}).get_json()
    assert d.get("error"), d
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(xml2ae, "virtual_edl", lambda p: {"cams": []})
    d = client.post("/api/preview_audio", json={"xml": str(xml)}).get_json()
    assert d.get("error"), d


def test_failed_extraction_is_a_clear_error(client, clip, monkeypatch):
    xml, _src = clip
    monkeypatch.setattr(draftrender, "build_preview_audio", lambda s, d: None)
    d = client.post("/api/preview_audio", json={"xml": str(xml)}).get_json()
    assert d.get("error"), d


def test_auto_clean_keeps_the_camera_audio(tmp_path):
    """Звук камеры — такой же кэш по файлу камеры, как прокси: авто-уборка его бережёт."""
    tdir = tmp_path / "_tmp"
    tdir.mkdir()
    pa = tdir / "pa_0123456789ab.wav"
    pa.write_bytes(b"x" * 10)
    junk = tdir / "junk.wav"
    junk.write_bytes(b"y" * 7)
    freed = draftrender.clean_tmp(str(tmp_path), emit=lambda *a, **k: None, proxies=False)
    assert pa.is_file(), "авто-уборка снесла звук камеры редактора"
    assert not junk.exists() and freed == 7
    assert draftrender.proxy_size(str(tmp_path)) == 10, "звук камеры не учтён в размере кэша"
