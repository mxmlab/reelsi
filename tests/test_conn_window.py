# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно «Настройки» → «Подключения»: форма профиля по утверждённому макету.

Что было и почему переделано (скриншот владельца): большие круглые кнопки вперемешку
с квадратной, «Одновременных запросов» и «Доп. заголовки» на виду, колонка уходила
вниз за край модалки — до «Сохранить» приходилось долистывать всю форму, а список
профилей и форма стояли в одной колонке друг под другом.

Как стало:

- список профилей — колонкой слева, форма — справа;
- тело формы (`.conn-body`) прокручивается ВНУТРИ, подвал (`.conn-foot`) закреплён
  внизу колонки и виден всегда: панель вкладки не прокручивается целиком
  (`#aistab_models{display:flex;flex-direction:column;overflow:hidden}`), а высоты
  идут цепочкой с `min-height:0` — иначе flex-элемент не сжимается меньше
  содержимого и подвал уезжает вниз;
- подписи — узкой колонкой слева (`.conn-row` — сетка `92px minmax(0,1fr)`), поля
  сжимаются, а не распирают форму за правый край окна;
- кнопки одной формы и прямоугольные (`var(--r)`, без «пилюль»), главная одна —
  «Сохранить»; значки — с `data-t` и `aria-label`;
- «Проверить» пишет результат РЯДОМ с кнопкой (`#connTestRes`), а не тостом, и
  гаснет, как только тронули поля;
- редкие ручки (заголовки, одновременные запросы) убраны в свёрнутое «Дополнительно»;
- адрес спрашивается только у провайдера «свой URL» — у остальных он известен.

Форма разметки и сохранение данных не менялись: `aiSetForm` шлёт те же поля
(`provider`, `base_url`, `api_key`, `headers_text`, `model`, `concurrency`), тело
запроса к `/api/ai_config` — прежнего формата.

Числа раскладки берутся ИЗ CSS и разметки, а не из головы: ширина колонки профилей,
подписей, отступы и потолок модалки вычитаются из окна 1366×768 и 1920×1080 —
«ни одного элемента правее окна» проверяется арифметикой, а не глазом.

Поведение формы проверяется на БОЕВОМ `static/app/10-settings.js` под node (мини-DOM
из `tests/test_style_panel_js.py`): выбор профиля, признак несохранённых правок,
«Отмена», «Проверить» с результатом у кнопки и тело запроса при сохранении.

Мутация: убрать `flex:none` из правила `.conn-foot` — падает
`test_body_scrolls_and_the_footer_stays_pinned` (подвал начинает сжиматься и уезжает
вместе с прокруткой).

Запуск: py -3.10 -m pytest tests/test_conn_window.py -q
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
sys.path.insert(0, HERE)

from test_style_panel_js import DOM_STUB  # noqa: E402

HTML = os.path.join(ROOT, "templates", "index.html")
CSS = os.path.join(ROOT, "static", "app.css")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")
CORE_JS = os.path.join(ROOT, "static", "app", "00-core.js")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _rule(css: str, selector: str) -> str:
    """Тело правила по селектору без пробелов (как в соседних CSS-тестах)."""
    m = re.search(re.escape(selector) + r"\{([^}]+)\}", css)
    assert m, "в app.css нет правила %s" % selector
    return m.group(1)


def _px(css: str, selector: str, prop: str) -> int:
    """Число пикселей из свойства правила: отступы, промежутки, ширина подписи."""
    rule = _rule(css, selector)
    m = re.search(re.escape(prop) + r":\s*(\d+)px", rule)
    assert m, "в правиле %s не нашлось %s: %s" % (selector, prop, rule)
    return int(m.group(1))


def _basis(css: str, selector: str) -> int:
    """Ширина колонки из `flex:0 0 Npx` — так заданы колонка вкладок и список профилей."""
    rule = _rule(css, selector)
    m = re.search(r"flex:0 0 (\d+)px", rule)
    assert m, "колонка %s больше не фиксированной ширины: %s" % (selector, rule)
    return int(m.group(1))


def _fn(src: str, marker: str) -> str:
    """Тело функции от `marker` до парной закрывающей скобки."""
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


def _models_tab(html: str) -> str:
    """Кусок разметки вкладки «Подключения» — от её id до закрывающего комментария."""
    start = html.index('id="aistab_models"')
    end = html.index("/aistab_models", start)
    return html[start:end]


