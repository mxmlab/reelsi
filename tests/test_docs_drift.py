# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож дрейфа документации (журнал заданий).

ПОЧЕМУ он есть. Внешнее ревью 2026-09-19 (P2-7) насчитало в документации
расхождения, которые видно только глазами: 12 из 399 ссылок `файл:строка`
указывали за конец файла; в перечне `static/app` не хватало `94-stylepanel.js`
(«четырнадцать файлов» при пятнадцати); `webui.py` назывался «91 строка» при 129;
мёртвый путь `reelsi/tests` жил в пяти местах. `tests/test_docs_freshness.py`
этого не видел: его регэкспы до номеров строк не достают, а отсутствующий
документ он пропускал молча.

Что проверяется — дёшево и без интерпретации:

1. каждая ссылка `путь:строка` и `путь:начало-конец` — файл существует, номер
   не больше числа строк файла; ссылка без пути считается ссылкой от корня
   репозитория (номер строки заявляет точность — путь обязан быть однозначным);
2. `reelsi/<каталог репозитория>` — исторический префикс папки клона; путь
   каталога ВНУТРИ репозитория пишется от корня (`tests`, а не `reelsi/tests`);
3. одинокие бэктик-спаны вида `docs/x.md` — пути от корня репозитория: обязаны
   существовать;
4. перечни файлов `static/app/*.js` в документах совпадают с фактическим набором:
   абзац, где названо пять и больше имён, обязан назвать ВСЕ файлы папки;
5. документ из списка проверяемых есть на диске: пропажа — ошибка, а не тихий
   пропуск. Документ, вырезанный `.publicignore` (публичный
   срез), проверяется, ЕСЛИ он есть, и молча пропускается, если его нет: в
   публичном репозитории его не может быть by construction, а требовать его
   там — красный набор на ровном месте (внешнее ревью 2026-09-22, P0-1).

Что публикуется, решает `.publicignore` — единственный источник правды
(`tools/public_slice.py`, `parse_ignore`/`is_ignored`): своего списка
непубликуемого тест не заводит.

Чего тест НЕ делает — и почему это осознанно:

- **не проверяет числа строк файлов.** Их в документах быть не должно: число
  устаревает с каждой правкой, а неверное число хуже отсутствующего. Историю
  («до 2026-08-06 это был один файл на 2617 строк») тест не трогает — она не
  меняется;
- **не проверяет, что ссылка `файл:строка` ведёт на ТОТ ЖЕ код.** Номер внутри
  файла тест устраивает: свериться со смыслом строки механика не может. Ссылка,
  уехавшая на чужую строку внутри файла, остаётся на совести автора;
- **не разбирает команды и куски кода.** Спаны с пробелами (`python reelsi/webui.py`,
  `tests/fixtures/x.json` при `tests/`) пропускаются: путь там может быть
  относительным к другой папке или внешним;
- **не считает дрейфом пути, которых в репозитории нет и быть не должно:**
  личные файлы под `.gitignore` (`ai_config.json`, `styles/*.json`,
  `docs/speakers-calibration.md`) и внешние папки (`Reelsi_out/`, `_tmp/`,
  `~/.reelsi/...`) — первые отсекает `git check-ignore`, вторые не начинаются
  с имени из корня репозитория.

Архивные доки (каталог архива, `_ARCHIVE_DIR`) пропускаются: это история, а не карта кода.

Отдельно от этого файла живёт `tests/test_docs_freshness.py`:
пропавшие модули, роуты, таблица ступеней, галки `chk_*`.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))
import public_slice  # noqa: E402

