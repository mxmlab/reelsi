# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Храповик строгой типизации на границе ядра (внешнее ревью 2026-09-22).

Три сторожа — по одному на каждую часть решения:

1. **Список строгих модулей не сжимается.** Тест держит НИЖНИЙ список: `[tool.mypy]
   files` обязан содержать все эти модули, а `[[tool.mypy.overrides]]` — держать для
   них `disallow_untyped_defs = true`. Граница может расти, уменьшаться — нет.
2. **`mypy` по этому списку не находит ошибок.** Нет `mypy` — тест пропускается с
   причиной: в CI он есть (ставится рядом с ruff), локально может не стоять.
3. **`.project.json` пишет и читает ровно один модуль.** `write_project` ставит
   `version`, `read_project` читает файл БЕЗ `version` (старый = версия 0), и прямых
   `atomic_json_dump(... ".project.json" ...)` вне `core/project_file.py` нет —
   иначе поле, добавленное одним писателем, молча теряется при следующей записи
   другим (так и жили пять независимых писателей раньше).
"""
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core.project_file import PROJECT_VERSION, PROJECT_VERSION_LEGACY, read_project, write_project  # noqa: E402

# Нижний список строгого контура: модули, которые зовут все остальные (пути,
# файловый ввод-вывод, медиа, метаданные приложения, рендер, камеры, rclone,
# .project.json) — граница ядра. Свой список, а не чтение чужого: pyproject можно
# расширить свободно, а выкинуть из него модуль тест не даст.
LOWER_BOUND = (
    "core/umsg.py",
    "core/fileio.py",
    "core/media.py",
    "core/paths.py",
    "core/app_meta.py",
    "core/aerender.py",
    "core/cams.py",
    "core/rclone.py",
    "core/xml2ae/plan_style.py",
    "core/aicut/config_actions.py",
    "core/project_file.py",
    "core/cutjob.py",
    "api/_core.py",
)

# Каталоги, которые сканирует сторож писателей .project.json: код, а не тесты
# (тесты вправе готовить файл руками — им и проверяем чтение старого формата).
CODE_DIRS = ("core", "api", "tools")


def _pyproject() -> dict[str, Any]:
    """pyproject.toml словарём: tomllib (3.12+), tomli из pip или пропуск."""
    path = ROOT / "pyproject.toml"
    try:
        import tomllib
    except ImportError:
        try:
            from pip._vendor import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            pytest.skip("нет ни tomllib, ни tomli — pyproject.toml не разобрать")
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_pyproject_keeps_strict_module_list():
    """`[tool.mypy] files` содержит строгий список целиком (граница не сжалась)."""
    cfg = _pyproject().get("tool", {}).get("mypy", {})
    files = cfg.get("files")
    assert isinstance(files, list) and files, "[tool.mypy] files пуст или отсутствует"
    missing = [m for m in LOWER_BOUND if m not in files]
    assert not missing, (
        "из строгого списка [tool.mypy] files пропали модули: " + ", ".join(missing)
    )
    for key in ("python_version", "ignore_missing_imports", "warn_unused_ignores",
                "warn_redundant_casts"):
        assert key in cfg, f"в [tool.mypy] нет ключа {key}"


def test_pyproject_strict_modules_require_annotations():
    """Для строгих модулей включён `disallow_untyped_defs` (иначе список ничего не значит)."""
    cfg = _pyproject().get("tool", {}).get("mypy", {})
    strict = None
    for override in cfg.get("overrides", []):
        if override.get("disallow_untyped_defs"):
            strict = set(override.get("module", []))
            break
    assert strict, "нет [[tool.mypy.overrides]] с disallow_untyped_defs = true"
    expected = {m[:-3].replace("/", ".") for m in LOWER_BOUND}
    missing = sorted(expected - strict)
    assert not missing, (
        "disallow_untyped_defs не распространён на модули: " + ", ".join(missing)
    )


def test_mypy_reports_no_errors():
    """`mypy` по строгому списку — 0 ошибок (нет mypy — пропуск с причиной)."""
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy не установлен (в CI ставится рядом с ruff: pip install -r "
                    "requirements-dev.txt)")
    # --no-incremental: без него mypy заводит .mypy_cache в корне репозитория, а тесты
    # не должны оставлять следов в дереве (сторож изоляции в conftest.py).
    r = subprocess.run([sys.executable, "-m", "mypy", "--no-incremental"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    assert r.returncode == 0, ("mypy нашёл ошибки в строгом списке:\n"
                               + (r.stdout or "") + (r.stderr or ""))


def test_write_project_stamps_version(tmp_path):
    """`write_project` пишет `version` и остальные поля (тем же отступом, indent=1)."""
    path = tmp_path / "01_clip.project.json"
    write_project(str(path), {"cams": ["a.mp4"], "keep": [[0.0, 1.5]]})
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["version"] == PROJECT_VERSION == 1
    assert data["cams"] == ["a.mp4"] and data["keep"] == [[0.0, 1.5]]
    assert "\n " in raw, "сайдкар записан не с отступом indent=1"


def test_read_project_reads_file_without_version(tmp_path):
    """Файл без `version` — старый (версия 0): читается как раньше, поля на месте."""
    path = tmp_path / "old.project.json"
    path.write_text(json.dumps({"cams": ["a.mp4"], "keep": [[0.0, 2.0]]}),
                    encoding="utf-8")
    proj = read_project(str(path))
    assert proj is not None
    assert proj["cams"] == ["a.mp4"] and proj["keep"] == [[0.0, 2.0]]
    assert proj["version"] == PROJECT_VERSION_LEGACY == 0


def test_read_project_is_soft_on_missing_and_broken(tmp_path):
    """Нет файла / рваный JSON / не наш формат — None, а не исключение (как было)."""
    assert read_project(str(tmp_path / "нет-такого.project.json")) is None
    broken = tmp_path / "broken.project.json"
    broken.write_text('{"cams": [', encoding="utf-8")
    assert read_project(str(broken)) is None
    not_ours = tmp_path / "list.project.json"
    not_ours.write_text("[1, 2]", encoding="utf-8")
    assert read_project(str(not_ours)) is None


def _project_json_dump_calls() -> list[str]:
    """Вызовы atomic_json_dump с литералом «.project.json» вне core/project_file.py."""
    found = []
    files = [p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py")]
    files += sorted(ROOT.glob("*.py"))
    for path in files:
        if path.name == "project_file.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Имя как есть (`atomic_json_dump(...)`) и через модуль
            # (`fileio.atomic_json_dump(...)`) — обе формы записи в проекте есть.
            named = (isinstance(func, ast.Name) and func.id == "atomic_json_dump")
            attr = (isinstance(func, ast.Attribute) and func.attr == "atomic_json_dump")
            if not (named or attr):
                continue
            for arg in node.args:
                literals = [n.value for n in ast.walk(arg) if isinstance(n, ast.Constant)
                            and isinstance(n.value, str)]
                if any(".project.json" in s for s in literals):
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                    break
    return found


def test_project_json_has_single_writer():
    """Ни один модуль, кроме core/project_file.py, не пишет .project.json напрямую."""
    offenders = _project_json_dump_calls()
    assert not offenders, (
        "прямая запись .project.json мимо core.project_file.write_project: "
        + ", ".join(offenders)
    )


def test_project_file_module_keeps_layers():
    """`core/project_file.py` — ядро: ни Flask, ни api, ни импорта из CLI."""
    path = ROOT / "core" / "project_file.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "flask" not in imported, "ядро не имеет права зависеть от HTTP-слоя"
    assert "api" not in imported, "api импортирует core, а не наоборот"
