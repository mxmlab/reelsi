# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Операции над КЛИПОМ берут спикера КЛИПА, а не общий выбор шага 1.

Общий селектор «Спикер» на шаге 1 описывает НОВУЮ нарезку. Клип же помнит, кем его
резали, в своём теге (`c.job.speaker`). Пока запросы операций над клипом слали общий
селектор, клип спикера Б получал квоту спикера А: владелец выбрал наверху спикера А
(5 фото + 2 видео = 7), работал с клипом спикера Б (своей квоты нет → дефолт 10 + 3 = 13),
а ИИ-вставок выходило 7.

Тесты гоняют БОЕВЫЕ функции из `static/app/*.js` под node с заглушками (приём других
тестов интерфейса): вырезаем функцию по балансу скобок и подставляем ей состояние и
внешние двери. Проверяется:

1. Конвейер «Разметить все» (`markupAllRun`, `70-editor.js`) шлёт в `/api/ai_inserts`
   спикера КЛИПА; у клипа без тега поле пустое (запасного пути на общий выбор нет);
2. одиночная разметка клипа (`markupClip`, `70-editor.js`) — то же самое;
3. окно вставок (`aiInsertsRun`, `80-inserts.js`) — то же самое, а `spkInsTarget(clip)`
   считает цель по профилю КЛИПА: спикер Б без квоты → 13, спикер А 5 + 2 → 7;
4. сторож: `val('speaker')` в `static/app/*.js` встречается только в функциях белого
   списка (новая нарезка и профиль спикера) — имена функций, а не номера строк;
5. сторож: каждый вызов `/api/ai_inserts` в `static/app/*.js` несёт `clipSpeaker`.

Запуск: python -m pytest tests/test_clip_speaker.py -q
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "static" / "app"
QUEUE_JS = APP / "40-queue.js"
EDITOR_JS = APP / "70-editor.js"
INSERTS_JS = APP / "80-inserts.js"

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")

# Общий селектор шага 1 во всех стендах — спикер А с квотой 5 + 2; клип без тега своего
# спикера не имеет вовсе. Ошибка «взяли общий выбор» видна как speaker:"СпикерА" там,
# где ожидается "СпикерБ" (или пусто).
HARNESS_HEAD = r"""
const GENERAL='СпикерА';
const SPEAKERS={'СпикерА':{inserts:{photo:5,video:2}},'СпикерБ':{}};
const CALLS=[];
function val(id){return id==='speaker'?GENERAL:'';}
function t(s){return s;}
function esc(s){return String(s==null?'':s);}
function errText(e){return String((e&&(e.error||e.message))||e||'');}
function uiLog(m){}
function toast(m){}
function askConfirm(m){return Promise.resolve(false);}
function sleep(ms){return Promise.resolve();}
"""

EDITOR_HARNESS = HARNESS_HEAD + r"""
let UICANCEL=false,curAE=-1,LOCALQ=null,PROGQ=null;
const AICFG={step_local:{yellow:false,inserts:false},
  step_profiles:{yellow:'y',inserts:'i'},step_concurrency:{yellow:1,inserts:1}};
function localQStart(names){}
function localQSet(name,stage,detail){}
function localQEnd(){}
function progQueue(title,k,n){}
function progStep(title,pct){}
function progDone(msg,err){}
function renderClips2(){}
function saveState(){}
function clearHl(c){}
function loadWordsFor(xml){}
function insLog(d){}
function insAfterAI(c){return Promise.resolve(null);}
function engLabel(s){return s;}
function subSkipped(d){return '';}
async function fetch(url,opts){
  if(url==='/api/xml_state')return {json:async()=>({subs:12,colored:3,ncams:2})};
  return {json:async()=>({})};
}
async function aiPost(url,body,title){
  if(url==='/api/ai_inserts'){
    CALLS.push({xml:body.xml,
      speaker:(body.speaker===undefined||body.speaker===null)?null:String(body.speaker)});
    return {inserts:[{type:'photo',start_sec:1}],insTarget:1};
  }
  if(url==='/api/ai_yellow')return {colored:[1]};
  return {};
}
"""

