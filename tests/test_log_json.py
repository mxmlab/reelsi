# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторожа для задания HH: защита логов от падения в 500 при не-JSON объектах.

Проверяют:
1. Запись в JOB['log'] через api._core.emit с err=URLError и path=Path отдаётся в
   /api/status как 200 JSON со строковыми v['err'] и v['path'].
2. Запись в RJOB['log'] через api.render.remit с err=URLError и path=Path отдаётся в
   /api/render_status как 200 JSON со строковыми v['err'] и v['path'].
3. Запись в PXJOB['log'] через api.previewproxy._emit с err=URLError и path=Path
   отдаётся в /api/preview_proxy_status как 200 JSON со строковыми v['err'] и v['path'].
4. Сторож «одна дверь»: в api/_core.py, api/render.py, api/previewproxy.py нет
   литерала записи {"t": в обход общей функции (кроме тела log_entry).
5. log_entry корректно обрабатывает чистые строки, словари переменных и не-JSON объекты.
"""
import os
from pathlib import Path
import re
from urllib.error import URLError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def test_emit_job_status_json_safe():
    """api._core.emit с err=URLError и path=Path -> GET /api/status отдает 200 JSON."""
    import webui
    from api._core import JOB, LOCK, emit

    client = webui.app.test_client()
    with LOCK:
        old_log = list(JOB["log"])
    try:
        err_inst = URLError("boom")
        path_inst = Path("/test/path")
        emit("  ✗ {name}: {err} at {path}", name="test_job", err=err_inst, path=path_inst)

        res = client.get("/api/status", headers={"Host": "127.0.0.1:5001"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.get_data(as_text=True)}"
        data = res.get_json()
        assert isinstance(data, dict), "Response must be a JSON object"
        assert len(data.get("log", [])) > 0, "Log must not be empty"
        last = data["log"][-1]
        assert isinstance(last, dict), f"Last log entry must be a dict: {last}"
        assert last.get("t") == "  ✗ {name}: {err} at {path}"
        assert last.get("v", {}).get("err") == str(err_inst)
        assert last.get("v", {}).get("path") == str(path_inst)
    finally:
        with LOCK:
            JOB["log"] = old_log


def test_remit_render_status_json_safe():
    """api.render.remit с err=URLError и path=Path -> GET /api/render_status отдает 200 JSON."""
    import webui
    from api.render import RJOB, RLOCK, remit

    client = webui.app.test_client()
    with RLOCK:
        old_log = list(RJOB["log"])
    try:
        err_inst = URLError("boom")
        path_inst = Path("/render/path")
        remit("  ✗ {name}: {err} at {path}", name="test_render", err=err_inst, path=path_inst)

        res = client.get("/api/render_status", headers={"Host": "127.0.0.1:5001"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.get_data(as_text=True)}"
        data = res.get_json()
        assert isinstance(data, dict), "Response must be a JSON object"
        assert len(data.get("log", [])) > 0, "Log must not be empty"
        last = data["log"][-1]
        assert isinstance(last, dict), f"Last log entry must be a dict: {last}"
        assert last.get("t") == "  ✗ {name}: {err} at {path}"
        assert last.get("v", {}).get("err") == str(err_inst)
        assert last.get("v", {}).get("path") == str(path_inst)
    finally:
        with RLOCK:
            RJOB["log"] = old_log


def test_preview_proxy_status_json_safe(monkeypatch):
    """api.previewproxy._emit с err=URLError и path=Path -> GET /api/preview_proxy_status отдает 200 JSON."""
    import webui
    from api import previewproxy
    from core import draftrender

    err_inst = URLError("boom")
    path_inst = Path("/proxy/path")

    def fake_build_preview_proxy(src, dst, height=720, emit=None):
        if emit:
            emit("  ✗ {name}: {err} at {path}", name="test_proxy", err=err_inst, path=path_inst)

    monkeypatch.setattr(draftrender, "build_preview_proxy", fake_build_preview_proxy)

    client = webui.app.test_client()
    with previewproxy.PXLOCK:
        old_log = list(previewproxy.PXJOB["log"])
    try:
        previewproxy._run_preview_proxy([("cam1.mp4", "cam1_pv.mp4", False)], 720)

        res = client.get("/api/preview_proxy_status", headers={"Host": "127.0.0.1:5001"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.get_data(as_text=True)}"
        data = res.get_json()
        assert isinstance(data, dict), "Response must be a JSON object"
        assert len(data.get("log", [])) > 0, "Log must not be empty"
        last = data["log"][-1]
        assert isinstance(last, dict), f"Last log entry must be a dict: {last}"
        assert last.get("t") == "  ✗ {name}: {err} at {path}"
        assert last.get("v", {}).get("err") == str(err_inst)
        assert last.get("v", {}).get("path") == str(path_inst)
    finally:
        with previewproxy.PXLOCK:
            previewproxy.PXJOB["log"] = old_log


def test_log_entry_single_gate():
    """Сторож «одна дверь»: в api/_core.py, api/render.py, api/previewproxy.py
    нет литерала записи {"t": в обход общей функции (кроме ее собственного тела)."""
    pattern = re.compile(r'\{\s*["\']t["\']\s*:')

    for fname in ("api/render.py", "api/previewproxy.py"):
        p = os.path.join(ROOT, fname)
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        matches = pattern.findall(content)
        assert len(matches) == 0, (
            f"{fname} содержит литерал записи лога {{'t': ({len(matches)} раз(а)) вместо использования log_entry"
        )

    core_path = os.path.join(ROOT, "api", "_core.py")
    with open(core_path, "r", encoding="utf-8") as f:
        core_content = f.read()
    core_matches = pattern.findall(core_content)
    assert len(core_matches) == 1, (
        f"api/_core.py должен содержать {{'t': ровно 1 раз (внутри log_entry), найдено: {len(core_matches)}"
    )
    log_entry_match = re.search(r'def log_entry\([^)]*\):.*?(?=\ndef |\Z)', core_content, re.DOTALL)
    assert log_entry_match is not None, "В api/_core.py не найдена функция log_entry"
    assert pattern.search(log_entry_match.group(0)) is not None, (
        "Литерал {'t': в api/_core.py должен находиться внутри log_entry"
    )


def test_log_entry_unit():
    """Проверка работы log_entry: чистая строка, строка с переменными, не-JSON объекты."""
    from api._core import log_entry

    assert log_entry("simple string") == "simple string"
    assert log_entry("empty vars", {}) == "empty vars"
    assert log_entry("empty vars", None) == "empty vars"

    entry1 = log_entry("with int", {"count": 42})
    assert entry1 == {"t": "with int", "v": {"count": 42}}

    entry2 = log_entry("with exc", {"err": URLError("boom"), "p": Path("/a/b")})
    assert entry2["t"] == "with exc"
    assert entry2["v"]["err"] == str(URLError("boom"))
    assert entry2["v"]["p"] == str(Path("/a/b"))

    entry3 = log_entry("with kwargs", err=ValueError("bad"))
    assert entry3["t"] == "with kwargs"
    assert entry3["v"]["err"] == str(ValueError("bad"))
