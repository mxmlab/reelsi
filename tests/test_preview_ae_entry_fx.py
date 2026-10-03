# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Эффекты ПОЯВЛЕНИЯ в превью: вход фото-вставки, вход видеовставки, жёлтое слово, раскрытие интро.

Сверка рендера без AE с собранным проектом AE ловила четыре расхождения — и все четыре
про появление:

  * фото-вставка приходила РЕЗКОЙ: в .jsx на слое cam2-вставки стоит Box Blur с ключами
    ``ins.anim.blur`` (``INS_BLUR`` 41 px -> 0 за ``INS_ENTER``, обратно за ``INS_EXIT``),
    а превью его не рисовало вовсе;
  * вход ВИДЕОвставки не начинался до её ``start``: в .jsx на входе и выходе каждой
    видеовставки лежит слой перехода (``addTransAt``) со ``startTime = cut - TR_IN`` —
    то есть за 0.386 с ДО стыка, файл играет от нуля, наложением Add и поверх всего;
  * жёлтое слово: время появления (``t0``), подъём, проявление и блюр появления
    (``hlBlur``: HL_BLUR -> 0 за ``hl_dur`` по кривой easePair) — всё из плана;
  * раскрытие интро играет F_DUR .jsx (ключи блюра, Scale слоя и Percent Offset селектора
    стоят на ``t0+F_DUR*SQ``), а не длительность глитча.

Проверки держат ОДИН источник чисел: их считает Python (план сцены), ими же играет .jsx,
а превью их только рисует. Тела функций превью берутся из боевого
``static/app/85-inserts-view.js`` и исполняются node'ом (как в соседних тестах).
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

from PIL import Image  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import HL_DUR, INS_BLUR, INS_ENTER, _blur_keys  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")
JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")

# Числа ролика, на котором сверяли кадры: входы фото- и видеовставки, жёлтое слово
# с блюром появления и раскрытие интро. Тайминги — как у вставок того клипа.
PHOTO_T0, VIDEO_T0, INS_DUR = 12.92, 29.17, 2.5
HL_BLUR_AMT = 70.4
STYLE = {"insert_anim": "zoom", "insert_fx": "card", "insert_style": "cam2",
         "hl_blur": True, "hl_blur_amt": HL_BLUR_AMT}


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


# Роли ассетов рядом с XML: файлы кладём в `<tmp_path>/assets` — ровно туда, откуда их
# берёт `scene_plan`. Без СВОЕГО `assets.json` роли перехода разрешались в файлы папки
# `assets/` репозитория: локально они там лежат, а в публичном срезе её нет вовсе —
# и тесты перехода падали на `plan["trans"] is None` (папка не в git, в срез не едет).
ASSET_ROLES = {"transition": "trans.mov"}
# Размер файла перехода: 4K, как настоящий `Quick 2.mov`. Файла-медиа в тесте нет,
# размеров не прочитать — подменяем `_media_dims` в build (как вернул бы ffprobe).
TRANS_W, TRANS_H = 3840, 2160


@pytest.fixture(autouse=True)
def _isolate_assets(xml_subs, tmp_path, monkeypatch):
    """Свои ассеты рядом с XML и размер перехода: план и .jsx читают одни числа."""
    adir = tmp_path / "assets"
    adir.mkdir(exist_ok=True)
    for fn in set(ASSET_ROLES.values()):
        (adir / fn).write_bytes(b"RIFF")
    (adir / "assets.json").write_text(json.dumps(ASSET_ROLES), encoding="utf-8")

    from core.xml2ae import build as build_mod

    def _dims(path):
        # Размеры — только видео и переходу: у фото-вставки их не спрашивают вовсе.
        p = (path or "").lower()
        return (TRANS_W, TRANS_H) if p.endswith((".mp4", ".mov")) else None

    monkeypatch.setattr(build_mod, "_media_dims", _dims)


@pytest.fixture()
def photo(tmp_path):
    """Фото 2000x500 — как у вставки клипа: видимая часть упирается в ширину кадра."""
    p = str(tmp_path / "photo.png")
    Image.new("RGB", (2000, 500), (200, 60, 60)).save(p)
    return p


def _inserts(photo):
    """Вставки клипа: фото (cam2, наезд) и видео (мозаика) — те же тайминги и стиль."""
    return [{"type": "photo", "style": "cam2", "media": photo,
             "start_s": PHOTO_T0, "dur_s": INS_DUR},
            {"type": "video", "style": "cam2", "media": "C:/v/7581432-uhd.mp4",
             "start_s": VIDEO_T0, "dur_s": INS_DUR, "mosaic": True}]


