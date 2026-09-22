# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Откреплённое интро: ручка видна по делу, потолок увеличения, безопасная зона (задание MO).

Три дефекта, найденные внешним ревью 2026-09-19 и подтверждённые архитектором:

1. «Интро по ширине, %» (`intro_fit_w`) у ПРИВЯЗАННОГО интро мёртвая: замер — `intro_cam=True`,
   ручка 92 и 70 дают один и тот же ds = 100 (ширину привязанному задаёт зум Камеры 1).
   Теперь ручка показывается только у откреплённого — `show_if` на `intro_cam == False`.
2. Блок откреплённого интро стоял ВЫШЕ безопасной линии: стиль по умолчанию, `intro_cam=False`,
   группы «большое слева + 2 коротких», «большое + 3 строки», «одно слово» — верх блока
   164–253 px при `INTRO_SAFE_TOP = 285`, якорь center и first. Причина: опускание под
   безопасную зону (`_intro_i_dy`) считает половину высоты блока как n/2·LINE_STEP — про
   капитель строки и «большое слева» (lk) оно не знает, а автофит MI группу УВЕЛИЧИВАЕТ.
   Теперь сдвиг считается ПОСЛЕ автофита по фактическому верху блока (`layout.intro_block_span`,
   задание MH) и едет в тот же iDy/y, что уходят в .jsx (INTRO_IDY) и в план (y) — превью
   покажет то же.
3. Потолка увеличения не было: одно короткое слово («ДА») раздувалось до 667–819 %
   (≈780 px высотой). Новая ручка `intro_fit_max` — «Потолок увеличения интро, %», дефолт 250,
   100…1000, шаг 10, тот же `show_if`: `ds = min(fit, потолок)`. Режется только увеличение —
   ужатие длинной строки потолком не ограничивается.

Здесь:
  1. схема: `show_if` на `intro_cam == False` у обеих ручек, потолок стоит сразу за долей ширины;
  2. потолок: дефолт 250, `intro_fit_max=400` → 400, ужатие ниже 100 потолком не режется;
  3. безопасная зона: три группы из «Зачем» п.2 × оба якоря — верх блока по `intro_block_span`
     не выше `INTRO_SAFE_TOP` (±0.5), а по старому расчёту был бы выше (проверка не на пустом
     месте); сдвиг доезжает до .jsx тем же числом (INTRO_IDY) и до плана (y);
  4. `intro_cam=True` — как на main: ручки откреплённого интро сборку не меняют, ds не
     растягивается, iDy считается прежней формулой (golden стережёт test_geometry_python);
  5. сторож «каждая ручка» (`intro_fit_max` в схеме, BASE, переводе en и в сборке), счётчики
     схемы — в test_style_schema.

Метрики шрифта заданы формулами, а не тем, какие шрифты стоят на машине: буква шириной
0.5 кегля, капитель 0.72 кегля, выносные 0.24 (те же запасные числа, что у раскладки без
файла шрифта). Числа теста — формулы сборки, а не машины.
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

T_CAM1 = 1.0
W, H = 1080.0, 1920.0           # кадр фикстуры timeline_subs.xml.gz
FSIZE = 140                     # кегль интро = max(60, int(W*0.13))
LETTER = 0.5                    # ширина буквы в кеглях (метрика фикстуры metrics)
DEFAULT_MAX = 250.0             # дефолт ручки intro_fit_max
ZOOM = [(0, 160), (300, 160)]   # зум Камеры 1 на окне группы (привязанное интро)

# Группы из «Зачем» п.2: одно короткое слово и два «большое слева».
ONE = [dict(words=["ДА"], color="white", times=[T_CAM1])]
BIG2 = [
    dict(words=["8"], color="white", times=[T_CAM1], big=True),
    dict(words=["КИЛО"], color="white", times=[T_CAM1 + 0.3]),
    dict(words=["ЗА МЕСЯЦ"], color="white", times=[T_CAM1 + 0.6]),
]
BIG3 = BIG2 + [dict(words=["РОВНО"], color="white", times=[T_CAM1 + 0.9])]
# Три обычные строки — для сверки с прежней формулой опускания (iDy = 78.1094).
THREE = [dict(words=["СТРОКА"], color="white", times=[T_CAM1 + i * 0.3]) for i in range(3)]
# Строка заведомо шире кадра: 30 букв по 0.5 кегля = 2100 px при W = 1080.
LONG = [dict(words=["Д" * 30], color="white", times=[T_CAM1])]


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
def metrics(monkeypatch):
    """Метрики шрифта — формулы: буква 0.5 кегля, высот из файла нет (капитель 0.72 кегля,
    выносные 0.24 — запасная ветка раскладки). Числа не зависят от шрифтов машины."""
    monkeypatch.setattr(fonts, "text_width",
                        lambda ps, text, size: LETTER * float(size) * len(text))
    monkeypatch.setattr(fonts, "ink_extent", lambda ps, text, size: None)


