# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож распила scene_plan: подстановки шаблона интро (задание MV, этап 5).

Подстановки уехали из `scene_plan` в `core/xml2ae/plan_intro_tpl.py`. Сторож держит СТЫК:
`plan_intro_tpl`, вызванный НАПРЯМУЮ на фикстуре (`tests/fixtures/timeline_subs.xml.gz`),
обязан отдать ровно те строки JS, что `scene_plan` кладёт в план (`plan["_ae"]`), а собранный
.jsx — нести их дословно. Разъедутся — в AE уедет не то, что считает Python, и увидеть это
можно только рендером.

Входы собираются здесь ТАК ЖЕ, как их собирает `scene_plan` до вызова блока: разбор XML,
резолв стиля, разбивка интро на группы, `plan_subs` и `plan_intro` (готовые массивы
раскладки). Флаги строк и цвет считаются теми же правилами build.py (`_grp_big_i`,
`_tritone_on`), а не переписанными здесь копиями.

Запуск: python -m pytest tests/test_plan_intro_tpl_split.py -q
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
from core.xml2ae.build import (DEEP_GLOW2_GLITCH, INTRO_ANIMS, _accent_word,  # noqa: E402
                               _has_valid_count, _intro_appear_dur, _intro_cnt_positions,
                               _intro_fit_ds, _intro_line_font, _parse_intro_count, _sv, _sv_or,
                               _tritone_on)
from core.xml2ae.plan_intro import IntroInputs, _g_at, _grp_big_i, plan_intro  # noqa: E402
from core.xml2ae.plan_intro_tpl import IntroTplInputs, plan_intro_tpl  # noqa: E402
from core.xml2ae.plan_subs import SubsInputs, plan_subs  # noqa: E402

PS = "TestInk-Regular"          # шрифта с таким именем нет — см. докстринг соседнего сторожа
T1 = 1.0                        # первая группа интро: в фикстуре 1-я секунда — камера 1

# Глитч на полосе субтитров (gy тянет блок вниз) + «большое слева»: глитч/Deep Glow/Tritone,
# INTRO_LY и INTRO_LX/LK в одной фикстуре.
GLITCH_BIG = [
    {"words": ["8"], "color": "white", "times": [T1], "big": True},
    {"words": ["КИЛО"], "color": "white", "times": [T1 + 0.3]},
    {"words": ["ЗА МЕСЯЦ"], "color": "white", "times": [T1 + 0.6]},
    {"words": ["ПЕРВОЕ", "ВТОРОЕ"], "color": "yellow", "times": [1.6667, 1.9167],
     "anim": "glitch", "gy": 600},
    {"words": ["ХВОСТ"], "color": "white", "times": [5.4]},
]
GLITCH_BIG_SPLITS = [3, 4]

# Строки заднего плана, акцента и своего цвета + счётчик: ветки цвета (HL_FILL3, cf) и
# заднеплановой раскладки.
STYLED = [
    {"words": ["8"], "color": "white", "times": [T1], "big": True},
    {"words": ["ФОН"], "color": "white", "times": [T1 + 0.3], "back": True},
    {"words": ["АКЦЕНТ"], "color": "accent", "times": [T1 + 0.6], "accent": True},
    {"words": ["ЦВЕТ"], "color": "custom", "times": [T1 + 0.9], "fill": [0.2, 0.4, 0.9]},
    {"words": ["ЖЁЛТОЕ"], "color": "yellow", "times": [T1 + 1.2], "fx": "glow"},
    {"words": ["ДВЕНАДЦАТЬ"], "color": "custom", "times": [T1 + 1.5], "is_count": True,
     "dec": 2, "fill": [0.1, 0.8, 0.3]},
    {"words": ["ХВОСТ"], "color": "white", "times": [5.4]},
]
STYLED_SPLITS = [6]
STYLED_STYLE = {"accent_font": PS, "back_font": PS, "back_scale": 0.65,
                "intro_line_step": 200, "intro_big_step": 120, "back_step_after": 0.5,
                "intro_anchor": "first", "intro_fit_w": 80}

