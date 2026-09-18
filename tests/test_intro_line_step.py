# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Межстрочный интервал интро: ползунок intro_line_step (задание ZO).

Шаг строки внутри прекомпа интро был жёсткими 160 px: шаг заднего плана (back_step) и
зазор малых строк (back_gap) регулируются, основной шаг — нет. Теперь ключ стиля
intro_line_step (%) даёт ОДИН множитель k = intro_line_step/100 на оба места:

  * шаги строк и центровку блока считает Python (`intro_line_ys`, `_intro_i_dy`);
  * в шаблон уезжает готовое число: `var LINE_STEP=160*k` (template.py).

При 100 (дефолт) множитель равен единице, и .jsx фикстуры остаётся прежним БАЙТ В БАЙТ
(эталон fixtures/golden_geometry.jsx) — поэтому внутри функции шаг собран одной
переменной, а не множителем в каждой формуле.

Здесь:
  1. golden: intro_line_step=100 (и дефолт) — .jsx побайтово как на main;
  2. `intro_line_ys` при step_k=1.5: разности соседних y ×1.5 (без заднего плана, с ним
     и при якоре «first»); зазор по ЧЕРНИЛАМ не множится — он и так не меньше базового;
  3. сборка при intro_line_step=150: LINE_STEP=240 в .jsx, y групп разъехались сильнее;
  4. сторож «каждая ручка»: ключ есть в схеме стиля и реально влияет на сборку.

Шрифт намеренно несуществующий (`PS`): автофит интро тогда не находит ширин и не
ужимает группу (`_intro_fit_ds` возвращает ds), поэтому числа iDy в тестах считаются
от gs=100 и от системных шрифтов машины не зависят.
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_LINE_STEP, intro_line_ys  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402

PS = "TestInk-Regular"          # шрифта с таким именем в системе нет — см. докстринг
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер фикстуры: 1-я секунда кам1, 8-я — перебивка


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм golden: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture()
def ink(monkeypatch):
    """Детерминированные чернила строки: asc 0.7·size, desc 0.2·size (как в других тестах)."""
    def mock_ink_extent(ps_name, text, size_px):
        if not text or not str(text).strip():
            return (0.0, 0.0)
        k = float(size_px)
        return (round(0.7 * k, 2), round(0.2 * k, 2))

    monkeypatch.setattr(fonts, "ink_extent", mock_ink_extent)


def _ln(word, back=False):
    return {"words": [word], "times": [T_CAM1], **({"back": True} if back else {})}


def _ys(lines, fonts_list, step_k=None, fs=100.0, bs=0.69, bstep=0.45, gap=4.0,
        any_back=False, anchor="center", h=1920.0):
    kw = {} if step_k is None else {"step_k": step_k}
    return intro_line_ys(lines, fonts_list, fs, bs, bstep, gap, any_back, anchor, h, **kw)


def _plan(xml_subs, intro, splits=None, style=None):
    return xml2ae.scene_plan(xml_subs, disclaimer="", intro_riser=False, intro=intro,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS), emit=lambda *a, **k: None)


def _build_intro(xml_subs, tmp_path, intro, style=None, splits=None):
    path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "intro.jsx"),
                                   intro=intro, intro_splits=(splits or []),
                                   style=dict(style or {}, font=PS),
                                   disclaimer="", intro_riser=False,
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


# ---- 1. дефолт 100 = прежний .jsx (golden) --------------------------------------------------

def test_default_100_jsx_is_byte_identical_to_golden(xml_subs, tmp_path):
    """intro_line_step=100: .jsx фикстуры побайтово совпадает с эталоном main.

    Так же проверяется и просто дефолт стиля (ключ не задан вовсе): BASE несёт 100.
    """
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    for st in ({}, {"intro_line_step": 100}):
        jsx = _mask_assets(_build(xml_subs, tmp_path, style=dict(st)))
        assert jsx == golden, f"стиль {st}: .jsx разошёлся с эталоном"

    # и в сборке С ИНТРО шаг на месте: объявление прежнее (160), раскладка — прежняя формула
    intro = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["g"], color="white", times=[T_CAM1 + 0.5])]
    jsx = _build_intro(xml_subs, tmp_path, intro, splits=[])
    assert "var LINE_STEP=160," in jsx, "дефолтный шаг строк интро уехал со 160"
    assert "cY=H/2 - (nL-1)/2*LINE_STEP, maxLineW=0;" in jsx


