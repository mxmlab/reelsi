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
(г) пауза и скраб — дублёр не готовится;
(е) стык РЕДАКТОРА шага 1: прицел и разгон ставит `edArm` и ставит их на `ED` (у него
    живёт дорожка голоса), а не на `PV` — на нём разбег не разгонял никого;
(ж) промах видео-дублёра (edTake не удался): голос НЕ подменяется дублёром — он глушится
    и ждёт `seeked` живого видео, а после события включается ровно на его позиции. Это
    и есть «звук не обгоняет картинку»: замер архитектора — p95 91 мс, 15 точек > 45 мс.

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
CONSTS = ("PV_PREROLL", "PV_SWAP_LO", "PV_SWAP_HI", "VT_SWAP_LO", "VT_SWAP_HI", "VT_ARM",
          "VT_SOFT", "VT_DRIFT", "VT_RATE", "MEDIA_VOL")
# Двери РЕДАКТОРА шага 1 (70-editor.js): стык блоков правки — его, и прицел дорожки голоса
# ставит edArm. Тела берём из файла, копий в тесте нет. edJump здесь ради (е) ниже: голос
# идёт за ФАКТИЧЕСКИМ решением видео, а проверяется это его дверью, а не по кускам;
# edVoiceSeekWait/Close — ожидание промаха, которое edJump и заводит.
EDITOR_FUNCS = ("edBlockAt", "edArm", "edSeek", "edTake", "edJump",
                "edVoiceSeekWait", "edVoiceSeekClose", "edVoiceSeekOff")


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
                     + [_const(editor, "EDMUTVOICE")]   # выключатель мутации — из файла
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
function edRaw(){return false;}                 // выреза нет: стенд проверяет стык
function edInCut(){return false;}
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
    """Прыжок через вырез (edJump) проходит дублёром; не готов — seek на месте.

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

    Прицел у дорожки голоса один на все плееры: у редактора его ставит edArm (блоки
    правки), у превью шага 3 и раскладки камер — vtSpareSwap (сегмент EDL). Машина
    подмены одна, поэтому и проверяем оба прицела: зазевавшийся взвод = стык через
    перемотку живого, то есть ровно та задержка, от которой уходим.
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
# (е) стык РЕДАКТОРА шага 1: прицел и разгон ставит edArm — и ставит их на ED
# --------------------------------------------------------------------------- #
# Стенд редактора: PV — кадр и видео-дублёр, ED — часы и дорожка обработанного голоса.
# Два РАЗНЫХ плеера, как в бою: на одном объекте промах «взвели не тому дублёру» не поймать.
# Стык: блок правки кончается на 4.0, следующий начинается на 4.3 — вырезано 0.3 с.
EDITOR_STAND = r"""
const cam=new El('video');cam.src='C:/cam1.mp4';cam._ct=0;
const live=new El('audio');live.src='/api/media?path=C:/cache/final.wav';live._ct=0;
PV={vids:[cam],bufs:[],cams:[{path:'C:/cam1.mp4'}],segs:[],audio:[],words:[],dur:10,aidx:0,vidx:-1,
  curCi:-1,scrubbing:false,raf:0,xml:'C:/out/01_clip.xml',voicePanel:null};
ED={xml:'C:/out/01_clip.xml',blocks:[{s0:0,s1:4},{s0:4.3,s1:10}],fps:60,cam:'C:/cam1.mp4',dur:10,
  peaks:[],pps:80,sel:-1,play:true,raw:false,raf:0,cs:0,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],
  brBand:0,cams:[{path:'C:/cam1.mp4'}],voicePanel:null,
  vt:{on:true,el:live,path:'C:/cache/final.wav',fin:true,seq:0,timer:0,poll:0,want:'',note:'',
      vtq:false,vtend:false,live:null,dn:'',wantDn:'',wantFinal:true,warn:'',statesWait:null,
      vsp:null,vspAt:null}};
const b0=ED.blocks[0],nb=ED.blocks[1],dt=1/60;
"""


def test_the_editor_cut_rolls_the_voice_spare_on_ed(tmp_path: Path) -> None:
    """Стык редактора: дублёр голоса разгоняется ЗАРАНЕЕ, а на стыке сам выходит в эфир.

    Баг (найден архитектором в браузере): `edArm` ставил прицел и разгон дорожки голоса
    на `PV`, а дорожка редактора живёт на `ED` — на каждом из четырёх прыжков дублёр
    стоял `paused:true, currentTime:0, rolling:false`, удачных подмен 0, и стык шёл
    перемоткой живого <audio> (та самая задержка 100–200 мс). Здесь часы идут к концу
    первого блока, и проверяется весь путь: дублёр уведён на `PV_PREROLL` до позиции
    ПОСЛЕ стыка, едет немым, на стыке `vtSpareTake` отдаёт эфир ему — а живого не сеcит
    никто.
    """
    res = _run_node(tmp_path, EDITOR_STAND + """
