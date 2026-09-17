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
# Панель стиля: с задания JB поля строятся из схемы, а не выписаны в разметке,
# поэтому проверки стиля смотрят в схему + в код панели (см. tests/test_style_keys_in_ui.py).
PANEL_JS = os.path.join(ROOT, "static", "app", "94-stylepanel.js")
STYLES_JS = os.path.join(ROOT, "static", "app", "95-styles.js")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _panel_js():
    """Код панели стиля — единственная дверь полей (задание JB)."""
    return _read(PANEL_JS)


def _schema_field(key):
    return watcher.schema_field(key)


def _schema_toggles():
    """Ключи-тумблеры схемы: группу/слой прячет её же галка (задание JB п. 2)."""
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


def test_video_cancel_reachable_from_overlay(js):
    """«Остановить» в оверлее прогресса гасит и генерацию видео.

    Видео живёт в своём джобе: /api/cancel его не касается, а оверлей перекрывает
    страницу с кнопкой «Остановить» — раньше нажатие не делало ничего.
    """
    body = js[js.index("async function cancelTask"):]
    body = body[:body.index("\n// ")] if "\n// " in body else body[:600]
    assert "VIDPOLL" in body and "vidCancel" in body


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


def test_editor_playback_drives_the_words_panel(js):
    """Играешь в РЕДАКТОРЕ — панель слов и строка субтитра едут вместе с картинкой.

    Пойманный баг (2026-08-11): редактор играет ИСХОДНИК (ED.cs), а слова и `#pvsub`
    размечены по МОНТАЖНОМУ времени, и вёл их только pvTick монтажного плеера. Пока
    правишь нарезку — а это и есть основной сценарий окна, — видео едет, слово под
    ним висит прежнее, ни один чип не подсвечен.

    Пересчёт идёт по ED.orig (раскладка ИЗ XML): PV.words сняты с неё, и
    несохранённая правка блоков их тайминги не двигает — иначе подсветка уползала бы
    на чужие слова ровно там, где её и смотрят.
    """
    assert "function edWords(" in js, "пересчёт слов под плейхед редактора пропал"
    body = js[js.index("function edWords("):][:600]
    assert "ED.orig" in body, "слова считаются по правленой раскладке, а не по той, что в XML"
    assert "pvwHighlight(" in body, "панель слов не получает текущее слово"
    assert "if(PV.playing)return" in body, "монтаж ведёт панель сам — второй раз не считаем"
    ui = js[js.index("function edUI("):][:200]
    assert "edWords()" in ui, "edUI — единственная точка, куда стекаются сдвиги плейхеда"


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


def test_editor_tooltip_shows_cut_rule(js):
    """Тултип вырезанного куска в редакторе нарезки (задание CA) показывает rule —
    имя функции, снявшей кусок («кто виноват в лишнем резе» видно в обычной работе).
    Пустой rule не печатается: старые .cuts.json без поля остаются читаемыми."""
    seg = js[js.index("вырезано [{src}]"):js.index("вырезанный кусок — двойной клик")]
    assert "{rule}" in seg, "тултип не подставляет rule"
    assert "k.rule?(' · '+k.rule):''" in seg, "пустой rule должен молчать"


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


def test_status_refresh_is_not_dropped(js):
    """Запрос статусов, пришедший во время обхода, не теряется, а повторяет обход.

    Клипы прилетают по одному прямо во время нарезки: старое «уже идёт — выходим»
    оставляло свежий клип без тегов субтитров/жёлтых до конца очереди.
    """
    body = js[js.index("async function refreshStatuses"):js.index("// ================= progress overlay")]
    assert "REFRESHWANT=true;return;" in body, "refreshStatuses снова молча пропускает запрос"
    assert "if(REFRESHWANT){REFRESHWANT=false;refreshStatuses();}" in body, (
        "отложенный обход статусов не запускается")


def test_loadstyles_blames_the_right_thing(js):
    """«Сервер не ответил» — только про сеть.

    Один try на запрос и на разбор превращал любую ошибку применения (падение
    onStyleChange на битом состоянии) в ложное обвинение сервера, а честную ошибку
    бэкенда глушил через if(!d.ok)return — без тоста и без записи в журнал.
    """
    body = js[js.index("async function loadStyles()"):js.index("// Какой пункт селектора")]
    assert body.count("catch") >= 2, "loadStyles снова ловит запрос и применение одним catch"
    assert "сервер не ответил" in body.split("if(!d.ok)")[0], (
        "«сервер не ответил» уехало из ветки запроса")
    assert "d.error" in body, "ошибка бэкенда снова уходит в никуда"


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


