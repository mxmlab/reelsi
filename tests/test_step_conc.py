# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OV: «Роликов одновременно» — настройка НА ШАГЕ (нарезка и разметка).

Число одновременных запросов задавалось только в профиле модели (⚙ → «Подключения»,
поле «Одновременных запросов»), и найти там настройку шага было нельзя. Теперь у шага
есть СВОЁ переопределение (`ai_config.step_concurrency_override`, действие
`set_step_concurrency`), а профильное поле осталось умолчанием:

1. переопределение шага (1..16) сильнее числа из профиля; снятое/битое — снова профиль;
2. нарезку это не распараллеливает: `cut_parallel_width` с локальной моделью даёт 1
   при любом переопределении (локальная модель делит видеокарту с распознаванием);
3. действие `set_step_concurrency`: чужой шаг — `unknown_step`, «20» — `bad_concurrency`,
   пусто/None — переопределение снято;
4. фронт: поле `conc_cut` живёт на вкладке «Нарезка», `conc_markup` — на «Разметка»,
   и строка разметки пишет одно число в ОБА шага (`yellow` и `inserts`).

Запуск: python -m pytest tests/test_step_conc.py -q
"""
from __future__ import annotations

import copy
import io
import os
import re
import sys
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.aicut import config as ai_config  # noqa: E402
from core.aicut import config_actions as ai_actions  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

HTML = os.path.join(ROOT, "templates", "index.html")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")

# Облачный профиль с числом 6: переопределение шага обязано победить именно его.
CLOUD_CFG: dict[str, Any] = {
    "active": "OR",
    "profiles": {"OR": {"provider": "openrouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "model": "deepseek/deepseek-v4-flash",
                        "concurrency": 6}},
    "step_profiles": {"cut": "OR", "yellow": "OR"},
}

# Локальная модель нарезки: видеокарту делит с распознаванием — параллелить нельзя.
LOCAL_CFG: dict[str, Any] = {
    "active": "LM Studio",
    "profiles": {"LM Studio": {"provider": "lmstudio",
                               "base_url": "http://localhost:1234/v1",
                               "model": "qwen3.6-27b-4bpw-16gb-vram"}},
    "step_profiles": {"cut": "LM Studio"},
}


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, cfg: dict[str, Any]) -> dict[str, Any]:
    """Подменить конфиг целиком: тест правит его на месте и видит результат сразу."""
    monkeypatch.setattr(ai_config, "load_ai_config", lambda: cfg)
    return cfg


# ---- 1. Переопределение шага сильнее профиля -------------------------------

def test_step_override_wins_over_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """override 2 -> 2 при профиле с concurrency 6; снят/битый -> снова 6."""
    cfg = _patch_cfg(monkeypatch, copy.deepcopy(CLOUD_CFG))
    assert ai_config.step_concurrency("cut") == 6          # без переопределения — профиль

    cfg["step_concurrency_override"] = {"cut": 2}
    assert ai_config.step_concurrency("cut") == 2
    # Другой шаг переопределение не задевает
    assert ai_config.step_concurrency("yellow") == 6

    del cfg["step_concurrency_override"]
    assert ai_config.step_concurrency("cut") == 6

    # Битое значение — не число, вне 1..16 или bool: шаг живёт по профилю, не падает
    for bad in ("2", "мусор", 0, 17, True, None, 2.5):
        cfg["step_concurrency_override"] = {"cut": bad}
        assert ai_config.step_concurrency("cut") == 6, f"битое {bad!r} победило профиль"


# ---- 2. Нарезку переопределение не распараллеливает ------------------------

def test_local_cut_stays_one_with_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Локальная модель нарезки + переопределение 8 -> ширина пула всё равно 1."""
    cfg = _patch_cfg(monkeypatch, copy.deepcopy(LOCAL_CFG))
    cfg["step_concurrency_override"] = {"cut": 8}
    assert ai_config.step_concurrency("cut") == 8          # число шага видно
    assert ai_config.cut_parallel_width("gigaam", False, 10) == 1


# ---- 3. Действие set_step_concurrency --------------------------------------

