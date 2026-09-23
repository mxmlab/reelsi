# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свечение интро гасится из ЧЕТЫРЁХ дверей (задание «glowfix»).

Владелец: «выключаю глоу в AE, он всё равно есть — светится выделенное хайлайтом
слово». Свечение интро ставилось из четырёх мест, а галок было только у двух:

  дверь                                 ключ стиля          где в коде
  слово с anim=="glitch" -> Glo2        intro_glitch_glow   plan_intro_tpl._glow_set
  слово строки fx=="glow" -> Glo2       intro_fx_glow       plan_intro_tpl._glow_set
  жёлтое слово -> introHlGlow (Glo2)    intro_hl_glow       plan_intro_tpl._intro_hl_glow_fn
  слой прекомпа группы -> Glo2          intro_comp_glow     plan_intro_tpl._intro_comp_glow

Третья дверь — ровно то, на что жаловались: каждое жёлтое слово интро получало Glo2
с жёсткими 149/77/0.62 мимо всех галок стиля. Четвёртая объясняет «выключаю в AE, а
светится»: Glo2 висит не на слое слова, а на слое ПРЕКОМПА группы — в мастер-композиции.

Что проверяем:

  * intro_hl_glow=False: в .jsx нет ни вызовов introHlGlow, ни самой функции, у жёлтого
    слова нет Glo2 — а глитч-слово со своей галкой свечение сохраняет;
  * тритон внутри introHlGlow к свечению отношения не имеет (красит жёлтую букву на
    тёмном цвете): при снятой галке функция остаётся, но БЕЗ строки Glo2;
  * intro_comp_glow=False: на слое прекомпа Glo2 нет ни в одной из веток — ни мягкий
    42/INTRO_GLOW, ни усиленный 211/93/0.42 у группы с глитчем; на словах он остался;
  * все четыре галки сняты — в коде интро НЕТ НИ ОДНОГО ADBE Glo2 (главная проверка);
  * дефолты (обе галки True) не меняют .jsx ни на байт: явные дефолты равны стилю без
    ключей, а сборка со стилем по умолчанию — эталону golden_geometry.jsx (он не тронут);
  * план сцены несёт glow_hl/glow_comp для превью, и фронт читает ИХ, а не ключи стиля.

