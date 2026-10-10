# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дублёр дорожки голоса: подмена в окне стыка и инвариант источника (превью шага 3).

Первый баг этого файла (голос редактора шага 1 подменялся раньше прыжка картинки через
узкий вырез) ушёл вместе с машиной: шаг 1 больше не играет голос элементом <audio>, звук
там — буфер Web Audio с очередью блоков (60-preview.js, блок `ea*`;
tests/test_editor_audio_clock.py). Здесь осталось то, что живёт у превью шага 3:

1. плеер по EDL: прицел в пределах окна `VT_SWAP_HI` — подмена в том же кадре.

Второй баг того же узла (воспроизведён в браузере на копиях двух клипов владельца):
дублёр дорожки голоса оставался от ПРЕДЫДУЩЕГО клипа. Открыли клип 1 (трек A), поиграли,
открыли клип 2 (трек B) — у живого `<audio>` источник B, а у дублёра A: `vtSpareOf`
возвращал `st.vsp` без сверки источника. Плеер разгонял этот дублёр под стыки нового
клипа, и на подмене в эфир выходил голос первого ролика.

Решение — инвариант: дублёр играет ТОТ ЖЕ файл, что живая дорожка.
`vtSpareOf` при расхождении источников забывает разбег (та же дверь `vtSpareIdle`, что и
у смены источника) и создаёт дублёра с новым src, а `vtSpareTake` отказывает в подмене,
если источники разошлись между взводом и стыком. У видео-дублёров камер тот же инвариант
обеспечен их собственными дверями, и проверяется здесь, что он на месте: свежий клип
пересоздаёт дублёров с его src (`bufMake`), а прежний источник правит `sparePrime`.

Стенд гоняет боевые функции под node, поэтому проверки продолжаются:

2. живой трек сменил src — `vtSpareOf` отдаёт дублёра с НОВЫМ src и `armed=false`;
3. дублёр от прежнего трека — `vtSpareTake` = false и живого не трогает (он прежний);
4. то же у видео-дублёра камеры: свежий клип даёт свежий источник, подмена не приносит
   на стык кадр чужого клипа;
5. мутации: вернуть `if(st.vsp)return st.vsp;` без сверки и убрать сверку из
   `vtSpareTake` — тесты 2 и 3 обязаны покраснеть.

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

# Машина дублёра дорожки голоса (60-preview.js) — тела из файла, копий в тесте нет.
PREVIEW_FUNCS = ("bufMake", "bufIdle", "bufSilent", "bufArm", "bufRoll", "bufTake", "bufSwap",
                 "liveOf", "spareLead", "segGap", "spareSwap", "voicePrime",
                 "vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying", "vtGate",
                 "vtIsPv", "vtIsEd", "vtMuteHost",
                 "vtSpareOf", "vtSpareLive", "vtSpareIdle", "vtSpareStop", "vtSpareSeg",
                 "vtSpareAt", "vtSpareArm", "vtSpareRoll", "vtSpareTake", "vtSpareSwap",
                 "vtSpareCtl", "vtPause", "vtSeek", "vtRate", "vtTick")
# Константы порогов — тоже из файла: свои копии в стенде разъезжались бы с боевыми молча.
CONSTS = ("PV_PREROLL", "PV_SWAP_LO", "PV_SWAP_HI", "VT_SWAP_LO", "VT_SWAP_HI",
          "VT_SOFT", "VT_DRIFT", "VT_RATE", "MEDIA_VOL")
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


# Боевые тела двух дверей инварианта — из файла, а не копией в тесте: мутации (а) и (б)
# снимают в них ровно ту сверку источника, которую сторожат тесты 5, и ничего больше.
def _spare_source(name: str) -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    return _func_src(preview, name)


def _no_source_sync(body: str, marker: str) -> str:
    """Тело функции без блока, начинающегося со строки `marker` (сверка источника).

    Блок убираем ЦЕЛИКОМ и по фигурным скобкам, а не регуляркой: сверка живёт в одну
    строку, а её снятие обязано оставить тело синтаксически целым.
    """
    out: list[str] = []
    skip = 0
    for line in body.splitlines():
        if skip > 0:
            skip += line.count("{") - line.count("}")
            continue
        if line.strip().startswith(marker):
            skip = line.count("{") - line.count("}")
            continue
        out.append(line)
    assert len(out) < len(body.splitlines()), \
        "не нашлась сверка источника (%r) — мутация не собралась" % marker
    return "\n".join(out)


