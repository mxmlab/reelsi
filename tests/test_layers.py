# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож слоёв: `core/` не знает про `api/` и Flask, `doctor.py` — про `api/`.

ПОЧЕМУ он есть. Движок безголового рендера жил в `api/render.py` — HTTP-модуле, — и
`doctor.py` брал поиск After Effects прямо оттуда: диагностика окружения тянула за собой
Flask ради одной функции. Разрез сделан (движок уехал в `core/aerender.py`), но ничто не
мешало ему поехать обратно: обратный импорт `core` → `api` не падает и не мешает тестам,
он просто снова привязывает ядро к HTTP-слою. Ловится это только по исходникам.

Проверяется статически, деревом разбора (как `tests/test_infra_dedup.py`), а не
импортом: импорт `core` в тесте выполнился бы и при обратной зависимости — она видна
лишь в тексте модуля. Относительные импорты разворачиваются в абсолютные по месту
файла (`from .. import api` внутри `core/` — это импорт корневого `api`).

Запуск:  python -m pytest tests/test_layers.py -q
"""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Пакеты, которых в ядре быть не должно: HTTP-слой (`api`) и его движок (flask).
FORBIDDEN_IN_CORE = ("api", "flask")


def _module_names(path):
    """Имена модулей, которые файл импортирует: (имя, номер строки).

    `import a.b` даёт `a.b`; `from a.b import c` — `a.b` и `a.b.c` (c может быть
    подмодулем); относительный импорт разворачивается по месту файла.
    """
    rel = path.relative_to(ROOT)
    pkg = rel.parent.parts                                  # каталог файла от корня
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            # level=1 — текущий пакет, level=2 — родительский и так далее до корня
            base = pkg[:len(pkg) - (node.level - 1)] if node.level else ()
            head = ".".join([p for p in base if p] + ([node.module] if node.module else []))
            if head:
                out.append((head, node.lineno))
            out += [(".".join(p for p in (head, a.name) if p), node.lineno)
                    for a in node.names]
    return out


def _forbidden(path, names):
    """Импорты `path`, попадающие в запретные пакеты (сам пакет или его подмодуль)."""
    bad = []
    for name, line in _module_names(path):
        for forb in names:
            if name == forb or name.startswith(forb + "."):
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{line}: import {name}")
    return bad


def test_core_does_not_import_api_or_flask():
    """Ни один модуль `core/` не импортирует `api` и `flask`.

    Обратный импорт не ломает ни тестов, ни запуска — он ломает слой: ядро снова
    оказывается доступно только вместе с HTTP-сервером (ради этого движок рендера
    и выносили из `api/render.py`)."""
    files = sorted((ROOT / "core").rglob("*.py"))
    assert files, "в core/ не нашлось ни одного модуля — проверять нечего"
    bad = []
    for path in files:
        bad += _forbidden(path, FORBIDDEN_IN_CORE)
    assert not bad, ("ядро импортирует HTTP-слой — вынеси зависимость в core/:\n  "
                     + "\n  ".join(bad))


def test_doctor_does_not_import_api():
    """`doctor.py` не импортирует `api`: диагностика окружения не должна поднимать Flask.

    Раньше он брал `_find_ae` из `api.render` — инверсия слоёв, из-за которой
    проверка «что стоит на машине» требовала собранного веб-слоя."""
    path = ROOT / "doctor.py"
    assert path.is_file(), "нет doctor.py — проверять нечего"
    bad = _forbidden(path, ("api",))
    assert not bad, ("doctor.py импортирует api — движок бери из core/:\n  "
                     + "\n  ".join(bad))


def test_render_job_and_jobstate_do_not_import_flask():
    """`core/render_job.py` и `core/jobstate.py` не импортируют `flask` или `api`.

    Оркестрация рендера и состояние задач изолированы от HTTP-фреймворка."""
    for mod_name in ("core/render_job.py", "core/jobstate.py"):
        p = ROOT / mod_name
        assert p.is_file(), f"нет {mod_name} — проверять нечего"
        bad = _forbidden(p, FORBIDDEN_IN_CORE)
        assert not bad, f"{mod_name} импортирует HTTP-слой:\n  " + "\n  ".join(bad)


def test_api_render_no_subprocess_and_bounded_function_length():
    """В `api/render.py` нет `subprocess.Popen` и нет функций длиннее 60 строк.

    Оркестрация вынесена в `core/render_job.py`, в HTTP-слое остаются только
    роуты и тонкие обёртки."""
    render_py = ROOT / "api" / "render.py"
    assert render_py.is_file(), "нет api/render.py — проверять нечего"
    tree = ast.parse(render_py.read_text(encoding="utf-8"), filename="api/render.py")

    # 1. Нет вызовов или атрибутов Popen, нет импорта subprocess
    bad_popen = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess" or a.name.startswith("subprocess."):
                    bad_popen.append(f"api/render.py:{node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess" or (node.module and node.module.startswith("subprocess.")):
                bad_popen.append(f"api/render.py:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr == "Popen":
            bad_popen.append(f"api/render.py:{node.lineno}: обращение к Popen")
    assert not bad_popen, (
        "в api/render.py обнаружено использование subprocess/Popen (оркестрация должна быть в core/render_job):\n  "
        + "\n  ".join(bad_popen)
    )

    # 2. Нет функций длиннее порога 60 строк
    max_lines = 60
    long_funcs = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > max_lines:
                long_funcs.append(f"{node.name} ({length} строк > {max_lines})")
    assert not long_funcs, (
        f"в api/render.py найдены функции длиннее {max_lines} строк: {', '.join(long_funcs)}"
    )


def test_api_core_no_rebound_jobstate_vars():
    """В api/_core.py нет имён JOB_LOCK_PATH, _JOB_LOCK_FH, JOB_STATE_PATH на уровне модуля.

    Переприсваиваемые переменные и константы путей живут в core.jobstate;
    реэкспорт через from core.jobstate import ... копирует значения на момент
    импорта, ломая смену путей в conftest и отслеживание захвата лока."""
    core_py = ROOT / "api" / "_core.py"
    assert core_py.is_file(), "нет api/_core.py — проверять нечего"
    tree = ast.parse(core_py.read_text(encoding="utf-8"), filename="api/_core.py")

    forbidden = {"JOB_LOCK_PATH", "_JOB_LOCK_FH", "JOB_STATE_PATH"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in forbidden or alias.asname in forbidden:
                    bad.append(f"api/_core.py:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in forbidden:
                    bad.append(f"api/_core.py:{node.lineno}: assign {target.id}")

    assert not bad, (
        "в api/_core.py обнаружен реэкспорт/объявление перепривязываемых переменных:\n  "
        + "\n  ".join(bad)
    )

    import api._core as api_core
    for name in forbidden:
        assert not hasattr(api_core, name), f"api._core имеет атрибут {name} на уровне модуля"


def test_no_unmocked_pid_0_or_1_in_tests():
    """Ни один тест не создаёт фальшивый процесс с pid = 0/1 без подмены killpg.

    На Linux в docker PID 1 — init контейнера; фальшивый pid=1 в тесте при
    вызове kill_tree посылал SIGKILL группе 1 и убивал весь CI runner.
    Если тест использует pid = 0 или 1 в заглушке процесса, он обязан
    подменять killpg на записывающую заглушку.
    """
    tests_dir = ROOT / "tests"
    test_files = sorted(tests_dir.glob("test_*.py"))
    assert test_files, "тесты не найдены"

    bad = []
    for f in test_files:
        content = f.read_text(encoding="utf-8")
        if "pid" not in content:
            continue
        try:
            tree = ast.parse(content, filename=str(f.name))
        except SyntaxError:
            continue

        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            # Ищем присваивания pid = 0 или pid = 1 внутри функций тестов (в классах-заглушках или атрибутах)
            has_bad_pid = False
            bad_line = func.lineno
            for node in ast.walk(func):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        target_name = None
                        if isinstance(target, ast.Name) and target.id == "pid":
                            target_name = "pid"
                        elif isinstance(target, ast.Attribute) and target.attr == "pid":
                            target_name = target.attr
                        if target_name and isinstance(node.value, ast.Constant) and node.value.value in (0, 1):
                            has_bad_pid = True
                            bad_line = node.lineno
                            break
                if has_bad_pid:
                    break

            if has_bad_pid:
                # Проверяем, что в теле функции есть упоминание killpg (подмена в monkeypatch)
                func_text = ast.get_source_segment(content, func) or ""
                if "killpg" not in func_text:
                    bad.append(f"{f.name}:{bad_line}: {func.name} задаёт pid 0/1, но не подменяет killpg")

    assert not bad, (
        "Найдены тесты с фальшивым pid = 0/1 без monkeypatch для killpg:\n  "
        + "\n  ".join(bad)
    )



