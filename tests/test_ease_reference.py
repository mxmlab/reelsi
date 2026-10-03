# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Одна кривая на все движения: эталон cubic-bezier(0.35,0.01,0.10,0.99) = 35/90.

Решение владельца 01.10.2026: у ВСЕХ анимаций одна плавная кривая — у ключа-источника
движения out-влияние 35, у ключа-цели in-влияние 90 (``HL_EASE_OUT``/``HL_EASE_IN``).
Отъезд наезда камеры переведён с S-образного 75/75 на ту же пару; превью обязано считать
ровно её.

Что стерегут тесты:

1. Числа эталона — в ОДНОМ месте (``core/xml2ae/layout.py``): в шаблон .jsx они уезжают
   подстановкой, в превью — умолчаниями ``aeEase``, в рендере без AE — импортом из
   ``layout``. Своих копий 35/90 по файлам нет; числа 75 у отъезда больше нет вовсе.
2. ``_zoom_key_eases``: каждое движение — пара [in 90, out 35], включая дрейф (mode 0);
   только одиночный ключ (движения нет) остаётся на Easy Ease.
3. Отъезд наезда в тейке (mode 3) играет той же парой, длительность отъезда не меняется.
4. Шаблон .jsx применяет эту пару К КАЖДОМУ ключу движения — проверяется исполнением
   настоящих ``temporalEase``/``easePair`` из ``template.AE_FULL`` (node).
5. Превью и план сходятся в 50 точках: боевые ``keysAt``/``aeEase`` из
   ``static/app/85-inserts-view.js`` (node) против кривой, посчитанной в Python из тех
   же влияний независимой реализацией безье.

Запуск: py -3.10 -m pytest tests/test_ease_reference.py -q -p no:cacheprovider
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.xml2ae import layout  # noqa: E402
from core.xml2ae import template as template  # noqa: E402

REF_OUT, REF_IN = 35.0, 90.0
EASE_DEFAULT = 33.3333
JS85 = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
WEBRENDER = os.path.join(ROOT, "core", "webrender.py")
LAYOUT = os.path.join(ROOT, "core", "xml2ae", "layout.py")

