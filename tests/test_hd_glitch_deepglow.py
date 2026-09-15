# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты задания HD: свечение жёлтого глитча (встроенные Blur + Glo2 или Deep Glow 2)."""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import aicut  # noqa: E402
from core import xml2ae  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

@pytest.fixture()
def client():
    from webui import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture()
def html():
    from core import app_meta
    return open(app_meta.paths.root("templates", "index.html"), encoding="utf-8").read()


node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, mode="word", name="out.jsx", **kw):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode=mode, disclaimer="", emit=lambda *a: None, **kw)
    return open(path, encoding="utf-8-sig").read(), path


def _glow_intro():
    return [
        dict(words=["ЖЁЛТЫЙ"], color="yellow", times=[T_CAM1], anim="glitch"),
        dict(words=["ЖЁЛТОЕ"], color="yellow", times=[T_CAM1 + 0.3], fx="glow"),
        dict(words=["БЕЛОЕ"], color="white", times=[T_CAM1 + 0.6], fx="glow"),
        dict(words=["ФОН"], color="white", times=[T_CAM1 + 0.9], back=True, fx="glow"),
        dict(words=["АКЦЕНТ"], color="accent", times=[T_CAM2], anim="glitch"),
    ]


def _accent_only_glitch_intro():
    return [
        dict(words=["АКЦЕНТ"], color="accent", times=[T_CAM2], anim="glitch"),
        dict(words=["БЕЛОЕ"], color="white", times=[T_CAM1 + 0.6], fx="glow"),
    ]