def _plan(xml, photo, style=None, inserts=None, intro=None, highlights=None):
    """План сцены: вставки клипа, стиль клипа, интро (раскрытие) — как в ролике сверки."""
    st = dict(STYLE)
    st.update(style or {})
    return xml2ae.scene_plan(xml, inserts=list(inserts if inserts is not None
                                               else _inserts(photo)),
                             intro=intro, highlights=highlights, style=st, disclaimer="",
                             intro_riser=False, include_xml_inserts=False,
                             emit=lambda *a, **k: None)


def _jsx(xml, photo, tmp_path, style=None, inserts=None, intro=None, name="out.jsx"):
    st = dict(STYLE)
    st.update(style or {})
    path, _, _ = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / name),
        inserts=list(inserts if inserts is not None else _inserts(photo)),
        intro=intro, style=st, disclaimer="", intro_riser=False,
        include_xml_inserts=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _item(plan, kind):
    for x in plan["inserts"]:
        if (x.get("t") or x.get("type")) == kind:
            return x
    raise AssertionError(f"в плане нет вставки вида {kind!r}: {plan['inserts']}")


# --------------------------------------------------------------------------- #
# 1. План и .jsx: одни и те же числа на вход вставки
# --------------------------------------------------------------------------- #
def test_photo_insert_blur_keys_are_the_jsx_box_blur(xml_subs, photo, tmp_path):
    """Ключи блюра вставки в плане — ровно те, что .jsx ставит на Box Blur слоя.

    В шаблоне эффект вешается только по наличию ключей
    (``if(ins.anim && ins.anim.blur)`` + ``applyKeyframes(... "ADBE Box Blur2-0001", ins.anim.blur)``),
    поэтому «нет ключей» = «вставка приходит резкой». Проверяем и наличие ключей, и то,
    что .jsx читает ИХ, а не считает свои.
    """
    plan = _plan(xml_subs, photo)
    jsx = _jsx(xml_subs, photo, tmp_path)
    ins = _item(plan, "photo")

    t0, t1 = ins["start"], ins["end"]
    assert ins["style"] == "cam2", "стиль фото уехал — блюр входа есть только у cam2"
    # Ключи — ровно правило layout._blur_keys (то же, что печатает .jsx): 41 px на t0,
    # ноль за INS_ENTER, ноль до конца, 41 px на выходе. Выход срезан катом (noexit) —
    # последнего ключа нет, и блюр не возвращается: так же играет и .jsx.
    assert ins["anim"]["blur"] == _blur_keys(t0, t1, ins["noexit"], 1.0), \
        f"ключи блюра вставки разошлись с формулой .jsx: {ins['anim']['blur']}"
    assert ins["anim"]["blur"][0] == [t0, INS_BLUR] and \
        ins["anim"]["blur"][1] == [round(t0 + INS_ENTER, 4), 0.0], \
        f"вход вставки не размыт: {ins['anim']['blur']}"
    if ins["noexit"]:
        assert ins["anim"]["blur"][-1] == [t1, 0.0], "срезанный катом выход всё ещё размыт"
    else:
        assert ins["anim"]["blur"][-1] == [t1, INS_BLUR], "выход вставки не размыт"

    assert "if(ins.anim && ins.anim.blur){" in jsx, \
        "в .jsx нет ветки блюра вставки — ключи плана некому ставить"
    assert 'addFX(L,"ADBE Box Blur2")' in jsx, "в .jsx не тот эффект блюра вставки"
    assert 'applyKeyframes(bl.property("ADBE Box Blur2-0001"), ins.anim.blur);' in jsx, \
        ".jsx считает ключи блюра сам — план и сборка разъедутся"


def test_video_insert_entry_is_the_transition_not_blur(xml_subs, photo, tmp_path):
    """Вход видеовставки — слой перехода: в .jsx он ставится ДО стыка, у вставки нет anim.

    Тот же вопрос «что происходит на входе», но для видео: ключей блюра у неё нет вовсе,
    а вход даёт слой перехода со ``startTime = cut - TR_IN`` (то есть видно РАНЬШЕ
    ``start`` вставки). Числа перехода — из плана (``trans``), второй копии нет.
    """
    plan = _plan(xml_subs, photo)
    jsx = _jsx(xml_subs, photo, tmp_path)
    ins = _item(plan, "video")

    assert "anim" not in ins, f"у видеовставки завелись ключи анимации: {ins.get('anim')}"
    tr = plan["trans"]
    assert tr and tr["in"] == pytest.approx(0.386), f"план не несёт сдвиг перехода: {tr}"

    # .jsx: объявление сдвига — из того же числа (подстановка), слой — за TR_IN до стыка
    m = re.search(r"var TR_IN = ([\d.]+), TR_SFX_LEAD = ([\d.]+);", jsx)
    assert m, "в .jsx не нашлось объявление TR_IN/TR_SFX_LEAD"
    assert float(m.group(1)) == pytest.approx(tr["in"]), \
        f"TR_IN .jsx ({m.group(1)}) разошёлся с планом ({tr['in']})"
    assert "tl.startTime=cut-TR_IN;" in jsx, "слой перехода в .jsx ставится не по TR_IN"
    assert "addTransAt(t0); if(!ins.noexit) addTransAt(t1);" in jsx, \
        "в .jsx нет слоя перехода на входе и выходе видеовставки"

    # Файл перехода — тот же, что уехал в .jsx (TRANS)
    assert 'var TRANS=%s, TRANS_SFX=' % json.dumps(tr["media"]) in jsx, \
        f"файл перехода в .jsx не совпал с планом: {tr['media']!r}"


