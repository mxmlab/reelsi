# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания CN: галка «наезд в начале» (cam1_zoom_start).

При cam1_zoom_start=True (дефолт):
  - поведение совпадает с прежним побайтово во всех трёх режимах (pulse, jump, drift).

При cam1_zoom_start=False:
  - в кадре 0 масштаб ровно 100% во всех трёх режимах;
  - pulse: вместо пары (0, big) + (punch, 100) — один ключ (0, 100), возвраты работают как раньше;
  - drift: старт со (0, 100.0, 0), дальше дрейф к цели;
  - jump: в кадре 0 значение 100 вместо случайного;
  - none: галка скрыта, зума нет как и раньше.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import ZOOM_BIG  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build_jsx(xml, out_name, style=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=out_name,
                                   style=style or {}, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _cam1_scale_from_jsx(jsx):
    m = re.search(r"var CAM1_SCALE\s*=\s*(\[.*?\]);", jsx)
    assert m, "var CAM1_SCALE не найден в JSX"
    return json.loads(m.group(1))


def test_cam1_zoom_start_default_matches_previous(xml_subs, tmp_path):
    """С галкой (по умолчанию) .jsx совпадает побайтово для всех режимов."""
    for mode in ("pulse", "jump", "drift"):
        jsx_implicit = _build_jsx(xml_subs, str(tmp_path / f"out_{mode}_impl.jsx"),
                                  style={"cam1_zoom": mode})
        jsx_explicit = _build_jsx(xml_subs, str(tmp_path / f"out_{mode}_expl.jsx"),
                                  style={"cam1_zoom": mode, "cam1_zoom_start": True})
        assert jsx_implicit == jsx_explicit, f"Не совпадает для режима {mode}"


def test_cam1_zoom_start_pulse_keys(xml_subs, tmp_path):
    """В режиме pulse при cam1_zoom_start=False: кадр 0 = 100%, ключей на 1 меньше."""
    jsx_start_true = _build_jsx(xml_subs, str(tmp_path / "pulse_true.jsx"),
                                style={"cam1_zoom": "pulse", "cam1_zoom_start": True})
    keys_true = _cam1_scale_from_jsx(jsx_start_true)
    assert keys_true[0] == [0, ZOOM_BIG]
    assert keys_true[1][1] == 100.0

    jsx_start_false = _build_jsx(xml_subs, str(tmp_path / "pulse_false.jsx"),
                                 style={"cam1_zoom": "pulse", "cam1_zoom_start": False})
    keys_false = _cam1_scale_from_jsx(jsx_start_false)
    assert keys_false[0] == [0, 100.0] or keys_false[0] == [0, 100]
    assert len(keys_false) == len(keys_true) - 1
    # Последующие возвраты идентичны
    assert keys_false[1:] == keys_true[2:]


def test_cam1_zoom_start_drift_keys(xml_subs, tmp_path):
    """В режиме drift при cam1_zoom_start=False: кадр 0 = 100%, ключей на 1 меньше."""
    jsx_start_true = _build_jsx(xml_subs, str(tmp_path / "drift_true.jsx"),
                                style={"cam1_zoom": "drift", "cam1_zoom_start": True})
    keys_true = _cam1_scale_from_jsx(jsx_start_true)
    assert keys_true[0][0] == 0
    assert keys_true[0][1] == ZOOM_BIG
    assert keys_true[0][2] == 1

    jsx_start_false = _build_jsx(xml_subs, str(tmp_path / "drift_false.jsx"),
                                 style={"cam1_zoom": "drift", "cam1_zoom_start": False})
    keys_false = _cam1_scale_from_jsx(jsx_start_false)
    assert keys_false[0][0] == 0
    assert keys_false[0][1] == 100.0
    assert keys_false[0][2] == 0  # обычный ease
    assert len(keys_false) == len(keys_true) - 1


def test_cam1_zoom_start_jump_keys(xml_subs, tmp_path):
    """В режиме jump при cam1_zoom_start=False: кадр 0 = 100%, количество ключей не меняется."""
    jsx_start_true = _build_jsx(xml_subs, str(tmp_path / "jump_true.jsx"),
                                style={"cam1_zoom": "jump", "cam1_zoom_start": True})
    keys_true = _cam1_scale_from_jsx(jsx_start_true)
    assert keys_true[0][0] == 0

    jsx_start_false = _build_jsx(xml_subs, str(tmp_path / "jump_false.jsx"),
                                 style={"cam1_zoom": "jump", "cam1_zoom_start": False})
    keys_false = _cam1_scale_from_jsx(jsx_start_false)
    assert keys_false[0] == [0, 100.0] or keys_false[0] == [0, 100]
    assert len(keys_false) == len(keys_true)


def test_ui_index_html_checkbox_cam1_zoom_start():
    """Галка «наезд в начале» есть в схеме и прячется при cam1_zoom='none' (задание JB п. 6).

    Раньше галка жила в разметке (id st_cam1zoomstart, обёртка cam1zoomstartwrap),
    которую правил fillStyleFields; теперь поле строит панель по core/style_schema.py,
    а видимость считает show_if — то же правило, что прятало обёртку.
    """
    field = watcher.schema_field("cam1_zoom_start")
    assert field, "в схеме пропало поле cam1_zoom_start"
    assert field["ctl"] == "bool", "cam1_zoom_start перестал быть галкой"
    assert field.get("show_if") == {"key": "cam1_zoom", "ne": "none"}, (
        "галка «наезд в начале» больше не прячется при cam1_zoom='none'")
