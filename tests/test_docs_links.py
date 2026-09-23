# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож ссылок во всех публичных документах (TASKS.md).

Обходит .md-файлы из `git ls-files` за вычетом `.publicignore` и проверяет
локальные markdown-ссылки и картинки.

ПОЧЕМУ этот тест есть:
В README.md и README.ru.md уже трижды оставалась битая ссылка на demo.gif
(сначала assets/demo.gif, хотя папка assets не для картинок и файла не было;
аудит 2026-08-09, план Фазы 6 2026-08-14, и снова при подготовке релиза).
Без автоматического сторожа битые относительные ссылки и картинки неизбежно
возвращаются в документацию.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402

LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# Файлы, описывающие внутренний процесс разработки / историю миграции:
# CLAUDE.md инструктирует агентов (включая работу с TASKS.md),
# OPENSOURCE_PLAN.md фиксирует историю закрытых аудитов.
_SKIP_IGNORED_REF_DOCS = {"CLAUDE.md", "OPENSOURCE_PLAN.md"}


def _git_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT,
                                  text=True, encoding="utf-8")
    return [f for f in out.splitlines() if f]


def _ignored_patterns():
    ignore_path = os.path.join(ROOT, public_slice.IGNORE_FILE)
    if not os.path.exists(ignore_path):
        return []
    with open(ignore_path, encoding="utf-8") as f:
        return public_slice.parse_ignore(f.read())


def _is_ignored(relpath):
    """Путь под `.publicignore`?"""
    return public_slice.is_ignored(relpath, _ignored_patterns())


def _public_md_files():
    return [f for f in _git_files() if f.endswith(".md") and not _is_ignored(f)]


def test_markdown_relative_links_exist(capsys):
    """Все относительные markdown-ссылки и картинки ведут на существующие файлы."""
    md_files = _public_md_files()
    assert md_files, "не найдено публичных .md файлов"

    broken = []
    checked_count = 0

    for rel in md_files:
        full_path = os.path.join(ROOT, rel)
        try:
            raw = open(full_path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        # Убираем HTML-комментарии (<!-- ... -->), сохраняя переносы строк
        def _repl_comment(m):
            return "\n" * m.group(0).count("\n")

        clean_text = re.sub(r"<!--.*?-->", _repl_comment, raw, flags=re.DOTALL)
        dir_name = os.path.dirname(rel)
        lines = clean_text.splitlines()

        for line_no, line in enumerate(lines, 1):
            for m in LINK_RE.finditer(line):
                target = m.group(2).strip()
                # Пропускать внешние ссылки, почту, чистые якоря, шаблоны и примеры
                if (
                    target.startswith(("http://", "https://", "mailto:", "#"))
                    or "{" in target
                    or "<" in target
                ):
                    continue

                # Отрезаем якорь в конце пути (docs/X.md#раздел)
                path_only = target.split("#", 1)[0].strip()
                if not path_only:
                    continue

                checked_count += 1
                resolved_rel = os.path.normpath(
                    os.path.join(dir_name, path_only)
                ).replace("\\", "/")
                target_full = os.path.join(ROOT, resolved_rel)

                if not os.path.exists(target_full):
                    broken.append(
                        f"{rel}:{line_no}: ссылка '{target}' -> файл '{resolved_rel}' не найден"
                    )

    print(f"проверено md-файлов: {len(md_files)}, ссылок: {checked_count}")
    assert not broken, "битые ссылки в документации:\n" + "\n".join(broken)
    assert checked_count >= 15, f"проверено ссылок подозрительно мало: {checked_count}"


def test_public_docs_do_not_reference_ignored_files(capsys):
    """Публичные .md не должны упоминать файлы, перечисленные в .publicignore."""
    md_files = _public_md_files()
    assert md_files, "не найдено публичных .md файлов"

    broken = []
    checked_count = 0

    for rel in md_files:
        if rel in _SKIP_IGNORED_REF_DOCS:
            continue
        full_path = os.path.join(ROOT, rel)
        try:
            raw = open(full_path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        def _repl_comment(m):
            return "\n" * m.group(0).count("\n")

        clean_text = re.sub(r"<!--.*?-->", _repl_comment, raw, flags=re.DOTALL)
        dir_name = os.path.dirname(rel)
        lines = clean_text.splitlines()

        for line_no, line in enumerate(lines, 1):
            # 1. Ссылки [текст](путь)
            for m in LINK_RE.finditer(line):
                target = m.group(2).strip()
                if (
                    target.startswith(("http://", "https://", "mailto:", "#"))
                    or "{" in target
                    or "<" in target
                ):
                    continue
                path_only = target.split("#", 1)[0].strip()
                if not path_only:
                    continue
                resolved_rel = os.path.normpath(
                    os.path.join(dir_name, path_only)
                ).replace("\\", "/")
                if _is_ignored(path_only) or _is_ignored(resolved_rel):
                    broken.append(
                        f"{rel}:{line_no}: ссылка '{target}' ведет на файл под .publicignore"
                    )

            # 2. Упоминания в обратных кавычках `файл`
            for m in BACKTICK_RE.finditer(line):
                val = m.group(1).strip()
                if val.endswith("/"):
                    continue
                path_only = val.split("#", 1)[0].strip()
                resolved_rel = os.path.normpath(
                    os.path.join(dir_name, path_only)
                ).replace("\\", "/")
                if _is_ignored(path_only) or _is_ignored(resolved_rel):
                    broken.append(
                        f"{rel}:{line_no}: упоминание `{val}` (файл под .publicignore)"
                    )

        checked_count += 1

    print(f"проверено md-файлов на ссылки под ignore: {checked_count}")
    assert not broken, "упоминания файлов из .publicignore в публичной документации:\n" + "\n".join(broken)