def test_reveal_duration_is_the_jsx_f_dur(xml_subs, photo, tmp_path):
    """Длительность раскрытия интро в плане — F_DUR .jsx (ключи блюра и селектора).

    Раскрытие играет столько же, сколько фейд и масштаб: ``pBl.setValueAtTime(t0,...)`` ->
    ``t0+F_DUR``, ``Percent Offset`` -100 -> 100 за F_DUR. Пока план нёс длительность
    ГЛИТЧА, превью открывало слово позже AE; теперь это одно число — ``intro_anims.f_dur``.
    """
    intro = [dict(words=["что", "то", "вроде"], color="white", times=[0.83, 0.98, 1.17],
                  anim="reveal")]
    plan = _plan(xml_subs, photo, intro=intro)
    jsx = _jsx(xml_subs, photo, tmp_path, intro=intro, name="reveal.jsx")

    m = re.search(r"var LINE_STEP=[\d.]+, F_DUR=([\d.]+),", jsx)
    assert m, "в .jsx не нашлась объявленная длительность F_DUR"
    f_dur = float(m.group(1))

    anims = plan["intro_anims"]
    assert anims["f_dur"] == pytest.approx(f_dur), \
        f"план несёт F_DUR={anims['f_dur']}, а .jsx играет {f_dur}"
    assert anims["reveal"]["dur"] == pytest.approx(f_dur), \
        "длительность раскрытия в плане не F_DUR: превью открывало бы слово позже AE"
    assert anims["reveal"]["dur"] != pytest.approx(anims["glitch"]["dur"]), \
        "длительность раскрытия снова равна длительности глитча"

    # Те же ключи в .jsx (раскрытие: блюр и Scale слоя — на t0 и t0+F_DUR)
    assert f"pBl.setValueAtTime(t0,{anims['reveal']['blur']:g}); pBl.setValueAtTime(t0+F_DUR,0);" in jsx
    assert "pOff.setValueAtTime(t0,-100); pOff.setValueAtTime(t0+F_DUR,100);" in jsx
    # Жёлтое слово: блюр появления играет HL_DUR, и он в плане тот же
    assert plan["hl_dur"] == HL_DUR and plan["hl_blur"] is True


# --------------------------------------------------------------------------- #
# 2. Стенды node: боевые функции превью на тех же числах
# --------------------------------------------------------------------------- #
def _func(src, name):
    """Тело функции (или объявление const) из боевого исходника превью."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    if not m:
        m = re.search(r"^const %s=.*?;$" % re.escape(name), src, re.M)
        assert m, f"в исходнике не нашлось {name}"
        return m.group(0)
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
    src = open(CORE_JS, encoding="utf-8").read() + "\n" + open(JS, encoding="utf-8").read()
    return "\n".join(_func(src, n) for n in names)


def _run_node(code, tmp_path, name):
    f = tmp_path / name
    f.write_text(code, encoding="utf-8")
    res = subprocess.run(["node", str(f)], capture_output=True, text=True,
                         encoding="utf-8-sig", errors="replace", timeout=60)
    assert res.returncode == 0, f"node упал: {(res.stderr or '')[-2000:]}"
    return res.stdout


# ---- стенд 1: блюр входа и выхода фото-вставки ---------------------------------
_INS_DOM = r"""
const assert = require('assert');
const ipl=null;
function El(tag){
  this.tagName=String(tag).toUpperCase();this.style={};this.children=[];this.dataset={};
  this._attrs={};this._ev={};
}
El.prototype.appendChild=function(c){this.children.push(c);return c;};
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.setAttribute=function(n,v){this._attrs[n]=String(v);};
El.prototype.getAttribute=function(n){return (n in this._attrs)?this._attrs[n]:null;};
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


