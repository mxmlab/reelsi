# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Единая корзина: клип с исходниками, общие исходники, подкасты в той же корзине, очистка.

Всё на tmp_path. Роуты: /api/clip_delete, /api/podcast_delete, /api/trash_list,
/api/trash_restore, /api/trash_empty; логика — core/clipstore.py.
"""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import clipstore, podcast  # noqa: E402

H = {"Content-Type": "application/json"}
TRASH = clipstore.TRASH_DIR


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, url, body):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == 200
    return r.get_json()


def _cut(folder: Path, stem: str, cams: list[Path]) -> Path:
    """Нарезка Reelsi: XML (xmeml) и сайдкар project.json со списком камер."""
    xml = folder / f"{stem}.xml"
    xml.write_text('<xmeml version="4"><sequence/></xmeml>', encoding="utf-8")
    (folder / f"{stem}.project.json").write_text(
        json.dumps({"cams": [str(c) for c in cams]}), encoding="utf-8")
    (folder / f"{stem}.cuts.json").write_text("{}", encoding="utf-8")
    return xml


def _pod(root: Path, name: str, title: str, source: str = "") -> Path:
    folder = root / name
    podcast.save_project(str(folder), podcast.new_project(title, "", source, 3))
    return folder


# --- клипы: исходники в корзину ---

def test_клип_с_исходником_уходит_в_корзину_и_возвращается(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cams = tmp_path / "камера1"
    cams.mkdir()
    cam = cams / "C1437.MP4"
    cam.write_bytes(b"camera")
    xml = _cut(out, "01_a", [cam])

    d = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})
    assert d["ok"] is True
    tid = d["trash"]
    assert not cam.exists()
    stored = cams / TRASH / tid / "C1437.MP4"
    assert stored.read_bytes() == b"camera"

    meta = json.loads((out / TRASH / tid / "trash.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "clip"
    rec = next(r for r in meta["files"] if "stored" in r)
    assert rec["from"] == os.path.abspath(str(cam))
    assert rec["stored"] == os.path.abspath(str(stored))
    assert rec["name"] == "C1437.MP4"

    # список: запись видна клипом, с одним исходником
    items = _post(client, "/api/trash_list", {"dir": str(out)})["items"]
    assert [i["id"] for i in items] == [tid]
    assert items[0]["kind"] == "clip" and items[0]["stem"] == "01_a"
    assert items[0]["sources"] == 1 and items[0]["root"] == str(out)

    r = _post(client, "/api/trash_restore", {"root": str(out), "id": tid})
    assert r["ok"] is True and r["skipped"] == []
    assert cam.read_bytes() == b"camera"
    assert xml.exists()
    assert not (cams / TRASH).exists()          # пустая корзина у камеры убрана
    assert not (out / TRASH).exists()


def test_камера_рядом_с_нарезкой_в_той_же_корзине(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cam = out / "01_a.MP4"
    cam.write_bytes(b"x")
    xml = _cut(out, "01_a", [cam])

    tid = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})["trash"]
    assert (out / TRASH / tid / "01_a.MP4").read_bytes() == b"x"
    assert (out / TRASH / tid / "trash.json").exists()

    assert _post(client, "/api/trash_restore", {"root": str(out), "id": tid})["ok"] is True
    assert cam.read_bytes() == b"x"
    assert not (out / TRASH).exists()


def test_занятый_исходник_при_возврате_остаётся_в_своей_записи(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cams = tmp_path / "камера1"
    cams.mkdir()
    cam = cams / "C1437.MP4"
    cam.write_bytes(b"camera")
    xml = _cut(out, "01_a", [cam])
    tid = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})["trash"]

    cam.write_bytes(b"new")                      # на старом месте уже есть файл
    r = _post(client, "/api/trash_restore", {"root": str(out), "id": tid})
    assert any(s["path"] == str(cam) for s in r["skipped"])
    assert cam.read_bytes() == b"new"
    # запись не потеряна: исходник по-прежнему в корзине камеры, и очистка его найдёт
    assert (cams / TRASH / tid / "C1437.MP4").read_bytes() == b"camera"
    assert (out / TRASH / tid / "trash.json").exists()
    real = _post(client, "/api/trash_empty", {"dir": str(out), "dry": False})
    assert real["removed"] == 1 and real["failed"] == []
    assert not (cams / TRASH).exists()


def test_общий_исходник_не_уносится_пока_им_пользуется_другой_клип(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cam = tmp_path / "cam" / "C1.MP4"
    cam.parent.mkdir()
    cam.write_bytes(b"shared")
    x1 = _cut(out, "01_a", [cam])
    _cut(out, "02_b", [cam])

    dry = _post(client, "/api/clip_delete", {"xml": str(x1), "dry": True, "sources": True})
    src = dry["sources"][0]
    assert src["path"] == str(cam) and src["shared_with"] == ["02_b"]
    assert all(f["path"] != str(cam) for f in dry["files"])
    assert any(s["path"] == str(cam) and "02_b" in s["why"] for s in dry["skipped"])

    real = _post(client, "/api/clip_delete", {"xml": str(x1), "dry": False, "sources": True})
    assert cam.read_bytes() == b"shared"        # второй клип ссылается — камера на месте
    assert any(s["path"] == str(cam) for s in real["skipped"])
    assert not (cam.parent / TRASH).exists()


def test_общий_исходник_уходит_если_второй_клип_в_той_же_пачке(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cam = tmp_path / "cam" / "C1.MP4"
    cam.parent.mkdir()
    cam.write_bytes(b"shared")
    x1 = _cut(out, "01_a", [cam])
    x2 = _cut(out, "02_b", [cam])

    body = {"xml": str(x1), "dry": True, "sources": True, "batch": [str(x1), str(x2)]}
    dry = _post(client, "/api/clip_delete", body)
    assert dry["sources"][0]["shared_with"] == []
    assert any(f["path"] == str(cam) for f in dry["files"])

    body["dry"] = False
    real = _post(client, "/api/clip_delete", body)
    assert real["ok"] is True
    assert not cam.exists()
    assert (cam.parent / TRASH / real["trash"] / "C1.MP4").read_bytes() == b"shared"


def test_без_sources_камера_не_тронута(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cam = tmp_path / "cam" / "C1.MP4"
    cam.parent.mkdir()
    cam.write_bytes(b"raw")
    xml = _cut(out, "01_a", [cam])

    d = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False})
    assert d["ok"] is True and not xml.exists()
    assert cam.read_bytes() == b"raw"
    assert d["sources"] == [{"path": str(cam), "size": 3, "shared_with": []}]
    assert not (cam.parent / TRASH).exists()


# --- рилс-исходник: его соседи (план рамки и sendcmd) уходят и возвращаются вместе с ним ---

def _reel_with_sidecars(folder: Path, stem: str) -> tuple[Path, Path, Path]:
    """Рилс `<stem>.mp4` и его соседи, как пишет core/podcast_reframe.convert_segment."""
    folder.mkdir(parents=True, exist_ok=True)
    reel = folder / f"{stem}.mp4"
    reel.write_bytes(b"reel")
    js = folder / f"{stem}.reframe.json"
    js.write_text('{"plan": []}', encoding="utf-8")
    crop = folder / f".{stem}.crop.txt"
    crop.write_text("0 crop", encoding="utf-8")
    return reel, js, crop


def test_рилс_с_соседями_уходит_с_клипом_при_sources(client, tmp_path):
    """sources: true — mp4 рилса и оба его соседа в корзине; dry показывает соседей в списке."""
    out = tmp_path / "out"
    out.mkdir()
    reels = tmp_path / "камера1"
    reel, js, crop = _reel_with_sidecars(reels, "vypusk_01")
    xml = _cut(out, "01_a", [reel])

    dry = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": True, "sources": True})
    dry_paths = {f["path"] for f in dry["files"]}
    assert str(js) in dry_paths and str(crop) in dry_paths, "соседи рилса не видны в сухом прогоне"
    assert reel.exists() and js.exists() and crop.exists()     # сухой прогон ничего не тронул

    d = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})
    tid = d["trash"]
    assert not reel.exists() and not js.exists() and not crop.exists()
    stored = reels / TRASH / tid
    assert (stored / js.name).read_text(encoding="utf-8") == '{"plan": []}'
    assert (stored / crop.name).read_text(encoding="utf-8") == "0 crop"
    meta = json.loads((out / TRASH / tid / "trash.json").read_text(encoding="utf-8"))
    stored_recs = {r["name"] for r in meta["files"] if "stored" in r}
    assert {reel.name, js.name, crop.name} <= stored_recs     # все три — записи с stored


def test_возврат_рилса_возвращает_соседей_на_место(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    reels = tmp_path / "камера1"
    reel, js, crop = _reel_with_sidecars(reels, "vypusk_01")
    xml = _cut(out, "01_a", [reel])
    tid = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})["trash"]

    # До возврата: оба соседа ушли с места и лежат в корзине рилса под тем же id, что и клип.
    # Без этой проверки тест проходит и на коде, где соседи вообще не трогались.
    assert not js.exists() and not crop.exists()
    stored = reels / TRASH / tid
    assert (stored / js.name).read_text(encoding="utf-8") == '{"plan": []}'
    assert (stored / crop.name).read_text(encoding="utf-8") == "0 crop"
    meta = json.loads((out / TRASH / tid / "trash.json").read_text(encoding="utf-8"))
    stored_recs = {rec["name"]: rec for rec in meta["files"] if "stored" in rec}
    assert stored_recs[js.name]["stored"] == os.path.abspath(str(stored / js.name))
    assert stored_recs[crop.name]["stored"] == os.path.abspath(str(stored / crop.name))

    r = _post(client, "/api/trash_restore", {"root": str(out), "id": tid})
    assert r["ok"] is True and r["skipped"] == []
    assert reel.read_bytes() == b"reel"
    assert js.read_text(encoding="utf-8") == '{"plan": []}'
    assert crop.read_text(encoding="utf-8") == "0 crop"
    assert not (reels / TRASH).exists()


def test_без_sources_рилс_и_соседи_на_месте(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    reels = tmp_path / "камера1"
    reel, js, crop = _reel_with_sidecars(reels, "vypusk_01")
    xml = _cut(out, "01_a", [reel])

    dry = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": True})
    assert all(f["path"] not in (str(js), str(crop)) for f in dry["files"])
    d = _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False})
    assert d["ok"] is True and not xml.exists()
    assert reel.exists() and js.exists() and crop.exists()
    assert not (reels / TRASH).exists()


def test_общий_рилс_остаётся_вместе_со_своими_соседями(client, tmp_path):
    """Рилс нужен и другому клипу: mp4 и его соседи остаются на месте, в корзину не уходят."""
    out = tmp_path / "out"
    out.mkdir()
    reels = tmp_path / "камера1"
    reel, js, crop = _reel_with_sidecars(reels, "vypusk_01")
    x1 = _cut(out, "01_a", [reel])
    _cut(out, "02_b", [reel])

    real = _post(client, "/api/clip_delete", {"xml": str(x1), "dry": False, "sources": True})
    assert real["ok"] is True
    assert reel.exists() and js.exists() and crop.exists()
    assert not (reels / TRASH).exists()


# --- подкасты: та же корзина ---

def test_подкаст_с_внешним_файлом_в_корзине_и_возврат(client, tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    src = tmp_path / "с диска" / "talk.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    folder = _pod(root, "talk", "Разговор", str(src))
    (folder / "source.mp3").write_bytes(b"inner")   # скачанное — уезжает с папкой

    d = _post(client, "/api/podcast_delete", {"dir": str(folder), "sources": True})
    assert d["ok"] is True and d["skipped"] == []
    tid = d["trash"]
    assert not folder.exists() and not src.exists()

    entry = root / TRASH / tid
    assert (entry / "talk" / "podcast.json").exists()
    assert (entry / "talk" / "source.mp3").read_bytes() == b"inner"
    assert (src.parent / TRASH / tid / "talk.mp3").read_bytes() == b"audio"
    meta = json.loads((entry / "trash.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "podcast" and meta["from"] == os.path.abspath(str(folder))
    assert meta["title"] == "Разговор"

    items = _post(client, "/api/trash_list", {"dir": str(out), "podcast_root": str(root)})["items"]
    pod = [i for i in items if i["kind"] == "podcast"]
    assert len(pod) == 1 and pod[0]["id"] == tid
    assert pod[0]["title"] == "Разговор" and pod[0]["root"] == str(root)
    assert pod[0]["sources"] == 1

    r = _post(client, "/api/trash_restore", {"root": str(root), "id": tid})
    assert r["ok"] is True
    assert (folder / "podcast.json").exists() and (folder / "source.mp3").exists()
    assert src.read_bytes() == b"audio"
    assert not (root / TRASH).exists()
    assert not (src.parent / TRASH).exists()


def test_подкаст_сбой_переноса_исходника_пустой_корзины_у_исходника_нет(client, tmp_path, monkeypatch):
    """Раньше при сбое переноса внешнего файла у подкаста оставалась пустая
    `<папка исходника>/_reelsi_trash/<id>` (у клипа её убирали). Теперь — как у клипа:
    исходник на месте, пустых папок рядом с ним нет, подкаст всё равно в корзине."""
    root = tmp_path / "podcasts"
    root.mkdir()
    src = tmp_path / "с диска" / "talk.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    folder = _pod(root, "talk", "Разговор", str(src))

    real_move = clipstore.move_file

    def fake_move(s, dst):
        if os.path.abspath(s) == os.path.abspath(str(src)):
            raise PermissionError(f"файл занят: {s}")
        return real_move(s, dst)

    monkeypatch.setattr(clipstore, "move_file", fake_move)

    d = _post(client, "/api/podcast_delete", {"dir": str(folder), "sources": True})
    assert d["ok"] is True and not folder.exists()
    assert [s["path"] for s in d["skipped"]] == [os.path.abspath(str(src))]
    assert src.read_bytes() == b"audio"
    assert not (src.parent / TRASH).exists()
    meta = json.loads((root / TRASH / d["trash"] / "trash.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "podcast" and meta["files"] == []


def test_подкаст_сбой_переименования_пустая_корзина_не_остаётся(client, tmp_path, monkeypatch):
    """rename(папка → запись) упал: папка подкаста на месте, и ни записи, ни пустой
    `_reelsi_trash` в папке подкастов не остаётся (раньше `_reelsi_trash` оставалась)."""
    root = tmp_path / "podcasts"
    root.mkdir()
    folder = _pod(root, "talk", "Разговор")
    real_rename = os.rename

    def fake_rename(src, dst):
        if os.path.abspath(src) == os.path.abspath(str(folder)):
            raise PermissionError("папка занята")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", fake_rename)
    r = _post(client, "/api/podcast_delete", {"dir": str(folder)})
    assert r.get("ok") is not True
    assert folder.exists() and (folder / "podcast.json").exists()
    assert not (root / TRASH).exists()


def test_возврат_запись_с_испорченным_путём_в_skipped_и_не_падает(client, tmp_path):
    """stored: null / пустой / не строка — запись испорчена: пропущена с причиной, без падения.
    Запись без пути к исходному месту — тоже в skipped с причиной."""
    root = tmp_path / "clips"
    root.mkdir()
    tid = "20260101-120000_bad"
    entry = root / TRASH / tid
    entry.mkdir(parents=True)
    (entry / "x.mp3").write_bytes(b"in-folder")       # обычный файл записи, без stored — вернётся
    meta = {"kind": "clip", "xml": str(root / "bad.xml"), "deleted_at": "2026-01-01T00:00:00+00:00",
            "files": [
                {"from": str(tmp_path / "a.mp3"), "name": "a.mp3", "stored": None},
                {"from": str(tmp_path / "b.mp3"), "name": "b.mp3", "stored": ""},
                {"from": str(tmp_path / "c.mp3"), "name": "c.mp3", "stored": 7},
                {"from": "", "name": "d.mp3"},
                {"from": str(tmp_path / "x.mp3"), "name": "x.mp3"},
            ]}
    (entry / "trash.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    r = _post(client, "/api/trash_restore", {"root": str(root), "id": tid})
    assert r["ok"] is True
    skipped_paths = {s["path"] for s in r["skipped"]}
    assert str(tmp_path / "a.mp3") in skipped_paths
    assert str(tmp_path / "b.mp3") in skipped_paths
    assert str(tmp_path / "c.mp3") in skipped_paths
    assert "" in skipped_paths and all(s["why"] for s in r["skipped"])
    assert (tmp_path / "x.mp3").read_bytes() == b"in-folder"   # обычная запись вернулась


def test_подкаст_без_sources_внешний_файл_на_месте(client, tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    src = tmp_path / "диск" / "talk.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    folder = _pod(root, "talk", "Разговор", str(src))

    d = _post(client, "/api/podcast_delete", {"dir": str(folder)})
    assert d["ok"] is True and not folder.exists()
    assert src.read_bytes() == b"audio"


def test_возврат_подкаста_при_занятом_месте_ошибка_и_ничего_не_сдвинуто(client, tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    folder = _pod(root, "talk", "Разговор")
    tid = _post(client, "/api/podcast_delete", {"dir": str(folder)})["trash"]

    (root / "talk").mkdir()                       # на старом месте уже что-то есть
    (root / "talk" / "чужое.txt").write_text("x", encoding="utf-8")

    r = _post(client, "/api/trash_restore", {"root": str(root), "id": tid})
    assert r.get("err") == "trash_restore_failed"
    assert (root / TRASH / tid / "talk" / "podcast.json").exists()   # в корзине всё на месте
    assert (root / "talk" / "чужое.txt").read_text(encoding="utf-8") == "x"
    assert not (root / "talk" / "podcast.json").exists()


def test_внешний_файл_общий_с_другим_подкастом_не_уносится(client, tmp_path):
    root = tmp_path / "podcasts"
    root.mkdir()
    src = tmp_path / "диск" / "talk.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    a = _pod(root, "a", "Первый", str(src))
    _pod(root, "b", "Второй", str(src))

    d = _post(client, "/api/podcast_delete", {"dir": str(a), "sources": True})
    assert d["ok"] is True and not a.exists()
    assert src.read_bytes() == b"audio"
    assert any("Второй" in s["why"] for s in d["skipped"])


# --- очистка корзины ---

def test_очистка_dry_считает_настоящая_стирает(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    root = tmp_path / "podcasts"
    root.mkdir()
    cam = tmp_path / "cam" / "C1.MP4"
    cam.parent.mkdir()
    cam.write_bytes(b"camera")
    xml = _cut(out, "01_a", [cam])
    _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False, "sources": True})

    src = tmp_path / "диск" / "talk.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    _post(client, "/api/podcast_delete", {"dir": str(_pod(root, "talk", "Разговор", str(src))),
                                          "sources": True})

    dry = _post(client, "/api/trash_empty", {"dir": str(out), "podcast_root": str(root), "dry": True})
    assert dry["ok"] is True and dry["entries"] == 2 and dry["bytes"] > 0
    assert (cam.parent / TRASH).exists()          # сухой прогон ничего не стёр

    real = _post(client, "/api/trash_empty", {"dir": str(out), "podcast_root": str(root), "dry": False})
    assert real["ok"] is True and real["removed"] == 2 and real["failed"] == []
    assert real["bytes"] == dry["bytes"]
    assert not (out / TRASH).exists()
    assert not (root / TRASH).exists()
    assert not (cam.parent / TRASH).exists()
    assert not (src.parent / TRASH).exists()
    assert _post(client, "/api/trash_list", {"dir": str(out), "podcast_root": str(root)})["items"] == []


def test_очистка_по_умолчанию_только_подсчёт(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    xml = _cut(out, "01_a", [])
    _post(client, "/api/clip_delete", {"xml": str(xml), "dry": False})
    d = _post(client, "/api/trash_empty", {"dir": str(out)})
    assert d["entries"] == 1 and "removed" not in d
    assert (out / TRASH).exists()


def test_подделка_stored_вне_корзины_не_стирается_и_не_возвращается(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "важное.txt"
    victim.write_text("keep", encoding="utf-8")
    back = tmp_path / "назад.txt"
    entry = out / TRASH / "20260101-000000_fake"
    entry.mkdir(parents=True)
    (entry / "trash.json").write_text(json.dumps({
        "kind": "clip", "xml": "x", "deleted_at": "2026-01-01T00:00:00+00:00",
        "files": [{"from": str(back), "name": "важное.txt", "stored": str(victim)}],
    }, ensure_ascii=False), encoding="utf-8")

    r = _post(client, "/api/trash_restore", {"root": str(out), "id": "20260101-000000_fake"})
    assert any(s["path"] == str(back) for s in r["skipped"])
    assert victim.read_text(encoding="utf-8") == "keep" and not back.exists()

    real = _post(client, "/api/trash_empty", {"dir": str(out), "dry": False})
    assert victim.read_text(encoding="utf-8") == "keep"
    assert real["removed"] == 0
    assert any(f["path"] == str(victim) for f in real["failed"])
    assert entry.exists()                        # запись с чужим путём остаётся целиком


def test_очистка_без_папки_отказ(client, tmp_path):
    d = _post(client, "/api/trash_empty", {"dry": False})
    assert d.get("err") == "no_folder"


def _clip_in_trash(out: Path, stem: str) -> str:
    """Клип в корзине папки out: вернуть id записи."""
    xml = _cut(out, stem, [])
    tid, _moved, _failed = clipstore.move_to_trash(str(xml), [str(xml)])
    assert tid
    return tid


def test_список_корзины_несуществующая_папка_подкастов_не_роняет(client, tmp_path):
    """podcast_root ещё не создан (его заводит первый подкаст), dir — с клипом в корзине:
    сервер пропускает пустую папку и отдаёт запись клипа — без ошибки."""
    out = tmp_path / "out"
    out.mkdir()
    tid = _clip_in_trash(out, "04_gone")
    d = _post(client, "/api/trash_list",
              {"dir": str(out), "podcast_root": str(tmp_path / "нет-такой-папки")})
    assert d.get("err") is None and d["ok"] is True
    assert [i["id"] for i in d["items"]] == [tid]


def test_список_корзины_несуществующая_dir_не_ошибка_а_пусто(client, tmp_path):
    """Папка результата не существует, подкасты — с записью: ошибки нет, запись подкаста видна."""
    root = tmp_path / "podcasts"
    root.mkdir()
    folder = _pod(root, "talk", "Разговор")
    tid = _post(client, "/api/podcast_delete", {"dir": str(folder)})["trash"]
    d = _post(client, "/api/trash_list",
              {"dir": str(tmp_path / "нет-папки-результата"), "podcast_root": str(root)})
    assert d.get("err") is None and d["ok"] is True
    assert [i["id"] for i in d["items"]] == [tid]


def test_список_корзины_dirs_из_двух_папок_обе_записи(client, tmp_path):
    """Клип из другой папки (добавлен «Добавить XML…») держит корзину у себя: `dirs` даёт обе."""
    a = tmp_path / "out"
    b = tmp_path / "cut_out"
    a.mkdir()
    b.mkdir()
    tid_a = _clip_in_trash(a, "04_a")
    tid_b = _clip_in_trash(b, "05_b")
    d = _post(client, "/api/trash_list", {"dirs": [str(a), str(b)]})
    assert d.get("err") is None and d["ok"] is True
    assert sorted(i["id"] for i in d["items"]) == sorted([tid_a, tid_b])
    assert {i["root"] for i in d["items"]} == {str(a), str(b)}


def test_список_корзины_дубль_папки_одна_запись(client, tmp_path):
    """Одна папка указана трижды (dir, dirs, и dirs с «/» в конце) — запись одна."""
    a = tmp_path / "out"
    a.mkdir()
    tid = _clip_in_trash(a, "04_dup")
    d = _post(client, "/api/trash_list",
              {"dir": str(a), "dirs": [str(a), str(a) + os.sep, "", 7, None]})
    assert d.get("err") is None
    assert [i["id"] for i in d["items"]] == [tid]


def test_очистка_dirs_считает_и_стирает_все_папки(client, tmp_path):
    a = tmp_path / "out"
    b = tmp_path / "cut_out"
    a.mkdir()
    b.mkdir()
    _clip_in_trash(a, "04_x")
    _clip_in_trash(b, "05_y")
    dry = _post(client, "/api/trash_empty", {"dirs": [str(a), str(b), str(a)], "dry": True})
    assert dry["entries"] == 2
    real = _post(client, "/api/trash_empty", {"dirs": [str(a), str(b)], "dry": False})
    assert real.get("err") is None and real["removed"] == 2
    assert not (a / TRASH).exists() and not (b / TRASH).exists()


def test_список_корзины_без_папок_вообще_отказ(client):
    d = _post(client, "/api/trash_list", {"dir": "", "podcast_root": ""})
    assert d.get("err") == "no_folder"


def test_очистка_несуществующая_папка_подкастов_не_роняет(client, tmp_path):
    """Сухой прогон и настоящая очистка с несуществующим podcast_root: запись из dir стирается,
    ошибки нет."""
    out = tmp_path / "out"
    out.mkdir()
    _clip_in_trash(out, "04_gone2")
    missing = str(tmp_path / "нет-такой-папки")
    dry = _post(client, "/api/trash_empty", {"dir": str(out), "podcast_root": missing, "dry": True})
    assert dry.get("err") is None and dry["entries"] == 1
    real = _post(client, "/api/trash_empty", {"dir": str(out), "podcast_root": missing, "dry": False})
    assert real.get("err") is None and real["removed"] == 1 and real["failed"] == []
    assert not (out / TRASH).exists()
