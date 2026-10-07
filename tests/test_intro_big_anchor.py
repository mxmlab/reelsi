# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Группа «большое слева» стоит там, где её ставит СТИЛЬ, как любая другая группа.

Положение группы интро задают ключи стиля: `intro_y` (камера 1) | `intro_y2` (камера 2) и
якорь блока `intro_anchor` | `intro_anchor2` («first» — по первой, то есть ВЕРХНЕЙ строке).
У группы с большой строкой верхняя строка блока — это первая строка СТОПКИ справа (большая
строка садится на базовую линию стопки и слева от неё), и она обязана стоять на той же базе,
что верхняя строка обычной группы того же масштаба и камеры; `y` группы — тот же, что у
обычных, без собственной добавки.

Раньше у таких групп была своя подгонка верха (`intro_big_top_shift`): верх блока ровняли по
обычной раскладке той же группы. Большая строка подобрана под высоту стопки и ручкой
`intro_big_over` приподнята над верхом — подгонка сдвигала ВСЮ стопку с базы якоря, а вместе
с ней уезжал и `y` (компенсация якоря масштаба считается от верхней строки блока). Блок
вставал не там, где его поставил стиль, и владелец поднимал группы ручкой смещения.

Здесь:
  1. база верхней строки стопки «большого слева» в КАДРЕ равна базе верхней строки обычной
     группы того же стиля и камеры (±1 px), и `y` групп равны — и при ручном масштабе (ds
     совпадают), и при автофите (ds соседей близки);
  2. то же для камеры 2: положение берётся из ЕЁ ключей (`intro_y2`/`intro_anchor2`), смена
     `intro_y` группы камеры 2 не двигает;
  3. смена `intro_y` двигает «большое слева» ровно так же, как обычную группу;
  4. обычные группы не тронуты: их `ys`, `y` и якорь те же, а сборка со стилем по умолчанию
     побайтово равна эталону `fixtures/golden_geometry.jsx` (эталон не перегенерируется);
  5. якорь доезжает до .jsx (INTRO_ANCHOR_Y) — второй двери у этого числа нет.

Раскладка строк ВНУТРИ блока (`intro_big_layout`: большая слева, стопка справа, кегль
большой) не менялась — её стерегут `tests/test_intro_big.py` и `tests/test_intro_back_after.py`.

Числа теста — формулы метрик (`fonts.text_width`/`ink_extent` подменены, шрифта с именем
TestInk-Regular нет), а не то, какие шрифты стоят на машине.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_BASE_Y, intro_block_span, intro_line_ys  # noqa: E402
from test_geometry_python import _build as _golden_build, _mask_assets  # noqa: E402

PS = "TestInk-Regular"          # шрифта с таким именем нет: капитель 0.72 кегля, выносные 0.24
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер фикстуры: 1-я секунда — кам1, 8-я — перебивка
H = 1920.0                      # кадр фикстуры timeline_subs.xml.gz
HALF = H / 2.0                  # 960 — центр композиции прекомпа
INTRO_Y, INTRO_Y2 = 826.0, 700.0
# Якорь блока и точка масштабирования — «по верхней строке», как в стиле владельца: у обычной
# группы это её первая строка, у «большого слева» — первая строка стопки справа.
STYLE = {"intro_anchor": "first", "intro_scale_anchor": "first",
         "intro_anchor2": "first", "intro_scale_anchor2": "first",
         "intro_y": INTRO_Y, "intro_y2": INTRO_Y2,
         "intro_pos2_v": 2}      # intro_y2 — САМО положение, а не добавка к intro_y
GS = 97.0                       # ручной масштаб группы: автофит её не трогает, ds обеих групп один
TOP_TOL = 1.0                   # «база верхней строки стопки == база верхней строки соседа», px
GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")


def _big_rows(t0, gs=GS):
    """Группа «большое слева»: большая строка ПЕРВАЯ в списке, под ней стопка из двух строк."""
    rows = [{"words": ["8"], "color": "white", "times": [t0], "big": True},
            {"words": ["КИЛО"], "color": "white", "times": [t0 + 0.4]},
            {"words": ["Д" * 8], "color": "white", "times": [t0 + 0.8]}]
    return _head(rows, gs)


