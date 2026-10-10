# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Статические контракты интерфейса (аудит UI 2026-07-28).

Тесты не рендерят страницу — они стерегут ровно те правила DESIGN.md, которые уже
однажды нарушили и которые глазами не ловятся: цвет мимо палитры (через var с
фолбэком незаметно работает), радиус/кегль вне токенов, кликабельный не-контрол без
клавиатуры, модалка без role=dialog. Каждый ассерт — пойманный баг, не «покрытие».

Запуск: python -m pytest reelsi/tests -q
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

CSS = os.path.join(ROOT, "static", "app.css")
# Весь код интерфейса одной строкой: файлов теперь четырнадцать (static/app/),
# и список берётся из папки — проверки не надо чинить при добавлении файла.
from core import app_meta  # noqa: E402
import test_style_keys_in_ui as watcher  # noqa: E402
HTML = os.path.join(ROOT, "templates", "index.html")
# Панель стиля: поля строятся из схемы, а не выписаны в разметке,
# поэтому проверки стиля смотрят в схему + в код панели (см. tests/test_style_keys_in_ui.py).
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _panel_js():
    """Код панели стиля — единственная дверь полей."""
    return _read(PANEL_JS)


def _schema_field(key):
    return watcher.schema_field(key)


def _schema_toggles():
    """Ключи-тумблеры схемы: группу/слой прячет её же галка."""
    return {it["toggle"] for _kind, it in watcher.schema_items() if it.get("toggle")}


def test_batch_master_layout_contract(html, js, css):
    """Пакетные действия находятся сразу после списка файлов, а импорт и инструменты
    не занимают первый экран постоянно."""
    assert '<details class="card gdrive-card">' in html
    assert '<details class="toptools">' not in html
    assert 'class="toolmenu"' not in html
    assert html.index('id="clips3"') < html.index('id="buildbtn"')
    assert 'id="buildscope"' in html
    assert 'id="styleSaveRow"' in html
    assert '.clip.ready{border-color:var(--bd)}' in css
    assert '.ae-buildbar{position:sticky;bottom:0' in css
    assert '.clip.cur{border-left:2px solid var(--tx)}' in css


def test_render_button_keeps_secondary_colors_on_hover(html, css):
    """Кнопка рендера не становится белой при наведении."""
    assert 'id="renderbtn"' in html
    render = html[html.index('id="renderbtn"') - 80:html.index('id="renderbtn"') + 20]
    assert 'class="primary secondary"' in render
    assert '.ae-buildbar .secondary{background:transparent;color:var(--tx);border-color:var(--bd2)}' in css
    assert '.ae-buildbar .secondary:hover{background:var(--panel2);color:var(--tx);border-color:var(--iron);filter:none}' in css


def test_tools_live_in_ai_settings_tab(html, js, css):
    """Инструменты доступны из настроек отдельной вкладкой, а не из шапки."""
    assert 'id="aistabbtn_tools"' in html
    assert 'data-tab="tools"' in html and "aiSetTab('tools')" in html
    assert 'id="aistab_tools"' in html
    assert 'role="tabpanel"' in html and 'aria-labelledby="aistabbtn_tools"' in html
    assert 'id="navvideo"' in html and '<button class="sm" id="navvideo"' in html and '>Открыть</button>' in html and "closeModal('mbAISettings');openVideo()" in html
    assert 'id="illhdr"' in html and '<button class="sm" id="illhdr"' in html and 'База вставок' in html
    assert 'Обновить <span id="illhdrtxt"></span>' in html and 'onclick="illRefresh()"' in html
    assert 'onclick="cleanTmp()"' in html and '<button class="sm" id="navclean"' in html
    assert 'aria-label="Настройки"' in html and 'aria-label="Настройки ИИ"' not in html
    assert '<span class="t">Настройки</span>' in html and '<span class="t">Настройки ИИ</span>' not in html
    assert "'tools'" in js[js.index('function aiSetTab('):js.index('function aiSetTab(') + 500]
    assert '.spkpick{position:relative;min-width:116px;border-top:0;padding-top:0;margin-top:0}' in css
    assert '.toptools{' not in css and '.toolmenu{' not in css


def test_insert_library_has_a_door_in_the_tools_tab(html, js):
    """У окна «База вставок» есть дверь — кнопка в ⚙ → «Инструменты».

    Кнопку «База» из панели вставок убрали (её действия дублировали соседние, см.
    tests/test_gen_btn.py), а вместе с ней ушла и `openInsLib` — единственный вызов
    `openModal('mbInsLib')`. Окно с папками скана, импортом, описаниями и списком
    файлов осталось без входа: кнопка «Обновить базу вставок» пересканирует уже
    настроенную базу, но папки задать не даёт, а её тост отправлял в никуда.

    Ловится именно разрыв «разметка → функция»: inline-обработчики (`onclick="…"`)
    tests/test_ui_js_calls.py не сканирует вовсе — он читает только static/app/*.js,
    поэтому и удаление кнопки, и переименование функции проходят мимо него, а в
    браузере это ReferenceError по клику.
    """
    tools = html[html.index('id="aistab_tools"'):html.index('id="aistab_cut"')]
    assert 'id="mbInsLib"' in html, "окно базы вставок (#mbInsLib) пропало из разметки"
    assert "function openInsLib(" in js, (
        "openInsLib удалена — кнопка в «Инструментах» мертва (ReferenceError по клику)")
    assert 'id="illopen"' in tools and 'openInsLib()' in tools, (
        "в «Инструментах» пропала кнопка «Открыть базу вставок…»")
    # Вход — рядом с «Обновить базу вставок», в том же ряду .setrow: иначе он спрятан
    # в другом углу вкладки, а «Обновить» остаётся единственной кнопкой базы.
    row = tools[tools.index('id="illhdr"'):]
    row = row[:row.index('</div>')]
    assert 'id="illopen"' in row, "кнопка открытия базы стоит не в ряду «База вставок»"
    assert "closeModal('mbAISettings');openInsLib()" in row, (
        "кнопка не закрывает настройки перед открытием базы: у всех .backdrop один "
        "z-index, и окно базы уходит ПОД настройки (решает порядок в DOM)")
    assert 'Открыть базу вставок…' in row, "у кнопки нет подписи «Открыть базу вставок…»"
    assert 'data-t="Что в окне базы вставок' in row, (
        "у кнопки нет справки о том, что в окне: папки скана, импорт, описания, список файлов")
    # Тексты больше не отсылают к убранной кнопке панели вставок, а называют новую дверь.
    # Комментарии не считаем: в них то же имя стоит по делу — как объяснение, откуда ушла
    # кнопка, — а на экран они не попадают (поэтому и `//` в строках-ссылках не мешает:
    # режем хвост строки, а текст тоста стоит до него).
    code = re.sub(r"//[^\n]*", "", js)
    markup = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    assert "«База»" not in code and "«База»" not in markup, (
        "текст снова отправляет к кнопке «База» в панели вставок — её там нет")
    assert code.count("⚙ → Инструменты → База вставок") >= 2, (
        "тосты базы вставок не подсказывают, где она теперь открывается (папки задать "
        "можно только в самом окне)")


def test_ai_settings_has_six_semantic_tabs_and_no_common(html, js):
    """Настройки имеют один стабильный набор вкладок; старое «Общее» не
    остаётся отдельной панелью, но legacy-аргумент common не ломает переход."""
    tabs = re.findall(r'<button[^>]+data-tab="([^"]+)"[^>]*role="tab"', html)
    assert tabs == ["models", "cut", "markup", "generation", "words", "tools"]
    assert 'id="aistabbtn_common"' not in html and 'id="aistab_common"' not in html
    assert "if(tab==='common')tab='tools'" in js
    body = _fn_body(js, "function aiSetTab(")
    assert "allowed=['models','cut','markup','generation','words','tools']" in body
    assert "aria-hidden" in body


def test_settings_markup_generation_tools_diagnostics_and_words_contract(html, js, css):
    markup = html[html.index('id="aistab_markup"'):html.index('id="aistab_generation"')]
    assert all(x in markup for x in ('Задача', 'Модель', 'Ум', 'Жёлтые', 'Вставки', 'Интро'))
    assert all(markup.count(f'data-prof="{x}"') == 1 for x in ('yellow', 'inserts', 'intro'))
    assert all(markup.count(f'data-reas="{x}"') == 1 for x in ('yellow', 'inserts', 'intro'))
    assert markup.count('id="subengine"') == 1
    generation = html[html.index('id="aistab_generation"'):html.index('id="aistab_words"')]
    assert all(generation.count(f'id="{x}"') == 1 for x in ('ais_image', 'ais_rembgRow', 'ais_rembg', 'ais_video', 'vid_model', 'vidres'))
    tools = html[html.index('id="aistab_tools"'):html.index('id="aistab_cut"')]
    assert tools.count('id="dlfmt"') == 1
    models = html[html.index('id="aistab_models"'):html.index('/aistab_models')]
    assert models.count('id="aistats_host"') == 1
    assert models.count('<details class="ais-diagnostics">') == 1
    assert 'id="aistats_host"></div>' in models
    words = html[html.index('id="aistab_words"'):html.index('id="aistab_models"')]
    assert words.count('name="aiswords"') == 2
    assert '<details name="aiswords" open>' in words
    assert '#mbAISettings .modal{height:min(720px,calc(100vh - 80px));' in css


def test_new_tools_ids_are_unique(html):
    """Перенос инструментов не создаёт дублирующихся DOM id."""
    ids = re.findall(r'\bid="([^"]+)"', html)
    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    assert not duplicates, "дублирующиеся id: " + ", ".join(duplicates)


def test_language_button_shows_current_language(js):
    assert "LANG === 'en' ? 'EN' : 'RU'" in js
    assert "if(btn) btn.textContent = 'RU';" in js
    assert "if(btn) btn.textContent = 'EN';" in js


def test_batch_clip_controls_keep_speaker_and_markup_states(js):
    assert 'function spkSelHTML' in js and '<details class="spkpick"' in js
    assert 'this.closest' in js and '.open=false' in js
    for label in ('Добрать вставки', 'Проверить', 'Продолжить', 'Разметить'):
        assert label in js
    # Нативный <select> внутри .spkpick открывал варианты вторым кликом системным
    # попапом — его тут быть не должно, список вариантов свой (.opts).
    start = js.find('function spkSelHTML')
    assert start != -1
    end = js.find('function ', start + len('function spkSelHTML'))
    fn_body = js[start:end] if end != -1 else js[start:]
    assert '<select' not in fn_body
    assert 'class="opts"' in fn_body


def test_ae_words_help_and_style_default(html, js):
    """Справка «Как пользоваться» на месте, а стиль правится панелью по схеме (JB п. 1-2)."""
    assert 'Как пользоваться' in html
    assert 'id="stpanel"' in html and 'role="tree"' in html, (
        "в index.html нет контейнера панели стиля")
    assert "function renderStylePanel(" in js
    assert 'function updateStyleSaveUI' in js
    # Ручные «окна разделов» (openStylePart/closeStylePart) ушли вместе со старой разметкой
    assert "openStylePart(" not in js and "STYLE_PARTS" not in js, (
        "обвязка вкладок #styleparts вернулась — её заменила панель")


def test_howto_details_reset_global_separator(css):
    """Инлайновая справка не наследует разделитель обычных details."""
    assert '.howto{' in css
    assert '.howto{font-size:12px;color:var(--mut);border-top:0;padding-top:0;margin-top:0}' in css


def test_ai_settings_modal_has_fixed_scoped_scroll_contract(css):
    """Смена вкладки не растягивает модалку и не уводит шапку со вкладками."""
    assert '#mbAISettings .modal{height:min(720px,calc(100vh - 80px));max-height:calc(100vh - 80px);' in css
    assert 'display:flex;flex-direction:column;overflow:hidden}' in css
    assert '#mbAISettings .mhead{flex:none}' in css
    assert '#mbAISettings .aislayout{flex:1;min-height:0;overflow:hidden}' in css
    assert '.aispanes{flex:1;min-width:0;min-height:0;overflow:hidden;' in css
    assert 'min-height:544px' not in css
    assert '#mbAISettings .aistab{height:100%;overflow-y:auto;overscroll-behavior:contain}' in css
    assert '@media(max-width:620px)' in css
    assert '#mbAISettings .aispanes{border-left:none;border-top:1px solid var(--bd);flex:1;min-height:0}' in css


@pytest.fixture(scope="module")
def css():
    return _read(CSS)


@pytest.fixture(scope="module")
def js():
    return app_meta.app_js_text()


@pytest.fixture(scope="module")
def html():
    return _read(HTML)


def test_css_variables_all_declared(css, js):
    """Каждая var(--x) объявлена в :root.

    Вкладка «Видео» ссылалась на --card2/--inp/--gold/--mono, которых нет: var()
    молча берёт фолбэк, и цвета жили мимо палитры, пока их никто не измерил.
    """
    root = re.search(r":root\{(.*?)\}", css, re.S)
    assert root, "в app.css пропал блок :root с токенами"
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root.group(1)))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css + js))
    assert not (used - declared), (
        "переменные без объявления в :root: " + ", ".join(sorted(used - declared)))


def test_no_font_below_11px(css, js, html):
    """Кегль ниже 11px запрещён (DESIGN, «Типографика»)."""
    bad = []
    for name, text in (("app.css", css), ("app.js", js), ("index.html", html)):
        for m in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", text):
            if float(m.group(1)) < 11:
                bad.append("%s: %spx" % (name, m.group(1)))
    assert not bad, "кегль ниже минимума: " + ", ".join(bad)


def test_radius_only_from_tokens(css, js, html):
    """Радиусов ровно три (--r/--r-sm/--r-pill); 50% — круг, 0 — сброс.

    Значение бывает угловой четвёркой («0 var(--r-sm) var(--r-sm) 0» у ручки блока) —
    проверяем каждый угол отдельно. Исключение одно: галочка чекбокса рисуется
    псевдоэлементом, её 1px — скругление штриха, а не угол контрола.
    """
    css = re.sub(r"input\[type=checkbox\]::after\{[^}]*\}", "", css)
    bad = []
    for name, text in (("app.css", css), ("app.js", js), ("index.html", html)):
        for m in re.finditer(r"border-radius:\s*([^;\"'}]+)", text):
            for part in m.group(1).strip().split():
                if part in ("0", "50%") or part.startswith("var(--r"):
                    continue
                bad.append("%s: %s" % (name, part))
    assert not bad, "радиус вне токенов: " + ", ".join(bad)


def test_clickable_non_buttons_are_focusable(js):
    """Кликабельный не-контрол обязан быть в tab-порядке (DESIGN, чек-лист п.5).

    Крестики очереди пар и карточек вставок были <span onclick> без tabindex —
    без мыши вставку нельзя было ни удалить, ни отцепить от неё картинку.
    """
    bad = []
    for m in re.finditer(r"<(?:span|td|div)\s+class=\"(?:x|del|tagx|rm)\b[^>]*", js):
        tag = m.group(0)
        if "onclick=" in tag and "tabindex=" not in tag:
            bad.append(tag[:90])
    assert not bad, "кликабельно, но не фокусируемо: " + " | ".join(bad)


def test_modals_are_dialogs(html):
    """Каждая модалка объявлена диалогом и имеет имя."""
    modals = re.findall(r"<div class=\"modal[^\"]*\"[^>]*>", html)
    assert len(modals) >= 6, "модалок стало меньше — тест устарел?"
    for m in modals:
        assert 'role="dialog"' in m and 'aria-modal="true"' in m, "без role/aria-modal: " + m[:80]
        assert "aria-label" in m or "aria-labelledby" in m, "диалог без имени: " + m[:80]


def test_tabs_expose_selection(html, js):
    """Вкладки ⚙ сообщают выбранную не только классом (screen reader читает aria-selected)."""
    assert html.count('aria-selected=') >= 3, "у вкладок нет aria-selected"
    assert "aria-selected" in js, "aiSetTab не обновляет aria-selected"


def test_help_lives_in_tooltips(js):
    """Пустое состояние референсов — строка действия, а не абзац справки."""
    m = re.search(r"Референсов нет[^']*", js)
    assert m, "пустое состояние #vidrefs пропало"
    assert len(m.group(0)) < 80, "справка вернулась в видимый текст: " + m.group(0)[:120]


def test_tooltip_delay_skips_exclamation_marks(js):
    """Справка на контролах — с задержкой, «!»-кружки — сразу (DESIGN, правка юзера №5).

    Мгновенная карточка под каждым элементом мешала при движении мыши по панели:
    навёл мимоходом — получил подсказку. Задержка ставится только на контролы,
    «!»-кружок (.i) — осознанный заход за справкой, его трогать нельзя.
    """
    assert re.search(r"const TIP_DELAY_MS=\d+;", js), "задержка подсказок пропала (TIP_DELAY_MS)"
    start = js.index("function tipShowDelayed")
    body = js[start:start + 800]
    assert "classList.contains('i')" in body, "«!»-кружок обязан показываться без задержки"
    assert js.count("if(el)tipShowDelayed(el)") == 2, "mouseover и focusin должны идти через tipShowDelayed"


def test_nothing_shadows_the_translator_t():
    """Имя `t` занято функцией перевода — своей переменной `t` в static/app/ нет.

    Пойманный баг (2026-08-10): i18n-проход обернул строки в `t('…')` внутри
    `ipvUI(t)`, `ipvOverlay(t)` и `insAdd` (там `const t=` — время плейхеда). Там `t`
    — ЧИСЛО, и вызов падал «t is not a function». Молча ломалось полокна вставок и
    предпросмотр AE: не таскался плейхед по таймлайну (обработчик умирал до
    навешивания pointermove), не работал ползунок перемотки, из двух наехавших
    вставок рисовалась только первая, ни один блок не получал .cur — то есть на
    таймлайне «не нажималась» ни одна вставка, — а кнопка генерации картинки не
    доходила до fetch вообще.

    Первая версия теста стерегла «`t()` внутри функции, где `t` перекрыта» — то есть
    ждала, пока мину заденут. Правило переписано на запрет САМОГО имени (2026-08-10,
    время везде переименовано в `tm`): перевод покрыт не весь, и следующая строка,
    дописанная в `pvUI`/`cpvUI`/`ipvIntro`, наступила бы на то же самое. Проверять
    «нет такого имени» и дешевле, и без эвристик про области видимости.
    """
    from core import app_meta as _am
    binds = [
        ("параметр стрелки", re.compile(r"(?<![\w.$])t\s*=>")),
        ("параметр стрелки", re.compile(r"\(\s*(?:[\w$]+\s*,\s*)*t\s*(?:,[^)]*)?\)\s*=>")),
        ("параметр функции", re.compile(r"function\s*[\w$]*\s*\(\s*(?:[\w$]+\s*,\s*)*t\s*(?:,[^)]*)?\)")),
        ("объявление", re.compile(r"\b(?:const|let|var)\s+t\s*[=;,]")),
        ("for-of/in", re.compile(r"\bfor\s*\(\s*(?:const|let|var)\s+t\s+(?:of|in)\b")),
        ("catch", re.compile(r"\bcatch\s*\(\s*t\s*\)")),
    ]
    bad = []
    for path in _am.app_js_files():
        for n, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
            if line.strip().startswith("//"):
                continue
            for what, rx in binds:
                if rx.search(line):
                    bad.append("%s:%d %s t -> %s" % (
                        os.path.basename(path), n, what, line.strip()[:70]))
                    break
    assert not bad, ("имя t занято функцией перевода — переименуй "
                     "(время: tm, элемент: el):\n" + "\n".join(bad))


