# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Раскладка камер: звук ведёт часы — очередь Web Audio, как у вставок и шага 1.

Мини-плеер раскладки (CPV, static/app/88-cams.js) играет звук очередью `ea*` из
60-preview.js: К1 — звук камеры 1 (или обработанный голос), К2/К3 — звук своей камеры
(`eaOpenCam`) со сдвигом `delta[k]` от камеры 1. Часы плеера — то, что звучит; <video>
немые и догоняют звук. Стенд гоняет БОЕВЫЕ тела функций под node — копий в тесте нет.

Проверяется:
1. К1 — очередь звука камеры 1 по EDL;
2. К2 — очередь звука камеры 2, сдвинутая на её оффсет `delta[k]`;
3. пока буфер К2 не декодирован — тишина, а не звук камеры 1; приехал — очередь
   пересобирается сама;
4. камера, включившаяся позже камеры 1, звучит тишиной до своего нуля и не получает
   отрицательного смещения `start()`;
5. смена камеры при игре пересобирает очередь: прежние узлы остановлены;
6. часы отдают время монтажа по звуку, а не по `currentTime` немого `<video>`;
7. цензура превью — окна `audio.censor` плана стоят в очереди своим гейном.

Запуск:  py -3.10 -m pytest tests/test_cpv_audio_clock.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")

# Боевые тела звука (блок `ea*`) и его объявления — из 60-preview.js.
EA_DECLS = ("EA_LEAD", "EA_FADE", "EA")
EA_FUNCS = ("eaOpen", "eaOpenCam", "eaDecode", "eaVoice", "eaBuf", "eaSig", "eaPlan", "eaPlanMon",
            "eaNow", "eaStop", "eaStart", "eaClock", "eaSync", "eaGain", "eaOwner", "eaIsMon",
            "eaCensor", "eaCensorGain", "eaPos", "dbToGain", "eaLeadAt")


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


def _decl(src: str, name: str) -> str:
    """Объявление `const|let NAME=…;` из файла — и однострочное, и литерал на несколько строк."""
    m = re.search(r"^(?:const|let) %s=" % re.escape(name), src, re.M)
    assert m is not None, f"нет объявления {name}"
    depth = 0
    for i in range(m.end(), len(src)):
        c = src[i]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == ";" and depth == 0:
            return src[m.start():i + 1]
    raise AssertionError(f"не нашёлся конец объявления {name}")


def _bodies() -> str:
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    out = [_decl(preview, n) for n in EA_DECLS]
    out += [_func_src(preview, n) for n in EA_FUNCS]
    return "\n".join(out)


