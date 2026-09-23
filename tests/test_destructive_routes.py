# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Удаление только по своему пути: `/api/clip_delete` и `/api/clean_tmp`.

Внешнее ревью 2026-09-22 (P0-2, P0-3): `clip_delete` брал `xml` из тела и считал
stem от имени — по телу `{"xml": ".../notes.txt"}` сносились все `notes.*` рядом;
`clean_tmp` отдавал присланный `outdir` в `draftrender.clean_tmp`, а при `roto: true`
ещё и в `shutil.rmtree` по `roto/_cache` и по `<родитель outdir>/roto/_cache`.
Тесты держат обе двери с двух сторон: чужой путь — ошибка и ни одного удалённого
файла (в сухом прогоне тоже), своя нарезка и своя папка вывода работают как раньше.

Запуск:  python -m pytest tests/test_destructive_routes.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402

# Хост настоящего локального клиента: без него запрос отбивает защита от DNS
# rebinding (её тесты — в test_api_security.py), и до проверок дело не дойдёт.
H = {"Host": "127.0.0.1:5001"}

XML_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<xmeml version="4">\n  <sequence/>\n</xmeml>\n')


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _real_cut(tmp_path, stem="01_clip"):
    """Нарезка как на диске: XML с корнем xmeml, project.json, пара сайдкаров и
    ИСХОДНИК камеры — с тем же префиксом `stem.` в той же папке (роут обязан его
    пропустить, иначе проверка цели ничего не стоит)."""
    out = tmp_path / "Reelsi_out"
    out.mkdir()
    cam = out / f"{stem}.MP4"
    cam.write_bytes(b"raw camera video")
    xml = out / f"{stem}.xml"
    xml.write_text(XML_HEAD, encoding="utf-8")
    (out / f"{stem}.project.json").write_text(json.dumps({"cams": [str(cam)]}),
                                              encoding="utf-8")
    (out / f"{stem}.cuts.json").write_text("{}", encoding="utf-8")
    (out / f"{stem}.srt").write_text("1\n", encoding="utf-8")
    return out, xml, cam


