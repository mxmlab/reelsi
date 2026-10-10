# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож конфигурации CI: инструменты закреплены, тесты идут параллельно.

Четыре вещи, из-за которых сборка жила не тем, чем казалась:

1. **Список инструментов жил в двух местах** — строкой `pip install` в `ci.yml`
   и в `tools/docker/ci.Dockerfile`. Теперь он один: `requirements-dev.txt`.
   Тест следит, чтобы джобы ставили инструменты из него, а не перечисляли
   пакеты заново.
2. **Без верхних границ версий** зелёное держалось на pip-кэше раннера: новая
   мажорная версия pytest/ruff меняет правила и красит сборку на ровном месте.
   У pytest и плагинов — диапазон, у ruff — точная версия (новый набор правил
   находит то, чего раньше не было).
3. **Набор тестов идёт ~20 минут** при таймауте джобы 25 — одним процессом это
   в притык. В CI он идёт с `-n auto` (pytest-xdist). Локально xdist может не
   стоять, поэтому тесты НЕ требуют его: проверяется конфигурация, а не запуск.
4. **Тесты бюджета времени** (`@pytest.mark.perf`) меряют скорость раннера, а
   не код: в CI джобы идут с `-m "not perf"`, маркер объявлен в `pytest.ini`.
5. **`mypy` не гоняется дважды**: в джобе, где он уже есть отдельным шагом, тест
   храповика пропускается по `REELSI_MYPY_STEP=1`, а проверка «список не сжался»
   остаётся — она читает `pyproject.toml` и от установки mypy не зависит.
   Пропуск сторожит и `test_mypy_runs_without_the_env`: сам он `mypy` не запускает —
   `subprocess.run` теста-храповика подменён фейком, который отвечает «ошибок нет»
   и записывает вызов. Иначе тест был бы ВТОРЫМ прогоном mypy (~30 с) в CI, да ещё
   и ронял воркер под `pytest -n auto` («node down: Not properly terminated»).

`ci.yml` читается текстом: разбираем ровно те строки, которые правят руками, и
не заводим тесту лишнюю зависимость от YAML-парсера.

