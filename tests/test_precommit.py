# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
"""Тесты конфигурации pre-commit и инструмента проверки концов строк tools/check_eol.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.check_eol import check_file, count_lone_lf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load_precommit_config() -> dict[str, Any]:
    """Загружает .pre-commit-config.yaml через PyYAML или пропускает тест."""
    yaml = pytest.importorskip("yaml")
    cfg_path = ROOT / ".pre-commit-config.yaml"
    assert cfg_path.is_file(), "Файл .pre-commit-config.yaml не найден"
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), ".pre-commit-config.yaml должен быть словарём"
    return data


def test_precommit_config_structure() -> None:
    """Конфиг разбирается, все репозитории repo: local, все хуки language: system."""
    cfg = _load_precommit_config()
    repos = cfg.get("repos", [])
    assert isinstance(repos, list) and repos, "В конфиге должен быть непустой список repos"

    all_hooks: list[dict[str, Any]] = []
    for r in repos:
        assert isinstance(r, dict), "Элемент repos должен быть словарём"
        assert r.get("repo") == "local", "Разрешены только repo: local хуки"
        hooks = r.get("hooks", [])
        assert isinstance(hooks, list) and hooks, "Список hooks не должен быть пустым"
        for h in hooks:
            assert isinstance(h, dict), "Хук должен быть словарём"
            assert h.get("language") == "system", (
                f"Хук {h.get('id')} должен использовать language: system"
            )
            all_hooks.append(h)

    hook_ids = {h.get("id") for h in all_hooks}
    for expected_id in ("ruff", "mypy", "task-codes", "eol"):
        assert expected_id in hook_ids, f"Хук {expected_id} отсутствует в конфиге"


def test_check_eol_pure_crlf(tmp_path: Path) -> None:
    """Чистый файл с CRLF концами строк возвращает код 0."""
    sample = tmp_path / "pure_crlf.txt"
    sample.write_bytes(b"first line\r\nsecond line\r\nthird line\r\n")

    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_eol.py"), str(sample)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 0, f"Ожидался код 0 для pure CRLF, вывод: {res.stdout} {res.stderr}"

    is_mixed, lone_lf = check_file(sample)
    assert not is_mixed
    assert lone_lf == 0


def test_check_eol_pure_lf(tmp_path: Path) -> None:
    """Чистый файл с LF концами строк (например, .sh) возвращает код 0."""
    sample = tmp_path / "pure_lf.sh"
    sample.write_bytes(b"#!/usr/bin/env bash\necho hello\nexit 0\n")

    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_eol.py"), str(sample)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 0, f"Ожидался код 0 для pure LF, вывод: {res.stdout} {res.stderr}"

    is_mixed, lone_lf = check_file(sample)
    assert not is_mixed
    assert lone_lf == 3


def test_check_eol_mixed_file(tmp_path: Path) -> None:
    """Смешанный файл (есть и CRLF, и одиночные LF) падает с кодом 1 и выводит имя файла."""
    sample = tmp_path / "mixed_file.py"
    # 2 CRLF и 1 одиночный LF
    sample.write_bytes(b"line1\r\nline2\nline3\r\n")

    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_eol.py"), str(sample)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 1, "Для файла со смешанными концами строк ожидался код 1"
    output = res.stdout + res.stderr
    assert sample.name in output, "Вывод должен содержать имя смешанного файла"
    assert "1" in output, "Вывод должен содержать число одиночных LF"

    is_mixed, lone_lf = check_file(sample)
    assert is_mixed, "check_file должен определить файл как смешанный"
    assert lone_lf == 1, f"Ожидался 1 одиночный LF, получено {lone_lf}"


def test_count_lone_lf_helper() -> None:
    """Функция count_lone_lf корректно отсекает CRLF и считает только одиночные LF."""
    assert count_lone_lf(b"") == 0
    assert count_lone_lf(b"\r\n\r\n") == 0
    assert count_lone_lf(b"\n\n\n") == 3
    assert count_lone_lf(b"\r\na\nb\r\nc\n") == 2
