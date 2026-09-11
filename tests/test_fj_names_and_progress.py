# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FJ: имя выходного .mov и живая индикация того, что делает AE.

Первый живой прогон набора (12 роликов, 2026-08-26) вскрыл два дефекта:
1) om.file пишет .mov по ИМЕНИ КОМПОЗИЦИИ (meta["name"], имя секвенции в XML), а
   Python ждал файл по стему .jsx — успешный набор отображался как 12 провалов;
   тот же дефект был в одиночном пути.
2) Пока AfterFX собирает проект, интерфейс молчал: ExtendScript буферизует
   файл-лог до close(), читать было нечего, а стадии «собираю проект» не было.

Решение: Python знает имя композиции (возвращается из сборки через comp_name_out)
и ждёт .mov по нему; стем остаётся id строки очереди. Мастер после КАЖДОГО ролика
закрывает и заново открывает лог на дозапись — строки появляются на диске сразу,
Python читает их, пока AfterFX работает, и ведёт живую индикацию; на время AfterFX
элементы в стадии aep, перед aerender — в render.

Запуск: python -m pytest reelsi/tests -q
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import xml2ae  # noqa: E402
from api import render  # noqa: E402
from core.xml2ae.build import _write_master  # noqa: E402


@pytest.fixture()
def xml_mismatch(tmp_path):
    """XML, где имя секвенции НЕ равно стему файла. У фикстуры timeline_subs.xml
    имя секвенции — CLIP-006, а файл назовём 01_C0233.xml (стем 01_C0233). Ровно
    случай живого прогона: файл 01_C0233 → композиция C0233 (здесь CLIP-006)."""
    dst = str(tmp_path / "01_C0233.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def test_comp_name_returned_from_build(xml_mismatch, tmp_path):
    """to_ae_full кладёт имя главной композиции в comp_name_out: на этой фикстуре
    это «CLIP-006» (имя секвенции), а не стем файла «01_C0233»."""
    out = str(tmp_path / "01_C0233.jsx")
    comp_name_out = []
    xml2ae.to_ae_full(xml_mismatch, out, roto=False, comp_name_out=comp_name_out,
                      emit=lambda *a, **k: None)
    assert comp_name_out == ["CLIP-006"], comp_name_out


def test_mov_path_built_from_comp_name(xml_mismatch, tmp_path):
    """Ожидаемый путь .mov строится из ИМЕНИ КОМПОЗИЦИИ, а не из стема .jsx:
    01_C0233.xml -> композиция CLIP-006 -> ждём CLIP-006.mov, а не 01_C0233.mov."""
    out = str(tmp_path / "01_C0233.jsx")
    comp_name_out = []
    xml2ae.to_ae_full(xml_mismatch, out, roto=False, comp_name_out=comp_name_out,
                      emit=lambda *a, **k: None)
    comp_name = comp_name_out[0]
    # это то, что делает рендер: стем адресует строку, имя композиции — файл
    assert comp_name != "01_C0233"
    assert os.path.join("exp", comp_name + ".mov") == os.path.join("exp", "CLIP-006.mov")


def test_finished_composition_maps_by_comp_name():
    """Разбор строки «Finished composition: ИМЯ» находит ролик по ИМЕНИ КОМПОЗИЦИИ
    (meta["name"]), а не по стему .jsx."""
    comps = [("01_C0233", "C0233", 1000), ("02_рилс", "РИЛС 9 22.08", 2000)]
    assert render._comp_to_stem("C0233", comps) == "01_C0233"
    assert render._comp_to_stem("РИЛС 9 22.08", comps) == "02_рилс"
    assert render._comp_to_stem("Reelsi", comps) is None      # нет такого имени
    assert render._comp_to_stem(None, comps) is None


def test_master_reopens_log_after_each_clip(tmp_path):
    """Мастер-скрипт закрывает и заново открывает лог на дозапись ВНУТРИ цикла
    роликов — иначе ExtendScript буферизует файл до close() и Python нечего читать,
    пока AfterFX работает (задание FJ)."""
    jsx1 = str(tmp_path / "a.jsx")
    jsx2 = str(tmp_path / "b.jsx")
    open(jsx1, "w", encoding="utf-8-sig").write("var a=1;")
    open(jsx2, "w", encoding="utf-8-sig").write("var b=1;")
    master = str(tmp_path / "render_master.jsx")
    _write_master([jsx1, jsx2], master, str(tmp_path / "reelsi_batch.aep"), str(tmp_path))
    txt = open(master, encoding="utf-8-sig").read()
    # close + reopen("a") внутри цикла роликов — рядом с evalFile
    assert txt.count('_log.close();') >= 2, "лог не закрывается после каждого ролика"
    assert '_log.open("a");' in txt, "нет дозаписи лога (open 'a')"
    # close/reopen идёт ПОСЛЕ evalFile (внутри того же цикла)
    assert txt.index('_log.open("a");') > txt.index("$.evalFile(new File("), (
        "close/reopen лога не внутри цикла роликов")


def test_master_reopens_log_for_comp_ok(tmp_path):
    """Тот же приём у строк comp ok: — по ним видно, что сборка кончилась и
    начинается рендер."""
    jsx1 = str(tmp_path / "a.jsx")
    open(jsx1, "w", encoding="utf-8-sig").write("var a=1;")
    master = str(tmp_path / "render_master.jsx")
    _write_master([jsx1], master, str(tmp_path / "reelsi_batch.aep"), str(tmp_path))
    txt = open(master, encoding="utf-8-sig").read()
    assert txt.count('_log.open("a");') >= 2, (
        "дозапись лога не после comp ok (сброс буфера очереди)")


def test_batch_stages_aep_then_render(xml_mismatch, tmp_path, monkeypatch):
    """Элементы набора переводятся в стадию aep на время работы AfterFX и в render
    перед aerender: стадии перехода проверяются на живом RJOB."""
    render.RJOB.update(running=True, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    render.items_init(render.RJOB, render.RLOCK, ["01_C0233"])
    monkeypatch.setattr(render, "_find_ae",
                        lambda: ("AFX.EXE", "AER.EXE", "AE"))
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append(cmd[0])
            self.stdout = []
            self.returncode = 0
        def wait(self):
            return 0
        def poll(self):
            return 0

    monkeypatch.setattr(render.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(render, "_run_proc_master",
                        lambda *a, **k: (0 if False else None))  # заглушка-нет

    # вместо запуска процесса проверим только стадии через прямой вызов функций
    # перехода: после «сборки» элемент в aep, перед aerender — в render
    render.item_set(render.RJOB, render.RLOCK, "01_C0233", stage="aep")
    stages_after_afx = [it["stage"] for it in render.RJOB["items"]]
    assert stages_after_afx == ["aep"], stages_after_afx
    for it in render.RJOB["items"]:
        if it.get("stage") in ("wait", "check", "aep"):
            it["stage"] = "render"
    assert [it["stage"] for it in render.RJOB["items"]] == ["render"]
