# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""tools/intro_eval.py на фикстуре: замер строится из трёх сайдкаров клипа, а ручка
длины строки берётся из СТИЛЯ клипа (--style перекрывает). Данные владельца сюда не
попадают — всё собирается в tmp_path, стили — тоже (STYLE_DIR подменён)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import styles  # noqa: E402
import tools.intro_eval as ev  # noqa: E402


def _dump(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def _clip(tmp_path, style_key):
    """Клип с хуком из двух строк: белая «НИ В КОЕМ СЛУЧАЕ» и жёлтая «ДАВАЙТЕ ПОГОВОРИМ»."""
    stem = str(tmp_path / "clip")
    words = ["НИ", "В", "КОЕМ", "СЛУЧАЕ", "ДАВАЙТЕ", "ПОГОВОРИМ"]
    rows = [{"count": 4, "color": "white", "break": True},
            {"count": 2, "color": "yellow", "break": True}]
    _dump(stem + ".words.json", [{"w": w} for w in words])
    _dump(stem + ".clip.json", {"clip": {"job": {"introRows": rows, "styleKey": style_key}}})
    _dump(stem + ".intro.json", {"intro_rows": rows, "mid_groups": []})
    return stem + ".intro.json"


def test_ручка_по_умолчанию_из_base_и_строка_цела(tmp_path, monkeypatch):
    """Клип без стиля: ручка BASE (20). «НИ В КОЕМ СЛУЧАЕ» (16) и «ДАВАЙТЕ ПОГОВОРИМ»
    (18) влезают — по две строки, как у владельца."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    rep = ev.clip_report(_clip(tmp_path, None))
    assert rep is not None
    assert rep["row_max"] == 20
    assert rep["owner_lines"] == 2
    assert rep["rows_lines"] == 2


def test_ручка_берётся_из_стиля_клипа(tmp_path, monkeypatch):
    """Стиль клипа с ручкой 9 режет те же строки: «НИ В КОЕМ | СЛУЧАЕ», «ДАВАЙТЕ | ПОГОВОРИМ»."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    styles.save("Узкий", {"intro_row_max": 9})
    rep = ev.clip_report(_clip(tmp_path, "Узкий"))
    assert rep["row_max"] == 9
    assert rep["rows_lines"] == 4


def test_style_перекрывает_стиль_клипа(tmp_path, monkeypatch):
    """--style (аргумент замера) важнее стиля, записанного в клипе."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    styles.save("Узкий", {"intro_row_max": 9})
    rep = ev.clip_report(_clip(tmp_path, None), style_override="Узкий")
    assert rep["row_max"] == 9
    assert rep["rows_lines"] == 4


def test_без_пары_сайдкаров_клип_пропускается(tmp_path, monkeypatch):
    """Нет .clip.json — клип не попадает в замер (None), а не роняет прогон."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    path = _clip(tmp_path, None)
    os.remove(path[: -len(".intro.json")] + ".clip.json")
    assert ev.clip_report(path) is None


def test_слова_приводятся_к_верхнему_регистру(tmp_path, monkeypatch):
    """В бою cmd_intro получает слова из XML верхним регистром; на строчных склейка
    служебных слов не срабатывает и замер врёт. Поэтому слова хука идут в правила ВЕРХНИМ."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    seen = []
    real = ev.new_rules

    def spy(rows, words, row_max):
        seen.append([w[1] for w in words])
        return real(rows, words, row_max)

    monkeypatch.setattr(ev, "new_rules", spy)
    stem = str(tmp_path / "low")
    _dump(stem + ".words.json", [{"w": w} for w in ["ни", "в", "коем", "случае"]])
    _dump(stem + ".clip.json", {"clip": {"job": {"introRows": [
        {"count": 4, "color": "white", "break": True}]}}})
    _dump(stem + ".intro.json", {"intro_rows": [
        {"count": 4, "color": "white", "break": True}], "mid_groups": []})
    assert ev.clip_report(stem + ".intro.json") is not None
    assert seen and all(w == w.upper() for ws in seen for w in ws), seen


def test_правленый_хук_отличается_от_неправленого(tmp_path, monkeypatch):
    """edited — границы итога владельца не совпали с границами строк ИИ как есть."""
    monkeypatch.setattr(styles, "STYLE_DIR", str(tmp_path / "styles"))
    same = _clip(tmp_path, None)                     # владелец = ИИ (одинаковые строки)
    assert ev.clip_report(same)["edited"] is False
    other = tmp_path / "other"
    other.mkdir()
    stem = str(other / "clip")
    words = ["НИ", "В", "КОЕМ", "СЛУЧАЕ", "ДАВАЙТЕ", "ПОГОВОРИМ"]
    _dump(stem + ".words.json", [{"w": w} for w in words])
    _dump(stem + ".clip.json", {"clip": {"job": {"introRows": [
        {"count": 6, "color": "white", "break": True}], "styleKey": None}}})
    _dump(stem + ".intro.json", {"intro_rows": [
        {"count": 4, "color": "white", "break": True},
        {"count": 2, "color": "yellow", "break": True}], "mid_groups": []})
    assert ev.clip_report(stem + ".intro.json")["edited"] is True
