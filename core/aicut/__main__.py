# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Командная строка пакета:  python -m core.aicut yellow edited.xml

Раньше это был хвост aicut.py и запускалось как `python aicut.py`. Блок
переехал дословно; сменился только способ запуска.
"""
import sys
from .commands import cmd_yellow, cmd_inserts
from core.umsg import ReelsiError, cli_error

if __name__ == "__main__":
    try:
        import argparse, io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        # prog задаём явно: без него argparse печатает в usage «__main__.py», по которому
        # непонятно, что набирать.
        ap = argparse.ArgumentParser(prog="python -m core.aicut",
                                     description="ИИ-разметка монтажа (локальный LM Studio)")
        ap.add_argument("cmd", choices=["yellow", "inserts"])
        ap.add_argument("path", help="edited.xml (yellow/inserts)")
        ap.add_argument("--dry-run", action="store_true", help="показать промпт, не звонить в модель")
        ap.add_argument("--template", help="файл со своим system-промптом")
        ap.add_argument("--model", help="ID модели LM Studio")
        ap.add_argument("--url", help="эндпоинт LM Studio (…/v1)")
        a = ap.parse_args()
        tmpl = open(a.template, encoding="utf-8").read() if a.template else None
        {"yellow": cmd_yellow, "inserts": cmd_inserts}[a.cmd](
            a.path, system=tmpl, dry=a.dry_run, model=a.model, url=a.url)
    except ReelsiError as e:
        cli_error(e)