def _js_code_only(line):
    """Строка кода без строковых литералов и хвостового `//`-комментария."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "'\"`":
            q, i = ch, i + 1
            while i < n:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def test_t_is_only_ever_called_never_passed_as_value():
    """`t` в static/app/ бывает только вызовом `t(…)` — значением её не передают.

    Пойманный баг (2026-08-11): переименование `t`->`tm` в линейке редактора
    (`for(let tm=…)`) не дошло до самой подписи — там осталось `fmtT(t)`, то есть в
    форматирование времени уезжала ФУНКЦИЯ ПЕРЕВОДА. `Math.floor(функция)` = NaN, и
    вся линейка таймлайна нарезки была подписана «NaN:NaN» на всех зумах.

    Тест выше стережёт ОБЪЯВЛЕНИЯ своих `t` — а тут `t` больше не объявляли, остался
    висеть ИСПОЛЬЗОВАНИЕМ. Ошибка тихая: исключения нет, тесты зелёные, видно только
    глазами на картинке. Правило простое и без эвристик про области видимости: имя
    `t` вне вызова — опечатка. Ключ объекта (`{t:текст}` в подстановках перевода)
    исключён — это имя переменной шаблона, а не обращение к функции.
    """
    from core import app_meta as _am
    rx = re.compile(r"(?<![\w$.])t(?![\w$])(?!\s*[(:])")
    bad = []
    for path in _am.app_js_files():
        for n, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
            if line.strip().startswith("//"):
                continue
            if rx.search(_js_code_only(line)):
                bad.append("%s:%d %s" % (os.path.basename(path), n, line.strip()[:70]))
    assert not bad, ("t — функция перевода, значением её не передают "
                     "(время: tm):\n" + "\n".join(bad))


def test_editor_grabs_the_nearest_edge_not_the_first_one(js):
    """Под курсором хватается БЛИЖАЙШИЙ край блока, а не первый попавшийся слева.

    Пойманный баг (2026-08-11): цикл поиска края шёл слева направо и брал первый край
    в допуске. Допуск — 8px, а на общем зуме (163с на ~1300px) это ЦЕЛАЯ СЕКУНДА
    исходника, и у любой убранной паузы (0.3-0.8с) оба края щели ближе друг к другу.
    Замер на живом клипе: щель 21.128-21.473 — 2.8px. Целишься точно в НАЧАЛО
    следующего блока — хватается КОНЕЦ предыдущего; тянешь вправо, а упор у него ровно
    в начало следующего, и щель съедается ЦЕЛИКОМ. После такой правки плеер играет то,
    что считалось вырезанным, и «не переходит к следующему блоку» — переходить некуда.

    Плейхед участвует в том же сравнении и выигрывает только вничью: он нарисован
    поверх, но забирать себе край, до которого дальше, он не должен.
    """
    assert "function edGrabAt(" in js, "выбор края под курсором пропал"
    body = js[js.index("function edGrabAt("):][:500]
    assert "d<best.d" in body, "край выбирается не по минимуму расстояния — вернулся первый попавшийся"
    assert "dh<=best.d" in body, "плейхед должен выигрывать только вничью"
    down = js[js.index("c.addEventListener('mousedown'"):][:900]
    assert "edGrabAt(x)" in down, "mousedown ищет край мимо общего выбора"
    assert "edS2X(b.s0))<" not in down, "в mousedown вернулся посимвольный перебор краёв"


def test_cleared_insert_is_not_refilled_by_itself(js):
    """Снял картинку крестиком — ни автоподбор из базы, ни автогенерация её не вернут.

    Раньше возвращали: в базе файл при снятии НЕ бракуется (и правильно — брак портил
    базу другим роликам), значит на следующем же проходе он снова первый по score.
    Юзер убирал картинку, добирал вставки ИИ — и получал ровно ту же обратно.
    """
    assert "x.noAuto=true" in js, "insClearMedia больше не помечает вставку noAuto"
    fill = js[js.index("async function insLibFill"):js.index("async function insLibAuto")]
    assert "!x.noAuto" in fill, "автоподбор из базы игнорирует noAuto"
    gen = js[js.index("async function insGenBatch"):js.index("async function insGenMissing")]
    assert "!x.noAuto" in gen, "автогенерация игнорирует noAuto (и тратит деньги)"


def test_image_gen_never_locks_the_button_forever(js):
    """Кнопка генерации картинок не залипает в «…» навсегда.

    insGenOne держит x.genBusy=true до возврата fetch: зависший сервер (запрос без
    таймаута в rembg/записи в базу) запирал кнопку, а гвардия двойного клика
    глотала все нажатия — снять могла только перезагрузка страницы. У fetch
    появился AbortController-таймаут, а bfcache-переходы сбрасывают флаг.
    """
    core = js[js.index("async function insGenCore"):js.index("async function insGenOne")]
    assert "GEN_FETCH_MS" in core, "константа таймаута генерации пропала"
    assert "signal:ac.signal" in core, "fetch генерации не отменяется по таймауту"
    assert "clearTimeout(timer)" in core, "таймер отмены не убирается после ответа"

    rembg = js[js.index("async function insRembg"):js.index("const GEN_FETCH_MS")]
    assert "signal:ac.signal" in rembg, "rembg не отменяется по таймауту"

    boot = js[js.index("// ================= boot"):]
    assert "pageshow" in boot and "x.genBusy=false" in boot, (
        "bfcache-переход не сбрасывает зависший genBusy")
    st = js[js.index("function stateObj()"):js.index("function saveState()")]
    assert "delete xx.genBusy" in st, (
        "залипший genBusy снова уезжает в состояние и переживает F5")
    app_ = js[js.index("function applyState(s)"):js.index("function restoreState()")]
    assert "delete x.genBusy" in app_, (
        "applyState не чистит залипший genBusy из старого сохранённого состояния")


def test_curae_never_points_past_clips(js):
    """Индекс открытого клипа не переживает сам клип.

    curAE хранится в состоянии рядом со списком, но правился отдельно: delClip резал
    CLIPS, не трогая индекс. После удаления последнего клипа (сборка нового набора)
    состояние сохранялось как curAE=0 при пустом списке, и первый же captureAE падал
    на CLIPS[curAE].job. Падал он внутри try у loadStyles — юзер видел «Стили не
    загрузились — сервер не ответил» при живом сервере.
    """
    cap = js[js.index("function captureAE()"):]
    cap = cap[:cap.index("\n//")]
    assert "if(!c){curAE=-1;return;}" in cap, "captureAE снова верит, что CLIPS[curAE] существует"

    dele = js[js.index("function delClip("):]
    dele = dele[:dele.index("\n\n")]
    assert "curAE" in dele, "delClip режет список, не поправив индекс открытого клипа"

    apply_ = js[js.index("function applyState(s)"):js.index("function restoreState()")]
    assert "if(curAE>=CLIPS.length)curAE=-1" in apply_, (
        "applyState не подрезает curAE под восстановленный список")


def test_step1_row_controls_order_and_dedupe_in_state(html, js):
    """Порядок контролов в ряду шага 1 (ИИ-нарезка, Кастом, Omni-ревью):
    галки dedupe и draft убраны со страницы, остались кнопки запуска и Omni-ревью;
    ключ dedupe в состоянии UI сохраняется и восстанавливается при F5."""
    assert 'id="chk_dedupe"' not in html, "chk_dedupe должна быть убрана со страницы"
    assert 'id="chk_draft"' not in html, "chk_draft должна быть убрана со страницы"
    pos_runai = html.index('id="runai"')
    pos_runclassic = html.index('id="runclassic"')
    pos_review = html.index('id="chk_review"')
    assert pos_runai < pos_runclassic < pos_review, "порядок контролов в ряду шага 1 нарушен"
    # состояние UI: пишется при сохранении и читается при восстановлении (F5)
    st = js[js.index("function stateObj()"):js.index("function saveState()")]
    assert "dedupe:" in st, "состояние не хранит dedupe"
    app_ = js[js.index("function applyState(s)"):js.index("function restoreState()")]
    assert "dedupe" in app_, "applyState не восстанавливает dedupe"


def test_capture_ae_writes_only_the_clip_loaded_into_the_panel(js):
    """Панель AE пишется в задание только того клипа, который в неё загрузили.

    curAE переживает F5, а панель после перезагрузки пустая: вставок нет, интро нет,
    жёлтых нет. При этом captureAE зовут и со стороны: onStyleChange — при загрузке
    стилей на старте и при выборе спикера на шаге 1. В результате клип, открытый в
    прошлый раз на шаге AE, молча терял ИИ-вставки, жёлтые и интро при обычном
    обновлении страницы (поймано 2026-08-08 по ui_state: c.inserts=13, job.ins=0).
    """
    cap = js[js.index("function captureAE()"):js.index("function clipNcams(c)")]
    assert "if(AEXML!==c.xml)" in cap, "captureAE снова пишет панель в чужой (незагруженный) клип"

    sel = js[js.index("function selectAE(i)"):js.index("function captureAE()")]
    assert "AEXML=c.xml" in sel, "selectAE не отмечает, чей клип теперь в панели"
    assert sel.index("captureAE()") < sel.index("AEXML=c.xml"), (
        "AEXML переставлен ДО captureAE — предыдущий клип запишется под новым именем")

    open_ = js[js.index("function openAEFor(i)"):js.index("function selClips()")]
    assert "AEXML!==CLIPS[i].xml" in open_, (
        "клип открывается по одному индексу: после F5 панель покажет чужие вставки и стиль")


def test_ai_intro_answer_lands_in_the_clip_it_was_asked_for():
    """Ответ «ИИ интро» уезжает в задание СПРОШЕННОГО клипа, а панель — только если он открыт.

    Пойманный баг (жалоба владельца): aiIntroRun держал клип индексом curAE, результат клал
    в ГЛОБАЛЬНУЮ INTRO и записывал вызовом captureAE() — а тот пишет в CLIPS[curAE], то есть
    в клип, открытый В МОМЕНТ ОТВЕТА. Модель думает минутами: открыл за это время другой
    клип — его разметка затёрта чужими строками, а спрошенный не получил ничего. Защита
    `if(AEXML!==c.xml)` внутри captureAE тут не спасает: после переключения AEXML уже равен
    xml ВТОРОГО клипа, и запись считается «своей». Образец — пакетный aiIntroAllRun:
    ссылка на клип, запись в c.job.introRows, панель под гвардией «клип всё ещё открыт».
    """
    ae = _read(os.path.join(ROOT, "static", "app", "90-ae.js"))
    # Комментарии не считаем: в них те же имена по делу (captureAE() — в объяснении бага).
    body = re.sub(r"//[^\n]*", "", _func(ae, "aiIntroRun"))
    # 1. Клип запоминается ССЫЛКОЙ на момент запуска: индекс переставляется сортировкой и удалением.
    assert "const c=CLIPS[curAE]" in body, "клип снова берётся индексом после ответа"
    # 2. Результат пишется в задание этого клипа, а не в глобальную панель INTRO.
    assert "c.job.introRows=introRowsFromAI(d)" in body, (
        "разметка не уезжает в задание спрошенного клипа")
    assert "INTRO=introRowsFromAI(d)" not in body, (
        "результат снова кладётся в глобальную INTRO — панель чужого клипа уедет в его задание")
    # 3. Гвардия «целевой клип всё ещё открыт» — ПОСЛЕ ответа модели: до await открыт ещё тот,
    #    что спрашивали, а панель трогается только под гвардией.
    wait = body.index("await aiFetch(")
    guard = body.index("c===CLIPS[curAE]")
    assert guard > wait, "нет проверки «целевой клип всё ещё открыт» после ответа ИИ"
    assert "AEXML===c.xml" in body[guard:guard + 60], (
        "гвардия не сверяет, что панель принадлежит целевому клипу (AEXML)")
    for call in ("renderIntro()", "captureAE()", "aewRender()"):
        assert call in body, "панель интро больше не обновляется: " + call
        assert body.index(call) > guard, (
            "безусловный " + call + " до гвардии — панель открытого клипа перетрётся ответом")
    # 4. #introres — строка открытого клипа: «готово» по чужому ответу не пишем, а закрытому
    #    клипу разметка сохраняется и об этом честно пишется в лог (с именем клипа).
    assert body.index("el.className='ok'") > guard, (
        "#introres красится в «готово» даже когда целевой клип уже закрыт")
    assert "saveState()" in body[guard:], "закрытый клип: разметка не сохраняется"
    assert "уехало в задание клипа" in body[guard:], (
        "в логе не сказано, в задание какого клипа уехала разметка, когда панель занята другим")


def test_single_ai_doors_keep_their_clip_by_reference():
    """Соседние одиночные ИИ-двери держат клип ссылкой — тот же класс гонки, но их писать не надо.

    Ответ висит минутами, клип за это время открывают другой. У вставок (aiInsertsRun,
    aiInsertsMore) результат сразу пишется в объект клипа (`const c=CLIPS[curIns]` → `c.inserts`),
    а панель рисуется из CLIPS[curIns], то есть по открытому клипу. У жёлтых — в `c.status`
    и `clearHl(c)`, а панель слов грузится только под гвардией «этот клип открыт».
    Тест держит это свойство: новая правка не должна вернуть запись через CLIPS[curIns]/curAE.
    """
    ins = _read(os.path.join(ROOT, "static", "app", "80-inserts.js"))
    run = _func(ins, "aiInsertsRun")
    assert "const c=CLIPS[curIns]" in run and "c.inserts=(d.inserts||[]).map" in run, (
        "aiInsertsRun пишет ответ ИИ не в тот клип, для которого спрашивал")
    more = _func(ins, "aiInsertsMore")
    assert "const c=CLIPS[curIns]" in more and "c.inserts=cur.concat(" in more, (
        "aiInsertsMore пишет ответ ИИ не в тот клип, для которого спрашивал")

    ed = _read(os.path.join(ROOT, "static", "app", "70-editor.js"))
    assert "c.status.colored=(d.colored||d.yellow||[]).length" in ed, (
        "жёлтые больше не пишутся в статус клипа")
    assert ed.count("if(curAE>=0&&CLIPS[curAE]===c)loadWordsFor(c.xml)") == 2, (
        "жёлтые снова грузят панель слов без проверки, что этот клип открыт")


def test_cut_results_land_in_the_list_one_by_one(js, html):
    """Нарезанный клип виден на шаге 1 сразу, а не после всей очереди.

    Сервер пополняет JOB["results"] после КАЖДОГО файла, но клиент забирал их только
    в onDone: пока режется очередь на час, готовые клипы лежали на диске и править их
    было нечем. Теперь тот же список наполняет onTick на каждом опросе.
    """
    poll = js[js.index("async function pollJob"):js.index("// ---- render clip lists ----")]
    assert "onTick" in poll and "if(onTick)onTick(d)" in poll, (
        "pollJob снова отдаёт результаты только по окончании джоба")
    assert "d=>{cutAdopt(d);progReadySet(" in poll, (
        "нарезка не забирает готовые клипы на каждом опросе")
    adopt = js[js.index("function cutAdopt(d)"):js.index("async function pollAI")]
    assert "clipByXml(full)" in adopt, "cutAdopt добавит один и тот же клип на каждом опросе"
    assert 'id="progReady"' in html and "function progToReady" in js, (
        "из оверлея прогресса не уйти к готовым клипам — он перекрывает страницу")


# Двери длинных операций: на экране обязан быть контекст очереди — заголовок операции,
# «клип i из N» и имя клипа (требование «прогресс везде как у AE», эталон — нарезка/сборка).
PROG_DOORS = (
    ("40-queue.js", "function jobProg(d,eager,title)"),      # нарезка/сборка — эталон
    ("70-editor.js", "async function markupClip(c"),         # разметка ОДНОГО клипа (c, batch)
    ("70-editor.js", "async function markupAllRun("),        # пакетная разметка (фазы)
    ("90-ae.js", "async function aiIntroAllRun(list)"),      # ИИ интро на набор
    ("90-ae.js", "async function pollRender()"),             # рендер AE
)


def _app_functions():
    """Функции всех static/app/*.js: (файл, имя, тело). Тело — по балансу скобок."""
    from core import app_meta as _am
    for path in _am.app_js_files():
        src = _read(path)
        for m in re.finditer(r"(?:async\s+)?function\s+([\w$]+)\s*\(", src):
            i = src.index("{", m.end() - 1)
            depth = 0
            for j in range(i, len(src)):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        yield os.path.basename(path), m.group(1), src[m.start():j + 1]
                        break


def _prog_update_runs_in_a_loop(body):
    """Зовётся ли progUpdate( внутри цикла — то есть в длинном проходе по клипам."""
    for m in re.finditer(r"\b(?:for|while)\s*\(", body):
        depth = 0
        for j in range(m.end() - 1, len(body)):     # парная скобка условия цикла
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        k = j + 1
        if k < len(body) and body[k] == "{":        # тело в скобках — до парной закрывающей
            d2 = 0
            for e in range(k, len(body)):
                if body[e] == "{":
                    d2 += 1
                elif body[e] == "}":
                    d2 -= 1
                    if d2 == 0:
                        break
            chunk = body[k:e + 1]
        else:                                       # тело без скобок — до `;`
            chunk = body[k:body.find(";", k) + 1]
        if "progUpdate(" in chunk:
            return True
    return False


def _prog_update_calls(body):
    """Аргументы каждого вызова progUpdate( — по запятым верхнего уровня.

    Строковые литералы пропускаются: в них встречаются и запятые, и скобки
    («жёлтые слова (ИИ)…»), из-за которых регулярка посчитала бы аргументы неверно.
    """
    calls = []
    for m in re.finditer(r"progUpdate\s*\(", body):
        i, n, depth, args, cur = m.end(), len(body), 1, [], ""
        while i < n and depth:
            ch = body[i]
            if ch in "'\"`":
                q, i = ch, i + 1
                while i < n:
                    if body[i] == "\\":
                        i += 2
                        continue
                    if body[i] == q:
                        i += 1
                        break
                    i += 1
                cur += "''"
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
            if ch == "," and depth == 1:
                args.append(cur.strip())
                cur = ""
                i += 1
                continue
            cur += ch
            i += 1
        args.append(cur.strip())
        calls.append(args)
    return calls


def test_every_long_operation_shows_its_clip_and_the_queue():
    """Прогресс длинной операции отвечает, над каким клипом работа идёт и сколько в очереди.

    Пойманный дефект (задание «единый прогресс»): progUpdate(frac, stage, title, sub)
    рисует три поля, но title и sub необязательны — кто их не передал, у того на экране
    остаётся контекст прошлого вызова или пустота. Разметка ОДНОГО клипа (markupClip)
    писала «жёлтые слова (ИИ)…» без имени клипа и без очереди, ИИ интро — «файл N из M»
    без этапа (пока модель думает, экран не менялся), рендер AE передавал sub=null.

    Правило механическое, а не «посмотри глазами»: контекст очереди (progQueue) живёт
    отдельно от этапа (progStep), у каждой двери он выставляется, и progUpdate( внутри
    цикла без контекста очереди — дефект. Тест краснеет, если вернуть в markupClip
    старую строку progUpdate(null,t('жёлтые слова (ИИ)…'));
    """
    # 1. Двери: контекст очереди есть, а этапы внутри двери идут через progStep — голый
    #    progUpdate(null, …) затёр бы строку очереди этапом.
    bad = []
    for fname, marker in PROG_DOORS:
        src = _read(os.path.join(ROOT, "static", "app", fname))
        assert marker in src, f"{fname}: пропала дверь длинной операции ({marker})"
        body = _fn_body(src, marker)
        if "progQueue(" not in body and "progStep(" not in body:
            bad.append(f"{fname}: {marker} не выставляет контекст очереди")
        for args in _prog_update_calls(body):
            if args[0] == "null" and len(args) < 3:   # «progUpdate(null, этап)» без заголовка
                bad.append(f"{fname}: {marker} ставит этап голым progUpdate(null, …) — "
                           "имя клипа и номер в очереди при этом теряются")
    assert not bad, "длинная операция без ответа «над каким клипом»:\n" + "\n".join(bad)

    # 2. Общее правило по исходнику: цикл, который обновляет прогресс, обязан выставить
    #    контекст очереди (progQueue или progStep) — иначе он рисует этап без клипа.
    bad = []
    for fname, name, body in _app_functions():
        if not _prog_update_runs_in_a_loop(body):
            continue
        if "progQueue(" in body or "progStep(" in body:
            continue
        bad.append("%s: %s" % (fname, name))
    assert not bad, ("цикл обновляет прогресс, не выставив контекст очереди "
                     "(нужен progQueue/progStep):\n" + "\n".join(bad))


def _save_speaker(js):
    return js[js.index("async function saveSpeaker()"):js.index("async function delSpeaker()")]


def test_speaker_editor_keeps_fields_it_does_not_show(js):
    """Окно профиля правит КОПИЮ файла, а не собирает объект из своих полей.

    В speakers/*.json есть то, чего в окне нет: `ref` (референсные кадры под
    автоопределение по лицу) и `breath_model`. Сборка «с нуля» стёрла бы их молча —
    вылезло бы только на следующем прогоне, уже без данных.
    """
    body = _save_speaker(js)
    assert "JSON.parse(JSON.stringify(SPEAKERS[SPKEDIT]))" in body, (
        "saveSpeaker собирает профиль заново — поля вне окна потеряются")
    assert "d.key!==SPKEDIT" in body and "delspeaker" in body, (
        "после переименования старый файл остаётся вторым профилем в списке")


def test_speaker_editor_writes_only_real_overrides(js):
    """Порог, равный общему, в профиль не пишется.

    Пустое поле = общее значение (дефолт показан плейсхолдером). Иначе «профиль без
    правок» сохранился бы с шестнадцатью «своими» порогами, равными общим, и правка
    констант gigaam_cut до этого спикера больше никогда бы не дошла.
    """
    body = _save_speaker(js)
    assert "if(v!==d)cut[k]=v;" in body, "число, равное дефолту, уезжает в профиль"
    assert "if(el.checked!==d)cut[k]=el.checked;" in body, "галка в дефолте уезжает в профиль"
    grid = js[js.index("function spkGrid(cut)"):js.index("async function saveSpeaker()")]
    assert "placeholder=" in grid, "дефолт не подписан — пустое поле читается как «ничего»"


def test_ae_style_is_a_speaker_default_not_a_lock(js):
    """Стиль из профиля подставляется, но правка стиля в профиль не возвращается.

    Стилей больше, чем спикеров (одна камера / две / другой шрифт у того же
    человека), поэтому связь односторонняя: спикер даёт стиль по умолчанию, а на
    шаге сборки он меняется свободно.
    """
    style_edit = js[js.index("function onStyleChange()"):js.index("async function loadFonts()")]
    assert "savespeaker" not in style_edit, "смена стиля переписывает профиль спикера"
    assert "styleSpeakerNote" in style_edit, "не видно, чей стиль стоит и что было у спикера"
    spk = js[js.index("function onSpeakerChange()"):js.index("// ---- редактор профиля спикера ----")]
    assert "STYLES[p.style]" in spk and "не найден" in spk, (
        "пропавший стиль профиля снова игнорируется молча — клип соберётся чужим")


def test_localstorage_keys_are_migrated_not_just_renamed(js):
    """Переименование AutoCut -> Reelsi не должно стирать состояние страницы.

    Состояние мастера (шаг, набор, громкость, настройки базы вставок) живёт ТОЛЬКО
    в localStorage — с диска до него не дотянуться. Сменить ключи без переноса
    значит молча обнулить юзеру работу: открыл после обновления, а там пустой
    мастер. Перенос обязан быть до первого чтения и не затирать уже существующее.
    """
    assert "autocut2_" in js, "пропал перенос старых ключей — состояние юзера потеряется"
    mig = js[:js.index("// ================= helpers")]
    assert "autocut2_" in mig, "перенос ключей должен идти ДО первого чтения состояния"
    assert "localStorage.getItem(newK)===null" in mig, (
        "перенос затирает уже существующее значение нового ключа")
    assert "removeItem" not in mig, (
        "старые ключи удалять нельзя: откат на прошлую версию обнулит UI")
    # рабочие чтения/записи — только по новым ключам
    body = js[js.index("// ================= helpers"):]
    assert "autocut2_" not in body, "остались рабочие обращения к старым ключам"
    for key in ("reelsi_step", "reelsi_vol", "reelsi_inslib", "reelsi_state"):
        assert key in js, f"ключ {key} не заведён"


def test_preview_swaps_video_instead_of_seeking_at_the_cut(js):
    """На стыке живой <video> камеры 1 подменяется дублёром, а не сеcится на месте.

    `av.currentTime=a.src` в момент склейки — честный seek декодера (флаш буфера ->
    ключевой кадр -> декод вперёд): на 4K long-GOP 0.2-0.4 с замирания вместе со
    звуком (он с того же элемента). Именно «в разрезе», потому что на смежных кусках
    seek не делается вовсе. Лечится дублёром с разбегом; seek на месте остался только
    запасным путём, когда дублёр не успел.

    Плеер шага 1 (нарезка) с 02.10.2026 — один: редактор (ED). Монтажный pvTick убран
    вместе с блоком «Монтаж», и стык исходника в шаге 1 ведёт edTick; общий шаг pvStep
    по-прежнему один на шаг 3 (IPV) и раскладку камер (CPV).
    """
    step = js[js.index("function pvStep(P)"):js.index("// Своих часов воспроизведения")]
    assert "const done=spareSwap(P);" in step, "pvStep не пробует подмену дублёром"
    assert "if(Math.abs(live.currentTime-a.src)>0.06)" in step, (
        "seek на месте должен остаться ЗАПАСНЫМ путём (короткий сегмент, дублёр не успел)")
    assert "if(av.seeking){tm=a.ts;}" in step, (
        "во время seek время из currentTime не считается — иначе блоки проскакивают пачкой")
    # шаг 1 — редактор: картинка догоняет звук, стык — через дублёра (edFollow -> edJump)
    follow = js[js.index("function edFollow()"):js.index("function edArm()")]
    assert "edJump(v,cs)" in follow, "редактор снова сеcит живой <video> прямо на стыке блока"
    # шаг 3 и раскладка камер идут через один общий шаг (было три копии, и гонка правилась трижды)
    for player, head in (("IPV", "function ipvStep()"), ("CPV", "function cpvStep()")):
        body = js[js.index(head):js.index(head) + 300]
        assert f"pvStep({player})" in body, f"{player}: стык не идёт через общий pvStep"
    assert "function pvTick(" not in js, "вернулся второй плеер шага 1 (pvTick)"
    # дублёр обязан жить ВНЕ P.vids: иначе pvVisual покажет его как отдельную камеру
    take = js[js.index("function bufTake(P,b,at)"):js.index("function spareLead(P)")]
    assert "P.vids[b.slot]=b.el" in take and "b.el=old" in take, (
        "подмена больше не меняет элементы местами (у камер — слот в P.vids, у дорожки голоса — её <audio>)")
    assert "bufSwap(P,b)" in take, "подмена идёт мимо общего обмена ролями"
    assert "P.bufs.forEach(b=>{b.el.volume=MEDIA_VOL;})" in js, (
        "громкость мимо дублёров: после подмены они выходят в эфир и уровень прыгнет")


@pytest.mark.parametrize("player,step,seek,pause,scrub", [
    ("IPV", "function ipvStep()", "function ipvSeekTo(tm)", "function ipvPause()", "function ipvScrub(x)"),
    ("CPV", "function cpvStep()", "function cpvSeekTo(tm)", "function cpvPause()", "function cpvScrub(x)"),
])
def test_every_preview_player_uses_the_double_buffer(js, player, step, seek, pause, scrub):
    """Дублёр подключён во ВСЕХ плеерах, а не только на главной странице.

    Вставки/AE (IPV) и раскладка камер (CPV) — один и тот же контракт
    {audio:[{ts,te,src}], aidx} и один и тот же стык с seek'ом. Подмена на стыке и разбег
    к нему — в общей pvStep (одна копия, а не три расходящиеся); сами step-функции пускают
    дублёра вживую до стыка (spareRollAt). Шаг 1 (нарезка) в этом списке больше нет: там
    один плеер — редактор, и его стык идёт через edTick/edJump (см. тест выше).
    """
    def body(head, n=1400):
        return js[js.index(head):js.index(head) + n]
    pvs = js[js.index("function pvStep(P)"):js.index("// Своих часов воспроизведения")]
    assert "spareSwap(P)" in pvs, "pvStep: стык не идёт через подмену дублёром"
    assert "sparePrime(P)" in pvs, "pvStep: разбег к следующему стыку не готовится"
    assert f"pvStep({player})" in body(step, 300), f"{player}: шаг не идёт через общий pvStep"
    assert f"spareRollAt({player},st.tm)" in body(step, 300), f"{player}: дублёр не пускается вживую до стыка"
    assert f"spareIdle({player})" in body(seek), f"{player}: перемотка не сбрасывает дублёра"
    assert f"spareStop({player})" in body(pause), f"{player}: дублёр догорает на паузе"
    assert f"{player}.scrubbing=true" in body(scrub), (
        f"{player}: протяжка ползунка снова сеcит дублёра на каждый пиксель")


@pytest.mark.parametrize("player,apply_fn,seek,pause,opener,before", [
    ("PV", "function pvVideoTo(src)", "function edSeek(s)", "function edPause()",
     "async function openPreview(xml)", "$('pvsub')"),
    ("IPV", "function ipvApplyVisual(tm,play)", "function ipvSeekTo(tm)", "function ipvPause()",
     "async function ipvOpen(xml)", "io"),
    ("CPV", "function cpvApply(tm,play)", "function cpvSeekTo(tm)", "function cpvPause()",
     "async function cpvOpen(xml)", "$('cpvsub')"),
])
def test_every_preview_switches_cameras_through_one_machine(js, player, apply_fn, seek, pause, opener, before):
    """Ракурсы переключает ОДНА машина (camApply), а не три копии в каждом плеере.

    Копий было три, и правки моргания расходились между ними. Каждая камера при открытии
    получает свой оффсет (camDeltas) и свой дублёр (camBufs) — без этого она не была бы
    непрерывной дорожкой и на стыке её опять пришлось бы будить seek'ом.

    У шага 1 плеер один — редактор: ракурс ставит pvVideoTo (дверь edSeek), а на паузе
    скорости камер приводит edPause. Второй двери «показать кадр» (pvSeekTo/pvApplyVisual)
    больше нет — она и была вторым плеером.
    """
    def body(head, n=900):
        return js[js.index(head):js.index(head) + n]
    assert f"camApply({player},src,false)" in body(apply_fn, 400) or \
           f"camApply({player},tm,play)" in body(apply_fn, 400), (
        f"{player}: переключение ракурса снова живёт своей копией алгоритма"
    )
    open_body = js[js.index(opener):js.index(opener) + 2600]
    assert f"{player}.delta=camDeltas({player})" in open_body, f"{player}: камеры без оффсетов от ведущей"
    assert f"camBufs({player},stage,{before})" in open_body, f"{player}: камеры без дублёров — стык снова через seek"
    # camIdle у шага 1 зовёт ЕГО дверь показа кадра (pvVideoTo): отдельного pvSeekTo,
    # который это делал, больше нет — но правило одно: перемотка/пауза гасят скорости камер.
    door = _fn_body(js, "function pvVideoTo(")
    if player == "PV":
        assert f"camIdle({player})" in door, f"{player}: перемотка не приводит скорости камер в норму"
    else:
        assert f"camIdle({player})" in body(seek), f"{player}: перемотка не приводит скорости камер в норму"
    assert f"camIdle({player})" in body(pause), f"{player}: на паузе камеры остаются с правленой скоростью"


def test_camera_choice_never_walks_back_while_playing(js):
    """Во время игры показ не возвращается на предыдущий кусок EDL.

    Время монтажа считается от currentTime ведущей камеры, а на склейке её подменяет
    дублёр, которому позволено стоять в окне PV_SWAP_LO..HI (до 0.12 с недобега). Сразу
    после подмены t считается уже от него и на кадр-другой откатывается ЗА границу куска —
    pvSegAt честно отдаёт предыдущий кусок, а в нём ПРЕЖНЯЯ камера. Показ прыгал
    «новая → прежняя → новая»; счётчик «кам» набирал 4 на одной смене вместо 1 (замер
    пользователя на C1432, 2026-08-08). Назад ходят только перемоткой, а она сбрасывает
    P.vidx в -1 — на этом и держится запрет.
    """
    body = js[js.index("function camApply(P,tm,play)"):js.index("function camIdle(P)")]
    assert "if(vtPlaying(P)&&P.vidx>=0&&vi<P.vidx)" in body, (
        "показ снова может уехать на предыдущий кусок — вернётся мелькание прежней камеры")

    # Шаг 1 — редактор: его дверь перемотки pvVideoTo сбрасывает кусок сама.
    door = js[js.index("function pvVideoTo(src)"):js.index("function bufMake(P")]
    assert "PV.vidx=-1" in door, "pvVideoTo: перемотка не сбрасывает кусок"
    for fn in ("function ipvSeekTo(tm)", "function cpvSeekTo(tm)"):
        seek = js[js.index(fn):js.index(fn) + 400]
        assert ".vidx=-1" in seek, (
            f"{fn}: перемотка не сбрасывает кусок — запрет на ход назад запрёт и её саму")


def test_camera_switch_is_layer_order_not_opacity(js):
    """Ракурс переключается порядком слоёв, а не прозрачностью.

    Слой <video> с opacity:0 композитор вправе не рисовать, и на возврате в 1 первый кадр
    приходит с запозданием — видно то, что было под ним, то есть прежнюю камеру. В логах
    плеера этого нет вообще (кадры видео тут ни при чём), поэтому ловится только глазами.
    Камеры кроют кадр целиком, значит верхняя просто закрывает остальные.
    """
    vis = js[js.index("function camVisual(P,ci,play)"):js.index("function camDeltas(P)")]
    assert "v.style.opacity='1'" in vis, "камеры снова прячутся прозрачностью"
    assert "v.style.zIndex=(i===ci)?'2':'1'" in vis, "порядок слоёв больше не решает, кто в эфире"

    css = _read(CSS)
    stage = re.search(r"\.pvstage video\{([^}]*)\}", css)
    assert stage, "пропало правило .pvstage video — на нём держится, что камера кроет кадр целиком"
    for need in ("inset:0", "object-fit:cover", "background:#000"):
        assert need in stage.group(1).replace(" ", ""), (
            f"камера больше не кроет кадр целиком ({need}) — нижний слой будет просвечивать")

    track = js[js.index("function camTrack(P,v,want)"):js.index("function camApply(P,tm,play)")]
    assert "v.playbackRate=" in track, "дрейф ракурса опять правится только seek'ом"


def test_double_buffer_catches_up_to_the_seam_by_rate(js):
    """Дублёр приходит на стык кадр-в-кадр, догоняя скоростью, а не «как получится».

    Тик, на котором решаем его пускать, сам приходит с опозданием (в фоновой вкладке rAF
    молчит, остаётся страховочный интервал 120 мс), и дублёр не добегал ~0.15 с — за
    окном PV_SWAP_LO. Подмена срывалась на трети стыков, стык шёл через seek, а на seek'е
    входящая камера оказывалась не готова и на экране лишние кадры держалась ПРЕЖНЯЯ.
    Замер после правки: 14 стыков из 14 проходят подменой, опозданий переключения ноль.
    """
    roll = js[js.index("function bufRoll(b,left)"):js.index("function bufTake(P,b,at)")]
    assert "b.el.playbackRate=" in roll, "дублёр снова пускается без догона — не добежит до стыка"
    assert "b.at-b.el.currentTime" in roll and "left" in roll, (
        "скорость догона считается не из «сколько медиа осталось на сколько времени»")

    take = js[js.index("function bufTake(P,b,at)"):js.index("function spareLead(P)")]
    assert "live.playbackRate=1" in take, (
        "дублёр выходит в эфир с разгонной скоростью — картинка поедет быстрее звука")


def test_camera_layout_buffers_the_audio_camera_too(js):
    """В раскладке камер звуковая камера тоже проходит склейку подменой, а не seek'ом.

    Она вторая непрерывная дорожка: без дублёра её на каждой склейке дёргал
    cpvSyncAudioCam (допуск 0.25 с), то есть звук вставал ровно там, где раньше вставала
    картинка. Дублёр ей больше не заводят отдельно при смене К1/К2/К3 — camBufs делает
    его КАЖДОЙ камере при открытии (все они непрерывные дорожки), поэтому проверяем
    общий механизм: оффсет из P.delta[k] и что скорость звуковой камере не правят.
    """
    bufs = js[js.index("function camBufs(P,stage,before)"):js.index("function camTrack(P,v,want)")]
    assert "bufMake(P,stage,before,k,(P.delta||[])[k]||0)" in bufs, (
        "дублёр камеры создаётся без её оффсета — подменит не тот кадр")

    aud = js[js.index("function cpvAudio(k)"):js.index("function cpvSyncAudioCam()")]
    assert "CPV.vids[k].playbackRate=1" in aud, (
        "камера, ставшая звуковой, осталась с правленой скоростью — звук поедет по тону")
    sync = js[js.index("function cpvSyncAudioCam()"):js.index("// Картинка ракурса")]
    assert "CPV.delta[k]" in sync, "звуковую камеру ведут не по её постоянному оффсету"
    # общая машина обязана вести ВСЕ дублёры, а не только ведущий
    prime = js[js.index("function sparePrime(P)"):js.index("function spareRollAt(P,tm)")]
    assert "(P.bufs||[]).forEach(b=>" in prime, (
        "разбег готовится только ведущему — звуковая камера снова встанет на стыке")
    assert "bufArm(P,b,nx.src+b.off)" in prime, (
        "взвод дублёра к следующему стыку потерян")


def test_editor_playback_uses_the_same_double_buffer(js):
    """Стык блока в РЕДАКТОРЕ тоже идёт через дублёра, а не через seek на месте.

    По этому таймлайну и делают правки, поэтому замирание здесь больнее всего. Звук стык
    проходит очередью буфера (блок `ea*`), а картинка догоняет его: дублёром, взведённым
    заранее, и только при промахе — перемоткой с упреждением.
    """
    tick = js[js.index("function edTick()"):js.index("function edBreathAt(")]
    assert "edFollow()" in tick, "кадр редактора не подводит картинку к звуку"
    assert "edArm()" in tick, "редактор не готовит разбег дублёра"
    follow = js[js.index("function edFollow()"):js.index("function edArm()")]
    assert "const live=edJump(v,cs)" in follow, (
        "после подмены картинка берётся у снятого с эфира элемента")
    jump = js[js.index("function edJump(v,at)"):js.index("function edVideoSeek(")]
    assert "edTake(b.at)" in jump, "прыжок через вырез не пробует дублёра"
    assert "edVideoSeek(v," in jump, (
        "seek на месте должен остаться запасным путём, когда дублёр не успел")
    play = js[js.index("function edPlay()"):js.index("function edPause()")]
    assert "spareIdle(PV)" in play, "редактор стартует с чужим разбегом дублёра"
    assert "spareStop(PV)" in js[js.index("function edPause()"):js.index("function edTake(")], (
        "дублёр догорает после паузы редактора")


def test_scrubbing_does_not_starve_the_preview_double_buffer(js):
    """Протяжка ползунка не заваливает дублёра сеcками.

    oninput сыплется на каждый пиксель, а ползунок шага 3 (ipvScrub) зовёт
    ipvPause+ipvSeekTo+ipvPlay. Пока разбег готовился на каждом таком вызове, дублёр
    оставался вечно «seeking»: на ближайшем стыке подмена срывалась в запасной путь, и
    стык снова замирал — при том что обычное проигрывание шло гладко. Разбег готовим один
    раз, когда протяжка улеглась.

    У шага 1 ползунка перемотки больше нет вовсе (его заменил клик по таймлайну), поэтому
    здесь остаётся механика шага 3 и общий bufArm: в редакторе разбег гасит edPause.
    """
    body = js[js.index("function ipvScrub(x)"):js.index("function ipvJump(")]
    assert "IPV.scrubbing=true" in body and "clearTimeout(IPV.scrubT)" in body, (
        "ipvScrub снова готовит разбег на каждое событие ползунка")
    # гвардия стоит в ОБЩЕМ bufArm, а не у конкретного плеера: разбег готовят все, кто играет
    arm = js[js.index("function bufArm(P,b,at)"):js.index("function bufRoll(b,left)")]
    assert "P.scrubbing" in arm, "bufArm не знает про протяжку — seek на каждый пиксель вернулся"
    assert "function pvScrub(" not in js, "вернулся ползунок монтажа — это второй плеер шага 1"
    door = js[js.index("function pvVideoTo(src)"):js.index("function bufMake(P")]
    assert "sparePrime(" not in door, "pvVideoTo снова сеcит дублёра — его зовёт и клик, и кадр игры"


def test_style_template_is_editable_without_retyping_its_name(js, html):
    """Шаблон правится кнопкой, а имя для перезаписи подставляется само.

    Раньше карандаш переключал селектор на «кастом (свой)», и человеку казалось, что он
    заводит новый стиль, хотя «Сохранить как шаблон» перезаписывал тот же файл. Теперь
    правка идёт ОТДЕЛЬНЫМ режимом __edit__: селектор показывает имя шаблона с пометкой
    « — правится», кнопка «Сохранить» перезаписывает его. Встроенные base/geologica —
    исключение: файла у них нет, только «Сохранить как…».
    """
    assert 'onclick="editStyle()"' in html, "кнопки правки шаблона нет рядом с селектором"
    body = js[js.index("function editStyle()"):js.index("async function delStyle()")]
    assert "$('style').value='__edit__'" in body, (
        "правка шаблона снова идёт через «кастом» — человек видит враньё")
    assert "ensureEditOption(" in body, "селектор не показывает имя шаблона с пометкой «правится»"
    assert "BUILTIN_STYLES[key]?'':key" in body, (
        "имя шаблона снова вписывается руками (или подставляется встроенному)")
    # Поля панели раскладываются из шаблона ОДНИМ путём. Раньше тут вручную возвращали
    # рото открытого клипа (#roto/#rotobottom), потому что рото было настройкой КЛИПА;
    # с рото — поле стиля, и шаблон приносит его сам.
    assert "$('rotobottom')" not in body and "st_roto" not in body, (
        "заход в правку шаблона снова правит поля рото руками")
    assert "fillStyleFields();" in body, "поля панели не заполняются из шаблона"
    assert "roto" in _schema_toggles(), "рото перестало быть тумблером слоя схемы"
    assert _schema_field("roto_bottom")["ctl"] == "num", "«Низ маски» пропал из схемы"


def test_edit_mode_key_never_leaks_out_of_the_selector(js):
    """Служебный `__edit__` живёт ТОЛЬКО в селекторе — ни в задании, ни на сервере.

    `__edit__` — служебное значение режима правки шаблона, а не имя стиля: когда
    `captureAE` читает селектор в режиме правки, на этом месте `'__edit__'`. Попав
    в задание, оно осталось бы там навсегда — сборка не нашла бы стиль с таким
    именем, а `delStyle` слал бы на сервер несуществующее имя. Поэтому в обоих
    местах служебное значение обязано подменяться на `STYLE_EDITING`.
    """
    cap = _fn_body(js, "function captureAE()")
    assert "val('style')==='__edit__'" in cap and "STYLE_EDITING" in cap, (
        "в задание уезжает служебный __edit__ вместо имени шаблона")

    dele = _fn_body(js, "async function delStyle()")
    assert "name==='__edit__'" in dele and "STYLE_EDITING" in dele, (
        "корзина в режиме правки шаблона шлёт на сервер «__edit__»")


def test_single_camera_has_a_bulk_queue_button(js, html):
    """Одна камера: очередь набивается одной кнопкой, а не селектом по файлу.

    Авто-пары и подбор по звуку работают от двух камер, поэтому на одной камере
    массовых кнопок не было вовсе — двадцать файлов уходили в очередь двадцатью
    парами кликов (просьба юзера).
    """
    assert 'id="autoqueue"' in html and 'onclick="autoQueue()"' in html, (
        "кнопки массовой очереди для одной камеры нет")
    body = js[js.index("async function autoQueue()"):js.index("// «Только новые»")]
    assert "const have=new Set(QUEUE.map(p=>p[0]))" in body, (
        "в очередь снова уезжают файлы, которые в ней уже есть")
    assert "await newOnly(fresh)" in body, (
        "галочка «только новые» не действует на массовую очередь")

    mode = js[js.index("function camModeUI()"):js.index("function buildCamRows()")]
    for btn in ("autopair", "cammatch", "cammatchall", "autoqueue"):
        assert btn in mode, f"кнопка {btn} не переключается по числу камер"
    rows = js[js.index("function buildCamRows()"):js.index("async function pickCamDir(k)")]
    assert "camModeUI();" in rows, (
        "видимость кнопок ставится только на смене радио — после F5 на одной камере "
        "останутся кнопки от двух")


def test_ui_is_branded_reelsi(html):
    """Имя в хроме интерфейса — Reelsi. Старое имя в UI не показываем."""
    assert "<title>Reelsi</title>" in html
    assert "AutoCut" not in html, "старое имя осталось на видном месте в интерфейсе"


def test_every_app_script_is_wired_into_the_page():
    """Каждый файл static/app/ должен попадать в страницу, и в порядке имён.

    Интерфейс с 2026-08-06 распилен на четырнадцать файлов, которые грузятся обычными
    <script>-тегами в общий скоуп. Забытый тег — самая неприятная поломка из
    возможных: ошибки в консоли НЕТ, страница открывается, просто часть кнопок
    перестаёт отвечать. Поэтому теги собирает сервер по содержимому папки, а этот
    тест стережёт, чтобы их снова не выписали руками в шаблоне.

    Порядок значим: объявления функций поднимаются в пределах СВОЕГО файла, и код,
    исполняемый на загрузке (боот в 99-boot.js), обязан идти последним.
    """
    import os as _os
    from core import app_meta
    import webui

    names = [_os.path.basename(p) for p in app_meta.app_js_files()]
    assert names, "папка static/app пуста — интерфейс не из чего собрать"
    assert names == sorted(names), "порядок загрузки задаётся именами, а они не отсортированы"
    assert names[-1].endswith("boot.js"), "боот обязан грузиться последним"

    html = webui._page()
    pos = []
    for n in names:
        marker = "/static/app/" + n
        assert marker in html, f"{n} не подключён к странице"
        pos.append(html.index(marker))
    assert pos == sorted(pos), "теги идут не в том порядке, что файлы"
    assert "__APP_JS__" not in html, "плейсхолдер тегов остался неподставленным"


def test_style_roto_bottom_shows_mask_while_editing(js, html, css):
    """«Низ маски %» показывает на превью, где пройдёт рото, пока поле крутится.

    Процент без кадра не говорит ничего: «35%» — это где по вертикали? Пока поле
    рото в фокусе и крутится, на кадре предпросмотра снизу живёт полупрозрачная
    красная полоса на столько процентов высоты; перестал крутить или ушёл с поля —
    ушла. Раньше ротоскоп правился вслепую, до рендера.

    Живую маску включает поле с hint «rotomask»: id старой разметки
    (rotobottom) больше нет, поле строит панель по схеме.
    """
    field = _schema_field("roto_bottom")
    assert field and field.get("hint") == "rotomask", (
        "у поля «Низ маски» пропала живая маска (hint rotomask)")
    panel = _panel_js()
    assert "field.hint === 'rotomask'" in panel, "панель не включает маску у поля рото"
    assert "rotoMaskSync()" in panel and "rotoMaskHide()" in panel, (
        "поле рото не будит/не прячет маску")
    assert "function rotoMaskSync(" in js and "function rotoMaskHide()" in js, (
        "механика маски пропала из 95-styles.js")
    assert "setTimeout(rotoMaskHide,1500)" in js, (
        "маска не гаснет сама после паузы в кручении")
    assert "rotoMaskHide();" in js[js.index("function styleHome("):], (
        "уход со вкладки «Стиль» оставляет маску на кадре")
    assert ".rotomask{" in css and "rgba(255,60,60," in css, (
        "у маски пропал красный полупрозрачный стиль")


def test_roto_mask_hint_rides_cam1_zoom(js, css):
    """Полоса «Низ маски %» едет ВМЕСТЕ с кадром, а не прибита к низу стойки.

    RVM заливает белым низ ИСХОДНИКА камеры, в AE рото-слой висит на нуле Камеры 1 и
    едет с её наездом. Подсказка же стояла у низа стойки — при зуме 160% красным
    закрашивалось не то место, и «низ маски» правился вслепую (жалоба 2026-08-14).
    Инвариант: подсказка зовётся в ipvZoom раньше кадра (тот же s из ipvZoomAt), а при s===1 раннего выхода нет — кадр рисуется всегда.
    """
    assert "function ipvRotoMaskZoom(" in js, "пропала синхронизация подсказки рото с наездом"
    head = js[js.index("function ipvZoom(tm)"):]
    body = head[:head.index("\nfunction ")]
    assert body.index("ipvRotoMaskZoom(s,cx,cy)") < body.index("ipvCamPaint(s)"), (
        "подсказка рото обязана получить масштаб РАНЬШЕ кадра: тот же s из ipvZoomAt, "
        "второй интерполятор зума разъедется с кадром (задание L)")
    assert "if(s===1)return;" not in body, (
        "раннего выхода при s===1 в ipvZoom нет: при единичном зуме кадр всё равно перерисовывается")
    assert ".rotomask{position:absolute;inset:0" in css and ".rotomask .rmband{" in css, (
        "полоса обязана лежать внутри рамки во весь кадр: transform-origin в процентах "
        "считается от СВОЕЙ коробки, у полосы высотой 35% он попал бы мимо точки наезда")


def test_style_sub_height_live_moves_preview_subtitle(js, html):
    """«Высота субтитров %» двигает строку в превью сразу, а не после рендера.

    Поле тянется мышью и уходит в stEdit на каждый шаг — строка слова в превью садится
    на те же проценты от низа, что AE поставит POSY. Маркер не нужен: сами слова и есть
    подсказка. Поле строит панель по схеме (conv inv_pct), id старой разметки ушёл.
    """
    field = _schema_field("sub_y")
    assert field and field["ctl"] == "num" and field.get("conv") == "inv_pct", (
        "«Высота субтитров» пропала из схемы или потеряла пересчёт в % снизу")
    panel = _panel_js()
    drag = _fn_body(panel, "function initNumDrag(")
    assert "stEdit();" in drag, "перетаскивание числа не двигает превью вживую"
    assert "stEdit();" in _fn_body(panel, "function stSliderInput("), (
        "ползунок не двигает превью вживую")
    assert "function styleSubPos()" in js and ".pvsub" in js[js.index("function styleSubPos()"):], (
        "позиция субтитров в превью больше не пересчитывается")
    assert "styleSubPos();" in _fn_body(panel, "function stEdit()"), (
        "правка стиля не двигает строку в превью")
    refl = js[js.index("function reflectStyle()"):js.index("function styleFrameDim()")]
    assert "styleSubPos();" in refl, "смена шаблона не выставляет высоту субтитров в превью"




def _fn_body(js, marker):
    """Тело функции от её объявления до следующего объявления верхнего уровня."""
    i = js.index(marker)
    tail = js[i + len(marker):]
    ends = [p for p in (tail.find("\nfunction "), tail.find("\nasync function ")) if p >= 0]
    return tail[:min(ends)] if ends else tail


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


def test_every_player_video_is_wired_to_the_volume_graph(js):
    """Каждый <video> плеера подключён к графу громкости, ВКЛЮЧАЯ дублёров.

    Ползунки «Музыка/Голос» звучат через AudioContext, а плеер двухбуферный: на
    стыке дублёр (bufMake) меняется местами с живым элементом. Забыть о нём —
    значит после первого же стыка пустить звук мимо регулятора; симптом
    «громкость работает, а потом перестаёт», и ищется он долго.
    """
    for marker in ("function bufMake(", "async function ipvOpen("):
        assert "voiceWiring(" in _fn_body(js, marker), (
            "<video> создаётся без voiceWiring: " + marker)
    # Шаг 1 — исключение: его <video> немые (PV.silent), звук играет буфер редактора
    # (блок `ea*`) со своим гейном. `createMediaElementSource` необратим — немой камере
    # граф не нужен, и дублёра немого кадра bufMake тоже не заявляет.
    assert "voiceWiring(" not in _fn_body(js, "async function openPreview("), (
        "камера шага 1 заявлена на граф, а звука у неё нет")
    assert "if(!P.silent)voiceWiring(el)" in _fn_body(js, "function bufMake("), (
        "дублёр немого кадра шага 1 заявлен на граф")
    assert "v.__wired" in js and "if(!v||v.__wired)return" in js, (
        "пропал признак «источник уже создан»: второй createMediaElementSource "
        "на том же элементе роняет звук совсем")


def test_preview_draws_the_scene_plan_and_never_recomputes_it(js):
    """Предпросмотр шага 3 РИСУЕТ план сцены, ничего не досчитывая.

    Раньше формула окон групп интро жила в ДВУХ копиях — introGroupWindows в JS и
    inAt/outEnd в шаблоне — и они уже разошлись (JS не учитывал max(gMax, inAt+F_DUR)
    для серединных групп). Теперь ts/te считает scene_plan (xml2ae/build.py), а JS
    только разворачивает их в {inAt, outEnd}; вставки/зум/субтитры/рото — из тех же
    ключей плана. Третья копия в превью не заводится ни при каком раскладе.
    """
    fetch = _fn_body(js, "async function ipvPlanFetch(")
    assert "/api/scene" in fetch, "предпросмотр не запрашивает /api/scene"
    assert "IPV.plan=" in fetch, "план не кладётся в IPV.plan"
    assert "catch" in fetch, "ошибка плана должна не ломать предпросмотр"
    assert "if(d.ok&&d.plan)" in fetch, "план применяется только при ok"

    open_ = _fn_body(js, "async function ipvOpen(")
    assert "ipvPlanFetch()" in open_, "ipvOpen не тянет план в ae-режиме"

    intro = _fn_body(js, "function ipvIntroGroups(")
    assert "IPV.plan" in intro, "группы интро берутся не из плана"

    win = _fn_body(js, "function introGroupWindows(")
    assert "g.ts" in win and "g.te" in win, "окна не разворачиваются из ts/te плана"
    for banned in ("gMin", "gMax", "inAt=", "0.75", "1.3"):
        assert banned not in win, f"оконная формула вернулась в JS: {banned}"

    overlay = _fn_body(js, "function ipvOverlayPlan(")
    assert "IPV.plan.inserts" in overlay, "вставки рисуются не по плану"
    place = _fn_body(js, "function ipvInsPlace(")
    assert "keysAt(" in place, "позиция/масштаб вставки не интерполируются из ключей"
    assert "x.card" in place, "карточка фото-вставки берётся не из плана"

    # вставки шага 2: перевод в контракт плана единой функцией cardToIns
    body = _fn_body(js, "function ipvPlanBody(")
    assert "cardToIns(x)" in body, "вставки шага 2 в ipvPlanBody не переводятся через cardToIns"
    ensure = _fn_body(js, "function ensureJobs(")
    assert "cardToIns(x,was)" in ensure, "ensureJobs не использует общую cardToIns"
    card_fn = _fn_body(js, "function cardToIns(")
    assert "x.duration_sec||2" in card_fn, "cardToIns должен сохранять дефолт длительности ||2"
    refresh = _fn_body(js, "function ipvRefresh(")
    assert "ipvPlanSoon()" in refresh and "IPVMODE==='ae'" not in refresh, (
        "ipvRefresh должен перезапрашивать план в обоих режимах")

    zoom = _fn_body(js, "function ipvZoomAt(")
    assert "IPV.plan.zoom" in zoom and "keysAt(" in zoom and "z.hold" in zoom, (
        "наезд/дрейф камеры 1 не интерполируется из zoom.keys плана")
    frame = _fn_body(js, "function ipvZoom(")
    assert "ipvZoomAt(" in frame, "кадр берёт масштаб не из общего интерполятора"
    subs = _fn_body(js, "function ipvSubs(")
    assert "IPV.plan" in subs and (".gend" in subs) and (".row" in subs), (
        "стопка субтитров не читает row/gend плана")


def test_preview_ducks_voice_on_censor_windows_from_the_plan(js):
    """Голос предпросмотра ныряет в ноль на окнах цензуры ИЗ ПЛАНА.

    Окна audio.censor считает scene_plan (xml2ae/build.py) — в рендере на них голос
    уходит voice_db→−100. Превью обязано повторить то же самое через voiceGain (VG),
    иначе мат слышен там, где в готовом ролике тишина. Свой список слов и вторая
    формула в JS не заводятся: vgDuck читает plan.audio.censor как есть и проверяет
    «внутри окна или нет» по таймлайну.
    """
    duck = _fn_body(js, "function vgDuck(")
    assert "plan.audio.censor" in duck, "окна цензуры берутся не из плана"
    assert "VG.gain.value=0" in duck, "внутри окна voiceGain не уводится в ноль"
    assert "dbToGain(" in duck and "voice_db" in duck, (
        "вне окна voiceGain не возвращается к voice_db — заглушка останется навсегда")

    # Шаг 1 плана сцены не знает вовсе: /api/aicut_preview отдаёт EDL, а не план сцены.
    # Пока панель слов предпросмотра нарезки существовала, вызов брал PVW.plan — он всегда
    # был null, и вызвать vgDuck было не с чем. Теперь вызова нет ни у одного плеера шага 1
    # (осталась одна дверь показа — pvVideoTo), глушение голоса живёт в плеере вставок.
    door = _fn_body(js, "function pvVideoTo(")
    assert "vgDuck(" not in door, "показ кадра шага 1 снова глушит голос по несуществующему плану"
    ipv = _fn_body(js, "function ipvUI(")
    assert "vgDuck(tm,IPV.plan)" in ipv, "плеер вставок не глушит голос по плану"
    # без плана (запрос не прошёл) duck обязан вернуть обычную громкость, не сломать плеер
    assert "if(!VG)return" in duck, "vgDuck падает без графа громкости"


def test_editor_words_panel_shows_the_word_under_the_playhead(js):
    """Строка субтитра в редакторе нарезки идёт за плейхедом (жалоба 2026-08-11).

    Редактор играет ИСХОДНИК (ED.cs), а PV.words размечены по МОНТАЖНОМУ времени:
    без пересчёта слово под картинкой висит прежнее. Пересчёт — по ED.orig (раскладка
    ИЗ XML): несохранённая правка блоков их тайминги не двигает.

    Панель слов предпросмотра нарезки (#pvwords) и её подсветка чипов удалены — в
    разметке этих контейнеров нет, и pvwHighlight не находил ни одного чипа. Здесь
    остаётся то, что реально видно: текст #pvsub.

    Слово под плейхедом ищет ОДНА функция (pvWordAt, 60-preview.js): строку субтитра
    кадра ведёт edWords, и второй копии «какое слово на экране» в интерфейсе нет.
    """
    body = _fn_body(js, "function edWords(")
    assert "ED.orig" in body, "слова считаются по правленой раскладке, а не по той, что в XML"
    assert "pvsub" in body and "pvWordAt(" in body, (
        "строка субтитра не получает слово под плейхедом")
    # монтажного плеера, который вёл панель сам, больше нет — и гвардии его игры тоже
    assert "PV.playing" not in body, "edWords снова ждёт монтажный плеер, которого нет"
    assert "pvwHighlight(" not in body, "вернулась подсветка чипов удалённой панели"
    ui = _fn_body(js, "function edUI(")
    assert "edWords()" in ui, "edUI — единственная точка, куда стекаются сдвиги плейхеда"


def test_frame_drag_writes_data_not_a_second_storage(js):
    """Перетаскивание в кадре (шаг 2) пишет в ИМЕЮЩИЕСЯ источники: вставка —
    INS[].x/y, интро — gx/gy на головной строке. План-кэш
    правится только как временный показ (insShift / plan.intro.dx) и сам
    пересчитывается дебаунсом — второго хранилища значений нет. Субтитры из списка
    выпали: драга нет, высота — поле стиля «% снизу» (stEdit → ipvPlanSoon).

    Главная ловушка — пересчёт координат: экранные px делятся на k (стойка/plan.w),
    а для вставки style=cam1 на Камере 1 ещё и на ipvZoomAt: она отрисована увеличенной
    вместе с кадром, и на наезде 182% без деления палец сдвинул бы её вдвое дальше.
    """
    ins = _fn_body(js, "$('ipvins').addEventListener('pointerdown'")
    assert "/(k*z)" in ins, "драг вставки не делит экранные px на k·зум"
    assert "x.card&&x.style==='cam1'&&!x.oncam2" in ins, (
        "деление на зум привязано не к той вставке")
    assert "ipvZoomAt(ipvNow())" in ins, "зум для пересчёта не из общего интерполятора"
    assert "INS[real].x=nx;INS[real].y=ny" in ins, "значение пишется не в данные вставки INS"
    assert "captureAE();ipvPlanSoon()" in ins, "после отпускания не пересчитывается план"

    place = _fn_body(js, "function ipvInsPlace(")
    assert "insShift.i===+wr.dataset.ins" in place, (
        "сдвиг вставки ключуется не по data-ins (поймано вживую: голый i в ipvInsPlace "
        "вне области видимости — ReferenceError на каждом драге)")

    intro = _fn_body(js, "$('ipvintro').addEventListener('pointerdown'")
    assert "INTRO[h].gx=Math.round((st.gx+dx)*10)/10" in intro, (
        "интро пишется не в gx/gy головной строки (данные)")
    assert "captureAE();ipvPlanSoon()" in intro, "интро не пересчитывает план"
    assert "INTRO[h].gs=Math.round" in intro, "масштаб интро не пишется в gs головной строки"
    assert "intro-scale-handle" in intro, "у интро нет отдельного угла для масштаба"
    assert "st.gs+dx" in intro and "/(k*G)" in intro, (
        "масштаб интро не пересчитывается через k·G (общий масштаб, задание BG)")

    intro_dbl = _fn_body(js, "$('ipvintro').addEventListener('dblclick'")
    assert "intro-scale-handle" in intro_dbl, "двойной клик не фильтрует ручку масштаба"
    assert "INTRO[h].gs=100" in intro_dbl, "двойной клик не сбрасывает gs в 100 (возврат к авто, задание CF)"
    assert "captureAE();ipvPlanSoon()" in intro_dbl, "двойной клик не пересчитывает план"

    pos = _fn_body(js, "function ipvIntroPos(")
    assert "g.ds" in pos and "scale" in pos, "плановый масштаб группы не рисуется"
    assert "intro_scale" in pos and "G*" in pos, (
        "ipvIntroPos не множит на общий масштаб интро из плана (задание BG)")

    # Драга субтитров больше нет: высота правится ползунком стиля, а
    # перехваченный клик по строке мешал кадру. Здесь остаётся поле «% снизу».
    assert "$('ipvsub').addEventListener('pointerdown'" not in js, (
        "драг субтитров вернулся в предпросмотр (задание MD его убрало)")

    st = _fn_body(js, "function stEdit(")
    assert "ipvPlanSoon()" in st, (
        "правка поля «% снизу» не догоняет план — превью не двигается (обратная связь)")


def test_intro_scale_handle_centered_below_line(css):
    """Ручка масштаба интро — под блоком по центру, а не в потоке строки.

    Раньше ручка жила inline после последнего слова (display:inline-block + margin-left),
    и у широкой строки её конец уходил за край кадра — тянуть было нечего. Теперь она
    абсолютная относительно последней .iline и центрируется (left:50% + translate(-50%)),
    поэтому ни при какой ширине строки за кадр не выезжает. Курсор ew-resize: тянется
    только по горизонтали, уголок nwse-resize читался бы как «диагональ».
    """
    m = re.search(r"\.ipvintro \.intro-scale-handle\{([^}]*)\}", css)
    assert m, "нет правила .intro-scale-handle в app.css"
    rule = m.group(1)
    assert "position:absolute" in rule, "ручка осталась в потоке строки"
    assert "left:50%" in rule and "translate(-50%" in rule, "ручка не центрируется под строкой"
    assert "top:100%" in rule, "ручка не под блоком"
    assert "ew-resize" in rule, "курсор не горизонтальный"
    assert "display:inline-block" not in rule and "margin-left" not in rule, (
        "ручка вернулась в поток строки — широкую строку она утаскивает за кадр")
    iline = re.search(r"\.ipvintro \.iline\{([^}]*)\}", css)
    assert iline and "position:relative" in iline.group(1), (
        "у .iline нет position:relative — ручка позиционируется не от строки")


def test_insert_shift_survives_ensure_jobs_rebuild(js):
    """Сдвиг вставки, сделанный драгом в предпросмотре, переживает пересборку списка.
    ensureJobs целиком пересобирает c.job.ins из карточек шага 2 и убивал
    x/y: драг писал только в INS, а источник x/y — карточка c.inserts (в ui_state у всех
    54 вставок на момент жалобы стояли нули). Теперь драг пишет и в карточку, найденную по
    стабильному id вставки (insCardFor), а перенос tw для ручных вставок без карточки несёт x/y.
    """
    drag = _fn_body(js, "$('ipvins').addEventListener('pointerdown'")
    assert "insCardFor(INS[real])" in drag, (
        "драг ищет карточку мимо общего insCardFor — у дублей одного файла правка уедет в первую")
    assert "card.x=nx" in drag and "card.y=ny" in drag, (
        "сдвиг не записывается в карточку шага 2 (источник x/y)")

    jobs = _fn_body(js, "function ensureJobs(")
    assert "noexit:x.noexit,x:x.x,y:x.y" in jobs, (
        "перенос tw для ручных вставок (без карточки) не несёт x/y")
    assert "normInsPath(x.media)" in jobs, "ensureJobs не использует общий normInsPath"
    assert "tw[x.uid]" in jobs, (
        "подстройки переносятся не по стабильному id карточки — дубли одного файла получат чужие")


def test_frame_drag_shift_locks_one_axis(js):
    """Shift в драге вставок/интро фиксирует движение по ОДНОЙ оси.

    Ось выбирается по БОЛЬШЕМУ по модулю смещению от точки старта и переоценивается на
    каждом pointermove (как в Figma/Photoshop). Правило ОДНО на оба драга — общий
    axisLock — чтобы две копии снова не разъехались (на этом уже горели: introResolve
    и resolveIntroFor потеряли gs). pointerup применяет ИМЕННО st.lock,
    а не ev.shiftKey: Shift можно отпустить за миг до кнопки мыши, и в данные уехало бы
    не то, что нарисовано.
    """
    # тела обработчиков вырезаем по границам соседних (после insert'а идёт intro, после
    # intro — тела драга интро выражением const ipvIntroHitAt): _fn_body тащит всё до
    # следующего `function`, а тут обработчики — стрелки, и соседние попадали бы в один срез
    ins = js[js.index("$('ipvins').addEventListener('pointerdown'"):js.index("$('ipvintro').addEventListener('pointerdown'")]
    ilocks = re.findall(r"axisLock\([^)]*\)", ins)
    assert len(ilocks) == 2, "драг вставки зовёт axisLock не в move и не в up"
    assert "ev.shiftKey" in ilocks[0], "move: ось должна выбираться по Shift"
    assert "st.lock" in ilocks[1] and "ev.shiftKey" not in ilocks[1], (
        "up: применяется ИМЕННО st.lock, а не ev.shiftKey")

    intro = js[js.index("$('ipvintro').addEventListener('pointerdown'"):js.index("const ipvIntroHitAt=function")]
    ilocks = re.findall(r"axisLock\([^)]*\)", intro)
    assert len(ilocks) == 2, "интро зовёт axisLock не в move и не в up"
    assert "ev.shiftKey" in ilocks[0] and "st.lock" in ilocks[1], (
        "интро: тот же контракт axisLock, что у вставок")
    assert intro.count("else{[dx,dy]=axisLock") == 2, (
        "ось блокируется только в ветке ПЕРЕМЕЩЕНИЯ группы; уголок масштаба (handle) "
        "уже одномерный — ему lock не положен")


def test_frame_drag_axis_lock_behaves_like_graphics_editors(js):
    """axisLock считает ось как в Figma/Photoshop (математика, а не факт вызова).

    Статические проверки выше поймали бы лишь факт вызова — само правило живёт здесь:
    боевая функция из 85-inserts-view.js исполняется node'ом (как test_intro_group_offset).
    Сценарии: без Shift обе оси свободны; Shift — горизонталь/вертикаль по большему
    смещению; развернул движение — ось переоценилась; pointerup применяет ИМЕННО
    st.lock, а не ev.shiftKey (иначе последний сценарий вернул бы [50,6], а не [50,0]).
    """
    helper = _func(app_meta.app_js_text(), "axisLock")
    out = _run_node(
        helper
        + "\nvar r=[];var st={lock:null};"
        + "r.push([axisLock(st,30,5,false),st.lock]);"     # без Shift — обе оси свободны
        + "r.push([axisLock(st,30,5,true),st.lock]);"      # горизонталь сильнее
        + "r.push([axisLock(st,4,-40,true),st.lock]);"     # вертикаль сильнее
        + "r.push([axisLock(st,40,4,true),st.lock]);"      # развернули — ось переоценилась
        + "st.lock='x';"                                   # последний move был с локом
        + "r.push([axisLock(st,50,6,st.lock!==null)]);"    # up без ev.shiftKey — лок живёт
        + "console.log(JSON.stringify(r));")
    assert out == [
        [[30, 5], None],
        [[30, 0], "x"],
        [[0, -40], "y"],
        [[40, 0], "x"],
        [[50, 0]],
    ], "axisLock посчитал ось не по большему смещению"


# ---------------- связка «спикер → стиль → папки» ----------------

def test_new_clip_carries_the_speaker_tag(js):
    """Клип рождается в ОДНОЙ точке (newClip), и тег спикера ставится там же — во все
    три пути: после нарезки, «Из папки результата», «Добавить XML…». Без тега
    («спикер не выбран») клип работает как раньше — запасной путь обязателен."""
    body = _fn_body(js, "function newClip(xml)")
    assert "defJob()" in body, "новый клип заводит job не тем путём — тег не выживет"
    assert "j.speaker=" in body or "speaker:" in body, "newClip не ставит тег спикера"
    assert "val('speaker')" in body, "тег ставится не из текущего выбора спикера на шаге 1"
    assert "STYLES[p.style]" in body, "новый клип не наследует стиль своего спикера"

    clips3 = _fn_body(js, "function renderClips3()")
    assert "spkSelHTML(i,c)" in clips3, "селектор тега на шаге 3 не добавлен в строку клипа"
    assert "spkTagHTML(c,false)" in clips3, (
        "на шаге 3 имя спикера дублирует селектор — тег зовётся без имени (метки остаются)")
    sel = _fn_body(js, "function spkSelHTML(")
    assert "setClipSpeaker(" in sel, "селектор тега не правит тег клипа"


def test_build_sends_style_name_not_copy(js):
    """В сборку уходит ИМЯ стиля, а не копия: стиль резолвит бэкенд
    в момент сборки (_norm_build_jobs через styles.resolve), а копия из задания
    перекрывала свежие правки шаблона до того, как они доехали до клипа. Копия
    остаётся только у безымянного кастома. roto/roto_bottom из payload убраны —
    они и так берутся из стиля на бэкенде."""
    assert "function styleForJob(j)" in js, "имя стиля для сборки не вынесено в хелпер"
    jfb = _fn_body(js, "function jobForBuild(c)")
    assert "styleForJob(j)" in jfb, "jobForBuild не шлёт имя стиля"
    assert "style:j.style" not in jfb, "в сборку снова уходит копия стиля"
    assert "roto:!!j.roto" not in jfb, "рото из задания снова уходит в payload"
    assert "roto_bottom:j.roto_bottom" not in jfb, "рото-низ из задания снова в payload"
    tjsx = _fn_body(js, "async function tojsx(")
    assert "style:styleForJob(" in tjsx, "tojsx не шлёт имя стиля через styleForJob"
    assert "style:CURSTYLE};" not in tjsx, "tojsx снова шлёт копию стиля напрямую в payload"


def test_restyle_mechanism_is_gone_from_app_scripts():
    """Механизм разноса правок стиля по клипам удалён целиком.

    После EX1 и EX2a сборка читает стиль по имени из файла стиля (styleForJob →
    styles.resolve на бэкенде), поэтому разносить правку по копиям в заданиях нечего:
    свежая правка шаблона доезжает до клипа сама, в момент сборки. Имена ниже — его
    части: функции разноса и добора ключей и флаг STYLEPROP, по которому captureAE
    запускал разнос. Вернув любой из них, снова заводишь мёртвое состояние, которое
    промахивается мимо части клипов."""
    dead = ["restyleJobs", "propagateSpeakerStyle", "inheritMissingStyleKeys", "STYLEPROP"]
    text = app_meta.app_js_text()
    for name in dead:
        assert name not in text, f"{name} вернулся в static/app/*.js"


def test_jsx_folder_derives_from_the_tag(js):
    """Папка .jsx — производная от тега спикера; отдельного поля в интерфейсе нет.

    У клипа со спикером папка — jsxdir его профиля (иначе папка нарезки его же), и она
    уходит в сборку per-job (jobForBuild). У клипа без тега — пусто, и .jsx ложится рядом
    со своим XML: ровно это api/build.py делает с пустым outdir. Общего поля (AEGLOBAL)
    в интерфейсе больше нет — оно переписывалось каждым выбором спикера и уводило чужие
    клипы в папку последнего выбранного.
    """
    assert "function effOutdir(c)" in js, "лестницы папок по тегу нет"
    assert "AEGLOBAL" not in js, "общее поле папки .jsx вернулось"
    jfb = _fn_body(js, "function jobForBuild(c)")
    assert "outdir:effOutdir(c)" in jfb, "папка клипа не уходит в сборку per-job"
    eff = _fn_body(js, "function effOutdir(c)")
    assert "xmlDirOf(c&&c.xml)" in eff, "последняя ступень лестницы — не папка XML"
    build = _fn_body(js, "function buildOutdir()")
    assert "effOutdir(sel[0])" in build, "набор одного спикера собирается не в его папку"


def test_mixed_speakers_disable_set_build(js):
    """Клипы 2+ спикеров в наборе собираются только по одному, каждый в свою папку:
    «Собрать набор» и «Один на всё» для них недоступны."""
    sync = _fn_body(js, "function syncBuildBtn()")
    assert "spks.size>1" in sync, "смешанные спикеры не считаются"
    assert "b.disabled=!!mixed" in sync, "«Собрать набор» не гаснет при 2+ спикерах"
    assert "combined" in sync, "«Один на всё» не гасится при 2+ спикерах"
    # outdir набора — в общей buildOutdir (её зовут и сборка, и рендер);
    # «Собрать набор» с «Один на всё» без этого правила собрал бы общий файл не туда
    out = _fn_body(js, "function buildOutdir()")
    assert "spks.size===1" in out, "общий .jsx уходит не в папку единственного спикера"
    build = _fn_body(js, "async function buildMulti()")
    assert "buildOutdir()" in build, "сборка не пользуется общим правилом папки набора"


def test_render_uses_checked_clips_not_open_clip(js):
    """«Собрать и отрендерить» собирает тот же набор, что «Собрать набор» — по
    галочкам, а не по открытому в редакторе клипу (прогон 2026-08-14:
    галочка на 01, отрендерился 04 из CLIPS[curAE]). Вторая копия сбора набора здесь
    дала бы ровно ту же жалобу, поэтому обе кнопки зовут общий collectJobs()."""
    assert js.count("async function collectJobs(") == 1, "сбор набора объявлен не один раз"
    render = _fn_body(js, "async function startRender()")
    assert "collectJobs()" in render, "рендер не собирает набор через collectJobs()"
    assert "CLIPS[curAE]" not in render, "рендер снова привязан к открытому клипу"
    build = _fn_body(js, "async function buildMulti()")
    assert "collectJobs()" in build, "«Собрать набор» разошёлся с рендером по сбору набора"
    # RJOB копит СПИСОК готовых файлов — в интерфейсе показываем все имена, как pollBuild
    poll = _fn_body(js, "async function pollRender()")
    assert "d.result||[]" in poll, "результат рендера не читается как список"
    assert "res.map(p=>p.replace" in poll, "имена готовых файлов не показываются списком"


def test_photo_extensions_have_one_source(js):
    """Тип вставки (фото/видео) решает расширение, и раньше список был скопирован в
    шести местах — в пяти без tiff. Итог: .tif выбирался как фото, а в превью уезжал
    в <video> и кадр оставался пустым (аудит 2026-08-14). Теперь список один.

    Заодно он обязан совпадать с IMG_EXT в insertlib.py: там тот же вопрос решается
    на бэкенде (автоподбор из базы), и разъехавшись, они дадут «в базе фото, в UI
    видео» на одном и том же файле."""
    from core import insertlib
    assert js.count("function isPhotoPath(") == 1, "хелпер объявлен не один раз"
    copies = re.findall(r"/\\.\(png\|jpe\?g[^/]*/i", js)
    assert len(copies) == 1, f"список расширений снова скопирован: {len(copies)} шт."
    m = re.search(r"function isPhotoPath\(p\)\{return /\\.\(([^)]+)\)\$/i", js)
    assert m, "не нашёл список расширений в isPhotoPath"
    # jpe?g -> jpg/jpeg, tiff? -> tif/tiff: разворачиваем, чтобы сравнить с питоном
    js_ext = set()
    for part in m.group(1).split("|"):
        if part.endswith("?g"):          # jpe?g
            js_ext |= {".jpg", ".jpeg"}
        elif part.endswith("f?"):         # tiff? записан как tiff?
            js_ext |= {".tif", ".tiff"}
        else:
            js_ext.add("." + part)
    assert js_ext == insertlib.IMG_EXT, (
        f"фронт и insertlib.IMG_EXT разъехались: только в JS {js_ext - insertlib.IMG_EXT}, "
        f"только в питоне {insertlib.IMG_EXT - js_ext}")


