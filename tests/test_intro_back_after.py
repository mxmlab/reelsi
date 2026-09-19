# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Межстрочный заднего плана отдельно сверху и снизу (задание ZZ).

Одна ручка `back_step` ставила ОДИН шаг базовых линий и НА строку заднего плана, и С неё,
а видимые зазоры при этом разные (у владельца над маленькой строкой 53 px, под ней 23 px):
строки заднего плана строчные (`back_case: lower`), высота букв и хвосты зависят от слов —
одним числом их не выровнять. Теперь шаг ДО строки заднего плана и между строками заднего
плана задаёт `back_step`, шаг ОТ блока заднего плана к обычной строке под ним —
`back_step_after`. Ключа в стиле нет — берётся `back_step` этого же стиля: старые стили
выглядят как раньше, .jsx у них побайтово прежний.

Здесь:
  1. `intro_line_ys` [обычная, задний план, обычная]: `back_step_after=None` — числа как на
     main; `back_step_after=2.0` — шаг над маленькой прежний, под ней `line_step * 2.0`;
  2. блок из двух строк заднего плана: между ними `back_step`, после блока `back_step_after`;
  3. `scene_plan`: стиль без ключа — `plan.intro[].ys` как на main; с ключом — меняется
     только шаг «задний план → обычная», то же в стопке группы с большой строкой;
  4. схема стиля, BASE и перевод en: ручка на месте, ключа в BASE нет, сборка несёт шаг
     в `INTRO_LY` (шаблон его не сосчитает);
  5. панель стиля (боевой 94-stylepanel.js под node): без ключа поле показывает значение
     `back_step`, круг «поля ↔ стиль» ключ не заводит, а правка поля — заводит.

Шрифт сборок намеренно несуществующий (`TestInk-Regular`): вертикаль интро считается без
метрик шрифта, и числа не зависят от того, какие шрифты стоят на машине.
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

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_LINE_STEP, intro_line_ys  # noqa: E402

PS = "TestInk-Regular"
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер в фикстуре: 1-я секунда кам1, 8-я — перебивка


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


def _ln(word, back=False):
    return {"words": [word], "back": True} if back else {"words": [word]}


def _main_ys(lines, back_step, h, step_k=1.0):
    """Y по формуле main (задание ZT): шаг с заднего плана и после него — ОДИН back_step."""
    n = len(lines)
    step = INTRO_LINE_STEP * step_k
    backs = [bool(ln.get("back")) for ln in lines]
    steps = [step * (back_step if (backs[i] or backs[i - 1]) else 1.0) for i in range(1, n)]
    cY = (h / 2 - (n - 1) * 60 * step_k) if (not backs[0] and n > 1) else (h / 2 - sum(steps) / 2)
    ys, y = [], cY
    for s in [0.0] + steps:
        y += s
        ys.append(round(y, 2))
    return ys


def _plan(xml, intro, style=None, splits=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False, intro=intro,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS), emit=lambda *a, **k: None)


def _build_intro(xml, tmp_path, intro, style=None, name="back_after.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[], style=dict(style or {}, font=PS),
                                   disclaimer="", intro_riser=False,
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


# Разметка: обычная строка, строка заднего плана, обычная — видно шаг ДО маленькой и ПОСЛЕ неё.
BACK_INTRO = [
    {"words": ["ВЕРХ"], "color": "white", "times": [T_CAM1]},
    {"words": ["ФОН"], "color": "white", "back": True, "times": [T_CAM1 + 0.5]},
    {"words": ["НИЗ"], "color": "white", "times": [T_CAM1 + 1.0]},
]

# То же, но первой строкой группы — «большое слева»: строки заднего плана живут в СТОПКЕ
# (большая строка шаг не занимает), и шаг стопки считается своим множителем intro_big_step.
BIG_BACK_INTRO = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["ФОН"], "color": "white", "back": True, "times": [T_CAM1 + 0.3]},
    {"words": ["МЕСЯЦ"], "color": "white", "times": [T_CAM1 + 0.6]},
]


# ---- 1. intro_line_ys: шаг снизу — своя ручка, без неё всё как на main ----------------------

