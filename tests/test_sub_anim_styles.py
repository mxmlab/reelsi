# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стили субтитров из каталога (партия 1): пресеты `wipe` и `weight`, размер жёлтых.

Продолжение пилота `tests/test_sub_anim.py` — тот же путь данных на три выхода:
пресет стиля -> ключи на КАЖДОЕ слово (Python, `core/xml2ae/layout.py`) -> план сцены
-> .jsx (те же ключи на слой слова) и превью (те же ключи на DOM-спан слова). Ни .jsx,
ни превью кривых не считают — только интерполируют ключи плана.

Проверяется:

1. `wipe` — слово открывается слева направо: у каждого белого слова ключ `wp` 0 -> 100
   за `dur`, прозрачность нейтральна (слово открывается, а не проявляется), движений,
   роста и блюра нет. В .jsx — маска-прямоугольник на слое слова;
2. `weight` — тонкое начертание (`sub_anim_font`) меняется на основное СТУПЕНЬКОЙ в
   середине `dur` (ключ `ft`), плюс лёгкое проявление. Тонкого шрифта нет — ключа нет,
   пресет работает одной прозрачностью;
3. жёлтые слова пресеты не трогают: у них своя анимация появления;
4. `hl_size_k` — множитель кегля жёлтого слова: при 1.0 .jsx и превью прежние, при
   другом числе кегль жёлтого в .jsx (во всех четырёх циклах субтитров) и в разметке
   превью;
5. превью (node, боевые функции из static/app/85-inserts-view.js): середина открытия и
   ступенька начертания совпадают с ключами плана, после анимации следов нет;
6. мутация: превью игнорирует ключ открытия — стенд краснеет (проверка, что стенд
   стережёт не пустоту);
7. умолчания: `sub_anim="none"`, `hl_size_k=1.0` и `sub_anim_font=None` не меняют
   собранный .jsx ни на байт (эталон fixtures/golden_geometry.jsx).
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
sys.path.insert(0, HERE)

from core import style_geometry, styles  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402
from tests.test_geometry_python import _build, _mask_assets  # noqa: E402
# Стенд превью (эмуляция DOM) и вырезка боевых функций — ОДНА на два модуля стилей:
# вторая копия стенда разошлась бы с первой молча.
from tests.test_sub_anim import _DOM_SIM, _ipv_code  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

AMT = 90.0          # сила появления в тестах: дефолт пресета в проверках не участвует
DUR = 0.4           # …и своя длительность
THIN = "Geologica-Thin"     # тонкое начертание пресета weight
YELLOW = 3          # индекс жёлтого слова фикстуры
HL_W = 1.0          # ширина кадра плана в тестах — для пересчёта кегля в cqw


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, preset="wipe", style=None, highlights=(YELLOW,)):
    """План сцены с пресетом появления; по умолчанию — «открывается слева направо»."""
    st = {"sub_anim": preset, "sub_anim_dur": DUR, "sub_anim_amt": AMT}
    st.update(style or {})
    return xml2ae.scene_plan(xml, highlights=list(highlights), style=st,
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, preset="wipe", style=None, highlights=(YELLOW,),
         hl_joins=None):
    st = {"sub_anim": preset, "sub_anim_dur": DUR, "sub_anim_amt": AMT}
    st.update(style or {})
    path, _n, _s = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                     highlights=list(highlights), style=st,
                                     hl_joins=(list(hl_joins) if hl_joins else None),
                                     inserts=[], disclaimer="", intro=None, roto=False,
                                     intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _anim_body(jsx):
    """Тело функции subAnimKeys: у каждого пресета в нём ТОЛЬКО его механизмы."""
    m = re.search(r"function subAnimKeys\(L, t0, y, half\)\{(.*?)\n    \}", jsx, re.S)
    assert m, "в .jsx нет функции subAnimKeys"
    return m.group(1)


def _white(plan):
    """Белые слова плана с ключами появления — и в режиме слов, и в режиме строк."""
    out = []
    for it in plan["subs"]:
        if it["color"] != "yellow" and it.get("anim"):
            out.append(it)
        for wd in it.get("words") or []:
            if wd.get("color") != "yellow" and wd.get("anim"):
                out.append(wd)
    return out


def _word_with_room(plan, preset, dur=DUR):
    """Белое слово, чьё появление целиком укладывается в его видимое окно.

    Иначе спан исчезнет раньше, чем анимация доиграет, и проверять «после» не на чем.
    """
    for it in _white(plan):
        if it["anim"]["name"] != preset:
            continue
        if it["gend"] - it["s"] > dur + 0.05:
            return it
    raise AssertionError("в фикстуре не нашлось белого слова с запасом окна")


