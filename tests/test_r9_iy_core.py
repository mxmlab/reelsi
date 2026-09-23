# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты (круг 9): ядро.

1. Отбраковка вставок: не-числовой start_sec получает шанс в _snap_to_phrase.
   Вставка с start_sec: NaN и цитатой из ленты остаётся со стартом цитаты;
   без цитаты — отбрасывается.
2. cmd_intro: гард ловит OverflowError от бесконечностей (float('inf')).
3. fileio:
   - fsync каталога не роняет уже совершённую запись (в собственном try/except).
   - существующая цель без прав на запись (readonly) даёт PermissionError, tmp убран.
4. subs: такты на кадр (pproTicks) считаются от частоты секвенции (254016000000 / fps).
   Для 60 fps — побайтово прежние 4233600000; для 29.97 — такты кадра 29.97.

Запуск: py -3.10 -m pytest tests/test_r9_im_core.py -q -p no:cacheprovider
"""
import gzip
import math
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.aicut import commands  # noqa: E402
from core import fileio, subs, subtitle_xml, xmlbuild  # noqa: E402


class _Emit:
    def __init__(self):
        self.lines = []

    def __call__(self, msg, **vars):
        try:
            self.lines.append(str(msg).format(**vars))
        except Exception:
            self.lines.append(str(msg))


@pytest.fixture()
def xml_subs(tmp_path):
    dst = tmp_path / "timeline.xml"
    with gzip.open(HERE / "fixtures" / "timeline_subs.xml.gz", "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return str(dst)


# =========================================================================== #
# 1. Порядок отбраковки вставок: шанс для цитаты
# =========================================================================== #
def test_insert_with_nan_start_and_matching_phrase_kept(xml_subs, monkeypatch):
    """Вставка с start_sec: NaN и фразой из ленты привязывается к цитате и сохраняется.
    Вставка с start_sec: NaN без цитаты (или с ненайденной фразой) отбрасывается."""
    # Слова 27 и 28 в fixtures/timeline_subs.xml.gz: 'СИТАМЕ' (~11.02с), 'КОНСЕК'
    answer = {
        "inserts": [
            {
                "type": "photo",
                "start_sec": float("nan"),
                "duration_sec": 2.5,
                "query": "чашка кофе",
                "phrase": "СИТАМЕ КОНСЕК",
            },
            {
                "type": "photo",
                "start_sec": float("nan"),
                "duration_sec": 2.5,
                "query": "ноутбук на столе",
                "phrase": "СОВЕРШЕННО ЧУЖАЯ ФРАЗА НЕ ИЗ РОЛИКА",
            },
            {
                "type": "video",
                "start_sec": 20.0,
                "duration_sec": 3.0,
                "query": "город с дрона",
            },
        ]
    }
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: answer)
    em = _Emit()
    res = commands.cmd_inserts(xml_subs, count=1, emit=em)

    queries = [it["query"] for it in res["inserts"]]
    # Вставка с фразой 'СИТАМЕ КОНСЕК' должна остаться
    assert "чашка кофе" in queries, f"Вставка с валидной цитатой потеряна: {res['inserts']}"
    # Вставка без найденной цитаты должна быть отброшена
    assert "ноутбук на столе" not in queries, f"Вставка без валидной цитаты не отброшена: {res['inserts']}"
    # Нормальная вставка остаётся
    assert "город с дрона" in queries, f"Годная вставка потеряна: {res['inserts']}"

    # Старт привязан к началу фразы (~11.02с)
    cup = next(it for it in res["inserts"] if it["query"] == "чашка кофе")
    assert math.isfinite(float(cup["start_sec"]))
    assert cup["start_sec"] == pytest.approx(11.02, abs=0.1)

    # В логе есть сообщение об отброшенной вставке без цитаты
    assert any("start_sec не число" in line for line in em.lines)


# =========================================================================== #
# 2. cmd_intro: гард ловит OverflowError от Infinity
# =========================================================================== #
def test_cmd_intro_survives_infinity_counts(xml_subs, monkeypatch):
    """cmd_intro не падает OverflowError, если модель вернула Infinity / float('inf')."""
    answer = {
        "intro_rows": [
            {"count": float("inf"), "color": "yellow"},
            {"count": float("-inf"), "color": "white"},
            {"count": 2, "color": "white", "break": True},
        ],
        "mid_groups": [
            {"from": float("inf"), "count": 2, "color": "accent"},
            {"from": 10, "count": float("inf"), "color": "accent"},
            {"from": 12, "count": 2, "color": "accent", "back": True},
        ],
    }
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: answer)
    em = _Emit()
    # Не должно упасть с OverflowError
    res = commands.cmd_intro(xml_subs, emit=em)
    assert "intro_rows" in res
    assert "mid_groups" in res
    # Годная строка хука осталась
    assert len(res["intro_rows"]) >= 1
    assert all(math.isfinite(r["count"]) for r in res["intro_rows"])


# =========================================================================== #
# 3. fileio: долговечность и гвард от записи в readonly
# =========================================================================== #
def test_atomic_write_readonly_raises_permission_error(tmp_path):
    """Существующий readonly файл даёт PermissionError, tmp убран, файл не изменён."""
    p = tmp_path / "readonly.txt"
    p.write_text("original", encoding="utf-8")
    os.chmod(str(p), stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            fileio.atomic_text_write(str(p), "modified")
        assert p.read_text(encoding="utf-8") == "original"
        # tmp-файл не должен остаться в каталоге
        tmps = list(tmp_path.glob("readonly.txt.tmp.*"))
        assert tmps == [], f"Остались временные файлы: {tmps}"
    finally:
        os.chmod(str(p), stat.S_IWRITE)


def test_atomic_write_dir_fsync_failure_does_not_fail_write(tmp_path, monkeypatch):
    """Сбой fsync каталога после успешного os.replace не роняет запись."""
    p = tmp_path / "test.txt"

    orig_os_name = os.name
    try:
        monkeypatch.setattr(os, "name", "posix")

        orig_open = fileio.os.open
        orig_fsync = fileio.os.fsync
        orig_close = fileio.os.close

        dummy_dir_fd = 9999

        def fake_open(path, flags, *args, **kwargs):
            if os.path.realpath(str(path)) == os.path.realpath(str(tmp_path)):
                return dummy_dir_fd
            return orig_open(path, flags, *args, **kwargs)

        def fake_fsync(fd):
            if fd == dummy_dir_fd:
                raise RuntimeError("fsync catastrophic directory error")
            return orig_fsync(fd)

        def fake_close(fd):
            if fd == dummy_dir_fd:
                return
            return orig_close(fd)

        monkeypatch.setattr(fileio.os, "open", fake_open)
        monkeypatch.setattr(fileio.os, "fsync", fake_fsync)
        monkeypatch.setattr(fileio.os, "close", fake_close)

        # Не должно бросить исключение: ошибка каталожного fsync изолирована
        fileio.atomic_text_write(str(p), "hello")
        assert p.read_text(encoding="utf-8") == "hello"
    finally:
        monkeypatch.setattr(os, "name", orig_os_name)


# =========================================================================== #
# 4. subs: такты на кадр зависят от частоты секвенции
# =========================================================================== #
def test_subs_ticks_per_frame_varies_with_fps():
    """pproTicksOut / кадры = такты кадра переданной частоты (254016000000 / fps)."""
    sb = subs.SubtitleBuilder()
    # 1. 60 fps (дефолт): 254016000000 / 60 = 4233600000
    c60 = sb.clip("ТЕСТ", 0, 60, 1, 1, fps=60)
    m60 = re.search(r"<pproTicksOut>(\d+)</pproTicksOut>", c60)
    assert m60 is not None
    ticks60 = int(m60.group(1))
    out60 = subs.GFX_IN + 60
    assert ticks60 == out60 * 4233600000
    assert ticks60 // out60 == 4233600000

    # 2. 29.97 fps (NTSC): 254016000000 / (30000/1001) = 8475675676
    fps_ntsc = 30000 / 1001
    tpf_ntsc = int(round(254016000000 / fps_ntsc))
    c29 = sb.clip("ТЕСТ", 0, 30, 1, 1, fps=fps_ntsc)
    m29 = re.search(r"<pproTicksOut>(\d+)</pproTicksOut>", c29)
    assert m29 is not None
    ticks29 = int(m29.group(1))
    out29 = subs.GFX_IN + 30
    assert ticks29 == out29 * tpf_ntsc
    assert ticks29 // out29 == tpf_ntsc
    assert ticks29 // out29 != 4233600000


def test_build_subtitle_track_passes_fps_to_clip():
    """xmlbuild.build_subtitle_track передаёт fps в clip."""
    words = [{"w": "ТЕСТ", "start": 0, "end": 30}]
    fps_ntsc = 30000 / 1001
    tpf_ntsc = int(round(254016000000 / fps_ntsc))
    vtrack, n_subs, longs, _ = xmlbuild.build_subtitle_track(words, start_id=1, fps=fps_ntsc)
    assert n_subs == 1
    m = re.search(r"<pproTicksOut>(\d+)</pproTicksOut>", vtrack)
    assert m is not None
    ticks = int(m.group(1))
    out = subs.GFX_IN + 30
    assert ticks // out == tpf_ntsc


def test_subtitle_xml_build_track_passes_fps():
    """subtitle_xml._build_subtitle_track передаёт fps в xmlbuild."""
    words = [{"w": "ТЕСТ", "start": 0, "end": 30}]
    fps_ntsc = 30000 / 1001
    tpf_ntsc = int(round(254016000000 / fps_ntsc))
    vtrack, n, longs = subtitle_xml._build_subtitle_track(words, 1, fps=fps_ntsc)
    assert n == 1
    m = re.search(r"<pproTicksOut>(\d+)</pproTicksOut>", vtrack)
    assert m is not None
    ticks = int(m.group(1))
    out = subs.GFX_IN + 30
    assert ticks // out == tpf_ntsc

