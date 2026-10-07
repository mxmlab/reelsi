# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Откреплённое интро подгоняется под ширину кадра.

Пока интро висело на нуле Камеры 1, его увеличивал зум камеры (150–180 %), а автофит
(`_intro_fit_ds`) только УЖИМАЛ длинную строку до INTRO_FIT_W = 0.92 ширины кадра.
Сняв галку «интро едет с камерой», владелец получил мелкое интро:
увеличивать его стало некому — зума у откреплённой группы нет, а автофит вверх не умел.

Теперь у откреплённого интро (`intro_cam = False`) автофит подгоняет группу под ширину
в ОБЕ стороны: ds = fit, где ширина цели = `intro_fit_w`/100 ширины кадра (ручка «Интро
по ширине, %», дефолт 92, группа «Transform»). Привязанное — как было: только ужатие по
константе INTRO_FIT_W с учётом зума камеры. Группа с ручным масштабом (gs != 100) не
трогается ни там, ни там (рука сильнее автофита). Ширина группы — как
раньше: максимум ширины строк, у «большого слева» — весь блок (total).
Опускание блока под INTRO_SAFE_TOP у откреплённого интро считается от ФАКТИЧЕСКОГО ds:
растянутая группа выше, и от неужатого масштаба (как у привязанного) её верх уезжал
за кадр. Сдвиг считается ещё и по фактическому габариту блока
(`intro_block_span`) — тест 6 сверяется тем же числом.

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
from core.xml2ae.layout import (INTRO_BASE_Y, INTRO_FIT_W, INTRO_SAFE_TOP,  # noqa: E402
                                INTRO_SCALE, _intro_i_dy, intro_block_span,
                                intro_line_sizes)
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
    Теперь ds = fit ≈ 733, и видимая ширина — ровно доля кадра из ручки. Потолок
    увеличения (дефолт 250) здесь поднят намеренно: он режет именно такой
    рост, а проверяется подгонка MI — своё число потолка стережёт test_intro_fit_safe.
    """
    g = _group(xml_subs, {"intro_cam": False, "intro_fit_max": 1000})
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
    """3. Галка «интро едет с камерой» включена — прежнее правило: только ужатие.

    Короткая строка остаётся на ds = 100 (вверх автофит не тянет: увеличивает зум
    камеры), длинная ужимается по доле ширины СВОЕЙ камеры (дефолтный отступ 4 % — те же
    92 %) с учётом зума 160 %. Отдельная ручка ширины есть теперь и у привязанного интро:
    доля у него та же (`intro_margin`, «Отступ от краёв»), а не константа.
    """
    short = _group(xml_subs, {}, cam1_scale=ZOOM)
    long_ = _group(xml_subs, {}, intro=LONG, cam1_scale=ZOOM)
    detached = _group(xml_subs, {"intro_cam": False}, intro=LONG)

    assert short["ds"] == 100, "привязанное интро растянулось — автофит полез вверх"
    assert "ds" not in short["lines"][0], "дефолтный ds=100 уехал в данные строки"

    expected = 100.0 * W * INTRO_FIT_W / ((FSIZE * 10) * (INTRO_SCALE / 100.0) * 1.6)
    assert long_["ds"] == pytest.approx(expected, rel=1e-9), \
        "привязанное ужимается не по доле ширины своей камеры и не по зуму"
    assert long_["ds"] < detached["ds"], \
        "привязанное обязано ужиматься зумом сильнее откреплённого"

    # Свой отступ у привязанного интро работает: 20 % с края — доля 0.60 ширины кадра.
    knob = _group(xml_subs, {"intro_margin": 20.0}, intro=LONG, cam1_scale=ZOOM)
    assert knob["ds"] < long_["ds"], "ручка «Отступ от краёв» не тронула привязанное интро"
    assert _visible(knob["ds"], FSIZE * 10, z=ZOOM[0][1]) == pytest.approx(W * 0.60, rel=1e-3)


# ==================================================== 4. рука сильнее автофита

def test_manual_group_scale_beats_the_fit_both_ways(wide_font, xml_subs):
    """4. gs != 100 — группу масштабировали руками, автофит её не трогает.

    Ни вверх (короткая строка с gs=125 остаётся 125, а не 733), ни вниз (длинная
    с gs=30 остаётся 30, а не 73): рука сильнее автофита — то же правило, что было
    у привязанного интро.
    """
    for intro, gs in ((SHORT, 125), (LONG, 30)):
        g = _group(xml_subs, {"intro_cam": False},
                   intro=[dict(intro[0], gs=gs)])
        assert g["ds"] == gs, f"автофит тронул ручной масштаб gs={gs} (ds={g['ds']})"
        assert g["lines"][0]["ds"] == gs, "ручной масштаб не доехал до головной строки"

    attached = _group(xml_subs, {}, intro=[dict(SHORT[0], gs=125)], cam1_scale=ZOOM)
    assert attached["ds"] == 125


# ==================================================== 5. ручка отступа и сторож

def test_intro_margin_knob_sets_the_frame_share(wide_font, xml_subs):
    """5а. Отступ 10 % с края — ширина цели 0.80·W, и вверх, и вниз.

    Доля ширины = 1 − 2·margin/100 (правило ОДНО на оба режима). Потолок увеличения
    поднят: у короткой строки подгонка выше дефолтных 250, и с ними ручка меняла бы не
    ширину, а только упор в потолок.
    """
    hi = {"intro_cam": False, "intro_fit_max": 1000}
    short = _group(xml_subs, dict(hi, intro_margin=10.0))
    long_ = _group(xml_subs, dict(hi, intro_margin=10.0), intro=LONG)

    assert _visible(short["ds"], FSIZE) == pytest.approx(W * 0.80, rel=0.01)
    assert short["ds"] > 100
    assert _visible(long_["ds"], FSIZE * 10) == pytest.approx(W * 0.80, rel=0.01)
    assert long_["ds"] < 100
    # дефолт (4 % — те же 92 %) : у той же группы ds больше — ручка реально рулит шириной
    assert short["ds"] < _group(xml_subs, hi)["ds"]


def test_intro_margin_knob_lives_in_schema_base_and_assembly(wide_font, xml_subs, tmp_path):
    """5б. Сторож «каждая ручка» (test_r11_li_every_knob) и счётчики схемы.

    Поле обязано быть в схеме (num, 0…30, шаг 1, в группе «Камера 1»), дефолт — в
    styles.BASE, перевод — в en.json, а изменение ручки — менять собранный .jsx: иначе
    ручка мертва и молчит. Старой общей ручки `intro_fit_w` в BASE и схеме больше нет.
    """
    field = watcher.schema_field("intro_margin")
    assert field, "в схеме нет ручки intro_margin"
    assert field.get("ctl") == "num", "intro_margin перестала быть числом"
    assert (field.get("min"), field.get("max"), field.get("step")) == (0, 30, 1)
    assert field.get("label") == "Отступ от краёв, % (камера 1)"
    assert "show_if" not in field, "ручка снова спрятана от привязанного интро"
    assert "intro_margin" in watcher.schema_keys(), \
        "ручка не попадает в счётчики схемы (test_style_schema)"
    assert watcher.schema_field("intro_margin2") is not None, "нет ручки камеры 2"
    assert watcher.schema_field("intro_fit_w") is None, "старая ручка осталась в схеме"

    assert styles.BASE.get("intro_margin") == 4.0, "дефолт intro_margin в styles.BASE не 4"
    assert styles.resolve(None).get("intro_margin") == 4.0
    assert "intro_fit_w" not in styles.BASE, "старая ручка осталась в BASE"

    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))
    assert en.get(field["label"]), "подпись ручки осталась без перевода"
    assert en.get(field["tip"]), "тултип ручки остался без перевода"

    ref = _build(xml_subs, tmp_path, {"intro_cam": False, "intro_fit_max": 1000},
                 name="fit92.jsx")
    mod = _build(xml_subs, tmp_path, {"intro_cam": False, "intro_margin": 10.0,
                                      "intro_fit_max": 1000},
                 name="fit80.jsx")
    assert ref != mod, "ручка intro_margin не изменила собранный .jsx"
    ds92 = _grp_ds(ref)
    ds80 = _grp_ds(mod)
    assert ds92 > ds80 > 0, f"ds не поехал за ручкой: {ds92} -> {ds80}"
    # привязанное интро читает СВОЙ отступ, но не потолок увеличения: сборки при
    # одинаковом отступе и разном потолке совпадают
    on92 = _build(xml_subs, tmp_path, {}, name="on92.jsx")
    on92b = _build(xml_subs, tmp_path, {"intro_fit_max": 1000}, name="on92b.jsx")
    assert on92 == on92b, "потолок увеличения изменил привязанное интро"


def _grp_ds(jsx):
    """ds головной строки первой группы из .jsx (то, что читает шаблон: GRP[0].ds)."""
    groups = json.loads(re.search(r"var INTRO_GROUPS=(\[.*?\]);", jsx).group(1))
    return groups[0][0]["ds"]


# ==================================================== 6. опускание под безопасную зону

def test_grown_intro_is_dropped_under_the_safe_top(wide_font, xml_subs, tmp_path):
    """6. Верх растянутого блока садится ровно на SAFE_TOP — по габариту блока.

    Блок интро стоит центром в H/2 − INTRO_BASE_Y + iDy, а его половина высоты растёт
    вместе с масштабом прекомпа: при ds = 250 (96.8·ds/100 = 242 %) верх блока был бы
    на 200 px, то есть ВЫШЕ безопасной линии 285. Опускание считает сборка ПОСЛЕ
    автофита и по фактическому габариту блока (`intro_block_span`):
    `_intro_i_dy` знает только n/2·LINE_STEP и верх недооценивает. Проверка берёт то же
    число, что и сборка, — второй копии формулы в тесте нет.
    """
    plan = _scene(xml_subs, {"intro_cam": False})
    jsx = _build(xml_subs, tmp_path, {"intro_cam": False}, name="safe.jsx")
    ds = _grp_ds(jsx)
    idy = json.loads(re.search(r"var INTRO_IDY=(\[.*?\]);", jsx).group(1))[0]
    p = plan["intro"][0]

    assert ds > 100, "предпосылка теста: короткая строка обязана растянуться"
    assert p["y"] == round(-INTRO_BASE_Y + idy, 2), \
        "сдвиг не доехал одним числом до плана (y) и до .jsx (INTRO_IDY)"
    sizes = intro_line_sizes(p["lines"], plan["intro_fsize"], plan["back_scale"], p.get("lk"))
    top, _bot = intro_block_span(p["ys"], sizes, plan["h"], ds=ds, g=plan["intro_scale"],
                                 y=p["y"], dy=p["dy"], zoom=100.0, intro_cam=False,
                                 fonts=p["fonts"])
    assert top == pytest.approx(INTRO_SAFE_TOP, abs=0.01), \
        "верх растянутого блока не сел на INTRO_SAFE_TOP"
    assert top - idy < INTRO_SAFE_TOP, \
        "верх блока и без опускания под линией — проверка ничего не значит"

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