# Заглушки — только внешние двери (DOM, лог, тосты) и то, чем стенд меряет правила.
STUBS = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function t(s){return String(s);}
function $(id){return null;}
function voiceWiring(){}
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
spareCam._ct=4-0.05;   // в допуске VT_SWAP_LO/HI к 4.0 — подмена видео обязана выйти
const live=new El('audio');live.src='/api/media?path=C:/cache/final.wav';live._ct=0;
PV={vids:[cam],bufs:[{el:spareCam,slot:0,off:0,at:0,rolling:false}],
  cams:[{path:'C:/cam1.mp4'}],segs:[],audio:[],words:[],dur:10,aidx:0,vidx:-1,
  curCi:-1,scrubbing:false,raf:0,xml:'C:/out/01_clip.xml',voicePanel:null};
ED={xml:'C:/out/01_clip.xml',blocks:[{s0:0,s1:4},{s0:4.15,s1:10}],fps:60,cam:'C:/cam1.mp4',dur:10,
  peaks:[],pps:80,sel:-1,play:true,raw:false,raf:0,cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],
  brBand:0,cams:[{path:'C:/cam1.mp4'}],voicePanel:null,
  vt:{on:true,el:live,path:'C:/cache/final.wav',fin:true,seq:0,timer:0,poll:0,want:'',note:'',
      vtq:false,vtend:false,live:null,dn:'',wantDn:'',wantFinal:true,warn:'',statesWait:null,
      vsp:null,vspAt:null}};
const b0=ED.blocks[0],nb=ED.blocks[1],dt=1/60;
// Дублёр дорожки голоса заводим сразу: у стенда дорожка уже играет (st.on и st.el
// заполнены), а без разбега vsp остаётся null — `vtOf` достраивает состояние только
// целиком, у готового плеера его не трогает. В бою это делает первый же кадр с прицелом.
vtSpareAt(ED,b0.s1);vtSpareArm(ED);
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
# 1. плеер шага 3: прицел и есть точка стыка — подмена в окне
# --------------------------------------------------------------------------- #
def test_the_edl_player_still_swaps_inside_the_window(tmp_path: Path) -> None:
    """Плеер по EDL (не редактор): прицел в пределах окна — подмена в том же кадре.

    У плеера шага 3 прицел дорожки голоса — сегмент EDL, и он же точка стыка: лишнего
    зазора между «прицелом» и «прыжком» нет. Поэтому здесь ранняя подмена обязана
    остаться — иначе стык пойдёт перемоткой живого, то есть той же задержкой 100–200 мс,
    от которой уходим.
    """
    res = _run_node(tmp_path, """
const v=new El('video');v._ct=4;
const live=new El('audio');live.src='/api/media?path=C:/cache/final.wav';live._ct=4;
const P={xml:'C:/out/01_clip.xml',cams:[{path:'C:/cam1.mp4'}],audioCi:0,playing:false,
  scrubbing:false,audio:[{ts:0,te:4,src:0},{ts:4,te:10,src:6}],aidx:0,vids:[v],
  voicePanel:null,cs:0,play:false,vt:{on:true,el:live,path:'C:/cache/final.wav',fin:true,
    seq:0,timer:0,poll:0,want:'',note:'',vtq:false,vtend:false,live:null,dn:'',wantDn:'',
    wantFinal:true,warn:'',statesWait:null,vsp:null,vspAt:null}};
const seg=vtSpareSeg(P);                    // прицел из EDL: кусок ПОСЛЕ стыка
vtSpareAt(P,seg.at);
const spare=vtSpareOf(P);
vtSpareArm(P);                              // разбег взведён
spare.el._ct=seg.at;spare.rolling=true;     // ...и отыгран: дублёр стоит на стыке
vtSpareCtl(P,seg.at-0.1,seg.at-0.1);        // кадр: прицел в пределах VT_SWAP_HI
console.log(JSON.stringify({isEd:vtIsEd(P),seg:seg.at,swapped:(spare.el===live),
  liveCt:P.vt.el.currentTime,seeks:live.seeks.length,cut:seg.at}));
""")

    assert res["isEd"] is False, "стенд подсунул редактор вместо плеера шага 3: %s" % res
    assert res["cut"] == 6, "прицел взят не из сегмента EDL: %s" % res
    # Ранняя подмена на месте: у этого плеера прицел и есть точка стыка. Роли
    # поменялись — в эфире дублёр, а прежний живой ушёл в дублёры (без перемотки).
    assert res["swapped"] is True, \
        "плеер по EDL потерял подмену в окне прицела — стык пойдёт перемоткой: %s" % res
    assert abs(res["liveCt"] - res["cut"]) < 1e-9 and res["seeks"] == 0, res


