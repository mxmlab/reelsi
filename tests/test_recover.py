# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Восстановление вставок клипа с диска (`core/recover.py`).

Сайдкар `.inserts.json` держит разметку (фраза/запрос/тайминг), собранный `.jsx`
— выбранный файл и геометрию. Слияние должно дать карточку шага 2 с обоими, а
вставка без `.jsx` — вернуться хотя бы разметкой (без файла).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import recover  # noqa: E402


def _write(p, text):
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_merge_takes_media_from_jsx_and_markup_from_sidecar():
    side = [{"phrase": "ФЛАКОН", "type": "photo", "start_sec": 6.0,
             "duration_sec": 2.5, "query": "pill bottle", "prompt": "Флакон"}]
    jsx = [{"t": "photo", "media": "C:/ins/pill.png", "start": 6.0, "end": 8.5,
            "x": 0, "y": 0, "sc": 100, "sin": 0, "mw": 100, "mh": 100}]
    out = recover.merge_inserts(side, jsx)
    assert len(out) == 1
    assert out[0]["media"] == "C:/ins/pill.png"     # файл — из .jsx
    assert out[0]["phrase"] == "ФЛАКОН"             # разметка — из сайдкара
    assert out[0]["query"] == "pill bottle"
    assert out[0]["duration_sec"] == 2.5            # end - start


def test_sidecar_without_jsx_returns_markup_without_media():
    side = [{"phrase": "БЛАНК", "type": "photo", "start_sec": 3.0, "duration_sec": 2.5}]
    out = recover.merge_inserts(side, [])
    assert len(out) == 1 and out[0]["media"] == "" and out[0]["phrase"] == "БЛАНК"


def test_merge_sorted_by_start_and_keeps_unmatched_jsx():
    side = [{"type": "photo", "start_sec": 20.0}]
    jsx = [{"t": "video", "media": "v.mp4", "start": 5.0, "end": 8.0},
           {"t": "photo", "media": "p.png", "start": 20.0, "end": 22.5}]
    out = recover.merge_inserts(side, jsx)
    assert [round(x["start_sec"], 1) for x in out] == [5.0, 20.0]
    assert out[0]["type"] == "video" and out[0]["media"] == "v.mp4"
    assert out[1]["media"] == "p.png"


def test_recover_inserts_from_files(tmp_path):
    xml = str(tmp_path / "01_C1381.xml")
    _write(xml, "<x/>")
    _write(os.path.splitext(xml)[0] + ".inserts.json",
           json.dumps({"inserts": [{"phrase": "ФЛАКОН", "type": "photo",
                                    "start_sec": 6.0, "duration_sec": 2.5,
                                    "query": "pill bottle"}]}))
    _write(os.path.splitext(xml)[0] + ".jsx",
           'var INSERTS=[{"t":"photo","media":"C:/ins/pill.png","start":6.0,"end":8.5,'
           '"mw":100,"mh":100}];\nvar OTHER=[1,2];')
    out = recover.recover_inserts(xml)
    assert len(out) == 1 and out[0]["media"] == "C:/ins/pill.png"
    assert out[0]["phrase"] == "ФЛАКОН"


def test_recover_missing_everything_is_empty(tmp_path):
    xml = str(tmp_path / "none.xml")
    _write(xml, "<x/>")
    assert recover.recover_inserts(xml) == []
    assert recover.read_project_speaker(xml) == ""


def test_jsx_array_with_brackets_in_strings(tmp_path):
    """Массив тянем по балансу скобок, а не регэкспом: внутри вложенные [] и «]» в строке."""
    xml = str(tmp_path / "c.xml")
    _write(xml, "<x/>")
    _write(os.path.splitext(xml)[0] + ".jsx",
           'var INSERTS=[{"t":"photo","media":"a]b.png","start":1.0,"end":3.0,'
           '"anim":{"position":[[1.0,[0,5]],[3.0,[0,-5]]]}}];\nvar X=1;')
    out = recover.read_jsx_inserts(xml)
    assert len(out) == 1 and out[0]["media"] == "a]b.png"