def _run_node(tmp_path: Any, name: str, body: str) -> Any:
    """Стенд под node: мини-DOM + боевой 10-settings.js + сценарий."""
    path = str(tmp_path / name)
    src = (DOM_STUB + _browser_globals() + _form_dom() + _read(SETTINGS_JS) + "\n" + body)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(src)
    p = subprocess.run(["node", path], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=120)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    lines = [x.strip() for x in p.stdout.strip().splitlines() if x.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


def _browser_globals() -> str:
    """Мелочи из 00-core.js: те же по смыслу, что в браузере.

    `t` подставляет `{n}` — подпись «✓ работает · {ms} мс» без подстановки не
    проверить: миллисекунды в ней и есть результат. `toast` копит вызовы: стенд
    доказывает, что результат проверки уходит в поле, а не во всплывашку.
    """
    return r"""
const TOASTS=[];
global.toast=(s)=>{TOASTS.push(String(s));};
global.t=(s,p)=>String(s).replace(/\{(\w+)\}/g,(m,k)=>(p&&p[k]!=null)?String(p[k]):m);
global.errText=(e)=>(e&&e.error)?String(e.error):String(e);
global.$=(id)=>document.getElementById(id);
global.val=(id)=>{const el=$(id);return el?el.value:'';};
__ESC__
""".replace("__ESC__", _fn(_read(CORE_JS), "function esc("))


def _form_dom() -> str:
    """Узлы формы — те же id, что находит боевой код в index.html.

    Мини-DOM отдаёт null на id, которого никто не создал (как браузер), поэтому
    список ведётся по коду: `aisList`, поля профиля и подвал `#connDirty`/`#aisStatus`.
    `value` у полей приводится к строке, как в браузере: профиль хранит
    `concurrency: 2` числом, и без приведения `val('ais_concurrency').trim()` падал бы
    там, где в браузере работает.
    """
    return r"""
resetDom();
function mk(tag,id){
  const e=document.createElement(tag);
  if(id)e.id=id;
  let v='';
  Object.defineProperty(e,'value',{get:()=>v,set:(x)=>{v=(x==null?'':String(x));},configurable:true});
  document.body.appendChild(e);
  return e;
}
['aisList','ais_name','ais_provider','ais_url','ais_key','ais_headers','ais_model',
 'ais_concurrency','aisDel','aisClone','aisActive','ais_key_warn','aisStatus','ais_reas',
 'connTestRes','connDirty','conn_url_row','connBody'].forEach((id)=>mk('div',id));
mk('datalist','ais_models').options=[];
"""


# Сценарий: профили как у человека — облачный «свой URL» с ключом-маской и второй,
# провайдер которого адрес подставляет сам.
STAND = r"""
const PROFILES={
  'CommandCode':{provider:'openai',base_url:'https://api.commandcode.ai/provider/v1',
    api_key:'••••••••',model:'meta/muse-spark-1.3-contributor',concurrency:2,
    headers:{'X-Test':'1'}},
  'Flux 2 Pro':{provider:'openrouter',base_url:'https://openrouter.ai/api/v1',
    model:'google/gemini-3.1-flash'}};
const ACTIVE='CommandCode';
AICFG={active:ACTIVE,profiles:PROFILES,active_omni:'__local__',active_image:'__off__',
  image_rembg:true,glitch_glow:'builtin',
  presets:{openai:{base_url:'',models:[]},openrouter:{base_url:'https://openrouter.ai/api/v1',models:[]},
    lmstudio:{base_url:'http://localhost:1234/v1',models:[]}},
  reasoning_models:[],reasoning_examples:[]};
// Списки шагов и видео — не предмет этого стенда (у них свои сторожа), а тянут
// половину интерфейса: подменяем, чтобы сценарий говорил только о форме профиля.
fillAIProfileSelects=function(){};

const NET=[];
let TEST_ERROR=false;
global.fetch=async(url,opts)=>{
  const body=JSON.parse((opts&&opts.body)||'{}');
  NET.push({url:String(url),body:(opts&&opts.body)||''});
  if(url==='/api/ai_test'){
    if(TEST_ERROR)return {ok:true,json:async()=>({error:'нет ключа API'})};
    return {ok:true,json:async()=>({ok:true,ms:123})};}
  if(url==='/api/ai_config'&&body.action==='save_profile'){
    PROFILES[body.name]=Object.assign({},PROFILES[body.name],{
      provider:body.profile.provider,base_url:body.profile.base_url,
      api_key:body.profile.api_key,model:body.profile.model,
      concurrency:body.profile.concurrency===''?null:Number(body.profile.concurrency)});
    return {ok:true,json:async()=>({active:ACTIVE,profiles:PROFILES,active_omni:'__local__',
      active_image:'__off__',image_rembg:true,glitch_glow:'builtin'})};}
  throw new Error('нежданный запрос: '+url);};

const out={};
const typing=(id,v)=>{$(id).value=v;$('connBody').dispatch('input',{type:'input',bubbles:true});};

// ---- 1. выбор профиля: поля из профиля, несохранённых правок нет ----
aiSetPick('CommandCode');
out.picked={name:val('ais_name'),provider:val('ais_provider'),url:val('ais_url'),
  key:val('ais_key'),headers:val('ais_headers'),model:val('ais_model'),
  conc:val('ais_concurrency'),dirty:$('connDirty').textContent,
  url_row:$('conn_url_row').style.display,active_btn:$('aisActive').style.display,
  caps:$('ais_reas').textContent};

// ---- 2. у облачного провайдера адрес скрыт: он известен ----
aiSetPick('Flux 2 Pro');
out.other={url_row:$('conn_url_row').style.display,url:val('ais_url'),
  dirty:$('connDirty').textContent,active_btn:$('aisActive').style.display};

// ---- 3. правка поля -> «есть несохранённые правки» ----
aiSetPick('CommandCode');
typing('ais_model','meta/other-model');
out.dirty_after=$('connDirty').textContent;

// ---- 4. «Проверить»: результат рядом с кнопкой, не тостом; правка полей его гасит ----
await aiSetTest();
out.test={res:$('connTestRes').textContent,cls:$('connTestRes').className,
  status:$('aisStatus').textContent,toasts:TOASTS.slice(),net:NET.map((x)=>x.url)};
typing('ais_model','meta/muse-spark-1.3-contributor');
out.test_cleared={res:$('connTestRes').textContent,cls:$('connTestRes').className};

// ---- 5. ошибка проверки — тем же полем ----
TEST_ERROR=true;
await aiSetTest();
out.test_err={res:$('connTestRes').textContent,cls:$('connTestRes').className,
  status:$('aisStatus').textContent};
TEST_ERROR=false;

// ---- 6. «Отмена» возвращает поля к сохранённому профилю ----
typing('ais_model','meta/третья');
typing('ais_concurrency','9');
aiSetCancel();
out.cancel={model:val('ais_model'),conc:val('ais_concurrency'),
  dirty:$('connDirty').textContent,status:$('aisStatus').textContent};

// ---- 6b. новый профиль: сохранять пока нечего, «Отмена» очищает форму ----
aiSetNew();
typing('ais_name','Новый');
out.new_profile={dirty:$('connDirty').textContent,del:$('aisDel').style.display,
  clone:$('aisClone').style.display,active:$('aisActive').style.display,
  url_row:$('conn_url_row').style.display};
aiSetCancel();
out.new_cancel={name:val('ais_name'),model:val('ais_model'),
  dirty:$('connDirty').textContent};

// ---- 7. провайдер «свой URL» показывает адрес; смена провайдера гасит ключ-маску ----
aiSetPick('CommandCode');
$('ais_provider').value='openrouter';aiSetProv();
out.prov_other={url_row:$('conn_url_row').style.display,url:val('ais_url'),
  key:val('ais_key'),model:val('ais_model'),warn:$('ais_key_warn').style.display};
$('ais_provider').value='openai';aiSetProv();
out.prov_openai={url_row:$('conn_url_row').style.display};

// ---- 8. сохранение: тело запроса прежнего формата, ключ-маска не тронут ----
aiSetPick('CommandCode');
typing('ais_model','meta/muse-spark-1.3-contributor');   // правка, которую сохраняем
NET.length=0;
await aiSetSave();
out.save={body:NET[0]?JSON.parse(NET[0].body):null,
  dirty:$('connDirty').textContent,status:$('aisStatus').textContent,
  key:val('ais_key'),model:val('ais_model'),
  profile:PROFILES.CommandCode};
out.list=$('aisList').innerHTML;
console.log(JSON.stringify(out));
"""


# ---- 1. подвал закреплён, тело прокручивается --------------------------------

def test_body_scrolls_and_the_footer_stays_pinned() -> None:
    """Подвал не сжимается и не уезжает: прокручивается тело формы, а не панель.

    Цепочка высот идёт от панели вкладки: `#aistab_models` — flex-колонка с
    `overflow:hidden` (панель не прокручивается целиком), `.conn-body` забирает
    высоту и прокручивается сам, `.conn-foot` — `flex:none` внизу. Пропадёт
    `flex:none` — подвал начнёт сжиматься вместе с телом, и «Сохранить» уедет
    под нижний край (мутация сторожа).
    """
    css = _read(CSS)

    tab = _rule(css, "#mbAISettings #aistab_models")
    assert "display:flex" in tab and "flex-direction:column" in tab, \
        "панель «Подключений» не flex-колонка: подвал некуда закрепить: %s" % tab
    assert "overflow:hidden" in tab, \
        "панель вкладки снова прокручивается целиком — подвал уедет вместе с ней: %s" % tab

    conn = _rule(css, ".conn")
    assert "flex:1" in conn and "min-height:0" in conn and "min-width:0" in conn, \
        "колонки окна не тянутся по высоте или не сжимаются: %s" % conn

    body = _rule(css, ".conn-body")
    for frag in ("flex:1", "min-height:0", "overflow-y:auto", "overflow-x:hidden"):
        assert frag in body, "тело формы не прокручивается внутри: нет %s: %s" % (frag, body)

    foot = _rule(css, ".conn-foot")
    assert "flex:none" in foot, \
        "подвал снова сжимается вместе с телом — «Сохранить» уезжает за край: %s" % foot

    # В разметке подвал стоит ПОСЛЕ тела и содержит кнопки действий
    tab_html = _models_tab(_read(HTML))
    assert tab_html.index('id="connBody"') < tab_html.index('class="conn-foot"'), \
        "подвал стоит выше тела формы — при прокрутке он окажется не внизу"
    # max-height/overflow в инлайне подвала — попытка «пришпилить» его руками, мимо цепочки
    assert "max-height" not in foot and "position:fixed" not in foot, foot


def test_rows_are_a_grid_with_a_narrow_label_column() -> None:
    """Подписи — узкой колонкой слева, поля сжимаются (иначе форма шире окна).

    Колонка значений `minmax(0,1fr)`: без нуля минимум равен ширине содержимого,
    и длинный `id` модели распирает форму за правый край модалки — ровно то, от
    чего окно и переделывали.
    """
    css = _read(CSS)

    row = _rule(css, ".conn-row")
    assert "grid-template-columns:92px minmax(0,1fr)" in row, \
        "строка настройки снова не сетка «узкая подпись — сжимаемое поле»: %s" % row
    assert "min-width:0" in row, row
    label = _rule(css, ".conn-row>label")
    assert "min-width:0" in label and "margin:0" in label, \
        "подпись поля распирает колонку: %s" % label

    prov = _rule(css, ".conn-row>select")
    # По ширине своего текста, а не жёсткие 240px: живой прогон 27.09 — «OpenAI-compatible
    # (custom URL)» обрезался до «(custom U». И не на всю строку: justify-self:start.
    assert "width:auto" in prov and "justify-self:start" in prov, \
        "провайдер — не по ширине текста: %s" % prov

    ctl = _rule(css, ".conn-ctl")
    assert "flex-wrap:wrap" in ctl and "min-width:0" in ctl, \
        "поле с кнопкой не переносится или не сжимается: %s" % ctl
    ctl_input = _rule(css, ".conn-ctl>input,.conn-ctl>select")
    assert "flex:1" in ctl_input and "min-width:130px" in ctl_input, \
        "поле в строке «Модель»/«Ключ» не сжимается: %s" % ctl_input

    name = _rule(css, ".conn-name>input")
    assert "flex:1" in name and "min-width:0" in name, \
        "поле имени профиля распирает строку со значками: %s" % name


def test_buttons_are_plain_rectangles_and_only_save_is_primary() -> None:
    """Кнопки одного вида и прямоугольные, главная одна — «Сохранить».

    Ряд с главным действием (`.actbar`) даёт «пилюли» — в этой форме их быть не
    должно, поэтому подвал размечен своим классом; иконки обязаны нести `data-t`
    (всплывающая справка) и `aria-label` (скринридер).
    """
    css = _read(CSS)
    foot_btn = _rule(css, ".conn-foot button")
    assert "border-radius:var(--r)" in foot_btn, \
        "кнопки подвала снова «пилюли»: %s" % foot_btn
    assert "min-height:var(--h-sm)" in foot_btn, foot_btn

    tab = _models_tab(_read(HTML))
    assert 'class="actbar"' not in tab, \
        "ряд кнопок профиля снова .actbar — его правило делает из кнопок пилюли"
    assert tab.count('class="primary') == 1, \
        "главная кнопка в форме не одна: %r" % re.findall(r'class="primary[^"]*"', tab)
    assert 'id="connSave"' in tab and 'id="connCancel"' in tab, \
        "в подвале нет пары «Отмена»/«Сохранить»"

    icons = re.findall(r"<button[^>]*>\s*<span data-ic=", tab)
    assert len(icons) >= 4, "значков в форме меньше четырёх: %r" % icons
    for btn in re.findall(r"<button[^>]*>\s*<span data-ic=[^>]*></span></button>", tab):
        assert "aria-label=" in btn, "у значка нет aria-label: %r" % btn
        assert "data-t=" in btn, "у значка нет всплывающей подсказки data-t: %r" % btn


def test_layout_fits_the_window_at_1366_and_1920() -> None:
    """Ни один элемент формы не выходит за окно на 1366×768 и 1920×1080.

    Считаем по числам из CSS и разметки: потолок модалки, отступы подложки,
    колонка вкладок, отступы тела, колонка профилей, колонка подписей и минимум
    поля с кнопкой. Расширить любую колонку «на глазок» — тест краснеет с суммой.
    """
    css = _read(CSS)
    html = _read(HTML)

    modal = re.search(r'id="mbAISettings".*?<div class="modal" style="max-width:(\d+)px"', html, re.S)
    assert modal, "у окна настроек пропал потолок ширины"
    modal_max = int(modal.group(1))
    bd = re.search(r"padding:(\d+)px (\d+)px", _rule(css, ".backdrop"))
    assert bd, "у подложки модалки не разобрать отступы: %s" % _rule(css, ".backdrop")
    backdrop_pad = int(bd.group(2))          # вертикальный отступ в бюджет ширины не входит
    tabs_w = _basis(css, ".tabs")
    mbody_pad = _px(css, ".mbody", "padding")
    gap = _px(css, ".conn", "gap")
    list_w = _basis(css, ".conn-list")
    label_w = int(re.search(r"grid-template-columns:(\d+)px", _rule(css, ".conn-row")).group(1))
    row_gap = _px(css, ".conn-row", "gap")
    input_min = int(re.search(r"min-width:(\d+)px",
                              _rule(css, ".conn-ctl>input,.conn-ctl>select")).group(1))
    # «Проверить» — текстовая кнопка рядом с полем; её минимум: кегль 12.5px и отступы
    # .sm. Берём с запасом: поле и кнопка обязаны ужиться в одной строке.
    btn_min = 96

    report = []
    for viewport in (1366, 1920):
        avail = viewport - 2 * backdrop_pad
        width = min(modal_max, avail)
        assert width + 2 * backdrop_pad <= viewport, (
            "модалка шире окна %d: %d + отступы %d" % (viewport, width, backdrop_pad))
        pane = width - tabs_w
        form = pane - 2 * mbody_pad - list_w - gap
        control = form - label_w - row_gap
        need = input_min + row_gap + btn_min
        report.append("окно %d: модалка %d, панель %d, форма %d, поле с кнопкой %d"
                      % (viewport, width, pane, form, control))
        assert control >= need, (
            "на %d×768 полю с кнопкой остаётся %d px, а нужно %d (%s)"
            % (viewport, control, need, "; ".join(report)))

    # Запасной ноль: инлайновых min-width в форме нет — ширины задаёт CSS, и ширина
    # колонки профилей не перебивается стилем на элементе (так список и распирал окно).
    tab = _models_tab(html)
    assert not re.search(r'style="[^"]*min-width:\s*\d+px', tab), \
        "в форме вернулся инлайновый min-width: %r" % re.findall(r'style="[^"]*min-width[^"]*"', tab)
    assert backdrop_pad == 20, \
        "отступы подложки изменились — пересчитай бюджет ширины: %d" % backdrop_pad


def test_advanced_block_is_collapsed_and_the_address_waits_for_its_provider() -> None:
    """«Дополнительно» свёрнуто, а адрес показывается только «своему URL».

    Заголовки и «Одновременных запросов» нужны редко (корпоративный шлюз), а на
    виду превращали форму в простыню. Адрес у остальных провайдеров известен —
    поле для него только сбивало с толку.
    """
    tab = _models_tab(_read(HTML))

    m = re.search(r"<details class=\"conn-adv\"([^>]*)>", tab)
    assert m, "в форме нет свёрнутого блока «Дополнительно»"
    assert "open" not in m.group(1), "«Дополнительно» раскрыто по умолчанию"
    adv = tab[tab.index('class="conn-adv"'):]
    adv = adv[:adv.index("</details>")]
    assert 'id="ais_headers"' in adv and 'id="ais_concurrency"' in adv, \
        "заголовки и «Одновременных запросов» не убраны в «Дополнительно»"
    assert "Дополнительно — заголовки, одновременные запросы" in adv, \
        "у блока «Дополнительно» нет подписи из макета"

    url_row = re.search(r'<div class="conn-row" id="conn_url_row"[^>]*>', tab)
    assert url_row, "в форме нет строки адреса"
    assert "display:none" in url_row.group(0), \
        "строка адреса показана сразу — у большинства провайдеров адрес известен"
    assert 'id="connTestRes"' in tab, "у «Проверить» нет места для результата"
    test_btn = re.search(r"<button[^>]*onclick=\"aiSetTest\(\)\"[^>]*>", tab)
    assert test_btn, "кнопка «Проверить» пропала"
    assert 'id="connTestRes"' in tab[tab.index(test_btn.group(0)):], \
        "результат проверки стоит не рядом с кнопкой"


# ---- 2. поведение формы на боевом коде ---------------------------------------

@node
def test_form_behavior_on_the_real_settings_script(tmp_path: Any) -> None:
    """Боевой `10-settings.js`: выбор профиля, признак правок, «Отмена», «Проверить», сохранение.

    Проверяется то, что видно человеку: поля заполняются из профиля и маска ключа
    остаётся маской, признак «есть несохранённые правки» появляется от правки поля
    и гаснет после «Отмены», результат «Проверить» уходит в `#connTestRes` (не
    тостом и не в подвал) и стирается при правке полей, а тело запроса на
    сохранение — прежнего формата `ai_config`.
    """
    res = _run_node(tmp_path, "conn_form.js", STAND)

    # 1. Профиль открылся: поля из профиля, правок нет, «свой URL» показывает адрес
    picked = res["picked"]
    assert picked["name"] == "CommandCode", picked
    assert picked["provider"] == "openai", picked
    assert picked["url"] == "https://api.commandcode.ai/provider/v1", picked
    assert picked["key"] == "••••••••", "ключ пришёл не маской: %r" % picked["key"]
    assert picked["headers"] == "X-Test: 1", "заголовки не развернулись в текст: %r" % picked
    assert picked["model"] == "meta/muse-spark-1.3-contributor", picked
    assert picked["conc"] == "2", "одновременные запросы не подставились: %r" % picked
    assert picked["dirty"] == "", "свежеоткрытый профиль помечен как изменённый"
    assert picked["url_row"] == "", "у провайдера «свой URL» адрес скрыт"
    assert picked["active_btn"] == "none", "у активного профиля осталась кнопка «сделать активным»"
    assert "уровни ума" in picked["caps"], "строка возможностей модели пропала: %r" % picked

    # 2. У облачного провайдера адрес скрыт и профиль не «изменён» от одного открытия
    other = res["other"]
    assert other["url_row"] == "none", "у провайдера с известным адресом строка адреса на виду"
    assert other["url"] == "https://openrouter.ai/api/v1", \
        "адрес провайдера не подставился (он уедет в сохранение пустым): %r" % other["url"]
    assert other["dirty"] == "", "переключение профиля пометило форму изменённой"
    assert other["active_btn"] == "", "у неактивного профиля нет кнопки «сделать активным»"

    # 3. Правка поля — признак несохранённых правок
    assert res["dirty_after"] == "есть несохранённые правки", res["dirty_after"]

    # 4. «Проверить»: результат у кнопки, подвал не тронут, тоста нет
    test = res["test"]
    assert test["res"] == "✓ работает · 123 мс", "результат проверки: %r" % test["res"]
    assert test["cls"] == "ok", "результат проверки без статуса .ok: %r" % test["cls"]
    assert test["status"] == "", "результат проверки ушёл в подвал вместо кнопки"
    assert test["toasts"] == [], "результат проверки показан тостом: %r" % test["toasts"]
    assert test["net"] == ["/api/ai_test"], test["net"]
    assert res["test_cleared"]["res"] == "" and res["test_cleared"]["cls"] == "muted", \
        "результат проверки не гаснет после правки полей: %r" % res["test_cleared"]

    # 5. Ошибка проверки — тем же полем
    err = res["test_err"]
    assert err["res"].startswith("✗ нет ключа API"), "текст ошибки проверки: %r" % err["res"]
    assert err["cls"] == "err", err
    assert err["status"] == "", "ошибка проверки ушла в подвал: %r" % err["status"]

    # 6. «Отмена» возвращает поля к сохранённому профилю
    cancel = res["cancel"]
    assert cancel["model"] == "meta/muse-spark-1.3-contributor", \
        "«Отмена» не вернула модель: %r" % cancel["model"]
    assert cancel["conc"] == "2", "«Отмена» не вернула одновременные запросы: %r" % cancel["conc"]
    assert cancel["dirty"] == "", "после «Отмены» форма всё ещё «изменена»"
    assert cancel["status"] == "правки отменены", cancel["status"]

    # 6b. Новый профиль: сохранять нечего — «Отмена» очищает форму, значки профиля скрыты
    newp = res["new_profile"]
    assert newp["dirty"] == "есть несохранённые правки", newp
    assert newp["del"] == "none" and newp["clone"] == "none" and newp["active"] == "none", \
        "у нового профиля остались значки дубля/удаления/активности: %r" % newp
    assert newp["url_row"] == "none", "у LM Studio (локальный) показан адрес"
    assert res["new_cancel"]["name"] == "" and res["new_cancel"]["model"] == "", res["new_cancel"]
    assert res["new_cancel"]["dirty"] == "", "пустая форма нового профиля помечена изменённой"

    # 7. Смена провайдера показывает/прячет адрес; ключ-маска при смене адреса гаснет
    #    (сохранённый ключ к новому адресу не подставляется — поведение не менялось)
    assert res["prov_other"]["url_row"] == "none", "адрес остался на виду у облачного провайдера"
    assert res["prov_other"]["url"] == "https://openrouter.ai/api/v1", res["prov_other"]
    assert res["prov_other"]["key"] == "", "ключ-маска переехала на другой адрес: %r" % res["prov_other"]
    assert res["prov_other"]["model"] == "", "модель осталась от другого провайдера"
    assert res["prov_other"]["warn"] == "", "человеку не сказали, почему ключ надо ввести заново"
    assert res["prov_openai"]["url_row"] == "", "у «своего URL» адрес не показался"

    # 8. Сохранение: формат тела прежний, ключ-маска уходит как есть (сервер её узнаёт)
    save = res["save"]
    body = save["body"]
    assert body is not None, "сохранение не сходило на сервер"
    assert body["action"] == "save_profile" and body["name"] == "CommandCode", body
    assert set(body["profile"]) == {"provider", "base_url", "api_key", "headers_text",
                                    "model", "concurrency"}, \
        "формат ai_config разъехался: %r" % sorted(body["profile"])
    assert body["profile"]["api_key"] == "••••••••", \
        "нетронутый ключ ушёл не маской — сервер посчитает его новым: %r" % body["profile"]
    assert body["profile"]["headers_text"] == "X-Test: 1", body["profile"]
    assert body["profile"]["concurrency"] == "2", body["profile"]
    assert save["dirty"] == "", "после сохранения форма помечена изменённой"
    assert save["status"] == "сохранено", save["status"]
    assert save["profile"]["model"] == "meta/muse-spark-1.3-contributor", \
        "правка модели не доехала до сервера: %r" % save["profile"]

    # 9. Список профилей не пострадал: активный — с точкой, имя — в .nm
    assert '<span class="dot">●</span>' in res["list"], "активный профиль потерял точку"
    assert '<span class="nm">' in res["list"], "имя профиля больше не в .nm (многоточие не сработает)"
    assert "＋ Новый профиль" in res["list"], "пункт «Новый профиль» пропал из списка"
