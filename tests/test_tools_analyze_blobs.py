# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты чистой логики разбора блобов Premiere: tools/analyze_blobs.py."""
from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path
from typing import Generator


import pytest

from core import paths


def _build_synthetic_blob(word: str, prefix_pad: int = 4, suffix_pad: int = 4) -> bytes:
    """Генерирует синтетический FlatBuffer-блоб со строкой word."""
    wb = word.encode("utf-8")
    wb_len = len(wb)
    # 4 байта длины блоба (size prefix), затем pad, затем u32 длина строки, текст, pad
    body = (
        b"\x00" * prefix_pad
        + wb_len.to_bytes(4, "little")
        + wb
        + b"\x00" * suffix_pad
    )
    total_len = len(body) + 4
    return total_len.to_bytes(4, "little") + body


@pytest.fixture(autouse=True)
def _isolate_analyze_blobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Предотвращает запись analyze_report.txt в корень репозитория при импорте модуля."""
    dummy_xml = tmp_path / "dummy.xml"
    dummy_xml.write_text("<empty/>", encoding="utf-8")
    orig_root = paths.root

    def _mock_root(name: str = "") -> str:
        if name == "analyze_report.txt":
            return str(tmp_path / name)
        return orig_root(name)

    monkeypatch.setattr(paths, "root", _mock_root)
    monkeypatch.setattr(sys, "argv", ["analyze_blobs.py", str(dummy_xml)])
    if "tools.analyze_blobs" in sys.modules:
        del sys.modules["tools.analyze_blobs"]
    yield
    if "tools.analyze_blobs" in sys.modules:
        del sys.modules["tools.analyze_blobs"]


def test_find_str_region_matches_and_mismatches() -> None:
    """find_str_region корректно находит смещение и длину UTF-8 строки."""
    from tools.analyze_blobs import find_str_region, show

    blob = _build_synthetic_blob("тест", prefix_pad=8)
    reg = find_str_region(blob, "тест")
    assert reg is not None
    offset, length = reg
    # 4 байта префикса + 8 байт pad = смещение 12
    assert offset == 12
    assert length == len("тест".encode("utf-8"))

    # Слово не найдено (другой текст)
    assert find_str_region(blob, "другое") is None

    # Буфер слишком короткий
    assert find_str_region(b"\x01\x02", "тест") is None

    # Функция show возвращает отформатированную строку
    s = show("тест", blob, reg)
    assert "word='тест'" in s
    assert f"textlen={length}" in s


def test_analyze_blobs_script_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Сквозной прогон логики analyze_blobs на синтетическом XML во tmp_path."""
    b1 = _build_synthetic_blob("слово1", prefix_pad=4, suffix_pad=4)
    b2 = _build_synthetic_blob("слово2", prefix_pad=4, suffix_pad=4)
    b3 = _build_synthetic_blob("да", prefix_pad=4, suffix_pad=4)

    val1 = base64.b64encode(b1).decode("ascii")
    val2 = base64.b64encode(b2).decode("ascii")
    val3 = base64.b64encode(b3).decode("ascii")

    xml_content = f"""<xsequence>
<effect>
    <name>слово1</name>
    <effectid>GraphicAndType</effectid>
    <name>Source Text</name>
    <hash>11111111-2222</hash>
    <value>{val1}</value>
</effect>
<effect>
    <name>слово2</name>
    <effectid>GraphicAndType</effectid>
    <name>Source Text</name>
    <hash>11111111-3333</hash>
    <value>{val2}</value>
</effect>
<effect>
    <name>да</name>
    <effectid>GraphicAndType</effectid>
    <name>Source Text</name>
    <hash>11111111-4444</hash>
    <value>{val3}</value>
</effect>
</xsequence>"""

    xml_file = tmp_path / "test_timeline.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    out_report = tmp_path / "analyze_report.txt"

    orig_root = paths.root

    def _mock_root(name: str = "") -> str:
        if name == "analyze_report.txt":
            return str(out_report)
        return orig_root(name)

    monkeypatch.setattr(paths, "root", _mock_root)
    monkeypatch.setattr(sys, "argv", ["analyze_blobs.py", str(xml_file)])

    if "tools.analyze_blobs" in sys.modules:
        del sys.modules["tools.analyze_blobs"]

    mod = importlib.import_module("tools.analyze_blobs")
    captured = capsys.readouterr()
    assert "report written" in captured.out

    assert out_report.is_file()
    report_text = out_report.read_text(encoding="utf-8")
    assert "matched effect blocks: 3" in report_text
    assert "equal-length substitution test" in report_text
    assert mod is not None