def _scene(xml, style=None, intro=None, cam1_scale=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=ONE if intro is None else intro,
                             intro_splits=[], style=dict(style or {}),
                             cam1_scale=cam1_scale, emit=lambda *a, **k: None)


def _build(xml, tmp_path, style=None, intro=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name),
                                   intro=ONE if intro is None else intro,
                                   intro_splits=[], style=dict(style or {}),
                                   intro_mode="word", disclaimer="",
                                   intro_riser=False, emit=lambda *a, **k: None)
    return open(path, encoding="utf-8-sig").read()


def _group(xml, style=None, intro=None, cam1_scale=None):
    """Группа интро из плана: ds готовый, второй копии расчёта в тесте нет."""
    plan = _scene(xml, style=style, intro=intro, cam1_scale=cam1_scale)
    assert len(plan["intro"]) == 1, "предпосылка теста: одна группа интро"
    return plan["intro"][0]


def _width(text):
    """Ширина строки по метрике фикстуры metrics, px."""
    return LETTER * FSIZE * len(text)


def _fit_free(width_px, fit_w=INTRO_FIT_W):
    """Подгонка по ширине БЕЗ потолка: 100·W·доля/(lineW·96.8/100) — формула автофита BP."""
    return 100.0 * W * fit_w / (width_px * (INTRO_SCALE / 100.0))


def _top(plan):
    """Верх блока группы в кадре — той же дверью, что у сборки (intro_block_span, MH)."""
    p = plan["intro"][0]
    sizes = intro_line_sizes(p["lines"], plan["intro_fsize"], plan["back_scale"], p.get("lk"))
    return intro_block_span(p["ys"], sizes, plan["h"], ds=p["ds"], g=plan["intro_scale"],
                            y=p["y"], dy=p["dy"], zoom=100.0, intro_cam=False,
                            fonts=p["fonts"])[0]


def _old_top(plan, anchor):
    """Верх блока, каким он был ДО задания MO: опускание считала только _intro_i_dy
    (половина блока = n/2·LINE_STEP, ни капители, ни lk). Проверка «не на пустом месте»."""
    p = plan["intro"][0]
    n_stack = len(p["ys"]) - (1 if "lk" in p else 0)     # большая строка шаг не занимает
    idy = _intro_i_dy(plan["h"], 1 if anchor == "first" else n_stack, p["ds"])
    sizes = intro_line_sizes(p["lines"], plan["intro_fsize"], plan["back_scale"], p.get("lk"))
    return intro_block_span(p["ys"], sizes, plan["h"], ds=p["ds"], g=plan["intro_scale"],
                            y=round(-INTRO_BASE_Y + idy, 2), dy=p["dy"], zoom=100.0,
                            intro_cam=False, fonts=p["fonts"])[0]


def _jsx_var(jsx, name):
    return json.loads(re.search(r"var %s=(\[.*?\]);" % name, jsx).group(1))


def _head_ds(jsx):
    """ds головной строки первой группы из .jsx (то, что читает шаблон: GRP[0].ds)."""
    return _jsx_var(jsx, "INTRO_GROUPS")[0][0]["ds"]


# ======================================== 1. ручки видны только у откреплённого интро

def test_fit_knobs_are_shown_only_for_the_detached_intro():
    """1. У `intro_fit_w` и `intro_fit_max` — `show_if` на `intro_cam == False`.

    У привязанного интро ширину задаёт зум Камеры 1 (замер MI: 92 и 70 дают один и тот же
    ds), поэтому обе ручки там мёртвые и в панели им делать нечего. Механизм show_if панель
    уже читает (updateStyleVisibility, static/app/94-stylepanel.js) — второй копии условия
    в JS не заводится.
    """
    for key in ("intro_fit_w", "intro_fit_max"):
        field = watcher.schema_field(key)
        assert field, f"в схеме нет ручки {key}"
        assert field.get("show_if") == {"key": "intro_cam", "eq": False}, (
            f"{key}: ручка показывается и у привязанного интро (show_if не тот)")

    keys = [it["key"] for kind, it in watcher.schema_items()
            if kind == "field" and it.get("key")]
    assert keys.index("intro_fit_max") == keys.index("intro_fit_w") + 1, \
        "потолок увеличения стоит не рядом с ручкой «Интро по ширине»"


