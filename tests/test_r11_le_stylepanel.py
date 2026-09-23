# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панель стиля: двери полей, видимость, рото в долях, сторож перевода.

Панель строится из схемы (`core/style_schema.py` → `/api/style_schema` →
`static/app/94-stylepanel.js`), значения ходят двумя дверями `stView`/`stStore`, а
строки рисует мини-DOM из `tests/test_style_panel_js.py` — теперь с РЕЕСТРОМ
созданных элементов: `getElementById` отдаёт null для id, которого панель не
создавала, как в браузере.

Здесь то, что внешний аудит нашёл незамеченным:

- дверь `stReadView` совпадает с показом для КАЖДОГО типа поля
  (select/font/file/textarea/num/color/bool) — со «заглушкой на любой id» мутация
  «читать `st_<key>_zzz`» не ловилась;
- `updateStyleVisibility` прячет тело группы по тумблеру И раскрытию, а строка
  ползунка возвращается вместе с полем (V2), как у треугольника;
- `rotoSync` пишет в стиль ДОЛЮ, а не показанные проценты (V1): проценты уезжали в
  сборку, `core/roto.py` зажимал их в 1.0, и рото фактически выключалось;
- защита сборки: проценты в уже испорченном состоянии делятся на 100 (api/build.py);
- карты звуков (`SFX_PREFIX`/`SFX_ISVIDEO`) знают НЫНЕШНИЕ ключи схемы, а
  `window.STSCHEMA`/`window.SFX_PREFIX` — живые (геттеры), а не снимки загрузки;
- сторож перевода (`tests/test_i18n.py`) видит строки панели.

Запуск: python -m pytest tests -q
"""
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

from core import app_meta, style_schema, styles  # noqa: E402
from test_i18n import _js_unescape  # noqa: E402
from test_style_panel_js import DOM_STUB  # noqa: E402

PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")
EN_JSON = os.path.join(ROOT, "static", "i18n", "en.json")
CYR = re.compile(r"[А-Яа-яЁё]")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт панели требует node в PATH")


def _src(path):
    return io.open(path, encoding="utf-8").read()


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


# Мелочи, которые в браузере приходят из 00-core.js/20-widgets.js: панель и её
# соседи зовут их как обычные глобальные функции, а тест грузит только два файла
# стиля. Подставлены один в один по смыслу (`$` — это getElementById).
BROWSER_GLOBALS = r"""
global.$ = (id) => document.getElementById(id);
global.esc = (s) => String(s == null ? '' : s);
global.ico = () => '<svg></svg>';
"""


def _run_node(tmp_path, name, body, with_styles=False):
    """Панель (+ при нужде 95-styles.js) и проверка — одним файлом под node.

    Мини-DOM идёт ПЕРВЫМ: 95-styles.js на верхнем уровне вешает
    DOMContentLoaded-слушатели, то есть `document` к этому моменту уже нужен.
    """
    path = str(tmp_path / name)
    src = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS)
    if with_styles:
        src += _src(STYLES_JS)
    src += "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n"
    src += body
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(src)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------
# B3: дверь восстановления — показ и чтение для каждого типа поля
# ------------------------------------------------------------------

DOORS = r"""
const CASES = ['select', 'font', 'file', 'textarea', 'num', 'color', 'bool'];
const CONV_PROBE = { inv_pct: 0.4, pct_h: 700, pct_fx: 900, pct_fy: 800,
                     frac_pct: 0.42, frac_pct_int: 0.42 };
const PROBE = {
  select: (f) => (f.options && f.options.length ? f.options[0][0] : 'probe'),
  font: () => 'Probe-Font',
  file: () => 'C:/probe/sound.wav',
  textarea: () => 'Текст пробы',
  num: (f) => (f.conv && CONV_PROBE[f.conv] != null ? CONV_PROBE[f.conv] : 42),
  color: () => [0.2, 0.4, 0.6],
  bool: () => true
};

function fieldsByCtl(ctl) {
  const out = [];
  function walk(items) {
    for (const it of items) {
      if (it.type === 'group') walk(it.items || []);
      else if (it.type === 'field' && it.ctl === ctl) out.push(it);
    }
  }
  for (const layer of STSCHEMA.layers) walk(layer.items || []);
  return out;
}

const res = { cases: [], bad: [] };
for (const ctl of CASES) {
  const f = fieldsByCtl(ctl).find(x => x.key in BASE);
  if (!f) { res.bad.push('в схеме нет поля ctl=' + ctl); continue; }
  const stored = PROBE[ctl](f);
  buildPanel();
  CURSTYLE = {};
  CURSTYLE[f.key] = stored;
  fillStyleFields();
  const shown = stView(f, stored);       // что панель обязана показывать
  const read = stReadView(f.key);        // что читает общая дверь
  res.cases.push({ ctl: ctl, key: f.key, shown: shown, read: read });
  const same = (shown === read) ||
    (String(shown).toUpperCase() === String(read).toUpperCase());
  if (!same) {
    res.bad.push(ctl + '/' + f.key + ': показ ' + JSON.stringify(shown) +
                 ', дверь ' + JSON.stringify(read));
  }
}

