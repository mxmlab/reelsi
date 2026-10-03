# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Цвет камер через Lumetri и файловое поле без редактора звука.

Две половины задания, обе проверяются здесь:

- **Lumetri.** Галка стиля `lm_on` кладёт на каждый клип камеры и на каждую рото-копию
  один эффект `ADBE Lumetri` с девятью значениями (номера параметров сняты с живого
  AE 26.2, таблица — в `core/xml2ae/build.py:LUMETRI_PARAMS`); экспозиция клипа (шаг AE,
  kwarg `exposure`) ПРИБАВЛЯЕТСЯ к стилевой. Выключенная галка обязана оставить .jsx
  побайтово прежним (`fixtures/golden_geometry.jsx`), поэтому проверяется и она.
  Превью рисует приближение теми же числами из `plan["lumetri"]` — кривая тона, баланс и
  насыщенность в SVG-фильтре (вторая копия формул в JS не заводится: числа даёт план).
- **Файловое поле.** Карты звуков (`SFX_PREFIX`/`SFX_ISVIDEO`) собираются только по полям
  со `sfx`: раньше в них попадало ЛЮБОЕ поле `ctl:"file"`, и выбор файла подложки
  открывал «Настройку звука».

Кривая превью — не выдумка, а ЗАМЕР эталона AE: пары «кадр нашего рендера без цвета
(`lm_on=false`) → тот же кадр из AE» по десяткам кадров, медиана по корзинам яркости
(`test_node_model_matches_measured_ae_curve`). Оттуда же три вывода, которые проверяет
этот файл: экспозиция — стопы в линейном свете, тона — множитель к линейной яркости,
и у каналов разные кривые (поканальная поправка входа).

Запуск: python -m pytest tests/test_lumetri.py -q
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
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.build import LUMETRI_PARAMS  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402
from tests.test_style_panel_js import DOM_STUB  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
VIEW_JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")

# Ручки группы «Цвет (Lumetri)» — в порядке таблицы задания.
LM_KEYS = ("lm_exposure", "lm_contrast", "lm_highlights", "lm_shadows", "lm_whites",
           "lm_blacks", "lm_temp", "lm_tint", "lm_sat")
# Ненулевые значения для «все поля заданы»: отличаются от дефолтов (0, насыщенность 100).
LM_ALL = {"lm_exposure": 0.5, "lm_contrast": 10, "lm_highlights": 20, "lm_shadows": 30,
          "lm_whites": -40, "lm_blacks": -50, "lm_temp": 60, "lm_tint": -70,
          "lm_sat": 150}
# Та же таблица для Камеры 2 (ключи с приставкой lm2_) — заведомо ДРУГИЕ числа:
# на одинаковых «применяется свой набор» не отличить от «берёт набор Камеры 1».
LM2_KEYS = tuple("lm2_" + k[len("lm_"):] for k in LM_KEYS)
LM2_ALL = {"lm2_exposure": -0.75, "lm2_contrast": -12, "lm2_highlights": -18,
           "lm2_shadows": -25, "lm2_whites": 38, "lm2_blacks": 45,
           "lm2_temp": -55, "lm2_tint": 65, "lm2_sat": 75}
# Экспозиция клипа (шаг AE) — её прибавляет сборка, а не панель.
CLIP_EXPOSURE = 1.25

# Тот же набор вставок, что у геометрии: golden обязан быть машино-независимым.
INS = [
    {"type": "photo", "style": "cam2", "media": "C:/x/a.png", "start_s": 1, "dur_s": 2},
    {"type": "photo", "style": "cam2", "media": "C:/x/cam2.png", "start_s": 8.0, "dur_s": 1.5},
    {"type": "photo", "style": "cam1", "media": "C:/x/b.png", "start_s": 5, "dur_s": 3},
]

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура таймлайна (та же, что у test_geometry_python)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личные словари."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _build(xml, tmp_path, style=None, exposure=0.0, inserts=None):
    """Сборка .jsx фикстуры: то же, что test_geometry_python._build, плюс exposure."""
    st = dict(style or {})
    st["intro_riser"] = False
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / "out.jsx"),
        inserts=[dict(x) for x in (inserts if inserts is not None else INS)],
        style=st, disclaimer="", intro_riser=False, exposure=exposure,
        emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _plan(xml, style=None, exposure=0.0):
    return xml2ae.scene_plan(xml, style=dict(style or {}), exposure=exposure,
                             disclaimer="", intro_riser=False, emit=lambda *a, **k: None)


def _lumetri_from_jsx(jsx, name="LUMETRI"):
    """LUMETRI (или LUMETRI2) из .jsx как словарь чисел: в .jsx это литерал объекта."""
    m = re.search(r"var %s = \{(.*?)\};" % re.escape(name), jsx)
    assert m, "в .jsx нет объявления var %s" % name
    out = {}
    for part in m.group(1).split(","):
        key, val = part.split(":")
        out[key.strip()] = float(val)
    return out


def _func(src, name):
    """Тело функции по имени (счёт скобок) — так же, как в test_cam1_zoom_none."""
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


def _lm_group():
    """Группа схемы с галкой lm_on — в ней живут все ручки цвета."""
    def walk(items):
        for it in items:
            if it.get("toggle") == "lm_on":
                return it
            got = walk(it.get("items", []) or [])
            if got:
                return got
        return None

    for layer in style_schema.LAYERS:
        got = walk(layer.get("items", []))
        if got:
            return got
    return None


def _run_node(tmp_path, name, prelude, body):
    """Скрипт = прелюдия (боевой код) + проверки; на выходе JSON последней строки."""
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(prelude)
        f.write(body)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------
# 1. Выключенная галка — .jsx побайтово как на main
# ------------------------------------------------------------------

def test_lm_off_jsx_is_the_main_one(xml_subs, tmp_path):
    """1. Дефолт BASE (и явное lm_on=False) — .jsx побайтово как на main: golden держит."""
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path, style={})) == golden, \
        "стиль по умолчанию разошёлся с эталоном"

    off = dict(LM_ALL)
    off["lm_on"] = False
    assert _mask_assets(_build(xml_subs, tmp_path, style=off)) == golden, \
        "выключенная галка с заполненными ручками изменила .jsx — подстановки не пустые"

    jsx = _build(xml_subs, tmp_path, style={"lm_on": False})
    assert "LUMETRI" not in jsx and "applyLumetri" not in jsx, \
        "при снятой галке в .jsx появились объявления Lumetri"
    assert "if (EXPOSURE!=0)" in jsx, "покадровая экспозиция клипов пропала из .jsx"


