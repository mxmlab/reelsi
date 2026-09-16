# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IF (круг 8): атомарная запись XML Премьера и проектов DaVinci Resolve (.drp).

1. `core/xmlbuild.py:450` писал основной XML Премьера прямым `open(..., "w")` с усечением.
2. `core/fileio.py` не имел `atomic_bytes_write`, а `core/drp.py:write` открывал
   `zipfile.ZipFile(path, "w")` прямо на целевом файле.
3. Между тестами `conftest.py` не сбрасывал флаг `running` у глобальных словарей джобов
   (`JOB`, `RJOB`, `GDJOB`, `VJOB`, `PXJOB`, `ILL_JOB`), из-за чего порядок запуска
   `test_render.py` -> `test_r8_ic_api.py` падал.

Запуск: py -3.10 -m pytest tests/test_r8_if_atomic_drp.py -q -p no:cacheprovider
"""
import importlib
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import drp, fileio, xmlbuild  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="права POSIX на Windows не проверить")


def test_прямой_записи_в_xmlbuild_и_drp_нет():
    """Сторож: в core/xmlbuild.py и core/drp.py нет прямых записей на целевой файл."""
    xmlbuild_src = (ROOT / "core" / "xmlbuild.py").read_text(encoding="utf-8")
    assert 'open(out_path, "w", encoding="UTF-8").write(seq)' not in xmlbuild_src, \
        "в xmlbuild.py осталась прямая запись open(out_path, 'w', ...)"

    drp_src = (ROOT / "core" / "drp.py").read_text(encoding="utf-8")
    assert 'zipfile.ZipFile(path, "w"' not in drp_src, \
        "в drp.py осталась прямая запись ZipFile(path, 'w', ...)"


def test_atomic_bytes_write_содержимое_совпадает(tmp_path):
    """Байты пишутся без искажений, временные файлы .tmp. не остаются."""
    data = b"PK\x03\x04\x14\x00\x00\x00some binary \x00\xff data \r\n"
    target = tmp_path / "test.bin"
    fileio.atomic_bytes_write(str(target), data)
    assert target.read_bytes() == data
    assert [p.name for p in tmp_path.iterdir() if ".tmp." in p.name] == []


@posix_only
def test_atomic_bytes_write_права_существующего_файла_сохраняются(tmp_path):
    """0644 и 0664 переживают атомарную запись байтов."""
    for mode in (0o644, 0o664):
        p = tmp_path / f"m{mode:o}.bin"
        p.write_bytes(b"initial")
        os.chmod(p, mode)
        fileio.atomic_bytes_write(str(p), b"updated")
        assert stat.S_IMODE(p.stat().st_mode) == mode, \
            f"права {mode:o} не сохранились: {stat.S_IMODE(p.stat().st_mode):o}"


@posix_only
def test_atomic_bytes_write_права_нового_файла_по_umask(tmp_path):
    """У нового файла права 0666 & ~umask."""
    old_umask = os.umask(0o027)
    try:
        importlib.reload(fileio)
        p = tmp_path / "new.bin"
        fileio.atomic_bytes_write(str(p), b"data")
        assert stat.S_IMODE(p.stat().st_mode) == 0o640, \
            f"новый файл не по umask: {stat.S_IMODE(p.stat().st_mode):o}"
    finally:
        os.umask(old_umask)
        importlib.reload(fileio)


def test_atomic_bytes_write_через_ссылку_меняет_цель(tmp_path):
    """Запись через симлинк меняет цель, а не подменяет ссылку."""
    target = tmp_path / "real.bin"
    target.write_bytes(b"old data")
    link = tmp_path / "link.bin"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("симлинки в этом окружении не создаются")
    fileio.atomic_bytes_write(str(link), b"new data")
    assert os.path.islink(link), "симлинк был заменён обычным файлом"
    assert target.read_bytes() == b"new data", "цель симлинка не обновлена"


def test_atomic_bytes_write_сбой_не_трогает_старый_файл(tmp_path, monkeypatch):
    """Сбой при подмене файла оставляет старые данные, tmp убирается."""
    p = tmp_path / "state.bin"
    p.write_bytes(b"old data")

    def boom(*a, **k):
        raise OSError("сбой на подмене файла")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        fileio.atomic_bytes_write(str(p), b"new data")
    assert p.read_bytes() == b"old data", "старый файл затёрт"
    assert [x.name for x in tmp_path.iterdir()] == ["state.bin"], "временный файл остался"


def test_drp_write_и_read_сохраняют_содержимое_и_файлы(tmp_path):
    """drp.write собирает валидный ZIP-архив, drp.read читает те же файлы побайтово."""
    files = {
        "project.xml": b"<Project><ProjectName>Test</ProjectName></Project>",
        "MediaPool/Master/MpFolder.xml": b"<MpFolder><Items/></MpFolder>",
        "SeqContainer/uuid.xml": b"<Sequence/>",
        "binary_blob.dat": b"\x00\x01\x02\x03\x04\xff\xfe\xfd",
    }
    drp_path = tmp_path / "project.drp"
    drp.write(str(drp_path), files)

    assert zipfile.is_zipfile(str(drp_path)), "выходной файл не является zip-архивом"
    read_back = drp.read(str(drp_path))
    assert read_back == files, "файлы после чтения не совпадают с записанными"
    assert [p.name for p in tmp_path.iterdir() if ".tmp." in p.name] == []


def test_drp_write_через_ссылку_меняет_цель(tmp_path):
    """drp.write через симлинк пишет в цель ссылки."""
    target = tmp_path / "real.drp"
    target.write_bytes(b"empty")
    link = tmp_path / "link.drp"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("симлинки в этом окружении не создаются")

    files = {"a.txt": b"content"}
    drp.write(str(link), files)
    assert os.path.islink(link), "симлинк подменён обычным файлом"
    assert drp.read(str(target)) == files, "цель симлинка не обновлена"


def test_drp_write_сбой_не_трогает_старый_файл(tmp_path, monkeypatch):
    """Сбой при drp.write не повреждает существующий .drp файл."""
    drp_path = tmp_path / "existing.drp"
    old_files = {"old.txt": b"old"}
    drp.write(str(drp_path), old_files)
    original_bytes = drp_path.read_bytes()

    def boom(*a, **k):
        raise OSError("сбой на подмене файла")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        drp.write(str(drp_path), {"new.txt": b"new"})
    assert drp_path.read_bytes() == original_bytes, "старый .drp файл повреждён"
    assert [p.name for p in tmp_path.iterdir()] == ["existing.drp"], "временный файл остался"


def test_xmlbuild_пишет_через_atomic_text_write(tmp_path, monkeypatch):
    """xmlbuild.build использует fileio.atomic_text_write: при сбое старый XML цел."""
    out_xml = tmp_path / "output.xml"
    original_xml = "<old_xml/>"
    out_xml.write_text(original_xml, encoding="utf-8")

    def boom(*a, **k):
        raise OSError("сбой на подмене файла")

    monkeypatch.setattr(fileio.os, "replace", boom)
    monkeypatch.setattr(xmlbuild, "probe",
                        lambda p, still_ok=True: {"width": 1080, "height": 1920, "dur_s": 10.0, "fps": 60, "timecode": "00:00:00:00"})

    segments = [(0.0, 1.0)]
    with pytest.raises(OSError):
        xmlbuild.build(
            cam_paths=["cam1.mp4"],
            segments=segments,
            offsets=[0.0],
            out_path=str(out_xml),
        )

    assert out_xml.exists(), "файл должен остаться на месте"
    assert out_xml.read_text(encoding="utf-8") == original_xml, "содержимое старого XML повреждено"
    assert [p.name for p in tmp_path.iterdir() if ".tmp." in p.name] == [], "временный файл остался"


def test_reset_job_state_полный_сброс():
    """Прямой вызов reset_all_job_state из conftest: очищает флаги, списки, результаты и кэши."""
    from conftest import reset_all_job_state
    from api import _core, gdrive, inserts, previewproxy, render, videogen
    from core import app_meta, insertlib

    with _core.LOCK:
        _core.JOB["running"] = True
        _core.JOB["cancel"] = True
        _core.JOB["log"].append("dirty")
        _core.JOB["results"].append("dirty_result")
        _core.JOB["custom_key"] = "leak"
    with render.RLOCK:
        render.RJOB["running"] = True
        render.RJOB["cancel"] = True
        render.RJOB["result"] = {"out": "leaked"}
        render.RJOB["items"] = [1, 2, 3]
        render.RJOB["log"].append("render_log")
        render.RJOB["out_dir"] = "leaked_dir"
    with gdrive.GDLOCK:
        gdrive.GDJOB["running"] = True
        gdrive.GDJOB["cancel"] = True
        gdrive.GDJOB["log"].append("gd_log")
    with videogen.VLOCK:
        videogen.VJOB["running"] = True
        videogen.VJOB["cancel"] = True
        videogen.VJOB["log"].append("vg_log")
    with previewproxy.PXLOCK:
        previewproxy.PXJOB["running"] = True
        previewproxy.PXJOB["cur"] = "dirty"
    with inserts.ILL_LOCK:
        inserts.ILL_JOB["running"] = True
        inserts.ILL_JOB["error"] = "dirty_error"

    app_meta._UI_LANG_CACHED = "dirty_lang"
    insertlib._CACHE["data"] = {"leaked": True}
    insertlib._CACHE["mtime"] = 999999

    reset_all_job_state()

    assert _core.JOB["running"] is False
    assert _core.JOB["cancel"] is False
    assert _core.JOB["log"] == []
    assert _core.JOB["results"] == []
    assert "custom_key" not in _core.JOB

    assert render.RJOB["running"] is False
    assert render.RJOB["cancel"] is False
    assert render.RJOB["result"] == []
    assert "items" not in render.RJOB
    assert render.RJOB["out_dir"] == ""
    assert render.RJOB["log"] == []

    assert gdrive.GDJOB["running"] is False
    assert gdrive.GDJOB["cancel"] is False
    assert gdrive.GDJOB["log"] == []

    assert videogen.VJOB["running"] is False
    assert videogen.VJOB["cancel"] is False
    assert videogen.VJOB["log"] == []

    assert previewproxy.PXJOB["running"] is False
    assert previewproxy.PXJOB["cur"] == ""

    assert inserts.ILL_JOB["running"] is False
    assert inserts.ILL_JOB["error"] == ""

    assert app_meta._UI_LANG_CACHED is None
    assert insertlib._CACHE["data"] is None
    assert insertlib._CACHE["mtime"] == 0
