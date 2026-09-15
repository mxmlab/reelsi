# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания CH: строки субтитров (группировка, перенос на 2 строки, .jsx, .srt)."""
import gzip
import os
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import subs  # noqa: E402
from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_build_sub_rows_basic():
    words = [
        (0, 30, "ПРИВЕТ"),
        (30, 60, "ЭТО"),
        (60, 90, "ТЕСТ"),
        (90, 120, "СУБТИТРОВ"),
        (120, 150, "НА"),
        (150, 180, "СТРОКИ"),
    ]
    # per_row = 1 -> 6 строк
    r1 = subs.build_sub_rows(words, per_row=1)
    assert len(r1) == 6
    assert [r["text"] for r in r1] == ["ПРИВЕТ", "ЭТО", "ТЕСТ", "СУБТИТРОВ", "НА", "СТРОКИ"]

    # per_row = 3 -> 2 строки по 3 слова
    r3 = subs.build_sub_rows(words, per_row=3)
    assert len(r3) == 2
    assert r3[0]["text"] == "ПРИВЕТ ЭТО ТЕСТ"
    assert r3[0]["start"] == 0 and r3[0]["end"] == 90
    assert r3[1]["text"] == "СУБТИТРОВ НА СТРОКИ"
    assert r3[1]["start"] == 90 and r3[1]["end"] == 180


def test_build_sub_rows_cut_bounds():
    words = [
        (0, 30, "РАЗ"),
        (30, 60, "ДВА"),
        (60, 90, "ТРИ"),
        (90, 120, "ЧЕТЫРЕ"),
    ]
    # склейка на 60 кадре: строка не переносится через границу склейки
    r = subs.build_sub_rows(words, per_row=3, cut_bounds=[60])
    assert len(r) == 2
    assert r[0]["text"] == "РАЗ ДВА"
    assert r[1]["text"] == "ТРИ ЧЕТЫРЕ"


def test_srt_formatting_and_writing(tmp_path):
    assert subs.format_srt_time(0.0) == "00:00:00,000"
    assert subs.format_srt_time(1.5) == "00:00:01,500"
    assert subs.format_srt_time(65.123) == "00:01:05,123"
    assert subs.format_srt_time(3661.05) == "01:01:01,050"

    srt_path = str(tmp_path / "test.srt")
    rows = [
        {"s": 0.0, "e": 1.5, "w": "ПЕРВАЯ РЕПЛИКА", "repl": 0},
        {"s": 1.5, "e": 3.2, "w": "ВТОРАЯ РЕПЛИКА", "repl": 1},
    ]
    subs.write_srt(rows, srt_path)
    content = open(srt_path, encoding="utf-8").read()
    assert "1\n00:00:00,000 --> 00:00:01,500\nПЕРВАЯ РЕПЛИКА\n" in content
    assert "2\n00:00:01,500 --> 00:00:03,200\nВТОРАЯ РЕПЛИКА\n" in content


def test_srt_multiline_replica_merging(tmp_path):
    """Строка, разбитая на 2 ряда (общий repl), уходит в SRT одной репликой с переходом строки."""
    srt_path = str(tmp_path / "multiline.srt")
    rows = [
        {"s": 0.0, "e": 2.2, "w": "ТЕСОСТЕРОН ПЛЮС", "repl": 0, "row": 0},
        {"s": 0.0, "e": 2.2, "w": "ОКСАН*РОЛОН", "repl": 0, "row": 1},
        {"s": 2.2, "e": 4.0, "w": "СЛЕДУЮЩАЯ СТРОКА", "repl": 1, "row": 0},
    ]
    subs.write_srt(rows, srt_path)
    content = open(srt_path, encoding="utf-8").read()
    assert "1\n00:00:00,000 --> 00:00:02,200\nТЕСОСТЕРОН ПЛЮС\nОКСАН*РОЛОН\n" in content
    assert "2\n00:00:02,200 --> 00:00:04,000\nСЛЕДУЮЩАЯ СТРОКА\n" in content
    cues = [c.strip() for c in content.strip().split("\n\n") if c.strip()]
    assert len(cues) == 2