# Имена двух корневых документов для агентов обход берёт руками: шаблоны
# `docs/*.md` и `README*.md` их не ловят. Написаны ЛИТЕРАЛАМИ нарочно: склейка
# вида `"CLAUDE" + ".md"` прятала их от сторожа
# `tests/test_review_fixes.py::test_public_files_have_no_dangling_links`, а вместе
# с ними пряталась и настоящая беда — этих документов нет в публичном срезе, и
# тест требовал их там by construction (внешнее ревью 2026-09-22, P0-1).
# Сам этот файл поэтому и стоит в EXCLUDED_FILES того сторожа: перечислять
# непубликуемые документы — его работа.
_ROOT_AGENT_DOCS = ("CLAUDE.md", "AGENTS.md")

# Каталог архива закрытых аудитов и реализованных планов: история, а не карта кода.
_ARCHIVE_DIR = "docs/archive"

# Документы, которые сторож обязан проверить. Список задан руками НАРОЧНО:
# пропавший или переименованный документ должен валить тест, а не уменьшать
# число проверок молча. Новые `docs/*.md` обход подхватывает сам, даже если
# их тут ещё нет.
REQUIRED_DOCS = (
    "README.md",
    "README.ru.md",
    *_ROOT_AGENT_DOCS,
    "docs/ARCHITECTURE.md",
    "docs/ARCHITECTURE.en.md",
    "docs/CUTTING_SPEC.md",
    "docs/DESIGN.md",
    "docs/DRP_SPEC.md",
    "docs/FEATURES.md",
    "docs/FEATURES.ru.md",
    "docs/HIGHLIGHT_SPEC.md",
    "docs/INSERTS_SPEC.md",
    "docs/INTRO_SPEC.md",
    "docs/KNOWN_ISSUES.md",
    "docs/PLATFORMS.md",
    "docs/ROADMAP.md",
)

# `файл.py:123` и `файл.py:123-456`. Перед путём не должно быть буквы, точки,
# слэша или двоеточия — иначе в ссылку попадают хвосты URL и чужих путей.
_REF_RE = re.compile(
    r"(?<![\w./\\:-])"
    r"((?:[\w.][\w./\\-]*/)?[\w.-]+"
    r"\.(?:py|js|jsx|md|json|html|css|ps1|sh|txt|yml|yaml|toml))"
    r":(\d+)(?:\s*[-–—]\s*(\d+))?")

# Историческое имя корня: `reelsi/tests` — это `tests`.
_CLONE_PREFIX_RE = re.compile(r"(?<![\w/.-])reelsi/([\w.-]+)")

# Одинокий бэктик-спан целиком — путь от корня репозитория.
_SPAN_RE = re.compile(r"`([^`\n]+)`")
_PATH_RE = re.compile(r"\.?[\w-]+(?:/[\w.-]+)+")

# Имя файла фронта: `94-stylepanel.js`.
_APP_JS_RE = re.compile(r"\b(\d{2}-[a-z0-9._-]+\.js)\b")

_DOCS_CACHE = None
_IGNORE_PATTERNS = None


def _ignored_patterns():
    """Шаблоны `.publicignore` — единственный источник правды о публикации.

    Файла нет — публикуемым считается всё: тогда сторож работает в полную силу,
    как и до этой правки."""
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
    """Документ обязан попасть в проверку сейчас?

    Правило: непубликуемый документ проверяется, ЕСЛИ он есть
    (приватное дерево), и молча пропускается, если вырезан (публичный срез).
    Публикуемый документ обязан существовать: его пропажу валит
    `test_all_required_docs_exist`, а не тихий пропуск."""
    return (ROOT / rel).is_file() or not _is_ignored(rel)


def _doc_paths():
    """Документы обхода: `docs/*.md`, `README*.md` и два корневых документа для агентов.

    Архивные доки не берутся: каталог архива — подпапка, в `docs/*.md` он не
    попадает, и это здесь же проверяется утверждением.
    Вырезанное `.publicignore` (публичный срез) в обход не попадает: файла там
    нет, проверять нечего."""
    rels = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md"))
    rels = [rel for rel in rels if not rel.startswith(_ARCHIVE_DIR + "/")]
    rels += sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("README*.md"))
    rels += list(_ROOT_AGENT_DOCS)
    rels = [rel for rel in rels if _is_checked(rel)]
    assert not [rel for rel in rels if rel.startswith(_ARCHIVE_DIR + "/")], (
        "обход забрал архивные доки: архив — история, а не карта кода")
    return rels


