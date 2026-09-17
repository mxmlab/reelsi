# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панель стиля: полный круг через интерфейс (fillStyleFields -> stEdit -> rotoSync).

Сторож класса регрессий «показанное записано как хранимое»:
1. Полный круг stView -> UI -> stStore:
   Для стилей BASE, GEOLOGICA и кастомного пресета с отличиями:
   CURSTYLE = style -> fillStyleFields() -> прогон всех пишущих путей (stEdit, rotoSync) ->
   CURSTYLE по каждому ключу схемы равен исходному стилю (с допуском показа не более 1 шага).
2. Защита от регрессий: мутация rotoSync (CURSTYLE.roto_bottom = b) гарантированно
   роняет проверку (35% вместо 0.35).
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
sys.path.insert(0, HERE)

from core import style_schema, styles  # noqa: E402
from tests.test_style_panel_js import DOM_STUB  # noqa: E402

PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт панели требует node в PATH")

BROWSER_GLOBALS = r"""
global.$ = (id) => document.getElementById(id);
global.esc = (s) => String(s == null ? '' : s);
global.ico = () => '<svg></svg>';
global.captureAE = () => { global._captureCalls = (global._captureCalls || 0) + 1; };
global.applyStyleToUI = () => {};
"""


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


def _run_node_script(tmp_path, name, script):
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(
        ["node", path],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
        timeout=60,
    )
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:1000]
    return json.loads(p.stdout.strip().splitlines()[-1])


ROUNDTRIP_BODY = r"""
function allSchemaEntries(items) {
  const out = [];
  for (const it of items) {
    if (it.toggle) out.push({ key: it.toggle, field: it, kind: 'toggle' });
    if (it.key) out.push({ key: it.key, field: it, kind: 'key' });
    if (it.key2) out.push({ key: it.key2, field: it, kind: 'key2' });
    if (it.items) out.push(...allSchemaEntries(it.items));
  }
  return out;
}

const entries = allSchemaEntries(STSCHEMA.layers);

function runRoundtrip(srcStyle, name) {
  buildPanel();
  global._captureCalls = 0;
  CURSTYLE = JSON.parse(JSON.stringify(srcStyle));
  
  // 1. Заполнить панель из CURSTYLE
  fillStyleFields();
  
  // 2. Прогнать все пишущие пути обычной работы без изменений значений:
  // stEdit() — обход всех полей и запись stStore(stReadView)
  stEdit();
  // rotoSync() — синхронизация roto, roto_bottom, roto_cam1_only
  rotoSync();
  
  const bad = [];
  let checked = 0;
  
  for (const entry of entries) {
    const k = entry.key;
    const f = entry.field;
    checked++;
    
    // Ожидаемое значение: если ключ был в стиле — он сам; иначе дефолт BASE
    let exp = (k in srcStyle) ? srcStyle[k] : BASE[k];
    const act = CURSTYLE[k];
    
    // Специфика hl_font: при выключенном hl_bold галка скрыта и stEdit пишет null по контракту
    if (k === 'hl_font' && srcStyle.hl_bold === false) {
      exp = null;
    }
    
    // Дисклеймер: null и пустая строка '' оба означают отсутствие пользовательского текста
    if (k === 'disclaimer') {
      if (act !== null && act !== exp) {
        bad.push(name + '.' + k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
      }
      continue;
    }
    
    // Пусто у nullable-полей: "" и null эквивалентны по представлению
    if (f.nullable && (act === '' || act === null) && (exp === '' || exp === null)) {
      continue;
    }
    
    // Цвета: сравнение компонент с допуском 0.02 (округление hex #RRGGBB)
    if (Array.isArray(exp) && exp.length === 3 && typeof exp[0] === 'number') {
      const diff = Math.max(...exp.map((v, i) => Math.abs(v - (act && act[i] != null ? act[i] : 0))));
      if (diff > 0.02) {
        bad.push(name + '.' + k + ': color diff ' + diff + ' (' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp) + ')');
      }
      continue;
    }
    
    // Числа: допуск округления показа не более одного шага поля
    if (typeof exp === 'number') {
      const step = f.step != null ? f.step : 1;
      if (typeof act !== 'number' || Math.abs(exp - act) > step + 1e-4) {
        bad.push(name + '.' + k + ': number diff ' + exp + ' vs ' + act + ' (step ' + step + ')');
      }
      continue;
    }
    
    // Массивы (например layer_order)
    if (Array.isArray(exp)) {
      if (JSON.stringify(exp) !== JSON.stringify(act)) {
        bad.push(name + '.' + k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
      }
      continue;
    }
    
    // Остальные типы: строгое равенство
    if (exp !== act) {
      bad.push(name + '.' + k + ': ' + JSON.stringify(act) + ' vs ' + JSON.stringify(exp));
    }
  }
  
  return { bad: bad, checked: checked, captureCalls: global._captureCalls };
}

const CUSTOM_STYLE = {
  label: 'Кастом-тест',
  roto: true,
  roto_bottom: 0.55,
  sub_scale: 125,
  sub_y: 0.35,
  sub_case: 'lower',
  hl_bold: true,
  hl_fill: [0.2, 0.4, 0.6],
  music_db: -15.0
};

const resBase = runRoundtrip(BASE, 'BASE');
const resGeo = runRoundtrip(GEOLOGICA, 'GEOLOGICA');
const resCustom = runRoundtrip(CUSTOM_STYLE, 'CUSTOM');

console.log(JSON.stringify({
  base: resBase,
  geo: resGeo,
  custom: resCustom,
  totalEntries: entries.length
}));
"""


