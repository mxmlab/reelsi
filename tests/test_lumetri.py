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


def _lumetri_from_jsx(jsx):
    """LUMETRI из .jsx как словарь чисел (в .jsx это литерал объекта, не JSON)."""
    m = re.search(r"var LUMETRI = \{(.*?)\};", jsx)
    assert m, "в .jsx нет объявления var LUMETRI"
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

def _prelude_view():
    """Боевой код превью + константы приближения, вынутые из него же."""
    src = io.open(VIEW_JS, encoding="utf-8").read()
    consts = "".join("const %s=%s;\n" % (name, val)
                     for name, val in re.findall(r"(IPV_LM_\w+)\s*=\s*(-?[\d.]+)", src))
    assert "IPV_LM_HL" in consts and "IPV_LM_BAL" in consts, \
        "в 85-inserts-view.js не нашлись константы приближения Lumetri"
    return (consts + _func(src, "ipvLmSmooth") + "\n" + _func(src, "ipvLumetriTone") + "\n"
            + _func(src, "ipvLumetriTable") + "\n" + _func(src, "ipvLumetriFilter") + "\n")


TONE = r"""
const zero = {exposure:0, contrast:0, highlights:0, shadows:0, whites:0, blacks:0,
              temp:0, tint:0, sat:100};
const xs = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1];
const ident = xs.map(function(x){ return Math.abs(ipvLumetriTone(x, zero) - x); });
const bright = Object.assign({}, zero, {exposure:1});
const mid = [0.25, 0.5].map(function(x){ return ipvLumetriTone(x, bright) - ipvLumetriTone(x, zero); });
const dark100 = Object.assign({}, zero, {shadows:100});
const sh = [0.1, 0.3].map(function(x){ return ipvLumetriTone(x, dark100) - ipvLumetriTone(x, zero); });
const light = [0.9, 1].map(function(x){ return Math.abs(ipvLumetriTone(x, dark100) - ipvLumetriTone(x, zero)); });
console.log(JSON.stringify({ident: ident, mid: mid, sh: sh, light: light}));
"""


@node
def test_node_tone_curve(tmp_path):
    """4. При нулях кривая — тождество; exposure=1 светлее; shadows=+100 поднимает тени,
    а светлые почти не трогает."""
    res = _run_node(tmp_path, "zj_tone.js", _prelude_view(), TONE)
    assert max(res["ident"]) <= 1e-6, f"кривая при нулях не тождество: {res['ident']}"
    assert min(res["mid"]) > 0.1, f"exposure=1 не поднял середину: {res['mid']}"
    assert min(res["sh"]) > 0.05, f"shadows=+100 не поднял тёмные: {res['sh']}"
    assert max(res["light"]) < 0.01, f"shadows=+100 перекрасил светлые: {res['light']}"


FILTER = r"""
// Мини-DOM: только то, что нужно фильтру (svg, атрибуты, innerHTML).
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
    assert res["table"] == 64, f"точек в таблице кривой: {res['table']}, а не 64"
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
