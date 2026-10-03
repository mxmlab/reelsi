# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно прогресса разметки и ИИ-интро — свой список роликов со статусом под каждым.

Пока «Разметить всё» шло, в окне прогресса висел список ПРОШЛОЙ серверной нарезки
(«gravacao_… готово 01_gravacao_….xml»): разметка и пакетное ИИ-интро идут из браузера
обычными POST-ами, серверного задания у них нет, и очередь рисовалась только из d.items.
Хвост лога при этом собирался из общего лога, куда параллельные ролики пишут вперемешку.

Сторожим три вещи:

1. `static/app/55-progress.js` — локальная очередь (LOCALQ/localQStart/localQSet/localQEnd):
   пока она живёт, `queueRender` рисует ТОЛЬКО её строки и серверные d.items игнорирует,
   перерисовывается сразу на каждом `localQSet`, а `localQEnd` метит список `done` и
   оставляет итог на экране до следующего задания. Сбрасывают его только начало нового
   задания (`progOpen`, он же за дверью `progShow`) и работающее серверное задание в
   опросе статуса (`d.running`). Под именем — вторая строка `detail`;
2. `static/app/70-editor.js` — `logTail(setStatus, tag)` берёт из общего лога только строки
   своего клипа (префикс «[имя XML] ») и показывает их без префикса; `aiPost` при batch
   передаёт тег и превращает хвост в КОД события (в строку ролика сырая строка не идёт),
   а прогоны (markupAllRun/markupClip) ведут локальный список и закрывают его в finally;
   то же у ИИ-интро (90-ae.js);
3. `api/ai.py` — в пачке (`batch`) строки трёх ИИ-роутов уходят в общий лог с префиксом
   «[имя XML] », без batch — как раньше, без префикса.

Запуск: py -3.10 -m pytest tests/test_prog_rows.py -q
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Callable

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402
import api.ai as ai_route  # noqa: E402
from core import aicut  # noqa: E402

APP = os.path.join(ROOT, "static", "app")
PROG_JS = os.path.join(APP, "55-progress.js")
EDITOR_JS = os.path.join(APP, "70-editor.js")
AE_JS = os.path.join(APP, "90-ae.js")
CORE_JS = os.path.join(APP, "00-core.js")
CSS = os.path.join(ROOT, "static", "app.css")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

H = {"Host": "127.0.0.1:5001"}

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


def _func(path: str, name: str) -> str:
    """Тело функции по имени (от объявления до парной скобки)."""
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


# Стенд: подделки DOM и мелочей + НАСТОЯЩИЙ модуль формы целиком. Модуль грузим как
# есть, а не по кускам: список строк у серверного задания и у клиентского прогона —
# теперь ОДИН (PROGITEMS), и связку этих дверей надо проверять вместе.
_STAND_HEAD = """
const fs=require('fs');
let LANG='ru',I18N={};
__T__
__FMTLOG__
__ESC__
let LOGCACHE=[],LOGSINCE=0,CLIPS=[],curAE=-1,curIns=-1,AICFG=null;
let VIDPOLL=false,UIBUSY=false;
function uiLog(){}function toast(){}function goStep(){}function vidCancel(){}function saveState(){}
function refreshLog(){}function renderClips2(){}function renderInsHost(){}function insLog(){}
function clearHl(){}function loadWordsFor(){}function uiBusySet(){}function insTarget(){}
function sleep(){return Promise.resolve();}function defJob(){return {};}
function introRowsFromAI(){return [];}function midCount(){return 0;}function plur(n,a,b,c){return c;}
function askConfirm(){return Promise.resolve(true);}function errText(e){return String(e);}
function subSkipped(){return '';}function engLabel(){return 'whisper';}function aiStepConc(){return 1;}
function insAfterAI(){return Promise.resolve(null);}function loadAIProfiles(){return Promise.resolve();}
function captureAE(){}function aewRender(){}function renderIntro(){}function syncClipLists(){}
const EL={};
function mkEl(){return {innerHTML:'',textContent:'',className:'',value:'',disabled:false,
  style:{},scrollHeight:0,clientHeight:0,scrollTop:0,scrollLeft:7,scrolled:null,
  classList:{add(){},remove(){},contains(){return false;}},scrollIntoView(o){this.scrolled=o;},focus(){}};}
function $(id){return EL[id]||(EL[id]=mkEl());}
__PROG__
"""


