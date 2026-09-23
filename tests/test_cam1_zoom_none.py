# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: режим «нет зума» у Камеры 1 (cam1_zoom='none').

При cam1_zoom='none':
  - у Null Камеры 1 один ключ [(0, 100.0)], движения нет ни в AE, ни в превью;
  - в плане сцены zoom.keys = [(0, 100.0)], зум постоянен (100%) на всей длине;
  - интро и вставки в плане не меняют масштаб от зума со временем;
  - режимы pulse, jump, drift не затронуты.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import EASE_DEFAULT  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


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


def _func(src, name):
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


def test_cam1_zoom_none_jsx_one_key(xml_subs, tmp_path):
    """При cam1_zoom='none' в JSX у CAM1_SCALE ровно 1 ключ со значением 100."""
    jsx_pulse = _build_jsx(xml_subs, str(tmp_path / "out_pulse.jsx"), style={"cam1_zoom": "pulse"})
    keys_pulse = _cam1_scale_from_jsx(jsx_pulse)
    assert len(keys_pulse) > 1  # 13 ключей на этом таймлайне

    jsx_none = _build_jsx(xml_subs, str(tmp_path / "out_none.jsx"), style={"cam1_zoom": "none"})
    keys_none = _cam1_scale_from_jsx(jsx_none)
    assert keys_none == [[0, 100]] or keys_none == [[0, 100.0]]
    assert len(keys_none) == 1


def test_cam1_zoom_none_scene_plan(xml_subs):
    """План сцены при cam1_zoom='none' содержит один ключ 100.0 и holds=[0]."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", style={"cam1_zoom": "none"})
    zoom = plan["zoom"]
    assert zoom["keys"] == [(0, 100.0)]
    assert zoom["holds"] == [0]
    assert zoom["fit"] == 100.0
    assert zoom["ease"] == [[EASE_DEFAULT, EASE_DEFAULT]]


def test_existing_zoom_modes_unaffected(xml_subs, tmp_path):
    """Режимы pulse, jump, drift работают как раньше; pulse остаётся дефолтом."""
    jsx_default = _build_jsx(xml_subs, str(tmp_path / "out_def.jsx"), style={})
    jsx_pulse = _build_jsx(xml_subs, str(tmp_path / "out_pulse.jsx"), style={"cam1_zoom": "pulse"})
    assert jsx_default == jsx_pulse

    jsx_jump = _build_jsx(xml_subs, str(tmp_path / "out_jump.jsx"), style={"cam1_zoom": "jump"})
    assert "var CAM1_HOLD=" not in jsx_jump
    assert "var CAM1_HOLDS=" in jsx_jump

    jsx_drift = _build_jsx(xml_subs, str(tmp_path / "out_drift.jsx"), style={"cam1_zoom": "drift"})
    keys_drift = _cam1_scale_from_jsx(jsx_drift)
    assert any(len(k) > 2 for k in keys_drift)  # 3-элементные ключи дрейфа


def test_ui_index_html_option_none():
    """Пункт 'none' есть у поля cam1_zoom в схеме панели.

    Селектор режима зума строит панель по core/style_schema.py, поэтому пункты
    проверяются там, а не в разметке: id старой разметки (st_cam1zoom) больше нет.
    """
    field = watcher.schema_field("cam1_zoom")
    assert field, "в схеме пропало поле cam1_zoom"
    assert field["ctl"] == "select", "cam1_zoom перестал быть селектом"
    options = [o[0] for o in field["options"]]
    for mode in ("none", "pulse", "jump", "drift"):
        assert mode in options, f"в схеме cam1_zoom нет режима {mode!r}"


@node
def test_node_keys_at_constant_scale():
    """В JS keysAt на ключе [[0, 100]] даёт 100 на всей длине ролика."""
    js_path = os.path.join(os.path.dirname(HERE), "static", "app", "85-inserts-view.js")
    with open(js_path, "r", encoding="utf-8") as f:
        src = f.read()
    fn_keys_at = _func(src, "keysAt")
    code = f"""
    {fn_keys_at}
    const keys = [[0, 100]];
    const ease = [[33.3333, 33.3333]];
    const res = [
        keysAt(keys, ease, 0, false),
        keysAt(keys, ease, 5.5, false),
        keysAt(keys, ease, 60.0, false)
    ];
    console.log(JSON.stringify(res));
    """
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out == [100, 100, 100]
