# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты определения языка системы, t() и серверного умолчания.

Запуск:  python -m pytest reelsi/tests/test_lang.py -q
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import app_meta  # noqa: E402
import webui  # noqa: E402


def test_ui_lang_env_override(monkeypatch):
    """1. REELSI_LANG и AUTOCUT_LANG переопределяют язык независимо от системы."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    assert app_meta.ui_lang(force_reload=True) == "ru"

    monkeypatch.setenv("REELSI_LANG", "en")
    assert app_meta.ui_lang(force_reload=True) == "en"

    monkeypatch.delenv("REELSI_LANG", raising=False)
    monkeypatch.setenv("AUTOCUT_LANG", "ru")
    assert app_meta.ui_lang(force_reload=True) == "ru"

    monkeypatch.setenv("AUTOCUT_LANG", "en")
    assert app_meta.ui_lang(force_reload=True) == "en"


def test_ui_lang_system_env_fallback(monkeypatch):
    """2. Системное определение через переменные окружения (POSIX / fallback при сбое ctypes)."""
    orig_os_name = os.name
    orig_sys_platform = sys.platform

    monkeypatch.delenv("REELSI_LANG", raising=False)
    monkeypatch.delenv("AUTOCUT_LANG", raising=False)

    try:
        # 1) Подмена на не-Windows (POSIX/Linux): ветка ctypes не вызывается
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")

        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            # Очистим все
            for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
                monkeypatch.delenv(v, raising=False)

            monkeypatch.setenv(var, "ru_RU.UTF-8")
            assert app_meta.ui_lang(force_reload=True) == "ru"

            monkeypatch.setenv(var, "en_US.UTF-8")
            assert app_meta.ui_lang(force_reload=True) == "en"

        # Когда ничего не задано — fallback 'en' (независимо от хостовой машины)
        for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            monkeypatch.delenv(v, raising=False)
        assert app_meta.ui_lang(force_reload=True) == "en"

        # 2) Подмена на Windows, но со сбоем ctypes -> fallback на env
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(sys, "platform", "win32")

        class MockKernel32Err:
            def GetUserDefaultUILanguage(self):
                raise RuntimeError("ctypes unavailable")

        monkeypatch.setitem(sys.modules, "ctypes", type("C", (), {"windll": type("W", (), {"kernel32": MockKernel32Err()})}))
        monkeypatch.setenv("LANG", "ru_RU.UTF-8")
        assert app_meta.ui_lang(force_reload=True) == "ru"
        monkeypatch.delenv("LANG", raising=False)
        assert app_meta.ui_lang(force_reload=True) == "en"
    finally:
        os.name = orig_os_name
        sys.platform = orig_sys_platform


def test_ui_lang_windows_ctypes(monkeypatch):
    """2b. Ветка Windows ctypes: функция не падает и корректно декодирует lang_id."""
    orig_os_name = os.name
    orig_sys_platform = sys.platform

    monkeypatch.delenv("REELSI_LANG", raising=False)
    monkeypatch.delenv("AUTOCUT_LANG", raising=False)
    for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(v, raising=False)

    try:
        # 1) Подмена платформы на Windows (nt / win32) для проверки на любой машине
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(sys, "platform", "win32")

        class MockKernel32Ru:
            def GetUserDefaultUILanguage(self):
                return 0x0419  # Russian (primary lang id 0x19)

        class MockKernel32En:
            def GetUserDefaultUILanguage(self):
                return 0x0409  # English (primary lang id 0x09)

        class MockCtypesRu:
            windll = type("W", (), {"kernel32": MockKernel32Ru()})

        class MockCtypesEn:
            windll = type("W", (), {"kernel32": MockKernel32En()})

        monkeypatch.setitem(sys.modules, "ctypes", MockCtypesRu())
        assert app_meta.ui_lang(force_reload=True) == "ru"

        monkeypatch.setitem(sys.modules, "ctypes", MockCtypesEn())
        assert app_meta.ui_lang(force_reload=True) == "en"

        # 2) Подмена платформы на не-Windows: подменённый ctypes игнорируется
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "ctypes", MockCtypesRu())
        # На Linux ctypes не опрашивается, при пустом env возвращается 'en'
        assert app_meta.ui_lang(force_reload=True) == "en"
    finally:
        os.name = orig_os_name
        sys.platform = orig_sys_platform


def test_t_function_translations(monkeypatch):
    """Питоновский t() переводит по en.json на en и оставляет русский на ru."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    assert app_meta.t("Мой стиль") == "Мой стиль"
    assert app_meta.t("Пример профиля") == "Пример профиля"
    assert app_meta.t("заведён styles/{name}.json из примера — правь в интерфейсе", name="Test") == \
        "заведён styles/Test.json из примера — правь в интерфейсе"

    monkeypatch.setenv("REELSI_LANG", "en")
    app_meta.ui_lang(force_reload=True)
    assert app_meta.t("Мой стиль") == "My style"
    assert app_meta.t("Пример профиля") == "Example speaker"
    assert app_meta.t("заведён styles/{name}.json из примера — правь в интерфейсе", name="Test") == \
        "created styles/Test.json from template — edit in UI"


def test_russian_works_without_dictionary(monkeypatch, tmp_path):
    """5. Русский работает без словаря: при подмене пути на несуществующий ничего не падает."""
    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    monkeypatch.setattr(app_meta, "I18N_FILE", str(tmp_path / "nonexistent.json"))
    app_meta._I18N_DICT = None

    # Ничего не падает, возвращается ключ
    res = app_meta.t("Мой стиль")
    assert res == "Мой стиль"


def test_webui_replaces_ui_lang_in_html(monkeypatch):
    """3 & 4. webui._page() подставляет __UI_LANG__ в HTML-шаблон и JS-константу."""
    monkeypatch.setenv("REELSI_LANG", "en")
    app_meta.ui_lang(force_reload=True)
    webui._CACHE["mtime"] = None  # сбросить кэш шаблона
    page_en = webui._page()

    assert "__UI_LANG__" not in page_en
    assert '<html lang="en">' in page_en
    assert 'const REELSI_DEFAULT_LANG="en";' in page_en

    monkeypatch.setenv("REELSI_LANG", "ru")
    app_meta.ui_lang(force_reload=True)
    webui._CACHE["mtime"] = None
    page_ru = webui._page()

    assert "__UI_LANG__" not in page_ru
    assert '<html lang="ru">' in page_ru
    assert 'const REELSI_DEFAULT_LANG="ru";' in page_ru
