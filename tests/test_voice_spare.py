# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Дорожка голоса проходит стык монтажа дублёром, а не перемоткой живого <audio>.

Видео стык проходит дублёром давно (bufArm/bufRoll/bufTake), а дорожка обработанного
голоса прыгала на стыке перемоткой — и <audio> после seek начинает играть с задержкой
100–200 мс. Замер владельца: на шаге 1 (редактор, прыжки через вырезанные куски) p50
13 мс, но p95 120 мс и max 194 мс, и все девять точек с отставанием больше 125 мс стоят
ровно после перемоток <audio> на стыках. Порог заметности — звук отстаёт > 125 мс.

Лечение то же, что у видео: второй <audio> на том же файле заранее уводится на позицию
по́сле стыка и пускается немым, на стыке элементы меняются ролями. Машина НЕ вторая:
`vsp` — такой же объект дублёра, как `b` в `P.bufs`, и живёт он на тех же bufArm/
bufRoll/bufTake/bufSwap. Своё у него ровно то, чем <audio> отличается от <video>:
допуск позиции на стыке (VT_SWAP_LO/HI), прицел и способ подмены (местами меняются
два <audio>, а не слот в P.vids). Прицел у редактора — блоки правки, у остальных
плееров — сегмент EDL: время исходника из EDL не выводится.

Стенд гоняет БОЕВЫЕ функции под node с фейковыми <audio>/<video> и без часов (образец —
tests/test_voice_preview_mode.py). Проверяется:

(а) за PV_PREROLL до стыка дублёр получает currentTime = позиция ПОСЛЕ стыка и
    играет немым;
(б) на стыке живой становится тем, что был дублёром, без присваивания currentTime
    живому;
(в) дублёр не готов (readyState/позиция) — запасной путь: перемотка живого;
(г) пауза и скраб — дублёр не готовится.

Шаг 1 (редактор) этой машиной больше не пользуется: звук там играет буфер Web Audio, и
стыки идут очередью узлов с точностью до сэмпла (tests/test_editor_audio_clock.py). Машина
осталась у превью шага 3; плеер стенда по-прежнему зовётся `ED` — от имени тут зависит
только то, откуда берётся время исходника (`P.cs`), правила дублёра у плееров общие.

Запуск:  py -3.10 -m pytest tests/test_voice_spare.py -q
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

# Машина дублёра (60-preview.js) — тела берутся из файла, копий в тесте нет.
SPARE_FUNCS = ("bufSilent", "bufIdle", "liveOf", "bufArm", "bufRoll", "bufTake", "bufSwap",
               "segGap", "spareLead", "spareSwap",
               "vtOf", "vtCam1", "vtSrcAt", "vtNow", "vtAudioCam", "vtPlaying", "vtGate",
               "vtSpareOf", "vtSpareLive", "vtSpareIdle", "vtSpareStop", "vtSpareSeg",
               "vtSpareAt", "vtSpareArm", "vtSpareRoll", "vtSpareTake", "vtSpareSwap",
               "voicePrime", "vtSpareCtl", "vtIsEd",
               "vtPause", "vtSeek", "vtRate", "vtTick")
# Константы порогов — тоже из файла: свои копии в стенде разъезжались бы с боевыми молча.
CONSTS = ("PV_PREROLL", "PV_SWAP_LO", "PV_SWAP_HI", "VT_SWAP_LO", "VT_SWAP_HI",
          "VT_SOFT", "VT_DRIFT", "VT_RATE", "MEDIA_VOL")
# Из редактора — только поиск блока. Своей дорожки голоса у шага 1 больше нет: звук там
# играет буфер Web Audio (60-preview.js, блок `ea*`; tests/test_editor_audio_clock.py), а
# машина дублёра голоса осталась у превью шага 3 — её этот стенд и проверяет.
EDITOR_FUNCS = ("edBlockAt",)


