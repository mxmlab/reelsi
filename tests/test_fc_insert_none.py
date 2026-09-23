# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FC: фотовставки без анимации и без эффектов.

На роликах с ОДНОЙ камерой пользователю нужно «фото просто появилось и исчезло»:
при одной камере insert_style «авто» всегда даёт cam1 (вылет из-за спины — единственный
вариант), insert_anim применяется только у cam2, а insert_fx (card/white) оба вешают
Drop Shadow. Тест стережёт два новых значения:

- insert_anim="none": у cam2 anim.scale и anim.opacity ровно по одному ключу и blur
  отсутствует; у cam1 anim.position — один ключ, равный точке покоя (нижняя точка
  «за спиной» не строится); wiggle на такую вставку не вешается (подстановкой
  ins_wiggle в шаблоне, а не мёртвым полем плана);
- insert_fx="none": фото без Drop Shadow, Simple Choker и маски.

Дефолты zoom/card не меняются — собранный .jsx остаётся прежним (golden).

Запуск: python -m pytest reelsi/tests -q
"""
import json
import os
import re
import shutil
import subprocess
import sys
import gzip

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402
from core.xml2ae.layout import INS_C1_HIGH  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    """Распакованная копия эталонного XML с субтитрами (правки не трогают фикстуру)."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


def _ins_cam2(t0=2.0, dur=2.0, media="C:/x/a.png"):
    return {"type": "photo", "style": "cam2", "media": media, "start_s": t0, "dur_s": dur}


def _ins_cam1(t0=2.0, dur=2.0, media="C:/x/a.png"):
    return {"type": "photo", "style": "cam1", "media": media, "start_s": t0, "dur_s": dur}


def test_anim_none_cam2_single_keys_no_blur(xml_subs):
    """insert_anim='none' у cam2: scale и opacity по одному ключу, blur и position нет."""
    plan = xml2ae.scene_plan(xml_subs, inserts=[_ins_cam2()], style={
        "insert_anim": "none", "insert_style": "cam2", "insert_snap_cut": False,
        "intro_riser": False, "roto": False,
    })
    ins0 = plan["inserts"][0]
    anim = ins0["anim"]
    assert anim["scale"] == [[2.0, 44.0]], anim["scale"]      # осевший масштаб, один ключ
    assert anim["opacity"] == [[2.0, 100.0]], anim["opacity"]  # полная непрозрачность, один ключ
    assert "blur" not in anim
    assert "position" not in anim
    # Мёртвого поля wiggle в плане нет (замечание FC): дрожание гасится подстановкой
    # ins_wiggle в шаблоне, а не полем, которое никто не читает.
    assert "wiggle" not in ins0


def test_anim_none_cam1_single_key_rest_point(xml_subs):
    """insert_anim='none' у cam1: position — один ключ, равный точке ПОКОЯ (up из
    _cam1_pos_keys), нижняя точка «за спиной» (dn) не строится вовсе."""
    plan = xml2ae.scene_plan(xml_subs, inserts=[_ins_cam1()], style={
        "insert_anim": "none", "insert_style": "cam1", "insert_snap_cut": False,
        "intro_riser": False, "roto": False,
    })
    ins0 = plan["inserts"][0]
    anim = ins0["anim"]
    # up = [cx + ix, cy - INS_C1_HIGH + iy]; при дефолтах cx=cy=ix=iy=0 -> [0, -567]
    assert anim["position"] == [[2.0, [0.0, -INS_C1_HIGH]]], anim["position"]
    assert "wiggle" not in ins0                    # мёртвого поля в плане нет (см. cam2-тест)


def test_anim_none_cam1_respects_common_shift(xml_subs):
    """Точка покоя cam1 при none учитывает общий сдвиг insert_c1_x/y."""
    plan = xml2ae.scene_plan(xml_subs, inserts=[_ins_cam1()], style={
        "insert_anim": "none", "insert_style": "cam1", "insert_snap_cut": False,
        "intro_riser": False, "roto": False,
        "insert_c1_x": 30.0, "insert_c1_y": -40.0,
    })
    anim = plan["inserts"][0]["anim"]
    assert anim["position"] == [[2.0, [30.0, -40.0 - INS_C1_HIGH]]], anim["position"]