# ------------------------------------------------------------------
# 2. Включённая галка — LUMETRI, applyLumetri, matchName'ы, экспозиция
# ------------------------------------------------------------------

def test_lm_on_writes_lumetri_to_cameras_and_roto(xml_subs, tmp_path):
    """2. Все девять значений в .jsx, вызов на клипах камер и рото, номера из таблицы."""
    st = dict(LM_ALL)
    st["lm_on"] = True
    jsx = _build(xml_subs, tmp_path, style=st, exposure=CLIP_EXPOSURE)

    got = _lumetri_from_jsx(jsx)
    want = {"exposure": LM_ALL["lm_exposure"] + CLIP_EXPOSURE,
            "contrast": LM_ALL["lm_contrast"], "highlights": LM_ALL["lm_highlights"],
            "shadows": LM_ALL["lm_shadows"], "whites": LM_ALL["lm_whites"],
            "blacks": LM_ALL["lm_blacks"], "temp": LM_ALL["lm_temp"],
            "tint": LM_ALL["lm_tint"], "sat": LM_ALL["lm_sat"]}
    assert set(got) == set(want), f"в LUMETRI не те девять ключей: {sorted(got)}"
    for key, val in want.items():
        assert got[key] == pytest.approx(val), \
            f"LUMETRI.{key}: в .jsx {got[key]!r}, ожидалось {val!r}"

    for _key, match, _label in LUMETRI_PARAMS:
        assert '"%s"' % match in jsx, f"в .jsx нет matchName {match}"
    assert "applyLumetri(lay);" in jsx, "Lumetri не вешается на клипы камер"
    assert "applyLumetri(cc);" in jsx, "Lumetri не вешается на рото-копии"
    assert "if (EXPOSURE!=0)" not in jsx, \
        "вместе с Lumetri осталась покадровая экспозиция — цвет ставился бы дважды"
    assert "ADBE Lumetri" in jsx

    body = _func(jsx, "applyLumetri")
    assert "_LOG(" in body, "ошибки Lumetri не уходят в лог сборки"
    assert not re.search(r"catch\s*\(\s*\w+\s*\)\s*\{\s*\}", body), \
        "в applyLumetri появился пустой catch — ошибка цвета станет невидимой"


# ------------------------------------------------------------------
# 3. План сцены: None при снятой галке, иначе dict девяти значений
# ------------------------------------------------------------------

def test_plan_lumetri_none_or_dict(xml_subs):
    """3. plan["lumetri"] = None без галки (даже с заполненными ручками), иначе dict."""
    assert _plan(xml_subs, style={})["lumetri"] is None
    assert _plan(xml_subs, style=dict(LM_ALL))["lumetri"] is None, \
        "ручки без галки всё равно попали в план"
    assert _plan(xml_subs, style={"lm_on": False, "lm_exposure": 3.0})["lumetri"] is None

    plan = _plan(xml_subs, style=dict(LM_ALL, lm_on=True), exposure=CLIP_EXPOSURE)
    lum = plan["lumetri"]
    assert isinstance(lum, dict), "план не несёт значения Lumetri"
    assert set(lum) == {key for key, _m, _l in LUMETRI_PARAMS}, sorted(lum)
    assert lum["exposure"] == pytest.approx(LM_ALL["lm_exposure"] + CLIP_EXPOSURE), \
        "экспозиция клипа не прибавилась к стилевой"
    assert lum["sat"] == pytest.approx(LM_ALL["lm_sat"])
    assert lum["temp"] == pytest.approx(LM_ALL["lm_temp"])


# ------------------------------------------------------------------
# 4. Превью: кривая тона и фильтр (node, боевой static/app/85-inserts-view.js)
# ------------------------------------------------------------------

def _lm_points() -> int:
    """Сколько точек в таблице кривой превью (IPV_LM_N из 85-inserts-view.js)."""
    src = io.open(VIEW_JS, encoding="utf-8").read()
    m = re.search(r"IPV_LM_N\s*=\s*(\d+)", src)
    assert m, "в 85-inserts-view.js не нашлась IPV_LM_N"
    return int(m.group(1))


def _prelude_view():
    """Боевой код превью + константы приближения, вынутые из него же."""
    src = io.open(VIEW_JS, encoding="utf-8").read()
    consts = "".join("const %s=%s;\n" % (name, val)
                     for name, val in re.findall(r"(IPV_LM_\w+)\s*=\s*(-?[\d.]+)", src))
    assert "IPV_LM_HL" in consts and "IPV_LM_BAL" in consts, \
        "в 85-inserts-view.js не нашлись константы приближения Lumetri"
    # Подписи собранных фильтров — переменные верхнего уровня файла: у каждой камеры своя,
    # и без них стенд падает ReferenceError'ом на первой же сборке фильтра Камеры 2.
    sigs = "".join(line + "\n" for line in src.splitlines()
                   if re.match(r"\s*let\s+IPV_LM\w*SIG\b", line))
    assert "IPV_LM_SIG" in sigs and "IPV_LM2_SIG" in sigs, \
        "в 85-inserts-view.js не нашлись подписи собранных фильтров"
    return (consts + sigs + _func(src, "ipvLmSmooth") + "\n" + _func(src, "ipvLmToLin") + "\n"
            + _func(src, "ipvLmToSrgb") + "\n" + _func(src, "ipvLmBand") + "\n"
            + _func(src, "ipvLumetriTone") + "\n"
            + _func(src, "ipvLumetriTable") + "\n" + _func(src, "ipvLumetriFilter") + "\n")


