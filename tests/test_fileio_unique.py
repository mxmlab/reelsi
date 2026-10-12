# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""core.fileio.unique_path — свободное имя `name`, `name_2`, `name_3`… (одна функция вместо копий).

Файл: суффикс перед расширением. Папка: суффикс в конце целого имени, без отрезания «расширения»."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.fileio import unique_path  # noqa: E402


def test_файл_свободен_и_занят_1_и_2(tmp_path):
    folder = str(tmp_path)
    assert unique_path(folder, "talk.mp3") == os.path.join(folder, "talk.mp3")
    (tmp_path / "talk.mp3").write_bytes(b"1")
    assert unique_path(folder, "talk.mp3") == os.path.join(folder, "talk_2.mp3")
    (tmp_path / "talk_2.mp3").write_bytes(b"2")
    assert unique_path(folder, "talk.mp3") == os.path.join(folder, "talk_3.mp3")


def test_файл_без_расширения_и_с_точкой_в_имени(tmp_path):
    (tmp_path / "README").write_text("x", encoding="utf-8")
    assert unique_path(str(tmp_path), "README") == os.path.join(str(tmp_path), "README_2")
    (tmp_path / "clip.v1.mp4").write_text("x", encoding="utf-8")
    assert unique_path(str(tmp_path), "clip.v1.mp4") == os.path.join(str(tmp_path), "clip.v1_2.mp4")


def test_папка_суффикс_в_конце_расширение_не_режется(tmp_path):
    (tmp_path / "20260101-120000_clip.v1").mkdir()
    assert unique_path(str(tmp_path), "20260101-120000_clip.v1", is_dir=True) == \
        os.path.join(str(tmp_path), "20260101-120000_clip.v1_2")
    (tmp_path / "20260101-120000_clip.v1_2").mkdir()
    assert unique_path(str(tmp_path), "20260101-120000_clip.v1", is_dir=True) == \
        os.path.join(str(tmp_path), "20260101-120000_clip.v1_3")


def test_своя_проверка_занятости(tmp_path):
    """busy — чем считать занятым (подкаст: занято, если там podcast.json, а не сама папка)."""
    (tmp_path / "talk").mkdir()                    # папка есть, но «занятой» не считается
    busy = lambda p: os.path.exists(os.path.join(p, "podcast.json"))  # noqa: E731
    assert unique_path(str(tmp_path), "talk", is_dir=True, busy=busy) == os.path.join(str(tmp_path), "talk")
    (tmp_path / "talk" / "podcast.json").write_text("{}", encoding="utf-8")
    assert unique_path(str(tmp_path), "talk", is_dir=True, busy=busy) == os.path.join(str(tmp_path), "talk_2")
