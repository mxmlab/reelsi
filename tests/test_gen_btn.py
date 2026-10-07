# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Панель над превью вставок: пять кнопок, иконки вместо слов.

В панели остались только те действия, по которым владелец реально работает:
генерация недостающих картинок (иконкой со счётом), «+ фото», «+ видео», отмена и
повтор правок вставок. Разметка вставок ИИ запускается и останавливается в других
местах — в шапке шага 2 и у клипа, — поэтому кнопок «Подобрать заново (все)»,
«Добрать недостающие», «База» и их «Стоп» в этом ряду быть не должно: сторож ниже
следит, чтобы они не вернулись (три действия из четырёх дублировали соседние кнопки,
а «Стоп» останавливал бы то, что отсюда уже не запускается).

Ломается это тихо и не видно ни одним тестом бэкенда, поэтому стережётся
регулярками по тексту файлов:

1. состав ряда: ровно пять кнопок, у каждой свой обработчик;
2. класс: выделение «Недостающих» — положение (первая кнопка ряда) и чуть более
   толстая рамка (button.key: border-width 2px), а не другой стиль: акцентная
   белая заливка .primary выбивала кнопку из ряда соседей; иконка при этом
   золотая, как у соседних ИИ-кнопок — на прозрачной заливке .ic.gold читается;
3. число: считается по ТОЙ ЖЕ выборке, что insGenBatch, иначе кнопка обещает не
   то, что сделает. Именно ask=true: явный клик по кнопке генерит и снятые руками
   карточки — в выборке батча `(ask||!x.noAuto)` при ask=true истинно всегда,
   поэтому noAuto в счёте на кнопке стоять не должен;
4. ноль: генерить нечего — кнопка disabled, а не молчит на клик;
5. имя: слова у кнопки больше нет, поэтому у неё обязаны быть aria-label и data-t,
   причём число карточек — в самой подсказке (иначе «сгенерировать недостающие» не
   говорит, о скольких речь);
6. иконка: <span data-ic> к моменту рендера уже заменён на <svg> (99-boot), и
   перерисовка подписи не смеет трогать innerHTML кнопки — иначе иконка исчезает.

Запуск:  py -3.10 -m pytest tests/test_gen_btn.py -q
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

HTML = os.path.join(ROOT, "templates", "index.html")
INSERTS_JS = os.path.join(ROOT, "static", "app", "80-inserts.js")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")
CSS = os.path.join(ROOT, "static", "app.css")
# Три условия выборки insGenBatch, которые обязаны стоять и в счёте на кнопке.
BATCH_CONDITIONS = ("x.type!=='video'", "!x.media", "(x.query||'').trim()")
# Слова, которых в этом ряду быть не должно: их действия живут в других местах.
GONE = ("aiInsertsRun", "aiInsertsMore", "openInsLib", "insStop")
UNDO_TIP = "Отменить последнюю правку вставок (Ctrl+Z)"
REDO_TIP = "Повторить отменённую правку вставок (Ctrl+Shift+Z или Ctrl+Y)"
GEN_TIP = "Сгенерировать картинки для карточек без файла ({n})"


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _btn():
    """Кнопка insGenBtn целиком: иконка и счёт внутри неё, а не только атрибуты."""
    m = re.search(r'<button\b[^>]*\bid="insGenBtn".*?</button>', _read(HTML), re.S)
    assert m, "в index.html пропала кнопка insGenBtn"
    return m.group(0)


def _inserts_row():
    """Ряд кнопок панели вставок (#inspart_inserts .row): в нём insGenBtn — первая."""
    html = _read(HTML)
    block = html[html.index('id="inspart_inserts"'):]
    block = block[:block.index('id="insHost"')]
    m = re.search(r'<div class="row"[^>]*>(.*?)</div>', block, re.S)
    assert m, "в #inspart_inserts пропал ряд кнопок"
    return m.group(1)


def _render_block():
    """Блок кнопки в renderInsHost: от её id до выхода «клип не открыт»."""
    js = _read(INSERTS_JS)
    i = js.index("$('insGenBtn')")
    return js[i:js.index("if(curIns<0)return;", i)]