# --------------------------------------------------------------------------- #
# 1. clip_delete: .txt — не нарезка, ничего не удаляется
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dry", [True, False])
def test_clip_delete_чужой_txt_остаётся_на_месте(client, tmp_path, dry):
    """Тело `{"xml": ".../notes.txt"}` — отказ; и сам notes.txt, и соседний
    notes.secret целы. Сухой прогон отбивается так же, как удаление: список «что
    было бы удалено» по чужому пути — тоже неверный ответ."""
    notes = tmp_path / "notes.txt"
    notes.write_text("чужие заметки", encoding="utf-8")
    secret = tmp_path / "notes.secret"
    secret.write_text("чужое рядом", encoding="utf-8")

    r = client.post("/api/clip_delete", json={"xml": str(notes), "dry": dry}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    # Сначала ущерб, потом форма ответа: без проверки цели роут сносит notes.secret
    # (сайдкар по префиксу `notes.`), и это должно быть видно в отчёте падения.
    assert notes.exists() and notes.read_text(encoding="utf-8") == "чужие заметки"
    assert secret.exists(), "соседний notes.secret удалён по чужому пути"
    assert d["err"] == "not_a_cut", d
    assert not d.get("files"), "по чужому пути выдан список файлов на удаление"


# --------------------------------------------------------------------------- #
# 2. clip_delete: .xml, который не нарезка — тоже отказ
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", ["<notes><item/></notes>\n", "это вообще не XML\n"])
def test_clip_delete_чужой_xml_остаётся_на_месте(client, tmp_path, body):
    """Чужой корень XML и мусор вместо XML: ни XML, ни сайдкар `notes.srt` не
    удаляются — признаков нарезки (project.json рядом или корня xmeml) нет."""
    out = tmp_path / "чужая_папка"
    out.mkdir()
    xml = out / "notes.xml"
    xml.write_text(body, encoding="utf-8")
    sidecar = out / "notes.srt"
    sidecar.write_text("1\n", encoding="utf-8")

    r = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert xml.exists() and sidecar.exists(), "чужой .xml и его notes.srt удалены"
    assert d["err"] == "not_a_cut", d


# --------------------------------------------------------------------------- #
# 3. clip_delete: своя нарезка — прежнее поведение
# --------------------------------------------------------------------------- #
def test_clip_delete_свою_нарезку_удаляет_как_раньше(client, tmp_path):
    """`dry: true` показывает сайдкары, `dry: false` их удаляет, а исходник камеры
    (лежит рядом и начинается на тот же префикс) остаётся на диске."""
    out, xml, cam = _real_cut(tmp_path)

    r = client.post("/api/clip_delete", json={"xml": str(xml), "dry": True}, headers=H)
    d = r.get_json()
    assert d["ok"] is True, d
    assert {os.path.basename(f["path"]) for f in d["files"]} == {
        "01_clip.xml", "01_clip.project.json", "01_clip.cuts.json", "01_clip.srt"}
    assert xml.exists(), "сухой прогон удалил файл"

    r = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    d = r.get_json()
    assert d["ok"] is True, d
    for name in ("01_clip.xml", "01_clip.project.json", "01_clip.cuts.json", "01_clip.srt"):
        assert not (out / name).exists(), f"{name} не удалён при dry: false"
    assert cam.exists(), "исходник камеры удалён вместе с нарезкой"


# --------------------------------------------------------------------------- #
# 4. clean_tmp: чужой каталог не чистится
# --------------------------------------------------------------------------- #
def test_clean_tmp_чужую_папку_не_чистит(client, tmp_path):
    """Папка без `*.project.json` — отказ: `_tmp` и `roto/_cache` целы, и кэш
    `<родитель outdir>/roto/_cache` тоже (он чистится только после проверки цели),
    `.draft.mp4` рядом не тронут, хотя запрос просит и roto, и drafts."""
    foreign = tmp_path / "foreign"
    deep = foreign / "_tmp" / "deep"
    deep.mkdir(parents=True)
    important = deep / "important.bin"
    important.write_bytes(b"important")
    mask = foreign / "roto" / "_cache" / "mask.png"
    mask.parent.mkdir(parents=True)
    mask.write_bytes(b"mask")
    parent_mask = tmp_path / "roto" / "_cache" / "parent_mask.png"
    parent_mask.parent.mkdir(parents=True)
    parent_mask.write_bytes(b"parent mask")
    draft = foreign / "01_clip.draft.mp4"
    draft.write_bytes(b"draft")

    r = client.post("/api/clean_tmp", json={"outdir": str(foreign), "roto": True,
                                           "drafts": True}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert important.exists() and mask.exists(), "чужая папка очищена (_tmp или roto/_cache)"
    assert parent_mask.exists() and draft.exists(), \
        "тронут кэш родителя или чужой .draft.mp4"
    assert d["err"] == "not_out_dir", d
    assert "freed_mb" not in d


# --------------------------------------------------------------------------- #
# 5. clean_tmp: своя папка вывода чистится как раньше
# --------------------------------------------------------------------------- #
def test_clean_tmp_свою_папку_вывода_чистит_как_раньше(client, tmp_path):
    """Папка с `*.project.json` — своя: `_tmp` сносится, `.draft.mp4` и
    `roto/_cache` удаляются, ответ — прежний `{ok, freed_mb}`."""
    out = tmp_path / "Reelsi_out"
    tmp = out / "_tmp"
    tmp.mkdir(parents=True)
    # Файлы по мегабайту: `freed_mb` округляется до десятых, на паре байт он равен
    # нулю и ничего не проверяет.
    junk = tmp / "draft.ass"
    junk.write_bytes(b"junk" * 250_000)
    (out / "01_clip.project.json").write_text("{}", encoding="utf-8")
    draft = out / "01_clip.draft.mp4"
    draft.write_bytes(b"draft" * 200_000)
    mask = out / "roto" / "_cache" / "mask.png"
    mask.parent.mkdir(parents=True)
    mask.write_bytes(b"mask" * 250_000)

    r = client.post("/api/clean_tmp", json={"outdir": str(out), "roto": True,
                                           "drafts": True}, headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True, d
    assert not tmp.exists() and not junk.exists()
    assert not draft.exists()
    assert not mask.exists()
    assert d["freed_mb"] > 0


# --------------------------------------------------------------------------- #
# 6. Общая проверка цели: папка вывода приложения и её подпапка — свои
# --------------------------------------------------------------------------- #
def test_is_reelsi_target_папка_вывода_и_её_подпапка(tmp_path, monkeypatch):
    """Ветка `app_out_dir`: своя папка вывода и её подпапка проходят даже пустыми
    (нарезок ещё нет), соседняя чужая папка — нет. `app_out_dir` подменяем: реальная
    папка вывода лежит рядом с рабочим материалом пользователя, и тест не должен её
    создавать."""
    own = tmp_path / "Reelsi_out"
    (own / "sub").mkdir(parents=True)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setattr("api._core.app_out_dir", lambda base: str(own))

    assert api._core.is_reelsi_target(str(own), "outdir") is True
    assert api._core.is_reelsi_target(str(own / "sub"), "outdir") is True
    assert api._core.is_reelsi_target(str(foreign), "outdir") is False
    assert api._core.is_reelsi_target(str(tmp_path / "нет_такой"), "outdir") is False