let firstRoll=null,took=false,liveBefore=null,liveAfter=null,oldSeeks=null;
for(let i=0;i<600;i++){
  live._ct+=dt;cam._ct+=dt;ED.cs=cam._ct;               // живой исходник играет, часы идут за ним
  const sp=vtOf(ED).vsp;
  if(sp&&sp.rolling)sp.el._ct+=dt*sp.el.playbackRate;   // дублёр отыгрывает разбег в фоне
  if(ED.cs>=b0.s1-0.02){                                // стык: edTick -> edJump
    liveBefore=live.currentTime;oldSeeks=live.seeks.length;
    vtTick(ED,nb.s0);                                   // edJump: сначала кадр дорожки
    took=vtSpareTake(ED,nb.s0);                         // ...и подмена в том же кадре
    liveAfter=vtOf(ED).el.currentTime;                  // живой ПОСЛЕ подмены — бывший дублёр
    break;}
  vtTick(ED,ED.cs);                                     // edTick: кадр дорожки
  edArm();                                              // ...и один edArm на разбег
  const r=vtOf(ED).vsp;
  if(!firstRoll&&r&&r.rolling)
    firstRoll={cs:ED.cs,ct:r.el.currentTime,at:r.at,paused:!!r.el.paused,muted:!!r.el.muted};}
console.log(JSON.stringify({firstRoll:firstRoll,took:took,liveBefore:liveBefore,liveAfter:liveAfter,
  oldSeeks:oldSeeks,liveSeeks:live.seeks.length,cut:nb.s0,b0end:b0.s1,preroll:PV_PREROLL,
  win:VT_ARM-PV_PREROLL,lo:VT_SWAP_LO,hi:VT_SWAP_HI}));
""")

    assert res["firstRoll"], (
        "разбег дорожки голоса в редакторе не готовился вовсе (дублёр взведён не тому плееру?)")
    roll = res["firstRoll"]
    # Дублёр уведён на PV_PREROLL до позиции ПОСЛЕ стыка и едет немым: слышно его быть не должно.
    assert abs(roll["at"] - res["cut"]) < 1e-6, roll
    assert abs(roll["ct"] - (res["cut"] - res["preroll"])) < 1e-3, \
        "дублёр стоит не за PV_PREROLL до позиции после стыка: %s" % roll
    assert roll["muted"] is True and roll["paused"] is False, \
        "дублёр не поехал немым в окне разбега: %s" % roll
    # Разгон начат ДО прыжка (в хвосте первого блока) и не раньше окна VT_ARM-PV_PREROLL.
    assert 0 < res["b0end"] - roll["cs"] <= res["win"] + 2 / 60, \
        "разгон начат вне окна до стыка: %s" % roll
    # На стыке в эфир вышел дублёр: подмена прошла, и позиция — у начала следующего блока.
    assert res["took"] is True, "подмена дублёром на стыке редактора не прошла: %s" % res
    assert res["lo"] <= res["liveAfter"] - res["cut"] <= res["hi"], res
    # ...и главное: живого <audio> не перематывали — ни в кадре прыжка, ни на самой подмене.
    assert res["liveSeeks"] == res["oldSeeks"], \
        "живому <audio> присвоили currentTime на стыке — это и есть та задержка: %s" % res
    # Прежний живой остался там, где играл (конец первого блока), а не уехал на стык.
    assert res["b0end"] - 0.05 <= res["liveBefore"] < res["cut"], res


def test_the_last_block_and_a_seek_drop_the_voice_runup(tmp_path: Path) -> None:
    """Стыка впереди нет (последний блок) или плейхед перемотали — разбег голоса снят.

    Сброс живёт там же, где у видео-дублёра: `edArm` — для последнего блока, `edSeek` —
    для перемотки. Оставленный прицел страшен тем, что `vtSpareCtl` взводит дублёра по
    `vspAt` каждый кадр: разогнанный под исчезнувший стык дублёр вернулся бы на него и
    доигрывал фоном (лишний декод рядом с живым — тот самый ресурс, из-за которого стыки
    и дёргались).
    """
    res = _run_node(tmp_path, EDITOR_STAND + """
