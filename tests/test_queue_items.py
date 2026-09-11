# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общая пофайловая очередь этапов (задание FA) — помощники из api/_core.py.

Одна механика на нарезку, сборку .jsx и рендер (JOB и RJOB): у джоба есть список
items — по одному элементу на файл набора. «Готово/ошибка» решается ровно одним
местом — item_done/item_fail, которые и кладут результат/падение в bucket, и
переводят элемент в done/error. Проверяем сами помощники и главный инвариант на
рендере: список и счётчик «Готово: N» не должны разъехаться.

Запуск:  python -m pytest reelsi/tests -q
"""
import sys
import threading
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api._core as core  # noqa: E402
import api.render as render  # noqa: E402


def _job():
    """Свежий джоб-словарь в форме, в какой его держит сервер."""
    return {"results": [], "failed": [], "items": []}


def _by_name(job, name):
    return next(it for it in job["items"] if it["name"] == name)


def test_items_init_orders_by_set_and_all_wait():
    job = _job()
    core.items_init(job, threading.Lock(), ["01_a", "02_b", "03_c"])
    assert [it["name"] for it in job["items"]] == ["01_a", "02_b", "03_c"]
    assert all(it["stage"] == "wait" for it in job["items"])
    for it in job["items"]:
        assert it.get("pct") is None and it.get("path") == "" and it.get("reason") == ""


def test_item_done_one_call_two_writes_bucket_and_stage():
    """item_done кладёт путь в bucket И переводит элемент в done — один вызов."""
    job = _job()
    core.items_init(job, threading.Lock(), ["01_a", "02_b"])
    core.item_done(job, threading.Lock(), "01_a", "out/01_a.xml")  # bucket="results" по умолчанию
    assert job["results"] == ["out/01_a.xml"]
    assert _by_name(job, "01_a")["stage"] == "done"
    assert _by_name(job, "01_a")["path"] == "out/01_a.xml"
    # соседний файл не тронут
    assert _by_name(job, "02_b")["stage"] == "wait"


def test_item_done_honors_render_bucket():
    """У RJOB список готовых называется result, а не results — переименовывать нельзя."""
    job = _job()
    job["result"] = []
    core.items_init(job, threading.Lock(), ["01"])
    core.item_done(job, threading.Lock(), "01", "exp/01.mov", bucket="result")
    assert job["result"] == ["exp/01.mov"]
    assert "results" not in job or job["results"] == []
    assert _by_name(job, "01")["stage"] == "done"


def test_item_fail_one_call_two_writes_bucket_and_stage():
    """item_fail кладёт {"name","reason"} в bucket И переводит элемент в error."""
    job = _job()
    core.items_init(job, threading.Lock(), ["01"])
    core.item_fail(job, threading.Lock(), "01", "взорвалось", bucket="failed")
    assert job["failed"] == [{"name": "01", "reason": "взорвалось"}]
    it = _by_name(job, "01")
    assert it["stage"] == "error" and it["reason"] == "взорвалось"


def test_item_done_unknown_name_still_writes_bucket():
    """item_done с именем, которого нет в items, всё равно кладёт путь в bucket.
    Запись результата — ВСЕГДА: готовый файл не должен пропасть из results, даже
    если элемент очереди по имени не найден (элемента нет — не обновляем его)."""
    job = _job()
    core.items_init(job, threading.Lock(), ["01"])
    core.item_done(job, threading.Lock(), "нет_такого", "out/ghost.xml")
    assert job["results"] == ["out/ghost.xml"]
    assert _by_name(job, "01")["stage"] == "wait"  # соседний элемент не тронут


def test_render_invariant_counts_match_buckets():
    """Инвариант на рендере (задание FA): после прогона число элементов в done равно
    длине RJOB["result"], число error — длине RJOB["failed"] за вычетом глобальных
    записей БЕЗ клипа (AE не найден, внутренняя ошибка рендера)."""
    render.RJOB.update(running=False, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=False, items=[])
    stems = ["01", "02", "03", "04", "05"]
    render.items_init(render.RJOB, render.RLOCK, stems)
    # прогон: 01 и 03 отрендерились, 02 и 04 упали поклипово, 05 не дошёл («Стоп»)
    render.item_done(render.RJOB, render.RLOCK, "01", "exp/01.mov", bucket="result")
    render.item_fail(render.RJOB, render.RLOCK, "02", "aerender rc=1", bucket="failed")
    render.item_done(render.RJOB, render.RLOCK, "03", "exp/03.mov", bucket="result")
    render.item_fail(render.RJOB, render.RLOCK, "04", "файла нет на диске", bucket="failed")
    render.item_set(render.RJOB, render.RLOCK, "05", stage="stopped")
    # глобальная запись БЕЗ клипа оставляется прямой, как в _run_render_job
    render.RJOB["failed"].append({"name": "AE", "reason": "After Effects не найден"})

    done = [it for it in render.RJOB["items"] if it["stage"] == "done"]
    err = [it for it in render.RJOB["items"] if it["stage"] == "error"]
    globals_ = [f for f in render.RJOB["failed"] if f["name"] not in stems]
    assert len(done) == len(render.RJOB["result"]), "done-элементы и result разошлись"
    assert len(err) == len(render.RJOB["failed"]) - len(globals_), (
        "error-элементы и поклиповые failed разошлись")


def test_render_mark_stopped_waits_flips_wait_not_done():
    """«Стоп» на рендере (задание FA): _mark_stopped_waits оставляет done как есть,
    а все, кто ещё в wait, помечает stopped, чтобы очередь не показывала их «в очереди»."""
    render.RJOB.update(running=False, done=False, log=[], pct=None, cur="", ae="",
                       out_dir="", result=[], failed=[], cancel=True, items=[])
    stems = ["01", "02", "03", "04", "05"]
    render.items_init(render.RJOB, render.RLOCK, stems)
    render.item_done(render.RJOB, render.RLOCK, "03", "exp/03.mov", bucket="result")
    render._mark_stopped_waits()
    assert _by_name(render.RJOB, "03")["stage"] == "done"      # готовый не трогаем
    rest = [it for it in render.RJOB["items"] if it["name"] != "03"]
    assert len(rest) == 4
    assert all(it["stage"] == "stopped" for it in rest)


def test_item_set_on_unknown_name_is_silent():
    """item_set на несуществующем имени ведёт себя тихо (нет такого — молча выйти)."""
    job = _job()
    core.items_init(job, threading.Lock(), ["01"])
    core.item_set(job, threading.Lock(), "нет_такого", stage="cut")  # не должно падать
    assert _by_name(job, "01")["stage"] == "wait"
    assert len(job["items"]) == 1
