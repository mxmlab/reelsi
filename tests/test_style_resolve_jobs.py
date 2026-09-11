# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракт задания EX1: _norm_build_jobs резолвит стиль ОДИН раз на клип и берёт
рото и music_db ИЗ СТИЛЯ, а не из полей задания (клип хранит имя стиля, стиль
живёт отдельно — решение 2026-08-22). Клиповые roto/roto_bottom не читаются вовсе.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


@pytest.fixture()
def custom_style(tmp_path, monkeypatch):
    """Стиль с отличным от дефолта music_db: встроенные base/geologica оба −20,
    а для проверки «−20 молча» нужен стиль с другим значением."""
    from core import styles
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    monkeypatch.setattr(styles, "STYLE_DIR", str(styles_dir))
    with open(styles_dir / "custom.json", "w", encoding="utf-8") as f:
        json.dump({"label": "custom", "music_db": -27.0, "roto_bottom": 0.42}, f)
    return styles


def _norm(jobs):
    from api.build import _norm_build_jobs
    return _norm_build_jobs(jobs)


def test_геologica_строкой_даёт_своё_roto_bottom(xml_nosubs):
    """Стиль пришёл ИМЕНОМ — roto_bottom берётся из пресета (0.25), а не −20/0."""
    out = _norm([{"xml": xml_nosubs, "style": "geologica"}])[0]
    assert out["roto_bottom"] == 0.25
    assert out["roto"] is True


def test_base_строкой_даёт_своё_roto_bottom(xml_nosubs):
    out = _norm([{"xml": xml_nosubs, "style": "base"}])[0]
    assert out["roto_bottom"] == 0.35
    assert out["roto"] is True


def test_клиповые_рото_игнорируются_берётся_из_стиля(xml_nosubs):
    """Клиповые roto/roto_bottom больше не читаются: что бы ни лежало в задании,
    на выходе — значения стиля (base: roto=True, roto_bottom=0.35)."""
    out = _norm([{"xml": xml_nosubs, "style": "base",
                  "roto": False, "roto_bottom": 0.99}])[0]
    assert out["roto"] is True, "клиповое roto=False перебило стиль"
    assert out["roto_bottom"] == 0.35, "клиповое roto_bottom=0.99 перебило стиль"


def test_music_db_строкой_не_превращается_в_минус20(custom_style, xml_nosubs):
    """Стиль-СТРОКА с music_db ≠ −20 отдаёт своё значение, а не молчаливый дефолт:
    словарь {"music_db": -27} и стиль-имя с тем же значением дают одно и то же."""
    by_dict = _norm([{"xml": xml_nosubs, "style": {"music_db": -27.0}}])[0]
    by_name = _norm([{"xml": xml_nosubs, "style": "custom"}])[0]
    assert by_dict["music_db"] == -27.0
    assert by_name["music_db"] == -27.0
    assert by_dict["music_db"] == by_name["music_db"]


def test_style_none_даёт_базовый_стиль_а_не_нули(xml_nosubs):
    """Без стиля (None) — значения базового пресета (styles.resolve(None)), а не нули."""
    out = _norm([{"xml": xml_nosubs, "style": None}])[0]
    assert out["roto_bottom"] == 0.35
    assert out["roto"] is True
    assert out["music_db"] == -20.0
