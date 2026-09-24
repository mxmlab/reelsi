# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Проверка таймаутов джоб в .github/workflows/ci.yml.

Зачем. Зависшая джоба без таймаута может жечь до 6 часов раннера (на Windows
считается x2). Каждая джоба обязана объявлять timeout-minutes на уровне джобы.
Значения согласованы: test — 25, test-windows — 35, остальные (scan, smoke,
requirements, jsx) — 10 минут.

Разбор YAML выполняется без внешних зависимостей (pyyaml в CI не устанавливается).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")

EXPECTED_TIMEOUTS = {
    "test": 25,
    "test-windows": 35,
    "scan": 10,
    "smoke": 10,
    "requirements": 10,
    "jsx": 10,
}


def _parse_job_timeouts_text(text: str) -> dict[str, int]:
    """Извлекает {job_name: timeout_minutes} построчным разбором без pyyaml."""
    jobs: dict[str, int] = {}
    current_job: str | None = None
    in_jobs = False

    for raw_line in text.splitlines():
        # Секция верхнего уровня
        if raw_line and not raw_line.startswith(" ") and not raw_line.startswith("#"):
            in_jobs = (raw_line.rstrip() == "jobs:")
            current_job = None
            continue

        if not in_jobs:
            continue

        # Заголовок джобы: ровно 2 пробела отступа ("  job-name:")
        m_job = re.match(r"^ {2}([a-zA-Z0-9_-]+):\s*$", raw_line)
        if m_job:
            current_job = m_job.group(1)
            continue

        # Внутри джобы: timeout-minutes на уровне джобы (4 пробела отступа)
        if current_job:
            m_timeout = re.match(r"^ {4}timeout-minutes:\s*([0-9]+)\s*(?:#.*)?$", raw_line)
            if m_timeout:
                jobs[current_job] = int(m_timeout.group(1))

    return jobs


def test_у_каждой_джобы_ci_есть_timeout_minutes():
    """Каждая джоба ci.yml имеет объявленный timeout-minutes."""
    assert os.path.isfile(CI_YML), f"файл {CI_YML} не найден"
    with open(CI_YML, encoding="utf-8") as f:
        text = f.read()

    timeouts = _parse_job_timeouts_text(text)

    # Проверяем, что найдены все ожидаемые джобы
    for job, expected in EXPECTED_TIMEOUTS.items():
        assert job in timeouts, f"джоба {job} не имеет timeout-minutes на уровне джобы в ci.yml"
        assert timeouts[job] == expected, (
            f"джоба {job} имеет timeout-minutes={timeouts[job]}, ожидалось {expected}"
        )

    # Ни одна джоба не должна иметь таймаут больше 35 минут
    for job, minutes in timeouts.items():
        assert minutes <= 35, f"джоба {job} имеет чрезмерный таймаут {minutes} > 35 минут"

    # Если доступен PyYAML, валидируем также через официальный парсер
    try:
        import yaml
        data = yaml.safe_load(text)
        yaml_jobs = data.get("jobs", {})
        for job, expected in EXPECTED_TIMEOUTS.items():
            assert job in yaml_jobs, f"в ci.yml нет джобы {job}"
            assert yaml_jobs[job].get("timeout-minutes") == expected, (
                f"yaml parser: джоба {job} timeout-minutes != {expected}"
            )
    except ImportError:
        pass


def test_шаг_tests_джобы_test_имеет_coverage_file_в_env():
    """У шага Tests джобы test объявлен COVERAGE_FILE в env вне репозитория, если есть --cov."""
    assert os.path.isfile(CI_YML), f"файл {CI_YML} не найден"
    with open(CI_YML, encoding="utf-8") as f:
        text = f.read()

    # Текстовый построчный разбор
    in_jobs = False
    in_test_job = False
    step_has_cov = False
    step_env_coverage_file: str | None = None
    in_env = False

    for raw_line in text.splitlines():
        if raw_line and not raw_line.startswith(" ") and not raw_line.startswith("#"):
            in_jobs = (raw_line.rstrip() == "jobs:")
            in_test_job = False
            continue

        if not in_jobs:
            continue

        m_job = re.match(r"^ {2}([a-zA-Z0-9_-]+):\s*$", raw_line)
        if m_job:
            in_test_job = (m_job.group(1) == "test")
            continue

        if not in_test_job:
            continue

        # Шаг джобы test
        m_step = re.match(r"^ {6}-\s*name:\s*(.+)$", raw_line)
        if m_step:
            if step_has_cov:
                break
            in_env = False
            continue

        if re.match(r"^ {8}env:\s*$", raw_line):
            in_env = True
            continue

        if in_env:
            m_cov_env = re.match(r"^ {10}COVERAGE_FILE:\s*(.+)$", raw_line)
            if m_cov_env:
                step_env_coverage_file = m_cov_env.group(1).strip()
            elif not raw_line.startswith(" " * 10):
                in_env = False

        m_run = re.match(r"^ {8}run:\s*(.+)$", raw_line)
        if m_run and "--cov" in m_run.group(1):
            step_has_cov = True

    assert step_has_cov, "в джобе test не найден шаг с запуском pytest c --cov"
    assert step_env_coverage_file is not None, (
        "у шага с --cov в джобе test отсутствует COVERAGE_FILE в env"
    )
    assert "${{ runner.temp }}" in step_env_coverage_file or "/tmp" in step_env_coverage_file, (
        f"COVERAGE_FILE={step_env_coverage_file} должен указывать во временный каталог вне репозитория"
    )

    # Проверка через PyYAML, если доступен
    try:
        import yaml
        data = yaml.safe_load(text)
        steps = data.get("jobs", {}).get("test", {}).get("steps", [])
        found_cov_step = False
        for step in steps:
            run_cmd = step.get("run", "")
            if isinstance(run_cmd, str) and "--cov" in run_cmd:
                found_cov_step = True
                env = step.get("env", {})
                assert "COVERAGE_FILE" in env, f"шаг {step.get('name')} с --cov не имеет COVERAGE_FILE в env"
                assert "${{ runner.temp }}" in env["COVERAGE_FILE"] or "/tmp" in env["COVERAGE_FILE"]
        assert found_cov_step, "yaml parser: не найден шаг с --cov в джобе test"
    except ImportError:
        pass