def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки.

    `async` забираем вместе с функцией: без него `await` внутри тела — синтаксическая
    ошибка, и стенд падал бы «missing ) after argument list» вместо проверки.
    """
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
                     + [_func_src(preview, n) for n in SPARE_FUNCS]
                     + [_func_src(editor, n) for n in EDITOR_FUNCS])


# Заглушки — только внешние двери (DOM, лог, тосты) и то, чем стенд меряет правила.
STUBS = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+String(m));}
function t(s){return String(s);}
function $(id){return null;}
function voiceWiring(){}
function vLoaded(){return Promise.resolve();}   // источник дублёра в стенде уже верный
function vtLiveOn(){return false;}              // окно плагинов закрыто: играет дорожка
function vtLiveUpdate(){return false;}
function vtLivePause(){}
// Играет ли плеер: у стенда он один — редактор, и вердикт у него тот же, что в бою
// (vtPlaying в 60-preview.js смотрит на ED.play).
function vtPlaying(P){return !!P.play;}
// Кому ставится флаг «звук камеры молчит». В бою это хозяин дорожки: у шага 1 — редактор
// (его `st.on` и решает, наш ли трек), а `vids` у него — тот же живой список камер, что на
// объекте кадра: подмена на стыке меняет элемент ИМЕННО в нём. Плеер без `vids` (или без
// дорожки: `vtOf(P).on`) оставил бы гейт молча открытым — на этом стенд и попался.
function vtMuteHost(P){return P;}// Источник сверяется так же, как в бою: у абсолютного URL отрезается origin.
const location={origin:''};
let ED=null;
// Плеер кадра шага 1 (PV) держим ОТДЕЛЬНЫМ от редактора (ED): дорожка голоса живёт на ED,
// видео — на PV, и на одном объекте промах «взвели не тому дублёру» было бы не поймать.
let PV=null;
function pvVideoTo(){}         // перемотка кадра: в стенде редактора её роль играет edSeek
function edDraw(){}
function edUI(){}
// Дублёр дорожки голоса живёт на <audio>, у которого нет ни слоёв, ни z-index:
// ровно те свойства, которыми живёт подмена.
class El{
  constructor(tag){this.tag=tag;this.src='';this.volume=0;this.readyState=4;
    this.paused=true;this.seeking=false;this._ct=0;this.playbackRate=1;
    this.muted=false;this.plays=0;this.pauses=0;this.style={};this.seeks=[];this.handlers={};}
  get currentTime(){return this._ct;}
  // Присваивания currentTime видно тесту (`seeks`). `seeking` стенд поднимает сам, где
  // проверяется ожидание промаха: в бою его поднимает живой декодер, и от этого зависит
  // «ждать `seeked` или картинка уже на месте».
  set currentTime(v){this._ct=v;this.seeks.push(v);}
  play(){this.plays++;this.paused=false;return Promise.resolve();}
  pause(){this.pauses++;this.paused=true;}
  addEventListener(ev,fn){if(!this.handlers[ev])this.handlers[ev]=[];this.handlers[ev].push(fn);}
  removeEventListener(ev,fn){const list=this.handlers[ev]||[];
    const i=list.indexOf(fn);if(i>=0)list.splice(i,1);}
  fire(ev){const list=(this.handlers[ev]||[]).slice();
    list.forEach(fn=>fn({type:ev,target:this}));}
  // Конец перемотки: живое видео доехало до цели.
  seeked(){this.seeking=false;this.fire('seeked');}
}
// Таймер-страховка ожидания (300 мс, 70-editor.js) собирается в очередь, а не ждёт
// по-настоящему: стенд обязан уметь и «событие потерялось», и мгновенный выход.
const TIMERS=[];
function setTimeout(fn,ms){TIMERS.push({fn:fn,ms:ms});return TIMERS.length;}
function clearTimeout(id){if(id&&TIMERS[id-1])TIMERS[id-1].fn=null;}
const document={createElement:tag=>new El(tag)};
// Очередь микрозадач: `spareVoicePrime` — async (переезд дублёра на свежий источник).
const tick=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
// Плеер шага 1: сегменты EDL с вырезанным куском между 4.0 и 6.0 — ровно тот стык,
// который редактор проходит прыжком, а дорожка голоса до сих пор проходила перемоткой.
function P_(){
  const v=new El('video');v._ct=0;
  return {xml:'C:/out/01_clip.xml',cams:[{path:'C:/cam1.mp4'}],audioCi:0,playing:false,
    scrubbing:false,audio:[{ts:0,te:4,src:0},{ts:4,te:10,src:6}],aidx:0,vids:[v],
    voicePanel:null,cs:0,play:false,vt:null};}
"""


def _run_node(tmp_path: Path, body: str, name: str = "vspare.js") -> Any:
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