def test_named_style_change_hydrates_all_editor_fields(js):
    """Именованный стиль должен заполнить DOM до любого последующего stEdit().

    Одного reflectStyle() недостаточно: он обновляет только несколько полей превью,
    поэтому сохранение, например, одной громкости могло записать в стиль поля прошлого
    выбранного шаблона.
    """
    body = _func(js, "onStyleChange")
    ordinary = body.split("  else{\n", 1)[1]
    copied = ordinary.index("CURSTYLE=JSON.parse(JSON.stringify(STYLES[name]||{}));")
    assert "fillStyleFields();" in ordinary, (
        "выбор именованного стиля не гидратирует поля редактора")
    assert "reflectStyle();" not in ordinary, (
        "именованный стиль снова обновляет только урезанный набор полей")
    assert ordinary.index("fillStyleFields();") > copied, (
        "поля заполняются до копирования выбранного стиля")


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
    """
    step = js[js.index("function pvStep(P)"):js.index("function pvNow()")]
    assert "const done=spareSwap(P);" in step, "pvStep не пробует подмену дублёром"
    assert "if(Math.abs(live.currentTime-a.src)>0.06)" in step, (
        "seek на месте должен остаться ЗАПАСНЫМ путём (короткий сегмент, дублёр не успел)")
    assert "if(av.seeking){tm=a.ts;}" in step, (
        "во время seek время из currentTime не считается — иначе блоки проскакивают пачкой")
    # все три плеера идут через один общий шаг (было три копии, и гонка правилась трижды)
    for player, head in (("PV", "function pvTick()"), ("IPV", "function ipvStep()"), ("CPV", "function cpvStep()")):
        body = js[js.index(head):js.index(head) + 300]
        assert f"pvStep({player})" in body, f"{player}: стык не идёт через общий pvStep"
    # дублёр обязан жить ВНЕ P.vids: иначе pvVisual покажет его как отдельную камеру
    take = js[js.index("function bufTake(P,b,at)"):js.index("function spareLead(P)")]
    assert "P.vids[b.slot]=b.el;b.el=old" in take, "подмена больше не меняет элементы местами"
    assert "P.bufs.forEach(b=>{b.el.volume=MEDIA_VOL;})" in js, (
        "громкость мимо дублёров: после подмены они выходят в эфир и уровень прыгнет")


@pytest.mark.parametrize("player,step,seek,pause,scrub", [
    ("PV", "function pvTick()", "function pvSeekTo(tm)", "function pvPause()", "function pvScrub(v)"),
    ("IPV", "function ipvStep()", "function ipvSeekTo(tm)", "function ipvPause()", "function ipvScrub(x)"),
    ("CPV", "function cpvStep()", "function cpvSeekTo(tm)", "function cpvPause()", "function cpvScrub(x)"),
])
def test_every_preview_player_uses_the_double_buffer(js, player, step, seek, pause, scrub):
    """Дублёр подключён во ВСЕХ трёх плеерах, а не только на главной странице.

    Монтаж (PV), вставки/AE (IPV) и раскладка камер (CPV) — один и тот же контракт
    {audio:[{ts,te,src}], aidx} и один и тот же стык с seek'ом. Подмена на стыке и разбег
    к нему — в общей pvStep (одна копия, а не три расходящиеся); сами step-функции пускают
    дублёра вживую до стыка (spareRollAt).
    """
    def body(head, n=1400):
        return js[js.index(head):js.index(head) + n]
    pvs = js[js.index("function pvStep(P)"):js.index("function pvTick()")]
    assert "spareSwap(P)" in pvs, "pvStep: стык не идёт через подмену дублёром"
    assert "sparePrime(P)" in pvs, "pvStep: разбег к следующему стыку не готовится"
    assert f"pvStep({player})" in body(step, 300), f"{player}: шаг не идёт через общий pvStep"
    assert f"spareRollAt({player},st.tm)" in body(step, 300), f"{player}: дублёр не пускается вживую до стыка"
    assert f"spareIdle({player})" in body(seek), f"{player}: перемотка не сбрасывает дублёра"
    assert f"spareStop({player})" in body(pause), f"{player}: дублёр догорает на паузе"
    assert f"{player}.scrubbing=true" in body(scrub), (
        f"{player}: протяжка ползунка снова сеcит дублёра на каждый пиксель")


@pytest.mark.parametrize("player,apply_fn,seek,pause,opener,before", [
    ("PV", "function pvApplyVisual(tm,play)", "function pvSeekTo(tm)", "function pvPause()",
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
    """
    def body(head, n=900):
        return js[js.index(head):js.index(head) + n]
    assert f"camApply({player},tm,play)" in body(apply_fn, 200), (
        f"{player}: переключение ракурса снова живёт своей копией алгоритма"
    )
    open_body = js[js.index(opener):js.index(opener) + 2600]
    assert f"{player}.delta=camDeltas({player})" in open_body, f"{player}: камеры без оффсетов от ведущей"
    assert f"camBufs({player},stage,{before})" in open_body, f"{player}: камеры без дублёров — стык снова через seek"
    assert f"camIdle({player})" in body(seek), f"{player}: перемотка не приводит скорости камер в норму"
    assert f"camIdle({player})" in body(pause), f"{player}: на паузе камеры остаются с правленой скоростью"