TONE = r"""
const zero = {exposure:0, contrast:0, highlights:0, shadows:0, whites:0, blacks:0,
              temp:0, tint:0, sat:100};
const xs = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1];
const ident = xs.map(function(x){ return Math.abs(ipvLumetriTone(x, zero) - x); });
const bright = Object.assign({}, zero, {exposure:1});
const mid = [0.25, 0.5].map(function(x){ return ipvLumetriTone(x, bright) - ipvLumetriTone(x, zero); });
// Экспозиция — стопы в ЛИНЕЙНОМ свете: 2^ex множит линейную яркость, а не гамма-значение.
// Выше 1.0 линейная яркость уходит за белое — там кривая срезает (как и любая гамма),
// поэтому сверка идёт по значениям, которые в диапазон влезают.
const stops = [0.1, 0.25, 0.5, 0.7].map(function(x){
  return Math.abs(ipvLumetriTone(x, bright) - ipvLmToSrgb(ipvLmToLin(x) * 2)); });
const gamma = [0.25, 0.5].map(function(x){ return x * 2 - x; });
const dark100 = Object.assign({}, zero, {shadows:100});
const sh = [0.1, 0.3].map(function(x){ return ipvLumetriTone(x, dark100) - ipvLumetriTone(x, zero); });
const light = [0.9, 1].map(function(x){ return Math.abs(ipvLumetriTone(x, dark100) - ipvLumetriTone(x, zero)); });
console.log(JSON.stringify({ident: ident, mid: mid, sh: sh, light: light,
                            stops: stops, gamma: gamma}));
"""


@node
def test_node_tone_curve(tmp_path):
    """4. При нулях кривая — тождество; exposure=1 светлее на стоп в ЛИНЕЙНОМ свете;
    shadows=+100 поднимает тени, а светлые почти не трогает."""
    res = _run_node(tmp_path, "zj_tone.js", _prelude_view(), TONE)
    assert max(res["ident"]) <= 1e-6, f"кривая при нулях не тождество: {res['ident']}"
    assert min(res["mid"]) > 0.08, f"exposure=1 не поднял середину: {res['mid']}"
    assert max(res["stops"]) <= 1e-9, (
        f"экспозиция не стопы в линейном свете (расхождение {max(res['stops'])}): "
        "ручка множит гамма-значение, а не линейную яркость")
    # И это не то же самое, что прежняя формула: по гамме было бы вдвое светлее.
    assert min(res["gamma"]) > 0.2, res["gamma"]
    assert min(res["sh"]) > 0.05, f"shadows=+100 не поднял тёмные: {res['sh']}"
    assert max(res["light"]) < 0.01, f"shadows=+100 перекрасил светлые: {res['light']}"


# Мини-DOM для стендов фильтра: только то, что нужно фильтру (svg, атрибуты, innerHTML).
# Общий у обеих камер: стенд камеры 2 отличается только планом.
_FILTER_DOM = r"""
let builds = 0;
const byId = {};
function makeEl(tag){
  const el = {tagName: tag, attrs: {}, style: {}, children: [],
    setAttribute: function(k, v){ el.attrs[k] = v; if (k === 'id') byId[v] = el; },
    appendChild: function(c){ el.children.push(c); }};
  Object.defineProperty(el, 'innerHTML', {
    get: function(){ return el._html || ''; },
    set: function(v){ el._html = v; builds++; }});
  return el;
}
global.document = {body: makeEl('body'), createElementNS: function(_ns, tag){ return makeEl(tag); }};
global.$ = function(id){ return byId[id] || null; };
const grab = function(markup, what){
  const m = markup.match(new RegExp(what + '="([^"]*)"'));
  return m ? m[1] : '';
};
"""

FILTER = _FILTER_DOM + r"""
global.IPV = {plan: {lumetri: null}};
const none = ipvLumetriFilter();
const zero = {exposure:0, contrast:0, highlights:0, shadows:0, whites:0, blacks:0,
              temp:0, tint:0, sat:100};
IPV.plan.lumetri = zero;
const first = ipvLumetriFilter();
const afterFirst = builds;
const cached = ipvLumetriFilter();
const afterCached = builds;
const markup1 = byId['ipvLumetriSvg'].innerHTML;
IPV.plan.lumetri = Object.assign({}, zero, {temp:100, tint:-100, sat:150});
const second = ipvLumetriFilter();
const markup2 = byId['ipvLumetriSvg'].innerHTML;
IPV.plan.lumetri = null;
const offAgain = ipvLumetriFilter();
console.log(JSON.stringify({none: none, first: first, cached: cached, second: second,
  offAgain: offAgain, afterFirst: afterFirst, afterCached: afterCached,
  builds: builds, container: byId['ipvLumetriSvg'].attrs.id,
  funcs: (markup1.match(/feFuncR|feFuncG|feFuncB/g) || []).length,
  table: grab(markup1, 'tableValues').split(' ').length,
  filterId: markup1.indexOf('<filter id="ipvLumetri"') === 0,
  sRGB: markup1.indexOf('color-interpolation-filters="sRGB"') > 0,
  matrix1: grab(markup1, 'type="matrix" values').split(' '),
  sat1: grab(markup1, 'type="saturate" values'),
  matrix2: grab(markup2, 'type="matrix" values').split(' '),
  sat2: grab(markup2, 'type="saturate" values')}));
"""


@node
def test_node_filter_from_plan(tmp_path):
    """4. Без plan.lumetri фильтр — 'none'; иначе url(#ipvLumetri), и он собирается
    ЗАНОВО только при смене плана (не на каждом кадре)."""
    res = _run_node(tmp_path, "zj_filter.js", _prelude_view(), FILTER)
    assert res["none"] == "none", "снятая галка не отключила фильтр превью"
    assert res["first"] == "url(#ipvLumetri)" and res["second"] == "url(#ipvLumetri)"
    assert res["cached"] == "url(#ipvLumetri)"
    assert res["offAgain"] == "none", "фильтр остался после возврата плана в null"
    assert res["afterFirst"] == 1, "фильтр собран не один раз"
    assert res["afterCached"] == 1, "фильтр пересобирается на каждом кадре"
    assert res["builds"] == 2, "смена plan.lumetri не пересобрала фильтр"
    assert res["container"] == "ipvLumetriSvg"
    assert res["filterId"] and res["sRGB"], "фильтр не тот или считается в linearRGB"
    assert res["funcs"] == 3, "кривая тона висит не на всех трёх каналах"
    # Точек в таблице кривой: столько, сколько объявлено константой превью. Через неё
    # (а не числом) — чтобы правка кривой не требовала правки теста дважды; при этом
    # их заведомо больше прежних 64: у кривой с переводом в линейный свет изгиб круче.
    assert res["table"] == _lm_points(), (
        f"точек в таблице кривой: {res['table']}, а в IPV_LM_N {_lm_points()}")
    assert res["table"] >= 64, res["table"]
    # при нулях фильтр ничего не меняет: диагональ баланса 1, насыщенность 1
    assert [float(v) for v in res["matrix1"]] == [1, 0, 0, 0, 0, 0, 1, 0, 0, 0,
                                                  0, 0, 1, 0, 0, 0, 0, 0, 1, 0]
    assert float(res["sat1"]) == 1
    # temp=100 поднимает R и опускает B, tint=-100 поднимает G — как в таблице задания
    m2 = [float(v) for v in res["matrix2"]]
    assert m2[0] == pytest.approx(1.2), m2
    assert m2[6] == pytest.approx(1.2), m2
    assert m2[12] == pytest.approx(0.8), m2
    assert m2[18] == pytest.approx(1.0), m2
    assert float(res["sat2"]) == pytest.approx(1.5), res["sat2"]


