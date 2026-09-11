# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FE: масштаб субтитров — ключ стиля вместо ручного Scale в AE.

Субтитры собираются в прекомп, и слой прекомпа в AE правился руками (Scale) —
в интерфейсе ручки не было. Новый ключ sub_scale (%, дефолт 100) — масштаб
СЛОЯ прекомпа: раскладка внутри (перенос строк, автоподжатие, шаг стопки) не
меняется ни на пиксель. Главная тонкость — якорь: при scale != 100 якорь и
позицию слоя надо перенести в точку строки [W/2, POSY], иначе масштаб от
центра кадра утащит строку к середине и sub_y начнёт врать. При sub_scale=100
собранный .jsx обязан остаться прежним до байта (golden).

Запуск: python -m pytest reelsi/tests -q
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.build import scene_plan  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, out, style):
    return xml2ae.to_ae_full(xml, out, style=style, roto=False,
                             emit=lambda *a, **k: None)


def test_style_default_sub_scale():
    """sub_scale по умолчанию 100 — сегодняшнее поведение."""
    assert styles.BASE.get("sub_scale") == 100.0
    assert styles.resolve(None).get("sub_scale") == 100.0


def test_sub_scale_100_jsx_identical(xml_subs, tmp_path):
    """При sub_scale=100 собранный .jsx побайтово равен сборке без ключа (golden)."""
    out_def = str(tmp_path / "def.jsx")
    out_100 = str(tmp_path / "s100.jsx")
    _build(xml_subs, out_def, {})
    _build(xml_subs, out_100, {"sub_scale": 100})
    a = open(out_def, encoding="utf-8-sig").read()
    b = open(out_100, encoding="utf-8-sig").read()
    assert a == b, "sub_scale=100 изменил .jsx — golden нарушен"


def test_sub_scale_70_sets_anchor_position_scale(xml_subs, tmp_path):
    """При sub_scale=70: Scale слоя прекомпа 70, якорь [SW/2, POSY] и позиция [W/2, POSY]."""
    out = str(tmp_path / "s70.jsx")
    _build(xml_subs, out, {"sub_scale": 70})
    txt = open(out, encoding="utf-8-sig").read()
    assert 'setValue([70,70])' in txt, "Scale слоя прекомпа не 70"
    # якорь (центр широкого прекомпа) и позиция (в кадре) — в точку строки (posy из плана)
    assert 'ADBE Anchor Point").setValue([SW/2, ' in txt, "якорь не в точке строки"
    assert 'ADBE Position").setValue([W/2, ' in txt, "позиция не в точке строки"


def _bg_center(txt):
    """Центр плашки субтитров по формуле экрана AE.

    Плашка — shape-слой: прямоугольник нарисован вокруг ЛОКАЛЬНОГО (0,0), центр —
    P_local=(0,0). Экран = Position + (P_local - Anchor) * Scale. Из .jsx берём
    исходную Position.y плашки (BG_Y из sub_bg_js) и присваивания масштаба
    (Anchor/Position/Scale у bgLayer), считаем, где окажется центр."""
    import re
    # исходная позиция плашки (без масштаба) — из sub_bg_js
    m = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, ([0-9.]+)\]\);', txt)
    assert m, "Position.y плашки (BG_Y) не нашлась"
    bg_y = float(m.group(1))
    # присваивания масштаба у bgLayer (если sub_scale != 100)
    m_a = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Anchor Point"\)\.setValue\(\[([^\]\n]+)\]\);', txt)
    m_p = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, ([0-9.]+)\]\);', txt)
    m_s = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Scale"\)\.setValue\(\[([0-9.]+),[0-9.]+\]\);', txt)
    if not m_a:
        # масштаба нет — центр в исходной позиции
        return 540.0, bg_y
    # Anchor может быть выражением [0, POSY-BG_Y] или [W/2, POSY] с подставленными числами
    def _num(s):
        s = s.strip()
        if s == "W/2":
            return 540.0
        return float(s) if re.fullmatch(r"-?[0-9.]+", s) else float(eval(s))
    ax, ay = (_num(x) for x in m_a.group(1).split(","))
    # Position/Scale плашки берём ПОСЛЕ Anchor (в sub_scale_js), а не исходные из sub_bg_js
    tail = txt[m_a.end():]
    m_p = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, ([0-9.]+)\]\);', tail)
    m_s = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Scale"\)\.setValue\(\[([0-9.]+),[0-9.]+\]\);', tail)
    if not (m_p and m_s):
        return 540.0, bg_y
    px, py = 540.0, float(m_p.group(1))
    s = float(m_s.group(1)) / 100.0
    return px + (0 - ax) * s, py + (0 - ay) * s


