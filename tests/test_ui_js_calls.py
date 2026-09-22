# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Каждый вызов `NAME(…)` в static/app/*.js имеет определение в этих же файлах.

Пойманная регрессия (задание MY, приёмка архитектора 2026-09-19): вместе с мёртвой
панелью слов из `static/app/60-preview.js` уехали функции `shiftIndices` и
`shiftIntroRows`, а их по-прежнему зовёт `static/app/90-ae.js` — пересчёт наборов
HL/BRK/CNT/JNS и строк интро при удалении слова. Ни один сторож этого не видел:
python-тесты читают JS регулярками (имя в файле упомянуто — значит «есть»), а
`node --check` проверяет файлы ПО ОДНОМУ и только синтаксис, а не связи между ними.
В браузере это ReferenceError в момент нажатия, то есть на живом интерфейсе.

Правило: определения и вызовы собираются по ВСЕМ файлам интерфейса разом — они
грузятся пятнадцатью <script> в общем скоупе, и определение из `40-queue.js`
законно видно в `90-ae.js`. Всё, что не определено в проекте, обязано быть в
коротком закрытом списке браузерных глобалей ниже — списком, без подстановочных
масок: неизвестное имя это либо удалённая функция (этот баг), либо опечатка.

Сканируется только код: строковые литералы, текст шаблонов и комментарии
замаскированы. Иначе в «вызовы» уезжает проза из подсказок и комментариев
(«Copyright (c)», «(…не трогать)», «GigaAM (v3)») — и сторож ловит авторов вместо
багов. Обратная сторона: строки разметки с inline-обработчиками (`onclick="foo()"`)
— тоже текст, и этот сторож их не проверяет.

Запуск:  python -m pytest reelsi/tests -q
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import app_meta  # noqa: E402

# Слова языка и модификаторы: `(` после них — синтаксис, а не вызов функции
# (`if(`, `async(no,title)=>`, `function(`). `async` тут именно поэтому: длинная
# стрелка `const phase=async(no,title,dep,has,call)=>{` иначе выглядит вызовом.
KEYWORDS = frozenset("""
if else for while do switch case default break continue return throw try catch finally
function class const let var new delete typeof instanceof in of void yield await async
with this super true false null undefined import export
""".split())

# Браузерные глобали, которыми пользуется фронт. Список закрытый и короткий: имя
# отсюда — это стандартная библиотека, а не «своё». Дописать сюда имя проекта
# нельзя — для этого есть определение функцией в static/app/.
BROWSER_GLOBALS = frozenset("""
AbortController Array Audio Blob Boolean Date Error Function Map MutationObserver
Number Promise RegExp Set String
cancelAnimationFrame clearInterval clearTimeout encodeURIComponent fetch isFinite
isNaN parseFloat parseInt requestAnimationFrame setInterval setTimeout
""".split())

