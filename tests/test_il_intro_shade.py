# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IL: затемнение под интро — слой-фигура с Box Blur, галка и ползунок в стиле.

Пользователь в каждом из четырёх роликов `amdi1.aep` руками клал `Shape Layer 1`: мягкое
чёрное затемнение снизу кадра, чтобы белый текст интро читался на светлой одежде. Числа
сняты с этих роликов (см. `core/xml2ae/layout.SHADE_*`) и живут в ОДНОМ месте — плане сцены
(`scene_plan`), откуда их берут и `.jsx`, и предпросмотр. Второй копии формул нет.

Проверяет:
  * стиль без ключей -> `plan["shade"] is None`, в `.jsx` нет ни `INTRO_SHADE`, ни
    «Затемнение интро» (выключенная галка не меняет собранный скрипт ни на байт, golden);
  * числа плана: `intro_y: 768` -> y = 553, `intro_y: 672` -> y = 457, `intro_shade_op: 40` -> 40;
  * собранный `.jsx` содержит фигуру с размытием и проходит проверки `verify_jsx`
    (синтаксис `node --check`, необъявленные имена, разделители строк, BOM);
  * порядок слоёв: «Затемнение интро» стоит выше ВСЕХ клипов камер и ниже всех слоёв интро,
    вставок, рото и субтитров — проверяется исполнением `.jsx` в node с моком AE
    (`simulate_jsx_stack` из test_fm_layer_order);
  * предпросмотр (`static/app/85-inserts-view.js:ipvShade`) рисует элемент из `plan.shade`
    (размер, центр, размытие, прозрачность) и не создаёт его вовсе без ключа плана;
  * поля стиля на месте и wired в тех же трёх местах `95-styles.js`, что у соседних ключей;
  * ключи доезжают до плана через `/api/scene` — тем же путём, каким их шлёт предпросмотр
    (`stEdit` -> `ipvPlanBody` -> `/api/scene` -> `ipvUI`), без перезапуска сервера и F5.

