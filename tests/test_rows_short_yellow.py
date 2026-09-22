# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание MN: короткое жёлтое в режиме строк доигрывает появление.

Внешнее ревью (2026-09-19): MA ужало появление короткого жёлтого в режиме «по слову» и в
стопке, а цикл СТРОК остался на общей HL_DUR = 0.35 с. Момент появления там зажат окном
``min(max(время слова, r_t0), max(r_t0, r_t1 - HL_DUR))``: если строка короче 0.35 с, момент
остаётся в её начале, и подъём с проявлением обрываются на конце строки.

1. Длительность появления жёлтого в строке — ``d = layout.hl_appear_dur(r_t1 - w_t0)``: та же
   функция, что у MA (второй копии формулы нет), видимое время — до конца строки. Python
   кладёт её полем 3 слова данных SUB_ROWS ([начало, текст, hl, hd]) и полем ``hd`` слова
   строки в плане (превью берёт готовое); длинному жёлтому остаётся общая HL_DUR.
2. ``hl_row_anim="row"`` — то же правило: появление со строки, длительность от её конца.
3. Короткая строка не подменяет длительность стопке подряд жёлтых: HL_HD — одна переменная
   на оба цикла, и цикл стопки возвращает её к общей (иначе блюр стопки берёт длительность,
   оставшуюся от последнего жёлтого строки).
4. Нет коротких строк — .jsx побайтово прежний: ни поля длительности, ни функции hlRowDur,
   ни подстановок; цикл строк совпадает с роликом, где коротких строк нет вовсе.
5. Превью (боевая ipvSubs, node с заглушками DOM): в середине короткой анимации прозрачность
   промежуточная, на ``t0+hd`` слово стоит полностью проявленным, а длинное в тот же момент
   ещё едет на общей hl_dur плана; у строки из ОДНОГО слова длительность лежит в поле слова
   (у элемента плана её нет) — превью берёт её там же, где момент появления.

Слова фикстуры (tests/fixtures/timeline_subs.xml.gz, 60 fps): 46 «РА» — второе слово
КОРОТКОЙ строки (1171..1189 кадров, 0.3 с: окно подъёма в неё не влезает, момент появления
зажат в начало строки); 1 «СУ» — второе слово строки 0..35 кадров (в режиме "word" момент
зажат к её концу — видимое время ровно 0.35 с, в режиме "row" видно всю строку); 30 «ПИ» —
строка ровно из одного слова (734..744 кадра, 0.1667 с); 11 «МЕТКОНСЕКТ» — жёлтое длинной
строки 194..290 кадров (0.95 с: длительность остаётся общей); 7, 8, 9 «Д», «ОДЛО»,
«ИПСУМДОЛО» — серия подряд жёлтых: с hl_row_stack уезжает в стопку, где укороченных нет.

