# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания ZF: шрифт в AE ставится рабочей копией, а не по имени.

У AE бывает НЕСКОЛЬКО записей с одним PostScript-именем (след переустановки шрифта).
`d.font = "BebasNeue-Regular"` встаёт на битую копию — в тексте остаётся Times, и
try/catch этого не видит: ошибки нет, шрифт просто не тот. Шаблон теперь перебирает
копии из `app.fonts.getFontsByPostScriptName` через `fontObject` (`_fontPick`), кэширует
выбор на имя (проба один раз на имя) и ставит его через `setFont`.

Здесь `_fontPick` и `setFont` вынимаются из СОБРАННОГО .jsx и гоняются в node на
поддельных объектах AE (тем же приёмом, что tests/test_cam1_zoom_none.py), плюс
grep-тест: присваивания `.font` вне этих двух функций в сборке быть не должно.

Поддельный AE повторяет поведение настоящего: `sp.value` отдаёт копию документа,
`sp.setValue(d)` применяет её, а по имени шрифт разрешается так, как решила система
(в конфиге теста это `resolve`) — именно на этом и ломается сборка у владельца.
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

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт шаблона требует node в PATH")

PS = "BebasNeue-Regular"          # имя из диагноза: у него в AE две записи
BROKEN = "TimesNewRomanPSMT"      # что реально встаёт по имени


def _func(src, name):
    """Тело функции из исходника по балансу скобок (как в tests/test_cam1_zoom_none.py)."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в сборке не нашлась функция {name}"
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


def _font_code(jsx):
    """Кусок сборки: объявление кэша + _fontPick + setFont (по одной копии на .jsx)."""
    decl = re.search(r"[ \t]*var _FONT_PICK[^\n]*\n[ \t]*var _fontProbe[^\n]*\n", jsx)
    assert decl, "в сборке нет объявления кэша выбора шрифта (_FONT_PICK/_fontProbe)"
    return decl.group(0) + _func(jsx, "_fontPick") + "\n" + _func(jsx, "setFont") + "\n"


# Поддельные app/слои/документы AE. Счётчики создания пробной композиции и слоя в ней —
# ими проверяется, что проба делается один раз и только когда она вообще возможна.
_HARNESS = r"""
var _LOG_MSGS = [], _probeLayers = 0, _probeComps = 0;
function _LOG(m){ _LOG_MSGS.push("" + m); }
function _mkSp(resolve, cur){
    return { get value(){ return { font: cur.font, fontObject: cur.fontObject }; },
             setValue: function(d){
                 if (d.fontObject){ cur.fontObject = d.fontObject; cur.font = d.fontObject.applied; }
                 else { cur.fontObject = null; cur.font = resolve(d.font); } } };
}
function _targetSp(resolve){ return _mkSp(resolve, { font: "", fontObject: null }); }
function _mkEnv(cfg){
    var resolve = cfg.resolve || function(p){ return p; };
    var env = { resolve: resolve, compName: null, removed: false };
    env.app = { project: { items: { addComp: function(name){
        _probeComps++; env.compName = name;
        var sp = _mkSp(resolve, { font: "", fontObject: null });
        return { name: name,
                 layers: { addText: function(){ _probeLayers++;
                     return { property: function(){ return { property: function(){ return sp; } }; } }; } },
                 remove: function(){ env.removed = true; } };
    } } } };
    if (!cfg.noFonts) env.app.fonts = { getFontsByPostScriptName: function(p){ return cfg.copies || []; } };
    app = env.app;   // так же безымянно, как в .jsx: _fontPick читает глобальный app
    return env;
}
function _setFontOn(env, ps){
    var sp = _targetSp(env.resolve);
    var d = sp.value;
    setFont(d, ps);
    sp.setValue(d);
    return { font: sp.value.font, obj: d.fontObject ? (d.fontObject.applied || "") : null,
             isObj: !!d.fontObject };
}
function _report(env, r){
    console.log(JSON.stringify({ font: r.font, obj: r.obj, isObj: r.isObj,
        probeLayers: _probeLayers, probeComps: _probeComps, comp: env.compName,
        logs: _LOG_MSGS }));
}
"""


def _run(tmp_path, jsx, body):
    code = _HARNESS + _font_code(jsx) + "\n" + body
    js = tmp_path / "font_probe.js"
    js.write_text(code, encoding="utf-8")
    p = subprocess.run(["node", str(js)], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(scope="module")
def built_jsx(tmp_path_factory):
    """Сборка тем же путём, что tests/test_geometry_python.py: из неё вынимаются функции."""
    tmp = tmp_path_factory.mktemp("font_pick")
    dst = str(tmp / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    path, _, _ = xml2ae.to_ae_full(dst, jsx_path=str(tmp / "out.jsx"),
                                   style={"intro_riser": False}, disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


@node
def test_two_copies_broken_first(built_jsx, tmp_path):
    """Две копии: первая при setValue даёт Times, вторая — верное имя.

    setFont обязан поставить fontObject ВТОРОЙ копии (то, что раньше делал вслепую
    try{d.font=...} и попадал на битую)."""
    out = _run(tmp_path, built_jsx, """
var env = _mkEnv({ copies: [{applied: "%s"}, {applied: "%s"}],
                   resolve: function(){ return "%s"; } });