# Готовый трек уже играет: дорожка «наша» (st.on), путь есть, источник дублёра тот же.
TRACK = ("{on:true,el:__LIVE__,path:'C:/cache/final.wav',fin:true,seq:0,timer:0,poll:0,"
         "want:'',note:'',vtq:false,vtend:false,live:null,dn:'',wantDn:'',"
         "wantFinal:true,warn:'',statesWait:null,vsp:null,vspAt:null}")


def _player(live_ct: float, live_ready: int = 4) -> str:
    """Плеер с живой дорожкой голоса, стоящей на `live_ct`.

    Позиция живой — ЕГО currentTime: у стенда играет редактор, и время исходника лежит
    на плеере (vtNow = P.cs), а не выводится из currentTime <video>.
    """
    track = TRACK.replace("__LIVE__", "vox")   # состояние дорожки — с ЖИВЫМ элементом
    return f"""
const P=P_();ED=P;
P.cs={live_ct};
const vox=new El('audio');vox.src='/api/media?path=C:/cache/final.wav';
vox._ct={live_ct};vox.readyState={live_ready};vox.paused=true;
P.vt={track};
"""


# --------------------------------------------------------------------------- #
# (а) за PV_PREROLL до стыка дублёр стоит на позиции ПОСЛЕ стыка и играет немым
# --------------------------------------------------------------------------- #
def test_spare_is_armed_at_the_position_after_the_cut(tmp_path: Path) -> None:
    """За PV_PREROLL до стыка дублёр уводится на позицию по́сле стыка.

    Прицел у редактора — начало следующего блока правки (время ИСХОДНИКА: из EDL его
    не вывести). Дублёр встаёт на это время и вживую ещё не пускается: до стыка далеко,
    а разбег обязан быть коротким — он гоняет тот же файл голоса вторым декодером.
    """
    res = _run_node(tmp_path, _player(3.0) + """
vtSpareAt(P,6);            // стык: блок правки кончился, следующий начинается на 6.0
vtSpareArm(P);             // за VT_ARM до стыка разбег взводится
const b=P.vt.vsp;
console.log(JSON.stringify({
  made:!!b, ct:b?b.el.currentTime:null, at:b?b.at:null, rolling:!!(b&&b.rolling),
  muted:b?b.el.muted:null, plays:b?b.el.plays:0, src:b?b.el.src:'', liveSrc:P.vt.el.src,
  liveCt:P.vt.el.currentTime, preroll:PV_PREROLL}));
""")

    assert res["made"] is True, "дублёра дорожки голоса нет вовсе"
    # Дублёр встаёт на РАЗБЕГ: PV_PREROLL до позиции после стыка. Именно оттуда он её и
    # отыграет — пускать его сразу «на стык» значило бы пускать с середины.
    assert res["at"] == 6, "дублёр взведён не на позицию после стыка: %s" % res
    assert res["ct"] == 6 - res["preroll"], \
        "дублёр стоит не за PV_PREROLL до стыка: %s" % res
    # Сам источник — тот же, что у живого трека: иначе на стыке зазвучал бы не тот голос.
    assert res["src"] == res["liveSrc"], \
        "дублёр играет не тот же трек: %s" % res
    assert res["rolling"] is False, "дублёр пущен вживую задолго до стыка"
    assert res["plays"] == 0, res
    assert res["muted"] is True, \
        "дублёр не заглушён: его будет слышно поверх живого голоса"
    # Живой тронуть нечем: взвод разбега — это только второй элемент.
    assert res["liveCt"] == 3.0, res


def test_spare_rolls_muted_and_catches_up_by_rate(tmp_path: Path) -> None:
    """В окне PV_PREROLL дублёр едет вживую, немой, и догоняет стык СКОРОСТЬЮ.

    Скорость, а не «как получится»: тик, на котором решаем пускать, сам приходит с
    опозданием (в фоновой вкладке rAF молчит и остаётся страховочный интервал 120 мс),
    и пущенный «как есть» дублёр не добегает — подмена срывается и стык снова идёт
    перемоткой живого.
    """
    res = _run_node(tmp_path, _player(5.7) + """
vtSpareAt(P,6);            // стык — на 6.0: от живой позиции до него 0.3 с
vtSpareArm(P);             // разбег взведён: дублёр стоит за PV_PREROLL до стыка
vtSpareRoll(P,5.7);        // 0.3 с до стыка — окно разбега открыто, дублёр пускается
const b=P.vt.vsp;
console.log(JSON.stringify({
  ct:b.el.currentTime, rate:b.el.playbackRate, rolling:!!b.rolling,
  plays:b.el.plays, muted:b.el.muted, startsAt:b.at-PV_PREROLL}));
""")

    assert res["rolling"] is True and res["plays"] == 1, \
        "дублёр не пущен вживую в окне разбега: %s" % res
    assert res["muted"] is True, "дублёр поехал со звуком"
    assert res["rate"] > 1.0, \
        "дублёр не догоняет стык скоростью: %s" % res
    # Пущен он с РАЗБЕГА, а не брошен на стык: перепуск с середины слышен как проглоченное
    # слово. Часы в стенде стоят, поэтому позиция та же, что была при взводе.
    assert res["ct"] == res["startsAt"], \
        "дублёра пустили не с разбега: %s" % res


