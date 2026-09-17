# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты доступности панели стиля с клавиатуры (V6) и сторожа регрессий V3/V4/V10/V11.

Здесь собраны проверки, предупреждающие тихий откат правок внешнего аудита:
- V3: предупреждение о правке шаблона находит элемент #stylehint из index.html;
- V4: подсветка точки наезда ловит mouseenter по кнопке #st_pickzoom через фазу перехвата
      (мутация без capture: true обязана падать);
- V10: stEdit() на стиле без метки сохраняет русское «кастом» в CURSTYLE.label даже
       при включённом английском языке интерфейса;
- V11: loadStyleSchema() переживает первичный сбой сети, перезапрашивает схему по
       следующему открытию и дедуплицирует одновременные вызовы;
- V6: label.htmlFor каждого поля схемы указывает на существующий элемент,
      числовые поля поддерживают клавиатурный ввод (ArrowUp/Down, Enter/F2/Esc),
      а шеврон раскрытия слайдера доступен по Enter/Space.
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

from core import style_schema, styles  # noqa: E402
from test_style_panel_js import DOM_STUB  # noqa: E402

INDEX_HTML = os.path.join(ROOT, "templates", "index.html")
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт панели требует node в PATH")


def _src(path):
    return io.open(path, encoding="utf-8").read()


def _env_src():
    return (
        "const BASE=%s;\nconst GEOLOGICA=%s;\nconst LAYERS=%s;\n"
        % (
            json.dumps(styles.BASE, ensure_ascii=False),
            json.dumps(styles.GEOLOGICA, ensure_ascii=False),
            json.dumps(style_schema.LAYERS, ensure_ascii=False),
        )
    )


BROWSER_GLOBALS = r"""
global.$ = (id) => document.getElementById(id);
global.esc = (s) => String(s == null ? '' : s);
global.ico = () => '<svg></svg>';
global.toast = () => {};
global.val = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
global.t = (s, vars) => {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] !== undefined ? vars[k] : _);
};
"""


def _run_node_script(tmp_path, name, script_content):
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script_content)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------
# V3: #stylehint в index.html и предупреждение в editStyle()
# ------------------------------------------------------------------

V3_RUNNER = r"""
// Подготовка окружения для editStyle()
resetDom();
const host = document.createElement('div');
host.id = 'stpanel';
document.body.appendChild(host);

const sel = document.createElement('select');
sel.id = 'style';
sel.value = 'custom_template';
document.body.appendChild(sel);

const hint = document.createElement('span');
hint.id = 'stylehint';
document.body.appendChild(hint);

const stName = document.createElement('input');
stName.id = 'st_name';
document.body.appendChild(stName);

const stSaved = document.createElement('span');
stSaved.id = 'st_saved';
document.body.appendChild(stSaved);

STYLES['custom_template'] = { label: 'Мой шаблон', font: 'SFPro' };

editStyle();

console.log(JSON.stringify({
  hintText: hint.textContent,
  editingKey: STYLE_EDITING,
  editingOrigLabel: STYLE_EDIT_ORIG ? STYLE_EDIT_ORIG.label : null
}));
"""


@node
def test_v3_stylehint_exists_and_warns_on_template_edit(tmp_path):
    """V3: #stylehint присутствует в index.html и заполняется функцией editStyle()."""
    html = _src(INDEX_HTML)
    assert re.search(r'id=["\']stylehint["\']', html), "элемент #stylehint отсутствует в templates/index.html"

    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + _src(STYLES_JS) + V3_RUNNER
    res = _run_node_script(tmp_path, "v3_stylehint.js", script)
    assert "правится шаблон" in res["hintText"], "editStyle() не выставил текст предупреждения в #stylehint"
    assert "Мой шаблон" in res["hintText"]

    # Мутация: если предупреждение в stylehint не выставляется, тест падает
    target_stmt = "$('stylehint').textContent=t('правится шаблон «{n}» — «Сохранить» перезапишет его',{n:t(src.label||key)});"
    assert target_stmt in _src(STYLES_JS), "целевая строка editStyle() не найдена в 95-styles.js"
    mutated_styles = _src(STYLES_JS).replace(target_stmt, "$('stylehint').textContent='';")
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + mutated_styles + V3_RUNNER
    res_mut = _run_node_script(tmp_path, "v3_mut.js", mut_script)
    assert res_mut["hintText"] == "", "мутация V3 не повлияла на вывод"