# ------------------------------------------------------------------
# 4b. Модель против СНЯТОЙ кривой AE: опорные точки замера
# ------------------------------------------------------------------
# Замер (эталон exp/C1476.mov, наш рендер того же клипа с lm_on=false): пары
# пиксель-в-пиксель по 22 кадрам без субтитров/вставок/интро, медиана выхода AE по
# корзинам входа шириной 4 уровня — по каждому каналу отдельно. Здесь опорные точки
# оттуда: уровень нашего кадра -> уровень эталона. Диапазон 22…178: ниже 20 уровня
# ±1 код = 15-20 % яркости (шум кодека в обоих файлах), выше 180 кривая эталона
# выполаживается, и сравнение там ничего не значит.
LM_ANCHORS = {
    0: ((22, 27), (34, 43), (50, 62), (66, 82), (82, 102), (98, 120), (114, 139),
        (130, 158), (146, 176), (162, 193), (178, 208)),
    1: ((22, 26), (34, 40), (50, 60), (66, 79), (82, 98), (98, 116), (114, 135),
        (126, 149)),
    2: ((22, 25), (34, 38), (50, 57), (66, 76), (82, 94), (98, 112), (114, 132)),
}
# Стиль замера: тот же, что у владельца («ДжаггерНеу»).
LM_REF = {"exposure": 0.5, "highlights": 11, "shadows": -5, "whites": 0,
          "blacks": 0, "contrast": 0, "temp": 0, "tint": 0, "sat": 100}

MODEL_CURVE = r"""
const LM = %(lm)s;
const ANCH = %(anch)s;
const tabs = [0, 1, 2].map(function(c){
  return ipvLumetriTable(LM, c).split(' ').map(Number);});
let worst = 0, where = '', rows = [];
for (const c of [0, 1, 2]) {
  for (const pair of ANCH[c]) {
    const x = pair[0], want = pair[1];
    const v = x / 255 * (IPV_LM_N - 1), i = Math.floor(v), f = v - i;
    // в таблице фильтра значения 0…1 (так их и ждёт feComponentTransfer)
    const got = (tabs[c][i] * (1 - f) + tabs[c][Math.min(i + 1, IPV_LM_N - 1)] * f) * 255;
    const d = got - want;
    rows.push([c, x, want, +got.toFixed(2), +d.toFixed(2)]);
    if (Math.abs(d) > Math.abs(worst)) { worst = d; where = 'RGB'[c] + ' ' + x; }
  }
}
console.log(JSON.stringify({worst: worst, where: where, rows: rows,
                            points: rows.length}));
"""


@node
def test_node_model_matches_measured_ae_curve(tmp_path):
    """4б. Кривая превью повторяет снятую с эталона: на опорных точках замера
    расхождение не больше 3 уровней по каждому каналу."""
    body = MODEL_CURVE % {"lm": json.dumps(LM_REF),
                          "anch": json.dumps({str(c): v for c, v in LM_ANCHORS.items()})}
    res = _run_node(tmp_path, "zj_curve.js", _prelude_view(), body)
    assert res["points"] == sum(len(v) for v in LM_ANCHORS.values()), res
    assert abs(res["worst"]) <= 3.0, (
        f"кривая превью разошлась с замером AE на {res['worst']:+.2f} уровня "
        f"({res['where']}); точки: {res['rows']}")


# ------------------------------------------------------------------
# 5. Файловое поле не открывает редактор звука (карты звуков — только по sfx)
# ------------------------------------------------------------------

SFX_MAPS = r"""
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
STSCHEMA = {base: BASE, layers: LAYERS};
initSfxMaps();
console.log(JSON.stringify({
  plate: has(SFX_PREFIX, 'st_insert_plate_file'),
  plate_video: has(SFX_ISVIDEO, 'st_insert_plate_file'),
  pop: SFX_PREFIX['st_pop'],
  transition: SFX_PREFIX['st_transition'],
  transition_sfx: SFX_PREFIX['st_transition_sfx'],
  riser: SFX_PREFIX['st_intro_riser_file'],
  glitch: SFX_PREFIX['st_glitch'],
  transition_video: SFX_ISVIDEO['st_transition'] === true,
  sound_fields: Object.keys(SFX_PREFIX).sort()
}));
"""


@node
def test_file_field_without_sfx_is_not_a_sound(tmp_path):
    """5. Подложка (insert_plate_file) — не звук: её выбор не открывает «Настройку звука»,
    а все звуковые поля схемы по-прежнему в картах."""
    prelude = ("const BASE=%s;\nconst LAYERS=%s;\n"
               % (json.dumps(styles.BASE, ensure_ascii=False),
                  json.dumps(style_schema.LAYERS, ensure_ascii=False))
               + io.open(PANEL_JS, encoding="utf-8").read())
    res = _run_node(tmp_path, "zj_sfx.js", prelude, SFX_MAPS)
    assert res["plate"] is False, "поле подложки попало в SFX_PREFIX — откроется редактор звука"
    assert res["plate_video"] is False, "поле подложки попало в SFX_ISVIDEO"
    assert res["pop"] == "pop" and res["glitch"] == "glitch", res
    assert res["transition"] == "transition" and res["transition_sfx"] == "transition_sfx", res
    assert res["riser"] == "intro_riser", res
    assert res["transition_video"] is True, "переход перестал быть видео"
    # в картах ровно поля со `sfx` из схемы — ни одним больше
    want_sfx = set()

    def walk(items):
        for it in items:
            if it.get("type") == "field" and it.get("sfx"):
                want_sfx.add("st_" + it["key"])
            walk(it.get("items", []) or [])

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    assert set(res["sound_fields"]) == want_sfx, \
        f"карты звуков разошлись со схемой: {res['sound_fields']} vs {sorted(want_sfx)}"


