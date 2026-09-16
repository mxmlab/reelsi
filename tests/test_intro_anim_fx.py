# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Эффекты появления строк интро в After Effects — глитч, раскрытие, свечение.

Строка интро несёт anim ("glitch" | "reveal") и fx ("glow").
  * golden: без anim/fx .jsx побайтово прежний;
  * anim=="glitch" — в .jsx есть Randomize Order, Random Seed 10 и семь ключей Opacity
    с указанными значениями (+0.0, +0.05, +0.1, +0.1417, +0.1833, +0.225, +0.2667);
  * anim=="reveal" — есть Percent Offset -100/100, Scale 3D [11,11,91.66667],
    Blur 26.8->0 и Scale слоя 70->100;
  * fx=="glow" — есть Glow 149 / 77 / 0.62 без Blur (жёлтая строка — ещё и Tritone, задание G);
  * прекомп с глитчем внутри получает Glow 211 / 93 / 0.42, прекомп без глитча — 42 и
    INTRO_GLOW, как раньше; группе со свечением без глитча Glo2 не добавляется вовсе;
  * сборка .jsx с глитчем проходит проверку синтаксиса через verify_jsx.check_syntax и node --check.
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

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word", name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


def _groups(jsx):
    return json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))


def _plain_intro():
    return [dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
            dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])]


def test_без_anim_fx_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Golden: без anim/fx (или с пустыми строками) .jsx побайтово прежний."""
    jsx_default, _ = _build(xml_subs, tmp_path, _plain_intro(), style={}, name="out_def.jsx")
    jsx_explicit_empty, _ = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="", fx=""),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2], anim="", fx="")
    ], style={}, name="out_empty.jsx")

    assert jsx_explicit_empty == jsx_default
    assert "introAnimFX" not in jsx_default
    assert "ADBE Text Randomize Order" not in jsx_default
    assert "ADBE Text Percent Offset" not in jsx_default
    assert "ADBE Glo2-0002" not in jsx_default
    assert 'try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");' in jsx_default
    assert 'try{ igl.property("Glow Radius").setValue(42); }catch(e){}' in jsx_default
    assert 'try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){} }catch(e){}' in jsx_default


def test_anim_glitch(xml_subs, tmp_path):
    """anim=='glitch': Text Animator со случайными буквами и 7 ключей Opacity слоя без ease."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "glitch"
    assert "anim" not in g2[0]

    # Text Animator
    assert '"ADBE Text Randomize Order"' in jsx
    assert 'adv.property("ADBE Text Randomize Order").setValue(1)' in jsx
    assert '"ADBE Text Random Seed"' in jsx
    assert 'adv.property("ADBE Text Random Seed").setValue(10)' in jsx
    assert '"ADBE Text Percent Start"' in jsx
    assert "pStart.setValueAtTime(t0,0); pStart.setValueAtTime(t0+0.44,100);" in jsx
    assert '"ADBE Text Percent End"' in jsx
    assert "pEnd.setValueAtTime(t0+0.017,100); pEnd.setValueAtTime(t0+0.205,5); pEnd.setValueAtTime(t0+0.392,100);" in jsx
    assert '"ADBE Text Opacity"' in jsx
    assert "aOp.setValue(0)" in jsx

    # 7 ключей Opacity слоя (значения 0, 100, 100, 0, 93, 0, 100)
    assert "op.setValueAtTime(t0,0);" in jsx
    assert "op.setValueAtTime(t0+0.05,100);" in jsx
    assert "op.setValueAtTime(t0+0.1,100);" in jsx
    assert "op.setValueAtTime(t0+0.1417,0);" in jsx
    assert "op.setValueAtTime(t0+0.1833,93);" in jsx
    assert "op.setValueAtTime(t0+0.225,0);" in jsx
    assert "op.setValueAtTime(t0+0.2667,100);" in jsx