# ------------------------------------------------------------------
# V4: подсветка точки наезда через фазу перехвата (capture: true)
# ------------------------------------------------------------------

V4_RUNNER = r"""
const stage = document.createElement('div');
stage.id = 'ipvstage';
document.body.appendChild(stage);

const host = document.createElement('div');
host.id = 'stpanel';
document.body.appendChild(host);

// 1. Инициализация слушателей по DOMContentLoaded (кнопки #st_pickzoom ещё нет в DOM)
document.dispatchEvent({ type: 'DOMContentLoaded' });

// 2. Кнопка создаётся динамически ПОСЛЕ DOMContentLoaded
const btn = document.createElement('button');
btn.id = 'st_pickzoom';
host.appendChild(btn);

// 3. mouseenter не всплывает (bubbles: false). Без capture: true предок #stpanel не получит событие.
btn.dispatchEvent({ type: 'mouseenter', bubbles: false });
const zoomHoverOnEnter = ZOOM_HOVER;
const markDisplayOnEnter = $('zoommark') ? $('zoommark').style.display : 'missing';

// 4. mouseleave снимает подсветку
btn.dispatchEvent({ type: 'mouseleave', bubbles: false });
const zoomHoverOnLeave = ZOOM_HOVER;
const markDisplayOnLeave = $('zoommark') ? $('zoommark').style.display : 'missing';

console.log(JSON.stringify({
  zoomHoverOnEnter: zoomHoverOnEnter,
  markDisplayOnEnter: markDisplayOnEnter,
  zoomHoverOnLeave: zoomHoverOnLeave,
  markDisplayOnLeave: markDisplayOnLeave
}));
"""


@node
def test_v4_zoom_hover_captures_mouseenter_after_domcontentloaded(tmp_path):
    """V4: mouseenter по кнопке #st_pickzoom перехватывается родителем благодаря capture: true."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(STYLES_JS) + V4_RUNNER
    res = _run_node_script(tmp_path, "v4_capture.js", script)
    assert res["zoomHoverOnEnter"] is True, "ZOOM_HOVER не включился при mouseenter на кнопку #st_pickzoom"
    assert res["markDisplayOnEnter"] == "", "маркер зума не показан"
    assert res["zoomHoverOnLeave"] is False, "ZOOM_HOVER не выключился при mouseleave"
    assert res["markDisplayOnLeave"] == "none", "маркер зума не скрыт после mouseleave"

    # Мутация: удаление {capture:true} обязано ронять перехват не всплывающего mouseenter
    mutated_styles = _src(STYLES_JS).replace(",{capture:true}", "")
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_styles + V4_RUNNER
    res_mut = _run_node_script(tmp_path, "v4_mut.js", mut_script)
    assert res_mut["zoomHoverOnEnter"] is False, "мутация «убрать {capture:true}» не уронила тест — событие прошло без перехвата"
    assert res_mut["markDisplayOnEnter"] != ""


# ------------------------------------------------------------------
# V10: stEdit() на стиле без метки сохраняет русское «кастом»
# ------------------------------------------------------------------

V10_RUNNER = r"""
// Эмуляция английского интерфейса: переводчик отдаёт 'custom'
global.t = (s) => (s === 'кастом' ? 'custom' : s);

buildPanel();
CURSTYLE = {};  // стиля без метки
stEdit();

console.log(JSON.stringify({
  label: CURSTYLE.label
}));
"""


@node
def test_v10_stedit_preserves_russian_custom_label_in_english(tmp_path):
    """V10: stEdit() сохраняет в данных именно 'кастом', а не 'custom' из t()."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + V10_RUNNER
    res = _run_node_script(tmp_path, "v10_label.js", script)
    assert res["label"] == "кастом", f"CURSTYLE.label содержит {res['label']!r} вместо 'кастом'"

    # Мутация: если вернуть старый код `CURSTYLE.label = CURSTYLE.label || t('кастом')`
    mutated_panel = _src(PANEL_JS).replace(
        "CURSTYLE.label = CURSTYLE.label || 'кастом';",
        "CURSTYLE.label = CURSTYLE.label || t('кастом');",
    )
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + V10_RUNNER
    res_mut = _run_node_script(tmp_path, "v10_mut.js", mut_script)
    assert res_mut["label"] == "custom", "мутация V10 не воспроизвела подмену на custom"


