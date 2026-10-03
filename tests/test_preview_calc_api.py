# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пункт 2 задания: дверь расчёта рото и трекинга для превью (api/previewcalc.py).

Проверяется без GPU (предрасчёт подменён заглушками) и без настоящего лока видеокарты:

1. **быстрая дверь** `build` не задан — отвечает «что уже посчитано» по кэшам и НЕ
   запускает ни потока, ни расчёта (её дёргают при каждом открытии превью);
2. **задание ставится** `build=true` — идёт фоном, отдаёт прогресс (этап, проценты,
   i/n) и результат: список масок (путь, начала/концы, камера) + признак «трек готов»;
3. **«Стоп»** снимает задание: флаг доходит до расчёта (заглушка ждёт его, как
   `roto.alpha_for_ranges`), готовое остаётся;
4. **занятый лок** отвечает `busy`, второй расчёт поверх идущего не стартует;
5. **сбой старта потока** отпускает лок и снимает `running` (образец — api/render.py);
6. **прогресс рото** — i/n по кускам, которые считает сам предрасчёт (строка лога
   «рото: N кусков»), а не счёт по плану: тот давал 2 при 14 кусках;
7. **выгрузка RVM** после расчёта превью — одна, в общей функции предрасчёта.

Запуск:  python -m pytest tests/test_preview_calc_api.py -q -p no:cacheprovider
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

from core.xml2ae import precompute as _precompute  # noqa: E402

# Настоящий `roto_masks` до подмен фикстур: тест выгрузки RVM гоняет его самого.
_REAL_ROTO_MASKS = _precompute.roto_masks

os.environ.setdefault("REELSI_NO_BROWSER", "1")

XML_NAME = "timeline.xml"


@pytest.fixture()
def xml_clip(tmp_path):
    """Обезличенный XML фикстуры: настоящий файл, который примет _norm_build_jobs."""
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
    """Своё состояние двери до и после теста: PCJOB не сбрасывает общий conftest."""
    from api import previewcalc
    previewcalc.reset_state()
    with previewcalc.PCLOCK:
        previewcalc.PCJOB["log"] = []
    yield
    previewcalc.reset_state()


@pytest.fixture()
def no_gpu(monkeypatch):
    """Лок видеокарты и оба этапа предрасчёта — заглушки: ни RVM, ни трекера.

    `cached_plan` тоже подменён: настоящий считает план сцены, а он в этих тестах не
    проверяется (у него свой тест — tests/test_preview_calc_core.py).
    """
    from api import previewcalc
    from core import xml2ae

    calls = {"head": 0, "roto": 0, "plan": 0, "released": 0, "acquired": 0}

    monkeypatch.setattr(previewcalc, "_cross_lock_acquire",
                        lambda: (calls.__setitem__("acquired", calls["acquired"] + 1) or True))
    monkeypatch.setattr(previewcalc, "_cross_lock_release",
                        lambda: calls.__setitem__("released", calls["released"] + 1))
    monkeypatch.setattr(xml2ae.precompute, "cached_plan",
                        lambda *a, **k: {"roto": [], "head": {"cams": [], "ready": False}})
    monkeypatch.setattr(xml2ae, "scene_plan", lambda *a, **k: _fake_plan(calls))

    def fake_head(xml_path, style=None, **kw):
        calls["head"] += 1
        return {"cams": [1], "failed": {}}

    def fake_roto(plan, xml_path, kw, emit=None, cancel=None, strict=False):
        calls["roto"] += 1
        return [_entry()]

    monkeypatch.setattr(xml2ae.precompute, "head_track", fake_head)
    monkeypatch.setattr(xml2ae.precompute, "roto_masks", fake_roto)
    return calls


def _entry():
    """Запись маски — контракт `roto_masks` (как её видит превью)."""
    return {"ci": 0, "ts": 0.0, "te": 1.5, "cs": -2.0, "scale": 100, "mf": 1,
            "mask": "C:/base/roto/_cache/cam1/roto_abc.mp4",
            "path": "C:/footage/cam1.mp4", "src_start": 2.0, "src_end": 3.5}


def _fake_plan(calls):
    calls["plan"] += 1
    return {"cams": [{"path": "C:/footage/cam1.mp4"}],
            "roto": [{"ci": 0, "ts": 0.0, "te": 1.5, "src_start": 2.0, "src_end": 3.5,
                      "scale": 100}]}