def test_anim_reveal(xml_subs, tmp_path):
    """anim=='reveal': Text Animator Percent Offset -100/100, Scale 3D, Gaussian Blur 26.8->0, Scale слоя 70->100."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="reveal"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "reveal"

    # Text Animator
    assert '"ADBE Text Percent Offset"' in jsx
    assert "pOff.setValueAtTime(t0,-100); pOff.setValueAtTime(t0+F_DUR,100);" in jsx
    assert '"ADBE Text Range Shape"' in jsx
    assert 'adv.property("ADBE Text Range Shape").setValue(2)' in jsx
    assert '"ADBE Text Selector Smoothness"' in jsx
    assert 'adv.property("ADBE Text Selector Smoothness").setValue(100)' in jsx
    assert '"ADBE Text Levels Max Ease"' in jsx
    assert 'adv.property("ADBE Text Levels Max Ease").setValue(10)' in jsx
    assert '"ADBE Text Levels Min Ease"' in jsx
    assert 'adv.property("ADBE Text Levels Min Ease").setValue(95)' in jsx
    assert '"ADBE Text Scale 3D"' in jsx
    assert "aSc.setValue([11,11,91.66667]);" in jsx

    # Эффект размытия и Scale слоя
    assert "pBl.setValueAtTime(t0,26.8); pBl.setValueAtTime(t0+F_DUR,0);" in jsx
    assert "sc.setValueAtTime(t0,[70,70]); sc.setValueAtTime(t0+F_DUR,[100,100]);" in jsx
    # Opacity слоя остаётся стандартным
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);" in jsx


def test_anim_reveal_with_back(xml_subs, tmp_path):
    """anim=='reveal' на строке с back=True: Scale анимируется от 0.7*BACK_SCALE до BACK_SCALE (48.3->69)."""
    intro = [
        dict(words=["НЫТИКИ"], color="yellow", times=[T_CAM1], anim="glitch"),
        dict(words=["которые", "оправдывают"], color="white", times=[T_CAM1 + 0.5], back=True, anim="reveal"),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    assert "BACK_SCALE=0.69" in jsx
    assert "introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, ln.back, ln.color);" in jsx
    assert "sc.setValueAtTime(t0,[s0,s0]); sc.setValueAtTime(t0+F_DUR,[s1,s1]);" in jsx
    assert "sc.setValueAtTime(t0,[70,70]); sc.setValueAtTime(t0+F_DUR,[100,100]);" in jsx


def test_fx_glow(xml_subs, tmp_path):
    """fx=='glow': статичное свечение (Glow 149/77/0.62, без Blur — задание G)."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], fx="glow"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("fx") == "glow"

    assert 'setP(fxGb,"ADBE Gaussian Blur 2-0001",3.4);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0002",149);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0003",77);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0004",0.62);' in jsx


def test_прекомп_с_глитчем_и_без_глитча(xml_subs, tmp_path):
    """Прекомп с anim=='glitch' получает усиленный Glow 211/93/0.42, прекомп без глитча — 42 и INTRO_GLOW."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)

    # Проверяем ветвление в шаблоне
    assert 'if(grpGlitch){' in jsx
    assert 'setP(igl,"ADBE Glo2-0002",211); setP(igl,"ADBE Glo2-0003",93); setP(igl,"ADBE Glo2-0004",0.42);' in jsx
    assert 'igl.property("Glow Radius").setValue(42)' in jsx
    assert 'igl.property("Glow Intensity").setValue(INTRO_GLOW)' in jsx


def test_план_сцены_несет_anim_и_fx(xml_subs):
    """scene_plan проносит anim и fx в intro lines для превью."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch", fx="glow"),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], anim="reveal")
    ]
    plan = xml2ae.scene_plan(xml_subs, intro=intro, intro_splits=[1], disclaimer="", style={})
    lines_g1 = plan["intro"][0]["lines"]
    lines_g2 = plan["intro"][1]["lines"]
    assert lines_g1[0].get("anim") == "glitch"
    assert lines_g1[0].get("fx") == "glow"
    assert lines_g2[0].get("anim") == "reveal"


@node
def test_сборка_jsx_с_глитчем_проходит_проверку_синтаксиса(xml_subs, tmp_path):
    """Сборка с anim=='glitch', reveal и glow синтаксически валидна по verify_jsx и node --check."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch", fx="glow"),
        dict(words=["ВТОРОЕ"], color="yellow", times=[T_CAM2], anim="reveal")
    ]
    jsx, jsx_path = _build(xml_subs, tmp_path, intro)

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


def test_line_mode_anim_glitch_and_golden(xml_subs, tmp_path):
    """Построчный режим (intro_mode='line'): golden без anim/fx и introAnimFX(Ll, ...) с глитчем."""
    jsx_def, _ = _build(xml_subs, tmp_path, _plain_intro(), style={}, mode="line", name="line_def.jsx")
    jsx_empty, _ = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="", fx=""),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2], anim="", fx="")
    ], style={}, mode="line", name="line_empty.jsx")
    assert jsx_def == jsx_empty
    assert "introAnimFX" not in jsx_def

    jsx_glitch, _ = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ], style={}, mode="line", name="line_glitch.jsx")
    assert "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null, null, ln.color);" in jsx_glitch


def test_anim_left(xml_subs, tmp_path):
    """anim=='left': стартовый X меньше финишного на ширину слова, два ключа Position, Opacity 0->100, easePair."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="left"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "left"
    assert "anim" not in g2[0]

    # Вызов introAnimFX с шириной слова ww[wj2]
    assert "introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], null, null, null, ln.color);" in jsx

    # Position: стартовый X меньше финишного на ширину слова w
    assert "pos.setValueAtTime(t0, [curX-w, curY]);" in jsx
    assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx
    assert "easePair(pos);" in jsx

    # Opacity: 0 -> 100 на окне F_DUR с easePair
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);" in jsx