@node
def test_preview_blurs_photo_insert_entry_and_exit(xml_subs, photo, tmp_path):
    """Превью (node, боевой ipvInsPlace): на входе у вставки ТОТ ЖЕ блюр, что в .jsx.

    Момент 12.95 с — кадр входа: в AE вставка в этот момент размыта (Box Blur 41 -> 0 за
    0.38 с), у превью она появлялась только прозрачностью и выглядела резкой. Кривая —
    ключи ПЛАНА (``anim.blur``), интерполяция — та же ``keysAt`` (35/90, как ``bez()`` в .jsx).
    """
    plan = _plan(xml_subs, photo)
    ins = _item(plan, "photo")
    t0 = ins["start"]
    # Вторая вставка — то же окно, но не срезанное катом: у неё видно и ВЫХОД.
    full = dict(ins, noexit=False, end=round(t0 + INS_DUR, 4))
    full["anim"] = dict(ins["anim"], blur=_blur_keys(full["start"], full["end"], False, 1.0))
    code = (_INS_DOM + _js("aeEase", "bezierT", "bezierY", "keysAt", "ipvCamChild", "ipvInsPlace")
            + "\nconst item=%s;\nconst full=%s;\n" % (json.dumps(ins), json.dumps(full))
            + """
    IPV.plan={w:1080,h:1920,ins_c2x:540,ins_c2y:330,
              zoom:{cx:0.5,cy:0.5,rot:0,pan:[0,0],keys:[[0,100]],ease:[[33.3333,33.3333]]}};
    function shot(it,tm){ipvInsPlace(WRAP,it,tm);
      const keys=(it.anim&&it.anim.blur)||[];
      return {f:WRAP.firstChild.style.filter||'', op:WRAP.firstChild.style.opacity,
              blur:keysAt(keys,null,tm)};}
    const entry=shot(item,%s), mid=shot(item,%s), out=shot(full,%s), last=shot(full,%s);
    console.log(JSON.stringify({entry:entry,mid:mid,out:out,last:last,
      t0:item.start,t1:item.end,ft1:full.end}));
    """ % (t0 + 0.03, t0 + 0.7, full["end"] - 0.2, full["end"]))
    out = json.loads(_run_node(code, tmp_path, "ins_blur.js"))

    # вход: 12.95 — блюр есть и он РОВНО по ключам плана (та же кривая, что в .jsx)
    assert out["entry"]["blur"] > 20, f"вход вставки уже не размыт: {out['entry']}"
    assert out["entry"]["f"] == f"blur({out['entry']['blur']:.2f}px)", \
        f"блюр входа не по ключам .jsx: {out['entry']}"
    # середина показа: резко (ключи держат ноль между входом и выходом)
    assert out["mid"]["blur"] == 0 and out["mid"]["f"] == "", \
        f"в середине показа вставка осталась размытой: {out['mid']}"
    # выход: размытие возвращается, к последнему ключу — полное
    assert out["out"]["blur"] > 20 and out["out"]["f"] == \
        f"blur({out['out']['blur']:.2f}px)", f"выход вставки без блюра: {out['out']}"
    assert out["last"]["blur"] == INS_BLUR and \
        out["last"]["f"] == f"blur({float(INS_BLUR):.2f}px)", \
        f"на выходе блюр не вернулся к INS_BLUR: {out['last']}"


@node
def test_preview_without_blur_keys_leaves_insert_sharp(xml_subs, photo, tmp_path):
    """Вставка без ключей блюра (панель выключила наезд) рисуется резкой — как .jsx.

    Это и есть проверка «числа из плана»: превью не помнит своего блюра, а вешает фильтр
    ровно тогда, когда ключи в плане есть. Убери из плана ``anim.blur`` — фильтра нет.
    """
    plan = _plan(xml_subs, photo)
    ins = dict(_item(plan, "photo"))
    ins["anim"] = {"opacity": ins["anim"]["opacity"], "scale": ins["anim"]["scale"]}
    code = (_INS_DOM + _js("aeEase", "bezierT", "bezierY", "keysAt", "ipvCamChild", "ipvInsPlace")
            + "\nconst item=%s;\n" % json.dumps(ins)
            + """
    IPV.plan={w:1080,h:1920,ins_c2x:540,ins_c2y:330,
              zoom:{cx:0.5,cy:0.5,rot:0,pan:[0,0],keys:[[0,100]],ease:[[33.3333,33.3333]]}};
    ipvInsPlace(WRAP,item,item.start+0.03);
    console.log(JSON.stringify({f:WRAP.firstChild.style.filter||'',
                                keys:item.anim.blur||null}));
    """)
    out = json.loads(_run_node(code, tmp_path, "ins_noblur.js"))
    assert out["keys"] is None and out["f"] == "", \
        f"фильтр повесили без ключей плана: {out}"


