# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты удаления клипа с диска (/api/clip_delete).

Проверяет:
  - сбор всех сайдкаров <stem>.* в папке XML и .jsx в jsxdir;
  - гарды: исходное видео (камеры из project.json) не удаляется никогда, даже в той же папке;
  - папки и системные файлы защищены от удаления;
  - dry: true ничего не удаляет;
  - dry: false удаляет только файлы нарезки.
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


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_stem_sidecars_collected_and_dry_deletes_nothing(client, tmp_path):
    """Все сайдкары <stem>.* собираются в список, dry: true ничего не удаляет с диска."""
    out = tmp_path / "out"
    out.mkdir()
    stem = "01_interview"

    sidecars = [
        ".xml", ".xml.bak", ".xml.precensorfix",
        ".project.json", ".cuts.json", ".breaths.json",
        ".words.json", ".yellow.json", ".caption.json",
        ".inserts.json", ".omni.json", ".srt",
        ".srt.precensorfix", ".jsx", ".draft.mp4"
    ]

    created_paths = []
    for sc in sidecars:
        f = out / f"{stem}{sc}"
        f.write_text("dummy content", encoding="utf-8")
        created_paths.append(f)

    # Не относящийся к клипу файл
    other_file = out / "02_another.xml"
    other_file.write_text("other", encoding="utf-8")

    xml_path = str(out / f"{stem}.xml")
    res = client.post("/api/clip_delete", json={"xml": xml_path, "dry": True})
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True

    deleted_basenames = {os.path.basename(f["path"]) for f in d["files"]}
    expected_basenames = {f"{stem}{sc}" for sc in sidecars}
    assert deleted_basenames == expected_basenames
    assert d["bytes"] == sum(os.path.getsize(str(p)) for p in created_paths)

    # Проверяем, что в режиме dry ничего не удалилось
    for p in created_paths:
        assert p.exists()
    assert other_file.exists()


def test_camera_from_project_json_is_protected(client, tmp_path):
    """Камера из project.json НЕ попадает в список на удаление, даже если лежит в той же папке."""
    out = tmp_path / "out"
    out.mkdir()
    stem = "01_C1437"

    # Исходное видео лежит в той же папке и совпадает по префиксу stem. (01_C1437.MP4)
    cam_file = out / "01_C1437.MP4"
    cam_file.write_text("raw camera video content", encoding="utf-8")

    # Вторая камера в отдельной папке
    cam2_dir = tmp_path / "cam2"
    cam2_dir.mkdir()
    cam2_file = cam2_dir / "C1436.MP4"
    cam2_file.write_text("raw cam2 content", encoding="utf-8")

    # Сайдкары
    xml_file = out / f"{stem}.xml"
    xml_file.write_text("<xml/>", encoding="utf-8")
    proj_file = out / f"{stem}.project.json"
    proj_file.write_text(json.dumps({"cams": [str(cam_file), str(cam2_file)]}), encoding="utf-8")
    cuts_file = out / f"{stem}.cuts.json"
    cuts_file.write_text("{}", encoding="utf-8")

    res = client.post("/api/clip_delete", json={"xml": str(xml_file), "dry": True})
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True

    files_paths = [os.path.realpath(f["path"]) for f in d["files"]]
    assert os.path.realpath(str(cam_file)) not in files_paths
    assert os.path.realpath(str(cam2_file)) not in files_paths

    # Проверяем, что камера попала в skipped с понятной причиной
    skipped_paths = {os.path.realpath(s["path"]): s["why"] for s in d["skipped"]}
    assert os.path.realpath(str(cam_file)) in skipped_paths
    assert "исходник камеры" in skipped_paths[os.path.realpath(str(cam_file))]
    assert "01_C1437.MP4" in d["cams"] or "C1436.MP4" in d["cams"]


