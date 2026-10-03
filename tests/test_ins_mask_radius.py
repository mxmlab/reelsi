# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Скругление маски фото-вставки: одно число из Python — и в .jsx, и в превью.

В .jsx маска-«Скругление» (``roundMask``) лежит на слое прекомпа фотографии, и радиус
у неё — константа шаблона ``INS_MASK_R``. Превью рисовало окно маски (``.insmask``)
БЕЗ скругления: у собранного проекта углы круглые, у превью и у рендера без AE —
прямые, и на кадре это видно сразу.

Правило «у кого маски нет» — то же, что в шаблоне: вид вставок ``white`` и ``none``
маску не вешают вовсе, вставке «на подложке» — тоже (у неё своя плашка).

Стерегутся три вещи:
1. число радиуса в Python одно (``layout.INS_MASK_R``) и подставляется в шаблон —
   копии 60 в JS нет ни у .jsx, ни у превью;
2. план отдаёт радиус КАЖДОЙ вставке, которой маска положена (поле ``mask_r``),
   и не отдаёт тем, кому не положена;
3. превью (боевой ``ipvInsPlace``, node) ставит ``border-radius`` окну маски с учётом
   масштаба карточки — и не ставит его там, где радиуса нет в плане.

Запуск: py -3.10 -m pytest tests/test_ins_mask_radius.py -q
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

from PIL import Image  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import INS_MASK_R  # noqa: E402
from core.xml2ae import template as _template  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")
JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
PHOTO_T0, INS_DUR = 12.92, 2.5
STYLE = {"insert_anim": "zoom", "insert_fx": "card", "insert_style": "cam2"}


@pytest.fixture()
def xml_subs(tmp_path):
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


@pytest.fixture(autouse=True)
def _no_rembg(monkeypatch):
    """rembg не зовём: у вставки «на подложке» путь фото проходит через insertlib.nobg_path,
    а поднятие onnx-модели к маске-скруглению отношения не имеет (тот же приём, что в
    tests/test_ins_plate.py)."""
    from core import insertlib
    monkeypatch.setattr(insertlib, "remove_bg", lambda data, trim=True, emit=None: data)


@pytest.fixture()
def photo(tmp_path):
    """Фото 2000x500 — видимая часть упирается в ширину кадра, маска не квадратится."""
    p = str(tmp_path / "photo.png")
    Image.new("RGB", (2000, 500), (200, 60, 60)).save(p)
    return p


@pytest.fixture()
def plate(tmp_path):
    """Файл подложки: вставка с галкой идёт своим путём — плашка и БЕЗ маски."""
    p = str(tmp_path / "plate.png")
    Image.new("RGBA", (600, 600), (0, 0, 0, 0)).save(p)
    return p


def _inserts(photo, plate_file=None, plate=False):
    ins = {"type": "photo", "style": "cam2", "media": photo,
           "start_s": PHOTO_T0, "dur_s": INS_DUR}
    if plate:
        ins["plate"] = True
    return [ins]


def _plan(xml, photo, style=None, plate_file=None, plate=False):
    st = dict(STYLE)
    if plate_file:
        st["insert_plate_file"] = plate_file
    st.update(style or {})
    return xml2ae.scene_plan(xml, inserts=_inserts(photo, plate=plate), style=st,
                             disclaimer="", intro_riser=False, include_xml_inserts=False,
                             emit=lambda *a, **k: None)


def _photo_item(plan):
    for x in plan["inserts"]:
        if (x.get("t") or x.get("type")) == "photo":
            return x
    raise AssertionError(f"в плане нет фотовставки: {plan['inserts']}")


def _jsx(xml, photo, tmp_path, style=None, plate_file=None, plate=False, name="out.jsx"):
    st = dict(STYLE)
    if plate_file:
        st["insert_plate_file"] = plate_file
    st.update(style or {})
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / name), inserts=_inserts(photo, plate=plate),
        style=st, disclaimer="", intro_riser=False, include_xml_inserts=False,
        emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