# ---- стенд 2: слой перехода на входе видеовставки -------------------------------
_TRANS_DOM = r"""
const assert = require('assert');
function El(tag){
  this.tagName=String(tag).toUpperCase();this.style={};this.children=[];this.dataset={};
  this._attrs={};this._ev={};this.paused=true;this.currentTime=0;
  this.videoWidth=0;this.videoHeight=0;this.duration=1.6266;this.muted=false;this.volume=1;
}
El.prototype.appendChild=function(c){c.parentNode=this;this.children.push(c);return c;};
El.prototype.remove=function(){if(this.parentNode){const i=this.parentNode.children.indexOf(this);
  if(i>=0)this.parentNode.children.splice(i,1);this.parentNode=null;}};
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.setAttribute=function(n,v){this._attrs[n]=String(v);};
El.prototype.removeAttribute=function(n){delete this._attrs[n];};
El.prototype.getAttribute=function(n){return (n in this._attrs)?this._attrs[n]:null;};
El.prototype.querySelectorAll=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  const out=[];(function scan(el){for(const ch of el.children){
    if(want==='video'?ch.tagName==='VIDEO':ch._cls&&ch._cls.has(want))out.push(ch);scan(ch);}})(this);
  return out;};
Object.defineProperty(El.prototype,'className',{
  get(){return [...(this._cls||[])].join(' ');},
  set(v){this._cls=new Set(String(v||'').split(/\s+/).filter(Boolean));}});
Object.defineProperty(El.prototype,'innerHTML',{
  get(){return this._html||'';},
  set(v){this._html=String(v);if(!v)this.children=[];}});
Object.defineProperty(El.prototype,'clientWidth',{get(){return 1080;}});
El.prototype.play=function(){this.paused=false;return {catch(){}};};
El.prototype.pause=function(){this.paused=true;};
El.prototype.load=function(){};
const STAGE=new El('div');
const document={createElement:t=>new El(t)};
function $(id){
  if(id==='ipvstage')return STAGE;
  return STAGE.children.find(c=>c._id===id)||null;}
const IDS={};
function markIds(){STAGE.children.forEach(c=>{if(c.id)c._id=c.id;});}
function pvSrc(p){return '/api/media?path='+encodeURIComponent(p);}
function vidSeekTol(){return 0.4;}
function ipvNow(){return 0;}
function ipvUI(){}
const CSS={supports:()=>true};
const IPV={fps:60,plan:null,playing:false,vids:[1],transSig:'',transAsked:''};
"""


def _trans_stand(plan, tmp_path, name, times_js):
    """Стенд перехода: план клипа + боевые ipvTransPlan/ipvTrans, кадры из times_js."""
    code = (_TRANS_DOM + _js("mediaFree", "ipvTransPlan", "ipvTrans")
            + "\nIPV.plan=%s;\n" % json.dumps(plan)
            + """
    function shot(tm){ipvTrans(tm);markIds();
      const box=$('ipvtrans');
      if(!box)return {box:false};
      const vs=box.querySelectorAll('video');
      return {box:true, n:vs.length, blend:box.style.mixBlendMode||'',
        disp:vs.map(v=>v.style.display||''), w:vs.map(v=>v.style.width||''),
        h:vs.map(v=>v.style.height||''), ct:vs.map(v=>+(v.currentTime||0)),
        start:vs.map(v=>+v.dataset.t0)};}
    const out={};
    %s
    console.log(JSON.stringify(out));
    """ % times_js)
    return json.loads(_run_node(code, tmp_path, name))