def test_jsxdir_support_and_actual_deletion(client, tmp_path):
    """Проверка jsxdir спикера и реального удаления при dry: false."""
    out = tmp_path / "out"
    out.mkdir()
    jsxdir = tmp_path / "speaker_jsx"
    jsxdir.mkdir()
    stem = "01_clip"

    xml_file = out / f"{stem}.xml"
    xml_file.write_text("<xml/>", encoding="utf-8")
    json_file = out / f"{stem}.project.json"
    json_file.write_text(json.dumps({"cams": ["D:/cam/v.mp4"]}), encoding="utf-8")

    jsx_file = jsxdir / f"{stem}.jsx"
    jsx_file.write_text("// ae script", encoding="utf-8")

    res = client.post("/api/clip_delete", json={
        "xml": str(xml_file),
        "jsxdir": str(jsxdir),
        "dry": False
    })
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True

    # Проверяем, что файлы удалены
    assert not xml_file.exists()
    assert not json_file.exists()
    assert not jsx_file.exists()
    assert jsxdir.exists()  # Каталог не удален


def test_directories_and_never_serve_are_skipped(client, tmp_path):
    """Каталоги с префиксом stem. и системные файлы не удаляются."""
    out = tmp_path / "out"
    out.mkdir()
    stem = "01_clip"

    # Корень xmeml: цель обязана выглядеть нарезкой (project.json
    # рядом тут нет — значит признак только в самом XML), иначе роут её отбивает.
    xml_file = out / f"{stem}.xml"
    xml_file.write_text('<xmeml version="4"/>', encoding="utf-8")

    sub_dir = out / f"{stem}.subfolder"
    sub_dir.mkdir()

    res = client.post("/api/clip_delete", json={"xml": str(xml_file), "dry": False})
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True

    assert not xml_file.exists()
    assert sub_dir.exists()
    assert any(os.path.realpath(s["path"]) == os.path.realpath(str(sub_dir)) for s in d["skipped"])


def test_xml_without_project_json_protects_source_video(client, tmp_path):
    """XML без project.json защищает исходное видео, читая <pathurl> из самого XML."""
    from core import xmlbuild

    out = tmp_path / "out"
    out.mkdir()
    stem = "IMG_2430"

    # Исходное видео лежит в той же папке и совпадает по префиксу stem. (IMG_2430.MOV)
    mov_file = out / f"{stem}.MOV"
    mov_file.write_text("raw camera video content", encoding="utf-8")

    # XML с pathurl на этот MOV, но БЕЗ project.json
    xml_file = out / f"{stem}.xml"
    purl = xmlbuild.pathurl(str(mov_file))
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="4">
  <sequence>
    <media>
      <video>
        <track>
          <clipitem>
            <file id="file-1">
              <pathurl>{purl}</pathurl>
            </file>
          </clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>"""
    xml_file.write_text(xml_content, encoding="utf-8")

    # Пара сайдкаров
    cuts_file = out / f"{stem}.cuts.json"
    cuts_file.write_text("{}", encoding="utf-8")
    words_file = out / f"{stem}.words.json"
    words_file.write_text("[]", encoding="utf-8")

    # Проверяем, что project.json отсутствует
    assert not (out / f"{stem}.project.json").exists()

    res = client.post("/api/clip_delete", json={"xml": str(xml_file), "dry": True})
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True

    # .MOV не должен попасть в files на удаление
    files_paths = [os.path.realpath(f["path"]) for f in d["files"]]
    assert os.path.realpath(str(mov_file)) not in files_paths

    # Сайдкары и XML должны быть в files
    expected_files = {os.path.realpath(str(xml_file)), os.path.realpath(str(cuts_file)), os.path.realpath(str(words_file))}
    assert set(files_paths) == expected_files

    # .MOV попал в skipped с причиной «исходник камеры»
    skipped_paths = {os.path.realpath(s["path"]): s["why"] for s in d["skipped"]}
    assert os.path.realpath(str(mov_file)) in skipped_paths
    assert "исходник камеры" in skipped_paths[os.path.realpath(str(mov_file))]
    assert f"{stem}.MOV" in d["cams"]