def test_write_srt_for(xml_subs, tmp_path):
    """xml2ae.write_srt_for собирает план и сохраняет .srt файл."""
    srt_out = str(tmp_path / "custom.srt")
    res = xml2ae.write_srt_for(xml_subs, srt_out)
    assert res == srt_out
    assert os.path.isfile(srt_out)
    content = open(srt_out, encoding="utf-8").read()
    assert "1\n00:00:" in content


def test_sub_words_per_row_1_identical_output(xml_subs, tmp_path):
    # План с per_row = 1
    p1 = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 1})
    p_def = xml2ae.scene_plan(xml_subs, style={})
    assert p1["subs"] == p_def["subs"]
    assert p1["_ae"]["subs"] == p_def["_ae"]["subs"]
    assert p1["_ae"]["sub_loop"] == p_def["_ae"]["sub_loop"]

    # JSX с per_row = 1 идентичен
    jsx1_path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out1.jsx"),
                                        style={"sub_words_per_row": 1}, emit=lambda *a: None)
    jsx_def_path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out_def.jsx"),
                                            style={}, emit=lambda *a: None)
    c1 = open(jsx1_path, encoding="utf-8-sig").read()
    c_def = open(jsx_def_path, encoding="utf-8-sig").read()
    assert c1 == c_def

    # SRT с per_row = 1 содержит ровно столько же реплик, сколько слов
    srt1_path = str(tmp_path / "out1.srt")
    cues = [c.strip() for c in open(srt1_path, encoding="utf-8").read().strip().split("\n\n") if c.strip()]
    assert len(cues) == len(p1["subs"])


