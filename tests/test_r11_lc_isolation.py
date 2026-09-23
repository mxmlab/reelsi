# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты изоляции окружения.

Проверяет, что:
1. Модульные константы файлов состояния указывают во временный каталог, а не в корень репозитория.
2. Сторож снимка корня репозитория активен.
3. Пороги gigaam_cut.tune (в частности DEDUPE) восстанавливаются между тестами в любом порядке выполнения.
4. Сессионный и потестовый путь ai_config.json изначально не существуют и не засеваются боевыми ключами.
"""
import os

from api import _core, videogen
from core import aerender, censor, insertlib, paths, terms
from core.aicut import catalog, config
from core.gigaam_cut import tune


def test_constants_point_outside_repo_root():
    """После импорта api и core.aicut константы состояния указывают НЕ в корень репозитория.

    ПОЧЕМУ: если путь вычисляется от paths.ROOT без изоляции, тесты пишут в боевые
    файлы пользователя (ai_calls.jsonl, ui_state.json, job.lock и т.д.).
    """
    root = os.path.realpath(paths.ROOT)

    def is_in_root(p):
        norm = os.path.realpath(str(p))
        return os.path.dirname(norm) == root or norm == root

    assert not is_in_root(config.AI_LOG_PATH), f"AI_LOG_PATH указывает в корень: {config.AI_LOG_PATH}"
    assert not is_in_root(_core.JOB_LOCK_PATH), f"JOB_LOCK_PATH указывает в корень: {_core.JOB_LOCK_PATH}"
    assert not is_in_root(_core.UI_STATE_PATH), f"UI_STATE_PATH указывает в корень: {_core.UI_STATE_PATH}"
    assert not is_in_root(videogen.VIDEO_DIR), f"VIDEO_DIR указывает в корень: {videogen.VIDEO_DIR}"
    assert not is_in_root(videogen.VIDEO_HIST_PATH), f"VIDEO_HIST_PATH указывает в корень: {videogen.VIDEO_HIST_PATH}"
    assert not is_in_root(terms.TERMS_PATH), f"TERMS_PATH указывает в корень: {terms.TERMS_PATH}"
    assert not is_in_root(censor.USER_PATHS["bad"]), f"USER_PATHS['bad'] указывает в корень: {censor.USER_PATHS['bad']}"
    assert not is_in_root(censor.USER_PATHS["ok"]), f"USER_PATHS['ok'] указывает в корень: {censor.USER_PATHS['ok']}"
    assert not is_in_root(insertlib.INDEX_PATH), f"INDEX_PATH указывает в корень: {insertlib.INDEX_PATH}"
    assert not is_in_root(aerender.get_stats_path()), f"render_stats указывает в корень: {aerender.get_stats_path()}"
    assert not is_in_root(catalog.catalog_cache_path()), f"models_dev указывает в корень: {catalog.catalog_cache_path()}"
    assert not is_in_root(config.AI_CONFIG_PATH), f"AI_CONFIG_PATH указывает в корень: {config.AI_CONFIG_PATH}"


def test_ai_config_paths_do_not_exist_initially():
    """Сессионный и потестовый путь конфига не существуют в начале теста.

    ПОЧЕМУ: личные шаблоны и боевые файлы конфига (ai_config.test.json, ai_config.json)
    могут содержать настоящие API-ключи. Тестовое окружение не засевает конфиг:
    файл изначально не существует, код использует _default_ai_config(), а тесты,
    которым нужен конфиг, создают его сами.
    """
    import conftest

    # Сессионный путь конфига
    assert not os.path.exists(conftest._TEST_AI_CONFIG), (
        f"Сессионный ai_config.json существует: {conftest._TEST_AI_CONFIG}"
    )

    # Потестовый путь конфига (AI_CONFIG_PATH и REELSI_AI_CONFIG)
    assert not os.path.exists(config.AI_CONFIG_PATH), (
        f"Потестовый ai_config.json существует в начале теста: {config.AI_CONFIG_PATH}"
    )
    env_cfg = os.environ.get("REELSI_AI_CONFIG")
    assert env_cfg and not os.path.exists(env_cfg), (
        f"Путь REELSI_AI_CONFIG существует в начале теста: {env_cfg}"
    )

    # При чтении без существующего файла возвращается дефолтный конфиг
    assert config.load_ai_config() == config._default_ai_config()
    # Автозасев обезврежен — чтение не создало файл
    assert not os.path.exists(config.AI_CONFIG_PATH)


def test_ai_config_save_writes_to_tmp_path(tmp_path):
    """save_ai_config пишет во временный каталог теста (tmp_path), а не в корень репозитория.

    ПОЧЕМУ: ai_config.json содержит боевые API-ключи пользователя; любые вызовы API
    внутри тестов обязаны быть изолированы во tmp_path каждого теста.
    """
    root_cfg = os.path.join(paths.ROOT, "ai_config.json")
    root_mtime_before = os.stat(root_cfg).st_mtime_ns if os.path.exists(root_cfg) else None

    dummy_cfg = {"active": "LM Studio", "profiles": {"LM Studio": {"provider": "lmstudio"}}}
    config.save_ai_config(dummy_cfg)

    # Проверяем, что AI_CONFIG_PATH указывает внутрь tmp_path
    cfg_path = config.AI_CONFIG_PATH
    assert os.path.realpath(cfg_path).startswith(os.path.realpath(str(tmp_path))), (
        f"AI_CONFIG_PATH ({cfg_path}) не находится внутри tmp_path ({tmp_path})"
    )
    assert os.path.isfile(cfg_path), f"Файл {cfg_path} не был создан"

    # Проверяем, что файл в корне не изменился
    if root_mtime_before is not None:
        assert os.stat(root_cfg).st_mtime_ns == root_mtime_before, (
            "save_ai_config изменил mtime ai_config.json в корне репозитория!"
        )
    else:
        assert not os.path.exists(root_cfg), (
            "save_ai_config создал ai_config.json в корне репозитория!"
        )


def test_root_snapshot_watchdog_is_active():
    """Механизм сторожа снимка корня репозитория включён и содержит файлы корня."""
    import conftest
    assert isinstance(conftest._ROOT_SNAPSHOT, dict)
    assert len(conftest._ROOT_SNAPSHOT) > 0
    assert "pyproject.toml" in conftest._ROOT_SNAPSHOT
    assert "reelsi.py" in conftest._ROOT_SNAPSHOT

    # Проверяем, что сторож роняет сессию (exitstatus = 1) при появлении нового файла в корне
    class FakeSession:
        exitstatus = 0

    fake_session = FakeSession()
    # Подменяем снимок так, будто на старте не было pyproject.toml
    orig_snap = conftest._ROOT_SNAPSHOT
    try:
        conftest._ROOT_SNAPSHOT = {k: v for k, v in orig_snap.items() if k != "pyproject.toml"}
        conftest._check_root_snapshot(fake_session)
        assert fake_session.exitstatus == 1, "Сторож обязан установить exitstatus = 1 при расхождении"
    finally:
        conftest._ROOT_SNAPSHOT = orig_snap


def test_dedupe_isolation_step_a_mutates_dedupe():
    """Шаг А проверки порядка: мутируем tune.DEDUPE в противоположное дефолту значение.

    ПОЧЕМУ: тесты пайплайна могут переключать пороги; autouse-фикстура reset_tune_globals
    обязана восстановить дефолтное состояние до и после каждого теста.
    """
    default = tune._DEFAULT_CUT_GLOBALS["DEDUPE"]
    tune.DEDUPE = not default
    assert tune.DEDUPE is not default


def test_dedupe_isolation_step_b_expects_default_dedupe():
    """Шаг Б проверки порядка: ожидает дефолтное значение tune.DEDUPE из _DEFAULT_CUT_GLOBALS.

    Должен быть зелёным независимо от того, запускался ли step_a до него или после.
    """
    default = tune._DEFAULT_CUT_GLOBALS["DEDUPE"]
    assert tune.DEDUPE == default
