# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Одна форма прогресса на весь интерфейс: `static/app/55-progress.js`.

Пойманный дефект (скриншоты владельца): в шапке окна прогресса висела длинная строка
«клип 1 из 4 · 03_C1476.xml, 04_C1477.xml, … · 4с · [max_tokens] model=… → max_tokens=17120»,
а в строках роликов второй строкой шёл сырой хвост лога («думает: 560 симв. размышлений,
ответа пока нет · не хочешь ж…»). «Клип 1 из 4» при четырёх параллельных роликах неверен по
смыслу: в работе были все четыре.

Сторожим четыре вещи:

1. Шапка — заголовок, «k из N» и оценка «сколько ещё»; имён файлов и строк лога в ней нет.
   Сырую строку отсекает ОДИН фильтр (progHuman), поэтому достаточно одной двери,
   передавшей хвост, чтобы простыня вернулась на экран (мутация валит стенд);
2. Строка ролика — имя (обрезано многоточием по CSS, полное лежит в title) и КОРОТКИЙ
   человеческий статус из словаря PROGEV; чего в словаре нет — в строку не выводится;
3. «Строка лога → код события» разбирается в ОДНОМ месте (PROGLOG), по тесту на правило;
4. Разметка окна (классы строк и поля шапки) встречается только в самом модуле: вторую
   копию формы больше негде завести.

Запуск: py -3.10 -m pytest tests/test_progress_form.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

APP = os.path.join(ROOT, "static", "app")
PROG_JS = os.path.join(APP, "55-progress.js")
EDITOR_JS = os.path.join(APP, "70-editor.js")
AE_JS = os.path.join(APP, "90-ae.js")
CORE_JS = os.path.join(APP, "00-core.js")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _extract(src: str, marker: str) -> str:
    """Кусок исходника от marker до парной закрывающей скобки."""
    assert marker in src, "не нашёл в исходнике: %s" % marker
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("не закрылась скобка у %s" % marker)


def _fn(path: str, name: str) -> str:
    src = _read(path)
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "не нашёл функцию %s в %s" % (name, os.path.basename(path))
    return _extract(src, m.group(0))


