# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дисклеймер: ручки масштаба и положения в стиле и та же картинка в превью.

Раньше дисклеймер можно было только включить или выключить: положение стояло
захардкоженным `int(H·0.764)` в сборке, кегль считался сам под ширину кадра, а
предпросмотр его не рисовал ВООБЩЕ — в `static/app/*.js` не было ни слоя, ни чтения
из плана. Владелец просил «видно дисклеймер правильно в превью и ручки для его
регулирования (масштаб, положение), и чтобы как в АЕ».

Теперь числа считает ОДИН раз `plan_decor`, а берут их оба потребителя: `.jsx`
(подстановки `DISC_SIZE`/`DISC_Y`/`DISC_X`) и предпросмотр (план `plan["disclaimer"]`,
слой `#ipvdisc`). Здесь стерегутся пять вещей:

1. умолчания — байт в байт прежние: кегль подобран как раньше, `DISC_Y` = прежнее
   `int(H·0.764)`, `DISC_X` не объявлен вовсе (в эталоне положение — `W/2`);
2. ручки: `disc_scale` множит УЖЕ подобранный кегль (одно число и в плане, и в .jsx),
   `disc_y` задаёт долю высоты кадра, `disc_dx` объявляет `DISC_X`;
3. ключ плана есть при тексте и отсутствует при пустом/скрытом дисклеймере;
4. вертикаль: в плане есть `asc` — подъём ПЕРВОЙ строки над базовой линией при
   подобранном кегле (`fonts.ink_extent`, как у `disc_gap`). В AE якорь центрированного
   текстового слоя — базовая линия первой строки, поэтому превью ставит её верх на
   `y − asc` БЕЗ подгонки замером (замер полулидинга браузера уводил слой вниз);
5. превью (боевая `ipvDisc` из `static/app/85-inserts-view.js` под node) ставит в
   `#ipvdisc` строки, кегль и позицию ИЗ ПЛАНА, держит центр блока шириной в кадр на
   `x` (Position.x в .jsx: левый край = `x − W/2`, строки центрирует `text-align:center`
   без `transform`), а прозрачность ведёт по времени как в AE: 100 до `t_end−0.35`,
   линейно к нулю к `t_end`.

Шрифт собран программно (тот же приём, что в `test_disclaimer_fit.py`): тест не
опирается на системные шрифты, а кегль подобранного дисклеймера от ширины строки
зависит — значит нужен шрифт с известными метриками.
"""
import gzip
import io
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

from core import fonts  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import DISC_FIT_W, DEFAULT_DISCLAIMER  # noqa: E402

PS = "TestInk-Regular"
BASE_SIZE = 47                        # база при 1920: int(1920 * 0.0245)
W_FRAME, H_FRAME = 1080, 1920         # кадр по умолчанию: прежний DISC_Y = 1466
# «A» в тестовом шрифте — прямоугольник 0..700 при upm 1000 и asc 800: подъём чернил
# первой строки при кегле 47 — 32.9. Это и есть `asc` плана (fonts.ink_extent).
ASC_A = 0.7 * BASE_SIZE
JS_DIR = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
CSS_DIR = os.path.join(ROOT, "static", "app.css")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт превью требует node в PATH")


def _build_font(tmp_path):
    """Крошечный статичный шрифт с контурами известной высоты (upm 1000).

    «A» — прямоугольник 0..700, «g» — −200..500: по нему видно и ширину строки
    (кегль под ширину кадра), и шаг строк.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def _rect(x0, y0, x1, y1):
        pen = TTGlyphPen(None)
        pen.moveTo((x0, y0))
        pen.lineTo((x1, y0))
        pen.lineTo((x1, y1))
        pen.lineTo((x0, y1))
        pen.closePath()
        return pen.glyph()

    def _empty():
        return TTGlyphPen(None).glyph()

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "space", "A", "g"])
    fb.setupCharacterMap({0x20: "space", 0x41: "A", 0x67: "g"})
    fb.setupGlyf({".notdef": _empty(), "space": _empty(),
                  "A": _rect(0, 0, 500, 700), "g": _rect(0, -200, 500, 500)})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "space": (300, 0),
                               "A": (500, 0), "g": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestInk", "styleName": "Regular",
                       "uniqueFontIdentifier": "TestInk", "fullName": "TestInk",
                       "psName": PS})
    fb.setupOS2()
    fb.setupPost()
    fb.setupMaxp()
    fb.setupHead()
    path = str(tmp_path / "TestInk.ttf")
    fb.save(path)
    return path


