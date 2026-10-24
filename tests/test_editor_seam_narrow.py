# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Видео-дублёр камеры на свежем клипе: дублёр заводится на файл ЭТОГО клипа.

Тесты про дублёр дорожки голоса (инвариант источника, мутации `vtSpareOf`/`vtSpareTake`)
сняты 2026-10-23 вместе с машиной `<audio>`: голос всех плееров играет очередь Web Audio
(`ea*`). Здесь осталось то, что живёт у камер: свежий клип пересоздаёт дублёров с его
src (`bufMake`), прежние буферы снимаются с `PV.bufs`, и на стык выходит дублёр именно
этого клипа, а не чужая камера.

Стенд гоняет боевые функции под node.

Запуск:  py -3.10 -m pytest tests/test_editor_seam_narrow.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
EDITOR_JS = ROOT / "static" / "app" / "70-editor.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Машина дублёров (60-preview.js) — тела из файла, копий в тесте нет.
PREVIEW_FUNCS = ("bufMake", "bufIdle", "bufSilent", "bufArm", "bufRoll", "bufTake", "bufSwap",
                 "liveOf", "spareLead", "segGap", "spareSwap",
                 "vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying",
                 "vtIsPv", "vtIsEd", "vtEa", "vtMuteHost", "vtPause", "vtTick")
# Константы порогов — тоже из файла: свои копии в стенде разъезжались бы с боевыми молча.
CONSTS = ("PV_PREROLL", "PV_SWAP_LO", "PV_SWAP_HI", "MEDIA_VOL")
# Из редактора — только поиск блока: стенд проверяет машину дублёра, а не стык редактора.
EDITOR_FUNCS = ("edBlockAt",)


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


def _const(src: str, name: str) -> str:
    """Объявление `const NAME=...;` (`MEDIA_VOL` объявлен через `let`) — из файла."""
    m = re.search(r"^(?:const|let) %s=.*$" % re.escape(name), src, re.M)
    assert m is not None, f"в 60-preview.js нет объявления {name}"
    return m.group(0)


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    editor = EDITOR_JS.read_text(encoding="utf-8")
    return "\n".join([_const(preview, c) for c in CONSTS]
                     + [_func_src(preview, n) for n in PREVIEW_FUNCS]
                     + [_func_src(editor, n) for n in EDITOR_FUNCS])




# Заглушки — только внешние двери (DOM, лог, тосты) и то, чем стенд меряет правила.
STUBS = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function t(s){return String(s);}
function $(id){return null;}
function vLoaded(){return Promise.resolve();}   // источник дублёра в стенде уже верный
function pvVideoTo(){}                          // перемотка кадра: у стенда её роль у edSeek
function edDraw(){}
function edUI(){}
function vtLiveOn(){return false;}              // окно плагинов закрыто: играет дорожка
function vtLiveUpdate(){return false;}
function vtLivePause(){}
// Флаг «звук камеры молчит» ставится хозяину дорожки: у шага 1 это объект кадра.
function vtMuteHost(P){return P;}
// Играет ли плеер: у редактора вердикт тот же, что в бою (vtPlaying смотрит на ED.play).
function vtPlaying(P){return !!P.play;}
const location={origin:''};
let ED=null,PV=null;
// Мини-<audio>/<video>: ровно те свойства, которыми живёт подмена ролями.
class El{
  constructor(tag){this.tag=tag;this.src='';this.volume=0;this.readyState=4;
    this.paused=true;this.seeking=false;this._ct=0;this.playbackRate=1;
    this.muted=false;this.plays=0;this.pauses=0;this.style={};this.seeks=[];this.handlers={};}
  get currentTime(){return this._ct;}
  // Присваивания currentTime видно тесту (`seeks`): перемотка живого на стыке — и есть
  // та задержка 100–200 мс, от которой уходит дублёр.
  set currentTime(v){this._ct=v;this.seeks.push(v);}
  play(){this.plays++;this.paused=false;return Promise.resolve();}
  pause(){this.pauses++;this.paused=true;}
  addEventListener(ev,fn){if(!this.handlers[ev])this.handlers[ev]=[];this.handlers[ev].push(fn);}
  removeEventListener(ev,fn){const list=this.handlers[ev]||[];
    const i=list.indexOf(fn);if(i>=0)list.splice(i,1);}
  fire(ev){const list=(this.handlers[ev]||[]).slice();list.forEach(fn=>fn({type:ev,target:this}));}
  seeked(){this.seeking=false;this.fire('seeked');}
}
// Таймер-страховка ожидания промаха (300 мс) собирается в очередь, а не ждёт по-настоящему.
const TIMERS=[];
function setTimeout(fn,ms){TIMERS.push({fn:fn,ms:ms});return TIMERS.length;}
function clearTimeout(id){if(id&&TIMERS[id-1])TIMERS[id-1].fn=null;}
const document={createElement:tag=>new El(tag)};
"""

# Стенд плеера с дорожкой голоса: камера и её дублёр — на PV, дорожка — на ED (время
# исходника у него лежит в `cs`). Машина дублёра голоса общая у плееров, имя тут не
# решает ничего: проверяется инвариант источника, а не стык редактора (его машины больше нет).
EDITOR_STAND = r"""
const cam=new El('video');cam.src='C:/cam1.mp4';cam._ct=0;
// Дублёр ведущей камеры уже стоит на первом стыке — как у играющего плеера.
const spareCam=new El('video');spareCam.src='C:/cam1.mp4';spareCam.readyState=4;spareCam.muted=true;
spareCam._ct=4-0.05;   // в допуске PV_SWAP_LO/HI к 4.0 — подмена видео обязана выйти
const live=new El('audio');live.src='/api/media?path=C:/cache/final.wav';live._ct=0;
PV={vids:[cam],bufs:[{el:spareCam,slot:0,off:0,at:0,rolling:false}],
  cams:[{path:'C:/cam1.mp4'}],segs:[],audio:[],words:[],dur:10,aidx:0,vidx:-1,
  curCi:-1,scrubbing:false,raf:0,xml:'C:/out/01_clip.xml',voicePanel:null};