def _wait_done(client, timeout=10.0):
    """Дождаться конца задания: поток ставится и завершается сам."""
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        last = client.get("/api/preview_calc_status").get_json()
        if not last.get("running"):
            return last
        time.sleep(0.02)
    raise AssertionError("задание не закончилось за %.0f с: %r" % (timeout, last))


def test_быстрая_дверь_не_запускает_расчёт(client, xml_clip, no_gpu, monkeypatch):
    """`build` не задан — только «что уже посчитано»: ни потока, ни расчёта.

    Поток здесь запрещён наотрез: если бы роут его всё-таки завёл (как было в первой
    версии — расчёт стартовал на каждое открытие превью), запрос упал бы 500.
    """
    def boom(self):
        raise RuntimeError("поток заводить нельзя")

    monkeypatch.setattr(threading.Thread, "start", boom)
    d = client.post("/api/preview_calc", json={"xml": xml_clip}).get_json()

    assert d.get("ok") is True and not d.get("error"), d
    assert d["building"] is False, "быстрая дверь пометила расчёт идущим"
    assert "roto" in d and "head" in d, "ответ не несёт «что уже посчитано»"
    assert no_gpu["head"] == 0 and no_gpu["roto"] == 0, \
        "быстрая дверь полезла в GPU-расчёт: head=%d roto=%d" % (no_gpu["head"], no_gpu["roto"])


def test_задание_ставится_идёт_и_отдаёт_результат(client, xml_clip, no_gpu):
    """`build=true` — фоновое задание, прогресс и результат (маски + «трек готов»)."""
    d = client.post("/api/preview_calc", json={"xml": xml_clip, "build": True}).get_json()
    assert d.get("ok") is True and not d.get("error"), d
    assert d["building"] is True, "задание не пометило себя идущим"

    st = _wait_done(client)
    assert st["done"] is True and st["running"] is False, st
    assert st["head"] == {"cams": [1], "ready": True}, st["head"]
    assert st["roto"] and st["roto"][0]["mask"], st["roto"]
    assert st["roto"][0]["ci"] == 0 and st["roto"][0]["ts"] == 0.0, st["roto"]
    assert no_gpu["head"] == 1 and no_gpu["roto"] == 1, no_gpu
    assert no_gpu["acquired"] == 1 and no_gpu["released"] == 1, \
        "задание не заняло/не отпустило лок видеокарты: %r" % (no_gpu,)


def test_стоп_снимает_задание(client, xml_clip, no_gpu, monkeypatch):
    """«Стоп» доходит до расчёта: заглушка ждёт флаг, как `alpha_for_ranges`."""
    from core import xml2ae
    seen = {"cancel": False}

    def fake_roto(plan, xml_path, kw, emit=None, cancel=None, strict=False):
        for _ in range(1000):
            if cancel is not None and cancel():
                seen["cancel"] = True
                return []
            time.sleep(0.01)
        return []

    monkeypatch.setattr(xml2ae.precompute, "roto_masks", fake_roto)
    client.post("/api/preview_calc", json={"xml": xml_clip, "build": True})
    r = client.post("/api/preview_calc_cancel")
    assert r.status_code == 200 and r.get_json().get("ok") is True

    st = _wait_done(client)
    assert seen["cancel"] is True, "флаг «Стоп» не дошёл до расчёта"
    assert st["running"] is False and st["done"] is True, st


def test_занятый_лок_отвечает_ошибкой(client, xml_clip, monkeypatch):
    """Лок видеокарты занят нарезкой/рендером — расчёт не стартует, причина видна."""
    from api import previewcalc
    from core import xml2ae

    monkeypatch.setattr(previewcalc, "_cross_lock_acquire", lambda: False)
    monkeypatch.setattr(xml2ae.precompute, "cached_plan",
                        lambda *a, **k: {"roto": [], "head": {"cams": [], "ready": False}})
    d = client.post("/api/preview_calc", json={"xml": xml_clip, "build": True}).get_json()

    assert d.get("error"), d
    with previewcalc.PCLOCK:
        assert previewcalc.PCJOB["running"] is False, "задание осталось идущим"


def test_нет_файла_отвечает_ошибкой(client):
    """Путь без файла — внятная ошибка, а не 500."""
    d = client.post("/api/preview_calc", json={"xml": "C:/no/such/clip.xml"}).get_json()
    assert d.get("error") and not d.get("ok"), d


