# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Вставки с одним файлом не путаются между собой; многошаговая отмена любых правок вставок.

Два дефекта, пойманных воспроизведением у владельца:

1. **Дубли одного файла.** Вставка шага 3 (`c.job.ins`) не знала, из какой карточки шага 2
   (`c.inserts`) она собрана: связь искали по ПУТИ ФАЙЛА (`normInsPath` + `findIndex`), то
   есть всегда по ПЕРВОМУ совпадению. Три карточки с одним фото на 5/20/40 с: правка времени
   ВТОРОЙ (20→27) сдвигала ПЕРВУЮ (5→27) — после пересборки вставка «на 5 с» исчезала, а на
   27 с их оказывалось две. Тем же путём уезжали драг x/y, подсветка играющей карточки и
   ручные AE-подстройки (style/scale/sin/noexit), которые получали все дубли сразу.
   Лечится стабильным id карточки: `uid` у карточки (`insEnsureUids`), `cid` у вставки
   (`cardToIns`), поиск — `insCardFor` по id, а путь остаётся запасным ключом для легаси,
   причём каждая карточка достаётся не больше одной вставке.

2. **Отмена.** Была одна (`INS_UNDO`), и только на удаление. Теперь история на клип: снимок
   `{inserts, ins_rejected, insTarget, job_ins}`, стеки отмены/повтора глубиной 100, и пишет
   в неё ОДНА дверь — `insHistTouch`, которую зовёт `saveState`. Поэтому в историю попадает
   ЛЮБАЯ правка (удаление, тайминг, выбор файла, x/y, отбраковка), а не перечисленные вручную.

Тесты гоняют БОЕВЫЕ функции из `static/app/` под node с заглушками DOM (как
`tests/test_bulk_delete.py`): `ensureJobs`, `insCardFor`, `insSetSDCard`, драг кадра
(`$('ipvins').addEventListener('pointerdown', …)`), `insDel`, `insSetMedia`, `insUndo`/
`insRedo` и настоящие `stateObj`/`saveState` из 99-boot.js — иначе «убрать `insHistTouch`
из saveState» прошло бы мимо теста. Копий логики в тесте нет.

Запуск: py -3.10 -m pytest tests/test_ins_identity_undo.py -q
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