// Сама заглушка: чужой id — null, созданный — элемент (иначе тест выродится).
res.missing_id_is_null = document.getElementById('st_none_zzz') === null;
res.created_id_is_element = !!document.getElementById('st_' + fieldsByCtl('num')[0].key + '_val');
console.log(JSON.stringify(res));
"""


@node
def test_field_doors_match_the_shown_value_for_every_ctl(tmp_path):
    """B3: для select/font/file/textarea/num/color/bool дверь читает то же, что показано.

    Мутация «дверь читает `st_<key>_zzz`» обязана ронять тест: заглушка знает
    только созданные элементы, поэтому чужой id — это null, а не выдуманное поле.
    """
    res = _run_node(tmp_path, "le_doors.js", DOORS)
    assert res["missing_id_is_null"], "заглушка всё ещё выдумывает элементы на любой id"
    assert res["created_id_is_element"], "панель не создала поле, которое должна была"
    assert len(res["cases"]) == 7, "проверены не все типы полей: %s" % res["cases"]
    assert not res["bad"], "дверь stReadView расходится с показом:\n" + "\n".join(res["bad"])


# ------------------------------------------------------------------
# B2: видимость тела группы по тумблеру и раскрытию
# ------------------------------------------------------------------

VISIBILITY = r"""
function firstToggle(kind) {
  for (const layer of STSCHEMA.layers) {
    if (kind === 'layer' && layer.toggle) return { kind: 'layer', id: layer.id, toggle: layer.toggle };
    for (const it of (layer.items || [])) {
      if (kind === 'group' && it.type === 'group' && it.toggle) {
        return { kind: 'group', id: it.id, toggle: it.toggle };
      }
    }
  }
  return null;
}

const node = firstToggle('group') || firstToggle('layer');
buildPanel();
const body = document.getElementById('stbody_' + node.id.replace(/\./g, '_'));

function state(on, expanded) {
  CURSTYLE = CURSTYLE || {};
  CURSTYLE[node.toggle] = on;
  localStorage.setItem('reelsi_sttw_' + node.id, expanded ? '1' : '0');
  updateStyleVisibility();
  return body.style.display;
}

const out = {
  id: node.id,
  toggle: node.toggle,
  off_open: state(false, true),     // тумблер выключен, треугольник раскрыт
  on_closed: state(true, false),    // включён, свёрнут
  on_open: state(true, true)        // включён и раскрыт
};
console.log(JSON.stringify(out));
"""


@node
def test_group_body_follows_toggle_and_expansion(tmp_path):
    """B2: выключен → скрыто; включён и свёрнут → скрыто; включён и раскрыт → видно.

    Мутация `(on && exp)` → `(on || exp)` роняет первый случай: тело выключенной,
    но раскрытой группы показывается.
    """
    res = _run_node(tmp_path, "le_visibility.js", VISIBILITY)
    assert res["off_open"] == "none", "тело выключенной группы видно (%s)" % res["id"]
    assert res["on_closed"] == "none", "тело свёрнутой группы видно (%s)" % res["id"]
    assert res["on_open"] == "", "тело включённой раскрытой группы скрыто (%s)" % res["id"]


# ------------------------------------------------------------------
# B5: строка ползунка возвращается вместе с полем (V2)
# ------------------------------------------------------------------

SLIDER = r"""
function firstNumWithShowIf() {
  let found = null;
  function walk(items) {
    for (const it of items) {
      if (found) return;
      if (it.type === 'group') walk(it.items || []);
      else if (it.type === 'field' && it.ctl === 'num' && it.show_if && it.show_if.key) found = it;
    }
  }
  for (const layer of STSCHEMA.layers) walk(layer.items || []);
  return found;
}

const f = firstNumWithShowIf();
const si = f.show_if;
const visibleVal = (si.eq !== undefined) ? si.eq : (Array.isArray(si.in) ? si.in[0] : null);
const hiddenVal = (si.ne !== undefined) ? si.ne : '__другое__';

buildPanel();
CURSTYLE = {};
const sldRow = document.getElementById('stslider_row_' + f.key);
const tw = document.querySelector('.sttw-field[data-tw="field_' + f.key + '"]');