# Жёлтый глитч (тёмный и яркий) и жёлтая строка со свечением: Tritone, Deep Glow 2 и
# правило «свечение строки Deep Glow не берёт».
GLITCH_YELLOW = [
    {"words": ["ЖЁЛТЫЙ", "ГЛИТЧ"], "color": "yellow", "times": [T1, T1 + 0.3],
     "anim": "glitch"},
    {"words": ["ЖЁЛТОЕ"], "color": "yellow", "times": [T1 + 0.6], "fx": "glow"},
    {"words": ["ХВОСТ"], "color": "white", "times": [5.4]},
]
BRIGHT = [1.0, 0.9176, 0.0]     # яркий жёлтый: ни Tritone, ни Deep Glow (задания ZN/MK3)
DARK = [0.5, 0.2, 0.1]          # тёмный жёлтый: и Tritone, и Deep Glow ставятся

# Обычные строки без ручек: на них проверяются пустые подстановки (golden-текст шаблона).
PLAIN = [
    {"words": ["РАЗ"], "color": "white", "times": [T1]},
    {"words": ["ДВА"], "color": "white", "times": [T1 + 0.5]},
]

# Видеовставка поверх окна группы: группа уезжает наверх (INTRO_FRONT).
INS_VIDEO = [{"type": "video", "media": "no_such_video.mp4", "style": "cam2",
              "start_s": 1.2, "dur_s": 1.0}]
# Группа в нижней половине кадра (gy) и галка «интро над рото по положению» (задание C).
BELOW = [{"words": ["НИЗ"], "color": "white", "times": [T1], "gy": 600}]


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
    """Индекс показываемой камеры в момент t: то же правило, что camAt в .jsx."""
    f = t_sec * fps + 1e-4
    best = 0
    for ci in range(len(cams)):
        for cl in cams[ci]["clips"]:
            if cl[4] and cl[0] <= f < cl[1]:
                best = ci
                break
    return best


def _intro_plan(xml, plan, st, groups, font_ps, hl_font_ps):
    """IntroPlan — ровно так, как его получает scene_plan перед вызовом блока (задание MS).

    Своей арифметики здесь нет: тот же `plan_intro` с теми же входами, что собирает
    `scene_plan` (готовый план даёт ключи зума и окна видеовставок).
    """
    meta, cams, subs, _xml_inserts = xml2ae.parse_full(xml)
    hl = set()
    sp = plan_subs(SubsInputs(
        subs=subs, hl=hl, brk=set(), cnt=set(), joins=set(),
        font_ps=font_ps, hl_font_ps=hl_font_ps, sub_case=(_sv_or(st, "sub_case")).strip(),
        sub_words_per_row=max(1, int(_sv_or(st, "sub_words_per_row"))),
        sub_rows_max=max(1, int(_sv_or(st, "sub_rows_max"))),
        width=meta["w"], height=meta["h"], fps=meta["fps"] or 60, cams=cams,
        word_timings=None, st=st, sv=_sv, sv_or=_sv_or,
        accent_word=_accent_word, parse_count=_parse_intro_count))
    return plan_intro(IntroInputs(
        groups=groups, cams=cams, meta=meta, fps=meta["fps"] or 60,
        active_cam_at=lambda t: _active_cam_at(cams, t, meta["fps"] or 60),
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
        cam1_scale=plan["zoom"]["keys"], holds=[bool(h) for h in plan["zoom"]["holds"]],
        shadow_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow_fill"))],
        shadow_op=float(_sv(st, "intro_comp_shadow_op")),
        shadow2_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow2_fill"))],
        shadow2_op=float(_sv(st, "intro_comp_shadow2_op"))))


