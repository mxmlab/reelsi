# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Deep Glow не ставится на строку со «свечением» (задание MK).

Владелец выключил Deep Glow там, где на вид было очень сильное свечение. Разбор
reelsi_batch.aep: плагин (matchName PEDG2) ставится на жёлтые слова глитча, а у 7 из 10
таких слов строка ЕЩЁ И со свечением (fx=="glow" — Glo2 на слое слова): два свечения
складывались. Теперь по умолчанию таким словам Deep Glow не ставится — вместо плагина
идёт ровно то, что жёлтому глитчу в режиме «Встроенные» (Gaussian Blur + Glo2). Галка
стиля intro_dg_with_glow возвращает прежнее поведение — «Deep Glow вместе со свечением
строки», en «Deep Glow with line glow», дефолт False.

Список эффектов проверяется прогоном функции introAnimFX из готового .jsx в node с
заглушками слоя (как в tests/test_hd_glitch_deepglow.py): «в .jsx у слова нет PEDG2» —
это про слово, а не про текст шаблона, и в смешанной сборке обе ветки лежат в одном .jsx.

Доработка MK2/MK3: Deep Glow не ставится и на ЯРКИЙ цвет жёлтого — той же проверкой и с тем
же порогом TRITONE_MAX_LUM, что у Tritone (задание ZN): на ярком цвете свечение выбеливает
букву, и плагин добавляет к нему второе свечение. Правило ОДНО на любой яркий жёлтый —
заданный в стиле (hl_fill/intro_hl_fill) или стоковый из styles.BASE ([1,0.9176,0], 0.87):
исключения «только заданный цвет» нет (доработка MK3) — у владельца стоковый жёлтый в
большинстве стилей, и с исключением правило для них не работало бы вовсе. Правило MK (строка
со свечением + галка) остаётся для ТЁМНОГО цвета, поэтому тесты 1–3, 6 собираются на тёмном
жёлтом в стиле, а стоковый жёлтый стережёт тест 8. Цвет берётся из стиля — intro_hl_fill,
иначе hl_fill, незаданный — стоковый.
"""
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from core import style_schema, styles, xml2ae  # noqa: E402
from core.xml2ae.build import DEEP_GLOW2_GLITCH, TRITONE_MAX_LUM, _tritone_on  # noqa: E402

T_CAM1, T_CAM2 = 1.0, 8.3

# Цвета из замера 2026-09-18 — те же, что в tests/test_tritone_bright.py; в скобках яркость
# Rec.709, по которой решает общая с Tritone проверка `_tritone_on` (порог TRITONE_MAX_LUM).
YELLOW_BRIGHT = [1.0, 0.9686, 0.0784]   # 0.91 — Deep Glow не ставится
CYAN = [0.0, 0.749, 1.0]                # 0.61 — ставится
DARK_GRAY = [0.22, 0.22, 0.22]          # 0.22 — ставится
# Тёмный жёлтый (та же голубая заливка) для тестов, где Deep Glow ДОЛЖЕН ставиться: с
# доработки MK3 яркий жёлтый плагина не берёт вовсе, в том числе стоковый (тест 8).
DARK_FILL = [0.0, 0.75, 1.0]            # 0.61 — ниже порога

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _build(xml, tmp_path, intro, style=None, glitch_glow="builtin", name="out.jsx"):
    path, _, _ = xml2ae.to_ae_full(xml, jsx_path=str(tmp_path / name), intro=intro,
                                   intro_splits=[1], style=style or {},
                                   intro_mode="word", disclaimer="",
                                   glitch_glow=glitch_glow, emit=lambda *a: None)
    return io.open(path, encoding="utf-8-sig").read(), path


def _glow_glitch_intro():
    """Тот самый случай: жёлтое слово глитча СО свечением строки."""
    return [dict(words=["СВЕТИТСЯ"], color="yellow", times=[T_CAM1], anim="glitch", fx="glow")]


def _plain_glitch_intro():
    """Жёлтый глитч БЕЗ свечения — как было до задания."""
    return [dict(words=["ГЛИТЧ"], color="yellow", times=[T_CAM1], anim="glitch")]


def _mixed_intro():
    """Обе строки в одной сборке: со свечением и без — условие в .jsx решает по КАЖДОЙ."""
    return [
        dict(words=["СВЕТИТСЯ"], color="yellow", times=[T_CAM1], anim="glitch", fx="glow"),
        dict(words=["ПРОСТОЙ"], color="yellow", times=[T_CAM1 + 0.3], anim="glitch"),
    ]


def _balanced(jsx, open_brace):
    depth = 0
    for i in range(open_brace, len(jsx)):
        if jsx[i] == "{":
            depth += 1
        elif jsx[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("не нашёл закрывающую скобку")


def _anim_fx_src(jsx):
    """Исходник introAnimFX (с объявлением DG_MISS, если оно стоит прямо перед ним)."""
    i = jsx.index("function introAnimFX")
    prefix = ""
    idx_dg = jsx.rfind("var DG_MISS=0;", 0, i)
    if idx_dg != -1 and jsx[idx_dg:i].strip() == "var DG_MISS=0;":
        prefix = "var DG_MISS=0;\n"
    return prefix + jsx[i:_balanced(jsx, jsx.index("{", i))]


_JS_PROBE = r"""
'use strict';
var calls = [];
var DG_MISS = 0;
function eff(){ return { property: function(p){ return { setValue: function(v){ calls.push('set:'+p+'='+JSON.stringify(v)); } }; } }; }
function addFX(L, mn){ calls.push('add:'+mn); return eff(); }
function setP(fx, mn, v){ if(fx){ calls.push('set:'+mn+'='+JSON.stringify(v)); } }
function lay(){ var o={}; o.property=function(){ return lay(); }; o.addProperty=function(){ return lay(); };
  o.setValue=function(){}; o.setValueAtTime=function(){}; o.value=[0,0]; o.expression=''; return o; }
