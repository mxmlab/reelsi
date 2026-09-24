# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Замер покрытия роутов api/ вызовами из тестов (tests/*.py).

Для каждого `@bp.route` (путь + методы) ищет в `tests/*.py` настоящие вызовы
через тестовый клиент Flask (`client.get/post/put/delete(...)` или локальные
функции-помощники, оборачивающие клиент).

Вывод:
- Таблица: «роут | метод | файлов с вызовом | вызовов»
- Список роутов с НУЛЁМ вызовов
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


@dataclass(frozen=True)
class RouteCall:
    """Один зарегистрированный вызов роута из теста."""

    file: str
    line: int
    method: str
    rule: str


@dataclass(frozen=True)
class RouteCoverageInfo:
    """Сводка покрытия одного роута."""

    rule: str
    methods: tuple[str, ...]
    files_count: int
    calls_count: int
    calls: tuple[RouteCall, ...]


def _extract_str(node: ast.AST | None) -> str | None:
    """Извлекает строковый префикс или литерал из узла AST."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                prefix += part.value
            else:
                break
        return prefix
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _extract_str(node.left)
        if left:
            return left
    return None


def get_bp_routes() -> dict[str, tuple[str, ...]]:
    """Возвращает словарь всех роутов api.bp: {rule: (methods...)}."""
    # Импортируем api без запуска сервера
    sys_path_added = False
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
        sys_path_added = True
    try:
        import api
        from flask import Flask

        app = Flask("route_coverage_scanner")
        app.register_blueprint(api.bp)
        routes: dict[str, tuple[str, ...]] = {}
        for rule in app.url_map.iter_rules():
            rule_str = str(rule.rule)
            if not rule_str.startswith("/api/"):
                continue
            methods = sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"})
            routes[rule_str] = tuple(methods)
        return dict(sorted(routes.items()))
    finally:
        if sys_path_added and str(ROOT) in sys.path:
            try:
                sys.path.remove(str(ROOT))
            except ValueError:
                pass


def _match_route(path_str: str | None, bp_routes: dict[str, tuple[str, ...]]) -> str | None:
    """Сопоставляет путь из вызова с зарегистрированным роутом."""
    if not path_str or not isinstance(path_str, str):
        return None
    pure = path_str.split("?")[0].rstrip("/")
    for r in bp_routes:
        base = r.split("<")[0].rstrip("/")
        if pure == base or pure.startswith(base + "/"):
            return r
    return None


def _is_client_attr(node: ast.AST) -> bool:
    """Проверяет, является ли узел обращением к тестовому клиенту Flask."""
    unparsed = ast.unparse(node).lower()
    return "client" in unparsed or unparsed in ("c", "app_client")


def find_route_calls(test_dir: Path | None = None) -> list[RouteCall]:
    """Сканирует test_dir (*.py) и находит все вызовы роутов через тестовый клиент."""
    td = test_dir or (ROOT / "tests")
    bp_routes = get_bp_routes()
    calls: list[RouteCall] = []

    for test_file in sorted(td.glob("*.py")):
        try:
            content = test_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(test_file))
        except (OSError, SyntaxError):
            continue

        var_map: dict[str, str] = {}
        # func_name -> (url_arg_index, method)
        helpers: dict[str, tuple[int, str]] = {}

        # 1. Первый проход: сбор переменных с префиксами /api/ и локальных хелперов (_get, _post)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                s = _extract_str(node.value)
                if s and s.startswith("/api"):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            var_map[target.id] = s
            elif isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        method = child.func.attr.upper()
                        if method in HTTP_METHODS and _is_client_attr(child.func.value):
                            if child.args and isinstance(child.args[0], ast.Name):
                                arg_name = child.args[0].id
                                param_names = [a.arg for a in node.args.args]
                                if arg_name in param_names:
                                    helpers[node.name] = (param_names.index(arg_name), method)

        # 2. Второй проход: поиск прямых вызовов client.<method> и вызовов хелперов
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            call_func = node.func
            target_method: str | None = None
            raw_url_node: ast.AST | None = None

            if isinstance(call_func, ast.Attribute):
                m = call_func.attr.upper()
                if m in HTTP_METHODS and _is_client_attr(call_func.value) and node.args:
                    target_method = m
                    raw_url_node = node.args[0]
            elif isinstance(call_func, ast.Name) and call_func.id in helpers:
                idx, m = helpers[call_func.id]
                if len(node.args) > idx:
                    target_method = m
                    raw_url_node = node.args[idx]

            if target_method and raw_url_node:
                s = _extract_str(raw_url_node)
                if not s and isinstance(raw_url_node, ast.Name) and raw_url_node.id in var_map:
                    s = var_map[raw_url_node.id]

                matched_rule = _match_route(s, bp_routes)
                if matched_rule:
                    calls.append(
                        RouteCall(
                            file=test_file.name,
                            line=node.lineno,
                            method=target_method,
                            rule=matched_rule,
                        )
                    )

    return calls


def collect_route_coverage(test_dir: Path | None = None) -> list[RouteCoverageInfo]:
    """Формирует сводку покрытия для каждого объявленного роута в api/."""
    bp_routes = get_bp_routes()
    calls = find_route_calls(test_dir)

    calls_by_rule: dict[str, list[RouteCall]] = {r: [] for r in bp_routes}
    for c in calls:
        if c.rule in calls_by_rule:
            calls_by_rule[c.rule].append(c)

    res: list[RouteCoverageInfo] = []
    for rule, methods in bp_routes.items():
        r_calls = tuple(calls_by_rule[rule])
        files = {c.file for c in r_calls}
        res.append(
            RouteCoverageInfo(
                rule=rule,
                methods=methods,
                files_count=len(files),
                calls_count=len(r_calls),
                calls=r_calls,
            )
        )
    return res


def get_zero_call_routes(test_dir: Path | None = None) -> list[RouteCoverageInfo]:
    """Возвращает список роутов с нулём вызовов из тестов."""
    return [r for r in collect_route_coverage(test_dir) if r.calls_count == 0]


def format_table(coverage: list[RouteCoverageInfo]) -> str:
    """Форматирует сводную таблицу покрытия роутов."""
    lines: list[str] = [
        f"{'роут':<35} | {'метод':<10} | {'файлов с вызовом':<16} | {'вызовов':<7}",
        "-" * 76,
    ]
    for r in coverage:
        m_str = ",".join(r.methods)
        lines.append(f"{r.rule:<35} | {m_str:<10} | {r.files_count:<16} | {r.calls_count:<7}")
    return "\n".join(lines)


def main() -> int:
    """Точка входа CLI."""
    coverage = collect_route_coverage()
    zero_calls = [r for r in coverage if r.calls_count == 0]

    print(format_table(coverage))
    print("\n" + "=" * 76)
    print(f"Всего роутов в api/: {len(coverage)}")
    print(f"Роутов с вызовами: {len(coverage) - len(zero_calls)}")
    print(f"Роутов с НУЛЁМ вызовов: {len(zero_calls)}")
    if zero_calls:
        print("\nСписок роутов с НУЛЁМ вызовов:")
        for z in zero_calls:
            print(f"  - {z.rule} [{' ,'.join(z.methods)}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
