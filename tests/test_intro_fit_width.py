# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Откреплённое интро подгоняется под ширину кадра (задание MI).

Пока интро висело на нуле Камеры 1, его увеличивал зум камеры (150–180 %), а автофит
(`_intro_fit_ds`) только УЖИМАЛ длинную строку до INTRO_FIT_W = 0.92 ширины кадра.
Сняв галку «интро едет с камерой» (задание ZM), владелец получил мелкое интро:
увеличивать его стало некому — зума у откреплённой группы нет, а автофит вверх не умел.

Теперь у откреплённого интро (`intro_cam = False`) автофит подгоняет группу под ширину
в ОБЕ стороны: ds = fit, где ширина цели = `intro_fit_w`/100 ширины кадра (ручка «Интро
по ширине, %», дефолт 92, группа «Transform»). Привязанное — как было: только ужатие по
константе INTRO_FIT_W с учётом зума камеры. Группа с ручным масштабом (gs != 100) не
трогается ни там, ни там (задание CF: рука сильнее автофита). Ширина группы — как
раньше: максимум ширины строк, у «большого слева» — весь блок (total, задание ZY).
Опускание блока под INTRO_SAFE_TOP у откреплённого интро считается от ФАКТИЧЕСКОГО ds:
растянутая группа выше, и от неужатого масштаба (как у привязанного) её верх уезжал
за кадр.

Здесь:
  1. откреплённое + короткая строка: ds > 100, ширина строки·масштаб ≈ 0.92·W;
  2. откреплённое + длинная строка: ds < 100, та же ширина;
  3. привязанное — как на main: короткая строка так и стоит на 100, длинная ужимается
     зумом камеры, ручка «Интро по ширине» его не трогает;
  4. gs != 100 — рука сильнее автофита: ds не трогается ни вверх, ни вниз;
  5. ручка intro_fit_w = 80 → 0.80·W; сторож «каждая ручка» (схема, BASE, перевод en,
     счётчики схемы) и сборка: ручка меняет готовый ds в .jsx;
  6. iDy от фактического ds: верх растянутого блока садится ровно на SAFE_TOP;
  7. превью своей копии формулы не заводит: масштаб группы берётся из плана (ds).

