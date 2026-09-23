# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Точка масштабирования прекомпа интро (intro_scale_anchor): якорь слоя — на тексте.

Владелец: «если я уменьшаю интро руками, почему оно не учитывает, что уменьшается от точки
появления, а уменьшается каждый раз в середине». Причина: у слоя прекомпа в мастере стоят
Position и Scale, а Anchor Point никто не трогал — он оставался центром композиции прекомпа
(IW/2, H/2), хотя текст лежит НЕ в центре (Y базовых линий считает `intro_line_ys`, блок
опускают `intro_y` и `INTRO_IDY`). Любое уменьшение тянуло текст к середине кадра.

Теперь ключ `intro_scale_anchor` выбирает точку, от которой прекомп масштабируется:
"comp" — центр композиции (как раньше), "first" — Y первой строки блока, "block" —
середина между первой и последней строкой. Якорь и компенсацию Position считает Python
(plan_intro), шаблон только применяет; при "comp" подстановок нет вовсе.

Здесь:
  1. дефолт "comp" — .jsx прежний байт в байт: и эталон `fixtures/golden_geometry.jsx`
     (не тронуто), и сборка с интро (явный comp = отсутствию ключа);
  2. "first" — в .jsx появляется Anchor Point `[IW/2, INTRO_ANCHOR_Y[gI]]` с Y первой
     строки из плана, а Position по Y отличается от прежней ровно на `(ys[0] − H/2)·S`;
  3. картинка не сдвинулась: положение точки `ys[0]` в родительских координатах
     (`Position + (ys[0] − Anchor)·S`) при "comp" и при "first" совпадает до сотых —
     смена якоря меняет только центр масштабирования;
  4. уменьшение идёт ОТ ЯКОРЯ: при "first" первая строка стоит на месте при ds 100 → 60,
     при "comp" уезжает;
  5. не на пустом месте: на сборке без якоря (так выглядит игнорирование ключа) проверки
     2–4 краснеют — Anchor Point остаётся центром композиции, а текст уезжает.

Числа — формулы сборки (метрика шрифта задана формулами, как в test_intro_fit_safe), а не
то, какие шрифты стоят на машине. Сценарий теста 4 намеренно без опускания под безопасную
зону (`intro_line_step = 50`): у `INTRO_IDY` своя зависимость от ds, и её дрейф смазал бы
проверку якоря.
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

from core import fonts, xml2ae  # noqa: E402
from core.xml2ae.layout import INTRO_BASE_Y, INTRO_SCALE  # noqa: E402

T_CAM1 = 1.0
W, H = 1080.0, 1920.0            # кадр фикстуры timeline_subs.xml.gz
FSIZE = 140                      # кегль интро = max(60, int(W*0.13))
LETTER = 0.5                     # ширина буквы в кеглях (метрика фикстуры metrics)
MODE, MODE2 = "first", "comp"    # проверяемый режим и дефолт

# Три обычные строки: ys = 800, 960, 1120 при шаге 160 — первая строка НЕ в центре кадра
# (иначе якорь «первая строка» совпал бы с центром композиции и проверка была бы пустой).
THREE = [dict(words=["ПЕРВАЯ"], color="white", times=[T_CAM1]),
         dict(words=["ВТОРАЯ"], color="white", times=[T_CAM1 + 0.4]),
         dict(words=["ТРЕТЬЯ"], color="white", times=[T_CAM1 + 0.8])]
# То же, но с ручным масштабом группы 60 % (gs головной строки) — для теста «уменьшение
# идёт от якоря». Безопасная зона тут не срабатывает ни при одном ds: шаг строк 80 px.
THREE_SMALL = [dict(THREE[0], gs=60)] + THREE[1:]
SMOOTH = {"intro_line_step": 50}


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


@pytest.fixture()
def metrics(monkeypatch):
    """Метрики шрифта — формулы: буква 0.5 кегля. Числа теста не зависят от шрифтов машины."""
    monkeypatch.setattr(fonts, "text_width",
                        lambda ps, text, size: LETTER * float(size) * len(text))
    monkeypatch.setattr(fonts, "ink_extent", lambda ps, text, size: None)


def _scene(xml, style=None, intro=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=THREE if intro is None else intro, intro_splits=[],
                             style=dict(style or {}), emit=lambda *a, **k: None)