def _preview_plan(plan):
    """Только то, что читает ipvSubs: числа превью берёт из плана, своих не держит."""
    keys = ("w", "h", "posy", "fsize", "sub_step", "hl_step", "hl_rise", "hl_dur",
            "hl_blur", "hl_blur_amt", "subs", "hl_size_k", "sub_anim_font")
    return {k: plan[k] for k in keys if k in plan}


def _run_preview(tmp_path, name, plan, checks, mutate="", sim_extra=""):
    """Прогнать боевую отрисовку субтитров в node: эмуляция DOM + проверки.

    `mutate` — готовая мутация (кусок JS перед проверками), `sim_extra` — настройка
    стенда (список шрифтов и т.п.). Стенд тот же, что у пилота: вторая копия
    эмуляции DOM разошлась бы с первой молча.
    """
    script = (_DOM_SIM.replace("@PLAN@", json.dumps(_preview_plan(plan), ensure_ascii=False))
              + "\n" + _ipv_code() + "\n" + sim_extra + "\n" + mutate + "\n"
              + "const plan=IPV.plan;\n" + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    return subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


def _expect_ok(res, what):
    assert res.returncode == 0, "%s: node упал: %s" % (
        what, ((res.stderr or "") + (res.stdout or ""))[-2000:])
    assert "OK" in res.stdout, "%s: проверки превью не прошли: %s" % (what, res.stdout)


# --------------------------------------------------------------------------- #
# 1. `wipe`: открытие слева направо — план и .jsx
# --------------------------------------------------------------------------- #
def test_wipe_opens_every_white_word(xml_subs):
    """`wipe`: ключ открытия 0 -> 100 на КАЖДОМ белом слове, движений и роста нет."""
    plan = _plan(xml_subs, "wipe")
    checked = 0
    for it in plan["subs"]:
        if it["color"] == "yellow":
            assert "anim" not in it, "жёлтому слову выдали анимацию пресета: %r" % (it,)
            continue
        a = it["anim"]
        t0, t1 = it["s"], round(it["s"] + DUR, 4)
        assert a["name"] == "wipe"
        assert a["dur"] == DUR
        assert a["wp"] == [[t0, 0.0], [t1, 100.0]], \
            "доля открытия не 0 -> 100: %r" % (a,)
        # Прозрачность нейтральна: слово ОТКРЫВАЕТСЯ шторкой, а не проявляется.
        assert a["op"] == [[t0, 100.0], [t1, 100.0]], \
            "открытие получило проявление — это другая анимация: %r" % (a,)
        assert not ({"dy", "bl", "sc", "ft"} & set(a)), \
            "у открытия завелось движение, рост, блюр или начертание: %r" % (a,)
        checked += 1
    assert checked > 5, "в фикстуре не нашлось белых слов: %d" % checked


def test_wipe_reaches_words_inside_a_row(xml_subs):
    """Режим строк: открытие есть у КАЖДОГО слова строки, а не только у первого."""
    plan = _plan(xml_subs, "wipe", style={"sub_words_per_row": 2}, highlights=[])
    rows = [it for it in plan["subs"] if len(it.get("words") or []) > 1]
    assert rows, "в фикстуре не нашлось строк из двух слов"
    for row in rows:
        for wd in row["words"]:
            a = wd["anim"]
            assert a["name"] == "wipe", "в строке не открытие: %r" % (a,)
            assert a["wp"] == [[wd["s"], 0.0], [round(wd["s"] + DUR, 4), 100.0]], \
                "ключи открытия не на времени слова: %r" % (a,)


def test_wipe_preset_defaults(xml_subs):
    """Числа по умолчанию — у пресета, а силы у открытия нет вовсе."""
    w = layout.sub_anim_preset("wipe", 0, None)
    assert (w.dur, w.rise, w.blur, w.scale, w.op) == (0.25, 0.0, 0.0, 100.0, 100.0)
    # Сила появления у открытия молчит: 400 px подъёма к нему не относятся.
    loud = layout.sub_anim_preset("wipe", 0, 400)
    assert (loud.dur, loud.rise, loud.scale) == (0.25, 0.0, 100.0)
    # Своя длительность стиля перебивает пресетную — у всех пресетов одинаково.
    assert layout.sub_anim_preset("wipe", 0.5, None).dur == 0.5


def test_jsx_wipe_masks_the_word(xml_subs, tmp_path):
    """`wipe` в .jsx: маска-прямоугольник, правый край едет от левого края текста к правому."""
    jsx = _jsx(xml_subs, tmp_path, "wipe.jsx", "wipe")
    assert "var HL_W_DUR = %s" % DUR in jsx, "длительность пресета не уехала в .jsx"
    assert "try{ subAnimKeys(L, t0, POSY, HL_W_SC); }catch(e){}" in jsx
    body = _anim_body(jsx)
    assert "ADBE Mask Parade" in body and "ADBE Mask Shape" in body, \
        "у открытия нет маски на слое слова: %r" % body
    assert "subWipeRect(wx, wt, rr.left, wb)" in body, \
        "маска не начинается на левом крае слова"
    assert "subWipeRect(wx, wt, rr.left+rr.width, wb)" in body, \
        "маска не открывает слово целиком"
    assert "maskFeather = [0, 0]" in body, "край маски мягкий — в превью он жёсткий"
    assert "easePair(mp)" in body, "ключи маски поставлены без кривой (easePair)"
    assert "function subWipeRect(x1, y1, x2, y2){" in jsx, "нет построения прямоугольника"
    # Открытие — и только оно: ни подъёма, ни роста, ни начертания, ни блюра.
    for foreign in ("ADBE Position", "ADBE Scale", "ADBE Text Document",
                    "Gaussian Blur 2"):
        assert foreign not in body, "у открытия появилось чужое свойство: %s" % foreign
    # Доля открытия в ключах плана и в .jsx одна: и то и другое считается от ширины
    # текста, второй копии числа нет.
    assert "var SUBS=" in jsx and "hl_fsz" not in jsx


# --------------------------------------------------------------------------- #
# 2. `weight`: тонкое начертание ступенькой — план и .jsx
# --------------------------------------------------------------------------- #
def test_weight_steps_the_typeface(xml_subs):
    """`weight`: ступенька 1 -> 0 в середине dur и лёгкое проявление на тех же ключах."""
    plan = _plan(xml_subs, "weight", style={"sub_anim_font": THIN})
    assert plan["sub_anim_font"] == THIN, "тонкое начертание не уехало в план"
    checked = 0
    for it in plan["subs"]:
        if it["color"] == "yellow":
            assert "anim" not in it
            continue
        a = it["anim"]
        t0, t1 = it["s"], round(it["s"] + DUR, 4)
        mid = round(t0 + DUR * layout.SUB_ANIM_WEIGHT_SWITCH_AT, 4)
        assert a["name"] == "weight"
        assert a["ft"] == [[t0, 1.0], [mid, 0.0]], \
            "ступенька начертания не в середине появления: %r" % (a,)
        assert a["op"] == [[t0, layout.SUB_ANIM_WEIGHT_OP], [t1, 100.0]], \
            "проявление у начертания не лёгкое: %r" % (a,)
        assert not ({"dy", "bl", "sc", "wp"} & set(a)), \
            "у начертания завелось движение, рост, блюр или открытие: %r" % (a,)
        checked += 1
    assert checked > 5


def test_weight_without_font_is_opacity_only(xml_subs):
    """Тонкого начертания нет — ступенить нечего: только прозрачность, как в подсказке."""
    plan = _plan(xml_subs, "weight")
    assert plan["sub_anim_font"] == ""
    words = _white(plan)
    assert words, "в фикстуре не нашлось белых слов"
    for wd in words:
        a = wd["anim"]
        assert "ft" not in a, "ступенька начертания без шрифта: %r" % (a,)
        assert a["op"] == [[a["op"][0][0], layout.SUB_ANIM_WEIGHT_OP],
                           [a["op"][-1][0], 100.0]], "проявление не лёгкое: %r" % (a,)


def test_thin_font_is_read_only_by_weight(xml_subs, tmp_path):
    """`sub_anim_font` читает только пресет начертания: у соседей его в .jsx нет."""
    thin = {"sub_anim_font": THIN}
    assert "HL_W_FONT" in _jsx(xml_subs, tmp_path, "w_weight.jsx", "weight", thin)
    for preset in ("wipe", "rise", "pop"):
        jsx = _jsx(xml_subs, tmp_path, "w_%s.jsx" % preset, preset, thin)
        assert "HL_W_FONT" not in jsx, "начертание уехало в чужой пресет %s" % preset
    assert _plan(xml_subs, "wipe", style=thin)["sub_anim_font"] == ""
    assert _plan(xml_subs, "rise", style=thin)["sub_anim_font"] == ""


def test_jsx_weight_switches_the_source_text(xml_subs, tmp_path):
    """`weight` в .jsx: два держащих ключа Source Text и лёгкая прозрачность."""
    jsx = _jsx(xml_subs, tmp_path, "weight.jsx", "weight", {"sub_anim_font": THIN})
    assert 'var HL_W_FONT = "%s";' % THIN in jsx, "тонкое начертание не уехало в .jsx"
    body = _anim_body(jsx)
    assert "ADBE Text Document" in body, "начертание меняется не через Source Text"
    # Оба документа снимаются ДО первой установки ключа: иначе «основное» начертание
    # получилось бы тонким (value после ключа отдаёт значение ключа).
    assert body.index("var db = td.value;") < body.index("td.setValueAtTime(t0, dt)")
    assert "td.setValueAtTime(t0, dt)" in body
    assert "td.setValueAtTime(t0+HL_W_DUR*0.5, db)" in body, \
        "начертание меняется не в середине появления"
    assert "KeyframeInterpolationType.HOLD" in body, \
        "начертание меняется плавно — осей вариативного шрифта AE не анимирует"
    assert "op.setValueAtTime(t0, %g)" % layout.SUB_ANIM_WEIGHT_OP in body
    for foreign in ("ADBE Mask Parade", "ADBE Scale", "ADBE Position", "Gaussian Blur 2"):
        assert foreign not in body, "у начертания появилось чужое свойство: %s" % foreign


def test_jsx_weight_without_font_stays_opacity_only(xml_subs, tmp_path):
    """Без тонкого начертания .jsx не трогает Source Text вовсе."""
    jsx = _jsx(xml_subs, tmp_path, "weight_plain.jsx", "weight")
    assert "HL_W_FONT" not in jsx
    body = _anim_body(jsx)
    assert "ADBE Text Document" not in body and "HOLD" not in body
    assert "op.setValueAtTime(t0, %g)" % layout.SUB_ANIM_WEIGHT_OP in body


def test_rise_keeps_only_its_own_motion(xml_subs, tmp_path):
    """Соседние пресеты не получили чужого: у въезда снизу нет ни роста, ни открытия.

    Заодно стережётся найденная на пилоте лишняя анимация: рост 100 -> 108 -> 100
    стоял в .jsx у КАЖДОГО пресета (ветка «half > 0» пускала и нейтральные 100),
    хотя в плане масштаба у `rise` нет — превью и AE расходились.
    """
    jsx = _jsx(xml_subs, tmp_path, "rise.jsx", "rise")
    body = _anim_body(jsx)
    assert "ADBE Scale" not in body, "у въезда снизу остался рост из чужого пресета"
    assert "ADBE Mask Parade" not in body and "ADBE Text Document" not in body
    assert "Gaussian Blur 2" in body, "у въезда снизу пропал блюр появления"
    assert "p.setValueAtTime(t0, [X, by+HL_W_RISE]);" in body
    # У роста масштаб СВОЙ и остаётся: это его единственное движение.
    pop = _anim_body(_jsx(xml_subs, tmp_path, "pop.jsx", "pop", {"sub_anim_amt": 60}))
    assert "ADBE Scale" in pop and "sc.setValueAtTime(t0, [half, half]);" in pop
    assert "ADBE Mask Parade" not in pop and "ADBE Text Document" not in pop


# --------------------------------------------------------------------------- #
# 3. `hl_size_k`: множитель кегля жёлтого слова
# --------------------------------------------------------------------------- #
def test_hl_size_k_multiplies_the_highlight(xml_subs, tmp_path):
    """`hl_size_k` = 1.5: кегль жёлтого умножен в цикле слов, объявление одно на сборку."""
    jsx = _jsx(xml_subs, tmp_path, "k.jsx", "none", {"hl_size_k": 1.5})
    assert "var HL_SIZE_K = 1.5;" in jsx, "множитель кегля не уехал в .jsx"
    assert "d.fontSize=(hl?FONT_SIZE*HL_SIZE_K:FONT_SIZE);" in jsx, \
        "в цикле «по слову» кегль жёлтого не умножен (или умножен и у белых)"
    assert "Math.floor((hl?FONT_SIZE*HL_SIZE_K:FONT_SIZE)*FITW/rr.width)" in jsx, \
        "ужатие длинного слова считается не тем кеглем, каким слово нарисовано"


def test_hl_size_k_in_rows_and_stack(xml_subs, tmp_path):
    """Тот же множитель в цикле строк (кегль строки) и в циклах стопки (слова только жёлтые)."""
    rows = _jsx(xml_subs, tmp_path, "k_rows.jsx", "none",
                {"hl_size_k": 1.4, "sub_words_per_row": 2})
    assert "d.fontSize=(w_hl?cur_fsz*HL_SIZE_K:cur_fsz);" in rows, \
        "в цикле строк жёлтое слово рисуется базовым кеглем"
    assert "Math.floor(cur_fsz*FITW/rr.width)" not in rows, \
        "в цикле строк остался замер без множителя"

    stack = _jsx(xml_subs, tmp_path, "k_stack.jsx", "none",
                 {"hl_size_k": 1.4, "sub_words_per_row": 2, "hl_row_stack": True},
                 highlights=(3, 4))
    assert "d.fontSize=FONT_SIZE*HL_SIZE_K;" in stack, \
        "в цикле стопки кегль жёлтого не умножен"
    assert "Math.floor(FONT_SIZE*HL_SIZE_K*FITW/rr.width)" in stack


def test_hl_size_k_default_keeps_the_build(xml_subs, tmp_path):
    """Умолчание 1.0: в .jsx нет ни множителя, ни объявления — прежний текст."""
    base = _jsx(xml_subs, tmp_path, "k_base.jsx", "none")
    one = _jsx(xml_subs, tmp_path, "k_one.jsx", "none", {"hl_size_k": 1.0})
    assert "HL_SIZE_K" not in base, "умолчание протащило множитель кегля в .jsx"
    assert "HL_SIZE_K" not in one
    assert base == one, "единица в стиле меняет сборку"


def test_hl_size_k_factor_is_safe():
    """Множитель читается безопасно: ноль, пусто и мусор — «как сегодня» (1.0)."""
    assert styles.BASE["hl_size_k"] == 1.0, "умолчание перестало быть «как сегодня»"
    assert layout.hl_size_factor(0) == 1.0 and layout.hl_size_factor(None) == 1.0
    assert layout.hl_size_factor("мусор") == 1.0, "мусор в стиле обязан давать 1.0"
    assert layout.hl_size_factor(-2) == 1.0
    assert layout.hl_size_factor(1.4) == 1.4
    assert layout.hl_size_decl(1.0) == "" and "HL_SIZE_K = 1.4" in layout.hl_size_decl(1.4)


def test_hl_size_k_is_marked_in_geometry():
    """Поле без разметки уехало бы в пиксели 1080 — множитель обязан быть «от кадра не зависит»."""
    assert "hl_size_k" in style_geometry.FRAME_INDEPENDENT
    assert style_geometry.kind_of("hl_size_k") is None, \
        "множитель размечен как пиксели кадра: в квадратном ролике кегль уехал бы"


def test_style_panel_offers_the_new_presets():
    """Панель: новые пункты списка, ручка тонкого начертания и множитель размера."""
    from core import style_schema
    fields = {}

    def walk(items):
        for x in items:
            if x.get("key"):
                fields[x["key"]] = x
            walk(x.get("items") or [])

    walk(style_schema.LAYERS)
    opts = [o[0] for o in fields["sub_anim"]["options"]]
    # Список — ровно пресеты layout.SUB_ANIM_PRESETS плюс "none": пункт панели без
    # пресета молча ничего не делал бы, а пресет без пункта — недостижим из интерфейса.
    assert opts == ["none", "rise", "pop", "wipe", "weight", "glitch"], \
        "список «Появление слов» разошёлся с пресетами: %r" % (opts,)
    assert fields["sub_anim_font"]["ctl"] == "font"
    assert fields["sub_anim_font"].get("nullable") and fields["sub_anim_font"]["list"]
    assert fields["sub_anim_font"]["show_if"] == {"key": "sub_anim", "eq": "weight"}, \
        "тонкое начертание видно у пресетов, которые его не читают"
    assert fields["sub_anim_amt"]["show_if"] == {"key": "sub_anim", "in": ["rise", "pop"]}, \
        "сила появления видна у пресетов, у которых её нет"
    assert fields["hl_size_k"]["min"] >= 0.5 and fields["hl_size_k"]["max"] <= 4
    assert styles.BASE["sub_anim_font"] is None


# --------------------------------------------------------------------------- #
# 4. Превью (node): те же ключи, ступенькой и без следов
# --------------------------------------------------------------------------- #
@node
def test_preview_wipe_opens_by_plan_keys(xml_subs, tmp_path):
    """Середина открытия у спана — ровно интерполяция ключей плана, после — следов нет."""
    # Режим строк: в строке два слова, и второе появляется ПОЗЖЕ её начала — значит его
    # спан уже на экране, когда ключей ещё нет (в AE это слой до inPoint). Длительность
    # короче: слово с анимацией целиком внутри своего видимого окна.
    plan = _plan(xml_subs, "wipe", highlights=[],
                 style={"sub_words_per_row": 2, "sub_anim_dur": 0.05})
    row = next(it for it in plan["subs"] if len(it.get("words") or []) > 1)
    word = next(wd for wd in row["words"]
                if wd.get("anim")
                and wd["anim"]["wp"][0][0] > row["s"] + 0.02
                and wd["anim"]["wp"][-1][0] + 0.01 < row["gend"])
    t0, t1 = word["anim"]["wp"][0][0], word["anim"]["wp"][-1][0]
    tm = t0 + (t1 - t0) / 2.0
    checks = r"""
const t0=%(t0)s, t1=%(t1)s, tm=%(tm)s, w=%(word)s, end=%(gend)s;
function span(){const sp=words().filter(s=>s.textContent===w);
  assert.strictEqual(sp.length,1,'слово '+w+' не одно в разметке');
  return sp[0];}
// Число из clip-path: inset(0 X процентов 0 0) — сколько слова ещё ЗАКРЫТО.
function cut(sp){const m=/^inset\(0\s+([0-9.]+)%%/.exec(sp.style.clipPath||'');
  return m?parseFloat(m[1]):NaN;}

// Строка уже видна (её окно открылось), а слово ещё не появилось: спан есть, но скрыт
// и закрыт целиком — как слой AE до inPoint.
assert(t0-0.01>%(row_s)s && t0-0.01<end,'строка не видна — проверка «до t0» стерегла бы пустоту');
paint(plan,t0-0.01);
assert(animated().length>0,'в разметке нет слов с ключами появления');
assert.strictEqual(span().style.visibility,'hidden','до t0 слово не скрыто');
assert.strictEqual(cut(span()),100,'до t0 слово не закрыто: '+span().style.clipPath);

// Середина: открытие РОВНО то, что даёт интерполяция ключей плана.
paint(plan,tm);
const a=JSON.parse(decodeURIComponent(span().dataset.wa));
assert.strictEqual(a.name,'wipe','превью взяло не плановые ключи: '+span().dataset.wa);
assert.notStrictEqual(span().style.visibility,'hidden','в середине слово всё ещё скрыто');
const open=100-cut(span()), exp=keysAt(a.wp,null,tm);
assert(open>0&&open<100,'середина обязана быть промежуточной: '+open);
assert(Math.abs(open-exp)<1e-3,'открытие не по ключам плана: '+open+' vs '+exp);
assert.strictEqual(parseFloat(span().style.opacity),1,'открытие получило ещё и проявление');
assert.strictEqual(span().style.filter,'','открытие получило блюр');
assert.strictEqual(span().style.transform,'','открытие получило масштаб');

// После открытия: финальное состояние, следов анимации нет.
assert(t1+0.01<end,'слово ушло раньше конца открытия — проверка «после» стерегла бы пустоту');
paint(plan,t1+0.01);
assert.strictEqual(span().style.clipPath,'','после открытия осталась подрезка');
assert.strictEqual(span().style.opacity,'','после открытия осталась прозрачность');
assert.notStrictEqual(span().style.visibility,'hidden','слово осталось скрытым после открытия');
console.log('OK: preview wipes by plan keys');
""" % {"t0": json.dumps(t0), "t1": json.dumps(t1), "tm": json.dumps(round(tm, 6)),
       "row_s": json.dumps(row["s"]), "gend": json.dumps(row["gend"]),
       "word": json.dumps(word["w"], ensure_ascii=False)}
    _expect_ok(_run_preview(tmp_path, "wz_wipe.js", plan, checks), "превью wipe")


@node
def test_preview_wipe_mutation_turns_red(xml_subs, tmp_path):
    """Мутация: превью игнорирует ключ открытия — стенд краснеет."""
    plan = _plan(xml_subs, "wipe", highlights=[], style={"sub_anim_dur": 0.05})
    word = _word_with_room(plan, "wipe", dur=0.05)
    a = word["anim"]
    t0, t1 = a["wp"][0][0], a["wp"][-1][0]
    checks = r"""
const w=%(word)s, tm=%(tm)s;
paint(plan,tm);
const sp=words().filter(s=>s.textContent===w)[0];
assert(sp,'не нашёлся спан слова '+w);
const a=JSON.parse(decodeURIComponent(sp.dataset.wa));
const m=/^inset\(0\s+([0-9.]+)%%/.exec(sp.style.clipPath||'');
const open=m?100-parseFloat(m[1]):NaN;
const exp=keysAt(a.wp,null,tm);
assert(open>0&&open<100,'открытие не промежуточное: '+open);
assert(Math.abs(open-exp)<1e-3,'открытие не по ключам плана: '+open+' vs '+exp);
console.log('OK: wipe uses the plan key');
""" % {"word": json.dumps(word["w"], ensure_ascii=False),
       "tm": json.dumps(round(t0 + (t1 - t0) / 2.0, 6))}
    ok = _run_preview(tmp_path, "wz_mut_ok.js", plan, checks)
    _expect_ok(ok, "превью wipe без мутации")
    # Мутация: слово рисуется, а ключ открытия в него не доехал (clip-path снят).
    mut = ("const _realAnim=ipvSubWordAnim;\n"
           "ipvSubWordAnim=function(w,tm){_realAnim(w,tm);w.style.clipPath='';};\n")
    bad = _run_preview(tmp_path, "wz_mut_bad.js", plan, checks, mutate=mut)
    assert bad.returncode != 0, "мутация НЕ покраснела — стенд стережёт пустоту"


@node
def test_preview_weight_steps_the_typeface(xml_subs, tmp_path):
    """Ступенька начертания в превью: до середины — тонкое, после — основное."""
    plan = _plan(xml_subs, "weight", highlights=[],
                 style={"sub_anim_font": THIN, "sub_anim_dur": 0.05})
    word = _word_with_room(plan, "weight", dur=0.05)
    a = word["anim"]
    t0, t1 = a["op"][0][0], a["op"][-1][0]
    mid = a["ft"][1][0]
    sim = r"""
// Стенд шрифтов: тонкое начертание и основное есть в системе (боевой ipvFontFor
// ищет их в FONTS) — иначе ступеньки не будет вовсе, как и в AE без шрифта.
global.FONTS=[{ps:'%(thin)s',family:'ThinFam',var:{wght:100}},
              {ps:'BasePS',family:'BaseFam',var:{wght:800}}];
global.CURSTYLE={font:'BasePS'};
global.ipvFontFor=function(ps){const f=FONTS.find(f=>f.ps===ps);
  return f?{family:'reelsi-'+ps,var:f.var,ps:ps}:null;};
""" % {"thin": THIN}
    checks = r"""
const t0=%(t0)s, t1=%(t1)s, mid=%(mid)s, w=%(word)s;
function span(){const sp=words().filter(s=>s.textContent===w);
  assert.strictEqual(sp.length,1,'слово '+w+' не одно в разметке');
  return sp[0];}
// До середины — тонкое начертание, и проявление ровно по ключам плана (лёгкое: не 0 и не 1).
const tm1=t0+(mid-t0)*0.5;
paint(plan,tm1);
assert.strictEqual(span().style.fontFamily,"'reelsi-%(thin)s'",
  'слово приходит не тонким начертанием: '+span().style.fontFamily);
const a=JSON.parse(decodeURIComponent(span().dataset.wa));
const expOp=keysAt(a.op,null,tm1)/100, op=parseFloat(span().style.opacity);
assert(op>0&&op<1,'середина обязана быть промежуточной: '+op);
assert(Math.abs(op-expOp)<1e-4,'прозрачность не по ключам плана: '+op+' vs '+expOp);
// После середины — основное, СТУПЕНЬКОЙ: промежуточного начертания не бывает.
const tm2=mid+(t1-mid)*0.5;
paint(plan,tm2);
assert.strictEqual(span().style.fontFamily,"'reelsi-BasePS'",
  'начертание не вернулось к основному: '+span().style.fontFamily);
// После появления следов не остаётся: шрифт основной, прозрачность снята.
paint(plan,t1+0.01);
assert.strictEqual(span().style.fontFamily,"'reelsi-BasePS'",
  'после появления осталось тонкое начертание');
assert.strictEqual(span().style.opacity,'','после появления осталась прозрачность');
console.log('OK: preview steps the typeface');
""" % {"t0": json.dumps(t0), "t1": json.dumps(t1), "mid": json.dumps(mid),
       "word": json.dumps(word["w"], ensure_ascii=False), "thin": THIN}
    _expect_ok(_run_preview(tmp_path, "wz_weight.js", plan, checks, sim_extra=sim),
               "превью weight")


@node
def test_preview_weight_without_font_has_no_step(xml_subs, tmp_path):
    """Тонкого начертания нет — превью не трогает шрифт вовсе (как .jsx)."""
    plan = _plan(xml_subs, "weight", highlights=[], style={"sub_anim_dur": 0.05})
    word = _word_with_room(plan, "weight", dur=0.05)
    t0 = word["anim"]["op"][0][0]
    checks = r"""
paint(plan,%(tm)s);
const sp=words().filter(s=>s.textContent===%(word)s)[0];
assert(sp,'не нашёлся спан слова');
assert.strictEqual(sp.dataset.wf,undefined,'превью завело ступеньку без тонкого шрифта');
assert.strictEqual(sp.style.fontFamily,undefined,
  'превью переставило шрифт без тонкого начертания: '+sp.style.fontFamily);
assert(parseFloat(sp.style.opacity)>0&&parseFloat(sp.style.opacity)<1,
  'лёгкого проявления нет: '+sp.style.opacity);
console.log('OK: no typeface step without the font');
""" % {"tm": json.dumps(round(t0 + 0.005, 6)),
       "word": json.dumps(word["w"], ensure_ascii=False)}
    _expect_ok(_run_preview(tmp_path, "wz_weight_plain.js", plan, checks),
               "превью weight без шрифта")


@node
def test_preview_yellow_keeps_the_size_multiplier(xml_subs, tmp_path):
    """Размер жёлтого в превью — кегль строки, умноженный на hl_size_k (число из плана)."""
    # Режим строк: кегль строки общий (pl.fsize), поэтому ожидание считается точно.
    style = {"hl_size_k": 2.0, "sub_words_per_row": 2}
    plan = _plan(xml_subs, "none", style=style)
    yellow = next(it for it in plan["subs"]
                  if any(w.get("color") == "yellow" for w in it.get("words") or []))
    want = "font-size:%.3fcqw" % (plan["fsize"] * 2.0 / plan["w"] * 100)
    checks = r"""
paint(plan,%(tm)s);
const yel=words().filter(s=>s.classList.contains('yel'));
assert(yel.length>0,'в разметке нет жёлтых слов');
assert.strictEqual(yel[0].styleStr.indexOf(%(want)s)>=0,true,
  'жёлтое слово нарисовано не своим кеглем: '+yel[0].styleStr);
const white=words().filter(s=>!s.classList.contains('yel'));
assert(white.length>0,'в разметке нет белых слов');
assert.strictEqual(white[0].styleStr.indexOf('font-size:')<0,true,
  'множитель уехал на белые слова: '+white[0].styleStr);
console.log('OK: preview multiplies the yellow size');
""" % {"tm": json.dumps(round(yellow["s"] + 0.01, 4)), "want": json.dumps(want)}
    _expect_ok(_run_preview(tmp_path, "wz_size.js", plan, checks), "превью hl_size_k")

    # Умолчание: множителя нет — и в разметке нет ни одного своего кегля у слова.
    off = _plan(xml_subs, "none", style={"sub_words_per_row": 2})
    checks_off = r"""
paint(plan,%(tm)s);
const all=words();
assert(all.length>0,'в разметке нет слов');
assert.strictEqual(all.every(s=>s.styleStr.indexOf('font-size:')<0),true,
  'при hl_size_k=1.0 у слова появился свой кегль: '+all[0].styleStr);
console.log('OK: default keeps the row size');
""" % {"tm": json.dumps(round(yellow["s"] + 0.01, 4))}
    _expect_ok(_run_preview(tmp_path, "wz_size_off.js", off, checks_off),
               "превью hl_size_k=1.0")


# --------------------------------------------------------------------------- #
# 5. Умолчания: эталон .jsx не меняется ни на байт
# --------------------------------------------------------------------------- #
def test_defaults_keep_the_golden(xml_subs, tmp_path):
    """Дефолты новых ручек — «как сейчас»: ни подстановок, ни объявлений, эталон тот же."""
    assert styles.BASE["sub_anim_font"] is None and styles.BASE["hl_size_k"] == 1.0
    off = _jsx(xml_subs, tmp_path, "defaults.jsx", preset="none", highlights=[])
    for token in ("subAnimKeys", "HL_SIZE_K", "HL_W_", "subWipeRect"):
        assert token not in off, "умолчание оставило в .jsx подстановку %s" % token
    golden = _mask_assets(open(os.path.join(HERE, "fixtures", "golden_geometry.jsx"),
                               encoding="utf-8-sig").read())
    assert _mask_assets(_build(xml_subs, tmp_path)) == golden, \
        "сборка с дефолтами разошлась с эталоном golden_geometry.jsx"