@pytest.fixture()
def font(tmp_path, monkeypatch):
    _build_font(tmp_path)
    monkeypatch.setattr(fonts, "_FONT_DIRS", [str(tmp_path)])
    fonts.list_fonts(refresh=True)
    yield PS
    fonts._CACHE = None


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _old_size(text, w=W_FRAME, h=H_FRAME):
    """Прежний подбор кегля: база int(H·0.0245) и ужимание под DISC_FIT_W ширины.

    Считается прямо здесь, по формуле из git-истории: так тест доказывает «умолчания
    не поехали», а не сравнивает новую реализацию сама с собой.
    """
    base = int(h * 0.0245)
    widths = [fonts.text_width(PS, ln, base) for ln in text.split("\n")]
    if any(x is None for x in widths):
        return base
    w_max = max(widths)
    return (round(base * DISC_FIT_W * w / w_max, 2)
            if w_max > DISC_FIT_W * w else base)


def _plan(xml_subs, disclaimer, style=None):
    st = dict(style or {})
    st["font"] = PS
    return xml2ae.scene_plan(xml_subs, inserts=[], style=st, disclaimer=disclaimer,
                             emit=lambda *a, **k: None)


def _jsx(xml_subs, tmp_path, disclaimer, style=None, name="out.jsx"):
    st = dict(style or {})
    st["font"] = PS
    path, _, _ = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / name), style=st,
                                   disclaimer=disclaimer, emit=lambda *a, **k: None)
    return io.open(path, encoding="utf-8-sig").read()


def _decl(jsx, name):
    """Число объявления NAME= из .jsx (None, если объявления нет)."""
    m = re.search(r"\b" + re.escape(name) + r"=(-?[\d.]+)", jsx)
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# 1. Умолчания — прежние числа, DISC_X не объявлен
# --------------------------------------------------------------------------- #
def test_defaults_keep_old_size_and_y_and_no_disc_x(font, xml_subs, tmp_path):
    """Умолчания: кегль и DISC_Y прежние, DISC_X не объявлен вовсе.

    «Прежние» — посчитанные старой формулой, а не сверенные с новой реализацией:
    кегль под ширину кадра и положение `int(H·0.764)`. Выражение на Position при
    этом обязано остаться ровно прежним `W/2` — иначе эталон сборки разъехался бы.
    """
    text = "A" * 50 + "\nA"      # строка шире кадра: кегль ужимается под DISC_FIT_W
    want_size = _old_size(text)
    assert want_size == 42.85, "сдвинулась арифметика кегля (ожидали 42.85)"
    assert want_size != BASE_SIZE, "фикстура перестала быть шире кадра"

    plan = _plan(xml_subs, text)
    jsx = _jsx(xml_subs, tmp_path, text)
    assert plan["disclaimer"]["size"] == want_size
    assert _decl(jsx, "DISC_SIZE") == want_size, "кегль в .jsx разошёлся с планом"
    assert _decl(jsx, "DISC_Y") == int(H_FRAME * 0.764) == 1466
    assert plan["disclaimer"]["y"] == 1466, "preview уехал бы от .jsx по вертикали"
    # asc — подъём первой строки над базовой линией: по нему превью ставит верх блока.
    # Чернила «A» в тестовом шрифте — ровно 0.7 кегля (700/1000), и считать его надо при
    # УЖЕ подобранном кегле: иначе .jsx и превью разошлись бы.
    assert plan["disclaimer"]["asc"] == pytest.approx(0.7 * want_size, abs=0.01), \
        "в плане нет подъёма первой строки из метрик шрифта"
    assert "DISC_X" not in jsx, "при нулевом сдвиге DISC_X объявлять нельзя"
    assert 'setValue([W/2, DISC_Y])' in jsx, "выражение на Position текста изменилось"
    # хвостовой копии нет по умолчанию — и окна её показа в плане тоже нет
    assert plan["disclaimer"]["end_copy"] is None