def test_sub_words_per_row_3_on_real_xml(xml_subs, tmp_path):
    p_w1 = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 1})
    p_w3 = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 3})

    words_count = len(p_w1["subs"])
    rows_count = len(p_w3["subs"])
    assert words_count > 10
    assert rows_count < words_count
    # Строк примерно втрое меньше слов
    assert abs(rows_count - words_count / 3) <= 4

    # При per_row = 3 интро отключено
    assert p_w3["intro"] == []
    assert p_w3["_ae"]["intro_groups"] == "[]"

    # Проверка JSX через verify_jsx и выгрузка .srt
    jsx_path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "out3.jsx"),
                                       style={"sub_words_per_row": 3}, emit=lambda *a: None)
    rep = verify_jsx.Report(jsx_path)
    jsx_code = open(jsx_path, encoding="utf-8-sig").read()
    verify_jsx.check_syntax(jsx_path, jsx_code, rep)
    verify_jsx.check_undeclared(jsx_code, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"

    # Проверка .srt файла
    srt_file = str(tmp_path / "out3.srt")
    assert os.path.exists(srt_file)
    srt_text = open(srt_file, encoding="utf-8").read()
    cues = [c.strip() for c in srt_text.strip().split("\n\n") if c.strip()]
    # При sub_rows_max=1 по умолчанию число реплик равно числу строк плана
    assert len(cues) == rows_count


def test_build_sub_rows_words_per_row_and_max_rows():
    """Задание CJ: per_row — слов в ОДНОЙ строке, max_rows — строк в реплике."""
    words = [(i * 30, (i + 1) * 30, f"W{i}") for i in range(15)]
    # per_row=6, max_rows=2 -> 15 слов = реплика 0 (12 слов: ряд 0 (6), ряд 1 (6)) + реплика 1 (3 слова: ряд 0 (3))
    res = subs.build_sub_rows(words, per_row=6, max_rows=2)
    assert len(res) == 3
    # Первая реплика
    assert res[0]["repl"] == 0 and res[0]["row"] == 0
    assert len(res[0]["words"]) == 6
    assert res[0]["start"] == 0 and res[0]["end"] == 360
    assert res[1]["repl"] == 0 and res[1]["row"] == 1
    assert len(res[1]["words"]) == 6
    assert res[1]["start"] == 0 and res[1]["end"] == 360
    # Вторая реплика
    assert res[2]["repl"] == 1 and res[2]["row"] == 0
    assert len(res[2]["words"]) == 3
    assert res[2]["start"] == 360 and res[2]["end"] == 450


def test_two_rows_wrapping_vs_font_shrinking(xml_subs, tmp_path):
    # Тест двухстрочного переноса при sub_rows_max = 2 vs 1 на длинном тексте (задание CJ)
    meta, cams, subs_list, xml_inserts = xml2ae.parse_full(xml_subs)
    st_wrap2 = {"sub_words_per_row": 6, "sub_rows_max": 2}
    st_wrap1 = {"sub_words_per_row": 6, "sub_rows_max": 1}

    p_wrap2 = xml2ae.scene_plan(xml_subs, style=st_wrap2)
    p_wrap1 = xml2ae.scene_plan(xml_subs, style=st_wrap1)

    # В p_wrap2 есть строки с row 0 и row 1, в p_wrap1 — все строки row 0
    assert any(s.get("row") == 1 for s in p_wrap2["subs"])
    assert all(s.get("row") == 0 for s in p_wrap1["subs"])

    # Ни одна строка плана не шире 0.92 кадра
    from core import fonts as _fonts
    max_w = 0.92 * meta["w"]
    for s in p_wrap2["subs"]:
        fs = s.get("fsize", p_wrap2["fsize"])
        tw = _fonts.text_width("SFPro-CondensedSemibold", s["w"], fs)
        if tw is None:
            pytest.skip("нет шрифта SFPro-CondensedSemibold")
        assert tw <= max_w + 1.0, f"Line too wide: {s['w']} ({tw} > {max_w})"

    for s in p_wrap1["subs"]:
        fs = s.get("fsize", p_wrap1["fsize"])
        tw = _fonts.text_width("SFPro-CondensedSemibold", s["w"], fs)
        if tw is None:
            pytest.skip("нет шрифта SFPro-CondensedSemibold")
        assert tw <= max_w + 1.0, f"Line too wide: {s['w']} ({tw} > {max_w})"

    # sub_step в плане равен 1.18 * fsize, у отдельных строк fsize и sub_step отсутствуют (задание CK)
    assert "sub_step" in p_wrap2
    assert p_wrap2["sub_step"] == round(p_wrap2["fsize"] * 1.18, 2)
    for s in p_wrap2["subs"]:
        assert "fsize" not in s
        assert "sub_step" not in s

    # SRT: при wrap2 число реплик равно числу реплик (а не строк плана), двухстрочные титры объединены через \n
    srt2_path = str(tmp_path / "wrap2.srt")
    subs.write_srt(p_wrap2["subs"], srt2_path)
    srt2_text = open(srt2_path, encoding="utf-8").read()
    cues2 = [c.strip() for c in srt2_text.strip().split("\n\n") if c.strip()]

    # Уникальных repl в p_wrap2:
    unique_repls = len(set(s["repl"] for s in p_wrap2["subs"]))
    assert len(cues2) == unique_repls
    assert len(cues2) < len(p_wrap2["subs"])
    assert any("\n" in cue.split("\n", 2)[2] for cue in cues2)

    # При wrap1 число реплик равно числу строк плана
    srt1_path = str(tmp_path / "wrap1.srt")
    subs.write_srt(p_wrap1["subs"], srt1_path)
    srt1_text = open(srt1_path, encoding="utf-8").read()
    cues1 = [c.strip() for c in srt1_text.strip().split("\n\n") if c.strip()]
    assert len(cues1) == len(p_wrap1["subs"])


def test_build_sub_rows_multiword_elements():
    """Элементы со словами через пробел разворачиваются: не более per_row слов в строке."""
    words = [
        (0, 60, "СОЛО ОКС*НДРОЛОН"),  # 2 слова в одном элементе
        (60, 90, "ЛИБО"),
        (90, 120, "ТЕСО*ТЕРОН"),
        (120, 150, "С"),
        (150, 180, "ОКСАДОЙ"),
        (180, 210, "ОНА"),
    ]
    # per_row = 6, max_rows = 2
    res = subs.build_sub_rows(words, per_row=6, max_rows=2)
    assert len(res) == 2
    assert res[0]["text"] == "СОЛО ОКС*НДРОЛОН ЛИБО ТЕСО*ТЕРОН С ОКСАДОЙ"
    assert len(res[0]["text"].split()) == 6
    assert res[1]["text"] == "ОНА"
    assert len(res[1]["text"].split()) == 1
    # Проверяем тайминги и слова
    assert len(res[0]["words"]) == 6
    assert res[0]["words"][0]["w"] == "СОЛО"
    assert res[0]["words"][1]["w"] == "ОКС*НДРОЛОН"
    assert res[0]["words"][0]["idx"] == 0 and res[0]["words"][1]["idx"] == 0


def test_build_py_contains_no_extendscript():
    """В xml2ae/build.py не должно быть строк ExtendScript кода субтитров (subc.layers.addText, sourceRectAtTime)."""
    build_py_path = os.path.join(ROOT, "core", "xml2ae", "build.py")
    code = open(build_py_path, encoding="utf-8").read()
    assert "subc.layers.addText" not in code
    assert "sourceRectAtTime" not in code


def test_inserts_view_no_w_shadowing():
    """В static/app/85-inserts-view.js параметр стрелки не должен перекрывать w (ширину кадра)."""
    js_path = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
    code = open(js_path, encoding="utf-8").read()
    assert "w.fsize/w" not in code
    # Проверяем функцию ipvSubs
    assert "function ipvSubs" in code
    assert "vis.map(w=>" not in code and "vis.map(w =>" not in code


def test_single_fsize_and_step_per_video(xml_subs):
    """Задание CK: кегль один на ролик, шаг один на ролик, у строк нет полей fsize и sub_step."""
    meta, cams, subs_list, xml_inserts = xml2ae.parse_full(xml_subs)
    from core import fonts as _fonts
    max_w = 0.92 * meta["w"]

    for w_count in [2, 3, 4, 6]:
        plan = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": w_count, "sub_rows_max": 2})
        assert "fsize" in plan
        assert "sub_step" in plan
        assert plan["sub_step"] == round(plan["fsize"] * 1.18, 2)
        assert plan["_ae"]["fsize"] == plan["fsize"]

        # У отдельных строк полей fsize и sub_step нет
        for s in plan["subs"]:
            assert "fsize" not in s
            assert "sub_step" not in s
            tw = _fonts.text_width("SFPro-CondensedSemibold", s["w"], plan["fsize"])
            if tw is None:
                pytest.skip("нет шрифта SFPro-CondensedSemibold")
            assert tw <= max_w + 1.0, f"Line too wide at w={w_count}: {s['w']} ({tw} > {max_w})"


def test_font_change_changes_fsize(xml_subs):
    """Задание CK: смена шрифта в стиле меняет кегль."""
    from core import fonts as _fonts
    if not (_fonts.text_width("SFPro-CondensedSemibold", "ТЕСТ", 140) and _fonts.text_width("ArialMT", "ТЕСТ", 140)):
        pytest.skip("требуются установленные шрифты SFPro-CondensedSemibold и ArialMT")
    p_cond = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 3, "font": "SFPro-CondensedSemibold"})
    p_arial = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 3, "font": "ArialMT"})
    assert p_cond["fsize"] != p_arial["fsize"]
    # Arial шире Condensed, поэтому кегль для Arial меньше
    assert p_arial["fsize"] < p_cond["fsize"]


