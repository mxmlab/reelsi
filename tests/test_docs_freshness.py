# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Свежесть документации.

Документация дрейфует от кода за считанные дни, и расхождение видно только когда
кто-то наткнётся. Этот тест ловит два типовых дрейфа механически:

1. упомянутые в доках модули (`reelsi.py`, `xml2ae/build.py`, …) исчезли;
2. упомянутые в доках роуты (`/api/scene`, …) не определены ни в одном модуле `api/`.

Проверка относительных ссылок вынесена в tests/test_docs_links.py,
где проверяются все публичные .md-файлы из git ls-files.

Проверяются не все файлы подряд, а те, что описывают код для постороннего глаза:
README, ARCHITECTURE, спеки нарезки и их друзья. Цифры «сколько проверил»
печатаются самим тестом в комментарий к коллекциям — отчёт по ним берётся из
запуска `pytest -q`, а не пересказом.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))
import public_slice  # noqa: E402

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

_PY_RE = re.compile(r"`([\w./\\]+\.py)(?::\d+(?:[,\s]*\d+)*(?:[–—-]\d+)?)?`")
_ROUTE_RE = re.compile(r"(?<![\w.])/api/([a-z_][\w]*)")

# OPENSOURCE_PLAN — план с историей переименований: он по-русски ссылается на
# старые имена монолитов (aicut.py, api.py, webui2.py…), которых больше нет.
# Из модульной проверки его исключаем, из проверки роутов — нет.
_MODULE_DOCS = [d for d in DOCS if d != "OPENSOURCE_PLAN.md"]

_route_defined = None
_IGNORE_PATTERNS = None


def _ignored_patterns():
    """Шаблоны `.publicignore` — единственный источник правды о публикации.

    Файла нет — публикуемым считается всё: тогда сторож работает в полную силу,
    как и до этой правки (внешнее ревью 2026-09-22, P0-1)."""
    global _IGNORE_PATTERNS
    if _IGNORE_PATTERNS is None:
        path = ROOT / public_slice.IGNORE_FILE
        _IGNORE_PATTERNS = (public_slice.parse_ignore(path.read_text(encoding="utf-8"))
                            if path.is_file() else [])
    return _IGNORE_PATTERNS


def _is_ignored(rel):
    """Документ вырезан из публикации (перечислен в `.publicignore`)?"""
    return public_slice.is_ignored(rel, _ignored_patterns())


def _is_checked(rel):
    """Документ обязан быть проверен сейчас?

    Правило: непубликуемый документ проверяется, ЕСЛИ он есть
    (приватное дерево), и молча пропускается, если вырезан (публичный срез).
    Публикуемый документ обязан существовать: его пропажу валит
    `test_listed_docs_exist`."""
    return (ROOT / rel).is_file() or not _is_ignored(rel)


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
        if not p.is_file():
            # Вырезанный `.publicignore` документ (публичный срез) проверять нечем;
            # публикуемого тут быть не может — его пропажу ловит test_listed_docs_exist
            # (внешнее ревью 2026-09-22, P0-1).
            assert _is_ignored(rel), f"документ из списка проверяемых не найден: {rel}"
            continue
        texts[rel] = p.read_text(encoding="utf-8")
    return texts


def _doc_modules():
    """Уникальные упомянутые модули `*.py` (с путём и без).

    Имя с путём (`xml2ae/build.py`) должно существовать по этому пути. Голое имя
    (`jobs.py`) — это ссылка на модуль внутри пакета (`api/jobs.py`): ищем файл с
    таким именем по дереву, один ли он. Исключены исторические имена монолитов —
    файла с ними не осталось нигде, это пересказ, а не карта кода."""
    texts = _doc_texts()
    mods = {}
    for rel in _MODULE_DOCS:
        text = texts.get(rel)
        if text is None:
            continue          # документ вырезан из публикации (публичный срез)
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


def test_listed_docs_exist():
    """Каждый документ из списка проверяемых лежит на диске.

    Отдельным тестом, а не только assert'ом в `_doc_texts`: пропавший документ
    должен быть виден в отчёте прогона, а не прятаться за «меньше проверок —
    меньше ошибок». Вырезанный `.publicignore` документ
    (публичный срез) из проверки выпадает: файла там нет и быть не может
    (внешнее ревью 2026-09-22, P0-1)."""
    missing = [rel for rel in DOCS
               if _is_checked(rel) and not (ROOT / rel).is_file()]
    assert not missing, (
        f"документов из списка проверяемых нет ({len(missing)}): {missing} — "
        f"верни документ или убери его из DOCS")
    assert len(DOCS) >= 15, f"список проверяемых документов подозрительно короткий: {len(DOCS)}"


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


# --------------------------------------------------------------------------- #
# Та же свежесть, но по СМЫСЛУ: регэксп ловит пропавший файл, а не разъехавшееся
# значение. Оба дрейфа ниже нашлись: `_PY_RE` не видел ссылок вида
# `file.py:123`, и потому никто не замечал, что таблица ступеней в CUTTING_SPEC
# обещает не то, что лежит в cutstages.STAGES.
# --------------------------------------------------------------------------- #
def test_cutting_spec_stage_table_matches_code():
    """Спека ступеней нарезки обязана совпадать с `core/cutstages.py` — по названию
    и дефолту каждой ступени. Проверяются именно эти колонки: их читает человек,
    выбирая ступень в модалке «Кастом», и по ним же решается, что включено «как
    раньше». `dedupe` стоял в спеке как «Чистка дублей» с дефолтом `True`, хотя в
    коде это «Правка нарезки кодом» с `False`."""
    from core import cutstages

    text = _doc_texts()["docs/CUTTING_SPEC.md"]
    rows = {}
    for line in text.splitlines():
        m = re.match(r"\|\s*`(\w+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", line)
        if m:
            rows[m.group(1)] = {"label": m.group(2).strip(), "default": m.group(4).strip()}

    missing = [s["key"] for s in cutstages.STAGES if s["key"] not in rows]
    assert not missing, f"ступеней нет в таблице CUTTING_SPEC.md: {missing}"
    for s in cutstages.STAGES:
        d = s["default"]
        want = f'`"{d}"`' if isinstance(d, str) else f"`{d}`"
        assert rows[s["key"]]["label"] == s["label"], (
            f"ступень {s['key']}: в спеке «{rows[s['key']]['label']}», "
            f"в cutstages.py «{s['label']}»")
        assert rows[s["key"]]["default"] == want, (
            f"ступень {s['key']}: в спеке {rows[s['key']]['default']}, "
            f"в cutstages.py {want}")


def test_documented_ui_checkboxes_exist():
    """Галки `chk_*`, названные в доках, обязаны существовать в шаблоне.

    Спека обещала чекбокс «черновик mp4» (`chk_draft`, «вкл по умолчанию») в
    карточке шага 1 — такого id нет ни в `templates/index.html`, ни в
    `static/app/*.js`: ступени `draft`/`dedupe` живут в модалке «Кастом», а
    черновик включается вместе с Omni-ревью. Документ врал механически
    проверяемым способом, а поймать это было нечем."""
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    html += "".join(p.read_text(encoding="utf-8")
                    for p in (ROOT / "static" / "app").glob("*.js"))
    known = set(re.findall(r"""id=["'](chk_\w+)["']""", html))
    bad = {}
    for rel, text in _doc_texts().items():
        for name in re.findall(r"`(chk_\w+)`", text):
            if name not in known:
                bad.setdefault(name, rel)
    assert not bad, f"галок из доков нет ни в шаблоне, ни в скриптах: {bad}"
