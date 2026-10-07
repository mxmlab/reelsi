# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Состояние работы: сервер главный при загрузке, номер версии сохранения.

Что стерегут тесты (два дефекта одного корня — «состояние живёт в браузере»):

1. **Сервер главный.** `restoreState()` берёт состояние С СЕРВЕРА, а не из
   localStorage. Раньше порядок был обратный, и устаревшая копия браузера (вторая
   вкладка, старый профиль Chrome, другой браузер) перекрывала серверную, а через
   2,5 с перезаписывала её — работа над клипами откатывалась.
2. **Номер версии (ревизия).** Сервер хранит `_rev` вместе с состоянием, отдаёт его
   в GET и в каждом ответе на POST, а POST принимает `base_rev`. Разошлись — сервер
   НЕ пишет ничего (ни `ui_state.json`, ни снимки `<стем>.clip.json`) и отвечает
   кодом `stale_state`; вкладка после этого прекращает сохранения и показывает
   постоянную плашку с кнопкой «Обновить».

Роут-часть гоняет НАСТОЯЩИЙ `api/files.py` через flask-клиент, файл состояния — в
`tmp_path`, а не живой `ui_state.json`. Фронт-часть гоняет БОЕВЫЕ функции
`static/app/99-boot.js` под node с заглушками DOM и сети (как
`tests/test_ins_identity_undo.py`): копий логики в тесте нет.

Запуск: py -3.10 -m pytest tests/test_ui_state_rev.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402
from core import clipstore  # noqa: E402

BOOT = os.path.join(ROOT, "static", "app", "99-boot.js")
H = {"Content-Type": "application/json"}
node = pytest.mark.skipif(not shutil.which("node"),
                          reason="контракт фронта требует node в PATH")


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def live_state(tmp_path, monkeypatch):
    """Свой файл состояния на каждый тест: живой ui_state.json НЕ трогаем."""
    p = tmp_path / "ui_state.json"
    monkeypatch.setattr(api.files, "UI_STATE_PATH", str(p))
    return p


def _write_xml_cut(folder, stem="01_clip"):
    xml = folder / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")
    return xml


# ============================ роут /api/ui_state ============================

def test_get_returns_state_and_rev(client, live_state):
    """GET: нет файла — state=None, rev=0; после записи — состояние и его ревизия."""
    d = client.get("/api/ui_state").get_json()
    assert d["ok"] is True and d["state"] is None and d["rev"] == 0

    assert client.post("/api/ui_state", json={"state": {"STEP": 2}, "base_rev": 0}).get_json()["ok"]
    d = client.get("/api/ui_state").get_json()
    assert d["state"] == {"STEP": 2}
    assert d["rev"] == 1


def test_post_with_right_base_rev_bumps_rev_and_writes_the_file(client, live_state):
    """POST с верным base_rev: файл записан, _rev верхним ключом, ответ — новый rev.

    `_rev` лежит в САМОМ файле (переживает перезапуск сервера) и наружу в state НЕ
    уезжает: клиент получает состояние ровно таким, каким его отправил."""
    d = client.post("/api/ui_state", json={"state": {"STEP": 3}, "base_rev": 0}).get_json()
    assert d["ok"] is True and d["rev"] == 1

    raw = json.load(io.open(str(live_state), encoding="utf-8"))
    assert raw["_rev"] == 1 and raw["STEP"] == 3, raw
    assert client.get("/api/ui_state").get_json()["state"] == {"STEP": 3}, \
        "ревизия уехала в state — состояние должно быть ровно тем, что отправили"

    d = client.post("/api/ui_state", json={"state": {"STEP": 1}, "base_rev": 1}).get_json()
    assert d["rev"] == 2


