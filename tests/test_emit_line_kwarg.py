# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание PE: функции вывода принимают шаблон позиционно-только.

Шаблон вывода первым аргументом `line` и подстановки `**vars` приводили к
TypeError: got multiple values for argument 'line' при вызове emit("...{line}...", line=...).
В cutjob._forced_align исключение молча глоталось, и выравнивание слов отбрасывалось.

Сторожа:
1. По AST: у каждой функции в api/, core/, tools/, корневых *.py, у которой первый
   параметр line и есть **-параметр, — line позиционный-только (posonlyargs).
2. Поведение: console_emit, wrap_emit(fn) и api._core.emit с ("x {line}", line="y")
   дают строку "x y" (перехват вывода) и не бросают TypeError.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _find_line_kwarg_functions() -> list[tuple[str, int, str, bool]]:
    """Найти все функции в api/, core/, tools/ и корневых *.py, у которых первый
    параметр называется line и есть **-параметр (vars/extra/kwargs).

    Возвращает список кортежей (относительный_путь, строка, имя_функции, is_posonly).
    """
    search_files: list[Path] = []
    for d in ("api", "core", "tools"):
        dir_path = ROOT / d
        if dir_path.is_dir():
            search_files.extend(sorted(dir_path.rglob("*.py")))
    search_files.extend(sorted(ROOT.glob("*.py")))

    results: list[tuple[str, int, str, bool]] = []
    for file_path in search_files:
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                first_name: str | None = None
                is_posonly = False
                if args.posonlyargs:
                    first_name = args.posonlyargs[0].arg
                    is_posonly = (first_name == "line")
                elif args.args:
                    first_name = args.args[0].arg

                if first_name == "line" and args.kwarg is not None:
                    rel_path = file_path.relative_to(ROOT).as_posix()
                    results.append((rel_path, node.lineno, node.name, is_posonly))

    return results


def test_emit_functions_ast_posonly() -> None:
    """AST-сторож: у каждой функции в api/, core/, tools/, корневых *.py,
    у которой первый параметр line и есть **-параметр, — line позиционный-только (posonlyargs).
    """
    funcs = _find_line_kwarg_functions()
    assert funcs, "Не найдено ни одной функции с первым параметром line и **-параметром"

    non_posonly = [f"{path}:{line} def {name}" for path, line, name, is_pos in funcs if not is_pos]
    assert not non_posonly, (
        "Параметр 'line' обязан быть позиционно-только (posonlyargs, перед '/') "
        "для предотвращения TypeError при вызове с line=...:\n  "
        + "\n  ".join(non_posonly)
    )


def test_emit_line_kwarg_behavior(capsys: pytest.CaptureFixture[str]) -> None:
    """Поведенческий сторож: console_emit, wrap_emit(fn) и api._core.emit
    с ("x {line}", line="y") дают строку 'x y' (перехват вывода) и не бросают.
    """
    from api._core import JOB, emit
    from core.app_meta import console_emit, wrap_emit

    # 1. console_emit — вывод в stdout
    console_emit("x {line}", line="y")
    captured = capsys.readouterr().out.strip()
    assert captured == "x y"

    # 2. wrap_emit(fn) — передача в обёрнутый колбэк
    captured_wrap: list[str] = []
    cb: Callable[[str], None] = lambda s: captured_wrap.append(s)
    wrapped = wrap_emit(cb)
    wrapped("x {line}", line="y")
    assert captured_wrap == ["x y"]

    # 3. api._core.emit — запись в лог задания
    emit("x {line}", line="y")
    assert JOB["log"], "Запись не попала в JOB['log']"
    last_entry = JOB["log"][-1]
    assert last_entry == {"t": "x {line}", "v": {"line": "y"}}
    formatted = (
        last_entry["t"].format(**last_entry["v"])
        if isinstance(last_entry, dict) and "t" in last_entry
        else str(last_entry)
    )
    assert formatted == "x y"


def test_other_emit_functions_line_kwarg() -> None:
    """Дополнительная проверка remit, previewproxy._emit, videogen.vemit и log_entry:
    вызов с line=... не бросает TypeError и корректно форматирует значение.
    """
    from api.previewproxy import PXJOB, _emit as px_emit
    from api.render import RJOB, remit
    from api.videogen import VJOB, vemit
    from core.jobstate import log_entry

    remit("r {line}", line="ok")
    assert RJOB["log"][-1] == {"t": "r {line}", "v": {"line": "ok"}}

    px_emit("p {line}", line="ok")
    assert PXJOB["log"][-1] == {"t": "p {line}", "v": {"line": "ok"}}

    vemit("v {line}", line="ok")
    assert VJOB["log"][-1] == "v ok"

    entry = log_entry("l {line}", line="ok")
    assert entry == {"t": "l {line}", "v": {"line": "ok"}}