cam._ct=3.9;live._ct=3.9;ED.cs=3.9;
edArm();                                          // стык близко: дублёр взведён
const sp=vtOf(ED).vsp;
const armed={made:!!sp,at:sp?sp.at:null};
let onLast=null,onSeek=null;
if(sp){
  sp.el._ct=nb.s0;sp.rolling=true;sp.el.play();   // ...и уже разогнан
  ED.blocks=[{s0:0,s1:4}];                        // правка убрала стык: блок последний
  edArm();
  onLast={at:sp.at,rolling:!!sp.rolling,paused:sp.el.paused,muted:sp.el.muted,
    aim:vtOf(ED).vspAt};
  sp.el._ct=nb.s0;sp.rolling=true;sp.el.play();   // снова разогнан — и перемотка плейхеда
  ED.blocks=[{s0:0,s1:4},{s0:4.3,s1:10}];ED.cs=3.9;
  edArm();                                        // стык вернулся: разбег взведён заново
  sp.el._ct=nb.s0;sp.rolling=true;sp.el.play();
  ED.play=false;edSeek(1.0);
  onSeek={at:sp.at,rolling:!!sp.rolling,paused:sp.el.paused,muted:sp.el.muted,cs:ED.cs,
    aim:vtOf(ED).vspAt};}
console.log(JSON.stringify({armed:armed,onLast:onLast,onSeek:onSeek,cut:nb.s0}));
""")

    assert res["armed"]["made"] is True, "дублёр голоса не взведён вовсе: %s" % res
    assert res["armed"]["at"] == res["cut"], res
    assert res["onLast"] and res["onSeek"], "дублёр голоса пропал с плеера редактора: %s" % res
    last = res["onLast"]
    assert last["at"] is None and last["rolling"] is False, \
        "на последнем блоке разбег голоса остался взведён: %s" % last
    assert last["paused"] is True and last["muted"] is True, \
        "разогнанный дублёр остался играть фоном: %s" % last
    assert last["aim"] is None, "прицел голоса не снят — vtSpareCtl взведёт дублёра заново"
    seek = res["onSeek"]
    assert seek["at"] is None and seek["rolling"] is False, \
        "перемотка не сняла разбег голоса: %s" % seek
    assert seek["paused"] is True and seek["muted"] is True, seek
    assert seek["aim"] is None, "перемотка оставила прицел — дублёр вернётся на покинутый стык"
    assert abs(seek["cs"] - 1.0) < 1e-9, "плейхед перемотки не встал на место: %s" % seek


# --------------------------------------------------------------------------- #
# (ж) промах видео-дублёра: голос идёт за ФАКТИЧЕСКИМ решением картинки
# --------------------------------------------------------------------------- #
# Стенд прыжка: у видео дублёр ЕСТЬ (`bufs` не пуст — без него прыжок вообще не пробует
# подмену, и «мягкий» промах не воспроизвести), но не готов: он не разогнан, поэтому
# `bufTake` отказывает. Картинка идёт прежним путём, seek'ом на месте, — ровно тот случай,
# в котором голос раньше подменялся дублёром мгновенно и ОБГОНЯЛ ещё едущую картинку.
# Звук камеры (`cam`) и дорожка голоса (`vox`) — РАЗНЫЕ элементы, как в бою: без этого
# промах «гейт закрыт» не отличить от «гейт открыт», и сторож пропустил бы два голоса.
JUMP_STAND = r"""
const cam=new El('video');cam.src='C:/cam1.mp4';cam.readyState=4;cam._ct=3.9;
const spareCam=new El('video');spareCam.src='C:/cam1.mp4';spareCam.readyState=4;spareCam.muted=true;
const vox=new El('audio');vox.src='/api/media?path=C:/cache/final.wav';vox.readyState=4;vox._ct=3.9;
PV={vids:[cam],bufs:[{el:spareCam,slot:0,off:0,at:null,rolling:false}],
  cams:[{path:'C:/cam1.mp4'}],segs:[],audio:[],words:[],dur:10,aidx:0,vidx:0,
  curCi:-1,scrubbing:false,raf:0,xml:'C:/out/01_clip.xml',voicePanel:null};
