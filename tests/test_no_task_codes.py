# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож: в публикуемых файлах не остаётся ссылок на коды заданий (TASKS.md).

ПОЧЕМУ он есть. Комментарии и спеки годами ссылались на задания журнала —
«(задание ZI)», «задания ZI/ZK», «(задание HL, п. 1)». Читателю публичного
репозитория эти коды ничего не говорят: сам журнал в срез не идёт
(`.publicignore`), и отсылка ведёт в пустоту. Коды убрали, объяснения («почему
так») остались — этот сторож следит, чтобы они не вернулись вместе с новой
правкой: оборот «задание XX» пишется в комментарии машинально.

Проверяются ровно те носители, которые задания и правят:

- `.py` — комментарии и докстринги (одиночные строковые выражения);
- `.js`, `.jsx`, `.css` — комментарии, `.html` — `<!-- -->`;
- документы (`.md`, `.txt`, `.yml`, `.toml`, `LICENSE`, `.gitignore` и прочее
  без расширения) — текст целиком;
- шаблоны сборки `core/xml2ae/*.py` — сверх этого `// …` ВНУТРИ строковых
  литералов ExtendScript: литерал там и есть текст `.jsx`, который уезжает
  получателю, и его комментарии человек читает уже в After Effects;
- эталоны `tests/fixtures/**` — тот же текст `.jsx`, только замороженный.

Строковые литералы прочего кода не проверяются НАРОЧНО: строки интерфейса,
подсказки CLI и сообщения тестов не читает получатель среза, и правятся они
вместе со своими тестами, а не сторожем.

