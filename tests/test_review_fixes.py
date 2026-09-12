# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторожа для задания HF (внешнее ревью).

Проверяют:
1. umsg_err возвращает JSON-сериализуемые err_vars даже при URLError/Exception/Path.
2. /api/ai_test отдаёт валидный JSON (а не 500 HTML) при сетевой ошибке с URLError.
3. core.omni_cut и core.gigaam_cut имеют флаг --no-dedupe ровно 1 раз и не содержат --no-no-dedupe.
4. Опубликованные файлы не содержат ссылок на непубликуемые файлы из .publicignore.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError

import pytest

from api._core import umsg_err
from core.umsg import umsg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402


def test_umsg_err_json_safe():
    """umsg_err с URLError, Exception, Path в vars -> json.dumps проходит, значение = str()."""
    err_inst = URLError("connection refused")
    exc_inst = ValueError("some error")
    path_inst = Path("/tmp/file.txt")
    u = umsg("test_code", "test_msg", err=err_inst, exc=exc_inst, p=path_inst)
    res = umsg_err(SystemExit(u))
    dumped = json.dumps(res)
    loaded = json.loads(dumped)
    assert loaded["err"] == "test_code"
    assert loaded["err_vars"]["err"] == str(err_inst)
    assert loaded["err_vars"]["exc"] == str(exc_inst)
    assert loaded["err_vars"]["p"] == str(path_inst)


def test_api_ai_test_handles_url_error(monkeypatch):
    """/api/ai_test через тестовый клиент Flask отдаёт JSON (не 500) при URLError."""
    import webui
    from core import aicut

    def fake_ask_openai(*args, **kwargs):
        raise SystemExit(umsg("no_response", "провайдер не ответил", err=URLError("refused")))

    monkeypatch.setattr(aicut, "_ask_openai", fake_ask_openai)
    client = webui.app.test_client()
    res = client.post("/api/ai_test",
                      headers={"Host": "127.0.0.1:5001"},
                      json={"profile": {"provider": "openai", "model": "gpt-4o", "api_key": "test"}})
    assert res.status_code == 200
    data = res.get_json()
    assert data is not None, f"Ответ не JSON (статус {res.status_code}): {res.data[:200]}"
    assert data.get("err") == "no_response"
    assert data.get("err_vars", {}).get("err") == "refused" or isinstance(data.get("err_vars", {}).get("err"), str)


@pytest.mark.parametrize("mod", ["core.omni_cut", "core.gigaam_cut"])
def test_cli_help_dedupe_once(mod):
    """--help у core.omni_cut и core.gigaam_cut: код 0, --no-dedupe 1 раз, --no-no-dedupe нет."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-m", mod, "--help"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0 and "ImportError" in r.stderr:
        pytest.skip(f"модуль {mod} не импортируется в окружении: {r.stderr}")
    assert r.returncode == 0, f"{mod} --help завершился с ошибкой:\n{r.stderr}"
    assert "--no-no-dedupe" not in r.stdout
    opts = r.stdout.split("options:")[1] if "options:" in r.stdout else r.stdout.split("optional arguments:")[1]
    count = opts.count("--no-dedupe")
    assert count == 1, f"Ожидался --no-dedupe ровно 1 раз в options, получено {count}:\n{opts}"


def test_public_files_have_no_dangling_links():
    """Опубликованные файлы (вне .publicignore и исключений) не содержат имён непубликуемых путей."""
    path = os.path.join(ROOT, public_slice.IGNORE_FILE)
    if not os.path.exists(path):
        pytest.skip("нет .publicignore")
    with open(path, encoding="utf-8") as f:
        patterns = public_slice.parse_ignore(f.read())

    forbidden = set()
    for pat in patterns:
        if pat.endswith("/**"):
            forbidden.add(pat[:-3])
        else:
            forbidden.add(pat)
            if "/" in pat:
                forbidden.add(os.path.basename(pat))

    EXCLUDED_FILES = {
        ".publicignore", "tools/public_slice.py", "tests/test_public_clean.py",
        "tests/test_public_slice.py", "tests/test_layout.py", "tests/test_docs_links.py",
        "tests/test_docs_freshness.py", "tools/wt.ps1", "tests/test_review_fixes.py",
    }

    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    public_files = [
        f for f in out.splitlines()
        if f and os.path.exists(os.path.join(ROOT, f)) and not public_slice.is_ignored(f, patterns)
    ]

    bad = []
    for rel in public_files:
        rel_norm = rel.replace("\\", "/")
        if rel_norm in EXCLUDED_FILES or os.path.basename(rel_norm) in EXCLUDED_FILES:
            continue
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    for target in forbidden:
                        if target in line:
                            bad.append(f"{rel_norm}:{line_no}: найдена отсылка к {target}: {line.strip()}")
        except OSError:
            pass
    assert not bad, "В публикуемых файлах найдены отсылки к непубликуемым путям:\n" + "\n".join(bad)
