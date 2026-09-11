# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты единого источника версии (задание DX).

Версия приложения объявлена в app_meta.APP_VERSION и нигде не дублируется константой.
Этот сторож проверяет:
1. CHANGELOG.md (первый заголовок релиза) совпадает с APP_VERSION;
2. doctor.py печатает версию в первой строке;
3. webui._page() подставляет APP_VERSION в HTML-шаблон.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import app_meta
import doctor
import webui


def test_app_version_defined_and_exported():
    """app_meta.APP_VERSION объявлен и экспортирован в __all__."""
    assert hasattr(app_meta, "APP_VERSION")
    assert app_meta.APP_VERSION == "0.1.0-beta"
    assert "APP_VERSION" in app_meta.__all__


def test_changelog_matches_app_version():
    """Первый заголовок в CHANGELOG.md совпадает с app_meta.APP_VERSION."""
    changelog_path = ROOT / "CHANGELOG.md"
    assert changelog_path.exists(), "CHANGELOG.md не найден в корне"
    text = changelog_path.read_text(encoding="utf-8")
    m = re.search(r"^##\s+([^\s—]+)\s+—", text, re.MULTILINE)
    assert m, "В CHANGELOG.md не найден заголовок релиза вида ## X.Y.Z — YYYY-MM-DD"
    changelog_version = m.group(1).strip()
    assert changelog_version == app_meta.APP_VERSION, (
        f"Версия в CHANGELOG.md ({changelog_version}) не совпадает с app_meta.APP_VERSION ({app_meta.APP_VERSION})"
    )


def test_doctor_prints_app_version_on_first_line(capsys, monkeypatch):
    """doctor.py печатает версию Reelsi в первой строке отчёта."""
    monkeypatch.setattr(doctor, "_which", lambda n: "/usr/bin/" + n if n in ("ffmpeg", "node") else None)
    doctor.main()
    out = capsys.readouterr().out
    first_line = out.splitlines()[0] if out.splitlines() else ""
    assert f"Reelsi v{app_meta.APP_VERSION}" in first_line, (
        f"Первая строка doctor.py не содержит Reelsi v{app_meta.APP_VERSION}: {first_line!r}"
    )


def test_webui_replaces_app_version_in_html():
    """webui._page() подставляет APP_VERSION и не оставляет плейсхолдер."""
    page = webui._page()
    assert "__APP_VERSION__" not in page, "Плейсхолдер __APP_VERSION__ остался в HTML"
    assert f'<span class="ver">{app_meta.APP_VERSION}</span>' in page, (
        f"В шапке не найдена подстановка версии: <span class=\"ver\">{app_meta.APP_VERSION}</span>"
    )
