# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сверка AST двух ревизий: проверка отсутствия изменений логики.

Сравнивает синтаксические деревья файлов между базовой ревизией git и целевой
ревизией (или рабочим деревом на диске). Умеет нормализовать деревья, отбрасывая
аннотации типов, строки документации, импорты и обёртки cast(T, x).

Используется как критерий приёмки типизации и рефакторингов («логика не менялась»).
"""
import argparse
import ast
import difflib
import glob
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


class AstNormalizer(ast.NodeTransformer):
    """Нормализатор AST с опциональным отбрасыванием типов, докстрингов, импортов и cast."""

    def __init__(
        self,
        *,
        ignore_annotations: bool = False,
        ignore_docstrings: bool = False,
        ignore_imports: bool = False,
        ignore_cast: bool = False,
    ) -> None:
        super().__init__()
        self.ignore_annotations = ignore_annotations
        self.ignore_docstrings = ignore_docstrings
        self.ignore_imports = ignore_imports
        self.ignore_cast = ignore_cast

    def _strip_docstring(self, body: list[ast.stmt]) -> list[ast.stmt]:
        if not self.ignore_docstrings:
            return body
        if body and isinstance(body[0], ast.Expr):
            val = body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                return body[1:]
        return body

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if self.ignore_annotations and hasattr(node, "type_comment"):
            node.type_comment = None
        res = super().generic_visit(node)
        if hasattr(res, "body") and not isinstance(res, ast.Module):
            body = getattr(res, "body")
            if isinstance(body, list) and not body:
                res.body = [ast.Pass()]
        return res

    def visit_Module(self, n: ast.Module) -> ast.Module:
        self.generic_visit(n)
        if self.ignore_docstrings:
            n.body = self._strip_docstring(n.body)
        if self.ignore_annotations and hasattr(n, "type_ignores"):
            n.type_ignores = []
        return n

    def visit_arg(self, n: ast.arg) -> ast.arg:
        if self.ignore_annotations:
            n.annotation = None
            if hasattr(n, "type_comment"):
                n.type_comment = None
        return n

    def visit_FunctionDef(self, n: ast.FunctionDef) -> ast.FunctionDef:
        if self.ignore_annotations:
            n.returns = None
            if hasattr(n, "type_comment"):
                n.type_comment = None
        self.generic_visit(n)
        if self.ignore_docstrings:
            n.body = self._strip_docstring(n.body)
        if not n.body:
            n.body = [ast.Pass()]
        return n

    def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        if self.ignore_annotations:
            n.returns = None
            if hasattr(n, "type_comment"):
                n.type_comment = None
        self.generic_visit(n)
        if self.ignore_docstrings:
            n.body = self._strip_docstring(n.body)
        if not n.body:
            n.body = [ast.Pass()]
        return n

    def visit_ClassDef(self, n: ast.ClassDef) -> ast.ClassDef:
        self.generic_visit(n)
        if self.ignore_docstrings:
            n.body = self._strip_docstring(n.body)
        if not n.body:
            n.body = [ast.Pass()]
        return n

    def visit_AnnAssign(self, n: ast.AnnAssign) -> Any:
        if not self.ignore_annotations:
            return self.generic_visit(n)
        if n.value is None:
            return None
        self.generic_visit(n)
        assign = ast.Assign(targets=[n.target], value=n.value)
        return ast.copy_location(assign, n)

    def visit_Import(self, n: ast.Import) -> Any:
        if self.ignore_imports:
            return None
        return n

    def visit_ImportFrom(self, n: ast.ImportFrom) -> Any:
        if self.ignore_imports:
            return None
        return n

    def visit_Call(self, n: ast.Call) -> Any:
        self.generic_visit(n)
        if self.ignore_cast:
            is_cast = (
                (isinstance(n.func, ast.Name) and n.func.id == "cast")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "cast")
            )
            if is_cast and len(n.args) == 2:
                return n.args[1]
        return n


def normalize_tree(
    tree: ast.AST,
    *,
    ignore_annotations: bool = False,
    ignore_docstrings: bool = False,
    ignore_imports: bool = False,
    ignore_cast: bool = False,
) -> ast.AST:
    """Нормализует AST-дерево с применением заданных флагов фильтрации."""
    normalizer = AstNormalizer(
        ignore_annotations=ignore_annotations,
        ignore_docstrings=ignore_docstrings,
        ignore_imports=ignore_imports,
        ignore_cast=ignore_cast,
    )
    res = normalizer.visit(tree)
    ast.fix_missing_locations(res)
    return res


def normalize_source(
    src: str,
    *,
    ignore_annotations: bool = False,
    ignore_docstrings: bool = False,
    ignore_imports: bool = False,
    ignore_cast: bool = False,
) -> ast.AST:
    """Парсит исходный код и возвращает нормализованное AST-дерево."""
    tree = ast.parse(src)
    return normalize_tree(
        tree,
        ignore_annotations=ignore_annotations,
        ignore_docstrings=ignore_docstrings,
        ignore_imports=ignore_imports,
        ignore_cast=ignore_cast,
    )


def dump_normalized(tree: ast.AST) -> str:
    """Возвращает канонический дамп AST-дерева без атрибутов позиций."""
    return ast.dump(tree, include_attributes=False)


def find_repo_root(start_path: Path | None = None) -> Path:
    """Определяет корень git-репозитория."""
    p = (start_path or Path.cwd()).resolve()
    try:
        r = subprocess.run(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except OSError:
        pass
    return p


def get_changed_python_files(repo_root: Path, base: str, head: str | None) -> list[str]:
    """Возвращает изменённые *.py файлы через git diff --name-only, кроме tests/."""
    cmd = ["git", "-C", str(repo_root), "diff", "--name-only", base]
    if head is not None:
        cmd.append(head)
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        return []
    result: list[str] = []
    for line in r.stdout.splitlines():
        p = line.strip().replace("\\", "/")
        if not p:
            continue
        if p.startswith("tests/"):
            continue
        if p.endswith(".py"):
            result.append(p)
    return sorted(set(result))


def resolve_paths(repo_root: Path, pats: Sequence[str]) -> list[str]:
    """Разрешает пути и glob-шаблоны относительно корня репозитория."""
    resolved: set[str] = set()
    for pat in pats:
        norm_pat = pat.replace("\\", "/")
        candidate = Path(pat)
        if not candidate.is_absolute():
            candidate = repo_root / norm_pat

        if candidate.is_dir():
            for sub in candidate.rglob("*.py"):
                rel = str(sub.relative_to(repo_root)).replace("\\", "/")
                resolved.add(rel)
            continue
        elif candidate.is_file():
            rel = str(candidate.relative_to(repo_root)).replace("\\", "/")
            resolved.add(rel)
            continue

        pattern_to_glob = str(candidate)
        matches = glob.glob(pattern_to_glob, recursive=True)
        if matches:
            for m in matches:
                mp = Path(m)
                if mp.is_dir():
                    for sub in mp.rglob("*.py"):
                        rel = str(sub.relative_to(repo_root)).replace("\\", "/")
                        resolved.add(rel)
                elif mp.suffix == ".py":
                    rel = str(mp.relative_to(repo_root)).replace("\\", "/")
                    resolved.add(rel)
        else:
            if Path(pat).is_absolute():
                try:
                    rel = str(Path(pat).relative_to(repo_root)).replace("\\", "/")
                except ValueError:
                    rel = norm_pat
            else:
                rel = norm_pat
                if rel.startswith("./"):
                    rel = rel[2:]
            resolved.add(rel)
    return sorted(resolved)


def get_content(repo_root: Path, rev: str | None, rel_path: str) -> str | None:
    """Получает содержимое файла: из git (при наличии rev) или с диска."""
    if rev is None:
        disk_path = repo_root / rel_path
        if not disk_path.is_file():
            return None
        try:
            return disk_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    git_path = rel_path.replace("\\", "/")
    res = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{rev}:{git_path}"],
        capture_output=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Сверка AST двух ревизий: проверка отсутствия изменений логики.",
    )
    parser.add_argument("base", help="Базовая ревизия git")
    parser.add_argument(
        "--head",
        default=None,
        help="Целевая ревизия git (по умолчанию: рабочее дерево)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Пути или glob-шаблоны файлов (по умолчанию: изменённые *.py через git diff)",
    )
    parser.add_argument(
        "--ignore-annotations",
        action="store_true",
        help="Игнорировать аннотации типов",
    )
    parser.add_argument(
        "--ignore-docstrings",
        action="store_true",
        help="Игнорировать строки документации у модулей, классов и функций",
    )
    parser.add_argument(
        "--ignore-imports",
        action="store_true",
        help="Игнорировать инструкции импорта",
    )
    parser.add_argument(
        "--ignore-cast",
        action="store_true",
        help="Игнорировать вызовы cast(T, x) -> x",
    )
    parser.add_argument(
        "--types-only",
        action="store_true",
        help="Включить все 4 флага игнорирования (аннотации, докстринги, импорты, cast)",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Печатать unified diff для файлов с различиями (до 40 строк на файл)",
    )
    parser.add_argument(
        "--allow-new",
        action="store_true",
        help="Не возвращать код ошибки 1 при наличии новых (NEW) или удалённых (GONE) файлов",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Путь к репозиторию (по умолчанию: автоопределение по текущей папке)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_intermixed_args(argv)

    repo_root = Path(args.repo).resolve() if args.repo else find_repo_root()
    base: str = args.base
    head: str | None = args.head

    ignore_annotations: bool = bool(args.ignore_annotations or args.types_only)
    ignore_docstrings: bool = bool(args.ignore_docstrings or args.types_only)
    ignore_imports: bool = bool(args.ignore_imports or args.types_only)
    ignore_cast: bool = bool(args.ignore_cast or args.types_only)

    if args.paths:
        paths = resolve_paths(repo_root, args.paths)
    else:
        paths = get_changed_python_files(repo_root, base, head)

    diff_count = 0
    new_count = 0
    gone_count = 0
    head_label = head if head is not None else "working"

    for p in paths:
        content_a = get_content(repo_root, base, p)
        content_b = get_content(repo_root, head, p)

        if content_a is None and content_b is not None:
            print(f"NEW {p}")
            new_count += 1
            continue
        elif content_a is not None and content_b is None:
            print(f"GONE {p}")
            gone_count += 1
            continue
        elif content_a is None and content_b is None:
            print(f"GONE {p}")
            gone_count += 1
            continue

        assert content_a is not None and content_b is not None

        try:
            tree_a = ast.parse(content_a)
        except SyntaxError as e:
            print(f"DIFF {p}")
            diff_count += 1
            if args.diff:
                print(f"  Синтаксическая ошибка в {base}:{p}: {e}")
            continue

        try:
            tree_b = ast.parse(content_b)
        except SyntaxError as e:
            print(f"DIFF {p}")
            diff_count += 1
            if args.diff:
                print(f"  Синтаксическая ошибка в {head_label}:{p}: {e}")
            continue

        norm_a = normalize_tree(
            tree_a,
            ignore_annotations=ignore_annotations,
            ignore_docstrings=ignore_docstrings,
            ignore_imports=ignore_imports,
            ignore_cast=ignore_cast,
        )
        norm_b = normalize_tree(
            tree_b,
            ignore_annotations=ignore_annotations,
            ignore_docstrings=ignore_docstrings,
            ignore_imports=ignore_imports,
            ignore_cast=ignore_cast,
        )

        dump_a = dump_normalized(norm_a)
        dump_b = dump_normalized(norm_b)

        if dump_a == dump_b:
            print(f"same {p}")
        else:
            print(f"DIFF {p}")
            diff_count += 1
            if args.diff:
                lines_a = (ast.unparse(norm_a) + "\n").splitlines(keepends=True)
                lines_b = (ast.unparse(norm_b) + "\n").splitlines(keepends=True)
                udiff = list(
                    difflib.unified_diff(
                        lines_a,
                        lines_b,
                        fromfile=f"{base}:{p}",
                        tofile=f"{head_label}:{p}",
                    )
                )
                if len(udiff) > 40:
                    udiff = udiff[:40]
                for line in udiff:
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")

    bad = False
    if diff_count > 0:
        bad = True
    if (new_count > 0 or gone_count > 0) and not args.allow_new:
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
