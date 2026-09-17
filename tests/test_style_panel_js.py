# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панель стиля (задание JB): пересчёт «хранимое <-> показанное» на чистых функциях.

Панель строит поля по схеме (`core/style_schema.py`, `/api/style_schema`), и значение
каждого поля ходит двумя дверями: `stView(field, stored)` — что показать,
`stStore(field, view)` — что записать в CURSTYLE. Ошибка здесь тихая: стиль открылся,
поле показывает одно, а в файл уезжает другое (или не уезжает вовсе) — на глаз не
видно, ловится только сверкой.

Тест гоняет БОЕВОЙ `static/app/94-stylepanel.js` под node (схема подставляется из
`core.style_schema`, дефолты — из `core.styles`), а не переписывает формулы: иначе он
сторожил бы сам себя. Проверяется:

- round-trip `stStore(field, stView(field, v)) == v` для каждого поля схемы на
  `styles.BASE` и на `GEOLOGICA` (парный ключ пары X/Y проверяется вместе с полем);
- все шесть `conv` из таблицы JA п. 2 на граничных значениях кадра H=1920, W=1080;
- `music_db = -105` (дополнение JA-1): `min`/`max` ограничивают только ползунок, а
  значение из стиля при показе не обрезается — в трёх шаблонах music_db = −100…−105,
  и старое числовое поле это держало.

Запуск: python -m pytest reelsi/tests -q
"""
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import style_schema, styles  # noqa: E402

PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт панели требует node в PATH")


def _panel_src():
    """Боевой код панели: тест проверяет его, а не свою копию формул."""
    return io.open(PANEL_JS, encoding="utf-8").read()


def _env_src():
    """Схема и стили — тем же JSON, что уезжает в браузер по /api/style_schema."""
    return (
        "const BASE=%s;\nconst GEOLOGICA=%s;\nconst LAYERS=%s;\n"
        % (
            json.dumps(styles.BASE, ensure_ascii=False),
            json.dumps(styles.GEOLOGICA, ensure_ascii=False),
            json.dumps(style_schema.LAYERS, ensure_ascii=False),
        )
    )


def _run_node(tmp_path, name, body):
    """Панель + подставленная схема + проверка — одним файлом под node."""
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(_env_src())
        f.write(_panel_src())
        f.write("\nSTSCHEMA = {base: BASE, layers: LAYERS};\n")
        f.write(body)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:800]
    return json.loads(p.stdout.strip().splitlines()[-1])


def _schema_fields():
    """Все поля схемы (key + key2 пары X/Y) — так же, как их обходит панель."""
    out = []

    def walk(items):
        for it in items:
            if it.get("type") == "group":
                walk(it.get("items", []))
            elif it.get("type") == "field":
                out.append(it)

    for layer in style_schema.LAYERS:
        walk(layer.get("items", []))
    return out


def _schema_convs():
    return sorted({f["conv"] for f in _schema_fields() if f.get("conv")})


ROUNDTRIP = r"""
function allFields(){
  const out=[];
  function walk(items){
    for(const it of items){
      if(it.type==='group')walk(it.items||[]);
      else if(it.type==='field')out.push(it);
    }
  }
  for(const layer of STSCHEMA.layers) walk(layer.items||[]);
  return out;
}
const fields=allFields();
// Пусто у nullable-поля записано в styles.BASE двояко: "" (accent_font, back_font) и
// null (intro_font). Для панели это одно и то же «пусто», и старое поле вело себя так же
// (stEdit писал `g(id)||null`). Это разница ПРЕДСТАВЛЕНИЯ, а не значения: сверяем
// остальные значения строго, а пустое принимаем в любой из двух записей.
function same(f,a,b){
  if(JSON.stringify(a)===JSON.stringify(b))return true;
  return !!f.nullable && (a===''||a==null) && (b===''||b==null);
}
const bad=[];
let checked=0;
for(const f of fields){
  for(const pair of [['BASE',BASE],['GEOLOGICA',GEOLOGICA]]){
    const name=pair[0], src=pair[1];
    if(!(f.key in src))continue;
    const v=src[f.key];
    const back=stStore(f,stView(f,v));
    checked++;
    if(!same(f,back,v))
      bad.push(name+'.'+f.key+': '+JSON.stringify(v)+' -> '+JSON.stringify(back));
  }
  // Пара X/Y — одно поле строки: оба ключа обязаны пережить круг
  if(f.key2 && (f.key2 in BASE)){
    const v2=BASE[f.key2];
    const back2=stStore(f,stView(f,v2));
    checked++;
    if(!same(f,back2,v2))
      bad.push('BASE.'+f.key2+': '+JSON.stringify(v2)+' -> '+JSON.stringify(back2));
  }
}
console.log(JSON.stringify({fields:fields.length,checked:checked,bad:bad}));
"""


@node
def test_every_field_survives_the_round_trip(tmp_path):
    """stStore(stView(v)) == v для каждого поля на styles.BASE и на GEOLOGICA.

    Круг рвётся там, где панель молча меняет значение: показ обрезан ползунком,
    цвет не возвращается в тот же rgb, «пусто» превращается в 0, а строка — в число.
    """
    res = _run_node(tmp_path, "panel_roundtrip.js", ROUNDTRIP)
    assert res["fields"] == len(_schema_fields()), (
        "панель видит не все поля схемы: %s из %s" % (res["fields"], len(_schema_fields())))
    assert res["checked"] >= res["fields"], "проверено меньше полей, чем есть в схеме"
    assert not res["bad"], "поля не переживают круг stView/stStore:\n" + "\n".join(res["bad"])


CONVS = r"""
const H=1920, W=1080;
const out={};
out.inv_pct=[stConv.inv_pct.toView(0.5964),stConv.inv_pct.toStore(40),
             stConv.inv_pct.toView(0.5),stConv.inv_pct.toStore(50),
             stConv.inv_pct.toView(0.75),stConv.inv_pct.toStore(25)];