# ------------------------------------------------------------------
# V11: устойчивость загрузки схемы стиля к ошибкам сети и дедупликация
# ------------------------------------------------------------------

V11_RUNNER = r"""
(async () => {
  resetDom();
  STSCHEMA = null;
  STSCHEMA_LOADING = null;

  let fetchCalls = 0;
  let failFirst = true;

  global.fetch = async (url) => {
    fetchCalls++;
    if (failFirst) {
      return { ok: false, status: 500 };
    }
    await new Promise(r => setTimeout(r, 25));
    return {
      ok: true,
      json: async () => ({ base: BASE, layers: LAYERS })
    };
  };

  // 1. Первый запрос падает
  await loadStyleSchema();
  const afterFailSchema = STSCHEMA;
  const afterFailLoading = STSCHEMA_LOADING;
  const callsAfterFail = fetchCalls;

  // 2. Сеть восстановилась. Контейнер панели есть в DOM.
  const host = document.createElement('div');
  host.id = 'stpanel';
  document.body.appendChild(host);

  // Вызывается ТОЛЬКО renderStylePanel() (как при открытии панели)
  failFirst = false;
  renderStylePanel();

  // Дожидаемся завершения промиса загрузки схемы, запущенного внутри renderStylePanel()
  await (STSCHEMA_LOADING || Promise.resolve());
  // Даём отработать microtask / .then() и перерисовке
  await new Promise(r => setTimeout(r, 20));

  const callsAfterSuccess = fetchCalls;
  const loadedSchema = STSCHEMA;
  const fieldRows = host.querySelectorAll('.stfield').length;

  console.log(JSON.stringify({
    afterFailSchemaIsNull: afterFailSchema === null,
    afterFailLoadingIsNull: afterFailLoading === null,
    callsAfterFail: callsAfterFail,
    callsAfterSuccess: callsAfterSuccess,
    hasLayers: !!(loadedSchema && loadedSchema.layers),
    fieldRows: fieldRows
  }));
})();
"""


@node
def test_v11_style_schema_error_recovery_and_deduplication(tmp_path):
    """V11: первичный сбой сбрасывает промис, renderStylePanel() перезапрашивает схему и строит поля."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + V11_RUNNER
    res = _run_node_script(tmp_path, "v11_schema.js", script)
    assert res["afterFailSchemaIsNull"] is True, "схема не null после упавшего запроса"
    assert res["afterFailLoadingIsNull"] is True, "STSCHEMA_LOADING не сброшен после ошибки"
    assert res["callsAfterFail"] == 1
    assert res["callsAfterSuccess"] == 2, (
        f"ожидалось 2 сетевых запроса (1 сбой + 1 повтор через renderStylePanel), было: {res['callsAfterSuccess']}"
    )
    assert res["hasLayers"] is True, "схема не загрузилась при повторной попытке через renderStylePanel"
    assert res["fieldRows"] > 0, "в #stpanel нет строк полей после повторной загрузки"

    # Мутация: если убрать повтор загрузки из renderStylePanel(),
    # то второй вызов fetch не происходит и панель остаётся пустой
    target_line = "loadStyleSchema().then(() => { if (STSCHEMA && STSCHEMA.layers) renderStylePanel(); });"
    assert target_line in _src(PANEL_JS), "целевая строка loadStyleSchema().then(...) не найдена в 94-stylepanel.js"
    mutated_panel = _src(PANEL_JS).replace(target_line, "")
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel + V11_RUNNER
    res_mut = _run_node_script(tmp_path, "v11_mut.js", mut_script)
    assert res_mut["callsAfterSuccess"] == 1, "мутация без повторной загрузки не должна была делать второй fetch"
    assert res_mut["fieldRows"] == 0, "мутация без повторной загрузки не должна была построить строки полей"


V11_DEDUP_RUNNER = r"""
(async () => {
  resetDom();
  STSCHEMA = null;
  STSCHEMA_LOADING = null;

  let fetchCalls = 0;
  global.fetch = async (url) => {
    fetchCalls++;
    await new Promise(r => setTimeout(r, 25));
    return {
      ok: true,
      json: async () => ({ base: BASE, layers: LAYERS })
    };
  };

  const [s1, s2] = await Promise.all([loadStyleSchema(), loadStyleSchema()]);
  console.log(JSON.stringify({
    fetchCalls: fetchCalls,
    loaded: !!(STSCHEMA && STSCHEMA.layers)
  }));
})();
"""


@node
def test_v11_style_schema_concurrent_deduplication(tmp_path):
    """V11: параллельные вызовы loadStyleSchema() схлопываются в один сетевой запрос."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + V11_DEDUP_RUNNER
    res = _run_node_script(tmp_path, "v11_dedup.js", script)
    assert res["fetchCalls"] == 1, f"ожидался 1 сетевой запрос, было: {res['fetchCalls']}"
    assert res["loaded"] is True

    # Мутация: без if (STSCHEMA_LOADING) return STSCHEMA_LOADING; идут 2 запроса
    mutated_panel = _src(PANEL_JS).replace("if (STSCHEMA_LOADING) return STSCHEMA_LOADING;", "")
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel + V11_DEDUP_RUNNER
    res_mut = _run_node_script(tmp_path, "v11_dedup_mut.js", mut_script)
    assert res_mut["fetchCalls"] == 2, "мутация дедупликации не привела к двум запросам"