def _stand(body: str) -> str:
    src = _read(CORE_JS)
    out = _STAND_HEAD
    out = out.replace("__T__", _extract(src, "function t(s, vars)"))
    out = out.replace("__FMTLOG__", _extract(src, "function fmtLog(l)"))
    out = out.replace("__ESC__", _extract(src, "function esc(s)"))
    out = out.replace("__PROG__", _read(PROG_JS))
    return out + "\n" + body


# ---- 1. Локальная очередь: рисуем свои строки, серверные игнорируем --------

def test_queue_render_draws_local_list_and_detail(tmp_path: Any) -> None:
    """Пока LOCALQ жив, queueRender рисует его строки (с detail) и НЕ рисует d.items;
    после localQEnd итог остаётся на экране, а новое серверное задание (d.running) снова своё.

    Мутация «при LOCALQ рисовать серверные d.items» валит проверку `withLocal`.
    """
    script = _stand("""
const out={};
localQStart(['Первый ролик','Второй ролик']);
out.started=$('qlist').innerHTML;
localQSet('Первый ролик','yellow','14 с · жёлтых: 3');
out.detail=$('qlist').innerHTML;
localQSet('Второй ролик','done','жёлтых: 12 · вставок: 13');
queueRender({items:[{name:'СЕРВЕРНЫЙ',stage:'cut'},{name:'СЕРВЕРНЫЙ2',stage:'done',path:'out\\\\clip.xml'}]});
out.withLocal=$('qlist').innerHTML;
localQEnd();
out.afterEnd=$('qlist').innerHTML;
queueRender({running:true,items:[{name:'СЕРВЕРНЫЙ',stage:'cut'}]});
out.withServer=$('qlist').innerHTML;
fs.writeSync(1, JSON.stringify(out)+'\\n');
process.exit(0);
""")
    res = _run_node(tmp_path, "prog_rows_queue.js", script)

    # 1. Список заведён сразу: все строки на месте и «в очереди»
    assert "Первый ролик" in res["started"] and "Второй ролик" in res["started"], res["started"]
    assert "в очереди" in res["started"], res["started"]

    # 2. detail — второй строкой под именем (класс qdetail), активная строка помечена qcur
    assert '<span class="qdetail">14 с · жёлтых: 3</span>' in res["detail"], res["detail"]
    assert "qrow qcur" in res["detail"], res["detail"]

    # 3. Серверные d.items в это время НЕ рисуются (это и был дефект: остатки нарезки)
    assert "Первый ролик" in res["withLocal"], res["withLocal"]
    assert "СЕРВЕРНЫЙ" not in res["withLocal"], res["withLocal"]

    # 4. localQEnd список не стирает — итог виден до следующего задания
    assert res["afterEnd"] == res["withLocal"], "итог локального прогона пропал с экрана"

    # 5. Новое серверное задание (в опросе d.running) рисует свои строки, локальных нет
    assert "СЕРВЕРНЫЙ" in res["withServer"], res["withServer"]
    assert "Первый ролик" not in res["withServer"], res["withServer"]


