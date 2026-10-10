# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Два клипа — два спикера: каждая фича берёт спикера СВОЕГО клипа.

ПОЧЕМУ этот тест есть. Система задумана разделять спикеров, а фичи раз за разом
читали спикера не того клипа. Последний случай: квота ИИ-вставок бралась из общего
селектора шага 1 — владелец выбрал наверху одного спикера, работал с клипом другого,
и вставок выходило 7 вместо 13. Тесты были с ОДНИМ спикером, поэтому ошибка не
проявлялась: не с чем было сравнить.

Стенд ставит ровно ту ловушку, на которой ошибка видна:

* клип 1 — спикер А, клип 2 — спикер Б;
* у КАЖДОГО профиля свои значения ВСЕХ полей (квота `inserts`, `style`,
  `jsxdir`/`outdir`/`renderdir`, `lut`, кадр камеры, музыка в его стиле, голос
  `voice_fx`, `format`);
* общий селектор шага 1 стоит на ТРЕТЬЕМ, чужом спикере В.

Тогда «взяли общий выбор» видно сразу: значение В всплывает там, где обязано быть
А или Б. Проверка в каждом тесте одна и та же по форме: клип 1 получает А, клип 2 —
Б, а значения В не появляется НИГДЕ.

Тесты гоняют БОЕВЫЕ функции из `static/app/*.js` под node с заглушками (приём
`tests/test_clip_speaker.py`): функция вырезается по балансу скобок, ей
подставляются состояние и внешние двери. Копий правил в тесте нет.

СПИСОК ФИЧ, ЗАВИСЯЩИХ ОТ СПИКЕРА (новая фича дописывается сюда же, вместе со своим
тестом ниже — иначе она снова прочитает не того спикера):

1. квота и цель ИИ-вставок + тела `/api/ai_inserts` — `spkInsTarget`,
   `aiInsertsRun` (окно вставок), `markupClip` (разметка);
   `test_inserts_quota_and_requests_use_the_speaker_of_the_clip`;
2. стиль клипа при смене спикера и в сборке — `setClipSpeaker`, `styleForJob`;
   `test_clip_style_follows_its_own_speaker_on_switch_and_in_build`;
3. папка `.jsx` и папка рендера со всей лестницей — `effOutdir`, `effRenderdir`,
   `jobForBuild`, `buildOutdir`;
   `test_jsx_and_render_folders_follow_the_speaker_of_the_clip`;
4. музыка и цензура: режим, папка стиля и переопределение клипа — `effMusic`,
   `clipStyleObj`, `clipCensor`, `musicPickDir`;
   `test_music_folder_follows_the_speaker_of_the_clip`;
5. LUT камеры и кадр камеры (формат ролика, рамка) — `lutPath`, `camFrameSpeaker`,
   `camFrameProfile`, `camFrameWH`, `camFrameOf`;
   `test_lut_and_camera_frame_follow_the_speaker_of_the_clip`;
6. голос: профиль спикера в панели и в запекании — `vtProfileFx`, `pvVoicePanel`
   (`VOICEFXSPK`); `test_voice_profile_of_the_panel_belongs_to_the_clip`;
7. стоки, картинки, видео и база вставок: `speaker`/`format` в телах запросов —
   `insClipFormat`, `insStockFor`, `insGenCore`, `insGenBatch`, `insGenVideo`,
   `insLibAuto`; `test_requests_about_inserts_carry_the_speaker_of_the_clip`;
8. подпись панели стиля и правка стиля сабов — `styleSpeakerNote`,
   `patchSubStyleSoon`; `test_style_panel_and_sub_style_take_the_clip_speaker`;
9. строка клипа (селектор и тег спикера) и набор из клипов двух спикеров —
   `spkSelHTML`, `spkTagHTML`, `syncBuildBtn`;
   `test_clip_row_and_mixed_set_take_the_tag_of_the_clip`;
10. сторож «одной двери»: прямых чтений спикера клипа мимо `clipSpeaker` нет —
   `test_no_direct_clip_speaker_reads_outside_the_door`;
11. конвейер «Разметить всё» — `markupAllRun`;
   `test_markup_all_pipeline_sends_the_speaker_of_the_clip`;
12. панель открытого клипа: стиль в `selectAE` и профиль открытого клипа
   (`openClipSpeaker`); `test_open_clip_panel_takes_the_speaker_of_the_clip`;
13. общий селектор шага 1 не переписывает стиль открытого клипа с тегом —
   `onSpeakerChange`; `test_step1_selector_does_not_overwrite_the_open_clip_style`;
14. папка рендера набора — по спикеру НАБОРА, `startRender`;
   `test_render_folder_follows_the_speaker_of_the_selected_set`;
15. переименование спикера перетегирует клипов ТОГО спикера — `saveSpeaker`;
   `test_renaming_a_speaker_retags_only_clips_of_that_speaker`.
16. вставки по названиям (слово из личного словаря с картинкой из базы): выключатель
   `named_inserts` берётся из профиля спикера КЛИПА — `cmd_inserts(speaker=…)`;
   `test_named_inserts_follow_the_switch_of_the_clip_speaker`.
17. ИИ-интро: длина строки интро — из СТИЛЯ клипа (тело
   `/api/ai_intro` несёт `styleForJob(job)`, как сборка) — `aiIntroRun`, `aiIntroOne`;
   `test_ai_intro_sends_the_style_of_the_clip`.

ДЫРУ В ПОКРЫТИИ ВИДНО ТОЛЬКО МУТАЦИЕЙ. Место, не попавшее ни в одну проверку
ниже, остаётся «зелёным», пока кто-нибудь не подменит в нём `clipSpeaker(...)`
на общий селектор. Поэтому порядок для новой фичи со спикером такой:
новая фича со спикером → строка в тесте → `py -3.10 tools/mutate_clip_speaker.py`
зелёный (скрипт по очереди подменяет КАЖДОЕ чтение спикера клипа и печатает,
какое из них тест не поймал).

Запуск: python -m pytest tests/test_two_speakers.py -q
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "static" / "app"
QUEUE_JS = APP / "40-queue.js"
SETTINGS_JS = APP / "10-settings.js"
PREVIEW_JS = APP / "60-preview.js"
EDITOR_JS = APP / "70-editor.js"
INSERTS_JS = APP / "80-inserts.js"
VIEW_JS = APP / "85-inserts-view.js"
LUT_JS = APP / "86-lut.js"
CAMFRAME_JS = APP / "87-camframe.js"
AE_JS = APP / "90-ae.js"
STYLES_JS = APP / "95-styles.js"

node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")

