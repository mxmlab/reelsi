# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Тесты прогона публичного среза через весь CI (tools/slice_check.py).

Настоящие проверки внутри тестов НЕ запускаются: запуск внешних команд подменён
заглушкой (`fake_commands`), поиск программ — `fake_tools`. Проверяется то, ради
чего скрипт и переписан: дерево среза совпадает с деревом коммита, список шагов
повторяет CI, падение любого шага красит код возврата и печатает `FAIL` с именем
шага, отсутствие бинарника gitleaks — провал с причиной, а `--no-gitleaks` —
громкий пропуск при нулевом коде. Фикстура-репозиторий — та же, что в
tests/test_public_slice.py.
"""
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile

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
    """Ответ запуска команды: код возврата и вывод."""

    def __init__(self, returncode, text="", err=""):
        self.returncode = returncode
        self.stdout = text
        self.stderr = err


class _FakeCommands:
    """Заглушка `slice_check.run_command`: код возврата по подстроке команды.

    Ключ `codes` — подстрока в команде (`"pytest"`, `"ruff"`, `"mypy"`, `"node"`,
    `"compileall"`, `"pip"`, `"gitleaks"`, `"ssh"`); что не совпало — считается зелёным.
    Реальные pytest/ruff/mypy/node/pip/gitleaks/ssh в тестах не запускаются.
    """

    def __init__(self, codes=None, text="", err=""):
        self.codes = dict(codes or {})
        self.text = text
        self.err = err
        self.calls = []

    def __call__(self, args, cwd, env=None, timeout=None, input=None, **kwargs):
        joined = " ".join(str(a) for a in args)
        self.calls.append({"args": joined, "cwd": cwd, "input": input})
        code = 0
        for marker, value in self.codes.items():
            if marker in joined:
                code = value
                break
        return _Result(code, self.text, self.err)

    def calls_with(self, marker):
        """Запуски, в команде которых есть подстрока (например, имя шага)."""
        return [c for c in self.calls if marker in c["args"]]


def _tree_of(fake):
    """Каталог среза, в котором выполнялись команды."""
    assert fake.calls, "ни одна внешняя команда не запускалась"
    return fake.calls[0]["cwd"]


def _pytest_trees(fake):
    """Каталоги среза, в которых шёл pytest.

    Признак — сама команда, а не подстрока «pytest»: её же содержит путь к
    игрушечным node и gitleaks внутри каталога pytest'а.
    """
    return [c["cwd"] for c in fake.calls_with(" -m pytest ") if not c["args"].startswith("ssh")]


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

    # Шаг `jsx` без единого файла счёл бы себя не пройденным (и правильно): в
    # игрушечном репозитории должны быть и ExtendScript, и JS интерфейса.
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "inspect.jsx").write_text("var cam = 1;\n", encoding="utf-8")
    app_dir = tmp_path / "static" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.js").write_text("var app = 2;\n", encoding="utf-8")

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - name: Tests\n"
        "        env:\n"
        "          COVERAGE_FILE: ${{ runner.temp }}/.coverage\n"
        "        run: python -m pytest tests -q --cov=api --cov=core --cov=tools --cov-report=term:skip-covered --cov-fail-under=73\n",
        encoding="utf-8",
    )

    _git(["add", "."], cwd=tmp_path, check=True)
    _git(["commit", "-m", "Initial commit"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def fake_tools(tmp_path_factory, monkeypatch):
    """Подмена `slice_check.find_tool`: node и gitleaks «стоят», PATH машины не важен."""
    folder = tmp_path_factory.mktemp("fake-bin")
    paths = {}
    for name in ("node", "gitleaks"):
        path = folder / name
        path.write_text("", encoding="utf-8")
        paths[name] = str(path)

    monkeypatch.setattr(slice_check, "find_tool", lambda name: paths.get(name))
    monkeypatch.setenv(slice_check.ENV_LINUX_SSH, "ci@fakehost")
    return paths


@pytest.fixture
def fake_commands(monkeypatch):
    """Подмена запуска внешних команд: `fake_commands({"ruff": 1}, text="поломка")`."""

    def make(codes=None, text="", err=""):
        fake = _FakeCommands(codes, text, err)
        monkeypatch.setattr(slice_check, "run_command", fake)
        return fake

    return make


def test_дерево_среза_совпадает_со_срезом(repo, fake_tools, fake_commands):
    """Каталог, который готовит slice_check, — ровно дерево среза, без вырезанного."""
    sha = public_slice.build_slice(str(repo))
    assert sha is not None
    expected = _git(["ls-tree", "-r", "--name-only", sha], cwd=repo, check=True).stdout.splitlines()
    assert ".publicignore" in expected and "README.md" in expected

    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--keep"]) == 0

    tree = _tree_of(fake)
    try:
        assert os.path.isdir(tree), "каталог среза не оставлен, хотя просили --keep"
        assert _walk(tree) == sorted(expected)
        for gone in ("TASKS.md", "docs/archive/old.md", "tests/personal_words.txt"):
            assert gone not in _walk(tree), f"в срез попало вырезанное: {gone}"
    finally:
        slice_check.remove_tree(tree)


def test_код_возврата_равен_коду_pytest(repo, fake_tools, fake_commands, capsys):
    """Код возврата скрипта = код возврата pytest, и каталог за собой убран."""
    fake_ok = fake_commands(text="1 failed, 2 passed in 0.10s")
    assert slice_check.main(["--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Код возврата pytest: 0" in out
    assert "1 failed, 2 passed in 0.10s" in out, "последние строки вывода pytest не напечатаны"

    fake_commands({"pytest": 3}, text="1 failed, 2 passed in 0.10s")
    assert slice_check.main(["--root", str(repo)]) == 3
    out = capsys.readouterr().out
    assert "Код возврата pytest: 3" in out
    assert "[FAIL] pytest" in out, "упавший шаг не назван в выводе"

    assert len(_pytest_trees(fake_ok)) == 1
    for tree in _pytest_trees(fake_ok):
        assert not os.path.exists(tree), f"временный каталог среза остался: {tree}"


def test_в_репозиторий_ничего_не_пишется(repo, fake_tools, fake_commands):
    """Сборка среза не трогает рабочую копию: ни индекса, ни объектов, ни ссылок."""
    fake_commands()
    before = _state(repo)
    assert slice_check.main(["--root", str(repo)]) == 0
    assert _state(repo) == before


def test_ref_берёт_названный_коммит(repo, fake_tools, fake_commands):
    """`--ref` собирает срез указанного коммита, а не HEAD."""
    _git(["rm", "README.md"], cwd=repo, check=True)
    _git(["commit", "-m", "remove readme"], cwd=repo, check=True)

    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--ref", "HEAD~1", "--keep"]) == 0

    tree = _tree_of(fake)
    try:
        assert "README.md" in _walk(tree)
    finally:
        slice_check.remove_tree(tree)


def test_шаги_повторяют_джобы_ci():
    """Список шагов, запускалки и подсказки описывают один и тот же набор."""
    assert slice_check.STEP_NAMES == (
        "pytest", "ruff", "mypy", "jsx", "smoke", "requirements", "gitleaks", "linux")
    assert list(slice_check._step_calls()) == list(slice_check.STEP_NAMES)
    assert set(slice_check.STEP_HINTS) == set(slice_check.STEP_NAMES)


@pytest.mark.parametrize("marker,name", [
    ("ruff", "ruff"),
    ("mypy", "mypy"),
    ("node", "jsx"),
    ("compileall", "smoke"),
    ("pip", "requirements"),
    ("gitleaks", "gitleaks"),
    ("ssh", "linux"),
])
def test_падение_шага_красит_код_возврата(repo, fake_tools, fake_commands, marker, name, capsys):
    """Любой упавший шаг виден строкой FAIL с именем шага и делает код ненулевым."""
    fake_commands({marker: 1}, text="поломка шага")
    assert slice_check.main(["--root", str(repo)]) != 0
    out = capsys.readouterr().out
    assert f"[FAIL] {name}" in out, out


def test_падение_pytest_отдаёт_его_код(repo, fake_tools, fake_commands, capsys):
    """Провал тестов отдаёт их собственный код, а не единицу."""
    fake_commands({"pytest": 3})
    assert slice_check.main(["--root", str(repo)]) == 3
    assert "[FAIL] pytest" in capsys.readouterr().out


def test_нет_бинарника_gitleaks_это_провал(repo, fake_commands, monkeypatch, capsys):
    """Бинарника нет — провал с понятной причиной, а не молчаливый пропуск."""
    monkeypatch.delenv(slice_check.ENV_GITLEAKS, raising=False)
    monkeypatch.setattr(slice_check, "find_tool", lambda name: None)
    fake = fake_commands()

    assert slice_check.main(["--root", str(repo)]) != 0

    out = capsys.readouterr().out
    assert "[FAIL] gitleaks" in out, out
    assert "--gitleaks" in out and slice_check.ENV_GITLEAKS in out, "причина без пути к бинарнику"
    assert "--no-gitleaks" in out, "не сказано, как пропустить шаг осознанно"
    assert not fake.calls_with("gitleaks"), "gitleaks всё равно запускался"


def test_указанный_путь_gitleaks_проверяется(repo, fake_tools, fake_commands, capsys):
    """Явный `--gitleaks PATH` с несуществующим файлом — тот же провал с причиной."""
    fake_commands()
    missing = os.path.join(str(repo), "нет-такого-gitleaks")
    assert slice_check.main(["--root", str(repo), "--gitleaks", missing]) != 0
    out = capsys.readouterr().out
    assert "[FAIL] gitleaks" in out and "файл не найден" in out, out


def test_явный_пропуск_gitleaks_не_красит_код(repo, fake_tools, fake_commands, capsys):
    """`--no-gitleaks`: код 0 при остальных зелёных и громкая строка о пропуске."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--no-gitleaks"]) == 0
    out = capsys.readouterr().out
    assert "gitleaks НЕ ПРОГОНЯЛСЯ" in out, out
    assert "[ПРОПУЩЕН] gitleaks" in out
    assert not fake.calls_with("gitleaks"), "шаг gitleaks всё же запускался"


