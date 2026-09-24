# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты утилиты сверки AST: tools/ast_same.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AST_SAME = ROOT / "tools" / "ast_same.py"


def _git(cmd: list[str], cwd: Path) -> str:
    """Выполняет команду git в каталоге cwd."""
    r = subprocess.run(
        ["git", *cmd],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return r.stdout.strip()


def _init_repo(tmp_path: Path) -> None:
    """Инициализирует тестовый git-репозиторий."""
    _git(["init"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Tester"], tmp_path)
    _git(["config", "core.autocrlf", "false"], tmp_path)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Запускает tools/ast_same.py через subprocess."""
    return subprocess.run(
        [sys.executable, str(AST_SAME), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_added_annotation_same_with_types_only(tmp_path: Path) -> None:
    """Добавленная аннотация → same с --types-only и DIFF без флагов."""
    _init_repo(tmp_path)
    f = tmp_path / "calc.py"
    f.write_text("def calc(x):\n    y = x + 1\n    return y\n", encoding="utf-8")
    _git(["add", "calc.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    # Добавляем типы в рабочем дереве
    f.write_text("def calc(x: int) -> int:\n    y: int = x + 1\n    return y\n", encoding="utf-8")

    # С флагом --types-only: same, код 0
    res_types = _run_cli(["HEAD", "--types-only", "calc.py"], tmp_path)
    assert res_types.returncode == 0
    assert "same calc.py" in res_types.stdout

    # Без флагов: DIFF, код 1
    res_strict = _run_cli(["HEAD", "calc.py"], tmp_path)
    assert res_strict.returncode == 1
    assert "DIFF calc.py" in res_strict.stdout


def test_int_or_zero_is_diff(tmp_path: Path) -> None:
    """int(x) → int(x or 0) → DIFF даже с --types-only."""
    _init_repo(tmp_path)
    f = tmp_path / "logic.py"
    f.write_text("def f(x):\n    return int(x)\n", encoding="utf-8")
    _git(["add", "logic.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("def f(x):\n    return int(x or 0)\n", encoding="utf-8")

    res = _run_cli(["HEAD", "--types-only", "logic.py"], tmp_path)
    assert res.returncode == 1
    assert "DIFF logic.py" in res.stdout


def test_added_assert_is_diff(tmp_path: Path) -> None:
    """Добавлен assert → DIFF."""
    _init_repo(tmp_path)
    f = tmp_path / "guard.py"
    f.write_text("def f(x):\n    return x * 2\n", encoding="utf-8")
    _git(["add", "guard.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("def f(x):\n    assert x > 0\n    return x * 2\n", encoding="utf-8")

    # Проверяем как без флагов, так и с --types-only
    res = _run_cli(["HEAD", "--types-only", "guard.py"], tmp_path)
    assert res.returncode == 1
    assert "DIFF guard.py" in res.stdout


def test_removed_module_docstring(tmp_path: Path) -> None:
    """Удалён докстринг модуля → same только с --ignore-docstrings."""
    _init_repo(tmp_path)
    f = tmp_path / "doc.py"
    f.write_text('"""Документация модуля."""\n\ndef get_val():\n    return 42\n', encoding="utf-8")
    _git(["add", "doc.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("def get_val():\n    return 42\n", encoding="utf-8")

    # С флагом --ignore-docstrings: same, код 0
    res_doc = _run_cli(["HEAD", "--ignore-docstrings", "doc.py"], tmp_path)
    assert res_doc.returncode == 0
    assert "same doc.py" in res_doc.stdout

    # Без флага (и с другим флагом): DIFF, код 1
    res_none = _run_cli(["HEAD", "doc.py"], tmp_path)
    assert res_none.returncode == 1
    assert "DIFF doc.py" in res_none.stdout

    res_other = _run_cli(["HEAD", "--ignore-annotations", "doc.py"], tmp_path)
    assert res_other.returncode == 1
    assert "DIFF doc.py" in res_other.stdout


def test_cast_call_replacement(tmp_path: Path) -> None:
    """cast(int, x) вместо x → same с --ignore-cast."""
    _init_repo(tmp_path)
    f = tmp_path / "typing_cast.py"
    f.write_text("def f(x):\n    y = x\n    return y\n", encoding="utf-8")
    _git(["add", "typing_cast.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("def f(x):\n    y = cast(int, x)\n    return y\n", encoding="utf-8")

    # С флагом --ignore-cast: same, код 0
    res_cast = _run_cli(["HEAD", "--ignore-cast", "typing_cast.py"], tmp_path)
    assert res_cast.returncode == 0
    assert "same typing_cast.py" in res_cast.stdout

    # Без флага: DIFF, код 1
    res_strict = _run_cli(["HEAD", "typing_cast.py"], tmp_path)
    assert res_strict.returncode == 1
    assert "DIFF typing_cast.py" in res_strict.stdout


def test_tuple_assignment_split_is_diff(tmp_path: Path) -> None:
    """a, b = 1, 2 разбито на две строки → DIFF."""
    _init_repo(tmp_path)
    f = tmp_path / "assign.py"
    f.write_text("a, b = 1, 2\n", encoding="utf-8")
    _git(["add", "assign.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("a = 1\nb = 2\n", encoding="utf-8")

    res = _run_cli(["HEAD", "--types-only", "assign.py"], tmp_path)
    assert res.returncode == 1
    assert "DIFF assign.py" in res.stdout


def test_new_file_reporting_and_allow_new(tmp_path: Path) -> None:
    """Новый файл → NEW и код 1, с --allow-new → 0."""
    _init_repo(tmp_path)
    f_old = tmp_path / "old.py"
    f_old.write_text("x = 1\n", encoding="utf-8")
    _git(["add", "old.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f_new = tmp_path / "new_module.py"
    f_new.write_text("y = 2\n", encoding="utf-8")

    # Без --allow-new: NEW и код 1
    res_strict = _run_cli(["HEAD", "new_module.py"], tmp_path)
    assert res_strict.returncode == 1
    assert "NEW new_module.py" in res_strict.stdout

    # С --allow-new: NEW и код 0
    res_allow = _run_cli(["HEAD", "--allow-new", "new_module.py"], tmp_path)
    assert res_allow.returncode == 0
    assert "NEW new_module.py" in res_allow.stdout


def test_diff_flag_prints_unified_diff(tmp_path: Path) -> None:
    """Флаг --diff печатает unified diff для различающихся файлов."""
    _init_repo(tmp_path)
    f = tmp_path / "diff_test.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(["add", "diff_test.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    f.write_text("def f():\n    return 2\n", encoding="utf-8")

    res = _run_cli(["HEAD", "--diff", "diff_test.py"], tmp_path)
    assert res.returncode == 1
    assert "DIFF diff_test.py" in res.stdout
    assert "-    return 1" in res.stdout
    assert "+    return 2" in res.stdout


def test_no_paths_scans_git_diff_excluding_tests(tmp_path: Path) -> None:
    """Без путей проверяются изменённые файлы из git diff, пропуская tests/."""
    _init_repo(tmp_path)
    src = tmp_path / "core.py"
    src.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    tst = tmp_path / "tests" / "test_dummy.py"
    tst.write_text("assert True\n", encoding="utf-8")

    _git(["add", "core.py", "tests/test_dummy.py"], tmp_path)
    _git(["commit", "-m", "base"], tmp_path)

    # Меняем оба файла
    src.write_text("x = 2\n", encoding="utf-8")
    tst.write_text("assert False\n", encoding="utf-8")

    # Запускаем без путей
    res = _run_cli(["HEAD"], tmp_path)
    assert "core.py" in res.stdout
    assert "test_dummy.py" not in res.stdout