# ------------------------------------------------------------------
# V6: доступность панели с клавиатуры (htmlFor, ArrowUp/Down, Enter/Space)
# ------------------------------------------------------------------

V6_RUNNER = r"""
buildPanel();
CURSTYLE = JSON.parse(JSON.stringify(BASE));
fillStyleFields();

// 1. Проверка htmlFor каждого поля схемы
const badLabels = [];
function checkFields(items) {
  for (const it of items) {
    if (it.type === 'group' && it.items) {
      checkFields(it.items);
    } else if (it.type === 'field') {
      const row = document.getElementById('strow_' + it.key);
      if (!row) continue;
      const lbl = row.querySelector('.stfield-lbl');
      if (!lbl) {
        badLabels.push({ key: it.key, err: 'нет label.stfield-lbl' });
        continue;
      }
      if (it.key === 'layer_order') {
        if (!lbl.id) {
          badLabels.push({ key: it.key, err: 'нет id у метки layer_order' });
        }
        continue;
      }
      const forId = lbl.htmlFor;
      if (!forId) {
        badLabels.push({ key: it.key, err: 'пустой htmlFor' });
        continue;
      }
      const target = document.getElementById(forId);
      if (!target) {
        badLabels.push({ key: it.key, htmlFor: forId, err: 'элемент не найден в DOM' });
      }
    }
  }
}
for (const layer of STSCHEMA.layers) checkFields(layer.items || []);

// 2. Клавиатурное управление числовым полем (num): tabindex, role, aria-valuenow, ArrowUp
const numKey = 'start_blur';
const span = document.getElementById('st_' + numKey + '_val');
const step = 1;
const beforeVal = parseFloat(span.textContent) || 0;
span.dispatchEvent({ type: 'keydown', key: 'ArrowUp' });
const afterUp = parseFloat(span.textContent) || 0;

span.dispatchEvent({ type: 'keydown', key: 'ArrowUp', shiftKey: true });
const afterShiftUp = parseFloat(span.textContent) || 0;

// 3. Клавиатурное управление треугольником (twField): Enter и Space
const tw = document.querySelector('.sttw-field[data-tw="field_' + numKey + '"]');
const sldRow = document.getElementById('stslider_row_' + numKey);
const openBefore = tw.getAttribute('aria-expanded') === 'true';

tw.dispatchEvent({ type: 'keydown', key: 'Enter' });
const openAfterEnter = tw.getAttribute('aria-expanded') === 'true';
const sldDisplayAfterEnter = sldRow.style.display;

tw.dispatchEvent({ type: 'keydown', key: ' ' });
const openAfterSpace = tw.getAttribute('aria-expanded') === 'true';

console.log(JSON.stringify({
  badLabels: badLabels,
  numRole: span.getAttribute('role'),
  numTabIndex: span.tabIndex,
  numAriaLabel: span.getAttribute('aria-label'),
  numAriaNow: span.getAttribute('aria-valuenow'),
  beforeVal: beforeVal,
  afterUp: afterUp,
  afterShiftUp: afterShiftUp,
  twRole: tw.getAttribute('role'),
  twTabIndex: tw.tabIndex,
  openBefore: openBefore,
  openAfterEnter: openAfterEnter,
  sldDisplayAfterEnter: sldDisplayAfterEnter,
  openAfterSpace: openAfterSpace
}));
"""


