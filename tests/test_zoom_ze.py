# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания ZE: «Заполнение кадра» (cam1_fit) в AE = как в превью.

fit переехал в зум нула: ключи зума умножаются на cam1_fit/100 один раз — в
scene_plan, сразу после выбора ключей, — а слои клипа и рото Камеры 1
заполняют кадр ровно (CAM1_FIT=100). Превью читает plan.zoom.fit и ключи и
получает ровно то же произведение (fit/100)·(ключ/100). До задания fit жил в
Scale слоёв клипа и рото: кадр рос вокруг СВОЕГО центра, а вставки кам1 и
интро не росли вовсе — в превью кадр хороший, в AE картинка уезжала на
240–335 px.

1. cam1_fit=150, jump: CAM1_SCALE = ключи при cam1_fit=100, ×1.5 (хвост
   ключа — mode/hold — не трогаем); в .jsx CAM1_FIT=100.
2. cam1_fit=150: plan["zoom"]["fit"] == 100, а ключи плана = ×1.5.
3. cam1_fit=100 — .jsx побайтово как при пустом стиле (идентичность эталону
   golden_geometry.jsx держат test_geometry_python и test_r11_li_every_knob).
4. Автофит интро: _zoom_max получает ключи ×1.5 (через план), а ds при
   fit=150 и ключах 100 равен ds при fit=100 и ключах 150.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, xml2ae  # noqa: E402
from core.xml2ae import build  # noqa: E402
from test_cam1_zoom_start import _build_jsx, _cam1_scale_from_jsx  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_jsx_keys_multiplied_by_fit_and_clip_scale_is_100(xml_subs, tmp_path):
    """1. cam1_fit=150, jump: ключи CAM1_SCALE — те же кадры и тот же хвост, но
    значение ×1.5 от сборки при cam1_fit=100; CAM1_FIT в .jsx — 100 (заполнение
    уехало в ключи, слои клипа и рото больше его не получают)."""
    base = _cam1_scale_from_jsx(_build_jsx(xml_subs, str(tmp_path / "fit100.jsx"),
                                           style={"cam1_zoom": "jump"}))
    jsx150 = _build_jsx(xml_subs, str(tmp_path / "fit150.jsx"),
                        style={"cam1_zoom": "jump", "cam1_fit": 150})
    keys150 = _cam1_scale_from_jsx(jsx150)

    assert len(base) > 1, "jump без ключей — тест ничего не проверяет"
    assert [k[0] for k in keys150] == [k[0] for k in base]
    for got, ref in zip(keys150, base):
        assert got[1] == pytest.approx(round(ref[1] * 1.5, 2))
        assert got[2:] == ref[2:], "хвост ключа (mode/hold) умножению не подлежит"

    assert "var CAM1_FIT=100;" in jsx150
    assert "var CAM1_FIT=150;" not in jsx150


def test_plan_fit_is_100_and_keys_scaled(xml_subs):
    """2. Расписание плана: fit == 100 (превью множит его на ключи), а сами ключи
    плана — ×1.5. Ключи плана читают и превью, и автофит интро, и слежение."""
    p100 = xml2ae.scene_plan(xml_subs, disclaimer="", style={"cam1_zoom": "jump"},
                             emit=lambda *a, **k: None)
    p150 = xml2ae.scene_plan(xml_subs, disclaimer="",
                             style={"cam1_zoom": "jump", "cam1_fit": 150},
                             emit=lambda *a, **k: None)

    assert p100["zoom"]["fit"] == 100
    assert p150["zoom"]["fit"] == 100
    assert len(p100["zoom"]["keys"]) > 1
    assert [k[0] for k in p150["zoom"]["keys"]] == [k[0] for k in p100["zoom"]["keys"]]
    for got, ref in zip(p150["zoom"]["keys"], p100["zoom"]["keys"]):
        assert got[1] == pytest.approx(round(ref[1] * 1.5, 2))


def test_fit_100_is_byte_identical_to_default(xml_subs, tmp_path):
    """3. cam1_fit=100 — умножения нет вовсе: .jsx побайтово как с пустым стилем
    (эталон golden_geometry.jsx стерегут golden-тесты, здесь — что явная сотня
    не включает округление ключей)."""
    empty = _build_jsx(xml_subs, str(tmp_path / "empty.jsx"), style={})
    explicit = _build_jsx(xml_subs, str(tmp_path / "fit100.jsx"),
                          style={"cam1_fit": 100})
    assert empty == explicit


def test_intro_autofit_gets_scaled_keys(xml_subs, monkeypatch):
    """4. Автофит интро считает по ключам с fit (через план): _zoom_max получает
    ×1.5, и ds при fit=150/зум 100 совпадает с ds при fit=100/зум 150 — обе
    сборки дают на кадре один и тот же зум."""
    real_zoom_max = build._zoom_max
    seen = []

    def spy(keys, fps, ts, te, **kw):
        seen.append([(float(k[0]), float(k[1])) for k in keys])
        return real_zoom_max(keys, fps, ts, te, **kw)

    monkeypatch.setattr(build, "_zoom_max", spy)
    # ширины шрифта — программно (1 px на пункт кегля на символ): тест не зависит
    # от того, какие шрифты стоят на машине (как в test_intro_fit)
    monkeypatch.setattr(fonts, "text_width",
                        lambda ps, text, size: float(size) * len(text or ""))

    def plan(fit, zoom):
        return xml2ae.scene_plan(
            xml_subs, disclaimer="", intro_riser=False,
            intro=[dict(words=["A" * 10], color="white", times=[1.0])],
            style={"cam1_fit": fit},
            cam1_scale=[(0, zoom), (300, zoom)],
            emit=lambda *a, **k: None)

    ds150 = plan(150, 100)["intro"][0]["ds"]
    assert seen, "автофит интро не позвал _zoom_max — тест ничего не проверяет"
    assert [v for _f, v in seen[-1]] == [150.0, 150.0], \
        "автофит интро получил ключи без заполнения кадра"
    assert ds150 < 100, "широкая строка обязана ужаться — иначе проверка пустая"

    ds100 = plan(100, 150)["intro"][0]["ds"]
    assert ds150 == pytest.approx(ds100)