# --------------------------------------------------------------------------- #
# (б) на стыке живой становится тем, что был дублёром — без seek живого
# --------------------------------------------------------------------------- #
def test_at_the_cut_the_spare_becomes_the_live_track(tmp_path: Path) -> None:
    """На стыке элементы меняются ролями, и живого НЕ сеcит никто.

    Это и есть весь смысл: перемотки живого <audio> на стыке нет, а значит нет и
    задержки 100–200 мс, с которой он начинает играть после seek. Дублёр уже стоит на
    позиции по́сле стыка — он и выходит в эфир, немой становится прежний живой.
    """
    res = _run_node(tmp_path, _player(4.0) + """
const el=P.vt.el;
vtSpareAt(P,6);
vtSpareArm(P);
const spare=P.vt.vsp;
spare.el._ct=6;            // дублёр уже отыграл разбег и стоит на стыке
spare.rolling=true;
const took=vtSpareTake(P,6);
console.log(JSON.stringify({
  took:took, liveSrc:P.vt.el.src, liveCt:P.vt.el.currentTime,
  livePlays:P.vt.el.plays, liveMuted:P.vt.el.muted,
  oldCt:el.currentTime, oldMuted:el.muted, oldPaused:el.paused,
  spareIsOld:P.vt.vsp.el===el,
  armed:!!P.vt.vsp.armed, at:P.vt.vsp.at}));
""")

    assert res["took"] is True, "подмена дублёром не состоялась: %s" % res
    # Вышел в эфир ИМЕННО дублёр: живой теперь — тот элемент, что был дублёром.
    assert res["liveSrc"] == "/api/media?path=C:/cache/final.wav", res
    assert res["liveCt"] == 6, "вышедший в эфир дублёр стоит не на стыке: %s" % res
    # Это главное: живого не перематывали — перемотка и есть та задержка, от которой уходим.
    assert res["oldCt"] == 4.0, \
        "живой <audio> всё-таки перемотали на стыке: %s" % res
    assert res["oldMuted"] is True and res["oldPaused"] is True, \
        "прежний живой остался звучать: %s" % res
    assert res["spareIsOld"] is True, \
        "дублёр не поменялся местами с живым — обмен ролями не состоялся: %s" % res
    assert res["armed"] is False and res["at"] is None, \
        "подмена не забыла разбег: следующий стык взведётся на прежнюю цель"


# --------------------------------------------------------------------------- #
# (в) дублёр не готов — запасной путь: прежняя перемотка
# --------------------------------------------------------------------------- #
def test_a_spare_that_is_not_ready_falls_back_to_the_seek(tmp_path: Path) -> None:
    """Дублёр не долез (readyState/позиция вне допуска) — стык идёт прежним путём.

    Запасной путь обязан остаться: <audio> может не успеть открыться, а стык мимо себя
    не пропустит. Тогда, и только тогда, живой перематывается — как было до дублёра.
    """
    res = _run_node(tmp_path, _player(4.0) + """
const spare=vtSpareOf(P);               // дублёр создан, но ещё не взведён
vtSpareAt(P,6);
vtSpareArm(P);
spare.el._ct=6;spare.rolling=true;
spare.el.readyState=2;                 // трек дублёра ещё не открылся
const ready=vtSpareTake(P,6);
const rolling=spare.rolling;           // отказ обязан снять дублёра с разбега
spare.el.readyState=4;
spare.el._ct=3;spare.rolling=true;     // теперь трек открыт, но дублёр отстал на 3 с
const far=vtSpareTake(P,6);
spare.el._ct=6.5;spare.rolling=true;   // и наоборот, перебежал
const ahead=vtSpareTake(P,6);
console.log(JSON.stringify({
  took:ready, rolling:rolling, far:far, ahead:ahead, ct:P.vt.el.currentTime}));
""")

    assert res["took"] is False, "подмена прошла с неготовым дублёром: %s" % res
    assert res["rolling"] is False, "отказавший дублёр остался «едущим» — подмена сорвётся молча"
    assert res["far"] is False and res["ahead"] is False, \
        "подмена прошла при позиции дублёра вне допуска: %s" % res
    assert res["ct"] == 4.0, "живого тронули на отказавшей подмене"


