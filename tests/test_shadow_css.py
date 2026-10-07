# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тень AE (Drop Shadow) в превью: числа из плана, перевод — одна функция.

Тень в превью и тень в собранном .jsx — одна и та же тень. Числа её считает Python
(`plan_decor.shadows_plan`, оттуда же они уезжают подстановками в шаблон) и отдаёт
планом, а фронт переводит их в CSS ОДНОЙ дверью `aeShadowCss` — ею рисуют все места
превью, где есть Drop Shadow: прекомп интро, субтитры, фото-вставки и плашка под
субтитрами. Своих чисел тени у превью не осталось: пока они были, тень кадра
расходилась с собранной в AE.

Проверяется:
1. `aeShadowCss` переводит тень по замеру рендера AE 2026 (06.10.2026): Softness 287 →
   радиус 107.3·k, Opacity 68 из 255 → альфа 0.267, Direction 181 при Distance 5 →
   смещение −5·k по X и −0.09·k по Y, и всё это растёт вместе с кадром (множитель k —
   пиксели превью на пиксель кадра);
2. мутация «вернуть прежнюю половину мягкости» (0.374 → 0.5) красит стенд — проверка
   стережёт замер, а не пустоту;
3. план несёт `shadows.sub/ins/sub_bg` — числа ровно те, что уехали подстановками в
   .jsx (проценты ручки там умножаются на 255/100, план отдаёт готовую шкалу 0..255);
4. в `85-inserts-view.js` не осталось ни одного литерала `drop-shadow(` с числами
   (кроме формирования строки внутри самой `aeShadowCss`) и ни одной второй копии
   чисел тени (`SH_TSH`/`SH_FX`);
5. все места тени зовут эту одну функцию, а стенд рендера без AE
   (`tests/test_webrender.py`) знает её имя — иначе он вырезал бы `ipvSubs` без неё.
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

from core import xml2ae  # noqa: E402

JS85 = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
WEBRENDER = os.path.join(HERE, "test_webrender.py")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

R_K = 0.374             # r = 0.374·Softness — замер, см. комментарий у aeShadowCss
HALF_SOFT = 0.5         # прежнее приближение превью: мягкость пополам


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_webrender.py)."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
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


def _plan(xml, style=None):
    return xml2ae.scene_plan(xml, style=dict(style or {}), emit=lambda *a, **k: None)


def _jsx(xml, tmp_path, name, style=None):
    path, _n, _s = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                     style=dict(style or {}), inserts=[], disclaimer="",
                                     intro=None, roto=False, intro_riser=False,
                                     emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _run_node(tmp_path, name, plan, checks, src=None):
    """node-стенд: боевое тело aeShadowCss + тени плана + проверки."""
    js85 = src if src is not None else _read(JS85)
    script = ("const assert=require('assert');\n" + _func(js85, "aeShadowCss") + "\n"
              + "const PLAN=%s;\n" % json.dumps(plan["shadows"], ensure_ascii=False)
              + checks)
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    return subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


# Числа читаются ИЗ строки тени: проверяется то, что реально уедет в filter, а не
# отдельные переменные функции.
_CHECKS = r"""
function nums(s){
  const m=/^drop-shadow\((-?[\d.]+)px (-?[\d.]+)px ([\d.]+)px rgba\((\d+),(\d+),(\d+),([\d.]+)\)\)$/.exec(s);
  assert(m,'тень не разобралась как drop-shadow(dx dy r rgba): '+s);
  return {dx:+m[1],dy:+m[2],r:+m[3],rgb:[+m[4],+m[5],+m[6]],a:+m[7]};
}
function near(got,want,tol,what){
  assert(Math.abs(got-want)<=tol,what+': '+got+' вместо '+want);
}
// Замер: Softness 287 -> радиус 107.3 px кадра (ноль смещения при Distance 0).
let t=nums(aeShadowCss({op255:255,dir:0,dist:0,soft:287,color:[0,0,0]},1));
near(t.r,107.3,0.05,'радиус при Softness 287');
near(t.dx,0,0.05,'смещение X при Distance 0');
near(t.a,1,0.001,'непрозрачность 255');
// Непрозрачность: 68 из 255 -> альфа 0.267 (шкала AE переходит в альфу CSS как есть).
t=nums(aeShadowCss({op255:68,dir:0,dist:0,soft:0,color:[0,0,0]},1));
assert.strictEqual(t.a,0.267,'альфа из Opacity 68: '+t.a);
// Смещение: Direction 181, Distance 5 -> (-5, -0.09) px кадра (Y в кадре вниз).
t=nums(aeShadowCss({op255:255,dir:181,dist:5,soft:0,color:[0.5,0.25,1]},1));
near(t.dx,-5,0.06,'смещение X при Direction 181');
near(t.dy,-0.0873,0.06,'смещение Y при Direction 181');
assert.deepStrictEqual(t.rgb,[128,64,255],'цвет тени не из плана: '+t.rgb);
// Множитель кадра: и мягкость, и смещение заданы в пикселях кадра.
t=nums(aeShadowCss(PLAN.sub,2));
near(t.r,0.374*PLAN.sub.soft*2,0.05,'радиус с множителем кадра');
near(t.dx,PLAN.sub.dist*Math.cos(PLAN.sub.dir*Math.PI/180)*2,0.06,'смещение X с множителем кадра');
near(t.dy,PLAN.sub.dist*Math.sin(PLAN.sub.dir*Math.PI/180)*2,0.06,'смещение Y с множителем кадра');
assert.strictEqual(t.a,+(PLAN.sub.op255/255).toFixed(3),'альфа тени субтитров из плана');
// Тени нет (старый бэкенд без перезапуска) — пустая строка, а не мусорная функция.
assert.strictEqual(aeShadowCss(null,1),'','тень без чисел плана не пустая');
console.log('OK: aeShadowCss переводит тень AE по замеру');
"""