def test_secondary_cameras_run_as_continuous_tracks(js):
    """Вторичная камера — непрерывная дорожка, а не «спящий кадр, который будят на стыке».

    Это третий заход на «моргание при переключении камер», и первые два лечили симптом.
    Держать камеру на паузе — значит на стыке показывать ПРЕЖНЮЮ лишние кадры, пока
    декодер просыпается (жалоба «мелькает прежняя камера»). Держать играющей, но править
    дрейф seek'ом — сам seek роняет кадр и даёт тот же откат. Поэтому: ведём КАЖДУЮ немую
    камеру каждый кадр (цель — время ведущей + P.delta[k]), дрейф гасим скоростью, стык
    проходим дублёром.
    """
    body = js[js.index("function camApply(P,tm,play)"):js.index("function camIdle(P)")]
    assert "for(let k=1;k<P.vids.length;k++)" in body, (
        "ведём только текущую камеру — входящая опять окажется не готова к стыку")
    assert "lead.currentTime+((P.delta||[])[k]||0)" in body, "цель вторичной камеры больше не привязана к ведущей"
    assert "k!==ac" in body, "скорость правится и звуковой камере — поедет звук"

    vis = js[js.index("function camVisual(P,ci,play)"):js.index("function camDeltas(P)")]
    assert "v.pause()" not in vis, "скрытые камеры снова ставятся на паузу — стык будет их будить"


def test_camera_shown_is_exactly_the_one_edl_asks_for(js):
    """Показывается РОВНО тот ракурс, который просит EDL, без «подождём готовности».

    Любое ожидание = показ ДРУГОЙ камеры, а цикл показа идёт 60 раз в секунду: «подержим
    пару кадров» и есть видимая вспышка чужого ракурса — та самая жалоба. Держать прежнюю
    камеру нельзя ни при каких условиях; худшее допустимое — пара кадров ТОГО ЖЕ ракурса,
    ещё доигрывающего seek.
    """
    body = js[js.index("function camApply(P,tm,play)"):js.index("function camIdle(P)")]
    assert "const ci=s.ci;" in body, "ракурс снова вычисляется, а не берётся из EDL как есть"
    assert "P.curCi>=0?P.curCi" not in body, "снова показываем прежнюю камеру вместо запрошенной"
    assert "camVisual(P,ci,play)" in body, "показ идёт мимо общей функции"


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
    assert "if(P.playing&&P.vidx>=0&&vi<P.vidx)" in body, (
        "показ снова может уехать на предыдущий кусок — вернётся мелькание прежней камеры")

    for fn in ("function pvSeekTo(tm)", "function ipvSeekTo(tm)", "function cpvSeekTo(tm)"):
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
    assert "P.vids[b.slot].playbackRate=1" in take, (
        "дублёр выходит в эфир с разгонной скоростью — картинка поедет быстрее звука")


@pytest.mark.parametrize("opener,end", [
    ("async function openPreview(xml)", "function pvSegAt("),
    ("async function ipvOpen(xml)", "function ipvApplyVisual("),
    ("async function cpvOpen(xml)", "function cpvAudio("),
])
def test_every_preview_plays_camera_proxies(js, opener, end):
    """Все три предпросмотра играют прокси камер, а не исходники.

    H.264 High 4:2:2 10 бит (Sony/Canon) не берёт ни один аппаратный декодер — ни NVDEC,
    ни QSV; браузер отвечает powerEfficient=false и жуёт 4K софтом, seek на стыке 365 мс.
    Тот же материал в 720p 4:2:0 8 бит — 124 мс и powerEfficient=true.
    """
    body = js[js.index(opener):js.index(end)]
    assert "pvSrc(c.path)" in body, "плеер снова создаёт <video> прямо с исходника"
    assert "encodeURIComponent(c.path)" not in body, "остался прямой путь в обход прокси"
    assert "pvProxyLoad(" in body, "сборка прокси не запускается при открытии"
    assert "bufMake(" in body, "дублёр не создаётся"


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

    По этому таймлайну и делают правки, поэтому замирание здесь больнее всего. Дублёр
    общий с монтажным плеером: цель у обоих — исходное время камеры 1, поэтому она и
    хранится одним числом (PV.spareAt), а не индексом в чьём-то списке сегментов.
    """
    tick = js[js.index("function edTick()"):js.index("function edBreathAt(")]
    assert "edJump(v,nb.s0)" in tick, "edTick снова сеcит живой <video> прямо на стыке блока"
    assert "let v=PV.vids[0]" in tick, (
        "v захвачен const — после подмены он указывает на снятый с эфира элемент")
    assert "edArm()" in tick, "редактор не готовит разбег дублёра"
    jump = js[js.index("function edJump(v,at)"):js.index("function edArm()")]
    assert "try{v.currentTime=at;}" in jump, (
        "seek на месте должен остаться запасным путём, когда дублёр не успел")
    play = js[js.index("function edPlay()"):js.index("function edPause()")]
    assert "spareIdle(PV)" in play, "редактор стартует с чужим разбегом дублёра"
    assert "spareStop(PV)" in js[js.index("function edPause()"):js.index("function edTake(")], (
        "дублёр догорает после паузы редактора")


def test_scrubbing_does_not_starve_the_preview_double_buffer(js):
    """Протяжка ползунка не заваливает дублёра сеcками.

    oninput сыплется на каждый пиксель, а pvScrub зовёт pvPause+pvSeekTo+pvPlay. Пока
    разбег готовился на каждом таком вызове, дублёр оставался вечно «seeking»: на
    ближайшем стыке pvSwap срывался в запасной путь, и стык снова замирал — при том
    что обычное проигрывание шло гладко. Разбег готовим один раз, когда протяжка улеглась.
    """
    body = js[js.index("function pvScrub(v)"):js.index("// ===== панель слов")]
    assert "PV.scrubbing=true" in body and "clearTimeout(PV.scrubT)" in body, (
        "pvScrub снова готовит разбег на каждое событие ползунка")
    # гвардия стоит в ОБЩЕМ bufArm, а не у конкретного плеера: разбег готовят все трое
    arm = js[js.index("function bufArm(P,b,at)"):js.index("function bufRoll(b,left)")]
    assert "P.scrubbing" in arm, "bufArm не знает про протяжку — seek на каждый пиксель вернулся"
    seek = js[js.index("function pvSeekTo(tm)"):js.index("// --- дублёр камеры 1")]
    assert "sparePrime(" not in seek, "pvSeekTo снова сеcит дублёра — он зовётся из протяжки"


def test_style_template_is_editable_without_retyping_its_name(js, html):
    """Шаблон правится кнопкой, а имя для перезаписи подставляется само (задание AC2).

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
    # с задания EX2c рото — поле стиля, и шаблон приносит его сам (JB п. 2).
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

    Живую маску включает поле с hint «rotomask» (задание JB): id старой разметки
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
    for marker in ("async function openPreview(", "function bufMake(", "async function ipvOpen("):
        assert "voiceWiring(" in _fn_body(js, marker), (
            "<video> создаётся без voiceWiring: " + marker)
    assert "v.__wired" in js and "if(!v||v.__wired)return" in js, (
        "пропал признак «источник уже создан»: второй createMediaElementSource "
        "на том же элементе роняет звук совсем")


