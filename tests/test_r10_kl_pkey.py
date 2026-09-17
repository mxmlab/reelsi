# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты ключа пути pkey и инвалидации кэша индекса вставок (задание KL).

Проверяет:
1. Инвариант на текущей машине: pkey(p) == os.path.normcase(p).
2. Имитацию POSIX: на регистронезависимой ФС (macOS APFS) pkey приводит к нижнему регистру,
   на регистрозависимой (Linux ext4) — сохраняет исходный регистр.
3. Дедупликацию записей с разным регистром в insertlib.build_index при case-insensitive ФС.
4. Инвалидацию кэша _load() по паре (st_mtime_ns, st_size) при неизменных секундах mtime.
5. Совпадение ключей маски рото и истории видео с normcase-формой на текущей машине.
"""
import hashlib
import json
import os
from api import videogen
from core import insertlib, paths, roto


def test_pkey_matches_normcase_on_current_machine(tmp_path):
    """На текущей платформе pkey полностью совпадает с os.path.normcase."""
    # Существующий файл
    existing_file = tmp_path / "ExistingFile.png"
    existing_file.write_text("dummy", encoding="utf-8")
    p_exist = str(existing_file)
    assert paths.pkey(p_exist) == os.path.normcase(p_exist)

    # Существующая папка
    p_dir = str(tmp_path)
    assert paths.pkey(p_dir) == os.path.normcase(p_dir)

    # Несуществующий файл в существующей папке
    p_nonexist = str(tmp_path / "NonExistentFolder" / "SubFile.PNG")
    assert paths.pkey(p_nonexist) == os.path.normcase(p_nonexist)

    # Пустая строка
    assert paths.pkey("") == os.path.normcase("")


def test_pkey_posix_simulation(monkeypatch):
    """Имитация POSIX: пробник ФС определяет нечувствительность к регистру через samefile."""
    monkeypatch.setattr(paths.os, "name", "posix")
    paths._pkey_cache_clear()

    # 1. Случай case-insensitive ФС (APFS по умолчанию на macOS):
    # swapcase-вариант существует на диске и указывает на тот же inode/файл
    known_ci = {"/media/photos/cool_photo.png", "/media/photos"}

    def fake_exists_ci(p):
        return str(p).lower() in known_ci

    def fake_samefile_ci(p1, p2):
        return str(p1).lower() == str(p2).lower()

    monkeypatch.setattr(os.path, "exists", fake_exists_ci)
    monkeypatch.setattr(os.path, "samefile", fake_samefile_ci)

    # Существующий файл: регистр опускается
    assert paths.pkey("/media/photos/Cool_Photo.png") == "/media/photos/cool_photo.png"
    # Несуществующий файл в существующей папке: регистр также опускается
    assert paths.pkey("/media/photos/NonExistent.PNG") == "/media/photos/nonexistent.png"

    # 2. Сброс кэша пробника перед проверкой регистрозависимой ФС
    paths._pkey_cache_clear()

    # Случай case-sensitive ФС (Linux ext4):
    # swapcase-вариант не существует либо не является тем же файлом
    known_cs = {"/media/photos/Cool_Photo.png", "/media/photos"}

    def fake_exists_cs(p):
        return str(p) in known_cs

    def fake_samefile_cs(p1, p2):
        return str(p1) == str(p2)

    monkeypatch.setattr(os.path, "exists", fake_exists_cs)
    monkeypatch.setattr(os.path, "samefile", fake_samefile_cs)

    # Регистр должен сохраниться без изменений
    assert paths.pkey("/media/photos/Cool_Photo.png") == "/media/photos/Cool_Photo.png"
    assert paths.pkey("/media/photos/NonExistent.PNG") == "/media/photos/NonExistent.PNG"


def test_insertlib_build_index_case_insensitive_dedup(tmp_path, monkeypatch):
    """Вставки Foo.png и foo.png на регистронезависимой ФС объединяются в одну запись."""
    monkeypatch.setattr(paths.os, "name", "posix")
    # На Windows у модуля os нет chown, который вызывается в atomic_json_dump при os.name == 'posix'
    monkeypatch.setattr(os, "chown", lambda *a, **kw: None, raising=False)
    paths._pkey_cache_clear()

    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mtime"] = 0
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    # Прежняя запись в индексе имела путь с заглавной буквы
    old_items = [
        {
            "path": "/media/photos/Foo.png",
            "name": "Foo.png",
            "type": "photo",
            "used": 3,
            "desc": "nice award cup",
            "desc_src": "user",
            "ru": "наградной кубок",
            "rej": ["query1"],
            "mw": 100,
            "mh": 80,
            "added": 12345.0,
            "emb": [0.1, 0.2],
        }
    ]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(
            {"items": old_items, "dirs": ["/media/photos"], "emb_model": "", "emb_tag": insertlib.EMB_TAG},
            f,
        )

    # Скан на диске находит файл в нижнем регистре
    found_scanned = {
        "/media/photos/foo.png": {"type": "photo", "used": 1, "st_size": 1000, "st_mtime": 12345.0}
    }
    monkeypatch.setattr(insertlib, "scan", lambda dirs, emit=None: dict(found_scanned))

    # Имитируем case-insensitive ФС для /media
    real_exists = os.path.exists
    real_samefile = getattr(os.path, "samefile", None)

    def fake_exists(p):
        p_str = str(p).replace("\\", "/").lower()
        if "media/photos" in p_str:
            return True
        return real_exists(p)

    def fake_samefile(p1, p2):
        s1, s2 = str(p1).replace("\\", "/").lower(), str(p2).replace("\\", "/").lower()
        if "media/photos" in s1 and "media/photos" in s2:
            return s1 == s2
        if real_samefile:
            return real_samefile(p1, p2)
        return s1 == s2

    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(os.path, "samefile", fake_samefile)

    # Запускаем построение индекса без эмбеддингов
    insertlib.build_index(["/media/photos"], use_emb=False)

    with open(idx_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    # Запись не должна задвоиться
    assert len(d["items"]) == 1
    it = d["items"][0]
    assert it["path"] == "/media/photos/foo.png"
    assert "gone" not in it
    assert it["used"] == 3
    assert it["desc"] == "nice award cup"


def test_insertlib_load_cache_invalidation_mtime_ns_and_size(tmp_path, monkeypatch):
    """_load() перечитывает файл, если st_mtime в секундах не изменился, но изменился размер или ns."""
    idx_path = str(tmp_path / "insertlib.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mtime"] = 0

    # Первичная запись
    initial_data = {"items": [{"path": "file1.png", "used": 1}], "dirs": []}
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(initial_data, f)

    d1 = insertlib._load()
    assert d1 is not None
    assert len(d1["items"]) == 1
    assert d1["items"][0]["path"] == "file1.png"

    st1 = os.stat(idx_path)

    # Перезаписываем индекс другим содержимым (большим размером)
    new_data = {
        "items": [
            {"path": "file1.png", "used": 1},
            {"path": "file2.png", "used": 2},
        ],
        "dirs": ["/some/dir"],
    }
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(new_data, f)

    # Принудительно выставляем mtime в секундах ровно такой же, как был у st1
    # При старой проверке по getmtime кэш счёл бы файл неизменным
    os.utime(idx_path, (st1.st_atime, st1.st_mtime))

    st2 = os.stat(idx_path)
    # Секунды mtime совпадают, но размер изменился
    assert int(st2.st_mtime) == int(st1.st_mtime)
    assert st2.st_size != st1.st_size

    # _load() должен заметить изменение st_size и вернуть свежие данные
    d2 = insertlib._load()
    assert d2 is not None
    assert len(d2["items"]) == 2
    assert d2["items"][1]["path"] == "file2.png"


def test_roto_and_videogen_keys_match_normcase(tmp_path, monkeypatch):
    """Ключи маски рото и истории видео на текущей платформе совпадают с вычисленными через normcase."""
    # 1. Проверка roto._mask_key
    vid_file = tmp_path / "Clip_01.MP4"
    vid_file.write_bytes(b"dummy_video_bytes")
    p_vid = str(vid_file)

    actual_mask_key = roto._mask_key(p_vid, 0.0, 3.5, 0.0, 2.0)

    st = os.stat(p_vid)
    expected_raw = (
        f"{os.path.normcase(os.path.abspath(p_vid))}|{st.st_mtime_ns}|{st.st_size}|0.0|3.5|0.0|"
        f"d2.0|m{roto._VARIANT}|px{roto._INTERNAL_PX}"
    )
    expected_mask_key = hashlib.md5(expected_raw.encode("utf-8")).hexdigest()[:16]
    assert actual_mask_key == expected_mask_key

    # 2. Проверка videogen vhist_scan_files
    out_dir = tmp_path / "videogen_out"
    out_dir.mkdir()
    gen_file = out_dir / "gen_001.mp4"
    gen_file.write_bytes(b"dummy_video_out")
    p_gen = str(gen_file)

    monkeypatch.setattr(videogen, "VIDEO_OUT", str(out_dir))
    monkeypatch.setattr(videogen, "_vhist_write", lambda items: None)

    # Уже известная запись в истории (ключ пути в known)
    known_items = [{"key": "seed:123", "path": p_gen, "ts": 100}]
    scanned = videogen.vhist_scan_files(known_items)
    # Файл не должен дублироваться, так как paths.pkey(p_gen) найден в known
    assert len(scanned) == 1