def _batch_need():
    """Строка выборки из insGenBatch — эталон, с которым сверяется счёт на кнопке."""
    js = _read(SETTINGS_JS)
    i = js.index("const need=", js.index("async function insGenBatch("))
    return js[i:js.index("\n", i)]


def _func(src, name):
    """Вырезать `function name(...){...}` целиком по балансу скобок."""
    m = re.search(r"function\s+%s\s*\(" % re.escape(name), src)
    assert m, "в исходнике не нашлась функция %s" % name
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


def _run_node(script):
    # node на Windows пишет в пайп UTF-8 (с BOM для кириллицы), а locale-декодирование
    # (cp1251) превращает слова в «?» и роняет json.loads — декодируем явно utf-8-sig
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr.strip()[:400]
    return json.loads(p.stdout)


def test_panel_keeps_exactly_five_buttons():
    """В ряду панели — ровно пять кнопок, и ни одной из убранных.

    «Подобрать заново (все)», «Добрать недостающие», «База» и «Стоп» дублировали
    действия, которые запускаются в других местах (шапка шага 2, карточка клипа,
    кнопка «Обновить базу» в инструментах), а «Стоп» останавливал бы группу,
    которая отсюда уже не стартует. Отмена и повтор правок вставок — свои кнопки.
    """
    row = _inserts_row()
    for gone in GONE:
        assert gone not in row, "в панель вставок вернулась убранная кнопка/группа: " + gone
    buttons = re.findall(r"<button\b[^>]*>", row)
    assert len(buttons) == 5, "в панели вставок не пять кнопок: %d" % len(buttons)
    handlers = [re.search(r'onclick="([^"]*)"', b).group(1) for b in buttons]
    assert handlers == ["insGenMissing()", "insAdd('photo')", "insAdd('video')",
                        "insUndo()", "insRedo()"], handlers
    # Отмена и повтор прижаты вправо — распоркой, как в остальных рядах интерфейса.
    assert '<span class="grow"></span>' in row[:row.index('id="insUndoBtn"')], (
        "кнопка отмены не прижата вправо — распорка .grow пропала")
    assert 'id="insGenTxt"' not in row, "вернулась подпись «Недостающие» — у кнопки остался только счёт"
    assert 'insGenTxt' not in _read(INSERTS_JS), "в JS остался мёртвый id подписи insGenTxt"


def test_gen_button_is_first_in_row_and_key_class():
    """Кнопка первая в ряду, в классе key и без primary — выделение положением и рамкой."""
    btn = _btn()
    attrs = btn[:btn.index(">")]
    m = re.search(r'class="([^"]*)"', attrs)
    assert m, "у кнопки insGenBtn пропал класс"
    cls = m.group(1).split()
    assert "key" in cls, "кнопка генерации потеряла класс key: " + attrs[:120]
    assert "primary" not in cls, (
        "акцентная белая заливка .primary выбивает кнопку из ряда соседей — выделять "
        "её надо положением (первая в ряду) и толщиной рамки")
    assert "sm" in cls, "у кнопки потерян компактный размер sm: " + attrs[:120]
    assert 'data-t="' in attrs, "у кнопки пропала подсказка data-t"
    assert 'aria-label="' in attrs, "у кнопки без слова обязано быть имя для скринридера"
    assert 'data-ic="ai"' in btn and 'data-cls="gold"' in btn, (
        "иконка кнопки обязана быть золотой, как у соседних ИИ-кнопок")
    # Слова у кнопки нет: остались иконка генерации и счёт.
    assert "Недостающие" not in btn, "с кнопки генерации не убрали слово «Недостающие»"
    assert 'id="insGenCnt"' in btn, "у кнопки генерации пропал счётчик"
    first = re.search(r"<button\b[^>]*", _inserts_row())
    assert first and 'id="insGenBtn"' in first.group(0), (
        "кнопка генерации больше не первая в ряду — выделение положением потеряно")


def test_key_button_is_border_width_only():
    """button.key меняет только толщину рамки: цвет и заливка — как у обычной кнопки."""
    m = re.search(r"button\.key\s*\{([^}]*)\}", _read(CSS))
    assert m, "в static/app.css пропало правило button.key"
    body = m.group(1)
    assert "border-width:2px" in body, "button.key не задаёт рамку 2px: " + body
    assert "background" not in body and "border-color" not in body, (
        "button.key перекрашивает кнопку — выделять её должны положение и толщина рамки")