def test_set_step_concurrency_validation() -> None:
    """Чужой шаг -> unknown_step, «20» -> bad_concurrency, пусто -> переопределение снято."""
    cfg: dict[str, Any] = {}
    with pytest.raises(ReelsiError) as e:
        ai_actions.set_step_concurrency(cfg, {"step": "нетакого", "value": 2})
    assert e.value.code == "unknown_step"
    assert cfg == {}

    # Строку приводим к числу, как поле профиля
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": "2"})
    assert cfg["step_concurrency_override"] == {"cut": 2}
    ai_actions.set_step_concurrency(cfg, {"step": "yellow", "value": 4})
    assert cfg["step_concurrency_override"] == {"cut": 2, "yellow": 4}
    # Границы 1..16 принимаются
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": 1})
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": 16})
    assert cfg["step_concurrency_override"]["cut"] == 16

    for bad in ("20", "0", "abc", True, 2.5, 17, -1):
        with pytest.raises(ReelsiError) as e:
            ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": bad})
        assert e.value.code == "bad_concurrency", f"значение {bad!r} принято как число"
    # Неудачные попытки конфиг не портят
    assert cfg["step_concurrency_override"] == {"cut": 16, "yellow": 4}

    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": ""})
    assert cfg["step_concurrency_override"] == {"yellow": 4}
    ai_actions.set_step_concurrency(cfg, {"step": "yellow", "value": None})
    assert "step_concurrency_override" not in cfg
    # Повторное снятие на пустом конфиге не падает
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": ""})
    assert "step_concurrency_override" not in cfg


def test_step_concurrency_override_kept_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Действие и чтение — один контракт: записанное действие отдаёт step_concurrency."""
    cfg = _patch_cfg(monkeypatch, copy.deepcopy(CLOUD_CFG))
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": "3"})
    assert ai_config.step_concurrency("cut") == 3
    ai_actions.set_step_concurrency(cfg, {"step": "cut", "value": ""})
    assert ai_config.step_concurrency("cut") == 6


# ---- 4. Фронт: поля на своих вкладках, разметка шлёт оба шага --------------

def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _tab_block(tab_id: str) -> str:
    """Кусок разметки одной вкладки: от её id до следующей вкладки `<div class="aistab"`."""
    html = _read(HTML)
    start = html.index('id="%s"' % tab_id)
    nxt = html.find('<div class="aistab"', start)
    return html[start:nxt if nxt > 0 else len(html)]


def _js_function(name: str) -> str:
    """Тело функции из 10-settings.js: от объявления до следующего объявления."""
    js = _read(SETTINGS_JS)
    start = js.index("function %s(" % name)
    ends = [i for i in (js.find("\nasync function ", start + 1),
                        js.find("\nfunction ", start + 1)) if i > 0]
    assert ends, "не нашёл конец функции %s" % name
    return js[start:min(ends)]


def test_concurrency_fields_live_on_their_tabs() -> None:
    """conc_cut — на вкладке «Нарезка», conc_markup — на «Разметка», и не наоборот."""
    cut, markup = _tab_block("aistab_cut"), _tab_block("aistab_markup")
    assert 'id="conc_cut"' in cut, "поля «Роликов одновременно» нет на вкладке «Нарезка»"
    assert 'for="conc_cut"' in cut, "подпись поля и сам input разъехались"
    assert "conc_cut" not in markup, "поле нарезки уехало на вкладку разметки"
    assert 'id="conc_markup"' in markup, "поля «Роликов одновременно» нет на вкладке «Разметка»"
    assert "conc_markup" not in cut, "поле разметки уехало на вкладку нарезки"
    # Обработчик у обоих полей — общий, иначе значение молча не сохранялось бы
    for tab, el in ((cut, "conc_cut"), (markup, "conc_markup")):
        row = tab[tab.index('id="%s"' % el):]
        assert "setStepConcurrency(this)" in row.split("</div>")[0], \
            "у поля %s нет обработчика setStepConcurrency" % el


def test_markup_field_sends_both_steps() -> None:
    """conc_markup пишет одно число в оба шага разметки: yellow и inserts."""
    body = _js_function("setStepConcurrency")
    assert "action:'set_step_concurrency'" in body, "поле шлёт не set_step_concurrency"
    assert "'yellow'" in body and "'inserts'" in body, \
        "строка разметки не пишет число в оба шага (yellow и inserts)"
    assert "conc_markup" in body, "разметка и нарезка не различаются в обработчике"
    assert "conc_cut" not in body.split("conc_markup")[1], \
        "обработчик разметки тронул поле нарезки"

    # Поля заполняются при открытии настроек вместе с селектами шагов
    assert "fillStepConcurrency()" in _js_function("fillAIProfileSelects"), \
        "поля «Роликов одновременно» не заполняются при открытии настроек"
    fill = _js_function("fillStepConcurrency")
    assert "conc_cut" in fill and "conc_markup" in fill, "заполнение идёт не по тем полям"
    assert "step_concurrency_override" in fill, "поле показывает не своё переопределение"
    assert re.search(r"placeholder", fill), "placeholder не обновляется — не видно, сколько сейчас"