Собранный цикл строк ИСПОЛНЯЕТСЯ в node с заглушками AE (приём tests/test_hl_anim), а
боевая ipvSubs — с заглушками DOM (приём tests/test_hl_short): проверяются реальные ключи,
inPoint/outPoint и прозрачность, а не текст .jsx.
"""
import gzip
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import xml2ae  # noqa: E402
from core.xml2ae import layout  # noqa: E402
from core.xml2ae.layout import HL_DUR, hl_appear_dur  # noqa: E402
from tests.test_hl_anim import _run as _run_jsx  # noqa: E402
from tests.test_hl_short import _blur_keys, _keys, _run_preview  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

CLAMP = 1        # «СУ»: строка 0.5833 с, слово на 0.4 — в "word" момент зажат к концу
SHORT_ROW = 46   # «РА»: строка 0.3 с — короче окна подъёма, момент зажат в её начало
LONG = 11        # «МЕТКОНСЕКТ»: строка 1.6 с — длительности хватает
SINGLE = 30      # «ПИ»: строка ровно из одного слова (0.1667 с) — короче окна подъёма
SERIES = [7, 8, 9]                      # серия подряд жёлтых — уезжает в стопку (hl_row_stack)
W_SERIES = ("Д", "ОДЛО", "ИПСУМДОЛО")   # слова серии: у стопки укороченных нет
HIGHLIGHTS = [CLAMP, SHORT_ROW, LONG]
W_CLAMP, W_SHORT, W_LONG = "СУ", "РА", "МЕТКОНСЕКТ"
# Режим строк без блюра — так .jsx и сверяется с «как на main»; блюр едет тем же путём
# (его ключи — те же, что у подъёма), поэтому в тестах 1–2 он включён.
ROWS = {"sub_words_per_row": 2}
BLUR = {"hl_blur": True, "hl_blur_amt": 70.4}


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml, style=None, highlights=None):
    return xml2ae.scene_plan(xml, highlights=list(highlights or []), style=dict(style or {}),
                             emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None, highlights=None):
    path, _, _ = xml2ae.to_ae_full(xml, str(tmp_path / name), highlights=list(highlights or []),
                                   style=dict(style or {}), disclaimer="",
                                   emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _json_var(jsx, name):
    m = re.search(r"var %s = (\[.*?\]);" % name, jsx)
    assert m, "в .jsx нет var %s" % name
    return json.loads(m.group(1))


def _rows_loop(jsx):
    """Тело цикла строк (строка данных SUB_ROWS в него не входит): по ней и видно, играет
    цикл общую длительность или подстановку hlRowDur."""
    a = jsx.index("    for (var ri=0; ri<SUB_ROWS.length; ri++){")
    return jsx[a:jsx.index("    // ---- поп-SFX", a)]


def _yellow_plan_words(plan):
    """(строка плана, слово плана) по всем жёлтым словам строк (элементы стопки — не строки)."""
    return [(it, wd) for it in plan["subs"] if not it.get("stack")
            for wd in it.get("words") or [] if wd["color"] == "yellow"]


def _exp_t0(it, wd, row_anim):
    """Момент появления жёлтого в строке — контракт ZH, тот же, что у цикла строк."""
    if row_anim == "word":
        return round(min(max(wd["s"], it["s"]), max(it["s"], it["e"] - HL_DUR)), 4)
    return round(it["s"], 4)


# ------------------------------------ 1-2. короткая строка: своя длительность появления
@node
@pytest.mark.parametrize("row_anim", ["word", "row"])
def test_short_row_yellow_plays_its_own_time(xml_subs, tmp_path, row_anim):
    """Короткая строка (0.3 с) и зажатый момент в длинной: жёлтое играет появление за
    hl_appear_dur(r_t1 - w_t0) — ключи подъёма, проявления и блюра кончаются до outPoint
    строки; длинному жёлтому остаётся общая HL_DUR. row_anim="row" — то же правило."""
    style = dict(ROWS, hl_row_anim=row_anim, **BLUR)
    plan = _plan(xml_subs, style=style, highlights=HIGHLIGHTS)
    assert plan["hl_dur"] == HL_DUR, "план потерял общую длительность появления"

    exp = {}                     # текст слова -> (t0, d) — ожидания считает тест, не .jsx
    shorts = longs = 0
    for it, wd in _yellow_plan_words(plan):
        t0 = _exp_t0(it, wd, row_anim)
        assert wd["t0"] == t0, "t0 жёлтого не по правилу ZH: %r" % (wd,)
        d = hl_appear_dur(it["e"] - t0)
        assert d <= HL_DUR
        if d < HL_DUR:
            assert wd["hd"] == d, "в плане не своя длительность появления: %r" % (wd,)
            shorts += 1
        else:
            assert "hd" not in wd, "длинному жёлтому проставили своё появление: %r" % (wd,)
            longs += 1
        exp[wd["w"]] = (t0, d)
    # В "word" укорочены оба: «СУ» (момент зажат к концу строки) и «РА» (строка короче
    # окна подъёма). В "row" «СУ» видно всю строку (0.5833 с) — укорочен только «РА».
    assert (shorts, longs) == ((2, 1) if row_anim == "word" else (1, 2)), \
        "фикстура перестала воспроизводить короткие строки: shorts=%d longs=%d" % (shorts, longs)
    assert exp[W_SHORT][1] < HL_DUR, "короткая строка не укоротила появление"
    if row_anim == "word":
        assert exp[W_CLAMP][1] < HL_DUR, "зажатый к концу строки момент не укоротил появление"
    else:
        assert exp[W_CLAMP][1] == HL_DUR, "со строки появление укоротили зря"

    jsx = _jsx(xml_subs, tmp_path, "mn_%s.jsx" % row_anim, style=style, highlights=HIGHLIGHTS)
    assert "function hlRowDur(wd){ return wd[3]; }" in jsx, "нет функции длительности строки"
    rows = _json_var(jsx, "SUB_ROWS")
    assert all(len(wd) == 4 for row in rows for wd in row[4]), \
        "словам строки не проставили поле длительности"
    got = {wd[1]: wd[3] for row in rows for wd in row[4] if wd[2]}
    for w, exp_d in exp.items():
        assert got[w] == pytest.approx(exp_d[1]), "в данных цикла не своя длительность у %r" % w

    # Цикл строк в node: ключи подъёма/проявления/блюра — за d, последний не позже конца строки.
    run = _run_jsx(jsx, tmp_path, "mn_run_%s" % row_anim)
    pairs = [(row, wd) for row in rows for wd in row[4]]
    assert [l["text"] for l in run["layers"]] == [wd[1] for _, wd in pairs]
    fps = float(xml2ae.parse_full(xml_subs)[0]["fps"])
    seen = 0
    for lay, (row, wd) in zip(run["layers"], pairs):
        if not wd[2]:
            assert lay["inPoint"] == pytest.approx(row[0] / fps), "белое слово уехало со строки"
            continue
        t0, out, d = lay["inPoint"], lay["outPoint"], wd[3]
        assert d == pytest.approx(exp[wd[1]][1]), "слою не своя длительность: %r" % wd[1]
        assert out == pytest.approx(row[1] / fps), "конец слоя не по концу строки"
        assert _keys(lay, "opacity") == pytest.approx([t0, t0 + d]), \
            "ключи проявления не за длительность слова"
        assert _keys(lay, "position") == pytest.approx([t0, t0 + d]), "ключи подъёма не за неё же"
        assert _blur_keys(lay) == pytest.approx([t0, t0 + d]), "блюр не за ту же длительность"
        assert _keys(lay, "opacity")[-1] <= out + 1e-9, "последний ключ остался за outPoint строки"
        seen += 1
    assert seen == len(exp), "проверены не все жёлтые ролика"


# ---------------- 3. короткая строка не подменяет длительность стопке (найденный баг)
@node
def test_stack_does_not_inherit_row_duration(xml_subs, tmp_path):
    """Стопка подряд жёлтых идёт своим циклом ПОСЛЕ строк и об укорочении не знает: её
    словам остаётся общая HL_DUR. HL_HD — одна переменная на оба цикла, и цикл стопки
    обязан вернуть её к общей: иначе блюр стопки берёт длительность, оставшуюся от
    последнего жёлтого строки (ловится только этой комбинацией — строки короткие, стопка
    нет: у самой стопки укороченных слов нет, и функции hlDur в ролике не появляется)."""
    style = dict(ROWS, hl_row_stack=True, **BLUR)
    hl = SERIES + [CLAMP, SHORT_ROW, LONG]
    plan = _plan(xml_subs, style=style, highlights=hl)
    shorts = {wd["w"]: wd["hd"] for _it, wd in _yellow_plan_words(plan) if wd.get("hd")}
    assert set(shorts) == {W_CLAMP, W_SHORT}, "короткие строки в фикстуре не те: %r" % shorts

    jsx = _jsx(xml_subs, tmp_path, "mn_stack.jsx", style=style, highlights=hl)
    assert "function hlRowDur(wd){ return wd[3]; }" in jsx, "нет функции длительности строки"
    assert "hlDur(" not in jsx, "стопке нашлось что укорачивать — фикстура не та"

    run = _run_jsx(jsx, tmp_path, "mn_stack")
    yellow = {l["text"]: l for l in run["layers"] if l["fx"].get("ADBE Gaussian Blur 2")}
    assert set(yellow) == set(W_SERIES) | {W_CLAMP, W_SHORT, W_LONG}, \
        "слои стопки/строки не нашлись: %r" % list(yellow)
    for w in tuple(W_SERIES) + (W_LONG,):
        lay = yellow[w]
        t0 = lay["inPoint"]
        assert _keys(lay, "opacity") == pytest.approx([t0, t0 + HL_DUR]), \
            "стопке/длинной строке уехала длительность короткой строки"
        assert _blur_keys(lay) == pytest.approx([t0, t0 + HL_DUR]), \
            "блюр взял длительность последнего жёлтого строки"
    for w in (W_CLAMP, W_SHORT):
        lay = yellow[w]
        t0, d = lay["inPoint"], shorts[w]
        assert d < HL_DUR, "короткая строка %r не укоротилась" % w
        assert _keys(lay, "opacity") == pytest.approx([t0, t0 + d])
        assert _blur_keys(lay) == pytest.approx([t0, t0 + d]), "строке не досталось её длительности"


# --------------------------------------- 4. без коротких строк — .jsx как на main
def test_no_short_row_jsx_is_main(xml_subs, tmp_path, monkeypatch):
    """Без коротких строк .jsx не несёт ни функции hlRowDur, ни поля длительности, а цикл
    строк — ровно прежний текст (ключи на общей HL_DUR). Укорочение, выключенное на том же
    ролике с короткой строкой, обязано дать цикл ролика без коротких строк байт в байт."""
    long_only = _jsx(xml_subs, tmp_path, "mn_long.jsx", style=ROWS, highlights=[LONG])
    for token in ("hlRowDur", "HL_HD"):
        assert token not in long_only, "в .jsx без коротких строк остался %s" % token
    assert all(len(wd) == 3 for row in _json_var(long_only, "SUB_ROWS") for wd in row[4]), \
        "у слова строки появилось поле длительности"
    loop = _rows_loop(long_only)
    assert "posP.setValueAtTime(t0+HL_DUR, [wCenter, lineY]);" in loop, \
        "подъём строки поехал с общей длительности"
    assert "op.setValueAtTime(t0, 0); op.setValueAtTime(t0+HL_DUR, 100);" in loop, \
        "проявление строки поехало с общей длительности"

    # Тот же ролик с короткой строкой: цикл и данные меняются — проверка не пустая.
    on = _jsx(xml_subs, tmp_path, "mn_on.jsx", style=ROWS, highlights=HIGHLIGHTS)
    assert "hlRowDur" in on and _rows_loop(on) != loop, "короткая строка не тронула цикл строк"
    assert all(len(wd) == 4 for row in _json_var(on, "SUB_ROWS") for wd in row[4])

    # А с выключенным укорочением (HL_FIT) подстановки обязаны стать прежними: цикл строк
    # совпадает с роликом, где коротких строк нет вовсе, — то есть .jsx как на main.
    monkeypatch.setattr(layout, "HL_FIT", 1e6)
    off = _jsx(xml_subs, tmp_path, "mn_off.jsx", style=ROWS, highlights=HIGHLIGHTS)
    assert "hlRowDur" not in off and "HL_HD" not in off
    assert all(len(wd) == 3 for row in _json_var(off, "SUB_ROWS") for wd in row[4])
    assert _rows_loop(off) == loop, "выключенное укорочение меняет цикл строк"
    assert not [wd for _it, wd in _yellow_plan_words(_plan(xml_subs, style=ROWS,
                                                          highlights=HIGHLIGHTS)) if "hd" in wd], \
        "укорочение выключено, а длительность в плане осталась"


# --------------------------------------------- 4. превью: длительность из плана (node)
@node
def test_preview_takes_row_duration_from_plan(xml_subs, tmp_path):
    """Превью анимирует короткое жёлтое строки за hd из плана: в середине прозрачность
    промежуточная, на t0+hd — слово на месте; длинное в тот же момент ещё едет на hl_dur."""
    plan = _plan(xml_subs, style=dict(ROWS, **BLUR), highlights=HIGHLIGHTS)
    shorts = [(it, wd) for it, wd in _yellow_plan_words(plan) if wd.get("hd")]
    longs = [(it, wd) for it, wd in _yellow_plan_words(plan) if not wd.get("hd")]
    assert shorts and longs, "в плане нет ни короткого, ни длинного жёлтого"

    checks = r"""