def test_anim_default_zoom_unchanged(xml_subs):
    """Дефолт insert_anim='zoom' даёт прежний план: у cam2 blur есть, wiggle не пишется
    вовсе (поле отсутствует — .jsx прежний, golden)."""
    plan = xml2ae.scene_plan(xml_subs, inserts=[_ins_cam2()], style={
        "insert_style": "cam2", "insert_snap_cut": False, "intro_riser": False,
        "roto": False,
    })
    ins0 = plan["inserts"][0]
    assert "blur" in ins0["anim"]
    assert len(ins0["anim"]["scale"]) > 1          # наезд: не один ключ
    assert "wiggle" not in ins0                    # дефолт: поле не пишется (golden)


def test_fx_none_jsx_has_no_effects(xml_nosubs, tmp_path):
    """insert_fx='none': в .jsx нет вызова insFX (Drop Shadow / Simple Choker) и маски.

    Проверяем по собранному .jsx: INS_FX равен "none", подстановки insfx_cam1/cam2 и
    ins_mask пусты (ни insFX(L,...), ни if (INS_FX!="white"){…roundMask…}), а wiggle
    не вешается (подстановка ins_wiggle пуста; поля wiggle в INSERTS нет — см. FC)."""
    out = str(tmp_path / "fx_none.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[_ins_cam2()], style={
        "insert_fx": "none", "insert_anim": "none", "insert_style": "cam2",
        "insert_snap_cut": False, "intro_riser": False, "roto": False,
    })
    txt = open(out, encoding="utf-8-sig").read()
    assert 'var INS_FX = "none";' in txt
    # ни эффектов (вызов insFX не подставлен), ни маски-скругления, ни wiggle на позиции
    assert 'insFX(L,"cam2");' not in txt
    assert 'insFX(L,"cam1");' not in txt
    assert 'if (INS_FX!="white"){' not in txt
    assert 'roundMask(L, (W-mw)/2' not in txt               # вызов маски не подставлен
    assert 'Position").expression="wiggle(1,15)"' not in txt
    # в INSERTS нет мёртвого поля wiggle: дрожание гасится подстановкой ins_wiggle
    ins = json.loads(re.search(r"var INSERTS=(\[.*?\]);", txt).group(1))
    assert "wiggle" not in ins[0]
    # у дефолтной cam2-вставки эффекты и маска были бы (контроль, что подстановки живые)
    assert 'addMosaic(L, ins.mosaic);' in txt


def test_fx_card_jsx_still_has_mask(xml_nosubs, tmp_path):
    """Дефолт insert_fx='card' не тронут: вызов insFX и маска-скругление остаются
    (подстановки непустые — .jsx прежний, golden)."""
    out = str(tmp_path / "fx_card.jsx")
    xml2ae.to_ae_full(xml_nosubs, out, inserts=[_ins_cam2()], style={
        "insert_anim": "zoom", "insert_style": "cam2",
        "insert_snap_cut": False, "intro_riser": False, "roto": False,
    })
    txt = open(out, encoding="utf-8-sig").read()
    assert 'var INS_FX = "card";' in txt
    assert 'insFX(L,"cam2");' in txt
    assert 'if (INS_FX!="white"){' in txt
    assert 'roundMask(' in txt
    assert 'Position").expression="wiggle(1,15)"' in txt


def _keys_at_script():
    """Вырезать keysAt из 85-inserts-view.js (боевая функция предпросмотра)."""
    src = open(os.path.join(ROOT, "static", "app", "85-inserts-view.js"),
               encoding="utf-8").read()
    m = re.search(r"function keysAt\(.*?\n}", src, re.S)
    assert m, "keysAt не нашлась в 85-inserts-view.js"
    return m.group(0)


def test_preview_draws_single_key_animation_as_static():
    """Однокейфреймовая анимация (insert_anim='none') в предпросмотре — статика.

    Предпросмотр интерполирует готовые ключи плана функцией keysAt:
    массив из ОДНОГО ключа [[t0, S]] обязан давать S на любом времени — до, в и после
    окна вставки. Если интерполятор начнёт что-то «досчитывать» на одном ключе, фото
    будет прыгать — а требуется, чтобы оно просто стояло."""
    helper = _keys_at_script()
    out = subprocess.run(
        ["node", "-e",
         helper
         + "\nvar r=[keysAt([[2,44]],null,0),keysAt([[2,44]],null,2),"
         + "keysAt([[2,44]],null,3.5),keysAt([[2,[30,-607]]],null,1),"
         + "keysAt([[2,[30,-607]]],null,4)];console.log(JSON.stringify(r));"],
        capture_output=True, text=True, encoding="utf-8-sig", errors="replace",
        timeout=60)
    assert out.returncode == 0, out.stderr.strip()[:400]
    assert json.loads(out.stdout) == [44, 44, 44, [30, -607], [30, -607]], (
        "однокейфреймовая анимация не рисуется как статика")
