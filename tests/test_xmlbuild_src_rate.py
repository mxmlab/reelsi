# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты параметров частоты кадров исходника в Premiere XML (core/xmlbuild.py)."""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xmlbuild
from core.xmlbuild import _file_def, _src_rate
from core.xmltext import xml_text as _esc


def _legacy_file_def(file_id: str, name: str, url: str, dur_s: float, width: int, height: int, tc: str) -> str:
    """Точная копия старой реализации _file_def без параметра fps (для проверки байт-в-байт)."""
    src_fps = 30000 / 1001
    src_dur = round(dur_s * src_fps)
    return f"""\t\t\t\t\t\t<file id="{file_id}">
\t\t\t\t\t\t\t<name>{_esc(name)}</name>
\t\t\t\t\t\t\t<pathurl>{url}</pathurl>
\t\t\t\t\t\t\t<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
\t\t\t\t\t\t\t<duration>{src_dur}</duration>
\t\t\t\t\t\t\t<timecode><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><string>{tc}</string><displayformat>DF</displayformat></timecode>
\t\t\t\t\t\t\t<media>
\t\t\t\t\t\t\t\t<video><samplecharacteristics><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><width>{width}</width><height>{height}</height><anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance></samplecharacteristics></video>
\t\t\t\t\t\t\t\t<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio>
\t\t\t\t\t\t\t</media>
\t\t\t\t\t\t</file>
"""


# --------------------------------------------------------------------------- #
# 1. Таблица частот кадров через чистую функцию _src_rate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fps,expected", [
    (None, (30, True, True)),
    (0, (30, True, True)),
    (-1.0, (30, True, True)),
    (24000 / 1001, (24, True, False)),
    (23.976, (24, True, False)),
    (30000 / 1001, (30, True, True)),
    (29.97, (30, True, True)),
    (60000 / 1001, (60, True, True)),
    (59.94, (60, True, True)),
    (24.0, (24, False, False)),
    (25.0, (25, False, False)),
    (30.0, (30, False, False)),
    (50.0, (50, False, False)),
    (60.0, (60, False, False)),
    (48.0, (48, False, False)),
])
def test_src_rate_table(fps, expected):
    """Проверка таблицы сопоставления fps -> (timebase, ntsc, drop)."""
    assert _src_rate(fps) == expected


# --------------------------------------------------------------------------- #
# 2. _file_def при fps=25.0
# --------------------------------------------------------------------------- #
def test_file_def_25fps():
    """_file_def при fps=25.0: 3 раза <timebase>25</timebase><ntsc>FALSE</ntsc>,
    NDF, таймкод с разделителем ':' и длительность round(dur_s * 25).
    """
    dur_s = 10.5
    out = _file_def("file-1", "cam1.mp4", "file://localhost/cam1.mp4", dur_s, 1920, 1080, "01;02;03;04", fps=25.0)
    assert out.count("<timebase>25</timebase><ntsc>FALSE</ntsc>") == 3
    assert "<displayformat>NDF</displayformat>" in out
    assert "<string>01:02:03:04</string>" in out
    expected_dur = round(dur_s * 25)
    assert f"<duration>{expected_dur}</duration>" in out


# --------------------------------------------------------------------------- #
# 3. Байт-в-байт совпадение с эталоном для 29.97 / None
# --------------------------------------------------------------------------- #
def test_file_def_byte_for_byte_identical_to_legacy():
    """_file_def с fps=30000/1001 и fps=None даёт байт-в-байт идентичный XML старому коду."""
    args = ("file-test", "Cam_1 & 2.mp4", "file://localhost/C%3a/Cam_1.mp4", 45.678, 3840, 2160, "01;23;45;12")
    legacy = _legacy_file_def(*args)
    assert _file_def(*args, fps=None) == legacy
    assert _file_def(*args) == legacy
    assert _file_def(*args, fps=30000 / 1001) == legacy


# --------------------------------------------------------------------------- #
# 4. Передача частоты исходника через xmlbuild.build()
# --------------------------------------------------------------------------- #
def _extract_file_block(xml_text: str, file_id: str) -> str:
    """Вырезает блок <file id="...">...</file> из текста XML."""
    m = re.search(rf'<file\s+id="{re.escape(file_id)}"\s*>.*?</file>', xml_text, re.DOTALL)
    assert m is not None, f"Определение <file id=\"{file_id}\">...</file> не найдено в XML"
    return m.group(0)


def test_build_passes_source_fps(tmp_path, monkeypatch):
    """xmlbuild.build() передаёт реальную частоту исходника из probe() в определение <file>."""
    monkeypatch.setattr(xmlbuild, "probe", lambda p: {
        "dur_s": 60.0,
        "width": 1920,
        "height": 1080,
        "timecode": "01;00;00;00",
        "fps": 25.0 if "cam1" in str(p) else 30000 / 1001,
    })

    out = tmp_path / "out.xml"
    xmlbuild.build(
        [str(tmp_path / "cam1.mp4"), str(tmp_path / "cam2.mp4")],
        [(0.0, 5.0)],
        [0.0, 0.0],
        str(out),
        assign=[0],
    )
    xml_text = out.read_text(encoding="utf-8")

    block1 = _extract_file_block(xml_text, "file-1")
    assert "<timebase>25</timebase><ntsc>FALSE</ntsc>" in block1
    assert "NDF" in block1
    assert "01:00:00:00" in block1

    block2 = _extract_file_block(xml_text, "file-2")
    assert "<timebase>30</timebase><ntsc>TRUE</ntsc>" in block2
    assert "DF" in block2
    assert "01;00;00;00" in block2

