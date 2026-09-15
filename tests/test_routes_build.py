# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты трёх роутов api/build.py, которые до круга 7 не были покрыты (задание HX):

* `POST /api/cams_load` — раскладка камер по сегментам для окна-редактора;
* `POST /api/swap_cam`  — замена файла камеры (обычно второй) с пересборкой XML;
* `POST /api/export_drp` — таймлайн в `.drp` для DaVinci Resolve.

На каждый роут: плохой вход (пустое поле, несуществующий путь, чужой тип) и рабочий
путь на КОПИИ эталона `tests/fixtures/timeline_nosubs.xml` в tmp_path — проверяется
контракт ответа и побочные эффекты (сайдкар `project.json`, сам XML, мусор в %TEMP%).

Изоляция: медиа фикстуры (`<tmp_path>/footage/...`) на диске не лежит, поэтому ffprobe
(`xmlbuild.probe`) и синхрон камер (`sync.extract_audio`/`find_offset`) подменены —
реального ffmpeg, GPU и сети нет. Эталон не перегенерируется: он копируется.

Запуск (только новые файлы): py -3.10 -m pytest tests/test_routes_*.py -q -p no:cacheprovider
"""
import glob
import gzip
import json
import os
import shutil
import sys
import tempfile

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
    """Копия эталона: роуты пишут сайдкар И ПЕРЕСОБИРАЮТ XML рядом с файлом."""
    from core import xmlbuild
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    # Медиа — в путях ОС: пересборка пишет пути через pathurl = os.path.abspath, и на Linux
    # `C:/footage/…` из эталона превращался в `<cwd>/C:/footage/…` (публичный CI, круг 7).
    c1 = str(tmp_path / "footage" / "cam1" / "CLIP-030.MP4")
    c2 = str(tmp_path / "footage" / "cam2" / "CLIP-031.MP4")
    text = open(dst, encoding="utf-8").read()
    text = text.replace("file://localhost/C%3a/footage/cam1/CLIP-030.MP4", xmlbuild.pathurl(c1))
    text = text.replace("file://localhost/C%3a/footage/cam2/CLIP-031.MP4", xmlbuild.pathurl(c2))
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return dst


@pytest.fixture
def xml_subs(tmp_path):
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe вместо настоящего: у камер фикстуры нет файлов на диске."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "probe", lambda p, still_ok=True: {
        "width": 3840, "height": 2160, "dur_s": 600.0,
        "timecode": "00;00;00;00", "fps": 29.97})


@pytest.fixture
def fake_sync(monkeypatch):
    """Замена камеры не должна звать ffmpeg: аудио «извлекаем», сдвиг задаём числом."""
    from core import sync

    def extract(src, dst):
        with open(dst, "wb") as f:
            f.write(b"RIFF-fake")            # sync.find_offset подменён, читать нечего

    monkeypatch.setattr(sync, "extract_audio", extract)
    monkeypatch.setattr(sync, "find_offset", lambda a, b: (1.5, 0.8))


def _sidecar(xml):
    return os.path.splitext(xml)[0] + ".project.json"


def _cams_load(client, xml, **extra):
    payload = {"xml": xml}
    payload.update(extra)
    return client.post("/api/cams_load", json=payload, headers=H).get_json()


# --------------------------------------------------------------------------- #
# /api/cams_load
# --------------------------------------------------------------------------- #
def test_cams_load_contract(client, xml_nosubs):
    """Сегменты, тайминги монтажной ленты и раскладка сходятся между собой."""
    d = _cams_load(client, xml_nosubs)

    assert d["ok"] is True, d
    assert d["n"] == 2                                   # у фикстуры две камеры
    assert d["fps"] == 60
    assert len(d["names"]) == 2
    # имена — именно basename: фронт показывает их в селекторе раскладки
    assert all(os.path.basename(n) == n and n.lower().endswith(".mp4") for n in d["names"])
    assert d["manual"] is False                          # раскладки в проекте не было -> авто

    segs, assign = d["segs"], d["assign"]
    assert segs, "пустой список сегментов — контракт нарушен"
    assert len(assign) == len(segs)
    assert all(set(s) == {"start", "end", "dur", "tl"} for s in segs)
    assert segs[0]["tl"] == 0
    assert all(s["end"] > s["start"] and s["dur"] == round(s["end"] - s["start"], 3)
               for s in segs)
    # tl — курсор по монтажной ленте: он только растёт и равен сумме прежних длительностей
    for prev, cur in zip(segs, segs[1:]):
        assert cur["tl"] >= prev["tl"] + prev["dur"] - 0.001
    assert all(isinstance(a, int) and 0 <= a < d["n"] for a in assign)
    # total = длительность монтажа, а не исходников
    assert abs(d["total"] - (segs[-1]["tl"] + segs[-1]["dur"])) < 0.05

    # сайдкар реконструирован из XML (это документированное поведение _ensure_project),
    # но авто-раскладка в него НЕ пишется: cams_load только читает
    proj = json.load(open(_sidecar(xml_nosubs), encoding="utf-8"))
    assert len(proj["keep"]) == len(segs)
    assert len(proj["cams"]) == d["n"]
    assert "assign" not in proj


def test_cams_load_manual_assign_and_auto_flag(client, xml_nosubs):
    """Ручная раскладка из project.json возвращается как есть, а `auto` её игнорирует."""
    first = _cams_load(client, xml_nosubs)
    n = len(first["segs"])
    assert first["assign"] != [1] * n, "авто-раскладка вырождена — проверка ничего не докажет"

    p = _sidecar(xml_nosubs)
    proj = json.load(open(p, encoding="utf-8"))
    proj["assign"] = [1] * n                     # «всё камера 2» — заведомо не авто-раскладка
    with open(p, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)

    manual = _cams_load(client, xml_nosubs)
    assert manual["manual"] is True
    assert manual["assign"] == [1] * n           # сохранённое отдаём без пересчёта

    auto = _cams_load(client, xml_nosubs, auto=True)
    assert auto["manual"] is False
    assert auto["assign"] == first["assign"]     # auto=True — та же авто-раскладка
    # и сохранённая ручная раскладка не затёрта чтением
    assert json.load(open(p, encoding="utf-8"))["assign"] == [1] * n


@pytest.mark.parametrize("payload", [{}, {"xml": ""}, {"xml": "C:/нет-такого-файла.xml"}])
def test_cams_load_bad_input(client, payload):
    """Нет файла — понятная ошибка в формате umsg, а не 500 и не трейсбек."""
    r = client.post("/api/cams_load", json=payload, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True
    assert d["err"] == "file_not_found" and d["error"]
    assert d["err_vars"]["path"] == payload.get("xml", "")


# --------------------------------------------------------------------------- #
# /api/swap_cam
# --------------------------------------------------------------------------- #
def test_swap_cam_replaces_only_target_camera(client, xml_nosubs, tmp_path, fake_probe, fake_sync):
    """Меняется ровно камера k и её сдвиг: камера 1, нарезка и куски те же."""
    first = _cams_load(client, xml_nosubs)
    p = _sidecar(xml_nosubs)
    proj_before = json.load(open(p, encoding="utf-8"))
    keep_before = proj_before["keep"]
    cam0_before = proj_before["cams"][0]
    cam1_before = proj_before["cams"][1]
    xml_before = open(xml_nosubs, encoding="utf-8").read()

    new_cam = tmp_path / "cam2_new.mp4"
    new_cam.write_bytes(b"fake-camera-file")

    r = client.post("/api/swap_cam",
                    json={"xml": xml_nosubs, "cam": 1, "path": str(new_cam)}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True, d
    assert d["offset"] == 1.5 and d["conf"] == 0.8
    assert d["low_conf"] is False                # 0.8 >= порога 0.30
    assert d["name"] == "cam2_new.mp4"
    assert d["subs"] == 0 and d["yellow"] == 0   # в фикстуре нет ни субтитров, ни жёлтых

    proj_after = json.load(open(p, encoding="utf-8"))
    assert proj_after["cams"][0] == cam0_before, "камера 1 (звук) не должна меняться"
    assert proj_after["cams"][1] == str(new_cam)
    assert proj_after["cams"][1] != cam1_before
    assert proj_after["offsets"][1] == 1.5       # сдвиг новой камеры — из find_offset
    assert proj_after["offsets"][0] == proj_before["offsets"][0]
    assert proj_after["keep"] == keep_before     # нарезка не пересчитана
    assert len(proj_after["cams"]) == 2

    # XML пересобран под новую камеру (иначе своп виден только в сайдкаре)
    assert open(xml_nosubs, encoding="utf-8").read() != xml_before
    from core import xml2ae
    _meta, cams, subs, _ins = xml2ae.parse_full(xml_nosubs)
    assert len(cams) == 2 and subs == []
    assert os.path.normcase(cams[0]["path"]) == os.path.normcase(cam0_before)
    assert os.path.normcase(cams[1]["path"]) == os.path.normcase(str(new_cam))
    assert len(first["segs"]) == len(keep_before)


def test_swap_cam_bad_input(client, xml_nosubs, tmp_path, fake_probe, fake_sync):
    """Камера 1 неприкосновенна, чужого файла нет, XML не найден — всё это umsg."""
    d = client.post("/api/swap_cam", json={"xml": "C:/нет-такого.xml", "cam": 1,
                                           "path": "C:/нет.mp4"}, headers=H).get_json()
    assert d["err"] == "file_not_found"

    _cams_load(client, xml_nosubs)               # сайдкар проекта — как в бою
    proj_before = json.load(open(_sidecar(xml_nosubs), encoding="utf-8"))
    missing = str(tmp_path / "нет.mp4")
    d = client.post("/api/swap_cam", json={"xml": xml_nosubs, "cam": 1,
                                           "path": missing}, headers=H).get_json()
    assert d.get("ok") is not True and d["err"] == "cam_file_not_found"
    assert d["err_vars"]["path"] == missing

    existing = tmp_path / "real.mp4"
    existing.write_bytes(b"x")
    for cam in (0, 2, 99):                       # 0 — камера 1, 2.. — за диапазоном
        d = client.post("/api/swap_cam",
                        json={"xml": xml_nosubs, "cam": cam, "path": str(existing)},
                        headers=H).get_json()
        assert d.get("ok") is not True and d["err"] == "cam1_immutable", d
        assert d["err_vars"]["n"] == 2

    # ни одна из неудачных попыток не тронула проект и не подменила камеру
    proj = json.load(open(_sidecar(xml_nosubs), encoding="utf-8"))
    assert proj["cams"] == proj_before["cams"]
    assert proj["offsets"] == proj_before["offsets"]
    assert str(existing) not in proj["cams"]


# --------------------------------------------------------------------------- #
# /api/export_drp
# --------------------------------------------------------------------------- #
def test_export_drp_contract(client, xml_nosubs, tmp_path, fake_probe):
    """Ответ — zip-контейнер `.drp` вложением; временный файл за собой убран."""
    tmpdir = tempfile.gettempdir()
    drp_before = set(glob.glob(os.path.join(tmpdir, "*.drp")))

    pic = tmp_path / "pic.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    r = client.post("/api/export_drp", json={
        "xml": xml_nosubs,
        "inserts": [
            {"type": "photo", "media": str(pic), "start_sec": 2.0, "duration_sec": 2.0},
            # файла нет — вставка молча пропускается, сборка не падает
            {"type": "video", "media": str(tmp_path / "нет-такого.mp4"), "start_sec": 5.0},
        ]}, headers=H)

    assert r.status_code == 200
    assert r.mimetype == "application/octet-stream"
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and "timeline_nosubs.drp" in cd
    assert r.data[:2] == b"PK", "`.drp` — это zip-контейнер (docs/DRP_SPEC.md)"
    assert len(r.data) > 1000
    assert set(glob.glob(os.path.join(tmpdir, "*.drp"))) == drp_before, \
        "роут оставил временный .drp в %TEMP%"


def test_export_drp_bad_input(client, xml_nosubs, tmp_path, fake_probe):
    """Нет файла / нет камер в сайдкаре / кривой тайминг — JSON-ошибка, не 500."""
    d = client.post("/api/export_drp", json={"xml": "C:/нет-такого.xml"},
                    headers=H).get_json()
    assert d.get("ok") is not True and d["err"] == "file_not_found"

    with open(_sidecar(xml_nosubs), "w", encoding="utf-8") as f:
        json.dump({"cams": [], "keep": [], "fps": 60}, f)
    d = client.post("/api/export_drp", json={"xml": xml_nosubs}, headers=H).get_json()
    assert d.get("ok") is not True and d["err"] == "no_cams_sidebar"

    # кривой тайминг вставки ловится на месте (float("abc")), сборка не падает
    pic = tmp_path / "pic.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    os.remove(_sidecar(xml_nosubs))               # вернуть рабочий сайдкар из XML
    r = client.post("/api/export_drp", json={
        "xml": xml_nosubs,
        "inserts": [{"type": "photo", "media": str(pic), "start_sec": "вчера"}]},
        headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is not True and d["err"] == "export_drp_failed"


def test_export_drp_never_serves_secrets(client, tmp_path):
    """`ai_config.json` с ключами не отдаём даже как .drp — 403, как у /api/media."""
    secret = tmp_path / "ai_config.json"
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), str(secret))
    r = client.post("/api/export_drp", json={"xml": str(secret)}, headers=H)
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Чужой тип поля
# --------------------------------------------------------------------------- #
def test_wrong_type_xml_in_build_routes(client, xml_nosubs, tmp_path):
    """Число вместо строки в `xml` — ошибка запроса, а не внутренняя ошибка сервера."""
    bad = []
    for url in ("/api/cams_load", "/api/swap_cam", "/api/export_drp"):
        r = client.post(url, json={"xml": 123, "path": str(tmp_path / "x.mp4")}, headers=H)
        if r.status_code != 200 or r.get_json().get("err") != "file_not_found":
            bad.append((url, r.status_code, r.get_json()))
    assert bad == [], bad
