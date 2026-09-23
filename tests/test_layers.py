# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож слоёв: `core/` не знает про `api/` и Flask, `doctor.py` — про `api/`.

ПОЧЕМУ он есть. Движок безголового рендера жил в `api/render.py` — HTTP-модуле, — и
`doctor.py` брал поиск After Effects прямо оттуда: диагностика окружения тянула за собой
Flask ради одной функции. Разрез сделан (движок уехал в `core/aerender.py`), но ничто не
мешало ему поехать обратно: обратный импорт `core` → `api` не падает и не мешает тестам,
он просто снова привязывает ядро к HTTP-слою. Ловится это только по исходникам.

Проверяется статически, деревом разбора (как `tests/test_infra_dedup.py`), а не
импортом: импорт `core` в тесте выполнился бы и при обратной зависимости — она видна
лишь в тексте модуля. Относительные импорты разворачиваются в абсолютные по месту
файла (`from .. import api` внутри `core/` — это импорт корневого `api`).

Запуск:  python -m pytest tests/test_layers.py -q
"""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Пакеты, которых в ядре быть не должно: HTTP-слой (`api`) и его движок (flask).
FORBIDDEN_IN_CORE = ("api", "flask")


def _module_names(path):
    """Имена модулей, которые файл импортирует: (имя, номер строки).

    `import a.b` даёт `a.b`; `from a.b import c` — `a.b` и `a.b.c` (c может быть
    подмодулем); относительный импорт разворачивается по месту файла.
    """
    rel = path.relative_to(ROOT)
    pkg = rel.parent.parts                                  # каталог файла от корня
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            # level=1 — текущий пакет, level=2 — родительский и так далее до корня
            base = pkg[:len(pkg) - (node.level - 1)] if node.level else ()
            head = ".".join([p for p in base if p] + ([node.module] if node.module else []))
            if head:
                out.append((head, node.lineno))
            out += [(".".join(p for p in (head, a.name) if p), node.lineno)
                    for a in node.names]
    return out


def _forbidden(path, names):
    """Импорты `path`, попадающие в запретные пакеты (сам пакет или его подмодуль)."""
    bad = []
    for name, line in _module_names(path):
        for forb in names:
            if name == forb or name.startswith(forb + "."):
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{line}: import {name}")
    return bad


def test_core_does_not_import_api_or_flask():
    """Ни один модуль `core/` не импортирует `api` и `flask`.

    Обратный импорт не ломает ни тестов, ни запуска — он ломает слой: ядро снова
    оказывается доступно только вместе с HTTP-сервером (ради этого движок рендера
    и выносили из `api/render.py`)."""
    files = sorted((ROOT / "core").rglob("*.py"))
    assert files, "в core/ не нашлось ни одного модуля — проверять нечего"
    bad = []
    for path in files:
        bad += _forbidden(path, FORBIDDEN_IN_CORE)
    assert not bad, ("ядро импортирует HTTP-слой — вынеси зависимость в core/:\n  "
                     + "\n  ".join(bad))


def test_doctor_does_not_import_api():
    """`doctor.py` не импортирует `api`: диагностика окружения не должна поднимать Flask.

    Раньше он брал `_find_ae` из `api.render` — инверсия слоёв, из-за которой
    проверка «что стоит на машине» требовала собранного веб-слоя."""
    path = ROOT / "doctor.py"
    assert path.is_file(), "нет doctor.py — проверять нечего"
    bad = _forbidden(path, ("api",))
    assert not bad, ("doctor.py импортирует api — движок бери из core/:\n  "
                     + "\n  ".join(bad))