# ------------------------------------------------------------------
# 6. Сторож «каждая ручка»: все поля lm_* живут под галкой lm_on
# ------------------------------------------------------------------

def test_every_lm_knob_reaches_the_jsx(xml_subs, tmp_path):
    """6. Сторож ручек (test_r11_li_every_knob) включает РОДИТЕЛЬСКИЕ тумблеры: у каждой
    ручки lm_* родитель один — lm_on, и без него .jsx не меняется. Здесь то же самое
    проверяется вручную: ручка есть в схеме, её значение доезжает до LUMETRI в .jsx."""
    assert styles.BASE["lm_on"] is False, "Lumetri включён по умолчанию — golden поедет"

    group = _lm_group()
    assert group, "в схеме нет группы с галкой lm_on"
    fields = [it for it in group["items"] if it.get("type") == "field"]
    assert [f["key"] for f in fields] == list(LM_KEYS), \
        "ручки группы Lumetri разошлись с таблицей задания"
    for f in fields:
        assert f["ctl"] == "num" and not f.get("show_if"), \
            f"{f['key']}: ручка обязана быть числом, видимым по галке группы"
        assert "tip" in f
    assert group.get("label") and group.get("tip"), "у группы Lumetri нет подписи или тултипа"

    # без галки ни одна ручка на .jsx не влияет — сторож обязан включать lm_on
    base = _build(xml_subs, tmp_path, style=dict(LM_ALL))
    assert "LUMETRI" not in base, "ручки Lumetri сработали без галки группы"

    ref = _lumetri_from_jsx(_build(xml_subs, tmp_path, style={"lm_on": True}))
    cases = {"lm_exposure": 0.5, "lm_contrast": 5, "lm_highlights": 6, "lm_shadows": -7,
             "lm_whites": 8, "lm_blacks": -9, "lm_temp": 10, "lm_tint": -11, "lm_sat": 120}
    for key in LM_KEYS:
        # в плане и .jsx ключи без приставки lm_: lm_temp -> temp (см. LUMETRI_PARAMS)
        jkey = key[len("lm_"):]
        got = _lumetri_from_jsx(_build(xml_subs, tmp_path,
                                       style={"lm_on": True, key: cases[key]}))
        assert got[jkey] == pytest.approx(cases[key]), \
            f"ручка {key} не доехала до .jsx: {got[jkey]!r}"
        for other in LM_KEYS:
            if other != key:
                assert got[other[len("lm_"):]] == pytest.approx(ref[other[len("lm_"):]]), \
                    f"ручка {key} сдвинула чужое значение {other}"


# ------------------------------------------------------------------
# 7. Цвет Камеры 2: свой блок и цепочка связи «как у Камеры 1»
# ------------------------------------------------------------------

def _lm2_group():
    """Группа схемы с галкой lm2_on — она же несёт ключ связи lm2_link."""
    def walk(items):
        for it in items:
            if it.get("toggle") == "lm2_on":
                return it
            got = walk(it.get("items", []) or [])
            if got:
                return got
        return None

    for layer in style_schema.LAYERS:
        got = walk(layer.get("items", []))
        if got:
            return layer, got
    return None, None


def test_cam2_lm_group_schema():
    """7. Группа Камеры 2: своя галка, ключ связи и девять ручек lm2_* — в разделе Камеры 2.

    Дефолт связи — True: у стиля без ключей lm2_* вид прежний, а «свой» цвет включается
    только осознанным размыканием цепи.
    """
    assert styles.BASE["lm2_link"] is True, "цепочка связи Камеры 2 замкнута не по умолчанию"
    assert styles.BASE["lm2_on"] is False, "свой цвет Камеры 2 включён по умолчанию — golden поедет"

    layer, group = _lm2_group()
    assert group, "в схеме нет группы с галкой lm2_on"
    assert layer["id"] == "cam2", f"группа цвета Камеры 2 уехала в раздел {layer['id']!r}"
    assert group["id"] == "cam2.lm"
    assert group["link"] == "lm2_link", "у группы Камеры 2 нет ключа связи с Камерой 1"
    assert group.get("label") and group.get("tip"), "у группы нет подписи или тултипа"

    fields = [it for it in group["items"] if it.get("type") == "field"]
    assert [f["key"] for f in fields] == list(LM2_KEYS), \
        "ручки цвета Камеры 2 разошлись с таблицей"
    for f in fields:
        assert f["ctl"] == "num" and not f.get("show_if"), \
            f"{f['key']}: ручка обязана быть числом, видимым по галке группы"

    # Раздел Камеры 2: Transform и анимация (зум/наезды) стоят ВЫШЕ цвета
    ids = [it.get("id") for it in layer["items"]]
    assert ids.index("cam2.lm") > ids.index("cam2.tr"), "цвет Камеры 2 стоит до её Transform"
    assert ids.index("cam2.lm") > ids.index("cam2.zoom"), "цвет Камеры 2 стоит до группы cam2.zoom"
    c2_zoom = next(it for it in layer["items"] if it.get("id") == "cam2.zoom")
    z_ids = [it.get("id") for it in c2_zoom.get("items", [])]
    for zoom_id in ("cam2.zoom_start", "cam2.zoom_cut", "cam2.zoom_take"):
        assert zoom_id in z_ids, f"в cam2.zoom отсутствует подгруппа {zoom_id}"


def test_lm2_default_jsx_is_the_main_one(xml_subs, tmp_path):
    """7. Замкнутая цепь (дефолт) — .jsx побайтово прежний, отдельного цвета в плане нет.

    Ключей lm2_* в старом стиле нет вовсе, а записанные (галка включена, числа заданы)
    на сборку не влияют: цвет Камеры 2 берётся у Камеры 1.
    """
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path, style={})) == golden, \
        "стиль по умолчанию разошёлся с эталоном"

    linked = dict(LM2_ALL)
    linked["lm2_on"] = True                      # галка есть, но цепь замкнута
    jsx = _build(xml_subs, tmp_path, style=linked)
    assert _mask_assets(jsx) == golden, "замкнутая цепь не погасила свой цвет Камеры 2"
    assert "LUMETRI2" not in jsx and "applyLumetri2" not in jsx, \
        "при замкнутой цепи в .jsx появился свой цвет Камеры 2"

    assert "lumetri2" not in _plan(xml_subs, style={}), "план несёт отдельный цвет Камеры 2"
    assert "lumetri2" not in _plan(xml_subs, style=linked), \
        "план несёт отдельный цвет Камеры 2 при замкнутой цепи"


