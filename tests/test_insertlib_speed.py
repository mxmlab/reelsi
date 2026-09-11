# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты для задания EI: отметка gone для пропавших файлов, матричный расчёт косинусов и скорость подбора.

Запуск:  python -m pytest reelsi/tests/test_insertlib_speed.py -q
"""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import insertlib

# Слияние записей, у которых путь отличается регистром, — контракт ФАЙЛОВОЙ СИСТЕМЫ,
# а не индекса: ключ записи считается os.path.normcase, и там, где регистр значим
# (Linux в CI), Cool_Photo.png и cool_photo.png — два РАЗНЫХ файла, склеивать их
# нельзя. Тесты ниже проверяют поведение на ФС без учёта регистра (Windows).
insensitive_fs = pytest.mark.skipif(
    os.path.normcase("A") == "A",
    reason="ФС учитывает регистр пути — записи в разном регистре не дубли")


def test_build_index_sets_gone_and_preserves_meta(tmp_path, monkeypatch):
    """build_index ставит gone пропавшему файлу и не теряет used/rej/ru/mw/mh."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()

    f1 = media_dir / "file1.png"
    f2 = media_dir / "file2.png"
    f1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    f2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    # Исходный индекс с метаданными
    old_items = [
        {
            "path": str(f1),
            "name": "file1.png",
            "type": "photo",
            "used": 4,
            "desc": "golden cup trophy",
            "desc_src": "user",
            "ru": "золотой кубок",
            "rej": ["bad query"],
            "mw": 110,
            "mh": 130,
            "emb": [0.1, 0.2, 0.3],
            "added": 1234567890.0,
        },
        {
            "path": str(f2),
            "name": "file2.png",
            "type": "photo",
            "used": 1,
            "desc": "silver coin",
            "desc_src": "ai",
            "ru": "серебряная монета",
            "emb": [0.3, 0.2, 0.1],
        },
    ]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    # Удаляем file1 с диска
    f1.unlink()

    t_before = time.time()
    insertlib.build_index([str(media_dir)], use_emb=False)

    d = json.load(open(idx_path, encoding="utf-8"))
    items_by_path = {it["path"]: it for it in d["items"]}

    assert str(f1) in items_by_path
    it1 = items_by_path[str(f1)]
    assert "gone" in it1
    assert it1["gone"] >= t_before
    assert it1["used"] == 4
    assert it1["ru"] == "золотой кубок"
    assert it1["rej"] == ["bad query"]
    assert it1["mw"] == 110
    assert it1["mh"] == 130
    assert it1["desc"] == "golden cup trophy"

    # file2 на месте — у него нет gone
    assert str(f2) in items_by_path
    assert "gone" not in items_by_path[str(f2)]


def test_build_index_clears_gone_when_file_returns(tmp_path, monkeypatch):
    """Вернувшийся файл теряет gone на следующем скане."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()

    f1 = media_dir / "file1.png"
    # Файл помечен gone
    old_items = [
        {
            "path": str(f1),
            "name": "file1.png",
            "type": "photo",
            "used": 4,
            "desc": "golden cup trophy",
            "desc_src": "user",
            "ru": "золотой кубок",
            "rej": ["bad query"],
            "gone": 1234567890.0,
        }
    ]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    # Файл вернулся на диск
    f1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    insertlib.build_index([str(media_dir)], use_emb=False)

    d = json.load(open(idx_path, encoding="utf-8"))
    assert len(d["items"]) == 1
    it1 = d["items"][0]
    assert "gone" not in it1
    assert it1["used"] == 4
    assert it1["ru"] == "золотой кубок"
    assert it1["rej"] == ["bad query"]


def test_match_many_filters_gone_and_missing_disk_files(tmp_path, monkeypatch):
    """match_many не отдаёт записи с gone, а запись без gone, чей файл удалён с диска, фильтруется проверкой isfile."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    # Создаём 4 файла на диске
    files = []
    for i in range(4):
        f = tmp_path / f"img_{i}.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
        files.append(f)

    items = [
        # 0: помечен gone (файл есть на диске, но скан признал gone)
        {"path": str(files[0]), "name": "img_0.png", "type": "photo", "used": 10,
         "desc": "common subject query alpha", "gone": 12345678.0},
        # 1: файла нет на диске (удалён прямо сейчас), но в индексе gone ещё не стоит
        {"path": str(files[1]), "name": "img_1.png", "type": "photo", "used": 9,
         "desc": "common subject query alpha"},
        # 2 и 3: живые нормальные файлы
        {"path": str(files[2]), "name": "img_2.png", "type": "photo", "used": 8,
         "desc": "common subject query alpha"},
        {"path": str(files[3]), "name": "img_3.png", "type": "photo", "used": 7,
         "desc": "common subject query alpha"},
    ]
    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    # Удаляем files[1] с диска прямо сейчас
    files[1].unlink()

    res = insertlib.match_many(["common subject query alpha"], k=3)[0]
    paths = [r["path"] for r in res]

    # img_0 не должен попасть, т.к. gone
    assert str(files[0]) not in paths
    # img_1 не должен попасть, т.к. удалён с диска (отсеян проверкой os.path.isfile)
    assert str(files[1]) not in paths
    # img_2 и img_3 должны быть в ответе
    assert str(files[2]) in paths
    assert str(files[3]) in paths