// Жёлтое короткой строки: своя длительность в плане и своя анимация в превью.
const shortIt=plan.subs.filter(s=>!s.stack&&(s.words||[]).some(w=>w.hd!=null))[0];
const sw=shortIt.words.filter(w=>w.hd!=null)[0];
const hd=sw.hd, t0=sw.t0, hDur=plan.hl_dur;
assert(hd>0&&hd<hDur,'в плане нет укороченной длительности: '+hd);
function at(t){return words().filter(w=>w.dataset.hl0!==undefined
  &&Math.abs(parseFloat(w.dataset.hl0)-t)<1e-9)[0]||null;}

// Середина короткой анимации — прозрачность промежуточная, длительность из плана.
paint(plan,t0+hd/2);
let el=at(t0);
assert(el,'слой жёлтого короткой строки не найден');
assert.strictEqual(el.dataset.hld,String(hd),'превью не взяло длительность из плана');
let op=el.style.opacity;
assert(op!==''&&parseFloat(op)>0&&parseFloat(op)<1,'середина короткой анимации: '+op);

// На t0+hd анимация доиграла: слово на месте, следов подъёма и блюра нет.
paint(plan,t0+hd);
el=at(t0);
assert.strictEqual(el.style.opacity,'','на t0+hd короткое жёлтое ещё не доиграло');
assert.strictEqual(el.style.top,'','на t0+hd остался подъём: '+el.style.top);
assert.strictEqual(el.style.filter,'','на t0+hd остался блюр: '+el.style.filter);