ED={xml:'C:/out/01_clip.xml',blocks:[{s0:0,s1:4},{s0:4.15,s1:10}],fps:60,cam:'C:/cam1.mp4',dur:10,
  peaks:[],pps:80,sel:-1,play:true,raw:false,raf:0,cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],
  brBand:0,cams:[{path:'C:/cam1.mp4'}],voicePanel:null,
  vt:{on:true,el:live,path:'C:/cache/final.wav',fin:true,seq:0,timer:0,poll:0,want:'',note:'',
      vtq:false,vtend:false,live:null,dn:'',wantDn:'',wantFinal:true,warn:'',statesWait:null}};
const b0=ED.blocks[0],nb=ED.blocks[1],dt=1/60;
"""


def _run_node(tmp_path: Path, body: str, name: str = "seamnarrow.js") -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    script = STUBS + "\n" + _bodies() + "\n" + body
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# камера: свежий клип — свежий дублёр на его файле
# --------------------------------------------------------------------------- #
def test_a_fresh_clip_rebuilds_the_camera_spare_on_its_own_source(tmp_path: Path) -> None:
    """Открыли клип 2 — видео-дублёр заводится на ЕГО файл, а не остаётся с прежним.

    У камер инвариант держат свои двери: буферы пересоздаёт `bufMake` при открытии клипа
    (прежние снимаются с `PV.bufs`), а разошедшийся источник правит `sparePrime`. Здесь
    проверяется, что свежий клип действительно даёт дублёра на своём файле и что чужая
    камера на стык не выходит: подмена работает только по своему источнику.
    """
    res = _run_node(tmp_path, EDITOR_STAND + """
const oldSpare=spareLead(PV);                  // дублёр, что был у прежнего клипа
oldSpare.el.src='C:/cam_old_clip1.mp4';
// Открыли клип 2: живая камера и её дублёр — новые элементы на новом файле (bufMake).
PV.vids[0].src='C:/cam2.mp4';
PV.bufs=[];
const stage={insertBefore(){}};                // DOM в стенде не нужен: bufMake только вставляет узел
const oldLive=PV.vids[0];
const b=bufMake(PV,stage,null,0,0);
const newSpare=b.el;                           // свежий дублёр клипа: он и должен выйти в эфир
const made=b.el.src;
b.at=nb.s0;b.el._ct=nb.s0;b.rolling=true;
const took=bufTake(PV,b,nb.s0);
console.log(JSON.stringify({made:made,liveSrc:PV.vids[0].src,oldSrc:oldSpare.el.src,
  took:took,liveIsSpare:(PV.vids[0]!==oldLive),liveIsNewSpare:(PV.vids[0]===newSpare),
  oldSpareOut:(spareLead(PV)===oldSpare)}));
""")

    # Дублёр заведён на файл НОВОГО клипа: у видеоряда нет «чужого» источника на стыке.
    assert res["made"] == "C:/cam2.mp4" and res["liveSrc"] == "C:/cam2.mp4", res
    assert res["oldSrc"] == "C:/cam_old_clip1.mp4", res
    # Список буферов начат заново — прежний дублёр в нём не остался, стал бы он живым.
    assert res["oldSpareOut"] is False, \
        "дублёр прежнего клипа пережил открытие нового: %s" % res
    assert res["took"] is True and res["liveIsSpare"] is True, \
        "дублёр нового клипа не вышел на стык: %s" % res
    # Обмен ролями: в эфире именно свежий дублёр клипа, а прежний живой ушёл в дублёры.
    assert res["liveIsNewSpare"] is True, \
        "на стык вышел не свежий дублёр клипа: %s" % res
    assert res["liveIsSpare"] is True, res