INSERTS_HARNESS = HARNESS_HEAD + r"""
let curIns=0;
const ELS={};
function $(id){if(!ELS[id])ELS[id]={id,className:'',textContent:'',innerHTML:''};return ELS[id];}
function insLog(d){}
function insAfterAI(c){return Promise.resolve('');}
function renderInsHost(){}
function syncClipLists(){}
function saveState(){}
function aiAborted(e){return false;}
async function aiFetch(url,body,tag,res){
  CALLS.push({url:url,
    speaker:(body.speaker===undefined||body.speaker===null)?null:String(body.speaker)});
  return {inserts:[{type:'photo',start_sec:1}]};
}
"""


def _read(path: Path) -> str:
    return io.open(str(path), encoding="utf-8").read()


def _func(src: str, name: str) -> str:
    """Вырезать `[async] function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "не нашлась функция %s" % name
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError("не сошлись скобки у %s" % name)


def _clip_speaker() -> str:
    """Боевой `clipSpeaker` — его зовут все операции над клипом."""
    return _func(_read(QUEUE_JS), "clipSpeaker")


def _run_node(tmp_path: Path, name: str, script: str) -> dict[str, Any]:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    path = tmp_path / name
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    res: dict[str, Any] = json.loads(lines[-1])
    return res


CLIPS_BODY = r"""
const CLIPS=[
  {xml:'СпикерБ.xml',name:'СпикерБ',status:{},inserts:[],job:{speaker:'СпикерБ'}},
  {xml:'безымянный.xml',name:'без спикера',status:{},inserts:[],job:{speaker:''}},
];
"""


@node
def test_pipeline_sends_the_speaker_of_the_clip(tmp_path):
    """1. Конвейер «Разметить все»: /api/ai_inserts несёт спикера КЛИПА.

    Клип спикера Б — "СпикерБ" (а не общий спикер А), клип без тега — пусто: запасной
    путь на общий выбор запрещён, у него дефолты (квота 13, без профиля).
    """
    editor = _read(EDITOR_JS)
    script = (EDITOR_HARNESS + "\n" + _clip_speaker() + "\n"
              + "\n\n".join(_func(editor, n) for n in
                            ("markupPlan", "markupAllRun", "qClipSum", "aiStepConc", "runPool"))
              + "\n" + CLIPS_BODY + r"""
(async()=>{
  await markupAllRun('whisper',CLIPS,['subs','yellow','inserts']);
  console.log(JSON.stringify({calls:CALLS}));
})().catch(e=>{console.error(e);process.exit(1);});
""")
    res = _run_node(tmp_path, "clipspk_pipeline.js", script)
    by_xml = {c["xml"]: c["speaker"] for c in res["calls"]}
    assert set(by_xml) == {"СпикерБ.xml", "безымянный.xml"}, res["calls"]
    assert by_xml["СпикерБ.xml"] == "СпикерБ", res["calls"]
    assert by_xml["безымянный.xml"] is None, res["calls"]


@node
def test_single_clip_markup_sends_the_speaker_of_the_clip(tmp_path):
    """2. Одиночная разметка клипа (`markupClip`): то же — спикер клипа, не общий."""
    editor = _read(EDITOR_JS)
    script = (EDITOR_HARNESS + "\n" + _clip_speaker() + "\n"
              + "\n\n".join(_func(editor, n) for n in ("markupPlan", "markupClip", "qClipSum"))
              + "\n" + CLIPS_BODY + r"""