def _run_node(tmp_path: Any, name: str, script: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON (последняя строка stdout)."""
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, "node: %s" % (proc.stderr or proc.stdout)[:600]
    lines = [x.strip() for x in proc.stdout.strip().splitlines() if x.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


# Общий стенд: подделки DOM и мелочей интерфейса + НАСТОЯЩИЙ модуль формы целиком.
# Модуль грузим как есть (а не по кускам): так стенд ловит и связку функций между собой.
_PRELUDE = """
const fs=require('fs');
let LANG='ru',I18N={};
__T__
__FMTLOG__
__ESC__
// состояние, которое в бою живёт в других файлах static/app/
let LOGCACHE=[],LOGSINCE=0,CLIPS=[],curAE=-1,curIns=-1,AICFG=null;
let VIDPOLL=false,UIBUSY=false,RENDERSEEN=0,RENDERPOLLFAIL=0,RENDERENGINE='ae';
function uiLog(){}function toast(){}function goStep(){}function vidCancel(){}function saveState(){}
function refreshLog(){}function renderClips2(){}function renderInsHost(){}function insLog(){}
function clearHl(){}function loadWordsFor(){}function uiBusySet(){}function insTarget(){}
function sleep(){return new Promise(r=>setTimeout(r,1));}
function defJob(){return {};}function introRowsFromAI(){return [];}function midCount(){return 0;}
function askConfirm(){return Promise.resolve(true);}function errText(e){return String(e);}
function plur(n,a,b,c){return c;}function subSkipped(){return '';}function engLabel(){return 'whisper';}
function aiStepConc(){return 1;}function loadAIProfiles(){return Promise.resolve();}
function captureAE(){}function aewRender(){}function renderIntro(){}function syncClipLists(){}
function insAfterAI(){return Promise.resolve(null);}function renderClips1(){}function renderClips3(){}
const EL={};
function mkEl(){return {innerHTML:'',textContent:'',className:'',value:'',disabled:false,
  style:{},scrollHeight:120,clientHeight:40,scrollTop:0,scrollLeft:7,scrolled:null,
  classList:{add(){},remove(){},contains(){return false;}},scrollIntoView(o){this.scrolled=o;},focus(){}};}
function $(id){return EL[id]||(EL[id]=mkEl());}
__PROG__
"""


def _stand(body: str, *extras: str) -> str:
    """Полный стенд: подделки + настоящий модуль + (необязательно) боевые функции + сценарий."""
    src = _read(CORE_JS)
    out = _PRELUDE
    out = out.replace("__T__", _extract(src, "function t(s, vars)"))
    out = out.replace("__FMTLOG__", _extract(src, "function fmtLog(l)"))
    out = out.replace("__ESC__", _extract(src, "function esc(s)"))
    out = out.replace("__PROG__", _read(PROG_JS))
    for e in extras:
        out += "\n" + e
    return out + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exit(3);});\n"


_TAIL = """
fs.writeSync(1, JSON.stringify(out)+'\\n');
process.exit(0);
"""


# ---- 1. Шапка окна: без имён файлов и без строк лога ----------------------

@node
def test_header_shows_count_not_file_names_and_not_raw_log(tmp_path: Any) -> None:
    """Шапка — «k из N» и этап; имя файла и сырая строка лога в неё не попадают.

    Мутация «вернуть в шапку сырой stage» (progHeadText без progHuman) валит проверку
    `header_has_no_raw`: ровно так в окне и появлялось
    «[max_tokens] model=meta/muse-spark-1.3-contributor → max_tokens=17120».
    """
    raw = "[max_tokens] model=meta/muse-spark-1.3-contributor \u2192 max_tokens=17120"
    script = _stand("""
const out={};
progOpen({title:'РАЗМЕТКА 3/3 — ВСТАВКИ (ИИ)',items:[{name:'03_C1476.xml'},{name:'04_C1477.xml'}]});
progItem('03_C1476.xml','inserts',{detail:__RAW__});
progItem('04_C1477.xml','done',{detail:'жёлтых: 12 · вставок: 13'});
progUpdate(null,__RAW__,'РАЗМЕТКА 3/3 — ВСТАВКИ (ИИ)');
out.title=$('progStage').textContent;
out.header=$('progSub').textContent;
out.rows=$('qlist').innerHTML;
""" + _TAIL)
    out = _run_node(tmp_path, "prog_head.js",
                    script.replace("__RAW__", json.dumps(raw, ensure_ascii=False)))

    # 1. Заголовок задачи — на месте; в шапке «k из N», а не «клип 1 из 4»
    assert out["title"] == "РАЗМЕТКА 3/3 — ВСТАВКИ (ИИ)", out["title"]
    assert "1 из 2" in out["header"], out["header"]
    assert "клип" not in out["header"], out["header"]

    # 2. Ни имён файлов, ни строки лога в шапке
    assert ".xml" not in out["header"], out["header"]
    assert "max_tokens" not in out["header"] and "model=" not in out["header"], out["header"]

    # 3. Строки роликов: имя + человеческий статус из словаря
    assert "03_C1476.xml" in out["rows"] and "04_C1477.xml" in out["rows"], out["rows"]
    assert "вставки (ИИ)" in out["rows"], out["rows"]
    assert ">готово<" in out["rows"], out["rows"]

    # 4. Сырой хвост лога в строку не попал, человеческий итог — попал
    assert "max_tokens" not in out["rows"], out["rows"]
    assert "жёлтых: 12 · вставок: 13" in out["rows"], out["rows"]

    # 5. Полное имя — в title строки (в списке его режет многоточие)
    assert 'title="03_C1476.xml — вставки (ИИ)"' in out["rows"], out["rows"]


@node
def test_unknown_event_never_reaches_a_row(tmp_path: Any) -> None:
    """Чего нет в словаре — в строку не выводится: код события остаётся в логе."""
    script = _stand("""
const out={};
progOpen({title:'Разметка',items:[{name:'01_ролик.xml'}]});
progItem('01_ролик.xml','zzz_секретное_событие',{detail:'  … 12с · думает: 560 симв. размышлений'});
out.rows=$('qlist').innerHTML;
out.header=$('progSub').textContent;
""" + _TAIL)
    out = _run_node(tmp_path, "prog_unknown.js", script)

    assert "zzz_секретное_событие" not in out["rows"], out["rows"]
    assert "560 симв" not in out["rows"], out["rows"]
    assert "в очереди" in out["rows"], out["rows"]      # строка осталась в исходном статусе
    assert "zzz" not in out["header"], out["header"]


@node
def test_second_line_is_only_human_error(tmp_path: Any) -> None:
    """Ошибка — короткая причина во второй строке, а не простыня на пол-экрана."""
    long_reason = ("нет ключа API (401) — проверь ключ в настройках, "
                   "а потом перезапусти сервер и повтори попытку ещё раз, и ещё, и ещё разок")
    script = _stand("""
const out={};
progOpen({title:'Разметка',items:[{name:'01_ролик.xml',stage:'error',reason:__REASON__}]});
out.rows=$('qlist').innerHTML;
""" + _TAIL)
    out = _run_node(tmp_path, "prog_err.js",
                    script.replace("__REASON__", json.dumps(long_reason, ensure_ascii=False)))

    assert ">ошибка<" in out["rows"], out["rows"]
    assert "ошибка: нет ключа API (401)" in out["rows"], out["rows"]
    assert "и ещё разок" not in out["rows"], "причина не укорочена: %r" % out["rows"]
    assert "…" in out["rows"], "нет многоточия у обрезанной причины: %r" % out["rows"]


# ---- 2. Правила разбора «строка лога → код события» -----------------------

PARSE_RULES = (
    ("[max_tokens] model=meta/muse-spark-1.3-contributor \u2192 max_tokens=17120", "setup"),
    ("[reasoning] model=x -> reasoning=high", "setup"),
    ("[cache] model=x -> cache_control=ephemeral", "setup"),
    ("12с · думает: 560 симв. размышлений, ответа пока нет · не хочешь ждать — «Стоп»", "think"),
    ("думает: 560 симв. размышлений, ответа пока нет · не хочешь ждать — отмени", "think"),
    ("  … 24с · пишет ответ: 700 симв. (размышлений 300 симв.)", "answer"),
    ("пишет ответ: 700 симв.", "answer"),
    ("  … 3с · ждём первый чанк от провайдера", "waiting"),
    ("! апстрим провайдера занят — повторяю (1/3)…", "retry"),
    ("видео: задача abc принята, жду готовности…", "video"),
    ("видео: in_progress… (12с)", "video"),
)


@pytest.mark.parametrize("line,event", PARSE_RULES)
def test_log_line_maps_to_event_code(line: str, event: str) -> None:
    """Каждое правило разбора закрыто тестом: строка лога → код события.

    Проверяем боевым кодом модуля (не копией правил в тесте): копия разошлась бы с ним.
    """
    m = re.search(r"const PROGLOG=\[(.*?)\];", _read(PROG_JS), re.DOTALL)
    assert m, "в модуле нет таблицы правил PROGLOG"
    body = m.group(1)
    fn = _extract(_read(PROG_JS), "function progEventFromLog(")
    script = """
const PROGLOG=[%s].map(r=>[new RegExp(r[0].source),r[1]]);
%s
const out={ev:progEventFromLog(%s),none:progEventFromLog('  03_C1476.xml нарезан, 12.3с')};
require('fs').writeSync(1,JSON.stringify(out)+'\\n');process.exit(0);
""" % (body, fn, json.dumps(line, ensure_ascii=False))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr[:400]
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert res["ev"] == event, "строка %r разобрана как %r" % (line, res["ev"])


@node
def test_structured_event_code_wins_over_text(tmp_path: Any) -> None:
    """Сервер может положить код события структурно (v.ev) — тогда разбор строки не нужен."""
    script = _stand("""
const out={};
out.code=progEventOf({t:'режу {name}',v:{name:'01.xml',ev:'cut'}});
out.fromText=progEventOf('   … 12с · думает: 42 симв. размышлений');
out.none=progEventOf('03_C1476.xml нарезан');
out.short=progEventFromLog('');
""" + _TAIL)
    out = _run_node(tmp_path, "prog_ev.js", script)
    assert out["code"] == "cut", out
    assert out["fromText"] == "think", out
    assert out["none"] == "" and out["short"] == "", out


# ---- 3. Разметка окна живёт только в модуле ------------------------------

# Классы строк и поля окна: своя копия формы в другом файле — это ровно тот дефект,
# из-за которого окно прогресса приходилось править в нескольких местах.
MARKUP_TOKENS = ("qrow", "qname", "qdetail", "qstage", "qwrap", "qlist",
                 "progStage", "progSub", "progFill", "progPct",
                 "pmFill", "pmText", "pmPct", "progmini", "qcur")


def test_progress_markup_lives_in_one_module() -> None:
    """Разметка прогресса — только в `static/app/55-progress.js`.

    Нашлась в другом файле: рисуй окно через progOpen/progItem/progDone (или его двери
    progShow/progUpdate/progQueue/progStep/queueRender/localQSet), а не своей разметкой —
    иначе правка формы снова начнёт теряться в копиях.
    """
    bad = []
    files = sorted(f for f in os.listdir(APP) if f.endswith(".js"))
    assert "55-progress.js" in files, "модуль формы прогресса пропал из static/app/"
    for f in files:
        if f == "55-progress.js":
            continue
        for n, line in enumerate(_read(os.path.join(APP, f)).splitlines(), 1):
            code = line.strip()
            if code.startswith("//") or code.startswith("*"):
                continue          # упоминание в комментарии — не разметка
            for tok in MARKUP_TOKENS:
                if tok in code:
                    bad.append("%s:%d — %s" % (f, n, tok))
    assert not bad, ("разметка прогресса ушла из общего модуля "
                     "(static/app/55-progress.js):\n" + "\n".join(bad))


def test_status_dictionary_is_single_and_translated() -> None:
    """Словарь «событие → статус» один, и каждая его строка переведена."""
    prog = _read(PROG_JS)
    m = re.search(r"const PROGEV=\{(.*?)\};", prog, re.DOTALL)
    assert m, "в модуле нет словаря PROGEV"
    pairs = re.findall(r"(\w+):t\('([^']+)'\)", m.group(1))
    assert len(pairs) >= 20, "словарь статусов подозрительно короткий: %r" % pairs
    en = json.loads(_read(EN))
    for code, label in pairs:
        assert label in en, "нет перевода для статуса %s (%r)" % (code, label)
    for code in ("wait", "cut", "think", "answer", "done", "error", "stopped", "render"):
        assert code in dict(pairs), "в словаре нет события %s" % code


# ---- 4. Стенды мест из инвентаризации ------------------------------------

def test_markup_and_intro_doors_use_the_common_form() -> None:
    """Разметка и ИИ-интро идут через общий модуль, а не через свою разметку.

    Двери открывают окно через progOpen (не своей копией), строки ведёт модуль
    (localQStart/localQSet/progItem), а хвост лога превращается в КОД события —
    в строку ролика он не попадает.
    """
    for name in ("markupOne", "markupAll", "markupPhase"):
        body = _fn(EDITOR_JS, name)
        assert "progOpen({title:" in body, "%s открывает окно мимо progOpen" % name

    run = _fn(EDITOR_JS, "markupAllRun")
    assert "localQStart(list.map(c=>c.name))" in run, "пакетная разметка не завела строки"
    assert "localQSet(c.name,'subs','')" in run and "localQSet(c.name,'files','')" in run, \
        "этапы разметки не пишутся в строки"
    assert "finally{localQEnd();}" in run, "строки прогона не закрываются в finally"
    assert "qstage" not in run and "qrow" not in run, "разметка рисует свою строку"

    post = _fn(EDITOR_JS, "aiPost")
    assert "progEventFromLog(s)" in post, "хвост лога не разбирается в код события"
    assert "progItem(row.name,ev)" in post, "статус строки пишется мимо общей двери"
    assert "localQSet(row.name,row.stage,s)" not in post, \
        "сырой хвост лога снова пишется в строку ролика"

    intro = _fn(AE_JS, "aiIntroAll")
    assert "progOpen({title:t('ИИ интро')})" in intro, "ИИ-интро открывает окно мимо progOpen"
    run = _fn(AE_JS, "aiIntroAllRun")
    assert "localQStart(list.map(c=>c.name))" in run, "пакетное ИИ-интро не завело строки"
    assert "finally{localQEnd();}" in run, "строки прогона не закрываются в finally"

    one = _fn(AE_JS, "aiIntroOne")
    for frag in ("localQSet(c.name,'intro','')", "localQSet(c.name,'done',t('строк интро: ')",
                 "localQSet(c.name,'error',''+e)", "localQSet(c.name,'wait',t('нет субтитров'))"):
        assert frag in one, "этап ИИ-интро потерялся: %r" % frag


def test_render_and_cut_doors_use_the_common_form() -> None:
    """Рендер AE/встроенный и нарезка: окно открывает модуль, строки — общая дверь."""
    start = _read(AE_JS)
    assert "progOpen({title:rendEngineLabel()})" in start, \
        "рендер открывает окно мимо progOpen"
    poll = _fn(AE_JS, "pollRender")
    assert "queueRender(d)" in poll, "рендер рисует строки мимо общей двери queueRender"
    assert "progQueue(rendEngineLabel(),doneN,total||0)" in poll, \
        "в шапке рендера не «готово из скольких»"
    assert "d.cur" not in poll.split("const stageEv")[0], \
        "имя файла вернулось в шапку рендера"
    assert "progEtaSet(etaTot)" in poll, "оценка времени не доходит до шапки"

    job = _fn(os.path.join(APP, "40-queue.js"), "jobProg")
    assert "progQueue(title,Math.max(0,p.i-1),p.n)" in job, \
        "шапка нарезки/сборки не «готово из скольких»"
    assert "p.name" not in job, "имя файла вернулось в шапку нарезки"

    poll = _fn(os.path.join(APP, "40-queue.js"), "pollJob")
    assert "progUpdate(pr.frac,jobStageText(),title,pr.sub)" in poll, \
        "этап нарезки берётся не кодом события"
    assert "jobStageText" in _read(os.path.join(APP, "40-queue.js")), \
        "пропала дверь «код события по последней строке лога»"


@node
def test_markup_run_through_module_stand(tmp_path: Any) -> None:
    """Стенд разметки (шаг 2): настоящий markupAllRun + настоящий модуль.

    Во время ИИ-фазы в строке ролика виден словарный статус («думает»), а сырая строка
    лога («560 симв. размышлений») в окне не появляется ни в шапке, ни в строке.
    """
    think = "[клип-А]   … 12с · думает: 560 симв. размышлений, ответа пока нет · не хочешь ждать — «Стоп»"
    body = """
const out={};
CLIPS=[{xml:'out/клип-А.xml',name:'клип-А.xml',status:{},inserts:[]},
       {xml:'out/клип-Б.xml',name:'клип-Б.xml',status:{},inserts:[]}];
await markupAllRun('whisper',CLIPS,['yellow']);
out.rows=$('qlist').innerHTML;
out.header=$('progSub').textContent;
out.snap=globalThis.SNAP||null;
""" + _TAIL
    script = _stand(body, _fn(EDITOR_JS, "markupAllRun"), _fn(EDITOR_JS, "qClipSum"),
                    _fn(EDITOR_JS, "aiPost"), _fn(EDITOR_JS, "logTail"),
                    _extract(_read(os.path.join(APP, "50-chrome.js")), "function mergeLog(d)"),
                    """
let SNAP=null;
async function fetch(url,opt){
  if(url.indexOf('/api/status')===0){
    return {json:async()=>({log:[__THINK__],log_total:1})};}
  if(url.indexOf('/api/ai_yellow')===0){
    await new Promise(r=>setTimeout(r,40));       // хвост лога успевает обновиться
    if(!globalThis.SNAP)globalThis.SNAP={rows:$('qlist').innerHTML,header:$('progSub').textContent};
    return {json:async()=>({colored:3})};}
  if(url.indexOf('/api/xml_state')===0)return {json:async()=>({subs:5,colored:0,ncams:1})};
  return {json:async()=>({})};
}
""".replace("__THINK__", json.dumps(think, ensure_ascii=False)))
    out = _run_node(tmp_path, "prog_markup.js", script)

    snap = out["snap"]
    assert snap, "ИИ-фаза не дошла до запроса — стенд сломан"
    assert "думает" in snap["rows"], "словарный статус не появился в строке: %r" % snap["rows"]
    assert "560 симв" not in snap["rows"], "сырой хвост лога утёк в строку: %r" % snap["rows"]
    assert "560 симв" not in snap["header"], "сырой хвост лога утёк в шапку: %r" % snap["header"]
    assert "думает" in snap["header"], "этап не виден в шапке: %r" % snap["header"]
    assert "из 2" in snap["header"], "в шапке нет «сколько готово из скольких»: %r" % snap["header"]
    assert ".xml" not in snap["header"], "имя файла в шапке: %r" % snap["header"]

    assert "клип-А.xml" in out["rows"] and "клип-Б.xml" in out["rows"], out["rows"]
    assert "2 из 2" not in out["header"] and ".xml" not in out["header"], out["header"]
    assert "Размечено клипов: 2" in out["header"], out["header"]


@node
def test_intro_clip_through_module_stand(tmp_path: Any) -> None:
    """Стенд ИИ-интро: настоящий aiIntroOne — этап в строке, итог по клипу, без сырого лога."""
    line = "[клип-Б]   … 9с · пишет ответ: 300 симв."
    body = """
const out={};
const c={xml:'out/клип-Б.xml',name:'клип-Б.xml',status:{subs:5},inserts:[],job:{}};
localQStart([c.name]);
progOpen({title:'ИИ интро'});
localQStart([c.name]);
out.res=await aiIntroOne(c,'b1');
out.rows=$('qlist').innerHTML;
out.snap=globalThis.SNAP||null;
""" + _TAIL
    script = _stand(body, _fn(AE_JS, "aiIntroOne"), _fn(EDITOR_JS, "aiPost"),
                    _fn(EDITOR_JS, "logTail"),
                    _extract(_read(os.path.join(APP, "50-chrome.js")), "function mergeLog(d)"),
                    """
let SNAP=null;
async function fetch(url,opt){
  if(url.indexOf('/api/status')===0)return {json:async()=>({log:[__LINE__],log_total:1})};
  if(url.indexOf('/api/ai_intro')===0){
    await new Promise(r=>setTimeout(r,40));
    globalThis.SNAP={rows:$('qlist').innerHTML,header:$('progSub').textContent};
    return {json:async()=>({intro_rows:[{count:3}]})};}
  return {json:async()=>({})};
}
""".replace("__LINE__", json.dumps(line, ensure_ascii=False)))
    out = _run_node(tmp_path, "prog_intro.js", script)

    assert out["res"] == "ok", out
    snap = out["snap"]
    assert snap, "стенд не дошёл до запроса интро"
    assert "пишет ответ" in snap["rows"], "этап не появился в строке: %r" % snap["rows"]
    assert "300 симв" not in snap["rows"], "сырая строка лога утекла в строку: %r" % snap["rows"]

    assert "клип-Б.xml" in out["rows"], out["rows"]
    assert "готово" in out["rows"], out["rows"]
    assert "строк интро: 1" in out["rows"], out["rows"]


@node
def test_render_run_through_module_stand(tmp_path: Any) -> None:
    """Стенд рендера: настоящий pollRender — «k из N» в шапке, статусы строк из словаря."""
    body = """
const out={};
let calls=0;
globalThis.setTimeout=function(){return 0;};      // второй тик не нужен: смотрим первый
await pollRender();
out.header=$('progSub').textContent;
out.rows=$('qlist').innerHTML;
calls=0;
""" + _TAIL
    script = _stand(body, _fn(AE_JS, "pollRender"), _fn(AE_JS, "rendEngineLabel"),
                    """
async function fetch(url,opt){
  return {json:async()=>({running:true,engine:'ae',pct:0.5,stage_total:2,stage_done:1,
    eta_total:90,log:[],ae:'AE 24.6',out_dir:'C:\\\\exp',
    items:[{name:'01_ролик',stage:'done',path:'exp/01.mov'},
           {name:'02_ролик',stage:'render',pct:0.25,reason:''}]})};
}
""")
    out = _run_node(tmp_path, "prog_render.js", script)

    assert "1 из 2" in out["header"], out["header"]
    assert "~" in out["header"], "оценка времени не показана: %r" % out["header"]
    assert "01_ролик" not in out["header"] and ".mov" not in out["header"], \
        "имя файла вернулось в шапку рендера: %r" % out["header"]
    assert "рендер 25%" in out["rows"], "процент рендера не в строке ролика: %r" % out["rows"]
    assert "01_ролик" in out["rows"] and "готово" in out["rows"], out["rows"]