def test_finished_local_run_survives_status_poll(tmp_path: Any) -> None:
    """Итог закончившегося клиентского прогона не смывает опросом статуса.

    Дефект: `localQEnd()` обнулял LOCALQ, а опрос зовёт `queueRender(d)` каждую секунду
    (40-queue.js, 99-boot.js, 90-ae.js) — и СРАЗУ после конца разметки/интро в окне
    всплывал список прошлой серверной нарезки, ровно в конце прогона. Теперь `localQEnd`
    метит список `done`, и он держится до нового задания: `progOpen` (за ним стоит
    `progShow`) его сбрасывает, а серверное — только когда сервер сообщает о РАБОТАЮЩЕМ
    задании (`d.running`).

    Мутация «localQEnd снова обнуляет LOCALQ» валит проверку `afterEnd`.
    """
    script = _stand("""
const out={};
localQStart(['Первый ролик','Второй ролик']);
localQSet('Второй ролик','done','жёлтых: 12');
localQEnd();
queueRender({items:[{name:'СЕРВ-1',stage:'cut'}]});
out.afterEnd=$('qlist').innerHTML;
queueRender({running:true,items:[{name:'СЕРВ-2',stage:'cut'}]});
out.serverRunning=$('qlist').innerHTML;
localQStart(['Третий ролик']);
localQSet('Третий ролик','yellow','14 с');
queueRender({running:true,items:[{name:'СЕРВ-3',stage:'cut'}]});
out.whileLocal=$('qlist').innerHTML;
localQEnd();
progShow('Нарезка','готовлю…');
queueRender({items:[{name:'СЕРВ-4',stage:'cut'}]});
out.afterProgShow=$('qlist').innerHTML;
fs.writeSync(1, JSON.stringify(out)+'\\n');
process.exit(0);
""")
    res = _run_node(tmp_path, "prog_rows_queue_end.js", script)

    # 1. Опрос без работающего задания итог прогона не трогает: видно строки прогона,
    #    серверный элемент не нарисован
    assert "Первый ролик" in res["afterEnd"], res["afterEnd"]
    assert "СЕРВ-1" not in res["afterEnd"], \
        "опрос статуса вернул список прошлой нарезки вместо итога прогона: %r" % res["afterEnd"]

    # 2. Сервер сообщил о работающем задании — закончившийся прогон вытесняется серверным
    assert "СЕРВ-2" in res["serverRunning"], res["serverRunning"]
    assert "Первый ролик" not in res["serverRunning"], res["serverRunning"]

    # 3. Прогон ЕЩЁ ИДЁТ (done нет) — серверные items не рисуются даже при running
    assert "Третий ролик" in res["whileLocal"], res["whileLocal"]
    assert "СЕРВ-3" not in res["whileLocal"], \
        "работающий серверный джоб перебил идущий клиентский прогон: %r" % res["whileLocal"]

    # 4. Новое задание открывает окно через progShow/progOpen — он сбрасывает список
    #    прогона, дальше серверные строки
    assert "СЕРВ-4" in res["afterProgShow"], res["afterProgShow"]
    assert "Третий ролик" not in res["afterProgShow"], res["afterProgShow"]


def test_local_list_is_dropped_on_new_server_job() -> None:
    """Новое задание начинается с progOpen (дверь progShow) — он и сбрасывает LOCALQ
    (иначе список прошлого прогона пережил бы нарезку и снова показывался вместо
    серверного). Второй сброс — в опросе статуса, по работающему серверному заданию
    (d.running), и только у ЗАКОНЧИВШЕГОСЯ прогона: localQEnd список не обнуляет."""
    assert "LOCALQ=null;" in _func(PROG_JS, "progOpen"), \
        "новое задание не сбрасывает локальный список — он переживёт старт нарезки"
    assert "progOpen({title:" in _func(PROG_JS, "progShow"), \
        "progShow перестал быть дверью в общее окно"

    end = _func(PROG_JS, "localQEnd")
    assert "LOCALQ.done=true" in end, "localQEnd не метит список законченным: %r" % end
    assert "LOCALQ=null" not in end, \
        "localQEnd снова обнуляет список — итог прогона пропадёт на первом же опросе"

    render = _func(PROG_JS, "queueRender")
    assert "d&&d.running&&LOCALQ&&LOCALQ.done" in render, \
        "опрос статуса не вытесняет закончившийся прогон работающим серверным заданием"


def test_qstage_stages_and_detail_css() -> None:
    """Этапы разметки объявлены в словаре статусов и переведены, а .qdetail режет
    длинное многоточием."""
    qstage = _extract(_read(PROG_JS), "const PROGEV={")
    for key, label in (("subs", "субтитры"), ("yellow", "жёлтые (ИИ)"),
                       ("inserts", "вставки (ИИ)"), ("intro", "интро (ИИ)"),
                       ("files", "файлы вставок")):
        assert "%s:t('%s')" % (key, label) in qstage, "в словаре статусов нет этапа %s" % key

    m = re.search(r"\.qdetail\{([^}]+)\}", _read(CSS))
    assert m, "в app.css нет правила .qdetail"
    rule = m.group(1).replace(" ", "")
    for frag in ("display:block", "color:var(--mut)", "text-overflow:ellipsis", "white-space:nowrap"):
        assert frag in rule, "в .qdetail нет %s: %s" % (frag, rule)

    en = json.loads(_read(EN))
    for key in ("файлы вставок", "нет субтитров", "строк интро: ", "вставок: "):
        assert key in en, "нет перевода для строки списка: %r" % key


