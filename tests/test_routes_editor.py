# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты пяти «читающих» роутов api/editor.py (круг 7):

* `POST /api/xml_state`    — что уже размечено в XML (число субтитров, НЕ-белых слов, камер);
* `POST /api/omnicut_cuts` — журнал нарезки `<stem>.cuts.json`;
* `POST /api/editor_load`  — блоки нарезки для окна-редактора (сайдкар или реконструкция);
* `POST /api/aicut_preview`— виртуальный EDL для предпросмотра в браузере;
* `POST /api/scanxml`      — список .xml в папке.

Фикстуры: копии эталонов `timeline_nosubs.xml` (2 камеры, без субтитров) и
`timeline_subs.xml.gz` (253 слова-субтитра) — эталоны не перегенерируются. Всё пишется
в tmp_path, ни один путь не ведёт в рабочую копию.

Запуск (только новые файлы): py -3.10 -m pytest tests/test_routes_*.py -q -p no:cacheprovider
"""
import gzip
import json
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
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _post(client, url, payload):
    r = client.post(url, json=payload, headers=H)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.data[:200]!r}"
    return r.get_json()


def _sidecar(xml, suffix):
    return os.path.splitext(xml)[0] + suffix


# --------------------------------------------------------------------------- #
# /api/xml_state
# --------------------------------------------------------------------------- #
def test_xml_state_counts_words_and_colors(client, xml_subs):
    """subs/colored/ncams считаются по САМОМУ XML (цвет из Премьера), а не по сайдкару."""
    from core import xml2ae
    pristine = _post(client, "/api/xml_state", {"xml": xml_subs})
    assert pristine["ok"] is True
    assert pristine["subs"] == 253            # столько слов-субтитров в эталоне
    assert pristine["colored"] == 0           # в эталоне цветных слов нет
    assert pristine["ncams"] == 2

    # «Разметить всё» пропускает шаг по реальной разметке: покрасили два слова в XML...
    assert sorted(xml2ae.set_highlights(xml_subs, [4, 9])["colored"]) == [4, 9]
    colored = _post(client, "/api/xml_state", {"xml": xml_subs})
    assert colored["subs"] == 253
    assert colored["colored"] == 2
    assert colored["ncams"] == 2

    # ...а сайдкар .yellow.json тут НЕ считается (это только фолбэк /api/words)
    with open(_sidecar(xml_subs, ".yellow.json"), "w", encoding="utf-8") as f:
        json.dump({"yellow": [1, 2, 3, 4]}, f)
    assert _post(client, "/api/xml_state", {"xml": xml_subs})["colored"] == 2


def test_xml_state_without_subs(client, xml_nosubs):
    d = _post(client, "/api/xml_state", {"xml": xml_nosubs})
    assert d == {"ok": True, "subs": 0, "colored": 0, "ncams": 2}


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": "C:/нет-такого.xml"}])
def test_xml_state_bad_input(client, payload):
    d = _post(client, "/api/xml_state", payload)
    assert d.get("ok") is not True
    assert d["err"] == "file_not_found" and d["error"]
    assert d["err_vars"]["path"] == payload.get("xml", "")


# --------------------------------------------------------------------------- #
# /api/omnicut_cuts
# --------------------------------------------------------------------------- #
def test_omnicut_cuts_reads_sidecar(client, xml_nosubs):
    """Журнал нарезки отдаётся как есть; нет файла — пустой список, а не ошибка."""
    assert _post(client, "/api/omnicut_cuts", {"xml": xml_nosubs}) == {"ok": True, "cuts": []}

    cuts = [{"t0": 1.5, "t1": 3.0, "text": "лишняя пауза"}, {"t0": 7.0, "t1": 9.5, "text": ""}]
    with open(_sidecar(xml_nosubs, ".cuts.json"), "w", encoding="utf-8") as f:
        json.dump(cuts, f, ensure_ascii=False)
    assert _post(client, "/api/omnicut_cuts", {"xml": xml_nosubs}) == {"ok": True, "cuts": cuts}


@pytest.mark.parametrize("payload", [{}, {"xml": "C:/нет-такого.xml"}])
def test_omnicut_cuts_missing_file_is_empty(client, payload):
    """Роут не проверяет сам XML: нет пути — просто нет и журнала (контракт фронта)."""
    assert _post(client, "/api/omnicut_cuts", payload) == {"ok": True, "cuts": []}


def test_omnicut_cuts_broken_sidecar_is_json_error(client, xml_nosubs):
    """Рваный .cuts.json — umsg, а не 500: остальной редактор продолжает работать."""
    with open(_sidecar(xml_nosubs, ".cuts.json"), "w", encoding="utf-8") as f:
        f.write("{это не json")
    d = _post(client, "/api/omnicut_cuts", {"xml": xml_nosubs})
    assert d.get("ok") is not True
    assert d["err"] == "omnicut_cuts_failed" and d["error"]


# --------------------------------------------------------------------------- #
# /api/editor_load
# --------------------------------------------------------------------------- #
def test_editor_load_reconstructs_project_from_xml(client, xml_nosubs):
    """Без сайдкара проект реконструируется из XML: камера 1, fps и куски на месте."""
    d = _post(client, "/api/editor_load", {"xml": xml_nosubs})
    assert d["ok"] is True
    assert d["fps"] == 60 and d["have_proj"] is True
    assert isinstance(d["cam"], str) and d["cam"].lower().endswith(".mp4")
    keep = d["keep"]
    assert keep and all(len(seg) == 2 and seg[1] > seg[0] for seg in keep)
    # куски — из камеры 1: путь тот же, что записал сайдкар
    proj = json.load(open(_sidecar(xml_nosubs, ".project.json"), encoding="utf-8"))
    assert d["cam"] == proj["cams"][0]
    assert len(keep) == len(proj["keep"])


def test_editor_load_prefers_sidecar(client, xml_nosubs):
    """Есть сайдкар — отдаём ЕГО поля (fps/keep/cam), XML не перечитывается."""
    with open(_sidecar(xml_nosubs, ".project.json"), "w", encoding="utf-8") as f:
        json.dump({"cams": ["C:/cam/A.mp4", "C:/cam/B.mp4"], "offsets": [0.0, 0.0],
                   "fps": 25, "keep": [[1.0, 2.0], [3.0, 4.5]], "scale": 50.4}, f)
    d = _post(client, "/api/editor_load", {"xml": xml_nosubs})
    assert d == {"ok": True, "fps": 25, "cam": "C:/cam/A.mp4",
                 "keep": [[1.0, 2.0], [3.0, 4.5]], "have_proj": True}


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": "C:/нет-такого.xml"}])
def test_editor_load_bad_input(client, payload):
    d = _post(client, "/api/editor_load", payload)
    assert d.get("ok") is not True and d["err"] == "file_not_found"


def test_editor_load_broken_sidecar_is_json_error(client, xml_nosubs):
    """Сайдкар без камер — ошибка в формате umsg, а не необработанный KeyError."""
    with open(_sidecar(xml_nosubs, ".project.json"), "w", encoding="utf-8") as f:
        json.dump({"fps": 60, "keep": []}, f)
    d = _post(client, "/api/editor_load", {"xml": xml_nosubs})
    assert d.get("ok") is not True and d["err"] == "editor_load_failed" and d["error"]


# --------------------------------------------------------------------------- #
# /api/aicut_preview
# --------------------------------------------------------------------------- #
def test_aicut_preview_virtual_edl(client, xml_subs):
    """EDL описывает то, что видно и слышно: сегменты, звук камеры 1 и слова."""
    d = _post(client, "/api/aicut_preview", {"xml": xml_subs})
    assert d["ok"] is True
    assert d["fps"] == 60 and d["w"] == 1080 and d["h"] == 1920
    assert d["dur"] > 0
    assert [c["name"] for c in d["cams"]] and len(d["cams"]) == 2
    assert all(c["path"] for c in d["cams"])

    segs = d["segs"]
    assert segs and all(set(s) == {"ci", "ts", "te", "src"} for s in segs)
    assert all(0 <= s["ci"] < 2 and s["src"] >= 0 and s["te"] > s["ts"] for s in segs)
    assert segs[0]["ts"] == 0                          # монтаж начинается с нуля
    for prev, cur in zip(segs, segs[1:]):
        assert abs(cur["ts"] - prev["te"]) < 1e-6      # без дыр на таймлайне

    audio = d["audio"]
    assert audio and all(set(a) == {"ts", "te", "src"} for a in audio)
    assert audio[0]["ts"] == 0

    words = d["words"]
    assert len(words) == 253 and all(set(w) == {"s", "e", "w"} for w in words)
    assert all(w["e"] > w["s"] and isinstance(w["w"], str) and w["w"] for w in words)


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": "C:/нет-такого.xml"}])
def test_aicut_preview_bad_input(client, payload):
    d = _post(client, "/api/aicut_preview", payload)
    assert d.get("ok") is not True and d["err"] == "file_not_found"


# --------------------------------------------------------------------------- #
# /api/scanxml
# --------------------------------------------------------------------------- #
def test_scanxml_finds_only_xml_in_folder(client, tmp_path):
    """Ровно .xml из САМОЙ папки (регистр расширения не важен), вложенные — нет."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "b.xml").write_text("<x/>", encoding="utf-8")
    (out / "A.XML").write_text("<x/>", encoding="utf-8")
    (out / "notes.txt").write_text("не xml", encoding="utf-8")
    sub = out / "sub"
    sub.mkdir()
    (sub / "deep.xml").write_text("<x/>", encoding="utf-8")

    d = _post(client, "/api/scanxml", {"dir": str(out)})
    assert d["ok"] is True
    assert d["paths"] == sorted([str(out / "b.xml"), str(out / "A.XML")])
    assert all(os.path.isabs(p) for p in d["paths"])