def test_proxy_moves_on_the_seam_not_in_the_live_video(js):
    """Переезд на превью-прокси не трогает живому <video> src.

    pvProxyRepoint менял src живому элементу посреди игры: присвоение сбрасывает
    элемент в readyState 0 — камеры кроют сцену поверх чёрного фона, отсюда чёрный
    кадр, — а pvSeekTo после ожидания метаданных откатывал время на длительность
    загрузки («сбилось»). Теперь источник переезжает на стыке: sparePrime подтягивает
    свежий src дублёру перед взводом, pvProxyRefresh для стоящего плеера делает то же
    через spareHandover. Живому src не присваивается нигде.
    """
    ref = _fn_body(js, "async function pvProxyRefresh(")
    assert "pvPause" not in ref and "pvSeekTo" not in ref, (
        "pvProxyRefresh снова останавливает и перематывает плеер")
    assert "spareHandover(" in ref, "стоящий плеер не переезжает дублёром"
    assert "src=" not in ref, "pvProxyRefresh снова присваивает src живому <video>"

    prime = _fn_body(js, "function sparePrime(P)")
    assert "b.el.src=" in prime, "sparePrime не подтягивает свежий src дублёру"
    assert "vLoaded(b.el)" in prime, "после смены src дублёра не ждут метаданные"
    assert "bufArm(P,b,nx.src+b.off)" in prime, "взвод дублёра после смены src потерян"

    hand = _fn_body(js, "async function spareHandover(P)")
    assert "P.cams[b.slot].path" in hand, "дублёр не знает путь своей камеры"
    assert "bufSwap(P,b)" in hand, "передача эфира идёт мимо общего обмена"

    swap = _fn_body(js, "function bufSwap(P,b)")
    assert "P.vids[b.slot]=b.el" in swap and "b.el=old" in swap, (
        "общий обмен потерял подмену элементов")


