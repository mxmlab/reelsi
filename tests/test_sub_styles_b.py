# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стили субтитров из каталога (партия 2): подложка слова, градиент, свечение, глитч.

Продолжение пилота (`tests/test_sub_anim.py`) и партии 1
(`tests/test_sub_anim_styles.py`): тот же путь данных на три выхода — числа считает
Python (`core/xml2ae/layout.py`, `plan_subs.py`), план несёт готовые ключи, .jsx ставит
их на слой слова, превью интерполирует те же ключи на DOM-спан. Ни .jsx, ни превью
своих формул не держат.

Проверяется:

1. подложка слова (`sub_wbg_*`) — ОДИН шейп-слой на всю сборку с ключами по таймингам
   слов: у маркера ширина раскрывается за `sweep`, у таблетки появляется целиком;
   момент слова считает Python (в строке — время произнесения, зажатое в окно строки);
2. подложка — не плашка под строкой (`sub_bg_*`): у той ширина живёт ВЫРАЖЕНИЕМ от всей
   строки, она стоит на месте и лежит в главном композе под прекомпом субтитров, а
   фигура подложки — в прекомпе под словами и прыгает по словам;
3. заливка текста градиентом (`sub_fill_mode`) — эффект Gradient Ramp на слое слова с
   цветами и углом из плана, вызов в каждом из четырёх циклов слов;
4. свечение текста (`sub_glow_*`) — Glo2 на слоях слов, как у интро, с цветом в Color A/B
   и веткой «только жёлтые»;
5. пресет появления `glitch` — ТОТ ЖЕ глитч, что у строк интро: в .jsx зовётся
   `introAnimFX`, числа общие (layout.SUB_GLITCH_*), а в плане лежат ключи мерцания;
6. превью (node, боевые функции static/app/85-inserts-view.js): подложка встаёт за
   ТЕКУЩИМ словом по ключам плана, градиент и свечение — теми же числами, а тень
   градиентного слова играет ОТДЕЛЬНЫМ слоем под ним (на буквах она просвечивала бы
   сквозь прозрачные буквы и слово выходило тёмным), глитч мерцает по ключам плана;
   мутации «подложка не за текущим словом», «тень снова на буквах» и «свечение без
   имени свойства / без drop-shadow» красят стенд;
7. умолчания: все новые ключи выключены, собранный .jsx не меняется ни на байт
   (эталон fixtures/golden_geometry.jsx), разметка полей в core/style_geometry.py есть.
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
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_geometry, styles, xml2ae  # noqa: E402
from core.xml2ae import build as _build_mod  # noqa: E402
from core.xml2ae import layout  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402
# Стенд превью (эмуляция DOM) и вырезка боевых функций — ОДНА на все модули стилей:
# вторая копия стенда разошлась бы с первой молча.
from tests.test_sub_anim import _DOM_SIM, _ipv_code  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

YELLOW = 3          # индекс жёлтого слова фикстуры
ROUND = 24.0        # скругление подложки в тестах (дефолт стиля в проверках не участвует)
PAD = 30.0          # поле подложки по бокам, px
HH = 150.0          # высота подложки, px
SWEEP = 0.2         # раскрытие маркера, с
WBG = {"sub_wbg_on": True, "sub_wbg_round": ROUND, "sub_wbg_pad": PAD, "sub_wbg_h": HH,
       "sub_wbg_sweep": SWEEP}


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=(YELLOW,)):
    return xml2ae.scene_plan(xml, highlights=list(highlights), style=dict(style or {}),
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=(YELLOW,), hl_joins=None):
    path, _n, _s = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                     highlights=list(highlights), style=dict(style or {}),
                                     hl_joins=(list(hl_joins) if hl_joins else None),
                                     inserts=[], disclaimer="", intro=None, roto=False,
                                     intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _words(plan):
    """Все слова плана по порядку: элементы режима «по слову», слова строк и стопки."""
    out = []
    for it in plan["subs"]:
        if it.get("words"):
            out.extend(it["words"])
        else:
            out.append(it)
    return out


def _preview_plan(plan):
    """Только то, что читает ipvSubs: числа превью берёт из плана, своих не держит."""
    keys = ("w", "h", "posy", "fsize", "sub_step", "hl_step", "hl_rise", "hl_dur",
            "hl_blur", "hl_blur_amt", "subs", "hl_size_k", "sub_anim_font",
            "sub_wbg", "sub_grad", "sub_glow")
    return {k: plan[k] for k in keys if k in plan}


