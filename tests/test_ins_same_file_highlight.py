# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подсветка играющей вставки, когда у нескольких вставок один и тот же файл.

Дефект, повторно пойманный владельцем: три видеовставки с одним файлом. Подсвечивалась
(карточка `.playing`, блок таймлайна `.cur`) ПЕРВАЯ вставка с этим файлом, а не та, над
которой стоит плейхед. Причина — план сцены находил карточку по ПУТИ файла (`findIndex`
по `normInsPath`), то есть всегда по первому совпадению. В режиме `ae` поиск по `uid`
промахивался (у вставок шага 3 стоит `cid`), в режиме `clips` у вставки плана не было
ни `cid`, ни `uid`, и путь снова давал первую.

Теперь индекс вставки плана — это индекс в списке, который ушёл в запрос плана
(`ipvPlanBody`: `ae` — `INS`, `clips` — карточки клипа, оба с непустым `media`, в том же
порядке). Путь файла остаётся только сверкой, что план не устарел.

Гоняется НАСТОЯЩИЙ `ipvOverlayPlan` из `static/app/85-inserts-view.js` под node на том же
мини-DOM, что и `tests/test_ins_video_preview.py`. Копий логики в тесте нет.

Запуск: py -3.10 -m pytest tests/test_ins_same_file_highlight.py -q
"""
import shutil

import pytest

from tests.test_ins_video_preview import _DOM_JS, _fill_box, _js, _run_node

node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")

# Сценарий поверх мини-DOM: три видеовставки одного файла на 0–2, 2–4 и 4–6 с.
# Документ отдаёт карточки и блоки таймлайна, у которых `classList.toggle` запоминает классы.
# Для каждого момента окна (1, 3, 5 с) оверлей строится заново (IPV.cur сбрасывается).
_SCENARIO = r"""
var SEL={};
function MockEl(){var self=this;this.cls={};this.scrollIntoView=function(){};
  this.classList={toggle:function(c,on){self.cls[c]=!!on;},contains:function(c){return !!self.cls[c];}};}
document.querySelectorAll=function(sel){return SEL[sel]||[];};
function mkEls(n){var a=[];for(var i=0;i<n;i++)a.push(new MockEl());return a;}
function mkCard(uid,s){return {uid:uid,type:'video',media:'C:/v/clip.mp4',start_sec:s,duration_sec:2,
  sc:100,x:0,y:0,sin:0,mosaic:false,plate:false};}
function mkIns(cid,s){return {cid:cid,type:'video',media:'C:/v/clip.mp4',start_s:s,start_f:0,dur_s:2,dur_f:0,
  sc:100,x:0,y:0,sin:0,mosaic:false,plate:false};}
var FW=__FW__, FH=__FH__;
function run(mode){
  var cards=[mkCard('u0',0),mkCard('u1',2),mkCard('u2',4)];
  IPVMODE=mode;curAE=-1;curIns=0;
  if(mode==='ae'){INS=cards.map(function(c){return mkIns(c.uid,c.start_sec);});CLIPS=[{inserts:[]}];}
  else{INS=[];CLIPS=[{inserts:cards}];}
  IPV.plan=mkPlan([mkItem(100,{start:0,end:2,fitw:FW,fith:FH}),
                   mkItem(100,{start:2,end:4,fitw:FW,fith:FH}),
                   mkItem(100,{start:4,end:6,fitw:FW,fith:FH})]);
  var cardSel=(mode==='ae')?'#inslist .inscard':'#insHost .inscard';
  var blkSel='#itlblocks .itlblk:not(.intro):not(.roto)';
  var out={};
  [1,3,5].forEach(function(tm){
    SEL={};SEL[cardSel]=mkEls(3);SEL[blkSel]=mkEls(3);
    IPV.cur=-2;ipvOverlayPlan(tm);
    out[tm]={playing:SEL[cardSel].map(function(e){return !!e.cls.playing;}),
             cur:SEL[blkSel].map(function(e){return !!e.cls.cur;})};});
  return out;}
console.log(JSON.stringify({ae:run('ae'),clips:run('clips')}));
"""


def _scenario_code():
    fw, fh = _fill_box(1920, 1080)
    return _js() + _DOM_JS + _SCENARIO.replace("__FW__", repr(fw)).replace("__FH__", repr(fh))


@pytest.fixture(scope="module")
def highlight():
    return _run_node(_scenario_code())


# окно плейхеда → индекс вставки, которой оно принадлежит
_EXPECT = {"1": 0, "3": 1, "5": 2}


def _only(flags):
    return [i for i, on in enumerate(flags) if on]


@node
@pytest.mark.parametrize("mode", ["ae", "clips"])
def test_playing_card_is_the_one_under_the_playhead(highlight, mode):
    """Окно второй вставки → `.playing` только у второй карточки; то же для первой и третьей.

    Поиск по пути отдавал бы всегда карточку 0 — тест на это и краснеет.
    """
    for tm, want in _EXPECT.items():
        got = highlight[mode][tm]["playing"]
        assert _only(got) == [want], (
            f"режим {mode}, плейхед в {tm} с: подсвечена карточка {_only(got)}, а должна {want}")


@node
@pytest.mark.parametrize("mode", ["ae", "clips"])
def test_cur_block_is_the_one_under_the_playhead(highlight, mode):
    """То же для блока на таймлайне вставок (`.cur`): подсвечивается блок того же окна."""
    for tm, want in _EXPECT.items():
        got = highlight[mode][tm]["cur"]
        assert _only(got) == [want], (
            f"режим {mode}, плейхед в {tm} с: блок {_only(got)} в .cur, а должен {want}")