Запуск: python -m pytest tests/test_ci_config.py -q
"""
import ast
import importlib.util
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS_DEV = ROOT / "requirements-dev.txt"
PYTEST_INI = ROOT / "pytest.ini"
DOCKERFILE = ROOT / "tools" / "docker" / "ci.Dockerfile"

# Джобы, в которых идёт pytest: обе ставят инструменты из requirements-dev.txt.
PYTEST_JOBS = ("test", "test-windows")

# У pytest и его плагинов — диапазон `>=низ,<верх`; ruff — точная версия `==`.
BOUNDED_REQUIREMENTS = ("pytest", "pytest-timeout", "pytest-cov", "pytest-xdist", "pyyaml")
EXACT_REQUIREMENTS = ("ruff",)

# Нижние версии, ниже которых в CI ничего ставиться не должно: это рабочие версии
# владельца, на них набор проверен.
FLOOR_VERSIONS = {
    "pytest": (9, 1),
    "pytest-timeout": (2, 4),
    "pytest-cov": (7, 1),
    "pytest-xdist": (3, 6),
    "pyyaml": (6, 0),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _job_block(job: str) -> list[str]:
    """Строки джобы `job` из ci.yml: от её заголовка до заголовка следующей джобы."""
    lines = _read(CI_YML).splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "  " + job + ":":
            start = i + 1
            break
    assert start is not None, f"в .github/workflows/ci.yml нет джобы {job}"
    block = []
    for line in lines[start:]:
        if line.strip() and not line.startswith("    "):
            break  # заголовок следующей джобы — отступ в два пробела
        block.append(line)
    return block


def _pytest_step_block(job: str) -> list[str]:
    """Строки шага, в котором джоба запускает pytest, вместе с его `env:`."""
    block = _job_block(job)
    start = None
    for i, line in enumerate(block):
        if re.match(r"^ {6}-\s*name:", line) or re.match(r"^ {6}- uses:", line):
            start = i
            continue
        if start is None:
            continue
        if re.match(r"^ {8}run:\s*.*python -m pytest tests", line):
            step = block[start:i + 1]
            # env объявлен выше run — забираем и его
            env_at = next((j for j, l in enumerate(step) if re.match(r"^ {8}env:\s*$", l)), None)
            if env_at is not None:
                step = step[env_at:]
            return step
    raise AssertionError(f"в джобе {job} не найден шаг 'run: python -m pytest tests ...'")


def _pytest_run_line(job: str) -> str:
    """Строка запуска pytest в джобе — то, что реально уходит в оболочку."""
    for line in _pytest_step_block(job):
        if re.match(r"^ {8}run:\s*.*python -m pytest tests", line):
            return line
    raise AssertionError(f"в джобе {job} нет строки запуска pytest")


def _requirements() -> dict[str, str]:
    """{имя пакета: спецификатор версии} из requirements-dev.txt."""
    found: dict[str, str] = {}
    for line in _read(REQUIREMENTS_DEV).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()  # инлайн-комментарий не часть спецификатора
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(\S*)$", line)
        if m:
            found[m.group(1).lower()] = m.group(2)
    return found


def _version_tuple(spec: str) -> tuple[int, ...]:
    """Версия из спецификатора кортежем: `>=6,<7` (первая часть) -> (6,)."""
    return tuple(int(x) for x in re.findall(r"\d+", spec))


def _at_least(version: tuple[int, ...], floor: tuple[int, ...]) -> bool:
    """`6` не ниже `6.0`: кортежи разной длины сравниваются по общим частям."""
    size = max(len(version), len(floor))
    return version + (0,) * (size - len(version)) >= floor + (0,) * (size - len(floor))


# --------------------------------------------------------------------------- #
# 1. Инструменты ставятся из requirements-dev.txt, а не перечнем в строке
# --------------------------------------------------------------------------- #


def test_ci_jobs_install_tools_from_requirements_dev():
    """Джобы с тестами ставят инструменты из requirements-dev.txt, а не перечнем."""
    assert REQUIREMENTS_DEV.is_file(), "нет requirements-dev.txt — инструменты ставить неоткуда"
    for job in PYTEST_JOBS:
        block = "\n".join(_job_block(job))
        assert "pip install -r requirements-dev.txt" in block, (
            f"джоба {job} не ставит инструменты из requirements-dev.txt: список пакетов "
            "живёт в двух местах и разъезжается"
        )
        install_lines = [line for line in block.splitlines() if "pip install" in line]
        for line in install_lines:
            if "requirements-dev.txt" in line or "install torch" in line:
                continue
            for tool in ("pytest", "ruff", "mypy", "pytest-cov", "pytest-timeout"):
                assert tool not in line, (
                    f"в джобе {job} инструмент {tool} ставится отдельной строкой, а не из "
                    f"requirements-dev.txt: {line.strip()}"
                )


def test_dockerfile_installs_tools_from_requirements_dev():
    """Образ CI берёт те же инструменты из requirements-dev.txt (одна версия на всех)."""
    assert DOCKERFILE.is_file(), "нет tools/docker/ci.Dockerfile"
    text = _read(DOCKERFILE)
    assert "-r /tmp/requirements-dev.txt" in text, (
        "образ CI не ставит инструменты из requirements-dev.txt: версии в образе и в "
        "CI разъедутся"
    )
    assert "COPY requirements-dev.txt" in text, (
        "requirements-dev.txt не копируется в образ: COPY иначе не найдёт файл "
        "(контекст сборки — корень репозитория)"
    )
    for line in text.splitlines():
        if line.startswith("RUN pip install") and "requirements-dev.txt" not in line \
                and "torch" not in line:
            for tool in ("pytest", "ruff", "mypy"):
                assert tool not in line, (
                    f"в образе инструмент {tool} ставится отдельной строкой: {line.strip()}"
                )


# --------------------------------------------------------------------------- #
# 2. Параллельно и без тестов бюджета времени
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("job", PYTEST_JOBS)
def test_pytest_runs_parallel(job):
    """В каждой джобе pytest идёт с `-n auto`: набор не влезает в таймаут джобы."""
    run = _pytest_run_line(job)
    assert re.search(r"(?:^|\s)-n\s+auto(?:\s|$)", run), (
        f"в джобе {job} pytest запускается без `-n auto`: набор идёт одним процессом "
        f"~20 мин при таймауте джобы. Строка: {run.strip()}"
    )


@pytest.mark.parametrize("job", PYTEST_JOBS)
def test_pytest_skips_perf_marker(job):
    """В каждой джобе pytest идёт с `-m "not perf"`: бюджет времени мерит раннер."""
    run = _pytest_run_line(job)
    assert re.search(r'-m\s+"not perf"', run), (
        f"в джобе {job} нет `-m \"not perf\"`: тесты бюджета времени падают на медленном "
        f"раннере. Строка: {run.strip()}"
    )


def test_ci_sets_mypy_step_env_where_mypy_runs():
    """В джобе с шагом `mypy` у шага pytest стоит REELSI_MYPY_STEP=1 (второй прогон не нужен)."""
    block = _job_block("test")
    assert any(re.match(r"^ {8}run:\s*mypy\s*$", line) for line in block), (
        "в джобе test нет отдельного шага mypy — тогда храповику нельзя пропускать прогон"
    )
    step = "\n".join(_pytest_step_block("test"))
    assert re.search(r'REELSI_MYPY_STEP:\s*"?1"?\s*$', step, re.M), (
        "у шага pytest джобы test нет REELSI_MYPY_STEP=1: mypy гоняется дважды "
        "(отдельным шагом и тестом-храповиком)"
    )


# --------------------------------------------------------------------------- #
# 3. Версии инструментов закреплены
# --------------------------------------------------------------------------- #


def test_requirements_dev_pins_upper_bounds():
    """У pytest и плагинов есть верхняя граница, у ruff — точная версия."""
    reqs = _requirements()
    for name in BOUNDED_REQUIREMENTS:
        assert name in reqs, f"в requirements-dev.txt нет {name}"
        spec = reqs[name]
        assert "<" in spec, (
            f"{name}{spec} без верхней границы: новая мажорная версия меняет поведение, "
            "и сборка краснеет на ровном месте"
        )
        assert ">" in spec, f"{name}{spec} без нижней границы: непонятно, с чего начинается диапазон"
        floor = _version_tuple(spec.split(",", 1)[0])
        assert _at_least(floor, FLOOR_VERSIONS[name]), (
            f"{name}{spec}: нижняя граница ниже рабочей версии {FLOOR_VERSIONS[name]}"
        )

    for name in EXACT_REQUIREMENTS:
        assert name in reqs, f"в requirements-dev.txt нет {name}"
        assert reqs[name].startswith("=="), (
            f"{name}{reqs[name]} не закреплён точной версией: новые правила линтера "
            "находят то, чего раньше не было, и сборка краснеет"
        )


# --------------------------------------------------------------------------- #
# 4. Маркер perf объявлен
# --------------------------------------------------------------------------- #


def test_pytest_ini_declares_perf_marker():
    """Маркер `perf` объявлен в pytest.ini — иначе опечатка вместо маркера не видна."""
    assert PYTEST_INI.is_file(), "нет pytest.ini"
    text = _read(PYTEST_INI)
    markers_at = re.search(r"^markers\s*=\s*$", text, re.M)
    assert markers_at, "в pytest.ini нет секции markers"
    markers = text[markers_at.end():]
    assert re.search(r"^\s+perf\s*:", markers, re.M), (
        "маркер perf не объявлен в pytest.ini: тест с чужим маркером молча не исключается "
        "флагом -m"
    )


# --------------------------------------------------------------------------- #
# 5. mypy не гоняется дважды там, где он уже есть отдельным шагом
# --------------------------------------------------------------------------- #


def _ratchet():
    """Модуль теста-храповика: он и есть предмет проверки ниже."""
    path = HERE / "test_typing_ratchet.py"
    spec = importlib.util.spec_from_file_location("test_typing_ratchet_probe", path)
    assert spec is not None and spec.loader is not None, "не удалось загрузить test_typing_ratchet.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mypy_skip_env_skips_the_run_but_not_the_list(monkeypatch):
    """`REELSI_MYPY_STEP=1` пропускает прогон mypy, но не проверку «список не сжался»."""
    ratchet = _ratchet()
    monkeypatch.setenv(ratchet.ENV_MYPY_STEP, "1")

    with pytest.raises(pytest.skip.Exception) as exc:
        ratchet.test_mypy_reports_no_errors()
    assert "mypy идёт отдельным шагом CI" in str(exc.value), (
        f"причина пропуска не объясняет, почему прогон пропущен: {exc.value}"
    )

    # Список строгих модулей — часть храповика, не прогон mypy: не пропускается.
    ratchet.test_pyproject_keeps_strict_module_list()
    ratchet.test_pyproject_strict_modules_require_annotations()


def test_mypy_runs_without_the_env(monkeypatch):
    """Без переменной прогон mypy не пропускается по «отдельный шаг CI».

    Настоящий `mypy` этот тест не запускает никогда: `subprocess.run` теста-храповика
    подменён фейком — он отвечает «ошибок нет» и записывает факт вызова. Без этой
    подмены тест был бы вторым прогоном mypy (~30 с) в CI и ронял бы воркер под
    `pytest -n auto` («node down: Not properly terminated»).
    """
    ratchet = _ratchet()
    monkeypatch.delenv(ratchet.ENV_MYPY_STEP, raising=False)

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        """Вместо mypy: «ошибок нет» и запись команды — храповик зовёт `subprocess.run`."""
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Патчим `subprocess.run` в модуле храповика — ровно то, чем он зовёт mypy.
    monkeypatch.setattr(ratchet.subprocess, "run", fake_run)

    try:
        ratchet.test_mypy_reports_no_errors()
    except pytest.skip.Exception as e:
        assert "отдельным шагом CI" not in str(e), (
            "без REELSI_MYPY_STEP прогон mypy пропускается как «идёт отдельным шагом CI»"
        )
        # Не установлен mypy — фейк не звали, и запускать настоящий здесь нечего.
        assert not calls, "mypy не нашёлся, а фейк уже вызван"
        return

    assert calls, "без REELSI_MYPY_STEP прогон mypy не состоялся: фейк не вызван"
    for cmd in calls:
        assert "mypy" in cmd, f"фейк вызван не для mypy: {cmd}"


# --------------------------------------------------------------------------- #
# 7. Настоящий Chrome идёт в одном воркере: xdist_group("chrome")
# --------------------------------------------------------------------------- #


# Тесты, поднимающие настоящий безголовый Chrome. Группу узнаём по строке
# `--headless=new` в теле того, кто этот процесс и запускает: имя помощника
# (`_run_chrome`) — договорённость, которая переживёт не всякую правку, а флаг
# запуска остаётся в командной строке браузера. Помощник в модуле ищем по
# цепочке вызовов: тест может звать не запускалку, а её обёртку.
CHROME_TEST_FILES = (
    "tests/test_ui_static.py",
    "tests/test_wv_ins_modal_geometry.py",
    "tests/test_style_dirfile_geometry.py",
    "tests/test_webrender.py",
)

# Маркер группы: `xdist_group("chrome")` в теле модуля-теста и в самих тестах.
CHROME_XDIST_GROUP = "chrome"

# Чем узнаётся запуск браузера: `subprocess.run` с путём к Chrome (путь разворачиваем
# из констант модуля — у стенда браузер лежит в `_CHROME`) либо `shutil.which("chrome")`
# в теле. Строка `--headless=new` признаком не служит: её пишет и сборщик аргументов,
# и тест, проверяющий флаги стенда, — браузера ни один из них не поднимает.
CHROME_LOOKUP_NAME = "which"
SUBPROCESS_MODULE = "subprocess"
PROCESS_LAUNCH_NAMES = ("run", "call", "check_call", "check_output", "Popen")

# Строка запуска pytest в шагах и в slice_check: `-n auto` и `--dist loadgroup`
# обязаны идти парой — loadgroup без xdist не существует, а Chrome-тесты без него
# разъезжаются по воркерам.
XDIST_ARG = "-n"
XDIST_DIST_ARG = "--dist"
XDIST_DIST_VALUE = "loadgroup"


def _module_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Функции модуля: верхний уровень и методы классов (тест — любая из них)."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(node)
        elif isinstance(node, ast.ClassDef):
            found.extend(n for n in node.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return found


def _module_variables(tree: ast.Module) -> dict[str, set[str]]:
    """Строковые литералы констант модуля: путь к браузеру живёт в них (`_CHROME`).

    Значение не всегда один литерал: у стенда это `shutil.which("chrome") or
    r"C:\\...\\chrome.exe"` — запасной путь лежит внутри `or`. Ключи в нижнем
    регистре: имя константы в разных файлах пишут по-разному (`_CHROME`, `CHROME`).
    """
    found: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        literals = {n.value for n in ast.walk(node.value)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if not literals:
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id.lower()] = literals
        elif isinstance(node.target, ast.Name):
            found[node.target.id.lower()] = literals
    return found


def _called_names(node: ast.AST) -> set[str]:
    """Имена, по которым в поддереве зовут функции (`_run_chrome`, `subprocess.run`)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _is_chrome_lookup(node: ast.AST) -> bool:
    """`shutil.which("chrome")` — дверь к исполняемому файлу браузера."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == CHROME_LOOKUP_NAME
            and any(isinstance(a, ast.Constant) and a.value == "chrome"
                    for a in node.args))


def _exec_literal(node: ast.AST | None, variables: dict[str, set[str]]) -> str | None:
    """Первый элемент команды: `_CHROME`, `cmd`, `[chrome, ...]`, `"node"`.

    Берём именно первый: в `subprocess.run(cmd)` запускается `cmd[0]`, а флаги и
    аргументы, которые лежат рядом, о запуске ничего не говорят.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        # Имя константы в разных файлах пишут по-разному (`_CHROME`, `CHROME`),
        # а переменную-хвост команды (`chrome`) модулю и вовсе знать неоткуда.
        return next(iter(variables.get(node.id.lower(), set())), None)
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _exec_literal(node.elts[0], variables)
    return None


