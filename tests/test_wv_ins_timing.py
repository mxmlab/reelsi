# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Правка тайминга вставки на шаге 3 доезжает до карточки шага 2 (и переживает переоткрытие).

Драг блока вставки на таймлайне и ручки краёв (itlBlockDown → insSetSD) правили ТОЛЬКО
список INS шага 3. Источник истины для вставок — карточки шага 2 (CLIPS[curAE].inserts):
ensureJobs на каждом открытии предпросмотра ПЕРЕСОБИРАЕТ c.job.ins из них. Поэтому сдвиг
по времени и растяжка молча пропадали: закрыл превью, открыл — вставка на старом месте
(сдвиг x/y этим же багом уже ловили, у него запись в карточку есть).

Стенд гоняет БОЕВЫЕ функции из static/app/ (node), копий правил в тесте нет:
itlBlockDown тащат фальшивыми pointer-событиями, затем ensureJobs пересобирает список.

Запуск: py -3.10 -m pytest tests/test_wv_ins_timing.py -q
"""
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

INS_JS = os.path.join(ROOT, "static", "app", "85-inserts-view.js")
AE_JS = os.path.join(ROOT, "static", "app", "90-ae.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_ins_video_preview.py)."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
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


def _stand():
    """Боевые функции обоих файлов + мини-DOM: ровно то, что нужно драгу и пересборке."""
    ins = _read(INS_JS)
    ae = _read(AE_JS)
    parts = [_func(ae, "defJob")]
    parts += [_func(ins, n) for n in ("normInsPath", "ipvIns", "insSD", "insSetSD", "insCardFor",
                                      "insSetSDCard", "ipvAfterEdit", "cardToIns", "itlBlockDown")]
    parts.append(_func(ae, "ensureJobs"))
    return "\n".join(parts)


# Мини-DOM стенда: блок таймлайна ($('itlb0')) и кнопки окна, которые дёргает пересборка.
_DOM = r"""
function El(){this.style={};this.dataset={};}
El.prototype.classList={contains:function(){return false;}};
var _els={};
function $(id){return _els[id]||(_els[id]=new El());}
var _win={};
var window={addEventListener:function(n,f){(_win[n]=_win[n]||[]).push(f);},
  removeEventListener:function(n,f){var a=_win[n]||[];var i=a.indexOf(f);if(i>=0)a.splice(i,1);}};
function _fire(n,ev){(_win[n]||[]).slice().forEach(function(f){f(ev);});}
var ITL={pps:20};
var IPV={fps:60,dur:30,vids:[1],playing:false};
var CNT={},HL=new Set(),BRK=new Set(),JNS=new Set(),HLXML='';
function t(s){return s;}
function fmtIns(s){return s+'s';}
function insLbl(){return 'ins';}
function ipvRefresh(){}function itlDraw(){}function itlPh(){}function ipvSeekTo(){}
function renderIns(){}function captureAE(){}function saveState(){}
function renderInsHost(){}function syncClipLists(){}function clipNcams(){return 2;}
"""


def _card(media, start, dur, **over):
    c = {"type": "photo", "media": media, "query": "q", "start_sec": start,
         "duration_sec": dur, "mosaic": False, "plate": False}
    c.update(over)
    return c


def _run_setup(cards):
    """Собрать стенд: клип шага 2 с карточками + INS шага 3 из них (как openAEPreview)."""
    clip = {"xml": "clip.xml", "name": "clip", "inserts": cards, "job": None}
    setup = ("var CLIPS=[%s];var curAE=0;var IPVMODE='ae';var curIns=-1;"
             "var INS=[];ensureJobs();INS=CLIPS[0].job.ins.map(function(x){return Object.assign({},x);});"
             % json.dumps(clip, ensure_ascii=False))
    return setup


def _run_node(code):
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@node
def test_drag_writes_timing_into_step2_card_and_survives_reopen():
    """Драг блока на +2 с: start_sec карточки меняется, а после ensureJobs правка жива."""
    cards = [_card(r"C:\m\a.png", 1, 2), _card(r"C:\m\b.mp4", 5, 2, type="video")]
    code = (_stand() + _DOM + _run_setup(cards) + r"""