def _tpl_inputs(st, groups, ip, glitch_glow):
    """IntroTplInputs — так же, как их собирает scene_plan перед вызовом блока (задание MV).

    Флаги строк и цвета считаются здесь ПРАВИЛАМИ build.py (`_grp_big_i`, `_tritone_on`,
    `_has_valid_count`), а не своей копией: иначе сторож проверял бы копию правила.
    """
    intro_fill, intro_hl_fill = st.get("intro_fill"), st.get("intro_hl_fill")
    _yellow_rgb = intro_hl_fill if intro_hl_fill is not None else st.get("hl_fill")
    yellow_dark = _tritone_on(_yellow_rgb)
    dg_with_glow = bool(_sv(st, "intro_dg_with_glow"))
    return IntroTplInputs(
        groups=groups, intro=ip,
        any_glitch=any(x.get("anim") == "glitch" for g in groups for x in g),
        any_back=any(bool(x.get("back") and not x.get("accent")) for g in groups for x in g),
        any_big=any(_grp_big_i(g) is not None for g in groups),
        accent_color_used=any(x.get("color") == "accent" for g in groups for x in g),
        custom_color_used=any(x.get("color") == "custom" for g in groups for x in g),
        intro_fill=intro_fill, intro_hl_fill=intro_hl_fill, hl_fill3=st.get("hl_fill3"),
        yellow_dark=yellow_dark,
        # _dg_bright = not _yellow_dark (build.py): плагин берёт ТОЛЬКО тёмный жёлтый
        dg_on=glitch_glow == "deepglow2" and yellow_dark and any(
            x.get("anim") == "glitch" and x.get("color") == "yellow"
            and (dg_with_glow or x.get("fx") != "glow") for g in groups for x in g),
        dg_with_glow=dg_with_glow,
        shadow_on=bool(st.get("intro_shadow")),
        shadow_op=float(_sv(st, "intro_shadow_op")),
        shadow_dir=float(_sv(st, "intro_shadow_dir")),
        shadow_dist=float(_sv(st, "intro_shadow_dist")),
        shadow_soft=float(_sv(st, "intro_shadow_soft")),
        back_shadow_op=float(_sv(st, "back_shadow_op")),
        back_shadow_soft=float(_sv(st, "back_shadow_soft")),
        comp_shadow_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow_fill"))],
        comp_shadow_op=float(_sv(st, "intro_comp_shadow_op")),
        comp_shadow2_fill=[float(v) for v in (_sv_or(st, "intro_comp_shadow2_fill"))],
        comp_shadow2_op=float(_sv(st, "intro_comp_shadow2_op")),
        back_step=float(_sv(st, "back_step")), back_scale=float(_sv(st, "back_scale")),
        anims=INTRO_ANIMS, deep_glow=DEEP_GLOW2_GLITCH, has_valid_count=_has_valid_count)


def _prepare(xml, style=None, intro=None, splits=None, highlights=None, inserts=None,
             glitch_glow="builtin"):
    """Собрать всё, что нужно блоку: plan сцены, стиль, группы, IntroPlan и входы подстановок."""
    style = dict(style or {}, font=PS)
    plan = xml2ae.scene_plan(xml, emit=lambda *a: None, disclaimer="", intro_riser=False,
                             intro=intro, intro_splits=splits or [], style=style,
                             highlights=highlights or [], inserts=inserts,
                             glitch_glow=glitch_glow)
    st = styles.resolve(dict(style))
    font_ps = _sv_or(st, "font")
    hl_font_ps = st.get("hl_font") or font_ps
    intro_lines = [x for x in (intro or [])
                   if (x.get("words") or (x.get("text") or "").strip())]
    cuts = sorted(set(int(s) for s in (splits or []) if 0 < int(s) < len(intro_lines)))
    bounds = [0] + cuts + [len(intro_lines)]
    groups = [intro_lines[bounds[k]:bounds[k + 1]] for k in range(len(bounds) - 1)]
    groups.sort(key=_g_at)                     # группы идут по таймингу (первая — с 0)
    ip = _intro_plan(xml, plan, st, groups, font_ps, hl_font_ps)
    return plan, _tpl_inputs(st, groups, ip, glitch_glow)


