# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Мелкие правки превью (задание MD).

Пять проверок — по одной на каждую правку задания:

1. Новый план (шрифт, высота) виден на паузе. `ipvSubs` перестраивал строки только при
   смене ключа показа (visKey — набор видимых слов): правка стиля приходила новым планом
   с тем же набором слов, и показ оставался старым до смены слова. Стенд прогоняет
   БОЕВОЙ путь — `ipvPlanFetch` с планом A, потом с планом B (другие `posy`/`fsize`) на
   том же времени, — и смотрит на DOM и на CSS-переменные субтитров.
2. Драга субтитров нет: нет обработчика `pointerdown` на `#ipvsub`, у `.pvsubw` нет
   `cursor:ns-resize` и `pointer-events:auto` (строка больше не перехватывает клик).
3. Панель стиля в превью прокручивается: `#aewstyle #stpanel` — `overflow:auto` с
   `!important` (перебивает `.stpanel{overflow:visible !important}` общей панели) и
   `min-height:0`; строка «Сохранить» снизу не сжимается.
4. Шаг 1: заголовок — имя открытого файла (стенд зовёт боевые `pvTitle`/`openEditClip`
   с боевым `clipLabel`), в разметке нет хвостов « — как будет в ролике» и
   « — исходник камеры 1», тултип «i» у «Редактор» остался.
5. Прогресс прокси: разбор `out_time_us=…` даёт проценты, `/api/preview_proxy_status`
   отдаёт `pct`, а фронт рисует по нему блок поверх плеера.
6. `_run_ff` с НАСТОЯЩИМ подпроцессом: прогресс приходит от живого процесса до его
   завершения, отмена убивает процесс (дефект приёмки: на Windows в `TimeoutExpired`
   от `communicate` лежит output=None, а подменённый в стендах ffmpeg это прятал).

Запуск:  python -m pytest tests/test_preview_ui_md.py -q
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

APP = os.path.join(ROOT, "static", "app")
VIEW_JS = os.path.join(APP, "85-inserts-view.js")
PREV_JS = os.path.join(APP, "60-preview.js")
QUEUE_JS = os.path.join(APP, "40-queue.js")
CSS = os.path.join(ROOT, "static", "app.css")
HTML = os.path.join(ROOT, "templates", "index.html")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _slice(src, start, end):
    """Кусок исходника от `start` до следующего `end` (обе границы — точные строки)."""
    a = src.index(start)
    b = src.index(end, a)
    return src[a:b]


