# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Подгонка интро по камерам.

Раньше доля ширины кадра для автофита была ОДНА на обе камеры: ручка «Интро по ширине, %»
(`intro_fit_w`) у откреплённого интро и константа `INTRO_FIT_W` у привязанного. На перебивке
интро выходило крупнее, чем на камере 1 (в стиле владельца кам1 привязана — только ужатие,
кам2 откреплена — растяжение до 92 % ширины), и привести их к одному виду было нечем.

Теперь ручки покадровые: «Отступ от краёв, %» (`intro_margin` | `intro_margin2`) с КАЖДОЙ
стороны кадра, доля ширины = `1 − 2·margin/100`, и потолок увеличения (`intro_fit_max` |
`intro_fit_max2`) — свой у каждой камеры. Правило ширины ОДНО на оба режима: непривязанное
интро подгоняется в обе стороны до этой доли, привязанное — только ужимается под неё
(шире его держит зум камеры). Выбор ручек — там же, где выбирается камера группы.

Здесь:
  1. миграция: старый стиль с `intro_fit_w=92` и `intro_fit_max=250` → `intro_margin=4`,
     `intro_margin2=4`, `intro_fit_max2=250`; со `intro_fit_w=80` → отступы 10;
  2. `_intro_fit_ds`: доля ШИРИНЫ кадра равна `1 − 2·margin/100` — и в обе стороны
     (непривязанное), и только ужатием (привязанное); потолок режет рост;
  3. план: одна и та же группа на камере 1 и на камере 2 подгоняется по СВОИМ ручкам
     (разные `intro_margin`), потолок — тоже свой.

Положения группы «большое слева» здесь нет: его целиком задают ключи стиля, как у любой
группы, — сторож `tests/test_intro_big_anchor.py`.

Шрифт подменён на «буква = кегль»: числа теста — формулы сборки, а не то, какие шрифты
стоят на машине. Для раскладки «большое слева» его достаточно: `intro_big_layout` мерит
ширины тем же `text_width`, а верх блока — капителью того же шрифта.
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import fonts, styles, xml2ae  # noqa: E402
from core.xml2ae.build import _intro_fit_ds  # noqa: E402
from core.xml2ae.layout import INTRO_SCALE  # noqa: E402

PS = "TestInk-Regular"          # шрифта с таким именем нет — см. докстринг
T_CAM1, T_CAM2 = 1.0, 8.3       # окна камер фикстуры: 1-я секунда кам1, 8-я — перебивка
W, H = 1080.0, 1920.0
FSIZE = 140                     # кегль интро фикстуры = max(60, int(W*0.13))
SHORT = [dict(words=["A"], color="white", times=[T_CAM1])]      # 140 px строки
LONG10 = [dict(words=["A" * 10], color="white", times=[T_CAM1])]  # 1400 px строки
# Группа с флагом big у головной строки — раскладка «большое слева»: ею проверяется, что
# готовые числа плана доезжают до .jsx (INTRO_LY). Положение таких групп — сторож
# tests/test_intro_big_anchor.py.
BIG = [
    {"words": ["8"], "color": "white", "times": [T_CAM1], "big": True},
    {"words": ["КИЛО"], "color": "white", "times": [T_CAM1 + 0.3]},
    {"words": ["ЗА МЕСЯЦ"], "color": "white", "times": [T_CAM1 + 0.6]},
]

CAM1_ZOOM = [(0, 160), (300, 160)]      # зум Камеры 1 на окне группы
ZOOM = 160.0                            # …то же число: им растянут блок в кадре


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


def _scene(xml, style=None, intro=None, cam1_scale=None, splits=None):
    return xml2ae.scene_plan(xml, disclaimer="", intro_riser=False,
                             intro=SHORT if intro is None else intro,
                             intro_splits=[] if splits is None else splits,
                             style=dict(style or {}, font=PS),
                             cam1_scale=cam1_scale, emit=lambda *a, **k: None)


def _groups(xml, style=None, intro=None, cam1_scale=None, splits=None):
    """Группы интро из плана: ds/ys/y готовые, второй копии расчёта в тесте нет."""
    return _scene(xml, style=style, intro=intro, cam1_scale=cam1_scale,
                  splits=splits)["intro"]