# Стенд: три профиля со своими значениями ВСЕХ полей, два клипа и чужой общий выбор.
# Пути нарочно разные и с меткой спикера: значение В, всплывшее у клипа А, видно
# сравнением строк, а не догадкой. `val('speaker')` отдаёт В — селектор шага 1.
HARNESS = r"""
const SPK_A='СпикерА',SPK_B='СпикерБ',SPK_V='СпикерВ';
const SPEAKERS={};
SPEAKERS[SPK_A]={label:'СпикерА',inserts:{photo:5,video:2},style:'stA',
  jsxdir:'D:\\spkA\\jsx',outdir:'D:\\spkA\\out',renderdir:'D:\\spkA\\render',
  lut:{'1':'D:\\spkA\\cam1.cube'},frame:{'1':{x:0.2,y:0.8,zoom:150}},
  voice_fx:{denoise:'a'},format:'1:1'};
SPEAKERS[SPK_B]={label:'СпикерБ',inserts:{photo:7,video:4},style:'stB',
  jsxdir:'D:\\spkB\\jsx',outdir:'D:\\spkB\\out',renderdir:'D:\\spkB\\render',
  lut:{'1':'D:\\spkB\\cam1.cube'},frame:{'1':{x:0.7,y:0.3,zoom:200}},
  voice_fx:{denoise:'b'},format:'16:9'};
SPEAKERS[SPK_V]={label:'СпикерВ',inserts:{photo:11,video:6},style:'stV',
  jsxdir:'D:\\spkV\\jsx',outdir:'D:\\spkV\\out',renderdir:'D:\\spkV\\render',
  lut:{'1':'D:\\spkV\\cam1.cube'},frame:{'1':{x:0.5,y:0.5,zoom:300}},
  voice_fx:{denoise:'v'},format:'4:5'};
// Музыка и цензура — свойства СТИЛЯ, а стиль клипа выбирается профилем ЕГО спикера
// (setClipSpeaker/selectAE): у каждого спикера свой режим, своя папка треков и своя
// цензура. Клип берёт их У СЕБЯ (clipStyleObj), а не из показанного в панели CURSTYLE.
const STYLES={stA:{label:'stA',music_mode:'random',music_dir:'D:\\spkA\\music',censor:false},
  stB:{label:'stB',music_mode:'file',music_dir:'D:\\spkB\\music',
       music_src:'D:\\spkB\\track.m4a',censor:true},
  stV:{label:'stV',music_mode:'random',music_dir:'D:\\spkV\\music'}};
const SPKFORMATS={'1:1':[1080,1080],'16:9':[1920,1080],'4:5':[1080,1350],'9:16':[1080,1920]};
const GENERAL=SPK_V;
function val(id){return id==='speaker'?GENERAL:'';}
function t(s,vars){if(vars)for(const k in vars){const v=vars[k];
  s=s.replace(new RegExp('\\{'+k+'\\}','g'),v==null?'':String(v));}return s;}
function esc(s){return String(s==null?'':s);}
function errText(e){return String((e&&(e.error||e.message))||e||'');}
function uiLog(m){}
function toast(m){}
function askConfirm(m){return Promise.resolve(false);}
function sleep(ms){return Promise.resolve();}
function nCams(){return 2;}
let AERENDER='D:\\global\\render',AEMUSICDIR='',CURSTYLE=null;
let curAE=-1,curIns=-1,VOICEFXSPK='';
const ELS={};
function $(id){if(!ELS[id])ELS[id]={id:id,className:'',textContent:'',innerHTML:'',
  value:'',style:{},dataset:{},checked:false};return ELS[id];}
function saveState(){}
function renderClips3(){}
function renderClips2(){}
function renderInsHost(){}
function renderRenderDirField(){}
function syncBuildBtn(){}
function syncClipLists(){}
function selectAE(i){curAE=i;}
const CLIPS=[
  {xml:'D:\\out\\СпикерА.xml',name:'СпикерА',status:{},inserts:[],
   job:{speaker:SPK_A,styleKey:'stA'}},
  {xml:'D:\\out\\СпикерБ.xml',name:'СпикерБ',status:{},inserts:[],
   job:{speaker:SPK_B,styleKey:'stB'}},
];
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


def _funcs(path: Path, names: tuple[str, ...]) -> str:
    src = _read(path)
    return "\n\n".join(_func(src, n) for n in names)


def _consts(path: Path, names: tuple[str, ...]) -> str:
    """Строки `const NAME = …` боевого файла — константы стенда, не копия значений."""
    src = _read(path)
    out = []
    for name in names:
        m = re.search(r"^const\s+%s\s*=.*$" % re.escape(name), src, re.M)
        assert m, "не нашлась константа %s" % name
        out.append(m.group(0))
    return "\n".join(out) + "\n"


def _door() -> str:
    """Боевая дверь `clipSpeaker` и разбор пути клипа, на который она опирается."""
    return _funcs(QUEUE_JS, ("clipSpeaker", "clipPathFix", "clipKey", "clipByXml")
                  ) + "\n" + _funcs(VIEW_JS, ("normInsPath",))


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


def _tail(script: str) -> str:
    """Хвост стенда: печать результата и внятный выход при ошибке."""
    return script + r"""
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""


# ============================ 1. КВОТА ИИ-ВСТАВОК ============================
AI_STUBS = r"""
const CALLS=[];
function insLog(d){}
function insAfterAI(c){return Promise.resolve('');}
function aiAborted(e){return false;}
async function aiFetch(url,body,tag,res){
  CALLS.push({url:url,xml:body.xml,
    speaker:(body.speaker===undefined||body.speaker===null)?null:String(body.speaker)});
  return {inserts:[{type:'photo',start_sec:1}],insTarget:5};}
async function aiPost(url,body,title){
  if(url!=='/api/ai_inserts')return url==='/api/ai_yellow'?{colored:[1]}:{};
  CALLS.push({url:url,xml:body.xml,
    speaker:(body.speaker===undefined||body.speaker===null)?null:String(body.speaker)});
  return {inserts:[{type:'photo',start_sec:1}],insTarget:1};}
let UICANCEL=false,LOCALQ=null,PROGQ=null;
function localQStart(names){}
function localQSet(name,stage,detail){}
function localQEnd(){}
function progQueue(title,k,n){}
function progStep(title,pct){}
function progDone(msg,err){}
function clearHl(c){}
function loadWordsFor(xml){}
function engLabel(s){return s;}
function subSkipped(d){return '';}
async function fetch(url,opts){return {json:async()=>({subs:12,colored:3,ncams:2})};}
"""


