# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Интро «большое слева» (задание ZY).

Строка интро с флагом `big` встаёт СЛЕВА крупно, остальные строки группы — стопкой
СПРАВА от неё, выровненные по левому краю. Кегль большой строки подобран под высоту
блока, низ блока — БАЗОВАЯ линия последней строки стопки (большая стоит на ней же),
верх блока — капитель (высота заглавных) первой строки стопки: вертикаль типографская,
по заглавным и базовой линии, а не по чернилам (правка ZY — хвост «Ц» в «ЗА МЕСЯЦ»
утаскивал «8» вниз). Блок центрирован по кадру.

Раскладку считает Python (`intro_big_layout`, `core/xml2ae/layout.py`), в .jsx уезжают
готовые числа: INTRO_LX (левый край строки, px прекомпа от центра), INTRO_LK (множитель
кегля) и INTRO_LY (Y базовой линии). Превью читает те же числа из плана.

Доработка ZY-2 добавляет две ручки: intro_big_step — шаг СТОПКИ, % от обычного шага
интро (160 px), от общего межстрочного не зависит; intro_big_over — высота большого
слова, % от высоты стопки (100 — верх капители большой в верх блока, дефолт 110 — на
десятую выше).

Здесь:
  1. `intro_big_layout` на живом шрифте стиля по умолчанию: низ большой = базовая линия
     последней строки стопки, верх капители большой = верх блока при over=100 (по капители
     первой строки, ±0.5 px), кейс с хвостом «Ц» низ не двигает, lx стопки один, зазор до
     большого слова — ровно из ручки, блок центрирован;
  2. `scene_plan`: у группы с большой строкой в плане есть lx/lk/ys длины 3, lk[0] > 1,
     у строк стопки lk пустой, в самих строках lx/lk нет (INTRO_GROUPS прежний);
  3. нет big ни у одной строки (и big у одинокой строки группы) — .jsx побайтово прежний
     (сверка с эталоном tests/fixtures/golden_geometry.jsx);
  4. собранный .jsx ПРОГОНЯЕТСЯ в node с заглушками AE: INTRO_LX/INTRO_LK/INTRO_LY
     объявлены, левый край первого слова строки стопки = IW/2 + lx, масштаб слоя
     большой строки = lk*100, строка стопки скейла не получает;
  5. ручки intro_big_gap, intro_big_step и intro_big_over: подписи, диапазоны, дефолты и
     перевод en (ZY-2), шаг стопки — только у группы с большой строкой, высота большой —
     от высоты стопки при неподвижной базовой линии; плюс замер дефолтов: шаг стопки /
     cap первой строки на шрифте фикстуры (Oswald-Regular), числами (задание MQ);
  6. двери: все семь копий маппинга строки интро (60-preview.js, 90-ae.js) прогоняются в
     node на строке с `big` — флаг обязан доехать (флаг терялся в любой не тронутой копии);
  7. node: introGroupWindows проносит lx/lk, превью сажает строку по плану
     (left = центр + lx в px превью, без translateX(-50%), кегль × lk) и раскладывает
     группу абсолютно даже без ys;
  8. масштаб появления (задание MG) — ключи ОТ БАЗЫ слоя: база = Scale, выставленный до
     анимации (большая — lk*100, задний план — BACK_SCALE*100, обычная — 100), ключи
     база*0.7 → база; у ролика без большой строки ветка прежняя (абсолютные 70→100).

Шрифт в сборках намеренно несуществующий (`TestInk-Regular`): метрики берутся из
запасной ветки раскладки, и числа не зависят от того, какие шрифты стоят на машине.
Приёмочные тесты геометрии (1 и 5г) собирают стиль на шрифте ФИКСТУРЫ —
`tests/fixtures/fonts/Oswald-Regular.ttf` (SIL OFL 1.1, кириллица, фикстура
`fixture_font`): раньше они пропускались, если на машине нет шрифта стиля по умолчанию,
и в CI геометрия «большого слева» не проверялась вовсе (задание MQ).
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

from core import fonts, style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import (INTRO_BIG_OVER, INTRO_LINE_STEP,  # noqa: E402
                                intro_big_layout, intro_line_ys)
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402
from tests.test_intro_preview_ys import (DOM_SIM, _ipvintro_region, _js_src,  # noqa: E402
                                         _run_node, node)

PS = "TestInk-Regular"          # шрифта с таким именем нет — см. докстринг
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер фикстуры: 1-я секунда кам1, 8-я — перебивка

# Пример владельца: большое «8» слева, справа стопка «КИЛО» / «ЗА МЕСЯЦ».
BIG_INTRO = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["КИЛО"], "color": "white", "times": [T_CAM1 + 0.3]},
    {"words": ["ЗА МЕСЯЦ"], "color": "white", "times": [T_CAM1 + 0.6]},
]


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


def _plan(xml, intro, style=None, splits=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False, intro=intro,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS), emit=lambda *a, **k: None)


