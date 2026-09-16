# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты фейд-аута интро (задание IK):
- единая настройка стиля intro_fade (дефолт 0.35 с);
- обычные группы: окно выхода te не меняется, короче только фейд;
- глитч-группы: спад intro_fade, te = outStart + intro_fade;
- старые ключи intro_fx_fade / intro_fx_fade_last игнорируются;
- в .jsx уходят F_FADE=%(intro_fade)g и Math.max(outStart,outEnd-F_FADE);
- синтаксис собранного .jsx валиден по verify_jsx.check_syntax.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _scene_plan(xml_subs, intro, splits=None, style=None):
    return xml2ae.scene_plan(xml_subs, intro=intro, intro_splits=splits,
                             style=dict(style or {}), disclaimer="",
                             intro_riser=False, emit=lambda *a: None)


def _build(xml, tmp_path, intro, style=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode="word", disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def test_default_style_intro_fade(xml_subs):
    """Стиль без ключа: у обычной группы fade == 0.35, te прежний."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
    ]
    plan = _scene_plan(xml_subs, intro, splits=[1])
    g0, g1 = plan["intro"][0], plan["intro"][1]
    assert g0["ts"] == 0 and g0["te"] == 2.75 and g0["fade"] == 0.35
    assert g1["ts"] == 8.3 and g1["te"] == 10.35 and g1["fade"] == 0.35


def test_custom_intro_fade_and_clamping(xml_subs):
    """intro_fade=0.5 -> fade == 0.5; intro_fade=2.0 -> fade == 0.75 (упор в окно выхода)."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
    ]
    # 0.5 -> 0.5
    plan = _scene_plan(xml_subs, intro, splits=[1], style={"intro_fade": 0.5})
    assert plan["intro"][0]["fade"] == 0.5
    assert plan["intro"][0]["te"] == 2.75
    assert plan["intro"][1]["fade"] == 0.5
    assert plan["intro"][1]["te"] == 10.35

    # 2.0 -> 0.75 (INTRO_F_OUT clamp)
    plan_clamped = _scene_plan(xml_subs, intro, splits=[1], style={"intro_fade": 2.0})
    assert plan_clamped["intro"][0]["fade"] == 0.75
    assert plan_clamped["intro"][0]["te"] == 2.75
    assert plan_clamped["intro"][1]["fade"] == 0.75
    assert plan_clamped["intro"][1]["te"] == 10.35


def test_glitch_groups_intro_fade(xml_subs):
    """Глитч-группа (обычная и «последняя») -> fade == 0.35, te == outStart + 0.35."""
    # Обычная (средняя) глитч-группа: outStart = 2.45 + 0.44 = 2.89
    intro_mid = [
        dict(words=["ПЕРВОЕ", "ВТОРОЕ"], color="white", times=[2.0, 2.45], anim="glitch"),
        dict(words=["БЛИЗКО"], color="white", times=[5.0]),
    ]
    plan_mid = _scene_plan(xml_subs, intro_mid, splits=[1])
    g0 = plan_mid["intro"][0]
    out_start_mid = 2.45 + 0.44
    assert g0["fade"] == 0.35
    assert abs(g0["te"] - (out_start_mid + 0.35)) < 1e-9

    # Последняя глитч-группа: outStart = 8.3 + 0.3 + 1.0 + 0.3 = 9.9
    intro_last = [
        dict(words=["ОБЫЧНОЕ"], color="white", times=[1.0]),
        dict(words=["ГЛИТЧ"], color="white", times=[8.3], anim="glitch"),
    ]
    plan_last = _scene_plan(xml_subs, intro_last, splits=[1])
    g1 = plan_last["intro"][1]
    out_start_last = 8.3 + 0.3 + 1.0 + 0.3
    assert g1["fade"] == 0.35
    assert abs(g1["te"] - (out_start_last + 0.35)) < 1e-9


def test_old_style_keys_ignored(xml_subs):
    """Стиль со старыми intro_fx_fade: 0.9 и intro_fx_fade_last: 0.9 -> всё равно 0.35."""
    intro = [
        dict(words=["ПЕРВОЕ", "ВТОРОЕ"], color="white", times=[2.0, 2.45], anim="glitch"),
        dict(words=["БЛИЗКО"], color="white", times=[5.0]),
    ]
    old_style = {"intro_fx_fade": 0.9, "intro_fx_fade_last": 0.9}
    plan = _scene_plan(xml_subs, intro, splits=[1], style=old_style)
    g0 = plan["intro"][0]
    assert g0["fade"] == 0.35
    assert abs(g0["te"] - (2.45 + 0.44 + 0.35)) < 1e-9

    intro_last = [
        dict(words=["ОБЫЧНОЕ"], color="white", times=[1.0]),
        dict(words=["ГЛИТЧ"], color="white", times=[8.3], anim="glitch"),
    ]
    plan_last = _scene_plan(xml_subs, intro_last, splits=[1], style=old_style)
    g1 = plan_last["intro"][1]
    assert g1["fade"] == 0.35
    assert abs(g1["te"] - (8.3 + 0.3 + 1.0 + 0.3 + 0.35)) < 1e-9


def test_jsx_contains_f_fade_and_keyframe(xml_subs, tmp_path):
    """В .jsx есть F_FADE=0.35 и Math.max(outStart,outEnd-F_FADE), синтаксис валиден."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
    ]
    jsx, jsx_path = _build(xml_subs, tmp_path, intro)
    assert "F_FADE=0.35" in jsx
    assert "Math.max(outStart,outEnd-F_FADE)" in jsx

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"