def test_a_cut_without_a_gap_goes_without_the_spare(tmp_path: Path) -> None:
    """Смежные куски исходника (сохранённый ✂ без удаления) подмены не требуют.

    Тот же порог, что у камер: живой <audio> доигрывает сам, микро-seek на смежном стыке
    только флашит декодер.
    """
    res = _run_node(tmp_path, _player(4.0) + """
P.audio=[{ts:0,te:4,src:0},{ts:4,te:10,src:4.02}];   // зазор 0.02 с
const seg=vtSpareSeg(P);
const took=vtSpareSwap(P);
console.log(JSON.stringify({seg:seg?seg.at:null,took:took}));
""")

    assert res["seg"] is None, "на смежном куске заведён разбег дублёра: %s" % res
    assert res["took"] is False, res


# --------------------------------------------------------------------------- #
# (г) пауза и скраб — дублёр не готовится
# --------------------------------------------------------------------------- #
def test_pause_and_scrub_do_not_prepare_the_spare(tmp_path: Path) -> None:
    """На паузе и под протяжкой ползунка разбег не готовится и дублёр не ПУСКАЕТСЯ.

    Разбег — это seek дублёра, а ползунок сыплется на каждый пиксель: пока разбег
    готовился на каждом таком вызове, дублёр оставался вечно «seeking», и на ближайшем
    стыке подмена срывалась в запасной путь. Тут сторож посильнее: на паузе дублёр не
    выпускается вживую вовсе.
    """
    res = _run_node(tmp_path, _player(5.7) + """
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;
spare.el._ct=6;spare.rolling=true;spare.el.plays=0;   // к стыку дублёр уже едет
const armCt=spare.at-PV_PREROLL;                      // где он был взведён на разбег
// Пауза: игра не идёт (редактор стоит) — дублёр обязан замолчать и остановиться
vtPause(P);
const onPause={plays:spare.el.plays,paused:spare.el.paused,muted:spare.el.muted};
// Под протяжкой ползунка игра тоже не идёт: кадр не должен готовить разбег. Прицел
// сдвигаем САМИ и снимаем отметку взвода: если кадр полезет готовить разбег на паузе,
// это будет видно по позиции дублёра, а не спрячется за прежним взводом.
vtSpareIdle(P);vtSpareAt(P,8);
spare.el._ct=3;spare.rolling=true;                    // отметка: тронули или нет
P.scrubbing=true;P.play=false;P.cs=5.7;
vtTick(P,5.7);
const onScrub={ct:spare.el.currentTime,rolling:!!spare.rolling,plays:spare.el.plays};
console.log(JSON.stringify({onPause:onPause,onScrub:onScrub,armCt:armCt}));
""")

    assert res["onPause"]["paused"] is True and res["onPause"]["muted"] is True, \
        "на паузе дублёр остался играть: %s" % res
    assert res["onPause"]["plays"] == 0, \
        "на паузе дублёра выпустили вживую: %s" % res
    # Под протяжкой ползунка кадр не готовит разбег: прицел сменили на 8.0, а дублёр
    # остался там, где его пометили, — значит ни seek'а, ни пуска не было.
    assert res["onScrub"]["plays"] == 0, \
        "под протяжкой дублёра выпустили вживую: %s" % res
    assert res["onScrub"]["ct"] == 3, \
        "под протяжкой дублёра перемотали — разбег готовится на каждый пиксель: %s" % res


def test_the_jump_through_a_cut_uses_the_spare_and_keeps_the_seek_as_a_fallback(
        tmp_path: Path) -> None:
    """Прыжок через вырез проходит дублёром; не готов — seek на месте.

    Прыжок делает картинка, и дорожка обязана прыгнуть В ТОМ ЖЕ кадре: «подъезжающий»
    голос слышен как чужой кусок клипа. Дублёр на стыке готов — живой не перематывается.
    """
    res = _run_node(tmp_path, _player(4.0) + """
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;spare.el._ct=6;spare.rolling=true;
const took=vtSpareTake(P,6);
console.log(JSON.stringify({took:took,liveCt:P.vt.el.currentTime}));
""")

    assert res["took"] is True and res["liveCt"] == 6, \
        "прыжок через вырез не воспользовался дублёром: %s" % res