def test_matrix_and_old_cos_produce_identical_ranking(tmp_path, monkeypatch):
    """Матричный расчёт и _cos дают одинаковый порядок и score на фикстуре из 5 записей."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: "mock-emb")
    monkeypatch.setattr(insertlib, "_ensure_emb_tag", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    # Фикстура из 5 векторов размерности 4
    vectors = [
        [0.8, 0.1, 0.1, 0.5],
        [0.1, 0.9, 0.2, 0.1],
        [0.5, 0.5, 0.5, 0.5],
        [0.2, 0.1, 0.8, 0.3],
        [0.7, 0.3, 0.2, 0.4],
    ]

    files = []
    items = []
    for i, vec in enumerate(vectors):
        f = tmp_path / f"item_{i}.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
        files.append(f)
        items.append({
            "path": str(f),
            "name": f"item_{i}.png",
            "type": "photo",
            "used": i,
            "desc": f"item {i} description",
            "emb": vec,
            "added": 1000.0 + i,
        })

    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "mock-emb",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    query_vec = [0.6, 0.2, 0.3, 0.4]
    monkeypatch.setattr(insertlib, "_emb_queries", lambda qs, m: [query_vec for _ in qs])

    # 1. Считаем эталонные score через старый insertlib._cos
    expected_scores = []
    for it in items:
        score = insertlib._cos(query_vec, it["emb"])
        expected_scores.append((score, it))

    # Сортировка как в контракте
    pos = {id(it): n for n, it in enumerate(items)}
    expected_scores.sort(key=lambda x: (-x[0], -x[1].get("used", 0),
                                        -(x[1].get("added") or 0), -pos[id(x[1])]))
    expected_ranking = [it["path"] for _, it in expected_scores]

    # 2. Вызов match_many с матричным расчётом
    actual_res = insertlib.match_many(["any query"], k=5)[0]
    actual_ranking = [r["path"] for r in actual_res]
    actual_scores = [r["score"] for r in actual_res]

    assert actual_ranking == expected_ranking
    for (exp_s, _), act_s in zip(expected_scores, actual_scores):
        assert abs(round(exp_s, 3) - act_s) < 1e-4


def test_match_many_sequential_disk_check_returns_full_k(tmp_path, monkeypatch):
    """match_many(k=2) возвращает ровно 2 живые записи, даже если первые 6 по убыванию score отсутствуют на диске и gone не проставлен."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: "mock-emb")
    monkeypatch.setattr(insertlib, "_ensure_emb_tag", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    # 8 записей с убывающими score относительно запроса
    # Запрос: [1.0, 0.0, 0.0]
    # Записи 0..5: score от 0.99 до 0.94, файлов на диске нет, gone не проставлен
    # Записи 6..7: score 0.80 и 0.70, файлы на диске есть
    query_vec = [1.0, 0.0, 0.0]
    monkeypatch.setattr(insertlib, "_emb_queries", lambda qs, m: [query_vec for _ in qs])

    items = []
    for i in range(6):
        score_val = 0.99 - i * 0.01
        items.append({
            "path": str(tmp_path / f"missing_{i}.png"),
            "name": f"missing_{i}.png",
            "type": "photo",
            "used": 10 - i,
            "desc": f"missing item {i}",
            "emb": [score_val, float((1.0 - score_val**2)**0.5), 0.0],
            "added": 1000.0 + i,
        })

    live_files = []
    for i, score_val in enumerate([0.80, 0.70], start=6):
        f = tmp_path / f"live_{i}.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
        live_files.append(f)
        items.append({
            "path": str(f),
            "name": f"live_{i}.png",
            "type": "photo",
            "used": 1,
            "desc": f"live item {i}",
            "emb": [score_val, float((1.0 - score_val**2)**0.5), 0.0],
            "added": 1000.0 + i,
        })

    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "mock-emb",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    res = insertlib.match_many(["test query"], k=2)[0]
    assert len(res) == 2
    assert res[0]["path"] == str(live_files[0])
    assert res[1]["path"] == str(live_files[1])


def test_stats_missing_and_gone_reporting(tmp_path, monkeypatch):
    """stats выводит число отсутствующих на диске файлов, сколько из них помечено gone, и подсказку."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    insertlib._CACHE["data"] = None

    live = tmp_path / "live.png"
    live.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    items = [
        {"path": str(live), "name": "live.png", "desc": "photo of live trophy", "desc_src": "user"},
        {"path": str(tmp_path / "miss1.png"), "name": "miss1.png", "desc": "missing one", "gone": 123.0},
        {"path": str(tmp_path / "miss2.png"), "name": "miss2.png", "desc": "missing two"},
    ]
    json.dump({"items": items, "dirs": [str(tmp_path)], "emb_model": "", "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    lines = []
    st = insertlib.stats(emit=lines.append)
    assert st["total"] == 3
    assert st["on_disk"] == 1
    assert st["missing"] == 2
    assert st["missing_gone"] == 1

    disk_line = [L for L in lines if "Файлов на диске:" in L][0]
    assert "нет на диске: 2" in disk_line
    assert "из них помечено gone: 1" in disk_line
    assert "запусти скан, чтобы пометить" in disk_line


@insensitive_fs
def test_build_index_case_insensitive_matching(tmp_path, monkeypatch):
    """Прежняя запись с путём в другом регистре после скана НЕ помечается gone и НЕ дублируется."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()
    f1 = media_dir / "Cool_Photo.png"
    f1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    # Путь в старом индексе записан в другом регистре (например, полностью в нижнем)
    alt_path = os.path.join(str(media_dir).lower(), "cool_photo.png")
    old_items = [
        {
            "path": alt_path,
            "name": "cool_photo.png",
            "type": "photo",
            "used": 3,
            "desc": "meaningful trophy award",
            "desc_src": "adopted",
            "ru": "наградной кубок",
            "rej": ["query1"],
            "mw": 120,
            "mh": 90,
            "added": 1234567.0,
            "emb": [0.1, 0.2, 0.3],
        }
    ]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    insertlib.build_index([str(media_dir)], use_emb=False)

    d = json.load(open(idx_path, encoding="utf-8"))
    assert len(d["items"]) == 1
    it = d["items"][0]
    assert it["path"] == str(f1)  # путь в регистре с диска
    assert "gone" not in it
    assert it["desc"] == "meaningful trophy award"
    assert it["desc_src"] == "adopted"
    assert it["ru"] == "наградной кубок"
    assert it["used"] == 3
    assert it["rej"] == ["query1"]
    assert it["mw"] == 120
    assert it["mh"] == 90
    assert it["added"] == 1234567.0
    assert it["emb"] == [0.1, 0.2, 0.3]


@insensitive_fs
def test_build_index_merges_duplicate_records(tmp_path, monkeypatch):
    """При слиянии дублей сохраняются: осмысленный desc, максимальный used, объединённый rej, mw/mh/ru/added."""
    idx_path = str(tmp_path / "test_idx.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx_path)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    insertlib._CACHE["data"] = None
    insertlib._CACHE["mat"] = None
    insertlib._CACHE["mat_items"] = None

    media_dir = tmp_path / "photos"
    media_dir.mkdir()
    f1 = media_dir / "anastrozole_box.png"
    f1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    # 2 дубликата одной записи с разным регистром и метаданными
    p_lower = str(f1).lower()
    p_upper = str(f1).upper()
    old_items = [
        {
            # Брошенная gone-запись с осмысленным desc, mw/mh, ru, emb
            "path": p_lower,
            "name": "anastrozole_box.png",
            "type": "photo",
            "used": 2,
            "desc": "anastrozole tablet packaging box",
            "desc_src": "adopted",
            "ru": "анастрозол таблетки упаковка",
            "rej": ["query1", "query2"],
            "mw": 120,
            "mh": 80,
            "emb": [0.1, 0.2, 0.3],
            "gone": 1700000000.0,
        },
        {
            # Новая запись-дубликат с desc=имя файла, большим used, своими rej и added
            "path": p_upper,
            "name": "ANASTROZOLE_BOX.PNG",
            "type": "photo",
            "used": 6,
            "desc": "anastrozole box photos",
            "desc_src": "name",
            "ru": "",
            "rej": ["query2", "query3"],
            "added": 1700010000.0,
            "emb": [0.9, 0.8, 0.7],
        },
    ]
    json.dump({"items": old_items, "dirs": [str(media_dir)], "emb_model": "",
               "emb_tag": insertlib.EMB_TAG},
              open(idx_path, "w", encoding="utf-8"))

    insertlib.build_index([str(media_dir)], use_emb=False)

    d = json.load(open(idx_path, encoding="utf-8"))
    assert len(d["items"]) == 1
    it = d["items"][0]
    assert it["path"] == str(f1)
    assert "gone" not in it
    # Побеждает осмысленный desc
    assert it["desc"] == "anastrozole tablet packaging box"
    assert it["desc_src"] == "adopted"
    assert it["ru"] == "анастрозол таблетки упаковка"
    # Максимум used
    assert it["used"] == 6
    # Объединённый rej без повторов
    assert it["rej"] == ["query1", "query2", "query3"]
    # mw, mh, added подтянуты
    assert it["mw"] == 120
    assert it["mh"] == 80
    assert it["added"] == 1700010000.0
    # emb от победителя (т.к. desc и ru совпали)
    assert it["emb"] == [0.1, 0.2, 0.3]

