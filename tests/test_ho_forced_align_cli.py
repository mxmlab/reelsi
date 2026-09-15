# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тест флага --forced-align в CLI reelsi.py (задание HO)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def test_reelsi_help_contains_forced_align():
    """`reelsi.py --help` содержит флаг --forced-align."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "reelsi.py", "--help"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0
    assert "--forced-align" in r.stdout


def test_reelsi_parser_sets_forced_align():
    """Разбор аргументов ставит forced_align=True при передаче --forced-align."""
    import reelsi
    parser_fn = getattr(reelsi, "build_parser", None)
    assert parser_fn is not None, "В reelsi.py должна быть функция build_parser()"
    p = parser_fn()
    args = p.parse_args(["--forced-align"])
    assert getattr(args, "forced_align", False) is True
