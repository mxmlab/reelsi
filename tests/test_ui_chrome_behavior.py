# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Поведенческие проверки интерфейса на НАСТОЯЩЕМ Chrome (серия B3, порция 3).

Страница — та же, что отдаёт сервер (static/app/*.js), открытая из file:// с заглушкой /api
(см. tests/_chrome_stand.py). Мышь и клавиатура — события браузера через DevTools Protocol.
Проверяется то, что видно и что записано в состояние: какой кусок выделен и какая подсказка
висит под курсором, куда уехало окно редактора, какая полоса маски на кадре, какой стиль
получил значение от поля панели. Не текст функций.

Все тесты помечены xdist_group("chrome"): стенд идёт в одном воркере (-n auto --dist loadgroup).
"""
import json

import pytest

from _chrome_stand import chrome_stand, run_steps

pytestmark = [pytest.mark.xdist_group("chrome"), chrome_stand]

# Открываем редактор так же, как пользователь: модалка «Превью» (60-preview.js openPreview),
# затем подключаем обработчики редактора и рисуем таймлайн на синтетическом исходнике.
ED_SETUP = """(()=>{
  openModal('mbPreview');
  ED.xml='D:/p/A.xml'; ED.dur=60; ED.fps=60;
  ED.blocks=[{s0:0,s1:20},{s0:30,s1:60}];
  ED.cuts=[{t0:20,t1:30,source:'ИИ',text:'лишнее слово',reason:'тишина',rule:'правило X'}];
  ED.v0=0; ED.v1=60; ED.cs=5; ED.peaks=[]; ED.br=[]; ED.brBand=0; ED.hist=[];
  edBind(); edDraw(); edUI(); return true; })()"""


def _style_steps():
    """Стили и схема панели — те же данные, что отдаёт сервер (core/styles, core/style_schema).

    Заглушка /api отдаёт их странице; дальше страница сама грузит стили и рисует панель.
    """
    from core import style_schema, styles
    sch = dict(style_schema.schema())
    sch["ok"] = True
    sty = {"ok": True, "styles": styles.all_styles()}
    return [
        {"op": "eval", "js": "window.__api['/api/style_schema']=()=>(" + json.dumps(sch, ensure_ascii=False) + ");"
                             "window.__api['/api/styles']=()=>(" + json.dumps(sty, ensure_ascii=False) + ");"},
        # открываем превью и переходим на вкладку «Стиль» — панель видна только там
        {"op": "eval", "js": "(async()=>{await loadStyles();await loadStyleSchema();renderStylePanel();"
                             "CLIPS=[{xml:'D:/p/A.xml',name:'A',status:{},inserts:[],job:defJob()}];curAE=0;"
                             "openAEPreview();aewSetMode('style');return true;})()"},
    ]


# ============== 1. МАСКА РОТО: ПОЛОСА ПО ВЫСОТЕ, СКРЫТИЕ, АВТОСКРЫТИЕ ==============
def test_chrome_roto_mask_shows_band_at_the_given_percent_and_hides(tmp_path):
    """Маска рото: полоса на кадре имеет высоту «Низ маски %», гаснет по hide и сама через 1.5 с.

    Замена test_style_roto_bottom_shows_mask_while_editing (поведение на экране; CSS-полоса и
    схема остаются в старом тесте).
    """
    res = run_steps(tmp_path, [
        {"op": "eval", "js": "(()=>{const s=document.querySelector('.pvstage');"
                             "if(!s){const d=document.createElement('div');d.className='pvstage';"
                             "document.body.appendChild(d);}return true;})()"},
        {"op": "eval", "name": "sync", "js":
            "(()=>{rotoMaskSync(35);const m=document.querySelector('.pvstage .rotomask');"
            "return [!!m, m&&m.style.display, m&&m.querySelector('.rmband').style.height];})()"},
        {"op": "eval", "name": "hidden", "js":
            "(()=>{rotoMaskHide();const m=document.querySelector('.pvstage .rotomask');"
            "return m?m.style.display:'none-el';})()"},
        {"op": "eval", "js": "rotoMaskSync(20);true"},
        {"op": "wait", "ms": 1800},
        {"op": "eval", "name": "auto", "js":
            "(()=>{const m=document.querySelector('.pvstage .rotomask');return m?m.style.display:'none-el';})()"},
    ])
    assert res["sync"] == [True, "", "35%"], res
    assert res["hidden"] == "none", res
    assert res["auto"] == "none", res


# ============== 2. СРЕДНЯЯ КНОПКА В РЕДАКТОРЕ — ПАН, ЛЕВАЯ — НЕТ ==============
def test_chrome_editor_middle_button_pans_and_left_button_does_not(tmp_path):
    """Средняя кнопка в редакторе сдвигает окно видимости по времени, левая — нет.

    Замена test_editor_middle_button_pans_and_keeps_the_rest (поведение; курсор и auxclick
    остаются в старом тесте).
    """
    res = run_steps(tmp_path, [
        {"op": "eval", "js": ED_SETUP},
        {"op": "eval", "js": "ED.v0=10;ED.v1=40;edDraw();true"},
        {"op": "eval", "name": "before", "js": "[ED.v0,ED.v1]"},
        {"op": "drag", "sel": "#edtl", "fx": 0.5, "fy": 0.8, "dx": -120, "dy": 0, "button": "middle", "steps": 10},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "afterMid", "js": "[ED.v0,ED.v1]"},
        {"op": "drag", "sel": "#edtl", "fx": 0.5, "fy": 0.8, "dx": -120, "dy": 0, "button": "left", "steps": 10},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "afterLeft", "js": "[ED.v0,ED.v1]"},
    ])
    before, mid, left = res["before"], res["afterMid"], res["afterLeft"]
    assert mid[0] > before[0] + 1, res                       # окно уехало вперёд по времени
    assert abs((mid[1] - mid[0]) - (before[1] - before[0])) < 1e-6, res   # масштаб не меняется
    assert left == mid, res                                  # левая кнопка окно не двигает


# ============== 3. ПОДСКАЗКА «ВЫРЕЗАНО» — ПО КУРСОРУ НАД ВЫРЕЗАННЫМ КУСКОМ ==============
def _point_at(time_sec):
    """JS, который отдаёт css-координаты точки таймлайна на времени time_sec (y — у низа дорожки)."""
    return ("(()=>{const c=$('edtl');const r=c.getBoundingClientRect();"
            "return [r.left+(%s-ED.v0)/(ED.v1-ED.v0)*r.width, r.top+r.height*0.8];})()" % time_sec)


def test_chrome_editor_tooltip_names_the_rule_of_the_cut_under_cursor(tmp_path):
    """Над вырезанным куском подсказка называет источник, текст, причину и правило; над блоком — пусто.

    Замена test_editor_tooltip_shows_cut_rule (поведение по курсору; текст шаблона — в старом тесте).
    """
    res = run_steps(tmp_path, [
        {"op": "eval", "js": ED_SETUP},
        {"op": "mouseAt", "type": "mouseMoved", "js": _point_at(25)},
        {"op": "wait", "ms": 120},
        {"op": "eval", "name": "tipRule", "js": "document.getElementById('edcut').textContent"},
        {"op": "mouseAt", "type": "mouseMoved", "js": _point_at(10)},
        {"op": "wait", "ms": 120},
        {"op": "eval", "name": "inBlock", "js": "document.getElementById('edcut').textContent"},
        {"op": "eval", "js": "ED.cuts[0].rule='';true"},
        {"op": "mouseAt", "type": "mouseMoved", "js": _point_at(25)},
        {"op": "wait", "ms": 120},
        {"op": "eval", "name": "tipNoRule", "js": "document.getElementById('edcut').textContent"},
    ])
    tip = res["tipRule"]
    assert "правило X" in tip and "лишнее слово" in tip and "тишина" in tip, res
    assert res["inBlock"] == "", res
    assert "правило" not in res["tipNoRule"] and "тишина" in res["tipNoRule"], res


# ============== 4. ВЫСОТА СУБТИТРОВ: ПОЛЕ ДВИГАЕТ СТРОКУ В ПРЕВЬЮ ВЖИВУЮ ==============
def test_chrome_subtitle_height_field_moves_the_preview_line_live(tmp_path):
    """Поле «Высота субтитров %» двигает строку в превью сразу: её отступ снизу равен введённому проценту.

    Ввод — с клавиатуры: фокус на значении, Enter, цифры, Enter. Замена части
    test_style_sub_height_live_moves_preview_subtitle (поведение; проводка stEdit в тексте — в старом тесте).
    """
    res = run_steps(tmp_path, [
        *_style_steps(),
        # группа «Положение» открывается кликом по её строке, как у пользователя
        {"op": "mouse", "sel": '.stgroup[data-tw="subs.tr"]', "fx": 0.4, "fy": 0.5, "type": "mousePressed", "button": "left"},
        {"op": "mouse", "sel": '.stgroup[data-tw="subs.tr"]', "fx": 0.4, "fy": 0.5, "type": "mouseReleased", "button": "left"},
        {"op": "wait", "ms": 100},
        {"op": "eval", "js": "document.getElementById('st_sub_y_val').focus();true"},
        {"op": "key", "key": "Enter", "vk": 13},
        {"op": "text", "text": "25"},
        {"op": "key", "key": "Enter", "vk": 13},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "state", "js": "[CURSTYLE.sub_y, document.getElementById('pvsub').style.bottom]"},
    ])
    sub_y, bottom = res["state"]
    assert sub_y is not None, res
    assert bottom == "%d%%" % round((1 - sub_y) * 100), res
    assert bottom == "25%", res


# ============== 5. ПАНЕЛЬ СТИЛЯ: ЧИСЛО, ГАЛКА, СПИСОК, ЦВЕТ ПИШУТ В СТИЛЬ ==============
def test_chrome_style_panel_controls_write_into_the_style(tmp_path):
    """Каждый тип контрола панели стиля записывает значение в текущий стиль (CURSTYLE).

    Замена части test_style_panel_cp3_all_fields_call_stedit (поведение контролов; обработчики
    в тексте панели остаются в старом тесте). Число — с клавиатуры, галка — настоящим кликом,
    список и цвет — изменением значения элемента (событие change).
    """
    res = run_steps(tmp_path, [
        *_style_steps(),
        {"op": "eval", "js": "(()=>{CURSTYLE=CURSTYLE||{};return true;})()"},
        {"op": "mouse", "sel": '.stgroup[data-tw="subs.hl"]', "fx": 0.4, "fy": 0.5, "type": "mousePressed", "button": "left"},
        {"op": "mouse", "sel": '.stgroup[data-tw="subs.hl"]', "fx": 0.4, "fy": 0.5, "type": "mouseReleased", "button": "left"},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "boldBefore", "js": "!!CURSTYLE.hl_bold"},
        {"op": "mouse", "sel": "#st_hl_bold", "fx": 0.5, "fy": 0.5, "type": "mousePressed", "button": "left"},
        {"op": "mouse", "sel": "#st_hl_bold", "fx": 0.5, "fy": 0.5, "type": "mouseReleased", "button": "left"},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "boldAfter", "js": "!!CURSTYLE.hl_bold"},
        {"op": "eval", "js": "(()=>{const s=document.getElementById('st_sub_case');"
                             "const o=[...s.options].map(x=>x.value).filter(v=>v!==s.value);"
                             "s.value=o[0];s.dispatchEvent(new Event('change',{bubbles:true}));"
                             "window.__picked=o[0];return o[0];})()", "name": "pickedCase"},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "caseAfter", "js": "CURSTYLE.sub_case"},
        {"op": "eval", "js": "(()=>{const h=document.getElementById('st_sub_fill_hex');"
                             "h.value='#ff0000';h.dispatchEvent(new Event('change',{bubbles:true}));return true;})()"},
        {"op": "wait", "ms": 100},
        {"op": "eval", "name": "fill", "js": "CURSTYLE.sub_fill"},
    ])
    assert res["boldAfter"] != res["boldBefore"], res
    assert res["caseAfter"] == res["pickedCase"], res
    # цвет хранится как RGB-доли: #ff0000 -> [1, 0, 0]
    assert list(res["fill"]) == [1, 0, 0], res


# ============== 7. ВЫПАДАЮЩИЕ МЕНЮ: ОДНА МЕХАНИКА ЗАКРЫТИЯ ==============
# Список спикера клипа (.spkpick, нативный details) и меню дорожки подкаста (#podcast_track)
# закрываются одинаково: клик мимо, Esc (фокус на кнопку), уход фокуса; открыт один.
# Место «мимо меню»: фиксированный блок в правом нижнем углу, с полем для ухода фокуса.
DD_OUT = """(()=>{
  const out=document.createElement('div');out.id='ddout';
  out.style.cssText='position:fixed;left:1000px;top:760px;width:80px;height:40px;z-index:70';
  document.body.appendChild(out);
  // поле отдельно от «мимо»-блока: клик по блоку не должен попадать в поле (иначе фокус уходит
  // в другую сторону, и правило 85-inserts-view.js гасит поле на pointerdown)
  const f=document.createElement('input');f.id='ddfocus';
  f.style.cssText='position:fixed;left:1000px;top:820px;width:80px;z-index:70';
  document.body.appendChild(f);return true;})()"""
# Два списка спикера (как строки шага 1) в видимой части окна: клики по ним должны попадать.
DD_SPK = """(()=>{
  SPEAKERS={a:{label:'Аня'},b:{label:'Борис'}};
  CLIPS=[{xml:'D:/p/A.xml',name:'A',job:defJob()},{xml:'D:/p/B.xml',name:'B',job:defJob()}];
  // каждый список — в своей колонке: открытый список не должен накрывать сводку соседнего
  [0,1].forEach(i=>{const w=document.createElement('div');w.id='spkw'+i;
    w.style.cssText='position:fixed;left:'+(20+400*i)+'px;top:140px;width:200px;z-index:60';
    w.innerHTML=spkSelHTML(i,CLIPS[i]);document.body.appendChild(w);});
  return true;})()"""

D1 = "#spkw0 > details"
D2 = "#spkw1 > details"


def _click(sel):
    # Пауза после клика: правило 70-editor.js снимает фокус с кнопки и summary отложенным таймером
    # (setTimeout 0). Без паузы он мог сработать уже после следующего шага и сбить фокус теста.
    return [{"op": "mouse", "sel": sel, "fx": 0.5, "fy": 0.5, "type": "mousePressed", "button": "left"},
            {"op": "mouse", "sel": sel, "fx": 0.5, "fy": 0.5, "type": "mouseReleased", "button": "left",
             "after_ms": 60}]


def test_chrome_speaker_list_closes_on_outside_click_esc_and_focus_out(tmp_path):
    """Список спикера: клик мимо и уход фокуса закрывают; Esc закрывает и возвращает фокус на summary.

    Esc не должен дойти до стека модалок: окно «Предпросмотр» под списком остаётся открытым.
    """
    steps = [{"op": "eval", "js": DD_SPK}, {"op": "eval", "js": DD_OUT}]
    steps += _click(D1 + " > summary")
    steps += [{"op": "eval", "name": "open1", "js": "document.querySelector(%r).open" % D1}]
    steps += _click("#ddout")                                   # клик мимо
    steps += [{"op": "eval", "name": "outside", "js": "document.querySelector(%r).open" % D1}]
    steps += _click(D1 + " > summary")
    # Окно под списком: модалка перекрывает страницу, поэтому открываем её уже после клика,
    # фокус возвращаем на summary и жмём Esc. Esc закрывает список, а окно остаётся открытым.
    # openModal сам переносит фокус отложенно, поэтому список открываем уже после паузы
    steps += [{"op": "eval", "js": "openModal('mbPreview');true"}, {"op": "wait", "ms": 300},
              {"op": "eval", "js": "document.querySelector(%r).open=true;"
                                   "document.querySelector(%r+' > summary').focus();true" % (D1, D1)},
              {"op": "wait", "ms": 50},
              {"op": "key", "key": "Escape", "vk": 27},
              {"op": "eval", "name": "esc", "js": (
                  "[document.querySelector(%r).open,"
                  " document.activeElement===document.querySelector(%r+' > summary'),"
                  " $('mbPreview').classList.contains('on')]") % (D1, D1)},
              {"op": "eval", "js": "closeModal('mbPreview');true"}]
    steps += _click(D1 + " > summary")                          # открыли снова
    steps += _click(D2 + " > summary")                          # второй — первый должен закрыться
    steps += [{"op": "eval", "name": "second", "js": "[document.querySelector(%r).open,"
                                                      " document.querySelector(%r).open]" % (D1, D2)}]
    steps += _click(D2 + " .opts button[data-k=\"b\"]")          # пункт — спикер поставлен, список закрыт
    steps += [{"op": "eval", "name": "picked", "js": "[CLIPS[1].job.speaker, document.querySelector(%r).open]" % D2}]
    steps += _click(D2 + " > summary")
    # фокус — внутри списка (на его сводке), потом уходит на поле вне меню
    steps += [{"op": "eval", "js": "document.querySelector(%r+' > summary').focus();true" % D2},
              {"op": "eval", "js": "document.getElementById('ddfocus').focus();true"},
              {"op": "wait", "ms": 50},
              {"op": "eval", "name": "focusOut", "js": "document.querySelector(%r).open" % D2}]
    res = run_steps(tmp_path, steps)
    assert res["open1"] is True, res
    assert res["outside"] is False, res
    assert res["esc"] == [False, True, True], res
    assert res["second"] == [False, True], res
    assert res["picked"] == ["b", False], res
    assert res["focusOut"] is False, res


def test_chrome_speaker_list_one_open_when_opened_from_script(tmp_path):
    """Открытие с клавиатуры (атрибут open из кода) тоже закрывает предыдущий список — через toggle."""
    res = run_steps(tmp_path, [
        {"op": "eval", "js": DD_SPK},
        {"op": "eval", "js": "document.querySelector(%r).open=true;true" % D1},
        {"op": "wait", "ms": 50},
        {"op": "eval", "js": "document.querySelector(%r).open=true;true" % D2},
        {"op": "wait", "ms": 50},
        {"op": "eval", "name": "oneOpen", "js": "[document.querySelector(%r).open,"
                                                  " document.querySelector(%r).open]" % (D1, D2)},
    ])
    assert res["oneOpen"] == [False, True], res


def test_chrome_track_menu_closes_the_same_way(tmp_path):
    """Меню дорожки подкаста: клик мимо, Esc (фокус на кнопку, выбор не меняется), уход фокуса; строка внутри — не уход."""
    steps = [{"op": "eval", "js": DD_OUT}]
    steps += _click("#podcast_track")
    steps += [{"op": "eval", "name": "tOpen", "js": "PODMENU"}]
    steps += _click("#ddout")
    steps += [{"op": "eval", "name": "tOutside", "js": "PODMENU"}]
    steps += _click("#podcast_track")
    steps += [{"op": "eval", "name": "selBefore", "js": "POD_TRACK_SEL"},
              {"op": "eval", "js": "podFocusTrackRow();true"},
              {"op": "key", "key": "Escape", "vk": 27},
              {"op": "eval", "name": "tEsc", "js": "[PODMENU, document.activeElement.id, POD_TRACK_SEL]"}]
    steps += _click("#podcast_track")
    steps += [{"op": "eval", "js": "podFocusTrackRow();true"},
              {"op": "eval", "name": "tInside", "js": "PODMENU"}]
    steps += [{"op": "eval", "js": "document.getElementById('ddfocus').focus();true"},
              {"op": "wait", "ms": 50},
              {"op": "eval", "name": "tFocusOut", "js": "PODMENU"}]
    res = run_steps(tmp_path, steps)
    assert res["tOpen"] is True, res
    assert res["tOutside"] is False, res
    assert res["tEsc"] == [False, "podcast_track", res["selBefore"]], res
    assert res["tInside"] is True, res
    assert res["tFocusOut"] is False, res
