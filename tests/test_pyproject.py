# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож метаданных пакета в pyproject.toml.

Проверяет:
1. pyproject.toml существует в корне репозитория;
2. версия пакета в pyproject.toml соответствует app_meta.APP_VERSION
   после нормализации к форме PEP 440 (например, '0.2.0-beta' -> '0.2.0b0');
3. requires-python = '>=3.10';
4. базовые метаданные (name, readme, license) соответствуют проекту.

Разбор файла выполняется без сторонних библиотек и без tomllib (отсутствующего в 3.10)
— простым разбором строк.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import app_meta, paths
from core.umsg import ReelsiError


def _pep440_version(raw_version: str) -> str:
    """Нормализация версии к стандарту PEP 440."""
    v = raw_version.strip()
    v = re.sub(r"-beta\.?(\d+)?", lambda m: f"b{m.group(1) or '0'}", v)
    v = re.sub(r"-alpha\.?(\d+)?", lambda m: f"a{m.group(1) or '0'}", v)
    v = re.sub(r"-rc\.?(\d+)?", lambda m: f"rc{m.group(1) or '0'}", v)
    return v


def _parse_pyproject(path: Path) -> dict:
    """Разбор pyproject.toml с использованием tomli/tomllib, если доступны, иначе построчный разбор."""
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        from pip._vendor import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass
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


def test_pyproject_build_system():
    """pyproject.toml содержит секцию [build-system] с setuptools.build_meta."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    bs = data.get("build-system", {})
    assert bs.get("build-backend") == "setuptools.build_meta", (
        f"build-backend: {bs.get('build-backend')!r} != 'setuptools.build_meta'"
    )
    reqs = bs.get("requires", [])
    assert any("setuptools" in r for r in reqs), f"requires {reqs!r} не содержит setuptools"


def test_pyproject_scripts():
    """[project.scripts] регистрирует reelsi, reelsi-webui, reelsi-doctor с вызываемыми функциями."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    project = data.get("project", {})
    scripts = project.get("scripts", {})

    expected = {
        "reelsi": "reelsi:main",
        "reelsi-webui": "webui:main",
        "reelsi-doctor": "doctor:main",
    }
    for name, target in expected.items():
        assert name in scripts, f"Команда {name!r} отсутствует в [project.scripts]"
        assert scripts[name] == target, f"Команда {name!r}: {scripts[name]!r} != {target!r}"

    # Проверка импортом без запуска
    for name, target in scripts.items():
        mod_name, func_name = target.split(":")
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, func_name), f"В модуле {mod_name} нет функции {func_name}"
        assert callable(getattr(mod, func_name)), f"{mod_name}.{func_name} не callable"


def test_pyproject_dynamic_and_requirements():
    """Динамические зависимости берутся из requirements*.txt, и строки валидны по PEP 508."""
    pyproject_path = ROOT / "pyproject.toml"
    data = _parse_pyproject(pyproject_path)
    project = data.get("project", {})
    dynamic = project.get("dynamic", [])
    assert "dependencies" in dynamic, "dependencies отсутствует в project.dynamic"
    assert "optional-dependencies" in dynamic, "optional-dependencies отсутствует в project.dynamic"

    tool_setuptools = data.get("tool", {}).get("setuptools", {})
    dyn_cfg = tool_setuptools.get("dynamic", {})
    assert "dependencies" in dyn_cfg, "dependencies отсутствует в [tool.setuptools.dynamic]"

    dep_files = dyn_cfg.get("dependencies", {}).get("file", [])
    assert "requirements.txt" in dep_files, f"requirements.txt не найден в dep_files: {dep_files}"

    opt_cfg = dyn_cfg.get("optional-dependencies", {})
    assert "requirements-optional.txt" in opt_cfg.get("optional", {}).get("file", [])
    assert "requirements-dev.txt" in opt_cfg.get("dev", {}).get("file", [])

    # Все файлы требований существуют и каждая строка разбирается как PEP 508 Requirement
    req_files = [
        ROOT / "requirements.txt",
        ROOT / "requirements-optional.txt",
        ROOT / "requirements-dev.txt",
    ]
    for rf in req_files:
        assert rf.exists(), f"Файл зависимостей {rf} не существует"
        lines = rf.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            line_clean = line.strip()
            if not line_clean or line_clean.startswith("#"):
                continue
            try:
                Requirement(line_clean)
            except Exception as e:
                pytest.fail(f"Файл {rf.name}, строка {i}: {line_clean!r} не валидна по PEP 508: {e}")


def test_require_source_tree(tmp_path, monkeypatch):
    """require_source_tree() завершает процесс вне клона и проходит внутри клона."""
    assert hasattr(paths, "require_source_tree"), "paths.require_source_tree не найдена"
    # Внутри клона проходит без исключений
    paths.require_source_tree()

    # В пустой временной папке падает ReelsiError
    monkeypatch.setattr(paths, "ROOT", str(tmp_path))
    with pytest.raises(ReelsiError) as exc_info:
        paths.require_source_tree()
    err_msg = str(exc_info.value)
    assert "pip install -e ." in err_msg