# ---- 2. Хвост лога: только строки своего клипа, без префикса ---------------

def test_log_tail_tag_filters_own_lines_and_strips_prefix(tmp_path: Any) -> None:
    """С тегом хвост берёт только строки «[тег] …» (в т.ч. структурные {t,v}) и срезает
    префикс; без тега — прежнее поведение: последняя строка общего лога как есть."""
    core = _read(CORE_JS)
    script = """
const fs=require('fs');
let LANG='ru',I18N={};
__T__
__FMTLOG__
let LOGCACHE=[],LOGSINCE=0;
function mergeLog(d){for(const l of (d.log||[]))LOGCACHE.push(l);LOGSINCE=d.log_total||LOGCACHE.length;}
const LINES=['[клип А] своя строка',{t:'[клип Б] чужая {n}',v:{n:1}},
  '[клип А] вторая строка','[клип Б] последняя'];
async function fetch(){return {json:async()=>({log:LINES,log_total:LINES.length})};}
__LOGTAIL__
(async()=>{
  const seen=[];
  const un1=logTail(s=>seen.push(s),'клип А');
  await new Promise(r=>setTimeout(r,50));
  un1();
  const un2=logTail(s=>seen.push(s));
  await new Promise(r=>setTimeout(r,50));
  un2();
  fs.writeSync(1, JSON.stringify({seen})+'\\n');
  process.exit(0);
})();
"""
    script = (script
              .replace("__T__", _extract(core, "function t(s, vars)"))
              .replace("__FMTLOG__", _extract(core, "function fmtLog(l)"))
              .replace("__LOGTAIL__", _extract(_read(EDITOR_JS), "function logTail(")))
    seen = _run_node(tmp_path, "prog_rows_logtail.js", script)["seen"]
    assert len(seen) == 2, seen

    own, alll = seen[0], seen[1]
    assert own.split(" · ", 1)[1] == "вторая строка", \
        "с тегом в хвост попало не своё или префикс не срезан: %r" % own
    assert "чужая" not in own and "последняя" not in own, \
        "в хвост утекли строки другого клипа: %r" % own
    assert alll.split(" · ", 1)[1] == "[клип Б] последняя", \
        "без тега поведение изменилось: %r" % alll


def test_ai_post_passes_tag_and_writes_event_code() -> None:
    """aiPost: тег считается из XML и уходит в logTail только при batch, а хвост лога
    превращается в КОД события и пишется в строку своего клипа — сама строка в окно
    прогресса не попадает (её место в «Показать логи»)."""
    post = _func(EDITOR_JS, "aiPost")
    assert "body.batch" in post, "aiPost шлёт тег и без пачки"
    assert "logTail(" in post and "},tag)" in post, "тег не уходит в logTail"
    assert "progEventFromLog(s)" in post, "хвост лога не разбирается в код события"
    assert "progItem(row.name,ev)" in post, \
        "статус строки клипа пишется мимо общей двери прогресса"
    assert "localQSet(row.name,row.stage,s)" not in post, \
        "сырой хвост лога снова пишется в строку ролика"
    assert "progCurItem()" in post, \
        "строка текущего клипа берётся не из общей формы (LOCALQ.cur)"

    tail = _extract(_read(EDITOR_JS), "function logTail(")
    assert re.search(r"function logTail\(setStatus,tag\)", _read(EDITOR_JS)), \
        "у logTail нет необязательного тега"
    assert "l.indexOf(pre)>=0" in tail, "хвост не фильтруется по тегу"
    assert "last.slice(pre.length)" in tail, "префикс не срезается"


# ---- 3. Прогоны ведут локальный список -------------------------------------