# --------------------------------------------------------------------------- #
# 2. Ручки: масштаб, положение по высоте, сдвиг по горизонтали
# --------------------------------------------------------------------------- #
def test_disc_scale_multiplies_fitted_size_in_plan_and_jsx(font, xml_subs, tmp_path):
    """disc_scale = 150: кегль = прежний × 1.5, и это ОДНО число в плане и в .jsx.

    Множитель применяется к УЖЕ подобранному кеглю: подбор «только уменьшение»
    идёт до него, поэтому узкий шрифт ручка не раздувает.
    """
    text = "A" * 50 + "\nA"
    base = _old_size(text)
    st = {"disc_scale": 150}
    plan = _plan(xml_subs, text, st)
    jsx = _jsx(xml_subs, tmp_path, text, st)
    assert plan["disclaimer"]["size"] == round(base * 1.5, 2)
    assert _decl(jsx, "DISC_SIZE") == plan["disclaimer"]["size"], \
        "кегль плана и .jsx разошлись — превью показало бы не то, что собрано"
    # Кегль вырос — вырос и подъём строки: asc меряется при УЖЕ подобранном кегле
    assert plan["disclaimer"]["asc"] == pytest.approx(0.7 * plan["disclaimer"]["size"], abs=0.01), \
        "asc посчитан не при том кегле, что уехал в .jsx"


def test_disc_y_sets_stated_share_of_frame_height(font, xml_subs, tmp_path):
    """disc_y = 50: положение = H/2 — доля ВЫСОТЫ кадра, а не пиксели 1080-кадра."""
    plan = _plan(xml_subs, "A\ng", {"disc_y": 50})
    jsx = _jsx(xml_subs, tmp_path, "A\ng", {"disc_y": 50})
    assert plan["disclaimer"]["y"] == H_FRAME // 2 == 960
    assert _decl(jsx, "DISC_Y") == 960


def test_disc_dx_declares_disc_x_in_jsx(font, xml_subs, tmp_path):
    """disc_dx = −40: центр сдвинут влево, и DISC_X появляется в .jsx.

    При нулевом сдвиге объявления нет (см. тест умолчаний) — так .jsx при умолчаниях
    остаётся байт в байт прежним.
    """
    st = {"disc_dx": -40}
    plan = _plan(xml_subs, "A\ng", st)
    jsx = _jsx(xml_subs, tmp_path, "A\ng", st)
    assert plan["disclaimer"]["x"] == W_FRAME // 2 - 40 == 500
    assert _decl(jsx, "DISC_X") == 500
    assert 'setValue([DISC_X, DISC_Y])' in jsx, "Position текста не берёт DISC_X"
    assert jsx.count("DISC_X=") == 1, "DISC_X объявляется не один раз"


def test_tail_copy_windows_in_plan(font, xml_subs, tmp_path):
    """Галка «Повторить в конце»: у плана есть окно хвостовой копии, у головной — t_end.

    Хвост живёт на ДЛИНЕ ролика (слой `dle` начинается в DUR), поэтому его окно
    считается от длительности, а не от нуля.
    """
    plan = _plan(xml_subs, "A\ng", {"disclaimer_end": True})
    d = plan["disclaimer"]
    assert d["t_end"] == pytest.approx(1.35)
    assert d["end_copy"] is not None, "галка стоит, а окна хвоста в плане нет"
    # план несёт длину контента в СЕКУНДАХ (plan["dur"]), а не в кадрах
    assert d["end_copy"]["t0"] == pytest.approx(plan["dur"])
    assert d["end_copy"]["t1"] == pytest.approx(plan["dur"] + 1.35)
    # без галки хвоста нет
    assert _plan(xml_subs, "A\ng")["disclaimer"]["end_copy"] is None


# --------------------------------------------------------------------------- #
# 3. Ключ плана: есть при тексте, нет при пустом
# --------------------------------------------------------------------------- #
def test_plan_key_present_with_text_and_absent_when_hidden(font, xml_subs):
    """Текст есть — ключ есть; текст скрыт пустой строкой — ключа нет вовсе."""
    with_text = _plan(xml_subs, DEFAULT_DISCLAIMER)
    assert "disclaimer" in with_text
    assert with_text["disclaimer"]["lines"] == DEFAULT_DISCLAIMER.split("\n")
    assert with_text["disclaimer"]["font"] == PS, "превью не знало бы, каким шрифтом рисовать"
    assert with_text["disclaimer"]["size"] == _old_size(DEFAULT_DISCLAIMER)
    assert with_text["disclaimer"]["asc"] > 0, \
        "без положительного asc превью не посадит верх строки на базовую линию"

    assert "disclaimer" not in _plan(xml_subs, ""), \
        "пустой дисклеймер оставил ключ плана — превью нарисовало бы пустоту"