def _docs():
    """Тексты документов обхода. Пропавший документ — ошибка, не тихий пропуск."""
    global _DOCS_CACHE
    if _DOCS_CACHE is None:
        texts = {}
        for rel in _doc_paths():
            path = ROOT / rel
            assert path.is_file(), (
                f"документ из обхода не найден: {rel} — пропажа документа ошибка, "
                f"а не повод проверить меньше")
            texts[rel] = path.read_text(encoding="utf-8")
        assert texts, "обход не нашёл ни одного документа"
        _DOCS_CACHE = texts
    return _DOCS_CACHE


def _line_of(text, pos):
    """Номер строки (с 1) для позиции в тексте."""
    return text[:pos].count("\n") + 1


def _paragraphs(text):
    """Абзацы текста: пары (номер первой строки, текст абзаца)."""
    out, pos = [], 0
    for m in re.finditer(r"\n[ \t]*\n", text):
        out.append((_line_of(text, pos), text[pos:m.start()]))
        pos = m.end()
    out.append((_line_of(text, pos), text[pos:]))
    return out


def _gitignored(paths):
    """Пути, которые `.gitignore` объявляет личными (их в репозитории нет нарочно).

    `None` — git недоступен: тогда тест, который этим пользуется, пропускается,
    а не падает на ровном месте."""
    if not paths:
        return set()
    try:
        res = subprocess.run(["git", "check-ignore", "--stdin"], cwd=str(ROOT),
                             input=("\n".join(paths) + "\n").encode("utf-8"),
                             capture_output=True)
    except OSError:
        return None
    if res.returncode not in (0, 1):          # 0 — есть игнорируемые, 1 — нет
        return None
    return {line.strip() for line in res.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()}


def test_all_required_docs_exist():
    """Документы из списка проверяемых лежат на диске и попадают в обход.

    Вырезанный `.publicignore` документ из проверки выпадает: в публичном срезе
    его нет и быть не может (внешнее ревью 2026-09-22, P0-1). Публикуемый
    документ обязан существовать, как и раньше."""
    missing = [rel for rel in REQUIRED_DOCS
               if _is_checked(rel) and not (ROOT / rel).is_file()]
    assert not missing, (
        f"документов из списка проверяемых нет ({len(missing)}): {missing} — "
        f"обнови список в тесте или верни документ")
    checked = set(_doc_paths())
    not_checked = [rel for rel in REQUIRED_DOCS
                   if (ROOT / rel).is_file() and rel not in checked]
    assert not not_checked, f"документы есть, но обход их не берёт: {not_checked}"
    present = [rel for rel in REQUIRED_DOCS if (ROOT / rel).is_file()]
    assert len(checked) >= len(present), (
        f"обход проверил меньше документов, чем есть из списка: {len(checked)} < {len(present)}")


def test_file_line_references_are_in_range():
    """Ссылки `файл:строка` не указывают за конец файла и не ведут в пустоту."""
    broken, checked = [], 0
    for rel, text in _docs().items():
        for m in _REF_RE.finditer(text):
            name = m.group(1).replace("\\", "/")
            start, end = int(m.group(2)), m.group(3)
            where = f"{rel}:{_line_of(text, m.start())}"
            target = ROOT / name
            if not target.is_file():
                broken.append(f"{where}: `{m.group(0)}` — файла {name} нет "
                              f"(ссылки в доке — от корня репозитория)")
                continue
            total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            last = int(end) if end else start
            if start > total or last > total:
                broken.append(f"{where}: `{m.group(0)}` — в {name} всего {total} строк")
            elif last < start:
                broken.append(f"{where}: `{m.group(0)}` — конец диапазона раньше начала")
            else:
                checked += 1
    assert not broken, (
        f"ссылки `файл:строка` разъехались с кодом ({len(broken)}):\n" + "\n".join(broken))
    assert checked >= 300, f"проверено ссылок подозрительно мало: {checked}"