@node
def test_ae_shadow_css_translates_by_the_measurement(xml_subs, tmp_path):
    """Замер: r = 0.374·Softness, альфа = Opacity/255, смещение Dist·(cos,sin) Dir."""
    res = _run_node(tmp_path, "shadow_css.js", _plan(xml_subs), _CHECKS)
    assert res.returncode == 0, "node упал: %s" % ((res.stderr or "") + (res.stdout or ""))[-2000:]
    assert "OK" in res.stdout, res.stdout


@node
def test_ae_shadow_css_mutation_half_softness_goes_red(xml_subs, tmp_path):
    """Мутация: прежняя половина мягкости (0.5) обязана покрасить тот же стенд.

    Иначе проверка стерегла бы пустоту: 0.374 и 0.5 — ровно то расхождение, из-за
    которого тень превью была в 1.34 раза шире собранной в AE.
    """
    js85 = _read(JS85)
    old = "const R_K=%g;" % R_K
    assert old in js85, "в aeShadowCss не нашлась константа замера %r" % old
    bad = _run_node(tmp_path, "shadow_css_mut.js", _plan(xml_subs), _CHECKS,
                    src=js85.replace(old, "const R_K=%g;" % HALF_SOFT, 1))
    assert bad.returncode != 0, ("мутация 0.5 прошла незамеченной: %s"
                                 % ((bad.stdout or "")[-500:]))
    err = (bad.stderr or "") + (bad.stdout or "")
    assert "радиус при Softness 287" in err, "мутация покраснела не на том: %s" % err[-500:]


