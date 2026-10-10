# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Поведенческие проверки интерфейса на стенде node (серия B3, порция 1).

ПОЧЕМУ этот файл. В tests/test_ui_static.py большинство проверок читает ТЕКСТ
static/app/*.js регулярками: «в функции X есть вызов Y». Такой тест зелёный при
сломанном поведении (вызов стоит, но не на том клипе) и красный при безобидном
переименовании локальной переменной. Здесь то же самое проверяется ПОВЕДЕНИЕМ:
боевая функция вырезается из исходника по балансу скобок (строки и комментарии
пропускаются), окружение подменяется заглушками, и смотрим, что записалось в
состояние, что ушло на сервер и что показалось.

Стенд НЕ копирует правила: если в исходнике что-то поменяется, стенд исполнит
новое. Заглушки — только внешние двери (DOM, сеть, таймеры, диалоги).

Мутация поведения (вызов убран, поле поменяли) — тест краснеет. Переименование
локальной переменной внутри той же функции — зелёный. Так проверено при переводе.

Запуск: py -3.10 -m pytest tests/test_ui_behavior.py -q
"""
from __future__ import annotations

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

from core import app_meta  # noqa: E402

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="поведенческий стенд интерфейса требует node в PATH")

# Окружение, общее для всех сценариев. Внешние двери — DOM ($), состояние клипов,
# диалоги и журнал. Всё, что нужно только одному сценарию, — в его `pre`.
BASE = r"""
const ELS={};
function $(id){if(!ELS[id])ELS[id]={id:id,value:'',checked:false,disabled:false,textContent:'',
  innerHTML:'',className:'',style:{},dataset:{},focus(){},closest(){return null;},
  querySelector(){return null;},querySelectorAll(){return [];}};return ELS[id];}
function val(id){const v=$(id).value;return v==null?'':String(v);}
let SAVES=0;const TOASTS=[],LOGS=[],FETCHES=[];
function saveState(){SAVES++;}
function toast(m){TOASTS.push(String(m));}
function uiLog(m){LOGS.push(String(m));}
function t(s,v){let r=String(s);if(v)for(const k in v)r=r.split('{'+k+'}').join(String(v[k]));return r;}
function errText(e){return String((e&&(e.error||e.message))||e||'');}
function nCams(){return 2;}
let CLIPS=[],curAE=-1,AEXML='',INS=[],INTRO=[],HL=[],BRK=[],CNT=[],JNS=[],HLXML='';
let STYLES={},CURSTYLE=null,STYLE_EDITING=null,AERENDER='';
function introResolve(){return null;}
function styleDirty(){return false;}
function ipvCalcStyleChanged(){}
"""

# Заглушки сети: запрос записывается в FETCHES, ответ — пустой успешный.
FETCH_STUB = r"""
function fetch(url,opts){FETCHES.push({url:String(url),body:opts&&opts.body});
  return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,key:'Ан',path:'p',error:'',
    subs:1,colored:0,ncams:2})});}
"""

# Заглушка с задержкой: нужна, чтобы два обхода статусов реально перекрылись.
FETCH_SLOW = r"""
function fetch(url,opts){FETCHES.push({url:String(url)});
  return new Promise(r=>setTimeout(()=>r({ok:true,json:()=>Promise.resolve({subs:1,colored:0,ncams:2})}),5));}
"""

# Заглушки очереди клипов для сценариев, где их списки перерисовываются.
QUEUE_PRE = r"""
let SEL_ANCHOR=-1,curEdit=-1,curIns=-1,ASK=[];
function renderClips1(){}function renderClips2(){}function renderClips3(){}
function syncNav(){}
async function askConfirm(m){ASK.push(String(m));return true;}
"""


def _block_end(text: str, i: int) -> int:
    """Индекс за закрывающей скобкой блока, открытого в text[i] == '{'.

    Строковые литералы и комментарии пропускаются: в комментариях исходника
    бывают скобки и кавычки, которые регулярка-счётчик приняла бы за код.
    """
    depth = 0
    j, n = i, len(text)
    while j < n:
        ch = text[j]
        if ch in "'\"`":
            q = ch
            j += 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            j += 1
            continue
        if text.startswith("//", j):
            k = text.find("\n", j)
            j = n if k < 0 else k + 1
            continue
        if text.startswith("/*", j):
            j = text.find("*/", j) + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise AssertionError("не сошлись скобки блока")


def _fn(name: str) -> str:
    """Боевая `[async] function name(...){...}` из static/app/*.js, целиком."""
    text = app_meta.app_js_text()
    rx = re.compile(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name))
    found = list(rx.finditer(text))
    assert len(found) == 1, "функция %s объявлена %d раз(а)" % (name, len(found))
    m = found[0]
    return text[m.start():_block_end(text, text.index("{", m.end() - 1))]


def _run(tmp_path, name: str, script: str) -> dict:
    """Прогнать сценарий под node; вернуть JSON из последней строки вывода."""
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


def _case(tmp_path, name: str, names: tuple[str, ...], body: str, pre: str = "") -> dict:
    """Собрать сценарий: окружение + заглушки `pre` + боевые функции + тело.

    В теле пишем результаты в `out`; сценарий асинхронный (await допустим).
    """
    fns = "\n\n".join(_fn(n) for n in names)
    script = (BASE + pre + "\n" + fns + "\n"
              "(async()=>{const out={};\n" + body +
              "\nconsole.log(JSON.stringify(out));})()"
              ".catch(e=>{console.error(e&&e.stack||e);process.exit(1);});\n")
    return _run(tmp_path, name, script)


# ====================== 1. ПАНЕЛЬ AE ПИШЕТСЯ ТОЛЬКО В СВОЙ КЛИП ======================
@node
def test_behav_capture_ae_does_not_write_a_panel_of_another_clip(tmp_path):
    """Панель AE пишется только в клип, который в ней загружен (AEXML).

    Пока панель не принадлежит клипу, его задание не трогаем, а состояние
    сохраняем. Пойманный случай: запись дефолтной панели стирала ИИ-вставки клипа.
    """
    res = _case(tmp_path, "b3_capture_guard.js", ("defJob", "captureAE"), r"""
CLIPS=[{xml:'A.xml',name:'A',job:defJob(),status:{}},{xml:'B.xml',name:'B',job:defJob(),status:{}}];
curAE=0;INS=[{media:'x.jpg'}];
AEXML='B.xml';                       // в панели загружен другой клип
const before=JSON.stringify(CLIPS[0].job);
const s0=SAVES;
captureAE();
out.foreignKept=JSON.stringify(CLIPS[0].job)===before;
out.foreignSaved=SAVES-s0;
AEXML='A.xml';                       // теперь панель принадлежит A
captureAE();
out.ownIns=(CLIPS[0].job.ins||[]).map(x=>x.media);
""")
    assert res.get("foreignKept") is True, res
    assert res.get("foreignSaved") == 1, res
    assert res.get("ownIns") == ["x.jpg"], res


# ============== 2. ИМЕНОВАННЫЙ СТИЛЬ — ИМЯ, А НЕ КОПИЯ (ЗАДАНИЕ КЛИПА) ==============
@node
def test_behav_capture_ae_keeps_named_style_as_a_name_not_a_copy(tmp_path):
    """Клип с именованным стилем хранит ИМЯ стиля; копия — только у «грязного» стиля.

    Если правки стиля не сохранены, клип уходит кастомом с копией правок: иначе они
    пропадут при переходе на другой клип. Сохранили — копии нет, клип снова ссылается
    на имя, и правка шаблона доедет до сборки.
    """
    res = _case(tmp_path, "b3_capture_style.js", ("defJob", "captureAE"), r"""
CLIPS=[{xml:'A.xml',name:'A',job:defJob(),status:{}}];
STYLES={stA:{label:'stA'}};curAE=0;AEXML='A.xml';
$('style').value='stA';CURSTYLE={font:'Copy'};
captureAE();
out.named={key:CLIPS[0].job.styleKey,copy:CLIPS[0].job.style==null?null:CLIPS[0].job.style};
styleDirty=()=>true;
captureAE();
out.dirty={key:CLIPS[0].job.styleKey,copy:(CLIPS[0].job.style||{}).font};
""")
    assert res["named"] == {"key": "stA", "copy": None}, res
    assert res["dirty"] == {"key": "__custom__", "copy": "Copy"}, res


# ============== 3. СЛУЖЕБНЫЙ РЕЖИМ ПРАВКИ НЕ УХОДИТ В ЗАДАНИЕ ==============
@node
def test_behav_capture_ae_never_saves_the_edit_mode_marker(tmp_path):
    """В задание попадает имя редактируемого шаблона, а не служебное `__edit__`.

    Иначе сборка не нашла бы стиль с таким именем, а удаление слало бы на сервер
    несуществующее имя.
    """
    res = _case(tmp_path, "b3_capture_edit.js", ("defJob", "captureAE"), r"""
CLIPS=[{xml:'A.xml',name:'A',job:defJob(),status:{}}];
STYLES={stA:{label:'stA'}};curAE=0;AEXML='A.xml';CURSTYLE={};
$('style').value='__edit__';STYLE_EDITING='stA';
captureAE();
out.edit=CLIPS[0].job.styleKey;
out.leak=JSON.stringify(CLIPS[0].job).indexOf('__edit__')>=0;
STYLE_EDITING=null;
captureAE();
out.noName=CLIPS[0].job.styleKey;
""")
    assert res["edit"] == "stA", res
    assert res["leak"] is False, res
    assert res["noName"] is None, res


