# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import app_meta
from core.app_meta import py_exec

def test_py_exec_returns_existing_path():
    p = py_exec()
    assert isinstance(p, str)
    assert os.path.exists(p)

def test_py_exec_respects_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "fake_python.exe"
    fake.write_text("")
    monkeypatch.setenv("REELSI_PYTHON", str(fake))
    monkeypatch.setattr(app_meta, "_PY_EXEC_CACHED", None)
    assert py_exec() == str(fake)