def test_gen_button_counts_what_the_batch_will_generate():
    """Число на кнопке — по выборке insGenBatch(ask=true), для открытого клипа."""
    block = _render_block()
    batch = _batch_need()
    assert "CLIPS[curIns]" in block, "счёт берётся не с открытого клипа"
    for cond in BATCH_CONDITIONS:
        assert cond in block, "кнопка считает не то, что генерит батч: нет " + cond
        assert cond in batch, "выборка insGenBatch разошлась с условием " + cond
    assert "noAuto" not in block, (
        "явный клик по кнопке генерит и снятые руками карточки (в батче ask=true) — "
        "выкидывать их из счёта значит обещать меньше, чем сделаешь")


def test_gen_button_shows_the_number_and_goes_disabled_at_zero():
    """Счёт перерисовывается и уезжает в подсказку, ноль — кнопка гаснет, иконка цела."""
    block = _render_block()
    assert "$('insGenCnt')" in block, "счёт кнопки не перерисовывается"
    assert "cnt.textContent=need?String(need):''" in block, (
        "число обязано быть отдельным текстовым узлом рядом с иконкой "
        "(t('…({n})') остаётся только в подсказке)")
    assert GEN_TIP in block and "{n:need}" in block, (
        "число карточек пропало из подсказки: без слова на кнопке оно там и читается")
    assert "gb.disabled=!need" in block, "ноль карточек — кнопка не гаснет"
    assert "gb.innerHTML" not in block, (
        "текст кнопки переписывается через innerHTML — <svg>-иконка при этом теряется")


def test_gen_button_visibility_keeps_the_generation_profile_check():
    """Показ кнопки остался привязан к включённому профилю генерации картинок."""
    block = _render_block()
    assert "imgGenOn()" in block and "gb.style.display=" in block, (
        "кнопка показывается/прячется не по imgGenOn()")


# ── стенд на node: живая разметка ряда + НАСТОЯЩИЙ renderInsHost ──────────────
# Регулярки выше видят текст, а не поведение: кнопка могла бы остаться с числом, но
# не гаснуть, или счёт мог бы считаться по другому клипу. Здесь ряд собирается из
# настоящей разметки index.html, а счёт рисует боевая функция из 80-inserts.js —
# на клипе, где недостающих карточек ровно две.
NODE_STAND = r"""
const ROW = __ROW__;

// ── мини-DOM: ровно то, чего касается renderInsHost ───────────────────────────
function attrs(open){
  const a={};
  open.replace(/([\w:-]+)="([^"]*)"/g,(m,k,v)=>{a[k]=v;return m;});
  return a;
}
const parsed=(ROW.match(/<button\b[\s\S]*?<\/button>/g)||[]).map(full=>{
  const open=full.slice(0,full.indexOf('>')+1);
  const a=attrs(open);
  a.__inner=full.slice(open.length,full.lastIndexOf('</button>'));
  a.__text=a.__inner.replace(/<[^>]*>/g,'').trim();
  a.__ic=(full.match(/data-ic="([^"]+)"/)||[])[1]||'';
  return a;
});
function node(a){
  const el={attrs:a||{},dataset:{},style:{},className:'',innerHTML:'',textContent:'',
    disabled:false,children:[],
    classList:{add(){},remove(){},contains(){return false;}},
    appendChild(c){this.children.push(c);}};
  for(const k in el.attrs){
    if(k.indexOf('data-')===0)el.dataset[k.slice(5).replace(/-(\w)/g,(m,c)=>c.toUpperCase())]=el.attrs[k];
  }
  return el;
}
const byId={};
(ROW.match(/<[a-z]+[^>]*\bid="[^"]+"/g)||[]).forEach(open=>{
  const a=attrs(open);byId[a.id]=node(a);
});
byId['insHost']=node({id:'insHost'});
const $=id=>byId[id]||null;

// ── заглушки соседей: проверяем счёт кнопки, а не сборку карточек ─────────────
const t=(s,v)=>{let o=s;if(v)for(const k in v)o=o.split('{'+k+'}').join(String(v[k]));return o;};
function ico(){return '<svg class="ic"></svg>';}
function esc(s){return String(s==null?'':s);}
function fmtIns(s){return String(s);}
function scrubXY(){return '';}function scrubScale(){return '';}
function scrubSin(){return '';}function scrubMask(){return '';}
function insScrubInit(){}function ipvMarks(){}function ipvRefresh(){}
function imgGenOn(){return true;}function vidGenOn(){return true;}
function insGenBtns(){return '';}function val(){return '';}
const document={createElement:()=>node({})};
const CURSTYLE=null;
const CLIPS=[{inserts:[
  {type:'photo',start_sec:1,query:'sunset',media:''},
  {type:'photo',start_sec:3,query:'city',media:''},
  {type:'photo',start_sec:5,query:'',media:''},
  {type:'photo',start_sec:7,query:'cat',media:'a.png'},
  {type:'video',start_sec:9,query:'sea',media:''}]}];
let curIns=0;

__RENDER__

const gb=byId['insGenBtn'],cnt=byId['insGenCnt'];
const shot=()=>({cnt:cnt.textContent,disabled:!!gb.disabled,tip:gb.dataset.t});
renderInsHost();
const full=shot();                                  // две карточки без файла с запросом
CLIPS[0].inserts.forEach(x=>{if(x.type==='photo')x.media='a.png';});
renderInsHost();                                    // файлы появились — генерить нечего
const empty=shot();
console.log(JSON.stringify({
  buttons:parsed.length,
  handlers:parsed.map(b=>b.onclick),
  icons:parsed.map(b=>b.__ic),
  texts:parsed.map(b=>b.__text),
  iconOnly:parsed.filter(b=>!b.__text).map(b=>({aria:b['aria-label']||'',tip:b['data-t']||''})),
  first:parsed.length?parsed[0].id:'',
  last:parsed.length?parsed[parsed.length-1].id:'',
  full:full,empty:empty}));
"""


