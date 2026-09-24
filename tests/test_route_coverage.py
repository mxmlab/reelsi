# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сторож покрытия роутов api/ вызовами из тестов (tests/*.py).

Каждый роут @bp.route обязан иметь хотя бы один вызов через тестовый клиент
Flask (client.get/post/put/delete(...) или локальный хелпер). Новый роут без
поведенческого теста на вызов — красный.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.route_coverage import get_zero_call_routes, collect_route_coverage  # noqa: E402


def test_every_bp_route_has_client_call_in_tests() -> None:
    """У каждого роута в api/ есть хотя бы 1 вызов через тестовый клиент."""
    coverage = collect_route_coverage()
    assert len(coverage) >= 88, f"Ожидалось не менее 88 роутов, получено: {len(coverage)}"

    zero_routes = get_zero_call_routes()
    assert not zero_routes, (
        f"Обнаружены роуты api/ с нулём вызовов из тестов ({len(zero_routes)}):\n"
        + "\n".join(f"  - {r.rule} [{', '.join(r.methods)}]" for r in zero_routes)
    )
