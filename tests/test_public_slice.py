# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты сборки публичного среза репозитория (tools/public_slice.py)."""
import hashlib
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_slice  # noqa: E402


def _git(args, cwd, **kwargs):
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    kwargs.setdefault("capture_output", True)
    return subprocess.run(["git", *args], cwd=cwd, **kwargs)


def _count_objects(repo_path):
    res = _git(["count-objects", "-v"], cwd=repo_path, check=True)
    counts = {}
    for line in res.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            counts[k.strip()] = int(v.strip())
    return counts.get("count", 0), counts.get("in-pack", 0)


@pytest.fixture
def repo(tmp_path):
    """Синтетический git-репозиторий с игнорируемыми и публичными файлами."""
    _git(["init"], cwd=tmp_path, check=True)
    _git(["config", "user.name", "Test User"], cwd=tmp_path, check=True)
    _git(["config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    _git(["config", "core.autocrlf", "false"], cwd=tmp_path, check=True)

    (tmp_path / "README.md").write_text("# Test Repo\nHello world\n", encoding="utf-8")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "app_meta.py").write_text('APP_VERSION = "9.9.9"\n', encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\x03\x04")
    (tmp_path / ".publicignore").write_text(
        "TASKS.md\ndocs/archive/**\ntests/personal_words.txt\n", encoding="utf-8"
    )
    (tmp_path / "TASKS.md").write_text("План задач, куратор ивановтест.\n", encoding="utf-8")

    docs_arch = tmp_path / "docs" / "archive"
    docs_arch.mkdir(parents=True)
    (docs_arch / "old.md").write_text("Архивный документ, автор ивановтест.\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "personal_words.txt").write_text(
        "# Список запретных слов\nивановтест\n", encoding="utf-8"
    )

    _git(["add", "."], cwd=tmp_path, check=True)
    _git(["commit", "-m", "Initial commit"], cwd=tmp_path, check=True)
    return tmp_path


def test_срез_один_коммит_без_игнорируемого(repo):
    sha = public_slice.build_slice(str(repo))
    assert sha is not None

    count = _git(["rev-list", "--count", sha], cwd=repo, check=True).stdout.strip()
    assert count == "1"

    tree_names = _git(["ls-tree", "-r", "--name-only", sha], cwd=repo, check=True).stdout.splitlines()
    assert set(tree_names) == {".publicignore", "README.md", "core/app_meta.py", "bin.dat"}

    msg = _git(["log", "-1", "--format=%B", sha], cwd=repo, check=True).stdout.strip()
    assert msg == "Reelsi 9.9.9"

    blob_head = _git(["rev-parse", "HEAD:README.md"], cwd=repo, check=True).stdout.strip()
    blob_slice = _git(["rev-parse", f"{sha}:README.md"], cwd=repo, check=True).stdout.strip()
    assert blob_head == blob_slice


def test_слово_в_публичном_файле_роняет_срез(repo, capsys):
    with open(repo / "README.md", "a", encoding="utf-8") as f:
        f.write("Слово ивановтест в публичном файле\n")
    _git(["commit", "-am", "leak"], cwd=repo, check=True)

    counts_before = _count_objects(repo)
    code = public_slice.main(["--root", str(repo)])
    out = capsys.readouterr().out
    assert code == 1
    assert "README.md:" in out
    counts_after = _count_objects(repo)
    assert counts_before == counts_after


def test_нет_списка_слов_срез_не_собирается(repo, capsys):
    _git(["rm", "tests/personal_words.txt"], cwd=repo, check=True)
    _git(["commit", "-m", "remove words file"], cwd=repo, check=True)

    counts_before = _count_objects(repo)
    code = public_slice.main(["--root", str(repo)])
    err = capsys.readouterr().err
    assert code == 2
    assert "списка личных слов" in err
    counts_after = _count_objects(repo)
    assert counts_before == counts_after


def test_игнорируемый_файл_со_словом_не_мешает(repo):
    sha = public_slice.build_slice(str(repo))
    assert sha is not None


def test_отслеживаемое_игнорируемое_роняет_срез(repo, capsys):
    (repo / ".gitignore").write_text("secret.json\n", encoding="utf-8")
    (repo / "secret.json").write_text('{"api_key": "123"}\n', encoding="utf-8")
    _git(["add", "-f", "secret.json", ".gitignore"], cwd=repo, check=True)
    _git(["commit", "-m", "commit ignored secret"], cwd=repo, check=True)

    code = public_slice.main(["--root", str(repo)])
    out = capsys.readouterr().out
    assert code == 1
    assert "secret.json" in out


def test_родитель(repo):
    sha1 = public_slice.build_slice(str(repo))
    assert sha1 is not None

    with open(repo / "README.md", "a", encoding="utf-8") as f:
        f.write("Вторая строка\n")
    _git(["commit", "-am", "second commit"], cwd=repo, check=True)

    sha2 = public_slice.build_slice(str(repo), parent=sha1)
    assert sha2 is not None

    count = _git(["rev-list", "--count", sha2], cwd=repo, check=True).stdout.strip()
    assert count == "2"

    parent_of_sha2 = _git(["rev-parse", f"{sha2}^"], cwd=repo, check=True).stdout.strip()
    assert parent_of_sha2 == sha1


def test_нечего_публиковать(repo):
    sha1 = public_slice.build_slice(str(repo))
    assert sha1 is not None

    counts_before = _count_objects(repo)
    res = public_slice.build_slice(str(repo), parent=sha1)
    assert res is None

    code = public_slice.main(["--root", str(repo), "--parent", sha1])
    assert code == 0

    counts_after = _count_objects(repo)
    assert counts_before == counts_after


def test_рабочая_копия_не_тронута(repo):
    def _state(r):
        rev = _git(["rev-parse", "HEAD"], cwd=r, check=True).stdout
        status = _git(["status", "--porcelain"], cwd=r, check=True).stdout
        refs = _git(["for-each-ref"], cwd=r, check=True).stdout
        idx_hash = hashlib.sha256((r / ".git" / "index").read_bytes()).hexdigest()
        return rev, status, refs, idx_hash

    before = _state(repo)
    public_slice.build_slice(str(repo))
    after = _state(repo)
    assert before == after


def test_check_не_пишет_объекты(repo):
    counts_before = _count_objects(repo)
    code = public_slice.main(["--check", "--root", str(repo)])
    assert code == 0
    counts_after = _count_objects(repo)
    assert counts_before == counts_after


def test_oversized():
    res = public_slice.oversized([("a", public_slice.SIZE_LIMIT + 1), ("b", 10)])
    assert res == ["a"]


def test_список_фамилий_не_публикуется():
    with open(os.path.join(ROOT, public_slice.IGNORE_FILE), encoding="utf-8") as f:
        patterns = public_slice.parse_ignore(f.read())
    assert public_slice.is_ignored(public_slice.WORDS_FILE, patterns) is True