def test_post_with_stale_base_rev_writes_nothing(client, live_state, tmp_path):
    """Устаревшая вкладка: ни файла состояния, ни снимка клипа — только stale_state.

    Проверка «ничего не записали» — по ДВУМ файлам сразу: состояние и
    `<стем>.clip.json` пишутся одним запросом, и второй не должен проскочить мимо
    первой проверки (работа над клипами откатывалась именно через снимки)."""
    xml = _write_xml_cut(tmp_path, "01_stale")
    accepted = {"CLIPS": [{"xml": str(xml), "name": "01_stale.xml", "inserts": [{"media": "keep.png"}]}]}
    assert client.post("/api/ui_state", json={"state": accepted, "base_rev": 0}).get_json()["ok"]
    before_state = live_state.read_text(encoding="utf-8")
    cpath = clipstore.clip_path(str(xml))
    before_clip = io.open(cpath, encoding="utf-8").read()

    stale = {"CLIPS": [{"xml": str(xml), "name": "01_stale.xml", "inserts": []}]}
    d = client.post("/api/ui_state", json={"state": stale, "base_rev": 0}).get_json()

    assert d.get("ok") is not True and d["err"] == "stale_state", d
    assert d["error"].strip(), "у кода stale_state нет русского текста для интерфейса"
    assert d["rev"] == 1, "ответ обязан нести текущую ревизию — по ней вкладка понимает, что отстала"
    assert live_state.read_text(encoding="utf-8") == before_state, "устаревший POST переписал ui_state.json"
    assert io.open(cpath, encoding="utf-8").read() == before_clip, "устаревший POST переписал снимок клипа"
    assert clipstore.load_clip(str(xml))["inserts"] == [{"media": "keep.png"}]


def test_post_without_base_rev_is_accepted(client, live_state):
    """Старый клиент (без base_rev) принимается как раньше — совместимость.

    Иначе обновлённый сервер отказывал бы открытой странице прежней версии, и
    человек терял бы правки, ничего не поняв."""
    d = client.post("/api/ui_state", json={"state": {"STEP": 1}}).get_json()
    assert d["ok"] is True and d["rev"] == 1
    assert json.load(io.open(str(live_state), encoding="utf-8"))["STEP"] == 1
    # и дальше по-прежнему: у клиента ревизии нет, любая его запись проходит
    assert client.post("/api/ui_state", json={"state": {"STEP": 2}}).get_json()["ok"] is True


def test_two_posts_with_one_base_rev_second_is_refused(client, live_state):
    """Два POST с ОДНИМ base_rev подряд: второй отказ (проверка и запись под замком)."""
    body = {"state": {"STEP": 2}, "base_rev": 0}
    assert client.post("/api/ui_state", json=body).get_json()["rev"] == 1
    d = client.post("/api/ui_state", json=body).get_json()
    assert d["err"] == "stale_state" and d["rev"] == 1, d
    assert json.load(io.open(str(live_state), encoding="utf-8"))["_rev"] == 1