APP = os.path.join(ROOT, "static", "app")
JS85 = os.path.join(APP, "85-inserts-view.js")
JS80 = os.path.join(APP, "80-inserts.js")
JS90 = os.path.join(APP, "90-ae.js")
JS99 = os.path.join(APP, "99-boot.js")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции name из исходника (тот же приём, что в tests/test_wv_ins_timing.py)."""
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


def _slice(src, start, end):
    """Кусок исходника от start до end — так вырезается обработчик драга (стрелка, не функция)."""
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _line(src, needle):
    """Строка верхнего уровня с объявлением состояния (let/const) — им пользуются функции."""
    m = re.search(r"^.*%s.*$" % re.escape(needle), src, re.M)
    assert m, f"в исходнике нет объявления {needle}"
    return m.group(0)


# Заглушки — только внешние двери (DOM, сеть, тосты). Всё, что проверяется, — боевые функции.
# $ отдаёт элемент, который помнит навешенные обработчики: драг кадра регистрируется
# на загрузке файла, и вызвать его иначе нечем. ON.mbInserts — «окно вставок открыто»
# (по нему insHistClip решает, чьи правки писать в историю).
STUBS = r"""
var TOASTS=[],LOGS=[],ELS={},WINS={},ON={mbInserts:false};
var window={addEventListener:function(n,f){WINS[n]=f;},removeEventListener:function(n){delete WINS[n];}};
function El(id){this.id=id;this.style={};this.dataset={};this.disabled=false;this.textContent='';
  this.innerHTML='';this.children=[];this._ev={};this.clientWidth=1080;this.checked=false;
  this.classList={contains:function(n){return n==='on'&&!!ON[id];},
                  toggle:function(){},add:function(){},remove:function(){}};}
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.appendChild=function(c){this.children.push(c);return c;};
El.prototype.querySelector=function(){return null;};
El.prototype.getBoundingClientRect=function(){return {left:0,top:0,width:1080,height:1920};};
function $(id){if(!ELS[id])ELS[id]=new El(id);return ELS[id];}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,function(m,k){return String((vars||{})[k]);});}
function esc(s){return String(s==null?'':s);}
function uiLog(m){LOGS.push(String(m));}
function toast(m){TOASTS.push(String(m));}
function val(){return '';}
function rendEngine(){return 'ae';}
function nCams(){return 2;}
function vidStateObj(){return {};}
function srvStateSave(){}
function isPhotoPath(p){return /\.(png|jpe?g|webp|avif|gif|bmp)$/i.test(p||'');}
var localStorage={setItem:function(){},getItem:function(){return null;},removeItem:function(){}};
var AEGLOBAL='',AERENDER='',CAMDIRS=[],CAMFILES=[],CAMFROM=[],QUEUE=[],CUT_STAGES={},CUT_THRESHOLDS={};
var CURSTYLE=null,STEP=2;
var IPV={fps:60,dur:30,vids:[1],playing:false,plan:null,insShift:null,cur:-1};
var ITL={pps:20};
var CLIPS=[],curAE=-1,curIns=-1,IPVMODE='clips',INS=[];
function fmtIns(s){return String(s)+'s';}
function insLbl(){return 'ins';}
function insScrubInit(){}
function renderInsHost(){}
function syncClipLists(){}
function renderIns(){}
function itlDraw(){}
function ipvRefresh(){}
function ipvMarks(){}
function captureAE(){}
function ipvPlanSoon(){}
function ipvInsPlace(){}
function ipvIntroHitAt(){return null;}
function ipvIntroDragStart(){}
function ipvNow(){return 0;}
function ipvZoomAt(){return 1;}
function camFrameAxisK(){return {x:1,y:1};}
function openModal(){}
"""

# Помощники сценариев: карточка шага 2 ровно в том виде, в каком её правит интерфейс,
# и сериализация состояния вставок той же дверью, что и история.
HELPERS = r"""
function mkcard(media,s,d,over){
  const c={type:'photo',media:media,query:'q',start_sec:s,duration_sec:d,mosaic:false,plate:false,
           x:0,y:0,sc:100,mw:100,mh:100,sin:0};
  for(const k in (over||{}))c[k]=over[k];
  return c;}
