# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Статические контракты блока «Подкаст → рилсы» на шаге 1 и вкладки «Подкаст» в ⚙ (П4).

Без Chrome и без сервера: читаем templates/index.html и static/app/*.js. Проверяем то, что
глазами не ловится и что ломается тихо: порядок карточек, id из контракта П4 (их ждёт П5
и JS), подключённые обработчики и то, что настройки шага видны во вкладке ⚙.
"""
import io
import json
import os
import re
import shutil
import subprocess

import pytest

from core import app_meta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "templates", "index.html")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def html():
    return _read(HTML)


@pytest.fixture(scope="module")
def js():
    return app_meta.app_js_text()


@pytest.fixture(scope="module")
def podcast_block(html):
    """Карточка «Подкаст → рилсы» целиком: от её открывающего div до карточки «Проект» (П18: обычная карточка, не details)."""
    start = html.index('<div class="card" id="podcast_card">')
    return html[start:html.index('<h2><span class="k">◆</span>Проект</h2>', start)]


@pytest.fixture(scope="module")
def podcast_tab(html):
    """Панель вкладки «Подкаст» в ⚙: от её div до вкладки «Разметка»."""
    start = html.index('id="aistab_podcast"')
    return html[start:html.index('id="aistab_markup"', start)]


def test_podcast_card_sits_between_gdrive_and_project(html):
    """Блок подкаста — сразу после гугл-диска и до карточки «Проект» на шаге 1."""
    gdrive = '<div class="card" id="gdrive_card">'
    project = '<h2><span class="k">◆</span>Проект</h2>'
    assert html.count(gdrive) == 1 and html.count(project) == 1
    g = html.index(gdrive)
    p = html.index('<div class="card" id="podcast_card">')
    pr = html.index(project)
    assert g < p < pr, "порядок шага 1: гугл-диск → подкаст → «Проект»"


def test_gdrive_and_podcast_cards_are_plain_cards_not_details(html):
    """П18: обе карточки — обычные div.card с h2, без свёртки (<details>/<summary>)."""
    assert '<details class="card gdrive-card"' not in html
    assert '<details class="card podcast-card"' not in html
    assert '<details class="card podcast-card" id="podcast_card">' not in html
    assert html.count('<div class="card" id="gdrive_card">') == 1
    assert html.count('<div class="card" id="podcast_card">') == 1
    for card in ('<div class="card" id="gdrive_card">', '<div class="card" id="podcast_card">'):
        body = html[html.index(card) + len(card):]
        assert body.lstrip().startswith("<h2>"), "первым в карточке — один заголовок h2"
    step1 = html[html.index('id="gdrive_card"'):html.index('<h2><span class="k">◆</span>Проект</h2>')]
    assert "<summary" not in step1, "в карточках Google Drive и подкаста нет summary"
    assert "</details>" not in step1, "свёрнутых блоков на шаге 1 больше нет"


@pytest.mark.parametrize("element_id", ["gdrive_url", "gd_go", "gdrive_status"])
def test_gdrive_card_contract_ids(html, element_id):
    """Контракт UI: ссылка, «Скачать» и статус — на месте в карточке."""
    start = html.index('<div class="card" id="gdrive_card">')
    block = html[start:html.index('<div class="card" id="podcast_card">', start)]
    assert f'id="{element_id}"' in block


def test_gdrive_card_has_no_folder_or_spread_rows(html):
    """В карточке нет папки загрузки и «Разложить по камерам»: папка загрузки — в окне
    «Куда что качать», раскладку делает само окно."""
    start = html.index('<div class="card" id="gdrive_card">')
    block = html[start:html.index('<div class="card" id="podcast_card">', start)]
    for gone in ('id="gdrive_dest"', 'id="gdrive_cam1"', 'id="gd_spread"'):
        assert gone not in block, gone
    window = html[html.index('id="mbGdMap"'):html.index('id="mbAISettings"')]
    assert 'id="gdrive_dest"' in window, "папки загрузки нет в окне «Куда что качать»"
    assert "Куда что качать" in window


def test_cut_tab_has_page_blocks_section_with_two_checkboxes(html):
    """⚙ → «Нарезка»: раздел «Страница нарезки» и две галки показа блоков (по умолчанию ВКЛ)."""
    start = html.index('id="aistab_cut"')
    tab = html[start:html.index('id="aistab_podcast"', start)]
    section = tab[tab.index("Страница нарезки"):]
    for chk, handler in (("show_gdrive", "setPageBlock('gdrive', this.checked)"),
                         ("show_podcast", "setPageBlock('podcast', this.checked)")):
        tag = re.search(r'<input type="checkbox" id="%s"[^>]*>' % chk, section)
        assert tag, f"нет галки {chk} в разделе «Страница нарезки»"
        assert re.search(r"\schecked[\s>]", tag.group(0)), f"галка {chk} должна быть отмечена по умолчанию"
        assert handler in tag.group(0)
    assert "Гугл-диск" in section and "Подкаст → рилсы" in section


def test_page_block_labels_are_short_and_do_not_wrap(html):
    """П19: подписи галок короткие и в одну строку (колонка подписи 176px их переносила).
    Что это блоки страницы, уже сказано заголовком раздела и его «!»."""
    start = html.index('id="aistab_cut"')
    tab = html[start:html.index('id="aistab_podcast"', start)]
    section = tab[tab.index("Страница нарезки"):]
    for chk, label in (("show_gdrive", "Гугл-диск"), ("show_podcast", "Подкаст → рилсы")):
        m = re.search(r'<label class="chk pgblk"[^>]*><input type="checkbox" id="%s"[^>]*> %s</label>'
                      % (chk, re.escape(label)), section)
        assert m, f"подпись галки {chk} должна быть «{label}» с классом pgblk"
    css = _read(os.path.join(ROOT, "static", "app.css"))
    rule = re.search(r"\.pgblk\s*\{([^}]*)\}", css)
    assert rule, "нет правила .pgblk в app.css"
    assert "white-space:nowrap" in rule.group(1).replace(" ", "")


@pytest.mark.parametrize("element_id", [
    "podcast_card", "podcast_src", "podcast_url", "podcast_file",
    "podcast_track", "podcast_root", "podcast_reels", "podcast_go", "podcast_stop",
    "podcast_status", "podcast_list",
])
def test_podcast_card_contract_ids(podcast_block, element_id):
    """id — часть контракта: по ним JS (45-podcast.js) и П5 находят элементы."""
    assert f'id="{element_id}"' in podcast_block


def test_podcast_source_switch_is_a_segment(podcast_block):
    """Режим источника — сегмент-переключатель, как режим камер (.seg), два варианта."""
    assert '<div class="seg" id="podcast_src">' in podcast_block
    assert podcast_block.count('name="podcast_src"') == 2
    assert 'value="url"' in podcast_block and 'value="file"' in podcast_block


def test_podcast_go_is_primary_and_stop_hidden_by_default(podcast_block):
    """«Разобрать» — primary; «Стоп» видна только пока идёт джоб, по умолчанию скрыта."""
    assert 'class="primary" id="podcast_go"' in podcast_block
    stop = podcast_block[podcast_block.index('id="podcast_stop"'):]
    assert 'style="display:none"' in stop[:stop.index(">")]


def test_podcast_handlers_are_defined_in_js(podcast_block, js):
    """Каждый onclick/onchange блока — функция, которая реально есть в JS."""
    names = set(re.findall(r'on(?:click|change|input)="(\w+)\(', podcast_block))
    assert names, "в блоке нет обработчиков"
    missing = [n for n in sorted(names)
               if not re.search(r"function\s+%s\s*\(" % re.escape(n), js)]
    assert not missing, f"обработчики без функции: {missing}"


def test_podcast_block_has_no_inline_hint_and_no_emoji(podcast_block):
    """Справка — только в «!»-тултипах; эмодзи в хроме запрещены (docs/DESIGN.md)."""
    assert 'class="hint"' not in podcast_block
    assert not re.search(r"[\U0001F000-\U0001FAFF]", podcast_block)


def test_podcast_tab_is_listed_after_cut_and_before_markup(html):
    """Вкладка «Подкаст» в ⚙ — после «Нарезка», до «Разметка»."""
    cut = html.index('id="aistabbtn_cut"')
    pod = html.index('id="aistabbtn_podcast"')
    mark = html.index('id="aistabbtn_markup"')
    assert cut < pod < mark
    assert 'id="aistab_podcast"' in html


def test_podcast_tab_panel_has_step_settings(podcast_tab):
    """Панель: модель и ум шага podcast, движок расшифровки и язык."""
    assert 'data-prof="podcast"' in podcast_tab
    assert 'data-reas="podcast"' in podcast_tab
    assert 'id="reas_hint_podcast"' in podcast_tab
    assert 'id="podcast_asr"' in podcast_tab and 'onchange="setPodcastAsr(this.value)"' in podcast_tab
    assert 'id="podcast_lang"' in podcast_tab and 'onchange="setPodcastLang(this.value)"' in podcast_tab


def test_podcast_lang_options_are_the_agreed_set(podcast_tab):
    """Языки — ровно согласованный набор, «auto» первым."""
    values = re.findall(r'<option value="(\w+)"', podcast_tab[podcast_tab.index('id="podcast_lang"'):])
    assert values == ["auto", "ru", "en", "uk", "de", "es", "fr", "it", "pt", "pl", "tr"]


def test_aisettab_knows_the_podcast_tab():
    """aiSetTab перечисляет вкладки сам — без 'podcast' вкладка не откроется."""
    src = _read(SETTINGS_JS)
    m = re.search(r"const allowed=\[([^\]]+)\]", src)
    assert m and "'podcast'" in m.group(1)


def test_podcast_state_hooks_wired_into_boot():
    """Состояние формы уходит в stateObj и возвращается в applyState (99-boot.js)."""
    boot = _read(os.path.join(ROOT, "static", "app", "99-boot.js"))
    assert "podcastStateObj()" in boot
    assert "podcastApplyState(s.podcast)" in boot


def test_open_editor_is_real_since_p5(js):
    """Окно правки (П5) настоящее: заглушка «в следующей версии» удалена, функция — в 46-podcast-editor.js."""
    assert "Окно правки — в следующей версии" not in js
    assert re.search(r"function openPodcastEditor\(dir\)", js)


def test_pod_window_timeline_is_canvas_and_pick_bar_wired(html):
    """Окно правки (П14): «Вся запись» — холст; над списком — счётчик выбора и два действия;
    шапочная кнопка переводит выбранные (или все), а не старый podConvert без ids."""
    start = html.index('<div class="backdrop" id="mbPod"')
    block = html[start:html.index("MODAL: inserts", start)]
    assert '<canvas id="pod_tl_cv"' in block
    for element_id in ("pod_conv_n", "pod_del_n", "pod_del_picked", "pod_conv_picked", "pod_conv_lbl"):
        assert f'id="{element_id}"' in block, f"в окне нет id {element_id}"
    assert 'onclick="podConvert()"' not in block
    assert 'onclick="podConvertPicked()"' in block


# --- П16: дорожка YouTube в интерфейсе — подписи и «Разобрать» с проверкой (node) ---

POD45 = os.path.join(ROOT, "static", "app", "45-podcast.js")
STYLES95 = os.path.join(ROOT, "static", "app", "95-styles.js")
EN_JSON = os.path.join(ROOT, "static", "i18n", "en.json")
NODE = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")

# Заглушки браузера для 45-podcast.js: $ и элементы с детьми, атрибутами и фокусом, document, fetch,
# t() по en.json. Кнопка и список лежат в боксе (podcast_track_box) — его contains() видит оба.
_POD_STUB = r"""
const EN = JSON.parse(require('fs').readFileSync(__EN__, 'utf8'));
function t(s, v){
  let out = Object.prototype.hasOwnProperty.call(EN, s) ? EN[s] : s;
  if(v) for(const k in v) out = out.split('{' + k + '}').join(String(v[k]));
  return out;
}
let LANG='en';                 // язык интерфейса: в app его задаёт 00-core.js
let __focused=null;            // элемент, который последним взял focus()
class El{
  constructor(){this.style={};this.disabled=false;this.value='';this.className='';
    this.textContent='';this.title='';this.dataset={};this.tabIndex=0;this.kids=[];this.attrs={};this.onclick=null;}
  set innerHTML(v){this.kids=[];}
  get innerHTML(){return '';}
  get children(){return this.kids;}
  appendChild(o){this.kids.push(o);return o;}
  contains(x){if(!x)return false;if(x===this)return true;return this.kids.some(k=>k.contains(x));}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?this.attrs[k]:null;}
  focus(){__focused=this;}
}
const __els={};
function $(id){return __els[id]||(__els[id]=new El());}
function val(id){return String($(id).value==null?'':$(id).value).trim();}
const document={addEventListener(){},
  querySelector(){return {value:'url'};},
  createElement(){return new El();},
  get activeElement(){return __focused;}};
function toast(){}
function uiLog(){}
function errText(d){return String(d.error||'');}
function segUI(){}
function saveState(){}
// Окна: удаление подкаста открывает mbDelPodcast (45-podcast.js), здесь только журнал открытий
const OPENED=[],CLOSED=[];
function openModal(id){OPENED.push(id);}
function closeModal(id){CLOSED.push(id);}
function fmtLog(x){return String(x);}
const __calls=[];
const PROBE={ok:true,title:'Тестовый выпуск',default:'140-23',tracks:[
  {format_id:'140-23',lang:'en-US',label:'English (US) original (default)',original:true,dub:false,abr:129.475},
  {format_id:'140-14',lang:'ru',label:'Russian',original:false,dub:true,abr:129.478}]};
async function fetch(url, opts){
  const body=opts&&opts.body?JSON.parse(opts.body):null;
  __calls.push({url:url,body:body});
  const data=url==='/api/podcast_probe'?PROBE:{error:'стоп для теста'};
  return {json:async()=>data};
}
$('podcast_url').value=__URL__;
$('podcast_root').value='/work/podcasts';
$('podcast_reels').value='5';
$('podcast_track_box').kids=[$('podcast_track'),$('podcast_track_menu')];
// Снимок выбора: открыт ли список, подпись кнопки, значение для сервера, строки списка и фокус
function __state(){
  const btn=$('podcast_track'),menu=$('podcast_track_menu');
  let f=null;
  if(__focused===btn)f='btn';else if(__focused===menu)f='menu';else if(__focused)f='row:'+__focused.dataset.v;
  return {open:PODMENU,label:$('podcast_track_txt').textContent,sel:POD_TRACK_SEL,focus:f,
    rows:menu.children.map(r=>({text:r.textContent,sel:r.getAttribute('aria-selected'),v:r.dataset.v,
      dis:r.getAttribute('aria-disabled')}))};
}
"""


def _run_pod_js(tmp_path, body):
    """45-podcast.js под node: заглушки браузера, LANGNAME из 95-styles.js, кусок проверки.
    Возвращает JSON из последней строки stdout."""
    stub = (_POD_STUB.replace("__EN__", json.dumps(EN_JSON))
            .replace("__URL__", json.dumps("https://www.youtube.com/watch?v=abcDEF12345")))
    langname = re.search(r"const LANGNAME=\{[\s\S]*?\};", _read(STYLES95)).group(0)
    # кнопки строк — iconBtn из 20-widgets.js; ico — заглушка (тест с иконками подменяет её своим ico+IC)
    w = _read(os.path.join(ROOT, "static", "app", "20-widgets.js"))
    btn_at = w.index("function iconBtn(")
    btn = w[btn_at:w.index("return b;}", btn_at) + len("return b;}")]
    script = (stub + "\n" + langname + "\nfunction ico(){return '';}\n" + btn + "\n"
              + _read(POD45) + "\n" + body + "\n")
    path = tmp_path / "pod_track_check.js"
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


@NODE
def test_подпись_оригинала_и_автодубляжа(tmp_path):
    """«<язык> — оригинал» и «<язык> — автодубляж» по дорожке; без пометки — только язык."""
    out = _run_pod_js(tmp_path, """
console.log(JSON.stringify({
  orig: podTrackLabel({lang:'en-US',original:true,dub:false}),
  dub: podTrackLabel({lang:'ru',original:false,dub:true}),
  plain: podTrackLabel({lang:'ru',original:false,dub:false}),
  unknown: podTrackLabel({lang:'xx-YY',original:false,dub:true})}));
""")
    assert out["orig"] == "English — original"
    assert out["dub"] == "Russian — auto-dub"
    assert out["plain"] == "Russian"
    assert out["unknown"] == "xx — auto-dub"   # без кода страны: основа кода, не «xx-YY»


def _intl_ru_knows_bengali():
    """Есть ли в этом node русское название языка bn (нужен полный ICU). Без него тест пропускаем."""
    proc = subprocess.run(["node", "-e", "process.stdout.write(new Intl.DisplayNames(['ru'],"
                           "{type:'language'}).of('bn'))"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return proc.returncode == 0 and proc.stdout.strip() == "бенгальский"


@NODE
def test_язык_без_словаря_берётся_из_intl(tmp_path):
    """Нет в LANGNAME (bn) — название из Intl.DisplayNames на языке интерфейса, с маленькой буквы."""
    if not _intl_ru_knows_bengali():
        pytest.skip("node без Intl.DisplayNames с ru (нет полного ICU): проверка имён через Intl не нужна")
    out = _run_pod_js(tmp_path, """
LANG='ru';
console.log(JSON.stringify({bn: podTrackName('bn'), bnDashed: podTrackName('bn-BD')}));
""")
    assert out["bn"] == "бенгальский"
    assert out["bnDashed"] == "бенгальский"


@NODE
def test_неизвестный_код_сводится_к_основе(tmp_path):
    """xx-YY — не язык, которого Intl знает: остаётся основа «xx»; пустой код — прочерк."""
    out = _run_pod_js(tmp_path, """
LANG='en';
console.log(JSON.stringify({code: podTrackName('xx-YY'), empty: podTrackName('')}));
""")
    assert out["code"] == "xx"
    assert out["empty"] == "—"


@NODE
def test_разобрать_не_раскрывает_список(tmp_path):
    """Проверка из «Разобрать» — не открытие списка: после неё список сам не открывается, выбор — оригинал."""
    out = _run_pod_js(tmp_path, """
(async()=>{ await podcastGo(); console.log(JSON.stringify({state:__state()})); })();
""")
    assert out["state"]["open"] is False
    assert out["state"]["sel"] == "140-23"


@NODE
def test_разобрать_без_проверки_сначала_зовёт_проверку(tmp_path):
    """«Разобрать» по ссылке без проведённой проверки: сперва /api/podcast_probe, потом create
    с оригиналом по умолчанию — а не отдаёт выбор yt-dlp."""
    out = _run_pod_js(tmp_path, """
(async()=>{ await podcastGo();
  console.log(JSON.stringify({urls:__calls.map(c=>c.url),
    format:(__calls.find(c=>c.url==='/api/podcast_create')||{body:{}}).body.format_id}));
})();
""")
    assert out["urls"] == ["/api/podcast_probe", "/api/podcast_create"]
    assert out["format"] == "140-23"


@NODE
def test_разобрать_после_проверки_не_проверяет_снова(tmp_path):
    """Проверка уже проведена — «Разобрать» идёт сразу к созданию, второго запроса нет."""
    out = _run_pod_js(tmp_path, """
(async()=>{ await podcastLoadTracks($('podcast_url').value); __calls.length=0; await podcastGo();
  console.log(JSON.stringify({urls:__calls.map(c=>c.url)}));
})();
""")
    assert out["urls"] == ["/api/podcast_create"]


def test_подписи_дорожки_в_en_json():
    """Ключи подписей и карточки — в словаре: без них по-английски останется русский текст."""
    en = json.load(io.open(EN_JSON, encoding="utf-8"))
    for key in ("{lang} — оригинал", "{lang} — автодубляж", "автодубляж",
                "дорожка: {lang}, {kind}", "ERR_podcast_no_tracks", "ERR_podcast_track_missing",
                 "не удалось загрузить — будет оригинал"):
        assert str(en.get(key, "")).strip(), f"нет перевода: {key}"


def test_podcast_card_has_one_heading_with_the_name(podcast_block):
    """П18: один заголовок на карточку — h2 с названием и «!»-справкой (summary и второй h2 слиты)."""
    heading = podcast_block[:podcast_block.index("</h2>")]
    assert heading.count("<h2>") == 1 and "Подкаст → рилсы" in heading and 'class="i"' in heading
    assert podcast_block.count("<h2>") == 1, "внутренних h2 не должно остаться"


def test_collapsible_card_css_is_gone():
    """П18: карточки обычные — правил свёртки (summary, [open]) для них в CSS не осталось."""
    css = _read(os.path.join(ROOT, "static", "app.css"))
    for needle in (".gdrive-card", ".podcast-card"):
        assert needle not in css, f"правило {needle} должно уйти вместе со свёрткой"


def test_pod_header_buttons_share_one_size(html):
    """П15: «Сохранить» и «Перевести в рилсы» в одной шапке — обе `sm` (12.5 px), иначе разной высоты.
    Кнопки пачки под списком (добавить, удалить выбранные, в рилсы выбранные) — тоже `sm`."""
    start = html.index('<div class="backdrop" id="mbPod"')
    block = html[start:html.index("MODAL: inserts", start)]
    for element_id in ("pod_convert", "pod_add", "pod_del_picked", "pod_conv_picked"):
        tag = re.search(r'<button[^>]*id="%s"[^>]*>' % element_id, block).group(0)
        assert re.search(r'class="[^"]*\bsm\b', tag), f"кнопка {element_id} не `sm`: {tag}"


# --- П17–П20: ссылка и выбор дорожки в одной строке; своё меню дорожек (node) ---

YT_A = "https://www.youtube.com/watch?v=abcDEF12345"
YT_B = "https://www.youtube.com/watch?v=ZZZZZZZZZZZ"


def test_url_and_track_share_one_row(html):
    """Ссылка и звуковая дорожка — в одной `.row`; отдельной строки дорожки и кнопки «Проверить» нет."""
    start = html.index('<div class="row" id="podcast_url_row">')
    row = html[start:html.index('<div class="row" id="podcast_file_row"', start)]
    assert 'id="podcast_url"' in row and 'id="podcast_track"' in row
    assert 'for="podcast_track"' in row, "подпись выбора — лейбл над полем, как у соседних строк"
    assert "podcast_track_row" not in html
    assert "podcast_probe_btn" not in html
    assert "podcastProbe" not in _read(POD45)


def _url_row(html):
    start = html.index('<div class="row" id="podcast_url_row">')
    return html[start:html.index('<div class="row" id="podcast_file_row"', start)]


def test_track_is_a_button_with_listbox_menu(html):
    """П20: выбор — кнопка #podcast_track (aria-haspopup=listbox, aria-expanded), список #podcast_track_menu
    (role=listbox); нативного <select id="podcast_track"> больше нет, кнопка не скрыта."""
    row = _url_row(html)
    assert '<select id="podcast_track"' not in html
    btn = re.search(r'<button[^>]*id="podcast_track"[^>]*>', row)
    assert btn, "#podcast_track — кнопка"
    assert 'type="button"' in btn.group(0) and 'aria-haspopup="listbox"' in btn.group(0)
    assert 'aria-expanded="false"' in btn.group(0)
    assert "display:none" not in btn.group(0)
    menu = re.search(r'<div[^>]*id="podcast_track_menu"[^>]*>', row)
    assert menu and 'role="listbox"' in menu.group(0)


def test_track_menu_is_a_styled_box_that_scrolls():
    """Меню — как .spkpick .opts: фон/рамка/радиус, max-height 260px с прокруткой, скрыто, пока не открыто."""
    css = _read(os.path.join(ROOT, "static", "app.css")).replace("\r", "")
    rule = re.search(r"\.podmenu\s*\{([^}]*)\}", css)
    assert rule, "нет правила .podmenu"
    body = rule.group(1).replace(" ", "")
    assert "display:none" in body, "меню не скрыто, пока не открыто"
    assert "min-width:100%" in body, "ширина меню не меньше кнопки"
    # рамка, фон и прокрутка — у общей панели .dd-panel (её же у списка спикера)
    panel = re.search(r"\.dd-panel\s*\{([^}]*)\}", css)
    assert panel, "нет общей панели .dd-panel"
    pbody = panel.group(1).replace(" ", "")
    assert "max-height:260px" in pbody and "overflow:auto" in pbody and "background:var(--panel2)" in pbody
    assert ".podmenu.open{display:flex}" in css


def test_track_menu_js_contract():
    """Строки списка — role=option с aria-selected; клавиши и уход фокуса подключены в JS."""
    src = _read(POD45)
    assert "'role','option'" in src and "'aria-selected'" in src
    assert "podTrackKey" in src and "dropdownBind" in src, "меню дорожки не подключено к общей механике"
    assert "podTrackFocusOut" not in src, "своя копия ухода фокуса вернулась"
    assert "onfocusout" not in _read(HTML), "у #podcast_track_box снова свой onfocusout"


def test_removed_pieces_are_gone():
    """П20 убирает нативный выбор и всё, что было вокруг него: показ через showPicker, флаги жеста и ухода."""
    src = _read(POD45)
    for needle in ("podShouldReopen", "podReopenTrackSel", "showPicker", "PODBYUSER", "PODAWAY", "podMarkAway"):
        assert needle not in src, f"{needle} должен уйти вместе с нативным select"


@NODE
def test_нужна_ли_проверка_только_для_youtube_непроверенной_и_не_грузящейся(tmp_path):
    """podNeedProbe: да — только для ссылки YouTube, по которой ещё не было проверки и которую сейчас не грузим."""
    out = _run_pod_js(tmp_path, """
const Y='https://youtu.be/abcDEF12345';
console.log(JSON.stringify({
  fresh: podNeedProbe(Y,'',''),
  probed: podNeedProbe(Y,Y,''),
  loading: podNeedProbe(Y,'',Y),
  other: podNeedProbe('https://disk.yandex.ru/d/abc','',''),
  empty: podNeedProbe('','',''),
  watch: podNeedProbe('https://www.youtube.com/watch?v=abcDEF12345','','')}));
""")
    assert out == {"fresh": True, "probed": False, "loading": False,
                   "other": False, "empty": False, "watch": True}


@NODE
def test_выбор_дорожки_по_состоянию(tmp_path):
    """podTrackOptions: idle — «Оригинал (по умолчанию)» со значением ''; loading — строка «загружаю»,
    не выбирается; error — «не удалось загрузить — будет оригинал»; ready — дорожки, выбран оригинал."""
    out = _run_pod_js(tmp_path, """
const ready=podTrackOptions({kind:'ready',tracks:PROBE.tracks,def:'140-23'});
console.log(JSON.stringify({
  idle: podTrackOptions({kind:'idle'}),
  loading: podTrackOptions({kind:'loading'}),
  error: podTrackOptions({kind:'error'}),
  ready: ready,
  readyNoDef: podTrackOptions({kind:'ready',tracks:PROBE.tracks,def:''}).value}));
""")
    assert out["idle"] == {"kind": "idle", "value": "", "opts": [{"value": "", "text": "Original (default)"}]}
    assert out["loading"]["kind"] == "loading" and out["loading"]["value"] == ""
    assert out["loading"]["opts"] == [{"value": "", "text": "loading tracks…", "loading": True}]
    assert out["error"]["opts"] == [{"value": "", "text": "failed to load — will use original"}]
    assert out["ready"]["value"] == "140-23", "выбран оригинал"
    texts = [(o["value"], o["text"]) for o in out["ready"]["opts"]]
    assert texts == [("140-23", "English — original"), ("140-14", "Russian — auto-dub")]
    assert out["readyNoDef"] == "140-23", "без совета сервера — первый оригинал"


@NODE
def test_клик_открывает_список_сразу_и_дорожки_приходят_в_нём_же(tmp_path):
    """Клик по кнопке при непроверенной ссылке YouTube: список открыт СРАЗУ с «загружаю дорожки…»,
    ушёл ровно один /api/podcast_probe; ответ пришёл при открытом списке — строки дорожек, оригинал
    отмечен aria-selected, список всё ещё открыт (тот же элемент)."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  podcastTrackOpen();
  const during=__state();
  const probesDuring=__calls.filter(c=>c.url==='/api/podcast_probe').length;
  await PODLOADP;
  console.log(JSON.stringify({during:during, probesDuring:probesDuring, after:__state(),
    probes:__calls.filter(c=>c.url==='/api/podcast_probe').length}));
})();
""")
    d = out["during"]
    assert d["open"] is True and d["focus"] == "menu"
    assert d["rows"] == [{"text": "loading tracks…", "sel": "false", "v": "", "dis": "true"}]
    assert out["probesDuring"] == 1 and out["probes"] == 1
    a = out["after"]
    assert a["open"] is True, "ответ не закрывает список"
    assert [r["text"] for r in a["rows"]] == ["English — original", "Russian — auto-dub"]
    assert a["rows"][0]["sel"] == "true" and a["rows"][1]["sel"] == "false"
    assert a["label"] == "English — original" and a["sel"] == "140-23"
    assert a["focus"] == "row:140-23", "фокус — на выбранной строке списка"


@NODE
def test_выбор_строки_меняет_подпись_и_значение_для_сервера(tmp_path):
    """Клик по строке: подпись кнопки и значение для сервера (format_id) обновились, список закрыт,
    фокус на кнопке; «Разобрать» уходит с выбранной дорожкой."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  podcastTrackOpen(); await PODLOADP;
  $('podcast_track_menu').children[1].onclick();
  const after=__state();
  await podcastGo();
  const create=__calls.find(c=>c.url==='/api/podcast_create');
  console.log(JSON.stringify({after:after, format:create?create.body.format_id:null}));
})();
""")
    a = out["after"]
    assert a["open"] is False and a["label"] == "Russian — auto-dub" and a["sel"] == "140-14"
    assert a["focus"] == "btn", "фокус вернулся на кнопку"
    assert out["format"] == "140-14"


@NODE
def test_стрелки_и_enter_выбирают_строку_с_фокусом(tmp_path):
    """Стрелка вниз переводит фокус на следующую строку, Enter выбирает её; список закрыт."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  podcastTrackOpen(); await PODLOADP;
  podTrackKey({key:'ArrowDown',preventDefault(){}});
  podTrackKey({key:'Enter',preventDefault(){}});
  console.log(JSON.stringify(__state()));
})();
""")
    assert out["open"] is False and out["sel"] == "140-14" and out["label"] == "Russian — auto-dub"




@NODE
def test_повторное_открытие_сразу_список_без_проверки(tmp_path):
    """Дорожки уже получены: стрелка вниз на кнопке открывает список сразу, без нового запроса."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  podcastTrackOpen(); await PODLOADP;
  podcastTrackOpen();                                   // закрыли кликом по кнопке
  const closed=__state().open;
  podTrackKey({key:'ArrowDown',preventDefault(){}});    // снова — с клавиатуры, сразу
  console.log(JSON.stringify({closed:closed, now:__state(),
    probes:__calls.filter(c=>c.url==='/api/podcast_probe').length}));
})();
""")
    assert out["closed"] is False
    assert out["now"]["open"] is True
    assert [r["text"] for r in out["now"]["rows"]] == ["English — original", "Russian — auto-dub"]
    assert out["probes"] == 1


@NODE
def test_открытие_во_время_загрузки_новую_проверку_не_шлёт(tmp_path):
    """Пока проверка идёт, повторное открытие новую не шлёт: строка «загружаю» и тот же ответ."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  podcastTrackOpen();                  // проверка пошла, список открыт
  podcastTrackOpen();                  // закрыли
  podcastTrackOpen();                  // снова открыли — проверка ещё идёт
  const during=__state();
  await PODLOADP;
  console.log(JSON.stringify({probes:__calls.filter(c=>c.url==='/api/podcast_probe').length,
    during:during, after:__state()}));
})();
""")
    assert out["probes"] == 1, "вторая проверка во время загрузки не нужна"
    assert out["during"]["rows"][0]["text"] == "loading tracks…"
    assert out["after"]["open"] is True and len(out["after"]["rows"]) == 2


@NODE
def test_ошибка_проверки_в_списке_и_не_повторяется_при_открытии(tmp_path):
    """Ошибка — строка «не удалось загрузить — будет оригинал» (выбирается как оригинал); повтор — по правке ссылки
    или «Разобрать», не по открытию списка."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  fetch=async function(url,opts){__calls.push({url:url});return {json:async()=>({error:'нет дорожек'})};};
  podcastTrackOpen(); await PODLOADP;
  const shown=__state();
  podcastTrackOpen(); podcastTrackOpen();          // закрыть и открыть снова
  console.log(JSON.stringify({shown:shown, probes:__calls.filter(c=>c.url==='/api/podcast_probe').length}));
})();
""")
    s = out["shown"]
    assert s["open"] is True and [r["text"] for r in s["rows"]] == ["failed to load — will use original"]
    assert s["rows"][0]["sel"] == "true" and s["label"] == "failed to load — will use original"
    assert s["sel"] == ""
    assert out["probes"] == 1


@NODE
def test_ответ_на_устаревшую_ссылку_отбрасывается(tmp_path):
    """Ссылку сменили, пока шла проверка прежней (список открыт): её ответ не пишет дорожки и не меняет выбор."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  const A='__YT_A__', B='__YT_B__';
  let resolveA=null;
  fetch=function(url,opts){__calls.push({url:url,body:JSON.parse(opts.body)});
    return new Promise(r=>{resolveA=r;});};
  $('podcast_url').value=A;
  podcastTrackOpen();
  const pA=PODLOADP;                    // ответ на A ещё в пути
  $('podcast_url').value=B; podcastUrlEdit();
  resolveA({json:async()=>PROBE});
  const okA=await pA;
  console.log(JSON.stringify({okA:okA, probed:PODPROBED, sel:POD_TRACK_SEL, state:__state()}));
})();
""".replace("__YT_A__", YT_A).replace("__YT_B__", YT_B))
    assert out["okA"] is False and out["probed"] == "" and out["sel"] == ""
    assert out["state"]["open"] is False
    assert out["state"]["label"] == "Original (default)"
    assert [r["text"] for r in out["state"]["rows"]] == ["Original (default)"]


@NODE
def test_смена_ссылки_сбрасывает_проверку(tmp_path):
    """Правка ссылки сбрасывает проверку: выбор снова «Оригинал», а вернувшаяся ссылка проверяется заново."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  const A='__YT_A__', B='__YT_B__';
  await podcastLoadTracks(A);
  const readyRows=$('podcast_track_menu').children.length;
  $('podcast_url').value=B; podcastUrlEdit();
  const afterB=__state();
  $('podcast_url').value=A; podcastUrlEdit();          // снова A: проверка должна пойти заново
  podcastTrackOpen(); await PODLOADP;
  const probes=__calls.filter(c=>c.url==='/api/podcast_probe').length;
  console.log(JSON.stringify({readyRows:readyRows, afterB:afterB, probes:probes, back:__state()}));
})();
""".replace("__YT_A__", YT_A).replace("__YT_B__", YT_B))
    assert out["readyRows"] == 2
    assert out["afterB"]["sel"] == "" and [r["text"] for r in out["afterB"]["rows"]] == ["Original (default)"]
    assert out["probes"] == 2, "после смены ссылки и возврата проверка пошла второй раз"
    assert out["back"]["open"] is True and out["back"]["sel"] == "140-23"


@NODE
def test_проверка_не_для_чужой_ссылки(tmp_path):
    """Ссылка не похожа на YouTube: проверку при открытии не запускаем, список — «Оригинал (по умолчанию)»."""
    out = _run_pod_js(tmp_path, """
(async()=>{
  $('podcast_url').value='https://disk.yandex.ru/d/abc';
  podRenderTrackSel();
  podcastTrackOpen();
  console.log(JSON.stringify({probes:__calls.length, state:__state()}));
})();
""")
    assert out["probes"] == 0
    assert out["state"]["open"] is True
    assert [r["text"] for r in out["state"]["rows"]] == ["Original (default)"]


def test_podcast_js_has_no_probe_button_handler():
    """Кнопка «Проверить» убрана вместе с обработчиком; её ключ словаря остаётся — он нужен вкладке очереди."""
    src = _read(POD45)
    assert "podcastProbe" not in src
    assert "podcast_probe_btn" not in src


# --- П18: блоки страницы нарезки — галки в ⚙ → «Нарезка», состояние page ---

SETTINGS10 = os.path.join(ROOT, "static", "app", "10-settings.js")
BOOT99 = os.path.join(ROOT, "static", "app", "99-boot.js")


def _run_js_snippet(tmp_path, script):
    """Кусок JS под node без браузера (чистые функции). Возвращает JSON из последней строки stdout."""
    path = tmp_path / "page_blocks_check.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, ("node упал:\n" + (proc.stderr or proc.stdout)).strip()[:1500]
    lines = [ln.strip() for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


@NODE
def test_page_blocks_from_state(tmp_path):
    """pageBlocksFrom: нет поля — оба блока показаны (старое состояние их не прячет);
    явный false прячет только свой блок."""
    src = _read(SETTINGS10)
    fn = re.search(r"function pageBlocksFrom\(state\)\{[\s\S]*?\n\}", src).group(0)
    out = _run_js_snippet(tmp_path, fn + """
console.log(JSON.stringify({
  none: pageBlocksFrom({}),
  nullState: pageBlocksFrom(null),
  oldPageNoKeys: pageBlocksFrom({page:{}}),
  podcastOff: pageBlocksFrom({page:{podcast:false}}),
  gdriveOff: pageBlocksFrom({page:{gdrive:false}})}));
""")
    assert out["none"] == {"gdrive": True, "podcast": True}
    assert out["nullState"] == {"gdrive": True, "podcast": True}
    assert out["oldPageNoKeys"] == {"gdrive": True, "podcast": True}
    assert out["podcastOff"] == {"gdrive": True, "podcast": False}, "подкаст скрыт, гугл-диск показан"
    assert out["gdriveOff"] == {"gdrive": False, "podcast": True}


def test_page_blocks_default_is_shown():
    """Умолчание блоков — ВКЛ: без него свежая установка прячет гугл-диск и подкаст."""
    src = _read(SETTINGS10)
    assert "let PAGEBLOCKS={gdrive:true,podcast:true};" in src


def test_page_blocks_state_is_wired_into_boot():
    """Состояние page пишется в stateObj, читается в applyState и ставится при загрузке
    (restoreState — до ответа сервера, из локальной копии)."""
    boot = _read(BOOT99)
    assert ("page:(typeof PAGEBLOCKS!=='undefined')?{gdrive:PAGEBLOCKS.gdrive,podcast:PAGEBLOCKS.podcast}"
            ":{gdrive:true,podcast:true}") in boot
    assert "PAGEBLOCKS=pageBlocksFrom(s);applyPageBlocks();" in boot
    apply_state = boot[boot.index("function applyState("):boot.index("function storedState(")]
    assert "PAGEBLOCKS=pageBlocksFrom(s);applyPageBlocks();" in apply_state


def test_page_blocks_hide_with_display_not_remove():
    """Скрытие — display:none: элементы остаются в DOM, фоновые джобы пишут в них без ошибок."""
    src = _read(SETTINGS10)
    apply_body = src[src.index("function applyPageBlocks"):src.index("function setPageBlock")]
    assert "style.display=" in apply_body and "remove()" not in apply_body


# --- П21: «!» у «Куски» по центру строки; поле названия куска растягивается (статика) ---

POD46 = os.path.join(ROOT, "static", "app", "46-podcast-editor.js")


def _css_rule(css, selector):
    """Тело правила селектора из app.css (без переводов строк)."""
    m = re.search(r"(?<![\w-])" + re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, f"нет правила {selector} в app.css"
    return m.group(1)


def test_pod_head_label_has_no_bottom_margin():
    """П21: «Куски» и «!» стоят в `.pod-head` (flex, по центру). Отступ `.edlbl` (подпись НАД блоком)
    там уводит подпись вверх относительно кружка «!» — обнуляем, как в `.pod-tlhead`."""
    css = _read(os.path.join(ROOT, "static", "app.css")).replace("\r", "")
    assert re.search(r"margin-bottom:\s*0\b", _css_rule(css, ".pod-head .edlbl"))


def test_pod_tlhead_has_six_px_gap_to_the_canvas_frame():
    """Шапка «Вся запись» отступает от рамки холста на 6 px — как .edtlbar у шага 1 (без зазора кнопки лежали вплотную)."""
    css = _read(os.path.join(ROOT, "static", "app.css")).replace("\r", "")
    assert re.search(r"margin-bottom:\s*6px\b", _css_rule(css, ".pod-tlhead"))


def test_podseg_title_field_stretches_and_fixed_parts_do_not_shrink():
    """П21: поле названия в строке куска растягивается на свободное место (не 0-базовое узкое),
    а время, длительность, кнопки и номер — `flex:none`, чтобы не сжимались первыми."""
    css = _read(os.path.join(ROOT, "static", "app.css")).replace("\r", "")
    title = _css_rule(css, ".podseg-title")
    assert re.search(r"flex:\s*1(\s|;|$)", title), f"поле названия не растягивается: {title}"
    assert re.search(r"min-width:\s*80px", title)
    for selector in (".pod-time", ".podseg-dur", ".podseg button.icon"):
        assert re.search(r"flex:\s*none", _css_rule(css, selector)), f"{selector} может сжиматься"


def test_podseg_title_input_shows_full_name_in_tooltip():
    """П21: полное название куска — в `title` поля (подсказка), обновляется при вводе."""
    js = _read(POD46)
    assert "title.title=title.value" in js
    assert "title.oninput=" in js


# --- П-новое (2026-10-11): строка подкаста как у клипов, корзина, «Все рилсы в очередь» ---

def test_все_рилсы_кнопка_одна_primary_внизу_списка(podcast_block):
    """«Все рилсы в очередь» — одна на блок и после списка подкастов (внизу), не в карточке."""
    assert podcast_block.count('id="podcast_all"') == 1
    assert 'class="primary" id="podcast_all"' in podcast_block
    assert podcast_block.index('id="podcast_list"') < podcast_block.index('id="podcast_all_row"')


def test_строка_подкаста_как_у_клипов_и_без_текстовых_кнопок():
    """Строка — класс .clip (как у клипов шага 1), справа иконки; «Править»/«В рилсы» текстом не остались."""
    src = _read(POD45)
    assert "clip podrow" in src
    assert "t('В рилсы')" not in src and "t('Править')" not in src
    assert "'/api/podcast_delete'" in src and "mbDelPodcast" in src, (
        "удаление подкаста не идёт через окно mbDelPodcast")
    assert "'/api/podcast_convert_all'" in src


# --- удаление подкаста: рилсы внутри его папки уходят вместе с ним (запасной путь без камеры 1) ---

_DELETE_STUB = r"""
const folder='/work/podcasts/Выпуск';
const inside='/work/podcasts/Выпуск/reels/Выпуск_01.mp4';
const cam1='/work/cam1/Выпуск_01.mp4';
const other='/work/podcasts/Другой/reels/Другой_01.mp4';
const sibling='/work/podcasts/Выпуск_2/reels/Выпуск_2_01.mp4';
const seen=[];
function renderQueue(){}
// source — путь проекта (файл с диска или скачанный внутри папки); сегменты — куски с рилсами
function __setup(segs,source){
  fetch=async function(url,opts){seen.push({url:url,body:opts&&opts.body?JSON.parse(opts.body):null});
    if(url.indexOf('/api/podcast_get')===0)return {json:async()=>({ok:true,project:{segments:segs,source:source||''}})};
    if(url==='/api/podcast_delete')return {json:async()=>({ok:true,trash:'x',skipped:[]})};
    return {json:async()=>({ok:true,items:[]})};};
}
function __delBody(){const d=seen.find(s=>s.url==='/api/podcast_delete');return d?d.body:null;}
"""


@NODE
def test_удаление_рилс_внутри_папки_уходит_из_очереди_и_число_в_вопросе(tmp_path):
    """Рилс внутри папки подкаста: в окне названо число, после удаления запись уходит из очереди.
    Рилс в папке камеры 1 и в чужой папке остаются; соседняя папка «Выпуск_2» — не «внутри».
    Внешнего источника нет — галки «И исходник» в окне нет, sources:false."""
    out = _run_pod_js(tmp_path, _DELETE_STUB + r"""
var QUEUE=[[inside],[cam1],[other],[sibling]];
__setup([{reel:inside},{reel:''}]);
(async()=>{
  await podcastDelete(folder,'Выпуск');
  const text=$('delPodText').textContent, opened=OPENED.slice();
  const title=$('delPodTitle').textContent;
  const srcRow=$('delPodSrcRow').style.display;
  await $('delPodOk').onclick();
  console.log(JSON.stringify({text:text,title:title,opened:opened,srcRow:srcRow,closed:CLOSED.slice(),
    body:__delBody(),queue:QUEUE}));
})();
""")
    assert out["opened"] == ["mbDelPodcast"], "удаление подкаста открывает не то окно"
    assert "Reels in its folder: 1" in out["text"], out["text"]
    assert "Выпуск" in out["title"], out["title"]
    assert out["srcRow"] == "none", "галка «И исходник» показана без внешнего источника"
    assert out["closed"] == ["mbDelPodcast"], "окно не закрылось после подтверждения"
    assert out["body"] == {"dir": "/work/podcasts/Выпуск", "sources": False}, out["body"]
    assert out["queue"] == [["/work/cam1/Выпуск_01.mp4"], ["/work/podcasts/Другой/reels/Другой_01.mp4"],
                             ["/work/podcasts/Выпуск_2/reels/Выпуск_2_01.mp4"]]


@NODE
def test_удаление_подкаста_запоминает_папку_подкастов_для_корзины(tmp_path):
    """Корзина подкаста лежит в папке подкастов (рядом с его папкой): после удаления она
    запоминается (trashRemember), чтобы окно «Удалённое» и очистка её видели."""
    out = _run_pod_js(tmp_path, _DELETE_STUB + r"""
var QUEUE=[[cam1]];
var REMEMBERED=[];
function trashRemember(d){REMEMBERED.push(d);}   // 40-queue.js; здесь — заглушка двери
__setup([{reel:cam1}]);
(async()=>{
  await podcastDelete(folder,'Выпуск');
  await $('delPodOk').onclick();
  console.log(JSON.stringify({remembered:REMEMBERED.slice(),body:__delBody()}));
})();
""")
    assert out["body"] == {"dir": "/work/podcasts/Выпуск", "sources": False}, out["body"]
    assert out["remembered"] == ["/work/podcasts"], (
        "папка подкастов не запомнилась для корзины: %r" % out["remembered"])


@NODE
def test_удаление_без_рилсов_внутри_папки_прежний_вопрос_и_очередь_цела(tmp_path):
    """Рилсов внутри папки нет: в окне «останутся», очередь не трогаем."""
    out = _run_pod_js(tmp_path, _DELETE_STUB + r"""
var QUEUE=[[cam1],[other]];
__setup([{reel:cam1}]);
(async()=>{
  await podcastDelete(folder,'Выпуск');
  const text=$('delPodText').textContent;
  await $('delPodOk').onclick();
  console.log(JSON.stringify({text:text,queue:QUEUE}));
})();
""")
    assert "will stay" in out["text"] and "folder" not in out["text"]
    assert out["queue"] == [["/work/cam1/Выпуск_01.mp4"], ["/work/podcasts/Другой/reels/Другой_01.mp4"]]


@NODE
def test_галка_исходника_только_для_файла_вне_папки(tmp_path):
    """«И исходник» — только когда источник выбран с диска и лежит вне папки подкаста.

    Файл снаружи: галка есть, по умолчанию снята, при отметке уходит sources:true. Скачанный
    source.* внутри папки: галки нет вовсе — он уедет вместе с папкой. Поставил, потом снял —
    sources:false.
    """
    out = _run_pod_js(tmp_path, _DELETE_STUB + r"""
(async()=>{
  __setup([{reel:cam1}], cam1);
  await podcastDelete(folder,'Выпуск');
  const ext={row:$('delPodSrcRow').style.display,name:$('delPodSrcName').textContent,
    checkedBefore:!!$('delPodSrc').checked};
  $('delPodSrc').checked=true;
  await $('delPodOk').onclick();
  const ticked=__delBody();
  seen.length=0;
  await podcastDelete(folder,'Выпуск');   // галку не трогаем: окно открывается со снятой
  await $('delPodOk').onclick();
  const unticked=__delBody();
  seen.length=0;
  __setup([{reel:inside}], inside);
  await podcastDelete(folder,'Выпуск');
  const inner=$('delPodSrcRow').style.display;
  console.log(JSON.stringify({ext:ext,ticked:ticked,unticked:unticked,inner:inner}));
})();
""")
    assert out["ext"]["row"] == "", "галки нет у внешнего источника"
    assert out["ext"]["name"] == "Выпуск_01.mp4", "в окне не назван файл источника: %r" % out["ext"]["name"]
    assert out["ext"]["checkedBefore"] is False, "галка «И исходник» стоит по умолчанию"
    assert out["ticked"] == {"dir": "/work/podcasts/Выпуск", "sources": True}, out["ticked"]
    assert out["unticked"] == {"dir": "/work/podcasts/Выпуск", "sources": False}, out["unticked"]
    assert out["inner"] == "none", "галка показана для источника внутри папки подкаста"


# --- иконки строки подкаста: рисуются, подписаны, кнопки работают ---

def _ico_src():
    """ico() и IC из 20-widgets.js — как их видит страница (строки подкаста строятся через ico)."""
    src = _read(os.path.join(ROOT, "static", "app", "20-widgets.js"))
    a = src.index("const IC={")
    return src[a:src.index("\n", src.index("function ico(", a))] + "\n"


@NODE
def test_кнопки_строки_подкаста_рисуют_иконку_и_подписаны(tmp_path):
    """Карандаш, «повтор» и мусорка строки подкаста: в svg есть path, подпись — не имя иконки;
    клик по карандашу открывает правку кусков, по мусорке — окно удаления подкаста."""
    out = _run_pod_js(tmp_path, _ico_src() + r"""
Object.defineProperty(El.prototype,'innerHTML',{configurable:true,
  set(v){this._html=String(v);this.kids=[];}, get(){return this._html||'';}});
let opened=null;
function openPodcastEditor(d){opened=d;}
const DIR='/work/podcasts/Выпуск';
function buttons(card){return card.kids[0].kids[2].kids;}   // строка → .acts (третий ребёнок)
function info(b){return {label:b.getAttribute('aria-label'),dt:b.getAttribute('data-t'),html:b._html};}
(async()=>{
  const ready=podcastCard({dir:DIR,title:'Выпуск',status:'ready',n_segments:2,reels_wanted:5});
  const bReady=buttons(ready);
  bReady[0].onclick();                       // карандаш
  const opened1=opened;
  await bReady[1].onclick();                 // мусорка — окно удаления подкаста
  const err=podcastCard({dir:DIR,title:'Выпуск',status:'error',error:'x',n_segments:1,reels_wanted:5});
  console.log(JSON.stringify({ready:bReady.map(info),error:buttons(err).map(info),
    opened:opened1,asked:OPENED.slice(),delTitle:$('delPodTitle').textContent}));
})();
""")
    buttons = out["ready"] + out["error"]
    assert len(out["ready"]) == 2 and len(out["error"]) == 2
    for b in buttons:
        assert "<path" in b["html"], f"в svg нет path: {b}"
        assert b["label"] and b["label"] not in ("pencil", "trash", "refresh"), b
        assert b["dt"] == b["label"], b
    assert out["ready"][0]["label"] == "Edit segments"          # Править куски
    assert out["ready"][1]["label"] == "Move to trash"          # Убрать в корзину
    assert out["error"][0]["label"] == "Continue processing"    # Продолжить разбор
    assert out["opened"] == "/work/podcasts/Выпуск"
    assert out["asked"] == ["mbDelPodcast"], "мусорка не открывает окно удаления подкаста"
    assert "Delete podcast" in out["delTitle"] and "Выпуск" in out["delTitle"], out["delTitle"]


# --- метка «последним открывал» у подкастов (как у клипов, LASTPOD) ---

@NODE
def test_метка_последнего_подкаста_и_состояние(tmp_path):
    """Строка подкаста, открытого последним, получает класс cur (как .clip.cur у клипов),
    чужая строка — нет. В состоянии поле last уходит и возвращается через podcastApplyState;
    старое состояние без last метку не трогает."""
    out = _run_pod_js(tmp_path, _ico_src() + r"""
const A='/work/podcasts/A', B='/work/podcasts/B';
const card=(dir)=>podcastCard({dir:dir,title:dir,status:'new',n_segments:0,reels_wanted:5});
LASTPOD=A;
const rowA=card(A).kids[0], rowB=card(B).kids[0];
const st=podcastStateObj();
LASTPOD=B;
podcastApplyState({src:'url'});
const kept=LASTPOD;
podcastApplyState(st);
const back=LASTPOD;
console.log(JSON.stringify({clsA:rowA.className,clsB:rowB.className,last:st.last,kept:kept,back:back}));
""")
    assert "cur" in out["clsA"].split(), out
    assert "cur" not in out["clsB"].split(), out
    assert "podrow" in out["clsA"].split() and "clip" in out["clsA"].split(), out
    assert out["last"] == "/work/podcasts/A"
    assert out["kept"] == "/work/podcasts/B", "старое состояние без last стёрло метку"
    assert out["back"] == "/work/podcasts/A", "метка не вернулась из состояния"
