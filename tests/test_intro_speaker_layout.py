# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Макет спикера (задание Q): ползунки, точка наезда, зум интро.

Часть 1: интро в превью наследует зум Камеры 1 — правило экран = C + s*(p−C) живёт
в ОДНОЙ функции ipvCamChild, зовётся из вставок кам1 и из интро, второй копии нет.
Часть 2: ключи стиля cam1_zoom_cx/cy (точка наезда), insert_c2_x/y (вставки Кам2),
intro_x (интро по X); дефолты равны сегодняшнему поведению — при них .jsx побайтово
прежний (golden).

Здесь:
  * golden: стиль без новых ключей — .jsx побайтово прежний (плейсхолдеры пусты);
  * точка наезда — у нула Камеры 1 появляются anchor/position от неё (0.244 → y=−491.52);
  * вставки Кам2 — INS_C2_X/INS_C2_Y из стиля;
  * интро — сдвиг по X в шаблоне;
  * план несёт точку наезда и точку покоя Кам2;
  * фронт: ipvCamChild одна функция, ipvIntroPos и ipvInsPlace зовут её.
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

from core import app_meta  # noqa: E402
from core import xml2ae  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


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


def _run_node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, style=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   style=style or {}, disclaimer="", emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def test_дефолты_jsx_побайтово_прежний(xml_subs, tmp_path):
    """Стиль без новых ключей и с ключами-на-дефолте — .jsx побайтово прежний."""
    plain = _build(xml_subs, tmp_path, style={})
    off = _build(xml_subs, tmp_path, style={
        "cam1_zoom_cx": 0.5, "cam1_zoom_cy": 0.5,
        "insert_c2_x": 0.5, "insert_c2_y": 0.172, "intro_x": 0})
    assert off == plain
    # точка наезда в центре — anchor/position нула не пишем (AE сам ставит [0,0]/[W/2,H/2])
    assert 'cam1null.property("ADBE Transform Group").property("ADBE Anchor Point")' not in plain
    assert "INS_C2_X" not in plain
    assert "[0,INTRO_Y]" in plain


def test_точка_наезда_anchor_position(xml_subs, tmp_path):
    """cam1_zoom_cy=0.244 (замер из разбора) — anchor y = 0.244*H − H/2 = −491.52,
    position y = 0.244*H. Ставим ОБА: только якорь — и кадр уедет."""
    jsx = _build(xml_subs, tmp_path, style={"cam1_zoom_cx": 0.5, "cam1_zoom_cy": 0.244})
    assert "Anchor Point" in jsx
    assert "ADBE Position" in jsx
    assert "[0,-491.52]" in jsx or "[0, -491.52]" in jsx
    assert "[540,468.48]" in jsx or "[540, 468.48]" in jsx


def test_вставки_кам2_из_стиля(xml_subs, tmp_path):
    """insert_c2_y=0.688 (замер) — INS_C2_Y = round(0.688*1920) = 1321; insert_c2_x=0.3
    добавляет INS_C2_X = 324 и позиция использует его."""
    jsx = _build(xml_subs, tmp_path, style={"insert_c2_y": 0.688})
    assert "var INS_C2_Y = 1321;" in jsx
    jsx = _build(xml_subs, tmp_path, style={"insert_c2_x": 0.3})
    assert "INS_C2_X=324" in jsx
    assert "[INS_C2_X+ix, INS_C2_Y+iy]" in jsx
    # X по дефолту (0.5) — прежний W/2, INS_C2_X не объявляем
    assert "INS_C2_X" not in _build(xml_subs, tmp_path, style={"insert_c2_y": 0.688})


def test_интро_сдвиг_по_x(xml_subs, tmp_path):
    jsx = _build(xml_subs, tmp_path, style={"intro_x": 100})
    assert "[100,INTRO_Y]" in jsx
    assert "[W/2+100,H/2+INTRO_Y]" in jsx


def test_план_несёт_точку_наезда_и_покой_кам2(xml_subs):
    plan = xml2ae.scene_plan(xml_subs, disclaimer="",
                             style={"cam1_zoom_cx": 0.4, "cam1_zoom_cy": 0.244,
                                    "insert_c2_x": 0.3, "insert_c2_y": 0.688,
                                    "intro_x": 100})
    assert plan["zoom"]["cx"] == 0.4 and plan["zoom"]["cy"] == 0.244
    assert plan["ins_c2x"] == 324 and plan["ins_c2y"] == 1321
    assert plan["intro_x"] == 100


@node
def test_фронт_одна_функция_правила():
    """ipvCamChild — единственная реализация «экран = C + s*(p−C)»; и вставки кам1,
    и интро зовут её, второй копии формулы в JS нет."""
    src = app_meta.app_js_text()
    assert "function ipvCamChild(" in src
    child = _func(src, "ipvCamChild")
    assert "s*px+(1-s)*dx" in child
    ins = _func(src, "ipvInsPlace")
    intro = _func(src, "ipvIntroPos")
    assert "ipvCamChild(" in ins
    assert "ipvCamChild(" in intro
    # точка наезда кадра — из плана, а не жёсткий центр: ipvZoom больше не вешает
    # transform на видео, он зовёт ipvCamPaint, и тот режет кадр от pl.zoom.cx/cy
    zoom = _func(src, "ipvZoom")
    paint = _func(src, "ipvCamPaint")
    assert "ipvCamPaint(s)" in zoom, "ipvZoom обязан звать ipvCamPaint(s) — кадр рисует canvas"
    assert "pl.zoom.cx" in paint and "pl.zoom.cy" in paint, \
        "точка наезда в ipvCamPaint берётся из плана (pl.zoom.cx/cy), а не жёсткий центр"
    assert "cx*(vw-sw)" in paint and "cy*(vh-sh)" in paint, \
        "вырезка кадра в ipvCamPaint задаётся точкой наезда (cx*(vw-sw), cy*(vh-sh))"