# ---- 2. шаги строк ×1.5 ---------------------------------------------------------------------

def test_step_k_scales_line_gaps_without_back():
    """2а. Ветка без строк заднего плана: разности соседних y ровно ×1.5, база — h/2."""
    h, fs, bs = 1920.0, 100.0, 0.69
    lines = [_ln("A"), _ln("A"), _ln("A")]
    ys1 = _ys(lines, [None] * 3, 1.0, fs, bs, 0.45, 4.0, False, "center", h)
    ys15 = _ys(lines, [None] * 3, 1.5, fs, bs, 0.45, 4.0, False, "center", h)

    assert [round(ys1[i + 1] - ys1[i], 2) for i in range(2)] == [INTRO_LINE_STEP] * 2
    assert [round(ys15[i + 1] - ys15[i], 2) for i in range(2)] == [240.0] * 2
    # центровка блока тоже по новому шагу: (n-1)/2 от неё
    assert ys15[0] == round(h / 2 - (3 - 1) / 2 * 240.0, 2)
    assert ys15[0] < ys1[0], "блок из трёх строк при шаге 150% не стал выше"


def test_step_k_scales_first_anchor_steps():
    """2б. Якорь «first»: первая строка по-прежнему в h/2, шаги до нижних ×1.5."""
    h = 1920.0
    lines = [_ln("A"), _ln("A")]
    ys1 = _ys(lines, [None] * 2, 1.0, 100.0, 0.69, 0.45, 4.0, False, "first", h)
    ys15 = _ys(lines, [None] * 2, 1.5, 100.0, 0.69, 0.45, 4.0, False, "first", h)
    assert ys15[0] == h / 2 == ys1[0]
    assert round(ys15[1] - ys15[0], 2) == round((ys1[1] - ys1[0]) * 1.5, 2) == 240.0


def test_step_k_scales_back_steps_but_not_ink_gap(ink):
    """2в. С задним планом: БАЗОВЫЕ шаги ×1.5, а зазор по чернилам не множится —
    с ростом шага базовый его догоняет, теснее строки не становятся."""
    h, fs, bs, gap = 1920.0, 200.0, 0.69, 4.0
    lines = [_ln("g"), _ln("A", back=True)]
    # чернила: хвост «g» 0.2·200 = 40 + высота «A» 0.7·(200·0.69) = 96.6 + зазор 4 = 140.6
    ys1 = _ys(lines, [PS, PS], 1.0, fs, bs, 0.45, gap, True, "center", h)
    ys15 = _ys(lines, [PS, PS], 1.5, fs, bs, 0.45, gap, True, "center", h)
    d1 = round(ys1[1] - ys1[0], 2)
    d15 = round(ys15[1] - ys15[0], 2)
    assert d1 == 140.6, "базовый шаг 160·0.45=72 меньше чернил — взят зазор по чернилам"
    assert d15 == d1, "зазор по чернилам умножился на k, а не остался прежним"
    # центровка с головой не-back — те же 60, но по новому шагу
    assert ys1[0] == round(h / 2 - 60.0, 2)
    assert ys15[0] == round(h / 2 - 60.0 * 1.5, 2)

    # базовый шаг перерос чернила (back_step 1.0 = 160 px) — он и множится: 160 → 240
    b1 = _ys(lines, [PS, PS], 1.0, fs, bs, 1.0, gap, True, "center", h)
    b15 = _ys(lines, [PS, PS], 1.5, fs, bs, 1.0, gap, True, "center", h)
    assert round(b1[1] - b1[0], 2) == 160.0
    assert round(b15[1] - b15[0], 2) == 240.0


# ---- 3. сборка при 150 ----------------------------------------------------------------------