@node
def test_v6_stylepanel_keyboard_accessibility(tmp_path):
    """V6: каждый label.htmlFor валиден, число меняется по ArrowUp, треугольник по Enter."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + V6_RUNNER
    res = _run_node_script(tmp_path, "v6_a11y.js", script)

    assert not res["badLabels"], f"битые htmlFor у полей: {res['badLabels']}"
    assert res["numRole"] == "spinbutton", f"роль числа: {res['numRole']}"
    assert res["numTabIndex"] == 0, "числовой span не фокусируем"
    assert res["numAriaLabel"], "у числа нет aria-label"
    assert res["afterUp"] == res["beforeVal"] + 1, "ArrowUp не прибавил 1 шаг"
    assert res["afterShiftUp"] == res["afterUp"] + 10, "Shift+ArrowUp не прибавил 10 шагов"

    assert res["twRole"] == "button", "треугольник не role=button"
    assert res["twTabIndex"] == 0, "треугольник не в tab-порядке"
    assert res["openAfterEnter"] is True, "Enter не раскрыл треугольник"
    assert res["sldDisplayAfterEnter"] == "", "строка слайдера осталась скрытой"
    assert res["openAfterSpace"] is False, "Space не свернул треугольник обратно"

    # Мутация 1: откат htmlFor к слепому 'st_' + item.key ломает доступность полей
    mutated_panel = re.sub(
        r"if\s*\(\s*isNumCtl\s*\)\s*\{\s*fLbl\.htmlFor\s*=\s*'st_'\s*\+\s*item\.key\s*\+\s*'_val';\s*\}[\s\S]*?else\s*\{\s*fLbl\.htmlFor\s*=\s*'st_'\s*\+\s*item\.key;\s*\}",
        "fLbl.htmlFor = 'st_' + item.key;",
        _src(PANEL_JS),
    )
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + V6_RUNNER
    res_mut = _run_node_script(tmp_path, "v6_mut.js", mut_script)
    assert len(res_mut["badLabels"]) > 0, "мутация отката htmlFor не выявила битых меток"

    # Мутация 2: отключение клавиатурного шага ArrowUp оставляет значение неизменным
    mutated_panel2 = _src(PANEL_JS).replace("else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {", "else if (false) {")
    mut_script2 = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel2 + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + V6_RUNNER
    res_mut2 = _run_node_script(tmp_path, "v6_mut2.js", mut_script2)
    assert res_mut2["afterUp"] == res_mut2["beforeVal"], "мутация отключения ArrowUp не уронила изменение значения"


# ------------------------------------------------------------------
# Доступные имена интерактивных контролов панели (#stpanel)
# ------------------------------------------------------------------

ACCESSIBLE_NAMES_RUNNER = r"""
buildPanel();
CURSTYLE = JSON.parse(JSON.stringify(BASE));
fillStyleFields();
if (typeof renderLayerOrderUI === 'function') renderLayerOrderUI();

const panel = document.getElementById('stpanel');
const allNodes = [];
function walk(el) {
  for (const c of el.children) {
    allNodes.push(c);
    walk(c);
  }
}
walk(panel);

// 1. Интерактивные контролы: input/select/textarea/button/[role=button]/[role=spinbutton]/[tabindex]
const targetTags = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON']);
const targetRoles = new Set(['button', 'spinbutton']);

