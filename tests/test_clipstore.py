# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты хранения снимков клипов и корзины (core/clipstore.py)."""
import json
import os
import sys
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from core import clipstore  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

H = {"Content-Type": "application/json"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _write_xml_cut(folder: Path, stem: str = "01_clip") -> tuple[Path, Path]:
    xml = folder / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")
    proj = folder / f"{stem}.project.json"
    proj.write_text(json.dumps({"cams": ["cam1.mp4"]}), encoding="utf-8")
    return xml, proj


def test_save_clips_and_load_clip(tmp_path):
    """save_clips пишет снимок; повторный вызов с тем же клипом — 0 записей; клип без XML — пропущен."""
    xml, _ = _write_xml_cut(tmp_path, "01_test")
    missing_xml = tmp_path / "missing.xml"

    clip1 = {
        "xml": str(xml),
        "name": "01_test.xml",
        "job": {"introRows": [{"count": 3, "color": "yellow"}], "highlights": [1, 2]},
        "inserts": [{"media": "C:/media/p1.png", "type": "photo", "start_sec": 2.0}],
    }
    clip_missing = {
        "xml": str(missing_xml),
        "name": "missing.xml",
        "job": {},
    }

    state = {"CLIPS": [clip1, clip_missing]}

    # Первая запись: 1 снимок записан (клип missing пропущен)
    saved = clipstore.save_clips(state)
    assert saved == 1

    cpath = clipstore.clip_path(str(xml))
    assert os.path.isfile(cpath)
    with open(cpath, encoding="utf-8") as f:
        data = json.load(f)
    assert data["v"] == 1
    assert "saved_at" in data
    assert data["clip"] == clip1

    # Повторный вызов с тем же объектом — 0 записей
    assert clipstore.save_clips(state) == 0

    # load_clip возвращает job.introRows и inserts[].media ровно как были сохранены
    loaded = clipstore.load_clip(str(xml))
    assert loaded is not None
    assert loaded["job"]["introRows"] == [{"count": 3, "color": "yellow"}]
    assert loaded["inserts"][0]["media"] == "C:/media/p1.png"

    # Несуществующий или битый файл -> None
    assert clipstore.load_clip(str(missing_xml)) is None
    with open(cpath, "w", encoding="utf-8") as f:
        f.write("{not a json")
    assert clipstore.load_clip(str(xml)) is None


def test_api_ui_state_creates_clip_json(client, tmp_path, monkeypatch):
    """POST /api/ui_state создаёт <стем>.clip.json."""
    xml, _ = _write_xml_cut(tmp_path, "02_state")
    ui_state_file = tmp_path / "ui_state.json"
    monkeypatch.setattr(api.files, "UI_STATE_PATH", str(ui_state_file))

    clip_obj = {
        "xml": str(xml),
        "name": "02_state.xml",
        "job": {"introRows": [{"count": 2, "back": True}]},
        "inserts": [{"media": "foo.jpg"}],
    }
    payload = {
        "state": {
            "CLIPS": [clip_obj],
        }
    }

    res = client.post("/api/ui_state", json=payload, headers=H)
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    cpath = clipstore.clip_path(str(xml))
    assert os.path.isfile(cpath)
    loaded = clipstore.load_clip(str(xml))
    assert loaded == clip_obj


def test_api_scanxml_with_snapshot_and_fallback(client, tmp_path):
    """/api/scanxml: со снимком — отдаёт clips; без снимка, но с .intro.json — отдаёт intro."""
    # Клип 1: со снимком <стем>.clip.json
    xml1, _ = _write_xml_cut(tmp_path, "01_snap")
    clip_snap = {
        "xml": str(xml1),
        "name": "01_snap.xml",
        "job": {"introRows": [{"count": 5}]},
        "inserts": [],
    }
    clipstore.save_clips({"CLIPS": [clip_snap]})

    # Клип 2: без снимка, но с .intro.json и .inserts.json
    xml2, _ = _write_xml_cut(tmp_path, "02_nosnap")
    (tmp_path / "02_nosnap.intro.json").write_text(
        json.dumps({"intro_rows": [{"count": 2, "color": "white"}]}), encoding="utf-8"
    )

    res = client.post("/api/scanxml", json={"dir": str(tmp_path)}, headers=H)
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True
    assert str(xml1) in d["clips"]
    assert d["clips"][str(xml1)]["job"]["introRows"] == [{"count": 5}]

    assert str(xml2) not in d["clips"]
    assert str(xml2) in d["intro"]
    assert d["intro"][str(xml2)]["intro_rows"] == [{"count": 2, "color": "white"}]


def test_clip_delete_to_trash_and_restore(client, tmp_path):
    """api_clip_delete dry:false — файлов на старом месте нет, они в _reelsi_trash/<id>/,
    исходник камеры на месте; trash_restore возвращает их; при занятом месте — skipped, файл не перезаписан.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stem = "03_del"
    xml, proj = _write_xml_cut(out_dir, stem)

    cam_file = out_dir / f"{stem}.MP4"
    cam_file.write_bytes(b"raw camera video")
    proj.write_text(json.dumps({"cams": [str(cam_file)]}), encoding="utf-8")

    cuts_file = out_dir / f"{stem}.cuts.json"
    cuts_file.write_text("{}", encoding="utf-8")

    clip_file = Path(clipstore.clip_path(str(xml)))
    clip_file.write_text("{}", encoding="utf-8")

    # dry: false -> переносит в корзину
    res = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True
    trash_id = d.get("trash")
    assert trash_id

    # Файлы нарезки исчезли из исходной папки
    assert not xml.exists()
    assert not cuts_file.exists()
    assert not clip_file.exists()
    # Исходник камеры остался нетронутым
    assert cam_file.exists()

    # Файлы находятся в _reelsi_trash/<id>/
    trash_folder = out_dir / clipstore.TRASH_DIR / trash_id
    assert trash_folder.is_dir()
    assert (trash_folder / f"{stem}.xml").exists()
    assert (trash_folder / f"{stem}.cuts.json").exists()
    assert (trash_folder / f"{stem}.clip.json").exists()

    # trash_list видит запись
    r_list = client.post("/api/trash_list", json={"dir": str(out_dir)}, headers=H)
    assert r_list.status_code == 200
    items = r_list.get_json().get("items", [])
    assert len(items) == 1
    assert items[0]["id"] == trash_id
    assert items[0]["stem"] == stem

    # 1. Восстановление: trash_restore
    r_rest = client.post("/api/trash_restore", json={"dir": str(out_dir), "id": trash_id}, headers=H)
    assert r_rest.status_code == 200
    d_rest = r_rest.get_json()
    assert d_rest["ok"] is True
    assert len(d_rest["restored"]) >= 3
    assert xml.exists()
    assert cuts_file.exists()
    assert clip_file.exists()
    # Папка корзины удалена после полного восстановления
    assert not trash_folder.exists()

    # 2. Проверка skipped: снова удаляем в корзину, создаём на месте один файл, восстанавливаем
    res2 = client.post("/api/clip_delete", json={"xml": str(xml), "dry": False}, headers=H)
    trash_id2 = res2.get_json()["trash"]
    assert not cuts_file.exists()
    # Создаём на исходном месте занятый cuts_file
    cuts_file.write_text("occupied content", encoding="utf-8")

    r_rest2 = client.post("/api/trash_restore", json={"dir": str(out_dir), "id": trash_id2}, headers=H)
    d_rest2 = r_rest2.get_json()
    assert d_rest2["ok"] is True
    # cuts_file попал в skipped и не перезаписан
    assert cuts_file.read_text(encoding="utf-8") == "occupied content"
    skipped_paths = [s["path"] for s in d_rest2["skipped"]]
    assert str(cuts_file) in skipped_paths


def test_restore_trash_with_traversal_fails(tmp_path):
    """restore_trash с id='../x' — ошибка, ничего не тронуто."""
    with pytest.raises(ReelsiError) as exc_info:
        clipstore.restore_trash(str(tmp_path), "../x")
    assert exc_info.value.code == "bad_trash_id"


def test_move_to_trash_занятый_файл_не_ломает_запись(tmp_path, monkeypatch):
    """Занятый файл остаётся на месте и уходит в skipped, остальные — в корзине,
    trash.json перечисляет ТОЛЬКО перенесённые, и list_trash запись видит.

    На Windows открытый на чтение файл держит дескриптор и os.replace по нему падает
    сам; на Linux такого запрета нет, поэтому перенос занятого файла подменяется
    ошибкой доступа — проверяем пофайловый разбор, а не поведение ОС.
    """
    stem = "04_busy"
    xml = tmp_path / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")
    (tmp_path / f"{stem}.project.json").write_text(json.dumps({"cams": ["cam1.mp4"]}), encoding="utf-8")

    good_files = []
    for suffix in (".cuts.json", ".voice.wav"):
        p = tmp_path / f"{stem}{suffix}"
        p.write_bytes(b"data " + suffix.encode("utf-8"))
        good_files.append(p)

    busy = tmp_path / f"{stem}.voice.wav"
    real_move = clipstore.move_file

    def fake_move(src, dst):
        # На Windows файл держит открытый дескриптор и падает сам; на Linux
        # подменяем отказ доступа, чтобы сценарий проверялся на обеих системах.
        if os.path.abspath(src) == os.path.abspath(str(busy)):
            raise PermissionError(f"файл занят: {src}")
        return real_move(src, dst)

    monkeypatch.setattr(clipstore, "move_file", fake_move)

    with open(busy, "rb"):
        trash_id, moved, not_moved = clipstore.move_to_trash(str(xml), [str(p) for p in [xml, *good_files]])

    assert trash_id
    assert [os.path.basename(p) for p in moved] == [f"{stem}.xml", f"{stem}.cuts.json"]
    assert busy.exists(), "занятый файл не должен уехать в корзину"
    assert [os.path.basename(r["path"]) for r in not_moved] == [busy.name]
    assert str(busy) in [r["path"] for r in not_moved]
    assert not_moved[0]["why"].strip(), "у неперенесённого файла нет причины"

    # trash.json есть и перечисляет ТОЛЬКО перенесённые файлы
    trash_folder = tmp_path / clipstore.TRASH_DIR / trash_id
    meta = json.loads((trash_folder / "trash.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in meta["files"]] == [f"{stem}.xml", f"{stem}.cuts.json"]

    # list_trash запись видит
    items = clipstore.list_trash(str(tmp_path))
    assert [i["id"] for i in items] == [trash_id]


def test_move_to_trash_ничего_не_перенесено_папка_убирается(tmp_path, monkeypatch):
    """Все файлы заняты — папки корзины не остаётся: пустая запись в списке не нужна."""
    stem = "05_allbusy"
    xml = tmp_path / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")
    busy = tmp_path / f"{stem}.voice.wav"
    busy.write_bytes(b"busy")

    def busy_move(src, dst):
        raise PermissionError(f"файл занят: {src}")

    monkeypatch.setattr(clipstore, "move_file", busy_move)

    with open(busy, "rb"):
        trash_id, moved, not_moved = clipstore.move_to_trash(str(xml), [str(busy)])

    assert trash_id == ""
    assert moved == []
    assert [r["path"] for r in not_moved] == [str(busy)]
    assert not (tmp_path / clipstore.TRASH_DIR).exists(), "пустая папка корзины осталась"
    assert clipstore.list_trash(str(tmp_path)) == []


def test_restore_trash_имя_из_метаданных_только_простое(tmp_path):
    """Имя из trash.json с разделителями (../../x) не принимается: иначе запись
    читает и переносит файл ВНЕ корзины. Остальные записи восстанавливаются."""
    stem = "06_escape"
    xml = tmp_path / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")

    trash_id = "20260101-120000_" + stem
    trash_folder = tmp_path / clipstore.TRASH_DIR / trash_id
    trash_folder.mkdir(parents=True)

    # В корзине — файл, до которого запись с выходом наружу добралась бы копированием:
    # ../../секрет.txt от папки корзины это он и есть
    outside = tmp_path / "секрет.txt"
    outside.write_text("чужое", encoding="utf-8")

    (trash_folder / f"{stem}.cuts.json").write_bytes(b"{}")
    meta = {
        "xml": str(xml),
        "deleted_at": "2026-01-01T12:00:00+00:00",
        "files": [
            {"from": str(tmp_path / f"{stem}.cuts.json"), "name": f"{stem}.cuts.json"},
            {"from": str(tmp_path / "не-трогать.txt"), "name": "../../секрет.txt"},
        ],
    }
    (trash_folder / "trash.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    res = clipstore.restore_trash(str(tmp_path), trash_id)

    assert res["restored"] == [str(tmp_path / f"{stem}.cuts.json")]
    reasons = {s["path"]: s["why"] for s in res["skipped"]}
    assert str(tmp_path / "не-трогать.txt") in reasons, "запись с именем наружу не попала в skipped"
    assert outside.read_text(encoding="utf-8") == "чужое", "файл из-за корзины унесён"
    assert not (tmp_path / "не-трогать.txt").exists(), "восстановление сработало по записи наружу"
    assert not (trash_folder / f"{stem}.cuts.json").exists(), "восстановленный файл остался в корзине"