function snap(){return JSON.stringify(insHistState(CLIPS[0]));}
"""


def _stand():
    """Боевые функции четырёх файлов интерфейса + обработчик драга кадра как есть."""
    ins = _read(JS85)
    ae = _read(JS90)
    a80 = _read(JS80)
    boot = _read(JS99)
    parts = [STUBS]
    parts.append(_slice(ins, "$('ipvins').addEventListener('pointerdown'",
                        "$('ipvins').addEventListener('dblclick'"))
    parts += [_func(ins, n) for n in (
        "normInsPath", "insEnsureUids", "insCardKey", "cardToIns", "insCardFor",
        "insSetSDCard", "insSetSD", "ipvIns", "ipvAfterEdit", "axisLock")]
    parts += [_func(ae, n) for n in ("defJob", "ensureJobs")]
    parts += [_func(a80, n) for n in (
        "insSetMedia", "insDel", "insHistFor", "insHistState", "insHistClip", "insHistTouch",
        "insHistReset", "insUndoUI", "insHistApply", "insUndo", "insRedo")]
    # stateObj + saveState — одним куском: saveState и есть дверь истории, без неё
    # «убрать insHistTouch из saveState» тест бы не заметил.
    parts.append(_slice(boot, "function stateObj()", "function srvStateSave("))
    parts += [_func(a80, "insKind"),
              _line(ins, "INSUIDSEQ=0"), _line(a80, "INS_HIST={"), _line(a80, "INS_HIST_APPLY"),
              _line(boot, "LSKEY="), _line(boot, "LSWARNED=false")]
    return "\n".join(parts)


def _run(tmp_path, name, body):
    """Стенд + сценарий; вернуть разобранный JSON (последняя строка stdout)."""
    script = _stand() + "\n" + HELPERS + "\n" + body
    path = str(tmp_path / name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


def _three_same():
    """Три карточки ОДНОГО файла — сценарий, на котором дефект воспроизводился."""
    return ("const M='C:\\\\m\\\\same.png';\n"
            "CLIPS=[{xml:'c.xml',name:'clip',inserts:[mkcard(M,5,2),mkcard(M,20,3),mkcard(M,40,4)]}];\n"
            "curAE=0;curIns=-1;IPVMODE='ae';INS=[];\n")


@node
def test_same_file_cards_keep_distinct_ids_and_the_second_edit_hits_the_second_card(tmp_path):
    """1. Три карточки с одним путём: у вставок шага 3 РАЗНЫЕ cid, а правка тайминга второй
    меняет только вторую карточку.

    На main здесь стоял поиск по пути, и `insSetSDCard` второй вставки писал в ПЕРВУЮ
    карточку: 5 с превращались в 27, вставка «на 5 с» исчезала, на 27 с их становилось две.
    """
    out = _run(tmp_path, "id_second.js", _three_same() + r"""
ensureJobs();
const ins=CLIPS[0].job.ins;
const cids=ins.map(x=>x.cid||'');
const uids=CLIPS[0].inserts.map(x=>x.uid||'');
insSetSDCard(ins[1],27,7);                       // правка ВТОРОЙ вставки
console.log(JSON.stringify({cids,uids,
  bound:ins.map((x,i)=>x.cid===CLIPS[0].inserts[i].uid),
  starts:CLIPS[0].inserts.map(x=>x.start_sec),
  durs:CLIPS[0].inserts.map(x=>x.duration_sec)}));
""")

    assert all(out["cids"]), f"у вставок шага 3 нет cid: {out['cids']}"
    assert len(set(out["cids"])) == 3, (
        "у вставок с одним файлом одинаковые cid — связь с карточкой не различить: %r" % out["cids"])
    assert out["uids"] and len(set(out["uids"])) == 3, (
        "карточки шага 2 не получили разные uid: %r" % out["uids"])
    assert out["bound"] == [True, True, True], (
        "cid вставки не совпал с uid своей карточки: %r" % out)
    assert out["starts"] == [5, 27, 40], (
        "правка тайминга второй вставки уехала не в свою карточку: %r" % out["starts"])
    assert out["durs"] == [2, 7, 4], (
        "длительность правленой вставки уехала в чужую карточку: %r" % out["durs"])


@node
def test_frame_drag_of_second_insert_writes_into_the_second_card(tmp_path):
    """2. Драг x/y второй вставки пишет во ВТОРУЮ карточку (первая и третья — как были).

    Обработчик берётся из боевого файла целиком: он ключуется на `insCardFor`, а тот — на cid.
    """
    out = _run(tmp_path, "id_drag.js", _three_same() + r"""
ensureJobs();
INS=CLIPS[0].job.ins.map(x=>Object.assign({},x));      // как openAEPreview
IPV.plan={w:1080,inserts:INS.filter(r=>(r.media||'').trim())
  .map((r,i)=>Object.assign({},r,{start:i,end:i+2}))};
const wr={dataset:{ins:'1'},clientWidth:1080};
ELS['ipvins']._ev.pointerdown[0]({clientX:100,clientY:100,shiftKey:false,
  preventDefault:function(){},stopPropagation:function(){},target:{closest:function(){return wr;}}});
