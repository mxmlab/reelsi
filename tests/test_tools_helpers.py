# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистых функций-помощников в сложных скриптах: tools/bench_vision.py и tools/train_breath.py."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np

import tools.bench_vision as bv
import tools.train_breath as tb
from core.project_file import write_project


def test_bench_vision_keyword() -> None:
    """_keyword извлекает валидное ключевое слово из имени файла."""
    # Меньше минимальной длины (8)
    assert bv._keyword("cat_dog.png") is None

    # В списке исключений
    assert bv._keyword("medicine_packaging.png") is None
    assert bv._keyword("pharmacy_tablets.png") is None

    # Начинается с gemini
    assert bv._keyword("geminipro_output.jpg") is None

    # Не латиница
    assert bv._keyword("микроскоп_тест.png") is None

    # Валидное ключевое слово
    assert bv._keyword("medical_glucometer_sample.png") == "glucometer"


def test_bench_vision_hit() -> None:
    """hit нечувствителен к регистру и ищет вхождение подстроки."""
    assert bv.hit("glucometer", "A compact Glucometer on the desk") is True
    assert bv.hit("glucometer", "Blood pressure monitor") is False


def test_bench_vision_select_oracle(tmp_path: Path) -> None:
    """select_oracle фильтрует записи по desc_src, gone, наличию файла и ключевого слова."""
    real_f1 = tmp_path / "valid_glucometer.png"
    real_f1.write_bytes(b"data")
    real_f2 = tmp_path / "valid_stethoscope.png"
    real_f2.write_bytes(b"data")

    items: list[dict[str, Any]] = [
        # Подходит
        {"name": "valid_glucometer.png", "path": str(real_f1), "desc_src": "ai"},
        # Не ai
        {"name": "valid_stethoscope.png", "path": str(real_f2), "desc_src": "manual"},
        # gone
        {"name": "valid_stethoscope.png", "path": str(real_f2), "desc_src": "ai", "gone": True},
        # Нет файла на диске
        {"name": "valid_microscope.png", "path": str(tmp_path / "missing.png"), "desc_src": "ai"},
        # Нет ключевого слова (слишком короткое)
        {"name": "short.png", "path": str(real_f1), "desc_src": "ai"},
    ]

    oracle = bv.select_oracle(items)
    assert len(oracle) == 1
    assert oracle[0][1] == "glucometer"
    assert oracle[0][0]["path"] == str(real_f1)


def test_train_breath_rate() -> None:
    """_rate подсчитывает число истинных (hit) и ложных (fp) срабатываний по порогу."""
    p = np.array([0.1, 0.4, 0.6, 0.9])
    y = np.array([0, 1, 1, 0])
    # При пороге 0.5:
    # p >= 0.5: индексы 2 (y=1 -> hit) и 3 (y=0 -> fp)
    hit, fp = tb._rate(p, y, 0.5)
    assert hit == 1
    assert fp == 1

    # При пороге 0.0: все 4 предсказания >= 0.0 (два hit, два fp)
    hit_all, fp_all = tb._rate(p, y, 0.0)
    assert hit_all == 2
    assert fp_all == 2


def test_train_breath_clips(tmp_path: Path) -> None:
    """_clips отбирает ролики с ручной правкой по разнице mtime между project.json и cuts.json."""
    d = tmp_path / "clips_dir"
    d.mkdir()

    cam_file = d / "cam1.mp4"
    cam_file.write_bytes(b"video")

    # 1. Ролик без cuts.json -> пропуск
    proj1 = d / "01.project.json"
    write_project(str(proj1), {"cams": [str(cam_file)], "keep": [[0.0, 5.0]]})

    # 2. Ролик с cuts.json, но mtime разница <= 60 -> пропуск (автонарезка не правилась)
    proj2 = d / "02.project.json"
    cuts2 = d / "02.cuts.json"
    write_project(str(proj2), {"cams": [str(cam_file)], "keep": [[0.0, 5.0]]})
    cuts2.write_text("[]", encoding="utf-8")
    now = time.time()
    os.utime(cuts2, (now, now))
    os.utime(proj2, (now + 10, now + 10))

    # 3. Ролик с ручной правкой: project.json новее cuts.json на 100 секунд
    proj3 = d / "03.project.json"
    cuts3 = d / "03.cuts.json"
    write_project(str(proj3), {"cams": [str(cam_file)], "keep": [[1.0, 4.0]]})
    cuts3.write_text("[]", encoding="utf-8")
    os.utime(cuts3, (now, now))
    os.utime(proj3, (now + 100, now + 100))

    clips = tb._clips([str(d)])
    assert len(clips) == 1
    stem, cam, keep = clips[0]
    assert stem.endswith("03")
    assert cam == str(cam_file)
    assert keep == [[1.0, 4.0]]