ED={xml:'C:/out/01_clip.xml',blocks:[{s0:0,s1:4},{s0:6,s1:10}],fps:60,cam:'C:/cam1.mp4',dur:10,
  peaks:[],pps:80,sel:-1,play:true,raw:false,raf:0,cs:3.9,drag:null,v0:0,v1:0,hist:[],cuts:[],br:[],
  brBand:0,cams:[{path:'C:/cam1.mp4'}],voicePanel:null,vids:PV.vids,   // камеры — тот же живой список
  vt:{on:true,el:vox,path:'C:/cache/final.wav',fin:true,seq:0,timer:0,poll:0,want:'',note:'',
      vtq:false,vtend:false,live:null,dn:'',wantDn:'',wantFinal:true,warn:'',statesWait:null,
      vsp:null,vspAt:null}};
// Дублёр голоса взведён и разогнан: он ЖДЁТ подмены на 6.0. Всё, что от него требуется в
// промахе, — не выйти в эфир, а в мутации — выйти.
vtSpareAt(ED,6);vtSpareArm(ED);
const spare=vtOf(ED).vsp;spare.el._ct=6;spare.rolling=true;spare.armed=true;
PV.scrubbing=false;
const b0=ED.blocks[0],nb=ED.blocks[1];
"""


def test_a_missed_video_spare_keeps_the_voice_gated_until_the_video_seeks(tmp_path: Path) -> None:
    """Промах видео-дублёра: голос не подменяется, молчит и включается по `seeked` видео.

    Замер архитектора после `6c471e0`: отставаний больше 125 мс нет, но звук СПЕШИТ —
    15 точек > 45 мс, p95 91 мс. Причина в коде: `edJump` подменял дорожку голоса
    дублёром ВСЕГДА, а картинка при промахе видео-дублёра ещё ехала seek'ом, и голос
    оказывался на новом месте раньше неё. Правило одно: голос идёт за ФАКТИЧЕСКИМ
    решением видео на этом стыке.
    """
    res = _run_node(tmp_path, JUMP_STAND + """
const el=vtOf(ED).el,spareEl=vtOf(ED).vsp.el;
let tookCalls=0;
const realTake=vtSpareTake;
vtSpareTake=function(P,at){tookCalls++;return realTake(P,at);};   // подмена видна счётчиком
// Гейт — ОДНА дверь (vtGate): `false` его закрывает (звук камеры молчит), `true` открывает.
// Считаем именно открытия: пока голос ждёт `seeked`, кадр не имеет права открыть гейт.
let gateOpens=0;
const realGate=vtGate;
vtGate=function(P,on){if(on)gateOpens++;return realGate(P,on);};
const v=cam;
v.seeking=true;                     // декодер уже поехал: seek поднят ДО прыжка картинки
const out=edJump(v,nb.s0);          // дублёр видео не разогнан: edTake не удался -> seek
const opensAtJump=gateOpens;
const atMiss={vSeek:v.currentTime,voicePos:el.currentTime,voicePaused:el.paused,
  voiceMuted:el.muted,open:!!ED.vtOpen,gate:!!vtMuteHost(ED).voiceMute,
  spareRolling:!!vtOf(ED).vsp.rolling,spareAt:vtOf(ED).vsp.at,
  aim:vtOf(ED).vspAt,waited:TIMERS.filter(x=>x&&x.ms===300).length,
  listening:(cam.handlers.seeked||[]).length,out:(out===v)?'video':'spare'};
// Кадр игры во время ожидания: дорожку он не трогает и гейт НЕ открывает — иначе на стыке
// зазвучал бы сырой голос камеры, пока картинка ещё едет.
vtTick(ED,ED.cs);
const afterTick={opens:gateOpens-opensAtJump,voicePaused:el.paused,open:!!ED.vtOpen,
  voicePos:el.currentTime};
// Живое видео доехало: голос обязан включиться ровно на ЕГО позиции, а не на месте прыжка.
v._ct=6.42;v.seeked();
const atSeeked={voicePos:el.currentTime,voicePaused:el.paused,voiceMuted:el.muted,
  videoPos:v.currentTime,gate:!!vtMuteHost(ED).voiceMute,open:!!ED.vtOpen,plays:el.plays,
  gateOpens:gateOpens,sparePaused:spareEl.paused,
  waitedAfter:TIMERS.filter(x=>x&&x.fn).length};
console.log(JSON.stringify({tookCalls:tookCalls,atMiss:atMiss,afterTick:afterTick,
  atSeeked:atSeeked,cut:nb.s0}));