WINS['pointermove']({clientX:140,clientY:100,shiftKey:false});
WINS['pointerup']({clientX:140,clientY:100,shiftKey:false});
console.log(JSON.stringify({xs:CLIPS[0].inserts.map(x=>x.x),ys:CLIPS[0].inserts.map(x=>x.y)}));
""")

    assert out["xs"] == [0, 40, 0], (
        "драг второй вставки записал x не в свою карточку: %r" % out["xs"])
    assert out["ys"] == [0, 0, 0], "драг тронул y чужих карточек: %r" % out["ys"]


@node
def test_manual_ae_adjustments_survive_rebuild_without_leaking_to_duplicates(tmp_path):
    """3. Ручные подстройки (scale, style) второй вставки переживают ensureJobs и НЕ
    копируются на первую и третью.

    На main `tw` в ensureJobs собирался по пути файла: подстройки первой записи доставались
    всем дублям — своей правки вторая вставка не сохраняла.
    """
    out = _run(tmp_path, "id_adjust.js", _three_same() + r"""
ensureJobs();
CLIPS[0].job.ins[1].scale=77;CLIPS[0].job.ins[1].style='cam1';   // правка в панели шага 3
ensureJobs();                                                    // переоткрытие превью
console.log(JSON.stringify({ins:CLIPS[0].job.ins.map((x,i)=>[x.scale,x.style,x.cid===CLIPS[0].inserts[i].uid]),
  uid:CLIPS[0].inserts.map(x=>x.uid||'')}));
""")

    assert out["ins"][1][:2] == [77, "cam1"], (
        "ручные подстройки второй вставки не пережили ensureJobs: %r" % out["ins"])
    assert out["ins"][0][:2] == [44, "cam2"] and out["ins"][2][:2] == [44, "cam2"], (
        "подстройки второй вставки скопировались на дубли того же файла: %r" % out["ins"])
    assert out["ins"] == [[44, "cam2", True], [77, "cam1", True], [44, "cam2", True]], out["ins"]


@node
def test_legacy_inserts_without_ids_get_different_cards(tmp_path):
    """4. Легаси без uid/cid: две вставки с одним путём сопоставляются с РАЗНЫМИ карточками.

    Запасной путь («первая карточка с этим путём, ещё не занятая другой вставкой») обязан
    расходовать карточки по одной, а не отдавать всем дублям первую — и запоминать связь
    в самой вставке, чтобы следующий поиск был уже точным.
    """
    out = _run(tmp_path, "id_legacy.js", r"""
const M='C:\\m\\same.png';
ON.mbInserts=false;
CLIPS=[{xml:'c.xml',inserts:[mkcard(M,5,2),mkcard(M,20,2)]}];
curAE=0;curIns=-1;IPVMODE='ae';
INS=[{media:M},{media:M}];
const a=insCardFor(INS[0]);
const b=insCardFor(INS[1]);
console.log(JSON.stringify({
  same:a===b,firstIsCard0:a===CLIPS[0].inserts[0],secondIsCard1:b===CLIPS[0].inserts[1],
  cid0:INS[0].cid||'',cid1:INS[1].cid||'',
  uid0:CLIPS[0].inserts[0].uid||'',uid1:CLIPS[0].inserts[1].uid||''}));
""")

    assert not out["same"], "две легаси-вставки с одним путём достались одной карточке"
    assert out["firstIsCard0"], "первая вставка не взяла первую карточку"
    assert out["secondIsCard1"], "вторая вставка не взяла вторую карточку"
    assert out["cid0"] == out["uid0"] and out["cid1"] == out["uid1"], (
        "связь легаси-вставки с карточкой не запомнилась (следующий поиск снова по пути): %r" % out)
    assert out["uid0"] and out["uid0"] != out["uid1"], "карточкам не выдали разные uid"


@node
def test_undo_redo_any_insert_edit_and_the_depth_cap(tmp_path):
    """5. Пять разных правок → пять «отменить» = исходное состояние; два «повторить» =
    состояние после второй правки; новая правка после отмены очищает повтор; глубина 100.

    Правки идут через БОЕВЫЕ двери (insDel, insSetSD — тайминг шага 2, insSetMedia — выбор
    файла) и через те поля, которые правит драг (x/y) и отбраковка (ins_rejected). В историю
    их пишет одна дверь — insHistTouch из настоящего saveState.
    """
    out = _run(tmp_path, "undo_multi.js", r"""