def test_anim_right(xml_subs, tmp_path):
    """anim=='right': стартовый X больше финишного на ширину слова, два ключа Position, Opacity 0->100, easePair."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="right"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "right"

    # Position: стартовый X больше финишного на ширину слова w
    assert "pos.setValueAtTime(t0, [curX+w, curY]);" in jsx
    assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx
    assert "easePair(pos);" in jsx

    # Opacity: 0 -> 100 на окне F_DUR с easePair
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);" in jsx


def test_anim_up(xml_subs, tmp_path):
    """anim=='up': стартовый Y больше финишного на HL_RISE, X не меняется, два ключа Position, Opacity 0->100, easePair."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="up"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "up"

    # Position: стартовый Y больше финишного на HL_RISE, X не меняется
    assert "pos.setValueAtTime(t0, [curX, curY+HL_RISE]);" in jsx
    assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx
    assert "easePair(pos);" in jsx

    # Opacity: 0 -> 100 на окне F_DUR с easePair
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);" in jsx


def test_line_mode_anim_left_right_up(xml_subs, tmp_path):
    """Построчный режим (intro_mode='line'): анимации left/right/up работают со слоем строки."""
    for anim_name in ("left", "right", "up"):
        intro = [
            dict(words=["ПЕРВАЯ", "СТРОКА"], color="white", times=[T_CAM1], anim=anim_name),
            dict(words=["ВТОРАЯ", "СТРОКА"], color="white", times=[T_CAM2])
        ]
        jsx, _ = _build(xml_subs, tmp_path, intro, mode="line", name=f"line_{anim_name}.jsx")
        assert "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, null, null, null, ln.color);" in jsx
        assert 'if(typeof w==="undefined" || w===null) w=introW(L);' in jsx

        if anim_name == "left":
            assert "pos.setValueAtTime(t0, [curX-w, curY]);" in jsx
            assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx
        elif anim_name == "right":
            assert "pos.setValueAtTime(t0, [curX+w, curY]);" in jsx
            assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx
        elif anim_name == "up":
            assert "pos.setValueAtTime(t0, [curX, curY+HL_RISE]);" in jsx
            assert "pos.setValueAtTime(t0+F_DUR, [curX, curY]);" in jsx

        assert "easePair(pos);" in jsx
        assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+F_DUR,100); easePair(op);" in jsx


@node
def test_сборка_jsx_с_left_right_up_проходит_проверку_синтаксиса(xml_subs, tmp_path):
    """Сборка с anim=='left', 'right', 'up' синтаксически валидна по verify_jsx и node --check."""
    intro = [
        dict(words=["СЛЕВА"], color="white", times=[T_CAM1], anim="left"),
        dict(words=["СПРАВА"], color="yellow", times=[T_CAM1 + 0.5], anim="right"),
        dict(words=["СНИЗУ"], color="white", times=[T_CAM2], anim="up", fx="glow")
    ]
    jsx, jsx_path = _build(xml_subs, tmp_path, intro, name="out_dir_anims.jsx")

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


