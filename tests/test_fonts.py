# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""fonts.py — список установленных шрифтов для превью интро.

Смысл теста: вариативный шрифт перечисляется по именованным экземплярам fvar, а не
только по nameID 6. Без этого превью не видит `SFPro-CondensedSemibold`, который AE
показывает, — строка интро рисуется дефолтной шириной, и человек догоняет масштабом
(140% из жалобы BO). Шрифт собирается программно: физический файл в репозиторий не
кладём, машина теста от системных шрифтов не зависит.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import fonts  # noqa: E402


def _build_var_font(tmp_path, postscript_nameid=0xFFFF):
    """Собрать крошечный вариативный TTFont (name + fvar), сохранить в tmp_path.

    postscript_nameid=0xFFFF — как у Bahnschrift/SegUIVar/Inter: имени экземпляра в
    name нет, Windows/AE собирают его канонически. При заполненном nameID 6 вернётся
    прямое имя. Возвращает (путь, оси_дефолта).
    """
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._f_v_a_r import Axis, NamedInstance, table__f_v_a_r
    from fontTools.ttLib.tables._n_a_m_e import NameRecord, table__n_a_m_e

    def rec(nameID, text):
        r = NameRecord()
        r.nameID = nameID
        r.platformID = 3
        r.platEncID = 1
        r.langID = 0x409
        r.string = text.encode("utf-16-be")
        r.text = text
        return r

    nm = table__n_a_m_e()
    nm.names = [
        rec(1, "TestSans"),
        rec(6, "TestSans-Regular"),
        rec(16, "Test Sans"),
        rec(17, "Semibold"),
        rec(18, "Semibold"),
        rec(19, "TestSans-Semibold"),   # postscriptNameID экземпляра, если не 0xFFFF
    ]
    fvar = table__f_v_a_r()
    a = Axis()
    a.axisTag = "wght"
    a.minValue = 100
    a.defaultValue = 400
    a.maxValue = 900
    a.axisNameID = 256
    i = NamedInstance()
    i.subfamilyNameID = 17
    i.postscriptNameID = postscript_nameid
    i.coordinates = {"wght": 650}
    fvar.axes = [a]
    fvar.instances = [i]

    f = TTFont()
    f["name"] = nm
    f["fvar"] = fvar
    path = tmp_path / "TestSans.ttf"
    f.save(str(path))
    return path, {a.axisTag: a.defaultValue for a in fvar.axes}


def test_variable_font_lists_default_and_instance(tmp_path, monkeypatch):
    """Вариативный шрифт даёт и дефолт (nameID 6), и именованный экземпляр fvar —
    именно его показывает AE, а превью не видит. У экземпляра непустой `var`."""
    path, default_coords = _build_var_font(tmp_path, postscript_nameid=19)
    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fs = fonts.list_fonts(refresh=True)

    by_ps = {x["ps"]: x for x in fs}
    assert set(by_ps) == {"TestSans-Regular", "TestSans-Semibold"}, \
        f"должны быть дефолт и экземпляр, пришло: {sorted(by_ps)}"
    assert by_ps["TestSans-Regular"]["var"] == default_coords, \
        "дефолт вариативного файла обязан нести координаты по умолчанию"
    assert by_ps["TestSans-Semibold"]["var"] == {"wght": 650}, \
        "экземпляр несёт координаты своих осей"
    assert by_ps["TestSans-Semibold"]["file"] == str(path), \
        "поле file нужно заданию BP для замера ширины строки"


def test_instance_without_postscript_name_is_built_canonically(tmp_path, monkeypatch):
    """postscriptNameID пуст (Bahnschrift, SegUIVar, Inter): имя собирается так же,
    как Windows/AE, — дефолт до дефиса + '-' + subfamily без пробелов."""
    _build_var_font(tmp_path, postscript_nameid=0xFFFF)
    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fs = fonts.list_fonts(refresh=True)
    by_ps = {x["ps"]: x for x in fs}
    assert "TestSans-Semibold" in by_ps, f"каноническое имя не собрано: {sorted(by_ps)}"


def test_static_font_has_no_var_and_is_not_duplicated(tmp_path, monkeypatch):
    """Файл без fvar — одна запись без `var`, как было до BO. Два имени из одного
    файла дублями не размножаются (дедупликация по ps в list_fonts)."""
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._n_a_m_e import NameRecord, table__n_a_m_e

    def rec(nameID, text):
        r = NameRecord()
        r.nameID = nameID
        r.platformID = 3
        r.platEncID = 1
        r.langID = 0x409
        r.string = text.encode("utf-16-be")
        r.text = text
        return r

    nm = table__n_a_m_e()
    nm.names = [rec(1, "TestSans"), rec(6, "TestSans-Regular"), rec(16, "Test Sans")]
    f = TTFont()
    f["name"] = nm
    f.save(str(tmp_path / "TestSans.ttf"))
    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fs = fonts.list_fonts(refresh=True)
    assert [x["ps"] for x in fs] == ["TestSans-Regular"]
    assert "var" not in fs[0]
    assert fs[0]["weight"] == 400
    assert fs[0]["stretch"] == 100
    assert fs[0]["italic"] is False


