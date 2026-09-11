# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Хвост безголового режима в xml2ae/build.py (задание BT).

AfterFX -noui отработал, а .aep не появился — весь хвост был без единого try,
и любое исключение (applyTemplate с пресетом пользователя, save без галки
«Allow Scripts to Write Files and Access Network») обрывало функцию молча.
Три золотых факта:

- ручной режим (render_dir не задан) не меняется НИ НА БАЙТ — в нём очередь не
  ставится и save не делается, это отдельный путь для пользователя у экрана;
- безголовый — сохраняет проект ДВАЖДЫ: до очереди (чтобы .aep появился, даже
  если очередь не собралась) и после (чтобы в .aep попала очередь, иначе
  aerender отрендерит пустоту);
- каждый шаг хвоста защищён своим try/catch с $.writeln, который называет, что
  именно не вышло, а app.quit() стоит в finally — исключение не должно оставлять
  AE висеть процессом.

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.xml2ae import build as build  # noqa: E402

MANUAL_TAIL = "    main.openInViewer();\n    app.endUndoGroup();\n"


def test_manual_tail_unchanged_byte_for_byte():
    """Ручной режим (render_dir не задан) — прежняя строка, ни байтом больше."""
    assert build._render_tail(None, None) == MANUAL_TAIL


def test_headless_tail_saves_before_and_after_queue():
    """Безголовый — два save (до очереди и после), факты перед выходом, quit в finally."""
    t = build._render_tail("C:/out", "C:/proj/01.aep")
    assert t.count("app.project.save") == 2, t
    assert "rq.numItems=" in t and "exists=" in t, t
    assert "}finally{" in t and "app.quit()" in t, t


def test_headless_tail_every_step_has_named_catch():
    """Каждая точка обрыва названа в логе — по нему видно, где встало."""
    t = build._render_tail("C:/out", "C:/proj/01.aep")
    for marker in ("первый save не выполнился", "applyTemplate('Best Settings') не применился",
                   "applyTemplate('Untitled 1') не применился", "установка om.file не вышла",
                   "второй save не выполнился", "непредвиденная ошибка хвоста"):
        assert marker in t, t


def test_headless_tail_uses_explicit_aep_and_aelog_paths():
    """Путь .aep и .aelog задаёт Python, а не $.fileName (задание CD): AfterFX зовётся по
    короткому имени .jsx, и вывод имени проекта из $.fileName дал бы короткое имя .aep.
    Лог идёт ФАЙЛОМ, не $.writeln, и создаётся ПЕРВЫМ делом — его отсутствие у Python
    означает «скрипт не запустился»."""
    t = build._render_tail("C:/out", "C:/proj/01 РИЛС.aep")
    assert 'new File("C:/proj/01 РИЛС.aep")' in t, t           # .aep явной константой
    assert '$.fileName.replace' not in t, t                     # из $.fileName больше не берём
    assert 'new File("C:/proj/01 РИЛС.aelog.txt")' in t, t      # лог файлом рядом с проектом
    assert '$.writeln(' not in t, t                          # свои сообщения — не $.writeln
    assert t.index('_log.open("w")') < t.index("app.project.save"), t   # лог открыт первым делом
    assert "savePrefAsString" not in t and "Allow Scripts" not in t, t  # галка тут ни при чём (задание CD)