def test_anim_count_1500(xml_subs, tmp_path):
    """anim=='count' с текстом '1500': Slider Control 0->1500, Math.round, Opacity 0->100 за HL_DUR."""
    intro = [
        dict(words=["1500"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("anim") == "count"
    assert g1[0].get("cnt") == 1500
    assert g1[0].get("expr") == 'Math.round(effect("Slider Control")("Slider"))'
    assert "anim" not in g2[0]

    # Slider Control и ключи 0 и 1500
    assert '"ADBE Slider Control"' in jsx
    assert '"ADBE Slider Control-0001"' in jsx
    assert "slP.setValueAtTime(t0,0);" in jsx
    assert "slP.setValueAtTime(t0+HL_DUR,target);" in jsx

    # Выражение с Math.round в .jsx
    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx

    # Opacity 0->100 за HL_DUR с easePair
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+HL_DUR,100); easePair(op);" in jsx


def test_anim_count_decimal_comma(xml_subs, tmp_path):
    """anim=='count' с текстом '12,5': знаков 1, цель 12.5, toFixed(1) и замена точки на запятую."""
    intro = [
        dict(words=["12,5"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("anim") == "count"
    assert g1[0].get("cnt") == 12.5
    assert g1[0].get("dec") == 1
    assert g1[0].get("expr") == '(effect("Slider Control")("Slider")).toFixed(1).replace(".", ",")'

    assert 'toFixed(1).replace(\\".\\", \\",\\")' in jsx
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+HL_DUR,100); easePair(op);" in jsx


def test_anim_count_decimal_dot(xml_subs, tmp_path):
    """anim=='count' с текстом '3.75': знаков 2, цель 3.75, разделитель точка (без .replace)."""
    intro = [
        dict(words=["3.75"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("anim") == "count"
    assert g1[0].get("cnt") == 3.75
    assert g1[0].get("dec") == 2
    assert g1[0].get("expr") == '(effect("Slider Control")("Slider")).toFixed(2)'

    assert 'toFixed(2)' in jsx
    assert '.replace' not in g1[0].get("expr", "")


def test_anim_count_auto_dec_integer(xml_subs, tmp_path):
    """anim=='count' при тексте целого числа '1500' автоматически даёт dec=0 и Math.round, даже если в данных было dec=2."""
    intro = [
        dict(words=["1500"], color="white", times=[T_CAM1], anim="count", dec=2),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("cnt") == 1500
    assert g1[0].get("dec") == 0
    assert g1[0].get("expr") == 'Math.round(effect("Slider Control")("Slider"))'

    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx


def test_anim_count_auto_dec_float(xml_subs, tmp_path):
    """anim=='count' при 2.5 даёт dec=1 и toFixed(1), при 2,5 даёт dec=1 и replace('.', ',')."""
    intro = [
        dict(words=["2.5"], color="white", times=[T_CAM1], is_count=True),
        dict(words=["2,5"], color="white", times=[T_CAM2], is_count=True)
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, g2 = _groups(jsx)
    assert g1[0].get("cnt") == 2.5
    assert g1[0].get("dec") == 1
    assert g1[0].get("expr") == '(effect("Slider Control")("Slider")).toFixed(1)'
    assert g2[0].get("cnt") == 2.5
    assert g2[0].get("dec") == 1
    assert g2[0].get("expr") == '(effect("Slider Control")("Slider")).toFixed(1).replace(".", ",")'


def test_anim_count_thousands_space(xml_subs, tmp_path):
    """anim=='count' с пробелом-разделителем тысяч '1 500': цель 1500, 0 знаков, Math.round."""
    intro = [
        dict(words=["1 500"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("cnt") == 1500
    assert g1[0].get("dec") == 0
    assert g1[0].get("expr") == 'Math.round(effect("Slider Control")("Slider"))'
    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx


def test_anim_count_non_number(xml_subs, tmp_path):
    """anim=='count' с текстом 'привет': не число -> никакого Slider Control, обычный фейд."""
    intro = [
        dict(words=["привет"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert "cnt" not in g1[0]
    assert "expr" not in g1[0]

    assert "Slider Control" not in jsx
    assert "ADBE Slider Control" not in jsx
    # Обычный фейд на окне F_DUR, HL_DUR не используется для счётчика
    assert "F_DUR" in jsx
    assert "easePair" in jsx


def test_anim_count_line_mode(xml_subs, tmp_path):
    """Построчный режим (intro_mode='line') со счётчиком '1500' и не-числом 'привет'."""
    # Число в line mode: Slider Control, Math.round, Opacity 0->100 за HL_DUR
    intro_num = [
        dict(words=["1500"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx_num, _ = _build(xml_subs, tmp_path, intro_num, mode="line", name="line_count.jsx")
    assert "introAnimFX(Ll, t0l, ln.anim, ln.fx, null, ln.cnt, ln.expr, null, ln.color);" in jsx_num
    assert '"ADBE Slider Control"' in jsx_num
    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx_num
    assert "op.setValueAtTime(t0,0); op.setValueAtTime(t0+HL_DUR,100); easePair(op);" in jsx_num

    # Не-число в line mode: никакого Slider Control, обычный фейд
    intro_txt = [
        dict(words=["привет"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx_txt, _ = _build(xml_subs, tmp_path, intro_txt, mode="line", name="line_non_num.jsx")
    assert "Slider Control" not in jsx_txt
    assert "ADBE Slider Control" not in jsx_txt
    assert "introAnimFX" not in jsx_txt
    assert "opL.setValueAtTime(t0l,0); opL.setValueAtTime(t0l+F_DUR,100); easePair(opL);" in jsx_txt


@node
def test_сборка_jsx_со_счетчиком_проходит_проверку_синтаксиса(xml_subs, tmp_path):
    """Сборка с anim=='count' (числа 1500, 12,5 и 3.75) синтаксически валидна по verify_jsx и node --check."""
    intro = [
        dict(words=["1500"], color="white", times=[T_CAM1], anim="count"),
        dict(words=["12,5"], color="yellow", times=[T_CAM1 + 0.5], anim="count"),
        dict(words=["3.75"], color="white", times=[T_CAM2], anim="count", fx="glow")
    ]
    jsx, jsx_path = _build(xml_subs, tmp_path, intro, name="out_count_syntax.jsx")

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


def test_anim_glitch_авто_тень_и_свечение_без_галочки_intro_shadow(xml_subs, tmp_path):
    """anim=='glitch' при intro_shadow=False автоматически получает Drop Shadow 116/16/6.8/34 и Glow без fx."""
    intro = [
        dict(words=["ГЛИТЧ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, style={"intro_shadow": False})

    # Drop Shadow 116 / 16 / 6.8 / 34
    assert "INTRO_SHADOW_OP=116, INTRO_SHADOW_DIR=16, INTRO_SHADOW_DIST=6.8, INTRO_SHADOW_SOFT=34" in jsx
    assert 'function introWordShadow(L, isBack)' in jsx
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' in jsx

    # anim=="glitch" САМ приносит Gaussian Blur 3.4 и Glow 149 / 77 / 0.62 без выставления fx;
    # ветка глитча и ветка свечения разделены (у свечения Blur нет — задание G)
    assert 'if(anim=="glitch"){' in jsx
    assert '} else if(fx=="glow"){' in jsx
    assert 'setP(fxGb,"ADBE Gaussian Blur 2-0001",3.4);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0002",149);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0003",77);' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0004",0.62);' in jsx


def test_fx_glow_авто_тень_без_галочки_intro_shadow(xml_subs, tmp_path):
    """fx=='glow' при intro_shadow=False тени НЕ получает: автотень осталась только у
    anim=='glitch' и у строк заднего плана (задание G — свечение её больше не приносит)."""
    intro = [
        dict(words=["СВЕЧЕНИЕ"], color="white", times=[T_CAM1], fx="glow"),
        dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, style={"intro_shadow": False})

    assert "INTRO_SHADOW_OP=116, INTRO_SHADOW_DIR=16, INTRO_SHADOW_DIST=6.8, INTRO_SHADOW_SOFT=34" not in jsx
    assert "function introWordShadow(L, isBack)" not in jsx
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' not in jsx


def test_обычная_строка_при_выключенной_галочке_тени_не_получает(xml_subs, tmp_path):
    """Обычная строка при выключенной галочке intro_shadow тень не получает."""
    # 1. Если вообще нет спец-строк — функций и констант тени нет вовсе
    plain_intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])
    ]
    jsx_plain, _ = _build(xml_subs, tmp_path, plain_intro, style={"intro_shadow": False})
    assert "introWordShadow" not in jsx_plain
    assert "INTRO_SHADOW_OP" not in jsx_plain

    # 2. Если рядом есть строка с глитчем, вызов идёт под условием ln.anim/ln.back
    mixed_intro = [
        dict(words=["ГЛИТЧ"], color="white", times=[T_CAM1], anim="glitch"),
        dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2])
    ]
    jsx_mixed, _ = _build(xml_subs, tmp_path, mixed_intro, style={"intro_shadow": False})
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' in jsx_mixed
    g1, g2 = _groups(jsx_mixed)
    # У обычной строки нет anim, fx, back — условие ложно, тень не вызывается
    assert "anim" not in g2[0] and "fx" not in g2[0] and "back" not in g2[0]


def test_одновременные_glitch_и_glow_добавляют_эффекты_один_раз(xml_subs, tmp_path):
    """При одновременных anim=='glitch' и fx=='glow' эффекты свечения добавляются ровно один раз."""
    intro = [
        dict(words=["КОМБО"], color="white", times=[T_CAM1], anim="glitch", fx="glow"),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro, style={"intro_shadow": False})

    # Ветки if(anim=="glitch") / else if(fx=="glow") взаимоисключающие: в рантайме эффекты
    # добавляются ровно один раз (строка идёт ровно по одной ветке), поэтому в тексте
    # шаблона Blur один, а строки Glo2 — по одной на ветку.
    assert 'if(anim=="glitch"){' in jsx and '} else if(fx=="glow"){' in jsx
    assert jsx.count('setP(fxGb,"ADBE Gaussian Blur 2-0001",3.4);') == 1
    assert jsx.count('setP(fxGl,"ADBE Glo2-0002",149);') == 2
    assert jsx.count('setP(fxGl,"ADBE Glo2-0003",77);') == 2
    assert jsx.count('setP(fxGl,"ADBE Glo2-0004",0.62);') == 2


def test_прекомп_только_с_glow_получает_мягкий_glow(xml_subs, tmp_path):
    """Прекомп с fx=='glow' без anim=='glitch' Glo2 не получает: усиленный Glow 211/93/0.42
    на мастере включается ТОЛЬКО глитчем (задание G) — иначе свечение пересвечивало картинку."""
    intro = [
        dict(words=["ТОЛЬКО"], color="white", times=[T_CAM1], fx="glow"),
        dict(words=["БЕЗ ГЛИТЧА"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)

    # grpGlitch — только по anim=="glitch", свечение идёт в grpGlow; группа «только свечение»
    # Glo2 на прекомпе не получает вовсе (задание G): эффект добавляется под условием
    # grpGlitch || !grpGlow, иначе пересвет.
    assert 'if(GRP[gck].anim=="glitch") grpGlitch=true;' in jsx
    assert 'if(GRP[gck].fx=="glow") grpGlow=true;' in jsx
    assert 'if(grpGlitch || !grpGlow){' in jsx
    assert 'if(grpGlitch){' in jsx
    assert 'setP(igl,"ADBE Glo2-0002",211); setP(igl,"ADBE Glo2-0003",93); setP(igl,"ADBE Glo2-0004",0.42);' in jsx


def test_звук_глитча_тайминги_и_обрезка(xml_subs, tmp_path):
    """anim=='glitch' со звуком: ОДИН слой на группу глитч-слов — inPoint = первое слово,
    outPoint = последнее слово + полка 0.45 + спад 0.12 + кадр, startTime = первое слово − 0.567,
    и четыре ключа Audio Levels (−48 → db → db → −48), спад за кадр до конца слоя."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0], anim="glitch"),
        dict(words=["ВТОРОЕ"], color="white", times=[8.0])
    ]
    fake_wav = str(tmp_path / "gltchgltch_24.wav")
    with open(fake_wav, "wb") as f:
        f.write(b"RIFFdummy")

    jsx, _ = _build(xml_subs, tmp_path, intro, style={"glitch": fake_wav, "glitch_db": -3.5})

    assert 'var GLITCH=' in jsx
    assert 'var glitchItem=imp(GLITCH);' in jsx
    assert 'toBin(glitchItem,"Интро");' in jsx
    assert 'var gl=main.layers.add(glitchItem); gl.name="Глитч";' in jsx
    # Тайминги в секундах (не зависят от fps): GLITCH_SFX_PRE_S=0.567; огибающая из
    # полки 0.45, спада 0.12 и кадра (фикстура 60 fps).
    # t0=2.0 => startTime = 2.0 − 0.567 = 1.433, inPoint = 2.0, outPoint = 2.5867
    assert 'gl.startTime=1.433; gl.inPoint=2; gl.outPoint=2.5867;' in jsx
    assert 'glAlv.setValue([-3.5, -3.5]);' in jsx
    assert 'glAlv.setValueAtTime(2, [-48, -48]);' in jsx
    assert 'glAlv.setValueAtTime(2.08, [-3.5, -3.5]);' in jsx
    assert 'glAlv.setValueAtTime(2.45, [-3.5, -3.5]);' in jsx
    assert 'glAlv.setValueAtTime(2.57, [-48, -48]);' in jsx


def test_count_сочетается_с_глитчем_и_свечением(xml_subs, tmp_path):
    """is_count=True на строке с anim='glitch' и fx='glow': Slider Control, Glitch Animator, Glow."""
    intro = [
        dict(words=["1500"], color="white", times=[T_CAM1], is_count=True, anim="glitch", fx="glow"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("is_count") is True
    assert g1[0].get("cnt") == 1500
    assert g1[0].get("anim") == "glitch"
    assert g1[0].get("fx") == "glow"

    # Slider Control от счётчика
    assert '"ADBE Slider Control"' in jsx
    assert 'Math.round(effect(\\"Slider Control\\")(\\"Slider\\"))' in jsx

    # Glitch Text Animator
    assert '"ADBE Text Randomize Order"' in jsx
    assert 'adv.property("ADBE Text Randomize Order").setValue(1)' in jsx
    assert "op.setValueAtTime(t0+0.05,100);" in jsx

    # Glow
    assert '"ADBE Glo2"' in jsx
    assert 'setP(fxGl,"ADBE Glo2-0002",149);' in jsx


def test_count_сочетается_с_reveal(xml_subs, tmp_path):
    """is_count=True на строке с anim='reveal': Slider Control + Reveal Animator + Blur."""
    intro = [
        dict(words=["25"], color="white", times=[T_CAM1], is_count=True, anim="reveal"),
        dict(words=["СОВЕТОВ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("is_count") is True
    assert g1[0].get("cnt") == 25
    assert g1[0].get("anim") == "reveal"

    # Slider Control
    assert '"ADBE Slider Control"' in jsx

    # Reveal
    assert '"ADBE Text Percent Offset"' in jsx
    assert "pOff.setValueAtTime(t0,-100); pOff.setValueAtTime(t0+F_DUR,100);" in jsx
    assert "pBl.setValueAtTime(t0,26.8); pBl.setValueAtTime(t0+F_DUR,0);" in jsx


def test_count_только_на_слове_числе_в_многословной_строке(xml_subs, tmp_path):
    """Строка '100 СПОСОБОВ' с is_count=True: cnt_idx=0, Slider только для слова '100'."""
    intro = [
        dict(words=["100", "СПОСОБОВ"], color="white", times=[T_CAM1, T_CAM1 + 0.3], is_count=True, anim="glitch"),
        dict(words=["РАЗБОГАТЕТЬ"], color="white", times=[T_CAM2])
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    g1, _ = _groups(jsx)
    assert g1[0].get("is_count") is True
    assert g1[0].get("cnt") == 100
    assert g1[0].get("cnt_idx") == 0

    # Проверка вызова introAnimFX в пословном режиме:
    # слово 0 получает свой элемент cnts, слово 1 — null
    assert 'var cw=null; if(ln.cnts){for(var ci=0;ci<ln.cnts.length;ci++){if(ln.cnts[ci][0]===wj2){cw=ln.cnts[ci];break;}}}' in jsx
    assert 'introAnimFX(wl[wj2], tw, ln.anim, ln.fx, ww[wj2], cw?cw[1]:null, cw?cw[2]:null, null, ln.color);' in jsx


@node
def test_сборка_jsx_со_счетчиком_и_глитчем_проходит_проверку_синтаксиса(xml_subs, tmp_path):
    """Сборка с is_count=True, anim='glitch', fx='glow' синтаксически валидна."""
    intro = [
        dict(words=["100"], color="white", times=[T_CAM1], is_count=True, anim="glitch", fx="glow"),
        dict(words=["СОВЕТОВ"], color="white", times=[T_CAM2], anim="reveal")
    ]
    jsx, jsx_path = _build(xml_subs, tmp_path, intro, name="count_glitch_syntax.jsx")
    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"


# ---- ПРАВКА 3/4 / IK: окна фейд-аута прекомпов с глитчем ----
# Полка прекомпа (момент начала фейд-аута) не наступает раньше конца анимации последнего
# глитч-слова (слово + 0.44 из INTRO_ANIMS); спад единый: 0.35 (intro_fade), у
# последнего прекомпа или при следующем дальше 2 с — полка +0.3 (intro_fx_hold_add) и спад
# 0.35 (intro_fade). Прекомп без глитча не меняется. Числа живут в плане сцены
# (превью) и в .jsx через INTRO_FX — вторая копия не заводится.


def _scene_plan(xml_subs, intro, splits=None, style=None):
    return xml2ae.scene_plan(xml_subs, intro=intro, intro_splits=splits,
                             style=dict(style or {}), disclaimer="",
                             intro_riser=False, emit=lambda *a: None)


def test_прекомп_с_глитчем_полка_не_раньше_конца_анимации(xml_subs):
    """Средний прекомп с глитчем: полка (te − fade) не раньше последнего глитч-слова + 0.44."""
    intro = [
        dict(words=["ПЕРВОЕ", "ВТОРОЕ"], color="white", times=[2.0, 2.45], anim="glitch"),
        dict(words=["БЛИЗКО"], color="white", times=[5.0]),
    ]
    plan = _scene_plan(xml_subs, intro, splits=[1])
    g0, g1 = plan["intro"][0], plan["intro"][1]

    # по обычной формуле outStart = gMax = 2.45 — раньше конца анимации глитча (2.89);
    # прекомп продлевается: te − fade = 2.45 + 0.44 = 2.89, спад 0.35 (intro_fade)
    assert g0["ts"] == 0
    assert abs((g0["te"] - g0["fade"]) - (2.45 + 0.44)) < 1e-9
    assert g0["fade"] == 0.35
    assert g0["te"] == 3.24
    # следующий близко (5.0 − 3.24 < 2 с) — полка без добавки, единый спад intro_fade
    # прекомп БЕЗ глитча: окно te прежнее (7.05), фейд 0.35 (intro_fade)
    assert g1["te"] == 7.05 and g1["fade"] == 0.35


def test_прекомп_с_глитчем_следующий_далеко_полка_дольше_спад_короче(xml_subs):
    """Следующий прекомп дальше чем через 2 с после конца — полка +0.3, спад 0.35."""
    intro = [
        dict(words=["ПЕРВОЕ", "ВТОРОЕ"], color="white", times=[2.0, 2.45], anim="glitch"),
        dict(words=["ДАЛЕКО"], color="white", times=[10.0]),
    ]
    plan = _scene_plan(xml_subs, intro, splits=[1])
    g0 = plan["intro"][0]

    assert g0["te"] == 3.54 and g0["fade"] == 0.35
    # полка: 2.45 + 0.44 (конец анимации) + 0.3 (добавка) = 3.19
    assert abs((g0["te"] - g0["fade"]) - (2.45 + 0.44 + 0.3)) < 1e-9


def test_последний_прекомп_с_глитчем_полка_дольше(xml_subs):
    """Последний прекомп с глитчем (дальше интро нет вовсе): полка +0.3, спад 0.35."""
    intro = [
        dict(words=["ОБЫЧНОЕ"], color="white", times=[1.0]),
        dict(words=["ГЛИТЧ"], color="white", times=[8.3], anim="glitch"),
    ]
    plan = _scene_plan(xml_subs, intro, splits=[1])
    g1 = plan["intro"][1]

    # обычный outStart последнего = gMax + 0.3 + 1.0 = 9.6, дальше интро нет —
    # добавляем полку 0.3 и ставим спад 0.35: te = 9.9 + 0.35 = 10.25
    assert g1["te"] == 10.25 and g1["fade"] == 0.35
    assert abs((g1["te"] - g1["fade"]) - (8.3 + 0.3 + 1.0 + 0.3)) < 1e-9


def test_прекомп_без_глитча_не_изменился(xml_subs, tmp_path):
    """Без глитча: окно te прежнее, fade 0.35, .jsx прежний (INTRO_FX в шаблон не уезжает)."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
    ]
    plan = _scene_plan(xml_subs, intro, splits=[1])
    g0, g1 = plan["intro"][0], plan["intro"][1]
    # РОВНО формула inAt/outEnd из AE_FULL (см. xml2ae.layout._intro_group_window)
    assert g0["ts"] == 0 and g0["te"] == 2.75 and g0["fade"] == 0.35
    assert g1["ts"] == 8.3 and g1["te"] == 10.35 and g1["fade"] == 0.35

    jsx, _ = _build(xml_subs, tmp_path, intro)
    assert "var INTRO_FX=" not in jsx
    assert "outStart=INTRO_FX" not in jsx


def test_прекомп_с_глитчем_окна_уезжают_в_jsx(xml_subs, tmp_path):
    """Те же числа в .jsx: INTRO_FX несёт [начало фейд-аута, конец слоя] прекомпа."""
    intro = [
        dict(words=["ПЕРВОЕ", "ВТОРОЕ"], color="white", times=[2.0, 2.45], anim="glitch"),
        dict(words=["БЛИЗКО"], color="white", times=[5.0]),
    ]
    jsx, _ = _build(xml_subs, tmp_path, intro)
    # из test_прекомп_с_глитчем_полка_не_раньше_конца_анимации: [[2.89, 3.24], null]
    assert "var INTRO_FX=[[2.89,3.24],null];" in jsx
    assert "if (INTRO_FX[gI]){ outStart=INTRO_FX[gI][0]; outEnd=INTRO_FX[gI][1]; }" in jsx
