# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Признак front группы интро доезжает до плана, а превью кладёт группу над видео.

В AE группа интро, чьё окно [ts, te] пересекается с видеовставкой, получает признак front:
её прекомп после раскладки по layer_order уносится moveToBeginning НАД всем
(xml2ae/build.py _intro_front_at -> INTRO_FRONT, template.py intro_front_raise).

В превью признак не отдавался: словарь группы в plan.intro не содержал front, и слой
интро всегда получал место из layer_order — ниже видео (дефолтный порядок
['subs','video','roto','photo','intro'] даёт интро 6, видео 9). Текст был закрыт видео
и не хватался мышью, хотя в AE лежал сверху.

Что ловим:
  * plan.intro[].front — ровно то значение, что уходит в _intro_front (и в INTRO_FRONT);
  * группу без пересечения не поднимаем, без видеовставок фронта нет ни у кого;
  * признак НЕ кладётся в строки lines: они уезжают в .jsx как INTRO_GROUPS, и он
    обязан остаться байт в байт прежним (golden);
  * фронт: introGroupWindows протаскивает front, ipvIntro ставит zIndex (front -> 11,
    иначе слой из layer_order), а ipvSubs слой интро больше не трогает.
"""
import gzip
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402

JS_PATH = os.path.join(ROOT, "static", "app", "85-inserts-view.js")


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(xml_subs, intro, inserts=None, splits=None):
    return xml2ae.scene_plan(xml_subs, disclaimer="", intro=intro,
                             intro_splits=[] if splits is None else splits,
                             inserts=inserts or [], style={})


def _video(start_s=1.2, dur_s=2.0):
    """Видеовставка — только она (не фото) даёт группе интро признак front."""
    return dict(type="video", media="C:/x/v.mp4", start_s=start_s, dur_s=dur_s)


def test_группа_на_видеовставке_получает_front(xml_subs):
    """Окно группы [1.0, 1.5] пересекается с видео [1.2, 3.2] -> front=True."""
    intro = [dict(words=["ПЕРВОЕ", "СЛОВО"], times=[1.0, 1.5])]
    plan = _plan(xml_subs, intro, inserts=[_video()])
    assert plan["intro"][0]["front"] is True


def test_вторая_группа_без_пересечения_не_поднята(xml_subs):
    """Две группы: первая на видео, вторая (4.0-4.5) мимо -> [True, False]."""
    intro = [
        dict(words=["ПЕРВАЯ", "ГРУППА"], times=[1.0, 1.5]),
        dict(words=["ВТОРАЯ", "ГРУППА"], times=[4.0, 4.5]),
    ]
    plan = _plan(xml_subs, intro, inserts=[_video(start_s=1.2, dur_s=1.0)], splits=[1])
    assert [g["front"] for g in plan["intro"]] == [True, False]


def test_без_видеовставок_все_группы_не_подняты(xml_subs):
    """Без видеовставок front=False у каждой группы, и это именно bool, а не 0."""
    intro = [
        dict(words=["ПЕРВАЯ", "ГРУППА"], times=[1.0, 1.5]),
        dict(words=["ВТОРАЯ", "ГРУППА"], times=[2.0, 2.5]),
    ]
    plan = _plan(xml_subs, intro, splits=[1])
    assert [g["front"] for g in plan["intro"]] == [False, False]
    assert all(g["front"] is False for g in plan["intro"]), "front — bool, не int"


def test_в_строки_jsx_поле_front_не_попадает(xml_subs):
    """front живёт на группе, а строки lines уходят в .jsx как INTRO_GROUPS: в них
    поля быть не должно, иначе изменится .jsx (golden)."""
    intro = [
        dict(words=["ПЕРВАЯ", "ГРУППА"], times=[1.0, 1.5]),
        dict(words=["ВТОРАЯ", "ГРУППА"], times=[4.0, 4.5]),
    ]
    plan = _plan(xml_subs, intro, inserts=[_video(start_s=1.2, dur_s=1.0)], splits=[1])
    assert [g["front"] for g in plan["intro"]] == [True, False]
    for g in plan["intro"]:
        assert g["lines"], "группа без строк — проверять нечего"
        for ln in g["lines"]:
            assert "front" not in ln, "в .jsx-строки поле front не попадает"


def _fn_body(js, needle):
    """Тело функции верхнего уровня: от needle до следующего 'function ' в начале строки."""
    i = js.index(needle)
    return js[i:js.index("\nfunction ", i)]


def test_фронт_слой_интро_ставит_ipvintro():
    """Сторож фронта: introGroupWindows протаскивает front из плана, ipvIntro ставит
    zIndex (front -> 11, иначе слой из layer_order), в ipvSubs слоя интро больше нет."""
    js = open(JS_PATH, "r", encoding="utf-8").read()
    assert re.search(r"front:\s*!!g\.front", js), \
        "introGroupWindows обязан протащить front из плана"
    intro = _fn_body(js, "function ipvIntro(tm){")
    assert "zIndex" in intro and ".front" in intro, \
        "ipvIntro обязан ставить zIndex по признаку front"
    assert re.search(r"zIndex\s*=\s*g\.front\s*\?\s*'11'\s*:\s*String\(zIntro\)", intro), \
        "front-группа — 11 (выше любого layer_order), остальные — свой слой zIntro"
    assert re.search(r"const zIntro\s*=\s*i\s*>=\s*0\s*\?\s*\(?10\s*-\s*i\)?\s*:\s*0", intro), \
        "zIntro — та же формула, что getZ в ipvSubs (порядок из plan.layer_order)"
    subs = _fn_body(js, "function ipvSubs(tm){")
    assert "ipvintro" not in subs, "слой интро ставит только ipvIntro — в ipvSubs его нет"
