# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож двуязычной документации: у каждого документа есть пара, и структура в ней та же.

ПОЧЕМУ он есть. Владелец решил вести документацию на двух языках: английском и
русском. Имя оригинала при этом не меняется — на него ссылается полрепозитория,
поэтому правило простое:

* русский оригинал `X.md`  → английский перевод `X.en.md`;
* английский оригинал `X.md` → русский перевод `X.ru.md`;
* имя с суффиксом языка (`X.en.md`, `X.ru.md`) — уже перевод: ему ищется пара
  `X.md`, то есть собственный оригинал (перевод без оригинала не бывает).

Так сделаны `ARCHITECTURE.md`/`ARCHITECTURE.en.md` и `FEATURES.md`/`FEATURES.ru.md`.

Второй сторож — структура: перевод обязан повторять оригинал ПО ФОРМЕ, а не по
смыслу. Заголовки каждого уровня (`#` … `######`) сравниваются по количеству вне
блоков кода: пропавший при переводе раздел или два склеенных заголовка иначе
никто не заметит — перевод построчно не вычитывают, а ссылки на разделы в
репозитории живут.

Архив (`docs/archive/**`) — история, а не карта кода: он в обход не попадает,
как и в остальных сторожах документации.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Каталог документации и каталог архива закрытых аудитов: архив — история.
_DOCS_DIR = Path("docs")
_ARCHIVE_DIR = "docs/archive"

# Суффиксы переводов: имя с ними — перевод, а не оригинал.
_EN_SUFFIX = ".en.md"
_RU_SUFFIX = ".ru.md"

# Орфан-документ: пара «русский оригинал / английский перевод» отстала, её
# синхронизирует отдельное задание. Список задан явно и ровно одним элементом:
# он существует, чтобы сторож не краснел на уже известном расхождении, а не
# чтобы в него складывали новые.
_KNOWN_ORPHANS: tuple[str, ...] = ()   # ARCHITECTURE.en догнана — исключений нет

# Направление перевода знает только человек: по имени файла язык оригинала не
# виден (`CUTTING_SPEC.md` — русский, `DESIGN.md` — английский). Список задан
# явно и той же длины, что пар на диске: новый `docs/*.md` обязан появиться в
# нём вместе со своим переводом, иначе сторож краснеет
# (`test_known_pairs_cover_all_documents`), а не пропускает документ молча.
_KNOWN_PAIRS = {
    "docs/CLA.md": "docs/CLA.ru.md",
    "docs/CUTTING_SPEC.md": "docs/CUTTING_SPEC.en.md",
    "docs/DESIGN.md": "docs/DESIGN.ru.md",
    "docs/DRP_SPEC.md": "docs/DRP_SPEC.en.md",
    "docs/FEATURES.md": "docs/FEATURES.ru.md",
    "docs/HIGHLIGHT_SPEC.md": "docs/HIGHLIGHT_SPEC.en.md",
    "docs/INSERTS_SPEC.md": "docs/INSERTS_SPEC.en.md",
    "docs/INTRO_SPEC.md": "docs/INTRO_SPEC.en.md",
    "docs/KNOWN_ISSUES.md": "docs/KNOWN_ISSUES.en.md",
    "docs/PLATFORMS.md": "docs/PLATFORMS.en.md",
    "docs/ROADMAP.md": "docs/ROADMAP.en.md",
    "docs/STOCK_PROVIDERS.md": "docs/STOCK_PROVIDERS.en.md",
    "docs/TRADEMARK.md": "docs/TRADEMARK.ru.md",
    # Пара отстала, синхронизируется отдельным заданием.
    "docs/ARCHITECTURE.md": "docs/ARCHITECTURE.en.md",
}

# Блок кода (``` или ~~~) с необязательным языком после открывающего забора.
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

# Заголовок markdown: `#` в начале строки и пробел после них. `#` внутри строки
# заголовком не считается, а `#` в блоке кода — тем более (см. докстринг).
_HEADER_RE = re.compile(r"^(#{1,6})\s")


def _is_gitignored(rels: list[str], root: Path) -> set[str] | None:
    """Пути, вырезанные `.gitignore` (личные файлы). None — git недоступен."""
    if not rels:
        return set()
    try:
        res = subprocess.run(["git", "check-ignore", "--stdin"], cwd=str(root),
                             input=("\n".join(rels) + "\n").encode("utf-8"),
                             capture_output=True)
    except OSError:
        return None
    if res.returncode not in (0, 1):          # 0 — есть игнорируемые, 1 — нет
        return None
    return {line.strip() for line in res.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()}


def _header_counts(text: str) -> dict[int, int]:
    """Заголовки `#`-уровней вне блоков кода: {уровень: сколько таких строк}."""
    counts: dict[int, int] = {}
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADER_RE.match(line)
        if m:
            level = len(m.group(1))
            counts[level] = counts.get(level, 0) + 1
    return counts


def _docs(root: Path = ROOT) -> list[str]:
    """Относительные пути `docs/*.md` без архива и файлов под `.gitignore`.

    Обход идёт ПО ФАЙЛАМ на диске, а не по списку git: новые переводы сторож
    обязан видеть сразу, до `git add`. Отсутствие git — не ошибка: отсеять
    личные файлы тогда нечем, а в репозитории их и нет.
    """
    here = root / _DOCS_DIR
    if not here.is_dir():
        return []
    rels = sorted(p.relative_to(root).as_posix() for p in here.glob("*.md"))
    rels = [rel for rel in rels if not rel.startswith(_ARCHIVE_DIR + "/")]
    personal = _is_gitignored(rels, root)
    if personal:
        rels = [rel for rel in rels if rel not in personal]
    return rels