def test_missing_font_leaves_default_fsize(xml_subs):
    """Задание CK: шрифт не найден в системе — кегль не трогаем (как в автофите интро)."""
    p_missing = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 3, "font": "NonExistentFont12345"})
    # Дефолтный fsize = 140 (1080 * 0.13)
    assert p_missing["fsize"] == 140


def test_preview_css_no_padding_and_nowrap():
    """Задание CK: в app.css у .pvsubw убран боковой паддинг и добавлен white-space:nowrap."""
    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()
    assert ".pvsubw" in css
    assert "padding:0;white-space:nowrap" in css


def test_hl_font_word_width_measurement(xml_subs):
    """Задание CK: жёлтое слово меряется своим шрифтом (hl_font)."""
    # SFPro-CondensedSemibold (узкий) vs ArialMT (широкий hl_font)
    # Если слово выделено жёлтым и hl_font широкий, требуемый кегль уменьшается
    p_no_hl = xml2ae.scene_plan(xml_subs, highlights=[],
                                style={"sub_words_per_row": 3, "font": "SFPro-CondensedSemibold", "hl_font": "ArialMT"})
    p_with_hl = xml2ae.scene_plan(xml_subs, highlights=list(range(50)),
                                  style={"sub_words_per_row": 3, "font": "SFPro-CondensedSemibold", "hl_font": "ArialMT"})
    # При выделении слов широким hl_font кегль становится меньше или равен кеглю без hl
    assert p_with_hl["fsize"] <= p_no_hl["fsize"]


