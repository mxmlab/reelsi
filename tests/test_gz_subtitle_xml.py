# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт E: субтитры в готовую секвенцию (`core/subtitle_xml.py`).

Два дефекта одного модуля:
  * `txt.index("</video>", mi)` брал закрытие `<video>` ВНУТРИ `<file>` первого клипа,
    а не видео-часть секвенции — дорожка субтитров садилась внутрь определения файла;
  * `map_words_to_clips` считал кадры по константе `FPS = 60`, хотя `add_subtitles`
    читает настоящий fps секвенции из `meta["fps"]` — на 25-кадровой секвенции слова
    не попадали ни в один клип и субтитров не было вовсе.

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

FIXTURE = ROOT / "tests" / "fixtures" / "timeline_nosubs.xml"
SEQ_RATE = '<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>'
# Клип 1 фикстуры: таймлайн 0..43, источник 170..213 (кадры 60fps).
WORDS_60 = [{"w": "привет", "start": 2.9, "end": 3.1}]
# Та же секвенция с rate 25: те же in/out теперь 6.8..8.52с источника.
WORDS_25 = [{"w": "привет", "start": 7.0, "end": 7.2}]


def _no_emit(*a, **k):
    pass


@pytest.fixture
def words_stub(monkeypatch):
    """Транскрипт подменяем: тест про вставку дорожки и fps, а не про Whisper."""
    from core import transcribe

    def stub(words):
        monkeypatch.setattr(transcribe, "load_words_cache", lambda path: list(words))
        monkeypatch.setattr(transcribe, "words_cache_path", lambda src, **kw: "нет-кэша.json")
    return stub


def _cli_xml(tmp_path, name="edited.xml", fps=60):
    p = tmp_path / name
    text = FIXTURE.read_text(encoding="utf-8")
    if fps != 60:
        assert SEQ_RATE in text
        text = text.replace(SEQ_RATE, SEQ_RATE.replace("60", str(fps)), 1)
    p.write_text(text, encoding="utf-8")
    return p


def _tracks(path):
    """(дорожек у sequence/media/video, дорожек внутри любого <file>)."""
    root = ET.parse(path).getroot()
    seq = root.find(".//sequence")
    return (len(seq.findall("media/video/track")),
            sum(len(f.findall("media/video/track")) for f in root.findall(".//file")))


def test_дорожка_субтитров_идёт_в_видео_секвенции_а_не_в_file(tmp_path, words_stub):
    from core import subtitle_xml

    words_stub(WORDS_60)
    xml = _cli_xml(tmp_path)
    seq_before, file_before = _tracks(xml)
    assert file_before == 0, "фикстура изменилась: внутри <file> уже есть дорожки"

    res = subtitle_xml.add_subtitles(str(xml), str(tmp_path / "out.xml"), emit=_no_emit)

    assert res["subtitles"] == 1, res
    seq_after, file_after = _tracks(tmp_path / "out.xml")
    assert file_after == file_before == 0, "дорожка субтитров уехала внутрь <file>"
    assert seq_after == seq_before + 1, "дорожка не добавилась в sequence/media/video"


def test_слово_в_клипе_25fps_не_теряется():
    """Кадры считаются по fps секвенции: при 25 клип 0..50 — это 2 секунды, и слово
    внутри них обязано найтись (на константе 60 оно «не влезало» и пропадало)."""
    from core import subtitle_xml

    clips = [(0, 50, 0, 50, True, 100.0)]
    words = [{"w": "привет", "start": 1.0, "end": 1.2}]

    placed = subtitle_xml.map_words_to_clips(words, clips, fps=25)
    assert len(placed) == 1, "слово потеряно на 25 fps"
    assert placed[0]["w"] == "привет"
    # без fps остаётся прежний дефолт 60 — старый вызов не меняет поведения
    assert subtitle_xml.map_words_to_clips(words, clips) == []


def test_add_subtitles_берёт_fps_из_секвенции(tmp_path, words_stub, monkeypatch):
    """Сквозная проверка: на секвенции 25 fps слово доезжает до дорожки субтитров."""
    from core import subtitle_xml

    seen = {}
    real = subtitle_xml.map_words_to_clips

    def spy(words, clips, *a, **kw):
        seen.update(kw)
        return real(words, clips, *a, **kw)

    monkeypatch.setattr(subtitle_xml, "map_words_to_clips", spy)
    words_stub(WORDS_25)
    xml = _cli_xml(tmp_path, "edited25.xml", fps=25)

    res = subtitle_xml.add_subtitles(str(xml), str(tmp_path / "out25.xml"), emit=_no_emit)

    assert seen.get("fps") == 25, f"fps секвенции не передан: {seen}"
    assert res["subtitles"] == 1, "на 25 fps субтитров не собралось"
    text = (tmp_path / "out25.xml").read_text(encoding="utf-8")
    assert re.search(r'<name>[^<]*</name>\s*<effectid>GraphicAndType</effectid>', text), \
        "в XML нет ни одного слова-субтитра"