# --------------------------------------------------------------------------- #
# 2. инвариант источника: дублёр всегда играет файл ЖИВОЙ дорожки
# --------------------------------------------------------------------------- #
def test_the_voice_spare_takes_the_new_track_when_the_live_source_changes(
        tmp_path: Path) -> None:
    """Живая дорожка сменила файл — дублёр обязан переехать на НОВЫЙ, а не играть старый.

    Баг владельца (копии двух клипов, спикер с VST, редактор шага 1): в клипе 2 живой
    `<audio>` держал трек клипа 2 (`1ca7f1ad….wav`), а дублёр — трек клипа 1
    (`fd075436….wav`), потому что `vtSpareOf` возвращал уже созданный `st.vsp` без сверки
    источника. Плеер разгонял этот дублёр под стыки нового клипа, и на подмене живым
    голосом играл первый ролик. Стенд повторяет ровно эту смену: у живого новое имя
    трека, у дублёра — прежнее. `vtSpareOf` обязан отдать дублёра с НОВЫМ src и
    `armed=false`: разбег прежнего трека к новому стыку не относится.
    """
    res = _run_node(tmp_path, EDITOR_STAND + """
const spare=vtSpareOf(ED);                    // дублёр клипа 2 заведён, как в бою
spare.at=nb.s0;spare.el._ct=nb.s0;spare.rolling=true;   // он взведён на стык нового клипа
spare.el.src='/api/media?path=C:/cache/old_clip1.wav';   // ...но играет ТРЕК ПРЕЖНЕГО клипа
const armedBefore={armed:!!spare.armed,at:spare.at};
let stale=vtOf(ED).vsp;
vtOf(ED).el.src='/api/media?path=C:/cache/new_clip2.wav'; // открыли клип 2: у живого новый трек
const b=vtSpareOf(ED);
console.log(JSON.stringify({same:vtOf(ED).vsp===stale,src:b.el.src,live:vtOf(ED).el.src,
  armed:!!b.armed,at:b.at,armedBefore:armedBefore,voice:b.el.src===vtOf(ED).el.src,
  liveEl:vtOf(ED).el.src,old:'/api/media?path=C:/cache/old_clip1.wav',
  newSrc:'/api/media?path=C:/cache/new_clip2.wav'}));
""")

    assert res["same"] is True, \
        "дублёра пересоздали, а не перевели на новый трек: %s" % res
    # Аварийная готовность была: дублёр стоял взведённым ровно на стык, и без сброса он
    # вышел бы в эфир на первом же стыке нового клипа.
    assert res["armedBefore"]["armed"] is True and res["armedBefore"]["at"] == 4.15, res
    assert res["src"] == res["newSrc"], \
        "дублёр остался на треке прежнего клипа: %s" % res
    assert res["liveEl"] == res["newSrc"] and res["voice"] is True, res
    assert res["src"] != res["old"], res
    # Разбег забыт: прежний `armed` удержал бы взвод, и чужой трек остался бы готовым к стыку.
    assert res["armed"] is False and res["at"] is None, \
        "взведённый разбег прежнего трека пережил смену источника: %s" % res


def test_the_spare_from_the_previous_clip_cannot_come_back_on_the_seam(
        tmp_path: Path) -> None:
    """Дублёр от прежнего трека уже разогнан — подмена на стыке обязана отказать.

    Вторая защита того же инварианта: между взводом и стыком живой трек мог смениться, и
    `vtSpareTake` выпустил бы в эфир голос прежнего клипа. Дублёр камеры здесь идёт следом
    за новым клипом (как его и переводит `sparePrime`), а вот у голоса источник прежний —
    и его сверка обязана отказать. Отказ — это запасной путь (перемотка живого), а не
    подмена чужим файлом; взведённый разбег снимается, чтобы чужой дублёр не вернулся на
    следующем кадре.
    """
    res = _run_node(tmp_path, EDITOR_STAND + """
const spare=vtOf(ED).vsp;                     // дублёр голоса, что уже стоит на стыке
spare.el._ct=nb.s0;spare.rolling=true;
vtOf(ED).vsp.el.src='/api/media?path=C:/cache/old_clip1.wav';   // но играет трек прежнего клипа
const camSpare=spareLead(PV);                 // видео-дублёр за клипом следит — он на новом клипе
camSpare.el.src=PV.vids[0].src;
camSpare.at=nb.s0;camSpare.rolling=true;camSpare.el._ct=nb.s0;
vtOf(ED).el.src='/api/media?path=C:/cache/new_clip2.wav';
const liveEl=vtOf(ED).el;
const took=vtSpareTake(ED,nb.s0);
console.log(JSON.stringify({took:took,liveIsLive:vtOf(ED).el===liveEl,
  sameCt:vtOf(ED).el.currentTime===liveEl.currentTime,liveSrc:vtOf(ED).el.src,
  spareSrc:vtOf(ED).vsp.el.src,armed:!!vtOf(ED).vsp.armed,
  camSrc:camSpare.el.src,liveCamSrc:PV.vids[0].src}));
""")

    assert res["took"] is False, \
        "на стык вышел голос ПРЕЖНЕГО клипа — подмена не сверила источник: %s" % res
    assert res["liveIsLive"] is True and res["sameCt"] is True, \
        "отказавшая подмена всё-таки сменила живой элемент: %s" % res
    assert res["liveSrc"] == "/api/media?path=C:/cache/new_clip2.wav", res
    assert res["spareSrc"] != res["liveSrc"], res
    # Сверка голоса — ЕДИНСТВЕННАЯ причина отказа: дублёр камеры на стык готов.
    assert res["camSrc"] == res["liveCamSrc"], res
    assert res["armed"] is False, \
        "чужой дублёр остался взведён — он вернётся на следующем кадре: %s" % res


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