# ============== 4. СБОРКА ШЛЁТ ИМЯ СТИЛЯ, А НЕ КОПИЮ ==============
@node
def test_behav_build_payload_sends_style_name_not_copy(tmp_path):
    """В payload сборки для именованного стиля уходит имя; копия — только у кастома.

    Рото из задания в payload не попадает: оно живёт в стиле.
    """
    res = _case(tmp_path, "b3_build_payload.js", ("styleForJob", "jobForBuild", "defJob", "clipNcams"), r"""
STYLES={stA:{label:'stA'}};
const cNamed={xml:'A.xml',job:Object.assign(defJob(),{styleKey:'stA'})};
const cCustom={xml:'B.xml',job:Object.assign(defJob(),{styleKey:'__custom__',style:{font:'Copy'}})};
const pn=jobForBuild(cNamed),pc=jobForBuild(cCustom);
out.named=pn.style;
out.custom=pc.style;
out.rotoKeys=Object.keys(pn).filter(k=>/roto/.test(k));
""", pre="function musicJobFields(c){return {};}\nfunction effOutdir(c){return '';}")
    assert res["named"] == "stA", res
    assert res["custom"] == {"font": "Copy"}, res
    assert res["rotoKeys"] == [], res


# ============== 5. СМЕШАННЫЕ СПИКЕРЫ ГАСЯТ «СОБРАТЬ НАБОР» ==============
@node
def test_behav_mixed_speakers_disable_the_set_build(tmp_path):
    """Клипы двух спикеров не собираются одним набором: кнопка и «Один на всё» гаснут.

    Когда спикер один, оба снова доступны.
    """
    res = _case(tmp_path, "b3_mixed_build.js", ("syncBuildBtn", "clipSpeaker", "selClips", "introAllCount"), r"""
CLIPS=[{xml:'A.xml',name:'A',sel:true,job:{speaker:'spkA'},status:{}},
       {xml:'B.xml',name:'B',sel:true,job:{speaker:'spkB'},status:{}}];
syncBuildBtn();
out.mixed={btn:$('buildbtn').disabled,combined:CO.disabled};
CLIPS[1].job.speaker='spkA';
syncBuildBtn();
out.one={btn:$('buildbtn').disabled,combined:CO.disabled};
""", pre=r"""
const SEP={checked:false,disabled:false},CO={checked:false,disabled:false};
const document={querySelector:(s)=>(s.indexOf('separate')>=0?SEP:CO),querySelectorAll:()=>[]};
function segUI(){}
""")
    assert res["mixed"] == {"btn": True, "combined": True}, res
    assert res["one"] == {"btn": False, "combined": False}, res


# ============== 6. ГРОМКОСТЬ ГЛУШИТСЯ ТОЛЬКО ВНУТРИ ОКНА ЦЕНЗУРЫ ==============
@node
def test_behav_voice_is_muted_only_inside_censor_windows_of_the_plan(tmp_path):
    """Голос предпросмотра уходит в ноль внутри окна цензуры плана и возвращается вне её.

    Без плана голос идёт на обычной громкости стиля, плеер не ломается.
    """
    res = _case(tmp_path, "b3_vg_duck.js", ("vgDuck",), r"""
CURSTYLE={voice_db:-6};
const plan={audio:{censor:[[2,3]]}};
vgDuck(2.5,plan);out.inside=VG.gain.value;
vgDuck(4,plan);out.outside=Math.round(VG.gain.value*1000)/1000;
vgDuck(4,null);out.noPlan=Math.round(VG.gain.value*1000)/1000;
""", pre="let VG={gain:{value:1}};\nfunction dbToGain(db){return Math.pow(10,db/20);}")
    assert res["inside"] == 0, res
    assert res["outside"] == 0.501, res
    assert res["noPlan"] == 0.501, res


# ============== 7. ДУБЛЁР ДОГОНЯЕТ СТЫК СКОРОСТЬЮ ==============
@node
def test_behav_double_buffer_catches_up_to_the_seam_by_rate(tmp_path):
    """Дублёр приходит на стык кадр в кадр: скорость = «сколько медиа осталось» / «сколько времени».

    Скорость ограничена [0.25; 2.5]; дублёр немой и запускается на проигрывание.
    """
    res = _case(tmp_path, "b3_buf_roll.js", ("bufRoll",), r"""
const mk=(cur,rate)=>({at:10,rate:rate?(()=>rate):null,rolling:false,
  el:{seeking:false,readyState:4,currentTime:cur,playbackRate:1,muted:false,
      play(){return Promise.resolve();}}});
const b1=mk(9.5,null);bufRoll(b1,0.25);
out.rate=b1.el.playbackRate;out.muted=b1.el.muted;out.rolling=b1.rolling;
const b2=mk(9.0,null);bufRoll(b2,0.25);out.clampMax=b2.el.playbackRate;
const b3=mk(9.5,0.5);bufRoll(b3,0.25);out.withRate=b3.el.playbackRate;
const b4=mk(9.9,null);bufRoll(b4,0.5);out.clampMin=b4.el.playbackRate;
""", pre="const PV_PREROLL=0.5;")
    assert res["rate"] == 2, res
    assert res["clampMax"] == 2.5, res
    assert res["withRate"] == 1, res
    assert res["clampMin"] == 0.25, res
    assert res["muted"] is True and res["rolling"] is True, res


# ============== 8. МЕТЛА СПИСКА НЕ ТРОГАЕТ ДИСК ==============
@node
def test_behav_clear_list_removes_clips_from_the_list_only(tmp_path):
    """Метла «очистить список» убирает все клипы из состояния, но не стирает файлы.

    Подтверждение спрашивается ровно один раз, индекс открытого клипа сбрасывается.
    """
    res = _case(tmp_path, "b3_clear_list.js", ("clearClipsList", "_spliceClip"), r"""
CLIPS=[{xml:'A.xml',name:'A',inserts:[],status:{},job:defJob()},{xml:'B.xml',name:'B',inserts:[],status:{}}];
curAE=1;
await clearClipsList();
out.left=CLIPS.length;
out.disk=FETCHES.filter(f=>/clip_delete/.test(f.url)).length;
out.asks=ASK.length;
out.curAE=curAE;
out.saved=SAVES>0;
""", pre=FETCH_STUB + QUEUE_PRE + "function defJob(){return {};}\n")
    assert res["left"] == 0, res
    assert res["disk"] == 0, res
    assert res["asks"] == 1, res
    assert res["curAE"] == -1, res
    assert res["saved"] is True, res


# ============== 9. «УБРАТЬ ОТМЕЧЕННЫЕ» — ТОЛЬКО ОТМЕЧЕННЫЕ, БЕЗ ДИСКА ==============
@node
def test_behav_remove_selected_drops_only_checked_clips_and_keeps_open_one(tmp_path):
    """Метла «убрать отмеченные» удаляет только клипы с галочкой, файлы на диске остаются.

    Открытый клип остаётся открытым, даже когда его индекс сдвинулся.
    """
    res = _case(tmp_path, "b3_remove_sel.js", ("removeSelClips", "_spliceClip"), r"""
CLIPS=[{xml:'A.xml',name:'A',sel:false,status:{}},{xml:'B.xml',name:'B',sel:true,status:{}},
       {xml:'C.xml',name:'C',sel:false,status:{}},{xml:'D.xml',name:'D',sel:true,status:{}}];
curAE=2;
await removeSelClips();
out.left=CLIPS.map(c=>c.xml);
out.disk=FETCHES.filter(f=>/clip_delete/.test(f.url)).length;
out.curAE=curAE;
out.asks=ASK.length;
""", pre=FETCH_STUB + QUEUE_PRE)
    assert res["left"] == ["A.xml", "C.xml"], res
    assert res["disk"] == 0, res
    assert res["curAE"] == 1, res
    assert res["asks"] == 1, res


# ============== 10. УДАЛЕНИЕ ВСТАВКИ ЗАПОМИНАЕТ ЗАПРОС И СОХРАНЯЕТСЯ ==============
@node
def test_behav_deleting_an_insert_remembers_its_query_and_saves(tmp_path):
    """Удалённая вставка с запросом попадает в отбраковку; без запроса — нет. Каждое удаление сохраняет."""
    res = _case(tmp_path, "b3_insdel.js", ("insDel",), r"""
CLIPS=[{xml:'A.xml',inserts:[{type:'photo',start_sec:1,query:'кот'},{type:'photo',start_sec:2,query:''}]}];
curIns=0;
const s0=SAVES;
insDel(0);
out.rej=CLIPS[0].ins_rejected;
out.left=CLIPS[0].inserts.length;
out.saved=SAVES-s0;
out.render=R;
insDel(0);
out.rejCount=(CLIPS[0].ins_rejected||[]).length;
out.savedAll=SAVES-s0;
""", pre="let curIns=0,R=0,S=0;\nfunction renderInsHost(){R++;}\nfunction syncClipLists(){S++;}\n")
    assert res["rej"] == [{"type": "photo", "start_sec": 1, "query": "кот"}], res
    assert res["left"] == 1, res
    assert res["saved"] == 1 and res["render"] == 1, res
    assert res["rejCount"] == 1, res
    assert res["savedAll"] == 2, res


