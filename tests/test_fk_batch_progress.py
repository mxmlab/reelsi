# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FK: два прогресса при рендере набора — по клипу и по всему набору.

Живой прогон 12 роликов (2026-08-26) показал: при рендере набора полоса прогресса
стоит на нуле весь рендер — `_run_proc_batch` не обновляла `RJOB["pct"]` вообще.
Пользователь: «надо чтобы показывало и процент общий, и процент по данному клипу».

Проверяем на потоке строк aerender двух композиций:
- pct ЭЛЕМЕНТА = доля текущей композиции (_parse_progress по кадрам (N)/(N/M),
  M из _comp_frames, когда aerender его не печатает);
- RJOB["pct"] = (готовых + доля текущей) / всего — монотонно, БЕЗ прыжка в 1.0
  после первой композиции (маркер Finished composition = конец ОДНОЙ, не всего);
- RJOB["cur"] = имя текущей композиции;
- имя композиции из маркера — В КАВЫЧКАХ и с точкой (дефект FJ): реальная строка
  «PROGRESS: 8/26/2026 12:55:08 AM: Finished composition "C0250".» -> C0250;
- ETA: не выдумывается, пока замеров меньше 15-20 с (eta=None).

Запуск: python -m pytest reelsi/tests -q
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api import render  # noqa: E402


class _FakePopen:
    """Подделка subprocess.Popen для aerender: stdout — список строк, wait — 0.
    stdout=PIPE приходит числом (-1); реальные строки передаём через класс-атрибут."""

    LINES = []

    def __init__(self, cmd, stdout=None, stderr=None, text=True, encoding=None,
                 errors=None, creationflags=0):
        self.stdout = list(self.LINES or [])
        self.returncode = 0

    def wait(self):
        return self.returncode


def _run_fake(lines, comps, render_dir=None):
    """Прогнать _run_proc_batch на поддельном Popen. comps — [(стем, имя, кадры), …].
    Возвращает RJOB (со стадиями/путями). render_dir — куда «рендерит» aerender:
    не задан — свежая пустая временная папка (почему, см. ниже)."""
    # Почему временная папка, а не строковый "exp": render_dir у _run_proc_batch
    # ОТНОСИТЕЛЬНЫЙ, и из каталога запуска уровнем выше репозитория резолвится
    # в БОЕВУЮ папку рендера пользователя, где реально лежит "РИЛС 9.mov" (297 МБ).
    # _rendered_ok() видит настоящий файл, композиция закрывается через item_done,
    # а он ставит pct=None — тест падал только при запуске извне reelsi.
    if render_dir is None:
        render_dir = tempfile.mkdtemp()
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[], eta=None)
    render.items_init(render.RJOB, render.RLOCK, [s for s, _c, _f in comps])
    for s, _c, _f in comps:
        render.item_set(render.RJOB, render.RLOCK, s, stage="render")
    import types
    orig = render.subprocess.Popen
    render.subprocess.Popen = types.SimpleNamespace
    try:
        render.subprocess.Popen = _FakePopen
        _FakePopen.LINES = lines
        render._run_proc_batch("aerender.exe", "набор.aep", comps, render_dir)
    finally:
        render.subprocess.Popen = orig
    return render.RJOB


def test_overall_pct_monotonic_no_jump_to_1():
    """Две композиции: после Finished первой и кадров второй общий pct = (1 + доля)/2
    и НЕ прыгает в 1.0 — маркер Finished это конец ОДНОЙ композиции, не всего."""
    lines = [
        "PROGRESS:  0:00:39:11 (100): 0 Seconds",
        "PROGRESS:  0:00:39:12 (200): 0 Seconds",
        'PROGRESS:  8/26/2026 12:55:08 AM: Finished composition "C0250".',
        "PROGRESS:  0:00:40:00 (500): 0 Seconds",     # вторая, 500/1000
    ]
    comps = [("01_C0233", "C0250", 1000), ("02_рилс", "РИЛС 9", 1000)]
    rjob = _run_fake(lines, comps)
    # общий pct = 0.35 + ((1 готовый + 0.5 текущей) / 2) * 0.65 = 0.8375
    # (в рамках 3-фазной шкалы 0..15% JSX, 15..35% AEP, 35..100% aerender, задание FO).
    # Главное: нигде не 1.0, пока не закрыта вторая композиция.
    assert rjob["pct"] is not None
    assert rjob["pct"] < 1.0, f"общий pct прыгнул в 1.0 до конца набора: {rjob['pct']}"
    assert 0.8 <= rjob["pct"] <= 0.9, rjob["pct"]