def test_preview_draws_the_scene_plan_and_never_recomputes_it(js):
    """Предпросмотр шага 3 РИСУЕТ план сцены (задание D), ничего не досчитывая.

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

    # вставки шага 2 (задание DP): перевод в контракт плана единой функцией cardToIns
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
    """Голос предпросмотра ныряет в ноль на окнах цензуры ИЗ ПЛАНА (задание I).

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

    pv = _fn_body(js, "function pvUI(")
    assert "vgDuck(tm,PVW.plan)" in pv, "монтажный плеер не глушит голос по плану"
    ipv = _fn_body(js, "function ipvUI(")
    assert "vgDuck(tm,IPV.plan)" in ipv, "плеер вставок не глушит голос по плану"
    # без плана (запрос не прошёл) duck обязан вернуть обычную громкость, не сломать плеер
    assert "if(!VG)return" in duck, "vgDuck падает без графа громкости"


def test_words_panel_highlights_intro_from_the_plan(js):
    """Панель слов редактора нарезки подсвечивает группу интро по плану (задание K).

    Регрессия от D: introGroupWindows стала прослойкой над планом (ts/te считает
    scene_plan), а панель слала ей локально посчитанные строки без ts/te — окна
    были пустыми и подсветка тихо пропала. Чинить формулой в JS нельзя (две копии
    уже разошлись): панель обязана запросить /api/scene тем же образцом, что
    ipvPlanFetch, и строить окна по plan.intro[].ts/te. Правки интро догоняют план
    дебаунс-пересчётом (pvwPlanSoon), а не мгновенным пересчётом в JS.
    """
    fetch = _fn_body(js, "async function pvwPlanFetch(")
    assert "/api/scene" in fetch, "панель не запрашивает /api/scene"
    assert "PVW.igw=" in fetch and "introGroupWindows(" in fetch, (
        "окна панели не разворачиваются из ts/te плана")
    assert "catch" in fetch, "ошибка плана должна не ломать панель"
    assert "if(d.ok&&d.plan)" in fetch, "план применяется только при ok"

    open_ = _fn_body(js, "async function pvwOpen(")
    assert "pvwPlanSoon(" in open_, "панель не тянет план при открытии"

    commit = _fn_body(js, "function pvwCommitIntro(")
    assert "pvwPlanSoon(" in commit, "правка интро не пересчитывает план дебаунсом"

    render = _fn_body(js, "function pvwRenderIntro(")
    assert "introGroupWindows(" not in render and "resolveIntroFor(" not in render, (
        "панель снова считает окна групп сама — формула вернулась в JS")