out.pct_h=[stConv.pct_h.toView(0,H),stConv.pct_h.toStore(0,H),
           stConv.pct_h.toView(768,H),stConv.pct_h.toStore(40,H),
           stConv.pct_h.toView(-960,H),stConv.pct_h.toStore(-50,H),
           stConv.pct_h.toView(1920,H),stConv.pct_h.toStore(100,H)];
out.pct_fx=[stConv.pct_fx.toView(0,W),stConv.pct_fx.toStore(0,W),
            stConv.pct_fx.toView(540,W),stConv.pct_fx.toStore(50,W),
            stConv.pct_fx.toView(1080,W),stConv.pct_fx.toStore(100,W)];
out.pct_fy=[stConv.pct_fy.toView(0,H),stConv.pct_fy.toStore(0,H),
            stConv.pct_fy.toView(960,H),stConv.pct_fy.toStore(50,H),
            stConv.pct_fy.toView(1920,H),stConv.pct_fy.toStore(100,H)];
out.frac_pct=[stConv.frac_pct.toView(0),stConv.frac_pct.toStore(0),
              stConv.frac_pct.toView(0.25),stConv.frac_pct.toStore(25),
              stConv.frac_pct.toView(1),stConv.frac_pct.toStore(100)];
out.frac_pct_int=[stConv.frac_pct_int.toView(0),stConv.frac_pct_int.toStore(0),
                  stConv.frac_pct_int.toView(0.25),stConv.frac_pct_int.toStore(25),
                  stConv.frac_pct_int.toView(0.75),stConv.frac_pct_int.toStore(75)];