# --------------------------------------------------------------------------- #
# 6. мутации: сверку источника убрать — тесты 5 обязаны покраснеть
# --------------------------------------------------------------------------- #
def test_the_spare_source_mutation_turns_the_new_track_test_red(tmp_path: Path) -> None:
    """Мутация (а): `if(st.vsp)return st.vsp;` без сверки — тест нового трека красный.

    Возвращаем ровно прежний код: созданный дублёр отдаётся как есть. Тогда на смене
    трека `vtSpareOf` вернёт дублёра ПРЕЖНЕГО клипа, и он же пойдёт в эфир на стыке —
    то самое «во втором клипе звучит голос первого».
    """
    src = _spare_source("vtSpareOf")
    mutant = _no_source_sync(src, "if(st.vsp&&st.vsp.el.src!==st.el.src)")
    assert "vtSpareIdle(P)" not in mutant, \
        "мутация (а) не сняла сверку источника в vtSpareOf: %s" % mutant
    res = _run_node(tmp_path, EDITOR_STAND + "const HUNA=%s;" % json.dumps(mutant)
                    + """
eval('vtSpareOf='+HUNA);                      // прежний код: сверки источника нет
const spare=vtSpareOf(ED);
spare.el.src='/api/media?path=C:/cache/old_clip1.wav';
vtOf(ED).el.src='/api/media?path=C:/cache/new_clip2.wav';
const b=vtSpareOf(ED);
console.log(JSON.stringify({src:b.el.src,voice:b.el.src===vtOf(ED).el.src,
  old:'/api/media?path=C:/cache/old_clip1.wav'}));
""")

    # Живой трек — новый, а дублёр вернулся прежним: мутация воспроизводит баг владельца.
    assert res["src"] == res["old"] and res["voice"] is False, \
        "мутация не оставила дублёра на треке прежнего клипа — сторож проверяет не то: %s" % res


def test_the_take_source_mutation_turns_the_previous_clip_test_red(tmp_path: Path) -> None:
    """Мутация (б): убрать сверку в `vtSpareTake` — тест «дублёр прежнего клипа» красный.

    Стенд повторяет свой же стык смены клипа: дублёр голоса стоит на стыке готовым, но с
    прежним треком, а у живого уже новый. Со сверкой подмена отказывает; без неё она идёт
    по одной готовности разбега, и в эфир выходит голос прежнего клипа.
    """
    src = _spare_source("vtSpareTake")
    mutant = _no_source_sync(src, "if(b.el.src!==st.el.src)")
    assert "vtSpareIdle(P);return false" not in mutant, \
        "мутация (б) не сняла сверку источника в vtSpareTake: %s" % mutant
    res = _run_node(tmp_path, EDITOR_STAND + "const HUNB=%s;" % json.dumps(mutant)
                    + """
eval('vtSpareTake='+HUNB);                    // прежний код: сверки источника нет
const spare=vtOf(ED).vsp;
// Дублёр взведён и стоит ровно на стыке: без сверки источника подмене больше ничего не
// мешает (bufTake сверяет `at` с позицией дублёра, поэтому взводим его на точку стыка).
spare.at=nb.s0;spare.el._ct=nb.s0;spare.rolling=true;
vtOf(ED).el.src='/api/media?path=C:/cache/old_clip1.wav';   // у живого ещё трек прежнего клипа
const camSpare=spareLead(PV);                 // дублёр камеры за живым — он тоже готов
camSpare.el.src=PV.vids[0].src;
camSpare.at=nb.s0;camSpare.rolling=true;camSpare.el._ct=nb.s0;
const liveEl=vtOf(ED).el;
const took=vtSpareTake(ED,nb.s0);
console.log(JSON.stringify({took:took,liveIsSpare:(vtOf(ED).el!==liveEl),
  liveSrc:vtOf(ED).el.src,old:'/api/media?path=C:/cache/old_clip1.wav'}));
""")

    assert res["took"] is True and res["liveIsSpare"] is True, \
        "мутация не выпустила чужой трек в эфир — сторож проверяет не то: %s" % res