(async()=>{
  await markupClip(CLIPS[0]);
  await markupClip(CLIPS[1]);
  console.log(JSON.stringify({calls:CALLS}));
})().catch(e=>{console.error(e);process.exit(1);});
""")
    res = _run_node(tmp_path, "clipspk_markupclip.js", script)
    by_xml = {c["xml"]: c["speaker"] for c in res["calls"]}
    assert by_xml == {"СпикерБ.xml": "СпикерБ", "безымянный.xml": None}, res["calls"]


@node
def test_inserts_window_and_target_use_the_speaker_of_the_clip(tmp_path):
    """3. Окно вставок: запрос несёт спикера клипа, а цель — квоту ЕГО профиля.

    `spkInsTarget(clip)` — цель по числу вставок: спикер Б без квоты → 13 (дефолт
    10 + 3), спикер А 5 + 2 → 7, клип без тега → 13.
    """
    inserts = _read(INSERTS_JS)
    script = (INSERTS_HARNESS + "\n" + _clip_speaker() + "\n"
              + _func(inserts, "spkInsTarget") + "\n"
              + _func(inserts, "aiInsertsRun") + "\n" + CLIPS_BODY + r"""
(async()=>{
  curIns=0;await aiInsertsRun();
  curIns=1;await aiInsertsRun();
  console.log(JSON.stringify({calls:CALLS,
    tgt_b:spkInsTarget(CLIPS[0]),
    tgt_none:spkInsTarget(CLIPS[1]),
    tgt_a:spkInsTarget({job:{speaker:'СпикерА'}}),
    tgt_null:spkInsTarget(null)}));
})().catch(e=>{console.error(e);process.exit(1);});
""")
    res = _run_node(tmp_path, "clipspk_inswindow.js", script)
    assert [c["speaker"] for c in res["calls"]] == ["СпикерБ", None], res["calls"]
    assert res["tgt_b"] == 13, res
    assert res["tgt_none"] == 13, res
    assert res["tgt_a"] == 7, res
    assert res["tgt_null"] == 13, res


# ---- сторож: общий выбор остаётся только у нарезки и профиля (п. 3) ----
VAL_SPEAKER = re.compile(r"val\(\s*'speaker'\s*\)")
_FUNC_DECL = re.compile(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
_ARROW = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
    r"(?:\([^()]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{")
_FUNC_EXPR = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"function\s*\([^)]*\)\s*\{")

# Белый список: имя функции, где общий селектор шага 1 ЗАКОНЕН, — «общий выбор: новая
# нарезка/профиль, не клип». Всё остальное (операции над клипом) обязано звать clipSpeaker.
ALLOWED: dict[str, set[str]] = {
    "10-settings.js": {"cutSummary"},
    "40-queue.js": {"newClip", "runAI", "runCustom"},
    "95-styles.js": {
        "applySpeakerDirs", "spkEditUI", "spkDir", "jsxDirNote", "renderDirNote",
        "camDirApply", "camDirCommit", "saveSpeakerCamdirs", "onSpeakerChange",
        "styleSpeakerNote",
    },
    "99-boot.js": {"stateObj"},
}

# Минимум, который сторож обязан увидеть: иначе он «зелёный» просто потому, что
# разбор сломался и ни одной функции не нашёл.
REQUIRED_FOUND = {
    ("10-settings.js", "cutSummary"),
    ("40-queue.js", "newClip"),
    ("40-queue.js", "runAI"),
    ("40-queue.js", "runCustom"),
    ("99-boot.js", "stateObj"),
}


def _body_span(src: str, at: int) -> tuple[int, int] | None:
    """Границы блока `{...}` после позиции at (None — блока нет/не сошёлся)."""
    i = src.find("{", at)
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return (i, j)
    return None


def _functions(src: str) -> list[tuple[str, int, int]]:
    """[(имя, начало тела, конец тела)] — объявления и присвоенные стрелки/выражения."""
    out: list[tuple[str, int, int]] = []
    for rx in (_FUNC_DECL, _ARROW, _FUNC_EXPR):
        for m in rx.finditer(src):
            span = _body_span(src, m.end() - 1)
            if span:
                out.append((m.group(1), span[0], span[1]))
    return out


def _enclosing(src: str, pos: int, funcs: list[tuple[str, int, int]]) -> str:
    """Имя самой внутренней функции, накрывающей позицию (иначе `<top-level>`)."""
    inside = [f for f in funcs if f[1] <= pos <= f[2]]
    if not inside:
        return "<top-level>"
    inside.sort(key=lambda f: (f[2] - f[1], -f[1]))
    return inside[0][0]


def _val_speaker_sites() -> list[tuple[str, str]]:
    """[(файл, функция)] для каждого val('speaker') в static/app/*.js."""
    out: list[tuple[str, str]] = []
    for path in sorted(APP.glob("*.js")):
        src = _read(path)
        funcs = _functions(src)
        for m in VAL_SPEAKER.finditer(src):
            out.append((path.name, _enclosing(src, m.start(), funcs)))
    return out


def test_general_speaker_selector_is_only_for_cutting_and_profile():
    """4. Общий выбор шага 1 — только нарезка и профиль спикера; операции над клипом
    обязаны звать clipSpeaker (иначе квота одного спикера уезжает в клип другого)."""
    sites = _val_speaker_sites()
    bad = [(f, fn) for f, fn in sites if fn not in ALLOWED.get(f, set())]
    assert not bad, (
        "val('speaker') (общий выбор шага 1) в операциях над клипом: %s — "
        "операции над клипом берут спикера через clipSpeaker(c)" % bad)
    found = set(sites)
    assert REQUIRED_FOUND <= found, (
        "сторож не нашёл разбором обязательные места: не хватает %s"
        % sorted(REQUIRED_FOUND - found))
    assert ("95-styles.js", "onSpeakerChange") in found, (
        "сторож не увидел панель профиля спикера — разбор функций сломался")


def test_every_ai_inserts_call_passes_the_clip_speaker():
    """5. Каждый вызов /api/ai_inserts несёт спикера КЛИПА (clipSpeaker)."""
    missing: list[str] = []
    total = 0
    for path in sorted(APP.glob("*.js")):
        src = _read(path)
        for m in re.finditer(r"/api/ai_inserts", src):
            total += 1
            depth = 0
            end = None
            for j in range(m.end(), len(src)):
                if src[j] == "(":
                    depth += 1
                elif src[j] == ")":
                    depth -= 1
                    if depth <= 0:
                        end = j
                        break
            chunk = src[m.start():end if end is not None else m.end() + 400]
            if "clipSpeaker" not in chunk:
                missing.append("%s: %s" % (path.name, chunk.splitlines()[0].strip()[:160]))
    assert total >= 3, "вызовов /api/ai_inserts найдено меньше трёх — разбор сломался"
    assert not missing, "вызов /api/ai_inserts без спикера клипа: %s" % missing


def test_clip_speaker_reads_the_clip_tag_only():
    """`clipSpeaker` — тег клипа и ничего больше (общий селектор не запасной путь)."""
    src = _func(_read(QUEUE_JS), "clipSpeaker")
    assert "val(" not in src, "clipSpeaker полез в общий селектор: %s" % src
    assert ".job.speaker" in src and "||''" in src, src


def test_spk_ins_target_takes_a_clip():
    """`spkInsTarget` принимает КЛИП: вызовов без аргумента быть не должно."""
    src = _read(INSERTS_JS)
    assert re.search(r"function\s+spkInsTarget\s*\(\s*c\s*\)", src), (
        "spkInsTarget больше не принимает клип")
    assert not re.search(r"spkInsTarget\s*\(\s*\)", src), (
        "spkInsTarget зовут без клипа — цель посчитается по общему выбору")
    assert "clipSpeaker(c)" in _func(src, "spkInsTarget"), (
        "spkInsTarget читает спикера не у клипа")
