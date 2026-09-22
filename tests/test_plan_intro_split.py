# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: расчёт интро (задание MS, этап 2).

Расчёт интро уехал из `scene_plan` в `core/xml2ae/plan_intro.py`. Сторож держит СТЫК двух
дверей одной арифметики: `plan_intro`, вызванный НАПРЯМУЮ на фикстуре
(`tests/fixtures/timeline_subs.xml.gz`), обязан отдать ровно то, что `scene_plan` кладёт
в план (`plan["intro"]`) и в шаблон (INTRO_GROUPS, INTRO_IDY, INTRO_ON2, INTRO_LY/LX/LK,
INTRO_FX, INTRO_SUB_FX, INTRO_SQ). Разъедутся — .jsx соберётся не по тому, что рисует
предпросмотр, и увидеть это можно только в AE.

Фикстура сторожа — три группы интро (как в задании): «большое слева» (задание ZY), глитч
на полосе субтитров (ПРАВКИ 3/4 и правило MH: окно режется, появление слова сжимается) и
хвост ролика (последняя группа — правило MH её не трогает).

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока (разбор XML,
резолв стиля, разбивка интро на группы, данные субтитров из `plan_subs`): на разных входах
сравнение шло бы вхолостую. Ключи зума Камеры 1 и окна видеовставок берутся из самого
плана — своей копии арифметики зума и вставок сторож не держит.

Запуск: python -m pytest tests/test_plan_intro_split.py -q
"""
import copy
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import styles, xml2ae  # noqa: E402
from core.xml2ae.build import (INTRO_ANIMS, _accent_word, _intro_appear_dur,  # noqa: E402
                               _intro_cnt_positions, _intro_fit_ds, _intro_line_font,
                               _parse_intro_count, _sv, _sv_or)
from core.xml2ae.jsutil import _jd  # noqa: E402
from core.xml2ae.plan_intro import IntroInputs, _g_at, plan_intro  # noqa: E402
from core.xml2ae.plan_subs import SubsInputs, plan_subs  # noqa: E402

PS = "TestInk-Regular"          # шрифта с таким именем нет — см. докстринг
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер фикстуры: 1-я секунда кам1, 8-я — перебивка

# Три группы: «большое слева» (big), глитч на полосе субтитров (gy = 600 px тянет блок
# вниз, на полосу) и хвост ролика. Глитч-слово в 1.9167 не успевает доиграть появление
# (0.44 с) до начала затухания к субтитру 2.3667 — здесь же считается INTRO_SQ.
INTRO = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["КИЛО"], "color": "white", "times": [T_CAM1 + 0.3]},
    {"words": ["ЗА МЕСЯЦ"], "color": "white", "times": [T_CAM1 + 0.6]},
    {"words": ["ПЕРВОЕ", "ВТОРОЕ"], "color": "yellow", "times": [1.6667, 1.9167],
     "anim": "glitch", "gy": 600},
    {"words": ["ХВОСТ"], "color": "white", "times": [5.4]},
]
SPLITS = [3, 4]

# Вторая фикстура: ветки строки интро (accent перебивает back, свой цвет color=="custom",
# счётчик), ручки раскладки (шаг заднего плана, шаг после него, кегль) и якорь «first».
STYLED = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["ФОН"], "color": "white", "times": [T_CAM1 + 0.3], "back": True},
    {"words": ["АКЦЕНТ"], "color": "accent", "times": [T_CAM1 + 0.6], "accent": True},
    {"words": ["ЦВЕТ"], "color": "custom", "times": [T_CAM1 + 0.9], "fill": [0.2, 0.4, 0.9]},
    {"words": ["ЖЁЛТОЕ"], "color": "yellow", "times": [T_CAM1 + 1.2], "fx": "glow"},
    {"words": ["ДВЕНАДЦАТЬ"], "color": "custom", "times": [T_CAM1 + 1.5], "is_count": True,
     "dec": 2, "fill": [0.1, 0.8, 0.3]},
    {"words": ["ХВОСТ"], "color": "white", "times": [5.4]},
]
STYLED_SPLITS = [6]
STYLED_STYLE = {"accent_font": PS, "back_font": PS, "back_scale": 0.65,
                "intro_line_step": 200, "intro_big_step": 120, "back_step_after": 0.5,
                "intro_anchor": "first", "intro_fit_w": 80}

# Видеовставка поверх окна группы интро: группа уезжает наверх (INTRO_FRONT), а не на
# нул камеры — признак читают и шаблон, и превью.
INS_VIDEO = [{"type": "video", "media": "no_such_video.mp4", "style": "cam2",
              "start_s": 1.2, "dur_s": 1.0}]


@pytest.fixture()
def xml_subs(tmp_path):
    """Ролик фикстуры: 1-я секунда — кам1, 8-я — перебивка (та же, что у golden-теста)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Детерминизм сборки: цензура читает поставочные списки, а не личный badwords.user.txt."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