# --------------------------------------------------------------------------- #
# 1. Число одно: Python -> шаблон .jsx и план
# --------------------------------------------------------------------------- #
def test_radius_is_one_number_for_jsx_and_plan(xml_subs, photo, tmp_path):
    """Радиус объявлен в Python один раз: его же печатает .jsx и отдаёт план."""
    jsx = _jsx(xml_subs, photo, tmp_path)
    assert "var INS_MASK_R = %g;" % INS_MASK_R in jsx, \
        "радиус маски в .jsx не тот, что в layout.INS_MASK_R"
    assert "roundMask(L, (W-mw)/2" in jsx and "INS_MASK_R); }" in jsx, \
        "маска-скругление пропала из .jsx"
    # Плейсхолдер шаблона — не литерал: правка числа в Python обязана доехать до .jsx
    assert "%(ins_mask_r)s" in _template.AE_FULL, \
        "радиус в шаблоне снова литералом — правка Python до .jsx не доедет"
    ins = _photo_item(_plan(xml_subs, photo))
    assert ins.get("mask_r") == INS_MASK_R, f"план не отдал радиус маски: {ins.get('mask_r')}"


def test_plan_radius_only_where_the_mask_is(xml_subs, photo, plate, tmp_path):
    """Маски нет — нет и радиуса: white/none у вида, подложка — у вставки.

    Правило ровно шаблонное: ``if (INS_FX!="white")`` (в шаблоне пустая подстановка при
    ``none``) и ``if (!(ins.plate && INS_PLATE))``.
    """
    assert _photo_item(_plan(xml_subs, photo))["mask_r"] == INS_MASK_R
    for fx in ("white", "none"):
        item = _photo_item(_plan(xml_subs, photo, style={"insert_fx": fx}))
        assert "mask_r" not in item, f"при insert_fx={fx} радиус не положен: {item.get('mask_r')}"
        jsx = _jsx(xml_subs, photo, tmp_path, style={"insert_fx": fx}, name="fx_%s.jsx" % fx)
        if fx == "none":
            assert "roundMask(L, (W-mw)/2" not in jsx, "в .jsx при insert_fx=none появилась маска"
        else:
            # white: вызов в шаблоне есть, но под рантайм-проверкой вида — как в AE
            assert 'if (INS_FX!="white"){' in jsx and "roundMask(L, (W-mw)/2" in jsx, \
                "в .jsx пропала проверка вида white вокруг маски"
    # Подложка: маски нет ни в .jsx, ни в плане
    item = _photo_item(_plan(xml_subs, photo, plate_file=plate, plate=True))
    assert item.get("plate") is True and "mask_r" not in item, \
        f"вставке на подложке выдали радиус маски: {item}"
    jsx = _jsx(xml_subs, photo, tmp_path, plate_file=plate, plate=True, name="plate.jsx")
    assert "if (!(ins.plate && INS_PLATE)) {" in jsx, "в .jsx нет ветки «не на подложке»"
    assert jsx.index("if (!(ins.plate && INS_PLATE)) {") < jsx.index("roundMask(L, (W-mw)/2"), \
        "маска в .jsx вешается до проверки подложки — у плашки будут скруглённые углы"


# --------------------------------------------------------------------------- #
# 2. Превью: те же числа, что у слоя в AE
# --------------------------------------------------------------------------- #
def _func(src, name):
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
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


def _js(*names):
    src = open(JS, encoding="utf-8").read()
    return "\n".join(_func(src, n) for n in names)