def test_нет_хоста_linux_это_провал(repo, fake_tools, fake_commands, monkeypatch, capsys):
    """Хост не задан — провал с понятной причиной, а не молчаливый пропуск."""
    monkeypatch.delenv(slice_check.ENV_LINUX_SSH, raising=False)
    fake = fake_commands()

    assert slice_check.main(["--root", str(repo)]) != 0

    out = capsys.readouterr().out
    assert "[FAIL] linux" in out, out
    assert "--linux-ssh" in out and slice_check.ENV_LINUX_SSH in out, "причина без флага или переменной хоста"
    assert "--no-linux" in out, "не сказано, как пропустить шаг осознанно"
    assert not fake.calls_with("ssh"), "ssh всё равно запускался"


def test_явный_пропуск_linux_не_красит_код(repo, fake_tools, fake_commands, monkeypatch, capsys):
    """`--no-linux`: код 0 при остальных зелёных и громкая строка о пропуске."""
    monkeypatch.delenv(slice_check.ENV_LINUX_SSH, raising=False)
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--no-linux"]) == 0
    out = capsys.readouterr().out
    assert "linux НЕ ПРОГОНЯЛСЯ" in out, out
    assert "[ПРОПУЩЕН] linux" in out
    assert not fake.calls_with("ssh"), "шаг linux всё же запускался"