// Длинное жёлтое — на общей hl_dur плана: своего поля hd у него нет.
const longIt=plan.subs.filter(s=>!s.stack&&(s.words||[]).some(w=>w.color==='yellow'&&w.hd==null))[0];
const lw=longIt.words.filter(w=>w.color==='yellow'&&w.hd==null)[0];
paint(plan,lw.t0+hd);
el=at(lw.t0);
assert(el&&el.dataset.hld===undefined,'у длинного жёлтого своя длительность в превью');
op=el.style.opacity;
assert(op!==''&&parseFloat(op)>0&&parseFloat(op)<1,'длинное поехало на чужую длительность: '+op);
paint(plan,lw.t0+hDur);
assert.strictEqual(at(lw.t0).style.opacity,'','на t0+hl_dur длинное жёлтое не доиграло');
console.log("OK: preview takes row appear duration from the plan");
"""
    _run_preview(tmp_path, "mn_preview.js", plan, checks)

    # Строка из ОДНОГО слова: у элемента плана поля hd нет, оно лежит в поле его слова —
    # превью обязано взять его там же, где момент (иначе играло бы общую hl_dur, а AE ужатую).
    one = _plan(xml_subs, style=dict(ROWS, **BLUR), highlights=[SINGLE])
    rows_one = [it for it in one["subs"] if not it.get("stack")
                and any(w["color"] == "yellow" for w in it.get("words") or [])]
    assert len(rows_one) == 1 and len(rows_one[0]["words"]) == 1, \
        "фикстура: для этого случая нужна строка ровно из одного слова"
    sw = rows_one[0]["words"][0]
    assert sw.get("hd") and sw["hd"] < one["hl_dur"], "однословная строка не укоротилась"

    checks_one = r"""
// Строка из ОДНОГО слова: hd — поле слова, а не элемента плана.
const it=plan.subs.filter(s=>!s.stack&&(s.words||[]).length===1&&s.words[0].hd!=null)[0];
assert(it,'в плане нет однословной строки с укорочением');
const w=it.words[0], hd=w.hd, t0=w.t0;
function at(t){return words().filter(s=>s.dataset.hl0!==undefined
  &&Math.abs(parseFloat(s.dataset.hl0)-t)<1e-9)[0]||null;}
paint(plan,t0+hd/2);
let el=at(t0);
assert(el,'слой однословной строки не найден');
assert.strictEqual(el.dataset.hld,String(hd),'превью не взяло hd слова строки: '+el.dataset.hld);
const op=el.style.opacity;
assert(op!==''&&parseFloat(op)>0&&parseFloat(op)<1,'середина короткой анимации: '+op);
paint(plan,t0+hd);
assert.strictEqual(at(t0).style.opacity,'','на t0+hd однословная строка ещё не доиграла');
console.log("OK: preview takes single-word row duration from the word field");
"""
    _run_preview(tmp_path, "mn_preview_one.js", one, checks_one)
