# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пользовательское сообщение с кодом для перевода и пользовательская ошибка.

Бэкенд пишет ошибки по-русски, а английский накладывается словарём фронтенда
(static/i18n/en.json, ключ = сам русский текст). Динамические сообщения (с
подстановкой имени файла и т.п.) таким ключом не ловятся — поэтому рядом с текстом
мы передаём машинный код и переменные:

    raise ReelsiError(umsg("file_not_found", "Файл не найден: {path}", path=...))

Слой API распаковывает через api._core.umsg_err() в
{error: "…", err: "file_not_found", err_vars: {"path": …}}, а фронт показывает
перевод по коду (ERR_file_not_found в словаре) с подстановкой переменных.

__str__ и `.text` отдают русский текст С ПОДСТАВЛЕННЫМИ переменными: CLI
(`python -m core.aicut`), логи и старый код, который делает `str(e)`, видят
причину, а не шаблон. Пока подстановки не было, в логе, в консоли и в ответе
API лежало «Звук не собрался (код {code}): {err}» — ровно то, что случилось при
падении ffmpeg на сборочном сервере Windows: причину пришлось искать заново.
Сам `.msg` при этом остаётся ШАБЛОНОМ: по нему фронт берёт перевод
(ERR_<код> в static/i18n) и подставляет те же переменные у себя — переводить
уже подставленную строку нечем.

ПОЧЕМУ СВОЙ КЛАСС, А НЕ SystemExit (внешнее ревью 2026-09-22).
Каналом пользовательских ошибок был `raise SystemExit(umsg(…))` — около 250 мест.
SystemExit — наследник BaseException, а не Exception: его молча пропускает любой
`except Exception`, а в потоке `threading` он убивает поток без следа в логе
(в фоновых заданиях это чинили точечно). ReelsiError — обычное
исключение: его видит и `except Exception` — поэтому рядом с каждым таким
обработчиком стоит `except ReelsiError: raise`, и ошибка проходит сквозь него ровно
так же, как раньше проходил SystemExit. Видят её и поток задания, и обработчик
ошибок Flask, и командная строка.
"""
import string
import sys
from typing import Any, NoReturn

# Поля подстановки шаблона: `{err}`, `{path}` и `{dep:.0f}`. Ровно тот же вид,
# что разбирает `t()` фронтенда (static/app/00-core.js): без него строка
# «код {code}» выглядела бы как «код 1» без причины.
_FORMATTER = string.Formatter()


def format_text(msg: str, vars: dict[str, Any]) -> str:
    """Русский текст с подставленными переменными; без них — шаблон как есть.

    Ничего не бросает: сообщение об ошибке — не место для исключения. Кривой
    шаблон (`{` без пары, позиционное поле `{0}`), неизвестный формат или
    отсутствующая переменная оставляют текст ШАБЛОНОМ — как было до этой правки.
    Значения приводятся к строке: формат вроде `{code:.0f}` на числе рассчитан,
    а уронить показ причины из-за него нельзя.
    """
    if not vars:
        return msg
    values = {k: str(v) for k, v in vars.items()}
    try:
        # parse даёт поля и позиционные ({}), и именованные ({err}): позиционные
        # недостижимы — umsg зовут только с именованными, — но проверить их всё
        # равно надо, иначе format упадёт мимо нашей проверки.
        for _literal, field, _spec, _conv in _FORMATTER.parse(msg):
            if field is not None and field != "" and field not in values:
                return msg
        return msg.format(**values)
    except (IndexError, KeyError, ValueError):
        return msg                      # шаблон битый — показываем как есть


class UMsg:
    """Пользовательское сообщение: русский текст + код перевода (+переменные).

    `msg` — ШАБЛОН (его переводит фронт по коду), `text` — он же с подставленными
    переменными (его видит человек в логе, в консоли и в `str(e)`).
    """

    def __init__(self, code: str, msg: str, vars: dict[str, Any] | None = None) -> None:
        self.code = code
        self.msg = msg
        self.vars = vars or {}
        self.text = format_text(msg, self.vars)

    def __str__(self) -> str:
        return self.text


class ReelsiError(Exception):
    """Пользовательская ошибка: `umsg(…)` с кодом перевода ЛИБО готовая строка.

    `str(e)` — русский текст с подставленной причиной, ровно как печатал
    `SystemExit(umsg(…))`. У экземпляра доступны `code` и `vars` для перевода
    (у строки код пустой), а в `args[0]` лежит исходный аргумент — по нему
    `api._core.umsg_err` разбирает и ReelsiError, и оставшиеся SystemExit одним
    и тем же кодом.
    """

    def __init__(self, msg: str | UMsg) -> None:
        super().__init__(msg)
        self.umsg: UMsg | None = msg if isinstance(msg, UMsg) else None
        self.code: str | None = self.umsg.code if self.umsg is not None else None
        self.vars: dict[str, Any] = dict(self.umsg.vars) if self.umsg is not None else {}
        self.text: str = self.umsg.text if self.umsg is not None else str(msg)

    def __str__(self) -> str:
        return self.text


def umsg(err_code: str, text: str, **vars: Any) -> UMsg:
    """raise ReelsiError(umsg('код', 'русский текст', var=...)).

    Параметры названы не code/msg намеренно: у бэкенд-ошибок это частые имена
    переменных, и `umsg('key_rejected', text, code=401)` упало бы конфликтом."""
    return UMsg(err_code, text, vars)


def cli_error(e: BaseException) -> NoReturn:
    """Пользовательская ошибка в командной строке: текст в stderr, код выхода 1.

    Ровно то, что Python делал с `SystemExit(UMsg)` сам: печатал текст сообщения и
    выходил с кодом 1. Точки входа (`reelsi.py`, `doctor.py`, `python -m core.…`)
    зовут её из `except ReelsiError`, чтобы пользователь видел сообщение, а не
    трейсбек."""
    # Без консоли (пайп) у Python на Windows stderr — cp1251/cp1252, и русский текст
    # уезжает в виде \uXXXX (та же починка, что в doctor.py/webui.py). Заодно это
    # делает текст читаемым в логе задания, который родитель снимает из stderr.
    try:
        # sys.stderr в типах — TextIO, а reconfigure есть только у TextIOWrapper:
        # на практике это он и есть, но проверку типов это не устраивает.
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # TextIO не знает reconfigure
    except Exception:
        pass  # поток без reconfigure — текст ошибки всё равно печатаем
    print(str(e), file=sys.stderr)
    raise SystemExit(1) from None
