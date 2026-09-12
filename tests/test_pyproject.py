# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож метаданных пакета в pyproject.toml (задание HG).

Проверяет:
1. pyproject.toml существует в корне репозитория;
2. версия пакета в pyproject.toml соответствует app_meta.APP_VERSION
   после нормализации к форме PEP 440 (например, '0.1.0-beta' -> '0.1.0b0');
3. requires-python = '>=3.10';
4. базовые метаданные (name, readme, license) соответствуют проекту.

Разбор файла выполняется без сторонних библиотек и без tomllib (отсутствующего в 3.10)
— простым разбором строк.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import app_meta


def _pep440_version(raw_version: str) -> str:
    """Нормализация версии к стандарту PEP 440."""
    v = raw_version.strip()
    v = re.sub(r"-beta\.?(\d+)?", lambda m: f"b{m.group(1) or '0'}", v)
    v = re.sub(r"-alpha\.?(\d+)?", lambda m: f"a{m.group(1) or '0'}", v)
    v = re.sub(r"-rc\.?(\d+)?", lambda m: f"rc{m.group(1) or '0'}", v)
    return v


def _parse_pyproject(path: Path) -> dict:
    """Простой разбор секций и строковых ключей pyproject.toml без tomllib."""
    text = path.read_text(encoding="utf-8")
    data = {}
    current_section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            data[current_section] = {}
            continue
        if "=" in line and current_section is not None:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            data[current_section][key] = val
    return data


def test_pyproject_exists():
    """pyproject.toml существует в корне репозитория."""
    pyproject_path = ROOT / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml не найден в корне репозитория"


def test_pyproject_version_matches_app_version():
    """Версия в pyproject.toml соответствует app_meta.APP_VERSION в форме PEP 440."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    project = data.get("project", {})
    assert "version" in project, "В секции [project] pyproject.toml нет ключа version"
    pyproject_ver = project["version"]
    expected_ver = _pep440_version(app_meta.APP_VERSION)
    assert pyproject_ver == expected_ver, (
        f"Версия в pyproject.toml ({pyproject_ver!r}) не соответствует "
        f"app_meta.APP_VERSION ({app_meta.APP_VERSION!r} -> PEP 440 {expected_ver!r})"
    )


def test_pyproject_requires_python():
    """requires-python в pyproject.toml равен '>=3.10'."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    project = data.get("project", {})
    assert "requires-python" in project, "В секции [project] pyproject.toml нет ключа requires-python"
    assert project["requires-python"] == ">=3.10", (
        f"requires-python в pyproject.toml равен {project['requires-python']!r}, ожидался '>=3.10'"
    )


def test_pyproject_metadata():
    """Базовые метаданные проекта в pyproject.toml соответствуют контракту."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    project = data.get("project", {})
    assert project.get("name") == "reelsi", f"name: {project.get('name')!r} != 'reelsi'"
    assert project.get("readme") == "README.md", f"readme: {project.get('readme')!r} != 'README.md'"
    assert project.get("license") == "AGPL-3.0-or-later", (
        f"license: {project.get('license')!r} != 'AGPL-3.0-or-later'"
    )
    assert bool(project.get("description")), "description пуст или отсутствует в [project]"