node = pytest.mark.skipif(not shutil.which("node"), reason="проверка требует node в PATH")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name целиком по балансу скобок (как в соседних тестах фронта)."""
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
    p = subprocess.run(["node", "-e", script], capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace").strip()[:600]
    return json.loads(p.stdout.decode("utf-8", "replace"))


# ---- 1. Одно место с числами эталона --------------------------------------------

def test_reference_numbers_live_in_one_place():
    """HL_EASE_OUT/HL_EASE_IN — единственное место с 35/90 в Python; в остальных файлах
    эталон приезжает подстановкой/импортом, а не своими литералами."""
    assert (layout.HL_EASE_OUT, layout.HL_EASE_IN) == (35, 90)
    assert _read(LAYOUT).count("HL_EASE_OUT, HL_EASE_IN = 35, 90") == 1
    # Своего числа у отъезда больше нет: эталонная пара одна на все движения зума.
    assert not hasattr(layout, "TAKE_OUT_EASE"), "TAKE_OUT_EASE (75/75) должен быть убран"
    assert re.search(r"^TAKE_OUT_EASE\s*=", _read(LAYOUT), re.M) is None

    # .jsx: объявление — подстановка, не литералы
    assert "var HL_EASE_OUT = %(hl_ease_out)d, HL_EASE_IN = %(hl_ease_in)d" in template.AE_FULL

    # рендер без AE: пара приходит импортом из layout, своих литералов нет
    wr = _read(WEBRENDER)
    assert "from core.xml2ae.layout import HL_EASE_IN, HL_EASE_OUT" in wr
    assert re.search(r"or\s*\[35,\s*90\]", wr) is None, "в webrender своя копия пары 35/90"
    assert re.search(r"\(\s*0\.35,\s*0\.10\s*\)", wr) is None, "в webrender своя копия кривой"

    # превью: пара живёт в умолчаниях aeEase — и только там
    js = _read(JS85)
    assert "if(out==null)out=35;if(inp==null)inp=90;" in js
    assert "aeEase(35,90)" not in js, "у ipvEase своя копия пары 35/90"
    assert "ease[i][1]:35" not in js and "ease[i+1][0]:90" not in js, \
        "у keysAt своя копия пары 35/90 вместо умолчаний aeEase"


# ---- 2-3. Кривые ключей зума -----------------------------------------------------

def test_zoom_eases_reference_for_every_movement():
    """Любое движение зума — [in 90, out 35]: и откат pulse вниз, и подъём вверх,
    и дрейф (mode 0); HOLD-ключ (скачок) задаётся через CAM1_HOLDS, а не ease."""
    # pulse без тейка: 2-элементные ключи (наезд big→100 и подъёмы к пикам)
    pulse = [(0, 182), (62, 100), (856, 136.9), (918, 100)]
    assert layout._zoom_key_eases(pulse) == [[90, 35]] * 4

    # jump без тейка: 4-элементные ключи с hold — форма ease эталонная [90, 35],
    # а скачок на склейке держится за счёт CAM1_HOLDS
    jump = [(0, 182, 1, 0), (62, 100, 2, 1), (500, 120, 0, 1)]
    assert layout._zoom_key_eases(jump) == [[90, 35], [90, 35], [90, 35]]

    # дрейф: mode 0 — медленное движение между склейками, тоже эталон 90/35 (решение 02.10.2026)
    drift = [(0, 182, 1), (62, 100, 2), (100, 120, 0), (160, 110, 0)]
    assert layout._zoom_key_eases(drift) == [[90, 35], [90, 35],
                                             [REF_IN, REF_OUT],
                                             [REF_IN, REF_OUT]]


def test_take_out_ease_is_reference_and_duration_kept():
    """Отъезд наезда (mode 3): та же пара 35/90, длительность TAKE_OUT_S = 2.4 с."""
    keys = [(96, 100, 1, 0), (192, 131.4, 2, 1), (312, 131.4, 3, 0), (456, 100.0, 3, 1)]
    eases = layout._zoom_key_eases(keys)
    assert eases[2] == [REF_IN, REF_OUT] and eases[3] == [REF_IN, REF_OUT]
    assert layout.TAKE_OUT_S == 2.4
    # длительность отъезда — от режимов не зависит
    assert (keys[3][0] - keys[2][0]) / 60.0 == pytest.approx(2.4)


def test_start_key_without_punch_is_a_movement_source():
    """«Первый наезд» снят (start=False) + тейки: стартовый ключ 100 % — ИСТОЧНИК
    движения к пику, а не дрейф: у него out эталонной кривой (mode 1)."""
    keys = layout._cam1_zoom_keys([{"clips": [[0, 3600, 0, "cam1.mov", True]]}],
                                  big=182.0, lo=112.0, hi=140.0, fps=60.0, start=False,
                                  take={"min_s": 3.0, "lo": 25.0, "hi": 40.0, "hold_s": 0.5,
                                        "out_s": 1.0, "long_on": True, "yellow_on": False})
    assert keys and keys[0] == (0.0, 100.0, 1, 0), keys[:2]
    assert layout._zoom_key_eases(keys)[0] == [REF_IN, REF_OUT]


def test_single_key_has_no_movement():
    """Один ключ — движения нет: пара влияний не играет, остаётся Easy Ease."""
    assert layout._zoom_key_eases([(0, 100)]) == [[EASE_DEFAULT, EASE_DEFAULT]]


# ---- 4. Шаблон применяет пару к каждому ключу движения ---------------------------

def _ease_apply():
    """temporalEase и _LOG из боевого шаблона + мок-обвязка (как в test_temporal_ease)."""
    return _func(template.AE_FULL, "temporalEase"), _func(template.AE_FULL, "_LOG")


@node
def test_template_applies_reference_to_every_movement_key():
    """Настоящий ``temporalEase`` из .jsx получает eIns/eOuts от ``_zoom_key_eases``:
    каждый ключ движения — in 90 / out 35 (это и есть кривая easePair в AE), включая
    дрейф зума."""
    ease, log = _ease_apply()
    keys = ([(0, 182, 1, 0), (62, 100, 2, 1)]                    # импульс на входе
            + [(500, 100, 1, 0), (560, 131.4, 2, 1)]            # наезд в тейке
            + [(680, 131.4, 3, 0), (824, 100.0, 3, 1)]          # отъезд наезда
            + [(900, 140, 0, 0), (1000, 120, 0, 0)])            # дрейф
    eases = layout._zoom_key_eases(keys)
    script = (
        "var log=[], _log=null, _pending=[];\n"
        "%s\n"                                   # _LOG
        "var $={writeln:function(s){log.push('W:'+s);}};\n"
        "function KeyframeEase(spd,inf){ this.spd=spd; this.inf=inf; }\n"
        "var ok=[];\n"
        "var p={numKeys:%d, name:'Scale', value:{length:1},\n"
        "  setTemporalEaseAtKey:function(k,ai,ao){ ok.push([k, ai[0].inf, ao[0].inf]); } };\n"
        "%s\n"                                   # temporalEase
        "temporalEase(p, %s, %s);\n"
        "console.log(JSON.stringify({ok:ok, log:log}));"
    ) % (log, len(keys), ease, json.dumps([e[0] for e in eases]),
         json.dumps([e[1] for e in eases]))
    r = _run_node(script)
    assert r["log"] == [], r
    assert len(r["ok"]) == len(keys)
    for key_no, inn, out in r["ok"]:
        assert (inn, out) == (REF_IN, REF_OUT), (key_no, inn, out)


# ---- 5. Превью и план: 50 точек --------------------------------------------------

def _bezier_value(keys, ease, t):
    """Значение ключей в момент t по влияниям [in, out] — независимая от JS реализация
    (бисекция по параметру кривой вместо ньютона в ``bezierT``)."""
    if t <= keys[0][0]:
        return float(keys[0][1])
    if t >= keys[-1][0]:
        return float(keys[-1][1])
    for i in range(len(keys) - 1):
        f0, v0 = float(keys[i][0]), float(keys[i][1])
        f1, v1 = float(keys[i + 1][0]), float(keys[i + 1][1])
        if not f0 <= t < f1:
            continue
        p1x = ease[i][1] / 100.0
        p2x = 1.0 - ease[i + 1][0] / 100.0
        u = (t - f0) / (f1 - f0)
        lo, hi = 0.0, 1.0
        for _ in range(80):
            q = (lo + hi) / 2.0
            w = 1.0 - q
            x = 3 * w * w * q * p1x + 3 * w * q * q * p2x + q * q * q
            if x < u:
                lo = q
            else:
                hi = q
        q = (lo + hi) / 2.0
        w = 1.0 - q
        y = 3 * w * q * q + q * q * q
        return v0 + (v1 - v0) * y
    return float(keys[-1][1])


@node
def test_preview_matches_plan_in_fifty_points():
    """Превью (боевые ``keysAt``/``aeEase``/``bezierT``/``bezierY`` из 85-inserts-view.js)
    и план (те же ключи и влияния, посчитанные в Python) дают одно значение в 50 точках."""
    keys = [(0, 182), (62, 100), (856, 136.9), (918, 100), (1600, 128.6), (1662, 100)]
    ease = layout._zoom_key_eases(keys)
    src = _read(JS85)
    body = "\n".join(_func(src, n) for n in ("bezierY", "bezierT", "aeEase", "keysAt"))
    py_keys = [[k[0], float(k[1])] for k in keys]
    t0, t1 = keys[0][0], keys[-1][0]
    times = [t0 + (t1 - t0) * i / 49.0 for i in range(50)]
    script = ("%s\nvar KEYS=%s,EASE=%s,T=%s;\n"
              "console.log(JSON.stringify(T.map(function(t){return keysAt(KEYS,EASE,t,false);})));"
              % (body, json.dumps(py_keys), json.dumps(ease), json.dumps(times)))
    got = _run_node(script)
    assert len(got) == 50
    assert max(got) > 150.0, "набор ключей не разгоняет зум — проверять нечего"
    for t, preview in zip(times, got):
        mine = _bezier_value(py_keys, ease, t)
        assert abs(mine - preview) <= 0.01, f"момент {t}: план {mine}, превью {preview}"


@node
def test_preview_reference_curve_is_the_same_as_keys_at_default():
    """ ``ipvEase`` (кривая появления интро) и ``keysAt`` без готового ease — одна и та
    же эталонная кривая: 50 точек совпадают до четвёртого знака."""
    src = _read(JS85)
    body = "\n".join(_func(src, n) for n in ("bezierY", "bezierT", "aeEase", "keysAt", "ipvEase"))
    script = ("%s\nvar U=[];for(var i=0;i<50;i++)U.push(i/49);\n"
              "var A=U.map(function(u){return +keysAt([[0,0],[1,1]],null,u,false).toFixed(6);});\n"
              "var B=U.map(function(u){return +ipvEase(u).toFixed(6);});\n"
              "console.log(JSON.stringify([A,B]));" % body)
    a, b = _run_node(script)
    assert a == b, "ipvEase и дефолт keysAt считают разные кривые"
    assert a[25] > 0.6, a[25]          # эталон 35/90 крутой: на середине ~0.85


@node
def test_preview_matches_plan_drift_keys():
    """Превью и план совпадают на ключах дрейфа: drift играет по эталонной кривой 35/90."""
    drift_keys = [(0, 182.0, 1), (62, 100.0, 2), (100, 120.0, 0), (160, 110.0, 0)]
    ease = layout._zoom_key_eases(drift_keys)
    assert ease == [[REF_IN, REF_OUT]] * 4
    src = _read(JS85)
    body = "\n".join(_func(src, n) for n in ("bezierY", "bezierT", "aeEase", "keysAt"))
    py_keys = [[k[0], float(k[1])] for k in drift_keys]
    t0, t1 = drift_keys[0][0], drift_keys[-1][0]
    times = [t0 + (t1 - t0) * i / 49.0 for i in range(50)]
    script = ("%s\nvar KEYS=%s,EASE=%s,T=%s;\n"
              "console.log(JSON.stringify(T.map(function(t){return keysAt(KEYS,EASE,t,false);})));"
              % (body, json.dumps(py_keys), json.dumps(ease), json.dumps(times)))
    got = _run_node(script)
    assert len(got) == 50
    for t, preview in zip(times, got):
        mine = _bezier_value(py_keys, ease, t)
        assert abs(mine - preview) <= 0.01, f"момент {t}: план {mine}, превью {preview}"

