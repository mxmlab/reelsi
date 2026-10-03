# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Список файлов дерева для сторожей среза: git, а без него — обход.

Сторожа раскладки, ссылок и обезличивания (`test_layout`, `test_docs_links`,
`test_no_task_codes`, `test_public_clean`, `test_review_fixes`) спрашивали список
файлов у git. В рабочей копии и в срезе `tools/slice_check.py` это работает (в
каталоге среза заводится свой git), а в docker-прогоне CI дерево распаковано БЕЗ
`.git` — `git ls-files` там падает с кодом 128, и сторож краснел не по делу.

Поэтому список берётся двумя дверями:

* git есть и работает — как раньше (`--cached` — отслеживаемые; `--others
  --exclude-standard` — ещё не попавшие в индекс, без мусора из `.gitignore`);
* git недоступен — обход дерева с постаничными пропусками: каталоги сборки и
  кэшей, `.publicignore` и `.gitignore` (шаблоны, оканчивающиеся на `/`, — это
  каталоги). В распакованном дереве нет ни личных файлов, ни мусора, поэтому
  обход даёт ровно тот же список, что дал бы git.

`tracked` и `staged` — два имени одного вызова: первое для сторожей раскладки и
документов (достаточно уже известных файлов), второе — для сторожей, которым
нужен и НОВЫЙ файл до `git add` (см. test_public_clean).
"""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Каталоги, которые не смотрит ни git (мусор в `.gitignore`), ни обход: кэши,
# окружения, каталог самого git. Отдельный список нужен, потому что тесты
# сторожей обезличивания читают файлы, а не только сверяют имена.
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
              ".venv", "venv", "node_modules"}


def _ignore_patterns() -> list[str]:
    """Шаблоны `.gitignore` и `.publicignore` — оба файла лежат в корне."""
    pats: list[str] = []
    for name in (".gitignore", ".publicignore"):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                pat = line.strip()
                if pat and not pat.startswith("#"):
                    pats.append(pat.replace("\\", "/"))
    return pats


def _matches(rel: str, pats: list[str]) -> bool:
    """Путь под шаблонами игнора (та же семантика, что у `.publicignore`)."""
    import fnmatch
    for pat in pats:
        if pat.endswith("/**"):
            if rel.startswith(pat[:-3]):
                return True
        elif pat.endswith("/"):
            if rel.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False


def _walk(root: str) -> list[str]:
    """Обход дерева без git: относительные пути с «/», без каталогов кэшей.

    Игнор проверяется и для каталогов, и для файлов: без git некому применить
    `.gitignore`/`.publicignore`, а личные файлы в распакованном дереве читать
    незачем (их там и нет).
    """
    pats = _ignore_patterns()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS
                       and not _matches((rel_dir + "/" + d).lstrip("/"), pats)]
        for f in filenames:
            rel = (rel_dir + "/" + f).lstrip("/")
            if _matches(rel, pats):
                continue
            out.append(rel)
    return sorted(out)


def git_files(root: str = ROOT, with_untracked: bool = True) -> list[str]:
    """Список файлов дерева: git, а без git — обход (см. докстринг модуля)."""
    args = ["git", "ls-files"]
    if with_untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    try:
        out = subprocess.check_output(args, cwd=root, text=True, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError):
        return _walk(root)
    seen: set[str] = set()
    files: list[str] = []
    for f in out.splitlines():
        rel = f.replace("\\", "/")
        if rel and rel not in seen:
            seen.add(rel)
            files.append(rel)
    return files


def tracked(root: str = ROOT) -> list[str]:
    """Уже известные файлы (для сторожей раскладки и документов)."""
    return git_files(root, with_untracked=False)


def staged(root: str = ROOT) -> list[str]:
    """Известные плюс новые файлы, которые попали бы в коммит."""
    return git_files(root, with_untracked=True)
