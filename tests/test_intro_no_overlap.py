# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Группы интро не накладываются друг на друга: окно группы режется по старту следующей.

Пользователь: «мы правили анимации чтобы не накладывались друг на друга у интро, и всё
равно мне надо вручную доводить фейды на многих анимациях». Окно показа группы считалось
БЕЗ оглядки на следующую группу: базовая формула (`layout._intro_group_window`) для
непоследних групп даёт `outStart + 0.75`, а глитч-группа продлевается ещё и до конца
анимации глитча. Замер на реальной сборке (`MYASAutoCut_out/Reelsi_all.jsx`, ролик 4678):

| группа | слова | окно показа | старт следующей | перехлёст |
|---|---|---|---|---|
| 1 | «Я НЕ ПЬЮ / НА КУ*СЕ» | 0 → 1.87 | 1.57 | 0.30 с |
| 2 | «НО КУРЮ» (glitch) | 1.57 → 2.97 | 2.60 | 0.37 с |
| 3 | «это же печ*нь…» | 2.60 → 5.93 | 4.42 | 1.51 с |

Правило (`layout.intro_clamp_window`): группа гаснет НЕ ПОЗЖЕ момента появления следующей,
не влезает анимация последнего слова вместе с фейдом — обе ужимаются одним множителем, но
не короче `INTRO_MIN_PART`. Сжатие САМОЙ анимации живёт в `INTRO_SQ`: начало
затухания сдвинулось — слово играет появление за остаток; второго механизма нет.

Окно считает Python и ОДНИМ числом отдаёт обеим дверям: план сцены (`plan["intro"][].ts/te/
fade` — их рисует предпросмотр) и `INTRO_FX` в .jsx (по ним шаблон ставит outStart/outEnd —
своей копии формулы в `template.py` больше нет). Отсюда и проверки: план И массив в тексте
.jsx. Тесты:

1. три плотные группы (старты 0.0, 1.57, 2.60 — как в замере): `outEnd ≤ старт следующей`
   по плану и по INTRO_FX;
2. глитч-группа, за которой следующая через 0.42 с: окно кончается ровно на ней, фейд ужат
   (но не ниже пола), появление глитч-слова — множителем меньше 1;
3. одинокая группа (следующая дальше 2 с): окно число в число прежнее;
4. контроль: без вызова `intro_clamp_window` те же окна снова перехлёстываются — на это
   краснеют тесты 1 и 2.

Фикстура — та же, что у соседних интро-тестов (`tests/fixtures/timeline_subs.xml.gz`), шрифт
намеренно несуществующий: числа не зависят от того, какие шрифты стоят на машине. Галка
`intro_sub_cut` выключена: здесь проверяется подрезка под СЛЕДУЮЩУЮ ГРУППУ, а гашение к
субтитру стерегут свои тесты.

Запуск: python -m pytest tests/test_intro_no_overlap.py -q
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import core.xml2ae.plan_intro as plan_intro_mod  # noqa: E402
from core import xml2ae  # noqa: E402
from core.xml2ae.layout import (INTRO_F_DUR, INTRO_F_OUT, INTRO_MIN_PART,  # noqa: E402
                                _intro_group_window, intro_clamp_window)

PS = "TestInk-Regular"      # шрифта с таким именем нет — метрики из запасной ветки
T_INTRO_FADE = 0.35         # дефолт стиля styles.BASE["intro_fade"] (и INTRO_F_OUT длиннее)
GLITCH_DUR = 0.44           # INTRO_ANIMS["glitch"]["dur"] — длительность появления глитча

# Три плотные группы из замера (старты 0.0, 1.57 и 2.60, слова — как в таблице задания):
# до правки окна были 0→1.87, 1.57→2.97 и 2.60→5.45, то есть первые две висели в кадре
# поверх следующих 0.30 и 0.37 с.
DENSE = [
    {"words": ["Я", "НЕ", "ПЬЮ"], "color": "white", "times": [0.5, 0.8, 1.12]},
    {"words": ["НО", "КУРЮ"], "color": "yellow", "times": [1.57, 2.18], "anim": "glitch"},
    {"words": ["это", "же", "печ*нь"], "color": "white", "times": [2.6, 3.0, 3.4]},
]
DENSE_SPLITS = [1, 2]

# Глитч-группа и следующая ровно через 0.42 с после её последнего слова: полка глитча
# (1.0 + 0.44) вместе со спадом 0.35 кончалась бы в 1.79 — на 0.37 с позже следующей.
GLITCH_DENSE = [
    {"words": ["ГЛИТЧ"], "color": "yellow", "times": [1.0], "anim": "glitch"},
    {"words": ["СЛЕДУЮЩАЯ"], "color": "white", "times": [1.0 + 0.42]},
]
GLITCH_SPLITS = [1]

# Одинокая группа: следующая — через 10 с (много больше 2 с после конца окна), подрезать
# не подо что: окно обязано остаться ровно прежним.
LONELY = [
    {"words": ["ОДИНОКАЯ"], "color": "white", "times": [1.0]},
    {"words": ["ДАЛЕКО"], "color": "white", "times": [10.0]},
]
LONELY_SPLITS = [1]


