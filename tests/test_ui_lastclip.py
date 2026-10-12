# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Метка «над этим клипом работал» в списках клипов всех трёх шагов.

Что стерегут тесты:

1. **По одной метке на шаг.** Шаг 1 — клип из превью/редактора нарезки (openEditClip),
   шаг 2 — клип из окна вставок (openInsertsFor), шаг 3 — клип открытой панели (selectAE).
   Открытие клипа на шаге 1 не двигает метку шага 2 и наоборот.
2. **По XML, а не по индексу.** Удаление клипа ВЫШЕ помеченного не сдвигает метку на
   соседа: она остаётся на том же клипе.
3. **Удаление помеченного** снимает метку, без ошибок.
4. **Переживает F5.** stateObj кладёт метки в состояние, applyState возвращает их
   на те же клипы. Старое состояние без поля: шагу 3 — клип открытой панели, шагам 1 и 2 — пусто.
5. **Стиль одной строкой.** Правило `.clip.cur` в app.css одно, с `border-left`.

Фронт-часть гоняет БОЕВЫЕ функции из static/app/*.js под node: вырезаются по имени
(как в test_ui_state_rev.py), DOM и модалки — заглушки. Копий логики в тесте нет.

Запуск: py -3.10 -m pytest tests/test_ui_lastclip.py -q -p no:cacheprovider
"""
import io
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
CSS = os.path.join(ROOT, "static", "app.css")
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _fn(src, name):
    """Тело функции name (с async, если есть). Приём как в test_ui_state_rev.py."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике нет функции {name}"
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


def _line(src, needle):
    """Строка-объявление (let X=...) целиком — без неё боевая функция не работает."""
    m = re.search(r"^.*%s.*$" % re.escape(needle), src, re.M)
    assert m, f"в исходнике нет объявления {needle}"
    return m.group(0)


# Заглушки — только внешние двери: DOM, модалки, тосты, сохранение, музыка/стили/вставки.
# Всё, что проверяется, — боевые функции из static/app/*.js.
STUBS = r"""
let ELS={},TOASTS=[],LOGS=[],OPENED=[],SAVES=0;
class El{
  constructor(id){this.id=id;this.style={};this.dataset={};this.disabled=false;this.textContent='';
    this._html='';this.children=[];this.checked=false;this.value='';this.className='';this.tabIndex=0;this.onclick=null;
    this.options=[];
    this.classList={add(){},remove(){},toggle(){},contains(){return false;}};}
  get innerHTML(){return this._html;}
  set innerHTML(v){this._html=String(v);this.children=[];}
  appendChild(c){this.children.push(c);return c;}
  setAttribute(n,v){this[n]=v;}
  focus(){}
  querySelector(){return new El('q');}
  querySelectorAll(){return [];}
}
function $(id){if(!ELS[id])ELS[id]=new El(id);return ELS[id];}
let document={createElement:tag=>new El(tag),querySelector(){return null;},querySelectorAll(){return [];}};
let localStorage={getItem:()=>null,setItem(){}};
function t(s){return String(s);}
function esc(s){return String(s==null?'':s);}
function ico(){return '';}
function plur(){return 'клип';}
function clipActs(){return '';}
function spkTagHTML(){return '';}
function spkSelHTML(){return '';}
function statusTags(){return '';}
function editedTag(){return '';}
function aeDone(){return false;}
function clipReady(){return false;}
function syncDelSel(){}
function syncNav(){}
function syncBuildBtn(){}
function markupSelCount(){}
function clipLabel(c){return c.name;}
function pvTitle(){}
function openModal(id){OPENED.push(String(id));}
function closeModal(){}
async function openPreview(){}
async function edOpen(){}
async function pvVoicePanel(){}
function toast(m){TOASTS.push(String(m));}
function uiLog(m){LOGS.push(String(m));}
function insEnsureUids(){}
function insHistReset(){}
function renderInsHost(){}
function openInsPart(){}
function ipvOpen(){}
function captureAE(){}
function defJob(){return {};}
function renderIns(){}
function styleKeyFor(){return '';}
function onStyleChange(){}
function ensureCustomOption(){}
function clipSpeaker(){return '';}
function musicSync(){}
function musicClipUI(){}
function musicPickEnsure(){}
function rotoSync(){}
function renderRenderDirField(){}
function loadWordsFor(){}
function saveState(){SAVES++;}
function stMigrateCamZoom(s){return s;}
function stMigrateIntroCam2(s){return s;}
function stMigrateCam2Zoom(s){return s;}
function stMigrateIntroPos2(s){return s;}
function val(){return '';}
function rendEngine(){return 'ae';}
function rendEngineSet(){}
function nCams(){return 2;}
function vidStateObj(){return {};}
function buildCamRows(){}
function renderQueue(){}
function segUI(){}
function asrMigrate(v){return v;}
function clipPathFix(p){return p;}
function spkEditUI(){}
function cutSummary(){}
function renderCutStagesUI(){}
function insHistTouch(){}
let STYLES={},SPEAKERS={},STYLESAVED='',CURSTYLE=null,HL=new Set(),BRK=new Set(),CNT=new Set(),JNS=new Set(),HLXML='';
let INS=[],INTRO=[],INTRO_PICK=-1,AEXML='',AERENDER='',CAMDIRS=[],CAMFILES=[],CAMFROM=[],QUEUE=[];
let CUT_STAGES={},CUT_THRESHOLDS={},SUBWANT='',SPKSAVED='',VIDSAVED=null,VREFS=[],STEP=1,IPVMODE='';
let curAE=-1,curEdit=-1,curIns=-1,CLIPS=[],AEMUSICDIR='';
// Двери шага 1–2, которые не открывают предпросмотр: раскладка камер, разметка, очистка тега.
let ASK=true;
function askConfirm(){return Promise.resolve(ASK);}
function syncClipLists(){}
function clearHl(){}
function cpvReset(){}
function errText(d){return String((d&&d.error)||d);}
// Сервер «не отвечает ошибкой» — раскладка закрывается тостом, метка уже должна стоять.
// Удаление клипа отвечает успехом: тесты меток проверяют путь удаления, а не сервер.
// Остальные двери по-прежнему «не отвечают ошибкой».
async function fetch(url){
  if(String(url).indexOf('/api/clip_delete')===0)return {json:async()=>({ok:true,files:[],bytes:0,sources:[],cams:[]})};
  return {json:async()=>({error:'нет сервера'})};
}
function effOutdir(c){return null;}
function uiBusyGuard(){return false;}
function uiBusySet(){}
function progOpen(){}
function progDone(){}
async function markupClip(){return true;}
"""


def _stand():
    """Боевой код меток: объявление состояния, раскладка трёх списков, открытие клипов,
    удаление, сохранение и восстановление состояния — вырезан из своих файлов."""
    q = _read(os.path.join(APP, "40-queue.js"))
    pv = _read(os.path.join(APP, "60-preview.js"))
    ins = _read(os.path.join(APP, "80-inserts.js"))
    ae = _read(os.path.join(APP, "90-ae.js"))
    boot = _read(os.path.join(APP, "99-boot.js"))
    cams = _read(os.path.join(APP, "88-cams.js"))
    ed = _read(os.path.join(APP, "70-editor.js"))
    parts = [
        STUBS,
        _line(boot, "let LASTCLIP="),
        _line(q, "let SEL_ANCHOR="),
        _line(q, "let DEL_CLIP_IDXS="),
        _line(q, "let TRASHROOTS="),
        _line(cams, "let CAMED="),
        _fn(q, "lastClipCls"),
        _fn(q, "markLastClip"),
        _fn(q, "clearPart"),
        _fn(cams, "openCamsFor"),
        _fn(ed, "markupOne"),
        _fn(q, "renderClips1"),
        _fn(q, "renderClips2"),
        _fn(q, "renderClips3"),
        _fn(q, "_spliceClip"),
        _fn(q, "delClips"),
        _fn(q, "delClipCall"),
        _fn(q, "delClipDiskPrepare"),
        _fn(q, "delClipDiskExecute"),
        _fn(q, "trashDirOf"),
        _fn(q, "trashRemember"),
        _fn(pv, "openEditClip"),
        _fn(ins, "openInsertsFor"),
        _fn(ae, "selectAE"),
        _fn(boot, "stateObj"),
        _fn(boot, "applyState"),
        # Геттер метки шага — одной строкой, чтобы сценарии читали её так же, как в бою
        "function marks(h){return h.children.map((r,i)=>(' '+r.className+' ').includes(' cur ')?i:-1).filter(i=>i>=0);}",
    ]
    return "\n".join(parts)


def _errors(logs):
    """Ошибки из журнала. Удаление в корзину пишет и итог («удаление в корзину: …») — он не ошибка."""
    return [x for x in logs if "ошиб" in x or "не прочитано" in x]


def _run(tmp_path, name, body):
    # Сценарии зовут async-функции (openEditClip) — верхний уровень заворачиваем в async-IIFE,
    # а отказ печатаем и отдаём ненулевым кодом, чтобы исключение не осталось «зелёным».
    script = (_stand() + "\n(async()=>{\n" + body
              + "\n})().catch(e=>{console.error(e&&e.stack||e);process.exitCode=1;});\n")
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:1200]
    return json.loads(p.stdout.strip().splitlines()[-1])


THREE = """
CLIPS=[{name:'A',xml:'A.xml'},{name:'B',xml:'B.xml'},{name:'C',xml:'C.xml'}];
"""

RENDER_ALL = "renderClips1();renderClips2();renderClips3();"


@node
def test_open_edit_clip_marks_one_row_on_step_1(tmp_path):
    """openEditClip(1) — метка шага 1 у второй строки и ни у какой другой; шаг 2 не тронут."""
    body = THREE + RENDER_ALL + """
await openEditClip(1);
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),last:LASTCLIP,
  logs:LOGS,toasts:TOASTS}));
"""
    r = _run(tmp_path, "step1.js", body)
    assert r["m1"] == [1], f"метка шага 1 не на второй строке: {r['m1']}"
    assert r["m2"] == [], "открытие на шаге 1 пометило строку шага 2"
    assert r["last"]["1"] == "B.xml" and r["last"]["2"] == ""
    assert r["logs"] == [], f"ошибки при открытии клипа: {r['logs']}"


@node
def test_inserts_mark_is_step_2_only_and_steps_stay_independent(tmp_path):
    """Шаги независимы: метка шага 1 стоит, открытие вставок на первом клипе ставит метку
    шага 2 на первую строку и не сдвигает метку шага 1."""
    body = THREE + RENDER_ALL + """
await openEditClip(1);
openInsertsFor(0);
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),last:LASTCLIP,logs:LOGS}));
"""
    r = _run(tmp_path, "steps.js", body)
    assert r["m1"] == [1], "открытие вставок сдвинуло метку шага 1"
    assert r["m2"] == [0], f"метка шага 2 не на первой строке: {r['m2']}"
    assert r["last"]["1"] == "B.xml" and r["last"]["2"] == "A.xml"
    # Метка шага 3 идёт за панелью: openInsertsFor зовёт selectAE, если панель на другом клипе.
    # Так было и до меток (шаг 3 показывал curAE) — поведение шага 3 не меняем.
    assert r["last"]["3"] == "A.xml", "метка шага 3 не следует за панелью клипа"
    assert r["logs"] == []


@node
def test_step_3_mark_follows_the_selected_clip(tmp_path):
    """selectAE(2) — метка шага 3 у третьей строки, как и раньше (curAE)."""
    body = THREE + RENDER_ALL + """
selectAE(2);
console.log(JSON.stringify({m3:marks($('clips3')),lastAE:curAE,lastC:LASTCLIP[3],logs:LOGS}));
"""
    r = _run(tmp_path, "step3.js", body)
    assert r["m3"] == [2] and r["lastAE"] == 2 and r["lastC"] == "C.xml"
    assert r["logs"] == []


@node
def test_mark_stays_on_its_clip_when_a_clip_above_is_deleted(tmp_path):
    """Удаление клипа ВЫШЕ помеченного: метка остаётся на том же клипе (по XML), а не
    перескакивает на соседа по индексу."""
    body = THREE + RENDER_ALL + """
await openEditClip(2);
await delClips([0]);
await delClipDiskExecute();
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),m3:marks($('clips3')),
  names:CLIPS.map(c=>c.name),last:LASTCLIP,curAE:curAE,logs:LOGS}));
"""
    r = _run(tmp_path, "above.js", body)
    assert r["names"] == ["B", "C"]
    assert r["m1"] == [1], f"метка шага 1 уехала с клипа C: {r['m1']}"
    assert r["m3"] == [1], f"метка шага 3 уехала с клипа C: {r['m3']}"
    assert r["last"]["1"] == "C.xml" and r["curAE"] == 1
    assert _errors(r["logs"]) == [], r["logs"]


@node
def test_deleting_the_marked_clip_drops_the_mark(tmp_path):
    """Удаление помеченного клипа: метка шага 1 с него уходит; панель шага 3 (он был открыт)
    садится на первый живой клип A — и метка шага 3 переезжает на него (selectAE). Ошибок нет."""
    body = THREE + RENDER_ALL + """
await openEditClip(1);
await delClips([1]);
await delClipDiskExecute();
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),m3:marks($('clips3')),
  names:CLIPS.map(c=>c.name),last:LASTCLIP,logs:LOGS}));
"""
    r = _run(tmp_path, "delmarked.js", body)
    assert r["names"] == ["A", "C"]
    assert r["m1"] == [] and r["m2"] == []
    assert r["m3"] == [0], f"панель шага 3 не села на живой клип: {r['m3']}"
    assert r["last"]["1"] == "" and r["last"]["3"] == "A.xml"
    assert _errors(r["logs"]) == [], f"ошибки при удалении помеченного: {r['logs']}"


@node
def test_папки_корзин_переживают_f5(tmp_path):
    """Удалили клип из чужой папки → её путь в TRASHROOTS и в stateObj; после F5 (applyState)
    путь на месте. Старое состояние без поля trashroots список не трогает."""
    body = THREE + RENDER_ALL + """
CLIPS[1].xml='C:/cut/B.xml';
await delClips([1]);
await delClipDiskExecute();
const snap=JSON.stringify(stateObj());
TRASHROOTS=[];CLIPS=[];
applyState(JSON.parse(snap));
const restored=TRASHROOTS.slice();
const old=JSON.parse(snap);delete old.trashroots;
TRASHROOTS=['D:/keep'];
applyState(old);
console.log(JSON.stringify({snapHas:JSON.parse(snap).trashroots||null,restored:restored,
  oldKept:TRASHROOTS.slice(),logs:LOGS}));
"""
    r = _run(tmp_path, "trashroots_f5.js", body)
    assert r["snapHas"] == ["C:/cut"], f"папка корзины не попала в состояние: {r['snapHas']}"
    assert r["restored"] == ["C:/cut"], f"после F5 папка корзины пропала: {r['restored']}"
    assert r["oldKept"] == ["D:/keep"], "старое состояние без поля стёрло список папок"
    assert _errors(r["logs"]) == [], r["logs"]


@node
def test_marks_survive_f5(tmp_path):
    """stateObj → перезагрузка (метки и клипы сброшены) → applyState: метки на тех же клипах."""
    body = THREE + RENDER_ALL + """
await openEditClip(1);
openInsertsFor(2);
const snap=JSON.stringify(stateObj());
// перезагрузка: страница пустая, состояние приходит с сервера
CLIPS=[];LASTCLIP={1:'',2:'',3:''};curAE=-1;
applyState(JSON.parse(snap));
renderClips1();renderClips2();renderClips3();
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),m3:marks($('clips3')),
  last:LASTCLIP,curAE:curAE,logs:LOGS,snapHasLast:JSON.parse(snap).lastclip!==undefined}));
"""
    r = _run(tmp_path, "f5.js", body)
    assert r["snapHasLast"] is True, "метки не попали в stateObj — после F5 их не на чем восстановить"
    assert r["m1"] == [1], f"после F5 метка шага 1 пропала или уехала: {r['m1']}"
    assert r["m2"] == [2], f"после F5 метка шага 2 пропала или уехала: {r['m2']}"
    assert r["m3"] == [2], f"после F5 метка шага 3 пропала или уехала: {r['m3']}"
    assert r["last"] == {"1": "B.xml", "2": "C.xml", "3": "C.xml"}
    assert r["logs"] == [], f"состояние восстановлено с ошибкой: {r['logs']}"


@node
def test_old_state_without_the_field_marks_step_3_only(tmp_path):
    """Состояние до меток (поля lastclip нет): шагу 3 — клип открытой панели (как было),
    шаги 1 и 2 — пусто."""
    body = THREE + """
applyState({CLIPS:[{name:'A',xml:'A.xml'},{name:'B',xml:'B.xml'},{name:'C',xml:'C.xml'}],curAE:1});
renderClips1();renderClips2();renderClips3();
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),m3:marks($('clips3')),
  last:LASTCLIP,logs:LOGS}));
"""
    r = _run(tmp_path, "old.js", body)
    assert r["m1"] == [] and r["m2"] == [], "старое состояние без поля отметило шаги 1 или 2"
    assert r["m3"] == [1], f"старое состояние: шаг 3 потерял клип панели: {r['m3']}"
    assert r["logs"] == []


@node
def test_camera_layout_door_marks_step_1(tmp_path):
    """Раскладка камер (кнопка на шаге 1) — работа с клипом: метка шага 1 на нём, шаг 2 не тронут."""
    body = THREE + RENDER_ALL + """
await openCamsFor(2);
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),last:LASTCLIP,logs:LOGS}));
"""
    r = _run(tmp_path, "cams.js", body)
    assert r["m1"] == [2], f"раскладка камер не пометила клип на шаге 1: {r['m1']}"
    assert r["m2"] == [], "раскладка камер пометила строку шага 2"
    assert r["last"]["1"] == "C.xml"
    assert r["logs"] == []


@node
def test_markup_one_door_marks_step_2(tmp_path):
    """Разметка одного клипа (кнопка на шаге 2) — метка шага 2 на нём, шаг 1 не тронут."""
    body = THREE + RENDER_ALL + """
await markupOne(0);
console.log(JSON.stringify({m1:marks($('clips1')),m2:marks($('clips2')),last:LASTCLIP,logs:LOGS}));
"""
    r = _run(tmp_path, "markup.js", body)
    assert r["m2"] == [0], f"разметка не пометила клип на шаге 2: {r['m2']}"
    assert r["m1"] == [], "разметка пометила строку шага 1"
    assert r["last"]["2"] == "A.xml"
    assert r["logs"] == []


@node
def test_clear_tag_door_marks_step_2_only_after_confirm(tmp_path):
    """Крестик на теге шага 2 (clearPart): отмена не ставит метку, подтверждение — ставит."""
    body = THREE + RENDER_ALL + """
ASK=false;
await clearPart(1,'inserts');
const cancelled={m2:marks($('clips2')),last2:LASTCLIP[2]};
ASK=true;
await clearPart(1,'inserts');
console.log(JSON.stringify({cancelled,m2:marks($('clips2')),m1:marks($('clips1')),last:LASTCLIP,
  logs:LOGS,toasts:TOASTS}));
"""
    r = _run(tmp_path, "clear.js", body)
    assert r["cancelled"]["m2"] == [] and r["cancelled"]["last2"] == "", \
        "отмена очистки тега всё равно поставила метку"
    assert r["m2"] == [1], f"после подтверждения метка шага 2 не на клипе: {r['m2']}"
    assert r["m1"] == []
    assert r["last"]["2"] == "B.xml"
    assert not any("ОШИБКА" in x for x in r["logs"]), r["logs"]
    assert not any("⚠" in x for x in r["toasts"]), r["toasts"]


def test_clip_cur_rule_is_single_and_keeps_the_border_mark():
    """Правило .clip.cur в app.css одно и несёт border-left — одна метка на все три списка."""
    css = _read(CSS)
    rules = re.findall(r"(?m)^\s*\.clip\.cur\s*\{([^}]*)\}", css)
    assert len(rules) == 1, f".clip.cur объявлен {len(rules)} раз, должен один"
    assert "border-left:2px solid var(--tx)" in rules[0], rules[0]
    # Ни один другой селектор не красит .cur у клипа цветом рамки (иначе метка двоится)
    assert not re.search(r"\.clip\.cur[^{]*\{[^}]*box-shadow", css)
