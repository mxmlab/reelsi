# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
"""Проверка файлов на смешанные концы строк (CRLF + одиночные LF).

Падает с кодом 1, если в файле есть одновременно и CRLF (\\r\\n), и одиночные LF (\\n).
Чистые файлы только с CRLF или только с LF (например, шелл-скрипты по .gitattributes)
считаются корректными и возвращают код 0.
"""
from __future__ import annotations

import subprocess
import sys
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

BINARY_EXTS: frozenset[str] = frozenset({
    ".gz", ".npy", ".mp4", ".mov", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".ico", ".exe", ".dll", ".pyc", ".zip",
    ".tar", ".bin", ".woff", ".woff2", ".ttf", ".eot",
})


def count_lone_lf(data: bytes) -> int:
    """Считает число одиночных LF в байтах (LF, не входящих в CRLF)."""
    return data.replace(b"\r\n", b"").count(b"\n")


def check_file(path: Path | str) -> tuple[bool, int]:
    """Проверяет файл на смешанные концы строк.

    Возвращает (is_mixed, lone_lf_count):
    - is_mixed = True, если в файле есть и CRLF (>0), и одиночные LF (>0);
    - lone_lf_count — количество одиночных LF.
    """
    p = Path(path)
    if not p.is_file():
        return False, 0
    if p.suffix.lower() in BINARY_EXTS:
        return False, 0
    try:
        data = p.read_bytes()
    except OSError:
        return False, 0

    if b"\x00" in data:
        return False, 0

    crlf_count = data.count(b"\r\n")
    lone_lf = count_lone_lf(data)

    is_mixed = (crlf_count > 0 and lone_lf > 0)
    return is_mixed, lone_lf


def check_files(paths: list[str]) -> int:
    """Проверяет список файлов.

    Печатает пути и число одиночных LF для смешанных файлов.
    Возвращает 1, если найден хотя бы один смешанный файл, иначе 0.
    """
    has_errors = False
    for p_str in paths:
        path = Path(p_str)
        if not path.is_file():
            continue
        is_mixed, lone_lf = check_file(path)
        if is_mixed:
            print(f"{p_str}: смешанные концы строк ({lone_lf} одиночных LF)")
            has_errors = True
    return 1 if has_errors else 0


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        try:
            out = subprocess.check_output(
                ["git", "ls-files"], text=True, encoding="utf-8"
            )
            argv = [line.strip() for line in out.splitlines() if line.strip()]
        except Exception:
            return 0
    return check_files(argv)


if __name__ == "__main__":
    sys.exit(main())