def test_sub_scale_100_bg_center_unchanged(xml_subs, tmp_path):
    """При sub_scale=100 центр плашки — на исходном месте (bg_y), ничего не сдвинуто.

    ДО фикса bgLayer получал якорь [W/2, POSY] и уезжал в левый верхний угол; тест
    должен падать на сломанном коде и проходить после."""
    out_def = str(tmp_path / "def.jsx")
    out = str(tmp_path / "s100.jsx")
    _build(xml_subs, out_def, {"sub_bg": True})
    _build(xml_subs, out, {"sub_scale": 100, "sub_bg": True})
    def_txt = open(out_def, encoding="utf-8-sig").read()
    txt = open(out, encoding="utf-8-sig").read()
    cx, cy = _bg_center(txt)
    _, bg_y = _bg_center(def_txt)
    assert (cx, cy) == (540.0, bg_y), f"центр плашки при 100 сдвинулся: {(cx, cy)}"


def test_sub_scale_70_bg_center_pulls_to_line(xml_subs, tmp_path):
    """При sub_scale=70 центр плашки подтягивается к строке: POSY - (POSY-bg_y)*0.7.

    Формула экрана Position+(P_local-Anchor)*Scale, P_local=(0,0). Якорь плашки
    [0, POSY-bg_y], позиция [W/2, POSY] -> центр_y = POSY - (POSY-bg_y)*0.7,
    центр_x = W/2 (не уезжает влево). ДО фикса (якорь [W/2, POSY]) центр_x уезжал
    на W/2*(1-0.7) влево — тест падал."""
    out_def = str(tmp_path / "def.jsx")
    out = str(tmp_path / "s70.jsx")
    _build(xml_subs, out_def, {"sub_bg": True})
    _build(xml_subs, out, {"sub_scale": 70, "sub_bg": True})
    def_txt = open(out_def, encoding="utf-8-sig").read()
    txt = open(out, encoding="utf-8-sig").read()
    _, bg_y = _bg_center(def_txt)          # исходный центр плашки (Position.y)
    # ожидаемый центр при 70: x = W/2, y = POSY - (POSY - bg_y)*0.7
    posy = _posy_from_jsx(txt)             # POSY из .jsx с масштабом (там subLayer Position)
    exp_y = round(posy - (posy - bg_y) * 0.7, 2)
    cx, cy = _bg_center(txt)
    assert cx == 540.0, f"центр плашки уехал по X: {cx} (ожидалось 540)"
    assert cy == pytest.approx(exp_y, abs=0.5), f"центр плашки по Y: {cy} (ожидалось ~{exp_y})"


def _posy_from_jsx(txt):
    """POSY из .jsx: подстановка posy у слоя прекомпа субтитров."""
    import re
    m = re.search(r'subLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, ([0-9.]+)\]\);', txt)
    if m:
        return float(m.group(1))
    # масштаба нет — posy нигде не присваивается, берём из исходной позиции плашки
    m2 = re.search(r'bgLayer\.property\("ADBE Transform Group"\)\.property\("ADBE Position"\)\.setValue\(\[W/2, ([0-9.]+)\]\);', txt)
    return float(m2.group(1))


def test_sub_scale_keeps_layout(xml_subs):
    """posy (и раскладка) в плане НЕ меняются от sub_scale: кегль/строки прежние."""
    p_def = scene_plan(xml_subs, style={}, roto=False)
    p_70 = scene_plan(xml_subs, style={"sub_scale": 70}, roto=False)
    p_200 = scene_plan(xml_subs, style={"sub_scale": 200}, roto=False)
    assert p_70["posy"] == p_def["posy"]
    assert p_200["posy"] == p_def["posy"]
    assert p_70["fsize"] == p_def["fsize"]
    assert p_70["sub_scale"] == 70.0
    assert p_200["sub_scale"] == 200.0