const M='C:\\m\\same.png';
ON.mbInserts=true;IPVMODE='clips';curIns=0;curAE=-1;
CLIPS=[{xml:'c.xml',name:'clip',inserts:[mkcard(M,5,2),mkcard(M,20,3),mkcard(M,40,4),
  mkcard('C:/m/four.png',60,2,{query:'sunset'})]}];
INS=[];
ensureJobs();                                             // job.ins в снимке тоже
insHistReset(CLIPS[0]);                                    // как открытие окна вставок
const S0=snap();
insDel(3);saveState();                                     // 1. удаление карточки с запросом
const S1=snap();
insSetSD(CLIPS[0].inserts[1],27,7);saveState();            // 2. тайминг (правка блока)
const S2=snap();
insSetMedia(CLIPS[0].inserts[0],'C:/m/other.png');saveState();   // 3. выбор файла
const S3=snap();
CLIPS[0].inserts[1].x=42;CLIPS[0].inserts[1].y=-7;saveState();   // 4. x/y — то, что пишет драг
const S4=snap();
CLIPS[0].ins_rejected=[{type:'photo',query:'sunset'}];saveState();   // 5. отбраковка
const S5=snap();
const depth5=INS_HIST['c.xml'].undo.length;
for(let i=0;i<5;i++)insUndo();
const afterUndo=snap(),undoLeft=INS_HIST['c.xml'].undo.length,redoLen=INS_HIST['c.xml'].redo.length;
insRedo();insRedo();
const afterRedo=snap();
insUndo();
CLIPS[0].inserts[0].x=9;saveState();                       // новая правка убивает повтор
const redoAfterNew=INS_HIST['c.xml'].redo.length;
for(let i=0;i<120;i++){CLIPS[0].inserts[0].x=i+100;saveState();}
console.log(JSON.stringify({S0,S2,S5,afterUndo,undoLeft,redoLen,afterRedo,redoAfterNew,
  depth5,depthCap:INS_HIST['c.xml'].undo.length,
  undoDisabled:$('insUndoBtn').disabled,redoDisabled:$('insRedoBtn').disabled,logs:LOGS}));
""")

    assert out["depth5"] == 5, (
        "пять разных правок не дали пяти шагов истории: %r" % out["depth5"])
    assert out["afterUndo"] == out["S0"], (
        "пять «отменить» не вернули исходное состояние вставок:\n%r\n%r"
        % (out["afterUndo"], out["S0"]))
    assert out["undoLeft"] == 0, "стек отмены не опустошился"
    assert out["redoLen"] == 5, "в повтор не уехали все пять отменённых состояний"
    assert out["afterRedo"] == out["S2"], (
        "два «повторить» не вернули состояние после второй правки:\n%r\n%r"
        % (out["afterRedo"], out["S2"]))
    assert out["redoAfterNew"] == 0, "новая правка после отмены не очистила стек повтора"
    assert out["depthCap"] == 100, "глубина истории не ограничена сотней: %r" % out["depthCap"]
    assert out["undoDisabled"] is False and out["redoDisabled"] is True, (
        "кнопки отмены/повтора не отражают состояние стеков: %r" % out)
    assert any("отменена" in m for m in out["logs"]) and any("повторена" in m for m in out["logs"]), (
        "отмена и повтор не пишут в журнал: %r" % out["logs"])


@node
def test_applying_a_snapshot_does_not_write_to_history(tmp_path):
    """6. Применение отката само в историю не пишет.

    Иначе «отменить» навсегда оставляло бы в стеке отмены применённое состояние (и повтор
    было бы нечем очистить): после отката стек отмены пуст, в повторе — ровно один шаг.
    """
    out = _run(tmp_path, "undo_apply.js", r"""
