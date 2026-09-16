# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты корректности артефактов (задание HT, круг 6).

1. `_js`/`_js_multiline` не оставляют сырых U+2028/U+2029: ExtendScript (ES3) считает
   их переводом строки — литерал рвётся и падает импорт всего .jsx, а `node --check`
   (ES2019) этого не видит. Сторож в `core/verify_jsx.py` ловит их в готовом файле.
2. XML-экранирование одно на весь проект (`core/xmltext.xml_text`) и убирает символы,
   запрещённые XML 1.0 (C0 кроме \\t\\n\\r, U+FFFE/U+FFFF, одиночные суррогаты):
   иначе `\\x01` в имени файла — `ParseError` и не открывается весь XML/.drp.
3. `drp.timecode_frames`: drop-frame при 59.94 пропускает 4 кадра в минуту (1 ч = 3600 с).
4. Время SRT считается в одной функции (`core/subs.format_srt_time`, перенос через
   divmod), `align._ts`/`make_srt` — в fps таймлайна, `subtitle_xml` отдаёт `meta["fps"]`.
5. `highlights`: запись XML пользователя атомарная (сбой не оставляет пустой файл),
   ошибка бэкапа — warning в лог, а не молчание.
"""
import os
import xml.etree.ElementTree as ET

import pytest

from core import align, drp, subs
from core.xml2ae import jsutil


def test_js_escapes_u2028_u2029():
    """_js и _js_multiline экранируют U+2028 и U+2029 через \\u2028 / \\u2029."""
    raw_s = "hello\u2028world\u2029test"
    js_lit = jsutil._js(raw_s)
    assert "\u2028" not in js_lit, "Сырой U+2028 остался в литерале _js"
    assert "\u2029" not in js_lit, "Сырой U+2029 остался в литерале _js"
    assert r"\u2028" in js_lit
    assert r"\u2029" in js_lit

    multi_lit = jsutil._js_multiline("first\u2028line\r\nsecond")
    assert "\u2028" not in multi_lit, "Сырой U+2028 остался в литерале _js_multiline"
    assert r"\u2028" in multi_lit
    assert r"\r" in multi_lit  # \n -> \r для AE


def test_verify_jsx_catches_raw_u2028_u2029(tmp_path):
    """verify_jsx помечает ошибкой сырые U+2028/U+2029 в коде .jsx."""
    from core import verify_jsx

    jsx_file = tmp_path / "test_u2028.jsx"
    jsx_file.write_text('var MSG = "line\u2028break";\n', encoding="utf-8-sig")
    rep = verify_jsx.verify(str(jsx_file))
    assert not rep.ok
    assert any("U+2028" in err or "U+2029" in err for err in rep.errors)


def test_xml_text_strips_disallowed_characters():
    """xml_text удаляет запрещённые XML 1.0 символы (C0 кроме \\t\\n\\r, U+FFFE, суррогаты)."""
    from core.xmltext import xml_text

    bad = "test\x01\x08\x0b\x0c\x1f&\t<\n>\r\"\ud800\ufffe\uffff"
    cleaned = xml_text(bad)
    assert "\x01" not in cleaned
    assert "\x08" not in cleaned
    assert "\x0b" not in cleaned
    assert "\x0c" not in cleaned
    assert "\x1f" not in cleaned
    assert "\ud800" not in cleaned, "одиночный суррогат остался — файл не закодируется"
    assert "\ufffe" not in cleaned
    assert "\uffff" not in cleaned
    assert "&amp;" in cleaned
    assert "&lt;" in cleaned
    assert "&gt;" in cleaned
    assert "\t" in cleaned and "\n" in cleaned and "\r" in cleaned


def test_xml_escape_is_one_function_everywhere():
    """Своих копий XML-экранирования не осталось: все четыре места — та же функция.

    Копии расходятся: правку (например, вырезание запрещённых XML 1.0 символов)
    вносят в одну, остальные продолжают ломать файл на `\\x01` в имени."""
    from core import xmlbuild
    from core.xmltext import xml_text

    assert subs._xml_escape is xml_text
    assert drp._esc is xml_text
    assert xmlbuild._esc is xml_text


def test_xml_build_and_subs_handle_c0_characters():
    """Сборка клипов Premiere и субтитров не падает ParseError при C0 в именах."""
    from core import xmlbuild

    name_with_c0 = "clip\x01name&cool"
    cxml = xmlbuild._file_def("f1", name_with_c0, "http://path", 10.0, 1920, 1080, "00:00:00:00")
    # Должен быть валидным XML без ParseError
    root = ET.fromstring(f"<root>{cxml}</root>")
    name_el = root.find(".//name")
    assert name_el is not None
    assert "\x01" not in name_el.text
    assert name_el.text == "clipname&cool"

    # subs
    sb = subs.SubtitleBuilder()
    clip_xml = sb.clip("СЛОВО\x01ТЕСТ", 0, 60, 1, 1)
    c_root = ET.fromstring(clip_xml)
    eff_name = c_root.find(".//filter/effect/name")
    assert eff_name is not None
    assert "\x01" not in eff_name.text


def test_drp_escapes_c0_in_project_name():
    """Имя проекта с C0 не ломает .drp: `\\x01` в XML — ParseError, файл не откроется."""
    el = drp._set("<ProjectName></ProjectName>", "ProjectName", "проект\x01&тест")
    assert el == "<ProjectName>проект&amp;тест</ProjectName>"


def test_drp_timecode_frames_drop_frame_5994():
    """drp.timecode_frames для 59.94 сбрасывает 4 кадра/мин (1 ч = 3600 с ±1 кадр)."""
    fps_5994 = 60000 / 1001  # 59.94005994...
    f_1h_5994 = drp.timecode_frames("01;00;00;00", fps=fps_5994)
    # 3600 с * (60000 / 1001) = 215784.215784...
    # Ровно 3600 с (±1 кадр)
    dur_s_5994 = f_1h_5994 / fps_5994
    assert abs(dur_s_5994 - 3600.0) < (1.0 / 59.94), f"59.94 1h дал {dur_s_5994} сек вместо 3600"
    assert f_1h_5994 == 215784

    # 29.97 drop frame: сбрасывает 2 кадра/мин
    fps_2997 = 30000 / 1001
    f_1h_2997 = drp.timecode_frames("01;00;00;00", fps=fps_2997)
    dur_s_2997 = f_1h_2997 / fps_2997
    assert abs(dur_s_2997 - 3600.0) < (1.0 / 29.97), f"29.97 1h дал {dur_s_2997} сек вместо 3600"
    assert f_1h_2997 == 107892

    # 25 fps PAL: без drop-frame даже при наличии ';'
    f_1h_25 = drp.timecode_frames("01;00;00;00", fps=25)
    assert f_1h_25 == 3600 * 25


def test_format_srt_time_overflow():
    """format_srt_time корректно переносит 59.9996 в 00:01:00,000 и 3599.9996 в 01:00:00,000."""
    assert subs.format_srt_time(59.9996) == "00:01:00,000"
    assert subs.format_srt_time(3599.9996) == "01:00:00,000"
    assert subs.format_srt_time(0.0) == "00:00:00,000"


def test_align_ts_and_make_srt_respect_fps(tmp_path):
    """align._ts и make_srt учитывают параметр fps (кадр 150 при 25 fps -> 00:00:06,000)."""
    assert align._ts(150, fps=25) == "00:00:06,000"

    srt_file = tmp_path / "test.srt"
    words = [{"w": "слово", "start": 0, "end": 150}]
    align.make_srt(words, str(srt_file), min_cue_frames=0, fps=25)
    content = srt_file.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:06,000" in content


def test_subtitle_xml_srt_uses_sequence_fps(tmp_path, monkeypatch):
    """Сквозная проверка: .srt пишется в fps секвенции, а не в константе 60.

    Слово 1.0..1.6 с источника лежит в клипе 0..50 кадров 25-кадровой секвенции:
    на таймлайне это кадры 25..50, то есть 1.0..2.0 с. С прежним `_ts` (делил на 60)
    вышло бы 00:00:00,416 --> 00:00:00,833 — в 2.4 раза меньше реального."""
    from core import subtitle_xml, transcribe

    words = [{"w": "привет", "start": 1.0, "end": 1.6}]
    monkeypatch.setattr(transcribe, "words_cache_path", lambda src, **kw: "нет-кэша.json")
    monkeypatch.setattr(transcribe, "load_words_cache", lambda path: list(words))

    meta = {"fps": 25, "name": "Reelsi", "w": 1080, "h": 1920, "dur": 50}
    cams = [{"path": "cam1.mp4", "name": "Камера 1", "clips": [(0, 50, 0, 50, True, 100.0)]}]
    monkeypatch.setattr(subtitle_xml, "parse_full", lambda p, **kw: (meta, cams, [], []))

    xml_file = tmp_path / "seq.xml"
    xml_file.write_text("<xmeml version='4'><sequence><media><video><track/>"
                        "</video></media></sequence></xmeml>", encoding="utf-8")
    res = subtitle_xml.add_subtitles(str(xml_file), str(tmp_path / "out.xml"),
                                     emit=lambda *a, **k: None)
    assert res["subtitles"] == 1, res

    srt = (tmp_path / "out.srt").read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,000" in srt, srt


def test_highlights_atomic_write_preserves_file_on_error(tmp_path, monkeypatch):
    """При сбое во время записи исходный XML остаётся нетронутым (не усекается)."""
    from core.xml2ae import highlights

    orig_content = '<?xml version="1.0" encoding="UTF-8"?>\n<xmeml version="4"><sequence><name>test</name></sequence></xmeml>'
    xml_file = tmp_path / "seq.xml"
    xml_file.write_text(orig_content, encoding="utf-8")

    # Имитируем сбой посередине записи через подмену os.replace
    def fail_replace(src, dst):
        raise OSError("Диск полон или процесс прерван")

    monkeypatch.setattr(os, "replace", fail_replace)

    root = ET.fromstring(orig_content)
    with pytest.raises(OSError, match="Диск полон"):
        highlights._write_xml_prolog(root, str(xml_file))

    # Исходный файл должен остаться целым и не пустым!
    assert xml_file.read_text(encoding="utf-8") == orig_content
    # и временный файл за собой убран
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_highlights_backup_warning_on_failure(tmp_path, monkeypatch, caplog):
    """Ошибка бэкапа не прерывает запись, а оставляет предупреждение в логе."""
    import logging
    import shutil
    from core.applog import get_logger
    from core.xml2ae import highlights

    # файловый лог — в tmp, а не в reelsi.log рабочей копии
    monkeypatch.setenv("REELSI_LOG", str(tmp_path / "reelsi.log"))
    get_logger(highlights.log.name)

    orig_content = '<?xml version="1.0" encoding="UTF-8"?>\n<xmeml version="4"><sequence><name>test</name></sequence></xmeml>'
    xml_file = tmp_path / "seq_backup.xml"
    xml_file.write_text(orig_content, encoding="utf-8")

    def fail_copy(src, dst):
        raise PermissionError("Access denied")

    monkeypatch.setattr(shutil, "copy2", fail_copy)

    with caplog.at_level(logging.WARNING, logger="reelsi"):
        root = ET.fromstring(orig_content)
        highlights._write_xml_prolog(root, str(xml_file))

    # Запись успешна
    assert xml_file.exists()
    assert len(xml_file.read_text(encoding="utf-8")) > 0
    # В логе есть предупреждение об ошибке бэкапа
    assert any("бэкап" in r.message.lower() for r in caplog.records), caplog.records