# ==================================================== 2. потолок увеличения (ручка)

def test_fit_max_caps_the_growth_and_does_not_touch_the_shrink(metrics, xml_subs):
    """2. `intro_cam=False`, одно слово «ДА»: дефолт 250, `intro_fit_max=400` → 400.

    Без потолка подгонка ушла бы на 733 % (ширина «ДА» = 140 px) — предпосылка проверяется
    тем же числом. Ужатие потолком не режется: длинная строка садится на 48.9 %, хотя
    потолок стоит на 100.
    """
    free = _fit_free(_width("ДА"))
    assert free > 400 > DEFAULT_MAX, f"предпосылка теста: подгонка {free} ниже потолков"

    capped = _group(xml_subs, {"intro_cam": False})
    raised = _group(xml_subs, {"intro_cam": False, "intro_fit_max": 400})
    assert capped["ds"] == pytest.approx(DEFAULT_MAX), "дефолтный потолок не 250"
    assert raised["ds"] == pytest.approx(400.0), "ручка intro_fit_max не подняла потолок"
    assert capped["ds"] < raised["ds"]

    shrunk = _group(xml_subs, {"intro_cam": False, "intro_fit_max": 100}, intro=LONG)
    assert shrunk["ds"] == pytest.approx(_fit_free(_width("Д" * 30)), rel=1e-6)
    assert shrunk["ds"] < 100, "потолок урезал ужатие — он обязан резать только рост"
    # потолок доехал до .jsx: ds головной строки — то, что читает шаблон (GRP[0].ds)
    assert capped["lines"][0]["ds"] == round(capped["ds"], 4)
    assert raised["lines"][0]["ds"] == round(raised["ds"], 4)


# ==================================================== 3. безопасная зона (задание MO)

@pytest.mark.parametrize("anchor", ["center", "first"])
@pytest.mark.parametrize("name,group", [("одно слово", ONE),
                                        ("большое + 2 коротких", BIG2),
                                        ("большое + 3 строки", BIG3)])
def test_detached_block_stays_under_the_safe_line(metrics, xml_subs, anchor, name, group):
    """3. Верх блока откреплённой группы — не выше `INTRO_SAFE_TOP` (±0.5), оба якоря.

    Стиль по умолчанию (все ручки дефолтные), снята только галка «интро едет с камерой».
    Сдвиг считается после автофита по фактическому габариту блока (`intro_block_span`:
    капитель строки, кегль большой строки lk), поэтому верх садится ровно на линию.
    """
    style = {"intro_cam": False, "intro_anchor": anchor}
    plan = _scene(xml_subs, style, intro=group)
    p = plan["intro"][0]

    assert p["ds"] != 100, f"{name}/{anchor}: автофит не тронул масштаб — случай не из «Зачем»"
    top = _top(plan)
    assert top >= INTRO_SAFE_TOP - 0.5, (
        f"{name}/{anchor}: верх блока {top} выше безопасной линии {INTRO_SAFE_TOP}")
    assert top == pytest.approx(INTRO_SAFE_TOP, abs=0.5), \
        f"{name}/{anchor}: блок опущен не ровно до линии (верх {top})"

    # Не на пустом месте: по прежнему расчёту (только _intro_i_dy) верх был выше линии.
    assert _old_top(plan, anchor) < INTRO_SAFE_TOP, \
        f"{name}/{anchor}: прежний расчёт и так держал блок под линией — проверка ничего не значит"


def test_the_drop_travels_into_idy_and_y(metrics, xml_subs, tmp_path):
    """3б. Сдвиг — в тот же iDy/y, что едут в .jsx (INTRO_IDY) и в план (y).

    Превью позиционирует блок по plan.intro[].y, шаблон — по INTRO_IDY: разойдись они,
    предпросмотр показал бы не то, что соберётся в AE. Второй копии сдвига нет.
    """
    style = {"intro_cam": False}
    jsx = _build(xml_subs, tmp_path, style, intro=BIG2, name="safe_idy.jsx")
    idy = _jsx_var(jsx, "INTRO_IDY")[0]
    plan = _scene(xml_subs, style, intro=BIG2)
    p = plan["intro"][0]

    assert p["y"] == round(-INTRO_BASE_Y + idy, 2), \
        "план и .jsx разошлись в позиции блока (сдвиг не доехал до одного из них)"
    assert idy > _intro_i_dy(plan["h"], 2, p["ds"]), \
        "сдвиг не больше прежнего: блок опущен не по фактическому верху"


# ==================================================== 4. привязанное интро — как на main