# Заглушки страницы и поддельный Web Audio. Часы двигает сам тест (ACTX.currentTime),
# узлы помнят, куда и как их поставили (started/stopped).
STAND = r"""
const LOGS=[];
function uiLog(m){LOGS.push(String(m));}
function toast(m){LOGS.push('toast: '+m);}
function errText(d){return (d&&(d.error||d.err))||'';}
function voiceDbOf(){return 0;}          // громкость голоса стиля: 0 дБ — множитель 1
function vtLiveOn(){return false;}       // окно плагина закрыто
let MEDIA_VOL=1;
let IPV=null;
// Плеер раскладки — литерал с полями, которые читает очередь звука (как в бою)
let CPV={xml:'C:/out/01_clip.xml',audio:[{ts:0,te:10,src:20},{ts:10,te:20,src:40}],
  delta:[0,1.5],audioCi:0,tm:0,dur:20,ea:true};
// Редактора шага 1 в этом стенде нет: очередь звука хозяин — EA.P = CPV
let ED={};
class FParam{constructor(v){this.value=v;this.ev=[];}
  setValueAtTime(v,t){this.ev.push(['set',v,t]);}
  linearRampToValueAtTime(v,t){this.ev.push(['ramp',v,t]);}}
class FNode{constructor(kind){this.kind=kind;this.out=[];this.gain=new FParam(1);
    this.buffer=null;this.started=null;this.stopped=false;}
  connect(n){this.out.push(n);return n;}
  disconnect(){this.out=[];}
  start(when,off,dur){this.started={when:when,off:off,dur:dur};}
  stop(){this.stopped=true;}}
const SRCS=[];                           // все узлы-источники, по порядку создания
const GAINS=[];                          // все гейн-узлы: по ним тест находит гейн цензуры очереди
const RAWBUF={duration:120,name:'raw'},CAM2BUF={duration:120,name:'cam2'};
const ACTX={state:'running',currentTime:100,outputLatency:0,destination:new FNode('dest'),
  createGain(){const n=new FNode('gain');GAINS.push(n);return n;},
  createBufferSource(){const n=new FNode('src');SRCS.push(n);return n;},
  decodeAudioData(ab){return Promise.resolve(ab&&ab.cam2?CAM2BUF:RAWBUF);}};
let AUDIO=null;
function audioGraph(){AUDIO=ACTX;}
// Живые (не остановленные) узлы — то, что сейчас стоит в очереди звука.
function live(){return SRCS.filter(n=>!n.stopped&&n.started);}
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
// Сервер: звук камеры с номером cam:1 (К2) вынимается в свой файл, без cam — камера 1.
globalThis.fetch=async(url,opt)=>{
  const u=String(url);
  if(u==='/api/preview_audio'){const b=JSON.parse(opt.body);
    return {json:async()=>({ok:true,path:b.cam===1?'C:/out/_tmp/pa_cam2.wav':'C:/out/_tmp/pa_cam1.wav'})};}
  if(u.indexOf('/api/media?')===0)return {arrayBuffer:async()=>({cam2:u.indexOf('cam2')>=0})};
  return {json:async()=>({ok:true})};
};
function report(name,extra){console.log(JSON.stringify(Object.assign({name:name},extra)));}
"""


