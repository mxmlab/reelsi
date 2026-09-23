# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты прогона набора на публичном срезе (tools/slice_check.py).

Настоящий набор внутри теста НЕ гоняется: запуск pytest подменяется заглушкой
(`monkeypatch`), а проверяются две вещи из задания — дерево, которое готовит скрипт,
совпадает с деревом среза, и код возврата скрипта равен коду возврата pytest
(обе ветки: 0 и не 0). Фикстура-репозиторий — та же, что в tests/test_public_slice.py.
"""
import hashlib
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402
import slice_check  # noqa: E402


def _git(args, cwd, **kwargs):
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    kwargs.setdefault("capture_output", True)
    return subprocess.run(["git", *args], cwd=cwd, **kwargs)


def _walk(tree):
    """Относительные пути файлов каталога среза; служебный `.git` не считается."""
    found = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), tree)
            found.append(rel.replace("\\", "/"))
    return sorted(found)


def _state(repo):
    """Состояние рабочей копии: HEAD, статус, ссылки, объекты и индекс."""
    return {
        "head": _git(["rev-parse", "HEAD"], cwd=repo, check=True).stdout,
        "status": _git(["status", "--porcelain"], cwd=repo, check=True).stdout,
        "refs": _git(["for-each-ref"], cwd=repo, check=True).stdout,
        "objects": _git(["count-objects", "-v"], cwd=repo, check=True).stdout,
        "index": hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest(),
    }


class _Result:
    """Ответ запуска pytest: код возврата и вывод."""

    def __init__(self, returncode, text=""):
        self.returncode = returncode
        self.stdout = text
        self.stderr = ""


class _FakePytest:
    """Заглушка `slice_check.run_pytest`: помнит каталоги среза, отдаёт заданные коды."""

    def __init__(self, codes, text=""):
        self.codes = list(codes)
        self.text = text
        self.trees = []

    def __call__(self, tree):
        self.trees.append(tree)
        code = self.codes.pop(0) if self.codes else 0
        return _Result(code, self.text)


@pytest.fixture
def repo(tmp_path):
    """Синтетический git-репозиторий с публичными и игнорируемыми файлами."""
    _git(["init"], cwd=tmp_path, check=True)
    _git(["config", "user.name", "Test User"], cwd=tmp_path, check=True)
    _git(["config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    _git(["config", "core.autocrlf", "false"], cwd=tmp_path, check=True)

    (tmp_path / "README.md").write_text("# Test Repo\nHello world\n", encoding="utf-8")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "app_meta.py").write_text('APP_VERSION = "9.9.9"\n', encoding="utf-8")
    (tmp_path / ".publicignore").write_text(
        "TASKS.md\ndocs/archive/**\ntests/personal_words.txt\n", encoding="utf-8"
    )
    (tmp_path / "TASKS.md").write_text("План задач, куратор ивановтест.\n", encoding="utf-8")

    docs_arch = tmp_path / "docs" / "archive"
    docs_arch.mkdir(parents=True)
    (docs_arch / "old.md").write_text("Архивный документ, автор ивановтест.\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "personal_words.txt").write_text(
        "# Список запретных слов\nивановтест\n", encoding="utf-8"
    )

    _git(["add", "."], cwd=tmp_path, check=True)
    _git(["commit", "-m", "Initial commit"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def fake_pytest(monkeypatch):
    """Подмена запуска pytest: `fake_pytest(0, 3)` — первые два вызова с такими кодами."""

    def make(*codes, text=""):
        fake = _FakePytest(codes, text)
        monkeypatch.setattr(slice_check, "run_pytest", fake)
        return fake

    return make


def test_дерево_среза_совпадает_со_срезом(repo, fake_pytest):
    """Каталог, который готовит slice_check, — ровно дерево среза, без вырезанного."""
    sha = public_slice.build_slice(str(repo))
    assert sha is not None
    expected = _git(["ls-tree", "-r", "--name-only", sha], cwd=repo, check=True).stdout.splitlines()
    assert ".publicignore" in expected and "README.md" in expected

    fake = fake_pytest(0)
    assert slice_check.main(["--root", str(repo), "--keep"]) == 0

    tree = fake.trees[0]
    try:
        assert os.path.isdir(tree), "каталог среза не оставлен, хотя просили --keep"
        assert _walk(tree) == sorted(expected)
        for gone in ("TASKS.md", "docs/archive/old.md", "tests/personal_words.txt"):
            assert gone not in _walk(tree), f"в срез попало вырезанное: {gone}"
    finally:
        slice_check.remove_tree(tree)


def test_код_возврата_равен_коду_pytest(repo, fake_pytest, capsys):
    """Код возврата скрипта = код возврата pytest, и каталог за собой убран."""
    fake = fake_pytest(0, 3, text="1 failed, 2 passed in 0.10s")

    assert slice_check.main(["--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Код возврата pytest: 0" in out
    assert "1 failed, 2 passed in 0.10s" in out, "последние строки вывода pytest не напечатаны"

    assert slice_check.main(["--root", str(repo)]) == 3
    assert "Код возврата pytest: 3" in capsys.readouterr().out

    assert len(fake.trees) == 2
    for tree in fake.trees:
        assert not os.path.exists(tree), f"временный каталог среза остался: {tree}"


def test_в_репозиторий_ничего_не_пишется(repo, fake_pytest):
    """Сборка среза не трогает рабочую копию: ни индекса, ни объектов, ни ссылок."""
    fake_pytest(0)
    before = _state(repo)
    assert slice_check.main(["--root", str(repo)]) == 0
    assert _state(repo) == before


def test_ref_берёт_названный_коммит(repo, fake_pytest):
    """`--ref` собирает срез указанного коммита, а не HEAD."""
    _git(["rm", "README.md"], cwd=repo, check=True)
    _git(["commit", "-m", "remove readme"], cwd=repo, check=True)

    fake = fake_pytest(0)
    assert slice_check.main(["--root", str(repo), "--ref", "HEAD~1", "--keep"]) == 0

    tree = fake.trees[0]
    try:
        assert "README.md" in _walk(tree)
    finally:
        slice_check.remove_tree(tree)