Отдельно не проверяются одиночные сокращения в скобках — «(PV)», «(AI)»,
«(CI)», «(N/M)»: от кода задания их не отличить механикой.
"""
import io
import os
import re
import subprocess
import sys
import tokenize
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402

# Оборот целиком: «задания ZI/ZK», «задание HL, п. 1», «заданию BP». Ловим по
# первой заглавной букве после слова — так в сеть попадают и «задание C», и
# «задание BF2», которых требование `[A-Z]{2}` не видит.
CODE_RE = re.compile(r"задани[еяюий][а-яё]*\s+[A-Z]")

# Сам сторож: в докстринге выше стоят образцы запретного оборота — иначе
# сторож не объяснить.
_ITSELF = "tests/test_no_task_codes.py"

# Шаблоны сборки .jsx: единственное место, где строковый литерал .py И ЕСТЬ
# публикуемый текст (см. докстринг).
_TEMPLATES = "core/xml2ae/"

_DOC_EXT = {".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ""}
_COMMENT_EXT = {".js", ".jsx", ".css"}


def _ignored_patterns():
    """Шаблоны `.publicignore` — единственный источник правды о публикации."""
    path = os.path.join(ROOT, public_slice.IGNORE_FILE)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return public_slice.parse_ignore(f.read())


def _public_files():
    """Публикуемые файлы: `git ls-files` минус `.publicignore`."""
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT,
                                  text=True, encoding="utf-8")
    patterns = _ignored_patterns()
    files = []
    for rel in out.splitlines():
        if not rel or public_slice.is_ignored(rel, patterns):
            continue
        rel_norm = rel.replace("\\", "/")
        if rel_norm == _ITSELF:
            continue
        if os.path.isfile(os.path.join(ROOT, rel)):
            files.append(rel_norm)
    return files


def _line_starts(text):
    """Начала строк ровно по правилам лексера Python: \\n, \\r\\n, \\r."""
    starts = [0]
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\r":
            i += 2 if i + 1 < n and text[i + 1] == "\n" else 1
            starts.append(i)
        elif text[i] == "\n":
            i += 1
            starts.append(i)
        else:
            i += 1
    return starts


def _tokens(text):
    """Токены Python или пустой список: битый файл сторож не должен ронять."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def _py_spans(text, starts):
    """Комментарии и докстринги: единственный текст, который в .py правят."""
    spans = []
    toks = _tokens(text)
    skip = (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
    prev = None
    for i, tok in enumerate(toks):
        if tok.type == tokenize.COMMENT:
            spans.append((starts[tok.start[0] - 1] + tok.start[1],
                          starts[tok.end[0] - 1] + tok.end[1]))
        elif tok.type == tokenize.STRING and prev in (None, tokenize.NEWLINE, tokenize.INDENT,
                                                      tokenize.DEDENT, tokenize.ENCODING):
            nxt = next((t for t in toks[i + 1:] if t.type not in (tokenize.NL, tokenize.COMMENT)),
                       None)
            if nxt is not None and nxt.type == tokenize.NEWLINE:
                spans.append((starts[tok.start[0] - 1] + tok.start[1],
                              starts[tok.end[0] - 1] + tok.end[1]))
        if tok.type not in skip:
            prev = tok.type
    return spans


_STR_HEAD = re.compile(r"(?i)^([rubf]{0,3})('''|\"\"\"|'|\")")

_ESC_SIMPLE = {"n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f",
               "v": "\v", "\\": "\\", "'": "'", '"': '"'}


def _esc(body, i):
    """Значение escape-последовательности body[i:] и её длина в исходнике."""
    nxt = body[i + 1]
    if nxt in "\r\n":                      # продолжение строки: символа нет
        return "", 2 if body[i + 1:i + 3] == "\r\n" else 1
    if nxt in _ESC_SIMPLE:
        return _ESC_SIMPLE[nxt], 2
    try:
        if nxt == "x":
            return chr(int(body[i + 2:i + 4], 16)), 4
        if nxt == "u":
            return chr(int(body[i + 2:i + 6], 16)), 6
        if nxt == "U":
            return chr(int(body[i + 2:i + 10], 16)), 10
        if nxt == "N":
            end = body.index("}", i)
            return unicodedata.lookup(body[i + 3:end]), end - i + 1
    except (ValueError, KeyError):
        return nxt, 2
    if nxt in "01234567":
        j = i + 1
        while j < min(i + 4, len(body)) and body[j] in "01234567":
            j += 1
        return chr(int(body[i + 1:j], 8)), j - i
    return nxt, 2


def _string_parts(raw):
    """Расшифровка строкового литерала: символы и их координаты в исходнике.

    Без расшифровки позиция оборота в исходнике не совпала бы с позицией в тексте,
    который читает получатель `.jsx`: там `\\n` — перенос строки, а не два символа.
    """
    m = _STR_HEAD.match(raw)
    if not m:
        return [], [], []
    quote = m.group(2)
    body = raw[m.end():]
    if body.endswith(quote):
        body = body[:-len(quote)]
    chars, starts, ends = [], [], []
    base = m.end()
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            ch, step = _esc(body, i)
            if ch:
                chars.append(ch)
                starts.append(base + i)
                ends.append(base + i + step)
            i += step
            continue
        chars.append(body[i])
        starts.append(base + i)
        ends.append(base + i + 1)
        i += 1
    return chars, starts, ends


def _string_runs(toks):
    """Подряд идущие литералы одной склейки: `"a" "b"` — в Python одна строка.

    Комментарий ExtendScript пересекает границу кусков (`// …` начинается в одном
    литерале, продолжается в следующем), и по отдельному литералу его не видно.
    """
    runs, cur = [], []
    gap = (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
    for tok in toks:
        if tok.type == tokenize.STRING:
            cur.append(tok)
        elif tok.type in gap and cur:
            continue
        else:
            if cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)
    return runs


def _template_spans(text, starts):
    """`// …` внутри ExtendScript-литералов шаблонов `core/xml2ae/*.py`."""
    spans = []
    for run in _string_runs(_tokens(text)):
        chars, offs, ends = [], [], []
        for tok in run:
            base = starts[tok.start[0] - 1] + tok.start[1]
            c, s, e = _string_parts(tok.string)
            chars.extend(c)
            offs.extend(base + x for x in s)
            ends.extend(base + x for x in e)
        decoded = "".join(chars)
        for a, b in _js_spans(decoded):
            if a < b:
                spans.append((offs[a], ends[b - 1]))
    return spans


def _js_spans(text, line_comment=True, block_comment=True):
    """Комментарии JS/CSS: строковые литералы и шаблоны пропускаем."""
    spans = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == ch or text[i] == "\n":
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            i += 1
            depth = 0
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
                    depth += 1
                    i += 2
                    continue
                if depth and text[i] == "}":
                    depth -= 1
                    i += 1
                    continue
                if text[i] == "`" and depth == 0:
                    i += 1
                    break
                i += 1
            continue
        if line_comment and ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            spans.append((i, j))
            i = j
            continue
        if block_comment and ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            spans.append((i, j))
            i = j
            continue
        i += 1
    return spans


def _html_spans(text):
    spans, i = [], 0
    while True:
        j = text.find("<!--", i)
        if j == -1:
            return spans
        k = text.find("-->", j + 4)
        k = len(text) if k == -1 else k + 3
        spans.append((j, k))
        i = k


def text_spans(rel, text):
    """Куски файла, где ссылка на задание — дефект. None — проверять весь текст."""
    ext = os.path.splitext(rel)[1].lower()
    starts = None
    if ext == ".py":
        starts = _line_starts(text)
        spans = _py_spans(text, starts)
        if rel.replace("\\", "/").startswith(_TEMPLATES):
            spans.extend(_template_spans(text, starts))
        return spans
    if ext in _COMMENT_EXT:
        return _js_spans(text, line_comment=(ext != ".css"))
    if ext in (".html", ".htm"):
        return _html_spans(text)
    if ext in _DOC_EXT:
        return [(0, len(text))]
    return None


def offenders(rel, text):
    """Строки файла с запретным оборотом: [(номер строки, строка)]."""
    spans = text_spans(rel, text)
    if spans is None:
        return []
    bad = []
    for m in CODE_RE.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            continue
        line = text.count("\n", 0, m.start()) + 1
        snippet = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.start())]
        bad.append((line, snippet.strip()))
    return bad