const elements = allNodes.filter(el => {
  if (targetTags.has(el.tagName)) return true;
  const r = el.getAttribute('role');
  if (r && targetRoles.has(r)) return true;
  if (el.getAttribute('tabindex') !== null) return true;
  return false;
});

// 2. Проверка label[for]: ни одна не указывает на div/span без роли контрола
const labels = allNodes.filter(el => el.tagName === 'LABEL');
const labelForMap = new Map();
const badLabelTargets = [];

for (const lbl of labels) {
  const forId = lbl.htmlFor;
  if (forId) {
    const target = document.getElementById(forId);
    if (!target) {
      badLabelTargets.push({ forId, err: 'not_found' });
    } else {
      const tag = target.tagName;
      const role = target.getAttribute('role');
      const isControl = targetTags.has(tag) || (role && targetRoles.has(role));
      if (!isControl && (tag === 'DIV' || tag === 'SPAN')) {
        badLabelTargets.push({ forId, tag, role, err: 'div_or_span_without_control_role' });
      }
      labelForMap.set(forId, lbl);
    }
  }
}

// 3. Проверка доступного имени у каждого контрола
const missingNames = [];
for (const el of elements) {
  const ariaLabel = el.getAttribute('aria-label');
  const ariaLabelledBy = el.getAttribute('aria-labelledby');
  const id = el.id;
  const lbl = id ? labelForMap.get(id) : null;

  let hasName = false;
  if (ariaLabel && ariaLabel.trim()) hasName = true;
  else if (ariaLabelledBy && document.getElementById(ariaLabelledBy)) hasName = true;
  else if (lbl) hasName = true;

  if (!hasName) {
    missingNames.push({
      tag: el.tagName,
      type: el.type,
      id: el.id,
      className: el.className,
      role: el.getAttribute('role')
    });
  }
}

// 4. Проверка layer_order: role=list и aria-labelledby на id метки
const loBox = document.getElementById('st_layer_order_list');
const loRole = loBox ? loBox.getAttribute('role') : null;
const loLabelledBy = loBox ? loBox.getAttribute('aria-labelledby') : null;
const loLabelEl = loLabelledBy ? document.getElementById(loLabelledBy) : null;

console.log(JSON.stringify({
  totalControls: elements.length,
  missingCount: missingNames.length,
  missingNames: missingNames,
  badLabelTargets: badLabelTargets,
  loRole: loRole,
  loLabelledBy: loLabelledBy,
  loLabelFound: !!loLabelEl
}));
"""


@node
def test_stylepanel_controls_have_accessible_names(tmp_path):
    """Каждый контрол панели имеет доступное имя, и ни одна метка не указывает на div/span без роли."""
    script = DOM_STUB + BROWSER_GLOBALS + _env_src() + _src(PANEL_JS) + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + _src(STYLES_JS) + ACCESSIBLE_NAMES_RUNNER
    res = _run_node_script(tmp_path, "a11y_names.js", script)

    assert res["totalControls"] >= 400, f"слишком мало контролов найдено: {res['totalControls']}"
    assert res["badLabelTargets"] == [], f"label[for] указывает на div/span без роли: {res['badLabelTargets']}"
    assert res["missingCount"] == 0, f"контролы без доступного имени: {res['missingNames']}"
    assert res["loRole"] == "list", f"список layer_order должен иметь role=list, получено: {res['loRole']}"
    assert res["loLabelFound"] is True, f"элемент метки для layer_order не найден по {res['loLabelledBy']}"

    # Мутация: снять aria-label у ползунка -> тест красный
    mutated_panel = _src(PANEL_JS).replace(
        "range.setAttribute('aria-label', fTitle);",
        "",
    )
    assert mutated_panel != _src(PANEL_JS), "мутируемая строка не найдена в 94-stylepanel.js"
    mut_script = DOM_STUB + BROWSER_GLOBALS + _env_src() + mutated_panel + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n" + _src(STYLES_JS) + ACCESSIBLE_NAMES_RUNNER
    res_mut = _run_node_script(tmp_path, "a11y_mut.js", mut_script)
    assert res_mut["missingCount"] > 0, "мутация снятия aria-label у ползунка обязана ронять тест"