function setZoom(val) { CURSTYLE[si.key] = val; updateStyleVisibility(); }
function expand(want) {                     // треугольник — та же дверь, что у человека
  if ((tw.getAttribute('aria-expanded') === 'true') !== want) {
    tw.onclick({ stopPropagation: () => {} });
  }
}
function snap() {
  return {
    slider: sldRow.style.display,
    triangle: tw.getAttribute('aria-expanded') === 'true' ? '' : 'none'
  };
}

setZoom(visibleVal);
expand(true);
const visible = snap();

setZoom(hiddenVal);                         // поле ушло
const hidden = snap();

setZoom(visibleVal);                        // вернулось — ползунок обязан вернуться
const back = snap();

expand(false);                              // человек свернул поле
setZoom(hiddenVal);
setZoom(visibleVal);                        // и снова показал: ползунок остаётся скрытым
const back_closed = snap();

const out = {
  key: f.key,
  show_key: si.key,
  visible: visible,
  hidden: hidden,
  back: back,
  back_closed: back_closed
};
out.back_agrees = (out.back.slider === out.back.triangle);
out.back_closed_agrees = (out.back_closed.slider === out.back_closed.triangle);
console.log(JSON.stringify(out));
"""


@node
def test_slider_row_returns_with_the_field(tmp_path):
    """B5 (V2): «drift → pulse → drift» — строка ползунка снова как треугольник.

    Мутация «при vis ничего не восстанавливать» роняет `back`: ползунок остаётся
    скрытым, хотя треугольник раскрыт и поле снова видно.
    """
    res = _run_node(tmp_path, "le_slider.js", SLIDER)
    assert res["visible"]["slider"] == "", "раскрытое поле без строки ползунка"
    assert res["visible"]["triangle"] == ""
    assert res["hidden"]["slider"] == "none", "ползунок остался у скрытого поля"
    assert res["back"]["slider"] == "", (
        "после возврата видимости строка ползунка не вернулась: %s" % res["back"])
    assert res["back_agrees"], "строка ползунка и треугольник разошлись: %s" % res["back"]
    assert res["back_closed_agrees"], (
        "свёрнутое поле: ползунок и треугольник разошлись: %s" % res["back_closed"])


# ------------------------------------------------------------------
# B4: rotoSync пишет долю, а не проценты (V1)
# ------------------------------------------------------------------

ROTO_SYNC = r"""
buildPanel();
let captureCalls = 0;
global.captureAE = () => { captureCalls++; };

CURSTYLE = { roto: true, roto_bottom: 0.35, roto_cam1_only: false };
fillStyleFields();
const shown = document.getElementById('st_roto_bottom_val').textContent;
rotoSync();

