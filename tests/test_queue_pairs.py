# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка склейки путей очереди клипов и папок камер (api/jobs.py: build_pairs).

Очередь хранит только имена файлов. build_pairs склеивает полные пути и проверяет
наличие файлов на диске, чтобы рассинхрон папки камеры и очереди не уезжал молча
в ffmpeg с кодом -2 (жалоба 2026-08-20).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from api.jobs import build_pairs  # noqa: E402
from core.umsg import ReelsiError, UMsg  # noqa: E402


def test_build_pairs_validates_file_existence(tmp_path):
    """Несуществующий файл даёт ReelsiError(queue_file_missing), существующий отдаёт полные пути."""
    cam1 = tmp_path / "cam1"
    cam1.mkdir()
    camdirs = [str(cam1)]

    # 1. Несуществующий файл -> ReelsiError с кодом queue_file_missing
    with pytest.raises(ReelsiError) as exc_info:
        build_pairs(camdirs, [["missing_clip.mp4"]])
    err = exc_info.value.umsg
    assert isinstance(err, UMsg), "ошибка должна быть обёрнута в UMsg"
    assert err.code == "queue_file_missing"
    assert "missing_clip.mp4" in err.vars.get("path", "")

    # 2. Существующий файл (tmp_path) -> проходит и отдаёт полные пути
    f1 = cam1 / "clip_01.mp4"
    f1.write_text("data", encoding="utf-8")
    pairs = build_pairs(camdirs, [["clip_01.mp4"]])
    assert pairs == [[str(f1)]]