@node
def test_preview_transition_starts_before_video_insert(xml_subs, photo, tmp_path):
    """Превью (node, боевые ipvTransPlan/ipvTrans): вход вставки начинается ДО её start.

    В .jsx слой перехода ставится на ``cut - TR_IN`` (0.386 с до стыка), поэтому кадр
    перед входом видеовставки в AE уже несёт размытие перехода, а у нас был обычный
    кадр камеры. Стенд берёт числа из плана (файл, сдвиг, размер исходника) и проверяет
    и время слоя, и его величину: файл 3840x2160 в кадре 1080x1920 виден центральной
    частью 1:1, значит элемент шире кадра вдвое с лишним — как слой в AE.
    """
    plan = _plan(xml_subs, photo)
    t0, t1 = (_item(plan, "video")["start"], _item(plan, "video")["end"])
    tr = plan["trans"]
    times = """
    out.before=shot(%r);          // до слоя перехода
    out.pre=shot(%r);             // за кадр до старта вставки — слой уже играет
    out.at=shot(%r);              // ровно старт вставки
    out.exit=shot(%r);            // за кадр до выхода — второй слой (выход вставки)
    out.late=shot(%r);            // оба файла кончились
    """ % (t0 - tr["in"] - 0.1, t0 - 0.07, t0, t1 - 0.07, t1 - tr["in"] + 1.7)
    out = _trans_stand(plan, tmp_path, "trans.js", times)

    # В .jsx слоёв ДВА на вставку: вход (addTransAt(t0)) и выход (addTransAt(t1)).
    assert out["before"]["box"] is False or out["before"]["disp"] == ["none", "none"], \
        f"слой перехода виден до своего старта: {out['before']}"
    pre = out["pre"]
    assert pre["box"] and pre["n"] == 2, f"слоёв перехода не два (вход и выход): {pre}"
    assert pre["disp"] == ["", "none"], f"на кадре перед вставкой вход не показан: {pre}"
    assert pre["ct"][0] == pytest.approx(0.316, abs=1e-6), \
        f"время файла перехода не tm-(start-TR_IN): {pre['ct']}"
    assert pre["start"][0] == pytest.approx(t0 - tr["in"], abs=1e-4), \
        f"слой начинается не за TR_IN до вставки: {pre['start']} против {t0 - tr['in']}"
    # величина исходника из плана: 4K-переход в кадре 1080x1920 (k=1)
    assert pre["w"][0] == "%dpx" % tr["w"] and pre["h"][0] == "%dpx" % tr["h"], \
        f"переход нарисован не во всю величину исходника: {pre['w']} {pre['h']}"
    assert pre["blend"] == "plus-lighter", \
        f"переход наложен не сложением (Add в .jsx): {pre['blend']}"
    assert out["at"]["disp"] == ["", "none"], f"на старте вставки переход пропал: {out['at']}"
    assert out["exit"]["disp"] == ["none", ""], \
        f"за кадр до выхода вставки нет слоя выхода: {out['exit']}"
    assert out["late"]["disp"] == ["none", "none"], \
        f"за концом файла слой перехода остался виден: {out['late']}"


@node
def test_preview_transition_absent_without_video_insert(xml_subs, photo, tmp_path):
    """Нет видеовставок (или файла перехода) — нет ни слоя, ни правила вовсе."""
    only_photo = [x for x in _inserts(photo) if x["type"] == "photo"]
    plan = _plan(xml_subs, photo, inserts=only_photo)
    out = _trans_stand(plan, tmp_path, "trans_none.js", "out.any=shot(%r);" % PHOTO_T0)
    assert plan["trans"] is None, f"план завёл переход без видеовставок: {plan['trans']}"
    assert out["any"] == {"box": False}, f"слой перехода нарисовался без видеовставки: {out}"


@node
def test_preview_transition_hidden_when_source_undecodable(xml_subs, photo, tmp_path):
    """Файл перехода не декодируется (ProRes без прокси) — слоя в кадре нет, рендер не ждёт.

    Покадровая дверь помечает такой `<video>` (_dead), и рендер его не ждёт: иначе
    vSeekDone ждал бы полный таймаут на КАЖДОМ кадре окна перехода.
    """
    plan = _plan(xml_subs, photo)
    t0 = _item(plan, "video")["start"]
    js = """
    ipvTrans(%r);
    markIds();
    const vs=$('ipvtrans').querySelectorAll('video');
    vs.forEach(v=>{v._dead=true;});
    const waited=vs.filter(v=>!v._dead).length;
    console.log(JSON.stringify({n:vs.length, dead:vs.filter(v=>v._dead).length,
                                waited:waited}));
    """ % (t0 - 0.07)
    code = _TRANS_DOM + _js("mediaFree", "ipvTransPlan", "ipvTrans") + "\nIPV.plan=%s;\n" % json.dumps(plan) + js
    out = json.loads(_run_node(code, tmp_path, "trans_dead.js"))
    assert out["n"] == 2 and out["dead"] == 2, f"флаг нечитаемого файла не встал: {out}"
    assert out["waited"] == 0, f"рендер всё равно ждал бы кадр немого файла: {out}"
    src = open(JS, encoding="utf-8").read()
    assert "filter(v=>!v._dead)" in src, "из ожидания кадров рендера пропал фильтр _dead"