def test_players_remember_camera_paths_and_watch_proxies(js):
    """IPV/CPV сохраняют пути камер и следят за сборкой прокси.

    У PV.cams есть, а дублёру нужны пути своей камеры и в других плеерах, иначе переезд
    на прокси (spareHandover) не знает, на какой файл переводить дублёра. Плюс попутный
    дефект: раньше pvProxyWatch запускался только из openPreview, и шаг 3 / раскладка
    камер играли исходник весь сеанс, даже когда прокси уже собраны.
    """
    ipv = _fn_body(js, "async function ipvOpen(")
    assert "IPV.cams=d.cams" in ipv, "IPV не сохраняет пути камер"
    assert "pvProxyWatch(" in ipv, "предпросмотр вставок не следит за сборкой прокси"
    cpv = _fn_body(js, "async function cpvOpen(")
    assert "CPV.cams=d.cams" in cpv, "CPV не сохраняет пути камер"
    assert "pvProxyWatch(" in cpv, "раскладка камер не следит за сборкой прокси"


def test_speaker_image_prompts_ui(html, js):
    """Задание CQ: приписки к промптам генерации переехали из настроек в профиль спикера."""
    # 1. Поля в модалке спикера есть
    assert 'id="spk_extra_a"' in html, "spk_extra_a отсутствует в модалке спикера"
    assert 'id="spk_pos_a"' in html, "spk_pos_a отсутствует в модалке спикера"
    assert 'id="spk_extra_b"' in html, "spk_extra_b отсутствует в модалке спикера"
    assert 'id="spk_pos_b"' in html, "spk_pos_b отсутствует в модалке спикера"

    # 2. Из общих настроек убраны
    assert 'id="ais_promptRow"' not in html, "ais_promptRow остался в настройках"
    assert 'id="ais_extra"' not in html, "ais_extra остался в настройках"
    assert 'id="ais_extra2"' not in html, "ais_extra2 остался в настройках"

    # 3. openSpeaker / saveSpeaker работают с image_prompts
    save = _save_speaker(js)
    assert "data.image_prompts" in save, "saveSpeaker не сохраняет data.image_prompts"
    assert "val('spk_extra_a')" in save or "val(\"spk_extra_a\")" in save, "saveSpeaker не читает spk_extra_a"

    open_spk = _fn_body(js, "function openSpeaker(")
    assert "p.image_prompts" in open_spk, "openSpeaker не читает p.image_prompts"

    # 4. imgPrompts не откатывается на AICFG
    img_pr = _fn_body(js, "function imgPrompts(")
    assert "AICFG.image_prompt" not in img_pr and "AICFG.image_prompts" not in img_pr, (
        "imgPrompts всё ещё обращается к AICFG")

    # 5. Подсказки в HTML не упоминают ai_config или общие настройки
    assert "ai_config" not in html[html.find('class="spkcalib"'):html.find('id="mbSfx"')], (
        "в подсказках модалки спикера осталось упоминание ai_config")

    # 6. Кнопки генерации на карточке вставки передают speaker
    btns = _fn_body(js, "function insGenBtns(")
    assert "imgPrompts(spkKey)" in btns or "imgPrompts(spk" in btns, (
        "insGenBtns не передаёт ключ спикера в imgPrompts")


