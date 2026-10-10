# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кнопка «Рассчитать рото и трекинг»: нечего считать — ни лока, ни потока (api/previewcalc.py).

Раньше сервер гнал расчёт и тогда, когда в стиле клипа выключены и рото, и слежение за
головой: полоса на кадре стояла на 0 %, видеокарта была занята впустую. Теперь решение
одно (`_calc_wanted`) и для быстрой двери (`wanted` в ответе), и для `build=true`.

1. `build=true` при выключенных рото и слежении — ошибка `calc_nothing`; поток не
   запущен, лок видеокарты не взят (`status_snapshot()["running"] is False`);
2. `cam1_head_follow=True` — расчёт стартует (поток заглушён, GPU не трогается);
3. `cam2_head_follow=True` при одной камере — нечего считать;
4. `cam2_head_follow=True` при двух камерах — расчёт стартует;
5. быстрая дверь (без `build`) отдаёт `wanted` тем же решением и ничего не запускает.

Запуск:  py -3.10 -m pytest tests/test_preview_calc_guard.py -q -p no:cacheprovider
"""
import gzip
import os
import shutil
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

XML_NAME = "timeline.xml"
# Все головные и рото-галки выключены: тест сам включает ровно ту, что проверяет.
OFF = {"roto": False, "cam1_head_follow": False, "cam2_head_follow": False}


@pytest.fixture()
def xml_clip(tmp_path):
    """Обезличенный XML фикстуры (две камеры): настоящий файл, который примет _norm_build_jobs."""
    dst = str(tmp_path / XML_NAME)
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture()
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_pcjob():
    """Состояние двери до и после теста: общий conftest его не сбрасывает."""
    from api import previewcalc
    previewcalc.reset_state()
    yield
    previewcalc.reset_state()


@pytest.fixture()
def calls(monkeypatch):
    """Лок видеокарты, предрасчёт плана и поток расчёта — заглушки: GPU не трогаем.

    `stub_run` повторяет `finally` настоящего расчёта: лок отпускает и `running` снимает,
    чтобы состояние двери после теста было чистым.
    """
    from api import previewcalc
    from core import xml2ae

    c = {"acquired": 0, "released": 0, "started": []}

    def acquire():
        c["acquired"] += 1
        return True

    def release():
        c["released"] += 1

    def stub_run(job):
        c["started"].append(job.get("style"))
        release()
        with previewcalc.PCLOCK:
            previewcalc.PCJOB.update(running=False, done=True)

    monkeypatch.setattr(previewcalc, "_cross_lock_acquire", acquire)
    monkeypatch.setattr(previewcalc, "_cross_lock_release", release)
    monkeypatch.setattr(previewcalc, "_run_preview_calc", stub_run)
    monkeypatch.setattr(xml2ae.precompute, "cached_plan",
                        lambda *a, **k: {"roto": [], "head": {"cams": [], "ready": False}})
    return c


def _body(xml, style, **extra):
    d = {"xml": xml, "style": style}
    d.update(extra)
    return d


def _wait_started(calls, timeout=10.0):
    """Дождаться, пока поток заглушки отметится (сам поток стартует асинхронно)."""
    end = time.time() + timeout
    while time.time() < end:
        if calls["started"]:
            return
        time.sleep(0.02)
    raise AssertionError("расчёт не стартовал за %.0f с" % timeout)


def test_nothing_to_count_is_an_error_and_takes_no_lock(client, xml_clip, calls, monkeypatch):
    """1. Рото и слежение выключены — `calc_nothing`; ни потока, ни лока, `running` снят."""
    from api import previewcalc
    threads = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: threads.append(self))
    d = client.post("/api/preview_calc", json=_body(xml_clip, OFF, build=True)).get_json()

    assert d.get("err") == "calc_nothing", d
    assert d.get("error"), d
    assert threads == [], "поток расчёта запущен, хотя считать нечего"
    assert calls["acquired"] == 0, "лок видеокарты взят, хотя считать нечего"
    assert previewcalc.status_snapshot()["running"] is False


def test_head_follow_cam1_starts_the_calc(client, xml_clip, calls):
    """2. `cam1_head_follow` включён — расчёт стартует и лок занят."""
    d = client.post("/api/preview_calc",
                    json=_body(xml_clip, dict(OFF, cam1_head_follow=True), build=True)).get_json()

    assert not d.get("error"), d
    assert d["wanted"] is True and d["building"] is True, d
    _wait_started(calls)
    assert calls["acquired"] == 1 and calls["released"] == 1


def test_head_follow_cam2_with_one_camera_is_nothing(client, xml_clip, calls):
    """3. `cam2_head_follow` при одной камере — нечего считать (как в сборке: cam2 не ставится)."""
    d = client.post("/api/preview_calc",
                    json=_body(xml_clip, dict(OFF, cam2_head_follow=True), cams=1, build=True)).get_json()

    assert d.get("err") == "calc_nothing", d
    assert calls["acquired"] == 0 and calls["started"] == []


def test_head_follow_cam2_with_two_cameras_starts_the_calc(client, xml_clip, calls):
    """4. `cam2_head_follow` при двух камерах в XML — расчёт стартует."""
    d = client.post("/api/preview_calc",
                    json=_body(xml_clip, dict(OFF, cam2_head_follow=True), cams=2, build=True)).get_json()

    assert not d.get("error"), d
    assert d["wanted"] is True and d["building"] is True, d
    _wait_started(calls)


def test_fast_door_answers_the_same_decision_and_starts_nothing(client, xml_clip, calls):
    """5. Быстрая дверь отдаёт `wanted` тем же решением; без `build` ничего не запускает."""
    off = client.post("/api/preview_calc", json=_body(xml_clip, OFF)).get_json()
    assert off.get("wanted") is False and off["building"] is False, off

    on = client.post("/api/preview_calc",
                     json=_body(xml_clip, dict(OFF, roto=True))).get_json()
    assert on.get("wanted") is True and on["building"] is False, on

    assert calls["acquired"] == 0 and calls["started"] == []