def _active_cam_at(cams, t_sec, fps):
    """Индекс показываемой камеры в момент t: то же правило, что camAt в .jsx (его же
    считает scene_plan своим `_active_cam_at`). Им закрывается ВЫРОЖДЕННОЕ окно группы."""
    f = t_sec * fps + 1e-4
    best = 0
    for ci in range(len(cams)):
        for cl in cams[ci]["clips"]:
            if cl[4] and cl[0] <= f < cl[1]:
                best = ci
                break
    return best


def _inputs(xml, plan, style=None, intro=None, splits=None, highlights=None):
    """IntroInputs ровно такими, какими их собрал бы scene_plan к моменту вызова блока.

    Повторяет только ЧТЕНИЯ scene_plan: разбор XML, резолв стиля, разбивку интро на группы
    (с тем же `_g_at`, что и в build.py) и ключи стиля, плюс `plan_subs` — данные субтитров,
    по которым считается правило MH. Ключи зума Камеры 1 и окна видеовставок берутся из
    САМОГО плана (`plan["zoom"]`, `plan["inserts"]`): своей копии арифметики зума, вставок
    и раскладки камер здесь нет намеренно — иначе сторож проверял бы копию правила.
    """
    meta, cams, subs, _xml_inserts = xml2ae.parse_full(xml)
    st = styles.resolve(dict(style or {}, font=PS))
    font_ps = _sv_or(st, "font")
    hl_font_ps = st.get("hl_font") or font_ps
    hl = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    sp = plan_subs(SubsInputs(
        subs=subs, hl=hl, brk=set(), cnt=set(), joins=set(),
        font_ps=font_ps, hl_font_ps=hl_font_ps,
        sub_case=(_sv_or(st, "sub_case")).strip(),
        sub_words_per_row=max(1, int(_sv_or(st, "sub_words_per_row"))),
        sub_rows_max=max(1, int(_sv_or(st, "sub_rows_max"))),
        width=meta["w"], height=meta["h"], fps=meta["fps"] or 60, cams=cams,
        word_timings=None, st=st, sv=_sv, sv_or=_sv_or,
        accent_word=_accent_word, parse_count=_parse_intro_count))
    # интро разбиваем на группы по splits — ровно как scene_plan (до вызова блока)
    intro_lines = [x for x in (intro or []) if (x.get("words") or (x.get("text") or "").strip())]
    cuts = sorted(set(int(s) for s in (splits or []) if 0 < int(s) < len(intro_lines)))
    bounds = [0] + cuts + [len(intro_lines)]
    groups = [intro_lines[bounds[k]:bounds[k + 1]] for k in range(len(bounds) - 1)]
    groups.sort(key=_g_at)                     # группы идут по таймингу (первая — с 0)
    return IntroInputs(
        groups=groups, cams=cams, meta=meta, fps=meta["fps"] or 60,
        active_cam_at=lambda t: _active_cam_at(cams, t, meta["fps"] or 60),
        # окна ВИДЕОвставок — то же правило, что в scene_plan (по готовому плану вставок)
        video_segs=[(ins["start"], ins["end"]) for ins in plan["inserts"]
                    if ins.get("t") == "video"],
        subs=sp, font_ps=font_ps,
        intro_font_ps=st.get("intro_font") or font_ps,
        intro_hl_font_ps=st.get("intro_hl_font") or hl_font_ps,
        accent_font_ps=(_sv_or(st, "accent_font")).strip(),
        accent_case=(_sv_or(st, "accent_case")).strip(),
        back_font_ps=(_sv_or(st, "back_font")).strip(),
        back_case=(_sv_or(st, "back_case")).strip(),
        any_back=any(bool(x.get("back") and not x.get("accent")) for g in groups for x in g),
        any_glitch=any(x.get("anim") == "glitch" for g in groups for x in g),
        st=st, sv=_sv, sv_or=_sv_or,
        # Правила, живущие в build.py (задание MS): своей копии у модуля нет.
        accent_word=_accent_word, parse_count=_parse_intro_count,
        cnt_positions=_intro_cnt_positions, line_font=_intro_line_font,
        fit_ds=_intro_fit_ds, appear_dur=_intro_appear_dur, anims=INTRO_ANIMS,
        back_step=float(_sv(st, "back_step")),
        back_step_after=(None if _sv(st, "back_step_after") is None
                         else float(_sv(st, "back_step_after"))),
        back_scale=float(_sv(st, "back_scale")),
        line_step_k=float(_sv(st, "intro_line_step")) / 100.0,
        big_step_k=float(_sv(st, "intro_big_step")) / 100.0,
        intro_fade=float(_sv(st, "intro_fade")),
        intro_fx_hold_add=float(_sv(st, "intro_fx_hold_add")),
        intro_sub_cut=bool(_sv(st, "intro_sub_cut")),
        intro_sub_fade=float(_sv(st, "intro_sub_fade")),
        intro_scale_k=float(_sv_or(st, "intro_scale")) / 100,
        fit_w=float(_sv_or(st, "intro_fit_w")) / 100.0,
        fit_max=float(_sv_or(st, "intro_fit_max")),
        intro_cam=bool(_sv(st, "intro_cam")),
        # Ключи зума и тип интерполяции — из плана: ровно те, с которыми звался блок.
        cam1_scale=plan["zoom"]["keys"], holds=[bool(h) for h in plan["zoom"]["holds"]],
        shadow_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow_fill"))],
        shadow_op=float(_sv(st, "intro_comp_shadow_op")),
        shadow2_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow2_fill"))],
        shadow2_op=float(_sv(st, "intro_comp_shadow2_op")))