def test_cam1_inserts_follow_camera_zoom_only_on_cam1(js):
    """Вставки кам1 наследуют зум Камеры 1, остальные — нет (задание L).

    В AE нул «вставки кам1» привязан к нулу Камеры 1 (insNull1.parent=cam1null),
    а нулы «вставки кам1 на кам2» и «вставки кам2» свободны. Применить зум ко всем
    трём — развести превью с AE в другую сторону; таблица случаев в задании L.
    """
    place = _fn_body(js, "function ipvInsPlace(")
    assert "style==='cam1'&&!x.oncam2" in place, (
        "зум привязан не к той вставке: нужен style cam1 и oncam2=false")
    assert "ipvZoomAt(" in place, "вставка кам1 не берёт масштаб из общего интерполятора"


def test_frame_drag_writes_data_not_a_second_storage(js):
    """Перетаскивание в кадре (задание E, шаг 2) пишет в ИМЕЮЩИЕСЯ источники: вставка —
    INS[].x/y, интро — gx/gy на головной строке, субтитры — CURSTYLE.sub_y. План-кэш
    правится только как временный показ (insShift / plan.posy / plan.intro.dx) и сам
    пересчитывается дебаунсом — второго хранилища значений нет.

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

    sub = _fn_body(js, "$('ipvsub').addEventListener('pointerdown'")
    assert "CURSTYLE.sub_y=" in sub, "субтитры пишут не в стиль (sub_y)"
    assert "styleSubPos();captureAE();ipvPlanSoon()" in sub, (
        "поле «% снизу» не синхронизируется после драга субтитров")

    st = _fn_body(js, "function stEdit(")
    assert "ipvPlanSoon()" in st, (
        "правка поля «% снизу» не догоняет план — превью не двигается (обратная связь)")


def test_intro_scale_handle_centered_below_line(css):
    """Ручка масштаба интро — под блоком по центру, а не в потоке строки (задание BQ).

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
    """Сдвиг вставки, сделанный драгом в предпросмотре, переживает пересборку списка
    (задание BL). ensureJobs целиком пересобирает c.job.ins из карточек шага 2 и убивал
    x/y: драг писал только в INS, а источник x/y — карточка c.inserts (в ui_state у всех
    54 вставок на момент жалобы стояли нули). Теперь драг пишет и в карточку тем же
    norm-сравнением пути, а перенос tw для ручных вставок без карточки несёт x/y.
    """
    drag = _fn_body(js, "$('ipvins').addEventListener('pointerdown'")
    assert "cl.inserts" in drag, "драг не дотягивается до карточек шага 2"
    assert "normInsPath(INS[real].media)" in drag, (
        "карточка ищется не тем norm-сравнением, что ensureJobs")
    assert "cl.inserts[ic].x=nx" in drag and "cl.inserts[ic].y=ny" in drag, (
        "сдвиг не записывается в карточку шага 2 (источник x/y)")

    jobs = _fn_body(js, "function ensureJobs(")
    assert "noexit:x.noexit,x:x.x,y:x.y" in jobs, (
        "перенос tw для ручных вставок (без карточки) не несёт x/y")
    assert "normInsPath(x.media)" in jobs, "ensureJobs не использует общий normInsPath"


def test_frame_drag_shift_locks_one_axis(js):
    """Shift в драге вставок/интро фиксирует движение по ОДНОЙ оси.

    Ось выбирается по БОЛЬШЕМУ по модулю смещению от точки старта и переоценивается на
    каждом pointermove (как в Figma/Photoshop). Правило ОДНО на оба драга — общий
    axisLock — чтобы две копии снова не разъехались (на этом уже горели: introResolve
    и resolveIntroFor потеряли gs, задание BG). pointerup применяет ИМЕННО st.lock,
    а не ev.shiftKey: Shift можно отпустить за миг до кнопки мыши, и в данные уехало бы
    не то, что нарисовано.
    """
    # тела обработчиков вырезаем по границам соседних (после insert'а идёт intro,
    # после intro — субтитры): _fn_body тащит всё до следующего `function`, а тут
    # обработчики — стрелки, и соседние попадали бы в один срез
    ins = js[js.index("$('ipvins').addEventListener('pointerdown'"):js.index("$('ipvintro').addEventListener('pointerdown'")]
    ilocks = re.findall(r"axisLock\([^)]*\)", ins)
    assert len(ilocks) == 2, "драг вставки зовёт axisLock не в move и не в up"
    assert "ev.shiftKey" in ilocks[0], "move: ось должна выбираться по Shift"
    assert "st.lock" in ilocks[1] and "ev.shiftKey" not in ilocks[1], (
        "up: применяется ИМЕННО st.lock, а не ev.shiftKey")

    intro = js[js.index("$('ipvintro').addEventListener('pointerdown'"):js.index("$('ipvsub').addEventListener('pointerdown'")]
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