# ---- стенд 3: жёлтое слово с блюром появления ----------------------------------
@node
def test_preview_yellow_word_appears_with_plan_blur(xml_subs, photo, tmp_path):
    """Жёлтое слово (node, боевая ipvSubs): видно с блюром и подъёмом по ключам .jsx.

    Тот же вопрос, что у вставки, но для жёлтого: время появления слова — ``t0`` плана
    (inPoint слоя в .jsx), а подъём, прозрачность и блюр играют за ``hl_dur`` по кривой
    easePair (в .jsx это ``easePair`` у posP/op и у ключей hlBlur). Стенд берёт ПЛАН
    целиком и проверяет кадр сразу после появления слова.
    """
    from test_rows_yellow import _run_preview
    plan = _plan(xml_subs, photo, style={"hl_row_anim": "word"}, highlights=[0, 1, 2, 3])
    hl = [x for x in plan["subs"] if x.get("color") == "yellow"]
    assert hl, "в плане фикстуры нет жёлтых слов — стенд ничего не проверяет"
    item = hl[0]
    t0 = item["s"]
    tm = round(t0 + 0.08, 4)                       # кадр входа: слово уже проявилось
    checks = r"""
    paint(plan,@TM@);
    const ws=words().filter(w=>w.dataset.hl0!==undefined);
    assert(ws.length>=1,'жёлтое слово не нарисовано вовсе');
    const w=ws[0];
    const t0=parseFloat(w.dataset.hl0), dur=plan.hl_dur, k=540/plan.w;
    const rem=keysAt([[t0,1],[t0+dur,0]],null,@TM@);
    assert(rem>0&&rem<1,'кривая появления дала крайнее значение: '+rem);
    assert(Math.abs(parseFloat(w.style.opacity)-(1-rem))<1e-6,
      'прозрачность не по кривой .jsx: '+w.style.opacity+' vs '+(1-rem));
    assert(w.style.filter==='blur('+(plan.hl_blur_css*rem*k).toFixed(2)+'px)',
      'блюр появления не по hl_blur_css плана: '+w.style.filter);
    assert(Math.abs(parseFloat(w.style.top)-plan.hl_rise*rem*k)<0.011,
      'подъём не по кривой .jsx: '+w.style.top);
    console.log("OK: yellow word with plan blur");
    """.replace("@TM@", json.dumps(tm))
    _run_preview(tmp_path, "yellow_entry.js", plan, checks)
    assert plan["hl_blur"] is True and plan["hl_blur_amt"] == HL_BLUR_AMT


# ---- стенд 4: блюр раскрытия интро ---------------------------------------------
_INTRO_DOM = r"""
const assert = require('assert');
class El{
  constructor(tag){this.tagName=String(tag).toUpperCase();
    this.style={_p:{},setProperty(k,v){this._p[k]=v;},removeProperty(k){delete this._p[k];}};
    this.dataset={};
    this.children=[];this._text='';this._cls=new Set();
    const self=this;
    this.classList={add:c=>self._cls.add(c),remove:c=>self._cls.delete(c),
      toggle:(c,v)=>{const on=(v===undefined)?!self._cls.has(c):!!v;
        if(on)self._cls.add(c);else self._cls.delete(c);return on;},
      contains:c=>self._cls.has(c)};}
  get className(){return [...this._cls].join(' ');}
  set className(v){this._cls=new Set(String(v||'').split(/\s+/).filter(Boolean));}
  get textContent(){return (this._text!==''?this._text:this.children.map(c=>c.textContent).join(''));}
  set textContent(v){this._text=''+(v==null?'':v);this.children=[];}
  get innerHTML(){return this._html||'';}
  set innerHTML(h){this._html=String(h);this.children=[];}
  appendChild(c){this.children.push(c);c.parent=this;return c;}
  remove(){if(this.parent){const i=this.parent.children.indexOf(this);
    if(i>=0)this.parent.children.splice(i,1);this.parent=null;}}
  get offsetTop(){return 0;}
  get offsetHeight(){return 100;}
  querySelectorAll(sel){
    const out=[];
    const cls=el=>((el._cls&&el._cls.has('iline'))?true:false);
    if(sel==='.iline'){
      (function scan(el){for(const ch of el.children){if(cls(ch))out.push(ch);scan(ch);}})(this);
      return out;}
    if(sel==='.iline > span'){
      for(const line of this.children)if(cls(line))
        for(const sp of line.children)if(sp.tagName==='SPAN')out.push(sp);
      return out;}
    (function scan(el){for(const ch of el.children){
      if(sel.indexOf('span')>=0&&ch.tagName==='SPAN')out.push(ch);scan(ch);}})(this);
    return out;}
  get offsetHeight(){return 100;}
  get clientWidth(){return 1080;}
}
const IO=new El('div');
function $(id){return id==='ipvintro'?IO:null;}
const document={createElement:t=>new El(t),
  createTextNode:txt=>({tagName:'#text',textContent:''+txt,children:[]}),
  fonts:{status:'loaded',ready:Promise.resolve()}};
function t(s){return s;}
function esc(s){return String(s==null?'':s);}
function introMarkPlaying(){}
function ipvIntroPos(){}
const IPVMODE='ae',FONTS=[],CURSTYLE={intro_hl_fill:[1,0.9,0],hl_fill3:[0.6863,0.1216,0.1216],
  intro_fill:[1,1,1],back_scale:0.69};
"""


