# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Последняя группа интро держится столько же, сколько остальные (ключ intro_last_hold).

Владелец: «Почему последнее интро всё ещё длинное...................». Соседняя работа —
подрезка окна под старт следующей группы (`layout.intro_clamp_window`) — последнюю группу не
трогает: подрезать её не подо что, следующей нет. А базовая формула окна
(`layout._intro_group_window`) добавляла последней группе к её последнему слову F_DUR + HOLD
(1.3 с) и только потом F_OUT (0.75) — итого группа висела +2.05 с, тогда как остальные гаснут
почти сразу. Плюс к последней ВСЕГДА применялась надбавка intro_fx_hold_add (ветка `_far` в
`plan_intro.py`) — у стиля владельца это ещё 0.35 с.

Ключ стиля `intro_last_hold` (`styles.BASE`, группа схемы `intro.timing`) задаёт, сколько
держать ПОСЛЕДНЮЮ группу после её последнего слова: дефолт 1.0 — прежняя константа
INTRO_HOLD, поэтому .jsx стиля без ключа собирается байт в байт прежним (эталон
`fixtures/golden_geometry.jsx` не тронут, его стерегут golden-тесты — test_geometry_python,
test_hl_anim, test_intro_detach и соседи); 0 — последняя считается ровно как непоследние, и
надбавки intro_fx_hold_add она не получает.

Здесь:

1. дефолт 1.0: сборка без ключа и с явной единицей совпадают байт в байт, окно последней
   группы — прежняя формула `gMax + F_DUR + HOLD + F_OUT`;
2. при 0 окно последней группы считается ТОЙ ЖЕ формулой, что у непоследних
   (`max(gMax, inAt+F_DUR) + F_OUT`), а не `gMax + 2.05`: разница со старым поведением 1.3 с;
3. при 0 надбавка intro_fx_hold_add (0.35) к последней группе НЕ применяется, а к «далёкой»
   непоследней — применяется по-прежнему;
4. при 2.5 последняя держится дольше дефолта ровно на 1.5 с;
5. план (предпросмотр) и INTRO_FX (.jsx) несут одно и то же окно последней группы при любом
   значении ключа — оба берут числа из одного места (`plan_intro`);
6. контроль: ключ проигнорирован и вернулась прежняя формула — те самые числа разъезжаются,
   то есть тесты 2–4 краснеют (они сравнивают окно числом).

Фикстура — та же, что у соседних интро-тестов (`tests/fixtures/timeline_subs.xml.gz`), шрифт
намеренно несуществующий: числа не зависят от того, какие шрифты стоят на машине. Галка
`intro_sub_cut` (гашение к субтитру) выключена: она считает своё правило и
резала бы окно раньше полки — здесь проверяется ровно полка последней группы.