# --------------------------------------------------------------------------- #
# (д) тот же стык у плеера, который ведёт EDL (шаг 3, раскладка камер)
# --------------------------------------------------------------------------- #
def test_the_edl_cut_primes_the_spare_before_it_arrives(tmp_path: Path) -> None:
    """У плеера с EDL прицел дублёра — сегмент монтажа, и он взводится ДО стыка.

    У превью шага 3 и раскладки камер прицел ставит vtSpareSwap (сегмент EDL): зазевавшийся
    взвод = стык через перемотку живого, то есть ровно та задержка, от которой уходим.
    """
    res = _run_node(tmp_path, _player(4.0) + """
const seg=vtSpareSeg(P);                           // прицел — кусок ПОСЛЕ стыка
vtSpareAt(P,seg.at);
const spare=vtSpareOf(P);
const src=spare.el.src, liveSrc=P.vt.el.src;
vtSpareArm(P);                                     // взвод разбега (как за VT_ARM до стыка)
const armedAt=spare.at, armedCt=spare.el.currentTime;
spare.el._ct=6;spare.rolling=true;                 // разбег отыгран
const took=vtSpareSwap(P);
console.log(JSON.stringify({seg:seg.at, src:src, liveSrc:liveSrc, armedAt:armedAt,
  armedCt:armedCt, took:took, liveCt:P.vt.el.currentTime, liveSrcNow:P.vt.el.src}));
""")

    assert res["seg"] == 6, "прицел дублёра взят не из сегмента после стыка: %s" % res
    # Источник дублёра — тот же трек, что у живого: он же и выйдет в эфир.
    assert res["src"] == res["liveSrc"] == "/api/media?path=C:/cache/final.wav", res
    assert res["armedAt"] == 6, "дублёр взведён не на позицию после стыка EDL: %s" % res
    assert res["took"] is True, "стык EDL не прошёл подменой дублёра: %s" % res
    assert res["liveCt"] == 6, "дорожка после стыка стоит не на нужном кадре: %s" % res


# --------------------------------------------------------------------------- #
# (з) после подмены вышедший в эфир голос ЗВУЧИТ: дублёр немой, но живым он быть не должен
# --------------------------------------------------------------------------- #
# Баг архитектора (шаг 1, замер в браузере): `vtOf(ED).el.muted===true` в 147 замерах из
# 147 — с самого начала и после каждого стыка. Дублёр дорожки голоса создаётся НЕМЫМ, и
# это верно, пока он дублёр. На стыке `bufSwap` меняет элементы РОЛЯМИ, но размьючивал
# только сменившего роль гасил; вышедший в эфир оставался `muted`, а `muted` элемента
# глушит и путь через Web Audio (`createMediaElementSource`, voiceWiring) — после первого
# же стыка голос молчал до конца клипа. У видео этого не было: его звук решает отдельная
# логика (`camVisual`: `v.muted=(i!==ac)||…`), и молчание дублёра-камеры она же и снимает.
#
# Правило одно и то же во всех случаях: НЕМЫМ живёт только ДУБЛЁР. Тесты ниже проверяют
# его там, где оно обязано исполняться: на подмене, на паре стыков подряд, на паузе с
# повторным запуском и на передаче эфира стоящего плеера (spareHandover).
LIVE_VOL = 0.42   # громкость живого голоса в стендах: множитель прослушивания, не единица

# Стенд с громкостью, отличной от MEDIA_VOL: вышедший в эфир обязан взять ЕЁ, а не
# умолчание. Для этого и свой `_player`: у общего громкость — как в бою по умолчанию.
PLAYER_VOL = """
const P=P_();ED=P;
P.cs={live_ct};
const vox=new El('audio');vox.src='/api/media?path=C:/cache/final.wav';
vox._ct={live_ct};vox.readyState={live_ready};vox.paused=true;
vox.muted=false;vox.volume={live_vol};
P.vt={track};
"""


def _player_vol(live_ct: float, live_vol: float = LIVE_VOL, live_ready: int = 4) -> str:
    """Плеер, у которого живой голос играет со СВОЕЙ громкостью (не MEDIA_VOL)."""
    track = TRACK.replace("__LIVE__", "vox")
    return PLAYER_VOL.format(live_ct=live_ct, live_ready=live_ready, live_vol=live_vol,
                             track=track)