def _build(xml, tmp_path, style=None, intro=None, name="anchor.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=THREE if intro is None else intro, intro_splits=[],
                                   style=dict(style or {}), intro_mode="word", disclaimer="",
                                   intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _group(xml, style=None, intro=None):
    """Группа интро из плана (числа теста — те же, что уехали в .jsx)."""
    plan = _scene(xml, style=style, intro=intro)
    assert len(plan["intro"]) == 1, "предпосылка теста: одна группа интро"
    p = plan["intro"][0]
    assert p["ds"] == 100 or "gs" in (intro or THREE)[0], (
        "предпосылка теста: группу не тронул автофит (ds = %s)" % p["ds"])
    assert p["dy"] == 0, "предпосылка теста: группа без ручного сдвига"
    return p


def _jsx_arr(jsx, name):
    """Массив `NAME=[...]` из .jsx — по парным скобкам (объявление может быть не первым
    в строке: `var INTRO_ANCHOR_Y=…, INTRO_ANCHOR_DY=…;`)."""
    m = re.search(r"(?<![\w$])%s=\[" % re.escape(name), jsx)
    assert m, "в .jsx нет массива %s" % name
    i = m.end() - 1
    depth = 0
    for j in range(i, len(jsx)):
        if jsx[j] == "[":
            depth += 1
        elif jsx[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(jsx[i:j + 1])
    raise AssertionError("не сошлись скобки у массива %s" % name)


def _anchor_of(jsx, h=H):
    """(Anchor Point по Y, Position по Y) слоя прекомпа из .jsx — как их выставит шаблон.

    Якоря в .jsx нет (дефолт "comp") — это центр композиции прекомпа: AE ставит его сам.
    Компенсация Position приезжает готовым числом (INTRO_ANCHOR_DY), поэтому Position по Y —
    это база (-INTRO_BASE_Y + iDy) плюс она.
    """
    pos = -INTRO_BASE_Y + _jsx_arr(jsx, "INTRO_IDY")[0]
    if not re.search(r"(?<![\w$])INTRO_ANCHOR_Y=\[", jsx):
        return h / 2.0, pos
    assert 'setValue([IW/2, INTRO_ANCHOR_Y[gI]]);' in jsx, "якорь не ставится в .jsx"
    assert re.search(r"Position\"\)\.setValue\(\[gDx,-520\.7894\+iDy\+gDy"
                     r"\+INTRO_ANCHOR_DY\[gI\]\]\)", jsx), \
        "компенсация не доехала до Position прекомпа"
    return _jsx_arr(jsx, "INTRO_ANCHOR_Y")[0], pos + _jsx_arr(jsx, "INTRO_ANCHOR_DY")[0]


def _line_pos(pos_y, line_y, anchor_y, ds):
    """Положение точки line_y в родительских координатах: Position + (line_y − Anchor)·S.

    S = iSc/100 — масштаб слоя прекомпа (96.8 % × ds/100), ровно как в .jsx.
    """
    return pos_y + (line_y - anchor_y) * (INTRO_SCALE / 100.0) * (ds / 100.0)


# ======================================================== 1. дефолт "comp" — как было

def test_comp_keeps_the_golden_and_adds_nothing(xml_subs, tmp_path):
    """1. Дефолт "comp" не меняет .jsx: эталон golden_geometry.jsx побайтово, а сборка с
    интро с явным comp совпадает со сборкой без ключа и не несёт ни INTRO_ANCHOR, ни якоря.

    Эталон НЕ обновляется: разошёлся — значит дефолт неверен (эталон стерегут и
    test_geometry_python, и test_r11_li_every_knob).
    """
    from tests.test_geometry_python import _build as _golden_build, _mask_assets

    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    explicit = _mask_assets(_golden_build(xml_subs, tmp_path,
                                          style={"intro_scale_anchor": "comp"}))
    assert explicit == golden, "явный comp разошёлся с эталоном golden_geometry.jsx"

    off = _build(xml_subs, tmp_path, {}, name="off.jsx")
    on = _build(xml_subs, tmp_path, {"intro_scale_anchor": "comp"}, name="on.jsx")
    assert off == on, "явный comp изменил сборку с интро"
    assert "INTRO_ANCHOR" not in off, "при comp в .jsx появились подстановки якоря"
    assert "INTRO_LY" not in off, "фикстура не та: INTRO_LY появился на ровном месте"


# ======================================================== 2. якорь на первой строке

def test_first_puts_the_anchor_on_the_first_line(metrics, xml_subs, tmp_path):
    """2. При "first" в .jsx есть установка Anchor Point с Y = ys[0] из плана, а Position
    по Y отличается от прежней ровно на (ys[0] − H/2)·S."""
    p = _group(xml_subs, {**SMOOTH, "intro_scale_anchor": MODE})
    ys0 = p["ys"][0]
    assert ys0 != H / 2, "предпосылка теста: первая строка не в центре кадра"

    jsx = _build(xml_subs, tmp_path, {**SMOOTH, "intro_scale_anchor": MODE}, name="f.jsx")
    ay, pos = _anchor_of(jsx)
    _, pos_comp = _anchor_of(_build(xml_subs, tmp_path, {**SMOOTH,
                                                         "intro_scale_anchor": MODE2},
                                    name="c.jsx"))

    assert ay == ys0, "Anchor Point не на первой строке блока: %s != %s" % (ay, ys0)
    assert _jsx_arr(jsx, "INTRO_ANCHOR_Y")[0] == ys0, "массив якорей разошёлся с планом"
    shift = (ys0 - H / 2) * (INTRO_SCALE / 100.0) * (p["ds"] / 100.0)
    assert pos - pos_comp == pytest.approx(shift, abs=1e-4), \
        "Position по Y отличается от прежней не на (ys[0] − H/2)·S"


def test_block_anchor_is_the_middle_of_the_block(metrics, xml_subs, tmp_path):
    """2б. Режим "block" — середина между первой и последней строкой блока."""
    p = _group(xml_subs, {**SMOOTH, "intro_scale_anchor": "block"})
    jsx = _build(xml_subs, tmp_path, {**SMOOTH, "intro_scale_anchor": "block"}, name="b.jsx")
    ay, pos = _anchor_of(jsx)
    ys = p["ys"]
    assert ay == round((ys[0] + ys[-1]) / 2.0, 2), "якорь не посередине блока строк"
    shift = (ay - H / 2) * (INTRO_SCALE / 100.0) * (p["ds"] / 100.0)
    assert pos - (-INTRO_BASE_Y + _jsx_arr(jsx, "INTRO_IDY")[0]) == pytest.approx(shift, abs=1e-4)


def test_plan_carries_the_anchor_for_the_preview(metrics, xml_subs, tmp_path):
    """2в. План отдаёт превью и якорь, и компенсированную базу: transform-origin ставится
    по готовому anchor_y (второго чтения ключей стиля во фронте нет), а y уже включает
    компенсацию (G·(Ya − H/2)·S, кадровые px) — ровно то, что делает .jsx с Position."""
    style = {**SMOOTH, "intro_scale_anchor": MODE}
    plan = _scene(xml_subs, style)
    p = plan["intro"][0]
    jsx = _build(xml_subs, tmp_path, style, name="p.jsx")
    ay, pos = _anchor_of(jsx)

    assert p["anchor_y"] == ay, "план и .jsx разошлись в точке якоря"
    assert p["y"] == pytest.approx(pos * (plan["intro_scale"] / 100.0), abs=0.01), \
        "y плана не совпал с Position прекомпа: превью покажет не то, что соберётся"
    # Компенсация в y — та же добавка, что уехала в Position (сдвиг = ровно INTRO_ANCHOR_DY
    # в кадровых px: масштаб нула «интро» 100 %).
    assert (p["y"] - (-INTRO_BASE_Y + _jsx_arr(jsx, "INTRO_IDY")[0])) == \
        pytest.approx(_jsx_arr(jsx, "INTRO_ANCHOR_DY")[0], abs=0.01)

    # У дефолта поля нет вовсе: origin остаётся прежним — центром контейнера (golden).
    assert "anchor_y" not in _group(xml_subs, {**SMOOTH, "intro_scale_anchor": MODE2})


# ======================================================== 3. картинка не сдвинулась

def test_the_picture_does_not_move(metrics, xml_subs, tmp_path):
    """3. Главная проверка: положение точки ys[0] в родительских координатах
    (Position + (ys[0] − Anchor)·S) при "comp" и при "first" совпадает до сотых.

    Смена якоря не двигает текст — меняется только центр масштабирования.
    """
    p = _group(xml_subs, {**SMOOTH, "intro_scale_anchor": MODE})
    ys0, ds = p["ys"][0], p["ds"]

    ay_c, pos_c = _anchor_of(_build(xml_subs, tmp_path, {**SMOOTH,
                                                         "intro_scale_anchor": MODE2},
                                    name="cmp.jsx"))
    jsx_f = _build(xml_subs, tmp_path, {**SMOOTH, "intro_scale_anchor": MODE}, name="fst.jsx")
    ay_f, pos_f = _anchor_of(jsx_f)

    assert ay_f == ys0 and ay_c == H / 2, (
        "ключ не доехал до .jsx (якорь не поставлен) — сравнивать нечего")
    assert round(_line_pos(pos_c, ys0, ay_c, ds), 2) == round(_line_pos(pos_f, ys0, ay_f, ds), 2), \
        "смена якоря сдвинула текст: %s != %s" % (_line_pos(pos_c, ys0, ay_c, ds),
                                                  _line_pos(pos_f, ys0, ay_f, ds))


# ======================================================== 4. уменьшение идёт от якоря

def test_shrink_goes_from_the_anchor(metrics, xml_subs, tmp_path):
    """4. При "first" и уменьшенном ds группы первая строка стоит на месте, при "comp" —
    уезжает (её положение в родительских координатах меняется).

    Сценарий без опускания под безопасную зону (intro_line_step = 50): у INTRO_IDY своя
    зависимость от ds, и её дрейф смазал бы проверку якоря. Предпосылка — iDy = 0 при обоих ds.
    """
    def _at(mode, intro):
        style = {**SMOOTH, "intro_scale_anchor": mode}
        jsx = _build(xml_subs, tmp_path, style, intro=intro,
                     name="%s_%s.jsx" % (mode, intro[0].get("gs", 100)))
        return _group(xml_subs, style, intro=intro), _anchor_of(jsx), \
            _jsx_arr(jsx, "INTRO_IDY")[0]

    for mode in (MODE, MODE2):
        big, (ay100, pos100), idy100 = _at(mode, THREE)
        small, (ay60, pos60), idy60 = _at(mode, THREE_SMALL)
        assert idy100 == 0 and idy60 == 0, \
            "предпосылка теста: безопасная зона не срабатывает ни при одном ds"
        assert small["ds"] == 60 and big["ds"] == 100, "ручной масштаб группы не доехал до ds"
        ys0 = big["ys"][0]
        p100 = _line_pos(pos100, ys0, ay100, big["ds"])
        p60 = _line_pos(pos60, ys0, ay60, small["ds"])
        if mode == MODE:
            assert round(p60, 2) == round(p100, 2), (
                "при якоре «первая строка» уменьшение сдвинуло первую строку: %s -> %s"
                % (p100, p60))
        else:
            assert abs(p60 - p100) > 1.0, (
                "при якоре «центр композиции» первая строка обязана уезжать вместе с текстом")


# ======================================================== 5. не на пустом месте

def test_without_the_anchor_the_checks_would_be_red(metrics, xml_subs, tmp_path):
    """5. Так выглядит игнорирование ключа (сборка без якоря): проверки 2–4 краснеют —
    Anchor Point остаётся центром композиции, а уменьшение тянет текст к середине кадра."""
    p = _group(xml_subs, {**SMOOTH, "intro_scale_anchor": MODE2})
    ys0 = p["ys"][0]
    jsx = _build(xml_subs, tmp_path, {**SMOOTH, "intro_scale_anchor": MODE2}, name="no.jsx")

    ay, pos = _anchor_of(jsx)
    assert ay == H / 2, "якоря нет — это и есть игнорирование ключа"
    assert ay != ys0, "тест 2 на такой сборке обязан краснеть"
    assert "INTRO_ANCHOR" not in jsx, "тест 2 на такой сборке обязан краснеть (нет подстановок)"

    small = _group(xml_subs, {**SMOOTH, "intro_scale_anchor": MODE2}, intro=THREE_SMALL)
    jsx60 = _build(xml_subs, tmp_path, {**SMOOTH, "intro_scale_anchor": MODE2},
                   intro=THREE_SMALL, name="no60.jsx")
    ay60, pos60 = _anchor_of(jsx60)
    assert round(_line_pos(pos60, ys0, ay60, small["ds"]), 2) != \
        round(_line_pos(pos, ys0, ay, p["ds"]), 2), \
        "тест 4 («остаётся тем же») на сборке без якоря обязан краснеть"

    # И сам ключ на месте: в BASE, в схеме и в переводах (сторож каждой ручки — отдельный).
    from core import style_schema, styles
    assert styles.BASE["intro_scale_anchor"] == "comp", "дефолт ключа не comp"
    fields = [x for layer in style_schema.LAYERS
              for it in [layer]
              for x in _fields(it)]
    anchor = [f for f in fields if f.get("key") == "intro_scale_anchor"]
    assert anchor, "ручки intro_scale_anchor нет в схеме"
    assert [o[0] for o in anchor[0]["options"]] == ["comp", "first", "block"], \
        "варианты ручки разъехались с решением"


def _fields(node):
    """Поля схемы под узлом (группы вложены друг в друга)."""
    out = []
    for it in node.get("items", []):
        if it.get("type") == "group":
            out += _fields(it)
        elif it.get("key"):
            out.append(it)
    return out


# ======================================================== 6. предпросмотр

JS_REL = ("static", "app", "85-inserts-view.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Эмуляция DOM: ровно то, что трогает ipvIntroPos. Контейнер #ipvintro — 540 px ширины
# (k = 540/1080 = 0.5), машина кадра подменена заглушкой: у неё свои тесты (test_zoom_z*).
DOM_SIM = r"""
const assert = require('assert');
class El { constructor(){ this.style = {}; this.clientWidth = 540; } }
const io = new El();
global.$ = (id) => (id === 'ipvintro' ? io : null);
global.ipvIntroChild = (x, y, tm) => [x, y, 1];
global.IPV = { plan: @PLAN@ };
"""


def _js_src():
    return io.open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()


def _func(src, name):
    """Тело функции из исходника: от `function name(` до парной закрывающей скобки."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в исходнике не нашлась функция %s" % name
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


def _run_node(tmp_path, name, script):
    node_file = str(tmp_path / name)
    with io.open(node_file, "w", encoding="utf-8") as f:
        f.write(script)
    return subprocess.run(["node", node_file], capture_output=True, text=True,
                          encoding="utf-8-sig", timeout=60)


@node
def test_node_preview_origin_is_the_anchor_from_the_plan(tmp_path):
    """6. Превью ставит transform-origin по anchor_y из плана: 880/1920 = 45.833 %.

    Той же точкой блок масштабируется и в AE (Anchor Point слоя прекомпа), поэтому
    уменьшение в превью видно ровно таким, каким соберётся .jsx. Поля нет (дефолт
    «центр композиции») — origin снимается, блок снова масштабируется от центра.
    """
    plan = {"w": 1080, "h": 1920, "intro_scale": 100,
            "intro": [{"dx": 0, "dy": 0, "y": 100, "ds": 100, "anchor_y": 880}]}
    checks = r"""
    ipvIntroPos(0, 2.0);
    assert.strictEqual(io.style.transformOrigin, '50% 45.833%',
      'origin не в точке якоря: ' + io.style.transformOrigin);
    assert.strictEqual(io.style.transform, 'translate(0px,50px) scale(0.968)',
      'позиция/масштаб блока поехали: ' + io.style.transform);
    delete IPV.plan.intro[0].anchor_y;
    ipvIntroPos(0, 2.0);
    assert.strictEqual(io.style.transformOrigin, '',
      'origin остался от прошлой группы: ' + io.style.transformOrigin);
    console.log("OK: preview origin is the anchor");
    """
    src = DOM_SIM.replace("@PLAN@", json.dumps(plan, ensure_ascii=False)) + "\n" \
        + _func(_js_src(), "ipvIntroPos") + "\n" + checks
    res = _run_node(tmp_path, "test_anchor_origin.js", src)
    assert res.returncode == 0, "Node.js script failed: %s\n%s" % (res.stderr, res.stdout)
    assert "OK: preview origin is the anchor" in res.stdout


@node
def test_node_group_windows_pass_the_anchor(tmp_path):
    """6б. Сторож двери: introGroupWindows протаскивает anchor_y группы из плана."""
    script = "const assert = require('assert');\n" + _func(_js_src(), "introGroupWindows") + r"""
    const out = introGroupWindows([{ts: 1.0, te: 5.0, lines: [{words: ['A']}], anchor_y: 880}]);
    assert.strictEqual(out.length, 1);
    assert.strictEqual(out[0].anchor_y, 880, 'anchor_y не протащен из плана');
    assert.strictEqual(introGroupWindows([{ts: 1.0, te: 5.0, lines: [{words: ['A']}]}])[0].anchor_y,
      undefined, 'поле появилось там, где его не было');
    console.log("OK: group windows pass the anchor");
    """
    res = _run_node(tmp_path, "test_anchor_win.js", script)
    assert res.returncode == 0, "Node.js script failed: %s\n%s" % (res.stderr, res.stdout)
    assert "OK: group windows pass the anchor" in res.stdout


def test_preview_takes_the_anchor_from_the_plan():
    """6в. Второго чтения ключей стиля во фронте нет: точка — из плана, не из CURSTYLE."""
    body = _func(_js_src(), "ipvIntroPos")
    assert "g.anchor_y" in body, "превью перестало брать точку масштабирования из плана"
    assert "CURSTYLE" not in body, "точка якоря считается из стиля, а не берётся из плана"