_report(env, _setFontOn(env, "%s"));
""" % (BROKEN, PS, BROKEN, PS))
    assert out["isObj"] is True, "рабочая копия не выбрана: шрифт поставлен по имени"
    assert out["obj"] == PS, "поставлена не та копия: %r" % (out["obj"],)
    assert out["font"] == PS, "в документе остался чужой шрифт: %r" % (out["font"],)
    assert out["probeLayers"] == 1 and out["probeComps"] == 1, \
        "проба не сделана или сделана не один раз: %r" % (out,)
    assert out["comp"] == "__reelsi_font_probe", "пробная композиция названа не по контракту"


@node
def test_single_copy_set_by_name(built_jsx, tmp_path):
    """Одна копия — не тот случай (у AE двоятся ИМЕНА): шрифт ставится по имени,
    fontObject не трогаем."""
    out = _run(tmp_path, built_jsx, """
var env = _mkEnv({ copies: [{applied: "%s"}],
                   resolve: function(){ return "%s"; } });
_report(env, _setFontOn(env, "%s"));
""" % (PS, PS, PS))
    assert out["isObj"] is False, "при одной копии поставлен fontObject, а не имя"
    assert out["font"] == PS, "шрифт не встал по имени: %r" % (out["font"],)


@node
def test_no_app_fonts_set_by_name_without_probe(built_jsx, tmp_path):
    """AE < 24: app.fonts нет — ставим по имени и НЕ пробуем (проба была бы пустой
    тратой: getFontsByPostScriptName вернуть нечего)."""
    out = _run(tmp_path, built_jsx, """
var env = _mkEnv({ noFonts: true, resolve: function(){ return "%s"; } });
_report(env, _setFontOn(env, "%s"));
""" % (PS, PS))
    assert out["isObj"] is False and out["font"] == PS, "шрифт не поставлен по имени: %r" % (out,)
    assert out["probeLayers"] == 0 and out["probeComps"] == 0, \
        "проба сделана там, где метода нет: %r" % (out,)
    assert out["comp"] is None


@node
def test_nothing_accepted_logged(built_jsx, tmp_path):
    """Ни одна копия не встала и по имени тоже — в лог сбоя, а не в тишину:
    иначе на выходе молча Times, как и было до задания."""
    out = _run(tmp_path, built_jsx, """
var env = _mkEnv({ copies: [{applied: "%s"}, {applied: "%s"}],
                   resolve: function(){ return "%s"; } });
_report(env, _setFontOn(env, "%s"));
""" % (BROKEN, BROKEN, BROKEN, PS))
    assert out["isObj"] is False
    assert any(PS in m and "не принят" in m for m in out["logs"]), \
        "в _LOG нет сообщения про непринятый шрифт: %r" % (out["logs"],)


@node
def test_probe_cached_per_name(built_jsx, tmp_path):
    """Повторный вызов с тем же именем пробу не повторяет: слой в пробной композиции
    создан ровно один раз, композиция — тоже."""
    out = _run(tmp_path, built_jsx, """
var env = _mkEnv({ copies: [{applied: "%s"}, {applied: "%s"}],
                   resolve: function(){ return "%s"; } });
var r1 = _setFontOn(env, "%s");
var r2 = _setFontOn(env, "%s");
console.log(JSON.stringify({ first: r1.obj, second: r2.obj,
    probeLayers: _probeLayers, probeComps: _probeComps, logs: _LOG_MSGS }));
""" % (BROKEN, PS, BROKEN, PS, PS))
    assert out["first"] == PS and out["second"] == PS, "вторая установка потеряла выбор: %r" % (out,)
    assert out["probeLayers"] == 1, "проба повторилась: слоёв %r" % (out["probeLayers"],)
    assert out["probeComps"] == 1, "пробных композиций больше одной: %r" % (out["probeComps"],)


_FONT_ASSIGN = re.compile(r"\.font\s*=(?!=)")   # присваивание, а не сравнение `=== ps`


def _font_assigns_outside(jsx):
    """Куски сборки, где `.font` присваивается вне _fontPick/setFont."""
    spans = []
    for name in ("_fontPick", "setFont"):
        src = _func(jsx, name)
        i = jsx.index(src)
        spans.append((i, i + len(src)))
    bad = []
    for m in _FONT_ASSIGN.finditer(jsx):
        if not any(a <= m.start() < b for a, b in spans):
            bad.append(jsx[max(0, m.start() - 60):m.end() + 20].strip())
    return bad


def test_jsx_has_no_font_assignment_outside_pick(built_jsx):
    """grep-тест: в сборке присваивание `.font` осталось только внутри _fontPick/setFont.

    Иначе любое новое место установки шрифта в обход setFont снова уедет на битую копию."""
    bad = _font_assigns_outside(built_jsx)
    assert not bad, "присваивание .font в обход setFont: %r" % (bad,)


def test_caption_and_tail_disclaimer_use_setfont(xml_subs, tmp_path):
    """Подпись и концевой дисклеймер собирает Python — в сборке фикстуры их нет,
    а шрифт там ставился так же вслепую (вариант сборки с обоими включёнными)."""
    path, _, _ = xml2ae.to_ae_full(
        xml_subs, jsx_path=str(tmp_path / "cap.jsx"), caption="подпись",
        disclaimer="ДИСКЛЕЙМЕР",
        style={"caption": True, "caption_font": "SFPro-Bold", "caption_bg": False,
               "disclaimer_end": True, "intro_riser": False},
        intro_riser=False, emit=lambda *a: None)
    jsx = open(path, encoding="utf-8-sig").read()
    assert "setFont(capVal," in jsx, "подпись ставит шрифт в обход setFont"
    tail = jsx[jsx.index("дисклеймер в конце"):]
    assert "setFont(dd, FONT);" in tail, "концевой дисклеймер ставит шрифт в обход setFont"
    bad = _font_assigns_outside(jsx)
    assert not bad, "присваивание .font в обход setFont: %r" % (bad,)
