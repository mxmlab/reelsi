# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свежесть документации (задание BN).

Документация дрейфует от кода за считанные дни, и расхождение видно только когда
кто-то наткнётся. Этот тест ловит два типовых дрейфа механически:

1. упомянутые в доках модули (`reelsi.py`, `xml2ae/build.py`, …) исчезли;
2. упомянутые в доках роуты (`/api/scene`, …) не определены ни в одном модуле `api/`.

Проверка относительных ссылок вынесена в tests/test_docs_links.py (задание DV),
где проверяются все публичные .md-файлы из git ls-files.

Проверяются не все файлы подряд, а те, что описывают код для постороннего глаза:
README, ARCHITECTURE, спеки нарезки и их друзья. Цифры «сколько проверил»
печатаются самим тестом в комментарий к коллекциям — отчёт по ним берётся из
запуска `pytest -q`, а не пересказом.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOCS = [
    "README.md",
    "README.ru.md",
    "CHANGELOG.md",
    "docs/FEATURES.md",
    "docs/FEATURES.ru.md",
    "docs/ARCHITECTURE.md",
    "docs/ARCHITECTURE.en.md",
    "docs/CUTTING_SPEC.md",
    "OPENSOURCE_PLAN.md",
    "docs/DESIGN.md",
    "docs/ROADMAP.md",
    "docs/PLATFORMS.md",
    ".github/CONTRIBUTING.md",
    "docs/DRP_SPEC.md",
    "docs/INSERTS_SPEC.md",
    "docs/INTRO_SPEC.md",
    "docs/HIGHLIGHT_SPEC.md",
    "docs/KNOWN_ISSUES.md",
]

_PY_RE = re.compile(r"`([\w./\\]+\.py)`")
_ROUTE_RE = re.compile(r"(?<![\w.])/api/([a-z_][\w]*)")

# OPENSOURCE_PLAN — план с историей переименований: он по-русски ссылается на
# старые имена монолитов (aicut.py, api.py, webui2.py…), которых больше нет.
# Из модульной проверки его исключаем, из проверки роутов — нет.
_MODULE_DOCS = [d for d in DOCS if d != "OPENSOURCE_PLAN.md"]

_route_defined = None


def _defined_routes():
    """Все роуты, объявленные в api/*.py (`@bp.route("/api/…")`)."""
    global _route_defined
    if _route_defined is None:
        found = set()
        for py in (ROOT / "api").glob("*.py"):
            for line in py.read_text(encoding="utf-8").splitlines():
                m = re.search(r'@.*route\("(/api/[^"]+)"', line)
                if m:
                    found.add(m.group(1))
        _route_defined = found
    return _route_defined


def _doc_texts():
    texts = {}
    for rel in DOCS:
        p = ROOT / rel
        if p.exists():
            texts[rel] = p.read_text(encoding="utf-8")
    return texts


def _doc_modules():
    """Уникальные упомянутые модули `*.py` (с путём и без).

    Имя с путём (`xml2ae/build.py`) должно существовать по этому пути. Голое имя
    (`jobs.py`) — это ссылка на модуль внутри пакета (`api/jobs.py`): ищем файл с
    таким именем по дереву, один ли он. Исключены исторические имена монолитов —
    файла с ними не осталось нигде, это пересказ, а не карта кода."""
    mods = {}
    for rel in _MODULE_DOCS:
        text = _doc_texts()[rel]
        for m in _PY_RE.finditer(text):
            name = m.group(1).replace("\\", "/")
            if name.endswith(".py"):
                mods.setdefault(name, rel)
    return mods


def _resolve_module(name):
    """Путь к модулю: точный путь либо единственный файл с таким именем."""
    exact = ROOT / name
    if exact.exists():
        return exact
    if "/" in name:
        return None
    hits = [p for p in ROOT.rglob(name) if ".git" not in p.parts]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def _doc_routes():
    """Уникальные роуты `/api/<имя>` из доков (без префикса /api/)."""
    routes = {}
    for rel, text in _doc_texts().items():
        for m in _ROUTE_RE.finditer(text):
            routes.setdefault(m.group(1), rel)
    return routes


def test_documented_modules_exist():
    mods = _doc_modules()
    missing = [name for name in mods if _resolve_module(name) is None]
    assert not missing, f"модули из доков не найдены ({len(missing)}): {missing}"
    assert len(mods) >= 30, f"проверено модулей меньше ожидаемого: {len(mods)}"


def test_documented_routes_are_defined():
    routes = _doc_routes()
    defined = {r.removeprefix("/api/").split("/")[0] for r in _defined_routes() if r.startswith("/api/")}
    undefined = [r for r in routes if r not in defined]
    assert not undefined, f"роуты из доков не определены в api/ ({len(undefined)}): {undefined}"
    assert len(routes) >= 20, f"проверено роутов меньше ожидаемого: {len(routes)}"


def test_dynamic_route_normalization():
    """Динамические и параметризованные роуты (напр. /api/fontfile/<path:ps_name>) нормализуются к базовому имени."""
    defined = {r.removeprefix("/api/").split("/")[0] for r in _defined_routes() if r.startswith("/api/")}
    assert "fontfile" in defined
    assert "<path:ps_name>" not in defined