# ============== 11. КНОПКИ РАЗМЕТКИ ИДУТ ПО ВЫБРАННЫМ КЛИПАМ ==============
@node
def test_behav_markup_buttons_run_on_selected_clips_with_the_right_phases(tmp_path):
    """«Разметить всё» и фазы идут по отмеченным клипам; пусто у всех — по всем.

    «Разметить всё» молча пропускает готовое (ask=false), фаза — спрашивает (ask=true).
    """
    res = _case(tmp_path, "b3_markup_buttons.js", ("markupAll", "markupPhase", "selClips"), r"""
CLIPS=[{name:'A',sel:true},{name:'B',sel:false}];
await markupAll();
await markupPhase('yellow');
CLIPS[0].sel=false;
await markupAll();
out.all=MARK[0];out.phase=MARK[1];out.none=MARK[2];
""", pre=r"""
let MARK=[];
function uiBusyGuard(){return false;}function uiBusySet(){}function progOpen(){}
function markupAllRun(subeng,list,phases,ask){
  MARK.push({subeng:subeng,names:list.map(c=>c.name),phases:phases,ask:ask===true});}
""")
    assert res["all"] == {"subeng": "whisper", "names": ["A"],
                          "phases": ["subs", "yellow", "inserts"], "ask": False}, res
    assert res["phase"] == {"subeng": "whisper", "names": ["A"],
                            "phases": ["yellow"], "ask": True}, res
    assert res["none"]["names"] == ["A", "B"], res


# ============== 12. «ОСТАНОВИТЬ» ГАСИТ ВИДЕО, А НЕ ПОСТАВЛЕННУЮ ЗАДАЧУ ==============
@node
def test_behav_cancel_stops_video_generation_and_not_the_job(tmp_path):
    """Кнопка «Остановить» при генерации видео зовёт отмену видео и не трогает /api/cancel.

    Без видео — остановка идёт обычными запросами к серверу.
    """
    res = _case(tmp_path, "b3_cancel.js", ("cancelTask",), r"""
VIDPOLL=true;UIBUSY=false;
await cancelTask();
out.videoA={vid:VID.length,fetches:FETCHES.filter(f=>/api\/(cancel|ai_stop)/.test(f.url)).length,
  btn:$('progStop').disabled};
VIDPOLL=false;FETCHES.length=0;
await cancelTask();
out.jobB={vid:VID.length,fetches:FETCHES.filter(f=>/api\/(cancel|ai_stop)/.test(f.url)).length};
""", pre=FETCH_STUB + r"""
let UICANCEL=false,UIBUSY=false,VIDPOLL=false,VID=[];
async function vidCancel(){VID.push('vid');}
function progUpdate(){}
""")
    assert res["videoA"] == {"vid": 1, "fetches": 0, "btn": False}, res
    assert res["jobB"] == {"vid": 1, "fetches": 2}, res


# ============== 13. ПРОФИЛЬ СПИКЕРА НЕ ТЕРЯЕТ ПОЛЯ ВНЕ ОКНА ==============
SAVE_SPK_PRE = FETCH_STUB + r"""
let SPEAKERS={},SPKEDIT='',SPKSAVED='',SPKDEF={},SPKDEF_FORMAT='16:9';
async function loadSpeakers(){}
async function onSpeakerChange(){}
function closeModal(){}
"""


@node
def test_behav_speaker_save_keeps_fields_the_window_does_not_show(tmp_path):
    """Сохранение профиля правит копию файла: поля, которых в окне нет (ref, breath_model), остаются."""
    res = _case(tmp_path, "b3_save_speaker_keep.js", ("saveSpeaker", "clipSpeaker"), r"""
SPEAKERS={'Ан':{label:'Ан',ref:['r1.jpg'],breath_model:'bm.json',outdir:'o'}};SPKEDIT='Ан';
$('spk_label').value='Ан';
await saveSpeaker();
const sent=JSON.parse(FETCHES.find(f=>/savespeaker/.test(f.url)).body).data;
out.ref=sent.ref;out.bm=sent.breath_model;
""", pre=SAVE_SPK_PRE)
    assert res.get("ref") == ["r1.jpg"], res
    assert res.get("bm") == "bm.json", res


@node
def test_behav_speaker_save_writes_only_real_overrides_of_cuts(tmp_path):
    """Порог, равный общему значению, в профиль не пишется; отличающийся — пишется."""
    res = _case(tmp_path, "b3_save_speaker_cut.js", ("saveSpeaker", "clipSpeaker"), r"""
SPEAKERS={'Ан':{label:'Ан'}};SPKEDIT='Ан';
SPKDEF={gap:0.5,pad:0.2,flag:false};
const CUT=[{dataset:{cut:'gap'},type:'number',value:'0.5'},
           {dataset:{cut:'pad'},type:'number',value:'0.3'},
           {dataset:{cut:'flag'},type:'checkbox',checked:true}];
$('spk_cut').querySelectorAll=()=>CUT;
$('spk_label').value='Ан';
await saveSpeaker();
const sent=JSON.parse(FETCHES.find(f=>/savespeaker/.test(f.url)).body).data;
out.cut=sent.cut;
""", pre=SAVE_SPK_PRE)
    assert res.get("cut") == {"pad": 0.3, "flag": True}, res


# ============== 14. СМЕНА СТИЛЯ НЕ ПЕРЕПИСЫВАЕТ ПРОФИЛЬ СПИКЕРА ==============
@node
def test_behav_style_change_never_writes_the_speaker_profile(tmp_path):
    """Выбор стиля меняет поля редактора и текущий стиль, а профиль спикера не трогает."""
    res = _case(tmp_path, "b3_style_change.js", ("onStyleChange",), r"""
STYLES={stA:{label:'A'},stB:{label:'B',music_dir:'D:/b'}};
const sel=$('style');sel.value='stB';sel.dataset.prev='stA';
await onStyleChange();
out.cur=CURSTYLE&&CURSTYLE.music_dir;
out.profileWrites=FETCHES.filter(f=>/savespeaker/.test(f.url)).length;
out.fills=FILLS;
""", pre=FETCH_STUB + r"""
let FILLS=0,STYLE_TOUCHED=false,STYLE_EDIT_ORIG=null;
function fillStyleFields(){FILLS++;}
function updateStyleDiffDots(){}
function renderStyleInfo(){}
function captureAE(){}
async function askConfirm(){return true;}
""")
    assert res["cur"] == "D:/b", res
    assert res["profileWrites"] == 0, res
    assert res["fills"] == 1, res


@node
def test_behav_speaker_with_missing_style_says_so_and_keeps_current(tmp_path):
    """Стиль из профиля, которого нет в styles/, не подставляется молча: есть сообщение, текущий стиль остаётся."""
    res = _case(tmp_path, "b3_speaker_missing_style.js", ("onSpeakerChange",), r"""
SPEAKERS={'Ан':{label:'Ан',style:'stX'}};STYLES={stA:{label:'A'}};
$('speaker').value='Ан';$('style').value='stA';
CURSTYLE=null;CLIPS=[];curAE=-1;
await onSpeakerChange();
out.styleNow=$('style').value;
out.toasts=TOASTS.slice();
""", pre=FETCH_STUB + r"""
async function spkDir(){}
async function camDirApply(){}
function spkEditUI(){}
function cutSummary(){}
function renderRenderDirField(){}
function renderStyleInfo(){}
function clipSpeaker(c){return (c&&c.job&&c.job.speaker)||'';}
""")
    assert res["styleNow"] == "stA", res
    assert any("не найден" in x for x in res["toasts"]), res


# ============== 15. НОВЫЙ КЛИП НЕСЁТ ТЕГ СПИКЕРА И ЕГО СТИЛЬ ==============
@node
def test_behav_new_clip_carries_the_speaker_tag_and_its_style(tmp_path):
    """Клип, созданный при выбранном спикере, получает тег и стиль спикера.

    Без спикера — запасной путь (тега и стиля нет), стиль, которого нет в styles/, не ставится.
    """
    res = _case(tmp_path, "b3_new_clip.js", ("newClip", "defJob"), r"""
STYLES={stA:{label:'A'}};SPEAKERS={spkA:{label:'A',style:'stA'},spkB:{label:'B',style:'gone'}};
$('speaker').value='spkA';const c1=newClip('A.xml');
$('speaker').value='';const c2=newClip('B.xml');
$('speaker').value='spkB';const c3=newClip('C.xml');
out.a=[c1.job.speaker,c1.job.styleKey];
out.none=[c2.job.speaker||null,c2.job.styleKey||null];
out.b=[c3.job.speaker,c3.job.styleKey||null];
""")
    assert res["a"] == ["spkA", "stA"], res
    assert res["none"] == [None, None], res
    assert res["b"] == ["spkB", None], res


# ============== 16. ОБХОД СТАТУСОВ НЕ ТЕРЯЕТ ЗАПРОС, ПРИШЕДШИЙ ВО ВРЕМЯ ОБХОДА ==============
@node
def test_behav_status_refresh_repeats_when_asked_during_a_pass(tmp_path):
    """Запрос статусов во время обхода не пропадает: обход повторяется ровно один раз после текущего."""
    res = _case(tmp_path, "b3_refresh.js", ("refreshStatuses",), r"""
CLIPS=[{xml:'A.xml',name:'A',status:{}},{xml:'B.xml',name:'B',status:{}}];
const p1=refreshStatuses();
const p2=refreshStatuses();
await Promise.all([p1,p2]);
await new Promise(r=>setTimeout(r,60));
out.fetches=FETCHES.length;
out.want=REFRESHWANT;
out.busy=REFRESHING;
""", pre=FETCH_SLOW + r"""
let REFRESHING=false,REFRESHWANT=false;
function renderClips1(){}function renderClips2(){}function renderClips3(){}
""")
    assert res["fetches"] == 4, res
    assert res["want"] is False and res["busy"] is False, res