""")

    miss = res["atMiss"]
    # Картинка: дублёр не готов — прежний путь, seek на месте.
    assert miss["vSeek"] == res["cut"], "картинка не ушла seek'ом на место прыжка: %s" % res
    assert miss["out"] == "video", \
        "прыжок вернул дублёра, которого видео не отдавало: %s" % miss
    # Главное: голос НЕ подменён дублёром. Ни вызовом из edJump, ни сам — vtSpareCtl взвёл
    # бы его по оставленному прицелу в следующем же кадре.
    assert res["tookCalls"] == 0, \
        "на промахе видео голос всё-таки подменили дублёром — звук обгонит картинку: %s" % res
    assert miss["spareRolling"] is False and miss["spareAt"] is None, \
        "дублёр голоса остался разогнанным под пройденный стык: %s" % miss
    assert miss["aim"] is None, \
        "на промахе оставлен прицел — vtSpareCtl подменит дублёра в следующем кадре: %s" % miss
    # Глушение без рывка: гейт закрыт и дорожка стоит — на стыке тихо, пока картинка едет.
    assert miss["gate"] is False, "гейт не закрыт на промахе: %s" % miss
    assert miss["voicePaused"] is True, "дорожка не остановлена на время seek'а картинки: %s" % miss
    assert miss["voicePos"] == res["cut"], res
    assert miss["open"] is True, "ожидание `seeked` живого видео не заведено: %s" % miss
    # Ждём ровно одно событие: слушатель один, страховка на 300 мс — тоже одна, а не
    # «ждать вечно» и не «повесить второй обработчик на каждый промах».
    assert miss["waited"] == 1 and miss["listening"] == 1, \
        "ожидание `seeked` заведено не один раз: %s" % miss
    # Кадр во время ожидания гейт не открыл — иначе сырой голос камеры вернулся бы поверх
    # едущей картинки, то есть ровно та рассинхронность, от которой уходим.
    tick = res["afterTick"]
    assert tick["opens"] == 0, \
        "кадр открыл гейт, пока голос ждал `seeked` — звук камеры вернулся раньше картинки: %s" % tick
    assert tick["voicePaused"] is True and tick["open"] is True and tick["voicePos"] == res["cut"], tick
    # После события: голос включён и ВЫРОВНЕН ПО ВИДЕО (6.42), а не по месту прыжка (6.0).
    done = res["atSeeked"]
    assert abs(done["voicePos"] - done["videoPos"]) < 1e-9, \
        "после `seeked` голос стоит не на позиции видео — расхождение и есть обгон: %s" % done
    assert abs(done["voicePos"] - res["cut"]) > 0.05, \
        "голос остался на месте прыжка, а не на доехавшей картинке: %s" % done
    assert done["voicePaused"] is False and done["plays"] == 1, \
        "после `seeked` живого видео дорожка не вернулась в эфир: %s" % done
    assert done["gate"] is True, "гейт не открыт после доехавшей картинки: %s" % done
    assert done["open"] is False, "ожидание не закрылось по своему же событию: %s" % done
    assert done["waitedAfter"] == 0, "страховочный таймаут остался висеть после события: %s" % done


def test_the_watchdog_opens_the_gate_when_the_seek_event_is_lost(tmp_path: Path) -> None:
    """Событие `seeked` потерялось — через 300 мс голос всё равно включается.

    Слушатель — единственная дверь из тишины, и потерять его может кто угодно: `seeked`
    не приходит на микро-перемотку внутри буфера, элемент переехал на прокси, вкладку
    свернули. Тогда голос остался бы немым до конца клипа, поэтому рядом стоит страховка.
    """
    res = _run_node(tmp_path, JUMP_STAND + """
const el=vtOf(ED).el;
cam.seeking=true;
edJump(cam,nb.s0);
const watchdog=TIMERS.find(x=>x&&x.ms===300);
cam._ct=6.42;                                  // картинка доехала, но событие потеряно
if(watchdog)watchdog.fn();                     // сработала страховка
console.log(JSON.stringify({had:!!watchdog,voicePos:el.currentTime,voicePaused:el.paused,
  videoMuted:cam.muted,gate:!!vtMuteHost(ED).voiceMute,open:!!ED.vtOpen,plays:el.plays,
  listening:(cam.handlers.seeked||[]).length}));