Эффекты на слоях слов и прекомпов проверяются по-настоящему: код интро из готового .jsx
исполняется в node с заглушками слоя (проигрыватель берётся из tests/test_intro_hl_glow.py —
своя копия не заводится).
"""
import gzip
import io
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from tests.test_geometry_python import _build as _golden_build  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402
from tests.test_intro_hl_glow import _probe_intro  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

T_CAM1, T_CAM2 = 1.0, 8.3
GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")

# Новые ключи стиля (задание «glowfix»): третья и четвёртая двери свечения.
NEW_KEYS = ("intro_hl_glow", "intro_comp_glow")

# Тёмный жёлтый: тритон нужен (порог TRITONE_MAX_LUM) — им проверяется, что снятая галка
# свечения тритон не уносит. Яркий (стоковый) жёлтый тритона не получает вовсе.
DARK_HL_FILL = [0.6863, 0.1216, 0.1216]

# Жёлтая строка БЕЗ глитча и без fx — единственная, кому .jsx ставит introHlGlow
# (`yellow && !grpGlitch && fx!="glow"`). Глитч-сосед по группе снял бы вызов, поэтому
# глитч живёт в СВОЕЙ группе (splits=[1]) — и со своей галкой свечение сохраняет.
HL_INTRO = [
    dict(words=["ЖЁЛТОЕ"], color="yellow", times=[T_CAM1]),
    dict(words=["ГЛИТЧ"], color="white", anim="glitch", times=[T_CAM2]),
]
HL_SPLITS = [1]

# Все четыре двери в одной сборке: жёлтый хайлайт (своя группа), глитч и строка со
# свечением (вторая группа: её прекомп получает усиленный 211/93/0.42), обычная группа
# (мягкий Glo2 42/INTRO_GLOW). Жёлтая группа прекомпного свечения не получает и на
# дефолтах — иначе свечение слова и блока складывались бы.
ALL_INTRO = [
    dict(words=["ЖЁЛТОЕ"], color="yellow", times=[T_CAM1]),
    dict(words=["ГЛИТЧ"], color="white", anim="glitch", times=[T_CAM1 + 0.3]),
    dict(words=["СВЕЧЕНИЕ"], color="white", fx="glow", times=[T_CAM1 + 0.6]),
    dict(words=["ФОН"], color="white", times=[T_CAM2]),
]
ALL_SPLITS = [1, 3]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, style=None, intro=None, splits=None, name="glow.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=HL_INTRO if intro is None else intro,
                                   intro_splits=HL_SPLITS if splits is None else splits,
                                   style=dict(style or {}), disclaimer="",
                                   emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style=None):
    return xml2ae.scene_plan(xml, intro=HL_INTRO, intro_splits=HL_SPLITS,
                             style=dict(style or {}), disclaimer="", emit=lambda *a: None)


def _intro_src(jsx):
    """Код интро из .jsx: все четыре двери свечения живут только здесь.

    Весь файл целиком для проверки «нет ни одного Glo2» не годится: свой Glo2 есть
    у головного дисклеймера, и он к интро отношения не имеет.
    """
    return jsx[jsx.index("if (INTRO_GROUPS.length){"):jsx.index("var rotoLayers = [];")]


def _effects(data, text):
    """Список matchName эффектов слоя слова (в порядке добавления)."""
    lay = next(w for w in data["layers"] if w["text"].lower() == text.lower())
    return [e["matchName"] for e in lay["effects"]]


def _precomp_effects(data, gi):
    return [e["matchName"] for e in data["precomps"][gi]["effects"]]


# ---- 3-я дверь: свечение жёлтого хайлайта -----------------------------------------------

@node
def test_hl_glow_выключено_жёлтое_слово_не_светится(xml_subs, tmp_path):
    """intro_hl_glow=False: ни вызовов introHlGlow, ни самой функции, у жёлтого слова нет
    Glo2 — а глитч-слово со своей включённой галкой свечение сохраняет (галки независимы).
    """
    jsx = _build(xml_subs, tmp_path, style={"intro_hl_glow": False}, name="hl_off.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "introHlGlow" not in jsx, "функция/вызовы свечения хайлайта остались в .jsx"
    assert "ADBE Glo2" not in _effects(data, "ЖЁЛТОЕ")
    assert "ADBE Glo2" in _effects(data, "ГЛИТЧ"), "галка глитча погасла вместе с хайлайтом"


def test_hl_glow_дефолт_жёлтое_слово_светится(xml_subs, tmp_path):
    """Дефолт (галка включена): жёлтое слово получает introHlGlow с прежними числами."""
    jsx = _build(xml_subs, tmp_path, style={}, name="hl_on.jsx")

    assert "introHlGlow" in jsx
    assert 'addFX(L,"ADBE Glo2"); setP(fxGl,"ADBE Glo2-0002",149);' \
           ' setP(fxGl,"ADBE Glo2-0003",77); setP(fxGl,"ADBE Glo2-0004",0.62);' in jsx


@node
def test_hl_glow_выключено_тритон_тёмного_жёлтого_остался(xml_subs, tmp_path):
    """Тритон (покраска жёлтой буквы) к свечению отношения не имеет: на тёмном жёлтом
    функция остаётся — ровно без строки Glo2.
    """
    jsx = _build(xml_subs, tmp_path, style={"intro_hl_glow": False,
                                            "hl_fill": DARK_HL_FILL}, name="hl_dark.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "introHlGlow" in jsx, "тритон тёмного жёлтого уехал вместе со свечением"
    eff = _effects(data, "ЖЁЛТОЕ")
    assert "ADBE Glo2" not in eff, "свечение осталось при снятой галке"
    assert "ADBE Tritone" in eff, "тритон жёлтой буквы пропал"


# ---- 4-я дверь: свечение слоя прекомпа --------------------------------------------------

@node
def test_comp_glow_дефолт_обе_ветки_на_месте(xml_subs, tmp_path):
    """Дефолт: у группы с глитчем прекомп получает усиленный Glo2, у обычной — мягкий.

    Жёлтая группа прекомпного свечения не получает и здесь: иначе свечение слова
    (introHlGlow) и свечение блока складывались бы — так решено в шаблоне.
    """
    jsx = _build(xml_subs, tmp_path, intro=ALL_INTRO, splits=ALL_SPLITS,
                 style={}, name="comp_on.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert '"ADBE Glo2-0002",211' in jsx, "усиленной ветки глитча нет в .jsx"
    assert "ADBE Glo2" in _precomp_effects(data, 1), "прекомп группы с глитчем не светится"
    assert "ADBE Glo2" in _precomp_effects(data, 2), "прекомп обычной группы не светится"
    assert "ADBE Glo2" not in _precomp_effects(data, 0), "жёлтая группа: свечения сложились"


@node
def test_comp_glow_выключено_прекомп_не_светится(xml_subs, tmp_path):
    """intro_comp_glow=False: на слое прекомпа Glo2 нет НИ В ОДНОЙ из веток, а на словах
    он остался — галка прекомпа свечение слов не трогает.
    """
    jsx = _build(xml_subs, tmp_path, intro=ALL_INTRO, splits=ALL_SPLITS,
                 style={"intro_comp_glow": False}, name="comp_off.jsx")
    data = _probe_intro(jsx, tmp_path)

    for gi in range(len(data["precomps"])):
        assert "ADBE Glo2" not in _precomp_effects(data, gi), \
            "Glo2 на прекомпе группы %d" % gi
    assert '"ADBE Glo2-0002",211' not in jsx, "усиленная ветка глитча осталась в .jsx"
    assert "Glow Radius" not in _intro_src(jsx), "ветка 42/INTRO_GLOW осталась в .jsx"
    for text in ("ГЛИТЧ", "СВЕЧЕНИЕ", "ЖЁЛТОЕ"):
        assert "ADBE Glo2" in _effects(data, text), text


# ---- Все четыре галки: ни одного Glo2 ---------------------------------------------------

@node
def test_все_четыре_галки_сняты_ни_одного_glo2(xml_subs, tmp_path):
    """Все четыре галки свечения сняты — в коде интро НЕТ НИ ОДНОГО ADBE Glo2.

    Главная проверка задания: ровно этого просил владелец («выключаю глоу — он всё
    равно есть»). Размытие анимации глитча (Gaussian Blur) к свечению отношения не
    имеет и остаётся на месте.
    """
    jsx = _build(xml_subs, tmp_path, intro=ALL_INTRO, splits=ALL_SPLITS,
                 style={"intro_glitch_glow": False, "intro_fx_glow": False,
                        "intro_hl_glow": False, "intro_comp_glow": False},
                 name="glow_off.jsx")
    data = _probe_intro(jsx, tmp_path)

    assert "ADBE Glo2" not in _intro_src(jsx), "в коде интро осталось свечение"
    assert "introHlGlow" not in jsx
    for text in ("ЖЁЛТОЕ", "ГЛИТЧ", "СВЕЧЕНИЕ", "ФОН"):
        assert "ADBE Glo2" not in _effects(data, text), text
    for gi in range(len(data["precomps"])):
        assert "ADBE Glo2" not in _precomp_effects(data, gi), "прекомп %d" % gi
    assert "ADBE Gaussian Blur 2" in _effects(data, "ГЛИТЧ")


# ---- План и превью ----------------------------------------------------------------------

def test_план_несёт_галки_свечения_для_превью(xml_subs):
    """plan.intro_word_fx несёт glow_hl/glow_comp — дверь в превью, как у прочих галок."""
    plan = _plan(xml_subs, style={})
    assert plan["intro_word_fx"]["glow_hl"] is True
    assert plan["intro_word_fx"]["glow_comp"] is True

    plan = _plan(xml_subs, style={"intro_hl_glow": False, "intro_comp_glow": False})
    assert plan["intro_word_fx"]["glow_hl"] is False
    assert plan["intro_word_fx"]["glow_comp"] is False


def test_превью_читает_новые_галки_из_плана_а_не_из_стиля():
    """Сторож фронта: ipvIntro берёт галки из плана, ключей стиля не знает.

    Второе чтение стиля во фронте — ровно тот баг, от которого уходит эта дверь:
    правка галки обязана доезжать по stEdit() -> ipvPlanSoon() -> /api/scene -> ipvUI().
    """
    js = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
              encoding="utf-8").read()
    for token in ("wfx.glow_hl", "wfx.glow_comp"):
        assert token in js, "превью не читает из плана: " + token
    for key in NEW_KEYS:
        assert key not in js, "ключ стиля %s читается во фронте вторым чтением" % key


def test_галки_в_base_схеме_и_переводах():
    """Связка «BASE <-> схема <-> en.json»: обе галки в группе intro.glow, дефолт True,
    подпись и подсказка переведены (иначе краснеет test_style_schema)."""
    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))

    found = {}

    def walk(items, gid=None):
        for x in items:
            cur = x["id"] if x.get("type") == "group" else gid
            if x.get("type") == "field" and x.get("key") in NEW_KEYS:
                found[x["key"]] = (cur, x)
            walk(x.get("items", []), cur)

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []), layer.get("id"))

    for key in NEW_KEYS:
        assert styles.BASE.get(key) is True, "дефолт %s не True — golden поедет" % key
        assert key in found, "ключа %s нет в схеме стиля" % key
        gid, field = found[key]
        assert gid == "intro.glow", "%s заведён не в группе «Свечение»: %s" % (key, gid)
        assert field.get("ctl") == "bool", "%s перестала быть галкой" % key
        assert en.get(field["label"]), "нет перевода подписи %s" % key
        assert en.get(field["tip"]), "нет перевода подсказки %s" % key


# ---- Дефолты ничего не меняют (golden) --------------------------------------------------

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