def test_item_pct_reflects_current_comp():
    """pct элемента = доля ТЕКУЩЕЙ композиции (кадры (N) / M из comps)."""
    lines = [
        "PROGRESS:  0:00:39:11 (100): 0 Seconds",      # комп1, 100/1000 = 0.1
        'PROGRESS:  8/26/2026 12:55:08 AM: Finished composition "C0250".',
        "PROGRESS:  0:00:40:00 (250): 0 Seconds",      # комп2, 250/1000 = 0.25
    ]
    comps = [("01_C0233", "C0250", 1000), ("02_рилс", "РИЛС 9", 1000)]
    _run_fake(lines, comps)
    # последний pct, что ставился текущему элементу — у комп2 (0.25), у комп1 — done 1.0
    it1 = next(it for it in render.RJOB["items"] if it["name"] == "01_C0233")
    it2 = next(it for it in render.RJOB["items"] if it["name"] == "02_рилс")
    assert it1["pct"] == 1.0, it1        # комп1 закрыта (Finished) — pct 1.0
    assert it2["pct"] is not None and 0.2 <= it2["pct"] <= 0.3, it2


def test_cur_is_comp_name():
    """RJOB['cur'] — имя текущей композиции, а не .aep."""
    lines = [
        "PROGRESS:  0:00:39:11 (100): 0 Seconds",
        'PROGRESS:  8/26/2026 12:55:08 AM: Finished composition "C0250".',
        "PROGRESS:  0:00:40:00 (50): 0 Seconds",
    ]
    comps = [("01_C0233", "C0250", 1000), ("02_рилс", "РИЛС 9", 1000)]
    rjob = _run_fake(lines, comps)
    assert rjob["cur"] in ("C0250", "РИЛС 9"), rjob["cur"]


def test_finished_comp_name_from_quoted_marker():
    """Реальная строка живого прогона: PROGRESS: 8/26/2026 12:55:08 AM: Finished
    composition "C0250". — имя в кавычках и с точкой; разборщик обязан отдать C0250
    без кавычек, без точки, без даты (дефект FJ: старая регулярка захватывала всё)."""
    line = 'PROGRESS:  8/26/2026 12:55:08 AM: Finished composition "C0250".'
    assert render._finished_comp_name(line) == "C0250"
    # запасной вариант без кавычек (другая локаль)
    assert render._finished_comp_name("Finished composition: C0250") == "C0250"
    assert render._finished_comp_name("Total Time Elapsed: 16 Min, 48 Sec") is None


def test_frame_progress_line_parsed():
    """Реальная строка прогресса кадра: (2352) без /M — доля считается по M из comps."""
    line = "PROGRESS:  0:00:39:11 (2352): 0 Seconds"
    assert render._parse_progress(line, 10000) == pytest.approx(0.2352)
    assert render._parse_progress(line, None) is None     # M неизвестен — нет доли


def test_eta_none_without_enough_samples():
    """ETA не выдумывается: меньше двух замеров или окно <15 с — None (прочерк)."""
    assert render._eta_secs([], 1000, 0) is None
    assert render._eta_secs([(0.0, 0), (5.0, 100)], 1000, 100) is None  # окно <15 с
    # окно 20 с, 200 кадров отрендерено из 1000, скорость 10 кадр/с -> 80 с
    assert render._eta_secs([(0.0, 0), (20.0, 200)], 1000, 200) == pytest.approx(80.0)