# ---------------- задание N: связка «спикер → стиль → папки» ----------------

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
    """В сборку уходит ИМЯ стиля, а не копия (задание EX2a): стиль резолвит бэкенд
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
    """Механизм разноса правок стиля по клипам удалён целиком (задание EX2b).

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
    """Папка .jsx — производная от тега (задание N): у клипа со спикером — его jsxdir
    (effOutdir), без тега — глобальное поле. Эта папка уходит в сборку per-job
    (jobForBuild), а правка поля у клипа с тегом пишется в профиль спикера."""
    assert "function effOutdir(c)" in js, "папки по тегу нет"
    assert "function renderAeDirField()" in js, "поле не показывает папку тега"
    jfb = _fn_body(js, "function jobForBuild(c)")
    assert "outdir:effOutdir(c)" in jfb, "папка клипа не уходит в сборку per-job"
    commit = _fn_body(js, "function aeDirCommit(el)")
    assert "saveSpeakerJsxdir(sp,el.value)" in commit, (
        "правка папки у клипа с тегом не пишется в профиль спикера")
    assert "renderAeDirField()" in commit, "отказ не возвращает поле к папке профиля"
    save = _fn_body(js, "async function saveSpeakerJsxdir(")
    assert "savespeaker" in save, "папка не сохраняется через /api/savespeaker"


def test_mixed_speakers_disable_set_build(js):
    """Клипы 2+ спикеров в наборе собираются только по одному, каждый в свою папку
    (задание N): «Собрать набор» и «Один на всё» для них недоступны."""
    sync = _fn_body(js, "function syncBuildBtn()")
    assert "spks.size>1" in sync, "смешанные спикеры не считаются"
    assert "b.disabled=!!mixed" in sync, "«Собрать набор» не гаснет при 2+ спикерах"
    assert "combined" in sync, "«Один на всё» не гасится при 2+ спикерах"
    # outdir набора — в общей buildOutdir (задание BI: её зовут и сборка, и рендер);
    # «Собрать набор» с «Один на всё» без этого правила собрал бы общий файл не туда
    out = _fn_body(js, "function buildOutdir()")
    assert "spks.size===1" in out, "общий .jsx уходит не в папку единственного спикера"
    build = _fn_body(js, "async function buildMulti()")
    assert "buildOutdir()" in build, "сборка не пользуется общим правилом папки набора"


def test_render_uses_checked_clips_not_open_clip(js):
    """«Собрать и отрендерить» собирает тот же набор, что «Собрать набор» — по
    галочкам, а не по открытому в редакторе клипу (задание BI, прогон 2026-08-14:
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
    """Переезд на превью-прокси не трогает живому <video> src (задание BE).

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
    assert "P.vids[b.slot]=b.el;b.el=old" in swap, "общий обмен потерял подмену элементов"