def test_команда_ssh_содержит_init_batchmode_и_образ(repo, fake_tools, fake_commands):
    """Команда ssh содержит --init, BatchMode=yes и образ по умолчанию reelsi-ci:py310."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host"]) == 0
    ssh_calls = fake.calls_with("ssh")
    assert len(ssh_calls) == 1, "ssh должен вызываться ровно один раз"
    args = ssh_calls[0]["args"]
    assert "--init" in args, "команда docker не содержит обязательный флаг --init"
    assert "BatchMode=yes" in args, "команда ssh не содержит BatchMode=yes"
    assert "reelsi-ci:py310" in args, "команда docker не содержит образ по умолчанию"
    assert "user@host" in args


def test_команда_docker_шага_linux_содержит_аргументы_из_ci_и_coverage_file(repo, fake_tools, fake_commands):
    """Команда docker шага linux содержит аргументы pytest из ci.yml и COVERAGE_FILE вне /src."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host"]) == 0
    ssh_calls = fake.calls_with("ssh")
    assert len(ssh_calls) == 1
    args = ssh_calls[0]["args"]

    # Аргументы pytest из ci.yml
    assert "--cov=api" in args
    assert "--cov=core" in args
    assert "--cov=tools" in args
    assert "--cov-report=term:skip-covered" in args
    assert "--cov-fail-under=73" in args

    # COVERAGE_FILE вне /src
    m = re.search(r"-e\s+COVERAGE_FILE=([^\s;]+)", args)
    assert m, "команда docker не содержит -e COVERAGE_FILE"
    cov_path = m.group(1)
    assert not cov_path.startswith("/src"), f"COVERAGE_FILE ({cov_path}) находится внутри /src"
    assert cov_path == "/tmp/.coverage"