# --------------------------------------------------------------------------- #
# 4. Превью: боевая ipvDisc под node с заглушками DOM
# --------------------------------------------------------------------------- #
def _func(src, name):
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок.

    `async` — часть объявления: без неё у вырезанной функции остаётся `await` в теле,
    и node роняет скрипт ещё до проверок (тот же приём, что в прочих тестах интерфейса).
    """
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в 85-inserts-view.js не нашлась функция %s" % name
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError("не сошлись скобки у %s" % name)


# Заглушки: DOM и внешние двери. Разметка элемента повторяет templates/index.html
# (id, классы и вложенность #ipvdisc > .pvdis_text), чтобы проверялся боевой код.
STUBS = r"""
const ELS={};
function mk(tag,cls){
  return {tagName:tag,className:cls||'',style:{},dataset:{},kids:[],
    parentNode:null,parentElement:null,textContent:'',
    appendChild(o){o.parentNode=this;o.parentElement=this;this.kids.push(o);return o;},
    insertBefore(o,ref){o.parentNode=this;o.parentElement=this;const i=this.kids.indexOf(ref);
      if(i<0)this.kids.push(o);else this.kids.splice(i,0,o);return o;},
    querySelector(sel){return this.kids.find(k=>('.'+k.className)===sel)||null;},
    remove(){const p=this.parentNode;if(p)p.kids=p.kids.filter(k=>k!==this);},
    get firstChild(){return this.kids.length?this.kids[0]:null;},
    getBoundingClientRect(){return this.rect;}};
}
function $(id){return ELS[id]||null;}
const STAGE=mk('div','pvstage');STAGE.clientWidth=1080;  // экран 1:1 с кадром — масштаб 1
const DISC=mk('div','ipvdisc');DISC.id='ipvdisc';DISC.style.display='none';ELS.ipvdisc=DISC;
const TXT=mk('span','pvdis_text');
STAGE.appendChild(DISC);DISC.appendChild(TXT);
DISC.rect={top:100};                     // элемент уже на грубой вертикали
let STRUTS=0;                            // замеров базовой линии быть НЕ должно (asc из плана)
const STRUT_RECT={top:0,bottom:185};
const document={createElement(t){STRUTS++;return {className:'',parentNode:null,
  rect:STRUT_RECT,getBoundingClientRect(){return this.rect;},remove(){this.parentNode=null;}};}};