Метрика шрифта подменена — буква шириной ровно в кегль (как в test_intro_detach):
числа теста это формулы сборки, а не то, какие шрифты стоят на машине.
"""
import gzip
import io
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, styles, xml2ae  # noqa: E402
from core.xml2ae.layout import (INTRO_BASE_Y, INTRO_FIT_W, INTRO_LINE_STEP,  # noqa: E402
                                INTRO_SAFE_TOP, INTRO_SCALE, _intro_i_dy)
import test_style_keys_in_ui as watcher  # noqa: E402

JS_REL = ("static", "app", "85-inserts-view.js")
T_CAM1 = 1.0
W, H = 1080.0, 1920.0           # кадр фикстуры timeline_subs.xml.gz
FSIZE = 140                     # кегль интро = max(60, int(W*0.13))
SHORT = [dict(words=["A"], color="white", times=[T_CAM1])]           # 140 px строки
LONG = [dict(words=["A" * 10], color="white", times=[T_CAM1])]       # 1400 px строки
ZOOM = [(0, 160), (300, 160)]   # зум Камеры 1 на окне группы


@pytest.fixture()
def xml_subs(tmp_path):
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


@pytest.fixture()
def wide_font(monkeypatch):
    """Ширина текста известна и от системных шрифтов не зависит: буква = ровно кегль."""
    monkeypatch.setattr(fonts, "text_width", lambda ps, text, size: float(size) * len(text))


def _scene(xml, style=None, intro=None, cam1_scale=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=SHORT if intro is None else intro,
                             intro_splits=[], style=dict(style or {}),
                             cam1_scale=cam1_scale, emit=lambda *a, **k: None)


def _build(xml, tmp_path, style=None, intro=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=SHORT if intro is None else intro,
                                   intro_splits=[], style=dict(style or {}),
                                   intro_mode="word", disclaimer="",
                                   intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _group(xml, style=None, intro=None, cam1_scale=None):
    """Группа интро из плана: ds готовый, второй копии расчёта в тесте нет."""
    plan = _scene(xml, style=style, intro=intro, cam1_scale=cam1_scale)
    assert len(plan["intro"]) == 1, "предпосылка теста: одна группа интро"
    return plan["intro"][0]


def _visible(ds, linew, z=100.0, G=1.0):
    """Видимая ширина строки: linew·(iSc/100)·G·Z, iSc = INTRO_SCALE·ds/100 (автофит BP)."""
    return linew * (INTRO_SCALE / 100.0) * (ds / 100.0) * G * (z / 100.0)


# ============================================ 1–2. откреплённое: подгонка в обе стороны

def test_detached_short_line_stretches_to_the_frame_width(wide_font, xml_subs):
    """1. Короткая строка у откреплённого интро растягивается до 0.92·W (было мелко).

    Раньше автофит умел только min(ds, fit): строка «A» шириной 140 px так и осталась
    бы на ds = 100 (96.8 % кадра по высоте прекомпа, а по ширине — 12.5 % кадра).
    Теперь ds = fit ≈ 733, и видимая ширина — ровно доля кадра из ручки.
    """
    g = _group(xml_subs, {"intro_cam": False})
    ds = g["ds"]

    assert ds > 100, f"короткая строка не растянулась: ds={ds}"
    assert _visible(ds, FSIZE) == pytest.approx(W * INTRO_FIT_W, rel=0.01), \
        "ширина растянутой строки не сошлась с долей ширины кадра"
    # ds головной строки = ds группы: шаблон читает GRP[0].ds, превью — plan.intro[].ds
    assert g["lines"][0]["ds"] == round(ds, 4), "в строку уехал не тот ds"


def test_detached_long_line_shrinks_to_the_frame_width(wide_font, xml_subs):
    """2. Длинная строка у откреплённого интро по-прежнему ужимается — до той же ширины.

    Строка «A»×10 = 1400 px шире кадра: ds < 100, и видимая ширина та же доля кадра.
    Подгонка в обе стороны — одна формула, а не две ветки с разными числами.
    """
    g = _group(xml_subs, {"intro_cam": False}, intro=LONG)
    ds = g["ds"]

    assert ds < 100, f"длинная строка не ужалась: ds={ds}"
    assert _visible(ds, FSIZE * 10) == pytest.approx(W * INTRO_FIT_W, rel=0.01)
    assert g["lines"][0]["ds"] == round(ds, 4)


# ==================================================== 3. привязанное — как на main

def test_attached_intro_still_only_shrinks(wide_font, xml_subs):
    """3. Галка «интро едет с камерой» включена — поведение прежнее (как на main).

    Короткая строка остаётся на ds = 100 (вверх автофит не тянет: увеличивает зум
    камеры), длинная ужимается ровно по прежней формуле с INTRO_FIT_W и зумом 160 %,
    а ручка «Интро по ширине» на привязанное не влияет вовсе.
    """
    short = _group(xml_subs, {}, cam1_scale=ZOOM)
    long_ = _group(xml_subs, {}, intro=LONG, cam1_scale=ZOOM)
    detached = _group(xml_subs, {"intro_cam": False}, intro=LONG)

    assert short["ds"] == 100, "привязанное интро растянулось — автофит полез вверх"
    assert "ds" not in short["lines"][0], "дефолтный ds=100 уехал в данные строки"

    expected = 100.0 * W * INTRO_FIT_W / ((FSIZE * 10) * (INTRO_SCALE / 100.0) * 1.6)
    assert long_["ds"] == pytest.approx(expected, rel=1e-9), \
        "привязанное ужимается не по прежней формуле (INTRO_FIT_W + зум камеры)"
    assert long_["ds"] < detached["ds"], \
        "привязанное обязано ужиматься зумом сильнее откреплённого"

    knob = _group(xml_subs, {"intro_fit_w": 80}, intro=LONG, cam1_scale=ZOOM)
    assert knob["ds"] == long_["ds"], "ручка «Интро по ширине» тронула привязанное интро"


# ==================================================== 4. рука сильнее автофита (CF)

def test_manual_group_scale_beats_the_fit_both_ways(wide_font, xml_subs):
    """4. gs != 100 — группу масштабировали руками, автофит её не трогает.

    Ни вверх (короткая строка с gs=125 остаётся 125, а не 733), ни вниз (длинная
    с gs=30 остаётся 30, а не 73): рука сильнее автофита — то же правило, что было
    у привязанного интро (задание CF).
    """
    for intro, gs in ((SHORT, 125), (LONG, 30)):
        g = _group(xml_subs, {"intro_cam": False},
                   intro=[dict(intro[0], gs=gs)])
        assert g["ds"] == gs, f"автофит тронул ручной масштаб gs={gs} (ds={g['ds']})"
        assert g["lines"][0]["ds"] == gs, "ручной масштаб не доехал до головной строки"

    attached = _group(xml_subs, {}, intro=[dict(SHORT[0], gs=125)], cam1_scale=ZOOM)
    assert attached["ds"] == 125


# ==================================================== 5. ручка intro_fit_w и сторож

def test_intro_fit_w_knob_sets_the_frame_share(wide_font, xml_subs):
    """5а. intro_fit_w = 80 — ширина цели 0.80·W, и вверх, и вниз."""
    short = _group(xml_subs, {"intro_cam": False, "intro_fit_w": 80})
    long_ = _group(xml_subs, {"intro_cam": False, "intro_fit_w": 80}, intro=LONG)

    assert _visible(short["ds"], FSIZE) == pytest.approx(W * 0.80, rel=0.01)
    assert short["ds"] > 100
    assert _visible(long_["ds"], FSIZE * 10) == pytest.approx(W * 0.80, rel=0.01)
    assert long_["ds"] < 100
    # дефолт 92: у той же группы ds больше — ручка реально рулит шириной
    assert short["ds"] < _group(xml_subs, {"intro_cam": False})["ds"]


def test_intro_fit_w_knob_lives_in_schema_base_and_assembly(wide_font, xml_subs, tmp_path):
    """5б. Сторож «каждая ручка» (test_r11_li_every_knob) и счётчики схемы.

    Поле обязано быть в схеме (num, 50…100, шаг 1, рядом с intro_scale в «Transform»),
    дефолт — в styles.BASE, перевод — в en.json, а снятая галка «интро едет с камерой»
    вместе с новой ручкой обязана менять собранный .jsx: иначе ручка мертва и молчит.
    """
    field = watcher.schema_field("intro_fit_w")
    assert field, "в схеме нет ручки intro_fit_w"
    assert field.get("ctl") == "num", "intro_fit_w перестала быть числом"
    assert (field.get("min"), field.get("max"), field.get("step")) == (50, 100, 1)
    assert field.get("label") == "Интро по ширине, %"
    assert field.get("tip") == ("открепленное от камеры интро подгоняется под эту долю "
                                "ширины кадра (увеличивается и ужимается); группы с ручным "
                                "масштабом не трогаются")
    assert "intro_fit_w" in watcher.schema_keys(), \
        "ручка не попадает в счётчики схемы (test_style_schema)"

    assert styles.BASE.get("intro_fit_w") == 92.0, "дефолт intro_fit_w в styles.BASE не 92"
    assert styles.resolve(None).get("intro_fit_w") == 92.0

    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))
    assert en.get(field["label"]) == "Intro width, %"
    assert en.get(field["tip"]), "тултип ручки остался без перевода"

    ref = _build(xml_subs, tmp_path, {"intro_cam": False}, name="fit92.jsx")
    mod = _build(xml_subs, tmp_path, {"intro_cam": False, "intro_fit_w": 80},
                 name="fit80.jsx")
    assert ref != mod, "ручка intro_fit_w не изменила собранный .jsx"
    ds92 = _grp_ds(ref)
    ds80 = _grp_ds(mod)
    assert ds92 > ds80 > 0, f"ds не поехал за ручкой: {ds92} -> {ds80}"
    # привязанное интро ручку не читает: сборки при gs по умолчанию совпадают
    on92 = _build(xml_subs, tmp_path, {}, name="on92.jsx")
    on80 = _build(xml_subs, tmp_path, {"intro_fit_w": 80}, name="on80.jsx")
    assert on92 == on80, "ручка «Интро по ширине» изменила привязанное интро"


def _grp_ds(jsx):
    """ds головной строки первой группы из .jsx (то, что читает шаблон: GRP[0].ds)."""
    groups = json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))
    return groups[0][0]["ds"]


# ==================================================== 6. опускание под безопасную зону

def test_grown_intro_is_dropped_under_the_safe_top(wide_font, xml_subs, tmp_path):
    """6. iDy считается от ФАКТИЧЕСКОГО ds — верх растянутого блока садится на SAFE_TOP.

    Блок интро стоит центром в H/2 − INTRO_BASE_Y + iDy, а его половина высоты
    растёт вместе с масштабом прекомпа: при ds ≈ 733 (96.8·ds/100 ≈ 710 %) верх
    уезжает на 128 px ЗА кадр. От неужатого gs=100 (так считает привязанное интро)
    iDy был бы нулевым — проверка не на пустом месте.
    """
    jsx = _build(xml_subs, tmp_path, {"intro_cam": False}, name="safe.jsx")
    ds = _grp_ds(jsx)
    idy = json.loads(re.search(r"var INTRO_IDY=(\[.*?\]);", jsx).group(1))[0]

    assert ds > 100, "предпосылка теста: короткая строка обязана растянуться"
    top = H / 2 - INTRO_BASE_Y - 0.5 * INTRO_LINE_STEP * (INTRO_SCALE * ds / 100.0 / 100.0) + idy
    assert top == pytest.approx(INTRO_SAFE_TOP, abs=0.01), \
        "верх растянутого блока не сел на INTRO_SAFE_TOP"
    assert top - idy < 0, "верх блока и без опускания в кадре — проверка ничего не значит"

    assert _intro_i_dy(H, 1, 100.0) == 0.0, \
        "от неужатого масштаба опускание не срабатывает — именно эту дыру и закрыли"
    assert _intro_i_dy(H, 1, ds) > 0


# ==================================================== 7. превью считает по плану

def test_preview_takes_ds_from_the_plan():
    """7. Превью своей копии формулы не держит: масштаб группы — ds из плана.

    Подгонка по ширине живёт в Python (автофит BP/MI), превью обязано брать готовое
    число: вторая копия формулы в JS разошлась бы с .jsx молча.
    """
    src = io.open(os.path.join(ROOT, *JS_REL), encoding="utf-8").read()
    m = re.search(r"function\s+ipvIntroPos\s*\(", src)
    assert m, "в предпросмотре нет ipvIntroPos"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
    body = src[m.start():j + 1]
    assert "g.ds" in body, "превью перестало брать масштаб группы (ds) из плана"
    assert "intro_fit_w" not in src and "INTRO_FIT_W" not in src, \
        "в предпросмотре завелась своя копия формулы подгонки по ширине"