Полный `verify_jsx.verify()` тут не зовём: фикстура `timeline_subs.xml.gz` ссылается на
несуществующие файлы камер (`C:\\footage\\...`), и проверка медиа падает на них — не на
затемнении. Поэтому гоняются ровно те проверки, которые к сборке и относятся (как в
test_ik_intro_fade.py).
"""
import gzip
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

from core import verify_jsx  # noqa: E402
from core import xml2ae  # noqa: E402
from test_fm_layer_order import simulate_jsx_stack  # noqa: E402

JS_REL = ("static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


INTRO = [
    dict(words=["ПЕРВОЕ"], color="white", times=[2.0]),
    dict(words=["ВТОРОЕ"], color="white", times=[8.3]),
]

# Числа amdi1.aep, которые обязан нести план (см. layout.SHADE_*).
SHADE_768 = {"x": -4, "y": 553, "scale": 94, "w": 1416, "h": 1052,
             "ox": -20, "oy": 610, "blur": 653, "op": 100}


def _scene_plan(xml_subs, intro=None, splits=None, style=None):
    return xml2ae.scene_plan(xml_subs, intro=intro if intro is not None else INTRO,
                             intro_splits=splits if splits is not None else [1],
                             style=dict(style or {}), disclaimer="",
                             intro_riser=False, emit=lambda *a: None)


def _build(xml, tmp_path, intro=None, style=None, inserts=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=intro if intro is not None else INTRO,
                                   intro_splits=[1], style=style or {},
                                   inserts=inserts, intro_mode="word", disclaimer="",
                                   intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read(), path


# ------------------------------------------------------------------ план сцены

def test_style_without_keys_has_no_shade(xml_subs):
    """Галка выключена (ключа в стиле нет) -> plan["shade"] is None."""
    assert _scene_plan(xml_subs)["shade"] is None
    assert _scene_plan(xml_subs, style={"intro_shade": False})["shade"] is None


def test_shade_plan_numbers(xml_subs):
    """Числа слоя: позиция следует за intro_y (y = intro_y − 215), непрозрачность из стиля."""
    sh = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": 768})["shade"]
    assert sh == SHADE_768, sh

    sh_op = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": 768,
                                         "intro_shade_op": 40})["shade"]
    assert sh_op["op"] == 40, sh_op

    sh_low = _scene_plan(xml_subs, style={"intro_shade": True, "intro_y": 672})["shade"]
    assert sh_low["y"] == 457, sh_low


# ------------------------------------------------------------------ сборка .jsx

def test_jsx_without_shade_is_unchanged(xml_subs, tmp_path):
    """Выключенная галка: в .jsx нет ни объявления, ни слоя — скрипт прежний."""
    jsx, _ = _build(xml_subs, tmp_path, name="off.jsx")
    assert "INTRO_SHADE" not in jsx
    assert "Затемнение интро" not in jsx
    assert "shadeLayer" not in jsx


def test_jsx_with_shade_builds_figure_with_blur(xml_subs, tmp_path):
    """Включённая галка: фигура с прямоугольником, заливкой, Box Blur и родителем-нулом."""
    jsx, path = _build(xml_subs, tmp_path, style={"intro_shade": True, "intro_y": 768},
                       name="on.jsx")
    for token in ('var INTRO_SHADE=', "addShape", "Затемнение интро",
                  'ADBE Root Vectors Group', 'ADBE Vector Group', 'ADBE Vectors Group',
                  'ADBE Vector Shape - Rect', 'ADBE Vector Rect Size',
                  'ADBE Vector Graphic - Fill', 'ADBE Vector Fill Color',
                  'ADBE Vector Transform Group', 'ADBE Vector Position',
                  'ADBE Box Blur2', 'Blur Radius', 'ADBE Box Blur2-0001'):
        assert token in jsx, "в .jsx нет %r" % token
    assert json.loads(re.search(r"var INTRO_SHADE=(\{.*?\});", jsx).group(1)) == SHADE_768

    # порядок как у рото: parent раньше значений — иначе AE пересчитает локальную позицию
    assert jsx.index("shadeLayer.parent=cam1null") < jsx.index(
        'shadeLayer.property("ADBE Transform Group").property("ADBE Position")'
        '.setValue([INTRO_SHADE.x, INTRO_SHADE.y])')

    rep = verify_jsx.Report(path)
    verify_jsx.check_syntax(path, jsx, rep)        # node --check
    verify_jsx.check_undeclared(jsx, rep)
    verify_jsx.check_line_separators(jsx, rep)
    verify_jsx.check_bom(path, rep)
    assert rep.ok, "verify_jsx нашёл проблемы: %s" % rep.errors


def test_shade_layer_keeps_its_place_in_the_stack(xml_subs, tmp_path):
    """Порядок слоёв: затемнение выше клипов камер и ниже интро, вставок, рото, субтитров.

    Проверка не «на глаз»: .jsx исполняется в node с моком AE, который ведёт настоящий
    стек слоёв композиции (тот же simulate_jsx_stack, что у задания FM).
    """
    intro = [dict(words=["ПЕРВОЕ", "ВТОРОЕ"], times=[0.5, 1.0])]
    inserts = [dict(type="photo", style="cam2", media="C:/x/a.png", start_s=1.0, dur_s=2.0)]
    _, path = _build(xml_subs, tmp_path, intro=intro, inserts=inserts,
                     style={"intro_shade": True, "intro_y": 768}, name="stack.jsx")
    stack = simulate_jsx_stack(path)
    names = [s["name"] for s in stack]
    assert names.count("Затемнение интро") == 1, "слоя затемнения нет или он не один: %s" % names
    i = names.index("Затемнение интро")

    # «Затемнение интро» мок относит к интро (в имени есть «интро») — сравниваем по позиции
    assert not [s for s in stack[:i] if s["category"] == "cameras"], \
        "над затемнением оказался клип камеры: %s" % [s["name"] for s in stack[:i]]
    below = [s for s in stack[i + 1:] if s["category"] in ("intro", "photo", "video", "roto", "subs")]
    assert not below, "под затемнением слои интро/вставок/рото/субтитров: %s" % [s["name"] for s in below]

    # содержимое выше и ниже — чтобы проверка не проходила на пустом стеке
    assert any(s["category"] == "intro" and s["name"] != "Затемнение интро" for s in stack[:i])
    assert any(s["category"] == "photo" for s in stack[:i])
    assert any(s["category"] == "cameras" for s in stack[i + 1:])
    # и сразу под затемнением — клип камеры (над ним слои быть не могут по условию выше)
    assert stack[i + 1]["category"] == "cameras", stack[i + 1]


# ------------------------------------------------------------------ предпросмотр

DOM_SIM = r"""
const assert = require('assert');
const REG = {};
class El {
  constructor(tag){
    this.tagName = tag.toUpperCase(); this.style = {}; this.dataset = {};
    this._children = []; this.parent = null; this._id = null;
  }
  set id(v){ this._id = v; REG[v] = this; }
  get id(){ return this._id; }
  set className(v){ this._cn = String(v || ''); }
  get className(){ return this._cn || ''; }
  get children(){ return this._children; }
  appendChild(c){ this._children.push(c); c.parent = this; return c; }
  insertBefore(c, ref){ const i = this._children.indexOf(ref);
    if (i < 0) this._children.push(c); else this._children.splice(i, 0, c);
    c.parent = this; return c; }
  remove(){ if (this.parent){ const i = this.parent._children.indexOf(this);
      if (i >= 0) this.parent._children.splice(i, 1); }
    if (REG[this._id] === this) delete REG[this._id]; this.parent = null; }
}
const stage = new El('div'); stage.id = 'ipvstage'; stage.clientWidth = 540;   // k = 540/1080 = 0.5
const intro = new El('div'); intro.id = 'ipvintro'; stage.appendChild(intro);
global.$ = (id) => REG[id] || null;
global.document = { createElement: (t) => new El(t) };
global.IPV = { fps: 60, plan: @PLAN@ };
global.ipvNow = () => 0;
global.ipvZoomAt = () => 1;        // зум Камеры 1 — отдельная машина, здесь не проверяется
"""


def _js_src():
    return open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()


def _region(js_src, head, end):
    i = js_src.index(head)
    j = js_src.index(end, i + len(head))
    assert j > i, "не нашёлся конец куска %s" % head
    return js_src[i:j]


def _shade_script(plan, checks):
    js_src = _js_src()
    # ipvCamChild — общая машина координат нула Камеры 1 (второй копии правила нет);
    # кусок кончается на строке с комментарием, поэтому перевод строки ставим сами
    body = _region(js_src, "function ipvCamChild(", "\nfunction ") + "\n" + \
        _region(js_src, "function ipvShade(", "// ---- субтитры по плану")
    sim = DOM_SIM.replace("@PLAN@", json.dumps(plan, ensure_ascii=False))
    return sim + "\n" + body + "\n" + checks


def _run_node(tmp_path, name, script):
    node_file = str(tmp_path / name)
    with open(node_file, "w", encoding="utf-8") as f:
        f.write(script)
    return subprocess.run(["node", node_file], capture_output=True, text=True, encoding="utf-8")


SHADE_PLAN = {"x": -4, "y": 553, "scale": 94, "w": 1416, "h": 1052,
              "ox": -20, "oy": 610, "blur": 653, "op": 40}


@node
def test_node_preview_draws_shade_from_plan(tmp_path):
    """plan.shade -> элемент с размером, центром, размытием и opacity = op/100.

    Числа при k = 0.5, s = 1: 1416×0.5 = 708, 1052×0.5 = 526; центр = (x + ox·scale/100,
    y + oy·scale/100) = (−22.8, 1126.4) в пикселях кадра, то есть (−11.4, 563.2) превью;
    размытие 653×0.5 = 326.5; прозрачность 40 % -> 0.4.
    """
    checks = r"""
    ipvShade();
    const el = $('ipvshade');
    assert(el, 'элемент затемнения не создан при plan.shade');
    assert.strictEqual(el.parent, stage, 'затемнение создано не в кадре предпросмотра');
    assert.strictEqual(stage.children.indexOf(el) + 1, stage.children.indexOf(intro),
      'затемнение обязано стоять в DOM перед блоком интро');
    assert.strictEqual(el.style.width, '708px', el.style.width);
    assert.strictEqual(el.style.height, '526px', el.style.height);
    assert.strictEqual(el.style.opacity, '0.4', 'прозрачность не из op: ' + el.style.opacity);
    assert.strictEqual(el.style.filter, 'blur(326.5px)', el.style.filter);
    const tr = el.style.transform;
    assert(tr.indexOf('scale(0.94)') >= 0, 'нет масштаба слоя в трансформе: ' + tr);
    const m = tr.match(/translate\(([-\d.]+)px,([-\d.]+)px\)/);
    assert(m, 'не разобрать сдвиг центра: ' + tr);
    assert(Math.abs(parseFloat(m[1]) - (-22.8 * 0.5)) < 1e-9, 'центр по X: ' + m[1]);
    assert(Math.abs(parseFloat(m[2]) - (1126.4 * 0.5)) < 1e-9, 'центр по Y: ' + m[2]);

    // галку сняли — элемент уходит из кадра
    IPV.plan.shade = null;
    ipvShade();
    assert.strictEqual($('ipvshade'), null, 'без plan.shade элемент остался в кадре');
    assert.strictEqual(stage.children.length, 1, 'в кадре остались лишние элементы');

    console.log("OK: preview draws shade from plan");
    """
    res = _run_node(tmp_path, "test_il_shade.js",
                    _shade_script({"w": 1080, "h": 1920, "shade": SHADE_PLAN}, checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: preview draws shade from plan" in res.stdout


@node
def test_node_preview_without_shade_has_no_element(tmp_path):
    """plan.shade is None — элемента затемнения в кадре нет вовсе."""
    checks = r"""
    ipvShade();
    assert.strictEqual($('ipvshade'), null, 'затемнение создано без ключа плана');
    assert.strictEqual(stage.children.length, 1, 'в кадре появились лишние элементы');
    console.log("OK: preview has no shade without the key");
    """
    res = _run_node(tmp_path, "test_il_no_shade.js", _shade_script(None, checks))
    assert res.returncode == 0, f"Node.js script failed: {res.stderr}\n{res.stdout}"
    assert "OK: preview has no shade without the key" in res.stdout


# ------------------------------------------------------------------ интерфейс

def test_style_fields_are_wired_in_three_places():
    """Галка и ползунок на месте и ходят тем же путём, что соседние ключи: заполнение
    полей, точка «изменено», чтение в stEdit (иначе правка не доедет до плана и превью)."""
    html = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
    assert html.count('id="st_introshade"') == 1
    assert html.count('id="st_introshadeop"') == 1
    assert 'id="st_introshade"' in html[html.index('id="st_introfade"'):
                                        html.index('id="st_introfxholdadd"')], \
        "поля затемнения обязаны стоять рядом с фейд-аутом интро"

    st = open(os.path.join(ROOT, "static", "app", "95-styles.js"), encoding="utf-8").read()
    assert "$('st_introshade').checked=!!s.intro_shade" in st            # fillStyleFields
    assert "const d_introshade=(!!s.intro_shade)!==(!!orig.intro_shade)" in st
    assert "CURSTYLE.intro_shade_op=isNaN(ishop)?100:ishop" in st        # stEdit


@pytest.fixture()
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_scene_carries_the_shade_keys(xml_subs, client):
    """Ключи стиля доезжают до плана ровно тем путём, каким их шлёт предпросмотр
    (stEdit -> ipvPlanBody -> /api/scene): правка галки и ползунка меняет план, а по нему
    перестраивается кадр — перезапуск сервера и F5 для этого не нужны."""
    r = client.post("/api/scene", json={
        "xml": xml_subs,
        "style": {"intro_shade": True, "intro_y": 768, "intro_shade_op": 40},
    }, headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True, body
    assert body["plan"]["shade"] == dict(SHADE_768, op=40), body["plan"].get("shade")
