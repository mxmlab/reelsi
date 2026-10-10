# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обход дерева репозитория не заходит в `.claude` (рабочие копии подагентов).

В корне `reelsi/` лежит `.claude/worktrees/agent-*` — параллельные копии всего
репозитория, которые правят другие сессии. Обход, который в них заходит, падает
на файле, пропавшем на полпути (test_docs_freshness), и сторож корня считает
чужие правки правками тестов. Все обходы берут список каталогов из одного места
— `gitfiles.walk_repo` / `gitfiles.SKIP_DIRS`.
"""
import os

from tests import gitfiles


def _touch(root, rel):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x = 1\n")


def _walked(root, walker):
    """Все файлы, которые отдал обход, относительно корня с «/»."""
    out = []
    for dirpath, _dirs, filenames in walker(root):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append(rel.replace("\\", "/"))
    return sorted(out)


def test_claude_worktree_is_not_walked(tmp_path):
    root = str(tmp_path)
    _touch(root, "probe_api/one.py")
    _touch(root, "probe_core/two.py")
    _touch(root, ".claude/worktrees/agent-x/foo.py")
    _touch(root, ".claude/worktrees/agent-x/probe_api/one.py")
    _touch(root, ".git/HEAD")

    walked = _walked(root, gitfiles.walk_repo)
    assert walked == ["probe_api/one.py", "probe_core/two.py"]

    # Обход без git (запасная дверь `git_files`) — та же функция, тот же список.
    assert gitfiles._walk(root) == ["probe_api/one.py", "probe_core/two.py"]


def test_extra_skip_dirs_are_pruned_too(tmp_path):
    root = str(tmp_path)
    _touch(root, "probe_api/one.py")
    _touch(root, "probe_docs/three.py")

    walked = _walked(root, lambda r: gitfiles.walk_repo(r, skip={"probe_docs"}))
    assert walked == ["probe_api/one.py"]
