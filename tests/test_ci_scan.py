# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Сторож CI: torch ставится в чистом окружении, история и зависимости сканируются.

1. В ci.yml нет подмены индекса PyPI (одиночный index-url): подмена отдаёт PyPI
   целиком в пользу индекса pytorch, а там typing-extensions есть только исходником,
   и его сборка требует flit_core, которого в индексе нет — в чистом окружении
   установка падает. Рабочий вариант — дополнение (extra-index-url). Сейчас джоба
   зелена только за счёт кэша pip у setup-python, поэтому откат обязан её ронять.
2. Джоба `scan` ищет секреты во всей истории (gitleaks) и уязвимые зависимости
   (pip-audit); от джобы `test` она не зависит и идёт с ней параллельно.
3. Чекаут джобы `scan` — с `fetch-depth: 0`: с глубиной по умолчанию gitleaks видит
   один коммит, и проверка секретов вырождается в пустую.
4. `.gitleaks.toml` не глушит правило generic-api-key целиком: исключения — только по
   путям трёх файлов с разобранными ложными срабатываниями.

ci.yml читаем текстом: yaml в тестовой джобе CI не ставится, а проверяем мы ровно те
строки, которые правят руками.

Запуск: python -m pytest tests/test_ci_scan.py -q
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
GITLEAKS_TOML = os.path.join(ROOT, ".gitleaks.toml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _job_block(text, job):
    """Строки джобы `job` из ci.yml: от её заголовка до заголовка следующей джобы."""
    lines = text.splitlines()
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


def _rule_blocks(text):
    """Тела секций `[[rules]]` из .gitleaks.toml — по строке на блок."""
    blocks = []
    body = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            body = [] if s == "[[rules]]" else None
            if body is not None:
                blocks.append(body)
        elif body is not None:
            body.append(line)
    return ["\n".join(b) for b in blocks]


def test_индекс_pytorch_дополняет_pypi_а_не_заменяет():
    """Подмена индекса роняет установку torch в чистом окружении (нет flit_core)."""
    text = _read(CI_YML)
    bad = [f"{i}: {line.strip()}" for i, line in enumerate(text.splitlines(), 1)
           if "--index-url" in line]
    assert not bad, (
        "PyPI подменён индексом pytorch (`--index-url` заменяет его целиком, а не\n"
        "дополняет). В индексе pytorch typing-extensions есть только исходником, его\n"
        "сборка требует flit_core, которого в индексе нет:\n"
        "  ERROR: Could not find a version that satisfies the requirement flit_core<4,>=3.11\n"
        "В чистом окружении установка падает; зелено сейчас только за счёт кэша pip\n"
        "у setup-python. Нужно дополнение (`--extra-index-url`). Строки:\n" + "\n".join(bad)
    )
    torch_lines = [line for line in text.splitlines() if "install torch" in line]
    assert len(torch_lines) == 2, f"torch ставится не в двух джобах: {torch_lines}"
    for line in torch_lines:
        assert "--extra-index-url" in line, f"torch ставится без дополнения к PyPI: {line.strip()}"


def test_джоба_scan_ищет_секреты_и_уязвимости():
    """`scan` есть в ci.yml, и в ней оба сканера."""
    block = "\n".join(_job_block(_read(CI_YML), "scan"))
    assert "gitleaks" in block, "в джобе scan нет gitleaks — секреты в истории не проверяются"
    assert "pip-audit" in block, "в джобе scan нет pip-audit — уязвимые зависимости не проверяются"


def test_чекаут_scan_читает_всю_историю():
    """Без `fetch-depth: 0` gitleaks видит один коммит — проверка секретов пустая."""
    block = _job_block(_read(CI_YML), "scan")
    checkout = next((i for i, line in enumerate(block) if "actions/checkout" in line), None)
    assert checkout is not None, "в джобе scan нет чекаута"
    step = []
    for line in block[checkout + 1:]:
        if line.strip().startswith("- "):
            break  # началась следующая ступень
        step.append(line)
    assert any("fetch-depth: 0" in line for line in step), (
        "чекаут джобы scan без `fetch-depth: 0`: gitleaks читает один коммит,\n"
        "и проверка секретов в истории ничего не значит"
    )


def test_gitleaks_конфиг_не_глушит_правило_целиком():
    """Исключения — по путям ложных срабатываний, а не отключение правила."""
    assert os.path.exists(GITLEAKS_TOML), (
        ".gitleaks.toml нет: прогон по истории падает на ложных срабатываниях\n"
        "generic-api-key (имена полей схемы стиля и фикстуры тестов)"
    )
    text = _read(GITLEAKS_TOML)
    assert re.search(r"^\s*useDefault\s*=\s*true\s*$", text, re.M), (
        "в .gitleaks.toml нет `useDefault = true` — набор правил по умолчанию не наследуется"
    )

    for body in _rule_blocks(text):
        if not re.search(r"""\bid\s*=\s*["']generic-api-key["']""", body):
            continue
        assert not re.search(r"""^\s*regex\s*=\s*["']\s*["']\s*$""", body, re.M), (
            "правило generic-api-key переопределено с пустым regex — оно выключено целиком"
        )
        assert not re.search(r"^\s*stopwords\s*=", body, re.M), (
            "у правила generic-api-key заданы stopwords — оно выключено целиком"
        )

    # Исключение с `*` накрыло бы и остальные файлы: правило стало бы мёртвым.
    wildcards = [line.strip() for line in text.splitlines() if "*" in line.split("#", 1)[0]]
    assert not wildcards, (
        "исключение с `*` накрывает не только ложные срабатывания — правило мертво:\n"
        + "\n".join(wildcards)
    )