def test_preview_rows_no_vertical_intersection(xml_subs):
    """Задание CK: при 4 словах в строке два ряда не пересекаются."""
    plan = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 4, "sub_rows_max": 2})
    posy = plan["posy"]
    sub_step = plan["sub_step"]
    fsize = plan["fsize"]

    # Ряд 0: низ = posy, верх = posy - fsize
    r0_bot = posy
    r0_top = posy - fsize

    # Ряд 1: низ = posy + sub_step, верх = posy + sub_step - fsize
    r1_bot = posy + sub_step
    r1_top = posy + sub_step - fsize

    assert r0_top < r0_bot
    assert r1_top < r1_bot
    # Верх ряда 1 строго ниже низа ряда 0 (нет перекрытия)
    assert r1_top > r0_bot
    assert r1_top - r0_bot > 0


def test_single_word_sub_shrinking(xml_subs):
    """Задание CK (возврат): в режиме 1 слова длинные слова ужимаются по кеглю для превью."""
    from core import fonts as _fonts
    if _fonts.text_width("SFPro-CondensedSemibold", "ТЕСТ", 140) is None:
        pytest.skip("требуется установленный шрифт SFPro-CondensedSemibold")
    meta, cams, subs_list, xml_inserts = xml2ae.parse_full(xml_subs)
    max_w = 0.92 * meta["w"]

    plan = xml2ae.scene_plan(xml_subs, style={"sub_words_per_row": 1})
    # Ни одна строка не шире 0.92 кадра
    for s in plan["subs"]:
        fs = s.get("fsize", plan["fsize"])
        tw = _fonts.text_width("SFPro-CondensedSemibold", s["w"], fs)
        if tw is None:
            pytest.skip("нет шрифта SFPro-CondensedSemibold")
        assert tw <= max_w + 1.0, f"Single word too wide: {s['w']} ({tw} > {max_w})"

    # Проверяем, что длинные слова действительно получили индивидуальный fsize < 140
    shrunk_subs = [s for s in plan["subs"] if "fsize" in s]
    assert len(shrunk_subs) > 0
    for s in shrunk_subs:
        assert s["fsize"] < plan["fsize"]
        assert s["fsize"] >= 40