itlBlockDown({preventDefault:function(){},stopPropagation:function(){},clientX:100},0,'move');
_fire('pointermove',{clientX:140});
_fire('pointerup',{clientX:140});
var card=CLIPS[0].inserts[0];
ensureJobs();                                   // имитация повторного открытия превью
console.log(JSON.stringify({card:card.start_sec,cardDur:card.duration_sec,
  card2:CLIPS[0].inserts[1].start_sec,
  after:CLIPS[0].job.ins[0].start_s,dur:CLIPS[0].job.ins[0].dur_s}));
""")
    out = _run_node(code)
    # сдвиг на +40 px при ITL.pps=20 — это ровно +2 с
    assert out["after"] == 3, (
        f"после повторного открытия (ensureJobs) тайминг вставки потерян: {out}")
    assert out["card"] == 3, f"драг не записал тайминг в карточку шага 2: {out}"
    assert out["cardDur"] == 2, f"длительность при сдвиге не меняется: {out}"
    assert out["card2"] == 5, f"задет тайминг чужой вставки: {out}"


@node
def test_edge_handle_writes_duration_into_step2_card_and_survives_reopen():
    """Растяжка за правый край: duration_sec карточки растёт и переживает ensureJobs."""
    cards = [_card(r"C:\m\a.png", 1, 3)]
    code = (_stand() + _DOM + _run_setup(cards) + r"""
itlBlockDown({preventDefault:function(){},stopPropagation:function(){},clientX:100},0,'r');
_fire('pointermove',{clientX:120});
_fire('pointerup',{clientX:120});
var cardDur=CLIPS[0].inserts[0].duration_sec;
var cardStart=CLIPS[0].inserts[0].start_sec;
ensureJobs();
console.log(JSON.stringify({dur:cardDur,start:cardStart,after:CLIPS[0].job.ins[0].dur_s}));
""")
    out = _run_node(code)
    assert out["dur"] == 4, f"ручка края не записала длительность в карточку: {out}"
    assert out["start"] == 1, f"старт при растяжке за правый край не меняется: {out}"
    assert out["after"] == 4, f"после ensureJobs длительность потеряна: {out}"


@node
def test_timing_write_back_is_one_door_for_both_modes():
    """Запись тайминга в карточку — общий помощник (insCardFor/insSetSDCard), не копия в драге.

    Тот же поиск карточки по normInsPath, что у записи x/y и у ensureJobs: три места —
    одно правило. Копия разъехалась бы так же, как уже разъезжались introResolve.
    """
    ins = _read(INS_JS)
    drag = ins[ins.index("function itlBlockDown("):]
    drag = drag[:drag.index("\n// клик/протяжка по фону")]
    assert "insSetSDCard(x,s,d)" in drag, "драг таймлайна не пишет тайминг в карточку"
    assert "insCardFor(" in _func(ins, "insSetSDCard"), (
        "запись в карточку ищет её мимо общего insCardFor")
    assert "normInsPath" in _func(ins, "insCardFor"), (
        "карточка ищется не тем norm-сравнением пути, что ensureJobs")
    assert "cl.inserts.findIndex" in _func(ins, "insCardFor"), "поиск карточки потерял findIndex"

    # Ручная вставка шага 3 (карточки у неё нет) — запись не падает и ничего не портит
    code = (_stand() + _DOM
            + "var CLIPS=[{xml:'c.xml',inserts:[]}];var curAE=0;var IPVMODE='ae';var INS=[];"
            + "console.log(JSON.stringify({found:insCardFor({media:'C:\\\\m\\\\manual.png'})}));")
    out = _run_node(code)
    assert out["found"] is None, f"для вставки без карточки insCardFor вернул не null: {out}"
