# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пользовательское сообщение с кодом для перевода.

Бэкенд пишет ошибки по-русски, а английский накладывается словарём фронтенда
(static/i18n/en.json, ключ = сам русский текст). Динамические сообщения (с
подстановкой имени файла и т.п.) таким ключом не ловятся — поэтому рядом с текстом
мы передаём машинный код и переменные:

    raise SystemExit(umsg("file_not_found", "Файл не найден: {path}", path=...))

Слой API распаковывает через api._core.umsg_err() в
{error: "…", err: "file_not_found", err_vars: {"path": …}}, а фронт показывает
перевод по коду (ERR_file_not_found в словаре) с подстановкой переменных.

__str__ отдаёт русский текст: CLI (`python -m core.aicut`), логи и старый код,
который делает `str(e)`, видят сообщение как раньше.
"""


class UMsg:
    """Пользовательское сообщение: русский текст + код перевода (+переменные)."""

    def __init__(self, code, msg, vars=None):
        self.code = code
        self.msg = msg
        self.vars = vars or {}

    def __str__(self):
        return self.msg


def umsg(err_code, text, **vars):
    """raise SystemExit(umsg('код', 'русский текст', var=...)).

    Параметры названы не code/msg намеренно: у бэкенд-ошибок это частые имена
    переменных, и `umsg('key_rejected', text, code=401)` упало бы конфликтом."""
    return UMsg(err_code, text, vars)