def test_plan_shadows_are_the_jsx_substitutions(xml_subs, tmp_path):
    """Числа плана — ровно подстановки .jsx: вторая дверь разошлась бы молча."""
    plan = _plan(xml_subs)
    # Тень субтитров и вставок стоит в .jsx подстановками, а её вызов — только когда
    # тень текста включена: галка «плашка» снимает её (sub_shadow=false). Плашка —
    # наоборот, только со своей галкой: для её чисел собирается вторая сборка.
    jsx = _jsx(xml_subs, tmp_path, "plain.jsx")
    jsx_bg = _jsx(xml_subs, tmp_path, "sub_bg.jsx", {"sub_bg": True})
    shadows = plan["shadows"]

    m = re.search(r"var SH_OPACITY = ([\d.]+), SH_DIR = ([\d.]+), SH_DIST = ([\d.]+), "
                  r"SH_SOFT = ([\d.]+)", jsx)
    assert m, "в .jsx нет подстановки тени субтитров SH_*"
    op, dr, di, so = (float(v) for v in m.groups())
    assert shadows["sub"]["soft"] == so and shadows["sub"]["dir"] == dr
    assert shadows["sub"]["dist"] == di
    assert abs(shadows["sub"]["op255"] - op * 255 / 100) <= 0.1, \
        "op255 плана не равен SH_OPACITY/100*255 из .jsx"
    # В .jsx проценты ручки умножаются на 255/100 — ровно это число отдаёт план.
    assert 'setValue(SH_OPACITY/100*255)' in jsx

    m = re.search(r"var INS_SH_OP = ([\d.]+), INS_SH_DIR = ([\d.]+), INS_SH_DIST = ([\d.]+), "
                  r"INS_SH_SOFT = ([\d.]+)", jsx)
    assert m, "в .jsx нет подстановки тени вставок INS_SH_*"
    op, dr, di, so = (float(v) for v in m.groups())
    assert shadows["ins"]["soft"] == so and shadows["ins"]["dir"] == dr
    assert shadows["ins"]["dist"] == di
    assert abs(shadows["ins"]["op255"] - op * 255 / 100) <= 0.1, \
        "op255 плана не равен INS_SH_OP/100*255 из .jsx"
    # Та же арифметика у вставок (в шаблоне она стоит в setP, а не в setValue).
    assert 'INS_SH_OP/100*255' in jsx

    # Плашка: числа стоят в .jsx литералами (SUB_BG_SH_* печатает plan_decor).
    bg = dict(zip(("op255", "dir", "dist", "soft"),
                  (float(v) for v in re.findall(
                      r'bgDs\.property\("ADBE Drop Shadow-000[2345]"\)\.setValue\(([\d.]+)\)',
                      jsx_bg))))
    assert len(bg) == 4, "в .jsx нет подстановок тени плашки (%d из 4)" % len(bg)
    assert shadows["sub_bg"] == dict(bg, color=shadows["sub_bg"]["color"])

    # Цвет: все три тени чёрные, как в .jsx ([0,0,0] у вставок и плашки, дефолт AE у
    # субтитров — тень слоя прекомпа цвет не задаёт вовсе).
    for who in ("sub", "ins", "sub_bg"):
        assert shadows[who]["color"] == [0.0, 0.0, 0.0], "цвет тени %s не чёрный" % who


def test_no_shadow_numbers_left_in_the_preview():
    """Своих чисел тени у превью нет: ни литералов drop-shadow(...), ни второй копии.

    Литерал с числами сразу после `drop-shadow(` — это нарисованная «своя» тень:
    именно так превью и расходилось с .jsx (0 6px 18px у вставок, 0 2px 7px у слов).
    Формирование строки внутри самой aeShadowCss — не литерал: там склейка из чисел
    плана, и она одна на весь файл.
    """
    js = _read(JS85)
    rest = js.replace(_func(js, "aeShadowCss"), "")
    bad = [ln.strip() for ln in rest.splitlines()
           if re.search(r"drop-shadow\(\s*[-+0-9.]", ln)]
    assert not bad, "в превью осталась своя тень числами: %s" % bad
    assert "SH_TSH" not in js and "SH_FX" not in js, \
        "в превью осталась вторая копия чисел тени субтитров"
    assert "const R_K=%g;" % R_K in js, \
        "множитель замера не назван константой в aeShadowCss"
    assert "0.187" in js, "у константы замера нет пояснения про сигму из замера"


def test_preview_draws_every_shadow_by_the_one_door():
    """Тень AE рисуют все четыре места — и все через aeShadowCss, чисел у них нет."""
    js = _read(JS85)
    for fn in ("ipvSubs", "ipvInsPlace", "ipvIntro"):
        assert "aeShadowCss(" in _func(js, fn), \
            "тень AE в %s рисуется мимо aeShadowCss" % fn
    subs = _func(js, "ipvSubs")
    # Субтитры — фильтр на контейнере слов (Drop Shadow слоя прекомпа), плашка — свой.
    assert "pl.shadows&&pl.shadows.sub" in subs and "pl.shadows&&pl.shadows.sub_bg" in subs
    assert "host.style.filter=" in subs and "bgEl.style.filter=" in subs, \
        "тень субтитров и плашки ставится не на свой элемент"
    assert "pl.shadows&&pl.shadows.ins" in _func(js, "ipvInsPlace")
    assert "aeShadowCss({op255:sh.op" in _func(js, "ipvIntro"), \
        "тень прекомпа интро рисует не общая дверь"


def test_render_stand_knows_the_function():
    """Стенд рендера без AE вырезает боевые функции по списку — в нём есть aeShadowCss.

    Без этого его `ipvSubs`/`ipvInsPlace`/`ipvIntro` остались бы без функции перевода,
    и тень в снятых кадрах молча пропала бы (проверки стенда — не про тень).
    """
    src = _read(WEBRENDER)
    m = re.search(r"FUNCS85 = \((.*?)\n\)", src, re.S)
    assert m, "в tests/test_webrender.py не нашёлся список функций FUNCS85"
    assert '"aeShadowCss"' in m.group(1), "стенд рендера не знает aeShadowCss"