@pytest.fixture()
def xml_subs(tmp_path):
    """Ролик фикстуры (60 fps, две камеры) — тот же, что у соседних интро-тестов."""
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


def _style(**kw):
    return dict({"font": PS, "intro_sub_cut": False}, **kw)


def _plan(xml, intro, splits, style=None):
    return xml2ae.scene_plan(xml, intro=intro, intro_splits=splits,
                             style=_style(**(style or {})), disclaimer="",
                             intro_riser=False, emit=lambda *a: None)


def _build(xml, tmp_path, intro, splits, style=None, name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=splits, style=_style(**(style or {})),
                                   disclaimer="", intro_riser=False, emit=lambda *a: None)
    return open(path, encoding="utf-8-sig").read()


def _intro_fx(jsx):
    """INTRO_FX из текста .jsx: [группа] = [начало фейд-аута, конец слоя] прекомпа."""
    decl = [ln for ln in jsx.splitlines() if "var INTRO_FX=" in ln]
    assert decl, "INTRO_FX не доехал до .jsx"
    return json.loads(decl[0].split("var INTRO_FX=", 1)[1].split(";", 1)[0])


def _intro_sq(jsx):
    """INTRO_SQ из текста .jsx: [группа][строка][слово] — множитель появления."""
    decl = [ln for ln in jsx.splitlines() if "var INTRO_SQ=" in ln]
    assert decl, "INTRO_SQ не доехал до .jsx"
    return json.loads(decl[0].split("var INTRO_SQ=", 1)[1].split(";", 1)[0])


def _windows(plan):
    """Окна групп из плана: [(ts, te, fade)] — то, что рисует предпросмотр."""
    return [(g["ts"], g["te"], g["fade"]) for g in plan["intro"]]


# ---- 1. группы не перехлёстываются ---------------------------------------------------------

def test_группы_интро_не_перехлёстываются(xml_subs, tmp_path):
    """Ни одна группа не висит в кадре после появления следующей — по плану И по INTRO_FX.

    Окна из замера (0→1.87, 1.57→2.97 и 2.60→5.45) подрезаны под старты следующих групп:
    1.57 и 2.60; у последней группы следующей нет — её окно прежнее.
    """
    plan = _plan(xml_subs, DENSE, DENSE_SPLITS)
    win = _windows(plan)
    assert len(win) == 3, "фикстура не разбилась на три группы"

    # главное правило: конец окна не позже появления следующей группы
    for i in range(len(win) - 1):
        assert win[i][1] <= plan["intro"][i + 1]["ts"] + 1e-9, (
            "группа %d всё ещё висит после появления следующей: te=%.4f > ts=%.4f"
            % (i + 1, win[i][1], plan["intro"][i + 1]["ts"]))

    # числа после подрезки: конец — ровно старт следующей, фейд ужат пропорционально
    assert abs(win[0][1] - 1.57) < 1e-9 and abs(win[0][2] - 0.2423) < 5e-4
    assert abs(win[1][1] - 2.6) < 1e-9 and abs(win[1][2] - 0.1861) < 5e-4
    assert win[0][2] < T_INTRO_FADE and win[1][2] < T_INTRO_FADE, "фейд не ужат"
    # последняя группа под подрезку не попадает: у неё своё окно с HOLD
    assert abs(win[2][0] - 2.6) < 1e-9 and abs(win[2][2] - T_INTRO_FADE) < 1e-9

    # те же числа уезжают в .jsx: INTRO_FX[гр] = [начало затухания, конец слоя]
    jsx = _build(xml_subs, tmp_path, DENSE, DENSE_SPLITS)
    fx = _intro_fx(jsx)
    assert len(fx) == len(win), "INTRO_FX не на каждую группу"
    for i, (ts, te, fade) in enumerate(win):
        assert fx[i] is not None, "у группы со строками нет окна в INTRO_FX"
        assert abs(fx[i][1] - te) < 1e-9, "конец слоя в .jsx не тот, что в плане"
        assert abs(fx[i][0] - (te - fade)) < 1e-6, "начало затухания в .jsx не из плана"
        if i + 1 < len(fx):
            assert fx[i][1] <= plan["intro"][i + 1]["ts"] + 1e-9, (
                "в .jsx группа %d кончается позже появления следующей" % (i + 1))


# ---- 2. глитч ужимается, а не растягивает окно ---------------------------------------------