_DOM = r"""
const ipl=null;
function El(tag){
  this.tagName=String(tag).toUpperCase();this.style={};this.children=[];this.dataset={};
  this._attrs={};this._ev={};
}
El.prototype.appendChild=function(c){this.children.push(c);return c;};
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.setAttribute=function(n,v){this._attrs[n]=String(v);};
El.prototype.querySelector=function(sel){
  if(sel==='img'){for(const c of this.children)if(c.tagName==='IMG')return c;return null;}
  return null;};
El.prototype.querySelectorAll=function(){return [];};
const IMG=new El('img');
const WRAP=new El('div');WRAP.appendChild(IMG);
Object.defineProperty(WRAP,'firstChild',{get(){return this.children[0]||null;}});
Object.defineProperty(WRAP,'clientWidth',{get(){return 1080;}});
const IPV={fps:60,plan:null,insShift:null,cur:-1,vids:[]};
function ipvZoomAt(){return 1;}
"""


def _run_node(code, tmp_path, name):
    f = tmp_path / name
    f.write_text(code, encoding="utf-8")
    res = subprocess.run(["node", str(f)], capture_output=True, text=True,
                         encoding="utf-8-sig", errors="replace", timeout=60)
    assert res.returncode == 0, f"node упал: {(res.stderr or '')[-2000:]}"
    return res.stdout


@node
def test_preview_sets_the_mask_radius_with_the_card_scale(xml_subs, photo, tmp_path):
    """``.insmask`` получает border-radius из плана, умноженный на масштаб карточки.

    В AE радиус живёт в прекомпе и растёт вместе со Scale слоя: круглое фото остаётся
    круглым и на наезде. Проверяем на боевом ``ipvInsPlace``: радиус = mask_r × S/100 ×
    m × z × k, где k — пиксели стойки на пиксель кадра.
    """
    ins = json.loads(json.dumps(_photo_item(_plan(xml_subs, photo))))
    code = (_DOM + _js("aeEase", "bezierT", "bezierY", "keysAt", "ipvCamChild", "ipvInsPlace")
            + "\nconst item=%s;\n" % json.dumps(ins)
            + """
    IPV.plan={w:1080,h:1920,ins_c2x:540,ins_c2y:330,
              zoom:{cx:0.5,cy:0.5,rot:0,pan:[0,0],keys:[[0,100]],ease:[[33.3333,33.3333]]}};
    function shot(it){ipvInsPlace(WRAP,it,it.start+0.5);const el=WRAP.firstChild;
      return {r:el.style.borderRadius||'', w:el.style.width||'', mr:(it.mask_r==null?null:it.mask_r)};}
    const withMask=shot(item);
    const noMask=shot(Object.assign({},item,{mask_r:undefined}));
    console.log(JSON.stringify({withMask:withMask,noMask:noMask}));
    """)
    out = json.loads(_run_node(code, tmp_path, "ins_radius.js"))
    S = float(ins["scale"]) * float(ins["sc"]) / 100.0
    k = 1.0                                   # стойка 1080 px = кадр 1080 px
    want = INS_MASK_R * (S / 100.0) * 1 * 1 * k
    assert out["withMask"]["mr"] == INS_MASK_R, out
    assert out["withMask"]["r"] == "%.2fpx" % want, \
        f"радиус окна маски не по маске слоя: {out['withMask']['r']} вместо {want:.2f}px"
    # радиус задан в px ПРЕКОМПА и меньше половины окна — углы не срезают карточку целиком
    assert 0 < want < float(out["withMask"]["w"][:-2]) / 2, out
    # поля нет (вид white/none, подложка, старый сервер) — прямые углы, как было
    assert out["noMask"]["r"] == "", f"радиус появился без поля плана: {out['noMask']}"


@node
def test_preview_has_no_radius_of_its_own(xml_subs, photo, tmp_path):
    """Своей копии 60 у превью нет: поле плана — единственный источник радиуса."""
    src = open(JS, encoding="utf-8").read()
    m = re.search(r"function ipvInsPlace[\s\S]*?\n\}", src)
    assert m, "не нашлась ipvInsPlace"
    body = m.group(0)
    assert "x.mask_r" in body, "превью не читает радиус из плана"
    assert not re.search(r"borderRadius\s*=\s*['\"0-9]", body), \
        "радиус в превью задан числом — копия 60 вернулась"
