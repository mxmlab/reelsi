# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты единого User-Agent во всех исходящих HTTP-запросах.

1. http_req по умолчанию ставит User-Agent = APP_UA (Reelsi/0.2.0-beta).
2. http_req с явным User-Agent (в любом регистре) сохраняет переданный заголовок.
3. Сторож: отсутствие прямых вызовов urllib.request.Request( в ключевых модулях.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.app_meta import APP_NAME, APP_VERSION, APP_UA, http_req


def test_http_req_default_user_agent():
    """http_req без headers ставит User-Agent равным APP_UA."""
    assert APP_UA == f"{APP_NAME}/{APP_VERSION}"
    req = http_req("https://example.com/api")
    assert req.get_header("User-agent") == APP_UA


def test_http_req_custom_user_agent_preserved():
    """http_req с явным User-Agent (любой регистр) оставляет его без изменений."""
    req1 = http_req("https://example.com/api", headers={"User-Agent": "CustomUA/1.0"})
    assert req1.get_header("User-agent") == "CustomUA/1.0"

    req2 = http_req("https://example.com/api", headers={"user-agent": "LowerUA/2.0"})
    assert req2.get_header("User-agent") == "LowerUA/2.0"

    req3 = http_req("https://example.com/api", headers={"USER-AGENT": "UpperUA/3.0"})
    assert req3.get_header("User-agent") == "UpperUA/3.0"


def test_guard_no_bare_urllib_request():
    """Сторож: в aicut/*.py, api/*.py, omni_asr.py, insertlib.py, webui.py
    не должно быть прямых вызовов urllib.request.Request( кроме aicut/video.py (пробы) и app_meta.py.
    """
    target_files = []
    target_files.extend(glob.glob(os.path.join(ROOT, "core", "aicut", "*.py")))
    target_files.extend(glob.glob(os.path.join(ROOT, "api", "*.py")))
    for fname in ("core/omni_asr.py", "core/insertlib.py", "webui.py"):
        p = os.path.join(ROOT, fname)
        if os.path.isfile(p):
            target_files.append(p)

    violations = []
    for filepath in sorted(target_files):
        relpath = os.path.relpath(filepath, ROOT).replace("\\", "/")
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line_no, line in enumerate(lines, 1):
            if "urllib.request.Request(" in line:
                if relpath == "core/app_meta.py":
                    continue
                if relpath == "core/aicut/video.py" and any(
                    "_PROBE_UA" in lines[i]
                    for i in range(max(0, line_no - 2), min(len(lines), line_no + 3))
                ):
                    continue
                violations.append(f"{relpath}:{line_no}: {line.strip()}")

    msg = "\n".join(
        ["Обнаружены прямые вызовы urllib.request.Request() вместо http_req():"]
        + [f"  - {v}" for v in violations]
    )
    assert not violations, msg
