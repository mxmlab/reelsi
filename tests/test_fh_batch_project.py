# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание FH: весь набор в ОДИН проект AE и один запуск рендера.

Сейчас на каждый ролик — два запуска Adobe (AfterFX -noui + aerender). Набор
собирается в N обычных .jsx + мастер-скрипт, который evalFile'ит каждый ролик в
СВОЁМ try/catch, собирает главные композиции из $.global.REELSI_COMPS в одну
очередь и сохраняет один .aep — дальше один aerender рендерит всю очередь.
Бины префиксуются стемом ролика (при одиночной сборке — пусто, .jsx прежний).
Одиночный набор обязан давать ровно сегодняшний .jsx.

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
from core.xml2ae.build import _write_master  # noqa: E402


@pytest.fixture()
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _single_jsx(xml_subs, out, **kw):
    kw.setdefault("roto", False)
    return xml2ae.to_ae_full(xml_subs, out, emit=lambda *a, **k: None, **kw)[0]


def test_master_has_evalfile_per_clip_in_own_try(xml_subs, tmp_path):
    """Мастер-скрипт: на наборе из двух роликов — два пути в массиве _files, evalFile
    каждого в своём try/catch (упавший не мешает остальным)."""
    jsx1 = str(tmp_path / "a.jsx")
    jsx2 = str(tmp_path / "b.jsx")
    open(jsx1, "w", encoding="utf-8-sig").write("var a=1;")
    open(jsx2, "w", encoding="utf-8-sig").write("var b=1;")
    master = str(tmp_path / "render_master.jsx")
    aep = str(tmp_path / "reelsi_batch.aep")
    _write_master([jsx1, jsx2], master, aep, str(tmp_path))
    txt = open(master, encoding="utf-8-sig").read()
    # в массиве _files — оба ролика
    assert txt.count("a.jsx") == 1 and txt.count("b.jsx") == 1, "в _files не оба ролика"
    # evalFile — внутри try/catch
    assert "try{" in txt and "evalFile ОШИБКА" in txt, "evalFile не в своём try/catch"


def test_master_builds_queue_and_saves_twice(xml_subs, tmp_path):
    """Мастер: очередь чистится, композиции добавляются из REELSI_COMPS, om.file —
    после applyTemplate, save — дважды (ДО и ПОСЛЕ очереди)."""
    master = str(tmp_path / "render_master.jsx")
    aep = str(tmp_path / "reelsi_batch.aep")
    _write_master([str(tmp_path / "a.jsx")], master, aep, str(tmp_path))
    txt = open(master, encoding="utf-8-sig").read()
    assert "while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();" in txt, "очередь не чистится"
    assert "rq0.items.add(_c)" in txt, "композиции не добавляются в очередь"
    assert "$.global.REELSI_COMPS" in txt, "мастер не читает собранные композиции"
    # om.file ПОСЛЕ applyTemplate (пресет несёт свой путь) — порядок строк
    i_om = txt.index("om.file = _out;")
    i_omt = txt.index('om.applyTemplate("Untitled 1")')
    i_bt = txt.index('rq.applyTemplate("Best Settings")')
    assert i_bt < i_omt < i_om, "om.file не ПОСЛЕ applyTemplate"
    # перезапись поверх: существующий .mov удаляется перед om.file (задание FK)
    assert 'if(_out.exists){ _out.remove();' in txt, "нет перезаписи существующего .mov"
    # save дважды
    assert txt.count("app.project.save(f)") == 2, "save не дважды (ДО и ПОСЛЕ очереди)"


def test_master_log_first_thing(xml_subs, tmp_path):
    """Мастер заводит лог ПЕРВЫМ делом (как безголовый хвост): его отсутствие у Python —
    «скрипт не запустился»."""
    master = str(tmp_path / "render_master.jsx")
    _write_master([str(tmp_path / "a.jsx")], master, str(tmp_path / "reelsi_batch.aep"), str(tmp_path))
    txt = open(master, encoding="utf-8-sig").read()
    assert "REELSI-MASTER: начат" in txt
    # лог открывается до первого evalFile
    assert txt.index("_log.open(\"w\")") < txt.index("$.evalFile("), "лог не первым делом"


def test_single_clip_jsx_byte_identical(xml_subs, tmp_path):
    """Набор из ОДНОГО ролика: собранный .jsx побайтово равен сегодняшнему (binpfx пуст,
    comps_global False — строка в $.global не вставляется)."""
    out_def = str(tmp_path / "def.jsx")
    out_batch = str(tmp_path / "batch.jsx")
    _single_jsx(xml_subs, out_def)
    _single_jsx(xml_subs, out_batch, binpfx="", comps_global=False)
    a = open(out_def, encoding="utf-8-sig").read()
    b = open(out_batch, encoding="utf-8-sig").read()
    assert a == b, "одиночный .jsx изменился — golden нарушен"
    assert "REELSI_COMPS" not in b, "comps_global=False вставил строку в глобальный массив"


def test_single_clip_jsx_unchanged_with_batch_params_default(xml_subs, tmp_path):
    """Те же аргументы по умолчанию (binpfx="", comps_global=False) — .jsx прежний."""
    out_def = str(tmp_path / "def.jsx")
    out_d = str(tmp_path / "d.jsx")
    _single_jsx(xml_subs, out_def)
    _single_jsx(xml_subs, out_d)
    assert open(out_def, encoding="utf-8-sig").read() == open(out_d, encoding="utf-8-sig").read()


def test_batch_bins_get_stem_prefix(xml_subs, tmp_path):
    """При наборе бины получают приставку стема: toBin зовёт bin(BIN_PFX+n), BIN_PFX
    объявлен. При одиночной сборке — прежний bin(n), BIN_PFX не объявлен вовсе."""
    out_single = str(tmp_path / "single.jsx")
    out_batch = str(tmp_path / "batch.jsx")
    _single_jsx(xml_subs, out_single)
    _single_jsx(xml_subs, out_batch, binpfx="01_тест — ")
    s = open(out_single, encoding="utf-8-sig").read()
    b = open(out_batch, encoding="utf-8-sig").read()
    assert "BIN_PFX" not in s, "одиночный .jsx получил префикс бинов"
    assert 'var BIN_PFX = "01_тест — ";' in b, "набор не объявил BIN_PFX со стемом"
    assert "bin(BIN_PFX+n)" in b, "toBin набора не зовёт bin(BIN_PFX+n)"
    assert "bin(n)" in s, "одиночная сборка изменила toBin"
