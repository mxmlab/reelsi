# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Восстановление вставки с флагом «без фона» (`core/recover.py`, `_card_media`).

В сборке фото с «без фона» уезжает в `.jsx` с путём кэша `<исходник>.nobg.png`. Восстановление
обязано вернуть карточку к тому, что видел юзер: исходник и флаг nobg. Вставка на подложке
кэш несёт штатно — её не трогаем. Исходника на диске нет — кэш остаётся как есть.

Запуск: py -3.10 -m pytest tests/test_recover_nobg.py -q -p no:cacheprovider
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import recover  # noqa: E402


def _touch(path):
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    return str(path)


def _jsx(media, **extra):
    base = {"t": "photo", "media": media, "start": 6.0, "end": 8.5, "x": 0, "y": 0,
            "sc": 100, "sin": 0, "mw": 100, "mh": 100}
    base.update(extra)
    return base


def test_nobg_cache_is_restored_to_source_and_flag(tmp_path):
    """Кэш `<исходник>.nobg.png` с исходником на диске -> карточка с исходником и nobg=True."""
    src = _touch(tmp_path / "pill.png")
    cache = _touch(tmp_path / "pill.png.nobg.png")
    out = recover.merge_inserts([], [_jsx(cache, nobg=True)])
    assert out[0]["media"] == src, out[0]["media"]
    assert out[0]["nobg"] is True


def test_nobg_cache_without_source_stays_as_is(tmp_path):
    """Исходника нет — кэш остаётся путём карточки (он уже без фона), флаг не ставим."""
    cache = _touch(tmp_path / "gone.png.nobg.png")
    out = recover.merge_inserts([], [_jsx(cache, nobg=True)])
    assert out[0]["media"] == cache
    assert out[0]["nobg"] is False


def test_plate_cache_is_not_touched(tmp_path):
    """Вставка на подложке: кэш — её штатный путь, исходник на диске не подменяем."""
    _touch(tmp_path / "plate_photo.png")
    cache = _touch(tmp_path / "plate_photo.png.nobg.png")
    out = recover.merge_inserts([], [_jsx(cache, plate=True, nobg=True)])
    assert out[0]["media"] == cache
    assert out[0]["nobg"] is False


def test_plain_insert_keeps_media_and_no_flag(tmp_path):
    """Обычная вставка без кэша: путь как был, флага nobg нет (False)."""
    src = _touch(tmp_path / "plain.png")
    out = recover.merge_inserts([], [_jsx(src)])
    assert out[0]["media"] == src
    assert out[0]["nobg"] is False


def test_bare_markup_has_no_nobg(tmp_path):
    """Разметка без .jsx: файла нет, флаг false — и ключ есть у всех карточек одинаково."""
    side = [{"phrase": "ФЛАКОН", "type": "photo", "start_sec": 6.0, "duration_sec": 2.5}]
    out = recover.merge_inserts(side, [])
    assert out[0]["media"] == "" and out[0]["nobg"] is False