def _run_preview(tmp_path, name, plan, checks, mutate="", sim_extra="", src=()):
    """Прогнать боевую отрисовку субтитров в node: эмуляция DOM + проверки.

    `mutate` — готовая мутация (кусок JS перед проверками), `sim_extra` — настройка
    стенда, `src` — мутации САМОГО кода превью (пары «что заменить» -> «на что»).
    Откат правки проверяется ими: стенд обязан краснеть на возврате старого поведения,
    и проверяется это тем же прогоном, а не руками. Стенд и вырезка функций те же, что
    у партий 0–1: копия разошлась бы молча.
    """
    code = _ipv_code()
    for old, new in src:
        assert old in code, "мутация не нашлась в коде превью: %r" % (old,)
        code = code.replace(old, new, 1)
    script = (_DOM_SIM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + code + "\n" + sim_extra + "\n" + mutate + "\n"
              + "const plan=IPV.plan;\n" + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    return subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


def _expect_ok(res, what):
    assert res.returncode == 0, "%s: node упал: %s" % (
        what, ((res.stderr or "") + (res.stdout or ""))[-2000:])
    assert "OK" in res.stdout, "%s: проверки превью не прошли: %s" % (what, res.stdout)


def _expect_red(res, what):
    """Мутация обязана ПОКРАСНЕТЬ: иначе проверка стережёт пустоту."""
    assert res.returncode != 0, "%s: мутация прошла незамеченной: %s" % (what, res.stdout)
    return (res.stderr or "") + (res.stdout or "")


def _count(jsx, token):
    """Сколько раз строка есть в .jsx: цикл написан один раз, слова в нём перебираются."""
    return jsx.count(token)


def _grad_calls(jsx):
    """Вызовы градиента во всех циклах слов: у каждого своё выражение времени слова."""
    return (jsx.count(" subGrad(L, sw[0]/FPS);") + jsx.count(" subGrad(L, kw[0]/FPS);")
            + jsx.count(" subGrad(L, wd[0]/FPS);"))


def _wbg_calls(jsx):
    """Вызовы подложки по циклам: {имя цикла: сколько раз строка есть в .jsx}.

    Цикл в .jsx написан ОДИН раз, а слова перебираются в нём: поэтому «вызов есть» —
    это ровно 1 на свой цикл и 0 у чужих. Пропущенный цикл виден как 0.
    """
    return {
        "words": jsx.count("wbgKeys(L, t0, sw[1]/FPS, SW/2, POSY)"),
        "hl": jsx.count("wbgKeys(L, t0, sw[5]/FPS, SW/2, finalY)"),
        "hl_joined": jsx.count("wbgKeys(L, t0, rsw[5]/FPS, wCenter, finalY)"),
        "row": jsx.count("wbgKeys(L, (r_words[wi][4]!=null?r_words[wi][4]:r_t0s[wi])"),
    }


# --------------------------------------------------------------------------- #
# 1. Подложка слова: ОДИН слой с ключами, план несёт моменты слов
# --------------------------------------------------------------------------- #
def test_wbg_is_one_shape_layer_with_keys(xml_subs, tmp_path):
    """Подложка — ОДИН шейп-слой в прекомпе субтитров и ключи на нём, а не слой на слово."""
    jsx = _jsx(xml_subs, tmp_path, "wbg.jsx", WBG)
    assert jsx.count("subc.layers.addShape()") == 1, \
        "подложка завела не один шейп-слой: слов в ролике сотни"
    assert 'wbgLayer.name = "Подложка слова"' in jsx
    # Числа фигуры — из стиля, второй копии нет.
    assert 'wbgR.property("ADBE Vector Rect Roundness").setValue(%g);' % ROUND in jsx
    assert 'wbgFi.property("ADBE Vector Fill Color").setValue([1,0.9176,0]);' in jsx
    assert "var w = rr.width + 2*%g, hh = %g;" % (PAD, HH) in jsx
    # Ключи: позиция прыгает (HOLD), ширина маркера раскрывается кривой.
    assert "wbgP.setValueAtTime(tM, [mx, my]);" in jsx
    assert "wbgSz.setValueAtTime(tM, [1, hh]);" in jsx
    assert "wbgSz.setValueAtTime(tM+%g, [w, hh]);" % SWEEP in jsx
    assert "temporalEase(wbgSz, HL_EASE_IN, HL_EASE_OUT);" in jsx
    assert jsx.count("KeyframeInterpolationType.HOLD") >= 3, \
        "у подложки нет держащих ключей: фигура «ползла» бы между словами"
    # Хвост: окна показа одним списком и точка входа слоя.
    assert "WBG_W.push([tM, 100]); WBG_W.push([outT, 0]);" in jsx
    assert "wbgLayer.inPoint = WBG_W[0][0];" in jsx
    assert "wbgLayer.outPoint = WBG_W[WBG_W.length-1][0];" in jsx


def test_wbg_calls_sit_in_every_word_loop(xml_subs, tmp_path):
    """Вызов ключей подложки есть в КАЖДОМ цикле слов: пропустишь один — молчит режим."""
    words = _jsx(xml_subs, tmp_path, "w_words.jsx", WBG)
    calls = _wbg_calls(words)
    assert calls["words"] == 1 and calls["hl"] == 1, \
        "в режиме «по слову» подложка не зовётся: %r" % (calls,)
    assert calls["row"] == 0 and calls["hl_joined"] == 0
    rows = _jsx(xml_subs, tmp_path, "w_rows.jsx", dict(WBG, sub_words_per_row=2))
    assert _wbg_calls(rows)["row"] == 1, "в цикле строк подложка не зовётся"
    assert _wbg_calls(rows)["words"] == 0, "в режиме строк остался чужой вызов"
    joins = _jsx(xml_subs, tmp_path, "w_join.jsx", WBG, highlights=[], hl_joins=[2, 3])
    assert _wbg_calls(joins)["words"] == 1, "в цикле со склейками подложка не зовётся"
    stack = _jsx(xml_subs, tmp_path, "w_stack.jsx",
                 dict(WBG, sub_words_per_row=2, hl_row_stack=True), highlights=(3, 4))
    assert _wbg_calls(stack)["hl"] == 1, "в цикле стопки подложка не зовётся"
    stack_join = _jsx(xml_subs, tmp_path, "w_stack_join.jsx",
                      dict(WBG, sub_words_per_row=2, hl_row_stack=True),
                      highlights=(3, 4), hl_joins=[3, 4])
    assert _wbg_calls(stack_join)["hl_joined"] == 1, \
        "в стопке со склейками подложка не зовётся вовсе"


def test_wbg_pill_jumps_instead_of_sweeping(xml_subs, tmp_path):
    """Таблетка: ширина целая с первого кадра и стоит HOLD — она прыгает, а не раскрывается."""
    jsx = _jsx(xml_subs, tmp_path, "wbg_pill.jsx", dict(WBG, sub_wbg_kind="pill"))
    assert "wbgSz.setValueAtTime(tM, [w, hh]);" in jsx
    assert "wbgSz.setValueAtTime(tM+%g, [w, hh]);" % SWEEP not in jsx, \
        "у таблетки осталось раскрытие маркера"
    assert "Таблетка: ширина меняется СТУПЕНЬКОЙ" in jsx
    assert "(ws%2)?KeyframeInterpolationType.BEZIER" not in jsx, \
        "у таблетки ширина идёт кривой — она обязана прыгать"


def test_wbg_plan_carries_the_moments(xml_subs):
    """План: числа фигуры и момент КАЖДОГО слова — по нему превью выбирает текущее слово."""
    plan = _plan(xml_subs, WBG)
    assert plan["sub_wbg"] == {"kind": "highlight", "fill": [1.0, 0.9176, 0.0], "op": 100.0,
                               "h": HH, "round": ROUND, "pad": PAD, "dy": 0.0,
                               "sweep": SWEEP}
    off = _plan(xml_subs, None)
    assert "sub_wbg" not in off and all("wbg" not in it for it in _words(off)), \
        "выключенная подложка оставила следы в плане"
    # Режим «по слову»: момент слова — его собственное время (план печатает 4 знака).
    for it in plan["subs"]:
        assert abs(it["wbg"]["t"] - it["s"]) < 5e-5, "момент слова не совпал с его началом"
    # Режим строк: слово стоит в строке с её начала, а звучит позже — момент зажат в окно
    # строки и у слов РАЗНЫЙ (иначе караоке прыгало бы не по слову).
    rows = _plan(xml_subs, dict(WBG, sub_words_per_row=2))
    line = next(it for it in rows["subs"] if len(it.get("words") or []) > 1)
    ts = [wd["wbg"]["t"] for wd in line["words"]]
    assert ts == sorted(ts) and ts[0] >= line["s"] and ts[-1] <= line["e"], \
        "моменты слов строки не в её окне: %r" % (ts,)
    assert ts[0] != ts[-1] or len(ts) == 1, \
        "все слова строки получили один момент — караоке не перескочит"


def test_wbg_is_not_the_row_plate(xml_subs, tmp_path):
    """Подложка слова — НЕ плашка под строкой: разные механизмы и разные слои.

    Плашка (sub_bg) — прямоугольник на всю строку, её ширина считается ВЫРАЖЕНИЕМ от
    `sourceRectAtTime` слоя субтитров, и лежит она в ГЛАВНОМ композе под прекомпом
    субтитров. Подложка слова живёт ключами по таймингам слов и создаётся ВНУТРИ
    прекомпа, под словами. Поэтому это две группы настроек, а не режим одной.
    """
    plate = _jsx(xml_subs, tmp_path, "plate.jsx", {"sub_bg": True})
    assert "Фон субтитров" in plate and "Подложка слова" not in plate
    assert "main.layers.addShape()" in plate, "плашка уехала из главного композа"
    back = _jsx(xml_subs, tmp_path, "wbg_only.jsx", WBG)
    assert "Фон субтитров" not in back, "подложка слова притащила плашку под строку"
    assert "main.layers.addShape()" not in back, "подложка слова уехала в главный композ"
    # У плашки ширина — выражение; у подложки — ключи. Своих ключей у плашки нет.
    plate_bg = plate.split("Фон субтитров")[1][:800]
    assert ".expression =" in plate_bg and "setValueAtTime" not in plate_bg, \
        "у плашки завелись ключи вместо выражения"


# --------------------------------------------------------------------------- #
# 2. Заливка текста градиентом
# --------------------------------------------------------------------------- #
def test_grad_adds_ramp_to_every_word_loop(xml_subs, tmp_path):
    """Градиент: Gradient Ramp на слое слова, цвета и угол — из плана, вызов во всех циклах."""
    st = {"sub_fill_mode": "gradient", "sub_grad_from": [0.1, 0.2, 0.3],
          "sub_grad_to": [0.9, 0.8, 0.7], "sub_grad_angle": 45}
    jsx = _jsx(xml_subs, tmp_path, "grad.jsx", st)
    assert "var GRAD_FROM = [0.1,0.2,0.3], GRAD_TO = [0.9,0.8,0.7], GRAD_ANG = 45;" in jsx, \
        "числа градиента не уехали в .jsx"
    assert 'addProperty("ADBE Ramp")' in jsx
    assert 'gRamp.property("ADBE Ramp-0002").setValue(GRAD_FROM);' in jsx
    assert 'gRamp.property("ADBE Ramp-0004").setValue(GRAD_TO);' in jsx
    assert 'gRamp.property("ADBE Ramp-0007").setValue(0);' in jsx
    # Линия градиента — по прямоугольнику слова, той же формулой, что CSS.
    assert "var glen = Math.abs(gw*gdx) + Math.abs(gh*gdy);" in jsx
    assert "var gdx = Math.sin(ga), gdy = -Math.cos(ga);" in jsx
    assert jsx.count(" subGrad(L, sw[0]/FPS);") == 1, "нет вызова в цикле «по слову»"
    rows = _jsx(xml_subs, tmp_path, "grad_rows.jsx", dict(st, sub_words_per_row=2))
    assert rows.count(" subGrad(L, wd[0]/FPS);") == 1, "нет вызова в цикле строк"
    stack = _jsx(xml_subs, tmp_path, "grad_stack.jsx",
                 dict(st, sub_words_per_row=2, hl_row_stack=True), highlights=(3, 4))
    assert stack.count(" subGrad(L, sw[0]/FPS);") == 1, "нет вызова в цикле стопки"
    plan = _plan(xml_subs, st)
    assert plan["sub_grad"] == {"mode": "gradient", "from": [0.1, 0.2, 0.3],
                                "to": [0.9, 0.8, 0.7], "angle": 45}
    assert "sub_grad" not in _plan(xml_subs, None), "сплошная заливка уехала в план"


def test_grad_is_absent_by_default(xml_subs, tmp_path):
    """Сплошная заливка (дефолт): ни эффекта, ни функции, ни вызовов — .jsx прежний."""
    jsx = _jsx(xml_subs, tmp_path, "grad_off.jsx", None)
    for token in ("subGrad", "GRAD_FROM", "ADBE Ramp"):
        assert token not in jsx, "умолчание оставило в .jsx подстановку %s" % token
    assert _grad_calls(jsx) == 0, "вызовы градиента остались в .jsx при сплошной заливке"


# --------------------------------------------------------------------------- #
# 3. Свечение текста
# --------------------------------------------------------------------------- #
def test_glow_adds_glo2_like_intro(xml_subs, tmp_path):
    """Свечение: Glo2 на слоях слов с числами стиля, цвет — в Color A/B, порог как у интро."""
    st = {"sub_glow_on": True, "sub_glow_amt": 2.5, "sub_glow_rad": 120.0,
          "sub_glow_fill": [0.0, 1.0, 0.5]}
    jsx = _jsx(xml_subs, tmp_path, "glow.jsx", st)
    assert "var GLOW_RAD = 120, GLOW_INT = 2.5, GLOW_FILL = [0,1,0.5], GLOW_YEL = false;" in jsx
    assert 'L.property("ADBE Effect Parade").addProperty("ADBE Glo2")' in jsx
    assert 'gGl.property("ADBE Glo2-0002").setValue(%d);' % layout.SUB_GLOW_THR in jsx, \
        "порог свечения разошёлся с тем, что у интро"
    assert 'gGl.property("ADBE Glo2-0003").setValue(GLOW_RAD);' in jsx
    assert 'gGl.property("ADBE Glo2-0004").setValue(GLOW_INT);' in jsx
    assert 'gGl.property("ADBE Glo2-0007").setValue(2);' in jsx, \
        "цвет свечения не включён: Glo2 светил бы цветом буквы"
    assert jsx.count('gGl.property("ADBE Glo2-0012").setValue(GLOW_FILL);') == 1
    assert jsx.count(" subGlow(L, hl);") == 1, "свечение не зовётся из цикла «по слову»"
    rows = _jsx(xml_subs, tmp_path, "glow_rows.jsx", dict(st, sub_words_per_row=2))
    assert _count(rows, " subGlow(L, w_hl);") == 1, "нет вызова в цикле строк"
    stack = _jsx(xml_subs, tmp_path, "glow_stack.jsx",
                 dict(st, sub_words_per_row=2, hl_row_stack=True), highlights=(3, 4))
    assert stack.count(" subGlow(L, true);") == 1, "в стопке признак «жёлтое» не передан"
    plan = _plan(xml_subs, st)
    assert plan["sub_glow"] == {"amt": 2.5, "rad": 120.0, "fill": [0.0, 1.0, 0.5],
                                "yellow": False}
    assert "sub_glow" not in _plan(xml_subs, None)


def test_glow_yellow_only_branch(xml_subs, tmp_path):
    """Галка «только жёлтые»: свечение получают лишь выделенные слова, как introHlGlow."""
    jsx = _jsx(xml_subs, tmp_path, "glow_y.jsx", {"sub_glow_on": True,
                                                 "sub_glow_yellow": True})
    assert "GLOW_YEL = true;" in jsx
    assert "if (GLOW_YEL && !hl) return;" in jsx
    assert "«только жёлтые»: белым свечения нет" in jsx
    # Признак «жёлтое слово» у каждого цикла свой — свой и вызов.
    assert " subGlow(L, hl);" in jsx and " subGlow(L, w_hl);" not in jsx


def test_glow_is_absent_by_default(xml_subs, tmp_path):
    """Выключенное свечение не оставляет в .jsx ни эффекта, ни функции.

    Токен «ADBE Glo2» тут не годится: Glo2 есть у дисклеймера (он в шаблоне всегда) —
    поэтому ищем подстановки самой ручки.
    """
    jsx = _jsx(xml_subs, tmp_path, "glow_off.jsx", None)
    for token in ("subGlow", "GLOW_RAD", "GLOW_YEL", 'gGl.property'):
        assert token not in jsx, "умолчание оставило в .jsx подстановку %s" % token


# --------------------------------------------------------------------------- #
# 4. Пресет появления «глитч» — тот же, что у интро
# --------------------------------------------------------------------------- #
def test_glitch_plan_keys_are_the_intro_glitch(xml_subs):
    """План: ключи мерцания — те же числа, что играет глитч интро, и в абсолютном времени."""
    plan = _plan(xml_subs, {"sub_anim": "glitch"}, highlights=[])
    words = _words(plan)
    assert words, "в фикстуре нет белых слов"
    for wd in words:
        a = wd["anim"]
        assert a["name"] == "glitch" and a["dur"] == layout.SUB_GLITCH_DUR
        t0 = wd["s"]
        want = [[round(t0 + kt, 4), kv] for kt, kv in layout.SUB_GLITCH_OP_KEYS]
        assert a["op"] == want, "ключи мерцания не совпали с глитчем интро: %r" % (a["op"],)
    # Своя длительность стиля тянет за собой и ритм мерцания: ключи заданы долями.
    own = _plan(xml_subs, {"sub_anim": "glitch", "sub_anim_dur": 0.88}, highlights=[])
    a0 = _words(own)[0]["anim"]
    assert a0["dur"] == 0.88
    assert a0["op"][1][0] == round(_words(own)[0]["s"] + 0.1, 4), \
        "длительность стиля не растянула ключи мерцания"


def test_glitch_jsx_calls_the_intro_function(xml_subs, tmp_path):
    """В .jsx глитч субтитров — вызов introAnimFX: второго глитча в сборке не заводится."""
    jsx = _jsx(xml_subs, tmp_path, "glitch.jsx", {"sub_anim": "glitch"})
    assert "function introAnimFX(" in jsx, \
        "функции глитча интро нет — вызов ушёл бы в try/catch и молчал"
    assert jsx.count('try{ introAnimFX(L, t0, "glitch", null, null, null, null, null, null); }') == 1
    body = re.search(r"function subAnimKeys\(L, t0, y, half\)\{(.*?)\n    \}", jsx, re.S)
    assert body, "в .jsx нет функции subAnimKeys"
    assert "introAnimFX(L, t0, \"glitch\"" in body.group(1)
    # Своих ключей мерцания пресет не ставит: их поставит introAnimFX.
    assert "op.setValueAtTime" not in body.group(1), \
        "у глитча завелись свои ключи прозрачности — глитчей стало два"
    assert "HL_W_DUR = %g" % layout.SUB_GLITCH_DUR in jsx
    # Числа — ОДИН источник: таблица интро собирается из тех же констант layout.
    assert _build_mod.INTRO_ANIMS["glitch"]["op_keys"] == layout.SUB_GLITCH_OP_KEYS
    assert _build_mod.INTRO_ANIMS["glitch"]["dur"] == layout.SUB_GLITCH_DUR


def test_glitch_off_by_default(xml_subs, tmp_path):
    """`none` (дефолт): ни вызова, ни функции глитча — .jsx прежний."""
    jsx = _jsx(xml_subs, tmp_path, "glitch_off.jsx", None)
    assert "subAnimKeys" not in jsx and "introAnimFX" not in jsx


# --------------------------------------------------------------------------- #
# 5. Превью: подложка за ТЕКУЩИМ словом, градиент, свечение, глитч
# --------------------------------------------------------------------------- #
def _long_words(plan):
    """Белые слова плана, у которых есть момент подложки, по возрастанию момента."""
    out = [wd for wd in _white_words(plan) if wd.get("wbg")]
    out.sort(key=lambda wd: wd["wbg"]["t"])
    return out


def _white_words(plan):
    """Белые слова плана: подложка и глитч живут только на них (у жёлтых своя анимация)."""
    return [wd for wd in _words(plan) if wd.get("color") != "yellow"]


@node
def test_preview_wbg_stands_behind_the_current_word(xml_subs, tmp_path):
    """Подложка встаёт за текущим словом, раскрывается по ключам плана и переходит дальше."""
    plan = _plan(xml_subs, WBG, highlights=[])
    seq = _long_words(plan)
    assert len(seq) > 3, "в фикстуре мало слов с моментом подложки"
    cur, prev = seq[2], seq[1]
    checks = r"""
const cur=%(cur_w)s, prev=%(prev_w)s, t0=%(t0)s, tp=%(tp)s, sweep=%(sweep)s, tm=%(tm)s;
function mk(){return host().querySelector('.pvsub_wbg');}
function cqw(v,w){return (v/w*100).toFixed(3)+'cqw';}
const el=$('ipvsub'), w=plan.w;
// Момент каждого слова лежит в разметке (пришёл из плана): по нему и выбирается текущее.
paint(plan,tp);
const psp=words().filter(s=>s.textContent===prev)[0];
assert(psp,'не нашёлся спан предыдущего слова');
assert.strictEqual(psp.dataset.wbg,String(tp),'момент слова не доехал до разметки');
let pm=mk();
assert(pm,'подложки нет, хотя слово звучит');
assert.strictEqual(pm.dataset.w,''.concat(prev),'подложка стоит не за текущим словом');
const leftPrev=pm.style.left;
// Кадр текущего слова: фигура уехала за НИМ, а не осталась на предыдущем.
paint(plan,tm);
const sp=words().filter(s=>s.textContent===cur)[0];
assert(sp,'не нашёлся спан слова '+cur);
let m=mk();
assert(m,'подложка исчезла на текущем слове');
assert.strictEqual(m.dataset.w,''.concat(cur),'подложка стоит не за текущим словом: '+m.dataset.w);
assert.notStrictEqual(m.style.left,leftPrev,'подложка осталась на предыдущем слове');
// Геометрия — по прямоугольнику ТЕКУЩЕГО слова и числам плана.
const box=sp.getBoundingClientRect(), hb=host().getBoundingClientRect();
const k=w/(el.clientWidth||w);
const full=box.width*k+2*plan.sub_wbg.pad;
const frac=keysAt([[t0,0],[t0+sweep,100]],null,tm)/100;
assert(frac>0&&frac<1,'середина раскрытия обязана быть промежуточной: '+frac);
assert(Math.abs(parseFloat(m.style.width)-full*frac/w*100)<0.01,
  'ширина подложки не по ключам плана: '+m.style.width+' ждали '+cqw(full*frac,w));
const left=(box.left-hb.left)*k+(full-full*frac)/2;
assert(Math.abs(parseFloat(m.style.left)-left/w*100)<0.01,
  'левый край подложки не от текущего слова: '+m.style.left+' ждали '+cqw(left,w));
assert.strictEqual(m.style.height,cqw(plan.sub_wbg.h,w),'высота подложки не из плана');
assert.strictEqual(m.style.borderRadius,cqw(plan.sub_wbg.round,w),'скругление не из плана');
assert.strictEqual(m.style.background,rgb2hex(plan.sub_wbg.fill),'цвет подложки не из плана');
// В конце раскрытия фигура целая: доля 100%% — ширина слова с полем.
paint(plan,t0+sweep+0.005);
m=mk();
assert(Math.abs(parseFloat(m.style.width)-full/w*100)<0.02,
  'к концу раскрытия фигура не на всю ширину: '+m.style.width);
console.log('OK: preview backs the current word');
""" % {"cur_w": json.dumps(cur["w"], ensure_ascii=False),
       "prev_w": json.dumps(prev["w"], ensure_ascii=False),
       "t0": json.dumps(cur["wbg"]["t"]), "tp": json.dumps(prev["wbg"]["t"]),
       "sweep": json.dumps(SWEEP),
       "tm": json.dumps(round(cur["wbg"]["t"] + SWEEP * 0.5, 6))}
    _expect_ok(_run_preview(tmp_path, "wbg_cur.js", plan, checks), "превью подложки")


@node
def test_preview_wbg_vanishes_between_rows(xml_subs, tmp_path):
    """Пока слово не звучало (или уже ушло), подложки в кадре нет."""
    plan = _plan(xml_subs, WBG, highlights=[])
    items = sorted(plan["subs"], key=lambda it: it["s"])
    # Зазор между репликами — по самому плану: момент, когда НИ ОДНО слово не видно.
    gap = None
    for a, b in zip(items, items[1:]):
        if b["s"] - a["gend"] > 0.05:
            gap = round(a["gend"] + (b["s"] - a["gend"]) / 2.0, 4)
            break
    if gap is None:
        pytest.skip("в фикстуре нет зазора между репликами")
    assert not any(it["s"] <= gap < it["gend"] for it in items), "момент выбран не в зазоре"
    checks = r"""
// Зазор между репликами: ни одно слово не видно — фигуре не за чем стоять.
paint(plan,%(gap)s);
assert(host().querySelector('.pvsub_wbg')===null,
  'подложка висит, когда ни одно слово не звучит');
console.log('OK: preview drops the backing in the gap');
""" % {"gap": json.dumps(gap)}
    # Кадр ДО первого слова — только если он есть: ролик может начинаться со слова.
    before = round(items[0]["s"] - 0.05, 4)
    if before >= 0 and not any(it["s"] <= before < it["gend"] for it in items):
        checks += r"""
paint(plan,%(before)s);
assert(host().querySelector('.pvsub_wbg')===null,
  'подложка появилась раньше первого слова');
console.log('OK: preview has no backing before the first word');
""" % {"before": json.dumps(before)}
    _expect_ok(_run_preview(tmp_path, "wbg_gap.js", plan, checks), "превью подложки в зазоре")


@node
def test_preview_pill_mutation_turns_red(xml_subs, tmp_path):
    """Мутация: таблетка встаёт не за ТЕКУЩИМ словом — стенд краснеет.

    Проверка идёт в режиме строк: там в кадре сразу несколько слов строки, а текущее
    (по моменту из плана) среди них одно. Мутация ставит фигуру за ПЕРВОЕ видимое слово
    строки — то есть за уже прозвучавшее, а не за текущее: именно этот случай стенд и
    обязан поймать.
    """
    plan = _plan(xml_subs, dict(WBG, sub_wbg_kind="pill", sub_words_per_row=2),
                 highlights=[])
    row = next(it for it in plan["subs"] if len(it.get("words") or []) > 1)
    words = row["words"]
    first, cur = words[0], words[1]
    assert first["wbg"]["t"] < cur["wbg"]["t"], "слова строки идут не по времени"
    assert len(first["w"].strip()) != len(cur["w"].strip()), \
        "слова одной длины — по геометрии мутацию не отличить"
    tm = round(cur["wbg"]["t"] + 0.005, 6)
    checks = r"""
const cur=%(cur_w)s, first=%(first_w)s, tm=%(tm)s;
paint(plan,tm);
const m=host().querySelector('.pvsub_wbg');
assert(m,'подложки нет, хотя слово звучит');
assert.strictEqual(m.dataset.w,''.concat(cur),'подложка не за текущим словом: '+m.dataset.w);
const all=words();
const sp=all.filter(s=>s.textContent===cur)[0], fp=all.filter(s=>s.textContent===first)[0];
assert(sp&&fp,'в разметке не нашлись слова строки: '+all.map(s=>s.textContent).join(','));
const el=$('ipvsub'), w=plan.w, k=w/(el.clientWidth||w);
const full=sp.getBoundingClientRect().width*k+2*plan.sub_wbg.pad;
assert(Math.abs(parseFloat(m.style.width)-full/w*100)<0.01,
  'ширина таблетки не по текущему слову: '+m.style.width);
const hb=host().getBoundingClientRect();
const fw=fp.getBoundingClientRect().width*k+2*plan.sub_wbg.pad;
assert(Math.abs(parseFloat(m.style.width)-fw/w*100)>0.01,
  'мутацию не отличить: слова одной ширины');
assert(Math.abs(parseFloat(m.style.left)-((fp.getBoundingClientRect().left-hb.left)*k
  +(fw-fw)/2)/w*100)>0.5,'подложка стоит на первом слове строки');
console.log('OK: pill backs the current word');
""" % {"cur_w": json.dumps(cur["w"], ensure_ascii=False),
       "first_w": json.dumps(first["w"], ensure_ascii=False), "tm": json.dumps(tm)}
    ok = _run_preview(tmp_path, "wbg_mut_ok.js", plan, checks)
    _expect_ok(ok, "превью таблетки без мутации")
    # Мутация: фигура ставится за ПЕРВЫМ видимым словом (уже прозвучавшим) — не за текущим.
    mut = ("const _realWbg=ipvSubWbg;\n"
           "ipvSubWbg=function(tm){_realWbg(tm);\n"
           "  const host=$('ipvsub').querySelector('.pvsubs_host');\n"
           "  const m=host.querySelector('.pvsub_wbg'); if(!m)return;\n"
           "  const ws=host.querySelectorAll('.pvsubw_wd').filter(s=>s.dataset.wbg!==undefined);\n"
           "  if(!ws.length)return;\n"
           "  let first=null,ft=Infinity;\n"
           "  ws.forEach(s=>{const q=parseFloat(s.dataset.wbg); if(q<ft){ft=q;first=s;}});\n"
           "  const el=$('ipvsub'), w=IPV.plan.w, k=w/(el.clientWidth||w);\n"
           "  const b=first.getBoundingClientRect(), hb=host.getBoundingClientRect();\n"
           "  const fw=b.width*k+2*IPV.plan.sub_wbg.pad;\n"
           "  m.dataset.w=first.textContent;\n"
           "  m.style.left=(((b.left-hb.left)*k+(b.width*k)/2-fw/2)/w*100).toFixed(3)+'cqw';\n"
           "  m.style.width=(fw/w*100).toFixed(3)+'cqw';};\n")
    bad = _run_preview(tmp_path, "wbg_mut_bad.js", plan, checks, mutate=mut)
    assert bad.returncode != 0, "мутация НЕ покраснела — стенд стережёт пустоту"
    err = (bad.stderr or "") + (bad.stdout or "")
    assert "подложка не за текущим словом" in err, \
        "мутация покраснела не на том: %s" % err[-500:]


@node
def test_preview_gradient_puts_shadow_below_the_word(xml_subs, tmp_path):
    """Градиент: буквы остаются градиентом, а тень уходит ОТДЕЛЬНЫМ слоем под слово.

    Тень, нарисованная `text-shadow` на самих буквах градиента, ложится ПОД прозрачные
    буквы и просвечивает сквозь них чёрным — слово выходило тёмным, хотя числа тени те же,
    что у белого. В AE тень лежит на слое ПОД слоем с Ramp, поэтому у градиентного слова
    своего `text-shadow` нет вовсе: тень и свечение играет обёртка `.pvsubw_fx` функциями
    `drop-shadow` — она берёт УЖЕ нарисованные буквы и кладёт тень под них.
    """
    st = {"sub_fill_mode": "gradient", "sub_grad_from": [0.2, 0.4, 0.6],
          "sub_grad_to": [0.8, 0.6, 0.4], "sub_grad_angle": 45}
    plan = _plan(xml_subs, st)
    words = _white_words(plan)
    assert words, "в фикстуре нет белых слов"
    w = words[0]
    checks = r"""
const wtext=%(w)s, tm=%(tm)s, g=plan.sub_grad;
// Слой эффектов слова: в разметке это обёртка <span class="pvsubw_fx" style="…"> вокруг
// спана слова. Читается разметка ЦЕЛИКОМ (host().innerHTML) — по ней видно и слово, и слой.
function fxStyle(text){
  const html=host().innerHTML;
  const i=html.indexOf('>'+text+'</span>');
  assert(i>=0,'в разметке нет слова '+text);
  const a=html.lastIndexOf('<span class="pvsubw_fx"',i);
  const b=html.lastIndexOf('<span class="pvsubw_wd',i);
  if(a<0||b<0)return '';                     // обёрток в разметке нет вовсе
  const m=/^<span class="pvsubw_fx" style="([^"]*)">/.exec(html.slice(a));
  // Обёртка принадлежит ЭТОМУ слову, только если спан слова идёт сразу за её открытием.
  return (m&&a+m[0].length===b)?m[1]:'';
}
paint(plan,tm);
const sp=words().filter(s=>s.textContent===wtext)[0];
assert(sp,'не нашёлся спан слова');
// Заливка по буквам — прежняя: цвета и угол из плана.
const want='background-image:linear-gradient('+g.angle+'deg,'+rgb2hex(g.from)+','+rgb2hex(g.to)+');'
  +'-webkit-background-clip:text;background-clip:text;color:transparent;';
assert(sp.styleStr.indexOf(want)>=0,'градиент не по плану: '+sp.styleStr);
// На буквах тени нет: у градиента она просвечивала бы сквозь прозрачные буквы.
assert(sp.styleStr.indexOf('text-shadow:none')>=0,'у слова не снят text-shadow: '+sp.styleStr);
// Тень — на слое ПОД словом, функциями drop-shadow: числа те же, что у --subsh.
const fx=fxStyle(wtext);
assert(fx,'у градиентного слова нет слоя эффектов под ним: '+fx);
assert(fx.indexOf('filter:')===0,'слой эффектов без filter: '+fx);
assert(fx.indexOf('drop-shadow(0 2px 7px #000)')>=0,'на слое нет тени слова: '+fx);
assert(fx.indexOf('drop-shadow(0 0 3px #000)')>=0,'на слое нет второй тени слова: '+fx);
console.log('OK: gradient keeps the letters, the shadow lies below');
""" % {"w": json.dumps(w["w"], ensure_ascii=False), "tm": json.dumps(round(w["s"] + 0.01, 6))}
    _expect_ok(_run_preview(tmp_path, "grad_shadow.js", plan, checks), "тень градиентного слова")
    # Откат: тень снова на буквах слова — стенд обязан покраснеть.
    bad = _run_preview(tmp_path, "grad_shadow_mut.js", plan, checks,
                       src=(("-webkit-text-fill-color:transparent;text-shadow:none;",
                             "-webkit-text-fill-color:transparent;"),))
    err = _expect_red(bad, "возврат text-shadow на буквы градиента")
    assert "text-shadow" in err, "мутация покраснела не на том: %s" % err[-500:]


@node
def test_preview_gradient_glow_plays_on_the_layer_below(xml_subs, tmp_path):
    """Градиент + свечение: светит слой ПОД словом, цветом и радиусом плана.

    Свечение — Glo2 на слое слова в .jsx; у градиента оно, как и тень, играет на слое под
    словом, а числа те же, что у слов интро: радиус и сила множат две размытые копии букв
    (12 и 24 px при радиусе 77 и силе 0.62 — при дефолтах интро множитель 1). Галка
    «только жёлтые» — ветка GLOW_YEL в .jsx: белое слово свечения не получает. Галка
    снята — светится и белое: числа берутся из ПЛАНА, а не из памяти прошлого кадра.
    """
    st = {"sub_fill_mode": "gradient", "sub_grad_from": [0.2, 0.4, 0.6],
          "sub_grad_to": [0.8, 0.6, 0.4], "sub_grad_angle": 45,
          "sub_glow_on": True, "sub_glow_yellow": True, "sub_glow_amt": 1.0,
          "sub_glow_rad": 40.0, "sub_glow_fill": [0.0, 1.0, 0.5]}
    plan = _plan(xml_subs, st)
    words = _white_words(plan)
    assert words, "в фикстуре нет белых слов"
    w = words[0]
    yellow = next((it for it in plan["subs"] if it.get("color") == "yellow"), None)
    assert yellow is not None, "в фикстуре нет жёлтого слова"
    checks = r"""
const wtext=%(w)s, tm=%(tm)s, ytext=%(y)s, ytm=%(ytm)s, gl=plan.sub_glow;
function fxStyle(text){
  const html=host().innerHTML;
  const i=html.indexOf('>'+text+'</span>');
  assert(i>=0,'в разметке нет слова '+text);
  const a=html.lastIndexOf('<span class="pvsubw_fx"',i);
  const b=html.lastIndexOf('<span class="pvsubw_wd',i);
  if(a<0||b<0)return '';                     // обёрток в разметке нет вовсе
  const m=/^<span class="pvsubw_fx" style="([^"]*)">/.exec(html.slice(a));
  // Обёртка принадлежит ЭТОМУ слову, только если спан слова идёт сразу за её открытием.
  return (m&&a+m[0].length===b)?m[1]:'';
}
const col=rgb2hex(gl.fill);
const gk=(gl.rad/77)*(gl.amt/0.62);
const halo=[Math.round(12*gk*10)/10,Math.round(24*gk*10)/10]
  .map(b=>'drop-shadow(0 0 '+b+'px '+col+')');
// Белое слово: тень слова на слое есть, свечения при галке «только жёлтые» — нет.
paint(plan,tm);
let fx=fxStyle(wtext);
assert(fx&&fx.indexOf('drop-shadow(0 2px 7px #000)')>=0,'у белого слова нет тени: '+fx);
assert(fx.indexOf(col)<0,'свечение уехало на белое слово: '+fx);
// Жёлтое слово: свечение теми же числами плана, и тоже на слое под словом.
paint(plan,ytm);
let ys=words().filter(s=>s.textContent===ytext)[0];
assert(ys,'не нашёлся спан жёлтого слова');
assert(ys.classList.contains('yel'),'жёлтое слово потеряло свой класс');
assert(ys.styleStr.indexOf('text-shadow:none')>=0,'у жёлтого слова тень на буквах: '+ys.styleStr);
fx=fxStyle(ytext);
assert(fx.indexOf(halo[0])>=0,'свечение жёлтого не по радиусу плана: '+fx);
assert(fx.indexOf(halo[1])>=0,'второй ореол свечения не по плану: '+fx);
// Галка снята в плане — светится и белое слово.
const all=JSON.parse(JSON.stringify(plan)); all.sub_glow.yellow=false;
paint(all,tm);
fx=fxStyle(wtext);
assert(fx.indexOf(halo[0])>=0,'со снятой галкой белое слово не светится: '+fx);
console.log('OK: preview glows on the layer below by plan');
""" % {"w": json.dumps(w["w"], ensure_ascii=False),
       "tm": json.dumps(round(w["s"] + 0.01, 6)),
       "y": json.dumps(yellow["w"], ensure_ascii=False),
       "ytm": json.dumps(round(yellow["s"] + 0.01, 6))}
    _expect_ok(_run_preview(tmp_path, "grad_glow.js", plan, checks), "свечение градиента")
    # Откат: свечение уезжает в filter голым списком теней — CSS такого не примет.
    bad = _run_preview(tmp_path, "grad_glow_mut.js", plan, checks,
                       src=((".concat(glowTsh(pl.sub_glow,isY).map(s=>'drop-shadow('+s+')'))",
                             ".concat(glowTsh(pl.sub_glow,isY))"),))
    err = _expect_red(bad, "возврат свечения без drop-shadow")
    assert "свечени" in err, "мутация покраснела не на том: %s" % err[-500:]


@node
def test_preview_glow_on_the_word_for_solid_fill(xml_subs, tmp_path):
    """Сплошная заливка: свечение играет на самих буквах — объявлением `text-shadow`.

    Без имени свойства (`text-shadow:`) список теней браузер выбрасывает целиком, и
    свечения не было вовсе, хотя числа лежали в разметке. Стенд сверяет ОБЪЯВЛЕНИЕ
    целиком: тень слова плюс две размытые копии букв ЦВЕТОМ ПЛАНА — и что при галке
    «только жёлтые» белое слово свечения не получает.
    """
    st = {"sub_glow_on": True, "sub_glow_yellow": True, "sub_glow_amt": 1.0,
          "sub_glow_rad": 40.0, "sub_glow_fill": [0.0, 1.0, 0.5]}
    plan = _plan(xml_subs, st)
    words = _white_words(plan)
    assert words, "в фикстуре нет белых слов"
    w = words[0]
    yellow = next((it for it in plan["subs"] if it.get("color") == "yellow"), None)
    assert yellow is not None, "в фикстуре нет жёлтого слова"
    checks = r"""
const wtext=%(w)s, tm=%(tm)s, ytext=%(y)s, ytm=%(ytm)s, gl=plan.sub_glow;
const col=rgb2hex(gl.fill);
const gk=(gl.rad/77)*(gl.amt/0.62);
const b1=Math.round(12*gk*10)/10, b2=Math.round(24*gk*10)/10;
const glow='0 0 '+b1+'px '+col+',0 0 '+b2+'px '+col;
// Жёлтое слово: тень слова и свечение цветом плана — ОДНИМ объявлением text-shadow.
paint(plan,ytm);
let ys=words().filter(s=>s.textContent===ytext)[0];
assert(ys,'не нашёлся спан жёлтого слова');
assert(ys.classList.contains('yel'),'жёлтое слово потеряло свой класс');
assert(ys.styleStr.indexOf('text-shadow:0 2px 7px #000,0 0 3px #000,'+glow+';')>=0,
  'свечение жёлтого не по плану: '+ys.styleStr);
// Белое слово: свечения при галке «только жёлтые» нет, и тень его не подменена —
// объявления text-shadow у слова вовсе нет (тень приходит унаследованной из --subsh).
paint(plan,tm);
let ws=words().filter(s=>s.textContent===wtext)[0];
assert(ws,'не нашёлся спан белого слова');
assert(ws.styleStr.indexOf('text-shadow')<0,'белому слову подменили тень: '+ws.styleStr);
assert(ws.styleStr.indexOf(col)<0,'свечение уехало на белое слово: '+ws.styleStr);
console.log('OK: preview glows on the letters by plan');
""" % {"w": json.dumps(w["w"], ensure_ascii=False),
       "tm": json.dumps(round(w["s"] + 0.01, 6)),
       "y": json.dumps(yellow["w"], ensure_ascii=False),
       "ytm": json.dumps(round(yellow["s"] + 0.01, 6))}
    _expect_ok(_run_preview(tmp_path, "solid_glow.js", plan, checks), "свечение сплошного слова")
    # Откат: список теней уезжает БЕЗ имени свойства — объявление невалидно.
    bad = _run_preview(tmp_path, "solid_glow_mut.js", plan, checks,
                       src=(("return 'text-shadow:'+(pl.sub_shadow===false?[]:SH_TSH).concat(gl).join(',')+';';",
                             "return (pl.sub_shadow===false?[]:SH_TSH).concat(gl).join(',');"),))
    _expect_red(bad, "возврат свечения без имени свойства")


@node
def test_preview_glitch_flickers_by_plan_keys(xml_subs, tmp_path):
    """Глитч в превью: мерцание по ЛИНЕЙНЫМ ключам плана и буквы, проступающие по одной."""
    plan = _plan(xml_subs, {"sub_anim": "glitch"}, highlights=[])
    words = _white_words(plan)
    assert words, "в фикстуре нет белых слов"
    w = words[0]
    a = w["anim"]
    t0 = a["op"][0][0]
    tm = a["op"][2][0]          # ключ 100 %: слово обязано быть видно целиком
    dark = a["op"][3][0]        # ключ 0 %: вспышка гаснет
    checks = r"""
const wtext=%(w)s, t0=%(t0)s, tm=%(tm)s, dark=%(dark)s;
paint(plan,tm);
let sp=words().filter(s=>s.textContent===wtext)[0];
assert(sp,'не нашёлся спан слова');
const a=JSON.parse(decodeURIComponent(sp.dataset.wa));
assert.strictEqual(a.name,'glitch','превью взяло не плановые ключи: '+sp.dataset.wa);
const op=parseFloat(sp.style.opacity);
assert(Math.abs(op-ipvGlitchOp(tm,a.op))<1e-3,
  'мерцание не по ключам плана: '+op+' vs '+ipvGlitchOp(tm,a.op));
assert(op>0.99,'на ключе 100%% слово не видно: '+op);
// Буквы проступают по одной: слово разложено на спаны букв (ipvRenderChars).
assert(sp.children.length===wtext.length,'буквы не разложены: '+sp.children.length);
// Вспышка гаснет по ключам: на ключе 0 %% слово прозрачно целиком.
paint(plan,dark);
sp=words().filter(s=>s.textContent===wtext)[0];
assert(parseFloat(sp.style.opacity)<0.01,'вспышка не погасла: '+sp.style.opacity);
// После глитча следов нет: прозрачность снята, буквы снова одним текстом.
paint(plan,a.op[a.op.length-1][0]+0.05);
sp=words().filter(s=>s.textContent===wtext)[0];
assert.strictEqual(sp.style.opacity,'','после глитча осталась прозрачность');
assert.strictEqual(sp.children.length,0,'после глитча остались спаны букв');
console.log('OK: preview glitches by plan keys');
""" % {"w": json.dumps(w["w"], ensure_ascii=False), "t0": json.dumps(t0),
       "tm": json.dumps(tm), "dark": json.dumps(dark)}
    _expect_ok(_run_preview(tmp_path, "glitch.js", plan, checks), "превью глитча")


# --------------------------------------------------------------------------- #
# 6. Умолчания и разметка полей
# --------------------------------------------------------------------------- #
def test_new_fields_are_marked_in_geometry():
    """Новые размеры размечены видом, проценты и секунды — «от кадра не зависит»."""
    for key in ("sub_wbg_h", "sub_wbg_round", "sub_wbg_pad"):
        assert style_geometry.kind_of(key) == style_geometry.SIZE, \
            "%s не размечен размером: в другом кадре фигура уехала бы" % key
    assert style_geometry.kind_of("sub_wbg_dy") == style_geometry.Y
    assert style_geometry.kind_of("sub_glow_rad") == style_geometry.SIZE
    for key in ("sub_wbg_op", "sub_wbg_sweep", "sub_grad_angle", "sub_glow_amt"):
        assert key in style_geometry.FRAME_INDEPENDENT, "%s не размечен" % key
        assert style_geometry.kind_of(key) is None


def test_defaults_keep_the_golden(xml_subs, tmp_path):
    """Дефолты новых ручек — «как сейчас»: ни подстановок, ни объявлений, эталон тот же."""
    assert styles.BASE["sub_wbg_on"] is False and styles.BASE["sub_glow_on"] is False
    assert styles.BASE["sub_wbg_kind"] == "highlight"
    assert styles.BASE["sub_fill_mode"] == "solid"
    off = _jsx(xml_subs, tmp_path, "defaults.jsx", None, highlights=[])
    for token in ("wbgKeys", "WBG_W", "Подложка слова", "subGrad", "GRAD_FROM",
                  "subGlow", "GLOW_RAD", "introAnimFX"):
        assert token not in off, "умолчание оставило в .jsx подстановку %s" % token
    assert _grad_calls(off) == 0 and " subGlow(L, hl);" not in off
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path)) == golden, \
        "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"


