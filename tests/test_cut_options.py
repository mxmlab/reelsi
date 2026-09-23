# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Умолчания классической нарезки живут в одном месте.

Зачем. До этой задачи одни и те же числа стояли дважды: в `reelsi.build_parser()`
и в `argparse.Namespace`, который собирал `api/jobs.py` ради `process_pair`. Правка
умолчания в одном месте молча не доезжала до другого: CLI резал с `big_chunk=6.0`,
а джоб — с тем, что оказалось в его собственной копии.

Теперь умолчания объявлены полями `CutOptions` (`core/cutjob.py`), CLI берёт из них
значения своих опций, API собирает `CutOptions` из opts запроса. Здесь стерегутся:
  1. CLI без аргументов и `CutOptions()` дают ОДИНАКОВЫЕ значения всех полей;
  2. числовые умолчания остались историческими (golden) — иначе тест сверял бы
     «CLI против класса», то есть поле само с собой, и подмена умолчания прошла бы
     незамеченной: обе стороны читают одно и то же поле и разошлись бы только вместе.
"""
import os
import sys
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import reelsi  # noqa: E402
from api import jobs  # noqa: E402
from core import cutstages  # noqa: E402
from core.cutjob import CutOptions  # noqa: E402

# Исторические умолчания CLI (reelsi.build_parser() раньше) — контракт, а не
# украшение: их видит пользователь, когда запускает `python reelsi.py` без флагов.
GOLDEN = {
    "no_subs": False, "no_dedup": False, "no_srt": False, "ae": False,
    "keep": "last", "model": "large-v3", "scale": 50.4, "vad_thresh": 18.0,
    "min_silence": 0.30, "pad": 0.08, "no_cut": False, "aggressive": False,
    "restarts": False, "forced_align": False, "cam_return": 2, "big_chunk": 6.0,
    "pause_max": 1.0,
}


def test_cli_без_аргументов_совпадает_с_CutOptions():
    """`reelsi.py` без флагов и `CutOptions()` — одни и те же значения всех полей."""
    cli = CutOptions.from_namespace(reelsi.build_parser().parse_args([]))
    default = CutOptions()
    diff = {k: (v, getattr(default, k)) for k, v in asdict(cli).items()
            if v != getattr(default, k)}
    assert not diff, "CLI и CutOptions разошлись (CLI, класс): %s" % diff


def test_умолчания_остались_историческими():
    """Числа умолчаний — те же, что были у CLI: golden."""
    assert asdict(CutOptions()) == GOLDEN
    assert asdict(CutOptions.from_namespace(reelsi.build_parser().parse_args([]))) == GOLDEN


def test_api_без_необязательных_полей_даёт_умолчания_класса():
    """opts запроса без необязательных ключей -> те же умолчания, что у CLI.

    `/api/run` шлёт opts, собранные `cutstages.to_reelsi_opts`: `cam_return` там
    есть (из порогов), а `big_chunk`, `pause_max`, `restarts` и `forced_align` не
    приходят вовсе — они обязаны взяться из CutOptions, а не из второй копии числа
    в api/jobs.py.
    """
    api = jobs.cut_options(cutstages.to_reelsi_opts({}))
    cli = CutOptions.from_namespace(reelsi.build_parser().parse_args([]))
    for name in ("keep", "model", "scale", "vad_thresh", "min_silence", "pad",
                 "no_cut", "aggressive", "restarts", "forced_align",
                 "cam_return", "big_chunk", "pause_max"):
        assert getattr(api, name) == getattr(cli, name) == GOLDEN[name], name
    # opts задают эти поля явно, и «нет» в них означает «не надо», а не «не задано»
    assert (api.no_subs, api.no_dedup, api.no_srt) == (True, True, True)