console.log(JSON.stringify(out));
"""


@node
def test_all_six_convs_on_frame_bounds(tmp_path):
    """Шесть conv с реальными H=1920 / W=1080 (а не с фолбэками «плана нет»)."""
    res = _run_node(tmp_path, "panel_convs.js", CONVS)
    assert sorted(res) == _schema_convs(), (
        "проверены не те conv, что есть в схеме: %s" % sorted(res))
    assert res["inv_pct"] == [40, 0.5964, 50, 0.5, 25, 0.75], res["inv_pct"]
    assert res["pct_h"] == [0, 0, 40, 768, -50, -960, 100, 1920], res["pct_h"]
    assert res["pct_fx"] == [0, 0, 50, 540, 100, 1080], res["pct_fx"]
    assert res["pct_fy"] == [0, 0, 50, 960, 100, 1920], res["pct_fy"]
    assert res["frac_pct"] == [0, 0, 25, 0.25, 100, 1], res["frac_pct"]
    assert res["frac_pct_int"] == [0, 0, 25, 0.25, 75, 0.75], res["frac_pct_int"]


MUSIC_DB = r"""
const f={ctl:'num',key:'music_db',min:-40,max:6,step:0.5};
const out={};
out.view=stView(f,-105);                 // показ: значение из стиля не обрезается
out.back=stStore(f,stView(f,-105));      // круг: -105 остаётся -105
out.explicit=stStore(f,stView(f,-105));  // те же двери с явным полем
out.str=stStore(f,'-105');
out.base=stView(f,BASE.music_db);
out.empty=stStore(f,'');
console.log(JSON.stringify(out));
"""


@node
def test_music_db_below_slider_range_survives(tmp_path):
    """music_db = -105: min/max — это только диапазон ПОЛЗУНКА.

    В трёх шаблонах music_db = −100…−105 (тише, чем позволяет ползунок −40). Панель
    обязана показать и сохранить значение как есть: обрезав его при показе, она бы
    молча подняла музыку на 65 дБ при первом же сохранении стиля.
    """
    res = _run_node(tmp_path, "panel_music_db.js", MUSIC_DB)
    assert res["view"] == -105, "показ обрезал значение по min/max ползунка"
    assert res["back"] == -105, "круг stView/stStore потерял -105"
    assert res["explicit"] == -105, "stStore(f, stView(f, -105)) != -105"
    assert res["str"] == -105, "значение из строки (ввод с клавиатуры) обрезано"
    assert res["base"] == styles.BASE["music_db"], "дефолт BASE потерялся"
    assert res["empty"] == styles.BASE["music_db"], "пустое поле не вернулось к дефолту"


# ------------------------------------------------------------------
# JB-3 пп. A1-A4: полный цикл fillStyleFields → stEdit на мини-DOM
# ------------------------------------------------------------------

DOM_STUB = r"""
let elements = {};
function getEl(id) {
  if (!elements[id]) {
    elements[id] = {
      id: id,
      value: '',
      checked: false,
      textContent: '',
      style: { display: '' },
      classList: { toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false },
      setAttribute: () => {},
      getAttribute: () => null,
      querySelector: () => ({ setAttribute: () => {} }),
      querySelectorAll: () => [],
      dataset: {}
    };
  }
  return elements[id];
}
global.document = {
  getElementById: (id) => getEl(id),
  querySelector: () => ({ classList: { toggle: () => {} }, style: {}, setAttribute: () => {} }),
  querySelectorAll: () => []
};
global.window = global;
global.t = s => s;

function runCycle(initial) {
  elements = {};
  CURSTYLE = JSON.parse(JSON.stringify(initial));
  fillStyleFields();
  stEdit();
  return CURSTYLE;
}

function allSchemaKeys(items) {
  const out = [];
  for (const it of items) {
    if (it.toggle) out.push(it.toggle);
    if (it.key) out.push(it.key);
    if (it.key2) out.push(it.key2);
    if (it.items) out.push(...allSchemaKeys(it.items));
  }
  return out;
}

function stRgb2hexLocal(a) {
  const c = x => ('0' + Math.round(Math.max(0, Math.min(1, x || 0)) * 255).toString(16)).slice(-2);
  a = a || [1, 0.9176, 0];
  return '#' + c(a[0]) + c(a[1]) + c(a[2]);
}
"""

FULL_CYCLE_EMPTY = DOM_STUB + r"""
// (а) стиль {} → после «поля ← стиль → стиль» каждый ключ = BASE[key]
const cur = runCycle({});
const keys = allSchemaKeys(STSCHEMA.layers);
const bad = [];

