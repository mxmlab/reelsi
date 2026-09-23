# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свечение строк интро — на буквах, а не на прекомпе; тритон для жёлтого хайлайта.

Что проверяем:
  * anim=="glitch" — на слое СЛОВА Blur 3.4 + Glo2 149/77/0.62, на прекомпе Glo2 211/93/0.42;
  * fx=="glow" без глитча — на слое слова ТОЛЬКО Glo2 149/77/0.62 (никакого Blur), прекомп
    группы БЕЗ Glo2 вовсе; группа с глитчем (даже если в ней есть строки «только свечение») —
    прекомп как у глитча;
  * жёлтая строка (color=="yellow") с глитчем или свечением получает ADBE Tritone с Midtones
    = цвет заливки жёлтой строки (HL_FILL, либо INTRO_HL_FILL из стиля) ПОСЛЕ Glo2; белая,
    accent и custom — нет;
  * автотень — только glitch и back: свечение её больше не приносит;
  * golden: без глитча и свечения в .jsx нет ADBE Tritone, Glo2 прекомпа прежний (радиус 42).

Список эффектов проверяется по порядку: функция introAnimFX из готового .jsx исполняется в
node с заглушками слоя (как это делают другие тесты для шаблона), поэтому проверка не зависит
от того, что обе ветки свечения лежат в одном тексте шаблона.

Цвет выделения у сборок с тритоном — тёмный (DARK_HL_FILL): на ярком цвете
мидтонов тритон не ставится вовсе (свечение выбеливает букву — она уходит в Highlights),
а дефолтный жёлтый hl_fill [1,0.9176,0] имеет яркость 0.87. Отсутствие тритона на ярком
цвете проверяет tests/test_tritone_bright.py.
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

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Тёмный цвет выделения для сборок с тритоном: красный, яркость мидтонов
# 0.24 — тритон ставится. Дефолтный жёлтый (0.87) ярче порога TRITONE_MAX_LUM=0.7 —
# там тритона нет.
DARK_HL_FILL = [0.6863, 0.1216, 0.1216]


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


def _glow_intro():
    """Строки всех цветов и режимов: жёлтый глитч, жёлтое свечение, белое свечение,
    фон со свечением, акцентный глитч."""
    return [
        dict(words=["ЖЁЛТЫЙ"], color="yellow", times=[T_CAM1], anim="glitch"),
        dict(words=["ЖЁЛТОЕ"], color="yellow", times=[T_CAM1 + 0.3], fx="glow"),
        dict(words=["БЕЛОЕ"], color="white", times=[T_CAM1 + 0.6], fx="glow"),
        dict(words=["ФОН"], color="white", times=[T_CAM1 + 0.9], back=True, fx="glow"),
        dict(words=["АКЦЕНТ"], color="accent", times=[T_CAM2], anim="glitch"),
    ]


