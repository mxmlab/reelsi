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
