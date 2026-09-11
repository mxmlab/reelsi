# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Уборка за «Стоп»: temp-каталоги нарезки не должны переживать отмену.

omni_cut раскладывает в %TEMP% WAV всех камер — это сотни мегабайт на ролик. Убивается
он через taskkill /F, без atexit-хуков, поэтому за ним убирает сам сервер: подпроцесс
печатает свой каталог маркером `WORK_DIR=`, run_omnicut_job копит их в CURWORK, а
_kill_curproc удаляет.

Цепочка молча рвалась посередине (аудит 2026-08-14): в run_omnicut_job не было
`global CURWORK`, из-за чего присваивание делало имя ЛОКАЛЬНЫМ на всю функцию —
каталоги копились в списке, до которого _kill_curproc не дотягивается, а диск тихо
заполнялся. Ошибки при этом не возникало никакой.

Запуск: python -m pytest reelsi/tests -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from api import jobs  # noqa: E402


def test_curwork_is_global_in_omnicut_job():
    """Проверяем статически: динамически это стоило бы запуска настоящей нарезки.
    `global` виден в объекте кода — присвоенное имя лежит либо в co_names (глобальное),
    либо в co_varnames (локальное), третьего не дано."""
    code = jobs.run_omnicut_job.__code__
    assert "CURWORK" not in code.co_varnames, (
        "CURWORK снова локальный: потерян global, «Стоп» не почистит %TEMP%")
    assert "CURWORK" in code.co_names, "CURWORK вообще не читается — маркер WORK_DIR= потерян"


def test_kill_curproc_removes_work_dirs(tmp_path, monkeypatch):
    """Вторая половина цепочки: то, что попало в CURWORK, действительно удаляется."""
    work = tmp_path / "omnicut_abc"
    (work / "sub").mkdir(parents=True)
    (work / "sub" / "cam1.wav").write_bytes(b"0" * 1024)

    monkeypatch.setattr(jobs, "CURPROC", None)      # процесса нет — только уборка
    monkeypatch.setattr(jobs, "CURWORK", [str(work)])
    jobs._kill_curproc()
    assert not work.exists(), "temp-каталог нарезки пережил «Стоп»"


def test_kill_curproc_survives_missing_dir(monkeypatch, tmp_path):
    """Каталог мог убрать сам omni_cut, успев закрыться штатно, — это не ошибка."""
    monkeypatch.setattr(jobs, "CURPROC", None)
    monkeypatch.setattr(jobs, "CURWORK", [str(tmp_path / "нет-такого")])
    jobs._kill_curproc()        # не должно бросить