def _share(ds, linew, z=100.0, G=1.0):
    """Доля ШИРИНЫ кадра, под которую встала строка: linew·(iSc/100)·G·Z / W."""
    return linew * (INTRO_SCALE / 100.0) * (ds / 100.0) * G * (z / 100.0) / W


# ======================================================== 1. миграция старых стилей

def test_migration_turns_the_old_width_knob_into_edge_margins():
    """Старый стиль со `intro_fit_w`: отступ = (100 − fit)/2, обе камеры и свой потолок.

    92 % ширины — это 4 % с каждого края, поэтому вид уже собранных стилей не меняется;
    `intro_fit_max2` наследуется от камеры 1 (у перебивки своего потолка в старом стиле
    нет вовсе, и дефолт BASE поменял бы вид собранных роликов).
    """
    old = styles.resolve({"intro_fit_w": 92, "intro_fit_max": 250})
    assert old["intro_margin"] == 4.0
    assert old["intro_margin2"] == 4.0
    assert old["intro_fit_max2"] == 250.0

    narrow = styles.resolve({"intro_fit_w": 80, "intro_fit_max": 300})
    assert narrow["intro_margin"] == 10.0
    assert narrow["intro_margin2"] == 10.0
    assert narrow["intro_fit_max2"] == 300.0

    # новый стиль (ключа intro_fit_w в нём нет): дефолт по камерам из BASE
    fresh = styles.resolve({})
    assert fresh["intro_margin"] == styles.BASE["intro_margin"]
    assert fresh["intro_margin2"] == styles.BASE["intro_margin2"]
    assert fresh["intro_fit_max2"] == styles.BASE["intro_fit_max"]


# ============================================ 2. одно правило ширины для обоих режимов

@pytest.mark.parametrize("margin", [0.0, 4.0, 10.0, 25.0, 30.0])
def test_detached_fit_width_comes_from_the_margin(wide_font, margin):
    """Непривязанное интро: ширина ровно `1 − 2·margin/100` ширины кадра, в обе стороны.

    Короткая строка растягивается, длинная ужимается — доля одна и та же, и потолок
    (1000) намеренно выше подгонки, чтобы проверялась доля, а не упор в потолок.
    """
    fit = 1.0 - 2.0 * margin / 100.0
    short = _intro_fit_ds(SHORT, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                          PS, PS, FSIZE, fit_w=fit, both_ways=True, fit_max=1000.0)
    long_ = _intro_fit_ds(LONG10, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                          PS, PS, FSIZE, fit_w=fit, both_ways=True, fit_max=1000.0)
    assert _share(short, FSIZE) == pytest.approx(fit, rel=1e-9)
    assert _share(long_, FSIZE * 10) == pytest.approx(fit, rel=1e-9)
    assert short > 100 > long_, "подгонка обязана работать в обе стороны"


def test_attached_fit_shrinks_to_the_same_share_and_ignores_the_cap(wide_font):
    """Привязанное интро: только ужатие, и доля — та же `1 − 2·margin/100`.

    Зум Камеры 1 (160 %) входит в расчёт, потолок не читается вовсе (его роль играет
    ручной gs), короткая строка остаётся на gs — вверх автофит не тянет.
    """
    fit = 1.0 - 2.0 * 10.0 / 100.0
    long_ = _intro_fit_ds(LONG10, T_CAM1, T_CAM1, 100.0, int(W), 1.0, CAM1_ZOOM, 60.0,
                          0.69, PS, PS, FSIZE, fit_w=fit, both_ways=False, fit_max=250.0)
    assert _share(long_, FSIZE * 10, z=160.0) == pytest.approx(fit, rel=1e-9)
    assert long_ < 100

    short = _intro_fit_ds(SHORT, T_CAM1, T_CAM1, 100.0, int(W), 1.0, CAM1_ZOOM, 60.0,
                          0.69, PS, PS, FSIZE, fit_w=fit, both_ways=False, fit_max=250.0)
    assert short == 100.0, "привязанное интро растянулось — автофит полез вверх"