# Все подстановки блока: имя поля результата -> ключ в plan["_ae"].
FIELDS = (
    ("hlfill3_decl", "hlfill3_decl"),
    ("fill_decl", "intro_fill_decl"),
    ("fill_params", "fill_params"),
    ("fill_call", "fill_call"),
    ("fill_pick", "intro_fill_pick"),
    ("shadow_decl", "intro_shadow_decl"),
    ("word_shadow_fn", "intro_word_shadow_fn"),
    ("word_shadow_line", "intro_word_shadow_line"),
    ("word_shadow_word", "intro_word_shadow_word"),
    ("ly_decl", "intro_ly_decl"),
    ("lx_decl", "intro_lx_decl"),
    ("big_fn", "intro_big_fn"),
    ("big_qi_vars", "intro_big_qi_vars"),
    ("big_line_pos", "intro_big_line_pos"),
    ("big_word_x", "intro_big_word_x"),
    ("line_layout", "intro_line_layout"),
    ("back_scale_fn", "intro_back_scale_fn"),
    ("back_scale_line", "intro_back_scale_line"),
    ("back_scale_line_w", "intro_back_scale_line_w"),
    ("back_scale_tmp", "intro_back_scale_tmp"),
    ("back_scale_word", "intro_back_scale_word"),
    ("back_scale_wpx", "intro_back_scale_wpx"),
    ("front_decl", "intro_front_decl"),
    ("front_arr_decl", "intro_front_arr_decl"),
    ("front_route", "intro_front_route"),
    ("front_raise", "intro_front_raise"),
    ("above_roto_decl", "intro_above_roto_decl"),
    ("above_roto_arr_decl", "intro_above_roto_arr_decl"),
    ("above_roto_route", "intro_above_roto_route"),
    ("above_roto_raise", "intro_above_roto_raise"),
    ("anim_fx_fn", "intro_anim_fx_fn"),
    ("line_anim", "intro_line_anim"),
    ("word_anim", "intro_word_anim"),
    ("hl_glow_fn", "intro_hl_glow_fn"),
    ("group_flags", "intro_group_flags"),
    ("comp_glow", "intro_comp_glow"),
    ("comp_shadow", "intro_comp_shadow"),
    ("comp_shadow_fn", "intro_comp_shadow_fn"),
)


def _check(xml, style=None, intro=None, splits=None, highlights=None, inserts=None,
           glitch_glow="builtin"):
    """Сверить plan_intro_tpl с планом scene_plan; -> (plan, IntroTpl)."""
    plan, inp = _prepare(xml, style=style, intro=intro, splits=splits,
                         highlights=highlights, inserts=inserts, glitch_glow=glitch_glow)
    tp = plan_intro_tpl(inp)
    ae = plan["_ae"]
    for field, key in FIELDS:
        assert getattr(tp, field) == ae[key], "подстановка %s разошлась с планом" % key
    return plan, tp, inp