def test_public_files_have_no_task_codes():
    """В комментариях, докстрингах и документах публикуемых файлов нет кодов заданий."""
    files = _public_files()
    assert files, "не найдено публикуемых файлов — проверять нечего"
    bad = []
    for rel in files:
        try:
            text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for line, snippet in offenders(rel, text):
            bad.append(f"{rel}:{line}: {snippet[:140]}")
    print(f"проверено файлов: {len(files)}")
    assert not bad, (
        "в публикуемых файлах остались коды заданий — журнала в срезе нет, "
        "отсылка ведёт в пустоту; объяснение оставь, код убери:\n" + "\n".join(bad))


def test_golden_fixture_is_watched():
    """Эталон .jsx из проверки больше не исключён: он тоже уезжает получателю."""
    assert "tests/fixtures/golden_geometry.jsx" in _public_files(), (
        "эталон сборки выпал из проверяемых файлов")


def test_watchdog_sees_the_pattern():
    """Сторож не выродился: на образце запретного оборота он краснеет."""
    sample = "# тут был код (задание ZI, п. 1)\n"
    assert offenders("sample.py", sample), "сторож не видит «(задание ZI)» в комментарии"
    assert not offenders("sample.py", "x = 1  # код без ссылки\n"), (
        "сторож ругается на чистый комментарий")
    assert not offenders("sample.py", 'MSG = "задание ZI"\n'), (
        "сторож залез в строковый литерал: строки кода править нельзя")
    assert not offenders("sample.js", "var s = '// задание ZI';\n"), (
        "сторож принял строку JS за комментарий")


def test_watchdog_sees_templates_and_fixtures():
    """Шаблоны сборки и эталон — под сторожем: там код видит получатель .jsx."""
    assert offenders("core/xml2ae/template.py", 'JS = "// задание ZI\\n"\n'), (
        "сторож не видит код в комментарии ExtendScript внутри шаблона сборки")
    assert offenders("core/xml2ae/build.py", 'JS = "// задание ZI"\n'
                                             '    " продолжение\\n"\n'), (
        "комментарий ExtendScript склеен из двух литералов — код пропущен")
    assert not offenders("core/xml2ae/template.py", 'JS = "var a = 1; // ок\\n"\n'), (
        "сторож ругается на чистый комментарий шаблона")
    assert offenders("tests/fixtures/golden_geometry.jsx", "// задание ZI\n"), (
        "эталон .jsx сторож не проверяет")
