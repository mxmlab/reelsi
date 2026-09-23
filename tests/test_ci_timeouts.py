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