@node
def test_фронт_ipvCamChild_математика():
    """Точка наезда в центре (дефолт) — s*(p); сдвинута — (1−s)*C добавляется."""
    body = "\n".join(_func(app_meta.app_js_text(), n) for n in ("ipvCamChild",))
    out = _run_node(
        "%s\nvar IPV={plan:{w:1080,h:1920,zoom:{cx:0.5,cy:0.5}}};"
        "var a=ipvCamChild(100,-50,1.5);"
        "IPV.plan.zoom={cx:0.5,cy:0.244};"
        "var b=ipvCamChild(100,-50,1.5);"
        "console.log(JSON.stringify([a,b]));" % body)
    a, b = out
    assert a == [150, -75]                       # дефолт: s*p от центра
    # C от центра = (0.244-0.5)*1920 = −491.52: y = s*(-50)+(1−s)*(−491.52)
    assert abs(b[0] - 150) < 1e-6
    assert abs(b[1] - (-75 + 0.5 * 491.52)) < 1e-6


# ===== Задание Q2 — базовая позиция интро в плане, а не в CSS =====

def _intro_ids(xml, tmp_path, style, groups, splits=None):
    """Собрать .jsx с группами интро и вернуть (INTRO_IDY, позиции прекомпов, план)."""
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / "out.jsx"),
                                   intro=groups, intro_splits=splits or [],
                                   style=style or {},
                                   disclaimer="", emit=lambda *a: None)
    jsx = open(path, encoding="utf-8-sig").read()
    idy = json.loads(re.search(r"var INTRO_IDY=(\[.*?\]);", jsx).group(1))
    pos = re.findall(r"setValue\(\[gDx,-520\.7894\+iDy\+gDy\]\)", jsx)
    plan = xml2ae.scene_plan(xml, disclaimer="", intro=groups,
                             intro_splits=splits or [], style=style or {})
    return idy, pos, plan


def test_q2_план_несёт_базовую_позицию_y(xml_subs):
    """plan.intro[].y — невзведённая позиция блока от центра кадра: INTRO_Y − 520.7894
    + iDy (+ INTRO_Y2 для кам2). gDy НЕ включаем — он живёт отдельным полем dy (задание
    E: драг правит dy в кэше), превью сложит y + dy."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[1.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3])],
        intro_splits=[1], style=dict(intro_y=500, intro_y2=80))
    g0, g1 = plan["intro"]
    assert g0["on2"] is False and g0["y"] == -20.79      # 500 − 520.7894
    assert g1["on2"] is True and g1["y"] == 59.21        # 500 + 80 − 520.7894


def test_q2_шаблон_берёт_готовый_idy(xml_subs, tmp_path):
    """Шаблон не считает iDy сам: в .jsx только INTRO_IDY[gI], без iTop/INTRO_SAFE_TOP."""
    jsx = _build(xml_subs, tmp_path, style=dict(intro_y=500))
    assert "var iDy=INTRO_IDY[gI]||0;" in jsx
    assert "iTop=" not in jsx
    assert "var INTRO_SAFE_TOP=" not in jsx
    assert "var INTRO_IDY=" in jsx


def test_q2_idy_совпадает_со_старой_формулой(xml_subs, tmp_path):
    """iDy для группы из 3 строк gs=100 = 78.1094 (та же формула, что была в шаблоне):
    iTop=H/2−520.7894−(nL/2)·160·(iSc/100); если iTop<285 → iDy=285−iTop."""
    idy, _, plan = _intro_ids(xml_subs, tmp_path, None, [
        dict(words=["А"], color="white", times=[1.0]),
        dict(words=["Б"], color="white", times=[1.0]),
        dict(words=["В"], color="white", times=[1.0]),
        dict(words=["Г"], color="white", times=[8.3])],
        splits=[3])
    assert abs(idy[0] - 78.1094) < 1e-9
    assert idy[1] == 0.0
    # в плане та же база, что в .jsx: iDy уже внутри y
    assert plan["intro"][0]["y"] == round(-520.7894 + 78.1094, 2)


def test_q2_gdy_не_в_y_а_в_dy(xml_subs):
    """Смещение группы (задание E) не дублируется в y — оно отдельным полем dy, и превью
    сложит y + dy. Иначе драг (правит dy в кэше плана) получил бы двойной счёт."""
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro=[
        dict(words=["ПЕРВОЕ"], color="white", times=[1.0]),
        dict(words=["ВТОРОЕ"], color="white", times=[8.3], gy=-40)],
        intro_splits=[1], style=dict(intro_y=500))
    g0, g1 = plan["intro"]
    assert g1["y"] == -20.79      # без gy
    assert g1["dy"] == -40.0      # gy отдельно


@node
def test_q2_превью_берёт_y_из_плана():
    """ipvIntroPos читает plan.intro[].y (база) + g.dy, а не двигает блок от CSS-padding."""
    src = app_meta.app_js_text()
    intro = _func(src, "ipvIntroPos")
    assert "g.y" in intro and "g.dy" in intro
    assert "ipvCamChild(" in intro
    css = open(os.path.join(os.path.dirname(HERE), "static", "app.css"),
               encoding="utf-8").read()
    m = re.search(r"\.ipvintro\{[^}]*\}", css)
    assert m and "padding-top:15%" not in m.group(0)
    assert "justify-content:center" in m.group(0)