# ============== 17. ЗАХВАТ КРАЯ БЛОКА — БЛИЖАЙШИЙ, ПЛЕЙХЕД ВЫИГРЫВАЕТ ВНИЧЬЮ ==============
@node
def test_behav_editor_grabs_the_nearest_edge_and_playhead_wins_ties(tmp_path):
    """Под курсором берётся ближайший край (не первый попавшийся); при равенстве — плейхед.

    Далеко от краёв и плейхеда захвата нет.
    """
    res = _case(tmp_path, "b3_edge_grab.js", ("edGrabAt", "edS2X"), r"""
$('edtl').width=1000;
ED={blocks:[{s0:0,s1:100},{s0:105,s1:200}],cs:600,v0:0,v1:1000};
out.nearest=edGrabAt(103);
ED={blocks:[{s0:0,s1:100}],cs:108,v0:0,v1:1000};
out.tie=edGrabAt(104);
ED={blocks:[{s0:0,s1:100}],cs:600,v0:0,v1:1000};
out.far=edGrabAt(300);
""", pre="const EDGRAB=8;\nconst devicePixelRatio=1;\nlet ED={blocks:[],cs:0,v0:0,v1:1000};\n")
    assert res["nearest"]["i"] == 1 and res["nearest"]["edge"] == 0, res
    assert res["tie"] == {"head": 1, "d": 4}, res
    assert res["far"] is None, res