function easePair(){}
function introW(){ return 100; }
var HL_DUR=0.35, F_DUR=0.25, HL_RISE=60, BACK_SCALE=1, BACK_STEP=0.45, INTRO_GLOW=1;
var HL_FILL=__HLFILL__;
var GRP = [];

__ANIMFX__

function runWord(anim, fx, col){ calls=[]; introAnimFX(lay(), 0, anim, fx, null, null, null, false, col); return calls; }

var out = {
  yellow_glitch:      runWord('glitch', '', 'yellow'),
  yellow_glitch_glow: runWord('glitch', 'glow', 'yellow'),
  dg_miss: (typeof DG_MISS !== 'undefined') ? DG_MISS : -1
};
console.log(JSON.stringify(out));
"""


def _hl_fill_literal(jsx):
    m = re.search(r"var HL_FILL = (\[[^\]]*\])(?:,|;)", jsx)
    assert m, "в .jsx нет объявления HL_FILL"
    return m.group(1)


def _probe(jsx, tmp_path, name="probe_deepglow_glow.js"):
    """Прогон шаблона в node с заглушками слоя: списки эффектов по каждому слову."""
    script = (_JS_PROBE
              .replace("__HLFILL__", _hl_fill_literal(jsx))
              .replace("__ANIMFX__", _anim_fx_src(jsx)))
    node_file = tmp_path / name
    node_file.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(node_file)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node упал: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


def _dg_expected():
    """PEDG2 с 30 свойствами — ровно то, что ставит Deep Glow жёлтому слову глитча."""
    return ["add:PEDG2"] + [f"set:{mn}={json.dumps(val, separators=(',', ':'))}"
                            for mn, val in DEEP_GLOW2_GLITCH]


def _glow_expected():
    """Встроенное свечение — та же пара, что у жёлтого глитча в режиме «Встроенные»."""
    return [
        "add:ADBE Gaussian Blur 2", "set:ADBE Gaussian Blur 2-0001=3.4",
        "add:ADBE Glo2", "set:ADBE Glo2-0002=149", "set:ADBE Glo2-0003=77",
        "set:ADBE Glo2-0004=0.62",
    ]


def _tritone_expected(jsx):
    """Tritone, который на тёмном цвете идёт за жёлтой строкой (задание ZN)."""
    return ["add:ADBE Tritone", "set:ADBE Tritone-0002=" + _hl_fill_literal(jsx)]


def test_1_слово_со_свечением_deep_glow_не_берёт(xml_subs, tmp_path):
    """1. anim=glitch, color=yellow, fx=glow (режим deepglow2, галка выключена):
    в .jsx нет ни PEDG2, ни DG_MISS — сборка побайтово равна режиму «Встроенные».

    Цвет задан ТЁМНЫМ (DARK_FILL): на ярком жёлтом плагина нет ни у кого, и тест молча
    перестал бы проверять правило MK — отсутствие PEDG2 шло бы от яркости, а не от fx.
    """
    intro = _glow_glitch_intro()
    jsx, _ = _build(xml_subs, tmp_path, intro, style={"hl_fill": DARK_FILL},
                    glitch_glow="deepglow2", name="mk_glow.jsx")
    jsx_builtin, _ = _build(xml_subs, tmp_path, intro, style={"hl_fill": DARK_FILL},
                            glitch_glow="builtin", name="mk_glow_builtin.jsx")

    assert "PEDG2" not in jsx
    assert "DG_MISS" not in jsx
    assert "REELSI_DG_MISS" not in jsx
    # «вместо него ставится то же, что жёлтому глитчу без Deep Glow»: это ровно
    # сборка режима «Встроенные», байт в байт — не похожая, а та же.
    assert jsx == jsx_builtin


@node
def test_2_без_свечения_deep_glow_как_сейчас(xml_subs, tmp_path):
    """2. Та же строка без fx: PEDG2 есть (как сейчас), причём в смешанной сборке —
    только у слова БЕЗ свечения: у слова со свечением блюр с глоу, у второго PEDG2.
    Цвет — тёмный (DARK_FILL), иначе плагина не было бы вовсе (доработка MK3)."""
    jsx, _ = _build(xml_subs, tmp_path, _mixed_intro(), style={"hl_fill": DARK_FILL},
                    glitch_glow="deepglow2", name="mk_mixed.jsx")

    assert "PEDG2" in jsx
    assert 'if(col=="yellow" && fx!="glow"){' in jsx
    tritone = _tritone_expected(jsx)
    got = _probe(jsx, tmp_path, "probe_mixed.js")
    assert got["yellow_glitch"] == _dg_expected() + tritone
    assert got["yellow_glitch_glow"] == _glow_expected() + tritone
    assert got["dg_miss"] == 0

    # И то же на сборке без свечения вовсе — ровно прежнее поведение (задание HD).
    jsx_plain, _ = _build(xml_subs, tmp_path, _plain_glitch_intro(),
                          style={"hl_fill": DARK_FILL}, glitch_glow="deepglow2",
                          name="mk_plain.jsx")
    got_plain = _probe(jsx_plain, tmp_path, "probe_plain.js")
    assert got_plain["yellow_glitch"] == _dg_expected() + _tritone_expected(jsx_plain)
    assert got_plain["dg_miss"] == 0


@node
def test_3_галка_возвращает_deep_glow_на_строку_со_свечением(xml_subs, tmp_path):
    """3. intro_dg_with_glow=True: слово со свечением снова получает PEDG2, а условие
    в .jsx — прежнее (по одному цвету, без оговорки про fx). Цвет — тёмный (DARK_FILL):
    на ярком жёлтом галка не возвращает ничего (доработка MK3, тесты 5 и 8)."""
    jsx, _ = _build(xml_subs, tmp_path, _glow_glitch_intro(),
                    style={"intro_dg_with_glow": True, "hl_fill": DARK_FILL},
                    glitch_glow="deepglow2", name="mk_knob.jsx")

    assert "PEDG2" in jsx
    # Условие жёлтой ветки — прежнее: только по цвету, без оговорки про fx. Смотрим
    # именно тело introAnimFX: такая же оговорка есть у свечения строки (introHlGlow),
    # и по всему .jsx она нашлась бы не в том месте.
    src = _anim_fx_src(jsx)
    assert 'if(col=="yellow"){' in src
    assert 'fx!="glow"' not in src
    got = _probe(jsx, tmp_path, "probe_knob.js")
    assert got["yellow_glitch_glow"] == _dg_expected() + _tritone_expected(jsx)
    assert got["dg_miss"] == 0

    # Дефолт галки — False, и без неё то же слово плагина не получает (п. 1).
    assert styles.BASE["intro_dg_with_glow"] is False


def test_4_сторож_каждая_ручка_и_счётчики_схемы():
    """4. Сторож «каждая ручка» и счётчики схемы: новая галка посчитана везде.

    Ручка живёт в режиме Deep Glow (глобальная настройка ai_config), поэтому сторож
    tests/test_r11_li_every_knob.py кормит сборку этим режимом и держит в фикстуре жёлтый
    глитч: без жёлтой ветки ручка была бы «мёртвой» и выпала бы из проверки молча.
    """
    import test_r11_li_every_knob as r11
    import test_style_keys_in_ui as watcher

    assert styles.BASE["intro_dg_with_glow"] is False, "дефолт галки не False"
    field = watcher.schema_field("intro_dg_with_glow")
    assert field is not None, "ключ есть в BASE, а ручки в схеме нет"
    assert field["ctl"] == "bool", "intro_dg_with_glow перестал быть галкой"
    assert field["label"] == "Deep Glow вместе со свечением строки"

    en = json.loads(io.open(os.path.join(ROOT, "static", "i18n", "en.json"),
                            encoding="utf-8").read())
    assert en[field["label"]] == "Deep Glow with line glow"
    assert en[field["tip"]].strip(), "у галки нет перевода подсказки"

    # Сторож «каждая ручка»: ключ в схеме, не в исключениях, и в его фикстуре есть
    # жёлтый глитч (без него жёлтой ветки Deep Glow в сборке нет вовсе).
    assert "intro_dg_with_glow" in r11.ALL_SCHEMA_KEYS
    assert "intro_dg_with_glow" not in r11.EXCEPTIONS
    assert r11.EXCEPTIONS == {}
    assert any(x.get("anim") == "glitch" and x.get("color") == "yellow"
               for x in r11.RICH_INTRO), "в фикстуре сторожа нет жёлтого глитча"

    # Счётчики схемы (те же числа стережёт tests/test_style_schema.py): +1 галка.
    keys, groups, toggles, fields = [], [], [], 0

    def walk(items):
        nonlocal fields
        for it in items:
            if it.get("type") == "group":
                groups.append(it["id"])
                if it.get("toggle"):
                    toggles.append(it["toggle"])
                walk(it.get("items", []))
            elif it.get("type") == "field":
                fields += 1
                keys.append(it["key"])
                if it.get("key2"):
                    keys.append(it["key2"])

    for layer in style_schema.LAYERS:
        if layer.get("toggle"):
            toggles.append(layer["toggle"])
        walk(layer.get("items", []))

    assert len(styles.BASE) == 158, "ключей в styles.BASE стало не 158"
    assert (len(keys), fields, len(groups), len(toggles)) == (147, 146, 39, 10)


def test_5_яркий_цвет_жёлтого_deep_glow_не_ставится(xml_subs, tmp_path):
    """5. Яркий ЗАДАННЫЙ цвет жёлтого (задание MK2, доработка MK3; [1,0.97,0.08] — яркость
    0.91): Deep Glow не берёт слово глитча даже БЕЗ свечения строки, и галка
    intro_dg_with_glow его не возвращает.

    Цвет приходит из стиля — проверяем оба ключа: hl_fill и intro_hl_fill (своя подстановка
    интро перебивает общий). Вместо плагина слову идёт ровно то, что жёлтому глитчу в режиме
    «Встроенные», — сборка побайтово равна сборке с glitch_glow="builtin". Стоковый жёлтый
    стиля глушит плагин ровно так же — тест 8.
    """
    intro = _plain_glitch_intro()
    for style, tag in (({"hl_fill": YELLOW_BRIGHT}, "hl_fill"),
                       ({"intro_hl_fill": YELLOW_BRIGHT}, "intro_hl_fill")):
        jsx, _ = _build(xml_subs, tmp_path, intro, glitch_glow="deepglow2",
                        style=style, name=f"mk2_bright_{tag}.jsx")
        assert "PEDG2" not in jsx, tag
        assert "DG_MISS" not in jsx, tag
        assert "REELSI_DG_MISS" not in jsx, tag

        jsx_builtin, _ = _build(xml_subs, tmp_path, intro, glitch_glow="builtin",
                                style=style, name=f"mk2_bright_{tag}_builtin.jsx")
        assert jsx == jsx_builtin, tag

        jsx_knob, _ = _build(xml_subs, tmp_path, intro, glitch_glow="deepglow2",
                             style=dict(style, intro_dg_with_glow=True),
                             name=f"mk2_bright_{tag}_knob.jsx")
        assert "PEDG2" not in jsx_knob, f"{tag}: галка вернула Deep Glow на ярком цвете"


@node
def test_6_тёмный_цвет_правило_mk_как_было(xml_subs, tmp_path):
    """6. Тёмный цвет жёлтого (голубой 0.61 и тёмно-серый 0.22) — правило MK в силе: слово
    глитча БЕЗ свечения берёт PEDG2 (а за ним и Tritone: цвет ниже порога), слово со
    свечением — нет, ему идёт встроенный Blur + Glo2."""
    for fill, tag in ((CYAN, "cyan"), (DARK_GRAY, "gray")):
        jsx, _ = _build(xml_subs, tmp_path, _plain_glitch_intro(), glitch_glow="deepglow2",
                        style={"hl_fill": fill}, name=f"mk2_dark_{tag}.jsx")
        assert "PEDG2" in jsx, tag
        tritone = _tritone_expected(jsx)
        got = _probe(jsx, tmp_path, f"probe_dark_{tag}.js")
        assert got["yellow_glitch"] == _dg_expected() + tritone, tag
        assert got["yellow_glitch_glow"] == _glow_expected() + tritone, tag
        assert got["dg_miss"] == 0, tag


def test_7_яркость_считается_той_же_функцией_и_порогом():
    """7. Проверка яркости у Deep Glow — та же, что у Tritone (задание MK2, доработка MK3):
    порог TRITONE_MAX_LUM и функция `_tritone_on`, второй копии формулы Rec.709 в сборке нет.

    Стоковый жёлтый стиля ([1,0.9176,0] — 0.87) ярче порога — и глушит Deep Glow ровно так
    же, как заданный яркий (MK3; поведение — тест 8), а не как раньше: правило «только
    заданный цвет» у стилей со стоковым жёлтым не срабатывало вовсе. Не задан цвет и вовсе
    (`None`) — `_tritone_on` берёт тот же стоковый жёлтый: тоже не глушит.
    """
    assert TRITONE_MAX_LUM == 0.7
    assert _tritone_on(YELLOW_BRIGHT) is False
    assert _tritone_on(CYAN) is True
    assert _tritone_on(DARK_GRAY) is True
    assert _tritone_on(None) is False
    assert _tritone_on(styles.BASE["hl_fill"]) is False

    src = io.open(os.path.join(ROOT, "core", "xml2ae", "build.py"), encoding="utf-8").read()
    assert src.count("0.2126 *") == 1, "формула яркости Rec.709 размножилась в build.py"
    assert src.count("0.7152 *") == 1, "формула яркости Rec.709 размножилась в build.py"
    assert src.count("0.0722 *") == 1, "формула яркости Rec.709 размножилась в build.py"


def test_8_стоковый_жёлтый_deep_glow_не_ставится(xml_subs, tmp_path):
    """8. Стиль без цвета вовсе — стоковый жёлтый [1,0.9176,0] (0.87, ярче порога): правило
    ОДНО, Deep Glow не ставится и здесь (доработка MK3). Исключения «только заданный цвет»
    больше нет: у владельца стоковый жёлтый в большинстве стилей, и с ним правило для них не
    работало бы вовсе.

    Проверяем оба жёлтых слова — глитч без свечения и глитч со свечением: в .jsx нет ни
    PEDG2, ни DG_MISS, сборка побайтово равна режиму «Встроенные», и галка intro_dg_with_glow
    плагин не возвращает.
    """
    for intro, tag in ((_plain_glitch_intro(), "plain"), (_glow_glitch_intro(), "glow")):
        jsx, _ = _build(xml_subs, tmp_path, intro, style={}, glitch_glow="deepglow2",
                        name=f"mk3_stock_{tag}.jsx")
        assert "PEDG2" not in jsx, tag
        assert "DG_MISS" not in jsx, tag
        assert "REELSI_DG_MISS" not in jsx, tag

        jsx_builtin, _ = _build(xml_subs, tmp_path, intro, style={}, glitch_glow="builtin",
                                name=f"mk3_stock_{tag}_builtin.jsx")
        assert jsx == jsx_builtin, tag

        jsx_knob, _ = _build(xml_subs, tmp_path, intro, style={"intro_dg_with_glow": True},
                             glitch_glow="deepglow2", name=f"mk3_stock_{tag}_knob.jsx")
        assert "PEDG2" not in jsx_knob, f"{tag}: галка вернула Deep Glow на стоковом жёлтом"