def _jsx(xml, tmp_path, style=None, intro=None, splits=None, inserts=None,
         glitch_glow="builtin"):
    """Собрать .jsx тем же путём, что интерфейс, и вернуть текст."""
    path, _n, _s = xml2ae.to_ae_full(
        xml, jsx_path=str(tmp_path / "tpl.jsx"), intro=intro, intro_splits=splits or [],
        style=dict(style or {}, font=PS), inserts=inserts, disclaimer="", intro_riser=False,
        glitch_glow=glitch_glow, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def test_intro_tpl_glitch_big_matches_plan_and_jsx(xml_subs, tmp_path):
    """Глитч, жёлтая строка и «большое слева»: подстановки эффектов и раскладки совпадают
    с планом, а собранный .jsx несёт ровно их."""
    style = {"intro_hl_fill": DARK}
    plan, tp, _inp = _check(xml_subs, style=style, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert tp.anim_fx_fn, "функция introAnimFX не собрана"
    assert "function introAnimFX" in tp.anim_fx_fn
    assert "introAnimFX(Ll," in tp.line_anim and "introAnimFX(wl[wj2]" in tp.word_anim
    assert tp.hl_glow_fn, "свечение жёлтого хайлайта не собрано"
    assert tp.ly_decl and tp.lx_decl and tp.big_fn, "раскладка большой строки не собралась"
    assert "ADBE Tritone" in tp.anim_fx_fn, "тритон тёмного жёлтого не поставлен"

    jsx = _jsx(xml_subs, tmp_path, style=style, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert tp.anim_fx_fn in jsx, "introAnimFX не доехал до .jsx"
    assert tp.hl_glow_fn in jsx
    assert tp.ly_decl in jsx and tp.lx_decl in jsx
    assert tp.line_layout in jsx


def test_intro_tpl_deepglow_and_glow(xml_subs):
    """Deep Glow 2 на тёмном жёлтом глитче и его отсутствие на ярком и на строке со
    свечением: та же ветка, что была в scene_plan."""
    _p, dark, _i = _check(xml_subs, style={"intro_hl_fill": DARK}, intro=GLITCH_YELLOW,
                          glitch_glow="deepglow2")
    assert 'addFX(L,"PEDG2")' in dark.anim_fx_fn and "DG_MISS" in dark.anim_fx_fn
    assert 'col=="yellow" && fx!="glow"' in dark.anim_fx_fn
    _p, bright, _i = _check(xml_subs, style={"intro_hl_fill": BRIGHT},
                            intro=GLITCH_YELLOW, glitch_glow="deepglow2")
    assert "PEDG2" not in bright.anim_fx_fn, "яркий жёлтый взял Deep Glow (задание MK3)"
    _p, with_glow, _i = _check(xml_subs,
                               style={"intro_dg_with_glow": True, "intro_hl_fill": DARK},
                               intro=GLITCH_YELLOW, glitch_glow="deepglow2")
    assert 'col=="yellow"' in with_glow.anim_fx_fn
    assert 'fx!="glow"' not in with_glow.anim_fx_fn, "галка вернула прежнее условие"


def test_intro_tpl_back_rows_and_colors(xml_subs, tmp_path):
    """Задний план, акцент, свой цвет и счётчик: ветки раскладки и цветов уезжают в .jsx."""
    _p, tp, _inp = _check(xml_subs, style=STYLED_STYLE, intro=STYLED,
                          splits=STYLED_SPLITS)
    assert "BACK_STEP=" in tp.line_layout and "BACK_SCALE=" in tp.line_layout
    assert "INTRO_LY[gI]" in tp.line_layout
    assert tp.back_scale_fn and "introBackScale" in tp.back_scale_line
    assert tp.fill_params == ",cf" and tp.fill_call == ",ln.fill"
    assert tp.hlfill3_decl.startswith(", HL_FILL3=")
    assert 'col=="accent"?HL_FILL3' in tp.fill_pick
    assert tp.big_line_pos and tp.big_word_x, "куски большой строки не собрались"

    jsx = _jsx(xml_subs, tmp_path, style=STYLED_STYLE, intro=STYLED, splits=STYLED_SPLITS)
    assert tp.line_layout in jsx and tp.back_scale_fn in jsx
    assert tp.hlfill3_decl in jsx and tp.fill_pick in jsx


def test_intro_tpl_shadow_preset_and_auto(xml_subs):
    """Тень: пресет стиля ставится на каждое слово, без пресета — только глитчу и заднему
    плану (автотень)."""
    style = {"intro_shadow": True, "intro_shadow_op": 55, "intro_shadow_dir": 120,
             "intro_shadow_dist": 9, "intro_shadow_soft": 33,
             "back_shadow_op": 44, "back_shadow_soft": 22}
    _p, tp, _i = _check(xml_subs, style=style, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert "INTRO_SHADOW_OP=55" in tp.shadow_decl and "BACK_SHADOW_SOFT=22" in tp.shadow_decl
    assert tp.word_shadow_fn.startswith("\n        function introWordShadow")
    assert tp.word_shadow_line == " introWordShadow(Ll, ln.back);"
    assert tp.word_shadow_word == " introWordShadow(L2, ln.back);"
    # без пресета — автотень: объявление и функция есть (глитч в сборке), вызов условный
    _p, auto, _i = _check(xml_subs, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert "INTRO_SHADOW_OP=116" in auto.shadow_decl, "автотень взяла не числа стиля"
    assert auto.word_shadow_fn
    assert auto.word_shadow_line == ' if(ln.anim=="glitch"||ln.back) introWordShadow(Ll, ln.back);'
    assert auto.word_shadow_word == ' if(ln.anim=="glitch"||ln.back) introWordShadow(L2, ln.back);'
    # ни глитча, ни заднего плана, ни галки — тени нет вовсе (golden)
    _p, plain, _i = _check(xml_subs, intro=PLAIN)
    assert plain.shadow_decl == "" and plain.word_shadow_fn == ""
    assert plain.word_shadow_line == "" and plain.word_shadow_word == ""


def test_intro_tpl_front_and_above_roto(xml_subs):
    """Маршрутизация слоёв: видеовставка (front) и группа в нижней половине кадра при
    галке «интро над рото»."""
    _p, tp, _i = _check(xml_subs, style={"intro_roto_by_pos": True}, intro=GLITCH_BIG,
                        splits=GLITCH_BIG_SPLITS, inserts=INS_VIDEO)
    assert tp.front_decl.startswith("    var INTRO_FRONT=")
    assert "introFrontLayers" in tp.front_arr_decl and "introFrontLayers" in tp.front_route
    assert "moveToBeginning" in tp.front_raise
    _p, below, _i = _check(xml_subs, style={"intro_roto_by_pos": True}, intro=BELOW)
    assert below.above_roto_decl.startswith("    var INTRO_ABOVE_ROTO=")
    assert "introAboveRoto" in below.above_roto_route and "moveBefore" in below.above_roto_raise


def test_intro_tpl_comp_shadow_styled_and_default(xml_subs, tmp_path):
    """Тень прекомпа: дефолт — прежняя строка dropShadow(iL, 68) без функции, не-дефолт —
    introCompShadow по камере группы (задание B)."""
    _p, plain, _i = _check(xml_subs, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert plain.comp_shadow == "dropShadow(iL, 68);" and plain.comp_shadow_fn == ""
    style = {"intro_comp_shadow_fill": [1, 0, 0], "intro_comp_shadow_op": 50,
             "intro_comp_shadow2_fill": [0, 1, 0], "intro_comp_shadow2_op": 80}
    _p, tp, _i = _check(xml_subs, style=style, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert tp.comp_shadow == "introCompShadow(iL, INTRO_ON2[gI]);"
    assert "function introCompShadow" in tp.comp_shadow_fn
    assert "on2?[0,1,0]:[1,0,0]" in tp.comp_shadow_fn
    jsx = _jsx(xml_subs, tmp_path, style=style, intro=GLITCH_BIG, splits=GLITCH_BIG_SPLITS)
    assert tp.comp_shadow in jsx and tp.comp_shadow_fn in jsx


def test_intro_tpl_defaults_are_golden(xml_subs):
    """Дефолты: ни ручек, ни эффектов — подстановки пустые либо ровно прежние (golden)."""
    _p, tp, _i = _check(xml_subs, intro=PLAIN)
    for text in (tp.hlfill3_decl, tp.fill_decl, tp.shadow_decl, tp.word_shadow_fn,
                 tp.ly_decl, tp.lx_decl, tp.big_fn, tp.big_qi_vars, tp.big_line_pos,
                 tp.big_word_x, tp.front_decl, tp.front_arr_decl, tp.front_raise,
                 tp.above_roto_decl, tp.above_roto_arr_decl, tp.above_roto_route,
                 tp.above_roto_raise, tp.anim_fx_fn, tp.hl_glow_fn, tp.group_flags):
        assert text == "", "при дефолтах подстановка не пуста: %r" % text
    assert tp.fill_params == "" and tp.fill_call == ""
    assert tp.fill_pick == '(col=="yellow"?HL_FILL:[1,1,1])'
    assert tp.front_route == "introLayers.push(iL);"
    assert "cY=H/2 - (nL-1)/2*LINE_STEP" in tp.line_layout
    assert tp.line_anim and tp.word_anim and tp.comp_glow
    assert tp.word_shadow_line == "" and tp.word_shadow_word == ""
    assert tp.comp_shadow == "dropShadow(iL, 68);" and tp.comp_shadow_fn == ""


def test_intro_tpl_without_intro(xml_subs):
    """Ролик без интро: подстановок нет вовсе, модуль ничего не выдумывает."""
    _p, tp, _i = _check(xml_subs)
    for field, _key in FIELDS:
        text = getattr(tp, field)
        if field in ("comp_shadow", "line_anim", "word_anim", "comp_glow", "line_layout",
                     "word_shadow_line", "word_shadow_word", "fill_pick", "back_scale_line_w",
                     "front_route"):
            continue                            # эти подстановки есть всегда (golden-текст)
        assert text == "", "без интро подстановка не пуста: %s=%r" % (field, text)
    assert tp.ly_decl == "" and tp.lx_decl == "" and tp.big_fn == ""


def test_intro_tpl_keeps_inputs_intact(xml_subs):
    """Входы не правятся «по месту»: группы, стиль и результат plan_intro после вызова те же."""
    _plan, inp = _prepare(xml_subs, style=STYLED_STYLE, intro=STYLED, splits=STYLED_SPLITS)
    snapshot = copy.deepcopy(inp)
    plan_intro_tpl(inp)
    assert inp == snapshot
