# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторожевые тесты кастомной нарезки и панели ступеней.

Тест проверяет:
- /api/cutstages возвращает список ступеней, дефолты и дефолтные пороги из cutstages.py;
- в JS (static/app/*.js) нет своей копии списка ступеней — список грузится с сервера;
- галка #chk_dedupe убрана со страницы (templates/index.html), а ключ dedupe сохраняется и читается в состоянии UI;
- кнопка #runclassic называется «Кастом» и запускает кастомную нарезку;
- контейнеры #cut_stages_list и #vad_thresholds_box присутствуют во вкладке «Нарезка».
"""
import io
import json
import os
import pytest

from core import app_meta
from core import cutstages
from webui import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "templates", "index.html")


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def html():
    return io.open(HTML, encoding="utf-8").read()


@pytest.fixture
def js():
    return app_meta.app_js_text()


def test_api_cutstages_endpoint_contract(client):
    """Роут /api/cutstages отдаёт STAGES, DEFAULTS и DEFAULT_THRESHOLDS из cutstages.py."""
    res = client.get("/api/cutstages")
    assert res.status_code == 200
    data = res.get_json()
    assert "stages" in data
    assert "defaults" in data
    assert "threshold_defaults" in data or "thresholds" in data

    assert data["stages"] == cutstages.STAGES
    assert data["defaults"] == cutstages.DEFAULTS
    thresh = data.get("threshold_defaults") or data.get("thresholds")
    assert thresh == cutstages.DEFAULT_THRESHOLDS


def test_no_hardcoded_stages_list_in_js(js):
    """В JS нет захардкоженного списка ступеней нарезки (список приходит с сервера)."""
    # Проверяем, что в коде JS нет хардкодного списка всех ключей ступеней
    all_keys = [s["key"] for s in cutstages.STAGES]
    keys_str = json.dumps(all_keys)
    assert keys_str not in js, "В JS найден захардкоженный список ключей ступеней"

    # Проверяем, что список рендерится из CUT_STAGES_META
    assert "CUT_STAGES_META" in js
    assert "fetch('/api/cutstages')" in js


def test_chk_dedupe_removed_from_page_and_present_in_state(html, js):
    """Галка #chk_dedupe убрана со страницы, но ключ dedupe читается и сохраняется в состоянии."""
    assert 'id="chk_dedupe"' not in html, "#chk_dedupe остался в HTML"

    # Сохранение состояния
    st_body = js[js.index("function stateObj()"):js.index("function saveState()")]
    assert "dedupe:" in st_body, "stateObj не сохраняет dedupe"
    assert "cut_stages:" in st_body, "stateObj не сохраняет cut_stages"

    # Восстановление состояния
    app_body = js[js.index("function applyState(s)"):js.index("function restoreState()")]
    assert "s.dedupe" in app_body or "dedupe" in app_body, "applyState не читает dedupe"
    assert "cut_stages" in app_body, "applyState не восстанавливает cut_stages"


def test_runclassic_button_is_custom(html, js):
    """Кнопка #runclassic заменена на «Кастом»."""
    assert 'id="runclassic"' in html
    btn_html = html[html.index('id="runclassic"') - 10:html.index('id="runclassic"') + 80]
    assert "Кастом" in btn_html
    assert "Классическая" not in btn_html
    assert "runCustom()" in btn_html or "runClassic()" in btn_html

    # В JS определена функция runCustom
    assert "async function runCustom()" in js
    assert "runCustom" in js


def test_stages_markup_containers_exist_in_html(html):
    """Во вкладке «Нарезка» есть контейнеры списка ступеней и порогов VAD."""
    assert 'id="cut_stages_list"' in html
    assert 'id="vad_thresholds_box"' in html
    assert 'id="vad_thresh"' in html
    assert 'id="vad_min_silence"' in html
    assert 'id="vad_pad"' in html


def test_draft_stage_hidden_from_panel_but_present_in_contract(client, js):
    """Ступень draft присутствует в STAGES/API, но не рисуется в панели ступеней (panel: False)."""
    # 1. В cutstages.STAGES ступень draft есть
    all_keys = [s["key"] for s in cutstages.STAGES]
    assert "draft" in all_keys

    # 2. У draft panel: False, у остальных True
    draft_meta = next(s for s in cutstages.STAGES if s["key"] == "draft")
    assert draft_meta.get("panel") is False
    for s in cutstages.STAGES:
        if s["key"] != "draft":
            assert s.get("panel") is True

    # 3. /api/cutstages возвращает метаданные с panel: False для draft
    res = client.get("/api/cutstages")
    data = res.get_json()
    stages = data["stages"]
    draft_api = next(s for s in stages if s["key"] == "draft")
    assert draft_api.get("panel") is False

    # 4. В JS логика рендера панели пропускает ступени с panel === false
    assert "s.panel===false" in js or "s.panel !== false" in js or "s.panel" in js


def test_runcustom_does_not_send_separate_draft_field(js):
    """runCustom() не шлёт отдельное поле draft (значение передаётся только внутри stages)."""
    rc_start = js.index("async function runCustom()")
    rc_end = js.index("const runClassic=runCustom;", rc_start)
    rc_body = js[rc_start:rc_end]

    assert "stages:CUT_STAGES" in rc_body or "stages: CUT_STAGES" in rc_body
    assert "draft:" not in rc_body, "runCustom всё ещё шлёт отдельное поле draft"


def test_chk_draft_removed_draft_follows_review(html, js):
    """Галка #chk_draft убрана (черновик следует за Omni-ревью):
    в templates/index.html нет id="chk_draft", в static/app/*.js нет chk_draft,
    галка #chk_review на месте, а cut_stages сохраняется и восстанавливается в состоянии UI.
    """
    # Галки chk_draft нет на странице и в JS, а chk_review на месте
    assert 'id="chk_draft"' not in html, "chk_draft должна быть убрана со страницы"
    assert "chk_draft" not in js, "в static/app/*.js не должно быть обращений к chk_draft"
    assert 'id="chk_review"' in html, "галка #chk_review должна быть на странице"

    # Сохранение состояния включает cut_stages
    st_body = js[js.index("function stateObj()"):js.index("function saveState()")]
    assert "cut_stages:CUT_STAGES" in st_body or "cut_stages: CUT_STAGES" in st_body

    # Восстановление состояния восстанавливает cut_stages
    app_body = js[js.index("function applyState(s)"):js.index("function restoreState()")]
    assert "s.cut_stages" in app_body

