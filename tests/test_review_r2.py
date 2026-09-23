# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: исправления по итогам внешнего ревью раунда 2.

1. Трек головы:
   - load_or_track с cancel бросает Cancelled, сайдкар не пишется;
   - load_cached с ranges, которые кэш не покрывает, возвращает None.
2. Режим строк + интро:
   - тайминги слов .words.json переиндексируются по keep (строки как без сайдкара).
3. Кэш без фона:
   - cat.jpg и cat.png получают разные кэши cat.jpg.nobg.png и cat.png.nobg.png,
     remove_bg вызывается для каждого.
4. Вырез без фона не попадает в базу вставок:
   - scan не видит *.nobg.png;
   - nobg_path("x.png.nobg.png") возвращает тот же путь без повторного суффикса.
"""
import gzip
import io
import json
import os
import shutil
import sys
from unittest.mock import MagicMock

from PIL import Image
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import headtrack, insertlib, xml2ae  # noqa: E402
from core.xml2ae.parse import Cancelled  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_headtrack_cancel_and_cached_ranges(tmp_path, monkeypatch):
    """1. load_or_track с cancel, срабатывающим посреди — бросает Cancelled, сайдкара нет;
    load_cached с ranges, которые кэш не покрывает, — None."""
    xml_path = str(tmp_path / "clip.xml")
    video = str(tmp_path / "clip.mp4")
    with open(video, "wb") as f:
        f.write(b"\x00" * 2048)

    monkeypatch.setattr("core.roto._probe", lambda v: (1920, 1080, 60, 10))
    monkeypatch.setattr("core.roto._load", lambda: (MagicMock(), "cpu", None))
    monkeypatch.setattr("core.roto.release", lambda emit=None: None)
    monkeypatch.setitem(sys.modules, "torch", MagicMock())

    class FakeProc:
        def __init__(self):
            self.stdout = MagicMock()
            self.stdout.close = MagicMock()
            self.stdout.read = MagicMock(return_value=b"")

        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakeProc())

    step = [0]

    def cancel_mid():
        step[0] += 1
        return step[0] >= 2

    ranges = [(0.0, 5.0), (5.0, 10.0)]
    with pytest.raises(Cancelled):
        headtrack.load_or_track(xml_path, video, ranges, cancel=cancel_mid)

    head_file = str(tmp_path / "clip.head.json")
    assert not os.path.exists(head_file), "При отмене сайдкар не должен создаваться"

    cached_data = {
        "v": 1,
        "video": os.path.abspath(video),
        "size": os.path.getsize(video),
        "mtime": os.path.getmtime(video),
        "ranges": [[0.0, 5.0]],
        "pts": [[0.0, 0.5], [5.0, 0.5]],
    }
    with open(head_file, "w", encoding="utf-8") as f:
        json.dump(cached_data, f)

    assert headtrack.load_cached(xml_path, video, ranges=[(0.0, 5.0)]) is not None
    assert headtrack.load_cached(xml_path, video, ranges=[(0.0, 10.0)]) is None
    assert headtrack.load_cached(xml_path, video, ranges=[(6.0, 8.0)]) is None


def test_sub_rows_intro_words_json_filtered(xml_subs):
    """2. Строки + интро + .words.json с точкой на первом (интро) слове — строки как без сайдкара."""
    _meta, _cams, subs_orig, _xml_ins = xml2ae.parse_full(xml_subs)
    assert len(subs_orig) >= 4, "В фикстуре недостаточно слов"

    intro = [{"text": subs_orig[0][2], "start_s": 0.0, "dur_s": 1.0}]
    intro_remove = [0]

    plan_no_sidecar = xml2ae.scene_plan(
        xml_subs,
        style={"sub_words_per_row": 3},
        intro=intro,
        intro_remove=intro_remove,
    )

    sidecar_path = os.path.splitext(xml_subs)[0] + ".words.json"
    fake_words = []
    for i, (_s, _e, w) in enumerate(subs_orig):
        word_text = w.rstrip(".,!?;:")
        if i == 0:
            word_text += "."
        fake_words.append({"w": word_text, "start": i * 0.2, "end": (i + 1) * 0.2})

    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(fake_words, f, ensure_ascii=False)

    plan_with_sidecar = xml2ae.scene_plan(
        xml_subs,
        style={"sub_words_per_row": 3},
        intro=intro,
        intro_remove=intro_remove,
    )

    rows_no = [x["w"] for x in plan_no_sidecar["subs"]]
    rows_with = [x["w"] for x in plan_with_sidecar["subs"]]
    assert rows_no == rows_with, f"Строки разошлись:\nБез сайдкара: {rows_no}\nС сайдкаром: {rows_with}"


def test_nobg_cache_distinct_for_jpg_and_png(tmp_path, monkeypatch):
    """3. cat.jpg и cat.png — два разных кэша, remove_bg зван дважды."""
    cat_jpg = str(tmp_path / "cat.jpg")
    cat_png = str(tmp_path / "cat.png")

    im = Image.new("RGB", (100, 100), color=(255, 0, 0))
    im.save(cat_jpg, format="JPEG")
    im.save(cat_png, format="PNG")

    calls = []

    def fake_remove_bg(data, trim=True, emit=None):
        calls.append(len(data))
        buf = io.BytesIO()
        Image.new("RGBA", (80, 80), color=(0, 255, 0, 255)).save(buf, format="PNG")
        return buf.getvalue()

    monkeypatch.setattr(insertlib, "remove_bg", fake_remove_bg)

    dst_jpg = insertlib.nobg_path(cat_jpg)
    dst_png = insertlib.nobg_path(cat_png)

    assert dst_jpg != dst_png, f"Кэши должны быть разными: {dst_jpg} == {dst_png}"
    assert dst_jpg.endswith("cat.jpg.nobg.png"), f"Ожидался cat.jpg.nobg.png, получено: {dst_jpg}"
    assert dst_png.endswith("cat.png.nobg.png"), f"Ожидался cat.png.nobg.png, получено: {dst_png}"
    assert len(calls) == 2, f"remove_bg должен быть вызван дважды (по разу на файл), вызовов: {len(calls)}"


def test_scan_skips_nobg_and_nobg_path_idempotent(tmp_path):
    """4. scan не видит *.nobg.png; nobg_path("x.png.nobg.png") — тот же путь."""
    real_png = str(tmp_path / "card.png")
    nobg1 = str(tmp_path / "card.png.nobg.png")
    nobg2 = str(tmp_path / "legacy.nobg.png")

    Image.new("RGB", (50, 50), color=(100, 100, 100)).save(real_png)
    Image.new("RGBA", (40, 40), color=(0, 0, 0, 0)).save(nobg1)
    Image.new("RGBA", (40, 40), color=(0, 0, 0, 0)).save(nobg2)

    scanned = insertlib.scan([str(tmp_path)])
    assert os.path.abspath(real_png) in scanned, "Обычный файл должен быть в базе"
    assert os.path.abspath(nobg1) not in scanned, "card.png.nobg.png не должен попасть в базу"
    assert os.path.abspath(nobg2) not in scanned, "legacy.nobg.png не должен попасть в базу"

    res = insertlib.nobg_path("x.png.nobg.png")
    assert res == "x.png.nobg.png", f"nobg_path должен вернуть тот же путь, получено: {res!r}"