function ipvFontFor(){return null;}      // шрифта в системе нет — как у чужого семейства
function ipvNow(){return 0;}
var ITLFIT=0;
function itlFit(){ITLFIT++;}
function itlDraw(){}
function ipvUI(){}
async function ipvCalcApply(){}   // уже применённые рото-маски стенду не нужны
var IPV={plan:null,dur:112.33,contentDur:0,vids:[1],xml:'c:/out/a.xml'};
var _RESP=null;
async function fetch(){return {json:async()=>_RESP};}
function ipvPlanBody(){return {};}
function uiLog(){}
function t(s){return s;}
function pvApplyStageAspect(){}
function ipvSubsInvalidate(){}
function ipvIntroGroups(){return [];}
function sfxEnsure(){}
function renderSubRowsList(){}
function aewUpdateCaptionUI(){}
async function ipvCalcApply(){}
"""


def _funcs(src, names):
    """Несколько боевых функций файла подряд — стенду бывает нужно больше одной."""
    return "\n".join(_func(src, n) for n in names)


def _run(tmp_path, plan, times, name="pv_disc.js", mutate=None):
    """Боевая ipvDisc на заглушках: план, моменты времени — вернуть снимки слоя.

    `mutate` — пара «что заменить» -> «на что» в теле самой функции: так проверяется,
    что стенд ловит именно поломку центрирования, а не «зелен по любой правке».
    """
    src = io.open(JS_DIR, encoding="utf-8").read()
    body = ("IPV.plan=%s;\n" % json.dumps(plan, ensure_ascii=False)
            + "var out=%s.map(function(tm){ipvDisc(tm);\n"
              "return {display:DISC.style.display,fs:DISC.style.fontSize,lh:DISC.style.lineHeight,"
              "left:DISC.style.left,top:DISC.style.top,op:DISC.style.opacity,"
              "tf:DISC.style.transform,"
              "sh:DISC.style.textShadow,text:TXT.textContent};});\n"
              "console.log(JSON.stringify([out,STRUTS]));\n" % json.dumps(times))
    code = _func(src, "ipvDisc")
    if mutate is not None:
        old, new = mutate
        assert old in code, "мутация не нашлась в теле ipvDisc: %r" % (old,)
        code = code.replace(old, new)
    script = STUBS + "\n" + code + "\n" + body
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


# Левый край блока шириной в кадр: Position.x = ЦЕНТР блока, значит left = x − W/2.
# Второго способа центрирования (transform) быть не должно: сдвиг уже сидит в left,
# и translateX(-50%) увёл бы блок ещё на полкадра влево — центр блока уехал бы с x.
CENTER_LEFT = "(((d.x!=null?d.x:w/2)/w-0.5)*100)"
# Мутация «вернуть translateX(-50%)» ставит сдвиг инлайном: стенд обязан покраснеть.
MUT_TF = ("el.style.left=", "el.style.transform='translateX(-50%)';el.style.left=")


def _css_layer():
    """Тело правила .ipvdisc из app.css (ширина кадра и центрирование строк)."""
    css = io.open(CSS_DIR, encoding="utf-8").read()
    m = re.search(r"\.ipvdisc\{([^}]*)\}", css)
    assert m, "в app.css пропало правило .ipvdisc"
    return m.group(1)


def _cqw(text):
    """«-14.815cqw» -> -14.815; None, если это не cqw-число."""
    m = re.match(r"^(-?[\d.]+)cqw$", text or "")
    return float(m.group(1)) if m else None


def _width_cqw(body):
    """Ширина блока из правила .ipvdisc — вторая половина центра."""
    m = re.search(r"width:\s*(-?[\d.]+)cqw", body)
    assert m, "у .ipvdisc нет ширины в cqw: центр блока не посчитать"
    return float(m.group(1))


def _tf_shift_cqw(tf, width):
    """Сдвиг блока из transform: translateX(-50%) = −width/2, без transform — 0."""
    if not tf:
        return 0.0
    m = re.search(r"translateX\(\s*(-?[\d.]+)(%|cqw)?\s*\)", tf)
    assert m, "не разобрать transform слоя: %r" % tf
    v = float(m.group(1))
    unit = m.group(2) or "px"
    assert unit in ("%", "cqw"), "translateX в px: центр блока в cqw не посчитать: %r" % tf
    return width * v / 100.0 if unit == "%" else v


def _center_cqw(snap, width):
    """Фактический центр блока: left + width/2 + сдвиг из transform (если он есть)."""
    left = _cqw(snap.get("left"))
    assert left is not None, "left слоя не в cqw: %r" % snap.get("left")
    return left + width / 2.0 + _tf_shift_cqw(snap.get("tf"), width)


# --------------------------------------------------------------------------- #
# 5. Горизонталь: центр блока шириной в кадр стоит на Position.x
# --------------------------------------------------------------------------- #
@node
def test_preview_block_center_lands_on_position_x(tmp_path):
    """Центр блока — ровно на `x` (Position.x в .jsx), и центрирование ОДНО.

    Фактический центр считается арифметикой по выставленным стилям: `left` + ширина/2 +
    сдвиг из `transform`, если он есть, — и сверяется с `x/W·100` cqw. Сдвиг целиком
    сидит в `left` (x − W/2), поэтому при `width:100cqw` у слоя не должно быть `translate`:
    он увёл бы блок ещё на полкадра влево — строки занимали −540…526 px кадра вместо
    0…1080 и резались левым краем. Прежний тест сторожил лишь «наличие центрирования»
    (left = x − W/2 И translateX(-50%)) и ровно эту ошибку пропустил.
    """
    width = _width_cqw(_css_layer())
    assert width == 100.0, "ширина слоя не кадр: центр блока не сядет на x: %r" % width
    w = 1080
    plan = {"w": w, "h": 1920, "dur": 750, "fps": 25, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 540, "y": 1466, "asc": ASC_A,
        "lead": None, "t_end": 1.35, "fade": 0.35, "glow": 42, "end_copy": None}}
    for x in (540, 380, 760):
        plan["disclaimer"]["x"] = x
        got = _run(tmp_path, plan, [0.2], name="pv_disc_x%d.js" % x)[0][0]
        assert "translate" not in (got.get("tf") or ""), \
            "у слоя с width:100cqw появился transform — блок уедет на полкадра: %r" % got["tf"]
        center = _center_cqw(got, width)
        assert abs(center - x / w * 100) <= 0.1, \
            "центр блока не на Position.x: %r против %r" % (center, x / w * 100)

    src = io.open(JS_DIR, encoding="utf-8").read()
    assert CENTER_LEFT in _func(src, "ipvDisc"), \
        "в ipvDisc пропала формула левого края x − W/2 — центр блока уедет с x"


@node
def test_preview_centering_mutation_turns_red(tmp_path):
    """Мутация «вернуть translateX(-50%)» — проверка центра обязана покраснеть.

    Стенд считает центр блока по стилям (`left` + ширина/2 + сдвиг из `transform`):
    сдвиг уже сидит в `left`, поэтому второй сдвиг уводит центр на полкадра влево и он
    расходится с x. Тест, стороживший только «наличие центрирования», этой поломки не
    видел — его и заменила эта арифметика.
    """
    width = _width_cqw(_css_layer())
    plan = {"w": 1080, "h": 1920, "dur": 750, "fps": 25, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 380, "y": 1466, "asc": ASC_A,
        "lead": None, "t_end": 1.35, "fade": 0.35, "glow": 42, "end_copy": None}}
    got = _run(tmp_path, plan, [0.2], name="pv_disc_mut_tf.js", mutate=MUT_TF)[0][0]
    assert "translate" in (got.get("tf") or ""), "мутация не поставила сдвиг в transform"
    center = _center_cqw(got, width)
    assert abs(center - 380 / 1080 * 100) > 0.1, \
        f"мутация «лишний translateX(-50%)» не поймана: центр всё ещё на x ({center!r})"


@pytest.mark.skipif(not os.path.exists(CSS_DIR), reason="CSS превью нет — стенд отдельный")
def test_css_centers_rows_without_transform():
    """Строки центрирует text-align:center, а не translate: у слоя нет transform.

    Центр блока задан ОДНИМ числом — `left = x − W/2` (его ставит ipvDisc): при ширине
    кадра этого достаточно, а `translateX(-50%)` уводил блок ещё на полкадра влево.
    Внутренний span — блоком потока, не absolute: иначе text-align не центрирует строки.
    """
    body = _css_layer()
    assert re.search(r"width:\s*100cqw", body), "у .ipvdisc нет ширины кадра"
    assert "text-align:center" in body, "у .ipvdisc нет центрирования строк"
    assert "translate" not in body, "у слоя вернулся transform: центр блока уедет с x"
    inner = re.search(r"\.ipvdisc \.pvdis_text\{([^}]*)\}",
                      io.open(CSS_DIR, encoding="utf-8").read())
    assert inner, "в app.css пропало правило .ipvdisc .pvdis_text"
    ibody = inner.group(1)
    assert "position:absolute" not in ibody, \
        "внутренний текст снова absolute: text-align не центрирует строки по x"
    assert re.search(r"display:\s*block", ibody), \
        "внутренний текст не блок потока: text-align не центрирует строки по x"


@node
def test_preview_layer_takes_lines_size_and_position_from_plan(tmp_path):
    """Превью берёт из плана строки, кегль и положение — не рисует дисклеймер «на глаз».

    Кегль, шаг строк и горизонталь — теми же числами, что уехали в .jsx; вертикаль —
    базовая линия ПЕРВОЙ строки (якорь текстового слоя в AE): верх блока = `y − asc`
    из плана. Замером в браузере она больше не добирается — полулидинг уводил слой вниз.
    """
    w, h = 1080, 1920
    plan = {"w": w, "h": h, "dur": 30 * 25, "fps": 25, "disclaimer": {
        "lines": ["ПЕРВАЯ СТРОКА", "ВТОРАЯ СТРОКА"], "font": PS, "size": 47,
        "x": 540, "y": 1466, "asc": ASC_A, "lead": 40, "t_end": 1.35, "fade": 0.35,
        "glow": 42, "end_copy": None}}
    out, struts = _run(tmp_path, plan, [0.5])
    assert struts == 0, \
        "превью снова мерит базовую линию в браузере, а её держит asc плана: %d" % struts
    got = out[0]
    assert got["display"] == "", "слой дисклеймера не показан"
    assert got["text"] == "ПЕРВАЯ СТРОКА\nВТОРАЯ СТРОКА", \
        "в слой легли не строки плана: %r" % got["text"]
    assert got["fs"] == "%.3fcqw" % (47 / w * 100), "кегль взят не из плана: %r" % got["fs"]
    assert got["lh"] == "%.4f" % (40 / 47), "шаг строк не из плана: %r" % got["lh"]
    assert got["left"] == "%.3fcqw" % (((540 - w / 2) / w) * 100), "центр блока не по Position.x"
    # Position.y — базовая линия первой строки: 1466 минус asc (подъём чернил первой строки)
    assert got["top"] == "%.3fcqw" % ((1466 - ASC_A) / w * 100), \
        "верх блока не посажен базовой линией первой строки: %r" % got["top"]


@node
def test_preview_position_follows_style_knobs(tmp_path):
    """Ручки стиля доехали до превью: другой x/y и другой кегль — другие числа в слое.

    Проверяется, что превью читает ИМЕННО план, а не свои константы кадра: сдвинутый
    влево и опущенный дисклеймер с крупным кеглем обязан встать по числам плана.
    """
    plan = {"w": 1080, "h": 1920, "dur": 750, "fps": 25, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 70.5, "x": 380, "y": 960, "asc": 49.35,
        "lead": None, "t_end": 1.35, "fade": 0.35, "glow": 42, "end_copy": None}}
    got = _run(tmp_path, plan, [0.2])[0][0]
    assert got["fs"] == "%.3fcqw" % (70.5 / 1080 * 100)
    assert got["left"] == "%.3fcqw" % (((380 - 1080 / 2) / 1080) * 100)
    assert got["top"] == "%.3fcqw" % ((960 - 49.35) / 1080 * 100)
    assert got["lh"] == "", "авто-интервал не должен превращаться в множитель"


@node
def test_preview_opacity_fades_like_ae(tmp_path):
    """Прозрачность по времени как в AE: 100 до t_end−0.35, линейно к 0 к t_end.

    Ключи .jsx: `setValueAtTime(DISC_END-0.35, 100)`, `setValueAtTime(DISC_END, 0)`.
    Значит на `t_end−0.1` (середина спуска) слой полупрозрачный, а за `t_end` — ноль
    и слой спрятан: дисклеймер не «залипает» на кадре после конца показа.
    """
    plan = {"w": 1080, "h": 1920, "dur": 750, "fps": 25, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 540, "y": 1466, "lead": None,
        "t_end": 1.35, "fade": 0.35, "glow": 42, "end_copy": None}}
    out = _run(tmp_path, plan, [1.35 - 0.1, 1.35 + 0.1])[0]
    mid, after = out
    assert 0 < float(mid["op"]) < 1, "в середине спуска прозрачность не линейная: %r" % mid["op"]
    assert float(mid["op"]) == pytest.approx(0.1 / 0.35, abs=0.01), \
        "кривая спуска разошлась с ключами .jsx: %r" % mid["op"]
    assert after["op"] == "0" and after["display"] == "none", \
        "после t_end дисклеймер остался на экране: %r" % after


@node
def test_preview_shows_tail_copy_after_the_head(tmp_path):
    """Хвостовая копия — своё окно: после конца ролика слой снова виден и снова гаснет.

    Головной блок к этому моменту уже погас; без окна `end_copy` превью показало бы
    пустой кадр там, где в .jsx стоит вторая копия дисклеймера.
    """
    plan = {"w": 1080, "h": 1920, "dur": 750, "fps": 25, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 540, "y": 1466, "lead": None,
        "t_end": 1.35, "fade": 0.35, "glow": 42,
        "end_copy": {"t0": 30.0, "t1": 31.35}}}
    out = _run(tmp_path, plan, [5.0, 30.0, 30.9, 31.1, 31.36])[0]
    assert out[0]["display"] == "none", "головной показ не закончился"
    assert out[1]["display"] == "", "хвостовая копия не появилась"
    assert float(out[1]["op"]) == pytest.approx(1.0), "копия начинается не с полной прозрачности"
    assert float(out[2]["op"]) == pytest.approx(1.0), "копия гаснет раньше своих 0.35 с"
    assert 0 < float(out[3]["op"]) < 1, "копия не гаснет в своём окне"
    assert out[4]["display"] == "none" and out[4]["op"] == "0", "копия не догорела"


def test_preview_layer_exists_in_markup_and_is_driven_by_plan():
    """Слой #ipvdisc есть в разметке, а зовёт его боевой ipvUI — не «когда-нибудь»."""
    html = io.open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
    assert 'id="ipvdisc"' in html, "в разметке превью нет слоя дисклеймера"
    assert 'class="pvdis_text"' in html, "у слоя нет элемента под текст"
    src = io.open(JS_DIR, encoding="utf-8").read()
    body = src[src.index("function ipvUI("):src.index("function insPreviewBox(")]
    assert "ipvDisc(tm)" in body, "ipvUI не зовёт отрисовку дисклеймера"
    assert "ipvdisc" in body, "план без дисклеймера не прячет его слой"


