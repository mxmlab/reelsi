# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тень и свечение интро — отдельные рычаги стиля.

Владелец переделал вид интро руками в After Effects: у 130 из 136 слов интро снял
галки Drop Shadow, у 109 из 117 — Glo2. Повторить этот вид пересборкой было нельзя:
решение «ставить эффект или нет» стояло в шаблоне жёстко — строки с anim=="glitch" и
строки заднего плана получали тень всегда, глитч и строка с fx=="glow" — всегда Glo2,
а числа Glo2 (149/77/0.62) и тени прекомпа (135/0/287) были литералами.

Что проверяем:

  * галка intro_glitch_shadow: у глитч-слова Drop Shadow НЕТ, у строки заднего плана —
    есть; выключены обе галки — функции introWordShadow в .jsx нет вовсе;
  * галка intro_back_shadow: у строки заднего плана тени нет, у глитч-слова — есть;
    слово «и глитч, и задний план» теряет тень, только если сняты ОБЕ галки;
  * галка intro_glitch_glow: у глитч-слова нет Glo2, но Gaussian Blur анимации на месте,
    а слово строки fx=="glow" свечение сохраняет (галки независимы);
  * галка intro_fx_glow: у слова строки fx=="glow" нет Glo2 (и ветки `else if(fx=="glow")`
    в шаблоне не остаётся), у глитч-слова свечение есть;
  * свои intro_word_glow_thr/rad/int доезжают в .jsx вместо 149/77/0.62 — в ОБЕ ветки;
  * свои intro_comp_shadow_dir/dist/soft доезжают вместо 135/0/287 в introCompShadow;
  * план сцены несёт новые ключи для превью (plan.intro_word_fx/plan.intro_comp_shadow),
    и фронт (static/app/85-inserts-view.js) читает ИХ, а не ключи стиля;
  * дефолты ничего не меняют: сборка с новыми ключами, выставленными явно, побайтово
    равна сборке без них, а сборка со стилем по умолчанию — эталону golden_geometry.jsx.

Эффекты на слоях слов проверяются по-настоящему: код интро из готового .jsx исполняется
в node с заглушками слоя (проба взята из tests/test_intro_hl_glow.py — своя копия
проигрывателя не заводится).
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

from core import styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _build as _golden_build  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402
from tests.test_intro_hl_glow import _probe_intro  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

T_CAM1, T_CAM2 = 1.0, 8.3
GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")

# Новые ключи стиля — перечень для сторожа «каждая ручка».
NEW_KEYS = (
    "intro_glitch_shadow", "intro_back_shadow",
    "intro_glitch_glow", "intro_fx_glow",
    "intro_word_glow_thr", "intro_word_glow_rad", "intro_word_glow_int",
    "intro_comp_shadow_dir", "intro_comp_shadow_dist", "intro_comp_shadow_soft",
)

# Разметка интро: в одной группе собраны все четыре «спец-строки» — глитч, строка со
# свечением, строка заднего плана и строка, которая И глитч, И задний план (её тень по
# заданию обязана зависеть от ОБЕИХ галочек), плюс обычная строка на перебивке.
# Цвета НЕ жёлтые: на жёлтой строке глитча своего Glo2 добавляет ещё и свечение жёлтого
# хайлайта (introHlGlow) — в пробе эффектов оно мешало бы считать свечение рычага.
INTRO = [
    dict(words=["ГЛИТЧ"], color="white", anim="glitch", times=[T_CAM1]),
    dict(words=["СВЕЧЕНИЕ"], color="white", fx="glow", times=[T_CAM1 + 0.3]),
    dict(words=["ФОН"], color="white", back=True, times=[T_CAM1 + 0.6]),
    dict(words=["ГЛИТЧФОН"], color="white", back=True, anim="glitch", times=[T_CAM1 + 0.9]),
    dict(words=["ОБЫЧНОЕ"], color="white", times=[T_CAM2]),
]
SPLITS = [4]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, style=None, intro=None, name="fx.jsx", splits=None):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=INTRO if intro is None else intro,
                                   intro_splits=SPLITS if splits is None else splits,
                                   style=dict(style or {}), disclaimer="",
                                   emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style=None):
    return xml2ae.scene_plan(xml, intro=INTRO, intro_splits=SPLITS,
                             style=dict(style or {}), disclaimer="", emit=lambda *a: None)