def test_video_insert_generation_contract(html, js):
    """EZ: один выбор модели и VJOB, но у видео-карточки свои два слота промпта."""
    # Селекторы принадлежат общим настройкам, а на raw-вкладке остаётся только ссылка.
    assert html.count('id="ais_video"') == 1, "ais_video задублирован"
    assert html.count('id="vid_model"') == 1, "vid_model задублирован"
    generation = html[html.index('id="aistab_generation"'):html.index('id="aistab_words"')]
    assert 'id="ais_video"' in generation and 'id="vid_model"' in generation
    assert 'id="vidres"' in generation and 'onchange="setVideoResolution(this.value)"' in generation, (
        "в настройках генерации нет селекта разрешения видео")
    markup = html[html.index('id="aistab_markup"'):html.index('id="aistab_generation"')]
    assert 'id="ais_video"' not in markup and 'id="vid_model"' not in markup
    video_page = html[html.index('id="pageVideo"'):html.index('id="mbPreview"')]
    assert 'id="ais_video"' not in video_page and 'id="vid_model"' not in video_page
    assert 'id="vidres"' not in video_page, "селект разрешения задублирован на вкладку Видео"
    assert "openAISettings('generation')" in video_page, "вкладка Видео не ведёт к настройкам генерации"
    assert "openAISettings('markup')" in html, "шестерёнка разметки не ведёт к настройкам разметки"
    assert 'Профиль и модель задаются в ⚙' not in video_page, "видимая inline-справка вернулась"
    assert 'id="vid_dur"' in video_page and 'readonly' in video_page, "duration raw должен быть readonly"
    assert 'id="vid_aspect"' in video_page and 'readonly' in video_page, "aspect raw должен быть readonly"
    assert '<input id="vid_res"' in video_page and 'readonly' in video_page, (
        "resolution raw должен быть readonly отображением глобального значения")

    # Профиль спикера хранит video_prompts отдельно от image_prompts и пустое удаляет.
    for field in ("spk_video_extra_a", "spk_video_pos_a", "spk_video_extra_b", "spk_video_pos_b"):
        assert f'id="{field}"' in html, f"{field} отсутствует в модалке спикера"
    open_spk = _fn_body(js, "function openSpeaker(")
    save_spk = _save_speaker(js)
    assert "p.video_prompts" in open_spk, "openSpeaker не читает video_prompts"
    assert "data.video_prompts" in save_spk and "delete data.video_prompts" in save_spk
    video_pr = _fn_body(js, "function videoPrompts(")
    assert "spk.video_prompts" in video_pr and "image_prompts" not in video_pr

    # Карточка выбирает две видео-кнопки только при включённом видео-профиле.
    btns = _fn_body(js, "function insGenBtns(")
    render = _fn_body(js, "function renderInsHost(")
    assert "video?videoPrompts(spkKey):imgPrompts(spkKey)" in btns
    assert "insGenVideo" in btns and "insGenOne" in btns
    assert "vid?vidGenOn():imgGenOn()" in render, "видеокнопки не зависят от видео-профиля"

    # В карточку идёт только серверный контракт, без фронтовой сборки prompt/duration.
    gen = _fn_body(js, "async function insGenVideo(")
    for need in ("query:x.query", "slot:(slot==='b'?'b':'a')", "speaker:speaker",
                 "insert_duration:x.duration_sec", "xml:c.xml"):
        assert need in gen, f"payload видео-карточки потерял {need}"
    assert "prompt:" not in gen and "Math.ceil" not in gen, (
        "фронт снова собирает prompt или округляет длительность")

    raw = _fn_body(js, "async function vidGenerate(")
    assert "w:r.w||0" in raw and "h:r.h||0" in raw, "raw payload теряет probe-размеры"
    assert "resolution:" not in raw, (
        "raw payload посылает разрешение — оно общая настройка на сервере")
    st = _fn_body(js, "function vidStateObj(")
    assert "res:" not in st and "vid_res" not in st, (
        "разрешение не должно сохраняться в состояние вкладки Видео")
    sync = _fn_body(js, "async function vidSyncCaps(")
    assert "finally{VIDSYNC=false;}" in sync, "каталог нельзя блокировать после ошибки"
    assert "setTimeout(()=>vidSyncCaps(),0)" in _fn_body(js, "async function openAISettings(")
    assert "vidSyncCaps();" in _fn_body(js, "function openVideo(")

    # Poller единственный: карточка передаёт контекст videoStart, а результат делает медиа.
    assert "videoStart({query:x.query" in gen and "pollVideo(" not in gen
    start = _fn_body(js, "async function videoStart(")
    assert "VIDCTX=ctx||null" in start and "VIDPOLL=true;pollVideo()" in start
    assert "insVideoContext(target,actualSlot)" in gen and "x.video_job='pending'" in gen

    # Контекст переживает F5: VJOB key + opaque-token указывают на объект, а не индекс.
    resume = _fn_body(js, "function insVideoContextForJob(")
    adopt = _fn_body(js, "function insVideoApplyResult(")
    assert "video_target===token" in resume and "video_job===key" in resume
    assert "curIns" not in resume and "insSetMedia(x,res.path)" in adopt and "x.genAuto=true" in adopt
    assert "x.libAuto=false" in adopt and "x.libOpts=null" in adopt and "x.noAuto=false" in adopt
    boot_at = js.index("// видео-джоб живёт своим потоком")
    boot = js[boot_at:js.index("if(CLIPS.length)refreshStatuses()", boot_at)]
    assert "const ctx=videoContextForStatus(d)" in boot and "videoFinish(d,ctx)" in boot

    # У карточки свой done-текст, у raw-вкладки остаётся прежний; тип меняет tooltip.
    finish = _fn_body(js, "function videoFinish(")
    context = _fn_body(js, "function insVideoContext(")
    assert "ctx&&ctx.doneText" in finish and "Готово — видео ниже" in finish
    assert finish.index("if(d.result)") < finish.index("if(VIDCANCEL)")
    assert "Готово — видео добавлено во вставку" in context
    assert "vid?'Ролик сгенерирован ИИ и будет перенесён в базу при сборке'" in render
    assert "'Картинка сгенерирована ИИ (Nano Banana) и добавлена в базу вставок'" in render

    # Сбой status снимает локальную блокировку, а не стирает связь с оплаченной задачей;
    # локальная отмена включается только после подтверждённого ответа сервера.
    poll = _fn_body(js, "async function pollVideo(")
    cancel = _fn_body(js, "async function vidCancel(")
    # Временный transport/parse/не-2xx сбой опроса — НЕ конец: повторяем с backoff
    # (не-2xx и битый JSON идут в тот же retry), валидный status сбрасывает счётчик.
    assert "if(!r.ok){vidRetryWait();return;}" in poll, (
        "не-2xx опроса больше не гасит poller — это retry")
    assert "catch(e){vidRetryWait();return;}" in poll, (
        "битый JSON/reject опроса больше не гасит poller — это retry")
    assert "VIDRETRY=0" in poll, (
        "валидный status не сбрасывает счётчик transport-сбоев")
    retry = _fn_body(js, "function vidRetryWait(")
    assert "VID_RETRY_MS" in retry and "clearTimeout(VIDRT)" in retry, (
        "ограниченный backoff опроса пропал")
    # {running:false,done:false} — валидное terminal-состояние СЕРВЕРА (потеря VJOB),
    # отдельное от сетевого retry, со своим текстом и сохранением связи карточки.
    lost = _fn_body(js, "function videoStateLost(")
    assert "if(!d.running&&!d.done){videoStateLost(VIDCTX);return;}" in poll, (
        "idle-статус снова уходит в сетевой retry")
    assert "VIDPOLL=false" in lost and "transport:true" in lost and "lost:true" in lost
    assert "Сервер потерял состояние генерации" in lost
    assert cancel.index("if(!d.ok)") < cancel.index("VIDCANCEL=true")


