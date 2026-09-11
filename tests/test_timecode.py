# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стартовый таймкод исходника в XML.

Баг 2026-08-06: `probe()` спрашивал таймкод только у `v:0`, а камеры Sony (XAVC)
кладут его в служебную дорожку (`tmcd`/`rtmd`). В XML уезжали нули. Премьер этого
не замечает — он берёт медиа по `pathurl`, — а DaVinci Resolve позиционирует клипы
по таймкоду, и вся нарезка ехала на часы: файл начинается с 01;57;31;11, а в XML
было обещано 00;00;00;00.

Эталон снят с экспорта самого Премьера того же таймлайна (`C1412pp.xml`): там
`01;57;31;11` при том, что ffprobe отдаёт `01:57:31:11`. Значит точка с запятой
и `DF` — правильное форматирование, менять его не надо.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import xmlbuild
from core.xmlbuild import pick_timecode, unpathurl


def test_путь_из_xml_разбирается_разделителем_своей_системы(monkeypatch):
    """`unpathurl` жёстко ставил бэкслэш, и на Linux/macOS путь «/tmp/a/cam.mp4»
    превращался в «\\tmp\\a\\cam.mp4» — несуществующий. Любой путь из XML там
    «пропадал»: предполёт рендера на Linux-CI объявил камеры пропавшими и не пустил
    рендер (2026-08-14). os.sep подменяем — иначе тест проверял бы только свою систему.
    """
    monkeypatch.setattr(xmlbuild.os, "sep", "/")
    assert unpathurl("file://localhost//tmp/a/cam.mp4") == "/tmp/a/cam.mp4"
    monkeypatch.setattr(xmlbuild.os, "sep", "\\")
    assert unpathurl("file://localhost/C%3a/tmp/cam.mp4") == "C:\\tmp\\cam.mp4"


def test_разбор_pathurl_живёт_в_одном_месте(monkeypatch):
    """Копий этого преобразования было ДВЕ — `xmlbuild.unpathurl` и свой
    `_decode_pathurl` в `xml2ae/parse.py`, — и они разъехались: починили первую, а
    сборка читала вторую и продолжала терять все пути на Linux (2026-08-14, два
    красных прогона CI подряд). Проверяем тождество функций, а не поведение: копия,
    заведённая заново, пройдёт любой тест на результат и провалит этот.
    """
    from core.xml2ae.parse import _decode_pathurl
    assert _decode_pathurl is xmlbuild.unpathurl
    monkeypatch.setattr(xmlbuild.os, "sep", "/")
    assert _decode_pathurl("file://localhost//tmp/x/cam1.mp4") == "/tmp/x/cam1.mp4"


def test_таймкод_из_служебной_дорожки():
    """Sony: у видеопотока тега нет, таймкод лежит в rtmd — ровно наш случай."""
    d = {"streams": [{"codec_type": "video", "width": 3840, "height": 2160},
                     {"codec_type": "audio"},
                     {"codec_type": "data", "tags": {"timecode": "01:57:31:11"}}],
         "format": {"duration": "163.165"}}
    assert pick_timecode(d) == "01;57;31;11"


def test_таймкод_из_видеопотока():
    """Файлы, у которых тег есть в v:0 (как Timeline 2.mov), не должны сломаться."""
    d = {"streams": [{"codec_type": "video", "tags": {"timecode": "01:00:00:00"}},
                     {"codec_type": "data", "tags": {"timecode": "01:00:00:00"}}]}
    assert pick_timecode(d) == "01;00;00;00"


def test_таймкод_из_формата():
    d = {"streams": [{"codec_type": "video"}],
         "format": {"tags": {"timecode": "22:02:16:19"}}}
    assert pick_timecode(d) == "22;02;16;19"


def test_первый_поток_с_таймкодом_выигрывает():
    d = {"streams": [{"codec_type": "video", "tags": {"timecode": "10:00:00:00"}},
                     {"codec_type": "data", "tags": {"timecode": "20:00:00:00"}}]}
    assert pick_timecode(d) == "10;00;00;00"


@pytest.mark.parametrize("d", [
    {},
    {"streams": []},
    {"streams": [{"codec_type": "video"}], "format": {}},
    {"streams": [{"codec_type": "video", "tags": {}}]},
])
def test_нет_таймкода__нули_без_падения(d):
    """Таймкод есть не у всех файлов; отсутствие — не ошибка, а нули."""
    assert pick_timecode(d) == "00;00;00;00"


def test_drop_frame_не_переформатируется_дважды():
    """Если ffprobe уже отдал drop-frame нотацию, оставляем как есть."""
    d = {"streams": [{"codec_type": "video", "tags": {"timecode": "01;57;31;11"}}]}
    assert pick_timecode(d) == "01;57;31;11"


# --- починка готового XML на скачивании (/api/export_xml) ----------------------