for (const k of keys) {
  const exp = BASE[k];
  const act = cur[k];
  if (k === 'disclaimer') {
    if (act !== null) bad.push(k + ': expected null, got ' + JSON.stringify(act));
    continue;
  }
  if (Array.isArray(exp) && exp.length === 3 && typeof exp[0] === 'number') {
    // цвет
    const h1 = stRgb2hexLocal(act).toLowerCase();
    const h2 = stRgb2hexLocal(exp).toLowerCase();
    if (h1 !== h2) bad.push(k + ': hex ' + h1 + ' != ' + h2);
    continue;
  }
  if (Array.isArray(exp)) {
    if (JSON.stringify(exp) !== JSON.stringify(act))
      bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
    continue;
  }
  if (exp === '' || exp === null) {
    if (act !== '' && act !== null)
      bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
    continue;
  }
  if (JSON.stringify(exp) !== JSON.stringify(act)) {
    bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
  }
}
console.log(JSON.stringify({keys: keys.length, bad: bad}));
"""


@node
def test_empty_style_fills_from_base(tmp_path):
    """(а) стиль {} → каждый ключ после fillStyleFields→stEdit равен BASE[key]."""
    res = _run_node(tmp_path, "panel_empty.js", FULL_CYCLE_EMPTY)
    assert res["keys"] >= len(_schema_fields()), (
        "проверено меньше ключей схемы: %s" % res["keys"])
    assert not res["bad"], "ключи отличаются от BASE:\n" + "\n".join(res["bad"])


FULL_CYCLE_GEOLOGICA = DOM_STUB + r"""
// (б) GEOLOGICA (неполный) → ключи, которых в нём нет, = BASE[key]
const cur = runCycle(GEOLOGICA);
const keys = allSchemaKeys(STSCHEMA.layers);
const bad = [];
let checked = 0;

