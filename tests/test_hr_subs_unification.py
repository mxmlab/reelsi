# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты объединения сборки дорожки субтитров и правила раскладки (задание HR).

1. Единая сборка дорожки субтитров:
   - xmlbuild.build_subtitle_track(sub_words, start_id) -> (vtrack_xml, n_subs, long_words, next_id)
   - subtitle_xml._build_subtitle_track делегирует в xmlbuild.build_subtitle_track
   - Слова длиннее SUB_FIT_CHARS (14) получают font scaling (scale < 100.0) в обоих маршрутах
     (включая веб-маршрут), а не остаются с scale=100.0.

2. Единое правило раскладки:
   - align.map_words_to_clips — общее ядро раскладки (середина слова в кадрах источника)
   - subtitle_xml.map_words_to_clips и align.map_words работают через него
   - Слово, пересекающее границу реза с серединой вне оставленного сегмента, отбрасывается
     в обоих маршрутах одинаково.
   - Внутри сегментов результаты раскладки совпадают.
"""
import re
from core import align, subtitle_xml, xmlbuild


def test_build_subtitle_track_long_word_scales_in_web_route():
    """Слово длиннее SUB_FIT_CHARS (14) получает scale < 100 в веб-маршруте."""
    # 17 символов: scale должен быть round(14 / 17 * 100, 1) == 82.4
    words = [{"w": "СУПЕРДЛИННОЕСЛОВО", "start": 0, "end": 10}]
    vtrack, n, longs = subtitle_xml._build_subtitle_track(words, 1000)
    assert n == 1
    assert longs == []
    # В XML должен быть масштаб 82.4 вместо стандартного 100.
    assert "82.4" in vtrack, f"Масштаб 82.4 не найден в дорожке: {vtrack}"
    # Проверяем, что масштаб находится именно в свойстве Scale
    m = re.search(r'<name>Scale</name>.*?<value>-?\d+,([0-9.]+),', vtrack, re.S)
    assert m is not None, "Свойство Scale не найдено в XML"
    assert float(m.group(1)) == 82.4


def test_build_subtitle_track_returns_expected_tuple():
    """xmlbuild.build_subtitle_track возвращает (vtrack_xml, n_subs, long_words, next_id)."""
    words = [
        {"w": "первое", "start": 0, "end": 10},
        {"w": "второе", "start": 10, "end": 20},
    ]
    vtrack, n_subs, long_words, next_id = xmlbuild.build_subtitle_track(words, start_id=42)
    assert n_subs == 2
    assert long_words == []
    assert next_id == 44
    assert '<clipitem id="clipitem-42">' in vtrack
    assert '<clipitem id="clipitem-43">' in vtrack


def test_map_words_midpoint_outside_cut_dropped_identically():
    """Слово на стыке реза с серединой вне куска отбрасывается в обоих маршрутах."""
    # Сегмент 2.0..5.0с. Слово 1.7..2.1с (середина 1.9с — в вырезанной зоне).
    words = [{"w": "граница", "start": 1.7, "end": 2.1}]
    segments = [(2.0, 5.0)]
    clips = [(0, 180, 120, 300, True, 100.0)]  # 2.0..5.0с при 60 fps

    res_cli = align.map_words(words, segments, fps=60)
    res_web = subtitle_xml.map_words_to_clips(words, clips, fps=60)

    assert res_web == [], "Веб-маршрут должен отбросить слово с серединой вне клипа"
    assert res_cli == [], "CLI-маршрут должен отбросить слово с серединой вне сегмента"


def test_map_words_inside_segments_match_clips():
    """Слова внутри сегментов раскладываются одинаково в align.map_words и map_words_to_clips."""
    words = [
        {"w": "первое", "start": 1.0, "end": 1.5},
        {"w": "второе", "start": 2.0, "end": 2.5},
        {"w": "третье", "start": 5.2, "end": 5.8},
    ]
    # Два сегмента: 0.5..3.0с (150 кадров) и 5.0..6.5с (90 кадров)
    segments = [(0.5, 3.0), (5.0, 6.5)]
    # Соответствующие clips:
    # клип 1: start=0, end=150, in=30, out=180
    # клип 2: start=150, end=240, in=300, out=390
    clips = [
        (0, 150, 30, 180, True, 100.0),
        (150, 240, 300, 390, True, 100.0),
    ]

    res_cli = align.map_words(words, segments, fps=60)
    res_web = subtitle_xml.map_words_to_clips(words, clips, fps=60)

    assert len(res_cli) == 3
    assert res_cli == res_web
