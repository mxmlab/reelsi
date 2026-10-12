# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Шаг 3 панели сборки: «Только текущий» удалён, выбор движка — у кнопки рендера.

ПОЧЕМУ. Кнопка «Только текущий» (tojsx) дублировала «Собрать набор» на одном клипе и
владельцем не использовалась; её функция удалена вместе с телом. Выбор движка рендера
(«After Effects» / «Встроенный») жил в карточке «Папки и параметры», а решает он как раз
то, что жмут у «Собрать и отрендерить», — поэтому сегмент переехал в липкую панель сборки,
сразу за кнопку рендера. Статика здесь: тесты читают HTML и JS как текст, без браузера.
"""
import glob
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def html():
    return _read(os.path.join("templates", "index.html"))


def test_tojsx_is_gone_from_frontend_sources(html):
    """Функция tojsx и кнопка её вызова удалены из UI-кода: ни в шаблоне, ни в скриптах."""
    assert "tojsx" not in html, "в index.html осталась кнопка/вызов tojsx"
    for path in glob.glob(os.path.join(ROOT, "static", "app", "*.js")):
        with open(path, "r", encoding="utf-8") as f:
            assert "tojsx" not in f.read(), f"в {os.path.basename(path)} остался tojsx"


def test_current_only_button_is_gone(html):
    """Подпись «Только текущий» не выводится ни в шаблоне, ни в англ. словаре, ни в доках."""
    assert "Только текущий" not in html
    assert "Current only" not in _read(os.path.join("static", "i18n", "en.json"))
    for doc in ("ARCHITECTURE.md", "ARCHITECTURE.en.md"):
        text = _read(os.path.join("docs", doc))
        assert "Только текущий" not in text and "Current only" not in text, doc


def test_engine_segment_is_in_build_bar_right_after_render_button(html):
    """#rendengine — внутри #aebuildbar, сразу после #renderbtn и до сегмента «Отдельные .jsx»."""
    bar = html.index('id="aebuildbar"')
    aecfg = html.index('<div class="card" id="aecfg"')   # панель закрыта до карточки настройки
    render_btn = html.index('id="renderbtn"')
    engine = html.index('id="rendengine"')
    multimode = html.index('name="multimode"')
    assert bar < render_btn < engine < multimode < aecfg, (
        "#rendengine должен стоять в #aebuildbar сразу после #renderbtn")
    assert html.count('id="rendengine"') == 1, "сегмент движка должен быть один"


def test_engine_segment_is_not_in_folders_card(html):
    """В карточке «Папки и параметры» движка больше нет — остаётся только папка вывода."""
    folders = html.index("Папки и параметры")
    assert 'id="rendengine"' not in html[folders:], (
        "#rendengine снова в карточке «Папки и параметры»")
    assert 'id="aerenderdir"' in html[folders:], "папка вывода ушла из карточки"


def test_engine_inputs_keep_names_values_and_handler(html):
    """id/name/значения сегмента не менялись: их читают rendEngine(), stateObj и тесты движка."""
    seg = html[html.index('id="rendengine"'):]
    seg = seg[:seg.index("</div>")]
    assert seg.count('name="rendengine"') == 2
    assert 'name="rendengine" value="ae" checked onchange="rendEngineUI()"' in seg
    assert 'name="rendengine" value="builtin" onchange="rendEngineUI()"' in seg


def test_result_line_stays_in_build_bar(html):
    """#aeres (строка результата сборки) остаётся в панели: её пишут startBuild/pollBuild."""
    bar = html.index('id="aebuildbar"')
    aecfg = html.index('<div class="card" id="aecfg"')
    assert bar < html.index('id="aeres"') < aecfg


def test_folder_hint_points_to_engine_next_to_render_button(html):
    """Подсказка у папки вывода больше не отсылает к «ниже»: движок теперь у кнопки рендера."""
    m = re.search(r'<label for="aerenderdir">[^<]*<span class="i" data-t="([^"]*)"', html)
    assert m, "подсказка у папки вывода не найдена"
    hint = m.group(1)
    assert "ниже" not in hint
    assert "рядом с кнопкой «Собрать и отрендерить»" in hint


def test_collect_jobs_is_the_only_job_source_for_build_and_render():
    """Одиночного пути сборки нет: и «Собрать набор», и рендер берут задания из collectJobs."""
    js = _read(os.path.join("static", "app", "90-ae.js"))
    assert js.count("collectJobs()") >= 3, "collectJobs должен звать сборка набора и рендер"
    assert "await collectJobs()" in js[js.index("async function startRender("):]
    assert "await collectJobs()" in js[js.index("async function buildMulti("):]
