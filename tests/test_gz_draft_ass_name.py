# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт I: имя `.ass` не подставляется в фильтр ffmpeg сырым.

`flt.append(f"[vc]subtitles={ass_name}[vout]")` получал имя стема как есть, а фильтрграф
разбирается по запятым, `[ ]`, `;`, `:` и кавычкам: имя `C1,2[1];x'y.draft.ass` рвёт
`subtitles` (перепроверено на ffmpeg 8.0). Черновик не собирался вовсе.

Запуск:  python -m pytest tests -q
"""
import re
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

STEM = "C1,2[1];x'y"                      # так выглядит стем ролика пользователя
SAFE_NAME = re.compile(r"[A-Za-z0-9_.\-]+")


@pytest.fixture
def draft(tmp_path, monkeypatch):
    """render_draft с подменённым EDL и ffmpeg: тест про имя файла субтитров."""
    from core import draftrender, xml2ae

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    xml = tmp_path / f"{STEM}.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(xml2ae, "virtual_edl", lambda p, ncams=None: {
        "segs": [{"ci": 0, "ts": 0.0, "te": 1.0, "src": 0.0}],
        "audio": [{"ci": 0, "ts": 0.0, "te": 1.0, "src": 0.0}],
        "cams": [{"path": str(clip)}],
        "w": 1080, "h": 1920,
        "words": [{"w": "привет", "s": 0.0, "e": 1.0}]})
    monkeypatch.setattr(draftrender, "hw_encoder", lambda refresh=False: None)
    monkeypatch.setattr(draftrender, "_run_ff", lambda cmd, **kw: types.SimpleNamespace(
        returncode=1, stdout="", stderr="фильтр сломан"))
    return draftrender, xml, tmp_path


def test_фильтр_субтитров_не_содержит_спецсимволов(draft):
    draftrender, xml, tmp_path = draft

    with pytest.raises(RuntimeError):          # ffmpeg подменён: сборка «не удалась»
        draftrender.render_draft(str(xml), use_proxy=False, emit=lambda *a, **k: None)

    script = (tmp_path / "_tmp" / f"{STEM}.draft_filters.txt").read_text(encoding="utf-8")
    m = re.search(r"subtitles=([^\[\]]+)", script)
    assert m, f"в фильтре нет subtitles:\n{script}"
    name = m.group(1)
    assert SAFE_NAME.fullmatch(name), f"опасное имя файла субтитров: {name!r}"
    assert (tmp_path / "_tmp" / name).is_file(), "файла субтитров с таким именем нет"
    for bad in (",", "[", "]", ";", ":", "'", '"'):
        assert bad not in name, f"{bad!r} в имени {name!r} ломает фильтрграф"


def test_имя_файла_субтитров_одно_и_то_же(draft):
    """Имя фиксированное, а не производное от стема: два ролика с «опасными» стемами
    не могут дать разные имена, которые по-разному сломают фильтр."""
    draftrender, xml, tmp_path = draft

    with pytest.raises(RuntimeError):
        draftrender.render_draft(str(xml), use_proxy=False, emit=lambda *a, **k: None)

    script = (tmp_path / "_tmp" / f"{STEM}.draft_filters.txt").read_text(encoding="utf-8")
    assert "subtitles=draft_subs.ass" in script, script
