# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Прогон набора тестов НА ПУБЛИЧНОМ СРЕЗЕ, а не в приватном дереве.

Зачем: тесты гоняются в приватном дереве, а публикуется срез без файлов из
`.publicignore`. Два теста требовали как раз вырезанное — в публичном репозитории они
были красные с самого начала, и никто этого не видел, потому что срез ни разу не
прогонялся. `tools/public_slice.py --check` смотрит личные данные и размеры, но тестов
не гоняет: «собрал и отдал» ничего не говорило о том, что отдали. Здесь срез
материализуется во временный каталог (вне репозитория) и `python -m pytest tests -q`
гоняется уже в нём — тем же интерпретатором, которым запущен сам скрипт.

Правила отбора файлов НЕ дублируются: `.publicignore` читается и разбирается функциями
`tools/public_slice.py` (`IGNORE_FILE`, `parse_ignore`, `is_ignored`). `build_slice` для
этого не годится: он собирает КОММИТ и пишет объекты в `.git` приватного репозитория, а
нужен каталог с файлами — «ничего не писать внутрь репозитория» здесь обязательное
условие.

Внутри каталога среза заводится свой git (`git init` + `git add -A`): часть набора
читает `git ls-files` (`test_docs_links.py`, `test_layout.py`, `test_public_clean.py`,
`test_review_fixes.py`), а в публичном репозитории это клон — без `.git` эти тесты
падают с кодом 128 не по делу. Свой `.git` заодно прекращает поиск репозитория вверх
по дереву: каталог среза может оказаться и внутри другой рабочей копии (`TEMP` иногда
указывает в дерево), а тогда тесты увидели бы чужие приватные файлы.

Код возврата скрипта = код возврата pytest; не собрался срез — 2.
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import public_slice  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PYTEST_ARGS = ["-m", "pytest", "tests", "-q"]
TAIL_LINES = 15                     # сколько последних строк вывода pytest печатать
TREE_PREFIX = "reelsi_slice_"       # префикс временного каталога среза


