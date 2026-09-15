# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты роута /api/clear_subs (задание HV): удаляет yellow.json и пересобирает без sub_words, чужие файлы целы."""
import json
import pytest
from flask import Flask


@pytest.fixture
def client():
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_clear_subs_file_not_found(client, tmp_path):
    """Несуществующий XML возвращает ошибку file_not_found."""
    missing = tmp_path / "nonexistent.xml"
    r = client.post("/api/clear_subs", json={"xml": str(missing)}, headers={"Host": "127.0.0.1:5001"})
    d = r.get_json()
    assert d.get("ok") is not True
    assert "file_not_found" in str(d) or "error" in d


def test_clear_subs_removes_yellow_and_keeps_other_files(client, tmp_path, monkeypatch):
    """/api/clear_subs пересобирает XML с sub_words=None, удаляет .yellow.json и оставляет все остальные файлы нетронутыми."""
    from core import xmlbuild

    xml_path = tmp_path / "clip.xml"
    xml_path.write_text("<xmeml></xmeml>", encoding="utf-8")

    proj_path = tmp_path / "clip.project.json"
    proj_data = {
        "cams": [str(tmp_path / "cam1.mp4")],
        "offsets": [0.0],
        "keep": [[1.0, 5.0], [6.0, 10.0]],
        "scale": 50.4,
    }
    proj_path.write_text(json.dumps(proj_data), encoding="utf-8")

    yellow_path = tmp_path / "clip.yellow.json"
    yellow_path.write_text(json.dumps({"colored": [0, 1]}), encoding="utf-8")

    cuts_path = tmp_path / "clip.cuts.json"
    cuts_path.write_text(json.dumps({"cuts": []}), encoding="utf-8")

    other_path = tmp_path / "unrelated.txt"
    other_path.write_text("do not touch me", encoding="utf-8")

    build_calls = []

    def fake_build(cams, keep, offsets, xml, assign=None, scale=50.4, sub_words=None, music_path=None):
        build_calls.append({
            "cams": cams,
            "keep": keep,
            "sub_words": sub_words,
            "music_path": music_path,
        })
        return {"total_s": 8.0}

    monkeypatch.setattr(xmlbuild, "build", fake_build)

    r = client.post("/api/clear_subs", json={"xml": str(xml_path)}, headers={"Host": "127.0.0.1:5001"})
    d = r.get_json()

    assert d.get("ok") is True, f"Ответ clear_subs: {d}"
    assert d.get("segs") == 2
    assert d.get("dur") == 8.0

    # 1. Проверяем вызов xmlbuild.build: sub_words=None
    assert len(build_calls) == 1
    assert build_calls[0]["sub_words"] is None
    assert build_calls[0]["keep"] == [(1.0, 5.0), (6.0, 10.0)]

    # 2. .yellow.json удалён
    assert not yellow_path.exists(), ".yellow.json должен быть удалён"

    # 3. Чужие и соседние файлы не тронуты
    assert xml_path.exists(), "XML-файл должен остаться"
    assert proj_path.exists(), "project.json должен остаться"
    assert json.loads(proj_path.read_text(encoding="utf-8")) == proj_data
    assert cuts_path.exists(), "cuts.json должен остаться"
    assert other_path.exists(), "Посторонний файл должен остаться"
    assert other_path.read_text(encoding="utf-8") == "do not touch me"