def _fn_or_block_src(file_name: str, name: str) -> str:
    """Тело IIFE вида `(function name(){...})();` из static/app/<file_name> — целиком."""
    with open(os.path.join(ROOT, "static", "app", file_name), encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("(function %s(" % name)
    return text[start:text.index("})();", start) + len("})();")]


# ============== 18. ОШИБКИ ЗАГРУЗКИ СТИЛЕЙ НАЗЫВАЮТСЯ ЧЕСТНО ==============
LOAD_STYLES_PRE = r"""
let FETCHMODE='ok',MIGRATE_FAILS=false,STYLESAVED='',STSCHEMA=null;
ELS.style={value:'',dataset:{},innerHTML:'',appendChild(){}};
function fetch(url){
  if(FETCHMODE==='reject')return Promise.reject(new Error('net'));
  const d=FETCHMODE==='fail'?{ok:false,error:'boom'}:{ok:true,styles:{base:{label:'base'}}};
  return Promise.resolve({json:()=>Promise.resolve(d)});}
function migrateClipStyles(){if(MIGRATE_FAILS)throw new Error('bad state');}
async function loadStyleSchema(){}
function renderStylePanel(){}
function ensureCustomOption(){}
function onStyleChange(){}
const document={createElement(){return {value:'',textContent:'',appendChild(){}};}};
"""


@node
def test_behav_load_styles_blames_the_right_thing(tmp_path):
    """«Сервер не ответил» — только про сеть; ответ с ошибкой и сбой применения называют себя.

    Замена test_loadstyles_blames_the_right_thing (текстовый): три пути loadStyles,
    каждый со своей фразой. Раньше один try на всё выдавал сбой применения за
    недоступный сервер, а ошибку бэкенда глушил без тоста.
    """
    res = _case(tmp_path, "b3_load_styles.js", ("loadStyles",), r"""
FETCHMODE='reject';
await loadStyles();
out.reject=TOASTS.slice();TOASTS.length=0;
FETCHMODE='fail';
await loadStyles();
out.fail=TOASTS.slice();TOASTS.length=0;
FETCHMODE='ok';MIGRATE_FAILS=true;
await loadStyles();
out.apply=TOASTS.slice();
""", pre=LOAD_STYLES_PRE)
    assert res["reject"] == ["Стили не загрузились — сервер не ответил"], res
    assert res["fail"] == ["Стили не загрузились: boom"], res
    assert res["apply"] == ["Стили пришли, но не применились — смотри журнал"], res


# ============== 19. ЗВУК ЭФФЕКТА ИГРАЕТ НОВЫЙ ФАЙЛ, А НЕ СТАРЫЙ ==============
@node
def test_behav_sfx_switches_the_file_when_the_media_changes(tmp_path):
    """Эффект, у которого сменился файл, играет НОВЫЙ файл; убранный эффект уходит из памяти.

    Замена test_sfx_ensure_updates_src_on_media_change (текстовый). Здесь sfxEnsure
    дважды получает план со сменённым media — src и путь должны уйти за файлом, громкость
    считается от base+db — и затем план без эффекта.
    """
    from urllib.parse import quote
    res = _case(tmp_path, "b3_sfx.js", ("sfxEnsure", "sfxSyncApply"), r"""
const p1={audio:{sfx:[{kind:'whoosh',media:'Звук 1.wav',events:[1],base:0,db:-6}]}};
sfxEnsure(p1);
out.first=SFX_ELS.whoosh.el.src;
out.vol=Math.round(SFX_ELS.whoosh.vol*1000)/1000;
const p2={audio:{sfx:[{kind:'whoosh',media:'Звук 2.wav',events:[1],base:0,db:0}]}};
sfxEnsure(p2);
out.second=SFX_ELS.whoosh.el.src;
out.path=SFX_ELS.whoosh.path;
sfxEnsure({audio:{sfx:[]}});
out.gone=!('whoosh' in SFX_ELS);
out.freed=FREED.length;
""", pre=r"""
const SFX_ELS={};let MEDIA_VOL=1;const FREED=[];
function mediaFree(el){FREED.push(el);}
function dbToGain(db){return Math.pow(10,db/20);}
const document={createElement(){return {src:'',preload:'',volume:1,pause(){}};},body:{appendChild(){}}};
""")
    enc = lambda s: "/api/media?path=" + quote(s, safe="-_.!~*'()")
    assert res["first"] == enc("Звук 1.wav"), res
    assert res["vol"] == 0.501, res
    assert res["second"] == enc("Звук 2.wav"), res
    assert res["path"] == "Звук 2.wav", res
    assert res["gone"] is True and res["freed"] == 1, res


# ============== 20. ВОПРОС ИИ-ИНТРО ЛОЖИТСЯ В ЗАДАНИЕ СПРОШЕННОГО КЛИПА ==============
AI_INTRO_PRE = r"""
let FLIP=false,RENDERED=0,INTRO_PICK=-1;
function uiBusyGuard(){return false;}
async function aiFetch(url,body){if(FLIP){curAE=1;AEXML='B.xml';}FETCHES.push(String(url));return {rows:[{count:2}]};}
function introRowsFromAI(d){return d.rows;}
function midCount(){return 0;}
function insLog(){}
function aiAborted(){return false;}
function renderIntro(){RENDERED++;}
function aewRender(){}
function captureAE(){}
function styleForJob(){return 'stA';}
function defJob(){return {introRows:[]};}
"""


@node
def test_behav_ai_intro_answer_stays_with_the_clip_it_was_asked_for(tmp_path):
    """Ответ ИИ-интро уходит в задание клипа, который спрашивали, даже если открыт другой.

    Замена части test_ai_intro_answer_lands_in_the_clip_it_was_asked_for (текстовый):
    модель думает минутами, за это время клип может смениться. Результат — в задание
    спрошенного клипа, панель чужого клипа не трогаем, состояние сохраняем и пишем в лог.
    Клип, оставшийся открытым, получает панель и статус «готово».
    """
    res = _case(tmp_path, "b3_ai_intro.js", ("aiIntroRun",), r"""
CLIPS=[{xml:'A.xml',name:'A',inserts:[],status:{},job:{introRows:[]}},
       {xml:'B.xml',name:'B',inserts:[],status:{},job:{introRows:[{count:9}]}}];
const bOld=CLIPS[1].job.introRows;
curAE=0;AEXML='A.xml';FLIP=true;
await aiIntroRun();
out.aRows=CLIPS[0].job.introRows.map(r=>r.count);
out.bKept=CLIPS[1].job.introRows===bOld;
out.panelInto=INTRO.length;
out.saved=SAVES>0;
out.logged=LOGS.some(x=>x.indexOf('уехало в задание клипа A')>=0);
CLIPS=[{xml:'A.xml',name:'A',inserts:[],status:{},job:{introRows:[]}}];
curAE=0;AEXML='A.xml';FLIP=false;RENDERED=0;
await aiIntroRun();
out.openRows=INTRO.length;
out.openRendered=RENDERED;
out.openStatus=$('introres').className;
""", pre=AI_INTRO_PRE)
    assert res["aRows"] == [2], res
    assert res["bKept"] is True, res
    assert res["panelInto"] == 0 and res["saved"] is True and res["logged"] is True, res
    assert res["openRows"] == 1 and res["openRendered"] == 1 and res["openStatus"] == "ok", res


# ============== 21. СБОРКА И РЕНДЕР ИДУТ ТОЛЬКО ПО ОТМЕЧЕННЫМ КЛИПАМ ==============
@node
def test_behav_collect_jobs_takes_only_checked_clips_and_their_folders(tmp_path):
    """В набор сборки и рендера попадают ТОЛЬКО отмеченные клипы; папка каждого — своего спикера.

    Замена части test_render_uses_checked_clips_not_open_clip (текстовый): открытый, но не
    отмеченный клип в набор не идёт; без отметок набор — все клипы; папка .jsx берётся
    из тега спикера самого клипа.
    """
    res = _case(tmp_path, "b3_collect_jobs.js",
                ("collectJobs", "selClips", "jobForBuild", "effOutdir", "xmlDirOf",
                 "clipSpeaker", "clipNcams", "styleForJob", "defJob"), r"""
CLIPS=[{xml:'D:/p/A.xml',name:'A',sel:false,status:{},job:{speaker:'spkA'}},
       {xml:'D:/p/B.xml',name:'B',sel:true,status:{},job:{speaker:'spkB'}}];
SPEAKERS={spkA:{label:'A',jsxdir:'D:/jsxA',outdir:'D:/outA'},spkB:{label:'B',jsxdir:'',outdir:'D:/outB'}};
curAE=0;
const picked=await collectJobs();
out.picked=picked.map(j=>j.xml);
out.outdir=picked.map(j=>j.outdir);
CLIPS[1].sel=false;
const all=await collectJobs();
out.all=all.map(j=>j.outdir);
""", pre=r"""
let SPEAKERS={};
function captureAE(){}
function clipReady(c){return true;}
function musicJobFields(c){return {};}
function resolveIntroFor(){return {lines:[],remove:[],splits:[]};}
async function askConfirm(){return true;}
function plur(){return '';}
""")
    assert res["picked"] == ["D:/p/B.xml"], res
    assert res["outdir"] == ["D:/outB"], res
    assert res["all"] == ["D:/jsxA", "D:/outB"], res


# ============== 22. ПЕРЕЕЗД КЛЮЧЕЙ НЕ ЗАТИРАЕТ НОВОЕ И НЕ СТИРАЕТ СТАРОЕ ==============
@node
def test_behav_state_migration_copies_old_keys_without_overwriting(tmp_path):
    """Переезд ключей localStorage AutoCut → Reelsi: старое значение копируется, новое не затирается.

    Замена части test_localstorage_keys_are_migrated_not_just_renamed (текстовый).
    Старые ключи после переезда остаются: откат на прошлую версию не должен обнулить работу.
    """
    iife = _fn_or_block_src("00-core.js", "migrateLS")
    res = _case(tmp_path, "b3_migrate_ls.js", (), r"""
out.step=LSTORE['reelsi_step'];
out.vol=LSTORE['reelsi_vol'];
out.inslib=LSTORE['reelsi_inslib'];
out.state=LSTORE['reelsi_state'];
out.oldKept=LSTORE['autocut2_step']==='3'&&LSTORE['autocut2_state']==='{"old":1}';
""", pre=r"""
const LSTORE={'autocut2_step':'3','autocut2_vol':'0.5','autocut2_inslib':'{"i":1}',
  'autocut2_state':'{"old":1}','reelsi_state':'{"new":1}'};
const localStorage={getItem:k=>(Object.prototype.hasOwnProperty.call(LSTORE,k)?LSTORE[k]:null),
  setItem:(k,v)=>{LSTORE[k]=String(v);},removeItem:k=>{delete LSTORE[k];}};
""" + iife)
    assert res["step"] == "3" and res["vol"] == "0.5", res
    assert res["inslib"] == '{"i":1}', res
    assert res["state"] == '{"new":1}', res
    assert res["oldKept"] is True, res


# ============== 23. ПАПКА .JSX КЛИПА: СПИКЕР, ПОТОМ XML ==============
@node
def test_behav_clip_folder_takes_speaker_then_xml_folder(tmp_path):
    """Папка .jsx клипа — папка профиля его спикера, иначе его же нарезки, иначе папка XML.

    Замена части test_jsx_folder_derives_from_the_tag (текстовый). Клип без тега и клип
    с тегом пропавшего профиля кладут .jsx рядом со своим XML.
    """
    res = _case(tmp_path, "b3_clip_folder.js", ("effOutdir", "xmlDirOf", "clipSpeaker"), r"""
SPEAKERS={spkA:{label:'A',jsxdir:'D:/jsx',outdir:'D:/out'},spkB:{label:'B',jsxdir:'',outdir:'D:/outB'},spkC:{label:'C'}};
out.withJsx=effOutdir({xml:'D:/p/A.xml',job:{speaker:'spkA'}});
out.outOnly=effOutdir({xml:'D:/p/B.xml',job:{speaker:'spkB'}});
out.noDirs=effOutdir({xml:'D:/p/C.xml',job:{speaker:'spkC'}});
out.noSpeaker=effOutdir({xml:'D:/p/D.xml',job:{}});
out.ghost=effOutdir({xml:'D:/p/E.xml',job:{speaker:'nobody'}});
""", pre="let SPEAKERS={};")
    assert res["withJsx"] == "D:/jsx", res
    assert res["outOnly"] == "D:/outB", res
    assert res["noDirs"] == "D:/p", res
    assert res["noSpeaker"] == "D:/p", res
    assert res["ghost"] == "D:/p", res


# ============== 24. ПОРЦИЯ 2: КАМЕРЫ, ВЫБОР СТИЛЯ, ОЧЕРЕДЬ, ОТВЕТЫ ИИ ==============
CAM_PRE = r"""
const CAM_SOFT=0.04,CAM_HARD=0.5,CAM_RATE=0.06;
function fakeVid(t){return {style:{},muted:false,paused:false,playbackRate:1,currentTime:t,seeking:false,
  error:null,pauses:0,play(){return Promise.resolve();},pause(){this.pauses++;}};}
"""
CAM_NAMES = ("camApply", "camVisual", "camTrack", "pvSegAt", "vtPlaying")


@node
def test_behav_camera_shown_is_the_one_the_edl_asks_for(tmp_path):
    """Показ ракурса — ровно тот, что просит EDL, и не ждёт готовности камеры.

    Замена test_camera_shown_is_exactly_the_one_edl_asks_for: ожидание готовности показало бы
    другую камеру. Камера в seek'е не задерживает показ: ракурс берётся из EDL как есть.
    """
    res = _case(tmp_path, "b3_cam_shown.js", CAM_NAMES, r"""
const P={segs:[{ts:0,te:5,ci:0,src:0},{ts:5,te:10,ci:1,src:5}],vids:[fakeVid(0),fakeVid(5),fakeVid(0)],
  audioCi:0,delta:[0,0,0],stats:{cam:0,back:0,stale:0},curCi:-1,vidx:-1,playing:false};
camApply(P,6,false);
out.first=P.vids.findIndex(v=>v.style.zIndex==='2');
P.vids[0].seeking=true;
camApply(P,2,false);
out.second=P.vids.findIndex(v=>v.style.zIndex==='2');
""", pre=CAM_PRE)
    assert res["first"] == 1 and res["second"] == 0, res


@node
def test_behav_secondary_cameras_track_the_lead_and_never_pause(tmp_path):
    """Вторичные камеры ведутся к ведущей каждый кадр; звуковую не трогаем; паузы не ставим.

    Замена test_secondary_cameras_run_as_continuous_tracks. Звуковая камера (audioCi) не получает
    ни скорости, ни seek: с неё идёт звук. Далёкая камера догоняется seek'ом к своему времени
    (ведущая + оффсет). Камеры не ставятся на паузу.
    """
    res = _case(tmp_path, "b3_cam_tracks.js", CAM_NAMES, r"""
const P={segs:[{ts:0,te:10,ci:0,src:0}],vids:[fakeVid(5),fakeVid(5.3),fakeVid(0)],
  audioCi:1,delta:[0,0,0.5],stats:{cam:0,back:0,stale:0},curCi:-1,vidx:-1,playing:true};
camApply(P,5,true);
out.audioRate=P.vids[1].playbackRate;
out.audioTime=Math.round(P.vids[1].currentTime*100)/100;
out.farTime=Math.round(P.vids[2].currentTime*100)/100;
out.farRate=P.vids[2].playbackRate;
out.pauses=P.vids.reduce((a,v)=>a+v.pauses,0);
out.leadRate=P.vids[0].playbackRate;
""", pre=CAM_PRE)
    assert res["audioRate"] == 1 and res["audioTime"] == 5.3, res
    assert res["farTime"] == 5.5 and res["farRate"] == 1, res
    assert res["pauses"] == 0 and res["leadRate"] == 1, res


@node
def test_behav_camera_never_walks_back_while_playing(tmp_path):
    """Во время игры показ не возвращается на предыдущий кусок; на паузе назад ходить можно.

    Замена части test_camera_choice_never_walks_back_while_playing (поведение camApply).
    Двери перемотки, которые сбрасывают кусок, остаются в старом тесте.
    """
    res = _case(tmp_path, "b3_cam_back.js", CAM_NAMES, r"""
const P={segs:[{ts:0,te:5,ci:0,src:0},{ts:5,te:10,ci:1,src:5}],vids:[fakeVid(0),fakeVid(5)],
  audioCi:0,delta:[0,0],stats:{cam:0,back:0,stale:0},curCi:1,vidx:1,playing:true};
camApply(P,2,false);
out.playingCi=P.vids.findIndex(v=>v.style.zIndex==='2');
out.vidx=P.vidx;
P.playing=false;
camApply(P,2,false);
out.pausedCi=P.vids.findIndex(v=>v.style.zIndex==='2');
""", pre=CAM_PRE)
    assert res["playingCi"] == 1 and res["vidx"] == 1, res
    assert res["pausedCi"] == 0, res


@node
def test_behav_scrub_holds_the_double_buffer_arming(tmp_path):
    """Пока ползунок протягивают, дублёр не взводится; когда протяжка улеглась — взводится.

    Замена части test_scrubbing_does_not_starve_the_preview_double_buffer: протяжка на каждый
    пиксель не должна сбивать разбег дублёра, иначе на стыке подмена срывается.
    """
    res = _case(tmp_path, "b3_scrub.js", ("ipvScrub", "bufArm"), r"""
const b={at:null,el:{currentTime:0}};
ipvScrub(500);
bufArm(IPV,b,5);
out.during=b.at;
out.primedDuring=PRIMES.length;
await new Promise(r=>setTimeout(r,200));
bufArm(IPV,b,5);
out.after=b.at;
out.primed=PRIMES.length;
""", pre=r"""
const PV_PREROLL=0.35;
let PRIMES=[];
const IPV={vids:[{}],dur:10,playing:true,scrubbing:false,scrubT:null};
function ipvPause(){}function ipvSeekTo(){}function ipvPlay(){}
function sparePrime(){PRIMES.push(1);}
""")
    assert res["during"] is None and res["primedDuring"] == 0, res
    assert res["after"] == 5 and res["primed"] == 1, res


@node
def test_behav_named_style_change_fills_the_fields_after_the_copy(tmp_path):
    """Выбор именованного стиля заполняет поля редактора из КОПИИ выбранного стиля, без reflectStyle.

    Замена test_named_style_change_hydrates_all_editor_fields: reflectStyle обновляет урезанный
    набор полей, и сохранение потом писало бы в стиль поля прошлого шаблона.
    """
    res = _case(tmp_path, "b3_named_style_fill.js", ("onStyleChange",), r"""
STYLES={stA:{label:'A'},stB:{label:'B',music_dir:'D:/b'}};
const sel=$('style');sel.value='stB';sel.dataset.prev='stA';
await onStyleChange();
out.fills=FILLS.slice();
out.reflect=REFLECT;
""", pre=r"""
let FILLS=[],REFLECT=0,STYLE_TOUCHED=false,STYLE_EDIT_ORIG=null;
function fillStyleFields(){FILLS.push(CURSTYLE&&CURSTYLE.music_dir);}
function reflectStyle(){REFLECT++;}
function updateStyleDiffDots(){}
function renderStyleInfo(){}
function captureAE(){}
""")
    assert res["fills"] == ["D:/b"], res
    assert res["reflect"] == 0, res


@node
def test_behav_remove_selected_moves_panel_when_open_clip_removed(tmp_path):
    """Убрали отмеченными и открытый клип: панель садится на живой клип или прячется.

    Замена test_remove_selected_reloads_panel_when_active_removed: при живом списке панель
    переходит на первый клип, при пустом — прячется, а принадлежность панели снимается.
    """
    res = _case(tmp_path, "b3_remove_panel.js", ("removeSelClips", "_spliceClip"), r"""
CLIPS=[{xml:'A.xml',name:'A',sel:false,status:{}},{xml:'B.xml',name:'B',sel:true,status:{}},
       {xml:'C.xml',name:'C',sel:false,status:{}}];
curAE=1;AEXML='B.xml';
await removeSelClips();
out.selected=SELECTED.slice();
CLIPS=[{xml:'B.xml',name:'B',sel:true,status:{}}];
curAE=0;AEXML='B.xml';
await removeSelClips();
out.emptyAecfg=$('aecfg').style.display;
out.emptyAEXML=AEXML;
""", pre=QUEUE_PRE + "let SELECTED=[];\nfunction selectAE(i){SELECTED.push(i);}\n")
    assert res["selected"] == [0], res
    assert res["emptyAecfg"] == "none" and res["emptyAEXML"] == "", res


@node
def test_behav_language_button_shows_the_language_in_use(tmp_path):
    """Кнопка языка показывает язык, который сейчас в ходу, а не тот, на который переключим.

    Замена части test_language_button_shows_current_language: текст кнопки после setLang.
    Начальная установка (initLang при загрузке) — текстовая проверка, остаётся в старом тесте.
    """
    res = _case(tmp_path, "b3_lang_btn.js", ("setLang",), r"""
await setLang('en',false);
out.en=BTN.textContent;
await setLang('ru',false);
out.ru=BTN.textContent;
await setLang('en',true);
out.reloads=RELOADS.length;
""", pre=r"""
let LANG='ru',I18N={};
const BTN={textContent:''};
let RELOADS=[];
const document={getElementById(){return BTN;},documentElement:{setAttribute(){}},body:{}};
const location={reload(){RELOADS.push(1);}};
function applyI18n(){}function startI18nObserver(){}
""")
    assert res["en"] == "EN" and res["ru"] == "RU", res
    assert res["reloads"] == 1, res


@node
def test_behav_tooltip_waits_for_controls_but_not_for_exclamation(tmp_path):
    """Подсказка на «!» — сразу; на контроле — после задержки и только если курсор всё ещё на нём.

    Замена test_tooltip_delay_skips_exclamation_marks: задержка проверяется поведением.
    """
    res = _case(tmp_path, "b3_tip.js", ("tipShowDelayed",), r"""
const bang={classList:{contains:c=>c==='i'},matches(){return true;},contains(){return false;}};
const ctrl={classList:{contains:()=>false},matches(){return true;},contains(){return false;}};
tipShowDelayed(bang);
out.bangNow=SHOWN.length;
tipShowDelayed(ctrl);
out.ctrlNow=SHOWN.length;
await new Promise(r=>setTimeout(r,TIP_DELAY_MS+40));
out.ctrlLater=SHOWN.length;
""", pre=r"""
const TIP_DELAY_MS=20;
const document={activeElement:null};
let tipTimer=null,tipEl=null,SHOWN=[];
function tipShow(el){SHOWN.push(el);}
""")
    assert res["bangNow"] == 1 and res["ctrlNow"] == 1 and res["ctrlLater"] == 2, res


@node
def test_behav_cleared_insert_is_not_refilled_by_library(tmp_path):
    """Картинку, снятую крестиком (noAuto), автоподбор из базы не возвращает.

    Замена части test_cleared_insert_is_not_refilled_by_itself: пометку noAuto ставит
    insClearMedia, уважает insLibFill; остальные варианты для кнопки 📚 при этом заполняются.
    """
    res = _case(tmp_path, "b3_cleared_insert.js", ("insLibFill", "insClearMedia"), r"""
CLIPS=[{xml:'A.xml',name:'A',inserts:[{query:'кот',media:'old.jpg',type:'photo'},{query:'пёс',type:'photo'}],status:{}}];
curIns=0;
await insClearMedia(0);
const filled=await insLibFill(CLIPS[0].inserts);
out.cleared=CLIPS[0].inserts[0].media;
out.noAuto=CLIPS[0].inserts[0].noAuto===true;
out.second=CLIPS[0].inserts[1].media;
out.filled=filled;
""", pre=r"""
let curIns=-1;
function insWant(){return 'photo';}
function insSetMedia(x,p){x.media=p;}
function insApplyCrop(){}
function renderInsHost(){}function syncClipLists(){}
async function fetch(url,opts){const n=JSON.parse(opts.body).queries.length;
  return {json:async()=>({results:Array.from({length:n},()=>[{auto:true,path:'lib.jpg'}])})};}
""")
    assert res["cleared"] == "" and res["noAuto"] is True, res
    assert res["second"] == "lib.jpg" and res["filled"] == 1, res


@node
def test_behav_image_gen_gives_up_after_timeout(tmp_path):
    """Зависший запрос генерации картинки обрывается по таймауту и не держит вызов навсегда.

    Замена части test_image_gen_never_locks_the_button_forever: запрос отменяется по сигналу,
    в стенде таймаут короткий, вызов завершается ошибкой за секунды.
    """
    res = _case(tmp_path, "b3_gen_timeout.js", ("insGenCore",), r"""
const x={query:'кот',prompt:'',plate:false};
const t0=Date.now();
try{await insGenCore(x,'a',undefined);out.result='resolved';}
catch(e){out.result='rejected';out.msg=String(e&&e.message||e);}
out.quick=(Date.now()-t0)<2000;
""", pre=r"""
const GEN_FETCH_MS=30;
function fetch(url,opts){return new Promise((res,rej)=>{
  opts.signal.addEventListener('abort',()=>rej(new Error('AbortError: timeout')));});}
""")
    assert res["result"] == "rejected" and "AbortError" in res["msg"], res
    assert res["quick"] is True, res


@node
def test_behav_open_clip_index_resets_when_the_clip_is_gone(tmp_path):
    """Открытый индекс клипа не переживает сам клип: пустой список — curAE=-1, без падения.

    Замена части test_curae_never_points_past_clips: раньше captureAE падал на CLIPS[curAE].job.
    """
    res = _case(tmp_path, "b3_curae.js", ("captureAE",), r"""
CLIPS=[];curAE=3;
captureAE();
out.curAE=curAE;
""", pre="function defJob(){return {};}\n")
    assert res["curAE"] == -1, res


@node
def test_behav_ai_insert_answer_stays_with_its_clip(tmp_path):
    """Ответ ИИ-вставок ложится в клип, для которого спрашивали, даже если открыт другой.

    Замена части test_single_ai_doors_keep_their_clip_by_reference: модель отвечает минутами,
    пока открыт другой клип; ответ не уезжает ни в него, ни в его вставки.
    """
    res = _case(tmp_path, "b3_ai_inserts.js", ("aiInsertsRun", "clipSpeaker"), r"""
CLIPS=[{xml:'A.xml',name:'A',inserts:[],ins_rejected:[],status:{}},
       {xml:'B.xml',name:'B',inserts:[{type:'photo',start_sec:9}],status:{}}];
const bOld=CLIPS[1].inserts;
curIns=0;FLIP=true;
await aiInsertsRun();
out.aCount=CLIPS[0].inserts.length;
out.bKept=CLIPS[1].inserts===bOld;
""", pre=r"""
let FLIP=false,curIns=-1;
async function aiFetch(){if(FLIP){curIns=1;}return {inserts:[{type:'photo',start_sec:1}],insTarget:3};}
async function insAfterAI(){return '';}
function insLog(){}
function aiAborted(){return false;}
function renderInsHost(){}function syncClipLists(){}
""")
    assert res["aCount"] == 1 and res["bKept"] is True, res


@node
def test_behav_cut_results_are_adopted_once_per_clip(tmp_path):
    """Результаты нарезки попадают в список по одному и без дублей при повторных опросах.

    Замена части test_cut_results_land_in_the_list_one_by_one: опрос статуса звучит много раз,
    и клип из результатов не должен плодиться.
    """
    res = _case(tmp_path, "b3_cut_adopt.js", ("cutAdopt", "clipByXml", "newClip"), r"""
const d={results:['D:/p/A.xml','D:/p/B.xml']};
out.first=cutAdopt(d);
out.again=cutAdopt(d);
out.names=CLIPS.map(c=>c.name);
""", pre=r"""
let SPEAKERS={};
function clipKey(p){return String(p);}
function renderClips1(){}function saveState(){}function refreshStatuses(){}
function defJob(){return {};}
""")
    assert res["first"] == 2 and res["again"] == 0, res
    assert res["names"] == ["A.xml", "B.xml"], res


@node
def test_behav_style_template_edit_names_the_template_and_fills_its_fields(tmp_path):
    """Правка шаблона: селектор показывает его имя с пометкой, поля заполняются из шаблона.

    Замена части test_style_template_is_editable_without_retyping_its_name (поведение редактора).
    Встроенный шаблон правится без имени, с подсказкой «сохрани под своим именем».
    """
    res = _case(tmp_path, "b3_edit_style.js", ("editStyle", "ensureEditOption"), r"""
STYLES={stA:{label:'A',x:1},base:{label:'base'}};
const sel=$('style');sel.value='stA';sel.dataset.prev='stA';
editStyle();
out.value=sel.value;out.editing=STYLE_EDITING;out.name=$('st_name').value;
out.fills=FILLS;out.copyX=CURSTYLE.x;out.label=OPTS.map(o=>o.textContent);
sel.value='base';
editStyle();
out.builtinName=$('st_name').value;
out.builtinToast=TOASTS.some(x=>x.indexOf('встроенный')>=0);
""", pre=r"""
let FILLS=0,OPTS=[];
let STYLE_TOUCHED=false,STYLE_EDIT_ORIG=null;
const BUILTIN_STYLES={base:1,geologica:1};
function fillStyleFields(){FILLS++;}function renderStyleInfo(){}
ELS.style={value:'',dataset:{},querySelector(){return null;},appendChild(o){OPTS.push(o);}};
const document={createElement(){return {value:'',textContent:''};}};
""")
    assert res["value"] == "__edit__" and res["editing"] == "stA", res
    assert res["name"] == "stA" and res["fills"] == 1 and res["copyX"] == 1, res
    assert res["label"] == ["A — правится"], res
    assert res["builtinName"] == "" and res["builtinToast"] is True, res


@node
def test_behav_single_camera_bulk_queue_skips_queued_and_old_takes(tmp_path):
    """Массовая очередь одной камеры не добавляет повторно то, что уже в очереди; «только новые» действует.

    Замена части test_single_camera_has_a_bulk_queue_button (поведение очереди). Кнопки и
    переключение по числу камер проверяются текстом и остаются в старом тесте.
    """
    res = _case(tmp_path, "b3_bulk_queue.js", ("autoQueue", "eqArr"), r"""
CAMFILES=[['a.mp4','b.mp4','c.mp4']];QUEUE=[['a.mp4']];
await autoQueue();
out.asked=NEWONLY.map(f=>f.slice());
out.queue=QUEUE.map(p=>p[0]);
""", pre=r"""
let CAMFILES=[],QUEUE=[],NEWONLY=[];
function nCams(){return 1;}
async function newOnly(files){NEWONLY.push(files.slice());return files.filter(f=>f!=='b.mp4');}
function renderQueue(){}
""")
    assert res["asked"] == [["b.mp4", "c.mp4"]], res
    assert res["queue"] == ["a.mp4", "c.mp4"], res


@node
def test_behav_roto_mask_zoom_runs_before_the_frame_and_frame_always_paints(tmp_path):
    """Подсказка «низ маски» получает масштаб раньше кадра, а кадр рисуется и при единичном зуме.

    Замена части test_roto_mask_hint_rides_cam1_zoom (порядок вызовов и отсутствие раннего
    выхода). CSS-правила полосы остаются в старом тесте.
    """
    res = _case(tmp_path, "b3_roto_zoom.js", ("ipvZoom",), r"""
IPV.plan={zoom:{cx:0.3,cy:0.7}};
ZOOMS=[1.5,1];
ipvZoom(0);ipvZoom(0);
out.log=ORDER.slice();
""", pre=r"""
let ORDER=[],ZOOMS=[];
let IPV={plan:null};
function ipvZoomAt(){return ZOOMS.shift();}
function ipvRotoMaskZoom(s,cx,cy){ORDER.push('mask:'+s+':'+cx+':'+cy);}
function ipvCamPaint(s){ORDER.push('paint:'+s);}
""")
    assert res["log"] == ["mask:1.5:0.3:0.7", "paint:1.5", "mask:1:0.3:0.7", "paint:1"], res


@node
def test_behav_voice_wiring_queues_each_element_once(tmp_path):
    """Элемент голоса ставится в очередь подключения один раз; уже подключённый не трогаем.

    Замена части test_every_player_video_is_wired_to_the_volume_graph: поведение voiceWiring.
    Наличие voiceWiring в bufMake и ipvOpen остаётся в старом тесте.
    """
    res = _case(tmp_path, "b3_voice_wiring.js", ("voiceWiring", "voiceGraphNeeded"), r"""
CURSTYLE={voice_db:-6};
const v=fakeEl();voiceWiring(v);voiceWiring(v);
out.pending=VOICEPEND.length;
const w=fakeEl();w.__wired=true;voiceWiring(w);
out.wired=VOICEPEND.length;
""", pre=r"""
let AUDIO=null,VOICEPEND=[];
function voiceGraphWire(){return false;}
function fakeEl(){return {__inDom:false,isConnected:false};}
""")
    assert res["pending"] == 1 and res["wired"] == 1, res


@node
def test_behav_editor_words_show_the_word_under_the_playhead(tmp_path):
    """Строка субтитра в редакторе — слово под плейхедом в МОНТАЖНОМ времени, а не в исходном.

    Замена части test_editor_words_panel_shows_the_word_under_the_playhead: исходное время
    пересчитывается по раскладке из XML (ED.orig); монтажное — по ED.blocks.
    """
    res = _case(tmp_path, "b3_edit_words.js", ("edWords", "edCutOf", "pvWordAt"), r"""
ED={orig:[{s0:0,s1:10},{s0:20,s1:30}],blocks:[{s0:0,s1:10}],cs:25};
PV={words:[{s:14,e:16,w:'слово'}]};
edWords();
out.sub=$('pvsub').textContent;
""", pre="let ED={},PV={};\n")
    assert res["sub"] == "слово", res


@node
def test_behav_state_mirror_advances_the_delivered_mark_only_on_success(tmp_path):
    """Зеркало состояния отмечает доставку только успешного ответа; сбой повторится.

    Замена части теста зеркала (srvStatePost): SRVST_LAST и ревизия выставляются только после
    проверки ответа — ни при сетевом сбое, ни при ok=false.
    """
    res = _case(tmp_path, "b3_state_mirror.js", ("srvStatePost",), r"""
const seen=[];
for(const s of ['{"a":0}','{"a":1}','{"a":2}']){
  srvStatePost(s);
  await new Promise(r=>setTimeout(r,10));
  seen.push([SRVST_LAST,SRVST_REV]);
}
out.seen=seen;
""", pre=r"""
let SRVST_SENDING=false,SRVST_PENDING=null,SRVST_LAST='',SRVST_REV=0,SRVST_ERR=false,SRVST_STALE=false,SRVST_OK_T=0;
function srvStaleBanner(){}
const REPLIES=[
  ()=>Promise.reject(new Error('net')),
  ()=>Promise.resolve({ok:false,status:500,json:()=>Promise.resolve({rev:7})}),
  ()=>Promise.resolve({ok:true,json:()=>Promise.resolve({rev:5})})];
function fetch(){return REPLIES.shift()();}
""")
    assert res["seen"] == [["", 0], ["", 0], ['{"a":2}', 5]], res


@node
def test_behav_speaker_dirs_restore_every_camera_folder(tmp_path):
    """После загрузки страницы папки ВСЕХ камер восстанавливаются из профиля спикера.

    Замена части test_no_camcustom_and_speaker_dirs_restores_cams (вызов camDirApply по камерам).
    Лint идентификатора CAMCUSTOM остаётся в старом тесте.
    """
    res = _case(tmp_path, "b3_speaker_dirs.js", ("applySpeakerDirs", "samePath"), r"""
SPEAKERS={spkA:{label:'A',outdir:'D:/o',renderdir:'D:/r'}};
$('speaker').value='spkA';
applySpeakerDirs();
out.cams=CAMS.slice();
out.outdir=$('ai_outdir').value;
out.render=AERENDER;
""", pre=r"""
let CAMS=[];
async function camDirApply(k){CAMS.push(k);}
function renderRenderDirField(){}
""")
    assert res["cams"] == [0, 1], res
    assert res["outdir"] == "D:/o" and res["render"] == "D:/r", res


# ============== 25. ПОРЦИЯ 3: ПЕРЕСБОРКА ВСТАВОК, РАЗРЕШЕНИЕ И ЗАПРОС ВИДЕО ==============
@node
def test_behav_insert_shift_survives_ensure_jobs_rebuild(tmp_path):
    """Сдвиг вставки, записанный драгом в карточку шага 2, переживает пересборку job.ins.

    Замена test_insert_shift_survives_ensure_jobs_rebuild (текст). ensureJobs заново строит job.ins
    из карточек: источник x/y — карточка (её правит драг в предпросмотре), а не прежняя запись
    задания. Ручная вставка шага 3 (без карточки) не должна пропасть на пересборке.
    """
    res = _case(tmp_path, "b3_ensure_jobs.js",
                ("ensureJobs", "insEnsureUids", "cardToIns", "defJob"), r"""
CLIPS=[{xml:'A.xml',name:'A',status:{},inserts:[
  {uid:'u1',media:'a.jpg',type:'photo',start_sec:2,duration_sec:2,x:0.55,y:0.3},
  {uid:'u2',media:'b.jpg',type:'photo',start_sec:5,duration_sec:2,x:0.1,y:0.1}],
  job:{ins:[{cid:'u1',media:'a.jpg',x:0.9,y:0.9,src2:true},{cid:'u2',media:'b.jpg',x:0.2,y:0.2,src2:true},
    {media:'m.jpg',x:0.4,y:0.4,src2:false}]}}];
ensureJobs();
const ins=CLIPS[0].job.ins;
const byCid=c=>ins.find(x=>x.cid===c);
out.u1=[byCid('u1').x,byCid('u1').y];
out.u2=[byCid('u2').x,byCid('u2').y];
out.manual=ins.filter(x=>x.media==='m.jpg').map(x=>[x.x,x.y]);
""", pre="let INSUIDSEQ=0;\n"
    # normInsPath не вырезается по балансу скобок: в регулярке `/\//g` есть «//»; стенд берёт упрощённую нормализацию
    "function normInsPath(s){return String(s||'').toLowerCase();}\n")
    assert res["u1"] == [0.55, 0.3], res
    assert res["u2"] == [0.1, 0.1], res
    assert res["manual"] == [[0.4, 0.4]], res


@node
def test_behav_video_resolution_follows_the_model_and_history_reuse(tmp_path):
    """Разрешение видео — общая настройка на сервере: смена модели сбрасывает устаревшее, «Повторить» ждёт модель, потом разрешение, и только поддерживаемое.

    Замена test_video_resolution_settings_contract (поведение; разметка и справки текстом — в старом тесте).
    """
    res = _case(tmp_path, "b3_video_res.js",
                ("setVideoResolution", "setVideoModel", "vidFillResolution", "vidHistReuse",
                 "vidResList", "vidResValue", "vidSupportsRes", "vhFind"), r"""
AICFG={video_model:'m1',video_resolution:'',video_resolutions:['480p','720p']};
ELS.vidres={value:'',innerHTML:''};
ELS.vid_res={value:''};
ELS.vid_model={value:'m1',options:[{value:'m1'},{value:'m3'}]};
ELS.vid_prompt={value:'',scrollIntoView(){}};
await setVideoResolution('720p');
out.afterRes=AICFG.video_resolution;
out.selRes=ELS.vidres.value;
await setVideoModel('m3');
out.afterModel=[AICFG.video_model,AICFG.video_resolution];
AICFG.video_resolution='8k';
vidFillResolution();
out.staleSel=ELS.vidres.value;
LOG.length=0;
VHIST=[{key:'k1',model:'m3',prompt:'p',opts:{resolution:'480p'}},
       {key:'k2',model:'m3',prompt:'p',opts:{resolution:'8k'}}];
ELS.vid_model.value='m1';
await vidHistReuse('k1');
out.logK1=LOG.slice();
LOG.length=0;
await vidHistReuse('k2');
out.logK2=LOG.slice();
out.done=TOASTS.some(x=>x.indexOf('Настройки задачи подставлены')>=0);
""", pre=r"""
let LOG=[],VHIST=[],VREFS=[];
function esc(s){return String(s);}
function vidCaps(){return {resolutions:['480p','720p']};}
function vidProfSummary(){}function vidApplyModel(){}
function renderVidRefs(){}function vidRefreshAutoShape(){}
function fetch(url,opts){
  const b=JSON.parse(opts.body);LOG.push(b.action);
  const d=b.action==='set_video_model'?{video_model:b.model,video_resolution:''}:{video_resolution:b.resolution};
  return Promise.resolve({json:()=>Promise.resolve(d)});}
""")
    assert res["afterRes"] == "720p" and res["selRes"] == "720p", res
    assert res["afterModel"] == ["m3", ""], res
    assert res["staleSel"] == "", res
    assert res["logK1"] == ["set_video_model", "set_video_resolution"], res
    assert res["logK2"] == [], res
    assert res["done"] is True, res


@node
def test_behav_video_insert_request_carries_only_the_contract_fields(tmp_path):
    """Запрос видео-вставки несёт серверный контракт: запрос, слот, спикер, длительность как есть, клип — без промпта и округления.

    Замена части test_video_insert_generation_contract (payload видео-карточки и связь с задачей).
    Вкладка «Видео», разметка и справки текстом остаются в старом тесте.
    """
    res = _case(tmp_path, "b3_video_insert.js", ("insGenVideo", "insVideoContext", "clipSpeaker"), r"""
CLIPS=[{xml:'D:/p/A.xml',name:'A',status:{},inserts:[{query:'кот',duration_sec:2.4,type:'video'}],job:{speaker:'spkA'}}];
curIns=0;
await insGenVideo(0,'b');
out.body=SENT.body;
out.ctxType=SENT.ctx&&SENT.ctx.type;
out.pending=CLIPS[0].inserts[0].video_job;
out.slot=CLIPS[0].inserts[0].video_slot;
""", pre=r"""
let curIns=-1;
const SENT={};
async function videoStart(body,ctx){SENT.body=body;SENT.ctx=ctx;}
function insVideoTarget(){return {token:'tok'};}
function insVideoFindTarget(){return null;}
function insVideoClearLink(){}
function progOpen(){}function progUpdate(){}function logReset(){}function hideProg(){}
function renderInsHost(){}function syncClipLists(){}
function insVideoBindJob(){}function insVideoApplyResult(){}function insVideoSettle(){}
function insVideoForgetPending(){}
""")
    assert res["body"] == {"query": "кот", "slot": "b", "speaker": "spkA",
                           "insert_duration": 2.4, "xml": "D:/p/A.xml"}, res
    assert res["ctxType"] == "insert", res
    assert res["pending"] == "pending" and res["slot"] == "b", res


# ============== 33. ЖЁЛТЫЕ И ВСТАВКИ ОДНОГО РОЛИКА — ОДНОЙ ПАЧКОЙ ==============
MARKUP_CLIP_PRE = r"""
let UICANCEL=false,LOCALQ=null,PROGQ=null;
const AICFG={step_local:{yellow:false,inserts:false},
  step_profiles:{yellow:'y',inserts:'i'},step_concurrency:{yellow:1,inserts:1}};
const SENT=[];
function localQStart(){}function localQSet(){}function localQEnd(){}
function progQueue(){}function progStep(){}function progDone(){}
function renderClips2(){}function clearHl(){}function loadWordsFor(){}
function insLog(){}function insAfterAI(){return Promise.resolve('');}
function clipSpeaker(){return null;}
function engLabel(s){return s;}function subSkipped(){return '';}
function sleep(){return Promise.resolve();}
async function fetch(){return {json:async()=>({subs:12,colored:0,ncams:2})};}
async function aiPost(url,body){
  SENT.push({url:url,batch:(body.batch===undefined?null:body.batch)});
  return {colored:[1],inserts:[{type:'photo',start_sec:1}],insTarget:1};}
"""


@node
def test_behav_markup_clip_sends_one_batch_to_yellow_and_inserts(tmp_path):
    """Жёлтые и вставки одного ролика уходят ОДНОЙ пачкой ИИ-вызовов.

    Они идут параллельно (Promise.all). Без общей пачки сервер вытесняет первый
    стартовавший вызов («вызов заменён новым запуском»). Одиночная разметка заводит
    пачку сама, как markupAllRun; из пакета пачка передаётся снаружи и не меняется.
    """
    res = _case(tmp_path, "b33_markup_clip.js", ("markupClip", "markupPlan", "qClipSum"), r"""
const c1={xml:'A.xml',name:'A',inserts:[],status:{},ins_rejected:[]};
CLIPS=[c1];
await markupClip(c1);
out.single=SENT.splice(0);
const c2={xml:'B.xml',name:'B',inserts:[],status:{},ins_rejected:[]};
await markupClip(c2,'mk_pack');
out.pack=SENT.splice(0);
""", pre=MARKUP_CLIP_PRE)
    assert sorted(x["url"] for x in res["single"]) == ["/api/ai_inserts", "/api/ai_yellow"], res
    batches = {x["batch"] for x in res["single"]}
    assert len(batches) == 1 and None not in batches, res
    assert next(iter(batches)).startswith("mk"), res
    assert sorted(x["url"] for x in res["pack"]) == ["/api/ai_inserts", "/api/ai_yellow"], res
    assert all(x["batch"] == "mk_pack" for x in res["pack"]), res