Запуск: python -m pytest tests/test_intro_last_hold.py -q
"""
import gzip
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core.xml2ae.plan_intro as plan_intro_mod  # noqa: E402
from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.build import INTRO_ANIMS  # noqa: E402
from core.xml2ae.jsutil import _r  # noqa: E402
from core.xml2ae.layout import (INTRO_F_DUR, INTRO_F_OUT, INTRO_HOLD,  # noqa: E402
                                _intro_group_window)

PS = "TestInk-Regular"                       # шрифта с таким именем нет — метрики из запасной ветки
T_FADE = styles.BASE["intro_fade"]           # спад обычной (не глитч) группы, 0.35 с
GLITCH_DUR = INTRO_ANIMS["glitch"]["dur"]    # длительность появления глитча, 0.44 с
ADD = 0.35                                   # надбавка intro_fx_hold_add для теста 3, с

# Две группы без глитча: последняя — на 5-й секунде, следующей за ней нет (подрезать не подо
# что). Последнее слово 5.5, начало группы 5.0.
TAIL = [
    {"words": ["ПЕРВАЯ"], "color": "white", "times": [1.0, 1.4]},
    {"words": ["ПОСЛЕДНЯЯ"], "color": "white", "times": [5.0, 5.5]},
]
TAIL_SPLITS = [1]

# Три группы ради ветки `_far` (ПРАВКА 4 / IK): первая — глитч, следующая от неё далеко
# (8.0 − 1.79 = 6.2 с, больше двух), последняя — тоже глитч (у глитча спад intro_fade, и
# полка видна в окне прямо).
GLITCH_TAIL = [
    {"words": ["ДАЛЕКО"], "color": "yellow", "times": [1.0], "anim": "glitch"},
    {"words": ["СЕРЕДИНА"], "color": "white", "times": [8.0]},
    {"words": ["ПОСЛЕДНЯЯ"], "color": "yellow", "times": [12.0], "anim": "glitch"},
]
GLITCH_SPLITS = [1, 2]


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


def _jsx(xml, intro, splits, style=None):
    """Исходник .jsx без записи на диск — тем же путём, что сборка."""
    src, _, _ = xml2ae.to_ae_full(xml, return_source=True, intro=intro, intro_splits=splits,
                                  style=_style(**(style or {})), disclaimer="",
                                  intro_riser=False, emit=lambda *a: None)
    return src


def _intro_fx(jsx):
    """INTRO_FX из текста .jsx: [группа] = [начало фейд-аута, конец слоя] прекомпа."""
    decl = [ln for ln in jsx.splitlines() if "var INTRO_FX=" in ln]
    assert decl, "INTRO_FX не доехал до .jsx"
    return json.loads(decl[0].split("var INTRO_FX=", 1)[1].split(";", 1)[0])


def _windows(plan):
    """Окна групп из плана: [(ts, te, fade)] — то, что рисует предпросмотр."""
    return [(g["ts"], g["te"], g["fade"]) for g in plan["intro"]]


def _glitch_hold(gmax, in_at, gl_at):
    """Полка глитч-группы до спада: max(последнее слово/конец фейд-ина, конец анимации
    глитча) — ровно то, что считает план перед надбавкой (ПРАВКА 3 / IK)."""
    return max(max(gmax, in_at + INTRO_F_DUR), gl_at + GLITCH_DUR)


def _schema_field(key):
    """Поле схемы стиля по ключу (обходом — как test_intro_sub_fade)."""
    def walk(items):
        for x in items:
            if x.get("key") == key:
                return x
            got = walk(x.get("items", []))
            if got:
                return got
        return None
    return walk(style_schema.LAYERS)


def _schema_group(key):
    """(слой, группа) ручки: где она живёт в схеме."""
    def walk(items, path):
        for x in items:
            if x.get("key") == key:
                return path
            got = walk(x.get("items", []), path + (x.get("id") or x.get("key"),))
            if got:
                return got
        return None
    for layer in style_schema.LAYERS:
        got = walk(layer.get("items", ()), (layer.get("id"),))
        if got:
            return got
    return None


# ---- 1. дефолт ключа: сборка прежняя ---------------------------------------------------------

def test_дефолт_не_меняет_сборку(xml_subs):
    """1. Дефолт 1.0 — прежняя INTRO_HOLD: .jsx без ключа и с явной единицей совпадают байт
    в байт, а окно последней группы — прежняя формула gMax + F_DUR + HOLD + F_OUT."""
    assert styles.BASE["intro_last_hold"] == INTRO_HOLD, "дефолт ключа уехал с INTRO_HOLD"

    assert _jsx(xml_subs, TAIL, TAIL_SPLITS) == \
        _jsx(xml_subs, TAIL, TAIL_SPLITS, style={"intro_last_hold": 1.0}), \
        "явная единица разошлась со стилем без ключа"

    win = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS))
    assert win[-1] == (5, _r(5.5 + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT), T_FADE), \
        "при дефолте окно последней группы не прежнее: %r" % (win[-1],)


# ---- 2. ноль: последняя считается как непоследние --------------------------------------------

def test_ноль_считает_последнюю_как_непоследних(xml_subs):
    """2. При intro_last_hold=0 окно последней группы — та же формула, что у непоследних:
    max(gMax, inAt + F_DUR) + F_OUT, а не gMax + 2.05. Разница со старым поведением 1.3 с."""
    win = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS, style={"intro_last_hold": 0.0}))
    ts_last, te_last, fade_last = win[1]
    assert ts_last == 5, "начало окна последней группы поехало: %r" % (ts_last,)
    assert fade_last == T_FADE, "спад последней группы поехал: %r" % (fade_last,)
    te_new = _r(max(5.5, 5.0 + INTRO_F_DUR) + INTRO_F_OUT)
    assert te_last == te_new, "окно последней группы не по формуле непоследних: %r" % (te_last,)

    # формула одна: последняя группа (gi = n−1) и непоследняя с теми же словами дают одно окно
    assert _intro_group_window([5.0, 5.5], 2, 3, 0.0) == \
        _intro_group_window([5.0, 5.5], 0, 3, 0.0), "у последней своя формула окна"

    # старое поведение (F_DUR + HOLD у последней) длиннее ровно на 1.3 с — на этом и краснеет
    # тест, если ключ проигнорировать
    te_old = _r(5.5 + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT)
    assert te_old - te_last == pytest.approx(INTRO_F_DUR + INTRO_HOLD, abs=1e-9)
    assert te_old - te_last >= 1.3 - 1e-9, "разница со старым поведением меньше 1.3 с"


# ---- 3. надбавка «далёкой» группы: непоследним — да, последней при нуле — нет -----------------

def test_надбавка_к_последней_не_применяется(xml_subs):
    """3. При intro_last_hold=0 надбавка intro_fx_hold_add к последней группе не применяется,
    а к «далёкой» НЕпоследней — применяется по-прежнему."""
    style = {"intro_last_hold": 0.0, "intro_fx_hold_add": ADD}
    plan = _plan(xml_subs, GLITCH_TAIL, GLITCH_SPLITS, style=style)
    g_far, g_last = plan["intro"][0], plan["intro"][2]

    # первая группа — глитч, следующая от неё через 6.2 с: «далёкая», надбавка на месте
    far_hold = _glitch_hold(1.0, 0.0, 1.0)      # 1.0 + GLITCH_DUR = 1.44 (полка от глитча)
    assert g_far["te"] == _r(far_hold + ADD + T_FADE), \
        "у далёкой непоследней группы пропала надбавка: %r" % (g_far["te"],)

    # последняя — надбавки НЕТ: окно кончается на её же полке
    last_hold = _glitch_hold(12.0, 12.0, 12.0)  # 12.44
    assert g_last["te"] == _r(last_hold + T_FADE), \
        "надбавка всё ещё применена к последней группе: %r" % (g_last["te"],)
    assert abs(g_last["te"] - _r(last_hold + ADD + T_FADE)) > 0.3, \
        "фикстура: надбавку в окне последней группы и не различить"

    # прежнее поведение (дефолт 1.0): надбавка к последней применялась — вот она
    last_def = _plan(xml_subs, GLITCH_TAIL, GLITCH_SPLITS,
                     style={"intro_fx_hold_add": ADD})["intro"][2]
    assert last_def["te"] == _r(max(12.0 + INTRO_F_DUR + INTRO_HOLD,
                                    12.0 + GLITCH_DUR) + ADD + T_FADE), \
        "дефолт потерял надбавку у последней группы: %r" % (last_def["te"],)


# ---- 4. значение 2.5: последняя держится дольше ровно на 1.5 с --------------------------------

def test_два_с_половиной_это_плюс_полторы_секунды(xml_subs):
    """4. При intro_last_hold=2.5 последняя держится дольше дефолта ровно на 1.5 с."""
    w_def = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS))[1]
    w_long = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS,
                            style={"intro_last_hold": 2.5}))[1]
    assert w_long[0] == w_def[0], "начало окна последней группы поехало"
    assert w_long[2] == w_def[2], "спад последней группы поехал"
    assert w_long[1] - w_def[1] == pytest.approx(2.5 - INTRO_HOLD, abs=1e-9), \
        "полка 2.5 держится не на 1.5 с дольше дефолта: %r" % (w_long[1] - w_def[1],)
    assert w_long[1] == _r(5.5 + INTRO_F_DUR + 2.5 + INTRO_F_OUT), \
        "окно последней группы не по ключу: %r" % (w_long[1],)


# ---- 5. план и INTRO_FX несут одно окно ------------------------------------------------------

def test_план_и_intro_fx_несут_одно_окно(xml_subs):
    """5. План и INTRO_FX несут одно и то же окно последней группы при любом значении ключа."""
    expect_last = {
        0.0: _r(max(5.5, 5.0 + INTRO_F_DUR) + INTRO_F_OUT),
        1.0: _r(5.5 + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT),
        2.5: _r(5.5 + INTRO_F_DUR + 2.5 + INTRO_F_OUT),
    }
    for hold, te_last in expect_last.items():
        style = {"intro_last_hold": hold}
        win = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS, style=style))
        fx = _intro_fx(_jsx(xml_subs, TAIL, TAIL_SPLITS, style=style))

        assert win[-1][1] == te_last, "план: окно последней группы не по ключу %r" % (hold,)
        assert len(fx) == len(win), "INTRO_FX не на каждую группу"
        for i, (ts, te, fade) in enumerate(win):
            assert fx[i] is not None, "у группы со строками нет окна в INTRO_FX"
            assert abs(fx[i][1] - te) < 1e-9, \
                "в .jsx конец слоя группы %d не тот, что в плане (ключ %r)" % (i + 1, hold)
            assert abs(fx[i][0] - (te - fade)) < 1e-6, \
                "в .jsx начало затухания группы %d не из плана (ключ %r)" % (i + 1, hold)
        assert fx[-1][1] == win[-1][1], "последняя группа: .jsx и план разошлись"


# ---- 6. контроль: с прежней формулой числа разъезжаются ---------------------------------------

def _old_window(times, gi, n_groups, intro_last_hold=INTRO_HOLD):
    """Прежняя (до ключа) формула окна: у последней группы HOLD всегда, ключ не читается.
    Сигнатура совместима с новой — план зовёт её с четвёртым аргументом."""
    gmin = min(times) if times else 0.0
    gmax = max(times) if times else 0.0
    in_at = 0.0 if (gi == 0 and gmin < 3) else gmin
    out_start = (gmax + INTRO_F_DUR + INTRO_HOLD) if gi == n_groups - 1 \
        else max(gmax, in_at + INTRO_F_DUR)
    return _r(in_at), _r(out_start + INTRO_F_OUT)


def test_контроль_прежняя_формула_разъезжается_с_ключом(xml_subs, monkeypatch):
    """6. Контроль к тестам 2–4: проигнорируй ключ и верни INTRO_HOLD — и окно последней
    группы снова 7.55 при любом значении. Тесты 2–4 сравнивают окно числом, поэтому краснеют."""
    te_old = _r(5.5 + INTRO_F_DUR + INTRO_HOLD + INTRO_F_OUT)
    # Патч — своим контекстом: monkeypatch.undo() снял бы и изоляцию цензуры, а она нужна
    # каждому следующему _plan (иначе план поедет от личных словарей машины).
    with monkeypatch.context() as m:
        m.setattr(plan_intro_mod, "_intro_group_window", _old_window)
        for hold in (0.0, 2.5):
            win = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS, style={"intro_last_hold": hold}))
            assert win[1][1] == te_old, (
                "прежняя формула перестала держать HOLD у последней группы: %r" % (win[1][1],))
        # та же прежняя формула с нулевым ключом: базовая надбавка к последней не идёт, но окно
        # всё равно длиннее правильного — тест 3 краснеет на равенстве
        g_last = _plan(xml_subs, GLITCH_TAIL, GLITCH_SPLITS,
                       style={"intro_last_hold": 0.0, "intro_fx_hold_add": ADD})["intro"][2]
        assert g_last["te"] != _r(_glitch_hold(12.0, 12.0, 12.0) + T_FADE), \
            "фикстура: с прежней формулой окно последней группы то же самое"

    # а с ключом числа те же, что в тестах 2 и 4, и НЕ равны прежним
    for hold in (0.0, 2.5):
        win = _windows(_plan(xml_subs, TAIL, TAIL_SPLITS, style={"intro_last_hold": hold}))
        assert win[1][1] != te_old, "ключ %r ничего не изменил — тесты 2–4 слепы" % (hold,)
    assert _windows(_plan(xml_subs, TAIL, TAIL_SPLITS,
                          style={"intro_last_hold": 0.0}))[1][1] == \
        _r(max(5.5, 5.0 + INTRO_F_DUR) + INTRO_F_OUT), "с ключом 0 окно не по формуле непоследних"


# ---- 7. ручка заведена в стиле, схеме и переводе ----------------------------------------------

def test_ручка_заведена_и_переведена():
    """Ключ есть в styles.BASE, поле — в группе схемы intro.timing рядом с intro_fx_hold_add,
    подпись и подсказка переведены (иначе краснеет test_style_schema)."""
    assert styles.BASE["intro_last_hold"] == INTRO_HOLD
    f = _schema_field("intro_last_hold")
    assert f, "в схеме стиля нет ручки intro_last_hold"
    assert f["ctl"] == "num" and (f["min"], f["max"], f["step"]) == (0, 5, 0.05)
    assert _schema_group("intro_last_hold") == ("intro", "intro.timing"), \
        "ручка переехала из группы «Тайминг»"
    assert _schema_group("intro_fx_hold_add") == ("intro", "intro.timing")

    en = json.loads(open(os.path.join(ROOT, "static", "i18n", "en.json"),
                         encoding="utf-8").read())
    for key in (f["label"], f["tip"]):
        assert key and key in en, "нет перевода en: %r" % (key,)
        assert not any("А" <= ch <= "я" or ch in "Ёё" for ch in en[key]), \
            "в переводе остался русский текст: %r" % key