def test_build_sub_rows_pause_breaking_and_orphan_handling():
    """Задание CR: разрыв строк по паузам >= 0.30с и запрет сирот (1-словных строк)."""
    words = [
        (0, 30, "РАЗ"),
        (30, 60, "ДВА"),
        (60, 90, "ТРИ"),
        (90, 120, "ЧЕТЫРЕ"),
        (120, 150, "ПЯТЬ"),
        (150, 180, "ШЕСТЬ"),
    ]
    # Пауза 0.35с между словом 2 ("ТРИ") и словом 3 ("ЧЕТЫРЕ")
    word_timings = [
        {"w": "РАЗ", "start": 0.0, "end": 0.5},
        {"w": "ДВА", "start": 0.5, "end": 1.0},
        {"w": "ТРИ", "start": 1.0, "end": 1.5},
        {"w": "ЧЕТЫРЕ", "start": 1.85, "end": 2.35},  # gap 0.35s >= 0.30s
        {"w": "ПЯТЬ", "start": 2.35, "end": 2.85},
        {"w": "ШЕСТЬ", "start": 2.85, "end": 3.35},
    ]
    # per_row = 4: без пауз было бы 4 + 2. С паузой: 3 ("РАЗ ДВА ТРИ") + 3 ("ЧЕТЫРЕ ПЯТЬ ШЕСТЬ")
    r = subs.build_sub_rows(words, per_row=4, word_timings=word_timings)
    assert len(r) == 2
    assert r[0]["text"] == "РАЗ ДВА ТРИ"
    assert r[1]["text"] == "ЧЕТЫРЕ ПЯТЬ ШЕСТЬ"

    # Запрет сирот: при раннем разрыве по паузе, если следующей строке остаётся 1 слово, разрыв переносится назад
    # 5 слов, пауза после 4-го слова ("ЧЕТЫРЕ") оставила бы 5-е слово ("ПЯТЬ") сиротой -> переносится на 3-е слово
    word_timings_orphan = [
        {"w": "РАЗ", "start": 0.0, "end": 0.5},
        {"w": "ДВА", "start": 0.5, "end": 1.0},
        {"w": "ТРИ", "start": 1.0, "end": 1.5},
        {"w": "ЧЕТЫРЕ", "start": 1.5, "end": 2.0},
        {"w": "ПЯТЬ", "start": 2.35, "end": 2.85},  # gap 0.35s >= 0.30s
    ]
    r5 = subs.build_sub_rows(words[:5], per_row=4, word_timings=word_timings_orphan)
    assert len(r5) == 2
    assert r5[0]["text"] == "РАЗ ДВА ТРИ"
    assert r5[1]["text"] == "ЧЕТЫРЕ ПЯТЬ"
    assert all(len(x["words"]) >= 2 for x in r5)


def test_single_yellow_word_in_sub_row(xml_subs):
    """Задание CR: в превью и плане жёлтым красится только выделенное слово, а не вся строка."""
    plan = xml2ae.scene_plan(xml_subs, highlights=[1], style={"sub_words_per_row": 3})
    # Находим строку, содержащую выделенное слово (idx == 1)
    hl_row = None
    for sub in plan["subs"]:
        if any(w.get("color") == "yellow" for w in sub.get("words", [])):
            hl_row = sub
            break
    assert hl_row is not None
    # Цвет строки — белый, а не жёлтый (выделено 1 слово из 3)
    assert hl_row["color"] == "white"
    # Ровно одно слово имеет color == "yellow"
    yellow_words = [w for w in hl_row["words"] if w.get("color") == "yellow"]
    white_words = [w for w in hl_row["words"] if w.get("color") == "white"]
    assert len(yellow_words) == 1
    assert len(white_words) == len(hl_row["words"]) - 1


def test_cv_hl_fill_in_plan(xml_subs):
    """Задание CV часть 1: hl_fill отдаётся в плане для установки --subhl в превью."""
    # По умолчанию — жёлтый [1, 0.9176, 0]
    p1 = xml2ae.scene_plan(xml_subs, style={})
    assert "hl_fill" in p1
    assert p1["hl_fill"] == [1, 0.9176, 0]

    # Свой цвет выделения из стиля
    custom_hl = [0.8, 0.2, 0.9]
    p2 = xml2ae.scene_plan(xml_subs, style={"hl_fill": custom_hl})
    assert p2["hl_fill"] == custom_hl