def test_сбой_старта_потока_отпускает_лок(client, xml_clip, no_gpu, monkeypatch):
    """Поток не родился — лок отпускается, `running` снимается (иначе сервер встанет)."""
    from api import previewcalc

    def boom(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", boom)
    r = client.post("/api/preview_calc", json={"xml": xml_clip, "build": True})
    assert r.status_code == 500
    assert no_gpu["released"] == 1, "лок видеокарты остался занят после сбоя старта потока"
    with previewcalc.PCLOCK:
        assert previewcalc.PCJOB["running"] is False


def test_повторный_старт_поверх_идущего_не_запускает_второй(client, xml_clip, no_gpu,
                                                             monkeypatch):
    """Второй `build=true` поверх идущего не заводит второй RVM (видеокарта одна)."""
    from core import xml2ae
    release = threading.Event()

    def slow_roto(plan, xml_path, kw, emit=None, cancel=None, strict=False):
        release.wait(5)
        return [_entry()]

    monkeypatch.setattr(xml2ae.precompute, "roto_masks", slow_roto)
    first = client.post("/api/preview_calc", json={"xml": xml_clip, "build": True}).get_json()
    assert first["building"] is True
    second = client.post("/api/preview_calc", json={"xml": xml_clip, "build": True}).get_json()
    assert second["building"] is True, second
    assert no_gpu["acquired"] == 1, "второй расчёт занял лок поверх первого"
    release.set()
    _wait_done(client)


def test_прогресс_рото_идёт_по_кускам(client, xml_clip, no_gpu, monkeypatch):
    """Пункт 4: прогресс — i/n по кускам, которые считает сам предрасчёт.

    Знаменатель берётся из строки лога предрасчёта «рото: N кусков» (её пишет
    `roto_masks`): свой счёт по `plan["roto"]` врал — 2 при 14 кусках, и после первого
    куска полоса показывала 50 %, а на четырнадцатом — 100 %.
    """
    from api import previewcalc
    from core import xml2ae

    pct = []

    def fake_roto(plan, xml_path, kw, emit=None, cancel=None, strict=False):
        emit("  · рото: {chunks} кусков по {cams} камере(ам) — самый долгий этап сборки",
             chunks=3, cams=1)
        for i in range(3):
            emit("RVM (v1, cuda fp16, 960x540, seq=2) -> roto_%d.mp4" % i)
            pct.append(previewcalc.status_snapshot()["pct"])
        return [_entry()]

    monkeypatch.setattr(xml2ae.precompute, "roto_masks", fake_roto)
    client.post("/api/preview_calc", json={"xml": xml_clip, "build": True})
    st = _wait_done(client)

    assert pct == [33, 67, 100], \
        "прогресс рото идёт не по кускам (i из n): %r" % (pct,)
    assert st["n"] == 3 and st["i"] == 3, st


def test_расчёт_превью_выгружает_видеопамять(client, xml_clip, no_gpu, monkeypatch):
    """Пункт 5: RVM выгружается после расчёта превью — ровно один раз.

    Выгрузка живёт в общей функции предрасчёта (`precompute.roto_masks`): её получает и
    кнопка превью, и сборка. Живая проверка: после расчёта сервер держал ~2.4 ГБ —
    сборка выгружала модель, а превью нет. Здесь гоняется НАСТОЯЩИЙ `roto_masks`
    (заглушка из `no_gpu` снимается), а GPU-расчёт масок подменён.
    """
    from core import roto as _roto
    from core import xml2ae

    released = []

    def fake_release(emit=None):
        released.append(1)
        return True

    def fake_alpha(video, ranges, out_dir, **kw):
        os.makedirs(out_dir, exist_ok=True)
        out = []
        for (s, e) in ranges:
            mask = os.path.join(out_dir, "roto_%s_%s.mp4" % (s, e))
            with open(mask, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42")
            out.append({"start": float(s), "end": float(e), "mask": mask, "f": 1.0})
        return out

    monkeypatch.setattr(_roto, "release", fake_release)
    monkeypatch.setattr(_roto, "alpha_for_ranges", fake_alpha)
    monkeypatch.setattr(xml2ae.precompute, "roto_masks", _REAL_ROTO_MASKS)

    client.post("/api/preview_calc", json={"xml": xml_clip, "build": True})
    st = _wait_done(client)

    assert st["roto"], "превью не отдало ни одной маски: %r" % (st,)
    assert len(released) == 1, \
        "выгрузок RVM после расчёта превью: %d (ждали ровно одну)" % len(released)