def test_fit_max_caps_the_growth_per_camera(wide_font):
    """Потолок — свой у каждой камеры: рост режется, ужатие потолком не режется."""
    fit = 1.0 - 2.0 * 4.0 / 100.0
    capped = _intro_fit_ds(SHORT, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                           PS, PS, FSIZE, fit_w=fit, both_ways=True, fit_max=250.0)
    raised = _intro_fit_ds(SHORT, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                           PS, PS, FSIZE, fit_w=fit, both_ways=True, fit_max=1000.0)
    assert capped == 250.0 and raised > 250.0
    shrunk = _intro_fit_ds(LONG10, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                           PS, PS, FSIZE, fit_w=fit, both_ways=True, fit_max=100.0)
    assert shrunk < 100.0, "потолок урезал ужатие — он обязан резать только рост"


# ============================================ 3. план: своя доля у каждой камеры

def test_camera_2_takes_its_own_margin(wide_font, xml_subs):
    """Группа на перебивке подгоняется по `intro_margin2`, а не по `intro_margin`.

    Меняем ТОЛЬКО отступы: у группы камеры 2 подмена `intro_margin2` с 4 на 25 обязана
    отозваться её шириной ровно так же, как у группы камеры 1 отзывается `intro_margin`.
    Отношение ds двух прогонов сравнивается с отношением долей — так проверка не зависит
    от того, какая ширина строки у конкретной группы, и «общий intro_fit_w на обе камеры»
    её красит.
    """
    intro2 = [dict(words=["A"], color="white", times=[T_CAM1]),
              dict(words=["B"], color="white", times=[T_CAM2])]
    base = {"intro_cam": False, "intro_cam2": False, "intro_fit_max": 1000.0,
            "intro_fit_max2": 1000.0, "intro_margin": 25.0, "intro_margin2": 4.0}
    narrow = dict(base, intro_margin2=25.0)

    g1, g2 = _groups(xml_subs, style=base, intro=intro2, splits=[1])
    _n1, n2 = _groups(xml_subs, style=narrow, intro=intro2, splits=[1])
    assert g1["on2"] is False and g2["on2"] is True, "предпосылка: вторая группа на перебивке"

    # доля ширины камеры 1 — из intro_margin, камеры 2 — из intro_margin2
    assert g1["ds"] < g2["ds"], "узкий отступ камеры 1 не сработал"
    assert n2["ds"] == pytest.approx(g2["ds"] * (1.0 - 2 * 25.0 / 100.0) /
                                     (1.0 - 2 * 4.0 / 100.0), rel=1e-6), \
        "intro_margin2 не управляет шириной группы на перебивке"
    # и обратно: правка intro_margin группу камеры 2 не трогает
    other = dict(base, intro_margin=4.0)
    _o1, o2 = _groups(xml_subs, style=other, intro=intro2, splits=[1])
    assert o2["ds"] == pytest.approx(g2["ds"], rel=1e-9), \
        "intro_margin камеры 1 подвинул группу камеры 2"

    # потолок камеры 2 — свой: подгонка по 92 % ширины выше 400, и он её режет
    low = dict(base, intro_fit_max=1000.0, intro_fit_max2=400.0)
    _l1, l2 = _groups(xml_subs, style=low, intro=intro2, splits=[1])
    assert l2["ds"] == 400.0, "intro_fit_max2 не ограничил рост на камере 2"
    assert g2["ds"] > 400.0, \
        "потолок камеры 2 не режет рост — проверка ничего не значит"
    assert _l1["ds"] == g1["ds"], "потолок камеры 2 срезал группу камеры 1 (не свой)"


def test_attached_camera_2_shrinks_by_its_own_margin(wide_font, xml_subs):
    """Привязанная к Камере 2 группа ужимается долей из `intro_margin2`, а не константой.

    Правило ширины одно на оба режима: доля = 1 − 2·margin/100, а шире текста не пускает
    зум камеры. Меняем только `intro_margin2` — группа камеры 2 обязана отозваться
    пропорционально, как отзывается откреплённая.
    """
    long2 = [dict(words=["A"], color="white", times=[T_CAM1]),
             dict(words=["A" * 10], color="white", times=[T_CAM2])]
    base = {"intro_cam": True, "intro_cam2": True, "intro_fit_max": 1000.0,
            "intro_fit_max2": 1000.0, "intro_margin": 25.0, "intro_margin2": 4.0}
    wide = dict(base, intro_margin2=10.0)
    _a1, a2 = _groups(xml_subs, style=base, intro=long2, splits=[1])
    _b1, b2 = _groups(xml_subs, style=wide, intro=long2, splits=[1])
    assert a2["cam"] is True, "предпосылка: вторая группа привязана к камере 2"
    assert a2["ds"] < 100.0, "предпосылка: длинная строка обязана ужиматься"
    assert b2["ds"] == pytest.approx(a2["ds"] * (1.0 - 2 * 10.0 / 100.0) /
                                     (1.0 - 2 * 4.0 / 100.0), rel=1e-6), \
        "привязанная камера 2 ужалась не по своему отступу"