def test_video_insert_terminal_context_is_one_shot(js):
    """Терминальный VJOB стирает связь и больше не может переписать карточку.

    Хелперы намеренно малы и не трогают DOM, поэтому здесь исполняем именно боевой
    JS в node: static-проверка не отличила бы сохранённый старый token от одноразовой
    связи. Сценарии покрывают done/error/cancel (один путь settle), F5 с pending или
    известным key и локальный transport-сбой, который связь сохраняет.
    """
    settle = _fn_body(js, "function insVideoSettle(")
    resume = _fn_body(js, "function insVideoContextForJob(")
    forget = _fn_body(js, "function insVideoForgetPending(")
    gen = _fn_body(js, "async function insGenVideo(")
    assert "if(!(d&&d.transport))insVideoClearLink(x)" in settle
    assert "video_target===token&&insVideoJobMatches(v,key)" in resume
    assert "insVideoClearLink(x)" in forget
    assert "if(e.data)insVideoClearLink(found.x)" in gen

    names = ("insVideoTarget", "insVideoFindTarget", "insVideoClearLink",
             "insVideoJobMatches", "insVideoSettle", "insVideoContext",
             "insVideoContextForJob", "insVideoForgetPending")
    helpers = "\n".join(_func(js, name) for name in names)
    out = _run_node(
        "const CLIPS=[{xml:'clip',inserts:[]}];"
        "function saveState(){}function renderInsHost(){}function syncClipLists(){}"
        "function t(v){return v;}"
        + helpers
        + "\nconst stale={video_target:'iv-stale',video_job:'v-stale',video_slot:'a',media:'fresh.mp4'};"
        "CLIPS[0].inserts.push(stale);"
        "insVideoSettle({xml:'clip',token:'iv-stale'},{done:true});"
        "const staleCtx=insVideoContextForJob('v-stale','iv-stale');"
        "const failed={video_target:'iv-failed',video_job:'v-failed',video_slot:'a'};"
        "const cancelled={video_target:'iv-cancelled',video_job:'v-cancelled',video_slot:'b'};"
        "CLIPS[0].inserts.push(failed,cancelled);"
        "insVideoSettle({xml:'clip',token:'iv-failed'},{error:'provider'});"
        "insVideoSettle({xml:'clip',token:'iv-cancelled'},{cancelled:true});"
        "const pending={video_target:'iv-pending',video_job:'pending',video_slot:'b'};"
        "CLIPS[0].inserts.push(pending);"
        "const pendingCtx=insVideoContextForJob('v-pending','iv-pending');"
        "const running={video_target:'iv-running',video_job:'v-running',video_slot:'a'};"
        "CLIPS[0].inserts.push(running);"
        "const runningCtx=insVideoContextForJob('v-running','iv-running');"
        "const transport={video_target:'iv-transport',video_job:'v-transport',video_slot:'b'};"
        "CLIPS[0].inserts.push(transport);"
        "insVideoSettle({xml:'clip',token:'iv-transport'},{transport:true});"
        "const transportCtx=insVideoContextForJob('v-transport','iv-transport');"
        "const idle={video_target:'iv-idle',video_job:'pending',video_slot:'a'};"
        "CLIPS[0].inserts.push(idle);insVideoForgetPending();"
        "console.log(JSON.stringify({"
        "stale:[!!staleCtx,stale.media,'video_job' in stale,'video_slot' in stale,'video_target' in stale],"
        "terminal:[Object.keys(failed).join(','),Object.keys(cancelled).join(',')],"
        "pending:[!!pendingCtx,pending.video_job,pending.genBusy],"
        "running:[!!runningCtx,running.video_job,running.genBusy],"
        "transport:[!!transportCtx,transport.video_job,transport.video_slot,transport.video_target,transport.genBusy],"
        "idle:['video_job' in idle,'video_slot' in idle,'video_target' in idle,idle.genBusy]"
        "}));")
    assert out == {
        "stale": [False, "fresh.mp4", False, False, False],
        "terminal": ["genBusy", "genBusy"],
        "pending": [True, "v-pending", True],
        "running": [True, "v-running", True],
        "transport": [True, "v-transport", "b", "iv-transport", True],
        "idle": [False, False, False, False],
    }


def test_video_poll_retries_transport_and_delivers_result(js):
    """Временный сбой опроса НЕ роняет оплаченную генерацию (статус-ретрай).

    pollVideo исполняется боевым в node с мокнутым fetch: первый опрос падает
    (reject), второй возвращает done+result. Раньше (2026-08-24) один сбой сразу
    гасил VIDPOLL, стирал VIDCTX и показывал ложное «не удалось получить статус»,
    а доехавший позже done UI не подхватывал без F5. Теперь после сбоя poller
    выживает (запланирован повтор, счётчик вырос), onError/onSettled на сбое НЕ
    зовутся, а повтор доставляет result в контекст и завершает по-нормальному.
    """
    poll = _fn_body(js, "async function pollVideo(")
    helpers = "\n".join(_func(js, n) for n in
                        ("vidRetryWait", "videoStateLost", "videoFinish", "videoCall"))
    script = (
        "const events=[],prog=[];"
        "let VIDPOLL=true,VIDRETRY=0,VIDRT=null,VIDCTX=null,VIDCANCEL=false,"
        "LOGCACHE=[],LOGSINCE=0;"
        "const VID_RETRY_MS=[2000,4000,8000,15000];"
        "let calls=0;const resp=["
        "Promise.reject(new Error('net')),"
        "{ok:true,json:()=>Promise.resolve({running:false,done:true,"
        "  result:{url:'http://x/y.mp4',cost:0.01},key:'k',context:'tok'})}];"
        "globalThis.fetch=()=>resp[Math.min(calls++,resp.length-1)];"
        "let sched=null;globalThis.setTimeout=(fn,ms)=>{sched={fn,ms};return 1;};"
        "globalThis.clearTimeout=()=>{};"
        "function t(v){return v;}"
        "function mergeLog(){}function fmtLog(){return '';}"
        "function progUpdate(a,b,c,d){prog.push([a,b,c,d]);}"
        # stage/хвост лога разбирает общая форма прогресса (static/app/55-progress.js)
        "function progEventFromLog(){return '';}const PROGEV={};"
        "function progDone(){}function vidBusy(){}function vidHistLoad(){}function uiLog(){}"
        "function videoContextForStatus(d){return null;}"
        "function errText(d){return d&&d.error||'';}"
        "const $=()=>({style:{},textContent:'',className:''});"
        "const ctx={token:'tok',onStart(){},onResult:res=>events.push(['result',res.url]),"
        "  onError:d=>events.push(['error',d.error]),"
        "  onSettled:d=>events.push(['settled',!!d.transport])};"
        "VIDCTX=ctx;"
        "pollVideo().then(()=>{const first=[...events];const b=sched;const r0=VIDRETRY;"
        "  b.fn().then(()=>{"
        "    console.log(JSON.stringify({first,after:events,backoff:b.ms,retry:r0,prog}));"
        "  });});"
        # _fn_body возвращает ТЕЛО pollVideo (хвост после маркера) — нужна полная декларация
        + helpers + "\n" + "async function pollVideo(" + poll
    )
    out = _run_node(script)
    assert out["first"] == [], "первый сбой не должен давать onError/onSettled"
    assert out["backoff"] == 2000, "первый повтор идёт не с 2с"
    assert out["retry"] == 1, "счётчик transport-сбоев не вырос после первого сбоя"
    assert out["after"] == [["result", "http://x/y.mp4"], ["settled", False]], (
        "повтор не доставил result в контекст или завершил неверно")
    assert any(p[2] and "связь со статусом потеряна" in str(p[3]) for p in out["prog"]), (
        "во время retry прогресс не пишет «связь со статусом потеряна — повторяю…»")


def test_video_poll_lost_state_is_server_not_network(js):
    """Валидный {running:false,done:false} — terminal-состояние СЕРВЕРА, не сетевой retry.

    Рестарт/перезапись VJOB возвращает idle без done. Это не временный сбой транспорта:
    backoff тут бессмыслен. Poller останавливается, но связь карточки бережётся
    (transport:true, чтобы insVideoSettle не стёр video_target), а текст — про потерю
    состояния сервером, а не про сеть.
    """
    poll = _fn_body(js, "async function pollVideo(")
    lost = _func(js, "videoStateLost")
    helpers = "\n".join(_func(js, n) for n in
                        ("vidRetryWait", "videoFinish", "videoCall"))
    script = (
        "const events=[],prog=[];"
        "let VIDPOLL=true,VIDRETRY=0,VIDRT=null,VIDCTX=null,VIDCANCEL=false,"
        "LOGCACHE=[],LOGSINCE=0;"
        "const VID_RETRY_MS=[2000,4000,8000,15000];"
        "globalThis.fetch=()=>Promise.resolve({ok:true,"
        "  json:()=>Promise.resolve({running:false,done:false})});"
        "let sched=null;globalThis.setTimeout=(fn,ms)=>{sched={fn,ms};return 1;};"
        "globalThis.clearTimeout=()=>{};"
        "function t(v){return v;}"
        "function mergeLog(){}function fmtLog(){return '';}"
        "function progUpdate(a,b,c,d){prog.push([a,b,c,d]);}"
        # stage/хвост лога разбирает общая форма прогресса (static/app/55-progress.js)
        "function progEventFromLog(){return '';}const PROGEV={};"
        "function progDone(){}function vidBusy(){}function vidHistLoad(){}function uiLog(){}"
        "function videoContextForStatus(d){return null;}"
        "function errText(d){return d&&d.error||'';}"
        "const $=()=>({style:{},textContent:'',className:''});"
        "const ctx={token:'tok',onStart(){},onResult:res=>events.push(['result']),"
        "  onError:d=>events.push(['error',d.error]),"
        "  onSettled:d=>events.push(['settled',!!d.transport,!!d.lost])};"
        "VIDCTX=ctx;"
        "pollVideo().then(()=>{"
        "  console.log(JSON.stringify({events,VIDPOLL,sched:!!sched,prog}));});"
        # _fn_body возвращает ТЕЛО pollVideo (хвост после маркера) — нужна полная декларация
        + helpers + "\n" + lost + "\n" + "async function pollVideo(" + poll
    )
    out = _run_node(script)
    assert out["VIDPOLL"] is False, "idle-статус не остановил poller"
    assert out["sched"] is False, "idle-статус запланировал сетевой retry"
    assert out["events"] == [
        ["error", "Сервер потерял состояние генерации — проверь историю задач"],
        ["settled", True, True],
    ], "idle-статус завершён не как потеря состояния (с сохранением связи)"


def test_video_resolution_settings_contract(js):
    """EZB: разрешение — общая настройка в ai_config, резолвится против caps модели."""
    # setVideoResolution шлёт action set_video_resolution и применяет ответ.
    setres = _fn_body(js, "async function setVideoResolution(")
    assert "action:'set_video_resolution'" in setres or "action:\"set_video_resolution\"" in setres
    assert "AICFG.video_resolution=d.video_resolution" in setres
    assert "vidFillResolution()" in setres, "после сохранения селект не перестраивается"
    # setVideoModel переносит сброшенное сервером разрешение (без скрытого local state).
    setmodel = _fn_body(js, "async function setVideoModel(")
    assert "AICFG.video_resolution=d.video_resolution" in setmodel
    # vidFillResolution: «по умолчанию» + только caps.resolutions, устаревшее -> default.
    fill = _fn_body(js, "function vidFillResolution(")
    assert "по умолч." in fill and "vidResList()" in fill
    assert "sel.value=known?cur:''" in fill, "устаревшее разрешение должно уходить на «по умолчанию»"
    # Live-catalog sync переоценивает разрешение у сервера и применяет итог (нет local state).
    sync = _fn_body(js, "async function vidSyncCaps(")
    assert "AICFG.video_resolution=d.video_resolution" in sync, (
        "sync каталога не применяет переоценённое сервером разрешение — останется local state")
    # «Повторить» из истории: awaited модель → разрешение → сообщение; old/unsupported безопасны.
    reuse = _fn_body(js, "async function vidHistReuse(")
    assert "vid_res" not in reuse, "history reuse больше не трогает vid_res напрямую"
    assert "await setVideoModel(it.model)" in reuse
    assert "await setVideoResolution(o.resolution)" in reuse
    assert "vidSupportsRes(o.resolution)" in reuse, (
        "разрешение из истории выставляется только если поддерживается моделью")
    assert reuse.index("await setVideoModel") < reuse.index("await setVideoResolution"), (
        "«Повторить» должен ДОЖДАТЬСЯ смены модели, прежде чем ставить разрешение")
    assert reuse.index("await setVideoResolution") < reuse.index("Настройки задачи подставлены"), (
        "«Настройки задачи подставлены» показывается только после awaited server actions")


def test_style_panel_cp2_sldnum_and_pairs(html, css, js):
    """Задание CU (в редакции JB): у каждой величины есть и число, и ползунок, пары X/Y — одно поле.

    Списка id в разметке больше нет: строки строит панель по схеме, поэтому проверяем
    схему (все 22 величины на месте, у пары X/Y — один узел с key2) и общий код панели,
    который рисует ползунок КАЖДОМУ числовому полю, а не выписанному списку.
    """
    panel = _panel_js()
    quantity = ("num", "int", "angle", "range")
    for key in ("cam1_fit", "cam1_drift_lo", "cam1_drift_hi", "start_blur", "start_blur_dur",
                "sub_y", "sub_words_per_row", "sub_rows_max", "intro_scale", "intro_glow",
                "intro_y", "intro_y2", "insert_c2_y", "insert_c1_y", "insert_c1_x",
                "insert_c1on2_y", "insert_c1on2_x", "music_db", "voice_db", "pop_lead",
                "pop_db", "roto_bottom"):
        field = _schema_field(key)
        assert field, f"в схеме нет величины {key}"
        assert field["ctl"] in quantity, f"{key}: контрол {field['ctl']} вместо величины"

    # ползунок, число и правка с клавиатуры — у каждого числового поля, из одного места
    assert "'st_' + item.key + '_slider'" in panel, "панель не строит ползунок"
    assert "'st_' + item.key + '_val'" in panel and "'st_' + item.key + '_input'" in panel
    assert "range.min = item.min" in panel and "range.max = item.max" in panel, (
        "ползунок больше не берёт границы из схемы")
    assert "function stSliderInput(" in panel and "function initNumDrag(" in panel

    # Пара X/Y — ОДНО поле строки с вторым ключом (отдельной строки для Y нет)
    point = _schema_field("cam1_zoom_cx")
    assert point["ctl"] == "point" and point.get("key2") == "cam1_zoom_cy", (
        "точка наезда перестала быть парой X/Y одним полем")
    assert _schema_field("cam1_zoom_cy") is point, "вторая половина пары живёт отдельным полем"


def test_style_panel_cp3_all_fields_call_stedit(html):
    """Задание CP3/CU (в редакции JB): каждое поле стиля ведёт в stEdit().

    Полей в разметке больше нет — их строит панель по схеме, — поэтому проверяем саму
    панель: у каждого типа контрола обработчик заканчивается вызовом stEdit() (или
    зовёт помощника, который в него ведёт: перетаскивание числа, ползунок, HEX, галки,
    «Сброс»). Раньше это приходилось проверять по каждой строке index.html, и забытое
    поле не ловилось ничем.
    """
    panel = _panel_js()
    assert 'id="stpanel"' in html and "stylepart_" not in html, (
        "в index.html осталась старая разметка полей стиля")

    # Контролы панели: у каждого свой обработчик. stHexInput сюда не входит нарочно —
    # он только подкрашивает образец по ходу набора, а в стиль пишет stHexChange (onchange).
    for header in ("function initNumDrag(", "function stSliderInput(", "function stHexChange(",
                   "function stColorSwatchChange(",
                   "function stToggleLayer(", "function stToggleGroup(",
                   "function createAngleDial(", "function stReset(", "function stResetKey("):
        assert "stEdit()" in _fn_body(panel, header), (
            "%s не ведёт в stEdit() — правка поля не доедет до стиля" % header)

    # простые контролы подключаются к stEdit прямо в разметке панели. У ручек выбора
    # трека (флаг track) — своя дверь musicFieldEdit: она решает, писать переопределение
    # клипа или сам стиль, и в области стиля ведёт в тот же stEdit.
    for line in ("chk.onchange = () => (item.track && typeof musicFieldEdit === 'function')",
                 "sel.onchange = () => (item.track && typeof musicFieldEdit === 'function')",
                 "inp.onchange = () => (item.track && typeof musicFieldEdit === 'function')",
                 "inp.oninput = () => stEdit();",
                 "ta.onchange = () => stEdit();", "ta.oninput = () => stEdit();",
                 "hex.onchange = () => stHexChange(item.key, hex.value);",
                 "swatch.onchange = () => stColorSwatchChange(item.key, swatch.value);"):
        assert line in panel, "в панели пропала привязка контрола к stEdit(): " + line
    assert "? musicFieldEdit() : stEdit();" in panel, (
        "дверь ручек трека не ведёт в stEdit в области стиля")


def test_style_panel_cp3_layout_and_dots(html, css, js):
    """Задание CP3/CU: высота панели, прижатый actbar сохранения, зелёные точки."""
    # 1. Проверяем, что нет зашитого max-height:52vh у #aewstyle и max-height:38vh у .aewpanel .words
    assert "max-height:52vh" not in css, "у #aewstyle остался max-height:52vh"
    assert "max-height:38vh" not in css, "у .aewpanel .words остался max-height:38vh"
    assert "flex:1" in css, "#aewstyle не тянется flex:1"

    # 2. Ряд сохранения .stactbar со sticky и .actbar
    assert ".stactbar" in css, "в app.css нет класса .stactbar"
    assert "position:sticky;bottom:0" in css, "в .stactbar нет position:sticky;bottom:0"
    assert 'class="row actbar stactbar"' in html, "в index.html ряд сохранения не оформлен как .row.actbar.stactbar"
    assert '<button class="primary" onclick="saveStyle()">' in html, "кнопка Сохранить не primary"

    # 3. Зелёные точки на изменённых полях (.st-changed)
    assert ".st-changed" in css, "в app.css нет стилей .st-changed"
    assert "updateStyleDiffDots" in js, "в JS нет функции updateStyleDiffDots"


