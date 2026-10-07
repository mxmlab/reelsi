# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
r"""Мутационная проверка теста «два клипа — два спикера».

ПОЧЕМУ этот скрипт есть. Тест «два клипа — два спикера» ловит подмену спикера
КЛИПА общим селектором шага 1 (`val('speaker')`) только там, где эта подмена
попадает в проверяемое поведение. Место, не покрытое тестом, выглядит зелёным — а
владелец потом ищет .jsx не в той папке или собирает клип чужим стилем.

Скрипт перебирает КАЖДЫЙ вызов `clipSpeaker(...)` в `static/app/*.js` (кроме
самого определения двери), подменяет РОВНО это вхождение на `(val('speaker')||'')`
во временной копии файла и гоняет `tests/test_two_speakers.py`. Выжившая мутация
(тест зелёный) — дыра в покрытии: там новая фича прочтёт не того спикера.

Файл правится байтами и возвращается из `finally` байт-в-байт (хеш сверяется):
`git checkout` тут не годится — он вернул бы и чужие незакоммиченные правки.

Запуск: py -3.10 tools/mutate_clip_speaker.py
Код возврата 1, если хоть одна мутация не поймана.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT: Path = Path(__file__).resolve().parent.parent
APP: Path = ROOT / "static" / "app"
TEST_REL: str = "tests/test_two_speakers.py"
MUTATED: str = "(val('speaker')||'')"

# Вызов двери, а не её определение: `openClipSpeaker(` не подходит (регистр), а
# `myclipSpeaker(` — подошёл бы, поэтому слева запрещён символ имени.
CALL_RE = re.compile(r"(?<![A-Za-z0-9_$])clipSpeaker\s*\(")
DEF_RE = re.compile(r"function\s+clipSpeaker\s*\(")


def _calls(src: str) -> list[tuple[int, int, int]]:
    """Границы вызовов `clipSpeaker(...)`: (начало, конец, номер строки).

    Определение двери из списка исключается: его подменять нечем — это и есть
    единственное законное чтение `c.job.speaker`.
    """
    defs = {m.start() + m.group(0).rindex("clipSpeaker")
            for m in DEF_RE.finditer(src)}
    out: list[tuple[int, int, int]] = []
    for m in CALL_RE.finditer(src):
        if m.start() in defs:
            continue
        i = src.index("(", m.start())
        depth = 0
        while i < len(src):
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), i + 1, src.count("\n", 0, m.start()) + 1))
                    break
            i += 1
        else:
            raise AssertionError("не сошлись скобки у вызова clipSpeaker в строке %d"
                                 % (src.count("\n", 0, m.start()) + 1))
    return out


def _sha(data: bytes) -> str:
    """Хеш байтов файла — сверка «вернули ровно то, что было»."""
    return hashlib.sha256(data).hexdigest()


def _pytest_green() -> bool:
    """Прогон теста «два клипа — два спикера»; True — зелёный (мутация выжила)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_REL, "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900)
    return proc.returncode == 0


def main() -> int:
    """Перебрать все вызовы двери и напечатать таблицу «пойман / НЕ пойман»."""
    missed: list[str] = []
    rows: list[str] = []
    for path in sorted(APP.glob("*.js")):
        original = path.read_bytes()
        src = original.decode("utf-8")
        calls = _calls(src)
        if not calls:
            continue
        before = _sha(original)
        for start, end, line in calls:
            mutant = src[:start] + MUTATED + src[end:]
            try:
                path.write_bytes(mutant.encode("utf-8"))
                caught = not _pytest_green()
            finally:
                path.write_bytes(original)
                after = _sha(path.read_bytes())
                assert after == before, ("файл %s не вернулся байт-в-байт "
                                         "(было %s, стало %s)" % (path.name, before, after))
            mark = "пойман" if caught else "НЕ пойман"
            if not caught:
                missed.append("%s:%d" % (path.name, line))
            rows.append("%s:%d → %s" % (path.name, line, mark))
    print("\n".join(rows))
    print("всего мутаций: %d, не поймано: %d" % (len(rows), len(missed)))
    if missed:
        print("ДЫРЫ В ПОКРЫТИИ (тест не видит подмены): " + ", ".join(missed))
        return 1
    print("все пойманы")
    return 0


if __name__ == "__main__":
    sys.exit(main())