def _effects(data, text):
    """Список matchName эффектов слоя слова (в порядке добавления).

    Сравнение без регистра: строки заднего плана приезжают в слои строчными
    (back_case по умолчанию lower).
    """
    lay = next(w for w in data["layers"] if w["text"].lower() == text.lower())
    return [e["matchName"] for e in lay["effects"]]


def _props(data, text, match_name):
    lay = next(w for w in data["layers"] if w["text"].lower() == text.lower())
    fx = next(e for e in lay["effects"] if e["matchName"] == match_name)
    return fx["props"]


def _comp_shadow_fn(jsx):
    """Тело функции introCompShadow из .jsx (числа тени прекомпа живут только там)."""
    i = jsx.index("function introCompShadow")
    return jsx[i:jsx.index("}", i) + 1]


# ---- 1. Галки тени ---------------------------------------------------------------------

@node
def test_глитч_тень_выключена_у_заднего_плана_осталась(xml_subs, tmp_path):
    """intro_glitch_shadow=False: у глитч-слова Drop Shadow нет, у back-строки есть."""
    jsx = _build(xml_subs, tmp_path, style={"intro_glitch_shadow": False}, name="sh_glitch.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "ADBE Drop Shadow" not in _effects(data, "ГЛИТЧ")
    assert "ADBE Drop Shadow" in _effects(data, "ФОН")
    # строка «и глитч, и задний план»: тень разрешена галкой заднего плана
    assert "ADBE Drop Shadow" in _effects(data, "ГЛИТЧФОН")
    # условие в .jsx сузилось до заднего плана — ветки глитча в нём нет
    assert ' if(ln.back) introWordShadow(Ll, ln.back);' in jsx
    assert ' if(ln.back) introWordShadow(L2, ln.back);' in jsx
    assert 'ln.anim=="glitch"' not in jsx


@node
def test_back_тень_выключена_у_глитч_слова_осталась(xml_subs, tmp_path):
    """intro_back_shadow=False: у строк заднего плана тени нет, у глитч-слова есть.

    Слово «и глитч, и задний план» тень сохраняет: разрешена галкой ГЛИТЧА — ровно
    правило «получает тень, если разрешена хоть одна из двух».
    """
    jsx = _build(xml_subs, tmp_path, style={"intro_back_shadow": False}, name="sh_back.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "ADBE Drop Shadow" in _effects(data, "ГЛИТЧ")
    assert "ADBE Drop Shadow" not in _effects(data, "ФОН")
    assert "ADBE Drop Shadow" in _effects(data, "ГЛИТЧФОН")
    assert ' if(ln.anim=="glitch") introWordShadow(Ll, ln.back);' in jsx
    assert 'if(ln.anim=="glitch"||ln.back)' not in jsx


@node
def test_обе_галки_тени_сняты_теней_нет(xml_subs, tmp_path):
    """Обе галки сняты — тени нет ни у кого: ни функции, ни констант, ни эффектов.

    Слово «и глитч, и задний план» теряет тень только здесь — когда сняты ОБЕ галки.
    """
    jsx = _build(xml_subs, tmp_path,
                 style={"intro_glitch_shadow": False, "intro_back_shadow": False},
                 name="sh_off.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "introWordShadow" not in jsx
    assert "INTRO_SHADOW_OP" not in jsx
    assert "BACK_SHADOW_OP" not in jsx
    for text in ("ГЛИТЧ", "ФОН", "ГЛИТЧФОН", "ОБЫЧНОЕ"):
        assert "ADBE Drop Shadow" not in _effects(data, text), text


@node
def test_обе_галки_тени_включены_дефолтное_условие(xml_subs, tmp_path):
    """Обе галки на дефолте — условие ровно прежнее: `ln.anim=="glitch"||ln.back`."""
    jsx = _build(xml_subs, tmp_path, style={"intro_glitch_shadow": True,
                                            "intro_back_shadow": True}, name="sh_on.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert 'if(ln.anim=="glitch"||ln.back) introWordShadow(' in jsx
    for text in ("ГЛИТЧ", "ФОН", "ГЛИТЧФОН"):
        assert "ADBE Drop Shadow" in _effects(data, text), text


# ---- 2. Галки свечения -----------------------------------------------------------------

@node
def test_глитч_свечение_выключено_blur_на_месте(xml_subs, tmp_path):
    """intro_glitch_glow=False: у глитч-слова нет Glo2, но Gaussian Blur остался.

    Слово строки fx=="glow" свечение сохраняет: галки независимы.
    """
    jsx = _build(xml_subs, tmp_path, style={"intro_glitch_glow": False}, name="gl_glitch.jsx")
    data = _probe_intro(jsx, tmp_path)

    glitch = _effects(data, "ГЛИТЧ")
    assert "ADBE Glo2" not in glitch
    assert "ADBE Gaussian Blur 2" in glitch, "размытие анимации глитча пропало вместе со свечением"
    assert "ADBE Glo2" in _effects(data, "СВЕЧЕНИЕ")
    assert '} else if(fx=="glow"){' in jsx


@node
def test_fx_glow_выключено_глитч_свечение_осталось(xml_subs, tmp_path):
    """intro_fx_glow=False: у слова строки fx=="glow" нет Glo2, у глитч-слова есть."""
    jsx = _build(xml_subs, tmp_path, style={"intro_fx_glow": False}, name="gl_fx.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "ADBE Glo2" not in _effects(data, "СВЕЧЕНИЕ")
    assert "ADBE Glo2" in _effects(data, "ГЛИТЧ")
    # ветки свечения строки в шаблоне не осталось: ей нечего ставить
    assert '} else if(fx=="glow"){' not in jsx
    assert 'if(anim=="glitch"){' in jsx


def test_обе_галки_свечения_сняты_glo2_на_словах_нет(xml_subs, tmp_path):
    """Обе галки свечения сняты: строк Glo2 на словах в .jsx не остаётся."""
    jsx = _build(xml_subs, tmp_path,
                 style={"intro_glitch_glow": False, "intro_fx_glow": False},
                 name="gl_off.jsx")
    assert 'addFX(L,"ADBE Glo2")' not in jsx
    # прекомпный Glo2 (211/93/0.42 и мягкий 42/INTRO_GLOW) — отдельный механизм, он цел
    assert '"ADBE Glo2-0002",211' in jsx


# ---- 3. Числа Glo2 на словах -----------------------------------------------------------

@node
def test_свои_числа_glo2_доезжают_в_обе_ветки(xml_subs, tmp_path):
    """intro_word_glow_thr/rad/int — свои числа вместо 149/77/0.62 в обеих ветках."""
    style = {"intro_word_glow_thr": 200, "intro_word_glow_rad": 123,
             "intro_word_glow_int": 1.25}
    jsx = _build(xml_subs, tmp_path, style=style, name="gl_num.jsx")
    data = _probe_intro(jsx, tmp_path)

    want = {"ADBE Glo2-0002": 200, "ADBE Glo2-0003": 123, "ADBE Glo2-0004": 1.25}
    assert _props(data, "ГЛИТЧ", "ADBE Glo2") == want
    assert _props(data, "СВЕЧЕНИЕ", "ADBE Glo2") == want
    # жёстких литералов в .jsx больше нет — ручка не «проигнорирована»
    for lit in ('"ADBE Glo2-0002",149', '"ADBE Glo2-0003",77', '"ADBE Glo2-0004",0.62'):
        assert lit not in jsx, "остался жёсткий литерал " + lit


def test_дефолтные_числа_glo2_прежние(xml_subs, tmp_path):
    """Дефолты 149/77/0.62 печатаются ровно прежними литералами (эталон не поехал)."""
    jsx = _build(xml_subs, tmp_path, style={}, name="gl_def.jsx")
    assert 'var fxGl=addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149);' \
           ' setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);' in jsx


# ---- 4. Тень прекомпа: числа -----------------------------------------------------------

def test_свои_числа_тени_прекомпа_доезжают(xml_subs, tmp_path):
    """intro_comp_shadow_dir/dist/soft — свои числа вместо 135/0/287."""
    jsx = _build(xml_subs, tmp_path,
                 style={"intro_comp_shadow_dir": 45.0, "intro_comp_shadow_dist": 12.0,
                        "intro_comp_shadow_soft": 88.0}, name="cs_num.jsx")

    assert "introCompShadow(iL, INTRO_ON2[gI]);" in jsx
    fn = _comp_shadow_fn(jsx)
    assert 'setP(ds,"ADBE Drop Shadow-0003",45);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0004",12);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0005",88);' in fn
    # жёстких 135/0/287 в самой функции больше нет
    assert 'setP(ds,"ADBE Drop Shadow-0003",135);' not in fn
    assert 'setP(ds,"ADBE Drop Shadow-0004",0);' not in fn
    assert 'setP(ds,"ADBE Drop Shadow-0005",287);' not in fn
    # цвет/прозрачность не трогали — подстановка ушла от прежней только из-за новых чисел
    assert "dropShadow(iL, 68);" not in jsx


def test_цвет_тени_прекомпа_и_новые_числа_вместе(xml_subs, tmp_path):
    """Новые числа работают и вместе со своим цветом/прозрачностью тени прекомпа."""
    jsx = _build(xml_subs, tmp_path,
                 style={"intro_comp_shadow_fill": [0, 0, 0], "intro_comp_shadow_op": 240.0,
                        "intro_comp_shadow_dir": 20.0, "intro_comp_shadow_dist": 8.0,
                        "intro_comp_shadow_soft": 40.0}, name="cs_mix.jsx")
    fn = _comp_shadow_fn(jsx)
    assert 'setP(ds,"ADBE Drop Shadow-0001", on2?[1,1,1]:[0,0,0]);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0002", on2?68:240);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0003",20);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0004",8);' in fn
    assert 'setP(ds,"ADBE Drop Shadow-0005",40);' in fn


def test_дефолтные_числа_тени_прекомпа_прежняя_подстановка(xml_subs, tmp_path):
    """Все семь ключей тени прекомпа на дефолте — ровно прежняя dropShadow(iL, 68)."""
    jsx = _build(xml_subs, tmp_path, style={}, name="cs_def.jsx")
    assert "dropShadow(iL, 68);" in jsx
    assert "introCompShadow" not in jsx


# ---- 5. План: ключи обязаны доезжать до превью -----------------------------------------

def test_план_несёт_новые_ключи_для_превью(xml_subs):
    """plan.intro_word_fx и plan.intro_comp_shadow — дверь в превью, как у intro_fill.

    Две галки свечения (жёлтый хайлайт и слой прекомпа, задание «glowfix») приезжают
    в том же объекте плана: превью гасит свечение по плану, второго чтения ключей
    стиля во фронте нет.
    """
    plan = _plan(xml_subs, style={
        "intro_glitch_shadow": False, "intro_back_shadow": True,
        "intro_glitch_glow": True, "intro_fx_glow": False,
        "intro_hl_glow": False, "intro_comp_glow": True,
        "intro_word_glow_thr": 200, "intro_word_glow_rad": 123, "intro_word_glow_int": 1.25,
        "intro_comp_shadow_dir": 45.0, "intro_comp_shadow_dist": 12.0,
        "intro_comp_shadow_soft": 88.0})

    assert plan["intro_word_fx"] == {
        "shadow_all": False, "shadow_glitch": False, "shadow_back": True,
        "glow_glitch": True, "glow_fx": False,
        "glow_hl": False, "glow_comp": True,
        "glow_thr": 200.0, "glow_rad": 123.0, "glow_int": 1.25,
    }
    assert plan["intro_comp_shadow"] == {"dir": 45.0, "dist": 12.0, "soft": 88.0}
    # прежняя дверь тени прекомпа (цвет/прозрачность своей камеры) не сломана
    assert plan["intro"][0]["shadow"] == {"fill": [1, 1, 1], "op": 68}


def test_план_дефолты_для_превью(xml_subs):
    """Дефолтный стиль: план несёт прежние числа — превью выглядит как раньше."""
    plan = _plan(xml_subs, style={})
    assert plan["intro_word_fx"] == {
        "shadow_all": False, "shadow_glitch": True, "shadow_back": True,
        "glow_glitch": True, "glow_fx": True,
        "glow_hl": True, "glow_comp": True,
        "glow_thr": 149.0, "glow_rad": 77.0, "glow_int": 0.62,
    }
    assert plan["intro_comp_shadow"] == {"dir": 135.0, "dist": 0.0, "soft": 287.0}


def test_превью_читает_ключи_из_плана_а_не_из_стиля():
    """Сторож фронта: ipvIntro берёт галки/числа из плана, ключей стиля не знает.

    Второе чтение стиля во фронте — ровно тот баг, от которого уходит эта дверь:
    правка галки обязана доезжать по stEdit() -> ipvPlanSoon() -> /api/scene -> ipvUI().
    """
    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
              encoding="utf-8").read()
    for token in ("pl.intro_word_fx", "pl.intro_comp_shadow",
                  "wfx.glow_glitch", "wfx.glow_fx", "wfx.glow_thr", "wfx.glow_rad",
                  "wfx.glow_int", "wfx.shadow_all", "wfx.shadow_glitch", "wfx.shadow_back",
                  "cs.dir", "cs.dist", "cs.soft"):
        assert token in js, "превью не читает из плана: " + token
    for key in NEW_KEYS:
        assert key not in js, "ключ стиля %s читается во фронте вторым чтением" % key
    # тень слова и свечение ставятся по флагам плана, а не по одному факту anim/fx
    assert "dv.style.textShadow=tsh.join(', ')" in js

    panel = open(os.path.join(ROOT, "static", "app", "94-stylepanel.js"),
                 encoding="utf-8").read()
    body = panel[panel.index("function stEdit()"):]
    body = body[:body.index("\nfunction ")]
    assert "ipvPlanSoon()" in body, "stEdit не пересчитывает план — превью не догонит галку"


# ---- 6. Дефолты ничего не меняют (golden) ----------------------------------------------

def test_явные_дефолты_равны_отсутствию_ключей(xml_subs, tmp_path):
    """Ключей нет и ключи выставлены в дефолт — .jsx байт в байт один и тот же."""
    without = _build(xml_subs, tmp_path, style={}, name="d1.jsx")
    explicit = _build(xml_subs, tmp_path,
                      style={k: styles.BASE[k] for k in NEW_KEYS}, name="d2.jsx")
    assert without == explicit, "явные дефолты разошлись со стилем без ключей"


def test_сборка_со_стилем_по_умолчанию_равна_эталону(xml_subs, tmp_path):
    """Эталон golden_geometry.jsx не менялся: сборка с дефолтами побайтово прежняя."""
    jsx = _mask_assets(_golden_build(xml_subs, tmp_path))
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert jsx == golden, "сборка со стилем по умолчанию разошлась с эталоном"


def test_новые_ручки_в_стороже_каждой_ручки():
    """Сторож tests/test_r11_li_every_knob.py собирает перечень из схемы: новые ручки
    обязаны быть в нём — иначе ручка, которая на сборку не влияет, теряется молча."""
    from tests.test_r11_li_every_knob import TESTED_KEYS
    missing = [k for k in NEW_KEYS if k not in TESTED_KEYS]
    assert not missing, "ручек нет в стороже: %s" % missing