def test_preview_modal_cw_bounds(css):
    """Задание CW: модалка предпросмотра ограничена окном 94vh, тело и ряд колонок flex:1."""
    assert ".modal.aemode{max-height:94vh;display:flex;flex-direction:column}" in css or (
        "max-height:94vh" in css and ".modal.aemode" in css
    ), "у .modal.aemode нет max-height:94vh или flex layout"
    assert ".modal.aemode .mbody" in css, "нет правила .modal.aemode .mbody"
    assert "overflow:hidden" in css, "у .modal.aemode .mbody нет overflow:hidden"
    assert ".modal.aemode .inscols" in css, "нет правила .modal.aemode .inscols"


def test_preview_font_styles_db(js):
    """Задание DB: превью подключает файл шрифта через @font-face и /api/fontfile/."""
    assert "ensureFontFace(" in js, "нет функции ensureFontFace"
    assert "/api/fontfile/" in js, "нет запроса к роуту /api/fontfile/"
    assert "@font-face" in js, "нет объявления @font-face"

    font_for = _fn_body(js, "function ipvFontFor(")
    assert "ensureFontFace(ps)" in font_for, "ipvFontFor не вызывает ensureFontFace"
    assert "'reelsi-'+ps" in font_for, "ipvFontFor не возвращает reelsi- семейство"

    subs = _fn_body(js, "function ipvSubs(")
    assert "font-variation-settings:" in subs, "fvCss не применяет font-variation-settings"
    assert "font-weight:800" in subs, "fallback 800 для отсутствующего шрифта сломан в fvCss"

    intro = _fn_body(js, "function ipvIntro(")
    assert "fontVariationSettings" in intro, "ipvIntro не применяет fontVariationSettings"
    assert "fontWeight='800'" in intro, "fallback 800 для отсутствующего шрифта сломан в ipvIntro"


def test_sub_shadow_and_clean_preview_dk(js, css):
    """Задание DK: тень субтитров и очистка текстовых узлов в превью."""
    subs = _fn_body(js, "function ipvSubs(")
    assert "node.nodeType===3" in subs or "node.nodeType === 3" in subs
    assert "pvsub_bg" in subs and "pvsubs_host" in subs
    assert "--subsh" in subs
    assert "pl.sub_shadow" in subs

    assert "--subsh" in css


def test_style_diff_vars_all_declared_dm():
    """Задание DM (в редакции JB): точки «изменено» считает одна общая функция по схеме.

    До JB на каждое поле стиля в updateStyleDiffDots жила своя переменная d_<имя>.
    Удаление поля из разметки без удаления его переменной давало ReferenceError при
    вызове updateStyleDiffDots(), падение loadStyles() и пустой список стилей — то есть
    цена забывчивости была высокой, а поймать её было нечем. Теперь точек одна дверь:
    обход схемы, сравнение с дефолтом родителя (STSCHEMA.base). Сторожим её
    единственность и то, что необъявленных d_*-переменных в коде не осталось.
    """
    js = _read(os.path.join(ROOT, "static", "app", "95-styles.js"))
    clean = re.sub(r"//.*$", "", js, flags=re.MULTILINE)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    declared = set(re.findall(r"\b(?:const|let|var)\s+(d_[a-zA-Z0-9_]+)\s*=", clean))
    used = set(re.findall(r"\b(d_[a-zA-Z0-9_]+)\b", clean))
    assert not (used - declared), (
        "необъявленные переменные отличий в 95-styles.js: " + ", ".join(sorted(used - declared))
    )

    panel = _panel_js()
    app_js = app_meta.app_js_text()
    assert app_js.count("function updateStyleDiffDots(") == 1, (
        "дверей у точек «изменено» стало больше одной")
    assert "function updateStyleDiffDots(" in panel, "точки «изменено» ушли из панели"
    body = _fn_body(panel, "function updateStyleDiffDots(")
    assert "STSCHEMA.layers" in body and "STSCHEMA.base" in body, (
        "точки считаются не по схеме и её дефолтам")
    assert "updateStyleSaveUI()" in body, "точки не обновляют ряд сохранения"


def test_style_element_ids_exist_in_html_dm(html):
    """Задание DM (в редакции JB): обращение к полю стиля (st_*) ведёт к существующему элементу.

    Поля стиля больше не выписаны в index.html — их строит панель из схемы, — поэтому
    id бывает двух родов: постоянные (в разметке: st_name, st_saved, st_pickzoom,
    st_layer_order_list, st_disc_text) и выведенные из ключа схемы (st_<key> и его части
    _val/_input/_slider/_hex/_color, st_<toggle> у галки и st_<link> у кнопки-цепочки).
    Обращение к id, которого не будет ни там, ни там, — это null-deref, ради которого
    тест и заведён.
    """
    html_ids = set(re.findall(r"""\bid=["']([^"']+)["']""", html))
    derived = set()
    for key in (it.get("key") for _kind, it in watcher.schema_items() if it.get("key")):
        derived |= {"st_" + key, "st_" + key + "_val", "st_" + key + "_input",
                    "st_" + key + "_slider", "st_" + key + "_hex", "st_" + key + "_color"}
    for key in (it.get("toggle") for _kind, it in watcher.schema_items() if it.get("toggle")):
        derived.add("st_" + key)
    for key in (it.get("link") for _kind, it in watcher.schema_items() if it.get("link")):
        derived.add("st_" + key)
    # постоянные id панели и предпросмотра (см. JB п. 3, список оставшихся обращений).
    # st_music_* — блок «Трек ролика» в группе «Музыка»: ручек схемы у него нет (это
    # состояние открытого клипа, а не настройка стиля), id создаёт сама панель
    # (musicTrackNode), поэтому вывести их из ключей схемы нельзя.
    known = {"stpanel", "st_name", "st_saved", "st_pickzoom", "st_layer_order_list",
             "st_disc_text", "st_music_scope", "st_music_scope_row", "st_music_track",
             "st_music_reroll", "st_music_revert", "st_music_src_dl"}

    pattern = re.compile(r"""(?:\$|getElementById|val|num|setParentDot)\s*\(\s*["'](st_[a-zA-Z0-9_]+)["']\s*\)""")
    missing = []
    dynamic = 0
    for js_path in app_meta.app_js_files():
        js_text = _read(js_path)
        js_clean = re.sub(r"//.*$", "", js_text, flags=re.MULTILINE)
        js_clean = re.sub(r"/\*.*?\*/", "", js_clean, flags=re.DOTALL)
        for m in pattern.finditer(js_clean):
            el_id = m.group(1)
            if el_id in html_ids or el_id in known:
                continue
            if el_id in derived:
                dynamic += 1
                continue
            missing.append(f"{el_id} ({os.path.basename(js_path)})")
    assert not missing, (
        "обращения к несуществующим полям стиля (st_*) в index.html: " + ", ".join(sorted(missing))
    )
    assert dynamic > 0, "ни одного обращения к полю из схемы — проверка выродилась"


def test_no_camcustom_and_speaker_dirs_restores_cams(js):
    """Папки камер восстанавливаются на загрузке страницы, а флаг источника переведён на CAMFROM.

    Пойманный баг (2026-08-20): applySpeakerDirs восстанавливал только outdir/jsxdir/renderdir,
    а папки камер ехали только через onSpeakerChange. После F5 оставался автоподбор,
    очередь хранила только имена файлов спикера, и нарезка склеивала чужие папки с именами.
    Булев флаг CAMCUSTOM путал «можно ли перетереть автоподбором» и «спрашивать ли перед
    заменой» — заменён строкой источника CAMFROM ('', 'user', 'spk').

    Чтение s.CAMCUSTOM в applyState() разрешено как миграция состояния,
    сохранённого до 2026-08-20; без чтения старого ключа у пользователя слетит
    выбранная папка камеры и её затрёт автоподбор.
    """
    from core import app_meta as _am

    # В static/app/*.js нет идентификатора CAMCUSTOM
    bad_ident = []
    rx_state_prop = re.compile(r"\bs\.CAMCUSTOM\b")
    rx_ident = re.compile(r"\bCAMCUSTOM\b")
    for path in _am.app_js_files():
        for n, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
            code_line = rx_state_prop.sub("", _js_code_only(line))
            if rx_ident.search(code_line):
                bad_ident.append("%s:%d %s" % (os.path.basename(path), n, line.strip()[:70]))
    assert not bad_ident, "в коде static/app/ остался идентификатор CAMCUSTOM:\n" + "\n".join(bad_ident)

    # Тело applySpeakerDirs обязано содержать вызов camDirApply для восстановления папок камер
    assert "function applySpeakerDirs(" in js, "функция applySpeakerDirs пропала"
    body = _fn_body(js, "function applySpeakerDirs(")
    assert "camDirApply(" in body, "applySpeakerDirs не вызывает camDirApply — папки камер потеряются после F5"


def test_norm_ins_path_declared_once():
    """normInsPath объявлена РОВНО один раз во всех static/app/*.js ().

    Файлы static/app/*.js грузятся в один глобальный скоуп. Дубль в 90-ae.js был вторым
    источником: при разъезде сопоставление вставок ломалось бы по-разному в разных местах.
    """
    app_dir = os.path.join(ROOT, "static", "app")
    pattern = re.compile(r"\bfunction\s+normInsPath\s*\(")
    matches = []
    for fname in sorted(os.listdir(app_dir)):
        if fname.endswith(".js"):
            path = os.path.join(app_dir, fname)
            content = io.open(path, encoding="utf-8").read()
            for line_no, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    matches.append(f"{fname}:{line_no}")
    assert len(matches) == 1, f"normInsPath объявлена не один раз: {matches}"
    assert matches[0].startswith("85-inserts-view.js:"), (
        f"normInsPath должна быть объявлена в 85-inserts-view.js, а не {matches}"
    )


@pytest.mark.skipif(not shutil.which("node"), reason="проверка cardToIns исполняет боевую функцию под node")
def test_card_to_ins_duration_default_when_zero(js):
    """Дефолт длительности в cardToIns при duration_sec=0 даёт dur_s=2 ().

    В исходном ensureJobs было (x.duration_sec||2). При замене на !=null ? ... : 2
    значение duration_sec=0 превращалось в dur_s=0 и вставка исчезала из сборки AE.
    """
    fn = _fn_body(js, "function cardToIns(")
    assert "x.duration_sec||2" in fn, (
        "cardToIns должен использовать ||2 для сохранения дефолта 2с при duration_sec=0"
    )
    fn_card = _func(js, "cardToIns")
    out = _run_node(
        f"{fn_card}\n"
        "const r = cardToIns({duration_sec: 0});\n"
        "console.log(JSON.stringify(r));"
    )
    assert out["dur_s"] == 2, f"dur_s при duration_sec=0 должен быть 2, получено {out['dur_s']}"


def test_i18n_data_containers_opt_out(html, js):
    """Контейнеры с данными ролика помечены data-noi18n, applyI18n их пропускает.

    В английском интерфейсе applyI18n и MutationObserver не должны переводить текст ролика
    (субтитры, интро, слова, запросы/имена файлов вставок), совпавший со словами словаря.
    """
    # 1. Разметка в index.html
    assert re.search(r'id="ipvsub"[^>]*data-noi18n', html), (
        "контейнер субтитров #ipvsub обязан иметь атрибут data-noi18n"
    )
    assert re.search(r'id="ipvintro"[^>]*data-noi18n', html), (
        "контейнер интро #ipvintro обязан иметь атрибут data-noi18n"
    )
    assert re.search(r'id="pvsub"[^>]*data-noi18n', html), (
        "контейнер субтитров #pvsub обязан иметь атрибут data-noi18n"
    )
    assert re.search(r'id="aewwords"[^>]*data-noi18n', html), (
        "контейнер слов #aewwords обязан иметь атрибут data-noi18n"
    )
    assert re.search(r'id="subrowslist"[^>]*data-noi18n', html), (
        "список строк субтитров #subrowslist обязан иметь атрибут data-noi18n"
    )

    # 2. applyI18n и startI18nObserver в 00-core.js содержат проверку closest('[data-noi18n]')
    apply_fn = _fn_body(js, "function applyI18n(")
    assert "data-noi18n" in apply_fn and "closest" in apply_fn, (
        "applyI18n обязана проверять closest('[data-noi18n]')"
    )
    obs_fn = _fn_body(js, "function startI18nObserver(")
    assert "data-noi18n" in obs_fn and "closest" in obs_fn, (
        "startI18nObserver обязан проверять closest('[data-noi18n]')"
    )


def test_log_modal_tabs_and_blocks_ea(html, js):
    """Задание EA: окно логов с двумя вкладками (Сборка/Действия на странице), двумя блоками и без logpre."""
    # 1. Старого одиночного logpre не осталось ни в HTML, ни в JS
    assert not re.search(r'\bid=["\']logpre["\']', html), "в index.html остался старый id='logpre'"
    assert "$('logpre')" not in js and 'getElementById("logpre")' not in js, "в JS остался селектор logpre"

    # 2. Оба блока логов есть в HTML
    assert 'id="logpre_server"' in html, "в index.html нет #logpre_server"
    assert 'id="logpre_client"' in html, "в index.html нет #logpre_client"

    # 3. Обе вкладки и переключатель присутствуют
    assert 'name="logtab"' in html, "в index.html нет переключателя вкладок name='logtab'"
    assert 'value="server"' in html and 'value="client"' in html, "нет радиокнопок server/client в logtab"
    assert "Сборка" in html and "Действия на странице" in html, "нет подписей вкладок Сборка / Действия на странице"

    # 4. Функции управления логом объявлены в JS
    assert "function setLogTab(" in js, "нет функции setLogTab"
    assert "function _logScrolled(" in js, "нет функции _logScrolled"
    assert "function _logSetText(" in js, "нет функции _logSetText"
    assert "LOGLAST" in js and "LOGTAB" in js, "нет переменных LOGLAST / LOGTAB"

    # 5. refreshLog и setLogTab работают с обоими блоками
    refresh_fn = _fn_body(js, "function refreshLog(")
    assert "logpre_server" in refresh_fn and "logpre_client" in refresh_fn, (
        "refreshLog обязан обновлять оба блока (logpre_server и logpre_client)"
    )
    tab_fn = _fn_body(js, "function setLogTab(")
    assert "logpre_server" in tab_fn and "logpre_client" in tab_fn, (
        "setLogTab обязан переключать видимость обоих блоков"
    )


def test_clip_stores_style_name_not_copy(js):
    """Клип хранит ИМЯ стиля, копия — только у безымянного кастома.
    Копия в задании и была источником рассинхрона: правка стиля до клипа не доезжала.
    Рото стало свойством стиля, пометка styleOwn удалена вместе с механизмом разноса."""
    cap = _fn_body(js, "function captureAE()")
    assert "delete j.style" in cap, "копия стиля не снимается у клипа с именованным стилем"
    assert "j.roto=" not in cap, "клиповое рото снова пишется в задание"
    assert "styleOwn" not in cap, "пометка styleOwn вернулась"
    dj = _fn_body(js, "function defJob()")
    assert "roto:" not in dj, "поля рото вернулись в дефолтное задание"
    assert "function migrateClipStyles()" in js, "миграции состояния нет"
    assert "migrateClipStyles()" in _fn_body(js, "async function loadStyles()"), (
        "миграция не вызывается из loadStyles")
    assert "STYLE_LOCAL=['label']" in js.replace(" ", ""), (
        "STYLE_LOCAL не сведён к label — рото исключается из сравнения стилей")


# ---------------- задание: средняя кнопка = пан, метлы списков, undo вставки ----------------

def test_editor_middle_button_pans_and_keeps_the_rest(js):
    """Средняя кнопка мыши в редакторе нарезки = горизонтальный пан из ЛЮБОЙ точки.

    Левые драги (края/плейхед/селекция, линейка) и Shift+колесо остаются как были.
    У среднего клика в браузере дефолт — middle autoscroll, его гасим preventDefault
    на mousedown и auxclick; курсор grabbing — только на время пана и снимается.
    """
    bind = js[js.index("function edBind()"):js.index("function spaceOnControl(")]
    down = bind[bind.index("addEventListener('mousedown'"):bind.index("addEventListener('wheel'")]
    assert "e.button===1" in down, "средняя кнопка не обрабатывается в mousedown"
    assert "ED.drag={pan:1,x0:x,pv0:ED.v0,pv1:ED.v1" in down, "средняя кнопка не заводит пан"
    assert "preventDefault" in down, "средний клик не гасит autoscroll"
    assert "c.style.cursor='grabbing'" in down, "курсор grabbing не ставится при пане"
    # пан из любой точки: средний клик проверяется ДО ветки линейки (y<=EDRULER)
    assert down.index("e.button===1") < down.index("if(y<=EDRULER"), (
        "средняя кнопка проверяется после линейки — пан не из любой точки")
    # прежний курсор canvas сохраняется в снимке и возвращается, а не снимается пустой
    # строкой: у #edtl cursor задан inline, '' его убирает совсем
    assert "cursor:c.style.cursor" in down, "пан не запоминает прежний курсор"
    assert "auxclick" in bind, "middle autoscroll не глушится через auxclick"
    # существующие жесты сохранены
    assert "e.shiftKey" in bind[bind.index("addEventListener('wheel'"):], "Shift+колесо пропало"
    assert "if(y<=EDRULER*dpr()){ED.drag={pan:1" in down, "пан линейки пропал"
    up = bind[bind.index("addEventListener('mouseup'"):]
    assert "d.cursor" in up and "c.style.cursor=d.cursor" in up, (
        "на mouseup курсор не возвращается к прежнему значению")


def test_bulk_clear_list_only_never_touches_the_disk(js, html):
    """Метла в шапке клипов шага 1 убирает ВСЕ клипы только из CLIPS/UI.

    Дисковые XML и сайдкары не трогаем — это не delClip, тот чистит диск через
    /api/clip_delete. Поэтому подтверждение и справка говорят, что файлы остаются и
    список подхватится снова «Из папки результата», а индексы открытых клипов
    сбрасываются общим безопасным путём _spliceClip.
    """
    assert 'id="clipsClear"' in html, "кнопки-метлы в шапке списка клипов нет"
    btn = html[html.index('id="clipsClear"') - 140:html.index('id="clipsClear"') + 520]
    assert 'data-ic="broom"' in btn, "кнопка не с метлой"
    assert 'aria-label=' in btn and 'data-t=' in btn, "у кнопки нет имени/справки"
    body = _fn_body(js, "async function clearClipsList(")
    assert "askConfirm(" in body, "уборка ВСЕХ клипов без подтверждения — накликается случайно"
    assert "_spliceClip" in body, "индексы открытых клипов не правятся общим путём"
    assert "/api/clip_delete" not in body, "list-only очистка зовёт удаление с диска"
    assert "renderClips1()" in body and "renderClips2()" in body and "renderClips3()" in body, (
        "очистка не перерисовывает все списки")
    assert "saveState()" in body, "очистка не сохраняет состояние"


def test_remove_selected_works_only_on_checked_clips(js, html):
    """Метла в шапке «Файлы набора» убирает ТОЛЬКО отмеченные (c.sel) клипы.

    Пустой выбор НЕ означает «все»: это семантика selClips для СБОРКИ, а здесь она
    опасна — «убрать отмеченное» с пустым выбором убрало бы всё. /api/clip_delete
    не зовём: дисковые файлы остаются, список подхватится «Из папки результата».
    Кнопка disabled, когда нечего убирать (ставит syncBuildBtn).
    """
    assert 'id="rmSelClips"' in html, "кнопки-метлы в шапке набора нет"
    btn = html[html.index('id="rmSelClips"') - 140:html.index('id="rmSelClips"') + 560]
    assert 'data-ic="broom"' in btn and 'aria-label=' in btn and 'data-t=' in btn, (
        "кнопка не с метлой / без имени / без справки")
    assert 'disabled' in html[html.index('id="rmSelClips"'):html.index('id="rmSelClips"') + 80], (
        "кнопка не disabled при пустом выборе")
    body = _fn_body(js, "async function removeSelClips(")
    assert "c.sel" in body, "отбор идёт не по галочкам"
    assert "selClips()" not in body, "семантика «пусто = все» просочилась в уборку"
    assert "idx.sort((a,b)=>b-a)" in body, "удаление идёт не в обратном порядке"
    assert "_spliceClip" in body, "индексы открытых клипов не правятся общим путём"
    assert "/api/clip_delete" not in body, "уборка из списка зовёт удаление с диска"
    assert "askConfirm(" in body and "Ничего не отмечено" in body, "нет подтверждения/сообщения без галочек"
    assert "renderClips1()" in body and "renderClips2()" in body and "renderClips3()" in body
    assert "saveState()" in body
    sync = _fn_body(js, "function syncBuildBtn(")
    assert "rmSelClips" in sync, "disabled кнопки не обновляется из syncBuildBtn"