def test_style_panel_offers_the_new_groups():
    """Панель: три новые группы, ручки в них и подсказки — «!»-тултипами."""
    from core import style_schema
    fields, groups = {}, {}

    def walk(items):
        for x in items:
            if x.get("key"):
                fields[x["key"]] = x
            if x.get("id"):
                groups[x["id"]] = x
            walk(x.get("items") or [])

    walk(style_schema.LAYERS)
    assert "subs.wbg" in groups and groups["subs.wbg"]["toggle"] == "sub_wbg_on"
    assert "subs.fill" in groups and "subs.glow" in groups
    assert groups["subs.glow"]["toggle"] == "sub_glow_on"
    assert [o[0] for o in fields["sub_wbg_kind"]["options"]] == ["highlight", "pill"]
    assert [o[0] for o in fields["sub_fill_mode"]["options"]] == ["solid", "gradient"]
    assert fields["sub_wbg_sweep"]["show_if"] == {"key": "sub_wbg_kind", "eq": "highlight"}
    assert fields["sub_grad_angle"]["show_if"] == {"key": "sub_fill_mode", "eq": "gradient"}
    for key in ("sub_wbg_kind", "sub_wbg_fill", "sub_wbg_op", "sub_wbg_h", "sub_wbg_round",
                "sub_wbg_pad", "sub_wbg_dy", "sub_wbg_sweep", "sub_fill_mode",
                "sub_grad_from", "sub_grad_to", "sub_grad_angle", "sub_glow_yellow",
                "sub_glow_amt", "sub_glow_rad", "sub_glow_fill"):
        assert fields[key]["tip"], "у ручки %s нет подсказки" % key
