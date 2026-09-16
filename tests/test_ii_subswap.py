# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задания II:

1. Ключ стиля insert_sub_swap (дефолт True):
   - styles.BASE["insert_sub_swap"] is True
   - при insert_anim == "rise" и insert_sub_swap == False: sub_hide == []
   - при insert_sub_swap == True или без ключа: 4 ключа сокрытия субтитров.

2. Подхват жёлтых ИИ (борьба с пустым списком слов в UI):
   - в loadWordsFor (90-ae.js) есть ветка для совпавшего HLXML с пустым HL, берущая d.yellow и зовущая captureAE();
   - в 70-editor.js после вызова /api/ai_yellow оба места вызывают clearHl(c) и loadWordsFor(c.xml) для открытого ролика.
"""

import gzip
import os
import re
import shutil
import sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import styles
from core.xml2ae.build import scene_plan


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_styles_base_insert_sub_swap_default():
    """styles.BASE['insert_sub_swap'] равен True по умолчанию."""
    assert styles.BASE.get("insert_sub_swap") is True
    res = styles.resolve(None)
    assert res.get("insert_sub_swap") is True
    res_off = styles.resolve({"insert_sub_swap": False})
    assert res_off.get("insert_sub_swap") is False


def test_subswap_sub_hide_disabled_when_false(xml_subs):
    """При insert_sub_swap: False и rise субтитры НЕ скрываются (sub_hide пуст)."""
    st = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
        "insert_sub_swap": False,
    }
    inserts = [
        {"type": "photo", "media": "p1.png", "start_s": 6.5, "dur_s": 2.3, "style": "cam2"},
        {"type": "photo", "media": "p2.png", "start_s": 8.0, "dur_s": 2.5, "style": "cam2"},
    ]
    plan = scene_plan(xml_subs, inserts=inserts, style=st)
    assert "sub_hide" in plan
    assert plan["sub_hide"] == []


def test_subswap_sub_hide_enabled_by_default_and_true(xml_subs):
    """По образцу test_dd_rise:99 — при True или без ключа сохраняются прежние 4 ключа."""
    inserts = [
        {"type": "photo", "media": "p1.png", "start_s": 6.5, "dur_s": 2.3, "style": "cam2"},
        {"type": "photo", "media": "p2.png", "start_s": 8.0, "dur_s": 2.5, "style": "cam2"},
    ]
    expected_hide = [
        [6.5, 100.0],
        [7.0, 0.0],
        [10.03, 0.0],
        [10.5, 100.0],
    ]

    # 1. Без ключа (дефолт True)
    st_default = {
        "insert_anim": "rise",
        "insert_c2_y": 0.6,
        "insert_style": "cam2",
        "insert_snap_cut": False,
        "intro_riser": False,
        "roto": False,
    }
    plan_default = scene_plan(xml_subs, inserts=inserts, style=st_default)
    assert plan_default["sub_hide"] == expected_hide

    # 2. Явно True
    st_true = dict(st_default, insert_sub_swap=True)
    plan_true = scene_plan(xml_subs, inserts=inserts, style=st_true)
    assert plan_true["sub_hide"] == expected_hide


def test_load_words_for_handles_empty_hl_on_same_xml():
    """В loadWordsFor (90-ae.js) есть ветка для совпавшего HLXML с пустым HL, берущая d.yellow и зовущая captureAE."""
    ae_js_path = os.path.join(ROOT, "static", "app", "90-ae.js")
    with open(ae_js_path, encoding="utf-8") as f:
        src = f.read()

    # Ищем тело loadWordsFor
    m = re.search(r"async\s+function\s+loadWordsFor\s*\([^)]*\)\s*\{([\s\S]*?)\n\}", src)
    assert m, "loadWordsFor не найдена в 90-ae.js"
    body = m.group(1)

    # Проверяем ветку для HL.size === 0 (или !HL.size), где берутся d.yellow и вызывается captureAE()
    assert re.search(r"(?:HL\.size\s*===\s*0|!HL\.size)[\s\S]*?HL\s*=\s*new Set\(d\.yellow\)", body), \
        "В loadWordsFor отсутствует подхват d.yellow при пустом HL и совпавшем HLXML"
    assert re.search(r"(?:HL\.size\s*===\s*0|!HL\.size)[\s\S]*?captureAE\(\)", body), \
        "В loadWordsFor отсутствует вызов captureAE() после подхвата d.yellow при пустом HL"


def test_editor_yellow_phase_calls_clear_hl():
    """В 70-editor.js после вызова /api/ai_yellow оба места вызывают clearHl(c)."""
    ed_js_path = os.path.join(ROOT, "static", "app", "70-editor.js")
    with open(ed_js_path, encoding="utf-8") as f:
        src = f.read()

    # Находим все вызовы aiPost('/api/ai_yellow'...)
    matches = list(re.finditer(r"aiPost\s*\(\s*['\"]/api/ai_yellow['\"]", src))
    assert len(matches) == 2, f"Ожидалось 2 вызова /api/ai_yellow в 70-editor.js, найдено: {len(matches)}"

    for i, m in enumerate(matches, 1):
        # Смотрим фрагмент кода после каждого вызова ai_yellow
        snippet = src[m.end():m.end() + 350]
        assert "clearHl(c)" in snippet, f"Вызов {i} /api/ai_yellow не вызывает clearHl(c)"
        assert "loadWordsFor(c.xml)" in snippet, f"Вызов {i} /api/ai_yellow не перезагружает loadWordsFor(c.xml)"