def test_linux_шаг_падает_если_в_ci_нет_строки_pytest(repo, fake_tools, fake_commands, capsys):
    """Если в ci.yml среза нет строки pytest для джобы test, шаг linux падает с FAIL."""
    ci_file = repo / ".github" / "workflows" / "ci.yml"
    ci_file.write_text("jobs:\n  test:\n    steps:\n      - name: Other\n        run: echo hi\n", encoding="utf-8")
    _git(["add", str(ci_file)], cwd=repo, check=True)
    _git(["commit", "-m", "ci without pytest"], cwd=repo, check=True)

    fake_commands()
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host"]) != 0
    out = capsys.readouterr().out
    assert "[FAIL] linux" in out
    assert "не найдена строка" in out


def test_ненулевой_код_удалённой_стороны_дает_fail(repo, fake_tools, fake_commands, capsys):
    """Ненулевой код удалённой стороны даёт FAIL и делает код возврата ненулевым."""
    fake_commands({"ssh": 255}, err="ssh: connect to host failed\n")
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host"]) != 0
    out = capsys.readouterr().out
    assert "[FAIL] linux" in out, out
    assert "ssh: connect to host failed" in out, "stderr удалённой команды должен быть в выводе"


def test_tar_содержит_файлы_среза(repo, fake_tools, fake_commands):
    """Tar, передаваемый в stdin ssh, содержит файлы публичного среза без вырезанного."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host"]) == 0
    ssh_calls = fake.calls_with("ssh")
    assert len(ssh_calls) == 1
    tar_bytes = ssh_calls[0].get("input")
    assert isinstance(tar_bytes, bytes) and tar_bytes, "в stdin ssh не передан tar-поток"
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
        names = sorted(tf.getnames())
    assert "README.md" in names
    assert "core/app_meta.py" in names
    assert ".publicignore" in names
    for gone in ("TASKS.md", "docs/archive/old.md", "tests/personal_words.txt"):
        assert gone not in names, f"в tar попал игнорируемый файл: {gone}"
    assert not any(n == ".git" or n.startswith(".git/") for n in names), "в tar попал служебный .git"


def test_linux_image_позволяет_задать_свой_образ(repo, fake_tools, fake_commands):
    """`--linux-image` подставляет переданное имя образа в команду docker."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--linux-ssh", "user@host",
                             "--linux-image", "my-custom-image:v2"]) == 0
    ssh_calls = fake.calls_with("ssh")
    assert len(ssh_calls) == 1
    assert "my-custom-image:v2" in ssh_calls[0]["args"]