def test_cv_sub_rows_punctuation_breaking():
    """Задание CV часть 2: разбиение строк по знакам препинания (точки, запятые)."""
    words = [
        (0, 30, "ПЕРВОЕ"),
        (30, 60, "ПРЕДЛОЖЕНИЕ"),
        (60, 90, "ВТОРОЕ"),
        (90, 120, "ДЛИННОЕ"),
        (120, 150, "ПРЕДЛОЖЕНИЕ"),
        (150, 180, "И"),
        (180, 210, "ЕЩЕ"),
        (210, 240, "СЛОВО"),
    ]
    # Точка после слова 1 ("ПРЕДЛОЖЕНИЕ.") -> закрывает строку всегда
    # Запятая после слова 4 ("ПРЕДЛОЖЕНИЕ,") -> закрывает строку (len >= 4/2 = 2)
    word_timings = [
        {"w": "первое", "start": 0.0, "end": 0.5},
        {"w": "предложение.", "start": 0.5, "end": 1.0},
        {"w": "второе", "start": 1.0, "end": 1.5},
        {"w": "длинное", "start": 1.5, "end": 2.0},
        {"w": "предложение,", "start": 2.0, "end": 2.5},
        {"w": "и", "start": 2.5, "end": 3.0},
        {"w": "еще", "start": 3.0, "end": 3.5},
        {"w": "слово.", "start": 3.5, "end": 4.0},
    ]
    rows = subs.build_sub_rows(words, per_row=4, word_timings=word_timings)
    assert len(rows) == 3
    assert rows[0]["text"] == "ПЕРВОЕ ПРЕДЛОЖЕНИЕ"
    assert rows[1]["text"] == "ВТОРОЕ ДЛИННОЕ ПРЕДЛОЖЕНИЕ"
    assert rows[2]["text"] == "И ЕЩЕ СЛОВО"

    # Проверяем, что в результирующий текст титра знаки не попадают
    for r in rows:
        for w in r["words"]:
            assert not any(w["w"].endswith(p) for p in ('.', '!', '?', ',', ';', ':', '—'))


def test_cv_comma_under_half_does_not_break_early():
    """Задание CV: запятая на первом слове (< per_row/2) не должна создавать строку из 1 слова."""
    words = [
        (0, 30, "ДА"),
        (30, 60, "СЕЙЧАС"),
        (60, 90, "ЕСТЬ"),
        (90, 120, "ПРЕПАРАТЫ"),
    ]
    word_timings = [
        {"w": "Да,", "start": 0.0, "end": 0.5},
        {"w": "сейчас", "start": 0.5, "end": 1.0},
        {"w": "есть", "start": 1.0, "end": 1.5},
        {"w": "препараты.", "start": 1.5, "end": 2.0},
    ]
    # per_row = 4: 'Да,' имеет запятую, но len(cur)=1 < 2 -> не рвёт, получается 1 строка из 4 слов
    rows = subs.build_sub_rows(words, per_row=4, word_timings=word_timings)
    assert len(rows) == 1
    assert rows[0]["text"] == "ДА СЕЙЧАС ЕСТЬ ПРЕПАРАТЫ"
def test_cy_preview_font_weight_not_hardcoded():
    """Задание CY: в app.css у .pvsub, .pvsubw и .iline убран font-weight:800 (вес идёт из вариативной оси/шрифта).
    Если шрифт не найден в системе, fallback 800 ставится в JS (fvCss / ipvIntro)."""
    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()
    import re
    # .pvsub { ... }
    pvsub_m = re.search(r"\.pvsub\s*\{([^}]+)\}", css)
    assert pvsub_m, "селектор .pvsub не найден в app.css"
    assert "font-weight" not in pvsub_m.group(1), ".pvsub не должен иметь зашитый font-weight"

    # #ipvsub .pvsubw { ... }
    pvsubw_m = re.search(r"#ipvsub\s+\.pvsubw\s*\{([^}]+)\}", css)
    assert pvsubw_m, "селектор #ipvsub .pvsubw не найден в app.css"
    assert "font-weight" not in pvsubw_m.group(1), "#ipvsub .pvsubw не должен иметь зашитый font-weight"

    # .ipvintro .iline { ... }
    iline_m = re.search(r"\.ipvintro\s+\.iline\s*\{([^}]+)\}", css)
    assert iline_m, "селектор .ipvintro .iline не найден в app.css"
    assert "font-weight" not in iline_m.group(1), ".ipvintro .iline не должен иметь зашитый font-weight"

    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"), encoding="utf-8").read()
    assert "if(!fv)return 'font-weight:800;';" in js, "fvCss не ставит fallback font-weight:800 при отсутствии fv"
    assert "else{dv.style.fontWeight='800';}" in js or "else{dv.style.fontWeight=\"800\";}" in js, (
        "ipvIntro не ставит fallback fontWeight='800' при отсутствии fv"
    )