for (const k of keys) {
  if (k in GEOLOGICA) continue;  // ключ есть в GEOLOGICA — проверяется в roundtrip
  checked++;
  const exp = BASE[k];
  const act = cur[k];
  if (k === 'disclaimer') {
    if (act !== null) bad.push(k + ': expected null, got ' + JSON.stringify(act));
    continue;
  }
  if (Array.isArray(exp) && exp.length === 3 && typeof exp[0] === 'number') {
    const h1 = stRgb2hexLocal(act).toLowerCase();
    const h2 = stRgb2hexLocal(exp).toLowerCase();
    if (h1 !== h2) bad.push(k + ': hex ' + h1 + ' != ' + h2);
    continue;
  }
  if (Array.isArray(exp)) {
    if (JSON.stringify(exp) !== JSON.stringify(act))
      bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
    continue;
  }
  if (exp === '' || exp === null) {
    if (act !== '' && act !== null)
      bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
    continue;
  }
  if (JSON.stringify(exp) !== JSON.stringify(act)) {
    bad.push(k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
  }
}
console.log(JSON.stringify({checked: checked, bad: bad}));
"""


@node
def test_geologica_missing_keys_fill_from_base(tmp_path):
    """(б) GEOLOGICA — неполный: ключи, которых нет, должны = BASE."""
    res = _run_node(tmp_path, "panel_geo.js", FULL_CYCLE_GEOLOGICA)
    assert res["checked"] > 0
    assert not res["bad"], "ключи GEOLOGICA отличаются от BASE:\n" + "\n".join(res["bad"])


DISCLAIMER = DOM_STUB + r"""
// (в) disclaimer: null → null, "" → "", "Текст" → "Текст"
// Полный цикл fillStyleFields → stEdit на мини-DOM
const dNull = runCycle({ disclaimer: null });
const dEmpty = runCycle({ disclaimer: '' });
const dText = runCycle({ disclaimer: 'Текст' });

// Также проверяем прямое сохранение через stStore
const discField = { ctl: 'textarea', key: 'disclaimer', nullable: true };
console.log(JSON.stringify({
  cycle_null: dNull.disclaimer,
  cycle_empty: dEmpty.disclaimer,
  cycle_text: dText.disclaimer,
  raw_null: stStore(discField, null),
  raw_empty: stStore(discField, ''),
  raw_text: stStore(discField, 'Текст')
}));
"""


@node
def test_disclaimer_values(tmp_path):
    """(в) disclaimer: null→null, ''→'', 'Текст'→'Текст'."""
    res = _run_node(tmp_path, "panel_disc.js", DISCLAIMER)
    assert res["cycle_null"] is None, "полный цикл: disclaimer null не вернул null: %s" % res["cycle_null"]
    assert res["cycle_empty"] == "", "полный цикл: disclaimer '' не вернул '': %s" % res["cycle_empty"]
    assert res["cycle_text"] == "Текст", "полный цикл: disclaimer 'Текст' потерялся: %s" % res["cycle_text"]
    assert res["raw_null"] is None, "raw stStore: disclaimer null не вернул null: %s" % res["raw_null"]
    assert res["raw_empty"] == "", "raw stStore: disclaimer '' не вернул '': %s" % res["raw_empty"]
    assert res["raw_text"] == "Текст", "raw stStore: disclaimer 'Текст' потерялся: %s" % res["raw_text"]


MUSIC_DB_CYCLE = DOM_STUB + r"""
// (г) music_db: -105 в полном цикле и с явным полем
const mCycle = runCycle({ music_db: -105 });
const f = { ctl: 'num', key: 'music_db', min: -40, max: 6, step: 0.5 };
const view = stView(f, -105);
const back = stStore(f, view);
console.log(JSON.stringify({
  cycle_val: mCycle.music_db,
  view: view,
  back: back
}));
"""


@node
def test_music_db_explicit_field(tmp_path):
    """(г) music_db: -105 → -105 в полном цикле и с явным полем."""
    res = _run_node(tmp_path, "panel_mdb_expl.js", MUSIC_DB_CYCLE)
    assert res["cycle_val"] == -105, "полный цикл обрезал music_db: %s" % res["cycle_val"]
    assert res["view"] == -105
    assert res["back"] == -105


NO_DIFF_DOTS_BASE = DOM_STUB + r"""
// Проверка JB-4: после fillStyleFields на стиле base ни одна точка не горит
STYLES = { base: BASE };
STYLE_EDITING = 'base';
CURSTYLE = JSON.parse(JSON.stringify(BASE));

const changedDots = [];
const queryElements = {};
global.document.querySelector = (sel) => {
  if (!queryElements[sel]) {
    queryElements[sel] = {
      sel: sel,
      classList: {
        toggle: (cls, on) => {
          if (cls === 'st-changed') {
            const idx = changedDots.indexOf(sel);
            if (on && idx === -1) changedDots.push(sel);
            else if (!on && idx !== -1) changedDots.splice(idx, 1);
          }
        },
        contains: () => false
      },
      style: { display: '' },
      setAttribute: () => {}
    };
  }
  return queryElements[sel];
};

fillStyleFields();
const dotsAfterFill = [...changedDots];

stEdit();
const dotsAfterEdit = [...changedDots];

console.log(JSON.stringify({
  afterFill: dotsAfterFill,
  afterEdit: dotsAfterEdit
}));
"""


@node
def test_no_diff_dots_on_base_style(tmp_path):
    """JB-4: после fillStyleFields на стиле base ни одна точка не горит."""
    res = _run_node(tmp_path, "panel_base_dots.js", NO_DIFF_DOTS_BASE)
    assert res["afterFill"] == [], "горят точки после fillStyleFields: %s" % res["afterFill"]
    assert res["afterEdit"] == [], "горят точки после stEdit: %s" % res["afterEdit"]


DISABLED_TOGGLE_BODY = DOM_STUB + r"""
// JB-4: выключенный тумблер слоя/группы сообщает isNodeOn = false
CURSTYLE = JSON.parse(JSON.stringify(BASE));
CURSTYLE.caption = false;

const captionOff = isNodeOn({ toggle: 'caption' });
CURSTYLE.caption = true;
const captionOn = isNodeOn({ toggle: 'caption' });

CURSTYLE.disclaimer = '';
const discOff = isNodeOn({ toggle: 'disclaimer' });
CURSTYLE.disclaimer = null;
const discOn = isNodeOn({ toggle: 'disclaimer' });

console.log(JSON.stringify({
  captionOff: captionOff,
  captionOn: captionOn,
  discOff: discOff,
  discOn: discOn
}));
"""


@node
def test_disabled_toggle_is_node_off(tmp_path):
    """JB-4: выключенный тумблер слоя/группы сообщает isNodeOn = false."""
    res = _run_node(tmp_path, "panel_toggle_off.js", DISABLED_TOGGLE_BODY)
    assert res["captionOff"] is False
    assert res["captionOn"] is True
    assert res["discOff"] is False
    assert res["discOn"] is True


INCOMPLETE_TEMPLATES_NO_DOTS = DOM_STUB + r"""
// Проверка JB-5: на неполных шаблонах после выбора нет ложных точек «изменено»
const changedDots = [];
const queryElements = {};
global.document.querySelector = (sel) => {
  if (!queryElements[sel]) {
    queryElements[sel] = {
      sel: sel,
      classList: {
        toggle: (cls, on) => {
          if (cls === 'st-changed') {
            const idx = changedDots.indexOf(sel);
            if (on && idx === -1) changedDots.push(sel);
            else if (!on && idx !== -1) changedDots.splice(idx, 1);
          }
        },
        contains: () => false
      },
      style: { display: '' },
      setAttribute: () => {}
    };
  }
  return queryElements[sel];
};

function selectTemplate(tmpl) {
  changedDots.length = 0;
  STYLE_EDITING = null;
  STYLE_EDIT_ORIG = JSON.parse(JSON.stringify(tmpl));
  CURSTYLE = JSON.parse(JSON.stringify(tmpl));
  fillStyleFields();
  return [...changedDots];
}

const dotsBase = selectTemplate(BASE);
const dotsGeo = selectTemplate(GEOLOGICA);
const dotsMin = selectTemplate({ label: 'x', font: 'Y' });

// Правка одного поля (sub_scale) на неполном шаблоне
CURSTYLE.sub_scale = 65;
updateStyleDiffDots();
const dotsAfterEdit = [...changedDots];

console.log(JSON.stringify({
  dotsBase: dotsBase,
  dotsGeo: dotsGeo,
  dotsMin: dotsMin,
  dotsAfterEdit: dotsAfterEdit
}));
"""


@node
def test_incomplete_templates_no_false_diff_dots(tmp_path):
    """JB-5: неполные шаблоны (BASE, GEOLOGICA, {label, font}) без правок дают 0 точек.
    После правки одного поля (sub_scale) точка горит у поля, группы и слоя и больше нигде.
    """
    res = _run_node(tmp_path, "panel_incomplete_dots.js", INCOMPLETE_TEMPLATES_NO_DOTS)
    assert res["dotsBase"] == [], "горят точки на BASE: %s" % res["dotsBase"]
    assert res["dotsGeo"] == [], "горят точки на GEOLOGICA: %s" % res["dotsGeo"]
    assert res["dotsMin"] == [], "горят точки на {label, font}: %s" % res["dotsMin"]
    expected_edit_dots = [
        '[data-dot-key="sub_scale"]',
        '[data-dot-group="subs.tr"]',
        '[data-dot-layer="subs"]',
    ]
    assert sorted(res["dotsAfterEdit"]) == sorted(expected_edit_dots), (
        "после правки sub_scale должны гореть только поле, его группа и слой, получено: %s"
        % res["dotsAfterEdit"]
    )