def test_concurrent_posts_with_one_base_rev_pass_only_once(live_state, tmp_path):
    """Тот же случай, но по-настоящему одновременно: проходит ровно один.

    Без общего замка оба запроса успевают прочитать одну ревизию и оба пишут —
    именно так вторая вкладка затирала первую."""
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True

    results = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def post(step):
        c = app.test_client()
        start.wait(5)
        d = c.post("/api/ui_state", json={"state": {"STEP": step}, "base_rev": 0}).get_json()
        with lock:
            results.append(d)

    threads = [threading.Thread(target=post, args=(s,)) for s in (2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert len(results) == 2, results
    oks = [d for d in results if d.get("ok")]
    stale = [d for d in results if d.get("err") == "stale_state"]
    assert len(oks) == 1 and len(stale) == 1, \
        f"с одним base_rev прошло {len(oks)} записей, отказано {len(stale)}"
    assert json.load(io.open(str(live_state), encoding="utf-8"))["_rev"] == 1


def test_file_without_rev_is_read_as_rev_zero(client, live_state):
    """Файл состояния, записанный до этой правки (без `_rev`), — ревизия 0.

    Иначе обновление сервера ломало бы уже открытую вкладку: её base_rev=0 не
    совпал бы с «неизвестно чем» и все сохранения отклонялись бы."""
    live_state.write_text(json.dumps({"STEP": 3}), encoding="utf-8")
    d = client.get("/api/ui_state").get_json()
    assert d["rev"] == 0 and d["state"] == {"STEP": 3}
    assert client.post("/api/ui_state", json={"state": {"STEP": 4}, "base_rev": 0}).get_json()["ok"]


def test_stale_tab_cannot_write_after_the_refusal(client, live_state):
    """Сквозной случай двух вкладок: A (rev N) не затирает правку B (rev N+1).

    Ключ к дефекту — ВТОРАЯ попытка A: её отложенная отправка уходит уже после
    отказа, с тем же base_rev N (ревизию из отказа вкладка не принимает). Пока
    сервер отказывал только на первый POST, второй проходил и стирал правку B."""
    # вкладка A загружается на ревизии N
    assert client.post("/api/ui_state",
                       json={"state": {"STEP": 1, "note": "seed"}, "base_rev": 0}).get_json()["rev"] == 1
    a_rev = client.get("/api/ui_state").get_json()["rev"]
    assert a_rev == 1

    # вкладка B правит клип: ревизия N+1
    b = client.application.test_client()
    assert b.post("/api/ui_state",
                  json={"state": {"STEP": 7, "note": "b"}, "base_rev": a_rev}).get_json()["rev"] == 2

    # вкладка A шлёт свою правку от прежней ревизии — отказ, в файле остаётся правка B
    d = client.post("/api/ui_state", json={"state": {"STEP": 3, "note": "a"}, "base_rev": a_rev}).get_json()
    assert d.get("ok") is not True and d["err"] == "stale_state", d
    assert d["rev"] == 2, "в отказе сервер обязан отдать текущую ревизию"
    raw = json.load(io.open(str(live_state), encoding="utf-8"))
    assert raw["note"] == "b" and raw["STEP"] == 7 and raw["_rev"] == 2, raw

    # отложенная отправка A (тот же base_rev: из отказа ревизия НЕ присвоена) — снова отказ
    d = client.post("/api/ui_state", json={"state": {"STEP": 3, "note": "a"}, "base_rev": a_rev}).get_json()
    assert d.get("ok") is not True and d["err"] == "stale_state", d
    assert d["rev"] == 2, d
    raw = json.load(io.open(str(live_state), encoding="utf-8"))
    assert raw["note"] == "b" and raw["_rev"] == 2, "вторая попытка устаревшей вкладки затёрла правку B"
    assert client.get("/api/ui_state").get_json()["state"] == {"STEP": 7, "note": "b"}


# ============================ фронт: static/app/99-boot.js ============================

def _read(path):
    with io.open(path, encoding="utf-8") as f:
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
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _line(src, needle):
    m = re.search(r"^.*%s.*$" % re.escape(needle), src, re.M)
    assert m, f"в исходнике нет объявления {needle}"
    return m.group(0)


# Заглушки — только внешние двери (DOM, сеть, тосты). Всё, что проверяется, —
# боевые функции 99-boot.js. localStorage/setTimeout/fetch поддельные: тест должен
# видеть, ЧТО ушло на сервер и что легло в браузер, а не ждать настоящих 1,2 с.
STUBS = r"""
let TOASTS=[],LOGS=[],ELS={},BANNERS=[],WINS={},STORED={},REQS=[],RESP=[],RELOAD=0;
let STORE={},SEED='{}';
let localStorage={setItem:(k,v)=>{STORE[k]=String(v);},getItem:k=>(k in STORE?STORE[k]:null),removeItem:k=>{delete STORE[k];}};
// setTimeout настоящий, но с нулевой задержкой: дебаунс зеркала (SRVST_DELAY) в
// тесте не ждём, а микрозадачи промисов обязаны доходить — синхронный вызов
// колбэка останавливал бы разбор ответа сервера до конца сценария.
let REAL_SETTIMEOUT=setTimeout;
setTimeout=(f,ms)=>REAL_SETTIMEOUT(f,0);clearTimeout=()=>{};
let Date={now:()=>0};
function El(id){this.id=id;this.style={};this.dataset={};this.disabled=false;this.textContent='';
  this.innerHTML='';this.children=[];this._ev={};this.checked=false;this.value='';this.className='';
  this.classList={contains:()=>false,toggle:()=>{},add:()=>{},remove:()=>{}};}
El.prototype.addEventListener=function(n,f){(this._ev[n]=this._ev[n]||[]).push(f);};
El.prototype.appendChild=function(c){this.children.push(c);return c;};
El.prototype.setAttribute=function(n,v){this[n]=v;};
function $(id){if(!ELS[id])ELS[id]=new El(id);return ELS[id];}
let BODY=new El('body');
let document={body:BODY,getElementById:id=>ELS[id]||BANNERS.find(b=>b.id===id)||null,
  querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{},
  createElement:tag=>{const e=new El(tag);if(tag==='div')BANNERS.push(e);return e;}};
let location={reload:()=>{RELOAD++;}};
let navigator={sendBeacon:(u,b)=>{REQS.push('beacon '+u+' '+b);return true;}};
let Blob=function(parts,opts){this.parts=parts;this.opts=opts;this.toString=()=>String(parts[0]);};
let window={addEventListener:(n,f)=>{WINS[n]=f;},removeEventListener:n=>{delete WINS[n];}};
function fetch(url,opts){REQS.push(String(url)+' '+(opts&&opts.body?opts.body:''));
  const d=RESP.length?RESP.shift():{ok:true,rev:1};
  return Promise.resolve({ok:true,json:()=>Promise.resolve(d)});}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>String((vars||{})[k]));}
function errText(d){return String((d&&(d.error||d))||'');}
function esc(s){return String(s==null?'':s);}
function uiLog(m){LOGS.push(String(m));}
function toast(m,ms){TOASTS.push(String(m));}
function val(){return '';}
function rendEngine(){return 'ae';}
function rendEngineSet(){}
function nCams(){return 2;}
function vidStateObj(){return {};}
function buildCamRows(){}
function renderQueue(){}
function renderAeDirField(){}
function renderCutStagesUI(){}
function cutSummary(){}
function spkEditUI(){}
function asrMigrate(v){return v;}
function clipPathFix(p){return p;}
function insHistTouch(){}
function captureAE(){}
function flushSave(){saveState();}
// goStep в бою и рисует шаг, и ПРИСВАИВАЕТ STEP — здесь только присваивание:
// без него сценарий не отличил бы «шаг применился» от «шаг остался прежним».
function goStep(n){STEP=n;}
function refreshStatuses(){}
function stMigrateIntroCam2(s){return s;}
function stMigrateCam2Zoom(s){return s;}
function stMigrateIntroPos2(s){return s;}
let AEGLOBAL='',AERENDER='',CAMDIRS=[],CAMFILES=[],CAMFROM=[],QUEUE=[],CUT_STAGES={},CUT_THRESHOLDS={};
let CURSTYLE=null,STEP=1,curAE=-1,SPKSAVED='',SUBWANT='',VIDSAVED=null,STYLESAVED='',VREFS=[],LANG='ru';
let CLIPS=[];
let LASTCAMS=2;
"""


def _stand():
    """Боевая персистентность 99-boot.js: объявления состояния, saveState/зеркало,
    applyState/restoreState и обработчик beforeunload — как есть."""
    boot = _read(BOOT)
    parts = [STUBS]
    # от LSKEY (объявления дебаунса зеркала и ревизии) до applyState — вместе с
    # storedState, который лежит сразу перед restoreState
    parts.append(_slice(boot, "const LSKEY=", "function storedState(s)"))
    parts.append(_func(boot, "storedState"))
    parts.append(_func(boot, "applyState"))
    parts.append(_func(boot, "restoreState"))
    parts.append(_slice(boot, "window.addEventListener('beforeunload'", "document.addEventListener('visibilitychange'"))
    # Ждём и таймеры, и микрозадачи промисов: одного await мало — цепочка ответа
    # сервера длиннее (fetch → json() → проверка), и сценарий успел бы прочитать
    # состояние до того, как она доиграет.
    parts.append("const _flush=()=>new Promise(r=>REAL_SETTIMEOUT(r,0));")
    return "\n".join(parts)


def _run(tmp_path, name, body):
    # Сценарии ждут ответа сервера (`await`) — верхний await заворачиваем в async-IIFE,
    # а необязательный reject печатаем и отдаём ненулевым кодом, чтобы он не пропал
    # молча (отказ в промис-цепочке иначе оставил бы тест «зелёным» без данных).
    script = (_stand() + "\n(async()=>{\n" + body
              + "\n})().catch(e=>{console.error(e&&e.stack||e);process.exitCode=1;});\n")
    path = str(tmp_path / name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(script)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


@node
def test_boot_takes_the_server_state_over_localstorage(tmp_path):
    """Сервер отдал A, в localStorage лежит B → применено A (и A же легло в браузер).

    Это и есть главное правило: устаревшая копия браузера серверное не перекрывает."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[{xml:'B.xml'}],STEP:2});
RESP=[{ok:true,rev:7,state:{CLIPS:[{xml:'A.xml'}],STEP:3}}];
restoreState();
await _flush();
console.log(JSON.stringify({clips:CLIPS.map(c=>c.xml),step:STEP,stored:JSON.parse(STORE['reelsi_state']).CLIPS[0].xml,
  rev:SRVST_REV,last:SRVST_LAST,toasts:TOASTS,reqs:REQS}));
"""
    r = _run(tmp_path, "boot_server.js", body)
    assert r["clips"] == ["A.xml"], "применено состояние из localStorage, а не с сервера"
    assert r["step"] == 3
    assert r["stored"] == "A.xml", "серверное состояние обязано лечь в localStorage"
    assert r["rev"] == 7, "ревизия с сервера не запомнена — сохранения поедут с base_rev=0"
    assert r["last"] == json.dumps({"CLIPS": [{"xml": "A.xml"}], "STEP": 3}, separators=(",", ":")), \
        f"SRVST_LAST не равен применённому состоянию: {r['last']!r}"


@node
def test_server_down_falls_back_to_localstorage(tmp_path):
    """Сервер не ответил → работаем с копией браузера и говорим об этом тостом."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[{xml:'B.xml'}],STEP:2});
fetch=()=>Promise.reject(new Error('сеть легла'));
restoreState();
await _flush();
console.log(JSON.stringify({clips:CLIPS.map(c=>c.xml),toasts:TOASTS,logs:LOGS}));
"""
    r = _run(tmp_path, "boot_local.js", body)
    assert r["clips"] == ["B.xml"], "сервер молчит, а копия браузера не применена"
    assert any("Сервер недоступен" in x for x in r["toasts"]), r["toasts"]


@node
def test_stale_answer_stops_server_writes_and_shows_the_banner(tmp_path):
    """Ответ stale_state: следующий saveState на сервер НЕ идёт, плашка есть."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[],STEP:1});
RESP=[{ok:true,rev:4,state:{CLIPS:[],STEP:1}},{stale:true,err:'stale_state',rev:9}];
restoreState();
await _flush();
const afterBoot=REQS.length;
CLIPS=[{xml:'mine.xml'}];
saveState();
await _flush();
const afterSave=REQS.length;
CLIPS=[{xml:'mine2.xml'}];
saveState();
await _flush();
CLIPS=[{xml:'mine3.xml'}];
WINS['beforeunload']();
await _flush();
console.log(JSON.stringify({afterBoot,afterSave,afterStale:REQS.length,
  banner:BANNERS.map(b=>b.id),bannerText:BANNERS[0]&&BANNERS[0].children.map(c=>c.textContent),
  onclick:typeof (BANNERS[0]&&BANNERS[0].children[1]&&BANNERS[0].children[1].onclick),
  stale:SRVST_STALE,body:REQS[1]||''}));
"""
    r = _run(tmp_path, "boot_stale.js", body)
    assert r["afterSave"] == r["afterBoot"] + 1, "первое сохранение не ушло на сервер"
    assert r["afterStale"] == r["afterSave"], \
        "после stale_state вкладка продолжает писать на сервер — серверное затрётся"
    assert r["stale"] is True
    assert "srvstale" in r["banner"], f"постоянной плашки нет: {r['banner']}"
    assert any("устарела" in (t or "") for t in r["bannerText"]), r["bannerText"]
    assert "Обновить" in r["bannerText"], "у плашки нет кнопки «Обновить»"
    assert r["onclick"] == "function", "кнопка «Обновить» ничего не делает (нет reload)"
    assert '"base_rev":4' in r["body"], f"сохранение ушло без base_rev: {r['body']!r}"


@node
def test_every_state_write_carries_base_rev(tmp_path):
    """base_rev несёт и зеркало, и sendBeacon при закрытии вкладки.

    Пропущенный в sendBeacon base_rev вернул бы ровно тот дефект, ради которого всё
    и делается: закрытая старая вкладка дописывает своё состояние последней."""
    boot = _read(BOOT)
    posts = re.findall(r"fetch\('/api/ui_state'[^)]*\)", boot)
    assert posts, "в 99-boot.js не нашлось ни одного POST на /api/ui_state"
    assert '"base_rev"' in boot, "в 99-boot.js пропал base_rev из тела сохранения"

    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[],STEP:1});
RESP=[{ok:true,rev:5,state:{CLIPS:[],STEP:1}},{ok:true,rev:6}];
restoreState();
await _flush();
CLIPS=[{xml:'x.xml'}];
WINS['beforeunload']();
await _flush();
console.log(JSON.stringify({reqs:REQS}));
"""
    r = _run(tmp_path, "boot_beacon.js", body)
    assert any(x.startswith("beacon /api/ui_state") for x in r["reqs"]), r["reqs"]
    beacon_body = [x for x in r["reqs"] if x.startswith("beacon ")][0]
    assert '"base_rev":5' in beacon_body, f"sendBeacon без ревизии: {beacon_body!r}"


@node
def test_stale_tab_does_not_beacon_on_close(tmp_path):
    """Устаревшая вкладка молчит и при закрытии: «дописать при выходе» затирало бы сервер."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[],STEP:1});
RESP=[{ok:true,rev:1,state:{CLIPS:[],STEP:1}},{stale:true,err:'stale_state',rev:2}];
restoreState();
await _flush();
CLIPS=[{xml:'mine.xml'}];
saveState();
await _flush();
const before=REQS.length;
WINS['beforeunload']();
await _flush();
console.log(JSON.stringify({before,after:REQS.length}));
"""
    r = _run(tmp_path, "boot_stale_beacon.js", body)
    assert r["after"] == r["before"], "устаревшая вкладка отправила sendBeacon при закрытии"


@node
def test_refusal_does_not_hand_the_tab_the_server_rev(tmp_path):
    """Ответ stale_state с rev:8 при своей ревизии 5 НЕ присваивается: остаётся 5.

    Присвоенная чужая ревизия делала устаревшую вкладку «свежей»: первый же
    следующий POST уходил с base_rev=8, совпадал с сервером и принимался —
    устаревшая копия затирала чужую правку."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[],STEP:1});
RESP=[{ok:true,rev:5,state:{CLIPS:[],STEP:1}},{stale:true,err:'stale_state',rev:8}];
restoreState();
await _flush();
const bootLast=SRVST_LAST;
CLIPS=[{xml:'mine.xml'}];
saveState();
await _flush();
console.log(JSON.stringify({rev:SRVST_REV,stale:SRVST_STALE,last:SRVST_LAST,bootLast:bootLast}));
"""
    r = _run(tmp_path, "boot_stale_rev.js", body)
    assert r["stale"] is True, "отказ не перевёл вкладку в «устарела»"
    assert r["rev"] == 5, f"ревизия из отказа присвоена ({r['rev']}) — вкладка считает себя свежей"
    assert r["last"] == r["bootLast"], "отказ засчитан за успешную запись: SRVST_LAST обновлён"


@node
def test_delayed_post_armed_before_the_refusal_never_leaves(tmp_path):
    """Отложенная отправка, взведённая ДО отказа, после отказа на сервер НЕ уходит.

    Гонка, ради которой гард стоит в самой `srvStatePost`: таймер дебаунса
    срабатывает уже после отказа, в обход проверки в `srvStateSave`, — и без гарда
    уходил POST (с присвоенной чужой ревизией). `clearTimeout` в стенде заглушён,
    поэтому здесь проверяется именно дверь отправки, а не снятие таймера."""
    body = """
STORE['reelsi_state']=JSON.stringify({CLIPS:[],STEP:1});
RESP=[{ok:true,rev:5,state:{CLIPS:[],STEP:1}},{stale:true,err:'stale_state',rev:8}];
restoreState();
await _flush();
CLIPS=[{xml:'a.xml'}];
Date.now=()=>1e6;   // потолок ожидания: первая отправка уходит сразу и получит отказ
saveState();
Date.now=()=>0;     // дальше снова дебаунс
CLIPS=[{xml:'b.xml'}];
saveState();        // взводит отложенную отправку ДО прихода отказа
await _flush();     // сперва доигрывается отказ (SRVST_STALE=true), затем срабатывает таймер
await _flush();
console.log(JSON.stringify({reqs:REQS,rev:SRVST_REV,stale:SRVST_STALE}));
"""
    r = _run(tmp_path, "boot_delayed_stale.js", body)
    assert r["stale"] is True, "отказ не перевёл вкладку в «устарела»"
    posts = [x for x in r["reqs"] if x.startswith("/api/ui_state {")]
    assert len(posts) == 1, f"после отказа ушла ещё одна запись на сервер: {posts}"
    assert '"base_rev":5' in posts[0], f"запись ушла не со своей ревизией: {posts[0]!r}"


# ============== отправка по одной: «в полёте» и «ожидает» ==============
# Ответ сервера дольше паузы между сохранениями — штатный случай (дебаунс, flushSave
# раз в 2,5 с, состояние большого размера). Два запроса подряд несут ОДИН base_rev,
# второй получает stale_state — вкладка объявляет устаревшей саму себя, а последняя
# правка теряется. Поэтому запрос всегда один: следующее состояние ждёт ответа.
# fetch в этих сценариях отвечает промисом, который резолвим РУЧНУЮ: только так видно,
# что ушло, пока ответ не пришёл.

@node
def test_second_post_waits_for_the_first_and_leaves_with_the_new_rev(tmp_path):
    """Два сохранения подряд: пока запрос в полёте, второе состояние НЕ уходит.

    После успешного ответа (rev 6) ожидающее уходит со СВЕЖЕЙ ревизией и последним
    состоянием, а вкладка остаётся свежей (SRVST_STALE не поднимается)."""
    body = """
let ANSWERS=[];
fetch=(url,opts)=>{REQS.push(String(url)+' '+(opts&&opts.body?opts.body:''));
  return new Promise(res=>{ANSWERS.push(d=>res({ok:true,json:()=>Promise.resolve(d)}));});};
CLIPS=[{xml:'a.xml'}];
saveState();
await _flush();     // дебаунс дозрел: первый запрос ушёл и висит без ответа
CLIPS=[{xml:'b.xml'}];
saveState();
await _flush();     // запрос в полёте — второе состояние обязано ждать, а не уйти
const inFlight=REQS.length;
ANSWERS[0]({ok:true,rev:6});
await _flush();await _flush();
console.log(JSON.stringify({inFlight,reqs:REQS,rev:SRVST_REV,stale:SRVST_STALE,pending:SRVST_PENDING}));
"""
    r = _run(tmp_path, "srvst_serial.js", body)
    assert r["inFlight"] == 1, f"второе состояние ушло, не дождавшись ответа: {r['reqs']}"
    assert len(r["reqs"]) == 2, f"ожидающее не ушло после успешного ответа: {r['reqs']}"
    assert '"base_rev":0' in r["reqs"][0], r["reqs"][0]
    assert '"base_rev":6' in r["reqs"][1], f"ожидающее ушло со старой ревизией: {r['reqs'][1]!r}"
    assert '"xml":"b.xml"' in r["reqs"][1], f"ушло не последнее состояние: {r['reqs'][1]!r}"
    assert r["rev"] == 6, "ревизия успешного ответа не запомнена"
    assert r["stale"] is False, "своя же вкладка объявила себя устаревшей"
    assert r["pending"] is None, "ожидающее не снято после отправки"


@node
def test_only_the_last_state_is_kept_while_a_request_is_in_flight(tmp_path):
    """Три сохранения подряд: в ожидании остаётся только ПОСЛЕДНЕЕ состояние."""
    body = """
let ANSWERS=[];
fetch=(url,opts)=>{REQS.push(String(url)+' '+(opts&&opts.body?opts.body:''));
  return new Promise(res=>{ANSWERS.push(d=>res({ok:true,json:()=>Promise.resolve(d)}));});};
CLIPS=[{xml:'a.xml'}];saveState();await _flush();
CLIPS=[{xml:'b.xml'}];saveState();await _flush();
CLIPS=[{xml:'c.xml'}];saveState();await _flush();
const inFlight=REQS.length;
ANSWERS[0]({ok:true,rev:6});
await _flush();await _flush();
console.log(JSON.stringify({inFlight,reqs:REQS,pending:SRVST_PENDING}));
"""
    r = _run(tmp_path, "srvst_pending_last.js", body)
    assert r["inFlight"] == 1, f"пока запрос в полёте, ушёл не один запрос: {r['reqs']}"
    assert len(r["reqs"]) == 2, f"после ответа ушло не одно состояние: {r['reqs']}"
    assert '"xml":"c.xml"' in r["reqs"][1], f"ушло не последнее состояние: {r['reqs'][1]!r}"
    assert '"xml":"b.xml"' not in r["reqs"][1], "промежуточное состояние ушло следом за последним"
    assert '"base_rev":6' in r["reqs"][1], r["reqs"][1]
    assert r["pending"] is None, "ожидающее не снято после отправки"


@node
def test_pending_is_dropped_when_the_first_post_is_refused(tmp_path):
    """Отказ stale_state на первый запрос: ожидающее НЕ уходит.

    Устаревшая вкладка не дописывает своё даже последним состоянием — иначе правку,
    из-за которой она признана устаревшей, затёр бы второй запрос."""
    body = """
let ANSWERS=[];
fetch=(url,opts)=>{REQS.push(String(url)+' '+(opts&&opts.body?opts.body:''));
  return new Promise(res=>{ANSWERS.push(d=>res({ok:true,json:()=>Promise.resolve(d)}));});};
CLIPS=[{xml:'a.xml'}];saveState();await _flush();
CLIPS=[{xml:'b.xml'}];saveState();await _flush();
const inFlight=REQS.length;
ANSWERS[0]({stale:true,err:'stale_state',rev:9});
await _flush();await _flush();
console.log(JSON.stringify({inFlight,reqs:REQS,stale:SRVST_STALE,pending:SRVST_PENDING,rev:SRVST_REV}));
"""
    r = _run(tmp_path, "srvst_pending_stale.js", body)
    assert r["inFlight"] == 1, r["reqs"]
    assert len(r["reqs"]) == 1, f"после отказа ушло ожидающее состояние: {r['reqs']}"
    assert r["stale"] is True, "отказ не перевёл вкладку в «устарела»"
    assert r["pending"] is None, "ожидающее не выброшено при отказе"
    assert r["rev"] == 0, "ревизия из отказа присвоена вкладке"