INTRO_SPLIT = [
    {"words": ["A"], "color": "white", "times": [T_CAM1]},
    {"words": ["g"], "color": "white", "times": [T_CAM1 + 0.5]},
    {"words": ["A"], "color": "white", "times": [T_CAM1 + 1.0]},
    {"words": ["g"], "color": "white", "times": [T_CAM2]},
    {"words": ["A"], "color": "white", "times": [T_CAM2 + 0.5]},
]
SPLITS = [3]        # группа кам1 из трёх строк + группа кам2 (перебивка) из двух


def test_build_150_sets_line_step_and_spreads_groups(xml_subs, tmp_path):
    """3. intro_line_step=150: в .jsx LINE_STEP=240, а y групп в плане разъехались сильнее,
    чем при 100 (блок выше — его сильнее опускают под SAFE_TOP)."""
    jsx100 = _build_intro(xml_subs, tmp_path, INTRO_SPLIT, style={"intro_line_step": 100},
                          splits=SPLITS)
    jsx150 = _build_intro(xml_subs, tmp_path, INTRO_SPLIT, style={"intro_line_step": 150},
                          splits=SPLITS)
    assert "var LINE_STEP=160," in jsx100
    assert "var LINE_STEP=240," in jsx150, "шаг строк интро не доехал до шаблона"

    p100 = _plan(xml_subs, INTRO_SPLIT, splits=SPLITS, style={"intro_line_step": 100})
    p150 = _plan(xml_subs, INTRO_SPLIT, splits=SPLITS, style={"intro_line_step": 150})
    assert len(p100["intro"]) == len(p150["intro"]) == 2
    y100 = [g["y"] for g in p100["intro"]]
    y150 = [g["y"] for g in p150["intro"]]
    assert y100 != y150, "intro_line_step не доехал до позиции блока (y) в плане"
    spread100 = round(abs(y100[0] - y100[1]), 2)
    spread150 = round(abs(y150[0] - y150[1]), 2)
    assert spread150 > spread100, "при 150% блоки не разъехались сильнее, чем при 100%"
    assert spread150 == round(spread100 * 1.5, 2)


# ---- 4. сторож «каждая ручка» ---------------------------------------------------------------

def _schema_field(key):
    def walk(items):
        for x in items:
            if x.get("key") == key or x.get("key2") == key or x.get("toggle") == key:
                return x
            found = walk(x.get("items", []))
            if found:
                return found
        return None

    for layer in style_schema.LAYERS:
        found = walk(layer.get("items", []))
        if found:
            return found
    return None


def test_knob_in_schema_and_affects_assembly(xml_subs, tmp_path):
    """4. Ручка объявлена схемой (ползунок, %, 50–250, lim 20–400) и влияет на сборку
    фикстуры с двумя строками интро — не только объявлением LINE_STEP, но и раскладкой."""
    field = _schema_field("intro_line_step")
    assert field, "в схеме стиля нет ручки intro_line_step"
    assert field["ctl"] == "num" and styles.BASE["intro_line_step"] == 100
    assert (field["min"], field["max"]) == (50, 250)
    assert (field["lim_min"], field["lim_max"]) == (20, 400)

    intro = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["g"], color="white", times=[T_CAM1 + 0.5])]
    base = _build_intro(xml_subs, tmp_path, intro, splits=[])
    moved = _build_intro(xml_subs, tmp_path, intro, style={"intro_line_step": 150}, splits=[])
    assert moved != base, "intro_line_step не повлиял на собранный .jsx"

    p_base = _plan(xml_subs, intro, splits=[])
    p_moved = _plan(xml_subs, intro, splits=[], style={"intro_line_step": 150})
    d_base = round(p_base["intro"][0]["ys"][1] - p_base["intro"][0]["ys"][0], 2)
    d_moved = round(p_moved["intro"][0]["ys"][1] - p_moved["intro"][0]["ys"][0], 2)
    assert (d_base, d_moved) == (160.0, 240.0), "шаг строк в плане не следует за ручкой"
    assert re.search(r"var LINE_STEP=240,", moved), "в .jsx нет нового шага"