def test_line_ys_back_step_after_above_and_below():
    """1. [обычная, задний план, обычная]: `back_step_after=None` — числа как на main;
    2.0 — шаг над маленькой прежний (line_step * back_step), под ней line_step * 2.0."""
    h, bstep, after = 1920.0, 0.65, 2.0
    lines = [_ln("A"), _ln("A", back=True), _ln("A")]

    # Ключа в стиле нет: явный None — тот же расклад, что и вызов без параметра (main)
    main = intro_line_ys(lines, bstep, True, "center", h)
    assert main == _main_ys(lines, bstep, h), "формула main в тесте разошлась с раскладкой"
    assert intro_line_ys(lines, bstep, True, "center", h, back_step_after=None) == main
    assert round(main[2] - main[1], 2) == round(INTRO_LINE_STEP * bstep, 2), \
        "без ручки шаг с заднего плана обязан остаться прежним"

    ys = intro_line_ys(lines, bstep, True, "center", h, back_step_after=after)
    assert round(ys[1] - ys[0], 2) == round(INTRO_LINE_STEP * bstep, 2), \
        "шаг ДО строки заднего плана поехал от ручки «снизу»"
    assert round(ys[2] - ys[1], 2) == round(INTRO_LINE_STEP * after, 2), \
        "шаг ПОСЛЕ строки заднего плана не из back_step_after"
    assert ys[0] == main[0], "центровка блока сдвинулась (голова не back, n > 1 — те же 60 px)"
    assert ys[0] == round(h / 2 - 2 * 60.0, 2)

    # step_k (общий межстрочный интро) множит ОБА шага — как и раньше
    ys_k = intro_line_ys(lines, bstep, True, "center", h, step_k=1.5, back_step_after=after)
    assert round(ys_k[1] - ys_k[0], 2) == round(INTRO_LINE_STEP * 1.5 * bstep, 2)
    assert round(ys_k[2] - ys_k[1], 2) == round(INTRO_LINE_STEP * 1.5 * after, 2)


def test_line_ys_back_block_step_between_and_after():
    """2. [обычная, задний план, задний план, обычная]: между строками заднего плана —
    `back_step`, после блока — `back_step_after` (своя ручка только на выходе из блока)."""
    h, bstep, after = 1920.0, 0.5, 1.5
    lines = [_ln("A"), _ln("A", back=True), _ln("A", back=True), _ln("A")]

    ys = intro_line_ys(lines, bstep, True, "center", h, back_step_after=after)
    assert round(ys[1] - ys[0], 2) == round(INTRO_LINE_STEP * bstep, 2), "шаг до блока не back_step"
    assert round(ys[2] - ys[1], 2) == round(INTRO_LINE_STEP * bstep, 2), \
        "шаг МЕЖДУ строками заднего плана уехал в back_step_after"
    assert round(ys[3] - ys[2], 2) == round(INTRO_LINE_STEP * after, 2), \
        "шаг после блока не из back_step_after"

    # Без ручки — ровно как на main: три одинаковых шага
    assert intro_line_ys(lines, bstep, True, "center", h) == _main_ys(lines, bstep, h)


# ---- 3. план: ручка меняет только шаг «задний план → обычная» ------------------------------

def test_plan_without_key_is_like_main(xml_subs):
    """3а. Стиль без `back_step_after`: `plan.intro[].ys` как на main, а в плане ключ
    остаётся None — сборка не выдумывает число, которого в стиле нет."""
    plan = _plan(xml_subs, BACK_INTRO)
    assert len(plan["intro"]) == 1, "фикстура собралась не в одну группу"
    grp = plan["intro"][0]
    assert plan["back_step_after"] is None, "в план уехало значение, которого нет в стиле"
    assert grp["ys"] == _main_ys(grp["lines"], styles.BASE["back_step"], plan["h"]), \
        "стиль без нового ключа разошёлся с main"
    assert grp["ys"] == intro_line_ys(grp["lines"], styles.BASE["back_step"], True, "center",
                                      plan["h"])


def test_plan_knob_changes_only_step_below(xml_subs):
    """3б. С ключом меняется ТОЛЬКО шаг «задний план → обычная»: шаг до маленькой строки и
    её Y прежние (голова не back — центровка от шагов не зависит, задание A1)."""
    before = _plan(xml_subs, BACK_INTRO)["intro"][0]
    after = _plan(xml_subs, BACK_INTRO, style={"back_step_after": 1.5})
    grp = after["intro"][0]
    assert after["back_step_after"] == 1.5, "ручка не доехала до плана"

    assert grp["ys"][:2] == before["ys"][:2], "сдвинулись строки выше заднего плана"
    assert round(grp["ys"][2] - grp["ys"][1], 2) == round(INTRO_LINE_STEP * 1.5, 2), \
        "шаг с заднего плана на обычную не 160 * 1.5"
    assert round(before["ys"][2] - before["ys"][1], 2) == \
        round(INTRO_LINE_STEP * styles.BASE["back_step"], 2), "дефолт ручки разошёлся с BASE"


def test_plan_big_group_stack_uses_back_step_after(xml_subs):
    """3в. То же в СТОПКЕ группы с большой строкой: шаг стопки — 160 * intro_big_step/100,
    а шаг «задний план → обычная» в ней — ещё и на back_step_after. Большая строка остаётся
    на базовой линии последней строки стопки."""
    unit = INTRO_LINE_STEP * styles.BASE["intro_big_step"] / 100.0   # 160 * 0.8 = 128 px
    before = _plan(xml_subs, BIG_BACK_INTRO)["intro"][0]
    grp = _plan(xml_subs, BIG_BACK_INTRO, style={"back_step_after": 1.5})["intro"][0]

    assert len(before["ys"]) == len(grp["ys"]) == 3
    assert round(before["ys"][2] - before["ys"][1], 2) == \
        round(unit * styles.BASE["back_step"], 2), "без ручки шаг стопки не line_step * back_step"
    assert round(grp["ys"][2] - grp["ys"][1], 2) == round(unit * 1.5, 2), \
        "в стопке шаг с заднего плана не из back_step_after"
    assert before["ys"][0] == before["ys"][2] and grp["ys"][0] == grp["ys"][2], \
        "большая строка ушла с базовой линии последней строки стопки"