def _balanced(jsx, open_brace):
    depth = 0
    for i in range(open_brace, len(jsx)):
        if jsx[i] == "{":
            depth += 1
        elif jsx[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("не нашёл закрывающую скобку")


def _anim_fx_src(jsx):
    i = jsx.index("function introAnimFX")
    # захватываем var DG_MISS=0; если оно объявлено непосредственно перед introAnimFX
    prefix = ""
    idx_dg = jsx.rfind("var DG_MISS=0;", 0, i)
    if idx_dg != -1 and jsx[idx_dg:i].strip() == "var DG_MISS=0;":
        prefix = "var DG_MISS=0;\n"
    return prefix + jsx[i:_balanced(jsx, jsx.index("{", i))]


def _comp_glow_src(jsx):
    i = jsx.index("var grpGlitch")
    i_end = _balanced(jsx, jsx.index("{", jsx.index("for(var gck=0", i)))
    flags_src = jsx[i:i_end]
    j = jsx.index("if(grpGlitch || ", i_end)
    glow_src = jsx[j:_balanced(jsx, jsx.index("{", j))]
    return flags_src + "\n" + glow_src


def _hl_fill_literal(jsx):
    m = re.search(r"var HL_FILL = (\[[^\]]*\])(?:,|;)", jsx)
    assert m, "в .jsx нет объявления HL_FILL"
    return m.group(1)


_JS_PROBE = r"""
'use strict';
var calls = [];
var DG_MISS = 0;
var NULL_PEDG2 = __NULL_PEDG2__;
function eff(){ return { property: function(p){ return { setValue: function(v){ calls.push('set:'+p+'='+JSON.stringify(v)); } }; } }; }
function addFX(L, mn){
  if(NULL_PEDG2 && mn === 'PEDG2') return null;
  calls.push('add:'+mn);
  return eff();
}
function setP(fx, mn, v){ if(fx){ calls.push('set:'+mn+'='+JSON.stringify(v)); } }
function lay(){ var o={}; o.property=function(){ return lay(); }; o.addProperty=function(){ return lay(); };
  o.setValue=function(){}; o.setValueAtTime=function(){}; o.value=[0,0]; o.expression=''; return o; }
function easePair(){}
function introW(){ return 100; }
var HL_DUR=0.35, F_DUR=0.25, HL_RISE=60, BACK_SCALE=1, BACK_STEP=0.45, INTRO_GLOW=1;
var HL_FILL=__HLFILL__;
var GRP = [];

__ANIMFX__

function runWord(anim, fx, col){ calls=[]; introAnimFX(lay(), 0, anim, fx, null, null, null, false, col); return calls; }

function compLay(tag){ var o={}; o.property=function(n){ return compLay(tag+'.'+n); };
  o.setValue=function(v){ calls.push('val:'+tag+'='+JSON.stringify(v)); }; return o; }
function parade(){ return { addProperty: function(mn){ calls.push('add:'+mn); return compLay('igl'); } }; }
function compLayer(){ return { property: function(n){ return (n==='ADBE Effect Parade') ? parade() : compLay('iL'); } }; }
var iL = compLayer();
function runComp(grp){ calls=[]; GRP=grp; __COMPGLOW__ ; return calls; }

var out = {
  yellow_glitch:  runWord('glitch', '', 'yellow'),
  yellow_glow:    runWord('', 'glow', 'yellow'),
  white_glow:     runWord('', 'glow', 'white'),
  accent_glitch:  runWord('glitch', '', 'accent'),
  comp_glitch:    runComp([{anim:'glitch'}]),
  comp_glow_only: runComp([{fx:'glow'}]),
  comp_both:      runComp([{anim:'glitch'}, {fx:'glow'}]),
  comp_plain:     runComp([{}]),
  dg_miss:        (typeof DG_MISS !== 'undefined') ? DG_MISS : -1
};
console.log(JSON.stringify(out));
"""


def _probe(jsx, tmp_path, null_pedg2=False):
    script = (_JS_PROBE
              .replace("__NULL_PEDG2__", "true" if null_pedg2 else "false")
              .replace("__HLFILL__", _hl_fill_literal(jsx))
              .replace("__ANIMFX__", _anim_fx_src(jsx))
              .replace("__COMPGLOW__", _comp_glow_src(jsx)))
    node_file = tmp_path / "probe_deepglow.js"
    node_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(node_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node упал: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


@node
def test_1_deepglow2_yellow_glitch_and_accent_glitch(xml_subs, tmp_path):
    """1. glitch_glow='deepglow2': yellow_glitch == PEDG2 + 30 props + Tritone; accent == Blur+Glo2."""
    from core.xml2ae.build import DEEP_GLOW2_GLITCH
    jsx, _ = _build(xml_subs, tmp_path, _glow_intro(), glitch_glow="deepglow2", name="dg_test1.jsx")
    hl = _hl_fill_literal(jsx)
    got = _probe(jsx, tmp_path)

    expected_dg_props = [f"set:{mn}={json.dumps(val, separators=(',', ':'))}" for mn, val in DEEP_GLOW2_GLITCH]
    expected_yellow = ["add:PEDG2"] + expected_dg_props + ["add:ADBE Tritone", "set:ADBE Tritone-0002=" + hl]
    assert got["yellow_glitch"] == expected_yellow
    assert got["accent_glitch"] == [
        "add:ADBE Gaussian Blur 2", "set:ADBE Gaussian Blur 2-0001=3.4",
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
    ]
    assert got["yellow_glow"] == [
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
        "add:ADBE Tritone", "set:ADBE Tritone-0002=" + hl,
    ]
    assert got["comp_glitch"] == [
        "add:ADBE Glo2", "set:ADBE Glo2-0002=211", "set:ADBE Glo2-0003=93",
        "set:ADBE Glo2-0004=0.42",
    ]


@node
def test_2_deepglow2_addfx_null_increments_dg_miss(xml_subs, tmp_path):
    """2. addFX возвращает null для PEDG2: DG_MISS == 1, Tritone всё равно добавлен."""
    jsx, _ = _build(xml_subs, tmp_path, _glow_intro(), glitch_glow="deepglow2", name="dg_test2.jsx")
    hl = _hl_fill_literal(jsx)
    got = _probe(jsx, tmp_path, null_pedg2=True)

    assert got["dg_miss"] == 1
    assert got["yellow_glitch"] == ["add:ADBE Tritone", "set:ADBE Tritone-0002=" + hl]


def test_3_golden_builtin_and_no_yellow_glitch(xml_subs, tmp_path):
    """3. Golden: по умолчанию и glitch_glow='builtin' .jsx одинаковы; deepglow2 без жёлтого глитча — тоже."""
    jsx_def, _ = _build(xml_subs, tmp_path, _glow_intro(), name="golden_def.jsx")
    jsx_builtin, _ = _build(xml_subs, tmp_path, _glow_intro(), glitch_glow="builtin", name="golden_builtin.jsx")
    assert jsx_def == jsx_builtin
    assert "PEDG2" not in jsx_builtin
    assert "DG_MISS" not in jsx_builtin
    assert "REELSI_DG_MISS" not in jsx_builtin

    # deepglow2 на интро без жёлтого глитча (только accent-глитч) — байт в байт как builtin
    jsx_acc_builtin, _ = _build(xml_subs, tmp_path, _accent_only_glitch_intro(), glitch_glow="builtin", name="golden_acc_builtin.jsx")
    jsx_acc_dg, _ = _build(xml_subs, tmp_path, _accent_only_glitch_intro(), glitch_glow="deepglow2", name="golden_acc_dg.jsx")
    assert jsx_acc_builtin == jsx_acc_dg
    assert "PEDG2" not in jsx_acc_dg


@node
def test_4_message_modes_and_node_check(xml_subs, tmp_path):
    """4. Режимы сообщения: ручной to_ae_full, с render_dir, comps_global, build_combined."""
    intro = _glow_intro()
    # Ручной to_ae_full
    jsx_manual, p_manual = _build(xml_subs, tmp_path, intro, glitch_glow="deepglow2", name="dg_manual.jsx")
    assert jsx_manual.startswith("$.global.REELSI_DG_MISS=0;")
    assert len([l for l in jsx_manual.splitlines() if "alert(" in l and "Deep Glow 2" in l]) == 1

    # Проверка синтаксиса node --check (копируем в .js, так как node не принимает расширение .jsx)
    p_check = p_manual + ".check.js"
    shutil.copy(p_manual, p_check)
    chk = subprocess.run(["node", "--check", p_check], capture_output=True, text=True, encoding="utf-8")
    assert chk.returncode == 0, f"node --check failed: {chk.stderr}"

    # С render_dir (безголовый рендер одного ролика)
    rdir = str(tmp_path / "renders")
    os.makedirs(rdir, exist_ok=True)
    jsx_rdir, _ = _build(xml_subs, tmp_path, intro, glitch_glow="deepglow2", render_dir=rdir, name="dg_rdir.jsx")
    assert '_LOG("ОШИБКА: ' in jsx_rdir
    assert not any("alert(" in l and "Deep Glow 2" in l for l in jsx_rdir.splitlines())
    assert "$.global.REELSI_DG_MISS=0;" not in jsx_rdir

    # С comps_global=True (таймлайн под мастером рендера)
    jsx_comps, _ = _build(xml_subs, tmp_path, intro, glitch_glow="deepglow2", comps_global=True, name="dg_comps.jsx")
    assert "REELSI_MASTER_LOG" in jsx_comps
    assert "ОШИБКА:" in jsx_comps
    assert not any("alert(" in l and "Deep Glow 2" in l for l in jsx_comps.splitlines())

    # build_combined из двух ручных частей: ровно один alert с «Deep Glow 2» и один сброс в начале
    job1 = dict(xml_path=xml_subs, intro=intro, intro_splits=[1], style={}, intro_mode="word",
                disclaimer="", glitch_glow="deepglow2")
    job2 = dict(xml_path=xml_subs, intro=intro, intro_splits=[1], style={}, intro_mode="word",
                disclaimer="", glitch_glow="deepglow2")
    comb_path = str(tmp_path / "dg_combined.jsx")
    xml2ae.build_combined([job1, job2], comb_path, emit=lambda *a: None)
    jsx_comb = open(comb_path, encoding="utf-8-sig").read()
    assert jsx_comb.startswith("$.global.REELSI_DG_MISS=0;")
    assert jsx_comb.count("$.global.REELSI_DG_MISS=0;") == 2  # в начале и после alert в конце
    assert len([l for l in jsx_comb.splitlines() if "alert(" in l and "Deep Glow 2" in l]) == 1


def test_5_api_roundtrip_invalid_and_get_default(client, monkeypatch):
    """5. API: set_glitch_glow roundtrip, 'foo' ошибка и неизменность, GET без ключа -> 'builtin'."""
    old = aicut.load_ai_config().get("glitch_glow", "builtin")
    try:
        for want in ("deepglow2", "builtin"):
            d = client.post("/api/ai_config",
                            json={"action": "set_glitch_glow", "value": want}).get_json()
            assert d.get("glitch_glow") == want, d
            assert client.get("/api/ai_config").get_json()["glitch_glow"] == want

        # value: 'foo' — ошибка, значение не изменилось
        d_err = client.post("/api/ai_config",
                            json={"action": "set_glitch_glow", "value": "foo"}).get_json()
        assert d_err.get("error"), d_err
        assert client.get("/api/ai_config").get_json()["glitch_glow"] == "builtin"
    finally:
        client.post("/api/ai_config", json={"action": "set_glitch_glow", "value": old})

    # GET при отсутствии ключа — "builtin"
    dummy_cfg = {"active": "LM Studio", "profiles": {}}
    monkeypatch.setattr("core.aicut.config.load_ai_config", lambda: dummy_cfg)
    monkeypatch.setattr(aicut, "load_ai_config", lambda: dummy_cfg)
    assert client.get("/api/ai_config").get_json()["glitch_glow"] == "builtin"


def test_6_norm_build_jobs_passes_glitch_glow(monkeypatch, xml_subs):
    """6. _norm_build_jobs кладёт в каждый клип glitch_glow из aicut.glitch_glow_mode()."""
    from api.build import _norm_build_jobs
    monkeypatch.setattr(aicut, "glitch_glow_mode", lambda: "deepglow2")
    norm = _norm_build_jobs([{"xml": xml_subs}])
    assert len(norm) == 1
    assert norm[0]["glitch_glow"] == "deepglow2"


def test_7_ui_static_glitchglow(html):
    """7. UI-статика: id='glitchglow' ровно один, опции builtin и deepglow2."""
    tools = html[html.index('id="aistab_tools"'):html.index('id="aistab_cut"')]
    assert tools.count('id="glitchglow"') == 1
    m = re.search(r'<select[^>]*id="glitchglow"[^>]*>(.*?)</select>', tools, re.S)
    assert m, "селект glitchglow не найден"
    body = m.group(1)
    assert 'value="builtin"' in body
    assert 'value="deepglow2"' in body