def _plain_rows(t0, gs=GS):
    """Обычная группа того же стиля: строка заднего плана сверху и обычная под ней."""
    rows = [{"words": ["Б" * 12], "color": "white", "times": [t0], "back": True},
            {"words": ["К" * 11], "color": "white", "times": [t0 + 0.4]}]
    return _head(rows, gs)


def _head(rows, gs):
    """Масштаб группы рукой (gs): автофит тогда её не ужимает и ds у групп совпадает.

    gs = None — масштаб подбирает автофит, как в жизни: там ds соседей близки, но не равны,
    и проверка базы верха не должна зависеть от этой разницы.
    """
    if gs is not None:
        rows[0]["gs"] = gs
    return rows


def _intro(t0, gs=GS):
    """Две группы подряд: «большое слева», затем обычная. splits — голова второй группы."""
    return _big_rows(t0, gs) + _plain_rows(t0 + 0.6, gs)


SPLITS = [3]


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture()
def metrics(monkeypatch):
    """Метрики шрифта — формулы: буква = кегль, высот из файла нет (капитель 0.72 кегля).
    Числа теста не зависят от того, какие шрифты стоят на машине."""
    monkeypatch.setattr(fonts, "text_width", lambda ps, text, size: float(size) * len(text))
    monkeypatch.setattr(fonts, "ink_extent", lambda ps, text, size: None)


def _scene(xml, style=None, intro=None):
    # cam1_scale=[] — зума камеры нет: числа не зависят от кривой зума фикстуры (та же дверь,
    # что у соседних тестов интро).
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=_intro(T_CAM1) if intro is None else intro,
                             intro_splits=SPLITS,
                             style=dict(STYLE, **(style or {}), font=PS),
                             cam1_scale=[], emit=lambda *a, **k: None)


def _groups(xml, style=None, intro=None):
    """Группы интро из плана: ds/ys/y/anchor_y готовые, второй копии расчёта в тесте нет."""
    return _scene(xml, style, intro)["intro"]


def _row_base(plan, g, i):
    """База строки i в КАДРЕ, px от верха — дверью сборки `intro_block_span`.

    Кегль строки 0 выключает капитель и выносные, и полоса строки схлопывается ровно в её
    базовую линию: своей копии формулы «центр блока + смещение строки × масштаб прекомпа» в
    тесте не заводится.
    """
    top, bottom = intro_block_span([g["ys"][i]], [0.0], plan["h"], ds=g["ds"],
                                   g=plan["intro_scale"], y=g["y"], dy=g["dy"], zoom=100.0,
                                   intro_cam=g["cam"], fonts=[(g["fonts"] or [None])[i]])
    assert top == bottom, "нулевой кегль не схлопнул строку в базу: %s != %s" % (top, bottom)
    return top


def _top_stack_row(g):
    """Индекс верхней строки СТОПКИ группы с большой строкой: минимальный непустой ys.

    У обычной группы строки идут сверху вниз, и это её первая строка.
    """
    return min((i for i, v in enumerate(g["ys"]) if v is not None),
               key=lambda i: float(g["ys"][i]))


