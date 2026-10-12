# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож повторных объявлений верхнего уровня во всём static/app.

Файлы `static/app/*.js` грузятся пятнадцатью <script> в ОБЩИЙ скоуп. Позднее объявление
функции с тем же именем молча перекрывает раннее: так пустели иконки строк подкаста
(`podIconBtn` объявлена и в 45-podcast.js, и в 46-podcast-editor.js). Повтор `let`/`const`
в двух скриптах падает SyntaxError на весь файл, а `var` и `function` перекрываются
без ошибки. Поэтому повтор любого из трёх видов — ошибка, если он не записан в ALLOW.

Что считаем объявлениями верхнего уровня:
  * `function имя(` и `async function имя(` — только строки с нулевым отступом;
  * `let|const|var` — каждый declarator через запятую, но только у строк с нулевым отступом.
    Разбор идёт по символам с учётом строк, шаблонов и комментариев; объявление без `;`
    или деструктуризация — ошибка разбора, а не молчаливый пропуск.
Вложенные функции (в IIFE и блоках) сюда не попадают: они не в общем скоупе.

Запуск:  py -3.10 -m pytest tests/test_ui_js_redeclare.py -q
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_DIR = os.path.join(ROOT, "static", "app")

# Законные повторы: имя -> почему оно объявлено дважды намеренно (стенд, заглушка и т. п.).
# На 2026-10-11 повторов в static/app нет. Записи добавлять только с причиной,
# и тест сам попросит убрать запись, если повтора больше нет.
ALLOW = {}

_FUNC_RE = re.compile(r"^(?:async\s+)?function\s+([\w$]+)\s*\(", re.M)
_DECL_RE = re.compile(r"^(?:let|const|var)\b", re.M)
_KEYWORD_RE = re.compile(r"(?:let|const|var)\b")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_SKIP_RE = re.compile(r"\s+|//[^\n]*|/\*.*?\*/", re.S)
_QUOTED_RE = re.compile(
    r"'(?:\\.|[^'\\\n])*'"
    r"|\"(?:\\.|[^\"\\\n])*\""
    r"|`(?:\\.|[^`\\])*`",
    re.S,
)
# Регулярное выражение-литерал: `[…]` внутри может содержать `/`, а `[`/`(` — без пары
# (`/^(?:\[|!)/`). Без этого разбор терял скобки и не находил `;`.
_REGEX_LIT_RE = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\[\n])+/[a-z]*")
# Перед `/` такого вида начинается регулярное выражение, а не деление.
_REGEX_PREFIX_CHARS = "(,=:[!&|?{};+-*%<>~^"
_REGEX_KEYWORD_RE = re.compile(r"\b(?:return|typeof|case|in|of|void|throw|delete)$")


def _skip(text, i):
    """Пропускает пробелы и комментарии, возвращает позицию первого значащего символа."""
    while True:
        m = _SKIP_RE.match(text, i)
        if not m or m.end() == i:
            return i
        i = m.end()


def _starts_regex(text, i):
    """Стоит ли `/` в позиции i после оператора или ключевого слова (тогда это регулярка)."""
    before = text[max(0, i - 30):i].rstrip()
    if not before or before[-1] in _REGEX_PREFIX_CHARS:
        return True
    return bool(_REGEX_KEYWORD_RE.search(before))


def _declared_names(text, pos):
    """Имена, которые объявляет let/const/var, начинающееся в text[pos]."""
    i = _KEYWORD_RE.match(text, pos).end()
    names = []
    while True:
        i = _skip(text, i)
        m = _IDENT_RE.match(text, i)
        if not m:
            raise ValueError(
                "строка %d: не разобрать объявление (деструктуризация или опечатка)"
                % _line(text, pos))
        names.append(m.group())
        i = m.end()
        depth = 0
        while True:
            i = _skip(text, i)
            if i >= len(text):
                raise ValueError(
                    "строка %d: объявление без ';' — разбор ненадёжен" % _line(text, pos))
            q = _QUOTED_RE.match(text, i)
            if q:
                i = q.end()
                continue
            ch = text[i]
            if ch == "/" and _starts_regex(text, i):
                r = _REGEX_LIT_RE.match(text, i)
                if r:
                    i = r.end()
                    continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0 and ch == ";":
                return names
            elif depth == 0 and ch == ",":
                i += 1
                break
            i += 1


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _top_level_decls(text):
    """[(имя, строка)] для объявлений верхнего уровня одного файла."""
    out = [(m.group(1), _line(text, m.start())) for m in _FUNC_RE.finditer(text)]
    for m in _DECL_RE.finditer(text):
        line = _line(text, m.start())
        out.extend((name, line) for name in _declared_names(text, m.start()))
    return out


def _collect(app_dir):
    """{имя: [(файл, строка), ...]} по всем *.js в каталоге (без подкаталогов)."""
    seen = {}
    for fname in sorted(os.listdir(app_dir)):
        if not fname.endswith(".js"):
            continue
        with open(os.path.join(app_dir, fname), encoding="utf-8") as f:
            text = f.read()
        for name, line in _top_level_decls(text):
            seen.setdefault(name, []).append((fname, line))
    return seen


def _redeclared(decls, allow):
    """Имена, объявленные больше одного раза и не записанные в allow."""
    return {n: occ for n, occ in decls.items() if len(occ) > 1 and n not in allow}


def _format(dups):
    return "\n".join(
        "%s: %s" % (n, ", ".join("%s:%d" % (f, ln) for f, ln in occ))
        for n, occ in sorted(dups.items()))


def test_имена_верхнего_уровня_объявлены_один_раз():
    decls = _collect(APP_DIR)
    dups = _redeclared(decls, ALLOW)
    assert not dups, (
        "повторное объявление верхнего уровня в static/app (позднее молча перекрывает "
        "раннее; переименовать или внести в ALLOW с причиной):\n" + _format(dups))


def test_белый_список_не_устарел():
    decls = _collect(APP_DIR)
    stale = sorted(n for n in ALLOW if len(decls.get(n, ())) < 2)
    assert not stale, "в ALLOW есть имена, которых уже нет в двух местах: %s" % stale


def test_сторож_видит_объявления_и_не_пустой():
    # Сторож, который ничего не нашёл, проходит всегда. Опорные имена из разных файлов.
    decls = _collect(APP_DIR)
    assert len(decls) > 500, "разбор нашёл подозрительно мало имён: %d" % len(decls)
    assert "iconBtn" in decls and "$" in decls and "LANG" in decls


def test_детектор_ловит_повтор_функции_и_let_в_разных_файлах(tmp_path):
    (tmp_path / "a.js").write_text(
        "function iconBtn(){}\n"
        "let LANG = 'ru', I18N = {};\n"
        "const X = (function(){ return 1; })();\n",
        encoding="utf-8")
    (tmp_path / "b.js").write_text(
        "async function iconBtn(){}\n"
        "let I18N=null;\n"
        "  function nested(){}\n"
        "function nested(){}\n"
        "let ok = 1;\n",
        encoding="utf-8")
    dups = _redeclared(_collect(str(tmp_path)), {})
    assert set(dups) == {"iconBtn", "I18N"}, dups
    assert dups["iconBtn"] == [("a.js", 1), ("b.js", 1)]
    assert dups["I18N"] == [("a.js", 2), ("b.js", 2)]
    # Вложенная (с отступом) функция в общий скоуп не попадает: иначе nested было бы дважды.
    assert "nested" not in dups
