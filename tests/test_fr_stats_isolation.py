# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тест-сторож изоляции файлов состояния и render_stats.json (задание FR).

ПОЧЕМУ этот тест существует:
Прогон тестов не должен писать в боевые файлы состояния (render_stats.json,
ui_state.json, ai_calls.jsonl и др.). Файл статистики — источник ожидаемых времён,
по которым рассчитываются веса фаз, полоса прогресса и ETA. Загрязнение его
тестовыми прогонами (n=1,2, нулевой рендер) занижает ожидания в сотни раз.
Этот тест проверяет:
1. Автоматическая фикстура pytest изолирует путь render_stats.json во временную папку.
2. Прогон тестов не меняет боевой render_stats.json в корне репозитория: файла
   не было — не появился, был — размер и время модификации те же (файл у пользователя
   есть законно, отсутствие — не признак чистоты).
3. Сохранение статистики в тесте не создаёт и не меняет файлы в рабочей копии.
4. Соседние боевые файлы состояния (ui_state, ai_calls, job.lock, models_dev)
   также изолированы от тестового мусора.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.render as render  # noqa: E402


def _file_state(path):
    """Снимок состояния файла: (есть, размер, mtime). Нет файла — (False, None, None)."""
    if not os.path.exists(path):
        return (False, None, None)
    return (True, os.path.getsize(path), os.path.getmtime(path))


# Почему снимок на уровне модуля, а не внутри теста: pytest импортирует модуль на этапе
# сбора, ДО запуска первого теста, поэтому _REPO_STATS_BEFORE — состояние боевого файла
# ДО прогона. Снимок, снятый внутри самого теста (сразу «до» и «после», без действий
# между), сравнивал бы файл сам с собой и не ловил ничего.
# Почему сторожим неизменность, а не «файла не существует»: боевой render_stats.json
# пишется настоящими рендерами и у пользователя есть законно (внесён в .gitignore) —
# отсутствие файла не признак чистоты; сторожим неизменность: не было — не появился,
# был — размер и mtime те же.
_REPO_STATS = os.path.join(ROOT, "render_stats.json")
_REPO_STATS_BEFORE = _file_state(_REPO_STATS)


def test_guard_render_stats_not_in_repo_root():
    """Сторож: прогон тестов не меняет боевой render_stats.json в корне репозитория."""
    assert _file_state(_REPO_STATS) == _REPO_STATS_BEFORE, (
        f"Прогон тестов изменил боевой файл статистики {_REPO_STATS}! "
        "Тесты записали данные в боевой файл рабочей копии вместо временной папки."
    )


def test_guard_render_stats_path_is_isolated():
    """Сторож: _get_stats_path() указывает на изолированный временный файл, а не на корень."""
    stats_path = render._get_stats_path()
    repo_stats = os.path.join(ROOT, "render_stats.json")
    assert stats_path != repo_stats, (
        f"_get_stats_path() вернул боевой путь {repo_stats} вместо изолированного tmp_path"
    )
    assert os.environ.get("REELSI_RENDER_STATS") == stats_path


# Почему снимок ДО/ПОСЛЕ, а не «файла не существует»: боевой render_stats.json у
# пользователя есть законно (его пишут настоящие рендеры) — отсутствие файла не признак
# чистоты; сторожим неизменность: вызов _save_render_stats не должен ни создать боевой
# файл, ни изменить размер/mtime существующего.
def test_guard_save_stats_writes_to_isolated_file_only():
    """Сторож: вызов _save_render_stats пишет в изолированный файл и не трогает корень."""
    repo_stats = os.path.join(ROOT, "render_stats.json")
    stats_path = render._get_stats_path()
    before = _file_state(repo_stats)

    render._save_render_stats(10, 50.0, 360.0, 830.0)

    assert os.path.isfile(stats_path), "Статистика не записалась в изолированный файл"
    after = _file_state(repo_stats)
    assert before == after, (
        f"Запись статистики изменила боевой файл {repo_stats}! Тесты записали данные "
        "в боевой файл вместо временной папки."
    )


def test_guard_state_env_variables_isolated():
    """Сторож: переменные состояния указывают на временные пути и не указывают в корень."""
    protected_vars = {
        "REELSI_RENDER_STATS": "render_stats.json",
        "REELSI_UI_STATE": "ui_state.json",
        "REELSI_JOB_LOCK": "job.lock",
        "REELSI_AI_LOG": "ai_calls.jsonl",
        "REELSI_MODELS_DEV": "models_dev.json",
    }
    for var, filename in protected_vars.items():
        val = os.environ.get(var)
        assert val is not None, f"Переменная {var} не задана в автофикстуре conftest"
        repo_file = os.path.join(ROOT, filename)
        assert os.path.abspath(val) != os.path.abspath(repo_file), (
            f"Переменная {var} указывает на боевой файл {repo_file}"
        )