def _build(xml, tmp_path, style=None, intro=None, name="big_left.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=_intro(T_CAM1) if intro is None else intro,
                                   intro_splits=SPLITS,
                                   style=dict(STYLE, **(style or {}), font=PS),
                                   intro_mode="word", disclaimer="", intro_riser=False,
                                   cam1_scale=[], emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _jsx_arr(jsx, name):
    """Массив `NAME=[...]` из .jsx — по парным скобкам (объявление может быть не первым
    в строке: `var INTRO_ANCHOR_Y=…, INTRO_ANCHOR_DY=…;`)."""
    m = re.search(r"(?<![\w$])%s=\[" % re.escape(name), jsx)
    assert m, "в .jsx нет массива %s" % name
    i = m.end() - 1
    depth = 0
    for j in range(i, len(jsx)):
        if jsx[j] == "[":
            depth += 1
        elif jsx[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(jsx[i:j + 1])
    raise AssertionError("не сошлись скобки у массива %s" % name)


# =========================== 1. верх стопки «большого слева» — на базе обычной группы

@pytest.mark.parametrize("gs", [GS, None], ids=["ручной масштаб", "автофит"])
def test_big_left_stands_where_the_style_puts_it(metrics, xml_subs, gs):
    """Верхняя строка стопки «большого слева» — на той же базе, что верхняя строка обычной
    группы того же стиля и камеры (±1 px кадра), и `y` групп равны.

    Никакой своей подгонки у группы с большой строкой нет: обе стоят на базе якоря стиля
    (960 — верхняя строка), поэтому базы совпадают, а вместе с ними и `y`.
    """
    plan = _scene(xml_subs, intro=_intro(T_CAM1, gs))
    big, plain = plan["intro"][0], plan["intro"][1]

    assert big.get("lk") and big["lk"][0], "фикстура: большая строка не разложилась"
    if gs is not None:
        assert big["ds"] == plain["ds"] == gs, "фикстура: масштаб групп не тот, что задан рукой"
    else:
        assert abs(big["ds"] - plain["ds"]) < 5.0, "фикстура: ds соседей далеки друг от друга"

    top_big = _row_base(plan, big, _top_stack_row(big))
    top_plain = _row_base(plan, plain, 0)
    print("\nверх стопки «большого слева» %s; верх обычной группы %s; y %s и %s"
          % (round(top_big, 2), round(top_plain, 2), big["y"], plain["y"]))
    assert abs(top_big - top_plain) <= TOP_TOL, (
        "верх стопки «большого слева» (%s) разошёлся с верхом обычной группы (%s)"
        % (top_big, top_plain))
    assert big["y"] == plain["y"], "y группы с большой строкой получил свою добавку"
    assert big["anchor_y"] == plain["anchor_y"] == HALF, \
        "якорь масштаба группы уехал с верхней строки (базы стиля)"


def test_the_top_stack_row_sits_on_the_style_anchor(metrics, xml_subs):
    """Базовая линия верхней строки стопки в прекомпе — ровно база якоря стиля (960).

    Это то же число, что у обычной группы: у «first» первая строка стоит в центре
    композиции прекомпа, а большая строка садится на низ стопки и на верх не влияет.
    """
    big, plain = _groups(xml_subs)[:2]
    assert min(big["ys"]) == big["ys"][_top_stack_row(big)] == HALF
    assert plain["ys"][0] == HALF
    assert big["anchor_y"] == plain["anchor_y"] == HALF


# =========================== 2. камера 2 — свои ключи

def test_big_left_on_camera_2_takes_the_keys_of_its_own_camera(metrics, xml_subs):
    """На перебивке положение берётся из `intro_y2`/`intro_anchor2`, а не от камеры 1.

    Две группы на камере 2 (большая и обычная) стоят по `intro_y2`, и смена `intro_y`
    (ключ камеры 1) их не двигает вовсе.
    """
    plan = _scene(xml_subs, intro=_intro(T_CAM2))
    big, plain = plan["intro"][0], plan["intro"][1]
    assert bool(big["on2"]) and bool(plain["on2"]), "фикстура: группы не попали на камеру 2"

    top_big = _row_base(plan, big, _top_stack_row(big))
    top_plain = _row_base(plan, plain, 0)
    assert abs(top_big - top_plain) <= TOP_TOL, (
        "на камере 2 верх стопки «большого слева» (%s) разошёлся с верхом обычной группы (%s)"
        % (top_big, top_plain))
    assert big["y"] == plain["y"] == round(INTRO_Y2 - INTRO_BASE_Y, 2), \
        "группа камеры 2 стоит не по intro_y2"

    shifted = _scene(xml_subs, {"intro_y": INTRO_Y + 100.0}, _intro(T_CAM2))["intro"]
    assert shifted[0]["y"] == big["y"] and shifted[1]["y"] == plain["y"], \
        "intro_y (камера 1) подвинул группы камеры 2"

    moved = _scene(xml_subs, {"intro_y2": INTRO_Y2 + 100.0}, _intro(T_CAM2))["intro"]
    for before, after in zip((big, plain), moved):
        assert after["y"] == round(before["y"] + 100.0, 2), \
            "intro_y2 не подвинул группу камеры 2 на свою величину"


# =========================== 3. смена intro_y двигает обе группы одинаково

def test_the_style_position_moves_both_groups_equally(metrics, xml_subs):
    """`intro_y` в стиле двигает «большое слева» на ту же величину, что обычную группу.

    Группа с большой строкой едет за стилем, а не за своим верхом: своей ручки положения
    у неё нет, поэтому сдвиг стиля для обеих групп один и тот же — ровно 100 px кадра.
    """
    a = _scene(xml_subs)
    b = _scene(xml_subs, {"intro_y": INTRO_Y + 100.0})
    ga, gb = a["intro"], b["intro"]
    shift_big = _row_base(b, gb[0], _top_stack_row(gb[0])) - _row_base(a, ga[0], _top_stack_row(ga[0]))
    shift_plain = _row_base(b, gb[1], 0) - _row_base(a, ga[1], 0)
    print("\nсдвиг «большого слева» %s px, обычной группы %s px" % (shift_big, shift_plain))
    assert shift_big == pytest.approx(100.0, abs=0.01), "«большое слева» не поехало за стилем"
    assert shift_big == pytest.approx(shift_plain, abs=0.01), \
        "«большое слева» и обычная группа поехали за стилем по-разному"


# =========================== 4. обычные группы не тронуты

def test_plain_groups_and_the_golden_build_are_untouched(metrics, xml_subs, tmp_path):
    """Обычная группа: прежняя раскладка строк, `y` без компенсации, якорь — центр базы.

    Сборка со стилем по умолчанию побайтово равна эталону `golden_geometry.jsx`: эталон не
    перегенерируется, и правка «большого слева» обычных групп не касается.
    """
    plan = _scene(xml_subs)
    plain = plan["intro"][1]
    ys_expected = intro_line_ys(plain["lines"], _back_step(plan), True, "first", H,
                                step_k=1.0)
    assert plain["ys"] == ys_expected, "раскладка строк обычной группы изменилась"
    assert plain["anchor_y"] == HALF, "якорь обычной группы уехал из центра композиции"
    assert plain["y"] == pytest.approx(INTRO_Y - INTRO_BASE_Y, abs=0.01), \
        "y обычной группы получил компенсацию якоря"

    if os.path.isfile(GOLDEN):
        jsx = _mask_assets(_golden_build(xml_subs, tmp_path, style={}))
        golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
        assert jsx == golden, "сборка со стилем по умолчанию разошлась с эталоном"


def _back_step(plan):
    """Шаг строк заднего плана стиля — тем же ключом, что читает сборка (plan_style)."""
    from core import styles
    from core.xml2ae.plan_style import read_style
    return read_style(styles.resolve(dict(STYLE, font=PS)),
                      int(plan["w"]), int(plan["h"])).back_step


# =========================== 5. числа доезжают до .jsx

def test_the_position_reaches_the_jsx(metrics, xml_subs, tmp_path):
    """INTRO_ANCHOR_Y и INTRO_LY в .jsx — готовые числа плана, второй двери у них нет.

    У группы с большой строкой якорь — верхняя строка стопки (у обычной он совпадает с
    первой строкой), а раскладка строк уезжает списком `ys` как есть. Компенсация Position
    (INTRO_ANCHOR_DY) у такой группы нулевая: якорь стоит на базе стиля.
    """
    jsx = _build(xml_subs, tmp_path)
    plan = _scene(xml_subs)
    big, plain = plan["intro"][0], plan["intro"][1]

    anchors = _jsx_arr(jsx, "INTRO_ANCHOR_Y")
    assert anchors[0] == big["anchor_y"] == min(big["ys"]) == HALF, \
        "INTRO_ANCHOR_Y большой группы не на верхней строке стопки"
    assert anchors[1] == plain["anchor_y"] == plain["ys"][0] == HALF, \
        "INTRO_ANCHOR_Y обычной группы уехал с первой строки"
    assert _jsx_arr(jsx, "INTRO_LY") == [big["ys"], plain["ys"]], \
        "INTRO_LY разошёлся с раскладкой плана"
    dy = _jsx_arr(jsx, "INTRO_ANCHOR_DY")
    assert dy[0] == 0.0, "компенсация якоря у группы с большой строкой не нулевая"
    assert 'setValue([IW/2, INTRO_ANCHOR_Y[gI]]);' in jsx, "якорь не ставится в .jsx"