def _build_intro(xml, tmp_path, intro, style=None, splits=None, name="intro.jsx", mode="word"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=(splits or []),
                                   style=dict(style or {}, font=PS),
                                   intro_mode=mode,
                                   disclaimer="", intro_riser=False,
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


# ---- 1. раскладка: кегль большой под высоту блока, базовая линия и зазор -------------------

def _cap(ps, size):
    """Капитель по правилу правки ZY: высота заглавных («H»), нет шрифта/глифа — 0.72 кегля."""
    ext = fonts.ink_extent(ps, "H", size)
    return ext[0] if ext else 0.72 * size


def test_big_layout_caps_baseline_gap_and_center(fixture_font):
    """1. intro_big_layout на РЕАЛЬНОМ шрифте — шрифте фикстуры (Oswald-Regular).

    Шрифт едет вместе с тестами, поэтому тест не пропускается ни на чьей машине и в CI;
    каталог системных шрифтов на время теста подменён каталогом фикстуры, так что числа
    не зависят от установленного окружения.

    Вертикаль типографская (правка ZY): низ большой строки — БАЗОВАЯ линия последней
    строки стопки (y_big == y_last), верх её капители — верх блока, посчитанный по
    капители первой строки стопки: y_big − cap(big) == y_first − cap(first), ±0.5 px.
    Здесь раскладка зовётся с over=100 (высота большого = высоте стопки): именно при нём
    верх блока и верх большой совпадают по определению, а ручку высоты (100/120 и дефолт
    110) проверяет отдельный тест 5.
    Хвост «Ц» в «ЗА МЕСЯЦ» (ниже базовой линии) низ не двигает — на этом кейс отдельно.
    lx всех строк стопки равны, зазор между большим словом и стопкой — из ручки (±0.5),
    блок центрирован (lx большой = −total/2).
    """
    ps = fixture_font

    fsize, back_scale, gap, h = 140.0, 0.69, 40.0, 1920.0
    lines = [{"words": ["8"], "big": True}, {"words": ["КИЛО"]}, {"words": ["ЗА", "МЕСЯЦ"]}]
    fonts_ps = [ps] * len(lines)
    ys_stack = intro_line_ys(lines[1:], 0.65, True, "center", h)   # большая шаг не занимает
    lx, lk, ys = intro_big_layout(lines, ys_stack, fsize, fonts_ps, back_scale, gap, 100.0)

    assert len(lx) == len(lk) == len(ys) == 3
    assert lk[0] is not None and lk[0] > 1, "кегль большой строки не подобран"
    assert lk[1] is None and lk[2] is None, "строкам стопки множитель кегля не положен"
    assert ys[1:] == [round(v, 2) for v in ys_stack], "Y строк стопки разошлись с раскладкой"

    # низ большой — базовая линия последней строки стопки (y_big == y_last), кегль — под
    # высоту блока, верх блока — капитель первой строки стопки (здесь обе строки обычного
    # кегля, lk — множитель кегля большой)
    y_last = round(float(ys_stack[-1]), 2)
    assert ys[0] == y_last == ys[2], (
        f"низ большой строки {ys[0]} не на базовой линии последней строки стопки {y_last}")
    cap_first, cap_big = _cap(ps, fsize), _cap(ps, fsize)
    assert abs((ys[0] - cap_big * lk[0]) - (ys[1] - cap_first)) <= 0.5, (
        "верх капители большой строки не совпал с верхом блока: "
        f"{ys[0] - cap_big * lk[0]} != {ys[1] - cap_first}")

    # Кейс «хвост»: последняя строка стопки — «МЕСЯЦ», у «Ц» хвост НИЖЕ базовой линии,
    # первая — строка заднего плана, её капитель считается своим кеглем (fsize·back_scale).
    # Низ большой всё равно базовая линия: старая формула по чернилам поставила бы её
    # на desc_l ниже (чернила «8» кончаются на базовой линии).
    tail = [{"words": ["8"], "big": True},
            {"words": ["КИЛО"], "back": True},
            {"words": ["МЕСЯЦ"]}]
    ys_tail = intro_line_ys(tail[1:], 0.65, True, "center", h)
    _lx_t, lk_t, ys_t = intro_big_layout(tail, ys_tail, fsize, [ps] * 3, back_scale, gap, 100.0)
    _a_l, desc_l = fonts.ink_extent(ps, "МЕСЯЦ", fsize)
    assert desc_l > 1.0, "у «МЕСЯЦ» нет хвоста — кейс ничего не проверяет"
    assert ys_t[0] == round(float(ys_tail[-1]), 2) == ys_t[2], (
        f"низ большой строки уехал на хвост «Ц»: {ys_t[0]} != {ys_t[2]}")
    cap_first_t, cap_big_t = _cap(ps, fsize * back_scale), _cap(ps, fsize)
    assert abs((ys_t[0] - cap_big_t * lk_t[0]) - (ys_t[1] - cap_first_t)) <= 0.5, (
        "верх капители большой строки не совпал с верхом блока, где первая строка — задний план")
    desc_b = fonts.ink_extent(ps, "8", fsize * lk_t[0])[1]
    assert abs((ys_t[0] + desc_b) - (ys_t[2] + desc_l)) > 1.0, "«8» всё ещё висит на хвосте «Ц»"

    big_w = fonts.text_width(ps, "8", fsize * lk[0])
    stack_w = max(fonts.text_width(ps, "КИЛО", fsize), fonts.text_width(ps, "ЗА МЕСЯЦ", fsize))
    assert lx[1] == lx[2], "строки стопки выровнены не по одному левому краю"
    assert abs((lx[1] - (lx[0] + big_w)) - gap) <= 0.5, (
        f"зазор до большого слова: {lx[1] - (lx[0] + big_w)} != {gap}")
    total = big_w + gap + stack_w
    assert abs(lx[0] - (-total / 2)) <= 0.5, "блок не центрирован: lx большой != −total/2"


# ---- 2. план: lx/lk/ys у группы с большой строкой ------------------------------------------

def test_scene_plan_group_has_lx_lk_ys(xml_subs):
    """2. scene_plan: в plan.intro[g] есть lx/lk/ys длины 3, lk[0] > 1, lk[1:] пустые.

    В сами строки (lines) lx/lk не кладутся: они уезжают в .jsx как INTRO_GROUPS и
    обязаны остаться прежними (флаг big — единственное новое поле строки).
    """
    plan = _plan(xml_subs, BIG_INTRO)
    assert len(plan["intro"]) == 1
    grp = plan["intro"][0]
    for key in ("lx", "lk", "ys"):
        assert key in grp, f"в плане группы нет {key}"
        assert len(grp[key]) == 3, f"{key} не на все строки: {grp[key]}"
    assert grp["lk"][0] > 1, "множитель кегля большой строки не больше единицы"
    assert grp["lk"][1] is None and grp["lk"][2] is None
    assert grp["lx"][1] == grp["lx"][2], "строки стопки в плане стоят не по одному краю"
    assert grp["lx"][0] < grp["lx"][1], "большая строка не слева от стопки"
    assert grp["ys"][0] != grp["ys"][1], "Y большой строки не отличима от Y стопки"

    for ln in grp["lines"]:
        assert "lx" not in ln and "lk" not in ln, "lx/lk уехали в строки (INTRO_GROUPS)"
    assert grp["lines"][0]["big"] is True, "флаг big не доехал до строки"
    assert "big" not in grp["lines"][1] and "big" not in grp["lines"][2], (
        "big выставлен строкам без флага")


# ---- 3. без большой строки .jsx прежний ----------------------------------------------------

def test_no_big_jsx_matches_golden(xml_subs, tmp_path):
    """3. Нет big ни у одной строки — .jsx побайтово прежний (эталон main).

    Сверяется и полная сборка со стилем по умолчанию (эталон fixtures/golden_geometry.jsx),
    и сборка с интро: поле big=false — то же, что поля нет вовсе.
    """
    jsx = _mask_assets(_build(xml_subs, tmp_path, style={}))
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert jsx == golden, "сборка со стилем по умолчанию разошлась с эталоном"

    intro = [dict(w) for w in BIG_INTRO]
    intro_no_flag = [dict(w) for w in BIG_INTRO]
    intro_no_flag[0].pop("big")
    intro[0] = {"words": ["8"], "color": "white", "times": [T_CAM1], "big": False}
    with_field = _build_intro(xml_subs, tmp_path, intro, name="nobig1.jsx")
    without = _build_intro(xml_subs, tmp_path, intro_no_flag, name="nobig2.jsx")
    assert "INTRO_LX" not in with_field and "INTRO_LK" not in with_field
    assert "big" not in with_field, "big=false всё-таки уехал в .jsx"
    assert with_field == without, "big=false разошёлся со сборкой без поля"

    # big у ОДИНОКОЙ строки группы — флаг без эффекта: раскладывать не с чем, поэтому
    # ни INTRO_LX, ни INTRO_LY не появляются, а .jsx отличается ТОЛЬКО самим флагом строки
    lone = [{"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
            {"words": ["КИЛО"], "color": "white", "times": [T_CAM2]}]
    lone_no_flag = [dict(lone[0]), dict(lone[1])]
    lone_no_flag[0].pop("big")
    lone_jsx = _build_intro(xml_subs, tmp_path, lone, splits=[1], name="lone.jsx")
    plain_jsx = _build_intro(xml_subs, tmp_path, lone_no_flag, splits=[1], name="plain.jsx")
    assert "INTRO_LX" not in lone_jsx and "INTRO_LK" not in lone_jsx
    assert "INTRO_LY" not in lone_jsx, "одинокая большая строка поменяла раскладку группы"
    assert lone_jsx.replace(',"big":true', "") == plain_jsx, (
        "big у одинокой строки группы изменил .jsx не только флагом строки")


# ---- 4. .jsx в node: X слова стопки и масштаб большой строки -------------------------------

_NODE_STUB = r"""
const assert = require('assert');

// ---- заглушки After Effects: только то, что трогает блок интро ----
var _textLayers = [];
function makeProp(){
  const p = { keys: [], value: undefined,
    setValue: function(v){ p.value = v; p.keys.push([null, v]); },
    setValueAtTime: function(t, v){ p.keys.push([t, v]); } };
  return p;
}
function makeLayer(name){
  const lay = { name: name, props: {}, text: '' };
  lay.prop = function(pn){ if(!lay.props[pn]) lay.props[pn] = makeProp(); return lay.props[pn]; };
  lay.property = function(pn){
    if(pn === 'ADBE Text Properties'){
      const dd = { resetCharStyle: function(){}, resetParagraphStyle: function(){},
                   text: '', fontSize: 0, fillColor: [1,1,1], applyFill: false,
                   fauxBold: false, justification: 0 };
      return { property: function(){ return { get value(){ return dd; },
               setValue: function(v){ lay.text = v.text; } }; } };
    }
    if(pn === 'ADBE Transform Group') return { property: function(n){ return lay.prop(n); } };
    if(pn === 'ADBE Effect Parade') return { addProperty: function(mn){ return makeLayer(mn); } };
    return lay.prop(pn);
  };
  // ширина слоя слова: одинаковая у всех, чтобы арифметика теста была проверяемой
  lay.sourceRectAtTime = function(){ return { width: 200 }; };
  lay.remove = function(){ lay._removed = true; };
  return lay;
}
var ParagraphJustification = { CENTER_JUSTIFY: 1 };
function easePair(){}
function dropShadow(){}
function setFont(){}
function toBin(){}
function addFX(L, mn){ return makeLayer(mn); }
function setP(){}
var introNull = null, introNull2 = null;
var main = { layers: { add: function(ic){ return makeLayer(ic.name); } } };
var app = { project: { items: { addComp: function(nm, w, h, par, dur, fps){
  const ic = makeLayer(nm); ic.name = nm; ic.width = w;
  ic.layers = { addText: function(){ const L = makeLayer(''); _textLayers.push(L); return L; } };
  return ic;
} } } };
"""


def _jsx_decls(jsx):
    """Объявления шаблона, нужные блоку интро: значения — данные (числа/строки/массивы).

    BACK_SHADOW_OP — вторая строка объявления автотени (`intro_shadow_decl`): она
    появляется вместе с INTRO_SHADOW_*, когда в сборке есть строка заднего плана;
    HL_RISE/HL_DUR — анимации строк up/count (slide-up).
    """
    out = []
    for ln in jsx.splitlines():
        s = ln.strip()
        if s.startswith("var ") and s[4:].startswith(
                ("HL_BOLD", "HL_RISE", "FONT_SIZE", "HL_FILL", "W=", "INTRO_",
                 "BACK_SHADOW_OP")):
            out.append(s)
    assert any(x.startswith("var INTRO_GROUPS") for x in out), "INTRO_GROUPS не нашлись"
    return "\n".join(out)


def _intro_region(jsx):
    i = jsx.index("    // ---- интро-текст")
    j = jsx.index("    // ---- авто-ротоскоп")
    assert j > i
    return jsx[i:j]


@node
def test_jsx_node_big_word_leads_stack(xml_subs, tmp_path):
    """4. Прогон собранного .jsx в node: INTRO_LX/INTRO_LK/INTRO_LY объявлены, левый
    край первого слова строки стопки = IW/2 + lx, масштаб слоя большой строки = lk*100.

    Числа для сверки берутся из ПЛАНА (scene_plan) — .jsx обязан применить ровно то,
    что посчитал Python; INTRO_LX/INTRO_LK из самого .jsx сверяются с планом.
    """
    jsx = _build_intro(xml_subs, tmp_path, BIG_INTRO)
    assert "var INTRO_LX=" in jsx and ", INTRO_LK=" in jsx, "массивов большого слова нет"
    assert "var INTRO_LY=" in jsx, "INTRO_LY не объявлен (Y большой строки неоткуда взять)"

    plan = _plan(xml_subs, BIG_INTRO)["intro"][0]
    script = (
        _NODE_STUB
        + "\n" + _jsx_decls(jsx)
        + "\n" + _intro_region(jsx)
        + "\n" + """
// ---- проверки ----
const PLX = @PLX@, PLK = @PLK@, PYS = @PYS@;
assert.deepStrictEqual(INTRO_LX[0], PLX, 'INTRO_LX разошлись с планом');
assert.deepStrictEqual(INTRO_LK[0], PLK, 'INTRO_LK разошлись с планом');
assert.deepStrictEqual(INTRO_LY[0], PYS, 'INTRO_LY разошлись с планом');

const IWc = Math.round(W*INTRO_WIDE);
const bigL = _textLayers.filter(function(L){ return !L._removed && L.text === '8'; })[0];
const stackL = _textLayers.filter(function(L){ return !L._removed && L.text === 'КИЛО'; })[0];
assert(bigL, 'слой большого слова не создан');
assert(stackL, 'слой первой строки стопки не создан');

// большая строка: левый край слова на IW/2+lx, скейл слоя = lk*100
const bScale = bigL.props['ADBE Scale'].value[0];
assert(Math.abs(bScale - PLK[0]*100) < 0.06,
  'масштаб слоя большой строки: ' + bScale + ' != ' + (PLK[0]*100));
const bLeft = bigL.props['ADBE Position'].value[0] - 200*PLK[0]/2;
assert(Math.abs(bLeft - (IWc/2 + PLX[0])) < 0.01,
  'левый край большого слова: ' + bLeft + ' != ' + (IWc/2 + PLX[0]));

// строка стопки: левый край первого слова на IW/2+lx, скейла у неё нет
const sLeft = stackL.props['ADBE Position'].value[0] - 200/2;
assert(Math.abs(sLeft - (IWc/2 + PLX[1])) < 0.01,
  'левый край строки стопки: ' + sLeft + ' != ' + (IWc/2 + PLX[1]));
assert.strictEqual(stackL.props['ADBE Scale'], undefined,
  'строка стопки получила масштаб слоя (её кегль задаёт lk только у большой)');

console.log("OK: big word leads the stack");
"""
        .replace("@PLX@", json.dumps(plan["lx"]))
        .replace("@PLK@", json.dumps(plan["lk"]))
        .replace("@PYS@", json.dumps(plan["ys"]))
    )
    res = _run_node(tmp_path, "test_intro_big.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: big word leads the stack" in res.stdout


# ---- 5. ручки ZY / ZY-2: зазор, шаг стопки и высота большого слова --------------------------

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


def test_knob_intro_big_gap_moves_lx(xml_subs, tmp_path):
    """5. Ручка intro_big_gap (0…400, дефолт 40) меняет lx и в плане, и в .jsx."""
    field = _schema_field("intro_big_gap")
    assert field, "в схеме стиля нет ручки intro_big_gap"
    assert field["ctl"] == "num" and field["label"] == "Зазор до большого слова, px"
    assert (field["min"], field["max"], field["step"]) == (0, 400, 1)
    assert styles.BASE["intro_big_gap"] == 40, "дефолт ручки уехал"

    p40 = _plan(xml_subs, BIG_INTRO, style={"intro_big_gap": 40})["intro"][0]
    p160 = _plan(xml_subs, BIG_INTRO, style={"intro_big_gap": 160})["intro"][0]
    assert p40["lx"] != p160["lx"], "intro_big_gap не двигает lx в плане"
    # зазор вырос на 120: блок шире на 120, левый край большой уехал на −60, стопка на +60
    assert abs((p160["lx"][0] - p40["lx"][0]) + 60) <= 0.05
    assert abs((p160["lx"][1] - p40["lx"][1]) - 60) <= 0.05
    assert p160["lk"] == p40["lk"], "зазор не должен менять кегль большой строки"

    j40 = _build_intro(xml_subs, tmp_path, BIG_INTRO, style={"intro_big_gap": 40}, name="g40.jsx")
    j160 = _build_intro(xml_subs, tmp_path, BIG_INTRO, style={"intro_big_gap": 160}, name="g160.jsx")
    assert "INTRO_LX=" + json.dumps([p40["lx"]], separators=(",", ":")) in j40
    assert "INTRO_LX=" + json.dumps([p160["lx"]], separators=(",", ":")) in j160, (
        "ручка не доехала до .jsx")


# Разметка доработки ZY-2: ПЕРВАЯ группа — с большой строкой (стопка из двух строк), вторая
# группа обычная (две строки) и стоит на перебивке: по ней и видно, что ручка шага стопки
# чужие группы не трогает.
BIG_TWO_GROUPS = [dict(x) for x in BIG_INTRO] + [
    {"words": ["ПРОСТО"], "color": "white", "times": [T_CAM2]},
    {"words": ["СТРОКА"], "color": "white", "times": [T_CAM2 + 0.5]},
]
BIG_TWO_SPLITS = [3]


def test_knobs_intro_big_step_and_over_schema():
    """5а. Обе ручки ZY-2 в схеме: подписи, диапазоны и дефолты — как заказано, перевод en
    на месте, а дефолт раскладки не разъехался с дефолтом ручки."""
    f_step = _schema_field("intro_big_step")
    assert f_step, "в схеме стиля нет ручки intro_big_step"
    assert f_step["ctl"] == "num" and f_step["label"] == "Межстрочный стопки, %"
    assert (f_step["min"], f_step["max"], f_step["step"]) == (30, 200, 1)
    assert styles.BASE["intro_big_step"] == 80, "дефолт ручки intro_big_step уехал"

    f_over = _schema_field("intro_big_over")
    assert f_over, "в схеме стиля нет ручки intro_big_over"
    assert f_over["ctl"] == "num" and f_over["label"] == "Большое выше стопки, %"
    assert (f_over["min"], f_over["max"], f_over["step"]) == (80, 160, 1)
    assert styles.BASE["intro_big_over"] == 110, "дефолт ручки intro_big_over уехал"
    assert INTRO_BIG_OVER == styles.BASE["intro_big_over"], (
        "дефолт раскладки разъехался с дефолтом ручки intro_big_over")

    en = json.loads(open(os.path.join(ROOT, "static", "i18n", "en.json"),
                         encoding="utf-8").read())
    assert en["Межстрочный стопки, %"] == "Stack line spacing, %"
    assert en["Большое выше стопки, %"] == "Big word above stack, %"
    assert f_step["tip"] in en and f_over["tip"] in en, "тултипы ручек без перевода"


def test_knob_intro_big_step_scales_stack_only(xml_subs):
    """5б. Ручка intro_big_step меняет шаг СТОПКИ у группы с большой строкой; обычная группа
    той же сборки остаётся с теми же ys, а общий межстрочный в стопку не заезжает.

    Шаг стопки = 160 × intro_big_step/100: дефолтные 80 % — 128 px, 200 % — 320 px.
    """
    g80 = _plan(xml_subs, BIG_TWO_GROUPS, style={"intro_big_step": 80},
                splits=BIG_TWO_SPLITS)["intro"]
    g200 = _plan(xml_subs, BIG_TWO_GROUPS, style={"intro_big_step": 200},
                 splits=BIG_TWO_SPLITS)["intro"]
    assert len(g80) == len(g200) == 2, "фикстура не разбилась на две группы интро"
    big80, big200, plain80, plain200 = g80[0], g200[0], g80[1], g200[1]

    step80 = big80["ys"][2] - big80["ys"][1]
    step200 = big200["ys"][2] - big200["ys"][1]
    assert abs(step80 - 128.0) <= 0.01, f"дефолтный шаг стопки не 160·0.8: {step80}"
    assert abs(step200 - 320.0) <= 0.01, f"шаг стопки при 200 % не 320: {step200}"
    assert big80["ys"][0] == big80["ys"][2], "большая не на базовой линии последней строки стопки"
    assert big200["ys"][0] == big200["ys"][2], "то же при 200 %"
    assert plain80["ys"] == plain200["ys"], (
        "intro_big_step сдвинул обычную группу без большой строки: "
        f"{plain80['ys']} != {plain200['ys']}")

    # Общий межстрочный стопку с большой не двигает — у неё своя ручка шага.
    g_line = _plan(xml_subs, BIG_TWO_GROUPS, style={"intro_line_step": 200},
                   splits=BIG_TWO_SPLITS)["intro"]
    assert g_line[0]["ys"] == big80["ys"], (
        "intro_line_step поехал в стопку группы с большой строкой")
    assert g_line[1]["ys"] != plain80["ys"], (
        "intro_line_step не двигает обычную группу — фикстура ничего не проверяет")


def test_knob_intro_big_over_moves_top_only(xml_subs):
    """5в. Ручка intro_big_over: 100 — верх капители большой в верх блока (±0.5); 120 —
    высота большой = 1.2 высоты стопки (±0.5); базовая линия при этом не двигается.

    Считается по плану (шрифт сборок несуществующий, метрики — запасная ветка раскладки),
    высота стопки — от верха заглавных первой строки до базовой линии последней.
    """
    plan100 = _plan(xml_subs, BIG_INTRO, style={"intro_big_over": 100})
    plan120 = _plan(xml_subs, BIG_INTRO, style={"intro_big_over": 120})
    p100, p120 = plan100["intro"][0], plan120["intro"][0]
    fsize = float(plan100["intro_fsize"])

    cap_first = _cap(PS, fsize)                       # первая строка стопки — обычный кегль
    top = p100["ys"][1] - cap_first                   # верх блока
    box_h = p100["ys"][2] - top                       # высота стопки
    assert abs((p100["ys"][0] - _cap(PS, fsize * p100["lk"][0])) - top) <= 0.5, (
        "при over=100 верх капители большой не совпал с верхом блока")
    assert abs(_cap(PS, fsize * p120["lk"][0]) - 1.2 * box_h) <= 0.5, (
        "при over=120 высота большой не 1.2 высоты стопки")
    assert abs(p120["lk"][0] / p100["lk"][0] - 1.2) <= 0.01, "кегль большой не вырос в 1.2"

    assert p100["ys"] == p120["ys"], "ручка высоты сдвинула базовые линии строк"
    assert p120["ys"][0] == p120["ys"][2], "большая строка не на базовой линии стопки"

    # Дефолт ручки — 110: то же, что явные 110, и на десятую выше стопки.
    p_def = _plan(xml_subs, BIG_INTRO)["intro"][0]
    p110 = _plan(xml_subs, BIG_INTRO, style={"intro_big_over": 110})["intro"][0]
    assert p_def["lk"] == p110["lk"], "дефолт раскладки не 110 %"
    assert abs(_cap(PS, fsize * p_def["lk"][0]) - 1.1 * box_h) <= 0.5, (
        "дефолтное большое слово не на 10 % выше стопки")


def test_default_big_step_ratio_to_cap(fixture_font, xml_subs):
    """5г. Дефолты числом на шрифте фикстуры (Oswald-Regular): шаг стопки 128 px,
    капитель «H» 113.4 px, отношение шага к капители 1.1287.

    Шаг стопки — разность ys двух соседних строк стопки, капитель — «H» первой строки
    стопки из файла шрифта. Числа зафиксированы явно: раньше тест брал шрифт стиля по
    умолчанию и пропускался, если его нет (в CI — всегда), а отношение считалось по
    формуле, которую сам же и проверял.

    Эталон владельца — шрифт стиля по умолчанию (SFPro-CondensedSemibold): там та же
    ручка даёт ≈1.30 при коридоре 1.2…1.35. У Oswald капитель выше (0.81 кегля против
    0.726), поэтому то же число шага даёт 1.1287 — ВНЕ коридора. Дефолт ручки
    (intro_big_step=80) при этом не подгоняется: шаг 160×80 % от шрифта не зависит, а
    коридор — свойство конкретного шрифта; здесь он выписан числом, а не подменён.
    """
    ps = fixture_font
    plan = xml2ae.scene_plan(xml_subs, disclaimer="", intro_riser=False, intro=BIG_INTRO,
                             intro_splits=[],
                             style={"font": ps, "intro_font": ps},
                             emit=lambda *a, **k: None)
    grp = plan["intro"][0]
    fsize = float(plan["intro_fsize"])
    step = grp["ys"][2] - grp["ys"][1]
    cap = _cap(ps, fsize)
    ratio = step / cap

    assert fsize == 140.0, "кегль интро фикстуры уехал: %r" % fsize
    assert abs(step - 128.0) <= 0.01, (
        f"дефолтный шаг стопки не 160·{styles.BASE['intro_big_step']} %: {step}")
    assert abs(step - INTRO_LINE_STEP * styles.BASE["intro_big_step"] / 100.0) <= 0.01
    assert cap == pytest.approx(113.4, abs=0.01), "капитель «H» Oswald уехала: %r" % cap
    assert ratio == pytest.approx(1.1287, abs=0.001), (
        f"шаг стопки / cap = {ratio:.4f} при intro_big_step={styles.BASE['intro_big_step']}")


# ---- 6. двери: флаг не теряется по пути ----------------------------------------------------

DOORS_SRC = ("static/app/60-preview.js", "static/app/90-ae.js")
# 5 копий в 90-ae.js. В 60-preview.js их было две — в панели слов предпросмотра
# нарезки (pvwOpen и pvwCommitIntro); панель удалена вместе с контейнерами, которые
# она рисовала, её копии ушли. Общих функций разметки строк это не касается:
# introRowHtml/introToggleCount живут здесь же и проверяются отдельно.
DOORS_TOTAL = 5


def _enclosing_object(src, pos):
    """Начало объектного литерала `{`, внутри которого стоит позиция pos.

    Скобки внутри строк в этих объектах не встречаются, поэтому баланс считается по
    символам: назад — до первой `{` на нулевой вложенности."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        ch = src[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                assert ch == "{", "строка интро не объектный литерал: %r" % src[i:i + 40]
                return i
            depth -= 1
    raise AssertionError("не нашлось начало объекта строки интро")


def _object_end(src, start):
    """Позиция закрывающей `}` объекта, открытого в start."""
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    raise AssertionError("объект строки интро не закрылся")


def _intro_row_doors():
    """Копии маппинга строки интро: [{file, line, src}] — по каждой `back:!!r.back`.

    Строка интро едет из джоба в состояние и обратно семь раз, и в каждой копии флаг
    `big` надо пронести руками. Считаем не литералы, а сами объекты: текст найденного
    литерала уходит в node и исполняется (см. тест 6).
    """
    doors = []
    for rel in DOORS_SRC:
        src = open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()
        for m in re.finditer(r"back:!!r\.back", src):
            i = _enclosing_object(src, m.start())
            doors.append({"file": rel, "line": src[:m.start()].count("\n") + 1,
                          "src": src[i:_object_end(src, i) + 1]})
    return doors


# Стенд: строка интро с `big` и `back`, имена, которые читает объект из 90-ae.js (там
# маппинг живёт внутри функции разбора строк ИИ). Каждая копия ИСПОЛНЯЕТСЯ — тест падает
# на ReferenceError, если копия вдруг начнёт читать что-то ещё.
_DOOR_STAND = r"""
const assert = require('assert');
const r = {count: 1, color: 'white', fill: [1, 1, 1], anim: 'up', fx: 'glow', dec: 0,
           is_count: false, cnt_words: ['A'], break: false, from: null, gx: 10, gy: 20,
           gs: 100, accent: false, back: true, big: true, words: ['A'], times: [0.5],
           text: 'A', sel: false};
const head = true, decVal = 0, ws = ['A'], ts = [0.5];
const doors = @DOORS@;
assert.strictEqual(doors.length, @TOTAL@,
  'копий маппинга строки интро не @TOTAL@: ' + doors.map(function(d){ return d.file; }).join(', '));
const lost = [];
for (const d of doors) {
  const make = new Function('r', 'head', 'decVal', 'ws', 'ts',
                            'return (' + d.src + ');');
  const out = make(r, head, decVal, ws, ts);
  if (out.big !== true) lost.push(d.file + ':' + d.line + ' (big)');
  if (out.back !== true) lost.push(d.file + ':' + d.line + ' (back)');
  if (out.big === true && out.anim !== 'up') lost.push(d.file + ':' + d.line + ' (чужие поля)');
}
assert.deepStrictEqual(lost, [],
  'флаг строки интро не доехал в копиях: ' + lost.join(', '));
console.log('OK: big arrives through every intro row door (' + doors.length + ')');
"""


@node
def test_back_flag_doors_have_big_twin(tmp_path):
    """6. Двери: каждая из семи копий маппинга строки интро ПРОГОНЯЕТСЯ в node на строке
    с `big:true` — флаг обязан доехать до результата.

    Раньше сторож сравнивал число вхождений двух литералов (`back:!!r.back` и
    `big:!!r.big`): копия без обоих флагов или флаг, уехавший в чужое поле, проходили
    молча. Теперь исполняется сам объект копии — как фронтовые функции в тесте 7.
    """
    doors = _intro_row_doors()
    assert len(doors) == DOORS_TOTAL, (
        "копий маппинга строки интро не %d: %s"
        % (DOORS_TOTAL, ", ".join("%s:%d" % (d["file"], d["line"]) for d in doors)))

    script = (_DOOR_STAND
              .replace("@DOORS@", json.dumps(doors, ensure_ascii=False))
              .replace("@TOTAL@", str(DOORS_TOTAL)))
    res = _run_node(tmp_path, "test_big_doors.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: big arrives through every intro row door" in res.stdout


# ---- 7. превью: окна проносят lx/lk, строка садится по плану --------------------------------

def _window_fn():
    src = _js_src()
    idx = src.find("function introGroupWindows(")
    assert idx >= 0, "introGroupWindows не найдена в 85-inserts-view.js"
    end = src.find("\nfunction ", idx + 10)
    return src[idx:end if end > 0 else len(src)]


@node
def test_preview_group_windows_pass_lx_lk(tmp_path):
    """7а. introGroupWindows проносит lx/lk группы из плана (без него ветка большого
    слова мертва — это дверь превью)."""
    checks = r"""
    const plan = [{
      ts: 1.0, te: 5.0, fade: 0.75, front: false,
      shadow: { fill: [1, 1, 1], op: 68 }, fonts: ['A', 'B'],
      ys: [880, 1040], lx: [100, -40], lk: [2.5, null],
      lines: [{ words: ['A'] }, { words: ['B'] }]
    }];
    const out = introGroupWindows(plan);
    assert.strictEqual(out.length, 1, 'окно группы потерялось');
    assert.deepStrictEqual(out[0].lx, [100, -40], 'lx не протащен: ' + JSON.stringify(out[0].lx));
    assert.deepStrictEqual(out[0].lk, [2.5, null], 'lk не протащен: ' + JSON.stringify(out[0].lk));
    assert.deepStrictEqual(out[0].ys, [880, 1040]);
    console.log("OK: introGroupWindows passes lx/lk");
    """
    script = "const assert = require('assert');\n" + _window_fn() + "\n" + checks
    res = _run_node(tmp_path, "test_big_win.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: introGroupWindows passes lx/lk" in res.stdout


@node
def test_preview_line_sits_by_plan_lx(tmp_path):
    """7б. Превью сажает строку по плану: left = центр контейнера + lx в px превью,
    translateX(-50%) снят (текст идёт вправо от левого края), кегль × lk у большой
    строки, а группа с lx раскладывается абсолютно даже без ys.

    Контейнер 540×960, план 1080×1920: k = 0.5, центр = 270.
    """
    intro = [{
        "ts": 1.0, "te": 5.0, "fade": 0.75, "front": False,
        "ys": [880.0, 1040.0],
        "lx": [100.0, -40.0], "lk": [2.5, None],
        "lines": [
            {"color": "white", "words": ["8"], "times": [1.0]},
            {"color": "white", "words": ["КИЛО"], "times": [1.3]},
        ],
    }]
    intro_no_ys = [{
        "ts": 1.0, "te": 5.0, "fade": 0.75, "front": False,
        "lx": [100.0, -40.0], "lk": [2.5, None],
        "lines": [
            {"color": "white", "words": ["8"], "times": [1.0]},
            {"color": "white", "words": ["КИЛО"], "times": [1.3]},
        ],
    }]
    checks = r"""
    IPV.intro = introGroupWindows(@INTRO@);
    IPV.introCur = -2;
    ipvIntro(1.5);
    const rows = ioEl.querySelectorAll('.iline');
    assert.strictEqual(rows.length, 2, 'строк должно быть две: ' + rows.length);
    for (const r of rows){
      assert(r._classes.has('abs'), 'строка группы с lx обязана быть абсолютной: ' + r.className);
    }
    // 270 + 100*0.5 = 320; 270 + (-40)*0.5 = 250
    assert.strictEqual(rows[0].style.left, '320.00px',
      'левый край большого слова: ' + rows[0].style.left);
    assert.strictEqual(rows[1].style.left, '250.00px',
      'левый край строки стопки: ' + rows[1].style.left);
    assert.strictEqual(rows[0].style.transform, 'none',
      'строка с lx осталась с translateX(-50%): ' + rows[0].style.transform);
    assert.strictEqual(rows[0].style.fontSize, 'calc(var(--introsfs,8.4cqw) * 2.5)',
      'кегль большой строки не из плана: ' + rows[0].style.fontSize);
    assert.strictEqual(rows[1].style.fontSize, undefined,
      'строке стопки поставили чужой кегль: ' + rows[1].style.fontSize);
    // вертикаль осталась плановой: 480 + (880-960)*0.5 - 20 = 420
    assert.strictEqual(rows[0].style.top, '420.00px',
      'базовая линия уехала: ' + rows[0].style.top);

    // группа с lx без ys: раскладка всё равно абсолютная (горизонталь знает план)
    IPV.intro = introGroupWindows(@INTRO_NO_YS@);
    IPV.introCur = -2;
    ipvIntro(1.5);
    const rows2 = ioEl.querySelectorAll('.iline');
    assert.strictEqual(rows2.length, 2);
    assert(rows2[0]._classes.has('abs'), 'без ys группа с lx осталась в потоке');
    assert.strictEqual(rows2[0].style.left, '320.00px');
    assert.strictEqual(rows2[0].style.top, undefined, 'без ys вертикаль ставить нечем');

    console.log("OK: preview line sits by plan lx");
    """
    script = (
        DOM_SIM.replace("@INTRO@", "[]")
        + "\n" + _ipvintro_region(_js_src())
        + "\n" + _window_fn()
        + "\n" + checks.replace("@INTRO_NO_YS@", json.dumps(intro_no_ys, ensure_ascii=False))
                .replace("@INTRO@", json.dumps(intro, ensure_ascii=False))
    )
    res = _run_node(tmp_path, "test_big_preview.js", script)
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: preview line sits by plan lx" in res.stdout


def test_template_keeps_big_layout_wiring(xml_subs, tmp_path):
    """Сторож шаблона: задний-план-скейл и back-ширина не применяются к большой строке
    (lk заменяет их), а скейл/ширина/x большой берутся из INTRO_LK/INTRO_LX."""
    assert "%(intro_big_qi_vars)s" in xml2ae.AE_FULL, "нет чтения bigK/bigX на строку"

    jsx = _build_intro(xml_subs, tmp_path, BIG_INTRO, name="wiring.jsx")
    assert "introBigScale(L2,bigK)" in jsx and "introBigScale(Ll,bigK)" in jsx, (
        "скейл большой строки не применяется к её слоям")
    assert "if(bigX!=null) x=IW/2+bigX;" in jsx, "пословный старт не следит за lx"
    assert re.search(r"setValue\(\[IW/2, lineY\]\); if\(bigX!=null\)\{", jsx), (
        "построчная позиция не поправляется на lx")
    # пословно ширина слова и всей строки множится на lk, как BACK_SCALE у заднего плана
    assert "if(bigK!=null) wpx*=bigK;" in jsx and "if(bigK!=null) lineW*=bigK;" in jsx

    # группа с большой строкой И строкой заднего плана: back-скейл и back-ширина к большой
    # не применяются — lk его заменяет
    intro_back = [dict(x) for x in BIG_INTRO] + [
        {"words": ["ФОН"], "color": "white", "back": True, "times": [T_CAM1 + 0.9]}]
    jsx_back = _build_intro(xml_subs, tmp_path, intro_back, name="wiring_back.jsx")
    assert "introBigK(gI,qi)==null" in jsx_back, "нет страховки «строка не большая»"


# ---- 8. масштаб появления — от базы слоя (задание MG) --------------------------------------

# Фикстура: большая «8» слева, строка стопки «КИЛО» и строка заднего плана «ФОН» — у всех
# одна и та же анимация, чтобы видеть, каких слоёв ветка introAnimFX касается.
MG_LINES = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["КИЛО"], "color": "white", "times": [T_CAM1 + 0.3]},
    {"words": ["ФОН"], "color": "white", "times": [T_CAM1 + 0.6], "back": True},
]
# "" — обычный фейд по Opacity (ветка else в introAnimFX); Scale слоя анимирует ТОЛЬКО
# reveal. Ветка else в сборке появляется, только если у строки есть fx="glow": без
# anim/fx/cnt introAnimFX в .jsx нет вовсе — тогда проверять нечего.
MG_ANIMS = ("", "up", "left", "right", "reveal", "glitch")


def _mg_intro(anim):
    """Строки фикстуры с одной и той же анимацией (фейд приходит с fx="glow")."""
    extra = {"fx": "glow"} if not anim else {}
    return [dict(x, anim=anim, **extra) for x in MG_LINES]


# Проверки для прогона в node: база слоя — Scale, выставленный ДО анимации, и ключи
# появления не должны её перебивать. @PLK@ — множитель кегля большой строки из плана,
# @BSC@ — BACK_SCALE из самой сборки, @ANIM@ — анимация строки.
_MG_SCALE_CHECKS = r"""
const PLK = @PLK@, BSC = @BSC@, ANIM = @ANIM@;
// база большой строки — lk*100 (introBigScale), заднего плана — BACK_SCALE*100 (introBackScale)
const bigBase = PLK*100, backBase = Math.round(BSC*1000)/10;
function scaleKeys(L){
  const p = L.props['ADBE Scale'];
  return p ? p.keys.filter(function(k){ return k[0] !== null; }).map(function(k){ return k[1][0]; }) : [];
}
function staticScale(L){
  const p = L.props['ADBE Scale'];
  return (p && p.value) ? p.value[0] : null;
}
function near(a, b){ return typeof a === 'number' && Math.abs(a - b) < 0.1; }
const bigL = _textLayers.filter(function(L){ return !L._removed && L.text === '8'; })[0];
const stackL = _textLayers.filter(function(L){ return !L._removed && L.text === 'КИЛО'; })[0];
// строка заднего плана едет строчными: регистр строки back правит Python (back_case)
const backL = _textLayers.filter(function(L){ return !L._removed && L.text === 'фон'; })[0];
assert(bigL && stackL && backL, 'слои строк не созданы: '
  + _textLayers.map(function(L){ return (L._removed ? '-' : '') + L.text; }).join(','));

// статичный Scale слоя база остаётся базой — анимация её не переписывает
assert(near(staticScale(bigL), bigBase),
  'база большой строки: ' + staticScale(bigL) + ' != ' + bigBase);
assert(near(staticScale(backL), backBase),
  'база заднего плана: ' + staticScale(backL) + ' != ' + backBase);

const bk = scaleKeys(bigL), sk = scaleKeys(stackL), ck = scaleKeys(backL);
if (ANIM === 'reveal'){
  assert(bk.length === 2 && near(bk[0], bigBase*0.7) && near(bk[1], bigBase),
    'ключи большого слова не от базы ' + bigBase + ': ' + JSON.stringify(bk));
  assert(sk.length === 2 && near(sk[0], 70) && near(sk[1], 100),
    'ключи строки стопки: ' + JSON.stringify(sk));
  assert(ck.length === 2 && near(ck[0], backBase*0.7) && near(ck[1], backBase),
    'ключи строки заднего плана: ' + JSON.stringify(ck));
} else {
  assert(bk.length === 0 && sk.length === 0 && ck.length === 0,
    'анимация "' + ANIM + '" тронула Scale: ' + JSON.stringify([bk, sk, ck]));
}
console.log('OK: reveal scale keys from base (' + (ANIM || 'fade') + ')');
"""


def _mg_node(tmp_path, jsx, anim, plk, mode="word"):
    """Прогон сборки в node стендом теста 4: масштаб появления — от базы слоя."""
    bsc = float(re.search(r"BACK_SCALE=([0-9.]+)", jsx).group(1))
    script = (
        _NODE_STUB
        + "\n" + _jsx_decls(jsx)
        + "\n" + _intro_region(jsx)
        + "\n" + _MG_SCALE_CHECKS
        .replace("@PLK@", repr(plk)).replace("@BSC@", repr(bsc))
        .replace("@ANIM@", json.dumps(anim))
    )
    tag = (anim or "fade") + ("_line" if mode == "line" else "")
    res = _run_node(tmp_path, f"test_mg_{tag}.js", script)
    assert res.returncode == 0, f"anim={anim!r} ({mode}): node failed: {res.stderr}\n{res.stdout}"
    assert f"OK: reveal scale keys from base ({anim or 'fade'})" in res.stdout


@node
def test_reveal_scale_keys_go_from_layer_base(xml_subs, tmp_path):
    """8. Ключи масштаба появления — ОТ БАЗЫ слоя, а не в абсолютных 70→100.

    Большое слово: последний ключ Scale = lk*100, первый = lk*100*0.7 (±0.1); строка
    стопки — 100 и 70; строка заднего плана — прежние числа (BACK_SCALE*100 и ×0.7).
    Проверяются ВСЕ анимации строки: Scale анимирует только reveal, у остальных ключей
    быть не должно (иначе они так же перебивали бы базу). Построчный режим — тот же
    introAnimFX, но на слое строки (introBigScale(Ll, bigK)).
    """
    plan = _plan(xml_subs, MG_LINES)["intro"][0]
    plk = plan["lk"][0]
    assert plk and plk > 1, "фикстура без большой строки — тест ничего не проверяет"

    for anim in MG_ANIMS:
        intro = _mg_intro(anim)
        jsx = _build_intro(xml_subs, tmp_path, intro, name=f"mg_{anim or 'fade'}.jsx")
        assert "var base=(sc && sc.value" in jsx, (
            f"anim={anim!r}: в .jsx нет ветки «масштаб от базы слоя»")
        _mg_node(tmp_path, jsx, anim, plk)

    jsx_line = _build_intro(xml_subs, tmp_path, _mg_intro("reveal"), name="mg_line.jsx",
                            mode="line")
    assert "introBigScale(Ll,bigK)" in jsx_line, "построчная сборка без скейла большой строки"
    _mg_node(tmp_path, jsx_line, "reveal", plk, mode="line")


def test_reveal_scale_no_big_keeps_absolute_branch(xml_subs, tmp_path):
    """8б. Ролик без большой строки: ветка масштаба появления прежняя (абсолютные 70→100),
    подстановки «от базы» в .jsx нет — сборка такого ролика не меняется вовсе."""
    intro = [
        dict(words=["ПЕРВОЕ"], color="white", times=[T_CAM1], anim="reveal"),
        dict(words=["СДО*НУТЬ"], color="white", times=[T_CAM2], back=True, anim="reveal"),
    ]
    jsx = _build_intro(xml_subs, tmp_path, intro, splits=[1], name="mg_nobig.jsx")
    assert "INTRO_LK" not in jsx, "фикстура не без большой строки"
    assert "var base=(sc && sc.value" not in jsx, "ветка «от базы» уехала в сборку без большой"
    assert "sc.setValueAtTime(t0,[70,70]); sc.setValueAtTime(t0+F_DUR,[100,100]);" in jsx, (
        "прежняя ветка масштаба потерялась")
    assert "sc.setValueAtTime(t0,[s0,s0]); sc.setValueAtTime(t0+F_DUR,[s1,s1]);" in jsx, (
        "ветка заднего плана потерялась")