const M='C:\\m\\same.png';
ON.mbInserts=true;IPVMODE='clips';curIns=0;curAE=-1;
CLIPS=[{xml:'c.xml',name:'clip',inserts:[mkcard(M,5,2)]}];
INS=[];
ensureJobs();
insHistReset(CLIPS[0]);
CLIPS[0].inserts[0].x=5;saveState();                       // одна правка
const u0=INS_HIST['c.xml'].undo.length;
insUndo();
const u1=INS_HIST['c.xml'].undo.length,r1=INS_HIST['c.xml'].redo.length,x1=CLIPS[0].inserts[0].x;
const lastOk=INS_HIST_LAST['c.xml']===snap();
insRedo();
console.log(JSON.stringify({u0,u1,r1,x1,lastOk,
  u2:INS_HIST['c.xml'].undo.length,r2:INS_HIST['c.xml'].redo.length,x2:CLIPS[0].inserts[0].x}));
""")

    assert out["u0"] == 1, "правка не попала в историю: %r" % out["u0"]
    assert out["u1"] == 0, "применение отката записалось в историю (стек отмены не пуст)"
    assert out["r1"] == 1, "отменённое состояние не ушло в повтор: %r" % out["r1"]
    assert out["x1"] == 0, "откат не вернул поле карточки: %r" % out["x1"]
    assert out["lastOk"], "после отката последний снимок истории не совпал с состоянием"
    assert out["u2"] == 1 and out["r2"] == 0 and out["x2"] == 5, (
        "повтор не вернул правку или снова записался в историю: %r" % out)


def test_the_recording_door_is_save_state_and_the_window_resets_history():
    """Проводка истории: пишет только insHistTouch, её зовёт saveState, окно сбрасывает стек.

    Поведенческие тесты выше гоняют боевой saveState, поэтому здесь — только имена дверей:
    без них история либо не пишется вовсе, либо тащит чужие правки после смены клипа.
    """
    boot = _read(JS99)
    save = _slice(boot, "function saveState()", "function srvStateSave(")
    assert "insHistTouch()" in save, (
        "saveState больше не зовёт insHistTouch — ни одна правка вставок не попадёт в историю")

    a80 = _read(JS80)
    open_ins = _func(a80, "openInsertsFor")
    assert "insEnsureUids(CLIPS[i])" in open_ins, "окно вставок не выдаёт карточкам uid"
    assert "insHistReset(CLIPS[i])" in open_ins, "окно вставок не сбрасывает историю на клипе"
    open_ae = a80[a80.index("function openAEPreview()"):]
    open_ae = open_ae[:open_ae.index("\n// ===== панель слов")]
    assert "insHistReset(CLIPS[curAE])" in open_ae, "превью шага 3 не сбрасывает историю на клипе"

    ins85 = _read(JS85)
    drag = ins85[ins85.index("$('ipvins').addEventListener('pointerdown'"):
                 ins85.index("$('ipvins').addEventListener('dblclick'")]
    assert "insCardFor(INS[real])" in drag, (
        "драг кадра ищет карточку мимо общего insCardFor — дубли одного файла снова спутаются")

    html = _read(os.path.join(ROOT, "templates", "index.html"))
    assert 'id="insRedoBtn"' in html and 'onclick="insRedo()"' in html, (
        "в окне вставок нет кнопки повтора")
    assert "Ctrl+Shift+Z" in html and "insUndo()" in html, (
        "кнопки отмены/повтора не подписаны горячими клавишами")