def test_lm2_unlinked_gives_camera2_its_own_color(xml_subs, tmp_path):
    """7. Разомкнутая цепь: LUMETRI2 — на клипы и рото Камеры 2, LUMETRI — на Камеру 1."""
    st = dict(LM_ALL)
    st.update(LM2_ALL)
    st["lm_on"] = True
    st["lm2_on"] = True
    st["lm2_link"] = False
    jsx = _build(xml_subs, tmp_path, style=st, exposure=CLIP_EXPOSURE)

    got1 = _lumetri_from_jsx(jsx, "LUMETRI")
    got2 = _lumetri_from_jsx(jsx, "LUMETRI2")
    want1 = {"exposure": LM_ALL["lm_exposure"] + CLIP_EXPOSURE}
    want2 = {"exposure": LM2_ALL["lm2_exposure"] + CLIP_EXPOSURE}
    for key in LM_KEYS:
        jkey = key[len("lm_"):]
        want1.setdefault(jkey, LM_ALL[key])
        want2.setdefault(jkey, LM2_ALL["lm2_" + jkey])
    assert got1 == pytest.approx(want1), f"набор Камеры 1 поехал: {got1}"
    assert got2 == pytest.approx(want2), f"набор Камеры 2 не тот: {got2}"
    assert set(got2) == {key for key, _m, _l in LUMETRI_PARAMS}
    assert got1 != got2, "наборы камер совпали — тест не отличает свой цвет от чужого"

    # Каждая камера — своим вызовом, и это разветвление, а не второй проход по всем слоям
    assert jsx.count("function applyLumetri(") == 1
    assert jsx.count("function applyLumetri2(") == 1
    assert "if (isSecond){" in jsx and "applyLumetri2(lay);" in jsx, \
        "клипы Камеры 2 не получают свой Lumetri"
    assert "if (ci==1){" in jsx and "applyLumetri2(cc);" in jsx, \
        "рото-копии Камеры 2 не получают свой Lumetri"
    assert "applyLumetri(lay);" in jsx and "applyLumetri(cc);" in jsx, \
        "Камера 1 потеряла свой Lumetri"
    assert "if (EXPOSURE!=0)" not in jsx, \
        "вместе с Lumetri осталась покадровая экспозиция — цвет ставился бы дважды"

    # Камера 1 без своего цвета, Камера 2 — со своим: объявлен только LUMETRI2,
    # а на клипах и рото Камеры 1 остаётся прежняя покадровая экспозиция
    only2 = dict(LM2_ALL)
    only2.update({"lm_on": False, "lm2_on": True, "lm2_link": False})
    jsx2 = _build(xml_subs, tmp_path, style=only2)
    assert "var LUMETRI2" in jsx2 and "var LUMETRI =" not in jsx2, \
        "объявление LUMETRI появилось при снятой галке Камеры 1"
    assert "applyLumetri2(lay);" in jsx2 and "applyLumetri2(cc);" in jsx2
    assert "applyLumetri(lay);" not in jsx2 and "applyLumetri(cc);" not in jsx2, \
        "Камера 1 получила цвет при снятой галке"
    assert "if (EXPOSURE!=0)" in jsx2, "на Камере 1 пропала прежняя покадровая экспозиция"