def test_no_clone_folder_prefix_for_repo_dirs():
    """Путь каталога репозитория не начинается с имени папки клона.

    `python reelsi/webui.py` — запуск из родительской папки, дока объясняет такую
    форму (и файл клона в ней действительно лежит), поэтому такие команды тест не
    трогает. А каталог ВНУТРИ репозитория так не адресуют: `reelsi/tests` — это
    `tests` от корня. Именно эта ссылка нашлась в пяти местах (ревью 2026-09-19)."""
    bad = []
    for rel, text in _docs().items():
        for m in _CLONE_PREFIX_RE.finditer(text):
            if (ROOT / m.group(1)).is_dir():
                bad.append(f"{rel}:{_line_of(text, m.start())}: `{m.group(0)}` — "
                           f"каталог репозитория, актуальный путь `{m.group(1)}`")
    assert not bad, "путь каталога репозитория назван через папку клона:\n" + "\n".join(bad)


def test_repo_root_paths_exist():
    """Одинокие бэктик-спаны — пути от корня репозитория, и они существуют.

    Спаны с пробелами не разбираются: там команда или перечисление, и путь в них
    может быть относительным к другой папке (`fixtures/…` при `tests/`) или
    внешним (`Reelsi_out/`). Личные файлы под `.gitignore` пропускаются — их
    отсутствие в репозитории законно."""
    top = {p.name for p in ROOT.iterdir()}
    candidates = {}
    for rel, text in _docs().items():
        for span in _SPAN_RE.finditer(text):
            raw = span.group(1).strip()
            if re.search(r"\s", raw):         # команда или перечисление, не путь
                continue
            token = raw.rstrip(".,;:)")
            if not _PATH_RE.fullmatch(token):
                continue
            if any(ch in token for ch in "*<>${}~"):     # шаблоны и переменные
                continue
            if token.split("/", 1)[0] not in top:        # не от корня репозитория
                continue
            candidates.setdefault(token, []).append(f"{rel}:{_line_of(text, span.start())}")

    personal = _gitignored(sorted(candidates))
    if personal is None:
        pytest.skip("git недоступен — личные файлы под .gitignore не отсеять")

    bad = [f"{places[0]}: `{token}` — файла нет (ещё {len(places) - 1} мест)"
           if len(places) > 1 else f"{places[0]}: `{token}` — файла нет"
           for token, places in sorted(candidates.items())
           if token not in personal and not (ROOT / token).exists()]
    assert not bad, ("пути от корня репозитория ведут в пустоту:\n" + "\n".join(bad))


def test_documented_app_js_lists_match_disk():
    """Перечни файлов `static/app/*.js` в документах совпадают с папкой.

    Абзац, где названо пять и больше имён, — это перечень: он обязан назвать все
    файлы папки и не назвать лишних. Числом («четырнадцать файлов») такое не
    проверить — поэтому в доке и должен жить перечень, а не число."""
    on_disk = {p.name for p in (ROOT / "static" / "app").glob("*.js")}
    assert on_disk, "в static/app/ не нашлось ни одного *.js"

    lists, bad = 0, []
    for rel, text in _docs().items():
        for line, block in _paragraphs(text):
            names = set(_APP_JS_RE.findall(block))
            if len(names) < 5:
                continue
            lists += 1
            extra = sorted(names - on_disk)
            missing = sorted(on_disk - names)
            if extra:
                bad.append(f"{rel}:{line}: в перечне лишние файлы: {extra}")
            if missing:
                bad.append(f"{rel}:{line}: в перечне нет файлов: {missing}")
    assert not bad, "перечни static/app разошлись с папкой:\n" + "\n".join(bad)
    assert lists, "перечней `static/app/*.js` в документах не найдено — проверять нечего"
