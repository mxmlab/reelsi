# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка предзагрузки pyarrow модулем arrowfix во всех точках входа.

arrow.dll падает с access violation c0000005, если pyarrow загружен после torch
через candidate_generator в transformers/breath.py. Краш не меняет код возврата,
поэтому тесты проверяют именно ПОРЯДОК: arrowfix обязан загружать pyarrow до torch,
а точки входа обязаны импортировать arrowfix до опасных модулей.
"""
import ast
import os
import subprocess
import sys
import pytest

REELSI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRYPOINT_FILES = [
    "core/omni_cut.py",
    "core/gigaam_cut/__main__.py",
    "tools/train_breath.py",
]

DANGEROUS_IMPORTS = [
    "sync",
    "vad",
    "align",
    "xmlbuild",
    "aicut",
    "breath",
    "pipeline",
    "torch",
    "gigaam",
]


def test_arrowfix_loads_pyarrow():
    """arrowfix загружает pyarrow в свежем подпроцессе."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow не установлен в окружении")

    code = (
        "import sys; "
        f"sys.path.insert(0, {REELSI_ROOT!r}); "
        "from core import arrowfix; "
        "print('pyarrow' in sys.modules)"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "True"


def test_entrypoints_import_arrowfix_first():
    """Точки входа импортируют arrowfix раньше любого опасного модуля."""
    for rel_path in ENTRYPOINT_FILES:
        full_path = os.path.join(REELSI_ROOT, rel_path)
        assert os.path.exists(full_path), f"Файл не найден: {full_path}"
        with open(full_path, "r", encoding="utf-8") as fs:
            tree = ast.parse(fs.read(), filename=full_path)

        arrowfix_lines = []
        dangerous_lines = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".")[0]
                    if name == "arrowfix":
                        arrowfix_lines.append((name, node.lineno))
                    if name in DANGEROUS_IMPORTS:
                        dangerous_lines.append((name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lstrip(".").split(".")[0]
                if mod == "arrowfix" or any(a.name == "arrowfix" for a in node.names):
                    arrowfix_lines.append(("arrowfix", node.lineno))
                if mod in DANGEROUS_IMPORTS:
                    dangerous_lines.append((mod, node.lineno))
                for a in node.names:
                    if a.name in DANGEROUS_IMPORTS:
                        dangerous_lines.append((a.name, node.lineno))

        assert arrowfix_lines, f"В {rel_path} не найден импорт arrowfix"
        assert dangerous_lines, f"В {rel_path} не найдены опасные импорты для контроля порядка"

        min_arrowfix = min(lineno for _, lineno in arrowfix_lines)
        first_danger_name, first_danger_lineno = min(dangerous_lines, key=lambda x: x[1])

        assert min_arrowfix < first_danger_lineno, (
            f"В {rel_path} импорт arrowfix (строка {min_arrowfix}) "
            f"идёт ПОСЛЕ опасного {first_danger_name} (строка {first_danger_lineno})"
        )


def test_omni_cut_import_preloads_pyarrow():
    """Импорт omni_cut предзагружает pyarrow до torch."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow не установлен в окружении")

    code = (
        "import sys; "
        f"sys.path.insert(0, {REELSI_ROOT!r}); "
        "from core import omni_cut; "
        "print('pyarrow' in sys.modules, 'torch' in sys.modules)"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    out = res.stdout.strip().split()
    assert len(out) == 2, f"Неожиданный вывод: {res.stdout!r}"
    assert out[0] == "True", f"pyarrow не в sys.modules: {res.stdout!r}"
    assert out[1] == "False", f"torch уже в sys.modules: {res.stdout!r}"


def test_gigaam_cut_main_preloads_pyarrow():
    """Исполнение gigaam_cut/__main__.py предзагружает pyarrow до torch."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow не установлен в окружении")

    code = (
        "import runpy, sys; "
        f"sys.path.insert(0, {REELSI_ROOT!r}); "
        "runpy.run_module('core.gigaam_cut', run_name='_notmain'); "
        "print('pyarrow' in sys.modules, 'torch' in sys.modules)"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    out = res.stdout.strip().split()
    assert len(out) == 2, f"Неожиданный вывод: {res.stdout!r}"
    assert out[0] == "True", f"pyarrow не в sys.modules: {res.stdout!r}"
    assert out[1] == "False", f"torch уже в sys.modules: {res.stdout!r}"


def test_train_breath_preloads_pyarrow():
    """Исполнение train_breath.py предзагружает pyarrow до torch."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow не установлен в окружении")

    target = os.path.join(REELSI_ROOT, "tools", "train_breath.py")
    code = (
        "import runpy, sys; "
        f"sys.path.insert(0, {REELSI_ROOT!r}); "
        f"runpy.run_path({target!r}, run_name='_notmain'); "
        "print('pyarrow' in sys.modules, 'torch' in sys.modules)"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    out = res.stdout.strip().split()
    assert len(out) == 2, f"Неожиданный вывод: {res.stdout!r}"
    assert out[0] == "True", f"pyarrow не в sys.modules: {res.stdout!r}"
    assert out[1] == "False", f"torch уже в sys.modules: {res.stdout!r}"

