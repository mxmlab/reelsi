# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Запретные зоны вставок: вставка из зоны УДАЛЯЕТСЯ, а не сдвигается.

Баг, ради которого тест: раньше в cmd_inserts стояло `s = max(s0, 6.0)` + каскад
`s = last + 2.5`. Вставка про фразу со 2-й секунды переезжала на 6-ю, где звучит уже
другой текст, и тащила за собой соседей — весь «поезд» вставок начала ролика стоял не
по смыслу («он про то, что говорит на 2 сек, ставит на 7 сек»).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402
from core.aicut import commands as aicut_commands  # noqa: E402


def _ins(t, typ="photo", q="", dur=2.5):
    return {"type": typ, "start_sec": t, "duration_sec": dur, "query": q or f"q{t}"}


def _zones(ins, dur=60.0, occupied=()):
    return aicut._apply_zones(ins, dur, occupied, emit=lambda *a, **k: None)


def test_фото_из_начала_удаляется_а_не_едет_на_шестую():
    out = _zones([_ins(2.0, q="brain"), _ins(20.0, q="salt")])
    assert [round(x["start_sec"], 2) for x in out] == [20.0]
    assert all(x["query"] != "brain" for x in out)


def test_соседи_не_сдвигаются_следом_за_выброшенной():
    """Главное следствие бага: уцелевшие вставки стоят ровно там, где их фразы."""
    out = _zones([_ins(1.0), _ins(3.0), _ins(7.0), _ins(9.9), _ins(30.0)])
    assert [round(x["start_sec"], 2) for x in out] == [7.0, 9.9, 30.0]


def test_видео_разрешено_только_с_десятой():
    out = _zones([_ins(7.0, "video"), _ins(12.0, "video")])
    assert [round(x["start_sec"], 2) for x in out] == [12.0]


def test_слипшиеся_вставки_режутся_а_не_раздвигаются():
    out = _zones([_ins(20.0), _ins(21.0), _ins(23.0)])
    assert [round(x["start_sec"], 2) for x in out] == [20.0, 23.0]


def test_рядом_с_уже_выбранной_при_доборе():
    out = _zones([_ins(20.0), _ins(31.0)], occupied=[19.0])
    assert [round(x["start_sec"], 2) for x in out] == [31.0]


def test_у_конца_ролика_режется_длительность_а_не_старт():
    out = _zones([_ins(58.0, dur=4.0)], dur=60.0)
    assert round(out[0]["start_sec"], 2) == 58.0
    assert round(out[0]["duration_sec"], 2) == 2.0


def test_совсем_в_хвосте_вставка_выбрасывается():
    assert _zones([_ins(59.5)], dur=60.0) == []


# ---- Постоянная цель вставок и запретная зона конца числом --------------------

def test_ins_target_постоянно_13():
    for duration in (0, 60, 90, 150):
        assert aicut.ins_target(duration) == 13


def test_ins_target_монотонна():
    prev = -1
    for d in range(0, 600):
        v = aicut.ins_target(d / 10.0)
        assert v >= prev
        prev = v


def test_cap_by_quota_keeps_unknown_type_as_photo():
    items = [_ins(10 + i * 3, "mystery") for i in range(11)]
    out = aicut_commands._cap_by_quota(items, 10, 3)
    assert len(out) == 10
    assert all(x["type"] == "mystery" for x in out)


def test_cap_by_quota_spreads_excess_and_keeps_chronology():
    items = [_ins(10 + i * 3, "photo") for i in range(14)]
    items += [_ins(60 + i * 3, "video") for i in range(5)]
    out = aicut_commands._cap_by_quota(items, 10, 3)
    photos = [x for x in out if x["type"] == "photo"]
    videos = [x for x in out if x["type"] == "video"]
    assert len(photos) == 10 and len(videos) == 3
    assert photos[0]["start_sec"] == 10 and photos[-1]["start_sec"] == 49
    assert videos[0]["start_sec"] == 60 and videos[-1]["start_sec"] == 72
    assert [x["start_sec"] for x in out] == sorted(x["start_sec"] for x in out)
    assert aicut_commands._cap_by_quota(items[:14], 1, 0)[0]["start_sec"] == 31


def test_ins_end_sec_границы_разные_на_разной_длине():
    assert aicut.ins_end_sec(60) == 4.0
    assert aicut.ins_end_sec(100) == 6.0
    assert round(aicut.ins_end_sec(120), 1) == 7.2
    assert aicut.ins_end_sec(60) != aicut.ins_end_sec(120)
    assert aicut.ins_end_sec(150) == 9.0   # потолок 9 с
    assert aicut.ins_end_sec(30) == 4.0    # пол 4 с


def test_индекс_зоны_конца_считается_от_границы():
    words = [(i, f"w{i}", float(i), float(i + 1)) for i in range(150)]
    assert aicut._end_zone_word(words, 60.0) == 56     # 60.0 - 4.0 = 56.0 с
    assert aicut._end_zone_word(words, 120.0) == 113   # 120.0 - 7.2 = 112.8 с
    assert aicut._end_zone_word(words, 150.0) == 141   # 150.0 - 9.0 = 141.0 с


def test_зона_конца_идёт_от_границы_а_не_константой():
    words = [(i, f"w{i}", float(i), float(i + 1)) for i in range(150)]
    z60 = aicut._end_zone_word(words, 60.0)
    z120 = aicut._end_zone_word(words, 120.0)
    assert z120 > z60                    # у длинного ролика граница позже, чем у короткого
    assert z60 is not None and z120 is not None