def _launched_file(node: ast.AST, variables: dict[str, set[str]]) -> str | None:
    """Файл, который запускает `subprocess.run(...)`: None — это не запуск процесса.

    Команда приходит и списком (`subprocess.run([_CHROME, ...])`), и переменной
    модуля или локальной (`subprocess.run(cmd)`): путь к браузеру лежит либо в
    константе модуля (`_CHROME`), либо в локальном списке — оба разворачиваем.
    """
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in PROCESS_LAUNCH_NAMES
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == SUBPROCESS_MODULE):
        return None
    if not node.args:
        return None
    return _exec_literal(node.args[0], variables)


def _local_variables(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                     variables: dict[str, set[str]]) -> dict[str, set[str]]:
    """Локальные переменные тела: имя -> файл, который в них лежит (`cmd = [_CHROME]`)."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or node.value is None:
            continue
        literal = _exec_literal(node.value, variables)
        if literal is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.setdefault(target.id.lower(), set()).add(literal)
    return found


def _is_chrome_path(name: str) -> bool:
    """Похоже ли имя файла на исполняемый файл Chrome."""
    if os.path.basename(name).lower().startswith("chrome"):
        return True
    return bool(re.search(r"google[\\/]+chrome[\\/]+application[\\/]+chrome\.exe$",
                          name, re.IGNORECASE))


def _chrome_indicator(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                      variables: dict[str, set[str]]) -> str:
    """Чем функция выдаёт ЛАУНЧ Chrome: '' — не запускает, иначе — повод для пометки.

    Одного флага `--headless=new` мало: его пишет и сборщик аргументов
    (`_capture_cmd` в webrender), и тест, проверяющий флаги стенда, — браузера
    ни один из них не поднимает. Запуск — это `subprocess.run` с файлом Chrome
    либо `shutil.which("chrome")` внутри тела. Локальные переменные тела (`cmd`)
    разворачиваются вместе с константами модуля.
    """
    scope = {**variables, **_local_variables(fn, variables)}
    for node in ast.walk(fn):
        launched = _launched_file(node, scope)
        if launched and _is_chrome_path(launched):
            return f"{fn.name} запускает {launched}"
    if any(_is_chrome_lookup(n) for n in ast.walk(fn)):
        return f"{fn.name} ищет исполняемый файл Chrome"
    return ""


def _spawn_helpers(functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
                   variables: dict[str, set[str]]) -> set[str]:
    """Функции модуля, которые сами запускают Chrome (а не проверяют его флаги)."""
    return {fn.name for fn in functions if _chrome_indicator(fn, variables)}


def _chrome_tests(functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
                  chrome_spawners: set[str],
                  variables: dict[str, set[str]]) -> set[tuple[str, int]]:
    """Тесты, которые доводят дело до настоящего Chrome — напрямую или через помощника."""
    callers: dict[str, set[str]] = {fn.name: _called_names(fn) for fn in functions}
    chrome_callers = set(chrome_spawners)
    grew = True
    while grew:
        grew = False
        for name, calls in callers.items():
            if name not in chrome_callers and calls & chrome_callers:
                chrome_callers.add(name)
                grew = True

    found: set[tuple[str, int]] = set()
    for fn in functions:
        if not fn.name.startswith("test_"):
            continue
        calls = callers[fn.name]
        if calls & chrome_callers or _chrome_indicator(fn, variables):
            found.add((fn.name, fn.lineno))
    return found


def _group_of(decorator: ast.AST) -> set[str]:
    """Имена групп из `@pytest.mark.xdist_group("chrome")` (групп может быть список).

    Смотрим только позиционные аргументы и `name=`: строки в других ключах —
    пояснение к маркеру, а не имя группы.
    """
    if not isinstance(decorator, ast.Call):
        return set()
    func = decorator.func
    marker = (isinstance(func, ast.Attribute) and func.attr == "xdist_group")
    marked = (isinstance(func, ast.Name) and func.id == "xdist_group")
    if not (marker or marked):
        return set()
    values: list[ast.expr] = list(decorator.args)
    values += [kw.value for kw in decorator.keywords if kw.arg == "name"]
    names: set[str] = set()
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names.add(value.value)
        elif isinstance(value, (ast.List, ast.Tuple)):
            names |= {el.value for el in value.elts
                      if isinstance(el, ast.Constant) and isinstance(el.value, str)}
    return names


def _chrome_marked(tree: ast.Module) -> set[tuple[str, int]]:
    """Тесты с маркером группы «chrome» — на самих тестах и через pytestmark модуля."""
    module_groups: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            module_groups |= {g for el in node.value.elts for g in _group_of(el)}
        else:
            module_groups |= _group_of(node.value)

    marked: set[tuple[str, int]] = set()
    for fn in _module_functions(tree):
        if not fn.name.startswith("test_"):
            continue
        own = any(CHROME_XDIST_GROUP in _group_of(d) for d in fn.decorator_list)
        if own or CHROME_XDIST_GROUP in module_groups:
            marked.add((fn.name, fn.lineno))
    return marked


def _test_tree(rel: str) -> ast.Module:
    """AST модуля-теста: имена тестов и их тела — без импорта самого модуля."""
    return ast.parse(_read(ROOT / rel))


def _chrome_tests_in(rel: str) -> set[tuple[str, int]]:
    """Тесты файла `rel`, которые доводят дело до настоящего Chrome."""
    tree = _test_tree(rel)
    functions = _module_functions(tree)
    variables = _module_variables(tree)
    return _chrome_tests(functions, _spawn_helpers(functions, variables), variables)


def test_chrome_tests_are_pinned_to_one_xdist_group():
    """Каждый тест с настоящим Chrome помечен `xdist_group("chrome")`."""
    for rel in CHROME_TEST_FILES:
        unmarked = sorted(_chrome_tests_in(rel) - _chrome_marked(_test_tree(rel)))
        assert not unmarked, (
            f"{rel}: тесты с настоящим Chrome без @pytest.mark.xdist_group"
            f"(\"{CHROME_XDIST_GROUP}\"): "
            + ", ".join(f"{name}:{line}" for name, line in unmarked)
        )


def test_chrome_group_has_the_tests_that_raise_the_browser():
    """Группа «chrome» — ровно тесты с настоящим Chrome, и она непуста.

    Сторож выше молчит и тогда, когда маркеров нет вовсе: сканер мог перестать
    находить запуск (переписали помощник) — и «нет нарушителей» стало бы враньём.
    """
    expected: set[tuple[str, int]] = set()
    marked: set[tuple[str, int]] = set()
    for rel in CHROME_TEST_FILES:
        expected |= _chrome_tests_in(rel)
        marked |= _chrome_marked(_test_tree(rel))

    assert expected, ("ни в одном из файлов с Chrome-стендами не найден запуск браузера — "
                      "сторож ослеп")
    assert marked == expected, (
        "маркер группы «chrome» стоит не на тех тестах: "
        f"помечено {sorted(marked)}, запускают Chrome {sorted(expected)}")


def test_pytest_runs_chrome_tests_in_one_worker(monkeypatch):
    """`-n auto` в CI идёт с `--dist loadgroup`: Chrome-тесты — в одном воркере."""
    for job in PYTEST_JOBS:
        run = _pytest_run_line(job)
        assert re.search(r"(?:^|\s)" + XDIST_ARG + r"\s+auto(?:\s|$)", run), (
            f"в джобе {job} нет `{XDIST_ARG} auto` — тогда `--dist "
            f"{XDIST_DIST_VALUE}` не с чем работать"
        )
        assert re.search(r"(?:^|\s)" + XDIST_DIST_ARG + r"\s+" + XDIST_DIST_VALUE
                         + r"(?:\s|$)", run), (
            f"в джобе {job} pytest идёт без `--dist {XDIST_DIST_VALUE}`: тесты с настоящим "
            "Chrome разъезжаются по воркерам и меряют геометрию под нагрузкой. "
            f"Строка: {run.strip()}"
        )

    pytest_ini = _read(PYTEST_INI)
    assert "addopts" not in pytest_ini, (
        "`--dist` переехал в addopts pytest.ini: без pytest-xdist такого аргумента нет, "
        "и pytest падает на разборе командной строки, а не пропускает флаг"
    )

    sc = _load_slice_check()
    monkeypatch.delenv(sc.ENV_PYTEST_NO_XDIST, raising=False)
    monkeypatch.setattr(sc, "has_xdist", lambda: True)
    cmd = sc.pytest_command()
    assert XDIST_ARG in cmd and "auto" in cmd, (
        "в команде slice_check нет `-n auto` — параллельного прогона не будет"
    )
    assert XDIST_DIST_ARG in cmd and XDIST_DIST_VALUE in cmd, (
        f"в команде slice_check нет `--dist {XDIST_DIST_VALUE}`: Chrome-тесты slice_check "
        "разъедутся по воркерам"
    )


# Потолок pytest.ini — 120 с, timeout_method = thread. Прогон mypy в храповике
# (две платформы) под `pytest -n auto` в него не влезает, а thread-таймаут рубит
# ВЕСЬ процесс воркера: раннер пишет «worker crashed … node down», и шаг pytest
# краснеет не из-за кода. Маркер у теста снимает это и для локального прогона.
RATCHET_TIMEOUT_MIN_S = 600


def test_mypy_ratchet_test_has_own_timeout():
    """У теста-храповика есть маркер timeout не меньше 600 с — иначе он рубит воркер."""
    # getattr, а не обращение к атрибуту: без маркера pytest не заводит
    # `pytestmark` вовсе, и это должно выглядеть провалом проверки, а не
    # AttributeError посреди теста.
    marks = getattr(_ratchet().test_mypy_reports_no_errors, "pytestmark", [])
    timeouts = [m.args[0] for m in marks if m.name == "timeout"]
    assert timeouts, (
        "у test_mypy_reports_no_errors нет @pytest.mark.timeout: два прогона mypy "
        "дольше потолка pytest.ini, и thread-таймаут убьёт воркер под `-n auto`"
    )
    assert max(timeouts) >= RATCHET_TIMEOUT_MIN_S, (
        f"маркер timeout={max(timeouts)} меньше {RATCHET_TIMEOUT_MIN_S} с: прогон mypy "
        "двух платформ в холодную не влезет, и воркер снова упадёт"
    )


def test_slice_check_pytest_env_marks_mypy_step():
    """Шаг pytest в slice_check выставляет REELSI_MYPY_STEP=1 — как джоба test в ci.yml.

    У slice_check шаг mypy свой, и второй прогон внутри набора не нужен: он дольше
    таймаута pytest, а thread-таймаут роняет воркер целиком. Имя переменной берём
    у храповика: разъехавшиеся имена — тихий отказ пропуска.
    """
    sc = _load_slice_check()
    assert sc.ENV_MYPY_STEP == _ratchet().ENV_MYPY_STEP, (
        "slice_check и тест-храповик разошлись в имени переменной: пропуск mypy не сработает"
    )
    env = sc.pytest_env()
    assert env[sc.ENV_MYPY_STEP] == "1", (
        "шаг pytest slice_check идёт без REELSI_MYPY_STEP=1: mypy гоняется вторым "
        "прогоном и роняет воркер под `-n auto`"
    )
    for name in (k for k in ("PATH", "SYSTEMROOT") if k in os.environ):
        assert env.get(name) == os.environ[name], (
            f"окружение шага собрано не из os.environ: {name} потерян"
        )


def test_env_var_name_is_not_shadowed():
    """Имя переменной шага mypy то же, что выставляет ci.yml, — иначе пропуск не сработает."""
    ratchet = _ratchet()
    assert ratchet.ENV_MYPY_STEP == "REELSI_MYPY_STEP", (
        "переменная шага mypy переименована без правки ci.yml: пропуск не сработает"
    )
    step = "\n".join(_pytest_step_block("test"))
    assert ratchet.ENV_MYPY_STEP in step, (
        "ci.yml и тест-храповик разошлись в имени переменной REELSI_MYPY_STEP"
    )


# --------------------------------------------------------------------------- #
# 6. slice_check гоняет ту же команду, что CI
# --------------------------------------------------------------------------- #


def _load_slice_check():
    """`tools/slice_check.py` импортом: проверяем его команду, а не запускаем прогон."""
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import slice_check

    return slice_check


def test_slice_check_pytest_command_matches_ci(monkeypatch):
    """Шаг pytest в slice_check идёт с `-n auto` и `-m "not perf"`, как CI."""
    sc = _load_slice_check()
    monkeypatch.delenv(sc.ENV_PYTEST_NO_XDIST, raising=False)
    monkeypatch.setattr(sc, "has_xdist", lambda: True)

    cmd = sc.pytest_command()
    assert "-n" in cmd and "auto" in cmd, "без xdist в команде slice_check набор идёт одним процессом"
    assert "not perf" in cmd, "в команде slice_check нет исключения тестов бюджета времени"

    # Аргументы покрытия из ci.yml идут последними, как и в самой джобе.
    with_cov = sc.pytest_command("--cov=api")
    assert with_cov[-1] == "--cov=api"


def test_slice_check_runs_without_xdist(monkeypatch):
    """Без xdist в окружении шаг pytest не падает: `-n` просто не добавляется."""
    sc = _load_slice_check()
    monkeypatch.delenv(sc.ENV_PYTEST_NO_XDIST, raising=False)
    monkeypatch.setattr(sc, "has_xdist", lambda: False)

    cmd = sc.pytest_command()
    assert "-n" not in cmd, "без xdist `-n auto` роняет запуск, а не параллелит"
    assert "not perf" in cmd, "исключение perf не должно зависеть от xdist"


def test_slice_check_linux_command_quotes_perf_marker(monkeypatch):
    """Команда шага linux не теряет кавычки `-m "not perf"` во вложенной оболочке."""
    sc = _load_slice_check()
    monkeypatch.setattr(sc, "has_xdist", lambda: True)

    # Ровно то, что уезжает в docker по ssh: `python` из образа + аргументы команды.
    remote = sc.linux_pytest_command("--cov=api")
    assert '"not perf"' in remote or "'not perf'" in remote, (
        f"кавычки маркера потерялись при упаковке в ssh-команду: {remote}"
    )
    assert "--cov=api" in remote


def test_slice_check_linux_command_splits_ci_args(monkeypatch):
    """Аргументы pytest из ci.yml уезжают в контейнер ОТДЕЛЬНЫМИ элементами.

    Строкой целиком (`args.append(ci_args)`) шесть аргументов склеивались в один
    элемент списка, `shlex.join` экранировал его целиком, и pytest в контейнере
    падал на разборе: «argument -q/--quiet: ignored explicit argument ' -n auto
    --dist loadgroup …'» — код возврата 4. Проверяем и список команды, и то, что
    реально уходит по ssh: разбор `shlex.split` обязан дать те же аргументы.
    """
    sc = _load_slice_check()
    monkeypatch.setattr(sc, "has_xdist", lambda: True)

    ci_args = '-q -n auto --dist loadgroup -m "not perf" --cov=api --cov-fail-under=77'
    wanted = ("-q", "-n", "auto", "--dist", "loadgroup", "-m", "not perf",
              "--cov=api", "--cov-fail-under=77")

    args = sc.pytest_command(ci_args)
    assert args[-1] == "--cov-fail-under=77", (
        f"аргументы CI идут не последними, как в джобе: {args}"
    )
    for want in wanted:
        assert want in args, f"аргумента {want!r} нет в команде шага pytest: {args}"

    parts = shlex.split(sc.linux_pytest_command(ci_args))
    for want in wanted:
        assert want in parts, (
            f"в docker уезжает не отдельный аргумент {want!r}: {parts}"
        )
    glued = [p for p in parts if " " in p and p.startswith("-")]
    assert not glued, f"аргументы pytest склеены в один элемент команды: {glued}"

    # Шаг pytest (Windows) идёт по ТОЙ ЖЕ функции без аргументов CI: его команда
    # не должна меняться от этой правки.
    local = sc.pytest_command()
    assert "-n" in local and "auto" in local and "not perf" in local, (
        f"команда локального шага pytest поехала: {local}"
    )


# --------------------------------------------------------------------------- #
# 8. Сторожа дерева репозитория идут в одном воркере: xdist_group("repo_tree")
# --------------------------------------------------------------------------- #

# Сторожа, читающие дерево ВМЕСТЕ с неотслеживаемыми файлами (`git ls-files
# --others` — двери `git_files`/`staged` в `tests/gitfiles.py`), делят воркер с
# самопроверкой `test_public_clean`: та кладёт в корень репозитория временный
# файл без SPDX, и соседний воркер падает на нём («нет заголовка») либо на его
# исчезновении (FileNotFoundError). `tracked` неотслеживаемых не видит, поэтому
# его сторожа в группу не обязаны.
REPO_TREE_XDIST_GROUP = "repo_tree"

# Вызовы-двери в список файлов дерева: эти включают неотслеживаемые файлы.
UNTRACKED_LISTING_NAMES = ("git_files", "staged")


def _pytestmark_groups(tree: ast.Module) -> set[str]:
    """Имена групп из `pytestmark` модуля: у сторожа маркер обычно один на файл."""
    groups: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for el in node.value.elts:
                groups |= _group_of(el)
        else:
            groups |= _group_of(node.value)
    return groups


def _lists_untracked_files(node: ast.AST) -> bool:
    """Есть ли в поддереве вызов двери в список файлов с неотслеживаемыми."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name in UNTRACKED_LISTING_NAMES:
            return True
    return False