def test_the_voice_is_unmuted_when_the_spare_comes_on_air(tmp_path: Path) -> None:
    """На стыке вышедший в эфир голос ЗВУЧИТ: дублёр немой, живым он быть не должен.

    Баг (замер архитектора в браузере, 147 замеров из 147): `vtOf(ED).el.muted===true`.
    `bufSwap` гасил ушедшего в дублёры, но НЕ размьючивал вышедшего в эфир, а `muted`
    глушит и путь через Web Audio — после первого стыка голос молчал.
    """
    res = _run_node(tmp_path, _player(4.0) + """
const old=P.vt.el;
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;spare.el._ct=6;spare.rolling=true;
const took=vtSpareTake(P,6);
console.log(JSON.stringify({took:took,liveMuted:!!P.vt.el.muted,
  spareMuted:!!P.vt.vsp.el.muted,liveIsOld:P.vt.el===old,
  liveVol:P.vt.el.volume,spareVol:P.vt.vsp.el.volume}));
""")

    assert res["took"] is True, "подмена не состоялась — проверять нечего: %s" % res
    assert res["liveIsOld"] is False, res
    assert res["liveMuted"] is False, \
        "вышедший в эфир голос остался немым — молчание и есть тот баг: %s" % res
    assert res["spareMuted"] is True, \
        "ушедший в дублёры голос остался звучать: %s" % res
    assert res["liveVol"] == res["spareVol"], \
        "обмен ролями свёл громкости живого и дублёра к разным: %s" % res


def test_the_voice_keeps_sounding_through_two_cuts_in_a_row(tmp_path: Path) -> None:
    """Два стыка подряд: живой голос не заглох ни на первом, ни на втором.

    Свежий дублёр создаётся немым каждый раз (`vtSpareOf`), и если размьючивание живёт не
    в самой подмене, а рядом, второй стык снова оставил бы клип без голоса.
    """
    res = _run_node(tmp_path, _player(4.0) + """
vtSpareAt(P,6);vtSpareArm(P);
let sp=P.vt.vsp;sp.el._ct=6;sp.rolling=true;
const took1=vtSpareTake(P,6);
const live1=!!P.vt.el.muted;
vtSpareAt(P,10);vtSpareArm(P);          // второй стык: взводится свежий дублёр
sp=P.vt.vsp;sp.el._ct=10;sp.rolling=true;
const took2=vtSpareTake(P,10);
console.log(JSON.stringify({took1:took1,took2:took2,live1:live1,
  live2:!!P.vt.el.muted,at2:P.vt.el.currentTime,sp2:!!P.vt.vsp.el.muted,
  src2:P.vt.el.src===P.vt.vsp.el.src}));
""")

    assert res["took1"] is True and res["took2"] is True, \
        "подмены не прошли — стенд проверяет не то: %s" % res
    assert res["live1"] is False, res
    assert res["live2"] is False, \
        "после второго стыка подряд живой голос снова немой: %s" % res
    assert res["sp2"] is True, res
    assert res["src2"] is True, res
    assert res["at2"] == 10, res


def test_pause_and_replay_leave_the_voice_audible(tmp_path: Path) -> None:
    """Пауза и повторный запуск: дублёр гасится, живой голос — нет.

    На паузе `vtPause` -> `vtSpareStop` -> `bufSilent` глушит и останавливает ДУБЛЁРА
    (стенд зовёт эти двери сам: у него они не вырезаны). Расползись это правило на
    живого — и после первой же паузы клип остался бы без голоса.
    """
    res = _run_node(tmp_path, _player(4.0) + """
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;spare.el._ct=6;spare.rolling=true;
const took=vtSpareTake(P,6);
const liveRef=P.vt.el;                    // живой — вышедший в эфир дублёр
const newSp=vtSpareOf(P);newSp.el._ct=6;newSp.rolling=true;   // готовится следующий стык
P.play=true;
vtPause(P);
const onPause={live:!!liveRef.muted,spare:!!newSp.el.muted};
P.play=false;P.cs=6;                      // повторный запуск: дорожка играет с того же места
vtTick(P,6);
vtSpareAt(P,8);vtSpareArm(P);
console.log(JSON.stringify({took:took,onPause:onPause,liveStillLive:P.vt.el===liveRef,
  onResume:!!P.vt.el.muted,spAfter:!!P.vt.vsp.el.muted}));
""")

    assert res["took"] is True, res
    assert res["onPause"]["spare"] is True, \
        "на паузе дублёр остался звучать: %s" % res
    assert res["onPause"]["live"] is False, \
        "пауза заглушила ЖИВОЙ голос — после неё клип останется немым: %s" % res
    assert res["liveStillLive"] is True, res
    assert res["onResume"] is False, \
        "после повторного запуска живой голос немой: %s" % res
    assert res["spAfter"] is True, res