def _run_node(body: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cpv_audio_") as d:
        path = Path(d) / "stand.js"
        path.write_text(STAND + "\n" + _bodies() + "\n" + body, encoding="utf-8")
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                              encoding="utf-8-sig", errors="replace", timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


def _ea_lead() -> float:
    """EA_LEAD — из боевого файла, а не копией в тесте."""
    m = re.search(r"^const EA_LEAD=([0-9.]+);", PREVIEW_JS.read_text(encoding="utf-8"), re.M)
    assert m is not None, "в 60-preview.js нет const EA_LEAD"
    return float(m.group(1))


def _ea_fade() -> float:
    """EA_FADE — из боевого файла, как EA_LEAD."""
    m = re.search(r"^const EA_FADE=([0-9.]+);", PREVIEW_JS.read_text(encoding="utf-8"), re.M)
    assert m is not None, "в 60-preview.js нет const EA_FADE"
    return float(m.group(1))


# --------------------------------------------------------------------------- #
# 1. К1: очередь звука камеры 1 по EDL
# --------------------------------------------------------------------------- #
@node
def test_k1_queue_plays_camera1_from_the_edl() -> None:
    """К1 — звук камеры 1: куски исходника 25..30 и 40..50 встык с монтажом, со сдвигом 5 с.

    Что ломалось бы: звук выбранной камеры играл бы не из своего буфера или не по монтажу —
    и тогда звук камеры 1 (на нём печётся обработанный голос) расходился бы с картинкой.
    """
    out = _run_node("""
(async()=>{
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  eaStart(5,CPV);
  const t0=EA.t0;
  report('k1',{q:live().map(n=>({w:+(n.started.when-t0).toFixed(6),o:n.started.off,
    d:+n.started.dur.toFixed(6),buf:n.buffer.name}))});
})();
""")
    assert out["q"] == [{"w": 0, "o": 25, "d": 5, "buf": "raw"},
                        {"w": 5, "o": 40, "d": 10, "buf": "raw"}], (
        f"очередь К1 не совпала с EDL камеры 1: {out}")


# --------------------------------------------------------------------------- #
# 2. К2: звук своей камеры, сдвинутый на её оффсет
# --------------------------------------------------------------------------- #
@node
def test_k2_queue_plays_camera2_shifted_by_its_offset() -> None:
    """К2 — звук камеры 2 из её буфера, со сдвигом delta[1] = 1,5 с от камеры 1.

    Что ломалось бы: К2 играла бы буфер камеры 1 или без оффсета — звук расходился бы с
    картинкой К2 на постоянную величину, а именно это человек и проверяет на раскладке.
    """
    out = _run_node("""
(async()=>{
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  CPV.audioCi=1; await eaOpenCam(CPV.xml,1); await tick();
  eaStart(5,CPV);
  const t0=EA.t0;
  report('k2',{q:live().map(n=>({w:+(n.started.when-t0).toFixed(6),o:n.started.off,
    d:+n.started.dur.toFixed(6),buf:n.buffer.name}))});
})();
""")
    assert out["q"] == [{"w": 0, "o": 26.5, "d": 5, "buf": "cam2"},
                        {"w": 5, "o": 41.5, "d": 10, "buf": "cam2"}], (
        f"очередь К2 не сдвинута на её оффсет или играет чужой буфер: {out}")


# --------------------------------------------------------------------------- #
# 3. Буфер К2 ещё не готов — тишина, а не звук камеры 1
# --------------------------------------------------------------------------- #
@node
def test_k2_before_its_buffer_is_silent_not_camera1() -> None:
    """Пока буфер К2 не декодирован, очередь пуста; приехал — `eaSync` пересобирает её на cam2.

    Что ломалось бы: до декодирования звучал бы буфер камеры 1 — человек выбрал К2, а слышал
    К1, то есть сверку синхрона делал бы по чужому звуку.
    """
    out = _run_node("""
(async()=>{
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  CPV.audioCi=1;
  eaStart(5,CPV);
  const before=live().map(n=>n.buffer.name);
  await eaOpenCam(CPV.xml,1); await tick();
  eaSync();
  report('k2wait',{before:before,after:live().map(n=>n.buffer.name)});
})();
""")
    assert out["before"] == [], f"до буфера К2 звучит чужой звук: {out}"
    assert out["after"] and set(out["after"]) == {"cam2"}, (
        f"очередь не пересобралась на буфер К2 после декодирования: {out}")


# --------------------------------------------------------------------------- #
# 4. Камера, включившаяся позже камеры 1: тишина до её нуля
# --------------------------------------------------------------------------- #
@node
def test_camera_that_starts_later_is_silent_before_its_zero() -> None:
    """Камера включилась на 22 с позже камеры 1 (delta = −22): отрезок с отрицательным
    началом звучит с отсрочкой, а не с отрицательного смещения.

    Что ломалось бы: `start(…, −2, …)` в браузере бросает RangeError — очередь звука рвалась
    бы на первом же куске раскладки с такой камерой.
    """
    out = _run_node("""
(async()=>{
  CPV.delta=[0,-22];CPV.audioCi=1;
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  await eaOpenCam(CPV.xml,1); await tick();
  eaStart(0,CPV);
  const t0=EA.t0;
  report('late',{q:live().map(n=>({w:+(n.started.when-t0).toFixed(6),o:n.started.off,
    d:+n.started.dur.toFixed(6)}))});
})();
""")
    q = out["q"]
    assert q and q[0] == {"w": 2, "o": 0, "d": 8}, (
        f"первый кусок не стартует с тишины на 2 с и со смещения 0: {out}")
    assert all(n["o"] >= 0 for n in q), f"start() получил отрицательное смещение: {out}"


# --------------------------------------------------------------------------- #
# 5. Смена камеры при игре: прежняя очередь гаснет, новая — из буфера К2
# --------------------------------------------------------------------------- #
@node
def test_switching_camera_rebuilds_the_queue() -> None:
    """Игра на К1 → переключили на К2: узлы К1 остановлены, очередь из cam2.

    Что ломалось бы: очередь К1 доигрывала бы поверх К2 — два звука сразу, и сверка
    синхрона по слуху невозможна.
    """
    out = _run_node("""
(async()=>{
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  eaStart(5,CPV);
  const old=live();
  CPV.audioCi=1; await eaOpenCam(CPV.xml,1); await tick();
  eaSync();
  report('switch',{oldStopped:old.every(n=>n.stopped),nowBufs:live().map(n=>n.buffer.name)});
})();
""")
    assert out["oldStopped"] is True, f"прежняя очередь звучит поверх новой: {out}"
    assert out["nowBufs"] and set(out["nowBufs"]) == {"cam2"}, (
        f"очередь не пересобрана на К2: {out}")


# --------------------------------------------------------------------------- #
# 6. Часы — звук: время монтажа по очереди, а не по <video>
# --------------------------------------------------------------------------- #
@node
def test_clock_gives_montage_time() -> None:
    """Через 2 с звука часы показывают монтаж 5 + 2 − EA_LEAD (звук стартовал с задержкой).

    Что ломалось бы: часы брали бы currentTime немого <video>, который догоняет звук и
    отстаёт на кадры — плейхед раскладки ехал бы не по звуку.
    """
    out = _run_node("""
(async()=>{
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  eaStart(5,CPV);
  ACTX.currentTime+=2;
  report('clock',{tm:eaClock().tm});
})();
""")
    assert abs(out["tm"] - (5 + 2 - _ea_lead())) < 1e-6, (
        f"часы раскладки не отдали время монтажа по звуку: {out}")


# --------------------------------------------------------------------------- #
# 7. Цензура превью: окна audio.censor стоят в очереди своим гейном
# --------------------------------------------------------------------------- #
@node
def test_censor_windows_mute_inside_the_queue() -> None:
    """Цензура превью — окна `audio.censor` плана стоят в очереди своим гейном.

    Раньше их глушил общий граф (`vgDuck`) на кадре; его убрали вместе с графом голоса, и
    этот тест — единственный сторож цензуры в превью. Окно монтажа [6,7] при очереди с 5 с:
    гейн первого узла уходит в 0 на 1 с (+ склейка EA_FADE) и возвращается к 1 на 2 с.

    Что ломалось бы: без гейна мат в превью звучал бы на окне, где в готовом ролике тишина.
    """
    out = _run_node("""
(async()=>{
  CPV.plan={audio:{censor:[[6,7]]}};
  EA.P=CPV; await eaOpen(CPV.xml); await tick();
  eaStart(5,CPV);
  const t0=EA.t0;
  const cen=GAINS.find(n=>n.gain.ev.some(e=>e[0]==='ramp'&&e[1]===0&&Math.abs(e[2]-(t0+1+EA_FADE))<1e-6));
  const g=live()[0].out[0];
  report('cens',{first:!!cen&&g.out[0]===cen,
    ev:cen?cen.gain.ev.map(e=>[e[0],e[1],+(e[2]-t0).toFixed(6)]):null});
})();
""")
    assert out["ev"] is not None, f"у узла очереди нет гейна цензуры: {out}"
    assert out["first"] is True, f"гейн цензуры стоит не в цепочке первого узла очереди: {out}"
    fade = _ea_fade()
    zeros = [t for k, v, t in out["ev"] if k == "ramp" and v == 0]
    ones = [t for k, v, t in out["ev"] if k == "ramp" and v == 1]
    assert any(abs(t - (1 + fade)) < 1e-6 for t in zeros), (
        f"нет перехода в 0 на 1 с + EA_FADE: {out}")
    assert any(abs(t - 2) < 1e-6 for t in ones), f"нет возврата в 1 на 2 с: {out}"