@node
def test_lm2_unlinked_jsx_is_syntactically_valid(xml_subs, tmp_path):
    """7. Ветвление «клип Камеры 2 / остальные» в отрендеренном .jsx — целый синтаксис.

    Разомкнутая цепь вставляет в шаблон ветку по камере, а битый .jsx роняет импорт и
    весь проект AE — узнать об этом иначе можно только открыв Adobe.
    """
    st = dict(LM_ALL)
    st.update(LM2_ALL)
    st.update({"lm_on": True, "lm2_on": True, "lm2_link": False})
    out = str(tmp_path / "cam2_color.jsx")
    xml2ae.to_ae_full(xml_subs, out,
                      inserts=[dict(x) for x in INS], style=st, disclaimer="",
                      intro_riser=False, exposure=CLIP_EXPOSURE,
                      emit=lambda *a, **k: None)
    check = out + ".check.js"                 # node --check не принимает расширение .jsx
    shutil.copy(out, check)
    p = subprocess.run(["node", "--check", check], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, \
        f"отрендеренный .jsx не прошёл node --check:\n{p.stderr.strip()[:500]}"


def test_lm2_linked_build_equals_camera1_only(xml_subs, tmp_path):
    """7. Цепь замкнута при заданных lm2_*: сборка ровно та же, что без них."""
    ref_st = dict(LM_ALL)
    ref_st["lm_on"] = True
    linked_st = dict(ref_st)
    linked_st.update(LM2_ALL)
    linked_st["lm2_on"] = True
    linked_st["lm2_link"] = True
    assert _build(xml_subs, tmp_path, style=linked_st) == \
        _build(xml_subs, tmp_path, style=ref_st), \
        "свои значения Камеры 2 просочились в сборку при замкнутой цепи"


def test_lm2_off_unlinked_is_the_exposure_line_again(xml_subs, tmp_path):
    """7. Снятая галка Камеры 2 при разомкнутой цепи — прежняя строка EXPOSURE, как у lm_on.

    Обе галки сняты и цепь разомкнута — .jsx побайтово прежний: подстановок цвета нет.
    """
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    both_off = dict(LM2_ALL)
    both_off.update({"lm_on": False, "lm2_on": False, "lm2_link": False})
    assert _mask_assets(_build(xml_subs, tmp_path, style=both_off)) == golden, \
        "разомкнутая цепь со снятыми галками изменила .jsx"

    st = dict(LM_ALL)
    st.update(LM2_ALL)
    st["lm_on"] = True
    st["lm2_on"] = False
    st["lm2_link"] = False
    jsx = _build(xml_subs, tmp_path, style=st)
    assert "LUMETRI2" not in jsx and "applyLumetri2" not in jsx, \
        "снятая галка Камеры 2 не погасила её Lumetri"
    assert "if (EXPOSURE!=0)" in jsx, "на Камере 2 пропала прежняя покадровая экспозиция"
    assert "applyLumetri(lay);" in jsx and "applyLumetri(cc);" in jsx, \
        "Камера 1 потеряла свой Lumetri"
    assert "lumetri2" in _plan(xml_subs, style=st), \
        "план не различает «цепь разомкнута, галка снята» и «цепь замкнута»"
    assert _plan(xml_subs, style=st)["lumetri2"] is None


def test_plan_lumetri2_only_when_unlinked(xml_subs):
    """7. plan["lumetri2"]: нет при замкнутой цепи, None при снятой галке, иначе dict."""
    st = dict(LM_ALL)
    st.update(LM2_ALL)
    st["lm_on"] = True
    st["lm2_on"] = True
    st["lm2_link"] = False
    plan = _plan(xml_subs, style=st, exposure=CLIP_EXPOSURE)

    lum2 = plan["lumetri2"]
    assert isinstance(lum2, dict), "план не несёт значения цвета Камеры 2"
    assert set(lum2) == {key for key, _m, _l in LUMETRI_PARAMS}, sorted(lum2)
    assert lum2["exposure"] == pytest.approx(LM2_ALL["lm2_exposure"] + CLIP_EXPOSURE), \
        "экспозиция клипа не прибавилась к стилевой Камеры 2"
    assert lum2["sat"] == pytest.approx(LM2_ALL["lm2_sat"])
    assert lum2["temp"] == pytest.approx(LM2_ALL["lm2_temp"])
    assert plan["lumetri"]["exposure"] == pytest.approx(LM_ALL["lm_exposure"] + CLIP_EXPOSURE), \
        "набор Камеры 1 подменился набором Камеры 2"


# ------------------------------------------------------------------
# 8. Превью: фильтр по камере (node, боевой static/app/85-inserts-view.js)
# ------------------------------------------------------------------

FILTER_CAM2 = _FILTER_DOM + r"""
const zero = {exposure:0, contrast:0, highlights:0, shadows:0, whites:0, blacks:0,
              temp:0, tint:0, sat:100};
const l1 = Object.assign({}, zero, {temp:50});
const l2 = Object.assign({}, zero, {temp:-50, sat:150});
// Камеры нет вовсе (ни своей, ни общей) — фильтра нет
global.IPV = {plan: {lumetri: null}};
const none = ipvLumetriFilter(1);
// Цепь замкнута: отдельного ключа lumetri2 в плане нет — Камера 2 берёт цвет Камеры 1
IPV.plan = {lumetri: l1};
const cam2Linked = ipvLumetriFilter(1);
const cam2LinkedId = byId['ipvLumetriSvg'].attrs.id;
const buildsLinked = builds;
// Цепь разомкнута: у Камеры 2 свой фильтр с её числами
IPV.plan = {lumetri: l1, lumetri2: l2};
const cam2Own = ipvLumetriFilter(1);
const buildsOwn = builds;
const ownMarkup = byId['ipvLumetri2Svg'].innerHTML;
const cam1 = ipvLumetriFilter(0);
const cam2Again = ipvLumetriFilter(1);
const buildsCached = builds;
// Цепь разомкнута, галка Камеры 2 снята: у неё цвета нет, у Камеры 1 — остался
IPV.plan = {lumetri: l1, lumetri2: null};
const cam2Off = ipvLumetriFilter(1);
const cam1Still = ipvLumetriFilter(0);
console.log(JSON.stringify({none: none, cam2Linked: cam2Linked, cam2LinkedId: cam2LinkedId,
  buildsLinked: buildsLinked, cam2Own: cam2Own, buildsOwn: buildsOwn,
  ownFilterId: ownMarkup.indexOf('<filter id="ipvLumetri2"') === 0,
  ownMatrix: grab(ownMarkup, 'type="matrix" values').split(' '),
  ownSat: grab(ownMarkup, 'type="saturate" values'),
  cam1: cam1, cam2Again: cam2Again, buildsCached: buildsCached,
  cam2Off: cam2Off, cam1Still: cam1Still}));
"""


@node
def test_node_filter_cam2_from_plan(tmp_path):
    """8. Фильтр превью камеры 2: свой — только при разомкнутой цепи, иначе цвет Камеры 1.

    Ключа lumetri2 в плане нет — цепь замкнута, и Камера 2 показывает цвет Камеры 1
    (тот же фильтр). Ключ есть — свой фильтр `ipvLumetri2` с ЕЁ числами. Ключ равен null
    (галка снята) — цвета у Камеры 2 нет вовсе, даже когда у Камеры 1 он есть.
    """
    res = _run_node(tmp_path, "zj_filter_cam2.js", _prelude_view(), FILTER_CAM2)
    assert res["none"] == "none", "без плана фильтр не отключился"
    assert res["cam2Linked"] == "url(#ipvLumetri)", \
        "при замкнутой цепи Камера 2 не взяла фильтр Камеры 1"
    assert res["cam2LinkedId"] == "ipvLumetriSvg"
    assert res["cam2Own"] == "url(#ipvLumetri2)", "свой цвет Камеры 2 не завёл свой фильтр"
    assert res["ownFilterId"], "фильтр Камеры 2 собран под чужим id"
    assert res["cam1"] == "url(#ipvLumetri)" and res["cam2Again"] == "url(#ipvLumetri2)", \
        "фильтры камер перепутались при переключении"
    # Камера 2 — свои числа (temp=-50: R ×0.9, B ×1.1; sat=150 → 1.5), не числа Камеры 1
    m2 = [float(v) for v in res["ownMatrix"]]
    assert m2[0] == pytest.approx(0.9) and m2[12] == pytest.approx(1.1), m2
    assert float(res["ownSat"]) == pytest.approx(1.5)
    # Фильтр пересобирается на смене плана, а не на каждом кадре: две сборки — по одной
    # на камеру, переключение камер их не добавляет
    assert res["buildsLinked"] == 1, "фильтр Камеры 1 собран не один раз"
    assert res["buildsOwn"] == 2, "свой фильтр Камеры 2 не собрался"
    assert res["buildsCached"] == 2, "переключение камер пересобирает фильтры"
    # Разомкнуто со снятой галкой: Камера 2 — без цвета, Камера 1 — со своим
    assert res["cam2Off"] == "none", "снятая галка Камеры 2 не отключила её фильтр"
    assert res["cam1Still"] == "url(#ipvLumetri)", "фильтр Камеры 1 пострадал от Камеры 2"


# ------------------------------------------------------------------
# 9. Панель: цепочка связи (node, боевой static/app/94-stylepanel.js)
# ------------------------------------------------------------------

PANEL_GLOBALS = r"""
global.$ = (id) => document.getElementById(id);
global.esc = (s) => String(s == null ? '' : s);
global.captureAE = () => {};
global.applyStyleToUI = () => {};
"""

PANEL_LINK = DOM_STUB + PANEL_GLOBALS + r"""
// Стиль без ключей lm2_* — как у старых стилей: цепь замкнута, цвет Камеры 2 = цвет Камеры 1.
const CAM1 = {lm_on: true, lm_exposure: 0.7, lm_contrast: 12, lm_highlights: 20,
              lm_shadows: 30, lm_whites: -40, lm_blacks: -50, lm_temp: 25,
              lm_tint: -35, lm_sat: 130};
buildPanel();
CURSTYLE = Object.assign(JSON.parse(JSON.stringify(BASE)), CAM1, {label: 'цепь'});
for (const k of Object.keys(CURSTYLE)) {
  if (k.indexOf('lm2_') === 0) delete CURSTYLE[k];
}
fillStyleFields();

const el = (id) => document.getElementById(id);
const shown = (p) => String(el('st_lm2_' + p + '_val').textContent);
const start = {
  keyAbsent: !('lm2_link' in CURSTYLE),
  disabledInput: el('st_lm2_exposure_input').disabled,
  disabledSlider: el('st_lm2_exposure_slider').disabled,
  disabledToggle: el('st_lm2_on').disabled,
  rowGrey: el('strow_lm2_exposure').classList.contains('stdisabled'),
  shownExposure: shown('exposure'),
  shownSat: shown('sat'),
  shownOn: el('st_lm2_on').checked,
  btnUnlinked: el('st_lm2_link').classList.contains('st-unlinked')
};

// Разомкнули: lm2_* и lm2_on получают ТЕКУЩИЕ значения Камеры 1, поля — редактируемы
stToggleCam2Link();
const unlinked = {
  linkFalse: CURSTYLE.lm2_link === false,
  on: CURSTYLE.lm2_on,
  exposure: CURSTYLE.lm2_exposure,
  sat: CURSTYLE.lm2_sat,
  temp: CURSTYLE.lm2_temp,
  disabledInput: el('st_lm2_exposure_input').disabled,
  shownExposure: shown('exposure'),
  btnUnlinked: el('st_lm2_link').classList.contains('st-unlinked'),
  btnLinked: el('st_lm2_link').classList.contains('st-linked')
};

// Замкнули обратно: своё значение остаётся в стиле, а поле снова показывает Камеру 1
CURSTYLE.lm2_exposure = 0.1;
stToggleCam2Link();
const relinked = {
  linkTrue: CURSTYLE.lm2_link === true,
  ownKept: CURSTYLE.lm2_exposure,
  shownExposure: shown('exposure'),
  disabledInput: el('st_lm2_exposure_input').disabled
};

// Разомкнули, задали своё и нажали «Сброс» группы: цепь замыкается, своё не стирается
stToggleCam2Link();
CURSTYLE.lm2_exposure = 0.25;
stReset('cam2.lm');
const afterReset = {
  linkTrue: CURSTYLE.lm2_link === true,
  ownKept: CURSTYLE.lm2_exposure,
  shownExposure: shown('exposure'),
  disabledInput: el('st_lm2_exposure_input').disabled
};

console.log(JSON.stringify({start: start, unlinked: unlinked, relinked: relinked,
                            afterReset: afterReset}));
"""


@node
def test_node_panel_link_chain(tmp_path):
    """9. Панель: размыкание копирует текущие значения Камеры 1, «Сброс» замыкает цепь.

    Замкнуто — поля серые и показывают Камеру 1; разомкнуто — свои значения, поля
    редактируемы; замкнуто снова — своё остаётся в стиле, а показ возвращается к Камере 1.
    """
    prelude = ("const BASE=%s;\nconst LAYERS=%s;\n"
               % (json.dumps(styles.BASE, ensure_ascii=False),
                  json.dumps(style_schema.LAYERS, ensure_ascii=False))
               + io.open(PANEL_JS, encoding="utf-8").read()
               + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n")
    res = _run_node(tmp_path, "zj_panel_link.js", prelude, PANEL_LINK)

    s = res["start"]
    assert s["keyAbsent"], "стиль без lm2_link уже несёт ключ связи"
    assert s["disabledInput"] and s["disabledSlider"] and s["disabledToggle"], \
        "при замкнутой цепи поля Камеры 2 не серые"
    assert s["rowGrey"], "строка поля Камеры 2 не помечена серой"
    assert s["shownExposure"] == "0.7" and s["shownSat"] == "130", \
        f"при замкнутой цепи поля показывают не Камеру 1: {s}"
    assert s["shownOn"] is True, "галка Камеры 2 не повторила галку Камеры 1"
    assert s["btnUnlinked"] is False, "значок связи показывает разомкнутую цепь"

    u = res["unlinked"]
    assert u["linkFalse"], "размыкание не записало lm2_link=False"
    assert u["on"] is True and u["exposure"] == pytest.approx(0.7), \
        f"размыкание не скопировало значения Камеры 1: {u}"
    assert u["sat"] == pytest.approx(130) and u["temp"] == pytest.approx(25)
    assert u["disabledInput"] is False, "после размыкания поле осталось серым"
    assert u["btnUnlinked"] and not u["btnLinked"], "значок не переключился на разомкнутую цепь"

    r = res["relinked"]
    assert r["linkTrue"], "замыкание не записало lm2_link=True"
    assert r["ownKept"] == pytest.approx(0.1), "замыкание стёрло своё значение Камеры 2"
    assert r["shownExposure"] == "0.7", "замкнутое поле показывает не Камеру 1"
    assert r["disabledInput"] is True, "замкнутое поле снова редактируемо"

    a = res["afterReset"]
    assert a["linkTrue"], "«Сброс» группы Камеры 2 не замкнул цепь"
    assert a["ownKept"] == pytest.approx(0.25), "«Сброс» стёр своё значение Камеры 2"
    assert a["shownExposure"] == "0.7" and a["disabledInput"] is True