def _render_panel():
    """Ряд из index.html + боевой renderInsHost из 80-inserts.js на node."""
    script = NODE_STAND.replace("__ROW__", json.dumps(_inserts_row())) \
                       .replace("__RENDER__", _func(_read(INSERTS_JS), "renderInsHost"))
    return _run_node(script)


def test_panel_renders_five_buttons_with_names_and_a_live_count():
    """После отрисовки панели: пять кнопок, у иконочных — имя и справка, счёт живой."""
    out = _render_panel()
    assert out["buttons"] == 5, "после отрисовки панели кнопок не пять: %r" % out["handlers"]
    assert out["handlers"] == ["insGenMissing()", "insAdd('photo')", "insAdd('video')",
                               "insUndo()", "insRedo()"], (out["handlers"])
    assert out["icons"] == ["ai", "plus", "plus", "undo", "refresh"], out["icons"]
    assert out["texts"] == ["", "фото", "видео", "", ""], out["texts"]
    assert out["first"] == "insGenBtn" and out["last"] == "insRedoBtn", (
        "генерация обязана быть первой, повтор — последним (отмена и повтор прижаты вправо): %r" % out)
    assert out["iconOnly"] == [
        {"aria": "Сгенерировать картинки для карточек без файла",
         "tip": "Сгенерировать картинки для карточек без файла ({n})"},
        {"aria": "Отменить правку вставок", "tip": UNDO_TIP},
        {"aria": "Повторить правку вставок", "tip": REDO_TIP},
    ], ("у кнопок-иконок должны быть имя и подсказка: %r" % out["iconOnly"])
    # Клип с двумя фото-карточками без файла (третья без запроса, четвёртая с файлом,
    # пятая видео — не считаются): число, подсказка и активность кнопки.
    assert out["full"] == {"cnt": "2", "disabled": False,
                           "tip": "Сгенерировать картинки для карточек без файла (2)"}, out["full"]
    # Файлы появились — генерить нечего: счёт пуст, кнопка гаснет, подсказка честная.
    assert out["empty"] == {"cnt": "", "disabled": True,
                            "tip": "Сгенерировать картинки для карточек без файла (0)"}, out["empty"]