def _balanced(jsx, open_brace):
    """Индекс сразу за парной закрывающей скобкой (в телах шаблона нет скобок в строках)."""
    depth = 0
    for i in range(open_brace, len(jsx)):
        if jsx[i] == "{":
            depth += 1
        elif jsx[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("не нашёл закрывающую скобку")


def _anim_fx_src(jsx):
    i = jsx.index("function introAnimFX")
    return jsx[i:_balanced(jsx, jsx.index("{", i))]


def _comp_glow_src(jsx):
    i = jsx.index("var grpGlitch")
    i_end = _balanced(jsx, jsx.index("{", jsx.index("for(var gck=0", i)))
    flags_src = jsx[i:i_end]
    j = jsx.index("if(grpGlitch || ", i_end)
    glow_src = jsx[j:_balanced(jsx, jsx.index("{", j))]
    return flags_src + "\n" + glow_src


def _hl_fill_literal(jsx):
    """Литерал HL_FILL из самого .jsx — тритон обязан красить Midtones ИМ, а не своим."""
    m = re.search(r"var HL_FILL = (\[[^\]]*\])(?:,|;)", jsx)
    assert m, "в .jsx нет объявления HL_FILL"
    return m.group(1)


_JS_PROBE = r"""
'use strict';
var calls = [];
function eff(){ return { property: function(p){ return { setValue: function(v){ calls.push('set:'+p+'='+JSON.stringify(v)); } }; } }; }
function addFX(L, mn){ calls.push('add:'+mn); return eff(); }
function setP(fx, mn, v){ calls.push('set:'+mn+'='+JSON.stringify(v)); }
function lay(){ var o={}; o.property=function(){ return lay(); }; o.addProperty=function(){ return lay(); };
  o.setValue=function(){}; o.setValueAtTime=function(){}; o.value=[0,0]; o.expression=''; return o; }
function easePair(){}
function introW(){ return 100; }
var HL_DUR=0.35, F_DUR=0.25, HL_RISE=60, BACK_SCALE=1, BACK_STEP=0.45, INTRO_GLOW=1;
var HL_FILL=__HLFILL__;
var GRP = [];

__ANIMFX__

function runWord(anim, fx, col){ calls=[]; introAnimFX(lay(), 0, anim, fx, null, null, null, false, col); return calls; }

// Прекомп группы: своя заглушка — пишем и addProperty парада эффектов, и значения
// именованных свойств (у мягкой ветки Glo2 нет matchName-ключей, только имена).
function compLay(tag){ var o={}; o.property=function(n){ return compLay(tag+'.'+n); };
  o.setValue=function(v){ calls.push('val:'+tag+'='+JSON.stringify(v)); }; return o; }
function parade(){ return { addProperty: function(mn){ calls.push('add:'+mn); return compLay('igl'); } }; }
function compLayer(){ return { property: function(n){ return (n==='ADBE Effect Parade') ? parade() : compLay('iL'); } }; }
var iL = compLayer();
function runComp(grp){ calls=[]; GRP=grp; __COMPGLOW__ ; return calls; }

var out = {
  yellow_glitch:  runWord('glitch', '', 'yellow'),
  yellow_glow:    runWord('', 'glow', 'yellow'),
  white_glow:     runWord('', 'glow', 'white'),
  accent_glitch:  runWord('glitch', '', 'accent'),
  comp_glitch:    runComp([{anim:'glitch'}]),
  comp_glow_only: runComp([{fx:'glow'}]),
  comp_both:      runComp([{anim:'glitch'}, {fx:'glow'}]),
  comp_plain:     runComp([{}])
};
console.log(JSON.stringify(out));
"""


def _probe(jsx, tmp_path):
    """Прогон шаблона в node с заглушками слоя: возвращает списки эффектов по порядку."""
    script = (_JS_PROBE
              .replace("__HLFILL__", _hl_fill_literal(jsx))
              .replace("__ANIMFX__", _anim_fx_src(jsx))
              .replace("__COMPGLOW__", _comp_glow_src(jsx)))
    node_file = tmp_path / "probe_glow.js"
    node_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(node_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node упал: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


@node
def test_жёлтая_строка_с_глитчем_blur_глоу_и_тритон(xml_subs, tmp_path):
    """Жёлтый глитч: Blur 3.4 -> Glo2 149/77/0.62 -> Tritone Midtones=HL_FILL; прекомп 211/93/0.42."""
    jsx, _ = _build(xml_subs, tmp_path, _glow_intro(), style={"hl_fill": DARK_HL_FILL},
                    name="glow_all.jsx")
    hl = _hl_fill_literal(jsx)
    got = _probe(jsx, tmp_path)

    assert got["yellow_glitch"] == [
        "add:ADBE Gaussian Blur 2", "set:ADBE Gaussian Blur 2-0001=3.4",
        "set:ADBE Gaussian Blur 2-0003=0",
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
        "add:ADBE Tritone", "set:ADBE Tritone-0002=" + hl,
    ]
    # в шаблоне тритон красится именно жёлтой заливкой и стоит ПОСЛЕ Glo2
    assert 'var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",HL_FILL);' in jsx
    assert jsx.index('setP(fxGl,"ADBE Glo2-0004",0.62);') < jsx.index('"ADBE Tritone"')
    # прекомп группы с глитчем — усиленный Glo2
    assert got["comp_glitch"] == [
        "add:ADBE Glo2", "set:ADBE Glo2-0002=211", "set:ADBE Glo2-0003=93",
        "set:ADBE Glo2-0004=0.42",
    ]


@node
def test_жёлтая_строка_только_с_свечением_без_blur_тени_и_glo2_прекомпа(xml_subs, tmp_path):
    """fx=='glow' без глитча, жёлтая: только Glo2 + Tritone, без Blur и тени; у прекомпа Glo2 нет."""
    jsx, _ = _build(xml_subs, tmp_path, _glow_intro(), style={"hl_fill": DARK_HL_FILL},
                    name="glow_all2.jsx")
    hl = _hl_fill_literal(jsx)
    got = _probe(jsx, tmp_path)

    assert got["yellow_glow"] == [
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
        "add:ADBE Tritone", "set:ADBE Tritone-0002=" + hl,
    ]
    # ветка свечения отдельная: Blur живёт только в ветке глитча
    m_glow_branch = re.search(r'\} else if\(fx=="glow"\)\{(.*?)\n            \}',
                              _anim_fx_src(jsx), re.S)
    assert m_glow_branch, "в introAnimFX нет отдельной ветки fx==\"glow\""
    assert "Gaussian Blur" not in m_glow_branch.group(1)
    # группа со свечением и БЕЗ глитча: Glo2 на прекомпе не добавляется вовсе
    assert got["comp_glow_only"] == []
    assert 'if(grpGlitch || (!grpGlow && !grpYellow)){' in jsx
    # группа, где есть и глитч, и строка «только свечение», — прекомп как у глитча
    assert got["comp_both"] == got["comp_glitch"]


def test_жёлтая_строка_только_с_свечением_без_тени_в_тексте(xml_subs, tmp_path):
    """Та же сборка текстом: тени у строки со свечением нет (автотень — только glitch/back)."""
    jsx, _ = _build(xml_subs, tmp_path, [
        dict(words=["СВЕЧЕНИЕ"], color="yellow", times=[T_CAM1], fx="glow"),
        dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2]),
    ], style={"intro_shadow": False}, name="glow_only.jsx")

    assert "introWordShadow" not in jsx
    assert "INTRO_SHADOW_OP" not in jsx
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' not in jsx


