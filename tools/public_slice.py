# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сборка публичного среза репозитория без приватной истории и личных данных.

Почему срез, а не `git push public main`:
Приватный репозиторий содержит черновую историю разработки, личные пути
разработчиков (C:\Users\...) и закрытые списки. Прямой push перенесёт все
коммиты приватной истории в публичный доступ.
Скрипт собирает срез напрямую из объектов git (<ref>), отсекает непубликуемые
файлы по .publicignore, проверяет отсутствие личных данных и лимиты размера,
и формирует коммит через временный индекс без изменения рабочего дерева.
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tempfile

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

IGNORE_FILE = ".publicignore"
WORDS_FILE = "tests/personal_words.txt"   # личные слова; сам под .publicignore
SIZE_LIMIT = 100 * 1024 * 1024             # GitHub не принимает файл больше 100 МБ


class SliceCheckError(Exception):
    """Ошибки проверок среза (код 1)."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("\n".join(violations))


class SliceGitError(Exception):
    """Ошибки git или некорректных аргументов (код 2)."""
    pass


def parse_ignore(text: str) -> list[str]:
    """Строки без пустых и `#`."""
    patterns = []
    for line in text.splitlines():
        pat = line.strip()
        if not pat or pat.startswith("#"):
            continue
        patterns.append(pat)
    return patterns


def is_ignored(relpath: str, patterns: list[str]) -> bool:
    """Семантика РОВНО как сегодня в _is_ignored: шаблон на `/**` — префикс, иначе fnmatch."""
    rel = relpath.replace("\\", "/")
    for pat in patterns:
        if pat.endswith("/**"):
            if rel.startswith(pat[:-3]):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False


def parse_words(text: str) -> list[str]:
    """Слова в нижнем регистре, без пустых и `#`."""
    words = []
    for line in text.splitlines():
        w = line.strip()
        if not w or w.startswith("#"):
            continue
        words.append(w.lower())
    return words


def find_personal(rel: str, text: str, words: list[str]) -> list[str]:
    """'rel:номер_строки: слово', подстрокой, регистронезависимо."""
    bad = []
    lower_words = [w.lower() for w in words if w]
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for w in lower_words:
            if w in low:
                bad.append(f"{rel}:{i}: {w}")
    return bad


def oversized(entries: list[tuple[str, int]], limit: int = SIZE_LIMIT) -> list[str]:
    """Entries: [(path, size)]. Возвращает пути файлов больше limit."""
    return [path for path, size in entries if size > limit]


def build_slice(
    root: str,
    ref: str = "HEAD",
    parent: str | None = None,
    message: str | None = None,
    check_only: bool = False,
) -> str | None:
    """Сборка публичного среза из ref git-репозитория."""
    # 1. Проверка коммита ref
    res_ref = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res_ref.returncode != 0:
        raise SliceGitError(f"Коммит не найден: {ref}")

    # 2. .publicignore и WORDS_FILE из ref
    res_ign = subprocess.run(
        ["git", "show", f"{ref}:{IGNORE_FILE}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    patterns = parse_ignore(res_ign.stdout) if res_ign.returncode == 0 else []

    res_words = subprocess.run(
        ["git", "show", f"{ref}:{WORDS_FILE}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res_words.returncode != 0:
        raise SliceGitError("Срез без списка личных слов не собирается")
    words = parse_words(res_words.stdout)

    # 3. git ls-tree -r -l -z <ref>
    res_tree = subprocess.run(
        ["git", "ls-tree", "-r", "-l", "-z", ref],
        cwd=root,
        capture_output=True,
    )
    if res_tree.returncode != 0:
        raise SliceGitError(f"Ошибка git ls-tree: {res_tree.stderr.decode(errors='replace')}")

    kept_entries = []
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
        size_str = meta_parts[3].decode("ascii", errors="replace")

        if obj_type == "commit":
            raise SliceGitError(f"Обнаружен сабмодуль в срезе (запрещено): {path}")

        if is_ignored(path, patterns):
            continue

        size = 0 if size_str == "-" else int(size_str)
        kept_entries.append((mode, sha, size, path))

    # 4. Проверки по оставшимся, ВСЕ сразу
    violations = []

    # 4a. oversized
    entries_for_oversized = [(path, size) for _, _, size, path in kept_entries]
    for bad_file in oversized(entries_for_oversized, limit=SIZE_LIMIT):
        violations.append(f"Файл превышает лимит {SIZE_LIMIT} байт: {bad_file}")

    # 4b. личные слова
    for _, sha, _, path in kept_entries:
        res_blob = subprocess.run(
            ["git", "cat-file", "blob", sha],
            cwd=root,
            capture_output=True,
        )
        if res_blob.returncode != 0:
            raise SliceGitError(f"Не удалось прочитать блоб {sha} для {path}")
        text = res_blob.stdout.decode("utf-8", errors="replace")
        for hit in find_personal(path, text, words):
            violations.append(hit)

    # 4c. отслеживаемое, но игнорируемое по .gitignore
    if kept_entries:
        input_data = ("\n".join(path for _, _, _, path in kept_entries) + "\n").encode("utf-8")
        res_ci = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            input=input_data,
            cwd=root,
            capture_output=True,
        )
        if res_ci.stdout:
            for line in res_ci.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line:
                    violations.append(f"Отслеживаемый файл под .gitignore закоммичен по ошибке: {line}")

    if violations:
        for v in violations:
            print(v)
        raise SliceCheckError(violations)

    # 5. check_only
    total_size = sum(size for _, _, size, _ in kept_entries)
    if check_only:
        print(f"Проверка пройдена. Число файлов: {len(kept_entries)}, суммарный размер: {total_size} байт")
        return None

    # 6. Дерево через временный индекс
    with tempfile.TemporaryDirectory() as td:
        temp_index = os.path.join(td, "temp_git_index")
        env = dict(os.environ, GIT_INDEX_FILE=temp_index)

        index_data = bytearray()
        for mode, sha, _, path in kept_entries:
            line = f"{mode} {sha}\t{path}\0"
            index_data.extend(line.encode("utf-8"))

        res_upd = subprocess.run(
            ["git", "update-index", "-z", "--index-info"],
            input=index_data,
            env=env,
            cwd=root,
            capture_output=True,
        )
        if res_upd.returncode != 0:
            raise SliceGitError(f"Ошибка git update-index: {res_upd.stderr.decode(errors='replace')}")

        res_wt = subprocess.run(
            ["git", "write-tree"],
            env=env,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res_wt.returncode != 0:
            raise SliceGitError(f"Ошибка git write-tree: {res_wt.stderr.strip()}")
        tree_sha = res_wt.stdout.strip()

    # 7. Проверка совпадения с parent
    if parent:
        res_pt = subprocess.run(
            ["git", "rev-parse", "--verify", f"{parent}^{{tree}}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res_pt.returncode != 0:
            raise SliceGitError(f"Родительский коммит не найден: {parent}")
        parent_tree = res_pt.stdout.strip()
        if parent_tree == tree_sha:
            print("Нечего публиковать: дерево изменений совпадает с родителем.")
            return None

    # 8. git commit-tree
    if not message:
        res_meta = subprocess.run(
            ["git", "show", f"{ref}:core/app_meta.py"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        m = re.search(r'APP_VERSION\s*=\s*"(.+?)"', res_meta.stdout) if res_meta.returncode == 0 else None
        if m:
            message = f"Reelsi {m.group(1)}"
        else:
            message = "Reelsi public slice"

    commit_cmd = ["git", "commit-tree", tree_sha]
    if parent:
        commit_cmd.extend(["-p", parent])
    commit_cmd.extend(["-m", message])

    res_ct = subprocess.run(
        commit_cmd,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res_ct.returncode != 0:
        raise SliceGitError(f"Ошибка git commit-tree: {res_ct.stderr.strip()}")
    commit_sha = res_ct.stdout.strip()

    # 9. Печать информации и команды push
    print(f"Срез собран: {commit_sha}")
    print(f"Число файлов: {len(kept_entries)}")
    print(f"git push <url-публичного-репо> {commit_sha}:refs/heads/main")
    return commit_sha


def main(argv: list[str] | None = None, root: str | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Сборка публичного среза репозитория без приватной истории и личных данных."
    )
    parser.add_argument("--ref", default="HEAD", help="Коммит-источник для среза (по умолчанию HEAD)")
    parser.add_argument("--parent", default=None, help="Родительский коммит среза (если срез не первый)")
    parser.add_argument("-m", "--message", default=None, help="Сообщение коммита среза")
    parser.add_argument("--check", action="store_true", help="Только выполнить проверки, без создания коммита")
    parser.add_argument("--root", default=None, help="Корень репозитория (по умолчанию автоопределение)")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    repo_root = args.root or root
    if not repo_root:
        try:
            res_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res_root.returncode == 0 and res_root.stdout.strip():
                repo_root = res_root.stdout.strip()
        except OSError:
            pass
    if not repo_root:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        build_slice(
            root=repo_root,
            ref=args.ref,
            parent=args.parent,
            message=args.message,
            check_only=args.check,
        )
        return 0
    except SliceCheckError:
        return 1
    except SliceGitError as e:
        print(f"Ошибка git: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