def test_глитч_ужимается_а_не_растягивает_окно(xml_subs, tmp_path):
    """Глитч-группа гаснет ровно к следующей: фейд ужат, появление слова — через INTRO_SQ."""
    plan = _plan(xml_subs, GLITCH_DENSE, GLITCH_SPLITS)
    g0, g1 = plan["intro"][0], plan["intro"][1]
    assert abs(g1["ts"] - 1.42) < 1e-9, "фикстура: следующая группа не через 0.42 с"

    # без подрезки глитч-группа играла бы до 1.79 (полка 1.44 после продления под глитч
    # плюс спад 0.35) — это и есть перехлёст, который владелец правил руками
    _ts_old, te_old = _intro_group_window([1.0], 0, 2)
    out_old = max(te_old - INTRO_F_OUT, 1.0 + GLITCH_DUR)
    assert abs((out_old + T_INTRO_FADE) - 1.79) < 1e-9
    assert out_old + T_INTRO_FADE > g1["ts"], "фикстура: перехлёста и не было"

    assert abs(g0["te"] - g1["ts"]) < 1e-9, "окно глитч-группы не подрезано к следующей"
    assert INTRO_MIN_PART <= g0["fade"] < T_INTRO_FADE, (
        "фейд не ужат или ужат ниже пола: %r" % (g0["fade"],))
    # полка не раньше конца фейд-ина группы (0.3 с) — как и в базовой формуле
    assert g0["te"] - g0["fade"] >= g0["ts"] + INTRO_F_DUR - 1e-9
    assert abs((g0["te"] - g0["fade"]) - 1.2339) < 5e-4, (
        "начало затухания не то, что даёт правило: %r" % (g0["te"] - g0["fade"],))

    # второй механизм сжатия не заводится: анимацию ужимает INTRO_SQ
    sq = g0["sq"][0][0]
    assert sq is not None and 0 < sq < 1, "появление глитч-слова не сжато: %r" % (sq,)
    assert abs(sq - max(0.1, (g0["te"] - g0["fade"]) - 1.0) / GLITCH_DUR) < 1e-3

    jsx = _build(xml_subs, tmp_path, GLITCH_DENSE, GLITCH_SPLITS)
    fx = _intro_fx(jsx)
    assert abs(fx[0][1] - g1["ts"]) < 1e-9, "в .jsx окно глитч-группы не подрезано"
    assert abs(fx[0][0] - (g0["te"] - g0["fade"])) < 1e-6
    assert abs(_intro_sq(jsx)[0][0][0] - sq) < 1e-9, "в .jsx множитель не тот, что в плане"


# ---- 3. одинокая группа не меняется --------------------------------------------------------

def test_одинокая_группа_не_меняется(xml_subs, tmp_path):
    """Следующая группа дальше 2 с после конца — окно остаётся ровно прежним, число в число."""
    plan = _plan(xml_subs, LONELY, LONELY_SPLITS)
    g0 = plan["intro"][0]
    # РОВНО базовая формула _intro_group_window (inAt=0 при gMin<3, outStart+INTRO_F_OUT)
    assert (g0["ts"], g0["te"], g0["fade"]) == (0, 1.75, T_INTRO_FADE)
    assert "sq" not in g0, "у одинокой группы сжали появление"
    # вторая группа — последняя: у неё своё окно с HOLD (gmax + F_DUR + HOLD + F_OUT)
    assert _windows(plan)[1] == (10, 12.05, T_INTRO_FADE)

    # сама подрезка на таких входах возвращает окно как есть — и без следующей, и с далёкой
    assert intro_clamp_window(0.0, 1.0, 1.4, 1.75, T_INTRO_FADE, 0.3, None) == \
        (1.4, 1.75, T_INTRO_FADE)
    assert intro_clamp_window(0.0, 1.0, 1.4, 1.75, T_INTRO_FADE, 0.3, 10.0) == \
        (1.4, 1.75, T_INTRO_FADE)

    jsx = _build(xml_subs, tmp_path, LONELY, LONELY_SPLITS)
    assert _intro_fx(jsx) == [[1.4, 1.75], [11.7, 12.05]]
    assert "INTRO_SQ" not in jsx, "сжатие объявлено там, где окна не укорачивали"


# ---- 4. контроль: без вызова подрезки окна снова перехлёстываются --------------------------

def test_без_подрезки_окна_перехлёстываются(xml_subs, monkeypatch):
    """Убери вызов intro_clamp_window — и группы снова висят по две в кадре.

    Тесты 1 и 2 краснеют ровно на этом: подрезка не украшение, а несущий шаг.
    """
    monkeypatch.setattr(plan_intro_mod, "intro_clamp_window",
                        lambda in_at, t_last, out_start, out_end, fade, anim_dur, next_in:
                        (out_start, out_end, fade))
    old = _windows(_plan(xml_subs, DENSE, DENSE_SPLITS))
    assert old[0][1] > 1.57 and old[1][1] > 2.6, "без подрезки окна не перехлёстываются"
    assert abs(old[0][1] - 1.87) < 1e-9 and abs(old[1][1] - 2.97) < 1e-9, (
        "окна без подрезки разошлись с базовой формулой")

    monkeypatch.undo()
    new = _windows(_plan(xml_subs, DENSE, DENSE_SPLITS))
    for i in range(len(new) - 1):
        assert new[i][1] <= new[i + 1][0] + 1e-9, "с подрезкой окна всё ещё накладываются"
