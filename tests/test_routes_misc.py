# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты трёх «разных» роутов без тестов (круг 7):

* `POST /api/ai_inserts`  (api/ai.py)     — ИИ предлагает вставки по субтитрам XML;
* `GET  /api/tmp_info`    (api/jobs.py)   — что лежит в `<outdir>/_tmp` и в прокси;
* `POST /api/video_probe` (api/videogen.py) — что за файл по https-ссылке.

Тяжёлое подменено в точке вызова: LLM (`aicut.cmd_inserts`) и сеть
(`aicut.probe_media`) не вызываются вовсе — ни моделей, ни запросов наружу.
`emit` роута ai_inserts подменён, чтобы поток ИИ-лога не сыпался в общий JOB
(его читают другие тесты).

Запуск (только новые файлы): py -3.10 -m pytest tests/test_routes_*.py -q -p no:cacheprovider
"""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


@pytest.fixture
def no_model_unload(monkeypatch):
    """`_ai_end(unload=True)` не должен трогать LM Studio в тестах."""
    from core import aicut
    calls = []
    monkeypatch.setattr(aicut, "unload_ours", lambda *a, **k: calls.append(1))
    return calls


def _post(client, url, payload):
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _get(client, url, **kwargs):
    r = client.get(url, headers=H, **kwargs)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


# --------------------------------------------------------------------------- #
# /api/ai_inserts
# --------------------------------------------------------------------------- #
def test_ai_inserts_contract(client, xml_nosubs, monkeypatch, no_model_unload):
    """Ответ несёт набор вставок и цель набора; модель после вызова выгружена."""
    from core import aicut
    import api.ai as ai_route

    inserts = [{"type": "photo", "start_sec": 7.0, "duration_sec": 2.5, "query": "cat"},
               {"type": "video", "start_sec": 19.0, "duration_sec": 3.0, "query": "city"}]
    seen, emitted = {}, []

    def fake_cmd(xml_path, system=None, dry=False, model=None, url=None,
                 emit=None, count=None, avoid=None, rejected=None, window=None):
        seen.update(xml_path=xml_path, model=model, count=count,
                    avoid=avoid, rejected=rejected)
        emit("подбираю вставки…")
        emit("вставок: {total}, цель {target}", total=len(inserts), target=13)
        return {"path": xml_path + ".inserts.json", "inserts": inserts, "ins_target": 13}

    monkeypatch.setattr(aicut, "cmd_inserts", fake_cmd)
    monkeypatch.setattr(ai_route, "emit", lambda line="", **v: emitted.append((line, v)))

    avoid = [{"type": "photo", "start_sec": 3.0, "query": "dog"}]
    rejected = [{"type": "video", "start_sec": 11.0, "query": "car"}]
    # проверка до вызова: иначе чужой неотпущенный ИИ-вызов заставит _ai_begin ждать
    # AI_WAIT_SEC=25с, и тест «зависнет» вместо внятного падения
    assert api._core.AI_ACTIVE == 0, "предыдущий тест не отпустил AI_ACTIVE"
    d = _post(client, "/api/ai_inserts", {"xml": xml_nosubs, "model": "test-model",
                                          "count": 2, "avoid": avoid, "rejected": rejected})

    assert d["ok"] is True, d
    assert d["inserts"] == inserts
    assert d["insTarget"] == 13                       # цель набора — для кнопки «добрать»
    assert d["log"] == ["подбираю вставки…", "вставок: 2, цель 13"]
    assert seen == {"xml_path": xml_nosubs, "model": "test-model", "count": 2,
                    "avoid": avoid, "rejected": rejected}
    # строки ушли и в серверный лог (не только клиенту)
    assert [line for line, _v in emitted] == ["подбираю вставки…", "вставок: {total}, цель {target}"]
    assert emitted[1][1] == {"total": 2, "target": 13}
    assert no_model_unload == [1], "после успешного вызова модель обязана выгружаться"
    assert api._core.AI_ACTIVE == 0                   # счётчик одиночных ИИ-вызовов отпущен


def test_ai_inserts_llm_error_is_json(client, xml_nosubs, monkeypatch, no_model_unload):
    """Отказ модели/провайдера — umsg в ответе, модель не выгружаем, счётчик отпущен."""
    from core import aicut
    from core.umsg import umsg

    def boom(*a, **k):
        raise SystemExit(umsg("llm_down", "LM Studio недоступен"))

    monkeypatch.setattr(aicut, "cmd_inserts", boom)
    assert api._core.AI_ACTIVE == 0
    d = _post(client, "/api/ai_inserts", {"xml": xml_nosubs})
    assert d.get("ok") is not True
    assert d["err"] == "llm_down" and d["error"] == "LM Studio недоступен"
    assert no_model_unload == []
    assert api._core.AI_ACTIVE == 0


def test_ai_inserts_unexpected_error_is_json(client, xml_nosubs, monkeypatch, no_model_unload):
    """Любая другая ошибка внутри — тоже umsg (inserts_failed), а не 500."""
    from core import aicut

    def boom(*a, **k):
        raise RuntimeError("провайдер вернул мусор")

    monkeypatch.setattr(aicut, "cmd_inserts", boom)
    assert api._core.AI_ACTIVE == 0
    d = _post(client, "/api/ai_inserts", {"xml": xml_nosubs})
    assert d.get("ok") is not True
    assert d["err"] == "inserts_failed" and "провайдер вернул мусор" in d["error"]
    assert api._core.AI_ACTIVE == 0


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": "C:/нет-такого.xml"}])
def test_ai_inserts_bad_input(client, payload):
    """Нет XML — ошибка до всякого обращения к модели (её и не должно быть)."""
    d = _post(client, "/api/ai_inserts", payload)
    assert d.get("ok") is not True and d["err"] == "file_not_found"
    assert d["err_vars"]["path"] == payload.get("xml", "")


# --------------------------------------------------------------------------- #
# /api/tmp_info
# --------------------------------------------------------------------------- #
def test_tmp_info_counts_tmp_and_proxies(client, tmp_path):
    """total = всё в _tmp, proxy = только превью-прокси, other = остальное."""
    out = tmp_path / "out"
    tmp = out / "_tmp"
    (tmp / "sub").mkdir(parents=True)
    (tmp / "render.bin").write_bytes(b"\0" * 1_000_000)        # мусор
    (tmp / "pv_cam1.mp4").write_bytes(b"\0" * 400_000)         # превью-прокси
    (tmp / "sub" / "frame.bin").write_bytes(b"\0" * 200_000)   # мусор во вложенной папке

    d = _get(client, "/api/tmp_info", query_string={"outdir": str(out)})
    assert d == {"ok": True, "total_mb": 1.6, "proxy_mb": 0.4, "other_mb": 1.2}


def test_tmp_info_without_tmp_folder(client, tmp_path):
    """Папки `_tmp` нет — нули, а не ошибка: чистить нечего, но и пугать нечем."""
    out = tmp_path / "out"
    out.mkdir()
    d = _get(client, "/api/tmp_info", query_string={"outdir": str(out)})
    assert d == {"ok": True, "total_mb": 0.0, "proxy_mb": 0.0, "other_mb": 0.0}


@pytest.mark.parametrize("outdir", ["", "C:/нет-такой-папки"])
def test_tmp_info_bad_input(client, outdir):
    d = _get(client, "/api/tmp_info", query_string={"outdir": outdir})
    assert d.get("ok") is not True and d["err"] == "no_folder" and d["error"]
    assert d["err_vars"]["path"] == outdir


def test_tmp_info_file_instead_of_folder(client, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    d = _get(client, "/api/tmp_info", query_string={"outdir": str(f)})
    assert d.get("ok") is not True and d["err"] == "no_folder"


# --------------------------------------------------------------------------- #
# /api/video_probe
# --------------------------------------------------------------------------- #
def test_video_probe_contract(client, monkeypatch):
    """Ссылка уходит в probe как есть (без пробелов), ответ прокидывается целиком."""
    from core import aicut
    seen = {}

    def fake_probe(url, timeout=25):
        seen["url"] = url
        return {"kind": "video", "duration": 12.34, "width": 1920, "height": 1080,
                "codec": "h264", "ctype": "video/mp4", "size": 12345}

    monkeypatch.setattr(aicut, "probe_media", fake_probe)
    d = _post(client, "/api/video_probe", {"url": "  https://example.com/a.mp4  "})
    assert d == {"ok": True, "kind": "video", "duration": 12.34, "width": 1920,
                 "height": 1080, "codec": "h264", "ctype": "video/mp4", "size": 12345}
    assert seen["url"] == "https://example.com/a.mp4"


@pytest.mark.parametrize("kind,guess", [("video", True), ("image", False)])
def test_video_probe_unreadable_link_guesses_kind(client, monkeypatch, kind, guess):
    """ffprobe не открыл файл — тип берём из расширения и честно говорим ok:false."""
    from core import aicut
    monkeypatch.setattr(aicut, "probe_media", lambda url, timeout=25: {})
    monkeypatch.setattr(aicut, "is_video_url", lambda u: guess)
    d = _post(client, "/api/video_probe", {"url": "https://example.com/ref"})
    assert d["ok"] is False and d["kind"] == kind and "не удалось" in d["error"]


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "http://x/a.jpg"},
                                     {"url": "ftp://x/a.mp4"}, {"url": "C:/a.mp4"},
                                     {"url": 123}, {"url": None}, {"url": ["https://x"]}])
def test_video_probe_needs_https(client, monkeypatch, payload):
    """Не https — отказ до сети: probe_media не вызывается вовсе."""
    from core import aicut

    def boom(*a, **k):
        raise AssertionError("роут полез в сеть без https-ссылки")

    monkeypatch.setattr(aicut, "probe_media", boom)
    d = _post(client, "/api/video_probe", payload)
    assert d["ok"] is False and "https" in d["error"]


# --------------------------------------------------------------------------- #
# Чужой тип поля
# --------------------------------------------------------------------------- #
def test_wrong_type_xml_in_ai_inserts(client):
    r = client.post("/api/ai_inserts", json={"xml": 123}, headers=H)
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("err") == "file_not_found", r.get_json()


def test_ai_inserts_streams_log_into_job(client, xml_nosubs, monkeypatch):
    """Строки подбора уходят и клиенту (переведённые), и в общий JOB-лог (шаблоном):
    по JOB серверный прогресс стриминга виден в очереди, это документированное
    поведение роута. Свои строки из JOB за собой убираем — лог общий на процесс."""
    from core import aicut

    def fake_cmd(xml_path, **k):
        k["emit"]("строка {n}", n=1)
        return {"inserts": [], "ins_target": 0}

    monkeypatch.setattr(aicut, "cmd_inserts", fake_cmd)
    assert api._core.AI_ACTIVE == 0
    before = len(api._core.JOB["log"])
    try:
        d = _post(client, "/api/ai_inserts", {"xml": xml_nosubs})
        assert d["ok"] is True and d["log"] == ["строка 1"]        # клиенту — с подстановкой
        tail = api._core.JOB["log"][before:]
        assert [e["t"] if isinstance(e, dict) else e for e in tail] == ["строка {n}"]
        assert tail[0]["v"] == {"n": 1}                            # шаблон + переменные
    finally:
        with api._core.LOCK:
            del api._core.JOB["log"][before:]