# ---- 4. схема, BASE, перевод и .jsx ---------------------------------------------------------

def _schema_field(key):
    """Поле схемы стиля по ключу (обход групп) или None."""
    def walk(items):
        for x in items:
            if x.get("key") == key:
                return x
            found = walk(x.get("items", []))
            if found:
                return found
        return None

    for layer in style_schema.LAYERS:
        field = walk(layer.get("items", []))
        if field:
            return field
    return None


def test_schema_has_back_step_after_pair():
    """4а. Обе ручки в схеме: подписи «сверху»/«снизу», одинаковые диапазоны и conv; в BASE
    нового ключа нет (None) — старые стили считаются по back_step."""
    f_before = _schema_field("back_step")
    f_after = _schema_field("back_step_after")
    assert f_before is not None and f_after is not None, "ручек межстрочного заднего плана нет в схеме"
    assert f_before["label"] == "Межстрочный заднего плана сверху, %"
    assert f_after["label"] == "Межстрочный заднего плана снизу, %"

    for key in ("ctl", "min", "max", "lim_min", "lim_max", "step", "conv"):
        assert f_after.get(key) == f_before.get(key), f"у back_step_after {key} не как у back_step"
    assert (f_after["min"], f_after["max"], f_after["step"]) == (10, 300, 1)

    # Ключа нет — значение берётся у пары: поле nullable, пара названа в схеме
    assert styles.BASE["back_step_after"] is None, "BASE завёл значение — старые стили поехали"
    assert f_after.get("nullable") is True
    assert f_after.get("fallback_key") == "back_step", \
        "панель стиля не знает, чьё значение показывать при пустом ключе"

    en = json.loads(open(os.path.join(ROOT, "static", "i18n", "en.json"),
                         encoding="utf-8").read())
    assert en[f_after["label"]] == "Background line spacing below, %"
    assert en[f_before["label"]] == "Background line spacing above, %"
    assert f_after["tip"] in en and f_before["tip"] in en, "тултипы ручек без перевода"


def test_jsx_intro_ly_carries_back_step_after(xml_subs, tmp_path):
    """4б. .jsx: шаг снизу уезжает готовой раскладкой INTRO_LY (шаблон считает шаг по
    одному BACK_STEP и нового числа не знает), и числа совпадают с планом."""
    plan = _plan(xml_subs, BACK_INTRO, style={"back_step_after": 1.5})
    jsx = _build_intro(xml_subs, tmp_path, BACK_INTRO, style={"back_step_after": 1.5})
    assert "var INTRO_LY=" in jsx, "INTRO_LY не объявлен"
    assert "lineY=INTRO_LY[gI][qi]" in jsx, "шаблон считает lineY сам, а не берёт готовое"
    m = re.search(r"var INTRO_LY=(\[.*?\]);\s*//", jsx)
    assert m, "INTRO_LY не разобрался"
    assert json.loads(m.group(1)) == [plan["intro"][0]["ys"]], \
        "INTRO_LY в .jsx разошёлся с планом"


# ---- 5. панель стиля: показывает значение пары и не заводит ключ сам ------------------------

PANEL_FALLBACK = r"""
// Ключа в стиле нет: поле обязано ПОКАЗАТЬ значение пары (back_step = 0.4 → 40 %), а круг
// панели — не завести ключ, которого в стиле не было (иначе «нет ключа → берётся back_step»
// ломается от одного открытия панели).
buildPanel();
CURSTYLE = { back_step: 0.4 };
fillStyleFields();
const span = document.getElementById('st_back_step_after_val');
const shown = span ? span.textContent : null;
stEdit();
const kept = CURSTYLE.back_step_after;

// Правка поля (пользователь поставил 80 %) — теперь значение задано и уезжает в стиль.
const edit = document.getElementById('st_back_step_after_val');
edit.textContent = 80;
stEdit();

console.log(JSON.stringify({ shown: shown, kept: kept, edited: CURSTYLE.back_step_after }));
"""


def test_panel_shows_pair_value_and_keeps_key_unset(tmp_path):
    """5. Панель стиля (боевой 94-stylepanel.js под node): без ключа поле показывает
    значение back_step, круг «поля ↔ стиль» ключ не заводит, а правка поля — заводит."""
    if not shutil.which("node"):
        pytest.skip("контракт панели требует node в PATH")
    from tests.test_style_panel_js import DOM_STUB, _run_node
    res = _run_node(tmp_path, "panel_back_after.js", DOM_STUB + PANEL_FALLBACK)
    assert res["shown"] == 40, f"поле не показало значение пары back_step: {res['shown']!r}"
    assert res["kept"] is None, "панель завела ключ, которого в стиле не было"
    assert res["edited"] == 0.8, f"правка поля не доехала до стиля: {res['edited']!r}"