@node
def test_inserts_quota_and_requests_use_the_speaker_of_the_clip(tmp_path):
    """1. Квота, окно вставок и разметка: спикер КЛИПА, квота ЕГО профиля.

    `spkInsTarget` по профилю клипа: А 5 + 2 = 7, Б 7 + 4 = 11, а не 11 + 6 = 17
    чужого спикера В; у клипа без тега профиля нет — дефолт 13. Тела
    `/api/ai_inserts` (окно вставок, разметка и добор `aiInsertsMore`) несут
    спикера клипа.
    """
    script = (HARNESS + _door()
              + _funcs(INSERTS_JS, ("spkInsTarget", "aiInsertsRun", "aiInsertsMore"))
              + _funcs(EDITOR_JS, ("markupClip", "qClipSum")) + AI_STUBS
              + _tail(r"""(async()=>{
  const out={};
  out.targets=[spkInsTarget(CLIPS[0]),spkInsTarget(CLIPS[1]),spkInsTarget(null)];
  curIns=0;await aiInsertsRun();
  curIns=1;await aiInsertsRun();
  out.window=CALLS.splice(0);
  CLIPS[0].inserts=[];CLIPS[0].status={};
  CLIPS[1].inserts=[];CLIPS[1].status={};
  await markupClip(CLIPS[0]);
  await markupClip(CLIPS[1]);
  out.markup=CALLS.splice(0);
  CLIPS[0].inserts=[];CLIPS[1].inserts=[];      // цель 7 и 11 — есть что добирать
  curIns=0;await aiInsertsMore();
  curIns=1;await aiInsertsMore();
  out.more=CALLS.splice(0);
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_inserts.js", script)
    assert res["targets"] == [7, 11, 13], res["targets"]
    assert [c["speaker"] for c in res["window"]] == ["СпикерА", "СпикерБ"], res["window"]
    assert [c["speaker"] for c in res["markup"]] == ["СпикерА", "СпикерБ"], res["markup"]
    assert [c["speaker"] for c in res["more"]] == ["СпикерА", "СпикерБ"], res["more"]
    assert [c["xml"] for c in res["window"]] == [c["xml"] for c in res["markup"]], res
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ======================== 2. СТИЛЬ КЛИПА ========================
@node
def test_clip_style_follows_its_own_speaker_on_switch_and_in_build(tmp_path):
    """2. Стиль клипа — стиль ЕГО спикера: и при смене тега, и в сборке.

    `setClipSpeaker` ставит имя стиля профиля нового тега; смена тега на спикера В
    и обратно переключает стиль клипа целиком (запасного «как у общего выбора» нет).
    В сборку (`styleForJob`) уходит имя стиля клипа, а не стиль спикера из селектора
    шага 1.
    """
    script = (HARNESS + _door()
              + _funcs(STYLES_JS, ("samePath",))
              + _funcs(QUEUE_JS, ("setClipSpeaker", "selClips"))
              + _funcs(AE_JS, ("defJob", "styleForJob")) + _tail(r"""(async()=>{
  const out={};
  setClipSpeaker(0,SPK_A);setClipSpeaker(1,SPK_B);
  out.base=[CLIPS[0].job.styleKey,CLIPS[1].job.styleKey];
  out.build=[styleForJob(CLIPS[0].job),styleForJob(CLIPS[1].job)];
  setClipSpeaker(0,SPK_B);
  out.switched=[CLIPS[0].job.styleKey,CLIPS[0].job.speaker];
  setClipSpeaker(0,SPK_A);
  out.back=[CLIPS[0].job.styleKey,CLIPS[0].job.speaker];
  out.buildBack=[styleForJob(CLIPS[0].job),styleForJob(CLIPS[1].job)];
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_style.js", script)
    assert res["base"] == ["stA", "stB"], res
    assert res["build"] == ["stA", "stB"], res
    assert res["switched"] == ["stB", "СпикерБ"], res
    assert res["back"] == ["stA", "СпикерА"], res
    assert res["buildBack"] == ["stA", "stB"], res
    assert "stV" not in json.dumps(res, ensure_ascii=False), res


# ==================== 3. ПАПКИ .jsx И РЕНДЕРА ====================
@node
def test_jsx_and_render_folders_follow_the_speaker_of_the_clip(tmp_path):
    """3. Папки клипа — лестница ЕГО профиля: jsxdir → outdir → папка XML.

    У А и Б свои папки; провал на следующую ступень остаётся у ТОГО ЖЕ спикера
    (jsxdir Б пуст — берётся outdir Б, а не папка соседа). Набор из клипов двух спикеров
    в одну папку не собирается: `buildOutdir` отдаёт пусто (каждый — в свою, а клип без
    тега ложится рядом со своим XML).
    """
    script = (HARNESS + _door()
              + _funcs(QUEUE_JS, ("selClips",))
              + _funcs(STYLES_JS, ("xmlDirOf", "effOutdir", "effRenderdir"))
              + _funcs(AE_JS, ("defJob", "styleForJob", "clipNcams", "musicJobFields",
                               "jobForBuild", "buildOutdir"))
              + _funcs(STYLES_JS, ("effMusic", "clipStyleObj")) + _funcs(AE_JS, ("musicPickDir",))
              + _tail(r"""(async()=>{
  const out={};
  out.jsx=[effOutdir(CLIPS[0]),effOutdir(CLIPS[1])];
  out.render=[effRenderdir(CLIPS[0]),effRenderdir(CLIPS[1])];
  out.job=[jobForBuild(CLIPS[0]).outdir,jobForBuild(CLIPS[1]).outdir];
  SPEAKERS[SPK_A].jsxdir='';
  out.ladderOut=[effOutdir(CLIPS[0])];
  SPEAKERS[SPK_A].outdir='';
  out.ladderXml=[effOutdir(CLIPS[0])];
  SPEAKERS[SPK_A].jsxdir='D:\\spkA\\jsx';SPEAKERS[SPK_A].outdir='D:\\spkA\\out';
  const both=buildOutdir();
  CLIPS[0].sel=true;
  const one=buildOutdir();
  CLIPS[0].sel=false;
  out.mixed=both;out.single=one;
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_folders.js", script)
    assert res["jsx"] == ["D:\\spkA\\jsx", "D:\\spkB\\jsx"], res["jsx"]
    assert res["render"] == ["D:\\spkA\\render", "D:\\spkB\\render"], res["render"]
    assert res["job"] == ["D:\\spkA\\jsx", "D:\\spkB\\jsx"], res["job"]
    assert res["ladderOut"] == ["D:\\spkA\\out"], res["ladderOut"]
    assert res["ladderXml"] == ["D:\\out"], res["ladderXml"]
    # Два спикера — общей папки .jsx больше нет вовсе: каждый собирается в свою,
    # а клип без тега кладёт файл рядом со своим XML.
    assert res["mixed"] == "", res
    assert res["single"] == "D:\\spkA\\jsx", res        # один — папка ЕГО спикера
    assert "D:\\spkV" not in json.dumps(res, ensure_ascii=False), res


# ============================ 4. МУЗЫКА ============================
@node
def test_music_folder_follows_the_speaker_of_the_clip(tmp_path):
    """4. Музыка и цензура — у стиля ЭТОГО клипа, выше — своё клипа.

    Стиль клипа ставит его профиль (`setClipSpeaker` — та же дверь, что в `selectAE`:
    тег → стиль), а `effMusic`/`clipCensor` берут их У САМОГО КЛИПА (`clipStyleObj`),
    а не из показанного в панели CURSTYLE: у клипа А режим и папка из стиля А и цензура
    снята, у клипа Б — свой файл и цензура включена, третий спикер не участвует.
    `musicPickDir` над этим берёт папку переопределения клипа, иначе папку стиля.
    """
    script = (HARNESS + _door()
              + _funcs(STYLES_JS, ("samePath",))
              + _funcs(QUEUE_JS, ("setClipSpeaker", "selClips"))
              + _funcs(AE_JS, ("defJob", "musicPickDir"))
              + _funcs(STYLES_JS, ("clipStyleObj", "effMusic", "clipCensor"))
              + _tail(r"""(async()=>{
  // Стиль клипа выбирает его профиль — та же дверь, что в selectAE: тег → стиль.
  // CURSTYLE нарочно ставим чужим (стиль спикера В): клип обязан брать СВОЙ стиль.
  function styleOf(c){setClipSpeaker(CLIPS.indexOf(c),clipSpeaker(c));
    CURSTYLE=STYLES[SPK_V]||{};
    return {dir:musicPickDir(c),mode:effMusic(c).mode,censor:clipCensor(c)};}
  const out={};
  out.a=styleOf(CLIPS[0]);out.b=styleOf(CLIPS[1]);
  out.again=[styleOf(CLIPS[0]),styleOf(CLIPS[1])];
  CLIPS[0].job.music_override={mode:'random',dir:'D:\\spkA\\own'};
  out.override=[styleOf(CLIPS[0]).dir,styleOf(CLIPS[1]).dir];
  CLIPS[0].job.music_override=null;
  CLIPS[1].job.music_override={mode:'off'};
  out.modeOff=[styleOf(CLIPS[1]).dir];
  CLIPS[1].job.music_override=null;
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_music.js", script)
    assert res["a"] == {"dir": "D:\\spkA\\music", "mode": "random", "censor": False}, res["a"]
    assert res["b"] == {"dir": "D:\\spkB\\music", "mode": "file", "censor": True}, res["b"]
    assert res["again"] == [res["a"], res["b"]], res["again"]
    assert res["override"] == ["D:\\spkA\\own", "D:\\spkB\\music"], res["override"]
    assert res["modeOff"] == [""], res["modeOff"]
    assert "D:\\spkV" not in json.dumps(res, ensure_ascii=False), res


# ======================= 5. LUT И КАДР КАМЕРЫ =======================
@node
def test_lut_and_camera_frame_follow_the_speaker_of_the_clip(tmp_path):
    """5. LUT и кадр камеры — из профиля спикера ОТКРЫТОГО клипа.

    Открыт в превью клип А — LUT, формат кадра и рамка А; открыт клип Б — Б.
    Предпросмотр мог остаться от другого ролика, поэтому источник — XML в `IPV`,
    а не последний выбранный спикер.
    """
    script = (HARNESS + _door()
              + _consts(CAMFRAME_JS, ("CAMFRAME_MIN", "CAMFRAME_DEF"))
              + _funcs(LUT_JS, ("lutClip", "lutPath"))
              + _funcs(CAMFRAME_JS, ("camFrameClip", "camFrameSpeaker", "camFrameProfile",
                                     "camFrameNorm", "camFrameOf", "camFrameWH"))
              + _tail(r"""(async()=>{
  const out={};
  function forXml(x){IPV={xml:x};
    return {lut:lutPath(0),spk:camFrameSpeaker(),fmt:camFrameWH(),
      frame:camFrameOf(0)};}
  out.a=forXml(CLIPS[0].xml);
  out.b=forXml(CLIPS[1].xml);
  out.none=forXml('D:\\out\\чужой.xml');
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_lut_frame.js", script)
    assert res["a"]["lut"] == "D:\\spkA\\cam1.cube", res["a"]
    assert res["b"]["lut"] == "D:\\spkB\\cam1.cube", res["b"]
    assert res["a"]["spk"] == "СпикерА" and res["b"]["spk"] == "СпикерБ", res
    assert res["a"]["fmt"] == [1080, 1080] and res["b"]["fmt"] == [1920, 1080], res
    assert res["a"]["frame"] == {"x": 0.2, "y": 0.8, "zoom": 150}, res["a"]
    assert res["b"]["frame"] == {"x": 0.7, "y": 0.3, "zoom": 200}, res["b"]
    assert res["none"]["lut"] == "" and res["none"]["spk"] == "", res["none"]
    assert res["none"]["fmt"] == [1080, 1920], res["none"]
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============================ 6. ГОЛОС ============================
VOICE_STUBS = r"""
let ED=null;
let VOICERENDER=[];
function vtStop(P){}
function vtPrep(P){return Promise.resolve(true);}
function voiceFxRender(host,prof,opts){VOICERENDER.push({prof:prof,opts:opts});}
function vstFxList(host,own){}
function voiceFxSepFill(host){}
"""


@node
def test_voice_profile_of_the_panel_belongs_to_the_clip(tmp_path):
    """6. Голос: и панель, и запекание берут профиль спикера клипа.

    `vtProfileFx` (плеер без панели просит трек ровно под настройки профиля) и
    `pvVoicePanel` (панель «Голос» + ключ `VOICEFXSPK`, по нему пишется профиль)
    читают спикера клипа из `ED.xml`; у клипа без профиля — пусто, а не общий выбор.
    """
    script = (HARNESS + _door()
              + _funcs(PREVIEW_JS, ("vtProfileFx",))
              + _funcs(STYLES_JS, ("pvVoicePanel",)) + VOICE_STUBS
              + _tail(r"""(async()=>{
  const out={fx:{}};
  out.fx.a=vtProfileFx({xml:CLIPS[0].xml});
  out.fx.b=vtProfileFx({xml:CLIPS[1].xml});
  out.fx.none=vtProfileFx({xml:'D:\\out\\чужой.xml'});
  ED={xml:CLIPS[0].xml};VOICEFXSPK='';VOICERENDER=[];
  await pvVoicePanel();out.panel={spk:VOICEFXSPK,rend:VOICERENDER.slice()};
  ED={xml:CLIPS[1].xml};VOICEFXSPK='';VOICERENDER=[];
  await pvVoicePanel();out.panelB={spk:VOICEFXSPK,rend:VOICERENDER.slice()};
  ED={xml:'D:\\out\\чужой.xml'};VOICEFXSPK='';VOICERENDER=[];
  await pvVoicePanel();out.panelNone={spk:VOICEFXSPK,rend:VOICERENDER.slice()};
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_voice.js", script)
    assert res["fx"]["a"] == {"denoise": "a"}, res["fx"]
    assert res["fx"]["b"] == {"denoise": "b"}, res["fx"]
    assert res["fx"]["none"] == {}, res["fx"]
    assert res["panel"]["spk"] == "СпикерА", res["panel"]
    assert res["panelB"]["spk"] == "СпикерБ", res["panelB"]
    assert res["panelNone"]["spk"] == "", res["panelNone"]
    assert res["panel"]["rend"][0] == {"prof": {"denoise": "a"}, "opts": {"mode": "panel"}}, res
    assert res["panelB"]["rend"][0] == {"prof": {"denoise": "b"}, "opts": {"mode": "panel"}}, res
    assert res["panelNone"]["rend"][0]["opts"] == {"mode": "off"}, res["panelNone"]
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============ 7. СТОКИ, КАРТИНКИ, ВИДЕО, БАЗА ВСТАВОК ============
REQUEST_STUBS = r"""
const BODIES=[];
const window={};
let INSVIDSEQ=0;
const TOASTS=[];
const IMGKEYS=[],VIDKEYS=[];
function toast(m){TOASTS.push(String(m));}
function illCfg(){return {auto:true};}                  // автоподбор включён — карточка идёт в базу
function imgPrompts(k){IMGKEYS.push(k);return {a:{extra:''},b:{extra:''},
  pa:{extra:''},pb:{extra:''}};}
function videoPrompts(k){VIDKEYS.push(k);return {a:{extra:''},b:{extra:''}};}
function ico(n,c){return '';}
function insKind(p){p=String(p||'').toLowerCase();
  const m=p.match(/\.([a-z0-9]+)$/);return m?m[1]:'';}
function insSetMedia(x,p){x.media=p||'';}
function insApplyCrop(x,o){}
function insLog(d){}
function logReset(){}
function progOpen(o){}
function progUpdate(a,b){}
function hideProg(){}
function videoStart(body,ctx){BODIES.push({url:'/api/videogen_start',body:body});return Promise.resolve({ok:true});}
function insVideoContext(target,slot){return {type:'insert',token:target.token};}
function insVideoFindTarget(target){return null;}
function insAfterAI(c){return Promise.resolve('');}
function insRejectMedia(media,query){return Promise.resolve(false);}
const AICFG={active_image:'img',profiles:{img:{provider:'openai'}}};
async function fetch(url,opts){
  BODIES.push({url:url,body:JSON.parse((opts&&opts.body)||'{}')});
  if(url==='/api/ai_genimage')return {json:async()=>({path:'D:\\gen\\x.png'})};
  if(url==='/api/stock_search')return {json:async()=>({results:[{id:1}]})};
  if(url==='/api/insertlib_match'){const q=JSON.parse((opts&&opts.body)||'{}').queries||[];
    return {json:async()=>({results:q.map(()=>[{path:'D:\\lib\\x.png',auto:true}])})};}
  return {json:async()=>({})};}
"""


@node
def test_requests_about_inserts_carry_the_speaker_of_the_clip(tmp_path):
    """7. Стоки, картинки, видео, база и действия карточки: спикер — у СВОЕГО клипа.

    Формат ролика для поиска стока (`insClipFormat` — им задаётся ориентация кадра),
    `speaker` в телах `/api/ai_genimage` (генерация картинки, в том числе кнопкой на
    карточке — `insGenOne`), `/api/videogen_start`, `/api/insertlib_match` (подбор из
    базы: автоподбор `insLibAuto`, правка описания `insQuery`, кнопка 📚 `insLibFor`,
    смена типа `insToggleType`) — всё по клипу, чей запрос уходит. Кнопки генерации на
    карточке (`insGenBtns`) берут приписки профиля ЕГО спикера.
    """
    script = (HARNESS + _door()
              + _consts(SETTINGS_JS, ("GEN_FETCH_MS",))
              + _funcs(INSERTS_JS, ("insClipFormat", "insStockFor", "insVideoTarget",
                                    "insLibAuto", "insLibFill", "insWant",
                                    "insGenBtns", "insToggleType", "insQuery", "insLibFor"))
              + _funcs(SETTINGS_JS, ("insGenCore", "insGenBatch", "insGenOne"))
              + _funcs(INSERTS_JS, ("insGenVideo",))
              + REQUEST_STUBS
              + _tail(r"""(async()=>{
  const out={fmt:[insClipFormat(CLIPS[0]),insClipFormat(CLIPS[1])],bodies:{}};
  CLIPS[0].inserts=[{query:'кот',type:'photo'},{query:'море',type:'video'}];
  CLIPS[1].inserts=[{query:'пёс',type:'photo'},{query:'лес',type:'video'}];
  curIns=0;await insStockFor(0);await insGenBatch(CLIPS[0],false);await insGenVideo(1,'a');
  await insLibAuto(CLIPS[0]);
  out.bodies.a=BODIES.splice(0);
  CLIPS[0].inserts.forEach(x=>{x.media='';x.video_target=undefined;x.libOpts=null;});
  curIns=1;await insStockFor(0);await insGenBatch(CLIPS[1],false);await insGenVideo(1,'a');
  await insLibAuto(CLIPS[1]);
  out.bodies.b=BODIES.splice(0);
  // Действия КАРТОЧКИ вставки: каждому клипу — свой спикер в теле запроса.
  const card=[];
  const reset=()=>{CLIPS[0].inserts=[{query:'кот',type:'photo',media:''}];
    CLIPS[1].inserts=[{query:'пёс',type:'photo',media:''}];BODIES.splice(0);};
  const taken=()=>BODIES.splice(0).map(b=>({url:b.url,speaker:b.body.speaker}));
  reset();curIns=0;await insGenOne(0,'a');curIns=1;await insGenOne(0,'a');
  out.card_gen=taken();
  reset();curIns=0;await insToggleType(0);curIns=1;await insToggleType(0);
  out.card_toggle=taken();
  reset();curIns=0;await insQuery(0,'море');curIns=1;await insQuery(0,'лес');
  out.card_query=taken();
  reset();curIns=0;await insLibFor(0);curIns=1;await insLibFor(0);
  out.card_lib=taken();
  curIns=0;const htmlA=insGenBtns(0,CLIPS[0].inserts[0]);
  curIns=1;    const htmlB=insGenBtns(0,CLIPS[1].inserts[0]);
  out.card_btns=[htmlA.length>0&&htmlB.length>0,IMGKEYS.slice()];
  out.toasts=TOASTS.slice(0);
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_requests.js", script)
    assert res["fmt"] == ["1:1", "16:9"], res["fmt"]
    assert not res["toasts"], res["toasts"]

    def body(url: str, side: str) -> dict[str, Any]:
        found = [b["body"] for b in res["bodies"][side] if b["url"] == url]
        assert found, (url, side, res["bodies"][side])
        return found[0]

    assert body("/api/stock_search", "a")["format"] == "1:1", res["bodies"]["a"]
    assert body("/api/stock_search", "b")["format"] == "16:9", res["bodies"]["b"]
    assert body("/api/ai_genimage", "a")["speaker"] == "СпикерА", res["bodies"]["a"]
    assert body("/api/ai_genimage", "b")["speaker"] == "СпикерБ", res["bodies"]["b"]
    assert body("/api/videogen_start", "a")["speaker"] == "СпикерА", res["bodies"]["a"]
    assert body("/api/videogen_start", "b")["speaker"] == "СпикерБ", res["bodies"]["b"]
    assert body("/api/insertlib_match", "a")["speaker"] == "СпикерА", res["bodies"]["a"]
    assert body("/api/insertlib_match", "b")["speaker"] == "СпикерБ", res["bodies"]["b"]
    for name in ("card_gen", "card_toggle", "card_query", "card_lib"):
        assert [c["speaker"] for c in res[name]] == ["СпикерА", "СпикерБ"], (name, res[name])
    assert res["card_btns"][0] is True, res["card_btns"]
    assert res["card_btns"][1] == ["СпикерА", "СпикерБ"], res["card_btns"]
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============ 8. ПОДПИСЬ ПАНЕЛИ СТИЛЯ И СТИЛЬ САБОВ ============
@node
def test_style_panel_and_sub_style_take_the_clip_speaker(tmp_path):
    """8. Панель стиля: у открытого клипа — ЕГО спикер, без клипа — общий выбор.

    `styleSpeakerNote` описывает стиль открытого клипа (шаг 3) и только на шаге 1,
    где клипа нет, говорит о профиле общего селектора. Правка «строк/слов в ряд»
    (`patchSubStyleSoon`) пишется в стиль спикера клипа.
    """
    script = (HARNESS + _door()
              + _funcs(SETTINGS_JS, ("stylesMap",))
              + _funcs(STYLES_JS, ("styleSpeakerNote",))
              + _funcs(INSERTS_JS, ("patchSubStyleSoon",))
              + r"""
let _inspSubPatchTimer=null;
const PATCH=[];
async function fetch(url,opts){PATCH.push({url:url,body:JSON.parse((opts&&opts.body)||'{}')});
  return {json:async()=>({ok:true})};}
"""
              + _tail(r"""(async()=>{
  const out={};
  curAE=0;out.noteA=styleSpeakerNote();
  curAE=1;out.noteB=styleSpeakerNote();
  curAE=-1;out.noteStep1=styleSpeakerNote();
  curAE=0;patchSubStyleSoon(2,3);
  await new Promise(r=>setTimeout(r,600));
  curAE=1;patchSubStyleSoon(4,5);
  await new Promise(r=>setTimeout(r,600));
  out.patch=PATCH.splice(0);
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_stylepanel.js", script)
    assert "СпикерА" in res["noteA"] and "stA" in res["noteA"], res["noteA"]
    assert "СпикерВ" not in res["noteA"], res["noteA"]
    assert "СпикерБ" in res["noteB"] and "stB" in res["noteB"], res["noteB"]
    assert "СпикерВ" not in res["noteB"], res["noteB"]
    assert "СпикерВ" in res["noteStep1"], res["noteStep1"]
    assert [p["body"]["name"] for p in res["patch"]] == ["stA", "stB"], res["patch"]
    assert [p["body"]["patch"] for p in res["patch"]] == [
        {"sub_words_per_row": 2, "sub_rows_max": 3},
        {"sub_words_per_row": 4, "sub_rows_max": 5}], res["patch"]


# ============ 9. СТРОКА КЛИПА И НАБОР ИЗ ДВУХ СПИКЕРОВ ============
ROW_STUBS = r"""
const document={querySelector:()=>null,querySelectorAll:()=>[]};
function segUI(){}
function introAllCount(){}
"""


@node
def test_clip_row_and_mixed_set_take_the_tag_of_the_clip(tmp_path):
    """9. Строка клипа показывает ЕГО спикера, а набор двух спикеров не собирается.

    Селектор строки (`spkSelHTML`) и тег (`spkTagHTML`) читают тег клипа, а не общий
    выбор; `syncBuildBtn` по тегам клипов видит, что в наборе два спикера, и гасит
    «Собрать набор» (каждый в свою папку — собирать их одним файлом нельзя). С общим
    селектором оба клипа выглядели бы одним спикером, и кнопка осталась бы живой.
    """
    script = (HARNESS + _door()
              + _funcs(QUEUE_JS, ("spkSelHTML", "spkTagHTML", "selClips", "syncBuildBtn"))
              + ROW_STUBS
              + _tail(r"""(async()=>{
  const sum=h=>/<summary[^>]*>([^<]*)</.exec(h)[1];
  const picked=h=>{const m=/data-k="([^"]*)" aria-selected="true"/.exec(h);return m?m[1]:null;};
  const out={};
  out.sel=[picked(spkSelHTML(0,CLIPS[0])),picked(spkSelHTML(1,CLIPS[1]))];
  out.summary=[sum(spkSelHTML(0,CLIPS[0])),sum(spkSelHTML(1,CLIPS[1]))];
  out.tag=[spkTagHTML(CLIPS[0],true),spkTagHTML(CLIPS[1],true)];
  syncBuildBtn();
  out.mixedDisabled=$('buildbtn').disabled;out.mixedScope=$('buildscope').textContent;
  CLIPS[0].sel=true;
  syncBuildBtn();
  out.oneDisabled=$('buildbtn').disabled;out.oneScope=$('buildscope').textContent;
  CLIPS[0].sel=false;
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_row.js", script)
    assert res["sel"] == ["СпикерА", "СпикерБ"], res["sel"]
    assert res["summary"] == ["СпикерА", "СпикерБ"], res["summary"]
    assert "СпикерА" in res["tag"][0] and "СпикерБ" in res["tag"][1], res["tag"]
    assert "СпикерВ" not in res["tag"][0] and "СпикерВ" not in res["tag"][1], res["tag"]
    assert res["mixedDisabled"] is True, res
    assert res["oneDisabled"] is False, res
    assert "2" in res["mixedScope"] and "1" in res["oneScope"], res


# ==================== 10. СТОРОЖ «ОДНОЙ ДВЕРИ» ====================
# Чтение спикера клипа мимо двери: тег джоба клипа достают напрямую. Формы, на
# которых уже попадались: `c.job.speaker`, `(c.job||{}).speaker`, `j.speaker` (j — джоб).
_READ_RE = re.compile(r"(?:\.job\.speaker|job\s*\|\|\s*\{\}\s*\)\s*\.speaker|j\.speaker)\b")
_WRITE_RE = re.compile(r"\s*=[^=]")


def _strip_comments(src: str) -> str:
    """Пробелы вместо комментариев — позиции и номера строк сохраняются."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "'\"":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == ch or src[i] == "\n":
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _door_span(src: str) -> tuple[int, int]:
    """Границы боевой `clipSpeaker` в этом же тексте (её чтение — законное)."""
    door = _func(src, "clipSpeaker")
    at = src.find(door)
    assert at >= 0, "в тексте файла не нашлась вырезанная функция clipSpeaker"
    return at, at + len(door)


def test_no_direct_clip_speaker_reads_outside_the_door():
    """10. Спикера КЛИПА читает только `clipSpeaker(c)` — одна дверь на весь фронт.

    Прямое чтение `c.job.speaker` мимо двери возвращает ту же ошибку «взяли не того
    спикера», только тише: она не видна ни сторожу общего селектора, ни обзору. Запись
    тега (`j.speaker = …` в наборе клипа и в смене тега) двери не касается — она и есть
    единственный писатель.
    """
    bad: list[str] = []
    door_hits = 0
    for path in sorted(APP.glob("*.js")):
        src = _strip_comments(_read(path))
        lo, hi = ((0, 0) if path != QUEUE_JS else _door_span(src))
        for m in _READ_RE.finditer(src):
            if lo <= m.start() < hi:
                door_hits += 1
                continue                                  # сама дверь
            if _WRITE_RE.match(src[m.end():]):
                continue                                  # запись тега, не чтение
            line = src.count("\n", 0, m.start()) + 1
            bad.append("%s:%d: %s" % (path.name, line,
                                      src.splitlines()[line - 1].strip()[:160]))
    assert door_hits == 1, (
        "сторож не увидел ни одного чтения в самой двери clipSpeaker — разбор сломался")
    assert not bad, (
        "спикера клипа читают мимо двери clipSpeaker(c) — операция над клипом "
        "обязана брать спикера СВОЕГО клипа:\n" + "\n".join(bad))


# ============ 11. КОНВЕЙЕР «РАЗМЕТИТЬ ВСЁ» ============
MARKUP_STUBS = r"""
const AICFG={step_local:{yellow:false,inserts:false},
  step_profiles:{yellow:'y',inserts:'i'},step_concurrency:{yellow:1,inserts:1}};
const AICALLS=[];
let UICANCEL=false,LOCALQ=null,PROGQ=null;
function localQStart(names){}
function localQSet(name,stage,detail){}
function localQEnd(){}
function progQueue(title,k,n){}
function progStep(title,pct){}
function progDone(msg,err){}
function insLog(d){}
function insAfterAI(c){return Promise.resolve('');}
function engLabel(s){return s;}
function subSkipped(d){return '';}
async function fetch(url,opts){
  if(url==='/api/xml_state')return {json:async()=>({subs:12,colored:3,ncams:2})};
  return {json:async()=>({})};}
async function aiPost(url,body,title){
  if(url!=='/api/ai_inserts')return {};
  AICALLS.push({xml:body.xml,
    speaker:(body.speaker===undefined||body.speaker===null)?null:String(body.speaker)});
  return {inserts:[{type:'photo',start_sec:1}],insTarget:1};}
"""


@node
def test_markup_all_pipeline_sends_the_speaker_of_the_clip(tmp_path):
    """11. Конвейер «Разметить всё»: тело `/api/ai_inserts` — спикер КАЖДОГО клипа.

    Фаза «вставки» берёт спикера того клипа, по которому идёт в цикле (у А — А, у Б —
    Б), а не общий выбор шага 1: иначе клип Б размечался бы квотой и промптами чужого
    профиля, и заметить это можно было бы только по числу вставок.
    """
    script = (HARNESS + _door() + MARKUP_STUBS
              + _funcs(EDITOR_JS, ("markupPlan", "markupAllRun", "qClipSum",
                                   "aiStepConc", "runPool"))
              + _tail(r"""(async()=>{
  const out={};
  await markupAllRun('whisper',CLIPS,['inserts']);
  out.calls=AICALLS.map(c=>c.speaker);
  out.xml=AICALLS.map(c=>c.xml);
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_markupall.js", script)
    assert res["calls"] == ["СпикерА", "СпикерБ"], res["calls"]
    assert res["xml"] == ["D:\\out\\СпикерА.xml", "D:\\out\\СпикерБ.xml"], res["xml"]
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============ 12. ПАНЕЛЬ ОТКРЫТОГО КЛИПА ============
AE_PANEL_STUBS = r"""
let HL=new Set(),BRK=new Set(),CNT=new Set(),JNS=new Set(),HLXML='',AEXML='';
let INS=[],INTRO=[],INTRO_PICK=-1;
function captureAE(){}
function styleKeyFor(s){return '';}
function onStyleChange(){}
function stMigrateCamZoom(s){return s;}
function stMigrateIntroCam2(s){return s;}
function stMigrateCam2Zoom(s){return s;}
function stMigrateIntroPos2(s){return s;}
function ensureCustomOption(){}
function renderIns(){}
function musicSync(c){}
function musicClipUI(){}
function musicPickEnsure(c){}
function rotoSync(){}
function loadWordsFor(xml){}
"""


@node
def test_open_clip_panel_takes_the_speaker_of_the_clip(tmp_path):
    """12. Панель открытого клипа: стиль в `selectAE` и профиль в `openClipSpeaker`.

    Клип с тегом, но без своего стиля берёт стиль профиля СВОЕГО тега: у клипа А это
    `stA`, у Б — `stB`, а не стиль `stV` общего селектора. Профиль открытого клипа
    (`openClipSpeaker`) — тоже его собственный (и `null`, когда клипа нет).
    """
    script = (HARNESS + _door() + AE_PANEL_STUBS
              + _funcs(AE_JS, ("defJob", "selectAE"))
              + _funcs(STYLES_JS, ("openClipSpeaker",))
              + _tail(r"""(async()=>{
  const out={};
  CLIPS[0].job={speaker:SPK_A};CLIPS[1].job={speaker:SPK_B};
  selectAE(0);out.a={key:CLIPS[0].job.styleKey,val:$('style').value};
  selectAE(1);out.b={key:CLIPS[1].job.styleKey,val:$('style').value};
  curAE=0;out.open=(openClipSpeaker()||{}).label;
  curAE=1;out.openB=(openClipSpeaker()||{}).label;
  curAE=-1;out.none=openClipSpeaker();
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_aepanel.js", script)
    assert res["a"] == {"key": "stA", "val": "stA"}, res["a"]
    assert res["b"] == {"key": "stB", "val": "stB"}, res["b"]
    assert res["open"] == "СпикерА" and res["openB"] == "СпикерБ", res
    assert res["none"] is None, res
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============ 13. ОБЩИЙ СЕЛЕКТОР ШАГА 1 И ОТКРЫТЫЙ КЛИП ============
SPK_SELECT_STUBS = r"""
let STYLECHANGES=0;
async function spkDir(id,key,ask){}
async function camDirApply(k,msg){}
function onStyleChange(){STYLECHANGES++;}
function renderStyleInfo(){}
function spkEditUI(){}
function cutSummary(){}
"""


@node
def test_step1_selector_does_not_overwrite_the_open_clip_style(tmp_path):
    """13. Селектор шага 1 ставит стиль профиля только когда у открытого клипа нет тега.

    Открыт клип А (у него свой тег) — смена общего селектора на спикера В НЕ трогает
    выбранный стиль: клип живёт стилем своего тега. Открыт клип БЕЗ тега — профиля у
    него нет, и стиль берётся у общего селектора, как и когда клипа нет вовсе
    (`curAE<0`): это и есть та развилка, где общий выбор ещё законен.
    """
    script = (HARNESS + _door() + SPK_SELECT_STUBS
              + _funcs(STYLES_JS, ("onSpeakerChange",))
              + _tail(r"""(async()=>{
  const out={};
  CLIPS[0].job={speaker:SPK_A,styleKey:'stA'};
  curAE=0;$('style').value='stA';STYLECHANGES=0;await onSpeakerChange();
  out.openTag={value:$('style').value,changes:STYLECHANGES};
  CLIPS[0].job={speaker:'',styleKey:''};
  curAE=0;$('style').value='stA';STYLECHANGES=0;await onSpeakerChange();
  out.openNoTag={value:$('style').value,changes:STYLECHANGES};
  curAE=-1;$('style').value='stA';STYLECHANGES=0;await onSpeakerChange();
  out.noClip={value:$('style').value,changes:STYLECHANGES};
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_select.js", script)
    assert res["openTag"] == {"value": "stA", "changes": 0}, res["openTag"]
    assert res["openNoTag"] == {"value": "stV", "changes": 1}, res["openNoTag"]
    assert res["noClip"] == {"value": "stV", "changes": 1}, res["noClip"]


# ============ 14. ПАПКА РЕНДЕРА НАБОРА ============
RENDER_STUBS = r"""
const BODIES=[];
let RENDERSEEN=0,RENDERENGINE='ae';
function uiBusyGuard(){return false;}
async function collectJobs(){return [{xml:CLIPS[0].xml,name:CLIPS[0].name}];}
function rendEngine(){return 'ae';}
function rendEngineLabel(){return 'ae';}
function uiBusySet(v){}
function progOpen(o){}
function logReset(){}
function pollRender(){}
async function fetch(url,opts){
  BODIES.push({url:url,body:JSON.parse((opts&&opts.body)||'{}')});
  return {json:async()=>({})};}
"""


@node
def test_render_folder_follows_the_speaker_of_the_selected_set(tmp_path):
    """14. Папка рендера — по спикеру НАБОРА: один спикер = его renderdir, два — общая.

    Спикеров считают по тегам ОТМЕЧЕННЫХ клипов, а не по открытому в панели: набор из
    клипов А и Б в одну папку не рендерится (общая `AERENDER`), а набор из одного А —
    в папку А. Общий выбор шага 1 тут ни при чём.
    """
    script = (HARNESS + _door()
              + _funcs(QUEUE_JS, ("selClips",))
              + _funcs(STYLES_JS, ("effOutdir", "effRenderdir"))
              + _funcs(AE_JS, ("buildOutdir", "startRender"))
              + RENDER_STUBS
              + _tail(r"""(async()=>{
  const out={};
  CLIPS[0].sel=false;CLIPS[1].sel=false;
  await startRender();out.mixed=BODIES.splice(0)[0].body;
  CLIPS[0].sel=true;
  await startRender();out.one=BODIES.splice(0)[0].body;
  CLIPS[0].sel=false;
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_render.js", script)
    assert res["mixed"]["render_dir"] == "D:\\global\\render", res["mixed"]
    assert res["one"]["render_dir"] == "D:\\spkA\\render", res["one"]
    assert res["one"]["outdir"] == "D:\\spkA\\jsx", res["one"]
    assert "D:\\spkV" not in json.dumps(res, ensure_ascii=False), res


# ============ 15. ПЕРЕИМЕНОВАНИЕ СПИКЕРА ============
SAVE_SPK_STUBS = r"""
const BODIES=[];
const LABEL='СпикерА2';
const SPKDEF={},SPKDEF_FORMAT='9:16';
let SPKEDIT='',SPKSAVED='';
function $(id){if(!ELS[id])ELS[id]={id:id,className:'',textContent:'',innerHTML:'',
  value:'',style:{},dataset:{},checked:false,querySelectorAll:()=>[]};return ELS[id];}
function val(id){if(id==='spk_label')return LABEL;return id==='speaker'?GENERAL:'';}
async function loadSpeakers(){}
async function onSpeakerChange(){}
function closeModal(id){}
async function fetch(url,opts){
  BODIES.push({url:url,body:JSON.parse((opts&&opts.body)||'{}')});
  if(url==='/api/savespeaker')return {json:async()=>({ok:true,key:LABEL,
    path:'D:\\spk\\'+LABEL+'.json'})};
  return {json:async()=>({ok:true})};}
"""


@node
def test_renaming_a_speaker_retags_only_clips_of_that_speaker(tmp_path):
    """15. Переименование профиля перетегирует клипов ТОГО спикера — и только их.

    Тег клипа — это `c.job.speaker`: после переименования он обязан указывать на новый
    ключ, иначе следующая нарезка пойдёт несуществующим профилем. Сверка идёт по тегу
    КЛИПА, а не по общему селектору шага 1: клип другого спикера остаётся со своим тегом.
    """
    script = (HARNESS + _door() + SAVE_SPK_STUBS
              + _funcs(STYLES_JS, ("saveSpeaker",))
              + _tail(r"""(async()=>{
  const out={};
  SPKEDIT=SPK_A;
  CLIPS[0].job={speaker:SPK_A,styleKey:'stA'};
  CLIPS[1].job={speaker:SPK_B,styleKey:'stB'};
  await saveSpeaker();
  out.tags=[CLIPS[0].job.speaker,CLIPS[1].job.speaker];
  out.saved=SPKSAVED;
  out.del=BODIES.filter(b=>b.url==='/api/delspeaker').map(b=>b.body.name);
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_rename.js", script)
    assert res["tags"] == ["СпикерА2", "СпикерБ"], res["tags"]
    assert res["saved"] == "СпикерА2", res["saved"]
    assert res["del"] == ["СпикерА"], res["del"]
    assert "СпикерВ" not in json.dumps(res, ensure_ascii=False), res


# ============ 16. ВСТАВКИ ПО НАЗВАНИЯМ ПО ПРОФИЛЮ КЛИПА ============
def test_named_inserts_follow_the_switch_of_the_clip_speaker(tmp_path, monkeypatch):
    """16. Вставки по названиям: выключатель берётся из профиля СПИКЕРА КЛИПА.

    У клипа А профиль вставки по названиям не выключал — его клип получает фото-вставку с
    картинкой названного предмета. У клипа Б тот же вызов, но профиль выключатель снял
    (`named_inserts: false`) — у его клипа вставок по названиям нет ни одной. Разница только
    в спикере, который пришёл от клипа: возьми код общий селектор — оба клипа повели бы
    себя одинаково.

    Проверка питоновская (у вставок по названиям нет своей двери на фронте — весь выбор
    делает `cmd_inserts`), а стоит в этом файле потому, что это фича СО СПИКЕРОМ: список
    выше — реестр таких фич, и своя строка в нём обязательна.
    """
    sys.path.insert(0, str(ROOT))
    from core import insertlib
    from core.aicut import commands

    named = tmp_path / "named_inserts.json"
    named.write_text('{"кофемашина": ["кофемашины"]}', encoding="utf-8")
    monkeypatch.setattr(commands, "NAMED_INSERTS_PATH", str(named))
    monkeypatch.setitem(commands._NAMED_CACHE, "mtime", None)
    monkeypatch.setitem(commands._NAMED_CACHE, "cfg", None)
    words = [(0, "поставил", 1.0, 1.5), (1, "кофемашины", 34.2, 34.7), (2, "всё", 60.0, 60.5)]
    monkeypatch.setattr(commands, "_words_from_xml", lambda p: list(words))
    monkeypatch.setattr(commands, "_ask_json", lambda *a, **k: {"inserts": []})
    monkeypatch.setattr(insertlib, "find_named",
                        lambda terms, kind="photo", secondary=(), prefer=(), avoid=(): {"path": "D:\\lib\\coffee-machine-box.png"})
    base = {"inserts": {"photo": 10, "video": 3}}
    prof_a = dict(base, label="СпикерА")
    prof_b = dict(base, label="СпикерБ", named_inserts=False)

    res_a = commands.cmd_inserts(str(tmp_path / "СпикерА.xml"), speaker=prof_a,
                                 emit=lambda *a, **k: None)
    res_b = commands.cmd_inserts(str(tmp_path / "СпикерБ.xml"), speaker=prof_b,
                                 emit=lambda *a, **k: None)

    assert [x for x in res_a["inserts"] if x.get("auto") == "named"], res_a["inserts"]
    assert not [x for x in res_b["inserts"] if x.get("auto") == "named"], res_b["inserts"]


# ======================= 17. ИИ-ИНТРО: СТИЛЬ КЛИПА =======================
INTRO_STYLE_STUBS = r"""
const CALLS=[];
let AEXML='',UICANCEL=false,INTRO=[],INTRO_PICK=-1;
function uiBusyGuard(){return false;}
function insLog(d){}
function midCount(d){return 0;}
function renderIntro(){}
function captureAE(){}
function aewRender(){}
function aiAborted(e){return false;}
function localQSet(name,stage,detail){}
function take(){return CALLS.splice(0);}
function record(url,body){CALLS.push({url:url,style:(body.style==null)?null:String(body.style)});}
async function aiFetch(url,body,tag,res){record(url,body);return {intro_rows:[],mid_groups:[]};}
async function aiPost(url,body,title){record(url,body);return {intro_rows:[],mid_groups:[]};}
"""


@node
def test_ai_intro_sends_the_style_of_the_clip(tmp_path):
    """17. ИИ-интро: длина строки интро берётся из СТИЛЯ клипа.

    Тело `/api/ai_intro` несёт то же значение, что уходит в сборку клипа (`styleForJob`):
    у клипа А стиль stA, у клипа Б stB. Обе двери — одиночная (`aiIntroRun`) и пакетная
    (`aiIntroOne`) — шлют стиль СВОЕГО клипа; стиль без привязки к клипу дал бы обоим
    одну и ту же ручку длины строки.
    """
    script = (HARNESS
              + _funcs(AE_JS, ("aiIntroRun", "aiIntroOne", "introRowsFromAI",
                               "styleForJob", "defJob"))
              + INTRO_STYLE_STUBS
              + _tail(r"""(async()=>{
  const out={};
  AEXML=CLIPS[0].xml;curAE=0;await aiIntroRun();
  AEXML=CLIPS[1].xml;curAE=1;await aiIntroRun();
  out.single=take();
  CLIPS[0].status={subs:12};CLIPS[1].status={subs:12};
  CLIPS[0].inserts=[];CLIPS[1].inserts=[];
  await aiIntroOne(CLIPS[0]);
  await aiIntroOne(CLIPS[1]);
  out.batch=take();
  console.log(JSON.stringify(out));
"""))
    res = _run_node(tmp_path, "twospk_intro_style.js", script)
    assert [c["url"] for c in res["single"]] == ["/api/ai_intro"] * 2, res["single"]
    assert [c["style"] for c in res["single"]] == ["stA", "stB"], res["single"]
    assert [c["style"] for c in res["batch"]] == ["stA", "stB"], res["batch"]