def _run_node(tmp_path, name, script):
    js_file = tmp_path / name
    js_file.write_text(script, encoding="utf-8")
    return subprocess.run(["node", str(js_file)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


# --------------------------------------------------------------------------- #
# 1. Новый план виден на паузе (сброс кэша показа субтитров)
# --------------------------------------------------------------------------- #
# Эмуляция DOM ровно под ipvSubs: #ipvsub 540x960 (k = 540/1080 = 0.5), host разбирает
# innerHTML на строки .pvsubw и слова .pvsubw_wd. CSS-переменные субтитров (их ставит
# ipvSubs через setProperty) складываются в props — по ним и видно, что кегль обновился.
_SUBS_DOM = r"""
const assert = require('assert');

function El(tag){
  this.tagName=String(tag).toUpperCase();
  this._cls=new Set();
  this.props={};
  this.style={setProperty:(k,v)=>{this.props[k]=String(v);},
    removeProperty:k=>{delete this.props[k];},
    getPropertyValue:k=>(this.props[k]||'')};
  this.styleStr='';this.dataset={};this.children=[];this.childNodes=[];
  this.parent=null;this._html='';this._text='';
  const self=this;
  this.classList={
    add:c=>self._cls.add(c),remove:c=>self._cls.delete(c),
    toggle:(c,v)=>{const on=(v===undefined)?!self._cls.has(c):!!v;
      if(on)self._cls.add(c);else self._cls.delete(c);return on;},
    contains:c=>self._cls.has(c)};
}
Object.defineProperty(El.prototype,'className',{
  get(){return [...this._cls].join(' ');},
  set(v){this._cls=new Set(String(v||'').split(/\s+/).filter(Boolean));}});
Object.defineProperty(El.prototype,'textContent',{
  get(){return this._text||this.children.map(c=>c.textContent).join('');},
  set(v){this._text=''+(v==null?'':v);this.children=[];}});
Object.defineProperty(El.prototype,'innerHTML',{
  get(){return this._html;},
  set(h){
    this._html=h;this.children=[];
    const rowRe=/<span class="(pvsubw(?: yel)?)" style="([^"]*)">([\s\S]*?)(?=<span class="pvsubw(?: yel)?"|$)/g;
    let m;
    while((m=rowRe.exec(h))!==null){
      const row=new El('span');row.className=m[1];row.styleStr=m[2];
      const wdRe=/<span class="(pvsubw_wd[^"]*)"(?: data-hl0="([^"]*)")?(?: data-hld="([^"]*)")? style="([^"]*)">([\s\S]*?)<\/span>/g;
      let w;
      while((w=wdRe.exec(m[3]))!==null){
        const el=new El('span');el.className=w[1];
        if(w[2]!==undefined)el.dataset.hl0=w[2];
        if(w[3]!==undefined)el.dataset.hld=w[3];
        el.styleStr=w[4];el.textContent=w[5];
        row.appendChild(el);
      }
      this.appendChild(row);
    }
  }});
El.prototype.appendChild=function(ch){this.children.push(ch);ch.parent=this;return ch;};
El.prototype.remove=function(){if(this.parent){const i=this.parent.children.indexOf(this);
  if(i>=0)this.parent.children.splice(i,1);this.parent=null;}};
El.prototype.querySelector=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  return this.children.find(c=>c._cls.has(want))||null;};
El.prototype.querySelectorAll=function(sel){
  const want=String(sel||'').replace(/^\./,'');
  const res=[];
  (function scan(el){for(const ch of el.children){if(ch._cls.has(want))res.push(ch);scan(ch);}})(this);
  return res;};

const subEl=new El('div');subEl.id='ipvsub';subEl.clientWidth=540;subEl.clientHeight=960;
global.$=id=>(id==='ipvsub'?subEl:null);
global.document={createElement:tag=>new El(tag)};
global.t=s=>s;
global.esc=s=>String(s==null?'':s);
global.rgb2hex=()=>'#fff';
global.ipvFontFor=()=>null;
global.FONTS=[];
global.CURSTYLE={};
global.IPVMODE='ae';
global.IPV={plan:null,xml:'clip.xml',vids:[{}],insShift:null,intro:[],cur:-1,introCur:-1};
global.uiLog=()=>{};
global.ipvPlanBody=()=>({});
global.ipvIntroGroups=()=>[];
global.sfxEnsure=()=>{};
global.itlDraw=()=>{};
global.ipvNow=()=>1.5;
function host(){return subEl.querySelector('.pvsubs_host');}
function row(){return host().querySelectorAll('.pvsubw')[0];}
"""


def _subs_plan(posy, fsize=None, fill=None, win=(0, 5)):
    """План сцены с ОДНИМ И ТЕМ ЖЕ словом: ключ показа (visKey) у A и B совпадает —
    именно на этом и ломался показ (правка стиля приходила новым планом, а слова те же)."""
    plan = {"w": 1080, "h": 1920, "posy": posy, "step": 0,
            "subs": [{"s": win[0], "gend": win[1], "w": "ПРИВЕТ", "row": 0,
                      "color": "white"}]}
    if fsize is not None:
        plan["fsize"] = fsize
    if fill is not None:
        plan["sub_fill"] = fill
    return plan


@node
def test_node_new_plan_repaints_subtitles_on_pause(tmp_path):
    """План A → показ A; пришёл план B (другой posy и кегль) на том же времени → DOM и
    CSS-переменные отражают B, хотя слово то же (сброс visKey в ipvPlanFetch).

    Числа: posy 900 при h 1920 → bottom 53.13 %, fsize 100 при w 1080 → 9.259cqw;
    план B: posy 700 → 63.54 %, fsize 140 → 12.963cqw. План C без fsize/sub_fill —
    переменные обязаны СНЯТЬСЯ, а не остаться от прошлого плана. План D с окном вдали
    от текущего времени — старые строки обязаны уйти с экрана (снятый ключ, а не пустая
    строка: пустая совпала бы с ключом невидимого плана).
    """
    view = _read(VIEW_JS)
    src = "\n".join([
        _slice(view, "function aeEase(", "function ipvIns("),
        _slice(view, "function ipvPlanSoon(", "// ---- SFX в предпросмотре"),
        _slice(view, "function ipvSubsInvalidate(", "function ipvUI(tm){"),
    ])
    plan_a = json.dumps(_subs_plan(900, 100, [1, 1, 1]), ensure_ascii=False)
    plans = [_subs_plan(700, 140, [1, 1, 1]),            # B: правка шрифта и высоты
             _subs_plan(640),                            # C: полей нет — переменные снять
             _subs_plan(640, win=(10, 15))]              # D: в этот момент слов не видно
    answers = ",".join("{ok:true,plan:%s}" % json.dumps(p, ensure_ascii=False) for p in plans)
    script = (_SUBS_DOM + "\nconst planA=@PLAN_A@;\n" + src + "\n"
              + "global.ipvUI=tm=>ipvSubs(tm);\n"          # пауза: показ идёт через ipvUI
              + "const answers=[%s];\n" % answers
              + r"""
global.fetch=()=>({json:()=>Promise.resolve(answers.shift())});

// Показ плана A — точка отсчёта.
IPV.plan=planA;
ipvSubs(1.5);
assert(/bottom:53\.13%/.test(row().styleStr), 'план A: низ строки ' + row().styleStr);
assert.strictEqual(subEl.props['--subfs'], '9.259cqw', 'план A: кегль ' + subEl.props['--subfs']);
assert.strictEqual(subEl.props['--subfc'], '#fff', 'план A: цвет не поставлен');

// Пришёл новый план: тот же путь, что в бою (ipvPlanFetch после правки стиля).
ipvPlanFetch().then(()=>{
  assert(/bottom:63\.54%/.test(row().styleStr),
    'новый план не перестроил строки на паузе: ' + row().styleStr);
  assert.strictEqual(subEl.props['--subfs'], '12.963cqw',
    'кегль остался от прошлого плана: ' + subEl.props['--subfs']);
  return ipvPlanFetch();
}).then(()=>{
  assert(/bottom:66\.67%/.test(row().styleStr),
    'третий план не перестроил строки: ' + row().styleStr);
  assert(!('--subfs' in subEl.props),
    'кегль прошлого плана остался, когда новый его не задаёт: ' + subEl.props['--subfs']);
  assert(!('--subfc' in subEl.props),
    'цвет прошлого плана остался, когда новый его не задаёт');
  return ipvPlanFetch();
}).then(()=>{
  assert.strictEqual(host().querySelectorAll('.pvsubw').length, 0,
    'строки прошлого плана остались на экране, хотя в этом плане слов не видно');
  console.log('OK: new plan repaints subtitles on pause');
}).catch(e=>{console.error((e&&e.stack)||e);process.exit(1);});
""").replace("@PLAN_A@", plan_a)
    res = _run_node(tmp_path, "test_md_subs_plan.js", script)
    assert res.returncode == 0, f"node упал: {(res.stderr or '') + (res.stdout or '')}"
    assert "OK: new plan repaints subtitles on pause" in res.stdout


def test_plan_fetch_resets_subtitle_show_cache():
    """Сторож: сброс кэша показа стоит именно в ветке нового плана ipvPlanFetch."""
    fetch = _slice(_read(VIEW_JS), "async function ipvPlanFetch(", "// ---- SFX в предпросмотре")
    assert "ipvSubsInvalidate()" in fetch, (
        "ipvPlanFetch не сбрасывает кэш показа субтитров — правка шрифта/высоты "
        "не будет видна, пока не сменится слово")
    assert fetch.index("ipvSubsInvalidate()") < fetch.index("ipvUI("), (
        "сброс кэша идёт после отрисовки — показ останется старым")
    subs = _slice(_read(VIEW_JS), "function ipvSubsInvalidate(", "function ipvSubs(tm){")
    assert "dataset.visKey" in subs, (
        "сбрасывается не тот ключ, по которому ipvSubs решает перестраивать строки")


# --------------------------------------------------------------------------- #
# 2. Драга субтитров нет
# --------------------------------------------------------------------------- #
def test_subtitle_drag_removed_from_source_and_css():
    """Субтитры не таскаются мышью (задание MD): высота — ползунок стиля, а строка
    не перехватывает клик по кадру. Вставки и интро драг сохраняют — граница задания."""
    js = _read(VIEW_JS)
    assert "$('ipvsub').addEventListener('pointerdown'" not in js, (
        "драг субтитров вернулся в предпросмотр")
    assert "CURSTYLE.sub_y=" not in js, "остался путь правки sub_y из драга субтитров"

    css = _read(CSS)
    # Правил с этим селектором два: геометрия строки и (уже без драга) приём указателя.
    rules = re.findall(r"#ipvsub \.pvsubw\{([^}]*)\}", css)
    assert rules, "нет правила #ipvsub .pvsubw в app.css"
    joined = " ".join(rules)
    assert "ns-resize" not in joined, "у субтитров остался курсор драга: " + joined
    assert "pointer-events:auto" not in joined, (
        "субтитры снова перехватывают клики: " + joined)
    assert "pointer-events:none" in joined, "субтитры не отдают клик кадру: " + joined

    assert "$('ipvintro').addEventListener('pointerdown'" in js, (
        "задание MD не трогало драг интро — обработчик пропал")
    assert "$('ipvins').addEventListener('pointerdown'" in js, (
        "задание MD не трогало драг вставок — обработчик пропал")


# --------------------------------------------------------------------------- #
# 3. Панель стиля в превью прокручивается
# --------------------------------------------------------------------------- #
def test_style_panel_scrolls_inside_preview_tab():
    """Раскрытых групп много — они уходили за нижний край, потому что у общей панели
    `overflow:visible !important`. В превью панель — прокручиваемая область, строка
    «Сохранить» остаётся видимой внизу; панель на главной странице не тронута."""
    css = _read(CSS)
    m = re.search(r"#aewstyle #stpanel\{([^}]*)\}", css)
    assert m, "нет правила #aewstyle #stpanel в app.css"
    rule = m.group(1)
    assert re.search(r"overflow:\s*(auto|overlay)", rule), (
        "панель стиля в превью не прокручивается: " + rule)
    assert "!important" in rule, (
        "без !important точечное правило проиграет .stpanel{overflow:visible !important}")
    assert "min-height:0" in rule, "у панели нет min-height:0 — flex не даст ей сжаться"
    assert "flex:1" in rule, "панель не забирает свободную высоту колонки"

    save = re.search(r"#aewstyle #styleSaveRow\{([^}]*)\}", css)
    assert save and "flex:none" in save.group(1), (
        "строка «Сохранить» не закреплена — её выдавит прокручиваемой панелью")

    base = re.search(r"\.stpanel\{([^}]*)\}", css)
    assert base and "overflow:visible" in base.group(1), (
        "панель на главной странице обязана остаться как была (прокручивается страница)")


# --------------------------------------------------------------------------- #
# 4. Шаг 1: заголовок — имя файла, у подписей нет хвостов
# --------------------------------------------------------------------------- #
def test_step1_markup_has_no_label_tails():
    """В шапке шага 1 — имя открытого файла, а не название окна; подписи — «Монтаж» и
    «Редактор» (тултип «i» у редактора остался)."""
    html = _read(HTML)
    assert " — как будет в ролике" not in html, "у подписи «Монтаж» остался хвост"
    assert " — исходник камеры 1" not in html, "у подписи «Редактор» остался хвост"
    assert 'class="edlbl">Монтаж</div>' in html, "подпись монтажа не «Монтаж»"
    assert '<div class="edlbl">Редактор <span class="i" data-t="Клик — курсор' in html, (
        "подпись редактора потеряла тултип «i» или текст")
    assert re.search(r'class="t" id="mbPvTitle"', html), (
        "в шапке шага 1 нет элемента под имя открытого файла")
    assert "Предпросмотр и правка нарезки</span>" not in html, (
        "в шапке остался статический заголовок вместо имени файла")


@node
def test_node_step1_title_is_the_open_clip_name(tmp_path):
    """Заголовок и aria-label шага 1 заполняются именем файла из списка клипов —
    боевыми pvTitle/openEditClip и боевым clipLabel (своего разбора имени нет)."""
    prev = _read(PREV_JS)
    clip = next(l for l in _read(QUEUE_JS).splitlines() if l.startswith("function clipLabel("))
    idx = prev.index("async function openEditClip(")
    open_edit = prev[idx:prev.index("\n", prev.index("edOpen();}", idx))]
    script = r"""
const assert = require('assert');
function El(tag){this.tagName=String(tag).toUpperCase();this._text='';
  this.children=[];this.dataset={};this.style={setProperty(){},removeProperty(){}};
  const self=this;this.classList={add(){},remove(){},toggle(){},contains:()=>false};}
Object.defineProperty(El.prototype,'textContent',
  {get(){return this._text;},set(v){this._text=''+(v==null?'':v);}});
const dlg={attrs:{},setAttribute(k,v){this.attrs[k]=v;}};
const ttl=new El('span');
ttl.closest=sel=>(sel==='.modal'?dlg:null);
global.$=id=>(id==='mbPvTitle'?ttl:null);
global.CLIPS=[{xml:'C:\\reels\\clip7.xml',name:'clip7.xml'},{xml:'C:\\reels\\clip8.xml',name:''}];
var opened='';
global.openModal=id=>{opened=id;};
global.openPreview=async()=>{};
global.edOpen=()=>{};
var curEdit=-1;
""" + clip + "\n" + _slice(prev, "function pvTitle(", "async function openEditClip(") + "\n" + open_edit + r"""

// 1. Имя из списка клипов (c.name) — то, что видно в очереди.
pvTitle(clipLabel(CLIPS[0]));
assert.strictEqual(ttl.textContent,'clip7.xml','заголовок не имя файла: '+ttl.textContent);
assert.strictEqual(dlg.attrs['aria-label'],'clip7.xml','aria-label не имя файла');

// 2. Боевой вход шага 1 ставит то же имя (имя клипа без name берётся из пути XML).
openEditClip(1).then(()=>{
  assert.strictEqual(ttl.textContent,'clip8.xml',
    'openEditClip не заполнил заголовок именем файла: '+ttl.textContent);
  assert.strictEqual(dlg.attrs['aria-label'],'clip8.xml','aria-label не обновился');
  assert.strictEqual(opened,'mbPreview','модалка шага 1 не открылась');
  console.log('OK: step 1 title is the open clip name');
}).catch(e=>{console.error((e&&e.stack)||e);process.exit(1);});
"""
    res = _run_node(tmp_path, "test_md_step1_title.js", script)
    assert res.returncode == 0, f"node упал: {(res.stderr or '') + (res.stdout or '')}"
    assert "OK: step 1 title is the open clip name" in res.stdout


# --------------------------------------------------------------------------- #
# 5. Прогресс сборки прокси
# --------------------------------------------------------------------------- #
def test_ff_progress_lines_give_percent():
    """Разбор потока `ffmpeg -progress pipe:1`: out_time_us против длительности исходника.

    ffmpeg печатает блок полей раз в 0.5 с — берём последний (он и есть текущее
    положение), `N/A` на первых кадрах не ошибка, длительность неизвестна — процента нет."""
    from core import draftrender

    assert draftrender.ff_progress_us("out_time_us=25000000\nprogress=continue\n") == 25000000
    assert draftrender.ff_progress_pct("out_time_us=25000000\nprogress=continue\n", 50) == 50.0

    chunk = ("frame=1\nout_time_us=1000000\nprogress=continue\n"
             "frame=2\nout_time_us=N/A\nprogress=continue\n")
    assert draftrender.ff_progress_pct(chunk, 10) == 10.0, "взят не последний out_time_us"
    assert draftrender.ff_progress_pct("out_time_us=1000000\n", 0) is None, (
        "без длительности исходника процент не должен считаться")
    assert draftrender.ff_progress_pct("progress=continue\n", 10) is None, (
        "без строк out_time_us процента нет")
    assert draftrender.ff_progress_pct("out_time_us=99000000\n", 10) == 100.0, (
        "процент выше 100 не обрезан")


def test_preview_proxy_status_returns_pct(monkeypatch, tmp_path):
    """`/api/preview_proxy_status` отдаёт pct текущего файла: сборщик прокидывает проценты
    из ffmpeg (progress=build_preview_proxy) в PXJOB."""
    import time

    import webui
    from api import previewproxy
    from core import draftrender

    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(previewproxy, "_preview_proxy_plan",
                        lambda p, h: ([("/path/cam1.mp4", "/path/pv.mp4", False)], str(tmp_path)))

    seen = []
    during = {}

    def fake_build(src, dst, height=720, emit=None, progress=None):
        seen.append(progress is not None)
        with previewproxy.PXLOCK:   # что видит фронт, пока файл собирается
            during.update(i=previewproxy.PXJOB["i"], n=previewproxy.PXJOB["n"],
                          cur=previewproxy.PXJOB["cur"])
        if progress:
            progress(37.4)          # ffmpeg «доехал» до 37 %

    monkeypatch.setattr(draftrender, "build_preview_proxy", fake_build)

    client = webui.app.test_client()
    r = client.post("/api/preview_proxy", json={"xml": str(xml), "build": True})
    assert r.status_code == 200 and r.get_json().get("ok") is True
    for _ in range(50):
        with previewproxy.PXLOCK:
            if not previewproxy.PXJOB["running"]:
                break
        time.sleep(0.05)

    assert seen == [True], "сборщик не передал приём процентов в build_preview_proxy"
    assert during == {"i": 1, "n": 1, "cur": "cam1.mp4"}, (
        f"номер/имя текущего файла не дошли до статуса: {during}")
    st = client.get("/api/preview_proxy_status",
                    headers={"Host": "127.0.0.1:5001"}).get_json()
    assert st.get("pct") == 37, f"в статусе нет процента: {st}"


def test_proxy_progress_block_over_the_player():
    """Блок прогресса поверх плеера: полоса, «Готовлю прокси камеры {i}/{n}: {файл} — {pct} %»
    и подсказка «один раз на файл, дальше из кэша»; блок показывают все три плеера, которые
    ждут прокси, а прежний хвост в подписи камер убран."""
    prev = _read(PREV_JS)
    block = _slice(prev, "function pvProxyBlock(", "function pvProxyWatch(")
    assert "pvpx_fill" in block and "width=pct+'%'" in block, (
        "в блоке нет полосы прогресса от pct")
    assert "Готовлю прокси камеры {i}/{n}: {file} — {pct} %" in block, "нет строки прогресса"
    assert "один раз на файл, дальше из кэша" in block, "нет подсказки про кэш"
    assert "pvpx" in _read(CSS), "в app.css нет стилей блока .pvpx"
    assert "pvProxyNote" not in prev, "хвост «— прокси камер: n/m» в подписи камер остался"
    assert "pointer-events:none" in _slice(_read(CSS), ".pvpx{", "}"), (
        "блок перехватывает клики по кадру")

    watch = _slice(prev, "function pvProxyWatch(", "async function pvProxyPoll(")
    assert "pvProxyPoll()" in watch, "статус не опрашивается сразу — блок появится с задержкой"
    for path, stage in ((VIEW_JS, "ipvstage"), (PREV_JS, "pvstage"),
                        (os.path.join(APP, "88-cams.js"), "cpvstage")):
        assert f"pvProxyWatch('{stage}')" in _read(path), (
            f"плеер {stage} не показывает прогресс сборки прокси")


# --------------------------------------------------------------------------- #
# 6. `_run_ff`: прогресс от ЖИВОГО процесса и отмена (подмены subprocess нет)
# --------------------------------------------------------------------------- #
# Печатает ровно то, что печатает ffmpeg с `-progress pipe:1`: строку `out_time_us=…`
# раз в 0.6 с, с flush. Заодно валит в stderr 100 КБ — больше пайпа: не вычитай stderr,
# и процесс встанет на записи в него. Никакой подмены subprocess: дефект приёмки был
# именно в том, что подменённый ffmpeg отдавал вывод, а боевой — нет.
_FF_PROGRESS_CHILD = (
    "import sys, time\n"
    "sys.stderr.write('\\n'.join(['e' * 1000] * 100) + '\\n')\n"
    "sys.stderr.flush()\n"
    "for us in (0, 1000000, 2000000, 3000000):\n"
    "    sys.stdout.write('out_time_us=%d\\n' % us)\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.6)\n"
)

# Отмечается файлом-тиком каждые 0.2 с — по нему и видно, жив процесс после отмены.
_FF_TICK_CHILD = (
    "import sys, time\n"
    "path = sys.argv[1]\n"
    "for i in range(1, 121):\n"          # 24 с — заведомо дольше отмены
    "    with open(path, 'w') as f:\n"
    "        f.write(str(i))\n"
    "    time.sleep(0.2)\n"
)


def test_run_ff_progress_comes_from_the_live_process():
    """Прогресс обязан приходить ОТ ЖИВОГО процесса, а не из `TimeoutExpired.output`.

    Дефект приёмки: прогресс брался из `ex.output` исключения `TimeoutExpired` от
    `communicate(timeout=1.0)`, а на Windows CPython ветка Windows кладёт туда None —
    на машине владельца процента прокси не было бы никогда. Стенды с подменённым
    ffmpeg этого не видели: подменённый отдаёт вывод как ему удобно.

    Требуем минимум два вызова приёма ДО завершения процесса (время вызова против
    общего времени работы), роста значений, целого stdout/stderr и последнего
    `out_time_us=3000000` — то есть вывод дочитан до конца, а не оборван."""
    from core import draftrender

    calls = []
    t0 = time.monotonic()
    r = draftrender._run_ff([sys.executable, "-c", _FF_PROGRESS_CHILD],
                            on_progress=lambda text: calls.append(
                                (time.monotonic() - t0, draftrender.ff_progress_us(text))),
                            timeout=60)
    total = time.monotonic() - t0

    assert r.returncode == 0, "подпроцесс упал: " + (r.stderr or "")[:400]
    assert r.stdout.count("out_time_us=") == 4, "stdout собран не целиком: " + repr(r.stdout)
    assert len(r.stderr) >= 100000, (
        f"stderr не вычитан ({len(r.stderr)} симв.) — полный пайп остановил бы ffmpeg")

    us = [u for _at, u in calls]
    assert len(us) >= 2, f"приём прогресса не позван на живом процессе: {calls}"
    assert all(b > a for a, b in zip(us, us[1:])), f"значения не растут: {us}"
    assert us[-1] == 3000000, f"вывод процесса дочитан не до конца: {us}"
    assert calls[1][0] < total - 0.3, (
        f"приём позван только на завершении процесса: {calls} при {total:.2f} с работы")


def test_run_ff_cancel_kills_the_live_process(tmp_path):
    """Отмена посреди работы: `_run_ff` возвращает None, а процесс ДЕЙСТВИТЕЛЬНО убит.

    Живой процесс отмечается файлом-тиком каждые 0.2 с: после отмены файл обязан
    замереть — иначе «отмена» лишь бросает процесс, а он продолжает писать в _tmp."""
    from core import draftrender

    tick = tmp_path / "ticks.txt"
    t0 = time.monotonic()
    r = draftrender._run_ff([sys.executable, "-c", _FF_TICK_CHILD, str(tick)],
                            cancel=lambda: time.monotonic() - t0 > 0.6, timeout=60)
    assert r is None, f"отмена не вернула None: {r and (r.returncode, r.stderr[:200])}"
    assert tick.exists(), "подпроцесс не успел отметиться — отменять было нечего"
    mark = tick.read_text(encoding="utf-8")
    time.sleep(1.2)
    assert tick.read_text(encoding="utf-8") == mark, (
        "после отмены процесс жив: тики продолжаются")
    assert time.monotonic() - t0 < 10, "отмена ждала слишком долго"