def test_scanxml_empty_folder(client, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _post(client, "/api/scanxml", {"dir": str(empty)}) == {"ok": True, "paths": []}


@pytest.mark.parametrize("payload", [{}, {"dir": ""}, {"dir": "C:/нет-такой-папки"}])
def test_scanxml_bad_input(client, payload):
    d = _post(client, "/api/scanxml", payload)
    assert d.get("ok") is not True
    assert d["err"] == "no_folder" and d["error"]
    assert d["err_vars"]["path"] == payload.get("dir", "")


def test_scanxml_file_instead_of_folder(client, tmp_path):
    """Путь на файл — та же ошибка «нет папки», а не список из одного элемента."""
    f = tmp_path / "notes.txt"
    f.write_text("x", encoding="utf-8")
    d = _post(client, "/api/scanxml", {"dir": str(f)})
    assert d.get("ok") is not True and d["err"] == "no_folder"


# --------------------------------------------------------------------------- #
# Чужой тип поля
# --------------------------------------------------------------------------- #
def test_wrong_type_fields_in_editor_routes(client):
    bad = []
    for url, payload in (("/api/xml_state", {"xml": 123}),
                         ("/api/omnicut_cuts", {"xml": 123}),
                         ("/api/editor_load", {"xml": 123}),
                         ("/api/aicut_preview", {"xml": 123}),
                         ("/api/scanxml", {"dir": 123})):
        r = client.post(url, json=payload, headers=H)
        if url == "/api/omnicut_cuts":
            if r.status_code != 200 or r.get_json() != {"ok": True, "cuts": []}:
                bad.append((url, r.status_code, r.get_json()))
        else:
            if r.status_code != 200 or r.get_json().get("err") not in ("file_not_found", "no_folder"):
                bad.append((url, r.status_code, r.get_json()))
    assert bad == [], bad