def _check(xml, intro=None, splits=None, style=None, highlights=None, inserts=None):
    """Сверить plan_intro с планом scene_plan; -> (plan, IntroPlan).

    Шрифт кладётся в САМ стиль (как в соседних интро-тестах): шрифта с таким именем на
    машине нет, и ни автофит, ни раскладка не зависят от того, что установлено.
    """
    style = dict(style or {}, font=PS)
    plan = xml2ae.scene_plan(xml, emit=lambda *a: None, disclaimer="", intro_riser=False,
                             intro=intro, intro_splits=splits or [], style=style,
                             highlights=highlights or [], inserts=inserts)
    ip = plan_intro(_inputs(xml, plan, style=style, intro=intro, splits=splits,
                            highlights=highlights))
    ae = plan["_ae"]
    # группы интро — то, что читает план (предпросмотр) и что уезжает в .jsx
    assert ip.intro == plan["intro"]
    # готовые массивы шаблона и подстановки
    assert ip.groups_js == ae["intro_groups"]
    assert _jd(ip.idy) == ae["intro_idy"]
    assert _jd(ip.on2) == ae["intro_on2"]
    assert ip.on2 == [int(g["on2"]) for g in plan["intro"]]
    assert ip.front == [int(g["front"]) for g in plan["intro"]]
    assert ip.above_roto == [bool(g["above_roto"]) for g in plan["intro"]]
    assert ip.ly == [g["ys"] for g in plan["intro"]]
    assert ip.lx == [g.get("lx") for g in plan["intro"]]
    assert ip.lk == [g.get("lk") for g in plan["intro"]]
    assert ip.fx_decl == ae["intro_fx_decl"] and ip.fx_out == ae["intro_fx_out"]
    assert ip.sub_fx_decl == ae["intro_sub_fx_decl"]
    assert ip.sub_fx_out == ae["intro_sub_fx_out"]
    assert ip.sq_decl == ae["intro_sq_decl"] and ip.sq_fn == ae["intro_sq_fn"]
    assert ip.accent_used == (ae["accent_params"] == ",af")
    # множитель появления: массив объявлен ровно тогда, когда есть что сжимать
    assert ip.sq_used == bool(ip.sq_decl)
    assert ip.sq_used == any(v is not None for row in ip.sq for word in row for v in word)
    return plan, ip


def test_plan_intro_big_glitch_three_groups(xml_subs):
    """«Большое слева», глитч на полосе субтитров и хвост: три группы, и все числа
    модуля совпадают с планом scene_plan."""
    plan, ip = _check(xml_subs, intro=INTRO, splits=SPLITS, style={"font": PS})
    assert len(plan["intro"]) == 3, "фикстура не разбилась на три группы"
    assert len(ip.ly) == len(ip.on2) == len(ip.front) == 3
    big = plan["intro"][0]
    assert big["lk"][0] > 1 and big["ys"][0] == big["ys"][2], "большая строка раскладывается не здесь"
    glitch = plan["intro"][1]
    assert glitch["te"] < glitch["ts"] + 1.0, "окно глитч-группы не срезано к субтитру (MH)"
    assert "sq" in glitch and any(v is not None for row in glitch["sq"] for v in row), (
        "появление глитч-слова не сжато")
    assert ip.fx_decl and ip.sub_fx_decl and ip.sq_decl, "подстановки шаблона пусты"