def test_the_handover_of_a_standing_player_unmutes_the_voice(tmp_path: Path) -> None:
    """Передача эфира стоящего плеера (`bufSwap` напрямую) тоже размьючивает голос.

    Тот же обмен ролями, что на стыке, — только дверь другая (`spareHandover`). Правило
    живёт в самом обмене, поэтому вторая дверь не может его забыть.
    """
    res = _run_node(tmp_path, _player(4.0) + """
const st=vtOf(P),old=st.el;
const spare=vtSpareOf(P);spare.el._ct=4;
const ok=bufSwap(P,spare);
console.log(JSON.stringify({ok:ok,liveMuted:!!st.el.muted,liveIsOld:st.el===old,
  spareMuted:!!spare.el.muted,spareIsOld:spare.el===old}));
""")

    assert res["ok"] is True, res
    assert res["liveMuted"] is False, \
        "стоячий плеер передал эфир немому голосу: %s" % res
    assert res["spareMuted"] is True and res["spareIsOld"] is True, res


def test_the_voice_keeps_its_volume_on_the_handover(tmp_path: Path) -> None:
    """Громкость вышедшего в эфир голоса — прежнего живого, а не умолчание MEDIA_VOL.

    `bufSwap` ставит вышедшему `MEDIA_VOL` — это уровень прослушивания камер, и для
    дорожки голоса он не единственный: свой уровень (стиль, цензура, дорожка шумодава)
    у неё уже стоит, и обмен ролями не должен его ронять.
    """
    res = _run_node(tmp_path, _player_vol(4.0) + """
const old=P.vt.el;
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;spare.el._ct=6;spare.rolling=true;
const took=vtSpareTake(P,6);
console.log(JSON.stringify({took:took,vol:P.vt.el.volume,wasVol:old.volume,
  mediaVol:MEDIA_VOL,muted:!!P.vt.el.muted}));
""")

    assert res["took"] is True, res
    assert abs(res["wasVol"] - LIVE_VOL) < 1e-9, res
    assert abs(res["vol"] - LIVE_VOL) < 1e-9, \
        "обмен ролями сбросил громкость голоса на уровень камер: %s" % res
    assert res["muted"] is False, res


# --------------------------------------------------------------------------- #
# мутации: правило вернули в прежний вид — тесты обязаны покраснеть
# --------------------------------------------------------------------------- #
def test_the_missing_unmute_mutation_turns_the_voice_test_red(tmp_path: Path) -> None:
    """Мутация: вернуть `bufSwap` прежний вид (не размьючивать вышедшего) — тест краснеет.

    Проверяется не код, а сам сторож: если убрать одну строку размьючивания, живой голос
    снова окажется немым — ровно тот баг, ради которого тест и заведён.
    """
    res = _run_node(tmp_path, _player(4.0) + """
// Прежний вид обмена: размьючивания вышедшего в эфир нет вовсе.
const oldSwap=bufSwap;
bufSwap=function(P,b){
  const old=liveOf(P,b);if(!old)return false;
  if(b.slot==null)vtOf(P).el=b.el;else P.vids[b.slot]=b.el;
  b.el=old;old.pause();old.muted=true;old.playbackRate=1;
  if(old.style)old.style.zIndex='0';
  const live=liveOf(P,b);live.playbackRate=1;live.volume=MEDIA_VOL;
  b.rolling=false;b.at=null;return true;};
vtSpareAt(P,6);vtSpareArm(P);
const spare=P.vt.vsp;spare.el._ct=6;spare.rolling=true;
const took=vtSpareTake(P,6);
console.log(JSON.stringify({took:took,liveMuted:!!P.vt.el.muted}));
""")

    assert res["took"] is True, res
    assert res["liveMuted"] is True, \
        "мутация не воспроизвела баг — сторож проверяет не то: %s" % res