@node
def test_preview_reveal_blur_follows_jsx_curve(xml_subs, photo, tmp_path):
    """Блюр раскрытия интро (node, боевой ipvIntro) — по кривой .jsx в момент 1.0 с.

    .jsx: ``pBl.setValueAtTime(t0,26.8); ...(t0+F_DUR,0)`` — линейно за F_DUR, а прозрачность
    и Scale слоя идут той же кривой (Scale линейно 70->100, opacity — easePair). Превью
    берёт F_DUR ИЗ ПЛАНА (``intro_anims.f_dur``) и считает ровно это; своей длительности у
    него нет. Слово «что» на 0.83 с — как в сверяемом ролике.
    """
    intro = [dict(words=["что", "то", "вроде"], color="white", times=[0.83, 0.98, 1.17],
                  anim="reveal")]
    plan = _plan(xml_subs, photo, intro=intro)
    tm = 1.0
    src = open(JS, encoding="utf-8").read()
    i0, i1 = src.find("function aeEase"), src.find("function ipvIntroPos(")
    assert i0 >= 0 and i1 > i0, "не нашлись функции анимации интро"
    code = (_INTRO_DOM + src[i0:i1]
            + "\nIPV={introCur:-2,intro:introGroupWindows(%s),plan:{intro_anims:%s,w:1080,h:1920,"
              "fsize:140,intro_fsize:140,posy:1132,hl_dur:%s,sub_step:140,sub_scale:100,"
              "intro_scale:100,intro_x:0,back_scale:0.69,back_step:70,hl_step:119.5}};\n"
            % (json.dumps(plan["intro"]), json.dumps(plan["intro_anims"]),
               json.dumps(plan["hl_dur"]))
            + """
    ipvIntro(%r);
    const sp=IO.querySelectorAll('.iline > span').filter(x=>x.dataset.origWord==='что');
    assert(sp.length===1,'слово «что» не нарисовано: '+sp.length);
    const el=sp[0];
    console.log(JSON.stringify({filter:el.style.filter||'',opacity:el.style.opacity||'',
      transform:el.style.transform||'',dur:ipvIntroAnimParams().reveal.dur,
      blur:ipvIntroAnimParams().reveal.blur,
      scale:ipvIntroAnimParams().reveal.scale,
      ease:ipvEase(0.1)}));
    """ % tm)
    out = json.loads(_run_node(code, tmp_path, "reveal_blur.js"))

    f_dur = plan["intro_anims"]["f_dur"]
    assert out["dur"] == pytest.approx(f_dur), \
        f"превью взяло не плановую длительность раскрытия: {out['dur']} против {f_dur}"
    assert out["blur"] == pytest.approx(plan["intro_anims"]["reveal"]["blur_css"]), \
        "превью взяло «Blurriness» AE вместо пикселей CSS из плана"
    u = (tm - 0.83) / f_dur
    assert out["filter"] == "blur(%.1fpx)" % ((1 - u) * out["blur"]), \
        f"блюр раскрытия не по кривой .jsx: {out['filter']} при u={u:.3f}"
    # Прозрачность — та же кривая easePair. Допуск 0.01: превью решает кривую
    # восемью итерациями Ньютона от q=u (bezierT), эталон — до сходимости; расхождение
    # в третьем знаке — это решение уравнения, а не другая кривая.
    assert abs(float(out["opacity"]) - ipv_ease_ref(u)) <= 0.01, \
        f"прозрачность раскрытия не по easePair: {out['opacity']} против {ipv_ease_ref(u):.3f}"
    assert out["transform"] == "scale(%.3f)" % (out["scale"] + (1 - out["scale"]) * u), \
        f"Scale слоя раскрытия не линейный 70->100: {out['transform']}"


def ipv_ease_ref(u):
    """Кривая easePair (cubic-bezier 0.35,0.01,0.10,0.99) — эталон для сверки с превью.

    Числа те же, что у `easePair` в .jsx (HL_EASE_OUT/HL_EASE_IN = 35/90): считаем здесь
    независимо от реализации превью — иначе тест проверял бы функцию ею же.
    """
    p1x, p1y, p2x, p2y = 0.35, 0.01, 0.10, 0.99
    q = u
    for _ in range(60):
        m = 1 - q
        x = 3 * m * m * q * p1x + 3 * m * q * q * p2x + q ** 3
        d = 3 * m * m * p1x + 6 * m * q * (p2x - p1x) + 3 * q * q * (1 - p2x)
        if abs(d) < 1e-12:
            break
        q = min(1.0, max(0.0, q - (x - u) / d))
    m = 1 - q
    return 3 * m * m * q * p1y + 3 * m * q * q * p2y + q ** 3