def _expected_pair(rel: str) -> str:
    """Имя парного документа перевода, если он есть в `_KNOWN_PAIRS`.

    Оригинал по-русски получает `.en.md`, по-английски — `.ru.md`; направление
    задано списком (см. комментарий к нему).
    """
    if rel in _KNOWN_PAIRS:
        return _KNOWN_PAIRS[rel]
    if rel.endswith(_EN_SUFFIX):
        return rel[: -len(_EN_SUFFIX)] + ".md"
    if rel.endswith(_RU_SUFFIX):
        return rel[: -len(_RU_SUFFIX)] + ".md"
    return rel[: -len(".md")] + _EN_SUFFIX


def test_translations_look_for_their_original():
    """У перевода пара — его оригинал (`X.en.md` → `X.md`, `X.ru.md` → `X.md`)."""
    assert _expected_pair("docs/FEATURES.ru.md") == "docs/FEATURES.md"
    assert _expected_pair("docs/ARCHITECTURE.en.md") == "docs/ARCHITECTURE.md"
    assert _expected_pair("docs/CUTTING_SPEC.md") == "docs/CUTTING_SPEC.en.md"
    assert _expected_pair("docs/DESIGN.md") == "docs/DESIGN.ru.md"


def test_every_document_has_a_pair():
    """У каждого `docs/*.md` есть пара на другом языке (п.1).

    Пара ищется по имени перевода: оригинал по-русски ждёт `.en.md`, по-английски —
    `.ru.md`. Направление берётся из `_KNOWN_PAIRS`, поэтому новый документ нельзя
    завести без пары мимо списка.
    """
    missing = []
    for rel in _docs():
        if rel in _KNOWN_ORPHANS:
            continue
        pair = _expected_pair(rel)
        if not (ROOT / pair).is_file():
            missing.append(f"{rel}: нет пары {pair}")
    assert not missing, "у документов нет пары на другом языке:\n" + "\n".join(missing)


def test_known_pairs_cover_all_documents():
    """Список пар покрывает все документы: новый `docs/*.md` мимо списка не завести."""
    on_disk = set(_docs())
    covered = set(_KNOWN_PAIRS) | set(_KNOWN_PAIRS.values())
    uncovered = sorted(on_disk - covered)
    assert not uncovered, ("документы не заведены в `_KNOWN_PAIRS` этой проверки: "
                           + ", ".join(uncovered))
    assert len(_KNOWN_PAIRS) >= 11, f"список пар подозрительно короткий: {len(_KNOWN_PAIRS)}"


def test_no_known_orphans():
    """Исключений нет: все пары, включая ARCHITECTURE, синхронны. Новое исключение —
    это отставший перевод, его чинят переводом, а не строкой в списке."""
    assert _KNOWN_ORPHANS == (), _KNOWN_ORPHANS


def test_document_pairs_have_equal_header_counts():
    """Структура перевода один в один: те же заголовки тех же уровней (п.2).

    Идём по обоим именам пары: расхождение ловится и на русском оригинале, и на
    английском, и на самом переводе-файле.
    """
    bad = []
    for source, translation in sorted(_KNOWN_PAIRS.items()):
        if source in _KNOWN_ORPHANS:
            continue
        if not ((ROOT / source).is_file() and (ROOT / translation).is_file()):
            continue                     # пропажу пары ловит соседний тест
        src = _header_counts((ROOT / source).read_text(encoding="utf-8", errors="replace"))
        dst = _header_counts((ROOT / translation).read_text(encoding="utf-8", errors="replace"))
        if src == dst:
            continue
        levels = sorted(set(src) | set(dst))
        diff = ", ".join(f"#{lvl}: {src.get(lvl, 0)} → {dst.get(lvl, 0)}"
                         for lvl in levels if src.get(lvl, 0) != dst.get(lvl, 0))
        bad.append(f"{source} ↔ {translation}: {diff}")
    assert not bad, "структура перевода разъехалась с оригиналом:\n" + "\n".join(bad)


def test_header_counter_ignores_code_blocks():
    """Счётчик заголовков не считает `#` внутри блоков кода и не в начале строки."""
    text = "# Один\n\n```bash\n# не заголовок\n## тоже\n```\n\n## Два\n"
    assert _header_counts(text) == {1: 1, 2: 1}
    assert _header_counts("x # не заголовок\n") == {}
    assert _header_counts("~~~\n### не заголовок\n~~~\n# Заголовок\n") == {1: 1}


def test_watchdog_sees_a_missing_translation(tmp_path):
    """Сторож не выродился: документ без пары виден обходу, и пары для него нет."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ONLY.md").write_text("# Единственный\n", encoding="utf-8")
    assert _docs(tmp_path) == ["docs/ONLY.md"]
    assert not (tmp_path / _expected_pair("docs/ONLY.md")).is_file()


def test_watchdog_sees_a_half_translated_file(tmp_path):
    """Пропавший в переводе заголовок виден счётчику."""
    src = tmp_path / "X.md"
    dst = tmp_path / "X.en.md"
    src.write_text("# Раз\n\n## Два\n\n## Три\n", encoding="utf-8")
    dst.write_text("# One\n\n## Two\n", encoding="utf-8")
    before = _header_counts(src.read_text(encoding="utf-8"))
    after = _header_counts(dst.read_text(encoding="utf-8"))
    assert before != after, "счётчик не увидел пропавший заголовок"
    assert before == {1: 1, 2: 2} and after == {1: 1, 2: 1}