def test_attached_intro_is_as_on_main(metrics, xml_subs, tmp_path):
    """4. `intro_cam=True`: ручки откреплённого интро на сборку не влияют, числа прежние.

    Ширину привязанному задаёт зум Камеры 1: автофит по-прежнему только ужимает (ds = 100
    у короткой строки), iDy считает `_intro_i_dy` от НЕужатого gs, а `intro_fit_w` и
    `intro_fit_max` не читаются вовсе — иначе поехал бы golden (tests/test_geometry_python).
    """
    ref = _build(xml_subs, tmp_path, {}, intro=BIG2, name="att_ref.jsx")
    knobs = _build(xml_subs, tmp_path, {"intro_fit_w": 70, "intro_fit_max": 1000},
                   intro=BIG2, name="att_knobs.jsx")
    assert ref == knobs, "ручки откреплённого интро тронули привязанную сборку"

    plan = _scene(xml_subs, {}, intro=THREE)
    p = plan["intro"][0]
    assert p["ds"] == 100, "автофит потянул привязанное интро вверх"
    assert "ds" not in p["lines"][0], "дефолтный ds=100 уехал в данные строки"
    assert p["y"] == round(-INTRO_BASE_Y + _intro_i_dy(plan["h"], 3, 100), 2), \
        "опускание привязанного интро посчитано не прежней формулой"
    jsx = _build(xml_subs, tmp_path, {}, intro=THREE, name="att3.jsx")
    assert _jsx_var(jsx, "INTRO_IDY")[0] == _intro_i_dy(plan["h"], 3, 100)

    # Длинная строка ужимается прежней формулой: INTRO_FIT_W и зум Камеры 1 в расчёте.
    z = ZOOM[0][1] / 100.0
    long_plan = _scene(xml_subs, {}, intro=LONG, cam1_scale=ZOOM)
    expected = 100.0 * W * INTRO_FIT_W / (_width("Д" * 30) * (INTRO_SCALE / 100.0) * z)
    assert long_plan["intro"][0]["ds"] == pytest.approx(expected, rel=1e-9)


# ==================================================== 5. сторож «каждая ручка» и схема

def test_intro_fit_max_knob_lives_in_schema_base_and_assembly(metrics, xml_subs, tmp_path):
    """5. Ручка intro_fit_max: схема, дефолт в BASE, перевод en и реальный эффект в сборке.

    Четыре двери одной ручки (схема -> styles.BASE -> перевод -> сборка) — как у соседних
    ручек: разъехавшись, они дают мёртвую ручку, которая молча ничего не делает.
    """
    field = watcher.schema_field("intro_fit_max")
    assert field, "в схеме нет ручки intro_fit_max"
    assert field.get("ctl") == "num", "intro_fit_max перестала быть числом"
    assert (field.get("min"), field.get("max"), field.get("step")) == (100, 1000, 10)
    assert field.get("label") == "Потолок увеличения интро, %"
    assert "intro_fit_max" in watcher.schema_keys(), \
        "ручка не попадает в счётчики схемы (test_style_schema)"

    assert styles.BASE.get("intro_fit_max") == DEFAULT_MAX, "дефолт intro_fit_max в BASE не 250"
    assert styles.resolve(None).get("intro_fit_max") == DEFAULT_MAX

    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"), encoding="utf-8"))
    assert en.get(field["label"]) == "Intro max scale, %"
    assert en.get(field["tip"]), "тултип ручки остался без перевода"
    assert en.get(watcher.schema_field("intro_fit_w")["tip"]), \
        "тултип «Интро по ширине» остался без перевода"

    ref = _build(xml_subs, tmp_path, {"intro_cam": False}, name="cap250.jsx")
    mod = _build(xml_subs, tmp_path, {"intro_cam": False, "intro_fit_max": 400},
                 name="cap400.jsx")
    assert ref != mod, "ручка intro_fit_max не изменила собранный .jsx"
    assert _head_ds(ref) == pytest.approx(DEFAULT_MAX)
    assert _head_ds(mod) == pytest.approx(400.0)
    # привязанное интро ручку не читает
    on250 = _build(xml_subs, tmp_path, {}, name="on250.jsx")
    on400 = _build(xml_subs, tmp_path, {"intro_fit_max": 400}, name="on400.jsx")
    assert on250 == on400, "потолок увеличения изменил привязанное интро"

    # сторож «каждая ручка» (test_r11_li_every_knob): ключ проверяется, а не спрятан в исключениях
    import test_r11_li_every_knob as every
    assert "intro_fit_max" in every.TESTED_KEYS, \
        "intro_fit_max не попал в список проверяемых ручек — сторож его не увидит"