_IDENT = r"[A-Za-z_$][\w$]*"
RX_FUNCTION = re.compile(r"\bfunction\s+(" + _IDENT + r")")
RX_CLASS = re.compile(r"\bclass\s+(" + _IDENT + r")")
RX_DECL = re.compile(r"\b(?:const|let|var)\s+(" + _IDENT + r")")
RX_DESTR = re.compile(r"\b(?:const|let|var)\s*[\[{]([^\]}]*)[\]}]\s*=")
RX_PARAMS_FN = re.compile(r"\bfunction\s*(?:" + _IDENT + r")?\s*\(([^)]*)\)")
RX_PARAMS_ARROW = re.compile(r"\(([^)]*)\)\s*=>")
RX_PARAM_SINGLE = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*=>")
RX_CATCH = re.compile(r"\bcatch\s*\(([^)]*)\)")
RX_CALL = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\(")


def _mask(text):
    """Текст без строк, шаблонного текста, комментариев и регулярок.

    Замена — пробелами той же длины, переводы строк на месте: номера строк в
    отчёте остаются исходными. В шаблонах код из `${…}` сохраняется — там бывают
    настоящие вызовы (`${Math.max(60,…)}`). Деление от регулярки отличается по
    предыдущему значимому символу: после имени, `)` или `]` это деление.
    """
    out = list(text)
    i, n = 0, len(text)
    prev = ""          # последний значимый символ кода
    prev_word = ""     # последнее значимое слово (для `return /re/`)
    regex_before = set("(,=:[!&|?{};+-*%~^<>") | {""}
    regex_words = {"return", "typeof", "case", "in", "of", "do", "else", "void",
                   "delete", "instanceof", "new", "yield", "await"}
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = out[i + 1] = " "
                i += 2
            continue
        if ch in "'\"`":
            quote = ch
            out[i] = " "
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    for k in (i, i + 1):
                        if k < n and text[k] != "\n":
                            out[k] = " "
                    i += 2
                    continue
                if c == "\n":
                    if quote != "`":       # незакрытая строка — не наш случай
                        break
                    i += 1
                    continue
                if quote == "`" and c == "$" and i + 1 < n and text[i + 1] == "{":
                    out[i] = "$"
                    out[i + 1] = "{"
                    i += 2
                    depth = 1
                    while i < n and depth:
                        inner = text[i]
                        if inner == "{":
                            depth += 1
                        elif inner == "}":
                            depth -= 1
                            if not depth:
                                out[i] = "}"
                                i += 1
                                break
                        elif inner in "'\"":
                            q2 = inner
                            out[i] = " "
                            i += 1
                            while i < n:
                                if text[i] == "\\":
                                    for k in (i, i + 1):
                                        if k < n and text[k] != "\n":
                                            out[k] = " "
                                    i += 2
                                    continue
                                if text[i] == q2:
                                    out[i] = " "
                                    i += 1
                                    break
                                if text[i] != "\n":
                                    out[i] = " "
                                i += 1
                            continue
                        elif inner == "/" and i + 1 < n and text[i + 1] == "/":
                            while i < n and text[i] != "\n":
                                out[i] = " "
                                i += 1
                            continue
                        i += 1
                    continue
                if c == quote:
                    out[i] = " "
                    i += 1
                    break
                out[i] = " "
                i += 1
            prev, prev_word = (")" if quote != "`" else "`"), ""
            continue
        if ch == "/" and (prev in regex_before or prev_word in regex_words):
            j = i + 1
            in_class, closed = False, False
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "\n":
                    break
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    closed = True
                    break
                j += 1
            if closed:
                for k in range(i, j + 1):
                    if text[k] != "\n":
                        out[k] = " "
                i = j + 1
                while i < n and text[i].isalpha():      # флаги: /re/gi
                    out[i] = " "
                    i += 1
                prev, prev_word = "/", ""
                continue
        if not ch.isspace():
            prev = ch
            if ch.isalpha() or ch in "_$":
                prev_word = re.match(_IDENT, text[i:]).group(0)
            else:
                prev_word = ""
        i += 1
    return "".join(out)


def _params(text, rx):
    """Имена из списка параметров: они связаны в теле, их вызов — не ReferenceError."""
    names = set()
    for m in rx.finditer(text):
        for name in re.findall(_IDENT, m.group(1)):
            if name not in KEYWORDS:
                names.add(name)
    return names


def _scan(text):
    """(определения, вызовы) по коду: имена проекта и `{имя: [номера строк]}`."""
    defined = set()
    for rx in (RX_FUNCTION, RX_CLASS, RX_DECL, RX_PARAM_SINGLE):
        defined |= set(rx.findall(text))
    for rx in (RX_DESTR, RX_PARAMS_FN, RX_PARAMS_ARROW, RX_CATCH):
        defined |= _params(text, rx)
    calls = {}
    for m in RX_CALL.finditer(text):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        calls.setdefault(name, []).append(text.count("\n", 0, m.start()) + 1)
    return defined, calls


def test_every_js_call_has_a_definition():
    """Вызов без определения — ReferenceError в браузере. Исключений нет, кроме глобалей."""
    files = app_meta.app_js_files()
    assert len(files) >= 15, "файлов интерфейса стало меньше — список берётся из папки?"

    defined, calls = set(), {}
    for path in files:
        defs, found = _scan(_mask(io.open(path, encoding="utf-8").read()))
        defined |= defs
        base = os.path.basename(path)
        for name, lines in found.items():
            calls.setdefault(name, []).extend("%s:%d" % (base, n) for n in lines)

    # Сканер сломался — тест обязан упасть, а не «пройти» на пустом множестве.
    assert len(defined) > 500 and len(calls) > 800, (
        "сканер JS ничего не нашёл: определений %d, вызовов %d" % (len(defined), len(calls)))

    unknown = {k: v for k, v in calls.items()
               if k not in defined and k not in BROWSER_GLOBALS}
    assert not unknown, (
        "вызов без определения в static/app/ (в браузере это ReferenceError: функция "
        "удалена, а зовущий остался, либо опечатка в имени):\n"
        + "\n".join("  %s — %s" % (k, ", ".join(v[:6])) for k, v in sorted(unknown.items())))


def test_scanner_sees_definitions_and_ignores_text():
    """Самопроверка сканера: он обязан видеть код и не видеть строки с комментариями.

    Без неё `_mask` может «победить» незаметно: замаскировать лишнее — и сторож
    станет зелёным на пустом месте. Здесь тот же сценарий, что ловится в живых
    файлах: вызов функции, определение стрелкой и класса, шаблон с вызовом внутри
    `${…}`, деление (не регулярка) и проза в комментарии/строке.
    """
    defined, calls = _scan(_mask(
        "// писал(1) и думал\n"
        "/* удалили(2) */\n"
        "const s='строка (с вызовом) foo(3)';\n"
        "function alpha(x){return x;}\n"
        "const beta=(y)=>alpha(y);\n"
        "class Gamma{}\n"
        "async function run(){beta(1);Gamma();Math.max(1,2);let z=6/2;q(z);}\n"
        "const tpl=`x${alpha(2)}y`;\n"))
    assert {"alpha", "beta", "Gamma", "run", "x", "y"} <= defined, "определения не найдены"
    # Ровно эти вызовы: `alpha` в теле и в шаблоне, `beta`/`Gamma`/`q` — код после
    # деления `6/2` (прими его сканер за регулярку — `q` пропадёт), `run` — само
    # объявление функции. Проза из строки и комментариев (`foo`, `писал`, `удалили`)
    # сюда попасть не должна.
    assert set(calls) == {"alpha", "beta", "Gamma", "run", "q"}, (
        "сканер теряет вызовы или видит текст: %s" % sorted(calls))