@node
def test_белая_и_акцентная_строки_тритона_не_получают(xml_subs, tmp_path):
    """Белая строка со свечением — Glo2 без тритона; акцентная с глитчем — без тритона."""
    jsx, _ = _build(xml_subs, tmp_path, _glow_intro(), style={"hl_fill": DARK_HL_FILL},
                    name="glow_all3.jsx")
    got = _probe(jsx, tmp_path)

    assert got["white_glow"] == [
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
    ]
    assert "ADBE Tritone" not in got["white_glow"]
    assert got["accent_glitch"] == [
        "add:ADBE Gaussian Blur 2", "set:ADBE Gaussian Blur 2-0001=3.4",
        "set:ADBE Gaussian Blur 2-0003=0",
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
    ]
    assert "ADBE Tritone" not in got["accent_glitch"]
    # тритон заперт условием col=="yellow"
    assert 'if((anim=="glitch"||fx=="glow") && col=="yellow"){' in jsx


def test_тритон_берёт_intro_hl_fill_из_стиля(xml_subs, tmp_path):
    """При заданном в стиле intro_hl_fill тритон красится им, а не HL_FILL.

    Цвет тёмный (яркость 0.39): на ярком цвете мидтонов тритона нет вовсе,
    и проверить подстановку INTRO_HL_FILL было бы не на чем."""
    fill = [0.2, 0.4, 0.8]
    jsx, _ = _build(xml_subs, tmp_path, [
        dict(words=["ЖЁЛТОЕ"], color="yellow", times=[T_CAM1], fx="glow"),
        dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2]),
    ], style={"intro_hl_fill": fill}, name="glow_hlfill.jsx")

    assert "INTRO_HL_FILL=[0.2,0.4,0.8]" in jsx
    assert 'var tt=addFX(L,"ADBE Tritone"); setP(tt,"ADBE Tritone-0002",INTRO_HL_FILL);' in jsx


