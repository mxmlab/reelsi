# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож слоёв: `api/` не импортирует CLI.

Зачем. `find_cam_dirs`, `list_videos` и `process_pair` жили в CLI-модуле `reelsi.py`,
и HTTP-слой брал их оттуда: командная строка оказывалась фундаментом бэкенда, а
умолчания нарезки существовали в двух местах — в парсере CLI и в `Namespace`,
который собирал `api/jobs.py`. Всё это переехало в `core/`, и обратной дороги быть
не должно: `import reelsi` из `api/` тянет argparse, интерактивную очередь и
движок ради одной функции.

Проверка статическая, по исходникам: импортировать `api` и ловить ImportError
мало — `import reelsi` внутри функции или в редкой ветке на импорте модуля не
виден, а именно так зависимость и пролежала до этой задачи.

Общий сторож слоёв (все направления зависимостей) — отдельный файл;
этот про одно правило и с ним не сливается.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

API_DIR = os.path.join(ROOT, "api")

# Импорт CLI в любом виде: `import reelsi`, `import reelsi as cli`,
# `import os, reelsi`, `from reelsi import main`, `from reelsi.app_meta import t`.
# `\breelsi\b` — чтобы имя ловилось как отдельный модуль и не цепляло `reelsi_x`.
_CLI_IMPORT = re.compile(r"^\s*(?:import\s+[^\n#]*\breelsi\b[^\n#]*"
                         r"|from\s+reelsi\b)", re.MULTILINE)


def _api_sources():
    """Исходники api/ — по папке, а не списком: новый модуль попадёт под сторож сам."""
    return sorted(f for f in os.listdir(API_DIR) if f.endswith(".py"))


def test_api_does_not_import_cli():
    """Ни один модуль api/ не импортирует reelsi — ни сверху, ни внутри функции."""
    bad = []
    for name in _api_sources():
        text = open(os.path.join(API_DIR, name), encoding="utf-8").read()
        for m in _CLI_IMPORT.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            bad.append(f"api/{name}:{line_no}: {m.group(0).strip()}")
    assert not bad, "api/ импортирует CLI-модуль reelsi:\n" + "\n".join(bad)


def test_guard_actually_reads_the_backend():
    """Сторож смотрит на настоящие исходники api/, а не на пустой список.

    Иначе переезд папки или фильтра оставил бы зелёный тест, который ничего не читает.
    """
    names = _api_sources()
    assert "files.py" in names and "jobs.py" in names and "gdrive.py" in names, names
    assert "import os" in open(os.path.join(API_DIR, "files.py"), encoding="utf-8").read()


def test_guard_sees_a_cli_import():
    """Сам сторож не пустой: подставной исходник с `import reelsi` он ловит.

    Без этого регулярка могла бы «ничего не находить» всегда — и тест выше был бы
    зелёным на любой зависимости.
    """
    sample = "import os\nimport reelsi as cli\nfrom reelsi import main\nx = 1\n"
    assert len(_CLI_IMPORT.findall(sample)) == 2, _CLI_IMPORT.findall(sample)
    assert not _CLI_IMPORT.findall("import reelsi_lite\nfrom reelsi_lite import t\n")