def test_inserts_history_undo_redo_contract(js, html):
    """Отмена и повтор правок вставок: снимок состояния, ОДНА дверь записи, кнопки в окне.

    Раньше отмена была одноуровневой (INS_UNDO) и только на удаление. Теперь история на клип:
    снимок {inserts, ins_rejected, insTarget, job_ins}, стеки отмены и повтора глубиной 100, а
    пишет в них ОДНА дверь — insHistTouch, которую зовёт saveState (поэтому в историю попадает
    ЛЮБАЯ правка, а не перечисленные вручную двери). Поведение — пять разных правок, пять
    отмен, повтор и предел глубины — гоняет tests/test_ins_identity_undo.py на боевых функциях.
    """
    assert 'id="insUndoBtn"' in html, "кнопки отмены в окне вставок нет"
    btn = html[html.index('id="insUndoBtn"') - 120:html.index('id="insUndoBtn"') + 260]
    assert 'data-ic="undo"' in btn and 'aria-label=' in btn and 'data-t=' in btn, (
        "кнопка не с undo-иконкой / без имени / без справки")
    assert "Ctrl+Z" in btn, "кнопка отмены не подписана горячей клавишей"
    assert 'id="insRedoBtn"' in html and 'onclick="insRedo()"' in html, (
        "рядом с отменой нет кнопки повтора")
    redo = html[html.index('id="insRedoBtn"') - 120:html.index('id="insRedoBtn"') + 260]
    assert 'aria-label=' in redo and 'data-t=' in redo, "у кнопки повтора нет имени и справки"
    assert "Ctrl+Shift+Z" in redo, "кнопка повтора не подписана горячей клавишей"

    # двери: запись — только insHistTouch, а insDel своей копии снимка больше не держит
    dele = _fn_body(js, "function insDel(")
    assert "INS_UNDO" not in dele, "у insDel снова своя копия отмены вместо общего журнала"
    assert "saveState()" in dele, "удаление не доходит до общей двери истории (saveState)"

    boot = _read(os.path.join(ROOT, "static", "app", "99-boot.js"))
    save = boot[boot.index("function saveState()"):boot.index("function srvStateSave(")]
    assert "insHistTouch()" in save, "saveState не зовёт insHistTouch — история останется пустой"

    hist = _fn_body(js, "function insHistState(")
    for fld in ("inserts", "ins_rejected", "insTarget", "job_ins"):
        assert fld in hist, "в снимок истории не попало поле " + fld
    touch = _fn_body(js, "function insHistTouch(")
    assert "h.undo.push(last)" in touch and "h.redo.length=0" in touch, (
        "новая правка не уходит в стек отмены или не очищает стек повтора")
    assert "INS_HIST_MAX" in touch, "глубина стека отмены не ограничена"
    und = _fn_body(js, "function insUndo(")
    assert "h.undo.pop()" in und and "h.redo.push(" in und, (
        "отмена не перекладывает состояние в стек повтора")
    red = _fn_body(js, "function insRedo(")
    assert "h.redo.pop()" in red and "h.undo.push(" in red, (
        "повтор не возвращает состояние обратно в стек отмены")
    ap = _fn_body(js, "function insHistApply(")
    assert "INS_HIST_APPLY=true" in ap, (
        "применение снимка не защищено флагом — откат сам станет шагом истории")
    assert "ipvAfterEdit()" in ap, "после отката экран не перерисовывается общим путём"


def test_inserts_history_clip_and_depth_are_declared_once(js):
    """Журнал истории — один на клип, а глубина задана одним числом.

    Стек на клип (ключ — xml): переключение клипа в окне зовёт insHistReset, иначе на новом
    клипе отменялись бы правки прежнего. Отдельная копия «undo для удаления» вернулась бы —
    её тут и сторожим.
    """
    assert "INS_HIST={}" in js or "INS_HIST = {}" in js, "журнал истории не заведён"
    reset = _fn_body(js, "function insHistReset(")
    assert "INS_HIST[c.xml]={undo:[],redo:[]}" in reset, (
        "сброс истории не заводит пустые стеки отмены/повтора на клипе")
    assert "INS_HIST_LAST[c.xml]=" in reset, "сброс истории не ставит базовый снимок"
    assert "INS_UNDO" not in js, "вернулась прежняя одноуровневая отмена (INS_UNDO)"
    assert js.count("INS_HIST_MAX=100") == 1, "предел глубины истории задан не одним числом"


def test_insdel_goes_through_the_common_history_door(js):
    """insDel — обычная правка: мутирует состояние и зовёт saveState, своей отмены не держит.

    Одноуровневый снимок внутри insDel (INS_UNDO) ушёл вместе с прежней отменой: удаление
    попадает в общий журнал тем же путём, что тайминг, выбор файла и драг. Копия журнала
    (или снимок «до splice» рядом с ним) снова разошлась бы с общей дверью — на этом уже
    горели introResolve.
    """
    dele = _fn_body(js, "function insDel(")
    assert "INS_UNDO" not in dele, "в insDel вернулась своя копия отмены"
    assert "ins_rejected" in dele, (
        "удаление карточки с запросом больше не отбраковывает запрос для повторной разметки")
    assert dele.index("ins_rejected") < dele.index("c.inserts.splice"), (
        "отбраковка пишется ПОСЛЕ удаления — повторная разметка не узнает про удалённое")
    assert "saveState()" in dele, "insDel не доходит до общей двери истории (saveState)"
    assert "renderInsHost()" in dele and "syncClipLists()" in dele, (
        "после удаления не перерисованы список карточек и теги клипов")


def test_step2_phase_buttons_and_checkboxes_fd(html, js):
    """Задание FD: на шаге «Разметка» фазы запускаются по отдельности и на выбранных клипах.

    Раньше была одна кнопка «Разметить всё» (все три фазы по всем клипам): одни субтитры
    у нескольких клипов или одни жёлтые запустить было нельзя. Теперь в шапке шага 2 три
    кнопки фаз со счётчиком selClips(), а в строках клипов — те же галочки pickbox, что
    на шаге сборки (одно поле c.sel на оба шага, второго не заводить).
    """
    for btn in ("markupPhase('subs')", "markupPhase('yellow')", "markupPhase('inserts')"):
        assert btn in html, f"нет кнопки фазы {btn}"
    assert html.count("data-markupcnt") == 3, "счётчик фаз не на всех трёх кнопках"
    # «Разметить всё» осталась на месте и запускает все три фазы
    assert 'id="markupall"' in html and "markupAll()" in html

    # строки шага 2 несут pickbox с тем же c.sel, что и сборка; onchange обновляет счётчик
    r2 = js[js.index("function renderClips2()"):js.index("function markupSelCount(")]
    assert 'class="pickbox"' in r2, "нет галочки выбора в строке шага 2"
    assert "CLIPS[" in r2 and ".sel=this.checked" in r2, "галочка пишет не в c.sel"
    assert "markupSelCount()" in r2, "счётчик фаз не обновляется при клике по галочке"
    assert "syncBuildBtn()" in r2, "галочка шага 2 не обновляет кнопку сборки шага 3"

    # счётчик: пусто у всех = все (та же семантика, что selClips)
    cnt = js[js.index("function markupSelCount()"):js.index("function renderClips3(")]
    assert "CLIPS.length" in cnt and "data-markupcnt" in cnt, "счётчик считает не selClips/все"


def test_step2_phase_buttons_call_selclips_and_single_phase_fd(js):
    """Кнопки фаз и «Разметить всё» идут через общий markupAllRun(subeng, list, phases).

    list = selClips() (пусто у всех = все), phases = запускаемые фазы. Прогресс делится
    на число ВЫБРАННЫХ фаз, а не жёстко на 3; зависимость «нет субтитров» не отключается.
    """
    body = _fn_body(js, "async function markupAll(")
    assert "selClips()" in body, "«Разметить всё» идёт не по selClips()"
    assert "['subs','yellow','inserts']" in body, "«Разметить всё» запускает не все три фазы"
    assert "markupAllRun(subeng,list,['subs','yellow','inserts'])" in body, (
        "«Разметить всё» не передаёт ask (должен молча пропускать готовое)")

    ph = _fn_body(js, "async function markupPhase(")
    assert "selClips()" in ph, "кнопка фазы идёт не по selClips()"
    assert "markupAllRun(subeng,list,[phase],true)" in ph, "кнопка фазы не передаёт ask=true"

    run = js[js.index("async function markupAllRun(subeng,list,phases"):js.index("// субтитры с нуля")]
    assert "const P=phases.length" in run, "прогресс не знает число выбранных фаз"
    assert "(no-1)/P+i/N/P" in run, "прогресс делится не на число фаз"
    assert "has(c)" in run and "dep(c)" in run, "нет разделения «уже сделано» и «зависимость»"
    assert "!force&&has(c)" in run, "пропуск по «уже есть» не отключается силой (зависимость остаётся)"
    # фазы выбираются по списку, а не безусловно
    assert "phases.includes('subs')" in run and "phases.includes('yellow')" in run and "phases.includes('inserts')" in run


def test_step2_rewrite_question_behavior_in_node_fd(js):
    """Поведение: при 3 фазах askConfirm не зовётся ни разу; при одной фазе — зовётся.

    Исполняем phase-логику боевой функцией markupAllRun в node с подделанными
    fetch/askConfirm. Сценарий: 2 клипа, у обоих всё размечено (subs>0, colored>0,
    inserts.length>0). «Разметить всё» (3 фазы, ask=false) — askConfirm 0 вызовов,
    все клипы пропущены; «Субтитры» (ask=true) — askConfirm ровно 1 вызов.
    """
    run = js[js.index("async function markupAllRun(subeng,list,phases"):js.index("// субтитры с нуля")]
    # вырезаем саму функцию markupAllRun по балансу скобок (в теле async-стрелки с {})
    m = re.search(r"async function markupAllRun\s*\(", run)
    assert m, "markupAllRun не нашлась"
    i = run.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(run)):
        if run[j] == "{":
            depth += 1
        elif run[j] == "}":
            depth -= 1
            if depth == 0:
                break
    fn = run[m.start():j + 1]
    assert "async function markupAllRun" in fn, "markupAllRun не вырезалась"

    # Счётчик askConfirm живёт ВНУТРИ строки-заглушки (node), в Python его дублировать
    # не нужно — иначе ruff F841 («assigned but never used»).
    stub = (
        "let UICANCEL=false,CLIPS=[],curIns=-1,calls={ask:0};"
        "const $=id=>({textContent:'',className:'',style:{},value:''});"
        "function uiBusyGuard(){return false;}function uiBusySet(){}function progShow(){}"
        "function progUpdate(){}function progQueue(){}function progStep(){}function progDone(){}"
        "function renderClips2(){}function saveState(){}"
        "function uiLog(){}function toast(){}function sleep(){return Promise.resolve();}"
        "function engLabel(){return 'whisper';}function askConfirm(){calls.ask++;return true;}"
        "function t(s){return s;}function errText(e){return String(e);}"
        "function subSkipped(){return '';}function aiPost(){return Promise.resolve({});}"
        "function aiStepConc(){return 1;}"
        # список клиентского прогона: markupAllRun заводит его сам, тело
        # функции в стенде не проверяется — заглушки достаточно, как у aiStepConc
        "function localQStart(){}function localQSet(){}function localQEnd(){}"
        "function qClipSum(){return '';}"
        "function insLog(){}function insAfterAI(){return Promise.resolve(null);}"
        "async function fetch(){return {json:async()=>({subs:10,colored:5,ncams:2})};}"
        + fn + ";"
    )
    # сценарий 1: 3 фазы, ask=false (по умолчанию) — вопрос не задаётся ни разу
    s1 = stub + (
        "CLIPS=[{xml:'a',name:'A',status:{subs:10,colored:5},inserts:[{x:1}]},"
        "{xml:'b',name:'B',status:{subs:10,colored:5},inserts:[{x:1}]}];"
        "calls.ask=0;"
        "markupAllRun('whisper',CLIPS,['subs','yellow','inserts']).then(()=>{"
        "const a=calls.ask;"
        "calls.ask=0;"
        "return markupAllRun('whisper',CLIPS,['subs'],true).then(()=>{"
        "console.log(JSON.stringify({threePhasesAsk:a,singlePhaseAsk:calls.ask}));});});"
    )
    out = subprocess.run(["node", "-e", s1], capture_output=True, text=True,
                         encoding="utf-8-sig", errors="replace", timeout=60)
    assert out.returncode == 0, out.stderr.strip()[:400]
    res = json.loads(out.stdout.strip())
    assert res == {"threePhasesAsk": 0, "singlePhaseAsk": 1}, (
        f"вопрос о перезаписи зовётся не так: {res} (3 фазы — 0, одна фаза — 1)")


def test_зеркало_состояния_успевает_между_тиками():
    """Задержка отправки зеркала (SRVST_DELAY) обязана быть меньше периода тика
    flushSave (setInterval). При SRVST_DELAY >= период каждый тик отодвигает
    таймер и серверное зеркало не пишется никогда (поймано 2026-09-08).
    Вторая проверка: SRVST_LAST присваивается ПОСЛЕ проверки ответа, а не до —
    иначе неудачный запрос считается доставленным и не повторяется."""
    boot = _read(os.path.join(ROOT, "static", "app", "99-boot.js"))
    m_delay = re.search(r"SRVST_DELAY\s*=\s*(\d+)", boot)
    m_interval = re.search(r"setInterval\(flushSave\s*,\s*(\d+)\)", boot)
    assert m_delay, "SRVST_DELAY не найден в 99-boot.js"
    assert m_interval, "setInterval(flushSave, N) не найден в 99-boot.js"
    delay = int(m_delay.group(1))
    interval = int(m_interval.group(1))
    assert delay < interval, (
        f"SRVST_DELAY ({delay}) >= период flushSave ({interval}) — "
        "каждый тик отодвигает таймер и зеркало не пишется никогда")
    # SRVST_LAST обязан присваиваться ПОСЛЕ проверки ответа (.then),
    # а не до — иначе провал считается доставленным и не повторяется
    post_fn = boot[boot.index("function srvStatePost("):boot.index("function applyState(")]
    pos_then = post_fn.index(".then(")
    pos_last = post_fn.index("SRVST_LAST=")
    assert pos_last > pos_then, (
        "SRVST_LAST присваивается ДО проверки ответа (.then) — "
        "провал сохранения считается доставленным и не повторяется")


def test_no_triple_backslash_quote_in_on_attributes():
    """В static/app/*.js нет последовательности \\\' внутри строк, собирающих on…="…"-атрибуты,
    а экранированные кавычки в oninput строки цвета живы (typeof hex2rgb===\\'function\\')."""
    app_dir = os.path.join(ROOT, "static", "app")
    for fname in sorted(os.listdir(app_dir)):
        if not fname.endswith(".js"):
            continue
        fpath = os.path.join(app_dir, fname)
        raw = open(fpath, "rb").read().decode("utf-8")
        for line_no, line in enumerate(raw.splitlines(), 1):
            if re.search(r'on[a-z]+="[^"]*\\{3}\'', line):
                pytest.fail(f"Найдена последовательность \\\\\\' внутри on-атрибута в {fname}:{line_no}:\n{line.strip()}")

    # Ищем по содержимому, а не по номеру строки: правка выше по файлу сдвигала 811-ю,
    # и сторож падал на посторонней строке (добавило 30+ строк в 60-preview.js).
    preview_lines = open(os.path.join(app_dir, "60-preview.js"), "rb").read().decode("utf-8").splitlines()
    assert any(r"typeof hex2rgb===\'function\'" in line for line in preview_lines), (
        "в 60-preview.js пропала строка с экранированными кавычками "
        "typeof hex2rgb===\\'function\\'")


def test_style_panel_layer_order_and_slider_contracts(css, js):
    """Панель стиля:
    - поле layer_order не порождает строки .strow с подписью;
    - у .layer-order-item в CSS нет padding: 8px 12px и нет цвета amber;
    - у .stslider ширина не фиксирована в px.
    """
    # 1. Поле layer_order не порождает строки .strow с подписью
    assert "strow_layer_order" not in js
    panel_js = _read(PANEL_JS)
    m = re.search(r"if\s*\(\s*item\.ctl\s*===\s*['\"]layer_order['\"]\s*\)\s*\{([^}]+)\}", panel_js)
    assert m, "ветка item.ctl === 'layer_order' отсутствует в 94-stylepanel.js"
    lo_body = m.group(1)
    assert "frag.appendChild(loBox)" in lo_body
    assert "continue" in lo_body
    assert "fRow" not in lo_body
    assert "strow" not in lo_body
    assert "stfield-lbl" not in lo_body

    # 2. У .layer-order-item в CSS нет padding: 8px 12px и нет цвета amber
    m_item = re.search(r"\.layer-order-item\s*\{([^}]+)\}", css)
    assert m_item, ".layer-order-item не найден в app.css"
    item_rules = m_item.group(1)
    assert "padding: 8px 12px" not in item_rules and "padding:8px 12px" not in item_rules, (
        "у .layer-order-item остался старый паддинг 8px 12px"
    )
    lo_css_matches = re.findall(r"(\.layer-order-[^{]+)\{([^}]+)\}", css)
    assert lo_css_matches, "правила .layer-order-* не найдены в CSS"
    for selector, rules in lo_css_matches:
        assert "amber" not in rules.lower(), f"в правиле {selector} найден цвет amber: {rules}"

    # 3. У .stslider ширина не фиксирована в px
    m_slider = re.search(r"\.stslider\s*\{([^}]+)\}", css)
    assert m_slider, ".stslider не найден в app.css"
    slider_rules = m_slider.group(1)
    assert not re.search(r"(?<![\w-])width:\s*\d+px", slider_rules), (
        f"у .stslider ширина зафиксирована в px: {slider_rules}"
    )
    assert "min-width:240px" in slider_rules.replace(" ", "") or "min-width: 240px" in slider_rules


# Группа "chrome": замер идёт в настоящем безголовом Chrome, а несколько браузеров
# разом под `pytest -n auto` меряют геометрию под нагрузкой и разъезжаются. Вместе со
# стендами геометрии окна вставок тест идёт в том же воркере (--dist loadgroup).
@pytest.mark.xdist_group("chrome")
def test_preview_modal_style_column_geometry_no_overlap(css, tmp_path):
    """Узкое окно 1000x640: колонка панели стиля не налезает на таймлайн (п. 9)."""
    # 1. CSS-правила: inscols и блоки стиля замкнуты по высоте и скроллу
    assert ".modal.aemode .inscols{flex:1;min-height:0;overflow:hidden}" in css.replace(" ", "") or (
        "overflow:hidden" in css and ".modal.aemode .inscols" in css
    ), "у .modal.aemode .inscols нет overflow:hidden"

    assert "#aewstyle #stylebox" in css, "нет правила #aewstyle #stylebox"
    assert "#aewstyle #stylecustom" in css, "нет правила #aewstyle #stylecustom"
    assert "#aewstyle #stpanel" in css, "нет правила #aewstyle #stpanel"

    # 2. Геометрия на 1000x640 под Chrome (если установлен)
    import pathlib
    import shutil
    chrome = shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome):
        return

    html_path = tmp_path / "geom_test.html"
    css_url = pathlib.Path(CSS).resolve().as_uri()
    rows = "\n".join(f"<div class='strow'>Row {i}</div>" for i in range(50))
    html_src = f"""<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'>
<link rel='stylesheet' href='{css_url}'>
<style>
body {{ margin: 0; padding: 0; width: 1000px; height: 640px; overflow: hidden; }}
</style>
</head>
<body>
<div class='backdrop' id='mbInserts' style='display:flex'>
  <div class='modal wide aemode' role='dialog'>
    <div class='mhead'><h2>Preview</h2></div>
    <div class='mbody'>
      <div class='inscols'>
        <div class='inspv'>
          <div class='pvstage' id='ipvstage'></div>
          <div class='row' style='align-items:center;margin-top:10px'>
            <button class='sm' style='min-width:44px'>Play</button>
            <input type='range' class='grow'>
            <span class='muted mono'>0:00 / 0:00</span>
          </div>
          <div class='row' style='margin-top:6px;align-items:center'>
            <label class='dbctl'>Music <input type='range'></label>
            <label class='dbctl'>Voice <input type='range'></label>
          </div>
          <div style='margin-top:8px'><span class='i'>!</span></div>
        </div>
        <div id='aewpanel' class='aewpanel' style='display:flex'>
          <div class='row' style='align-items:center;gap:10px'>
            <span class='seg' id='aewmode'><label class='on'>Слова</label><label>Стиль</label></span>
          </div>
          <div id='aewstyle' style='display:flex'>
            <div id='stylebox'>
              <div class='sect' style='margin-top:0'>Стиль</div>
              <div class='row' style='align-items:center'><select><option>default</option></select></div>
              <div id='stylecustom' style='margin-top:12px'>
                <div id='stpanel' class='stpanel' role='tree'>
{rows}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class='itlbar'><button>Zoom</button></div>
      <div id='itl' class='itl'><div id='itlin'>Timeline</div></div>
    </div>
  </div>
</div>
<script>
window.addEventListener('load', () => {{
  const styleBox = document.getElementById('stylebox').getBoundingClientRect();
  const aewStyle = document.getElementById('aewstyle').getBoundingClientRect();
  const stpanel = document.getElementById('stpanel').getBoundingClientRect();
  const itl = document.getElementById('itl').getBoundingClientRect();
  const res = {{
    aewStyle: aewStyle,
    styleBox: styleBox,
    stpanel: stpanel,
    itl: itl
  }};
  const d = document.createElement('pre');
  d.id = 'result';
  d.textContent = JSON.stringify(res);
  document.body.appendChild(d);
}});
</script>
</body>
</html>"""
    html_path.write_text(html_src, encoding="utf-8")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--window-size=1000,640",
        "--virtual-time-budget=2000",
        "--dump-dom",
        html_path.resolve().as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"Chrome failed: {proc.stderr}"
    m = re.search(r'<pre id="result">(.*?)</pre>', proc.stdout, re.DOTALL)
    assert m, f"нет результата измерений в выводе Chrome: {proc.stdout[:500]}"
    data = json.loads(m.group(1))

    style_bottom = max(data["aewStyle"]["bottom"], data["styleBox"]["bottom"], data["stpanel"]["bottom"])
    itl_top = data["itl"]["top"]

    # Прямоугольники колонки стиля и таймлайна не пересекаются
    assert style_bottom <= itl_top, (
        f"Колонка стиля наезжает на таймлайн: style_bottom={style_bottom} > itl_top={itl_top}"
    )