def _git_text(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """git-команда с текстовым выводом."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _detect_root() -> str:
    """Корень рабочей копии — как в `public_slice.main`, чтобы скрипт шёл из любого каталога."""
    try:
        res = _git_text(["rev-parse", "--show-toplevel"], os.getcwd())
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except OSError:
        pass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def slice_entries(root: str, ref: str = "HEAD") -> list[tuple[str, str, str]]:
    """Файлы среза из `ref`: [(mode, blob_sha, path)].

    Отбор ровно как в `public_slice.build_slice`: `.publicignore` из того же коммита и
    `is_ignored` по каждому пути; сабмодуль — ошибка (в срез он не едет). Размер файла
    здесь не нужен: лимит GitHub проверяет `public_slice.py --check`.
    """
    res_ref = _git_text(["rev-parse", "--verify", f"{ref}^{{commit}}"], root)
    if res_ref.returncode != 0:
        raise public_slice.SliceGitError(f"Коммит не найден: {ref}")

    res_ign = _git_text(["show", f"{ref}:{public_slice.IGNORE_FILE}"], root)
    patterns = public_slice.parse_ignore(res_ign.stdout) if res_ign.returncode == 0 else []

    res_tree = subprocess.run(
        ["git", "ls-tree", "-r", "-l", "-z", ref],
        cwd=root,
        capture_output=True,
    )
    if res_tree.returncode != 0:
        raise public_slice.SliceGitError(f"Ошибка git ls-tree: {res_tree.stderr.decode(errors='replace')}")

    entries = []
    for item in res_tree.stdout.split(b"\0"):
        if not item:
            continue
        parts = item.split(b"\t", 1)
        if len(parts) != 2:
            continue
        meta, path_b = parts
        path = path_b.decode("utf-8", errors="replace")
        meta_parts = meta.split()
        if len(meta_parts) < 4:
            continue
        mode = meta_parts[0].decode("ascii", errors="replace")
        obj_type = meta_parts[1].decode("ascii", errors="replace")
        sha = meta_parts[2].decode("ascii", errors="replace")

        if obj_type == "commit":
            raise public_slice.SliceGitError(f"Обнаружен сабмодуль в срезе (запрещено): {path}")

        if public_slice.is_ignored(path, patterns):
            continue

        entries.append((mode, sha, path))
    return entries


def _write_symlink(target: str, link_to: str) -> None:
    """Симлинк среза — симлинком; где прав нет (Windows без режима разработчика) — файлом с целью.

    Симлинков в дереве сейчас нет, но молча превратить ссылку в текстовый файл хуже,
    чем попробовать: на файле с текстом цели тест упадёт непонятно почему.
    """
    try:
        os.symlink(link_to, target)
    except OSError:
        with open(target, "w", encoding="utf-8") as f:
            f.write(link_to)


def write_tree(root: str, entries: list[tuple[str, str, str]], dest: str) -> int:
    """Кладёт файлы среза в `dest`. Возвращает их число."""
    for mode, sha, path in entries:
        res = subprocess.run(["git", "cat-file", "blob", sha], cwd=root, capture_output=True)
        if res.returncode != 0:
            raise public_slice.SliceGitError(f"Не удалось прочитать блоб {sha} для {path}")
        target = os.path.join(dest, *path.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if mode == "120000":
            _write_symlink(target, res.stdout.decode("utf-8", errors="replace"))
        else:
            with open(target, "wb") as f:
                f.write(res.stdout)
    return len(entries)


def init_git(tree: str) -> None:
    """Свой git внутри каталога среза (зачем — в докстринге модуля)."""
    for args in (["init", "-q"], ["add", "-A"]):
        res = _git_text(args, tree)
        if res.returncode != 0:
            raise public_slice.SliceGitError(f"Ошибка git {' '.join(args)}: {res.stderr.strip()}")


def run_pytest(tree: str) -> subprocess.CompletedProcess:
    """pytest в каталоге среза — тем же интерпретатором, что запущен сам скрипт."""
    return subprocess.run(
        [sys.executable, *PYTEST_ARGS],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def remove_tree(tree: str) -> None:
    """Убрать каталог среза.

    Просто `shutil.rmtree` не хватает: `git add` кладёт блобы в `.git/objects`
    «только для чтения», а на Windows такой файл не удаляется — каталог остаётся
    на диске (поймано тестом на код возврата). Поэтому сначала снимаем флаг.
    """
    for dirpath, dirnames, filenames in os.walk(tree):
        for name in dirnames + filenames:
            try:
                os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(tree, ignore_errors=True)


def tail_lines(text: str, limit: int = TAIL_LINES) -> str:
    """Последние `limit` строк вывода."""
    return "\n".join((text or "").splitlines()[-limit:])


def main(argv: list[str] | None = None, root: str | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Прогон набора тестов на публичном срезе (tools/public_slice.py) во временном каталоге."
    )
    parser.add_argument("--ref", default="HEAD", help="Коммит-источник среза (по умолчанию HEAD)")
    parser.add_argument("--keep", action="store_true", help="Оставить каталог среза на диске (для разбора падений)")
    parser.add_argument("--root", default=None, help="Корень репозитория (по умолчанию автоопределение)")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    repo_root = args.root or root or _detect_root()
    tree = tempfile.mkdtemp(prefix=TREE_PREFIX)
    try:
        entries = slice_entries(repo_root, args.ref)
        write_tree(repo_root, entries, tree)
        init_git(tree)
        print(f"Срез {args.ref}: файлов {len(entries)}")
        print(f"Каталог среза: {tree}")

        res = run_pytest(tree)
        out = tail_lines((res.stdout or "") + (res.stderr or ""))
        if out:
            print(f"Последние {TAIL_LINES} строк вывода pytest:")
            print(out)
        print(f"Код возврата pytest: {res.returncode}")
        return res.returncode
    except public_slice.SliceGitError as e:
        print(f"Ошибка git: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"Каталог среза оставлен: {tree}")
        else:
            remove_tree(tree)


if __name__ == "__main__":
    sys.exit(main())