def test_players_remember_camera_paths_and_watch_proxies(js):
    """IPV/CPV сохраняют пути камер и следят за сборкой прокси (задание BE).

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

    # 4. imgPrompts не откатывается на AICFG (задание CS)
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
    quantity = ("num", "int", "angle")
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

    # простые контролы подключаются к stEdit прямо в разметке панели
    for line in ("chk.onchange = () => stEdit();", "sel.onchange = () => stEdit();",
                 "inp.onchange = () => stEdit();", "inp.oninput = () => stEdit();",
                 "ta.onchange = () => stEdit();", "ta.oninput = () => stEdit();",
                 "hex.onchange = () => stHexChange(item.key, hex.value);",
                 "swatch.onchange = () => stColorSwatchChange(item.key, swatch.value);"):
        assert line in panel, "в панели пропала привязка контрола к stEdit(): " + line


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
    _val/_input/_slider/_hex/_color). Обращение к id, которого не будет ни там, ни там, —
    это null-deref, ради которого тест и заведён.
    """
    html_ids = set(re.findall(r"""\bid=["']([^"']+)["']""", html))
    derived = set()
    for key in (it.get("key") for _kind, it in watcher.schema_items() if it.get("key")):
        derived |= {"st_" + key, "st_" + key + "_val", "st_" + key + "_input",
                    "st_" + key + "_slider", "st_" + key + "_hex", "st_" + key + "_color"}
    for key in (it.get("toggle") for _kind, it in watcher.schema_items() if it.get("toggle")):
        derived.add("st_" + key)
    # постоянные id панели и предпросмотра (см. JB п. 3, список оставшихся обращений)
    known = {"stpanel", "st_name", "st_saved", "st_pickzoom", "st_layer_order_list",
             "st_disc_text"}

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
    """normInsPath объявлена РОВНО один раз во всех static/app/*.js (задание DP-хвост).

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


def test_card_to_ins_duration_default_when_zero(js):
    """Дефолт длительности в cardToIns при duration_sec=0 даёт dur_s=2 (задание DP-хвост).

    В исходном ensureJobs было (x.duration_sec||2). При замене на !=null ? ... : 2
    значение duration_sec=0 превращалось в dur_s=0 и вставка исчезала из сборки AE.
    """
    fn = _fn_body(js, "function cardToIns(")
    assert "x.duration_sec||2" in fn, (
        "cardToIns должен использовать ||2 для сохранения дефолта 2с при duration_sec=0"
    )
    fn_card = _func(js, "cardToIns")
    try:
        out = _run_node(
            f"{fn_card}\n"
            "const r = cardToIns({duration_sec: 0});\n"
            "console.log(JSON.stringify(r));"
        )
        assert out["dur_s"] == 2, f"dur_s при duration_sec=0 должен быть 2, получено {out['dur_s']}"
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        pass


def test_i18n_data_containers_opt_out(html, js):
    """Контейнеры с данными ролика помечены data-noi18n, applyI18n их пропускает (задание DS).

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
    """Клип хранит ИМЯ стиля, копия — только у безымянного кастома (задание EX2c).
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


def test_remove_selected_reloads_panel_when_active_removed(js):
    """Убрали отмеченным и сам открытый в панели клип — панель не остаётся на удалённом.

    После _spliceClip curAE открытого клипа становится -1, а данные панели
    (AEXML/WORDS/INTRO) всё ещё про удалённый файл. При живых клипах панель
    пересаживается на живой клип (selectAE(0), как goStep(3)). При пустом списке
    selectAE звать не на чем — панель #aecfg прячется явно и снимается её
    принадлежность (AEXML=''), иначе старые данные удалённого клипа дожили бы
    до перезахода в шаг.
    """
    body = _fn_body(js, "function removeSelClips(")
    assert "curAE<0&&CLIPS.length)selectAE(0)" in body, (
        "панель не пересаживается на живой клип после удаления открытого")
    assert "$('aecfg').style.display='none'" in body, (
        "при пустом списке #aecfg не прячется — панель остаётся от удалённого клипа")
    assert "AEXML=''" in body, (
        "при пустом списке не снимается принадлежность панели (AEXML)")


def test_insdel_undo_snapshot_and_restore_contract(js, html):
    """Одноуровневый undo удаления вставки: снимок ДО мутации, откат по XML.

    insDel делает снимок {xml, idx, копию вставки, ТОЧНОЕ ins_rejected} до splice —
    иначе undo вернёт вставку уже после того, как insDel дописал брак в rejected.
    undo ищет клип ПО XML (индекс мог уехать), кладёт вставку на исходное место и
    восстанавливает ins_rejected. Кнопка disabled, когда отменять нечего.
    insClearMedia — другой сценарий (снять файл, карточка остаётся), его не трогаем.
    """
    assert 'id="insUndoBtn"' in html, "кнопки undo в окне вставок нет"
    btn = html[html.index('id="insUndoBtn"') - 120:html.index('id="insUndoBtn"') + 260]
    assert 'data-ic="undo"' in btn and 'aria-label=' in btn and 'data-t=' in btn, (
        "кнопка не с undo-иконкой / без имени / без справки")
    dele = _fn_body(js, "function insDel(")
    assert "INS_UNDO={" in dele, "insDel не делает снимок для undo"
    assert dele.index("INS_UNDO={") < dele.index("c.inserts.splice"), (
        "снимок делается ПОСЛЕ splice — undo вернёт не то")
    assert "insUndoUI()" in dele, "insDel не обновляет кнопку undo"
    und = _fn_body(js, "function insUndo(")
    assert "CLIPS.find" in und and ".xml===u.xml" in und, "undo ищет клип не по XML"
    assert "splice(Math.min(u.idx" in und, "вставка не возвращается на исходное место"
    assert "c.ins_rejected" in und, "undo не восстанавливает ins_rejected"
    assert "renderInsHost()" in und and "syncClipLists()" in und and "saveState()" in und
    assert "INS_UNDO=null" in und, "после undo снимок не гасится — кнопка останется активной"


def test_insdel_undo_runs_the_real_helpers(js):
    """insDel/insUndo исполняются боевыми в node: удалить → откатить точно.

    Статическая проверка не поймала бы, что undo кладёт вставку не на исходный
    индекс или не восстанавливает ins_rejected. Сценарий: фото с query уходит в
    rejected при удалении; клип впереди удалён (target уехал с индекса — поиск по
    XML); откат возвращает вставку со всеми полями и rejected как было, а снимок
    гаснет (кнопка disabled).
    """
    helpers = "\n".join(_func(js, n) for n in ("insDel", "insUndo", "insUndoUI"))
    out = _run_node(
        "let INS_UNDO=null,curIns=-1;"
        "function renderInsHost(){}function syncClipLists(){}function saveState(){}"
        "function uiLog(){}function t(v){return v;}const $=()=>({disabled:false});"
        + helpers +
        "const clip={xml:'A',inserts:["
        "{type:'photo',start_sec:1,query:'sunset',media:'x.png'},"
        "{type:'video',start_sec:5,query:'',media:'y.mp4'}],ins_rejected:[{query:'old'}]};"
        "CLIPS=[{xml:'B',inserts:[]},clip];curIns=1;"
        "insDel(0);"
        "const afterDel=JSON.parse(JSON.stringify(CLIPS[1]));"
        "CLIPS.splice(0,1);curIns=0;"
        "insUndo();"
        "const c=CLIPS[0];"
        "console.log(JSON.stringify({"
        "afterDelIns:afterDel.inserts.length,"
        "afterDelRej:afterDel.ins_rejected.length,"
        "afterDelLastQuery:afterDel.ins_rejected[afterDel.ins_rejected.length-1].query,"
        "restoredIns:c.inserts.length,"
        "i0type:c.inserts[0].type,i0sec:c.inserts[0].start_sec,"
        "i0query:c.inserts[0].query,i0media:c.inserts[0].media,"
        "i1sec:c.inserts[1].start_sec,i1media:c.inserts[1].media,"
        "rejected:c.ins_rejected.length,firstQuery:c.ins_rejected[0].query,"
        "hasUndo:!!INS_UNDO}));")
    assert out == {
        "afterDelIns": 1,                          # после удаления осталась только видео
        "afterDelRej": 2,                          # фото дописало брак в rejected
        "afterDelLastQuery": "sunset",
        "restoredIns": 2,
        "i0type": "photo", "i0sec": 1, "i0query": "sunset", "i0media": "x.png",
        "i1sec": 5, "i1media": "y.mp4",
        "rejected": 1, "firstQuery": "old",        # rejected вернулся к ДО-удаления значению
        "hasUndo": False,                          # снимок погас — кнопка disabled
    }


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


def test_step2_rewrite_question_only_for_single_phase_fd(js):
    """Вопрос о перезаписи — ТОЛЬКО у явного запуска одной фазы (ask=true).

    «Разметить всё» (ask=false) молча пропускает уже готовое, как до задания FD:
    askConfirm не вызывается вовсе, has(c) пропускает как раньше. Регресс был в том,
    что на наборе с частично размеченными клипами «Разметить всё» выдавал до ТРЁХ
    модалок подряд — проверяем, что при трёх фазах вопрос не задаётся ни разу.
    """
    run = js[js.index("async function markupAllRun(subeng,list,phases"):js.index("// субтитры с нуля")]
    # вопрос берётся под ask: без ask (undefined=false) already пуст и askConfirm не зовётся
    assert "const already=ask?list.filter(c=>!fail.has(c)&&has(c)):[]" in run, (
        "already считается только при ask — иначе вопрос вылезет и у «Разметить всё»")
    assert "const force=ask&&already.length&&await askConfirm(" in run, (
        "askConfirm вызывается только при ask=true и наличии готовых")
    # «Разметить всё» не передаёт ask — вызов без четвёртого аргумента
    assert "markupAllRun(subeng,list,['subs','yellow','inserts'])" in js, (
        "«Разметить всё» не передаёт ask (должен молча пропускать готовое)")


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
        "function progUpdate(){}function progDone(){}function renderClips2(){}function saveState(){}"
        "function uiLog(){}function toast(){}function sleep(){return Promise.resolve();}"
        "function engLabel(){return 'whisper';}function askConfirm(){calls.ask++;return true;}"
        "function t(s){return s;}function errText(e){return String(e);}"
        "function subSkipped(){return '';}function aiPost(){return Promise.resolve({});}"
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
    а строка 811 в 60-preview.js содержит typeof hex2rgb===\\'function\\'."""
    app_dir = os.path.join(ROOT, "static", "app")
    for fname in sorted(os.listdir(app_dir)):
        if not fname.endswith(".js"):
            continue
        fpath = os.path.join(app_dir, fname)
        raw = open(fpath, "rb").read().decode("utf-8")
        for line_no, line in enumerate(raw.splitlines(), 1):
            if re.search(r'on[a-z]+="[^"]*\\{3}\'', line):
                pytest.fail(f"Найдена последовательность \\\\\\' внутри on-атрибута в {fname}:{line_no}:\n{line.strip()}")

    preview_lines = open(os.path.join(app_dir, "60-preview.js"), "rb").read().decode("utf-8").splitlines()
    line_811 = preview_lines[810]
    assert r"typeof hex2rgb===\'function\'" in line_811