def test_plan_intro_styled_rows_land_in_jsx(xml_subs, tmp_path):
    """Строки заднего плана, акцента и своего цвета, ручки раскладки и якорь: выход
    plan_intro доезжает и до плана, и до .jsx (INTRO_GROUPS/INTRO_LY/INTRO_LX/INTRO_IDY)."""
    style = dict(STYLED_STYLE, font=PS)
    plan, ip = _check(xml_subs, intro=STYLED, splits=STYLED_SPLITS, style=style)
    assert ip.accent_used, "акцентная строка не доехала до подстановок"
    assert ip.ly and any(v is not None for v in ip.ly[0]), "Y базовых линий пуст"
    assert any(g["ys"] and g["ys"][1] - g["ys"][0] != 0 for g in plan["intro"])

    path, _n, _s = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "styled.jsx"),
                                     intro=STYLED, intro_splits=STYLED_SPLITS,
                                     style=style, disclaimer="", intro_riser=False,
                                     emit=lambda *a, **k: None)
    jsx = open(path, encoding="utf-8-sig").read()
    assert "var INTRO_GROUPS=" + ip.groups_js in jsx
    assert "var INTRO_IDY=" + _jd(ip.idy) in jsx
    assert "var INTRO_LY=" + _jd(ip.ly) in jsx
    assert "var INTRO_LX=" in jsx and _jd(ip.lx) in jsx, "INTRO_LX не из результата"


def test_plan_intro_knobs_and_detach(xml_subs):
    """Ручки, меняющие ветки блока: открепление от Камеры 1 (автофит откреплённого),
    выключенное правило MH и своя длина затухания — стык держится на каждой."""
    for style in ({"font": PS, "intro_cam": False, "intro_fit_w": 70, "intro_fit_max": 300},
                  {"font": PS, "intro_sub_cut": False},
                  {"font": PS, "intro_sub_fade": 0.3, "intro_fade": 0.1},
                  {"font": PS, "intro_scale": 60, "intro_y": 75, "intro_y2": 40}):
        plan, ip = _check(xml_subs, intro=INTRO, splits=SPLITS, style=style)
        assert ip.intro == plan["intro"]
    # откреплённое интро живёт в координатах кадра: автофит его увеличивает (ds != gs)
    plan, ip = _check(xml_subs, intro=INTRO, splits=SPLITS,
                      style={"font": PS, "intro_cam": False, "intro_fit_w": 70,
                             "intro_fit_max": 300})
    assert any(g["ds"] != 100 for g in plan["intro"]), "автофит откреплённого интро не сработал"


def test_plan_intro_video_insert_raises_group(xml_subs):
    """Группа легла на видеовставку — признак front (прекомп уезжает наверх) считается
    здесь и совпадает с планом."""
    plan, ip = _check(xml_subs, intro=INTRO, splits=SPLITS, style={"font": PS},
                      inserts=INS_VIDEO)
    assert any(ip.front), "фикстура: ни одна группа не легла на видеовставку"
    assert ip.front == [int(g["front"]) for g in plan["intro"]]


def test_plan_intro_without_intro(xml_subs):
    """Ролик без интро: строк нет — группа пустая (так же, как было в scene_plan: один
    пустой прекомп), ни множителей, ни подстановок — модуль ничего не выдумывает."""
    plan, ip = _check(xml_subs)
    assert len(plan["intro"]) == 1 and plan["intro"][0]["lines"] == []
    assert ip.intro == plan["intro"]
    assert ip.groups_js == "[[]]" and ip.ly == [[]] and ip.sq == [[]]
    for text in (ip.fx_decl, ip.fx_out, ip.sub_fx_decl, ip.sub_fx_out, ip.sq_decl, ip.sq_fn):
        assert text == "", "без интро подстановка не пуста: %r" % text
    assert not ip.accent_used and not ip.sq_used


def test_plan_intro_keeps_inputs_intact(xml_subs):
    """Входы не правятся «по месту»: группы, стиль и данные субтитров после вызова те же."""
    style = dict(STYLED_STYLE, font=PS)
    intro = STYLED + [{"words": ["НОМЕР"], "color": "custom", "times": [T_CAM2],
                       "is_count": True}]
    plan = xml2ae.scene_plan(xml_subs, emit=lambda *a: None, disclaimer="", intro_riser=False,
                             intro=intro, intro_splits=STYLED_SPLITS, style=style)
    inp = _inputs(xml_subs, plan, style=style, intro=intro, splits=STYLED_SPLITS)
    snapshot = copy.deepcopy(inp)
    plan_intro(inp)
    assert inp == snapshot
