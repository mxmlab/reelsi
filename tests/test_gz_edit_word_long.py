# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт H: `edit_word` больше не режет слово по старому лимиту.

Гард `len(text) > lib.max_len` (28 байт у цветной библиотеки, 38 у белой) остался с
эпохи до `subtitle_blobs._grow()`: библиотека уже умеет растить блоб, а правка слова
всё ещё отказывала — «ОТВЕТСТВЕННОСТЬ» (30 байт) не переименовывалась вовсе.

Запуск:  python -m pytest tests -q
"""
import gzip
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

LONG_WORD = "ОТВЕТСТВЕННОСТЬ"          # 30 байт utf-8: длиннее цветной библиотеки (28)
LONG_WHITE = "НЕПРОИЗВОДИТЕЛЬНОСТЬ"    # 40 байт utf-8: длиннее белой библиотеки (38)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = tmp_path / "timeline.xml"
    with gzip.open(ROOT / "tests" / "fixtures" / "timeline_subs.xml.gz", "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return str(dst)


def _blob_of(xml_path, index):
    """base64 Source Text слова #index (порядок parse_full)."""
    from core.xml2ae.highlights import _sub_items, _sub_value_elem
    root = ET.parse(xml_path).getroot()
    seq = root.find(".//sequence")
    _start, _word, clip, _eff = _sub_items(seq)[index]
    return _sub_value_elem(clip).text


@pytest.mark.parametrize("coloured, word", [
    (False, LONG_WHITE),               # белая библиотека кончается на 38 байтах
    (True, LONG_WORD)], ids=["белое", "цветное"])
def test_длинное_слово_правится_и_читается_обратно(xml_subs, coloured, word):
    """Слово длиннее библиотечного лимита: блоб собирается, читается обратно тем же
    словом, цветность сохраняется (как в set_highlights)."""
    from core import subtitle_blobs as sb
    from core.xml2ae import parse_full
    from core.xml2ae.highlights import _b64decode, edit_word, set_highlights

    lib = sb.colour_library() if coloured else sb.library()
    assert len(word.encode("utf-8")) > lib.max_len, \
        "слово должно быть длиннее лимита библиотеки — иначе тест ни о чём"
    if coloured:
        assert set_highlights(xml_subs, [0])["colored"] == [0], "слово 0 не покрасилось"

    res = edit_word(xml_subs, 0, word)

    assert res.get("ok") is True, res
    assert res.get("word") == word
    raw = _b64decode(_blob_of(xml_subs, 0))
    assert sb._read_text(raw) == word, "блоб не читается обратно тем же словом"
    assert bool(sb.blob_is_coloured(raw)) is coloured, "цветность не сохранилась"
    assert parse_full(xml_subs)[2][0][2] == word, "parse_full видит старое слово"


def test_очень_длинное_слово_тоже_правится(xml_subs):
    """Искусственного лимита больше нет: 400 байт библиотека собирает и читает обратно
    (padding блоба растёт под текст) — решать, влезет ли слово в кадр, не её дело."""
    from core import subtitle_blobs as sb
    from core.xml2ae.highlights import _b64decode, edit_word

    word = "Ы" * 400
    res = edit_word(xml_subs, 0, word)

    assert res.get("ok") is True, res
    assert sb._read_text(_b64decode(_blob_of(xml_subs, 0))) == word


def test_несобираемый_блоб_честно_отказывает(xml_subs, monkeypatch):
    """Если блоб не собрался (сборка соврала) — ошибка, а не битый XML: в XML ничего
    не пишем."""
    from core.xml2ae import highlights

    monkeypatch.setattr(highlights, "_make_blob", lambda lib, text, want_col: None)
    raw_before = Path(xml_subs).read_text(encoding="utf-8")

    res = highlights.edit_word(xml_subs, 0, "СЛОВО")

    assert res.get("error"), "сборка блоба провалилась — ожидался отказ"
    assert Path(xml_subs).read_text(encoding="utf-8") == raw_before, "XML тронут"