console.log(JSON.stringify({
  shown: String(shown),
  stored: CURSTYLE.roto_bottom,
  type_ok: typeof CURSTYLE.roto_bottom === 'number',
  roto: CURSTYLE.roto,
  cam1_only: CURSTYLE.roto_cam1_only,
  capture_calls: captureCalls
}));
"""


@node
def test_roto_sync_writes_fraction_not_percent(tmp_path):
    """B4 (V1): панель показывает 35%, а в стиль уезжает 0.35.

    Мутация «CURSTYLE.roto_bottom = b» роняет тест: в стиль попадают проценты,
    core/roto.py зажимает их в 1.0 — и маска накрывает весь кадр.
    """
    res = _run_node(tmp_path, "le_rotosync.js", ROTO_SYNC, with_styles=True)
    assert res["shown"] == "35", "панель показывает не проценты: %s" % res["shown"]
    assert res["type_ok"], "roto_bottom перестал быть числом"
    assert res["stored"] == 0.35, "в стиль ушло не 0.35, а %r" % res["stored"]
    assert res["roto"] is True
    assert res["cam1_only"] is False
    assert res["capture_calls"] >= 1, "rotoSync не позвал captureAE"


# ------------------------------------------------------------------
# V8/V9: карты звуков и живые window-имена панели
# ------------------------------------------------------------------

SFX_MAPS = r"""
initSfxMaps();
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
console.log(JSON.stringify({
  transition: SFX_PREFIX['st_transition'],
  transition_sfx: SFX_PREFIX['st_transition_sfx'],
  intro_riser: SFX_PREFIX['st_intro_riser_file'],
  pop: SFX_PREFIX['st_pop'],
  glitch: SFX_PREFIX['st_glitch'],
  video_transition: SFX_ISVIDEO['st_transition'] === true,
  dead_transsfx: has(SFX_PREFIX, 'st_transsfx'),
  dead_riserfile: has(SFX_PREFIX, 'st_riserfile'),
  dead_trans: has(SFX_PREFIX, 'st_trans'),
  dead_video_trans: has(SFX_ISVIDEO, 'st_trans')
}));
"""


@node
def test_sfx_maps_know_current_schema_keys(tmp_path):
    """V8: карты звуков собираются по нынешним ключам схемы, мёртвых id нет.

    По мёртвым (`st_transsfx`, `st_riserfile`, `st_trans`) openSfxEdit не находил
    кнопку и брал префикс из самого id: `transsfx` вместо `transition_sfx`.
    """
    res = _run_node(tmp_path, "le_sfx.js", SFX_MAPS, with_styles=True)
    assert res["transition"] == "transition", res
    assert res["transition_sfx"] == "transition_sfx", res
    assert res["intro_riser"] == "intro_riser", res
    assert res["pop"] == "pop" and res["glitch"] == "glitch", res
    assert res["video_transition"] is True, "переход перестал быть видео"
    for dead in ("dead_transsfx", "dead_riserfile", "dead_trans", "dead_video_trans"):
        assert res[dead] is False, "в картах остался мёртвый id (%s)" % dead


WINDOW_LIVE = r"""
const before = window.SFX_PREFIX;
initSfxMaps();
const after = window.SFX_PREFIX;
console.log(JSON.stringify({
  stschema_is_schema: !!(window.STSCHEMA && window.STSCHEMA.layers),
  prefix_alive: after !== before,          // снимок загрузки остался бы тем же {}
  prefix_transition: after['st_transition'] || null
}));
"""


@node
def test_window_names_are_live_not_load_time_snapshots(tmp_path):
    """V9: window.STSCHEMA/SFX_PREFIX — геттеры, а не снимок момента загрузки скрипта.

    Снимок отдавал null и {}, то есть внешний код по window.* не видел ни схемы,
    ни карт звука (схема приезжает позже, отдельным запросом).
    """
    res = _run_node(tmp_path, "le_window.js", WINDOW_LIVE, with_styles=True)
    assert res["stschema_is_schema"], "window.STSCHEMA снова снимок (null) вместо схемы"
    assert res["prefix_alive"], "window.SFX_PREFIX снова снимок, а не живая карта"
    assert res["prefix_transition"] == "transition", res


# ------------------------------------------------------------------
# B4 (питон): защита сборки от процентов в состоянии
# ------------------------------------------------------------------

def test_build_treats_percent_roto_bottom_as_fraction(tmp_path):
    """B4 (V1): стиль с roto_bottom = 26 (проценты) → в сборку уходит 0.26.

    Доля и проценты — разные величины; испорченное состояние (дефект V1 успел
    записать проценты) без этой двери давало маску на весь кадр.
    """
    from api import build

    assert build._roto_bottom_safe({"roto_bottom": 26}) == pytest.approx(0.26)
    assert build._roto_bottom_safe({"roto_bottom": 0.26}) == pytest.approx(0.26)
    assert build._roto_bottom_safe({"roto_bottom": 1}) == pytest.approx(1.0)
    assert build._roto_bottom_safe({}) == 0.0
    assert build._roto_bottom_safe({"roto_bottom": None}) == 0.0

    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    norm = build._norm_build_jobs([{"xml": str(xml),
                                    "style": {"roto": True, "roto_bottom": 26}}])
    assert norm[0]["roto_bottom"] == pytest.approx(0.26), norm[0]["roto_bottom"]
    assert norm[0]["roto"] is True


# ------------------------------------------------------------------
# B6: сторож перевода видит строки панели
# ------------------------------------------------------------------

GUARD = re.compile(r"\bt\('((?:[^'\\]|\\.)+)'")


def test_translation_guard_sees_style_panel_strings():
    """B6 (V5): строки панели ловятся тем же сторожем, что и остальной интерфейс.

    Алиас `tr('…')` сторож (`tests/test_i18n.py::test_js_keys_exist_in_dictionary`,
    шаблон `\\bt\\('…'`) не видел вовсе: три строки панели жили без перевода.
    """
    en = json.load(io.open(EN_JSON, encoding="utf-8"))
    panel = _src(PANEL_JS)
    panel_keys = {_js_unescape(k) for k in GUARD.findall(panel)}

    need = {"Изменено", "Прицел", "Настройка звука", "Сброс", "Файл…"}
    assert need <= panel_keys, "сторож не видит строки панели: %s" % sorted(need - panel_keys)
    assert not re.search(r"(?<![\w$])tr\('", panel), (
        "алиас tr(' вернулся — сторож перевода такие строки не видит")

    missing = sorted(k for k in panel_keys if CYR.search(k) and k not in en)
    assert not missing, "t('…') панели без перевода: %s" % missing

    # Тем же шаблоном по всему интерфейсу: строки панели обязаны в него попадать.
    app_keys = {_js_unescape(k) for k in GUARD.findall(app_meta.app_js_text())}
    assert panel_keys <= app_keys, "строки панели не доезжают до общего сторожа"