def _repo_tree_tests(tree: ast.Module) -> set[tuple[str, int]]:
    """Тесты модуля, доходящие до списка с неотслеживаемыми, — прямо или через помощника.

    Тест может звать дверь сам (`gitfiles.git_files`) или через помощника модуля
    (`_public_files`), поэтому цепочка вызовов разворачивается так же, как у
    группы «chrome»: иначе сторож ослепнет на первой же правке помощника.
    """
    functions = _module_functions(tree)
    helpers = {fn.name for fn in functions if _lists_untracked_files(fn)}
    callers = {fn.name: _called_names(fn) for fn in functions}
    chain = set(helpers)
    grew = True
    while grew:
        grew = False
        for name, calls in callers.items():
            if name not in chain and calls & chain:
                chain.add(name)
                grew = True
    found: set[tuple[str, int]] = set()
    for fn in functions:
        if not fn.name.startswith("test_"):
            continue
        if fn.name in helpers or callers[fn.name] & chain:
            found.add((fn.name, fn.lineno))
    return found


def test_repo_tree_guards_are_pinned_to_one_xdist_group():
    """Тесты, читающие дерево с неотслеживаемыми файлами, помечены `repo_tree`."""
    expected: set[tuple[str, int]] = set()
    marked: set[tuple[str, int]] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        expected |= _repo_tree_tests(tree)
        module_groups = _pytestmark_groups(tree)
        for fn in _module_functions(tree):
            if not fn.name.startswith("test_"):
                continue
            own = any(REPO_TREE_XDIST_GROUP in _group_of(d) for d in fn.decorator_list)
            if own or REPO_TREE_XDIST_GROUP in module_groups:
                marked.add((fn.name, fn.lineno))

    assert expected, ("ни один тест не читает дерево с неотслеживаемыми файлами — "
                      "сканер групп ослеп")
    unmarked = sorted(expected - marked)
    assert not unmarked, (
        "тесты, читающие дерево с неотслеживаемыми файлами, без "
        f"@pytest.mark.xdist_group(\"{REPO_TREE_XDIST_GROUP}\"): "
        + ", ".join(f"{name}:{line}" for name, line in unmarked))