""")

    assert res["had"] is True, "страховки на потерянное событие нет вовсе: %s" % res
    assert res["voicePos"] == 6.42, \
        "по страховке голос встал не на позицию видео: %s" % res
    assert res["open"] is False and res["voicePaused"] is False and res["plays"] == 1, \
        "по страховке голос не вернулся в эфир: %s" % res
    assert res["gate"] is True, "по страховке гейт остался закрыт: %s" % res
    assert res["listening"] == 0, "сработавшая страховка оставила слушателя на видео: %s" % res


def test_an_instant_seek_hands_the_voice_over_in_the_same_frame(tmp_path: Path) -> None:
    """Картинка встала seek'ом мгновенно — ждать нечего: голос выравнивается тем же кадром.

    `seeking` не поднялся (позиция уже в буфере декодера) — `seeked` не придёт вовсе, и
    «ждать событие» значило бы держать голос немым всю страховку. Тишины нет: оба элемента
    стоят на одном месте, и гейт открывается сразу — но ПО ПОЗИЦИИ КАРТИНКИ, а не по
    месту прыжка.
    """
    res = _run_node(tmp_path, JUMP_STAND + """
const el=vtOf(ED).el;
el._ct=3.9;                                    // живой голос ещё на старом месте
el.seeks.length=0;                             // интересны перемотки ЭТОГО прыжка
ED.cs=nb.s0;                                   // как edTick: плейхед встаёт на место прыжка
edJump(cam,nb.s0);
console.log(JSON.stringify({voicePos:el.currentTime,voicePaused:el.paused,
  videoMuted:cam.muted,voiceIsLive:vtOf(ED).el===el,open:!!ED.vtOpen,plays:el.plays,
  seeks:el.seeks.slice(),minSeek:Math.min.apply(null,el.seeks),
  waited:TIMERS.filter(x=>x&&x.fn).length,
  listening:(cam.handlers.seeked||[]).length}));
""")
    assert res["voiceIsLive"] is True, \
        "на мгновенном seek'е живой дорожкой стал дублёр, а картинка не переезжала: %s" % res
    # Дорожку ставили на место прыжка (её позиция была 3.9 — прыжок действительно был нужен).
    assert res["minSeek"] == 6, \
        "мгновенный seek не поставил голос на место прыжка: %s" % res
    assert res["voicePaused"] is False, \
        "мгновенный seek оставил голос на паузе, хотя картинка уже на месте: %s" % res
    assert res["open"] is False and res["waited"] == 0 and res["listening"] == 0, \
        "на мгновенный seek заведено ожидание события, которого не будет: %s" % res


def test_the_missed_spare_take_mutation_turns_the_gate_test_red(tmp_path: Path) -> None:
    """Мутация: снова звать `vtSpareTake` на стыке БЕЗУСЛОВНО — промах обязан покраснеть.

    Возвращаем ровно то, что было до правки: модуль держит для этого один выключатель
    (`EDMUTVOICE`, выключенный в бою). С ним в промахе живой дорожкой становится бывший
    дублёр с его собственным currentTime 6.0, гейта и ожидания `seeked` нет вовсе, а
    позиция видео голос уже не касается — это и есть обгон звука.
    """
    res = _run_node(tmp_path, JUMP_STAND + """
const el=vtOf(ED).el,spareRef=vtOf(ED).vsp;
// Стенд промаха: видео-дублёр есть, но не готов (не разогнан) — картинка идёт seek'ом.
// Мутация (`EDMUTVOICE`) возвращает прежнее правило: голос подменяется дублёром на стыке
// НЕЗАВИСИМО от того, как стык прошёл у видео.
let took=0;
const realTake=vtSpareTake;
vtSpareTake=function(P,at){took++;return realTake(P,at);};
spareRef.el._ct=6;spareRef.rolling=true;spareRef.armed=true;   // дублёр готов выйти в эфир
EDMUTVOICE=1;
edJump(cam,nb.s0);
const now=vtOf(ED).el;
console.log(JSON.stringify({took:took,pos:now.currentTime,open:!!ED.vtOpen,
  paused:now.paused,plays:now.plays,spareIsOld:now===spareRef.el,liveIsOld:now===el,
  gate:!!vtMuteHost(ED).voiceMute}));
""")

    # Мутация: прежнее правило позвало подмену и та СОСТОЯЛАСЬ. Голос ушёл на середину
    # прыжка (6.0) своим ходом, гейта нет — звук пошёл поверх незакрытой картинки.
    assert res["took"] == 1, "мутация не позвала подмену — стенд проверяет не то: %s" % res
    assert res["gate"] is True, "мутация не дала обгона — проверять нечего: %s" % res
    assert res["open"] is False, "мутация не миновала ожидание `seeked`: %s" % res


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