@node
def test_preview_extends_to_the_tail_copy(tmp_path):
    """Хвост длиннее ролика — предпросмотр обязан до него дотянуться.

    Композиция в AE удлиняется на хвостовой дисклеймер (build.py: `comp_dur`), а превью
    играет длину из `/api/aicut_preview`, где хвоста нет, — без продления копию в конце
    не увидеть ни здесь, ни в рендере без AE. Длину контента держим отдельно
    (`IPV.contentDur`): по ней считаются линейка таймлайна и полоса-прогресс.
    """
    tail_plan = {"w": 1080, "h": 1920, "dur": 112.33, "fps": 60, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 540, "y": 1466, "lead": None,
        "t_end": 1.35, "fade": 0.35, "glow": 42,
        "end_copy": {"t0": 112.33, "t1": 113.68}}}
    plain_plan = {"w": 1080, "h": 1920, "dur": 112.33, "fps": 60, "disclaimer": {
        "lines": ["A"], "font": PS, "size": 47, "x": 540, "y": 1466, "lead": None,
        "t_end": 1.35, "fade": 0.35, "glow": 42, "end_copy": None}}

    src = io.open(JS_DIR, encoding="utf-8").read()
    body = ("_RESP={ok:true,plan:IPV.plan};\n"
            "(async()=>{\n"
            "var out=[];\n"
            "for(const p of [window.TAIL,window.PLAIN]){\n"
            "  IPV={plan:p,dur:112.33,contentDur:0,vids:[1],xml:'c:/out/a.xml'};\n"
            "  _RESP.plan=p;ITLFIT=0;\n"
            "  await ipvPlanFetch();out.push([IPV.dur,ITLFIT]);}\n"
            "console.log(JSON.stringify(out));\n"
            "})();\n")
    script = (STUBS + "\n" + _funcs(src, ["ipvPlanFetch"]) + "\n"
              + "window={};window.TAIL=%s;window.PLAIN=%s;\n" % (
                  json.dumps(tail_plan, ensure_ascii=False),
                  json.dumps(plain_plan, ensure_ascii=False))
              + body)
    path = str(tmp_path / "pv_tail.js")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    tail_dur, tail_fit = out[0]
    plain_dur, plain_fit = out[1]
    assert tail_dur == pytest.approx(113.68), \
        "предпросмотр не дотянулся до хвостовой копии: %r" % tail_dur
    assert tail_fit == 1, "таймлайн не пересчитан под удлинённый предпросмотр"
    assert plain_dur == pytest.approx(112.33), \
        "без хвоста длина предпросмотра поехала: %r" % plain_dur
    assert plain_fit == 0, "таймлайн пересчитан там, где длина не менялась"