# ============================================ 4. превью и .jsx читают готовые числа

def test_plan_carries_the_numbers_and_jsx_reads_them(wide_font, xml_subs, tmp_path):
    """`ys` плана уезжают в .jsx массивом INTRO_LY (второй формулы в JS нет)."""
    import json as _json
    import re as _re
    style = {"intro_margin": 30.0}
    g = _groups(xml_subs, style=style, intro=BIG, cam1_scale=CAM1_ZOOM)[0]
    path, _n, _s = xml2ae.to_ae_full(xml_subs, jsx_path=str(tmp_path / "big.jsx"),
                                     intro=BIG, intro_splits=[],
                                     style=dict(style, font=PS), intro_mode="word",
                                     disclaimer="", intro_riser=False,
                                     emit=lambda *a, **k: None)
    jsx = open(path, encoding="utf-8-sig").read()
    m = _re.search(r"var INTRO_LY=(\[.*?\]);", jsx)
    assert m, "INTRO_LY не объявлен — раскладке строк неоткуда взяться"
    assert _json.loads(m.group(1)) == [g["ys"]], \
        "INTRO_LY в .jsx разошёлся с раскладкой плана"


def test_fit_knobs_live_in_schema_base_and_translations():
    """Сторож «каждая ручка»: поля в схеме, дефолты в BASE, подписи и тултипы в en.json."""
    import io
    import json
    import test_style_keys_in_ui as watcher

    for key, label in (("intro_margin", "Отступ от краёв, % (камера 1)"),
                       ("intro_margin2", "Отступ от краёв, % (камера 2)"),
                       ("intro_fit_max", "Масштаб интро, % (камера 1)"),
                       ("intro_fit_max2", "Масштаб интро, % (камера 2)")):
        field = watcher.schema_field(key)
        assert field, f"в схеме нет ручки {key}"
        assert field.get("ctl") == "num", f"{key} перестала быть числом"
        assert field.get("label") == label
        assert "show_if" not in field, f"{key}: ручка снова видна только у откреплённого интро"
        assert key in watcher.schema_keys(), f"ручка {key} не попадает в счётчики схемы"

    assert styles.BASE["intro_margin"] == styles.BASE["intro_margin2"] == 4.0
    assert styles.BASE["intro_fit_max2"] == 250.0
    assert "intro_fit_w" not in styles.BASE, "старая ручка осталась в BASE"

    en = json.load(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                           encoding="utf-8"))
    for key in ("intro_margin", "intro_margin2", "intro_fit_max", "intro_fit_max2"):
        field = watcher.schema_field(key)
        assert en.get(field["label"]), f"подпись {key} осталась без перевода"
        assert en.get(field["tip"]), f"тултип {key} остался без перевода"


def test_intro_fit_w_is_gone_from_the_panel(wide_font, xml_subs):
    """Старая ручка ушла из панели: доля ширины теперь у каждой камеры своя."""
    import test_style_keys_in_ui as watcher
    assert watcher.schema_field("intro_fit_w") is None, "intro_fit_w осталась в схеме"
    assert "intro_fit_w" not in styles.BASE


def test_intro_fit_ds_without_a_style_keeps_the_spare_share(wide_font):
    """Прямой вызов автофита без доли (fit_w=None) — запасная доля 0.92, как было."""
    from core.xml2ae.layout import INTRO_FIT_W
    ds = _intro_fit_ds(LONG10, T_CAM1, T_CAM1, 100.0, int(W), 1.0, [], 60.0, 0.69,
                       PS, PS, FSIZE)
    assert _share(ds, FSIZE * 10) == pytest.approx(INTRO_FIT_W, rel=1e-9)
