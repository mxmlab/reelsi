# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт F: кусок раньше старта камеры берётся с камеры 1.

`ink = round((s - offsets[k]) * FPS)` уходил отрицательным, если кусок начался раньше,
чем камера k включилась: в XML появлялся отрицательный `<in>` (кадры не из того места,
сломанный синхрон). Решение архитектора: у камеры, назначенной куску, `s < offsets[k]`
— кусок берётся с камеры 1 (её сдвиг 0), а не `max(0, …)`.

Запуск:  python -m pytest tests -q
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

NEG_IN = re.compile(r"<in>-\d+</in>")


@pytest.fixture
def build(tmp_path, monkeypatch):
    """xmlbuild.build с подменённым probe: ffprobe в тестах не нужен."""
    from core import xmlbuild

    monkeypatch.setattr(xmlbuild, "probe", lambda p: {
        "dur_s": 60.0, "width": 1920, "height": 1080, "timecode": "01;00;00;00"})

    def run(segments, offsets, assign, name="out.xml"):
        out = tmp_path / name
        xmlbuild.build([str(tmp_path / "cam1.mp4"), str(tmp_path / "cam2.mp4")],
                       segments, offsets, str(out), assign=assign)
        return out.read_text(encoding="utf-8")

    return run


def _enabled_video_clips(text):
    """Включённые видео-клипы секвенции (что реально показывается в кадре)."""
    seq = ET.fromstring(text).find(".//sequence")
    return [c for c in seq.findall("media/video/track/clipitem")
            if (c.findtext("enabled") or "") == "TRUE"]


def test_кусок_раньше_старта_камеры_берётся_с_камеры_1(build):
    """Сдвиги [0, 2.0], кусок 0.5–1.5с назначен камере 2: материала у неё там нет —
    источником становится камера 1, отрицательных `<in>` в XML не остаётся."""
    text = build([(0.5, 1.5)], [0.0, 2.0], assign=[1])

    assert not NEG_IN.search(text), "в XML остался отрицательный <in>"
    clip = _enabled_video_clips(text)
    assert len(clip) == 1, "в кадре должна быть ровно одна камера"
    assert clip[0].findtext("name") == "cam1.mp4", "кусок показан не с камеры 1"
    assert clip[0].find(".//file").get("id") == "file-1"
    assert "<in>30</in>" in text                      # 0.5с * 60 кадров — начало куска


def test_камера_с_материалом_остаётся_источником(build):
    """Обратная сторона: если камера 2 в этот момент уже писала — кусок её, подмена
    не должна срабатывать всегда."""
    text = build([(2.5, 3.5)], [0.0, 2.0], assign=[1])

    assert not NEG_IN.search(text)
    clip = _enabled_video_clips(text)
    assert [c.findtext("name") for c in clip] == ["cam1.mp4", "cam2.mp4"]
    cam2 = clip[1]
    assert cam2.find(".//file").get("id") == "file-2"
    assert int(cam2.findtext("in")) == round((2.5 - 2.0) * 60)   # 30, не отрицательный


def test_нулевые_сдвиги_ничего_не_меняют(build):
    """Обычный случай (камеры стартовали вместе): подмены быть не должно."""
    text = build([(0.2, 1.2)], [0.0, 0.0], assign=[1])

    assert not NEG_IN.search(text)
    clip = _enabled_video_clips(text)
    assert [c.findtext("name") for c in clip] == ["cam1.mp4", "cam2.mp4"]
    assert clip[1].find(".//file").get("id") == "file-2"
