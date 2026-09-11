# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Turn an xmeml timeline (our output, or a sequence you exported from Premiere
AFTER editing the cut) into an After Effects .jsx that rebuilds the FULL edit:
cameras, word subtitles, highlights, intro precomps, inserts, roto, music
(to_ae_full; build_combined merges several files into one .jsx).

Run in AE:  File -> Scripts -> Run Script File...  -> pick the .jsx

CLI:  python -m core.xml2ae "C:/.../EditedSequence.xml" [out.jsx]

С 2026-08-06 это пакет, а не файл на 1942 строки:

| модуль       | что там |
|--------------|---------|
| `parse`      | разбор xmeml: клипы камер, слова, вставки, интро, рото |
| `highlights` | жёлтые выделения и правка слова ПРЯМО В XML пользователя |
| `template`   | AE_FULL — шестьсот строк ExtendScript, ни строки Python |
| `jsutil`     | подстановка значений в шаблон (экранирование, числа, текст) |
| `layout`     | геометрия: карточки вставок, зум и дрейф камеры 1, раскладка |
| `build`      | сборка .jsx и виртуальный EDL для черновика |

Ниже переэкспортировано то, что берут снаружи. Остальное живёт в своём модуле
(`xml2ae.layout._ins_scale`) — пакет затем и заводили, чтобы не держать одну
плоскую свалку имён.
"""
from . import build, highlights, jsutil, layout, parse, template   # noqa: F401
from .parse import Cancelled, parse_full   # noqa: F401
from .highlights import (   # noqa: F401
    auto_highlights, write_highlights, set_highlights, edit_word, delete_word,
    shift_indices, shift_intro_rows, _word_color)
from .template import AE_FULL   # noqa: F401
from .jsutil import _jd   # noqa: F401
from .build import to_ae_full, build_combined, virtual_edl, scene_plan, write_srt_for   # noqa: F401