def test_only_гоняет_один_шаг(repo, fake_tools, fake_commands):
    """`--only pytest` — при разборе идёт один шаг, остальные на код не влияют."""
    fake = fake_commands({"ruff": 1, "mypy": 1, "gitleaks": 1, "ssh": 1})
    assert slice_check.main(["--root", str(repo), "--only", "pytest"]) == 0
    assert fake.calls_with("pytest"), "pytest не запускался"
    for other in ("ruff", "mypy", "node", "compileall", "pip", "gitleaks", "ssh"):
        assert not fake.calls_with(other), f"при --only pytest запускался шаг {other}"


def test_явный_пропуск_шага_не_красит_код(repo, fake_tools, fake_commands, capsys):
    """`--skip ШАГ`: шаг не идёт, в таблице виден пропущенным."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--skip", "ruff"]) == 0
    out = capsys.readouterr().out
    assert "[ПРОПУЩЕН] ruff" in out, out
    assert not fake.calls_with("ruff"), "пропущенный шаг всё же запускался"


def test_неизвестный_шаг_отвергается(repo, fake_commands, capsys):
    """Опечатка в `--only` видна в stderr, и ничего не запускается."""
    fake = fake_commands()
    assert slice_check.main(["--root", str(repo), "--only", "птест"]) == 2
    assert not fake.calls, "команды запускались, хотя шаг назван неверно"
    assert "Неизвестный шаг" in capsys.readouterr().err


def test_remove_tree_удаляет_каталог_с_подкаталогом_и_readonly(tmp_path):
    """Игрушечный каталог с подкаталогом и файлом «только для чтения» удаляется целиком."""
    toy = tmp_path / "toy"
    sub = toy / "sub"
    sub.mkdir(parents=True)
    f = sub / "ro.txt"
    f.write_text("read-only content", encoding="utf-8")
    os.chmod(str(f), stat.S_IREAD)

    assert os.path.exists(str(f))
    slice_check.remove_tree(str(toy))
    assert not os.path.exists(str(toy)), "каталог среза остался после remove_tree"


def test_remove_tree_не_снимает_чтение_и_вход_у_каталогов(tmp_path, monkeypatch):
    """remove_tree не лишает подкаталог прав чтения и входа перед вызовом shutil.rmtree."""
    toy = tmp_path / "toy"
    sub = toy / "sub"
    sub.mkdir(parents=True)
    f = sub / "ro.txt"
    f.write_text("hello", encoding="utf-8")
    os.chmod(str(f), stat.S_IREAD)

    chmod_calls: dict[str, int] = {}
    real_chmod = slice_check.os.chmod

    def fake_chmod(p, mode):
        chmod_calls[os.path.normpath(str(p))] = mode
        real_chmod(p, mode)

    rmtree_called = []

    def fake_rmtree(tree, ignore_errors=False):
        rmtree_called.append(tree)
        # Проверяем режимы подкаталогов ДО удаления
        sub_norm = os.path.normpath(str(sub))
        assert sub_norm in chmod_calls, f"chmod не вызывался для {sub_norm}"
        mode = chmod_calls[sub_norm]
        assert mode & stat.S_IREAD, f"у каталога снят бит чтения: {oct(mode)}"
        assert mode & stat.S_IEXEC, f"у каталога снят бит входа: {oct(mode)}"

    monkeypatch.setattr(slice_check.os, "chmod", fake_chmod)
    monkeypatch.setattr(slice_check.shutil, "rmtree", fake_rmtree)

    slice_check.remove_tree(str(toy))
    assert len(rmtree_called) == 1