def test_static_font_extracts_os2_weight_stretch_italic():
    """Задание DA: обычный шрифт берёт weight, stretch, italic из таблицы OS/2."""
    from fontTools.ttLib.tables._n_a_m_e import NameRecord, table__n_a_m_e

    def rec(nameID, text):
        r = NameRecord()
        r.nameID = nameID
        r.platformID = 3
        r.platEncID = 1
        r.langID = 0x409
        r.string = text.encode("utf-16-be")
        r.text = text
        return r

    nm = table__n_a_m_e()
    nm.names = [
        rec(1, "Roboto Condensed"),
        rec(2, "Bold Italic"),
        rec(6, "Roboto-BoldCondensedItalic"),
        rec(16, "Roboto"),
        rec(17, "Bold Condensed Italic"),
    ]

    class FakeOS2:
        usWeightClass = 700
        usWidthClass = 3  # 75% condensed
        fsSelection = 0x01  # italic

    tt = {"name": nm, "OS/2": FakeOS2()}
    recs = fonts._names(tt, "C:/fake/Roboto.ttf")
    assert len(recs) == 1
    assert recs[0]["ps"] == "Roboto-BoldCondensedItalic"
    assert recs[0]["family"] == "Roboto"
    assert recs[0]["weight"] == 700
    assert recs[0]["stretch"] == 75
    assert recs[0]["italic"] is True
    assert "var" not in recs[0]


def test_static_font_fallback_style_from_name(tmp_path, monkeypatch):
    """Задание DA: если OS/2 отсутствует, начертание восстанавливается по имени."""
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._n_a_m_e import NameRecord, table__n_a_m_e

    def rec(nameID, text):
        r = NameRecord()
        r.nameID = nameID
        r.platformID = 3
        r.platEncID = 1
        r.langID = 0x409
        r.string = text.encode("utf-16-be")
        r.text = text
        return r

    nm = table__n_a_m_e()
    nm.names = [
        rec(1, "Tahoma"),
        rec(2, "Bold"),
        rec(6, "Tahoma-Bold"),
    ]
    f = TTFont()
    f["name"] = nm
    path = tmp_path / "Tahoma-Bold.ttf"
    f.save(str(path))

    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fs = fonts.list_fonts(refresh=True)
    assert len(fs) == 1
    assert fs[0]["ps"] == "Tahoma-Bold"
    assert fs[0]["weight"] == 700
    assert fs[0]["stretch"] == 100
    assert fs[0]["italic"] is False


def test_api_fontfile_serves_font_file(tmp_path, monkeypatch):
    """Задание DB: /api/fontfile/<ps> отдаёт файл шрифта по PostScript-имени из list_fonts."""
    from flask import Flask
    import api
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._n_a_m_e import NameRecord, table__n_a_m_e

    def rec(nameID, text):
        r = NameRecord()
        r.nameID = nameID
        r.platformID = 3
        r.platEncID = 1
        r.langID = 0x409
        r.string = text.encode("utf-16-be")
        r.text = text
        return r

    nm = table__n_a_m_e()
    nm.names = [
        rec(1, "TestFont"),
        rec(2, "Regular"),
        rec(6, "TestFont-Regular"),
    ]
    f = TTFont()
    f["name"] = nm
    font_path = tmp_path / "TestFont-Regular.ttf"
    f.save(str(font_path))

    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    r = client.get("/api/fontfile/TestFont-Regular", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert r.content_type.startswith("font/ttf")
    assert r.data == font_path.read_bytes()


def test_api_fontfile_rejects_arbitrary_paths_and_missing_fonts(tmp_path, monkeypatch):
    """Задание DB: /api/fontfile/ отдаёт только шрифты из таблицы, путь в запросе игнорируется."""
    from flask import Flask
    import api

    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    # Несуществующий шрифт -> 404
    r = client.get("/api/fontfile/NonExistent-Font", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 404

    # Попытка path traversal -> 404 (нет такого PS-имени в таблице)
    r = client.get("/api/fontfile/../../secret", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 404

    r = client.get("/api/fontfile/C:/Windows/System32/cmd.exe", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 404


def test_api_fontfile_refuses_forbidden_files(tmp_path, monkeypatch):
    """Задание DB: даже если в таблице файл конфига, _never_serve блокирует (403)."""
    from flask import Flask
    import api

    secret_file = tmp_path / "ai_config.json"
    secret_file.write_text("{}", encoding="utf-8")

    fake_fonts = [{
        "ps": "SecretFont",
        "family": "Secret",
        "file": str(secret_file),
    }]
    monkeypatch.setattr(fonts, "list_fonts", lambda refresh=False: fake_fonts)

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    r = client.get("/api/fontfile/SecretFont", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403


def test_fonts_recursive_search_and_cross_platform_dirs(tmp_path, monkeypatch):
    """Задание HV: шрифты во вложенных папках находятся рекурсивно, _FONT_DIRS знает macOS и Linux."""
    # 1. Проверяем наличие путей macOS и Linux в _FONT_DIRS
    expected_mac = ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    expected_linux = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts"), os.path.expanduser("~/.fonts")]
    for p in expected_mac + expected_linux:
        assert p in fonts._FONT_DIRS, f"Путь {p} отсутствует в _FONT_DIRS"

    # 2. Рекурсивный обход вложенной папки
    nested = tmp_path / "nested" / "subdir"
    nested.mkdir(parents=True)
    _build_var_font(nested, postscript_nameid=19)

    monkeypatch.setattr(fonts, "_CACHE", None)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fs = fonts.list_fonts(refresh=True)
    by_ps = {x["ps"]: x for x in fs}
    assert "TestSans-Regular" in by_ps, f"Шрифт из вложенной папки не найден: {list(by_ps.keys())}"
