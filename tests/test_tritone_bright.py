# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тритон на жёлтом интро не ставится на ярком цвете (задание ZN).

Tritone красит по яркости: цвет мидтонов уезжает на букву, Highlights остаётся белым.
Свечение выталкивает букву почти в белое — она попадает в Highlights и выбеливается
вместо того, чтобы встать в мидтоны. Поэтому при яркости цвета мидтонов
(Rec.709, 0.2126R+0.7152G+0.0722B по значениям 0–1) выше TRITONE_MAX_LUM тритон не
ставится вовсе — обе подстановки пустые, а свечение (Glo2) и всё остальное остаются
как были.

Что проверяем:
  * `_tritone_on` — порог: жёлтый интро Джаггера 0.91 и жёлтый по умолчанию 0.87
    выключены, голубой 0.61 и красный 0.24 включены, белый 1.0 выключен;
  * сборка с жёлтым интро: яркий `intro_hl_fill` — в .jsx нет `ADBE Tritone` ни в
    ветке свечения/глитча (introAnimFX), ни в introHlGlow, а Glo2 на месте; тёмный
    (красный) — тритон есть в обеих подстановках, после Glo2;
  * без `intro_hl_fill` в стиле цвет мидтонов — дефолтный `hl_fill` [1,0.9176,0] (0.87),
    то есть яркий: тритона в .jsx нет (это и есть задуманная смена поведения).

Входные данные интро — как в соседних тестах интро (tests/test_intro_glow_tritone.py,
tests/test_intro_hl_glow.py): фикстура timeline_subs.xml.gz и строки интро со свечением.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import xml2ae  # noqa: E402
from core.xml2ae.build import TRITONE_MAX_LUM, _tritone_on  # noqa: E402

T_CAM1 = 1.0

# Цвета из замера 2026-09-18 (яркость Rec.709 в комментарии).
YELLOW_JAGGER = [1.0, 0.9686, 0.0784]      # 0.91 — выкл
YELLOW_DEFAULT = [1, 0.9176, 0]            # 0.87 — выкл
CYAN = [0.0, 0.749, 1.0]                   # 0.61 — вкл
RED = [0.6863, 0.1216, 0.1216]             # 0.24 — вкл
WHITE = [1.0, 1.0, 1.0]                    # 1.00 — выкл


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode="word", disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _intro():
    """Две группы (intro_splits=[1]), чтобы в сборке были ОБЕ подстановки тритона:
    жёлтая строка со свечением — ветка introAnimFX, жёлтая без свечения и глитча —
    ветка introHlGlow (её зовут только там, где группа без глитча и строка без свечения)."""
    return [
        dict(words=["СВЕЧЕНИЕ"], color="yellow", times=[T_CAM1], fx="glow"),
        dict(words=["ЯРКОЕ"], color="yellow", times=[T_CAM1 + 0.4]),
    ]


def test_порог_яркости_цвета_мидтонов():
    """`_tritone_on`: ярче 0.7 — не ставить. Яркости цветов — из замера 2026-09-18."""
    assert TRITONE_MAX_LUM == 0.7
    assert _tritone_on(YELLOW_JAGGER) is False
    assert _tritone_on(RED) is True
    assert _tritone_on(CYAN) is True
    assert _tritone_on(WHITE) is False
    # дефолт стиля: hl_fill пустой — тот же жёлтый, что подставляет _fill_js(None)
    assert _tritone_on(YELLOW_DEFAULT) is False
    assert _tritone_on(None) is False


def test_яркий_жёлтый_тритона_нет(xml_subs, tmp_path):
    """Яркий intro_hl_fill: обе подстановки тритона пустые, Glo2 и introHlGlow на месте."""
    jsx, _ = _build(xml_subs, tmp_path, _intro(),
                    style={"intro_hl_fill": YELLOW_JAGGER}, name="bright.jsx")

    assert "INTRO_HL_FILL=[1,0.9686,0.0784]" in jsx
    assert "ADBE Tritone" not in jsx
    # свечение — без изменений: ветка fx=="glow" на слове и Glo2 внутри introHlGlow
    assert '} else if(fx=="glow"){' in jsx
    assert "function introHlGlow(L)" in jsx
    assert ('var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149);'
            ' setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);') in jsx


def test_тёмный_красный_тритон_есть(xml_subs, tmp_path):
    """Тёмный (красный) intro_hl_fill: тритон в обеих подстановках, Midtones — им же."""
    jsx, _ = _build(xml_subs, tmp_path, _intro(),
                    style={"intro_hl_fill": RED}, name="dark.jsx")

    assert "INTRO_HL_FILL=[0.6863,0.1216,0.1216]" in jsx
    # ровно две подстановки: introAnimFX (:2268) и introHlGlow (:2478)
    assert jsx.count('addFX(L,"ADBE Tritone")') == 2
    assert 'var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",INTRO_HL_FILL);' in jsx
    assert 'if((anim=="glitch"||fx=="glow") && col=="yellow"){' in jsx
    assert jsx.index('setP(fxGl,"ADBE Glo2-0004",0.62);') < jsx.index('"ADBE Tritone"')


def test_дефолтный_жёлтый_ярче_порога_тритона_нет(xml_subs, tmp_path):
    """Без intro_hl_fill цвет мидтонов — дефолтный hl_fill [1,0.9176,0] (0.87): выкл."""
    jsx, _ = _build(xml_subs, tmp_path, _intro(), style={}, name="default.jsx")

    assert "var HL_FILL = [1,0.9176,0];" in jsx
    assert "ADBE Tritone" not in jsx
    assert 'setP(fxGl,"ADBE Glo2-0004",0.62);' in jsx