@node
def test_full_roundtrip_through_panel_interface(tmp_path):
    """1. Полный круг через интерфейс: BASE, GEOLOGICA и кастомный пресет.

    После fillStyleFields -> stEdit -> rotoSync каждое поле CURSTYLE сохраняет
    своё значение (числа — с допуском не больше одного шага).
    """
    full_js = (
        DOM_STUB
        + BROWSER_GLOBALS
        + _env_src()
        + _src(PANEL_JS)
        + "\n"
        + _src(STYLES_JS)
        + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n"
        + ROUNDTRIP_BODY
    )
    res = _run_node_script(tmp_path, "test_roundtrip.js", full_js)

    assert res["totalEntries"] >= len(styles.BASE) - 2
    assert not res["base"]["bad"], "Расхождения на BASE:\n" + "\n".join(res["base"]["bad"])
    assert not res["geo"]["bad"], "Расхождения на GEOLOGICA:\n" + "\n".join(res["geo"]["bad"])
    assert not res["custom"]["bad"], "Расхождения на CUSTOM:\n" + "\n".join(res["custom"]["bad"])

    assert res["base"]["captureCalls"] >= 1, "rotoSync не вызвал captureAE"
    assert res["geo"]["captureCalls"] >= 1
    assert res["custom"]["captureCalls"] >= 1


@node
def test_mutation_roto_bottom_percent_fails(tmp_path):
    """2. Мутация: возвращение старой строки CURSTYLE.roto_bottom=b в rotoSync обязано ронять тест.

    Ловит регрессию: показанные 35% попадают в стиль вместо доли 0.35.
    """
    styles_src = _src(STYLES_JS)
    target = "CURSTYLE.roto_bottom=stStore(findFieldByKey('roto_bottom'),b);"
    assert target in styles_src, f"Целевая строка rotoSync не найдена в {STYLES_JS}"

    # Мутируем: записываем b напрямую, минуя stStore
    mutated_styles_src = styles_src.replace(target, "CURSTYLE.roto_bottom=b;")

    full_js = (
        DOM_STUB
        + BROWSER_GLOBALS
        + _env_src()
        + _src(PANEL_JS)
        + "\n"
        + mutated_styles_src
        + "\nSTSCHEMA = {base: BASE, layers: LAYERS};\n"
        + ROUNDTRIP_BODY
    )
    res = _run_node_script(tmp_path, "test_mutated_roundtrip.js", full_js)

    # Тест обязан поймать расхождение roto_bottom
    found_in_base = any("roto_bottom" in s for s in res["base"]["bad"])
    found_in_geo = any("roto_bottom" in s for s in res["geo"]["bad"])
    found_in_custom = any("roto_bottom" in s for s in res["custom"]["bad"])

    assert found_in_base, f"Мутация roto_bottom=b не поймана на BASE: {res['base']['bad']}"
    assert found_in_geo, f"Мутация roto_bottom=b не поймана на GEOLOGICA: {res['geo']['bad']}"
    assert found_in_custom, f"Мутация roto_bottom=b не поймана на CUSTOM: {res['custom']['bad']}"