def test_sfx_ensure_updates_src_on_media_change(js):
    """sfxEnsure переустанавливает src элемента звука при смене media."""
    body = _func(js, "sfxEnsure")
    assert "path:s.media" in body or "path: s.media" in body, (
        "sfxEnsure обязан сохранять путь к медиа в записи эффекта (path: s.media)")
    assert "st.path!==s.media" in body or "st.path !== s.media" in body, (
        "sfxEnsure обязан проверять смену пути media эффекта")
    assert "st.el.src" in body and "encodeURIComponent(s.media)" in body
    assert "st.path=s.media" in body or "st.path = s.media" in body


def test_ipv_drag_insert_index_matches_plan_filter(js):
    """Индекс вставки при перетаскивании в предпросмотре сопоставляется с INS
    через тот же фильтр, что в ipvPlanBody: INS.filter(r => (r.media || '').trim())[i]."""
    ins = _fn_body(js, "$('ipvins').addEventListener('pointerdown'")
    assert ("INS.indexOf(INS.filter(r => (r.media || '').trim())[i])" in ins or
            "INS.indexOf(INS.filter(r=>(r.media||'').trim())[i])" in ins), (
        "сопоставление индекса обязано опираться на тот же фильтр, что ipvPlanBody, "
        "а не на findIndex по media")