def test_markup_runs_through_local_list() -> None:
    """markupAllRun: список в начале, итог/этапы по клипам, закрытие в finally.
    markupClip (одиночная разметка) — список из одного клипа."""
    run = _func(EDITOR_JS, "markupAllRun")
    assert "localQStart(list.map(c=>c.name))" in run, "пакетная разметка не завела список"
    for stage in ("subs", "yellow", "inserts", "files"):
        assert "localQSet(c.name,'%s','')" % stage in run, \
            "нет этапа %s у строки клипа" % stage
    assert "localQSet(c.name,'error',''+e)" in run, "ошибка фазы не видна в строке"
    assert "localQSet(c.name,no===P?'done':'wait',qClipSum(c))" in run, \
        "нет итога строки после фазы"
    assert "localQSet(c.name,'wait',t('нет субтитров'))" in run, \
        "пропуск «нет субтитров» не виден в строке"
    assert "finally{localQEnd();}" in run, "список не закрывается в finally"

    clip = _func(EDITOR_JS, "markupClip")
    assert "localQStart([c.name])" in clip, "одиночная разметка идёт мимо списка"
    assert "finally{if(own)localQEnd();}" in clip, "список одиночной разметки не закрывается"


def test_intro_runs_through_local_list() -> None:
    """Пакетное ИИ-интро: список на набор, этап/итог/ошибка — в строке клипа."""
    run = _func(AE_JS, "aiIntroAllRun")
    assert "localQStart(list.map(c=>c.name))" in run, "пакетное ИИ-интро не завело список"
    assert "finally{localQEnd();}" in run, "список не закрывается в finally"

    one = _func(AE_JS, "aiIntroOne")
    assert "localQSet(c.name,'intro','')" in one, "этап интро не виден в строке"
    assert "localQSet(c.name,'done',t('строк интро: ')" in one, "нет итога строки интро"
    assert "localQSet(c.name,'error',''+e)" in one, "ошибка интро не видна в строке"
    assert "localQSet(c.name,'wait',t('нет субтитров'))" in one, \
        "пропуск «нет субтитров» не виден в строке"


# ---- 4. Роуты: префикс строк лога в пачке ----------------------------------

def _cmd_stub(url: str) -> Callable[..., Any]:
    """Заглушка aicut.cmd_*: зовёт emit ровно как боевая — одной строкой лога."""
    def stub(*a: Any, **kw: Any) -> Any:
        kw["emit"]("строка лога")
        if url == "/api/ai_yellow":
            return {"yellow": [], "colored": [], "total": 0}
        if url == "/api/ai_inserts":
            return {"inserts": [], "ins_target": 0}
        return {"intro_rows": [], "mid_groups": []}
    return stub


@pytest.fixture
def client() -> Any:
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("url,cmd", [
    ("/api/ai_yellow", "cmd_yellow"),
    ("/api/ai_inserts", "cmd_inserts"),
    ("/api/ai_intro", "cmd_intro"),
])
def test_batch_log_lines_get_clip_prefix(url: str, cmd: str, client: Any,
                                         monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Any) -> None:
    """С batch строки уходят в общий лог с префиксом «[имя XML] », без batch — как раньше.

    Готовый текст ответа (log=notes) префикса не получает: это подпись для UI, а не строка
    общего лога — по префиксу фронт отличает свой хвост от чужого.
    """
    captured: list[str] = []
    monkeypatch.setattr(ai_route, "emit", lambda line="", **kw: captured.append(line))
    monkeypatch.setattr(ai_route, "_ai_begin", lambda *a, **kw: 7)
    monkeypatch.setattr(ai_route, "_ai_end", lambda *a, **kw: None)
    monkeypatch.setattr(aicut, cmd, _cmd_stub(url))

    xml = tmp_path / "07_teleprompter.xml"
    xml.write_text("<x/>", encoding="utf-8")

    r = client.post(url, json={"xml": str(xml), "batch": "b1"}, headers=H)
    assert r.status_code == 200, r.data[:300]
    assert captured == ["[07_teleprompter] строка лога"], captured
    log = r.get_json().get("log")
    if log is not None:
        assert log == ["строка лога"], "префикс попал в готовый текст ответа: %r" % log

    captured.clear()
    r2 = client.post(url, json={"xml": str(xml)}, headers=H)
    assert r2.status_code == 200, r2.data[:300]
    assert captured == ["строка лога"], "без batch строка ушла с префиксом: %r" % captured
