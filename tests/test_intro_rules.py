# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
from tools.intro_rules import split_mid_groups_precomps


def test_split_mid_groups_precomps_empty():
    """Пустые mid_groups: [] не должны вызывать IndexError (вызов без исключения)."""
    all_pc, n_null = split_mid_groups_precomps([])
    assert isinstance(all_pc, list)