def test_строка_back_со_свечением_тень_заднего_плана_есть(xml_subs, tmp_path):
    """back + fx=='glow': тень заднего плана остаётся (условие ln.anim=="glitch"||ln.back)."""
    jsx, _ = _build(xml_subs, tmp_path, [
        dict(words=["ГЛАВНОЕ"], color="white", times=[T_CAM1]),
        dict(words=["фон"], color="white", times=[T_CAM1 + 0.5], back=True, fx="glow"),
    ], name="back_glow.jsx")

    assert "function introWordShadow(L, isBack)" in jsx
    assert "BACK_SHADOW_OP=131, BACK_SHADOW_SOFT=38" in jsx
    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' in jsx
    # Blur строка со свечением не приносит: ветка fx=="glow" без глитча
    assert 'if(anim=="glitch"){' in jsx and '} else if(fx=="glow"){' in jsx


def test_ролик_без_глитча_и_свечения_прежний(xml_subs, tmp_path):
    """Без глитча и свечения: ни ADBE Tritone, ни grpGlow; Glo2 прекомпа — прежние 42/INTRO_GLOW."""
    jsx, _ = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1]),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2]),
    ], style={}, name="plain.jsx")

    assert "ADBE Tritone" not in jsx
    assert "grpGlitch" not in jsx
    assert "grpGlow" not in jsx
    assert "introAnimFX" not in jsx
    assert 'try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");' in jsx
    assert 'try{ igl.property("Glow Radius").setValue(42); }catch(e){}' in jsx
    assert 'try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){} }catch(e){}' in jsx

    # Ролик с другими анимациями (reveal/left), но без глитча и свечения: introAnimFX в .jsx
    # есть, а тритона в нём нет вовсе — подстановка пустая, прекомп по-прежнему 42/INTRO_GLOW.
    jsx_anim, _ = _build(xml_subs, tmp_path, [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="reveal"),
        dict(words=["ВТОРОЕ"], color="white", times=[T_CAM2], anim="left"),
    ], style={}, name="anim_no_glow.jsx")

    assert "function introAnimFX" in jsx_anim
    assert "ADBE Tritone" not in jsx_anim
    assert "grpGlitch" not in jsx_anim
    assert 'try{ igl.property("Glow Radius").setValue(42); }catch(e){}' in jsx_anim

    # Эталон геометрии собран без интро и пересборки не требует: в нём нет ни тритона,
    # ни новых вычислений grpGlow/grpGlitch.
    golden = open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                  encoding="utf-8-sig").read()
    assert "ADBE Tritone" not in golden
    assert "grpGlow" not in golden


@node
def test_сборка_jsx_с_тритоном_проходит_проверку_синтаксиса(xml_subs, tmp_path):
    """Сборка из п.1 (жёлтый глитч, свечение, back, акцент) валидна по verify_jsx и node --check."""
    jsx, jsx_path = _build(xml_subs, tmp_path, _glow_intro(), style={"hl_fill": DARK_HL_FILL},
                           name="glow_syntax.jsx")

    rep = verify_jsx.Report(jsx_path)
    verify_jsx.check_syntax(jsx_path, jsx, rep)
    assert rep.ok, f"verify_jsx failed: {rep.errors}"