def _xml(tc_cam="00;00;00;00"):
    """Скелет из двух <file>: камера с путём и субтитр-графика без пути."""
    return (
        '<xmeml version="4"><sequence>'
        '<timecode><rate><timebase>60</timebase></rate><string>00:00:00:00</string></timecode>'
        '<clipitem><file id="file-1"><name>CAM.MP4</name>'
        '<pathurl>file://localhost/C%3a/media/CAM.MP4</pathurl>'
        f'<timecode><rate><timebase>30</timebase></rate><string>{tc_cam}</string>'
        '<displayformat>DF</displayformat></timecode></file></clipitem>'
        '<clipitem><file id="file-2"><name>Graphic</name>'
        '<mediaSource>GraphicAndType</mediaSource>'
        '<timecode><rate><timebase>30</timebase></rate><string>00;00;00;00</string>'
        '</timecode></file></clipitem>'
        '</sequence></xmeml>')


def test_таймкод_проставляется_только_медиафайлам(monkeypatch, tmp_path):
    """Графике таймкод не трогаем: у неё нет пути и он синтетический.
    Таймкод последовательности — тоже не медиа, его трогать нельзя."""
    from core import xmlbuild
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    monkeypatch.setattr(xmlbuild, "probe", lambda p: {"timecode": "01;57;31;11"})
    out, n = xmlbuild.fix_timecodes(_xml())
    assert n == 1
    assert "<string>01;57;31;11</string>" in out
    assert out.count("<string>00;00;00;00</string>") == 1      # осталась только графика
    assert "<string>00:00:00:00</string>" in out               # таймкод последовательности цел


def test_пропавшее_медиа_не_роняет_скачивание(monkeypatch):
    """Исходник могли унести на другой диск — отдаём XML как был."""
    from core import xmlbuild
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    src = _xml()
    out, n = xmlbuild.fix_timecodes(src)
    assert (out, n) == (src, 0)


def test_битый_контейнер_не_роняет_скачивание(monkeypatch):
    from core import xmlbuild
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    def boom(p):
        raise RuntimeError("ffprobe не вернул длительность")
    monkeypatch.setattr(xmlbuild, "probe", boom)
    src = _xml()
    out, n = xmlbuild.fix_timecodes(src)
    assert (out, n) == (src, 0)


def test_повторная_починка_ничего_не_меняет(monkeypatch):
    """Идемпотентность: скачали дважды — результат тот же, счётчик пуст."""
    from core import xmlbuild
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    monkeypatch.setattr(xmlbuild, "probe", lambda p: {"timecode": "01;57;31;11"})
    once, n1 = xmlbuild.fix_timecodes(_xml())
    twice, n2 = xmlbuild.fix_timecodes(once)
    assert once == twice and n1 == 1 and n2 == 0


def test_путь_из_pathurl_декодируется(monkeypatch):
    """Кириллица и %3a вместо двоеточия — probe должен получить обычный путь."""
    from core import xmlbuild
    seen = []
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    monkeypatch.setattr(xmlbuild, "probe", lambda p: seen.append(p) or {"timecode": "01;00;00;00"})
    src = _xml().replace("C%3a/media/CAM.MP4",
                         "C%3a/%d0%ba%d0%b0%d0%bc%d0%b5%d1%80%d0%b01/CAM.MP4")
    xmlbuild.fix_timecodes(src)
    # Разделитель — свой у каждой системы (см. unpathurl). Тест про декодирование
    # кириллицы и %3a, а не про слеши: жёсткий «\» делал его проверкой одной только
    # Windows и падал на Linux-CI.
    assert seen == [os.sep.join(["C:", "камера1", "CAM.MP4"])]


def test_имя_камеры_не_зависит_от_ос():
    """Имя камеры = папка, в которой лежит файл, на ЛЮБОЙ ОС.

    Пути в XML всегда с обратными слешами (_decode_pathurl приводит их к виду
    Premiere). На Linux os.path обратный слеш разделителем не считает: dirname
    отдавал пустую строку, и в .jsx уезжало `"name":""` — слой камеры в After
    Effects оставался безымянным. На Windows баг не проявлялся вовсе (там
    os.path понимает оба разделителя), поэтому поймал его только CI 2026-08-11.
    """
    from core.xml2ae.parse import _parent_name
    assert _parent_name(r"C:\footage\cam1\CLIP-006.MP4") == "cam1"
    assert _parent_name("/mnt/footage/cam2/CLIP-006.MP4") == "cam2"
    assert _parent_name(r"C:\камера1\CAM.MP4") == "камера1"
    assert _parent_name("CLIP.MP4") == ""          # без папки — имени нет
    assert _parent_name(r"C:\CLIP.MP4") == ""      # корень диска папкой не считаем
    assert _parent_name(None) == ""
