# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IH (круг 8): долговечность переименования и fsync каталога в `core/fileio.py`.

1. `_atomic_write` делал `os.fsync` на файле, но на POSIX не сбрасывал каталог после `os.replace`.
   При отбое питания переименование могло не зафиксироваться на диске.
   Теперь на POSIX каталог открывается `os.open(d, os.O_RDONLY)` и сбрасывается через `os.fsync`.
   На Windows это не делается (NTFS журналирует метаданные, каталог в CRT не открыть).
2. Любые ошибки `OSError` при открытии или сбросе каталога игнорируются (для ФС, где
   открытие каталога не поддерживается).
3. Докстринг `_atomic_write` описывает целостность (всегда) и долговечность (fsync каталога на POSIX,
   журнал NTFS на Windows).

Запуск: py -3.10 -m pytest tests/test_r8_ih_durable.py -q -p no:cacheprovider
"""
import errno
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import fileio  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="fsync каталога только на POSIX")


@posix_only
def test_posix_directory_fsync_called(tmp_path, monkeypatch):
    """На POSIX каталог открывается и сбрасывается через fsync после os.replace."""
    opened_dirs = []
    fsynced_fds = []
    closed_fds = []

    orig_open = fileio.os.open
    orig_fsync = fileio.os.fsync
    orig_close = fileio.os.close

    def fake_open(path, flags, *args, **kwargs):
        fd = orig_open(path, flags, *args, **kwargs)
        if os.path.isdir(path):
            opened_dirs.append((os.path.realpath(str(path)), flags, fd))
        return fd

    def fake_fsync(fd):
        fsynced_fds.append(fd)
        return orig_fsync(fd)

    def fake_close(fd):
        closed_fds.append(fd)
        return orig_close(fd)

    monkeypatch.setattr(fileio.os, "open", fake_open)
    monkeypatch.setattr(fileio.os, "fsync", fake_fsync)
    monkeypatch.setattr(fileio.os, "close", fake_close)

    target = tmp_path / "posix_durable.json"
    fileio.atomic_json_dump(str(target), {"saved": 1})

    assert target.exists()
    assert len(opened_dirs) == 1, "Каталог должен быть открыт ровно один раз"
    dir_path, flags, dfd = opened_dirs[0]
    assert dir_path == os.path.realpath(str(tmp_path))
    assert (flags & os.O_RDONLY) == os.O_RDONLY
    assert dfd in fsynced_fds, f"os.fsync не вызван для каталога (fd={dfd})"
    assert dfd in closed_fds, f"дескриптор каталога {dfd} не закрыт"


def test_posix_branch_logic_on_any_os(tmp_path, monkeypatch):
    """Логика POSIX-ветки: открытие каталога os.O_RDONLY, fsync и закрытие дескриптора."""
    orig_os_name = os.name
    try:
        monkeypatch.setattr(os, "name", "posix")

        opened_dirs = []
        fsynced_fds = []
        closed_fds = []
        dummy_dir_fd = 9999

        orig_open = fileio.os.open
        orig_fsync = fileio.os.fsync
        orig_close = fileio.os.close

        def fake_open(p, flags, *args, **kwargs):
            if str(p) == str(tmp_path) or os.path.realpath(str(p)) == os.path.realpath(str(tmp_path)):
                opened_dirs.append((str(p), flags))
                return dummy_dir_fd
            return orig_open(p, flags, *args, **kwargs)

        def fake_fsync(fd):
            fsynced_fds.append(fd)
            if fd != dummy_dir_fd:
                return orig_fsync(fd)

        def fake_close(fd):
            closed_fds.append(fd)
            if fd != dummy_dir_fd:
                return orig_close(fd)

        monkeypatch.setattr(fileio.os, "open", fake_open)
        monkeypatch.setattr(fileio.os, "fsync", fake_fsync)
        monkeypatch.setattr(fileio.os, "close", fake_close)

        target = tmp_path / "simulated_posix.json"
        fileio.atomic_json_dump(str(target), {"sim": True})

        assert len(opened_dirs) == 1, "Каталог должен быть открыт в POSIX-ветке"
        _, flags = opened_dirs[0]
        assert (flags & os.O_RDONLY) == os.O_RDONLY
        assert dummy_dir_fd in fsynced_fds, "fsync должен быть вызван для каталога"
        assert dummy_dir_fd in closed_fds, "дескриптор каталога должен быть закрыт"
    finally:
        monkeypatch.setattr(os, "name", orig_os_name)


def test_directory_open_oserror_silent(tmp_path, monkeypatch):
    """На любой ОС OSError при open каталога не роняет запись."""
    orig_os_name = os.name
    try:
        monkeypatch.setattr(os, "name", "posix")

        orig_open = fileio.os.open

        def fake_open(p, flags, *args, **kwargs):
            if str(p) == str(tmp_path) or (isinstance(p, str) and os.path.isdir(p)):
                raise OSError(errno.EACCES, "Opening directory not supported")
            return orig_open(p, flags, *args, **kwargs)

        monkeypatch.setattr(fileio.os, "open", fake_open)

        target = tmp_path / "safe_open.json"
        fileio.atomic_json_dump(str(target), {"data": 123})
        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8")) == {"data": 123}
    finally:
        monkeypatch.setattr(os, "name", orig_os_name)


def test_directory_fsync_oserror_silent(tmp_path, monkeypatch):
    """На любой ОС OSError при fsync каталога не роняет запись и закрывает дескриптор."""
    orig_os_name = os.name
    try:
        monkeypatch.setattr(os, "name", "posix")
        dummy_dfd = 8888

        orig_open = fileio.os.open
        orig_fsync = fileio.os.fsync
        orig_close = fileio.os.close
        closed = []

        def fake_open(p, flags, *args, **kwargs):
            if str(p) == str(tmp_path) or (isinstance(p, str) and os.path.isdir(p)):
                return dummy_dfd
            return orig_open(p, flags, *args, **kwargs)

        def fake_fsync(fd):
            if fd == dummy_dfd:
                raise OSError(errno.EINVAL, "Invalid argument for directory fsync")
            return orig_fsync(fd)

        def fake_close(fd):
            closed.append(fd)
            if fd != dummy_dfd:
                return orig_close(fd)

        monkeypatch.setattr(fileio.os, "open", fake_open)
        monkeypatch.setattr(fileio.os, "fsync", fake_fsync)
        monkeypatch.setattr(fileio.os, "close", fake_close)

        target = tmp_path / "safe_fsync.txt"
        fileio.atomic_text_write(str(target), "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"
        assert dummy_dfd in closed, "Дескриптор каталога обязан быть закрыт даже при падении fsync"
    finally:
        monkeypatch.setattr(os, "name", orig_os_name)


def test_windows_does_not_open_directory(tmp_path, monkeypatch):
    """На Windows (os.name == 'nt') каталог не открывается (NTFS журналирует метаданные)."""
    monkeypatch.setattr(fileio.os, "name", "nt")
    opened_dirs = []
    orig_open = fileio.os.open

    def fake_open(p, flags, *args, **kwargs):
        if isinstance(p, str) and os.path.isdir(p):
            opened_dirs.append(p)
        return orig_open(p, flags, *args, **kwargs)

    monkeypatch.setattr(fileio.os, "open", fake_open)

    target = tmp_path / "win.json"
    fileio.atomic_json_dump(str(target), {"win": True})
    assert opened_dirs == [], "На Windows каталог не должен открываться"


def test_atomic_write_docstring_durability():
    """Докстринг _atomic_write содержит обещания целостности и долговечности (NTFS/POSIX)."""
    doc = fileio._atomic_write.__doc__
    assert doc is not None
    assert "целостность" in doc.lower(), "докстринг должен упоминать гарантию целостности"
    assert "долговечность" in doc.lower(), "докстринг должен упоминать долговечность"
    assert "ntfs" in doc.lower(), "докстринг должен упоминать NTFS"
